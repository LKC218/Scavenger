"""独立游戏资源浏览与冻结靶场；不占用或重启旧服务。"""
import argparse,hashlib,importlib.util,json,pathlib,sys,threading,socket,secrets,os,subprocess,time,webbrowser
from urllib.request import urlopen
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=ROOT/'local-data/网页资源库/游戏版第一版'
if (ROOT/'local-data/资源库/当前版本.json').exists():OUT=ROOT/'local-data/资源库/验收/独立预览'
sys.path.insert(0,str(ROOT/'scripts'))
def read(p):return json.loads(pathlib.Path(p).read_text('utf8'))
def load(file,name):
 s=importlib.util.spec_from_file_location(name,ROOT/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def safe_port(port):
 if port:return port
 # Windows自动分配可能落到浏览器禁用的6665至6669等端口。
 for _ in range(100):
  candidate=20000+secrets.randbelow(35000)
  with socket.socket() as sock:
   try:sock.bind(('127.0.0.1',candidate));return candidate
   except OSError:continue
 raise RuntimeError('无法分配浏览器安全端口')
def catalog(resolution=None):
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 统一资源 import available,current,record_path
 if available():
  _,cat,routes,index=current();registry=read(index/'模型登记.json');models=[]
  for key,row in registry.items():
   if row['kind']!='model':continue
   for v in row['variants']:
    if resolution and v['resolution']!=resolution:continue
    models.append({**v,'source_sha256':f"{key}.{v['resolution']}.lod{v['level']}",'base_sha256':key,'ids':[row['id']],'name':row['name'],'source_bytes':v['bytes'],'original_url':None})
  return {'models':models,'audio':cat['audio']},{url:record_path(row) for url,row in routes.items()}
 previous=load('scripts/资源优化/验证服务.py','game_previous');old,oldroutes=previous.catalog();by_hash={r['source_sha256']:r for r in old['models']};models=[];routes={r['url']:oldroutes[r['url']] for r in old['audio']}
 for p in sorted((OUT/'验证').glob('*.json')):
  r=read(p)
  if not r.get('passed'):continue
  base=by_hash[r['source_sha256']]
  for v in r['variants']:
   if resolution and v['resolution']!=resolution:continue
   identifier=f"{r['source_sha256']}.{v['resolution']}.lod{v['level']}";url='/media/'+v['sha256']+'.glb';original='/original/'+r['source_sha256']+'.glb';routes[url]=ROOT/v['path'];routes[original]=pathlib.Path(r['source'])
   models.append({**base,**v,'source_sha256':identifier,'base_sha256':r['source_sha256'],'original_source_sha256':r['source_sha256'],'name':f"{base['name']} · {v['resolution']} · 几何{v['level']}",'url':url,'original_url':original})
 models.sort(key=lambda r:('model:US_M1A2' not in r.get('ids',[]),r['resolution']!=256,r['level']!=1,r['name']))
 return {'models':models,'audio':old['audio']},routes
def create_catalog(port=0):
 sys.path.insert(0,str(ROOT/'scripts/断箭'));sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 统一资源 import available
 if available():return load('scripts/断箭/启动正式资料库.py','unified_formal_preview').create_server(safe_port(port))
 previous=load('scripts/资源优化/验证服务.py','game_catalog_server');result,routes=catalog();original_catalog=previous.catalog;previous.catalog=lambda include_lod=False:(result,routes)
 server=previous.create_server(safe_port(port));previous.catalog=original_catalog;return server
def create_range(resolution=256,level=1,port=0,baseline=False):
 m=load('scripts/预览载具靶场.py','game_range_server');s=m.create_server(safe_port(port));manifest=json.loads(s.manifest_bytes)
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 统一资源 import available,current,record_path
 unified=available();registry=read(current()[3]/'模型登记.json') if unified else None
 for key,row in manifest['models'].items():
  if unified:
   if baseline:raise ValueError('旧保真派生已退役；历史性能结果请读取已保留报告')
   source=row['runtime_source_sha256'];v=next((v for v in registry[source]['variants'] if v['resolution']==resolution and v['level']==level),None)
   if not v:raise ValueError('该规格已退役，仅保留256默认/远景与512回退')
   p=record_path(v);url=v['url'];s.routes[url]={'path':str(p),'bytes':v['bytes'],'sha256':v['sha256']};row.update(url=url,bytes=v['bytes'],runtime_sha256=v['sha256'],game_resolution=resolution,geometry_level=level)
  elif baseline:
   source=row.get('runtime_source_sha256') or row['sha256'];v=read(ROOT/'local-data/网页资源库/全库第一版/验证'/(source+'.model.json'));url='/media/'+v['sha256']+'.glb';s.routes[url]={'path':str(ROOT/v['path']),'bytes':v['bytes'],'sha256':v['sha256']};row.update(url=url,bytes=v['bytes'],runtime_sha256=v['sha256'])
  else:
   source=row.get('runtime_source_sha256') or row['sha256'];r=read(OUT/'验证'/(source+'.json'));v=next(v for v in r['variants'] if v['resolution']==resolution and v['level']==level)
   url='/media/'+v['sha256']+'.glb';p=ROOT/v['path'];s.routes[url]={'path':str(p),'bytes':v['bytes'],'sha256':v['sha256']};row.update(url=url,bytes=v['bytes'],runtime_sha256=v['sha256'],game_resolution=resolution,geometry_level=level)
 manifest['game_variant']={'resolution':resolution,'level':level,'baseline':baseline};s.manifest_bytes=json.dumps(manifest,ensure_ascii=False).encode()
 # 固定实际HTTP源码；同次对照不受其他会话修改影响。
 snap=OUT/'源码快照';snap.mkdir(parents=True,exist_ok=True)
 lockfile=OUT/'验证源码锁.json';locked=read(lockfile) if lockfile.exists() else {}
 s.game_source_identity={}
 for url,p in list(s.static.items()):
  if p.suffix not in ['.mjs','.js','.css','.html'] or url.startswith('/vendor/'):continue
  if url in locked:
   p=(ROOT/locked[url]['path']).resolve()
   if not p.is_relative_to(snap.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest()!=locked[url]['sha256']:raise ValueError('验证源码锁的快照越界或摘要变化')
  b=p.read_bytes();digest=hashlib.sha256(b).hexdigest();s.game_source_identity[url]=digest;target=snap/(digest+p.suffix)
  if not target.exists():target.write_bytes(b)
  s.static[url]=target
 return s
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',choices=['catalog','range'],default='catalog');p.add_argument('--resolution',type=int,choices=[256,512],default=256);p.add_argument('--level',type=int,choices=[0,1,2],default=1);p.add_argument('--port',type=int,default=0);p.add_argument('--open',action='store_true');a=p.parse_args();identity=OUT/f'预览-{a.app}-{a.resolution}-{a.level}.json'
 if a.open and a.app=='catalog' and (ROOT/'local-data/资源库/当前版本.json').exists():
  load('scripts/断箭/启动正式资料库.py','open_unified_library').open_library()
 elif a.open:
  def ready():
   try:
    r=read(identity);endpoint='catalog.json' if a.app=='catalog' else 'manifest.json';data=json.load(urlopen(r['url']+endpoint,timeout=2))
    lockfile=OUT/'验证源码锁.json';expected=hashlib.sha256(lockfile.read_bytes()).hexdigest() if lockfile.exists() else None
    return r['url'] if (data.get('models') and (a.app=='catalog' or (data.get('game_variant',{}).get('resolution')==a.resolution and r.get('source_lock_sha256')==expected))) else None
   except (OSError,ValueError):return None
  url=ready()
  if not url:
   OUT.mkdir(parents=True,exist_ok=True)
   with (OUT/f'预览-{a.app}.log').open('ab') as log:subprocess.Popen([sys.executable,'-X','utf8','-B',str(pathlib.Path(__file__).resolve()),'--app',a.app,'--resolution',str(a.resolution),'--level',str(a.level)],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
   deadline=time.monotonic()+30;event=threading.Event()
   while time.monotonic()<deadline and not url:url=ready();event.wait(.15)
  if not url:raise RuntimeError('独立游戏预览启动失败，请检查日志')
  webbrowser.open(url);print(url)
 else:
  s=create_catalog(a.port) if a.app=='catalog' else create_range(a.resolution,a.level,a.port);url=f'http://127.0.0.1:{s.server_port}/';lockfile=OUT/'验证源码锁.json';identity.parent.mkdir(parents=True,exist_ok=True);identity.write_text(json.dumps({'app':a.app,'url':url,'pid':os.getpid(),'resolution':a.resolution,'level':a.level,'source_lock_sha256':hashlib.sha256(lockfile.read_bytes()).hexdigest() if lockfile.exists() else None},ensure_ascii=False),encoding='utf8');print(url,flush=True);s.serve_forever()
