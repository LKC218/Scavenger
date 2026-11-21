"""B4阶段验证：核验.scvmap空间归档单包(<=3个包、总大小<=90MiB、二进制索引<=128KB)、HTTP Range按需加载，以及从70m连续缩放到9000m再推回70m全过程0次回退闪屏、0次整屏集体跳变、0条射线空洞。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/程序化地图"))
from 预览服务 import create_server

WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B4_DIR = WORK_DIR / "B4-单包归档"
EVIDENCE_DIR = B4_DIR / "验证凭证"
SHOTS_DIR = EVIDENCE_DIR / "截图"
PORT = 8821


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run_b4_browser_verification(port: int) -> dict:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-frame-rate-limit", "--disable-gpu-vsync"],
        )
        errors = []
        page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # 默认以 ?source=archive (HTTP Range .scvmap 模式) 打开统一预览入口
        page.goto(f"http://127.0.0.1:{port}/?source=archive", timeout=60000)
        page.wait_for_function("window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
        page.evaluate("window.__SCV_MAP__.waitForFullPrecision()")
        page.wait_for_function("window.__SCV_MAP__.isFullPrecisionReady() === true", timeout=60000)
        page.bring_to_front()
        page.wait_for_timeout(400)

        boot_metrics = page.evaluate("window.__SCV_MAP__.getBootMetrics()")
        page.evaluate("window.__SCV_MAP__.resetFrames()")

        # 从 70m 连续缩放到 9000m 再连续推回 70m (共 41 步，覆盖 70/180/350/500/800/2400/9000m 全部环带与滞回区间)
        zoom_out_spans = [70, 85, 105, 130, 155, 180, 215, 255, 300, 350, 410, 460, 500, 570, 650, 730, 800, 1050, 1450, 2000, 3000, 4800, 7000, 9000]
        zoom_in_spans = list(reversed(zoom_out_spans[:-1]))
        all_spans = zoom_out_spans + zoom_in_spans

        key_shot_spans = {70, 180, 350, 500, 800, 2000, 9000}
        captured_spans = set()
        key_screenshots = []

        # 选定既有建筑又有森林与草地的复合交界区 (-362.5, -462.5) 执行连续缩放
        cx, cz = -362.5, -462.5
        for step_idx, sp in enumerate(all_spans):
            page.evaluate(
                "arg => window.__SCV_MAP__.setCameraView(arg.x, arg.z, arg.span, 0.2)",
                {"x": cx, "z": cz, "span": float(sp)},
            )
            page.wait_for_timeout(90)
            page.evaluate("window.__SCV_MAP__.checkVisibleGroundRays(3)")

            if sp in key_shot_spans and sp not in captured_spans:
                captured_spans.add(sp)
                shot_file = SHOTS_DIR / f"B4-连续缩放-{sp}m.png"
                page.screenshot(path=str(shot_file))
                key_screenshots.append({
                    "span_m": sp,
                    "screenshot": shot_file.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(shot_file),
                })

        # 在推回 70m 时再截留一张推回终点核验图
        shot_return = SHOTS_DIR / "B4-连续缩放推回终点-70m.png"
        page.screenshot(path=str(shot_return))
        key_screenshots.append({
            "span_m": 70,
            "phase": "return_to_70m",
            "screenshot": shot_return.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(shot_return),
        })

        metrics = page.evaluate("window.__SCV_MAP__.getTransitionAndMemoryMetrics()")
        browser.close()

    return {
        "browser": "chrome",
        "errors": errors,
        "boot_metrics": boot_metrics,
        "transition_and_memory_metrics": metrics,
        "key_screenshots": key_screenshots,
    }


def main() -> None:
    if EVIDENCE_DIR.exists():
        shutil.rmtree(EVIDENCE_DIR)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    b4_manifest = json.loads((B4_DIR / "单包归档清单.json").read_text("utf-8"))
    budget = b4_manifest["budget_summary"]

    server = create_server(port=PORT, verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        browser_res = run_b4_browser_verification(PORT)
    finally:
        server.shutdown()
        server.server_close()

    m = browser_res["transition_and_memory_metrics"]
    gate_passed = (
        budget["within_budget"]
        and len(browser_res["errors"]) == 0
        and m["overviewFallbacks"] == 0
        and m["abruptPopEvents"] == 0
        and m["rayMissesTotal"] == 0
        and m["average_fps"] >= 60.0
        and m["p95_ms"] <= 16.67
        and m["rangeStats"]["rangeRequests"] >= 2
    )

    receipt = {
        "schema": "scavenger-procedural-map-b4-verification-v1",
        "phase": "B4",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "archive_budget": budget,
        "browser_transition_verification": browser_res,
        "gate_passed": bool(gate_passed),
    }

    receipt_path = EVIDENCE_DIR / "B4验证凭证.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "B4",
        "gate_passed": receipt["gate_passed"],
        "archive_count": budget["archive_count"],
        "binary_index_kb": budget["binary_index_kb"],
        "total_archive_mib": budget["total_archive_mib"],
        "overview_fallbacks": m["overviewFallbacks"],
        "abrupt_pop_events": m["abruptPopEvents"],
        "ray_misses": m["rayMissesTotal"],
        "range_requests": m["rangeStats"]["rangeRequests"],
        "average_fps": m["average_fps"],
        "p95_ms": m["p95_ms"],
    }, ensure_ascii=False))
    if not receipt["gate_passed"]:
        raise RuntimeError("B4 阶段关口未通过，请检查 B4验证凭证.json")


if __name__ == "__main__":
    main()
