"""按源Box/Capsule及导航形状生成区域碰撞；树木只派生真实下部树干，灌木不作整冠方盒。"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import struct

import numpy as np

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint
from 分页全图实例 import RECORD

C = np.diag([1., 1., -1., 1.])
PHYSICS = np.dtype([('group', '<u4'), ('index', '<u4'), ('prototype', '<u4'), ('component', '<u4'), ('flags', '<u4'),
                    ('matrix', '<f4', (16,)), ('params', '<f4', (4,)), ('bounds', '<f4', (6,))])


def vector(value):
    return np.array([value[k] for k in 'xyz'], dtype=float)


def local_template(component, navigation=False):
    fields = component['fields']
    frame = np.asarray(component['matrix_unity'], dtype=float)
    center = np.eye(4)
    center[:3, 3] = vector(fields['m_Center'])
    frame = C @ frame @ center @ C
    box = fields['m_Shape'] == 1 if navigation else component['type'] == 'BoxCollider'
    if box:
        params = vector(fields['m_Extents']) if navigation else vector(fields['m_Size']) / 2
        kind = 1
    else:
        if navigation:
            extents = vector(fields['m_Extents'])
            radius, height = float(extents[0]), float(extents[1]*2)
        else:
            radius, height = fields['m_Radius'], fields['m_Height']
            if fields['m_Direction'] != 1:
                raise ValueError('发现非Y轴源胶囊，须补专门变换')
        params, kind = [radius, max(0., height/2-radius), 0.], 2
    return {'kind': kind, 'matrix': frame.tolist(), 'params': list(params), 'movement': True, 'ballistics': not navigation,
            'native_capsule_scale': kind == 2, 'source_node': component['source_node'], 'source_id': component['source_id'],
            'source_sha256': component['raw_sha256'], 'policy': '源导航占位，仅通行' if navigation else '启用且非触发的源Collider'}


def trunk_template(source):
    if source['name'] in ['Bush_4', 'Bush_5']:
        return [], {'mode': 'visual_only', 'reason': '灌木无源Collider或导航形状，不将树冠变为实心方盒'}
    scene_file = Path(source['exported']['scene'])
    scene = json.loads(scene_file.read_text('utf-8'))
    nodes = {n['id']: n for n in scene['nodes']}

    def world(node):
        m = np.asarray(node['matrix'])
        return world(nodes[node['parent']]) @ m if node['parent'] is not None else m

    triangles = []
    for node in scene['nodes']:
        frame = world(node)
        for mesh in node['meshes']:
            with np.load(scene_file.parent / mesh['file']) as data:
                v = data['vertices'] @ frame[:3, :3].T + frame[:3, 3]
                triangles.append(v[:, [0, 2, 1]][data['triangles']])
    triangles = np.concatenate(triangles)
    low = triangles[:, :, 1].min()
    top = min(low+6, low+(triangles[:, :, 1].max()-low)*.35)
    slices = []
    for y in np.linspace(low+.35, top, 8):
        selected = triangles[(triangles[:, :, 1].min(1) < y) & (triangles[:, :, 1].max(1) > y)]
        segments = []
        for triangle in selected:
            points = []
            for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
                if (a[1] < y) != (b[1] < y):
                    p = a+(b-a)*((y-a[1])/(b[1]-a[1]))
                    points.append(p[[0, 2]])
            if len(points) == 2:
                segments.append(points)
        graph, coordinates = defaultdict(set), {}
        for a, b in segments:
            ka, kb = tuple(np.round(a, 4)), tuple(np.round(b, 4))
            graph[ka].add(kb); graph[kb].add(ka)
            coordinates[ka], coordinates[kb] = a, b
        groups, visited = [], set()
        for first in graph:
            if first in visited:
                continue
            pending, component = [first], []
            while pending:
                key = pending.pop()
                if key in visited:
                    continue
                visited.add(key); component.append(coordinates[key]); pending.extend(graph[key]-visited)
            if len(component) >= 3:
                points = np.asarray(component)
                center = (points.min(0)+points.max(0))/2
                radius = float(np.linalg.norm(points-center, axis=1).max())
                groups.append((float(np.linalg.norm(center)), center, radius))
        if not groups:
            break
        _, center, radius = min(groups, key=lambda x: x[0])
        if np.linalg.norm(center) > 1.5 or radius > 2.5 or radius < .01:
            break
        slices.append({'height': float(y), 'center': center.tolist(), 'radius': radius})
    if len(slices) < 2:
        raise ValueError('树干截面依据不足：' + source['name'])
    center = np.mean([x['center'] for x in slices], axis=0)
    radius = max(x['radius']+np.linalg.norm(np.asarray(x['center'])-center) for x in slices)
    height = slices[-1]['height']-low
    frame = np.eye(4)
    frame[:3, 3] = [center[0], low+height/2, -center[1]]
    template = {'kind': 3, 'matrix': frame.tolist(), 'params': [float(radius), height/2, 0.], 'movement': True, 'ballistics': True,
                'policy': '依据下部源网格截面派生有限树干圆柱；树冠不作阻挡', 'source_key': source['source_key']}
    return [template], {'mode': 'derived_trunk', 'slices': slices, 'radius': float(radius), 'height': height, 'source_scene': fingerprint(scene_file)}


def run(directory, revision):
    output = directory / '碰撞分页' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '区域').mkdir()
    index_file = directory / '实例分页/r01/实例索引.json'
    index = json.loads(index_file.read_text('utf-8'))
    source_file = directory / '来源/原型物理-r01.json'
    physics = json.loads(source_file.read_text('utf-8'))['assets']
    sources = {p['asset_id']: p for p in json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))}
    journal = Journal(directory)
    templates, policies = [], []
    try:
        for prototype in index['prototypes']:
            source = physics[prototype['id']]
            shapes = [local_template(c) for c in source['colliders'] if c['active'] and c['enabled'] and not c['trigger']]
            shapes += [local_template(c, True) for c in source['nav_obstacles'] if c['active'] and c['enabled']]
            policy = {'mode': 'source_shapes' if shapes else 'visual_only'}
            if prototype['category'] == 'tree' and not shapes:
                shapes, policy = trunk_template(sources[prototype['id']])
            templates.append(shapes);policies.append({'asset_id': prototype['id'], 'source_key': prototype['source_key'], **policy})
    except BaseException as error:
        journal.close(status='碰撞模板来源核验失败', error=repr(error))
        raise
    rows = [[] for _ in range(5184)]
    totals, source_totals, instance_counts, exclusions = defaultdict(int), defaultdict(int), defaultdict(int), []
    try:
        for n, page in enumerate(index['pages']):
            if n % 32 == 0:
                journal.update(phase='A2碰撞分页', operation='按源Collider和下部树干派生区域形状', current=n, total=len(index['pages']))
            file = index_file.parent / page['file']
            if fingerprint(file)['sha256'] != page['identity']['sha256']:
                raise ValueError('来源实例页变化')
            records = np.fromfile(file, dtype=RECORD, offset=16)
            for pi in np.unique(records['prototype']):
                selected = records[records['prototype'] == pi]
                base = selected['matrix'].reshape(-1, 4, 4).transpose(0, 2, 1).astype(float)
                for component, template in enumerate(templates[pi]):
                    matrices = base @ np.asarray(template['matrix'])[None]
                    params = np.tile([*template['params'], 0.], (len(selected), 1))
                    kind = template['kind']
                    if kind == 2:
                        scale = np.linalg.norm(matrices[:, :3, :3], axis=1)
                        radius = params[:, 0]*scale[:, [0, 2]].max(1)
                        height = (params[:, 1]+params[:, 0])*2*scale[:, 1]
                        matrices[:, :3, :3] /= scale[:, None, :]
                        params[:, 0], params[:, 1] = radius, np.maximum(0, height/2-radius)
                    half = params[:, :3].copy() if kind == 1 else np.column_stack([params[:, 0], params[:, 1]+(params[:, 0] if kind == 2 else 0), params[:, 0]])
                    extent = np.einsum('nij,nj->ni', np.abs(matrices[:, :3, :3]), half)
                    low, high = matrices[:, :3, 3]-extent, matrices[:, :3, 3]+extent
                    result = np.zeros(len(selected), dtype=PHYSICS)
                    result['group'], result['index'], result['prototype'], result['component'] = selected['group'], selected['source_index'], pi, component
                    result['flags'] = kind | (8 if template['movement'] else 0) | (16 if template['ballistics'] else 0)
                    result['matrix'] = matrices.transpose(0, 2, 1).reshape(-1, 16)
                    result['params'], result['bounds'] = params, np.column_stack([low, high])
                    source_totals[str(kind)] += len(selected)
                    b = index['bounds']
                    intersects = (low[:, 0] < b['maxX']) & (high[:, 0] > b['minX']) & (low[:, 2] < b['maxZ']) & (high[:, 2] > b['minZ'])
                    for excluded in result[~intersects]:
                        exclusions.append({'group': int(excluded['group']), 'source_index': int(excluded['index']), 'component': component, 'reason': '实例已保留，但此物理形状位于有效XZ范围外', 'bounds': excluded['bounds'].tolist()})
                    result = result[intersects]
                    totals[str(kind)] += len(result)
                    instance_counts[index['prototypes'][int(pi)]['id']] += len(result)
                    for shape in result:
                        x0, z0 = np.floor((shape['bounds'][[0, 2]]-[index['bounds']['minX'], index['bounds']['minZ']])/125).astype(int)
                        x1, z1 = np.floor((shape['bounds'][[3, 5]]-[index['bounds']['minX'], index['bounds']['minZ']])/125).astype(int)
                        for z in range(max(0, z0), min(71, z1)+1):
                            for x in range(max(0, x0), min(71, x1)+1):
                                rows[z*72+x].append(shape.copy())
        chunks = []
        for i, values in enumerate(rows):
            file = output / '区域' / f'{i%72:02}-{i//72:02}.bin'
            with file.open('xb') as stream:
                stream.write(struct.pack('<4sIII', b'BACO', 1, len(values), PHYSICS.itemsize))
                stream.write(np.asarray(values, dtype=PHYSICS).tobytes())
            chunks.append({'id': file.stem, 'file': file.relative_to(output).as_posix(), 'count': len(values), 'identity': fingerprint(file),
                           'decoded_budget_bytes': file.stat().st_size+len(values)*640})
        result = {'schema': 'airbase-collision-pages-v1', 'status': '来源及树干派生完成，待独立运行核验', 'source': fingerprint(source_file), 'bounds': index['bounds'],
            'instance_source': fingerprint(index_file), 'templates': templates, 'policies': policies, 'unique_shapes_by_kind': dict(totals),
            'source_shapes_by_kind': dict(source_totals), 'outside_shape_exclusions': exclusions,
            'instance_shapes': dict(instance_counts), 'chunks': chunks, 'layout': {'magic': 'BACO', 'version': 1, 'header': 16, 'stride': PHYSICS.itemsize,
                'fields': PHYSICS.descr, 'flags': {'box': 1, 'capsule': 2, 'trunk_cylinder': 3, 'movement': 8, 'ballistics': 16}},
            'source_program': fingerprint(Path(__file__)), 'human_acceptance': '待人工验收'}
        atomic_json(output / '碰撞索引.json', result)
        journal.close(status='碰撞来源分页候选完成，待运行核验', unique_shapes=sum(totals.values()))
        print(json.dumps({'shapes': dict(totals), 'chunks': len(chunks), 'bytes': sum(c['identity']['bytes'] for c in chunks)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='碰撞分页失败，保留恢复点', error=repr(error))
        raise


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
