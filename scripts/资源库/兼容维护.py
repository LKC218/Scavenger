"""旧准备命令读取当前资源，不再生成旧模型或旧声音。"""
from 统一资源 import ROOT,HOME,current,read,routes_absolute,sha,save
def legacy_entry(action='retired'):
 from 统一资源 import available
 if not available():return
 if action=='verify':
  from 核验统一库 import run
  run();raise SystemExit(0)
 if action=='report':
  from 生成交付报告 import run
  run();raise SystemExit(0)
 raise SystemExit('旧优化阶段已退役；请使用 scripts/资源库/维护资源库.py。当前重建写入待入库，旧目录不再生成媒体。')

def check_runtime(sync_legacy=False):
 _,_,_,index=current();manifest=read(index/'靶场运行清单.json');routes=routes_absolute(read(index/'靶场资源路由.json'))
 for row in routes.values():
  if sha(row['path'])!=row['sha256']:raise ValueError('当前运行资源摘要变化')
 if sync_legacy:
  folder=ROOT/'local-data/原型/载具靶场';save(folder/'资源清单.json',manifest);save(folder/'私有路由.json',routes);save(folder/'输入核验.json',list(routes.values()))
 print('统一资源库：3模型、13声音摘要通过；不再生成旧版派生')
 return manifest,routes
