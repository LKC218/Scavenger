"""对8813串行运行逐帧标签和地图淡变回归；默认无窗口，不启停服务、不修改世界数据。"""
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
OUTPUT = ROOT / "local-data/战术演训/G01标签与景别过渡/V003-01a1ac6a"
APP = "scavenger-airbase-g01-label-transition"
TEST_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_identity(url, sha):
    with urlopen(url + "api/status", timeout=10) as response:
        data = json.load(response)
    if data.get("app") != APP or data.get("port") != 8813 or data.get("source_sha256") != sha:
        raise RuntimeError("8813应用或冻结源码摘要不符，未操作其他服务")
    return data


MAP_COMMAND_CASE = "地图过渡中真实选择和正常命令均执行"


def audit_map_command_base(channel, base_directory, source_sha):
    report_path = (base_directory / channel / "标签与过渡验证.json").resolve()
    report_path.relative_to(OUTPUT.resolve())
    event_path = report_path.parent / "真实输入事件.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    events = json.loads(event_path.read_text(encoding="utf-8"))
    failed = [row["name"] for row in report["checks"] if not row["passed"]]
    if report["channel"] != channel or report["identity"]["source_sha256"] != source_sha or failed != [MAP_COMMAND_CASE] or sum(row["passed"] for row in report["checks"]) != 28:
        raise ValueError("原报告须为同源r07、其余28项通过且仅地图输入组合项失败")
    proof = [e for e in events if e["type"] == "pointerup" and e.get("button") == 0 and e.get("isTrusted") and e.get("targetInField") and e["beforeMapTransition"]["active"] and set(e["beforeSelected"]) != set(e["afterSelected"]) and "heli-1" in e["afterSelected"]]
    if not proof:
        raise ValueError("原r07没有独立可核对的地图活动期真实选择变化")
    return {"report_path": report_path.relative_to(ROOT).as_posix(), "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "events_path": event_path.relative_to(ROOT).as_posix(), "events_sha256": hashlib.sha256(event_path.read_bytes()).hexdigest(), "captured_identity": report["identity"], "captured_test_sha256": report["test_sha256"], "original_failed_checks": failed, "original_passed_other_checks": 28, "original_selection_proof": proof[0], "original_failed_preserved": True}


def map_command_pair(events):
    for up in events:
        if not command_executed(up) or not up["beforeMapTransition"]["active"] or up["beforeMapTransition"]["phase"] != "in" or (up.get("afterCommandResult") or {}).get("accepted", 0) <= 0:
            continue
        downs = [down for down in events if down["type"] == "pointerdown" and down.get("button") == 2 and down.get("isTrusted") and down.get("targetInField") and down.get("pointerId") == up.get("pointerId") and down["beforeWall"] <= up["beforeWall"] and down["beforeMapTransition"]["active"] and down["beforeMapTransition"]["phase"] == "in"]
        if downs:
            return {"down": downs[-1], "up": up}
    return None


def intersects(a, b, gap=0):
    return a["left"] < b["right"] + gap and a["right"] > b["left"] - gap and a["top"] < b["bottom"] + gap and a["bottom"] > b["top"] - gap


def command_executed(event):
    return event["isTrusted"] and event["type"] == "pointerup" and event.get("button") == 2 and event.get("targetInField") and event.get("afterCommandId") is not None and event.get("beforeCommandId") != event["afterCommandId"]


def overview_command_executed(event):
    return command_executed(event) and event.get("beforeCameraTransition", {}).get("active") and (event.get("afterCommandResult") or {}).get("accepted", 0) > 0


def input_response_metrics(frames, events):
    """短过渡看即时方向响应，持续持键看总推进；不规定短窗口必须转过固定角度。"""
    by_key = {}
    for code in ["KeyD", "KeyE"]:
        observations = []
        candidates = [e for e in events if e["type"] == "keydown" and e.get("code") == code and e.get("isTrusted") and e.get("targetInField") and e.get("beforeMapTransition", {}).get("active") and code in e.get("afterKeys", [])]
        for event in candidates:
            first = next((i for i, f in enumerate(frames) if f["wall"] >= event["beforeWall"]), len(frames))
            immediate = []
            for index in range(max(1, first), min(len(frames), first + 2)):
                a, b = frames[index - 1], frames[index]
                dt = (b["frameTime"] - a["frameTime"]) / 1000
                if dt <= 0 or code not in b["inputKeys"] or not b["transition"]["active"]:
                    continue
                dx = b["camera"]["center"]["x"] - a["camera"]["center"]["x"]
                dz = b["camera"]["center"]["z"] - a["camera"]["center"]["z"]
                yaw = b["camera"]["yaw"] - a["camera"]["yaw"]
                target = b["camera"]["targetAngle"] - a["camera"]["targetAngle"]
                right = dx * math.cos(a["camera"]["yaw"]) - dz * math.sin(a["camera"]["yaw"])
                expected = .85 * min(dt, .06)
                responsive = right > 1e-8 if code == "KeyD" else yaw > 1e-8 and target > 0 and abs(target - expected) <= max(1e-6, expected * .01)
                immediate.append({"frame": index, "frameTime": b["frameTime"], "dt": dt, "phase": b["transition"]["phase"], "generation": b["transition"]["generation"], "right_distance": right, "yaw_delta": yaw, "target_delta": target, "expected_target_delta": expected, "responsive": responsive})
            total = 0
            for index in range(max(1, first), len(frames)):
                a, b = frames[index - 1], frames[index]
                if code not in b["inputKeys"]:
                    break
                if code == "KeyE":
                    total += max(0, b["camera"]["yaw"] - a["camera"]["yaw"])
                else:
                    total += math.hypot(b["camera"]["center"]["x"] - a["camera"]["center"]["x"], b["camera"]["center"]["z"] - a["camera"]["center"]["z"])
            observations.append({"event": event, "next_frames": immediate, "held_total_advance": total, "passed": any(row["responsive"] for row in immediate) and total > .02})
        by_key[code] = {"passed": any(row["passed"] for row in observations), "observations": observations}
    return {"keys": by_key, "passed": all(row["passed"] for row in by_key.values())}


def available_grid(frame, kind, size):
    """只在隐藏样本上独立复核实际屏幕空间，不把no-space诊断本身当作通过依据。"""
    width, height = size
    blockers = frame.get("blockers", []) + frame.get("occupied", [])
    for y in range(4, max(4, int(frame["fieldHeight"] - height - 4)) + 1, 24):
        for x in range(4, max(4, int(frame["width"] - width - 4)) + 1, 24):
            box = {"left": x, "right": x + width, "top": y, "bottom": y + height}
            if not any(intersects(box, item, 6) for item in blockers):
                return True
    return False


def label_metrics(frames, strict_hover=False):
    result = {"frames": len(frames), "unexplained_hidden": [], "legitimate_hidden": [], "dom_diagnostic_mismatch": [], "kinds": {}}
    for kind in ["main", "height"]:
        previous, visible_rows, flips, sizes, slots, texts = None, [], 0, [], set(), set()
        for index, frame in enumerate(frames):
            item = frame[kind]
            visible = item["visible"]
            layout = item.get("layout")
            if previous is not None and previous != visible:
                flips += 1
            previous = visible
            if layout and bool(layout["visible"]) != visible:
                result["dom_diagnostic_mismatch"].append({"frame": index, "kind": kind, "layout": layout, "dom": item})
            eligible = frame["selected"] and frame["bodyInside"] and (kind == "main" or frame["groundInside"])
            if visible:
                visible_rows.append(item["rect"])
                sizes.append((item["rect"]["width"], item["rect"]["height"]))
                texts.add(item["text"])
                if layout:
                    slots.add(str(layout.get("slot")))
            elif eligible:
                reason = layout.get("reason") if layout else "缺少布局诊断"
                size = sizes[-1] if sizes else (layout.get("width", 240), layout.get("height", 36 if kind == "main" else 22)) if layout else (240, 36 if kind == "main" else 22)
                row = {"frame": index, "time": frame["time"], "kind": kind, "reason": reason}
                if reason == "no-space" and not available_grid(frame, kind, size):
                    result["legitimate_hidden"].append(row)
                else:
                    result["unexplained_hidden"].append(row)
            else:
                result["legitimate_hidden"].append({"frame": index, "kind": kind, "reason": "未选中或真实机身／地面投影在视口外"})
        jitter = max((math.hypot(r["left"] - visible_rows[0]["left"], r["top"] - visible_rows[0]["top"]) for r in visible_rows), default=0)
        result["kinds"][kind] = {"visible_frames": len(visible_rows), "visibility_flips": flips, "slots": sorted(slots), "text_variants": len(texts), "max_rect_offset": jitter}
    result["passed"] = not result["unexplained_hidden"] and not result["dom_diagnostic_mismatch"]
    if strict_hover:
        result["passed"] = result["passed"] and all(row["visible_frames"] == len(frames) and row["visibility_flips"] == 0 and len(row["slots"]) <= 1 and row["text_variants"] == 1 and row["max_rect_offset"] <= 1 for row in result["kinds"].values())
    return result


def transition_metrics(frames):
    states = [f["transition"] for f in frames]
    errors, intermediate, previous_generation = [], 0, -1
    intermediate_by_phase = {"out": 0, "in": 0}
    previous_by_phase = {}
    for index, state in enumerate(states):
        layers = state.get("layers", [])
        coverage = state.get("coverage")
        detail = state.get("detailCoverage")
        if not isinstance(coverage, (int, float)) or coverage < 1 - 1e-6 or not any(layer["visible"] and layer["opacity"] > 0 and layer["coverage"] > 0 for layer in layers):
            errors.append({"frame": index, "reason": "地图存在空覆盖", "state": state})
        if not isinstance(detail, (int, float)) or not 0 <= detail <= 1 or any(not math.isfinite(layer["opacity"]) or not 0 <= layer["opacity"] <= 1 for layer in layers):
            errors.append({"frame": index, "reason": "覆盖或透明度越界", "state": state})
        generation = state.get("displayedGeneration", -1)
        base_only = generation == 0 and state.get("displayedLevel") == "overview" and detail == 0 and any(layer["id"] == "overview" and layer["visible"] and layer["coverage"] >= 1 for layer in layers)
        if generation == 0 and not base_only:
            errors.append({"frame": index, "reason": "零代状态没有对应真实总览底层", "state": state})
        if generation != 0 and generation < previous_generation:
            errors.append({"frame": index, "reason": "已提交旧代重新出现", "state": state})
        previous_generation = max(previous_generation, generation)
        if state["active"] and state.get("phase") in ["out", "in"] and 0 < detail < 1:
            intermediate += 1
            intermediate_by_phase[state["phase"]] += 1
        phase = state.get("phase")
        key = (state.get("generation"), phase)
        old = previous_by_phase.get(key)
        if old is not None and ((phase == "out" and detail > old + 1e-6) or (phase == "in" and detail < old - 1e-6)):
            errors.append({"frame": index, "reason": "同代淡变透明度反向跳回", "state": state})
        previous_by_phase[key] = detail
    return {"frames": len(frames), "active_frames": sum(s["active"] for s in states), "intermediate_opacity_frames": intermediate, "intermediate_by_phase": intermediate_by_phase,
            "levels": sorted({s["displayedLevel"] for s in states}), "displayed_generations": sorted({s["displayedGeneration"] for s in states}),
            "errors": errors, "passed": not errors and intermediate >= 2 and all(intermediate_by_phase.values())}


def camera_metrics(frames, events=None):
    jumps, intervals = [], []
    modes = {"ordinary": 0, "log": 0, "start": 0, "finish": 0, "takeover": 0}

    def log_span(state):
        t = state["progress"]
        return math.exp(math.log(state["from"]) + math.log(state["to"] / state["from"]) * t * t * (3 - 2 * t))

    def close(a, b):
        return abs(a - b) <= max(.02, abs(b) * 1e-6)

    for index, (a, b) in enumerate(zip(frames, frames[1:]), 1):
        ta, tb = a.get("frameTime"), b.get("frameTime")
        if not isinstance(ta, (int, float)) or not isinstance(tb, (int, float)) or not math.isfinite(ta) or not math.isfinite(tb) or tb <= ta:
            jumps.append({"frame": index, "reason": "缺少递增的原生rAF时间戳", "before": ta, "after": tb})
            continue
        dt_ms, dt = tb - ta, (tb - ta) / 1000
        intervals.append(dt_ms)
        sample_lower_ms = max(0, tb - a["wall"])
        sample_upper_ms = max(0, b["wall"] - ta)
        previous, current = a["camera"]["span"], b["camera"]["span"]
        target, smoothing = b["camera"]["targetSpan"], b["camera"]["zoomTime"]
        sa, sb = a.get("cameraTransition", {}), b.get("cameraTransition", {})
        active_a, active_b = bool(sa.get("active")), bool(sb.get("active"))
        between = [e for e in events or [] if e.get("isTrusted") and a["wall"] < e.get("beforeWall", e.get("wall", -1)) <= b["wall"]]
        starters = [e for e in between if e["type"] == "click" and e.get("controlId") == "overview"]
        held = [e for e in events or [] if e.get("isTrusted") and e["type"] == "keydown" and e.get("beforeWall", -1) <= b["wall"] and e.get("code") in a.get("inputKeys", []) and e.get("code") in e.get("afterKeys", [])]
        takers = [e for e in between + held if e.get("beforeCameraTransition", {}).get("active") and ((e["type"] == "keydown" and e.get("targetInField") and e.get("code") in ["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "KeyF"]) or (e["type"] == "wheel" and e.get("targetInField") and e.get("deltaY", 0) != 0) or (e["type"] == "pointerdown" and e.get("targetInField") and e.get("button") == 1) or (e["type"] == "click" and e.get("controlId") == "focus"))]
        same_track = all(key in sa and key in sb and close(sa[key], sb[key]) for key in ["from", "to"])

        def log_step_allowed(end):
            if sa.get("from", 0) <= 0 or sa.get("to", 0) <= 0 or end <= 0:
                return False
            direction = math.log(sa["to"] / sa["from"])
            change = math.log(end / previous)
            bound = abs(direction) * (1.5 * sample_upper_ms / sa["durationMs"] + .06) + 1e-6
            return change * direction >= -1e-6 and abs(change) <= bound and min(sa["from"], sa["to"]) - .02 <= end <= max(sa["from"], sa["to"]) + .02

        ordinary_from = previous
        if active_b:
            if not close(current, log_span(sb)):
                jumps.append({"frame": index, "reason": "320毫秒对数轨迹与实际跨度不符", "span": current, "state": sb})
                continue
            if active_a and same_track and sb["progress"] >= sa["progress"]:
                modes["log"] += 1
                sample_elapsed_ms = (sb["progress"] - sa["progress"]) * sb["durationMs"]
                if not sample_lower_ms - 1e-6 <= sample_elapsed_ms <= sample_upper_ms + 1e-6 or not log_step_allowed(current):
                    jumps.append({"frame": index, "reason": "对数轨迹不在真实调用时刻可达区间", "native_dt": dt, "sample_elapsed_ms": sample_elapsed_ms, "sample_bounds_ms": [sample_lower_ms, sample_upper_ms], "before": a["camera"], "after": b["camera"], "state": sb})
            else:
                modes["start"] += 1
                if not starters or not close(sb["from"], previous):
                    jumps.append({"frame": index, "reason": "轨迹起点不连续或缺少真实启动事件", "span": previous, "state": sb})
                else:
                    direction = abs(math.log(sb["to"] / sb["from"]))
                    if sb["progress"] * sb["durationMs"] > sample_upper_ms + 1e-6 or abs(math.log(current / previous)) > direction * (1.5 * sample_upper_ms / sb["durationMs"] + .06) + 1e-6:
                        jumps.append({"frame": index, "reason": "新轨迹首帧瞬跳", "state": sb, "native_dt": dt, "sample_bounds_ms": [sample_lower_ms, sample_upper_ms]})
            continue
        if active_a:
            if same_track and sb.get("progress") == 1:
                modes["finish"] += 1
                if (1 - sa["progress"]) * sa["durationMs"] > sample_upper_ms + 1e-6 or not close(current, sa["to"]) or not log_step_allowed(current):
                    jumps.append({"frame": index, "reason": "轨迹结束帧不连续", "span": current, "state": sb, "native_dt": dt, "sample_bounds_ms": [sample_lower_ms, sample_upper_ms]})
                continue
            modes["takeover"] += 1
            if not takers or not close(sb.get("from", -1), sb.get("to", -2)) or not log_step_allowed(sb.get("from", -1)):
                jumps.append({"frame": index, "reason": "跨模式接管缺少真实输入或取消位置瞬跳", "before": sa, "after": sb, "dt": dt, "events": takers})
                continue
            # 接管可能发生在本帧对数采样前或后；取消点先受旧轨迹约束，后续仍受原指数界限约束。
            ordinary_from = sb["from"]
        else:
            modes["ordinary"] += 1
        gap = abs(target - ordinary_from)
        if gap <= 1 or smoothing <= 0:
            if abs(current - ordinary_from) > max(1, gap):
                jumps.append({"frame": index, "reason": "微小目标差值出现瞬移", "from": ordinary_from, "span": current, "target": target})
            continue
        permitted = 1 - math.exp(-min(dt, .1) / smoothing) + .06
        fraction = abs(current - ordinary_from) / gap
        if fraction > permitted:
            jumps.append({"frame": index, "fraction": fraction, "permitted": permitted, "dt": dt, "wall_dt": (b["wall"] - a["wall"]) / 1000, "previous_span": ordinary_from, "span": current, "target": target})
    return {"frames": len(frames), "modes": modes, "max_frame_interval_ms": max(intervals, default=0), "frame_intervals_over25ms": sum(value > 25 for value in intervals), "jumps": jumps, "passed": not jumps}


READ_FRAME = """id=>{
  const r=range,v=r.view,u=r.sim.units.find(u=>u.id===id),field=document.querySelector('#battlefield').getBoundingClientRect(),rig=v.units.get(id);
  const rect=node=>{if(!node)return null;const b=node.getBoundingClientRect();return {left:b.left-field.left,right:b.right-field.left,top:b.top-field.top,bottom:b.bottom-field.top,width:b.width,height:b.height};};
  const rows=v.labelStats?.rows??[];
  const nodeState=(node,kind)=>{const css=getComputedStyle(node),b=rect(node);let opacity=Number(css.opacity);for(let p=node.parentElement;p&&p!==document.body;p=p.parentElement)opacity*=Number(getComputedStyle(p).opacity);
    return{hidden:node.hidden,display:css.display,visibility:css.visibility,opacity,text:node.textContent,rect:b,
      visible:!node.hidden&&css.display!=='none'&&css.visibility!=='hidden'&&opacity>.01&&b.width>0&&b.height>0,
      layout:rows.find(row=>row.unitId===id&&row.kind===(kind==='main'?'unit':kind))??null};};
  const body=v.projectUnit(id),d=v.getAltitudeDisplay(id),g=d?.groundPoint?v.project(d.groundPoint):null;
  const transition=JSON.parse(JSON.stringify(r.externalMap.getTransitionState()));let pixels=null;
  if(transition.active){const gl=v.renderer.getContext(),canvas=v.renderer.domElement,pixel=new Uint8Array(4),rgba=[];for(const fx of [.2,.4,.6,.8])for(const fy of [.2,.4,.6,.8]){gl.readPixels(Math.floor(canvas.width*fx),Math.floor(canvas.height*fy),1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);rgba.push(Array.from(pixel));}pixels={rgba,opaque:rgba.filter(c=>c[3]>250).length,nearBlack:rgba.filter(c=>Math.max(c[0],c[1],c[2])<=8).length,meanLuma:rgba.reduce((n,c)=>n+.2126*c[0]+.7152*c[1]+.0722*c[2],0)/rgba.length};}
  return{wall:performance.now(),time:r.sim.time,phase:window.__g01LabelProbe?.phase??'unknown',x:u.x,y:u.y,z:u.z,selected:r.commander.selected.has(id),following:r.commander.following,
    job:u.job?{id:u.job.id,status:u.job.status,point:u.job.point}:null,queue:u.queue.map(j=>({id:j.id,point:j.point})),commandId:r.sim.lastCommandResult?.id??null,inputKeys:[...r.commander.keys],
    body,ground:g,bodyInside:body.visible&&body.x>=0&&body.x<=v.width&&body.y>=0&&body.y<=v.height,groundInside:!!g&&g.visible&&g.x>=0&&g.x<=v.width&&g.y>=0&&g.y<=v.height,
    width:v.width,fieldHeight:v.height,main:nodeState(rig.label,'main'),height:nodeState(rig.altitudeLabel,'height'),
    blockers:structuredClone(v.labelLayout?.blockers??[]),occupied:structuredClone(v.labelLayout?.occupied??[]),labelStats:{frame:v.labelStats?.frame,placementChanges:v.labelStats?.placementChanges,visibilityChanges:v.labelStats?.visibilityChanges},
    camera:{...v.getCameraState(),targetSpan:v.targetSpan,targetAngle:v.targetAngle},cameraTransition:JSON.parse(JSON.stringify(v.getCameraTransitionState())),transition,pixels,
    render:{calls:v.renderer.info.render.calls,triangles:v.renderer.info.render.triangles}};
}"""

START_TRACE = "id=>{const read=" + READ_FRAME + """;
  const probe=window.__g01LabelProbe={active:true,phase:'setup',samples:[],events:[],action:null};
  const tick=frameTime=>{if(!probe.active)return;if(probe.samples.length>=24000){probe.error='逐帧样本超过24000帧保护上限';probe.active=false;return;}const row=read(id);row.frameTime=frameTime;probe.samples.push(row);requestAnimationFrame(tick);};requestAnimationFrame(tick);
  const pending=new WeakMap(),before=e=>{if(!probe.active)return;const field=document.querySelector('#battlefield'),targetInField=field.contains(e.target),right=e.button===2&&['pointerdown','pointerup'].includes(e.type);let pointerGround=null;
    if(e.type==='pointerdown'&&right&&targetInField){try{const b=field.getBoundingClientRect(),local={x:e.clientX-b.left,y:e.clientY-b.top},ray=range.view.groundPoint(local.x,local.y),point=range.view.ground(local.x,local.y),ids=range.commander.ids();pointerGround={local,rayPoint:ray?{x:ray.x,y:ray.y,z:ray.z}:null,point,plans:point?range.sim.planCommand(ids,{type:'move',point},e.shiftKey).map(row=>({unitId:row.unit.id,status:row.status,valid:row.valid,reason:row.reason,point:row.job?.point})):[]};}catch(error){pointerGround={error:String(error)};}}
    pending.set(e,{type:e.type,button:e.button,pointerId:e.pointerId,code:e.code,deltaY:e.deltaY,controlId:e.target.closest?.('button')?.id??null,targetTag:e.target.tagName??null,isTrusted:e.isTrusted,action:probe.action,targetInField,pointerGround,beforeTime:range.sim.time,beforeWall:performance.now(),beforeCameraTransition:structuredClone(range.view.getCameraTransitionState()),beforeMapTransition:structuredClone(range.externalMap.getTransitionState()),beforeCommandId:range.sim.lastCommandResult?.id??null,beforeCommandResult:right?structuredClone(range.sim.lastCommandResult??null):null,beforeToast:right?document.querySelector('#toast')?.textContent:null,beforeSelected:[...range.commander.selected],beforeKeys:[...(range.commander.keys??[])]});};
  const after=e=>{if(!probe.active)return;const row=pending.get(e);if(!row)return;pending.delete(e);const afterSelected=[...range.commander.selected],afterCommandId=range.sim.lastCommandResult?.id??null,right=e.button===2&&['pointerdown','pointerup'].includes(e.type);probe.events.push({...row,time:range.sim.time,wall:performance.now(),cameraTransition:structuredClone(range.view.getCameraTransitionState()),transition:structuredClone(range.externalMap.getTransitionState()),afterSelected,afterCommandId,afterCommandResult:right?structuredClone(range.sim.lastCommandResult??null):null,afterToast:right?document.querySelector('#toast')?.textContent:null,afterKeys:[...(range.commander.keys??[])],selected:afterSelected,commandId:afterCommandId});};
  probe.beforeEvent=before;probe.afterEvent=after;probe.eventTypes=['pointerdown','pointerup','keydown','wheel','click'];for(const type of probe.eventTypes){document.addEventListener(type,before,true);window.addEventListener(type,after);}
}"""


def run_channel(channel, directory, args):
    from playwright.sync_api import sync_playwright

    routine = getattr(args, "routine", False)
    if not routine:
        directory.mkdir(exist_ok=False)

    def record(path, value):
        if not routine:
            save(path, value)
    result = {"passed": False, "channel": channel, "headless": True, "identity": get_identity(args.url, args.expect_source_sha), "test_sha256": TEST_SHA256,
              "started_epoch": time.time(), "manual_acceptance": "待工程师确认，G01整体未通过", "checks": [], "errors": [], "console_errors": [],
              "scope": "真实DOM与地图逐帧诊断会产生采样开销；本报告不作为正式性能或人工画面验收"}
    report = directory / "标签与过渡验证.json"
    record(report, result)

    collecting = False

    def check(name, passed, evidence=None):
        result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        record(report, result)
        if not passed and not collecting:
            raise AssertionError(name + "：" + repr(evidence))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: result["errors"].append(str(error)))
        page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" and len(result["console_errors"]) < 50 else None)
        trace_started = False
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_function("window.range?.ready", timeout=180000)
            result["browser_version"] = browser.version
            check("标签与地图过渡诊断接口存在", page.evaluate("Array.isArray(range.view.labelStats?.rows)&&typeof range.externalMap?.getTransitionState==='function'&&typeof range.view.getCameraTransitionState==='function'"))
            defaults = page.evaluate("range.view.getCameraState()")
            check("默认镜头参数保持48度和220米", abs(defaults["tiltDegrees"] - 48) < .1 and abs(defaults["viewWidth"] - 220) < 1, defaults)
            heli = page.evaluate("range.sim.units.find(u=>u.kind==='heli').id")
            tank = page.evaluate("range.sim.units.find(u=>u.kind==='tank').id")

            def sim_wait(seconds):
                start = page.evaluate("range.sim.time")
                page.wait_for_function("t=>range.sim.time>=t", arg=start + seconds, timeout=int((seconds * 5 + 20) * 1000))

            def settled():
                page.wait_for_function("!range.view.getCameraTransitionState().active&&!range.view.centerTarget&&!range.view.zoomAnchor&&Math.abs(range.view.span-range.view.targetSpan)<.02&&Math.abs(range.view.angle-range.view.targetAngle)<.001", timeout=25000)

            def paused(value):
                if page.evaluate("range.sim.paused") != value:
                    page.locator("#pause").click()
                page.wait_for_function("v=>range.sim.paused===v", arg=value)

            def phase(name):
                page.evaluate("name=>window.__g01LabelProbe.phase=name", name)

            def phase_samples(name):
                rows = page.evaluate("name=>window.__g01LabelProbe.samples.filter(s=>s.phase===name)", name)
                record(directory / (name + "逐帧.json"), rows)
                return rows

            def wheel(delta):
                old = page.evaluate("range.view.targetSpan")
                page.mouse.wheel(0, delta)
                page.wait_for_function("""({old,delta})=>{const v=range.view,b=delta<0?v.getCameraState().minWidth*v.height/v.width:v.map.maxSpan;return Math.abs(v.targetSpan-old)>1e-6||Math.abs(old-b)<1e-6;}""", arg={"old": old, "delta": delta}, timeout=10000)

            def zoom_to(width, delta, stop_on_transition=False):
                box = page.locator("#battlefield").bounding_box()
                page.mouse.move(box["x"] + box["width"] * .52, box["y"] + box["height"] * .55)
                for _ in range(36):
                    current = page.evaluate("range.view.targetSpan*range.view.width/range.view.height")
                    if (delta > 0 and current >= width) or (delta < 0 and current <= width + .1):
                        break
                    wheel(delta)
                    if stop_on_transition and page.evaluate("range.externalMap.getTransitionState().active"):
                        return
                    sim_wait(.04 if stop_on_transition else .1)
                    if stop_on_transition and page.evaluate("range.externalMap.getTransitionState().active"):
                        return

            def map_settled(level):
                page.wait_for_function("level=>{const s=range.externalMap.getTransitionState();return !s.active&&!s.pending&&s.displayedLevel===level&&s.detailCoverage>.999;}", arg=level, timeout=180000)

            def ensure_map_transition():
                if not page.evaluate("range.externalMap.getTransitionState().active"):
                    width = page.evaluate("range.view.targetSpan*range.view.width/range.view.height")
                    zoom_to(70 if width > 180 else 360, -180 if width > 180 else 180, stop_on_transition=True)
                page.wait_for_function("range.externalMap.getTransitionState().active", timeout=15000)

            def body_pixel(ident, prepare_selection=False):
                return page.evaluate("""({id,prepare})=>{const v=range.view,p=v.projectUnit(id),b=p.bounds,r=document.querySelector('#battlefield').getBoundingClientRect();
                  const done=pixel=>{if(!prepare)return pixel;const probe=window.__g01LabelProbe;probe.action='过渡机身选择';return{pixel,eventStart:probe.events.length,transition:range.externalMap.getTransitionState()};};if(!b)return done(null);
                  for(const fx of [.5,.35,.65,.2,.8])for(const fy of [.5,.35,.65,.2,.8]){const x=b.left+(b.right-b.left)*fx,y=b.top+(b.bottom-b.top)*fy,e=document.elementFromPoint(r.left+x,r.top+y);if(e&&!e.closest('button,input,select')&&v.pickUnits(x,y).includes(id))return done({x:r.left+x,y:r.top+y});}return done(null);}""", {"id": ident, "prepare": prepare_selection})

            def select_during_transition():
                attempts = result.setdefault("transition_selection_attempts", [])
                any_pixel = False
                for attempt in range(1, 5):
                    page.evaluate("window.__g01LabelProbe.action=null")
                    settled()
                    page.wait_for_function("!range.externalMap.getTransitionState().active&&!range.externalMap.getTransitionState().pending", timeout=180000)
                    # 所有侧栏前置操作在窗口外完成；随后只在真实淡入窗口里点机身。
                    page.locator(f'#roster [data-unit="{heli}"]').click()
                    page.locator("#focus").click()
                    settled()
                    page.wait_for_function("!range.externalMap.getTransitionState().active&&!range.externalMap.getTransitionState().pending", timeout=180000)
                    page.locator(f'#roster [data-unit="{tank}"]').click()
                    prepared = body_pixel(heli)
                    row = {"attempt": attempt, "prepared_pixel": prepared, "before_selected": page.evaluate("[...range.commander.selected]"), "before_transition": page.evaluate("range.externalMap.getTransitionState()")}
                    attempts.append(row)
                    record(report, result)
                    if not prepared:
                        row["reason"] = "前置状态无可命中的真实机身像素"
                        continue
                    any_pixel = True
                    page.mouse.move(prepared["x"], prepared["y"])
                    level = "middle" if row["before_transition"]["displayedLevel"] == "near" else "near"
                    target_width, delta = (360, 140) if level == "middle" else (70, -140)
                    for _ in range(18):
                        width = page.evaluate("range.view.targetSpan*range.view.width/range.view.height")
                        if (delta > 0 and width >= target_width) or (delta < 0 and width <= target_width + .1):
                            break
                        wheel(delta)
                    page.wait_for_function("""({generation,level})=>{const s=range.externalMap.getTransitionState();return s.generation>generation&&s.targetLevel===level&&((s.active&&s.phase==='in'&&s.progress<.6)||(!s.active&&!s.pending&&s.displayedLevel===level));}""", arg={"generation": row["before_transition"]["generation"], "level": level}, timeout=180000)
                    # 缩放可改变投影；保留前置像素，并只读更新一次当前真实命中位置。
                    prepared_event = body_pixel(heli, prepare_selection=True)
                    pixel = prepared_event["pixel"]
                    row["click_transition"] = prepared_event["transition"]
                    row["live_pixel"] = pixel
                    if not prepared_event["transition"]["active"]:
                        row["reason"] = "该次真实活动窗口已结束，未点选或延长过渡"
                        record(report, result)
                        continue
                    if not pixel:
                        row["reason"] = "当前淡入帧没有可命中的机身像素"
                        continue
                    page.mouse.click(pixel["x"], pixel["y"])
                    observed = page.evaluate("start=>window.__g01LabelProbe.events.slice(start)", prepared_event["eventStart"])
                    row["events"] = observed
                    row["passed"] = any(e["isTrusted"] and e["type"] == "pointerup" and e.get("button") == 0 and e["targetInField"] and e["beforeMapTransition"]["active"] and set(e["beforeSelected"]) != set(e["afterSelected"]) and heli in e["afterSelected"] for e in observed)
                    record(report, result)
                    if row["passed"]:
                        return {"pixel_available": True, "passed": True, "attempts": attempts}
                return {"pixel_available": any_pixel, "passed": False, "attempts": attempts}

            def destination(ident, distance=75):
                sample = page.evaluate("""({id,distance})=>{const r=range,u=r.sim.units.find(u=>u.id===id),v=r.view,rect=document.querySelector('#battlefield').getBoundingClientRect(),candidates=[];
                  for(const fx of [.16,.28,.4,.52,.64,.76,.86])for(const fy of [.22,.32,.42,.52,.62,.72,.82]){
                    const x=v.width*fx,y=v.height*fy,e=document.elementFromPoint(rect.left+x,rect.top+y);if(!e||e.closest('button,input,select,textarea'))continue;
                    const rayPoint=v.groundPoint(x,y),actual=v.ground(x,y);if(!rayPoint||!actual||actual.state!=='ready'||!Number.isFinite(actual.y))continue;
                    const support=r.sim.map.querySupport(actual.x,actual.z);if(support.state!=='ready'||!Number.isFinite(support.height))continue;
                    const point={x:actual.x,y:support.height,z:actual.z},length=Math.hypot(point.x-u.x,point.z-u.z);if(length<=5)continue;
                    const plan=r.sim.planCommand([id],{type:'move',point})[0];if(!plan?.valid||plan.status!=='ready')continue;
                    candidates.push({point,screen:{x,y},rayPoint:{x:rayPoint.x,y:rayPoint.y,z:rayPoint.z},support:{state:support.state,height:support.height},plan:{status:plan.status,reason:plan.reason},distance:length,cost:Math.abs(length-distance)});
                  }
                  candidates.sort((a,b)=>a.cost-b.cost);return{source:'真实视口网格经groundPoint和ground反投影',unitId:id,time:r.sim.time,camera:v.getCameraState(),candidateCount:candidates.length,chosen:candidates[0]??null};
                }""", {"id": ident, "distance": distance})
                result.setdefault("move_samples", []).append(sample)
                record(report, result)
                return sample["chosen"]["point"] if sample["chosen"] else None

            def move_mouse(point, append=False):
                q = page.evaluate("p=>range.view.project(p)", point)
                box = page.locator("#battlefield").bounding_box()
                if append:
                    page.keyboard.down("Shift")
                try:
                    page.mouse.move(box["x"] + q["x"], box["y"] + q["y"])
                    page.mouse.down(button="right")
                    page.mouse.up(button="right")
                finally:
                    if append:
                        page.keyboard.up("Shift")

            def overview_fixed_pixel_command():
                attempts = result.setdefault("overview_command_attempts", [])
                for attempt in range(1, 5):
                    page.evaluate("window.__g01LabelProbe.action=null")
                    settled()
                    width = page.evaluate("range.view.getCameraState().viewWidth")
                    if width > 400 or width < 180:
                        zoom_to(360, -220 if width > 400 else 220)
                        settled()
                    page.locator(f'#roster [data-unit="{heli}"]').click()
                    page.locator("#focus").click()
                    settled()
                    page.wait_for_function("!range.externalMap.getTransitionState().pending&&!range.externalMap.getTransitionState().active", timeout=180000)
                    page.locator("#overview").focus()
                    box = page.locator("#battlefield").bounding_box()
                    pixel = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
                    page.mouse.move(pixel["x"], pixel["y"])
                    start = page.evaluate("window.__g01LabelProbe.events.length")
                    row = {"attempt": attempt, "fixed_center_pixel": pixel, "camera_before": page.evaluate("range.view.getCameraState()")}
                    attempts.append(row)
                    record(report, result)
                    page.evaluate("window.__g01LabelProbe.action='总览中移动命令'")
                    # 焦点与Enter均为正常UI操作；保持鼠标在战场中心，不投影旧世界目标。
                    page.keyboard.press("Enter")
                    page.mouse.down(button="right")
                    page.mouse.up(button="right")
                    observed = page.evaluate("start=>window.__g01LabelProbe.events.slice(start)", start)
                    row["events"] = observed
                    ups = [e for e in observed if overview_command_executed(e)]
                    downs = [e for e in observed if e["type"] == "pointerdown" and e.get("button") == 2 and e["isTrusted"] and e["targetInField"] and e["beforeCameraTransition"]["active"]]
                    row["passed"] = any(down["pointerId"] == up["pointerId"] and down["beforeWall"] <= up["beforeWall"] for down in downs for up in ups)
                    if row["passed"]:
                        record(report, result)
                        return {"passed": True, "attempts": attempts}
                    down = next((e for e in observed if e["type"] == "pointerdown" and e.get("button") == 2), None)
                    up = next((e for e in observed if e["type"] == "pointerup" and e.get("button") == 2), None)
                    ground = (down or {}).get("pointerGround") or {}
                    if (ground.get("point") or {}).get("state") in ["pending", "unsupported"] or any(p.get("status") == "pending" for p in ground.get("plans", [])):
                        row["reason"] = "实际按下点地面未知或尚未准备"
                    elif any(p.get("status") == "rejected" for p in ground.get("plans", [])):
                        row["reason"] = "实际反投影点被规则拒绝"
                    elif not up:
                        row["reason"] = "没有可信右键抬起记录"
                    elif not up["beforeCameraTransition"]["active"]:
                        row["reason"] = "右键抬起前总览活动窗口已结束"
                    elif up["beforeCommandId"] == up["afterCommandId"]:
                        row["reason"] = "输入未提交，命令ID未变化；查看实际目标元素及toast"
                    else:
                        row["reason"] = "命令未受理；查看实际lastCommandResult拒绝项与toast"
                    record(report, result)
                return {"passed": False, "attempts": attempts}

            paused(True)
            page.locator('[data-layer="all"]').click()
            page.locator(f'#roster [data-unit="{heli}"]').click()
            page.locator("#stop").click()
            page.locator("#focus").click()
            settled()
            height = page.evaluate("id=>range.view.getAltitudeDisplay(id)", heli)
            check("局部30米高度基准保持", abs(height["height"] - 30) < .05, height)
            paused(False)
            sim_wait(.5)
            page.evaluate(START_TRACE, heli)
            trace_started = True
            phase("悬停")
            sim_wait(10.2)
            hover = phase_samples("悬停")
            hover_metrics = label_metrics(hover, True)
            check("真实悬停十秒主标签和高度文字持续稳定", hover[-1]["time"] - hover[0]["time"] >= 10 and math.hypot(hover[-1]["x"] - hover[0]["x"], hover[-1]["z"] - hover[0]["z"]) < .05 and hover_metrics["passed"], hover_metrics)
            if not routine:
                page.screenshot(path=str(directory / "悬停标签.png"))

            phase("移动准备")
            target = destination(heli)
            check("存在真实正常移动落点", bool(target), target)
            move_mouse(target)
            page.locator("#follow").click()
            phase("移动")
            sim_wait(10)
            moving = phase_samples("移动")
            moving_metrics = label_metrics(moving)
            check("真实移动中标签没有无故显隐", math.hypot(moving[-1]["x"] - moving[0]["x"], moving[-1]["z"] - moving[0]["z"]) > 20 and moving_metrics["passed"], moving_metrics)
            check("跟随期间真实坐标推进且机身持续可见", all(f["following"] == heli and f["bodyInside"] for f in moving[5:]), {"first": moving[0], "last": moving[-1]})
            if not routine:
                page.screenshot(path=str(directory / "移动标签.png"))

            phase("旋转")
            page.locator("#battlefield").focus()
            angle = page.evaluate("range.view.angle")
            page.keyboard.down("q")
            try:
                sim_wait(3)
            finally:
                page.keyboard.up("q")
            settled()
            sim_wait(.3)
            rotating = phase_samples("旋转")
            rotating_metrics = label_metrics(rotating)
            check("真实连续旋转中标签没有无故显隐", abs(page.evaluate("range.view.angle") - angle) > .5 and rotating_metrics["passed"], rotating_metrics)

            # 连续正反滚轮不等待每次收敛，逐帧观察镜头与地图；过渡阶段仍发送真实键鼠。
            phase("景别往返")
            if page.evaluate("Boolean(range.commander.following)"):
                page.locator("#follow").click()
            zoom_to(70, -220, stop_on_transition=True)
            page.wait_for_function("range.externalMap.getTransitionState().active", timeout=15000)
            result["transition_screenshot_before"] = page.evaluate("range.externalMap.getTransitionState()")
            if not routine:
                page.screenshot(path=str(directory / "反向地图淡变关键帧.png"))
            result["transition_screenshot_after"] = page.evaluate("range.externalMap.getTransitionState()")
            zoom_to(70, -220)
            settled()
            map_settled("near")
            check("70米近景仍可达", abs(page.evaluate("range.view.getCameraState().viewWidth") - 70) < .2)
            if not routine:
                page.screenshot(path=str(directory / "近景.png"))
            selection = select_during_transition()
            check("过渡中存在真实机身选择像素", selection["pixel_available"], selection)
            check("过渡真实机身选择已改变接收集合", selection["passed"], selection)
            ensure_map_transition()
            page.evaluate("window.__g01LabelProbe.action='过渡移动命令'")
            target = destination(heli, 65)
            check("过渡中存在真实正常移动落点", bool(target), target)
            old_command = page.evaluate("range.sim.lastCommandResult?.id??null")
            move_mouse(target)
            check("过渡正常移动命令实际提交", page.evaluate("old=>range.sim.lastCommandResult?.id!==old&&range.sim.lastCommandResult?.accepted>0", old_command))
            ensure_map_transition()
            page.evaluate("window.__g01LabelProbe.action='过渡平移旋转'")
            page.locator("#battlefield").focus()
            page.keyboard.down("d")
            page.keyboard.down("e")
            try:
                sim_wait(.25)
            finally:
                page.keyboard.up("d")
                page.keyboard.up("e")
            page.evaluate("window.__g01LabelProbe.action=null")
            zoom_to(360, 180)
            settled()
            map_settled("middle")
            if not routine:
                page.screenshot(path=str(directory / "中景.png"))
            overview_command = overview_fixed_pixel_command()
            check("总览轨迹中正常移动命令实际提交", overview_command["passed"], overview_command)
            settled()
            wheel(-350)
            settled()
            page.evaluate("window.__g01LabelProbe.action='总览轨迹接管'")
            page.locator("#overview").click()
            page.wait_for_function("range.view.getCameraTransitionState().active", timeout=10000)
            takeover_before = page.evaluate("({time:range.sim.time,center:{...range.view.center},yaw:range.view.angle})")
            page.locator("#battlefield").focus()
            page.keyboard.down("d")
            page.keyboard.down("e")
            try:
                sim_wait(.25)
            finally:
                page.keyboard.up("d")
                page.keyboard.up("e")
            takeover_after = page.evaluate("({time:range.sim.time,center:{...range.view.center},yaw:range.view.angle,transition:range.view.getCameraTransitionState()})")
            check("总览轨迹可被真实平移旋转接管", not takeover_after["transition"]["active"] and takeover_after["time"] - takeover_before["time"] >= .2 and math.hypot(takeover_after["center"]["x"] - takeover_before["center"]["x"], takeover_after["center"]["z"] - takeover_before["center"]["z"]) > .02 and abs(takeover_after["yaw"] - takeover_before["yaw"]) > .02, {"before": takeover_before, "after": takeover_after})
            page.evaluate("window.__g01LabelProbe.action=null")
            # 接管取消后单独重开完整总览，不要求旧轨迹继续执行。
            page.locator("#overview").click()
            page.wait_for_function("range.view.getCameraTransitionState().active", timeout=10000)
            settled()
            map_settled("far")
            check("重新启动的完整总览实际到达地图最大跨度", page.evaluate("Math.abs(range.view.span-range.view.map.maxSpan)<.1"))
            if not routine:
                page.screenshot(path=str(directory / "远景.png"))
            zoom_to(70, -220)
            settled()
            map_settled("near")
            sim_wait(.5)
            zooming = phase_samples("景别往返")
            collecting = True
            map_metrics = transition_metrics(zooming)
            zoom_labels = label_metrics(zooming)
            check("近中远及反向地图淡变持续覆盖且无旧代复现", map_metrics["passed"] and {"near", "middle", "far"}.issubset(map_metrics["levels"]), map_metrics)
            check("连续缩放标签隐藏均有真实原因", zoom_labels["passed"], zoom_labels)
            active = [f for f in zooming if f["transition"]["active"]]
            active_pairs = [(a, b) for a, b in zip(zooming, zooming[1:]) if a["transition"]["active"] and b["transition"]["active"] and a["transition"]["generation"] == b["transition"]["generation"]]
            active_motion = sum(math.hypot(b["x"] - a["x"], b["z"] - a["z"]) for a, b in active_pairs)
            camera_motion = sum(math.hypot(b["camera"]["center"]["x"] - a["camera"]["center"]["x"], b["camera"]["center"]["z"] - a["camera"]["center"]["z"]) for a, b in active_pairs)
            active_rotation = sum(abs(b["camera"]["yaw"] - a["camera"]["yaw"]) for a, b in active_pairs)
            active_sim_seconds = sum(max(0, b["time"] - a["time"]) for a, b in active_pairs)
            events = page.evaluate("window.__g01LabelProbe.events")
            record(directory / "真实输入事件.json", events)
            actual_events = [e for e in events if e["isTrusted"] and (e["beforeMapTransition"]["active"] or e["transition"]["active"])]
            check("地图过渡中模拟和真实移动持续推进", len(active_pairs) >= 2 and active_sim_seconds > .1 and active_motion > .02, {"active_frames": len(active), "same_generation_pairs": len(active_pairs), "active_sim_seconds": active_sim_seconds, "active_position_distance": active_motion})
            input_response = input_response_metrics(zooming, events)
            check("地图过渡中真实平移旋转保持响应", input_response["passed"], {"active_center_distance": camera_motion, "active_yaw_distance": active_rotation, "response": input_response})
            check("地图过渡中真实选择和正常命令均执行", any(e["type"] == "pointerup" and e.get("button") == 0 and e["targetInField"] and set(e["beforeSelected"]) != set(e["afterSelected"]) and heli in e["afterSelected"] for e in actual_events) and any(command_executed(e) for e in actual_events), actual_events)
            check("总览轨迹active期间确实收到命令和接管输入", any(overview_command_executed(e) for e in events) and any(e["isTrusted"] and e.get("code") == "KeyD" and e["beforeCameraTransition"]["active"] for e in events), [e for e in events if e["beforeCameraTransition"]["active"] or e["cameraTransition"]["active"]])
            overview_frames = [f for f in zooming if f["cameraTransition"]["active"] and 0 < f["cameraTransition"]["progress"] < 1]
            check("总览320毫秒轨迹确实跨越连续中间帧", len(overview_frames) >= 3 and all(f["cameraTransition"]["durationMs"] == 320 for f in overview_frames), {"intermediate_frames": len(overview_frames), "first": overview_frames[0] if overview_frames else None, "last": overview_frames[-1] if overview_frames else None})
            widths = [round(f["camera"]["viewWidth"], 3) for f in zooming]
            smooth = camera_metrics(zooming, events)
            check("镜头连续平滑经过中间景别而非直接跳档", smooth["passed"] and len(set(widths)) >= 20 and all(math.isfinite(w) and w >= 69.9 for w in widths), {"distinct_widths": len(set(widths)), "range": [min(widths), max(widths)], "continuity": smooth})
            pixel_frames = [f for f in zooming if f["pixels"]]
            valid_pixel_frames = [f for f in pixel_frames if f["pixels"]["opaque"] >= 12]
            black = [i for i, f in enumerate(zooming) if f["pixels"] and f["pixels"]["opaque"] == 16 and f["pixels"]["nearBlack"] == 16]
            check("地图淡变实际像素没有全黑帧", len(valid_pixel_frames) >= 3 and not black, {"sampled_frames": len(pixel_frames), "valid_opaque_frames": len(valid_pixel_frames), "near_black_frames": black, "min_grid_luma": min((f["pixels"]["meanLuma"] for f in valid_pixel_frames), default=None), "note": "每个活动帧读取地图画布内部16点；不把单个深色单位或局部暗像素当作全黑，透明无数据读回不作为渲染通过证据。"})
            try:
                result["final_identity"] = get_identity(args.url, args.expect_source_sha)
                check("结束时冻结实采身份一致", True, result["final_identity"])
            except Exception as identity_error:
                check("结束时冻结实采身份一致", False, {"error": repr(identity_error)})
            check("逐帧采样未超出保护预算", page.evaluate("!window.__g01LabelProbe.error"))
            check("地图资源和WebGL着色器没有运行错误", not result["console_errors"] and page.evaluate("!range.externalMap.stats.errors.length"), {"console_errors": result["console_errors"], "map_errors": page.evaluate("range.externalMap.stats.errors")})
            check("页面没有运行异常", not result["errors"], result["errors"])
            result["failed_checks"] = [row["name"] for row in result["checks"] if not row["passed"]]
            result["passed"] = not result["failed_checks"]
            if routine and not result["passed"]:
                record_failure(directory, result, page=page)
        except BaseException as error:
            result["failure"] = repr(error)
            if routine:
                record_failure(directory, result, error=error, page=page)
                if isinstance(error, Exception):
                    error.routine_result = result
            elif not page.is_closed():
                try:
                    result["failure_state"] = page.evaluate("({fatal:document.querySelector('#fatal')?.textContent,phase:window.__g01LabelProbe?.phase,camera:window.range?.view.getCameraState(),transition:window.range?.externalMap?.getTransitionState?.()})")
                    page.screenshot(path=str(directory / "失败.png"))
                except Exception as capture_error:
                    result["capture_error"] = repr(capture_error)
            raise
        finally:
            if not routine and trace_started and not page.is_closed():
                try:
                    trace = page.evaluate("()=>{const p=window.__g01LabelProbe;p.active=false;for(const type of p.eventTypes){document.removeEventListener(type,p.beforeEvent,true);window.removeEventListener(type,p.afterEvent);}return{samples:p.samples,events:p.events,error:p.error??null};}")
                    record(directory / "完整逐帧与事件.json", trace)
                except Exception as trace_error:
                    result["trace_error"] = repr(trace_error)
            result["finished_epoch"] = time.time()
            record(report, result)
            browser.close()
    return result


def run_map_command_only(channel, directory, args):
    """仅补r07缺少的地图淡入期命令证据，不重跑或改写其余28项。"""
    from playwright.sync_api import sync_playwright

    directory.mkdir(exist_ok=False)
    report_path = directory / "地图命令单项补充.json"
    result = {"mode": "地图命令单项补充实采", "passed": False, "channel": channel, "headless": True, "new_test_sha256": TEST_SHA256, "started_epoch": time.time(), "scope": "仅组合原r07真实选择证据与本次真实地图淡入命令证据；原failed及其余28项均不改写、不冒充重新实采", "checks": [], "attempts": [], "errors": [], "console_errors": [], "manual_acceptance": "G01整体仍待工程师人工验收"}

    def check(name, passed, evidence=None):
        result["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})
        save(report_path, result)

    baseline = audit_map_command_base(channel, args.base_run_dir, args.expect_source_sha)
    result["original_capture"] = baseline
    result["new_capture_identity"] = get_identity(args.url, args.expect_source_sha)
    check("原r07真实地图选择证据独立复核通过", True, baseline)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: result["errors"].append(str(error)))
        page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" and len(result["console_errors"]) < 50 else None)
        started = False
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_function("window.range?.ready", timeout=180000)
            result["new_browser_version"] = browser.version
            heli = page.evaluate("range.sim.units.find(u=>u.kind==='heli').id")
            if heli not in baseline["original_selection_proof"]["afterSelected"]:
                raise ValueError("本次直升机与原真实选择证据不一致")
            page.evaluate(START_TRACE, heli)
            started = True
            page.evaluate("window.__g01LabelProbe.phase='地图命令单项补充'")
            gesture = None
            for attempt in range(1, 5):
                page.evaluate("window.__g01LabelProbe.action=null")
                page.locator('[data-layer="all"]').click()
                page.locator(f'#roster [data-unit="{heli}"]').click()
                page.locator("#focus").click()
                page.wait_for_function("!range.view.getCameraTransitionState().active&&!range.view.centerTarget&&!range.view.zoomAnchor&&Math.abs(range.view.span-range.view.targetSpan)<.02", timeout=25000)
                page.wait_for_function("!range.externalMap.getTransitionState().pending&&!range.externalMap.getTransitionState().active", timeout=180000)
                before = page.evaluate("()=>{const b=document.querySelector('#battlefield').getBoundingClientRect(),x=b.left+b.width/2,y=b.top+b.height/2,e=document.elementFromPoint(x,y);return{pixel:{x,y},bare:!!e&&!e.closest('button,input,select,textarea,summary'),transition:range.externalMap.getTransitionState(),camera:range.view.getCameraState()};}")
                row = {"attempt": attempt, "prepared": before}
                result["attempts"].append(row)
                save(report_path, result)
                if not before["bare"]:
                    row["failure_kind"] = "中心像素被控件覆盖，未伪造点击"
                    continue
                page.mouse.move(before["pixel"]["x"], before["pixel"]["y"])
                start = page.evaluate("window.__g01LabelProbe.events.length")
                page.evaluate("window.__g01LabelProbe.action='地图专用中心命令'")
                target_level = "middle" if before["transition"]["displayedLevel"] == "near" else "near"
                target_width, delta = (360, 140) if target_level == "middle" else (70, -140)
                for _ in range(18):
                    width = page.evaluate("range.view.targetSpan*range.view.width/range.view.height")
                    if (delta > 0 and width >= target_width) or (delta < 0 and width <= target_width + .1):
                        break
                    old = page.evaluate("range.view.targetSpan")
                    page.mouse.wheel(0, delta)
                    page.wait_for_function("old=>Math.abs(range.view.targetSpan-old)>1e-6", arg=old, timeout=10000)
                # 所有准备均在前。返回即真实右键，不再查询世界点、像素或侧栏。
                gate = page.wait_for_function("""({generation,level})=>{const s=range.externalMap.getTransitionState();return s.generation>generation&&s.targetLevel===level&&((s.active&&s.phase==='in'&&s.progress<.4)||(!s.active&&!s.pending&&s.displayedLevel===level))?s:false;}""", arg={"generation": before["transition"]["generation"], "level": target_level}, timeout=180000)
                page.mouse.down(button="right")
                page.mouse.up(button="right")
                observed = page.evaluate("start=>window.__g01LabelProbe.events.slice(start)", start)
                row["wait_snapshot"] = gate.json_value()
                gate.dispose()
                row["events"] = observed
                gesture = map_command_pair(observed)
                row["passed"] = bool(gesture)
                if not gesture:
                    down = next((e for e in observed if e["type"] == "pointerdown" and e.get("button") == 2), None)
                    up = next((e for e in observed if e["type"] == "pointerup" and e.get("button") == 2), None)
                    ground = (down or {}).get("pointerGround") or {}
                    row["failure_kind"] = "地图淡入活动窗口内未取得同手势受理证据"
                    if (ground.get("point") or {}).get("state") in ["pending", "unsupported"]:
                        row["failure_kind"] = "实际按下点地面未知或未准备"
                    elif up and up.get("afterCommandResult") and up["afterCommandResult"].get("accepted", 0) == 0:
                        row["failure_kind"] = "实际命令未受理，保留拒绝项及toast"
                save(report_path, result)
                if gesture:
                    break
            check("新实采地图淡入期间同手势命令受理", bool(gesture), gesture or result["attempts"])
            result["combined_case"] = {"name": MAP_COMMAND_CASE, "original_selection_verified": True, "new_map_command_verified": bool(gesture), "passed": bool(gesture), "meaning": "仅组合此项；原r07 failed不覆盖，其余28项沿原实采记录"}
            page.screenshot(path=str(directory / "地图命令补充结果.png"))
            final_identity = get_identity(args.url, args.expect_source_sha)
            check("新旧实采绑定同一冻结源", final_identity["source_sha256"] == baseline["captured_identity"]["source_sha256"] and final_identity["map_sha256"] == baseline["captured_identity"]["map_sha256"], final_identity)
            unchanged = hashlib.sha256((ROOT / baseline["report_path"]).read_bytes()).hexdigest() == baseline["report_sha256"] and hashlib.sha256((ROOT / baseline["events_path"]).read_bytes()).hexdigest() == baseline["events_sha256"]
            check("原r07报告及事件SHA保持不变", unchanged)
            check("补充实采没有页面或控制台错误", not result["errors"] and not result["console_errors"], {"page": result["errors"], "console": result["console_errors"]})
            result["passed"] = all(row["passed"] for row in result["checks"])
        except BaseException as error:
            result["failure"] = repr(error)
            if not page.is_closed():
                try:
                    page.screenshot(path=str(directory / "补充失败.png"))
                except Exception as capture_error:
                    result["capture_error"] = repr(capture_error)
        finally:
            if started and not page.is_closed():
                trace = page.evaluate("()=>{const p=window.__g01LabelProbe;p.active=false;for(const type of p.eventTypes){document.removeEventListener(type,p.beforeEvent,true);window.removeEventListener(type,p.afterEvent);}return{samples:p.samples,events:p.events,error:p.error??null};}")
                save(directory / "补充逐帧与事件.json", trace)
            result["finished_epoch"] = time.time()
            save(report_path, result)
            browser.close()
    return result


def self_check():
    base = {"width": 200, "fieldHeight": 200, "bodyInside": True, "groundInside": True, "selected": True, "time": 0, "blockers": [], "occupied": []}
    label = {"visible": True, "text": "游隼01", "rect": {"left": 10, "right": 90, "top": 10, "bottom": 30, "width": 80, "height": 20}, "layout": {"visible": True, "slot": "top", "reason": "placed"}}
    frame = {**base, "main": label, "height": label}
    assert label_metrics([frame, frame], True)["passed"]
    hidden = {**label, "visible": False, "layout": {"visible": False, "slot": "top", "reason": "no-space", "width": 80, "height": 20}}
    assert not label_metrics([frame, {**frame, "main": hidden}])["passed"]
    state = {"active": True, "phase": "in", "generation": 1, "displayedGeneration": 1, "displayedLevel": "near", "coverage": 1, "detailCoverage": .3, "layers": [{"id": "overview", "visible": True, "opacity": 1, "coverage": 1}]}
    out = {**state, "phase": "out", "detailCoverage": .7}
    assert transition_metrics([{"transition": out}, {"transition": state}, {"transition": {**state, "detailCoverage": .8}}])["passed"]
    assert not transition_metrics([{"transition": {**state, "phase": "waiting"}}, {"transition": {**state, "phase": "preparing"}}])["passed"]
    assert not transition_metrics([{"transition": state}, {"transition": {**state, "coverage": 0}}])["passed"]
    overview = {**state, "phase": "waiting", "displayedGeneration": 0, "displayedLevel": "overview", "detailCoverage": 0}
    next_generation = {**state, "generation": 2, "displayedGeneration": 2}
    assert transition_metrics([{"transition": out}, {"transition": overview}, {"transition": next_generation}])["passed"]
    assert not transition_metrics([{"transition": next_generation}, {"transition": state}])["passed"]
    start = {"wall": 0, "frameTime": 0, "camera": {"span": 100, "targetSpan": 200, "zoomTime": .1}}
    assert camera_metrics([start, {"wall": 16, "frameTime": 16, "camera": {"span": 115, "targetSpan": 200, "zoomTime": .1}}])["passed"]
    assert not camera_metrics([start, {"wall": 16, "frameTime": 16, "camera": {"span": 200, "targetSpan": 200, "zoomTime": .1}}])["passed"]
    native_start = {"wall": 20, "frameTime": 0, "camera": {"span": 100, "targetSpan": 200, "zoomTime": .06}}
    q14 = 1 - math.exp(-.014 / .06)
    native_next = {"wall": 28, "frameTime": 14, "camera": {"span": 100 + 100 * q14, "targetSpan": 200, "zoomTime": .06}}
    assert abs(q14 - .20811) < .00001 and camera_metrics([native_start, native_next])["passed"]
    assert not camera_metrics([native_start, {**native_next, "camera": {**native_next["camera"], "span": 200}}])["passed"]
    dropped = {"wall": 58, "frameTime": 50, "camera": {"span": 100 + 100 * (1 - math.exp(-.05 / .06)), "targetSpan": 200, "zoomTime": .06}}
    assert camera_metrics([native_start, dropped])["passed"] and camera_metrics([native_start, dropped])["frame_intervals_over25ms"] == 1
    def sample_log(progress):
        return math.exp(math.log(100) + math.log(10) * progress * progress * (3 - 2 * progress))
    log_before = {"wall": 108, "frameTime": 100, "camera": {"span": sample_log(.2), "targetSpan": 1000, "zoomTime": .06}, "cameraTransition": {"active": True, "from": 100, "to": 1000, "progress": .2, "durationMs": 320}}
    cancelled = sample_log(.2 + 14 / 320)
    log_after = {"wall": 122, "frameTime": 114, "camera": {"span": cancelled, "targetSpan": cancelled, "zoomTime": .06}, "cameraTransition": {"active": False, "from": cancelled, "to": cancelled, "progress": 1, "durationMs": 320}}
    takeover = {"isTrusted": True, "type": "keydown", "code": "KeyD", "targetInField": True, "beforeWall": 112, "beforeCameraTransition": {"active": True}}
    assert camera_metrics([log_before, log_after], [takeover])["passed"]
    assert not camera_metrics([log_before, log_after])["passed"]
    teleported = {**log_after, "camera": {"span": 1000, "targetSpan": 1000, "zoomTime": .06}, "cameraTransition": {"active": False, "from": 1000, "to": 1000, "progress": 1, "durationMs": 320}}
    assert not camera_metrics([log_before, teleported], [takeover])["passed"]
    pending_frame = {**log_before, "wall": 122, "frameTime": 114, "inputKeys": ["KeyD"], "camera": {"span": sample_log(.24), "targetSpan": 1000, "zoomTime": .06}, "cameraTransition": {**log_before["cameraTransition"], "progress": .24}}
    delayed_cancel = {"wall": 136, "frameTime": 128, "inputKeys": ["KeyD"], "camera": {"span": sample_log(.24), "targetSpan": sample_log(.24), "zoomTime": .06}, "cameraTransition": {"active": False, "from": sample_log(.24), "to": sample_log(.24), "progress": 1, "durationMs": 320}}
    assert camera_metrics([log_before, pending_frame, delayed_cancel], [{**takeover, "afterKeys": ["KeyD"]}])["passed"]
    gpu_delayed = {**log_before, "wall": 140, "frameTime": 107, "camera": {"span": sample_log(.28), "targetSpan": 1000, "zoomTime": .06}, "cameraTransition": {**log_before["cameraTransition"], "progress": .28}}
    assert camera_metrics([log_before, gpu_delayed])["passed"]
    false_finish = {**gpu_delayed, "camera": {"span": 1000, "targetSpan": 1000, "zoomTime": .06}, "cameraTransition": {**log_before["cameraTransition"], "active": False, "progress": 1}}
    assert not camera_metrics([log_before, false_finish])["passed"]
    def response_frame(native, wall, active, x, yaw, target, keys):
        return {"frameTime": native, "wall": wall, "inputKeys": keys, "transition": {"active": active, "phase": "in" if active else "steady", "generation": 1}, "camera": {"center": {"x": x, "z": 0}, "yaw": yaw, "targetAngle": target}}
    response_frames = [response_frame(0, 0, True, 0, 0, 0, []), response_frame(5, 6, True, .001, .0005, .00425, ["KeyD", "KeyE"]), response_frame(50, 51, False, 1, .05, .0425, ["KeyD", "KeyE"]), response_frame(260, 261, False, 2, .2, .22, ["KeyD", "KeyE"])]
    response_events = [{"type": "keydown", "code": code, "beforeWall": 1, "isTrusted": True, "targetInField": True, "beforeMapTransition": {"active": True}, "afterKeys": ["KeyD", "KeyE"]} for code in ["KeyD", "KeyE"]]
    assert input_response_metrics(response_frames, response_events)["passed"]
    deferred_frames = [response_frames[0], response_frame(5, 6, True, 0, 0, 0, ["KeyD", "KeyE"]), *response_frames[2:]]
    assert not input_response_metrics(deferred_frames, response_events)["passed"]
    event = {"isTrusted": True, "type": "pointerup", "button": 2, "targetInField": True, "beforeCommandId": "command-2", "afterCommandId": "command-3"}
    assert command_executed(event)
    assert not command_executed({**event, "afterCommandId": "command-2"})
    assert not command_executed({**event, "isTrusted": False})
    overview_event = {**event, "beforeCameraTransition": {"active": True}, "afterCommandResult": {"accepted": 1}}
    assert overview_command_executed(overview_event)
    assert not overview_command_executed({**overview_event, "afterCommandResult": {"accepted": 0, "rejected": 1}})
    assert not overview_command_executed({**overview_event, "beforeCameraTransition": {"active": False}})
    down = {"type": "pointerdown", "button": 2, "pointerId": 1, "isTrusted": True, "targetInField": True, "beforeWall": 1, "beforeMapTransition": {"active": True, "phase": "in"}}
    up = {**event, "pointerId": 1, "beforeWall": 2, "beforeMapTransition": {"active": True, "phase": "in"}, "afterCommandResult": {"accepted": 1}}
    assert map_command_pair([down, up])
    assert not map_command_pair([down, {**up, "afterCommandResult": {"accepted": 0}}])
    assert not map_command_pair([down, {**up, "beforeMapTransition": {"active": False, "phase": "steady"}}])
    assert not map_command_pair([{**down, "beforeMapTransition": {"active": True, "phase": "out"}}, up])
    assert not map_command_pair([down, {**up, "pointerId": 2}])
    print("标签、地图覆盖、原生帧时序与真实接管分类离线自检通过；未启动浏览器或服务。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8813/")
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expect-source-sha")
    parser.add_argument("--map-command-only", action="store_true", help="仅补r07地图淡入期间命令证据，不重跑其余28项")
    parser.add_argument("--base-run-dir", type=Path, help="原r07双浏览器目录，只读核对报告和事件SHA")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--routine", action="store_true", help="日常回归通过只留汇总；失败保留截图和日志")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        url = urlparse(args.url)
        if url.scheme != "http" or url.hostname not in ["127.0.0.1", "localhost"] or url.port != 8813:
            parser.error("仅允许8813回环候选")
        if not args.output_dir or not args.expect_source_sha or len(args.expect_source_sha) != 64:
            parser.error("必须提供V003独立输出目录与完整冻结源码摘要")
        if args.routine and (args.map_command_only or args.base_run_dir):
            parser.error("日常模式不能用于正式单项补证")
        if args.map_command_only and not args.base_run_dir:
            parser.error("单项补充必须指定--base-run-dir原r07目录")
        if args.base_run_dir:
            args.base_run_dir = args.base_run_dir.resolve()
            args.base_run_dir.relative_to(OUTPUT.resolve())
        args.url = args.url.rstrip("/") + "/"
        directory = output_directory(args.output_dir, OUTPUT, args.routine)
        summary = {"passed": False, "mode": "地图命令单项补充" if args.map_command_only else "完整专项", "source_sha256": args.expect_source_sha, "test_sha256": TEST_SHA256, "channels": [], "started_epoch": time.time()}
        try:
            for channel in (["chrome", "msedge"] if args.channel == "both" else [args.channel]):
                if args.routine:
                    summary["channels"].append(compact(routine_channel(run_channel, channel, directory / channel, args)))
                else:
                    result = (run_map_command_only if args.map_command_only else run_channel)(channel, directory / channel, args)
                    summary["channels"].append({"channel": channel, "passed": result["passed"], "checks": len(result["checks"])})
            summary["passed"] = all(row["passed"] for row in summary["channels"])
        except Exception as error:
            if args.routine:
                result = getattr(error, "routine_result", {"channel": channel, "passed": False, "failure": repr(error)})
                summary["channels"].append(compact(result))
            raise
        finally:
            summary["finished_epoch"] = time.time()
            save(directory / "串行专项汇总.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
        if not summary["passed"]:
            raise SystemExit(1)
