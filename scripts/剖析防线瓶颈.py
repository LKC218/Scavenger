"""对冻结V003使用独立浏览器计时与CPU抽样；输出独立目录，不覆盖通过回执。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

BASE=Path(__file__).resolve().parents[1]/'local-data/原型/丧尸防线/V003-01a1a2af/诊断'

INSTRUMENT=r'''() => {
 const p=window.defenseProfile={rows:[],rebuild:[],sum:{},enabled:true};
 const wrap=(object,name,key)=>{const fn=object[name];object[name]=function(...args){const begin=performance.now();try{return fn.apply(this,args);}finally{const ms=performance.now()-begin;p.sum[key]=(p.sum[key]??0)+ms;if(key==='rebuild')p.rebuild.push({time:range.sim.time,ms,count:args[0]?.length});}}};
 wrap(range.sim,'step','sim');wrap(range.view,'sync','sync');wrap(range.view,'render','render');wrap(range.view.actors,'update','actors');wrap(range.view.actors,'rebuild','rebuild');
 let last=performance.now();function tick(now){if(!p.enabled)return;p.rows.push({dt:now-last,...p.sum,time:range.sim.time,wave:range.sim.waveSerial,alive:range.sim.stats().alive,calls:range.view.renderer.info.render.calls,textures:range.view.renderer.info.memory.textures,corpses:range.view.actors.corpses});p.sum={};last=now;requestAnimationFrame(tick)}requestAnimationFrame(tick);
}'''

def main(seconds,port):
    if seconds<=10:raise ValueError('诊断时长须大于十秒预热窗口')
    out=BASE/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1600,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{port}/');page.wait_for_function('window.range?.ready',timeout=60000)
        manifest=page.evaluate('range.manifest')
        if manifest.get('version')!='V003' or not manifest.get('frozen_source'):raise ValueError('只对冻结V003执行当前诊断')
        tag=manifest['version'];out.mkdir(parents=True,exist_ok=False)
        page.locator('#defense-panel details summary').click();page.locator('#defense-force').select_option('thirty');page.locator('#zombie-count').fill('500');page.locator('#defense-endless').check();page.locator('#defense-apply').click()
        page.evaluate(INSTRUMENT);cdp=page.context.new_cdp_session(page);cdp.send('Profiler.enable');cdp.send('Profiler.setSamplingInterval',{'interval':1000});cdp.send('Profiler.start')
        page.locator('#defense-start').click();started=time.monotonic();deadline=started+seconds
        while time.monotonic()<deadline:page.wait_for_timeout(min(900,max(0,(deadline-time.monotonic())*1000)))
        cpu=cdp.send('Profiler.stop')['profile'];record=page.evaluate('defenseProfile.enabled=false;({rows:defenseProfile.rows,rebuild:defenseProfile.rebuild,perf:range.perf()})');record.update(browser=browser.version,errors=errors,source=manifest['source_identity'],source_sha256=manifest['source_sha256'],version=tag,port=port,seconds=time.monotonic()-started,diagnostic=True)
        (out/(tag+'-阶段计时.json')).write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8');(out/(tag+'-CPU抽样.json')).write_text(json.dumps(cpu,ensure_ascii=False),encoding='utf-8')
        rows=[r for r in record['rows'] if r['time']>10]
        if not rows:raise ValueError('缺少预热后的诊断数据')
        result={'version':tag,'source_sha256':manifest['source_sha256'],'port':port,'errors':errors,'diagnostic':True}
        for key in ['dt','sim','sync','render','actors']:
            a=sorted(r.get(key,0) for r in rows);result[key]={'p50':a[len(a)//2],'p95':a[int(len(a)*.95)],'max':max(a)}
        nodes=sorted(cpu['nodes'],key=lambda n:n.get('hitCount',0),reverse=True)[:15];result['hotspots']=[{'name':n['callFrame']['functionName'],'url':n['callFrame']['url'],'hits':n.get('hitCount',0)} for n in nodes];result['rebuild']=record['rebuild'];result['maxCalls']=max(r['calls'] for r in rows)
        (out/(tag+'-剖析摘要.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'output':out.as_posix(),**result},ensure_ascii=False));assert not errors,errors;browser.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=55);p.add_argument('--port',type=int,default=8794);a=p.parse_args();main(a.seconds,a.port)
