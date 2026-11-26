"""在唯一正式端口 127.0.0.1:8800 上自动化实机验证写实爆炸特效 V3.0（P1 + P0 + P2）多武器、双画幅、深度羽化与性能红线。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:8800"
DEFAULT_OUT_DIR = ROOT / "tmp/vfx-explosion-v001-7e8ac44c"


def is_server_ready(base_url: str) -> bool:
    try:
        with urlopen(f"{base_url}/public/assets/vfx-explosion/smoke_vol_normal_alpha.png", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_main_server(base_url: str) -> subprocess.Popen | None:
    if is_server_ready(base_url):
        return None
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/启动主服务.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 25.0
    while time.time() < deadline:
        if is_server_ready(base_url):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"主服务提前退出，返回码={proc.returncode}")
        time.sleep(0.25)
    proc.terminate()
    raise TimeoutError("等待 127.0.0.1:8800 主服务就绪超时")


def wait_for_airlab_ready(page) -> None:
    page.wait_for_function(
        """() => Boolean(
            window.__SCV_MAP__
            && window.__SCV_MAP__.isCoreReady() === true
            && window.__SCAVENGER_AIR_LAB__
            && window.__SCAVENGER_AIR_LAB__.getVfxStats().atlasReady === true
        )""",
        timeout=65000,
    )


def step_frames(page, steps: int, dt_sec: float = 1.0 / 60.0) -> None:
    page.evaluate(
        """({ steps, dt }) => {
            window.__SCV_MAP__.setManualLoop(true);
            let nowSec = (window.__SCAVENGER_AIR_LAB__?.getConfig?.().simNowSec || 0);
            for (let i = 0; i < steps; i += 1) {
                nowSec += dt;
                const shouldRender = i >= steps - 2;
                window.__SCV_MAP__.stepSimAndRender(dt, nowSec, shouldRender);
            }
            window.__SCV_MAP__.finishGpu?.();
        }""",
        {"steps": int(steps), "dt": float(dt_sec)},
    )


def step_until_condition(page, js_pred: str, max_steps: int = 900, dt_sec: float = 1.0 / 60.0, chunk: int = 3) -> dict:
    return page.evaluate(
        """({ predSrc, maxSteps, dt, chunk }) => {
            window.__SCV_MAP__.setManualLoop(true);
            const pred = new Function('return Boolean(' + predSrc + ');');
            let nowSec = (window.__SCAVENGER_AIR_LAB__?.getConfig?.().simNowSec || 0);
            let elapsedSteps = 0;
            while (elapsedSteps < maxSteps) {
                for (let i = 0; i < chunk && elapsedSteps < maxSteps; i += 1) {
                    nowSec += dt;
                    window.__SCV_MAP__.stepSimAndRender(dt, nowSec, false);
                    elapsedSteps += 1;
                }
                if (pred()) {
                    window.__SCV_MAP__.stepSimAndRender(0.001, nowSec + 0.001, true);
                    window.__SCV_MAP__.finishGpu?.();
                    return { matched: true, elapsedSteps, simSec: Number((elapsedSteps * dt).toFixed(3)) };
                }
            }
            window.__SCV_MAP__.stepSimAndRender(0.001, nowSec + 0.001, true);
            window.__SCV_MAP__.finishGpu?.();
            return { matched: false, elapsedSteps, simSec: Number((elapsedSteps * dt).toFixed(3)) };
        }""",
        {"predSrc": js_pred, "maxSteps": int(max_steps), "dt": float(dt_sec), "chunk": int(chunk)},
    )


def set_camera_view(page, x: float, z: float, span: float) -> None:
    page.evaluate(
        """({ x, z, span }) => {
            const m = window.__SCV_MAP__;
            if (m?.setCameraView) {
                m.setCameraView(x, z, span);
            } else {
                const lab = window.__SCAVENGER_AIR_LAB__;
                lab?.executeAction?.('focus-range');
            }
            window.__SCV_MAP__.stepSimAndRender(0.001, performance.now() * 0.001);
        }""",
        {"x": x, "z": z, "span": span},
    )


def run_verification(base_url: str, out_dir: Path, channel: str = "chrome") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "base_url": base_url,
        "channel": channel,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": [],
        "screenshots": [],
        "errors": [],
        "telemetry": {},
    }

    def record(name: str, passed: bool, details=None) -> None:
        report["checks"].append({"name": name, "passed": bool(passed), "details": details})
        print(json.dumps({"check": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(f"验收未通过：{name} -> {details}")

    def snap(page, filename: str) -> dict:
        path = out_dir / filename
        page.evaluate("() => window.__SCV_MAP__?.finishGpu?.()")
        page.screenshot(path=str(path), animations="disabled", timeout=60000)
        # QA-09：校验截屏非黑屏、非纯色、分辨率有效
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            extrema = rgb.getextrema()
            spread = sum(hi - lo for lo, hi in extrema)
            # 抽样检查非黑像素占比（步长 16 快速无开销）
            small = rgb.resize((max(1, w // 16), max(1, h // 16)))
            px = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
            non_black = sum(1 for r, g, b in px if (r + g + b) > 24)
            non_black_ratio = non_black / max(1, len(px))
        if w < 640 or h < 360 or spread < 40 or non_black_ratio < 0.05:
            raise AssertionError(
                f"截图像素体检失败：{filename} -> size={(w, h)} spread={spread} non_black_ratio={non_black_ratio:.3f}"
            )
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        report["screenshots"].append(rel)
        return {"file": rel, "size": [w, h], "spread": spread, "non_black_ratio": round(non_black_ratio, 3)}

    # 0. 检查 /public/assets/vfx-explosion/ 静态路由 HTTP 200
    asset_codes = {}
    for rel in (
        "/public/assets/vfx-explosion/smoke_vol_normal_alpha.png",
        "/public/assets/vfx-explosion/smoke_vol_heat_thick.png",
        "/public/assets/vfx-explosion/README.md",
    ):
        with urlopen(base_url + rel, timeout=5) as resp:
            asset_codes[rel] = resp.status
    record("P0体积云图集静态路由HTTP 200", all(c == 200 for c in asset_codes.values()), asset_codes)

    launch_args = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=swiftshader",
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True, args=launch_args)
        page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda err: report["errors"].append(f"pageerror: {err}"))
        page.on(
            "console",
            lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error"
            else None,
        )

        try:
            # --- 场景 1：US_A10 air-lab 全武器与关键帧实机验证 ---
            page.goto(f"{base_url}/play?mode=air-lab&unit=US_A10&aspect=16:9", wait_until="domcontentloaded", timeout=50000)
            wait_for_airlab_ready(page)
            page.evaluate("() => window.__SCV_MAP__.waitForFullPrecision?.()")
            page.evaluate("() => { window.__SCV_MAP__.setManualLoop(true); }")
            step_frames(page, 6)

            init_audit = page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    const map = window.__SCV_MAP__;
                    return {
                        vfx: lab.getVfxStats(),
                        audit: lab.getRenderAudit(),
                        post: map.getPostprocessState(),
                        baseline: map.getRenderBaseline(),
                    };
                }"""
            )
            report["telemetry"]["init_audit"] = init_audit
            ok_init = (
                init_audit["vfx"]["vfxVersion"] == "3.0"
                and init_audit["vfx"]["atlasReady"] is True
                and init_audit["vfx"]["poolDrawCalls"] <= 6
                and init_audit["vfx"]["sunDir"][0] < 0
                and init_audit["audit"]["pointLightCount"] == 0
                and init_audit["audit"]["vfxRootLightCount"] == 0
                and init_audit["post"]["depthSplitEnabled"] is True
                and init_audit["baseline"]["activeResolution"] == [1208, 680]
            )
            record("V3.0初始化、图集就绪、太阳方向同步与零PointLight检查", ok_init, init_audit)

            # 开启 fast-approach，聚焦靶区近景
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    lab.executeAction('spawn-targets');
                    lab.executeAction('focus-range');
                }"""
            )
            step_frames(page, 18)

            # 1) GBU-31 航弹重爆四阶段关键帧
            page.evaluate(
                """() => {
                    window.__SCAVENGER_AIR_LAB__.executeAction('bomb', {
                        targetPos: { x: -10, z: 36 },
                        fastApproach: true,
                    });
                }"""
            )
            hit_gbu = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=900,
                chunk=1,
            )
            record("GBU-31重磅航弹命中触发", hit_gbu["matched"], hit_gbu)

            # 关键帧 1：0-60ms 瞬时白橙过曝爆闪核
            step_frames(page, 1)
            gbu_flash_stats = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getVfxStats()")
            snap(page, "01-gbu31-01-flash.png")

            # 关键帧 2：~240ms 急剧膨胀骤停 + 细长冷白破片烟丝 + 极细拉丝火花与泥块分离
            step_frames(page, 13)
            snap(page, "02-gbu31-02-burst-jets-sparks.png")
            gbu_burst_stats = page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    const audit = lab.getRenderAudit();
                    return {
                        vfx: lab.getVfxStats(),
                        audit: {
                            mainSceneDrawCalls: audit.mainSceneDrawCalls,
                            opaqueSceneDrawCalls: audit.opaqueSceneDrawCalls,
                            vfxOverlayDrawCalls: audit.vfxOverlayDrawCalls,
                            lastFrameUsedDepth: audit.lastFrameUsedDepth,
                            pointLightCount: audit.pointLightCount,
                            spotLightCount: audit.spotLightCount,
                        },
                        deltaCalls: audit.measureVfxDrawCallDelta(),
                    };
                }"""
            )
            ok_gbu_burst = (
                gbu_burst_stats["vfx"]["poolDrawCalls"] <= 6
                and gbu_burst_stats["deltaCalls"] <= 6
                and gbu_burst_stats["vfx"]["lastFrameUsedDepth"] is True
                and gbu_burst_stats["audit"]["lastFrameUsedDepth"] is True
                and gbu_burst_stats["audit"]["mainSceneDrawCalls"] > 1
                and gbu_burst_stats["audit"]["pointLightCount"] == 0
                and gbu_burst_stats["audit"]["spotLightCount"] == 0
                and gbu_burst_stats["vfx"]["samples"]["totalFragmentJetsSpawned"] >= 6
                and gbu_burst_stats["vfx"]["samples"]["totalSparkStreaksSpawned"] >= 8
            )
            record("GBU-31爆轰破片烟丝、深度采样、真实DrawCall遥测与<=6池校验", ok_gbu_burst, gbu_burst_stats)

            # 关键帧 3：~2.0s 方向性受光立体翻滚黑烟 + 缝隙黑体火焰 + Soft Particles 接地羽化
            step_frames(page, 105)
            snap(page, "03-gbu31-03-plume-soft.png")

            # 关键帧 4：~5.2s FBM 非规则焦坑翻土弹坑
            step_frames(page, 195)
            snap(page, "04-gbu31-04-crater.png")

            # 1b) QA-09：无 reset 连续多波并发连爆压力验证（Hydra + AGM-65 + GBU-31 叠加）
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('rocket', { targetPos: { x: -18, z: 30 }, fastApproach: true });
                }"""
            )
            step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 2",
                max_steps=900,
                chunk=2,
            )
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('missile', { targetPos: { x: -2, z: 42 }, fastApproach: true });
                }"""
            )
            step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 3",
                max_steps=900,
                chunk=2,
            )
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('bomb', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 4",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 12)
            stress_stats = page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    const audit = lab.getRenderAudit();
                    return {
                        vfx: lab.getVfxStats(),
                        pointLightCount: audit.pointLightCount,
                        spotLightCount: audit.spotLightCount,
                        mainSceneDrawCalls: audit.mainSceneDrawCalls,
                    };
                }"""
            )
            ok_stress = (
                stress_stats["vfx"]["poolDrawCalls"] <= 6
                and stress_stats["vfx"]["activeCraters"] >= 4
                and stress_stats["pointLightCount"] == 0
                and stress_stats["spotLightCount"] == 0
            )
            snap(page, "04b-multi-wave-stress.png")
            record("无Reset多波连续并发连爆压力与<=6DrawCall校验", ok_stress, stress_stats)

            # 2) GAU-8 30mm 机炮扫射接地（覆盖 red / green / mixed / none 模式兼容）
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    lab.executeAction('tracer-red');
                    lab.executeAction('focus-range');
                    lab.executeAction('strafe', {
                        startPos: { x: -45, z: 36 },
                        endPos: { x: 45, z: 36 },
                        targetPos: { x: -10, z: 36 },
                        fastApproach: true,
                    });
                }"""
            )
            hit_strafe_muzzle = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getWeaponsState()[0].ammo < 1140 && window.__SCAVENGER_AIR_LAB__.getUnitSummary().flightState === 'strafe'",
                max_steps=900,
                chunk=1,
            )
            # 先把相机切到正在扫射的 A-10C 并跟踪推进 ~0.28s，完整捕捉机首炮口亮光 + 浅灰火药烟雾拖尾 + 机首局部暖光
            page.evaluate(
                """async () => {
                    const u = window.__SCAVENGER_AIR_LAB__.getUnitSummary();
                    if (u?.position && window.__SCV_MAP__?.setCameraView) {
                        const projOffset = (u.position[1] || 0) * 0.78;
                        await window.__SCV_MAP__.setCameraView(u.position[0] + 15, u.position[2] - projOffset, 125);
                    }
                }"""
            )
            step_frames(page, 16)
            page.evaluate(
                """async () => {
                    const u = window.__SCAVENGER_AIR_LAB__.getUnitSummary();
                    if (u?.position && window.__SCV_MAP__?.setCameraView) {
                        const projOffset = (u.position[1] || 0) * 0.78;
                        await window.__SCV_MAP__.setCameraView(u.position[0] - 12, u.position[2] - projOffset, 125);
                    }
                }"""
            )
            step_frames(page, 2)
            snap(page, "05a-gau8-muzzle-closeup.png")
            page.evaluate("async () => { await window.__SCV_MAP__.setCameraView(-10, 36, 280); }")

            hit_strafe_red = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().samples.totalLightSplashRingsSpawned >= 3 && window.__SCAVENGER_AIR_LAB__.getVfxStats().samples.tracerStats.red >= 2",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 18)
            strafe_red_stats = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getVfxStats()")
            snap(page, "05-gau8-strafe-red.png")
            record("30mm扫射红曳光、机首浅灰火药烟拖尾与密集覆盖带轰击", hit_strafe_muzzle["matched"] and hit_strafe_red["matched"], strafe_red_stats["samples"])

            # 切换绿曳光 + mixed + none 验证 API 与计数兼容
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('tracer-green');
                    lab.executeAction('strafe', {
                        startPos: { x: -45, z: 36 },
                        endPos: { x: 45, z: 36 },
                        targetPos: { x: -10, z: 36 },
                        fastApproach: true,
                    });
                }"""
            )
            hit_strafe_green = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().samples.tracerStats.green >= 1",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 6)
            snap(page, "06-gau8-strafe-green.png")
            tracer_mode_check = page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    const m1 = lab.setTracerColorMode('mixed');
                    const m2 = lab.setTracerColorMode('none');
                    const m3 = lab.setTracerColorMode('red');
                    return { m1, m2, m3, cur: lab.getTracerColorMode() };
                }"""
            )
            record(
                "30mm绿曳光及setTracerColorMode四态兼容",
                hit_strafe_green["matched"] and tracer_mode_check == {"m1": "mixed", "m2": "none", "m3": "red", "cur": "red"},
                tracer_mode_check,
            )

            # 3) Hydra 70 火箭齐射
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    lab.executeAction('focus-range');
                    lab.executeAction('rocket', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            hit_rocket = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 2",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 10)
            snap(page, "07-hydra-rocket.png")
            record("Hydra 70火箭齐射命中与爆轰", hit_rocket["matched"], hit_rocket)

            # 4) AGM-65 导弹飞行中尾焰湍流烟带 + 落地后尾烟柔和羽化（对照图2/图3）
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    lab.executeAction('focus-range');
                    lab.executeAction('missile', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            in_flight_missile = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().pools.ordnance >= 1 && window.__SCAVENGER_AIR_LAB__.getVfxStats().pools.ribbon >= 1",
                max_steps=900,
                chunk=1,
            )
            step_frames(page, 48)
            page.evaluate(
                """async () => {
                    const st = window.__SCAVENGER_AIR_LAB__.getVfxStats();
                    const ord = st?.samples?.ords?.[0];
                    if (ord && window.__SCV_MAP__?.setCameraView) {
                        const projOffset = (ord.y || 0) * 0.78;
                        await window.__SCV_MAP__.setCameraView(ord.x, ord.z - projOffset, 220);
                    }
                }"""
            )
            step_frames(page, 2)
            snap(page, "08a-agm65-flight-plume.png")
            page.evaluate("async () => { await window.__SCV_MAP__.setCameraView(-10, 36, 170); }")

            hit_missile = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 30)
            snap(page, "08-agm65-missile.png")
            record("AGM-65导弹马赫尾焰湍流烟柱与落地柔和羽化", in_flight_missile["matched"] and hit_missile["matched"], hit_missile)

            # 4a) 固定翼最高速度直飞 -> 抵达切点自动降速进入盘旋验证
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    lab.executeAction('sprint', { targetPos: { x: 900, z: 600 } });
                }"""
            )
            step_frames(page, 60)
            sprint_mid = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getUnitSummary()")
            arrived_orbit = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getUnitSummary().flightState === 'orbit'",
                max_steps=1200,
                chunk=4,
            )
            step_frames(page, 60)
            sprint_end = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getUnitSummary()")
            ok_sprint = (
                sprint_mid["flightState"] == "sprint"
                and sprint_mid["anim"]["speedMps"] > 125
                and arrived_orbit["matched"] is True
                and sprint_end["flightState"] == "orbit"
                and sprint_end["anim"]["speedMps"] < 110
            )
            record(
                "固定翼最高速度直飞转场与抵达自动降速盘旋校验",
                ok_sprint,
                {"mid": sprint_mid, "end": sprint_end, "arrived": arrived_orbit},
            )

            # 4b) QA-05：视锥外重弹爆炸后移镜回看，验证既有焦坑又有长寿黑烟柱（不出现“有坑无烟”）
            page.evaluate(
                """async () => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    await window.__SCV_MAP__.setCameraView(950, 950, 260);
                    lab.executeAction('bomb', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            hit_offscreen = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=900,
                chunk=2,
            )
            # 爆炸后约 0.8s 再把镜头拉回靶区，确认长寿黑烟柱仍在持续翻涌
            step_frames(page, 45)
            page.evaluate("async () => { await window.__SCV_MAP__.setCameraView(-10, 36, 260); }")
            step_frames(page, 4)
            offscreen_stats = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getVfxStats()")
            snap(page, "08b-offscreen-return-plume.png")
            ok_offscreen = (
                hit_offscreen["matched"]
                and offscreen_stats["activeCraters"] >= 1
                and offscreen_stats["pools"]["smokeChunk"] >= 4
            )
            record("QA-05视锥外重弹移镜回看仍有长寿黑烟柱", ok_offscreen, offscreen_stats)

            # 5) 热诱弹 (flare) + 受击冒烟 + 强制坠毁（下坠连续烟带 + 撞地斜风黑烟明火 + 涡流熄灭 + 名牌状态）
            page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    lab.executeAction('reset');
                    lab.executeAction('focus-plane');
                    lab.executeAction('flare');
                    lab.executeAction('smoke');
                }"""
            )
            step_frames(page, 35)
            flare_stats = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getVfxStats()")
            snap(page, "09-flare-smoke.png")
            record("热诱弹发射与受击冒烟兼容", flare_stats["flareBurstCount"] >= 1, flare_stats)

            page.evaluate("() => { window.__SCAVENGER_AIR_LAB__.executeAction('crash'); }")
            # 先推进 ~1.0s 观察下坠中段（QA-04 连续黑烟带 + 非串珠），按相机倾角补偿机身高程以完整框住机身与尾烟
            step_frames(page, 60)
            page.evaluate(
                """async () => {
                    const u = window.__SCAVENGER_AIR_LAB__.getUnitSummary();
                    if (u?.position && window.__SCV_MAP__?.setCameraView) {
                        const cam = window.__SCV_MAP__.getCameraState?.() || { angle: 0 };
                        const ang = Number(cam.angle) || 0;
                        // 正交俯视 TILT≈52° 时高空 y 投影到地面偏移约 y / tan(52°) ≈ 0.78 * y
                        const projOffset = (u.position[1] || 0) * 0.78;
                        const cx = u.position[0] - Math.sin(ang) * projOffset;
                        const cz = u.position[2] - Math.cos(ang) * projOffset;
                        await window.__SCV_MAP__.setCameraView(cx, cz, 300);
                    }
                }"""
            )
            step_frames(page, 4)
            falling_state = page.evaluate(
                """() => ({
                    unit: window.__SCAVENGER_AIR_LAB__.getUnitSummary(),
                    vfx: window.__SCAVENGER_AIR_LAB__.getVfxStats(),
                })"""
            )
            snap(page, "10a-crash-falling-ribbon.png")

            hit_crash = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getUnitSummary().flightState === 'crashed'",
                max_steps=900,
                chunk=3,
            )
            # 镜头聚焦到坠毁点并推进 2.2s，观察持续斜风黑烟柱 + 底层明火瓣 + 确认翼尖涡流已清除 + 名牌文本正确
            page.evaluate(
                """async () => {
                    const u = window.__SCAVENGER_AIR_LAB__.getUnitSummary();
                    if (u?.position && window.__SCV_MAP__?.setCameraView) {
                        await window.__SCV_MAP__.setCameraView(u.position[0], u.position[2], 220);
                    }
                }"""
            )
            step_frames(page, 130)
            crash_check = page.evaluate(
                """() => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    const labels = Array.from(document.querySelectorAll('#unit-labels .unit-label, .scv-unit-label, .unit-label-meta'))
                        .map(el => (el.textContent || '').trim())
                        .filter(Boolean);
                    return {
                        unit: lab.getUnitSummary(),
                        vfx: lab.getVfxStats(),
                        labels,
                    };
                }"""
            )
            snap(page, "10-crash-wreck-smoke-flame.png")
            labels_joined = " | ".join(crash_check["labels"])
            ok_crash = (
                hit_crash["matched"]
                and crash_check["unit"]["flightState"] == "crashed"
                and crash_check["unit"]["wingtipVortexActive"] is False
                and crash_check["vfx"]["wingtipVortexActive"] is False
                and crash_check["vfx"]["wingtipFadeAlpha"] == 0
                and crash_check["vfx"]["pools"]["smokeChunk"] >= 4
                and ("盘旋" not in labels_joined)
            )
            record(
                "强制坠毁下坠连续烟带、撞地斜风黑烟明火、涡流归零与名牌状态校验",
                ok_crash,
                {"falling": falling_state["unit"]["flightState"], "crash": crash_check},
            )

            # 6) 21:9 画幅 (1588x680) + 详情卡零遮挡几何断言 (QA-06) + 关 Bloom 直渲爆闪核 (QA-08) + 高空视距
            # 切至 1280x720 典型 16:9 窗口，测试 21:9 信箱模式下展开详情卡绝不遮挡左上角状态栏
            page.set_viewport_size({"width": 1280, "height": 720})
            page.evaluate(
                """async () => {
                    const map = window.__SCV_MAP__;
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    map.setAspectProfile('21:9');
                    lab.executeAction('reset');
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    await map.setCameraView(-10, 36, 280);
                }"""
            )
            step_frames(page, 6)
            page.click("#btn-unit-detail-toggle")
            step_frames(page, 4)
            detail_geom = page.evaluate(
                """() => {
                    const card = document.getElementById('unit-detail-card');
                    const topLeft = document.querySelector('.scv-top-left');
                    const host = document.getElementById('canvas-host');
                    const cr = card ? card.getBoundingClientRect() : null;
                    const tr = topLeft ? topLeft.getBoundingClientRect() : null;
                    return {
                        hidden: card ? card.hidden : true,
                        stageH: host ? getComputedStyle(host).getPropertyValue('--scv-stage-h').trim() : '',
                        cardTop: cr ? Math.round(cr.top) : -1,
                        cardBottom: cr ? Math.round(cr.bottom) : -1,
                        cardHeight: cr ? Math.round(cr.height) : 0,
                        topLeftBottom: tr ? Math.round(tr.bottom) : -1,
                        clearancePx: (cr && tr) ? Math.round(cr.top - tr.bottom) : -999,
                    };
                }"""
            )
            snap(page, "11b-21x9-detail-card-no-overlap.png")
            page.click("#btn-unit-detail-toggle")
            page.set_viewport_size({"width": 1600, "height": 900})
            page.evaluate("() => { window.__SCV_MAP__.setAspectProfile('21:9'); }")
            step_frames(page, 4)
            ok_detail_card = (
                detail_geom["hidden"] is False
                and detail_geom["cardHeight"] >= 120
                and detail_geom["clearancePx"] >= 4
            )
            record("QA-06 21:9信箱画幅展开单位详情卡与左上状态栏零遮挡校验", ok_detail_card, detail_geom)

            page.evaluate(
                """() => {
                    window.__SCAVENGER_AIR_LAB__.executeAction('bomb', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=900,
                chunk=2,
            )
            step_frames(page, 18)
            snap(page, "11-21x9-gbu31.png")

            # QA-08：先校验关闭 Bloom 时热浪/景深仍解耦留在 Composer（线性输出不发白），再全关后处理切纯直渲 sRGB 路径实爆 GBU-31
            decoupled_state = page.evaluate(
                """() => {
                    window.__SCV_MAP__.setBloomEnabled(false, false);
                    window.__SCV_MAP__.stepSimAndRender(0.0167, performance.now() * 0.001, true);
                    return window.__SCV_MAP__.getPostprocessState();
                }"""
            )
            page.evaluate(
                """() => {
                    window.__SCV_MAP__.setDofMode('off', false);
                    window.__SCV_MAP__.setHeatShimmerEnabled(false);
                    window.__SCAVENGER_AIR_LAB__.executeAction('reset');
                    if (!window.__SCAVENGER_AIR_LAB__.getConfig().fastApproachMode) {
                        window.__SCAVENGER_AIR_LAB__.executeAction('fast-approach');
                    }
                    window.__SCAVENGER_AIR_LAB__.executeAction('bomb', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=900,
                chunk=1,
            )
            step_frames(page, 2)
            direct_state = page.evaluate(
                """() => ({
                    post: window.__SCV_MAP__.getPostprocessState(),
                    baseline: window.__SCV_MAP__.getRenderBaseline(),
                    vfx: window.__SCAVENGER_AIR_LAB__.getVfxStats(),
                })"""
            )
            snap(page, "12-bloom-off-direct.png")

            # 高空视距 span=950m LOD 与 21:9 远景验证（恢复 Bloom + 热浪 + 柔和景深）
            page.evaluate(
                """async () => {
                    window.__SCV_MAP__.setHeatShimmerEnabled(true);
                    window.__SCV_MAP__.setDofMode('soft', false);
                    window.__SCV_MAP__.setBloomEnabled(true, false);
                    await window.__SCV_MAP__.setCameraView(-10, 36, 950);
                }"""
            )
            step_frames(page, 8)
            high_alt_stats = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getVfxStats()")
            snap(page, "12b-21x9-high-alt-950m.png")
            ok_decoupled = (
                decoupled_state["bloomEnabled"] is False
                and decoupled_state["bloomPassEnabled"] is False
                and decoupled_state["composerActive"] is True
                and decoupled_state["heatShimmerActive"] is True
                and decoupled_state["vfxLinearOutput"] is True
            )
            ok_21x9_direct = (
                ok_decoupled
                and direct_state["baseline"]["activeResolution"] == [1588, 680]
                and direct_state["post"]["bloomEnabled"] is False
                and direct_state["post"]["composerActive"] is False
                and direct_state["post"]["vfxLinearOutput"] is False
                and direct_state["vfx"]["poolDrawCalls"] <= 6
                and direct_state["vfx"]["lastFrameUsedDepth"] is True
                and high_alt_stats["lodTier"] == "mid"
            )
            record(
                "21:9画幅Resize深度重建、Bloom解耦热浪保留、高空950m LOD与纯直渲爆闪校验",
                ok_21x9_direct,
                {"decoupled": decoupled_state, "direct": direct_state, "high_alt": high_alt_stats},
            )

            # --- 场景 2：US_B1B 高空四发凝结尾迹 + 战略重磅航弹实爆验证 (QA-09) ---
            page.goto(f"{base_url}/play?mode=air-lab&unit=US_B1B&aspect=16:9", wait_until="domcontentloaded", timeout=50000)
            wait_for_airlab_ready(page)
            page.evaluate("() => window.__SCV_MAP__.waitForFullPrecision?.()")
            step_frames(page, 85)
            page.evaluate(
                """async () => {
                    const u = window.__SCAVENGER_AIR_LAB__.getUnitSummary();
                    if (u?.position) await window.__SCV_MAP__.setCameraView(u.position[0], u.position[2], 320);
                }"""
            )
            step_frames(page, 4)
            b1b_stats = page.evaluate(
                """() => ({
                    vfx: window.__SCAVENGER_AIR_LAB__.getVfxStats(),
                    weapons: window.__SCAVENGER_AIR_LAB__.getWeaponsState(),
                })"""
            )
            snap(page, "13-b1b-contrail.png")
            has_heavy_bomb = any(w.get("explosionTier") == "heavy" for w in b1b_stats["weapons"])
            record("US_B1B高空四发凝结尾迹与重磅航弹配置回归", b1b_stats["vfx"]["contrailEmittedCount"] > 0 and has_heavy_bomb, b1b_stats)

            page.evaluate(
                """async () => {
                    const lab = window.__SCAVENGER_AIR_LAB__;
                    if (!lab.getConfig().fastApproachMode) lab.executeAction('fast-approach');
                    await window.__SCV_MAP__.setCameraView(-10, 36, 280);
                    lab.executeAction('bomb', { targetPos: { x: -10, z: 36 }, fastApproach: true });
                }"""
            )
            hit_b1b_bomb = step_until_condition(
                page,
                "window.__SCAVENGER_AIR_LAB__.getVfxStats().activeCraters >= 1",
                max_steps=1800,
                chunk=3,
            )
            step_frames(page, 14)
            snap(page, "13b-b1b-heavy-bomb.png")
            record("QA-09 US_B1B战略重磅航弹实爆验证", hit_b1b_bomb["matched"], hit_b1b_bomb)

            # --- 场景 3：常规 /play 9km 实战隔离 + Live PiP + 21:9 真实渲染 Resize ---
            page.goto(f"{base_url}/play?aspect=16:9", wait_until="domcontentloaded", timeout=50000)
            page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
            page.wait_for_timeout(450)
            play_state = page.evaluate(
                """() => {
                    window.__SCV_MAP__.setManualLoop(true);
                    window.__SCV_MAP__.setAspectProfile('21:9');
                    window.__SCV_MAP__.stepSimAndRender(0.0167, 1.0, true);
                    window.__SCV_MAP__.stepSimAndRender(0.0167, 1.0167, true);
                    const post21x9 = window.__SCV_MAP__.getPostprocessState();
                    window.__SCV_MAP__.setAspectProfile('16:9');
                    window.__SCV_MAP__.stepSimAndRender(0.0167, 1.0334, true);
                    window.__SCV_MAP__.stepSimAndRender(0.0167, 1.05, true);
                    window.__SCV_MAP__.finishGpu?.();
                    return {
                        airLabUndefined: typeof window.__SCAVENGER_AIR_LAB__ === 'undefined',
                        post21x9,
                        post: window.__SCV_MAP__.getPostprocessState(),
                        vfx: window.__SCV_MAP__.getVfxStats(),
                        hud: window.__SCV_MAP__.getUiHudState(),
                    };
                }"""
            )
            snap(page, "14-play-normal-pip.png")
            ok_play = (
                play_state["airLabUndefined"] is True
                and play_state["post21x9"]["internalWidth"] == 1588
                and play_state["post21x9"]["depthSplitEnabled"] is True
                and play_state["post"]["depthSplitEnabled"] is True
                and play_state["vfx"]["poolDrawCalls"] <= 6
            )
            record("常规/play实战隔离、Live PiP与21:9实渲Resize回归", ok_play, play_state)

            # 零控制台/页面错误门禁
            record("全程浏览器控制台与页面零报错", len(report["errors"]) == 0, report["errors"])

        finally:
            browser.close()

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report_path = out_dir / "vfx-v30-verification-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Scavenger 写实爆炸特效 V3.0 实机多武器自动化验证")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="唯一主服务地址（默认 http://127.0.0.1:8800）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="截图与遥测输出目录")
    parser.add_argument("--channel", default="chrome", choices=["chrome", "msedge"], help="浏览器通道")
    args = parser.parse_args()

    proc = ensure_main_server(args.base_url)
    try:
        report = run_verification(args.base_url, Path(args.out_dir), channel=args.channel)
        print(
            json.dumps(
                {
                    "status": "PASSED",
                    "checks_passed": len(report["checks"]),
                    "screenshots": len(report["screenshots"]),
                    "out_dir": str(Path(args.out_dir)),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
