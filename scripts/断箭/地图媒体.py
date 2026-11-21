"""GLB图像外置并按内容摘要共享，原始工程不变。仅处理普通未压缩GLB。"""
import hashlib
import json
from pathlib import Path
import struct


def externalize(path, root):
    data = path.read_bytes()
    if data[:4] != b'glTF': raise ValueError('不是GLB')
    length, kind = struct.unpack_from('<II', data, 12)
    if kind != 0x4E4F534A: raise ValueError('缺少JSON块')
    doc = json.loads(data[20:20+length])
    offset = 20 + length
    binary = data[offset+8:offset+8+struct.unpack_from('<I', data, offset)[0]]
    image_views = set()
    assets = []
    for image in doc.get('images', []):
        if 'bufferView' not in image: raise ValueError('预期嵌入图像')
        index = image.pop('bufferView'); image_views.add(index)
        view = doc['bufferViews'][index]; start = view.get('byteOffset', 0)
        pixels = binary[start:start+view['byteLength']]
        digest = hashlib.sha256(pixels).hexdigest()
        suffix = {'image/jpeg': '.jpg', 'image/ktx2': '.ktx2', 'image/webp': '.webp'}.get(image.get('mimeType'), '.png')
        target = root / '贴图' / (digest + suffix)
        target.parent.mkdir(exist_ok=True)
        if not target.exists(): target.write_bytes(pixels)
        import os
        image['uri'] = os.path.relpath(target, path.parent).replace('\\', '/')
        image['name'] = digest
        assets.append(target.relative_to(root).as_posix())
    for texture in doc.get('textures', []):
        source = texture.get('source')
        if source is None:
            extension = texture.get('extensions', {})
            source = next(v['source'] for v in extension.values() if 'source' in v)
        image = doc['images'][source]
        texture['name'] = '共享图像-' + image['name']
    views, remap, output = [], {}, bytearray()
    for index, view in enumerate(doc.get('bufferViews', [])):
        if index in image_views: continue
        while len(output) % 4: output.append(0)
        remap[index] = len(views)
        view = dict(view)
        if view.get('buffer', 0) == 0:
            start = view.get('byteOffset', 0)
            view['byteOffset'] = len(output)
            output.extend(binary[start:start+view['byteLength']])
        compressed = view.get('extensions', {}).get('EXT_meshopt_compression')
        if compressed:
            if compressed['buffer'] != 0: raise ValueError('未支持多源压缩缓冲')
            while len(output) % 4: output.append(0)
            start = compressed.get('byteOffset', 0)
            compressed['byteOffset'] = len(output)
            output.extend(binary[start:start+compressed['byteLength']])
        views.append(view)
    for accessor in doc.get('accessors', []):
        if 'bufferView' in accessor: accessor['bufferView'] = remap[accessor['bufferView']]
        for row in accessor.get('sparse', {}).values():
            if isinstance(row, dict) and 'bufferView' in row: row['bufferView'] = remap[row['bufferView']]
    doc['bufferViews'] = views
    doc['buffers'][0] = {'byteLength': len(output)}
    text = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf8')
    text += b' ' * (-len(text) % 4)
    output += b'\0' * (-len(output) % 4)
    result = struct.pack('<4sII', b'glTF', 2, 12+8+len(text)+8+len(output))
    result += struct.pack('<II', len(text), 0x4E4F534A) + text
    result += struct.pack('<II', len(output), 0x004E4942) + output
    path.write_bytes(result)
    return assets
