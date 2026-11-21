"""在新Blender进程重开已收集工程，核对真实相对链接及内嵌图像，不保存源文件。"""

import argparse
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import atomic_json, fingerprint, Journal


def run(directory, collection, revision):
    output = collection / '独立回读' / revision
    output.mkdir(parents=True, exist_ok=False)
    receipt_path = collection / '依赖收集凭证.json'
    receipt = json.loads(receipt_path.read_text('utf-8'))
    journal = Journal(directory)
    records = []
    try:
        for index, (key, identity) in enumerate(receipt['files'].items()):
            path = (collection / key).resolve()
            path.relative_to(collection)
            journal.update(phase='A1双层资产样本', operation='收集副本独立工程回读', current=index + 1,
                           total=len(receipt['files']), file=key)
            if fingerprint(path)['sha256'] != identity['sha256']:
                raise ValueError('收集后的文件身份变化：' + key)
            if path.suffix != '.blend':
                continue
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
            if bpy.context.scene.unit_settings.scale_length != 1:
                raise ValueError('收集工程米制不符')
            libraries = []
            for library in bpy.data.libraries:
                if not library.filepath.startswith('//'):
                    raise ValueError('收集工程含绝对链接')
                linked = Path(bpy.path.abspath(library.filepath)).resolve()
                linked.relative_to(collection)
                if linked.relative_to(collection).as_posix() not in receipt['files'] or not linked.is_file():
                    raise ValueError('实际工程链接未被依赖闭包覆盖')
                libraries.append(linked.relative_to(collection).as_posix())
            images = [img for img in bpy.data.images if img.source == 'FILE']
            if any(not img.packed_file for img in images):
                raise ValueError('收集工程含未打包图像')
            graph = bpy.context.evaluated_depsgraph_get()
            meshes = sum(1 for item in graph.object_instances if item.object.type == 'MESH')
            if not meshes:
                raise ValueError('收集工程没有实际可求值网格')
            records.append({'file': key, 'libraries': libraries, 'packed_images': len(images),
                            'evaluated_meshes': meshes, 'sha256': identity['sha256']})
        for key, identity in receipt['files'].items():
            if fingerprint(collection / key)['sha256'] != identity['sha256']:
                raise ValueError('回读改变了收集文件')
        result = {'status': '收集依赖及工程独立重开通过', 'passed': True, 'files': len(receipt['files']),
                  'projects': records, 'collection_receipt': fingerprint(receipt_path),
                  'human_acceptance': '待人工验收', 'scope': '独立工程、真实链接与图像；不等于网页或全图通过'}
        atomic_json(output / '核验凭证.json', result)
        journal.close(status='A1依赖收集工程回读通过，继续候选预览', projects=len(records))
        print(json.dumps({'passed': True, 'projects': len(records)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='依赖收集回读失败，保留证据', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    directory, collection = args.directory.resolve(), args.collection.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    collection.relative_to(ROOT / 'local-data')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, collection, args.revision)
