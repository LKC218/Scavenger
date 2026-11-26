"""Scavenger 网页端硬件 WebGL 性能基线与 Phase 1 门禁自动采集脚本（只读探针）。

覆盖项：
1. 普通玩家单次冷启动与后台全精度升级真实基线（不外部重复调用 waitForFullPrecision，
   分离记录冷启动 LongTask 观察项与 coreReady 后后台升级 LongTask / 峰值堆，作为 Phase 3 触发闸门）；
2. upgradeToFullPrecision() 并发重入单例锁校验 + WebGL 绘制探针核验道面网格仅提交 1 次；
3. 热路径每帧强制回流（#canvas-host clientWidth/Height）、getComputedStyle 与静止名牌 DOM 写入归零核验；
4. 正式 50 车满编（MAX_HERO_UNITS = 50）与修参 100 混编（50 己方）10Hz 纯模拟耗时及实时帧率/P95；
5. 460m 中景与快速缩放观察项（供 Phase 2 参考）；
6. /editor 放楼、清楼与地形笔刷后的战争迷雾遮挡实时局部失效闭环校验。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_BASE = ROOT / "tmp" / "perf-opt-baseline"


INIT_PROBE_JS = r"""
(() => {
  const perfProbe = {
    navStart: performance.now(),
    longTasks: [],
    drawCaptureActive: false,
    drawCalls: [],
    totalTriangles: 0,
  };
  window.__SCV_PERF_PROBE__ = perfProbe;

  if (typeof PerformanceObserver === 'function') {
    try {
      const obs = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          perfProbe.longTasks.push({
            startTime: Number(entry.startTime.toFixed(1)),
            duration: Number(entry.duration.toFixed(1)),
            name: entry.name || 'self',
          });
        }
      });
      obs.observe({ type: 'longtask', buffered: true });
    } catch {
      // 忽略不支持 longtask 的环境
    }
  }

  function wrapProto(proto) {
    if (!proto || proto.__scvPerfWrapped) return;
    proto.__scvPerfWrapped = true;
    const origDrawElements = proto.drawElements;
    if (typeof origDrawElements === 'function') {
      proto.drawElements = function (mode, count, type, offset) {
        if (perfProbe.drawCaptureActive) {
          const tris = mode === 4 ? Math.floor(count / 3) : 0;
          perfProbe.totalTriangles += tris;
          perfProbe.drawCalls.push({ fn: 'drawElements', mode, count, instances: 1, tris });
        }
        return origDrawElements.call(this, mode, count, type, offset);
      };
    }
    const origDrawElementsInstanced = proto.drawElementsInstanced;
    if (typeof origDrawElementsInstanced === 'function') {
      proto.drawElementsInstanced = function (mode, count, type, offset, instanceCount) {
        if (perfProbe.drawCaptureActive) {
          const tris = mode === 4 ? Math.floor(count / 3) * (instanceCount || 0) : 0;
          perfProbe.totalTriangles += tris;
          perfProbe.drawCalls.push({
            fn: 'drawElementsInstanced',
            mode,
            count,
            instances: instanceCount || 0,
            tris,
          });
        }
        return origDrawElementsInstanced.call(this, mode, count, type, offset, instanceCount);
      };
    }
  }
  if (typeof WebGL2RenderingContext !== 'undefined') wrapProto(WebGL2RenderingContext.prototype);
  if (typeof WebGLRenderingContext !== 'undefined') wrapProto(WebGLRenderingContext.prototype);
})();
"""


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(len(s) * p)))
    return float(s[idx])


def _launch_args(angle_backend: str) -> list[str]:
    args = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        f"--use-angle={angle_backend}",
        "--enable-precise-memory-info",
    ]
    if angle_backend != "swiftshader":
        args.append("--disable-gpu-vsync")
    return args


def run_probe(
    *,
    channel: str,
    base_url: str,
    out_dir: Path,
    angle_backend: str,
    headed: bool,
    sim_peak_limit_ms: float,
    min_fps: float,
    check_phase3: bool = False,
    max_upgrade_longtask_ms: float = 60.0,
    max_peak_heap_mib: float = 380.0,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "channel": channel,
        "angle_backend": angle_backend,
        "headed": headed,
        "base_url": base_url,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": [],
        "errors": [],
        "metrics": {},
    }

    def record(name: str, passed: bool, details=None) -> None:
        report["checks"].append({"name": name, "passed": bool(passed), "details": details})
        print(json.dumps({"check": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed, args=_launch_args(angle_backend))
        report["browser_version"] = browser.version
        try:
            # =========================================================================
            # Run A: 普通玩家单次冷启动与后台全精度升级基线（绝不外部重复调用 waitForFullPrecision）
            # =========================================================================
            ctx_a = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            ctx_a.add_init_script(INIT_PROBE_JS)
            page = ctx_a.new_page()
            page.on("pageerror", lambda err: report["errors"].append(f"pageerror: {err}"))
            page.on(
                "console",
                lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}")
                if msg.type == "error"
                else None,
            )
            page.on(
                "response",
                lambda resp: report["errors"].append(f"http.{resp.status}: {resp.url}")
                if resp.status >= 400
                else None,
            )

            t_nav = time.perf_counter()
            page.goto(f"{base_url}/play?aspect=16:9", wait_until="domcontentloaded", timeout=60000)

            core_ready_ms = None
            full_ready_ms = None
            peak_heap_mib = 0.0
            peak_heap_at_ms = 0.0
            heap_samples: list[dict] = []

            deadline = time.perf_counter() + 90.0
            while time.perf_counter() < deadline:
                snap = page.evaluate(
                    """() => {
                        const m = window.__SCV_MAP__;
                        const heap = performance.memory ? performance.memory.usedJSHeapSize / (1024 * 1024) : 0;
                        return {
                            nowMs: performance.now(),
                            core: Boolean(m && m.isCoreReady && m.isCoreReady()),
                            full: Boolean(m && m.isFullPrecisionReady && m.isFullPrecisionReady()),
                            heapMiB: Number(heap.toFixed(2)),
                        };
                    }"""
                )
                if snap["heapMiB"] > peak_heap_mib:
                    peak_heap_mib = snap["heapMiB"]
                    peak_heap_at_ms = snap["nowMs"]
                heap_samples.append({"tMs": round(snap["nowMs"], 1), "heapMiB": snap["heapMiB"]})
                if core_ready_ms is None and snap["core"]:
                    core_ready_ms = snap["nowMs"]
                if snap["full"]:
                    full_ready_ms = snap["nowMs"]
                    break
                page.wait_for_timeout(80)

            if full_ready_ms is None:
                raise RuntimeError("等待普通玩家自然完成 fullPrecisionReady 超时 (>90s)")

            page.wait_for_timeout(600)

            boot_data = page.evaluate(
                r"""({ coreReadyMs, fullReadyMs }) => {
                    const probe = window.__SCV_PERF_PROBE__;
                    const resources = performance.getEntriesByType('resource');
                    let transferBytes = 0;
                    for (const r of resources) {
                        transferBytes += (r.transferSize || r.encodedBodySize || 0);
                    }
                    const bootMetrics = window.__SCV_MAP__.getBootMetrics();
                    const windowStartMs = (bootMetrics?.perfDiag?.bootEndMs > 0)
                        ? bootMetrics.perfDiag.bootEndMs
                        : coreReadyMs;
                    const coldTasks = probe.longTasks.filter(t => t.startTime < windowStartMs - 2);
                    const postCoreTasks = probe.longTasks.filter(
                        t => t.startTime >= windowStartMs - 2 && t.startTime <= fullReadyMs + 500
                    );
                    const maxCold = coldTasks.reduce((a, b) => Math.max(a, b.duration), 0);
                    const maxPostCore = postCoreTasks.reduce((a, b) => Math.max(a, b.duration), 0);
                    const slowResources = resources
                        .map(r => ({
                            name: String(r.name || '').replace(/^https?:\/\/[^/]+/, ''),
                            startMs: Number((r.startTime || 0).toFixed(1)),
                            durMs: Number((r.duration || 0).toFixed(1)),
                            sizeKiB: Number(((r.transferSize || r.encodedBodySize || 0) / 1024).toFixed(1)),
                        }))
                        .sort((a, b) => b.durMs - a.durMs)
                        .slice(0, 10);
                    const steadyHeap = performance.memory ? performance.memory.usedJSHeapSize / (1024 * 1024) : 0;
                    const glCanvas = document.querySelector('#canvas-host > canvas');
                    let glRenderer = 'unknown';
                    try {
                        const gl = glCanvas?.getContext('webgl2') || glCanvas?.getContext('webgl');
                        const ext = gl?.getExtension('WEBGL_debug_renderer_info');
                        if (gl && ext) glRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
                        else if (gl) glRenderer = gl.getParameter(gl.RENDERER);
                    } catch {}

                    // 捕获单帧 WebGL 绘制调用核验道面网格提交次数
                    probe.drawCalls = [];
                    probe.totalTriangles = 0;
                    probe.drawCaptureActive = true;
                    window.__SCV_MAP__.stepSimAndRender(0.01667, performance.now() * 0.001, true);
                    probe.drawCaptureActive = false;

                    const roadDraws = probe.drawCalls.filter(d => d.count === 491229);
                    return {
                        glRenderer,
                        bootMetrics,
                        windowStartMs: Number(windowStartMs.toFixed(1)),
                        resourceCount: resources.length,
                        transferMiB: Number((transferBytes / (1024 * 1024)).toFixed(2)),
                        steadyHeapMiB: Number(steadyHeap.toFixed(2)),
                        coldLongTaskCount: coldTasks.length,
                        coldMaxLongTaskMs: Number(maxCold.toFixed(1)),
                        postCoreLongTaskCount: postCoreTasks.length,
                        postCoreMaxLongTaskMs: Number(maxPostCore.toFixed(1)),
                        postCoreTopTasks: postCoreTasks.slice().sort((a, b) => b.duration - a.duration).slice(0, 8),
                        slowResources,
                        singleFrameDrawCalls: probe.drawCalls.length,
                        singleFrameTriangles: probe.totalTriangles,
                        roadMeshDrawCount: roadDraws.length,
                    };
                }""",
                {"coreReadyMs": core_ready_ms or 0.0, "fullReadyMs": full_ready_ms},
            )

            report["metrics"]["clean_single_pass_boot"] = {
                "wallElapsedSec": round(time.perf_counter() - t_nav, 2),
                "coreReadyMs": round(core_ready_ms or 0.0, 1),
                "fullPrecisionReadyMs": round(full_ready_ms, 1),
                "upgradeWindowMs": round(full_ready_ms - (core_ready_ms or 0.0), 1),
                "peakJsHeapMiB": round(peak_heap_mib, 2),
                "peakHeapAtMs": round(peak_heap_at_ms, 1),
                **boot_data,
                "phase3_gate_triggered": (
                    boot_data["postCoreMaxLongTaskMs"] > 60.0 or peak_heap_mib > 380.0
                ),
            }
            record(
                "道面网格(491229索引)仅提交1次(无重复叠画)",
                boot_data["roadMeshDrawCount"] == 1,
                {"roadMeshDrawCount": boot_data["roadMeshDrawCount"], "glRenderer": boot_data["glRenderer"]},
            )
            if check_phase3:
                record(
                    f"后台全精度升级单次最长LongTask<={max_upgrade_longtask_ms}ms",
                    boot_data["postCoreMaxLongTaskMs"] <= max_upgrade_longtask_ms,
                    {
                        "postCoreMaxLongTaskMs": boot_data["postCoreMaxLongTaskMs"],
                        "postCoreLongTaskCount": boot_data["postCoreLongTaskCount"],
                        "scvhUsedWorker": bool(boot_data.get("bootMetrics", {}).get("perfDiag", {}).get("scvhUsedWorker")),
                        "topTasks": boot_data["postCoreTopTasks"][:5],
                    },
                )
                record(
                    f"/play全链路JS堆峰值<={max_peak_heap_mib}MiB",
                    peak_heap_mib <= max_peak_heap_mib,
                    {
                        "peakJsHeapMiB": round(peak_heap_mib, 2),
                        "steadyHeapMiB": boot_data["steadyHeapMiB"],
                    },
                )

            # =========================================================================
            # Run B: 热路径强制回流 / CSS 查询 / 静止名牌 DOM 写入归零核验
            # =========================================================================
            hotpath = page.evaluate(
                """async () => {
                    const host = document.getElementById('canvas-host');
                    const origCW = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
                    const origCH = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight');
                    const origGCS = window.getComputedStyle;
                    const origText = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');

                    let hostClientReads = 0;
                    let gcsCalls = 0;
                    let labelTextWrites = 0;

                    Object.defineProperty(Element.prototype, 'clientWidth', {
                        configurable: true,
                        get() {
                            if (this === host) hostClientReads += 1;
                            return origCW.get.call(this);
                        },
                    });
                    Object.defineProperty(Element.prototype, 'clientHeight', {
                        configurable: true,
                        get() {
                            if (this === host) hostClientReads += 1;
                            return origCH.get.call(this);
                        },
                    });
                    window.getComputedStyle = function (...args) {
                        gcsCalls += 1;
                        return origGCS.apply(this, args);
                    };
                    Object.defineProperty(Node.prototype, 'textContent', {
                        configurable: true,
                        get() { return origText.get.call(this); },
                        set(v) {
                            const el = this.nodeType === 1 ? this : this.parentElement;
                            if (el && el.closest && el.closest('.scv-label')) {
                                labelTextWrites += 1;
                            }
                            return origText.set.call(this, v);
                        },
                    });

                    try {
                        // 先稳态跑 3 帧让初次缓存稳定，再归零计数
                        for (let i = 0; i < 3; i++) {
                            await new Promise(r => requestAnimationFrame(r));
                        }
                        hostClientReads = 0;
                        gcsCalls = 0;
                        labelTextWrites = 0;

                        // 阶段 1：60 帧静止稳态（手动隔离 UI 140ms 定时刷新以外的纯主循环帧）
                        window.__SCV_MAP__.setManualLoop(true);
                        const baseSec = performance.now() * 0.001;
                        for (let f = 0; f < 60; f++) {
                            window.__SCV_MAP__.stepSimAndRender(0.01667, baseSec + f * 0.01667, true);
                        }
                        const steady = {
                            hostClientReads,
                            gcsCalls,
                            labelTextWrites,
                        };

                        // 阶段 2：30 帧连续平移 + 镜头步进
                        hostClientReads = 0;
                        gcsCalls = 0;
                        const camCtrl = window.__SCV_INTERACTION__?.cameraCtrl;
                        for (let f = 0; f < 30; f++) {
                            camCtrl?.panByPixels?.(8, 5);
                            camCtrl?.step?.(16.67, performance.now(), new Set());
                            window.__SCV_INTERACTION__?.renderOverlay?.();
                        }
                        camCtrl?.endPointerDrag?.();
                        const panning = {
                            hostClientReads,
                            gcsCalls,
                        };
                        window.__SCV_MAP__.setManualLoop(false);
                        return { steady, panning };
                    } finally {
                        Object.defineProperty(Element.prototype, 'clientWidth', origCW);
                        Object.defineProperty(Element.prototype, 'clientHeight', origCH);
                        window.getComputedStyle = origGCS;
                        Object.defineProperty(Node.prototype, 'textContent', origText);
                    }
                }"""
            )
            report["metrics"]["hotpath_probe"] = hotpath
            ok_hotpath = (
                hotpath["steady"]["hostClientReads"] == 0
                and hotpath["steady"]["gcsCalls"] == 0
                and hotpath["steady"]["labelTextWrites"] == 0
                and hotpath["panning"]["hostClientReads"] == 0
                and hotpath["panning"]["gcsCalls"] == 0
            )
            record("热路径强制回流/getComputedStyle/静止名牌写DOM全归零", ok_hotpath, hotpath)

            # =========================================================================
            # Run C1: 正式 50 车满编（MAX_HERO_UNITS = 50）10Hz 纯模拟与实时 rAF 性能
            # =========================================================================
            deploy_50 = page.evaluate(
                """async () => {
                    await window.__SCV_MAP__.resetDefaultRoster();
                    await window.__SCV_MAP__.setCameraView(0, 85, 220, 0, { immediate: true });
                    // 填满 5 大配额合计 50 车（默认已有 1 重甲 + 1 旋翼 + 1 固定翼）
                    const plan = [
                        ['US_M1A2', 11],
                        ['US_LAV25', 16],
                        ['US_M109A7', 8],
                        ['US_AH64D', 7],
                        ['US_A10', 5],
                    ];
                    let idx = 0;
                    for (const [slug, count] of plan) {
                        for (let k = 0; k < count; k++) {
                            const col = idx % 10;
                            const row = Math.floor(idx / 10);
                            const wx = -90 + col * 20;
                            const wz = 20 + row * 22;
                            await window.__SCV_MAP__.deployVehicle(slug, { x: wx, z: wz }, { select: false, notify: false });
                            idx += 1;
                        }
                    }
                    // 预热 8 帧（8 * 16.67ms = 133ms >= 100ms），确保至少完成 1 次真实 10Hz 迷雾首次局部块填充
                    window.__SCV_MAP__.setManualLoop(true);
                    const tBase = performance.now() * 0.001 + 10;
                    const warmupSteps = [];
                    for (let w = 0; w < 8; w++) {
                        const tw0 = performance.now();
                        window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + w * 0.01667, false);
                        warmupSteps.push(Number((performance.now() - tw0).toFixed(3)));
                    }

                    // 连续 24 步纯模拟（覆盖 4 次 10Hz 迷雾节拍）
                    const simSteps = [];
                    for (let i = 0; i < 24; i++) {
                        const t0 = performance.now();
                        window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + 0.2 + i * 0.01667, false);
                        simSteps.push(Number((performance.now() - t0).toFixed(3)));
                    }

                    // 单帧真实 WebGL 绘制统计
                    const probe = window.__SCV_PERF_PROBE__;
                    probe.drawCalls = [];
                    probe.totalTriangles = 0;
                    probe.drawCaptureActive = true;
                    window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + 0.7, true);
                    probe.drawCaptureActive = false;
                    const webglDrawCalls = probe.drawCalls.length;
                    const webglTriangles = probe.totalTriangles;

                    // 实时 120 帧 rAF 采样
                    window.__SCV_MAP__.setManualLoop(false);
                    const rafDeltas = [];
                    await new Promise((resolve) => {
                        let last = performance.now();
                        let count = 0;
                        function tick(now) {
                            if (count > 5) rafDeltas.push(now - last);
                            last = now;
                            count += 1;
                            if (rafDeltas.length >= 120) resolve();
                            else requestAnimationFrame(tick);
                        }
                        requestAnimationFrame(tick);
                    });

                    const hud = window.__SCV_MAP__.getUiHudState();
                    const mem = window.__SCV_MAP__.getTransitionAndMemoryMetrics();
                    const fog = window.__SCV_MAP__.getFogStats();
                    return {
                        heroCount: hud.heroCount,
                        emitterCount: fog.emitterCount,
                        warmupMaxMs: Number(Math.max(...warmupSteps).toFixed(3)),
                        simSteps,
                        simMaxMs: Number(Math.max(...simSteps).toFixed(3)),
                        simAvgMs: Number((simSteps.reduce((a, b) => a + b, 0) / simSteps.length).toFixed(3)),
                        rafDeltas,
                        webglDrawCalls,
                        webglTriangles,
                        jsHeapMiB: mem.jsHeapMiB,
                    };
                }"""
            )
            raf50 = deploy_50.pop("rafDeltas")
            avg50 = sum(raf50) / max(1, len(raf50))
            fps50 = 1000.0 / avg50 if avg50 > 0 else 0.0
            deploy_50["avgFrameMs"] = round(avg50, 2)
            deploy_50["p95FrameMs"] = round(_percentile(raf50, 0.95), 2)
            deploy_50["maxFrameMs"] = round(max(raf50) if raf50 else 0.0, 2)
            deploy_50["avgFps"] = round(fps50, 1)
            report["metrics"]["hero_50_full_roster"] = deploy_50
            record(
                f"正式50车满编10Hz模拟峰值<={sim_peak_limit_ms}ms且帧率>={min_fps}FPS",
                deploy_50["heroCount"] == 50
                and deploy_50["simMaxMs"] <= sim_peak_limit_ms
                and deploy_50["avgFps"] >= min_fps,
                {
                    "heroCount": deploy_50["heroCount"],
                    "simMaxMs": deploy_50["simMaxMs"],
                    "avgFps": deploy_50["avgFps"],
                    "p95FrameMs": deploy_50["p95FrameMs"],
                },
            )

            # =========================================================================
            # Run C2: 修参 100 混编（50 己方）10Hz 纯模拟与实时 rAF 性能
            # =========================================================================
            mixed_100 = page.evaluate(
                """async () => {
                    window.__SCV_MAP__.setLoadMode('100_mixed_combat');
                    await window.__SCV_MAP__.setCameraView(0, 85, 220, 0, { immediate: true });
                    window.__SCV_MAP__.setManualLoop(true);
                    const tBase = performance.now() * 0.001 + 50;
                    const warmupSteps = [];
                    for (let w = 0; w < 8; w++) {
                        const tw0 = performance.now();
                        window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + w * 0.01667, false);
                        warmupSteps.push(Number((performance.now() - tw0).toFixed(3)));
                    }

                    const simSteps = [];
                    for (let i = 0; i < 24; i++) {
                        const t0 = performance.now();
                        window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + 0.2 + i * 0.01667, false);
                        simSteps.push(Number((performance.now() - t0).toFixed(3)));
                    }

                    const probe = window.__SCV_PERF_PROBE__;
                    probe.drawCalls = [];
                    probe.totalTriangles = 0;
                    probe.drawCaptureActive = true;
                    window.__SCV_MAP__.stepSimAndRender(0.01667, tBase + 0.7, true);
                    probe.drawCaptureActive = false;
                    const webglDrawCalls = probe.drawCalls.length;
                    const webglTriangles = probe.totalTriangles;

                    window.__SCV_MAP__.setManualLoop(false);
                    const rafDeltas = [];
                    await new Promise((resolve) => {
                        let last = performance.now();
                        let count = 0;
                        function tick(now) {
                            if (count > 5) rafDeltas.push(now - last);
                            last = now;
                            count += 1;
                            if (rafDeltas.length >= 120) resolve();
                            else requestAnimationFrame(tick);
                        }
                        requestAnimationFrame(tick);
                    });

                    const mem = window.__SCV_MAP__.getTransitionAndMemoryMetrics();
                    const fog = window.__SCV_MAP__.getFogStats();
                    return {
                        squadCount: window.__SCV_MAP__.getUiHudState().squadCount,
                        emitterCount: fog.emitterCount,
                        warmupMaxMs: Number(Math.max(...warmupSteps).toFixed(3)),
                        simSteps,
                        simMaxMs: Number(Math.max(...simSteps).toFixed(3)),
                        simAvgMs: Number((simSteps.reduce((a, b) => a + b, 0) / simSteps.length).toFixed(3)),
                        rafDeltas,
                        webglDrawCalls,
                        webglTriangles,
                        jsHeapMiB: mem.jsHeapMiB,
                    };
                }"""
            )
            raf100 = mixed_100.pop("rafDeltas")
            avg100 = sum(raf100) / max(1, len(raf100))
            fps100 = 1000.0 / avg100 if avg100 > 0 else 0.0
            mixed_100["avgFrameMs"] = round(avg100, 2)
            mixed_100["p95FrameMs"] = round(_percentile(raf100, 0.95), 2)
            mixed_100["maxFrameMs"] = round(max(raf100) if raf100 else 0.0, 2)
            mixed_100["avgFps"] = round(fps100, 1)
            report["metrics"]["mixed_100_combat"] = mixed_100
            record(
                f"100混编(50己方过滤)10Hz模拟峰值<={sim_peak_limit_ms}ms且帧率>={min_fps}FPS",
                mixed_100["squadCount"] == 100
                and mixed_100["emitterCount"] == 50
                and mixed_100["simMaxMs"] <= sim_peak_limit_ms
                and mixed_100["avgFps"] >= min_fps,
                {
                    "squadCount": mixed_100["squadCount"],
                    "emitterCount": mixed_100["emitterCount"],
                    "simMaxMs": mixed_100["simMaxMs"],
                    "avgFps": mixed_100["avgFps"],
                    "p95FrameMs": mixed_100["p95FrameMs"],
                },
            )

            # =========================================================================
            # Run C3: 460m 中景城镇 & 快速缩放观察项（供 Phase 2 参考）
            # =========================================================================
            obs_mid_zoom = page.evaluate(
                """async () => {
                    window.__SCV_MAP__.setLoadMode('g01_vehicles');
                    await window.__SCV_MAP__.resetDefaultRoster();
                    await window.__SCV_MAP__.setCameraView(-362.5, -462.5, 460, 0.2, { immediate: true });
                    await new Promise(r => setTimeout(r, 200));
                    const probe = window.__SCV_PERF_PROBE__;
                    probe.drawCalls = [];
                    probe.totalTriangles = 0;
                    probe.drawCaptureActive = true;
                    window.__SCV_MAP__.stepSimAndRender(0.01667, performance.now() * 0.001, true);
                    probe.drawCaptureActive = false;
                    const mid460DrawCalls = probe.drawCalls.length;
                    const mid460Triangles = probe.totalTriangles;

                    // 快速缩放观察：从 9000m 切回 135m
                    await window.__SCV_MAP__.setCameraView(75, 350, 9000, 0, { immediate: true });
                    await new Promise(r => setTimeout(r, 150));
                    const zoomDeltas = [];
                    const stopZoom = { flag: false };
                    const trackPromise = new Promise((resolve) => {
                        let last = performance.now();
                        function tick(now) {
                            zoomDeltas.push(now - last);
                            last = now;
                            if (stopZoom.flag) resolve();
                            else requestAnimationFrame(tick);
                        }
                        requestAnimationFrame(tick);
                    });
                    for (const [x, z, sp] of [[-362.5, -462.5, 900], [-362.5, -462.5, 460], [-362.5, -462.5, 135]]) {
                        await window.__SCV_MAP__.setCameraView(x, z, sp, 0, { immediate: true });
                        await new Promise(r => setTimeout(r, 180));
                    }
                    stopZoom.flag = true;
                    await trackPromise;
                    return {
                        mid460DrawCalls,
                        mid460Triangles,
                        rapidZoomMaxFrameMs: Number(Math.max(...zoomDeltas, 0).toFixed(1)),
                    };
                }"""
            )
            report["metrics"]["phase2_observations"] = obs_mid_zoom

            # =========================================================================
            # Run D: /editor 放楼、删楼与地形抬升笔刷的迷雾遮挡实时局部失效闭环
            # =========================================================================
            editor_fog = page.evaluate(
                """async () => {
                    await window.__SCV_MAP__.resetDefaultRoster();
                    window.__SCV_MAP__.setFogEnabled(true);
                    await window.__SCV_MAP__.setCameraView(0, 85, 220, 0, { immediate: true });
                    // 将空中两机移到远处，确保仅由地面 M1A2 照亮 (0..250, 85)
                    window.__SCV_MAP__.resetHeroVehiclePos('US_M1A2_01', 0, 85, 0);
                    window.__SCV_MAP__.resetHeroVehiclePos('US_AH64D_01', -2800, -2800, 24);
                    window.__SCV_MAP__.resetHeroVehiclePos('US_A10C_01', -2800, -2800, 200);
                    window.__SCV_MAP__.setManualLoop(true);
                    window.__SCV_MAP__.stepSimAndRender(0.01667, performance.now() * 0.001 + 200, false);

                    const before = window.__SCV_MAP__.queryFogAt(190, 85);
                    const b3 = window.__SCV_MAP__.getB3Manifest();
                    const wideWall = b3.building_prototypes.find(p => {
                        const dx = (p.bounds_max[0] - p.bounds_min[0]);
                        const dy = (p.bounds_max[1] - p.bounds_min[1]);
                        const dz = (p.bounds_max[2] - p.bounds_min[2]);
                        return dy >= 10 && Math.max(dx, dz) >= 24;
                    }) || b3.building_prototypes[0];

                    // 在 M1A2 正前方 x=95, z=85 沿 Z 轴放一排建筑形成阻挡墙
                    await window.__SCV_MAP__.addObstacleBuilding({
                        id: 'perf_fog_wall_0',
                        prototype_id: wideWall.id,
                        x: 95,
                        z: 85,
                        yaw_rad: 0,
                        scale: [1.2, 1.2, 2.5],
                    });
                    const afterAdd = window.__SCV_MAP__.queryFogAt(190, 85);

                    await window.__SCV_MAP__.clearObstacleBuildings();
                    const afterClear = window.__SCV_MAP__.queryFogAt(190, 85);

                    const verBeforeBrush = window.__SCV_MAP__.getFogStats().gridVersion;
                    window.__SCV_MAP__.applyTerrainBrush({ x: 80, z: 85, radius: 22, deltaH: 6.0, mode: 'raise' });
                    const verAfterBrush = window.__SCV_MAP__.getFogStats().gridVersion;
                    window.__SCV_MAP__.setManualLoop(false);

                    return {
                        beforeVisible: Boolean(before?.visible),
                        afterAddVisible: Boolean(afterAdd?.visible),
                        afterClearVisible: Boolean(afterClear?.visible),
                        brushRefreshedFog: verAfterBrush > verBeforeBrush,
                        protoUsed: wideWall.id,
                    };
                }"""
            )
            report["metrics"]["editor_fog_invalidation"] = editor_fog
            ok_editor_fog = (
                editor_fog["beforeVisible"] is True
                and editor_fog["afterAddVisible"] is False
                and editor_fog["afterClearVisible"] is True
                and editor_fog["brushRefreshedFog"] is True
            )
            record("编辑器放楼/删楼/地形笔刷后迷雾遮挡实时局部失效闭环", ok_editor_fog, editor_fog)
            ctx_a.close()

            # =========================================================================
            # Run E: 并发重入锁实测（coreReady 后并发调用两次 waitForFullPrecision 返回同一 Promise 且道面仅 1 次）
            # =========================================================================
            ctx_e = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            ctx_e.add_init_script(INIT_PROBE_JS)
            page_e = ctx_e.new_page()
            page_e.on("pageerror", lambda err: report["errors"].append(f"pageerror(E): {err}"))
            page_e.goto(f"{base_url}/play?aspect=16:9", wait_until="domcontentloaded", timeout=60000)
            page_e.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
            reentry = page_e.evaluate(
                """async () => {
                    const p1 = window.__SCV_MAP__.waitForFullPrecision();
                    const p2 = window.__SCV_MAP__.waitForFullPrecision();
                    const samePromise = (p1 === p2);
                    await Promise.all([p1, p2]);
                    const probe = window.__SCV_PERF_PROBE__;
                    probe.drawCalls = [];
                    probe.totalTriangles = 0;
                    probe.drawCaptureActive = true;
                    window.__SCV_MAP__.stepSimAndRender(0.01667, performance.now() * 0.001, true);
                    probe.drawCaptureActive = false;
                    const roadDraws = probe.drawCalls.filter(d => d.count === 491229).length;
                    return { samePromise, roadDraws, ready: window.__SCV_MAP__.isFullPrecisionReady() };
                }"""
            )
            report["metrics"]["reentrancy_lock"] = reentry
            record(
                "并发调用waitForFullPrecision返回同一Promise且道面仅提交1次",
                reentry["samePromise"] is True and reentry["roadDraws"] == 1 and reentry["ready"] is True,
                reentry,
            )
            ctx_e.close()

            record("全流程零未捕获异常与控制台错误", len(report["errors"]) == 0, report["errors"])
        finally:
            browser.close()

    report["passed"] = all(c["passed"] for c in report["checks"])
    out_file = out_dir / "性能基线报告.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT_PATH={out_file}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Scavenger 网页端硬件 WebGL 性能基线与 Phase 1 门禁采集脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:8800", help="8800 正式主服务地址")
    parser.add_argument("--channel", default="msedge", choices=("msedge", "chrome"), help="浏览器通道")
    parser.add_argument("--angle", default="d3d11", choices=("d3d11", "gl", "swiftshader"), help="ANGLE 后端")
    parser.add_argument("--out-base", default=str(DEFAULT_OUT_BASE), help="输出根目录（建议 tmp/perf-opt-phase<N>-<会话短ID>）")
    parser.add_argument("--run", required=True, help="本次采样独立子目录名")
    parser.add_argument("--headed", action="store_true", help="使用可见窗口运行")
    parser.add_argument("--sim-peak-limit", type=float, default=2.0, help="10Hz 纯模拟峰值门限 (ms)")
    parser.add_argument("--min-fps", type=float, default=60.0, help="50 车满编与 100 混编最低平均帧率门限")
    parser.add_argument("--check-phase3", action="store_true", help="启用 Phase 3 (SCAV-15) 后台升级长任务与堆峰值门禁校验")
    parser.add_argument("--max-upgrade-longtask", type=float, default=60.0, help="coreReady 后后台升级最长 LongTask 门限 (ms)")
    parser.add_argument("--max-peak-heap", type=float, default=380.0, help="/play 全链路 JS 堆峰值门限 (MiB)")
    args = parser.parse_args()

    out_dir = Path(args.out_base) / args.run
    report = run_probe(
        channel=args.channel,
        base_url=args.base_url.rstrip("/"),
        out_dir=out_dir,
        angle_backend=args.angle,
        headed=args.headed,
        sim_peak_limit_ms=args.sim_peak_limit,
        min_fps=args.min_fps,
        check_phase3=args.check_phase3,
        max_upgrade_longtask_ms=args.max_upgrade_longtask,
        max_peak_heap_mib=args.max_peak_heap,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
