"""核对可读路径迁移、全部HTTP媒体入口和双浏览器实际加载。"""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from 统一资源 import ROOT, HOME, current, read, routes_absolute, sha, save

OUTPUT = ROOT / 'local-data/资源库命名/可读迁移V002-01a1a4e6'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/断箭'))


def load(file, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run():
    receipt = read(OUTPUT / '迁移回执.json')
    pointer, catalog, routes, index = current()
    assert sha(HOME / '当前版本.json') == receipt['new_pointer_sha256']
    before = read(OUTPUT / '旧指针.json')
    previous = HOME / before['directory']
    for name, expected in before['metadata'].items():
        assert sha(previous / name) == expected, '历史索引被改写'
    old_catalog = read(previous / '资源索引.json')
    assert old_catalog['models'] == catalog['models'] and old_catalog['audio'] == catalog['audio']
    assert old_catalog['summary'] == catalog['summary']
    assert set(routes) == set(read(previous / '资源路由.json'))
    by_target = {row['new_path']: row for row in receipt['moved']}
    for row in receipt['moved']:
        assert not (ROOT / row['old_path']).exists()
        stat = (ROOT / row['new_path']).stat()
        assert stat.st_size == row['bytes'] and stat.st_mtime_ns == row['mtime_ns']
    for url, record in routes.items():
        expected = by_target[record['path']]
        assert expected['sha256'] == record['sha256'] and expected['bytes'] == record['bytes']
        assert url == '/media/' + record['sha256'] + Path(record['path']).suffix

    def check_head(item):
        url, record = item
        with urlopen(Request('http://127.0.0.1:8767' + url, method='HEAD'), timeout=15) as response:
            assert response.status == 200 and int(response.headers['Content-Length']) == record['bytes']
        return {'url': url, 'passed': True}

    with ThreadPoolExecutor(max_workers=6) as pool:
        heads = list(pool.map(check_head, routes.items()))
    formal = load('scripts/断箭/启动正式资料库.py', 'readable_formal_verify')
    absolute = routes_absolute(routes)
    reveal_count = 0
    for card in catalog['models'] + catalog['audio']:
        urls = [card['url']] if card['type'] == 'audio' else [card['blend_url'], *[row['url'] for row in card['runtime_variants']]]
        for url in urls:
            path = formal.resolve_reveal(catalog, absolute, card['id'], url)
            assert path.is_file() and path.name != (routes[url]['sha256'] + path.suffix)
            reveal_count += 1
    from 群体资源交接 import load_delivery
    delivery_checks = []
    for resolution in [256, 512]:
        delivered, media = load_delivery(resolution=resolution)
        assert len(delivered['models']) == len(media) == 13
        delivery_checks.append({'resolution': resolution, 'models': 13, 'passed': True})
    evidence = load('scripts/资源库/核验统一库.py', 'readable_master_evidence').master_evidence(index, read(index / '模型登记.json'))
    result = {'task': receipt['task'], 'revision': pointer['revision'], 'pointer_sha256': sha(HOME / '当前版本.json'),
              'files': len(receipt['moved']), 'http_heads': len(heads), 'reveal_checks': reveal_count,
              'content_and_status_unchanged': True, 'historical_index_unchanged': True,
              'delivery': delivery_checks, 'master_evidence': evidence, 'browsers': [], 'started_at': time.time()}
    save(OUTPUT / '迁移核验.json', result)
    print(f'文件路径、{len(heads)}条HTTP入口、{reveal_count}项文件定位及双档交付核对通过', flush=True)
    # 临时服务只为验证固定启动器的真实构建路径，测试结束即关闭。
    game = load('scripts/资源优化/游戏版服务.py', 'readable_verify_ports')
    services = {}
    try:
        for name, file in [('range', 'scripts/预览载具靶场.py'), ('horde', 'scripts/预览丧尸群体测试.py')]:
            server = load(file, 'readable_verify_' + name).create_server(game.safe_port(0))
            services[name] = server
            threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as pw:
            for channel in ['chrome', 'msedge']:
                browser = pw.chromium.launch(channel=channel, headless=True)
                errors = []
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:8767/')
                page.wait_for_function('document.querySelectorAll("#model-list [data-model]").length>0')
                page.locator('#global-search').fill('M1A2')
                page.locator('#model-list [data-model="model:US_M1A2"]').click()
                assert page.locator('#spec-row [data-spec]').count() == 3
                for spec in range(3):
                    page.locator(f'#spec-row [data-spec="{spec}"]').click()
                    page.locator('#load-3d').click()
                    page.wait_for_function('document.querySelector("#viewer-state").textContent.startsWith("模型已就绪") && !document.querySelector("#canvas").hidden', timeout=60000)
                page.screenshot(path=str(OUTPUT / f'{channel}可读目录模型加载.png'))
                # 同一模型的三档已经由界面实际加载；抽验三类声音的真实浏览器解码。
                sounds = [next(row for row in catalog['audio'] if row['name'] == name) for name in ['US_AbramsM1A2_Engine', 'Death_Exp_Veh_AmmoDetonation', 'US_AbramsM1A2_Engine（当前无缝循环）']]
                decoded = page.evaluate('''async(items)=>{const context=new AudioContext();const rows=[];for(const item of items){const response=await fetch(item.url);if(!response.ok)throw Error('声音读取失败');const b=await context.decodeAudioData(await response.arrayBuffer());rows.push({id:item.id,duration:b.duration,channels:b.numberOfChannels});}await context.close();return rows;}''', sounds)
                assert all(row['duration'] > 0 for row in decoded)
                page.locator('#global-search').fill('')
                page.locator('#audio-tab').click()
                page.locator('[data-play]:visible').first.click()
                page.wait_for_function('document.querySelector("#player").currentTime>0', timeout=15000)
                page.close()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:36075/')
                page.wait_for_function('window.range?.ready', timeout=60000)
                page.close()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{services["range"].server_port}/')
                page.wait_for_function('window.range?.ready', timeout=60000)
                page.close()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{services["horde"].server_port}/')
                page.wait_for_function('window.horde?.ready', timeout=60000)
                snapshot = page.evaluate('horde.snapshot()')
                assert snapshot['config']['delivery']['library_revision'] == pointer['revision']
                assert len(snapshot['config']['models']) == 13
                page.close()
                browser.close()
                assert not errors, errors
                result['browsers'].append({'channel': channel, 'model_specs': 3, 'audio_decoded': decoded, 'audio_playback': True, 'range_independent': True, 'range_fixed_launcher': True, 'horde_roles': 13, 'errors': errors})
                save(OUTPUT / '迁移核验.json', result)
                print(channel + '三档模型、声音播放、两类靶场及十三款群体加载通过', flush=True)
    finally:
        for server in services.values():
            server.shutdown()
            server.server_close()
            if hasattr(server, 'observer'):
                server.observer.close()
    result.update(passed=True, finished_at=time.time(), performance_claim=False, listening_acceptance='待工程师听审')
    save(OUTPUT / '迁移核验.json', result)
    return result


if __name__ == '__main__':
    run()
