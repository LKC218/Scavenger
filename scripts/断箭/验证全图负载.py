"""按冻结位置执行真实前台负载矩阵，记录整帧、模拟、同屏、声音和地图资源。"""

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')


def run(args):
    output = args.directory / '负载正式验证' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    root = args.directory.parents[1]
    location_file = root / '负载冻结/r01/负载与位置.json'
    locations = json.loads(location_file.read_text('utf-8'))
    url = f'http://127.0.0.1:{args.port}/'
    with urlopen(url+'api/status') as response:
        identity = json.load(response)
    result = {'browser': args.browser, 'identity': identity, 'samples': [], 'errors': [], 'scope': '正式负载' if not args.quick else '探索短样本，不能升级A3',
              'location_sha256': hashlib.sha256(location_file.read_bytes()).hexdigest(), 'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    dense, forest = locations['main_load'], locations['dense_vegetation']
    cases = [
        ('密集区空场', 0, 'static', 60, {'tank': 0, 'heli': 0, 'plane': 0}, dense, False, 58),
        ('十辆坦克移动', 10, 'move', 60, {'tank': 10, 'heli': 0, 'plane': 0}, dense, False, 140),
        ('百单位连续移动交战', 100, 'combat', 180, {'tank': 80, 'heli': 16, 'plane': 4}, dense, False, 140),
        ('百单位全程同屏', 100, 'combat', 180, {'tank': 80, 'heli': 16, 'plane': 4}, dense, True, 3000),
        ('植被峰值空场', 0, 'static', 60, {'tank': 0, 'heli': 0, 'plane': 0}, forest, False, 140),
        ('全图总览', 30, 'static', 60, None, {'x': 75, 'z': 350}, True, 12150),
    ]
    if args.case:
        cases = [case for case in cases if case[0] == args.case]
        if not cases:
            raise ValueError('不存在指定负载')
    if args.resume_from:
        names=[case[0] for case in cases]
        if args.resume_from not in names:
            raise ValueError('续跑起点不在固定矩阵中')
        cases=cases[names.index(args.resume_from):]
    result['selected_cases']=[case[0] for case in cases]
    result['matrix_complete']=len(cases)==6
    result['unexpected_lifecycle_events']=[]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False, args=['--disable-frame-rate-limit', '--disable-gpu-vsync'])
        result['version'] = browser.version
        result['browser_processes']=browser.new_browser_cdp_session().send('SystemInfo.getProcessInfo')['processInfo']
        closing={'expected':False}
        def lifecycle(name):
            if not closing['expected']:
                result['unexpected_lifecycle_events'].append({'event':name,'epoch':time.time()})
        browser.on('disconnected',lambda _:lifecycle('browser_disconnected'))
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        navigation={'initial':True}
        def navigated(frame):
            if frame==page.main_frame and not navigation['initial'] and not closing['expected']:
                lifecycle('unexpected_main_navigation')
                result['unexpected_lifecycle_events'][-1]['in_scope']=frame.url.startswith(url)
        page.on('framenavigated',navigated)
        page_cdp=page.context.new_cdp_session(page)
        page_cdp.send('Page.enable');page_cdp.send('Network.enable')
        page_cdp.on('Page.frameRequestedNavigation',lambda event:result.setdefault('navigation_requests',[]).append({'reason':event.get('reason'),'in_scope':event.get('url','').startswith(url),'epoch':time.time()}))
        page.on('crash',lambda _:lifecycle('page_crash'))
        page.on('close',lambda _:lifecycle('page_close'))
        page.on('pageerror', lambda e: result['errors'].append(e.stack or str(e)))
        page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
        try:
            page.goto(url)
            page.wait_for_function('window.range?.ready', timeout=90000)
            navigation['initial']=False
            result['initial_document']=page.evaluate('({origin:performance.timeOrigin,discarded:document.wasDiscarded??false})')
            assert page.evaluate('range.mixer.runtime.masterVolume')==.25, '主验必须使用工程师指定的25%总音量'
            page.evaluate('document.title="Airbase自动性能测试 · 完成后自动关闭"')
            page.bring_to_front()
            page.evaluate('''()=>{window.sampleScheduler={longTasks:[],longFrames:[],visibility:[]};for(const [type,key]of [['longtask','longTasks'],['long-animation-frame','longFrames']])if(PerformanceObserver.supportedEntryTypes.includes(type)){new PerformanceObserver(list=>{for(const entry of list.getEntries())sampleScheduler[key].push(entry.toJSON());}).observe({type,buffered:false});}for(const type of ['focus','blur','visibilitychange'])addEventListener(type,()=>sampleScheduler.visibility.push({type,ms:performance.now(),hidden:document.hidden,focused:document.hasFocus()}),true);}''')
            page.evaluate('''()=>{window.benchmarkInputActive=false;window.benchmarkInputEvents=[];for(const type of ['keydown','keyup','pointerdown','pointerup','pointermove','wheel','click','dblclick','contextmenu'])addEventListener(type,event=>{if(!window.benchmarkInputActive)return;benchmarkInputEvents.push({type:event.type,target:event.target?.id??'',ms:performance.now()});event.stopImmediatePropagation();if(event.cancelable)event.preventDefault();},{capture:true,passive:false});}''')
            assert page.evaluate('typeof range.configureBenchmark') == 'function'
            page.locator('#sound').click()
            page.wait_for_function('range.mixer.active')
            for name, count, mode, duration, composition, center, all_visible, span in cases:
                parameters = {'count': count, 'mode': mode, 'composition': composition, 'center': {'x': center['x'], 'z': center['z']}, 'allVisible': all_visible, 'span': span}
                result['active'] = {'name': name, 'parameters': parameters, 'started_epoch': time.time()}
                save(output / '进行中.json', result)
                state = page.evaluate('options=>range.configureBenchmark(options)', parameters)
                assert state['totalUnits'] == count
                result['active']['frozen_camera']=state['camera']
                page.bring_to_front()
                page.evaluate('window.benchmarkInputEvents=[];window.benchmarkInputActive=true;window.sampleScheduler={longTasks:[],longFrames:[],visibility:[]}')
                duration = args.quick if args.quick else duration
                page.evaluate('''p=>{range.resetMetrics();range.benchmark.start(performance.now(),p.seconds,{...range.perf(),name:p.name,map:range.sim.map.id,audio:range.mixer.active,window:true});}''', {'seconds': duration, 'name': name})
                started, last_write = time.time(), time.time()
                while True:
                    status = page.evaluate('({running:!!range.benchmark.run,completed:!!range.benchmark.last,hidden:document.hidden})')
                    assert not status['hidden'], '正式前台样本页面进入后台或被最小化'
                    if status['completed'] and not status['running']:
                        break
                    if result['errors']:
                        raise RuntimeError('页面运行错误：'+result['errors'][-1])
                    if not status['running']:
                        raise RuntimeError('采样被中止：'+page.locator('#bench-status').inner_text())
                    now = time.time()
                    if now-last_write >= 15:
                        result['active'].update(elapsed_s=now-started, latest=page.evaluate('range.perf()'))
                        save(output / '进行中.json', result)
                        print(args.browser+'：'+name+' '+str(round(now-started, 1))+'秒', flush=True)
                        last_write = now
                    if now-started > duration+80:
                        raise TimeoutError('负载样本未在规定时长完成')
                    page.wait_for_timeout(250)
                receipt = page.evaluate('range.benchmark.last')
                receipt['final'] = page.evaluate('range.perf()')
                receipt['parameters'] = parameters
                receipt['frozen_camera']=state['camera']
                receipt['ignored_input_events']=page.evaluate('benchmarkInputEvents')
                receipt['scheduler_observation']=page.evaluate('sampleScheduler')
                page.evaluate('window.benchmarkInputActive=false')
                receipt['formal'] = not bool(args.quick)
                samples = receipt['frame_times_ms']
                receipt['average_fps'] = 1000*len(samples)/sum(samples)
                receipt['long_frame_ratio'] = sum(t>33.3 for t in samples)/len(samples)
                receipt['simulated_seconds'] = receipt['final']['time']-receipt['time']
                assert receipt['simulated_seconds'] >= (duration+10)*.98, '模拟推进不足，不能以停滞场景验收性能'
                if all_visible and count:
                    receipt['minimum_visible_units'] = min(s['visibleUnits'] for s in receipt['timeline'])
                    assert receipt['minimum_visible_units'] == count, ('未保持全部单位入画', receipt['minimum_visible_units'])
                assert receipt['final']['internal'] == [640, 364]
                assert receipt['final']['enhancementDb'] == 6
                assert receipt['final']['masterVolume'] == .25
                for row in receipt['timeline']:
                    assert all(abs(row['camera'][key]-state['camera'][key])<.001 for key in ['x','z','span']), '固定主验镜头发生改变'
                    stats = row['mapStats']
                    assert stats['visibleChunks'] <= 49 and stats['maxRequests'] <= 4 and stats['maxTranscodes'] <= 2
                    assert stats['simulationBytes'] <= 64*1048576 and stats['decodedBytes'] <= 256*1048576
                    assert stats['geometryBytes'] <= 192*1048576 and row['textureEstimateBytes'] <= 256*1048576
                    assert row['jsHeap'] <= 512*1048576 and stats['downloadCacheBytes'] <= 64*1048576
                if count == 10 and mode == 'move':
                    assert max(s['movingUnits'] for s in receipt['timeline']) == 10, '十车移动负载未实际建立'
                if count == 100 and mode == 'combat' and not args.quick:
                    assert receipt['final']['shots'] > 100 and receipt['final']['hits'] > 0
                    assert max(s['movingUnits'] for s in receipt['timeline']) >= 30
                    assert max(s['voices'] for s in receipt['timeline']) > 0
                receipt['performance_passed'] = receipt['average_fps'] >= 60 and receipt['p95'] <= 16.67 and receipt['p99'] <= 25 and receipt['long_frame_ratio'] < .001 and receipt['max'] <= 100
                save(output / (name+'.json'), receipt)
                page.screenshot(path=str(output / (name+'.png')))
                result['samples'].append({'name': name, 'performance_passed': receipt['performance_passed'], 'p95': receipt['p95'], 'p99': receipt['p99'], 'maximum': receipt['max'], 'average_fps': receipt['average_fps']})
                save(output / '进行中.json', result)
                print(json.dumps(result['samples'][-1], ensure_ascii=False), flush=True)
                if not args.quick:
                    assert receipt['performance_passed'], result['samples'][-1]
                page.evaluate('range.benchmark.last=null')
            result['passed'] = not result['errors'] and (not args.quick) and all(s['performance_passed'] for s in result['samples'])
            if not args.quick:
                assert result['passed'], result['errors']
        except BaseException as error:
            result.update(passed=False, exception=repr(error))
            try:
                result['failure_document']=page.evaluate('({in_scope:location.origin===new URL("'+url+'").origin,title:document.title,origin:performance.timeOrigin,discarded:document.wasDiscarded??false,range_type:typeof window.range,ready:!!window.range?.ready,navigation:performance.getEntriesByType("navigation").map(row=>({type:row.type,start:row.startTime}))})')
            except Exception:pass
            try:
                result['failure_state'] = page.evaluate('range.perf()')
                result['failure_benchmark'] = page.evaluate('range.benchmark.last')
                page.screenshot(path=str(output / '失败.png'))
            except Exception:
                pass
            try:page.screenshot(path=str(output/'失败页面.png'))
            except Exception:pass
            raise
        finally:
            closing['expected']=True
            browser.close()
            save(output / '核验凭证.json', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], required=True)
    parser.add_argument('--port', type=int, default=8784)
    parser.add_argument('--quick', type=int)
    parser.add_argument('--case')
    parser.add_argument('--resume-from', help='从固定矩阵中指定项目继续；子集通过不能替代六项矩阵覆盖')
    run(parser.parse_args())
