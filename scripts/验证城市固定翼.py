"""独立浏览器真实输入和串行分档采样；不占用工程师已有浏览器。"""
import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/原型/载具靶场/验证/城市修订1'


def run(channel,performance=False,audio_only=False):
    OUT.mkdir(parents=True,exist_ok=True)
    result={'channel':channel,'headless':True,'viewport':[1600,1000],'checks':[],'samples':[],'errors':[]}
    result['source_identity']=[{'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((ROOT/'src/载具靶场').glob('*')) if p.suffix in ['.mjs','.html','.css']]
    file=OUT/(channel+('-声音链路.json' if audio_only else '-性能.json' if performance else '-固定翼.json'))
    def save():file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    def passed(name):result['checks'].append(name);save();print(channel+'：'+name,flush=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        result['version']=browser.version
        page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
        page.on('response',lambda r:result['errors'].append(str(r.status)+' '+r.url) if r.status>=400 else None)
        def wait(expression,timeout=15000):page.wait_for_function('() => '+expression,timeout=timeout)
        def value(expression):return page.evaluate('() => '+expression)
        def overview():page.locator('#overview').click();wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
        def point(x,z,y=0):
            q=page.evaluate('(p)=>range.view.project(p)',{'x':x,'y':y,'z':z});b=page.locator('#battlefield').bounding_box()
            assert 0<q['x']<b['width'] and 0<q['y']<b['height'],q
            return b['x']+q['x'],b['y']+q['y']
        def drag(mode):
            page.keyboard.press(mode);a=point(240,360);b=point(240,100)
            page.mouse.move(*a);page.mouse.down();page.mouse.move(*b,steps=12);page.mouse.up()
        try:
            page.goto('http://127.0.0.1:8768/',wait_until='networkidle');wait('window.range?.ready',40000)
            assert value('range.sim.units.length')==30
            assert value('range.mixer.runtime.masterVolume')==.7
            assert value('range.perf().buildings')==96
            passed('三模型与13声音清单、30单位和96建筑加载')
            if not performance:
                result['audio_proof']=page.evaluate('''async()=>{
                  const {createMix}=await import('/声音.mjs');
                  async function render(volume,amplitude){const c=new OfflineAudioContext(2,24000,48000),gain=c.createGain();gain.gain.value=volume;createMix(c,gain);const buffer=c.createBuffer(2,24000,48000);for(let channel=0;channel<2;channel++){const a=buffer.getChannelData(channel);for(let i=0;i<a.length;i++)a[i]=Math.sin(i/48000*220*Math.PI*2)*amplitude*(channel?-.7:1);}const source=c.createBufferSource();source.buffer=buffer;source.connect(gain);source.start();const output=await c.startRendering();let peak=0,sum=0;for(let ch=0;ch<2;ch++)for(const value of output.getChannelData(ch)){peak=Math.max(peak,Math.abs(value));sum+=value*value;}return{peak,rms:Math.sqrt(sum/(output.length*2))};}
                  const low=await render(.35,.1),high=await render(.7,.1),dense=await render(.7,30);return{low,high,dense,gainDb:20*Math.log10(high.rms/low.rms)};
                }''')
                proof=result['audio_proof'];assert proof['dense']['peak']<=.951 and proof['dense']['rms']>.1,proof
                assert abs(proof['gainDb']-6.0206)<.02,proof
                passed('实际混音链离线逐样本：输出非静音、密集峰值受限、低电平增益6.02dB')
            if audio_only:
                result['passed']=True;save();return result
            if performance:
                page.locator('#sound').click();wait('range.mixer.active && range.audio.stats().cached===13')
                # 每项独立重置；所有采样只通过界面启动，使用页面真实时间戳。
                cases=[(50,'static',60,True,640),(100,'static',60,True,640),(100,'move',60,True,640),(100,'combat',180,True,640),(200,'combat',60,True,640),(500,'combat',60,True,640),(100,'combat',60,False,640),(100,'combat',60,True,480)] if channel=='chrome' else [(100,'combat',180,True,640)]
                for count,mode,seconds,sound,quality in cases:
                    page.locator('#unit-count').select_option(str(count));page.locator('#load-mode').select_option(mode);page.locator('#reset').click()
                    page.locator('#quality').select_option(str(quality))
                    if value('range.mixer.active')!=sound:page.locator('#sound').click();wait('!document.querySelector("#sound").disabled')
                    overview();page.keyboard.press('3')
                    page.locator('#bench'+str(seconds)).click();wait('!!range.benchmark.run')
                    wait('!range.benchmark.run',int((seconds+30)*1000))
                    sample=value('range.benchmark.last');sample['label']=f'{count}-{mode}-{quality}-声音{sound}';sample['final']=value('range.perf()')
                    sample['anchors']=value('({registered:range.mixer.anchors.size,inScene:range.view.scene.children.filter(o=>o.userData.expires!==undefined).length})')
                    assert sample['anchors']['registered']==sample['anchors']['inScene'],sample['anchors']
                    if mode=='combat':
                        assert sample['timeline'][-1]['shots']>sample['timeline'][0]['shots']+20
                        assert sample['timeline'][-1]['hits']>sample['timeline'][0]['hits']
                    result['samples'].append(sample);save()
                    page.screenshot(path=str(OUT/(channel+'-'+sample['label']+'.png')))
                    passed(sample['label']+f'：P95 {sample["p95"]:.2f}ms，{sample["frames"]}帧')
            else:
                # 清晰UI控制不透传；空间音频预算与默认音量。
                page.locator('#volume').focus();page.keyboard.press('Home');page.keyboard.press('ArrowRight');assert value('range.audio.masterVolume')==.01
                page.keyboard.press('End');assert value('range.audio.masterVolume')==1
                page.locator('#volume').fill('70');page.locator('#volume').dispatch_event('input')
                page.locator('#sound').click();wait('range.mixer.active && range.audio.stats().cached===13')
                assert value('range.sim.shots')==0;passed('音量滑杆、手势解锁与UI不透传')
                page.keyboard.press('4');assert value('range.commander.ids().length')==1
                page.keyboard.press('c');wait('!range.view.centerTarget');page.screenshot(path=str(OUT/(channel+'-A10近景.png')))
                overview();page.keyboard.press('k');a=point(240,360);b=point(240,100);page.mouse.move(*a);page.mouse.down();page.mouse.move(*b,steps=8);page.keyboard.press('Escape');page.mouse.up()
                assert value('range.sim.units.find(u=>u.kind==="plane").job') is None
                page.keyboard.press('k');page.mouse.click(*a);assert value('range.commander.mode')=='strafe';assert value('range.sim.shots')==0
                page.keyboard.press('Escape');assert value('range.commander.mode') is None
                passed('固定翼编组与主动聚焦；短航线拒绝、Esc只退预览')
                drag('b');assert value('range.sim.units.find(u=>u.kind==="plane").job.type')=='bomb'
                wait('range.sim.shots===1',70000);wait('range.sim.hits>0',10000)
                page.screenshot(path=str(OUT/(channel+'-轰炸命中.png')))
                wait('!range.sim.units.find(u=>u.kind==="plane").job',30000)
                shots=value('range.sim.shots');wait('range.sim.units.find(u=>u.kind==="plane").state==="盘旋待命"')
                passed('真实B拖线：进场、普通炸弹落地命中、单次脱离后盘旋')
                page.locator('#reset').click();overview();page.keyboard.press('4');q=point(240,230,2.1);page.mouse.click(*q,button='right')
                wait('range.sim.shots>0',70000);wait('range.sim.hits>0',10000)
                page.keyboard.down('Shift');drag('k');page.keyboard.up('Shift')
                assert value('range.sim.units.find(u=>u.kind==="plane").queue.length')==1
                page.keyboard.press('e');assert value('!range.sim.units.find(u=>u.kind==="plane").job && !range.sim.units.find(u=>u.kind==="plane").queue.length')
                position=value('({x:range.sim.units.at(-1).x,z:range.sim.units.at(-1).z})');page.wait_for_timeout(1000)
                assert value(f'Math.hypot(range.sim.units.at(-1).x-({position["x"]}),range.sim.units.at(-1).z-({position["z"]}))')>40
                passed('右键靶标自动扫射命中、Shift航线排队、E取消后持续飞行')
                page.locator('#unit-count').select_option('500');assert value('range.sim.units.length')==500
                assert page.locator('[data-unit]').count()==40
                page.locator('#roster-next').click();assert page.locator('[data-unit]').count()==40
                page.locator('#load-mode').select_option('combat');overview();wait('range.sim.shots>100')
                assert value('range.audio.stats().voices')<=48 and value('range.mixer.engines.size')<=8
                page.locator('#bench60').click();wait('!!range.benchmark.run');page.locator('#pause').click();assert value('range.benchmark.run') is None
                passed('500档分页、恢复靶持续交战、音频预算、暂停中止采样')
                page.set_viewport_size({'width':1280,'height':800});page.screenshot(path=str(OUT/(channel+'-1280布局.png')))
                assert value('document.documentElement.scrollWidth<=innerWidth')
                page.locator('#scenario').select_option('classic');assert value('range.sim.units.length')==10
                assert value('range.sim.targets.length===6 && range.sim.loadMode==="manual" && range.sim.units.every(u=>Math.abs(u.x)<72 && u.z<83)')
                page.keyboard.press('1');page.keyboard.press('g');page.keyboard.press('4');page.keyboard.press('Escape');assert value('range.commander.mode') is None
                assert value('range.commander.selected.size')==5
                passed('1280布局不横溢出、小靶场对照可回退')
                page.set_viewport_size({'width':1600,'height':1000});page.locator('[data-unit="tank-1"]').click()
                page.mouse.click(*point(-38,37,2.3));page.mouse.click(*point(-60,-45),button='right')
                wait('!range.sim.units[0].job',30000);page.keyboard.press('c');wait('!range.view.centerTarget')
                u=value('({x:range.sim.units[0].x,z:range.sim.units[0].z})');page.mouse.click(*point(u['x'],u['z'],2.3))
                assert value('range.commander.ids().length===1 && range.commander.ids()[0]==="tank-1"')
                passed('实例移动至旧点选范围外后，真实点选仍能命中正确单位')
            assert not result['errors'],result['errors']
            result['passed']=True
        except Exception as error:
            result['failure']=str(error);page.screenshot(path=str(OUT/(channel+'-失败.png')));raise
        finally:save();browser.close()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chrome','msedge'],default='chrome');parser.add_argument('--performance',action='store_true');parser.add_argument('--audio-only',action='store_true')
    args=parser.parse_args();run(args.browser,args.performance,args.audio_only)
