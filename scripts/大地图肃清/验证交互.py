"""对固定8810候选做交互回归；不启动或终止服务，不代替人工和正式性能验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit


def verify_body_mouse(page, check):
    """只用公开镜头、正常命令和真实鼠标验证；清空选择仅作为前置状态。"""
    def frame_settle():
        page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')

    def selected_ids():
        return page.evaluate('window.scavenger.interaction.getState().selectedIds')

    def clear_selection():
        page.evaluate('window.scavenger.interaction.setSelection([])')

    def body(ident):
        return page.evaluate('''id => {
            const a=window.scavenger,p=a.presentation.projectUnit(id),r=document.querySelector('#viewport').getBoundingClientRect();
            const x=r.left+p.x,y=r.top+p.y,hit=document.elementFromPoint(x,y);
            return {x,y,localX:p.x,localY:p.y,visible:p.visible,hitCanvas:hit?.id==='viewport',hit:hit?.className||hit?.id||null,hitUnitId:hit?.dataset?.unitId||null};
        }''', ident)

    def exact_selection(ids):
        page.wait_for_function('(ids)=>JSON.stringify([...window.scavenger.interaction.getState().selectedIds].sort())===JSON.stringify([...ids].sort())', arg=ids, timeout=3000)

    def fresh_fixture(paused=True):
        page.evaluate('''paused => {const a=window.scavenger;a.setPaused(true);a.reset({preset:'interaction',seed:1105});a.interaction.setView({focus:{x:0,y:0,z:0},viewWidth:360,yaw:0});a.setPaused(paused);}''', paused)
        page.locator('[data-action="filter"][data-value="all"]').click()
        frame_settle()

    fresh_fixture()
    ids = page.evaluate('window.scavenger.sim.snapshot().units.map(u=>({id:u.id,kind:u.kind}))')
    # 每次把待验机身放到无遮挡视口中心，不能让面板或标签截获输入。
    for width in (120, 360, 900):
        for direction, yaw in enumerate((0, math.pi*2/3, -math.pi*2/3), start=1):
            for row in ids:
                view = page.evaluate('''v => {const a=window.scavenger,u=a.sim.snapshot().units.find(u=>u.id===v.id);return a.interaction.setView({focus:{...u.position},viewWidth:v.width,yaw:v.yaw});}''', {'id': row['id'], 'width': width, 'yaw': yaw})
                frame_settle()
                point = body(row['id'])
                setup = {'型号': row['kind'], '单位ID': row['id'], '视角': view, '机身CSS位置': point, '前置状态': '公开暂停模拟；公开镜头居中；仅清空选择'}
                check(f'机身输入无遮挡-{row["kind"]}-{width}米-方向{direction}', point['visible'] and point['hitCanvas'], setup, 'B.projectUnit机身中心与elementFromPoint')
                clear_selection()
                page.mouse.click(point['x'], point['y'])
                exact_selection([row['id']])
                check(f'真实机身点选-{row["kind"]}-{width}米-方向{direction}', selected_ids() == [row['id']], setup, '真实page.mouse.click；读取selectedIds')
                clear_selection()
                page.mouse.move(point['x']-8, point['y']-8)
                page.mouse.down()
                page.mouse.move(point['x']+8, point['y']+8, steps=5)
                page.mouse.up()
                exact_selection([row['id']])
                check(f'真实机身拖框-{row["kind"]}-{width}米-方向{direction}', selected_ids() == [row['id']], {**setup, '框CSS尺寸': [16, 16]}, '真实鼠标按下/拖动/松开；读取selectedIds')

    # 固定翼恢复持续运动，连续重取当前机身中心并实际点击；不点标签或卡片。
    plane = next(row['id'] for row in ids if row['kind'] == 'plane')
    page.evaluate('''id=>{const a=window.scavenger,u=a.sim.snapshot().units.find(u=>u.id===id);a.interaction.setView({focus:{...u.position},viewWidth:360,yaw:0});a.interaction.setSelection([]);a.setPaused(false);}''', plane)
    frame_settle()
    point = body(plane)
    check('固定翼跟随前机身输入无遮挡', point['visible'] and point['hitCanvas'], point, 'B.projectUnit机身中心与elementFromPoint')
    page.mouse.click(point['x'], point['y'])
    exact_selection([plane])
    page.locator('[data-action="follow"]').click()
    page.wait_for_function('(id)=>window.scavenger.interaction.getState().followId===id', arg=plane)
    start_position = page.evaluate('(id)=>({...window.scavenger.sim.snapshot().units.find(u=>u.id===id).position})', plane)
    follow_started = time.time()
    clicks = []
    for index in range(8):
        # 每个采样点按同一绝对起点排期，避免累计休眠作为计时基准。
        remaining = follow_started + index*.25 - time.time()
        if remaining > 0:
            page.wait_for_timeout(remaining*1000)
        point = body(plane)
        check(f'运动固定翼机身输入无遮挡-{index+1}', point['visible'] and point['hitCanvas'], point, '当前B.projectUnit与elementFromPoint；未点击标签')
        page.mouse.click(point['x'], point['y'])
        exact_selection([plane])
        follow = page.evaluate('window.scavenger.interaction.getState().followId')
        clicks.append({'次序': index+1, '绝对起点后秒': round(time.time()-follow_started, 3), '机身CSS位置': point, '选中ID': selected_ids(), '跟随ID': follow})
        check(f'跟随运动固定翼真实机身点击-{index+1}', selected_ids() == [plane] and follow == plane, clicks[-1], '持续飞行中真实page.mouse.click；读取选择与跟随')
    end_position = page.evaluate('(id)=>({...window.scavenger.sim.snapshot().units.find(u=>u.id===id).position})', plane)
    moved = math.hypot(end_position['x']-start_position['x'], end_position['z']-start_position['z'])
    check('连续机身点击期间固定翼实际移动', moved > 20, {'XZ位移米': moved, '用时秒': round(time.time()-follow_started, 3), '点击次数': len(clicks)}, '公开规则位置只读比较；未暂停运动')

    # 新本轮中用正常move命令构造地空投影重叠，不写规则内部状态。
    fresh_fixture()
    tank = next(row['id'] for row in ids if row['kind'] == 'tank')
    heli = next(row['id'] for row in ids if row['kind'] == 'heli')
    page.evaluate('''id=>{const a=window.scavenger,u=a.sim.snapshot().units.find(u=>u.id===id);a.interaction.setView({focus:{...u.position},viewWidth:360,yaw:0});}''', tank)
    frame_settle()
    moves = []
    for attempt in range(5):
        ground, air = body(tank), body(heli)
        separation = math.hypot(ground['x']-air['x'], ground['y']-air['y'])
        if separation <= 5:
            break
        move = page.evaluate('''v=>{
            const a=window.scavenger,s=a.sim.snapshot(),u=s.units.find(u=>u.id===v.heli),r=document.querySelector('#viewport').getBoundingClientRect(),view=a.interaction.getState();
            const scale=view.viewWidth/r.width,dx=v.dx*scale,dz=v.dy*scale/Math.sin(a.config.camera.tilt),yaw=view.yaw;
            const point={x:u.position.x+Math.cos(yaw)*dx+Math.sin(yaw)*dz,z:u.position.z-Math.sin(yaw)*dx+Math.cos(yaw)*dz};
            const support=a.world.querySupport(point.x,point.z);if(support.status!=='ready')return{ok:false,reason:'重叠样本落点无支撑',point};
            point.y=support.height;const result=a.dispatch({type:'move',unitIds:[u.id],point});a.setPaused(false);return{...result,point,runId:s.runId};
        }''', {'heli': heli, 'dx': ground['x']-air['x'], 'dy': ground['y']-air['y']})
        moves.append(move)
        check(f'重叠样本正常移动命令-{attempt+1}', bool(move.get('ok')), move, '新本轮公开dispatch(move)；不改规则内部字段')
        page.wait_for_function('''v=>{const u=window.scavenger.sim.snapshot().units.find(u=>u.id===v.id);return Math.hypot(u.position.x-v.point.x,u.position.z-v.point.z)<.1;}''', arg={'id': heli, 'point': move['point']}, timeout=8000)
        page.evaluate('window.scavenger.setPaused(true)')
        frame_settle()
    ground, air = body(tank), body(heli)
    separation = math.hypot(ground['x']-air['x'], ground['y']-air['y'])
    overlap_detail = {'样本来源': '独立reset后用正常直升机move命令校准地空机身投影；无规则字段注入', '移动命令': moves, '地面机身': ground, '空中机身': air, '投影距离CSS像素': separation}
    check('真实命令构成地空重叠样本', separation <= 5, overlap_detail, '规则移动结果与B机身投影')
    clear_selection()
    overlap_x, overlap_y = (ground['x']+air['x'])/2, (ground['y']+air['y'])/2
    page.mouse.click(overlap_x, overlap_y)
    first = selected_ids()
    page.keyboard.press('Tab')
    second = selected_ids()
    page.keyboard.press('Tab')
    third = selected_ids()
    check('地空重叠真实鼠标与Tab循环', len(first) == len(second) == len(third) == 1 and set(first+second) == {tank, heli} and first == third, {**overlap_detail, '首次点击': first, '首次Tab': second, '再次Tab': third}, '真实机身点击与真实键盘Tab；读取selectedIds')
    clear_selection()
    page.mouse.move(overlap_x-10, overlap_y-10)
    page.mouse.down()
    page.mouse.move(overlap_x+10, overlap_y+10, steps=6)
    page.mouse.up()
    exact_selection([tank, heli])
    check('地空重叠真实拖框同时选择', set(selected_ids()) == {tank, heli}, overlap_detail, '20CSS像素真实拖框；不通过卡片选择')
    for filter_id, ident in [('ground', tank), ('air', heli)]:
        page.locator(f'[data-action="filter"][data-value="{filter_id}"]').click()
        clear_selection()
        point = body(ident)
        page.mouse.click(point['x'], point['y'])
        exact_selection([ident])
        check(f'重叠后{filter_id}筛选真实机身点击', selected_ids() == [ident], {'筛选': filter_id, '单位ID': ident, '机身CSS位置': point, '投影距离CSS像素': separation}, '筛选按钮设前置条件；选择仅用真实机身点击')
    fresh_fixture(paused=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8810/')
    parser.add_argument('--channel', choices=['chrome', 'msedge', 'chromium'], default='chrome')
    parser.add_argument('--headless', action='store_true', help='仅作无窗口冒烟，不记作前台验收')
    parser.add_argument('--output', type=Path, help='可选JSON回执；由负责运行者登记该输出路径')
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.port != 8810:
        parser.error('本脚本仅连接127.0.0.1或localhost的8810候选服务')
    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    errors: list[str] = []
    identity = {}
    started = time.time()

    def check(name: str, condition: bool, detail=None, source='界面控件与公开规则快照'):
        view = page.evaluate('''() => {const s=window.scavenger.interaction.getState();return{focus:s.focus,viewWidth:s.viewWidth,yaw:s.yaw,filter:s.filter};}''')
        results.append({'项目': name, '通过': bool(condition), '来源': source, '视角': view, '详情': detail})
        if not condition:
            raise AssertionError(f'{name}失败：{detail}')

    with sync_playwright() as p:
        options = {'headless': args.headless}
        if args.channel != 'chromium':
            options['channel'] = args.channel
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(args.url, wait_until='domcontentloaded')
            page.wait_for_function('window.scavenger?.ready && window.scavenger?.interaction', timeout=120000)
            identity = page.evaluate('''() => {const m=window.scavenger.manifest;return{revision:m.revision,sourceSha256:m.sourceSha256,version:m.version};}''')
            page.bring_to_front()
            page.evaluate("window.scavenger.reset({preset:'interaction',seed:1105})")
            page.wait_for_function("window.scavenger.sim.snapshot().preset==='interaction'")
            page.wait_for_function("document.querySelectorAll('.unit-label').length>=3")

            def evaluate(expression):
                return page.evaluate(f'() => {{ const a=window.scavenger; const s=a.sim.snapshot(); return ({expression}); }}')

            tank = evaluate("s.units.find(u=>u.kind==='tank').id")
            heli = evaluate("s.units.find(u=>u.kind==='heli').id")
            plane = evaluate("s.units.find(u=>u.kind==='plane').id")
            check('默认交互视野保持360米', evaluate('a.interaction.getState().viewWidth===360'))
            view = evaluate('a.interaction.setView({focus:{x:0,y:0,z:0},viewWidth:600,yaw:0})')
            check('固定检验镜头可复现', view['viewWidth'] == 600 and view['focus'] == {'x': 0, 'y': 0, 'z': 0} and view['yaw'] == 0, view)
            limited = evaluate('a.interaction.setView({focus:{x:1e6,y:1e6,z:-1e6},viewWidth:1e6,yaw:20})')
            check('检验镜头钳制合法范围', limited['viewWidth'] == 900 and limited['focus']['x'] == 300 and limited['focus']['z'] == -300 and -math.pi <= limited['yaw'] <= math.pi, limited)
            evaluate('a.interaction.setView({focus:{x:0,y:0,z:0},viewWidth:360,yaw:0})')
            check('初始武器不预选', evaluate("s.units.filter(u=>u.kind==='tank').every(u=>Object.values(u.weapons).every(w=>!w.enabled&&w.ammoId==null))"))
            check('地面警戒默认开启', evaluate("s.units.filter(u=>u.kind==='tank').every(u=>u.guardEnabled)"))

            verify_body_mouse(page, check)

            page.locator(f'.unit-card[data-unit="{tank}"]').click()
            page.wait_for_function('(id)=>window.scavenger.interaction.getState().selectedIds.includes(id)', arg=tank)
            check('卡片选择与界面一致', page.locator(f'.unit-card[data-unit="{tank}"]').get_attribute('aria-pressed') == 'true')
            page.locator('[data-action="focus"]').click()
            check('聚焦不自动跟随', evaluate('a.interaction.getState().followId===null'))
            page.locator('[data-action="ammo"][data-ammo="ap"]').click()
            page.wait_for_function('(id)=>window.scavenger.sim.snapshot().units.find(u=>u.id===id).weapons.main.ammoId==="ap"', arg=tank)
            check('选弹不自动启用', evaluate("!s.units.find(u=>u.kind==='tank').weapons.main.enabled"))
            page.locator('[data-action="enable"][data-weapon="main"]').click()
            page.locator('[data-action="ammo"][data-ammo="ball"]').click()
            page.locator('[data-action="enable"][data-weapon="m2"]').click()
            check('主炮与M2可同时启用', evaluate("s.units.find(u=>u.kind==='tank').weapons.main.enabled&&s.units.find(u=>u.kind==='tank').weapons.m2.enabled"))
            page.locator('[data-action="enable"][data-weapon="m2"]').click()
            check('M2禁用不影响主炮', evaluate("s.units.find(u=>u.kind==='tank').weapons.main.enabled&&!s.units.find(u=>u.kind==='tank').weapons.m2.enabled"))

            page.locator('[data-action="attack-ground"]').click()
            check('攻击地面进入落点模式', evaluate("a.interaction.getState().commandMode==='attackGround'"))
            page.keyboard.press('Escape')
            check('界面获焦时Esc仍能取消', evaluate('a.interaction.getState().commandMode===null'))
            before = evaluate('a.interaction.getState().focus')
            page.locator('[data-action="preset"]').focus()
            page.keyboard.down('KeyW')
            page.wait_for_timeout(150)
            page.keyboard.up('KeyW')
            after = evaluate('a.interaction.getState().focus')
            check('界面获焦不触发平移', math.dist(list(before.values()), list(after.values())) < 0.01)

            page.locator('[data-action="filter"][data-value="air"]').click()
            check('空中筛选移除地面选择', evaluate('a.interaction.getState().selectedIds.length===0'))
            check('筛选同步卡片', page.locator(f'.unit-card[data-unit="{tank}"]').is_disabled())
            page.locator(f'.unit-card[data-unit="{heli}"]').click()
            page.locator('[data-action="follow"]').click()
            page.wait_for_function('(id)=>window.scavenger.interaction.getState().followId===id', arg=heli)
            check('跟随独立开启', evaluate('a.interaction.getState().followId!==null'))
            canvas = page.locator('#viewport')
            box = canvas.bounding_box()
            page.mouse.move(box['x']+box['width']*.5, box['y']+box['height']*.45)
            page.mouse.wheel(0, -80)
            check('缩放保留跟随', evaluate('a.interaction.getState().followId!==null'))
            canvas.focus()
            page.keyboard.down('KeyQ')
            page.wait_for_timeout(100)
            page.keyboard.up('KeyQ')
            check('旋转保留跟随', evaluate('a.interaction.getState().followId!==null'))
            page.keyboard.down('KeyD')
            page.wait_for_function('window.scavenger.interaction.getState().followId===null')
            page.keyboard.up('KeyD')
            check('手动平移立即退出跟随', evaluate('a.interaction.getState().followId===null'))

            page.locator('[data-action="filter"][data-value="all"]').click()
            page.locator(f'.unit-card[data-unit="{plane}"]').click()
            check('固定翼卡片没有武器控件', page.locator('[data-action="ammo"]').count() == 0)
            page.locator('[data-action="focus"]').click()
            page.locator(f'.unit-card[data-unit="{tank}"]').click()
            page.locator('[data-action="focus"]').click()
            # 用同一平地世界点检查鼠标锚点，不以视野宽度相同冒充位置稳定。
            anchor = evaluate("({x:a.interaction.getState().focus.x+10,y:0,z:a.interaction.getState().focus.z+8})")
            project_js = """point => {const a=window.scavenger,c=a.camera,r=document.querySelector('#viewport').getBoundingClientRect();const v=new c.position.constructor(point.x,point.y,point.z).project(c);return{x:(v.x+1)*r.width/2+r.left,y:(1-v.y)*r.height/2+r.top};}"""
            screen = page.evaluate(project_js, anchor)
            for _ in range(10):
                page.mouse.move(screen['x'], screen['y'])
                page.mouse.wheel(0, 80)
                page.wait_for_timeout(25)
                page.mouse.wheel(0, -80)
                page.wait_for_timeout(25)
            end = page.evaluate(project_js, anchor)
            error = math.hypot(end['x']-screen['x'], end['y']-screen['y'])
            check('滚轮往返10次静止地面锚点偏差', error <= 8, {'CSS像素偏差': error, '上限': 8})

            old_run = evaluate('s.runId')
            page.locator('[data-action="reset"]').click()
            page.wait_for_function('(old)=>window.scavenger.sim.snapshot().runId!==old', arg=old_run)
            check('重置创建新本轮身份', evaluate('s.runId') != old_run)
            page.wait_for_function('window.scavenger.interaction.getState().selectedIds.length===0')
            check('重置清空选择和跟随', evaluate('a.interaction.getState().selectedIds.length===0&&a.interaction.getState().followId===null'))
            check('重置后不沿用选弹', evaluate("s.units.filter(u=>u.kind==='tank').every(u=>Object.values(u.weapons).every(w=>!w.enabled&&w.ammoId==null))"))
            check('浏览器无脚本异常', not errors, errors)
        except Exception as exc:
            results.append({'项目': '执行异常', '通过': False, '详情': str(exc)})
        finally:
            version = browser.version
            browser.close()

    report = {'范围': 'A线交互回归；机身点框选、跟随运动点击和命令构成的地空重叠单列；不替代人工验收或性能采样', '地址': args.url,
              '候选身份': identity, '验证脚本SHA256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              '浏览器': args.channel, '版本': version, '无窗口': args.headless,
              '用时秒': round(time.time()-started, 3), '结果': results,
              '全部通过': bool(results) and all(item['通过'] for item in results)}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n', encoding='utf-8')
    return 0 if report['全部通过'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
