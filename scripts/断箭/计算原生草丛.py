"""在全新隔离Unity批次工程计算草丛原生变换；编辑器版本与来源版本分别记录。"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def run(directory, revision, editor):
    output = directory / '草丛分布' / revision
    output.mkdir(parents=True, exist_ok=False)
    project = ROOT / 'tmp/Airbase全图-V001-01a1a602' / ('草丛原生-' + revision)
    project.mkdir(parents=True, exist_ok=False)
    for folder in ['Assets/Editor', 'Packages', 'ProjectSettings', '输入']:
        (project / folder).mkdir(parents=True)
    source = directory / '草丛分布/r01/密度索引.json'
    density = json.loads(source.read_text('utf-8'))
    prototypes = json.loads((directory / '独立原型/full-r06/build-汇总.json').read_text('utf-8'))
    source_index = json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))
    by_source = {p['source_key']: p['asset_id'] for p in source_index}
    by_id = {p['asset_id']: p for p in prototypes}
    native_program = Path(__file__).with_name('草丛原生计算.cs')
    shutil.copy2(native_program, project / 'Assets/Editor/AirbaseDetailBatch.cs')
    (project / 'Packages/manifest.json').write_text(json.dumps({'dependencies': {'com.unity.modules.terrain': '1.0.0', 'com.unity.modules.physics': '1.0.0'}}), 'utf-8')
    (project / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.4.9f1\n', 'utf-8')
    native_prototypes = []
    identities = []
    for i, layer in enumerate(density['files']):
        file = source.parent / layer['file']
        if fingerprint(file)['sha256'] != layer['identity']['sha256']:
            raise ValueError('密度文件变化')
        np.load(file, allow_pickle=False).tofile(project / '输入' / f'密度-{i}.u8')
        p = dict(density['prototypes'][i])
        build = by_id[by_source[f"sharedassets7.assets:{p['prototype']['m_PathID']}"]]
        low, high = build['source_bounds_unity']
        p['boundsLow'] = dict(zip('xyz', low))
        p['boundsHigh'] = dict(zip('xyz', high))
        native_prototypes.append(p)
        identities.append({'layer': i, 'asset_id': build['asset_id'], 'source_bounds_unity': [low, high]})
    height_file = directory / '来源/TerrainData-1765.npy'
    np.load(height_file, allow_pickle=False).astype('<i2').tofile(project / '输入/高度.i16')
    config = {'input': str(project / '输入'), 'output': str(output / '原生补丁'), 'resolution': density['resolution'],
              'heightResolution': 4097, 'patchSamples': 64, 'size': dict(zip('xyz', [11052., 188.7902374267578, 11052.])),
              'prototypes': native_prototypes}
    atomic_json(project / '输入/配置.json', config)
    journal = Journal(directory)
    try:
        journal.update(phase='A2草丛原生派生', operation='独立批次工程原生计算，版本差异保留')
        args = [str(editor), '-batchmode', '-nographics', '-projectPath', str(project), '-executeMethod', 'AirbaseDetailBatch.Run',
                '-airbaseConfig', str(project / '输入/配置.json'), '-logFile', str(output / 'Unity.log'), '-disable-assembly-updater']
        result = subprocess.run(args, timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(f'Unity批次退出{result.returncode}，详见Unity.log')
        receipt = json.loads((output / '原生补丁/原生回执.json').read_text('utf-8'))
        copied = []
        for name in ['坐标夹具.asset', '完整草丛地形.asset']:
            shutil.copy2(project / 'Assets' / name, output / name)
            copied.append({'file': name, 'identity': fingerprint(output / name)})
        atomic_json(output / '计算凭证.json', {'schema': 'airbase-native-detail-v1', 'native': receipt,
            'tool_editor': fingerprint(editor), 'source_unity_header': '6000.3.24f1', 'derived_editor_version': receipt['unityVersion'],
            'source': fingerprint(source), 'height': fingerprint(height_file), 'prototypes': identities, 'serialized': copied,
            'source_program': fingerprint(Path(__file__)), 'native_program': fingerprint(native_program),
            'policy': '原密度、原高度、原尺寸参数；包围盒代理仅供原生变换计算，显示仍引用full-r06真实原型；版本不同，不声称与原游戏逐株变换相同',
            'status': '原生派生完成，待独立序列化回读及空间核验', 'human_acceptance': '待人工验收'})
        journal.close(status='草丛原生计算完成，待独立回读', count=receipt['count'])
        print(json.dumps({'version': receipt['unityVersion'], 'count': receipt['count'], 'layer_counts': receipt['layerCounts']}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='草丛原生计算失败，保留日志及隔离工程', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--editor', type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.revision, args.editor.resolve(strict=True))
