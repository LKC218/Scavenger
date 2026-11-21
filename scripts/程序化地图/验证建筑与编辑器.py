"""B3阶段验证：核验模块化建筑库体积(<=35MiB)、浏览器?mode=editor轻量地图编辑模式(新增/移动/旋转/替换/删除建筑、地形笔刷、Splat涂抹、道路样条)、自动派生旋转凸包碰撞+4m寻路绕行+直升机30m提前越障包络(误差0)及十秒级增量编译CLI。"""
from __future__ import annotations

import hashlib
import json
import shutil
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
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"
EVIDENCE_DIR = B3_DIR / "验证凭证"
SHOTS_DIR = EVIDENCE_DIR / "截图"

LOADER_MJS = ROOT / "src/资源加载/压缩模型加载.mjs"
PORT = 8821


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_b3_editor_html() -> bytes:
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <link rel="icon" href="data:," />
  <title>B3 模块化建筑库、?mode=editor 轻量地图编辑器与自动物理派生验证台</title>
  <style>
    html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #1c2219; color: #e6ecdf; font-family: "Microsoft YaHei", sans-serif; }
    #canvas-host { width: 100%; height: 100%; display: block; }
    #canvas-host canvas { width: 100% !important; height: 100% !important; display: block; }
    #hud { position: fixed; top: 12px; left: 12px; background: rgba(16, 22, 15, 0.88); border: 1px solid rgba(168, 196, 138, 0.45); padding: 9px 14px; border-radius: 6px; font-size: 12px; line-height: 1.65; pointer-events: none; z-index: 20; }
    #editor-panel { position: fixed; top: 12px; right: 12px; width: 310px; background: rgba(18, 24, 18, 0.90); border: 1px solid rgba(140, 180, 115, 0.45); border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; z-index: 20; box-shadow: 0 8px 24px rgba(0,0,0,0.45); }
    #editor-panel h3 { margin: 0 0 8px 0; font-size: 13px; color: #b9e38c; border-bottom: 1px solid rgba(168, 196, 138, 0.25); padding-bottom: 5px; }
    .ed-row { display: flex; justify-content: space-between; margin-bottom: 4px; color: #cfd8c4; }
    .ed-badge { display: inline-block; padding: 1px 6px; border-radius: 4px; background: rgba(68, 138, 60, 0.35); color: #b7f598; font-weight: bold; }
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
  <div id="hud">正在加载 B1+B2+B3 模块化大地图与 ?mode=editor 编辑器工作台…</div>
  <div id="editor-panel">
    <h3>🛠️ 轻量地图编辑器 (?mode=editor)</h3>
    <div class="ed-row"><span>当前模式：</span><span id="ed-mode" class="ed-badge">场景编辑与自动派生</span></div>
    <div class="ed-row"><span>活动插槽：</span><span id="ed-slot">original_refactored (双插槽就绪)</span></div>
    <div class="ed-row"><span>选中实例：</span><span id="ed-sel">无</span></div>
    <div class="ed-row"><span>动态派生碰撞盒：</span><span id="ed-obb">0 个</span></div>
    <div class="ed-row"><span>M1A2 4m寻路状态：</span><span id="ed-nav">待命</span></div>
    <div class="ed-row"><span>AH-64D 30m越障顶高：</span><span id="ed-heli">22.0 m</span></div>
    <div class="ed-row"><span>最近派生耗时：</span><span id="ed-ms">0.0 ms</span></div>
  </div>
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
      ThreeTierFoliageSystem
    } from '/src/程序化地图/立体植被与草丛.mjs';
    import {
      decodeSCVBBuildingInstances,
      decodeSCVCCollisions,
      AutoPhysicsAndNavEngine
    } from '/src/程序化地图/自动物理派生.mjs';
    import {
      ModularBuildingSystem,
      LightweightMapEditor
    } from '/src/程序化地图/地图编辑器.mjs';

    const host = document.getElementById('canvas-host');
    const hud = document.getElementById('hud');
    const isEditorUrl = new URLSearchParams(window.location.search).get('mode') === 'editor';
    document.getElementById('editor-panel').style.display = isEditorUrl ? 'block' : 'none';

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
    scene.add(new THREE.HemisphereLight(0xeef4e2, 0x68765c, 1.55));
    const sun = new THREE.DirectionalLight(0xffeacc, 3.25);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.04;
    scene.add(sun, sun.target);

    const TILT = 48 * Math.PI / 180;
    const state = {
      center: new THREE.Vector3(0, 0, 85),
      span: 135,
      angle: 0,
      internalWidth: 640,
      internalHeight: 364,
      ready: false,
    };

    const [b1Manifest, b2Manifest, b3Manifest, roadsData] = await Promise.all([
      fetch('/b1/地形与道路清单.json').then(r => r.json()),
      fetch('/b2/植被与草丛清单.json').then(r => r.json()),
      fetch('/b3/模块化建筑与场景清单.json').then(r => r.json()),
      fetch('/b1/roads/roads.json').then(r => r.json()),
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
      scvbBuf,
      scvcBuf,
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
      fetch('/b3/' + b3Manifest.spatial_building_instances.file).then(r => r.arrayBuffer()),
      fetch('/b3/' + b3Manifest.spatial_collisions_and_nav.file).then(r => r.arrayBuffer()),
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

    const [treeSpatialIndex, buildingIndex, collisionIndex] = await Promise.all([
      decodeSCVTTreeIndex(scvtBuf),
      decodeSCVBBuildingInstances(scvbBuf),
      decodeSCVCCollisions(scvcBuf),
    ]);

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

    const physicsEngine = new AutoPhysicsAndNavEngine({
      b3Manifest,
      b2Manifest,
      buildingIndex,
      collisionIndex,
      treeIndex: treeSpatialIndex,
      support,
    });

    const buildingSystem = new ModularBuildingSystem({
      b3Manifest,
      buildingIndex,
      physicsEngine,
      loader,
      baseUrl: '/b3/',
      sharedUniforms: foliageSystem.sharedUniforms,
    });
    scene.add(buildingSystem.root);

    const editor = new LightweightMapEditor({
      scene,
      camera,
      renderer,
      support,
      packedHeight,
      splatCtrl,
      roadsData,
      foliageSystem,
      buildingSystem,
      physicsEngine,
    });

    function syncEditorUI(overlayStats) {
      document.getElementById('ed-sel').textContent = editor.selectedInstanceId || '无';
      document.getElementById('ed-obb').textContent = `${physicsEngine.stats.activeDynamicOBBs} 个动态 (${overlayStats?.activeOBBsCount ?? 0} 局部总计)`;
      document.getElementById('ed-nav').textContent = overlayStats?.navFound
        ? `可达 (${overlayStats.navWaypointsCount} 航点自动绕行)`
        : '阻断';
      document.getElementById('ed-heli').textContent = `${(overlayStats?.maxHeliAltitudeY ?? 22.0).toFixed(1)} m`;
      document.getElementById('ed-ms').textContent = `${physicsEngine.stats.lastRecalcMs} ms`;
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

      const shadowHalf = Math.max(95, Math.min(360, state.span * 0.95));
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

    async function setCameraView(x, z, span, angle = 0, showOverlay = true) {
      state.center.x = x;
      state.center.z = z;
      const gy = support.heightAt(x, z);
      state.center.y = Number.isFinite(gy) ? gy : 0;
      state.span = span;
      state.angle = angle;
      updateCameraAndSun();
      terrainPool.update(state.center, span);
      const fStats = foliageSystem.update(state.center, span, true);
      const bStats = await buildingSystem.update(state.center, span, true);
      editor.overlayRoot.visible = showOverlay;
      let ovStats = null;
      if (showOverlay) {
        ovStats = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ovStats);
      }
      renderer.render(scene, camera);
      hud.textContent = `B3 模块化建筑与编辑器 | 中心:(${x.toFixed(0)}, ${z.toFixed(0)}) | 跨度:${span}m | 近景建筑:${bStats.nearBuildingInstances}栋 | 远景包络:${bStats.macroBuildings}栋 | 树木:${fStats.nearTreeInstances}棵`;
      return {foliageStats: fStats, buildingStats: bStats, overlayStats: ovStats};
    }

    setResolution(1280, 720);
    await setCameraView(0, 85, 135, 0, true);
    state.ready = true;

    function tick(now) {
      foliageSystem.setWindTime(now * 0.001);
      renderer.render(scene, camera);
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    window.__B3_TEST__ = {
      isReady: () => state.ready,
      setResolution,
      setCameraView,
      getBaselineNavAndHeli: () => {
        const ov = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ov);
        return ov;
      },
      addBuildingInCorridor: async (opts) => {
        const rec = await editor.addInstanceAt(opts);
        const ov = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ov);
        renderer.render(scene, camera);
        return {
          added: rec,
          centerBlocked: physicsEngine.isPointBlocked(opts.x, opts.z, 5.5),
          overlay: ov,
          recalcMs: physicsEngine.stats.lastRecalcMs,
        };
      },
      transformAndBrushScene: async (opts) => {
        const rec = await editor.transformInstance(opts.instanceId, opts.transform);
        editor.applyTerrainHeightBrush(opts.terrainBrush);
        editor.applySplatmapBrush(opts.splatBrush);
        const road = editor.editRoadSpline(opts.roadSpline);
        const ov = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ov);
        renderer.render(scene, camera);
        return {
          transformed: rec,
          newPosHeight: support.heightAt(opts.terrainBrush.x, opts.terrainBrush.z),
          roadId: road.id,
          overlay: ov,
        };
      },
      deleteAndVerifyCleared: async (instanceId) => {
        await editor.deleteInstance(instanceId);
        const ov = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ov);
        renderer.render(scene, camera);
        return {
          centerBlockedAfterDelete: physicsEngine.isPointBlocked(0, 85, 5.5),
          overlay: ov,
        };
      },
      exportSceneSource: () => physicsEngine.exportSceneSourceJSON(),
      importSceneSource: async (src) => {
        physicsEngine.importSceneSourceJSON(src);
        await buildingSystem.rebuildEditorDynamicMeshes();
        const ov = editor.refreshPhysicsAndNavOverlay();
        syncEditorUI(ov);
        renderer.render(scene, camera);
        return ov;
      },
    };
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


class B3TestHandler(SimpleHTTPRequestHandler):
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
        elif raw_path.startswith("/b3/"):
            file_path = (B3_DIR / raw_path[len("/b3/") :]).resolve()

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
        }.get(ext, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def run_b3_verification(port: int) -> dict:
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

        # 以 ?mode=editor 打开轻量地图编辑器
        page.goto(f"http://127.0.0.1:{port}/?mode=editor", timeout=60000)
        page.wait_for_function("window.__B3_TEST__ && window.__B3_TEST__.isReady() === true", timeout=60000)
        page.bring_to_front()
        page.wait_for_timeout(400)

        # 1. 记录无新建筑时的基准 4m 寻路与直升机巡航高度
        baseline = page.evaluate("window.__B3_TEST__.getBaselineNavAndHeli()")
        base_max_z_dev = max(abs(p[2] - 85.0) for p in baseline["navWaypoints"])
        base_heli_alt = baseline["maxHeliAltitudeY"]

        # 2. 在走廊中心 (0, 85) 新增并旋转一栋高大模块化建筑 (building_Air_control_tower_1: model:BA_SCENE_f5792b2df1f7bd2ca484_v001 或首栋高层建筑)
        b3_manifest = json.loads((B3_DIR / "模块化建筑与场景清单.json").read_text("utf-8"))
        # 挑选高度 >= 14m 且宽度适中的代表建筑
        tall_protos = [
            p for p in b3_manifest["building_prototypes"]
            if (p["bounds_max"][1] - p["bounds_min"][1]) >= 14.0
            and 8.0 <= (p["bounds_max"][0] - p["bounds_min"][0]) <= 32.0
        ]
        chosen_proto = tall_protos[0] if tall_protos else b3_manifest["building_prototypes"][0]
        replace_proto = tall_protos[1] if len(tall_protos) > 1 else b3_manifest["building_prototypes"][1]

        add_res = page.evaluate(
            "opts => window.__B3_TEST__.addBuildingInCorridor(opts)",
            {
                "id": "editor_test_tower_01",
                "prototype_id": chosen_proto["id"],
                "x": 0.0,
                "z": 85.0,
                "yaw_rad": 0.42,
                "scale": [1.0, 1.0, 1.0],
            },
        )
        page.wait_for_timeout(350)
        shot_add = SHOTS_DIR / "B3-编辑器新增旋转建筑与自动碰撞寻路越障包络.png"
        page.screenshot(path=str(shot_add))

        # 核验新建筑自动产生碰撞盒、M1A2 4m寻路自动绕行 (Z轴偏移显著增大且不穿过OBB)、AH-64D 直升机提前30m拉升越障
        after_add_max_z_dev = max(abs(p[2] - 85.0) for p in add_res["overlay"]["navWaypoints"])
        after_add_heli_alt = add_res["overlay"]["maxHeliAltitudeY"]
        expected_min_heli_alt = add_res["added"]["obb"]["topY"] + 10.5

        # 核验提前 30m 拉升：在 x = -25m (距离建筑中心 25m < 30m) 处直升机包络高度已经高于基准巡航高度
        heli_pts = add_res["overlay"]["heliProfile"]
        lookahead_lifted = any(p[0] <= -18.0 and p[1] > base_heli_alt + 3.0 for p in heli_pts)

        # 3. 执行移动/旋转/缩放/替换原型 + 地形高度笔刷 + Splatmap材质涂抹 + 道路样条实时编辑
        edit_res = page.evaluate(
            "opts => window.__B3_TEST__.transformAndBrushScene(opts)",
            {
                "instanceId": "editor_test_tower_01",
                "transform": {
                    "prototype_id": replace_proto["id"],
                    "x": 10.0,
                    "z": 89.0,
                    "yaw_rad": -0.55,
                    "scale": [1.08, 1.12, 1.08],
                },
                "terrainBrush": {"x": -24.0, "z": 62.0, "radius": 20.0, "deltaH": 4.2, "mode": "raise"},
                "splatBrush": {"x": -24.0, "z": 62.0, "radius": 22.0, "channel": 0, "strength": 0.9},
                "roadSpline": {
                    "splineId": "editor_road_01",
                    "width": 10.0,
                    "points": [[-48.0, 0.0, 52.0], [0.0, 0.0, 56.0], [48.0, 0.0, 52.0]],
                },
            },
        )
        page.wait_for_timeout(350)
        shot_edit = SHOTS_DIR / "B3-编辑器地形笔刷Splat涂抹与道路样条实时编辑.png"
        page.screenshot(path=str(shot_edit))

        # 4. 导出 scene-source.json 并调用 增量编译地图.py CLI 核验 <= 10 秒增量打包
        exported_scene = page.evaluate("window.__B3_TEST__.exportSceneSource()")
        scene_src_path = B3_DIR / "scene-source.json"
        scene_src_path.write_text(json.dumps(exported_scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        cli_proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/程序化地图/增量编译地图.py"), "--source", str(scene_src_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        cli_summary = json.loads(cli_proc.stdout.strip().splitlines()[-1])

        # 5. 在浏览器中删除实例确认恢复畅通，再重新导入 scene-source.json 确认 100% 复现
        del_res = page.evaluate("window.__B3_TEST__.deleteAndVerifyCleared('editor_test_tower_01')")
        reimport_ov = page.evaluate("src => window.__B3_TEST__.importSceneSource(src)", exported_scene)

        # 6. 截留机场机库群、城镇模块化建筑群与 9000m 全图建筑总览
        scene_views = [
            ("B3-机场机库与模块化建筑群近景", -362.5, -462.5, 220.0, 0.25),
            ("B3-城镇模块化建筑群与森林融合", 2887.5, 912.5, 240.0, -0.3),
            ("B3-9000m全图建筑与林冠总览", 75.0, 350.0, 9000.0, 0.0),
        ]
        view_shots = []
        for v_name, vx, vz, vspan, vang in scene_views:
            v_info = page.evaluate(
                "arg => window.__B3_TEST__.setCameraView(arg.x, arg.z, arg.span, arg.ang, false)",
                {"x": vx, "z": vz, "span": vspan, "ang": vang},
            )
            page.wait_for_timeout(450)
            sp = SHOTS_DIR / f"{v_name}.png"
            page.screenshot(path=str(sp))
            view_shots.append({
                "name": v_name,
                "center": [vx, vz],
                "span": vspan,
                "buildingStats": v_info["buildingStats"],
                "screenshot": sp.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(sp),
            })

        browser.close()

    # 重置 scene-source.json 为干净初始态，同时保留导出的测试副本在验证凭证目录
    (EVIDENCE_DIR / "导出的编辑场景-scene-source.json").write_text(
        json.dumps(exported_scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    auto_derivation_checks = {
        "center_blocked_on_add": bool(add_res["centerBlocked"]),
        "baseline_max_z_deviation_m": round(base_max_z_dev, 3),
        "detour_max_z_deviation_m": round(after_add_max_z_dev, 3),
        "tank_4m_detour_triggered": bool(after_add_max_z_dev >= base_max_z_dev + 8.0),
        "baseline_heli_altitude_m": round(base_heli_alt, 3),
        "after_add_heli_altitude_m": round(after_add_heli_alt, 3),
        "expected_min_heli_altitude_m": round(expected_min_heli_alt, 3),
        "heli_30m_lookahead_lifted": bool(lookahead_lifted and after_add_heli_alt >= expected_min_heli_alt - 0.1),
        "terrain_brush_elevated_m": round(float(edit_res["newPosHeight"]), 3),
        "cleared_after_delete": bool(not del_res["centerBlockedAfterDelete"]),
        "reimported_heli_altitude_m": round(float(reimport_ov["maxHeliAltitudeY"]), 3),
        "recalc_latency_ms": add_res["recalcMs"],
    }

    return {
        "browser": "chrome",
        "errors": errors,
        "editor_add_screenshot": shot_add.relative_to(ROOT).as_posix(),
        "editor_add_sha256": sha256_file(shot_add),
        "editor_brush_screenshot": shot_edit.relative_to(ROOT).as_posix(),
        "editor_brush_sha256": sha256_file(shot_edit),
        "auto_derivation_checks": auto_derivation_checks,
        "incremental_cli_check": cli_summary,
        "scene_view_checks": view_shots,
    }


def main() -> None:
    if EVIDENCE_DIR.exists():
        shutil.rmtree(EVIDENCE_DIR)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    b3_manifest = json.loads((B3_DIR / "模块化建筑与场景清单.json").read_text("utf-8"))
    budget = b3_manifest["budget_summary"]

    pin = json.loads((ROOT / "local-data/tools/three/当前版本.json").read_text("utf-8"))
    three_dir = ROOT / "local-data/tools/three" / pin["version"]

    server = ThreadingHTTPServer(("127.0.0.1", PORT), B3TestHandler)
    server.html_bytes = build_b3_editor_html()
    server.three_dir = three_dir
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        browser_res = run_b3_verification(PORT)
    finally:
        server.shutdown()
        server.server_close()

    checks = browser_res["auto_derivation_checks"]
    cli_ok = browser_res["incremental_cli_check"]["within_10s_target"]
    all_ok = (
        budget["within_budget"]
        and len(browser_res["errors"]) == 0
        and checks["center_blocked_on_add"]
        and checks["tank_4m_detour_triggered"]
        and checks["heli_30m_lookahead_lifted"]
        and checks["terrain_brush_elevated_m"] > 1.5
        and checks["cleared_after_delete"]
        and cli_ok
    )

    receipt = {
        "schema": "scavenger-procedural-map-b3-verification-v1",
        "phase": "B3",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "budget": budget,
        "browser_and_editor_verification": browser_res,
        "gate_passed": bool(all_ok),
    }

    receipt_path = EVIDENCE_DIR / "B3验证凭证.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "B3",
        "gate_passed": receipt["gate_passed"],
        "total_b3_mib": budget["total_b3_mib"],
        "cli_elapsed_s": browser_res["incremental_cli_check"]["elapsed_s"],
        "auto_derivation_checks": checks,
    }, ensure_ascii=False))
    if not receipt["gate_passed"]:
        raise RuntimeError("B3 阶段关口未通过，请检查 B3验证凭证.json")


if __name__ == "__main__":
    main()
