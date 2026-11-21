"""由独立Blender后台进程读取当前主工程，不保存或修改工程。"""
import bpy,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2];HOME=ROOT/'local-data/资源库';sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import current,read,restore
index=current()[3];masters=read(index/'主工程.json');results=[]
originals=read(index/'原件映射.json')
for key,row in originals.items():
 if row['category']=='源工程':masters['original:'+key]={'path':str(restore(key)),'sha256':key}
for identity,r in masters.items():
 if not r['path'].endswith('.blend'):continue
 p=ROOT/r['path'];bpy.ops.wm.open_mainfile(filepath=str(p),load_ui=False);images=[]
 for image in bpy.data.images:
  if image.source not in ['FILE','TILED']:continue
  source=pathlib.Path(bpy.path.abspath(image.filepath));images.append({'name':image.name,'packed':bool(image.packed_file or image.packed_files),'exists':source.is_file(),'source':str(source)})
 external=[i for i in images if not i['packed']]
 results.append({'id':identity,'path':r['path'],'sha256':r['sha256'],'objects':len(bpy.data.objects),'armatures':len(bpy.data.armatures),'actions':len(bpy.data.actions),'images':images,'external_images':external,'passed':not external});print('主工程',len(results),identity,'外部图像',len(external),flush=True)
(HOME/'验收/主工程回读.json').write_text(json.dumps({'count':len(results),'passed':all(r['passed'] for r in results),'models':results},ensure_ascii=False,indent=2),encoding='utf8')
