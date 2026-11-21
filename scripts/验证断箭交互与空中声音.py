"""V003真实鼠标指令验证，以及独立的原生炮声调度对照；不启动参考游戏。"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/原型/丧尸防线/V003-01a1a2af/验证'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    record={'checks':[],'errors':[],'passed':False,'scope':'真实键鼠交互＋声音适配器受控调度；真实固定翼战斗另由flight回归验证'}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1000})
        page.on('pageerror',lambda e:record['errors'].append(str(e)))
        def passed(text):record['checks'].append(text);print('通过：'+text,flush=True)
        try:
            page.goto('http://127.0.0.1:8794/');page.wait_for_function('window.range?.ready',timeout=60000)
            record['source']=page.evaluate('range.manifest.source_sha256')
            page.locator('#defense-panel details summary').click();page.locator('#defense-force').select_option('ten');page.locator('#defense-apply').click()
            page.wait_for_function('range.sim.units.length===10 && range.view.actors.items.length===100')
            field=page.locator('#battlefield').bounding_box()
            def point(x,y,z):
                p=page.evaluate('(p)=>range.view.project(p)',{'x':x,'y':y,'z':z})
                return field['x']+p['x'],field['y']+p['y']
            initial=page.evaluate('({angle:range.view.angle,span:range.view.span,center:range.view.center.toArray()})')
            # 两辆相邻坦克的真实框选，不通过规则入口替代输入。
            a=point(-31,2,20);b=point(-13,2,28)
            page.mouse.move(min(a[0],b[0]),min(a[1],b[1]));page.mouse.down();page.mouse.move(max(a[0],b[0]),max(a[1],b[1]),steps=8);page.mouse.up()
            assert page.evaluate('range.commander.selected.size')==2
            selected=page.evaluate('[...range.commander.selected]')
            passed('真实框选两辆坦克，选择不移动镜头')
            destination=point(-65,0,45);facing=point(-45,0,45)
            page.mouse.move(*destination);page.mouse.down(button='right');page.mouse.move(*facing,steps=12)
            assert page.evaluate('range.sim.units.filter(u=>range.commander.selected.has(u.id)).every(u=>u.job===null)')
            assert page.evaluate('range.view.commandPreview.visible')
            page.screenshot(path=str(OUT/'V003-朝向预览.png'))
            page.mouse.up(button='right')
            assert page.evaluate('range.sim.units.filter(u=>range.commander.selected.has(u.id)).every(u=>u.job?.type==="move"&&Math.abs(u.job.facing+Math.PI/2)<.01)')
            assert not page.evaluate('range.view.commandPreview.visible')
            passed('右键按住只预览，松开提交同一朝向与编队')
            page.keyboard.down('Shift');page.mouse.click(*point(-65,0,10),button='right');page.keyboard.up('Shift')
            assert page.evaluate('range.sim.units.filter(u=>range.commander.selected.has(u.id)).every(u=>u.queue.length===1)')
            page.mouse.move(*point(-50,0,15));page.mouse.down(button='right');page.keyboard.press('Escape');page.mouse.up(button='right')
            assert page.evaluate('range.sim.units.filter(u=>range.commander.selected.has(u.id)).every(u=>u.queue.length===1)')
            passed('Shift排队，Esc取消尚未提交的右键预览')
            page.keyboard.press('Shift+Digit8');page.keyboard.press('Digit4');page.keyboard.press('Alt+Digit8')
            assert sorted(page.evaluate('[...range.commander.selected]'))==sorted(selected)
            assert initial==page.evaluate('({angle:range.view.angle,span:range.view.span,center:range.view.center.toArray()})')
            page.mouse.dblclick(*point(-27.5,2.3,24))
            assert page.evaluate('range.commander.selected.size')==6
            passed('网页编组保存调用、双击同型选择及镜头状态保留')
            # 受控事件只验证播放调度，不冒充战斗命中或原作实时混音。
            page.locator('#sound').click();page.wait_for_function('range.mixer.active')
            page.evaluate('''()=>{const u=range.sim.units.find(u=>u.kind==='plane');u.x=0;u.z=0;window.audioTestEnd=performance.now()+1600;window.audioTestTimer=setInterval(()=>{range.mixer.event({type:'shot',unit:u.id,weapon:'planeGun',position:{x:0,y:72,z:0}});if(performance.now()>=audioTestEnd){clearInterval(audioTestTimer);window.audioTestDone=true;}},100);}''')
            page.wait_for_function('window.audioTestDone',timeout=5000)
            active=page.evaluate('range.mixer.stats()');assert active['airAudio']['sustainShots']>=15;assert active['airAudio']['sustainStarts']==1
            page.wait_for_function('range.mixer.gunRuns.size===0 && range.mixer.stats().airAudio.releases>=1',timeout=5000)
            page.wait_for_function('''(()=>{const m=range.mixer,shot=m.derived.get('planeShot').audio_id,tail=m.derived.get('planeRelease').audio_id;return [...range.audio.voices].every(v=>v.asset.audio_id!==shot)&&[...range.audio.voices].some(v=>v.asset.audio_id===tail)})()''',timeout=5000)
            page.evaluate('''()=>{const u=range.sim.units.find(u=>u.kind==='plane');range.mixer.event({type:'shot',unit:u.id,weapon:'planeGun',position:{x:0,y:72,z:0}});}''')
            page.wait_for_function('range.mixer.gunRuns.get(range.sim.units.find(u=>u.kind==="plane").id)?.entry',timeout=3000)
            record['retrigger']=page.evaluate('''()=>{const m=range.mixer,u=range.sim.units.find(u=>u.kind==='plane'),tail=m.derived.get('planeRelease').audio_id;return{correctSustain:m.gunRuns.get(u.id).entry.asset.audio_id===m.derived.get('planeShot').audio_id,releaseNotLooped:[...range.audio.voices].filter(v=>v.asset.audio_id===tail).every(v=>!v.sound.loop)}}''')
            assert record['retrigger']['correctSustain'] and record['retrigger']['releaseNotLooped'],record['retrigger']
            page.wait_for_function('range.mixer.gunRuns.size===0',timeout=5000)
            passed('停火尾音尚在播放时重新开火，只循环新的持续声，不误循环尾音')
            record['audio']=page.evaluate('range.mixer.stats()');record['audio']['duringBurst']=active
            assert record['audio']['dropped']==0
            passed('A10连续15发以上只启动一份持续炮声，停火释放原生尾音')
            record['impactPCM']=page.evaluate('''async()=>{const rows=[];for(const key of ['heliImpact','planeImpact']){const m=range.mixer,source=await m.runtime.buffer(range.manifest.audio[key]),asset=m.derived.get(key),derived=m.runtime.cache.get(asset.audio_id).buffer,offset=Math.round(m.airAudio[key].start*source.sampleRate);let changed=0;for(let c=0;c<source.numberOfChannels;c++){const a=source.getChannelData(c),b=derived.getChannelData(c);for(let i=0;i<b.length;i++)if(a[offset+i]!==b[i])changed++;}rows.push({key,sourceId:range.manifest.audio[key].audio_id,changedSamples:changed,frames:derived.length});}return rows;}''')
            assert all(row['changedSamples']==0 for row in record['impactPCM'])
            page.evaluate('''()=>{window.impactPeak=0;window.impactEnd=performance.now()+1100;window.impactTimer=setInterval(()=>{range.mixer.event({type:'impact',weapon:'planeGun',owner:'impact-check',serial:performance.now(),position:{x:0,y:0,z:0},surface:'terrain'});impactPeak=Math.max(impactPeak,[...range.audio.voices].filter(v=>v.group==='impact-impact-check-planeGun|airImpact').length);if(performance.now()>=impactEnd){clearInterval(impactTimer);window.impactDone=true;}},100);}''')
            page.wait_for_function('window.impactDone',timeout=5000)
            record['impactAudio']=page.evaluate('({peak:impactPeak,stats:range.mixer.stats()})')
            assert record['impactAudio']['peak']<=3 and record['impactAudio']['stats']['dropped']==0
            passed('两类命中派生与原事件PCM相等，连击尾音按同射手限制为最多3声部')
            page.locator('#sound').click();assert page.evaluate('range.mixer.stats().voices')==0
            assert not record['errors'],record['errors'];record['passed']=True
        finally:
            (OUT/'交互与空中声音.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');browser.close()


if __name__=='__main__':main()
