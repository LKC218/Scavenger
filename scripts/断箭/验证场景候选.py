"""串行使用原生可见Chrome和Edge核对候选查询、独立原型及引用场景；不作性能验收。"""

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen
from urllib.parse import urlsplit,unquote

from playwright.sync_api import sync_playwright

from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint
from 启动正式资料库 import create_server
sys.path.insert(0, str(ROOT / 'scripts/资源库'))


def run(directory, manifest_path, revision, supplied_url=None):
    output = directory / '候选浏览器验证' / revision
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(manifest_path.read_text('utf-8'))
    pointer = ROOT / 'local-data/资源库/当前版本.json'
    before = fingerprint(pointer)
    server = thread = None
    delay={'enabled':False,'path':None,'entered':threading.Event(),'release':threading.Event()}
    if supplied_url:
        if urlsplit(supplied_url).hostname != '127.0.0.1':
            raise ValueError('候选核验只允许本机服务')
        url = supplied_url.rstrip('/')+'/'
        from 发布场景 import sha_manifest
        with urlopen(url+'api/status') as response:
            status = json.load(response)
        if status['app'] != 'scavenger-library-candidate' or status['candidate_sha256'] != sha_manifest(manifest):
            raise ValueError('候选服务身份不对应清单')
    else:
        server = create_server(0, manifest)
        original_handler=server.RequestHandlerClass
        class DelayedHandler(original_handler):
            def serve(self,head):
                if delay['enabled'] and unquote(urlsplit(self.path).path)==delay['path']:
                    delay['entered'].set();delay['release'].wait(timeout=15)
                try:super().serve(head)
                except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):pass
        server.RequestHandlerClass=DelayedHandler
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}/'
    full = manifest['scene'].get('coverage', {}).get('cells') == 5184
    journal = Journal(directory)
    results = []
    try:
        with sync_playwright() as pw:
            for channel in ['chrome', 'msedge']:
                journal.update(phase='A3全图候选验收' if full else 'A1双层资产样本', operation='候选库原生可见浏览器核对', browser=channel)
                record = {'browser': channel, 'window': True, 'viewport': [1280, 720], 'errors': [], 'checks': [], 'requests': [], 'request_failures': []}
                evidence = output / channel
                evidence.mkdir()
                browser = pw.chromium.launch(channel=channel, headless=False)
                try:
                    record['version'] = browser.version
                    page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
                    page.add_init_script('''(()=>{let next=0;const ids=new WeakMap();window.glLifecycle=[];const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(kind,...args){const context=original.call(this,kind,...args);if(context&&['webgl','webgl2','experimental-webgl'].includes(kind)&&!ids.has(this)){const row={id:++next,lost:false};ids.set(this,row.id);glLifecycle.push(row);this.addEventListener('webglcontextlost',()=>row.lost=true);}return context;};})();''')
                    page.on('pageerror', lambda error: record['errors'].append(str(error)))
                    page.on('console', lambda message: record['errors'].append({'text': message.text, 'location': message.location}) if message.type == 'error' else None)
                    page.on('requestfailed', lambda request: record['request_failures'].append({'url': request.url, 'failure': request.failure}))
                    page.on('response', lambda response: record['errors'].append(f'{response.status} {response.url}') if response.status >= 400 else None)
                    page.on('request', lambda request: record['requests'].append(request.url))
                    started = time.time()
                    page.goto(url)
                    page.bring_to_front()
                    page.wait_for_selector('[data-category="scene"]', timeout=30000)
                    record['index_seconds'] = time.time() - started
                    assert not any('.glb' in value for value in record['requests']), '列表自动下载了三维载荷'
                    catalog = page.evaluate('fetch("/api/catalog").then(r=>r.json())')
                    assert catalog['status'] == 'candidate' and len(catalog['scenes']) == 1
                    related = page.evaluate('(id)=>fetch("/api/search?related_to="+encodeURIComponent(id)+"&limit=200").then(r=>r.json())', manifest['scene']['id'])
                    assert related['total'] == 133
                    for category in ['building', 'decor', 'tree', 'grass', 'scene']:
                        page.locator(f'[data-category="{category}"]').click()
                        assert page.locator('#model-list [data-model]').count() > 0
                    record['checks'].extend(['列表不预载GLB', '五类筛选', '场景关联133原型'])
                    # 围栏与树木分别覆盖单面裁切和双面叶片。
                    for asset_id, name in [('model:BA_SCENE_c3f67f84e961b9bed18c_v001', '围栏'),
                                           ('model:BA_SCENE_fe9c48cbccc2e6c0aa51_v001', '树木')]:
                        if name == '围栏':
                            page.goto(url + '?check=' + name + '#view=vehicles&model=' + asset_id)
                        else:
                            page.locator('[data-category="tree"]').click()
                            page.locator(f'[data-model="{asset_id}"]').click()
                        page.wait_for_function('(id)=>document.querySelector("#model-list [aria-selected=true]")?.dataset.model===id', arg=asset_id)
                        page.locator('#load-3d').click()
                        page.wait_for_selector('#canvas:not([hidden]) canvas', timeout=90000)
                        page.wait_for_function('document.querySelector("#view-tag-text").textContent.includes("已就绪")')
                        page.screenshot(path=str(evidence / f'{name}.png'))
                        record['checks'].append(name + '实际GLB加载')
                    record['checks'].append('同一预览器切换资产及进度回调更新')
                    page.goto(url + '?check=scene#view=vehicles&model=' + manifest['scene']['id'])
                    page.wait_for_function('(id)=>document.querySelector("#model-name").textContent.includes(id)', arg=manifest['scene']['name'].split('候选')[0])
                    page.locator('#load-3d').click()
                    page.wait_for_selector('#canvas:not([hidden]) canvas', timeout=180000)
                    page.wait_for_function('document.querySelector("#view-tag-text").textContent.includes("已就绪")')
                    record['scene_resources'] = page.evaluate('performance.getEntriesByType("resource").filter(r=>r.name.includes("candidate-media")).map(r=>({url:r.name,encoded:r.encodedBodySize,decoded:r.decodedBodySize}))')
                    if full:
                        assert 0 < sum('.glb' in row['url'] for row in record['scene_resources']) < 5313
                        page.locator('[data-scene-action="overview"]').click()
                        page.wait_for_function('document.querySelector(".scene-tier")?.textContent==="远景"&&document.querySelector("#viewer-state").textContent.includes("场景已就绪")', timeout=180000)
                        page.screenshot(path=str(evidence / '全图总览.png'))
                        page.locator('[data-scene-action="home"]').click()
                        page.wait_for_function('document.querySelector(".scene-tier")?.textContent==="近景"&&document.querySelector("#viewer-state").textContent.includes("场景已就绪")', timeout=90000)
                        assert page.locator('#canvas canvas').count() == 1
                        record['checks'].extend(['全图按区域加载而非全集预载', '真实总览及机场切换', '单一活动渲染画布'])
                        def select_model(asset_id,category):
                            page.locator(f'[data-category="{category}"]').click()
                            while not page.locator(f'[data-model="{asset_id}"]').count():
                                assert page.locator('#more-models').is_visible(),'分类中没有目标资产：'+asset_id
                                page.locator('#more-models').click()
                            page.locator(f'[data-model="{asset_id}"]').click()
                            page.wait_for_function('(id)=>document.querySelector("#model-list [aria-selected=true]")?.dataset.model===id',arg=asset_id)

                        def load_ready():
                            page.locator('#load-3d').click()
                            page.wait_for_function('document.querySelector("#view-tag-text").textContent.includes("已就绪")',timeout=180000)
                            page.wait_for_function('glLifecycle.filter(row=>!row.lost).length===1',timeout=10000)
                            assert page.locator('#canvas canvas').count()==1

                        # 暂停真实场景清单请求，在其返回前切回单体，检查取消不污染新选择。
                        select_model('model:BA_SCENE_fe9c48cbccc2e6c0aa51_v001','tree');load_ready()
                        if server:
                            delay.update(enabled=True,path=urlsplit(catalog['scenes'][0]['scene_url']).path)
                            delay['entered'].clear();delay['release'].clear()
                            select_model(manifest['scene']['id'],'scene');page.locator('#load-3d').click()
                            deadline=time.time()+10
                            while not delay['entered'].is_set() and time.time()<deadline:page.wait_for_timeout(50)
                            assert delay['entered'].is_set(),'没有捕获场景在途请求'
                            select_model('model:BA_SCENE_fe9c48cbccc2e6c0aa51_v001','tree');load_ready()
                            delay['enabled']=False;delay['release'].set();page.wait_for_timeout(150)
                            assert page.locator('.scene-tools').count()==0
                            assert any(unquote(urlsplit(row['url']).path)==delay['path'] and row['failure']=='net::ERR_ABORTED' for row in record['request_failures'])
                            record['checks'].append('真实HTTP挂起场景取消后单体就绪且无旧界面回写')
                        else:
                            record['cancel_scope']='外部服务不注入挂起；取消检查使用独立候选服务证据'

                        for cycle in range(1,6):
                            select_model(manifest['scene']['id'],'scene');load_ready()
                            assert page.locator('.scene-tools').count()==1
                            select_model('model:BA_SCENE_fe9c48cbccc2e6c0aa51_v001','tree');load_ready()
                            assert page.locator('.scene-tools').count()==0
                            print(channel+'：场景与单体第'+str(cycle)+'次切换及上下文释放通过',flush=True)
                        for item,label in [(next(row for row in catalog['models'] if row['id']=='model:US_M1A2'),'既有M1A2'),
                                           (next(row for row in catalog['models'] if row['id']=='model:zombie-01'),'既有骨骼角色')]:
                            select_model(item['id'],item['category']);load_ready()
                            page.screenshot(path=str(evidence/(label+'.png')))
                            record['checks'].append(label+'实际加载回归')
                        record['gl_context_lifecycle']=page.evaluate('glLifecycle')
                        record['checks'].extend(['五次场景单体往返','已销毁WebGL上下文实际失效'])
                        select_model(manifest['scene']['id'],'scene');load_ready()
                    else:
                        assert sum('.glb' in row['url'] for row in record['scene_resources']) == 35
                        page.screenshot(path=str(evidence / '两公里引用装配.png'))
                        record['checks'].append('源地表及34原型装配实际加载')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), '桌面出现横向溢出'
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.screenshot(path=str(evidence / '手机布局.png'))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), '窄屏出现横向溢出'
                    record['checks'].append('桌面及窄屏无横向溢出')
                    assert not record['errors'], record['errors']
                    record.update(passed=True, elapsed_s=time.time() - started)
                    results.append(record)
                    atomic_json(evidence / '核验.json', record)
                except BaseException as error:
                    record.update(passed=False, failure=repr(error))
                    if 'page' in locals() and not page.is_closed():
                        record['failure_dom'] = page.evaluate('({url:location.href,name:document.querySelector("#model-name")?.textContent,subtitle:document.querySelector("#model-subtitle")?.textContent,selected:document.querySelector("#model-list [aria-selected=true]")?.dataset.model,load:document.querySelector("#viewer-state")?.textContent})')
                        page.screenshot(path=str(evidence / '失败画面.png'))
                    atomic_json(evidence / '核验.json', record)
                    raise
                finally:
                    browser.close()
        if fingerprint(pointer)['sha256'] != before['sha256']:
            raise ValueError('候选核验期间正式指针变化，需要重新核对')
        result = {'passed': True, 'browsers': results, 'manifest': fingerprint(manifest_path),
            'source_program': fingerprint(Path(__file__)), 'formal_pointer_unchanged': True,
            'source_files': {p.relative_to(ROOT).as_posix(): fingerprint(p) for p in
                [ROOT / 'src/资料库/app.mjs', ROOT / 'src/资料库/viewer.mjs', ROOT / 'scripts/断箭/启动正式资料库.py']},
            'scope': '候选查询和实际模型／场景加载；全图另验区域切换，不包含限速、帧时间或长时间驻留', 'human_acceptance': '待人工验收'}
        atomic_json(output / '验证结果.json', result)
        journal.close(status='A3全图候选双浏览器加载与切换核对通过' if full else 'A1候选双浏览器加载核对通过，待实际审图和阶段封存')
        print(json.dumps({'passed': True, 'browsers': len(results)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='候选浏览器核验失败，保留恢复点', error=repr(error))
        raise
    finally:
        delay['enabled']=False;delay['release'].set()
        if server:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--url', help='复用已登记的本机候选服务，避免另起验证服务')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效修订名')
    run(directory, args.manifest.resolve(), args.revision, args.url)
