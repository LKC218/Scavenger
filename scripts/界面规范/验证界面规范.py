"""在独立无窗口 Chrome 与 Edge 下串行验证全局 UI/UX 活体规范台（V2.2）。"""
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
DEFAULT_BASE_URL = "http://127.0.0.1:8800"

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


def run_browser(channel: str, out_dir: Path, expected_sha: str, base_url: str) -> dict:
    with urlopen(f"{base_url}/api/status", timeout=5) as resp:
        live_status = json.load(resp)
    if live_status.get("app") != "scavenger-ui-ux-live-spec" or live_status.get("source_sha256") != expected_sha:
        raise RuntimeError(f"规范路由服务身份或源码摘要不一致：{live_status}")
    out_dir.mkdir(parents=True, exist_ok=False)

    report = {
        "channel": channel,
        "base_url": base_url,
        "identity": live_status,
        "checks": [],
        "errors": [],
        "started_at": time.time(),
        "scope": "无窗口双浏览器自动化功能与同源核验；最终视觉美学审阅由工程师在本机打开 8800/ui-spec 完成",
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
            # 1. 核对 /ui-spec 入口与 V2.2 全组件初始化就绪
            page.goto(f"{base_url}/ui-spec", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_function("window.uiSpec?.ready === true && window.uiSpec.getStatusIdentity() !== null", timeout=15000)
            report["browser_version"] = browser.version
            v22_ok = page.evaluate("window.uiSpec.ready === true && window.uiSpec.version === 'V2.2' && window.uiSpec.getV22ComponentsReady() === true")
            record("/ui-spec V2.2 全组件初始化就绪", v22_ok)

            # 2. 核对同源源码指纹一致性
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
                "7维语义色令牌同源与页面色卡一致",
                all(token_result["matched"].values()) and token_result["cardMatched"],
                token_result,
            )

            # 4. 12 类武器库兵种剪影 ＋ 4 大通用武器剪影 ＋ 4 枚情景警示图标 ＋ 26 枚功能图标验证
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
                    const weaponKinds = ['cannon', 'rocket', 'missile', 'bomb'];
                    const weapons = {};
                    for (const w of weaponKinds) {
                        const svgs = [...document.querySelectorAll(`#weapon-icon-mount svg[data-weapon-icon="${w}"]`)];
                        const p = svgs[0]?.querySelector('path');
                        const box = svgs[0]?.getBoundingClientRect();
                        weapons[w] = {
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
                    const reqHud = ['flare', 'altitude', 'evacuate', 'sprint', 'autofire', 'follow', 'wheel', 'stop', 'focus'];
                    const hudIcons = {};
                    for (const h of reqHud) {
                        const svg = document.querySelector(`#hud-icon-mount svg[data-hud-icon="${h}"]`);
                        const box = svg?.getBoundingClientRect();
                        hudIcons[h] = { exists: Boolean(svg), width: box?.width ?? 0, height: box?.height ?? 0 };
                    }
                    return {
                        vehicles: out,
                        weapons,
                        hazards: haz,
                        hudIcons,
                        totalHudCount: document.querySelectorAll('#hud-icon-mount svg[data-hud-icon]').length,
                    };
                }"""
            )
            ok_sil = all(
                v["count"] >= 4 and v["fillRule"] == "evenodd" and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["vehicles"].values()
            ) and all(
                v["count"] >= 2 and v["fillRule"] == "evenodd" and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["weapons"].values()
            ) and all(
                v["exists"] and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["hazards"].values()
            ) and all(
                v["exists"] and v["width"] > 0 and v["height"] > 0
                for v in silhouette_info["hudIcons"].values()
            ) and silhouette_info["totalHudCount"] >= 26
            record("12类兵种与4大武器剪影及4枚情景和26枚HUD图标均正常渲染", ok_sil, silhouette_info)

            # 4.1 品牌标识（生化肃清三楔盾）4 档光学尺码、色调切换、顶栏 Sigil 与 Path2D 停机坪实绘校验
            brand_before = page.evaluate(
                """() => {
                    const idMeta = window.uiSpec.getBrandIdentity();
                    const topSvg = document.querySelector('#brand-sigil svg[data-brand-logo="bio-purge-triad-shield"]');
                    const variants = ['mark', 'sigil', 'favicon', 'stencil'];
                    const variantOk = {};
                    for (const v of variants) {
                        const el = document.querySelector(`#brand-identity-mount svg[data-brand-variant="${v}"]`);
                        const r = el?.getBoundingClientRect();
                        variantOk[v] = Boolean(el && r && r.width > 0 && r.height > 0);
                    }
                    const lockup = document.querySelector('#brand-identity-mount .ui-brand-lockup');
                    const canvas = document.getElementById('brand-stencil-canvas');
                    let nonEmptyCanvas = false;
                    if (canvas) {
                        const ctx = canvas.getContext('2d');
                        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                        for (let i = 3; i < data.length; i += 64) {
                            if (data[i] > 0) { nonEmptyCanvas = true; break; }
                        }
                    }
                    return {
                        id: idMeta?.id,
                        topSigilOk: Boolean(topSvg && topSvg.dataset.brandVariant === 'sigil'),
                        variantOk,
                        lockupOk: Boolean(lockup),
                        nonEmptyCanvas,
                    };
                }"""
            )
            page.locator('#brand-tone-seg button[data-tone="tridomain"]').click()
            page.wait_for_timeout(60)
            brand_tridomain = page.evaluate(
                """() => {
                    const mark = document.querySelector('#brand-identity-mount svg[data-brand-variant="mark"]');
                    const wedges = [...(mark?.querySelectorAll('.brand-wedge') || [])].map(p => p.getAttribute('fill'));
                    return { tone: mark?.dataset?.brandTone, wedges };
                }"""
            )
            page.locator('#brand-tone-seg button[data-tone="a3"]').click()
            page.wait_for_timeout(40)
            ok_brand = (
                brand_before["id"] == "bio-purge-triad-shield"
                and brand_before["topSigilOk"]
                and all(brand_before["variantOk"].values())
                and brand_before["lockupOk"]
                and brand_before["nonEmptyCanvas"]
                and brand_tridomain["tone"] == "tridomain"
                and len(set(brand_tridomain["wedges"])) == 3
            )
            record("品牌标识生化肃清三楔盾4档光学尺码与三域切换及Path2D实绘", ok_brand, {"before": brand_before, "tridomain": brand_tridomain})

            # 4.2 Section 08.2 全页统一方案 B 翻页命令栏交互与切页动效校验（攻击 -> 机动 -> 调试 -> 攻击）
            paged_snap = """() => {
                const grid = document.getElementById('v22-paged-cmd-grid');
                const pager = document.getElementById('v22-cmd-pager');
                const cards = [...document.querySelectorAll('#v22-paged-cmd-grid .ui-cmd-wep-card')];
                const utils = [...document.querySelectorAll('#v22-paged-cmd-grid .ui-cmd-util-btn')];
                return {
                    isPartitioned: Boolean(grid?.classList.contains('is-partitioned')),
                    gridHeight: Math.round((grid?.getBoundingClientRect().height || 0) * 10) / 10,
                    cardCount: cards.length,
                    wepIconKinds: cards.map(c => c.querySelector('svg[data-weapon-icon]')?.dataset?.weaponIcon || ''),
                    cardHudIcons: cards.map(c => c.querySelector('svg[data-hud-icon]')?.dataset?.hudIcon || ''),
                    hasDangerT: cards.some(c => c.dataset.cmdKey === 'T' && c.classList.contains('is-danger-cmd')),
                    utilCount: utils.length,
                    utilHudIcons: utils.map(u => u.querySelector('svg[data-hud-icon]')?.dataset?.hudIcon || ''),
                    pagerHasIndicator: Boolean(pager?.classList.contains('has-indicator') && pager?.querySelector('.ui-cmd-pager-indicator')),
                    pagerIndex: pager?.style?.getPropertyValue('--pager-index')?.trim() || '',
                    pagerTone: pager?.dataset?.activeTone || '',
                    pageDir: grid?.dataset?.cmdPageDir || '',
                };
            }"""
            paged_atk = page.evaluate(paged_snap)
            page.locator('#v22-cmd-pager .ui-cmd-pager-tab[data-page="move"]').click()
            page.wait_for_timeout(80)
            paged_move = page.evaluate(paged_snap)
            page.locator('#v22-cmd-pager .ui-cmd-pager-tab[data-page="debug"]').click()
            page.wait_for_timeout(80)
            paged_debug = page.evaluate(paged_snap)
            page.locator('#v22-cmd-pager .ui-cmd-pager-tab[data-page="attack"]').click()
            page.wait_for_timeout(300)
            paged_back = page.evaluate(paged_snap)
            ok_paged_b = (
                paged_atk["isPartitioned"] is True
                and paged_atk["cardCount"] == 4
                and paged_atk["wepIconKinds"] == ["cannon", "rocket", "missile", "bomb"]
                and paged_atk["utilCount"] == 2
                and paged_atk["utilHudIcons"] == ["flare", "altitude"]
                and paged_atk["pagerHasIndicator"] is True
                and paged_atk["pagerIndex"] == "0"
                and paged_move["isPartitioned"] is True
                and paged_move["cardCount"] == 4
                and paged_move["cardHudIcons"] == ["sprint", "stop", "reset", "evacuate"]
                and paged_move["hasDangerT"] is True
                and paged_move["utilCount"] == 2
                and paged_move["utilHudIcons"] == ["focus", "follow"]
                and paged_move["pagerIndex"] == "1"
                and paged_move["pageDir"] == "down"
                and paged_debug["isPartitioned"] is True
                and paged_debug["cardCount"] == 4
                and paged_debug["pagerIndex"] == "2"
                and paged_debug["pagerTone"] == "plane"
                and paged_debug["pageDir"] == "down"
                and paged_back["pagerIndex"] == "0"
                and paged_back["pageDir"] == "up"
                and abs(paged_atk["gridHeight"] - paged_move["gridHeight"]) <= 1.0
                and abs(paged_atk["gridHeight"] - paged_debug["gridHeight"]) <= 1.0
            )
            record("V2.2右下翻页命令栏全页统一方案B等高与滑块指示及方向切页动效", ok_paged_b, {"attack": paged_atk, "move": paged_move, "debug": paged_debug, "back": paged_back})

            # 4.2b Section 08.5 跨页共享载具参数详情组件（三落点同源 + 固定翼去血量燃油制与 Bingo 态）
            page.locator('#v22-vd-unit-seg button[data-vd-unit="plane"]').click()
            page.wait_for_timeout(60)
            vd_plane = page.evaluate(
                """() => {
                    const full = document.querySelector('#v22-vd-full-mount .ui-vd-card');
                    const hov = document.querySelector('#v22-vd-hover-mount .ui-vd-hover');
                    const arm = document.querySelector('#v22-vd-armory-mount .ui-vd-card.is-narrow');
                    const lbl = full?.querySelector('.ui-vd-status-lbl')?.textContent || '';
                    return {
                        fullCh: full?.dataset?.channel || '',
                        hovCh: hov?.dataset?.channel || '',
                        armCh: arm?.dataset?.channel || '',
                        hasFuelLabel: lbl.includes('燃油') && !lbl.includes('HP'),
                        hasBingoTick: Boolean(full?.querySelector('.ui-vd-bingo-tick')),
                        wepCount: full?.querySelectorAll('.ui-vd-wep')?.length || 0,
                    };
                }"""
            )
            page.locator('#v22-vd-bingo-btn').click()
            page.wait_for_timeout(60)
            vd_bingo_on = page.evaluate("Boolean(document.querySelector('#v22-vd-full-mount .ui-vd-card.is-bingo'))")
            page.locator('#v22-vd-unit-seg button[data-vd-unit="ground"]').click()
            page.wait_for_timeout(50)
            ok_vd = (
                vd_plane["fullCh"] == "plane"
                and vd_plane["hovCh"] == "plane"
                and vd_plane["armCh"] == "plane"
                and vd_plane["hasFuelLabel"] is True
                and vd_plane["hasBingoTick"] is True
                and vd_plane["wepCount"] == 4
                and vd_bingo_on is True
            )
            record("跨页共享载具详情卡与悬停精简浮卡（三落点同源+固定翼燃油Bingo态）", ok_vd, {"plane": vd_plane, "bingoOn": vd_bingo_on})

            # 4.3 方案 A 连续八扇区 8 槽战术轮盘同源渲染与兵种/四态切换校验
            page.locator("#v22-wheel").scroll_into_view_if_needed()
            page.wait_for_timeout(80)
            wheel_ground = page.evaluate("window.uiSpec.getWheelMetrics()")
            page.locator('#v22-wheel-unit-seg button[data-unit="plane"]').click()
            page.locator('#v22-wheel-state-seg button[data-wstate="cooling"]').click()
            page.wait_for_timeout(60)
            wheel_plane_cd = page.evaluate("window.uiSpec.getWheelMetrics()")
            page.locator('#v22-wheel-state-seg button[data-wstate="disabled"]').click()
            page.wait_for_timeout(60)
            wheel_plane_dis = page.evaluate("window.uiSpec.getWheelMetrics()")
            page.locator('#v22-wheel-unit-seg button[data-unit="mixed"]').click()
            page.locator('#v22-wheel-state-seg button[data-wstate="normal"]').click()
            page.wait_for_timeout(60)
            wheel_mixed = page.evaluate("window.uiSpec.getWheelMetrics()")
            page.locator('#v22-wheel-unit-seg button[data-unit="ground"]').click()
            page.wait_for_timeout(40)
            ok_wheel = (
                wheel_ground["hasSvg"]
                and wheel_ground["slotCount"] == 8
                and wheel_ground["activeCount"] >= 1
                and wheel_ground["emptyCount"] == 2
                and wheel_ground["hasCenter"]
                and wheel_ground["clampCornerOk"]
                and wheel_plane_cd["emptyCount"] == 0
                and wheel_plane_cd["coolingCount"] >= 1
                and wheel_plane_dis["disabledCount"] >= 1
                and wheel_mixed["emptyCount"] == 4
            )
            record(
                "方案A连续八扇区战术轮盘同源渲染及兵种四态与边角内推校验",
                ok_wheel,
                {"ground": wheel_ground, "plane_cd": wheel_plane_cd, "plane_dis": wheel_plane_dis, "mixed": wheel_mixed},
            )

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

            # 6. 680p 双层分离高清沙盒与固定翼无地面双环验证
            page.locator("#sec-sandbox").scroll_into_view_if_needed()
            page.wait_for_timeout(120)
            before_sb = page.evaluate("window.uiSpec.getSandboxMetrics()")
            page.locator("#sandbox-pixel-toggle").click()
            page.wait_for_timeout(120)
            after_sb = page.evaluate("window.uiSpec.getSandboxMetrics()")
            dual_layer_ok = (
                not before_sb["pixelSim"]
                and after_sb["pixelSim"]
                and after_sb["groundCanvasHeight"] == 680
                and after_sb["vehicleCanvasHeight"] == 680
                and "sepia" in (after_sb.get("groundFilter") or "")
                and after_sb["airCanvasWidth"] == after_sb["stageCssWidth"] * 2
                and not before_sb["planeRingVisible"]
                and not after_sb["planeRingVisible"]
            )
            record("680p暖滤镜模拟下UI标绘层保持原生DPR高清且固定翼无地面双环", dual_layer_ok, {"before": before_sb, "after": after_sb})
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
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="活体规范台所在服务根地址（默认 8800 主端口）")
    parser.add_argument("--channel", choices=["chrome", "msedge", "both"], default="both")
    parser.add_argument("--out-base", default=str(OUTPUT_ROOT), help="输出根目录")
    parser.add_argument("--run", required=True, help="本轮独立输出目录名（禁止覆盖既有目录）")
    args = parser.parse_args()
    if Path(args.run).name != args.run or not args.run:
        raise ValueError("--run 只接受单个目录名")

    out_base = Path(args.out_base)
    if not out_base.is_absolute():
        out_base = ROOT / out_base
    run_root = out_base / args.run
    if run_root.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{run_root.relative_to(ROOT).as_posix()}")

    expected_sha = compute_expected_source_sha()
    base_url = args.base_url.rstrip("/")
    channels = ["chrome", "msedge"] if args.channel == "both" else [args.channel]
    summary = {"run": args.run, "base_url": base_url, "expected_source_sha256": expected_sha, "channels": {}}
    for ch in channels:
        report = run_browser(ch, run_root / ch, expected_sha, base_url)
        summary["channels"][ch] = {"passed": report["passed"], "checks": len(report["checks"])}

    (run_root / "汇总摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
