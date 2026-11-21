"""从相同只读输入导出八种植被的指定原生LOD，保留最高细节工程及来源不变。"""

import argparse
import json
from pathlib import Path
import shutil

import UnityPy

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint
from 导出全图原型 import FullMapExtractor, scene_bounds


def run(directory, revision, lod_level=1):
    output = directory / '植被细节' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '来源').mkdir()
    for name in ['输入清单.json', '来源/依赖包节点定位.json']:
        shutil.copy2(directory / name, output / name)
    sources = [p for p in json.loads((directory / '来源/全量原型来源.json').read_text('utf-8')) if p['category'] == 'tree']
    game = Path(json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text('utf-8'))['game_directory'])
    worker, journal = FullMapExtractor(output, game), Journal(directory)
    rows = []
    try:
        af = worker.load('sharedassets7.assets')
        worker.load('globalgamemanagers.assets')
        worker.load('resources.assets')
        for i, source in enumerate(sources):
            journal.update(phase='A2原生植被细节', operation=f'读取源LOD{lod_level}，保留LOD0', current=i, total=len(sources))
            pointer = UnityPy.classes.PPtr(m_FileID=0, m_PathID=source['source_id'], assetsfile=af)
            result = worker.export(source['path'], 'tree', source_ptr=pointer, lod_level=lod_level)
            file = Path(result['scene'])
            bounds, files = scene_bounds(file)
            scene = json.loads(file.read_text('utf-8'))
            triangles = sum(m['triangles'] for n in scene['nodes'] for m in n['meshes'])
            if not triangles:
                raise ValueError(f'源植被LOD{lod_level}为空：' + source['name'])
            rows.append({'asset_id': source['asset_id'], 'name': source['name'], 'source_key': source['source_key'], 'lod_level': lod_level,
                'scene': file.relative_to(directory).as_posix(), 'bounds': bounds, 'triangles': triangles, 'files': files,
                'policy': '原生较低细节网格，不由全树冠方盒替代；正常白天材质语义另核验'})
            atomic_json(output / f'来源/植被LOD{lod_level}.json', rows)
        atomic_json(output / '导出凭证.json', {'passed_source': True, 'prototypes': len(rows), 'source_program': fingerprint(Path(__file__)),
            'export_program': fingerprint(Path(__file__).with_name('导出场景.py')), 'source_manifest': fingerprint(directory / '来源/全量原型来源.json'),
            'lod_level': lod_level, 'status': f'原生LOD{lod_level}来源已导出，工程、GLB、材质及画面另验', 'human_acceptance': '待人工验收'})
        journal.close(status=f'原生植被LOD{lod_level}来源完成，继续独立工程及显示核验', prototypes=len(rows))
        print(json.dumps([(r['name'], r['triangles']) for r in rows], ensure_ascii=False))
    except BaseException as error:
        journal.close(status=f'原生植被LOD{lod_level}导出失败，保留来源', error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--lod-level', type=int, choices=[0, 1, 2], default=1)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.revision, args.lod_level)
