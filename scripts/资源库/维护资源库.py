"""统一库维护：核验、查询、原件恢复与待入库重建；没有删除命令。"""
import argparse,subprocess,sys
from 统一资源 import ROOT,HOME,current,record_path,sha,save,restore

def audit():
 pointer,catalog,routes,index=current();files={r['path']:r for r in routes.values()}
 from 统一资源 import read
 for row in read(index/'主工程.json').values():files[row['path']]=row
 original=read(index/'原件映射.json')
 for row in original.values():files[row['archive']['path']]=row['archive']
 for row in files.values():
  p=record_path(row)
  if not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('当前文件不符：'+row['path'])
 result={'revision':pointer['revision'],'summary':catalog['summary'],'files':len(files),'retained_bytes':sum(r['bytes'] for r in files.values()),'all_routes_inside_unified_library':True,'passed':True}
 save(HOME/'验收/当前文件审计.json',result);print(result,flush=True);return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['audit','verify','restore','rebuild']);p.add_argument('--hash');p.add_argument('--output');a=p.parse_args()
 if a.action=='audit':audit()
 elif a.action=='verify':
  from 核验统一库 import run
  run()
 elif a.action=='restore':
  if not a.hash:p.error('restore需要--hash')
  print(restore(a.hash,a.output))
 else:subprocess.run(['node',str(ROOT/'scripts/资源优化/生成游戏版.mjs')],cwd=ROOT,check=True)
if __name__=='__main__':main()
