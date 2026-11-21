"""当前固定端口的冷浏览器加载、机械动作及原型交战抽查。"""
import time
from playwright.sync_api import sync_playwright
from 统一资源 import HOME,save
def run():
 result=[]
 with sync_playwright() as pw:
  for channel in ['chrome','msedge']:
   browser=pw.chromium.launch(channel=channel,headless=True);page=browser.new_page(viewport={'width':1600,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));started=time.perf_counter();page.goto('http://127.0.0.1:8768/?v=unified-1');page.wait_for_function('window.range?.ready',timeout=60000);ready=(time.perf_counter()-started)*1000
   entries=page.evaluate('performance.getEntriesByType("resource").filter(r=>r.name.includes(".glb")).map(r=>({url:new URL(r.name).pathname,bytes:r.encodedBodySize,transfer:r.transferSize,durationMs:r.duration}))');assert len(entries)==3 and sum(r['bytes'] for r in entries)==3585884
   assert page.evaluate('Object.values(range.manifest.models).every(r=>r.asset_library==="统一资源库")')
   page.locator('#sound').click();page.wait_for_function('range.audio.stats().cached===13',timeout=30000);page.locator('#unit-count').select_option('100');page.locator('#load-mode').select_option('combat');page.wait_for_function('range.sim.shots>20 && range.sim.hits>0',timeout=40000)
   page.locator('#roster .unit-card').nth(15).click();page.locator('#focus').click();page.wait_for_function('!range.view.centerTarget',timeout=15000)
   # 观察真实时间推进下的机械状态和实时阴影；本次不重做长程性能验收。
   observed=page.evaluate('({perf:range.perf(),sound:range.audio.stats(),boost:range.mixer.boostDb,volume:range.audio.masterVolume,models:range.manifest.models})');page.screenshot(path=str(HOME/f'验收/{channel}固定8768.png'));assert not errors;assert observed['boost']==6 and observed['volume']==1
   result.append({'browser':channel,'local_loopback':True,'cold_browser_context':True,'ready_ms':ready,'model_transfers':entries,'observed':observed,'errors':errors});browser.close()
 save(HOME/'验收/固定入口核验.json',result);print([(r['browser'],round(r['ready_ms'])) for r in result],flush=True);return result
if __name__=='__main__':run()
