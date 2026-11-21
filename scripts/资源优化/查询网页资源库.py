"""按正式稳定ID或源摘要查询网页派生，不改变正式库查询语义。"""
import argparse,json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版'
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--id');p.add_argument('--hash');p.add_argument('--query',default='');p.add_argument('--game',action='store_true');p.add_argument('--resolution',type=int,choices=[256,512],default=256);p.add_argument('--level',type=int,choices=[0,1,2],default=1);a=p.parse_args();results=[]
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 统一资源 import available
 if available():
  from 查询资源 import query_assets
  print(json.dumps(query_assets(a.id,a.hash,a.query,a.resolution,a.level),ensure_ascii=False,indent=2));raise SystemExit(0)
 inventory=json.loads((OUT/'盘点.json').read_text(encoding='utf8'))
 for file in sorted((OUT/'验证').glob('*.json')):
  if not file.name.endswith(('.model.json','.audio.json')):continue
  r=json.loads(file.read_text(encoding='utf8'));aliases=[x for x in inventory['files'] if x['sha256']==r['source_sha256']];ids=sorted({f['id'] for x in aliases for f in x.get('formal',[])})
  if a.id and a.id not in ids:continue
  if a.hash and r['source_sha256']!=a.hash:continue
  if a.query and not any(a.query.casefold() in text.casefold() for text in ids+[x['path'] for x in aliases]):continue
  results.append({'ids':ids,'source_sha256':r['source_sha256'],'kind':r['kind'],'technical_passed':r['passed'],'runtime_path':str(ROOT/r['path']) if r.get('path') else None,'bytes':r.get('bytes'),'sha256':r.get('sha256'),'source_paths':[str(ROOT/x['path']) for x in aliases],'quality_statuses':sorted({str(f.get('quality_status')) for x in aliases for f in x.get('formal',[])})})
  if a.game and r['kind']=='model':
   game=ROOT/'local-data/网页资源库/游戏版第一版';receipt=game/'验证'/(r['source_sha256']+'.json')
   if receipt.exists():
    record=json.loads(receipt.read_text('utf8'));v=next((v for v in record['variants'] if v['resolution']==a.resolution and v['level']==(0 if v['animated'] else a.level)),None)
    if v:
     identifier=f"{r['source_sha256']}.{v['resolution']}.lod{v['level']}";checks={}
     for browser in ['chrome','msedge']:
      pth=game/'浏览器验证'/browser/(identifier+'.json');test=json.loads(pth.read_text('utf8')) if pth.exists() else {};checks[browser]=test.get('sha256')==v['sha256'] and test.get('technical_passed',False) and test.get('visual_threshold_passed',False)
     results[-1].update(runtime_path=str(ROOT/v['path']),bytes=v['bytes'],sha256=v['sha256'],game_resolution=v['resolution'],geometry_level=v['level'],triangles=v['triangles'],browser_checks=checks)
 print(json.dumps({'count':len(results),'results':results},ensure_ascii=False,indent=2))
