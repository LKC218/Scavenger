"""引用不可变编码媒体和碰撞分页构成运行候选，不改写已核验媒体及原始清单。"""

import argparse
import copy
import json
from pathlib import Path

from 地图执行记录 import ROOT, atomic_json, fingerprint


def run(directory, media_revision, physics_revision, far_revision, revision, impostor_revision=None, impostor_calibration=None, background_revision=None, water_revision=None):
    if background_revision and not water_revision:
        raise ValueError('替换背景时必须显式绑定两处独立水面，防止遗漏全局渲染层')
    media = directory / '全图运行' / media_revision
    physics = directory / '碰撞分页' / physics_revision
    output = directory / '运行接入' / revision
    output.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((media / '压缩核验.json').read_text('utf-8'))
    if receipt['status'] != 'geometry_passed_pending_visual_runtime':
        raise ValueError('媒体无完整几何回读凭证')
    collision_receipt = physics / '独立核验/r01/核验凭证.json'
    if not json.loads(collision_receipt.read_text('utf-8'))['passed']:
        raise ValueError('碰撞页独立回读未通过')
    manifest_file = media / '地图运行.json'
    if fingerprint(manifest_file)['sha256'] != receipt['manifest']['sha256']:
        raise ValueError('编码清单摘要变化')
    manifest = copy.deepcopy(json.loads(manifest_file.read_text('utf-8')))
    collision = json.loads((physics / '碰撞索引.json').read_text('utf-8'))
    sources = {p['asset_id']: p for p in json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))}
    media_prefix = media.relative_to(directory).as_posix() + '/'
    collision_prefix = physics.relative_to(directory).as_posix() + '/'
    files = {media_prefix + name: identity for name, identity in manifest['files'].items()}
    for chunk in collision['chunks']:
        files[collision_prefix + chunk['file']] = chunk['identity']
    far = directory / '远景运行' / far_revision
    far_receipt = json.loads((far / '远景核验.json').read_text('utf-8'))
    if far_receipt['status'] != 'geometry_passed_pending_projection_visual':
        raise ValueError('远景几何回读未完成')
    far_prefix = far.relative_to(directory).as_posix() + '/'
    files.update({far_prefix + name: identity for name, identity in far_receipt['assets'].items()})
    for key in ['overview', 'background']:
        manifest[key] = media_prefix + manifest[key]
    if background_revision:
        background = directory / '背景运行' / background_revision
        evidence = background / '背景核验.json'
        background_receipt = json.loads(evidence.read_text('utf-8'))
        if background_receipt['status'] != 'geometry_passed_pending_projection_visual':
            raise ValueError('背景编码未完成独立回读')
        prefix = background.relative_to(directory).as_posix()+'/'
        files.update({prefix+name: identity for name, identity in background_receipt['assets'].items()})
        manifest['background'] = prefix+'背景.glb'
        manifest['background_layering'] = {'exclusive_source_background': True, 'source': fingerprint(evidence),
            'policy': '真实混合底色；全局背景就绪后停绘区块中同源背景，避免重合面竞争，源网格和模拟页不变'}
    if water_revision:
        water=directory/'水面运行'/water_revision
        evidence=water/'水面核验.json';record=json.loads(evidence.read_text('utf-8'))
        if not record['passed'] or record['triangles']!=10417 or len(set(record['source_keys']))!=2:
            raise ValueError('独立水面来源或回读不完整')
        prefix=water.relative_to(directory).as_posix()+'/'
        for name,identity in record['assets'].items():
            actual=fingerprint(water/name)
            if any(actual[key]!=identity[key] for key in ['bytes','sha256']):raise ValueError('水面文件身份变化')
            files[prefix+name]=identity
        manifest['water']=prefix+'水面.glb'
        manifest['water_layer']={'source':fingerprint(evidence),'source_keys':record['source_keys'],'triangles':10417,'policy':'原水面独立全局渲染，物理及通行页不变'}
    physics_chunks = {c['id']: c for c in collision['chunks']}
    for chunk in manifest['chunks']:
        for key in ['file', 'geometry']:
            if chunk.get(key):
                chunk[key] = media_prefix + chunk[key]
        row = physics_chunks[chunk['id']]
        chunk.update(collision=collision_prefix + row['file'], collision_identity=row['identity'], collision_count=row['count'],
                     collision_decoded_bytes=max(row['decoded_budget_bytes'], row['identity']['bytes']+row['count']*1536+4096))
    for page in manifest['pages']:
        page['file'] = media_prefix + page['file']
    trunks = {}
    for pi, prototype in enumerate(manifest['prototypes']):
        if prototype['file']:
            prototype['file'] = media_prefix + prototype['file']
        source = sources[prototype['id']]
        prototype['name'] = source['name']
        if prototype['category'] == 'tree':
            prototype['vegetation_kind'] = 'shrub' if source['name'] in ['Bush_4', 'Bush_5'] else 'tree'
            trunk = next((shape for shape in collision['templates'][pi] if shape['kind'] == 3), None)
            if trunk:
                trunks[pi] = {'matrix': [trunk['matrix'][r][c] for c in range(4) for r in range(4)], 'params': trunk['params']}
    manifest['vegetation']['trunks'] = trunks
    plant = directory / '植被运行/r01'
    plant_receipt = json.loads((plant / '远景核验.json').read_text('utf-8'))
    if plant_receipt['status'] != 'geometry_passed_pending_projection_visual':
        raise ValueError('原生植被细节层编码未完成')
    plant_prefix = plant.relative_to(directory).as_posix()+'/'
    files.update({plant_prefix+name: identity for name, identity in plant_receipt['assets'].items()})
    for row in plant_receipt['files']:
        manifest['prototypes'][row['prototype']]['vegetation_file'] = plant_prefix+row['file']
    manifest['vegetation']['native_lod'] = True
    manifest['vegetation']['native_lod_source'] = fingerprint(plant / '远景核验.json')
    if impostor_revision:
        impostor = directory / '植被替身' / impostor_revision
        descriptor = json.loads((impostor / '替身编码.json').read_text('utf-8'))
        prefix = impostor.relative_to(directory).as_posix()+'/'
        files.update({prefix+name: identity for name, identity in descriptor['assets'].items()})
        for row in descriptor['rows']:
            prototype = manifest['prototypes'][row['prototype']]
            if prototype['id'] != row['id'] or row['prototype'] < 0:
                raise ValueError('原生替身来源身份不符')
            # 两类灌木的原图集明显比源LOD1更实心，保留已审阅的真实LOD1。
            if prototype.get('vegetation_kind') == 'shrub':
                continue
            prototype['native_lod1_file'] = prototype['vegetation_file']
            prototype['vegetation_file'] = prefix+row['file']
            prototype['impostor'] = {key: row[key] for key in ['frames', 'size', 'offset', 'alpha_cutoff']}
        manifest['vegetation']['native_impostor'] = True
        manifest['vegetation']['impostor_source'] = fingerprint(impostor / '替身编码.json')
        if impostor_calibration:
            calibration_file = (directory / impostor_calibration).resolve()
            calibration_file.relative_to(directory)
            calibration = json.loads(calibration_file.read_text('utf-8'))
            expected = {pi for pi, prototype in enumerate(manifest['prototypes']) if prototype.get('impostor')}
            indices = [row['index'] for row in calibration['assets']]
            if calibration['errors'] or len(set(indices)) != len(indices) or not expected.issubset(indices):
                raise ValueError('植被替身色阶凭证不完整')
            for row in calibration['assets']:
                prototype = manifest['prototypes'][row['index']]
                scale = row['calibration']['color_scale']
                if prototype['id'] != row['id'] or len(scale) != 3 or not all(0 < value <= 32 for value in scale):
                    raise ValueError('植被替身色阶来源身份或数值无效')
                if prototype.get('impostor'):
                    prototype['impostor']['color_scale'] = scale
            manifest['vegetation']['impostor_calibration'] = fingerprint(calibration_file)
    for row in far_receipt['files']:
        if 'prototype' in row:
            key = 'middle_file' if row.get('level') == 'middle' else 'far_file'
            manifest['prototypes'][row['prototype']][key] = far_prefix + row['file']
    manifest['far'] = {'surface': far_prefix+'道面与直接表面.glb', 'instances': far_prefix+'固定布景.bin', 'source': fingerprint(far / '远景核验.json')}
    if far_receipt.get('overview_colors'):
        for pi, color in far_receipt['overview_colors'].items():
            manifest['prototypes'][int(pi)]['overview_color'] = color
        manifest['far']['aggregate_max_diameter_pixels'] = 1.5
        manifest['far']['aggregate_policy'] = '实际镜头投影完整源世界包围盒八角，屏幕包围矩形对角线不超过1.5个内部像素时，用同世界包围盒及真实源底色聚合；记录数量与最大直径，近处继续真实几何'
    manifest['camera'] = {'max_height_m': 9000, 'middle_height_m': 100, 'far_height_m': 800,
                          'lod_hysteresis_m': 15, 'reference': 'Ebbtide公开说明的连续战术与总览交互；具体高度和LOD是本项目配置，非其内部参数'}
    coarse = json.loads((directory / '粗层/r01/粗层索引.json').read_text('utf-8'))
    manifest['coarse_height'] = {'file': '粗层/r01/'+coarse['file'], 'source': fingerprint(directory / '粗层/r01/粗层索引.json')}
    files[manifest['coarse_height']['file']] = coarse['identity']
    graph = directory / '通行粗层/r01/分区通行.json'
    manifest['navigation_graph'] = {'file': graph.relative_to(directory).as_posix(), 'identity': fingerprint(graph)}
    files[manifest['navigation_graph']['file']] = fingerprint(graph)
    manifest['scope_adjustment'] = fingerprint(directory / '范围冻结/运行调整-r02.json')
    altitude = json.loads((directory / '容量预检/安全空域-r01.json').read_text('utf-8'))
    manifest['safeAltitude'] = altitude['fixed_wing_altitude']
    manifest['helicopterAltitude'] = altitude['helicopter_altitude']
    manifest['flight_envelope'] = {'buffer_m': 1300, 'source': fingerprint(directory / '容量预检/安全空域-r01.json')}
    packets = json.loads((directory / '模拟分区/r01/模拟分区索引.json').read_text('utf-8'))
    manifest['simulation_packages'] = packets['packages']
    for chunk in manifest['chunks']:
        chunk['simulation_package'] = packets['cells'][chunk['id']]
    for packet in packets['packages'].values():
        files[packet['file']] = packet['identity']
    manifest.update(package_base='../..', files=files, source_media=fingerprint(manifest_file), source_collision=fingerprint(physics / '碰撞索引.json'),
                    collision_verified=fingerprint(collision_receipt), physics_enabled=True)
    for name, identity in files.items():
        file = directory / name
        if fingerprint(file)['sha256'] != identity['sha256']:
            raise ValueError('运行接入依赖变化：' + name)
    atomic_json(output / '地图运行.json', manifest)
    atomic_json(output / '组装凭证.json', {'schema': 'airbase-runtime-composition-v1', 'files': len(files),
        'manifest': fingerprint(output / '地图运行.json'), 'source_media': fingerprint(media / '压缩核验.json'),
        'source_collision': fingerprint(collision_receipt), 'source_program': fingerprint(Path(__file__)),
        'status': '运行依赖已组成，待实际浏览器与完整关口', 'human_acceptance': '待人工验收'})
    print(json.dumps({'files': len(files), 'manifest': fingerprint(output / '地图运行.json')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--media-revision', required=True)
    parser.add_argument('--physics-revision', required=True)
    parser.add_argument('--far-revision', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--impostor-revision')
    parser.add_argument('--impostor-calibration')
    parser.add_argument('--background-revision')
    parser.add_argument('--water-revision')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.media_revision, args.physics_revision, args.far_revision, args.revision, *([args.impostor_revision] if args.impostor_revision else []), *([args.background_revision] if args.background_revision else []), *([args.water_revision] if args.water_revision else [])]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    if args.impostor_calibration and not args.impostor_revision:
        raise ValueError('色阶凭证必须与明确的替身修订共同使用')
    run(directory, args.media_revision, args.physics_revision, args.far_revision, args.revision, args.impostor_revision, args.impostor_calibration, args.background_revision,args.water_revision)
