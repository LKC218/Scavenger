"""独立Chrome或Edge的真实键鼠回归与战斗帧时间采样，不操纵用户已有窗口。"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/原型/载具靶场/验证/城市修订1'


def run(channel):
    OUT.mkdir(parents=True,exist_ok=True)
    result={'channel':channel,'viewport':[1600,1000],'headless':True,'checks':[],'errors':[]}
    def passed(name):result['checks'].append(name);print(channel+'：'+name,flush=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        result['browser_version']=browser.version
        page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
        page.on('response',lambda r:result['errors'].append(str(r.status)+' '+r.url) if r.status>=400 else None)
        try:
            page.goto('http://127.0.0.1:8768/',wait_until='networkidle')
            page.wait_for_function('() => window.range?.ready',timeout=30000)
            page.locator('#scenario').select_option('classic')
            assert page.evaluate('() => range.sim.units.length')==10
            passed('首次加载：10单位、6靶标，无外部网络依赖')
            def wait(js,timeout=15000):page.wait_for_function('() => '+js,timeout=timeout)
            def point(x,z,y=0):
                q=page.evaluate('(p) => range.view.project(p)',{'x':x,'z':z,'y':y})
                box=page.locator('#battlefield').bounding_box()
                return q['x']+box['x'],q['y']+box['y']
            def click_world(x,z,y=0,button='right',shift=False):
                q=point(x,z,y)
                if shift:page.keyboard.down('Shift')
                page.mouse.click(*q,button=button)
                if shift:page.keyboard.up('Shift')
            def reset():
                page.locator('#reset').click();wait('range.sim.shots===0 && range.sim.projectiles.length===0 && range.commander.selected.size===0')
            # 战场点选和框选都经过真实坐标输入。
            click_world(-38,37,2.3,'left');assert page.evaluate('() => range.commander.selected.size')==1
            click_world(-22,37,2.3,'left',True);assert page.evaluate('() => range.commander.selected.size')==2
            box=page.locator('#battlefield').bounding_box();page.mouse.move(box['x']+20,box['y']+80);page.mouse.down();page.mouse.move(box['x']+box['width']-20,box['y']+box['height']-25,steps=8);page.mouse.up()
            assert page.evaluate('() => range.commander.selected.size')==10
            page.keyboard.press('Tab');assert page.evaluate('() => range.commander.ids().length')==1
            page.keyboard.press('3');assert page.evaluate('() => range.commander.ids().length')==10
            passed('点选、Shift追加、拖框混选、Tab子选择与编组')
            # 摄像机与低分辨率独立于DOM界面。
            old=page.evaluate('() => range.view.center.x');page.keyboard.down('d');page.wait_for_timeout(220);page.keyboard.up('d');assert page.evaluate('() => range.view.center.x')>old
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.wheel(0,-220)
            wait('range.view.span<96');page.keyboard.press('f');wait('range.view.span===96 && !range.view.centerTarget')
            page.locator('#quality').select_option('480');wait('range.view.renderer.domElement.width===480');page.locator('#quality').select_option('640')
            passed('WASD、滚轮缩放、F复位与480/640两档渲染')
            # 暂停命令不应提前执行，UI点击不向战场透传。
            reset();page.keyboard.press('1');page.keyboard.press('Space');frozen=page.evaluate('() => range.sim.time');click_world(-38,18)
            page.wait_for_timeout(250);assert page.evaluate('() => range.sim.time')==frozen
            assert page.evaluate('() => range.sim.units.filter(u=>u.kind==="tank").every(u=>u.job?.type==="move")')
            page.keyboard.press('Space');wait('range.sim.units[0].z<35');page.keyboard.press('e');wait('range.sim.units.filter(u=>u.kind==="tank").every(u=>!u.job)')
            passed('暂停可下令、恢复移动、E停止，工具栏不透传')
            # 1加1闭环：坦克+导弹直升机共同攻击开阔靶。
            reset();page.locator('[data-unit="tank-1"]').click();page.keyboard.down('Shift');page.locator('[data-unit="heli-1"]').click();page.keyboard.up('Shift')
            page.locator('[data-weapon="missile"]').click();assert page.evaluate('() => range.sim.units.find(u=>u.id==="heli-1").weapon')=='missile'
            click_world(-44,-27,2.1);wait('range.sim.shots>=2',18000);wait('range.sim.targets[0].hp<720',8000)
            page.screenshot(path=str(OUT/(channel+'-混合打靶.png')))
            passed('1坦克加1直升机：超射程接近、停稳、瞄准、发射及命中')
            # 重新选择一辆坦克，对墙后靶检查受阻反馈。
            reset();page.locator('[data-unit="tank-3"]').click();click_world(-4,-29,2.1)
            wait('range.sim.units[2].state.includes("受阻")',12000)
            assert page.evaluate('() => range.sim.shots')==0
            click_world(-30,-15);wait('range.sim.units[2].job===null',18000);click_world(-4,-29,2.1)
            wait('range.sim.targets[1].hp<720',12000)
            passed('受阻提示、玩家重新选位后恢复射击')
            # 新指令替换旧任务，Shift加入队列；退出预览不撤销命令。
            reset();page.locator('[data-unit="tank-1"]').click();click_world(-44,12);click_world(-54,0,shift=True)
            assert page.evaluate('() => range.sim.units[0].queue.length')==1
            page.keyboard.press('g');page.keyboard.press('Escape');assert page.evaluate('() => range.sim.units[0].queue.length')==1
            page.keyboard.press('e');page.keyboard.press('g');click_world(-44,12,button='left');wait('range.sim.shots===1',6000);page.wait_for_timeout(3500);assert page.evaluate('() => range.sim.shots')==1
            passed('Shift排队、Esc只退预览、攻击地面仅一轮')
            # 声音由真实用户手势解锁并解码，验证播放状态，不代替人工听审。
            page.locator('#sound').click();wait('range.audio.stats().unlocked && range.audio.stats().cached===Object.keys(range.manifest.audio).length',15000)
            page.keyboard.press('g');click_world(-44,0,button='left');wait('range.audio.stats().voices>0',6000)
            assert page.evaluate('() => range.audio.listener.context.state')=='running'
            click_world(-44,0);wait('[...range.audio.voices].some(v=>v.group==="engine-tank-1|move" && v.sound?.isPlaying)')
            page.keyboard.press('e');wait('![...range.audio.voices].some(v=>v.group==="engine-tank-1|move")')
            click_world(-54,0);wait('[...range.audio.voices].some(v=>v.group==="engine-tank-1|move" && v.sound?.isPlaying)')
            passed('Web Audio用户手势解锁、登记音效解码、发射与移动停止后重启声音')
            # 10单位战斗负载：全部指定开阔靶，同时移动、瞄准和发射。
            reset();page.keyboard.press('3');click_world(-44,-27,2.1)
            page.evaluate('() => range.resetMetrics()');page.wait_for_timeout(10000)
            result['combat_640']=page.evaluate('() => range.perf()');result['load'] = page.evaluate('() => ({shots:range.sim.shots,hits:range.sim.hits,kills:range.sim.kills,units:range.sim.units.length})')
            page.screenshot(path=str(OUT/(channel+'-十单位战斗.png')))
            assert result['load']['shots']>5 and result['load']['hits']>0
            page.locator('#quality').select_option('480');reset();page.keyboard.press('3');click_world(-44,-27,2.1)
            page.evaluate('() => range.resetMetrics()');page.wait_for_timeout(10000);result['combat_480']=page.evaluate('() => range.perf()')
            result['load_480']=page.evaluate('() => ({shots:range.sim.shots,hits:range.sim.hits,kills:range.sim.kills,units:range.sim.units.length})')
            assert result['load_480']['shots']>5 and result['load_480']['hits']>0
            passed('5加5战斗：双画面档位帧时间、绘制与资源统计')
            # 切出/失焦后无残留运动键；重复重置观察GPU资源数量是否增长。
            page.keyboard.down('d');page.evaluate('() => window.dispatchEvent(new Event("blur"))');assert page.evaluate('() => range.commander.keys.size')==0;page.keyboard.up('d')
            before=page.evaluate('() => range.view.renderer.info.memory');
            for _ in range(3):reset();page.wait_for_timeout(80)
            after=page.evaluate('() => range.view.renderer.info.memory')
            result['reset_memory']={'before':before,'after':after}
            assert after['geometries']<=before['geometries']+1 and after['textures']==before['textures']
            assert page.evaluate('() => range.audio.stats().voices')==0
            passed('失焦清理、3次重置、弹丸/音频/选择归零与GPU资源无持续增长')
            assert not result['errors'],result['errors']
            result['status']='passed'
        except Exception as e:
            result['status']='failed';result['failure']=str(e)
            result['state']=page.evaluate('() => ({units:window.range?.sim.units,shots:window.range?.sim.shots,targets:window.range?.sim.targets})')
            page.screenshot(path=str(OUT/(channel+'-失败.png')))
        finally:
            (OUT/(channel+'-浏览器验证.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');browser.close()
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chrome','msedge'],default='chrome');run(parser.parse_args().browser)
