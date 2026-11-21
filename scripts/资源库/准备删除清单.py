"""只生成精确删除候选；不会调用删除、移动或结束进程。"""
import collections,csv,json,pathlib,re
from urllib.request import urlopen
from 统一资源 import ROOT,HOME,current,read,save,sha,record_path
MEDIA={'.glb','.blend','.blend1','.wav','.opus','.flac','.ktx2','.png','.jpg','.jpeg','.zip','.7z'}
def run():
 verification=read(HOME/'验收/统一库核验.json');assert len(verification['browsers'])==2 and all(not r['errors'] for r in verification['browsers'])
 assert read(HOME/'验收/当前文件审计.json')['passed'] and read(HOME/'验收/本机服务迁移.json')['passed']
 _,catalog,routes,index=current();originals=read(index/'原件映射.json');copies=read(HOME/'验收/复制核验.json');retained={r['sha256']:r for r in routes.values()}
 actual=json.load(urlopen('http://127.0.0.1:8768/manifest.json'));assert all(r['asset_library']=='统一资源库' and r['url'] in routes for group in ['models','audio'] for r in actual[group].values())
 for r in originals.values():retained[r['archive']['sha256']]=r['archive']
 for r in copies:retained[r['retained']['sha256']]=r['retained']
 active={record_path(r).resolve() for r in retained.values()};inventory=read(ROOT/'local-data/网页资源库/全库第一版/盘点.json');expected={r['path']:r['sha256'] for r in inventory['files']};candidates={};protected=[];model_ids=sorted(catalog['models'],key=lambda x:-len(x['source_id']))
 def add(path,category):
  path=path.resolve()
  if not path.is_relative_to(ROOT.resolve()) or path in active:return
  relative=path.relative_to(ROOT).as_posix()
  if relative in candidates:return
  digest=sha(path)
  if relative in expected and expected[relative]!=digest:protected.append({'path':str(path),'reason':'盘点后发生变化，保护其他会话修改','sha256':digest});return
  destination=retained.get(digest);archive=originals.get(digest);replacement=None
  if destination:replacement={'kind':'当前同摘要副本',**destination}
  elif archive:replacement={'kind':'原件归档','path':archive['archive']['path'],'archive_sha256':archive['archive']['sha256'],'entry':archive['entry']}
  else:
   for model in model_ids:
    if re.search(r'(?<![A-Za-z0-9_])'+re.escape(model['source_id'])+r'(?![A-Za-z0-9_])',relative):replacement={'kind':'已替代的历史版本','id':model['id'],'path':routes[model['model_url']]['path']};break
  candidates[relative]={'path':str(path),'relative_path':relative,'bytes':path.stat().st_size,'sha256':digest,'category':category,'reason':'当前引用已切换统一资源库；按已确认规则退役旧文件','retained':replacement,'intentional_history_removal':not bool(destination or archive),'reference_status':'活动索引与新服务不引用；执行前须复查文件及进程','approval':'等待工程师确认'}
 for r in inventory['files']:
  p=ROOT/r['path']
  if p.is_file():add(p,'旧源文件及中间工程')
 for directory,category in [('local-data/网页资源库/全库第一版','旧保真压缩及验证派生'),('local-data/网页资源库/游戏版第一版','旧游戏派生及多余规格'),('local-data/资源归档/全库第一版','被替代的混合历史归档'),('tmp/整库固实归档恢复核验','旧整库归档恢复临时件'),('tmp/资源恢复','旧恢复缓存'),('local-data/资源库/归档核验临时','本轮归档验证临时件')]:
  folder=ROOT/directory
  if folder.exists():
   for p in folder.rglob('*'):
    if p.is_file() and p.suffix.lower() in MEDIA:add(p,category)
 # 老批次预览可重建，活动库缩略图已经独立复制；不处理游戏输入或未知原始提取文件。
 old=ROOT/'local-data/断箭'
 for p in old.rglob('*'):
  if not p.is_file() or p.suffix.lower() not in {'.png','.jpg','.jpeg'}:continue
  if any(part in {'预览','渲染','截图','模型预览'} for part in p.parts) or '/正式资料库/资源/模型/' in p.as_posix():add(p,'历史模型预览')
 summary={key:{'files':len(group),'bytes':sum(r['bytes'] for r in group)} for key in sorted({r['category'] for r in candidates.values()}) for group in [[r for r in candidates.values() if r['category']==key]]}
 result={'schema':1,'policy':'只保留原件、最新主工程和已确认当前规格；未获批准，不删除','deleted':0,'count':len(candidates),'bytes':sum(r['bytes'] for r in candidates.values()),'summary':summary,'protected':protected,'files':sorted(candidates.values(),key=lambda r:r['relative_path'])};folder=HOME/'待删除';save(folder/'删除候选精确清单.json',result)
 with (folder/'删除候选精确清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.writer(f);writer.writerow(['完整路径','字节','SHA256','类别','保留或替代位置','归档条目','不保留历史内容','审批状态'])
  for r in result['files']:v=r['retained'] or {};writer.writerow([r['path'],r['bytes'],r['sha256'],r['category'],str(ROOT/v['path']) if v.get('path') else '历史迭代内容明确不保留',v.get('entry',''),r['intentional_history_removal'],r['approval']])
 digest=sha(folder/'删除候选精确清单.json');save(folder/'清单身份.json',{'sha256':digest,'count':result['count'],'bytes':result['bytes'],'deleted':0});print('候选',result['count'],result['bytes'],digest,flush=True);return result
if __name__=='__main__':run()
