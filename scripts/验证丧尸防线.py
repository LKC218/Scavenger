"""独立浏览器验证真实控件、战斗生命周期及有声性能，不操作既有浏览器。"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/原型/丧尸防线/V003-01a1a2af/验证'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def run(channel,performance=False,duration=180,force='trio',zombies=100,port=8794):
    name=channel+('-性能-'+force+'-'+str(zombies) if performance else '-交互')
    record={'browser':channel,'headless':True,'viewport':[1600,1000],'checks':[],'errors':[],'passed':False,'sampling':None,'source_identity':None}
    path=OUT/(name+'.json')
    def passed(label):record['checks'].append(label);save(path,record);print(channel+'：'+label,flush=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        record['version']=browser.version;page=browser.new_page(viewport={'width':1600,'height':1000})
        page.on('pageerror',lambda error:record['errors'].append(str(error)))
        page.on('console',lambda m:record['errors'].append(m.text) if m.type=='error' else None)
        page.on('response',lambda r:record['errors'].append(str(r.status)+' '+r.url) if r.status>=400 else None)
        try:
            page.goto(f'http://127.0.0.1:{port}/')
            page.wait_for_function('window.range?.ready || !document.querySelector("#fatal").hidden',timeout=60000)
            assert page.evaluate('!!window.range?.ready'),page.locator('#fatal').text_content()
            manifest=page.evaluate('range.manifest');record['source_identity']=manifest['source_identity'];record['frozen_source']=manifest['frozen_source']
            for row in manifest['source_identity']:
                assert hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest()==row['sha256'],'运行源与清单不一致：'+row['path']
            passed('固定运行源码、三模型及十三款骨骼角色加载')
            def settings(force_value='trio',count=100,health=30,endless=False,speed=1.6):
                page.locator('#defense-panel details summary').click() if not page.locator('#defense-force').is_visible() else None
                page.locator('#defense-force').select_option(force_value);page.locator('#zombie-count').fill(str(count));page.locator('#defense-health').fill(str(health));page.locator('#zombie-speed').fill(str(speed))
                page.locator('#defense-endless').set_checked(endless);page.locator('#defense-apply').click()
                page.wait_for_function('range.sim.phase==="ready" && range.view.actors.items.length===range.sim.targets.length')
            if performance:
                settings(force,zombies,health=1000,endless=True)
                page.locator('#defense-start').click();page.wait_for_function('range.mixer.active && range.sim.shots>0',timeout=30000)
                # 保持防线局部视野，原生键鼠不持续扰动样本。
                page.locator('#bench180' if duration==180 else '#bench60').click()
                if duration not in (60,180):raise ValueError('正式采样只接受60或180秒')
                deadline=time.monotonic()+duration+45;last_notice=0
                while time.monotonic()<deadline:
                    sample=page.evaluate('range.benchmark.last')
                    if sample:break
                    if time.monotonic()-last_notice>=30:
                        last_notice=time.monotonic();print(channel+' 持续有声采样：'+page.locator('#bench-status').inner_text(),flush=True)
                    page.wait_for_timeout(900)
                else:raise TimeoutError('正式采样未完成')
                record['sampling']=sample;record['final']=page.evaluate('range.perf()')
                assert sample['duration']>=duration and sample['p95']<=16.67
                assert sample['audio'] and sample['volume']==1
                assert sample['totalUnits']=={'trio':3,'ten':10,'thirty':30}[force]
                timeline=sample['timeline'];assert len(timeline)>=duration-2
                assert timeline[-1]['shots']>timeline[0]['shots']
                assert all(p['defense']['conserved'] for p in timeline)
                assert all(p['voices']<=48 and p['engines']<=8 and p['visibleCorpses']<=80 for p in timeline)
                record['sampling_summary']={'minAlive':min(p['defense']['alive'] for p in timeline),'maxAlive':max(p['defense']['alive'] for p in timeline),'maxActorsAnimated':max(p['animatedActors'] for p in timeline),'minWave':timeline[0]['defense']['wave'],'maxWave':timeline[-1]['defense']['wave'],'newShots':timeline[-1]['shots']-timeline[0]['shots'],'simulationDebtDelta':timeline[-1]['droppedSimulationSeconds']-timeline[0]['droppedSimulationSeconds']}
                passed('180秒有声交战、帧预算、数量守恒、声部与尸体预算' if duration==180 else '60秒有声压力档及预算')
                page.screenshot(path=str(OUT/(name+'.png')))
            else:
                assert page.evaluate('range.sim.units.length===3 && range.sim.targets.length===100 && range.view.actors.models.length===13')
                page.keyboard.press('Digit1');page.locator('#guard-toggle').click();assert page.evaluate('range.sim.units.find(u=>u.kind==="tank").guard===false')
                page.locator('#guard-toggle').click();assert page.evaluate('range.sim.units.find(u=>u.kind==="tank").guard===true')
                page.locator('#defense-start').click();page.wait_for_function('range.sim.kills>0 && range.mixer.active',timeout=20000)
                assert page.evaluate('range.sim.stats().conserved');passed('真实开始按钮、声音解锁、坦克击杀和警戒开关')
                page.locator('#pause').click();before=page.evaluate('range.sim.time');page.wait_for_timeout(350);assert page.evaluate('range.sim.time')==before
                assert page.evaluate('range.mixer.stats().voices')==0
                page.locator('#pause').click();page.keyboard.press('Digit2');page.locator('[data-weapon="missile"]').click()
                assert page.evaluate('range.sim.units.find(u=>u.kind==="heli").weapon')=='missile'
                page.locator('[data-weapon="gun"]').click();passed('暂停释放音源，恢复与手选旋翼武器')
                # 真实右键对移动丧尸下令；投影只用于定位，不调用规则替代输入。
                target=page.evaluate('(()=>{const t=range.sim.targets.find(t=>t.hp>0&&range.view.project(t).visible);return range.view.project(t)})()')
                field=page.locator('#battlefield').bounding_box();page.mouse.click(field['x']+target['x'],field['y']+target['y'],button='right')
                assert page.evaluate('range.sim.units.find(u=>u.kind==="heli").job?.type')=='attack'
                page.keyboard.press('KeyE');assert page.evaluate('range.sim.units.find(u=>u.kind==="heli").job') is None
                passed('移动丧尸点击命中、真实指定攻击与取消')
                settings('trio',100,30);assert page.evaluate('range.sim.shots===0 && range.sim.projectiles.length===0 && range.sim.integrity===30')
                page.keyboard.press('Digit3');page.screenshot(path=str(OUT/(name+'-布防.png')))
                textures=[]
                for _ in range(3):
                    page.locator('#reset').click();page.wait_for_timeout(250);textures.append(page.evaluate('range.view.renderer.info.memory.textures'))
                assert max(textures)-min(textures)<=3,textures;record['reset_textures']=textures
                settings('ten',300,endless=True);assert page.evaluate('range.sim.units.length===10 && range.view.actors.items.length===300')
                settings('trio',100);passed('重复重开、10车300角色配置及资源不逐轮增长')
                settings('tank',1,health=1,speed=5)
                page.keyboard.press('Digit1');page.locator('#guard-toggle').click();page.locator('#defense-start').click()
                page.wait_for_function('range.sim.result==="defeat"',timeout=45000)
                assert page.evaluate('range.sim.leaked')==1;page.locator('#defense-result').wait_for(state='visible',timeout=3000)
                page.locator('#defense-retry').click();assert page.evaluate('range.sim.phase')=='ready';passed('真实越线失败界面与重新布防')
                settings('trio',100);page.locator('#defense-start').click();page.wait_for_function('range.sim.kills>0',timeout=20000)
                page.screenshot(path=str(OUT/(name+'-交战.png')))
                page.locator('#scenario').select_option('city');page.wait_for_function('window.range?.ready && !range.defense')
                assert page.evaluate('range.sim.units.length')==30;assert not page.locator('#defense-panel').is_visible()
                page.locator('#scenario').select_option('classic');page.wait_for_function('range.sim.map.id==="classic"');assert page.evaluate('range.sim.units.length')==10
                passed('城市和小靶场对照模式仍可切换')
            assert not record['errors'],record['errors']
            record['passed']=True;save(path,record)
        except Exception as error:
            record['errors'].append(str(error));save(path,record);page.screenshot(path=str(OUT/(name+'-失败.png')));raise
        finally:browser.close()
    return record

def flight(channel,port):
    result={'browser':channel,'headless':True,'checks':[],'errors':[],'samples':[],'passed':False}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True);result['version']=browser.version
        page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:result['errors'].append(str(e)))
        for mode in ['strafe','bomb']:
            page.goto(f'http://127.0.0.1:{port}/');page.wait_for_function('window.range?.ready',timeout=60000)
            result['source_identity']=page.evaluate('range.manifest.source_identity')
            page.locator('#defense-panel details summary').click();page.locator('#zombie-count').fill('500');page.locator('#defense-health').fill('1000');page.locator('#defense-apply').click()
            page.keyboard.press('Digit3');page.locator('#guard-toggle').click();page.keyboard.press('Digit4');page.locator('#overview').click();page.wait_for_function('range.view.span>1400')
            start=-190 if mode=='strafe' else -95
            points=page.evaluate(f'[{{x:60,y:0,z:{start}}},{{x:60,y:0,z:65}}].map(p=>range.view.project(p))')
            box=page.locator('#battlefield').bounding_box();page.keyboard.press('KeyK' if mode=='strafe' else 'KeyB')
            page.mouse.move(box['x']+points[0]['x'],box['y']+points[0]['y']);page.mouse.down();page.mouse.move(box['x']+points[1]['x'],box['y']+points[1]['y'],steps=12);page.mouse.up()
            assert page.evaluate('range.sim.units.find(u=>u.kind==="plane").job.type')==mode
            page.locator('#defense-start').click();weapon='planeGun' if mode=='strafe' else 'bomb'
            page.wait_for_function(f'(range.sim.damageByWeapon.{weapon}??0)>0',timeout=65000)
            page.screenshot(path=str(OUT/(channel+'-'+mode+'-命中.png')))
            page.wait_for_function('range.sim.units.find(u=>u.kind==="plane").job===null',timeout=40000)
            sample=page.evaluate('({stats:range.sim.stats(),shots:range.sim.shots,plane:range.sim.units.find(u=>u.kind==="plane").state})')
            assert sample['stats']['conserved'] and sample['plane']=='盘旋待命'
            result['samples'].append({'mode':mode,**sample});result['checks'].append(mode+'真实拖线、发射、命中与脱离盘旋');print(channel+'：'+result['checks'][-1],flush=True)
            save(OUT/(channel+'-固定翼交互.json'),result)
        assert not result['errors'];result['passed']=True;save(OUT/(channel+'-固定翼交互.json'),result);browser.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--browser',choices=['chrome','msedge'],default='chrome');p.add_argument('--performance',action='store_true');p.add_argument('--flight',action='store_true');p.add_argument('--duration',type=int,choices=[60,180],default=180);p.add_argument('--force',choices=['trio','ten','thirty'],default='trio');p.add_argument('--zombies',type=int,default=100);p.add_argument('--port',type=int,default=8794);a=p.parse_args()
    if a.flight:flight(a.browser,a.port)
    else:run(a.browser,a.performance,a.duration,a.force,a.zombies,a.port)
