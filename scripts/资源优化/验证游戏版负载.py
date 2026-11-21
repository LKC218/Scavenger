"""两浏览器100单位有声交战，原运行版与游戏版串行同源码采样。"""
import importlib.util,json,pathlib,threading,time
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/游戏版第一版'
spec=importlib.util.spec_from_file_location('game_service',ROOT/'scripts/资源优化/游戏版服务.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def run():
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry()
 servers={'baseline':m.create_range(baseline=True),'256-lod1':m.create_range()}
 for s in servers.values():threading.Thread(target=s.serve_forever,daemon=True).start()
 result={'condition':'当前开发机，独立无窗口，两浏览器串行，100单位有声交战，640宽，预热10秒采样60秒；不代表普通电脑设备验收','samples':[]}
 with sync_playwright() as pw:
  for browser in ['chrome','msedge']:
   for name,s in servers.items():
    errors=[];b=pw.chromium.launch(channel=browser,headless=True);page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{s.server_port}/');page.wait_for_function('window.range?.ready',timeout=90000);page.locator('#sound').click();page.wait_for_function('range.mixer.active && range.audio.stats().cached===13',timeout=60000);page.locator('#unit-count').select_option('100');page.locator('#load-mode').select_option('combat');page.locator('#overview').click();page.wait_for_function('range.sim.shots>10 && range.sim.hits>0',timeout=30000);page.locator('#bench60').click();deadline=time.monotonic()+100
    while time.monotonic()<deadline:
     if page.evaluate('!!range.benchmark.last'):break
     page.wait_for_timeout(500)
    sample=page.evaluate('range.benchmark.last');assert sample and sample['duration']>=60;assert sample['timeline'][-1]['shots']>sample['timeline'][0]['shots'];assert sample['timeline'][-1]['hits']>sample['timeline'][0]['hits'];assert not errors,errors
    result['samples'].append({'browser':browser,'version':b.version,'variant':name,'sample':sample,'errors':errors});(OUT/'有声负载验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(browser,name,'P95',sample['p95'],flush=True);b.close()
 for s in servers.values():s.shutdown()
if __name__=='__main__':run()
