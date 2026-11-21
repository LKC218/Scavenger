"""把明确的三角形属性写入标准GLB；贴图保持受控相对依赖，不执行减面或量化。"""

import json
from pathlib import Path
import struct

import numpy as np


def write_glb(path, primitives, materials, translation=(0, 0, 0)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = bytearray()
    document = {'asset': {'version': '2.0', 'generator': 'Airbase源表面派生'}, 'scene': 0,
                'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0, 'translation': list(translation)}],
                'meshes': [{'primitives': []}], 'buffers': [], 'bufferViews': [], 'accessors': [],
                'materials': [], 'images': [], 'textures': [], 'samplers': []}
    material_indices, texture_indices, sampler_indices = {}, {}, {}

    def accessor(array, kind, target):
        array = np.ascontiguousarray(array)
        if not np.isfinite(array).all():
            raise ValueError('GLB属性包含无效数值')
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(array.tobytes())
        vi = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': array.nbytes, 'target': target})
        record = {'bufferView': vi, 'componentType': 5126 if array.dtype == np.dtype('<f4') else 5125,
                  'count': len(array), 'type': kind}
        if kind == 'VEC3' and target == 34962:
            record.update(min=array.min(0).tolist(), max=array.max(0).tolist())
        document['accessors'].append(record)
        return len(document['accessors']) - 1

    def material_index(key):
        if key in material_indices:
            return material_indices[key]
        source = materials[key]
        record = {'name': source['name'], 'pbrMetallicRoughness': {'baseColorFactor': list(source.get('color', [1, 1, 1, 1])),
            'metallicFactor': 0, 'roughnessFactor': source.get('roughness', .9)},
            'alphaMode': source.get('alpha_mode', 'OPAQUE'), 'doubleSided': source.get('double_sided', False),
            'extras': {'source_material': source.get('source_key', key), 'render_role': source.get('render_role'),
                       'forest_distance': source.get('forest_distance'), 'conversion': '静态底色及透明语义近似，原Shader字段另存'}}
        if record['alphaMode'] != 'BLEND':
            record['pbrMetallicRoughness']['baseColorFactor'][3] = 1
        if record['alphaMode'] == 'MASK':
            record['alphaCutoff'] = source.get('alpha_cutoff', .5)
        image = source.get('image')
        if image:
            wrap = 33071 if source.get('clamp') else 10497
            texture_key = image + '|' + str(wrap)
            if texture_key not in texture_indices:
                if wrap not in sampler_indices:
                    sampler_indices[wrap] = len(document['samplers'])
                    document['samplers'].append({'wrapS': wrap, 'wrapT': wrap, 'magFilter': 9729, 'minFilter': 9987})
                ii = len(document['images'])
                document['images'].append({'uri': image, 'name': source.get('image_name', Path(image).stem)})
                texture_indices[texture_key] = len(document['textures'])
                document['textures'].append({'source': ii, 'sampler': sampler_indices[wrap], 'name': '共享图像-' + Path(image).stem})
            texture = {'index': texture_indices[texture_key]}
            scale, offset = source.get('scale', [1, 1]), source.get('offset', [0, 0])
            if scale != [1, 1] or offset != [0, 0]:
                texture['extensions'] = {'KHR_texture_transform': {'scale': scale, 'offset': [offset[0], 1 - scale[1] - offset[1]]}}
                document['extensionsUsed'] = ['KHR_texture_transform']
            record['pbrMetallicRoughness']['baseColorTexture'] = texture
        material_indices[key] = len(document['materials'])
        document['materials'].append(record)
        return material_indices[key]

    triangles = 0
    for primitive in primitives:
        attributes = {}
        for semantic, array in primitive['attributes'].items():
            array = np.asarray(array, dtype='<f4')
            kind = {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3', 4: 'VEC4'}[array.shape[1]]
            attributes[semantic] = accessor(array, kind, 34962)
        count = len(primitive['attributes']['POSITION'])
        if not count or count % 3:
            raise ValueError('GLB三角形角点数不符')
        indices = accessor(np.arange(count, dtype='<u4'), 'SCALAR', 34963)
        document['meshes'][0]['primitives'].append({'attributes': attributes, 'indices': indices, 'mode': 4,
            'material': material_index(primitive['material']), 'extras': primitive.get('extras', {})})
        triangles += count // 3
    if not triangles:
        raise ValueError('拒绝写入空地表GLB')
    document['buffers'] = [{'byteLength': len(binary)}]
    encoded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    data = struct.pack('<4sII', b'glTF', 2, 28 + len(encoded) + len(binary))
    data += struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
    data += struct.pack('<II', len(binary), 0x004E4942) + binary
    with path.open('xb') as stream:
        stream.write(data)
    return {'triangles': triangles, 'primitives': len(primitives), 'bytes': len(data)}
