"""在独立Blender进程中回读未压缩GLB，并对照派生工程的实际三角形顶点。"""
import argparse,hashlib,json,sys,math
from pathlib import Path
import bpy
from mathutils.kdtree import KDTree

ROOT=Path(__file__).resolve().parents[2]
def points(obj):
    ev=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=ev.to_mesh();indices={i for p in mesh.polygons for i in p.vertices};p=[obj.matrix_world@mesh.vertices[i].co for i in indices];ev.to_mesh_clear();return p
def at(seconds):
    f=1+seconds*bpy.context.scene.render.fps;bpy.context.scene.frame_set(int(f),subframe=f-int(f))
def run(role):
    directory=ROOT/'local-data/断箭/批次/BA-26273833-014'/role;source=directory/f'{role}-LOD2-运行版.blend';sha=hashlib.sha256(source.read_bytes()).hexdigest();bpy.ops.wm.open_mainfile(filepath=str(source))
    arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE');obj=next(o for o in bpy.context.scene.objects if o.type=='MESH');expected={}
    for action in list(bpy.data.actions):
        if '源动作' not in action:continue
        arm.animation_data.action=action;arm.animation_data.action_slot=action.slots[0]
        duration=float(action['源时长秒']);expected[action.name]=[(t,points_after(arm,obj,action,t)) for t in [0,duration*.5,duration*.97]]
    bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=str(directory/f'{role}-LOD2-256.glb'));scene=bpy.context.scene;scene.render.fps=24
    arm=next(o for o in scene.objects if o.type=='ARMATURE');obj=next(o for o in scene.objects if o.type=='MESH');rows=[]
    for name,frames in expected.items():
        action=bpy.data.actions.get(name)
        if not action:raise AssertionError('回读动作缺失：'+name)
        arm.animation_data.action=action;arm.animation_data.action_slot=action.slots[0]
        for seconds,reference in frames:
            at(seconds);actual=points(obj);tree=KDTree(len(reference))
            for i,p in enumerate(reference):tree.insert(p,i)
            tree.balance();maximum=max(tree.find(p)[2] for p in actual);rows.append({'clip':name,'seconds':seconds,'max_surface_vertex_distance_m':maximum})
    report={'role':role,'bones':len(arm.data.bones),'triangles':len(obj.data.polygons),'clips':len(expected),'checks':rows,'source_unchanged':sha==hashlib.sha256(source.read_bytes()).hexdigest(),'maximum':max(r['max_surface_vertex_distance_m'] for r in rows)}
    report['passed']=report['bones']==46 and report['clips']==9 and report['maximum']<.02 and report['source_unchanged']
    with (directory/'GLB独立回读.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    assert report['passed'],report;print(json.dumps({k:report[k] for k in ['role','bones','triangles','clips','maximum','passed']},ensure_ascii=False))
def points_after(arm,obj,action,seconds):
    at(seconds);return points(obj)
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--role',choices=['Walker','Runner'],required=True);a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);run(a.role)
