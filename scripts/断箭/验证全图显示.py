"""原生前台浏览器检查全图显示、分页与回收；不替代载具负载或网络验收。"""

import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def run(args):
    output = args.directory / '全图显示核验' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    result = {'scope': '静态显示与区域资源，不是A3正式性能验收', 'errors': [], 'checks': [], 'samples': []}

    def save():
        (output / '进行中.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')

    deadline = time.time()+90
    while True:
        try:
            with urlopen(f'http://127.0.0.1:{args.port}/api/status', timeout=2) as response:
                identity = json.load(response)
            if identity.get('app') != 'scavenger-airbase-full-scene':
                raise ValueError('端口不是本轮全图候选服务')
            break
        except OSError:
            if time.time() >= deadline:
                raise TimeoutError('候选服务未就绪，尚未开始浏览器样本')
            # 等待仅用于调度，截止时间始终按系统绝对时间计算。
            time.sleep(.25)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False)
        context = browser.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        page = context.new_page()
        def error(message):
            result['errors'].append(str(message))
            save()
        page.on('pageerror', error)
        page.on('console', lambda message: error(message.text) if message.type == 'error' else None)
        page.on('response', lambda response: result['errors'].append(f'HTTP {response.status}: {response.url}') if response.status >= 400 else None)
        try:
            start = time.time()
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.fullMap?.ready || document.getElementById("load-status").textContent.startsWith("预览加载失败")', timeout=120000)
            assert page.evaluate('!!window.fullMap?.ready'), page.locator('#load-status').inner_text()
            page.evaluate('()=>fullMap.map.waitForView()')
            page.wait_for_function('fullMap.map.stats.errors.length || (!fullMap.map.stats.overview && !fullMap.map.stats.pending)', timeout=120000)
            assert not page.evaluate('fullMap.map.stats.errors'), page.evaluate('fullMap.map.stats.errors')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            result.update(version=browser.version, ready_seconds=time.time()-start, identity=page.evaluate('fetch("/api/status").then(r=>r.json())'))
            initial = page.evaluate('fullMap.perf()')
            assert initial['mapStats']['maxRequests'] <= 4
            assert initial['mapStats']['maxTranscodes'] <= 2
            assert initial['mapStats']['visibleChunks'] <= 49
            assert initial['mapStats']['grassEnabled'] is False
            result['initial'] = initial
            page.screenshot(path=str(output / '机场近景.png'))
            page.locator('#overview').click()
            page.wait_for_function('fullMap.map.stats.overview && !fullMap.map.stats.pending')
            page.evaluate('()=>fullMap.map.waitForView()')
            page.screenshot(path=str(output / '全图总览.png'))
            # 同一世界坐标保持不变，过度拉远受高度上限约束，并统一切换远景。
            page.evaluate('()=>fullMap.setCamera(75,350,1000000)')
            page.wait_for_function('fullMap.map.stats.errors.length || (!fullMap.map.stats.pending && !fullMap.map.stats.requests)', timeout=120000)
            result['camera_cap'] = page.evaluate('''()=>({height:fullMap.camera.position.y-fullMap.controls.target.y,max:fullMap.map.camera.max_height_m,level:fullMap.map.stats.sceneLevel})''')
            assert result['camera_cap']['height'] <= result['camera_cap']['max']+.001
            assert result['camera_cap']['level'] == 'far'
            for index, (x, z) in enumerate([(0, 85), (2800, 2400), (-2500, -2400), (0, 85)]):
                page.evaluate('(p)=>fullMap.setCamera(p[0],p[1],140)', [x, z])
                page.wait_for_function('!fullMap.map.stats.overview && !fullMap.map.stats.pending && !fullMap.map.stats.requests', timeout=120000)
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                sample = page.evaluate('fullMap.perf()')
                assert sample['mapStats']['geometryBytes'] <= 192*1048576
                assert sample['mapStats']['textureBytes'] <= 256*1048576
                assert sample['mapStats']['decodedBytes'] <= 256*1048576
                assert sample['mapStats']['dataBytes'] <= 64*1048576
                result['samples'].append(sample)
                page.screenshot(path=str(output / f'区域-{index+1:02}.png'))
                save()
            # 快速转移取消旧视区；最后地点须保持唯一新一轮的可见实例和资源。
            page.evaluate('''()=>{for(const p of [[-3500,3500],[3500,-3500],[-2500,-2000],[0,85]])fullMap.setCamera(...p,100);}''')
            page.wait_for_function('!fullMap.map.stats.overview && !fullMap.map.stats.pending && !fullMap.map.stats.requests', timeout=120000)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            result['after_cancellation'] = page.evaluate('fullMap.perf()')
            result['checks'] = ['前台原生浏览器', '草丛未进入运行', '近景与总览切换', '镜头最大高度及全局远景切换', '跨区及回访资源预算', '快速转移与取消']
            result['passed'] = not result['errors'] and not result['after_cancellation']['mapStats']['errors']
            (output / '核验凭证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
            assert result['passed'], result['errors']
            print(json.dumps({'passed': True, 'browser': args.browser, 'version': browser.version}, ensure_ascii=False))
        except BaseException as error:
            result['exception'] = repr(error)
            page.screenshot(path=str(output / '失败.png'))
            save()
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8782)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], required=True)
    parser.add_argument('--run', required=True)
    run(parser.parse_args())
