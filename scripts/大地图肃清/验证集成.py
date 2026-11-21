"""本批固定候选的浏览器检查和串行长测；技术结果不替代工程师人工验收。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

ROOT=Path(__file__).resolve().parents[2]
HOME=ROOT/'local-data/大地图肃清/首批/V001-01a1ac6a'
URL='http://127.0.0.1:8810/'


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')


def wait_ready(page):
    page.goto(URL,wait_until='domcontentloaded')
    page.wait_for_function('window.scavenger?.ready===true || window.scavenger?.errors?.length>0',timeout=120000)
    if not page.evaluate('window.scavenger.ready'):
        raise RuntimeError(str(page.evaluate('window.scavenger.errors')))


def setup_battle(page,preset):
    return page.evaluate('''preset=>{
      const a=window.scavenger;a.reset({preset,seed:1105});
      a.interaction.setView?.({focus:{x:0,y:0,z:0},viewWidth:preset==='individuals'?360:460,yaw:0});
      const ids=a.snapshot().units.filter(u=>u.kind==='tank'&&u.alive).map(u=>u.id);
      for(const [weaponId,ammoId] of [['main','heat'],['m2','ball']]){
        a.dispatch({type:'selectAmmo',unitIds:ids,weaponId,ammoId});
        a.dispatch({type:'enableWeapon',unitIds:ids,weaponId,enabled:true});
      }
      return {runId:a.snapshot().runId,counts:a.snapshot().counts};
    }''',preset)


def smoke(channel,folder):
    result={'scope':'无窗口浏览器集成技术检查，不代表前台性能或人工通过','channel':channel,'errors':[],'checks':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' and not m.location.get('url','').endswith('/favicon.ico') else None)
        try:
            wait_ready(page)
            result['browserVersion']=browser.version
            result['identity']=page.request.get(URL+'api/status').json()
            first=page.evaluate('''()=>{const a=window.scavenger,s=a.snapshot();return{ready:a.ready,units:s.units.length,total:s.counts.total,models:Object.keys(a.manifest.models),native:a.manifest.native.models.length,gpu:a.getStats().gpu,buffer:[a.renderer.domElement.width,a.renderer.domElement.height],stats:a.getStats()};}''')
            result['initial']=first
            result['checks'].append({'name':'三款载具两款原作模型实际载入','pass':first['units']==3 and len(first['models'])==3 and first['native']==2})
            page.screenshot(path=str(folder/(channel+'-初始候选.png')),full_page=True)
            shot=page.evaluate('''()=>{const a=window.scavenger,s=a.snapshot(),u=s.units.find(u=>u.kind==='tank');a.interaction.setSelection([u.id]);a.dispatch({type:'guard',unitIds:[u.id],enabled:false});a.dispatch({type:'selectAmmo',unitIds:[u.id],weaponId:'main',ammoId:'heat'});a.dispatch({type:'enableWeapon',unitIds:[u.id],weaponId:'main',enabled:true});const r=a.dispatch({type:'attackGround',unitIds:[u.id],point:{x:u.position.x,z:u.position.z-45}});return r;}''')
            page.wait_for_function('window.scavenger.snapshot().diagnostics.shots>0',timeout=12000)
            result['shot']=page.evaluate('''()=>{const a=window.scavenger;return{commandResult:null,shots:a.snapshot().diagnostics.shots,main:a.snapshot().units[0].weapons.main,presentation:a.presentation.getStats()};}''')
            result['checks'].append({'name':'真实校准主炮经命令消耗弹药并发射','pass':bool(shot.get('ok')) and result['shot']['shots']>0 and result['shot']['main']['ammo']['heat']<8})
            page.screenshot(path=str(folder/(channel+'-主炮发射.png')),full_page=True)
            page.locator('[data-action="audio"]').click()
            page.wait_for_function('window.scavenger.presentation.getAudioState().ready===true',timeout=30000)
            audio=page.evaluate('window.scavenger.presentation.getAudioState()')
            result['audio']=audio
            result['checks'].append({'name':'原作声音按手势载入且无解码失败','pass':audio['ready'] and not audio['errors']})
            for preset in ('low','main','individuals','explore'):
                page.evaluate('p=>window.scavenger.reset({preset:p,seed:1105})',preset)
                page.evaluate('''p=>window.scavenger.interaction.setView?.({focus:{x:0,y:0,z:0},viewWidth:p==='individuals'?360:460,yaw:0})''',preset)
                page.wait_for_function('p=>window.scavenger.snapshot().preset===p',arg=preset)
                page.wait_for_timeout(350)
                record=page.evaluate('''()=>{const a=window.scavenger;return{preset:a.snapshot().preset,counts:a.snapshot().counts,render:a.getStats(),view:a.interaction.getState()};}''')
                result.setdefault('presets',[]).append(record)
                result['checks'].append({'name':preset+'真实逻辑人数与身份守恒','pass':record['counts']['total']=={'low':500,'main':2000,'individuals':500,'explore':5000}[preset] and record['render'].get('identityPreserved',False)})
                page.screenshot(path=str(folder/(channel+'-'+preset+'.png')),full_page=True)
            result['checks'].append({'name':'浏览器无脚本异常','pass':not result['errors']})
        except Exception as e:
            result['failure']=str(e)
            result['errors']+=page.evaluate('window.scavenger?.errors??[]') if not page.is_closed() else []
            page.screenshot(path=str(folder/(channel+'-失败.png')),full_page=True)
        finally:
            browser.close()
    result['pass']=not result.get('failure') and all(c['pass'] for c in result['checks'])
    save(folder/(channel+'-集成检查.json'),result)
    print(json.dumps({'channel':channel,'pass':result['pass'],'checks':result['checks'],'failure':result.get('failure'),'report':str(folder/(channel+'-集成检查.json'))},ensure_ascii=False),flush=True)
    return result['pass']


def performance(channel,folder,presets,duration,warmup,combination,headless=False):
    sys.path.insert(0,str(ROOT/'scripts'))
    from 验证丧尸群体测试 import NativeWindow
    reports=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=headless,args=['--start-fullscreen','--window-position=0,0','--window-size=1920,1080','--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows'])
        page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:errors.append(m.text) if m.type=='error' and not m.location.get('url','').endswith('/favicon.ico') else None)
        wait_ready(page)
        native=None if headless else NativeWindow(browser)
        window_session=None if headless else page.context.new_cdp_session(page)
        window_id=None if headless else window_session.send('Browser.getWindowForTarget')['windowId']
        if native:native.activate(page)
        page.locator('[data-action="audio"]').click();page.wait_for_function('window.scavenger.presentation.getAudioState().ready',timeout=30000)
        plans=[(p,duration,False) for p in presets]+([('main',600,True)] if combination else [])
        for preset,seconds,combined in plans:
            if native:native.activate(page)
            setup_battle(page,preset)
            if window_session:window_session.send('Browser.setWindowBounds',{'windowId':window_id,'bounds':{'windowState':'fullscreen'}})
            page.evaluate('''o=>window.scavenger.startSample(o)''',{'warmup':warmup,'duration':seconds,'allowResets':True})
            started=time.monotonic();deadline=started+seconds+warmup+45;next_note=started;next_reset=started+30;next_camera=started+5;proofs=[];activity=[];interrupted=None
            while page.evaluate('window.scavenger.sampleStatus().state')!='done':
                now=time.monotonic()
                if native:
                    import ctypes
                    from ctypes import wintypes
                    proof=native.proof();rect=wintypes.RECT();native.u.GetClientRect(native.hwnd,ctypes.byref(rect));proof['clientSize']=[rect.right-rect.left,rect.bottom-rect.top]
                    proof['bounds']=window_session.send('Browser.getWindowBounds',{'windowId':window_id})['bounds']
                    proofs.append({'time':time.time(),**proof})
                    if not proof['foreground'] or not proof['visible'] or proof['minimized']:
                        interrupted={'reason':'测试窗口离开实际前台','proof':proof};break
                    if proof['clientSize'][0]<1920 or proof['clientSize'][1]<1080:
                        interrupted={'reason':'1920×1080测试视口未完整容纳于实际客户区','proof':proof};break
                    if proof['bounds'].get('windowState')!='fullscreen':
                        interrupted={'reason':'测试窗口退出全屏，不能以虚拟视口代替完整可见区域','proof':proof};break
                if now>deadline:raise TimeoutError('超过绝对采样截止时间')
                activity.append(page.evaluate('''()=>{const a=window.scavenger,s=a.snapshot(),b=a.presentation.getStats();return{wallTime:performance.now()/1000,runId:s.runId,simTime:s.time,counts:s.counts,shots:s.diagnostics.shots,damageEvents:s.diagnostics.damageEvents,render:{onScreenIndividuals:b.onScreenIndividuals,onScreenProxyMembers:b.onScreenProxyMembers,offscreenAlive:b.offscreenAlive,offscreenCombat:b.offscreenCombat,hidden:b.hidden,corpses:b.corpses,effects:b.effects,identityPreserved:b.identityPreserved},view:a.interaction.getState()};}'''))
                if now>=next_reset:
                    setup_battle(page,preset);next_reset=now+30
                if combined and now>=next_camera:
                    phase=int((now-started)/5)%4
                    page.evaluate('''phase=>{const a=window.scavenger;a.interaction.setView?.({focus:{x:0,y:0,z:0},viewWidth:[360,600,900,600][phase],yaw:[0,.35,.7,0][phase]});}''',phase)
                    next_camera=now+5
                if now>=next_note:
                    print(json.dumps({'channel':channel,'preset':preset,'combined':combined,'status':page.evaluate('window.scavenger.sampleStatus()')},ensure_ascii=False),flush=True)
                    next_note=now+30
                try:page.wait_for_function('window.scavenger.sampleStatus().state==="done"',timeout=1000)
                except PlaywrightTimeout:pass
            result=page.evaluate('window.scavenger.sampleResult()') if not interrupted else {'interrupted':interrupted,'status':page.evaluate('window.scavenger.sampleStatus()')}
            result.update(channel=channel,browserVersion=browser.version,headless=headless,nativeForegroundProofs=proofs,activityChecks=activity,pageErrors=list(errors),combined=combined)
            result['activitySummary']={'checks':len(activity),'combatChecks':sum(1 for x in activity if x['render'].get('offscreenCombat',0)>0),'peakOffscreenCombat':max((x['render'].get('offscreenCombat',0) for x in activity),default=0),'peakShotsPerRound':max((x['shots'] for x in activity),default=0),'note':'真实动态交战曲线；不宣称峰值交战人数全程恒定'}
            if headless or interrupted or errors:result.setdefault('summary',{})['pass']=False
            result['technicalOnly']=True
            name=channel+'-'+preset+('-组合10分钟' if combined else '-'+str(seconds)+'秒')
            save(folder/(name+'.json'),result)
            page.screenshot(path=str(folder/(name+'.png')),full_page=True)
            reports.append({'preset':preset,'combined':combined,'summary':result.get('summary'),'interrupted':interrupted,'report':str(folder/(name+'.json'))})
            print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
            if interrupted:break
        browser.close()
    save(folder/(channel+'-性能汇总.json'),reports)
    return all(r.get('summary',{}).get('pass',False) for r in reports)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=('chrome','msedge'),default='chrome')
    parser.add_argument('--performance',action='store_true')
    parser.add_argument('--headless',action='store_true',help='性能时只能作无窗口诊断，禁止标为正式通过')
    parser.add_argument('--presets',default='low,main,individuals')
    parser.add_argument('--duration',type=float,default=180)
    parser.add_argument('--warmup',type=float,default=30)
    parser.add_argument('--combination',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    folder=args.output or HOME/'技术检查'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+args.channel)
    folder=folder.resolve();folder.relative_to(HOME.resolve());folder.mkdir(parents=True,exist_ok=True)
    with urlopen(URL+'api/status',timeout=10) as response:identity=json.load(response)
    if identity.get('app')!='scavenger-clearance-first-batch':raise SystemExit('8810不是本批候选，拒绝检查')
    save(folder/'候选身份.json',identity)
    ok=performance(args.channel,folder,args.presets.split(','),args.duration,args.warmup,args.combination,args.headless) if args.performance else smoke(args.channel,folder)
    raise SystemExit(0 if ok else 1)
