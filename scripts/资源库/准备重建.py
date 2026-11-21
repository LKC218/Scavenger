"""从唯一原件归档及当前装配源准备重建；输出到待入库，不回写旧目录。"""
from 统一资源 import ROOT,HOME,current,read,save,resolve_source
def run():
 _,_,_,index=current();registry=read(index/'模型登记.json');destination=HOME/'维护/验证';destination.mkdir(parents=True,exist_ok=True)
 for key,r in registry.items():
  if r['kind']!='model':continue
  source=resolve_source(key);save(destination/(key+'.model.json'),{'kind':'model','passed':True,'source':str(source),'source_sha256':key,'source_bytes':source.stat().st_size});save(destination/(key+'.lod.json'),{'passed':True,'variants':r['variants']})
 print(str(HOME/'维护'))
if __name__=='__main__':run()
