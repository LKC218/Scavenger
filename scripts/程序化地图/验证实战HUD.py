"""在独立端口上用 Chrome 与 Edge 串行实机验证程序化大地图 V2.1 实战 HUD 与 680p 渲染基线。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "local-data/程序化地图/V001-fcf46de3/HUD实装验证-c7c1e6f7"


def run_browser(channel: str, base_url: str, out_dir: Path, headed: bool = False, entry_path: str = "/") -> dict:
    out_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "channel": channel,
        "base_url": base_url,
        "checks": [],
        "errors": [],
        "started_at": time.time(),
    }

    def record(name: str, passed: bool, details=None) -> None:
        report["checks"].append({"name": name, "passed": bool(passed), "details": details})
        print(json.dumps({"channel": channel, "check": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(f"[{channel}] 校验未通过：{name} -> {details}")

    # 1. 验证白名单路由 /ui/* 与 /ui-spec 均返回 HTTP 200
    routes_to_check = [
        "/ui/令牌.css",
        "/ui/组件.css",
        "/ui/场内视觉.css",
        "/ui/交互控件.mjs",
        "/ui/图标与标绘.mjs",
        "/ui-spec",
        "/src/程序化地图/实战HUD.css",
    ]
    route_codes = {}
    for rt in routes_to_check:
        with urlopen(base_url + quote(rt), timeout=5) as resp:
            route_codes[rt] = resp.status
    record("UI规范白名单路由均返回HTTP 200", all(v == 200 for v in route_codes.values()), route_codes)

    launch_args = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=swiftshader",
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed, args=launch_args)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda err: report["errors"].append(f"pageerror: {err}"))
        page.on("console", lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        try:
            norm_path = entry_path if entry_path.startswith("/") else f"/{entry_path}"
            page.goto(f"{base_url}{norm_path}?aspect=16:9", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
            report["browser_version"] = browser.version
            page.wait_for_timeout(600)

            # 2. 680p 16:9 基线、无原生 <select>、无大面积模糊验证
            base_state = page.evaluate(
                """() => {
                    const cv = document.querySelector('#canvas-host > canvas');
                    const cs = cv ? getComputedStyle(cv) : null;
                    const pods = [...document.querySelectorAll('.ui-pod')];
                    const allNoBlur = pods.every(p => getComputedStyle(p).backdropFilter === 'none');
                    return {
                        baseline: window.__SCV_MAP__.getRenderBaseline(),
                        hud: window.__SCV_MAP__.getUiHudState(),
                        canvasAttr: cv ? [cv.width, cv.height] : null,
                        imageRendering: cs?.imageRendering,
                        filter: cs?.filter,
                        nativeSelectCount: document.querySelectorAll('select').length,
                        allPodsNoBlur: allNoBlur,
                        podCount: pods.length,
                    };
                }"""
            )
            ok_base = (
                base_state["canvasAttr"] == [1208, 680]
                and base_state["imageRendering"] == "pixelated"
                and "sepia" in (base_state["filter"] or "")
                and base_state["nativeSelectCount"] == 0
                and base_state["allPodsNoBlur"] is True
                and base_state["hud"]["uiSize"] == "lg"
                and base_state["hud"]["heroCount"] == 3
            )
            record("16:9 680p基线与V2.1 HUD实底渲染及零原生select校验", ok_base, base_state)
            page.screenshot(path=str(out_dir / "01-16x9-lg首屏.png"))

            # 3. 展开右抽屉，测试三档原生物理尺码递增、100混编负载及添加高楼物理派生
            page.locator("#btn-toggle-editor").click()
            page.locator("#editor-drawer").wait_for(state="visible")
            scale_metrics = {}
            for sz in ("compact", "lg", "xl"):
                page.locator(f'#hud-size-seg button[data-size="{sz}"]').click()
                page.wait_for_timeout(120)
                scale_metrics[sz] = page.evaluate(
                    """() => {
                        const btn = document.getElementById('btn-toggle-editor');
                        const cell = document.querySelector('#hud-cmd-grid .ui-cmd-wep-card');
                        const items = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')];
                        const host = document.getElementById('canvas-host');
                        return {
                            uiSize: window.__SCV_MAP__.getUiHudState().uiSize,
                            btnH: Math.round(btn.getBoundingClientRect().height * 10) / 10,
                            cmdH: Math.round((cell?.getBoundingClientRect().height || 0) * 10) / 10,
                            cmdSvgCount: items.filter(c => Boolean(c.querySelector('svg'))).length,
                            cmdSubCount: document.querySelectorAll('#hud-cmd-grid .c-sub').length,
                            isPartitioned: Boolean(document.getElementById('hud-cmd-grid')?.classList.contains('is-partitioned')),
                            transform: getComputedStyle(host).transform,
                        };
                    }"""
                )
            ok_scale = (
                scale_metrics["compact"]["btnH"] < scale_metrics["lg"]["btnH"] < scale_metrics["xl"]["btnH"]
                and scale_metrics["compact"]["cmdH"] < scale_metrics["lg"]["cmdH"] < scale_metrics["xl"]["cmdH"]
                and all(v["isPartitioned"] and v["cmdSvgCount"] == 6 and v["cmdSubCount"] == 0 for v in scale_metrics.values())
                and all(v["transform"] == "none" for v in scale_metrics.values())
            )
            page.locator("#btn-load-100").click()
            page.locator("#btn-ed-add-bldg").click()
            page.wait_for_function("() => (document.getElementById('ui-ed-obb')?.textContent || '').includes('1 个动态')", timeout=10000)
            page.wait_for_timeout(120)
            after_edit = page.evaluate(
                """() => ({
                    hud: window.__SCV_MAP__.getUiHudState(),
                    obbText: document.getElementById('ui-ed-obb')?.textContent,
                    navText: document.getElementById('ui-ed-nav')?.textContent,
                })"""
            )
            record(
                "右抽屉三档物理尺码无损递增及100混编和实时建楼物理派生",
                ok_scale and after_edit["hud"]["squadCount"] == 100 and "1 个动态" in (after_edit["obbText"] or ""),
                {"scale_metrics": scale_metrics, "after_edit": after_edit},
            )
            page.screenshot(path=str(out_dir / "02-右抽屉地图编辑与超大尺码.png"))

            # 切回 G01 与 lg，关闭抽屉测试选车、小地图跳转、ROE 向上弹出与 4A 暂停
            page.locator('#hud-size-seg button[data-size="lg"]').click()
            page.locator("#btn-load-g01").click()
            page.locator("#btn-close-editor").click()
            page.locator("#editor-drawer").wait_for(state="hidden")

            page.keyboard.press("2")
            page.wait_for_timeout(150)
            cam_before = page.evaluate("window.__SCV_MAP__.getCameraState()")
            mm_box = page.locator("#hud-minimap-box").bounding_box()
            assert mm_box is not None
            page.mouse.click(mm_box["x"] + mm_box["width"] * 0.65, mm_box["y"] + mm_box["height"] * 0.35)
            page.wait_for_timeout(200)
            cam_after = page.evaluate("window.__SCV_MAP__.getCameraState()")

            page.locator("#hud-roe-select .ui-select-trigger").click()
            roe_open = page.evaluate(
                """() => {
                    const box = document.getElementById('hud-roe-select');
                    const menu = box.querySelector('.ui-select-menu');
                    const rect = menu.getBoundingClientRect();
                    return {
                        isOpen: box.classList.contains('is-open'),
                        isDropup: box.classList.contains('is-dropup'),
                        inViewport: rect.top >= 0 && rect.bottom <= window.innerHeight,
                    };
                }"""
            )
            # 第一次 ESC 仅收起下拉，第二次 ESC 呼出 4A 二分暂停菜单
            page.keyboard.press("Escape")
            after_esc1 = page.evaluate("({ roeOpen: document.getElementById('hud-roe-select').classList.contains('is-open'), paused: window.__SCV_MAP__.getUiHudState().paused })")
            page.keyboard.press("Escape")
            after_esc2 = page.evaluate("window.__SCV_MAP__.getUiHudState().paused")
            page.wait_for_timeout(180)
            page.screenshot(path=str(out_dir / "03-4A二分暂停菜单.png"))
            page.keyboard.press("Escape")
            after_esc3 = page.evaluate("window.__SCV_MAP__.getUiHudState().paused")

            ok_flow = (
                page.evaluate("window.__SCV_MAP__.getUiHudState().selectedUnitId") == "US_AH64D_01"
                and (cam_after["x"] != cam_before["x"] or cam_after["z"] != cam_before["z"])
                and roe_open["isOpen"] and roe_open["isDropup"] and roe_open["inViewport"]
                and not after_esc1["roeOpen"] and not after_esc1["paused"]
                and after_esc2 is True
                and after_esc3 is False
            )
            record(
                "选车快捷键、雷达点击跳镜、ROE自动向上弹出与LIFO ESC 4A暂停闭环",
                ok_flow,
                {
                    "cam_before": cam_before,
                    "cam_after": cam_after,
                    "roe_open": roe_open,
                    "after_esc1": after_esc1,
                    "after_esc2": after_esc2,
                    "after_esc3": after_esc3,
                },
            )

            # 5. 2560×1080 视口下切换 21:9 (1588×680) Hor+ 核验顶栏正间距、放大 1:1 雷达、大图标指令舱与弹性居中卡片坞
            page.set_viewport_size({"width": 2560, "height": 1080})
            page.locator("#btn-quick-aspect").click()
            page.wait_for_timeout(350)
            wide_metrics = page.evaluate(
                """() => {
                    const cv = document.querySelector('#canvas-host > canvas');
                    const tl = document.querySelector('.scv-top-left').getBoundingClientRect();
                    const tc = document.querySelector('.scv-top-center').getBoundingClientRect();
                    const tr = document.querySelector('.scv-top-right').getBoundingClientRect();
                    const bl = document.querySelector('.scv-bl-wing').getBoundingClientRect();
                    const deck = document.querySelector('.scv-card-deck').getBoundingClientRect();
                    const br = document.querySelector('.scv-br-wing').getBoundingClientRect();
                    const mm = document.getElementById('hud-minimap-box').getBoundingClientRect();
                    const cells = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card, #hud-cmd-grid .ui-cmd-util-btn')];
                    const radarDock = document.getElementById('hud-radar-dock').getBoundingClientRect();
                    const pipSt = window.__SCV_MAP__.getPipCamState();
                    return {
                        canvasAttr: cv ? [cv.width, cv.height] : null,
                        hud: window.__SCV_MAP__.getUiHudState(),
                        topGapL: Math.round(tc.left - tl.right),
                        topGapR: Math.round(tr.left - tc.right),
                        bottomCenterClearance: Math.round(br.left - bl.right),
                        deckWidth: Math.round(deck.width),
                        deckHeight: Math.round(deck.height),
                        rightDockWidth: Math.round(br.width),
                        mmWidth: Math.round(mm.width * 10) / 10,
                        mmHeight: Math.round(mm.height * 10) / 10,
                        radarRightDiffH: Math.round(Math.abs(radarDock.height - br.height)),
                        hasDeployRail: Boolean(document.getElementById('hud-deploy-rail')),
                        pipFrameCount: pipSt?.frameCount || 0,
                        pipEnvExcluded: Boolean(pipSt?.environmentExcluded),
                        pipShadowSkipped: Boolean(pipSt?.shadowMapSkipped),
                        pipDrawCalls: Number(pipSt?.lastDrawCalls || 0),
                        pipTriangles: Number(pipSt?.lastTriangles || 0),
                        pipDurationMs: Number(pipSt?.lastDurationMs || 0),
                        isPartitioned: Boolean(document.getElementById('hud-cmd-grid')?.classList.contains('is-partitioned')),
                        cmdGridH: Math.round((document.getElementById('hud-cmd-grid')?.getBoundingClientRect().height || 0) * 10) / 10,
                        cmdSvgCount: cells.filter(c => Boolean(c.querySelector('svg'))).length,
                        cmdSubCount: document.querySelectorAll('#hud-cmd-grid .c-sub').length,
                        threeBaysNoOverlap: bl.right <= deck.left + 2 && deck.right <= br.left + 2,
                    };
                }"""
            )
            page.locator('#hud-cmd-pager button[data-cmd-page="move"]').click()
            page.wait_for_timeout(120)
            move_grid_h = page.evaluate("Math.round((document.getElementById('hud-cmd-grid')?.getBoundingClientRect().height || 0) * 10) / 10")
            page.locator('#hud-cmd-pager button[data-cmd-page="attack"]').click()
            page.wait_for_timeout(300)
            wide_metrics["moveCmdGridH"] = move_grid_h
            ok_wide = (
                wide_metrics["canvasAttr"] == [1588, 680]
                and wide_metrics["hud"]["aspectKey"] == "21:9"
                and wide_metrics["topGapL"] > 0
                and wide_metrics["topGapR"] > 0
                and wide_metrics["bottomCenterClearance"] > 300
                and wide_metrics["threeBaysNoOverlap"] is True
                and 300 <= wide_metrics["deckWidth"] < wide_metrics["bottomCenterClearance"] - 200
                and wide_metrics["deckHeight"] <= 156
                and wide_metrics["mmWidth"] >= 268
                and abs(wide_metrics["mmWidth"] - wide_metrics["mmHeight"]) <= 1.5
                and wide_metrics["rightDockWidth"] >= 400
                and wide_metrics["radarRightDiffH"] <= 18
                and wide_metrics["hasDeployRail"] is True
                and wide_metrics["pipFrameCount"] >= 1
                and wide_metrics["pipEnvExcluded"] is True
                and wide_metrics["pipShadowSkipped"] is True
                and 1 <= wide_metrics["pipDrawCalls"] <= 48
                and wide_metrics["isPartitioned"] is True
                and abs(wide_metrics["cmdGridH"] - move_grid_h) <= 1.0
                and wide_metrics["cmdSvgCount"] == 6
                and wide_metrics["cmdSubCount"] == 0
            )
            record("2560x1080下21:9 680p Hor+画布与放大1:1雷达、大图标指令舱及弹性卡片坞校验", ok_wide, wide_metrics)
            page.screenshot(path=str(out_dir / "04-21x9-2560超宽实景.png"))

            record("控制台与页面零报错", len(report["errors"]) == 0, report["errors"])
            report["passed"] = True
        except Exception as exc:
            report["passed"] = False
            report["failure"] = repr(exc)
            page.screenshot(path=str(out_dir / "失败现场.png"))
            raise
        finally:
            report["finished_at"] = time.time()
            (out_dir / "验证结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            browser.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--path", default="/play", help="主入口路由路径（如 / 或 /play）")
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--out-base", default=str(OUT_ROOT), help="输出根目录")
    parser.add_argument("--run", required=True, help="本轮独立输出目录名")
    parser.add_argument("--headed", action="store_true", help="使用有窗口模式运行")
    args = parser.parse_args()
    if Path(args.run).name != args.run or not args.run:
        raise ValueError("--run 只接受单个目录名")

    out_base = Path(args.out_base)
    if not out_base.is_absolute():
        out_base = ROOT / out_base
    run_root = out_base / args.run
    if run_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{run_root.relative_to(ROOT).as_posix()}")

    base_url = f"http://127.0.0.1:{args.port}"
    with urlopen(f"{base_url}/healthz", timeout=3) as r:
        health = json.load(r)
    if health.get("status") != "ok":
        raise RuntimeError(f"预览服务健康检查异常：{health}")

    channels = ["chrome", "msedge"] if args.channel == "both" else [args.channel]
    summary = {"run": args.run, "port": args.port, "path": args.path, "channels": {}}
    for ch in channels:
        rep = run_browser(ch, base_url, run_root / ch, headed=args.headed, entry_path=args.path)
        summary["channels"][ch] = {"passed": rep["passed"], "checks": len(rep["checks"])}

    (run_root / "汇总摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
