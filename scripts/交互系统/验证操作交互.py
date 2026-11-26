"""
Scavenger 操作交互系统 Chrome + Edge 双浏览器端到端专项验证。

严格衔接统一正式单端口 http://127.0.0.1:8800（需主服务预先运行或显式起停）。
测试范围覆盖实施计划第五节第 2 项全部 7 大维度：
1) 4B->4C 编队打通（/armory 添入第 4 车 US_LAV25 跳转 /play 验证 4 车进场与槽位同步）
2) 真实左键点选、drag 框选 2+ 车、双击同类、Shift 增减、Tab/Shift+Tab 重叠循环、频道过滤
3) 右键拖预览（2D 标绘层像素非空 + Esc 取消未提交）、右键提交、Shift+右键三段队列、X 停止与固定翼盘旋
4) 建筑内部与出界坐标拒收（lastCommandResult.rejected > 0、标红、保留原任务）
5) 中键平移（无自动滚屏）、Q/E 旋转、滚轮地面锚点缩放、小地图拖曳、F 聚焦、G 持续跟随 + WASD 退跟随
6) 编辑器笔刷 #btn-ed-tool-bldg 放楼阻断、Space 8 槽轮盘与死区取消、K 强制攻地、Shift+1 / Alt+1 编组
7) 16:9 (1208x680) 与 21:9 (1588x680 Hor+) 双画幅无原生 <select>、标绘层 DPR 同步无滤镜、零控制台报错
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_BASE = ROOT / "local-data" / "交互系统" / "V001-b9029b57-w1" / "验证"


def wait_server_online(base_url: str) -> dict:
    with urlopen(f"{base_url}/healthz", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") != "ok":
            raise RuntimeError(f"主服务健康检查失败：{data}")
        return data


def must(cond: bool, message: str):
    if not cond:
        raise AssertionError(message)


def drag_mouse(page, x1: float, y1: float, x2: float, y2: float, button: str = "left", steps: int = 8, hold_before_up_ms: int = 0):
    page.mouse.move(x1, y1)
    page.mouse.down(button=button)
    for i in range(1, steps + 1):
        t = i / steps
        page.mouse.move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        page.wait_for_timeout(16)
    if hold_before_up_ms > 0:
        page.wait_for_timeout(hold_before_up_ms)
    page.mouse.up(button=button)


def find_safe_ground_point(page, offset_x: int = 0, offset_y: int = 0) -> tuple[float, float, dict]:
    """在视口中安全无 HUD 遮挡的区域寻找有效地形落点，并校验 elementFromPoint 命中画布。"""
    res = None
    for _attempt in range(6):
        res = page.evaluate(
            """([ox, oy]) => {
                const host = document.getElementById('canvas-host');
                const r = host.getBoundingClientRect();
                const candidates = [
                    [r.left + r.width * 0.50 + ox, r.top + r.height * 0.44 + oy],
                    [r.left + r.width * 0.42 + ox, r.top + r.height * 0.40 + oy],
                    [r.left + r.width * 0.58 + ox, r.top + r.height * 0.48 + oy],
                ];
                for (const fx of [0.24, 0.34, 0.46, 0.58, 0.68, 0.78, 0.85]) {
                    for (const fy of [0.22, 0.32, 0.42, 0.52, 0.62, 0.70]) {
                        candidates.push([r.left + r.width * fx, r.top + r.height * fy]);
                    }
                }
                for (const [cx, cy] of candidates) {
                    if (cx < r.left + 8 || cx > r.right - 8 || cy < r.top + 8 || cy > r.bottom - 8) continue;
                    const hit = document.elementFromPoint(cx, cy);
                    if (!hit || !host.contains(hit) || hit.closest('.ui-pod:not(.scv-mode-banner), .scv-minimap-box, .scv-deploy-rail, .scv-editor-drawer, .scv-deploy-drawer, .ground-mark, button')) {
                        continue;
                    }
                    const lx = cx - r.left;
                    const ly = cy - r.top;
                    const g = window.__SCV_INTERACTION__.queryGroundAt(lx, ly);
                    if (g && g.status === 'ready' && g.point && g.walkable !== false) {
                        return { cx, cy, lx, ly, ground: g, tag: hit.tagName };
                    }
                }
                return null;
            }""",
            [offset_x, offset_y],
        )
        if res is not None:
            break
        page.wait_for_timeout(120)
    must(res is not None, "未能找到无 HUD 遮挡且可通行的高程落点")
    return float(res["cx"]), float(res["cy"]), res


def find_open_ground_points(page, unit_ids: list[str], count: int = 3) -> list[dict]:
    """在中央至右侧无 HUD 遮挡画布区域筛选间距充足且 planCommand 全员可达的落点列表。"""
    pts = page.evaluate(
        """([ids, needed]) => {
            const host = document.getElementById('canvas-host');
            const r = host.getBoundingClientRect();
            const chosen = [];
            const fracsX = [0.64, 0.72, 0.56, 0.78, 0.48, 0.38];
            const fracsY = [0.42, 0.54, 0.30, 0.62];
            for (const fx of fracsX) {
                for (const fy of fracsY) {
                    const cx = r.left + r.width * fx;
                    const cy = r.top + r.height * fy;
                    const hit = document.elementFromPoint(cx, cy);
                    if (!hit || !host.contains(hit) || hit.closest('.ui-pod:not(.scv-mode-banner), .scv-minimap-box, .scv-deploy-rail, .scv-editor-drawer, .scv-deploy-drawer, .ground-mark, button')) {
                        continue;
                    }
                    if (chosen.some(p => Math.hypot(p.cx - cx, p.cy - cy) < 88)) continue;
                    const lx = cx - r.left;
                    const ly = cy - r.top;
                    const g = window.__SCV_INTERACTION__.queryGroundAt(lx, ly);
                    if (!g || g.status !== 'ready' || !g.point || g.walkable === false) continue;
                    const rows = window.__SCV_INTERACTION__.planCommand(ids, { type: 'move', point: g.point, facing: 1.1 }, false);
                    if (Array.isArray(rows) && rows.length === ids.length && rows.every(row => row.valid)) {
                        chosen.push({ cx, cy, lx, ly, point: g.point });
                        if (chosen.length >= needed) return chosen;
                    }
                }
            }
            return chosen;
        }""",
        [unit_ids, count],
    )
    must(len(pts) >= count, f"仅找到 {len(pts)}/{count} 个开阔可通行点")
    return pts


def get_unit_viewport_pos(page, unit_id: str) -> dict:
    return page.evaluate(
        """(uid) => {
            const host = document.getElementById('canvas-host');
            const r = host.getBoundingClientRect();
            const p = window.__SCV_INTERACTION__.projectUnit(uid);
            const cx = r.left + p.x;
            const cy = r.top + p.y;
            const el = p.visible ? document.elementFromPoint(cx, cy) : null;
            return {
                x: p.x,
                y: p.y,
                cx,
                cy,
                visible: p.visible,
                hitCanvas: Boolean(el && el.tagName === 'CANVAS'),
                hitTag: el ? el.tagName : null,
            };
        }""",
        unit_id,
    )


def wait_rendered_frames(page, count: int = 2):
    page.evaluate(
        """(n) => new Promise(resolve => {
            let left = Math.max(1, n);
            const tick = () => {
                left -= 1;
                if (left <= 0) resolve();
                else requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        })""",
        count,
    )


def focus_unit_safe(page, unit_id: str, span: float = 160.0) -> dict:
    """将指定单位通过 setView 置于屏幕中上部安全画布区内并验证不被 HUD 遮挡。"""
    pos = page.evaluate(
        """(uid) => {
            const units = window.__SCV_MAP__.getHeroVehiclesState();
            const u = units.find(x => x.id === uid);
            return u ? u.position : null;
        }""",
        unit_id,
    )
    must(pos is not None, f"找不到单位 {unit_id}")
    page.evaluate(
        """async ([x, z, sp]) => {
            await window.__SCV_INTERACTION__.setView({ focus: { x, z }, span: sp, yaw: 0, immediate: true });
        }""",
        [pos[0], pos[2] + span * 0.08, span],
    )
    wait_rendered_frames(page, 2)
    vp = get_unit_viewport_pos(page, unit_id)
    must(vp["visible"] and vp["hitCanvas"], f"单位 {unit_id} 未落在画布安全点击区: {vp}")
    return vp


def count_overlay_opaque_pixels(page) -> int:
    return page.evaluate(
        """() => {
            const c = document.getElementById('scv-tactical-overlay');
            if (!c || c.width === 0 || c.height === 0) return -1;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            let count = 0;
            for (let i = 3; i < data.length; i += 16) {
                if (data[i] > 18) count += 1;
            }
            return count;
        }"""
    )


def run_channel_verification(channel: str, base_url: str, out_dir: Path, headed: bool) -> dict:
    launch_args = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=swiftshader",
    ]
    report = {
        "channel": channel,
        "baseUrl": base_url,
        "passed": False,
        "checks": {},
        "screenshots": {},
        "errors": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed, args=launch_args)

        def attach_error_listeners(page, tag: str):
            page.on("pageerror", lambda err: report["errors"].append(f"[{tag}] pageerror: {err}"))
            page.on(
                "console",
                lambda msg: report["errors"].append(f"[{tag}] console.error: {msg.text}") if msg.type == "error" else None,
            )

        try:
            # =========================================================
            # Phase A (隔离上下文): 4B -> 4C 编队打通测试
            # =========================================================
            ctx_a = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
            page_a = ctx_a.new_page()
            attach_error_listeners(page_a, "4B->4C")

            page_a.goto(f"{base_url}/armory", wait_until="domcontentloaded", timeout=30000)
            page_a.wait_for_function("() => window.__SCV_APP__?.ready === true", timeout=25000)
            page_a.wait_for_selector('#armory-list .ui-unit-card[data-slug="US_LAV25"]', timeout=15000)
            initial_count = int(page_a.locator("#armory-roster-count").inner_text().strip())
            must(initial_count == 3, f"/armory 默认编队计数应为 3，实际 {initial_count}")

            page_a.fill("#armory-search", "US_LAV25")
            page_a.click('#armory-list .ui-unit-card[data-slug="US_LAV25"]')
            page_a.click("#armory-toggle-roster")
            page_a.wait_for_function("() => document.getElementById('armory-roster-count')?.textContent?.trim() === '4'", timeout=8000)
            shot_armory = out_dir / f"{channel}-01-armory-roster4.png"
            page_a.screenshot(path=str(shot_armory))
            report["screenshots"]["armoryRoster4"] = str(shot_armory)

            page_a.click(".armory-deploy-btn")
            page_a.wait_for_url("**/play**", timeout=20000)
            page_a.wait_for_function("() => window.__SCV_MAP__?.isCoreReady?.() === true && Boolean(window.__SCV_INTERACTION__)", timeout=30000)
            page_a.wait_for_timeout(400)

            hud_4 = page_a.evaluate("() => window.__SCV_MAP__.getUiHudState()")
            heroes_4 = page_a.evaluate("() => window.__SCV_MAP__.getHeroVehiclesState()")
            cg_slots = page_a.locator("#hud-cg-strip .scv-cg-btn").count()
            must(hud_4["heroCount"] == 4, f"4B->4C 后 /play heroCount 应为 4，实际 {hud_4['heroCount']}")
            must(any("US_LAV25" in h["id"] for h in heroes_4), f"应实装 US_LAV25 载具，实际 {[h['id'] for h in heroes_4]}")
            must(cg_slots == 6, f"战术编组快捷槽应恒定为 6 格，实际 {cg_slots}")
            shot_play4 = out_dir / f"{channel}-02-play-4vehicles.png"
            page_a.screenshot(path=str(shot_play4))
            report["screenshots"]["play4Vehicles"] = str(shot_play4)
            report["checks"]["armoryToPlay4Vehicles"] = {"heroCount": hud_4["heroCount"], "cgSlots": cg_slots, "ids": [h["id"] for h in heroes_4]}
            ctx_a.close()

            # =========================================================
            # Phase B (新隔离上下文): 默认 3 车 /play 全量交互验证 (DPR 1.25 / 16:9)
            # =========================================================
            ctx_b = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.25)
            ctx_b.add_init_script(
                """
                localStorage.setItem('scavenger.ui.prefs.v2', JSON.stringify({ vehicleAudioEnabled: false }));
                window.__SCV_AUDIO_CREATIONS__ = 0;
                const OrigAC = window.AudioContext;
                const OrigWAC = window.webkitAudioContext;
                if (OrigAC) {
                    window.AudioContext = function(...args) {
                        window.__SCV_AUDIO_CREATIONS__ += 1;
                        return new OrigAC(...args);
                    };
                }
                if (OrigWAC) {
                    window.webkitAudioContext = function(...args) {
                        window.__SCV_AUDIO_CREATIONS__ += 1;
                        return new OrigWAC(...args);
                    };
                }
                """
            )
            page = ctx_b.new_page()
            attach_error_listeners(page, "play-16x9")

            page.goto(f"{base_url}/play", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("() => window.__SCV_MAP__?.isCoreReady?.() === true && Boolean(window.__SCV_INTERACTION__)", timeout=30000)
            page.wait_for_timeout(500)

            # 2) 左键点选机身、drag 框选 2+ 车、Shift 增减、Tab/Shift+Tab 重叠循环、频道过滤
            vp_m1 = focus_unit_safe(page, "US_M1A2_01", span=150.0)
            page.mouse.click(vp_m1["cx"], vp_m1["cy"])
            st = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st["selectedIds"] == ["US_M1A2_01"], f"左键点选应仅选中 US_M1A2_01，实际 {st['selectedIds']}")

            # 双击同类验证不报错且保持同类选中
            page.mouse.dblclick(vp_m1["cx"], vp_m1["cy"])
            st_dbl = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must("US_M1A2_01" in st_dbl["selectedIds"], f"双击同类后应选中 US_M1A2_01，实际 {st_dbl['selectedIds']}")

            # 让直升机飞近地面坦克，方便做 2+ 车真框选 & Tab 重叠循环
            m1_pos = page.evaluate("() => window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_M1A2_01').position")
            target_x = m1_pos[0] + 18
            target_z = m1_pos[2] + 8
            page.evaluate(
                "([x, z]) => window.__SCV_INTERACTION__.dispatchCommand(['US_AH64D_01'], { type: 'move', point: { x, z } })",
                [target_x, target_z],
            )
            page.wait_for_function(
                """([tx, tz]) => {
                    const hs = window.__SCV_MAP__?.getHeroVehiclesState?.() || [];
                    const h = hs.find(u => u.id === 'US_AH64D_01');
                    const st = window.__SCV_INTERACTION__?.getState?.();
                    const hu = st?.units?.find(u => u.id === 'US_AH64D_01');
                    if (!h) return false;
                    const dist = Math.hypot(h.position[0] - tx, h.position[2] - tz);
                    return dist < 6.0 || (hu && hu.remainingWaypoints === 0);
                }""",
                arg=[target_x, target_z],
                timeout=45000,
            )

            # 计算包含地面车与直升机中点的宽俯瞰视野进行真框选
            both = page.evaluate(
                """async () => {
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const g = hs.find(u => u.id === 'US_M1A2_01').position;
                    const h = hs.find(u => u.id === 'US_AH64D_01').position;
                    await window.__SCV_INTERACTION__.setView({
                        focus: { x: (g[0] + h[0]) * 0.5, z: (g[2] + h[2]) * 0.5 + 15 },
                        span: 240,
                        yaw: 0,
                        immediate: true,
                    });
                    const r = document.getElementById('canvas-host').getBoundingClientRect();
                    const p1 = window.__SCV_INTERACTION__.projectUnit('US_M1A2_01');
                    const p2 = window.__SCV_INTERACTION__.projectUnit('US_AH64D_01');
                    return {
                        x1: r.left + Math.min(p1.x, p2.x) - 48,
                        y1: r.top + Math.min(p1.y, p2.y) - 48,
                        x2: r.left + Math.max(p1.x, p2.x) + 48,
                        y2: r.top + Math.max(p1.y, p2.y) + 48,
                        p1, p2,
                    };
                }"""
            )
            # 验证拖曳过程中 #scv-drag-box 可见
            page.mouse.move(both["x1"], both["y1"])
            page.mouse.down(button="left")
            page.mouse.move(both["x2"], both["y2"], steps=8)
            drag_visible = page.evaluate("() => !document.getElementById('scv-drag-box')?.hidden")
            must(drag_visible, "左键拖曳超过 5px 应显示 #scv-drag-box 矩形框")
            page.mouse.up(button="left")
            st_box = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(len(st_box["selectedIds"]) >= 2, f"框选应选中 >=2 辆载具，实际 {st_box['selectedIds']}")

            # Shift 减选与加选
            vp_ah = get_unit_viewport_pos(page, "US_AH64D_01")
            page.keyboard.down("Shift")
            page.mouse.click(vp_ah["cx"], vp_ah["cy"])
            st_sub = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must("US_AH64D_01" not in st_sub["selectedIds"] and "US_M1A2_01" in st_sub["selectedIds"], f"Shift 点选已选单位应剔除之，实际 {st_sub['selectedIds']}")
            page.mouse.click(vp_ah["cx"], vp_ah["cy"])
            page.keyboard.up("Shift")
            st_add = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(set(["US_M1A2_01", "US_AH64D_01"]).issubset(set(st_add["selectedIds"])), f"Shift 再点选应加回单位，实际 {st_add['selectedIds']}")

            # 频道过滤与空军防呆
            page.evaluate("() => window.__SCV_INTERACTION__.setChannelFilter('ground')")
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01', 'US_AH64D_01', 'US_A10C_01'])")
            st_flt = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st_flt["selectedIds"] == ["US_M1A2_01"], f"ground 频道下选区应过滤掉空军，实际 {st_flt['selectedIds']}")
            page.evaluate("() => window.__SCV_INTERACTION__.setChannelFilter('all')")

            # Tab / Shift+Tab 重叠循环：先拉远镜头使 M1A2 与 AH64D 屏幕间距落入拾取重叠圈 (< 15px)
            overlap = page.evaluate(
                """async () => {
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const g = hs.find(u => u.id === 'US_M1A2_01').position;
                    await window.__SCV_INTERACTION__.setView({ focus: { x: g[0], z: g[2] }, span: 2800, yaw: 0, immediate: true });
                    const r = document.getElementById('canvas-host').getBoundingClientRect();
                    const pg = window.__SCV_INTERACTION__.projectUnit('US_M1A2_01');
                    const ph = window.__SCV_INTERACTION__.projectUnit('US_AH64D_01');
                    const lx = (pg.x + ph.x) * 0.5;
                    const ly = (pg.y + ph.y) * 0.5;
                    const cands = window.__SCV_INTERACTION__.pickUnits(lx, ly, 'all');
                    return { cx: r.left + lx, cy: r.top + ly, cands };
                }"""
            )
            must(len(overlap["cands"]) >= 2, f"拉远视角重叠点应返回 >=2 个候选单位，实际 {overlap['cands']}")
            page.mouse.move(overlap["cx"], overlap["cy"])
            page.mouse.click(overlap["cx"], overlap["cy"])
            first_id = page.evaluate("() => window.__SCV_INTERACTION__.getState().primaryUnitId")
            page.keyboard.press("Tab")
            second_id = page.evaluate("() => window.__SCV_INTERACTION__.getState().primaryUnitId")
            page.keyboard.press("Shift+Tab")
            back_id = page.evaluate("() => window.__SCV_INTERACTION__.getState().primaryUnitId")
            must(first_id != second_id and back_id == first_id, f"Tab/Shift+Tab 重叠循环异常: {first_id} -> {second_id} -> {back_id}")

            report["checks"]["selectionAndFilter"] = {
                "boxSelected": st_box["selectedIds"],
                "channelFiltered": st_flt["selectedIds"],
                "overlapCycle": [first_id, second_id, back_id],
            }

            # 3) 右键拖预览（像素非空 + Esc 取消）、右键提交、Shift+右键 3 段队列、X 停止固定翼盘旋
            page.evaluate(
                "async () => await window.__SCV_INTERACTION__.setView({ focus: { x: 25, z: 85 }, span: 230, yaw: 0, immediate: true })"
            )
            page.wait_for_timeout(120)
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01', 'US_AH64D_01'])")
            open_pts = find_open_ground_points(page, ["US_M1A2_01", "US_AH64D_01"], count=3)
            gx1, gy1 = open_pts[0]["cx"], open_pts[0]["cy"]
            gx2, gy2 = open_pts[1]["cx"], open_pts[1]["cy"]
            gx3, gy3 = open_pts[2]["cx"], open_pts[2]["cy"]

            # 右键按住拖动朝向预览
            page.mouse.move(gx1, gy1)
            page.mouse.down(button="right")
            page.mouse.move(gx1 + 72, gy1 + 24, steps=6)
            page.wait_for_timeout(120)
            banner_vis = page.evaluate("() => !document.getElementById('scv-mode-banner')?.hidden")
            preview_px = count_overlay_opaque_pixels(page)
            must(banner_vis and preview_px > 25, f"右键拖动预览期横幅与标绘层应可见且非空: banner={banner_vis}, px={preview_px}")
            shot_preview = out_dir / f"{channel}-03-right-drag-preview.png"
            page.screenshot(path=str(shot_preview))
            report["screenshots"]["rightDragPreview"] = str(shot_preview)

            # Esc 取消未提交预览
            page.keyboard.press("Escape")
            page.mouse.up(button="right")
            page.wait_for_timeout(80)
            st_after_esc = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            m1_after_esc = next(u for u in st_after_esc["units"] if u["id"] == "US_M1A2_01")
            must(m1_after_esc["remainingWaypoints"] == 0, "Esc 取消未提交预览后不应下发航路")

            # 右键拖设朝向并松开提交 + Shift 追加两段构成 3 段队列
            drag_mouse(page, gx1, gy1, gx1 + 56, gy1 - 12, button="right", hold_before_up_ms=80)
            page.wait_for_timeout(100)
            page.keyboard.down("Shift")
            drag_mouse(page, gx2, gy2, gx2 + 48, gy2 + 12, button="right", hold_before_up_ms=60)
            drag_mouse(page, gx3, gy3, gx3 + 48, gy3 - 10, button="right", hold_before_up_ms=60)
            page.keyboard.up("Shift")
            page.wait_for_timeout(120)

            st_q = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            m1_q = next(u for u in st_q["units"] if u["id"] == "US_M1A2_01")
            queue_px = count_overlay_opaque_pixels(page)
            must(m1_q["queueLength"] == 2 and m1_q["remainingWaypoints"] > 0, f"应形成 1 活动 + 2 队列共 3 段，实际 {m1_q}")
            must(queue_px > 30, f"提交多段队列后标绘层像素应非空，实际 {queue_px}")

            # 对全编队下移动后再按 X 急停，核对固定翼更新盘旋中心且清空队列
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01', 'US_AH64D_01', 'US_A10C_01'])")
            page.evaluate(
                "() => window.__SCV_INTERACTION__.dispatchCommand(['US_A10C_01'], { type: 'move', point: { x: 420, z: -260 } }, false)"
            )
            page.keyboard.press("x")
            page.wait_for_timeout(120)
            st_x = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            m1_x = next(u for u in st_x["units"] if u["id"] == "US_M1A2_01")
            a10_x = next(u for u in st_x["units"] if u["id"] == "US_A10C_01")
            must(m1_x["queueLength"] == 0 and m1_x["commandStatus"] == "stopped", f"X 急停应清空地面队列，实际 {m1_x}")
            must(a10_x["commandStatus"] == "stopped" and a10_x["orbitCenter"] is not None, f"X 急停固定翼应转为空域盘旋，实际 {a10_x}")
            report["checks"]["commandAndQueue"] = {"previewPx": preview_px, "queuePx": queue_px, "m1Queue": m1_q["queueLength"], "a10AfterStop": a10_x}

            # 4) 建筑内部 + 出界坐标拒收，保留原任务
            page.evaluate(
                """async () => {
                    await window.__SCV_MAP__.addObstacleBuilding({ x: 60, z: 95, width: 28, depth: 28, height: 22 });
                }"""
            )
            page.evaluate(
                "() => window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 18, z: 95 } }, false)"
            )
            before_reject = next(u for u in page.evaluate("() => window.__SCV_INTERACTION__.getState().units") if u["id"] == "US_M1A2_01")
            blk_res = page.evaluate(
                "() => window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 60, z: 95 } }, false)"
            )
            out_res = page.evaluate(
                "() => window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 6800, z: 6800 } }, false)"
            )
            after_reject = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            m1_rej = next(u for u in after_reject["units"] if u["id"] == "US_M1A2_01")
            must(blk_res["rejected"] > 0 and out_res["rejected"] > 0, f"建筑与出界指令均应拒收: blk={blk_res}, out={out_res}")
            must(after_reject["lastCommandResult"]["rejected"] > 0 and m1_rej["hasRejected"], "应记录 lastCommandResult 与单位拒收提示")
            must(m1_rej["remainingWaypoints"] == before_reject["remainingWaypoints"] or m1_rej["jobType"] == "move", "拒收后应保留原移动任务")
            report["checks"]["cqsReject"] = {"buildingRejected": blk_res["rejected"], "oobRejected": out_res["rejected"]}

            # 5) 中键平移、Q/E 旋转、滚轮地面锚点缩放、小地图拖动、F 聚焦、G 跟随 + WASD 退跟随
            cam_0 = page.evaluate("() => window.__SCV_INTERACTION__.getState().view")
            gx2, gy2, ginfo2 = find_safe_ground_point(page, -40, 0)
            drag_mouse(page, gx2, gy2, gx2 + 120, gy2 + 60, button="middle", steps=8)
            page.wait_for_timeout(240)
            cam_pan = page.evaluate("() => window.__SCV_INTERACTION__.getState().view")
            pan_dist = math.hypot(cam_pan["focus"]["x"] - cam_0["focus"]["x"], cam_pan["focus"]["z"] - cam_0["focus"]["z"])
            must(pan_dist > 5.0, f"中键拖动应有效平移视口中心，实际位移 {pan_dist:.2f}m")

            yaw_before = cam_pan["yaw"]
            page.keyboard.down("q")
            page.evaluate("() => { for (let i = 0; i < 6; i++) window.__SCV_INTERACTION__.step(45, performance.now()); }")
            page.wait_for_timeout(200)
            page.keyboard.up("q")
            page.evaluate("() => { for (let i = 0; i < 4; i++) window.__SCV_INTERACTION__.step(45, performance.now()); }")
            yaw_after = page.evaluate("() => window.__SCV_INTERACTION__.getState().view.yaw")
            must(abs(yaw_after - yaw_before) > 0.025, f"Q 键应平滑旋转偏航角: {yaw_before:.3f} -> {yaw_after:.3f}")

            # 滚轮地面锚点缩放验证
            ax_vx, ax_vy, anchor_info = find_safe_ground_point(page, -60, -40)
            anchor_before = anchor_info["ground"]["point"]
            span_before_wheel = page.evaluate("() => window.__SCV_INTERACTION__.getState().view.span")
            page.mouse.move(ax_vx, ax_vy)
            for _ in range(4):
                page.mouse.wheel(0, -240)
                page.wait_for_timeout(45)
            page.wait_for_timeout(320)
            wheel_after = page.evaluate(
                """([lx, ly]) => ({
                    view: window.__SCV_INTERACTION__.getState().view,
                    pt: window.__SCV_INTERACTION__.queryGroundAt(lx, ly)?.point || null,
                })""",
                [anchor_info["lx"], anchor_info["ly"]],
            )
            must(wheel_after["view"]["span"] < span_before_wheel * 0.85, f"滚轮向上应明显缩小视距 span: {span_before_wheel} -> {wheel_after['view']['span']}")
            anchor_drift = math.hypot(
                wheel_after["pt"]["x"] - anchor_before["x"],
                wheel_after["pt"]["z"] - anchor_before["z"],
            )
            must(anchor_drift < 8.0, f"滚轮缩放在鼠标地面锚点处漂移过大: {anchor_drift:.2f}m")

            # 小地图左键按住拖曳平移
            mm_box = page.locator("#hud-minimap-box").bounding_box()
            must(mm_box is not None, "缺少 #hud-minimap-box")
            drag_mouse(
                page,
                mm_box["x"] + mm_box["width"] * 0.30,
                mm_box["y"] + mm_box["height"] * 0.35,
                mm_box["x"] + mm_box["width"] * 0.72,
                mm_box["y"] + mm_box["height"] * 0.65,
                button="left",
                steps=6,
            )
            page.wait_for_timeout(200)
            cam_mm = page.evaluate("() => window.__SCV_INTERACTION__.getState().view")
            must(cam_mm["focus"]["x"] > 500 and cam_mm["focus"]["z"] > 500, f"小地图拖曳右下象限应将视角移动至正象限，实际 {cam_mm['focus']}")

            # 选中 US_M1A2_01 并令其向开阔空地机动，测试 F 聚焦 + G 开启跟随 + W 平移保留跟随并累加偏移 + F 归中 + G 退出跟随
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01'])")
            page.evaluate(
                "() => window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 260, z: 85 } }, false)"
            )
            page.keyboard.press("f")
            page.wait_for_timeout(450)
            page.keyboard.press("g")
            page.wait_for_timeout(150)
            st_follow_on = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st_follow_on["followUnitId"] == "US_M1A2_01", f"按 G 应锁定跟随 US_M1A2_01，实际 {st_follow_on['followUnitId']}")
            pos_f1 = st_follow_on["view"]["focus"]
            page.wait_for_timeout(850)
            pos_f2 = page.evaluate("() => window.__SCV_INTERACTION__.getState().view.focus")
            follow_shift = math.hypot(pos_f2["x"] - pos_f1["x"], pos_f2["z"] - pos_f1["z"])
            must(follow_shift > 0.25, f"持续跟随期间相机中心应随移动车辆同步跟进，实际位移 {follow_shift:.3f}m")

            # 新持续跟随规则：按 WASD 平移不再退出跟随，改为累加 followOffset
            page.keyboard.down("w")
            page.evaluate("() => window.__SCV_INTERACTION__.step(60, performance.now())")
            page.wait_for_timeout(60)
            page.keyboard.up("w")
            st_follow_offset = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            off_w = st_follow_offset["view"].get("followOffset") or {"x": 0, "z": 0}
            off_w_norm = math.hypot(off_w["x"], off_w["z"])
            must(
                st_follow_offset["followUnitId"] == "US_M1A2_01" and off_w_norm > 0.5,
                f"按 WASD 平移应保持持续跟随并累加 followOffset，实际 followUnitId={st_follow_offset['followUnitId']}, offset={off_w}",
            )

            # 按 F 归零偏移回归居中，仍保持跟随
            page.keyboard.press("f")
            page.wait_for_timeout(100)
            st_follow_recenter = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            off_f = st_follow_recenter["view"].get("followOffset") or {"x": 0, "z": 0}
            must(
                st_follow_recenter["followUnitId"] == "US_M1A2_01" and math.hypot(off_f["x"], off_f["z"]) < 0.05,
                f"按 F 应归零 followOffset 且保持跟随，实际 {st_follow_recenter['followUnitId']}, offset={off_f}",
            )

            # 再按 G 明确关闭持续跟随
            page.keyboard.press("g")
            page.wait_for_timeout(80)
            st_follow_off = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st_follow_off["followUnitId"] is None, f"再次按 G 应退出持续跟随，实际 {st_follow_off['followUnitId']}")

            report["checks"]["cameraAndFollow"] = {
                "panDist": round(pan_dist, 2),
                "yawDelta": round(abs(yaw_after - yaw_before), 3),
                "wheelAnchorDriftM": round(anchor_drift, 2),
                "minimapFocus": [round(cam_mm["focus"]["x"], 1), round(cam_mm["focus"]["z"], 1)],
                "followShiftM": round(follow_shift, 2),
                "followOffsetAfterWM": round(off_w_norm, 2),
            }

            # 6) Space 方案 A 8 槽轮盘（Q12-A 轻按单击 + 按住无限外延甩选 + 死区取消 + 边角零裁切 + 兵种动态适配 + 失焦取消）
            focus_unit_safe(page, "US_M1A2_01", span=135.0)
            gx3, gy3, _ = find_safe_ground_point(page, 0, 0)
            page.mouse.move(gx3, gy3)
            page.keyboard.press("Space")
            page.wait_for_timeout(120)
            wheel_tap = page.evaluate(
                """() => {
                    const host = document.getElementById('scv-wheel-host');
                    const svg = host?.querySelector('.ui-wheel-svg');
                    const st = window.__SCV_INTERACTION__.getState();
                    return {
                        vis: Boolean(host && !host.hidden),
                        hasSvg: Boolean(svg),
                        slots: host ? host.querySelectorAll('.ui-wheel-slot').length : 0,
                        wheelState: st.wheelState,
                    };
                }"""
            )
            wheel_slots = wheel_tap["slots"]
            must(
                wheel_tap["vis"] and wheel_tap["hasSvg"] and wheel_slots == 8 and wheel_tap["wheelState"]["mode"] == "tap_click",
                f"轻按 Space 应以单击模式打开方案 A 8 槽 SVG 轮盘: {wheel_tap}",
            )
            shot_wheel = out_dir / f"{channel}-04-wheel-16x9.png"
            page.screenshot(path=str(shot_wheel))
            report["screenshots"]["wheel16x9"] = str(shot_wheel)

            page.click("#scv-wheel-host [data-wheel-cancel]")
            wheel_closed = page.evaluate("() => document.getElementById('scv-wheel-host')?.hidden === true && window.__SCV_INTERACTION__.getState().mode === 'play'")
            must(wheel_closed, "点击轮盘中心取消死区应关闭轮盘回到 play 模式")

            # 6.1) 按住 Space → 向右上 (-45°) 甩出越过死区 → 高亮警戒守备 → 松开执行一次并同步右下方案 B 命令栏
            guard_before = page.evaluate("() => Boolean(window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_M1A2_01')?.groundGuard)")
            page.mouse.move(gx3, gy3)
            page.keyboard.down("Space")
            page.wait_for_timeout(60)
            page.mouse.move(gx3 + 120, gy3 - 120, steps=4)
            page.wait_for_timeout(60)
            flick_hover = page.evaluate("() => window.__SCV_INTERACTION__.getState().wheelState")
            must(
                flick_hover["open"] and flick_hover["mode"] == "hold_flick" and flick_hover["hoveredIndex"] == 1 and flick_hover["hoveredSlotId"] == "guard",
                f"按住 Space 向右上甩选应锁定 1 号槽警戒守备: {flick_hover}",
            )
            page.keyboard.up("Space")
            page.wait_for_timeout(80)
            guard_sync = page.evaluate(
                """() => {
                    const u = window.__SCV_MAP__.getHeroVehiclesState().find(x => x.id === 'US_M1A2_01');
                    const cmdBtn = document.querySelector('#hud-cmd-grid [data-cmd-key="Z"]');
                    return {
                        closed: document.getElementById('scv-wheel-host')?.hidden === true,
                        guardOn: Boolean(u?.groundGuard),
                        cmdActive: Boolean(cmdBtn?.classList.contains('is-active-mode')),
                    };
                }"""
            )
            must(
                guard_sync["closed"] and guard_sync["guardOn"] != guard_before and guard_sync["cmdActive"] == guard_sync["guardOn"],
                f"松开 Space 应执行一次并与右下方案 B 命令栏完全同源: before={guard_before}, after={guard_sync}",
            )

            # 6.2) 甩出后退回中心死区再松开 Space -> 安全取消不改变状态
            page.mouse.move(gx3, gy3)
            page.keyboard.down("Space")
            page.mouse.move(gx3 + 110, gy3 - 110, steps=3)
            page.mouse.move(gx3, gy3, steps=3)
            page.keyboard.up("Space")
            page.wait_for_timeout(60)
            guard_after_cancel = page.evaluate(
                "() => ({ closed: document.getElementById('scv-wheel-host')?.hidden === true, guardOn: Boolean(window.__SCV_MAP__.getHeroVehiclesState().find(x => x.id === 'US_M1A2_01')?.groundGuard) })"
            )
            must(
                guard_after_cancel["closed"] and guard_after_cancel["guardOn"] == guard_sync["guardOn"],
                f"甩回中心死区松开应取消而不改变警戒状态: {guard_after_cancel}",
            )

            # 6.3) 屏幕左上/右下角呼出自动内推零裁切 + 静止释放不误触
            host_rect = page.evaluate("() => { const r = document.getElementById('canvas-host').getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; }")
            for corner_x, corner_y in [
                (host_rect["left"] + 12, host_rect["top"] + 12),
                (host_rect["left"] + host_rect["width"] - 12, host_rect["top"] + host_rect["height"] - 12),
            ]:
                page.mouse.move(corner_x, corner_y)
                page.keyboard.press("Space")
                page.wait_for_timeout(80)
                c_check = page.evaluate(
                    """() => {
                        const hr = document.getElementById('canvas-host').getBoundingClientRect();
                        const wr = document.querySelector('#scv-wheel-host .ui-wheel').getBoundingClientRect();
                        const ws = window.__SCV_INTERACTION__.getState().wheelState;
                        return {
                            clamped: ws.clamped,
                            mode: ws.mode,
                            hoveredIndex: ws.hoveredIndex,
                            insideX: wr.left >= hr.left - 1 && wr.right <= hr.right + 1,
                            insideY: wr.top >= hr.top - 1 && wr.bottom <= hr.bottom + 1,
                        };
                    }"""
                )
                must(
                    c_check["clamped"] and c_check["mode"] == "tap_click" and c_check["hoveredIndex"] == -1 and c_check["insideX"] and c_check["insideY"],
                    f"边角呼出轮盘应自动内推零裁切且静止不误触: {c_check}",
                )
                page.keyboard.press("Escape")
                page.wait_for_timeout(50)

            # 6.4) 兵种动态槽位（A-10C 固定翼 8/8 满槽 vs 混选 4/8 槽）与窗口失焦安全取消
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_A10C_01'])")
            page.mouse.move(gx3, gy3)
            page.keyboard.press("Space")
            page.wait_for_timeout(80)
            fw_wheel = page.evaluate("() => window.__SCV_INTERACTION__.getState().wheelState")
            must(fw_wheel["open"] and fw_wheel["validSlotCount"] == 8, f"固定翼 A-10C 应呈现 8 个专属有效槽位: {fw_wheel}")
            page.evaluate("() => window.dispatchEvent(new Event('blur'))")
            page.wait_for_timeout(50)
            blur_closed = page.evaluate("() => !window.__SCV_INTERACTION__.getState().wheelOpen")
            must(blur_closed, "窗口失焦应自动安全关闭轮盘")

            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01', 'US_A10C_01'])")
            page.keyboard.press("Space")
            page.wait_for_timeout(80)
            mix_wheel = page.evaluate("() => window.__SCV_INTERACTION__.getState().wheelState")
            must(mix_wheel["open"] and mix_wheel["validSlotCount"] == 4, f"混选编队应仅保留 4 个语义一致通用槽位: {mix_wheel}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(60)
            # 恢复 M1A2 原警戒状态（通过右下命令栏按钮双向切回，同时验证命令栏 -> 轮盘反向同源）
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01'])")
            page.wait_for_timeout(50)
            page.click('#hud-cmd-grid [data-cmd-key="Z"]')
            page.wait_for_timeout(60)
            guard_restored = page.evaluate("() => Boolean(window.__SCV_MAP__.getHeroVehiclesState().find(x => x.id === 'US_M1A2_01')?.groundGuard)")
            must(guard_restored == guard_before, f"点击右下命令栏 Z 应双向恢复警戒状态: {guard_restored} vs {guard_before}")

            # K 强制攻地
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01'])")
            page.keyboard.press("k")
            page.wait_for_timeout(80)
            st_tgt = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st_tgt["mode"] == "targeting" and st_tgt["cursor"] == "attack", f"K 键应进入 targeting 攻地模式，实际 {st_tgt}")
            page.mouse.click(gx3, gy3)
            page.wait_for_timeout(100)
            st_after_ag = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            m1_ag = next(u for u in st_after_ag["units"] if u["id"] == "US_M1A2_01")
            must(st_after_ag["mode"] == "play" and m1_ag["hasAttackGroundIntent"], f"左键指定攻地后应退回 play 并登记意图: {m1_ag}")

            # Shift+1 保存与 Alt+1 召回编组
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01', 'US_AH64D_01'])")
            page.keyboard.press("Shift+Digit1")
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_A10C_01'])")
            page.keyboard.press("Alt+Digit1")
            st_cg = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(set(st_cg["selectedIds"]) == {"US_M1A2_01", "US_AH64D_01"}, f"Alt+1 应召回 Shift+1 编组，实际 {st_cg['selectedIds']}")

            # 进入编辑器模式测试 #btn-ed-tool-bldg 建楼笔刷
            page.keyboard.press("p")
            page.wait_for_timeout(150)
            obb_before = page.locator("#ui-ed-obb").inner_text()
            page.click("#btn-ed-tool-bldg")
            page.wait_for_timeout(100)
            st_ed = page.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(st_ed["mode"] == "editor" and st_ed["activeBrush"] == "add_building", f"应进入建楼笔刷子模式，实际 {st_ed}")
            gx_ed, gy_ed, _ = find_safe_ground_point(page, 20, 20)
            page.mouse.click(gx_ed, gy_ed)
            page.wait_for_timeout(450)
            obb_after = page.locator("#ui-ed-obb").inner_text()
            must(obb_after != obb_before, f"建楼笔刷左键落点应增加动态碰撞盒计数: {obb_before} -> {obb_after}")
            shot_ed = out_dir / f"{channel}-05-editor-brush.png"
            page.screenshot(path=str(shot_ed))
            report["screenshots"]["editorBrush"] = str(shot_ed)

            # 右键退出笔刷回到指挥态，再 Esc 收起编辑器抽屉
            page.mouse.click(gx_ed, gy_ed, button="right")
            page.wait_for_timeout(80)
            must(page.evaluate("() => window.__SCV_INTERACTION__.getState().mode === 'play'"), "编辑器下右键应退回作战指挥态")
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)

            report["checks"]["wheelTargetingAndEditor"] = {
                "wheelSlots": wheel_slots,
                "attackGroundIntent": m1_ag["hasAttackGroundIntent"],
                "controlGroupRecalled": st_cg["selectedIds"],
                "editorObbTransition": f"{obb_before} -> {obb_after}",
            }

            # 7) 16:9 渲染与标绘层基线核验 + 21:9 (1588x680 Hor+) 全幅核验
            def audit_baseline(expected_aspect: str, expected_w: int):
                info = page.evaluate(
                    """() => {
                        const hud = window.__SCV_MAP__.getUiHudState();
                        const met = window.__SCV_MAP__.getTransitionAndMemoryMetrics();
                        const ov = document.getElementById('scv-tactical-overlay');
                        const host = document.getElementById('canvas-host');
                        const ovStyle = getComputedStyle(ov);
                        return {
                            aspectKey: hud.aspectKey,
                            internalResolution: met.internal_resolution,
                            nativeSelectCount: document.querySelectorAll('select').length,
                            hostW: host.clientWidth,
                            hostH: host.clientHeight,
                            dpr: window.devicePixelRatio || 1,
                            ovW: ov.width,
                            ovH: ov.height,
                            ovFilter: ovStyle.filter,
                            ovImageRendering: ovStyle.imageRendering,
                            hasErrorsBanner: false,
                        };
                    }"""
                )
                must(info["aspectKey"] == expected_aspect, f"画幅键应为 {expected_aspect}，实际 {info['aspectKey']}")
                must(info["internalResolution"] == [expected_w, 680], f"内部分辨率应为 [{expected_w}, 680]，实际 {info['internalResolution']}")
                must(info["nativeSelectCount"] == 0, f"应零原生 <select>，实际 {info['nativeSelectCount']}")
                must(info["ovFilter"] == "none", f"高清标绘层禁止滤镜，实际 filter={info['ovFilter']}")
                must(info["ovImageRendering"] != "pixelated", f"高清标绘层不应 pixelated，实际 {info['ovImageRendering']}")
                expected_ov_w = round(info["hostW"] * info["dpr"])
                expected_ov_h = round(info["hostH"] * info["dpr"])
                must(
                    abs(info["ovW"] - expected_ov_w) <= 2 and abs(info["ovH"] - expected_ov_h) <= 2,
                    f"标绘层尺寸未与 DPR 同步: actual=({info['ovW']},{info['ovH']}), expected=({expected_ov_w},{expected_ov_h})",
                )
                return info

            base_16x9 = audit_baseline("16:9", 1208)

            # 切换至 21:9 + 2560x1080 视口并核验标绘层重算与布局
            page.set_viewport_size({"width": 2560, "height": 1080})
            page.keyboard.press("u")
            page.wait_for_timeout(350)
            base_21x9 = audit_baseline("21:9", 1588)
            shot_21x9 = out_dir / f"{channel}-06-play-21x9.png"
            page.screenshot(path=str(shot_21x9))
            report["screenshots"]["play21x9"] = str(shot_21x9)

            report["checks"]["dualAspectAndOverlayBaseline"] = {"16:9": base_16x9, "21:9": base_21x9}

            audio_creations = page.evaluate("() => window.__SCV_AUDIO_CREATIONS__ || 0")
            must(audio_creations == 0, f"载具交互全程禁止触发音频创建/播放，实际 AudioContext 次数={audio_creations}")
            report["checks"]["silentVehicleInteraction"] = {"audioContextCreations": audio_creations}

            # =========================================================
            # Phase C (Wave 1 Stage 1A/1B/2 专项): 4级视距图标 + 固定翼贴地范围圈 + 37车部件与刚体姿态
            # =========================================================
            # 清理 Phase B 编辑测试放置的临时建筑，并切回 16:9 (1600x900) 标准视口
            page.evaluate("async () => await window.__SCV_MAP__.clearObstacleBuildings()")
            page.set_viewport_size({"width": 1600, "height": 900})
            page.evaluate("() => window.__SCV_MAP__.setAspectProfile('16:9')")
            wait_rendered_frames(page, 2)

            # C1) L0 (span=135m) 近景零 2D 图标 + 贴身名牌可见 + 近景车型与部件细节截图
            page.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_M1A2_01'])")
            focus_unit_safe(page, "US_M1A2_01", span=135.0)
            wait_rendered_frames(page, 2)
            l0_state = page.evaluate(
                """() => ({
                    overlay: window.__SCV_INTERACTION__.getState().overlay,
                    visibleLabels: [...document.querySelectorAll('.ground-mark')].filter(el => el.style.display !== 'none').length,
                })"""
            )
            must(l0_state["overlay"]["tier"] == "none" and l0_state["overlay"]["iconCount"] == 0, f"L0 (135m) 不应绘制 2D 图标: {l0_state}")
            must(l0_state["visibleLabels"] >= 1, f"L0 近景应显示 DOM 贴身名牌: {l0_state}")
            shot_l0_m1 = out_dir / f"{channel}-07a-L0-135m-M1A2.png"
            page.screenshot(path=str(shot_l0_m1))
            report["screenshots"]["l0M1A2"] = str(shot_l0_m1)

            focus_unit_safe(page, "US_AH64D_01", span=135.0)
            wait_rendered_frames(page, 2)
            shot_l0_ah = out_dir / f"{channel}-07b-L0-135m-AH64D.png"
            page.screenshot(path=str(shot_l0_ah))
            report["screenshots"]["l0AH64D"] = str(shot_l0_ah)

            # C2) L1 (span=460m) 紧凑图标 22x16 + 仅选中/悬停单位留名牌 + 点击图标命中
            page.evaluate(
                """async () => {
                    window.__SCV_INTERACTION__.setSelection(['US_M1A2_01']);
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const g = hs.find(u => u.id === 'US_M1A2_01').position;
                    const h = hs.find(u => u.id === 'US_AH64D_01').position;
                    await window.__SCV_INTERACTION__.setView({ focus: { x: (g[0] + h[0]) * 0.5, z: (g[2] + h[2]) * 0.5 }, span: 460, yaw: 0, immediate: true });
                }"""
            )
            wait_rendered_frames(page, 2)
            l1_state = page.evaluate(
                """() => {
                    const st = window.__SCV_INTERACTION__.getState();
                    const labels = [...document.querySelectorAll('.ground-mark')].filter(el => el.style.display !== 'none').map(el => el.dataset.unitId);
                    return { overlay: st.overlay, labels };
                }"""
            )
            must(l1_state["overlay"]["tier"] == "compact" and l1_state["overlay"]["iconCount"] >= 2, f"L1 (460m) 应绘制紧凑剪影图标: {l1_state}")
            must(set(l1_state["labels"]).issubset({"US_M1A2_01"}), f"L1 仅选中/悬停单位保留 DOM 名牌，实际 {l1_state['labels']}")
            ah_node_l1 = next((n for n in l1_state["overlay"]["nodes"] if "US_AH64D_01" in n["unitIds"]), None)
            must(ah_node_l1 is not None, f"L1 应能找到 US_AH64D_01 图标节点: {l1_state}")
            click_pt = page.evaluate(
                "([x, y]) => { const r = document.getElementById('canvas-host').getBoundingClientRect(); return { cx: r.left + x, cy: r.top + y }; }",
                [ah_node_l1["x"], ah_node_l1["y"]],
            )
            page.mouse.click(click_pt["cx"], click_pt["cy"])
            wait_rendered_frames(page, 2)
            sel_after_l1_click = page.evaluate("() => window.__SCV_INTERACTION__.getState().selectedIds")
            must("US_AH64D_01" in sel_after_l1_click, f"L1 点击 2D 图标应命中 US_AH64D_01，实际 {sel_after_l1_click}")
            shot_l1 = out_dir / f"{channel}-08-L1-460m.png"
            page.screenshot(path=str(shot_l1))
            report["screenshots"]["l1Compact"] = str(shot_l1)

            # C3) L2 (span=1200m) 标准筹码 26x18 + 零 DOM 名牌 + 点击图标命中
            page.evaluate(
                """async () => {
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const g = hs.find(u => u.id === 'US_M1A2_01').position;
                    await window.__SCV_INTERACTION__.setView({ focus: { x: g[0], z: g[2] }, span: 1200, yaw: 0, immediate: true });
                }"""
            )
            wait_rendered_frames(page, 2)
            l2_state = page.evaluate(
                """() => ({
                    overlay: window.__SCV_INTERACTION__.getState().overlay,
                    visibleLabels: [...document.querySelectorAll('.ground-mark')].filter(el => el.style.display !== 'none').length,
                })"""
            )
            must(l2_state["overlay"]["tier"] == "chip" and l2_state["overlay"]["iconCount"] >= 2, f"L2 (1200m) 应绘制标准筹码图标: {l2_state}")
            must(l2_state["visibleLabels"] == 0, f"L2 应隐藏全部 DOM 名牌，实际 {l2_state['visibleLabels']}")
            m1_node_l2 = next((n for n in l2_state["overlay"]["nodes"] if "US_M1A2_01" in n["unitIds"]), None)
            must(m1_node_l2 is not None, "L2 应能找到 US_M1A2_01 图标节点")
            m1_click_l2 = page.evaluate(
                "([x, y]) => { const r = document.getElementById('canvas-host').getBoundingClientRect(); return { cx: r.left + x, cy: r.top + y }; }",
                [m1_node_l2["x"], m1_node_l2["y"]],
            )
            page.mouse.click(m1_click_l2["cx"], m1_click_l2["cy"])
            wait_rendered_frames(page, 2)
            must("US_M1A2_01" in page.evaluate("() => window.__SCV_INTERACTION__.getState().selectedIds"), "L2 点击 2D 筹码应选中 US_M1A2_01")
            shot_l2 = out_dir / f"{channel}-09-L2-1200m.png"
            page.screenshot(path=str(shot_l2))
            report["screenshots"]["l2Chip"] = str(shot_l2)

            # C4) L3 (span=4500m) 极简战略点标 18x14 + 零 DOM 名牌
            page.evaluate("async () => await window.__SCV_INTERACTION__.setView({ focus: { x: 0, z: 85 }, span: 4500, yaw: 0, immediate: true })")
            wait_rendered_frames(page, 2)
            l3_state = page.evaluate(
                """() => ({
                    overlay: window.__SCV_INTERACTION__.getState().overlay,
                    visibleLabels: [...document.querySelectorAll('.ground-mark')].filter(el => el.style.display !== 'none').length,
                })"""
            )
            must(l3_state["overlay"]["tier"] == "overview" and l3_state["overlay"]["iconCount"] >= 2, f"L3 (4500m) 应绘制战略点标: {l3_state}")
            must(l3_state["visibleLabels"] == 0, f"L3 应隐藏全部 DOM 名牌，实际 {l3_state['visibleLabels']}")
            shot_l3 = out_dir / f"{channel}-10-L3-4500m.png"
            page.screenshot(path=str(shot_l3))
            report["screenshots"]["l3Overview"] = str(shot_l3)

            # C5) Stage 1B：固定翼贴地主范围圈 + 无 234m 空中大圈 + 圆周投影对齐 + 空中近景俯仰滚转检查
            a10_cam = page.evaluate(
                """async () => {
                    window.__SCV_INTERACTION__.setSelection(['US_A10C_01']);
                    const st = window.__SCV_INTERACTION__.getState();
                    const a10 = st.units.find(u => u.id === 'US_A10C_01');
                    const oc = a10.orbitCenter;
                    await window.__SCV_INTERACTION__.setView({ focus: { x: oc.x, z: oc.z - 95 }, span: 640, yaw: 0, immediate: true });
                    return oc;
                }"""
            )
            wait_rendered_frames(page, 3)
            a10_info = page.evaluate(
                """(oc) => {
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const a10v = hs.find(u => u.id === 'US_A10C_01');
                    return {
                        oc,
                        pos: a10v.position,
                        anim: a10v.anim,
                        slots: a10v.slotsPresent,
                        overlay: window.__SCV_INTERACTION__.getState().overlay,
                    };
                }""",
                a10_cam,
            )
            dist_from_oc = math.hypot(a10_info["pos"][0] - a10_info["oc"]["x"], a10_info["pos"][2] - a10_info["oc"]["z"])
            must(a10_info["overlay"]["hasHighOrbitTrack"] is False, "固定翼严禁再绘制 234m 空中完整大圈")
            must(a10_info["overlay"]["fixedWingGroundZones"] >= 1, "固定翼应绘制贴地主范围圈")
            must(abs(dist_from_oc - 150.0) < 3.0 and abs(a10_info["pos"][1] - 215.0) < 1.0, f"固定翼高度或盘旋半径未精确落圆周: pos={a10_info['pos']}, dist={dist_from_oc}")
            must(abs(a10_info["anim"]["bankRoll"]) > 0.35, f"US_A10C_01 盘旋时滚转角应明显非零 (>0.35 rad)，实际 {a10_info['anim']}")
            shot_a10 = out_dir / f"{channel}-11-A10-ground-orbit.png"
            page.screenshot(path=str(shot_a10))
            report["screenshots"]["a10GroundOrbit"] = str(shot_a10)

            # 同步抓取一张 A-10 空中 L0 近景图，直视检查 35° 机头导引虚弧、锁框与压坡度滚转角
            page.evaluate(
                """async () => {
                    const a10v = window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_A10C_01');
                    await window.__SCV_INTERACTION__.setView({ focus: { x: a10v.position[0], z: a10v.position[2] - 210 }, span: 150, yaw: 0, immediate: true });
                }"""
            )
            wait_rendered_frames(page, 2)
            shot_a10_air = out_dir / f"{channel}-11b-A10-air-bank.png"
            page.screenshot(path=str(shot_a10_air))
            report["screenshots"]["a10AirBank"] = str(shot_a10_air)

            # C6) Stage 2：AH-64D 旋翼角跨帧递增 + M1A2 炮塔攻地转向 + M1A2/AH64D .scvveh 部件就绪
            rotor_samples = []
            for _ in range(3):
                rotor_samples.append(
                    page.evaluate(
                        """() => {
                            const h = window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_AH64D_01');
                            return { main: h.anim.mainRotorAngle, tail: h.anim.tailRotorAngle, slots: h.slotsPresent };
                        }"""
                    )
                )
                wait_rendered_frames(page, 1)
            d_rotor_1 = (rotor_samples[1]["main"] - rotor_samples[0]["main"] + 2 * math.pi) % (2 * math.pi)
            d_rotor_2 = (rotor_samples[2]["main"] - rotor_samples[1]["main"] + 2 * math.pi) % (2 * math.pi)
            must("main_rotor" in rotor_samples[0]["slots"] and "tail_rotor" in rotor_samples[0]["slots"], f"US_AH64D_01 应加载 .scvveh 旋翼部件: {rotor_samples[0]['slots']}")
            must(d_rotor_1 > 0.2 and d_rotor_2 > 0.2, f"US_AH64D_01 主旋翼角应跨帧持续递增: {rotor_samples}")

            # 测试 M1A2 炮塔向右侧攻地意图坐标平滑转向
            m1_turret_res = page.evaluate(
                """() => {
                    const hs = window.__SCV_MAP__.getHeroVehiclesState();
                    const m1 = hs.find(u => u.id === 'US_M1A2_01');
                    const yaw = m1.yaw || 0;
                    // 在车身右侧 90 度方向放置强制攻地目标
                    const aimX = m1.position[0] + Math.cos(yaw) * 90;
                    const aimZ = m1.position[2] - Math.sin(yaw) * 90;
                    window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'attackGround', point: { x: aimX, z: aimZ } });
                    return { beforeTurret: m1.anim.turretYaw, slots: m1.slotsPresent };
                }"""
            )
            page.wait_for_function(
                "(prevYaw) => { const m1 = window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_M1A2_01'); return m1 && Math.abs(m1.anim.turretYaw - prevYaw) > 0.45 && m1.anim.barrelPitch > 0.06; }",
                arg=m1_turret_res["beforeTurret"],
                timeout=15000,
            )
            m1_after_turret = page.evaluate(
                "() => window.__SCV_MAP__.getHeroVehiclesState().find(u => u.id === 'US_M1A2_01').anim"
            )
            must("turret" in m1_turret_res["slots"] and "barrel" in m1_turret_res["slots"], f"US_M1A2_01 应加载 .scvveh 炮塔与炮管部件: {m1_turret_res['slots']}")
            must(abs(m1_after_turret["turretYaw"] - m1_turret_res["beforeTurret"]) > 0.4, f"M1A2 炮塔应平滑转向攻地坐标: {m1_turret_res['beforeTurret']} -> {m1_after_turret['turretYaw']}")
            must(m1_after_turret["barrelPitch"] > 0.06, f"M1A2 炮管应同步微仰角: {m1_after_turret['barrelPitch']}")

            # 再在 L0 拍一张 M1A2 攻地转向炮塔的特写图方便肉眼审图！
            focus_unit_safe(page, "US_M1A2_01", span=120.0)
            wait_rendered_frames(page, 2)
            shot_m1_turret = out_dir / f"{channel}-12-M1A2-turret-aim.png"
            page.screenshot(path=str(shot_m1_turret))
            report["screenshots"]["m1a2TurretAim"] = str(shot_m1_turret)

            report["checks"]["stage1AndStage2"] = {
                "l0": l0_state,
                "l1Tier": l1_state["overlay"]["tier"],
                "l2Tier": l2_state["overlay"]["tier"],
                "l3Tier": l3_state["overlay"]["tier"],
                "a10BankRoll": a10_info["anim"]["bankRoll"],
                "ah64dRotorSamples": rotor_samples,
                "m1a2TurretTransition": [m1_turret_res["beforeTurret"], m1_after_turret["turretYaw"], m1_after_turret["barrelPitch"]],
            }

            audio_creations_final = page.evaluate("() => window.__SCV_AUDIO_CREATIONS__ || 0")
            must(audio_creations_final == 0, f"全流程载具交互应保持零 AudioContext，实际 {audio_creations_final}")

            must(len(report["errors"]) == 0, f"控制台/页面出现报错: {report['errors']}")
            ctx_b.close()

            # =========================================================
            # Phase D (隔离上下文): L2 同通道 3 车密集簇聚合 (×3 徽标与左键点击全簇选中)
            # =========================================================
            ctx_c = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.25)
            page_c = ctx_c.new_page()
            attach_error_listeners(page_c, "cluster-x3")
            page_c.add_init_script(
                "localStorage.setItem('scavenger.roster.v1', JSON.stringify(['US_M1A2', 'US_LAV25', 'US_M2A3', 'US_AH64D', 'US_A10']));"
            )
            page_c.goto(f"{base_url}/play", wait_until="domcontentloaded", timeout=30000)
            page_c.wait_for_function("() => window.__SCV_MAP__?.isCoreReady?.() === true && Boolean(window.__SCV_INTERACTION__)", timeout=30000)
            page_c.wait_for_timeout(400)
            cluster_info = page_c.evaluate(
                """async () => {
                    // 将 3 辆地面载具近距离聚拢并在 L2 (span=1500m) 观察 ×3 聚簇
                    window.__SCV_MAP__.resetHeroVehiclePos('US_M1A2_01', 0, 85, 0);
                    window.__SCV_MAP__.resetHeroVehiclePos('US_LAV25_01', 6, 87, 0);
                    window.__SCV_MAP__.resetHeroVehiclePos('US_M2A3_01', -6, 84, 0);
                    window.__SCV_INTERACTION__.setSelection(['US_A10C_01']);
                    await window.__SCV_INTERACTION__.setView({ focus: { x: 0, z: 85 }, span: 1500, yaw: 0, immediate: true });
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                    return window.__SCV_INTERACTION__.getState().overlay;
                }"""
            )
            page_c.wait_for_timeout(150)
            cluster_info = page_c.evaluate("() => window.__SCV_INTERACTION__.getState().overlay")
            ground_cluster = next((n for n in cluster_info["nodes"] if n["isCluster"] and n["channel"] == "ground" and n["count"] == 3), None)
            must(ground_cluster is not None, f"3 辆紧邻地面载具在 L2 应聚合为 ×3 簇: {cluster_info}")
            cl_pt = page_c.evaluate(
                "([x, y]) => { const r = document.getElementById('canvas-host').getBoundingClientRect(); return { cx: r.left + x, cy: r.top + y }; }",
                [ground_cluster["x"], ground_cluster["y"]],
            )
            page_c.mouse.click(cl_pt["cx"], cl_pt["cy"])
            page_c.wait_for_timeout(120)
            sel_cluster = page_c.evaluate("() => window.__SCV_INTERACTION__.getState().selectedIds")
            must(set(sel_cluster) == {"US_M1A2_01", "US_LAV25_01", "US_M2A3_01"}, f"左键点击 ×3 簇应选中簇内全部 3 辆地面载具，实际 {sel_cluster}")
            shot_cluster = out_dir / f"{channel}-13-L2-cluster-x3.png"
            page_c.screenshot(path=str(shot_cluster))
            report["screenshots"]["l2ClusterX3"] = str(shot_cluster)
            report["checks"]["l2ClusterSelection"] = {"cluster": ground_cluster, "selectedIds": sel_cluster}
            must(len(report["errors"]) == 0, f"簇聚合上下文出现报错: {report['errors']}")
            ctx_c.close()

            # =========================================================
            # Phase E (新隔离上下文): 载具 3D 空间引擎音效、视距衰减、暂停与 /settings 验证
            # =========================================================
            ctx_e = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.25)
            ctx_e.add_init_script(
                """
                window.__SCV_AUDIO_CREATIONS_E__ = 0;
                const OrigAC = window.AudioContext;
                const OrigWAC = window.webkitAudioContext;
                if (OrigAC) {
                    window.AudioContext = function(...args) {
                        window.__SCV_AUDIO_CREATIONS_E__ += 1;
                        return new OrigAC(...args);
                    };
                }
                if (OrigWAC) {
                    window.webkitAudioContext = function(...args) {
                        window.__SCV_AUDIO_CREATIONS_E__ += 1;
                        return new OrigWAC(...args);
                    };
                }
                """
            )
            page_e = ctx_e.new_page()
            attach_error_listeners(page_e, "spatial-audio")

            page_e.goto(f"{base_url}/play", wait_until="domcontentloaded", timeout=30000)
            page_e.wait_for_function("() => window.__SCV_MAP__?.isCoreReady?.() === true && Boolean(window.__SCV_INTERACTION__)", timeout=30000)
            page_e.wait_for_timeout(300)

            pre_gesture = page_e.evaluate("() => ({ audio: window.__SCV_MAP__.getAudioState(), creations: window.__SCV_AUDIO_CREATIONS_E__ || 0 })")
            must(pre_gesture["audio"]["enabled"] is True, f"默认应开启载具引擎环境音: {pre_gesture}")
            must(pre_gesture["audio"]["contextCreated"] is False and pre_gesture["creations"] == 0, f"未手势激活前严禁提前创建 AudioContext: {pre_gesture}")

            # 模拟真实点击手势解锁，激活默认 3 车空间引擎音
            gx_a, gy_a, _ = find_safe_ground_point(page_e, 0, 0)
            page_e.mouse.click(gx_a, gy_a)
            page_e.wait_for_function(
                "() => { const a = window.__SCV_MAP__.getAudioState(); return a.unlocked && a.mapLoaded && a.activeEngineCount >= 1 && a.activeEngines.some(e => e.playing); }",
                timeout=15000,
            )
            page_e.wait_for_timeout(350)
            near_audio = page_e.evaluate("() => ({ audio: window.__SCV_MAP__.getAudioState(), creations: window.__SCV_AUDIO_CREATIONS_E__ || 0 })")
            must(near_audio["creations"] == 1, f"仅应创建 1 个载具空间音效上下文（无 UI 提示音上下文）: {near_audio}")
            must(1 <= near_audio["audio"]["activeEngineCount"] <= 8, f"活跃引擎声部数应在 1..8 之间: {near_audio['audio']}")
            must(near_audio["audio"]["cachedLoopsCount"] <= 6, f"仅懒加载当前出击编队，缓存数应 <=6（实际 {near_audio['audio']['cachedLoopsCount']}）")
            must(near_audio["audio"]["spanGain"] == 1.0 and near_audio["audio"]["effectiveMasterGain"] > 0, f"近景 135m 有效增益应 > 0: {near_audio['audio']}")

            # 拉远至 span=2500m 总览验证衰减归零
            page_e.evaluate("async () => await window.__SCV_INTERACTION__.setView({ focus: { x: 0, z: 85 }, span: 2500, yaw: 0, immediate: true })")
            page_e.wait_for_function("() => { const a = window.__SCV_MAP__.getAudioState(); return a.spanGain === 0 && a.effectiveMasterGain === 0 && a.activeEngineCount === 0; }", timeout=8000)
            far_audio = page_e.evaluate("() => window.__SCV_MAP__.getAudioState()")
            must(far_audio["rms"] == 0, f"总览 2500m 下空间引擎混音 RMS 应归零: {far_audio}")

            # 拉回近景并切到 100 混编交战验证 8 声部硬上限与三兵种保底
            page_e.evaluate("async () => await window.__SCV_INTERACTION__.setView({ focus: { x: 0, z: 85 }, span: 135, yaw: 0, immediate: true })")
            page_e.click("#btn-toggle-editor")
            page_e.click("#btn-load-100")
            page_e.click("#btn-close-editor")
            page_e.wait_for_function("() => window.__SCV_MAP__.getAudioState().activeEngineCount >= 3", timeout=10000)
            page_e.wait_for_timeout(350)
            mix100_audio = page_e.evaluate("() => window.__SCV_MAP__.getAudioState()")
            mix_kinds = {e["kind"] for e in mix100_audio["activeEngines"]}
            must(mix100_audio["activeEngineCount"] <= 8, f"100 混编下活跃声部严禁超过 8: {mix100_audio}")
            must({"ground", "heli", "plane"}.issubset(mix_kinds), f"100 混编三兵种应各保底至少 1 路声部: {mix_kinds}")

            # 切回 G01，ESC 打开暂停菜单验证自动静音 + 开关与音量调节 + 16:9 / 21:9 截图
            page_e.click("#btn-toggle-editor")
            page_e.click("#btn-load-g01")
            page_e.click("#btn-close-editor")
            page_e.keyboard.press("Escape")
            page_e.wait_for_timeout(200)
            paused_audio = page_e.evaluate("() => ({ hud: window.__SCV_MAP__.getUiHudState(), audio: window.__SCV_MAP__.getAudioState(), nativeSelects: document.querySelectorAll('select').length })")
            must(paused_audio["hud"]["paused"] is True and paused_audio["audio"]["effectiveMasterGain"] == 0 and paused_audio["audio"]["rms"] == 0, f"暂停时引擎混音应自动静音: {paused_audio}")
            must(paused_audio["nativeSelects"] == 0, "暂停菜单不得出现原生 <select>")
            shot_pause_16x9 = out_dir / f"{channel}-14a-play-pause-16x9.png"
            page_e.screenshot(path=str(shot_pause_16x9))
            report["screenshots"]["playPause16x9"] = str(shot_pause_16x9)

            # 切换 21:9 (2560x1080) 截取暂停菜单
            page_e.set_viewport_size({"width": 2560, "height": 1080})
            page_e.click("#pause-aspect-btn")
            page_e.wait_for_timeout(220)
            shot_pause_21x9 = out_dir / f"{channel}-14b-play-pause-21x9.png"
            page_e.screenshot(path=str(shot_pause_21x9))
            report["screenshots"]["playPause21x9"] = str(shot_pause_21x9)

            # 在暂停菜单切换画幅 (21:9 -> full -> 16:9) 并切换载具引擎音关->开及调音量，再 ESC 恢复验证
            page_e.set_viewport_size({"width": 1600, "height": 900})
            page_e.click("#pause-aspect-btn")
            page_e.click("#pause-aspect-btn")
            page_e.click("#pause-veh-audio-toggle")
            off_checked = page_e.get_attribute("#pause-veh-audio-toggle", "aria-checked")
            must(off_checked == "false", f"暂停菜单点击关闭后 aria-checked 应为 false，实际 {off_checked}")
            page_e.click("#pause-veh-audio-toggle")
            on_checked = page_e.get_attribute("#pause-veh-audio-toggle", "aria-checked")
            must(on_checked == "true", f"暂停菜单再次点击开启后 aria-checked 应为 true，实际 {on_checked}")
            page_e.evaluate("() => { const r = document.getElementById('pause-veh-vol-range'); r.value = '60'; r.dispatchEvent(new Event('input', { bubbles: true })); }")
            page_e.keyboard.press("Escape")
            page_e.wait_for_function(
                "() => { const a = window.__SCV_MAP__.getAudioState(); return !a.paused && a.activeEngineCount >= 1 && a.activeEngines.some(e => e.playing); }",
                timeout=10000,
            )
            page_e.wait_for_timeout(300)
            resumed_audio = page_e.evaluate("() => window.__SCV_MAP__.getAudioState()")
            must(
                resumed_audio["paused"] is False
                and resumed_audio["effectiveMasterGain"] > 0
                and resumed_audio["activeEngineCount"] >= 1
                and abs(resumed_audio["volume"] - 0.6) < 0.02,
                f"恢复游戏后空间音效与新音量应生效: {resumed_audio}",
            )

            # 校验 /settings 独立载具引擎音设置页（无原生 <select>、持久化及截图）
            page_e.goto(f"{base_url}/settings", wait_until="domcontentloaded", timeout=30000)
            page_e.wait_for_function("() => window.__SCV_APP__?.ready === true", timeout=20000)
            page_e.wait_for_timeout(150)
            set_before = page_e.evaluate("() => window.__SCV_APP__.getPrefs()")
            must(abs(set_before["vehicleAudioVolume"] - 0.6) < 0.02, f"/play 暂停菜单调整的音量应持久化到 /settings: {set_before}")
            page_e.click("#set-veh-audio-toggle")
            set_off = page_e.evaluate("() => window.__SCV_APP__.getPrefs()")
            must(set_off["vehicleAudioEnabled"] is False, f"/settings 开关应持久化为 false: {set_off}")
            page_e.click("#set-reset-prefs")
            set_reset = page_e.evaluate("() => window.__SCV_APP__.getPrefs()")
            must(set_reset["vehicleAudioEnabled"] is True and abs(set_reset["vehicleAudioVolume"] - 0.45) < 0.01, f"恢复默认应重置载具引擎音偏好: {set_reset}")
            must(page_e.evaluate("() => document.querySelectorAll('select').length") == 0, "/settings 不得存在原生 <select>")
            shot_settings = out_dir / f"{channel}-15-settings-vehicle-audio.png"
            page_e.screenshot(path=str(shot_settings))
            report["screenshots"]["settingsVehicleAudio"] = str(shot_settings)

            report["checks"]["spatialVehicleAudio"] = {
                "preGestureContextCreated": pre_gesture["audio"]["contextCreated"],
                "near": near_audio["audio"],
                "far2500m": far_audio,
                "mix100": mix100_audio,
                "resumedAfterPause": resumed_audio,
            }
            must(len(report["errors"]) == 0, f"空间音效与设置上下文出现报错: {report['errors']}")
            ctx_e.close()

            # =========================================================
            # Phase F (4项体验与性能精修专项):
            # ① 固定翼直线飞 airTurnArcCount===0 vs 盘旋态 >=1
            # ② US_AH64D_01 按 F 3D 机身居中偏差 < 25px
            # ③ US_A10C_01 按 F 居中 & G 跟随旋转后机身居中偏差 < 30px
            # ④ B 键 129 车部署面板：搜索、一键部署、落点部署、移除选中与重置恢复
            # ⑤ 1208×680 与 2560×1080 顶栏不折行无重叠
            # =========================================================
            ctx_f = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.25)
            page_f = ctx_f.new_page()
            attach_error_listeners(page_f, "polish-4tasks")
            page_f.goto(f"{base_url}/play?aspect=16:9", wait_until="domcontentloaded", timeout=30000)
            page_f.wait_for_function("() => window.__SCV_MAP__?.isCoreReady?.() === true && Boolean(window.__SCV_INTERACTION__)", timeout=30000)
            wait_rendered_frames(page_f, 2)

            def settle_camera_and_render(pg, max_steps=28, dt_sim=0.001):
                return pg.evaluate(
                    """([maxSteps, dtSim]) => {
                        for (let i = 0; i < maxSteps; i++) {
                            window.__SCV_INTERACTION__.step(45, performance.now());
                            const v = window.__SCV_INTERACTION__.getState().view;
                            if (
                                Math.hypot(v.focus.x - v.target.x, v.focus.z - v.target.z) < 0.08 &&
                                Math.abs(v.span - v.target.span) < 0.15 &&
                                Math.abs(v.yaw - v.target.angle) < 0.002
                            ) {
                                break;
                            }
                        }
                        window.__SCV_MAP__.stepSimAndRender(dtSim, performance.now() * 0.001);
                        return window.__SCV_INTERACTION__.getState().view;
                    }""",
                    [max_steps, dt_sim],
                )

            # F1) 固定翼盘旋态 airTurnArcCount >= 1 vs 直线飞行切入态 airTurnArcCount === 0
            orbit_arc_state = page_f.evaluate(
                """() => {
                    window.__SCV_INTERACTION__.setSelection(['US_A10C_01']);
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                    return window.__SCV_INTERACTION__.getState().overlay;
                }"""
            )
            must(orbit_arc_state["airTurnArcCount"] >= 1, f"固定翼绕圈盘旋态应显示 35° 转向弧: {orbit_arc_state}")

            # 下达远距离直线飞行命令，验证直线飞向新盘旋点期间 airTurnArcCount === 0
            straight_arc_state = page_f.evaluate(
                """() => {
                    window.__SCV_INTERACTION__.dispatchCommand(['US_A10C_01'], { type: 'move', point: { x: 260, z: 260 } }, false);
                    window.__SCV_MAP__.stepSimAndRender(0.02, performance.now() * 0.001);
                    return window.__SCV_INTERACTION__.getState();
                }"""
            )
            must(
                straight_arc_state["overlay"]["airTurnArcCount"] == 0,
                f"固定翼直线飞行期间严禁绘制分叉 35° 机头转向弧: {straight_arc_state['overlay']}",
            )
            # 对焦在直线飞行中的 A-10C 机身截取无转向弧清晰验证图
            page_f.keyboard.press("f")
            settle_camera_and_render(page_f, max_steps=30, dt_sim=0.001)
            wait_rendered_frames(page_f, 1)
            straight_check_after_f = page_f.evaluate("() => window.__SCV_INTERACTION__.getState().overlay")
            must(straight_check_after_f["airTurnArcCount"] == 0, f"聚焦直线飞行 A-10C 时 airTurnArcCount 必须为 0: {straight_check_after_f}")
            shot_a10_straight = out_dir / f"{channel}-16-A10-straight-no-arc.png"
            page_f.screenshot(path=str(shot_a10_straight))
            report["screenshots"]["a10StraightNoArc"] = str(shot_a10_straight)

            # F2) US_AH64D_01 按 F 键 3D 视线投影居中聚焦（偏差 < 25px）
            page_f.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_AH64D_01'])")
            page_f.keyboard.press("f")
            settle_camera_and_render(page_f, max_steps=30, dt_sim=0.001)
            ah_center_metrics = page_f.evaluate(
                """() => {
                    const host = document.getElementById('canvas-host');
                    const p = window.__SCV_INTERACTION__.projectUnit('US_AH64D_01');
                    const cx = host.clientWidth * 0.5;
                    const cy = host.clientHeight * 0.5;
                    return {
                        proj: p,
                        center: [cx, cy],
                        errPx: Number(Math.hypot(p.x - cx, p.y - cy).toFixed(2)),
                        span: window.__SCV_INTERACTION__.getState().view.span,
                    };
                }"""
            )
            must(
                ah_center_metrics["proj"]["visible"] and ah_center_metrics["errPx"] < 25.0,
                f"US_AH64D_01 按 F 聚焦后屏幕中心偏差应 < 25px，实际 {ah_center_metrics}",
            )
            shot_ah_centered = out_dir / f"{channel}-17-AH64D-F-centered.png"
            page_f.screenshot(path=str(shot_ah_centered))
            report["screenshots"]["ah64dFCentered"] = str(shot_ah_centered)

            # F3) US_A10C_01 按 F 居中聚焦 (< 30px) 及 G 持续跟随旋转视角后居中 (< 35px)
            page_f.evaluate("() => window.__SCV_INTERACTION__.setSelection(['US_A10C_01'])")
            page_f.keyboard.press("f")
            a10_f_metrics = page_f.evaluate(
                """() => {
                    // 原子化收敛 F 聚焦飞镜并当帧测量，避免未跟随态下跨 IPC rAF 让 135m/s 喷气机提前飞离焦点
                    for (let i = 0; i < 32; i++) {
                        window.__SCV_INTERACTION__.step(45, performance.now());
                        const v = window.__SCV_INTERACTION__.getState().view;
                        if (
                            Math.hypot(v.focus.x - v.target.x, v.focus.z - v.target.z) < 0.08 &&
                            Math.abs(v.span - v.target.span) < 0.15 &&
                            Math.abs(v.yaw - v.target.angle) < 0.002
                        ) {
                            break;
                        }
                    }
                    window.__SCV_MAP__.stepSimAndRender(0.001, performance.now() * 0.001);
                    const host = document.getElementById('canvas-host');
                    const p = window.__SCV_INTERACTION__.projectUnit('US_A10C_01');
                    const cx = host.clientWidth * 0.5;
                    const cy = host.clientHeight * 0.5;
                    return {
                        proj: p,
                        center: [cx, cy],
                        errPx: Number(Math.hypot(p.x - cx, p.y - cy).toFixed(2)),
                        span: window.__SCV_INTERACTION__.getState().view.span,
                    };
                }"""
            )
            must(
                a10_f_metrics["proj"]["visible"] and a10_f_metrics["errPx"] < 30.0 and 180.0 <= a10_f_metrics["span"] <= 320.0,
                f"US_A10C_01 按 F 聚焦后屏幕中心偏差应 < 30px 且 span 处于 [180,320]，实际 {a10_f_metrics}",
            )
            shot_a10_centered = out_dir / f"{channel}-18-A10-F-centered.png"
            page_f.screenshot(path=str(shot_a10_centered))
            report["screenshots"]["a10FCentered"] = str(shot_a10_centered)

            # 开启 G 持续跟随并旋转视角 65 度，验证旋转后机身依然钉在屏幕正中央
            page_f.keyboard.press("g")
            a10_follow_rot_metrics = page_f.evaluate(
                """() => {
                    window.__SCV_INTERACTION__.cameraCtrl.rotateByPixels(-180);
                    for (let i = 0; i < 28; i++) {
                        window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                        window.__SCV_INTERACTION__.step(45, performance.now());
                    }
                    window.__SCV_MAP__.stepSimAndRender(0.001, performance.now() * 0.001);
                    const host = document.getElementById('canvas-host');
                    const p = window.__SCV_INTERACTION__.projectUnit('US_A10C_01');
                    const cx = host.clientWidth * 0.5;
                    const cy = host.clientHeight * 0.5;
                    return {
                        followUnitId: window.__SCV_INTERACTION__.getState().followUnitId,
                        yaw: window.__SCV_INTERACTION__.getState().view.yaw,
                        errPx: Number(Math.hypot(p.x - cx, p.y - cy).toFixed(2)),
                    };
                }"""
            )
            must(
                a10_follow_rot_metrics["followUnitId"] == "US_A10C_01" and a10_follow_rot_metrics["errPx"] < 32.0,
                f"US_A10C_01 开启 G 跟随并旋转视角后机身应保持在屏幕中心 (<32px)，实际 {a10_follow_rot_metrics}",
            )
            page_f.keyboard.press("g")

            # F4) 按 B 键展开 129 车部署面板、搜索、一键部署 + 落点部署、移除与重置
            page_f.evaluate("async () => await window.__SCV_INTERACTION__.setView({ focus: { x: 5, z: 85 }, span: 155, yaw: 0, immediate: true })")
            wait_rendered_frames(page_f, 1)
            page_f.keyboard.press("b")
            page_f.wait_for_function("() => window.__SCV_MAP__.getDeployCatalogState().open === true && window.__SCV_MAP__.getDeployCatalogState().totalCount === 129", timeout=15000)
            cat_init = page_f.evaluate("() => window.__SCV_MAP__.getDeployCatalogState()")
            must(cat_init["totalCount"] == 129 and cat_init["heroCount"] == 3, f"部署抽屉应加载全部 129 车目录: {cat_init}")

            # 在搜索框输入 M2A3（同时验证输入框内按 b/f 等字母绝不误触关闭抽屉）
            page_f.fill("#deploy-search", "M2A3")
            page_f.wait_for_timeout(100)
            cat_filtered = page_f.evaluate("() => window.__SCV_MAP__.getDeployCatalogState()")
            must(cat_filtered["open"] is True and 1 <= cat_filtered["filteredCount"] <= 10, f"搜索 M2A3 应过滤出对应车型且抽屉保持开启: {cat_filtered}")

            # 点击一键「部署」按钮部署 US_M2A3
            page_f.click('button[data-deploy-now="US_M2A3"]')
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 4", timeout=15000)

            # 清空搜索框，使用「落点」模式在地图左键点击部署 US_LAV_AD
            page_f.fill("#deploy-search", "")
            page_f.wait_for_timeout(80)
            page_f.click('button[data-deploy-pick="US_LAV_AD"]')
            mode_pick = page_f.evaluate("() => window.__SCV_INTERACTION__.getState()")
            must(mode_pick["mode"] == "deploy" and mode_pick["deploySlug"] == "US_LAV_AD" and mode_pick["cursor"] == "deploy", f"点击落点按钮应进入 deploy 模式: {mode_pick}")

            gx_d, gy_d, _ = find_safe_ground_point(page_f, -40, -20)
            page_f.mouse.click(gx_d, gy_d)
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 5", timeout=20000)
            wait_rendered_frames(page_f, 2)

            after_deploy = page_f.evaluate(
                """() => ({
                    hud: window.__SCV_MAP__.getUiHudState(),
                    deploy: window.__SCV_MAP__.getDeployCatalogState(),
                    mode: window.__SCV_INTERACTION__.getState().mode,
                    heroes: window.__SCV_MAP__.getHeroVehiclesState().map(u => ({ id: u.id, slots: u.slotsPresent })),
                    quotaText: document.getElementById('deploy-quota-badge')?.textContent,
                    nativeSelects: document.querySelectorAll('select').length,
                })"""
            )
            must(
                after_deploy["hud"]["heroCount"] == 5
                and after_deploy["mode"] == "play"
                and after_deploy["quotaText"] == "5 / 50"
                and after_deploy["nativeSelects"] == 0,
                f"一键+落点部署后场内应有 5 辆载具且回到 play 模式: {after_deploy}",
            )
            shot_deploy_b = out_dir / f"{channel}-19-deploy-drawer-B.png"
            page_f.screenshot(path=str(shot_deploy_b))
            report["screenshots"]["deployDrawerB"] = str(shot_deploy_b)

            # 测试「移除选中」与「重置默认 3 车」
            page_f.click("#btn-deploy-remove-sel")
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 4", timeout=10000)
            page_f.click("#btn-deploy-reset-default")
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 3", timeout=15000)
            after_reset = page_f.evaluate("() => window.__SCV_MAP__.getHeroVehiclesState().map(u => u.id)")
            must(after_reset == ["US_M1A2_01", "US_AH64D_01", "US_A10C_01"], f"重置默认 3 车应恢复初始编队: {after_reset}")
            page_f.click("#btn-close-deploy")

            # F5) 校验 1208×680 (16:9) 与 2560×1080 (21:9) 下顶栏不折行、不溢出、零碰撞
            topbar_checks = {}
            for vw, vh, asp in [(1208, 680, "16:9"), (2560, 1080, "21:9")]:
                page_f.set_viewport_size({"width": vw, "height": vh})
                page_f.evaluate("(a) => window.__SCV_MAP__.setAspectProfile(a)", asp)
                wait_rendered_frames(page_f, 1)
                tb = page_f.evaluate(
                    """() => {
                        const top = document.getElementById('top-bar');
                        const left = top.querySelector('.scv-top-left').getBoundingClientRect();
                        const center = top.querySelector('.scv-top-center').getBoundingClientRect();
                        const right = top.querySelector('.scv-top-right').getBoundingClientRect();
                        const rightBtns = [...top.querySelectorAll('.scv-top-right > button')].map(b => b.getBoundingClientRect());
                        const sameRow = rightBtns.length >= 5 && rightBtns.every(r => Math.abs(r.top - rightBtns[0].top) < 6);
                        const noOverlap = left.right <= center.left + 1 && center.right <= right.left + 1;
                        return {
                            sameRow,
                            noOverlap,
                            leftRight: Math.round(left.right),
                            centerLeft: Math.round(center.left),
                            centerRight: Math.round(center.right),
                            rightLeft: Math.round(right.left),
                            btnCount: rightBtns.length,
                            btnHeights: rightBtns.map(r => Math.round(r.height)),
                        };
                    }"""
                )
                must(tb["sameRow"] and tb["noOverlap"], f"{vw}x{vh} ({asp}) 顶栏出现折行或碰撞: {tb}")
                topbar_checks[f"{vw}x{vh}_{asp}"] = tb

            # 恢复 1600×900 视口并确保全精度高度场/道路/建筑已烘焙，进行 F6 ~ F8 (HUD 可读性升级 + 弹性卡片坞 + 新跟随闭环)
            page_f.set_viewport_size({"width": 1600, "height": 900})
            page_f.evaluate("async () => { window.__SCV_MAP__.setAspectProfile('16:9'); await window.__SCV_MAP__.waitForFullPrecision(); }")
            wait_rendered_frames(page_f, 2)

            # F6-A) 默认 3 车：1600×900 放大 1:1 军图轮廓 9KM 雷达 (>=240px) + 6 枚大图标无副标题指令舱 + 弹性居中 3 车卡片坞
            default_16x9_hud = page_f.evaluate(
                """() => {
                    const bl = document.querySelector('.scv-bl-wing').getBoundingClientRect();
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const br = document.querySelector('.scv-br-wing').getBoundingClientRect();
                    const mm = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const radarDock = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const radarBtns = [...document.querySelectorAll('#hud-radar-dock .scv-radar-micro-btn')].map(b => b.getBoundingClientRect());
                    const cells = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')];
                    const availCenter = br.left - bl.right;
                    return {
                        mmWidth: Number(mm.width.toFixed(1)),
                        mmHeight: Number(mm.height.toFixed(1)),
                        radarBtnsInside: radarBtns.length >= 5 && radarBtns.every(r => r.left >= radarDock.left - 1 && r.right <= radarDock.right + 1),
                        deckWidth: Math.round(deck.width),
                        deckHeight: Math.round(deck.height),
                        availCenter: Math.round(availCenter),
                        rightDockWidth: Math.round(br.width),
                        cmdCount: cells.length,
                        cmdSvgCount: cells.filter(c => Boolean(c.querySelector('svg'))).length,
                        cmdSubCount: document.querySelectorAll('#hud-cmd-grid .c-sub').length,
                        threeBaysNoOverlap: bl.right <= deck.left + 2 && deck.right <= br.left + 2,
                        minimapStats: window.__SCV_MAP__.getMinimapState().stats,
                    };
                }"""
            )
            mm_stats = default_16x9_hud["minimapStats"]
            must(
                default_16x9_hud["mmWidth"] >= 240
                and abs(default_16x9_hud["mmWidth"] - default_16x9_hud["mmHeight"]) <= 1.5
                and default_16x9_hud["radarBtnsInside"] is True
                and default_16x9_hud["deckHeight"] <= 156
                and default_16x9_hud["deckWidth"] < default_16x9_hud["availCenter"] - 120
                and default_16x9_hud["rightDockWidth"] >= 370
                and default_16x9_hud["cmdCount"] == 6
                and default_16x9_hud["cmdSvgCount"] == 6
                and default_16x9_hud["cmdSubCount"] == 0
                and default_16x9_hud["threeBaysNoOverlap"] is True
                and mm_stats["baseReady"] is True
                and mm_stats["usedMacroTintImage"] is True
                and mm_stats["roadsBaked"] is True
                and mm_stats["roadTrianglesBaked"] > 0
                and mm_stats["staticBuildings9kmCount"] > 0
                and mm_stats["staticBuildings3kmCount"] > mm_stats["staticBuildings9kmCount"] * 2,
                f"1600x900 军图雷达/大图标指令舱/弹性3车卡片坞校验失败: {default_16x9_hud}",
            )
            # 代表图 1：1600×900 清晰地形路网 9KM 雷达 + 大图标指令舱 + 弹性 3 车卡片坞
            shot_hud_16x9_elastic = out_dir / f"{channel}-20-hud-16x9-radar-cmd-elastic.png"
            page_f.screenshot(path=str(shot_hud_16x9_elastic))
            report["screenshots"]["hud16x9RadarCmdElastic"] = str(shot_hud_16x9_elastic)

            # F6-B) 2560×1080 宽屏默认 3 车：272px+ 放大 1:1 雷达 + 居中弹性卡片坞（告别长黑条）+ 420px 大图标指令舱
            page_f.set_viewport_size({"width": 2560, "height": 1080})
            page_f.evaluate("() => window.__SCV_MAP__.setAspectProfile('full')")
            wait_rendered_frames(page_f, 2)
            wide_3unit_elastic = page_f.evaluate(
                """() => {
                    const host = document.getElementById('canvas-host').getBoundingClientRect();
                    const bl = document.querySelector('.scv-bl-wing').getBoundingClientRect();
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const br = document.querySelector('.scv-br-wing').getBoundingClientRect();
                    const mm = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const cells = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')];
                    const availCenter = br.left - bl.right;
                    return {
                        baseline: window.__SCV_MAP__.getRenderBaseline(),
                        hostW: Math.round(host.width),
                        hostH: Math.round(host.height),
                        mmWidth: Number(mm.width.toFixed(1)),
                        mmHeight: Number(mm.height.toFixed(1)),
                        deckWidth: Math.round(deck.width),
                        deckHeight: Math.round(deck.height),
                        availCenter: Math.round(availCenter),
                        rightDockWidth: Math.round(br.width),
                        cmdSvgCount: cells.filter(c => Boolean(c.querySelector('svg'))).length,
                        cmdSubCount: document.querySelectorAll('#hud-cmd-grid .c-sub').length,
                        threeBaysNoOverlap: bl.right <= deck.left + 2 && deck.right <= br.left + 2,
                    };
                }"""
            )
            must(
                wide_3unit_elastic["mmWidth"] >= 268
                and abs(wide_3unit_elastic["mmWidth"] - wide_3unit_elastic["mmHeight"]) <= 1.5
                and wide_3unit_elastic["rightDockWidth"] >= 400
                and wide_3unit_elastic["deckHeight"] <= 156
                and 300 <= wide_3unit_elastic["deckWidth"] < wide_3unit_elastic["availCenter"] - 200
                and wide_3unit_elastic["cmdSvgCount"] == 6
                and wide_3unit_elastic["cmdSubCount"] == 0
                and wide_3unit_elastic["threeBaysNoOverlap"] is True,
                f"2560x1080 宽屏放大雷达与弹性3车居中卡片坞校验失败: {wide_3unit_elastic}",
            )
            # 代表图 2：2560×1080 宽屏 272px+ 雷达 + 居中弹性卡片坞 + 420px 大图标指令舱
            shot_wide_elastic = out_dir / f"{channel}-21-wide-2560-elastic-3bay.png"
            page_f.screenshot(path=str(shot_wide_elastic))
            report["screenshots"]["wide2560Elastic3Bay"] = str(shot_wide_elastic)

            # 恢复 1600×900 16:9 进行 5 大分类独立配额 (MAX_HERO_UNITS=50) 与多车弹性撑展 + 横滑 + 3KM 局部建筑放大验证
            page_f.set_viewport_size({"width": 1600, "height": 900})
            page_f.evaluate("() => window.__SCV_MAP__.setAspectProfile('16:9')")
            wait_rendered_frames(page_f, 2)

            # F6-C) 5 大分类独立部署配额 (MAX_HERO_UNITS=50) + 方案 B 多车卡片弹性撑展 + 滚轮横滑 + 3KM 局部精细建筑放大
            page_f.keyboard.press("b")
            page_f.wait_for_function("() => window.__SCV_MAP__.getDeployCatalogState().open === true", timeout=10000)
            quota_burst = page_f.evaluate(
                """async () => {
                    // 默认已有 1 架固定翼 (US_A10C_01)，从 129 车真实目录提取固定翼型号再部署 5 架填满 6 架上限
                    const domPlaneSlugs = [...document.querySelectorAll('#deploy-vehicle-list .scv-deploy-item[data-deploy-cat="plane"]')]
                        .map(el => el.dataset.slug)
                        .filter(Boolean);
                    const p0 = domPlaneSlugs[0] || 'US_A10';
                    const p1 = domPlaneSlugs[1] || 'US_A10';
                    const planeSlugs = [p0, p0, p1, p1, 'US_A10'];
                    const planeResults = [];
                    for (const s of planeSlugs) {
                        const r = await window.__SCV_MAP__.deployVehicle(s);
                        planeResults.push({ slug: s, ok: r?.ok, heroCount: r?.heroCount });
                    }
                    // 第 7 架固定翼必须因本类已满 (6/6) 被拒收
                    const seventhPlane = await window.__SCV_MAP__.deployVehicle('US_A10');
                    // 但其他未满类别仍可正常部署：多部署 8 种不同型号地面/旋翼以真实测试弹性卡片坞撑展与滚轮横向滑动
                    const extraCandidates = [];
                    const seen = new Set(['US_M1A2', 'US_AH64D', 'US_A10', p0, p1]);
                    for (const cat of ['armor', 'ifv', 'support', 'heli']) {
                        const els = [...document.querySelectorAll(`#deploy-vehicle-list .scv-deploy-item[data-deploy-cat="${cat}"]`)];
                        for (const el of els) {
                            const slug = el.dataset.slug;
                            if (slug && !seen.has(slug)) {
                                seen.add(slug);
                                extraCandidates.push(slug);
                                if (extraCandidates.length >= 8) break;
                            }
                        }
                        if (extraCandidates.length >= 8) break;
                    }
                    const extraResults = [];
                    for (const s of ['US_M1A2', 'US_AH64D', ...extraCandidates]) {
                        const r = await window.__SCV_MAP__.deployVehicle(s);
                        extraResults.push({ slug: s, ok: Boolean(r?.ok) });
                    }
                    const summary = window.__SCV_MAP__.getDeployQuotaSummary();
                    return {
                        planeResults,
                        seventhPlane,
                        extraResults,
                        summary,
                    };
                }"""
            )
            must(
                all(r["ok"] for r in quota_burst["planeResults"])
                and quota_burst["seventhPlane"]["ok"] is False
                and "固定翼" in str(quota_burst["seventhPlane"].get("reason", ""))
                and all(r["ok"] for r in quota_burst["extraResults"])
                and quota_burst["summary"]["totalCount"] >= 16
                and quota_burst["summary"]["maxTotal"] == 50
                and quota_burst["summary"]["categories"]["plane"]["count"] == 6
                and quota_burst["summary"]["categories"]["plane"]["isFull"] is True,
                f"分类型 50 车独立配额验证失败: {quota_burst}",
            )
            page_f.click("#btn-close-deploy")
            page_f.evaluate(
                """() => {
                    const heroes = window.__SCV_MAP__.getHeroVehiclesState();
                    window.__SCV_INTERACTION__.setSelection(heroes.slice(0, 4).map(u => u.id));
                    window.__SCV_INTERACTION__.selectionCtrl.saveControlGroup('1');
                    window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 260, z: 85 } }, false);
                    window.__SCV_INTERACTION__.dispatchCommand(['US_AH64D_01'], { type: 'move', point: { x: -160, z: 220 } }, false);
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            wait_rendered_frames(page_f, 2)

            # 检验多车部署后弹性卡片坞自动撑展、滚轮横向滚动防误缩放 3D 与三舱零重叠
            scheme_b_metrics = page_f.evaluate(
                """() => {
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const grid = document.getElementById('hud-cards-grid');
                    const cards = [...grid.querySelectorAll('.scv-unit-card')];
                    const cardRects = cards.map(c => c.getBoundingClientRect());
                    const sameRow = cardRects.length >= 6 && cardRects.every(r => Math.abs(r.top - cardRects[0].top) < 6);

                    // 滚轮事件测试：派发 wheel deltaY=260 到 #hud-cards-grid，应推动 scrollLeft 且相机 span 不变
                    const spanBefore = window.__SCV_INTERACTION__.getState().view.span;
                    const scrollBefore = grid.scrollLeft;
                    grid.dispatchEvent(new WheelEvent('wheel', { deltaY: 260, bubbles: true, cancelable: true }));
                    const scrollAfter = grid.scrollLeft;
                    const spanAfter = window.__SCV_INTERACTION__.getState().view.span;

                    // 复原 scrollLeft=110 以便截图直观展示已横滑的卡片坞与左侧放大 3KM 雷达
                    grid.scrollLeft = 0;

                    const radarDock = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const mmBox = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const radarBtns = [...document.querySelectorAll('#hud-radar-dock .scv-radar-micro-btn')].map(b => b.getBoundingClientRect());
                    const radarBtnsInside = radarBtns.length >= 5 && radarBtns.every(r => r.left >= radarDock.left - 1 && r.right <= radarDock.right + 1);

                    const cgBtns = [...document.querySelectorAll('#hud-cg-strip .scv-cg-btn')];
                    const cgRects = cgBtns.map(b => b.getBoundingClientRect());
                    const cgMinHeight = Math.min(...cgRects.map(r => r.height));
                    const cgSlot1Badge = cgBtns[0]?.querySelector('.scv-cg-badge')?.textContent?.trim();

                    const rightDock = document.querySelector('.scv-right-dock').getBoundingClientRect();
                    const cmdCells = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')].map(c => c.getBoundingClientRect());
                    let cmdNoOverlap = cmdCells.length === 6;
                    for (let i = 0; i < cmdCells.length; i++) {
                        for (let j = i + 1; j < cmdCells.length; j++) {
                            const a = cmdCells[i], b = cmdCells[j];
                            const xOverlap = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                            const yOverlap = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                            if (xOverlap > 1 && yOverlap > 1) cmdNoOverlap = false;
                        }
                    }

                    const blWing = document.querySelector('.scv-bl-wing').getBoundingClientRect();
                    const brWing = document.querySelector('.scv-br-wing').getBoundingClientRect();

                    return {
                        deckWidth: Math.round(deck.width),
                        deckHeight: Math.round(deck.height),
                        cardCount: cards.length,
                        sameRow,
                        gridClientWidth: grid.clientWidth,
                        gridScrollWidth: grid.scrollWidth,
                        scrollBefore,
                        scrollAfter,
                        spanDelta: Number(Math.abs(spanAfter - spanBefore).toFixed(4)),
                        mmWidth: Number(mmBox.width.toFixed(1)),
                        mmHeight: Number(mmBox.height.toFixed(1)),
                        radarBtnsInside,
                        cgSlotCount: cgBtns.length,
                        cgMinHeight: Number(cgMinHeight.toFixed(1)),
                        cgSlot1Badge,
                        rightDockWidth: Math.round(rightDock.width),
                        cmdMinCellWidth: Math.round(Math.min(...cmdCells.map(c => c.width))),
                        cmdNoOverlap,
                        threeBaysNoOverlap: blWing.right <= deck.left + 2 && deck.right <= brWing.left + 2,
                        deckState: window.__SCV_MAP__.getCardDeckState(),
                        minimapState: window.__SCV_MAP__.getMinimapState(),
                    };
                }"""
            )
            must(
                scheme_b_metrics["deckHeight"] <= 156
                and scheme_b_metrics["deckWidth"] > default_16x9_hud["deckWidth"] + 200
                and scheme_b_metrics["cardCount"] >= 8
                and scheme_b_metrics["sameRow"] is True
                and scheme_b_metrics["gridScrollWidth"] > scheme_b_metrics["gridClientWidth"]
                and scheme_b_metrics["scrollAfter"] > scheme_b_metrics["scrollBefore"]
                and scheme_b_metrics["spanDelta"] < 0.01
                and scheme_b_metrics["mmWidth"] >= 240
                and abs(scheme_b_metrics["mmWidth"] - scheme_b_metrics["mmHeight"]) <= 1.5
                and scheme_b_metrics["radarBtnsInside"] is True
                and scheme_b_metrics["cgSlotCount"] == 6
                and scheme_b_metrics["cgMinHeight"] >= 24.0
                and scheme_b_metrics["cgSlot1Badge"] == "×4"
                and scheme_b_metrics["rightDockWidth"] >= 370
                and scheme_b_metrics["cmdMinCellWidth"] >= 95
                and scheme_b_metrics["cmdNoOverlap"] is True
                and scheme_b_metrics["threeBaysNoOverlap"] is True
                and scheme_b_metrics["minimapState"]["stats"]["baseReady"] is True
                and scheme_b_metrics["minimapState"]["stats"]["usedMacroTintImage"] is True,
                f"多车弹性撑展卡片坞 + 左下 1:1 真实 9km 雷达校验失败: {scheme_b_metrics}",
            )

            # 雷达 3KM 局部精细建筑放大 + 视角旋转 + 展开多车 ×N 外部上弹浮卡防裁切 + 卡片横滑代表图
            page_f.click("#btn-mm-zoom")
            page_f.evaluate(
                """() => {
                    const cam = window.__SCV_INTERACTION__.cameraCtrl;
                    cam.rotateByPixels(-160);
                    for (let i = 0; i < 20; i++) cam.step(16, performance.now() + i * 16, new Set());
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            pop_trigger_key = page_f.evaluate(
                """() => {
                    const grp = window.__SCV_MAP__.getCardDeckState().groups.find(g => g.count >= 2);
                    return grp ? grp.key : null;
                }"""
            )
            must(pop_trigger_key is not None, "应存在聚合数量 >= 2 的同型载具卡片")
            page_f.click(f'#hud-cards-grid .scv-card-count[data-pop-toggle="{pop_trigger_key}"]')
            wait_rendered_frames(page_f, 2)
            pop_and_radar = page_f.evaluate(
                """() => {
                    const pop = document.getElementById('hud-deck-popover');
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const pRect = pop.getBoundingClientRect();
                    const items = [...pop.querySelectorAll('.scv-pop-row')];
                    const mmSt = window.__SCV_MAP__.getMinimapState();
                    const camAngle = window.__SCV_INTERACTION__.getState().view.yaw;
                    return {
                        popVisible: !pop.hidden && pRect.width > 120 && pRect.height > 40,
                        popAboveDeck: pRect.bottom <= deck.top + 4 && pRect.top >= 0,
                        popItemCount: items.length,
                        zoomMode: mmSt.zoomMode,
                        zoomLabel: document.getElementById('btn-mm-zoom')?.textContent?.trim(),
                        camAngle: Number(camAngle.toFixed(3)),
                    };
                }"""
            )
            must(
                pop_and_radar["popVisible"] is True
                and pop_and_radar["popAboveDeck"] is True
                and pop_and_radar["popItemCount"] >= 2
                and pop_and_radar["zoomMode"] == "3km"
                and pop_and_radar["zoomLabel"] == "3KM"
                and abs(pop_and_radar["camAngle"]) > 0.15,
                f"×N 外部单机浮卡防裁切 + 3KM 旋转雷达校验失败: {pop_and_radar}",
            )
            # 代表图 3：多车部署横滑 + 外部单机浮卡 + 3KM 局部建筑放大旋转黄框
            shot_multi_3km = out_dir / f"{channel}-22-multi-deck-scroll-3km-bldg.png"
            page_f.screenshot(path=str(shot_multi_3km))
            report["screenshots"]["multiDeckScroll3kmBldg"] = str(shot_multi_3km)

            # ESC 一键收起外部浮卡（接入 escStack）+ 航线/网格开关 + 指北针 N 一键回正北向 + 右键雷达跨区下令
            page_f.keyboard.press("Escape")
            page_f.wait_for_timeout(80)
            pop_closed_by_esc = page_f.evaluate("() => document.getElementById('hud-deck-popover').hidden === true")
            must(pop_closed_by_esc is True, "按 Esc 应由 escStack 收起同型单机浮卡")

            page_f.click("#btn-mm-routes")
            page_f.click("#btn-mm-grid")
            layers_off = page_f.evaluate("() => window.__SCV_MAP__.getMinimapState().layers")
            must(layers_off["routes"] is False and layers_off["grid"] is False, f"雷达航线/网格图层应支持关闭: {layers_off}")
            page_f.click("#btn-mm-routes")
            page_f.click("#btn-mm-grid")

            # 点击指北针回正北向
            page_f.click("#btn-mm-north")
            settle_camera_and_render(page_f, max_steps=28, dt_sim=0.005)
            angle_after_north = abs(page_f.evaluate("() => window.__SCV_INTERACTION__.getState().view.yaw"))
            must(angle_after_north < 0.05, f"点击雷达指北针 N 应一键回正北向 (<0.05 rad)，实际 {angle_after_north:.4f}")

            # 切回 9KM 并测试右键雷达直接给选中坦克下达远程机动
            page_f.click("#btn-mm-zoom")
            page_f.evaluate("() => { window.__SCV_INTERACTION__.setSelection(['US_M1A2_01']); window.__SCV_INTERACTION__.cancelUnits(['US_M1A2_01']); }")
            mm_box_f = page_f.locator("#hud-minimap-box").bounding_box()
            must(mm_box_f is not None, "缺少 #hud-minimap-box")
            # (0.53, 0.51) 对应世界坐标约 (270m, 90m) 开阔平地
            page_f.mouse.click(
                mm_box_f["x"] + mm_box_f["width"] * 0.53,
                mm_box_f["y"] + mm_box_f["height"] * 0.51,
                button="right",
            )
            page_f.wait_for_timeout(120)
            m1_after_radar_rc = page_f.evaluate(
                "() => window.__SCV_INTERACTION__.getState().units.find(u => u.id === 'US_M1A2_01')"
            )
            must(
                m1_after_radar_rc is not None and m1_after_radar_rc.get("remainingWaypoints", 0) > 0,
                f"右键点击雷达应直接给选中坦克下达远程机动，实际 {m1_after_radar_rc}",
            )

            # 窄窗 1208×680 下三舱零碰撞验证
            page_f.set_viewport_size({"width": 1208, "height": 680})
            wait_rendered_frames(page_f, 1)
            narrow_680 = page_f.evaluate(
                """() => {
                    const bl = document.querySelector('.scv-bl-wing').getBoundingClientRect();
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const br = document.querySelector('.scv-br-wing').getBoundingClientRect();
                    const mm = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const radarDock = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const radarBtns = [...document.querySelectorAll('#hud-radar-dock .scv-radar-micro-btn')].map(b => b.getBoundingClientRect());
                    return {
                        mmWidth: Number(mm.width.toFixed(1)),
                        mmHeight: Number(mm.height.toFixed(1)),
                        deckWidth: Math.round(deck.width),
                        rightDockWidth: Math.round(br.width),
                        radarBtnsInside: radarBtns.length >= 5 && radarBtns.every(r => r.left >= radarDock.left - 1 && r.right <= radarDock.right + 1),
                        threeBaysNoOverlap: bl.right <= deck.left + 2 && deck.right <= br.left + 2,
                    };
                }"""
            )
            must(
                narrow_680["threeBaysNoOverlap"] is True
                and narrow_680["radarBtnsInside"] is True
                and abs(narrow_680["mmWidth"] - narrow_680["mmHeight"]) <= 1.5
                and narrow_680["mmWidth"] >= 200,
                f"1208x680 窄窗三舱防撞与雷达微按钮不溢出校验失败: {narrow_680}",
            )

            # F7) U 键三态画幅 (16:9 -> 21:9 -> full -> 16:9) 自适应 680p 零黑边
            page_f.set_viewport_size({"width": 1600, "height": 900})
            page_f.keyboard.press("u")
            wait_rendered_frames(page_f, 1)
            asp_21 = page_f.evaluate("() => window.__SCV_MAP__.getRenderBaseline()")
            must(asp_21["activeAspect"] == "21:9" and asp_21["activeResolution"] == [1588, 680], f"第 1 次按 U 应切至 21:9 (1588x680): {asp_21}")

            page_f.keyboard.press("u")
            wait_rendered_frames(page_f, 2)
            full_state = page_f.evaluate(
                """() => {
                    const host = document.getElementById('canvas-host');
                    const rect = host.getBoundingClientRect();
                    const fsBtn = document.getElementById('btn-native-fullscreen');
                    const fsRect = fsBtn?.getBoundingClientRect();
                    return {
                        baseline: window.__SCV_MAP__.getRenderBaseline(),
                        quickLbl: document.getElementById('quick-aspect-lbl')?.textContent?.trim(),
                        hostW: Math.round(rect.width),
                        hostH: Math.round(rect.height),
                        vw: window.innerWidth,
                        vh: window.innerHeight,
                        fsBtnVisible: Boolean(fsRect && fsRect.width >= 24 && fsRect.height >= 24),
                    };
                }"""
            )
            must(
                full_state["baseline"]["activeAspect"] == "full"
                and full_state["baseline"]["activeResolution"][1] == 680
                and full_state["baseline"]["activeResolution"][0] % 2 == 0
                and full_state["quickLbl"] == "全屏"
                and abs(full_state["hostW"] - full_state["vw"]) <= 1
                and abs(full_state["hostH"] - full_state["vh"]) <= 1
                and full_state["fsBtnVisible"] is True,
                f"full 全屏自适应 680p 校验失败: {full_state}",
            )

            page_f.keyboard.press("u")
            wait_rendered_frames(page_f, 1)
            asp_back_16 = page_f.evaluate("() => window.__SCV_MAP__.getRenderBaseline()")
            must(asp_back_16["activeAspect"] == "16:9" and asp_back_16["activeResolution"] == [1208, 680], f"第 3 次按 U 应切回 16:9 (1208x680): {asp_back_16}")

            # F8) 持续跟随新规则全闭环 + 指令舱「跟随中」与「攻地」大图标高亮实景
            page_f.evaluate("async () => await window.__SCV_MAP__.resetDefaultRoster()")
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 3", timeout=15000)
            page_f.evaluate(
                """() => {
                    window.__SCV_INTERACTION__.dispatchCommand(['US_M1A2_01'], { type: 'move', point: { x: 145, z: 195 } }, false);
                    window.__SCV_INTERACTION__.dispatchCommand(['US_AH64D_01'], { type: 'move', point: { x: -85, z: 185 } }, false);
                    window.__SCV_INTERACTION__.setSelection(['US_M1A2_01']);
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            focus_unit_safe(page_f, "US_M1A2_01", span=155.0)

            # 点击左下角 #btn-mm-follow 开启持续跟随模式
            page_f.click("#btn-mm-follow")
            follow_on_m1 = page_f.evaluate(
                """() => {
                    const btn = document.getElementById('btn-mm-follow');
                    return {
                        hud: window.__SCV_MAP__.getUiHudState(),
                        hasActiveClass: btn.classList.contains('is-follow-active'),
                        ariaPressed: btn.getAttribute('aria-pressed'),
                        label: document.getElementById('mm-follow-lbl')?.textContent?.trim(),
                    };
                }"""
            )
            must(
                follow_on_m1["hud"]["followModeActive"] is True
                and follow_on_m1["hud"]["followUnitId"] == "US_M1A2_01"
                and follow_on_m1["hasActiveClass"] is True
                and follow_on_m1["ariaPressed"] == "true"
                and follow_on_m1["label"] == "跟随中",
                f"点击 #btn-mm-follow 应开启持久跟随并显示『跟随中』高亮: {follow_on_m1}",
            )

            # 按 W 平移 + Q 旋转 + 小地图左键拖移 -> 必须全程保持跟随锁定不退出！
            page_f.keyboard.down("w")
            page_f.keyboard.down("q")
            for _ in range(4):
                page_f.evaluate("() => window.__SCV_INTERACTION__.step(45, performance.now())")
            page_f.keyboard.up("q")
            page_f.keyboard.up("w")
            mm_box_f2 = page_f.locator("#hud-minimap-box").bounding_box()
            must(mm_box_f2 is not None, "缺少 #hud-minimap-box")
            drag_mouse(
                page_f,
                mm_box_f2["x"] + mm_box_f2["width"] * 0.50,
                mm_box_f2["y"] + mm_box_f2["height"] * 0.50,
                mm_box_f2["x"] + mm_box_f2["width"] * 0.515,
                mm_box_f2["y"] + mm_box_f2["height"] * 0.495,
                button="left",
                steps=4,
            )
            page_f.evaluate(
                """() => {
                    const cam = window.__SCV_INTERACTION__.cameraCtrl;
                    cam.resetFollowOffset();
                    cam.panByPixels(-85, -45);
                    for (let i = 0; i < 16; i++) cam.step(16, performance.now() + i * 16, new Set());
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            wait_rendered_frames(page_f, 2)
            follow_after_pan_rot = page_f.evaluate(
                """() => {
                    const st = window.__SCV_INTERACTION__.getState();
                    const hud = window.__SCV_MAP__.getUiHudState();
                    const off = st.view.followOffset || { x: 0, z: 0 };
                    return {
                        followModeActive: hud.followModeActive,
                        followUnitId: hud.followUnitId,
                        offsetNorm: Number(Math.hypot(off.x, off.z).toFixed(2)),
                        angleAbs: Number(Math.abs(st.view.yaw).toFixed(3)),
                        label: document.getElementById('mm-follow-lbl')?.textContent?.trim(),
                    };
                }"""
            )
            must(
                follow_after_pan_rot["followModeActive"] is True
                and follow_after_pan_rot["followUnitId"] == "US_M1A2_01"
                and follow_after_pan_rot["offsetNorm"] > 2.0
                and follow_after_pan_rot["angleAbs"] > 0.10
                and follow_after_pan_rot["label"] == "跟随中",
                f"持续跟随期间 WASD + Q/E + 小地图拖移不得退出跟随: {follow_after_pan_rot}",
            )

            # 在保持「跟随中」激活态下按下 K 进入「攻地」瞄准模式，验证右下指令舱双大图标同时高亮
            page_f.keyboard.press("k")
            wait_rendered_frames(page_f, 1)
            cmd_dual_hl = page_f.evaluate(
                """() => {
                    const gBtn = document.querySelector('#hud-cmd-grid [data-cmd-key="G"]');
                    const kBtn = document.querySelector('#hud-cmd-grid [data-cmd-key="K"]');
                    const allCells = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')];
                    return {
                        gActive: Boolean(gBtn?.classList.contains('is-active-mode')),
                        gText: gBtn?.querySelector('.c-txt')?.textContent?.trim(),
                        kActive: Boolean(kBtn?.classList.contains('is-active-mode')),
                        kText: kBtn?.querySelector('.c-txt')?.textContent?.trim(),
                        svgCount: allCells.filter(c => Boolean(c.querySelector('svg'))).length,
                        subCount: document.querySelectorAll('#hud-cmd-grid .c-sub').length,
                    };
                }"""
            )
            must(
                cmd_dual_hl["gActive"] is True
                and cmd_dual_hl["gText"] == "跟随中"
                and cmd_dual_hl["kActive"] is True
                and cmd_dual_hl["kText"] in ("强制攻地", "攻地")
                and cmd_dual_hl["svgCount"] == 6
                and cmd_dual_hl["subCount"] == 0,
                f"指令舱『跟随中』与『强制攻地』大图标双高亮校验失败: {cmd_dual_hl}",
            )
            # 代表图 4：指令舱开启「跟随中」与「攻地」大图标高亮实景
            shot_cmd_highlight = out_dir / f"{channel}-23-cmd-follow-attack-highlight.png"
            page_f.screenshot(path=str(shot_cmd_highlight))
            report["screenshots"]["cmdFollowAttackHighlight"] = str(shot_cmd_highlight)

            # 按 Esc 退出攻地瞄准态，继续验证 F 归中 -> 卡片接力 -> 退出跟随
            page_f.keyboard.press("Escape")
            page_f.wait_for_timeout(80)

            # 按 F 归零偏移回归居中（仍保持跟随中）
            page_f.keyboard.press("f")
            settle_camera_and_render(page_f, max_steps=30, dt_sim=0.008)
            follow_recentered = page_f.evaluate(
                """() => {
                    const host = document.getElementById('canvas-host');
                    const p = window.__SCV_INTERACTION__.projectUnit('US_M1A2_01');
                    const st = window.__SCV_INTERACTION__.getState();
                    const off = st.view.followOffset || { x: 0, z: 0 };
                    return {
                        followModeActive: window.__SCV_MAP__.getUiHudState().followModeActive,
                        followUnitId: st.followUnitId,
                        offsetNorm: Number(Math.hypot(off.x, off.z).toFixed(3)),
                        errPx: Number(Math.hypot(p.x - host.clientWidth * 0.5, p.y - host.clientHeight * 0.5).toFixed(2)),
                    };
                }"""
            )
            must(
                follow_recentered["followModeActive"] is True
                and follow_recentered["followUnitId"] == "US_M1A2_01"
                and follow_recentered["offsetNorm"] < 0.05
                and follow_recentered["errPx"] < 28.0,
                f"按 F 应归零偏移回归居中且保持跟随: {follow_recentered}",
            )

            # 在跟随开启状态下，直接点击方案 B 卡片坞中的 AH-64D 卡片 -> 自动接力跟随 AH-64D 而不关闭跟随
            page_f.click('#hud-cards-grid .scv-unit-card[data-slug-key="US_AH64D"]')
            settle_camera_and_render(page_f, max_steps=30, dt_sim=0.01)
            follow_relay_ah = page_f.evaluate(
                """() => {
                    const host = document.getElementById('canvas-host');
                    const p = window.__SCV_INTERACTION__.projectUnit('US_AH64D_01');
                    const cx = host.clientWidth * 0.5;
                    const cy = host.clientHeight * 0.5;
                    const btn = document.getElementById('btn-mm-follow');
                    return {
                        hud: window.__SCV_MAP__.getUiHudState(),
                        hasActiveClass: btn.classList.contains('is-follow-active'),
                        label: document.getElementById('mm-follow-lbl')?.textContent?.trim(),
                        errPx: Number(Math.hypot(p.x - cx, p.y - cy).toFixed(2)),
                    };
                }"""
            )
            must(
                follow_relay_ah["hud"]["followModeActive"] is True
                and follow_relay_ah["hud"]["followUnitId"] == "US_AH64D_01"
                and follow_relay_ah["hasActiveClass"] is True
                and follow_relay_ah["label"] == "跟随中"
                and follow_relay_ah["errPx"] < 28.0,
                f"跟随开启状态下点击方案 B 卡片切选新单位应保持跟随并自动接力居中 (<28px): {follow_relay_ah}",
            )

            # 再次点击 #btn-mm-follow 关闭跟随
            page_f.click("#btn-mm-follow")
            follow_off = page_f.evaluate(
                """() => {
                    const btn = document.getElementById('btn-mm-follow');
                    return {
                        hud: window.__SCV_MAP__.getUiHudState(),
                        hasActiveClass: btn.classList.contains('is-follow-active'),
                        ariaPressed: btn.getAttribute('aria-pressed'),
                        label: document.getElementById('mm-follow-lbl')?.textContent?.trim(),
                    };
                }"""
            )
            must(
                follow_off["hud"]["followModeActive"] is False
                and follow_off["hud"]["followUnitId"] is None
                and follow_off["hasActiveClass"] is False
                and follow_off["ariaPressed"] == "false"
                and follow_off["label"] == "跟随",
                f"再次点击 #btn-mm-follow 应关闭持续跟随模式: {follow_off}",
            )

            # 按 G 重新开启跟随，然后按 R 复位 -> 正式退出持续跟随
            page_f.keyboard.press("g")
            must(page_f.evaluate("() => window.__SCV_MAP__.getUiHudState().followModeActive") is True, "按 G 应开启跟随")
            page_f.keyboard.press("r")
            page_f.wait_for_timeout(100)
            must(page_f.evaluate("() => window.__SCV_MAP__.getUiHudState().followModeActive") is False, "按 R 复位视角应退出跟随模式")

            # F9) HUD 四项精修全回归：纯军标命名 + 4A 左上部署零碰撞 + 卡片坞单车展矩阵与宽度变化 + 3A 实景 PiP 等高对称
            page_f.set_viewport_size({"width": 1600, "height": 900})
            page_f.evaluate("async () => { window.__SCV_MAP__.setAspectProfile('16:9'); await window.__SCV_MAP__.resetDefaultRoster(); }")
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().heroCount === 3", timeout=15000)
            wait_rendered_frames(page_f, 3)

            # 1) 验证全界面纯军标型号（无旧中文代号/绰号与 US_ 下划线脏名）
            clean_names = page_f.evaluate(
                """() => {
                    const forbidden = ['铁砧', '游隼', '雷霆', '阿帕奇', '疣猪', 'US_AC130J', 'US_LAV_AT-M_body'];
                    const hudTxt = (document.getElementById('hud-grid')?.innerText || '') + ' ' +
                                   [...document.querySelectorAll('.ground-mark')].map(e => e.textContent).join(' ');
                    const hits = forbidden.filter(w => hudTxt.includes(w));
                    return {
                        hits,
                        inspName: document.getElementById('insp-name')?.textContent?.trim(),
                        inspSub: document.getElementById('insp-sub')?.textContent?.trim(),
                        hasOldRail: Boolean(document.getElementById('hud-cat-rail')),
                        hasDeployRail: Boolean(document.getElementById('hud-deploy-rail')),
                        railCount: document.querySelectorAll('#hud-deploy-rail .scv-drail-btn').length,
                    };
                }"""
            )
            must(
                len(clean_names["hits"]) == 0
                and clean_names["inspName"] == "M1A2 SEP v3"
                and clean_names["inspSub"] != clean_names["inspName"]
                and clean_names["hasOldRail"] is False
                and clean_names["hasDeployRail"] is True
                and clean_names["railCount"] == 5,
                f"纯军标命名与左上 4A 5兵种配额轨校验失败: {clean_names}",
            )

            # 2) 点击 4A 左上轨道中的旋翼分类，向右侧滑出部署面板且严守不碰撞左下雷达
            page_f.click('#hud-deploy-rail .scv-drail-btn[data-deploy-rail-cat="heli"]')
            page_f.wait_for_function("() => window.__SCV_MAP__.getDeployCatalogState().open === true", timeout=10000)
            page_f.wait_for_timeout(150)
            deploy_4a_metrics = page_f.evaluate(
                """() => {
                    const rail = document.getElementById('hud-deploy-rail').getBoundingClientRect();
                    const drawer = document.getElementById('deploy-drawer').getBoundingClientRect();
                    const radar = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const listTxt = document.getElementById('deploy-vehicle-list')?.innerText || '';
                    return {
                        railRight: Math.round(rail.right),
                        drawerLeft: Math.round(drawer.left),
                        drawerBottom: Math.round(drawer.bottom),
                        radarTop: Math.round(radar.top),
                        clearancePx: Math.round(radar.top - drawer.bottom),
                        hasBareUs: listTxt.includes('US_'),
                    };
                }"""
            )
            must(
                deploy_4a_metrics["drawerLeft"] >= deploy_4a_metrics["railRight"]
                and deploy_4a_metrics["clearancePx"] >= 4
                and deploy_4a_metrics["hasBareUs"] is False,
                f"左上 4A 向右滑出部署舱应不压轨道、不碰雷达且无裸 US_ 前缀: {deploy_4a_metrics}",
            )
            shot_4a_deploy = out_dir / f"{channel}-24-deploy-rail-4a.png"
            page_f.screenshot(path=str(shot_4a_deploy))
            report["screenshots"]["deployRail4a"] = str(shot_4a_deploy)
            page_f.click("#btn-close-deploy")

            # 3) 部署 9 台载具 -> 测试卡片坞「展开 ▴」默认单车全展多行矩阵 + 收起聚合 + 切旋翼显式像素宽度收缩
            page_f.evaluate(
                """async () => {
                    for (const s of ['US_A10', 'US_A10', 'US_AH64D', 'US_F22', 'US_F35A', 'US_AC130J', 'US_M2A3', 'US_PATRIOT_PAC3MSE', 'US_M1A2']) {
                        await window.__SCV_MAP__.deployVehicle(s);
                    }
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            wait_rendered_frames(page_f, 2)
            w_all_collapsed = page_f.evaluate("() => window.__SCV_MAP__.getCardDeckState().deckWidth")
            page_f.click('#hud-card-deck .scv-deck-tab[data-deck-tab="heli"]')
            page_f.wait_for_function("() => window.__SCV_MAP__.getCardDeckState().deckWidth < 600", timeout=5000)
            wait_rendered_frames(page_f, 2)
            w_heli_collapsed = page_f.evaluate("() => window.__SCV_MAP__.getCardDeckState().deckWidth")
            must(w_heli_collapsed < w_all_collapsed, f"切至旋翼页签卡片坞宽度应丝滑缩窄 ({w_all_collapsed} -> {w_heli_collapsed})")

            page_f.click('#hud-card-deck .scv-deck-tab[data-deck-tab="all"]')
            page_f.click("#btn-deck-expand")
            page_f.wait_for_function(
                "() => window.__SCV_MAP__.getCardDeckState().expanded === true && window.__SCV_MAP__.getCardDeckState().deckWidth >= 600",
                timeout=5000,
            )
            wait_rendered_frames(page_f, 2)
            expand_metrics = page_f.evaluate(
                """() => {
                    const st = window.__SCV_MAP__.getCardDeckState();
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    const topBar = document.getElementById('top-bar').getBoundingClientRect();
                    const radar = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const right = document.getElementById('hud-inspector').getBoundingClientRect();
                    const cards = [...document.querySelectorAll('#hud-cards-grid .scv-unit-card')];
                    const multiRow = cards.length >= 6 && cards.some(c => c.getBoundingClientRect().top > cards[0].getBoundingClientRect().top + 20);
                    return {
                        ...st,
                        cardCount: cards.length,
                        multiRow,
                        aboveBottomBar: deck.top > topBar.bottom + 8,
                        noOverlapWings: radar.right <= deck.left + 2 && deck.right <= right.left + 2,
                    };
                }"""
            )
            must(
                expand_metrics["expanded"] is True
                and expand_metrics["expandView"] == "single"
                and expand_metrics["cardCount"] == 12
                and expand_metrics["multiRow"] is True
                and expand_metrics["aboveBottomBar"] is True
                and expand_metrics["noOverlapWings"] is True,
                f"卡片坞展开默认『单车全展』多行矩阵校验失败: {expand_metrics}",
            )
            shot_deck_expand = out_dir / f"{channel}-25-deck-expand-single.png"
            page_f.screenshot(path=str(shot_deck_expand))
            report["screenshots"]["deckExpandSingle"] = str(shot_deck_expand)

            page_f.click("#btn-deck-expand")
            page_f.wait_for_function("() => window.__SCV_MAP__.getCardDeckState().expanded === false", timeout=5000)
            wait_rendered_frames(page_f, 1)
            after_collapse = page_f.evaluate("() => window.__SCV_MAP__.getCardDeckState()")
            must(after_collapse["expanded"] is False and after_collapse["expandView"] == "group", f"收起后应自动恢复同型聚合: {after_collapse}")

            # 4) 验证右下 3A Live PiP 纯载具隔离实时实况画布（无地形/草木/建筑环境、极低 DrawCall、切换追尾视角及与左下雷达 1:1 等高对称）
            page_f.evaluate(
                """() => {
                    window.__SCV_INTERACTION__.setSelection(['US_M1A2_01']);
                    window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001);
                }"""
            )
            pip_front_metrics = page_f.evaluate(
                """() => {
                    const cv = document.getElementById('hud-pip-canvas');
                    const ctx = cv.getContext('2d');
                    const img = ctx.getImageData(0, 0, cv.width, cv.height);
                    const data = img.data;
                    const w = cv.width, h = cv.height;
                    const cornerCoords = [[3, 3], [w - 4, 3], [3, h - 4], [w - 4, h - 4]];
                    const corners = cornerCoords.map(([cx, cy]) => {
                        const off = (cy * w + cx) * 4;
                        return [data[off], data[off + 1], data[off + 2]];
                    });
                    const bgR = corners.reduce((a, c) => a + c[0], 0) / corners.length;
                    const bgG = corners.reduce((a, c) => a + c[1], 0) / corners.length;
                    const bgB = corners.reduce((a, c) => a + c[2], 0) / corners.length;
                    const cornerMaxDiff = corners.reduce((mx, c) => Math.max(
                        mx,
                        Math.abs(c[0] - bgR) + Math.abs(c[1] - bgG) + Math.abs(c[2] - bgB)
                    ), 0);

                    let sum = 0, sumSq = 0, n = 0, vehiclePx = 0, bgPx = 0;
                    for (let i = 0; i < data.length; i += 16) {
                        const r = data[i], g = data[i + 1], b = data[i + 2];
                        const luma = r * 0.299 + g * 0.587 + b * 0.114;
                        sum += luma; sumSq += luma * luma; n += 1;
                        const diff = Math.abs(r - bgR) + Math.abs(g - bgG) + Math.abs(b - bgB);
                        if (diff > 28) vehiclePx += 1;
                        else bgPx += 1;
                    }
                    const mean = sum / Math.max(1, n);
                    const std = Math.sqrt(Math.max(0, sumSq / Math.max(1, n) - mean * mean));
                    const rad = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const rgt = document.getElementById('hud-inspector').getBoundingClientRect();
                    return {
                        std: Number(std.toFixed(2)),
                        bgCornerMean: [Math.round(bgR), Math.round(bgG), Math.round(bgB)],
                        cornerMaxDiff: Math.round(cornerMaxDiff),
                        vehiclePixelRatio: Number((vehiclePx / Math.max(1, n)).toFixed(3)),
                        bgPixelRatio: Number((bgPx / Math.max(1, n)).toFixed(3)),
                        radarH: Math.round(rad.height),
                        rightH: Math.round(rgt.height),
                        diffH: Math.round(Math.abs(rad.height - rgt.height)),
                        pipState: window.__SCV_MAP__.getPipCamState(),
                    };
                }"""
            )
            must(
                pip_front_metrics["std"] > 6.0
                and pip_front_metrics["cornerMaxDiff"] <= 8
                and max(pip_front_metrics["bgCornerMean"]) < 75
                and pip_front_metrics["bgCornerMean"][2] >= pip_front_metrics["bgCornerMean"][0]
                and 0.04 <= pip_front_metrics["vehiclePixelRatio"] <= 0.72
                and pip_front_metrics["bgPixelRatio"] >= 0.25
                and pip_front_metrics["diffH"] <= 14
                and pip_front_metrics["pipState"]["frameCount"] >= 1
                and pip_front_metrics["pipState"]["environmentExcluded"] is True
                and pip_front_metrics["pipState"]["shadowMapSkipped"] is True
                and 1 <= pip_front_metrics["pipState"]["lastDrawCalls"] <= 48,
                f"3A 纯载具隔离 Live PiP 实况画布、低 DrawCall 或 1:1 等高对称校验失败: {pip_front_metrics}",
            )

            page_f.click("#btn-pip-angle")
            page_f.evaluate("() => window.__SCV_MAP__.stepSimAndRender(0.016, performance.now() * 0.001)")
            pip_rear_state = page_f.evaluate("() => ({ mode: window.__SCV_MAP__.getPipCamState().cameraMode, label: document.getElementById('btn-pip-angle')?.textContent?.trim(), hud: window.__SCV_MAP__.getUiHudState().pipMode })")
            must(
                pip_rear_state["mode"] == "rear" and pip_rear_state["label"] == "追尾" and pip_rear_state["hud"] == "rear",
                f"点击角标应切换 Live PiP 至追尾机位: {pip_rear_state}",
            )
            shot_live_pip = out_dir / f"{channel}-26-live-pip-rear.png"
            page_f.screenshot(path=str(shot_live_pip))
            report["screenshots"]["livePipRear"] = str(shot_live_pip)

            # 5) 反复点多方向目的地：校验真转向运动中球面最短弧跟拍，零穿模缩放与跳动
            anti_jitter = page_f.evaluate(
                """() => {
                    const combat = window.__SCV_INTERACTION__;
                    combat.setSelection(['US_M1A2_01']);
                    window.__SCV_MAP__.resetHeroVehiclePos('US_M1A2_01', 0, 85);
                    let simT = performance.now() * 0.001 + 0.02;
                    window.__SCV_MAP__.stepSimAndRender(0.033, simT);
                    const dists = [];
                    const azs = [];
                    let acceptedCmds = 0;
                    const targets = [
                        { x: 40, z: 85 },
                        { x: -40, z: 85 },
                        { x: 0, z: 125 },
                        { x: 0, z: 45 },
                    ];
                    for (const pt of targets) {
                        const res = combat.dispatchCommand(['US_M1A2_01'], { type: 'move', point: pt }, false);
                        if (res?.accepted > 0) acceptedCmds += 1;
                        for (let k = 0; k < 6; k++) {
                            simT += 0.033;
                            window.__SCV_MAP__.stepSimAndRender(0.033, simT);
                            const st = window.__SCV_MAP__.getPipCamState();
                            dists.push(st.lastCamDist);
                            azs.push(st.smoothAzimuth);
                        }
                    }
                    const minD = Math.min(...dists);
                    const maxD = Math.max(...dists);
                    let maxStepAz = 0;
                    let totalAbsAz = 0;
                    for (let i = 1; i < azs.length; i++) {
                        let d = Math.abs(azs[i] - azs[i - 1]) % (Math.PI * 2);
                        if (d > Math.PI) d = Math.PI * 2 - d;
                        totalAbsAz += d;
                        if (d > maxStepAz) maxStepAz = d;
                    }
                    return {
                        acceptedCmds,
                        sampleCount: dists.length,
                        distSpan: Number((maxD - minD).toFixed(4)),
                        maxStepAz: Number(maxStepAz.toFixed(4)),
                        totalAbsAz: Number(totalAbsAz.toFixed(4)),
                    };
                }"""
            )
            must(
                anti_jitter["acceptedCmds"] == 4
                and anti_jitter["sampleCount"] == 24
                and anti_jitter["totalAbsAz"] > 0.4
                and anti_jitter["distSpan"] < 0.5
                and anti_jitter["maxStepAz"] < 0.45,
                f"反复下令换向时小窗球面距离应恒定不穿模且单步方位角平滑转向: {anti_jitter}",
            )

            # 6) 静态小窗按钮 0 FPS 省电切换 + 图 2 红框紧致三层（2×2 遥测 + ROE 上移 + 无开发部件标签）验收
            page_f.click("#btn-pip-static")
            page_f.evaluate("() => window.__SCV_MAP__.stepSimAndRender(0.033, performance.now() * 0.001 + 0.05)")
            static_frozen_metrics = page_f.evaluate(
                """() => {
                    const beforeFc = window.__SCV_MAP__.getPipCamState().frameCount;
                    let t = performance.now() * 0.001 + 0.1;
                    for (let i = 0; i < 12; i++) {
                        t += 0.033;
                        window.__SCV_MAP__.stepSimAndRender(0.033, t);
                    }
                    const after12Fc = window.__SCV_MAP__.getPipCamState().frameCount;
                    window.__SCV_INTERACTION__.setSelection(['US_AH64D_01']);
                    t += 0.033;
                    window.__SCV_MAP__.stepSimAndRender(0.033, t);
                    const afterSwitchFc = window.__SCV_MAP__.getPipCamState().frameCount;
                    t += 0.033;
                    window.__SCV_MAP__.stepSimAndRender(0.033, t);
                    const afterExtraFc = window.__SCV_MAP__.getPipCamState().frameCount;

                    const st = window.__SCV_MAP__.getPipCamState();
                    const hudSt = window.__SCV_MAP__.getUiHudState();
                    const teleRect = document.querySelector('.scv-pip-tele')?.getBoundingClientRect();
                    const frameRect = document.getElementById('hud-pip-frame')?.getBoundingClientRect();
                    const radRect = document.getElementById('hud-radar-dock')?.getBoundingClientRect();
                    const rgtRect = document.getElementById('hud-inspector')?.getBoundingClientRect();
                    const roeEl = document.getElementById('hud-roe-select');
                    return {
                        staticMode: st.staticMode,
                        staticFrozen: st.staticFrozen,
                        lastModeDrawn: st.lastPose?.mode,
                        hudPipStatic: hudSt.pipStatic,
                        btnTxt: document.getElementById('pip-mode-txt')?.textContent?.trim(),
                        angleBtnDisabled: Boolean(document.getElementById('btn-pip-angle')?.disabled),
                        angleBtnTxt: document.getElementById('btn-pip-angle')?.textContent?.trim(),
                        deltaDuring12Frames: after12Fc - beforeFc,
                        deltaOnUnitSwitch: afterSwitchFc - after12Fc,
                        deltaAfterUnitSwitch: afterExtraFc - afterSwitchFc,
                        hasOldRigTags: Boolean(document.getElementById('insp-rig-tags')),
                        roeInTele: Boolean(roeEl && roeEl.closest('.scv-pip-tele')),
                        chBadge: document.getElementById('insp-ch-badge')?.textContent?.trim(),
                        speedTxt: document.getElementById('insp-speed')?.textContent?.trim(),
                        altTxt: document.getElementById('insp-alt')?.textContent?.trim(),
                        posTxt: document.getElementById('insp-pos')?.textContent?.trim(),
                        motionTxt: document.getElementById('insp-motion')?.textContent?.trim(),
                        teleFrameDiffH: teleRect && frameRect ? Math.round(Math.abs(teleRect.height - frameRect.height)) : 999,
                        radarRightDiffH: radRect && rgtRect ? Math.round(Math.abs(radRect.height - rgtRect.height)) : 999,
                    };
                }"""
            )
            must(
                static_frozen_metrics["staticMode"] is True
                and static_frozen_metrics["staticFrozen"] is True
                and static_frozen_metrics["lastModeDrawn"] == "front"
                and static_frozen_metrics["hudPipStatic"] is True
                and static_frozen_metrics["btnTxt"] == "静态"
                and static_frozen_metrics["angleBtnDisabled"] is True
                and static_frozen_metrics["angleBtnTxt"] == "已冻结"
                and static_frozen_metrics["deltaDuring12Frames"] == 0
                and static_frozen_metrics["deltaOnUnitSwitch"] == 1
                and static_frozen_metrics["deltaAfterUnitSwitch"] == 0
                and static_frozen_metrics["hasOldRigTags"] is False
                and static_frozen_metrics["roeInTele"] is True
                and static_frozen_metrics["chBadge"] == "HELI"
                and bool(static_frozen_metrics["speedTxt"])
                and bool(static_frozen_metrics["altTxt"])
                and bool(static_frozen_metrics["posTxt"])
                and bool(static_frozen_metrics["motionTxt"])
                and static_frozen_metrics["teleFrameDiffH"] <= 10
                and static_frozen_metrics["radarRightDiffH"] <= 14,
                f"静态 0 FPS 省电模式或图 2 红框紧凑布局校验失败: {static_frozen_metrics}",
            )
            shot_pip_static = out_dir / f"{channel}-27-live-pip-static.png"
            page_f.screenshot(path=str(shot_pip_static))
            report["screenshots"]["livePipStatic"] = str(shot_pip_static)

            # 点回 LIVE 态，校验恢复追尾视角标签
            page_f.click("#btn-pip-static")
            restored_live = page_f.evaluate("() => ({ pipStatic: window.__SCV_MAP__.getUiHudState().pipStatic, btnTxt: document.getElementById('pip-mode-txt')?.textContent?.trim(), angleTxt: document.getElementById('btn-pip-angle')?.textContent?.trim(), angleDisabled: Boolean(document.getElementById('btn-pip-angle')?.disabled) })")
            must(
                restored_live["pipStatic"] is False
                and restored_live["btnTxt"] == "LIVE"
                and restored_live["angleTxt"] == "追尾"
                and restored_live["angleDisabled"] is False,
                f"点回 LIVE 应恢复动态追踪与原视角设置: {restored_live}",
            )

            # 7) 左下角小地图 M1 无级滚轮缩放 + R1 <=3600m 矢量高清 LOD + M 键大号战术图 + Esc 优先收回
            page_f.evaluate(
                """() => {
                    window.__SCV_MAP__.setMinimapZoomRange(9000, true);
                    window.__SCV_MAP__.setMinimapExpanded(false);
                }"""
            )
            cam_span_before_mm_wheel = page_f.evaluate("() => window.__SCV_MAP__.getCameraState().span")
            mm_rect = page_f.locator("#hud-minimap-box").bounding_box()
            must(mm_rect is not None, "缺少 #hud-minimap-box")
            page_f.mouse.move(mm_rect["x"] + mm_rect["width"] * 0.5, mm_rect["y"] + mm_rect["height"] * 0.5)
            for _ in range(3):
                page_f.mouse.wheel(0, -180)
                page_f.evaluate(
                    """() => {
                        window.__scvTestMmT = (window.__scvTestMmT || performance.now() * 0.001) + 0.05;
                        window.__SCV_MAP__.stepSimAndRender(0.05, window.__scvTestMmT);
                    }"""
                )
            for _ in range(12):
                page_f.evaluate(
                    """() => {
                        window.__scvTestMmT = (window.__scvTestMmT || performance.now() * 0.001) + 0.06;
                        window.__SCV_MAP__.stepSimAndRender(0.06, window.__scvTestMmT);
                    }"""
                )
            cam_span_after_mm_wheel = page_f.evaluate("() => window.__SCV_MAP__.getCameraState().span")
            mm_wheel_metrics = page_f.evaluate(
                """() => ({
                    st: window.__SCV_MAP__.getMinimapState(),
                    btnTxt: document.getElementById('btn-mm-zoom')?.textContent?.trim(),
                    modeLbl: document.getElementById('mm-mode-lbl')?.textContent?.trim(),
                    outDisabled: Boolean(document.getElementById('btn-mm-zoom-out')?.disabled),
                })"""
            )
            must(
                abs(cam_span_after_mm_wheel - cam_span_before_mm_wheel) < 0.01
                and mm_wheel_metrics["st"]["currentRangeM"] <= 3600
                and mm_wheel_metrics["st"]["stats"]["vectorLodActive"] is True
                and mm_wheel_metrics["st"]["stats"]["vectorBuildingsDrawn"] > 0
                and mm_wheel_metrics["outDisabled"] is False,
                f"小地图滚轮无级放大应切入 R1 矢量高清层且不穿透主 3D 场景缩放: {mm_wheel_metrics} (camSpan {cam_span_before_mm_wheel} -> {cam_span_after_mm_wheel})",
            )

            # 点击右下悬浮 [+] 按钮进一步放大至 <=1800m 并截取高清矢量细节图
            page_f.click("#btn-mm-zoom-in")
            page_f.click("#btn-mm-zoom-in")
            for _ in range(10):
                page_f.evaluate(
                    """() => {
                        window.__scvTestMmT = (window.__scvTestMmT || performance.now() * 0.001) + 0.06;
                        window.__SCV_MAP__.stepSimAndRender(0.06, window.__scvTestMmT);
                    }"""
                )
            mm_deep_zoom = page_f.evaluate("() => window.__SCV_MAP__.getMinimapState()")
            must(
                mm_deep_zoom["currentRangeM"] <= 1850
                and mm_deep_zoom["stats"]["vectorLodActive"] is True,
                f"点击 [+] 应持续高清放大至 <=1.85KM: {mm_deep_zoom}",
            )
            shot_mm_zoom = out_dir / f"{channel}-28-minimap-zoom-vector.png"
            page_f.screenshot(path=str(shot_mm_zoom))
            report["screenshots"]["minimapZoomVector"] = str(shot_mm_zoom)

            # 按 M 键原地向上向右展开大号战术地图 (>=420px 1:1 且不碰顶栏)，再按 Esc 优先收回不触发暂停
            page_f.keyboard.press("m")
            page_f.wait_for_function(
                "() => window.__SCV_MAP__.getUiHudState().minimapExpanded === true && document.getElementById('hud-minimap-box').getBoundingClientRect().width >= 420",
                timeout=5000,
            )
            wait_rendered_frames(page_f, 2)
            mm_expanded_metrics = page_f.evaluate(
                """() => {
                    const box = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const dock = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const topBar = document.getElementById('top-bar').getBoundingClientRect();
                    const deck = document.getElementById('hud-card-deck').getBoundingClientRect();
                    return {
                        hud: window.__SCV_MAP__.getUiHudState(),
                        mm: window.__SCV_MAP__.getMinimapState(),
                        boxW: Math.round(box.width),
                        boxH: Math.round(box.height),
                        dockTop: Math.round(dock.top),
                        topBarBottom: Math.round(topBar.bottom),
                        deckLeft: Math.round(deck.left),
                    };
                }"""
            )
            must(
                mm_expanded_metrics["hud"]["minimapExpanded"] is True
                and mm_expanded_metrics["boxW"] >= 420
                and abs(mm_expanded_metrics["boxW"] - mm_expanded_metrics["boxH"]) <= 2
                and mm_expanded_metrics["dockTop"] > mm_expanded_metrics["topBarBottom"] + 8,
                f"按 M 键应展开 1:1 大号战术地图且不碰撞顶栏: {mm_expanded_metrics}",
            )
            shot_mm_expanded = out_dir / f"{channel}-29-minimap-expanded-M.png"
            page_f.screenshot(path=str(shot_mm_expanded))
            report["screenshots"]["minimapExpandedM"] = str(shot_mm_expanded)

            page_f.keyboard.press("Escape")
            page_f.wait_for_function("() => window.__SCV_MAP__.getUiHudState().minimapExpanded === false", timeout=5000)
            after_esc_mm = page_f.evaluate("() => window.__SCV_MAP__.getUiHudState()")
            must(after_esc_mm["minimapExpanded"] is False and after_esc_mm["paused"] is False, f"Esc 应优先收回大号战术图而不触发暂停菜单: {after_esc_mm}")

            page_f.click("#btn-mm-zoom")
            mm_reset = page_f.evaluate("() => window.__SCV_MAP__.getMinimapState()")
            must(mm_reset["targetRangeM"] >= 8950 and mm_reset["zoomMode"] == "9km", f"放大态点击顶栏倍率钮应一键复位回 9KM 全域总览: {mm_reset}")

            report["checks"]["polish4Tasks"] = {
                "hudOverhaulF9": {
                    "cleanNames": clean_names,
                    "deploy4aMetrics": deploy_4a_metrics,
                    "deckWidthSmooth": {"all": w_all_collapsed, "heli": w_heli_collapsed},
                    "expandSingleMetrics": expand_metrics,
                    "livePipFront": pip_front_metrics,
                    "livePipRear": pip_rear_state,
                    "antiJitterOrbit": anti_jitter,
                    "static0FpsAndInspectorLayout": static_frozen_metrics,
                    "restoredLiveState": restored_live,
                    "minimapWheelAndVectorLod": mm_wheel_metrics,
                    "minimapDeepZoom": mm_deep_zoom,
                    "minimapExpandedM": mm_expanded_metrics,
                    "minimapReset9km": mm_reset,
                },
                "a10OrbitArcCount": orbit_arc_state["airTurnArcCount"],
                "a10StraightArcCount": straight_arc_state["overlay"]["airTurnArcCount"],
                "ah64dFocusCenter": ah_center_metrics,
                "a10FocusCenter": a10_f_metrics,
                "a10FollowRotateCenter": a10_follow_rot_metrics,
                "deployPanel": after_deploy,
                "afterResetIds": after_reset,
                "topbarLayout": topbar_checks,
                "default16x9ElasticHud": default_16x9_hud,
                "wide2560Elastic3Unit": wide_3unit_elastic,
                "narrow1208x680Check": narrow_680,
                "deployCategoryQuotas50": quota_burst,
                "schemeBAndRadar": scheme_b_metrics,
                "popoverAnd3kmRadar": pop_and_radar,
                "northResetAngleRad": round(angle_after_north, 4),
                "radarRightClickMoveWp": m1_after_radar_rc.get("remainingWaypoints", 0),
                "fullAspect680p": full_state,
                "cmdDualHighlight": cmd_dual_hl,
                "followModeRelay": {
                    "onM1": follow_on_m1,
                    "afterPanAndRot": follow_after_pan_rot,
                    "recenteredByF": follow_recentered,
                    "relayAH64D": follow_relay_ah,
                    "off": follow_off,
                },
            }
            must(len(report["errors"]) == 0, f"精修专项上下文出现报错: {report['errors']}")
            report["passed"] = True
            ctx_f.close()

        finally:
            browser.close()

    return report


def main():
    parser = argparse.ArgumentParser(description="Scavenger 操作交互系统 Chrome + Edge 双浏览器端到端验证")
    parser.add_argument("--base-url", default="http://127.0.0.1:8800", help="统一主服务地址")
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--run", default=f"run-{int(time.time())}", help="独立输出 run 子目录名")
    parser.add_argument("--out-base", default=str(ARTIFACT_BASE), help="自定义输出根目录")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    health = wait_server_online(args.base_url)
    out_dir = Path(args.out_base) / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    channels = ["chrome", "msedge"] if args.channel == "both" else [args.channel]
    all_reports = {
        "run": args.run,
        "baseUrl": args.base_url,
        "serverHealth": health,
        "channels": {},
        "passed": True,
    }

    for ch in channels:
        print(f"=== 开始运行 [{ch}] 操作交互端到端专项验证 ===")
        rep = run_channel_verification(ch, args.base_url, out_dir, args.headed)
        all_reports["channels"][ch] = rep
        if not rep["passed"]:
            all_reports["passed"] = False
        print(f"=== [{ch}] 结果: {'PASS' if rep['passed'] else 'FAIL'} | errors={len(rep['errors'])} ===")

    report_path = out_dir / "验证报告.json"
    report_path.write_text(json.dumps(all_reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n双浏览器专项验证报告已保存：{report_path}")
    if not all_reports["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
