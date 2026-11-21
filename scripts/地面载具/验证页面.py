"""独立无窗口浏览器验证共通检验场，不控制工程师的桌面或既有窗口。"""
import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'local-data/原型/地空载具实装/V001-01a1ac6a/验证'


def run(channel='chrome', port=8809, all_models=False):
    OUT.mkdir(parents=True, exist_ok=True)
    record = {'browser': channel, 'headless': True, 'checks': [], 'errors': [], 'passed': False}
    with urlopen(f'http://127.0.0.1:{port}/api/status') as response:
        record['identity'] = json.load(response)
    assert record['identity']['app'] == 'scavenger-ground-workbench'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={'width': 1560, 'height': 980})
        record['version'] = browser.version
        page.on('pageerror', lambda e: record['errors'].append(str(e)))
        page.on('console', lambda m: record['errors'].append(m.text) if m.type == 'error' else None)
        page.on('response', lambda r: record['errors'].append(f'{r.status} {r.url}') if r.status >= 400 else None)

        def check(label):
            record['checks'].append(label)
            print(channel + '：' + label, flush=True)

        def click_world(x, z, button='right'):
            point = page.evaluate('''async ([x,z])=>{const {Vector3}=await import('three');const a=__ground;const p=new Vector3(x,0,z).project(a.camera),r=a.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};}''', [x, z])
            page.mouse.click(point['x'], point['y'], button=button)

        def select_model(name):
            page.locator('#search').fill(name)
            page.locator('.model-row').filter(has_text=name).first.click()

        try:
            page.goto(f'http://127.0.0.1:{port}/', wait_until='networkidle')
            page.wait_for_function('window.__ground?.sim.units.length===1 && __ground.visuals.size===1', timeout=60000)
            assert page.evaluate('__ground.sim.catalog.length') == 77
            assert page.evaluate('__ground.sim.catalog.filter(r=>r.deployable).length') == 75
            page.locator('#filter').select_option('partial')
            assert page.locator('.model-row').count() == 2
            page.locator('.model-row').first.click()
            assert page.locator('#deploy').is_disabled()
            page.locator('#filter').select_option('all')
            check('77条完整登记、75允许基础部署、两条参考待修禁止部署')

            page.locator('#units button').first.click()
            page.locator('.test-tools summary').click()
            page.locator('#damage').click()
            assert page.evaluate('__ground.sim.units[0].hp') == 75
            click_world(-15, 15)
            page.wait_for_function('!__ground.sim.units[0].moving', timeout=15000)
            assert page.evaluate('Math.abs(__ground.sim.units[0].x+15)<1')
            page.locator('#target').select_option('target-1')
            page.locator('#attack').click()
            page.wait_for_function('__ground.sim.shots>=1', timeout=5000)
            assert page.evaluate('__ground.sim.units[0].ammo') < 8
            click_world(-25, 20)
            page.wait_for_function('!__ground.sim.units[0].moving', timeout=10000)
            check('真实右键移动、主炮扣弹与检验受损')

            select_model('M548A3')
            page.locator('#deploy').click()
            page.wait_for_function('__ground.sim.units.length===2 && __ground.visuals.size===2', timeout=60000)
            page.locator('#reload').click()
            page.wait_for_function('__ground.sim.units[1].cargo>=40', timeout=15000)
            click_world(-32, 20)
            page.wait_for_function('!__ground.sim.units[1].moving', timeout=10000)
            page.locator('#supply-target').select_option('ground-1')
            page.locator('#supply').click()
            page.wait_for_function('__ground.sim.units[0].hp===100 && __ground.sim.units[0].ammo===8', timeout=15000)
            assert page.evaluate('__ground.sim.units[1].cargo') < 80
            check('M548回站有限装载、前线运输、近距补弹与维修')

            old = page.evaluate('({stock:__ground.sim.stock,ammo:__ground.sim.units[0].ammo,cargo:__ground.sim.units[1].cargo,points:__ground.sim.points})')
            page.locator('#buy-stock').click()
            new = page.evaluate('({stock:__ground.sim.stock,ammo:__ground.sim.units[0].ammo,cargo:__ground.sim.units[1].cargo,points:__ground.sim.points})')
            assert abs(new['stock'] - old['stock'] - 20) < .001
            assert new['points'] == old['points'] - 20 and new['ammo'] == old['ammo'] and new['cargo'] == old['cargo']
            check('购买物资只进入后方库存')

            page.screenshot(path=str(OUT / (channel + '-共通闭环.png')), full_page=True)
            page.locator('#units button').first.click()
            for _ in range(4):
                page.locator('#damage').click()
            assert not page.evaluate('__ground.sim.units[0].alive')
            assert page.locator('#attack').is_disabled()
            page.locator('#reset').click()
            assert page.evaluate('__ground.sim.units.length') == 0
            check('本局永久损失、摧毁后禁令及重开复位')

            if all_models:
                rows = page.evaluate('__ground.sim.catalog.filter(r=>r.deployable).map(r=>({id:r.id,name:r.name}))')
                started = time.monotonic()
                for i, row in enumerate(rows):
                    page.locator('#reset').click()
                    page.locator('#search').fill(row['id'].replace('model:', ''))
                    page.locator('.model-row').first.click()
                    page.locator('#deploy').click()
                    page.wait_for_function('window.__ground.sim.units.length===1 && __ground.visuals.size===1', timeout=60000)
                    assert page.evaluate('__ground.sim.units[0].modelId') == row['id']
                    if i % 10 == 0:
                        print(channel + f'：模型加载部署 {i+1}/{len(rows)}', flush=True)
                record['models_loaded'] = len(rows)
                record['model_seconds'] = round(time.monotonic() - started, 2)
                check('75条允许部署记录逐项真实加载并部署；不等同逐车外观或专用玩法验收')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(OUT / (channel + '-窄屏.png')), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not record['errors'], record['errors']
            check('窄屏无横向溢出与无页面资源错误')
            record['passed'] = True
        finally:
            (OUT / (channel + '-页面验证.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), 'utf-8')
            browser.close()
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], default='chrome')
    parser.add_argument('--port', type=int, default=8809)
    parser.add_argument('--all-models', action='store_true')
    args = parser.parse_args()
    run(args.browser, args.port, args.all_models)
