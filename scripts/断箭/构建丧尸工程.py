"""在独立Blender进程中构建原始三档网格、可编辑蒙皮及原生采样动作。"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
C = Matrix(((1,0,0,0),(0,0,1,0),(0,1,0,0),(0,0,0,1)))


def matrix(values):
    t, q, s = values[:3], values[3:7], values[7:10]
    return Matrix.LocRotScale(Vector(t), Quaternion((q[3],q[0],q[1],q[2])), Vector(s))


def source_matrix(node):
    t=node['transform']
    return matrix([t['m_LocalPosition'][k] for k in 'xyz']+[t['m_LocalRotation'][k] for k in 'xyzw']+[t['m_LocalScale'][k] for k in 'xyz'])


def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def material(root, source):
    raw=source['raw']; mat=bpy.data.materials.new(raw['m_Name']); mat.use_nodes=True
    mat['源着色器']=source['shader_name']; mat['转换说明']='HDRP Lit转Principled；源贴图及原始参数完整保留，运行时特效单独核验'
    tree=mat.node_tree; bsdf=tree.nodes.get('Principled BSDF'); textures={}
    floats=dict(raw['m_SavedProperties']['m_Floats']); colors=dict(raw['m_SavedProperties']['m_Colors'])
    for name, info in source['textures'].items():
        if name=='_MainTex': continue
        image=bpy.data.images.load(str(root/info.get('decoded_file',info['file'])),check_existing=True)
        image.colorspace_settings.name='sRGB' if name=='_BaseColorMap' else 'Non-Color'; image.pack()
        node=tree.nodes.new('ShaderNodeTexImage'); node.image=image; node.label=name; node.name=name; textures[name]=node
    # 原始打包法线另行保留，便于编辑及通道审计。
    info=source['textures']['_NormalMap']; image=bpy.data.images.load(str(root/info['file']),check_existing=True)
    image.colorspace_settings.name='Non-Color'; image.pack()
    original=tree.nodes.new('ShaderNodeTexImage'); original.image=image; original.label='原始Unity打包法线，仅留档'
    tree.links.new(textures['_BaseColorMap'].outputs['Color'],bsdf.inputs['Base Color'])
    mask=textures.get('_MaskMap')
    if mask:
        sep=tree.nodes.new('ShaderNodeSeparateColor');tree.links.new(mask.outputs['Color'],sep.inputs['Color'])
        tree.links.new(sep.outputs['Red'],bsdf.inputs['Metallic'])
        rough=tree.nodes.new('ShaderNodeMath');rough.operation='SUBTRACT';rough.inputs[0].default_value=1
        tree.links.new(mask.outputs['Alpha'],rough.inputs[1]);tree.links.new(rough.outputs[0],bsdf.inputs['Roughness'])
        sep.label='R金属度、G环境遮蔽、B细节遮罩；A平滑度'
    else:
        bsdf.inputs['Metallic'].default_value=floats.get('_Metallic',0)
        bsdf.inputs['Roughness'].default_value=1-floats.get('_Smoothness',.5)
    normal=tree.nodes.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value=floats.get('_NormalScale',1)
    tree.links.new(textures['_NormalMap'].outputs['Color'],normal.inputs['Color']);tree.links.new(normal.outputs['Normal'],bsdf.inputs['Normal'])
    if floats.get('_AlphaCutoffEnable',0):
        cutoff=tree.nodes.new('ShaderNodeMath');cutoff.operation='GREATER_THAN';cutoff.inputs[1].default_value=floats.get('_AlphaCutoff',.5)
        tree.links.new(textures['_BaseColorMap'].outputs['Alpha'],cutoff.inputs[0]);tree.links.new(cutoff.outputs[0],bsdf.inputs['Alpha'])
    return mat


def build(batch,role,revision):
    source=batch/'角色'/role; target=batch/'工程'/f'{role}-原生骨骼与动作-{revision}.blend'
    if target.exists() or target.with_suffix('.构建记录.json').exists(): raise FileExistsError('工程已存在')
    data=json.loads((source/'角色数据.json').read_text('utf-8'))
    native=next(r for r in json.loads((batch/'原生动作采样-r04.json').read_text('utf-8'))['roles'] if r['name']==role)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1;scene.render.fps=24
    model=bpy.data.collections.new('模型部件');scene.collection.children.link(model)
    arm=bpy.data.objects.new(role+'-原生骨架',bpy.data.armatures.new(role+'-骨架数据'));model.objects.link(arm)
    arm.show_in_front=True;arm.data.display_type='STICK'
    nodes={n['id']:n for n in data['nodes']}; worlds={}; paths={}
    for n in data['nodes']:
        worlds[n['id']]=(worlds[n['parent']] if n['parent'] else Matrix.Identity(4))@source_matrix(n)
        paths[n['id']]=(paths[n['parent']]+'/' if paths[n['parent']] else '')+n['name'] if n['parent'] else ''
    renderer_ids={m['node_id'] for m in data['meshes']}
    bone_nodes=[n for n in data['nodes'] if n['id'] not in renderer_ids]
    names={};seen=set()
    for n in bone_nodes:
        name=n['name'] if n['name'] not in seen else n['name']+'-根节点'
        names[n['id']]=name;seen.add(name)
    bpy.context.view_layer.objects.active=arm;arm.select_set(True);bpy.ops.object.mode_set(mode='EDIT')
    for n in bone_nodes:
        bone=arm.data.edit_bones.new(names[n['id']]);bone.length=.07;bone.matrix=C@worlds[n['id']]@C
        if n['parent']:bone.parent=arm.data.edit_bones[names[n['parent']]]
    bpy.ops.object.mode_set(mode='OBJECT')
    for n in bone_nodes:
        arm.data.bones[names[n['id']]]['源节点ID']=n['id'];arm.data.bones[names[n['id']]]['源层级路径']=paths[n['id']]
        arm.pose.bones[names[n['id']]].rotation_mode='QUATERNION'
    mats={k:material(source,v) for k,v in data['materials'].items()}
    for item in data['meshes']:
        arrays=np.load(source/(item['name']+'.npz'));verts=arrays['vertices'][:,[0,2,1]];tris=arrays['triangles'][:,[0,2,1]]
        mesh=bpy.data.meshes.new(role+'-'+item['name']);mesh.from_pydata(verts,[],tris);mesh.update()
        uv=mesh.uv_layers.new(name='UVMap');loop_indices=np.array([l.vertex_index for l in mesh.loops]);uv.data.foreach_set('uv',arrays['uv0'][loop_indices].ravel())
        for polygon in mesh.polygons:polygon.use_smooth=True
        mesh.normals_split_custom_set_from_vertices(arrays['normals'][:,[0,2,1]])
        # Blender切线空间自定义法线有存储量化；同时逐值保存原始浮点属性。
        original_normals=mesh.attributes.new('源Unity法线','FLOAT_VECTOR','POINT');original_normals.data.foreach_set('vector',arrays['normals'].ravel())
        tangent_file=batch/'来源原始对象'/f'切线-{item["mesh_id"]}.npz'
        tangents=arrays['tangents'] if 'tangents' in arrays else np.load(tangent_file)['tangents'] if tangent_file.exists() else None
        if tangents is not None and len(tangents):
            original_tangents=mesh.attributes.new('源Unity切线','FLOAT_COLOR','POINT');original_tangents.data.foreach_set('color',tangents.ravel())
        for k in item['materials']:mesh.materials.append(mats[k])
        obj=bpy.data.objects.new(role+'-'+item['name'],mesh);lod=bpy.data.collections.new(item['name'].upper());model.children.link(lod);lod.objects.link(obj)
        lod.hide_viewport=item['name']!='lod_0';lod.hide_render=item['name']!='lod_0'
        obj.matrix_world=C@worlds[item['node_id']]@C;obj['源网格ID']=item['mesh_id'];obj['源渲染器ID']=item['renderer_id'];obj['源节点ID']=item['node_id']
        groups=[obj.vertex_groups.new(name=names[bid]) for bid in item['bone_ids']]
        for vertex,(indices,weights) in enumerate(zip(arrays['indices'],arrays['weights'])):
            for index,weight in zip(indices,weights):
                if weight>0:groups[int(index)].add([vertex],float(weight),'ADD')
        modifier=obj.modifiers.new('原生蒙皮','ARMATURE');modifier.object=arm;modifier.use_deform_preserve_volume=False
    arm.animation_data_create();actions={};frame_stats=[]
    native_index={path:i for i,path in enumerate(native['paths'])}
    for clip in native['clips']:
        action=bpy.data.actions.new(clip['name']);action.use_fake_user=True
        slot=action.slots.new(id_type='OBJECT',name=arm.name);layer=action.layers.new('原生采样');strip=layer.strips.new(type='KEYFRAME');bag=strip.channelbag(slot,ensure=True)
        arm.animation_data.action=action;arm.animation_data.action_slot=slot
        frames=np.array([1+f['time']*scene.render.fps for f in clip['frames']]);channels={n['id']:[] for n in bone_nodes}; previous={}
        for frame in clip['frames']:
            for n in bone_nodes:
                offset=native_index[paths[n['id']]]*10;local=C@matrix(frame['trs'][offset:offset+10])@C
                rest=arm.data.bones[names[n['id']]].matrix_local
                basis=rest.inverted()@(arm.data.bones[names[n['parent']]].matrix_local@local if n['parent'] else local)
                t,q,s=basis.decompose()
                if n['id'] in previous and q.dot(previous[n['id']])<0:q.negate()
                previous[n['id']]=q.copy();channels[n['id']].append(list(t)+list(q)+list(s))
        for n in bone_nodes:
            values=np.array(channels[n['id']]);pb=arm.pose.bones[names[n['id']]]
            for prop,start,count in [('location',0,3),('rotation_quaternion',3,4),('scale',7,3)]:
                for index in range(count):
                    curve=bag.fcurves.new(data_path=pb.path_from_id(prop),index=index);curve.keyframe_points.add(len(frames))
                    curve.keyframe_points.foreach_set('co',np.column_stack([frames,values[:,start+index]]).ravel())
                    for point in curve.keyframe_points:point.interpolation='LINEAR'
        action['源动作']=clip['name'];action['原生采样频率']=clip['sampleRate'];action['源时长秒']=clip['duration'];action['循环']=clip['looping'];actions[clip['name']]=action
        frame_stats.append({'name':clip['name'],'frames':len(frames),'curves':len(bag.fcurves),'duration':clip['duration']})
        print(role,clip['name'],'动作已建立',flush=True)
    default='ZombieRunning' if role=='Walker' else 'ZombieSprint';arm.animation_data.action=actions[default];arm.animation_data.action_slot=actions[default].slots[0]
    scene.frame_start=1;scene.frame_end=int(math.ceil(actions[default].frame_range[1]));scene.frame_set(1)
    arm['来源']=data['source'];arm['骨引用数量']=len(data['meshes'][0]['bone_ids']);arm['动作列表']=json.dumps(list(actions),ensure_ascii=False)
    scene['原生动画说明']='源AnimationClip经原Avatar、Unity 6000.4.9f1 Playables采样；保留源24或30Hz关键时刻，Blender帧率24；未重建原游戏状态机与VFX'
    scene['材质说明']='1024原始贴图已打包，HDRP Lit转Principled近似；原材质参数与原始压缩动画在批次内留档；原始浮点法线和切线逐值保存在网格属性中'
    text=bpy.data.texts.new('使用说明');text.write('选中骨架，在动作编辑器的动作下拉框选择9段原生动作。默认显示LOD0；在集合中切换LOD1、LOD2，避免重复叠显。\n'+scene['原生动画说明']+'\n'+scene['材质说明'])
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                area.spaces.active.region_3d.view_distance=3.4;area.spaces.active.region_3d.view_location=Vector((0,0,1));area.spaces.active.shading.type='MATERIAL'
    target.parent.mkdir(parents=True,exist_ok=True);bpy.ops.wm.save_as_mainfile(filepath=str(target),check_existing=False)
    report={'role':role,'blender':bpy.app.version_string,'source_sha256':sha(source/'角色数据.json'),'native_sha256':sha(batch/'原生动作采样-r04.json'),
            'output':str(target),'sha256':sha(target),'bones_with_helpers':len(bone_nodes),'skin_bone_references':len(data['meshes'][0]['bone_ids']),
            'nodes':len(nodes),'actions':frame_stats,'packed_images':len([i for i in bpy.data.images if i.packed_file])}
    with target.with_suffix('.构建记录.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',required=True);parser.add_argument('--role',choices=['Walker','Runner'],required=True);parser.add_argument('--revision',default='r01')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);batch=Path(args.batch).resolve()
    if batch.parent!=(ROOT/'local-data/断箭/批次').resolve():raise ValueError('批次越界')
    build(batch,args.role,args.revision)
