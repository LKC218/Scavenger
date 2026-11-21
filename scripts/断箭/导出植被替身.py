"""保留原生植被替身图集及参数；不把专用Shader网格误作普通白色GLB。"""

import argparse
import json
from pathlib import Path
import shutil

from PIL import Image

from 导出全图原型 import FullMapExtractor
from 地图执行记录 import ROOT, atomic_json, fingerprint


def run(directory, revision):
    output = directory / '植被替身' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '来源').mkdir()
    for name in ['输入清单.json', '来源/依赖包节点定位.json']:
        shutil.copy2(directory / name, output / name)
    game = Path(json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text('utf-8'))['game_directory'])
    worker = FullMapExtractor(output, game)
    sources = json.loads((directory / '植被细节/r02/来源/植被LOD2.json').read_text('utf-8'))
    rows = []
    try:
        worker.load('sharedassets7.assets')
        for source in sources:
            scene = json.loads((directory / source['scene']).read_text('utf-8'))
            if len(scene['materials']) != 1:
                raise ValueError('替身来源材质数量变化')
            material = next(iter(scene['materials'].values()))
            af = worker.load(material['source_node'])
            raw = material['raw']
            textures = {}
            for name, env in raw['m_SavedProperties']['m_TexEnvs']:
                if name not in ['BaseAlpha', 'NormalDepth']:
                    continue
                file = Path(worker.texture(env['m_Texture'], af))
                textures[name] = {'file': file.relative_to(output).as_posix(), **fingerprint(file)}
            if 'BaseAlpha' not in textures:
                raise ValueError('原生替身缺失必要图集')
            floats = dict(raw['m_SavedProperties']['m_Floats'])
            vectors = dict(raw['m_SavedProperties']['m_Colors'])
            with Image.open(output / textures['BaseAlpha']['file']) as image:
                size = list(image.size)
                preview = image.copy()
                preview.thumbnail((1200, 1200))
                preview.save(output / (source['name']+'-图集.png'))
            rows.append({'id': source['asset_id'], 'name': source['name'], 'textures': textures,
                         'frames': floats['_ImpostorFrames'], 'size': floats['_ImpostorSize'],
                         'offset': [vectors['_ImpostorOffset'][k] for k in 'rgb'],
                         'alpha_cutoff': floats['_Alpha_Clip_Treshold'], 'dimensions': size,
                         'source_material': raw, 'source_scene': fingerprint(directory / source['scene'])})
            atomic_json(output / '替身来源.json', {'rows': rows, 'source_program': fingerprint(Path(__file__)),
                         'status': '仅原件与参数，网页替身实现和方向对应尚未验收'})
        print(json.dumps([(r['name'], r['dimensions'], r['frames']) for r in rows], ensure_ascii=False))
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


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
