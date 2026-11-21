"""从冻结源网格建立新BVH，与网页解码器逐块样点独立比较，拒绝虚构地面。"""

import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import Journal, atomic_json, fingerprint


def run(directory, runtime_revision, run):
    runtime = directory / '全图分块' / runtime_revision
    evidence = runtime / '支撑核验' / run
    target = evidence / '独立BVH凭证.json'
    if target.exists():
        raise FileExistsError(target)
    samples = json.loads((evidence / '样点.json').read_text('utf-8'))
    source_root = directory / '表面来源/full-r01'
    sources = json.loads((source_root / '直接表面.json').read_text('utf-8'))
    journal = Journal(directory)
    try:
        trees = []
        for fallback in [False, True]:
            vertices, triangles, offset = [], [], 0
            for part in sources['parts']:
                if fallback != (part['category'] == 'background') or part['category'] == 'water':
                    continue
                included = []
                for slot, key in enumerate(part['materials']):
                    material = sources['materials'][key]
                    # 参考直接从源类型和材质身份选择，不读取分块后的角色字节。
                    ground = part['category'] in ['terrain', 'road', 'background'] or material['shader']['name'] in ['Shader Graphs/Detail_CC', 'Shader Graphs/WeatherLit'] or material['name'] in ['Road_decal', 'Parking', 'line_marking', 'line_marking_yellow']
                    if ground:
                        included.append(slot)
                if not included:
                    continue
                with np.load(source_root / part['file']) as a:
                    world = np.asarray(part['source_world_matrix'])
                    v = a['source_vertices'] @ world[:3, :3].T + world[:3, 3]
                    selected = np.isin(a['material_indices'], included)
                    faces = a['source_triangles'][selected]
                vertices.append(v)
                triangles.append(faces + offset)
                offset += len(v)
            trees.append(BVHTree.FromPolygons(np.concatenate(vertices).tolist(), np.concatenate(triangles).tolist(), all_triangles=True))
        layer_trees = {}
        for layer in sorted({s['query_layer'] for s in samples if 'query_layer' in s}):
            part = sources['parts'][layer]
            with np.load(source_root / part['file']) as a:
                world = np.asarray(part['source_world_matrix'])
                vertices = a['source_vertices'] @ world[:3, :3].T + world[:3, 3]
                faces = a['source_triangles']
            layer_trees[layer] = BVHTree.FromPolygons(vertices.tolist(), faces.tolist(), all_triangles=True)
        errors, failures = [], []
        for index, sample in enumerate(samples):
            if index % 512 == 0:
                journal.update(phase='A2独立高度核验', operation='源网格新BVH逐块射线', current=index, total=len(samples))
            origin = Vector((sample['x'] + 4425, 1000, 4850 - sample['z']))
            if 'query_layer' in sample:
                heights = []
                tree = layer_trees[sample['query_layer']]
                for _ in range(64):
                    hit = tree.ray_cast(origin, Vector((0, -1, 0)), 2000)[0]
                    if hit is None:
                        break
                    heights.append(float(hit.y-136))
                    origin.y = hit.y-.0001
                expected = min(heights, key=lambda h: abs(h-sample['referenceY'])) if heights else None
            else:
                hit = trees[0].ray_cast(origin, Vector((0, -1, 0)), 2000)[0]
                if hit is None:
                    hit = trees[1].ray_cast(origin, Vector((0, -1, 0)), 2000)[0]
                expected = float(hit.y - 136) if hit is not None else None
            if (expected is None) != (sample['height'] is None):
                failures.append({**sample, 'bvh_height': expected, 'reason': '支撑状态不一致'})
            elif expected is not None:
                error = abs(expected - sample['height'])
                errors.append(error)
                if error > .1:
                    failures.append({**sample, 'bvh_height': expected, 'error_m': error})
        p95 = float(np.quantile(errors, .95)) if errors else 0
        maximum = max(errors, default=0)
        passed = not failures and p95 <= .05 and maximum <= .1
        result = {'passed': passed, 'samples': len(samples), 'finite_samples': len(errors), 'p95_error_m': p95,
                  'max_error_m': maximum, 'failures': failures, 'source_manifest': fingerprint(source_root / '直接表面.json'),
                  'samples_identity': fingerprint(evidence / '样点.json'), 'source_program': fingerprint(Path(__file__)),
                  'policy': '独立BVH零容差；主地面及道路优先，背景只作无主支撑处回退，森林壳及水面不当成行走地面',
                  'human_acceptance': '待人工验收'}
        atomic_json(target, result)
        journal.close(status='全块高度独立核验通过' if passed else '全块高度独立核验失败，保留差异', p95=p95, maximum=maximum)
        print(json.dumps({'passed': passed, 'samples': len(samples), 'p95': p95, 'max': maximum, 'failures': len(failures)}))
        if not passed:
            raise ValueError('独立高度比较未通过')
    except BaseException as error:
        if journal.thread.is_alive():
            journal.close(status='独立高度核验异常', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--runtime-revision', required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for name in [args.runtime_revision, args.run]:
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):
            raise ValueError('无效修订名')
    run(directory, args.runtime_revision, args.run)
