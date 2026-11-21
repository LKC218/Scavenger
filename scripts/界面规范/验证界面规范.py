"""在独立无窗口 Chrome 与 Edge 下串行验证 8766 全局 UI/UX 活体规范路由。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = ROOT / "src/界面规范"
OUTPUT_ROOT = ROOT / "local-data/界面规范/V003-c7c1e6f7/验证"
BASE_URL = "http://127.0.0.1:8766"

EXPECTED_TOKENS = {
    "--ui-ch-ground": "#38e5c0",
    "--ui-ch-heli": "#68c8ff",
    "--ui-ch-plane": "#b197fc",
    "--ui-state-select": "#f0d372",
    "--ui-state-warn": "#ffb800",
    "--ui-state-danger": "#ff5266",
    "--ui-state-toxic": "#a8f22c",
    "--ui-state-safe": "#3cd69f",
}


def compute_expected_source_sha() -> str:
    names = sorted(["index.html", "令牌.css", "组件.css", "场内视觉.css", "图标与标绘.mjs", "交互控件.mjs", "规范页.css", "规范页.mjs"])
    files = {}
    for name in names:
        with (SPEC_DIR / name).open("rb") as stream:
            files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def run_browser(channel: str, out_dir: Path, expected_sha: str) -> dict:
    with urlopen(f"{BASE_URL}/api/status", timeout=5) as resp:
        live_status = json.load(resp)
    if live_status.get("app") != "scavenger-ui-ux-live-spec" or live_status.get("source_sha256") != expected_sha:
        raise RuntimeError(f"8766 服务身份或源码摘要不一致：{live_status}")
    out_dir.mkdir(parents=True, exist_ok=False)

    report = {
        "channel": channel,
        "base_url": BASE_URL,
        "identity": live_status,
        "checks": [],
        "errors": [],
        "started_at": time.time(),
        "scope": "无窗口双浏览器自动化功能与同源核验；最终视觉美学审阅由工程师在本机打开 8766 完成",
    }

    def record(name: str, passed: bool, details=None) -> None:
        report["checks"].append({"name": name, "passed": bool(passed), "details": details})
        print(json.dumps({"channel": channel, "check": name, "passed": bool(passed)}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(f"[{channel}] 校验未通过：{name} -> {details}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        page.on("pageerror", lambda err: report["errors"].append(f"pageerror: {err}"))
        page.on("console", lambda msg: report["errors"].append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        try:
            # 1. 核对 /ui-spec 子路由别名可达
            page.goto(f"{BASE_URL}/ui-spec", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.uiSpec?.ready === true", timeout=15000)
            record("/ui-spec 子路由可达且初始化就绪", page.evaluate("window.uiSpec.ready === true"))

            # 2. 核对主入口 / 与同源身份
            page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.uiSpec?.ready === true && window.uiSpec.getStatusIdentity() !== null", timeout=15000)
            report["browser_version"] = browser.version
            live_id = page.evaluate("window.uiSpec.getStatusIdentity()")
            record("页面同源源码摘要一致", live_id.get("source_sha256") == expected_sha, live_id.get("source_sha256"))

            # 3. 色彩令牌同源读取验证
            token_result = page.evaluate(
                """expected => {
                    const cs = getComputedStyle(document.documentElement);
                    const actual = {};
                    const matched = {};
                    for (const [k, v] of Object.entries(expected)) {
                        actual[k] = cs.getPropertyValue(k).trim().toLowerCase();
                        matched[k] = actual[k] === v.toLowerCase();
                    }
                    const cardValues = {};
                    document.querySelectorAll('.swatch-card').forEach(el => {
                        cardValues[el.dataset.tokenName] = (el.dataset.tokenValue || '').toLowerCase();
                    });
                    return { actual, matched, cardMatched: Object.keys(expected).every(k => cardValues[k] === expected[k].toLowerCase()) };
                }""",
                EXPECTED_TOKENS,
            )
            record(
                "五维语义色令牌同源与页面色卡一致",
                all(token_result["matched"].values()) and token_result["cardMatched"],
                token_result,
            )

            # 4. 12 类武器库兵种剪影 ＋ 4 枚情景警示图标验证
            silhouette_info = page.evaluate(
                """() => {
                    const kinds = ['mbt', 'ifv', 'recon', 'arty', 'aa', 'atgm', 'supply', 'attack_heli', 'trans_heli', 'cas', 'fighter', 'uav'];
                    const out = {};
                    for (const k of kinds) {
                        const svgs = [...document.querySelectorAll(`#silhouette-mount svg[data-silhouette="${k}"]`)];
                        const p = svgs[0]?.querySelector('path');
                        const box = svgs[0]?.getBoundingClientRect();
                        out[k] = {
                            count: svgs.length,
                            fillRule: p?.getAttribute('fill-rule'),
                            width: box?.width ?? 0,
                            height: box?.height ?? 0,
                        };
                    }
                    const hazards = ['biohazard', 'radiation', 'gasmask', 'horde'];
                    const haz = {};
                    for (const h of hazards) {
                        const svg = document.querySelector(`#hazard-icon-mount svg[data-hazard="${h}"]`);
                        const box = svg?.getBoundingClientRect();
                        haz[h] = { exists: Boolean(svg), width: box?.width ?? 0, height: box?.height ?? 0 };
                    }
                    return { vehicles: out, hazards: haz };
                }"""
            )
            ok_sil = all(
                v["count"] >= 4 and v["fillRule"] == "evenodd" and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["vehicles"].values()
            ) and all(
                v["exists"] and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["hazards"].values()
            )
            record("12类武器库负空间剪影与4枚情景图标均正常渲染", ok_sil, silhouette_info)

            # 5. 演训与调试抽屉默认隐藏及键鼠开关验证
            default_closed = page.evaluate("document.getElementById('debug-drawer').hidden === true && !window.uiSpec.isDrawerOpen()")
            page.locator("#btn-toggle-drawer").click()
            opened_by_btn = page.evaluate("document.getElementById('debug-drawer').hidden === false && window.uiSpec.isDrawerOpen()")
            page.locator("#drawer-close").click()
            closed_by_btn = page.evaluate("document.getElementById('debug-drawer').hidden === true")
            page.keyboard.press("Backquote")
            opened_by_key = page.evaluate("window.uiSpec.isDrawerOpen()")
            page.keyboard.press("Backquote")
            closed_by_key = page.evaluate("!window.uiSpec.isDrawerOpen()")
            record(
                "演训调试抽屉默认隐藏且按钮/反引号键正常开合",
                default_closed and opened_by_btn and closed_by_btn and opened_by_key and closed_by_key,
                {
                    "default_closed": default_closed,
                    "opened_by_btn": opened_by_btn,
                    "closed_by_btn": closed_by_btn,
                    "opened_by_key": opened_by_key,
                    "closed_by_key": closed_by_key,
                },
            )

            # 6. 双层分离高清沙盒与固定翼无地面双环验证
            page.locator("#sec-sandbox").scroll_into_view_if_needed()
            page.wait_for_timeout(120)
            before_sb = page.evaluate("window.uiSpec.getSandboxMetrics()")
            page.locator("#sandbox-pixel-toggle").click()
            page.wait_for_timeout(120)
            after_sb = page.evaluate("window.uiSpec.getSandboxMetrics()")
            dual_layer_ok = (
                not before_sb["pixelSim"]
                and after_sb["pixelSim"]
                and after_sb["groundCanvasWidth"] == 640
                and after_sb["vehicleCanvasWidth"] == 640
                and after_sb["airCanvasWidth"] == after_sb["stageCssWidth"] * 2
                and not before_sb["planeRingVisible"]
                and not after_sb["planeRingVisible"]
            )
            record("640px模拟下UI标绘层保持原生DPR高清且固定翼无地面双环", dual_layer_ok, {"before": before_sb, "after": after_sb})
            page.screenshot(path=str(out_dir / "05-沙盒双层高清对照.png"))

            # 7. 密集编队车底锚定防叠字、悬停展开与 Tab 循环验证
            page.locator("#sec-cluster").scroll_into_view_if_needed()
            page.evaluate("window.uiSpec.setClusterSpacing(8)")
            page.wait_for_timeout(100)
            dense = page.evaluate("window.uiSpec.getClusterMetrics()")
            has_degraded = any(r["mode"] in ("compact", "dimmed") for r in dense["rows"])
            anchors_fixed = all(not r["anchorMoved"] for r in dense["rows"])
            compact_target = next((r["id"] for r in dense["rows"] if r["mode"] == "compact"), None)
            hovered_expanded = False
            if compact_target:
                page.locator(f'#cluster-ui-layer .ground-mark[data-cluster-id="{compact_target}"]').hover(force=True)
                page.wait_for_timeout(80)
                after_hover = page.evaluate("window.uiSpec.getClusterMetrics()")
                row = next((r for r in after_hover["rows"] if r["id"] == compact_target), None)
                hovered_expanded = bool(row and row["mode"] == "full")
            before_sel = page.evaluate("window.uiSpec.getClusterMetrics().selectedId")
            page.locator("#cluster-stage").focus()
            page.keyboard.press("Tab")
            after_sel = page.evaluate("window.uiSpec.getClusterMetrics().selectedId")
            record(
                "密集编队车底锚定不跳槽、紧凑降级、悬停展开及Tab循环",
                has_degraded and anchors_fixed and hovered_expanded and before_sel != after_sel,
                {
                    "has_degraded": has_degraded,
                    "anchors_fixed": anchors_fixed,
                    "compact_target": compact_target,
                    "hovered_expanded": hovered_expanded,
                    "before_sel": before_sel,
                    "after_sel": after_sel,
                },
            )
            page.screenshot(path=str(out_dir / "06-密集防叠字试验.png"))

            # 8. 定制战术下拉同源交互、底沿自动 .is-dropup 与严格 LIFO ESC
            page.evaluate(
                """() => {
                    const trig = document.querySelector('#scale-roe-select .ui-select-trigger');
                    const r = trig.getBoundingClientRect();
                    window.scrollTo(0, Math.max(0, window.scrollY + r.bottom - (window.innerHeight - 90)));
                }"""
            )
            page.wait_for_timeout(120)
            roe_trig = page.locator("#scale-roe-select .ui-select-trigger")
            roe_trig.click()
            dropup_state = page.evaluate(
                """() => {
                    const box = document.getElementById('scale-roe-select');
                    const menu = box.querySelector('.ui-select-menu');
                    const rect = menu.getBoundingClientRect();
                    return {
                        isOpen: box.classList.contains('is-open'),
                        isDropup: box.classList.contains('is-dropup'),
                        menuVisible: !menu.hidden,
                        inViewport: rect.top >= 0 && rect.bottom <= window.innerHeight,
                    };
                }"""
            )
            page.locator('#scale-roe-select .ui-select-option[data-val="return"]').click()
            selected_after_click = page.evaluate("document.getElementById('scale-roe-select').value")
            roe_trig.click()
            page.keyboard.press("Escape")
            esc_closed = page.evaluate(
                """() => {
                    const box = document.getElementById('scale-roe-select');
                    return !box.classList.contains('is-open') && box.querySelector('.ui-select-menu').hidden === true && !window.uiSpec.isDrawerOpen();
                }"""
            )
            record(
                "定制战术下拉同源交互、自动向上弹出与LIFO ESC优先收起",
                dropup_state["isOpen"]
                and dropup_state["isDropup"]
                and dropup_state["menuVisible"]
                and dropup_state["inViewport"]
                and selected_after_click == "return"
                and esc_closed,
                {"dropup_state": dropup_state, "selected_after_click": selected_after_click, "esc_closed": esc_closed},
            )

            # 9. 三档原生物理尺码无损放大验证（compact < lg < xl 且零 transform:scale）
            page.locator("#sec-hud-scale").scroll_into_view_if_needed()
            page.wait_for_timeout(100)
            scale_samples = {}
            for sz in ("compact", "lg", "xl"):
                page.locator(f'#spec-size-seg button[data-size="{sz}"]').click()
                page.wait_for_timeout(80)
                scale_samples[sz] = page.evaluate("window.uiSpec.getHudScaleMetrics()")
            scale_ok = (
                scale_samples["compact"]["btnHeight"] < scale_samples["lg"]["btnHeight"] < scale_samples["xl"]["btnHeight"]
                and scale_samples["compact"]["cmdHeight"] < scale_samples["lg"]["cmdHeight"] < scale_samples["xl"]["cmdHeight"]
                and scale_samples["compact"]["selectHeight"] < scale_samples["lg"]["selectHeight"] < scale_samples["xl"]["selectHeight"]
                and all(m["stageTransform"] == "none" for m in scale_samples.values())
            )
            record("三档原生物理尺码严格递增且零transform缩放", scale_ok, scale_samples)
            page.screenshot(path=str(out_dir / "07-三档物理尺码与HUD骨架.png"))

            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(100)
            page.screenshot(path=str(out_dir / "01-规范页首屏全貌.png"), full_page=True)

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
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--run", required=True, help="本轮独立输出目录名（禁止覆盖既有目录）")
    args = parser.parse_args()
    if Path(args.run).name != args.run or not args.run:
        raise ValueError("--run 只接受单个目录名")

    run_root = OUTPUT_ROOT / args.run
    if run_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{run_root.relative_to(ROOT).as_posix()}")

    expected_sha = compute_expected_source_sha()
    channels = ["chrome", "msedge"] if args.channel == "both" else [args.channel]
    summary = {"run": args.run, "expected_source_sha256": expected_sha, "channels": {}}
    for ch in channels:
        report = run_browser(ch, run_root / ch, expected_sha)
        summary["channels"][ch] = {"passed": report["passed"], "checks": len(report["checks"])}

    (run_root / "汇总摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
