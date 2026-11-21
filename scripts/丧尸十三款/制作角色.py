"""独立后台Blender制作单款角色；原包只读，输出必须是新目录。"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

import bpy
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'scripts/资源库')]
from 统一资源 import restore, sha, save
from 测试丧尸模型 import 独立角色

SOURCE_SHA = '19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea'


def vertices(mesh):
    e = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return [e.matrix_world @ v.co for v in e.data.vertices]


def bounds(vs):
    return [Vector([fn(v[i] for v in vs) for i in range(3)]) for fn in (min, max)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--inspect', action='store_true')
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:])
    out = a.output.resolve()
    if not out.is_relative_to((ROOT/'local-data/丧尸模型/十三款优化').resolve()) or out.exists():
        raise ValueError('输出必须是专项内不存在的新目录')
    source = restore(SOURCE_SHA)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.unit_settings.system = 'METRIC'
    rig = next(o for o in scene.objects if o.type == 'ARMATURE')
    meshes = {int(o.data.name): o for o in scene.objects if o.type == 'MESH' and o.data.name.isdecimal()}
    if set(meshes) != set(range(13)):
        raise ValueError('原包角色编号不完整')
    original = meshes[a.index]
    # 原包所有形态键都必须实际为零，才允许复用既有拆分函数。
    shape_error = 0.
    if original.data.shape_keys:
        blocks = original.data.shape_keys.key_blocks
        shape_error = max(((v.co-blocks[0].data[i].co).length for block in blocks[1:] for i,v in enumerate(block.data)), default=0.)
        if shape_error > 1e-8:
            raise ValueError('存在真实形态变化，不能按空形态键删除')
    mesh, actor, weighted = 独立角色(original, rig, scene)
    # 所有原动作帧逐顶点比较；源单位通过统一尺度换算为米。
    reference = meshes[0]
    saved_action = rig.animation_data.action
    rig.animation_data.action = None
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()
    lo0, hi0 = bounds(vertices(reference))
    common_scale = 1.75/(hi0.z-lo0.z)
    rig.animation_data.action = saved_action
    max_error = 0.
    samples = []
    last = float(saved_action.frame_range[1])
    for frame in range(math.ceil(last)+1):
        t = min(float(frame), last)
        scene.frame_set(int(t), subframe=t%1)
        va, vb = vertices(original), vertices(mesh)
        error = max((x-y).length for x,y in zip(va,vb))*common_scale
        max_error = max(max_error,error)
        if frame%60 == 0 or t == last:
            samples.append({'frame':t,'max_error_m':error})
        if error > 1e-4:
            raise ValueError(f'原动作拆分顶点不一致：{frame} {error}')
    source_action = actor.animation_data.action
    source_action.name = f'原始动作-角色{a.index:02}'
    source_action.use_fake_user = True
    actor.animation_data_clear()
    for b in actor.pose.bones:
        b.matrix_basis.identity()
    bpy.context.view_layer.update()
    low, high = bounds(vertices(mesh))
    canonical = {}
    for name in [b.name for b in actor.data.bones if b.name.startswith('CC_Base_')]:
        key = re.sub(r'_\d+$','',name).removeprefix('CC_Base_')
        if key in canonical:
            raise ValueError('加权骨骼角色身份混合')
        canonical[key] = name
    # 由该网格真实加权骨名解析语义，不依赖导入顺序或编号0后缀。
    for key in ['Hip','Spine01','L_Thigh','R_Thigh','L_Calf','R_Calf','L_Foot','R_Foot','L_Upperarm','R_Upperarm']:
        if key not in canonical:
            raise ValueError('缺少动作所需骨骼：'+key)
    if a.inspect:
        print(json.dumps({'index':a.index,'weighted':sorted(weighted),'canonical':canonical,'bones':len(actor.data.bones),'scale':common_scale,'bounds':[list(low),list(high)],'rig_matrix':[list(r) for r in actor.matrix_world],'split_error_m':max_error},ensure_ascii=False),flush=True)
        return
    out.mkdir(parents=True)
    actor.name = f'角色{a.index:02}-骨架'
    mesh.name = f'角色{a.index:02}-蒙皮'
    root = bpy.data.objects.new(f'角色{a.index:02}-运行根',None)
    scene.collection.objects.link(root)
    actor.parent = root
    root.scale = (common_scale,)*3
    # 从每款左右肩的静置位置推导身体坐标，消除原包不同的朝向。
    left=actor.matrix_world @ actor.data.bones[canonical['L_Upperarm']].head_local
    right=actor.matrix_world @ actor.data.bones[canonical['R_Upperarm']].head_local
    lateral=right-left;lateral.z=0;lateral.normalize()
    forward=Vector((0,0,1)).cross(lateral).normalized()
    angle=-math.pi/2-math.atan2(forward.y,forward.x)
    root.rotation_euler.z=angle
    center=(low+high)/2
    root.location=(-common_scale*(math.cos(angle)*center.x-math.sin(angle)*center.y),-common_scale*(math.sin(angle)*center.x+math.cos(angle)*center.y),-common_scale*low.z)
    # 动作轴转到骨架空间，再由每根骨骼静置矩阵转到局部空间。
    inverse=actor.matrix_world.to_3x3().inverted()
    lateral_axis=(inverse@lateral).normalized()
    forward_axis=(inverse@forward).normalized()
    for o in list(scene.objects):
        if o not in (actor,mesh,root):
            bpy.data.objects.remove(o,do_unlink=True)
    # 移除其他角色未使用的数据，避免每款工程携带整包贴图和原动画。
    bpy.data.orphans_purge(do_recursive=True)
    def find(key):
        return actor.pose.bones.get(canonical.get(key,''))
    def rotate(key,angle,lower=0):
        b = find(key)
        if b:
            q = b.bone.matrix_local.to_quaternion()
            b.rotation_quaternion = q.inverted() @ Quaternion(lateral_axis,angle) @ Quaternion(forward_axis,lower) @ q
    checks = {}
    actions = []
    actor_base_location=actor.location.copy()
    for name,moving,length in [('待机',False,48),('移动',True,30)]:
        action = bpy.data.actions.new(name)
        actor.animation_data_create()
        actor.animation_data.action = action
        for frame in range(length+1):
            phase = 2*math.pi*frame/length
            for bone in actor.pose.bones:
                bone.matrix_basis.identity()
                bone.rotation_mode = 'QUATERNION'
            rotate('Spine01',.10+.025*math.sin(phase))
            rotate('Head',-.06+.025*math.cos(phase))
            for side,offset in [('L',0),('R',math.pi)]:
                phase_side = phase+offset
                rotate(side+'_Thigh',.34*math.sin(phase_side) if moving else .025*math.sin(phase_side))
                rotate(side+'_Calf',.08+.40*max(0,-math.sin(phase_side)) if moving else .07)
                rotate(side+'_Foot',-.04-(.13*math.sin(phase_side) if moving else 0))
                rotate(side+'_Upperarm',-.16+(.13*math.sin(phase_side+math.pi) if moving else .025*math.sin(phase_side)),-1.05 if side=='L' else 1.05)
                rotate(side+'_Forearm',-.16)
            for bone in actor.pose.bones:
                bone.keyframe_insert('rotation_quaternion',frame=frame,group=bone.name)
        # 逐帧整体落地校正，不改变骨骼长度或权重；未提供足部IK。
        for frame in range(length+1):
            scene.frame_set(frame)
            actor.location=actor_base_location
            bpy.context.view_layer.update()
            bottom=min(v.z for v in vertices(mesh))
            actor.location.z-=bottom/common_scale
            actor.keyframe_insert('location',frame=frame)
        action.use_fake_user = True
        actions.append(action)
        samples_v = []
        foot_min = []
        for frame in range(length+1):
            scene.frame_set(frame)
            vs = vertices(mesh)
            if not all(math.isfinite(c) for v in vs for c in v):
                raise ValueError('动作顶点非有限')
            samples_v.append(vs)
            foot_min.append(min(v.z for v in vs))
        seam = max((v-w).length for v,w in zip(samples_v[0],samples_v[-1]))
        change = max((v-w).length for v,w in zip(samples_v[0],samples_v[length//4]))
        if seam>1e-4 or change<.005:
            raise ValueError('循环接缝或顶点变化失败')
        checks[name] = {'duration_s':length/24,'loop_max_error_m':seam,'pose_max_change_m':change,'ground_min_m':min(foot_min),'ground_max_m':max(foot_min),'samples':length+1}
    actor.animation_data.action = None
    for action in actions:
        track = actor.animation_data.nla_tracks.new()
        track.name = action.name
        track.strips.new(action.name,0,action)
        track.mute = True
    for img in bpy.data.images:
        if img.source=='FILE' and not img.packed_file:
            img.pack()
    scene.frame_start,scene.frame_end = 0,48
    scene.frame_set(0)
    bpy.ops.object.select_all(action='SELECT')
    blend = out/'角色主工程.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend),compress=True)
    glb = out/'运行角色.glb'
    bpy.ops.export_scene.gltf(filepath=str(glb),export_format='GLB',use_selection=True,export_animations=True,export_animation_mode='NLA_TRACKS',export_force_sampling=True,export_nla_strips=True,export_extras=True,export_cameras=False,export_lights=False)
    mesh.data.calc_loop_triangles()
    record = {'schema':1,'index':a.index,'id':f'model:zombie-{a.index:02}','source_sha256':SOURCE_SHA,'source_mesh_index':a.index,'source_mesh_name':str(a.index),'source_node':f'Object_{7+2*a.index}','source_materials':[m.name for m in mesh.data.materials],'source_shape_max_delta':shape_error,'weighted_bones':sorted(weighted),'bone_names':[b.name for b in actor.data.bones],'bone_mapping':canonical,'bones':len(actor.data.bones),'vertices':len(mesh.data.vertices),'triangles':len(mesh.data.loop_triangles),'materials':len(mesh.data.materials),'split_validation':{'passed':True,'max_error_m':max_error,'tolerance_m':1e-4,'samples':math.ceil(last)+1,'frame_end':last,'fps':24,'key_samples':samples},'checks':checks,'scale':{'common_source_to_m':common_scale,'rest_height_m':(high.z-low.z)*common_scale,'forward':'+Z','up':'+Y','unit':'米','origin':'静置脚底中心'},'actions_source':'本项目自制待机和移动循环；原始动作保留于工程，未作为运行循环导出','source_action':source_action.name,'blend':{'path':str(blend),'sha256':sha(blend),'bytes':blend.stat().st_size},'asset':{'path':str(glb),'sha256':sha(glb),'bytes':glb.stat().st_size},'source_unchanged':sha(source)==SOURCE_SHA,'blender':bpy.app.version_string,'human_acceptance':'待工程师验收'}
    save(out/'制作回执.json',record)
    print(json.dumps({k:record[k] for k in ['index','bones','vertices','triangles','split_validation','checks']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
