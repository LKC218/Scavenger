"""使用同一材质语义构建指定原生植被LOD工程，并在新进程严格回读。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import Journal, atomic_json, fingerprint
from 构建全图原型 import build_one, readback_one


def run(directory, source_revision, revision, mode, lod_level=1):
    source = directory / '植被细节' / source_revision / f'来源/植被LOD{lod_level}.json'
    output = directory / '植被细节' / revision
    if mode == 'build':
        output.mkdir(parents=True, exist_ok=False)
    material_file = directory / '来源/材质语义-r03.json'
    materials = json.loads(material_file.read_text('utf-8'))['materials']
    sources = json.loads(source.read_text('utf-8'))
    journal, results = Journal(directory), []
    try:
        for i, entry in enumerate(sources):
            journal.update(phase=f'A2原生植被LOD{lod_level}', operation='构建工程' if mode == 'build' else '新进程工程与GLB回读', current=i, total=len(sources))
            if entry['lod_level'] != lod_level:
                raise ValueError('来源植被级别与请求不符')
            row = {**entry, 'source_local_bounds': entry['bounds'], 'exported': {'scene': str(directory / entry['scene'])}}
            target = output / entry['asset_id'].split(':')[1]
            if mode == 'build':
                result = build_one(row, target, True, materials, 'unique')
            else:
                if (target / '回读凭证.json').exists():
                    raise FileExistsError('回读凭证已有，拒绝覆盖')
                result = readback_one(row, target, True)
            results.append(result)
            atomic_json(output / (mode+'-汇总.json'), results)
        atomic_json(output / (mode+'-凭证.json'), {'passed': True, 'source_lod': fingerprint(source), 'materials': fingerprint(material_file),
            'count': len(results), 'source_program': fingerprint(Path(__file__)), 'builder': fingerprint(Path(__file__).with_name('构建全图原型.py')),
            'scope': f'原生LOD{lod_level}构建或独立结构回读；本次四向图仍须实际审阅', 'human_acceptance': '待人工验收'})
        journal.close(status=f'植被LOD{lod_level}'+('构建完成，待新进程回读' if mode == 'build' else '结构回读完成，待审图及网页'), count=len(results))
        print(json.dumps({'mode': mode, 'count': len(results)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='植被LOD1构建或回读失败', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--mode', choices=['build', 'verify'], required=True)
    parser.add_argument('--lod-level', type=int, choices=[0, 1, 2], default=1)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for value in [args.source_revision, args.revision]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(directory, args.source_revision, args.revision, args.mode, args.lod_level)
