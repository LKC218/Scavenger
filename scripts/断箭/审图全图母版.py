"""独立重开母版，只渲染默认总览四向图；不保存或更改源工程。"""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import Journal, atomic_json, fingerprint


def run(args):
    directory = args.directory.resolve();directory.relative_to(ROOT / 'local-data/断箭/批次')
    master = directory / '总装配' / args.revision
    output = master / '审图' / args.run
    output.mkdir(parents=True, exist_ok=False)
    record = json.loads((master / '母版构建凭证.json').read_text('utf-8'))
    file = master / 'Airbase全图母版.blend'
    identity = fingerprint(file)
    if identity['sha256'] != record['blend_identity']['sha256']:
        raise ValueError('母版来源摘要变化')
    journal, rows = Journal(directory), []
    try:
        bpy.ops.wm.open_mainfile(filepath=str(file), load_ui=False)
        scene = bpy.context.scene
        scene.render.engine = 'BLENDER_EEVEE'
        scene.render.resolution_x = scene.render.resolution_y = 960
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'
        camera = scene.camera
        camera.data.type = 'ORTHO';camera.data.ortho_scale = 14500;camera.data.clip_end = 200000
        center = Vector((75, -350, 0))
        for name, direction in [('北侧', (0, -1, 1.5)), ('东侧', (1, 0, 1.5)), ('南侧', (0, 1, 1.5)), ('西侧', (-1, 0, 1.5))]:
            journal.update(phase='A2母版四向审图', operation='默认总览独立渲染', view=name)
            camera.location = center+Vector(direction).normalized()*18000
            camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
            path = output / (name+'.png');scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            rows.append({'view': name, 'file': path.name, 'identity': fingerprint(path)})
        if fingerprint(file)['sha256'] != identity['sha256']:
            raise ValueError('审图修改了源母版')
        atomic_json(output / '审图输入.json', {'views': rows, 'blend': identity, 'source_program': fingerprint(Path(__file__)),
            'scope': '默认总览四向；81个高细节源集合继续保留，全部作者矩阵及133种实际求值见独立核验',
            'ai_visual': '待实际查看', 'human_acceptance': '待人工验收'})
        journal.close(status='母版四向图已渲染，源工程未变，待实际查看')
        print(json.dumps({'views': len(rows), 'source_unchanged': True}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='母版审图渲染失败，保留候选', error=repr(error));raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    for value in [args.revision, args.run]:
        if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
            raise ValueError('无效修订名')
    run(args)
