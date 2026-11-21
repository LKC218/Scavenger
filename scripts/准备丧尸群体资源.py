"""由独立Blender进程准备丧尸性能实验资源；原始文件只读，输出目录不得存在。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector, Quaternion

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'local-data/丧尸模型/原件/6c112bed707b4444864b606299f8b85a/super_low-poly_stylized_zombies_animated.glb'
EXPECTED = '19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea'
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import available,resolve_source
if available():SOURCE=resolve_source(EXPECTED)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def vertices(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    return [evaluated.matrix_world @ v.co for v in evaluated.data.vertices]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if sha(SOURCE) != EXPECTED:
        raise ValueError('原件摘要变化，停止')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    bpy.ops.import_scene.gltf(filepath=str(SOURCE))
    rig = next(o for o in scene.objects if o.type == 'ARMATURE')
    source = next(o for o in scene.objects if o.type == 'MESH' and o.data.name == '0')
    sys.path.insert(0, str(Path(__file__).parent))
    from 测试丧尸模型 import 独立角色
    mesh, actor, _ = 独立角色(source, rig, scene)
    scene.frame_set(0)
    if args.inspect:
        print(json.dumps({'rig_matrix': [list(r) for r in actor.matrix_world],
                          'mesh_matrix': [list(r) for r in mesh.matrix_world],
                          'bones': [{'name': b.name, 'head': list(b.head_local),
                                     'tail': list(b.tail_local), 'parent': b.parent.name if b.parent else None}
                                    for b in actor.data.bones]}, ensure_ascii=False), flush=True)
        return
    out = args.output.resolve()
    allowed = (ROOT / 'local-data/丧尸模型/性能测试').resolve()
    if out.exists() or not out.is_relative_to(allowed):
        raise ValueError('必须指定性能测试目录内不存在的新批次')
    out.mkdir(parents=True)
    # 本阶段制作基础测试循环；不把原始30秒片段当作现成行走动作。
    actor.animation_data_clear()
    for b in actor.pose.bones:
        b.matrix_basis.identity()
    bpy.context.view_layer.update()
    rest = vertices(mesh)
    low = Vector([min(v[i] for v in rest) for i in range(3)])
    high = Vector([max(v[i] for v in rest) for i in range(3)])
    root = bpy.data.objects.new('测试角色根节点', None)
    scene.collection.objects.link(root)
    actor.parent = root
    # 整体缩放到1.75米，居中并落地；源骨架与蒙皮关系保持不变。
    scale = 1.75 / (high.z - low.z)
    root.scale = (scale,) * 3
    root.rotation_euler.z = math.pi / 2
    root.location = (scale * (low.y + high.y) / 2,
                     -scale * (low.x + high.x) / 2, -scale * low.z)
    bone_names = [b.name for b in actor.pose.bones]
    def find(suffix):
        return next((b for b in actor.pose.bones if '_' + suffix + '_' in b.name), None)
    # 本角色在原骨架中沿X朝前、沿负Y朝上；旋转转换到每根骨的静置局部坐标。
    def rotate(bone, angle, lower=0):
        if bone:
            rest_q = bone.bone.matrix_local.to_quaternion()
            bone.rotation_mode = 'QUATERNION'
            bone.rotation_quaternion = rest_q.inverted() @ Quaternion((0,0,1), angle) @ Quaternion((1,0,0), lower) @ rest_q
    actions = []
    checks = {}
    for name, moving, length in [('待机', False, 48), ('移动', True, 30)]:
        action = bpy.data.actions.new(name)
        actor.animation_data_create()
        actor.animation_data.action = action
        for frame in range(length + 1):
            phase = 2 * math.pi * frame / length
            for b in actor.pose.bones:
                b.matrix_basis.identity()
                b.rotation_mode = 'QUATERNION'
            rotate(find('Spine01'), .10 + .025 * math.sin(phase))
            rotate(find('Head'), -.06 + .025 * math.cos(phase))
            for side, offset in [('L', 0), ('R', math.pi)]:
                p = phase + offset
                rotate(find(side + '_Thigh'), (.34 * math.sin(p)) if moving else .025 * math.sin(p))
                rotate(find(side + '_Calf'), (.08 + .40 * max(0, -math.sin(p))) if moving else .07)
                rotate(find(side + '_Foot'), -.04 - (.13 * math.sin(p) if moving else 0))
                rotate(find(side + '_Upperarm'), -.16 + (.13 * math.sin(p + math.pi) if moving else .025 * math.sin(p)), -1.15 if side == 'L' else 1.15)
                rotate(find(side + '_Forearm'), -.16)
            for b in actor.pose.bones:
                b.keyframe_insert('rotation_quaternion', frame=frame, group=b.name)
            scene.frame_set(frame)
        action.use_fake_user = True
        actions.append(action)
        scene.frame_set(0); a = vertices(mesh)
        scene.frame_set(length); z = vertices(mesh)
        scene.frame_set(length // 4); q = vertices(mesh)
        checks[name] = {'duration_s': length / 24,
                        'loop_max_error_m': max((x-y).length for x, y in zip(a,z)),
                        'pose_max_change_m': max((x-y).length for x,y in zip(a,q))}
        if checks[name]['loop_max_error_m'] > 1e-4 or checks[name]['pose_max_change_m'] < .005:
            raise ValueError('动作接缝或蒙皮变化检查失败')
    # 每段保存在独立NLA轨，导出两个命名片段。
    actor.animation_data.action = None
    for action in actions:
        track = actor.animation_data.nla_tracks.new()
        track.name = action.name
        track.strips.new(action.name, 0, action)
        track.mute = True
    scene.frame_start, scene.frame_end = 0, 48
    for o in list(scene.objects):
        if o not in (actor, mesh, root):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action='SELECT')
    scene.frame_set(0)
    blend = out / '群体测试角色.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    target = out / '群体测试角色.glb'
    bpy.ops.export_scene.gltf(filepath=str(target), export_format='GLB', use_selection=True,
                              export_animations=True, export_animation_mode='NLA_TRACKS',
                              export_force_sampling=True, export_nla_strips=True,
                              export_extras=True, export_cameras=False, export_lights=False)
    record = {'source_sha256': EXPECTED, 'source_id': '6c112bed707b4444864b606299f8b85a',
              'author': 'Jungle Jim', 'license': 'CC BY 4.0',
              'source_url': 'https://sketchfab.com/3d-models/super-low-poly-stylized-zombies-animated-6c112bed707b4444864b606299f8b85a',
              'changes': '编号0拆分23骨骼、去空形态键、统一高度1.75米；待机和移动为本项目自制测试循环，并非原动作复刻。',
              'blender': bpy.app.version_string, 'bone_names': bone_names,
              'bones': len(actor.data.bones), 'vertices': len(mesh.data.vertices),
              'checks': checks, 'asset': {'file': target.name, 'bytes': target.stat().st_size, 'sha256': sha(target)},
              'blend': {'file': blend.name, 'sha256': sha(blend)}, 'source_unchanged': sha(SOURCE) == EXPECTED}
    (out / '资源清单.json').write_text(json.dumps(record,ensure_ascii=False,indent=2), 'utf-8')
    print(json.dumps(record,ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
