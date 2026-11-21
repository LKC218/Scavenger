"""按原始补丁及图层索引恢复草丛密度格，保留原值，不把格数升级为最终可见实例数。"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from 地图执行记录 import ROOT, atomic_json, fingerprint, Journal


def run(directory, revision):
    output = directory / '草丛分布' / revision
    output.mkdir(parents=True, exist_ok=False)
    sources = json.loads((directory / '来源/植被来源索引.json').read_text('utf-8'))
    source = next(r for r in sources if r['coverage_bytes'])
    database = source['detail_database']
    patch_count, patch_samples = database['m_PatchCount'], database['m_PatchSamples']
    resolution, prototypes = patch_count * patch_samples, database['m_DetailPrototypes']
    original = directory / source['density_file']
    if fingerprint(original)['sha256'] != source['density_identity']['sha256']:
        raise ValueError('原始密度指纹变化')
    journal = Journal(directory)
    journal.update(phase='A2草丛分布', operation='按源补丁恢复密度', current=0, total=source['patches'])
    try:
        layers = np.zeros((len(prototypes), resolution, resolution), dtype=np.uint8)
        with np.load(original) as data:
            for patch in range(patch_count**2):
                first, last = data['coverage_offsets'][patch:patch + 2]
                begin, end = data['index_offsets'][patch:patch + 2]
                indices = data['layer_indices'][begin:end]
                values = data['coverage'][first:last]
                if len(values) != len(indices) * patch_samples**2 or np.any(indices >= len(prototypes)):
                    raise ValueError('补丁图层大小或来源编号不符')
                x, z = patch % patch_count, patch // patch_count
                blocks = values.reshape(len(indices), patch_samples, patch_samples)
                layers[indices, z*patch_samples:(z+1)*patch_samples, x*patch_samples:(x+1)*patch_samples] = blocks
                if patch % 128 == 0:
                    journal.update(current=patch)
        if int(np.count_nonzero(layers)) != source['coverage_nonzero'] or int(layers.sum(dtype=np.uint64)) != source['coverage_sum']:
            raise ValueError('密度格和原始字节未守恒')
        extent_x, extent_z = source['scale']['x'] * (source['resolution'] - 1), source['scale']['z'] * (source['resolution'] - 1)
        world = np.asarray(source['world_matrix'])
        files, counts = [], []
        for i, layer in enumerate(layers):
            file = output / f'图层-{i}.npy'
            np.save(file, layer, allow_pickle=False)
            counts.append(int(np.count_nonzero(layer)))
            files.append({'prototype_index': i, 'file': file.name, 'identity': fingerprint(file), 'nonzero_cells': counts[-1]})
        combined = np.minimum(layers.sum(0, dtype=np.uint16), 6)
        colors = np.array([[30, 30, 30], [83, 140, 51], [155, 168, 62], [193, 143, 39], [157, 91, 47], [105, 65, 43], [213, 190, 94]], dtype=np.uint8)
        image = Image.fromarray(colors[combined][::-1])
        image.thumbnail((1600, 1600))
        image.save(output / '密度总览.png')
        result = {'schema': 'airbase-detail-density-v1', 'status': '原值守恒；坐标方向待独立场景核对',
            'resolution': resolution, 'patch_count': patch_count, 'patch_samples': patch_samples, 'files': files,
            'origin_source': world[:3, 3].tolist(), 'extent_source_xz': [extent_x, extent_z],
            'spacing_m': [extent_x / resolution, extent_z / resolution], 'prototypes': prototypes,
            'scatter_mode_serialized': database['m_DetailScatterMode'],
            'scatter_mode_definition': {'CoverageMode': 0, 'InstanceCountMode': 1},
            'enum_evidence': '本机构建恢复的UnityEngine.TerrainModule.dll反射枚举，独立记录另存',
            'source': fingerprint(original), 'source_program': fingerprint(Path(__file__)),
            'nonzero_cells': sum(counts), 'density_sum': int(layers.sum(dtype=np.uint64)),
            'policy': '保留整个源密度和全部六种原型；渲染排布、范围相交、缩放及密度分级另存派生凭证',
            'human_acceptance': '待人工验收'}
        atomic_json(output / '密度索引.json', result)
        journal.close(status='源草丛密度恢复完成，待坐标及派生核验', nonzero_cells=sum(counts))
        print(json.dumps({'layers': len(files), 'nonzero_cells': sum(counts), 'density_sum': result['density_sum']}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='草丛密度恢复失败，保留恢复点', error=repr(error))
        raise


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
