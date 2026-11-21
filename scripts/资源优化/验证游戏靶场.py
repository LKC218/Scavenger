"""冻结靶场同镜头对照和限速冷加载；网络为CDP模拟，非公网测速。"""
import argparse,base64,importlib.util,io,json,pathlib,statistics,threading,time
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/游戏版第一版'
spec=importlib.util.spec_from_file_location('game_services',ROOT/'scripts/资源优化/游戏版服务.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def image(s):return np.asarray(Image.open(io.BytesIO(base64.b64decode(s.split(',')[1]))).convert('RGB')).astype(np.float32)
def run(browser,network=False):
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry()
 servers={'baseline':module.create_range(baseline=True)}
 for res in [256,512]:
  for level in [0,1,2]:servers[f'{res}-lod{level}']=module.create_range(res,level)
 for s in servers.values():threading.Thread(target=s.serve_forever,daemon=True).start()
 folder=OUT/'靶场验证'/browser;folder.mkdir(parents=True,exist_ok=True);result={'browser':browser,'condition':'当前开发机，独立无窗口，冻结相同源码，真实靶场光照与镜头；CDP限速不等于公网测试','visual':[],'network':[],'errors':[]}
 if network and (folder/'加载验证.json').exists():result=json.loads((folder/'加载验证.json').read_text('utf8'))
 with sync_playwright() as pw:
  b=pw.chromium.launch(channel=browser,headless=True);result['version']=b.version
  if network:
   for name in ['baseline','256-lod1','512-lod1']:
    for mbps in [10,50]:
     for repeat in range(3):
      if any(r['variant']==name and r['mbps']==mbps and r['repeat']==repeat+1 for r in result['network']):continue
      context=b.new_context(viewport={'width':1600,'height':1000});page=context.new_page();page.on('pageerror',lambda e:result['errors'].append(str(e)));cdp=context.new_cdp_session(page);cdp.send('Network.enable');cdp.send('Network.setCacheDisabled',{'cacheDisabled':True});cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':40,'downloadThroughput':mbps*1e6/8,'uploadThroughput':mbps*1e6/8,'connectionType':'wifi'})
      start=time.monotonic();page.goto(f'http://127.0.0.1:{servers[name].server_port}/',wait_until='domcontentloaded',timeout=120000);page.wait_for_function('window.range?.ready',timeout=180000);elapsed=time.monotonic()-start
      resources=page.evaluate('performance.getEntriesByType("resource").map(x=>({url:new URL(x.name).pathname,bytes:x.transferSize,encoded:x.encodedBodySize,duration:x.duration}))');manifest=page.evaluate('range.manifest');result['network'].append({'variant':name,'mbps':mbps,'repeat':repeat+1,'ready_seconds':elapsed,'model_bytes':sum(x['bytes'] for x in manifest['models'].values()),'transfer_bytes':sum(x['bytes'] for x in resources),'resources':resources,'audio_loaded':False});context.close();print(browser,'冷加载',name,mbps,repeat+1,round(elapsed,2),flush=True)
      (folder/'加载验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
  else:
   originals={}
   for name,server in servers.items():
    page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:result['errors'].append(str(e)));page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('window.range?.ready',timeout=90000)
    for kind in ['tank','heli','plane']:
     for quality in [640,480]:
      for span in [58,96]:
       for angle in [0,1.2]:
        for phase in [0.3,0.8]:
         item={'kind':kind,'quality':quality,'span':span,'angle':angle,'phase':phase};data=page.evaluate('''(p)=>{const {sim,view,commander}=range;sim.reset(30);sim.paused=true;sim.time=p.phase;const u=sim.units.find(u=>u.kind===p.kind);for(const [i,other] of sim.units.entries())if(other!==u){other.x=10000+i*30;other.z=10000;}u.x=0;u.z=60;u.y=p.kind==='tank'?0:p.kind==='heli'?35:120;u.yaw=p.phase;u.turretYaw=p.phase*.6;u.pitch=.1;sim.targets=[];sim.projectiles=[];view.setup(sim);view.quality=p.quality;view.resize();view.stopCamera();view.span=view.targetSpan=p.span;view.angle=view.targetAngle=p.angle;view.center.set(-u.y*140/165*Math.sin(p.angle),0,60-u.y*140/165*Math.cos(p.angle));view.updateCamera();view.sync(sim,new Set(),null,0);view.render();return {image:view.renderer.domElement.toDataURL('image/png'),perf:range.perf(),motion:Object.fromEntries(Object.entries(view.rigs[p.kind].motion).map(([k,v])=>[k,v.matrix.elements])),manifest:range.manifest};}''',item)
         pixels=image(data['image']);key=json.dumps(item,sort_keys=True);file=f'{name}-{kind}-{quality}-{span}-{angle}-{phase}.jpg';Image.fromarray(pixels.astype('uint8')).save(folder/file,quality=90)
         row={**item,'variant':name,'image':file,'triangles':data['perf']['triangles'],'draw_calls':data['perf']['drawCalls'],'motion':data['motion']}
         if name=='baseline':originals[key]=(pixels,data['motion'])
         else:
          a,motion=originals[key];diff=np.abs(a-pixels);mask=np.max(diff,axis=2)>3;h,w=diff.shape[:2];radius=round(w*.18);crop=diff[max(0,h//2-radius):min(h,h//2+radius),max(0,w//2-radius):min(w,w//2+radius)];motion_error=max(float(np.max(np.abs(np.array(motion[k])-np.array(data['motion'][k])))) for k in motion);row.update(frame_mae=float(np.mean(diff)),center_crop_mae=float(np.mean(crop)),motion_max_error=motion_error,changed_fraction=float(np.mean(mask)),changed_mae=float(np.mean(diff[mask])) if mask.any() else 0)
         result['visual'].append(row)
    page.close();print(browser,'实际靶场画面',name,flush=True)
   (folder/'画面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
  b.close()
 for s in servers.values():s.shutdown()
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--browser',choices=['chrome','msedge'],default='chrome');p.add_argument('--network',action='store_true');a=p.parse_args();run(a.browser,a.network)
