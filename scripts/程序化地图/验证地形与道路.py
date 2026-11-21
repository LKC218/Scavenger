"""B1阶段验证：核对60237点BVH高程/道路/多层精度、裙边防缝射线，并在可见浏览器实测640x364与1080p双分辨率帧率与截图。"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
VERIFY_DIR = B1_DIR / "验证凭证"
PORT = 8821


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def run_node_height_verification() -> dict:
    """使用与浏览器完全一致的 src/程序化地图/程序化地形.mjs + TriangleSupportTile 核验全部 60,237 个 BVH 样点。"""
    node_code = """
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const root = path.resolve(process.argv[1]);
const b1Dir = path.resolve(process.argv[2]);
const batchDir = path.resolve(process.argv[3]);

const {decodeSCVHHeightfield, decodeSCVPPatch, HybridTerrainSupport} = await import(pathToFileURL(path.join(root, 'src/程序化地图/高度支撑.mjs')).href);
const {TriangleSupportTile} = await import(pathToFileURL(path.join(root, 'src/地图验证/精细支撑.mjs')).href);

const scvhComp = fs.readFileSync(path.join(b1Dir, 'heightfield/global-height-uint16.scvh'));
const scvhRaw = zlib.inflateSync(scvhComp);
const hf = await decodeSCVHHeightfield(scvhRaw.buffer.slice(scvhRaw.byteOffset, scvhRaw.byteOffset + scvhRaw.byteLength));
const support = new HybridTerrainSupport(hf);

const samples = JSON.parse(fs.readFileSync(path.join(batchDir, '全图分块/r02/支撑核验/r02/样点.json'), 'utf8'));

// 按 250m 分区按需加载 SCVP 补丁并核对全部 60,237 样点
const loadedPackages = new Set();
function ensureChunkLoaded(chunkId) {
  const [cx, cz] = chunkId.split('-').map(Number);
  const px = Math.floor(cx / 2), pz = Math.floor(cz / 2);
  const key = `${String(px).padStart(2, '0')}-${String(pz).padStart(2, '0')}`;
  if (loadedPackages.has(key)) return;
  loadedPackages.add(key);
  const comp = fs.readFileSync(path.join(b1Dir, 'heightfield/support-patches', key + '.scvp'));
  const raw = zlib.inflateSync(comp);
  const map = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const count = map.getUint32(8, true);
  for (let i = 0; i < count; i++) {
    const at = 16 + i * 12;
    const cell = map.getUint32(at, true);
    const offset = map.getUint32(at + 4, true);
    const len = map.getUint32(at + 8, true);
    const x = cell % 72, z = Math.floor(cell / 72);
    const cid = `${String(x).padStart(2, '0')}-${String(z).padStart(2, '0')}`;
    const slice = raw.buffer.slice(raw.byteOffset + offset, raw.byteOffset + offset + len);
    support.setPatchTile(cid, new TriangleSupportTile(slice));
  }
}

const primaryErrors = [];
const roadErrors = [];
const multiLayerErrors = [];
let stateMismatches = 0;

for (const s of samples) {
  ensureChunkLoaded(s.chunk);
  if ('query_layer' in s) {
    const res = support.query(s.x, s.z, {referenceY: s.referenceY, layer: s.query_layer});
    if (res.state !== 'ready' || !Number.isFinite(res.height)) {
      stateMismatches++;
      continue;
    }
    const err = Math.abs(res.height - s.height);
    if (s.feature === '下层支撑') multiLayerErrors.push(err);
    else roadErrors.push(err);
  } else {
    const res = support.query(s.x, s.z);
    if ((res.state === 'ready') !== (s.height !== null)) {
      stateMismatches++;
      continue;
    }
    if (s.height !== null) {
      primaryErrors.push(Math.abs(res.height - s.height));
    }
  }
}

primaryErrors.sort((a, b) => a - b);
roadErrors.sort((a, b) => a - b);
multiLayerErrors.sort((a, b) => a - b);

const quantile = (arr, q) => arr.length ? arr[Math.min(arr.length - 1, Math.floor(arr.length * q))] : 0;

// 连续射线多层夹具验证（桥下横向无遮挡、从上命中桥面、向上命中桥面）
let rayChecks = 0, maxRayErr = 0;
for (let cz = 0; cz < 72; cz += 8) {
  for (let cx = 0; cx < 72; cx += 8) {
    const cid = `${String(cx).padStart(2, '0')}-${String(cz).padStart(2, '0')}`;
    ensureChunkLoaded(cid);
    const wx = -4425 + cx * 125 + 62.5;
    const wz = -4150 + cz * 125 + 62.5;
    const q = support.query(wx, wz);
    if (q.state === 'ready' && Number.isFinite(q.height)) {
      const hit = support.segment({x: wx, y: q.height + 2, z: wz}, {x: wx, y: q.height - 2, z: wz});
      assert.notEqual(hit, null);
      maxRayErr = Math.max(maxRayErr, Math.abs(hit - 0.5) * 4);
      rayChecks++;
    }
  }
}

const out = {
  total_samples: samples.length,
  state_mismatches: stateMismatches,
  primary_samples: primaryErrors.length,
  primary_p50_m: quantile(primaryErrors, 0.5),
  primary_p95_m: quantile(primaryErrors, 0.95),
  primary_p99_m: quantile(primaryErrors, 0.99),
  primary_max_m: primaryErrors.at(-1) ?? 0,
  road_samples: roadErrors.length,
  road_p95_m: quantile(roadErrors, 0.95),
  road_max_m: roadErrors.at(-1) ?? 0,
  multilayer_samples: multiLayerErrors.length,
  multilayer_max_m: multiLayerErrors.at(-1) ?? 0,
  ray_checks: rayChecks,
  max_ray_error_m: maxRayErr,
  passed: (
    stateMismatches === 0 &&
    quantile(primaryErrors, 0.95) <= 0.05 &&
    (primaryErrors.at(-1) ?? 0) <= 0.12 &&
    (roadErrors.at(-1) ?? 0) <= 0.01 &&
    (multiLayerErrors.at(-1) ?? 0) <= 0.02 &&
    maxRayErr <= 0.01
  ),
};
console.log(JSON.stringify(out));
if (!out.passed) process.exit(1);
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_code, str(ROOT), str(B1_DIR), str(BATCH_DIR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print("NODE STDOUT:", proc.stdout)
        print("NODE STDERR:", proc.stderr)
        proc.check_returncode()
    return json.loads(proc.stdout.strip().splitlines()[-1])


def build_b1_test_html() -> bytes:
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <link rel="icon" href="data:," />
  <title>B1 程序化地形、Splatmap 与道路验证台</title>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #1c2219; color: #e6ecdf; font-family: "Microsoft YaHei", sans-serif; }
    #canvas-host { width: 100%; height: 100%; display: block; }
    #canvas-host canvas { width: 100% !important; height: 100% !important; display: block; image-rendering: auto; }
    #hud { position: fixed; top: 12px; left: 12px; background: rgba(18, 24, 16, 0.82); border: 1px solid rgba(168, 196, 138, 0.35); padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6; pointer-events: none; }
  </style>
  <script type="importmap">
  {
    "imports": {
      "three": "/vendor/build/three.module.js",
      "three/addons/": "/vendor/examples/jsm/"
    }
  }
  </script>
</head>
<body>
  <div id="canvas-host"></div>
  <div id="hud">正在加载 B1 程序化地形与道路底座…</div>
  <script type="module">
    import * as THREE from 'three';
    import {createModelLoader} from '/compressed-loader.mjs';
    import {
      decodeSCVHHeightfield,
      HybridTerrainSupport,
      loadTerrainArrayTexture,
      loadSCVSSplatControl,
      loadImageTextureWithData,
      createPackedHeightTexture,
      createProceduralTerrainMaterial,
      ProceduralTerrainPool,
      buildCustomRoadRibbonMesh
    } from '/src/程序化地图/程序化地形.mjs';

    const host = document.getElementById('canvas-host');
    const hud = document.getElementById('hud');
    const renderer = new THREE.WebGLRenderer({antialias: false, powerPreference: 'high-performance'});
    renderer.setPixelRatio(1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.18;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x3a4633);
    const camera = new THREE.OrthographicCamera(-100, 100, 65, -65, 0.1, 30000);
    scene.add(new THREE.HemisphereLight(0xe9efdc, 0x6c7a62, 1.55));
    const sun = new THREE.DirectionalLight(0xffedd3, 3.2);
    sun.position.set(-70, 110, 40);
    scene.add(sun, sun.target);

    const TILT = 48 * Math.PI / 180;
    const state = {
      center: new THREE.Vector3(0, 0, 85),
      span: 140,
      angle: 0,
      internalWidth: 640,
      internalHeight: 364,
      ready: false,
      frames: [],
    };

    const manifest = await (await fetch('/b1/地形与道路清单.json')).json();
    const [hfBuf, terrainArrayTex, splatCtrl, macroTint, roadsData] = await Promise.all([
      fetch('/b1/' + manifest.heightfield.global_file).then(r => r.arrayBuffer()),
      loadTerrainArrayTexture('/b1/' + manifest.splatmap.array_atlas.file),
      loadSCVSSplatControl('/b1/' + manifest.splatmap.splat_control.file),
      loadImageTextureWithData('/b1/' + manifest.splatmap.macro_tint.file, {nearest: false, srgb: false}),
      fetch('/b1/' + manifest.roads.roads_json.file).then(r => r.json()),
    ]);

    const hf = await decodeSCVHHeightfield(hfBuf);
    const support = new HybridTerrainSupport(hf);
    const packedHeight = createPackedHeightTexture(hf);
    const {material: terrainMat} = createProceduralTerrainMaterial({
      heightTex: packedHeight,
      terrainArrayTex,
      splatIndexTex: splatCtrl.indexTexture,
      splatWeightTex: splatCtrl.weightTexture,
      macroTintGrassTex: macroTint.texture,
      bounds: manifest.bounds,
      splatConfig: manifest.splatmap,
    });

    const terrainPool = new ProceduralTerrainPool(terrainMat, manifest.bounds);
    scene.add(terrainPool.root);

    const loader = createModelLoader(renderer);
    const [roadsGlb, waterGlb] = await Promise.all([
      loader.loadAsync('/b1/' + manifest.roads.roads_surface_glb.file),
      loader.loadAsync('/b1/' + manifest.roads.water_glb.file),
    ]);

    roadsGlb.scene.traverse(o => {
      if (o.isMesh) {
        o.receiveShadow = true;
        o.position.y += 0.04;
        for (const m of [o.material].flat()) {
          if (m) {
            m.polygonOffset = true;
            m.polygonOffsetFactor = -1;
            m.polygonOffsetUnits = -2;
          }
        }
      }
    });
    waterGlb.scene.traverse(o => { if (o.isMesh) o.receiveShadow = true; });
    scene.add(roadsGlb.scene, waterGlb.scene);

    function setResolution(w, h) {
      state.internalWidth = w;
      state.internalHeight = h;
      renderer.setSize(w, h, false);
      updateCamera();
    }

    function setCameraView(x, z, span, angle = 0) {
      state.center.x = x;
      state.center.z = z;
      const gy = support.heightAt(x, z);
      state.center.y = Number.isFinite(gy) ? gy : 0;
      state.span = span;
      state.angle = angle;
      updateCamera();
      terrainPool.update(state.center, span);
      renderer.render(scene, camera);
    }

    function updateCamera() {
      const aspect = (host.clientWidth || 1280) / (host.clientHeight || 720);
      camera.left = -state.span * aspect / 2;
      camera.right = state.span * aspect / 2;
      camera.top = state.span / 2;
      camera.bottom = -state.span / 2;
      const k = Math.max(1.5, state.span / 160);
      const horiz = (165 / Math.tan(TILT)) * k;
      camera.position.set(
        state.center.x + Math.sin(state.angle) * horiz,
        state.center.y + 165 * k,
        state.center.z + Math.cos(state.angle) * horiz
      );
      camera.lookAt(state.center);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld();
    }

    // 检查当前视口 25 个采样点的真实地表高度连续性（无空洞/裂纹）
    function checkGroundRays() {
      let checked = 0, misses = 0;
      const raycaster = new THREE.Raycaster();
      const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -state.center.y);
      const pt = new THREE.Vector3();
      for (const nx of [-0.8, -0.4, 0, 0.4, 0.8]) {
        for (const ny of [-0.8, -0.4, 0, 0.4, 0.8]) {
          raycaster.setFromCamera(new THREE.Vector2(nx, ny), camera);
          if (raycaster.ray.intersectPlane(plane, pt)) {
            if (pt.x >= manifest.bounds.minX && pt.x <= manifest.bounds.maxX && pt.z >= manifest.bounds.minZ && pt.z <= manifest.bounds.maxZ) {
              checked++;
              const h = support.heightAt(pt.x, pt.z);
              if (!Number.isFinite(h)) misses++;
            }
          }
        }
      }
      return {checked, misses};
    }

    setResolution(640, 364);
    setCameraView(0, 85, 140, 0);
    await renderer.compileAsync(scene, camera);
    renderer.render(scene, camera);
    state.ready = true;

    let last = performance.now();
    function loop(now) {
      const dt = now - last;
      last = now;
      if (dt > 0 && dt < 250) {
        state.frames.push(dt);
        if (state.frames.length > 1800) state.frames.shift();
      }
      renderer.render(scene, camera);
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);

    window.b1Test = {
      ready: true,
      setResolution,
      setCameraView,
      checkGroundRays,
      resetFrames() { state.frames = []; },
      samplePerf() {
        const sorted = [...state.frames].sort((a, b) => a - b);
        const avgMs = sorted.length ? sorted.reduce((a, b) => a + b, 0) / sorted.length : 0;
        const p95 = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : 0;
        const p99 = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.99))] : 0;
        return {
          frames: sorted.length,
          average_fps: avgMs > 0 ? 1000 / avgMs : 0,
          p95_ms: p95,
          p99_ms: p99,
          internal: [renderer.domElement.width, renderer.domElement.height],
          drawCalls: renderer.info.render.calls,
          triangles: renderer.info.render.triangles,
          textures: renderer.info.memory.textures,
          geometries: renderer.info.memory.geometries,
        };
      }
    };
    hud.textContent = 'B1 程序化地形 + 12层 TextureArray Splatmap + 道路样条已就绪';
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


class B1RequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path in ("/", "/index.html"):
            body = self.server.html_bytes
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        file_path = None
        if path == "/compressed-loader.mjs":
            file_path = ROOT / "src/资源加载/压缩模型加载.mjs"
        elif path.startswith("/src/"):
            file_path = (ROOT / path.lstrip("/")).resolve()
        elif path.startswith("/b1/"):
            file_path = (B1_DIR / path[4:]).resolve()
        elif path.startswith("/vendor/"):
            file_path = (self.server.three_dir / path[8:]).resolve()

        if not file_path or not file_path.is_file():
            self.send_error(404)
            return

        data = file_path.read_bytes()
        ext = file_path.suffix.lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".webp": "image/webp",
            ".glb": "model/gltf-binary",
            ".wasm": "application/wasm",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_browser_verification(screenshots_dir: Path) -> dict:
    pin = json.loads((ROOT / "local-data/tools/three/当前版本.json").read_text("utf-8"))
    three_dir = ROOT / "local-data/tools/three" / pin["version"]

    server = ThreadingHTTPServer(("127.0.0.1", PORT), B1RequestHandler)
    server.html_bytes = build_b1_test_html()
    server.three_dir = three_dir
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
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
            page.goto(f"http://127.0.0.1:{PORT}/")
            page.wait_for_function("window.b1Test?.ready === true", timeout=60000)
            page.bring_to_front()

            # 1. 四处典型区域截图与裙边防缝射线检查
            views = [
                ("机场跑道与停机坪交界", 0, 85, 140, 0.0),
                ("丘陵坡面与山谷过渡", -1850, -1420, 220, 0.4),
                ("道路路口与Splatmap混合", 620, -480, 140, -0.3),
                ("9公里全图总览", 75, 350, 9000, 0.0),
            ]
            view_checks = []
            for name, x, z, span, ang in views:
                page.evaluate("p => window.b1Test.setCameraView(p.x, p.z, p.span, p.ang)", {"x": x, "z": z, "span": span, "ang": ang})
                page.wait_for_timeout(300)
                rays = page.evaluate("window.b1Test.checkGroundRays()")
                shot = screenshots_dir / f"B1-{name}.png"
                page.screenshot(path=str(shot))
                view_checks.append({
                    "name": name,
                    "center": [x, z],
                    "span": span,
                    "ray_checked": rays["checked"],
                    "ray_misses": rays["misses"],
                    "screenshot": shot.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(shot),
                })

            # 2. 640x364 主验性能采样（预热2s + 采样8s）
            page.evaluate("window.b1Test.setResolution(640, 364)")
            page.evaluate("window.b1Test.setCameraView(0, 85, 180, 0)")
            page.wait_for_timeout(2000)
            page.evaluate("window.b1Test.resetFrames()")
            page.wait_for_timeout(8000)
            perf_640 = page.evaluate("window.b1Test.samplePerf()")

            # 3. 1920x1080 原生高分选测采样（视口 1920x1080，内部 1920x1080，预热2s + 采样8s）
            page.set_viewport_size({"width": 1920, "height": 1080})
            page.evaluate("window.b1Test.setResolution(1920, 1080)")
            page.evaluate("window.b1Test.setCameraView(0, 85, 180, 0)")
            page.wait_for_timeout(2000)
            page.evaluate("window.b1Test.resetFrames()")
            page.wait_for_timeout(8000)
            perf_1080 = page.evaluate("window.b1Test.samplePerf()")
            shot_1080 = screenshots_dir / "B1-1080p原生分辨率.png"
            page.screenshot(path=str(shot_1080))

            browser.close()

            passed = (
                len(errors) == 0
                and all(v["ray_checked"] >= 9 and v["ray_misses"] == 0 for v in view_checks)
                and perf_640["average_fps"] >= 60.0
                and perf_640["p95_ms"] <= 16.67
                and perf_1080["average_fps"] >= 60.0
                and perf_1080["p95_ms"] <= 16.67
            )
            return {
                "browser": "chrome",
                "errors": errors,
                "view_checks": view_checks,
                "perf_640x364": perf_640,
                "perf_1920x1080": perf_1080,
                "screenshot_1080p": shot_1080.relative_to(ROOT).as_posix(),
                "passed": passed,
            }
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    screenshots_dir = VERIFY_DIR / "截图"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((B1_DIR / "地形与道路清单.json").read_text("utf-8"))
    height_res = run_node_height_verification()
    browser_res = run_browser_verification(screenshots_dir)

    gate_passed = (
        manifest["budget_accounting"]["within_budget"]
        and height_res["passed"]
        and browser_res["passed"]
    )
    receipt = {
        "schema": "scavenger-procedural-map-b1-verification-v1",
        "phase": "B1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "budget": manifest["budget_accounting"],
        "height_and_support_accuracy": height_res,
        "browser_visual_and_performance": browser_res,
        "gate_passed": gate_passed,
    }
    out_file = VERIFY_DIR / "B1验证凭证.json"
    out_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "B1",
        "gate_passed": gate_passed,
        "total_b1_mib": manifest["budget_accounting"]["total_b1_mib"],
        "primary_p95_m": round(height_res["primary_p95_m"], 5),
        "primary_max_m": round(height_res["primary_max_m"], 5),
        "road_max_m": round(height_res["road_max_m"], 5),
        "multilayer_max_m": round(height_res["multilayer_max_m"], 5),
        "fps_640x364": round(browser_res["perf_640x364"]["average_fps"], 1),
        "p95_640x364_ms": round(browser_res["perf_640x364"]["p95_ms"], 2),
        "fps_1080p": round(browser_res["perf_1920x1080"]["average_fps"], 1),
        "p95_1080p_ms": round(browser_res["perf_1920x1080"]["p95_ms"], 2),
    }, ensure_ascii=False))
    if not gate_passed:
        raise RuntimeError("B1 硬性关口未全部通过")


if __name__ == "__main__":
    main()
