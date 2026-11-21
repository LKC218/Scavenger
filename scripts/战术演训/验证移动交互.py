"""在8811串行验证真实键鼠移动与镜头回归，不启停服务、不代替人工验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from 日常留存 import compact, output_directory, record_failure, write_summary

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "local-data/战术演训/G01移动指挥闭环/V001-01a1ac6a"
URL = "http://127.0.0.1:8811/"


def run(channel, directory, headed, routine=False):
    directory = output_directory(directory, OUTPUT, routine)
    try:
        run_checks(channel, directory, headed, routine)
    except Exception as error:
        if routine:
            record_failure(directory, {}, error=error)
            target = directory / "交互验证.json"
            if not target.exists():
                write_summary(target, {"channel": channel, "passed": False, "failure": repr(error), "checks": []})
        raise


def run_checks(channel, directory, headed, routine):
    with urlopen(URL + "api/status", timeout=5) as response:
        identity = json.load(response)
    if identity.get("app") != "scavenger-airbase-g01-movement":
        raise RuntimeError("8811不是本轮演训候选")
    result = {"passed": False, "channel": channel, "headless": not headed, "identity": identity, "started_epoch": time.time(), "checks": [], "errors": [], "manual_acceptance": "待工程师人工测试", "scope": "Airbase真实键鼠与公开命令状态回归；无窗口不代表正式前台性能"}
    target = directory / "交互验证.json"

    def save():
        if routine:
            write_summary(target, compact(result))
            return
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def check(name, passed, evidence=None):
        result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        if not routine:
            save()
        if not passed:
            raise AssertionError(name + "：" + repr(evidence))

    if not routine:
        save()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: result["errors"].append(str(error)))
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_function("window.range?.ready", timeout=180000)
            result["browser_version"] = browser.version
            check("实际Airbase流式地图", page.evaluate("range.externalMap?.streaming===true&&range.sim.map.id==='airbase-full'"))
            check("移动阶段没有启动防线", page.evaluate("!range.defense&&range.sim.shots===0"))
            if not page.evaluate("range.sim.paused"):
                page.locator("#pause").click()
            page.wait_for_function("range.sim.paused")
            paused_state = page.evaluate("({time:range.sim.time,units:range.sim.units.map(u=>({id:u.id,x:u.x,z:u.z}))})")
            tank = page.evaluate("range.sim.units.find(u=>u.kind==='tank').id")

            def settle():
                page.evaluate("()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")

            def select(ident):
                page.locator('[data-layer="all"]').click()
                card = page.locator(f'#roster [data-unit="{ident}"]')
                if card.count():
                    card.click()
                else:
                    raise AssertionError("当前页缺少测试单位卡：" + ident)
                page.locator("#focus").click()
                page.wait_for_function("!range.view.centerTarget", timeout=15000)
                settle()

            def job(ident):
                return page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {job:u.job,queue:u.queue,state:u.state}}", ident)

            def points(ident):
                return page.evaluate("""id=>{
                    const u=range.sim.units.find(u=>u.id===id),a=[];
                    const field=document.querySelector('#battlefield').getBoundingClientRect();
                    for(const [dx,dz] of [[32,24],[48,40],[-30,40],[55,-20],[-40,-24],[20,-45],[70,20],[-60,20],[28,-32],[-20,55],[65,-35],[-55,45]]){
                        const x=u.x+dx,z=u.z+dz,y=range.sim.map.heightAt(x,z);
                        if(!Number.isFinite(y))continue;
                        const point={x,y,z},plan=range.sim.planCommand([id],{type:'move',point});
                        const q=range.view.project(point);
                        const hit=document.elementFromPoint(field.left+q.x,field.top+q.y);
                        if(plan[0]?.valid&&q.x>35&&q.y>55&&q.x<range.view.width-35&&q.y<range.view.height-35&&!hit?.closest('button,a,input,select,textarea,summary'))a.push({point,screen:q});
                    }
                    return a.slice(0,4);
                }""", ident)

            def screen(point):
                local = page.evaluate("p=>range.view.project(p)", point)
                field = page.locator("#battlefield").bounding_box()
                return field["x"] + local["x"], field["y"] + local["y"]

            def right(point, shift=False, facing=False):
                x, y = screen(point)
                if shift:
                    page.keyboard.down("Shift")
                page.mouse.move(x, y)
                page.mouse.down(button="right")
                if facing:
                    page.mouse.move(x + 35, y + 12, steps=5)
                page.mouse.up(button="right")
                if shift:
                    page.keyboard.up("Shift")
                settle()

            select(tank)
            page.locator("#stop").click()
            candidates = points(tank)
            check("实际地形提供至少三个可达测试点", len(candidates) >= 3, candidates)
            p1, p2, p3 = [p["point"] for p in candidates[:3]]
            before = job(tank)
            x, y = screen(p1)
            page.mouse.move(x, y)
            page.mouse.down(button="right")
            settle()
            check("右键按住仅预览不提交", job(tank) == before and page.evaluate("range.commander.drag?.button===2"))
            page.keyboard.press("Escape")
            page.mouse.up(button="right")
            check("Esc取消预览保留既有任务", job(tank) == before)
            right(p1, facing=True)
            current = job(tank)
            check("拖朝向松开提交", current["job"] and current["job"]["type"] == "move" and isinstance(current["job"].get("facing"), (int, float)), current)
            page.locator("#stop").click()
            for point in [p1, p2, p3]:
                right(point, shift=True)
            current = job(tank)
            check("Shift连续三个移动点", current["job"] and len(current["queue"]) == 2, current)
            page.wait_for_function("(()=>{const t=document.querySelector('#command-list')?.textContent??'';return t.includes('当前')&&t.includes('后续1')&&t.includes('后续2')})()", timeout=5000)
            check("队列持续显示当前及后续顺序", all(text in page.locator("#command-list").inner_text() for text in ["当前", "后续1", "后续2"]))
            right(p2)
            current = job(tank)
            check("普通命令替换并清队列", current["job"] and len(current["queue"]) == 0, current)
            check("暂停期间只排令不推进时间与位置", page.evaluate("v=>range.sim.time===v.time&&v.units.every(p=>{const u=range.sim.units.find(u=>u.id===p.id);return u.x===p.x&&u.z===p.z})", paused_state))
            start_position = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {x:u.x,z:u.z}}", tank)
            page.locator("#pause").click()
            page.wait_for_function("v=>{const u=range.sim.units.find(u=>u.id===v.id);return Math.hypot(u.x-v.x,u.z-v.z)>2}", arg={"id": tank, **start_position}, timeout=45000)
            moved = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {x:u.x,z:u.z,state:u.state}}", tank)
            check("真实地图移动推进", ((moved["x"] - start_position["x"]) ** 2 + (moved["z"] - start_position["z"]) ** 2) ** .5 > 2, moved)
            page.wait_for_function("id=>!range.sim.units.find(u=>u.id===id).job", arg=tank, timeout=45000)
            arrived = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {x:u.x,z:u.z,state:u.state}}", tank)
            check("真实地图移动到达", ((arrived["x"] - p2["x"]) ** 2 + (arrived["z"] - p2["z"]) ** 2) ** .5 < 4, arrived)
            page.locator("#pause").click()
            page.locator("#stop").click()
            current = job(tank)
            check("停止清空地面移动", current["job"] is None and not current["queue"], current)
            page.wait_for_function("document.querySelector('#command-list')?.textContent.includes('队列已清空')", timeout=5000)
            check("停止后持续反馈清队列", "队列已清空" in page.locator("#command-list").inner_text())
            invalid = page.evaluate("""id=>{
                const b=range.sim.map.bounds,count=range.sim.command([id],{type:'move',point:{x:b.maxX+200,y:0,z:b.maxZ+200}},false);
                return {count,rejected:range.sim.lastCommandResult?.rejected,rows:range.sim.lastCommandResult?.rows};
            }""", tank)
            check("公开命令拒绝地图外终点", invalid["count"] == 0 and invalid["rejected"] == 1, invalid)
            select(tank)
            # 使用实际机身投影点选；卡片只用于公开聚焦前置设置。
            body = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return range.view.projectUnit?.(id)??range.view.project({x:u.x,y:u.y+2,z:u.z});}", tank)
            field = page.locator("#battlefield").bounding_box()
            bx, by = field["x"] + body["x"], field["y"] + body["y"]
            page.mouse.click(bx, by)
            check("真实机身点选", page.evaluate("id=>range.commander.selected.has(id)", tank), body)
            page.mouse.move(bx - 12, by - 12)
            page.mouse.down()
            page.mouse.move(bx + 12, by + 12, steps=5)
            page.mouse.up()
            check("真实机身框选", page.evaluate("id=>range.commander.selected.has(id)", tank))
            page.locator('[data-layer="ground"]').click()
            check("地面筛选一致", page.evaluate("range.commander.layerFilter==='ground'&&[...range.commander.selected].every(id=>range.sim.units.find(u=>u.id===id).kind==='tank')"))
            page.locator('[data-layer="air"]').click()
            check("空中筛选生效", page.evaluate("range.commander.layerFilter==='air'"))
            page.locator('[data-layer="all"]').click()
            page.keyboard.press("3")
            check("全部组包含三类单位", page.evaluate("new Set(range.sim.units.filter(u=>range.commander.selected.has(u.id)).map(u=>u.kind)).size===3"))
            right(p1)
            mixed = page.evaluate("range.sim.units.filter(u=>range.commander.selected.has(u.id)).map(u=>({id:u.id,kind:u.kind,job:u.job,queue:u.queue,state:u.state}))")
            check("混选三类接收移动", all(any(u["kind"] == kind and u["job"] and u["job"]["type"] == "move" for u in mixed) for kind in ["tank", "heli", "plane"]), mixed)
            if not routine:
                page.screenshot(path=str(directory / "移动与队列界面.png"))
            page.locator("#stop").click()
            stopped = page.evaluate("range.sim.units.filter(u=>range.commander.selected.has(u.id)).map(u=>({kind:u.kind,job:u.job,queue:u.queue,state:u.state}))")
            check("混选停止清队列", all(not u["job"] and not u["queue"] for u in stopped), stopped)
            page.wait_for_function("(()=>{const t=document.querySelector('#command-list')?.textContent??'';return t.includes('盘旋待命')&&t.includes('悬停待命')&&t.includes('队列已清空')})()", timeout=5000)
            check("停止反馈区分停车悬停盘旋", all(text in page.locator("#command-list").inner_text() for text in ["盘旋待命", "悬停待命", "队列已清空"]))
            plane = page.evaluate("range.sim.units.find(u=>u.kind==='plane').id")
            select(plane)
            before = job(plane)
            page.locator("#follow").click()
            check("跟随按钮不穿透下令", job(plane) == before)
            settle()
            plane_before = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {x:u.x,z:u.z,screen:range.view.projectUnit(id)}}", plane)
            if page.evaluate("range.sim.paused"):
                page.locator("#pause").click()
            initial = page.evaluate("range.sim.time")
            page.wait_for_function("t=>range.sim.time>t+.5", arg=initial, timeout=10000)
            plane_after = page.evaluate("id=>{const u=range.sim.units.find(u=>u.id===id);return {x:u.x,z:u.z,screen:range.view.projectUnit(id),following:range.commander.following,moving:u.moving}}", plane)
            distance = ((plane_after["x"]-plane_before["x"])**2+(plane_after["z"]-plane_before["z"])**2)**.5
            screen_distance = ((plane_after["screen"]["x"]-plane_before["screen"]["x"])**2+(plane_after["screen"]["y"]-plane_before["screen"]["y"])**2)**.5
            check("固定翼真实飞行且镜头持续跟随", plane_after["following"] == plane and plane_after["moving"] and distance > 10 and screen_distance < 2, {"before":plane_before,"after":plane_after,"world_distance":distance,"screen_distance":screen_distance})
            page.keyboard.down("d")
            page.wait_for_function("!range.commander.following", timeout=5000)
            page.keyboard.up("d")
            check("手动平移退出跟随", not page.evaluate("Boolean(range.commander.following)"))
            angle = page.evaluate("range.view.angle")
            page.keyboard.down("q")
            page.wait_for_function("a=>Math.abs(range.view.angle-a)>.03", arg=angle, timeout=5000)
            page.keyboard.up("q")
            check("Q旋转没有触发攻击模式", page.evaluate("!range.commander.mode&&range.sim.shots===0"))
            if not page.evaluate("range.sim.paused"):
                page.locator("#pause").click()
            select(plane)
            page.evaluate("range.commander.clearTransient()")
            local = page.evaluate("({x:range.view.width*.4,y:range.view.height*.65})")
            anchor_before = page.evaluate("p=>range.view.ground(p.x,p.y)", local)
            check("缩放锚点使用实际地图支撑", anchor_before is not None, anchor_before)
            field = page.locator("#battlefield").bounding_box()
            page.mouse.move(field["x"] + local["x"], field["y"] + local["y"])
            old_span = page.evaluate("range.view.span")
            page.mouse.wheel(0, -120)
            page.wait_for_function("s=>range.view.span<s*.99&&Math.abs(range.view.span-range.view.targetSpan)<.02", arg=old_span, timeout=10000)
            anchor_after = page.evaluate("p=>range.view.ground(p.x,p.y)", local)
            anchor_error = ((anchor_after["x"]-anchor_before["x"])**2+(anchor_after["z"]-anchor_before["z"])**2)**.5
            check("真实滚轮保持鼠标地面锚点", anchor_error < 1, {"before":anchor_before,"after":anchor_after,"error_m":anchor_error})
            old_center = page.evaluate("({x:range.view.center.x,z:range.view.center.z,angle:range.view.angle})")
            page.mouse.down(button="middle")
            page.mouse.move(field["x"] + local["x"] + 40, field["y"] + local["y"] + 20, steps=5)
            page.mouse.up(button="middle")
            new_center = page.evaluate("({x:range.view.center.x,z:range.view.center.z,angle:range.view.angle})")
            check("中键拖动平移且不旋转", abs(new_center["angle"]-old_center["angle"]) < .001 and ((new_center["x"]-old_center["x"])**2+(new_center["z"]-old_center["z"])**2)**.5 > 1, {"before":old_center,"after":new_center})
            for kind in ["heli", "plane"]:
                ident = page.evaluate("kind=>range.sim.units.find(u=>u.kind===kind).id", kind)
                select(ident)
                body = page.evaluate("id=>range.view.projectUnit(id)", ident)
                field = page.locator("#battlefield").bounding_box()
                bx, by = field["x"] + body["x"], field["y"] + body["y"]
                unobstructed = page.evaluate("p=>!document.elementFromPoint(p.x,p.y)?.closest('button,a,input,select,textarea,summary')", {"x":bx,"y":by})
                check(kind + "机身输入位置不被界面截获", unobstructed and body["visible"], body)
                page.mouse.click(bx, by)
                check(kind + "真实机身点选", page.evaluate("id=>range.commander.selected.has(id)", ident))
                page.mouse.move(bx - 10, by - 10)
                page.mouse.down()
                page.mouse.move(bx + 10, by + 10, steps=5)
                page.mouse.up()
                check(kind + "真实机身框选", page.evaluate("id=>range.commander.selected.has(id)", ident))
            result["final_state"] = page.evaluate("({units:range.sim.units.length,shots:range.sim.shots,map:range.sim.map.id,following:range.commander.following,perf:range.perf()})")
            check("页面无运行异常", not result["errors"], result["errors"])
            if not routine:
                page.screenshot(path=str(directory / "空中交互与高度.png"))
            result["passed"] = True
        except BaseException as error:
            result["failure"] = repr(error)
            if routine:
                record_failure(directory, result, error=error, page=page)
            elif not page.is_closed():
                result["failure_document"] = page.evaluate("({url:location.href,title:document.title,fatal:document.querySelector('#fatal')?.textContent})")
                page.screenshot(path=str(directory / "失败.png"))
            raise
        finally:
            result["finished_epoch"] = time.time()
            result["test_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            save()
            browser.close()
    print(json.dumps({"channel": channel, "passed": result["passed"], "checks": len(result["checks"]), "output": target.relative_to(ROOT).as_posix()}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=["chrome", "msedge"], default="msedge")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--headed", action="store_true", help="显式允许可见窗口；默认无窗口技术回归")
    parser.add_argument("--routine", action="store_true", help="日常回归仅留摘要，失败保存截图和日志")
    args = parser.parse_args()
    run(args.channel, args.output_dir, args.headed, args.routine)
