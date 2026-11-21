"""Chrome/Edge完整加载、原版对照截图和骨骼变形采样。"""
import argparse,base64,importlib.util,io,json,pathlib,threading,time
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版'
spec=importlib.util.spec_from_file_location('asset_server',ROOT/'scripts/资源优化/验证服务.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def image(data):return np.asarray(Image.open(io.BytesIO(base64.b64decode(data.split(',')[1]))).convert('RGB')).astype(np.float32)

def run(browser):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 核验统一库 import run as verify
        return verify()
    server=module.create_server(0,include_lod=True);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}/'
    home=OUT/'浏览器验证'/browser;home.mkdir(parents=True,exist_ok=True);records=[]
    with sync_playwright() as p:
        b=p.chromium.launch(channel=browser,headless=True);page=b.new_page(viewport={'width':1280,'height':800});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(url);page.wait_for_function('window.lastResult',timeout=120000);page.evaluate('window.validation.stable()');catalog=page.evaluate('window.catalog')
        for i,item in enumerate(catalog['models']):
            identifier=item['source_sha256'];receipt=home/(identifier+'.json')
            if receipt.exists():
                old=json.loads(receipt.read_text(encoding='utf8'))
                if old.get('validator')=='silhouette-v2' and old.get('optimized_sha256')==item['sha256'] and old.get('technical_passed'):records.append(old);continue
            try:
                original=page.evaluate('(id)=>window.validation.show(id,false)',identifier);a=page.evaluate('window.validation.sample(0.37)');mask_a=page.evaluate('window.validation.silhouette(0.37)');poses_a=[page.evaluate('(t)=>window.validation.pose(t)',t) for t in [0,.25,.5,.75,1.25]]
                optimized=page.evaluate('(id)=>window.validation.show(id,true)',identifier);b64=page.evaluate('window.validation.sample(0.37)');mask_b=page.evaluate('window.validation.silhouette(0.37)');poses_b=[page.evaluate('(t)=>window.validation.pose(t)',t) for t in [0,.25,.5,.75,1.25]]
                ia,ib=image(a),image(b64);ma=np.max(image(mask_a),axis=2)>128;mb=np.max(image(mask_b),axis=2)>128;union=ma|mb;iou=float(np.sum(ma&mb)/max(np.sum(union),1));mae=float(np.mean(np.abs(ia-ib)[union])) if union.any() else 255
                pose_error=max((float(np.max(np.abs(np.array(x)-np.array(y)))) if x else 0 for x,y in zip(poses_a,poses_b)),default=0)
                technical=original['meshes']==optimized['meshes'] and original['skinned']==optimized['skinned'] and original['animations']==optimized['animations'] and pose_error<.01
                visual=iou>=.97 and mae<=18
                Image.fromarray(np.concatenate([ia,ib],axis=1).astype('uint8')).save(home/(identifier+'.jpg'),quality=85)
                result={'id':identifier,'validator':'silhouette-v2','optimized_sha256':item['sha256'],'name':item['name'],'technical_passed':technical,'visual_threshold_passed':visual,'silhouette_iou':iou,'foreground_mae':mae,'pose_max_error':pose_error,'source':original,'optimized':optimized,'comparison':identifier+'.jpg'}
            except Exception as e:result={'id':identifier,'optimized_sha256':item['sha256'],'technical_passed':False,'error':str(e)}
            receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');records.append(result)
            if i%20==0:print(browser,'模型',i+1,'/',len(catalog['models']),flush=True)
        audio=page.evaluate('''async (items)=>{const context=new AudioContext();const result=[];for(const item of items){try{const buffer=await context.decodeAudioData(await(await fetch(item.url)).arrayBuffer());result.push({id:item.source_sha256,passed:true,seconds:buffer.duration,channels:buffer.numberOfChannels,sampleRate:buffer.sampleRate});}catch(e){result.push({id:item.source_sha256,passed:false,error:String(e)});}}await context.close();return result;}''',catalog['audio'])
        result={'browser':browser,'version':b.version,'condition':'独立无窗口，640x360渲染；全库串行加载，不代表普通电脑帧率验收','model_count':len(records),'technical_passed':sum(r.get('technical_passed',False) for r in records),'visual_threshold_passed':sum(r.get('visual_threshold_passed',False) for r in records),'models':records,'audio':audio,'errors':errors};(home/'汇总.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(browser,'完成',result['model_count'],result['technical_passed'],len(audio),flush=True);b.close()
    server.shutdown()
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chrome','msedge'],default='chrome');a=parser.parse_args();run(a.browser)
