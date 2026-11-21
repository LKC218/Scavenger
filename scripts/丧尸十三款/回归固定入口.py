"""串行检查固定8767新卡片与8768现有资源；无窗口结果只作功能证据。"""
import argparse
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import save


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True);a=parser.parse_args();run=1
    while (a.batch/f'固定入口浏览器{run:03}').exists():run+=1
    out=a.batch/f'固定入口浏览器{run:03}';out.mkdir(exist_ok=False)
    result={'headless':True,'performance_acceptance':False,'browsers':[]}
    with sync_playwright() as pw:
        for channel in ['chrome','msedge']:
            browser=pw.chromium.launch(channel=channel,headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8767/');page.wait_for_function('document.querySelectorAll("#model-list button").length>0');page.locator('#global-search').fill('丧尸角色');page.wait_for_selector('[data-model="model:zombie-12"]');cards=[]
            for i in range(1,13):
                page.locator(f'[data-model="model:zombie-{i:02}"]').click();count=page.locator('#spec-row [data-spec]').count();assert count==2
                for profile in range(count):
                    page.locator(f'#spec-row [data-spec="{profile}"]').click();page.locator('#load-3d').click();page.wait_for_function('!document.querySelector("#canvas").hidden && document.querySelector("#viewer-state").textContent.includes("已就绪")',timeout=60000)
                cards.append({'id':f'model:zombie-{i:02}','profiles_loaded':count,'passed':True})
            page.screenshot(path=str(out/(channel+'新增卡片.png')));page.close()
            page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:8768/');page.wait_for_function('window.range?.ready',timeout=60000);page.locator('#sound').click();page.wait_for_function('range.audio.stats().cached===13',timeout=60000)
            range_state=page.evaluate('({models:range.manifest.models,audio:range.audio.stats(),volume:range.audio.masterVolume,boostDb:range.mixer.boostDb})')
            assert len(range_state['models'])==3 and range_state['audio']['cached']==13 and range_state['volume']==1 and range_state['boostDb']==6
            page.screenshot(path=str(out/(channel+'原靶场.png')));assert not errors,errors
            result['browsers'].append({'channel':channel,'version':browser.version,'new_cards':cards,'range':range_state,'errors':errors,'passed':True});browser.close();print(channel,'固定入口通过',flush=True)
    result['passed']=True;save(a.batch/'固定入口浏览器核验.json',result)


if __name__=='__main__':main()
