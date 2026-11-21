"""双浏览器只访问独立收集服务，核对区域、总览、视口与销毁，无原批次HTTP依赖。"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def run(args):
    if urlsplit(args.url).hostname != '127.0.0.1':
        raise ValueError('仅核验本机只读收集服务')
    output = args.directory / '独立包网页核验' / args.revision
    output.mkdir(parents=True, exist_ok=False)
    receipt = args.collection / '依赖收集凭证.json'
    result = {'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(), 'browsers': [],
              'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': '真实独立HTTP闭包、相机及释放，不继承主验帧时间或设备性能'}
    with sync_playwright() as pw:
        for channel in ['chrome', 'msedge']:
            evidence = output / channel;evidence.mkdir()
            row = {'browser': channel, 'errors': [], 'requests': []}
            browser = pw.chromium.launch(channel=channel, headless=False)
            page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
            page.on('pageerror', lambda error: row['errors'].append(error.stack or str(error)))
            page.on('console', lambda message: row['errors'].append(message.text) if message.type == 'error' else None)
            page.on('response', lambda response: row['errors'].append(f'{response.status} {response.url}') if response.status >= 400 else None)
            page.on('request', lambda request: row['requests'].append(request.url))
            def wait(expression, timeout=180):
                deadline = time.time()+timeout
                while not page.evaluate(expression):
                    if row['errors']:
                        raise RuntimeError(row['errors'])
                    if time.time()>deadline:
                        raise TimeoutError(expression)
                    page.wait_for_timeout(100)
            try:
                page.goto(args.url)
                wait('!!window.sceneViewer?.map&&!sceneViewer.map.stats.pending')
                row['near'] = page.evaluate('({stats:{...sceneViewer.map.stats},view:sceneViewer.view(),internal:[sceneViewer.renderer.domElement.width,sceneViewer.renderer.domElement.height]})')
                row['near']['view'].pop('camera', None)
                assert not row['near']['stats']['lastError'] and row['near']['internal'][0] == 640
                page.screenshot(path=str(evidence / '独立近景.png'))
                page.locator('[data-scene-action="overview"]').click()
                wait('sceneViewer.map.stats.sceneLevel==="far"&&!sceneViewer.map.stats.pending')
                row['overview'] = page.evaluate('({stats:{...sceneViewer.map.stats},view:{center:sceneViewer.view().center,span:sceneViewer.view().span,height:sceneViewer.view().height}})')
                assert not row['overview']['stats']['lastError'] and row['overview']['view']['height'] <= 9000.01
                row['visible_water_meshes']=page.evaluate('sceneViewer.map.visibleWaterMeshes().length')
                assert row['overview']['stats']['waterReady'] and row['visible_water_meshes']==2,'独立包遗漏原水面渲染'
                page.screenshot(path=str(evidence / '独立总览.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_timeout(300)
                rect = page.evaluate('(()=>{const c=sceneViewer.renderer.domElement,r=c.getBoundingClientRect();return{width:r.width,height:r.height,internal:[c.width,c.height],overflow:document.documentElement.scrollWidth>innerWidth};})()')
                assert not rect['overflow'] and rect['width']/rect['height'] >= 1.749 and rect['internal'][0] == 640
                row['portrait'] = rect
                page.screenshot(path=str(evidence / '高窄视口.png'))
                assert not [url for url in row['requests'] if url.startswith(('http:', 'https:')) and not url.startswith(args.url.rstrip('/')+'/')], '收集包仍访问其他HTTP服务'
                page.evaluate('sceneViewer.dispose()')
                wait('!document.querySelector("#scene canvas")', 10)
                row['disposed'] = True
                assert not row['errors'], row['errors']
                row['passed'] = True
                print(channel+'：独立收集包加载、总览、窄屏及释放通过', flush=True)
            except BaseException as error:
                row.update(passed=False, exception=repr(error))
                page.screenshot(path=str(evidence / '失败.png'))
                raise
            finally:
                browser.close();result['browsers'].append(row)
                (evidence / '核验凭证.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), 'utf-8')
                (output / '验证结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    result['passed'] = all(row['passed'] for row in result['browsers'])
    (output / '验证结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--collection', required=True, type=Path)
    parser.add_argument('--url', required=True)
    parser.add_argument('--revision', required=True)
    run(parser.parse_args())
