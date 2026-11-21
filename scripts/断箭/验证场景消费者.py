"""串行验证场景增量前后的既有模型、音效、靶场和十三款群体；不作性能验收。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from 地图执行记录 import ROOT, atomic_json, fingerprint
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'scripts/资源库')]
from 统一资源 import current
from 启动正式资料库 import create_server


def load(relative, name):
    spec=importlib.util.spec_from_file_location(name, ROOT/relative)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def run(args):
    output=args.directory/'消费者回归'/args.revision;output.mkdir(parents=True,exist_ok=False)
    pointer,catalog,routes,index=current()
    before=fingerprint(ROOT/'local-data/资源库/当前版本.json')
    old_assets={r['id']:digest(r) for group in ['models','audio'] for r in catalog[group] if not r.get('scene_id')}
    protected_metadata={name:sha for name,sha in pointer['metadata'].items() if name not in
        ['资源索引.json','资源路由.json','模型登记.json','主工程.json','场景登记.json','保留策略.json']}
    if args.baseline:
        baseline=json.loads(args.baseline.read_text('utf-8'))
        assert baseline['passed'] and baseline['old_assets']==old_assets,'既有模型或声音登记变化'
        assert baseline['protected_metadata']==protected_metadata,'原件或运行映射变化'
    result={'passed':False,'scope':'真实浏览器功能回归，不继承帧时间、听审或外观验收',
        'pointer_before':before,'revision':pointer['revision'],'old_assets':old_assets,
        'protected_metadata':protected_metadata,'browsers':[],'started_epoch':time.time(),
        'source_program':fingerprint(Path(__file__))}
    services={};threads=[]
    def start(name,server):
        services[name]=server;thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start();threads.append(thread);return f'http://127.0.0.1:{server.server_port}/'
    try:
        if args.library_url:
            url=args.library_url.rstrip('/')+'/'
            assert url.startswith('http://127.0.0.1:')
            with urlopen(url+'api/status') as response:status=json.load(response)
            assert status['app']=='scavenger-library' and status['revision']==pointer['revision']
        else:
            manifest=json.loads(args.manifest.read_text('utf-8')) if args.manifest else None
            url=start('library',create_server(0,manifest))
            result['candidate_manifest']=fingerprint(args.manifest) if args.manifest else None
        # 在原始HTTP JSON侧比较登记；浏览器往返会将1.0规范化为1，不能据此判成媒体变化。
        with urlopen(url+'api/catalog') as response:api_catalog=json.load(response)
        api_assets={r['id']:digest(r) for group in ['models','audio'] for r in api_catalog[group] if r['id'] in old_assets}
        assert api_assets==old_assets,'候选HTTP接口改变了既有登记'
        safe=load('scripts/资源优化/游戏版服务.py','scene_consumer_ports')
        range_url=start('range',load('scripts/预览载具靶场.py','scene_consumer_range').create_server(safe.safe_port(0)))
        horde_url=start('horde',load('scripts/预览丧尸群体测试.py','scene_consumer_horde').create_server(safe.safe_port(0)))
        with sync_playwright() as pw:
            for channel in ['chrome','msedge']:
                evidence=output/channel;evidence.mkdir()
                row={'browser':channel,'errors':[],'request_failures':[],'passed':False}
                pending=set()
                browser=pw.chromium.launch(channel=channel,headless=False)
                try:
                    page=browser.new_page(viewport={'width':1280,'height':720})
                    def attach(p):
                        p.on('pageerror',lambda error:row['errors'].append(str(error)))
                        p.on('console',lambda message:row['errors'].append(message.text) if message.type=='error' else None)
                        p.on('response',lambda r:row['errors'].append(f'{r.status} {r.url}') if r.status>=400 else None)
                        p.on('request',lambda request:pending.add(request.url))
                        p.on('requestfinished',lambda request:pending.discard(request.url))
                        p.on('requestfailed',lambda request:(pending.discard(request.url),row['request_failures'].append({'url':request.url,'failure':request.failure})))
                    attach(page);page.goto(url)
                    page.wait_for_selector('#model-list [data-model]')
                    served=page.evaluate('fetch("/api/catalog").then(r=>r.json())')
                    assert len(served.get('scenes',[]))==1
                    assert set(old_assets)<={x['id'] for g in ['models','audio'] for x in served[g]}
                    page.locator('#global-search').fill('M1A2')
                    page.locator('[data-model="model:US_M1A2"]').click()
                    assert page.locator('#spec-row [data-spec]').count()==3
                    for spec in range(3):
                        page.locator(f'#spec-row [data-spec="{spec}"]').click();page.locator('#load-3d').click()
                        page.wait_for_function('document.querySelector("#viewer-state").textContent.startsWith("模型已就绪")&&!document.querySelector("#canvas").hidden',timeout=60000)
                    page.screenshot(path=str(evidence/'既有模型三档.png'));row['model_specs']=3
                    sounds=[next(x for x in served['audio'] if x['name']==name) for name in
                        ['US_AbramsM1A2_Engine','Death_Exp_Veh_AmmoDetonation','US_AbramsM1A2_Engine（当前无缝循环）']]
                    row['decoded_audio']=page.evaluate('''async(items)=>{const c=new AudioContext(),rows=[];try{for(const item of items){const r=await fetch(item.url);if(!r.ok)throw Error('音频HTTP失败');const b=await c.decodeAudioData(await r.arrayBuffer());rows.push({id:item.id,duration:b.duration,channels:b.numberOfChannels});}return rows;}finally{await c.close();}}''',sounds)
                    assert all(x['duration']>0 for x in row['decoded_audio'])
                    page.locator('#global-search').fill('');page.locator('#audio-tab').click()
                    page.evaluate('document.querySelector("#player").volume=.1')
                    page.locator('[data-play]:visible').first.click()
                    page.wait_for_function('document.querySelector("#player").currentTime>0',timeout=15000)
                    row['playback']=True;page.close()
                    page=browser.new_page();attach(page);page.goto(range_url)
                    page.wait_for_function('window.range?.ready',timeout=60000)
                    assert page.evaluate('Object.values(range.manifest.models).every(x=>x.asset_library==="统一资源库")')
                    page.locator('#volume').evaluate('(e)=>{e.value="25";e.dispatchEvent(new Event("input",{bubbles:true}));}')
                    page.locator('#sound').click()
                    page.wait_for_function('range.mixer.active&&Object.values(range.manifest.audio).every(x=>range.audio.cache.has(x.audio_id))',timeout=60000)
                    page.locator('#unit-count').select_option('10');page.locator('#load-mode').select_option('combat')
                    page.wait_for_function('range.sim.shots>0&&range.sim.hits>0',timeout=40000)
                    row['range']=page.evaluate('({shots:range.sim.shots,hits:range.sim.hits,audio:range.audio.stats(),volume:range.audio.masterVolume,models:range.manifest.models,source_audio:Object.keys(range.manifest.audio).length})')
                    assert row['range']['volume']==.25
                    page.screenshot(path=str(evidence/'既有靶场交战.png'));page.close()
                    page=browser.new_page();attach(page);page.goto(horde_url)
                    page.wait_for_function('window.horde?.ready',timeout=60000)
                    snapshot=page.evaluate('horde.snapshot()')
                    assert snapshot['config']['delivery']['library_revision']==pointer['revision'] and len(snapshot['config']['models'])==13
                    page.wait_for_function('(v)=>horde.snapshot().animation.vertex.some((x,i)=>Math.abs(x-v[i])>.001)',arg=snapshot['animation']['vertex'])
                    row['horde']={'roles':13,'animation_changed':True,'delivery':snapshot['config']['delivery']}
                    page.screenshot(path=str(evidence/'十三款群体.png'));page.close()
                    assert not row['errors'],row['errors'];row['passed']=True
                    print(channel+'：既有三档模型、音效解码播放、靶场交战、十三款动态群体通过',flush=True)
                except BaseException as error:
                    row['failure']=repr(error)
                    row['pending_requests']=sorted(pending)
                    if not page.is_closed():
                        row['failure_document']=page.evaluate('({url:location.href,timeOrigin:performance.timeOrigin,discarded:document.wasDiscarded,resources:performance.getEntriesByType("resource").map(x=>({name:x.name,duration:x.duration,bytes:x.encodedBodySize}))})')
                        page.screenshot(path=str(evidence/'失败.png'))
                    raise
                finally:
                    browser.close();result['browsers'].append(row);atomic_json(output/'验证结果.json',result)
        assert fingerprint(ROOT/'local-data/资源库/当前版本.json')['sha256']==before['sha256'],'回归期间指针变化'
        result.update(passed=True,finished_epoch=time.time())
    finally:
        for server in services.values():
            server.shutdown();server.server_close()
            if hasattr(server,'observer'):server.observer.close()
        for thread in threads:thread.join()
        atomic_json(output/'验证结果.json',result)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--revision',required=True)
    parser.add_argument('--manifest',type=Path);parser.add_argument('--library-url');parser.add_argument('--baseline',type=Path)
    args=parser.parse_args();args.directory=args.directory.resolve();args.directory.relative_to(ROOT/'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):raise ValueError('修订名无效')
    run(args)
