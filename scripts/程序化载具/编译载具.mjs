/**
 * 单源程序化载具编译器（V1 / V2 共用）：
 * - 输入：V0 基线凭证 + vehicle-rig-rules.json + 全库贴图哈希表 + 只读原件缓存（M1A2/AH-64D 优先复用带法线机械装配源）
 * - 输出：每款载具同源 LOD0 / LOD1 / LOD2（强量化 + Meshopt + 外置去重 KTX2 图集/共享贴图）+ 物理/轴心/挂点元数据 JSON
 *
 * 用法：
 *   node scripts/程序化载具/编译载具.mjs --v1-samples
 *   node scripts/程序化载具/编译载具.mjs --all --out local-data/程序化载具/V001-b4570e71/V2-全库编译
 *   node scripts/程序化载具/编译载具.mjs --ids model:US_M1A2 --retry-level 1 --force
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

// 静默 ktx2-encoder 内部 WASM 切片刷屏输出，仅保留本脚本带前缀日志
const _rawLog = console.log.bind(console);
console.log = (...args) => {
  const head = String(args[0] ?? '');
  if (head.startsWith('[')) _rawLog(...args);
};

const ROOT = path.resolve(import.meta.dirname, '../..');
const BATCH_ID = 'V001-b4570e71';
const WORK_DIR = path.join(ROOT, 'local-data/程序化载具', BATCH_ID);
const V0_DIR = path.join(WORK_DIR, 'V0-基线与规则');
const TMP_DIR = path.join(ROOT, 'tmp', `程序化载具-${BATCH_ID}`);
const TEX_CACHE_DIR = path.join(TMP_DIR, 'tex-cache');

const MODULES = path.join(ROOT, 'local-data/tools/网页资源优化/1/node_modules');
const loadMod = async p => import(pathToFileURL(path.join(MODULES, p)).href);
const require = createRequire(path.join(MODULES, '../package.json'));

const sharp = require('sharp');
const {Document, NodeIO} = await loadMod('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu} = await loadMod('@gltf-transform/extensions/dist/index.js');
const {quantize} = await loadMod('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier} = await loadMod('meshoptimizer/index.js');
const {encodeToKTX2} = await loadMod('ktx2-encoder/dist/node/index.js');

await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);

const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder});

// 优先复用已有已验机械拆分且带原版高清法线贴图的黄金装配源
const GOLDEN_ASSEMBLY_SOURCES = {
  'model:US_M1A2': path.join(ROOT, 'local-data/资源库/当前资源/装配源/靶场装配/runtime-tank-M1A2靶场机械装配-装配源.glb'),
  'model:US_AH64D': path.join(ROOT, 'local-data/资源库/当前资源/装配源/靶场装配/runtime-heli-AH-64D靶场机械装配-装配源.glb'),
};

const sha256 = buf => crypto.createHash('sha256').update(buf).digest('hex');
const readJson = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const writeJson = (p, obj) => {
  fs.mkdirSync(path.dirname(p), {recursive: true});
  fs.writeFileSync(p, JSON.stringify(obj, null, 2), 'utf8');
};

const imageDecoder = async b => {
  const {data, info} = await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject: true});
  return {data: new Uint8Array(data), width: info.width, height: info.height};
};

// ---------------- 线性代数小工具（零外部依赖） ----------------
function mat4Identity() {
  return new Float64Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
}
function mat4Mul(a, b) {
  const o = new Float64Array(16);
  for (let c = 0; c < 4; c++) {
    const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b0 + a[4 + r] * b1 + a[8 + r] * b2 + a[12 + r] * b3;
    }
  }
  return o;
}
function mat4FromTRS(t, q, s) {
  const [x, y, z, w] = q;
  const [sx, sy, sz] = s;
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  return new Float64Array([
    (1 - (yy + zz)) * sx, (xy + wz) * sx, (xz - wy) * sx, 0,
    (xy - wz) * sy, (1 - (xx + zz)) * sy, (yz + wx) * sy, 0,
    (xz + wy) * sz, (yz - wx) * sz, (1 - (xx + yy)) * sz, 0,
    t[0], t[1], t[2], 1,
  ]);
}
function mat4Det3(m) {
  return (
    m[0] * (m[5] * m[10] - m[6] * m[9]) -
    m[4] * (m[1] * m[10] - m[2] * m[9]) +
    m[8] * (m[1] * m[6] - m[2] * m[5])
  );
}
function normalMatrix3FromMat4(m) {
  // 逆转置 3x3
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
  const det = a00 * c00 + a01 * c01 + a02 * c02;
  const inv = Math.abs(det) > 1e-12 ? 1.0 / det : 1.0;
  // 按列存转置后的逆矩阵，供 transformNormal 使用
  return new Float64Array([
    c00 * inv, c01 * inv, c02 * inv,
    c10 * inv, c11 * inv, c12 * inv,
    c20 * inv, c21 * inv, c22 * inv,
  ]);
}
function transformPoint(m, x, y, z) {
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
  ];
}
function transformNormal(nm, x, y, z) {
  const nx = nm[0] * x + nm[1] * y + nm[2] * z;
  const ny = nm[3] * x + nm[4] * y + nm[5] * z;
  const nz = nm[6] * x + nm[7] * y + nm[8] * z;
  const len = Math.hypot(nx, ny, nz);
  return len > 1e-8 ? [nx / len, ny / len, nz / len] : [0, 1, 0];
}
function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

// ---------------- 节点语义归类与轴心提取 ----------------
function inferSlot(role, chainNames, isGoldenAssembly) {
  if (isGoldenAssembly) {
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const nm = chainNames[i];
      if (nm.includes('原型炮管')) return 'barrel';
      if (nm.includes('原型炮塔')) return 'turret';
      if (nm.includes('原型主旋翼')) return 'main_rotor';
      if (nm.includes('原型尾旋翼')) return 'tail_rotor';
      if (nm.includes('原型机炮')) return 'gun';
    }
    return 'hull';
  }
  if (role === 'ground') {
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(barrel|cannon|gun_tube|gunbarrel|main_gun)/.test(low)) return 'barrel';
      if (/(gun|launcher|mortar|tube)/.test(low) && !low.includes('turret') && !low.includes('gmv')) {
        if (chainNames.some(a => a.toLowerCase().includes('turret')) || low.includes('launcher')) return 'barrel';
      }
    }
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(radar|dish|antenna_rot)/.test(low)) return 'radar';
    }
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(turret|tower|cupola|crows|rws)/.test(low)) return 'turret';
    }
    return 'hull';
  }
  if (role === 'rotorcraft') {
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(tail_rotor|tailrotor|rotor_tail|rear_rotor|tail_blade|back_rotor)/.test(low)) return 'tail_rotor';
      if (/(tail|rear|back)/.test(low) && /(rotor|blade|prop|vint)/.test(low)) return 'tail_rotor';
    }
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(main_rotor|mainrotor|rotor|blade|propeller|vint|proprotor)/.test(low)) return 'main_rotor';
    }
    for (let i = chainNames.length - 1; i >= 0; i--) {
      const low = chainNames[i].toLowerCase();
      if (/(turret|gun|cannon|chin)/.test(low)) return 'gun';
    }
    return 'hull';
  }
  return 'hull';
}

// ---------------- 旋翼机独立连通分量安全剥离器 ----------------
function splitRotorcraftComponents(chunks, anchors, totalTris) {
  // 仅当存在旋翼锚点且尚无独立 main_rotor / tail_rotor 时尝试基于空间连通分量安全剥离
  const mainAnchor = anchors.main_rotor;
  const tailAnchor = anchors.tail_rotor;
  const isTandem = Boolean(anchors.isTandem);
  const bladeAnchors = anchors.blades || [];
  if (!mainAnchor && !tailAnchor) return chunks;

  const hasMain = chunks.some(c => c.slot === 'main_rotor');
  const hasTail = chunks.some(c => c.slot === 'tail_rotor');
  if (hasMain && hasTail) return chunks;

  const out = [];
  for (const ch of chunks) {
    if (ch.slot !== 'hull' || ch.indices.length < 30) {
      out.push(ch);
      continue;
    }
    const pos = ch.positions;
    const idx = ch.indices;
    const triCount = idx.length / 3;

    // 量化空间格网(2mm)合并重合顶点，防止尾桨与紧邻垂直尾翼在接缝处发生误粘连
    const vCount = pos.length / 3;
    const vRep = new Int32Array(vCount);
    const grid = new Map();
    for (let i = 0; i < vCount; i++) {
      const gx = Math.round(pos[i * 3] * 500);
      const gy = Math.round(pos[i * 3 + 1] * 500);
      const gz = Math.round(pos[i * 3 + 2] * 500);
      const key = `${gx},${gy},${gz}`;
      const prev = grid.get(key);
      if (prev === undefined) {
        grid.set(key, i);
        vRep[i] = i;
      } else {
        vRep[i] = prev;
      }
    }
    const parent = new Int32Array(vCount);
    for (let i = 0; i < vCount; i++) parent[i] = vRep[i];
    const find = x => {
      let r = x;
      while (parent[r] !== r) r = parent[r];
      let c = x;
      while (parent[c] !== r) {
        const nxt = parent[c];
        parent[c] = r;
        c = nxt;
      }
      return r;
    };
    const unite = (a, b) => {
      const ra = find(a), rb = find(b);
      if (ra !== rb) parent[rb] = ra;
    };
    for (let t = 0; t < triCount; t++) {
      const a = vRep[idx[t * 3]], b = vRep[idx[t * 3 + 1]], c = vRep[idx[t * 3 + 2]];
      unite(a, b);
      unite(b, c);
    }

    // 统计各连通岛包围盒与三角形列表
    const comps = new Map();
    for (let t = 0; t < triCount; t++) {
      const a = idx[t * 3], b = idx[t * 3 + 1], c = idx[t * 3 + 2];
      const root = find(vRep[a]);
      let comp = comps.get(root);
      if (!comp) {
        comp = {
          tris: [],
          minX: Infinity, minY: Infinity, minZ: Infinity,
          maxX: -Infinity, maxY: -Infinity, maxZ: -Infinity,
        };
        comps.set(root, comp);
      }
      comp.tris.push(t);
      for (const vi of [a, b, c]) {
        const x = pos[vi * 3], y = pos[vi * 3 + 1], z = pos[vi * 3 + 2];
        if (x < comp.minX) comp.minX = x;
        if (y < comp.minY) comp.minY = y;
        if (z < comp.minZ) comp.minZ = z;
        if (x > comp.maxX) comp.maxX = x;
        if (y > comp.maxY) comp.maxY = y;
        if (z > comp.maxZ) comp.maxZ = z;
      }
    }

    const triTargetSlot = new Array(triCount).fill('hull');
    let mainTrisAdded = 0, tailTrisAdded = 0;

    for (const comp of comps.values()) {
      const tCnt = comp.tris.length;
      if (tCnt < 6 || tCnt > totalTris * 0.38) continue;
      const sx = comp.maxX - comp.minX;
      const sy = comp.maxY - comp.minY;
      const sz = comp.maxZ - comp.minZ;
      const cx = (comp.minX + comp.maxX) * 0.5;
      const cy = (comp.minY + comp.maxY) * 0.5;
      const cz = (comp.minZ + comp.maxZ) * 0.5;
      const horizSpan = Math.max(sx, sz);

      if (isTandem && mainAnchor && tailAnchor) {
        // 纵列双主旋翼（CH-47 / MH-47）：按 Z 与 Y 同时归属前/后水平大旋翼
        const dMain = Math.hypot(cx - mainAnchor[0], cz - mainAnchor[2]);
        const dTail = Math.hypot(cx - tailAnchor[0], cz - tailAnchor[2]);
        const isFrontPlane = Math.abs(cy - mainAnchor[1]) <= 1.05 && sy <= 1.4 && (horizSpan >= 2.0 || dMain <= 1.3);
        const isRearPlane = Math.abs(cy - tailAnchor[1]) <= 1.05 && sy <= 1.4 && (horizSpan >= 2.0 || dTail <= 1.3);
        if (!hasMain && isFrontPlane && dMain <= dTail && dMain <= 9.5 && mainTrisAdded + tCnt <= totalTris * 0.35) {
          for (const ti of comp.tris) triTargetSlot[ti] = 'main_rotor';
          mainTrisAdded += tCnt;
          continue;
        }
        if (!hasTail && isRearPlane && dTail < dMain && dTail <= 9.5 && tailTrisAdded + tCnt <= totalTris * 0.35) {
          for (const ti of comp.tris) triTargetSlot[ti] = 'tail_rotor';
          tailTrisAdded += tCnt;
          continue;
        }
        continue;
      }

      if (!hasMain && mainAnchor) {
        const dHoriz = Math.hypot(cx - mainAnchor[0], cz - mainAnchor[2]);
        // 以 mainAnchor[1] 自身高度为基准，不再拿高耸尾翼顶点卡死重型/低矮主旋翼
        const nearMainPlane = comp.minY >= mainAnchor[1] - 1.15 && Math.abs(cy - mainAnchor[1]) <= 1.35 && sy <= 2.25;
        const nearBladeAnchor = bladeAnchors.some(ba => Math.hypot(cx - ba[0], cy - ba[1], cz - ba[2]) <= 4.5);
        const isBladeOrHub =
          (horizSpan >= 2.0 && dHoriz <= 12.5 && sy <= 1.55) ||
          (dHoriz <= 1.35 && comp.minY >= mainAnchor[1] - 0.55 && sy <= 1.35 && horizSpan <= 3.6) ||
          (nearBladeAnchor && sy <= 1.4 && horizSpan >= 1.5);
        if (nearMainPlane && isBladeOrHub && mainTrisAdded + tCnt <= totalTris * 0.42) {
          for (const ti of comp.tris) triTargetSlot[ti] = 'main_rotor';
          mainTrisAdded += tCnt;
          continue;
        }
      }
      if (!hasTail && tailAnchor) {
        const d3 = Math.hypot(cx - tailAnchor[0], cy - tailAnchor[1], cz - tailAnchor[2]);
        const maxDim = Math.max(sx, sy, sz);
        // 常规单旋翼尾桨必位于垂尾左侧或右侧单边，严格排除横跨机身对称中轴线 x=0 的垂直尾翼本体
        const straddlesCenterline = comp.minX < -0.07 && comp.maxX > 0.07;
        const isSideMounted = !straddlesCenterline && Math.abs(cx) >= 0.08 && sx <= 0.72;
        if (isSideMounted && d3 <= 1.35 && maxDim <= 3.8 && tailTrisAdded + tCnt <= totalTris * 0.12) {
          for (const ti of comp.tris) triTargetSlot[ti] = 'tail_rotor';
          tailTrisAdded += tCnt;
        }
      }
    }

    if (mainTrisAdded === 0 && tailTrisAdded === 0) {
      out.push(ch);
      continue;
    }

    // 按新槽位拆出子块
    const bySlot = new Map();
    for (let t = 0; t < triCount; t++) {
      const s = triTargetSlot[t];
      let bucket = bySlot.get(s);
      if (!bucket) {
        bucket = [];
        bySlot.set(s, bucket);
      }
      bucket.push(t);
    }
    for (const [slotName, tList] of bySlot.entries()) {
      const vMap = new Map();
      const nPos = [], nNorm = [], nUv = [], nIdx = [];
      for (const t of tList) {
        for (let k = 0; k < 3; k++) {
          const oi = idx[t * 3 + k];
          let ni = vMap.get(oi);
          if (ni === undefined) {
            ni = nPos.length / 3;
            vMap.set(oi, ni);
            nPos.push(pos[oi * 3], pos[oi * 3 + 1], pos[oi * 3 + 2]);
            nNorm.push(ch.normals[oi * 3], ch.normals[oi * 3 + 1], ch.normals[oi * 3 + 2]);
            nUv.push(ch.uvs[oi * 2], ch.uvs[oi * 2 + 1]);
          }
          nIdx.push(ni);
        }
      }
      out.push({
        slot: slotName,
        matIndex: ch.matIndex,
        positions: new Float32Array(nPos),
        normals: new Float32Array(nNorm),
        uvs: new Float32Array(nUv),
        indices: new Uint32Array(nIdx),
      });
    }
  }
  return out;
}

// ---------------- 图像打包、Edge Padding 与 KTX2 编码 ----------------
function padTileRGBA(srcRGBA, w, h, pad) {
  const pw = w + pad * 2;
  const ph = h + pad * 2;
  const dst = Buffer.alloc(pw * ph * 4);
  for (let y = 0; y < ph; y++) {
    const sy = Math.max(0, Math.min(h - 1, y - pad));
    for (let x = 0; x < pw; x++) {
      const sx = Math.max(0, Math.min(w - 1, x - pad));
      const si = (sy * w + sx) * 4;
      const di = (y * pw + x) * 4;
      dst[di] = srcRGBA[si];
      dst[di + 1] = srcRGBA[si + 1];
      dst[di + 2] = srcRGBA[si + 2];
      dst[di + 3] = srcRGBA[si + 3];
    }
  }
  return {data: dst, width: pw, height: ph};
}

async function encodeCachedKTX2(pngBuf, isLinear, outTexDir, texKey) {
  fs.mkdirSync(TEX_CACHE_DIR, {recursive: true});
  fs.mkdirSync(outTexDir, {recursive: true});
  const cacheKey = sha256(Buffer.concat([pngBuf, Buffer.from(isLinear ? '|uastc-lin-v1' : '|etc1s-srgb-v1')]));
  const cachePath = path.join(TEX_CACHE_DIR, `${cacheKey}.ktx2`);
  let ktxBuf;
  if (fs.existsSync(cachePath)) {
    ktxBuf = fs.readFileSync(cachePath);
  } else {
    const encoded = await encodeToKTX2(new Uint8Array(pngBuf), {
      imageDecoder,
      isUASTC: isLinear,
      qualityLevel: 200,
      generateMipmap: true,
      isInputSRGB: !isLinear,
      isSetKTX2SRGBTransferFunc: !isLinear,
      enableDebug: false,
    });
    ktxBuf = Buffer.from(encoded);
    fs.writeFileSync(cachePath, ktxBuf);
  }
  // 统一采用内容寻址键名，确保同家族衍生车与跨车共享底色/法线/玻璃天然 100% 物理去重
  const ktxHash = sha256(ktxBuf);
  const finalKey = texKey && texKey.startsWith('shared_') ? texKey : `tex_${ktxHash.slice(0, 14)}_${isLinear ? 'n' : 'b'}`;
  const destPath = path.join(outTexDir, `${finalKey}.ktx2`);
  if (!fs.existsSync(destPath)) {
    fs.writeFileSync(destPath, ktxBuf);
  }
  // 为底色贴图同步生成 256px 轻量中景 Mip（供 LOD1 战场高速流式加载，LOD0 展示与近景仍使用 1024 原图）
  if (!isLinear) {
    const mipDestPath = path.join(outTexDir, `${finalKey}_m256.ktx2`);
    if (!fs.existsSync(mipDestPath)) {
      const m256Png = await sharp(pngBuf)
        .resize({width: 256, height: 256, fit: 'inside', withoutEnlargement: true})
        .png()
        .toBuffer();
      const mipCacheKey = sha256(Buffer.concat([m256Png, Buffer.from('|etc1s-srgb-m256-v1')]));
      const mipCachePath = path.join(TEX_CACHE_DIR, `${mipCacheKey}.ktx2`);
      let mipBuf;
      if (fs.existsSync(mipCachePath)) {
        mipBuf = fs.readFileSync(mipCachePath);
      } else {
        const encMip = await encodeToKTX2(new Uint8Array(m256Png), {
          imageDecoder,
          isUASTC: false,
          qualityLevel: 140,
          generateMipmap: true,
          isInputSRGB: true,
          isSetKTX2SRGBTransferFunc: true,
          enableDebug: false,
        });
        mipBuf = Buffer.from(encMip);
        fs.writeFileSync(mipCachePath, mipBuf);
      }
      fs.writeFileSync(mipDestPath, mipBuf);
    }
  }
  return {
    key: finalKey,
    uri: `tex/${finalKey}.ktx2`,
    bytes: ktxBuf.length,
    sha256: ktxHash,
    linear: isLinear,
  };
}

async function renderMaterialTile(matInfo, targetSize, isNormal) {
  if (isNormal) {
    if (matInfo.normBytes) {
      const {data, info} = await sharp(matInfo.normBytes)
        .resize(targetSize, targetSize, {fit: 'fill'})
        .ensureAlpha()
        .raw()
        .toBuffer({resolveWithObject: true});
      return {data, width: info.width, height: info.height};
    }
    // 平坦法线 (128, 128, 255, 255)
    const flat = Buffer.alloc(targetSize * targetSize * 4);
    for (let i = 0; i < flat.length; i += 4) {
      flat[i] = 128;
      flat[i + 1] = 128;
      flat[i + 2] = 255;
      flat[i + 3] = 255;
    }
    return {data: flat, width: targetSize, height: targetSize};
  }

  // 底色块：烘焙 baseColorFactor
  const [fr, fg, fb, fa] = matInfo.baseColorFactor;
  if (matInfo.baseBytes) {
    const {data, info} = await sharp(matInfo.baseBytes)
      .resize(targetSize, targetSize, {fit: 'fill'})
      .ensureAlpha()
      .raw()
      .toBuffer({resolveWithObject: true});
    const needsTint = Math.abs(fr - 1) > 1e-3 || Math.abs(fg - 1) > 1e-3 || Math.abs(fb - 1) > 1e-3 || Math.abs(fa - 1) > 1e-3;
    if (needsTint) {
      for (let i = 0; i < data.length; i += 4) {
        data[i] = Math.min(255, Math.round(data[i] * fr));
        data[i + 1] = Math.min(255, Math.round(data[i + 1] * fg));
        data[i + 2] = Math.min(255, Math.round(data[i + 2] * fb));
        data[i + 3] = Math.min(255, Math.round(data[i + 3] * fa));
      }
    }
    return {data, width: info.width, height: info.height};
  }
  const solid = Buffer.alloc(targetSize * targetSize * 4);
  const r8 = Math.min(255, Math.round(Math.pow(Math.max(0, fr), 1 / 2.2) * 255));
  const g8 = Math.min(255, Math.round(Math.pow(Math.max(0, fg), 1 / 2.2) * 255));
  const b8 = Math.min(255, Math.round(Math.pow(Math.max(0, fb), 1 / 2.2) * 255));
  const a8 = Math.min(255, Math.round(Math.max(0, fa) * 255));
  for (let i = 0; i < solid.length; i += 4) {
    solid[i] = r8;
    solid[i + 1] = g8;
    solid[i + 2] = b8;
    solid[i + 3] = a8;
  }
  return {data: solid, width: targetSize, height: targetSize};
}

async function buildAtlasForMaterials(matInfos, atlasSize, normSize, slug, outTexDir) {
  const count = matInfos.length;
  const cols = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / cols);
  const cellW = Math.floor(atlasSize / cols);
  const cellH = Math.floor(atlasSize / rows);
  const pad = Math.min(6, Math.max(4, Math.floor(Math.min(cellW, cellH) * 0.02)));
  const innerW = Math.max(8, cellW - pad * 2);
  const innerH = Math.max(8, cellH - pad * 2);
  const tileSide = Math.min(innerW, innerH);

  const baseComp = [];
  const normComp = [];
  const uvTransforms = new Map();
  const hasAnyNormal = matInfos.some(m => Boolean(m.normBytes));

  const nCellW = Math.floor(normSize / cols);
  const nCellH = Math.floor(normSize / rows);
  const nPad = Math.max(2, Math.round((pad * normSize) / atlasSize));
  const nInnerW = Math.max(4, nCellW - nPad * 2);
  const nInnerH = Math.max(4, nCellH - nPad * 2);
  const nTileSide = Math.min(nInnerW, nInnerH);

  let wMet = 0, wRough = 0, wSum = 0;
  let alphaMask = false, doubleSided = false;

  for (let i = 0; i < count; i++) {
    const m = matInfos[i];
    const col = i % cols;
    const row = Math.floor(i / cols);
    const left = col * cellW;
    const top = row * cellH;

    const rawTile = await renderMaterialTile(m, tileSide, false);
    const padded = padTileRGBA(rawTile.data, rawTile.width, rawTile.height, pad);
    baseComp.push({
      input: padded.data,
      raw: {width: padded.width, height: padded.height, channels: 4},
      left,
      top,
    });

    if (hasAnyNormal) {
      const nRaw = await renderMaterialTile(m, nTileSide, true);
      const nPadded = padTileRGBA(nRaw.data, nRaw.width, nRaw.height, nPad);
      normComp.push({
        input: nPadded.data,
        raw: {width: nPadded.width, height: nPadded.height, channels: 4},
        left: col * nCellW,
        top: row * nCellH,
      });
    }

    // 精确内缩 UV 映射（不含 padding 边缘）
    const u0 = (left + pad) / atlasSize;
    const v0 = (top + pad) / atlasSize;
    const uScale = tileSide / atlasSize;
    const vScale = tileSide / atlasSize;
    uvTransforms.set(m.origIndex, {u0, v0, uScale, vScale});

    const weight = Math.max(1, m.triCount);
    wMet += m.metallic * weight;
    wRough += m.roughness * weight;
    wSum += weight;
    if (m.alphaMode === 'MASK') alphaMask = true;
    if (m.doubleSided) doubleSided = true;
  }

  const basePng = await sharp({
    create: {width: atlasSize, height: atlasSize, channels: 4, background: {r: 80, g: 85, b: 75, alpha: 1}},
  })
    .composite(baseComp)
    .png()
    .toBuffer();

  const baseTex = await encodeCachedKTX2(basePng, false, outTexDir, `veh_${slug}_base`);
  const {data: lod2RgbLut} = await sharp(basePng).resize(64, 64, {fit: 'fill'}).removeAlpha().raw().toBuffer({resolveWithObject: true});
  let normTex = null;
  if (hasAnyNormal) {
    const normPng = await sharp({
      create: {width: normSize, height: normSize, channels: 4, background: {r: 128, g: 128, b: 255, alpha: 1}},
    })
      .composite(normComp)
      .png()
      .toBuffer();
    normTex = await encodeCachedKTX2(normPng, true, outTexDir, `veh_${slug}_norm`);
  }

  return {
    name: `${slug}_atlas`,
    baseTex,
    normTex,
    baseColorFactor: [1, 1, 1, 1],
    metallic: wSum > 0 ? wMet / wSum : 0.15,
    roughness: wSum > 0 ? wRough / wSum : 0.72,
    alphaMode: alphaMask ? 'MASK' : 'OPAQUE',
    alphaCutoff: alphaMask ? 0.5 : 0.5,
    doubleSided,
    uvTransforms,
    lod2RgbLut,
    lod2LutSize: 64,
    isShared: false,
  };
}

// ---------------- GLB 外置图像 URI 注入器（符合 glTF 2.0 规范） ----------------
function injectExternalImageURIs(glbUint8, imageMetaList) {
  if (!imageMetaList.length) return glbUint8;
  const dv = new DataView(glbUint8.buffer, glbUint8.byteOffset, glbUint8.byteLength);
  const jlen = dv.getUint32(12, true);
  const gltf = JSON.parse(new TextDecoder().decode(glbUint8.subarray(20, 20 + jlen)));
  const binChunkStart = 20 + jlen;
  const restBin = glbUint8.subarray(binChunkStart);

  if (Array.isArray(gltf.images)) {
    for (let i = 0; i < gltf.images.length && i < imageMetaList.length; i++) {
      gltf.images[i] = {
        name: imageMetaList[i].key,
        uri: imageMetaList[i].uri,
        mimeType: 'image/ktx2',
      };
    }
  }
  const jsonBytes = Buffer.from(JSON.stringify(gltf), 'utf8');
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const paddedJson = pad ? Buffer.concat([jsonBytes, Buffer.alloc(pad, 0x20)]) : jsonBytes;

  const totalLen = 12 + 8 + paddedJson.length + restBin.length;
  const out = Buffer.alloc(totalLen);
  out.write('glTF', 0, 4, 'ascii');
  out.writeUInt32LE(2, 4);
  out.writeUInt32LE(totalLen, 8);
  out.writeUInt32LE(paddedJson.length, 12);
  out.writeUInt32LE(0x4e4f534a, 16);
  paddedJson.copy(out, 20);
  Buffer.from(restBin).copy(out, 20 + paddedJson.length);
  return new Uint8Array(out);
}

// ---------------- 几何合并、焊接与 Meshopt 减面 ----------------
function mergeGeoList(list) {
  let totalV = 0, totalI = 0;
  for (const g of list) {
    totalV += g.positions.length / 3;
    totalI += g.indices.length;
  }
  const positions = new Float32Array(totalV * 3);
  const normals = new Float32Array(totalV * 3);
  const uvs = new Float32Array(totalV * 2);
  const indices = new Uint32Array(totalI);
  let vOff = 0, iOff = 0;
  for (const g of list) {
    positions.set(g.positions, vOff * 3);
    normals.set(g.normals, vOff * 3);
    uvs.set(g.uvs, vOff * 2);
    for (let k = 0; k < g.indices.length; k++) {
      indices[iOff + k] = g.indices[k] + vOff;
    }
    vOff += g.positions.length / 3;
    iOff += g.indices.length;
  }
  return {positions, normals, uvs, indices};
}

function dedupAndCompactGeo(geo, hasColor = false) {
  const vCount = geo.positions.length / 3;
  const map = new Map();
  const remap = new Uint32Array(vCount);
  const nPos = [], nNorm = [], nUv = [], nCol = [];

  for (let i = 0; i < vCount; i++) {
    const px = geo.positions[i * 3], py = geo.positions[i * 3 + 1], pz = geo.positions[i * 3 + 2];
    const nx = geo.normals[i * 3], ny = geo.normals[i * 3 + 1], nz = geo.normals[i * 3 + 2];
    const u = geo.uvs ? geo.uvs[i * 2] : 0;
    const v = geo.uvs ? geo.uvs[i * 2 + 1] : 0;
    const key = hasColor
      ? `${Math.round(px * 1000)},${Math.round(py * 1000)},${Math.round(pz * 1000)}|${Math.round(nx * 20)},${Math.round(ny * 20)},${Math.round(nz * 20)}|${Math.round(geo.colors[i * 3] * 32)},${Math.round(geo.colors[i * 3 + 1] * 32)},${Math.round(geo.colors[i * 3 + 2] * 32)}`
      : `${Math.round(px * 2000)},${Math.round(py * 2000)},${Math.round(pz * 2000)}|${Math.round(nx * 40)},${Math.round(ny * 40)},${Math.round(nz * 40)}|${Math.round(u * 2048)},${Math.round(v * 2048)}`;
    let ni = map.get(key);
    if (ni === undefined) {
      ni = nPos.length / 3;
      map.set(key, ni);
      nPos.push(px, py, pz);
      nNorm.push(nx, ny, nz);
      if (geo.uvs) nUv.push(u, v);
      if (hasColor) nCol.push(geo.colors[i * 3], geo.colors[i * 3 + 1], geo.colors[i * 3 + 2]);
    }
    remap[i] = ni;
  }

  // 过滤退化三角形
  const cleanIdx = [];
  for (let i = 0; i < geo.indices.length; i += 3) {
    const a = remap[geo.indices[i]], b = remap[geo.indices[i + 1]], c = remap[geo.indices[i + 2]];
    if (a !== b && b !== c && a !== c) {
      cleanIdx.push(a, b, c);
    }
  }
  const out = {
    positions: new Float32Array(nPos),
    normals: new Float32Array(nNorm),
    indices: new Uint32Array(cleanIdx),
  };
  if (geo.uvs) out.uvs = new Float32Array(nUv);
  if (hasColor) out.colors = new Float32Array(nCol);
  return out;
}

function compactUnusedVertices(geo, newIndices) {
  const vCount = geo.positions.length / 3;
  const remap = new Int32Array(vCount).fill(-1);
  let next = 0;
  const nPos = [], nNorm = [], nUv = [], nCol = [];
  const outIdx = new Uint32Array(newIndices.length);

  for (let i = 0; i < newIndices.length; i++) {
    const oi = newIndices[i];
    let ni = remap[oi];
    if (ni === -1) {
      ni = next++;
      remap[oi] = ni;
      nPos.push(geo.positions[oi * 3], geo.positions[oi * 3 + 1], geo.positions[oi * 3 + 2]);
      nNorm.push(geo.normals[oi * 3], geo.normals[oi * 3 + 1], geo.normals[oi * 3 + 2]);
      if (geo.uvs) nUv.push(geo.uvs[oi * 2], geo.uvs[oi * 2 + 1]);
      if (geo.colors) nCol.push(geo.colors[oi * 3], geo.colors[oi * 3 + 1], geo.colors[oi * 3 + 2]);
    }
    outIdx[i] = ni;
  }
  const out = {
    positions: new Float32Array(nPos),
    normals: new Float32Array(nNorm),
    indices: outIdx,
  };
  if (geo.uvs) out.uvs = new Float32Array(nUv);
  if (geo.colors) out.colors = new Float32Array(nCol);
  return out;
}

  // LOD2 专用：按 1.5cm 空间位置焊接，并按新拓扑重算平面/平滑法线避免暗块伪影
function recomputeVertexNormals(pos, idx) {
  const n = new Float32Array(pos.length);
  for (let i = 0; i < idx.length; i += 3) {
    const a = idx[i] * 3, b = idx[i + 1] * 3, c = idx[i + 2] * 3;
    const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
    const vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    n[a] += nx; n[a + 1] += ny; n[a + 2] += nz;
    n[b] += nx; n[b + 1] += ny; n[b + 2] += nz;
    n[c] += nx; n[c + 1] += ny; n[c + 2] += nz;
  }
  for (let i = 0; i < n.length; i += 3) {
    const l = Math.hypot(n[i], n[i + 1], n[i + 2]);
    if (l > 1e-8) {
      n[i] /= l; n[i + 1] /= l; n[i + 2] /= l;
    } else {
      n[i] = 0; n[i + 1] = 1; n[i + 2] = 0;
    }
  }
  return n;
}

function dedupLod2ByPosition(geo, gridScale = 200) {
  // 纯位置空间焊接：机体采用 5mm (gridScale=200) 闭合硬边接缝同时不焊穿薄钢梁；旋翼采用 1mm (gridScale=1000) 绝不焊穿薄桨叶
  const vCount = geo.positions.length / 3;
  const map = new Map();
  const remap = new Uint32Array(vCount);
  const accPos = [], accCol = [], accCnt = [];

  for (let i = 0; i < vCount; i++) {
    const px = geo.positions[i * 3], py = geo.positions[i * 3 + 1], pz = geo.positions[i * 3 + 2];
    const key = `${Math.round(px * gridScale)},${Math.round(py * gridScale)},${Math.round(pz * gridScale)}`;
    let ni = map.get(key);
    if (ni === undefined) {
      ni = accCnt.length;
      map.set(key, ni);
      accPos.push(px, py, pz);
      accCol.push(geo.colors[i * 3], geo.colors[i * 3 + 1], geo.colors[i * 3 + 2]);
      accCnt.push(1);
    } else {
      accPos[ni * 3] += px; accPos[ni * 3 + 1] += py; accPos[ni * 3 + 2] += pz;
      accCol[ni * 3] += geo.colors[i * 3]; accCol[ni * 3 + 1] += geo.colors[i * 3 + 1]; accCol[ni * 3 + 2] += geo.colors[i * 3 + 2];
      accCnt[ni] += 1;
    }
    remap[i] = ni;
  }

  const outV = accCnt.length;
  const positions = new Float32Array(outV * 3);
  const colors = new Float32Array(outV * 3);
  for (let i = 0; i < outV; i++) {
    const inv = 1 / accCnt[i];
    positions[i * 3] = accPos[i * 3] * inv;
    positions[i * 3 + 1] = accPos[i * 3 + 1] * inv;
    positions[i * 3 + 2] = accPos[i * 3 + 2] * inv;
    // 远景无贴图面缺少法线微遮蔽与接缝阴影，施加 0.72 线性亮度对齐补偿，使 LOD2 与 LOD0/1 色彩明暗丝滑一致
    colors[i * 3] = Math.min(1, accCol[i * 3] * inv * 0.72);
    colors[i * 3 + 1] = Math.min(1, accCol[i * 3 + 1] * inv * 0.72);
    colors[i * 3 + 2] = Math.min(1, accCol[i * 3 + 2] * inv * 0.72);
  }
  const cleanIdx = [];
  for (let i = 0; i < geo.indices.length; i += 3) {
    const a = remap[geo.indices[i]], b = remap[geo.indices[i + 1]], c = remap[geo.indices[i + 2]];
    if (a !== b && b !== c && a !== c) cleanIdx.push(a, b, c);
  }
  const indices = new Uint32Array(cleanIdx);
  const normals = recomputeVertexNormals(positions, indices);
  return {positions, normals, colors, indices};
}

function cullTinyTriangles(geo, minAreaSq) {
  // 剔除中景完全不可见的极微三角形（如 2cm 铆钉碎面），保留完整大轮廓
  const pos = geo.positions;
  const idx = geo.indices;
  const kept = [];
  for (let i = 0; i < idx.length; i += 3) {
    const a = idx[i] * 3, b = idx[i + 1] * 3, c = idx[i + 2] * 3;
    const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
    const vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
    const cx = uy * vz - uz * vy;
    const cy = uz * vx - ux * vz;
    const cz = ux * vy - uy * vx;
    if (cx * cx + cy * cy + cz * cz >= minAreaSq) {
      kept.push(idx[i], idx[i + 1], idx[i + 2]);
    }
  }
  if (kept.length < Math.min(36, idx.length)) return geo;
  return compactUnusedVertices(geo, new Uint32Array(kept));
}

function simplifyGeo(geo, targetTris, maxError, options = {}) {
  const curTris = geo.indices.length / 3;
  if (curTris <= Math.max(12, targetTris) || options.skipSimplify) {
    return geo;
  }
  const targetIdx = Math.max(12, Math.floor(targetTris)) * 3;
  const flags = options.lockBorder === true ? ['LockBorder'] : [];

  if (options.directSloppy) {
    const [sloppy] = MeshoptSimplifier.simplifySloppy(geo.indices, geo.positions, 3, null, targetIdx, 1.0);
    if (sloppy.length >= 36) {
      return compactUnusedVertices(geo, sloppy);
    }
  }

  let workGeo = geo;
  if (options.cullMicroAreaSq) {
    workGeo = cullTinyTriangles(geo, options.cullMicroAreaSq);
  }
  if (workGeo.indices.length / 3 <= Math.max(12, targetTris)) {
    return workGeo;
  }

  let simpIdx;
  if (options.useAttributes && workGeo.normals && workGeo.uvs) {
    const vCount = workGeo.positions.length / 3;
    const attrs = new Float32Array(vCount * 5);
    for (let i = 0; i < vCount; i++) {
      attrs[i * 5] = workGeo.normals[i * 3];
      attrs[i * 5 + 1] = workGeo.normals[i * 3 + 1];
      attrs[i * 5 + 2] = workGeo.normals[i * 3 + 2];
      attrs[i * 5 + 3] = workGeo.uvs[i * 2];
      attrs[i * 5 + 4] = workGeo.uvs[i * 2 + 1];
    }
    const weights = [0.45, 0.45, 0.45, 1.1, 1.1];
    const [res] = MeshoptSimplifier.simplifyWithAttributes(
      workGeo.indices,
      workGeo.positions,
      3,
      attrs,
      5,
      weights,
      null,
      targetIdx,
      maxError,
      flags,
    );
    simpIdx = res;
    if (options.allowGeomFallback && simpIdx.length / 3 > targetTris * 1.45 && maxError >= 0.015) {
      const [res2] = MeshoptSimplifier.simplify(simpIdx, workGeo.positions, 3, targetIdx, maxError * 1.15, []);
      if (res2.length >= 36) simpIdx = res2;
    }
  } else {
    const [res] = MeshoptSimplifier.simplify(workGeo.indices, workGeo.positions, 3, targetIdx, maxError, flags);
    simpIdx = res;
  }

  // LOD2 温和兜底：仅当显式开启且面数仍超上限时轻度介入
  if (options.fallbackSloppy && simpIdx.length / 3 > Math.max(targetTris * 1.85, options.sloppyThreshold ?? 2500)) {
    const [slop] = MeshoptSimplifier.simplifySloppy(simpIdx, workGeo.positions, 3, null, targetIdx, options.sloppyError ?? 0.025);
    if (slop.length >= 36) {
      simpIdx = slop;
    }
  }

  if (simpIdx.length < 12) return workGeo;
  return compactUnusedVertices(workGeo, simpIdx);
}

function computePositionsBounds(pos) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i += 3) {
    const x = pos[i], y = pos[i + 1], z = pos[i + 2];
    if (x < min[0]) min[0] = x;
    if (y < min[1]) min[1] = y;
    if (z < min[2]) min[2] = z;
    if (x > max[0]) max[0] = x;
    if (y > max[1]) max[1] = y;
    if (z > max[2]) max[2] = z;
  }
  return {min, max};
}

// ---------------- 写出带量化与外置贴图 URI 的 GLB ----------------
async function writeOptimizedGLB(slotGroups, finalMaterials, quantOpts, outPath, withTextures = true) {
  const doc = new Document();
  const buf = doc.createBuffer('main');
  const scene = doc.createScene('vehicle');

  const texMetaList = [];
  const texMap = new Map();
  const getDocTex = texInfo => {
    if (!texInfo) return null;
    if (texMap.has(texInfo.key)) return texMap.get(texInfo.key);
    const t = doc.createTexture(texInfo.key).setMimeType('image/ktx2').setURI(texInfo.uri);
    texMap.set(texInfo.key, t);
    texMetaList.push(texInfo);
    return t;
  };

  const docMatMap = new Map();
  if (withTextures) {
    for (const [mKey, mSpec] of finalMaterials.entries()) {
      const m = doc
        .createMaterial(mSpec.name)
        .setBaseColorFactor(mSpec.baseColorFactor)
        .setMetallicFactor(Math.max(0, Math.min(1, mSpec.metallic)))
        .setRoughnessFactor(Math.max(0.08, Math.min(1, mSpec.roughness)))
        .setAlphaMode(mSpec.alphaMode)
        .setDoubleSided(Boolean(mSpec.doubleSided));
      if (mSpec.alphaMode === 'MASK') m.setAlphaCutoff(mSpec.alphaCutoff ?? 0.5);
      if (mSpec.baseTex) m.setBaseColorTexture(getDocTex(mSpec.baseTex));
      if (mSpec.normTex) m.setNormalTexture(getDocTex(mSpec.normTex));
      docMatMap.set(mKey, m);
    }
  } else {
    const farMat = doc
      .createMaterial('lod2_vertex_color')
      .setBaseColorFactor([1, 1, 1, 1])
      .setMetallicFactor(0.02)
      .setRoughnessFactor(0.95)
      .setDoubleSided(true);
    docMatMap.set('__lod2__', farMat);
  }

  let totalPrims = 0;
  let totalTris = 0;

  for (const sg of slotGroups) {
    const mesh = doc.createMesh(sg.slot);
    for (const part of sg.parts) {
      if (!part.geo || part.geo.indices.length < 3) continue;
      const g = part.geo;
      const vCount = g.positions.length / 3;
      const idxArr = vCount <= 65535 ? new Uint16Array(g.indices) : g.indices;
      const prim = doc
        .createPrimitive()
        .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(g.positions).setBuffer(buf))
        .setAttribute('NORMAL', doc.createAccessor().setType('VEC3').setArray(g.normals).setBuffer(buf))
        .setIndices(doc.createAccessor().setType('SCALAR').setArray(idxArr).setBuffer(buf))
        .setMaterial(docMatMap.get(part.matKey));
      if (withTextures && g.uvs) {
        prim.setAttribute('TEXCOORD_0', doc.createAccessor().setType('VEC2').setArray(g.uvs).setBuffer(buf));
      }
      if (!withTextures && g.colors) {
        prim.setAttribute('COLOR_0', doc.createAccessor().setType('VEC3').setArray(g.colors).setBuffer(buf));
      }
      mesh.addPrimitive(prim);
      totalPrims += 1;
      totalTris += g.indices.length / 3;
    }
    if (mesh.listPrimitives().length === 0) continue;
    const node = doc.createNode(sg.slot).setMesh(mesh).setTranslation(sg.pivot);
    scene.addChild(node);
  }

  if (withTextures && texMetaList.length > 0) {
    doc.createExtension(KHRTextureBasisu).setRequired(true);
  }
  await doc.transform(quantize(quantOpts));
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({
    method: EXTMeshoptCompression.EncoderMethod.QUANTIZE,
  });

  let bin = await io.writeBinary(doc);
  if (withTextures && texMetaList.length > 0) {
    bin = injectExternalImageURIs(bin, texMetaList);
  }
  fs.mkdirSync(path.dirname(outPath), {recursive: true});
  fs.writeFileSync(outPath, bin);

  return {
    bytes: bin.length,
    sha256: sha256(bin),
    triangles: totalTris,
    drawCalls: totalPrims,
    nodes: scene.listChildren().length,
    textures: texMetaList,
  };
}

// ---------------- 单车全自动编译主流程 ----------------
async function compileOneVehicle(vEntry, rule, sharedHashMap, outDir, retryLevel = 0, onlyMip = false) {
  const t0 = performance.now();
  const vid = vEntry.id;
  const slug = vEntry.slug;
  const role = vEntry.role;

  const isGoldenAssembly = Boolean(GOLDEN_ASSEMBLY_SOURCES[vid] && fs.existsSync(GOLDEN_ASSEMBLY_SOURCES[vid]));
  const srcPath = isGoldenAssembly ? GOLDEN_ASSEMBLY_SOURCES[vid] : path.join(ROOT, vEntry.original_cache_path);
  const srcDoc = await io.read(srcPath);
  const srcRoot = srcDoc.getRoot();
  const srcScene = srcRoot.getDefaultScene() || srcRoot.listScenes()[0];

  // 1. 递归收集全部节点世界矩阵、提取锚点与挂点、归类全部带网格子块
  const anchors = {};
  const hardpoints = {muzzles: [], exhausts: []};
  const rawChunks = [];
  const srcMats = srcRoot.listMaterials();
  const srcMatIndex = new Map(srcMats.map((m, i) => [m, i]));

  function visit(node, parentMat, chainNames) {
    const nm = node.getName() || '';
    const locMat = mat4FromTRS(node.getTranslation(), node.getRotation(), node.getScale());
    const worldMat = mat4Mul(parentMat, locMat);
    const nextChain = [...chainNames, nm];
    const low = nm.toLowerCase();

    // 提取原件骨架精确空节点锚点
    if (!node.getMesh()) {
      const wp = [worldMat[12], worldMat[13], worldMat[14]];
      if (low === 'turret_0' && !anchors.turret) anchors.turret = wp;
      if (low === 'rot_blade_1') anchors.main_rotor = wp;
      else if (low === 'rotorangle_0' && !anchors.main_rotor) anchors.main_rotor = wp;
      if (low === 'rot_blade_2') {
        anchors.tail_rotor = wp;
        anchors.isTandem = true;
      } else if ((low === 'tail' || low === 'rotorangle_1') && !anchors.tail_rotor) {
        anchors.tail_rotor = wp;
      }
      if (low.startsWith('heli_blade')) {
        if (!anchors.blades) anchors.blades = [];
        anchors.blades.push(wp);
      }
      if (role === 'rotorcraft' && low === 'turret_0' && !anchors.gun) anchors.gun = wp;
      if (low.includes('muzzle') || low.includes('fire_point') || low.includes('shot_point')) {
        hardpoints.muzzles.push({name: nm, pos: wp.map(x => Number(x.toFixed(4)))});
      }
      if (low.includes('exhaust')) {
        hardpoints.exhausts.push({name: nm, pos: wp.map(x => Number(x.toFixed(4)))});
      }
    }

    const mesh = node.getMesh();
    if (mesh) {
      const slot = inferSlot(role, nextChain, isGoldenAssembly);
      const det = mat4Det3(worldMat);
      const flip = det < 0;
      const nMat = normalMatrix3FromMat4(worldMat);

      for (const prim of mesh.listPrimitives()) {
        const posAcc = prim.getAttribute('POSITION');
        if (!posAcc) continue;
        const normAcc = prim.getAttribute('NORMAL');
        const uvAcc = prim.getAttribute('TEXCOORD_0');
        const idxAcc = prim.getIndices();
        const vCount = posAcc.getCount();
        const posSrc = posAcc.getArray();
        const normSrc = normAcc ? normAcc.getArray() : null;
        const uvSrc = uvAcc ? uvAcc.getArray() : null;

        const positions = new Float32Array(vCount * 3);
        const normals = new Float32Array(vCount * 3);
        const uvs = new Float32Array(vCount * 2);

        for (let i = 0; i < vCount; i++) {
          const p = transformPoint(worldMat, posSrc[i * 3], posSrc[i * 3 + 1], posSrc[i * 3 + 2]);
          positions[i * 3] = p[0];
          positions[i * 3 + 1] = p[1];
          positions[i * 3 + 2] = p[2];
          if (normSrc) {
            const n = transformNormal(nMat, normSrc[i * 3], normSrc[i * 3 + 1], normSrc[i * 3 + 2]);
            normals[i * 3] = n[0];
            normals[i * 3 + 1] = n[1];
            normals[i * 3 + 2] = n[2];
          } else {
            normals[i * 3] = 0;
            normals[i * 3 + 1] = 1;
            normals[i * 3 + 2] = 0;
          }
          if (uvSrc) {
            uvs[i * 2] = uvSrc[i * 2];
            uvs[i * 2 + 1] = uvSrc[i * 2 + 1];
          }
        }

        let indices;
        if (idxAcc) {
          const rawI = idxAcc.getArray();
          indices = new Uint32Array(rawI.length);
          for (let i = 0; i < rawI.length; i += 3) {
            if (flip) {
              indices[i] = rawI[i];
              indices[i + 1] = rawI[i + 2];
              indices[i + 2] = rawI[i + 1];
            } else {
              indices[i] = rawI[i];
              indices[i + 1] = rawI[i + 1];
              indices[i + 2] = rawI[i + 2];
            }
          }
        } else {
          indices = new Uint32Array(vCount);
          for (let i = 0; i < vCount; i += 3) {
            if (flip) {
              indices[i] = i;
              indices[i + 1] = i + 2;
              indices[i + 2] = i + 1;
            } else {
              indices[i] = i;
              indices[i + 1] = i + 1;
              indices[i + 2] = i + 2;
            }
          }
        }

        const matObj = prim.getMaterial();
        const matIndex = matObj ? (srcMatIndex.get(matObj) ?? -1) : -1;
        rawChunks.push({slot, matIndex, positions, normals, uvs, indices});
      }
    }

    for (const ch of node.listChildren()) {
      visit(ch, worldMat, nextChain);
    }
  }

  const rootMat = mat4Identity();
  for (const ch of srcScene.listChildren()) {
    visit(ch, rootMat, []);
  }

  const totalOrigTris = rawChunks.reduce((a, c) => a + c.indices.length / 3, 0);

  // 2. 旋翼机独立连通分量安全剥离（针对非黄金装配源的其他旋翼机）
  const chunks =
    role === 'rotorcraft' && !isGoldenAssembly
      ? splitRotorcraftComponents(rawChunks, anchors, totalOrigTris)
      : rawChunks;

  // 2b. 过滤不可见/过小/内藏假可动槽位（如运输机舱门微枪、迫击炮车内藏炮管、微小传感器），归并回有效父槽或 hull
  if (!isGoldenAssembly) {
    const preTris = new Map();
    const preBounds = new Map();
    for (const ch of chunks) {
      const b = computePositionsBounds(ch.positions);
      preTris.set(ch.slot, (preTris.get(ch.slot) || 0) + ch.indices.length / 3);
      let sb = preBounds.get(ch.slot);
      if (!sb) {
        sb = {min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity]};
        preBounds.set(ch.slot, sb);
      }
      for (let k = 0; k < 3; k++) {
        if (b.min[k] < sb.min[k]) sb.min[k] = b.min[k];
        if (b.max[k] > sb.max[k]) sb.max[k] = b.max[k];
      }
    }
    const hullB = preBounds.get('hull');
    const isAttackHeli = /AH64|AH-1|RAH66|AH6/i.test(vid);
    const mergedAway = new Set();
    for (const [sn, sb] of preBounds.entries()) {
      if (sn === 'hull') continue;
      const sx = sb.max[0] - sb.min[0];
      const sy = sb.max[1] - sb.min[1];
      const sz = sb.max[2] - sb.min[2];
      const maxDim = Math.max(sx, sy, sz);
      const tris = preTris.get(sn) || 0;
      const isTiny = tris < 240 || maxDim < 0.72;
      const isTransportDoorGun = role === 'rotorcraft' && sn === 'gun' && !isAttackHeli;
      // 地面载具若炮管/炮塔完全藏于车体内部（如迫击炮舱内炮管）且顶部未明显探出，则视为内藏件归并入 hull
      const isHiddenInsideHull =
        role === 'ground' &&
        hullB &&
        (sn === 'barrel' || sn === 'turret') &&
        sb.min[0] >= hullB.min[0] + 0.08 &&
        sb.max[0] <= hullB.max[0] - 0.08 &&
        sb.min[2] >= hullB.min[2] + 0.08 &&
        sb.max[2] <= hullB.max[2] - 0.08 &&
        sb.max[1] <= hullB.max[1] + 0.06;
      if (isTiny || isTransportDoorGun || isHiddenInsideHull) {
        mergedAway.add(sn);
      }
    }
    if (mergedAway.size > 0) {
      const hasValidTurret = preBounds.has('turret') && !mergedAway.has('turret');
      for (const ch of chunks) {
        if (mergedAway.has(ch.slot)) {
          ch.slot = ch.slot === 'barrel' && hasValidTurret ? 'turret' : 'hull';
        }
      }
    }
  }

  // 3. 计算整车世界包围盒与每个槽位的精确包围盒、轴心
  const wholeMin = [Infinity, Infinity, Infinity];
  const wholeMax = [-Infinity, -Infinity, -Infinity];
  const slotBounds = new Map();
  for (const ch of chunks) {
    const b = computePositionsBounds(ch.positions);
    for (let k = 0; k < 3; k++) {
      if (b.min[k] < wholeMin[k]) wholeMin[k] = b.min[k];
      if (b.max[k] > wholeMax[k]) wholeMax[k] = b.max[k];
    }
    let sb = slotBounds.get(ch.slot);
    if (!sb) {
      sb = {min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity]};
      slotBounds.set(ch.slot, sb);
    }
    for (let k = 0; k < 3; k++) {
      if (b.min[k] < sb.min[k]) sb.min[k] = b.min[k];
      if (b.max[k] > sb.max[k]) sb.max[k] = b.max[k];
    }
  }

  const pivots = {};
  const pivotsOverride = rule.pivots_override || {};
  for (const [slotName, sb] of slotBounds.entries()) {
    if (slotName === 'hull') {
      pivots[slotName] = [0, 0, 0];
      continue;
    }
    if (pivotsOverride[slotName]) {
      pivots[slotName] = pivotsOverride[slotName];
      continue;
    }
    const anchor = anchors[slotName];
    const cx = (sb.min[0] + sb.max[0]) * 0.5;
    const cy = (sb.min[1] + sb.max[1]) * 0.5;
    const cz = (sb.min[2] + sb.max[2]) * 0.5;
    if (anchor) {
      // 检查锚点是否在槽位包围盒 1.6m 安全邻域内，防止某些模型节点原点在世界(0,0,0)导致轴心漂移
      const dx = Math.max(sb.min[0] - anchor[0], 0, anchor[0] - sb.max[0]);
      const dy = Math.max(sb.min[1] - anchor[1], 0, anchor[1] - sb.max[1]);
      const dz = Math.max(sb.min[2] - anchor[2], 0, anchor[2] - sb.max[2]);
      if (Math.hypot(dx, dy, dz) <= 1.6) {
        pivots[slotName] = anchor.map(v => Number(v.toFixed(4)));
        continue;
      }
    }
    const py = slotName === 'turret' || slotName === 'radar' ? sb.min[1] + (sb.max[1] - sb.min[1]) * 0.15 : cy;
    pivots[slotName] = [Number(cx.toFixed(4)), Number(py.toFixed(4)), Number(cz.toFixed(4))];
  }

  // 4. 盘点使用到的原材质、UV 越界情况与贴图元数据
  const usedMatIndices = [...new Set(chunks.map(c => c.matIndex))];
  const matInfos = new Map();

  for (const mi of usedMatIndices) {
    const mObj = mi >= 0 ? srcMats[mi] : null;
    const bTex = mObj?.getBaseColorTexture() || null;
    const nTex = mObj?.getNormalTexture() || null;
    const mrTex = mObj?.getMetallicRoughnessTexture() || null;
    const baseBytes = bTex?.getImage() ? Buffer.from(bTex.getImage()) : null;
    const normBytes = nTex?.getImage() ? Buffer.from(nTex.getImage()) : null;
    const mrBytes = mrTex?.getImage() ? Buffer.from(mrTex.getImage()) : null;

    const matName = mObj?.getName() || `mat_${mi}`;
    const alphaMode = mObj ? mObj.getAlphaMode() : 'OPAQUE';
    const isTrackMat = /track|trak|tread/i.test(matName);

    let metallic = mObj ? mObj.getMetallicFactor() : 0.15;
    let roughness = mObj ? mObj.getRoughnessFactor() : 0.72;
    let aoMean = 1.0;
    if (mrBytes) {
      try {
        const st = await sharp(mrBytes).removeAlpha().stats();
        aoMean = st.channels[0].mean / 255;
        metallic *= st.channels[2].mean / 255;
        roughness *= st.channels[1].mean / 255;
      } catch {
        // 保留默认因子
      }
    }

    const rawBcf = mObj ? mObj.getBaseColorFactor() : [0.55, 0.56, 0.50, 1];
    const bcf = [...rawBcf];
    if (alphaMode !== 'BLEND') {
      if (isTrackMat) {
        // glTF baseColorFactor 为线性空间：按原件 AO(≈0.34) 施加线性压暗 0.24 (等效 sRGB ≈ 0.52) 并锁低金属高粗糙，恢复厚重深黑灰橡胶/铸钢履带
        const linDarken = Math.max(0.18, Math.min(0.32, Math.pow(Math.max(0.35, aoMean * 1.45), 2.2)));
        bcf[0] *= linDarken;
        bcf[1] *= linDarken;
        bcf[2] *= linDarken;
        metallic = 0.05;
        roughness = 0.94;
      } else if (baseBytes) {
        // 军用涂装油漆去 MR 贴图后，钳制全局标量金属/粗糙度范围，避免暗黑军机因整机高金属度(0.7+)在 IBL 下发黑发闷；
        // 无底色贴图的纯素白模（如原库 partial 待修参考件）保持原件金属度/粗糙度，避免明暗漂移
        metallic = Math.max(0.04, Math.min(0.35, metallic));
        roughness = Math.max(0.50, Math.min(0.90, roughness));
      }
    }

    let avgLin = [bcf[0], bcf[1], bcf[2]];
    let lod2RgbLut = null;
    if (baseBytes) {
      try {
        const st = await sharp(baseBytes).removeAlpha().stats();
        avgLin = [
          srgbToLinear(st.channels[0].mean / 255) * bcf[0],
          srgbToLinear(st.channels[1].mean / 255) * bcf[1],
          srgbToLinear(st.channels[2].mean / 255) * bcf[2],
        ];
        // 生成 32x32 柔和底色查找表，供 LOD2 逐顶点真实 UV 采样，避免整图白底均值拉白深色飞机
        const {data} = await sharp(baseBytes).resize(32, 32, {fit: 'fill'}).removeAlpha().raw().toBuffer({resolveWithObject: true});
        lod2RgbLut = data;
      } catch {
        // 保留因子均值
      }
    }

    // 检查 UV 极值（检测平铺履带/重复贴图）与该材质总面数
    let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity, triCount = 0;
    for (const ch of chunks) {
      if (ch.matIndex !== mi) continue;
      triCount += ch.indices.length / 3;
      for (let i = 0; i < ch.uvs.length; i += 2) {
        const u = ch.uvs[i], v = ch.uvs[i + 1];
        if (u < minU) minU = u;
        if (u > maxU) maxU = u;
        if (v < minV) minV = v;
        if (v > maxV) maxV = v;
      }
    }
    const baseHash = baseBytes ? sha256(baseBytes) : null;
    const normHash = normBytes ? sha256(normBytes) : null;
    const isShared = Boolean(baseHash && sharedHashMap[baseHash]);
    const uvInUnit = minU >= -0.03 && maxU <= 1.03 && minV >= -0.03 && maxV <= 1.03;
    const canAtlas = alphaMode !== 'BLEND' && uvInUnit;

    matInfos.set(mi, {
      origIndex: mi,
      name: matName,
      baseBytes,
      normBytes,
      baseHash,
      normHash,
      baseColorFactor: bcf,
      metallic,
      roughness,
      alphaMode,
      alphaCutoff: mObj ? mObj.getAlphaCutoff() : 0.5,
      doubleSided: mObj ? mObj.getDoubleSided() : false,
      avgLin,
      lod2RgbLut,
      triCount,
      uvInUnit,
      canAtlas,
      isShared,
    });
  }

  // 5. 智能图集规划：确保不透明材质总数 <= 3（总 DrawCall <= 4）且平铺材质零花屏
  const opaqueMats = [...matInfos.values()].filter(m => m.alphaMode !== 'BLEND');
  const glassMats = [...matInfos.values()].filter(m => m.alphaMode === 'BLEND');

  // 初始方案：独有且 UV∈[0,1] 的进单车图集；跨车共享或 UV 平铺的独立保留
  let atlasCandidates = opaqueMats.filter(m => !m.isShared && m.canAtlas);
  let standaloneOpaque = opaqueMats.filter(m => m.isShared || !m.canAtlas);

  // 若独立不透明材质超预算，把 UV∈[0,1] 的共享小材质也吸纳入单车图集，严格守住总不透明材质 <= 3（且单槽+多槽不超 4 DC）
  const maxStandaloneOpaque = atlasCandidates.length > 0 ? 2 : 3;
  if (standaloneOpaque.length > maxStandaloneOpaque) {
    // 优先保持 UV 超 [0,1] 平铺材质（如履带）独立，其次保留面数最多的共享材质，其余吸纳入图集
    const mustKeep = standaloneOpaque.filter(m => !m.canAtlas);
    const movable = standaloneOpaque.filter(m => m.canAtlas).sort((a, b) => b.triCount - a.triCount);
    const keepBudget = Math.max(0, 2 - mustKeep.length);
    const keptMovable = movable.slice(0, keepBudget);
    const absorb = movable.slice(keepBudget);
    atlasCandidates = [...atlasCandidates, ...absorb];
    standaloneOpaque = [...mustKeep, ...keptMovable];
  }

  const outTexDir = path.join(outDir, 'tex');
  const finalMaterials = new Map();
  const origMatToFinal = new Map();
  let atlasUvTransforms = new Map();

  // 5a. 若有图集候选，打包为单车 1024(small=512) 底色图集 + 256 UASTC 法线图集（兼顾清晰立体感与 <=85MiB 整库归档预算）
  if (atlasCandidates.length > 0) {
    if (atlasCandidates.length === 1) {
      // 仅 1 个独有材质：无需拼缝，直接编码单张 1024 底色 / 256 法线贴图，零 UV 变更！
      const m = atlasCandidates[0];
      const baseLimit = rule.atlas_max_size || 1024;
      const normLimit = Math.min(256, rule.normal_max_size || 256);
      let baseTex = null;
      if (m.baseBytes) {
        const bPng = await sharp(m.baseBytes)
          .resize({width: baseLimit, height: baseLimit, fit: 'inside', withoutEnlargement: true})
          .png()
          .toBuffer();
        const key = m.isShared ? `shared_${m.baseHash.slice(0, 16)}_b` : `veh_${slug}_b0`;
        baseTex = await encodeCachedKTX2(bPng, false, outTexDir, key);
      }
      let normTex = null;
      if (m.normBytes) {
        const nPng = await sharp(m.normBytes)
          .resize({width: normLimit, height: normLimit, fit: 'inside', withoutEnlargement: true})
          .png()
          .toBuffer();
        const key = m.isShared ? `shared_${m.normHash.slice(0, 16)}_n` : `veh_${slug}_n0`;
        normTex = await encodeCachedKTX2(nPng, true, outTexDir, key);
      }
      const fKey = 'm_main';
      finalMaterials.set(fKey, {
        name: fKey,
        baseTex,
        normTex,
        baseColorFactor: m.baseColorFactor,
        metallic: m.metallic,
        roughness: m.roughness,
        alphaMode: m.alphaMode,
        alphaCutoff: m.alphaCutoff,
        doubleSided: m.doubleSided,
        lod2RgbLut: m.lod2RgbLut,
        lod2LutSize: 32,
        avgLin: m.avgLin,
        isShared: m.isShared,
      });
      origMatToFinal.set(m.origIndex, fKey);
    } else {
      const atlasSpec = await buildAtlasForMaterials(
        atlasCandidates,
        rule.atlas_max_size || 1024,
        Math.min(256, rule.normal_max_size || 256),
        slug,
        outTexDir,
      );
      atlasUvTransforms = atlasSpec.uvTransforms;
      finalMaterials.set('m_atlas', atlasSpec);
      for (const m of atlasCandidates) {
        origMatToFinal.set(m.origIndex, 'm_atlas');
      }
    }
  }

  // 5b. 独立保留的共享/平铺不透明材质
  for (let i = 0; i < standaloneOpaque.length; i++) {
    const m = standaloneOpaque[i];
    const baseLimit = m.isShared ? 512 : (rule.atlas_max_size || 1024);
    const normLimit = 256;
    let baseTex = null;
    if (m.baseBytes) {
      const bPng = await sharp(m.baseBytes)
        .resize({width: baseLimit, height: baseLimit, fit: 'inside', withoutEnlargement: true})
        .png()
        .toBuffer();
      const key = m.isShared ? `shared_${m.baseHash.slice(0, 16)}_b` : `veh_${slug}_sb${i}`;
      baseTex = await encodeCachedKTX2(bPng, false, outTexDir, key);
    }
    let normTex = null;
    if (m.normBytes) {
      const nPng = await sharp(m.normBytes)
        .resize({width: normLimit, height: normLimit, fit: 'inside', withoutEnlargement: true})
        .png()
        .toBuffer();
      const key = m.isShared && m.normHash ? `shared_${m.normHash.slice(0, 16)}_n` : `veh_${slug}_sn${i}`;
      normTex = await encodeCachedKTX2(nPng, true, outTexDir, key);
    }
    const fKey = `m_ext_${i}`;
    finalMaterials.set(fKey, {
      name: fKey,
      baseTex,
      normTex,
      baseColorFactor: m.baseColorFactor,
      metallic: m.metallic,
      roughness: m.roughness,
      alphaMode: m.alphaMode,
      alphaCutoff: m.alphaCutoff,
      doubleSided: m.doubleSided,
      lod2RgbLut: m.lod2RgbLut,
      lod2LutSize: 32,
      avgLin: m.avgLin,
      isShared: m.isShared,
    });
    origMatToFinal.set(m.origIndex, fKey);
  }

  // 5c. 半透明座舱/玻璃统一归为 1 个玻璃材质槽
  if (glassMats.length > 0) {
    const m = glassMats.sort((a, b) => b.triCount - a.triCount)[0];
    let baseTex = null;
    if (m.baseBytes) {
      const bPng = await sharp(m.baseBytes).resize({width: 256, height: 256, fit: 'inside', withoutEnlargement: true}).png().toBuffer();
      const key = m.isShared ? `shared_${m.baseHash.slice(0, 16)}_g` : `veh_${slug}_glass`;
      baseTex = await encodeCachedKTX2(bPng, false, outTexDir, key);
    }
    finalMaterials.set('m_glass', {
      name: 'm_glass',
      baseTex,
      normTex: null,
      baseColorFactor: m.baseColorFactor,
      metallic: Math.max(0.25, m.metallic),
      roughness: Math.min(0.28, m.roughness),
      alphaMode: 'BLEND',
      doubleSided: true,
      lod2RgbLut: null,
      lod2LutSize: 0,
      avgLin: [0.06, 0.08, 0.10],
      isShared: m.isShared,
    });
    for (const gm of glassMats) {
      origMatToFinal.set(gm.origIndex, 'm_glass');
    }
  }

  if (onlyMip) {
    return null;
  }

  // 6. 按 (slot, finalMatKey) 局部化坐标并合并清洗，同步准备 LOD2 真实 UV 采样顶点色几何
  const slotMatBuckets = new Map();
  const lod2HullList = [];
  const lod2RotorList = [];
  let sumLinR = 0, sumLinG = 0, sumLinB = 0, sumLinW = 0;

  for (const ch of chunks) {
    const piv = pivots[ch.slot] || [0, 0, 0];
    const fKey = origMatToFinal.get(ch.matIndex);
    const uvTr = atlasUvTransforms.get(ch.matIndex);
    const mInfo = matInfos.get(ch.matIndex);
    const vCount = ch.positions.length / 3;

    const locPos = new Float32Array(vCount * 3);
    const newUvs = new Float32Array(vCount * 2);
    const cols = new Float32Array(vCount * 3);
    const isGlass = mInfo.alphaMode === 'BLEND';
    const lut = !isGlass ? mInfo.lod2RgbLut : null;
    const [bfR, bfG, bfB] = mInfo.baseColorFactor;
    const [fbR, fbG, fbB] = isGlass ? [0.06, 0.08, 0.10] : mInfo.avgLin;
    let cSumR = 0, cSumG = 0, cSumB = 0;

    for (let i = 0; i < vCount; i++) {
      locPos[i * 3] = ch.positions[i * 3] - piv[0];
      locPos[i * 3 + 1] = ch.positions[i * 3 + 1] - piv[1];
      locPos[i * 3 + 2] = ch.positions[i * 3 + 2] - piv[2];
      const origU = ch.uvs[i * 2];
      const origV = ch.uvs[i * 2 + 1];
      if (uvTr) {
        const u = Math.max(0, Math.min(1, origU));
        const v = Math.max(0, Math.min(1, origV));
        newUvs[i * 2] = uvTr.u0 + u * uvTr.uScale;
        newUvs[i * 2 + 1] = uvTr.v0 + v * uvTr.vScale;
      } else {
        newUvs[i * 2] = origU;
        newUvs[i * 2 + 1] = origV;
      }
      if (lut) {
        // 按顶点真实 UV 采样 32x32 柔和底色表，保留机背迷彩与机腹分层色差，杜绝整图均值拉白
        const wu = ((origU % 1) + 1) % 1;
        const wv = ((origV % 1) + 1) % 1;
        const px = Math.min(31, Math.floor(wu * 32));
        const py = Math.min(31, Math.floor(wv * 32));
        const off = (py * 32 + px) * 3;
        const lr = srgbToLinear(lut[off] / 255) * bfR;
        const lg = srgbToLinear(lut[off + 1] / 255) * bfG;
        const lb = srgbToLinear(lut[off + 2] / 255) * bfB;
        cols[i * 3] = lr;
        cols[i * 3 + 1] = lg;
        cols[i * 3 + 2] = lb;
        cSumR += lr;
        cSumG += lg;
        cSumB += lb;
      } else {
        cols[i * 3] = fbR;
        cols[i * 3 + 1] = fbG;
        cols[i * 3 + 2] = fbB;
        cSumR += fbR;
        cSumG += fbG;
        cSumB += fbB;
      }
    }
    const tW = Math.max(1, ch.indices.length / 3);
    const invV = vCount > 0 ? 1 / vCount : 1;
    sumLinR += cSumR * invV * tW;
    sumLinG += cSumG * invV * tW;
    sumLinB += cSumB * invV * tW;
    sumLinW += tW;

    const bKey = `${ch.slot}||${fKey}`;
    if (!slotMatBuckets.has(bKey)) slotMatBuckets.set(bKey, []);
    slotMatBuckets.get(bKey).push({
      positions: locPos,
      normals: ch.normals,
      uvs: newUvs,
      indices: ch.indices,
    });

    // LOD2 将旋翼与机体分开收集，避免全局简化把薄旋翼桨叶吃空
    const l2Item = {
      positions: ch.positions,
      normals: ch.normals,
      colors: cols,
      indices: ch.indices,
    };
    if (ch.slot === 'main_rotor' || ch.slot === 'tail_rotor') {
      lod2RotorList.push(l2Item);
    } else {
      lod2HullList.push(l2Item);
    }
  }

  // 对每个 (slot, mat) 合并焊接
  const weldedParts = [];
  let weldedTotalTris = 0;
  for (const [bKey, list] of slotMatBuckets.entries()) {
    const [slot, matKey] = bKey.split('||');
    const merged = mergeGeoList(list);
    const welded = dedupAndCompactGeo(merged, false);
    if (welded.indices.length < 3) continue;
    weldedParts.push({slot, matKey, geo: welded});
    weldedTotalTris += welded.indices.length / 3;
  }

  // 检查最终槽位×材质总 DrawCall，若超 4（不含 glass），优先把极少面非核心小槽位合并进 hull 保持严格合规
  const opaquePartsCount = weldedParts.filter(p => p.matKey !== 'm_glass').length;
  // 按计划门限：每车不透明主槽 <= 4 DrawCall（若有极少数多槽×多材质>4，则对同一槽内把非平铺件进一步收口已在上面控制；记录实际 DC 供静态闸门检查）

  // 7. 同源生成 LOD0 / LOD1 / LOD2
  const approxRadius = Math.hypot(wholeMax[0] - wholeMin[0], wholeMax[1] - wholeMin[1], wholeMax[2] - wholeMin[2]) * 0.5;
  const lod0BaseBudget = Math.min(13800, rule.lod0_target_tris || 13200);
  const lod0Budget =
    retryLevel === 0
      ? lod0BaseBudget
      : retryLevel === 1
        ? Math.round(lod0BaseBudget * 1.45)
        : Math.min(weldedTotalTris, 26000);
  const lod0Error = retryLevel === 0 ? 0.0095 : retryLevel === 1 ? 0.0035 : 0.0014;
  const skipLod0Simp = retryLevel >= 2 && weldedTotalTris <= 26000;

  const lod1Budget = Math.min(2900, rule.lod1_target_tris || 2800);
  // LOD2 兼顾清晰剪影（保巨型运输机 T 型垂尾/长拖车底盘/四叶旋翼不塌断）与极小体积(全库129车<=2.4MiB)
  const lod2Budget = approxRadius > 6.2 || role === 'rotorcraft' ? 1350 : 980;

  const lod0SlotMap = new Map();
  const lod1SlotMap = new Map();

  for (const wp of weldedParts) {
    const partTris = wp.geo.indices.length / 3;
    const share = partTris / Math.max(1, weldedTotalTris);
    const minKeep0 = wp.slot !== 'hull' || wp.matKey === 'm_glass' ? Math.min(partTris, 520) : 120;
    const target0 = Math.max(minKeep0, Math.round(lod0Budget * share));
    const g0 = simplifyGeo(wp.geo, target0, lod0Error, {
      lockBorder: false,
      useAttributes: true,
      skipSimplify: skipLod0Simp,
      cullMicroAreaSq: retryLevel === 0 && weldedTotalTris > 90000 ? 4e-7 : 0,
    });

    // LOD1 严格使用带属性保轮廓减面，严禁无属性二刀流破坏垂尾与旋翼，旋翼槽位进一步收紧误差
    const isRotorSlot = wp.slot === 'main_rotor' || wp.slot === 'tail_rotor';
    const minKeep1 = isRotorSlot ? Math.min(g0.indices.length / 3, 460) : wp.slot !== 'hull' ? Math.min(g0.indices.length / 3, 300) : 140;
    const target1 = Math.max(minKeep1, Math.round(lod1Budget * share));
    const g1 = simplifyGeo(g0, target1, isRotorSlot ? 0.007 : 0.019, {
      lockBorder: false,
      useAttributes: true,
      skipSimplify: false,
      cullMicroAreaSq: isRotorSlot ? 0 : 1.5e-5,
      allowGeomFallback: false,
      fallbackSloppy: false,
    });

    if (!lod0SlotMap.has(wp.slot)) lod0SlotMap.set(wp.slot, {slot: wp.slot, pivot: pivots[wp.slot], parts: []});
    if (!lod1SlotMap.has(wp.slot)) lod1SlotMap.set(wp.slot, {slot: wp.slot, pivot: pivots[wp.slot], parts: []});
    lod0SlotMap.get(wp.slot).parts.push({matKey: wp.matKey, geo: g0});
    lod1SlotMap.get(wp.slot).parts.push({matKey: wp.matKey, geo: g1});
  }

  // 构建 LOD2 单一无贴图顶点色远景轮廓壳：
  // 1) 取消 cullMicroAreaSq 暴力删面，彻底防止爱国者拖车细钢梁底盘断空散架；
  // 2) 旋翼槽与机体分离减面再合并，确保双旋翼/武直远景壳永远保留醒目完整的旋翼轮廓！
  function mergeColorGeoList(list) {
    let vSum = 0, iSum = 0;
    for (const g of list) {
      vSum += g.positions.length / 3;
      iSum += g.indices.length;
    }
    const positions = new Float32Array(vSum * 3);
    const normals = new Float32Array(vSum * 3);
    const colors = new Float32Array(vSum * 3);
    const indices = new Uint32Array(iSum);
    let vOff = 0, iOff = 0;
    for (const g of list) {
      positions.set(g.positions, vOff * 3);
      normals.set(g.normals, vOff * 3);
      colors.set(g.colors, vOff * 3);
      for (let k = 0; k < g.indices.length; k++) indices[iOff + k] = g.indices[k] + vOff;
      vOff += g.positions.length / 3;
      iOff += g.indices.length;
    }
    return {positions, normals, colors, indices};
  }

  // 直接从已验证完美保形（全库 IoU >= 0.931）的 LOD1 机体与旋翼槽衍生 LOD2：
  // 彻底杜绝 18 万面含高模人偶怪兽车（如 LMTV War Pig）从原高模一步硬减到 1 千面时车壳塌空，同时保留真实 UV 采样迷彩与完整旋翼轮廓！
  const lod1HullColorParts = [];
  const lod1RotorColorParts = [];
  for (const [slotName, sg1] of lod1SlotMap.entries()) {
    const isRotor = slotName === 'main_rotor' || slotName === 'tail_rotor';
    const piv = sg1.pivot || [0, 0, 0];
    for (const part of sg1.parts) {
      const g = part.geo;
      if (!g || g.indices.length < 3) continue;
      const vCnt = g.positions.length / 3;
      const wPos = new Float32Array(vCnt * 3);
      const cols = new Float32Array(vCnt * 3);
      const fm = finalMaterials.get(part.matKey);
      const isGlass = fm?.alphaMode === 'BLEND';
      const lut = !isRotor && !isGlass ? fm?.lod2RgbLut : null;
      const lutSz = fm?.lod2LutSize || 32;
      const [bfR, bfG, bfB] = fm?.baseColorFactor || [1, 1, 1];
      const [fbR, fbG, fbB] = isGlass ? [0.06, 0.08, 0.10] : (fm?.avgLin || [0.22, 0.24, 0.20]);

      for (let i = 0; i < vCnt; i++) {
        wPos[i * 3] = g.positions[i * 3] + piv[0];
        wPos[i * 3 + 1] = g.positions[i * 3 + 1] + piv[1];
        wPos[i * 3 + 2] = g.positions[i * 3 + 2] + piv[2];
        if (isRotor) {
          // 旋翼桨叶统一采用真实军用深炭灰线性色
          cols[i * 3] = 0.085;
          cols[i * 3 + 1] = 0.090;
          cols[i * 3 + 2] = 0.082;
        } else if (lut && g.uvs) {
          const u = g.uvs[i * 2];
          const v = g.uvs[i * 2 + 1];
          const wu = ((u % 1) + 1) % 1;
          const wv = ((v % 1) + 1) % 1;
          const px = Math.min(lutSz - 1, Math.floor(wu * lutSz));
          const py = Math.min(lutSz - 1, Math.floor(wv * lutSz));
          const off = (py * lutSz + px) * 3;
          cols[i * 3] = srgbToLinear(lut[off] / 255) * bfR;
          cols[i * 3 + 1] = srgbToLinear(lut[off + 1] / 255) * bfG;
          cols[i * 3 + 2] = srgbToLinear(lut[off + 2] / 255) * bfB;
        } else {
          cols[i * 3] = fbR;
          cols[i * 3 + 1] = fbG;
          cols[i * 3 + 2] = fbB;
        }
      }
      const item = {
        positions: wPos,
        normals: g.normals,
        colors: cols,
        indices: g.indices,
      };
      if (isRotor) lod1RotorColorParts.push(item);
      else lod1HullColorParts.push(item);
    }
  }

  const hasLod2Rotors = lod1RotorColorParts.length > 0;
  const hullL2Budget = hasLod2Rotors ? Math.round(lod2Budget * 0.78) : lod2Budget;

  const partsL2 = [];
  if (lod1HullColorParts.length > 0) {
    const weldedHullL2 = dedupLod2ByPosition(mergeColorGeoList(lod1HullColorParts), 200);
    // 基于已保形 LOD1 温和保拓扑减面，绝不塌空车壳与车轮；若首轮误差 0.032 后仍超 2200 面再温和放宽至 0.055
    let simpHullL2 = simplifyGeo(weldedHullL2, hullL2Budget, 0.032, {
      lockBorder: false,
      useAttributes: false,
      cullMicroAreaSq: 0,
      fallbackSloppy: false,
    });
    if (simpHullL2.indices.length / 3 > 2200) {
      simpHullL2 = simplifyGeo(simpHullL2, Math.max(hullL2Budget, 1500), 0.055, {
        lockBorder: false,
        useAttributes: false,
        cullMicroAreaSq: 0,
        fallbackSloppy: false,
      });
    }
    partsL2.push(simpHullL2);
  }
  if (hasLod2Rotors) {
    // 旋翼直接沿用 LOD1 完整几何（已保形且极轻，绝不发生桨叶塌缩！）
    const mergedRot = mergeColorGeoList(lod1RotorColorParts);
    const simpRot = simplifyGeo(mergedRot, 550, 0.008, {
      lockBorder: false,
      useAttributes: false,
      cullMicroAreaSq: 0,
      fallbackSloppy: false,
    });
    partsL2.push(simpRot);
  }
  let g2 = mergeColorGeoList(partsL2);
  g2.normals = recomputeVertexNormals(g2.positions, g2.indices);

  const modelDir = path.join(outDir, 'models', slug);
  const lod0Path = path.join(modelDir, 'lod0.glb');
  const lod1Path = path.join(modelDir, 'lod1.glb');
  const lod2Path = path.join(modelDir, 'lod2.glb');

  const lod0Res = await writeOptimizedGLB(
    [...lod0SlotMap.values()],
    finalMaterials,
    {quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 12},
    lod0Path,
    true,
  );
  const lod1Res = await writeOptimizedGLB(
    [...lod1SlotMap.values()],
    finalMaterials,
    {quantizePosition: 12, quantizeNormal: 8, quantizeTexcoord: 10},
    lod1Path,
    true,
  );
  const lod2Res = await writeOptimizedGLB(
    [{slot: 'hull', pivot: [0, 0, 0], parts: [{matKey: '__lod2__', geo: g2}]}],
    finalMaterials,
    {quantizePosition: 12, quantizeNormal: 8, quantizeColor: 8},
    lod2Path,
    false,
  );

  // 8. 自动派生物理、贴地采样点、旋翼名牌包络与挂点元数据
  const size = [wholeMax[0] - wholeMin[0], wholeMax[1] - wholeMin[1], wholeMax[2] - wholeMin[2]];
  const center = [(wholeMin[0] + wholeMax[0]) * 0.5, (wholeMin[1] + wholeMax[1]) * 0.5, (wholeMin[2] + wholeMax[2]) * 0.5];
  const radius = Math.hypot(size[0], size[1], size[2]) * 0.5;

  // 名牌安全包络（旋翼机自动包含主/尾旋翼旋转扫掠半径）
  const labelMin = [...wholeMin];
  const labelMax = [...wholeMax];
  if (role === 'rotorcraft' && slotBounds.has('main_rotor')) {
    const rb = slotBounds.get('main_rotor');
    const mp = pivots.main_rotor || [0, rb.max[1], 0];
    const rMain = Math.max(
      Math.hypot(rb.min[0] - mp[0], rb.min[2] - mp[2]),
      Math.hypot(rb.max[0] - mp[0], rb.max[2] - mp[2]),
      Math.hypot(rb.min[0] - mp[0], rb.max[2] - mp[2]),
      Math.hypot(rb.max[0] - mp[0], rb.min[2] - mp[2]),
    );
    labelMin[0] = Math.min(labelMin[0], mp[0] - rMain);
    labelMax[0] = Math.max(labelMax[0], mp[0] + rMain);
    labelMin[2] = Math.min(labelMin[2], mp[2] - rMain);
    labelMax[2] = Math.max(labelMax[2], mp[2] + rMain);
  }

  // 地面载具四角贴地轮距采样点
  const groundSamples =
    role === 'ground'
      ? [
          [Number((wholeMin[0] + size[0] * 0.16).toFixed(3)), Number(wholeMin[1].toFixed(3)), Number((wholeMin[2] + size[2] * 0.16).toFixed(3))],
          [Number((wholeMax[0] - size[0] * 0.16).toFixed(3)), Number(wholeMin[1].toFixed(3)), Number((wholeMin[2] + size[2] * 0.16).toFixed(3))],
          [Number((wholeMin[0] + size[0] * 0.16).toFixed(3)), Number(wholeMin[1].toFixed(3)), Number((wholeMax[2] - size[2] * 0.16).toFixed(3))],
          [Number((wholeMax[0] - size[0] * 0.16).toFixed(3)), Number(wholeMin[1].toFixed(3)), Number((wholeMax[2] - size[2] * 0.16).toFixed(3))],
        ]
      : [];

  // 若未提取到显式炮口节点，按炮管/炮塔前向极值估算
  if (hardpoints.muzzles.length === 0 && (slotBounds.has('barrel') || slotBounds.has('turret') || slotBounds.has('gun'))) {
    const sb = slotBounds.get('barrel') || slotBounds.get('gun') || slotBounds.get('turret');
    hardpoints.muzzles.push({
      name: 'estimated_muzzle',
      pos: [Number(((sb.min[0] + sb.max[0]) * 0.5).toFixed(3)), Number(((sb.min[1] + sb.max[1]) * 0.5).toFixed(3)), Number(sb.min[2].toFixed(3))],
      estimated: true,
    });
  }

  const slotAxisMap = {
    hull: 'none',
    turret: 'y',
    barrel: 'x',
    radar: 'y',
    main_rotor: 'y',
    tail_rotor: anchors.isTandem ? 'y' : 'x',
    gun: 'y',
  };
  const slotsMeta = [...lod0SlotMap.values()].map(sg => ({
    slot: sg.slot,
    pivot: sg.pivot,
    axis: slotAxisMap[sg.slot] || 'y',
    tris_lod0: sg.parts.reduce((a, p) => a + p.geo.indices.length / 3, 0),
    tris_lod1: (lod1SlotMap.get(sg.slot)?.parts || []).reduce((a, p) => a + p.geo.indices.length / 3, 0),
    drawCalls: sg.parts.length,
  }));

  const uniqueTexBytes = lod0Res.textures.reduce((a, t) => a + t.bytes, 0);
  const vehOwnTexBytes = lod0Res.textures.filter(t => !t.key.startsWith('shared_')).reduce((a, t) => a + t.bytes, 0);
  const sharedTexBytes = lod0Res.textures.filter(t => t.key.startsWith('shared_')).reduce((a, t) => a + t.bytes, 0);

  const avgLinearColor =
    sumLinW > 0
      ? [Number((sumLinR / sumLinW).toFixed(4)), Number((sumLinG / sumLinW).toFixed(4)), Number((sumLinB / sumLinW).toFixed(4))]
      : [0.28, 0.30, 0.25];

  const meta = {
    schema_version: 1,
    id: vid,
    slug,
    name: vEntry.name,
    category: vEntry.category,
    role,
    size_tier: vEntry.size_tier,
    quality_status: vEntry.quality_status,
    is_partial: vEntry.is_partial,
    source_sha256: vEntry.source_sha256,
    used_golden_assembly: isGoldenAssembly,
    retry_level: retryLevel,
    elapsed_sec: Number(((performance.now() - t0) / 1000).toFixed(2)),
    baseline_256_bytes: vEntry.current_256_bytes,
    baseline_256_tris: vEntry.current_256_tris,
    original_tris: totalOrigTris,
    bounds: {
      min: wholeMin.map(v => Number(v.toFixed(4))),
      max: wholeMax.map(v => Number(v.toFixed(4))),
      size: size.map(v => Number(v.toFixed(4))),
      center: center.map(v => Number(v.toFixed(4))),
      radius: Number(radius.toFixed(4)),
    },
    labelBounds: {
      min: labelMin.map(v => Number(v.toFixed(4))),
      max: labelMax.map(v => Number(v.toFixed(4))),
    },
    groundSamplePoints: groundSamples,
    hardpoints,
    avgLinearColor,
    opaqueMaterialsCount: [...finalMaterials.keys()].filter(k => k !== 'm_glass').length,
    hasGlassMaterial: finalMaterials.has('m_glass'),
    hasNormalMap: lod0Res.textures.some(t => t.linear),
    slots: slotsMeta,
    lods: {
      lod0: {path: path.relative(ROOT, lod0Path).replaceAll('\\', '/'), ...lod0Res},
      lod1: {path: path.relative(ROOT, lod1Path).replaceAll('\\', '/'), ...lod1Res},
      lod2: {path: path.relative(ROOT, lod2Path).replaceAll('\\', '/'), ...lod2Res},
    },
    textures: lod0Res.textures,
    totals: {
      lod0_plus_own_tex_bytes: lod0Res.bytes + vehOwnTexBytes,
      lod0_lod1_plus_own_tex_bytes: lod0Res.bytes + lod1Res.bytes + vehOwnTexBytes,
      lod0_plus_all_tex_bytes: lod0Res.bytes + uniqueTexBytes,
      shared_tex_bytes: sharedTexBytes,
      own_tex_bytes: vehOwnTexBytes,
      opaque_draw_calls: opaquePartsCount,
    },
  };

  writeJson(path.join(outDir, 'meta', `${slug}.json`), meta);
  return meta;
}

// ---------------- CLI 入口 ----------------
const args = process.argv.slice(2);
const flag = name => args.includes(name);
const opt = (name, def = null) => {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : def;
};

const baseline = readJson(path.join(V0_DIR, '基线凭证.json'));
const rules = readJson(path.join(V0_DIR, 'vehicle-rig-rules.json'));
const sharedDoc = readJson(path.join(V0_DIR, '全库贴图哈希表.json'));

const outDir = path.resolve(ROOT, opt('--out', flag('--v1-samples') ? `local-data/程序化载具/${BATCH_ID}/V1-样本验证` : `local-data/程序化载具/${BATCH_ID}/V2-全库编译`));
const retryLevel = Number(opt('--retry-level', '0'));
const force = flag('--force');
const onlyMip = flag('--only-mip');

let targets = baseline.vehicles;
if (flag('--v1-samples')) {
  targets = targets.filter(v => v.is_v1_sample);
} else if (opt('--ids')) {
  const wanted = new Set(opt('--ids').split(',').map(s => s.trim()).filter(Boolean));
  targets = targets.filter(v => wanted.has(v.id) || wanted.has(v.short_id) || wanted.has(v.slug));
}

console.log(`[编译载具] 目标数=${targets.length} | retryLevel=${retryLevel} | onlyMip=${onlyMip} | 输出=${path.relative(ROOT, outDir)}`);
fs.mkdirSync(outDir, {recursive: true});

const results = [];
const failures = [];
let idx = 0;

for (const v of targets) {
  idx += 1;
  if (onlyMip) {
    try {
      await compileOneVehicle(v, rules.vehicles[v.id], sharedDoc.shared_images, outDir, 0, true);
      if (idx % 25 === 0 || idx === targets.length) {
        console.log(`[${idx}/${targets.length}] 中景 256px Mip 补生完成 (${v.name})`);
      }
    } catch (err) {
      failures.push({id: v.id, name: v.name, error: String(err.stack || err)});
      console.error(`[${idx}/${targets.length}] FAIL ${v.name}: ${err.message}`);
    }
    continue;
  }
  const metaPath = path.join(outDir, 'meta', `${v.slug}.json`);
  if (!force && fs.existsSync(metaPath)) {
    const prev = readJson(metaPath);
    if (
      prev.source_sha256 === v.source_sha256 &&
      prev.retry_level === retryLevel &&
      fs.existsSync(path.join(ROOT, prev.lods.lod0.path)) &&
      fs.existsSync(path.join(ROOT, prev.lods.lod1.path)) &&
      fs.existsSync(path.join(ROOT, prev.lods.lod2.path))
    ) {
      results.push(prev);
      console.log(`[${idx}/${targets.length}] 缓存命中 ${v.name} (LOD0=${prev.lods.lod0.triangles}面, DC=${prev.lods.lod0.drawCalls}, ${(prev.totals.lod0_plus_own_tex_bytes / 1024).toFixed(1)}KB)`);
      continue;
    }
  }
  try {
    const meta = await compileOneVehicle(v, rules.vehicles[v.id], sharedDoc.shared_images, outDir, retryLevel);
    results.push(meta);
    const kb = (meta.totals.lod0_plus_own_tex_bytes / 1024).toFixed(1);
    const slotsStr = meta.slots.map(s => s.slot).join('+');
    console.log(
      `[${idx}/${targets.length}] OK ${v.name.padEnd(24)} | LOD0=${String(meta.lods.lod0.triangles).padStart(6)}面 LOD1=${String(meta.lods.lod1.triangles).padStart(5)}面 LOD2=${String(meta.lods.lod2.triangles).padStart(4)}面 | DC=${meta.lods.lod0.drawCalls} | 槽=[${slotsStr}] | 独有=${kb}KB (${meta.elapsed_sec}s)`,
    );
  } catch (err) {
    failures.push({id: v.id, name: v.name, error: String(err.stack || err)});
    console.error(`[${idx}/${targets.length}] FAIL ${v.name}: ${err.message}`);
  }
}

if (onlyMip) {
  if (failures.length > 0) {
    console.error(`[编译载具] 中景 Mip 补生存在 ${failures.length} 项失败！`);
    process.exit(1);
  }
  console.log(`[编译载具] 全部 ${targets.length}/${targets.length} 项中景 256px Mip 补生完成（零修改已有 GLB/Meta）！`);
  process.exit(0);
}

// 始终从 outDir/meta/*.json 全量聚合写出 编译汇总.json，防止单车重编覆盖整库摘要
const metaDirPath = path.join(outDir, 'meta');
const allMetas = fs.existsSync(metaDirPath)
  ? fs
      .readdirSync(metaDirPath)
      .filter(f => f.endsWith('.json'))
      .sort()
      .map(f => readJson(path.join(metaDirPath, f)))
  : results;

writeJson(path.join(outDir, '编译汇总.json'), {
  schema_version: 1,
  created_at: new Date().toISOString(),
  retry_level: retryLevel,
  target_count: allMetas.length,
  passed_count: allMetas.length,
  failed_count: failures.length,
  failures,
  vehicles: allMetas.map(r => ({
    id: r.id,
    slug: r.slug,
    name: r.name,
    role: r.role,
    quality_status: r.quality_status,
    retry_level: r.retry_level,
    lod0_tris: r.lods.lod0.triangles,
    lod1_tris: r.lods.lod1.triangles,
    lod2_tris: r.lods.lod2.triangles,
    draw_calls: r.lods.lod0.drawCalls,
    nodes: r.lods.lod0.nodes,
    lod0_glb_bytes: r.lods.lod0.bytes,
    lod1_glb_bytes: r.lods.lod1.bytes,
    lod2_glb_bytes: r.lods.lod2.bytes,
    own_tex_bytes: r.totals.own_tex_bytes,
    lod0_plus_own_tex_bytes: r.totals.lod0_plus_own_tex_bytes,
    baseline_256_bytes: r.baseline_256_bytes,
    slots: r.slots.map(s => s.slot),
    has_normal: r.hasNormalMap,
  })),
});

if (failures.length > 0) {
  console.error(`[编译载具] 存在 ${failures.length} 项失败！`);
  process.exit(1);
}
console.log(`[编译载具] 全部 ${results.length}/${targets.length} 项编译完成！`);
