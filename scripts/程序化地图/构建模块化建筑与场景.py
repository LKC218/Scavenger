"""B3阶段：构建模块化建筑与道具库(Modular Kit, 含原作改造件与自研替换件双插槽)、93,008条空间分块实例包、5,281条源碰撞与越障包络包、可编辑scene-source.json及Blender模块化模板工程。"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"

MIN_X, MAX_X = -4425.0, 4575.0
MIN_Z, MAX_Z = -4150.0, 4850.0
CHUNK_M = 125.0
GRID_N = 72


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> dict:
    st = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def build_modular_building_kit(b3_dir: Path, idx_meta: dict, r14_meta: dict) -> list[dict]:
    """
    构建 125 个非树木建筑与装饰原型的轻量模块化 GLB 库 (near-kit 与 far-kit)：
    - 移除冗余 TEXCOORD_1 光照贴图 UV；
    - 对超高面数工业/在建建筑 (10万+ 顶点) 执行 MeshoptSimplifier 减面并保持 EXTMeshoptCompression + KTX2 压缩；
    - 建立原作改造件与自研合规替换件双插槽 (slot_policy)。
    """
    kit_dir = b3_dir / "kit"
    near_kit_dir = kit_dir / "near-kit"
    far_kit_dir = kit_dir / "far-kit"
    custom_slot_dir = kit_dir / "custom-replacements/buildings"
    for d in (near_kit_dir, far_kit_dir, custom_slot_dir):
        d.mkdir(parents=True, exist_ok=True)

    r14_map = {p["id"]: p for p in r14_meta["prototypes"]}
    bldg_protos = idx_meta["prototypes"][:125]

    # 4 种山体岩石原型在原地图导出清单中缺失 overview_color ([0,0,0])，按线性渲染空间校准为沉稳花岗岩暖灰褐（严丝合缝匹配近景岩色）
    rock_color_overrides = {
        97: [0.27, 0.25, 0.22],  # decor_Rock_01_Low
        98: [0.24, 0.23, 0.20],  # decor_Rock_02_Low
        99: [0.30, 0.27, 0.23],  # decor_Rock_03_Low
        100: [0.22, 0.21, 0.19], # decor_Rock_04_Low
    }
    # 岩石、圆柱/球形储罐等明显非长方体原型在远景直接采用真实低模轮廓 far-kit，绝不用方盒敷衍
    far_kit_macro_indices = {58, 77, 97, 98, 99, 100, 104}

    tasks = []
    for p_idx, p_entry in enumerate(bldg_protos):
        full_id = p_entry["id"]
        short_id = full_id.split(":")[1]
        r14_p = r14_map.get(full_id, {})
        enabled = bool(r14_p.get("enabled", True))
        name = r14_p.get("name", p_entry.get("source_key", short_id))
        category = r14_p.get("category", p_entry.get("category", "building"))
        overview_color = list(r14_p.get("overview_color", [0.52, 0.51, 0.49]))
        if p_idx in rock_color_overrides:
            overview_color = rock_color_overrides[p_idx]
        else:
            # 兜底防护：任何非细线类原型若远景色接近纯黑，自动校准至安全自然灰褐，杜绝总览出现纯黑块
            low_name = name.lower()
            if max(overview_color) < 0.22 and not any(k in low_name for k in ("powerwire", "roadrail", "railroad")):
                overview_color = [0.46, 0.44, 0.41]
        macro_shape = "far_kit" if p_idx in far_kit_macro_indices else "box"

        # 源近中景与远景 GLB 路径
        src_near_rel = r14_p.get("middle_file") or r14_p.get("file") or p_entry["glb"]
        src_far_rel = r14_p.get("far_file") or src_near_rel
        src_full_bounds_glb = BATCH_DIR / p_entry["glb"]

        # 从未压缩源 GLB 读取精确局部包围盒 bounds_min / bounds_max
        raw = src_full_bounds_glb.read_bytes()
        j_len = struct.unpack_from("<I", raw, 12)[0]
        j = json.loads(raw[20 : 20 + j_len])
        b_min = [1e9, 1e9, 1e9]
        b_max = [-1e9, -1e9, -1e9]
        for m in j.get("meshes", []):
            for prim in m.get("primitives", []):
                pos_acc = j["accessors"][prim["attributes"]["POSITION"]]
                if "min" in pos_acc and "max" in pos_acc:
                    for axis in range(3):
                        b_min[axis] = min(b_min[axis], float(pos_acc["min"][axis]))
                        b_max[axis] = max(b_max[axis], float(pos_acc["max"][axis]))
        if b_min[0] > b_max[0]:
            b_min, b_max = [-2.0, 0.0, -2.0], [2.0, 4.0, 2.0]

        tasks.append({
            "prototype_index": p_idx,
            "id": full_id,
            "short_id": short_id,
            "name": name,
            "category": category,
            "enabled": enabled,
            "macro_shape": macro_shape,
            "overview_color": [round(float(c), 4) for c in overview_color],
            "bounds_min": [round(x, 3) for x in b_min],
            "bounds_max": [round(x, 3) for x in b_max],
            "src_near": str(BATCH_DIR / src_near_rel),
            "src_far": str(BATCH_DIR / src_far_rel),
            "dst_near": str(near_kit_dir / f"{short_id}.glb"),
            "dst_far": str(far_kit_dir / f"{short_id}.glb"),
            "near_file": f"kit/near-kit/{short_id}.glb",
            "far_file": f"kit/far-kit/{short_id}.glb",
            "original_reference_glb": p_entry["glb"],
        })

    # Node 批量处理：
    # 1) 彻底废弃暴力按面积删面的 filterLargestTriangles（避免 85 栋建筑破洞撕裂）；
    # 2) 离线烘焙节点负缩放/非均匀变换 (如 Farm_BarnO_L det=-1) 并统一修正三角形正面绕序，杜绝 InstancedMesh+DoubleSide 法线翻黑；
    # 3) <=10,000 面的原型 100% 原始拓扑保留；>10,000 面的高层/大型建筑采用保硬边焊合 + MeshoptSimplifier(['Prune']) 保拓扑减面；
    # 4) disabled 且全图零实例原型 (idx 119..124) 写入极简占位 GLB，保持 0..124 索引兼容并释放 6.28 MiB 冗余体积。
    node_kit_script = """
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const root = path.resolve(process.argv[1]);
const tasksJson = path.resolve(process.argv[2]);
const tasks = JSON.parse(fs.readFileSync(tasksJson, 'utf8'));

const modules = path.join(root, 'local-data/tools/网页资源优化/1/node_modules');
const load = p => import(pathToFileURL(path.join(modules, p)).href);
const {Document, NodeIO} = await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS, EXTMeshoptCompression} = await load('@gltf-transform/extensions/dist/index.js');
const {weld, dedup, prune, quantize} = await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier} = await load('meshoptimizer/index.js');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder,
  'meshopt.decoder': MeshoptDecoder,
});

function mat4Det3(m) {
  return (
    m[0] * (m[5] * m[10] - m[6] * m[9]) -
    m[4] * (m[1] * m[10] - m[2] * m[9]) +
    m[8] * (m[1] * m[6] - m[2] * m[5])
  );
}

function isIdentityMat4(m) {
  const id = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  for (let i = 0; i < 16; i++) {
    if (Math.abs(m[i] - id[i]) > 1e-5) return false;
  }
  return true;
}

function invertTransposeUpper3x3(m) {
  const a00 = m[0], a01 = m[4], a02 = m[8];
  const a10 = m[1], a11 = m[5], a12 = m[9];
  const a20 = m[2], a21 = m[6], a22 = m[10];
  const c00 = a11 * a22 - a12 * a21;
  const c01 = -(a10 * a22 - a12 * a20);
  const c02 = a10 * a21 - a11 * a20;
  const c10 = -(a01 * a22 - a02 * a21);
  const c11 = a00 * a22 - a02 * a20;
  const c12 = -(a00 * a21 - a01 * a20);
  const c20 = a01 * a12 - a02 * a11;
  const c21 = -(a00 * a12 - a02 * a10);
  const c22 = a00 * a11 - a01 * a10;
  let det = a00 * c00 + a01 * c01 + a02 * c02;
  if (Math.abs(det) < 1e-12) det = 1.0;
  const invDet = 1.0 / det;
  // 按行读取即 (A^-1)^T * vec 的列主序转置结果：第一行 [c00, c01, c02] * invDet
  return [
    c00 * invDet, c01 * invDet, c02 * invDet,
    c10 * invDet, c11 * invDet, c12 * invDet,
    c20 * invDet, c21 * invDet, c22 * invDet,
  ];
}

function ensureIndexed(prim, doc) {
  let idxAcc = prim.getIndices();
  if (idxAcc) return idxAcc;
  const posAcc = prim.getAttribute('POSITION');
  const vCount = posAcc ? posAcc.getCount() : 0;
  const seq = new Uint32Array(vCount);
  for (let i = 0; i < vCount; i++) seq[i] = i;
  idxAcc = doc.createAccessor().setType('SCALAR').setArray(seq);
  prim.setIndices(idxAcc);
  return idxAcc;
}

function reversePrimitiveWinding(prim, doc) {
  const idxAcc = ensureIndexed(prim, doc);
  const src = idxAcc.getArray();
  const dst = new Uint32Array(src.length);
  dst.set(src);
  for (let t = 0; t + 2 < dst.length; t += 3) {
    const tmp = dst[t + 1];
    dst[t + 1] = dst[t + 2];
    dst[t + 2] = tmp;
  }
  idxAcc.setArray(dst);
}

function bakeNodeTransformsToGeometry(doc) {
  const rootD = doc.getRoot();
  const scene = rootD.getDefaultScene() || rootD.listScenes()[0];
  if (!scene) return;

  const meshNodes = rootD.listNodes().filter(n => !!n.getMesh());
  const usedMeshes = new Set();

  for (const node of meshNodes) {
    const wm = node.getWorldMatrix();
    let mesh = node.getMesh();
    if (usedMeshes.has(mesh)) {
      const cloned = doc.createMesh(mesh.getName() + '_copy');
      for (const sp of mesh.listPrimitives()) {
        const cp = doc.createPrimitive();
        cp.setMode(sp.getMode()).setMaterial(sp.getMaterial());
        if (sp.getIndices()) cp.setIndices(sp.getIndices().clone());
        for (const sem of sp.listSemantics()) {
          cp.setAttribute(sem, sp.getAttribute(sem).clone());
        }
        cloned.addPrimitive(cp);
      }
      mesh = cloned;
      node.setMesh(mesh);
    }
    usedMeshes.add(mesh);

    if (!isIdentityMat4(wm)) {
      const det = mat4Det3(wm);
      const nMat = invertTransposeUpper3x3(wm);
      for (const prim of mesh.listPrimitives()) {
        const posAcc = prim.getAttribute('POSITION');
        if (posAcc) {
          const src = posAcc.getArray();
          const dst = new Float32Array(src.length);
          for (let i = 0; i < src.length; i += 3) {
            const x = src[i], y = src[i + 1], z = src[i + 2];
            dst[i]     = wm[0] * x + wm[4] * y + wm[8]  * z + wm[12];
            dst[i + 1] = wm[1] * x + wm[5] * y + wm[9]  * z + wm[13];
            dst[i + 2] = wm[2] * x + wm[6] * y + wm[10] * z + wm[14];
          }
          posAcc.setArray(dst);
        }
        const normAcc = prim.getAttribute('NORMAL');
        if (normAcc) {
          const src = normAcc.getArray();
          const dst = new Float32Array(src.length);
          for (let i = 0; i < src.length; i += 3) {
            const nx = src[i], ny = src[i + 1], nz = src[i + 2];
            const tx = nMat[0] * nx + nMat[1] * ny + nMat[2] * nz;
            const ty = nMat[3] * nx + nMat[4] * ny + nMat[5] * nz;
            const tz = nMat[6] * nx + nMat[7] * ny + nMat[8] * nz;
            const len = Math.hypot(tx, ty, tz);
            if (len > 1e-8) {
              dst[i] = tx / len; dst[i + 1] = ty / len; dst[i + 2] = tz / len;
            } else {
              dst[i] = 0; dst[i + 1] = 1; dst[i + 2] = 0;
            }
          }
          normAcc.setArray(dst);
        }
        if (det < 0) {
          reversePrimitiveWinding(prim, doc);
        }
      }
    }
  }

  // 展平层级并归零所有 Mesh 节点变换，清理仅用于挂载/负缩放的空节点
  for (const node of meshNodes) {
    const parent = node.getParentNode();
    if (parent) parent.removeChild(node);
    scene.addChild(node);
    node.setTranslation([0, 0, 0]).setRotation([0, 0, 0, 1]).setScale([1, 1, 1]);
  }
  const keepSet = new Set(meshNodes);
  for (const node of rootD.listNodes()) {
    if (!keepSet.has(node)) node.dispose();
  }
}

function fixInvertedWinding(prim, doc) {
  const posAcc = prim.getAttribute('POSITION');
  const normAcc = prim.getAttribute('NORMAL');
  if (!posAcc || !normAcc) return;
  const idxAcc = ensureIndexed(prim, doc);
  const pa = posAcc.getArray();
  const na = normAcc.getArray();
  const ia = idxAcc.getArray();
  const triCount = Math.floor(ia.length / 3);
  let changed = false;
  const dst = new Uint32Array(ia);

  for (let t = 0; t < triCount; t++) {
    const i0 = dst[t * 3], i1 = dst[t * 3 + 1], i2 = dst[t * 3 + 2];
    const ax = pa[i1 * 3] - pa[i0 * 3], ay = pa[i1 * 3 + 1] - pa[i0 * 3 + 1], az = pa[i1 * 3 + 2] - pa[i0 * 3 + 2];
    const bx = pa[i2 * 3] - pa[i0 * 3], by = pa[i2 * 3 + 1] - pa[i0 * 3 + 1], bz = pa[i2 * 3 + 2] - pa[i0 * 3 + 2];
    const gx = ay * bz - az * by, gy = az * bx - ax * bz, gz = ax * by - ay * bx;
    if (gx * gx + gy * gy + gz * gz < 1e-14) continue;
    const nx = na[i0 * 3] + na[i1 * 3] + na[i2 * 3];
    const ny = na[i0 * 3 + 1] + na[i1 * 3 + 1] + na[i2 * 3 + 1];
    const nz = na[i0 * 3 + 2] + na[i1 * 3 + 2] + na[i2 * 3 + 2];
    if (nx * nx + ny * ny + nz * nz < 1e-8) continue;
    if (gx * nx + gy * ny + gz * nz < 0) {
      dst[t * 3 + 1] = i2;
      dst[t * 3 + 2] = i1;
      changed = true;
    }
  }
  if (changed) idxAcc.setArray(dst);
}

function weldPreserveHardEdges(prim, posTol = 0.005, uvTol = 0.005, minNormDot = 0.82) {
  const posAcc = prim.getAttribute('POSITION');
  const normAcc = prim.getAttribute('NORMAL');
  const uvAcc = prim.getAttribute('TEXCOORD_0');
  const idxAcc = prim.getIndices();
  if (!posAcc || !idxAcc) return;
  const posArr = posAcc.getArray();
  const normArr = normAcc ? normAcc.getArray() : null;
  const uvArr = uvAcc ? uvAcc.getArray() : null;
  const idxArr = idxAcc.getArray();
  const nVerts = posAcc.getCount();
  const nIdx = idxArr.length;

  const buckets = new Map();
  const newPos = [], newNorm = normArr ? [] : null, newUv = uvArr ? [] : null;
  const newIdx = new Uint32Array(nIdx);
  const remap = new Int32Array(nVerts).fill(-1);
  let outCount = 0;

  for (let i = 0; i < nIdx; i++) {
    const vi = idxArr[i];
    let nv = remap[vi];
    if (nv === -1) {
      const px = posArr[vi * 3], py = posArr[vi * 3 + 1], pz = posArr[vi * 3 + 2];
      const u = uvArr ? uvArr[vi * 2] : 0, v = uvArr ? uvArr[vi * 2 + 1] : 0;
      const nx = normArr ? normArr[vi * 3] : 0, ny = normArr ? normArr[vi * 3 + 1] : 1, nz = normArr ? normArr[vi * 3 + 2] : 0;
      const key =
        Math.round(px / posTol) + ',' + Math.round(py / posTol) + ',' + Math.round(pz / posTol) +
        '|' + Math.round(u / uvTol) + ',' + Math.round(v / uvTol);
      let list = buckets.get(key);
      if (!list) {
        list = [];
        buckets.set(key, list);
      }
      let found = -1;
      for (const cand of list) {
        if (!normArr || (newNorm[cand * 3] * nx + newNorm[cand * 3 + 1] * ny + newNorm[cand * 3 + 2] * nz) >= minNormDot) {
          found = cand;
          break;
        }
      }
      if (found === -1) {
        nv = outCount++;
        list.push(nv);
        newPos.push(px, py, pz);
        if (newNorm) newNorm.push(nx, ny, nz);
        if (newUv) newUv.push(u, v);
      } else {
        nv = found;
      }
      remap[vi] = nv;
    }
    newIdx[i] = nv;
  }
  posAcc.setArray(new Float32Array(newPos));
  if (normAcc) normAcc.setArray(new Float32Array(newNorm));
  if (uvAcc) uvAcc.setArray(new Float32Array(newUv));
  idxAcc.setArray(newIdx);
}

function simplifyPrimTopology(prim, targetTris, maxError) {
  const posAcc = prim.getAttribute('POSITION');
  const idxAcc = prim.getIndices();
  if (!posAcc || !idxAcc) return;
  const srcIdx = idxAcc.getArray();
  const srcTris = Math.floor(srcIdx.length / 3);
  if (srcTris <= targetTris) return;
  const posF32 = new Float32Array(posAcc.getArray());
  const idxU32 = new Uint32Array(srcIdx);
  const targetIndexCount = Math.max(96, Math.floor(targetTris) * 3);
  const [dstIdx] = MeshoptSimplifier.simplify(idxU32, posF32, 3, targetIndexCount, maxError, ['Prune']);
  if (dstIdx && dstIdx.length >= 3 && dstIdx.length < idxU32.length) {
    idxAcc.setArray(dstIdx);
  }
}

async function writeEmptyDisabledPlaceholder(dstPath, overviewColor) {
  const doc = new Document();
  const buf = doc.createBuffer();
  const pos = doc.createAccessor().setBuffer(buf).setType('VEC3').setArray(new Float32Array([0, 0, 0, 0.001, 0, 0, 0, 0.001, 0]));
  const norm = doc.createAccessor().setBuffer(buf).setType('VEC3').setArray(new Float32Array([0, 1, 0, 0, 1, 0, 0, 1, 0]));
  const idx = doc.createAccessor().setBuffer(buf).setType('SCALAR').setArray(new Uint16Array([0, 1, 2]));
  const mat = doc.createMaterial('disabled_placeholder').setBaseColorFactor([overviewColor[0], overviewColor[1], overviewColor[2], 1.0]).setRoughnessFactor(0.9).setMetallicFactor(0.0);
  const prim = doc.createPrimitive().setAttribute('POSITION', pos).setAttribute('NORMAL', norm).setIndices(idx).setMaterial(mat);
  const mesh = doc.createMesh('disabled_mesh').addPrimitive(prim);
  const node = doc.createNode('disabled_node').setMesh(mesh);
  doc.createScene('Scene').addChild(node);
  await io.write(dstPath, doc);
}

async function optimizeOne(srcPath, dstPath, targetTrisCap, isFarLod, overviewColor, enabled, isFarKitMacro) {
  if (!enabled) {
    await writeEmptyDisabledPlaceholder(dstPath, overviewColor);
    return;
  }

  const doc = await io.read(srcPath);
  bakeNodeTransformsToGeometry(doc);

  let totalSrcTris = 0;
  for (const m of doc.getRoot().listMeshes()) {
    for (const p of m.listPrimitives()) {
      for (const sem of p.listSemantics()) {
        if (sem !== 'POSITION' && sem !== 'NORMAL' && sem !== 'TEXCOORD_0') {
          p.setAttribute(sem, null);
        }
      }
      fixInvertedWinding(p, doc);
      const id = p.getIndices();
      totalSrcTris += id ? Math.floor(id.getCount() / 3) : 0;

      if (isFarLod) {
        if (p.getAttribute('TEXCOORD_0')) p.setAttribute('TEXCOORD_0', null);
        const mat = p.getMaterial();
        if (mat) {
          mat.setBaseColorTexture(null);
          mat.setBaseColorFactor([overviewColor[0], overviewColor[1], overviewColor[2], 1.0]);
          mat.setRoughnessFactor(0.90);
          mat.setMetallicFactor(0.04);
        }
      }
    }
  }
  if (isFarLod) {
    for (const tex of doc.getRoot().listTextures()) tex.dispose();
  }

  if (!isFarLod) {
    // 近中景：<= 10,000 面原型 100% 原始拓扑保留；> 10,000 面保硬边焊合 + Meshopt 保拓扑减面
    if (totalSrcTris > 10000) {
      const targetTotal = Math.min(12500, Math.max(7500, Math.round(totalSrcTris * 0.35)));
      for (const m of doc.getRoot().listMeshes()) {
        for (const p of m.listPrimitives()) {
          const id = p.getIndices();
          const tc = id ? Math.floor(id.getCount() / 3) : 0;
          if (tc > 1800) {
            weldPreserveHardEdges(p, 0.005, 0.005, 0.82);
            const primTarget = Math.max(1200, Math.round(targetTotal * (tc / totalSrcTris)));
            simplifyPrimTopology(p, primTarget, 0.048);
            const afterTc = Math.floor(p.getIndices().getCount() / 3);
            if (afterTc > 20000) {
              simplifyPrimTopology(p, Math.max(primTarget, 14000), 0.068);
            }
          }
        }
      }
    }
  } else {
    // 远景 far-kit：对实际直接上屏的岩石/球形圆柱油罐保留 1200 面高清低模，其余 450 面
    const farTarget = isFarKitMacro ? 1200 : targetTrisCap;
    const farError = isFarKitMacro ? 0.065 : 0.12;
    if (totalSrcTris > farTarget) {
      for (const m of doc.getRoot().listMeshes()) {
        for (const p of m.listPrimitives()) {
          const id = p.getIndices();
          const tc = id ? Math.floor(id.getCount() / 3) : 0;
          if (tc > 120) {
            weldPreserveHardEdges(p, 0.01, 0.01, 0.78);
            const primTarget = Math.max(80, Math.round(farTarget * (tc / totalSrcTris)));
            simplifyPrimTopology(p, primTarget, farError);
          }
        }
      }
    }
  }

  await doc.transform(
    dedup(),
    weld({tolerance: 0.001}),
    prune()
  );
  // 拓扑减面与焊合之后再次全量校准三角形正面绕序，杜绝如 Farm_Log_Block 等细长圆柱体经减面产生反绕暗面
  for (const m of doc.getRoot().listMeshes()) {
    for (const p of m.listPrimitives()) {
      fixInvertedWinding(p, doc);
    }
  }
  await doc.transform(
    quantize({quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 12})
  );
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({
    method: EXTMeshoptCompression.EncoderMethod.QUANTIZE,
  });
  await io.write(dstPath, doc);
}

for (const t of tasks) {
  await optimizeOne(t.src_near, t.dst_near, 12500, false, t.overview_color, t.enabled, t.macro_shape === 'far_kit');
  await optimizeOne(t.src_far, t.dst_far, 450, true, t.overview_color, t.enabled, t.macro_shape === 'far_kit');
}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(tasks, tf, ensure_ascii=False)
        tasks_path = Path(tf.name)
    try:
        subprocess.run(
            ["node", "--input-type=module", "-e", node_kit_script, str(ROOT), str(tasks_path)],
            cwd=ROOT,
            check=True,
        )
    finally:
        tasks_path.unlink(missing_ok=True)

    records = []
    for t in tasks:
        near_p = Path(t["dst_near"])
        far_p = Path(t["dst_far"])
        records.append({
            "prototype_index": t["prototype_index"],
            "id": t["id"],
            "short_id": t["short_id"],
            "name": t["name"],
            "category": t["category"],
            "enabled": t["enabled"],
            "macro_shape": t["macro_shape"],
            "overview_color": t["overview_color"],
            "bounds_min": t["bounds_min"],
            "bounds_max": t["bounds_max"],
            "near_file": t["near_file"],
            "far_file": t["far_file"],
            "near_identity": fingerprint(near_p),
            "far_identity": fingerprint(far_p),
            "slot_policy": {
                "active_slot": "original_refactored",
                "original_reference_glb": t["original_reference_glb"],
                "custom_replacement_slot": f"kit/custom-replacements/buildings/{t['short_id']}.glb",
            },
        })
    return records


def build_buildings_and_collisions_spatial(b3_dir: Path, idx_meta: dict, col_meta: dict, proto_records: list[dict]) -> tuple[dict, dict]:
    """
    构建：
    1) instances/buildings-spatial.scvb：全图 93,008 条非树木建筑与装饰实例的 72x72 分块空间包；
    2) physics/collisions-spatial.scvc：全图 5,281 条源 Box/Capsule 碰撞体 + 72x72 直升机 30m 越障顶高包络与粗通行成本包。
    """
    inst_dir = b3_dir / "instances"
    phys_dir = b3_dir / "physics"
    inst_dir.mkdir(parents=True, exist_ok=True)
    phys_dir.mkdir(parents=True, exist_ok=True)

    src_inst_dir = BATCH_DIR / "实例分页/r01"
    src_col_dir = BATCH_DIR / "碰撞分页/r01"

    baip_dt = np.dtype([
        ("p", "<u4"),
        ("g", "<u4"),
        ("si", "<u4"),
        ("f", "<u4"),
        ("m", "<f4", (16,)),
        ("b", "<f4", (6,)),
    ])
    # 每条建筑/道具实例 56 字节：<HHHH12f (proto_u16, flags_u16, group_u16, source_idx_u16, col_major_3x4_f32[12])
    # 其中 col_major_3x4 存 m[[0,1,2, 4,5,6, 8,9,10, 12,13,14]]
    scvb_rec_dt = np.dtype([
        ("proto", "<u2"),
        ("flags", "<u2"),
        ("group", "<u2"),
        ("sidx", "<u2"),
        ("m3x4", "<f4", (12,)),
    ])

    page_map = {p["id"]: p for p in idx_meta["pages"]}
    cell_table_b = bytearray(GRID_N * GRID_N * 8)
    b_chunks = []
    cur_off_b = 0
    total_bldg_inst = 0

    # 记录每格最高建筑/树木顶高，用于直升机 30m 提前越障高度包络
    max_top_y_grid = np.full((GRID_N, GRID_N), -40.0, dtype=np.float32)

    for cz in range(GRID_N):
        for cx in range(GRID_N):
            cell_idx = cz * GRID_N + cx
            cid = f"{cx:02}-{cz:02}"
            p_info = page_map.get(cid)
            if not p_info or p_info["count"] == 0:
                struct.pack_into("<II", cell_table_b, cell_idx * 8, cur_off_b, 0)
                continue

            raw = (src_inst_dir / p_info["file"]).read_bytes()
            arr = np.frombuffer(raw, dtype=baip_dt, offset=16, count=p_info["count"])
            # 更新该格所有实例 (含建筑与高大树木) 的最高世界 Y (b[:, 4] 即 maxY)
            if len(arr) > 0:
                max_top_y_grid[cz, cx] = float(np.max(arr["b"][:, 4]))

            b_mask = arr["p"] < 125
            b_arr = arr[b_mask]
            n_b = len(b_arr)
            if n_b == 0:
                struct.pack_into("<II", cell_table_b, cell_idx * 8, cur_off_b, 0)
                continue

            packed = np.empty(n_b, dtype=scvb_rec_dt)
            packed["proto"] = b_arr["p"].astype(np.uint16)
            packed["flags"] = b_arr["f"].astype(np.uint16)
            packed["group"] = b_arr["g"].astype(np.uint16)
            packed["sidx"] = (b_arr["si"] & 0xFFFF).astype(np.uint16)
            m16 = b_arr["m"]
            packed["m3x4"] = m16[:, [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14]]

            b_chunks.append(packed.tobytes())
            struct.pack_into("<II", cell_table_b, cell_idx * 8, cur_off_b, n_b)
            cur_off_b += n_b
            total_bldg_inst += n_b

    scvb_header = struct.pack("<4sIIIIfff", b"SCVB", 1, GRID_N, GRID_N, total_bldg_inst, MIN_X, MIN_Z, CHUNK_M)
    raw_scvb = scvb_header + bytes(cell_table_b) + b"".join(b_chunks)
    comp_scvb = zlib.compress(raw_scvb, level=9)
    scvb_path = inst_dir / "buildings-spatial.scvb"
    scvb_path.write_bytes(comp_scvb)

    inst_res = {
        "file": "instances/buildings-spatial.scvb",
        "total_building_instances": total_bldg_inst,
        "raw_bytes": len(raw_scvb),
        "compressed_bytes": len(comp_scvb),
        "identity": fingerprint(scvb_path),
    }

    # --- 构建 physics/collisions-spatial.scvc ---
    baco_dt = np.dtype([
        ("group", "<u4"),
        ("index", "<u4"),
        ("prototype", "<u4"),
        ("component", "<u4"),
        ("flags", "<u4"),
        ("matrix", "<f4", (16,)),
        ("params", "<f4", (4,)),
        ("bounds", "<f4", (6,)),
    ])
    col_chunk_map = {c["id"]: c for c in col_meta["chunks"]}
    cell_table_c = bytearray(GRID_N * GRID_N * 8)
    c_chunks = []
    cur_off_c = 0
    total_box_capsule = 0

    for cz in range(GRID_N):
        for cx in range(GRID_N):
            cell_idx = cz * GRID_N + cx
            cid = f"{cx:02}-{cz:02}"
            c_info = col_chunk_map.get(cid)
            if not c_info or c_info["count"] == 0:
                struct.pack_into("<II", cell_table_c, cell_idx * 8, cur_off_c, 0)
                continue

            raw = (src_col_dir / c_info["file"]).read_bytes()
            arr = np.frombuffer(raw, dtype=baco_dt, offset=16, count=c_info["count"])
            # 保留所有非树干碰撞体 ((flags & 3) != 3，即 Box=1 和 Capsule=2)；树干圆柱由 trees-spatial.scvt 实时派生
            non_trunk = (arr["flags"] & 3) != 3
            nt_arr = arr[non_trunk]
            n_c = len(nt_arr)
            if n_c == 0:
                struct.pack_into("<II", cell_table_c, cell_idx * 8, cur_off_c, 0)
                continue

            c_chunks.append(nt_arr.tobytes())
            struct.pack_into("<II", cell_table_c, cell_idx * 8, cur_off_c, n_c)
            cur_off_c += n_c
            total_box_capsule += n_c

    # 8 种树的树干碰撞半径与高度 (灌木为 0)
    trunk_radii = np.array([0.45, 0.36, 0.34, 0.16, 0.49, 0.15, 0.0, 0.0], dtype=np.float32)
    trunk_heights = np.array([8.0, 7.5, 6.0, 2.4, 3.0, 2.7, 0.0, 0.0], dtype=np.float32)

    scvc_header = struct.pack("<4sIIIIfff", b"SCVC", 1, GRID_N, GRID_N, total_box_capsule, MIN_X, MIN_Z, CHUNK_M)
    raw_scvc = (
        scvc_header
        + trunk_radii.tobytes()
        + trunk_heights.tobytes()
        + max_top_y_grid.tobytes()
        + bytes(cell_table_c)
        + b"".join(c_chunks)
    )
    comp_scvc = zlib.compress(raw_scvc, level=9)
    scvc_path = phys_dir / "collisions-spatial.scvc"
    scvc_path.write_bytes(comp_scvc)

    # 复制 125m 分区粗通行图供编辑器与自动物理派生增量更新
    nav_src = BATCH_DIR / "通行粗层/r01/分区通行.json"
    nav_dst = phys_dir / "coarse-navigation.json"
    shutil.copy2(nav_src, nav_dst)

    phys_res = {
        "file": "physics/collisions-spatial.scvc",
        "coarse_nav_file": "physics/coarse-navigation.json",
        "total_box_capsule_colliders": total_box_capsule,
        "derived_tree_trunks_supported": 343234,
        "raw_bytes": len(raw_scvc),
        "compressed_bytes": len(comp_scvc),
        "identity": fingerprint(scvc_path),
        "coarse_nav_identity": fingerprint(nav_dst),
    }
    return inst_res, phys_res


def build_editable_scene_source_and_blender_template(b3_dir: Path, proto_records: list[dict], tree_records: list[dict]) -> dict:
    """生成可编辑的 scene-source.json 与 Blender 模块化场景模板工程。"""
    scene_source = {
        "schema": "scavenger-editable-scene-source-v1",
        "version": "V001",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "bounds": {"minX": MIN_X, "maxX": MAX_X, "minZ": MIN_Z, "maxZ": MAX_Z, "minH": -40.0, "maxH": 120.0},
        "grid": {"cells": [GRID_N, GRID_N], "chunk_m": CHUNK_M, "local_nav_step_m": 4.0, "helicopter_lookahead_m": 30.0},
        "building_kit_count": len(proto_records),
        "tree_kit_count": len(tree_records),
        "slot_switch_global": "original_refactored",
        "manual_overrides": {
            "added_instances": [],
            "modified_instances": {},
            "deleted_instances": [],
            "terrain_height_strokes": [],
            "splat_strokes": [],
            "modified_road_splines": {},
        },
    }
    src_json_path = b3_dir / "scene-source.json"
    src_json_path.write_text(json.dumps(scene_source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 生成 Blender 模块化场景编辑模板工程 (.blend)
    blender_dir = b3_dir / "blender"
    blender_dir.mkdir(parents=True, exist_ok=True)
    blend_path = blender_dir / "模块化场景编辑模板.blend"

    local_cfg = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))
    blender_exe = local_cfg.get("tools", {}).get("Blender", "")

    if blender_exe and Path(blender_exe).is_file():
        blender_py = """
import bpy, json, sys
from pathlib import Path

out_blend = Path(sys.argv[sys.argv.index('--') + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = 'Scavenger_Modular_Map_V001'
scene['scv_schema'] = 'scavenger-editable-scene-source-v1'
scene['scv_bounds'] = json.dumps({'minX': -4425.0, 'maxX': 4575.0, 'minZ': -4150.0, 'maxZ': 4850.0})

for col_name in ('01_Terrain_Reference', '02_Road_Splines', '03_Modular_Buildings_Kit', '04_Modular_Trees_Kit'):
    col = bpy.data.collections.new(col_name)
    scene.collection.children.link(col)

# 创建一个示例模块化建筑锚点 Empty，演示自定义属性契约
b_col = bpy.data.collections['03_Modular_Buildings_Kit']
anchor = bpy.data.objects.new('Sample_Building_Anchor', None)
anchor.empty_display_type = 'CUBE'
anchor.empty_display_size = 6.0
anchor.location = (0.0, 85.0, 0.0)
anchor['scv_prototype_id'] = 'model:BA_SCENE_caa7d50dc404abb1ba90_v001'
anchor['scv_slot'] = 'original_refactored'
b_col.objects.link(anchor)

bpy.ops.wm.save_as_mainfile(filepath=str(out_blend), compress=True)
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as bf:
            bf.write(blender_py)
            bf_path = Path(bf.name)
        try:
            subprocess.run(
                [blender_exe, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(bf_path), "--", str(blend_path)],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
        finally:
            bf_path.unlink(missing_ok=True)

    return {
        "scene_source_file": "scene-source.json",
        "scene_source_identity": fingerprint(src_json_path),
        "blender_template_file": "blender/模块化场景编辑模板.blend" if blend_path.is_file() else None,
        "blender_template_identity": fingerprint(blend_path) if blend_path.is_file() else None,
    }


def main() -> None:
    t0 = time.time()
    if B3_DIR.exists():
        shutil.rmtree(B3_DIR)
    B3_DIR.mkdir(parents=True, exist_ok=True)

    idx_meta = json.loads((BATCH_DIR / "实例分页/r01/实例索引.json").read_text("utf-8"))
    col_meta = json.loads((BATCH_DIR / "碰撞分页/r01/碰撞索引.json").read_text("utf-8"))
    r14_meta = json.loads((BATCH_DIR / "运行接入/r14/地图运行.json").read_text("utf-8"))
    b2_manifest = json.loads((B2_DIR / "植被与草丛清单.json").read_text("utf-8"))

    proto_records = build_modular_building_kit(B3_DIR, idx_meta, r14_meta)
    inst_res, phys_res = build_buildings_and_collisions_spatial(B3_DIR, idx_meta, col_meta, proto_records)
    editor_res = build_editable_scene_source_and_blender_template(B3_DIR, proto_records, b2_manifest["tree_prototypes"])

    near_kit_bytes = sum(r["near_identity"]["bytes"] for r in proto_records)
    far_kit_bytes = sum(r["far_identity"]["bytes"] for r in proto_records)
    inst_bytes = inst_res["identity"]["bytes"]
    phys_bytes = phys_res["identity"]["bytes"] + phys_res["coarse_nav_identity"]["bytes"]
    editor_bytes = editor_res["scene_source_identity"]["bytes"] + (
        editor_res["blender_template_identity"]["bytes"] if editor_res["blender_template_identity"] else 0
    )

    # 注意：运行时按需加载，主用模块化套件为 near_kit (或 far_kit)；统计 B3 全套资产 (near_kit + far_kit + instances + physics + scene_source)
    total_b3_bytes = near_kit_bytes + far_kit_bytes + inst_bytes + phys_bytes + editor_bytes
    total_b3_mib = round(total_b3_bytes / (1024 * 1024), 3)

    manifest = {
        "schema": "scavenger-procedural-map-b3-modular-kit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bounds": {"minX": MIN_X, "maxX": MAX_X, "minZ": MIN_Z, "maxZ": MAX_Z},
        "building_prototypes": proto_records,
        "spatial_building_instances": inst_res,
        "spatial_collisions_and_nav": phys_res,
        "editable_source": editor_res,
        "budget_summary": {
            "near_kit_bytes": near_kit_bytes,
            "near_kit_mib": round(near_kit_bytes / (1024 * 1024), 3),
            "far_kit_bytes": far_kit_bytes,
            "far_kit_mib": round(far_kit_bytes / (1024 * 1024), 3),
            "instances_bytes": inst_bytes,
            "instances_mib": round(inst_bytes / (1024 * 1024), 3),
            "physics_and_nav_bytes": phys_bytes,
            "physics_and_nav_mib": round(phys_bytes / (1024 * 1024), 3),
            "editor_source_bytes": editor_bytes,
            "editor_source_mib": round(editor_bytes / (1024 * 1024), 3),
            "total_b3_bytes": total_b3_bytes,
            "total_b3_mib": total_b3_mib,
            "limit_mib": 35.0,
            "within_budget": total_b3_mib <= 35.0,
        },
        "elapsed_s": round(time.time() - t0, 2),
    }

    manifest_path = B3_DIR / "模块化建筑与场景清单.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["budget_summary"], ensure_ascii=False))
    if not manifest["budget_summary"]["within_budget"]:
        raise RuntimeError(f"B3 模块化建筑与场景总体积超出 35 MiB 门限: {total_b3_mib} MiB")


if __name__ == "__main__":
    main()
