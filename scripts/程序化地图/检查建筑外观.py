"""全量 125 栋模块化建筑原型真机 WebGL 外观体检、联系表生成与实景对比复拍工具。"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"
AUDIT_BASE_DIR = WORK_DIR / "B5-全矩阵验证/建筑外观体检"
MANIFEST_PATH = B3_DIR / "模块化建筑与场景清单.json"
R14_META_PATH = BATCH_DIR / "运行接入/r14/地图运行.json"
SCVB_PATH = B3_DIR / "instances/buildings-spatial.scvb"


def wait_port_alive(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"预览服务 127.0.0.1:{port} 未就绪，请先启动 scripts/程序化地图/预览服务.py --port {port}")


def static_geometry_audit(manifest: dict, r14_meta: dict) -> dict[int, dict]:
    """调用 gltf-transform + meshoptimizer 对 125 个原型做精确静态几何/矩阵/绕向扫描。"""
    node_script = """
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const root = path.resolve(process.argv[1]);
const cfgPath = path.resolve(process.argv[2]);
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
const modules = path.join(root, 'local-data/tools/网页资源优化/1/node_modules');
const load = p => import(pathToFileURL(path.join(modules, p)).href);
const {NodeIO} = await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS} = await load('@gltf-transform/extensions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder} = await load('meshoptimizer/index.js');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder,
  'meshopt.decoder': MeshoptDecoder,
});

function mat4Det(m) {
  const n11=m[0],n21=m[1],n31=m[2],n41=m[3],
        n12=m[4],n22=m[5],n32=m[6],n42=m[7],
        n13=m[8],n23=m[9],n33=m[10],n43=m[11],
        n14=m[12],n24=m[13],n34=m[14],n44=m[15];
  return (
    n41*(+n14*n23*n32 - n13*n24*n32 - n14*n22*n33 + n12*n24*n33 + n13*n22*n34 - n12*n23*n34) +
    n42*(+n11*n23*n34 - n11*n24*n33 + n14*n21*n33 - n13*n21*n34 + n13*n24*n31 - n14*n23*n31) +
    n43*(+n11*n24*n32 - n11*n22*n34 - n14*n21*n32 + n12*n21*n34 + n14*n22*n31 - n12*n24*n31) +
    n44*(-n13*n22*n31 - n11*n23*n32 + n11*n22*n33 + n13*n21*n32 - n12*n21*n33 + n12*n23*n31)
  );
}

async function analyzeDoc(filePath) {
  const doc = await io.read(filePath);
  const rootD = doc.getRoot();
  let tris = 0;
  let flipTris = 0;
  let hasNegDet = false;
  let hasNonUniform = false;
  let missingTex = false;
  let minMetalMax = 0;
  for (const node of rootD.listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const wm = node.getWorldMatrix();
    const det = mat4Det(wm);
    if (det < -1e-6) hasNegDet = true;
    const sx = Math.hypot(wm[0], wm[1], wm[2]);
    const sy = Math.hypot(wm[4], wm[5], wm[6]);
    const sz = Math.hypot(wm[8], wm[9], wm[10]);
    if (Math.max(sx, sy, sz) / Math.max(1e-6, Math.min(sx, sy, sz)) > 1.05) hasNonUniform = true;
    for (const prim of mesh.listPrimitives()) {
      const pos = prim.getAttribute('POSITION');
      const norm = prim.getAttribute('NORMAL');
      const idx = prim.getIndices();
      const tc = idx ? Math.floor(idx.getCount() / 3) : (pos ? Math.floor(pos.getCount() / 3) : 0);
      tris += tc;
      if (pos && norm) {
        const pa = pos.getArray(), na = norm.getArray(), ia = idx ? idx.getArray() : null;
        for (let t = 0; t < tc; t++) {
          const i0 = ia ? ia[t*3] : t*3, i1 = ia ? ia[t*3+1] : t*3+1, i2 = ia ? ia[t*3+2] : t*3+2;
          const ax = pa[i1*3]-pa[i0*3], ay = pa[i1*3+1]-pa[i0*3+1], az = pa[i1*3+2]-pa[i0*3+2];
          const bx = pa[i2*3]-pa[i0*3], by = pa[i2*3+1]-pa[i0*3+1], bz = pa[i2*3+2]-pa[i0*3+2];
          const gx = ay*bz-az*by, gy = az*bx-ax*bz, gz = ax*by-ay*bx;
          const gl = Math.hypot(gx, gy, gz);
          if (gl < 1e-10) continue;
          const nx = (na[i0*3]+na[i1*3]+na[i2*3]) / 3;
          const ny = (na[i0*3+1]+na[i1*3+1]+na[i2*3+1]) / 3;
          const nz = (na[i0*3+2]+na[i1*3+2]+na[i2*3+2]) / 3;
          if (gx * nx + gy * ny + gz * nz < 0) flipTris++;
        }
      }
      const mat = prim.getMaterial();
      if (!mat || !mat.getBaseColorTexture()) missingTex = true;
      if (mat) minMetalMax = Math.max(minMetalMax, mat.getMetallicFactor() ?? 0);
    }
  }
  return {tris, flipTris, hasNegDet, hasNonUniform, missingTex, maxMetal: +minMetalMax.toFixed(3)};
}

const out = {};
for (const item of cfg.items) {
  const cur = await analyzeDoc(item.near_path);
  let srcTris = cur.tris;
  if (fs.existsSync(item.src_path)) {
    const src = await analyzeDoc(item.src_path);
    srcTris = src.tris;
  }
  out[item.idx] = {
    src_tris: srcTris,
    cur_tris: cur.tris,
    keep_ratio: +(cur.tris / Math.max(1, srcTris)).toFixed(4),
    flip_tris: cur.flipTris,
    has_neg_det: cur.hasNegDet,
    has_non_uniform: cur.hasNonUniform,
    missing_tex: cur.missingTex,
    max_metal: cur.maxMetal,
  };
}
fs.writeFileSync(cfg.out_path, JSON.stringify(out), 'utf8');
"""
    r14_map = {p["id"]: p for p in r14_meta["prototypes"]}
    items = []
    for p in manifest["building_prototypes"]:
        r14_p = r14_map.get(p["id"], {})
        src_rel = r14_p.get("middle_file") or r14_p.get("file") or p["slot_policy"]["original_reference_glb"]
        items.append({
            "idx": p["prototype_index"],
            "near_path": str(B3_DIR / p["near_file"]),
            "src_path": str(BATCH_DIR / src_rel),
        })

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as cf:
        out_json = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False, encoding="utf-8")
        out_path = Path(out_json.name)
        out_json.close()
        json.dump({"items": items, "out_path": str(out_path)}, cf, ensure_ascii=False)
        cfg_path = Path(cf.name)

    try:
        subprocess.run(
            ["node", "--input-type=module", "-e", node_script, str(ROOT), str(cfg_path)],
            cwd=ROOT,
            check=True,
        )
        raw = json.loads(out_path.read_text("utf-8"))
        return {int(k): v for k, v in raw.items()}
    finally:
        cfg_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def build_audit_page_html() -> str:
    """构造与主场景 100% 同源光照/曝光/InstancedMesh/enhanceBuildingMaterial 渲染管线的内存诊断页。"""
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>B3 全量建筑真机 WebGL 体检台</title>
  <script type="importmap">
  {
    "imports": {
      "three": "/vendor/build/three.module.js",
      "three/addons/": "/vendor/examples/jsm/"
    }
  }
  </script>
</head>
<body style="margin:0;background:#d6dacb;">
<canvas id="c" width="256" height="256" style="width:256px;height:256px;display:block;"></canvas>
<script type="module">
import * as THREE from 'three';
import {createModelLoader} from '/compressed-loader.mjs';
import {RENDER_STYLE_BASELINE} from '/src/程序化地图/渲染基线.mjs?v=audit';
import {enhanceBuildingMaterial} from '/src/程序化地图/地图编辑器.mjs?v=audit';

const SIZE = 256;
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  powerPreference: 'high-performance',
  preserveDrawingBuffer: true,
});
renderer.setSize(SIZE, SIZE, false);
renderer.setPixelRatio(1);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = RENDER_STYLE_BASELINE.webgl.exposure;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
// 中性浅暖灰背景 [214, 218, 203]，便于与纯黑屋顶/墙体精准分离测量
scene.background = new THREE.Color(0xd6dacb);

scene.add(new THREE.HemisphereLight(0xeef4e2, 0x68765c, 1.55));
const sun = new THREE.DirectionalLight(0xffeacc, 3.25);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.bias = -0.0005;
sun.shadow.normalBias = 0.04;
scene.add(sun, sun.target);

const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 5000);
const TILT = 48 * Math.PI / 180;
const YAW = 0.58; // 侧俯视约 33 度，兼顾屋顶与立面可见性
const loader = createModelLoader(renderer);
const sharedUniforms = {
  uCameraCenter: {value: new THREE.Vector3(0, 0, 0)},
  uCameraSpan: {value: 40.0},
  uDitherEnabled: {value: 0.0},
};
const holder = new THREE.Group();
scene.add(holder);

const gl = renderer.getContext();
const pxBuf = new Uint8Array(SIZE * SIZE * 4);
const BG_R = 214, BG_G = 218, BG_B = 203;

function disposeGroup(grp) {
  grp.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) {
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (m.map) m.map.dispose();
        m.dispose();
      }
    }
  });
  grp.clear();
}

window.__AUDIT_API__ = {
  async renderPrototype(meta) {
    disposeGroup(holder);
    const cacheBust = `?audit=${Date.now()}-${meta.prototype_index}`;
    const gltf = await loader.loadAsync(`/b3/${meta.near_file}${cacheBust}`);
    gltf.scene.updateMatrixWorld(true);

    const worldBox = new THREE.Box3();
    const subMeshes = [];
    const bw = (meta.bounds_max?.[0] ?? 2) - (meta.bounds_min?.[0] ?? -2);
    const bh = (meta.bounds_max?.[1] ?? 2) - (meta.bounds_min?.[1] ?? 0);
    const bd = (meta.bounds_max?.[2] ?? 2) - (meta.bounds_min?.[2] ?? -2);
    const isSmallProp = Math.max(bw, bd) < 4.2 || bh < 2.2;
    const ocNear = meta.overview_color || [0.55, 0.54, 0.52];

    gltf.scene.traverse(o => {
      if (!o.isMesh || !o.geometry) return;
      const mat = o.material.clone();
      enhanceBuildingMaterial(mat, sharedUniforms, 'near', {
        overviewColor: ocNear,
        isSmallProp,
        protoKey: meta.id,
      });
      // 关键：强制走与真实大地图完全相同的 Three.InstancedMesh + localMatrix 路径
      const im = new THREE.InstancedMesh(o.geometry, mat, 1);
      im.setMatrixAt(0, o.matrixWorld.clone());
      im.instanceMatrix.needsUpdate = true;
      im.castShadow = true;
      im.receiveShadow = true;
      im.frustumCulled = false;
      holder.add(im);
      subMeshes.push({geo: o.geometry, mat: o.matrixWorld.clone()});

      o.geometry.computeBoundingBox();
      if (o.geometry.boundingBox) {
        const b = o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
        worldBox.union(b);
      }
    });

    if (worldBox.isEmpty()) {
      worldBox.setFromCenterAndSize(new THREE.Vector3(0, 1, 0), new THREE.Vector3(2, 2, 2));
    }
    const center = worldBox.getCenter(new THREE.Vector3());
    const size = worldBox.getSize(new THREE.Vector3());
    const radius = Math.max(0.8, size.length() * 0.5);
    const halfSpan = Math.max(size.x * 0.72, size.z * 0.72, size.y * 0.88, radius * 0.85, 1.0);

    camera.left = -halfSpan;
    camera.right = halfSpan;
    camera.top = halfSpan;
    camera.bottom = -halfSpan;
    camera.updateProjectionMatrix();

    const dist = Math.max(80, radius * 6.0);
    camera.position.set(
      center.x + Math.sin(YAW) * Math.cos(TILT) * dist,
      center.y + Math.sin(TILT) * dist,
      center.z + Math.cos(YAW) * Math.cos(TILT) * dist
    );
    camera.lookAt(center);

    // 与主场景 updateCameraAndSun 同方向光照
    const shadowHalf = Math.max(6.0, radius * 1.45);
    sun.target.position.copy(center);
    sun.position.set(
      center.x - shadowHalf * 0.85,
      center.y + shadowHalf * 1.45,
      center.z + shadowHalf * 0.55
    );
    sun.shadow.camera.left = -shadowHalf;
    sun.shadow.camera.right = shadowHalf;
    sun.shadow.camera.top = shadowHalf;
    sun.shadow.camera.bottom = -shadowHalf;
    sun.shadow.camera.near = 0.5;
    sun.shadow.camera.far = shadowHalf * 4.5;
    sun.shadow.camera.updateProjectionMatrix();

    renderer.render(scene, camera);

    gl.readPixels(0, 0, SIZE, SIZE, gl.RGBA, gl.UNSIGNED_BYTE, pxBuf);
    let fgPixels = 0;
    let blackPixels = 0;
    let darkPixels = 0;
    let lumaSum = 0;
    let totalMaxR = 0;
    for (let i = 0; i < pxBuf.length; i += 4) {
      const r = pxBuf[i], g = pxBuf[i + 1], b = pxBuf[i + 2];
      // 忽略中性浅灰背景像素（注：readPixels 与 sRGB canvas 值略有 1-2 误差）
      const bgDiff = Math.abs(r - BG_R) + Math.abs(g - BG_G) + Math.abs(b - BG_B);
      if (bgDiff <= 20) continue;
      fgPixels++;
      const mx = Math.max(r, g, b);
      const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      lumaSum += luma;
      totalMaxR += mx;
      if (mx < 22) blackPixels++;
      if (luma < 42) darkPixels++;
    }

    const blackRatio = fgPixels > 0 ? blackPixels / fgPixels : 0;
    const darkRatio = fgPixels > 0 ? darkPixels / fgPixels : 0;
    const meanLuma = fgPixels > 0 ? lumaSum / fgPixels : 0;

    return {
      fg_pixels: fgPixels,
      black_pixels: blackPixels,
      black_ratio: +blackRatio.toFixed(4),
      dark_ratio: +darkRatio.toFixed(4),
      mean_luma: +meanLuma.toFixed(1),
      submesh_count: subMeshes.length,
      bbox_size: [+size.x.toFixed(2), +size.y.toFixed(2), +size.z.toFixed(2)],
      data_url: canvas.toDataURL('image/png'),
    };
  },
};
window.__AUDIT_READY__ = true;
</script>
</body>
</html>"""


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_contact_sheets(records: list[dict], thumbs: dict[int, Image.Image], out_dir: Path) -> list[Path]:
    """每 25 个原型拼一张 5x5 高清联系表，标注编号、名称、纯黑占比与异常红框。"""
    cell_w, cell_h = 280, 304
    img_w, img_h = 256, 256
    font_title = load_font(13)
    font_sub = load_font(12)
    sheets = []

    for s_idx in range(0, len(records), 25):
        chunk = records[s_idx : s_idx + 25]
        sheet = Image.new("RGB", (cell_w * 5, cell_h * 5), (32, 36, 30))
        draw = ImageDraw.Draw(sheet)

        for local_i, rec in enumerate(chunk):
            gx = local_i % 5
            gy = local_i // 5
            ox = gx * cell_w
            oy = gy * cell_h
            thumb = thumbs.get(rec["prototype_index"])
            if thumb is not None:
                sheet.paste(thumb.resize((img_w, img_h)), (ox + 12, oy + 6))

            flags = rec.get("flags", [])
            is_bad = len(flags) > 0
            border_color = (235, 68, 56) if is_bad else (86, 128, 78)
            draw.rectangle([ox + 8, oy + 4, ox + cell_w - 8, oy + cell_h - 4], outline=border_color, width=3 if is_bad else 1)

            short_name = rec["name"]
            if len(short_name) > 25:
                short_name = short_name[:23] + ".."
            tag = "[DISABLED] " if not rec["enabled"] else ""
            title_line = f"#{rec['prototype_index']:03d} {tag}{short_name}"
            stat_line = (
                f"黑:{rec['black_ratio']*100:.1f}% 暗:{rec['dark_ratio']*100:.1f}% "
                f"面:{rec['cur_tris']}/{rec['src_tris']}({rec['keep_ratio']*100:.0f}%)"
            )
            title_col = (255, 168, 150) if is_bad else (228, 238, 218)
            draw.text((ox + 12, oy + 265), title_line, fill=title_col, font=font_title)
            draw.text((ox + 12, oy + 283), stat_line, fill=(195, 205, 188), font=font_sub)

        sheet_num = s_idx // 25 + 1
        out_path = out_dir / f"联系表-{sheet_num:02d}.png"
        sheet.save(out_path)
        sheets.append(out_path)
    return sheets


def shoot_world_comparison(page, out_dir: Path, port: int, manifest: dict) -> list[str]:
    """打开 8820 实景地图，自动查询 scvb 飞往典型问题建筑位置并截图存档。"""
    shot_dir = out_dir / "实景复拍"
    shot_dir.mkdir(parents=True, exist_ok=True)
    raw = zlib.decompress(SCVB_PATH.read_bytes())
    magic, ver, gx, gz, total, minx, minz, cm = struct.unpack_from("<4sIIIIfff", raw, 0)
    off = 32 + gx * gz * 8

    # 精确找出：
    # 1) idx 36 Farm_BarnO_L 且 60m 内有 idx 77 筒仓的位置（对应工程师图1/图2）
    # 2) idx 11 & 12 在建工地高楼相邻位置（对应工程师图3）
    # 3) idx 56 CCityB_Panel_1 高密度面板楼群
    # 4) idx 29 Domna 巨型工业厂房
    targets: dict[int, list[tuple[float, float]]] = {36: [], 77: [], 11: [], 12: [], 56: [], 29: []}
    for i in range(total):
        rec_off = off + i * 56
        p = struct.unpack_from("<H", raw, rec_off)[0]
        if p in targets:
            tx, ty, tz = struct.unpack_from("<3f", raw, rec_off + 44)
            if -4200 <= tx <= 4300 and -3900 <= tz <= 4600:
                targets[p].append((tx, tz))

    barn_silo_spot = (1770.4, -4043.4)
    for bx, bz in targets[36]:
        if any(math.hypot(bx - sx, bz - sz) < 50.0 for sx, sz in targets[77]):
            # 避开地图极边缘钳制区，优先选内部可直视居中的点
            if -4000 <= bx <= 4100 and -3750 <= bz <= 4400:
                barn_silo_spot = (bx, bz)
                break
    # 也备选纯 (1770.4, -4043.4) 或 (-295.1, -3816.7)
    if targets[36]:
        for bx, bz in targets[36]:
            if any(math.hypot(bx - sx, bz - sz) < 55.0 for sx, sz in targets[77]):
                barn_silo_spot = (bx, bz)
                break

    const_spot = (-2010.0, 2295.0)
    for cx, cz in targets[11]:
        if any(math.hypot(cx - cx2, cz - cz2) < 120.0 for cx2, cz2 in targets[12]):
            const_spot = (cx, cz)
            break

    panel_spot = targets[56][0] if targets[56] else (0.0, 85.0)
    domna_spot = targets[29][0] if targets[29] else (0.0, 85.0)

    shots_plan = [
        ("01-谷仓与三筒仓-Farm_BarnO_L.png", barn_silo_spot[0], barn_silo_spot[1], 145),
        ("02-双谷仓农场-Farm_BarnN.png", targets[34][0][0] if 34 in targets and targets[34] else (1562.7 if not targets[36] else barn_silo_spot[0]), targets[34][0][1] if 34 in targets and targets[34] else barn_silo_spot[1], 175),
        ("03-工地在建双塔-construction.png", const_spot[0], const_spot[1], 210),
        ("04-高层面板楼-CCityB_Panel_1.png", panel_spot[0], panel_spot[1], 195),
        ("05-工业厂房-Domna.png", domna_spot[0], domna_spot[1], 220),
    ]
    # 顺手查一个 Farm_BarnN (idx 34)
    barn_n_coords = []
    for i in range(total):
        rec_off = off + i * 56
        p = struct.unpack_from("<H", raw, rec_off)[0]
        if p == 34:
            tx, ty, tz = struct.unpack_from("<3f", raw, rec_off + 44)
            if -4000 <= tx <= 4100 and -3750 <= tz <= 4400:
                barn_n_coords.append((tx, tz))
                break
    if barn_n_coords:
        shots_plan[1] = ("02-谷仓-Farm_BarnN.png", barn_n_coords[0][0], barn_n_coords[0][1], 145)
    else:
        shots_plan.pop(1)

    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(f"http://127.0.0.1:{port}/?aspect=16:9", wait_until="domcontentloaded")
    page.wait_for_function("() => window.__SCV_MAP__ && window.__SCV_MAP__.isFullPrecisionReady() === true", timeout=60000)

    saved = []
    for fname, x, z, span in shots_plan:
        page.evaluate("async ([x, z, span]) => { await window.__SCV_MAP__.setCameraView(x, z, span, 0); }", [x, z, span])
        # 等待该位置附近的 near-kit GLB 加载并稳定渲染 3 帧
        page.evaluate(
            """() => new Promise(resolve => {
                let n = 0;
                const step = () => { if (++n >= 8) resolve(true); else requestAnimationFrame(step); };
                requestAnimationFrame(step);
            })"""
        )
        time.sleep(0.35)
        out_file = shot_dir / fname
        page.screenshot(path=str(out_file))
        saved.append(str(out_file.relative_to(ROOT).as_posix()))
        print(f"  [实景复拍] ({x:.1f}, {z:.1f}) span={span} -> {out_file.name}")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="全量 125 栋建筑真机 WebGL 外观体检与联系表生成")
    parser.add_argument("--phase", choices=["before", "after"], default="after")
    parser.add_argument("--port", type=int, default=8820)
    parser.add_argument("--shoot-world", action="store_true", help="附加飞往真实大地图典型建筑位置拍摄 680p 实景图")
    args = parser.parse_args()

    wait_port_alive(args.port)
    manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
    r14_meta = json.loads(R14_META_PATH.read_text("utf-8"))

    phase_dir = AUDIT_BASE_DIR / ("修复前" if args.phase == "before" else "修复后")
    phase_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = phase_dir / "单栋截图"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[建筑体检-{args.phase}] 1/3 静态几何与矩阵预扫描 (125 个原型)...")
    static_stats = static_geometry_audit(manifest, r14_meta)

    print(f"[建筑体检-{args.phase}] 2/3 启动 Chrome 逐一通过 InstancedMesh 真机渲染 125 个原型...")
    audit_html = build_audit_page_html()
    records = []
    thumbs: dict[int, Image.Image] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-frame-rate-limit",
                "--disable-gpu-vsync",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )
        try:
            ctx = browser.new_context(viewport={"width": 320, "height": 320}, device_scale_factor=1)
            page = ctx.new_page()

            def handle_audit_route(route):
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=audit_html)

            page.route(f"http://127.0.0.1:{args.port}/__bldg_audit__", handle_audit_route)
            page.goto(f"http://127.0.0.1:{args.port}/__bldg_audit__", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__AUDIT_READY__ === true", timeout=30000)

            for p in manifest["building_prototypes"]:
                idx = p["prototype_index"]
                st = static_stats[idx]
                res = page.evaluate("async (meta) => await window.__AUDIT_API__.renderPrototype(meta)", p)
                b64 = res["data_url"].split(",", 1)[1]
                img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
                thumbs[idx] = img
                img.save(thumbs_dir / f"{idx:03d}-{p['short_id']}.png")

                flags = []
                if p["enabled"]:
                    if res["fg_pixels"] < 12:
                        flags.append("EMPTY_RENDER")
                    if res["black_ratio"] >= 0.08:
                        flags.append("BLACK_ROOF_OR_WALL")
                    if st["has_neg_det"]:
                        flags.append("NEG_DET")
                    if st["flip_tris"] > max(15, st["cur_tris"] * 0.05):
                        flags.append("FLIP_WINDING")
                    if args.phase == "before" and st["keep_ratio"] < 0.70:
                        flags.append("HEAVY_TRUNCATED")
                    elif st["keep_ratio"] < 0.12 and st["src_tris"] > 5000:
                        flags.append("OVER_SIMPLIFIED")

                rec = {
                    "prototype_index": idx,
                    "id": p["id"],
                    "name": p["name"],
                    "enabled": p["enabled"],
                    "near_file": p["near_file"],
                    "near_kb": round(p["near_identity"]["bytes"] / 1024.0, 1),
                    **st,
                    "fg_pixels": res["fg_pixels"],
                    "black_ratio": res["black_ratio"],
                    "dark_ratio": res["dark_ratio"],
                    "mean_luma": res["mean_luma"],
                    "submesh_count": res["submesh_count"],
                    "bbox_size": res["bbox_size"],
                    "flags": flags,
                }
                records.append(rec)
                if flags or idx % 25 == 0:
                    print(
                        f"  #{idx:03d} {p['name']}: black={res['black_ratio']*100:.1f}% dark={res['dark_ratio']*100:.1f}% "
                        f"tris={st['cur_tris']}/{st['src_tris']} negDet={st['has_neg_det']} flip={st['flip_tris']} flags={flags}"
                    )

            ctx.close()

            world_shots = []
            if args.shoot_world:
                print(f"[建筑体检-{args.phase}] 2.5/3 飞往 8820 真实大地图拍摄典型同款实景对比图...")
                wctx = browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
                wpage = wctx.new_page()
                world_shots = shoot_world_comparison(wpage, AUDIT_BASE_DIR, args.port, manifest)
                wctx.close()
        finally:
            browser.close()

    print(f"[建筑体检-{args.phase}] 3/3 生成 5 张 5x5 高清联系表与体检汇总...")
    sheets = build_contact_sheets(records, thumbs, phase_dir)

    enabled_recs = [r for r in records if r["enabled"]]
    black_flagged = [r for r in enabled_recs if "BLACK_ROOF_OR_WALL" in r["flags"]]
    neg_det_flagged = [r for r in enabled_recs if r["has_neg_det"]]
    flip_flagged = [r for r in enabled_recs if "FLIP_WINDING" in r["flags"]]
    trunc_flagged = [r for r in enabled_recs if "HEAVY_TRUNCATED" in r["flags"] or "OVER_SIMPLIFIED" in r["flags"]]
    empty_flagged = [r for r in enabled_recs if "EMPTY_RENDER" in r["flags"]]
    any_flagged = [r for r in enabled_recs if r["flags"]]

    summary = {
        "phase": args.phase,
        "total_prototypes": len(records),
        "enabled_prototypes": len(enabled_recs),
        "counts": {
            "any_flagged": len(any_flagged),
            "black_flagged": len(black_flagged),
            "neg_det_flagged": len(neg_det_flagged),
            "flip_winding_flagged": len(flip_flagged),
            "heavy_truncated_flagged": len(trunc_flagged),
            "empty_render_flagged": len(empty_flagged),
        },
        "flagged_names": [{"idx": r["prototype_index"], "name": r["name"], "black_ratio": r["black_ratio"], "dark_ratio": r["dark_ratio"], "keep_ratio": r["keep_ratio"], "cur_tris": r["cur_tris"], "src_tris": r["src_tris"], "flags": r["flags"]} for r in any_flagged],
        "top_black_enabled": [
            {"idx": r["prototype_index"], "name": r["name"], "black_ratio": r["black_ratio"], "dark_ratio": r["dark_ratio"], "mean_luma": r["mean_luma"], "flags": r["flags"]}
            for r in sorted(enabled_recs, key=lambda x: (x["black_ratio"], x["dark_ratio"]), reverse=True)[:20]
        ],
        "contact_sheets": [p.relative_to(ROOT).as_posix() for p in sheets],
        "world_shots": world_shots,
        "records": records,
    }
    summary_path = phase_dir / "体检汇总.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[建筑体检-{args.phase}] 完成！汇总={summary_path} | counts={json.dumps(summary['counts'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
