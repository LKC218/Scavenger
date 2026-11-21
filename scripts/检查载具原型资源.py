"""通过正式资料库固定两款载具及声音，只向独立原型目录派生资源。"""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'local-data/原型/载具靶场'
MODEL_IDS = {'tank': 'model:US_M1A2', 'heli': 'model:US_AH64D'}
AUDIO = {
    'tankShot': ('tank', 'audio:f07e2819-3033-4eef-860b-cc9cafd80e31:49073e01da189783'),
    'tankImpact': ('tank', 'audio:d9dc23e9-37e8-4eeb-95a4-5331317b98dd:9e553e2f45c3013d'),
    'tankMove': ('tank', 'audio:ff1ba8b0-db8d-4187-ba22-35dd8762b5d4:fac3530269ac8b52'),
    'heliShot': ('heli', 'audio:e937bdf0-5f3e-4555-b864-9bbd4ff1fff8:f91ab61bcb334e9b'),
    'heliImpact': ('heli', 'audio:cb642353-edd3-4e72-baca-90c61a0be49a:9e553e2f45c3013d'),
    'missileShot': ('heli', 'audio:9b192d85-200c-4e17-8788-9801ffab417f:49073e01da189783'),
    'missileImpact': ('heli', 'audio:bfda472e-5ed0-40d4-bead-3f9abffcc986:9e553e2f45c3013d'),
    'heliMove': ('heli', 'audio:78be8b8a-f46a-4ffb-b5ff-bbb34d23ca7c:204aa9fa2d39cfd7'),
}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query(identity):
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(ROOT / 'scripts/断箭/查询资料库.py'), '--id', identity, '--json'],
                            check=True, capture_output=True, text=True, encoding='utf-8')
    data = json.loads(result.stdout)
    if data['total'] != 1: raise ValueError(f'资源身份不唯一：{identity}')
    return data['revision'], data['results'][0]


def read_glb(path):
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from('<III', data)
    if magic != 0x46546C67 or version != 2 or length != len(data): raise ValueError('GLB头无效')
    size = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + size]), bytearray(data[28 + size:])


def array(doc, binary, accessor_id):
    a = doc['accessors'][accessor_id]; v = doc['bufferViews'][a['bufferView']]
    dtype = {5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: 'u1'}[a['componentType']]
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[a['type']]
    step = v.get('byteStride', np.dtype(dtype).itemsize * width)
    return np.ndarray((a['count'], width), dtype=dtype, buffer=binary,
                      offset=v.get('byteOffset', 0) + a.get('byteOffset', 0), strides=(step, np.dtype(dtype).itemsize)).copy()


def components(doc, binary, primitive):
    positions = array(doc, binary, primitive['attributes']['POSITION'])
    triangles = array(doc, binary, primitive['indices']).reshape(-1, 3)
    _, welded = np.unique(np.round(positions, 4), axis=0, return_inverse=True)
    parent = list(range(int(welded.max()) + 1))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for row in welded[triangles]:
        a, b, c = map(find, row); parent[b] = a; parent[c] = a
    groups = {}
    for i, row in enumerate(triangles): groups.setdefault(find(int(welded[row[0]])), []).append(i)
    return positions, triangles, [np.array(v) for v in groups.values()]


def inspect():
    for kind, identity in MODEL_IDS.items():
        _, row = query(identity); doc, binary = read_glb(row['local_paths']['model_url'])
        primitive = doc['meshes'][4 if kind == 'tank' else 12]['primitives'][0]
        pos, triangles, groups = components(doc, binary, primitive)
        print(kind)
        for group in sorted(groups, key=len, reverse=True):
            if len(group) < 15: continue
            p = pos[triangles[group]].reshape(-1, 3)
            print(len(group), np.round(p.min(0), 3).tolist(), np.round(p.max(0), 3).tolist())


def append_view(doc, binary, data, target=None):
    binary.extend(b'\0' * ((-len(binary)) % 4)); offset = len(binary); binary.extend(data)
    view = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)}
    if target: view['target'] = target
    doc['bufferViews'].append(view)
    return len(doc['bufferViews']) - 1


def write_glb(path, doc, binary):
    doc['buffers'][0]['byteLength'] = len(binary)
    js = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode('utf-8'); js += b' ' * (-len(js) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    Path(path).write_bytes(struct.pack('<III', 0x46546C67, 2, 28 + len(js) + len(binary)) + struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(binary), 0x004E4942) + binary)


def derive(kind, path):
    doc, binary = read_glb(path)
    original_triangles = sum(doc['accessors'][p['indices']]['count'] // 3 for m in doc['meshes'] for p in m['primitives'])
    mesh_id = 4 if kind == 'tank' else 12
    source = doc['meshes'][mesh_id]['primitives'][0]
    positions, triangles, groups = components(doc, binary, source)
    labels = np.zeros(len(triangles), dtype=np.int32)
    names = ['炮管'] if kind == 'tank' else ['主旋翼', '尾旋翼', '机炮']
    for group in groups:
        points = positions[triangles[group]].reshape(-1, 3)
        lo, hi = points.min(0), points.max(0)
        if kind == 'tank':
            if max(abs(lo[0]), abs(hi[0])) < .42 and hi[2] < -1.3: labels[group] = 1
        else:
            if lo[1] > 3.58 and hi[1] < 4.03 and lo[2] > -7.5 and hi[2] < 7.5: labels[group] = 1
            elif hi[0] < -.20 and lo[2] > 7.7 and lo[1] > 1.5: labels[group] = 2
            elif hi[1] < 1.23 and lo[2] < -2.5 and hi[2] < -2.5 and max(abs(lo[0]), abs(hi[0])) < .45: labels[group] = 3
    source_node = next(i for i, n in enumerate(doc['nodes']) if n.get('mesh') == mesh_id)
    parent_node = next(n for n in doc['nodes'] if source_node in n.get('children', []))
    counts = {}
    for label in range(len(names) + 1):
        indices = triangles[labels == label].astype('<u4').ravel()
        if not len(indices): raise ValueError(f'部件未识别：{kind}/{label}')
        view = append_view(doc, binary, indices.tobytes(), 34963)
        doc['accessors'].append({'bufferView': view, 'componentType': 5125, 'count': len(indices), 'type': 'SCALAR'})
        primitive = copy.deepcopy(source); primitive['indices'] = len(doc['accessors']) - 1
        name = '主体' if label == 0 else names[label - 1]
        counts[name] = len(indices) // 3
        if label == 0: doc['meshes'][mesh_id]['primitives'][0] = primitive
        else:
            doc['meshes'].append({'name': name, 'primitives': [primitive]})
            doc['nodes'].append({'name': '原型' + name, 'mesh': len(doc['meshes']) - 1})
            parent_node.setdefault('children', []).append(len(doc['nodes']) - 1)
    if kind == 'tank':
        for node in doc['nodes']:
            if node.get('mesh') in (4, 5, 6, 7): node['name'] = '原型炮塔附件' + str(node['mesh'])
    # 重建每个缓冲区视图；原图片字节不留在派生文件中。保留所有顶点与三角形。
    images = {}
    texture_sizes = []
    for im in doc.get('images', []):
        view = doc['bufferViews'][im['bufferView']]
        data = bytes(binary[view.get('byteOffset', 0):view.get('byteOffset', 0) + view['byteLength']])
        with Image.open(io.BytesIO(data)) as image:
            before = list(image.size)
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            out = io.BytesIO(); image.save(out, format='PNG', optimize=True)
            images[im['bufferView']] = out.getvalue(); im['mimeType'] = 'image/png'
            texture_sizes.append({'before': before, 'after': list(image.size)})
    compact = bytearray()
    for i, view in enumerate(doc['bufferViews']):
        compact.extend(b'\0' * (-len(compact) % 4))
        data = images.get(i)
        if data is None: data = bytes(binary[view.get('byteOffset', 0):view.get('byteOffset', 0) + view['byteLength']])
        view['byteOffset'] = len(compact); view['byteLength'] = len(data); compact.extend(data)
    doc['asset']['generator'] = '载具靶场派生：部件精确分区与1024贴图；非原游戏动画'
    output = OUT / (kind + '.glb'); write_glb(output, doc, compact)
    after, _ = read_glb(output)
    after_triangles = sum(after['accessors'][p['indices']]['count'] // 3 for m in after['meshes'] for p in m['primitives'])
    if original_triangles != after_triangles: raise ValueError('三角形不守恒')
    return output, {'triangles_before': original_triangles, 'triangles_after': after_triangles, 'parts': counts,
                    'textures': texture_sizes, 'source_animations': len(doc.get('animations', [])), 'front': '-Z', 'up': '+Y'}


def prepare():
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 兼容维护 import check_runtime
        check_runtime(sync_legacy=True)
        return
    OUT.mkdir(parents=True, exist_ok=True)
    routes = {}; inputs = []; manifest = {'schema': 1, 'models': {}, 'audio': {}, 'notes': ['程序动作与挂点为原型派生', '世界尺度1单位约1米，武器参数为游戏化参数', '音效为有限事件渲染，未确认原生循环接缝']}
    def register(path, identity, expected=None):
        path = Path(path).resolve(); digest = sha(path)
        if expected and digest != expected: raise ValueError('输入摘要变化：' + identity)
        if not path.is_relative_to((ROOT / 'local-data').resolve()): raise ValueError('资源越界')
        url = '/media/' + digest + path.suffix
        routes[url] = {'path': str(path), 'sha256': digest, 'bytes': path.stat().st_size}
        return url
    for kind, identity in MODEL_IDS.items():
        revision, row = query(identity)
        path = Path(row['local_paths']['model_url']); before = sha(path)
        if before != row['sha256']: raise ValueError('原模型摘要不符')
        output, detail = derive(kind, path)
        if sha(path) != before: raise ValueError('原模型被修改')
        url = register(output, kind)
        manifest['models'][kind] = {'id': identity, 'url': url, 'source_sha256': before, 'bytes': output.stat().st_size, 'detail': detail}
        inputs.append({'id': identity, 'revision': revision, 'path': str(path), 'sha256': before, 'bytes': row['bytes'], 'derived_bytes': output.stat().st_size})
    for key, (kind, identity) in AUDIO.items():
        revision, row = query(identity)
        if MODEL_IDS[kind] not in row['model_ids']: raise ValueError('缺少型号声音关联')
        url = register(row['local_paths']['url'], identity, row['sha256'])
        check = row['check']
        manifest['audio'][key] = {'audio_id': identity, 'url': url, 'name': row['name'], 'sha256': row['sha256'],
                                 'duration_seconds': check['duration_seconds'], 'sample_rate': check['sample_rate'], 'channels': check['channels'],
                                 'bindings': [b for b in row['bindings'] if b['model_id'] == MODEL_IDS[kind]]}
        inputs.append({'id': identity, 'revision': revision, **routes[url]})
    for name, value in [('资源清单.json', manifest), ('私有路由.json', routes), ('输入核验.json', inputs)]:
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'models': {k: {'bytes': v['bytes'], 'detail': v['detail']['parts']} for k, v in manifest['models'].items()}, 'audio': len(manifest['audio'])}, ensure_ascii=False))


def check():
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 兼容维护 import check_runtime
        check_runtime(sync_legacy=False)
        return
    rows = json.loads((OUT / '输入核验.json').read_text('utf-8'))
    routes = json.loads((OUT / '私有路由.json').read_text('utf-8'))
    for row in [*rows, *routes.values()]:
        if sha(row['path']) != row['sha256']: raise ValueError('摘要不符：' + row['path'])
    print(f'原件和派生资源摘要通过：{len(rows)}输入，{len(routes)}媒体路由')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    if args.inspect: inspect()
    elif args.prepare: prepare()
    else: check()


if __name__ == '__main__': main()
