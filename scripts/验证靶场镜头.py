"""以真实键鼠验证平滑镜头、鼠标锚点、主动聚焦与战斗中的镜头负载。"""
import argparse
import json
import math
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/原型/载具靶场/验证/城市修订1/镜头'


def run(channel):
    OUT.mkdir(parents=True,exist_ok=True)
    result={'browser':channel,'checks':[],'errors':[],'viewport':[1600,1000],'headless':True}
    def passed(label):result['checks'].append(label);print(f'{channel}：{label}',flush=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel=channel,headless=True)
        result['version']=browser.version
        page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
        def wait(expression,timeout=8000):page.wait_for_function('() => '+expression,timeout=timeout)
        def evaluate(expression):return page.evaluate('() => '+expression)
        def home():
            page.keyboard.press('f');wait('range.view.span===96 && range.view.angle===0 && !range.view.centerTarget')
        def reset():page.locator('#reset').click();wait('range.sim.shots===0 && range.commander.selected.size===0')
        def world_click(x,z,y=0,button='right'):
            q=page.evaluate('(point)=>range.view.project(point)',{'x':x,'y':y,'z':z});box=page.locator('#battlefield').bounding_box()
            assert 0<q['x']<box['width'] and 0<q['y']<box['height'],q
            page.mouse.click(box['x']+q['x'],box['y']+q['y'],button=button)
        try:
            page.goto('http://127.0.0.1:8768/',wait_until='networkidle');wait('window.range?.ready',30000)
            page.locator('#scenario').select_option('classic')
            assert evaluate('range.view.span')==96
            assert page.locator('#focus').is_disabled()
            passed('默认视野96、未选择时聚焦按钮禁用')
            # 用真实滚轮采样每一显示帧，验证整个过渡中的鼠标锚点。
            box=page.locator('#battlefield').bounding_box();sx=box['width']*.68;sy=box['height']*.43
            page.mouse.move(box['x']+sx,box['y']+sy)
            page.evaluate('''({x,y})=>{
              const v=range.view;window.__anchor={point:v.groundPoint(x,y),x,y,errors:[],spans:[]};
              window.__recordAnchor=true;
              function sample(){if(!window.__recordAnchor)return;const a=window.__anchor,p=v.project(a.point);a.errors.push(Math.hypot(p.x-a.x,p.y-a.y));a.spans.push(v.span);requestAnimationFrame(sample);}requestAnimationFrame(sample);
            }''',{'x':sx,'y':sy})
            page.mouse.wheel(0,-240);wait('Math.abs(range.view.targetSpan-range.view.span)<.001')
            page.wait_for_timeout(80);page.evaluate('() => window.__recordAnchor=false')
            anchor=evaluate('({maxError:Math.max(...__anchor.errors),samples:__anchor.errors.length,start:__anchor.spans[0],end:range.view.span,spans:__anchor.spans})')
            assert anchor['maxError']<.5 and anchor['samples']>10,anchor
            assert all(a>=b-1e-8 for a,b in zip(anchor['spans'],anchor['spans'][1:]))
            assert anchor['end']<90
            anchor.pop('spans');result['zoom_anchor']=anchor
            # 连续滚轮会累计目标值，在边界停稳，不发生超调。
            for _ in range(5):page.mouse.wheel(0,-300)
            wait('range.view.span===58');page.mouse.wheel(0,300);wait('range.view.span>70')
            passed('滚轮锚点全过渡误差小于0.5显示像素，连续输入与最小缩放边界')
            home()
            # 旋转经过真实中键拖动；指北针应与投影到屏幕上的世界北向一致。
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
            page.mouse.down(button='middle');page.mouse.move(box['x']+box['width']/2+130,box['y']+box['height']/2,steps=8);page.mouse.up(button='middle')
            wait('Math.abs(range.view.targetAngle-range.view.angle)<.0001')
            compass=evaluate('''(()=>{const v=range.view,c=v.project(v.center),n=v.project({x:v.center.x,y:0,z:v.center.z-10});return {angle:v.angle,target:v.targetAngle,expected:Math.atan2(n.x-c.x,c.y-n.y),css:parseFloat(document.querySelector('.compass').style.transform.slice(7))};})()''')
            assert .50<compass['angle']<.54 and abs(compass['expected']-compass['css'])<.001,compass
            result['compass']=compass;passed('中键减敏与平滑旋转、指北针和世界北向一致')
            home()
            # 实际UI验证选择和下令不移动镜头；聚焦一次后不追随单位。
            center=evaluate('range.view.center.toArray()')
            page.locator('[data-unit="tank-1"]').click();page.wait_for_timeout(100)
            assert evaluate('range.view.center.toArray()')==center
            page.locator('#focus').click();wait('!range.view.centerTarget')
            centered=evaluate('range.view.project({x:range.sim.units[0].x,y:2.3,z:range.sim.units[0].z})')
            assert math.hypot(centered['x']-box['width']/2,centered['y']-box['height']/2)<.1,centered
            center=evaluate('range.view.center.toArray()');world_click(-50,10)
            wait('range.sim.units[0].z<32');assert evaluate('range.view.center.toArray()')==center
            page.locator('[data-unit="heli-1"]').click();assert evaluate('range.view.center.toArray()')==center
            page.keyboard.press('c');wait('!range.view.centerTarget')
            centered=evaluate('range.view.project({x:range.sim.units[5].x,y:13.3,z:range.sim.units[5].z})')
            assert math.hypot(centered['x']-box['width']/2,centered['y']-box['height']/2)<.1,centered
            assert all(u['job'] is None for u in evaluate('range.sim.units.filter(u=>u.kind==="heli")'))
            passed('选择及下令不抢镜头，按钮和C聚焦地空单位，移动后不自动跟随')
            # 手动输入打断聚焦，失焦清除速度及待完成镜头动作。
            page.keyboard.press('f');page.keyboard.down('d');page.wait_for_timeout(180)
            assert not evaluate('Boolean(range.view.centerTarget)')
            page.evaluate('() => window.dispatchEvent(new Event("blur"))');page.keyboard.up('d')
            frozen=evaluate('range.view.center.toArray()');page.wait_for_timeout(180)
            assert evaluate('range.view.center.toArray()')==frozen
            assert evaluate('range.view.panVelocity.lengthSq()')==0
            passed('手动输入打断聚焦，失焦后无按键、惯性或镜头补动画残留')
            reset();page.keyboard.press('Space')
            clock=evaluate('range.sim.time');old=evaluate('range.view.center.x')
            page.keyboard.down('d');page.wait_for_timeout(180);page.keyboard.up('d');page.wait_for_timeout(180)
            assert evaluate('range.view.center.x')>old and evaluate('range.sim.time')==clock
            passed('暂停时仍可操纵镜头，游戏时钟保持冻结')
            # 同一实际控制器的确定时间步长比较；此项是数值检查，不冒充真实键鼠验收。
            numerical=evaluate('''(()=>{
              const v=range.view,out={};for(const fps of [30,60,144]){v.home(true);v.pan(1,0);for(let i=0;i<fps*.5;i++)v.stepCamera(1/fps);const startStop=v.center.x;v.pan(0,0);for(let i=0;i<fps*.3;i++)v.stepCamera(1/fps);out[fps]={x:v.center.x,stopDistance:v.center.x-startStop};}
              v.home(true);v.pan(1,0);for(let i=0;i<60;i++)v.stepCamera(1/60);
              const axis=v.center.x;v.home(true);v.pan(1,1);for(let i=0;i<60;i++)v.stepCamera(1/60);const diagonal=Math.hypot(v.center.x,v.center.z+4);v.home(true);
              return {frames:out,axis,diagonal};})()''')
            values=[n['x'] for n in numerical['frames'].values()]
            assert max(values)-min(values)<.0001,numerical
            assert abs(numerical['axis']-numerical['diagonal'])<.0001,numerical
            assert max(n['stopDistance'] for n in numerical['frames'].values())<1.1,numerical
            result['numerical']=numerical;passed('30/60/144时间步长位移一致、斜向等速、松键滑行小于1.1米')
            reset();page.keyboard.press('3');world_click(-44,-27,2.1);page.evaluate('() => range.resetMetrics()')
            for i in range(3):
                page.mouse.move(box['x']+box['width']*.55,box['y']+box['height']*.45);page.mouse.wheel(0,-70 if i%2==0 else 70)
                page.keyboard.down('d' if i%2==0 else 'a');page.wait_for_timeout(160);page.keyboard.up('d' if i%2==0 else 'a');page.wait_for_timeout(500)
            page.wait_for_timeout(4000);result['performance']=evaluate('range.perf()')
            result['shots']=evaluate('range.sim.shots');assert result['shots']>5
            page.screenshot(path=str(OUT/(channel+'-镜头战斗.png')))
            passed('十单位移动射击期间操纵平移与缩放，保存帧时间及截图')
            assert not result['errors'],result['errors'];result['status']='passed'
        except Exception as e:
            result['status']='failed';result['failure']=str(e);page.screenshot(path=str(OUT/(channel+'-失败.png')))
            result['state']=page.evaluate('() => window.range?{center:range.view.center.toArray(),span:range.view.span,angle:range.view.angle,centerTarget:range.view.centerTarget}:null')
        finally:
            (OUT/(channel+'-镜头验证.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');browser.close()
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chrome','msedge'],default='chrome');run(parser.parse_args().browser)
