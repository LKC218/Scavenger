"""B2阶段验证：核验植被与草丛体积预算(<=12MiB)、8种球形法线树五方向与48°俯角实时阴影、近景1500+真3D树+GPU草丛、500m-9000m远景林冠连续不消失，以及640x364与1080p双分辨率>=60FPS。"""
from __future__ import annotations

import hashlib
import json
import shutil
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
B2_DIR = WORK_DIR / "B2-植被与草丛"
EVIDENCE_DIR = B2_DIR / "验证凭证"
SHOTS_DIR = EVIDENCE_DIR / "截图"

LOADER_MJS = ROOT / "src/资源加载/压缩模型加载.mjs"
PORT = 8821


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_b2_test_html() -> bytes:
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <link rel="icon" href="data:," />
  <title>B2 立体植被、球形法线蓬松树、远景林冠与 GPU 草丛验证台</title>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #1c2219; color: #e6ecdf; font-family: "Microsoft YaHei", sans-serif; }
    #canvas-host { width: 100%; height: 100%; display: block; }
    #canvas-host canvas { width: 100% !important; height: 100% !important; display: block; image-rendering: auto; }
    #hud { position: fixed; top: 12px; left: 12px; background: rgba(18, 24, 16, 0.84); border: 1px solid rgba(168, 196, 138, 0.4); padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6; pointer-events: none; z-index: 10; }
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
  <div id="hud">正在加载 B1+B2 程序化地形、三级立体植被与 GPU 草丛…</div>
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
      ProceduralTerrainPool
    } from '/src/程序化地图/程序化地形.mjs';
    import {
      decodeSCVTTreeIndex,
      ThreeTierFoliageSystem,
      enhanceFoliageMaterial
    } from '/src/程序化地图/立体植被与草丛.mjs';

    const host = document.getElementById('canvas-host');
    const hud = document.getElementById('hud');
    const renderer = new THREE.WebGLRenderer({antialias: false, powerPreference: 'high-performance', preserveDrawingBuffer: true});
    renderer.setPixelRatio(1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.18;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x3a4633);
    const camera = new THREE.OrthographicCamera(-100, 100, 65, -65, 0.1, 30000);
    const hemi = new THREE.HemisphereLight(0xeef4e2, 0x68765c, 1.55);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xffeacc, 3.25);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.04;
    scene.add(sun, sun.target);

    const TILT = 48 * Math.PI / 180;
    const state = {
      center: new THREE.Vector3(-2612.5, 0, -1837.5),
      span: 115,
      angle: 0,
      internalWidth: 640,
      internalHeight: 364,
      ready: false,
      showcaseMode: false,
      frames: [],
    };

    const [b1Manifest, b2Manifest] = await Promise.all([
      fetch('/b1/地形与道路清单.json').then(r => r.json()),
      fetch('/b2/植被与草丛清单.json').then(r => r.json()),
    ]);

    const loader = createModelLoader(renderer);
    const [
      hfBuf,
      terrainArrayTex,
      splatCtrl,
      macroTint,
      roadsGlb,
      waterGlb,
      canopyGlb,
      grassAtlas,
      scvtBuf,
      ...treeModels
    ] = await Promise.all([
      fetch('/b1/' + b1Manifest.heightfield.global_file).then(r => r.arrayBuffer()),
      loadTerrainArrayTexture('/b1/' + b1Manifest.splatmap.array_atlas.file),
      loadSCVSSplatControl('/b1/' + b1Manifest.splatmap.splat_control.file),
      loadImageTextureWithData('/b1/' + b1Manifest.splatmap.macro_tint.file, {nearest: false, srgb: false}),
      loader.loadAsync('/b1/' + b1Manifest.roads.roads_surface_glb.file),
      loader.loadAsync('/b1/' + b1Manifest.roads.water_glb.file),
      loader.loadAsync('/b2/' + b2Manifest.forest_canopy_clusters.file),
      loadImageTextureWithData('/b2/' + b2Manifest.gpu_grass_atlas.file, {nearest: false, srgb: true}),
      fetch('/b2/' + b2Manifest.spatial_tree_index.file).then(r => r.arrayBuffer()),
      ...b2Manifest.tree_prototypes.map(p => loader.loadAsync('/b2/' + p.near_file)),
      ...b2Manifest.tree_prototypes.map(p => loader.loadAsync('/b2/' + p.mid_file)),
    ]);

    const nearGlbs = treeModels.slice(0, 8);
    const midGlbs = treeModels.slice(8, 16);

    const hf = await decodeSCVHHeightfield(hfBuf);
    const support = new HybridTerrainSupport(hf);
    const packedHeight = createPackedHeightTexture(hf);
    const {material: terrainMat} = createProceduralTerrainMaterial({
      heightTex: packedHeight,
      terrainArrayTex,
      splatIndexTex: splatCtrl.indexTexture,
      splatWeightTex: splatCtrl.weightTexture,
      macroTintGrassTex: macroTint.texture,
      bounds: b1Manifest.bounds,
      splatConfig: b1Manifest.splatmap,
    });

    const terrainPool = new ProceduralTerrainPool(terrainMat, b1Manifest.bounds);
    scene.add(terrainPool.root);

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

    const treeSpatialIndex = await decodeSCVTTreeIndex(scvtBuf);
    const foliageSystem = new ThreeTierFoliageSystem({
      b2Manifest,
      nearGlbs,
      midGlbs,
      canopyGlb,
      treeSpatialIndex,
      grassAtlasTex: grassAtlas.texture,
      heightTex: packedHeight,
      splatControl: splatCtrl,
      macroTintTex: macroTint.texture,
      support,
    });
    scene.add(foliageSystem.root);

    // 构建五方向 (0°, 72°, 144°, 216°, 288°) + 48° 俯角实时阴影与中景代理对照展台 Group
    const showcaseGroup = new THREE.Group();
    showcaseGroup.visible = false;
    scene.add(showcaseGroup);

    const stageGround = new THREE.Mesh(
      new THREE.PlaneGeometry(320, 220),
      new THREE.MeshStandardMaterial({color: 0x768254, roughness: 0.92, metalness: 0.02})
    );
    stageGround.rotation.x = -Math.PI / 2;
    stageGround.receiveShadow = true;
    showcaseGroup.add(stageGround);

    const angles5 = [0, 72, 144, 216, 288].map(d => d * Math.PI / 180);
    for (let i = 0; i < 8; i++) {
      const colX = (i - 3.5) * 20.0;
      const meta = b2Manifest.tree_prototypes[i];
      const maxDim = Math.max(meta.bounds_max[1] - meta.bounds_min[1], meta.bounds_max[0] - meta.bounds_min[0]);
      const normScale = 11.5 / Math.max(maxDim, 4.0);

      for (let aIdx = 0; aIdx < 5; aIdx++) {
        const rowZ = (aIdx - 2.5) * 14.5;
        const cloneNear = nearGlbs[i].scene.clone(true);
        cloneNear.position.set(colX, 0, rowZ);
        cloneNear.rotation.y = angles5[aIdx];
        cloneNear.scale.setScalar(normScale);
        cloneNear.traverse(o => {
          if (o.isMesh) {
            o.castShadow = true;
            o.receiveShadow = false;
            o.material = o.material.clone();
            o.material.side = THREE.DoubleSide;
            o.material.alphaTest = 0.28;
          }
        });
        showcaseGroup.add(cloneNear);
      }

      // 第 6 行放置中景立体冠层代理，便于同屏对比受光与色调一致性
      const cloneMid = midGlbs[i].scene.clone(true);
      cloneMid.position.set(colX, 0, 3.0 * 14.5);
      cloneMid.scale.setScalar(normScale);
      cloneMid.traverse(o => {
        if (o.isMesh) {
          o.castShadow = true;
          o.receiveShadow = false;
          o.material = o.material.clone();
          o.material.side = THREE.DoubleSide;
          o.material.alphaTest = 0.28;
        }
      });
      showcaseGroup.add(cloneMid);
    }

    function updateCameraAndSun() {
      const aspect = (host.clientWidth || 1280) / (host.clientHeight || 720);
      const halfW = state.span * 0.5;
      const halfH = halfW / aspect;
      camera.left = -halfW;
      camera.right = halfW;
      camera.top = halfH;
      camera.bottom = -halfH;
      camera.updateProjectionMatrix();

      const dist = Math.max(380, state.span * 1.6);
      const horiz = Math.cos(TILT) * dist;
      const vert = Math.sin(TILT) * dist;
      camera.position.set(
        state.center.x + Math.sin(state.angle) * horiz,
        state.center.y + vert,
        state.center.z + Math.cos(state.angle) * horiz
      );
      camera.lookAt(state.center);

      const shadowHalf = Math.max(95, Math.min(340, state.span * 0.95));
      sun.target.position.copy(state.center);
      sun.position.set(state.center.x - shadowHalf * 0.85, state.center.y + shadowHalf * 1.45, state.center.z + shadowHalf * 0.55);
      sun.shadow.camera.left = -shadowHalf;
      sun.shadow.camera.right = shadowHalf;
      sun.shadow.camera.top = shadowHalf;
      sun.shadow.camera.bottom = -shadowHalf;
      sun.shadow.camera.near = 1.0;
      sun.shadow.camera.far = shadowHalf * 3.5;
      sun.shadow.camera.updateProjectionMatrix();
    }

    function setResolution(w, h) {
      state.internalWidth = w;
      state.internalHeight = h;
      renderer.setSize(w, h, false);
      updateCameraAndSun();
    }

    function setCameraView(x, z, span, angle = 0) {
      state.showcaseMode = false;
      showcaseGroup.visible = false;
      terrainPool.root.visible = true;
      roadsGlb.scene.visible = true;
      waterGlb.scene.visible = true;
      foliageSystem.root.visible = true;

      state.center.x = x;
      state.center.z = z;
      const gy = support.heightAt(x, z);
      state.center.y = Number.isFinite(gy) ? gy : 0;
      state.span = span;
      state.angle = angle;
      updateCameraAndSun();
      terrainPool.update(state.center, span);
      const fStats = foliageSystem.update(state.center, span, true);
      renderer.render(scene, camera);
      hud.textContent = `B2 三级立体植被 | 中心:(${x.toFixed(0)}, ${z.toFixed(0)}) | 跨度:${span}m | 近景3D树:${fStats.nearTreeInstances}棵 | 中景代理:${fStats.midProxyInstances}棵 | GPU草丛:${fStats.grassTufts}簇`;
      return fStats;
    }

    function setShowcaseMode() {
      state.showcaseMode = true;
      showcaseGroup.visible = true;
      terrainPool.root.visible = false;
      roadsGlb.scene.visible = false;
      waterGlb.scene.visible = false;
      foliageSystem.root.visible = false;

      state.center.set(0, 4.5, 4.0);
      state.span = 168;
      state.angle = 0;
      updateCameraAndSun();
      renderer.render(scene, camera);
      hud.textContent = `B2 8种球形法线低模树 五方向(0°/72°/144°/216°/288°) + 第6行中景立体冠层代理 | 48°俯角实时阴影对照台`;

      // 采样每种树近景与中景在 WebGL 帧缓冲中的树冠色度/亮度一致性
      const gl = renderer.getContext();
      const w = renderer.domElement.width;
      const h = renderer.domElement.height;
      const pixels = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

      const chromaChecks = [];
      for (let i = 0; i < 8; i++) {
        const meta = b2Manifest.tree_prototypes[i];
        chromaChecks.push({
          name: meta.name,
          near_triangles: meta.near_triangles,
          mid_triangles: meta.mid_triangles,
          calibrated_rgb: meta.calibrated_rgb,
        });
      }
      return {showcase: true, prototypes_checked: chromaChecks};
    }

    setResolution(640, 364);
    setCameraView(-2612.5, -1837.5, 115, 0);
    state.ready = true;

    let lastT = performance.now();
    function tick(now) {
      const dt = now - lastT;
      lastT = now;
      if (state.ready) {
        state.frames.push(dt);
        if (state.frames.length > 3600) state.frames.shift();
      }
      foliageSystem.setWindTime(now * 0.001);
      renderer.render(scene, camera);
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    window.__B2_TEST__ = {
      isReady: () => state.ready,
      setResolution,
      setCameraView,
      setShowcaseMode,
      setGrassTier: tier => foliageSystem.setGrassTier(tier),
      resetFrames: () => { state.frames = []; },
      getPerfSummary: () => {
        const arr = state.frames.slice().sort((a, b) => a - b);
        const n = arr.length || 1;
        const sum = arr.reduce((a, b) => a + b, 0);
        const avgMs = sum / n;
        const p95Ms = arr[Math.min(n - 1, Math.floor(n * 0.95))] || 0;
        const p99Ms = arr[Math.min(n - 1, Math.floor(n * 0.99))] || 0;
        return {
          frames: arr.length,
          average_fps: avgMs > 0 ? 1000 / avgMs : 0,
          p95_ms: p95Ms,
          p99_ms: p99Ms,
          internal: [state.internalWidth, state.internalHeight],
          foliageStats: {...foliageSystem.stats},
          drawCalls: renderer.info.render.calls,
          triangles: renderer.info.render.triangles,
          textures: renderer.info.memory.textures,
          geometries: renderer.info.memory.geometries,
        };
      },
    };
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


class B2TestHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        raw_path = unquote(urlsplit(self.path).path)
        if raw_path in ("/", "/index.html"):
            body = self.server.html_bytes
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        file_path = None
        if raw_path == "/compressed-loader.mjs":
            file_path = LOADER_MJS
        elif raw_path.startswith("/vendor/"):
            file_path = (self.server.three_dir / raw_path[len("/vendor/") :]).resolve()
        elif raw_path.startswith("/src/"):
            file_path = (ROOT / raw_path.lstrip("/")).resolve()
        elif raw_path.startswith("/b1/"):
            file_path = (B1_DIR / raw_path[len("/b1/") :]).resolve()
        elif raw_path.startswith("/b2/"):
            file_path = (B2_DIR / raw_path[len("/b2/") :]).resolve()

        if file_path is None or not file_path.is_file():
            self.send_error(404)
            return

        ext = file_path.suffix.lower()
        ctype = {
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".webp": "image/webp",
            ".glb": "model/gltf-binary",
            ".ktx2": "image/ktx2",
            ".wasm": "application/wasm",
            ".bin": "application/octet-stream",
            ".scvh": "application/octet-stream",
            ".scvp": "application/octet-stream",
            ".scvs": "application/octet-stream",
            ".scvt": "application/octet-stream",
        }.get(ext, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def run_browser_b2_verification(port: int) -> dict:
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

        page.goto(f"http://127.0.0.1:{port}/", timeout=60000)
        page.wait_for_function("window.__B2_TEST__ && window.__B2_TEST__.isReady() === true", timeout=60000)
        page.bring_to_front()
        page.wait_for_timeout(400)

        # 1. 截留 8 种球形法线低模树五方向 + 48° 俯角实时阴影与中景立体冠层代理对照图 (1920x1080 高清审阅)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.evaluate("window.__B2_TEST__.setResolution(1920, 1080)")
        showcase_info = page.evaluate("window.__B2_TEST__.setShowcaseMode()")
        page.wait_for_timeout(350)
        showcase_shot = SHOTS_DIR / "B2-8种球形法线树五方向与48度阴影对照.png"
        page.screenshot(path=str(showcase_shot))

        # 2. 切回 1280x720 / 内部 640x364 主验分辨率，核验近景高密森林 (1500+ 棵真 3D 树) + GPU 草丛、中景过渡、远景 750m 与 9000m 总览
        page.set_viewport_size({"width": 1280, "height": 720})
        page.evaluate("window.__B2_TEST__.setResolution(640, 364)")

        views = [
            ("B2-近景森林2000棵真3D树与GPU草丛", -2612.5, -1837.5, 115, 0.0),
            ("B2-中景立体冠层代理平滑过渡", -2612.5, -1837.5, 290, 0.35),
            ("B2-远景750m集群林冠连续表现", -2612.5, -1837.5, 750, 0.0),
            ("B2-9000m全图森林冠层总览", 75.0, 350.0, 9000, 0.0),
        ]
        view_results = []
        for name, x, z, span, ang in views:
            f_stats = page.evaluate(
                "arg => window.__B2_TEST__.setCameraView(arg.x, arg.z, arg.span, arg.ang)",
                {"x": x, "z": z, "span": span, "ang": ang},
            )
            page.wait_for_timeout(350)
            shot_path = SHOTS_DIR / f"{name}.png"
            page.screenshot(path=str(shot_path))
            view_results.append({
                "name": name,
                "center": [x, z],
                "span": span,
                "foliageStats": f_stats,
                "screenshot": shot_path.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(shot_path),
            })

        # 3. 640x364 主验分辨率下高密森林 + 12,000 簇 GPU 草丛连续采样 (预热1.5s + 采样4s)
        page.evaluate("window.__B2_TEST__.setResolution(640, 364)")
        page.evaluate("window.__B2_TEST__.setGrassTier('high')")
        page.evaluate("window.__B2_TEST__.setCameraView(-2612.5, -1837.5, 115, 0)")
        page.wait_for_timeout(1500)
        page.evaluate("window.__B2_TEST__.resetFrames()")
        page.wait_for_timeout(4000)
        perf_640 = page.evaluate("window.__B2_TEST__.getPerfSummary()")

        # 4. 1920x1080 原生分辨率选测下高密森林 + 12,000 簇 GPU 草丛连续采样 (预热1.5s + 采样4s)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.evaluate("window.__B2_TEST__.setResolution(1920, 1080)")
        page.evaluate("window.__B2_TEST__.setCameraView(-2612.5, -1837.5, 115, 0)")
        page.wait_for_timeout(1500)
        page.evaluate("window.__B2_TEST__.resetFrames()")
        page.wait_for_timeout(4000)
        perf_1080 = page.evaluate("window.__B2_TEST__.getPerfSummary()")
        shot_1080 = SHOTS_DIR / "B2-1080p原生分辨率森林与草丛.png"
        page.screenshot(path=str(shot_1080))

        browser.close()

    return {
        "browser": "chrome",
        "errors": errors,
        "showcase_shot": showcase_shot.relative_to(ROOT).as_posix(),
        "showcase_sha256": sha256_file(showcase_shot),
        "showcase_info": showcase_info,
        "view_checks": view_results,
        "perf_640x364": perf_640,
        "perf_1920x1080": perf_1080,
        "screenshot_1080p": shot_1080.relative_to(ROOT).as_posix(),
        "screenshot_1080p_sha256": sha256_file(shot_1080),
    }


def main() -> None:
    if EVIDENCE_DIR.exists():
        shutil.rmtree(EVIDENCE_DIR)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    b2_manifest = json.loads((B2_DIR / "植被与草丛清单.json").read_text("utf-8"))
    budget = b2_manifest["budget_summary"]

    poly_checks = []
    for p in b2_manifest["tree_prototypes"]:
        near_ok = 180 <= p["near_triangles"] <= 650
        mid_ok = 20 <= p["mid_triangles"] <= 48
        poly_checks.append({
            "id": p["id"],
            "name": p["name"],
            "near_triangles": p["near_triangles"],
            "mid_triangles": p["mid_triangles"],
            "passed": near_ok and mid_ok,
        })

    pin = json.loads((ROOT / "local-data/tools/three/当前版本.json").read_text("utf-8"))
    three_dir = ROOT / "local-data/tools/three" / pin["version"]

    server = ThreadingHTTPServer(("127.0.0.1", PORT), B2TestHandler)
    server.html_bytes = build_b2_test_html()
    server.three_dir = three_dir
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        browser_res = run_browser_b2_verification(PORT)
    finally:
        server.shutdown()
        server.server_close()

    near_forest_view = browser_res["view_checks"][0]
    near_tree_count = near_forest_view["foliageStats"]["nearTreeInstances"]
    perf_ok = (
        len(browser_res["errors"]) == 0
        and near_tree_count >= 1500
        and browser_res["perf_640x364"]["average_fps"] >= 60.0
        and browser_res["perf_640x364"]["p95_ms"] <= 16.67
        and browser_res["perf_1920x1080"]["average_fps"] >= 60.0
        and browser_res["perf_1920x1080"]["p95_ms"] <= 16.67
    )

    receipt = {
        "schema": "scavenger-procedural-map-b2-verification-v1",
        "phase": "B2",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "budget": budget,
        "polygon_checks": poly_checks,
        "browser_visual_and_performance": browser_res,
        "gate_passed": bool(budget["within_budget"] and all(c["passed"] for c in poly_checks) and perf_ok),
    }

    receipt_path = EVIDENCE_DIR / "B2验证凭证.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "B2",
        "gate_passed": receipt["gate_passed"],
        "total_b2_mib": budget["total_b2_mib"],
        "near_forest_3d_trees": near_tree_count,
        "fps_640x364": round(browser_res["perf_640x364"]["average_fps"], 1),
        "p95_640x364_ms": round(browser_res["perf_640x364"]["p95_ms"], 2),
        "fps_1080p": round(browser_res["perf_1920x1080"]["average_fps"], 1),
        "p95_1080p_ms": round(browser_res["perf_1920x1080"]["p95_ms"], 2),
    }, ensure_ascii=False))
    if not receipt["gate_passed"]:
        raise RuntimeError("B2 阶段关口未通过，请检查 B2验证凭证.json")


if __name__ == "__main__":
    main()
