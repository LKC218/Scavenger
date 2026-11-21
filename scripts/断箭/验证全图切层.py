"""百单位暂停场景五次近景／总览往返，检查原预算与回收，单列操作系统进程内存。"""
import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from 验证全图故障 import VIEW
from 验证全图回访 import process_memory


def run(args):
    output=args.directory/'跨层往返验证'/args.run/args.browser
    output.mkdir(parents=True,exist_ok=False)
    result={'browser':args.browser,'errors':[],'cycles':[],
        'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'100单位暂停场景五次近景／总览往返及逐进程内存；不替代180秒回访或正式帧时间'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=args.browser,headless=False)
        page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
        cdp=browser.new_browser_cdp_session()
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        page.on('console',lambda message:result['errors'].append(message.text) if message.type=='error' else None)
        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.bring_to_front();page.wait_for_function('window.range?.ready',timeout=90000)
            page.evaluate('document.title="Airbase自动切层核验 · 完成后自动关闭"')
            result['identity']=page.evaluate('fetch("/api/status").then(r=>r.json())')
            result['version']=browser.version;result['gpu']=cdp.send('SystemInfo.getInfo')['gpu']
            freeze=args.directory/('浏览器源码-'+result['identity']['revision'])/'服务身份.json'
            result['service_freeze_sha256']=hashlib.sha256(freeze.read_bytes()).hexdigest()
            page.evaluate('()=>range.configureBenchmark({count:100,mode:"static",composition:{tank:80,heli:16,plane:4},span:140,center:{x:4137.5,z:3912.5}})')
            page.evaluate('range.sim.paused=true')
            for cycle in range(1,6):
                entry={'cycle':cycle}
                for name,span in [('总览',12150),('近景',140)]:
                    state=page.evaluate(VIEW,{'x':4137.5,'z':3912.5,'span':span})
                    page.wait_for_function('!range.externalMap.stats.pending&&!range.externalMap.stats.requests&&!range.externalMap.stats.transcodes&&!range.externalMap.stats.dataPending',timeout=90000)
                    assert not state['mapStats']['lastError'] if 'lastError' in state['mapStats'] else True
                    assert not state['mapStats']['errors'],state['mapStats']['errors']
                    assert state['totalUnits']==100
                    s=state['mapStats']
                    assert s['visibleChunks']<=49 and s['decodedBytes']<=256*1048576 and s['simulationBytes']<=64*1048576
                    assert s['geometryBytes']<=192*1048576 and state['textureEstimateBytes']<=256*1048576
                    if name=='近景':
                        assert not s['overview'] and s['visibleChunks']>0
                        page.evaluate('range.externalMap.compact()')
                        page.context.new_cdp_session(page).send('HeapProfiler.collectGarbage')
                    entry[name]={'state':page.evaluate('range.perf()'),'process_memory':process_memory(cdp)}
                    if cycle in (1,5):page.screenshot(path=str(output/(f'第{cycle}次-'+name+'.png')))
                result['cycles'].append(entry)
                (output/'进行中.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
                print(args.browser+'：第'+str(cycle)+'次跨层往返及预算通过',flush=True)
            second=result['cycles'][1]['近景']['state'];fifth=result['cycles'][4]['近景']['state']
            checks={key:fifth[key]<=second[key]*1.05 for key in ['geometries','textures']}
            checks.update({key:fifth['mapStats'][key]<=second['mapStats'][key]*1.05 for key in ['decodedBytes','geometryBytes','textureBytes','simulationBytes']})
            checks['jsHeap']=fifth['jsHeap']-second['jsHeap']<=max(16*1048576,second['jsHeap']*.1)
            result['growth_checks']=checks
            assert all(checks.values()),checks
            assert not result['errors'],result['errors']
            result['passed']=True
        except BaseException as error:
            result.update(passed=False,exception=repr(error))
            if not page.is_closed():page.screenshot(path=str(output/'失败.png'))
            raise
        finally:
            browser.close();(output/'核验凭证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--browser',choices=['chrome','msedge'],required=True)
    parser.add_argument('--port',type=int,default=8784)
    run(parser.parse_args())
