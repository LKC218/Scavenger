"""用已冻结的直接表面制作独立工程、GLB和四向对照，支持新进程严格回读。"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 构建全图原型 import build_one, readback_one
from 地图执行记录 import Journal, atomic_json, fingerprint


def run(directory, source_revision, revision, mode):
    source = directory / '表面来源' / source_revision
    source_path = source / '直接表面.json'
    data = json.loads(source_path.read_text('utf-8'))
    output = directory / '表面样本' / revision
    if mode == 'build':
        output.mkdir(parents=True, exist_ok=False)
        (output / '输入').mkdir()
    journal = Journal(directory)
    results = []
    try:
        for index, part in enumerate(data['parts']):
            journal.update(phase='A1特殊表面样本', operation='直接表面构建' if mode == 'build' else '直接表面独立回读',
                           current=index + 1, total=len(data['parts']), source_key=part['source_key'])
            name = part['source_key'].replace(':', '-')
            contract = output / '输入' / (name + '.json')
            mesh_file = source / part['file']
            if fingerprint(mesh_file)['sha256'] != part['identity']['sha256']:
                raise ValueError('冻结的表面原始属性变化')
            with np.load(mesh_file) as arrays:
                points = arrays['vertices'][:, [0, 2, 1]]
                local_bounds = [points.min(0).tolist(), points.max(0).tolist()]
            if mode == 'build':
                snapshot = {'name': part['name'], 'source_path': part['source_key'], 'warnings': part['warnings'],
                    'nodes': [{'id': part['source_key'], 'name': part['name'], 'parent': None, 'active': True,
                        'matrix': np.eye(4).tolist(), 'meshes': [{'file': str(mesh_file), 'materials': part['materials'],
                            'renderer_id': str(part['renderer_id']), 'skinned': False, 'triangles': part['triangles']}]}],
                    'materials': {key: data['materials'][key] for key in part['materials']}}
                atomic_json(contract, snapshot)
            row = {'asset_id': 'surface:BA_Airbase_' + name + '_v001', 'source_key': part['source_key'],
                   'exported': {'scene': str(contract)}, 'source_local_bounds': local_bounds}
            result = build_one(row, output / name, True, {}, 'unique') if mode == 'build' else readback_one(row, output / name, True)
            results.append(result)
        atomic_json(output / (mode + '-汇总.json'), {'source_manifest': fingerprint(source_path), 'parts': results,
            'scope': '直接表面特殊样本，不构成全图分块、碰撞或人工验收通过'})
        journal.close(status='特殊表面作业完成，待实际审图及A1封存', completed=len(results))
        print(json.dumps({'parts': len(results), 'mode': mode}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='特殊表面作业失败，保留恢复点', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--mode', choices=['build', 'readback'], default='build')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.source_revision, args.revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.source_revision, args.revision, args.mode)
