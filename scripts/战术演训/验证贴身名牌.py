"""在独立浏览器核对贴身名牌、真实选择与移动，保存人工复测截图。"""
import argparse, hashlib, json, time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'local-data/战术演训/G01载具名牌重构/V004-01a1e3c0'
def read(p):return json.loads(p.read_text('utf8'))
def run(channel,directory):
    directory.mkdir(parents=True,exist_ok=False)
    identity=read(OUTPUT/'当前候选.json')
    with urlopen('http://127.0.0.1:8814/api/status',timeout=5) as response:live=json.load(response)
    assert live['app']=='scavenger-airbase-g01-attached-nameplate' and live['source_sha256']==identity['source_sha256']
    result={'identity':live,'channel':channel,'checks':[],'errors':[],'started_at':time.time(),'scope':'无窗口技术检查及截图，人工画面与正式前台性能待确认'}
    def check(name,ok,details=None):
        result['checks'].append({'name':name,'passed':bool(ok),'details':details})
        print(json.dumps({'channel':channel,'check':name,'passed':bool(ok)},ensure_ascii=False),flush=True)
        if not ok:raise AssertionError(name)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        page.on('console',lambda msg:result['errors'].append(msg.text) if msg.type=='error' else None)
        try:
            page.goto('http://127.0.0.1:8814/',wait_until='domcontentloaded',timeout=120000)
            page.wait_for_function('window.range?.ready',timeout=180000)
            result['browser_version']=browser.version
            check('默认48度和220米镜头保持',page.evaluate("Math.abs(range.view.getCameraState().tiltDegrees-48)<.1&&Math.abs(range.view.getCameraState().viewWidth-220)<1"))
            heli=page.evaluate("range.sim.units.find(u=>u.kind==='heli').id")
            tank=page.evaluate("range.sim.units.find(u=>u.kind==='tank').id")
            def select(id):
                page.locator('#roster [data-unit="'+id+'"]').click()
                page.locator('#focus').click()
                page.wait_for_function('!range.view.centerTarget&&!range.view.zoomAnchor',timeout=20000)
            def settle_map():
                page.wait_for_function('!range.externalMap.getTransitionState().active&&!range.externalMap.getTransitionState().pending',timeout=180000)
            def metrics():
                return page.evaluate("""()=>{const v=range.view;return v.labelStats.rows.filter(r=>r.kind==='unit'&&r.visible).map(r=>{const b=v.projectNameplateBounds(r.unitId),e=v.units.get(r.unitId).label;const gap=Math.hypot(Math.max(b.left-(r.x+r.width),r.x-b.right,0),Math.max(b.top-(r.y+r.height),r.y-b.bottom,0));return{id:r.unitId,gap,compact:e.dataset.compact,slot:r.slot,rect:{x:r.x,y:r.y,width:r.width,height:r.height},body:b};});}""")
            select(heli);settle_map()
            check('直升机只有一张名牌且高度合并',page.locator('.altitude-marker:visible').count()==0 and '离地' in page.locator('.unit-label[data-unit-id="'+heli+'"]').inner_text())
            check('保留真实高度辅助线',page.evaluate('range.view.altitudeLine.visible&&range.view.altitudeLinks.length===1'))
            m=metrics();check('所有可见名牌限制在机身30像素内',bool(m) and all(r['gap']<=30.1 for r in m),m)
            page.screenshot(path=str(directory/'直升机中景.png'))
            label=page.locator('.unit-label[data-unit-id="'+heli+'"]')
            label.hover()
            page.wait_for_function('id=>range.view.hoveredUnit===id',arg=heli)
            check('名牌悬停对应机身联动',page.evaluate("id=>range.view.units.get(id).highlight.getAttribute('d')?.length>0",heli))
            label.click()
            check('真实标签点击选择正确',page.evaluate('id=>range.commander.selected.size===1&&range.commander.selected.has(id)',heli))
            # 用真实机身像素选择，排除标签、HUD和路径标记。
            select(tank);settle_map()
            body=page.evaluate("""id=>{const v=range.view,b=v.projectNameplateBounds(id),r=document.querySelector('#battlefield').getBoundingClientRect();for(const fx of [.5,.35,.65,.2,.8])for(const fy of [.5,.35,.65,.2,.8]){const x=b.left+(b.right-b.left)*fx,y=b.top+(b.bottom-b.top)*fy,e=document.elementFromPoint(r.left+x,r.top+y);if(e&&!e.closest('button')&&v.pickUnits(x,y).includes(id))return{x:r.left+x,y:r.top+y};}return null;}""",tank)
            check('真实机身有可点选像素',body is not None,body)
            page.mouse.move(body['x'],body['y'])
            page.wait_for_function('id=>range.view.hoveredUnit===id',arg=tank)
            page.mouse.click(body['x'],body['y'])
            check('机身悬停和点击归属一致',page.evaluate('id=>range.commander.selected.has(id)&&range.view.hoveredUnit===id',tank))
            page.screenshot(path=str(directory/'坦克中景.png'))
            # 用正常直升机命令核对移动反馈；地面像素选择已在前项核对。
            select(heli);settle_map()
            destination=page.evaluate("""()=>{const v=range.view,r=document.querySelector('#battlefield').getBoundingClientRect();for(const [fx,fy] of [[.6,.65],[.65,.6],[.4,.65],[.55,.55]]){const x=v.width*fx,y=v.height*fy,p=v.ground(x,y),e=document.elementFromPoint(r.x+x,r.y+y);if(p?.state==='ready'&&!e?.closest('button'))return{x:r.x+x,y:r.y+y};}return null;}""")
            check('移动检查找到已加载落点',destination is not None)
            previous=page.evaluate('range.sim.lastCommandResult?.id??null')
            page.mouse.click(destination['x'],destination['y'],button='right')
            page.wait_for_function('old=>(range.sim.lastCommandResult?.id??null)!==old',arg=previous,timeout=15000)
            check('真实右键命令已受理',page.evaluate('(range.sim.lastCommandResult?.accepted??0)>0'),page.evaluate('range.sim.lastCommandResult'))
            page.locator('#stop').click()
            check('停止清空队列',page.evaluate('id=>range.sim.units.find(u=>u.id===id).queue.length===0',heli))
            select(heli)
            field=page.locator('#battlefield').bounding_box()
            page.mouse.move(field['x']+field['width']*.6,field['y']+field['height']*.6)
            page.mouse.wheel(0,-650)
            page.wait_for_function('Math.abs(range.view.span-range.view.targetSpan)<.02',timeout=20000);settle_map()
            page.screenshot(path=str(directory/'直升机近景.png'))
            m=metrics();check('近景名牌保持贴身',bool(m) and all(r['gap']<=30.1 for r in m),m)
            # 同一近景下旋转镜头，随后等待真实收敛再采样。
            page.locator('#battlefield').focus()
            before=page.evaluate('range.view.angle')
            page.keyboard.down('KeyE')
            page.wait_for_function('a=>range.view.angle>a+.12',arg=before,timeout=10000)
            page.keyboard.up('KeyE')
            page.wait_for_function('Math.abs(range.view.angle-range.view.targetAngle)<.000001',timeout=20000)
            m=metrics();check('旋转后名牌仍在局部范围',bool(m) and all(r['gap']<=30.1 for r in m),m)

            page.locator('#pause').click()
            samples=page.evaluate("""()=>new Promise(resolve=>{const frames=[],start=performance.now();function sample(){const v=range.view;frames.push(v.labelStats.rows.filter(r=>r.kind==='unit'&&r.visible).map(r=>({id:r.unitId,x:r.x,y:r.y,slot:r.slot})));if(performance.now()-start>=1500)resolve(frames);else requestAnimationFrame(sample);}sample();})""")
            check('静止布局无无故跳槽',all(frame==samples[0] for frame in samples[1:]),{'frames':len(samples)})
            # 使用页面已有总览按钮，不直接写入镜头或模拟内部状态。
            page.locator('#overview').click()
            page.wait_for_function('!range.view.getCameraTransitionState().active&&!range.view.centerTarget',timeout=20000)
            settle_map()
            m=metrics();check('总览名牌有界且采用兵种图标',bool(m) and all(r['gap']<=30.1 for r in m) and any(r['compact']=='true' for r in m),m)
            page.screenshot(path=str(directory/'全场总览.png'))
            check('引线不拦截机身输入',page.evaluate("getComputedStyle(range.view.nameplateLines).pointerEvents==='none'"))
            page.locator('[data-layer="ground"]').click()
            check('地面筛选不显示空中名牌',page.locator('.unit-label[data-kind="heli"]:visible').count()==0 and page.locator('.unit-label[data-kind="plane"]:visible').count()==0)
            page.locator('[data-layer="all"]').click()
            page.locator('#reset').click()
            page.wait_for_function('Math.abs(range.view.getCameraState().viewWidth-220)<1',timeout=15000)
            select(heli);settle_map()
            page.screenshot(path=str(directory/'最终效果.png'))
            check('页面和Shader无异常',not result['errors'],result['errors'])
            result['passed']=True
        except Exception as error:
            result['passed']=False;result['failure']=repr(error)
            page.screenshot(path=str(directory/'失败.png'))
            raise
        finally:
            result['finished_at']=time.time()
            (directory/'验证结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
            browser.close()
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=['chrome','msedge','both'],default='msedge')
    parser.add_argument('--run',required=True,help='本轮独立结果目录名')
    args=parser.parse_args()
    if Path(args.run).name!=args.run:raise ValueError('只接受独立目录名')
    for channel in (['msedge','chrome'] if args.channel=='both' else [args.channel]):
        run(channel,OUTPUT/'验证'/args.run/channel)

