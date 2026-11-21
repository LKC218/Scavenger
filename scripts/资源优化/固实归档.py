"""固实7z归档与完整恢复摘要核验；保留原ZIP及恢复临时文件待审批。"""
import hashlib,json,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版';HOME=ROOT/'local-data/资源归档/全库第一版'
SEVEN=ROOT/'local-data/tools/网页资源优化/1/node_modules/7zip-bin/win/x64/7za.exe'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
if __name__=='__main__':
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry()
 data=json.loads((OUT/'盘点.json').read_text(encoding='utf8'));unique={}
 for r in data['files']:unique.setdefault(r['sha256'],r)
 listing=HOME/'固实输入清单.txt';listing.write_text('\n'.join(r['path'] for r in unique.values()),encoding='utf8');dst=HOME/'原始资源固实归档.7z'
 start=time.perf_counter();log=(HOME/'固实压缩.log').open('ab')
 if not dst.exists():subprocess.run([str(SEVEN),'a','-t7z',str(dst),'@'+str(listing),'-scsUTF-8','-mx=5','-m0=lzma2','-md=128m','-ms=4g','-mmt=4','-y','-bsp0'],cwd=ROOT,stdout=log,stderr=log,check=True)
 subprocess.run([str(SEVEN),'t',str(dst),'-bsp0'],stdout=log,stderr=log,check=True)
 restored=ROOT/'tmp/整库固实归档恢复核验';restored.mkdir(parents=True,exist_ok=True)
 subprocess.run([str(SEVEN),'x',str(dst),'-o'+str(restored),'-y','-bsp0'],stdout=log,stderr=log,check=True)
 entries={};checks=[]
 for key,r in unique.items():
  p=restored/r['path'];passed=p.is_file() and p.stat().st_size==r['bytes'] and sha(p)==key
  if not passed:raise ValueError('固实恢复摘要失败：'+r['path'])
  entries[key]={'archive':dst.relative_to(ROOT).as_posix(),'entry':r['path'],'bytes':r['bytes'],'format':'7z'};checks.append({'restored':p.relative_to(ROOT).as_posix(),'bytes':r['bytes'],'sha256':key})
 receipt={'passed':True,'archive':dst.relative_to(ROOT).as_posix(),'archive_bytes':dst.stat().st_size,'archive_sha256':sha(dst),'unique_files':len(entries),'seconds':time.perf_counter()-start,'entries':entries,'restored_checks':checks,'tool_sha256':sha(SEVEN)}
 (HOME/'固实归档验证.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
 old=HOME/'路径映射.json';mapping=json.loads(old.read_text(encoding='utf8'));new={'files':[{**r,'retained':entries[r['sha256']]} for r in mapping['files']]};(HOME/'固实路径映射.json').write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({k:v for k,v in receipt.items() if k not in ['entries','restored_checks']},ensure_ascii=False),flush=True)
