"""绑定真实原型与两公里引用工程生成A1候选契约；不升级全图或网页验收。"""

import argparse
import json
from pathlib import Path
import shutil
import sys
from PIL import Image

from 地图执行记录 import ROOT, atomic_json, fingerprint

sys.path.insert(0, str(ROOT / 'scripts/资源库'))
from 发布场景 import validate


def run(directory, prototype_revision, assembly_revision, revision):
    output = directory / '候选入库' / revision
    output.mkdir(parents=True, exist_ok=False)
    prototypes = directory / '独立原型' / prototype_revision
    assembly = directory / '引用装配/2000米' / assembly_revision
    review_path = prototypes / 'AI视觉复核-r01.json'
    review = json.loads(review_path.read_text('utf-8'))
    reviewed = {r['asset_id']: r for r in review['assets']}
    if review['ai_visual_status'] != 'passed':
        raise ValueError('候选原型尚未实际审图')
    source_rows = json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))
    physics = json.loads((directory / '来源/原型物理-r01.json').read_text('utf-8'))['assets']
    assembly_receipt = json.loads((assembly / '引用装配凭证.json').read_text('utf-8'))
    verification = json.loads((assembly / '独立核验/r01/核验凭证.json').read_text('utf-8'))
    if verification['assembly_receipt']['sha256'] != fingerprint(assembly / '引用装配凭证.json')['sha256']:
        raise ValueError('引用装配回读凭证失效')
    files, assets = {}, []

    def add(path, dependencies=()):
        path = path.resolve()
        key = path.relative_to(directory).as_posix()
        identity = fingerprint(path)
        files[key] = {k: identity[k] for k in ('bytes', 'sha256')}
        files[key]['dependencies'] = list(dependencies)
        return key

    source = {'game': 'Broken Arrow', 'build': '26273833', 'engine': '6000.3.24f1',
              'usage_scope': '本机离线研究与技术验证', 'license_status': '未取得对外展示或分发的单独许可'}
    for row in source_rows:
        identity = row['asset_id']
        folder = prototypes / identity.split(':')[1]
        build = json.loads((folder / '构建凭证.json').read_text('utf-8'))
        check = json.loads((folder / '回读凭证.json').read_text('utf-8'))
        decision = reviewed[identity]
        for name, proof in [('原型.blend', 'blend'), ('原型.glb', 'glb')]:
            if fingerprint(folder / name)['sha256'] != decision[proof]['sha256']:
                raise ValueError('审图后原型身份变化：' + identity)
        if check['build_receipt']['sha256'] != fingerprint(folder / '构建凭证.json')['sha256']:
            raise ValueError('原型回读不对应当前构建')
        refs = {'runtime': add(folder / '原型.glb'), 'blend': add(folder / '原型.blend'),
                'preview': add(folder / '回读-右侧.png')}
        evidence = [add(folder / '构建凭证.json'), add(folder / '回读凭证.json')]
        for view in ['正面', '背面', '左侧', '右侧']:
            for role in ['工程', '回读']:
                evidence.append(add(folder / f'{role}-{view}.png'))
        low, high = row['source_local_bounds']
        materials = [{key: value for key, value in m.items() if key != 'basecolor'}
                     for m in build['materials'].values()]
        texture_max = max((max(Image.open(m['basecolor']).size) for m in build['materials'].values()
                           if m.get('basecolor')), default=0)
        anchors = physics[identity]['anchor']
        assets.append({'id': identity, 'source_key': row['source_key'], 'version': 'V001',
            'name': row['name'], 'category': row['category'], 'source': {**source, 'node': row['source_node'],
                'object_id': row['source_id'], 'source_path': row['path']},
            'dimensions_m': [high[i] - low[i] for i in range(3)], 'anchor': anchors,
            'materials': materials, 'collision': physics[identity],
            'detail_levels': [{'name': '来源基础层', 'file': refs['runtime'], 'triangles': check['triangles'],
                'policy': '保留源形体及等价重合面归并；网页细节层待A2制作'}],
            'triangles': check['triangles'], 'source_triangles': check['source_triangles'],
            'texture_max': texture_max, 'files': refs, 'evidence': evidence,
            'technical_status': 'passed', 'ai_visual_status': decision['ai_visual_status'],
            'human_acceptance': '待人工验收'})
    blend_dependencies = [add(directory / row['blend_path']) for row in assembly_receipt['links']]
    blend_key = add(assembly / 'Airbase两公里引用装配.blend', blend_dependencies)
    baseline_dir = ROOT / 'local-data/断箭/批次/BA-26273833-011/交付/2000米/r02'
    baseline = json.loads((baseline_dir / '地图运行.json').read_text('utf-8'))
    ground = output / '两公里源地表.glb'
    shutil.copyfile(baseline_dir / baseline['ground'], ground)
    ground_key = add(ground)
    by_key = {r['source_key'].removeprefix('catalog:'): r for r in assets}
    runtime = {'schema': 'airbase-reference-scene-v1', 'id': 'scene:BA_Airbase_A1_reference_v001',
        'name': 'Airbase两公里引用装配候选', 'bounds': baseline['bounds'], 'package_base': '../../',
        'ground': ground_key, 'prototypes': {key: {'asset_id': by_key[key]['id'],
            'file': by_key[key]['files']['runtime']} for key in baseline['prototypes']},
        'instances': [{**r, 'asset_id': by_key[r['prototype']]['id']} for r in baseline['instances']],
        'status': 'A1两公里引用样本，非全图运行包', 'human_acceptance': '待人工验收'}
    runtime_path = output / '两公里装配.json'
    atomic_json(runtime_path, runtime)
    runtime_key = add(runtime_path, [ground_key, *[r['file'] for r in runtime['prototypes'].values()]])
    preview_key = add(assembly / '独立核验/r01/机库道路.png')
    evidence = [add(review_path), add(directory / '来源/原型物理-r01.json'),
        add(assembly / '引用装配凭证.json'), add(assembly / '独立核验/r01/核验凭证.json')]
    manifest = {'schema': 1, 'package_root': directory.relative_to(ROOT).as_posix(), 'files': files, 'assets': assets,
        'scene': {'id': runtime['id'], 'source_key': 'scene:Airbase:level7:A1_reference', 'version': 'V001',
            'name': runtime['name'], 'source': source, 'bounds': baseline['bounds'],
            'conversion': {'source_origin': [4425, 136, 4850], 'rule': 'x-4425,y-136,4850-z'},
            'coverage': {'scope': '两公里引用样本及133种独立原型，非全图完整验收', 'placed_prototypes': 34,
                'placed_instances': 162, 'independent_assets': len(assets)}, 'asset_ids': [r['id'] for r in assets],
            'files': {'manifest': runtime_key, 'blend': blend_key, 'preview': preview_key}, 'human_acceptance': '待人工验收'},
        'acceptance': {'complete': False, 'modular': True, 'web': False, 'library': False, 'ai_visual': True,
            'human': '待人工验收', 'evidence': evidence}}
    result = validate(manifest)
    atomic_json(output / '场景候选清单.json', manifest)
    atomic_json(output / '候选核验.json', {**result, 'package': manifest['package_root'],
        'manifest': fingerprint(output / '场景候选清单.json'), 'scope': '候选契约与文件闭合，不切换正式指针'})
    print(json.dumps({'assets': len(assets), 'files': len(files), 'bytes': result['bytes'], 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--prototype-revision', required=True)
    parser.add_argument('--assembly-revision', required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.prototype_revision, args.assembly_revision, args.revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.prototype_revision, args.assembly_revision, args.revision)
