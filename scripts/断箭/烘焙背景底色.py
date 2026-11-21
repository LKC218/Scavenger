"""按原生背景Splat容器、真实UV及十二图层派生外围底色，不改源背景几何。"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from 导出全图原型 import FullMapExtractor
from 提取地图 import bake_terrain
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def run(args):
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    output = directory / '背景派生' / args.revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '来源').mkdir()
    for name in ['输入清单.json', '来源/依赖包节点定位.json']:
        shutil.copy2(directory / name, output / name)
    source_root = directory / '表面来源/full-r01'
    source_file = source_root / '直接表面.json'
    source = json.loads(source_file.read_text('utf-8'))
    parts = [p for p in source['parts'] if p['category'] == 'background' and any(source['materials'][key]['shader']['name'] == 'Shader Graphs/EZTerrainSimple' for key in p['materials'])]
    if len(parts) != 1:
        raise ValueError('外围混合背景来源数量变化')
    part = parts[0]
    file = source_root / part['file']
    if fingerprint(file)['sha256'] != part['identity']['sha256']:
        raise ValueError('背景源数组变化')
    controller = directory / '地表派生/r03/输入地表参数.json'
    shutil.copy2(controller, output / '输入地表参数.json')
    fields = json.loads(controller.read_text('utf-8'))['data']
    container = next(c for c in fields['SplatController']['Containers'] if c['Renderer']['m_PathID'] == part['renderer_id'])
    layer_file = directory / '地表派生/r03/图层引用.json'
    layers = json.loads(layer_file.read_text('utf-8'))
    layer_root = ROOT / layers['directory']
    for name, identity in layers['files'].items():
        if fingerprint(layer_root / name)['sha256'] != identity['sha256']:
            raise ValueError('冻结图层变化')
    with np.load(file) as arrays:
        matrix = np.asarray(part['source_world_matrix'])
        vertices = arrays['source_vertices'] @ matrix[:3, :3].T + matrix[:3, 3]
        uv, triangles = arrays['source_uv0'], arrays['source_triangles']
    minimum = np.floor(vertices[:, [0, 2]].min(0)/1000)*1000
    maximum = vertices[:, [0, 2]].max(0)
    size = int(np.ceil(np.max(maximum-minimum)/1000)*1000)
    bounds = [float(minimum[0]), float(minimum[0]+size), float(minimum[1]), float(minimum[1]+size)]
    game = Path(json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text('utf-8'))['game_directory'])
    worker, journal = FullMapExtractor(output, game), Journal(directory)
    try:
        worker.load('level7')
        journal.update(phase='A2外围背景底色', operation='原生Splat权重与真实UV混合', resolution=args.dimension, bounds=bounds)
        row = {'id': int(part['source_key'].split(':')[1]), 'components': [{'type': 'MeshRenderer', 'id': part['renderer_id']}]}
        image = Path(bake_terrain(worker, row, vertices, uv, triangles, bounds, output, size,
                                 dimension=args.dimension, controller_path=output / '输入地表参数.json',
                                 layers_directory=layer_root, preserve_source_holes=True))
        rgb = output / '背景底色.png'
        with Image.open(image) as pixels:
            pixels.convert('RGB').save(rgb)
        weights = {key: fingerprint(Path(worker.texture(container[key], worker.files['level7']))) for key in ['Splat0', 'Splat1', 'Splat2']}
        atomic_json(output / '背景底色.json', {'schema': 'airbase-background-albedo-v1', 'source_key': part['source_key'],
            'renderer': part['renderer_id'], 'material_keys': part['materials'], 'bounds_source_xz': bounds,
            'image': rgb.name, 'image_identity': fingerprint(rgb), 'source_rgba': fingerprint(image),
            'source_surface': fingerprint(source_file), 'source_geometry': part['identity'], 'source_controller': fingerprint(controller),
            'source_layers': fingerprint(layer_file), 'source_weights': weights, 'container': container,
            'source_program': fingerprint(Path(__file__)), 'baker_program': fingerprint(Path(__file__).with_name('提取地图.py')),
            'policy': '背景真实十二层底色与原生BigFactor；源几何及空洞不变，RGBA覆盖凭证保留；运行RGB仅作为同一源网格的颜色，不复刻完整高度混合、法线及光照',
            'status': '背景底色已派生，工程、GLB及网页画面另验', 'human_acceptance': '待人工验收'})
        journal.close(status='外围背景底色完成，继续独立工程和网页核验', image=fingerprint(rgb))
        print(json.dumps({'bounds': bounds, 'dimension': args.dimension, 'image': fingerprint(rgb)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='外围背景底色失败，保留候选', error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--dimension', type=int, choices=[1024, 2048, 4096], default=2048)
    args = parser.parse_args()
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(args)
