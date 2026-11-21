"""当前资源查询；原始内容只给归档位置，不自动批量解压。"""
import argparse,json
from 统一资源 import current,read,ROOT
def query_assets(identity=None,digest=None,term='',resolution=256,level=1):
 pointer,catalog,_,index=current();registry=read(index/'模型登记.json');originals=read(index/'原件映射.json');results=[]
 for key,row in registry.items():
  if identity and identity not in [row['id'],key]:continue
  if digest and digest not in [key,row.get('sha256'),*[v['sha256'] for v in row.get('variants',[])]]:continue
  if term and term.casefold() not in json.dumps([row['id'],row['name']],ensure_ascii=False).casefold():continue
  if row['kind']=='model':
   v=next((v for v in row['variants'] if v['resolution']==resolution and v['level']==(0 if v['animated'] else level)),None)
   if not v:continue
   record={**v,'runtime_path':str(ROOT/v['path'])}
  else:record={**row,'runtime_path':str(ROOT/row['path'])}
  source_sha=row.get('source_sha256',key)
  results.append({'id':row['id'],'kind':row['kind'],'name':row['name'],'source_sha256':source_sha,'current':record,'original_archive':originals.get(source_sha)})
 for scene in catalog.get('scenes',[]):
  if identity and identity!=scene['id']:continue
  if digest:continue
  if term and term.casefold() not in json.dumps([scene['id'],scene['name'],scene.get('tags',[])],ensure_ascii=False).casefold():continue
  details=read(index/'场景登记.json')[scene['id']]
  result={**scene,'runtime_path':str(ROOT/details['retained_files'][details['files']['manifest']]['path']),
          'version':details['version'],'bounds':details['bounds'],'asset_ids':details['asset_ids'],'human_acceptance':details['human_acceptance']}
  results.append({'id':scene['id'],'kind':'scene','name':scene['name'],'current':result,'original_archive':None})
 return {'count':len(results),'asset_library_revision':1,'revision':pointer['revision'],'results':results}
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--id');p.add_argument('--hash');p.add_argument('--query',default='');p.add_argument('--resolution',type=int,choices=[256,512],default=256);p.add_argument('--level',type=int,choices=[0,1,2],default=1);a=p.parse_args();print(json.dumps(query_assets(a.id,a.hash,a.query,a.resolution,a.level),ensure_ascii=False,indent=2))
