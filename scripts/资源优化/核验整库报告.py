"""断网打开单文件报告，核对全量计数、筛选、窄屏与打印。"""
import json,pathlib
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版';REPORT=ROOT/'reports/资源优化/整库资源优化与归档报告.html'
if __name__=='__main__':
 result=[]
 with sync_playwright() as p:
  for channel in ['chrome','msedge']:
   b=p.chromium.launch(channel=channel,headless=True);context=b.new_context(viewport={'width':1440,'height':1000},offline=True);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto(REPORT.as_uri());assert page.locator('#rows tr').count()==729
   page.locator('#type').select_option('model');assert page.locator('#rows tr').count()==227;page.locator('#query').fill('US_M1A2');assert page.locator('#rows tr').count()>0;page.locator('#query').fill('');page.locator('#type').select_option('audio');assert page.locator('#rows tr').count()==502
   page.locator('#type').select_option('all');page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(OUT/f'{channel}报告桌面.png'));page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth');page.screenshot(path=str(OUT/f'{channel}报告窄屏.png'));page.emulate_media(media='print');assert page.evaluate('getComputedStyle(document.body).backgroundColor')=='rgb(255, 255, 255)';assert not errors
   result.append({'browser':channel,'version':b.version,'offline':True,'rows':729,'models':227,'audio':502,'mobile_width_layout_only':390,'print_white':True,'errors':errors});b.close()
 (OUT/'报告页面核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=False))
