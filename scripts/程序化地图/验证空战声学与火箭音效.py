"""在唯一正式端口 127.0.0.1:8800 上使用 Edge 无头浏览器全面验证战场物理声学传播总线、GAU-8/A V2 与 Hydra 70 R1。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:8800"
DEFAULT_OUT_DIR = ROOT / "tmp/vfx-explosion-v001-7e8ac44c"

AUDIO_PROBE_INIT_SCRIPT = r"""
(() => {
  const probe = {
    audioContextCreations: 0,
    convolversCreated: 0,
    biquadFiltersCreated: 0,
    bufferSourcesCreated: 0,
    gainNodesCreated: 0,
    stereoPannersCreated: 0,
  };
  window.__AUDIO_PROBE__ = probe;
  const OrigAC = window.AudioContext || window.webkitAudioContext;
  if (!OrigAC) return;

  function patchProto(AC) {
    const proto = AC.prototype;
    if (proto.__scvPatched) return;
    proto.__scvPatched = true;

    const origConv = proto.createConvolver;
    proto.createConvolver = function (...args) {
      probe.convolversCreated += 1;
      return origConv.apply(this, args);
    };

    const origFilter = proto.createBiquadFilter;
    proto.createBiquadFilter = function (...args) {
      probe.biquadFiltersCreated += 1;
      return origFilter.apply(this, args);
    };

    const origSrc = proto.createBufferSource;
    proto.createBufferSource = function (...args) {
      probe.bufferSourcesCreated += 1;
      return origSrc.apply(this, args);
    };

    const origGain = proto.createGain;
    proto.createGain = function (...args) {
      probe.gainNodesCreated += 1;
      return origGain.apply(this, args);
    };

    if (typeof proto.createStereoPanner === 'function') {
      const origPan = proto.createStereoPanner;
      proto.createStereoPanner = function (...args) {
        probe.stereoPannersCreated += 1;
        return origPan.apply(this, args);
      };
    }
  }

  patchProto(OrigAC);
  const WrappedAC = function (...args) {
    probe.audioContextCreations += 1;
    return new OrigAC(...args);
  };
  WrappedAC.prototype = OrigAC.prototype;
  Object.setPrototypeOf(WrappedAC, OrigAC);
  if (window.AudioContext) window.AudioContext = WrappedAC;
  if (window.webkitAudioContext) window.webkitAudioContext = WrappedAC;
})();
"""


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


def run_verification(base_url: str, out_dir: Path, channel: str = "msedge") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    server_proc = ensure_main_server(base_url)

    report = {
        "baseUrl": base_url,
        "channel": channel,
        "checks": [],
        "errors": [],
        "mediaResponses": [],
        "passed": False,
    }

    def record(name: str, ok: bool, detail: object) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {json.dumps(detail, ensure_ascii=False)}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel=channel,
                headless=True,
                args=[
                    "--enable-webgl",
                    "--ignore-gpu-blocklist",
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                    "--autoplay-policy=no-user-gesture-required",
                ],
            )
            try:
                # ==================== 1. 试听台零回归验证 ====================
                ctx_ab = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
                page_ab = ctx_ab.new_page()
                ab_errors: list[str] = []
                ab_media: list[dict] = []
                page_ab.on("pageerror", lambda err: ab_errors.append(f"ab_pageerror: {err}"))
                page_ab.on("console", lambda msg: ab_errors.append(f"ab_console: {msg.text}") if msg.type == "error" else None)
                page_ab.on("response", lambda res: ab_media.append({"url": res.url, "status": res.status}) if "/media/" in res.url else None)

                page_ab.goto(f"{base_url}/dev/audio/空战音效对比试听.html", wait_until="domcontentloaded", timeout=30000)
                page_ab.wait_for_function("() => Boolean(window.__airAudioAB && window.__airAudioAB.v2Ready === true)", timeout=15000)
                page_ab.click("#v2PlayCurrentBtn")
                page_ab.wait_for_timeout(450)
                page_ab.eval_on_selector("#v2DistSlider", "el => { el.value = '1250'; el.dispatchEvent(new Event('input')); }")
                page_ab.wait_for_timeout(250)
                page_ab.click("#stopAllBtn")
                ab_state = page_ab.evaluate("() => window.__airAudioAB")
                ab_all_200 = all(m["status"] == 200 for m in ab_media) and len(ab_media) >= 2
                record(
                    "试听台V2兼容与媒体HTTP200",
                    ab_state.get("v2Ready") is True and ab_state.get("catalogCount") == 13 and len(ab_errors) == 0 and ab_all_200,
                    {"abState": ab_state, "mediaCount": len(ab_media), "all200": ab_all_200, "errors": ab_errors},
                )
                report["errors"].extend(ab_errors)
                report["mediaResponses"].extend(ab_media)
                ctx_ab.close()

                GL_BLACK_FRAME_PROBE_INIT_SCRIPT = r"""
(() => {
  if (window.__SCV_GL_PROBE__) return;
  const probe = window.__SCV_GL_PROBE__ = {
    contexts: 0,
    mainCanvas: null,
    mainGl: null,
    running: false,
    frames: 0,
    blackFrames: 0,
    unboundFboFrames: 0,
    glErrors: [],
    minAvgLuma: 255,
    maxSeenLuma: 0,
    bannerShowCount: 0,
    _sampleCanvas: null,
    _sampleCtx: null,
    _observer: null,
    _rafId: 0,
    reset() {
      this.frames = 0;
      this.blackFrames = 0;
      this.unboundFboFrames = 0;
      this.glErrors.length = 0;
      this.minAvgLuma = 255;
      this.maxSeenLuma = 0;
      this.bannerShowCount = 0;
    },
    start() {
      if (this.running) return;
      this.running = true;
      if (!this._sampleCanvas) {
        this._sampleCanvas = document.createElement('canvas');
        this._sampleCanvas.width = 16;
        this._sampleCanvas.height = 9;
        this._sampleCtx = this._sampleCanvas.getContext('2d', { willReadFrequently: true });
      }
      const bannerEl = document.getElementById('scv-mode-banner');
      if (bannerEl && !this._observer) {
        const recordBanner = () => {
          if (!bannerEl.hidden && getComputedStyle(bannerEl).display !== 'none') {
            probe.bannerShowCount += 1;
          }
        };
        this._observer = new MutationObserver(recordBanner);
        this._observer.observe(bannerEl, { attributes: true, attributeFilter: ['hidden', 'class', 'style'] });
      }
      const step = () => {
        if (!this.running) return;
        const canvas = this.mainCanvas || document.querySelector('#canvas-host > canvas');
        const gl = this.mainGl;
        if (canvas && canvas.width > 0 && canvas.height > 0 && this._sampleCtx) {
          this.frames += 1;
          try {
            this._sampleCtx.clearRect(0, 0, 16, 9);
            this._sampleCtx.drawImage(canvas, 0, 0, 16, 9);
            const data = this._sampleCtx.getImageData(0, 0, 16, 9).data;
            let sum = 0;
            let maxPx = 0;
            const n = data.length / 4;
            for (let i = 0; i < data.length; i += 4) {
              const l = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
              sum += l;
              if (l > maxPx) maxPx = l;
            }
            const avg = n > 0 ? sum / n : 0;
            if (avg < this.minAvgLuma) this.minAvgLuma = Math.round(avg * 100) / 100;
            if (maxPx > this.maxSeenLuma) this.maxSeenLuma = Math.round(maxPx * 100) / 100;
            if (avg < 1.5 && maxPx < 3) {
              this.blackFrames += 1;
            }
          } catch (_) {}
          if (gl) {
            try {
              const err = gl.getError();
              if (err && err !== gl.NO_ERROR) {
                this.glErrors.push(err);
              }
              const fb = gl.getParameter(gl.FRAMEBUFFER_BINDING);
              if (fb !== null) {
                this.unboundFboFrames += 1;
              }
            } catch (_) {}
          }
        }
        this._rafId = requestAnimationFrame(step);
      };
      this._rafId = requestAnimationFrame(step);
    },
    stop() {
      this.running = false;
      if (this._rafId) {
        cancelAnimationFrame(this._rafId);
        this._rafId = 0;
      }
      if (this._observer) {
        this._observer.disconnect();
        this._observer = null;
      }
      const bannerEl = document.getElementById('scv-mode-banner');
      return {
        contexts: this.contexts,
        frames: this.frames,
        blackFrames: this.blackFrames,
        unboundFboFrames: this.unboundFboFrames,
        glErrors: [...this.glErrors],
        minAvgLuma: this.minAvgLuma,
        maxSeenLuma: this.maxSeenLuma,
        bannerShowCount: this.bannerShowCount,
        bannerHiddenNow: Boolean(bannerEl?.hidden),
      };
    },
  };
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, attrs) {
    const ctx = origGetContext.apply(this, arguments);
    if (ctx && (type === 'webgl2' || type === 'webgl' || type === 'experimental-webgl') && !probe.mainGl) {
      probe.mainCanvas = this;
      probe.mainGl = ctx;
      probe.contexts += 1;
    }
    return ctx;
  };
})();
"""
                # ==================== 2. Air-Lab 全链路物理声学与火箭齐射验证 ====================
                ctx = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
                ctx.add_init_script(AUDIO_PROBE_INIT_SCRIPT)
                ctx.add_init_script(GL_BLACK_FRAME_PROBE_INIT_SCRIPT)

                # ==================== 2a. Air-Lab 右键点击目的地防黑屏闪烁与短按免横幅验证 ====================
                page_rclick = ctx.new_page()
                rclick_errors: list[str] = []
                page_rclick.on("pageerror", lambda err: rclick_errors.append(f"rclick_pageerror: {err}"))
                page_rclick.on("console", lambda msg: rclick_errors.append(f"rclick_console: {msg.text}") if msg.type == "error" else None)
                page_rclick.goto(f"{base_url}/play?mode=air-lab&unit=US_A10", wait_until="domcontentloaded", timeout=30000)
                page_rclick.wait_for_function(
                    "() => Boolean(window.__SCAVENGER_AIR_LAB__ && window.__SCV_MAP__?.getPipCamState?.()?.offscreenIsolated && window.__SCV_GL_PROBE__?.mainGl)",
                    timeout=30000,
                )
                page_rclick.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                page_rclick.evaluate("() => { window.__SCV_GL_PROBE__.reset(); window.__SCV_GL_PROBE__.start(); }")
                rclick_targets = [
                    (560, 420), (740, 380), (920, 460), (650, 510),
                    (830, 340), (1020, 410), (520, 490), (880, 530),
                    (690, 360), (960, 480), (610, 440), (790, 395),
                ]
                for rx, ry in rclick_targets:
                    page_rclick.evaluate("() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)))")
                    page_rclick.mouse.click(rx, ry, button="right", delay=25)
                page_rclick.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                short_click_probe = page_rclick.evaluate("() => window.__SCV_GL_PROBE__.stop()")

                page_rclick.evaluate("() => { window.__SCV_GL_PROBE__.reset(); window.__SCV_GL_PROBE__.start(); }")
                page_rclick.locator('#hud-cmd-grid .ui-cmd-wep-card').first.click()
                page_rclick.mouse.click(780, 420, button="left")
                page_rclick.evaluate("() => new Promise(r => requestAnimationFrame(r))")
                page_rclick.mouse.click(860, 450, button="right", delay=25)
                page_rclick.mouse.move(740, 420)
                page_rclick.mouse.down(button="right")
                page_rclick.wait_for_timeout(160)
                page_rclick.mouse.move(810, 455, steps=6)
                page_rclick.wait_for_timeout(60)
                page_rclick.mouse.up(button="right")
                page_rclick.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                drag_interrupt_probe = page_rclick.evaluate("() => window.__SCV_GL_PROBE__.stop()")
                pip_rclick_state = page_rclick.evaluate("() => window.__SCV_MAP__?.getPipCamState?.() || null")
                rclick_ok = (
                    len(rclick_errors) == 0
                    and short_click_probe.get("frames", 0) >= 6
                    and short_click_probe.get("blackFrames", 1) == 0
                    and short_click_probe.get("unboundFboFrames", 1) == 0
                    and len(short_click_probe.get("glErrors", [1])) == 0
                    and short_click_probe.get("bannerShowCount", 1) == 0
                    and short_click_probe.get("bannerHiddenNow") is True
                    and drag_interrupt_probe.get("frames", 0) >= 4
                    and drag_interrupt_probe.get("blackFrames", 1) == 0
                    and drag_interrupt_probe.get("unboundFboFrames", 1) == 0
                    and len(drag_interrupt_probe.get("glErrors", [1])) == 0
                    and drag_interrupt_probe.get("bannerHiddenNow") is True
                    and bool(pip_rclick_state and pip_rclick_state.get("offscreenIsolated") is True)
                )
                record(
                    "Air-Lab右键点击目的地防黑屏闪烁与短按免横幅",
                    rclick_ok,
                    {
                        "errors": rclick_errors,
                        "shortClickProbe": short_click_probe,
                        "dragInterruptProbe": drag_interrupt_probe,
                        "pipState": pip_rclick_state,
                    },
                )
                page_rclick.close()

                page = ctx.new_page()
                page_errors: list[str] = []
                lab_media: list[dict] = []
                page.on("pageerror", lambda err: page_errors.append(f"lab_pageerror: {err}"))
                page.on("console", lambda msg: page_errors.append(f"lab_console: {msg.text}") if msg.type == "error" else None)
                page.on("response", lambda res: lab_media.append({"url": res.url, "status": res.status}) if "/media/" in res.url else None)

                page.goto(f"{base_url}/play?mode=air-lab&unit=US_A10", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_function(
                    """() => Boolean(
                        window.__SCV_MAP__
                        && window.__SCV_MAP__.isCoreReady() === true
                        && window.__SCAVENGER_AIR_LAB__
                        && window.__SCAVENGER_AIR_LAB__.getVfxStats().atlasReady === true
                    )""",
                    timeout=65000,
                )

                # 真实用户手势解锁音频上下文
                page.mouse.click(800, 450)
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('focus-range')")
                page.wait_for_function(
                    """() => {
                        const t = window.__SCAVENGER_AIR_LAB__?.getAudioEventsTelemetry?.();
                        return Boolean(t && t.unlocked === true && t.decodedKeysCount >= 16);
                    }""",
                    timeout=25000,
                )

                # 校验所需 5 个核心 .ogg URL 是否均返回 HTTP 200
                required_media_hashes = [
                    "93d08dc607473a4c0e25096c03d48a2c93b6b945b5238f700424892468a96428.ogg",  # GSH30_RU
                    "b30b3c0a7f08ca8ea444aed092a6e38db2ec9b0f4b23297a02926a3c041b2105.ogg",  # GAU12_US
                    "0863ed9dc9cf18bcd8fccf8716fd016b0e4738862c85d08051a8bf9ef121bf84.ogg",  # SupFAST_HE
                    "358aed471d8072e31989b112bd1211d96f7b3d099e2ffcee71281eb73b693a6f.ogg",  # ROCKETPOD_Small
                    "82495050ef20941bc82950d42606facdb1be4332c576b0b47aede5b9c9fd52b3.ogg",  # ROCKETPOD_62-77
                ]
                ok_200_urls = {m["url"].split("/")[-1]: m["status"] for m in lab_media}
                all_required_200 = all(ok_200_urls.get(h) == 200 for h in required_media_hashes)
                all_media_200 = all(m["status"] == 200 for m in lab_media)
                record(
                    "核心.ogg音轨加载HTTP200",
                    all_required_200 and all_media_200,
                    {"requiredStatuses": {h[:12]: ok_200_urls.get(h) for h in required_media_hashes}, "totalMedia": len(lab_media)},
                )

                # 校验全局唯一 AudioContext、唯一共享 ConvolverNode 与三档分桶配置
                spatial_init = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getSpatialAudioState()")
                probe_init = page.evaluate("() => window.__AUDIO_PROBE__")
                buckets = (spatial_init or {}).get("bucketConfigs") or {}
                bus_ok = (
                    probe_init.get("audioContextCreations") == 1
                    and probe_init.get("convolversCreated") == 1
                    and spatial_init.get("busInitialized") is True
                    and spatial_init.get("convolverCount") == 1
                    and spatial_init.get("panLanesPerBucket") == 5
                    and spatial_init.get("subPunchRoute") == "dry_direct"
                    and spatial_init.get("engineDuckReady") is True
                    and buckets.get("near", {}).get("lpfHz") == 8000
                    and buckets.get("mid", {}).get("lpfHz") == 2400
                    and buckets.get("far", {}).get("lpfHz") == 520
                )
                record(
                    "全局唯一AudioContext与共享Convolver及三档分桶与静态声像干声SubPunch与闪避母线",
                    bus_ok,
                    {
                        "probe": probe_init,
                        "spatialBus": spatial_init.get("bucketConfigs"),
                        "convolverCount": spatial_init.get("convolverCount"),
                        "panLanesPerBucket": spatial_init.get("panLanesPerBucket"),
                        "subPunchRoute": spatial_init.get("subPunchRoute"),
                        "engineDuckReady": spatial_init.get("engineDuckReady"),
                    },
                )

                # 校验 A-10C gunAcousticPreset、武器双层命名、盘旋内倾滚转、初始高程一致与独立调试面板
                weapons = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getWeaponsState()")
                gau8_w = next((w for w in weapons if w.get("id") == "gau8_30mm"), None)
                preset = (gau8_w or {}).get("gunAcousticPreset") or {}
                record(
                    "A10_gunAcousticPreset参数化配置就位",
                    preset.get("rpm") == 3970 and preset.get("caliberMm") == 30 and preset.get("barrelCount") == 7,
                    preset,
                )

                ui_flight_check = page.evaluate(
                    """() => {
                        const lab = window.__SCAVENGER_AIR_LAB__;
                        const summary = lab.getUnitSummary();
                        const gridEl = document.getElementById('hud-cmd-grid');
                        const pagerEl = document.getElementById('hud-cmd-pager');
                        const pagerTabs = [...document.querySelectorAll('#hud-cmd-pager button[data-cmd-page]')].map(b => b.dataset.cmdPage);
                        const weaponCells = [...document.querySelectorAll('#hud-cmd-grid button[data-airlab-weapon-id]')].map(b => {
                            const svg = b.querySelector('svg[data-weapon-icon]');
                            const r = svg?.getBoundingClientRect();
                            return {
                                id: b.getAttribute('data-airlab-weapon-id'),
                                model: b.querySelector('.c-txt')?.textContent?.trim() || '',
                                category: b.querySelector('.c-subtxt')?.textContent?.trim() || '',
                                weaponIcon: svg?.dataset?.weaponIcon || '',
                                iconW: Math.round((r?.width || 0) * 10) / 10,
                                iconH: Math.round((r?.height || 0) * 10) / 10,
                                isWepCard: b.classList.contains('ui-cmd-wep-card'),
                            };
                        });
                        const utilBtns = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-util-bar .ui-cmd-util-btn')].map(b => ({
                            key: b.dataset.cmdKey || '',
                            txt: b.querySelector('.c-txt')?.textContent?.trim() || '',
                            hudIcon: b.querySelector('svg[data-hud-icon]')?.dataset?.hudIcon || '',
                        }));
                        const rightDock = document.getElementById('hud-inspector');
                        const deckDock = document.getElementById('hud-card-deck');
                        const rightRect = rightDock?.getBoundingClientRect();
                        const deckRect = deckDock?.getBoundingClientRect();
                        const dockOverlap = Boolean(
                            rightRect && deckRect && rightRect.left < deckRect.right - 1
                        );
                        const dockInViewport = Boolean(
                            rightRect && rightRect.right <= window.innerWidth + 1 && rightRect.bottom <= window.innerHeight + 1
                        );
                        const debugPanel = document.getElementById('airlab-debug-panel');
                        const debugBtnCount = debugPanel ? debugPanel.querySelectorAll('[data-airlab-action]').length : 0;
                        const toggleBtn = document.getElementById('btn-airlab-collapse');
                        let collapsedOk = false;
                        if (debugPanel && toggleBtn) {
                            toggleBtn.click();
                            const isCol = debugPanel.classList.contains('is-collapsed');
                            toggleBtn.click();
                            const isExp = !debugPanel.classList.contains('is-collapsed');
                            collapsedOk = isCol && isExp;
                        }
                        return {
                            flightState: summary?.flightState,
                            altY: Math.round(Array.isArray(summary?.position) ? summary.position[1] : 0),
                            inspAltText: document.getElementById('insp-alt')?.textContent?.trim() || '',
                            highAltM: summary?.gameStats?.highAltitudeM || 215,
                            rollRad: summary?.anim?.roll,
                            bankDeg: summary?.anim?.bankDeg,
                            yawRate: summary?.anim?.yawRate,
                            pagerTabs,
                            pagerHasIndicator: Boolean(pagerEl?.classList.contains('has-indicator') && pagerEl?.querySelector('.ui-cmd-pager-indicator')),
                            pagerIndex: pagerEl?.style?.getPropertyValue('--pager-index')?.trim() || '',
                            isPartitioned: Boolean(gridEl?.classList.contains('is-partitioned')),
                            attackGridH: Math.round((gridEl?.getBoundingClientRect().height || 0) * 10) / 10,
                            attackDockH: Math.round((rightRect?.height || 0) * 10) / 10,
                            weaponCells,
                            utilBtns,
                            dockOverlap,
                            dockInViewport,
                            hasFloatingDebugPanel: Boolean(debugPanel),
                            debugBtnCount,
                            collapsedOk,
                        };
                    }"""
                )
                right_dock_loc = page.locator("#hud-inspector")
                if right_dock_loc.count() > 0:
                    right_dock_loc.screenshot(path=str(out_dir / "cmd_panel_scheme_b_attack.png"))
                page.click('#hud-cmd-pager button[data-cmd-page="move"]')
                page.wait_for_timeout(300)
                move_page_check = page.evaluate(
                    """() => {
                        const gridEl = document.getElementById('hud-cmd-grid');
                        const pagerEl = document.getElementById('hud-cmd-pager');
                        const rightDock = document.getElementById('hud-inspector');
                        const roeEl = document.getElementById('hud-roe-select');
                        const badgeEl = document.getElementById('hud-fw-manual-badge');
                        const cards = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card')];
                        const utils = [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-util-btn')];
                        const summary = window.__SCAVENGER_AIR_LAB__?.getUnitSummary?.() || null;
                        const toggleActionRes = window.__SCAVENGER_AIR_LAB__?.executeAction?.('toggle-autofire') || null;
                        const pipState = window.__SCV_MAP__?.getPipCamState?.() || null;
                        const hudState = window.__SCV_MAP__?.getUiHudState?.() || null;
                        return {
                            isPartitioned: Boolean(gridEl?.classList.contains('is-partitioned')),
                            moveGridH: Math.round((gridEl?.getBoundingClientRect().height || 0) * 10) / 10,
                            moveDockH: Math.round((rightDock?.getBoundingClientRect().height || 0) * 10) / 10,
                            cardCount: cards.length,
                            utilCount: utils.length,
                            cardIcons: cards.map(c => c.querySelector('svg[data-hud-icon]')?.dataset?.hudIcon || ''),
                            utilIcons: utils.map(u => u.querySelector('svg[data-hud-icon]')?.dataset?.hudIcon || ''),
                            hasDangerT: cards.some(c => c.dataset.cmdKey === 'T' && c.classList.contains('is-danger-cmd')),
                            hasAutofireText: cards.some(c => /自动开火/.test(c.textContent || '')),
                            roeSelectVisible: Boolean(roeEl && !roeEl.hidden && getComputedStyle(roeEl).display !== 'none'),
                            manualBadgeVisible: Boolean(badgeEl && !badgeEl.hidden && getComputedStyle(badgeEl).display !== 'none'),
                            manualBadgeHeight: Math.round((badgeEl?.getBoundingClientRect().height || 0) * 10) / 10,
                            summaryManual: summary ? { orbitAutoFire: summary.orbitAutoFire, manualFireOnly: summary.manualFireOnly } : null,
                            toggleActionRes,
                            pipIsolated: Boolean(pipState?.offscreenIsolated),
                            hudState,
                            pageDir: gridEl?.dataset?.cmdPageDir || '',
                            pagerIndex: pagerEl?.style?.getPropertyValue('--pager-index')?.trim() || '',
                        };
                    }"""
                )
                if right_dock_loc.count() > 0:
                    right_dock_loc.screenshot(path=str(out_dir / "cmd_panel_scheme_b_move.png"))
                page.click('#hud-cmd-pager button[data-cmd-page="attack"]')
                page.wait_for_timeout(300)
                back_page_dir = page.evaluate("() => ({ pageDir: document.getElementById('hud-cmd-grid')?.dataset?.cmdPageDir || '', pagerIndex: document.getElementById('hud-cmd-pager')?.style?.getPropertyValue('--pager-index')?.trim() || '' })")
                expected_models = ["GAU-8/A", "Hydra 70", "AGM-65E2", "GBU-31"]
                expected_cats = ["30mm 机炮", "火箭弹", "空地导弹", "制导航弹"]
                expected_wep_icons = ["cannon", "rocket", "missile", "bomb"]
                actual_models = [c["model"] for c in ui_flight_check.get("weaponCells", [])]
                actual_cats = [c["category"] for c in ui_flight_check.get("weaponCells", [])]
                actual_wep_icons = [c["weaponIcon"] for c in ui_flight_check.get("weaponCells", [])]
                actual_util_icons = [u["hudIcon"] for u in ui_flight_check.get("utilBtns", [])]
                move_page_icons = move_page_check.get("cardIcons", []) + move_page_check.get("utilIcons", [])
                ui_flight_ok = (
                    ui_flight_check.get("rollRad", 0) > 0.25
                    and -42 <= ui_flight_check.get("bankDeg", 0) <= -18
                    and abs(ui_flight_check.get("altY", 0) - ui_flight_check.get("highAltM", 215)) <= 3
                    and ui_flight_check.get("pagerTabs") == ["attack", "move"]
                    and ui_flight_check.get("pagerHasIndicator") is True
                    and ui_flight_check.get("pagerIndex") == "0"
                    and ui_flight_check.get("isPartitioned") is True
                    and ui_flight_check.get("dockOverlap") is False
                    and ui_flight_check.get("dockInViewport") is True
                    and actual_models == expected_models
                    and actual_cats == expected_cats
                    and actual_wep_icons == expected_wep_icons
                    and all(c.get("isWepCard") and c.get("iconW", 0) >= 20 for c in ui_flight_check.get("weaponCells", []))
                    and actual_util_icons == ["flare", "altitude"]
                    and move_page_check.get("isPartitioned") is True
                    and move_page_check.get("cardCount") == 4
                    and move_page_check.get("utilCount") == 2
                    and move_page_icons == ["sprint", "stop", "reset", "evacuate", "focus", "follow"]
                    and move_page_check.get("hasDangerT") is True
                    and move_page_check.get("hasAutofireText") is False
                    and move_page_check.get("roeSelectVisible") is False
                    and move_page_check.get("manualBadgeVisible") is True
                    and 22 <= move_page_check.get("manualBadgeHeight", 0) <= 32
                    and (move_page_check.get("summaryManual") or {}).get("orbitAutoFire") is False
                    and (move_page_check.get("summaryManual") or {}).get("manualFireOnly") is True
                    and (move_page_check.get("toggleActionRes") or {}).get("ok") is False
                    and (move_page_check.get("toggleActionRes") or {}).get("orbitAutoFire") is False
                    and (move_page_check.get("toggleActionRes") or {}).get("manualOnly") is True
                    and move_page_check.get("pipIsolated") is True
                    and move_page_check.get("pageDir") == "down"
                    and move_page_check.get("pagerIndex") == "1"
                    and back_page_dir.get("pageDir") == "up"
                    and back_page_dir.get("pagerIndex") == "0"
                    and abs(ui_flight_check.get("attackGridH", 0) - move_page_check.get("moveGridH", 999)) <= 1.0
                    and abs(ui_flight_check.get("attackDockH", 0) - move_page_check.get("moveDockH", 999)) <= 1.0
                    and ui_flight_check.get("hasFloatingDebugPanel") is True
                    and ui_flight_check.get("debugBtnCount", 0) >= 13
                    and ui_flight_check.get("collapsedOk") is True
                )
                ui_flight_check["movePageCheck"] = move_page_check
                ui_flight_check["backPageDir"] = back_page_dir
                record("盘旋内倾滚转与高程一致及方案1全页等高与切页动效及专属矢量图标", ui_flight_ok, ui_flight_check)

                # 校验 GAU-8 V2 实时扫射、距离/多普勒更新、地面先撕地后雷鸣、停火拖尾、Convolver 保持 1
                page.evaluate("() => { window.__SCV_MAP__.setManualLoop(true); window.__SCAVENGER_AIR_LAB__.executeAction('strafe', { fastApproach: true }); }")
                gau8_samples = []
                sim_now = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec")
                for step_idx in range(280):
                    sim_now += 1.0 / 60.0
                    page.evaluate("({t}) => window.__SCV_MAP__.stepSimAndRender(1/60, t, false)", {"t": sim_now})
                    if step_idx % 4 == 0:
                        time.sleep(0.012)
                        tel = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                        if tel.get("activeGau8Details"):
                            gau8_samples.append(tel["activeGau8Details"][0])
                    if step_idx > 140:
                        unit_st = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getUnitSummary().flightState")
                        if unit_st == "egress" and len(gau8_samples) >= 4:
                            break

                time.sleep(0.25)
                tel_after_strafe = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                probe_after_strafe = page.evaluate("() => window.__AUDIO_PROBE__")
                dists = [s["distanceMeters"] for s in gau8_samples]
                lpfs = [s["lpfCutoffHz"] for s in gau8_samples]
                dopplers = [s["dopplerMul"] for s in gau8_samples]
                ground_events = [e for e in tel_after_strafe.get("recentEvents", []) if e.get("key") in ("SFX_Explosion_20-30mm_SupFAST_HE", "GAU8_GROUND_MACH_CRACK")]
                last_snap = tel_after_strafe.get("lastGau8Snapshot") or {}
                strafe_ok = (
                    len(gau8_samples) >= 4
                    and len(set(dists)) >= 2
                    and len(set(lpfs)) >= 2
                    and len(set(dopplers)) >= 2
                    and probe_after_strafe.get("convolversCreated") == 1
                    and tel_after_strafe.get("activeGau8Count") == 0
                    and last_snap.get("stopped") is True
                    and last_snap.get("hasReleaseTail") is True
                    and len(ground_events) >= 1
                    and tel_after_strafe.get("maxObservedPeak", 99) <= 1.0
                )
                record(
                    "GAU-8_V2实时距离多普勒调频与停火拖尾及零削波",
                    strafe_ok,
                    {
                        "sampleCount": len(gau8_samples),
                        "distRange": [min(dists) if dists else None, max(dists) if dists else None],
                        "lpfRange": [min(lpfs) if lpfs else None, max(lpfs) if lpfs else None],
                        "dopplerRange": [min(dopplers) if dopplers else None, max(dopplers) if dopplers else None],
                        "convolversCreated": probe_after_strafe.get("convolversCreated"),
                        "lastSnap": last_snap,
                        "groundEventsCount": len(ground_events),
                        "maxObservedPeak": tel_after_strafe.get("maxObservedPeak"),
                    },
                )

                # 校验 Hydra 70 R1：7 连发 ~110ms 间隔、无 16ms 叠爆、随机音高、1/sqrt(n)、新音轨、单次瞬态 1 Source + 1 Gain
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('reset')")
                time.sleep(0.05)
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('rocket', { fastApproach: true })")
                sim_now = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec")
                rocket_fire_events = []
                rocket_impact_events = []
                # 以真实墙钟同步步进（每帧约 16.7ms），以观测真实的 110ms 火箭发射间隔与落地爆炸
                for step_idx in range(360):
                    sim_now += 1.0 / 60.0
                    page.evaluate("({t}) => window.__SCV_MAP__.stepSimAndRender(1/60, t, false)", {"t": sim_now})
                    time.sleep(0.016)
                    if step_idx % 10 == 0:
                        tel_r = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                        evs = tel_r.get("recentEvents", [])
                        rocket_fire_events = [e for e in evs if e.get("key") == "SFX_GENERIC_ROCKETPOD_Small"]
                        rocket_impact_events = [e for e in evs if e.get("key") == "SFX_Expl_ROCKETPOD_62-77"]
                        if len(rocket_fire_events) >= 7 and len(rocket_impact_events) >= 2 and step_idx > 140:
                            break

                time.sleep(0.20)
                tel_rocket = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                evs_all = tel_rocket.get("recentEvents", [])
                rocket_fire_events = [e for e in evs_all if e.get("key") == "SFX_GENERIC_ROCKETPOD_Small"]
                rocket_impact_events = [e for e in evs_all if e.get("key") == "SFX_Expl_ROCKETPOD_62-77"]
                fire_intervals = [
                    round(rocket_fire_events[i]["scheduledAtMs"] - rocket_fire_events[i - 1]["scheduledAtMs"], 1)
                    for i in range(1, len(rocket_fire_events))
                ]
                fire_rates = [e.get("pitchRate") for e in rocket_fire_events]
                impact_scales = [e.get("concurrencyScale") for e in rocket_impact_events]
                last_nodes = tel_rocket.get("nodeAllocationStats", {}).get("lastEventNodes", {})
                rocket_ok = (
                    len(rocket_fire_events) == 7
                    and all(iv >= 75.0 for iv in fire_intervals)
                    and len(set(fire_rates)) >= 2
                    and all(0.95 <= r <= 1.05 for r in fire_rates)
                    and len(rocket_impact_events) >= 1
                    and any(s < 1.0 for s in impact_scales)
                    and last_nodes == {"bufferSources": 1, "gainNodes": 1, "biquadFilters": 0, "convolvers": 0}
                    and tel_rocket.get("convolverCount") == 1
                    and tel_rocket.get("maxObservedPeak", 99) <= 1.0
                )
                record(
                    "Hydra70_R1_7连发无叠爆与并发保护及节点约束",
                    rocket_ok,
                    {
                        "fireCount": len(rocket_fire_events),
                        "fireIntervalsMs": fire_intervals,
                        "fireRates": fire_rates,
                        "impactCount": len(rocket_impact_events),
                        "impactConcurrencyScales": impact_scales,
                        "lastEventNodes": last_nodes,
                        "maxObservedPeak": tel_rocket.get("maxObservedPeak"),
                    },
                )

                # 验证连续快速重复触发的 40ms 防抖与增量节点计数（复位至靶区附近确保 small 声压处于可听范围）
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('reset')")
                time.sleep(0.05)
                probe_before_single = page.evaluate("() => ({ ...window.__AUDIO_PROBE__ })")
                debounced_res = page.evaluate(
                    """() => {
                        const lab = window.__SCAVENGER_AIR_LAB__;
                        const before = lab.getAudioEventsTelemetry().dedupDroppedCount;
                        // 触发两次间隔 < 40ms 的同类指令，验证烟雾小爆防抖生效
                        lab.executeAction('smoke');
                        lab.executeAction('smoke');
                        const telAfter = lab.getAudioEventsTelemetry();
                        const after = telAfter.dedupDroppedCount;
                        const lastSmoke = [...(telAfter.recentEvents || [])].reverse().find(e => e.key === 'SFX_Expl_MISSILE_AA-A2A-A2G_Small') || null;
                        return { before, after, delta: after - before, lastSmoke, virtualListenerPos: telAfter.virtualListenerPos };
                    }"""
                )
                time.sleep(0.05)
                probe_after_single = page.evaluate("() => ({ ...window.__AUDIO_PROBE__ })")
                delta_src = probe_after_single["bufferSourcesCreated"] - probe_before_single["bufferSourcesCreated"]
                delta_gain = probe_after_single["gainNodesCreated"] - probe_before_single["gainNodesCreated"]
                delta_filter = probe_after_single["biquadFiltersCreated"] - probe_before_single["biquadFiltersCreated"]
                delta_conv = probe_after_single["convolversCreated"] - probe_before_single["convolversCreated"]
                delta_pan = probe_after_single["stereoPannersCreated"] - probe_before_single["stereoPannersCreated"]
                last_smoke = debounced_res.get("lastSmoke") or {}
                record(
                    "40ms防抖与单次瞬态严格1BufferSource+1GainNode+0Panner",
                    debounced_res.get("delta", 0) >= 1
                    and delta_src == 1
                    and delta_gain == 1
                    and delta_filter == 0
                    and delta_conv == 0
                    and delta_pan == 0
                    and last_smoke.get("powerTier") == "small"
                    and isinstance(last_smoke.get("panLane"), int)
                    and 0 <= last_smoke.get("panLane", -1) <= 4,
                    {
                        "debounced": debounced_res,
                        "nodeDelta": {
                            "bufferSources": delta_src,
                            "gainNodes": delta_gain,
                            "biquadFilters": delta_filter,
                            "convolvers": delta_conv,
                            "stereoPanners": delta_pan,
                        },
                    },
                )

                # 校验传播 V2 纯规则：相机旋转声像翻转、声压等级无断崖平滑衰减、虚拟监听点下沉
                prop_v2 = page.evaluate(
                    """async () => {
                        const mod = await import('/src/程序化地图/载具空间音效.mjs');
                        const posRight = { x: 260, y: 20, z: 0 };
                        const refPos = { x: 0, y: 120, z: 0 };
                        const pan0 = mod.computeCameraPan(posRight, refPos, { x: 1, y: 0, z: 0 }, 280);
                        const pan180 = mod.computeCameraPan(posRight, refPos, { x: -1, y: 0, z: 0 }, 280);
                        const smallClose = mod.computeAcousticAttenuation(120, 'small');
                        const smallEdge = mod.computeAcousticAttenuation(740, 'small');
                        const smallFar = mod.computeAcousticAttenuation(820, 'small');
                        const heavy1990 = mod.computeAcousticAttenuation(1990, 'heavy');
                        const heavy2010 = mod.computeAcousticAttenuation(2010, 'heavy');
                        const heavyFar = mod.computeAcousticAttenuation(2500, 'heavy');
                        const vl = mod.computeVirtualListenerPos({ x: 0, y: 300, z: 200 }, { x: 0, y: 0, z: 0 });
                        return {
                            pan0: Number(pan0.toFixed(4)),
                            pan180: Number(pan180.toFixed(4)),
                            smallClose,
                            smallEdge,
                            smallFar,
                            heavy1990,
                            heavy2010,
                            heavyFar,
                            virtualListener: vl,
                        };
                    }"""
                )
                prop_v2_ok = (
                    prop_v2["pan0"] > 0.2
                    and abs(prop_v2["pan0"] + prop_v2["pan180"]) < 1e-3
                    and prop_v2["smallClose"]["distGain"] == 1.0
                    and 0 < prop_v2["smallEdge"]["distGain"] < 0.15
                    and prop_v2["smallFar"]["audible"] is False
                    and prop_v2["smallFar"]["distGain"] == 0.0
                    and prop_v2["heavy1990"]["audible"] is True
                    and prop_v2["heavy2010"]["audible"] is True
                    and abs(prop_v2["heavy1990"]["distGain"] - prop_v2["heavy2010"]["distGain"]) < 0.03
                    and prop_v2["heavyFar"]["audible"] is False
                    and 180 <= prop_v2["virtualListener"]["y"] <= 210
                )
                record("物理传播V2相机右向量声像与声压无断崖衰减及虚拟监听点", prop_v2_ok, prop_v2)

                # 校验现实声学 V3 纯规则：喷气引擎多普勒与前后半球指向性、超远距对数声速延迟延展
                v3_rules = page.evaluate(
                    """async () => {
                        const mod = await import('/src/程序化地图/载具空间音效.mjs');
                        const listener = { x: 0, y: 120, z: 0 };
                        const baseMot = { rate: 1.0, filterHz: 3400 };
                        // 迎头高速飞近监听点
                        const approach = mod.computeJetEngineAeroacoustics({ x: -300, y: 200, z: 0 }, { x: 140, y: -20, z: 0 }, listener, baseMot);
                        // 掠过后背飞远离（尾喷口对着监听点）
                        const recede = mod.computeJetEngineAeroacoustics({ x: 300, y: 200, z: 0 }, { x: 140, y: 20, z: 0 }, listener, baseMot);
                        // 超远距对数声速延迟：500m 线性、1200m & 2000m 持续延展但 <= 2600ms
                        const d500 = mod.computeAudioDelayMs(500, { maxDelayMs: 1650, extendedFarMaxMs: 2600 });
                        const d800 = mod.computeAudioDelayMs(800, { maxDelayMs: 1650, extendedFarMaxMs: 2600 });
                        const d1400 = mod.computeAudioDelayMs(1400, { maxDelayMs: 1650, extendedFarMaxMs: 2600 });
                        const d2400 = mod.computeAudioDelayMs(2400, { maxDelayMs: 1650, extendedFarMaxMs: 2600 });
                        return { approach, recede, delays: { d500, d800, d1400, d2400 } };
                    }"""
                )
                v3_rules_ok = (
                    v3_rules["approach"]["dopplerMul"] > 1.10
                    and v3_rules["recede"]["dopplerMul"] < 0.90
                    and v3_rules["approach"]["filterHz"] > v3_rules["recede"]["filterHz"] + 1500
                    and v3_rules["recede"]["gainMul"] > v3_rules["approach"]["gainMul"] + 0.15
                    and v3_rules["delays"]["d500"] < v3_rules["delays"]["d800"] < v3_rules["delays"]["d1400"] < v3_rules["delays"]["d2400"] <= 2600
                )
                record("现实声学V3引擎多普勒前后指向性与超远距对数延迟", v3_rules_ok, v3_rules)

                # 校验 V3 实机：GBU-31 投弹呼啸随弹体跟踪 + 重爆超压闪避触发 + 坠机动态呼啸推进与释放
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('reset')")
                time.sleep(0.03)
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('bomb', { fastApproach: true })")
                s_now = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec")
                whistle_active_seen = False
                for _ in range(420):
                    s_now += 1.0 / 60.0
                    page.evaluate("({t}) => window.__SCV_MAP__.stepSimAndRender(1/60, t, false)", {"t": s_now})
                    t_b = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                    if t_b.get("activeWhistleCount", 0) >= 1:
                        whistle_active_seen = True
                    if whistle_active_seen and t_b.get("activeWhistleCount", 0) == 0 and t_b.get("duckTriggerCount", 0) >= 1:
                        break
                t_after_bomb = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")

                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('reset')")
                time.sleep(0.03)
                page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('crash')")
                s_now = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec")
                falling_active_seen = False
                for _ in range(360):
                    s_now += 1.0 / 60.0
                    page.evaluate("({t}) => window.__SCV_MAP__.stepSimAndRender(1/60, t, false)", {"t": s_now})
                    t_c = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                    if t_c.get("activeFallingCount", 0) >= 1:
                        falling_active_seen = True
                    st_c = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getUnitSummary().flightState")
                    if st_c == "crashed":
                        break
                t_after_crash = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                v3_live_ok = (
                    whistle_active_seen is True
                    and t_after_bomb.get("whistleStartedCount", 0) >= 1
                    and t_after_bomb.get("whistleUpdateCount", 0) >= 5
                    and t_after_bomb.get("activeWhistleCount", 0) == 0
                    and t_after_bomb.get("duckTriggerCount", 0) >= 1
                    and t_after_bomb.get("lastDuckDepth", 1.0) < 0.95
                    and falling_active_seen is True
                    and t_after_crash.get("fallingUpdateCount", 0) >= 5
                    and t_after_crash.get("activeFallingCount", 0) == 0
                    and t_after_crash.get("maxObservedPeak", 99) <= 1.0
                )
                record(
                    "现实声学V3航弹呼啸跟踪与重爆超压闪避及坠机动态追踪",
                    v3_live_ok,
                    {
                        "whistleActiveSeen": whistle_active_seen,
                        "whistleStartedCount": t_after_bomb.get("whistleStartedCount"),
                        "whistleUpdateCount": t_after_bomb.get("whistleUpdateCount"),
                        "activeWhistleAfterBomb": t_after_bomb.get("activeWhistleCount"),
                        "duckTriggerCount": t_after_bomb.get("duckTriggerCount"),
                        "lastDuckDepth": t_after_bomb.get("lastDuckDepth"),
                        "fallingActiveSeen": falling_active_seen,
                        "fallingUpdateCount": t_after_crash.get("fallingUpdateCount"),
                        "activeFallingAfterCrash": t_after_crash.get("activeFallingCount"),
                        "maxObservedPeak": t_after_crash.get("maxObservedPeak"),
                    },
                )

                # 补充验证：GAU-8 扫射中途直飞（sprint）与切换打击指令（rocket）停火无泄漏
                def _run_strafe_interrupt(interrupt_action: str) -> dict:
                    page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('reset')")
                    time.sleep(0.03)
                    page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('strafe', { fastApproach: true })")
                    s_now = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec")
                    active_seen = False
                    for _ in range(180):
                        s_now += 1.0 / 60.0
                        page.evaluate("({t}) => window.__SCV_MAP__.stepSimAndRender(1/60, t, false)", {"t": s_now})
                        t_mid = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                        if t_mid.get("activeGau8Count", 0) >= 1:
                            active_seen = True
                            break
                    if interrupt_action == "sprint":
                        page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('sprint', { targetPos: { x: 750, y: 2, z: 750 } })")
                    else:
                        page.evaluate("() => window.__SCAVENGER_AIR_LAB__.executeAction('rocket', { fastApproach: false })")
                    t_after = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getAudioEventsTelemetry()")
                    snap_after = t_after.get("lastGau8Snapshot") or {}
                    return {
                        "activeSeen": active_seen,
                        "activeAfter": t_after.get("activeGau8Count"),
                        "stoppedAfter": snap_after.get("stopped"),
                    }

                sprint_int = _run_strafe_interrupt("sprint")
                rocket_int = _run_strafe_interrupt("rocket")
                interrupt_ok = (
                    sprint_int["activeSeen"] is True
                    and sprint_int["activeAfter"] == 0
                    and sprint_int["stoppedAfter"] is True
                    and rocket_int["activeSeen"] is True
                    and rocket_int["activeAfter"] == 0
                    and rocket_int["stoppedAfter"] is True
                )
                record(
                    "GAU-8扫射途中sprint与切弹停火无泄漏",
                    interrupt_ok,
                    {"sprint": sprint_int, "rocket": rocket_int},
                )

                # ==================== 2B. 草地线性防发白 + 512双分区弹坑 + A-10C飞行姿态 + RTB撤离全链路验证 ====================
                # (1) 近景立体草丛 linearOutput 与 HDR EffectComposer 管线联动校验（仅关 Bloom 保持线性不发白；全关切直渲切回 sRGB）
                grass_bloom_on = page.evaluate("() => window.__SCV_MAP__.getGrassState()")
                page.evaluate("() => window.__SCV_MAP__.setBloomEnabled(false, false)")
                grass_bloom_only_off = page.evaluate("() => window.__SCV_MAP__.getGrassState()")
                page.evaluate(
                    """() => {
                        window.__SCV_MAP__.setDofMode('off', false);
                        window.__SCV_MAP__.setHeatShimmerEnabled(false);
                    }"""
                )
                grass_direct_off = page.evaluate("() => window.__SCV_MAP__.getGrassState()")
                page.evaluate(
                    """() => {
                        window.__SCV_MAP__.setHeatShimmerEnabled(true);
                        window.__SCV_MAP__.setDofMode('soft', false);
                        window.__SCV_MAP__.setBloomEnabled(true, false);
                    }"""
                )
                grass_bloom_restore = page.evaluate("() => window.__SCV_MAP__.getGrassState()")
                grass_ok = (
                    grass_bloom_on.get("bloomEnabled") is True
                    and grass_bloom_on.get("linearOutput") is True
                    and grass_bloom_on.get("uLinearOutput") == 1.0
                    and grass_bloom_only_off.get("bloomEnabled") is False
                    and grass_bloom_only_off.get("composerActive") is True
                    and grass_bloom_only_off.get("linearOutput") is True
                    and grass_direct_off.get("bloomEnabled") is False
                    and grass_direct_off.get("composerActive") is False
                    and grass_direct_off.get("linearOutput") is False
                    and grass_direct_off.get("uLinearOutput") == 0.0
                    and grass_bloom_restore.get("linearOutput") is True
                )
                record(
                    "近景草地双伽马修复与HDR_Composer线性联动",
                    grass_ok,
                    {
                        "on": grass_bloom_on,
                        "bloom_only_off": grass_bloom_only_off,
                        "direct_off": grass_direct_off,
                        "restored": grass_bloom_restore,
                    },
                )

                # (2) 地面损毁贴花 512 双分区保护 + 空间去重 + 单 DrawCall 验证
                crater_check = page.evaluate(
                    """() => {
                        window.__SCAVENGER_AIR_LAB__.executeAction('reset');
                        // 先落 10 个重型 GBU-31 弹坑（分布足够间距不互相合并）
                        for (let i = 0; i < 10; i++) {
                            window.__SCV_MAP__.spawnVfxExplosion({ x: -200 + i * 45, y: 2, z: 120 }, 'heavy');
                        }
                        // 在重型弹坑内部落 5 个轻弹着（应被空间去重忽略）
                        for (let i = 0; i < 5; i++) {
                            window.__SCV_MAP__.spawnVfxExplosion({ x: -200 + i * 45 + 1.0, y: 2, z: 120 + 0.5 }, 'light');
                        }
                        // 再刷 340 个轻型 30mm 焦痕（超出 320 轻区上限，不得覆盖任何重型弹坑！）
                        for (let j = 0; j < 340; j++) {
                            const gx = -400 + (j % 20) * 14;
                            const gz = -260 + Math.floor(j / 20) * 14;
                            window.__SCV_MAP__.spawnVfxExplosion({ x: gx, y: 2, z: gz }, 'light');
                        }
                        return window.__SCV_MAP__.getVfxStats();
                    }"""
                )
                crater_ok = (
                    crater_check.get("maxCraters") == 512
                    and crater_check.get("majorCraterSlots") == 192
                    and crater_check.get("lightCraterSlots") == 320
                    and crater_check.get("activeMajorCraters") == 10
                    and crater_check.get("activeLightCraters") == 320
                    and crater_check.get("activeCraters") == 330
                    and crater_check.get("dedupedCraters", 0) >= 5
                    and crater_check.get("poolDrawCalls", 99) <= 6
                )
                record(
                    "地面损毁贴花512双分区保护与空间去重及单DrawCall",
                    crater_ok,
                    {
                        "maxCraters": crater_check.get("maxCraters"),
                        "majorSlots": crater_check.get("majorCraterSlots"),
                        "lightSlots": crater_check.get("lightCraterSlots"),
                        "activeMajor": crater_check.get("activeMajorCraters"),
                        "activeLight": crater_check.get("activeLightCraters"),
                        "activeTotal": crater_check.get("activeCraters"),
                        "deduped": crater_check.get("dedupedCraters"),
                        "poolDrawCalls": crater_check.get("poolDrawCalls"),
                    },
                )

                # (3) A-10C 写实气动姿态（巡航正迎角、盘旋无常亮涡流、低空气流微扰）+ 撤离 RTB / 取消 / 飞离边界 / 呼回进场
                attitude_and_rtb = page.evaluate(
                    """() => {
                        const lab = window.__SCAVENGER_AIR_LAB__;
                        lab.executeAction('reset');
                        let tNow = lab.getConfig().simNowSec;
                        for (let i = 0; i < 90; i++) {
                            tNow += 1 / 60;
                            window.__SCV_MAP__.stepSimAndRender(1 / 60, tNow, false);
                        }
                        const orbitSummary = lab.getUnitSummary();

                        // 切换到机动页签，校验【T · 撤离战区】按钮存在
                        const moveTab = document.querySelector('#hud-cmd-pager button[data-cmd-page="move"]');
                        if (moveTab) moveTab.click();
                        const cmdLabels = [...document.querySelectorAll('#hud-cmd-grid button')].map(b => b.textContent.trim());
                        const hasEvacuateBtn = cmdLabels.some(s => s.includes('撤离'));

                        // 触发撤离 RTB -> 推进 1.5s 检查爬升与加速 -> 取消撤离回到盘旋
                        const rtbStartRes = lab.executeAction('rtb');
                        const altBeforeRtb = orbitSummary.position[1];
                        for (let i = 0; i < 90; i++) {
                            tNow += 1 / 60;
                            window.__SCV_MAP__.stepSimAndRender(1 / 60, tNow, false);
                        }
                        const rtbMid = lab.getUnitSummary();
                        const rtbCancelRes = lab.executeAction('rtb');
                        const afterCancel = lab.getUnitSummary();

                        // 再次撤离并推进直至飞离 9km 战场边界（evacuated），最后验证调试台【进场】一键呼回
                        lab.executeAction('rtb', { force: true });
                        let evacuatedReached = false;
                        for (let i = 0; i < 3200; i++) {
                            tNow += 0.05;
                            window.__SCV_MAP__.stepSimAndRender(0.05, tNow, false);
                            const cur = lab.getUnitSummary();
                            if (cur && cur.flightState === 'evacuated') {
                                evacuatedReached = true;
                                break;
                            }
                        }
                        const evacSummary = lab.getUnitSummary();
                        lab.executeAction('approach');
                        const recallSummary = lab.getUnitSummary();

                        return {
                            orbitPitchDeg: orbitSummary?.anim?.pitchDeg,
                            orbitBankDeg: orbitSummary?.anim?.bankDeg,
                            orbitVortexActive: orbitSummary?.wingtipVortexActive,
                            hasEvacuateBtn,
                            rtbStartOk: Boolean(rtbStartRes?.ok),
                            rtbMidState: rtbMid?.flightState,
                            rtbMidSpeedMps: rtbMid?.anim?.speedMps,
                            rtbAltGainM: Number(((rtbMid?.position?.[1] || 0) - altBeforeRtb).toFixed(1)),
                            rtbPredictedKind: rtbMid?.predictedPath?.kind,
                            rtbPredictedIsRtb: Boolean(rtbMid?.predictedPath?.isRtb),
                            rtbCancelOk: Boolean(rtbCancelRes?.ok),
                            afterCancelState: afterCancel?.flightState,
                            evacuatedReached,
                            evacFlag: Boolean(evacSummary?.evacuated),
                            recallState: recallSummary?.flightState,
                            recallEvacFlag: Boolean(recallSummary?.evacuated),
                        };
                    }"""
                )
                att_rtb_ok = (
                    1.5 <= attitude_and_rtb.get("orbitPitchDeg", 0) <= 5.5
                    and attitude_and_rtb.get("orbitVortexActive") is False
                    and attitude_and_rtb.get("hasEvacuateBtn") is True
                    and attitude_and_rtb.get("rtbStartOk") is True
                    and attitude_and_rtb.get("rtbMidState") == "rtb"
                    and attitude_and_rtb.get("rtbMidSpeedMps", 0) > 108
                    and attitude_and_rtb.get("rtbAltGainM", 0) > 4.0
                    and attitude_and_rtb.get("rtbPredictedIsRtb") is True
                    and attitude_and_rtb.get("rtbCancelOk") is True
                    and attitude_and_rtb.get("afterCancelState") == "orbit"
                    and attitude_and_rtb.get("evacuatedReached") is True
                    and attitude_and_rtb.get("evacFlag") is True
                    and attitude_and_rtb.get("recallState") == "approach"
                    and attitude_and_rtb.get("recallEvacFlag") is False
                )
                record("A-10C气动正迎角清洁涡流与RTB撤离取消离场呼回全链路", att_rtb_ok, attitude_and_rtb)

                # (4) 撤离防卡死（跟随自动解除 + 瞄准退态 + 0 单位待命 + WASD 可控）与底部兵牌抽卡/发牌动画（Air-Lab 端）
                page.evaluate("() => { window.__SCV_MAP__.setManualLoop(false); window.__SCAVENGER_AIR_LAB__.executeAction('reset'); }")
                page.click('#hud-cmd-pager button[data-cmd-page="attack"]')
                page.wait_for_timeout(180)
                page.keyboard.press("g")
                page.wait_for_timeout(120)
                page.click('#hud-cmd-grid button[data-airlab-weapon-id="gau8_30mm"]')
                page.wait_for_timeout(120)
                pre_rtb = page.evaluate(
                    """() => ({
                        inter: window.__SCV_INTERACTION__?.getState?.(),
                        deck: window.__SCV_MAP__.getCardDeckState(),
                    })"""
                )
                page.keyboard.press("t")
                page.wait_for_timeout(150)
                during_rtb = page.evaluate(
                    """() => ({
                        inter: window.__SCV_INTERACTION__?.getState?.(),
                        deck: window.__SCV_MAP__.getCardDeckState(),
                        flightState: window.__SCAVENGER_AIR_LAB__.getUnitSummary()?.flightState,
                    })"""
                )
                page.evaluate(
                    """() => {
                        window.__SCV_MAP__.setManualLoop(true);
                        let tNow = window.__SCAVENGER_AIR_LAB__.getConfig().simNowSec;
                        for (let i = 0; i < 3200; i++) {
                            tNow += 0.05;
                            window.__SCV_MAP__.stepSimAndRender(0.05, tNow, false);
                            if (window.__SCAVENGER_AIR_LAB__.getUnitSummary()?.flightState === 'evacuated') break;
                        }
                        window.__SCV_MAP__.stepSimAndRender(0.016, tNow + 0.016, true);
                        window.__SCV_MAP__.setManualLoop(false);
                    }"""
                )
                evac_anim_now = page.evaluate("() => window.__SCV_MAP__.getCardDeckState()")
                page.wait_for_timeout(420)
                after_evac_ui = page.evaluate(
                    """() => ({
                        deck: window.__SCV_MAP__.getCardDeckState(),
                        inter: window.__SCV_INTERACTION__?.getState?.(),
                        allTabText: document.querySelector('#deck-filter-tabs [data-deck-tab="all"]')?.textContent?.trim() || '',
                        inspName: document.getElementById('insp-name')?.textContent?.trim() || '',
                        cmdTexts: [...document.querySelectorAll('#hud-cmd-grid button .c-txt')].map(e => e.textContent.trim()),
                    })"""
                )
                # 实测 WASD 平移不被锁死（无头 SwiftShader 仅 ~2FPS，需跨越真实 rAF 帧等待输入被 animate() 消费）
                focus_z_before = after_evac_ui["inter"]["view"]["focus"]["z"]
                page.mouse.click(800, 300)
                page.keyboard.down("KeyW")
                page.evaluate(
                    """() => new Promise(resolve => {
                        let left = 3;
                        const tick = () => {
                            left -= 1;
                            if (left <= 0) resolve();
                            else requestAnimationFrame(tick);
                        };
                        requestAnimationFrame(tick);
                    })"""
                )
                page.keyboard.up("KeyW")
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
                after_pan = page.evaluate("() => window.__SCV_INTERACTION__?.getState?.()")
                focus_z_after = after_pan["view"]["focus"]["z"]

                # 点击命令盘【重新进场】验证发牌加入动画
                page.click('#hud-cmd-grid button[data-cmd-key="T"]')
                enter_anim_now = page.evaluate("() => window.__SCV_MAP__.getCardDeckState()")
                page.wait_for_timeout(420)
                after_recall_ui = page.evaluate(
                    """() => ({
                        deck: window.__SCV_MAP__.getCardDeckState(),
                        allTabText: document.querySelector('#deck-filter-tabs [data-deck-tab="all"]')?.textContent?.trim() || '',
                        inspName: document.getElementById('insp-name')?.textContent?.trim() || '',
                        unitState: window.__SCAVENGER_AIR_LAB__.getUnitSummary()?.flightState,
                    })"""
                )
                lab_evac_anim_ok = (
                    pre_rtb["inter"].get("followUnitId") == "US_A10C_01"
                    and pre_rtb["inter"].get("mode") == "targeting"
                    and during_rtb.get("flightState") == "rtb"
                    and during_rtb["inter"].get("mode") == "play"
                    and during_rtb["inter"].get("followUnitId") is None
                    and during_rtb["deck"].get("rtbCardCount") == 1
                    and (evac_anim_now.get("leavingCount", 0) >= 1 or (evac_anim_now.get("lastAnimEvent") or {}).get("type") == "leave")
                    and (after_evac_ui["deck"].get("lastAnimEvent") or {}).get("type") == "leave"
                    and after_evac_ui["deck"].get("leavingCount") == 0
                    and after_evac_ui["deck"].get("activeHeroCount") == 0
                    and after_evac_ui["deck"].get("cardKeys") == []
                    and after_evac_ui.get("allTabText") == "全军 (0)"
                    and after_evac_ui.get("inspName") == "无在场单位"
                    and "重新进场" in after_evac_ui.get("cmdTexts", [])
                    and abs(focus_z_after - focus_z_before) > 5.0
                    and after_pan.get("followUnitId") is None
                    and (enter_anim_now.get("enteringCount", 0) >= 1 or (enter_anim_now.get("lastAnimEvent") or {}).get("type") == "enter")
                    and "US_A10" in after_recall_ui["deck"].get("cardKeys", [])
                    and after_recall_ui["deck"].get("activeHeroCount") == 1
                    and after_recall_ui.get("allTabText") == "全军 (1)"
                    and after_recall_ui.get("inspName") == "A-10C"
                )
                record(
                    "AirLab撤离防卡死跟随解除与空选待命及兵牌抽发动画",
                    lab_evac_anim_ok,
                    {
                        "preRtb": pre_rtb["inter"],
                        "duringRtb": {"mode": during_rtb["inter"].get("mode"), "follow": during_rtb["inter"].get("followUnitId"), "rtbCards": during_rtb["deck"].get("rtbCardCount")},
                        "evacLeaveAnim": evac_anim_now.get("lastAnimEvent"),
                        "afterEvacUi": after_evac_ui,
                        "panDeltaZ": round(focus_z_after - focus_z_before, 2),
                        "enterAnim": enter_anim_now.get("lastAnimEvent"),
                        "afterRecallUi": after_recall_ui,
                    },
                )

                # 验证 dispose() 清理释放
                dispose_res = page.evaluate("() => window.__SCV_MAP__.disposeAudio()")
                spatial_after_dispose = page.evaluate("() => window.__SCAVENGER_AIR_LAB__.getSpatialAudioState()")
                dispose_ok = (
                    dispose_res.get("spatialDisposed") is True
                    and dispose_res.get("convolverCount") == 0
                    and spatial_after_dispose.get("busInitialized") is False
                    and spatial_after_dispose.get("convolverCount") == 0
                )
                record("dispose干净断开释放共享Convolver与分桶", dispose_ok, {"disposeRes": dispose_res, "after": spatial_after_dispose})

                report["errors"].extend(page_errors)
                report["mediaResponses"].extend(lab_media)
                ctx.close()

                # ==================== 3. 常规 /play 实战路由无回归验证 ====================
                ctx_play = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1)
                page_play = ctx_play.new_page()
                play_errors: list[str] = []
                page_play.on("pageerror", lambda err: play_errors.append(f"play_pageerror: {err}"))
                page_play.on("console", lambda msg: play_errors.append(f"play_console: {msg.text}") if msg.type == "error" else None)

                page_play.goto(f"{base_url}/play", wait_until="domcontentloaded", timeout=60000)
                page_play.wait_for_function("() => Boolean(window.__SCV_MAP__ && window.__SCV_MAP__.isCoreReady() === true)", timeout=60000)
                page_play.mouse.click(800, 450)
                page_play.wait_for_timeout(500)
                play_audio_state = page_play.evaluate("() => window.__SCV_MAP__.getAudioState()")
                play_no_debug_panel = page_play.evaluate("() => document.getElementById('airlab-debug-panel') === null")
                play_ok = (
                    len(play_errors) == 0
                    and play_no_debug_panel is True
                    and play_audio_state.get("busInitialized") is True
                    and play_audio_state.get("convolverCount") == 1
                    and play_audio_state.get("peak", 0) <= 1.0
                )
                record("常规/play实战总线初始化与零调试DOM注入及零报错", play_ok, {"audioState": play_audio_state, "noDebugPanel": play_no_debug_panel, "errors": play_errors})

                # 常规 /play 固定翼选中态 680p (1208x680) 与 1600x900 命令盘方案B布局防重叠验证 + 撤离防卡死
                ground_cards = page_play.locator('#hud-cards-grid .scv-unit-card:not([data-channel="plane"])')
                ground_hud_state = None
                if ground_cards.count() > 0:
                    ground_cards.first.click()
                    page_play.wait_for_timeout(120)
                    ground_hud_state = page_play.evaluate("() => window.__SCV_MAP__?.getUiHudState?.() || null")
                page_play.click('#hud-cards-grid .scv-unit-card[data-channel="plane"]')
                page_play.wait_for_timeout(150)
                play_cmd_b = page_play.evaluate(
                    """() => ({
                        isPartitioned: document.getElementById('hud-cmd-grid')?.classList.contains('is-partitioned'),
                        wepIcons: [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-wep-card svg[data-weapon-icon]')].map(s => s.dataset.weaponIcon),
                        utilIcons: [...document.querySelectorAll('#hud-cmd-grid .ui-cmd-util-btn svg[data-hud-icon]')].map(s => s.dataset.hudIcon),
                        hudState: window.__SCV_MAP__?.getUiHudState?.() || null,
                        pipState: window.__SCV_MAP__?.getPipCamState?.() || null,
                    })"""
                )
                page_play.set_viewport_size({"width": 1208, "height": 680})
                page_play.evaluate("() => window.dispatchEvent(new Event('resize'))")
                page_play.wait_for_function(
                    "() => (document.getElementById('canvas-host')?.getBoundingClientRect().height || 9999) <= 685",
                    timeout=10000,
                )
                page_play.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                compact_bounds = page_play.evaluate(
                    """() => {
                        const r = document.getElementById('hud-inspector')?.getBoundingClientRect();
                        const d = document.getElementById('hud-card-deck')?.getBoundingClientRect();
                        const h = document.getElementById('canvas-host')?.getBoundingClientRect();
                        return {
                            uiSize: document.getElementById('canvas-host')?.dataset?.uiSize || '',
                            hostHeight: h ? Math.round(h.height) : 0,
                            rightLeft: r ? Math.round(r.left) : 0,
                            rightTop: r ? Math.round(r.top) : 0,
                            rightRight: r ? Math.round(r.right) : 0,
                            rightBottom: r ? Math.round(r.bottom) : 0,
                            deckRight: d ? Math.round(d.right) : 0,
                            overlap: Boolean(r && d && r.left < d.right - 1),
                            inViewport: Boolean(r && r.right <= 1209 && r.bottom <= 681 && r.top >= 40),
                        };
                    }"""
                )
                page_play.screenshot(path=str(out_dir / "play_680p_scheme_b.png"))
                page_play.set_viewport_size({"width": 1600, "height": 900})
                page_play.evaluate("() => window.dispatchEvent(new Event('resize'))")
                page_play.wait_for_function(
                    "() => (document.getElementById('canvas-host')?.getBoundingClientRect().height || 0) >= 880",
                    timeout=10000,
                )
                page_play.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                play_b_ok = (
                    play_cmd_b["isPartitioned"] is True
                    and play_cmd_b["wepIcons"] == ["cannon", "rocket", "missile", "bomb"]
                    and play_cmd_b["utilIcons"] == ["flare", "altitude"]
                    and compact_bounds["overlap"] is False
                    and compact_bounds["inViewport"] is True
                    and bool(ground_hud_state and ground_hud_state.get("roeVisible") is True and ground_hud_state.get("fixedWingManualBadgeVisible") is False)
                    and bool((play_cmd_b.get("hudState") or {}).get("roeVisible") is False and (play_cmd_b.get("hudState") or {}).get("fixedWingManualBadgeVisible") is True)
                    and bool((play_cmd_b.get("pipState") or {}).get("offscreenIsolated") is True)
                )
                record(
                    "常规/play方案B武器图标及680p窄视口防重叠布局",
                    play_b_ok,
                    {"playCmdB": play_cmd_b, "groundHudState": ground_hud_state, "compactBounds": compact_bounds},
                )

                page_play.keyboard.press("g")
                page_play.wait_for_timeout(150)
                play_pre = page_play.evaluate("() => window.__SCV_INTERACTION__?.getState?.()")
                page_play.keyboard.press("t")
                page_play.wait_for_timeout(150)
                play_rtb = page_play.evaluate(
                    """() => ({
                        inter: window.__SCV_INTERACTION__?.getState?.(),
                        deck: window.__SCV_MAP__.getCardDeckState(),
                    })"""
                )
                page_play.evaluate(
                    """() => {
                        window.__SCV_MAP__.setManualLoop(true);
                        let now = performance.now() * 0.001;
                        for (let i = 0; i < 1600; i++) {
                            now += 0.05;
                            window.__SCV_MAP__.stepSimAndRender(0.05, now, i % 40 === 0);
                            if (window.__SCV_MAP__.getCardDeckState().activeHeroCount <= 2) break;
                        }
                        window.__SCV_MAP__.stepSimAndRender(0.016, now + 0.016, true);
                        window.__SCV_MAP__.setManualLoop(false);
                    }"""
                )
                page_play.wait_for_timeout(420)
                play_after_evac = page_play.evaluate(
                    """() => ({
                        hud: window.__SCV_MAP__.getUiHudState(),
                        deck: window.__SCV_MAP__.getCardDeckState(),
                        inter: window.__SCV_INTERACTION__?.getState?.(),
                    })"""
                )
                pz0 = play_after_evac["inter"]["view"]["focus"]["z"]
                page_play.mouse.click(800, 300)
                page_play.keyboard.down("KeyW")
                page_play.evaluate(
                    """() => new Promise(resolve => {
                        let left = 3;
                        const tick = () => {
                            left -= 1;
                            if (left <= 0) resolve();
                            else requestAnimationFrame(tick);
                        };
                        requestAnimationFrame(tick);
                    })"""
                )
                page_play.keyboard.up("KeyW")
                page_play.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
                play_after_pan = page_play.evaluate("() => window.__SCV_INTERACTION__?.getState?.()")
                pz1 = play_after_pan["view"]["focus"]["z"]
                play_evac_ok = (
                    len(play_errors) == 0
                    and play_pre.get("followUnitId") == "US_A10C_01"
                    and play_rtb["inter"].get("followUnitId") is None
                    and play_rtb["deck"].get("rtbCardCount") == 1
                    and play_after_evac["hud"].get("heroCount") == 2
                    and play_after_evac["hud"].get("followUnitId") is None
                    and play_after_evac["hud"].get("followModeActive") is False
                    and "US_A10" not in play_after_evac["deck"].get("cardKeys", [])
                    and (play_after_evac["deck"].get("lastAnimEvent") or {}).get("type") == "leave"
                    and abs(pz1 - pz0) > 3.0
                )
                record(
                    "常规/play固定翼撤离解除跟随不跳锁配额返还与抽卡移除及WASD可控",
                    play_evac_ok,
                    {
                        "preFollow": play_pre.get("followUnitId"),
                        "rtbFollow": play_rtb["inter"].get("followUnitId"),
                        "rtbCards": play_rtb["deck"].get("rtbCardCount"),
                        "afterHud": {"heroCount": play_after_evac["hud"].get("heroCount"), "followId": play_after_evac["hud"].get("followUnitId")},
                        "afterDeck": {"cardKeys": play_after_evac["deck"].get("cardKeys"), "lastAnim": play_after_evac["deck"].get("lastAnimEvent")},
                        "panDeltaZ": round(pz1 - pz0, 2),
                        "errors": play_errors,
                    },
                )
                report["errors"].extend(play_errors)
                ctx_play.close()
            finally:
                browser.close()
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except Exception:
                server_proc.kill()

    report["passed"] = all(c["ok"] for c in report["checks"]) and len(report["errors"]) == 0
    out_path = out_dir / "air_audio_r1_r4_verification.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT_PATH={out_path}")
    print(f"OVERALL_PASSED={report['passed']} ({sum(1 for c in report['checks'] if c['ok'])}/{len(report['checks'])})")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    rep = run_verification(args.base_url, Path(args.out_dir), channel=args.channel)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
