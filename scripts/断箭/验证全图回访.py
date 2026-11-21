"""五圈各180秒的固定闭合路线，逐秒检查真实可见地表，并按相同回收条件比较第2／5圈。"""

import argparse
import json
from pathlib import Path
import time
import hashlib

import psutil
from playwright.sync_api import sync_playwright


def process_memory(browser_cdp):
    """仅采样本次浏览器CDP列出的进程；工作集含共享页，不与堆或GPU估算相加。"""
    rows = []
    for item in browser_cdp.send('SystemInfo.getProcessInfo')['processInfo']:
        row = {'pid': item['id'], 'type': item['type']}
        try:
            info = psutil.Process(item['id']).memory_info()
            row['bytes'] = info._asdict()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
            row['unavailable'] = type(error).__name__
        rows.append(row)
    return {'epoch': time.time(), 'processes': rows,
            'scope': '浏览器、渲染、GPU及辅助进程逐进程操作系统读数；rss工作集含共享页，private为私有提交；不代表驱动显存，不能直接与JS堆或地图估算相加'}


def run(args):
    if args.diagnostic_loops and (not args.texture_diagnostic or not args.quick):
        raise ValueError('缩短圈数仅限带纹理追踪的探索样本')
    output = args.directory / '全图回访正式验证' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    result = {'browser': args.browser, 'loops': [], 'errors': [], 'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': '零单位显示回访；单位／命令和弹丸数据独立驻留另作交互验证', 'formal': not bool(args.quick),
              'gc_condition': '每圈回到机场并重新提交同一精确视区，等待请求与转码为0；清理未引用精细页、CPU实例页与下载缓存，保留显示和消费者引用；第2与第5圈同样触发浏览器GC'}
    points = [[0, 85], [4137.5, 3912.5], [-2500, -2400], [4262.5, -2712.5], [-2612.5, 4212.5], [0, 85]]
    duration = args.quick or 180
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False, args=['--disable-frame-rate-limit', '--disable-gpu-vsync'])
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        if args.texture_diagnostic:
            page.add_init_script(path=str(Path(__file__).with_name('纹理驻留诊断.mjs')))
        cdp = page.context.new_cdp_session(page)
        browser_cdp = browser.new_browser_cdp_session()
        result['version'] = browser.version
        result['gpu'] = browser_cdp.send('SystemInfo.getInfo')['gpu']
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda m: result['errors'].append({'text':m.text,'location':m.location}) if m.type == 'error' else None)
        result['request_failures']=[]
        page.on('requestfailed',lambda request:result['request_failures'].append({'url':request.url,'failure':request.failure,'epoch':time.time()}))
        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready', timeout=90000)
            if args.texture_diagnostic:
                page.evaluate('installTextureMaterialDiagnostic()')
            result['identity'] = page.evaluate('fetch("/api/status").then(r=>r.json())')
            freeze = args.directory / ('浏览器源码-'+result['identity']['revision']) / '服务身份.json'
            result['service_freeze_sha256'] = hashlib.sha256(freeze.read_bytes()).hexdigest()
            page.evaluate('()=>range.configureBenchmark({count:0,mode:"static",composition:{tank:0,heli:0,plane:0},span:140,center:{x:0,z:85}})')
            page.evaluate('range.sim.paused=true')
            for loop in range(1, (args.diagnostic_loops or 5)+1):
                page.evaluate('''p=>{const v=range.view;v.stopCamera();v.span=v.targetSpan=140;const start=performance.now();window.revisit={done:false,timeline:[],start};let next=0;const tick=now=>{const elapsed=Math.max(0,(now-start)/1000),f=Math.min(1,elapsed/p.duration),segment=Math.min(p.points.length-2,Math.floor(f*(p.points.length-1))),t=f===1?1:f*(p.points.length-1)-segment,a=p.points[segment],b=p.points[segment+1];v.center.x=a[0]+(b[0]-a[0])*t;v.center.z=a[1]+(b[1]-a[1])*t;v.updateCamera();range.externalMap.update({center:v.center,span:v.span*v.width/v.height,height:v.camera.position.y-v.center.y});if(elapsed>=next){next=elapsed+1;let checked=0,misses=0;const meshes=range.externalMap.visibleGroundMeshes();for(const x of [.1,.3,.5,.7,.9])for(const y of [.1,.3,.5,.7,.9]){const point=v.ground(x*v.width,y*v.height),bounds=range.externalMap.bounds;if(!point||point.x<bounds.minX||point.x>bounds.maxX||point.z<bounds.minZ||point.z>bounds.maxZ)continue;checked++;if(!v.ray(x*v.width,y*v.height).intersectObjects(meshes,false).length)misses++;}window.revisit.timeline.push({seconds:elapsed,checked,misses,...range.perf()});}if(f<1)requestAnimationFrame(tick);else window.revisit.done=true;};requestAnimationFrame(tick);}''', {'points': points, 'duration': duration})
                start, last = time.time(), time.time()
                while not page.evaluate('revisit.done'):
                    if time.time()-last >= 15:
                        result['active'] = {'loop': loop, 'elapsed_s': time.time()-start, 'latest': page.evaluate('range.perf()')}
                        (output / '进行中.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
                        print(f'{args.browser}：回访第{loop}圈 {round(time.time()-start,1)}秒', flush=True)
                        last = time.time()
                    if time.time()-start > duration+40:
                        raise TimeoutError('闭合路线没有结束')
                    page.wait_for_timeout(300)
                # 连续移动有16米请求阈值；强制同一终点正常提交，保证第2／5圈比较相同资源集合。
                page.evaluate('''async()=>{const v=range.view,m=range.externalMap;v.center.x=0;v.center.z=85;v.updateCamera();m.retry();m.update({center:v.center,span:v.span*v.width/v.height,height:v.camera.position.y-v.center.y,camera:v.camera});await m.waitForView();}''')
                page.wait_for_function('!range.externalMap.stats.pending && !range.externalMap.stats.requests && !range.externalMap.stats.dataPending && !range.externalMap.stats.transcodes', timeout=60000)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                page.evaluate('range.externalMap.compact()')
                if loop in [2, 5]:
                    cdp.send('HeapProfiler.collectGarbage')
                receipt = page.evaluate('({timeline:revisit.timeline,final:range.perf()})')
                if args.texture_diagnostic:
                    receipt['texture_diagnostic'] = page.evaluate('textureDiagnostic()')
                assert all(sample['checked'] >= 9 and sample['misses'] == 0 for sample in receipt['timeline'])
                assert all(sample['mapStats']['visibleChunks'] <= 49 for sample in receipt['timeline'])
                (output / f'第{loop}圈.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), 'utf-8')
                page.screenshot(path=str(output / f'第{loop}圈.png'))
                result['loops'].append({'loop': loop, 'final': receipt['final'], 'samples': len(receipt['timeline']), 'non_source_holes': sum(s['misses'] for s in receipt['timeline']),
                                        'process_memory': process_memory(browser_cdp)})
            if len(result['loops']) < 5:
                result.update(passed=False, diagnostic_complete=True)
                return
            second, fifth = result['loops'][1]['final'], result['loops'][4]['final']
            checks = {}
            for key in ['geometries', 'textures']:
                checks[key] = fifth[key] <= second[key]*1.05
            for key in ['decodedBytes', 'geometryBytes', 'textureBytes', 'simulationBytes']:
                checks[key] = fifth['mapStats'][key] <= second['mapStats'][key]*1.05
            checks['jsHeap'] = fifth['jsHeap']-second['jsHeap'] <= max(16*1048576, second['jsHeap']*.1)
            result['growth_checks'] = checks
            result['passed'] = all(checks.values()) and not result['errors'] and not args.quick
            if not args.quick:
                assert result['passed'], checks
        except BaseException as error:
            result.update(passed=False, exception=repr(error))
            page.screenshot(path=str(output / '失败.png'))
            raise
        finally:
            browser.close()
            (output / '核验凭证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], required=True)
    parser.add_argument('--port', type=int, default=8784)
    parser.add_argument('--quick', type=int)
    parser.add_argument('--texture-diagnostic', action='store_true', help='探索样本追踪WebGL纹理创建、删除及当前材料归属，不用于正式性能结论')
    parser.add_argument('--diagnostic-loops', type=int, choices=range(1, 6), help='纹理探索样本圈数；正式仍固定五圈')
    run(parser.parse_args())
