"""为全图远景保存真实道面、直接表面与较大固定布景分页；不读入全部高细节原型。"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint
from 地图GLB import write_glb
from 分块全图 import role_for
from 分页全图实例 import RECORD


def run(directory, revision):
    output = directory / '远景来源' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '贴图').mkdir()
    root = directory / '表面来源/full-r01'
    source = json.loads((root / '直接表面.json').read_text('utf-8'))
    materials, primitives, textures = {}, [], {}
    journal = Journal(directory)
    try:
        for i, part in enumerate(source['parts']):
            if part['category'] not in ['road', 'direct']:
                continue
            journal.update(phase='A2远景来源', operation='保留全图道路和直接表面', current=i, total=len(source['parts']))
            file = root / part['file']
            if fingerprint(file)['sha256'] != part['identity']['sha256']:
                raise ValueError('远景来源网格变化')
            with np.load(file) as data:
                vertices = data['vertices'][:, [0, 2, 1]].copy()
                vertices[:, 2] *= -1
                normals = data['normals'][:, [0, 2, 1]].copy()
                normals[:, 2] *= -1
                triangles, slots = data['triangles'], data['material_indices']
                uv = data['uv0'].copy()
                uv[:, 1] = 1-uv[:, 1]
            for slot, key in enumerate(part['materials']):
                m = source['materials'][key]
                if key not in materials:
                    record = {'name': m['name'], 'source_key': key, 'color': m.get('color', [1, 1, 1, 1]),
                        'alpha_mode': m['alpha_mode'], 'alpha_cutoff': m['alpha_cutoff'], 'double_sided': m['double_sided'], 'render_role': role_for(part, m)}
                    if m.get('basecolor'):
                        image = Path(m['basecolor'])
                        digest = fingerprint(image)['sha256']
                        if digest not in textures:
                            target = output / '贴图' / (digest+'.png')
                            with Image.open(image) as pixels:
                                pixels = pixels.convert('RGBA')
                                pixels.thumbnail((512, 512), Image.Resampling.LANCZOS)
                                pixels.save(target)
                            textures[digest] = {'file': target.relative_to(output).as_posix(), 'identity': fingerprint(target), 'source': fingerprint(image)}
                        record['image'] = textures[digest]['file']
                        info = next(v for v in m['textures'].values() if v['file'] == m['basecolor'])
                        record['scale'] = [info['scale']['x'], info['scale']['y']]
                        record['offset'] = [info['offset']['x'], info['offset']['y']]
                    materials[key] = record
                faces = triangles[slots == slot]
                if len(faces):
                    face_normals = normals[faces].copy()
                    geometric = np.cross(vertices[faces[:, 1]]-vertices[faces[:, 0]], vertices[faces[:, 2]]-vertices[faces[:, 0]])
                    geometric /= np.maximum(np.linalg.norm(geometric, axis=1, keepdims=True), 1e-30)
                    face_normals = np.where((np.linalg.norm(face_normals, axis=2)<1e-8)[:, :, None], geometric[:, None], face_normals)
                    face_normals /= np.maximum(np.linalg.norm(face_normals, axis=2, keepdims=True), 1e-30)
                    primitives.append({'material': key, 'attributes': {'POSITION': vertices[faces].reshape(-1, 3),
                        'NORMAL': face_normals.reshape(-1, 3), 'TEXCOORD_0': uv[faces].reshape(-1, 2)}, 'extras': {'source_key': part['source_key'], 'category': part['category']}})
        geometry = write_glb(output / '道面与直接表面.glb', primitives, materials)
        index_file = directory / '实例分页/r01/实例索引.json'
        index = json.loads(index_file.read_text('utf-8'))
        selected, total = [], 0
        for page in index['pages']:
            file = index_file.parent / page['file']
            if fingerprint(file)['sha256'] != page['identity']['sha256']:
                raise ValueError('远景来源实例页变化')
            records = np.fromfile(file, dtype=RECORD, offset=16)
            size = (records['bounds'][:, 3:] - records['bounds'][:, :3]).max(1)
            building = np.array([index['prototypes'][pi]['category'] == 'building' for pi in records['prototype']])
            bank = (records['flags'] & 2) == 0
            values = records[bank & (building | (size >= 15))]
            selected.append(values);total += len(values)
        values = np.concatenate(selected)
        import struct
        with (output / '固定布景.bin').open('xb') as stream:
            stream.write(struct.pack('<4sIII', b'BAIP', 1, len(values), RECORD.itemsize))
            stream.write(values.tobytes())
        atomic_json(output / '远景来源.json', {'schema': 'airbase-overview-source-v1', 'surface': geometry, 'instances': total,
            'source_surface': fingerprint(root / '直接表面.json'), 'source_instances': fingerprint(index_file), 'textures': textures,
            'files': {name: fingerprint(output / name) for name in ['道面与直接表面.glb', '固定布景.bin']},
            'source_program': fingerprint(Path(__file__)), 'policy': '全部建筑及包围尺寸至少15米的固定布景；较小装饰仍由近景分页保留，树木使用真实冠层远景壳，草丛省略', 'human_acceptance': '待人工验收'})
        journal.close(status='全图远景来源候选完成，待原型细节层及浏览器核验', instances=total)
        print(json.dumps({'surface_triangles': geometry['triangles'], 'instances': total}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图远景来源失败', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.revision)
