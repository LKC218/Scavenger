"""独立Chrome/Edge串行执行地图键鼠、回归及固定时长负载。"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
from playwright.sync_api import sync_playwright


def save(path, data): path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def run(args):
    out = args.directory / '浏览器验收' / args.run / args.browser
    out.mkdir(parents=True, exist_ok=False)
    result = {'browser': args.browser, 'window': args.window, 'viewport': [1280, 720], 'dpr': 1,
              'host': platform.platform(), 'phase': args.phase, 'errors': [], 'checks': [], 'samples': [],
              'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        flags = ['--disable-frame-rate-limit', '--disable-gpu-vsync']
        browser = pw.chromium.launch(channel=args.browser, headless=not args.window, args=flags)
        result.update(version=browser.version, flags=flags, timing='解除垂直同步上限测整帧；可见窗口与无窗口分别登记')
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
        page.on('response', lambda r: result['errors'].append(str(r.status) + ' ' + r.url) if r.status >= 400 else None)
        try:
            begin = time.time()
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready', timeout=60000)
            result['cold_ready_seconds'] = time.time() - begin
            result['identity'] = page.evaluate('fetch("/api/status").then(r=>r.json())')
            resources = page.evaluate('performance.getEntriesByType("resource").map(r=>({path:new URL(r.name).pathname,bytes:r.encodedBodySize,duration:r.duration}))')
            result['cold_resources'] = resources
            result['map_transfer_bytes'] = sum(r['bytes'] for r in resources if r['path'].startswith('/map/'))
            result['initial'] = page.evaluate('range.perf()')
            if result['identity']['map']=='airbase-2000':
                assert result['initial']['triangles']<=2000000, '默认局部提交超过建议预算'
                result['negative_static_instances']=page.evaluate('''()=>{let negative=0;range.externalMap.root.traverse(o=>{if(o.isInstancedMesh){const m=o.matrix.clone();for(let i=0;i<o.count;i++){o.getMatrixAt(i,m);if(m.determinant()*o.matrixWorld.determinant()<0)negative++;}}});return negative;}''')
                assert result['negative_static_instances']==0
                assert result['initial']['mapStats']['mirroredPrototypeMeshes']>=1
            def check(name): result['checks'].append(name); print(args.browser + '：' + name, flush=True); save(out / '进行中.json', result)
            def wait(js, timeout=20000):
                result['waiting_for'] = js
                save(out / '进行中.json', result)
                page.wait_for_function('() => ' + js, timeout=timeout)
            def shot(name): page.screenshot(path=str(out / (name + '.png')))
            def click_world(p, button='left'):
                q = page.evaluate('(p)=>range.view.project(p)', p)
                box = page.locator('#battlefield').bounding_box()
                assert 0 < q['x'] < box['width'] and 0 < q['y'] < box['height'], ('落点不在画内', q)
                page.mouse.click(box['x'] + q['x'], box['y'] + q['y'], button=button)
            if args.phase in ('all', 'interaction'):
                assert result['identity']['app'] == 'scavenger-airbase-map'
                shot('局部')
                page.locator('#overview').click(); wait('Math.abs(range.view.span-range.view.targetSpan)<.01'); shot('全图')
                page.locator('#map-debug').click(); shot('来源轮廓与道路叠加'); page.locator('#map-debug').click()
                page.locator('#battlefield').click(position={'x': 40, 'y': 50}); page.keyboard.press('f'); wait('!range.view.centerTarget && range.view.span===range.sim.map.defaultSpan')
                # 暂停后真实坐标选择当前视野内载具。
                page.keyboard.press('Space'); wait('range.sim.paused')
                point = page.evaluate('''()=>{const u=range.sim.units.find(u=>{const p=range.view.project({x:u.x,y:u.y+2.3,z:u.z});return u.kind==='tank'&&p.x>70&&p.x<range.view.width-70&&p.y>100&&p.y<range.view.height-100;});return {x:u.x,y:u.y+2.3,z:u.z};}''')
                click_world(point); assert page.evaluate('range.commander.selected.size') == 1
                uid = page.evaluate('[...range.commander.selected][0]')
                target = page.evaluate('''id=>{const u=range.sim.units.find(u=>u.id===id),offsets=[[8,-12],[-8,-12],[0,18]];if(range.sim.map.streaming)for(const r of [8,12,18,24,32])for(let i=0;i<16;i++)offsets.push([Math.cos(i*Math.PI/8)*r,Math.sin(i*Math.PI/8)*r]);for(const [dx,dz] of offsets){const x=u.x+dx,z=u.z+dz,point={x,z,y:range.sim.map.heightAt(x,z)},screen=range.view.project(point);if(screen.x<40||screen.y<50||screen.x>range.view.width-40||screen.y>range.view.height-40)continue;if(range.sim.nav.walkable(x,z,5.3)&&(!range.sim.map.streaming||!range.sim.nav.groundBlocked(u,point,5.3)))return point;}throw Error('没有测试落点');}''', uid)
                frozen = page.evaluate('range.sim.time'); click_world(target, 'right')
                assert page.evaluate('range.sim.time') == frozen
                assert page.evaluate('id=>range.sim.units.find(u=>u.id===id).job?.type', uid) == 'move'
                page.keyboard.press('Space'); wait('range.sim.units.some(u=>u.kind==="tank"&&u.moving)')
                page.keyboard.press('e'); assert page.evaluate('id=>!range.sim.units.find(u=>u.id===id).job', uid)
                page.keyboard.press('r'); click_world(target); wait('range.sim.units.some(u=>u.state==="倒车中")'); page.keyboard.press('e')
                page.keyboard.press('c'); wait('!range.view.centerTarget')
                box = page.locator('#battlefield').bounding_box(); page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2); page.mouse.wheel(0, 180)
                wait('range.view.span>58.1'); page.keyboard.press('f'); wait('range.view.span===range.sim.map.defaultSpan')
                check('真实点选、右键移动、暂停恢复、停止、倒车、聚焦和缩放')
                # 用射线投影点核对高度；实际GLB核对另存100点报告。
                result['picking_error'] = page.evaluate('''()=>{const p=range.view.ground(range.view.width/2,range.view.height/2);return Math.abs(p.y-range.sim.map.heightAt(p.x,p.z));}''')
                assert result['picking_error'] < .01
                external = page.evaluate('range.sim.map.id')
                for name in ['classic', 'city', external, 'classic', external]:
                    page.locator('#scenario').select_option(name)
                    wait('range.sim.map.id===' + json.dumps(name))
                    assert page.evaluate('range.sim.units.every(u=>Number.isFinite(u.y))')
                    if name in ('classic','city'): assert page.evaluate('range.sim.units.filter(u=>u.kind==="tank").every(u=>u.y===0)')
                page.locator('#reset').click(); wait('!range.externalMap.stats.pending'); page.wait_for_timeout(200)
                before = page.evaluate('range.view.renderer.info.memory')
                history = []
                for _ in range(5):
                    page.locator('#reset').click(); page.wait_for_timeout(100); history.append(page.evaluate('range.view.renderer.info.memory'))
                result['reset_resources'] = {'before': before, 'after_each': history}
                assert all(r['geometries'] <= before['geometries']+1 and r['textures'] <= before['textures'] for r in history)
                page.locator('#sound').click(); wait('range.mixer.active && Object.values(range.manifest.audio).every(asset=>range.audio.cache.has(asset.audio_id)) && range.audio.stats().pending===0')
                result['audio_cache_coverage'] = page.evaluate('''()=>({required:Object.values(range.manifest.audio).map(a=>a.audio_id),cached:[...range.audio.cache.keys()],stats:range.audio.stats()})''')
                result['audio'] = page.evaluate('range.mixer.stats()'); shot('声音与阴影')
                check('真实高度拾取、旧地图平地回归、五次切换与重置、声音解码')
            if result['identity']['map']=='airbase-2000' and args.phase in ('all','streaming'):
                # 绝对时间驱动90秒闭合镜头路线，逐秒射线检查实际可见地表，记录回收与回访。
                page.evaluate('''()=>{const v=range.view;v.stopCamera();v.span=v.targetSpan=120;range.resetMetrics();
                  const points=[[-850,-850],[850,-850],[850,850],[-850,850],[-850,-850]],start=performance.now();
                  window.mapCross={done:false,start,duration:90};
                  function frame(now){const t=Math.max(0,Math.min(90,(now-start)/1000)),s=t/22.5,i=Math.min(3,Math.floor(s)),f=Math.min(1,s-i);
                    v.center.set(points[i][0]*(1-f)+points[i+1][0]*f,0,points[i][1]*(1-f)+points[i+1][1]*f);v.updateCamera();
                    if(t<90)requestAnimationFrame(frame);else mapCross.done=true;}
                  requestAnimationFrame(frame);}''')
                cross={'duration':90,'timeline':[],'coverage':[]};start=time.time();deadline=start+110;next_shot=0;checkpoint=start
                result['streaming']=cross
                while not page.evaluate('mapCross.done'):
                    if time.time()>deadline: raise RuntimeError('跨区镜头超时')
                    page.wait_for_timeout(1000)
                    elapsed=time.time()-start
                    cross['timeline'].append(page.evaluate('range.perf()'))
                    coverage=page.evaluate('''()=>{const v=range.view,m=range.externalMap,meshes=m.visibleGroundMeshes();let tested=0,missing=0;
                      for(const x of [.1,.3,.5,.7,.9])for(const y of [.1,.3,.5,.7,.9]){const p=v.groundPoint(x*v.width,y*v.height);
                        if(!p||p.x<m.bounds.minX||p.x>m.bounds.maxX||p.z<m.bounds.minZ||p.z>m.bounds.maxZ)continue;tested++;
                        if(!v.ray(x*v.width,y*v.height).intersectObjects(meshes,false).length)missing++;}
                      return{tested,missing,pending:m.stats.pending,overview:m.stats.overview};}''')
                    cross['coverage'].append({'seconds':elapsed,**coverage})
                    if time.time()-checkpoint>=15:
                        checkpoint=time.time();result['active']={'name':'跨区90秒','elapsed_seconds':elapsed};save(out/'进行中.json',result)
                    if elapsed>=next_shot: shot('跨区-'+str(round(next_shot)));next_shot+=22.5
                wait('!range.externalMap.stats.pending')
                cross['elapsed_seconds']=time.time()-start;cross['final']=page.evaluate('range.perf()');shot('跨区-回访')
                result['streaming']=cross;save(out/'跨区90秒.json',cross)
                assert cross['elapsed_seconds']>=90
                assert sum(r['tested'] for r in cross['coverage'])>=2000 and all(r['missing']==0 for r in cross['coverage'])
                assert max(r['mapStats']['loadedChunks'] for r in cross['timeline'])<=49
                assert cross['final']['mapStats']['evicted']>0 and not cross['final']['mapStats']['errors']
                assert max(r['textureEstimateBytes'] for r in cross['timeline'])<=512*1024**2
                page.keyboard.press('f');wait('range.view.span===range.sim.map.defaultSpan && !range.view.centerTarget');wait('!range.externalMap.stats.pending')
                check('90秒跨区、逐秒25点真实地表覆盖、分块回收和回访')
            if args.phase in ('all', 'performance'):
                if not page.evaluate('range.mixer.active'): page.locator('#sound').click(); wait('range.mixer.active')
                def sample(name, count, mode, duration, composition=None):
                    page.evaluate("p=>{range.sim.options.composition=p.composition??undefined;const select=document.querySelector('#unit-count');if(![...select.options].some(o=>o.value===String(p.count)))select.add(new Option(String(p.count),String(p.count)));}", {'count': count, 'composition': composition})
                    page.locator('#unit-count').select_option(str(count))
                    page.locator('#load-mode').select_option(mode)
                    page.evaluate("p=>{range.resetMetrics();range.benchmark.start(performance.now(),p.duration,{...range.perf(),map:range.sim.map.id,load:p.mode,audio:range.mixer.active,window:!navigator.userAgent.includes('Headless')});}", {'duration':duration,'mode':mode})
                    wait('range.benchmark.last===null || range.benchmark.run!==null')
                    start=time.time();deadline=start+duration+35;checkpoint=start
                    while page.evaluate('!!range.benchmark.run'):
                        if time.time()>deadline: raise RuntimeError('性能采样超时或窗口进入后台')
                        page.wait_for_timeout(1000)
                        if time.time()-checkpoint>=15:
                            checkpoint=time.time();result['active']={'name':name,'elapsed_seconds':checkpoint-start,'latest':page.evaluate('range.perf()')};save(out/'进行中.json',result)
                    receipt = page.evaluate('range.benchmark.last'); receipt['name']=name; receipt['final']=page.evaluate('range.perf()')
                    assert receipt['duration'] >= duration-.05
                    assert receipt['totalUnits'] == count
                    assert not page.evaluate('document.hidden')
                    assert receipt['p95']<=16.67, ('整帧P95超标',receipt['p95'])
                    assert max(r['drawCalls'] for r in receipt['timeline'])<=(350 if result['identity']['map']=='airbase-2000' else 200)
                    assert max(r['triangles'] for r in receipt['timeline'])<=(2000000 if result['identity']['map']=='airbase-2000' else 1000000)
                    if count==100:
                        assert receipt['final']['shots']>100 and receipt['final']['hits']>0
                        assert max(r.get('movingUnits',0) for r in receipt['timeline'])>=30
                        assert receipt['p95']<=16.67, ('100单位P95超标',receipt['p95'])
                    result['samples'].append(receipt);save(out/(name+'.json'),receipt);shot(name)
                    check(name+'完成，P95 '+str(round(receipt['p95'],3))+'ms')
                sample('空场60秒',0,'static',60,{'tank':0,'heli':0,'plane':0})
                sample('十辆M1A2移动60秒',10,'move',60,{'tank':10,'heli':0,'plane':0})
                sample('百单位移动交战180秒',100,'combat',180)
                budget = 80 if result['identity']['map']=='airbase-2000' else 50
                assert result['map_transfer_bytes']<=budget*1024**2
                assert result['cold_ready_seconds']<=(15 if budget==80 else 10)
                assert max(r['textureEstimateBytes'] for s in result['samples'] for r in s['timeline'])<=(512 if budget==80 else 256)*1024**2
            assert not result['errors'], result['errors']
            result.pop('active',None);result['status']='passed'
        except Exception as error:
            result['status']='failed';result['failure']=repr(error)
            try: result['failure_state']=page.evaluate('window.range?.perf()');page.screenshot(path=str(out/'失败.png'))
            except Exception: pass
        finally:
            save(out/'浏览器验收.json',result);browser.close()
    print(json.dumps({'status':result['status'],'browser':args.browser,'failure':result.get('failure'),'output':str(out)},ensure_ascii=False),flush=True)
    if result['status']!='passed': raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--browser',choices=['chrome','msedge'],required=True);p.add_argument('--window',action='store_true');p.add_argument('--run',required=True);p.add_argument('--port',type=int,default=8780);p.add_argument('--phase',choices=['all','interaction','performance','streaming'],default='all');run(p.parse_args())
