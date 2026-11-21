"""按2×2格合并精细支撑与碰撞HTTP载荷；每个内部原件保持逐字节不变。"""

import argparse
import json
from pathlib import Path
import struct

from 地图执行记录 import ROOT, atomic_json, fingerprint


def run(directory, revision):
    output = directory / '模拟分区' / revision
    output.mkdir(parents=True, exist_ok=False)
    (output / '数据').mkdir()
    surface_file = directory / '全图分块/r02/地表索引.json'
    physics_file = directory / '碰撞分页/r01/碰撞索引.json'
    surface = {r['id']: r for r in json.loads(surface_file.read_text('utf-8'))['chunks']}
    physics = {r['id']: r for r in json.loads(physics_file.read_text('utf-8'))['chunks']}
    packages, cells = {}, {}
    for pz in range(36):
        for px in range(36):
            key = f'{px:02}-{pz:02}'
            entries = []
            for z in range(pz*2, pz*2+2):
                for x in range(px*2, px*2+2):
                    cell = f'{x:02}-{z:02}'
                    ground, collider = surface[cell], physics[cell]
                    a = directory / '全图分块/r02' / ground['geometry']
                    b = directory / '碰撞分页/r01' / collider['file']
                    if fingerprint(a)['sha256'] != ground['geometry_identity']['sha256'] or fingerprint(b)['sha256'] != collider['identity']['sha256']:
                        raise ValueError('模拟分区原件摘要变化')
                    entries.append((z*72+x, a.read_bytes(), b.read_bytes()))
                    cells[cell] = key
            payload = bytearray(struct.pack('<4sIII', b'BASP', 1, len(entries), 20))
            payload.extend(bytes(len(entries)*20))
            for i, (cell, a, b) in enumerate(entries):
                ao = len(payload);payload.extend(a)
                bo = len(payload);payload.extend(b)
                struct.pack_into('<5I', payload, 16+i*20, cell, ao, len(a), bo, len(b))
            file = output / '数据' / (key+'.bin')
            file.write_bytes(payload)
            packages[key] = {'file': file.relative_to(directory).as_posix(), 'identity': fingerprint(file)}
    atomic_json(output / '模拟分区索引.json', {'schema': 'airbase-simulation-packages-v1', 'packages': packages, 'cells': cells,
        'source_surface': fingerprint(surface_file), 'source_physics': fingerprint(physics_file), 'source_program': fingerprint(Path(__file__)),
        'policy': '一请求承载2×2个125米模拟格；只解析需要的格，内部几何和物理原字节不变；显示仍按125米和49块上限'})
    print(json.dumps({'packages': len(packages), 'cells': len(cells)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效分区修订')
    run(directory, args.revision)
