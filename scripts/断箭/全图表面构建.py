"""Blender母版与运行分块共用的源表面、材质和图集关联；不修改冻结数组。"""

import copy
import json

import bpy
import numpy as np

from 构建全图原型 import PrototypeBuilder
from 地图执行记录 import fingerprint


class SurfaceFactory:
    def __init__(self, directory, source_revision='full-r01', atlas_revision='r03', background_revision=None):
        self.directory = directory
        self.source = directory / '表面来源' / source_revision
        self.atlas_root = directory / '地表派生' / atlas_revision
        source_path = self.source / '直接表面.json'
        self.data = json.loads(source_path.read_text('utf-8'))
        self.atlas = {r['source_key']: r for r in json.loads((self.atlas_root / '地表图集.json').read_text('utf-8'))['parts']}
        self.background_root = directory / '背景派生' / background_revision if background_revision else None
        self.background = json.loads((self.background_root / '背景底色.json').read_text('utf-8')) if self.background_root else None
        self.material_data = copy.deepcopy(self.data['materials'])
        self.material_builder = PrototypeBuilder(source_path, {'materials': self.material_data}, 'none')
        self.records = []

    def arrays(self, part):
        path = self.source / part['file']
        if fingerprint(path)['sha256'] != part['identity']['sha256']:
            raise ValueError('源表面数组变化：' + part['source_key'])
        with np.load(path) as data:
            arrays = {key: data[key] for key in ['vertices', 'normals', 'uv0', 'uv1', 'colors', 'triangles', 'material_indices']}
        return arrays

    def material_keys(self, part):
        if self.background and part['source_key'] == self.background['source_key']:
            path = self.background_root / self.background['image']
            if fingerprint(path)['sha256'] != self.background['image_identity']['sha256']:
                raise ValueError('背景底色身份变化')
            key = part['materials'][0] + '-背景-' + part['source_key']
            material = copy.deepcopy(self.data['materials'][part['materials'][0]])
            material.update(name='真实背景底色-' + part['source_key'], basecolor=str(path), color=[1, 1, 1, 1], alpha_mode='OPAQUE',
                            textures={'_BaseColorMap': {'file': str(path), 'scale': {'x': 1, 'y': 1}, 'offset': {'x': 0, 'y': 0}}})
            self.material_data[key] = material
            return [key]
        if part['category'] != 'terrain':
            return part['materials']
        atlas = self.atlas[part['source_key']]
        path = self.atlas_root / atlas['file']
        if fingerprint(path)['sha256'] != atlas['identity']['sha256']:
            raise ValueError('地表图集身份变化')
        key = part['materials'][0] + '-' + part['source_key']
        material = copy.deepcopy(self.data['materials'][part['materials'][0]])
        material.update(name='源图集-' + part['source_key'], basecolor=str(path), color=[1, 1, 1, 1], alpha_mode='OPAQUE',
                        textures={'_BaseColorMap': {'file': str(path), 'scale': {'x': 1, 'y': 1}, 'offset': {'x': 0, 'y': 0}}})
        self.material_data[key] = material
        return [key]

    def create(self, part, collection):
        a = self.arrays(part)
        vertices, triangles = a['vertices'], a['triangles']
        keys = self.material_keys(part)
        uv0 = a['uv0']
        if part['category'] == 'terrain':
            bounds = self.atlas[part['source_key']]['bounds_source_xz']
            uv0 = np.column_stack([(vertices[:, 0] + 4425 - bounds[0]) / 3000,
                                   (vertices[:, 1] + 4850 - bounds[2]) / 3000]).astype(np.float32)
        mixed_background = self.background and part['source_key'] == self.background['source_key']
        if mixed_background:
            bounds = self.background['bounds_source_xz'];size = bounds[1]-bounds[0]
            uv0 = np.column_stack([(vertices[:, 0]+4425-bounds[0])/size, (vertices[:, 1]+4850-bounds[2])/size]).astype(np.float32)
        mesh = bpy.data.meshes.new(part['name'])
        mesh.from_pydata(vertices.tolist(), [], triangles.tolist())
        mesh.update()
        for key in keys:
            material = self.material_builder.material(key)
            if part['category'] == 'terrain' or mixed_background:
                for node in material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE':
                        node.extension = 'EXTEND'
            mesh.materials.append(material)
        mesh.polygons.foreach_set('material_index', a['material_indices'])
        loops = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', loops)
        channels = [('UVMap', uv0), ('源UV1', a['uv1'])]
        if mixed_background:
            channels.append(('源UV0', a['uv0']))
        for name, coordinates in channels:
            if coordinates.size:
                layer = mesh.uv_layers.new(name=name)
                layer.data.foreach_set('uv', coordinates[loops].ravel())
        mesh.uv_layers.active_index = 0
        if len(a['normals']):
            mesh.polygons.foreach_set('use_smooth', np.ones(len(triangles), dtype=bool))
            mesh.normals_split_custom_set_from_vertices(a['normals'].tolist())
        if len(a['colors']) == len(vertices):
            colors = a['colors']
            if colors.shape[1] == 3:
                colors = np.column_stack([colors, np.ones(len(vertices))])
            attribute = mesh.color_attributes.new(name='源颜色', type='FLOAT_COLOR', domain='POINT')
            attribute.data.foreach_set('color', colors.ravel())
        obj = bpy.data.objects.new(part['name'] + '-' + part['source_key'], mesh)
        collection.objects.link(obj)
        obj['source_key'], obj['category'], obj['source_triangles'] = part['source_key'], part['category'], len(triangles)
        obj['source_renderer'] = str(part['renderer_id'])
        self.records.append({'source_key': part['source_key'], 'name': obj.name, 'triangles': len(triangles),
                             'vertices': len(vertices), 'source_identity': part['identity'], 'materials': keys})
        return obj
