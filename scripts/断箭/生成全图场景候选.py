"""以真实A1独立原型、全图母版和有界运行包生成候选；不发布、不升级未完成验收。"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint

sys.path.insert(0, str(ROOT / 'scripts/资源库'))
from 发布场景 import validate, glb_dependencies


def run(args):
    directory = args.directory.resolve();directory.relative_to(ROOT / 'local-data/断箭/批次')
    output = directory / '候选入库' / args.revision
    output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads((directory / args.reference).read_text('utf-8'))
    validate(baseline)
    runtime_file = directory / '运行接入' / args.runtime_revision / '地图运行.json'
    runtime = json.loads(runtime_file.read_text('utf-8'))
    if runtime['schema'] != 'airbase-streaming-scene-v1':
        raise ValueError('候选必须引用真实全图运行清单')
    if not runtime.get('water') or len(runtime.get('water_layer',{}).get('source_keys',[]))!=2:
        raise ValueError('完整全图候选必须显式包含两处原水面的渲染层')
    files, assets = {}, copy.deepcopy(baseline['assets'])
    journal = Journal(directory)

    def add(path, dependencies=None, expected=None):
        path = path.resolve();key = path.relative_to(directory).as_posix()
        if key in files:
            return key
        identity = fingerprint(path)
        if expected and (identity['sha256'] != expected['sha256'] or identity['bytes'] != expected['bytes']):
            raise ValueError('候选来源文件变化：'+key)
        if dependencies is None:
            dependencies = sorted(glb_dependencies(path, key)) if path.suffix.lower() == '.glb' else []
        files[key] = {'bytes': identity['bytes'], 'sha256': identity['sha256'], 'dependencies': list(dependencies)}
        return key

    try:
        journal.update(phase='A3全图入库候选', operation='绑定133种独立原型与完整工程')
        for asset in assets:
            for key in [*asset['files'].values(), *asset['evidence']]:
                add(directory / key, baseline['files'][key].get('dependencies', []), baseline['files'][key])
            original = asset['name'];asset['aliases'] = [original]
            if original == 'building_Air_76':
                asset.update(name='固定运输机布景（伊尔76）', category='decor')
                asset['source']['original_category'] = 'building'
            asset['detail_levels'][0]['policy'] = '完整来源基础层及等价重合面归并；运行细节层另存，人工验收独立保留'
        journal.update(operation='核对全图运行媒体及真实GLB外部依赖', total=len(runtime['files']))
        runtime_keys = []
        for i, (key, identity) in enumerate(runtime['files'].items()):
            if i % 256 == 0:
                journal.update(current=i)
            runtime_keys.append(add(directory / key, expected=identity))
        master = directory / '总装配' / args.master_revision
        master_record = json.loads((master / '母版构建凭证.json').read_text('utf-8'))
        master_check = json.loads((master / '独立核验/r01/核验凭证.json').read_text('utf-8'))
        if not master_check['passed'] or master_check['build_receipt']['sha256'] != fingerprint(master / '母版构建凭证.json')['sha256']:
            raise ValueError('全图母版回读失效')
        blend_links = [add(directory / row['file'], expected=row['identity']) for row in master_record['libraries']]
        blend = add(master / 'Airbase全图母版.blend', blend_links, master_record['blend_identity'])

        snapshot = output / '运行源码'
        code_keys = []
        for source in [*(ROOT / 'src/地图验证').glob('*.mjs'), ROOT / 'src/资料库/scene-viewer.mjs', ROOT / 'src/资源加载/压缩模型加载.mjs']:
            relative = source.relative_to(ROOT / 'src')
            target = snapshot / relative;target.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(source, target)
            code_keys.append(add(target))
        tool = ROOT / 'local-data/tools/three'
        pin = json.loads((tool / '当前版本.json').read_text('utf-8'))
        vendor = tool / pin['version']
        metadata = vendor / '来源与校验.json'
        if fingerprint(metadata)['sha256'] != pin['metadata_sha256']:
            raise ValueError('Three.js版本冻结失效')
        sys.path.insert(0, str(ROOT / 'scripts'))
        from 资源优化.运行资源接入 import VENDOR as DECODERS
        from 启动正式资料库 import VENDOR
        frozen = {row['path']: row for row in json.loads(metadata.read_text('utf-8'))['files']}
        vendor_keys = []
        for name in sorted(set(VENDOR+DECODERS)):
            source = vendor / name
            if fingerprint(source)['sha256'] != frozen[name]['sha256']:
                raise ValueError('第三方依赖变化：'+name)
            target = output / 'vendor' / name;target.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(source, target)
            vendor_keys.append(add(target))
        helper = (snapshot / '资源加载/压缩模型加载.mjs').relative_to(directory).as_posix()
        for key in code_keys:
            if key.endswith('/资源加载/压缩模型加载.mjs'):
                files[key]['dependencies'] = vendor_keys
            elif key.endswith('/资料库/scene-viewer.mjs'):
                files[key]['dependencies'] = [helper, *[x for x in code_keys if '/地图验证/' in x], *vendor_keys]
            elif '/地图验证/' in key:
                # 区域模块的宿主解码器和固定Three.js闭包；相对模块统一由运行入口携带。
                files[key]['dependencies'] = [helper, *vendor_keys]

        compact = copy.deepcopy(runtime)
        compact['package_base'] = '../../..'
        compact['viewer_module'] = (snapshot / '资料库/scene-viewer.mjs').relative_to(directory).as_posix()
        compact['runtime_module'] = (snapshot / '地图验证/全图显示.mjs').relative_to(directory).as_posix()
        compact['source_runtime'] = {'file': runtime_file.relative_to(directory).as_posix(), **fingerprint(runtime_file)}
        unused = set()
        for chunk in compact['chunks']:
            unused.update([chunk['geometry'], chunk['collision']])
            chunk['geometry_identity'] = {'bytes': chunk['geometry_identity']['bytes']}
            chunk['collision_identity'] = {'bytes': chunk['collision_identity']['bytes']}
            chunk.pop('identity', None)
        unused.update(page['file'] for page in compact['pages'])
        unused.update(packet['file'] for packet in compact['simulation_packages'].values())
        compact['files'] = {key: {'bytes': row['bytes'], 'sha256': row['sha256']} for key, row in runtime['files'].items() if key not in unused}
        entry = output / '运行包/地图运行.json';entry.parent.mkdir()
        entry.write_text(json.dumps(compact, ensure_ascii=False, separators=(',', ':')), 'utf-8')
        manifest_key = add(entry, [*runtime_keys, *code_keys, *vendor_keys])

        standalone = output / '独立预览';standalone.mkdir()
        server = standalone / '场景预览.py';shutil.copy2(ROOT / 'scripts/断箭/预览已收集场景.py', server)
        server_key = add(server)
        html = standalone / '场景预览.html'
        viewer_url = '/'+compact['viewer_module']
        html.write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Airbase全图场景</title><style>html,body,#scene{margin:0;width:100%;height:100%;overflow:hidden;background:#111814;color:#e8efdd;font:14px sans-serif}canvas{display:block;width:100%;height:100%}#status{position:absolute;bottom:14px;left:14px;background:#111e17dd;padding:10px;z-index:3}.scene-tools{position:absolute;top:14px;right:14px;background:#111e17dd;padding:8px;display:flex;gap:8px;align-items:center}.scene-tools button{background:#273824;border:1px solid #63774e;color:#e8efdd;padding:6px 10px}[hidden]{display:none!important}</style><div id="scene"></div><div id="status">正在准备场景…</div><script type="importmap">{"imports":{"three":"/vendor/build/three.module.js","three/addons/":"/vendor/examples/jsm/"}}</script><script type="module">import {StreamingSceneViewer} from '''+json.dumps(viewer_url, ensure_ascii=False)+''';const url=new URL('''+json.dumps('/'+manifest_key, ensure_ascii=False)+''',location.href),manifest=await(await fetch(url)).json(),viewer=new StreamingSceneViewer(document.querySelector('#scene'),text=>document.querySelector('#status').textContent=text);window.sceneViewer=viewer;await viewer.load(url,manifest);addEventListener('pagehide',()=>viewer.dispose());</script></html>''', 'utf-8')
        html_key = add(html, [manifest_key, server_key, *code_keys, *vendor_keys])
        inventory_key = (output / '场景候选清单.json').relative_to(directory).as_posix()
        launcher = standalone / '打开场景.cmd'
        launcher.write_text('@echo off\nchcp 65001 >nul\ncd /d "%~dp0..\\..\\.."\npy -3 "%~dp0场景预览.py" --root . --manifest "'+inventory_key+'" --entry "'+html_key+'" --open\n', 'utf-8')
        launcher_key = add(launcher, [html_key, server_key])

        evidence = []
        for path in [directory / '范围冻结/V001-r02/范围与条目.json', directory / '独立原型/full-r06/AI视觉复核-r01.json',
                     directory / '来源/原型物理-r01.json', master / '母版构建凭证.json', master / '独立核验/r01/核验凭证.json',
                     master / '审图/r01/AI审图凭证.json', directory / '运行接入' / args.runtime_revision / '分区网页审图/final-r01/AI审图凭证.json',
                     directory / Path(runtime['water']).parent / '水面核验.json']:
            evidence.append(add(path))
        preview = add(master / '审图/r01/北侧.png')
        source = baseline['scene']['source']
        scene = {'id': 'scene:BA_Airbase_full_v001', 'source_key': 'scene:Airbase:level7:full', 'version': 'V001',
            'name': 'Airbase全图静态场景V001', 'source': source, 'bounds': runtime['bounds'],
            'conversion': {'source_origin': [4425, 136, 4850], 'rule': 'x-4425,y-136,4850-z'},
            'coverage': {'scope': '冻结9公里范围、完整Bank及树木；草丛按授权省略运行实例，源原型保留', 'bank_instances': 93008,
                         'tree_instances': 343234, 'independent_assets': len(assets), 'cells': 5184, 'source_runtime_sha256': fingerprint(runtime_file)['sha256']},
            'asset_ids': [asset['id'] for asset in assets],
            'files': {'manifest': manifest_key, 'runtime': launcher_key, 'web_entry': html_key, 'blend': blend, 'preview': preview},
            'human_acceptance': '待人工验收'}
        manifest = {'schema': 1, 'package_root': directory.relative_to(ROOT).as_posix(), 'files': files, 'assets': assets, 'scene': scene,
            'acceptance': {'complete': False, 'modular': True, 'web': False, 'library': False, 'ai_visual': False,
                           'human': '待人工验收', 'evidence': evidence}}
        checked = validate(manifest)
        atomic_json(output / '场景候选清单.json', manifest)
        atomic_json(output / '候选核验.json', {**checked, 'package': manifest['package_root'], 'manifest': fingerprint(output / '场景候选清单.json'),
            'source_program': fingerprint(Path(__file__)), 'status': '真实全图候选已组成，尚未发布或升级关口'})
        journal.close(status='全图候选契约通过，待库预览及真实独立收集', files=len(files), assets=len(assets))
        print(json.dumps({'files': len(files), 'assets': len(assets), 'bytes': checked['bytes'], 'manifest': str(output / '场景候选清单.json')}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图候选生成失败，保留输出', error=repr(error));raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--reference', default='候选入库/r02/场景候选清单.json')
    parser.add_argument('--runtime-revision', required=True)
    parser.add_argument('--master-revision', required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    for value in [args.runtime_revision, args.master_revision, args.revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(args)
