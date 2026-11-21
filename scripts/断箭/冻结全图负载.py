"""按完整来源面数和原生植被LOD1冻结建筑／植被密集区与道路邻接主负载位置。"""

import argparse
import json
from pathlib import Path

import numpy as np

from 地图执行记录 import ROOT, atomic_json, fingerprint
from 分页全图实例 import RECORD


def run(directory, revision):
    output = directory / '负载冻结' / revision
    output.mkdir(parents=True, exist_ok=False)
    source = directory / '实例分页/r01/实例索引.json'
    index = json.loads(source.read_text('utf-8'))
    builds = {r['asset_id']: r['triangles'] for r in json.loads((directory / '独立原型/full-r06/build-汇总.json').read_text('utf-8'))}
    plants = {r['asset_id']: r['triangles'] for r in json.loads((directory / '植被细节/project-r01/build-汇总.json').read_text('utf-8'))}
    weights, banks, trees, counts = [np.zeros((72, 72), dtype=np.int64) for _ in range(4)]
    for page in index['pages']:
        x, z = map(int, page['id'].split('-'))
        records = np.fromfile(source.parent / page['file'], dtype=RECORD, offset=16)
        for pi, count in zip(*np.unique(records['prototype'], return_counts=True)):
            prototype = index['prototypes'][int(pi)]
            faces = plants.get(prototype['id'], builds[prototype['id']])
            weights[z, x] += int(count)*faces
            (trees if prototype['category'] == 'tree' else banks)[z, x] += int(count)*faces
            counts[z, x] += int(count)
    graph_file = directory / '通行粗层/r01/分区通行.json'
    graph = json.loads(graph_file.read_text('utf-8'))
    candidates = []
    b = index['bounds']
    for z in range(2, 70):
        for x in range(2, 70):
            area = (slice(z-1, z+2), slice(x-1, x+2))
            node = graph['nodes'][z*72+x]
            candidates.append({'x': b['minX']+(x+.5)*125, 'z': b['minZ']+(z+.5)*125, 'cell': f'{x:02}-{z:02}',
                'source_faces_375m': int(weights[area].sum()), 'bank_faces_375m': int(banks[area].sum()),
                'plant_faces_375m': int(trees[area].sum()), 'source_instances_375m': int(counts[area].sum()),
                'road': node['road'], 'water_samples': node['water_samples']})
    dense = max(candidates, key=lambda r: r['source_faces_375m'])
    building = max(candidates, key=lambda r: r['bank_faces_375m'])
    plant = max(candidates, key=lambda r: r['plant_faces_375m'])
    on_road = [r for r in candidates if r['road'] and r['water_samples'] == 0]
    main = max(on_road, key=lambda r: r['bank_faces_375m'])
    result = {'schema': 'airbase-workload-freeze-v1', 'dense_static': dense, 'dense_buildings': building, 'dense_vegetation': plant,
        'main_load': main, 'source': fingerprint(source), 'coarse_graph': fingerprint(graph_file), 'source_program': fingerprint(Path(__file__)),
        'policy': '375米来源邻域独立预测；主移动负载取有道路且常规采样无水域的最大建筑面数区，另外覆盖全部静态和植被预测峰值；实际提交与帧时间另测',
        'human_acceptance': '待人工验收'}
    atomic_json(output / '负载与位置.json', result)
    print(json.dumps({k: result[k] for k in ['dense_static', 'dense_buildings', 'dense_vegetation', 'main_load']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效负载修订')
    run(directory, args.revision)
