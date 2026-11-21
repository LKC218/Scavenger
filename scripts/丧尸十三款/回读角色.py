"""制作进程以外的独立Blender只读回读；工程与双档实际GLB均检查。"""
import argparse
import json
import math
from pathlib import Path
import sys
import bpy

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import sha,save,read,current,record_path


def inspect(path,expected,job):
    if sha(path)!=expected:
        raise ValueError('回读输入摘要不符')
    if path.suffix=='.blend':
        bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
    scene=bpy.context.scene
    rigs=[o for o in scene.objects if o.type=='ARMATURE']
    meshes=[o for o in scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' for m in o.modifiers)]
    assert len(rigs)==len(meshes)==1
    rig,mesh=rigs[0],meshes[0]
    mesh.data.calc_loop_triangles()
    assert len(rig.data.bones)==job['bones'] and len(mesh.data.loop_triangles)==job['triangles']
    assert all(b.parent is None or b.parent.name in rig.data.bones for b in rig.data.bones)
    weight_error=max(abs(sum(g.weight for g in v.groups)-1) for v in mesh.data.vertices)
    assert weight_error<1e-5
    images=[{'name':i.name,'size':list(i.size),'packed':bool(i.packed_file or i.packed_files)} for i in bpy.data.images if i.source=='FILE' and i.users]
    assert images and all(i['packed'] and min(i['size'])>0 for i in images)
    actions=[x for x in bpy.data.actions if x.name.startswith(('待机','移动'))]
    assert len(actions)==2
    ad=rig.animation_data_create()
    for track in ad.nla_tracks:
        track.mute=True
    checks=[]
    for action in actions:
        ad.action=action
        if action.slots:
            ad.action_slot=action.slots[0]
        samples=[]
        for t in [0,.25,.5,.75,1]:
            frame=action.frame_range[0]+t*(action.frame_range[1]-action.frame_range[0])
            scene.frame_set(int(frame),subframe=frame%1)
            obj=mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
            vs=[obj.matrix_world@v.co for v in obj.data.vertices]
            assert all(math.isfinite(c) for v in vs for c in v)
            samples.append(vs)
        delta=max((a-b).length for a,b in zip(samples[0],samples[1]))
        seam=max((a-b).length for a,b in zip(samples[0],samples[-1]))
        assert delta>.005 and seam<1e-4
        checks.append({'name':action.name,'pose_max_change_m':delta,'loop_max_error_m':seam})
    assert sha(path)==expected
    return {'path':str(path),'sha256':expected,'bones':len(rig.data.bones),'triangles':len(mesh.data.loop_triangles),'weight_sum_max_error':weight_error,'images':images,'checks':checks,'passed':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);rows=[]
    pointer,catalog,routes,index=current()
    zero=read(index/'主工程.json')['model:zombie-runtime']
    save(a.batch/'编号零主工程回读.json',inspect(record_path(zero),zero['sha256'],{'bones':23,'triangles':800}))
    for i in range(1,13):
        folder=a.batch/f'角色{i:02}'
        job=read(folder/'制作回执.json');derived=read(folder/'派生回执.json')
        row={'index':i,'id':job['id'],'blend':inspect(Path(job['blend']['path']),job['blend']['sha256'],job),'variants':[]}
        for v in derived['variants']:
            row['variants'].append({'resolution':v['resolution'],**inspect(Path(v['path']),v['sha256'],job)})
        row['passed']=True;rows.append(row)
        save(folder/'回读回执.json',row);print('回读通过',i,flush=True)
    save(a.batch/'Blender回读.json',{'passed':True,'count':len(rows),'roles':rows,'blender':bpy.app.version_string})


if __name__=='__main__':main()
