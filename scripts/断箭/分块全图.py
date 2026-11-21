"""从冻结直接网格按125米裁切运行表面，并保存可按区域查询的原三角形支撑数据。"""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

import numpy as np
from PIL import Image

from 地图GLB import write_glb
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint

GEOMETRY = np.dtype([('vertices', '<f4', (9,)), ('source', '<u4'), ('role', '<u4')])
ROLES = {'terrain': 1, 'road': 2, 'overlay': 3, 'background': 4, 'water': 5, 'decor': 6, 'forest': 7}


def role_for(part, material):
    category = part['category']
    if category in ['terrain', 'road', 'background', 'water']:
        return ROLES[category]
    shader = material['shader']['name']
    if shader == 'Shader Graphs/ForestConstrainV2':
        return ROLES['forest']
    if shader in ['Shader Graphs/Detail_CC', 'Shader Graphs/WeatherLit'] or material['name'] in ['Road_decal', 'Parking', 'line_marking', 'line_marking_yellow']:
        return ROLES['overlay']
    return ROLES['decor']


def clip_triangles(values, source_ids, materials, bounds):
    low, high = values[:, :, [0, 2]].min(1), values[:, :, [0, 2]].max(1)
    inside = (low[:, 0] >= bounds[0]) & (high[:, 0] <= bounds[1]) & (low[:, 1] >= bounds[2]) & (high[:, 1] <= bounds[3])
    output, ids, slots = [values[inside]], [source_ids[inside]], [materials[inside]]
    for face, source_id, material in zip(values[~inside], source_ids[~inside], materials[~inside]):
        polygon = list(face)
        for axis, value, sign in [(0, bounds[0], 1), (0, bounds[1], -1), (2, bounds[2], 1), (2, bounds[3], -1)]:
            result = []
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                ia, ib = sign * (a[axis] - value) >= -1e-9, sign * (b[axis] - value) >= -1e-9
                if ia:
                    result.append(a)
                if ia != ib:
                    point = a + (b - a) * ((value - a[axis]) / (b[axis] - a[axis]))
                    point[axis] = value
                    result.append(point)
            polygon = result
            if len(polygon) < 3:
                break
        for i in range(1, len(polygon) - 1):
            triangle = np.asarray([polygon[0], polygon[i], polygon[i + 1]])
            if np.linalg.norm(np.cross(triangle[1, :3] - triangle[0, :3], triangle[2, :3] - triangle[0, :3])) <= 1e-10:
                continue
            output.append(triangle[None])
            ids.append(np.asarray([source_id], dtype=np.uint32))
            slots.append(np.asarray([material], dtype=np.int32))
    return np.concatenate(output), np.concatenate(ids), np.concatenate(slots)


def write_geometry(path, positions, source_ids, roles, origin):
    count = len(positions)
    data = np.empty(count, dtype=GEOMETRY)
    data['vertices'] = positions.reshape(-1, 9)
    data['source'], data['role'] = source_ids, roles
    lists = [[] for _ in range(64)]
    low = np.clip(np.floor((positions[:, :, [0, 2]].min(1) + 62.5) / 125 * 8), 0, 7).astype(int)
    high = np.clip(np.floor((positions[:, :, [0, 2]].max(1) + 62.5) / 125 * 8), 0, 7).astype(int)
    for i, (a, b) in enumerate(zip(low, high)):
        for z in range(a[1], b[1] + 1):
            for x in range(a[0], b[0] + 1):
                lists[z * 8 + x].append(i)
    offsets = np.asarray([0, *np.cumsum([len(v) for v in lists])], dtype='<u4')
    refs = np.asarray([i for values in lists for i in values], dtype='<u4')
    with path.open('xb') as stream:
        stream.write(struct.pack('<4sIIIIfff', b'BAGE', 1, count, 8, len(refs), origin[0], origin[1], 125))
        stream.write(data.tobytes())
        stream.write(offsets.tobytes())
        stream.write(refs.tobytes())
    return {'triangles': count, 'references': len(refs), 'bytes': path.stat().st_size}


def run(directory, revision):
    output = directory / '全图分块' / revision
    output.mkdir(parents=True, exist_ok=False)
    for folder in ['分块', '支撑', '贴图']:
        (output / folder).mkdir()
    source_root = directory / '表面来源/full-r01'
    source_file = source_root / '直接表面.json'
    data = json.loads(source_file.read_text('utf-8'))
    atlas_root = directory / '地表派生/r03'
    atlases = {r['source_key']: r for r in json.loads((atlas_root / '地表图集.json').read_text('utf-8'))['parts']}
    scope = json.loads((directory / '范围冻结/V001-r02/范围与条目.json').read_text('utf-8'))
    map_bounds = scope['runtime_bounds']
    journal = Journal(directory)
    materials, images, prepared, buckets = {}, {}, [], [[] for _ in range(72 * 72)]
    image_hashes, atlas_pixels = {}, {}
    overview_primitives, background_primitives = [], []

    def image_ref(path):
        digest = image_hashes.get(path)
        if digest is None:
            digest = image_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in images:
            target = output / '贴图' / (digest + path.suffix.lower())
            shutil.copyfile(path, target)
            images[digest] = target.relative_to(output).as_posix()
        return '../' + images[digest]

    def primitive(values, key, translation, extras):
        flat = values.reshape(-1, values.shape[-1]).copy()
        flat[:, 0] -= translation[0]
        flat[:, 2] -= translation[1]
        normals = flat[:, 3:6]
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        normals /= np.maximum(length, 1e-30)
        uv0, uv1 = flat[:, 6:8].copy(), flat[:, 8:10].copy()
        uv0[:, 1], uv1[:, 1] = 1 - uv0[:, 1], 1 - uv1[:, 1]
        return {'material': key, 'attributes': {'POSITION': flat[:, :3], 'NORMAL': normals,
            'TEXCOORD_0': uv0, 'TEXCOORD_1': uv1, '_SOURCE_COLOR': flat[:, 10:14]}, 'extras': extras}

    try:
        for pi, part in enumerate(data['parts']):
            journal.update(phase='A2全图分块', operation='按完整三角形包围盒建立空间引用', current=pi + 1, total=len(data['parts']))
            path = source_root / part['file']
            if fingerprint(path)['sha256'] != part['identity']['sha256']:
                raise ValueError('冻结源网格变化')
            with np.load(path) as a:
                positions = a['vertices'][:, [0, 2, 1]].astype(float)
                positions[:, 2] *= -1
                normals = a['normals'][:, [0, 2, 1]].astype(float)
                normals[:, 2] *= -1
                colors = a['colors']
                if len(colors) != len(positions):
                    colors = np.ones((len(positions), 4))
                elif colors.shape[1] == 3:
                    colors = np.column_stack([colors, np.ones(len(positions))])
                values = np.column_stack([positions, normals, a['uv0'], a['uv1'], colors])
                triangles = a['triangles']
                slots = a['material_indices']
            faces = values[triangles]
            face_normals = np.cross(faces[:, 1, :3] - faces[:, 0, :3], faces[:, 2, :3] - faces[:, 0, :3])
            lengths = np.linalg.norm(face_normals, axis=1, keepdims=True)
            face_normals /= np.maximum(lengths, 1e-30)
            zero = np.linalg.norm(faces[:, :, 3:6], axis=2) < 1e-8
            faces[:, :, 3:6] = np.where(zero[:, :, None], face_normals[:, None, :], faces[:, :, 3:6])
            material_keys = []
            roles = []
            for key in part['materials']:
                m = data['materials'][key]
                source = {'name': m['name'], 'source_key': key, 'color': m.get('color', [1, 1, 1, 1]),
                    'alpha_mode': m['alpha_mode'], 'alpha_cutoff': m['alpha_cutoff'], 'double_sided': m['double_sided'],
                    'render_role': role_for(part, m)}
                if source['render_role'] == ROLES['forest']:
                    props = dict(m['raw']['m_SavedProperties']['m_Floats'])
                    source['forest_distance'] = {'start': props.get('_StartDistance'), 'fade': props.get('_FadeDistance'), 'outside': props.get('_OUTSIDE')}
                if m.get('basecolor'):
                    source['image'] = image_ref(Path(m['basecolor']))
                    info = next(v for v in m['textures'].values() if v['file'] == m['basecolor'])
                    source['scale'] = [info['scale']['x'], info['scale']['y']]
                    source['offset'] = [info['offset']['x'], info['offset']['y']]
                materials[key] = source
                material_keys.append(key)
                roles.append(role_for(part, m))
            if part['category'] == 'terrain':
                atlas = atlases[part['source_key']]
                bounds = atlas['bounds_source_xz']
                faces[:, :, 6] = (faces[:, :, 0] + 4425 - bounds[0]) / 3000
                faces[:, :, 7] = (4850 - faces[:, :, 2] - bounds[2]) / 3000
                with Image.open(atlas_root / atlas['file']) as im:
                    low = im.convert('RGB')
                    low.thumbnail((512, 512))
                    low_path = output / '贴图' / ('总览-' + part['source_key'].replace(':', '-') + '.png')
                    low.save(low_path)
                overview_key = '总览-' + part['source_key']
                materials[overview_key] = {'name': overview_key, 'color': [1, 1, 1, 1], 'clamp': True,
                    'image': '../' + low_path.relative_to(output).as_posix(), 'alpha_mode': 'OPAQUE'}
                overview_primitives.append(primitive(faces, overview_key, (0, 0), {'source_key': part['source_key']}))
            if part['category'] in ['background', 'water']:
                for slot, key in enumerate(material_keys):
                    if np.any(slots == slot):
                        background_primitives.append(primitive(faces[slots == slot], key, (0, 0), {'source_key': part['source_key']}))
            prepared.append({'part': part, 'faces': faces, 'slots': slots, 'materials': material_keys, 'roles': roles})
            low, high = faces[:, :, [0, 2]].min(1), faces[:, :, [0, 2]].max(1)
            intersects = (high[:, 0] >= map_bounds['minX']) & (low[:, 0] <= map_bounds['maxX']) & (high[:, 1] >= map_bounds['minZ']) & (low[:, 1] <= map_bounds['maxZ'])
            minimum = np.clip(np.floor((low - [map_bounds['minX'], map_bounds['minZ']]) / 125), 0, 71).astype(int)
            maximum = np.clip(np.floor((high - [map_bounds['minX'], map_bounds['minZ']]) / 125), 0, 71).astype(int)
            cells = defaultdict(list)
            for ti in np.flatnonzero(intersects):
                for z in range(minimum[ti, 1], maximum[ti, 1] + 1):
                    for x in range(minimum[ti, 0], maximum[ti, 0] + 1):
                        cells[z * 72 + x].append(ti)
            for cell, ids in cells.items():
                buckets[cell].append((pi, np.asarray(ids, dtype=np.int32)))
        # 两个总览GLB单独位于子目录，以统一受控相对贴图的基址。
        (output / '总览').mkdir()
        overview_record = write_glb(output / '总览/地形.glb', overview_primitives, materials)
        background_record = write_glb(output / '总览/背景水面.glb', background_primitives, materials)
        chunks, coverage, seam_points = [], np.zeros(len(prepared), dtype=np.int64), {}
        max_seam, seam_count = 0., 0
        for cell, sources in enumerate(buckets):
            x, z = cell % 72, cell // 72
            journal.update(operation='裁切地表及精细支撑数据', current=cell + 1, total=72 * 72)
            minx, minz = map_bounds['minX'] + x * 125, map_bounds['minZ'] + z * 125
            bounds, center = [minx, minx + 125, minz, minz + 125], (minx + 62.5, minz + 62.5)
            primitives, floor_positions, floor_sources, floor_roles = [], [], [], []
            for pi, ids in sources:
                source = prepared[pi]
                faces, source_ids, slots = clip_triangles(source['faces'][ids], ids.astype(np.uint32), source['slots'][ids], bounds)
                if not len(faces):
                    continue
                part = source['part']
                coverage[pi] += len(faces)
                roles = np.asarray(source['roles'], dtype=np.uint32)[slots]
                p = faces[:, :, :3].copy()
                p[:, :, 0] -= center[0]
                p[:, :, 2] -= center[1]
                floor_positions.append(p.astype(np.float32))
                floor_sources.append((np.uint32(pi) << np.uint32(20)) | source_ids)
                floor_roles.append(roles)
                for axis, a, b in [(0, bounds[0], bounds[1]), (2, bounds[2], bounds[3])]:
                    for v, source_id in zip(faces[:, :, :3].reshape(-1, 3), np.repeat(source_ids, 3)):
                        if min(abs(v[axis] - a), abs(v[axis] - b)) < 1e-7:
                            key = (pi, int(source_id), axis, round(float(v[axis]), 6), round(float(v[2 - axis]), 6))
                            prior = seam_points.setdefault(key, [])
                            other = [abs(float(v[1]) - height) for height, owner in prior if owner != cell]
                            if other:
                                max_seam = max(max_seam, min(other));seam_count += 1
                            if not any(owner == cell and abs(height - float(v[1])) < 1e-10 for height, owner in prior):
                                prior.append((float(v[1]), cell))
                if part['category'] in ['background', 'water']:
                    continue
                terrain_key = None
                if part['category'] == 'terrain':
                    atlas = atlases[part['source_key']]
                    image_path = atlas_root / atlas['file']
                    if part['source_key'] not in atlas_pixels:
                        with Image.open(image_path) as im:
                            atlas_pixels[part['source_key']] = np.asarray(im.convert('RGBA'))
                    pixels = atlas_pixels[part['source_key']]
                    ab = atlas['bounds_source_xz']
                    ix = int(round((minx + 4425 - ab[0]) * 2.048))
                    iy = int(round((ab[3] - (4850 - minz)) * 2.048))
                    xx, yy = np.clip(np.arange(ix - 1, ix + 257), 0, pixels.shape[1] - 1), np.clip(np.arange(iy - 1, iy + 257), 0, pixels.shape[0] - 1)
                    cropped = np.ascontiguousarray(pixels[yy[:, None], xx[None, :]])
                    digest = hashlib.sha256(cropped.tobytes()).hexdigest()
                    target = output / '贴图' / (digest + '.png')
                    if not target.exists():
                        Image.fromarray(cropped).save(target)
                    terrain_key = '地表-' + digest
                    materials[terrain_key] = {'name': terrain_key, 'image': '../贴图/' + target.name, 'clamp': True,
                                             'color': [1, 1, 1, 1], 'alpha_mode': 'OPAQUE'}
                    faces[:, :, 6] = (1 + (faces[:, :, 0] - minx) * 2.048) / 258
                    faces[:, :, 7] = 1 - (1 + (faces[:, :, 2] - minz) * 2.048) / 258
                for slot, key in enumerate(source['materials']):
                    selected = faces[slots == slot]
                    if len(selected):
                        primitives.append(primitive(selected, terrain_key or key, center, {'source_key': part['source_key']}))
            if not floor_positions:
                chunks.append({'id': f'{x:02}-{z:02}', 'empty': True, 'bounds': bounds})
                continue
            geometry_path = output / '支撑' / f'{x:02}-{z:02}.bin'
            geometry_info = write_geometry(geometry_path, np.concatenate(floor_positions), np.concatenate(floor_sources), np.concatenate(floor_roles), center)
            file = output / '分块' / f'{x:02}-{z:02}.glb'
            visible = write_glb(file, primitives, materials, (center[0], 0, center[1])) if primitives else None
            chunks.append({'id': f'{x:02}-{z:02}', 'bounds': bounds, 'geometry': geometry_path.relative_to(output).as_posix(),
                'geometry_identity': fingerprint(geometry_path), 'geometry_info': geometry_info,
                'file': file.relative_to(output).as_posix() if visible else None, 'identity': fingerprint(file) if visible else None,
                'render': visible})
            if cell % 72 == 71:
                atomic_json(output / '进行中.json', {'rows_completed': z + 1, 'chunks': len(chunks), 'max_seam': max_seam})
        if max_seam > .001:
            raise ValueError('同源分块接缝超出0.001米')
        result = {'schema': 'airbase-surface-chunks-v1', 'status': '分块及精细几何支撑完成，待独立回读及运行接入',
            'bounds': map_bounds, 'grid': [72, 72], 'chunk_m': 125, 'chunks': chunks,
            'overview': {'file': '总览/地形.glb', 'identity': fingerprint(output / '总览/地形.glb'), **overview_record},
            'background': {'file': '总览/背景水面.glb', 'identity': fingerprint(output / '总览/背景水面.glb'), **background_record},
            'sources': [{'id': i, 'source_key': p['part']['source_key'], 'name': p['part']['name'], 'category': p['part']['category'],
                'source_triangles': len(p['faces']), 'clipped_triangles': int(coverage[i]), 'global_retained': p['part']['category'] in ['background', 'water']}
                for i, p in enumerate(prepared)], 'roles': ROLES, 'source': fingerprint(source_file),
            'seams': {'pairs': seam_count, 'max_error_m': max_seam}, 'human_acceptance': '待人工验收'}
        atomic_json(output / '地表索引.json', result)
        journal.close(status='全图地表分块候选完成，继续独立几何查询核验', chunks=len(chunks))
        print(json.dumps({'chunks': len(chunks), 'max_seam': max_seam, 'geometry_bytes': sum(c.get('geometry_info', {}).get('bytes', 0) for c in chunks)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图分块失败，保留恢复点', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.revision)
