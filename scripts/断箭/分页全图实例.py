"""按125米唯一所有者分页冻结Bank和树木实例，保留跨块包围盒引用与来源编号。"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import struct

import numpy as np

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint

RECORD = np.dtype([('prototype', '<u4'), ('group', '<u4'), ('source_index', '<u4'), ('flags', '<u4'),
                   ('matrix', '<f4', (16,)), ('bounds', '<f4', (6,))])


def corners(bounds):
    low, high = np.asarray(bounds)
    return np.array([[x, y, z] for x in [low[0], high[0]] for y in [low[1], high[1]] for z in [low[2], high[2]]])


def trs_matrices(trs):
    """与源放置表相同的xyzw四元数公式，不额外归一化或改动源缩放。"""
    x, y, z, w = trs[:, 3:7].T
    result = np.tile(np.eye(4), (len(trs), 1, 1))
    result[:, :3, :3] = np.stack([1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)], axis=1).reshape(-1, 3, 3)
    result[:, :3, :3] *= trs[:, None, 7:10]
    result[:, :3, 3] = trs[:, :3]
    return result


def records_for(matrices, prototype, group, indices, bounds, flags, origin):
    points = corners(bounds)
    world = np.einsum('nij,kj->nki', matrices[:, :3, :3], points) + matrices[:, None, :3, 3]
    low, high = world.min(1), world.max(1)
    conversion = np.diag([1., 1., -1., 1.])
    runtime = conversion[None, :, :] @ matrices @ conversion[None, :, :]
    runtime[:, :3, 3] = (matrices[:, :3, 3] - origin) * [1, 1, -1]
    record = np.zeros(len(matrices), dtype=RECORD)
    record['prototype'], record['group'], record['source_index'] = prototype, group, indices
    record['flags'] = flags
    record['matrix'] = runtime.transpose(0, 2, 1).reshape(-1, 16)
    record['bounds'] = np.column_stack([low[:, 0] - origin[0], low[:, 1] - origin[1], origin[2] - high[:, 2],
                                       high[:, 0] - origin[0], high[:, 1] - origin[1], origin[2] - low[:, 2]])
    cross = (low[:, 0] < 0) | (high[:, 0] > 9000) | (low[:, 2] < 0) | (high[:, 2] > 9000)
    record['flags'][cross] |= 1
    return record, np.stack([low, high], axis=1)


def run(directory, revision, prototype_revision):
    output = directory / '实例分页' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '实例页').mkdir()
    frozen = directory / '范围冻结/V001-r02'
    scope = json.loads((frozen / '范围与条目.json').read_text('utf-8'))
    prototypes = json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))
    by_id = {p['asset_id']: (i, p) for i, p in enumerate(prototypes)}
    origin = np.asarray(scope['source_origin'])
    journal = Journal(directory)
    groups, arrays, source_checks = [], [], []
    try:
        for index, bank in enumerate(scope['banks']):
            journal.update(phase='A2实例分页', operation='Bank来源逐项对账', current=index + 1, total=len(scope['banks']))
            file = frozen / bank['file']
            if fingerprint(file)['sha256'] != bank['identity']['sha256']:
                raise ValueError('冻结Bank文件变化')
            with np.load(file) as source:
                mask = source['intersects']
                ids = source['source_indices'][mask]
                matrices = trs_matrices(source['trs'][mask].astype(float))
                expected = source['world_bounds'][mask]
            pi, prototype = by_id[bank['asset_id']]
            group = len(groups)
            records, actual = records_for(matrices, pi, group, ids, prototype['source_local_bounds'], 0, origin)
            error = float(np.max(np.abs(actual - expected))) if len(ids) else 0
            if error > .002 or len(records) != bank['required_intersections']:
                raise ValueError('Bank分页与冻结来源不符')
            arrays.append(records)
            groups.append({'kind': 'bank', 'source_key': bank['source_key'], 'resource_path': bank['resource_path']})
            source_checks.append({'source_key': bank['source_key'], 'records': len(records), 'bounds_error_m': error})
        vegetation = json.loads((directory / '来源/植被来源索引.json').read_text('utf-8'))
        for tree in scope['trees']:
            journal.update(operation='树木来源逐项对账', source_key=str(tree['terrain_data']))
            file = frozen / tree['file']
            if fingerprint(file)['sha256'] != tree['identity']['sha256']:
                raise ValueError('冻结树木文件变化')
            terrain = next(v for v in vegetation if v['data_id'] == tree['terrain_data'])
            group = len(groups)
            groups.append({'kind': 'tree', 'source_key': f"{terrain['data_node']}:{terrain['data_id']}"})
            with np.load(file) as source:
                for ti, entry in enumerate(terrain['detail_database']['m_TreePrototypes']):
                    mask = source['intersects'] & (source['prototype_indices'] == ti)
                    positions, parameters = source['positions'][mask].astype(float), source['parameters'][mask].astype(float)
                    ids = source['source_indices'][mask]
                    angle = parameters[:, 2]
                    matrices = np.tile(np.eye(4), (len(ids), 1, 1))
                    matrices[:, :3, 3] = positions
                    c, s = np.cos(angle), np.sin(angle)
                    matrices[:, :3, :3] = np.stack([c, c*0, s, c*0, c*0+1, c*0, -s, c*0, c], axis=1).reshape(-1, 3, 3)
                    matrices[:, :3, :3] *= parameters[:, None, [0, 1, 0]]
                    key = f"{terrain['data_node']}:{entry['prefab']['m_PathID']}"
                    pi, prototype = next((i, p) for i, p in enumerate(prototypes) if p['source_key'] == key)
                    records, _ = records_for(matrices, pi, group, ids, prototype['source_local_bounds'], 2, origin)
                    arrays.append(records)
                    source_checks.append({'source_key': key, 'records': len(records), 'tree_source_group': group})
        records = np.concatenate(arrays)
        expected = scope['counts']['bank_required'] + scope['counts']['trees_required']
        if len(records) != expected or len(np.unique(np.column_stack([records['group'], records['source_index']]), axis=0)) != expected:
            raise ValueError('分页来源计数或唯一身份不守恒')
        centers = records['matrix'][:, [12, 14]]
        owners = np.clip(np.floor((centers - [scope['runtime_bounds']['minX'], scope['runtime_bounds']['minZ']]) / 125), 0, 71).astype(int)
        owner_keys = owners[:, 0] + owners[:, 1] * 72
        order = np.argsort(owner_keys, kind='stable')
        keys, starts, counts = np.unique(owner_keys[order], return_index=True, return_counts=True)
        pages, references = [], defaultdict(set)
        for number, (key, start, count) in enumerate(zip(keys, starts, counts)):
            journal.update(operation='写入实例页及跨块引用', current=number + 1, total=len(keys))
            page = records[order[start:start + count]]
            px, pz = int(key % 72), int(key // 72)
            name = f'{px:02}-{pz:02}'
            file = output / '实例页' / (name + '.bin')
            with file.open('xb') as stream:
                stream.write(struct.pack('<4sIII', b'BAIP', 1, len(page), RECORD.itemsize))
                stream.write(page.tobytes())
            bounds = page['bounds'].astype(float)
            low = np.clip(np.floor((bounds[:, [0, 2]] - [scope['runtime_bounds']['minX'], scope['runtime_bounds']['minZ']]) / 125), 0, 71).astype(int)
            high = np.clip(np.floor((bounds[:, [3, 5]] - [scope['runtime_bounds']['minX'], scope['runtime_bounds']['minZ']]) / 125), 0, 71).astype(int)
            for a, b in zip(low, high):
                for cz in range(a[1], b[1] + 1):
                    for cx in range(a[0], b[0] + 1):
                        references[f'{cx:02}-{cz:02}'].add(name)
            pages.append({'id': name, 'file': file.relative_to(output).as_posix(), 'count': len(page), 'identity': fingerprint(file)})
        index = {'schema': 'airbase-instance-pages-v1', 'status': '来源分页完成，待独立回读及运行驻留核验',
            'bounds': scope['runtime_bounds'], 'chunk_m': 125, 'grid': [72, 72], 'count': len(records), 'record_bytes': RECORD.itemsize,
            'layout': ['u32 prototype', 'u32 source_group', 'u32 source_index', 'u32 flags', 'f32 matrix_column_major[16]', 'f32 minXYZ_maxXYZ[6]'],
            'flags': {'cross_boundary': 1, 'tree': 2}, 'groups': groups,
            'prototypes': [{'id': p['asset_id'], 'source_key': p['source_key'], 'category': p['category'],
                'glb': (Path('独立原型') / prototype_revision / p['asset_id'].split(':')[1] / '原型.glb').as_posix(),
                'glb_identity': fingerprint(directory / '独立原型' / prototype_revision / p['asset_id'].split(':')[1] / '原型.glb')} for p in prototypes],
            'pages': pages, 'chunk_references': {k: sorted(v) for k, v in references.items()}, 'source_checks': source_checks,
            'scope': fingerprint(frozen / '范围与条目.json'), 'source_program': fingerprint(Path(__file__)),
            'grass_policy': '草丛保留独立密度来源，不在此处把非零格当作既定实例', 'human_acceptance': '待人工验收'}
        atomic_json(output / '实例索引.json', index)
        journal.close(status='Bank与树木分页完成，继续A2', instances=len(records), pages=len(pages))
        print(json.dumps({'instances': len(records), 'pages': len(pages), 'bytes': sum(p['identity']['bytes'] for p in pages)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图实例分页失败，保留恢复点', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--prototype-revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.revision, args.prototype_revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.revision, args.prototype_revision)
