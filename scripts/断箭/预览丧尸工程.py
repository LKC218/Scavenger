"""只读重开角色工程，生成七视角、原生移动循环及攻击死亡姿态证据。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector


def run(batch,role,revision,preview_revision,poses_only):
    blend=batch/'工程'/f'{role}-原生骨骼与动作-{revision}.blend';target=batch/'预览'/f'{role}-{preview_revision}'
    target.mkdir(parents=True,exist_ok=False);before=hashlib.sha256(blend.read_bytes()).hexdigest();bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=16;scene.cycles.use_denoising=True
    scene.render.threads_mode='FIXED';scene.render.threads=2;scene.render.resolution_x=512;scene.render.resolution_y=512;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
    scene.world=bpy.data.worlds.new('预览环境');scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.22,.22,.22,1);scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.5
    studio=bpy.data.collections.new('摄影棚');scene.collection.children.link(studio)
    cam=bpy.data.objects.new('预览相机',bpy.data.cameras.new('预览相机'));studio.objects.link(cam);cam.data.type='ORTHO';scene.camera=cam
    for name,location,power,size in [('主光',(3,4,5),550,4),('补光',(-3,2,3),300,3),('轮廓光',(1,-4,4),650,3)]:
        light=bpy.data.objects.new(name,bpy.data.lights.new(name,'AREA'));studio.objects.link(light);light.location=location;light.data.energy=power;light.data.shape='DISK';light.data.size=size;light.rotation_euler=(Vector((0,0,1))-light.location).to_track_quat('-Z','Y').to_euler()
    arm=next(o for o in scene.objects if o.type=='ARMATURE');arm.animation_data.action=None
    for bone in arm.pose.bones:bone.matrix_basis.identity()
    bpy.context.view_layer.update()
    def render(name,direction):
        mesh=next(o for o in scene.objects if o.type=='MESH' and o.name.endswith('lod_0'));ev=mesh.evaluated_get(bpy.context.evaluated_depsgraph_get());data=ev.to_mesh()
        coords=np.array([list(mesh.matrix_world@v.co) for v in data.vertices]);ev.to_mesh_clear();low,high=coords.min(axis=0),coords.max(axis=0);center=Vector((low+high)/2)
        cam.location=center+Vector(direction).normalized()*5;cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
        rotation=cam.rotation_euler.to_matrix();projected=(coords-np.asarray(center))@np.asarray(rotation)
        pmin,pmax=projected.min(axis=0),projected.max(axis=0);mid=(pmin+pmax)/2
        cam.location+=rotation@Vector((mid[0],mid[1],0));cam.data.ortho_scale=max(float(max(pmax[:2]-pmin[:2]))*1.15,1.8)
        scene.render.filepath=str(target/(name+'.png'));bpy.ops.render.render(write_still=True);print(role,name,flush=True)
    views=[('正面',(0,5,0)),('背面',(0,-5,0)),('左面',(-5,0,0)),('右面',(5,0,0)),('顶面',(0,0,5)),('底面',(0,0,-5)),('斜视',(3,5,2))]
    if not poses_only:
        for name,direction in views:render('绑定姿态-'+name,direction)
    default='ZombieRunning' if role=='Walker' else 'ZombieSprint';action=bpy.data.actions[default];arm.animation_data.action=action;arm.animation_data.action_slot=action.slots[0]
    length=float(action['源时长秒'])
    if not poses_only:
        for index in range(8):
            frame=1+index*length*scene.render.fps/8;scene.frame_set(int(frame),subframe=frame-int(frame));render(f'原生移动-{index:02}',(3,5,1.6))
    for name,ratio in [('ZombieAttack',.5),('ZombieDying',.98)]:
        action=bpy.data.actions[name];arm.animation_data.action=action;arm.animation_data.action_slot=action.slots[0];frame=1+float(action['源时长秒'])*scene.render.fps*ratio;scene.frame_set(int(frame),subframe=frame-int(frame));render(name,(3,5,1.6))
    record={'role':role,'views':0 if poses_only else 7,'motion_frames':0 if poses_only else 8,'pose_checks':['ZombieAttack','ZombieDying'],'source_unchanged':before==hashlib.sha256(blend.read_bytes()).hexdigest()}
    with (target/'预览记录.json').open('x',encoding='utf-8') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--role',choices=['Walker','Runner'],required=True);parser.add_argument('--revision',default='r02')
    parser.add_argument('--preview-revision',default='r02');parser.add_argument('--poses-only',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);run(args.batch.resolve(),args.role,args.revision,args.preview_revision,args.poses_only)
