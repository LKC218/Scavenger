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
    this.stats = {
      recalcCount: 0,
      lastRecalcMs: 0,
      dirtyCellsCount: 0,
      activeDynamicOBBs: 0,
    };
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
   * 在 4 米局部寻路网格上使用 A* 搜索从 (startX, startZ) 到 (goalX, goalZ) 的平滑绕行路径。
   */
  findLocalPath4m(startX, startZ, goalX, goalZ, clearance = 5.5) {
    const step = 4.0;
    const pad = 80.0;
    const minX = Math.min(startX, goalX) - pad;
    const maxX = Math.max(startX, goalX) + pad;
    const minZ = Math.min(startZ, goalZ) - pad;
    const maxZ = Math.max(startZ, goalZ) + pad;

    const cols = Math.min(160, Math.max(16, Math.ceil((maxX - minX) / step)));
    const rows = Math.min(160, Math.max(16, Math.ceil((maxZ - minZ) / step)));
    const obbs = this.getActiveOBBsInBounds(minX - clearance, minZ - clearance, maxX + clearance, maxZ + clearance);

    const isCellBlocked = (ix, iz) => {
      const px = minX + (ix + 0.5) * step;
      const pz = minZ + (iz + 0.5) * step;
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
    };

    const sx = Math.max(0, Math.min(cols - 1, Math.floor((startX - minX) / step)));
    const sz = Math.max(0, Math.min(rows - 1, Math.floor((startZ - minZ) / step)));
    const gx = Math.max(0, Math.min(cols - 1, Math.floor((goalX - minX) / step)));
    const gz = Math.max(0, Math.min(rows - 1, Math.floor((goalZ - minZ) / step)));

    const total = cols * rows;
    const gScore = new Float32Array(total).fill(1e9);
    const parent = new Int32Array(total).fill(-1);
    const closed = new Uint8Array(total);
    const blockedGrid = new Uint8Array(total);
    for (let iz = 0; iz < rows; iz++) {
      for (let ix = 0; ix < cols; ix++) {
        blockedGrid[iz * cols + ix] = isCellBlocked(ix, iz) ? 1 : 0;
      }
    }
    // 起点与终点自身放行
    const startIdx = sz * cols + sx;
    const goalIdx = gz * cols + gx;
    blockedGrid[startIdx] = 0;
    blockedGrid[goalIdx] = 0;

    const open = [startIdx];
    gScore[startIdx] = 0;
    const dirs = [
      [1, 0, 1.0], [-1, 0, 1.0], [0, 1, 1.0], [0, -1, 1.0],
      [1, 1, 1.414], [1, -1, 1.414], [-1, 1, 1.414], [-1, -1, 1.414],
    ];

    let found = false;
    while (open.length > 0) {
      let bestPos = 0;
      let bestF = 1e9;
      for (let i = 0; i < open.length; i++) {
        const u = open[i];
        const ux = u % cols;
        const uz = Math.floor(u / cols);
        const f = gScore[u] + Math.hypot(gx - ux, gz - uz);
        if (f < bestF) {
          bestF = f;
          bestPos = i;
        }
      }
      const cur = open[bestPos];
      open.splice(bestPos, 1);
      if (cur === goalIdx) {
        found = true;
        break;
      }
      if (closed[cur]) continue;
      closed[cur] = 1;

      const cx = cur % cols;
      const cz = Math.floor(cur / cols);
      for (const [dx, dz, cost] of dirs) {
        const nx = cx + dx;
        const nz = cz + dz;
        if (nx < 0 || nx >= cols || nz < 0 || nz >= rows) continue;
        const nIdx = nz * cols + nx;
        if (closed[nIdx] || blockedGrid[nIdx]) continue;
        // 防止对角线穿墙角
        if (dx !== 0 && dz !== 0) {
          if (blockedGrid[cz * cols + nx] || blockedGrid[nz * cols + cx]) continue;
        }
        const tentativeG = gScore[cur] + cost;
        if (tentativeG < gScore[nIdx]) {
          gScore[nIdx] = tentativeG;
          parent[nIdx] = cur;
          open.push(nIdx);
        }
      }
    }

    const waypoints = [];
    if (found) {
      let curr = goalIdx;
      while (curr !== -1) {
        const ix = curr % cols;
        const iz = Math.floor(curr / cols);
        const wx = curr === startIdx ? startX : (curr === goalIdx ? goalX : minX + (ix + 0.5) * step);
        const wz = curr === startIdx ? startZ : (curr === goalIdx ? goalZ : minZ + (iz + 0.5) * step);
        const wy = this.support.heightAt(wx, wz) || 0;
        waypoints.push([wx, wy + 0.25, wz]);
        curr = parent[curr];
      }
      waypoints.reverse();
    } else {
      const sy = this.support.heightAt(startX, startZ) || 0;
      const gy = this.support.heightAt(goalX, goalZ) || 0;
      waypoints.push([startX, sy + 0.25, startZ], [goalX, gy + 0.25, goalZ]);
    }

    return {
      found,
      waypoints,
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
    return record;
  }

  /**
   * 编辑器修改已有或新增实例（移动自动贴地、旋转、缩放、替换原型 ID）。
   */
  updateInstance(instanceKey, {prototype_id, position, yaw_rad, scale, origPos}) {
    const t0 = performance.now();
    if (this.addedInstances.has(instanceKey)) {
      const cur = this.addedInstances.get(instanceKey);
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
      return updated;
    }

    const prev = this.modifiedInstances.get(instanceKey) || {};
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
    return record;
  }

  /**
   * 编辑器删除实例并立即移除其碰撞与越障包络。
   */
  deleteInstance(instanceKey, worldPosHint = null) {
    const t0 = performance.now();
    if (this.addedInstances.has(instanceKey)) {
      this.addedInstances.delete(instanceKey);
    } else {
      this.modifiedInstances.delete(instanceKey);
      this.deletedKeys.add(worldPosHint ? {key: instanceKey, x: worldPosHint[0], z: worldPosHint[2]} : instanceKey);
    }
    this._recordRecalc(t0);
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
    return this.stats;
  }

  _recordRecalc(t0) {
    this.stats.recalcCount++;
    this.stats.lastRecalcMs = Number((performance.now() - t0).toFixed(3));
    this.stats.activeDynamicOBBs = this.addedInstances.size + this.modifiedInstances.size;
  }
}
