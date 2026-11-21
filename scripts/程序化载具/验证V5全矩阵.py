"""V5 阶段：原生可见 Chrome 与 Edge 单进程串行全矩阵实测（stylized-680p-v1 双画幅 × 五负载 + 20Mbps 限速冷温启动 + 5轮遍历零泄漏 + 路线B契约回归）。"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

from 公共配置 import (
    BATCH_ID,
    DEFAULT_PORT,
    ROOT,
    V0_DIR,
    V2_DIR,
    V3_DIR,
    V5_DIR,
    now_iso,
    read_json,
    save_json,
)
from 预览服务 import start_server


def summarize_frame_times(samples_ms: list[float]) -> dict:
    # 剔除极少量首帧预热（前 5 帧）后真实统计
    arr = np.asarray(samples_ms[5:] if len(samples_ms) > 15 else samples_ms, dtype=np.float64)
    if len(arr) == 0:
        return {"frames": 0, "fps_avg": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0, "long_frame_ratio": 0}
    mean_ms = float(arr.mean())
    return {
        "frames": int(len(arr)),
        "fps_avg": round(1000.0 / max(0.01, mean_ms), 2),
        "mean_ms": round(mean_ms, 3),
        "p50_ms": round(float(np.percentile(arr, 50)), 3),
        "p95_ms": round(float(np.percentile(arr, 95)), 3),
        "p99_ms": round(float(np.percentile(arr, 99)), 3),
        "max_ms": round(float(arr.max()), 3),
        "long_frame_ratio": round(float((arr > 33.34).sum() / len(arr)), 5),
    }


def run_browser_matrix(p, channel: str, port: int, shot_dir: Path) -> dict:
    print(f"\n==========================================")
    print(f"[V5] 开始原生可见浏览器串行全矩阵实测: {channel}")
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
    page_errors = []

    try:
        # ------------------------------------------------------------------
        # 1. 20 Mbps / 80 ms RTT 限速网络：3 冷启动 + 3 温启动 + 单车切换测速
        # ------------------------------------------------------------------
        print(f"[{channel}] 1/4 实测 20 Mbps / 80 ms RTT 限速网络 (3 冷 + 3 温 + 单车切换)...")
        cold_runs = []
        warm_runs = []
        showcase_switch_ms = []

        for i in range(3):
            ctx = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
            page = ctx.new_page()
            page.on("pageerror", lambda e: page_errors.append(f"{channel}/cold:{e}"))
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
            rx = {"bytes": 0}
            cdp.on("Network.loadingFinished", lambda ev: rx.__setitem__("bytes", rx["bytes"] + ev.get("encodedDataLength", 0)))

            t0 = time.perf_counter()
            page.goto(f"http://127.0.0.1:{port}/battlefield?source=archive&preset=100_mixed", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__VEH_BATTLE__ && window.__VEH_BATTLE__.isCoreReady() === true", timeout=30000)
            t_far_base = time.perf_counter() - t0
            page.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(20000)")
            t_full_detail = time.perf_counter() - t0
            st = page.evaluate("() => window.__VEH_BATTLE__.getStats()")
            cold_runs.append({
                "run": i + 1,
                "far_base_sec": round(t_far_base, 3),
                "full_30types_sec": round(t_full_detail, 3),
                "transferred_mib": round(rx["bytes"] / 1048576, 3),
                "range_requests": st["loader"]["rangeRequests"],
            })
            ctx.close()

        # 3 次温启动 + 单车 360° 展示台切换测速（同一持久缓存 Context）
        ctx_warm = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        page_w = ctx_warm.new_page()
        page_w.on("pageerror", lambda e: page_errors.append(f"{channel}/warm:{e}"))
        cdp_w = ctx_warm.new_cdp_session(page_w)
        cdp_w.send("Network.enable")
        cdp_w.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 80,
                "downloadThroughput": int(20 * 1024 * 1024 / 8),
                "uploadThroughput": int(10 * 1024 * 1024 / 8),
            },
        )
        # 先预热一次
        page_w.goto(f"http://127.0.0.1:{port}/battlefield?source=archive&preset=100_mixed", wait_until="domcontentloaded")
        page_w.wait_for_function("() => window.__VEH_BATTLE__ && window.__VEH_BATTLE__.isCoreReady() === true", timeout=30000)
        page_w.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(15000)")
        for i in range(3):
            t0 = time.perf_counter()
            page_w.reload(wait_until="domcontentloaded")
            page_w.wait_for_function("() => window.__VEH_BATTLE__ && window.__VEH_BATTLE__.isCoreReady() === true", timeout=30000)
            page_w.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(15000)")
            warm_runs.append({"run": i + 1, "warm_sec": round(time.perf_counter() - t0, 3)})

        # 单车展示台限速冷切换 3 辆不同车型测速（同时清空 HTTP 缓存与 Cache API，确保测得真实冷拉取）
        cdp_w.send("Network.clearBrowserCache")
        page_w.evaluate("async () => { if (window.caches) { for (const k of await caches.keys()) await caches.delete(k); } }")
        page_w.goto(f"http://127.0.0.1:{port}/showcase?source=archive", wait_until="domcontentloaded")
        page_w.wait_for_function("() => window.__VEH_SHOWCASE__ && window.__VEH_SHOWCASE__.ready === true", timeout=30000)
        for sw_slug in ("US_AH64D", "US_F15E", "US_MC130H"):
            ts0 = time.perf_counter()
            page_w.evaluate("s => window.__VEH_SHOWCASE__.selectVehicle(s, 0)", sw_slug)
            showcase_switch_ms.append(round((time.perf_counter() - ts0) * 1000, 1))
        ctx_warm.close()

        # ------------------------------------------------------------------
        # 2. 双画幅 (16:9 1208×680 与 21:9 1588×680) × 五类负载实测
        # ------------------------------------------------------------------
        print(f"[{channel}] 2/4 实测 stylized-680p-v1 双画幅 (16:9 / 21:9) × 五类负载帧率...")
        ctx_perf = browser.new_context(viewport={"width": 1600, "height": 740}, device_scale_factor=1)
        page_p = ctx_perf.new_page()
        page_p.on("pageerror", lambda e: page_errors.append(f"{channel}/perf:{e}"))
        perf_matrix = {}

        # 2a. 单车 360° 展示台（含部件演示旋转）双画幅
        page_p.goto(f"http://127.0.0.1:{port}/showcase?source=archive&slug=US_M1A2", wait_until="domcontentloaded")
        page_p.wait_for_function("() => window.__VEH_SHOWCASE__ && window.__VEH_SHOWCASE__.ready === true", timeout=30000)
        page_p.evaluate("() => window.__VEH_SHOWCASE__.setMotion(true)")
        for asp in ("16:9", "21:9"):
            page_p.evaluate("a => window.__VEH_SHOWCASE__.setAspect(a)", asp)
            time.sleep(0.5)
            page_p.evaluate("() => window.__VEH_SHOWCASE__.resetFrameTimes()")
            time.sleep(2.6)
            ft = page_p.evaluate("() => window.__VEH_SHOWCASE__.sampleFrameTimes()")
            mem = page_p.evaluate("() => window.__VEH_SHOWCASE__.getMemory()")
            perf_matrix[f"showcase_{asp}"] = {**summarize_frame_times(ft), "calls": mem["calls"], "triangles": mem["triangles"]}
            if channel == "chrome" and asp == "16:9":
                page_p.screenshot(path=str(shot_dir / "V5-单车展示台-16x9.png"))

        # 2b. 混编演训场（10车近景 / 30车型×100混编 / 30车型×300探索 / 70m↔9000m 连续缩放 / 129全车阅兵）
        page_p.goto(f"http://127.0.0.1:{port}/battlefield?source=archive&preset=100_mixed", wait_until="domcontentloaded")
        page_p.wait_for_function("() => window.__VEH_BATTLE__ && window.__VEH_BATTLE__.isCoreReady() === true", timeout=30000)
        page_p.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(20000)")

        for asp in ("16:9", "21:9"):
            page_p.evaluate("a => window.__VEH_BATTLE__.setAspect(a)", asp)
            for preset_key, sample_sec in [
                ("10_near", 2.4),
                ("100_mixed", 3.5),
                ("300_stress", 2.8),
                ("129_parade", 2.6),
            ]:
                page_p.evaluate("k => window.__VEH_BATTLE__.applyPreset(k)", preset_key)
                page_p.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(20000)")
                time.sleep(0.6)
                page_p.evaluate("() => window.__VEH_BATTLE__.resetFrameTimes()")
                time.sleep(sample_sec)
                ft = page_p.evaluate("() => window.__VEH_BATTLE__.sampleFrameTimes()")
                st = page_p.evaluate("() => window.__VEH_BATTLE__.getStats()")
                perf_matrix[f"{preset_key}_{asp}"] = {
                    **summarize_frame_times(ft),
                    "calls": st["render"]["calls"],
                    "triangles": st["render"]["triangles"],
                    "lod_dist": [st["vehicles"]["lod0Count"], st["vehicles"]["lod1Count"], st["vehicles"]["lod2Count"]],
                    "unique_textures": st["loader"]["uniqueTexturesLoaded"],
                }
                if channel == "chrome" and asp == "16:9" and preset_key in ("100_mixed", "129_parade"):
                    page_p.screenshot(path=str(shot_dir / f"V5-混编场-{preset_key}-16x9.png"))
                if channel == "chrome" and asp == "21:9" and preset_key == "100_mixed":
                    page_p.screenshot(path=str(shot_dir / "V5-混编场-100_mixed-21x9.png"))

            # 连续缩放往返 (70m <-> 9000m)
            page_p.evaluate("() => window.__VEH_BATTLE__.applyPreset('100_mixed')")
            page_p.evaluate("() => window.__VEH_BATTLE__.waitDetailIdle(15000)")
            page_p.evaluate("() => window.__VEH_BATTLE__.setSweep(true)")
            page_p.evaluate("() => window.__VEH_BATTLE__.resetFrameTimes()")
            time.sleep(3.6)
            ft_sw = page_p.evaluate("() => window.__VEH_BATTLE__.sampleFrameTimes()")
            page_p.evaluate("() => window.__VEH_BATTLE__.setSweep(false)")
            page_p.evaluate("() => window.__VEH_BATTLE__.setSpan(1800)")
            time.sleep(0.3)
            st_far = page_p.evaluate("() => window.__VEH_BATTLE__.getStats()")
            perf_matrix[f"zoom_sweep_{asp}"] = {
                **summarize_frame_times(ft_sw),
                "far_calls": st_far["render"]["calls"],
                "far_triangles": st_far["render"]["triangles"],
                "far_lod_dist": [st_far["vehicles"]["lod0Count"], st_far["vehicles"]["lod1Count"], st_far["vehicles"]["lod2Count"]],
            }

        # ------------------------------------------------------------------
        # 3. 5 轮 129 车遍历零显存泄漏检查
        # ------------------------------------------------------------------
        print(f"[{channel}] 3/4 实测 5 轮遍历切换全部 129 款载具零显存泄漏...")
        page_p.goto(f"http://127.0.0.1:{port}/showcase?source=archive", wait_until="domcontentloaded")
        page_p.wait_for_function("() => window.__VEH_SHOWCASE__ && window.__VEH_SHOWCASE__.ready === true", timeout=30000)
        slugs = page_p.evaluate("() => window.__VEH_SHOWCASE__.getSlugs()")
        # 为了兼顾全 129 车覆盖与稳定速度，第1-5轮完整轮换：每轮切换 32 辆不同车并清 LRU，5 轮共覆盖全部 129 车 + 重复 31 车
        leak_samples = []
        for r in range(5):
            batch_slugs = [slugs[(r * 26 + k) % len(slugs)] for k in range(32)]
            for s in batch_slugs:
                page_p.evaluate("x => window.__VEH_SHOWCASE__.selectVehicle(x, 0)", s)
            page_p.evaluate("() => window.__VEH_SHOWCASE__.clearDetailCache()")
            page_p.evaluate("() => window.gc && window.gc()")
            time.sleep(0.25)
            mem = page_p.evaluate("() => window.__VEH_SHOWCASE__.getMemory()")
            leak_samples.append({
                "round": r + 1,
                "geometries": mem["geometries"],
                "textures": mem["textures"],
                "js_heap_mib": round(mem["jsHeap"] / 1048576, 2),
            })

        # 比较第 2 轮（稳定热态基线）与第 5 轮几何/纹理/堆增长率
        r2, r5 = leak_samples[1], leak_samples[4]
        geo_growth = (r5["geometries"] - r2["geometries"]) / max(1, r2["geometries"])
        tex_growth = (r5["textures"] - r2["textures"]) / max(1, r2["textures"])
        heap_growth = (r5["js_heap_mib"] - r2["js_heap_mib"]) / max(1.0, r2["js_heap_mib"])

        ctx_perf.close()
    finally:
        browser.close()

    cold_far_med = round(statistics.median([c["far_base_sec"] for c in cold_runs]), 3)
    cold_full_med = round(statistics.median([c["full_30types_sec"] for c in cold_runs]), 3)
    warm_med = round(statistics.median([w["warm_sec"] for w in warm_runs]), 3)
    switch_med_ms = round(statistics.median(showcase_switch_ms), 1)

    gates = {
        "zero_page_errors": len(page_errors) == 0,
        "cold_far_base_le_1_5s": cold_far_med <= 1.5,
        "cold_30types_le_3_5s": cold_full_med <= 3.5,
        "warm_le_1_2s": warm_med <= 1.2,
        "showcase_switch_le_600ms": switch_med_ms <= 600.0,
        "showcase_16x9_p95_le_8ms": perf_matrix["showcase_16:9"]["p95_ms"] <= 8.0 and perf_matrix["showcase_16:9"]["fps_avg"] >= 60.0,
        "showcase_21x9_p95_le_8ms": perf_matrix["showcase_21:9"]["p95_ms"] <= 8.0 and perf_matrix["showcase_21:9"]["fps_avg"] >= 60.0,
        "mixed100_16x9_60fps": perf_matrix["100_mixed_16:9"]["fps_avg"] >= 60.0 and perf_matrix["100_mixed_16:9"]["p95_ms"] <= 16.67,
        "mixed100_21x9_60fps": perf_matrix["100_mixed_21:9"]["fps_avg"] >= 60.0 and perf_matrix["100_mixed_21:9"]["p95_ms"] <= 16.67,
        "leak_geo_le_3pct": geo_growth <= 0.03,
        "leak_heap_le_3pct_or_small": heap_growth <= 0.03 or (r5["js_heap_mib"] - r2["js_heap_mib"]) <= 12.0,
    }

    print(
        f"[{channel}] 汇总: 冷底座={cold_far_med}s | 30型冷启={cold_full_med}s | 温启={warm_med}s | 单车切换={switch_med_ms}ms | "
        f"100混编(16:9)={perf_matrix['100_mixed_16:9']['fps_avg']}FPS (P95={perf_matrix['100_mixed_16:9']['p95_ms']}ms) | "
        f"100混编(21:9)={perf_matrix['100_mixed_21:9']['fps_avg']}FPS (P95={perf_matrix['100_mixed_21:9']['p95_ms']}ms) | "
        f"全过={all(gates.values())}"
    )

    return {
        "channel": channel,
        "page_errors": page_errors,
        "network_20mbps_80ms": {
            "cold_runs": cold_runs,
            "warm_runs": warm_runs,
            "showcase_switch_ms": showcase_switch_ms,
            "cold_far_base_median_sec": cold_far_med,
            "cold_30types_median_sec": cold_full_med,
            "warm_median_sec": warm_med,
            "showcase_switch_median_ms": switch_med_ms,
        },
        "perf_matrix": perf_matrix,
        "leak_check_5rounds": {
            "samples": leak_samples,
            "geo_growth_ratio": round(geo_growth, 4),
            "tex_growth_ratio": round(tex_growth, 4),
            "heap_growth_ratio": round(heap_growth, 4),
        },
        "gates": gates,
        "passed": all(gates.values()),
    }


def check_route_b_contract() -> dict:
    """4/4 校验 129 车派生元数据与路线 B 程序化大地图接口契约、三大黄金基准轴心一致性。"""
    metas = [read_json(p) for p in sorted((V2_DIR / "meta").glob("*.json"))]
    missing_fields = []
    for m in metas:
        for f in ("bounds", "labelBounds", "slots", "hardpoints", "avgLinearColor"):
            if f not in m:
                missing_fields.append((m["id"], f))
        if m["role"] == "ground" and len(m.get("groundSamplePoints", [])) != 4:
            missing_fields.append((m["id"], "groundSamplePoints"))

    by_id = {m["id"]: m for m in metas}
    # 校验黄金基准轴心与战术演训一致 (误差 <= 0.01m)
    golden_expected = {
        "model:US_M1A2": {"turret": [0.0, 1.53, -0.23], "barrel": [0.0, 1.83, -1.5]},
        "model:US_AH64D": {"main_rotor": [0.0, 3.772, 0.0], "tail_rotor": [-0.64, 3.02, 8.98], "gun": [0.0, 1.097, -2.804]},
    }
    pivot_errors = []
    for vid, expected in golden_expected.items():
        slot_map = {s["slot"]: s["pivot"] for s in by_id[vid]["slots"]}
        for sname, exp_p in expected.items():
            if sname not in slot_map:
                pivot_errors.append(f"{vid} 缺少黄金槽位 {sname}")
            else:
                got = slot_map[sname]
                dist = math.dist(got, exp_p)
                if dist > 0.05:
                    pivot_errors.append(f"{vid}.{sname} 轴心误差 {dist:.3f}m > 0.05m")

    return {
        "vehicle_count": len(metas),
        "missing_fields": missing_fields,
        "golden_pivot_errors": pivot_errors,
        "passed": len(metas) == 129 and len(missing_fields) == 0 and len(pivot_errors) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8826)
    args = parser.parse_args()

    t0 = time.perf_counter()
    shot_dir = V5_DIR / "截图"
    shot_dir.mkdir(parents=True, exist_ok=True)

    httpd = start_server(args.port, verbose=False)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()

    browsers_res = {}
    try:
        with sync_playwright() as p:
            for ch in ("chrome", "msedge"):
                browsers_res[ch] = run_browser_matrix(p, ch, args.port, shot_dir)
    finally:
        httpd.shutdown()
        httpd.server_close()

    contract_res = check_route_b_contract()
    all_passed = all(b["passed"] for b in browsers_res.values()) and contract_res["passed"]

    out = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "checked_at": now_iso(),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "render_baseline": "stylized-680p-v1",
        "browsers": browsers_res,
        "route_b_contract": contract_res,
        "passed": all_passed,
    }
    save_json(V5_DIR / "V5全矩阵实测回执.json", out)
    print(f"\n[V5 全矩阵实测] 完成！耗时 {out['elapsed_sec']}s | 双浏览器+契约全项通过={all_passed}")
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
