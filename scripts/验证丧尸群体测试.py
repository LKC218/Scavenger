"""真实浏览器操作回归与前台群体性能采样；功能检查和可见窗口性能分别归档。"""
import argparse
import json
import math
from pathlib import Path
import time
import ctypes
from ctypes import wintypes
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001/浏览器验证'
URL='http://127.0.0.1:8769/'


class NativeWindow:
    """Playwright可模拟页面焦点，性能验收另核对Windows实际前台进程。"""
    def __init__(self,browser):
        self.u=ctypes.windll.user32
        self.u.GetForegroundWindow.restype=wintypes.HWND
        self.u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        for name in ['ShowWindow','BringWindowToTop','SetForegroundWindow','IsWindowVisible','IsIconic']:
            getattr(self.u,name).argtypes=[wintypes.HWND]+([ctypes.c_int] if name=='ShowWindow' else [])
        info=browser.new_browser_cdp_session().send('SystemInfo.getProcessInfo')
        self.pid=int(next(p['id'] for p in info['processInfo'] if p['type']=='browser'))
        self.hwnd=None
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def visit(hwnd,_):
            pid=wintypes.DWORD();self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value==self.pid and self.u.IsWindowVisible(hwnd):self.hwnd=hwnd
            return True
        self.u.EnumWindows(callback(visit),0)
        if not self.hwnd:raise RuntimeError('未找到测试浏览器原生窗口')
    def proof(self):
        pid=wintypes.DWORD();self.u.GetWindowThreadProcessId(self.u.GetForegroundWindow(),ctypes.byref(pid))
        return {'foreground':pid.value==self.pid,'visible':bool(self.u.IsWindowVisible(self.hwnd)),'minimized':bool(self.u.IsIconic(self.hwnd))}
    def activate(self,page):
        page.bring_to_front();self.u.ShowWindow(self.hwnd,9)
        fg=self.u.GetForegroundWindow();fg_thread=self.u.GetWindowThreadProcessId(fg,None)
        current=ctypes.windll.kernel32.GetCurrentThreadId()
        attached=bool(self.u.AttachThreadInput(current,fg_thread,True)) if fg_thread!=current else False
        try:self.u.BringWindowToTop(self.hwnd);self.u.SetForegroundWindow(self.hwnd)
        finally:
            if attached:self.u.AttachThreadInput(current,fg_thread,False)
        if not self.proof()['foreground']:raise RuntimeError('Windows未允许激活测试浏览器窗口')


def measure(page,native,button,duration,name):
    """窗口被切走时保存中断记录，恢复后重测本组，最多三次。"""
    for trial in range(3):
        native.activate(page);page.locator(button).click();checks=0
        assert page.evaluate('() => horde.sampleStatus().state')=='warmup'
        assert page.locator('#mode').is_disabled()
        deadline=time.monotonic()+duration+45;interruption=None
        while page.evaluate('() => horde.sampleStatus().state')!='done':
            proof=native.proof();checks+=1
            if not proof['foreground'] or not proof['visible'] or proof['minimized']:
                interruption={'reason':'Windows原生窗口不在前台','proof':proof};break
            if page.evaluate('() => horde.sampleStatus().state')=='idle':
                interruption={'reason':'采样被外部操作取消'};break
            if time.monotonic()>deadline:raise TimeoutError('采样超出绝对截止时间')
            try:page.wait_for_function('() => horde.sampleStatus().state === "done"',timeout=1000)
            except Exception as exc:
                if 'Timeout' not in type(exc).__name__:raise
        if interruption:
            interruption['snapshot']=page.evaluate('() => horde.snapshot()')
            save(OUT/(name+f'-窗口中断-{time.time_ns()}.json'),interruption)
            native.activate(page)
            if page.locator('#cancel').is_visible():page.locator('#cancel').click()
            print(name+'：检测原生窗口中断，重测本组',flush=True);continue
        data=page.evaluate('() => horde.result()')
        if not data['valid']:
            save(OUT/(name+f'-页面中断-{time.time_ns()}.json'),data)
            print(name+'：页面焦点中断，重测本组',flush=True);continue
        data['nativeForegroundVerified']=True;data['nativeForegroundChecks']=checks;data['nativeWindowAtEnd']=native.proof()
        return data
    raise RuntimeError('连续三次窗口中断，请保持测试浏览器前台后续跑')


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8')


def run(channel,performance=False):
    OUT.mkdir(parents=True,exist_ok=True)
    result={'channel':channel,'headless':not performance,'viewport':[1600,1000],'checks':[],'errors':[],'samples':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=not performance)
        result['browser_version']=browser.version
        page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1,accept_downloads=True)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
        def check(name):result['checks'].append(name);print(channel+'：'+name,flush=True)
        def snapshot():return page.evaluate('() => horde.snapshot()')
        def wait(js,timeout=30000):page.wait_for_function('() => '+js,timeout=timeout)
        def world(x,z,button='right'):
            p=page.evaluate('p => horde.screen(p[0],p[1])',[x,z]);page.mouse.click(p['x'],p['y'],button=button)
        try:
            page.goto(URL,wait_until='networkidle');page.bring_to_front();wait('window.horde?.ready && horde.snapshot().row !== null')
            native=NativeWindow(browser) if performance else None
            first=snapshot();result['environment']=first['environment']
            assert first['health']['count']==500 and first['row']['visible']==500
            assert first['animation']['independentSkeletons']==500 and first['animation']['sharedGeometry']==1
            assert first['animation']['bones']==23 and first['row']['animated']==500
            check('首次加载：500只、独立23骨骼、共享网格、500只位于视锥内')
            if performance:
                # 浏览器前台、无开发者工具、无录屏；每个样本独立重置及预热。
                cases=[(n,m,640,60) for n in (100,300,500) for m in ('static','animated','open','channel')]
                cases += [(500,'open',480,60),(500,'open',1600,60),(500,'open',640,180),(500,'channel',640,180)]
                for i,(n,mode,width,duration) in enumerate(cases):
                    name=f'{channel}-{i+1:02d}-{n}-{mode}-{width}-{duration}'
                    previous=OUT/(name+'.json')
                    if previous.exists():
                        old=json.loads(previous.read_text('utf-8'))
                        if old.get('valid') and old.get('nativeForegroundVerified') and old['config']['code_sha256']==first['config']['code_sha256'] and old.get('browser_version')==browser.version:
                            result['samples'].append({'file':name+'.json','count':n,'mode':mode,'width':width,'duration':duration,'valid':True,'summary':old['summary'],'endHealth':old.get('endHealth',{})})
                            save(OUT/(channel+'-性能汇总.json'),result);check(name+'：复用同版本有效样本');continue
                    native.activate(page);page.locator('#count').select_option(str(n));page.locator('#mode').select_option(mode);page.locator('#quality').select_option(str(width))
                    data=measure(page,native,'#long-sample' if duration==180 else '#sample',duration,name)
                    data['browser_version']=browser.version;data['headless']=False
                    data['endHealth']=snapshot()['health']
                    save(OUT/(name+'.json'),data)
                    s=data['summary'];health=snapshot()['health']
                    row={'file':name+'.json','count':n,'mode':mode,'width':width,'duration':duration,'valid':data['valid'],'summary':s,'endHealth':health}
                    result['samples'].append(row);save(OUT/(channel+'-性能汇总.json'),result)
                    assert s['minVisible']==n and health['finite'] and health['insideObstacles']==0
                    assert s['minAnimated']==(0 if mode=='static' else n)
                    check(f'{n}只 {mode} {width}宽 {duration}秒：{s["fps"]:.1f}FPS，P95 {s["p95Ms"]:.2f}ms，有效={data["valid"]}')
                    if i==0:
                        for fmt in ('json','csv'):
                            with page.expect_download() as pending:page.locator('#'+fmt).click()
                            download=pending.value;download.save_as(OUT/(channel+'-导出检查.'+fmt));assert (OUT/(channel+'-导出检查.'+fmt)).stat().st_size>100
                        check('JSON和CSV按钮实际下载通过')
                    if n==500 and mode in ('open','channel') and width==640:
                        page.screenshot(path=str(OUT/(name+'.png')),full_page=True)
                assert all(s['valid'] for s in result['samples']),'存在窗口中断的样本，须重测该条件'
            else:
                wait('horde.snapshot().health.elapsed > 1.5');after=snapshot()
                assert math.dist(first['animation']['vertex'],after['animation']['vertex'])>.005
                assert len(set(round(x,2) for x in after['animation']['phases']))>1
                assert math.dist(list(first['agents'][0].values()),list(after['agents'][0].values()))>.2
                check('实际蒙皮顶点变化、独立动画相位和真实位移')
                world(17,8);state=snapshot();assert not state['config']['patrol'] and abs(state['target']['z']-8)<.1
                check('右键地面更新整群目标，关闭自动往返')
                page.locator('#pause').click();state=snapshot();frozen=state['health']['ticks']
                page.keyboard.down('KeyD');page.wait_for_function('(x) => horde.snapshot().config.view.center[0] > x+1',arg=state['config']['view']['center'][0]);page.keyboard.up('KeyD')
                assert snapshot()['health']['ticks']==frozen;check('暂停冻结模拟，镜头仍可移动')
                page.locator('#home').click();wait('Math.abs(horde.snapshot().config.view.center[0]) < .05')
                box=page.locator('#viewport').bounding_box();page.mouse.move(box['x']+box['width']*.65,box['y']+box['height']*.5)
                before=snapshot()['config']['view']['span'];page.mouse.wheel(0,-200);wait(f'horde.snapshot().config.view.span < {before}-1')
                page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down(button='middle');page.mouse.move(box['x']+box['width']/2+70,box['y']+box['height']/2,steps=8);page.mouse.up(button='middle');wait('Math.abs(horde.snapshot().config.view.angle) > .1')
                page.locator('#focus').click();wait('horde.snapshot().config.view.center[0] < -5');check('滚轮缩放、中键旋转和主动聚焦')
                page.locator('#pause').click();page.locator('#count').select_option('100');assert snapshot()['health']['count']==100
                page.locator('#count').select_option('300');assert snapshot()['health']['count']==300
                page.locator('#count').select_option('500');check('100、300和500数量切换及重置')
                page.locator('#home').click();page.locator('#mode').select_option('channel');wait('horde.snapshot().health.elapsed > 14',timeout=30000)
                health=snapshot()['health'];assert health['insideObstacles']==0 and health['finite'] and health['rightSide']>0
                check('500只通道推进：有人通过、无穿墙或无效坐标')
                result['channelSnapshot']=snapshot();page.screenshot(path=str(OUT/(channel+'-功能检查.png')),full_page=True)
                page.locator('#mode').select_option('static');wait('horde.snapshot().row.animated === 0')
                page.locator('#mode').select_option('animated');wait('horde.snapshot().row.animated === 500');check('静态与真实动画负载切换')
                for width in ('480','1600','640'):
                    page.locator('#quality').select_option(width);assert snapshot()['config']['view']['internal']==[int(width),int(width)*9//16]
                check('三档实际内部渲染尺寸')
                page.locator('#sample').click();page.evaluate('() => window.dispatchEvent(new Event("blur"))');page.locator('#cancel').click()
                assert not page.locator('#mode').is_disabled();check('采样锁定、窗口中断处理及取消恢复')
            assert not result['errors'],result['errors']
        except Exception as exc:
            result['failure']=repr(exc);page.screenshot(path=str(OUT/(channel+'-失败.png')),full_page=True);raise
        finally:
            result['finishedAt']=time.time();save(OUT/(channel+('-性能汇总.json' if performance else '-功能检查.json')),result);browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',choices=['chrome','msedge'],default='chrome');parser.add_argument('--performance',action='store_true')
    args=parser.parse_args();run(args.browser,args.performance)
