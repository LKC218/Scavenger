"""只读打开原作主工程，派生单LOD2、原骨架及九段原生动作的独立运行版。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy

ROOT=Path(__file__).resolve().parents[2]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',required=True,choices=['Walker','Runner']);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);role=args.role;output=args.output.resolve()
    if output.parent!=(ROOT/'local-data/断箭/批次/BA-26273833-014').resolve():raise ValueError('输出不在本轮角色目录')
    output.mkdir(exist_ok=False,parents=True)
    source=ROOT/f'local-data/断箭/批次/BA-26273833-013/工程/{role}-原生骨骼与动作-r03.blend';before=sha(source)
    bpy.ops.wm.open_mainfile(filepath=str(source));scene=bpy.context.scene
    arm=next(o for o in scene.objects if o.type=='ARMATURE');mesh=next(o for o in scene.objects if o.type=='MESH' and o.name.endswith('lod_2'))
    for obj in list(scene.objects):
        if obj not in (arm,mesh):bpy.data.objects.remove(obj,do_unlink=True)
    for c in bpy.data.collections:c.hide_viewport=False;c.hide_render=False
    for image in bpy.data.images:
        if image.source=='FILE':
            image.scale(256,256);image.pack()
    # GLB标准坐标转换后再绕竖轴转半圈，使运行前向为+Z。
    arm.rotation_euler.z=3.141592653589793;mesh.rotation_euler.z=3.141592653589793
    arm.animation_data.action=None
    for bone in arm.pose.bones:bone.matrix_basis.identity()
    bpy.ops.object.select_all(action='DESELECT');arm.select_set(True);mesh.select_set(True);bpy.context.view_layer.objects.active=arm
    glb=output/f'{role}-LOD2-256.glb'
    bpy.ops.export_scene.gltf(filepath=str(glb),export_format='GLB',use_selection=True,export_animations=True,
        export_animation_mode='ACTIONS',export_frame_range=False,export_force_sampling=True,export_anim_single_armature=True,
        export_optimize_animation_size=True,export_skins=True,export_all_influences=False,export_cameras=False,export_lights=False,export_extras=True)
    default='ZombieRunning' if role=='Walker' else 'ZombieSprint';arm.animation_data.action=bpy.data.actions[default];arm.animation_data.action_slot=bpy.data.actions[default].slots[0]
    scene.frame_set(1);scene['运行派生说明']='单LOD2、256贴图、原46骨架节点及9原生动作；来源工程只读；GLB运行前向+Z'
    blend=output/f'{role}-LOD2-运行版.blend';bpy.ops.wm.save_as_mainfile(filepath=str(blend),check_existing=False)
    assert before==sha(source),'原工程摘要变化'
    row={'role':role,'source':source.relative_to(ROOT).as_posix(),'source_sha256':before,'source_unchanged':True,
         'blend':{'path':str(blend),'bytes':blend.stat().st_size,'sha256':sha(blend)},'glb':{'path':str(glb),'bytes':glb.stat().st_size,'sha256':sha(glb)},
         'bones':len(arm.data.bones),'triangles':len(mesh.data.polygons),'vertices':len(mesh.data.vertices),
         'animations':[{'name':a.name,'duration_s':a['源时长秒']} for a in bpy.data.actions if '源动作' in a]}
    with (output/'派生记录.json').open('x',encoding='utf-8') as stream:json.dump(row,stream,ensure_ascii=False,indent=2)
    print(json.dumps(row,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
