"""按冻结来源读取场景直接网格和材质，保留原始属性与世界坐标派生；不替代分块或完整验收。"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from UnityPy.helpers.MeshHelper import MeshHandler

from 导出全图原型 import FullMapExtractor
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def category(name):
    if name.startswith('water_'):
        return 'water'
    if name.startswith('tile_mesh_high_'):
        return 'terrain'
    if name.startswith('big_terrain'):
        return 'background'
    if name.startswith(('road_geo_', 'road_ellement_', 'airport_')):
        return 'road'
    return 'direct'


def extract(directory, revision, selected_category):
    output = directory / '表面来源' / revision
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    worker = FullMapExtractor(directory, Path(json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text('utf-8'))['game_directory']))
    try:
        scope_path = directory / '范围冻结/V001-r02/范围与条目.json'
        scope = json.loads(scope_path.read_text('utf-8'))
        if selected_category == 'all':
            if not (directory / '阶段验收/A1-r01.json').is_file():
                raise ValueError('全量表面制作须先完成A1样本阶段封存')
            journal.state['gates']['A2'] = '进行中'
        source_objects = json.loads((directory / '来源/全图来源审计.json').read_text('utf-8'))['objects']
        objects = {int(row['source_key'].split(':')[1]): row for row in source_objects}
        required = [row for row in scope['direct_meshes'] if row['required'] and
                    (selected_category == 'all' or category(row['name']) == selected_category)]
        level = worker.load('level7')
        worker.load('sharedassets7.assets')
        worker.load('globalgamemanagers.assets')
        worker.load('resources.assets')
        parts, materials = [], {}
        center = np.asarray(scope['source_origin'], dtype=float)
        conversion = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float)
        for index, row in enumerate(required):
            journal.update(phase='A1特殊表面样本' if selected_category == 'water' else 'A2全图表面来源',
                           operation='按冻结直接网格提取', current=index + 1, total=len(required), source_object=row['gameobject'])
            source = objects[row['gameobject']]
            components = source['components']
            mesh_filter = next(c for c in components if c['type'] == 'MeshFilter')
            renderer = next(c for c in components if c['type'] == 'MeshRenderer')
            mesh_object = worker.deref(mesh_filter['data']['m_Mesh'], level)
            mesh = mesh_object.read()
            if mesh.m_StreamData and mesh.m_StreamData.path:
                worker.load(Path(mesh.m_StreamData.path).name)
            handler = MeshHandler(mesh)
            handler.process()
            vertices = np.asarray(handler.m_Vertices, dtype=np.float32)[:, :3]
            normals = np.asarray(handler.m_Normals, dtype=np.float32)[:, :3] if handler.m_Normals else np.zeros_like(vertices)
            uv0 = np.asarray(handler.m_UV0, dtype=np.float32)[:, :2] if handler.m_UV0 else np.zeros((len(vertices), 2), dtype=np.float32)
            uv1 = np.asarray(handler.m_UV1, dtype=np.float32)[:, :2] if handler.m_UV1 else np.zeros((len(vertices), 2), dtype=np.float32)
            colors = np.asarray(handler.m_Colors, dtype=np.float32) if handler.m_Colors else np.empty((0, 4), dtype=np.float32)
            triangles, slots = [], []
            for slot, faces in enumerate(handler.get_triangles()):
                faces = np.asarray(faces, dtype=np.int32) + (getattr(mesh.m_SubMeshes[slot], 'baseVertex', 0) or 0)
                triangles.extend(faces)
                slots.extend([slot] * len(faces))
            triangles = np.asarray(triangles, dtype=np.int32)
            world = np.asarray(source['world_matrix'], dtype=float)
            world_vertices = vertices @ world[:3, :3].T + world[:3, 3]
            world_normals = normals @ np.linalg.inv(world[:3, :3])
            lengths = np.linalg.norm(world_normals, axis=1, keepdims=True)
            world_normals /= np.maximum(lengths, 1e-30)
            if not np.isfinite(world_vertices).all() or not np.isfinite(normals).all():
                raise ValueError('源表面位置或法线无效')
            if triangles.min() < 0 or triangles.max() >= len(vertices):
                raise ValueError('源表面索引越界')
            keys, warnings = [], []
            for ptr in renderer['data']['m_Materials']:
                key, material = worker.material(ptr, level, warnings)
                shader = worker.deref(material['raw']['m_Shader'], level if material['source_node'] == level.name else
                                      worker.env.cabs[material['source_node'].lower()])
                material = {**material, 'shader': {'node': shader.assets_file.name, 'id': shader.path_id,
                    'name': shader.read_typetree()['m_ParsedForm']['m_Name'],
                    'raw_sha256': hashlib.sha256(shader.get_raw_data()).hexdigest()}}
                materials[key] = material
                keys.append(key)
            if len(keys) <= int(max(slots)):
                raise ValueError('源表面材质槽不完整')
            file = output / f'直接网格-{row["gameobject"]}.npz'
            np.savez_compressed(file, source_vertices=vertices, source_normals=normals, source_uv0=uv0, source_uv1=uv1,
                source_colors=colors, source_triangles=triangles, vertices=((world_vertices - center) @ conversion).astype(np.float32),
                normals=(world_normals @ conversion).astype(np.float32), uv0=uv0, uv1=uv1, colors=colors,
                triangles=triangles[:, [0, 2, 1]], material_indices=np.asarray(slots, dtype=np.int32))
            parts.append({'source_key': source['source_key'], 'name': row['name'], 'category': category(row['name']),
                'mesh_source': {'node': mesh_object.assets_file.name, 'id': mesh_object.path_id,
                                'raw_sha256': hashlib.sha256(mesh_object.get_raw_data()).hexdigest()},
                'renderer_id': renderer['id'], 'source_world_matrix': world.tolist(), 'source_world_bounds': row['world_bounds'],
                'actual_world_bounds': [world_vertices.min(0).tolist(), world_vertices.max(0).tolist()],
                'materials': keys, 'vertices': len(vertices), 'triangles': len(triangles), 'file': file.name,
                'identity': fingerprint(file), 'nonfinite_uv_corners': int((~np.isfinite(uv0).all(1)).sum()), 'warnings': warnings})
            atomic_json(output / '直接表面.json', {'parts': parts, 'materials': materials, 'status': '来源提取中'})
        result = {'schema': 1, 'status': '直接表面来源属性已冻结，待材质派生、分块和独立回读',
            'scope': fingerprint(scope_path), 'source_program': fingerprint(Path(__file__)), 'selected_category': selected_category,
            'conversion': scope['conversion'], 'parts': parts, 'materials': materials,
            'source_geometry_unclipped': True, 'note': '保留完整源形体与原始属性；运行范围裁切另作派生，不能作为全图完成凭证'}
        atomic_json(output / '直接表面.json', result)
        journal.close(status='直接表面来源提取完成，继续样本及全图制作', parts=len(parts))
        print(json.dumps({'parts': len(parts), 'triangles': sum(p['triangles'] for p in parts), 'materials': len(materials)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='直接表面提取失败，保留恢复点', error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--category', choices=['all', 'water'], default='all')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    extract(directory, args.revision, args.category)
