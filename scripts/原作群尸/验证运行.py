"""独立浏览器核对GPU姿态、同屏数量、动作、画面及180秒新旧对照。"""
import argparse,json,time,sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48';URL='http://127.0.0.1:8797/'
sys.path.insert(0,str(ROOT/'scripts'))
from 验证丧尸群体测试 import NativeWindow

POSE_CHECK=r'''async()=>{
 const T=await import('/vendor/build/three.module.js'),{clone}=await import('/vendor/examples/jsm/utils/SkeletonUtils.js'),reports=[];
 for(const rig of crowdBench.actors().rigs){let maximum=0,exact=0;const root=clone(rig.asset.scene);let mesh;root.traverse(o=>{if(o.isSkinnedMesh)mesh=o;});const mixer=new T.AnimationMixer(root),pos=mesh.geometry.attributes.position,ids=mesh.geometry.attributes.skinIndex,w=mesh.geometry.attributes.skinWeight;
  for(const clip of rig.asset.animations){const c=rig.clips[clip.name];for(const fraction of[0,Math.floor(c.frames/2)/(c.frames-1),.371,.86,1]){
   mixer.stopAllAction();const action=mixer.clipAction(clip);action.setLoop(T.LoopOnce,1);action.clampWhenFinished=true;action.play();mixer.setTime(clip.duration*fraction);root.updateMatrixWorld(true);
   const f=fraction*(c.frames-1),a=Math.floor(f),b=Math.min(a+1,c.frames-1),alpha=f-a;
   for(let index=0;index<pos.count;index++){
    const input=new T.Vector3().fromBufferAttribute(pos,index).applyMatrix4(rig.base.bindMatrix),gpu=new T.Vector3();
    for(let j=0;j<4;j++){const weight=w.getComponent(index,j);if(!weight)continue;const bone=ids.getComponent(index,j),m=new T.Matrix4().fromArray(rig.values,((c.offset+a)*rig.width+bone*4)*4),n=new T.Matrix4().fromArray(rig.values,((c.offset+b)*rig.width+bone*4)*4);for(let k=0;k<16;k++)m.elements[k]=m.elements[k]*(1-alpha)+n.elements[k]*alpha;gpu.addScaledVector(input.clone().applyMatrix4(m),weight);}
    gpu.applyMatrix4(rig.base.bindMatrixInverse).applyMatrix4(rig.meshMatrix);const original=new T.Vector3();mesh.getVertexPosition(index,original);original.applyMatrix4(mesh.matrixWorld);const error=gpu.distanceTo(original);maximum=Math.max(maximum,error);if(Math.abs(f-Math.round(f))<1e-6)exact=Math.max(exact,error);
   }
  }}mixer.stopAllAction();mixer.uncacheRoot(root);mesh.skeleton.dispose();reports.push({id:rig.model.id,clips:rig.asset.animations.length,maximum,exact});
 }return reports;
}'''

def functional():
    folder=OUT/'功能核验'/str(time.time_ns());folder.mkdir(parents=True,exist_ok=False);result={'errors':[],'families':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda error:result['errors'].append(str(error)))
        for family in ['native','cartoon']:
            page.goto(URL+'?family='+family+'&headless=1');page.wait_for_function('window.crowdBench?.ready',timeout=90000)
            rows=[]
            for count in [500,650,800]:
                page.locator('#count').select_option(str(count));page.wait_for_function('(n)=>crowdBench.snapshot().row?.visible===n && crowdBench.snapshot().row.animated===n',arg=count,timeout=20000)
                snapshot=page.evaluate('crowdBench.snapshot()');rows.append({'count':count,'snapshot':snapshot});page.locator('#viewport canvas').screenshot(path=str(folder/f'{family}-{count}-总览.png'))
            pose=page.evaluate(POSE_CHECK)
            (folder/(family+'-全顶点姿态.json')).write_text(json.dumps({'scope':'各原片段5个时刻，全部顶点','reports':pose},ensure_ascii=False,indent=2),encoding='utf-8')
            assert max(r['exact'] for r in pose)<.0001,pose
            assert max(r['maximum'] for r in pose)<.005,pose
            page.locator('#count').select_option('500');page.locator('#mode').select_option('channel');page.wait_for_function('crowdBench.snapshot().row?.visible===500');page.locator('#viewport canvas').screenshot(path=str(folder/f'{family}-拥堵.png'))
            if family=='native':
                page.locator('#mode').select_option('animated');page.locator('#closeup').click();page.wait_for_timeout(500);page.locator('#viewport canvas').screenshot(path=str(folder/'native-近景.png'))
                page.locator('#attack').click();page.wait_for_timeout(1100);page.locator('#viewport canvas').screenshot(path=str(folder/'native-原生攻击.png'))
                page.locator('#death').click();page.wait_for_timeout(2600);page.locator('#viewport canvas').screenshot(path=str(folder/'native-原生死亡.png'))
            result['families'].append({'family':family,'counts':rows,'pose':pose})
        browser.close()
    result['passed']=not result['errors'];(folder/'功能核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');assert result['passed'];print(json.dumps({'passed':True,'counts':6,'models':15,'maxPoseError':max(r['maximum'] for f in result['families'] for r in f['pose'])},ensure_ascii=False),flush=True)

def performance(headless=False,counts=(500,650,800),family_filter='all',run_name=None):
    folder=OUT/'长测'/('无窗口' if headless else '前台')
    if run_name:folder=folder/run_name
    folder.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=headless,timeout=60000,args=['--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows']);page=browser.new_page(viewport={'width':1600,'height':1000});native=None if headless else NativeWindow(browser);activated=False
        if native:page.request.post(URL+'api/test-window',data={'pid':native.pid})
        for count in counts:
            for family in (['cartoon','native'] if family_filter=='all' else [family_filter]):
                name=f'{family}-{count}-180s';marker=folder/(name+'-完成.json')
                if marker.exists():
                    saved=json.loads(marker.read_text('utf-8'));source=json.loads((OUT/'样本'/(saved['archive']['file']+'.json')).read_text('utf-8'));current=page.request.get(URL+'manifest.json?family='+family).json()
                    if source['config']['code_sha256']!=current['code_sha256'] or {m['asset']['sha256'] for m in source['config']['models']}!={m['asset']['sha256'] for m in current['models']}:raise RuntimeError('既有样本输入已变，请指定全新--run，不能覆盖或混用')
                    continue
                page.goto(URL+'?family='+family+('&headless=1' if headless else ''));page.wait_for_function('window.crowdBench?.ready',timeout=90000);page.locator('#count').select_option(str(count));page.locator('#mode').select_option('open')
                if native and not activated:native.activate(page);activated=True
                page.locator('#sample').click();deadline=time.monotonic()+240;checks=[];last_note=time.monotonic()
                while True:
                    state=page.evaluate('crowdBench.status()');proof=native.proof() if native else None
                    if proof:checks.append({'epoch':time.time(),**proof})
                    if state['state']=='done' and state['archive'] is not None:break
                    if time.monotonic()>deadline:raise TimeoutError(name+'超出绝对截止时间')
                    if time.monotonic()-last_note>=30:print(name,'剩余',round(state['remaining']),'秒',flush=True);last_note=time.monotonic()
                    try:page.wait_for_function('crowdBench.status().state === "done" && crowdBench.status().archive !== null',timeout=1000)
                    except Exception:pass
                if state['archive'].get('error'):raise RuntimeError(state['archive']['error'])
                data=page.evaluate('crowdBench.result()');assert data['summary']['minVisible']==count and data['summary']['minAnimated']==count
                page.locator('#viewport canvas').screenshot(path=str(folder/(name+'.png')))
                record={'family':family,'count':count,'browser':browser.version,'headless':headless,'native_window_checks':checks,'archive':state['archive'],'summary':data['summary'],'source_sha256':data['config']['code_sha256'],'assets':[m['asset'] for m in data['config']['models']]}
                marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');print(name,json.dumps(record['summary'],ensure_ascii=False),flush=True)
        browser.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--performance',action='store_true');parser.add_argument('--headless',action='store_true');parser.add_argument('--counts',default='500,650,800');parser.add_argument('--family',choices=['all','native','cartoon'],default='all');parser.add_argument('--run');args=parser.parse_args()
    if args.performance:performance(args.headless,[int(x) for x in args.counts.split(',')],args.family,args.run)
    else:functional()
