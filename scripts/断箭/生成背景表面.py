"""保留两份源背景几何，以真实世界投影底色替换缺失的运行时Splat材质。"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np

from 地图执行记录 import ROOT, atomic_json, fingerprint
from 地图GLB import write_glb


def run(args):
    directory = args.directory.resolve();directory.relative_to(ROOT / 'local-data/断箭/批次')
    output = directory / '背景来源' / args.revision
    output.mkdir(parents=True, exist_ok=False);(output / '贴图').mkdir()
    atlas_root = directory / '背景派生' / args.atlas_revision
    atlas_file = atlas_root / '背景底色.json'
    atlas = json.loads(atlas_file.read_text('utf-8'))
    if fingerprint(atlas_root / atlas['image'])['sha256'] != atlas['image_identity']['sha256']:
        raise ValueError('背景图集变化')
    source_root = directory / '表面来源/full-r01'
    source_file = source_root / '直接表面.json'
    source = json.loads(source_file.read_text('utf-8'))
    primitives, materials, copies = [], {}, {}
    for part in source['parts']:
        if part['category'] != 'background':
            continue
        source_path = source_root / part['file']
        if fingerprint(source_path)['sha256'] != part['identity']['sha256']:
            raise ValueError('源背景几何变化')
        with np.load(source_path) as arrays:
            positions = arrays['vertices'][:, [0, 2, 1]].copy();positions[:, 2] *= -1
            normals = arrays['normals'][:, [0, 2, 1]].copy();normals[:, 2] *= -1
            triangles, slots = arrays['triangles'], arrays['material_indices']
            original = arrays['uv0'].copy();uv = original.copy();uv1 = arrays['uv1'].copy()
        mixed = part['source_key'] == atlas['source_key']
        if mixed:
            bounds = atlas['bounds_source_xz'];size = bounds[1]-bounds[0]
            uv = np.column_stack([(positions[:, 0]+4425-bounds[0])/size, (4850-positions[:, 2]-bounds[2])/size]).astype(np.float32)
        uv[:, 1] = 1-uv[:, 1]
        if uv1.size:
            uv1[:, 1] = 1-uv1[:, 1]
        for slot, key in enumerate(part['materials']):
            m = source['materials'][key]
            material = {'name': m['name'], 'source_key': key, 'color': m.get('color', [1, 1, 1, 1]),
                        'alpha_mode': m['alpha_mode'], 'alpha_cutoff': m['alpha_cutoff'],
                        'double_sided': m['double_sided'], 'render_role': 4}
            image = atlas_root / atlas['image'] if mixed else Path(m['basecolor']) if m.get('basecolor') else None
            if image:
                filename = '背景源十二层底色.png' if mixed else fingerprint(image)['sha256']+'.png'
                target = output / '贴图' / filename
                if not target.exists():
                    shutil.copy2(image, target)
                copies[target.relative_to(output).as_posix()] = fingerprint(target)
                material.update(image=target.relative_to(output).as_posix(), clamp=mixed)
                if not mixed:
                    info = next(v for v in m['textures'].values() if v['file'] == m['basecolor'])
                    material.update(scale=[info['scale']['x'], info['scale']['y']], offset=[info['offset']['x'], info['offset']['y']])
            materials[key] = material
            faces = triangles[slots == slot]
            if not len(faces):
                continue
            face_normals = normals[faces].copy()
            geometric = np.cross(positions[faces[:, 1]]-positions[faces[:, 0]], positions[faces[:, 2]]-positions[faces[:, 0]])
            geometric /= np.maximum(np.linalg.norm(geometric, axis=1, keepdims=True), 1e-30)
            face_normals = np.where((np.linalg.norm(face_normals, axis=2)<1e-8)[:, :, None], geometric[:, None], face_normals)
            face_normals /= np.maximum(np.linalg.norm(face_normals, axis=2, keepdims=True), 1e-30)
            attributes = {'POSITION': positions[faces].reshape(-1, 3), 'NORMAL': face_normals.reshape(-1, 3),
                          'TEXCOORD_0': uv[faces].reshape(-1, 2), '_SOURCE_UV0': original[faces].reshape(-1, 2)}
            if uv1.size:
                attributes['TEXCOORD_1'] = uv1[faces].reshape(-1, 2)
            primitives.append({'material': key, 'attributes': attributes, 'extras': {'source_key': part['source_key'], 'category': 'background'}})
    result = write_glb(output / '背景.glb', primitives, materials)
    atomic_json(output / '背景表面.json', {'geometry': result, 'source_surface': fingerprint(source_file),
        'source_atlas': fingerprint(atlas_file), 'source_program': fingerprint(Path(__file__)), 'images': copies,
        'policy': '源位置、绕序和孔洞保留，原UV0另存；只更新缺失混合底色及其世界UV，继续独立编码回读和网页审图'})
    print(json.dumps({'triangles': result['triangles'], 'image_count': len(copies)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--atlas-revision', required=True)
    args = parser.parse_args()
    for value in [args.revision, args.atlas_revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(args)
