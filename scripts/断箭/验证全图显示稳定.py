"""实际键鼠移动及连续缩放时逐帧检查总览回切，并保存可见浏览器录像。"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright


def run(args):
    output=args.directory/'显示稳定验证'/args.run/args.browser
    output.mkdir(parents=True,exist_ok=False)
    result={'browser':args.browser,'errors':[],'phases':[],
            'verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope':'连续小范围移动与近景缩放的显示稳定检查；跨层切换、长回访及正式性能另验'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=args.browser,headless=False)
        context=browser.new_context(viewport={'width':1280,'height':720},device_scale_factor=1,
                                    record_video_dir=str(output/'录像'),record_video_size={'width':1280,'height':720})
        page=context.new_page()
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready',timeout=90000)
            result['identity']=page.evaluate('fetch("/api/status").then(r=>r.json())')
            result['version']=browser.version
            page.evaluate('()=>range.configureBenchmark({count:0,mode:"static",composition:{tank:0,heli:0,plane:0},span:140,center:{x:0,z:85}})')
            page.evaluate('range.sim.paused=true')
            page.wait_for_function('!range.externalMap.stats.pending')
            field=page.locator('#battlefield');box=field.bounding_box()
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
            for name,key,wheel in [('向右平移','d',0),('向左平移','a',0),('连续拉近',None,-20),('连续拉远',None,20)]:
                page.wait_for_function('!range.externalMap.stats.pending',timeout=90000)
                page.evaluate('''()=>{window.stability={active:true,rows:[]};const tick=()=>{if(!stability.active)return;const s=range.externalMap.stats;stability.rows.push({ms:performance.now(),overview:s.overview,pending:s.pending,visible:s.visibleChunks,commits:s.displayCommits??0,fallbacks:s.overviewFallbacks??0,decoded:s.decodedBytes,geometry:s.geometryBytes,textures:s.textureBytes});requestAnimationFrame(tick);};requestAnimationFrame(tick);}''')
                if key:page.keyboard.down(key)
                started=time.time();next_wheel=started
                while time.time()-started<3:
                    if wheel and time.time()>=next_wheel:
                        page.mouse.wheel(0,wheel);next_wheel=time.time()+.15
                    page.wait_for_timeout(40)
                if key:page.keyboard.up(key)
                page.wait_for_timeout(300)
                page.evaluate('stability.active=false')
                rows=page.evaluate('stability.rows')
                entry={'name':name,'frames':len(rows),'overview_frames':sum(bool(r['overview']) for r in rows),
                       'fallbacks':rows[-1]['fallbacks']-rows[0]['fallbacks'],'rows':rows}
                result['phases'].append(entry)
                page.screenshot(path=str(output/(name+'.png')))
                assert len(rows)>30 and entry['overview_frames']==0,entry
                assert all(r['visible']<=49 and r['decoded']<=256*1048576 and r['geometry']<=192*1048576 and r['textures']<=256*1048576 for r in rows)
            assert not result['errors'],result['errors']
            result['passed']=True
        except BaseException as error:
            result.update(passed=False,exception=repr(error)[:4000])
            page.screenshot(path=str(output/'失败.png'))
            raise
        finally:
            context.close();browser.close()
            (output/'核验凭证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--browser',choices=['chrome','msedge'],required=True)
    parser.add_argument('--port',type=int,default=8784)
    run(parser.parse_args())
