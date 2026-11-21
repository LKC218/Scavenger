"""主入口游戏接入与离线报告最终核验，不删除或重启旧服务。"""
import importlib.util,json,pathlib,re,threading
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/游戏版第一版'
spec=importlib.util.spec_from_file_location('game_final_services',ROOT/'scripts/资源优化/游戏版服务.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def run():
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry('verify')
 module=m.load('scripts/预览载具靶场.py','game_final_main');server=module.create_server(m.safe_port(0));manifest=json.loads(server.manifest_bytes)
 for row in manifest['models'].values():assert '/游戏版第一版/' in server.routes[row['url']]['path'].replace('\\','/'),row
 threading.Thread(target=server.serve_forever,daemon=True).start();game=m.create_range();threading.Thread(target=game.serve_forever,daemon=True).start();locked=json.loads((OUT/'验证源码锁.json').read_text('utf8'));assert game.game_source_identity=={url:r['sha256'] for url,r in locked.items()};report=ROOT/'reports/资源优化/游戏版整库优化验证.html';assert not re.search(r'(?<![A-Za-z])[A-Za-z]:[\\/]',report.read_text('utf8'));result={'source_identity':game.game_source_identity,'main_models':{k:{'bytes':r['bytes'],'sha256':r['runtime_sha256']} for k,r in manifest['models'].items()},'browsers':[]}
 with sync_playwright() as pw:
  for browser in ['chrome','msedge']:
   errors=[];b=pw.chromium.launch(channel=browser,headless=True);page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('window.range?.ready',timeout=90000)
   for key,kind in [('1','tank'),('2','heli'),('4','plane')]:
    page.keyboard.press(key);page.keyboard.press('c');page.wait_for_function('(kind)=>range.commander.ids().length>0 && range.commander.ids().every(id=>range.sim.units.find(u=>u.id===id).kind===kind)',arg=kind)
   page.locator('#sound').click();page.wait_for_function('range.audio.stats().cached===13',timeout=60000);assert page.evaluate('range.audio.masterVolume===1 && range.mixer.boostDb===6');page.close()
   page=b.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{game.server_port}/');page.wait_for_function('window.range?.ready',timeout=90000);assert page.evaluate('range.manifest.game_variant.resolution===256');page.close()
   context=b.new_context(offline=True,viewport={'width':1280,'height':900});page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(report.as_uri());assert page.locator('#assets tbody tr').count()==227;page.locator('#search').fill('M1A2');assert page.locator('#assets tbody tr:visible').count()>0;page.locator('#search').fill('');page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.emulate_media(media='print');assert page.evaluate('getComputedStyle(document.body).backgroundColor')=='rgb(255, 255, 255)';page.screenshot(path=str(OUT/f'{browser}游戏版离线报告.png'),full_page=True);context.close();b.close();assert not errors,errors;result['browsers'].append({'browser':browser,'ready':True,'frozen_preview':True,'selection':True,'audio':13,'gain_preserved':True,'offline_report':True,'rows':227,'narrow':True,'print':True,'errors':errors})
 server.shutdown();game.shutdown();(OUT/'交付核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print('主入口及离线报告双浏览器通过')
if __name__=='__main__':run()
