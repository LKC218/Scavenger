/**
 * 纯数据高程与多层/道路 3D 支撑模块（无 DOM / Three.js 依赖，供主线程、Web Worker 与 Node 核验共用）。
 */

/**
 * 解码 SCVH 全局 2m 步距 uint16 高度场 + 角色掩码 + 法线 Y 分量。
 */
export async function decodeSCVHHeightfield(compressedBuffer) {
  let rawBuffer = compressedBuffer;
  const viewCheck = new DataView(compressedBuffer);
  if (viewCheck.byteLength < 40 || viewCheck.getUint32(0, true) !== 0x48564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compressedBuffer));
    writer.close();
    rawBuffer = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuffer);
  if (dv.getUint32(0, true) !== 0x48564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVH 高度场头部签名或版本无效');
  }
  const width = dv.getUint32(8, true);
  const height = dv.getUint32(12, true);
  const minX = dv.getFloat32(16, true);
  const minZ = dv.getFloat32(20, true);
  const stepX = dv.getFloat32(24, true);
  const stepZ = dv.getFloat32(28, true);
  const minH = dv.getFloat32(32, true);
  const maxH = dv.getFloat32(36, true);

  const count = width * height;
  const deltaXZ = new Uint16Array(rawBuffer, 40, count);
  const roleGrid = new Uint8Array(rawBuffer, 40 + count * 2, count);
  const nyGrid = new Uint8Array(rawBuffer, 40 + count * 3, count);

  const qGrid = new Uint16Array(count);
  const rowAccum = new Uint16Array(width);
  for (let iz = 0; iz < height; iz++) {
    const rowOffset = iz * width;
    let dxAccum = 0;
    for (let ix = 0; ix < width; ix++) {
      dxAccum = (dxAccum + deltaXZ[rowOffset + ix]) & 0xffff;
      rowAccum[ix] = (rowAccum[ix] + dxAccum) & 0xffff;
      qGrid[rowOffset + ix] = rowAccum[ix];
    }
  }

  return {
    width,
    height,
    minX,
    minZ,
    maxX: minX + (width - 1) * stepX,
    maxZ: minZ + (height - 1) * stepZ,
    stepX,
    stepZ,
    minH,
    maxH,
    qGrid,
    roleGrid: new Uint8Array(roleGrid),
    nyGrid: new Uint8Array(nyGrid),
    bytes: qGrid.byteLength + roleGrid.byteLength + nyGrid.byteLength,
  };
}

/**
 * 解码 SCVP 2x2 块压缩 3D 支撑补丁包（内部每个 125m 格为标准 BAGE 格式，供 TriangleSupportTile 使用）。
 */
export async function decodeSCVPPatch(compressedBuffer, targetCellIndex = null) {
  let rawBuffer = compressedBuffer;
  const viewCheck = new DataView(compressedBuffer);
  if (viewCheck.byteLength < 16 || viewCheck.getUint32(0, true) !== 0x50564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compressedBuffer));
    writer.close();
    rawBuffer = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuffer);
  if (dv.getUint32(0, true) !== 0x50564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVP 支撑补丁包头部无效');
  }
  const count = dv.getUint32(8, true);
  const stride = dv.getUint32(12, true);
  if (stride !== 12) throw new Error('SCVP 目录步幅不符');
  const map = new Map();
  for (let i = 0; i < count; i++) {
    const at = 16 + i * 12;
    const cell = dv.getUint32(at, true);
    const offset = dv.getUint32(at + 4, true);
    const length = dv.getUint32(at + 8, true);
    if (targetCellIndex === null || cell === targetCellIndex) {
      map.set(cell, rawBuffer.slice(offset, offset + length));
    }
  }
  return targetCellIndex !== null ? map.get(targetCellIndex) : map;
}

/**
 * 混合高程与多层/道路 3D 支撑查询器：
 * - 主地面常驻 2m uint16 高度场双线性插值（精度 ~2.4mm）；
 * - 局部驻留的 3D 支撑补丁（BAGE 兼容块）优先处理道路分层、桥梁双层及陡坎精确射线。
 */
export class HybridTerrainSupport {
  constructor(hf) {
    this.hf = hf;
    this.scaleH = (hf.maxH - hf.minH) / 65534.0;
    this.waterLevel = -0.6999969482421875;
    this.patchTiles = new Map();
  }

  setPatchTile(chunkId, tile) {
    this.patchTiles.set(chunkId, tile);
  }

  deletePatchTile(chunkId) {
    this.patchTiles.delete(chunkId);
  }

  chunkIdAt(x, z) {
    const {minX, minZ, maxX, maxZ} = this.hf;
    if (!Number.isFinite(x) || !Number.isFinite(z) || x < minX || x > maxX || z < minZ || z > maxZ) return null;
    const cx = Math.min(71, Math.max(0, Math.floor((x - minX) / 125)));
    const cz = Math.min(71, Math.max(0, Math.floor((z - minZ) / 125)));
    return `${String(cx).padStart(2, '0')}-${String(cz).padStart(2, '0')}`;
  }

  compactForRuntime() {
    const {width, height, qGrid, roleGrid} = this.hf;
    if (!roleGrid || this.hf.roleGridHalf) return;
    const hw = Math.floor((width + 1) / 2);
    const hh = Math.floor((height + 1) / 2);
    const halfRole = new Uint8Array(hw * hh);
    for (let iz = 0; iz < hh; iz++) {
      const sz = Math.min(height - 1, iz * 2);
      const rowOff = sz * width;
      const dstOff = iz * hw;
      for (let ix = 0; ix < hw; ix++) {
        halfRole[dstOff + ix] = roleGrid[rowOff + Math.min(width - 1, ix * 2)];
      }
    }
    this.hf.roleGridHalf = halfRole;
    this.hf.halfWidth = hw;
    this.hf.roleGrid = null;
    this.hf.nyGrid = null;
    this.hf.bytes = qGrid.byteLength + halfRole.byteLength;
  }

  sampleHeightGrid(x, z) {
    const {width, height, minX, minZ, stepX, stepZ, minH, qGrid, roleGrid, roleGridHalf, halfWidth, nyGrid} = this.hf;
    const px = (x - minX) / stepX;
    const pz = (z - minZ) / stepZ;
    if (!Number.isFinite(px) || !Number.isFinite(pz) || px < -1e-5 || pz < -1e-5 || px > width - 1 + 1e-5 || pz > height - 1 + 1e-5) {
      return {state: 'unsupported', height: NaN};
    }
    const cpx = Math.max(0, Math.min(width - 1, px));
    const cpz = Math.max(0, Math.min(height - 1, pz));
    const ix = Math.min(width - 2, Math.floor(cpx));
    const iz = Math.min(height - 2, Math.floor(cpz));
    const ax = cpx - ix;
    const az = cpz - iz;
    const i00 = iz * width + ix;
    const q00 = qGrid[i00];
    const q10 = qGrid[i00 + 1];
    const q01 = qGrid[i00 + width];
    const q11 = qGrid[i00 + width + 1];
    if (q00 === 65535 || q10 === 65535 || q01 === 65535 || q11 === 65535) {
      return {state: 'unsupported', height: NaN};
    }
    const h00 = minH + q00 * this.scaleH;
    const h10 = minH + q10 * this.scaleH;
    const h01 = minH + q01 * this.scaleH;
    const h11 = minH + q11 * this.scaleH;
    const h = (h00 * (1 - ax) + h10 * ax) * (1 - az) + (h01 * (1 - ax) + h11 * ax) * az;

    let role = 1;
    let normalY = 0.98;
    if (roleGrid) {
      const rIdx = (az < 0.5 ? iz : iz + 1) * width + (ax < 0.5 ? ix : ix + 1);
      role = roleGrid[rIdx] || 1;
      normalY = Math.max(0.1, (nyGrid ? nyGrid[rIdx] : 250) / 255.0);
    } else if (roleGridHalf) {
      const hix = Math.min(halfWidth - 1, (ax < 0.5 ? ix : ix + 1) >> 1);
      const hiz = Math.min(halfWidth - 1, (az < 0.5 ? iz : iz + 1) >> 1);
      role = roleGridHalf[hiz * halfWidth + hix] || 1;
      const gx = (h10 - h00) / stepX;
      const gz = (h01 - h00) / stepZ;
      normalY = 1.0 / Math.sqrt(1.0 + gx * gx + gz * gz);
    }
    const hit = {height: h, role, normalY, source: 0, triangle: 0};
    return {
      state: 'ready',
      ...hit,
      layers: [hit],
      waterHeight: NaN,
      waterDepth: 0,
    };
  }

  query(x, z, hint = {}) {
    const cid = this.chunkIdAt(x, z);
    if (!cid) return {state: 'unsupported', height: NaN};
    const tile = this.patchTiles.get(cid);
    if (tile) {
      if (hint?.layer !== undefined || hint?.referenceY !== undefined) {
        const res = tile.query(x, z, hint);
        if (res.state === 'ready' || hint?.layer !== undefined) return res;
      } else {
        const res = tile.query(x, z, hint);
        if (res.state === 'ready') {
          if (res.role === 4) {
            const gridRes = this.sampleHeightGrid(x, z);
            if (gridRes.state === 'ready' && gridRes.role !== 4) {
              const water = tile.intersections(x, z, {roles: [5]})[0];
              if (water) {
                gridRes.waterHeight = water.height;
                gridRes.waterDepth = Math.max(0, water.height - gridRes.height);
              }
              return gridRes;
            }
          }
          return res;
        }
      }
      const gridRes = this.sampleHeightGrid(x, z);
      if (gridRes.state === 'ready') {
        const water = tile.intersections(x, z, {roles: [5]})[0];
        if (water) {
          gridRes.waterHeight = water.height;
          gridRes.waterDepth = Math.max(0, water.height - gridRes.height);
        }
      }
      return gridRes;
    }
    return this.sampleHeightGrid(x, z);
  }

  querySupport(x, z, hint) {
    return this.query(x, z, hint);
  }

  heightAt(x, z, hint) {
    return this.query(x, z, hint).height;
  }

  segment(a, b) {
    const minX = Math.min(a.x, b.x);
    const maxX = Math.max(a.x, b.x);
    const minZ = Math.min(a.z, b.z);
    const maxZ = Math.max(a.z, b.z);
    const {hf} = this;
    const cx0 = Math.max(0, Math.floor((minX - hf.minX) / 125));
    const cx1 = Math.min(71, Math.floor((maxX - hf.minX) / 125));
    const cz0 = Math.max(0, Math.floor((minZ - hf.minZ) / 125));
    const cz1 = Math.min(71, Math.floor((maxZ - hf.minZ) / 125));
    let nearest = null;
    for (let cz = cz0; cz <= cz1; cz++) {
      for (let cx = cx0; cx <= cx1; cx++) {
        const tile = this.patchTiles.get(`${String(cx).padStart(2, '0')}-${String(cz).padStart(2, '0')}`);
        if (tile) {
          const hit = tile.segment(a, b);
          if (hit !== null && (nearest === null || hit < nearest)) nearest = hit;
        }
      }
    }
    if (nearest !== null && Math.hypot(b.x - a.x, b.z - a.z) < 0.01) {
      return nearest;
    }
    const dist = Math.hypot(b.x - a.x, b.z - a.z);
    const steps = Math.max(2, Math.ceil(dist / 2.0));
    let prevT = 0;
    const q0 = this.query(a.x, a.z);
    let prevDiff = a.y - Math.max(q0.height, Number.isFinite(q0.waterHeight) ? q0.waterHeight : -Infinity);
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      if (nearest !== null && t > nearest) break;
      const x = a.x + (b.x - a.x) * t;
      const z = a.z + (b.z - a.z) * t;
      const y = a.y + (b.y - a.y) * t;
      const s = this.query(x, z);
      if (s.state !== 'ready') continue;
      const surfY = Math.max(s.height, Number.isFinite(s.waterHeight) ? s.waterHeight : -Infinity);
      const diff = y - surfY;
      if ((prevDiff >= -1e-5 && diff <= 1e-5) || (prevDiff <= 1e-5 && diff >= -1e-5)) {
        let lo = prevT;
        let hi = t;
        for (let k = 0; k < 14; k++) {
          const mid = (lo + hi) * 0.5;
          const mx = a.x + (b.x - a.x) * mid;
          const mz = a.z + (b.z - a.z) * mid;
          const my = a.y + (b.y - a.y) * mid;
          const ms = this.query(mx, mz);
          const mSurf = Math.max(ms.height, Number.isFinite(ms.waterHeight) ? ms.waterHeight : -Infinity);
          if ((my - mSurf) * prevDiff > 0) lo = mid;
          else hi = mid;
        }
        const hitT = (lo + hi) * 0.5;
        if (nearest === null || hitT < nearest) nearest = hitT;
        break;
      }
      prevT = t;
      prevDiff = diff;
    }
    return nearest;
  }

  applyHeightBrush(cx, cz, radius, deltaHeight) {
    const {width, height, minX, minZ, stepX, stepZ, minH, maxH, qGrid} = this.hf;
    const ix0 = Math.max(0, Math.floor((cx - radius - minX) / stepX));
    const ix1 = Math.min(width - 1, Math.ceil((cx + radius - minX) / stepX));
    const iz0 = Math.max(0, Math.floor((cz - radius - minZ) / stepZ));
    const iz1 = Math.min(height - 1, Math.ceil((cz + radius - minZ) / stepZ));
    for (let iz = iz0; iz <= iz1; iz++) {
      const wz = minZ + iz * stepZ;
      for (let ix = ix0; ix <= ix1; ix++) {
        const wx = minX + ix * stepX;
        const d = Math.hypot(wx - cx, wz - cz);
        if (d > radius) continue;
        const falloff = 0.5 + 0.5 * Math.cos((d / radius) * Math.PI);
        const idx = iz * width + ix;
        const curH = minH + qGrid[idx] * this.scaleH;
        const nextH = Math.max(minH, Math.min(maxH, curH + deltaHeight * falloff));
        qGrid[idx] = Math.round(((nextH - minH) / (maxH - minH)) * 65534);
      }
    }
    return {ix0, ix1, iz0, iz1};
  }

  applyRoadSplineFlattening(points, width = 12) {
    if (!Array.isArray(points) || points.length < 2) return;
    const {width: gw, height: gh, minX, minZ, stepX, stepZ, minH, maxH, qGrid, roleGrid} = this.hf;
    const halfW = width * 0.5;
    const shoulder = halfW + 4.0;
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      const segLen = Math.hypot(b[0] - a[0], b[2] - a[2]);
      if (segLen < 0.1) continue;
      const minBx = Math.min(a[0], b[0]) - shoulder;
      const maxBx = Math.max(a[0], b[0]) + shoulder;
      const minBz = Math.min(a[2], b[2]) - shoulder;
      const maxBz = Math.max(a[2], b[2]) + shoulder;
      const ix0 = Math.max(0, Math.floor((minBx - minX) / stepX));
      const ix1 = Math.min(gw - 1, Math.ceil((maxBx - minX) / stepX));
      const iz0 = Math.max(0, Math.floor((minBz - minZ) / stepZ));
      const iz1 = Math.min(gh - 1, Math.ceil((maxBz - minZ) / stepZ));
      const dx = b[0] - a[0];
      const dz = b[2] - a[2];
      const invLen2 = 1.0 / (dx * dx + dz * dz);
      for (let iz = iz0; iz <= iz1; iz++) {
        const wz = minZ + iz * stepZ;
        for (let ix = ix0; ix <= ix1; ix++) {
          const wx = minX + ix * stepX;
          const t = Math.max(0, Math.min(1, ((wx - a[0]) * dx + (wz - a[2]) * dz) * invLen2));
          const px = a[0] + dx * t;
          const pz = a[2] + dz * t;
          const dist = Math.hypot(wx - px, wz - pz);
          if (dist > shoulder) continue;
          const roadY = a[1] + (b[1] - a[1]) * t;
          const idx = iz * gw + ix;
          const curH = minH + qGrid[idx] * this.scaleH;
          const w = dist <= halfW ? 1.0 : 0.5 + 0.5 * Math.cos(((dist - halfW) / (shoulder - halfW)) * Math.PI);
          const nextH = Math.max(minH, Math.min(maxH, curH * (1 - w) + roadY * w));
          qGrid[idx] = Math.round(((nextH - minH) / (maxH - minH)) * 65534);
          if (dist <= halfW) {
            if (roleGrid) roleGrid[idx] = 2;
            else if (this.hf.roleGridHalf) this.hf.roleGridHalf[(iz >> 1) * this.hf.halfWidth + (ix >> 1)] = 2;
          }
        }
      }
    }
  }
}
