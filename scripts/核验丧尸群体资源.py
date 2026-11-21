"""在全新Blender进程中重新导入性能实验GLB，检查导出后的实际蒙皮和循环。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import bpy

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.output.exists():raise ValueError('不覆盖旧核验回执')
    record=json.loads((DATA/'资源清单.json').read_text('utf-8'));asset=DATA/record['asset']['file']
    assert hashlib.sha256(asset.read_bytes()).hexdigest()==record['asset']['sha256']
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene;scene.render.fps=24
    bpy.ops.import_scene.gltf(filepath=str(asset))
    mesh=next(o for o in scene.objects if o.type=='MESH');rig=next(o for o in scene.objects if o.type=='ARMATURE')
    actions=list(bpy.data.actions);rig.animation_data_clear();rig.animation_data_create()
    def verts():
        graph=bpy.context.evaluated_depsgraph_get();obj=mesh.evaluated_get(graph)
        return [obj.matrix_world@v.co for v in obj.data.vertices]
    checks=[]
    for action in actions:
        rig.animation_data.action=action
        rig.animation_data.action_slot=next(s for s in action.slots if s.target_id_type=='OBJECT')
        first,last=action.frame_range;scene.frame_set(int(first));a=verts()
        scene.frame_set(int(last));b=verts();scene.frame_set(int(first+(last-first)/4));c=verts()
        row={'name':action.name,'seconds':(last-first)/24,
             'loop_error_m':max((x-y).length for x,y in zip(a,b)),
             'pose_change_m':max((x-y).length for x,y in zip(a,c)),
             'min_height_m':min(v.z for v in c),'max_height_m':max(v.z for v in c)}
        assert row['loop_error_m']<.001 and row['pose_change_m']>.005,row
        checks.append(row)
    assert len(actions)==2 and len(rig.data.bones)==23
    result={'blender':bpy.app.version_string,'sha256':record['asset']['sha256'],'bones':len(rig.data.bones),
            'vertices':len(mesh.data.vertices),'checks':checks,'passed':True}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
