"""用不对称Unity序列化夹具确认补丁方向，并逐字节核对原密度及高度的原生回写。"""

import argparse
import json
from pathlib import Path
import struct

import numpy as np
import UnityPy

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def terrain(file):
    env = UnityPy.load(str(file))
    objects = [o for o in env.objects if o.type.name == 'TerrainData']
    if len(objects) != 1:
        raise ValueError('序列化夹具不是唯一TerrainData')
    return objects[0].read_typetree()


def reconstruct(database):
    count, samples = database['m_PatchCount'], database['m_PatchSamples']
    resolution = count * samples
    result = np.zeros((6, resolution, resolution), dtype=np.uint8)
    for patch_index, patch in enumerate(database['m_Patches']):
        values = np.asarray(patch['coverage'], dtype=np.uint8)
        indices = patch['layerIndices']
        if len(values) != len(indices) * samples**2:
            raise ValueError('原生序列化补丁尺寸不符')
        x, z = patch_index % count, patch_index // count
        result[indices, z*samples:(z+1)*samples, x*samples:(x+1)*samples] = values.reshape(-1, samples, samples)
    return result


def run(directory, revision, check):
    base = directory / '草丛分布' / revision
    output = base / '独立核验' / check
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    try:
        fixture = reconstruct(terrain(base / '坐标夹具.asset')['m_DetailDatabase'])
        expected = np.zeros((6, 128, 128), dtype=np.uint8)
        for layer in range(6):
            for z in range(0, 128, 19):
                for x in range(0, 128, 23):
                    expected[layer, (z+layer*3)%128, (x+layer*5)%128] = 1+(x+z+layer)%3
        if not np.array_equal(fixture, expected):
            raise ValueError('原生不对称夹具的补丁次序或补丁内方向不符')
        journal.update(phase='A2草丛独立核验', operation='原生完整TerrainData与来源逐值比较')
        full = terrain(base / '完整草丛地形.asset')
        height = np.load(directory / '来源/TerrainData-1765.npy', allow_pickle=False)
        restored = np.asarray(full['m_Heightmap']['m_Heights'], dtype=np.int16).reshape(height.shape)
        error = int(np.abs(restored.astype(int)-height).max())
        if error:
            raise ValueError(f'原生高度回写相差{error}个源量化单位')
        layers = reconstruct(full['m_DetailDatabase'])
        counts = []
        for i in range(6):
            original = np.load(directory / f'草丛分布/r01/图层-{i}.npy', allow_pickle=False)
            if not np.array_equal(original, layers[i]):
                raise ValueError('原生密度序列化回读不符')
            counts.append(int(layers[i].sum(dtype=np.uint64)))
        del full, layers
        totals = [0]*6
        max_y_error, min_rotation, max_rotation = 0., float('inf'), float('-inf')
        for file in sorted((base / '原生补丁').glob('*.bin')):
            x, z = map(int, file.stem.split('-'))
            data = file.read_bytes()
            if data[:4] != b'BAGN' or struct.unpack_from('<I', data, 4)[0] != 1:
                raise ValueError('原生补丁身份错误')
            sizes = struct.unpack_from('<6I', data, 8)
            if len(data) != 32 + sum(sizes)*24:
                raise ValueError('原生补丁长度不符')
            start = 32
            for i, count in enumerate(sizes):
                values = np.frombuffer(data, '<f4', count=count*6, offset=start).reshape(-1, 6)
                start += count*24
                totals[i] += count
                if not count:
                    continue
                if not np.all(np.isfinite(values)) or np.any(values[:, 3:5] <= 0):
                    raise ValueError('原生变换包含无效数值')
                spacing = 11052/64
                if np.any(values[:, 0] < x*spacing-.001) or np.any(values[:, 0] > (x+1)*spacing+.001) or np.any(values[:, 2] < z*spacing-.001) or np.any(values[:, 2] > (z+1)*spacing+.001):
                    raise ValueError('原生变换不在对应补丁')
                min_rotation = min(min_rotation, float(values[:, 5].min()))
                max_rotation = max(max_rotation, float(values[:, 5].max()))
        if totals != counts:
            raise ValueError('原生逐层变换与密度计数不符')
        result = {'passed': True, 'fixture': '6层不对称位置和计数逐值相同，补丁及格内均为z行x列',
            'height_raw_max_error': error, 'density_counts': counts, 'native_counts': totals,
            'rotation_range_rad': [min_rotation, max_rotation], 'source_program': fingerprint(Path(__file__)),
            'native_receipt': fingerprint(base / '计算凭证.json'), 'density_source': fingerprint(directory / '草丛分布/r01/密度索引.json'),
            'scope': '恢复输入、原生API及序列化方向通过；6000.4.9工具派生不等同6000.3.24原游戏逐株输出；接地和显示另验',
            'human_acceptance': '待人工验收'}
        atomic_json(output / '核验凭证.json', result)
        journal.close(status='草丛密度方向及原生数据回读通过', count=sum(totals))
        print(json.dumps({'passed': True, 'counts': totals, 'height_error': error}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='草丛原生独立回读失败', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--check', required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.revision, args.check]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.revision, args.check)
