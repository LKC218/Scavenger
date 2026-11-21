"""独立浏览器核验真实机械声、状态、清理和有限持续负载，不操作工程师窗口。"""
import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'local-data/原型/载具靶场/验证/音效补全1'


def run(channel, performance=False):
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'channel': channel, 'headless': True, 'viewport': [1600, 1000], 'checks': [], 'errors': [], 'samples': []}
    result['source_identity'] = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                 for p in sorted((ROOT/'src/载具靶场').glob('*')) if p.suffix in ['.mjs', '.html', '.css']]
    file = OUT / (channel + ('-持续负载.json' if performance else '-音效交互.json'))
    def save(): file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    def passed(name): result['checks'].append(name); save(); print(channel + '：' + name, flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        result['version'] = browser.version
        page = browser.new_page(viewport={'width': 1600, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
        page.on('response', lambda r: result['errors'].append(str(r.status)+' '+r.url) if r.status >= 400 else None)
        def value(expression): return page.evaluate('() => ' + expression)
        def wait(expression, timeout=15000): page.wait_for_function('() => ' + expression, timeout=timeout)
        def click_point(x, z, y=0):
            q = page.evaluate('(p)=>range.view.project(p)', {'x': x, 'y': y, 'z': z})
            box = page.locator('#battlefield').bounding_box()
            assert 0 < q['x'] < box['width'] and 0 < q['y'] < box['height'], q
            page.mouse.click(box['x']+q['x'], box['y']+q['y'], button='right')
        def focus(kind):
            page.locator('#roster-filter').select_option(kind)
            page.locator('#roster [data-unit]').first.click()
            page.keyboard.press('c'); wait('!range.view.centerTarget')
        try:
            page.goto('http://127.0.0.1:8768/', wait_until='networkidle')
            wait('window.range?.ready', 40000)
            assert not value('range.mixer.active')
            assert value('Object.values(range.manifest.audio).filter(a=>a.loop).length') == 3
            page.locator('#sound').click()
            wait('range.mixer.active && range.audio.stats().cached===13')
            wait('range.mixer.stats().engineStates["待机"]>0 && range.mixer.stats().engineStates["悬停"]>0 && range.mixer.stats().engineStates["巡航盘旋"]>0')
            passed('手势解锁13声音；默认三型机械声均入预算，停车与悬停持续发声')
            if performance:
                for count, seconds in [(100, 180 if channel == 'chrome' else 60), (500, 60)]:
                    page.locator('#unit-count').select_option(str(count)); page.locator('#load-mode').select_option('combat')
                    page.locator('#overview').click(); wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
                    page.keyboard.press('3')
                    page.locator('#bench'+str(seconds)).click(); wait('!!range.benchmark.run')
                    wait('!range.benchmark.run', (seconds+35)*1000)
                    sample = value('range.benchmark.last'); sample['final'] = value('range.perf()'); sample['audio'] = value('range.mixer.stats()')
                    sample['anchors'] = value('({registered:range.mixer.anchors.size,inScene:range.view.scene.children.filter(o=>o.userData.expires!==undefined).length})')
                    assert sample['frames'] > 1000 and sample['timeline'][-1]['shots'] > sample['timeline'][0]['shots'] + 20
                    assert sample['audio']['voices'] <= 48 and sample['audio']['eventVoices'] <= 40 and sample['audio']['engines'] <= 8
                    assert sample['audio']['engines'] > 0 and .001 < sample['audio']['peak'] < .951
                    assert sample['anchors']['registered'] == sample['anchors']['inScene']
                    result['samples'].append(sample)
                    passed(str(count)+'单位有声交战'+str(seconds)+'秒：P95 '+str(round(sample['p95'], 2))+'ms，预算与节点守恒')
                page.screenshot(path=str(OUT/(channel+'-有声负载.png')))
            else:
                # 从实际运行中的三型声源分接分析器；14秒跨越至少两个6.38秒循环。
                result['real_asset_output'] = page.evaluate('''async()=>{
                  const mixer=range.mixer,c=mixer.listener.context,probes=[];
                  for(const kind of ['tank','heli','plane']){const entry=[...mixer.engines.values()].find(e=>e.kind===kind&&e.sound?.isPlaying),a=c.createAnalyser();a.fftSize=2048;entry.sound.gain.connect(a);probes.push({kind,entry,a,wave:new Float32Array(2048),min:Infinity,max:0,frames:0});}
                  const started=performance.now();while(performance.now()-started<14000){for(const p of probes){p.a.getFloatTimeDomainData(p.wave);let sum=0;for(const x of p.wave)sum+=x*x;const rms=Math.sqrt(sum/p.wave.length);if(performance.now()-started>500)p.min=Math.min(p.min,rms);p.max=Math.max(p.max,rms);p.frames++;}await new Promise(r=>setTimeout(r,50));}
                  return probes.map(p=>{p.entry.sound.gain.disconnect(p.a);p.a.disconnect();return{kind:p.kind,minRms:p.min,maxRms:p.max,windows:p.frames,loop:p.entry.sound.source.loop,bufferSeconds:p.entry.sound.buffer.duration};});
                }''')
                for r in result['real_asset_output']: assert r['loop'] and r['minRms'] > .0005 and r['windows'] > 100, r
                passed('三型真实声源14秒逐窗非静音，跨两个循环，无两秒断声')
                focus('tank'); wait('range.mixer.engines.has("tank-1")')
                before = value('range.mixer.engines.get("tank-1").sound.uuid')
                u = value('range.sim.units.find(u=>u.id==="tank-1")')
                click_point(u['x'], u['z']+28)
                wait('range.mixer.engines.get("tank-1")?.state==="行驶"')
                moving = value('({gain:range.mixer.engines.get("tank-1").targetGain,rate:range.mixer.engines.get("tank-1").targetRate})')
                page.keyboard.press('e'); wait('range.mixer.engines.get("tank-1")?.state==="待机"')
                assert value('range.mixer.engines.get("tank-1").sound.uuid') == before
                idle = value('({gain:range.mixer.engines.get("tank-1").targetGain,rate:range.mixer.engines.get("tank-1").targetRate})')
                assert moving['rate'] > idle['rate'] and moving['gain'] > idle['gain']
                result['tank_transition'] = {'moving': moving, 'idle': idle, 'same_source': True}
                passed('真实右键行驶与E停车：同一循环平滑转速／增益，停车保留待机声')
                focus('heli'); wait('range.mixer.engines.get("heli-1")?.state==="悬停"')
                u = value('range.sim.units.find(u=>u.id==="heli-1")')
                click_point(u['x']+28, u['z'])
                wait('range.mixer.engines.get("heli-1")?.state==="旋翼飞行"')
                page.keyboard.press('e'); wait('range.mixer.engines.get("heli-1")?.state==="悬停"')
                passed('旋翼真实移动与停止：飞行／悬停切换持续发声')
                focus('plane'); wait('range.mixer.engines.get("plane-1")?.state==="巡航盘旋"')
                page.locator('#overview').click(); wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
                page.keyboard.press('b')
                a = page.evaluate('()=>range.view.project({x:240,y:0,z:360})'); b = page.evaluate('()=>range.view.project({x:240,y:0,z:100})')
                box = page.locator('#battlefield').bounding_box()
                page.mouse.move(box['x']+a['x'], box['y']+a['y']); page.mouse.down(); page.mouse.move(box['x']+b['x'], box['y']+b['y'], steps=8); page.mouse.up()
                wait('range.mixer.engines.get("plane-1")?.state==="攻击飞行"')
                page.keyboard.press('e'); wait('range.mixer.engines.get("plane-1")?.state==="巡航盘旋"')
                passed('固定翼真实轰炸命令与取消：攻击转速变化，取消后盘旋仍有涡轮声')
                # 相机的真实按键离开初始声区，再回位，核对预算转换与距离。
                page.keyboard.press('f'); wait('!range.view.centerTarget')
                near = value('[...range.mixer.engines.keys()]')
                page.keyboard.down('w'); page.wait_for_timeout(3500); page.keyboard.up('w')
                page.keyboard.press('f'); wait('!range.view.centerTarget')
                wait('range.mixer.stats().engines<=8')
                result['budget'] = value('range.mixer.stats()')
                page.locator('#volume').fill('0'); page.locator('#volume').dispatch_event('input')
                result['muted_peak'] = page.evaluate('''async()=>{await new Promise(r=>setTimeout(r,500));let peak=0;for(let i=0;i<8;i++){const a=new Float32Array(1024);range.mixer.analyser.getFloatTimeDomainData(a);for(const x of a)peak=Math.max(peak,Math.abs(x));await new Promise(r=>setTimeout(r,30));}return peak;}''')
                assert result['muted_peak'] < .000001
                page.locator('#volume').fill('70'); page.locator('#volume').dispatch_event('input')
                page.locator('#pause').click(); wait('range.mixer.stats().voices===0 && range.mixer.anchors.size===0')
                page.locator('#pause').click(); wait('range.mixer.stats().engines>0')
                page.locator('#sound').click(); wait('!range.mixer.active && range.mixer.anchors.size===0 && range.mixer.stats().voices===0')
                page.locator('#sound').click(); wait('range.mixer.active && range.mixer.stats().engines>0')
                page.locator('#reset').click(); wait('range.mixer.stats().engines>0')
                anchors = value('({registered:range.mixer.anchors.size,inScene:range.view.scene.children.filter(o=>o.userData.expires!==undefined).length})')
                assert anchors['registered'] == anchors['inScene'] and anchors['registered'] <= 8, anchors
                result['reset_anchors'] = anchors
                passed('零音量真实输出静音；暂停／关闭清零，恢复／重置重建且节点守恒')
                page.locator('#scenario').select_option('classic')
                wait('range.sim.map.id!=="city" && range.mixer.stats().engines>0')
                assert not value('[...range.mixer.engines.values()].some(e=>e.kind==="plane")')
                passed('切小靶场不残留固定翼声音，不跨场景串声')
                page.screenshot(path=str(OUT/(channel+'-音效界面.png')))
            assert not result['errors'], result['errors']
            result['passed'] = True
        except Exception as error:
            result['failure'] = str(error); save(); raise
        finally:
            save(); browser.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], default='chrome')
    parser.add_argument('--performance', action='store_true')
    args = parser.parse_args(); run(args.browser, args.performance)
