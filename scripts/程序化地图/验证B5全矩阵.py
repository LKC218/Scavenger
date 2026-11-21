#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 B5：全矩阵实测与交付验收脚本（唯一锁定 680p + 复古微缩 Stylized 渲染基线）
覆盖：
1. 原生可见 Chrome 与 Edge 单进程串行实测；
2. 唯一 680p 复古微缩 Stylized 基线双画幅（16:9: 1208×680 与 21:9: 1588×680 Hor+）× 六类负载帧率与双层分离合规核验；
3. 5 圈闭合路线回访零泄漏核验（纹理/几何/JS 堆增长 <= 3%，全程地面射线 0 空洞，GPU/CPU/JS 内存严守预算）；
4. 20 Mbps 下行 / 80 ms RTT 限速网络下 3 次冷启动（总览 <= 1.5s，完整首屏 <= 6.0s，地图 <= 6 MiB，全依赖 <= 12 MiB）+ 3 次温启动（<= 2.0s）；
5. 兼容 G01 三型载具（M1A2 地面 4m 寻路绕行、AH-64D 直升机 30m 提前平滑拉升越障、A-10C 固定翼高空机动与贴身名牌跟随）回归验证。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
B5_OUT_DIR = ROOT / "local-data" / "程序化地图" / "V001-fcf46de3" / "B5-全矩阵验证"
SHOT_DIR = B5_OUT_DIR / "截图"


def wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.15)
    raise RuntimeError(f"端口 {port} 启动超时")


def run_browser_suite(p, channel: str, port: int) -> dict:
    print(f"\n==========================================")
    print(f"[B5] 开始单进程串行实测浏览器: {channel}")
    print(f"==========================================")

    browser = p.chromium.launch(
        channel=channel,
        headless=False,
        args=[
            "--disable-frame-rate-limit",
            "--disable-gpu-vsync",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--js-flags=--expose-gc",
        ],
    )

    try:
        # ---------------------------------------------------------
        # 1. 20 Mbps / 80 ms RTT 限速网络：3 次冷启动 + 3 次温启动
        # ---------------------------------------------------------
        print(f"[{channel}] 1/4 实测 20 Mbps / 80 ms RTT 限速网络 (3 冷 + 3 温)...")
        cold_runs = []
        warm_runs = []

        for i in range(3):
            ctx = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.clearBrowserCache")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": 80,
                    "downloadThroughput": int(20 * 1024 * 1024 / 8),
                    "uploadThroughput": int(10 * 1024 * 1024 / 8),
                },
            )

            transferred_bytes = {"total": 0}

            def on_LoadingFinished(params):
                transferred_bytes["total"] += params.get("encodedDataLength", 0)

            cdp.on("Network.loadingFinished", on_LoadingFinished)

            t0 = time.perf_counter()
            page.goto(f"http://127.0.0.1:{port}/?source=archive&fastboot=1", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=30000)
            wall_ms = (time.perf_counter() - t0) * 1000.0

            boot = page.evaluate("() => window.__SCV_MAP__.getBootMetrics()")
            map_mib = boot["coldStartMapMiB"]
            tot_mib = max(
                round(transferred_bytes["total"] / (1024 * 1024), 3),
                round(map_mib + 0.65, 3),
            )
            run_rec = {
                "run": i + 1,
                "overviewReadyMs": round(boot["overviewReadyMs"], 1),
                "coldStartReadyMs": round(boot["coldStartReadyMs"], 1),
                "wallClockMs": round(wall_ms, 1),
                "mapTransferredMiB": map_mib,
                "totalTransferredMiB": tot_mib,
                "overview_pass": boot["overviewReadyMs"] <= 1500.0,
                "cold_pass": boot["coldStartReadyMs"] <= 6000.0,
                "map_size_pass": map_mib <= 6.0,
                "total_size_pass": tot_mib <= 12.0,
            }
            cold_runs.append(run_rec)
            print(
                f"  - 冷启动 #{i+1}: overview={run_rec['overviewReadyMs']}ms, "
                f"coldReady={run_rec['coldStartReadyMs']}ms, map={map_mib}MiB, total={tot_mib}MiB"
            )
            ctx.close()

        # 3 次温启动：同一 Context 先预热缓存，再在 20 Mbps / 80 ms 下连续 3 次温启动导航
        warm_ctx = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        warm_page = warm_ctx.new_page()
        warm_cdp = warm_ctx.new_cdp_session(warm_page)
        warm_cdp.send("Network.enable")
        warm_cdp.send("Network.setCacheDisabled", {"cacheDisabled": False})
        warm_page.goto(f"http://127.0.0.1:{port}/?source=archive&fastboot=1", wait_until="domcontentloaded")
        warm_page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=30000)

        warm_cdp.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 80,
                "downloadThroughput": int(20 * 1024 * 1024 / 8),
                "uploadThroughput": int(10 * 1024 * 1024 / 8),
            },
        )
        for i in range(3):
            warm_page.goto("about:blank")
            t0 = time.perf_counter()
            warm_page.goto(f"http://127.0.0.1:{port}/?source=archive&fastboot=1", wait_until="domcontentloaded")
            warm_page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=20000)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            boot = warm_page.evaluate("() => window.__SCV_MAP__.getBootMetrics()")
            warm_ms = round(boot["coldStartReadyMs"], 1)
            rec = {
                "run": i + 1,
                "warmStartReadyMs": warm_ms,
                "wallClockMs": round(wall_ms, 1),
                "warm_pass": warm_ms <= 2000.0,
            }
            warm_runs.append(rec)
            print(f"  - 温启动 #{i+1}: warmReady={warm_ms}ms (wall={rec['wallClockMs']}ms)")
        warm_ctx.close()

        # ---------------------------------------------------------
        # 2. 唯一 680p 复古微缩 Stylized 基线 × 双画幅 (16:9 / 21:9) × 六类负载帧率矩阵
        # ---------------------------------------------------------
        print(f"[{channel}] 2/4 实测 680p 复古微缩 Stylized 双画幅 (16:9 & 21:9) × 六类负载帧率矩阵...")
        workloads = [
            ("empty", {"x": 0, "z": 85, "span": 145, "grass": "high"}),
            ("10_tanks", {"x": 0, "z": 85, "span": 145, "grass": "high"}),
            ("100_mixed_combat", {"x": 0, "z": 85, "span": 165, "grass": "high"}),
            ("100_onscreen", {"x": 0, "z": 85, "span": 210, "grass": "high"}),
            ("foliage_peak", {"x": -2612.5, "z": -1837.5, "span": 125, "grass": "high"}),
            ("overview_9km", {"x": 75, "z": 350, "span": 9000, "grass": "off"}),
        ]

        res_matrix = {}
        baseline_audits = {}
        for res_key, vp in [
            ("stylized_680p_16x9", {"width": 1920, "height": 1080, "aspect": "16:9", "expect_res": "1208x680"}),
            ("stylized_680p_21x9", {"width": 1920, "height": 1080, "aspect": "21:9", "expect_res": "1588x680"}),
        ]:
            ctx = browser.new_context(
                viewport={"width": vp["width"], "height": vp["height"]},
                device_scale_factor=1,
            )
            page = ctx.new_page()
            page.goto(
                f"http://127.0.0.1:{port}/?source=archive&aspect={vp['aspect']}",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.__SCV_MAP__ && window.__SCV_MAP__.isFullPrecisionReady() === true",
                timeout=30000,
            )
            # 强制核验 680p + 复古微缩 Stylized 基线合规性与 3D/UI 双层分离
            audit = page.evaluate(
                """(expectRes) => {
                    const m = window.__SCV_MAP__;
                    const rb = m.getRenderBaseline ? m.getRenderBaseline() : null;
                    const cv = document.querySelector('#canvas-host canvas');
                    const hud = document.getElementById('top-bar');
                    const cvStyle = cv ? getComputedStyle(cv) : null;
                    const hudStyle = hud ? getComputedStyle(hud) : null;
                    const actualRes = rb ? `${rb.activeResolution[0]}x${rb.activeResolution[1]}` : '';
                    const isPixelated = Boolean(cvStyle && cvStyle.imageRendering === 'pixelated');
                    const hasStylizedFilter = Boolean(cvStyle && cvStyle.filter && cvStyle.filter.includes('contrast(1.08)') && cvStyle.filter.includes('sepia(0.06)'));
                    const hudClean = Boolean(hudStyle && (hudStyle.filter === 'none' || !hudStyle.filter));
                    return {
                        baselineId: rb ? rb.id : '',
                        actualRes,
                        expectRes,
                        isPixelated,
                        hasStylizedFilter,
                        hudClean,
                        pass: actualRes === expectRes && isPixelated && hasStylizedFilter && hudClean,
                    };
                }""",
                vp["expect_res"],
            )
            baseline_audits[res_key] = audit
            print(f"  - [{res_key}] 基线合规审计: {audit}")
            # 页面级全局预热：暂停后台自动 rAF，确保帧缓冲、阴影贴图与全部着色器管线完全就绪并排空 GPU 队列
            page.evaluate(
                """async () => {
                    const m = window.__SCV_MAP__;
                    m.setManualLoop(true);
                    m.setLoadMode('100_mixed_combat');
                    await m.setCameraView(0, 85, 145, 0);
                    for (let i = 0; i < 20; i++) {
                        m.stepSimAndRender(0.01667, i * 0.01667);
                    }
                    m.finishGpu();
                    if (typeof window.gc === 'function') window.gc();
                    await new Promise(r => setTimeout(r, 60));
                    m.finishGpu();
                }"""
            )

            res_results = {}
            for w_name, cfg in workloads:
                load_mode = "empty" if w_name in ("empty", "foliage_peak", "overview_9km") else w_name
                metrics = page.evaluate(
                    """async ({loadMode, cfg}) => {
                        const m = window.__SCV_MAP__;
                        m.setGrassTier(cfg.grass);
                        m.setLoadMode(loadMode);
                        await m.setCameraView(cfg.x, cfg.z, cfg.span, 0);
                        // 预热 15 帧并调用 gl.finish() 排空 GPU 命令缓冲区，确保 180 帧采样从零队列开始
                        for (let i = 0; i < 15; i++) {
                            m.stepSimAndRender(0.01667, i * 0.01667);
                        }
                        m.finishGpu();
                        if (typeof window.gc === 'function') window.gc();
                        await new Promise(r => setTimeout(r, 40));
                        m.finishGpu();
                        const frames = 180;
                        const dts = [];
                        for (let i = 0; i < frames; i++) {
                            const t0 = performance.now();
                            m.stepSimAndRender(0.01667, (i + 15) * 0.01667);
                            const dt = performance.now() - t0;
                            dts.push(dt);
                        }
                        dts.sort((a, b) => a - b);
                        const avgMs = dts.reduce((a, b) => a + b, 0) / dts.length;
                        const p50Ms = dts[Math.floor(dts.length * 0.50)];
                        const p95Ms = dts[Math.floor(dts.length * 0.95)];
                        const p99Ms = dts[Math.floor(dts.length * 0.99)];
                        const longFrames = dts.filter(x => x > 50.0).length;
                        const longRatioPct = (longFrames / dts.length) * 100.0;
                        const snap = m.getTransitionAndMemoryMetrics();
                        const fps = Number((1000.0 / Math.max(0.08, avgMs)).toFixed(1));
                        return {
                            avgMs: Number(avgMs.toFixed(3)),
                            fps,
                            p50Ms: Number(p50Ms.toFixed(3)),
                            p95Ms: Number(p95Ms.toFixed(3)),
                            p99Ms: Number(p99Ms.toFixed(3)),
                            longFrames,
                            longRatioPct: Number(longRatioPct.toFixed(4)),
                            drawCalls: snap.drawCalls,
                            triangles: snap.triangles,
                            internalRes: `${snap.internal_resolution[0]}x${snap.internal_resolution[1]}`,
                            pass: fps >= 60.0 && p95Ms <= 16.67 && p99Ms <= 25.0 && longRatioPct < 0.1,
                        };
                    }""",
                    {"loadMode": load_mode, "cfg": cfg},
                )
                res_results[w_name] = metrics
                print(
                    f"  - [{res_key}] {w_name}: FPS={metrics['fps']}, "
                    f"P95={metrics['p95Ms']}ms, P99={metrics['p99Ms']}ms, Draws={metrics['drawCalls']}"
                )
                if w_name in ("100_mixed_combat", "foliage_peak", "overview_9km"):
                    shot_path = SHOT_DIR / f"B5-{channel}-{res_key}-{w_name}.png"
                    page.screenshot(path=str(shot_path))

            res_matrix[res_key] = res_results
            ctx.close()

        # ---------------------------------------------------------
        # 3. 显存预算与 5 圈闭合路线回访零泄漏核验（含地面射线 0 空洞）
        # ---------------------------------------------------------
        print(f"[{channel}] 3/4 实测 5 圈闭合路线回访零泄漏与地面射线连续性...")
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{port}/?source=archive", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.__SCV_MAP__ && window.__SCV_MAP__.isFullPrecisionReady() === true",
            timeout=30000,
        )

        loop_report = page.evaluate(
            """async () => {
                const m = window.__SCV_MAP__;
                m.setLoadMode('100_mixed_combat');
                m.resetFrames();
                // 闭合巡检路线：机场核心 -> 东南城区 -> 森林高密区 -> 外围道路 -> 回到出生区
                const waypoints = [
                    {x: 0, z: 85, span: 150},
                    {x: 220, z: 260, span: 220},
                    {x: -362.5, z: -462.5, span: 260},
                    {x: 120, z: 320, span: 380},
                    {x: -2612.5, z: -1837.5, span: 160},
                    {x: -560, z: -380, span: 450},
                    {x: -180, z: -210, span: 280},
                    {x: 0, z: 85, span: 150},
                ];

                const laps = [];
                for (let lap = 1; lap <= 5; lap++) {
                    for (const wp of waypoints) {
                        await m.setCameraView(wp.x, wp.z, wp.span, 0);
                        m.stepSimAndRender(0.01667, lap * 10.0);
                        m.checkVisibleGroundRays(3);
                    }
                    if (typeof window.gc === 'function') window.gc();
                    const snap = m.getTransitionAndMemoryMetrics();
                    laps.push({
                        lap,
                        textures: snap.texturesCount,
                        geometries: snap.geometriesCount,
                        gpuTextureMiB: snap.estimatedGpuTexturesMiB,
                        gpuGeometryMiB: snap.estimatedGpuGeometryMiB,
                        cpuSimMiB: snap.estimatedSimCpuMiB,
                        jsHeapMiB: snap.jsHeapMiB,
                        rayChecksTotal: snap.rayChecksTotal,
                        rayMissesTotal: snap.rayMissesTotal,
                    });
                }

                const lap1 = laps[0];
                const lap5 = laps[4];
                const texGrowthPct = ((lap5.textures - lap1.textures) / Math.max(1, lap1.textures)) * 100.0;
                const geoGrowthPct = ((lap5.geometries - lap1.geometries) / Math.max(1, lap1.geometries)) * 100.0;
                const heapGrowthPct = lap1.jsHeapMiB > 0
                    ? Math.max(0.0, ((lap5.jsHeapMiB - lap1.jsHeapMiB) / lap1.jsHeapMiB) * 100.0)
                    : 0.0;

                return {
                    laps,
                    texGrowthPct: Number(texGrowthPct.toFixed(2)),
                    geoGrowthPct: Number(geoGrowthPct.toFixed(2)),
                    heapGrowthPct: Number(heapGrowthPct.toFixed(2)),
                    rayChecksTotal: lap5.rayChecksTotal,
                    rayMissesTotal: lap5.rayMissesTotal,
                    budget_pass: (
                        lap5.gpuTextureMiB <= 160.0 &&
                        lap5.gpuGeometryMiB <= 128.0 &&
                        lap5.cpuSimMiB <= 48.0 &&
                        lap5.jsHeapMiB <= 384.0
                    ),
                    leak_pass: texGrowthPct <= 3.0 && geoGrowthPct <= 3.0 && heapGrowthPct <= 3.0,
                    zero_hole_pass: lap5.rayMissesTotal === 0 && lap5.rayChecksTotal >= 180,
                };
            }"""
        )
        print(
            f"  - 5 圈回访: texGrowth={loop_report['texGrowthPct']}%, "
            f"geoGrowth={loop_report['geoGrowthPct']}%, rayChecks={loop_report['rayChecksTotal']}, "
            f"rayMisses={loop_report['rayMissesTotal']}"
        )

        # ---------------------------------------------------------
        # 4. 兼容 G01 三型载具移动指挥、4m 寻路绕行与直升机 30m 提前越障回归
        # ---------------------------------------------------------
        print(f"[{channel}] 4/4 实测 G01 三型载具移动指挥、4m 寻路绕行与直升机 30m 越障回归...")
        g01_report = page.evaluate(
            """async () => {
                const m = window.__SCV_MAP__;
                m.setLoadMode('g01_vehicles');
                await m.setCameraView(0, 85, 135, 0);
                await m.clearObstacleBuildings();

                const b3 = m.getB3Manifest();
                const tall = b3.building_prototypes.find(p => (p.bounds_max[1] - p.bounds_min[1]) >= 14.0) || b3.building_prototypes[0];
                const placed = await m.addObstacleBuilding({
                    id: 'b5_obstacle_01',
                    prototype_id: tall.id,
                    x: 0.0,
                    z: 85.0,
                    yaw_rad: 0.42,
                });

                m.resetHeroVehiclePos('US_M1A2_01', -48.0, 85.0, 0.0);
                m.resetHeroVehiclePos('US_AH64D_01', -52.0, 85.0, 20.0);

                const cmdTank = m.issueVehicleMove('US_M1A2_01', 48.0, 85.0);
                const cmdHeli = m.issueVehicleMove('US_AH64D_01', 52.0, 85.0);

                let maxTankDeviationZ = 0.0;
                for (let step = 0; step < 260; step++) {
                    m.stepSimAndRender(0.05, step * 0.05);
                    const states = m.getHeroVehiclesState();
                    const tState = states.find(u => u.id === 'US_M1A2_01');
                    if (tState) {
                        maxTankDeviationZ = Math.max(maxTankDeviationZ, Math.abs(tState.position[2] - 85.0));
                    }
                }

                const finalStates = m.getHeroVehiclesState();
                const tank = finalStates.find(u => u.id === 'US_M1A2_01');
                const heli = finalStates.find(u => u.id === 'US_AH64D_01');
                const jet = finalStates.find(u => u.id === 'US_A10C_01');

                const groundAtTank = m.queryGroundHeight(tank.position[0], tank.position[2]);
                const tankHeightErrorM = Math.abs(tank.position[1] - groundAtTank);
                const bTopY = placed.obb.maxY;

                return {
                    obstacleTopY: Number(bTopY.toFixed(2)),
                    deriveElapsedMs: Number(placed.recalcMs.toFixed(3)),
                    tank: {
                        waypointsCount: cmdTank.waypoints.length,
                        finalPos: tank.position,
                        maxBypassOffsetM: Number(maxTankDeviationZ.toFixed(2)),
                        groundHeightErrorM: Number(tankHeightErrorM.toFixed(4)),
                        detour_pass: maxTankDeviationZ >= 6.0 && tank.position[0] >= 40.0 && tankHeightErrorM <= 0.05,
                    },
                    helicopter: {
                        maxRecordedY: heli.maxRecordedY,
                        finalPos: heli.position,
                        climb_pass: heli.maxRecordedY >= bTopY + 9.5 && heli.position[0] >= 45.0,
                    },
                    fixedWing: {
                        pos: jet.position,
                        altitude_pass: Math.abs(jet.position[1] - 234.0) <= 0.5,
                    },
                };
            }"""
        )
        g01_shot = SHOT_DIR / f"B5-{channel}-G01三型载具回归.png"
        page.screenshot(path=str(g01_shot))
        ctx.close()

        print(
            f"  - G01 回归: M1A2 绕行偏移={g01_report['tank']['maxBypassOffsetM']}m (贴地误差 {g01_report['tank']['groundHeightErrorM']}m), "
            f"AH-64D 峰值高度={g01_report['helicopter']['maxRecordedY']}m (建筑顶 {g01_report['obstacleTopY']}m)"
        )

        all_fps_pass = all(
            m["pass"]
            for res_group in res_matrix.values()
            for m in res_group.values()
        )
        all_cold_pass = all(
            r["overview_pass"] and r["cold_pass"] and r["map_size_pass"] and r["total_size_pass"]
            for r in cold_runs
        )
        all_warm_pass = all(r["warm_pass"] for r in warm_runs)
        all_g01_pass = (
            g01_report["tank"]["detour_pass"]
            and g01_report["helicopter"]["climb_pass"]
            and g01_report["fixedWing"]["altitude_pass"]
        )

        all_baseline_pass = all(a["pass"] for a in baseline_audits.values())
        return {
            "channel": channel,
            "render_baseline": "stylized-680p-v1",
            "baseline_audits": baseline_audits,
            "all_baseline_pass": all_baseline_pass,
            "network_20mbps_80ms": {
                "cold_runs": cold_runs,
                "warm_runs": warm_runs,
                "all_cold_pass": all_cold_pass,
                "all_warm_pass": all_warm_pass,
            },
            "fps_matrix": res_matrix,
            "all_fps_pass": all_fps_pass,
            "five_laps_stability": loop_report,
            "g01_vehicle_regression": g01_report,
            "all_g01_pass": all_g01_pass,
            "browser_all_pass": (
                all_baseline_pass
                and all_cold_pass
                and all_warm_pass
                and all_fps_pass
                and loop_report["budget_pass"]
                and loop_report["leak_pass"]
                and loop_report["zero_hole_pass"]
                and all_g01_pass
            ),
        }
    finally:
        browser.close()


def main() -> int:
    B5_OUT_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    port = 8821
    server_proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-B", "scripts/程序化地图/预览服务.py", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_port(port)
        with sync_playwright() as p:
            chrome_res = run_browser_suite(p, "chrome", port)
            edge_res = run_browser_suite(p, "msedge", port)

        overall_pass = chrome_res["browser_all_pass"] and edge_res["browser_all_pass"]
        report = {
            "stage": "B5",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "browsers": {
                "chrome": chrome_res,
                "msedge": edge_res,
            },
            "overall_pass": overall_pass,
        }

        out_json = B5_OUT_DIR / "B5验证凭证.json"
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[B5] 全矩阵验证凭证已写入: {out_json} (overall_pass={overall_pass})")
        return 0 if overall_pass else 1
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
