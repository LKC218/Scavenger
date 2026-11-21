"""串行按原始UV和十二层图层烘焙九个地形分区，保持旧两公里每米像素密度。"""

import argparse
import json
from pathlib import Path

import numpy as np

from 导出全图原型 import FullMapExtractor
from 提取地图 import bake_terrain
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def normalized(value):
    if isinstance(value, dict):
        if set(value) == {'x', 'y'}:
            return [value['x'], value['y']]
        if set(value) == {'r', 'g', 'b', 'a'}:
            return [value[k] for k in 'rgba']
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def run(directory, source_revision, revision, prior_revision=None):
    source = directory / '表面来源' / source_revision
    data = json.loads((source / '直接表面.json').read_text('utf-8'))
    terrain = [p for p in data['parts'] if p['category'] == 'terrain']
    if len(terrain) != 9:
        raise ValueError('当前完整区域须包含九个真实高细节地形')
    output = directory / '地表派生' / revision
    output.mkdir(parents=True, exist_ok=False)
    controller_source = directory / '来源/组件完整字段/level7-961.json'
    controller = normalized(json.loads(controller_source.read_text('utf-8')))
    controller_path = output / '输入地表参数.json'
    atomic_json(controller_path, {'data': controller, 'source': fingerprint(controller_source),
                                'policy': '仅将原始向量和颜色字段转成既有烘焙器的数组结构，不修改数值'})
    # A0已重核真实输入与010一致；图层是同一原始纹理数组的冻结导出，作为只读依赖复用。
    layers = ROOT / 'local-data/断箭/批次/BA-26273833-010/原始贴图/地表数组'
    identities = {f'底色-{i}.png': fingerprint(layers / f'底色-{i}.png') for i in range(12)}
    atomic_json(output / '图层引用.json', {'directory': layers.relative_to(ROOT).as_posix(), 'files': identities,
        'input_evidence': fingerprint(directory / '输入清单.json'), 'runtime_dependency': False})
    worker = FullMapExtractor(directory, Path(json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text('utf-8'))['game_directory']))
    journal = Journal(directory)
    results = []
    try:
        worker.load('level7')
        geometry = []
        for part in terrain:
            with np.load(source / part['file']) as arrays:
                matrix = np.asarray(part['source_world_matrix'])
                geometry.append({'source_key': part['source_key'], 'renderer_id': part['renderer_id'],
                    'vertices': arrays['source_vertices'] @ matrix[:3, :3].T + matrix[:3, 3],
                    'uv': arrays['source_uv0'], 'triangles': arrays['source_triangles']})
        for index, part in enumerate(terrain):
            journal.update(phase='A2全图地表派生', operation='真实十二层底色烘焙', current=index + 1, total=9,
                           source_key=part['source_key'], resolution=6144)
            file = source / part['file']
            if fingerprint(file)['sha256'] != part['identity']['sha256']:
                raise ValueError('地形来源属性变化')
            with np.load(file) as arrays:
                vertices = arrays['source_vertices'].astype(float)
                uv = arrays['source_uv0']
                triangles = arrays['source_triangles']
            world = np.asarray(part['source_world_matrix'])
            vertices = vertices @ world[:3, :3].T + world[:3, 3]
            # 源分区边缘留有少量重叠，运行贴图以稳定的三公里方区作为域；完整源网格另行保留。
            center = (vertices.min(0) + vertices.max(0)) / 2
            x, z = int(center[0] // 3000) * 3000, int(center[2] // 3000) * 3000
            bounds = [x, x + 3000, z, z + 3000]
            if prior_revision:
                prior = directory / '地表派生' / prior_revision
                receipt = prior / '地表图集.json'
                previous = json.loads(receipt.read_text('utf-8')) if receipt.exists() else {'parts': []}
                matching = next((p for p in previous['parts'] if p['source_key'] == part['source_key']), None)
                recipe_matches = (json.loads((prior / '输入地表参数.json').read_text('utf-8'))['data'] == controller and
                                  json.loads((prior / '图层引用.json').read_text('utf-8'))['files'] == identities)
                if matching and recipe_matches and matching['bounds_source_xz'] == bounds and fingerprint(prior / matching['file'])['sha256'] == matching['identity']['sha256']:
                    import shutil
                    for old in [prior / matching['file'], prior / f"地表混合-{part['source_key'].split(':')[1]}.json"]:
                        shutil.copy2(old, output / old.name)
                    results.append({**matching, 'reused_from': prior_revision, 'source_identity': part['identity']})
                    continue
            row = {'id': int(part['source_key'].split(':')[1]),
                   'components': [{'type': 'MeshRenderer', 'id': part['renderer_id']}]}
            path = Path(bake_terrain(worker, row, vertices, uv, triangles, bounds, output, 3000,
                                    dimension=6144, controller_path=controller_path, layers_directory=layers,
                                    fallbacks=[g for g in geometry if g['source_key'] != part['source_key']], preserve_source_holes=True))
            results.append({'source_key': part['source_key'], 'bounds_source_xz': bounds, 'file': path.name,
                            'identity': fingerprint(path), 'pixels_per_m': 6144 / 3000, 'source_identity': part['identity']})
            atomic_json(output / '地表图集.json', {'status': '烘焙中', 'parts': results,
                        'source_manifest': fingerprint(source / '直接表面.json')})
        for name, identity in identities.items():
            if fingerprint(layers / name)['sha256'] != identity['sha256']:
                raise ValueError('只读图层在作业中变化')
        atomic_json(output / '地表图集.json', {'status': '九分区底色派生完成，待分块、接缝与画面核对', 'parts': results,
            'source_manifest': fingerprint(source / '直接表面.json'), 'source_program': fingerprint(Path(__file__)),
            'baker_program': fingerprint(ROOT / 'scripts/断箭/提取地图.py'), 'human_acceptance': '待人工验收'})
        journal.close(status='九分区地表底色完成，继续A2分块及独立核验', completed=9)
        print(json.dumps({'parts': len(results), 'resolution': 6144}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图地表烘焙失败，保留恢复点', error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--prior-revision', help='仅复用有完整摘要凭证的已完成图集项，其余重新生成')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.source_revision, args.revision, args.prior_revision or 'unused']:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.source_revision, args.revision, args.prior_revision)
