"""核验独立音效报告的离线交互、链接、窄屏与打印样式。"""
import json
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT/'reports/载具靶场/载具音效补全验证.html'
OUT = ROOT/'local-data/原型/载具靶场/验证/音效补全1'


def run():
    result = {'checks': [], 'errors': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.goto(REPORT.as_uri(), wait_until='load')
        assert page.locator('#sample option').count() == 4
        for sample in range(4):
            page.locator('#sample').select_option(str(sample))
            for metric in ['p95', 'voices', 'engines', 'audioPeak']:
                page.locator('#metric').select_option(metric)
                assert page.locator('#chart path').count() == 2
                assert 'NaN' not in page.locator('#chart').inner_html()
        result['checks'].append('四样本四指标离线曲线切换')
        for browser_name in ['chrome', 'msedge']:
            page.locator('#browser').select_option(browser_name)
            assert page.locator('#asset-bars .bar').count() == 3
        result['checks'].append('双浏览器三型真实声源指标切换')
        for href in page.locator('a').evaluate_all('(a)=>a.map(e=>e.getAttribute("href"))'):
            if not href.startswith(('http:', 'https:', '#')):
                assert (REPORT.parent/unquote(href)).is_file(), href
        assert 'E:\\' not in REPORT.read_text('utf-8') and 'C:\\Users' not in REPORT.read_text('utf-8')
        result['checks'].append('本地链接存在，报告不包含本机绝对路径')
        page.locator('#sample').select_option('0'); page.locator('#metric').select_option('p95')
        page.screenshot(path=str(OUT/'音效报告-桌面.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(OUT/'音效报告-窄屏.png'), full_page=True)
        result['checks'].append('390像素窄屏页面无横向溢出，宽表内部滚动')
        page.emulate_media(media='print')
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
        result['checks'].append('打印媒体切换为白底；未执行实体打印')
        assert not result['errors'], result['errors']
        result['passed'] = True
        browser.close()
    (OUT/'音效报告页面验证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': run()
