"""Chrome 与 Edge 双浏览器串行验证 127.0.0.1:8800 单端口主服务全链路闭环。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "local-data/主服务/V001-c7c1e6f7"


def run_browser(channel: str, base_url: str, out_dir: Path, headed: bool = False) -> dict:
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

    # 1. /healthz、/api/status 与白名单 /ui/* HTTP 状态检查
    with urlopen(f"{base_url}/healthz", timeout=5) as resp:
        health = json.load(resp)
    with urlopen(f"{base_url}/api/status", timeout=5) as resp:
        api_st = json.load(resp)
    ui_codes = {}
    for rt in ("/ui/令牌.css", "/ui/组件.css", "/ui/场内视觉.css", "/ui/交互控件.mjs", "/ui/图标与标绘.mjs", "/ui-spec"):
        with urlopen(base_url + quote(rt), timeout=5) as resp:
            ui_codes[rt] = resp.status
    ok_health = (
        health.get("status") == "ok"
        and health.get("source_sha256") == api_st.get("source_sha256")
        and api_st.get("app") == "scavenger-ui-ux-live-spec"
        and all(v == 200 for v in ui_codes.values())
    )
    record("/healthz、/api/status及/ui/*单源HTTP 200且摘要一致", ok_health, {"health": health, "ui_codes": ui_codes})

    launch_args = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=swiftshader",
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed, args=launch_args)
        page = browser.new_page(viewport={"width": 1600, "height": 960}, device_scale_factor=1)
        page.on("pageerror", lambda err: report["errors"].append(f"pageerror: {err}"))
        page.on("console", lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        try:
            # 2. 4A 主菜单大厅 (/) + /settings 偏好修改持久化
            page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("() => window.__SCV_APP__?.ready === true", timeout=20000)
            report["browser_version"] = browser.version
            lobby_info = page.evaluate(
                """() => ({
                    view: window.__SCV_APP__.getView(),
                    armory: window.__SCV_APP__.getArmoryState(),
                    selectCount: document.querySelectorAll('select').length,
                })"""
            )
            page.screenshot(path=str(out_dir / "01-4A主菜单大厅.png"))

            # 切到系统设置调整物理尺码与画幅再切回默认以确认持久化
            page.locator('#app-nav-tabs button[data-route="/settings"]').click()
            page.wait_for_timeout(120)
            page.locator('#set-size-seg button[data-size="xl"]').click()
            page.locator("#set-sfx-toggle").click()
            prefs_after = page.evaluate("window.__SCV_APP__.getPrefs()")
            page.locator("#set-reset-prefs").click()
            prefs_reset = page.evaluate("window.__SCV_APP__.getPrefs()")
            ok_lobby = (
                lobby_info["view"] == "/"
                and lobby_info["armory"]["total"] == 129
                and lobby_info["selectCount"] == 0
                and prefs_after["uiSize"] == "xl"
                and prefs_reset["uiSize"] == "lg"
            )
            record("4A主菜单大厅加载与系统设置持久化闭环", ok_lobby, {"lobby": lobby_info, "prefs_after": prefs_after})

            # 3. 4B 129车武器库 (/armory) 真3D流式渲染、搜索空态与编队持久化
            page.goto(f"{base_url}/armory", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("() => window.__SCV_APP__?.ready === true && window.__SCV_APP__.getArmoryState().total === 129", timeout=20000)
            page.wait_for_function("() => window.__SCV_APP__.getArmoryState().stage.meshCount > 0", timeout=30000)
            m1a2_state = page.evaluate("window.__SCV_APP__.getArmoryState()")
            page.screenshot(path=str(out_dir / "02-4B武器库129载具3D整备.png"))

            # 切换空中载具 US_AH64D 并加入第4辆进编队
            page.locator("#armory-search").fill("NOT_EXISTS_KEYWORD_999")
            page.wait_for_timeout(80)
            empty_shown = page.evaluate("document.getElementById('armory-empty').hidden === false")
            page.locator("#armory-reset-filter").click()
            page.evaluate("window.__SCV_APP__.selectVehicle('US_LAV25')")
            page.wait_for_function("() => window.__SCV_APP__.getArmoryState().selectedSlug === 'US_LAV25' && window.__SCV_APP__.getArmoryState().stage.meshCount > 0", timeout=30000)
            page.locator("#armory-toggle-roster").click()
            roster_after = page.evaluate("window.__SCV_APP__.getArmoryState().rosterSlugs")
            page.locator("#armory-clear-roster").click()
            ok_armory = (
                m1a2_state["total"] == 129
                and m1a2_state["stage"]["meshCount"] > 0
                and empty_shown
                and "US_LAV25" in roster_after
            )
            record("4B 129车武器库真3D Range流式展台、空态与编队选配", ok_armory, {"m1a2": m1a2_state, "empty": empty_shown, "roster": roster_after})

            # 4. 4C 9km大地图实战 (/play) + 过渡管线保留 + 暂停与LIFO ESC
            page.goto(f"{base_url}/play?aspect=16:9", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
            page.wait_for_timeout(500)
            play_state = page.evaluate(
                """() => {
                    const cv = document.querySelector('#canvas-host > canvas');
                    return {
                        canvasAttr: cv ? [cv.width, cv.height] : null,
                        hud: window.__SCV_MAP__.getUiHudState(),
                        trans: window.__SCV_MAP__.getTransitionPipelineState?.() || null,
                    };
                }"""
            )
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)
            pause_links = page.evaluate(
                """() => ({
                    paused: window.__SCV_MAP__.getUiHudState().paused,
                    hasLobbyLink: Boolean(document.querySelector('#pause-overlay a[href="/"]')),
                    hasArmoryLink: Boolean(document.querySelector('#pause-overlay a[href="/armory"]')),
                })"""
            )
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            page.screenshot(path=str(out_dir / "03-4C实战9km大地图.png"))
            ok_play = (
                play_state["canvasAttr"] == [1208, 680]
                and play_state["trans"] is not None
                and pause_links["paused"] is True
                and pause_links["hasLobbyLink"]
                and pause_links["hasArmoryLink"]
                and page.evaluate("window.__SCV_MAP__.getUiHudState().paused") is False
            )
            record("4C 9km大地图680p实战、过渡管线完整保留与暂停主入口闭环", ok_play, {"play": play_state, "pause": pause_links})

            # 5. /editor 自动展开地图编辑器抽屉并实时放置高楼验证物理派生
            page.goto(f"{base_url}/editor?aspect=16:9", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true", timeout=60000)
            editor_open = page.evaluate("window.__SCV_MAP__.getUiHudState().editorOpen === true")
            page.locator("#btn-ed-add-bldg").click()
            page.wait_for_timeout(320)
            obb_txt = page.locator("#ui-ed-obb").inner_text()
            nav_txt = page.locator("#ui-ed-nav").inner_text()
            record("/editor自动展开地图编辑抽屉并实时物理派生阻断", editor_open and ("1 个动态" in obb_txt) and ("阻断" in nav_txt), {"editor_open": editor_open, "obb": obb_txt, "nav": nav_txt})

            # 6. /ui-spec V2.2 活体规范台全面检视
            page.goto(f"{base_url}/ui-spec", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("() => window.uiSpec?.ready === true", timeout=20000)
            spec_check = page.evaluate(
                """() => ({
                    version: window.uiSpec.version,
                    v22Ready: window.uiSpec.getV22ComponentsReady(),
                    has640Text: document.body.innerText.includes('640px 3D 画布模拟'),
                    hasOldChamfer: document.querySelectorAll('.ui-pod--chamfer').length,
                })"""
            )
            page.locator("#v22-open-modal").click()
            page.wait_for_timeout(80)
            modal_open = page.evaluate("document.getElementById('v22-modal-backdrop').hidden === false")
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            modal_closed = page.evaluate("document.getElementById('v22-modal-backdrop').hidden === true")
            page.screenshot(path=str(out_dir / "04-UI规范台V2_2.png"))
            ok_spec = (
                spec_check["version"] == "V2.2"
                and spec_check["v22Ready"] is True
                and spec_check["has640Text"] is False
                and spec_check["hasOldChamfer"] == 0
                and modal_open
                and modal_closed
            )
            record("/ui-spec V2.2无640px/死类名残留且全组件与模态LIFO可用", ok_spec, spec_check)

            # 7. 并入的开发与工具子路由 HTTP 200 与零报错加载核验
            dev_states = {}
            for dev_path in ("/dev/battlefield", "/dev/library/", "/dev/audio/"):
                resp = page.goto(f"{base_url}{dev_path}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(350)
                dev_states[dev_path] = resp.status if resp else 0
            record("并入的3项/dev/*工具子路由均返回200且加载正常", all(s == 200 for s in dev_states.values()), dev_states)

            # 8. 全链路零报错断言
            record("全链路控制台与页面零报错", len(report["errors"]) == 0, report["errors"])
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
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--out-base", default=str(OUT_ROOT), help="输出根目录")
    parser.add_argument("--run", required=True, help="本轮独立输出目录名")
    parser.add_argument("--headed", action="store_true", help="有窗口运行")
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
        raise RuntimeError(f"主服务健康检查异常：{health}")

    channels = ["chrome", "msedge"] if args.channel == "both" else [args.channel]
    summary = {"run": args.run, "port": args.port, "channels": {}}
    for ch in channels:
        rep = run_browser(ch, base_url, run_root / ch, headed=args.headed)
        summary["channels"][ch] = {"passed": rep["passed"], "checks": len(rep["checks"])}

    (run_root / "汇总摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
