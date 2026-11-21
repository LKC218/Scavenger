"""串行验证8812高度与镜头候选；不启停服务，不修改单位或地图，不替代人工验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from 日常留存 import compact, output_directory, record_failure, routine_channel


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "local-data/战术演训/G01高度与镜头调整/V002-01a1ac6a"
APP = "scavenger-airbase-g01-altitude-camera"
TEST_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def identity(url, expected_sha):
    with urlopen(url + "api/status", timeout=10) as response:
        value = json.load(response)
    if value.get("app") != APP or value.get("port") != 8812:
        raise RuntimeError("目标不是8812高度与镜头候选，未操作其他服务")
    if value.get("source_sha256") != expected_sha:
        raise RuntimeError("运行源码与指定冻结摘要不一致，拒绝将旧修订作为新结果")
    return value


def trace_metrics(samples):
    if len(samples) < 2:
        raise ValueError("高度样本不足")
    rates, climbs, descents, jumps, pending_drift, pending_vertical = [], [], [], [], [], []
    for a, b in zip(samples, samples[1:]):
        dt = b["time"] - a["time"]
        if dt < 0:
            raise ValueError("采样期间模拟时间重置")
        if dt <= 0:
            continue
        dy = b["y"] - a["y"]
        rates.append(abs(dy) / dt)
        climbs.append(max(0, dy / dt))
        descents.append(max(0, -dy / dt))
        jumps.append(abs(dy))
        if a["altitude"]["status"] == b["altitude"]["status"] == "pending":
            pending_drift.append(math.hypot(b["x"] - a["x"], b["z"] - a["z"]))
            pending_vertical.append(abs(dy))
    return {
        "samples": len(samples),
        "sim_seconds": samples[-1]["time"] - samples[0]["time"],
        "displacement": math.hypot(samples[-1]["x"] - samples[0]["x"], samples[-1]["z"] - samples[0]["z"]),
        "min_y": min(s["y"] for s in samples),
        "max_y": max(s["y"] for s in samples),
        "max_vertical_rate": max(rates, default=0),
        "max_climb_rate": max(climbs, default=0),
        "max_descent_rate": max(descents, default=0),
        "max_sample_jump": max(jumps, default=0),
        "max_pending_horizontal_step": max(pending_drift, default=0),
        "max_pending_vertical_step": max(pending_vertical, default=0),
        "pending_samples": sum(s["altitude"]["status"] == "pending" for s in samples),
        "states": sorted({s["altitude"]["status"] for s in samples}),
    }


def local_cruise_ready(sample):
    altitude = sample["altitude"]
    base = altitude.get("baseGround")
    return sample["jobStatus"] is None and isinstance(base, (int, float)) and math.isfinite(base) and abs(sample["y"] - base - 30) <= .05 and altitude.get("status") in ["cruise", "holding"]


def altitude_label_unobscured(sample):
    label = sample.get("label")
    if not label or label["hidden"] or not label["rect"]["visible"] or not label["text"].strip():
        return False
    box, field, viewport = label["rect"], sample["fieldRect"], sample["viewport"]
    if not (max(0, field["left"]) <= box["left"] < box["right"] <= min(viewport["width"], field["right"]) and max(0, field["top"]) <= box["top"] < box["bottom"] <= min(viewport["height"], field["bottom"])):
        return False
    return not any(other["rect"] and other["rect"]["visible"] and box["left"] < other["rect"]["right"] and box["right"] > other["rect"]["left"] and box["top"] < other["rect"]["bottom"] and box["bottom"] > other["rect"]["top"] for other in sample["overlayRects"])


SNAPSHOT = """id=>{
  const r=window.range,u=r.sim.units.find(u=>u.id===id),v=r.view,display=v.getAltitudeDisplay(id);
  const rig=v.units.get(id),links=v.altitudeLinks??[],link=links.find(row=>row.unitId===id);
  const label=rig?.altitudeLabel;
  const rectOf=node=>{if(!node)return null;const b=node.getBoundingClientRect(),css=getComputedStyle(node);return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,width:b.width,height:b.height,visible:!node.hidden&&css.display!=='none'&&css.visibility!=='hidden'&&Number(css.opacity)>0&&b.width>0&&b.height>0};};
  return {time:r.sim.time,x:u.x,y:u.y,z:u.z,state:u.state,job:u.job,queue:u.queue,
    altitude:structuredClone(u.altitudeState),display,link:link?structuredClone(link):null,
    body:v.projectUnit(id),ground:display.groundPoint?v.project(display.groundPoint):null,
    label:label?{hidden:label.hidden,text:label.textContent,height:label.dataset.height,status:label.dataset.status,runY:label.dataset.runY,groundY:label.dataset.groundY,rect:rectOf(label)}:null,
    fieldRect:rectOf(document.querySelector('#battlefield')),viewport:{width:innerWidth,height:innerHeight},
    overlayRects:['toast','mode-banner','pause-overlay'].map(id=>({id,rect:rectOf(document.getElementById(id))})),
    linePositions:Array.from(v.altitudeLine.geometry.attributes.position?.array??[]),
    camera:v.getCameraState(),width:v.width,height:v.height,following:r.commander.following};
}"""


CAPTURE = """async ({id,seconds,minSeconds=5})=>new Promise((resolve,reject)=>{
  const samples=[],r=range,start=r.sim.time,deadline=performance.now()+(seconds*4+20)*1000;
  let arrivedAt=null;
  function tick(){
    const u=r.sim.units.find(u=>u.id===id),v=r.view,d=v.getAltitudeDisplay(id);
    if(!u||!u.altitudeState){reject(new Error('高度公开状态丢失'));return;}
    const body=v.projectUnit(id),ground=d.groundPoint?v.project(d.groundPoint):null;
    samples.push({time:r.sim.time,wall:performance.now(),x:u.x,y:u.y,z:u.z,
      altitude:structuredClone(u.altitudeState),jobStatus:u.job?.status??null,jobPoint:u.job?.point?structuredClone(u.job.point):null,body,ground,
      width:v.width,height:v.height,following:r.commander.following,camera:v.getCameraState()});
    if(!u.job&&arrivedAt===null)arrivedAt=r.sim.time;
    const elapsed=r.sim.time-start;
    if(elapsed>=seconds||(elapsed>=minSeconds&&arrivedAt!==null&&r.sim.time-arrivedAt>=3))resolve(samples);
    else if(performance.now()>deadline)reject(new Error('采样未推进足够模拟时间；请检查暂停或页面后台状态'));
    else requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})"""


def run_channel(channel, directory, options):
    from playwright.sync_api import sync_playwright

    routine = getattr(options, "routine", False)
    if not routine:
        directory.mkdir(exist_ok=False)

    def record(path, value):
        if not routine:
            save(path, value)
    result = {"passed": False, "channel": channel, "headless": True, "started_epoch": time.time(),
              "manual_acceptance": "待工程师人工测试", "checks": [], "boundaries": [], "errors": [],
              "scope": "真实键鼠、公开只读状态和正常移动命令；未改单位位置或地图碰撞；技术通过不等同实景越障及人工全部通过",
              "identity": identity(options.url, options.expect_source_sha)}
    report = directory / "高度与镜头验证.json"

    def check(name, passed, evidence=None):
        result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        record(report, result)
        if not passed:
            raise AssertionError(name + "：" + repr(evidence))

    def boundary(name, reason, evidence=None):
        result["boundaries"].append({"name": name, "status": "未覆盖实景", "reason": reason, "evidence": evidence})
        record(report, result)

    record(report, result)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: result["errors"].append(str(error)))
        try:
            page.goto(options.url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_function("window.range?.ready", timeout=180000)
            result["browser_version"] = browser.version
            check("V002高度和镜头公开接口存在", page.evaluate("typeof range.view.getCameraState==='function'&&typeof range.view.getAltitudeDisplay==='function'&&range.sim.units.some(u=>u.kind==='heli'&&u.altitudeState)"))
            result["height_configuration"] = page.evaluate("async()=>JSON.parse(JSON.stringify((await import('/直升机高度.mjs')).HELICOPTER_HEIGHT))")
            height_config = result["height_configuration"]
            check("高度候选使用批准的局部巡航与升降限制", height_config["cruiseAGL"] == 30 and height_config["climbRate"] == 6 and height_config["descentRate"] == 3, height_config)

            def settle():
                page.evaluate("()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")

            def camera_settled():
                page.wait_for_function("!range.view.centerTarget&&!range.view.zoomAnchor&&Math.abs(range.view.span-range.view.targetSpan)<.02&&Math.abs(range.view.angle-range.view.targetAngle)<.001", timeout=20000)
                settle()

            def wheel(delta):
                before = page.evaluate("range.view.targetSpan")
                event = {"delta_y": delta, "before_target_span": before}
                result.setdefault("wheel_events", []).append(event)
                record(report, result)
                page.mouse.wheel(0, delta)
                # wheel返回时事件处理和相机更新未必已发生；不能用原本成立的收敛条件确认本次输入。
                page.wait_for_function("""({before,delta})=>{
                  const v=range.view,boundary=delta<0?v.getCameraState().minWidth*v.height/v.width:v.map.maxSpan;
                  return Math.abs(v.targetSpan-before)>1e-6||
                    (Math.abs(before-boundary)<1e-6&&Math.abs(v.targetSpan-boundary)<1e-6);
                }""", arg={"before": before, "delta": delta}, timeout=10000)
                event["acknowledged_target_span"] = page.evaluate("range.view.targetSpan")
                camera_settled()
                event["settled_view_width"] = page.evaluate("range.view.getCameraState().viewWidth")
                record(report, result)

            def paused(value):
                if page.evaluate("range.sim.paused") != value:
                    page.locator("#pause").click()
                page.wait_for_function("v=>range.sim.paused===v", arg=value)

            def select(ident):
                page.locator('[data-layer="all"]').click()
                page.locator(f'#roster [data-unit="{ident}"]').click()
                page.locator("#focus").click()
                camera_settled()

            def snap(ident):
                return page.evaluate(SNAPSHOT, ident)

            def near():
                box = page.locator("#battlefield").bounding_box()
                page.mouse.move(box["x"] + box["width"] * .5, box["y"] + box["height"] * .5)
                for _ in range(16):
                    if abs(page.evaluate("range.view.getCameraState().viewWidth") - 70) < .15:
                        break
                    wheel(-350)

            def frame_evidence(ident):
                s = snap(ident)
                b, g, w, h = s["body"], s["ground"], s["width"], s["height"]
                bounds = b.get("bounds")
                body_inside = b["visible"] and 3 < b["x"] < w - 3 and 3 < b["y"] < h - 3
                if bounds:
                    body_inside = body_inside and bounds["left"] >= 0 and bounds["right"] <= w and bounds["top"] >= 0 and bounds["bottom"] <= h
                ground_inside = bool(g and g["visible"] and 3 < g["x"] < w - 3 and 3 < g["y"] < h - 3)
                return body_inside and ground_inside, s

            def height_evidence(ident):
                s = snap(ident)
                d, a, link, label = s["display"], s["altitude"], s["link"], s["label"]
                valid = isinstance(a.get("baseGround"), (int, float)) and isinstance(d.get("height"), (int, float))
                if valid:
                    valid = abs(d["height"] - (s["y"] - a["baseGround"])) < .02 and abs(d["runPoint"]["y"] - s["y"]) < .02
                    valid = valid and abs(d["groundPoint"]["y"] - a["baseGround"]) < .02
                    valid = valid and bool(link) and abs(link["runPoint"]["y"] - s["y"]) < .02 and abs(link["groundPoint"]["y"] - a["baseGround"]) < .02
                    valid = valid and bool(label) and not label["hidden"] and abs(float(label["height"]) - d["height"]) < .11
                    line = s["linePositions"]
                    valid = valid and len(line) >= 6 and max(abs(line[i] - expected) for i, expected in enumerate([s["x"], s["y"], s["z"], s["x"], a["baseGround"], s["z"]])) < .02
                    valid = valid and abs(float(label["runY"]) - s["y"]) < .02 and abs(float(label["groundY"]) - a["baseGround"]) < .02
                return valid, s

            paused(True)
            initial = page.evaluate("({camera:range.view.getCameraState(),units:range.sim.units.map(u=>({id:u.id,kind:u.kind,x:u.x,y:u.y,z:u.z,altitude:u.altitudeState??null}))})")
            result["initial_state"] = initial
            check("默认中景与48度候选参数", abs(initial["camera"]["viewWidth"] - 220) < 1 and abs(initial["camera"]["tiltDegrees"] - 48) < .1, initial["camera"])
            heli = next(u["id"] for u in initial["units"] if u["kind"] == "heli")
            helis = [u for u in initial["units"] if u["kind"] == "heli"]
            check("直升机初始局部离地约30米", all(u["altitude"] and isinstance(u["altitude"].get("baseGround"), (int, float)) and abs(u["y"] - u["altitude"]["baseGround"] - 30) < .5 for u in helis), helis)
            select(heli)
            valid, evidence = height_evidence(heli)
            check("高度数值和连接线统一使用运行基准", valid, evidence)
            if not routine:
                page.screenshot(path=str(directory / "中景与局部离地高度.png"))

            near()
            check("滚轮可进入70米近景", abs(page.evaluate("range.view.getCameraState().viewWidth") - 70) < .2, page.evaluate("range.view.getCameraState()"))
            page.locator("#focus").click()
            camera_settled()
            valid, evidence = frame_evidence(heli)
            check("近景聚焦直升机机身与地面投影同框", valid, evidence)
            check("近景高度文字可见且不被提示条遮挡", altitude_label_unobscured(evidence), {"label": evidence["label"], "field_rect": evidence["fieldRect"], "viewport": evidence["viewport"], "overlays": evidence["overlayRects"]})
            if not routine:
                page.screenshot(path=str(directory / "近景聚焦空地同框.png"))
            before_job = page.evaluate("id=>structuredClone(range.sim.units.find(u=>u.id===id).job)", heli)
            page.locator("#follow").click()
            settle()
            valid, evidence = frame_evidence(heli)
            check("近景跟随直升机机身与地面投影同框", valid and evidence["following"] == heli, evidence)
            check("跟随按钮不穿透下令", page.evaluate("id=>range.sim.units.find(u=>u.id===id).job", heli) == before_job)
            if not routine:
                page.screenshot(path=str(directory / "近景跟随空地同框.png"))
            page.locator("#follow").click()

            anchor = page.evaluate("()=>{const v=range.view,s={x:v.width*.62,y:v.height*.57};return {screen:s,point:v.groundPoint(s.x,s.y)}}")
            check("滚轮锚点具有真实地形落点", bool(anchor["point"]) and all(math.isfinite(anchor["point"][axis]) for axis in ["x", "y", "z"]), anchor)
            box = page.locator("#battlefield").bounding_box()
            page.mouse.move(box["x"] + anchor["screen"]["x"], box["y"] + anchor["screen"]["y"])
            wheel(200)
            projected = page.evaluate("p=>range.view.project(p)", anchor["point"])
            drift = math.hypot(projected["x"] - anchor["screen"]["x"], projected["y"] - anchor["screen"]["y"])
            check("滚轮缩放保持鼠标地面锚点", drift < 4, {"drift_pixels": drift, "before": anchor, "after": projected})
            angle = page.evaluate("range.view.angle")
            page.locator("#battlefield").focus()
            page.keyboard.down("q")
            try:
                page.wait_for_function("a=>Math.abs(range.view.angle-a)>.08", arg=angle, timeout=10000)
            finally:
                page.keyboard.up("q")
            camera_settled()
            check("Q真实旋转并保持移动阶段", abs(page.evaluate("range.view.angle") - angle) > .08 and page.evaluate("!range.commander.mode&&range.sim.shots===0"))
            page.locator("#overview").click()
            camera_settled()
            overview = page.evaluate("range.view.getCameraState()")
            check("全场总览仍覆盖地图最大跨度", abs(overview["span"] - overview["maxSpan"]) < 1 and overview["viewWidth"] > 220, overview)
            if not routine:
                page.screenshot(path=str(directory / "全图总览.png"))

            page.locator("#reset").click()
            paused(True)
            select(heli)
            # 只读拾取用于找真实机身点击像素，不把投影中心一定命中当作前提。
            click = page.evaluate("""id=>{
              const v=range.view,p=v.projectUnit(id),b=p.bounds??{left:p.x-4,right:p.x+4,top:p.y-4,bottom:p.y+4},r=document.querySelector('#battlefield').getBoundingClientRect();
              for(const fx of [.5,.35,.65,.2,.8])for(const fy of [.5,.35,.65,.2,.8]){
                const x=b.left+(b.right-b.left)*fx,y=b.top+(b.bottom-b.top)*fy,e=document.elementFromPoint(r.left+x,r.top+y);
                if(e&&!e.closest('button,input,select,textarea')&&v.pickUnits(x,y).includes(id))return {x:r.left+x,y:r.top+y,local:{x,y}};
              }return null;
            }""", heli)
            check("存在可操作的真实直升机机身像素", bool(click), click)
            page.mouse.click(click["x"], click["y"])
            check("真实机身点选保持", page.evaluate("id=>range.commander.selected.has(id)", heli))
            center = page.evaluate("id=>range.view.projectUnit(id)", heli)
            box = page.locator("#battlefield").bounding_box()
            page.mouse.move(box["x"] + center["x"] - 8, box["y"] + center["y"] - 8)
            page.mouse.down()
            page.mouse.move(box["x"] + center["x"] + 8, box["y"] + center["y"] + 8, steps=5)
            page.mouse.up()
            check("真实机身框选保持", page.evaluate("id=>range.commander.selected.has(id)", heli))
            select(heli)
            destination = page.evaluate("""id=>{
              const r=range,u=r.sim.units.find(u=>u.id===id),v=r.view,rect=document.querySelector('#battlefield').getBoundingClientRect();
              for(const [dx,dz] of [[50,20],[-50,20],[20,-50],[-40,-30],[65,0]]){
                const p={x:u.x+dx,z:u.z+dz},h=r.sim.map.heightAt(p.x,p.z);if(!Number.isFinite(h))continue;p.y=h;
                const q=v.project(p),e=document.elementFromPoint(rect.left+q.x,rect.top+q.y);
                if(q.x>30&&q.y>70&&q.x<v.width-30&&q.y<v.height-30&&e&&!e.closest('button,input,select,textarea')&&r.sim.planCommand([id],{type:'move',point:p})[0]?.valid)return {point:p,screen:q};
              }return null;
            }""", heli)
            check("真实机场有正常移动样本", bool(destination), destination)
            box = page.locator("#battlefield").bounding_box()
            page.mouse.move(box["x"] + destination["screen"]["x"], box["y"] + destination["screen"]["y"])
            page.mouse.down(button="right")
            settle()
            check("高度候选右键预览不提交", page.evaluate("id=>!range.sim.units.find(u=>u.id===id).job", heli))
            page.mouse.up(button="right")
            check("真实右键提交移动", page.evaluate("id=>range.sim.units.find(u=>u.id===id).job?.type==='move'", heli))
            page.locator("#follow").click()
            controls_before = page.evaluate("range.sim.units.filter(u=>u.kind!=='heli').map(u=>({id:u.id,kind:u.kind,x:u.x,y:u.y,z:u.z}))")
            paused(False)
            samples = page.evaluate(CAPTURE, {"id": heli, "seconds": options.sample_seconds})
            record(directory / "正常移动高度逐帧.json", samples)
            metrics = trace_metrics(samples)
            check("直升机实际移动而非仅状态变化", metrics["displacement"] > 20, metrics)
            check("升降样本全部有限且没有高度突跳", all(math.isfinite(s[k]) for s in samples for k in ["x", "y", "z"]) and metrics["max_climb_rate"] <= height_config["climbRate"] + .25 and metrics["max_descent_rate"] <= height_config["descentRate"] + .25, metrics)
            check("正常移动不进入高度错误", "error" not in metrics["states"], metrics)
            tracked = [s for s in samples if s["following"] == heli]
            check("移动跟随持续保持真实机身和地面关联入画", len(tracked) == len(samples) and all(s["body"]["visible"] and 0 <= s["body"]["x"] <= s["width"] and 0 <= s["body"]["y"] <= s["height"] and s["ground"] and s["ground"]["visible"] and 0 <= s["ground"]["x"] <= s["width"] and 0 <= s["ground"]["y"] <= s["height"] for s in tracked), {"samples": len(tracked), "first": samples[0], "last": samples[-1]})
            controls_after = page.evaluate("range.sim.units.filter(u=>u.kind!=='heli').map(u=>({id:u.id,kind:u.kind,x:u.x,y:u.y,z:u.z}))")
            before_by_id = {u["id"]: u for u in controls_before}
            check("地面待命位置没有被直升机高度逻辑改变", all(math.dist([u["x"], u["y"], u["z"]], [before_by_id[u["id"]][k] for k in ["x", "y", "z"]]) < .05 for u in controls_after if u["kind"] == "tank"), controls_after)
            planes = [u for u in controls_after if u["kind"] == "plane"]
            check("固定翼继续真实飞行且高度保持原规则", bool(planes) and all(math.hypot(u["x"] - before_by_id[u["id"]]["x"], u["z"] - before_by_id[u["id"]]["z"]) > 2 and abs(u["y"] - before_by_id[u["id"]]["y"]) < .1 for u in planes), planes)
            paused(True)
            valid, evidence = height_evidence(heli)
            check("移动后高度数值和连接线仍同基准", valid, evidence)
            if not routine:
                page.screenshot(path=str(directory / "真实移动与高度关联.png"))
            page.locator("#stop").click()

            # 只读取实际已加载源碰撞和BAIP实例包络；不调用规划器内部scan或修改世界状态。
            obstacle = page.evaluate("""({id,config})=>{
              const r=range,u=r.sim.units.find(u=>u.id===id),map=r.sim.map,radius=180;
              const bounds={minX:u.x-radius,maxX:u.x+radius,minZ:u.z-radius,maxZ:u.z+radius},query=map.collisionsIn(bounds),shapes=[],seen=new Set();
              for(const s of query.state==='ready'?query.shapes:[])shapes.push({shape:s,source:'源碰撞'});
              for(const row of r.sim.helicopterHeight?.envelopes.pages.values()??[])if(row.status==='ready')for(const s of row.shapes??[])shapes.push({shape:s,source:'BAIP实例安全包络'});
              const candidates=[],observed=[];
              for(const {shape:s,source} of shapes){
                const b=s.bounds?Array.from(s.bounds):[s.x-s.w/2,s.baseY??0,s.z-s.d/2,s.x+s.w/2,(s.baseY??0)+s.h,s.z+s.d/2];
                if(!b.every(Number.isFinite)||seen.has(b.join(',')))continue;seen.add(b.join(','));
                const cx=(b[0]+b[3])/2,cz=(b[2]+b[5])/2,d=Math.hypot(cx-u.x,cz-u.z),size=Math.hypot(b[3]-b[0],b[5]-b[2]);
                if(d>radius||d<config.radius+size/2+8||size>90)continue;
                const base=map.heightAt(cx,cz),rise=b[4]+config.clearance-u.y;
                observed.push({id:s.id??null,source,bounds:b,distance:d,potentialRise:rise});
                if(rise<2||!Number.isFinite(base))continue;
                const tail=size/2+config.radius+40,dx=(cx-u.x)/d,dz=(cz-u.z)/d,point={x:cx+dx*tail,z:cz+dz*tail};
                point.y=map.heightAt(point.x,point.z);if(!Number.isFinite(point.y))continue;
                const plan=r.sim.planCommand([id],{type:'move',point})[0];if(!plan?.valid)continue;
                candidates.push({id:s.id??null,source,bounds:b,point,distance:d,routeLength:d+tail,potentialRise:rise});
              }
              candidates.sort((a,b)=>a.routeLength-b.routeLength);
              return {chosen:candidates[0]??null,queryState:query.state,queryBounds:bounds,observed:observed.sort((a,b)=>b.potentialRise-a.potentialRise).slice(0,20),candidateCount:candidates.length};
            }""", {"id": heli, "config": height_config})
            result["obstacle_search"] = obstacle
            if obstacle["chosen"]:
                chosen = obstacle["chosen"]
                # 此处为正常公开移动命令，目标来自真实障碍后的有效地形；真键鼠提交另有上一项回归。
                page.evaluate("p=>range.commander.issue({type:'move',point:p},false)", chosen["point"])
                check("真实障碍路线通过正常移动命令提交", page.evaluate("id=>range.sim.units.find(u=>u.id===id).job?.type==='move'", heli), chosen)
                if not routine:
                    page.screenshot(path=str(directory / "真实障碍路线开始.png"))
                paused(False)
                obstacle_samples = page.evaluate(CAPTURE, {"id": heli, "seconds": options.obstacle_seconds, "minSeconds": 8})
                record(directory / "真实障碍高度逐帧.json", obstacle_samples)
                obstacle_metrics = trace_metrics(obstacle_samples)
                peak = max(range(len(obstacle_samples)), key=lambda i: obstacle_samples[i]["y"])
                peak_y = obstacle_samples[peak]["y"]
                rose = peak_y - obstacle_samples[0]["y"] > 1
                descended = peak_y - min(s["y"] for s in obstacle_samples[peak:]) > 1
                check("真实障碍前后已观测平滑升高再降低", rose and descended, {"obstacle": chosen, "metrics": obstacle_metrics, "peak_sample": obstacle_samples[peak], "last_sample": obstacle_samples[-1]})
                b, radius = chosen["bounds"], height_config["radius"]
                crossings = [i for i, s in enumerate(obstacle_samples) if b[0] - radius <= s["x"] <= b[3] + radius and b[2] - radius <= s["z"] <= b[5] + radius]
                safe_y = b[4] + height_config["clearance"]
                pre_climb = bool(crossings) and any(s["y"] > obstacle_samples[0]["y"] + 1 for s in obstacle_samples[:crossings[0]])
                check("实际进入障碍包络前已爬升并保留安全余量", pre_climb and all(obstacle_samples[i]["y"] >= safe_y - .3 for i in crossings), {"obstacle": chosen, "crossing_samples": len(crossings), "required_y": safe_y, "first_crossing": obstacle_samples[crossings[0]] if crossings else None})
                check("真实越障升降没有突跳", obstacle_metrics["max_climb_rate"] <= height_config["climbRate"] + .25 and obstacle_metrics["max_descent_rate"] <= height_config["descentRate"] + .25, obstacle_metrics)
                check("越障未擅自更换正常移动终点", all(not s["jobPoint"] or math.hypot(s["jobPoint"]["x"] - chosen["point"]["x"], s["jobPoint"]["z"] - chosen["point"]["z"]) < .01 for s in obstacle_samples), chosen)
                last = obstacle_samples[-1]
                check("越障完成后恢复局部30米巡航", local_cruise_ready(last), last)
                stable_samples = page.evaluate(CAPTURE, {"id": heli, "seconds": 4, "minSeconds": 4})
                record(directory / "到达后高度稳定逐帧.json", stable_samples)
                stable_metrics = trace_metrics(stable_samples)
                check("恢复后继续四秒模拟时间保持精确巡航高度", stable_metrics["sim_seconds"] >= 3.8 and all(local_cruise_ready(s) for s in stable_samples), {"metrics": stable_metrics, "first_sample": stable_samples[0], "last_sample": stable_samples[-1], "tolerance_m": .05})
                result["arrival_stability"] = stable_metrics
                paused(True)
                valid, evidence = height_evidence(heli)
                check("真实越障后高度线仍使用同一运行基准", valid, evidence)
                if not routine:
                    page.screenshot(path=str(directory / "真实障碍路线完成.png"))
                result["real_obstacle_coverage"] = {"status": "实景已观测", "source": chosen["source"], "metrics": obstacle_metrics}
                page.locator("#stop").click()
                pending_metrics = [metrics, obstacle_metrics]
            else:
                boundary("真实机场越障升高再降低", "已加载的实际机场邻域未找到足够高、可正常穿越且终点有有效地面的障碍路线。不得用平地移动或伪造位置作为越障通过；需规则契约补充。", obstacle)
                result["real_obstacle_coverage"] = {"status": "未覆盖实景"}
                pending_metrics = [metrics]
            observed_pending = [item for item in pending_metrics if item["pending_samples"]]
            if observed_pending:
                check("实景已观测等待期间保持悬停", all(item["max_pending_horizontal_step"] < .15 and item["max_pending_vertical_step"] < .05 for item in observed_pending), observed_pending)
            else:
                boundary("前方未知数据悬停", "本次正常移动样本未遇到前方未知数据；应由规则契约补充，不伪造地图数据。")
            result["final_identity"] = identity(options.url, options.expect_source_sha)
            check("运行期间冻结源码身份未变", result["final_identity"]["source_sha256"] == result["identity"]["source_sha256"])
            check("页面没有运行异常", not result["errors"], result["errors"])
            result["passed"] = True
        except BaseException as error:
            result["failure"] = repr(error)
            if routine:
                record_failure(directory, result, error=error, page=page)
                if isinstance(error, Exception):
                    error.routine_result = result
            elif not page.is_closed():
                try:
                    result["failure_document"] = page.evaluate("({url:location.href,title:document.title,fatal:document.querySelector('#fatal')?.textContent})")
                    page.screenshot(path=str(directory / "失败.png"))
                except Exception as screenshot_error:
                    result["failure_capture_error"] = repr(screenshot_error)
            raise
        finally:
            result["finished_epoch"] = time.time()
            result["test_sha256"] = TEST_SHA256
            record(report, result)
            browser.close()
    return result


def self_check():
    samples = [{"time": i / 10, "x": i, "y": 30 + i / 10, "z": 0, "altitude": {"status": "climbing"}} for i in range(5)]
    metrics = trace_metrics(samples)
    assert metrics["displacement"] == 4 and abs(metrics["max_vertical_rate"] - 1) < 1e-9
    stopped = [{"time": i / 10, "x": 0, "y": 30, "z": 0, "altitude": {"status": "pending"}} for i in range(5)]
    assert trace_metrics(stopped)["max_pending_horizontal_step"] == 0
    assert not local_cruise_ready({"jobStatus": None, "y": 30.248, "altitude": {"baseGround": 0, "status": "descending"}})
    assert not local_cruise_ready({"jobStatus": None, "y": 30.01, "altitude": {"baseGround": 0, "status": "descending"}})
    assert local_cruise_ready({"jobStatus": None, "y": 30.04, "altitude": {"baseGround": 0, "status": "cruise"}})
    display = {"label": {"hidden": False, "text": "离地30米", "rect": {"left": 80, "right": 100, "top": 80, "bottom": 100, "visible": True}}, "fieldRect": {"left": 0, "right": 200, "top": 0, "bottom": 200}, "viewport": {"width": 200, "height": 200}, "overlayRects": [{"id": "toast", "rect": {"left": 90, "right": 120, "top": 90, "bottom": 120, "visible": True}}]}
    assert not altitude_label_unobscured(display)
    display["overlayRects"][0]["rect"]["left"] = 110
    assert altitude_label_unobscured(display)
    display["label"]["rect"]["left"] = -1
    assert not altitude_label_unobscured(display)
    print("离线采样统计自检通过；未启动浏览器或服务。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8812/")
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expect-source-sha", help="实际冻结服务的完整source_sha256，必填以防复测旧修订")
    parser.add_argument("--sample-seconds", type=float, default=30)
    parser.add_argument("--obstacle-seconds", type=float, default=60, help="真实障碍路线最大模拟采样时长")
    parser.add_argument("--self-check", action="store_true", help="仅运行离线统计自检，不打开浏览器")
    parser.add_argument("--routine", action="store_true", help="日常回归通过只留汇总；失败保留截图和日志")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        parsed = urlparse(args.url)
        if parsed.scheme != "http" or parsed.hostname not in ["127.0.0.1", "localhost"] or parsed.port != 8812:
            parser.error("仅允许8812回环候选")
        if not args.output_dir or not args.expect_source_sha or len(args.expect_source_sha) != 64:
            parser.error("必须提供V002独立--output-dir及完整--expect-source-sha")
        if not 5 <= args.sample_seconds <= 120 or not 8 <= args.obstacle_seconds <= 180:
            parser.error("普通采样须为5至120秒，障碍采样须为8至180秒")
        args.url = args.url.rstrip("/") + "/"
        output = output_directory(args.output_dir, OUTPUT, args.routine)
        summary = {"passed": False, "started_epoch": time.time(), "channels": [], "source_sha256": args.expect_source_sha}
        try:
            for channel in (["chrome", "msedge"] if args.channel == "both" else [args.channel]):
                if args.routine:
                    summary["channels"].append(compact(routine_channel(run_channel, channel, output / channel, args)))
                else:
                    item = run_channel(channel, output / channel, args)
                    summary["channels"].append({"channel": channel, "passed": item["passed"], "checks": len(item["checks"]), "boundaries": item["boundaries"]})
            summary["passed"] = True
        except Exception as error:
            if args.routine:
                item = getattr(error, 'routine_result', {"channel": channel, "passed": False, "failure": repr(error)})
                summary["channels"].append(compact(item))
            raise
        finally:
            summary["finished_epoch"] = time.time()
            save(output / "串行专项汇总.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
