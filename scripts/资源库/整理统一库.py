"""将当前模型、音频、主工程与原件归档汇入统一库；不删除任何旧文件。"""
import collections,copy,hashlib,json,pathlib,shutil,subprocess,sys,time
from 统一资源 import ROOT,HOME,read,save,sha
OLD=ROOT/'local-data/网页资源库/全库第一版';GAME=ROOT/'local-data/网页资源库/游戏版第一版';INDEX=HOME/'索引';SEVEN=ROOT/'local-data/tools/网页资源优化/1/node_modules/7zip-bin/win/x64/7za.exe'
def run():
 if (HOME/'当前版本.json').exists():raise SystemExit('统一库已经发布；日常维护请使用维护资源库.py，不再次从历史目录初始化。')
 INDEX.mkdir(parents=True,exist_ok=True);inventory=read(OLD/'盘点.json');by_hash=collections.defaultdict(list)
 for r in inventory['files']:by_hash[r['sha256']].append(r)
 lib=ROOT/'local-data/断箭/正式资料库';pointer=read(lib/'当前版本.json');base=lib/pointer['directory'];catalog=read(base/'资源索引.json');oldroutes=read(base/'资源路由.json');catalog=copy.deepcopy(catalog);routes={};registry={};originals={};aliases={};replace={};copies=[];masters={}
 def copy_file(source,kind,expected=None):
  source=pathlib.Path(source);digest=sha(source)
  if expected and digest!=expected:raise ValueError('输入摘要变化：'+str(source))
  dst=HOME/'当前资源'/kind/(digest+source.suffix.lower());dst.parent.mkdir(parents=True,exist_ok=True)
  if not dst.exists():shutil.copyfile(source,dst)
  if sha(dst)!=digest:raise ValueError('副本摘要不符')
  r={'path':dst.relative_to(ROOT).as_posix(),'bytes':dst.stat().st_size,'sha256':digest};copies.append({'source':str(source),'retained':r});return r
 def add_route(row):
  url='/media/'+row['sha256']+pathlib.Path(row['path']).suffix;routes[url]=row;return url
 def original(source,category,expected=None):
  source=pathlib.Path(source);digest=sha(source)
  if expected and digest!=expected:raise ValueError('原件摘要变化')
  originals.setdefault(digest,{'path':source.relative_to(ROOT).as_posix(),'bytes':source.stat().st_size,'sha256':digest,'category':category})
  for r in by_hash.get(digest,[]):aliases[r['path']]=digest;aliases[str(ROOT/r['path']).replace('\\','/')]=digest
  return digest
 def game_source(digest,identity,name):
  r=read(GAME/'验证'/(digest+'.json'));assert r['passed'];variants=[]
  for v in r['variants']:
   if not ((v['resolution']==256 and v['level'] in ([0] if v['animated'] else [1,2])) or (v['resolution']==512 and v['level']==(0 if v['animated'] else 1))):continue
   identifier=f"{digest}.{v['resolution']}.lod{v['level']}"
   checks={}
   for browser in ['chrome','msedge']:
    check=read(GAME/'浏览器验证'/browser/(identifier+'.json'));assert check['sha256']==v['sha256'] and check['technical_passed'] and check['visual_threshold_passed'];checks[browser]=check
   record=copy_file(ROOT/v['path'],'模型',v['sha256']);url=add_route(record);variants.append({**v,**record,'url':url,'validation':checks})
  chosen=next(v for v in variants if v['resolution']==256 and v['level']==(0 if v['animated'] else 1));registry[digest]={'id':identity,'name':name,'source_sha256':digest,'kind':'model','variants':variants,'default':chosen['sha256']};replace[digest]={**chosen,'kind':'model','source_sha256':digest}
  old=read(OLD/'验证'/(digest+'.model.json'));replace[old['sha256']]=replace[digest]
  return variants,chosen
 for row in catalog['models']:
  source=row['sha256'];variants,chosen=game_source(source,row['id'],row['name']);original(pathlib.Path(oldroutes[row['model_url']]['path']),'模型',source);master=copy_file(oldroutes[row['blend_url']]['path'],'主工程',oldroutes[row['blend_url']]['sha256']);masters[row['id']]=master;preview=copy_file(oldroutes[row['preview_url']]['path'],'预览',oldroutes[row['preview_url']]['sha256']);row.update(source_sha256=source,model_url=chosen['url'],sha256=chosen['sha256'],bytes=chosen['bytes'],blend_url=add_route(master),preview_url=add_route(preview),runtime_variants=[{k:v[k] for k in ['resolution','level','url','bytes','sha256','triangles','before_triangles']} for v in variants],geometry={'triangles':chosen['triangles'],'source_triangles':chosen['before_triangles'],'texture_max':256,'profile':'游戏版'});row['extensions']['asset_library']='统一资源库'
 for row in catalog['audio']:
  source=row['sha256'];original(oldroutes[row['url']]['path'],'音效',source);r=read(OLD/'验证'/(source+'.audio.json'));assert r['passed'];record=copy_file(ROOT/r['path'],'音效',r['sha256']);url=add_route(record);registry[source]={'id':row['id'],'name':row['name'],'kind':'audio','source_sha256':source,**record};replace[source]={**r,**record,'kind':'audio','source_sha256':source};replace[r['sha256']]=replace[source];row.update(source_sha256=source,url=url,sha256=record['sha256'],bytes=record['bytes'],format=pathlib.Path(record['path']).suffix[1:].upper());row['extensions']['asset_library']='统一资源库'
 # 当前靶场机械装配与自制循环，保留用途变体及可重建输入。
 range_home=ROOT/'local-data/原型/载具靶场';range_manifest=read(range_home/'资源清单.json');range_routes=read(range_home/'私有路由.json');runtime=copy.deepcopy(range_manifest);runtime_routes={}
 for kind,row in runtime['models'].items():
  old=range_routes[row['url']];source=old['sha256'];variants,chosen=game_source(source,'runtime:'+kind,{'tank':'M1A2靶场机械装配','heli':'AH-64D靶场机械装配','plane':'A-10C靶场装配'}[kind]);master=copy_file(old['path'],'装配源',source);masters['runtime:'+kind]=master;row.update(url=chosen['url'],bytes=chosen['bytes'],runtime_source_sha256=source,runtime_sha256=chosen['sha256'],asset_library='统一资源库');runtime_routes[chosen['url']]=routes[chosen['url']]
 for key,row in runtime['audio'].items():
  old=range_routes[row['url']];source=old['sha256']
  if source not in replace:
   r=read(OLD/'验证'/(source+'.audio.json'));record=copy_file(ROOT/r['path'],'音效',r['sha256']);url=add_route(record);master=copy_file(old['path'],'循环源',source);masters['loop:'+key]=master;replace[source]={**r,**record,'kind':'audio','source_sha256':source};replace[r['sha256']]=replace[source];registry[source]={'id':'audio:loop:'+key,'name':row['name'],'kind':'audio','source_sha256':source,**record};model_id={'tankMove':'model:US_M1A2','heliMove':'model:US_AH64D','planeMove':'model:US_A10'}[key];identity='audio:loop:'+key;catalog['audio'].append({'id':identity,'type':'audio','name':row['name']+'（当前无缝循环）','aliases':[key],'category':'引擎循环','tags':['循环',key],'quality_status':'ready','issue':'项目派生循环，实际听感仍需验收','event_guid':'','event_path':'','banks':[],'url':url,'source_sha256':source,'sha256':record['sha256'],'bytes':record['bytes'],'check':{'duration_seconds':row['duration_seconds'],'channels':row['channels'],'sample_rate':row['sample_rate']},'format':pathlib.Path(record['path']).suffix[1:].upper(),'render_profile':{},'kind':'derived_loop','model_ids':[model_id],'bindings':[{'model_id':model_id,'field':'MoveEvent','evidence':'当前靶场循环清单'}],'source':{'derivation':row.get('derivation')},'extensions':{}});next(m for m in catalog['models'] if m['id']==model_id)['audio_ids'].append(identity)
  v=replace[source];url=add_route(v);row.update(url=url,bytes=v['bytes'],runtime_source_sha256=source,runtime_sha256=v['sha256'],asset_library='统一资源库');runtime_routes[url]=routes[url]
 runtime['runtime_optimization']={'used':list(runtime['models'])+list(runtime['audio']),'asset_library':'统一资源库'}
 # 原始角色包及当前单只动态角色均列入统一入口。
 zombie=read(ROOT/'research/丧尸模型/资源清单.json');horde=read(ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001/资源清单.json');horde_home=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001'
 for identity,digest,name in [('model:zombie-source',horde['source_sha256'],'原始丧尸角色包'),('model:zombie-runtime',horde['asset']['sha256'],'当前丧尸动态角色')]:
  variants,chosen=game_source(digest,identity,name);source=pathlib.Path(read(GAME/'验证'/(digest+'.json'))['source'])
  if identity.endswith('source'):original(source,'模型',digest)
  else:masters[identity+':source']=copy_file(source,'装配源',digest)
  master_source=ROOT/next(r['路径'] for r in zombie['资源'] if r['ID']==zombie['采用工程ID']) if identity.endswith('source') else horde_home/horde['blend']['file'];master=copy_file(master_source,'主工程');masters[identity]=master
  # 使用本轮真实浏览器对照作为缩略图，不新增渲染或改变模型。
  image=GAME/'浏览器验证/chrome'/(digest+'.256.lod0.jpg');preview=copy_file(image,'预览');catalog['models'].append({'id':identity,'source_id':identity.split(':')[1],'type':'model','name':name,'aliases':['丧尸','zombie'],'category':'other','tags':['骨骼动画'],'variant':'保留完整拓扑及动作','quality_status':'pending','model_url':chosen['url'],'preview_url':add_route(preview),'blend_url':add_route(master),'source_sha256':digest,'sha256':chosen['sha256'],'bytes':chosen['bytes'],'audio_ids':[],'reference_counts':{},'geometry':{'triangles':chosen['triangles'],'texture_max':256,'profile':'游戏版'},'limitations':['骨骼技术通过，主体外观待工程师验收'],'source':{'author':horde['author'],'license':horde['license'],'url':horde['source_url']},'extensions':{},'runtime_variants':[{k:v[k] for k in ['resolution','level','url','bytes','sha256','triangles','before_triangles']} for v in variants]})
  if identity.endswith('runtime'):
   horde['asset'].update(file=str(ROOT/chosen['path']),bytes=chosen['bytes'],sha256=chosen['sha256'],source_sha256=digest,runtime_variant=chosen['path']);horde['blend'].update(file=str(ROOT/master['path']),sha256=master['sha256']);save(INDEX/'群体运行清单.json',horde)
 # 首次导入的52个源工程保留原始档，装配过程中的中间工程不带入新库。
 for r in inventory['files']:
  if r['format']=='.blend' and '/场景数据/' in r['path']:original(ROOT/r['path'],'源工程',r['sha256'])
 for identity,r in masters.items():
  # 最新主工程也建立来源别名；它们直接可编辑，不强制解压。
  for old in by_hash.get(r['sha256'],[]):aliases[old['path']]=r['sha256'];aliases[str(ROOT/old['path']).replace('\\','/')]=r['sha256']
 catalog.update(name='统一模型音频库',revision=pointer['revision']+1,asset_library_revision=1,status='current',scope='当前模型、骨骼角色、事件声音与已使用循环；历史迭代不列入日常库')
 catalog['summary']={'models':len(catalog['models']),'audio':len(catalog['audio']),'models_with_audio':sum(bool(m['audio_ids']) for m in catalog['models']),'associated_audio':sum(bool(a['model_ids']) for a in catalog['audio']),'unassigned_audio':sum(not a['model_ids'] for a in catalog['audio']),'model_statuses':dict(collections.Counter(m['quality_status'] for m in catalog['models'])),'audio_statuses':dict(collections.Counter(a['quality_status'] for a in catalog['audio']))}
 save(INDEX/'资源索引.json',catalog);save(INDEX/'资源路由.json',routes);save(INDEX/'运行替换.json',replace);save(INDEX/'模型登记.json',registry);save(INDEX/'主工程.json',masters);save(INDEX/'靶场运行清单.json',runtime);save(INDEX/'靶场资源路由.json',runtime_routes);save(INDEX/'归档输入.json',originals);save(INDEX/'来源别名.json',aliases);save(HOME/'验收/复制核验.json',copies);save(INDEX/'维护输入.json',{'models':[{'source_sha256':h,'id':r['id'],'source':masters.get(r['id']+':source') or masters.get(r['id'])} for h,r in registry.items() if r['kind']=='model'],'source_archive_count':len(originals)})
 print('当前库',catalog['summary'],'原件',len(originals),'运行模型来源',sum(r['kind']=='model' for r in registry.values()),flush=True)
 # 原始分类归档；分组限制固实块，避免每次恢复小文件都解压整个集合。
 entries={}
 for category in ['模型','音效','源工程']:
  items=[r for r in originals.values() if r['category']==category];archive=HOME/'原始归档'/(category+'.7z');archive.parent.mkdir(parents=True,exist_ok=True);listing=HOME/'验收'/(category+'输入.txt');listing.write_text('\n'.join(r['path'] for r in items),encoding='utf8');log=HOME/'验收'/(category+'归档.log')
  with log.open('ab') as stream:
   if not archive.exists():subprocess.run([str(SEVEN),'a','-t7z',str(archive),'@'+str(listing),'-scsUTF-8','-mx=5','-m0=lzma2','-md=64m','-ms=256m','-mmt=4','-y','-bsp0'],cwd=ROOT,stdout=stream,stderr=stream,check=True)
   subprocess.run([str(SEVEN),'t',str(archive),'-bsp0'],stdout=stream,stderr=stream,check=True)
   restored=HOME/'归档核验临时'/category;subprocess.run([str(SEVEN),'x',str(archive),'-o'+str(restored),'-y','-bsp0'],stdout=stream,stderr=stream,check=True)
  archive_record={'path':archive.relative_to(ROOT).as_posix(),'bytes':archive.stat().st_size,'sha256':sha(archive)}
  for r in items:
   p=restored/r['path']
   if not p.is_file() or sha(p)!=r['sha256']:raise ValueError('归档恢复失败：'+r['path'])
   entries[r['sha256']]={'archive':archive_record,'entry':r['path'],'bytes':r['bytes'],'category':category}
  print('原件归档恢复通过',category,len(items),archive.stat().st_size,flush=True)
 save(INDEX/'原件映射.json',entries)
 # 最新主工程通过当前文件直接返回；只有真正原件走归档。
 save(INDEX/'保留策略.json',{'game_profiles':['256-lod1','256-lod2','512-lod1'],'animated_profiles':['256-lod0','512-lod0'],'original_categories':['模型','音效','源工程'],'masters':len(masters),'deleted':0,'historical_reports':'保留追溯及必要核验，历史预览列清单'})
 names=[p.name for p in INDEX.glob('*.json')];save(HOME/'当前版本.json',{'schema':1,'revision':catalog['revision'],'asset_library_revision':1,'directory':'索引','metadata':{name:sha(INDEX/name) for name in names}});print('统一库发布完成',flush=True)
if __name__=='__main__':run()
