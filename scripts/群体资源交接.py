"""只读核对冻结十三款交付及统一库身份，生成不含本机路径的服务清单。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'local-data/丧尸模型/十三款优化/交接/当前交付.json'
SOURCE_SHA256 = '19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_file(root, record, boundary):
    path = Path(record['path'])
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(boundary.resolve()):
        raise ValueError('交付文件超出允许目录')
    if not path.is_file() or digest(path) != record['sha256']:
        raise ValueError('交付文件缺失或摘要变化：' + path.name)
    if 'bytes' in record and path.stat().st_size != record['bytes']:
        raise ValueError('交付文件字节数变化：' + path.name)
    return path


def load_delivery(path=DEFAULT, resolution=256, root=ROOT):
    """不写交付、索引、媒体或服务；实际动画与外观仍须浏览器验证。"""
    root, path = Path(root).resolve(), Path(path).resolve()
    if resolution not in (256, 512):
        raise ValueError('仅支持256与512档')
    if not path.is_relative_to(root / 'local-data/丧尸模型/十三款优化/交接'):
        raise ValueError('交付入口不在约定交接目录')
    raw = path.read_bytes()
    delivery = json.loads(raw.decode('utf-8-sig'))
    if delivery.get('schema') != 1 or delivery.get('technical_passed') is not True:
        raise ValueError('交付协议或技术通过状态无效')
    if delivery.get('source_sha256') != SOURCE_SHA256:
        raise ValueError('交付不是约定的丧尸来源包')
    frozen = datetime.fromisoformat(delivery['frozen_at'].replace('Z', '+00:00'))
    if frozen.tzinfo is None or not delivery.get('batch_id'):
        raise ValueError('缺少有效批次或带时区的冻结时间')
    library = root / 'local-data/资源库'
    pointer_path = library / '当前版本.json'
    if digest(pointer_path) != delivery['library_pointer_sha256']:
        raise ValueError('统一库指针已改变，须重新冻结交付')
    pointer = read(pointer_path)
    index = (library / pointer['directory']).resolve()
    if not index.is_relative_to(library):
        raise ValueError('统一库索引越界')
    if pointer['revision'] != delivery['library_revision']:
        raise ValueError('统一库版本不匹配')
    for name, expected in pointer['metadata'].items():
        member = (index / name).resolve()
        if not member.is_relative_to(index) or digest(member) != expected:
            raise ValueError('统一库索引摘要变化：' + name)
    if digest(index / '资源索引.json') != delivery['catalog_sha256']:
        raise ValueError('交付与资源索引身份不匹配')
    catalog = {m['id']: m for m in read(index / '资源索引.json')['models']}
    library_routes = read(index / '资源路由.json')
    models = delivery['models']
    expected_ids = {'model:zombie-runtime'} | {f'model:zombie-{i:02d}' for i in range(1, 13)}
    if not isinstance(models, list) or len(models) != 13 or {m['id'] for m in models} != expected_ids:
        raise ValueError('必须交付13个不同的约定稳定ID')
    if {m['source_mesh_index'] for m in models} != set(range(13)):
        raise ValueError('13款独立源网格身份不完整')
    routes, public = {}, []
    for model in sorted(models, key=lambda m: m['source_mesh_index']):
        identifier = model['id']
        if identifier != ('model:zombie-runtime' if model['source_mesh_index'] == 0 else f"model:zombie-{model['source_mesh_index']:02d}"):
            raise ValueError('稳定ID与源网格编号错配')
        if identifier not in catalog or model.get('technical_passed', True) is not True:
            raise ValueError('角色未入库或技术未通过：' + identifier)
        for field in ('bones', 'materials', 'triangles'):
            if not isinstance(model[field], int) or model[field] < 1:
                raise ValueError('角色计数无效：' + field)
        clips = model['clips']
        if len(clips) != 2 or {c['name'] for c in clips} != {'待机', '移动'} or any(c['duration_s'] <= 0 for c in clips):
            raise ValueError('待机与移动片段无效')
        coordinates = model['coordinate_system']
        if coordinates.get('up') != '+Y' or coordinates.get('forward') != '+Z':
            raise ValueError('角色未统一Y向上、Z向前')
        if not model.get('source_mesh_name') or not model.get('technical_receipts'):
            raise ValueError('角色缺少源网格身份或技术回执')
        for receipt in model['technical_receipts']:
            checked_file(root, receipt, root / 'local-data')
        variants = model['variants']
        if len(variants) != 2 or {v['resolution'] for v in variants} != {256, 512}:
            raise ValueError('双档规格不完整')
        selected = None
        for variant in variants:
            asset = checked_file(root, variant, library / '当前资源/模型')
            matched = next((v for v in catalog[identifier]['runtime_variants'] if v['resolution'] == variant['resolution'] and v['sha256'] == variant['sha256'] and v['bytes'] == variant['bytes']), None)
            if not matched:
                raise ValueError('交付规格与统一库不匹配：' + identifier)
            route = library_routes[matched['url']]
            routed_path = checked_file(root, route, library / '当前资源/模型')
            if routed_path != asset:
                raise ValueError('统一库路由与交付文件不一致')
            if variant['resolution'] == resolution:
                selected = {'sha256': variant['sha256'], 'bytes': variant['bytes'], 'resolution': resolution}
                url = f"/actors/{model['source_mesh_index']:02d}-{resolution}.glb"
                routes[url] = asset
        public.append({'id': identifier, 'source_mesh_index': model['source_mesh_index'],
                       'source_mesh_name': model['source_mesh_name'], 'bones': model['bones'],
                       'materials': model['materials'], 'triangles': model['triangles'],
                       'clips': clips, 'coordinate_system': coordinates, 'url': url, 'asset': selected})
    # 读取期间发生发布或交付重写时拒绝使用混合身份。
    if path.read_bytes() != raw or digest(pointer_path) != delivery['library_pointer_sha256']:
        raise ValueError('核验期间发生交付或统一库更新')
    return {'delivery': {'sha256': hashlib.sha256(raw).hexdigest(), 'batch_id': delivery['batch_id'],
                         'frozen_at': delivery['frozen_at'], 'source_sha256': delivery['source_sha256'],
                         'library_revision': delivery['library_revision'],
                         'library_pointer_sha256': delivery['library_pointer_sha256'],
                         'catalog_sha256': delivery['catalog_sha256']}, 'models': public}, routes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', type=Path, default=DEFAULT)
    parser.add_argument('--resolution', type=int, choices=[256, 512], default=256)
    args = parser.parse_args()
    manifest, routes = load_delivery(args.delivery, args.resolution)
    print(json.dumps({'identity_verified': True, 'browser_verified': False,
                      'manifest': manifest, 'route_count': len(routes)}, ensure_ascii=False, indent=2))
