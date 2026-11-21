"""双浏览器分别执行三次限速冷启动与三次保留HTTP缓存的新页面温启动。"""

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit, unquote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def save(file, value):
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')


def run(args):
    output = args.directory / '网络正式验证' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    url = f'http://127.0.0.1:{args.port}/'
    with urlopen(url+'api/status') as response:
        identity = json.load(response)
    assert identity['app'] == 'scavenger-airbase-map' and identity['map'] == 'airbase-full'
    freeze = args.directory / ('浏览器源码-'+identity['revision'])
    service = json.loads((freeze / '服务身份.json').read_text('utf-8'))
    routes = service['routes']
    result = {'browser': args.browser, 'identity': identity, 'viewport': [1280, 720], 'internal_required': [640, 364], 'dpr': 1,
              'network': {'download_mbps': 20, 'upload_mbps': 5, 'latency_ms': 80}, 'samples': [],
              'scope': 'HTTP冷缓存及同上下文HTTP缓存的新页面；不冒称设备冷启动或清除驱动着色缓存',
              'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False, args=['--disable-frame-rate-limit', '--disable-gpu-vsync'])
        result['version'] = browser.version

        def sample(context, name, cold, seed=False):
            page = context.new_page()
            cdp = context.new_cdp_session(page)
            cdp.send('Network.enable')
            cdp.send('Network.setCacheDisabled', {'cacheDisabled': cold and not seed})
            cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': 80, 'downloadThroughput': 20*1000000/8, 'uploadThroughput': 5*1000000/8, 'connectionType': 'cellular4g'})
            requests, errors = {}, []
            cdp.on('Network.requestWillBeSent', lambda event: requests.setdefault(event['requestId'], {'url': event['request']['url'], 'type': event.get('type'), 'start': event['timestamp']}))
            def response(event):
                row = requests.setdefault(event['requestId'], {'url': event['response']['url']})
                row.update(status=event['response']['status'], disk_cache=event['response'].get('fromDiskCache', False))
            cdp.on('Network.responseReceived', response)
            def complete(event):
                requests.setdefault(event['requestId'], {}).update(transfer_bytes=event['encodedDataLength'], end=event['timestamp'])
            cdp.on('Network.loadingFinished', complete)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.add_init_script('window.startupVisibility=[];addEventListener("visibilitychange",()=>startupVisibility.push({ms:performance.now(),hidden:document.hidden}),true)')
            page.bring_to_front()
            started = time.time()
            record = {'name': name, 'cold_http': cold, 'cache_disabled': cold and not seed, 'started_epoch': started}
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=60000)
                record['feedback_seconds'] = time.time()-started
                assert page.locator('#loading').is_visible() or page.evaluate('!!window.range?.ready')
                page.wait_for_function('window.range?.ready || !document.querySelector("#fatal").hidden', timeout=60000)
                assert page.evaluate('!!window.range?.ready'), page.locator('#fatal').inner_text()
                record['ready_seconds'] = time.time()-started
                record['ready_perf'] = page.evaluate('range.perf()')
                record['visibility']=page.evaluate('({hidden:document.hidden,focused:document.hasFocus(),events:startupVisibility})')
                assert not record['visibility']['hidden'], '原生前台启动样本页面不可见'
                record['startup_marks'] = page.evaluate('performance.getEntriesByType("mark").map(m=>({name:m.name,time:m.startTime}))')
                record['audio_cache'] = page.evaluate('''()=>({required:Object.values(range.manifest.audio).map(a=>a.audio_id),cached:[...range.audio.cache.keys()],stats:range.audio.stats()})''')
                assert set(record['audio_cache']['required']).issubset(record['audio_cache']['cached'])
                assert record['ready_perf']['totalUnits'] == 30 and record['ready_perf']['internal'] == [640, 364]
                assert record['ready_perf']['mapStats']['dataPending'] == 0
                assert record['ready_perf']['mapStats']['pending'] == 0 and record['ready_perf']['mapStats']['transcodes'] == 0, '就绪时显示区域仍未完成'
                # 工作器和初始区域都已准备，实际提交一条移动命令检查能力，而非只判断函数存在。
                move = page.evaluate('''()=>{for(const u of range.sim.units.filter(u=>u.kind==='tank'))for(const [dx,dz]of [[0,-12],[12,0],[-12,0],[0,12]]){const p={x:u.x+dx,z:u.z+dz,y:range.sim.map.heightAt(u.x+dx,u.z+dz)};if(range.sim.nav.walkable(p.x,p.z,5.8)&&!range.sim.nav.groundBlocked(u,p,5.8)){range.sim.command([u.id],{type:'move',point:p});return{id:u.id,point:p};}}throw Error('首屏未找到可移动探测点');}''')
                page.wait_for_function('id=>range.sim.units.find(u=>u.id===id).moving', arg=move['id'], timeout=15000)
                record['operable_seconds'] = time.time()-started
                record['movement_probe'] = move
                record['resources'] = page.evaluate('performance.getEntriesByType("resource").map(r=>({url:r.name,encoded:r.encodedBodySize,decoded:r.decodedBodySize,transfer:r.transferSize,duration:r.duration}))')
                http = [row for row in record['resources'] if row['url'].startswith(url)]
                record['response_body_bytes'] = sum(row['encoded'] for row in http)
                record['decoded_body_bytes'] = sum(row['decoded'] for row in http)
                record['map_response_body_bytes'] = sum(row['encoded'] for row in http if urlsplit(row['url']).path.startswith('/map/'))
                network = [r for r in requests.values() if r.get('url', '').startswith(url)]
                record['actual_transfer_bytes'] = sum(r.get('transfer_bytes', 0) for r in network)
                record['map_actual_transfer_bytes'] = sum(r.get('transfer_bytes', 0) for r in network if urlsplit(r['url']).path.startswith('/map/'))
                unique, unique_all = {}, {}
                for row in http:
                    key = unquote(urlsplit(row['url']).path)
                    if key in routes:
                        route = routes[key]
                        unique[route['sha256']] = route['bytes']
                        unique_all[route['sha256']] = route['bytes']
                    else:
                        source_name = 'index.html' if key == '/' else key.lstrip('/')
                        source = service.get('sources', {}).get(source_name)
                        unique_all[source['http_sha256'] if source else key] = row['decoded']
                record['unique_registered_content_bytes'] = sum(unique.values())
                record['unique_content_bytes'] = sum(unique_all.values())
                record['unique_content_method'] = '登记媒体与源码按SHA256去重，其余固定第三方代码按规范URL去重；均按解码后内容大小'
                record['requests'] = network
                record['errors'] = errors
                page.screenshot(path=str(output / (name+'.png')))
                assert not errors, errors
                assert record['feedback_seconds'] <= 3
                if cold:
                    assert record['map_actual_transfer_bytes'] <= 20*1048576
                    assert record['actual_transfer_bytes'] <= 25*1048576
                    assert record['operable_seconds'] <= 15
                else:
                    assert record['operable_seconds'] <= 3
                record['passed'] = True
            except BaseException as error:
                record.update(passed=False, exception=repr(error), errors=errors)
                try:
                    record['failure_state'] = page.evaluate('window.range?.perf()')
                    page.screenshot(path=str(output / (name+'-失败.png')))
                except Exception:
                    pass
                raise
            finally:
                result['samples'].append(record)
                save(output / '进行中.json', result)
                page.close()
            print(json.dumps({'sample': name, 'operable_seconds': record['operable_seconds'], 'map_mib': record['map_actual_transfer_bytes']/1048576, 'total_mib': record['actual_transfer_bytes']/1048576}, ensure_ascii=False), flush=True)

        try:
            for i in range(3):
                context = browser.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
                sample(context, f'冷启动-{i+1:02}', True)
                context.close()
            context = browser.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
            # 首个页面建立正常HTTP缓存，仍保留记录并执行相同可操作标准。
            sample(context, '温缓存种子', True, seed=True)
            for i in range(3):
                sample(context, f'温启动-{i+1:02}', False)
            context.close()
            result['passed'] = True
        except BaseException as error:
            result.update(passed=False, exception=repr(error))
            raise
        finally:
            browser.close()
            save(output / '核验凭证.json', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], required=True)
    parser.add_argument('--port', type=int, default=8784)
    run(parser.parse_args())
