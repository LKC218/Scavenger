"""独立重开工程，对源几何、权重、全帧骨矩阵及Unity蒙皮快照进行数值回读。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from 构建丧尸工程 import C, matrix, source_matrix


def max_error(a,b):return float(np.max(np.abs(np.asarray(a)-np.asarray(b))))


def run(batch,role,revision,report_revision):
    source=batch/'角色'/role; blend=batch/'工程'/f'{role}-原生骨骼与动作-{revision}.blend';out=batch/'核验'/f'{role}-{report_revision or revision}.json'
    if out.exists():raise FileExistsError('核验回执已存在')
    before=hashlib.sha256(blend.read_bytes()).hexdigest();bpy.ops.wm.open_mainfile(filepath=str(blend))
    data=json.loads((source/'角色数据.json').read_text('utf-8')); native=next(r for r in json.loads((batch/'原生动作采样-r04.json').read_text('utf-8'))['roles'] if r['name']==role)
    arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE');report={'role':role,'blender':bpy.app.version_string,'source_meshes':[],'actions':[],'failures':[]}
    mesh_objects={o['源节点ID']:o for o in bpy.context.scene.objects if o.type=='MESH'}
    bone_map={b['源节点ID']:b.name for b in arm.data.bones}; nodes={n['id']:n for n in data['nodes']};paths={}
    for n in data['nodes']:paths[n['id']]=(paths[n['parent']]+'/' if paths[n['parent']] else '')+n['name'] if n['parent'] else ''
    by_path={p:i for i,p in enumerate(native['paths'])};renderer_ids=set(mesh_objects)
    rest={};rest_error=0
    for n in data['nodes']:
        rest[n['id']]=(rest[n['parent']] if n['parent'] else Matrix.Identity(4))@source_matrix(n)
        if n['id'] in bone_map:rest_error=max(rest_error,max_error(arm.data.bones[bone_map[n['id']]].matrix_local,C@rest[n['id']]@C))
    report['rest_bone_matrix_max_error']=rest_error
    if rest_error>1e-5:report['failures'].append('骨架绑定矩阵不符')
    for item in data['meshes']:
        arr=np.load(source/(item['name']+'.npz'));obj=mesh_objects[item['node_id']];mesh=obj.data
        positions=np.empty((len(mesh.vertices),3),dtype=np.float32);mesh.vertices.foreach_get('co',positions.ravel())
        vertex_error=max_error(positions,arr['vertices'][:,[0,2,1]])
        triangles=np.array([list(p.vertices) for p in mesh.polygons]);topology=bool(np.array_equal(triangles,arr['triangles'][:,[0,2,1]]))
        loop_indices=np.array([l.vertex_index for l in mesh.loops]);uv=np.empty((len(mesh.loops),2),dtype=np.float32);mesh.uv_layers[0].data.foreach_get('uv',uv.ravel());uv_error=max_error(uv,arr['uv0'][loop_indices])
        normals=np.empty((len(mesh.loops),3),dtype=np.float32);mesh.corner_normals.foreach_get('vector',normals.ravel())
        expected_normals=arr['normals'][loop_indices][:,[0,2,1]];expected_normals/=np.linalg.norm(expected_normals,axis=1)[:,None]
        normal_error=max_error(normals,expected_normals)
        source_normals=np.empty_like(arr['normals']);mesh.attributes['源Unity法线'].data.foreach_get('vector',source_normals.ravel())
        raw_normal_error=max_error(source_normals,arr['normals'])
        tangent_path=batch/'来源原始对象'/f'切线-{item["mesh_id"]}.npz';source_tangents=arr['tangents'] if 'tangents' in arr else np.load(tangent_path)['tangents'];stored_tangents=np.empty_like(source_tangents)
        mesh.attributes['源Unity切线'].data.foreach_get('color',stored_tangents.ravel());raw_tangent_error=max_error(stored_tangents,source_tangents)
        actual=np.zeros_like(arr['weights']);error=0
        for i,(indices,weights) in enumerate(zip(arr['indices'],arr['weights'])):
            expected={}
            for idx,w in zip(indices,weights):
                if w>0:expected[bone_map[item['bone_ids'][int(idx)]]]=expected.get(bone_map[item['bone_ids'][int(idx)]],0)+float(w)
            got={obj.vertex_groups[g.group].name:g.weight for g in mesh.vertices[i].groups}
            if set(expected)!=set(got):raise AssertionError('蒙皮影响骨集合变化')
            error=max(error,max((abs(v-got[k]) for k,v in expected.items()),default=0))
        angle=float(np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i',normals,expected_normals),-1,1))).max())
        row={'lod':item['name'],'vertices':len(mesh.vertices),'triangles':len(mesh.polygons),'position_max_error_m':vertex_error,'topology_equal':topology,'uv_max_error':uv_error,'weights_max_error':error,'normal_max_component_error':normal_error,'shading_normal_max_angle_deg':angle,'raw_normal_error':raw_normal_error,'raw_tangent_error':raw_tangent_error}
        report['source_meshes'].append(row)
        if vertex_error>1e-6 or not topology or uv_error>1e-6 or error>1e-6 or raw_normal_error>1e-6 or raw_tangent_error>1e-6:report['failures'].append('源网格回读不符：'+item['name'])
    report['shading_normal_note']='Blender切线空间法线存储有量化误差，单独报告角度；原浮点法线、切线在网格属性中逐值保留并严格核对，不把着色法线称为逐值无损'
    report['packed_images']=[]
    for image in bpy.data.images:
        if image.source!='FILE':continue
        packed=image.packed_file
        original=Path(bpy.path.abspath(image.filepath))
        same=bool(packed) and hashlib.sha256(packed.data).hexdigest()==hashlib.sha256(original.read_bytes()).hexdigest()
        report['packed_images'].append({'name':image.name,'size':list(image.size),'packed':bool(packed),'source_bytes_equal':same})
        if not same:report['failures'].append('贴图打包不完整：'+image.name)
    scene=bpy.context.scene
    for clip in native['clips']:
        action=bpy.data.actions.get(clip['name'])
        if action is None:raise AssertionError('动作缺失')
        arm.animation_data.action=action;arm.animation_data.action_slot=action.slots[0];maximum=0;skin_max=0;world_cache=[]
        for frame in clip['frames']:
            at=1+frame['time']*scene.render.fps;scene.frame_set(int(math.floor(at)),subframe=at-math.floor(at))
            world={}
            for n in data['nodes']:
                k=by_path[paths[n['id']]]*10;local=matrix(frame['trs'][k:k+10]);world[n['id']]=(world[n['parent']] if n['parent'] else Matrix.Identity(4))@local
                if n['id'] in bone_map:maximum=max(maximum,max_error(arm.pose.bones[bone_map[n['id']]].matrix,C@world[n['id']]@C))
        checks=[]
        for check in clip['checks']:
            frame=clip['frames'][check['frame']];at=1+frame['time']*scene.render.fps;scene.frame_set(int(math.floor(at)),subframe=at-math.floor(at))
            obj=next(o for o in mesh_objects.values() if o.name.endswith('lod_0'));evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
            v=np.array([list(v.co) for v in mesh.vertices]);expected=np.asarray(check['vertices']).reshape(-1,3)[:,[0,2,1]]
            diff=np.linalg.norm(v-expected,axis=1);err=float(diff.max());checks.append({'source_frame':check['frame'],'max_vertex_error_m':err,'mean_vertex_error_m':float(diff.mean())});skin_max=max(skin_max,err);evaluated.to_mesh_clear()
        report['actions'].append({'name':clip['name'],'frames':len(clip['frames']),'duration':clip['duration'],'all_frame_bone_matrix_max_error':maximum,'unity_skin_max_error_m':skin_max,'skin_checks':checks})
        if maximum>2e-4 or skin_max>2e-4:report['failures'].append('动作回读偏差：'+clip['name'])
        print(role,clip['name'],'骨矩阵',maximum,'蒙皮',skin_max,flush=True)
    report['source_unchanged']=before==hashlib.sha256(blend.read_bytes()).hexdigest();report['bones_with_helpers']=len(arm.data.bones);report['action_count']=len(native['clips'])
    report['status']='passed' if not report['failures'] and report['source_unchanged'] else 'failed'
    out.parent.mkdir(exist_ok=True)
    with out.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    if report['status']!='passed':raise AssertionError(report['failures'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--role',required=True,choices=['Walker','Runner']);parser.add_argument('--revision',default='r01')
    parser.add_argument('--report-revision')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);run(args.batch.resolve(),args.role,args.revision,args.report_revision)
