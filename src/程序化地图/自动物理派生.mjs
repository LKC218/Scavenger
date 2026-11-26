/**
 * B3阶段：浏览器与Worker共用的模块化建筑实例解码、自动旋转凸包/OBB碰撞派生、
 * 4米局部寻路网格A*自动绕行与直升机30米提前越障高度包络引擎。
 */

/**
 * 解码 SCVB 全图 93,008 条建筑与装饰 72x72 (125m 格) 空间分块包。
 */
export async function decodeSCVBBuildingInstances(compressedBuffer) {
  let rawBuffer = compressedBuffer;
  const check = new DataView(compressedBuffer);
  if (check.byteLength < 32 || check.getUint32(0, true) !== 0x42564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compressedBuffer));
    writer.close();
    rawBuffer = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuffer);
  if (dv.getUint32(0, true) !== 0x42564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVB 建筑空间实例包头部无效');
  }
  const gridX = dv.getUint32(8, true);
  const gridZ = dv.getUint32(12, true);
  const totalInstances = dv.getUint32(16, true);
  const minX = dv.getFloat32(20, true);
  const minZ = dv.getFloat32(24, true);
  const chunkM = dv.getFloat32(28, true);

  const cellCount = gridX * gridZ;
  const cellOffsets = new Uint32Array(cellCount);
  const cellCounts = new Uint32Array(cellCount);
  for (let i = 0; i < cellCount; i++) {
    cellOffsets[i] = dv.getUint32(32 + i * 8, true);
    cellCounts[i] = dv.getUint32(32 + i * 8 + 4, true);
  }
  const recordsOffset = 32 + cellCount * 8;
  const recordsView = new DataView(rawBuffer, recordsOffset, totalInstances * 56);

  return {
    gridX,
    gridZ,
    totalInstances,
    minX,
    minZ,
    chunkM,
    cellOffsets,
    cellCounts,
    recordsView,
    bytes: rawBuffer.byteLength,
  };
}

/**
 * 解码 SCVC 全图碰撞与直升机越障顶高包络包。
 */
export async function decodeSCVCCollisions(compressedBuffer) {
  let rawBuffer = compressedBuffer;
  const check = new DataView(compressedBuffer);
  if (check.byteLength < 32 || check.getUint32(0, true) !== 0x43564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compressedBuffer));
    writer.close();
    rawBuffer = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuffer);
  if (dv.getUint32(0, true) !== 0x43564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVC 碰撞与越障包头部无效');
  }
  const gridX = dv.getUint32(8, true);
  const gridZ = dv.getUint32(12, true);
  const totalColliders = dv.getUint32(16, true);
  const minX = dv.getFloat32(20, true);
  const minZ = dv.getFloat32(24, true);
  const chunkM = dv.getFloat32(28, true);

  let offset = 32;
  const trunkRadii = new Float32Array(rawBuffer, offset, 8);
  offset += 32;
  const trunkHeights = new Float32Array(rawBuffer, offset, 8);
  offset += 32;
  const cellCount = gridX * gridZ;
  const maxTopYGrid = new Float32Array(rawBuffer.slice(offset, offset + cellCount * 4));
  offset += cellCount * 4;

  const cellOffsets = new Uint32Array(cellCount);
  const cellCounts = new Uint32Array(cellCount);
  for (let i = 0; i < cellCount; i++) {
    cellOffsets[i] = dv.getUint32(offset + i * 8, true);
    cellCounts[i] = dv.getUint32(offset + i * 8 + 4, true);
  }
  offset += cellCount * 8;
  const recordsView = new DataView(rawBuffer, offset, totalColliders * 124);

  return {
    gridX,
    gridZ,
    totalColliders,
    minX,
    minZ,
    chunkM,
    trunkRadii,
    trunkHeights,
    maxTopYGrid,
    cellOffsets,
    cellCounts,
    recordsView,
    bytes: rawBuffer.byteLength,
  };
}

/**
 * 从原型局部包围盒与实例变换派生世界空间旋转包围盒 (OBB)。
 */
export function deriveWorldOBB(protoMeta, position, yawRad = 0, scale = [1, 1, 1]) {
  const bMin = protoMeta.bounds_min || [-2, 0, -2];
  const bMax = protoMeta.bounds_max || [2, 4, 2];
  const sx = Array.isArray(scale) ? scale[0] : scale;
  const sy = Array.isArray(scale) ? scale[1] : scale;
  const sz = Array.isArray(scale) ? scale[2] : scale;

  const lcx = (bMin[0] + bMax[0]) * 0.5 * sx;
  const lcy = (bMin[1] + bMax[1]) * 0.5 * sy;
  const lcz = (bMin[2] + bMax[2]) * 0.5 * sz;
  const hx = Math.max(0.25, (bMax[0] - bMin[0]) * 0.5 * Math.abs(sx));
  const hy = Math.max(0.25, (bMax[1] - bMin[1]) * 0.5 * Math.abs(sy));
  const hz = Math.max(0.25, (bMax[2] - bMin[2]) * 0.5 * Math.abs(sz));

  const c = Math.cos(yawRad);
  const s = Math.sin(yawRad);
  const wcx = position[0] + lcx * c - lcz * s;
  const wcy = position[1] + lcy;
  const wcz = position[2] + lcx * s + lcz * c;

  const extX = Math.abs(c) * hx + Math.abs(s) * hz;
  const extZ = Math.abs(s) * hx + Math.abs(c) * hz;

  return {
    center: [wcx, wcy, wcz],
    halfExtents: [hx, hy, hz],
    yawRad,
    axisX: [c, s],
    axisZ: [-s, c],
    aabb: [wcx - extX, wcy - hy, wcz - extZ, wcx + extX, wcy + hy, wcz + extZ],
    topY: wcy + hy,
    maxY: wcy + hy,
  };
}

/**
 * 自动物理派生与实时导航/越障引擎：
 * - 统一管理源静态碰撞体与编辑器新增/修改/删除的建筑/树木实例；
 * - 每次修改立即自动更新旋转建筑 OBB、4m 寻路阻挡网格、125m 粗通行成本与直升机 30m 提前越障高度包络。
 */
export class AutoPhysicsAndNavEngine {
  constructor({b3Manifest, b2Manifest, buildingIndex, collisionIndex, treeIndex, support}) {
    this.b3Manifest = b3Manifest;
    this.b2Manifest = b2Manifest;
    this.buildingIndex = buildingIndex;
    this.collisionIndex = collisionIndex;
    this.treeIndex = treeIndex;
    this.support = support;

    this.protoById = new Map();
    this.bldgProtoByIndex = [];
    for (const p of b3Manifest.building_prototypes) {
      this.protoById.set(p.id, p);
      this.bldgProtoByIndex[p.prototype_index] = p;
    }
    this.treeProtoByIndex = [];
    for (const tp of b2Manifest.tree_prototypes) {
      this.protoById.set(tp.id, tp);
      this.treeProtoByIndex[tp.tree_index] = tp;
    }

    // 编辑器手工覆盖表
    this.addedInstances = new Map();    // id -> {id, prototype_id, position:[x,y,z], yaw_rad, scale:[sx,sy,sz], obb}
    this.modifiedInstances = new Map(); // key ("bldg:cell:idx" | "tree:cell:idx") -> {prototype_id, position, yaw_rad, scale, obb}
    this.deletedKeys = new Set();       // key
    this.terrainStrokes = [];
    this.splatStrokes = [];
    this.modifiedRoadSplines = new Map();

    // 缓存每个 125m 格解码出的源 OBB 列表
    this._cellSourceOBBs = new Map();
    // 建筑/障碍变更通知监听（供战争迷雾静态 OBB 遮挡缓存实时局部失效）
    this._occlusionDirtyListeners = new Set();
    this.stats = {
      recalcCount: 0,
      lastRecalcMs: 0,
      dirtyCellsCount: 0,
      activeDynamicOBBs: 0,
    };
  }

  /**
   * 订阅建筑/碰撞体变更事件（编辑器增删改或导入场景后立即通知迷雾等只读缓存失效）。
   */
  onOcclusionDirty(listener) {
    if (typeof listener !== 'function') return () => {};
    this._occlusionDirtyListeners.add(listener);
    return () => {
      this._occlusionDirtyListeners.delete(listener);
    };
  }

  _notifyOcclusionDirty(x = null, z = null, radiusM = null) {
    if (this._occlusionDirtyListeners.size === 0) return;
    const payload = { x, z, radiusM };
    for (const fn of this._occlusionDirtyListeners) {
      try {
        fn(payload);
      } catch {
        // 隔离下游监听异常，不影响物理派生主链
      }
    }
  }

  _isSourceOBBSuppressed(obb) {
    if (this.deletedKeys.size === 0 && this.modifiedInstances.size === 0) return false;
    for (const delKey of this.deletedKeys) {
      if (
        delKey === obb.sourceKey ||
        (typeof delKey === 'object' && Math.hypot(delKey.x - obb.center[0], delKey.z - obb.center[2]) < 6.0)
      ) {
        return true;
      }
    }
    for (const mod of this.modifiedInstances.values()) {
      if (mod.origPos && Math.hypot(mod.origPos[0] - obb.center[0], mod.origPos[2] - obb.center[2]) < 8.0) {
        return true;
      }
    }
    return false;
  }

  /**
   * 获取指定 125m 格 (cx, cz) 的全部源建筑 OBB 碰撞体。
   */
  getSourceCellOBBs(cx, cz) {
    const {gridX, gridZ, cellOffsets, cellCounts, recordsView} = this.collisionIndex;
    if (cx < 0 || cx >= gridX || cz < 0 || cz >= gridZ) return [];
    const cellIdx = cz * gridX + cx;
    if (this._cellSourceOBBs.has(cellIdx)) return this._cellSourceOBBs.get(cellIdx);

    const count = cellCounts[cellIdx];
    const start = cellOffsets[cellIdx];
    const list = [];

    for (let k = 0; k < count; k++) {
      const off = (start + k) * 124;
      const group = recordsView.getUint32(off, true);
      const index = recordsView.getUint32(off + 4, true);
      const proto = recordsView.getUint32(off + 8, true);
      const flags = recordsView.getUint32(off + 16, true);
      if ((flags & 8) === 0) continue; // 仅考虑 movement 阻挡碰撞体

      const m0 = recordsView.getFloat32(off + 20, true);
      const m2 = recordsView.getFloat32(off + 28, true);
      const m5 = recordsView.getFloat32(off + 40, true);
      const m8 = recordsView.getFloat32(off + 52, true);
      const m10 = recordsView.getFloat32(off + 60, true);
      const wx = recordsView.getFloat32(off + 68, true);
      const wy = recordsView.getFloat32(off + 72, true);
      const wz = recordsView.getFloat32(off + 76, true);

      const px = recordsView.getFloat32(off + 84, true);
      const py = recordsView.getFloat32(off + 88, true);
      const pz = recordsView.getFloat32(off + 92, true);

      const bMinX = recordsView.getFloat32(off + 100, true);
      const bMinY = recordsView.getFloat32(off + 104, true);
      const bMinZ = recordsView.getFloat32(off + 108, true);
      const bMaxX = recordsView.getFloat32(off + 112, true);
      const bMaxY = recordsView.getFloat32(off + 116, true);
      const bMaxZ = recordsView.getFloat32(off + 120, true);

      const sx = Math.hypot(m0, m2) || 1;
      const sy = Math.abs(m5) || 1;
      const sz = Math.hypot(m8, m10) || 1;
      const yaw = Math.atan2(m8, m0);
      const c = Math.cos(yaw);
      const s = Math.sin(yaw);

      const kind = flags & 3;
      const hx = kind === 1 ? px * 0.5 * sx : px * sx;
      const hy = py * 0.5 * sy;
      const hz = kind === 1 ? pz * 0.5 * sz : px * sz;

      list.push({
        sourceKey: `${group}:${index}`,
        proto,
        center: [wx, wy, wz],
        halfExtents: [hx, hy, hz],
        yawRad: yaw,
        axisX: [c, s],
        axisZ: [-s, c],
        aabb: [bMinX, bMinY, bMinZ, bMaxX, bMaxY, bMaxZ],
        topY: bMaxY,
      });
    }

    this._cellSourceOBBs.set(cellIdx, list);
    return list;
  }

  /**
   * 查询世界坐标范围 [minX, minZ, maxX, maxZ] 内所有活动 OBB 碰撞体（自动排除已删除/已移动的旧实例并包含新增/修改实例）。
   */
  getActiveOBBsInBounds(minX, minZ, maxX, maxZ) {
    const {minX: gMinX, minZ: gMinZ, chunkM, gridX, gridZ} = this.collisionIndex;
    const cx0 = Math.max(0, Math.min(gridX - 1, Math.floor((minX - gMinX) / chunkM)));
    const cx1 = Math.max(0, Math.min(gridX - 1, Math.floor((maxX - gMinX) / chunkM)));
    const cz0 = Math.max(0, Math.min(gridZ - 1, Math.floor((minZ - gMinZ) / chunkM)));
    const cz1 = Math.max(0, Math.min(gridZ - 1, Math.floor((maxZ - gMinZ) / chunkM)));

    const result = [];
    for (let cz = cz0; cz <= cz1; cz++) {
      for (let cx = cx0; cx <= cx1; cx++) {
        const srcList = this.getSourceCellOBBs(cx, cz);
        for (const obb of srcList) {
          if (
            obb.aabb[3] < minX || obb.aabb[0] > maxX ||
            obb.aabb[5] < minZ || obb.aabb[2] > maxZ
          ) continue;
          // 检查该区域内是否有被删除或被移走的实例覆盖了此源碰撞盒
          let suppressed = false;
          for (const delKey of this.deletedKeys) {
            if (delKey === obb.sourceKey || (typeof delKey === 'object' && Math.hypot(delKey.x - obb.center[0], delKey.z - obb.center[2]) < 6.0)) {
              suppressed = true;
              break;
            }
          }
          if (!suppressed) {
            for (const mod of this.modifiedInstances.values()) {
              if (mod.origPos && Math.hypot(mod.origPos[0] - obb.center[0], mod.origPos[2] - obb.center[2]) < 8.0) {
                suppressed = true;
                break;
              }
            }
          }
          if (!suppressed) result.push(obb);
        }
      }
    }

    for (const item of this.addedInstances.values()) {
      const obb = item.obb;
      if (
        obb.aabb[3] >= minX && obb.aabb[0] <= maxX &&
        obb.aabb[5] >= minZ && obb.aabb[2] <= maxZ
      ) {
        result.push(obb);
      }
    }

    for (const mod of this.modifiedInstances.values()) {
      const obb = mod.obb;
      if (
        obb.aabb[3] >= minX && obb.aabb[0] <= maxX &&
        obb.aabb[5] >= minZ && obb.aabb[2] <= maxZ
      ) {
        result.push(obb);
      }
    }
    return result;
  }

  /**
   * 判定点 (px, pz) 在给定载具安全半径 clearance 下是否被建筑/障碍阻挡。
   */
  isPointBlocked(px, pz, clearance = 5.5) {
    // 1. 地形支撑与坡度/水域检查
    const sup = this.support.query(px, pz);
    if (sup.state !== 'ready' || sup.waterDepth > 0.4 || sup.role === 5) return true;
    const hL = this.support.heightAt(px - 2.0, pz);
    const hR = this.support.heightAt(px + 2.0, pz);
    const hD = this.support.heightAt(px, pz - 2.0);
    const hU = this.support.heightAt(px, pz + 2.0);
    if (Number.isFinite(hL) && Number.isFinite(hR) && Number.isFinite(hD) && Number.isFinite(hU)) {
      const slopeGrad = Math.hypot((hR - hL) * 0.25, (hU - hD) * 0.25);
      if (slopeGrad > 0.58) return true; // > 30° 陡坡禁行
    }

    // 2. 旋转建筑 OBB 碰撞检查
    const obbs = this.getActiveOBBsInBounds(px - clearance - 2, pz - clearance - 2, px + clearance + 2, pz + clearance + 2);
    for (const obb of obbs) {
      const dx = px - obb.center[0];
      const dz = pz - obb.center[2];
      const lx = Math.abs(dx * obb.axisX[0] + dz * obb.axisX[1]);
      const lz = Math.abs(dx * obb.axisZ[0] + dz * obb.axisZ[1]);
      if (lx <= obb.halfExtents[0] + clearance && lz <= obb.halfExtents[2] + clearance) {
        return true;
      }
    }
    return false;
  }

  /**
   * 纯建筑旋转 OBB 点遮挡判定（零数组分配，剥离地形坡度与水域查询，专供战争迷雾视线判定）。
   */
  isPointBlockedByBuildingOBB(px, pz, clearance = 1.5) {
    const pad = clearance + 2.0;
    const minX = px - pad;
    const maxX = px + pad;
    const minZ = pz - pad;
    const maxZ = pz + pad;

    if (this.collisionIndex) {
      const { minX: gMinX, minZ: gMinZ, chunkM, gridX, gridZ } = this.collisionIndex;
      const cx0 = Math.max(0, Math.min(gridX - 1, Math.floor((minX - gMinX) / chunkM)));
      const cx1 = Math.max(0, Math.min(gridX - 1, Math.floor((maxX - gMinX) / chunkM)));
      const cz0 = Math.max(0, Math.min(gridZ - 1, Math.floor((minZ - gMinZ) / chunkM)));
      const cz1 = Math.max(0, Math.min(gridZ - 1, Math.floor((maxZ - gMinZ) / chunkM)));

      for (let cz = cz0; cz <= cz1; cz++) {
        for (let cx = cx0; cx <= cx1; cx++) {
          const srcList = this.getSourceCellOBBs(cx, cz);
          for (let i = 0; i < srcList.length; i++) {
            const obb = srcList[i];
            if (
              obb.aabb[3] < minX || obb.aabb[0] > maxX ||
              obb.aabb[5] < minZ || obb.aabb[2] > maxZ
            ) continue;
            if (this._isSourceOBBSuppressed(obb)) continue;
            const dx = px - obb.center[0];
            const dz = pz - obb.center[2];
            const lx = Math.abs(dx * obb.axisX[0] + dz * obb.axisX[1]);
            if (lx > obb.halfExtents[0] + clearance) continue;
            const lz = Math.abs(dx * obb.axisZ[0] + dz * obb.axisZ[1]);
            if (lz <= obb.halfExtents[2] + clearance) return true;
          }
        }
      }
    }

    for (const item of this.addedInstances.values()) {
      const obb = item.obb;
      if (
        obb.aabb[3] < minX || obb.aabb[0] > maxX ||
        obb.aabb[5] < minZ || obb.aabb[2] > maxZ
      ) continue;
      const dx = px - obb.center[0];
      const dz = pz - obb.center[2];
      const lx = Math.abs(dx * obb.axisX[0] + dz * obb.axisX[1]);
      if (lx > obb.halfExtents[0] + clearance) continue;
      const lz = Math.abs(dx * obb.axisZ[0] + dz * obb.axisZ[1]);
      if (lz <= obb.halfExtents[2] + clearance) return true;
    }

    for (const mod of this.modifiedInstances.values()) {
      const obb = mod.obb;
      if (
        obb.aabb[3] < minX || obb.aabb[0] > maxX ||
        obb.aabb[5] < minZ || obb.aabb[2] > maxZ
      ) continue;
      const dx = px - obb.center[0];
      const dz = pz - obb.center[2];
      const lx = Math.abs(dx * obb.axisX[0] + dz * obb.axisX[1]);
      if (lx > obb.halfExtents[0] + clearance) continue;
      const lz = Math.abs(dx * obb.axisZ[0] + dz * obb.axisZ[1]);
      if (lz <= obb.halfExtents[2] + clearance) return true;
    }

    return false;
  }

  /**
   * 纯建筑 OBB 块级覆盖光栅化（专供战争迷雾 512×512 静态遮挡位图按需填充）：
   * - 仅判定旋转建筑 OBB，完全剥离与迷雾高程射线重复的 4× 坡度与水域查询；
   * - 使用 2D OBB vs 格网矩形分离轴（SAT）覆盖写入，宽度 < 11.6m 的薄墙/窄建筑同样可靠标记为 2（阻挡）。
   */
  rasterizeBuildingOcclusionRegion(outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1, clearance = 1.5) {
    if (!outGrid || !bounds || gridW <= 0 || gridH <= 0) return;
    const minX = bounds.minX;
    const minZ = bounds.minZ;
    const spanX = Math.max(1, bounds.maxX - minX);
    const spanZ = Math.max(1, bounds.maxZ - minZ);
    const cellW = spanX / gridW;
    const cellH = spanZ / gridH;
    const invCellW = gridW / spanX;
    const invCellH = gridH / spanZ;
    const halfCellX = cellW * 0.5;
    const halfCellZ = cellH * 0.5;

    const cIx0 = Math.max(0, ix0);
    const cIz0 = Math.max(0, iz0);
    const cIx1 = Math.min(gridW - 1, ix1);
    const cIz1 = Math.min(gridH - 1, iz1);
    if (cIx0 > cIx1 || cIz0 > cIz1) return;

    const rMinX = minX + cIx0 * cellW - clearance;
    const rMaxX = minX + (cIx1 + 1) * cellW + clearance;
    const rMinZ = minZ + cIz0 * cellH - clearance;
    const rMaxZ = minZ + (cIz1 + 1) * cellH + clearance;

    const stampOBB = (obb) => {
      if (
        obb.aabb[3] < rMinX || obb.aabb[0] > rMaxX ||
        obb.aabb[5] < rMinZ || obb.aabb[2] > rMaxZ
      ) return;
      const cx = obb.center[0];
      const cz = obb.center[2];
      const hx = obb.halfExtents[0] + clearance;
      const hz = obb.halfExtents[2] + clearance;
      const ax0 = obb.axisX ? obb.axisX[0] : 1;
      const ax1 = obb.axisX ? obb.axisX[1] : 0;
      const az0 = obb.axisZ ? obb.axisZ[0] : 0;
      const az1 = obb.axisZ ? obb.axisZ[1] : 1;

      const extX = Math.abs(ax0) * hx + Math.abs(az0) * hz;
      const extZ = Math.abs(ax1) * hx + Math.abs(az1) * hz;
      const oMinX = cx - extX;
      const oMaxX = cx + extX;
      const oMinZ = cz - extZ;
      const oMaxZ = cz + extZ;

      const gx0 = Math.max(cIx0, Math.floor((oMinX - minX) * invCellW));
      const gx1 = Math.min(cIx1, Math.floor((oMaxX - minX) * invCellW));
      const gz0 = Math.max(cIz0, Math.floor((oMinZ - minZ) * invCellH));
      const gz1 = Math.min(cIz1, Math.floor((oMaxZ - minZ) * invCellH));
      if (gx0 > gx1 || gz0 > gz1) return;

      const limX = hx + Math.abs(ax0) * halfCellX + Math.abs(ax1) * halfCellZ;
      const limZ = hz + Math.abs(az0) * halfCellX + Math.abs(az1) * halfCellZ;

      for (let iz = gz0; iz <= gz1; iz++) {
        const dz = minZ + (iz + 0.5) * cellH - cz;
        const rowOff = iz * gridW;
        for (let ix = gx0; ix <= gx1; ix++) {
          const idx = rowOff + ix;
          if (outGrid[idx] === 2) continue;
          const dx = minX + (ix + 0.5) * cellW - cx;
          if (Math.abs(dx * ax0 + dz * ax1) <= limX && Math.abs(dx * az0 + dz * az1) <= limZ) {
            outGrid[idx] = 2;
          }
        }
      }
    };

    if (this.collisionIndex) {
      const { minX: gMinX, minZ: gMinZ, chunkM, gridX, gridZ } = this.collisionIndex;
      const cx0 = Math.max(0, Math.min(gridX - 1, Math.floor((rMinX - gMinX) / chunkM)));
      const cx1 = Math.max(0, Math.min(gridX - 1, Math.floor((rMaxX - gMinX) / chunkM)));
      const cz0 = Math.max(0, Math.min(gridZ - 1, Math.floor((rMinZ - gMinZ) / chunkM)));
      const cz1 = Math.max(0, Math.min(gridZ - 1, Math.floor((rMaxZ - gMinZ) / chunkM)));
      for (let cz = cz0; cz <= cz1; cz++) {
        for (let cx = cx0; cx <= cx1; cx++) {
          const srcList = this.getSourceCellOBBs(cx, cz);
          for (let i = 0; i < srcList.length; i++) {
            const obb = srcList[i];
            if (
              obb.aabb[3] < rMinX || obb.aabb[0] > rMaxX ||
              obb.aabb[5] < rMinZ || obb.aabb[2] > rMaxZ
            ) continue;
            if (this._isSourceOBBSuppressed(obb)) continue;
            stampOBB(obb);
          }
        }
      }
    }

    for (const item of this.addedInstances.values()) {
      stampOBB(item.obb);
    }
    for (const mod of this.modifiedInstances.values()) {
      stampOBB(mod.obb);
    }
  }

  /**
   * 在 4 米局部寻路网格上使用 A* 搜索从 (startX, startZ) 到 (goalX, goalZ) 的平滑绕行路径。
   * 采用 OBB 反向包围盒光栅化 + TypedArray 最小二叉堆 + inOpen 去重 + 共线拐点抽稀。
   */
  findLocalPath4m(startX, startZ, goalX, goalZ, clearance = 5.5) {
    const step = 4.0;
    const invStep = 1.0 / step;
    const pad = 80.0;
    const minX = Math.min(startX, goalX) - pad;
    const maxX = Math.max(startX, goalX) + pad;
    const minZ = Math.min(startZ, goalZ) - pad;
    const maxZ = Math.max(startZ, goalZ) + pad;

    const cols = Math.min(160, Math.max(16, Math.ceil((maxX - minX) * invStep)));
    const rows = Math.min(160, Math.max(16, Math.ceil((maxZ - minZ) * invStep)));
    const total = cols * rows;
    const blockedGrid = new Uint8Array(total);

    // 1. 构建水域与 >30° 陡坡地形阻挡标记（零对象分配直读高度场或兼容单测 mock support）
    const hf = this.support?.hf;
    if (hf && hf.qGrid) {
      const {
        width: hfW,
        height: hfH,
        minX: hfMinX,
        minZ: hfMinZ,
        stepX: hfStepX,
        stepZ: hfStepZ,
        qGrid,
        roleGrid,
        roleGridHalf,
        halfWidth,
      } = hf;
      const scaleH = this.support.scaleH || ((hf.maxH - hf.minH) / 65534.0);
      const invHfStepX = 1.0 / (hfStepX || 2.0);
      const invHfStepZ = 1.0 / (hfStepZ || 2.0);
      for (let iz = 0; iz < rows; iz++) {
        const pz = minZ + (iz + 0.5) * step;
        const cpz = Math.max(0, Math.min(hfH - 2, (pz - hfMinZ) * invHfStepZ | 0));
        const rowOff = iz * cols;
        const hfRowOff = cpz * hfW;
        for (let ix = 0; ix < cols; ix++) {
          const px = minX + (ix + 0.5) * step;
          const cpx = Math.max(0, Math.min(hfW - 2, (px - hfMinX) * invHfStepX | 0));
          const i00 = hfRowOff + cpx;
          const q00 = qGrid[i00];
          if (q00 === 65535) {
            blockedGrid[rowOff + ix] = 1;
            continue;
          }
          let role = 1;
          if (roleGrid) {
            role = roleGrid[i00] || 1;
          } else if (roleGridHalf) {
            role = roleGridHalf[(cpz >> 1) * halfWidth + (cpx >> 1)] || 1;
          }
          if (role === 5) {
            blockedGrid[rowOff + ix] = 1;
            continue;
          }
          const gx = (qGrid[i00 + 1] - q00) * scaleH * invHfStepX;
          const gz = (qGrid[i00 + hfW] - q00) * scaleH * invHfStepZ;
          if (gx * gx + gz * gz > 0.3364) {
            blockedGrid[rowOff + ix] = 1;
          }
        }
      }
    } else if (typeof this.support?.query === 'function') {
      for (let iz = 0; iz < rows; iz++) {
        const pz = minZ + (iz + 0.5) * step;
        const rowOff = iz * cols;
        for (let ix = 0; ix < cols; ix++) {
          const px = minX + (ix + 0.5) * step;
          const sup = this.support.query(px, pz);
          if (sup && (sup.waterDepth > 0.4 || sup.role === 5)) {
            blockedGrid[rowOff + ix] = 1;
          }
        }
      }
    }

    // 2. OBB 反向光栅化：仅遍历每个活动 OBB 在局部网格中（含 clearance 扩张）覆盖的包围盒格
    const obbs = this.getActiveOBBsInBounds(minX - clearance, minZ - clearance, maxX + clearance, maxZ + clearance);
    for (let k = 0; k < obbs.length; k++) {
      const obb = obbs[k];
      const cx = obb.center[0];
      const cz = obb.center[2];
      const hx = obb.halfExtents[0] + clearance;
      const hz = obb.halfExtents[2] + clearance;
      const ax0 = obb.axisX ? obb.axisX[0] : 1;
      const ax1 = obb.axisX ? obb.axisX[1] : 0;
      const az0 = obb.axisZ ? obb.axisZ[0] : 0;
      const az1 = obb.axisZ ? obb.axisZ[1] : 1;

      const extX = Math.abs(ax0) * hx + Math.abs(az0) * hz;
      const extZ = Math.abs(ax1) * hx + Math.abs(az1) * hz;
      const oMinX = cx - extX;
      const oMaxX = cx + extX;
      const oMinZ = cz - extZ;
      const oMaxZ = cz + extZ;

      if (oMaxX < minX || oMinX > maxX || oMaxZ < minZ || oMinZ > maxZ) continue;

      const gx0 = Math.max(0, Math.floor((oMinX - minX) * invStep));
      const gx1 = Math.min(cols - 1, Math.floor((oMaxX - minX) * invStep));
      const gz0 = Math.max(0, Math.floor((oMinZ - minZ) * invStep));
      const gz1 = Math.min(rows - 1, Math.floor((oMaxZ - minZ) * invStep));

      for (let iz = gz0; iz <= gz1; iz++) {
        const pz = minZ + (iz + 0.5) * step;
        const dz = pz - cz;
        const rowOff = iz * cols;
        for (let ix = gx0; ix <= gx1; ix++) {
          const idx = rowOff + ix;
          if (blockedGrid[idx]) continue;
          const px = minX + (ix + 0.5) * step;
          const dx = px - cx;
          const lx = Math.abs(dx * ax0 + dz * ax1);
          if (lx > hx) continue;
          const lz = Math.abs(dx * az0 + dz * az1);
          if (lz <= hz) {
            blockedGrid[idx] = 1;
          }
        }
      }
    }

    const sx = Math.max(0, Math.min(cols - 1, Math.floor((startX - minX) * invStep)));
    const sz = Math.max(0, Math.min(rows - 1, Math.floor((startZ - minZ) * invStep)));
    const gx = Math.max(0, Math.min(cols - 1, Math.floor((goalX - minX) * invStep)));
    const gz = Math.max(0, Math.min(rows - 1, Math.floor((goalZ - minZ) * invStep)));

    // 起点与终点自身放行
    const startIdx = sz * cols + sx;
    const goalIdx = gz * cols + gx;
    blockedGrid[startIdx] = 0;
    blockedGrid[goalIdx] = 0;

    // 3. TypedArray 最小二叉堆 + inOpen 去重 A* 搜索
    const gScore = new Float32Array(total).fill(1e9);
    const fScore = new Float32Array(total).fill(1e9);
    const parent = new Int32Array(total).fill(-1);
    const closed = new Uint8Array(total);
    const inOpen = new Uint8Array(total);
    const heap = new Int32Array(total + 1);
    const heapPos = new Int32Array(total).fill(-1);
    let heapSize = 0;

    const siftUp = (pos) => {
      const node = heap[pos];
      const f = fScore[node];
      let p = pos;
      while (p > 1) {
        const parentPos = p >> 1;
        const parentNode = heap[parentPos];
        if (fScore[parentNode] <= f) break;
        heap[p] = parentNode;
        heapPos[parentNode] = p;
        p = parentPos;
      }
      heap[p] = node;
      heapPos[node] = p;
    };

    const siftDown = (pos) => {
      const node = heap[pos];
      const f = fScore[node];
      let p = pos;
      const half = heapSize >> 1;
      while (p <= half) {
        let child = p << 1;
        let childNode = heap[child];
        let childF = fScore[childNode];
        const right = child + 1;
        if (right <= heapSize) {
          const rightNode = heap[right];
          const rightF = fScore[rightNode];
          if (rightF < childF) {
            child = right;
            childNode = rightNode;
            childF = rightF;
          }
        }
        if (f <= childF) break;
        heap[p] = childNode;
        heapPos[childNode] = p;
        p = child;
      }
      heap[p] = node;
      heapPos[node] = p;
    };

    const pushOpen = (node) => {
      heapSize += 1;
      heap[heapSize] = node;
      heapPos[node] = heapSize;
      inOpen[node] = 1;
      siftUp(heapSize);
    };

    const popOpen = () => {
      const top = heap[1];
      inOpen[top] = 0;
      heapPos[top] = -1;
      const last = heap[heapSize];
      heapSize -= 1;
      if (heapSize > 0) {
        heap[1] = last;
        heapPos[last] = 1;
        siftDown(1);
      }
      return top;
    };

    gScore[startIdx] = 0;
    fScore[startIdx] = Math.hypot(gx - sx, gz - sz);
    pushOpen(startIdx);

    const dirDX = [1, -1, 0, 0, 1, 1, -1, -1];
    const dirDZ = [0, 0, 1, -1, 1, -1, 1, -1];
    const dirCost = [1.0, 1.0, 1.0, 1.0, 1.414, 1.414, 1.414, 1.414];

    let found = false;
    let expanded = 0;
    while (heapSize > 0) {
      const cur = popOpen();
      if (cur === goalIdx) {
        found = true;
        break;
      }
      if (closed[cur]) continue;
      closed[cur] = 1;
      expanded += 1;

      const cz = (cur / cols) | 0;
      const cx = cur - cz * cols;
      const curG = gScore[cur];

      for (let d = 0; d < 8; d++) {
        const dx = dirDX[d];
        const dz = dirDZ[d];
        const nx = cx + dx;
        const nz = cz + dz;
        if (nx < 0 || nx >= cols || nz < 0 || nz >= rows) continue;
        const nIdx = nz * cols + nx;
        if (closed[nIdx] || blockedGrid[nIdx]) continue;
        // 防止对角线穿墙角
        if (d >= 4) {
          if (blockedGrid[cz * cols + nx] || blockedGrid[nz * cols + cx]) continue;
        }
        const tentativeG = curG + dirCost[d];
        if (tentativeG < gScore[nIdx]) {
          gScore[nIdx] = tentativeG;
          fScore[nIdx] = tentativeG + Math.hypot(gx - nx, gz - nz);
          parent[nIdx] = cur;
          if (inOpen[nIdx]) {
            siftUp(heapPos[nIdx]);
          } else {
            pushOpen(nIdx);
          }
        }
      }
    }

    // 4. 回溯路径并做轻量共线中间格点抽稀（保留全部绕障拐点与首尾点）
    const waypoints = [];
    if (found) {
      const rawCells = [];
      let curr = goalIdx;
      while (curr !== -1) {
        rawCells.push(curr);
        curr = parent[curr];
      }
      rawCells.reverse();

      const keptCells = [rawCells[0]];
      for (let i = 1; i < rawCells.length - 1; i++) {
        const prevCell = keptCells[keptCells.length - 1];
        const curCell = rawCells[i];
        const nextCell = rawCells[i + 1];
        const pz = (prevCell / cols) | 0;
        const px = prevCell - pz * cols;
        const cz = (curCell / cols) | 0;
        const cx = curCell - cz * cols;
        const nz = (nextCell / cols) | 0;
        const nx = nextCell - nz * cols;
        const vx1 = cx - px;
        const vz1 = cz - pz;
        const vx2 = nx - cx;
        const vz2 = nz - cz;
        const cross = Math.abs(vx1 * vz2 - vz1 * vx2);
        const dot = vx1 * vx2 + vz1 * vz2;
        const spanCells = Math.hypot(nx - px, nz - pz);
        if (cross === 0 && dot > 0 && spanCells <= 16) {
          continue;
        }
        keptCells.push(curCell);
      }
      if (rawCells.length > 1) {
        keptCells.push(rawCells[rawCells.length - 1]);
      }

      for (let i = 0; i < keptCells.length; i++) {
        const cell = keptCells[i];
        const iz = (cell / cols) | 0;
        const ix = cell - iz * cols;
        const wx = cell === startIdx ? startX : (cell === goalIdx ? goalX : minX + (ix + 0.5) * step);
        const wz = cell === startIdx ? startZ : (cell === goalIdx ? goalZ : minZ + (iz + 0.5) * step);
        const wy = this.support?.heightAt?.(wx, wz) || 0;
        waypoints.push([wx, wy + 0.25, wz]);
      }
    } else {
      const sy = this.support?.heightAt?.(startX, startZ) || 0;
      const gy = this.support?.heightAt?.(goalX, goalZ) || 0;
      waypoints.push([startX, sy + 0.25, startZ], [goalX, gy + 0.25, goalZ]);
    }

    return {
      found,
      waypoints,
      expanded,
      gridBounds: {minX, minZ, step, cols, rows},
      blockedGrid,
      activeOBBs: obbs,
    };
  }

  /**
   * 直升机 30 米提前越障高度包络查询：
   * 采样当前位置 (x, z) 至前方 lookaheadM (默认 30m) 走廊内的地面高程与所有静态/动态建筑顶高 topY，
   * 自动返回平滑拉升后的安全飞行海拔。
   */
  queryHelicopterSafeAltitude(x, z, dirX = 1.0, dirZ = 0.0, {
    cruiseAboveGround = 22.0,
    clearanceAboveObstacle = 11.0,
    lookaheadM = 30.0,
    corridorHalfWidth = 10.0,
  } = {}) {
    const len = Math.hypot(dirX, dirZ) || 1.0;
    const ux = dirX / len;
    const uz = dirZ / len;

    const minX = Math.min(x, x + ux * lookaheadM) - corridorHalfWidth - 6.0;
    const maxX = Math.max(x, x + ux * lookaheadM) + corridorHalfWidth + 6.0;
    const minZ = Math.min(z, z + uz * lookaheadM) - corridorHalfWidth - 6.0;
    const maxZ = Math.max(z, z + uz * lookaheadM) + corridorHalfWidth + 6.0;

    const obbs = this.getActiveOBBsInBounds(minX, minZ, maxX, maxZ);
    const groundHere = this.support.heightAt(x, z);
    let requiredAlt = (Number.isFinite(groundHere) ? groundHere : 0.0) + cruiseAboveGround;
    let highestObstacleTopY = -40.0;

    // 沿前瞻 30m 采样 7 个点
    for (let s = -4.0; s <= lookaheadM; s += 5.0) {
      const sx = x + ux * s;
      const sz = z + uz * s;
      const gh = this.support.heightAt(sx, sz);
      if (Number.isFinite(gh)) {
        requiredAlt = Math.max(requiredAlt, gh + cruiseAboveGround);
      }
      for (const obb of obbs) {
        const dx = sx - obb.center[0];
        const dz = sz - obb.center[2];
        const lx = Math.abs(dx * obb.axisX[0] + dz * obb.axisX[1]);
        const lz = Math.abs(dx * obb.axisZ[0] + dz * obb.axisZ[1]);
        if (lx <= obb.halfExtents[0] + corridorHalfWidth && lz <= obb.halfExtents[2] + corridorHalfWidth) {
          highestObstacleTopY = Math.max(highestObstacleTopY, obb.topY);
          requiredAlt = Math.max(requiredAlt, obb.topY + clearanceAboveObstacle);
        }
      }
    }

    return {
      safeAltitudeY: requiredAlt,
      highestObstacleTopY,
      hasObstacleAhead: highestObstacleTopY > -30.0,
    };
  }

  /**
   * 采样从 (startX, startZ) 到 (goalX, goalZ) 整条直升机航线的 30m 提前越障三维包络轨迹。
   */
  buildHelicopterFlightProfile(startX, startZ, goalX, goalZ, samples = 48) {
    const dx = goalX - startX;
    const dz = goalZ - startZ;
    const rawAlts = new Float32Array(samples);
    const points = [];

    for (let i = 0; i < samples; i++) {
      const t = i / (samples - 1);
      const px = startX + dx * t;
      const pz = startZ + dz * t;
      const q = this.queryHelicopterSafeAltitude(px, pz, dx, dz);
      rawAlts[i] = q.safeAltitudeY;
      points.push([px, q.safeAltitudeY, pz]);
    }

    // 5点滑动平滑包络（只增不减安全高度，保证平滑拉升与平滑下降且绝不低于障碍安全顶高）
    for (let i = 0; i < samples; i++) {
      let sum = 0;
      let cnt = 0;
      for (let k = Math.max(0, i - 3); k <= Math.min(samples - 1, i + 3); k++) {
        sum += rawAlts[k];
        cnt++;
      }
      points[i][1] = Math.max(rawAlts[i], sum / cnt);
    }
    return points;
  }

  /**
   * 编辑器新增建筑/道具/树木实例并立即触发自动物理派生。
   */
  addInstance({id, prototype_id, position, yaw_rad = 0, scale = [1, 1, 1]}) {
    const t0 = performance.now();
    const protoMeta = this.protoById.get(prototype_id) || this.bldgProtoByIndex[0];
    const gy = this.support.heightAt(position[0], position[2]);
    const finalPos = [
      position[0],
      Number.isFinite(gy) ? gy : (position[1] ?? 0),
      position[2],
    ];
    const instId = id || `added_${Date.now()}_${this.addedInstances.size + 1}`;
    const obb = deriveWorldOBB(protoMeta, finalPos, yaw_rad, scale);
    obb.instanceId = instId;
    obb.prototypeId = protoMeta.id;

    const record = {
      id: instId,
      prototype_id: protoMeta.id,
      position: finalPos,
      yaw_rad,
      scale: Array.isArray(scale) ? [...scale] : [scale, scale, scale],
      obb,
    };
    this.addedInstances.set(instId, record);
    this._recordRecalc(t0);
    record.recalcMs = this.stats.lastRecalcMs;
    const rad = Math.max(48, Math.hypot(obb.aabb[3] - obb.aabb[0], obb.aabb[5] - obb.aabb[2]) * 0.5 + 32);
    this._notifyOcclusionDirty(obb.center[0], obb.center[2], rad);
    return record;
  }

  /**
   * 编辑器修改已有或新增实例（移动自动贴地、旋转、缩放、替换原型 ID）。
   */
  updateInstance(instanceKey, {prototype_id, position, yaw_rad, scale, origPos}) {
    const t0 = performance.now();
    if (this.addedInstances.has(instanceKey)) {
      const cur = this.addedInstances.get(instanceKey);
      const prevObb = cur.obb;
      const nextProtoId = prototype_id ?? cur.prototype_id;
      const protoMeta = this.protoById.get(nextProtoId) || this.bldgProtoByIndex[0];
      const nextXZ = position ? [position[0], position[2]] : [cur.position[0], cur.position[2]];
      const gy = this.support.heightAt(nextXZ[0], nextXZ[1]);
      const nextPos = [nextXZ[0], Number.isFinite(gy) ? gy : cur.position[1], nextXZ[1]];
      const nextYaw = yaw_rad ?? cur.yaw_rad;
      const nextScale = scale ?? cur.scale;
      const obb = deriveWorldOBB(protoMeta, nextPos, nextYaw, nextScale);
      obb.instanceId = instanceKey;
      obb.prototypeId = protoMeta.id;

      const updated = {
        ...cur,
        prototype_id: protoMeta.id,
        position: nextPos,
        yaw_rad: nextYaw,
        scale: Array.isArray(nextScale) ? [...nextScale] : [nextScale, nextScale, nextScale],
        obb,
      };
      this.addedInstances.set(instanceKey, updated);
      this._recordRecalc(t0);
      if (prevObb) {
        const prevRad = Math.max(48, Math.hypot(prevObb.aabb[3] - prevObb.aabb[0], prevObb.aabb[5] - prevObb.aabb[2]) * 0.5 + 32);
        this._notifyOcclusionDirty(prevObb.center[0], prevObb.center[2], prevRad);
      }
      const nextRad = Math.max(48, Math.hypot(obb.aabb[3] - obb.aabb[0], obb.aabb[5] - obb.aabb[2]) * 0.5 + 32);
      this._notifyOcclusionDirty(obb.center[0], obb.center[2], nextRad);
      return updated;
    }

    const prev = this.modifiedInstances.get(instanceKey) || {};
    const prevObb = prev.obb || null;
    const nextProtoId = prototype_id ?? prev.prototype_id ?? this.bldgProtoByIndex[0].id;
    const protoMeta = this.protoById.get(nextProtoId) || this.bldgProtoByIndex[0];
    const basePos = position ?? prev.position ?? [0, 0, 0];
    const gy = this.support.heightAt(basePos[0], basePos[2]);
    const nextPos = [basePos[0], Number.isFinite(gy) ? gy : basePos[1], basePos[2]];
    const nextYaw = yaw_rad ?? prev.yaw_rad ?? 0;
    const nextScale = scale ?? prev.scale ?? [1, 1, 1];
    const obb = deriveWorldOBB(protoMeta, nextPos, nextYaw, nextScale);
    obb.instanceId = instanceKey;
    obb.prototypeId = protoMeta.id;

    const record = {
      key: instanceKey,
      prototype_id: protoMeta.id,
      position: nextPos,
      origPos: origPos ?? prev.origPos ?? nextPos,
      yaw_rad: nextYaw,
      scale: Array.isArray(nextScale) ? [...nextScale] : [nextScale, nextScale, nextScale],
      obb,
    };
    this.modifiedInstances.set(instanceKey, record);
    this._recordRecalc(t0);
    if (prevObb) {
      const prevRad = Math.max(48, Math.hypot(prevObb.aabb[3] - prevObb.aabb[0], prevObb.aabb[5] - prevObb.aabb[2]) * 0.5 + 32);
      this._notifyOcclusionDirty(prevObb.center[0], prevObb.center[2], prevRad);
    } else if (record.origPos) {
      this._notifyOcclusionDirty(record.origPos[0], record.origPos[2], 64);
    }
    const nextRad = Math.max(48, Math.hypot(obb.aabb[3] - obb.aabb[0], obb.aabb[5] - obb.aabb[2]) * 0.5 + 32);
    this._notifyOcclusionDirty(obb.center[0], obb.center[2], nextRad);
    return record;
  }

  /**
   * 编辑器删除实例并立即移除其碰撞与越障包络。
   */
  deleteInstance(instanceKey, worldPosHint = null) {
    const t0 = performance.now();
    const prevObb = this.addedInstances.get(instanceKey)?.obb || this.modifiedInstances.get(instanceKey)?.obb || null;
    if (this.addedInstances.has(instanceKey)) {
      this.addedInstances.delete(instanceKey);
    } else {
      this.modifiedInstances.delete(instanceKey);
      this.deletedKeys.add(worldPosHint ? {key: instanceKey, x: worldPosHint[0], z: worldPosHint[2]} : instanceKey);
    }
    this._recordRecalc(t0);
    if (prevObb) {
      const rad = Math.max(48, Math.hypot(prevObb.aabb[3] - prevObb.aabb[0], prevObb.aabb[5] - prevObb.aabb[2]) * 0.5 + 32);
      this._notifyOcclusionDirty(prevObb.center[0], prevObb.center[2], rad);
    } else if (worldPosHint && Number.isFinite(worldPosHint[0]) && Number.isFinite(worldPosHint[2])) {
      this._notifyOcclusionDirty(worldPosHint[0], worldPosHint[2], 64);
    } else {
      this._notifyOcclusionDirty(null, null, null);
    }
  }

  /**
   * 导出完整可复现的 scene-source.json 对象。
   */
  exportSceneSourceJSON(slotSwitchGlobal = 'original_refactored') {
    const modifiedObj = {};
    for (const [k, v] of this.modifiedInstances.entries()) {
      modifiedObj[k] = {
        prototype_id: v.prototype_id,
        position: v.position.map(n => Number(n.toFixed(3))),
        orig_position: v.origPos ? v.origPos.map(n => Number(n.toFixed(3))) : undefined,
        yaw_rad: Number(v.yaw_rad.toFixed(4)),
        scale: v.scale.map(n => Number(n.toFixed(3))),
      };
    }
    const modifiedRoadsObj = {};
    for (const [k, v] of this.modifiedRoadSplines.entries()) {
      modifiedRoadsObj[k] = v;
    }
    return {
      schema: 'scavenger-editable-scene-source-v1',
      version: 'V001',
      updated_at: new Date().toISOString(),
      bounds: this.b3Manifest.bounds,
      grid: {cells: [72, 72], chunk_m: 125.0, local_nav_step_m: 4.0, helicopter_lookahead_m: 30.0},
      building_kit_count: this.b3Manifest.building_prototypes.length,
      tree_kit_count: this.b2Manifest.tree_prototypes.length,
      slot_switch_global: slotSwitchGlobal,
      manual_overrides: {
        added_instances: Array.from(this.addedInstances.values()).map(item => ({
          id: item.id,
          prototype_id: item.prototype_id,
          position: item.position.map(n => Number(n.toFixed(3))),
          yaw_rad: Number(item.yaw_rad.toFixed(4)),
          scale: item.scale.map(n => Number(n.toFixed(3))),
        })),
        modified_instances: modifiedObj,
        deleted_instances: Array.from(this.deletedKeys).map(d => (typeof d === 'object' ? d.key : d)),
        terrain_height_strokes: this.terrainStrokes,
        splat_strokes: this.splatStrokes,
        modified_road_splines: modifiedRoadsObj,
      },
    };
  }

  /**
   * 从 scene-source.json 对象导入并重算全部派生碰撞、4m寻路与30m直升机越障包络。
   */
  importSceneSourceJSON(sceneSource) {
    const t0 = performance.now();
    this.addedInstances.clear();
    this.modifiedInstances.clear();
    this.deletedKeys.clear();

    const ov = sceneSource?.manual_overrides || {};
    for (const item of ov.added_instances || []) {
      this.addInstance(item);
    }
    for (const [k, mod] of Object.entries(ov.modified_instances || {})) {
      this.updateInstance(k, {
        prototype_id: mod.prototype_id,
        position: mod.position,
        yaw_rad: mod.yaw_rad,
        scale: mod.scale,
        origPos: mod.orig_position,
      });
    }
    for (const delKey of ov.deleted_instances || []) {
      this.deletedKeys.add(delKey);
    }
    this.terrainStrokes = [...(ov.terrain_height_strokes || [])];
    this.splatStrokes = [...(ov.splat_strokes || [])];
    this._recordRecalc(t0);
    this._notifyOcclusionDirty(null, null, null);
    return this.stats;
  }

  _recordRecalc(t0) {
    this.stats.recalcCount++;
    this.stats.lastRecalcMs = Number((performance.now() - t0).toFixed(3));
    this.stats.activeDynamicOBBs = this.addedInstances.size + this.modifiedInstances.size;
  }
}
