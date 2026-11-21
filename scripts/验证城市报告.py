"""检查离线报告的数据切换、窄屏、图片、链接与打印样式。"""
import json
from pathlib import Path
from urllib.parse import urlsplit,unquote
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/载具靶场/城市固定翼与性能验证.html'
OUT=ROOT/'local-data/原型/载具靶场/验证/城市修订1'


def main():
    result={'checks':[],'errors':[],'network':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},offline=True)
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.on('request',lambda r:result['network'].append(r.url) if r.url.startswith(('http:','https:')) else None)
        page.goto(REPORT.as_uri());page.wait_for_selector('#results tr')
        assert page.locator('#results tr').count()==9
        assert page.locator('#sample option').count()==9
        for i in range(9):
            page.locator('#sample').select_option(str(i))
            for metric in ['p95','cpuP95','voices','projectiles','shots']:
                page.locator('#metric').select_option(metric)
                points=page.locator('#chart polyline').get_attribute('points')
                assert points and 'NaN' not in points and 'undefined' not in points
        result['checks'].append('9样本与5指标均能离线切换，曲线为有限数值')
        assert page.evaluate('() => [...document.images].every(i=>i.complete&&i.naturalWidth>0)')
        for href in page.locator('a[href]').evaluate_all('(links)=>links.map(a=>a.getAttribute("href"))'):
            split=urlsplit(href)
            if split.scheme or not split.path:continue
            path=(REPORT.parent/unquote(split.path)).resolve();assert path.is_relative_to(ROOT) and path.exists(),href
        result['checks'].append('展示图片与全部本地文档链接存在，无外部资源请求')
        page.screenshot(path=str(OUT/'报告桌面.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('() => document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(OUT/'报告窄屏.png'),full_page=True)
        result['checks'].append('390像素窄屏无整页横向溢出')
        page.emulate_media(media='print');assert page.locator('nav').is_hidden()
        result['checks'].append('打印样式生效')
        assert not result['errors'] and not result['network'],result
        result['passed']=True;browser.close()
    (OUT/'报告页面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
