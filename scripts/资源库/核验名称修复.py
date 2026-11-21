"""核验角色语义名称、512细节档、旧名搜索和改名后的实际加载。"""

import importlib.util
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright
from 统一资源 import ROOT, HOME, current, read, save, sha, routes_absolute
from 修复资产名称 import BASE, RENAMES

sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/断箭'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run():
    output = BASE / '执行'
    receipt = read(output / '迁移回执.json')
    pointer, catalog, routes, index = current()
    assert sha(HOME / '当前版本.json') == receipt['new_pointer_sha256']
    previous = read(output / '旧指针.json')
    old_index = HOME / previous['directory']
    for name, digest in previous['metadata'].items():
        assert sha(old_index / name) == digest
    old_catalog = read(old_index / '资源索引.json')
    assert old_catalog['audio'] == catalog['audio'] and old_catalog['summary'] == catalog['summary']
    changed_ids = {'model:zombie-source', 'model:zombie-runtime'}
    for old, new in zip(old_catalog['models'], catalog['models']):
        assert old['id'] == new['id'] and old['quality_status'] == new['quality_status'] and old['runtime_variants'] == new['runtime_variants']
        if old['id'] not in changed_ids:
            assert old == new
        else:
            assert new['name'] == RENAMES[old['name']] and old['name'] in new['aliases']
    old_routes = read(old_index / '资源路由.json')
    assert set(routes) == set(old_routes)
    for url, record in routes.items():
        assert record['sha256'] == old_routes[url]['sha256'] and record['bytes'] == old_routes[url]['bytes']
        assert (ROOT / record['path']).is_file()
    moved = {row['new_path']: row for row in receipt['moved']}
    formal = load('scripts/断箭/启动正式资料库.py', 'name_fix_formal')
    absolute = routes_absolute(routes)
    http_checks = 0
    for url, record in routes.items():
        if record['path'] not in moved:
            continue
        with urlopen(Request('http://127.0.0.1:8767' + url, method='HEAD'), timeout=10) as response:
            assert response.status == 200 and int(response.headers['Content-Length']) == record['bytes']
        http_checks += 1
    for row in receipt['moved']:
        assert not (ROOT / row['old_path']).exists()
        assert sha(ROOT / row['new_path']) == row['sha256']
    for row in read(index / '主工程.json').values():
        assert (ROOT / row['path']).is_file()
    for record in [*routes.values(), *read(index / '主工程.json').values()]:
        assert not any(old in record['path'] for old in ['512回退', *RENAMES])
    for card in catalog['models']:
        for variant in card['runtime_variants']:
            formal.resolve_reveal(catalog, absolute, card['id'], variant['url'])
    from 群体资源交接 import load_delivery
    for resolution in [256, 512]:
        delivery, media = load_delivery(resolution=resolution)
        assert len(delivery['models']) == len(media) == 13
    result = {'task': receipt['task'], 'revision': pointer['revision'], 'files_renamed': len(moved), 'changed_http_checks': http_checks, 'all_media_urls_unchanged': True, 'asset_status_unchanged': True, 'old_index_unchanged': True, 'delivery_checks': [256, 512], 'browsers': [], 'started_at': time.time()}
    save(output / '名称修复核验.json', result)
    game = load('scripts/资源优化/游戏版服务.py', 'name_fix_ports')
    horde = load('scripts/预览丧尸群体测试.py', 'name_fix_horde').create_server(game.safe_port(0))
    threading.Thread(target=horde.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            for channel in ['chrome', 'msedge']:
                browser = pw.chromium.launch(channel=channel, headless=True)
                errors = []
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:8767/')
                page.wait_for_function('document.querySelectorAll("#model-list [data-model]").length>0')
                for old_name, identity in [('当前丧尸动态角色', 'model:zombie-runtime'), ('原始丧尸角色包', 'model:zombie-source')]:
                    page.locator('#global-search').fill(old_name)
                    item = page.locator(f'#model-list [data-model="{identity}"]')
                    assert item.count() == 1 and RENAMES[old_name] in item.inner_text()
                    item.click()
                    assert page.locator('#spec-row').inner_text().find('细节档') >= 0
                    if identity == 'model:zombie-source':
                        assert '非独立运行角色' in page.locator('#model-subtitle').inner_text()
                page.locator('#global-search').fill('丧尸角色00')
                page.locator('#model-list [data-model="model:zombie-runtime"]').click()
                page.locator('#spec-row [data-spec="1"]').click()
                assert '512 · 细节档' in page.locator('#spec-row [data-spec="1"]').inner_text()
                assert '贴图最大边长512像素' in page.locator('#spec-row [data-spec="1"]').get_attribute('title')
                page.locator('#load-3d').click()
                page.wait_for_function('document.querySelector("#viewer-state").textContent.startsWith("模型已就绪") && !document.querySelector("#canvas").hidden', timeout=60000)
                page.screenshot(path=str(output / f'{channel}角色00细节档.png'))
                page.locator('#global-search').fill('M1A2')
                page.locator('#model-list [data-model="model:US_M1A2"]').click()
                page.locator('#spec-row [data-spec="2"]').click()
                page.locator('#load-3d').click()
                page.wait_for_function('document.querySelector("#viewer-state").textContent.startsWith("模型已就绪") && !document.querySelector("#canvas").hidden', timeout=60000)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.close()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{horde.server_port}/')
                page.wait_for_function('window.horde?.ready', timeout=60000)
                assert page.evaluate('horde.snapshot().config.delivery.library_revision') == pointer['revision']
                page.close()
                browser.close()
                assert not errors, errors
                result['browsers'].append({'channel': channel, 'old_name_search': True, 'source_label': True, 'actor00_512_loaded': True, 'vehicle_512_loaded': True, 'narrow': True, 'horde_loaded': True, 'errors': errors})
                save(output / '名称修复核验.json', result)
                print(channel + '名称、旧名搜索、512模型及群体加载通过', flush=True)
    finally:
        horde.shutdown()
        horde.server_close()
        horde.observer.close()
    result.update(passed=True, finished_at=time.time())
    save(output / '名称修复核验.json', result)


if __name__ == '__main__':
    run()
