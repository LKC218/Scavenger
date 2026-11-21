"""游戏视角整库加载、轮廓与动作核验；与源版对照，不继承旧材质通过结论。"""
import argparse,base64,hashlib,importlib.util,io,json,pathlib,threading
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/游戏版第一版'
spec=importlib.util.spec_from_file_location('game_server',ROOT/'scripts/资源优化/游戏版服务.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def pixels(s):return np.asarray(Image.open(io.BytesIO(base64.b64decode(s.split(',')[1]))).convert('RGB')).astype(np.float32)
def run(browser):
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry('verify')
 server=module.create_catalog();threading.Thread(target=server.serve_forever,daemon=True).start();home=OUT/'浏览器验证'/browser;home.mkdir(parents=True,exist_ok=True);records=[];errors=[]
 with sync_playwright() as pw:
  b=pw.chromium.launch(channel=browser,headless=True);page=b.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('window.validation?.silhouette',timeout=120000);page.evaluate('validation.stable()');catalog=page.evaluate('catalog');originals={}
  for i,item in enumerate(catalog['models']):
   dest=home/(item['source_sha256']+'.json')
   if dest.exists():
    old=json.loads(dest.read_text('utf8'))
    if old.get('sha256')==item['sha256'] and old.get('technical_passed') and old.get('visual_threshold_passed'):records.append(old);continue
   result={'id':item['source_sha256'],'source_sha256':item['base_sha256'],'sha256':item['sha256'],'resolution':item['resolution'],'level':item['level'],'name':item['name']}
   try:
    source=item['base_sha256']
    if source not in originals:
     original=page.evaluate('(id)=>validation.show(id,false)',item['source_sha256']);poses=[page.evaluate('(t)=>validation.pose(t)',t) for t in [0,.25,.5,.75,1.25]]
     views=[]
     for distance in [4.0,2.4]:
      views.append(capture(page,distance))
     originals[source]=(original,poses,views)
    original,poses,views=originals[source];optimized=page.evaluate('(id)=>validation.show(id,true)',item['source_sha256']);poses_b=[page.evaluate('(t)=>validation.pose(t)',t) for t in [0,.25,.5,.75,1.25]]
    pose_error=max((float(np.max(np.abs(np.array(a)-np.array(c)))) if a else 0 for a,c in zip(poses,poses_b)),default=0);metrics=[]
    for distance,view in zip([4.0,2.4],views):
     ib,mb=capture(page,distance);ia,ma=view;union=ma|mb;iou=float(np.sum(ma&mb)/max(1,np.sum(union)));mae=float(np.mean(np.abs(ia-ib)[union])) if union.any() else 255;metrics.append({'distance':distance,'silhouette_iou':iou,'foreground_mae':mae})
     if distance==2.4 and (item['level']==1 or item.get('animated')):Image.fromarray(np.concatenate([ia,ib],axis=1).astype('uint8')).save(home/(item['source_sha256']+'.jpg'),quality=85)
    result.update(technical_passed=original['meshes']==optimized['meshes'] and original['skinned']==optimized['skinned'] and original['animations']==optimized['animations'] and pose_error<.01,pose_error=pose_error,views=metrics,visual_threshold_passed=all(v['silhouette_iou']>=.94 and v['foreground_mae']<=30 for v in metrics))
   except Exception as e:result.update(technical_passed=False,error=str(e))
   dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');records.append(result)
   if i%30==0:print(browser,i+1,'/',len(catalog['models']),flush=True)
  summary={'browser':browser,'version':b.version,'condition':'640x360，俯斜视角两档距离，自动阈值IoU>=0.94，前景MAE<=30/255；实际靶场另测，非人工签收','count':len(records),'technical_passed':sum(bool(r.get('technical_passed')) for r in records),'visual_passed':sum(bool(r.get('visual_threshold_passed')) for r in records),'errors':errors,'models':records};(home/'汇总.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');b.close();print(browser,'完成',summary['count'],summary['technical_passed'],summary['visual_passed'],flush=True)
 server.shutdown();return summary
def capture(page,distance):
 page.evaluate('''(distance)=>{const v=validation,c=v.camera;const box=v.current.userData.__unused;const direction=c.position.clone();const size=lastResult.size,scale=Math.max(...size);const center=v.current.position.clone();let min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];v.current.traverse(o=>{if(!o.isMesh)return;o.geometry.computeBoundingBox();for(const x of [o.geometry.boundingBox.min.x,o.geometry.boundingBox.max.x])for(const y of [o.geometry.boundingBox.min.y,o.geometry.boundingBox.max.y])for(const z of [o.geometry.boundingBox.min.z,o.geometry.boundingBox.max.z]){const p=c.position.clone().set(x,y,z).applyMatrix4(o.matrixWorld);for(let k=0;k<3;k++){min[k]=Math.min(min[k],p.getComponent(k));max[k]=Math.max(max[k],p.getComponent(k));}}});center.set(...min.map((x,i)=>(x+max[i])/2));if(lastResult.skinned){if(!v.current.userData.validationCenter)v.current.userData.validationCenter=c.position.toArray().map((x,i)=>x-[.95,.8,.95][i]*scale);center.set(...v.current.userData.validationCenter);}c.position.copy(center).add(direction.set(.95,.8,.95).multiplyScalar(scale*distance));c.lookAt(center);c.updateMatrixWorld();}''',distance)
 return pixels(page.evaluate('validation.sample(.37)')),np.max(pixels(page.evaluate('validation.silhouette(.37)')),axis=2)>128
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--browser',choices=['chrome','msedge'],default='chrome');a=p.parse_args();run(a.browser)
