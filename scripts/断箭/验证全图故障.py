"""原生前台验证目标租约、离屏模拟、取消重置、资源回退重试及真实水域。"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright


VIEW = """async p=>{const v=range.view,m=range.externalMap;v.stopCamera();v.center.set(p.x,m.coarseHeightAt(p.x,p.z),p.z);v.span=v.targetSpan=p.span??58;v.angle=v.targetAngle=p.angle??0;v.updateCamera();v.sync(range.sim,range.commander.selected,null,0);await m.waitForView();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));v.render();return range.perf();}"""


def run(args):
    output = args.directory / '故障与驻留验证' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    result = {'browser': args.browser, 'cases': [], 'errors': [], 'scope': '功能、数据驻留和独立故障；不混入正式帧时间样本',
              'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    phase = {'name': '启动'}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False)
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        result['version'] = browser.version
        page.on('pageerror', lambda error: result['errors'].append({'phase': phase['name'], 'kind': 'pageerror', 'text': error.stack or str(error)}))
        page.on('console', lambda message: result['errors'].append({'phase': phase['name'], 'kind': 'console', 'text': message.text, 'location': message.location}) if message.type == 'error' else None)

        def record(name, value):
            result['cases'].append({'name': name, 'passed': True, 'evidence': value})
            (output / '进行中.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
            print(args.browser+'：'+name+'通过', flush=True)

        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready', timeout=90000)
            result['identity'] = page.evaluate('fetch("/api/status").then(r=>r.json())')
            assert page.evaluate('typeof range.externalMap.commandState') == 'function'

            phase['name'] = '目标准备'
            issued = page.evaluate("""()=>{const u=range.sim.units.find(u=>u.kind==='tank'),point={x:-4000,z:-3500};return{id:u.id,point,before:range.externalMap.querySupport(point.x,point.z).state,accepted:range.sim.command([u.id],{type:'move',point})};}""")
            assert issued['before'] == 'pending' and issued['accepted'] == 1
            page.wait_for_function("id=>range.externalMap.commandState(range.sim.units.find(u=>u.id===id))!=='pending'", arg=issued['id'], timeout=20000)
            prepared = page.evaluate("""id=>{const u=range.sim.units.find(u=>u.id===id),m=range.externalMap;return{state:m.commandState(u),support:m.querySupport(-4000,-3500),owner:m.data.owners.has('command:'+id),unit_state:u.state,job:u.job};}""", issued['id'])
            assert prepared['support']['state'] in ['ready', 'unsupported']
            assert prepared['state'] == 'unreachable' or prepared['owner']
            page.evaluate('id=>range.sim.cancel([id])', issued['id'])
            page.wait_for_function("()=>![...range.externalMap.data.operations.keys()].some(k=>k.startsWith('command:'))", timeout=10000)
            assert not page.evaluate("[...range.externalMap.data.owners.keys()].some(k=>k.startsWith('command:'))")
            record('未知目标准备及取消引用', {'issued': issued, 'prepared': prepared})

            phase['name'] = '离屏单位'
            local = page.evaluate("""()=>{const r=range,u=r.sim.units.find(u=>u.kind==='tank');for(const [dx,dz]of [[0,-12],[12,0],[-12,0],[0,12],[0,-30],[30,0],[-30,0]]){const p={x:u.x+dx,z:u.z+dz,y:r.externalMap.heightAt(u.x+dx,u.z+dz)};if(r.sim.nav.walkable(p.x,p.z,5.8)&&!r.sim.nav.groundBlocked(u,p,5.8)){const start={x:u.x,z:u.z,time:r.sim.time};return{id:u.id,start,point:p,accepted:r.sim.command([u.id],{type:'move',point:p})};}}throw Error('未找到实际直线通行的短路线');}""")
            assert local['accepted'] == 1
            page.wait_for_function('id=>range.sim.units.find(u=>u.id===id).moving', arg=local['id'], timeout=10000)
            page.evaluate(VIEW, {'x': 4137.5, 'z': 3912.5, 'span': 140})
            page.wait_for_function("p=>{const u=range.sim.units.find(u=>u.id===p.id);return Math.hypot(u.x-p.start.x,u.z-p.start.z)>4&&range.sim.time-p.start.time>2;}", arg=local, timeout=10000)
            offscreen = page.evaluate("""id=>{const u=range.sim.units.find(u=>u.id===id),m=range.externalMap;return{unit:{x:u.x,y:u.y,z:u.z},support:m.querySupport(u.x,u.z),collisions:m.collisionsIn({minX:u.x-6,maxX:u.x+6,minZ:u.z-6,maxZ:u.z+6}).state,unit_owner:m.data.owners.has('unit:'+id),perf:range.perf()};}""", local['id'])
            assert offscreen['unit_owner'] and offscreen['support']['state'] == 'ready' and offscreen['collisions'] == 'ready'
            assert offscreen['perf']['visibleUnits'] == 0 and offscreen['perf']['totalUnits'] == 30
            record('镜头移开仍保留单位支撑碰撞及实际移动', {'route': local, 'after': offscreen})

            phase['name'] = '取消重置'
            page.evaluate(VIEW, {'x': 0, 'z': 85, 'span': 58})
            packet = page.evaluate("""()=>{const m=range.externalMap,p={x:4000,z:-3500},id=m.data.ids({minX:p.x,maxX:p.x,minZ:p.z,maxZ:p.z})[0],row=m.chunks.find(c=>c.id===id);return{point:p,url:new URL(m.simulation_packages[row.simulation_package].file,m.packageBase).href,before:m.querySupport(p.x,p.z).state};}""")
            assert packet['before'] == 'pending'
            held = []
            page.route(packet['url'], lambda route: held.append(route))
            page.evaluate('p=>range.sim.command([range.sim.units.find(u=>u.kind==="tank").id],{type:"move",point:p})', packet['point'])
            page.wait_for_function("()=>[...range.externalMap.data.operations.keys()].some(k=>k.startsWith('command:'))", timeout=10000)
            deadline = time.time()+5
            while not held and time.time() < deadline:
                page.wait_for_timeout(50)
            assert held, '目标模拟包没有进入实际请求'
            page.evaluate('range.sim.cancel(range.sim.units.map(u=>u.id))')
            page.locator('#reset').click()
            page.wait_for_function("()=>![...range.externalMap.data.owners.keys(),...range.externalMap.data.operations.keys()].some(k=>k.startsWith('command:'))", timeout=15000)
            for route in held:
                try:
                    route.abort()
                except Exception:
                    pass
            page.unroute(packet['url'])
            page.wait_for_function('!range.externalMap.stats.requests&&!range.externalMap.stats.dataPending', timeout=30000)
            reset = page.evaluate("""()=>({units:range.sim.units.length,tasks:range.sim.nav.tasks.size,command_owners:[...range.externalMap.data.owners.keys()].filter(k=>k.startsWith('command:')),command_operations:[...range.externalMap.data.operations.keys()].filter(k=>k.startsWith('command:')),command_generations:[...range.externalMap.data.generations.keys()].filter(k=>k.startsWith('command:')),perf:range.perf()})""")
            assert reset['units'] == 30 and not reset['command_owners'] and not reset['command_operations'] and not reset['command_generations']
            record('在途目标取消与重置不留下引用或旧回调', {'packet': packet, 'held_requests': len(held), 'after': reset})

            phase['name'] = '资源中断'
            broken = page.evaluate("""()=>{const m=range.externalMap,p={x:-4000,z:4000},id=m.data.ids({minX:p.x,maxX:p.x,minZ:p.z,maxZ:p.z})[0],row=m.chunks.find(c=>c.id===id);return{point:p,url:new URL(row.file,m.packageBase).href};}""")
            page.route(broken['url'], lambda route: route.abort())
            page.evaluate(VIEW, {**broken['point'], 'span': 58})
            page.wait_for_function('!!range.externalMap.stats.lastError&&!range.externalMap.stats.pending', timeout=30000)
            fallback = page.evaluate('({stats:{...range.externalMap.stats},ground_meshes:range.externalMap.visibleGroundMeshes().length})')
            assert fallback['stats']['overview'] and fallback['ground_meshes'] > 0
            page.locator('#map-retry').wait_for(state='visible', timeout=10000)
            page.screenshot(path=str(output / '资源中断回退.png'))
            page.unroute(broken['url'])
            page.locator('#map-retry').click()
            page.wait_for_function('!range.externalMap.stats.pending&&!range.externalMap.stats.lastError&&!range.externalMap.stats.overview', timeout=45000)
            page.screenshot(path=str(output / '真实重试恢复.png'))
            record('非当前区域资源中断回退及真实按钮重试', {'broken': broken, 'fallback': fallback, 'recovered': page.evaluate('range.perf()')})

            phase['name'] = '快速镜头'
            rapid = page.evaluate("""async()=>{const v=range.view,m=range.externalMap;const points=[[-2200,1800],[3500,-2500],[0,85],[4137.5,3912.5],[-1200,-1200]];for(const [x,z]of points){v.stopCamera();v.center.set(x,m.coarseHeightAt(x,z),z);v.span=v.targetSpan=140;v.updateCamera();v.sync(range.sim,range.commander.selected,null,0);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));}await m.waitForView();await new Promise(r=>requestAnimationFrame(r));return range.perf();}""")
            assert abs(rapid['camera']['x']+1200) < .001 and abs(rapid['camera']['z']+1200) < .001
            assert not rapid['mapStats']['lastError'] and rapid['mapStats']['visibleChunks'] <= 49
            record('快速转场只提交最新视区', rapid)

            phase['name'] = '空中聚焦'
            page.evaluate(VIEW, {'x': 0, 'z': 85, 'span': 58})
            if not page.evaluate('range.sim.paused'):
                page.keyboard.press('Space')
            focus = []
            for kind in ['tank', 'heli', 'plane']:
                page.evaluate('kind=>range.commander.choose(range.sim.units.find(u=>u.kind===kind).id)', kind)
                page.keyboard.press('c')
                page.wait_for_function('!range.view.centerTarget&&!range.externalMap.stats.pending', timeout=20000)
                row = page.evaluate("""async kind=>{const T=await import('three'),u=range.sim.units.find(u=>u.kind===kind),v=range.view,p=new T.Vector3(u.x,u.y,u.z).project(v.camera);return{kind,unit:{x:u.x,y:u.y,z:u.z},ndc:p.toArray(),display_index:v.units.get(u.id).displayIndex,camera:v.camera.position.toArray(),center:v.center.toArray(),level:range.externalMap.stats.sceneLevel};}""", kind)
                assert abs(row['ndc'][0]) < .2 and abs(row['ndc'][1]) < .2 and -1 <= row['ndc'][2] <= 1 and row['display_index'] >= 0, row
                assert row['level'] == 'near'
                focus.append(row)
                page.screenshot(path=str(output / (kind+'-真实聚焦.png')))
            record('三类单位真实聚焦均在视锥且保留近景', focus)

            phase['name'] = '水域与预算'
            await_water_view=page.evaluate(VIEW,{'x':2732,'z':3608,'span':140})
            visible_water=page.evaluate('''async()=>{const T=await import('three'),m=range.externalMap;m.root.updateMatrixWorld(true);const meshes=m.visibleWaterMeshes(),hits=new T.Raycaster(new T.Vector3(2728,1000,3571),new T.Vector3(0,-1,0)).intersectObjects(meshes,false);return{meshes:meshes.length,height:hits[0]?.point.y,physical:m.querySupport(2728,3571).waterHeight};}''')
            assert visible_water['meshes']==2 and abs(visible_water['height']-visible_water['physical'])<.001,visible_water
            water = page.evaluate("""async()=>{const m=range.externalMap,b={minX:2660,maxX:2805,minZ:3555,maxZ:3660};await m.prepareRegion('fault:water',b);let found;for(let z=3565;z<3650&&!found;z+=6)for(let x=2668;x<2795;x+=6){const q=m.querySupport(x,z);if(q.state==='ready'&&q.waterDepth>.25){const a={x,y:q.waterHeight+2,z},end={x,y:q.waterHeight-2,z};found={point:{x,z},support:q,walkable:range.sim.nav.walkable(x,z,5.8),segment:m.supportSegment(a,end)};break;}}if(!found)throw Error('源池塘没有找到实际深水样点');const admitted=m.data.canPrepare(m.bounds,12*1048576),u=range.sim.units.find(u=>u.kind==='tank'),retained=m.querySupport(u.x,u.z).state;m.releaseRegion('fault:water');return{water:found,whole_map_admitted:admitted,unit_support:retained};}""")
            assert not water['water']['walkable'] and abs(water['water']['segment']['hit']-.5) < .001
            assert not water['whole_map_admitted'] and water['unit_support'] == 'ready'
            water['visible_water']=visible_water
            record('真实池塘不可通行及整图准备预算拒绝', water)

            phase['name'] = '粗路由'
            rough = page.evaluate("""async()=>{const n=range.sim.nav,r=await n.roughGoal({x:-4400,y:0,z:-4100},{x:4400,z:4700}),nodes=n.graph.nodes;return{result:r,edge_points:r.edge?.map(i=>nodes[i]),grid:n.graph.grid};}""")
            assert rough['result'].get('edge') and max(abs(rough['edge_points'][0][axis]-rough['edge_points'][1][axis]) for axis in ['x', 'z']) <= 125.001
            record('跨全图粗路由阻断键为相邻格边', rough)

            unexpected = [error for error in result['errors'] if error['kind'] == 'pageerror' or not (error['phase'] in ['取消重置', '资源中断'] and any(word in error['text'] for word in ['ERR_', '加载失败', 'Failed to fetch', 'aborted']))]
            result['unexpected_errors'] = unexpected
            assert not unexpected, unexpected
            result['passed'] = True
        except BaseException as error:
            result.update(passed=False, exception=repr(error), failed_phase=phase['name'])
            try:
                result['failure_state'] = page.evaluate('window.range?.perf()')
                page.screenshot(path=str(output / '失败.png'))
            except Exception:
                pass
            raise
        finally:
            browser.close()
            (output / '核验凭证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', required=True, choices=['chrome', 'msedge'])
    parser.add_argument('--port', type=int, default=8784)
    run(parser.parse_args())
