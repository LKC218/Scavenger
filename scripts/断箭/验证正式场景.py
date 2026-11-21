"""核对当前正式场景的全部HTTP入口、查询、工程定位及双浏览器区域预览。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen, Request

from playwright.sync_api import sync_playwright
from 地图执行记录 import ROOT, atomic_json, fingerprint
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import current
from 启动正式资料库 import resolve_reveal


def run(args):
    output=args.directory/'正式入口核验'/args.revision;output.mkdir(parents=True,exist_ok=False)
    pointer,catalog,routes,index=current();identity=fingerprint(ROOT/'local-data/资源库/当前版本.json')
    scene=next(x for x in catalog['scenes'] if x['id']=='scene:BA_Airbase_full_v001')
    record=json.loads((index/'场景登记.json').read_text('utf-8'))[scene['id']]
    result={'passed':False,'pointer':identity,'revision':pointer['revision'],'http':[],'browsers':[],
        'scope':'正式入口功能与依赖可达性；不重算已冻结的载具性能结论','program':fingerprint(Path(__file__))}
    def get(path):
        with urlopen(args.url.rstrip('/')+path,timeout=120) as response:return json.load(response)
    status=get('/api/status');assert status['app']=='scavenger-library' and status['revision']==pointer['revision']
    assert status['summary']['scenes']==1 and scene['technical_status']==scene['ai_visual_status']=='passed' and scene['human_acceptance']=='待人工验收'
    assert get('/api/search?type=scene&id='+quote(scene['id'],safe=''))['total']==1
    assert get('/api/search?related_to='+quote(scene['id'],safe='')+'&limit=200')['total']==133
    result['queries']={'scene':1,'related_assets':133,'summary':status['summary']}
    for role,url in [('runtime',scene['scene_url']),('blend',scene['blend_url'])]:
        path=resolve_reveal(catalog,{key:{**row,'path':str(ROOT/row['path'])} for key,row in routes.items()},scene['id'],url)
        result.setdefault('file_locations',{})[role]=fingerprint(path)
    for kind,extra,total in [('scene',['--id',scene['id']],1),('all',['--related-to',scene['id'],'--limit','200'],133)]:
        process=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'scripts/断箭/查询资料库.py'),'--type',kind,*extra,'--json'],capture_output=True,text=True,encoding='utf-8',check=True)
        payload=json.loads(process.stdout);assert payload['revision']==pointer['revision'] and payload['total']==total
    def check(item):
        key,row=item
        with urlopen(Request(args.url.rstrip('/')+quote(key,safe='/:'),method='HEAD'),timeout=30) as response:
            size=int(response.headers['Content-Length']);assert response.status==200 and size==row['bytes'],key
        return {'url':key,'bytes':size,'expected_sha256':row['sha256'],'passed':True}
    try:
        # 只在HTTP阶段使用4路请求；浏览器及性能作业不与此阶段并行。
        if args.http_proof:
            previous=json.loads(args.http_proof.read_text('utf-8'))
            assert previous['pointer']['sha256']==identity['sha256'] and len(previous['http'])==len(routes)
            assert {r['url'] for r in previous['http']}==set(routes)
            assert all(r['passed'] and r['bytes']==routes[r['url']]['bytes'] and r['expected_sha256']==routes[r['url']]['sha256'] for r in previous['http'])
            result['http']=previous['http'];result['http_reused_from']=fingerprint(args.http_proof)
        else:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for n,row in enumerate(pool.map(check,routes.items()),1):
                    result['http'].append(row)
                    if n%1024==0:
                        atomic_json(output/'执行状态.json',{'status':'HTTP逐项核验','done':n,'total':len(routes),'epoch':time.time()})
                        print(f'正式HTTP：{n}/{len(routes)}',flush=True)
        for extension in ['.glb','.ktx2','.wasm']:
            key,row=next((key,row) for key,row in routes.items() if key.endswith(extension))
            with urlopen(Request(args.url.rstrip('/')+quote(key,safe='/:'),headers={'Range':'bytes=0-63'})) as response:
                body=response.read();assert response.status==206 and body==(ROOT/row['path']).read_bytes()[:64]
        with sync_playwright() as pw:
            for channel in ['chrome','msedge']:
                folder=output/channel;folder.mkdir();row={'browser':channel,'errors':[],'passed':False}
                browser=pw.chromium.launch(channel=channel,headless=False)
                try:
                    page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
                    requests=[];page.on('request',lambda request:requests.append(request.url))
                    page.on('pageerror',lambda error:row['errors'].append(str(error)))
                    page.on('console',lambda message:row['errors'].append(message.text) if message.type=='error' else None)
                    page.on('response',lambda response:row['errors'].append(f'{response.status} {response.url}') if response.status>=400 else None)
                    page.goto(args.url);page.wait_for_selector('[data-category="scene"]')
                    assert not any('.glb' in url for url in requests),'列表预载了GLB'
                    # 包内真实类的只读探针，观察正常界面创建的实例；销毁即移除观察引用。
                    page.evaluate('''async(card)=>{const url=new URL(card.scene_url,location.href),manifest=await(await fetch(url)).json(),base=new URL(manifest.package_base||'.',url);const {StreamingSceneViewer}=await import(new URL(manifest.viewer_module,base));const load=StreamingSceneViewer.prototype.load,dispose=StreamingSceneViewer.prototype.dispose;window.sceneProbeDisposed=0;StreamingSceneViewer.prototype.load=async function(...args){const result=await load.apply(this,args);if(result)window.sceneProbe=()=>({stats:{...this.map.stats},water:this.map.visibleWaterMeshes().length,internal:[this.renderer.domElement.width,this.renderer.domElement.height],height:this.view().height});return result;};StreamingSceneViewer.prototype.dispose=function(){window.sceneProbe=null;window.sceneProbeDisposed++;return dispose.call(this);};}''',scene)
                    page.locator('[data-category="scene"]').click();page.locator(f'[data-model="{scene["id"]}"]').click();page.locator('#load-3d').click()
                    page.wait_for_function('window.sceneProbe&&!sceneProbe().stats.pending',timeout=180000)
                    row['near']=page.evaluate('sceneProbe()');assert row['near']['internal'][0]==640 and not row['near']['stats']['lastError']
                    page.screenshot(path=str(folder/'正式机场近景.png'))
                    page.locator('[data-scene-action="overview"]').click()
                    page.wait_for_function('sceneProbe().stats.sceneLevel==="far"&&!sceneProbe().stats.pending',timeout=180000)
                    row['overview']=page.evaluate('sceneProbe()');assert row['overview']['water']==2 and row['overview']['height']<=9000.01
                    assert len([url for url in requests if '.glb' in url])<5313
                    page.screenshot(path=str(folder/'正式全图总览.png'))
                    page.locator('[data-scene-action="home"]').click()
                    page.wait_for_function('sceneProbe().stats.sceneLevel==="near"&&!sceneProbe().stats.pending',timeout=90000)
                    page.locator('[data-category="tree"]').click();page.locator('#model-list [data-model]').first.click();page.locator('#load-3d').click()
                    page.wait_for_function('document.querySelector("#viewer-state").textContent.startsWith("模型已就绪")',timeout=60000)
                    assert page.evaluate('window.sceneProbe===null&&sceneProbeDisposed>0') and page.locator('#canvas canvas').count()==1
                    assert not row['errors'],row['errors'];row['passed']=True
                    print(channel+'：正式场景、水面、近远景及单体切换通过',flush=True)
                except BaseException as error:
                    row['failure']=repr(error)
                    if not page.is_closed():
                        row['failure_document']=page.evaluate('({url:location.href,state:document.querySelector("#viewer-state")?.textContent,probe:typeof sceneProbe==="function"?sceneProbe():null})')
                        page.screenshot(path=str(folder/'失败.png'))
                    raise
                finally:browser.close();result['browsers'].append(row)
        assert fingerprint(ROOT/'local-data/资源库/当前版本.json')['sha256']==identity['sha256']
        result.update(passed=True,http_complete=len(result['http'])==len(routes),finished_epoch=time.time())
    finally:atomic_json(output/'验证结果.json',result)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--revision',required=True);parser.add_argument('--url',default='http://127.0.0.1:8767/')
    parser.add_argument('--http-proof',type=Path,help='仅复用同一指针且逐路由仍匹配的已完成HTTP部分')
    args=parser.parse_args();args.directory=args.directory.resolve();args.directory.relative_to(ROOT/'local-data/断箭/批次')
    if not args.url.startswith('http://127.0.0.1:') or not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):raise ValueError('入口或修订无效')
    run(args)
