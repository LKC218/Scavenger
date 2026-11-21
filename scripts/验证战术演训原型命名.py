"""串行核验统一命名、根快捷入口复用及实际地图交互；不升级为新性能验收。"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
import 启动战术演训原型 as launcher
from 战术演训.日常留存 import compact, output_directory, record_failure, write_summary
from 战术演训.只读核对演训入口 import check_only

ROOT=Path(__file__).resolve().parents[1]


def fingerprint(path):
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,'sha256':digest}


def run(output, routine=False):
    output=output_directory(output,launcher.HOME,routine)
    try:
        run_checks(output,routine)
    except Exception as error:
        if routine:
            if not any(output.glob("*/失败日志.txt")):
                record_failure(output,{},error=error)
            target=output/'验证结果.json'
            if not target.exists():
                write_summary(target,{"passed":False,"failure":repr(error)})
        raise


def run_checks(output,routine):
    config,_,_=launcher.configuration()
    identity=check_only() if routine else launcher.verify_service(launcher.status(),config)
    pointer=fingerprint(ROOT/'local-data/资源库/当前版本.json')
    result={'passed':False,'identity':identity,'source_program':fingerprint(Path(__file__)),'pointer_before':pointer,
        'scope':'真实双浏览器名称、布局、地图交互及入口复用；原性能证据仍绑定r16，命名副本不冒称新增长测',
        'launchers':[],'browsers':[],'human_acceptance':'待人工验收','started_epoch':time.time()}
    def save():
        target=output/'验证结果.json'
        if routine:
            summary={**result,'browsers':[compact(row) for row in result['browsers']],
                     'launchers':[{k: row[k] for k in ('file','passed','same_pid')} for row in result['launchers']]}
            write_summary(target,summary)
        else:
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        for name in ['打开战术演训原型.cmd','打开载具靶场.cmd']:
            command=([sys.executable,'-X','utf8','-B',str(ROOT/'scripts/战术演训/只读核对演训入口.py'),'--check-only','--entry',name]
                     if routine else [os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/c',str(ROOT/name),'--start'])
            completed=subprocess.run(command,
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=100,check=True)
            current=launcher.verify_service(launcher.status(),config);assert current['pid']==identity['pid'],'重复启动了服务'
            result['launchers'].append({'file':name,'passed':True,'same_pid':True,'output':completed.stdout})
        foreign=copy.deepcopy(identity);foreign['app']='unrelated-fixture'
        try:launcher.verify_service(foreign,config)
        except RuntimeError:result['foreign_identity_rejected']=True
        else:raise AssertionError('未拒绝外部服务身份')
        with sync_playwright() as pw:
            for channel in ['chrome','msedge']:
                folder=output/channel
                if not routine:folder.mkdir()
                row={'browser':channel,'errors':[],'passed':False}
                browser=pw.chromium.launch(channel=channel,headless=routine)
                try:
                    page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
                    page.on('pageerror',lambda error:row['errors'].append(str(error)))
                    page.on('console',lambda message:row['errors'].append(message.text) if message.type=='error' else None)
                    page.on('response',lambda response:row['errors'].append(f'{response.status} {response.url}') if response.status>=400 else None)
                    page.goto(launcher.URL)
                    if not routine:page.bring_to_front()
                    page.wait_for_function('window.range?.ready&&!range.externalMap.stats.pending&&!range.externalMap.stats.transcodes',timeout=120000)
                    assert page.title()=='战术演训原型 · Airbase全图'
                    assert page.locator('.brand').get_attribute('aria-label')=='战术演训原型首页'
                    assert page.locator('.brand small').inner_text()==config['subtitle']
                    assert page.locator('.brand>span').last.inner_text().startswith(config['name'])
                    initial=page.evaluate('({count:range.sim.units.length,volume:range.audio.masterVolume,boost:range.mixer.boostDb,sound:range.mixer.active,perf:range.perf(),time:range.sim.time})')
                    assert initial['count']==30 and initial['volume']==.25 and initial['boost']==6 and not initial['sound']
                    assert initial['perf']['internal']==[640,364] and not initial['perf']['mapStats']['lastError']
                    if not routine:page.screenshot(path=str(folder/'统一名称与机场.png'))
                    row['initial']=initial
                    page.locator('#pause').click();page.wait_for_function('range.sim.paused')
                    page.locator('#roster .unit-card').first.click()
                    assert page.evaluate('range.commander.selected.size')==1
                    page.keyboard.press('3');assert page.evaluate('range.commander.selected.size')==30
                    page.locator('#pause').click();page.wait_for_function('!range.sim.paused')
                    page.wait_for_function('(t)=>range.sim.time>t+.2',arg=initial['time'])
                    old_x=page.evaluate('range.view.center.x');page.keyboard.down('d');page.wait_for_timeout(250);page.keyboard.up('d')
                    assert page.evaluate('range.view.center.x')>old_x
                    field=page.locator('#battlefield').bounding_box();page.mouse.move(field['x']+field['width']/2,field['y']+field['height']/2)
                    old_span=page.evaluate('range.view.span');page.mouse.wheel(0,120)
                    page.wait_for_function('(s)=>range.view.span>s',arg=old_span)
                    expanded=page.evaluate('range.view.span');page.mouse.wheel(0,-120)
                    page.wait_for_function('(s)=>range.view.span<s',arg=expanded)
                    page.keyboard.press('f');page.wait_for_function('!range.view.centerTarget&&!range.externalMap.stats.pending',timeout=90000)
                    page.locator('#unit-count').select_option('10');page.locator('#load-mode').select_option('combat')
                    page.wait_for_function('range.sim.shots>0',timeout=45000)
                    row['interaction']=page.evaluate('({shots:range.sim.shots,units:range.sim.units.length,perf:range.perf()})')
                    assert row['interaction']['units']==10 and not row['interaction']['perf']['mapStats']['lastError']
                    page.set_viewport_size({'width':1024,'height':768})
                    page.wait_for_timeout(350)
                    row['header_layout']=page.evaluate('(()=>{const a=document.querySelector(".brand").getBoundingClientRect(),b=document.querySelector(".header-center").getBoundingClientRect(),c=document.querySelector(".header-actions").getBoundingClientRect();return{brandRight:a.right,centerLeft:b.left,actionsLeft:c.left,viewport:innerWidth};})()')
                    assert row['header_layout']['brandRight']<=row['header_layout']['centerLeft'] and row['header_layout']['brandRight']<=row['header_layout']['actionsLeft']
                    if not routine:page.screenshot(path=str(folder/'较窄桌面布局.png'))
                    assert not row['errors'],row['errors'];row.update(passed=True,version=browser.version)
                    print(channel+'：统一名称、30单位、暂停选择、镜头、10单位开火与桌面布局通过',flush=True)
                except BaseException as error:
                    row['failure']=repr(error)
                    if routine:
                        record_failure(folder,row,error=error,page=page)
                    elif not page.is_closed():
                        row['failure_document']=page.evaluate('({url:location.href,title:document.title,fatal:document.querySelector("#fatal")?.textContent})')
                        page.screenshot(path=str(folder/'失败.png'))
                    raise
                finally:browser.close();result['browsers'].append(row);save()
        assert fingerprint(ROOT/'local-data/资源库/当前版本.json')==pointer,'正式资源库指针变化'
        if routine:
            current=check_only()
            if any(current.get(key)!=identity.get(key) for key in ('pid','started_at','revision','map_sha256')):
                raise RuntimeError('8784运行身份在日常验证期间变化')
        result.update(passed=True,finished_epoch=time.time());save()
    finally:save()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--routine',action='store_true',help='无窗口日常回归只留摘要，失败留截图和日志')
    args=parser.parse_args()
    run(args.output,args.routine)
