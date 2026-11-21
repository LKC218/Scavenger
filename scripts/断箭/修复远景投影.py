"""依据真实投影失败项回退确切近景字节；通过项不重编码，另存源图平均色。"""
import argparse
import io
import json
from pathlib import Path
import shutil
import struct
from urllib.parse import unquote

from PIL import Image, ImageStat
from 地图执行记录 import ROOT, atomic_json, fingerprint


def glb(path):
    data = path.read_bytes()
    if data[:4] != b'glTF' or struct.unpack_from('<I', data, 8)[0] != len(data):
        raise ValueError('GLB身份或长度错误')
    size, kind = struct.unpack_from('<II', data, 12)
    if kind != 0x4e4f534a:
        raise ValueError('GLB无JSON首块')
    document = json.loads(data[20:20+size])
    binary = data[28+size:] if len(data) > 20+size else b''
    return document, binary


def source_color(path):
    document, binary = glb(path)
    total, rgb = 0, [0., 0., 0.]
    for mesh in document.get('meshes', []):
        for primitive in mesh['primitives']:
            count = document['accessors'][primitive['indices']]['count']
            material = document.get('materials', [{}])[primitive.get('material', 0)]
            pbr = material.get('pbrMetallicRoughness', {})
            color = pbr.get('baseColorFactor', [1, 1, 1, 1])[:3]
            texture = pbr.get('baseColorTexture')
            if texture:
                image = document['images'][document['textures'][texture['index']]['source']]
                if 'bufferView' in image:
                    view = document['bufferViews'][image['bufferView']]
                    raw = binary[view.get('byteOffset', 0):view.get('byteOffset', 0)+view['byteLength']]
                    with Image.open(io.BytesIO(raw)) as pixels:
                        pixels = pixels.convert('RGBA');pixels.thumbnail((32, 32))
                        mask = pixels.getchannel('A').point(lambda a: 255 if a >= material.get('alphaCutoff', .5)*255 else 0)
                        average = ImageStat.Stat(pixels.convert('RGB'), mask).mean
                        color = [value*sample/255 for value, sample in zip(color, average)]
            for channel in range(3):
                rgb[channel] += color[channel]*count
            total += count
    return [round(value/max(1, total), 6) for value in rgb]


def run(args):
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    old = directory / '远景运行' / args.source_revision
    output = directory / '远景运行' / args.revision
    output.mkdir(parents=True, exist_ok=False)
    projection = (directory / args.projection).resolve();projection.relative_to(directory)
    failed = json.loads(projection.read_text('utf-8'))
    if failed['errors']:
        raise ValueError('投影检查有运行错误，拒绝以不可靠的失败集合修复')
    repairs = {(asset['index'], row['level']) for asset in failed['assets'] for row in asset['rows'] if not row['passed']}
    near = directory / '全图运行/r01'
    near_index = json.loads((near / '地图运行.json').read_text('utf-8'))
    source_index = json.loads((directory / '实例分页/r01/实例索引.json').read_text('utf-8'))
    prior = json.loads((old / '远景核验.json').read_text('utf-8'))
    files, rows = {}, []

    def copy(source, target, package):
        source = source.resolve();source.relative_to(package.resolve())
        target = target.resolve();target.relative_to(output.resolve())
        identity = fingerprint(source)
        if target.exists():
            if fingerprint(target)['sha256'] != identity['sha256']:
                raise ValueError('相同依赖名内容冲突')
        else:
            target.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(source, target)
        files[target.relative_to(output).as_posix()] = {'bytes': identity['bytes'], 'sha256': identity['sha256']}

    for row in prior['files']:
        pi = row.get('prototype')
        repaired = (pi, row.get('level')) in repairs
        package = near if repaired else old
        source = near / near_index['prototypes'][pi]['file'] if repaired else old / row['file']
        if repaired and near_index['prototypes'][pi]['id'] != source_index['prototypes'][pi]['id']:
            raise ValueError('近景原型来源身份变化')
        target = output / row['file']
        copy(source, target, package)
        document, _ = glb(source)
        for image in document.get('images', []):
            if 'uri' in image:
                uri = unquote(image['uri'])
                copy(source.parent / uri, target.parent / uri, package)
        if any(buffer.get('uri') for buffer in document.get('buffers', [])):
            raise ValueError('不支持未声明外置几何')
        triangles = sum(document['accessors'][primitive['indices']]['count']//3 for mesh in document['meshes'] for primitive in mesh['primitives'])
        result = {**row, 'triangles': triangles, 'copied_from': source.relative_to(directory).as_posix(), 'copied_identity': fingerprint(source), 'projection_fallback': repaired}
        if repaired:
            result.pop('geometry_signature', None)
            result['fallback'] = {'reason': '实际四向投影超门限，逐字节回退已核验近景GLB及纹理'}
            result['derived_bounds'] = row['source_bounds']
        rows.append(result)
    copy(old / '固定布景.bin', output / '固定布景.bin', old)
    colors = {str(i): source_color(directory / source['glb']) for i, source in enumerate(source_index['prototypes']) if source['category'] in ['building', 'decor']}
    atomic_json(output / '远景核验.json', {'status': 'geometry_passed_pending_projection_visual', 'files': rows, 'assets': files,
        'overview_colors': colors, 'repairs': len(repairs), 'source_projection': fingerprint(projection),
        'source_prior': fingerprint(old / '远景核验.json'), 'source_program': fingerprint(Path(__file__)),
        'recipe': {'geometry': '未失败层级逐字节沿用；失败层级逐字节回退近景及真实依赖，仍须实际投影复测',
                   'color': '原始GLB底色图有效Alpha像素平均，按primitive索引数加权；只供亚像素建筑聚合表示'}})
    print(json.dumps({'repairs': len(repairs), 'assets': len(files), 'colors': len(colors)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--projection', required=True)
    args = parser.parse_args()
    for revision in [args.source_revision, args.revision]:
        if not revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in revision):
            raise ValueError('无效修订名')
    run(args)
