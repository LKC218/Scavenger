"""统一资源位置、稳定ID、原件恢复与运行清单；不依赖旧优化目录。"""
import copy,hashlib,json,pathlib,subprocess
from functools import lru_cache
ROOT=pathlib.Path(__file__).resolve().parents[2];HOME=ROOT/'local-data/资源库'
def read(p):return json.loads(pathlib.Path(p).read_text('utf-8-sig'))
def sha(p):
 with pathlib.Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,d):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')
def available():return (HOME/'当前版本.json').exists()
def current():
 return _current(sha(HOME/'当前版本.json'))
@lru_cache(maxsize=1)
def _current(identity):
 pointer=read(HOME/'当前版本.json');base=HOME/pointer['directory']
 for name,digest in pointer['metadata'].items():
  if sha(base/name)!=digest:raise ValueError('统一库索引摘要变化：'+name)
 return pointer,read(base/'资源索引.json'),read(base/'资源路由.json'),base
def record_path(row):
 p=(ROOT/row['path']).resolve()
 if not p.is_relative_to(HOME.resolve()):raise ValueError('资源路径越界')
 return p
def replacements():
 return _replacements(sha(HOME/'当前版本.json'))
@lru_cache(maxsize=1)
def _replacements(identity):
 _,_,_,base=current();rows=read(base/'运行替换.json');result={}
 for key,r in rows.items():
  p=record_path(r)
  if not p.is_file() or p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:raise ValueError('当前资源损坏：'+key)
  result[key]={**r,'url':'/media/'+r['sha256']+p.suffix,'route':{'path':str(p),'bytes':r['bytes'],'sha256':r['sha256']}}
 return result
def routes_absolute(routes):return {u:{**r,'path':str(record_path(r))} for u,r in routes.items()}
def restore(digest,output=None):
 _,_,_,base=current();entry=read(base/'原件映射.json')[digest];archive=record_path(entry['archive']);target=pathlib.Path(output) if output else HOME/'恢复缓存'/digest/(pathlib.Path(entry['entry']).name)
 if target.exists():
  if sha(target)!=digest:raise ValueError('恢复目标存在不同内容，拒绝覆盖')
  return target
 target.parent.mkdir(parents=True,exist_ok=True);tool=ROOT/'local-data/tools/网页资源优化/1/node_modules/7zip-bin/win/x64/7za.exe'
 with target.with_suffix(target.suffix+'.writing').open('wb') as f:subprocess.run([str(tool),'x','-so',str(archive),entry['entry']],stdout=f,stderr=subprocess.PIPE,check=True)
 temp=target.with_suffix(target.suffix+'.writing')
 if sha(temp)!=digest:raise ValueError('归档恢复摘要不符')
 temp.replace(target);return target
def resolve_source(path_or_sha):
 _,_,_,base=current();mapping=read(base/'原件映射.json');aliases=read(base/'来源别名.json');value=str(path_or_sha);key=value if value in mapping else aliases.get(value.replace('\\','/'))
 if key in mapping:return restore(key)
 masters=read(base/'主工程.json')
 for r in masters.values():
  if r['sha256']==value or (key and r['sha256']==key):return record_path(r)
 raise ValueError('该路径不是保留原件；历史派生已退役，请按稳定ID查询当前资源：'+value)
def range_manifest(original,routes):
 result=copy.deepcopy(original);available=replacements();used=[]
 for group in ['models','audio']:
  for key,row in result.get(group,{}).items():
   old=routes.get(row.get('url'));source=old['sha256'] if old else row.get('runtime_source_sha256');v=available.get(source) or available.get(row.get('runtime_source_sha256'))
   if not v:raise ValueError('统一库缺少活动资源：'+key)
   row.update(url=v['url'],bytes=v['bytes'],runtime_source_sha256=v['source_sha256'],runtime_sha256=v['sha256'],asset_library='统一资源库');routes[v['url']]=v['route'];used.append(key)
 result['runtime_optimization']={'used':used,'source_manifest_unchanged':True,'asset_library':'统一资源库'};return result
