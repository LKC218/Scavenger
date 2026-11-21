"""按冻结几何、实例及图像尺寸估算本轮母版和分块工作集，并记录本机实时余量。"""

import argparse
import ctypes
import json
from pathlib import Path
import shutil
import time

from 地图执行记录 import ROOT, atomic_json, fingerprint


def memory_status():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in ['physical_total', 'physical_available', 'page_total',
                                                            'page_available', 'virtual_total', 'virtual_available', 'extended']]]
    status = Status()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError('无法读取实际内存余量')
    return {name: int(getattr(status, name)) for name in ['physical_total', 'physical_available', 'load']}


def run(directory, revision):
    from PIL import Image
    output = directory / '容量预检' / (revision + '.json')
    if output.exists():
        raise FileExistsError(output)
    surface_file = directory / '表面来源/full-r01/直接表面.json'
    atlas_file = directory / '地表派生/r03/地表图集.json'
    instance_file = directory / '实例分页/r01/实例索引.json'
    surface, atlas, instances = [json.loads(p.read_text('utf-8')) for p in [surface_file, atlas_file, instance_file]]
    prototypes = json.loads((directory / '来源/全量原型来源.json').read_text('utf-8'))
    images = {Path(m['basecolor']) for m in surface['materials'].values() if m.get('basecolor')}
    images.update(directory / '地表派生/r03' / row['file'] for row in atlas['parts'])
    image_records = []
    for path in sorted(images):
        with Image.open(path) as im:
            width, height = im.size
        image_records.append({'file': path.relative_to(ROOT).as_posix(), 'width': width, 'height': height,
                              'rgba8': width * height * 4, 'rgba32f': width * height * 16})
    libraries_bytes = 0
    for p in prototypes:
        libraries_bytes += (directory / '独立原型/full-r06' / p['asset_id'].split(':')[1] / '原型.blend').stat().st_size
    triangles = sum(p['triangles'] for p in surface['parts'])
    # 本估算适用于未实现的集合实例和默认总览；不展开43万份原型顶点。
    geometry_bytes = triangles * 3 * 64 + triangles * 12
    instance_bytes = instances['count'] * (64 + 12 + 12)
    estimated_master_ram = (sum(p['rgba32f'] for p in image_records) + libraries_bytes + geometry_bytes +
                            instance_bytes * 3 + 2 * 1024**3)
    estimated_new_disk = (sum(p['identity']['bytes'] for p in atlas['parts']) * 4 + libraries_bytes * 2 +
                          geometry_bytes * 12 + instances['count'] * 104 * 4 + 4 * 1024**3)
    memory, disk = memory_status(), shutil.disk_usage(directory)
    passed = memory['physical_available'] > estimated_master_ram + 1024**3 and disk.free > estimated_new_disk
    result = {'passed': passed, 'created_epoch': time.time(), 'memory': memory, 'disk_free': disk.free,
        'estimate': {'master_ram': estimated_master_ram, 'new_disk': estimated_new_disk, 'surface_geometry': geometry_bytes,
                     'point_instance_data': instance_bytes, 'linked_library_bytes': libraries_bytes},
        'source': {'surface': fingerprint(surface_file), 'atlas': fingerprint(atlas_file), 'instances': fingerprint(instance_file)},
        'images': image_records, 'policy': '按集合实例保留完整坐标系，母版默认总览；不逐实例复制几何或创建43万个独立对象。制作时继续检查余量。',
        'limitation': '用于本轮母版和分块准入，不等于浏览器驻留或硬件性能验收，也不为未来不同规模作业自动放行'}
    atomic_json(output, result)
    print(json.dumps({k: result[k] for k in ['passed', 'memory', 'disk_free', 'estimate']}, ensure_ascii=False))
    if not passed:
        raise MemoryError('当前余量不足以按本轮估算启动，须调整装配组织或等待余量恢复')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.revision)
