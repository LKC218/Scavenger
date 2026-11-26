/**
 * Scavenger 9km 战场战争迷雾权威数据与空地分级侦察引擎（契约版本：fog-of-war-v1）。
 * -------------------------------------------------------------------------------
 * 统筹衔接 SCAV-8（规则与数据层）与 SCAV-9（3D 渲染与小地图同步层）：
 * - 零 Three.js / DOM 顶层依赖，可在 Node ESM 纯规则单测与浏览器主循环共用；
 * - 维护 256×256（可配 512）RGBA8 双缓冲（prevRGBA / currRGBA）：
 *     R = 实时己方精细视野强度 (0..255)
 *     G = 历史已探索地形强度 (0..255，单调累积)
 *     B = 侦察穿透档位 (0 / 128=高空广域 / 255=低空或地面精细查漏)
 *     A = 电战/声源宏观态势热区 (0..255，随时间衰减，仅小地图叠绘不破除 3D 迷雾)
 * - 全部侦察半径与走廊宽度统一经 数值基底与倍率规范.mjs::deriveUnitReconProfile 派生。
 */

import {
  GLOBAL_BALANCE_SCALES,
  deriveUnitReconProfile,
} from './数值基底与倍率规范.mjs';

export const FOG_CONTRACT_VERSION = 'fog-of-war-v1';

export const DEFAULT_FOG_BOUNDS = Object.freeze({
  minX: -4425,
  maxX: 4575,
  minZ: -4150,
  maxZ: 4850,
});

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function smoothstep(edge0, edge1, x) {
  if (edge1 <= edge0) return x >= edge1 ? 1 : 0;
  const t = clamp((x - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

/**
 * 唯一世界坐标 → 战争迷雾归一化 UV 映射（3D FogOfWarPass / 地形 / 小地图 / 数据层共用）。
 */
export function worldToFogUV(wx, wz, bounds = DEFAULT_FOG_BOUNDS) {
  const b = bounds || DEFAULT_FOG_BOUNDS;
  const spanX = Math.max(1, (b.maxX ?? 4575) - (b.minX ?? -4425));
  const spanZ = Math.max(1, (b.maxZ ?? 4850) - (b.minZ ?? -4150));
  const u = clamp(((Number(wx) || 0) - b.minX) / spanX, 0, 1);
  const v = clamp(((Number(wz) || 0) - b.minZ) / spanZ, 0, 1);
  return { u, v };
}

/**
 * 世界坐标 → 迷雾网格连续/离散栅格索引。
 */
export function worldToFogCell(wx, wz, width = 256, height = 256, bounds = DEFAULT_FOG_BOUNDS) {
  const { u, v } = worldToFogUV(wx, wz, bounds);
  const fx = u * (width - 1);
  const fz = v * (height - 1);
  return {
    u,
    v,
    fx,
    fz,
    ix: clamp(Math.round(fx), 0, width - 1),
    iz: clamp(Math.round(fz), 0, height - 1),
  };
}

/**
 * 点到线段的最短距离平方（用于高速固定翼/无人机胶囊扫掠光栅化，防 10Hz 串珠断节）。
 */
function distSqPointToSegment(px, pz, ax, az, bx, bz) {
  const abx = bx - ax;
  const abz = bz - az;
  const apx = px - ax;
  const apz = pz - az;
  const abLenSq = abx * abx + abz * abz;
  if (abLenSq <= 1e-6) {
    return apx * apx + apz * apz;
  }
  const t = clamp((apx * abx + apz * abz) / abLenSq, 0, 1);
  const cx = ax + abx * t;
  const cz = az + abz * t;
  const dx = px - cx;
  const dz = pz - cz;
  return dx * dx + dz * dz;
}

/**
 * 从任意运行时单位对象提取标准化 FogVisionEmitter（兼容场景运行 heroUnits、固定翼底座与纯规则测试对象）。
 */
export function extractUnitVisionEmitter(unit, prevPosMap = null, scales = GLOBAL_BALANCE_SCALES) {
  if (!unit || unit.evacuated || unit.destroyed) return null;
  if (unit.node && unit.node.visible === false) return null;

  const pos = unit.node?.position || unit.position || unit;
  const x = Number(pos?.x);
  const z = Number(pos?.z);
  if (!Number.isFinite(x) || !Number.isFinite(z)) return null;

  const rawId = unit.id || unit.unitId;
  const id = rawId
    ? String(rawId)
    : (Number.isFinite(unit.index) ? `idx_${unit.index}` : '');
  const slug = String(unit.slug || unit.fixedWingSpec?.slug || unit.meta?.slug || '');
  const fwState = unit.fixedWingController?.getUnitState?.(unit) || null;
  const fwSpec = fwState?.spec || unit.fixedWingSpec || null;
  const archetype = String(unit.archetype || fwSpec?.archetype || '');
  const rawRole = String(fwSpec?.role || unit.role || 'ground');
  const isUav = rawRole === 'uav' || archetype === 'fixed_wing_uav';
  const role = isUav ? 'uav' : (rawRole === 'fixed_wing' || fwSpec ? 'fixed_wing' : rawRole === 'helicopter' || rawRole === 'heli' ? 'helicopter' : 'ground');

  // 坠毁或已撤离单位停止发射视野
  const flightState = String(fwState?.flightState || unit.flightAttitude?.flightState || '');
  if (flightState === 'crashed' || flightState === 'crash' || flightState === 'evacuated') {
    return null;
  }

  const yaw = Number.isFinite(unit.node?.rotation?.y)
    ? unit.node.rotation.y
    : Number.isFinite(unit.yaw)
      ? unit.yaw
      : 0;

  const altModeRaw = fwState?.altitudeMode || unit.altitudeMode || (role === 'ground' ? 'ground' : 'high');
  const altitudeMode = altModeRaw === 'low' ? 'low' : altModeRaw === 'ground' ? 'ground' : 'high';

  const sourceRef = fwSpec?.sourceRef || unit.sourceRef || {
    opticsM: Number(unit.opticsM) || Number(unit.visionRangeM) || undefined,
  };
  const recon = deriveUnitReconProfile(sourceRef, { role, archetype, slug }, scales);

  const prev = prevPosMap && id ? prevPosMap.get(id) : null;
  const prevX = prev && Number.isFinite(prev.x) ? prev.x : x;
  const prevZ = prev && Number.isFinite(prev.z) ? prev.z : z;
  const jumpSq = (x - prevX) * (x - prevX) + (z - prevZ) * (z - prevZ);
  if (prevPosMap && id) {
    if (prev) {
      prev.x = x;
      prev.z = z;
    } else {
      prevPosMap.set(id, { x, z });
    }
  }
  const safePrevX = jumpSq > 900 * 900 ? x : prevX;
  const safePrevZ = jumpSq > 900 * 900 ? z : prevZ;

  // 燃油与 Bingo 返航预警（联动 SCAV-6）
  const fuelTel = fwState?.fuelTelemetry || unit.fuelTelemetry || null;
  const fuelPct = Number.isFinite(fuelTel?.fuelPct) ? fuelTel.fuelPct : 100;
  const fuelRatio = clamp(fuelPct / 100, 0, 1);
  const bingoState = String(fuelTel?.bingoState || 'nominal');
  const isRtb = flightState === 'rtb' || bingoState === 'rtb_bingo';
  const bingoWarning = Boolean(
    fuelTel && (bingoState === 'low' || bingoState === 'bingo' || bingoState === 'rtb_bingo' || isRtb)
  );

  // 盘旋态判断（UAV / AC-130 光电凝视中心）
  const oc = fwState?.orbitCenter || unit.orbitCenter || null;
  const hasOrbitCenter = Boolean(oc && Number.isFinite(oc.x) && Number.isFinite(oc.z));
  const isOrbiting = hasOrbitCenter && (flightState === 'orbit' || flightState === '' || unit.isOrbiting === true);

  let shape = recon.shape;
  let liveRadiusM = altitudeMode === 'low' ? recon.lowRadiusM : recon.highRadiusM;
  let corridorHalfWidthM = recon.corridorHalfWidthM;
  let corridorForwardM = recon.corridorForwardM;
  let ewPulseRadiusM = recon.ewPulseRadiusM;
  let orbitCenterX = null;
  let orbitCenterZ = null;

  // UAV 处于 RTB 返航途中时收窄光电吊舱半径（体现即将断视收拢节奏）
  if (isUav && isRtb) {
    liveRadiusM = Math.max(90, Math.round(liveRadiusM * 0.55));
  } else if (isUav && isOrbiting) {
    // 盘旋凝视时将光电聚焦中心轻拉向盘旋圆心（不超过盘旋半径与机下中点，保证画面自然连贯）
    const dxOc = oc.x - x;
    const dzOc = oc.z - z;
    const distOc = Math.hypot(dxOc, dzOc);
    if (distOc <= Math.max(420, liveRadiusM * 0.65)) {
      orbitCenterX = x + dxOc * 0.55;
      orbitCenterZ = z + dzOc * 0.55;
    }
  }

  // AC-130 炮艇：仅盘旋态开启中等光电聚焦，赶路/突击退回窄走廊
  if (recon.profileKey === 'cas_gunship_orbit_pod') {
    if (isOrbiting && !isRtb) {
      shape = 'orbit_pod';
      orbitCenterX = oc.x;
      orbitCenterZ = oc.z;
    } else {
      shape = 'corridor';
    }
  }

  const penetrationTier = altitudeMode === 'low' || role === 'ground' || role === 'helicopter'
    ? recon.lowPenetrationTier
    : recon.highPenetrationTier;

  return {
    unitId: id,
    slug,
    role,
    archetype: archetype || recon.profileKey,
    shape,
    x,
    z,
    prevX: safePrevX,
    prevZ: safePrevZ,
    yaw,
    altitudeMode,
    liveRadiusM,
    corridorHalfWidthM,
    corridorForwardM,
    ewPulseRadiusM,
    orbitCenterX,
    orbitCenterZ,
    writesExploredG: recon.writesExploredG,
    ignoresGroundOcclusion: recon.ignoresGroundOcclusion,
    penetrationTier,
    fuelRatio,
    bingoWarning,
    isRtb,
    active: true,
  };
}

export class FogOfWarEngine {
  /**
   * @param {{
   *   width?: number,
   *   height?: number,
   *   bounds?: { minX: number, maxX: number, minZ: number, maxZ: number },
   *   tickIntervalSec?: number,
   *   ghostExpireSec?: number,
   *   ewDecayPerSec?: number,
   *   scales?: typeof GLOBAL_BALANCE_SCALES,
   *   terrainSupport?: { heightAt?: (x:number,z:number)=>number },
   *   occlusionQuery?: (x0:number,z0:number,x1:number,z1:number)=>boolean,
   * }} [options]
   */
  constructor(options = {}) {
    this.width = Math.max(32, Math.min(512, Math.round(Number(options.width) || 256)));
    this.height = Math.max(32, Math.min(512, Math.round(Number(options.height) || 256)));
    this.bounds = {
      ...DEFAULT_FOG_BOUNDS,
      ...(options.bounds || {}),
    };
    this.spanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
    this.spanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);
    this.cellSizeX = this.spanX / Math.max(1, this.width - 1);
    this.cellSizeZ = this.spanZ / Math.max(1, this.height - 1);

    this.tickIntervalSec = Math.max(0.02, Number(options.tickIntervalSec) || 0.1);
    this.ghostExpireSec = Math.max(5, Number(options.ghostExpireSec) || 45);
    this.ewDecayPerSec = Math.max(0.05, Number(options.ewDecayPerSec) || 0.42);
    this.scales = options.scales || GLOBAL_BALANCE_SCALES;
    this.terrainSupport = options.terrainSupport || null;
    this.occlusionQuery = typeof options.occlusionQuery === 'function' ? options.occlusionQuery : null;
    // 默认关闭静态位图缓存以完整保留公开 occlusionQuery(x0,z0,x1,z1) 契约；主场景显式开启
    this.enableStaticOcclusionCache = Boolean(options.enableStaticOcclusionCache);
    this.buildingOcclusionRasterizer = typeof options.buildingOcclusionRasterizer === 'function'
      ? options.buildingOcclusionRasterizer
      : null;

    const pixelCount = this.width * this.height;
    this.prevRGBA = new Uint8Array(pixelCount * 4);
    this.currRGBA = new Uint8Array(pixelCount * 4);
    this._exploredGrid = new Uint8Array(pixelCount);
    this._ewGrid = new Float32Array(pixelCount);
    // 地面粗遮挡射线复用缓冲（36 方位，每方位 10°）+ 预计算三角表
    this._rayBins = 36;
    this._rayMaxDist = new Float32Array(this._rayBins);
    this._rayCos = new Float32Array(this._rayBins);
    this._raySin = new Float32Array(this._rayBins);
    for (let b = 0; b < this._rayBins; b += 1) {
      const ang = (b / this._rayBins) * Math.PI * 2;
      this._rayCos[b] = Math.cos(ang);
      this._raySin[b] = Math.sin(ang);
    }

    // 512×512 惰性静态遮挡位图（0=未采样 / 1=通透 / 2=阻挡，按 8×8 块懒填充）与高程缓存
    this._occSize = 512;
    this._occTileSize = 8;
    this._occTileDiv = this._occSize / this._occTileSize;
    this._occGrid = null;
    this._occTileState = null;
    this._heightCache = null;
    this._occEnvVersion = 1;
    if (this.enableStaticOcclusionCache) {
      this._ensureStaticCacheBuffers();
    }

    // 静止地面单位射线复用缓存（严格禁止空 unitId 写入）与复用 Set
    this._unitRayCache = new Map();
    this._seenIds = new Set();
    this._aliveEnemyIds = new Set();

    this.gridVersion = 0;
    this.updatedAtSec = 0;
    this._lastTickSec = -Infinity;
    this._prevUnitPos = new Map();
    this._emitters = [];
    this._ghostMap = new Map();
    this._ghostList = [];
    this._hasActiveEw = false;
    this._ewDirtyAlpha = false;
    this._wasFullReveal = false;
    this.enabled = true;
    this.revealAllDebug = false;
  }

  _ensureStaticCacheBuffers() {
    if (!this._occGrid) {
      this._occGrid = new Uint8Array(this._occSize * this._occSize);
      this._occTileState = new Uint8Array(this._occTileDiv * this._occTileDiv);
      this._heightCache = new Float32Array(this._occSize * this._occSize);
      this._heightCache.fill(NaN);
    }
  }

  /**
   * 全量失效静态建筑遮挡缓存、高程缓存与单位射线缓存（全精度升级或整图导入后调用）。
   */
  invalidateOcclusionCache() {
    if (this._occGrid) this._occGrid.fill(0);
    if (this._occTileState) this._occTileState.fill(0);
    if (this._heightCache) this._heightCache.fill(NaN);
    this._occEnvVersion += 1;
    this._unitRayCache.clear();
  }

  /**
   * 局部失效指定世界坐标与半径内的建筑遮挡缓存、高程缓存及受影响单位射线缓存（供 /editor 实时编辑调用）。
   */
  invalidateOcclusionRegion(wx, wz, radiusM = 48) {
    if (!Number.isFinite(wx) || !Number.isFinite(wz)) {
      this.invalidateOcclusionCache();
      return;
    }
    const r = Math.max(24, Number(radiusM) || 48);
    if (this._occGrid && this._occTileState && this._heightCache) {
      const n = this._occSize;
      const invSpanX = n / this.spanX;
      const invSpanZ = n / this.spanZ;
      const ix0 = clamp(Math.floor((wx - r - this.bounds.minX) * invSpanX), 0, n - 1);
      const ix1 = clamp(Math.floor((wx + r - this.bounds.minX) * invSpanX), 0, n - 1);
      const iz0 = clamp(Math.floor((wz - r - this.bounds.minZ) * invSpanZ), 0, n - 1);
      const iz1 = clamp(Math.floor((wz + r - this.bounds.minZ) * invSpanZ), 0, n - 1);

      // 失效重叠的 8×8 静态遮挡块（重置整块以便下次按完整块重光栅化）
      const tx0 = ix0 >> 3;
      const tx1 = ix1 >> 3;
      const tz0 = iz0 >> 3;
      const tz1 = iz1 >> 3;
      for (let tz = tz0; tz <= tz1; tz += 1) {
        const tileRow = tz * this._occTileDiv;
        const cz0 = tz << 3;
        const cz1 = cz0 + 7;
        for (let tx = tx0; tx <= tx1; tx += 1) {
          this._occTileState[tileRow + tx] = 0;
          const cx0 = tx << 3;
          const cx1 = cx0 + 7;
          for (let iz = cz0; iz <= cz1; iz += 1) {
            const rowOff = iz * n;
            for (let ix = cx0; ix <= cx1; ix += 1) {
              this._occGrid[rowOff + ix] = 0;
            }
          }
        }
      }

      // 失效受影响范围内的高程缓存（供地形笔刷实时生效）
      for (let iz = iz0; iz <= iz1; iz += 1) {
        const rowOff = iz * n;
        for (let ix = ix0; ix <= ix1; ix += 1) {
          this._heightCache[rowOff + ix] = NaN;
        }
      }
    }

    this._occEnvVersion += 1;
    // 清除视野可能覆盖该区域的单位射线缓存
    for (const [uid, cached] of this._unitRayCache.entries()) {
      const maxReach = (cached.radiusM || 0) + r + 24;
      const dx = cached.x - wx;
      const dz = cached.z - wz;
      if (dx * dx + dz * dz <= maxReach * maxReach) {
        this._unitRayCache.delete(uid);
      }
    }
  }

  /**
   * 绑定地形高度支撑或建筑粗遮挡回调。
   */
  setEnvironment({
    terrainSupport = undefined,
    occlusionQuery = undefined,
    enableStaticOcclusionCache = undefined,
    buildingOcclusionRasterizer = undefined,
  } = {}) {
    if (terrainSupport !== undefined) this.terrainSupport = terrainSupport;
    if (occlusionQuery !== undefined) {
      this.occlusionQuery = typeof occlusionQuery === 'function' ? occlusionQuery : null;
    }
    if (enableStaticOcclusionCache !== undefined) {
      this.enableStaticOcclusionCache = Boolean(enableStaticOcclusionCache);
      if (this.enableStaticOcclusionCache) this._ensureStaticCacheBuffers();
    }
    if (buildingOcclusionRasterizer !== undefined) {
      this.buildingOcclusionRasterizer = typeof buildingOcclusionRasterizer === 'function'
        ? buildingOcclusionRasterizer
        : null;
    }
    this.invalidateOcclusionCache();
  }

  /**
   * 重置全图探索、视野、热区与失联幽灵。
   */
  reset() {
    this.prevRGBA.fill(0);
    this.currRGBA.fill(0);
    this._exploredGrid.fill(0);
    this._ewGrid.fill(0);
    this._prevUnitPos.clear();
    this._unitRayCache.clear();
    this._seenIds.clear();
    this._aliveEnemyIds.clear();
    this._emitters = [];
    this._ghostMap.clear();
    this._ghostList = [];
    this._hasActiveEw = false;
    this._ewDirtyAlpha = false;
    this._wasFullReveal = false;
    this._lastTickSec = -Infinity;
    this.gridVersion += 1;
  }

  /**
   * 开局或事件预解锁一片已探索地形（仅写入 G 通道，不伪造实时视野 R）。
   */
  revealInitialZone(cx, cz, radiusM = 450) {
    const r = Math.max(40, Number(radiusM) || 450);
    const softInner = r * 0.72;
    const minIx = clamp(Math.floor(((cx - r - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const maxIx = clamp(Math.ceil(((cx + r - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const minIz = clamp(Math.floor(((cz - r - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);
    const maxIz = clamp(Math.ceil(((cz + r - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);

    for (let iz = minIz; iz <= maxIz; iz += 1) {
      const wz = this.bounds.minZ + (iz / (this.height - 1)) * this.spanZ;
      const dz = wz - cz;
      const row = iz * this.width;
      for (let ix = minIx; ix <= maxIx; ix += 1) {
        const wx = this.bounds.minX + (ix / (this.width - 1)) * this.spanX;
        const dist = Math.hypot(wx - cx, dz);
        if (dist > r) continue;
        const alpha = 1 - smoothstep(softInner, r, dist);
        const val = Math.round(alpha * 255);
        const pi = row + ix;
        if (val > this._exploredGrid[pi]) {
          this._exploredGrid[pi] = val;
          this.currRGBA[pi * 4 + 1] = val;
          this.prevRGBA[pi * 4 + 1] = val;
        }
      }
    }
    this.gridVersion += 1;
  }

  /**
   * 外部声源/电战脉冲写入 A 通道宏观态势热区（不破除 3D 精细迷雾）。
   */
  addEwPulse(cx, cz, radiusM = 400, strength = 0.75) {
    const r = Math.max(60, Number(radiusM) || 400);
    const s = clamp(Number(strength) || 0.75, 0, 1);
    const minIx = clamp(Math.floor(((cx - r - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const maxIx = clamp(Math.ceil(((cx + r - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const minIz = clamp(Math.floor(((cz - r - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);
    const maxIz = clamp(Math.ceil(((cz + r - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);

    for (let iz = minIz; iz <= maxIz; iz += 1) {
      const wz = this.bounds.minZ + (iz / (this.height - 1)) * this.spanZ;
      const dz = wz - cz;
      const row = iz * this.width;
      for (let ix = minIx; ix <= maxIx; ix += 1) {
        const wx = this.bounds.minX + (ix / (this.width - 1)) * this.spanX;
        const dist = Math.hypot(wx - cx, dz);
        if (dist > r) continue;
        const v = (1 - smoothstep(r * 0.25, r, dist)) * s;
        const pi = row + ix;
        if (v > this._ewGrid[pi]) {
          this._ewGrid[pi] = v;
          this._hasActiveEw = true;
          this._ewDirtyAlpha = true;
        }
      }
    }
  }

  /**
   * 惰性查询 512×512 静态建筑遮挡位图（按 8×8 格块懒光栅化，零启动耗时）。
   */
  _sampleCachedBuildingOcclusion(cx, cz, wx, wz) {
    const n = this._occSize;
    const ix = clamp(Math.floor(((wx - this.bounds.minX) / this.spanX) * n), 0, n - 1);
    const iz = clamp(Math.floor(((wz - this.bounds.minZ) / this.spanZ) * n), 0, n - 1);
    const idx = iz * n + ix;

    if (this.buildingOcclusionRasterizer) {
      const tx = ix >> 3;
      const tz = iz >> 3;
      const tIdx = tz * this._occTileDiv + tx;
      if (this._occTileState[tIdx] === 0) {
        const x0 = tx << 3;
        const z0 = tz << 3;
        const x1 = x0 + 7;
        const z1 = z0 + 7;
        for (let gz = z0; gz <= z1; gz += 1) {
          const rowOff = gz * n;
          for (let gx = x0; gx <= x1; gx += 1) {
            this._occGrid[rowOff + gx] = 1;
          }
        }
        this.buildingOcclusionRasterizer(this._occGrid, n, n, this.bounds, x0, z0, x1, z1);
        this._occTileState[tIdx] = 1;
      }
      return this._occGrid[idx] === 2;
    }

    if (this.occlusionQuery) {
      let st = this._occGrid[idx];
      if (st === 0) {
        const cellX = this.bounds.minX + ((ix + 0.5) / n) * this.spanX;
        const cellZ = this.bounds.minZ + ((iz + 0.5) / n) * this.spanZ;
        st = this.occlusionQuery(cx, cz, cellX, cellZ) ? 2 : 1;
        this._occGrid[idx] = st;
      }
      return st === 2;
    }
    return false;
  }

  /**
   * 惰性查询 512×512 地形高程缓存。
   */
  _sampleCachedHeight(wx, wz) {
    const n = this._occSize;
    const ix = clamp(Math.floor(((wx - this.bounds.minX) / this.spanX) * n), 0, n - 1);
    const iz = clamp(Math.floor(((wz - this.bounds.minZ) / this.spanZ) * n), 0, n - 1);
    const idx = iz * n + ix;
    let h = this._heightCache[idx];
    if (h !== h) {
      const cellX = this.bounds.minX + ((ix + 0.5) / n) * this.spanX;
      const cellZ = this.bounds.minZ + ((iz + 0.5) / n) * this.spanZ;
      const raw = Number(this.terrainSupport.heightAt(cellX, cellZ));
      h = Number.isFinite(raw) ? raw : -1e9;
      this._heightCache[idx] = h;
    }
    return h <= -1e8 ? NaN : h;
  }

  /**
   * 地面单位粗略 36 方位高差 + 建筑遮挡预计算（低开销，返回是否存在任意方位遮挡）。
   */
  _computeGroundRayDistances(cx, cz, maxRadiusM, outRays = this._rayMaxDist) {
    const bins = this._rayBins;
    const ts = this.terrainSupport;
    const hasHeight = Boolean(ts && typeof ts.heightAt === 'function');
    const useCache = this.enableStaticOcclusionCache;
    const occFn = this.occlusionQuery;
    const hasOcc = useCache
      ? Boolean(this.buildingOcclusionRasterizer || occFn)
      : Boolean(occFn);

    if (!hasHeight && !hasOcc) {
      outRays.fill(maxRadiusM);
      return false;
    }
    if (useCache) {
      this._ensureStaticCacheBuffers();
    }

    const originY = hasHeight ? ((Number(ts.heightAt(cx, cz)) || 0) + 2.8) : 2.8;
    const stepM = Math.max(24, maxRadiusM / 12);
    const cosTable = this._rayCos;
    const sinTable = this._raySin;
    let hasOcclusion = false;

    for (let b = 0; b < bins; b += 1) {
      const dx = cosTable[b];
      const dz = sinTable[b];
      let blockedDist = maxRadiusM;
      let maxSlope = -Infinity;

      for (let d = stepM; d <= maxRadiusM; d += stepM) {
        const px = cx + dx * d;
        const pz = cz + dz * d;
        if (hasOcc) {
          const blocked = useCache
            ? this._sampleCachedBuildingOcclusion(cx, cz, px, pz)
            : occFn(cx, cz, px, pz);
          if (blocked) {
            // 命中建筑后截断（留出 0.4 步距确保建筑外墙本身可见）
            blockedDist = Math.min(maxRadiusM, d + stepM * 0.4);
            break;
          }
        }
        if (hasHeight) {
          const py = useCache
            ? this._sampleCachedHeight(px, pz)
            : Number(ts.heightAt(px, pz));
          if (Number.isFinite(py)) {
            const slope = (py - originY) / d;
            // 山脊/高坡遮挡判定：若当前视线仰角明显低于前方山脊仰角且落差超过 6.5m
            if (d > stepM * 1.5 && slope < maxSlope - 0.045 && (originY + maxSlope * d - py) > 6.5) {
              blockedDist = Math.min(maxRadiusM, d);
              break;
            }
            if (slope > maxSlope) maxSlope = slope;
          }
        }
      }
      outRays[b] = blockedDist;
      if (blockedDist < maxRadiusM - 0.5) {
        hasOcclusion = true;
      }
    }
    return hasOcclusion;
  }

  /**
   * 静止地面单位射线复用缓存查询（仅缓存非空 unitId，严格杜绝空键碰撞）。
   */
  _getOrComputeGroundRays(em) {
    const uid = em.unitId;
    if (uid) {
      let entry = this._unitRayCache.get(uid);
      if (
        entry &&
        entry.envVersion === this._occEnvVersion &&
        Math.abs(em.x - entry.x) < 4.0 &&
        Math.abs(em.z - entry.z) < 4.0 &&
        Math.abs(em.liveRadiusM - entry.radiusM) < 1.0
      ) {
        return entry;
      }
      if (!entry) {
        entry = {
          x: em.x,
          z: em.z,
          radiusM: em.liveRadiusM,
          envVersion: this._occEnvVersion,
          rays: new Float32Array(this._rayBins),
          hasOcclusion: false,
        };
        this._unitRayCache.set(uid, entry);
      } else {
        entry.x = em.x;
        entry.z = em.z;
        entry.radiusM = em.liveRadiusM;
        entry.envVersion = this._occEnvVersion;
      }
      entry.hasOcclusion = this._computeGroundRayDistances(em.x, em.z, em.liveRadiusM, entry.rays);
      return entry;
    }

    // 空 unitId 严禁写入 _unitRayCache
    const hasOcclusion = this._computeGroundRayDistances(em.x, em.z, em.liveRadiusM, this._rayMaxDist);
    return { rays: this._rayMaxDist, hasOcclusion };
  }

  /**
   * 将圆形或胶囊扫掠视野光栅化写入 currRGBA（零堆分配，无遮挡走快速光栅路径）。
   */
  _rasterizeCapsule(ax, az, bx, bz, radiusM, writesG, penetrationTier, useGroundOcclusion, rayInfo = null) {
    if (radiusM <= 1) return;
    let rays = this._rayMaxDist;
    let applyOcclusion = false;
    if (useGroundOcclusion) {
      if (rayInfo) {
        rays = rayInfo.rays;
        applyOcclusion = Boolean(rayInfo.hasOcclusion);
      } else {
        applyOcclusion = this._computeGroundRayDistances(bx, bz, radiusM, this._rayMaxDist);
      }
    }

    const minX = Math.min(ax, bx) - radiusM;
    const maxX = Math.max(ax, bx) + radiusM;
    const minZ = Math.min(az, bz) - radiusM;
    const maxZ = Math.max(az, bz) + radiusM;

    const minIx = clamp(Math.floor(((minX - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const maxIx = clamp(Math.ceil(((maxX - this.bounds.minX) / this.spanX) * (this.width - 1)), 0, this.width - 1);
    const minIz = clamp(Math.floor(((minZ - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);
    const maxIz = clamp(Math.ceil(((maxZ - this.bounds.minZ) / this.spanZ) * (this.height - 1)), 0, this.height - 1);

    const rSq = radiusM * radiusM;
    const softInner = radiusM * 0.76;
    const softInnerSq = softInner * softInner;
    const bins = this._rayBins;
    const invTwoPi = bins / (Math.PI * 2);
    const invW = 1 / Math.max(1, this.width - 1);
    const invH = 1 / Math.max(1, this.height - 1);
    const bMinX = this.bounds.minX;
    const bMinZ = this.bounds.minZ;
    const spanX = this.spanX;
    const spanZ = this.spanZ;
    const currRGBA = this.currRGBA;
    const exploredGrid = this._exploredGrid;
    const isCircle = Math.abs(bx - ax) < 1e-3 && Math.abs(bz - az) < 1e-3;

    for (let iz = minIz; iz <= maxIz; iz += 1) {
      const wz = bMinZ + (iz * invH) * spanZ;
      const dzCircle = wz - bz;
      const dzCircleSq = dzCircle * dzCircle;
      const row = iz * this.width;
      for (let ix = minIx; ix <= maxIx; ix += 1) {
        const wx = bMinX + (ix * invW) * spanX;
        const dSq = isCircle
          ? ((wx - bx) * (wx - bx) + dzCircleSq)
          : distSqPointToSegment(wx, wz, ax, az, bx, bz);
        if (dSq > rSq) continue;

        let byteVal;
        if (applyOcclusion) {
          const rx = wx - bx;
          const rz = wz - bz;
          const rayDist = Math.hypot(rx, rz);
          let ang = Math.atan2(rz, rx);
          if (ang < 0) ang += Math.PI * 2;
          const fBin = ang * invTwoPi;
          const b0 = Math.floor(fBin) % bins;
          const b1 = (b0 + 1) % bins;
          const frac = fBin - Math.floor(fBin);
          const maxRay = rays[b0] * (1 - frac) + rays[b1] * frac;
          if (rayDist > maxRay) continue;
          const effectiveRadius = Math.min(radiusM, maxRay);
          const inner = Math.min(softInner, effectiveRadius * 0.78);
          const alpha = 1 - smoothstep(inner, effectiveRadius, rayDist);
          byteVal = Math.round(alpha * 255);
        } else if (dSq <= softInnerSq) {
          byteVal = 255;
        } else {
          const dist = Math.sqrt(dSq);
          const alpha = 1 - smoothstep(softInner, radiusM, dist);
          byteVal = Math.round(alpha * 255);
        }
        if (byteVal <= 0) continue;

        const pi = row + ix;
        const off = pi * 4;
        if (byteVal > currRGBA[off]) {
          currRGBA[off] = byteVal;
        }
        if (writesG && byteVal > exploredGrid[pi]) {
          exploredGrid[pi] = byteVal;
          currRGBA[off + 1] = byteVal;
        }
        if (byteVal >= 40 && penetrationTier > currRGBA[off + 2]) {
          currRGBA[off + 2] = penetrationTier;
        }
      }
    }
  }

  /**
   * 将有人固定翼的「航迹胶囊 + 机头前向窄锥走廊」写入 currRGBA。
   */
  _rasterizeFixedWingCorridor(em) {
    const halfW = Math.max(40, em.corridorHalfWidthM || 140);
    const fwdLen = Math.max(80, em.corridorForwardM || 420);
    const fx = Math.sin(em.yaw || 0);
    const fz = Math.cos(em.yaw || 0);
    const noseX = em.x + fx * fwdLen;
    const noseZ = em.z + fz * fwdLen;

    // 1) 上帧→当前航迹窄带胶囊（避免高速飞行串珠断节）
    this._rasterizeCapsule(em.prevX, em.prevZ, em.x, em.z, halfW, em.writesExploredG, em.penetrationTier, false);
    // 2) 当前机身→机头前向探测窄带
    this._rasterizeCapsule(em.x, em.z, noseX, noseZ, halfW * 0.92, em.writesExploredG, em.penetrationTier, false);
  }

  /**
   * 驱动 10Hz 战争迷雾更新。若未到节流周期且未传 force=true，直接跳过光栅化保持零开销。
   * @param {{ units?: any[], enemies?: any[], nowSec?: number, force?: boolean }} params
   * @returns {boolean} 本次是否发生了 10Hz 网格刷新
   */
  update({ units = [], enemies = null, nowSec = 0, force = false } = {}) {
    const t = Number.isFinite(nowSec) ? nowSec : 0;
    const dtTick = this._lastTickSec === -Infinity ? this.tickIntervalSec : Math.max(0, t - this._lastTickSec);
    if (!force && dtTick < this.tickIntervalSec - 1e-4) {
      return false;
    }

    // 1) 交换双缓冲：将上次 currRGBA 拷贝至 prevRGBA 供 GPU 60fps 平滑插值
    this.prevRGBA.set(this.currRGBA);
    const pixelCount = this.width * this.height;
    const currRGBA = this.currRGBA;

    // 2) 清空本帧实时 R / 穿透 B，仅在存在活跃电战热区时衰减并同步 A 通道
    if (this._hasActiveEw) {
      const ewDecay = Math.exp(-this.ewDecayPerSec * Math.max(0.05, Math.min(1.0, dtTick)));
      const ewGrid = this._ewGrid;
      let anyEw = false;
      for (let i = 0; i < pixelCount; i += 1) {
        const off = i * 4;
        currRGBA[off] = 0;
        currRGBA[off + 2] = 0;
        const raw = ewGrid[i];
        if (raw > 0) {
          const ew = raw * ewDecay;
          if (ew < 0.008) {
            ewGrid[i] = 0;
            currRGBA[off + 3] = 0;
          } else {
            ewGrid[i] = ew;
            currRGBA[off + 3] = Math.round((ew > 1 ? 1 : ew) * 255);
            anyEw = true;
          }
        } else {
          currRGBA[off + 3] = 0;
        }
      }
      this._hasActiveEw = anyEw;
      this._ewDirtyAlpha = false;
    } else if (this._ewDirtyAlpha) {
      for (let i = 0; i < pixelCount; i += 1) {
        const off = i * 4;
        currRGBA[off] = 0;
        currRGBA[off + 2] = 0;
        currRGBA[off + 3] = 0;
      }
      this._ewDirtyAlpha = false;
    } else {
      for (let i = 0; i < pixelCount; i += 1) {
        const off = i * 4;
        currRGBA[off] = 0;
        currRGBA[off + 2] = 0;
      }
    }

    // 3) 提取全部有效己方发射源并光栅化
    const nextEmitters = [];
    const seenIds = this._seenIds;
    seenIds.clear();
    let ewAddedInTick = false;
    for (let i = 0; i < units.length; i += 1) {
      const em = extractUnitVisionEmitter(units[i], this._prevUnitPos, this.scales);
      if (!em) continue;
      nextEmitters.push(em);
      if (em.unitId) seenIds.add(em.unitId);

      if (em.shape === 'corridor') {
        this._rasterizeFixedWingCorridor(em);
      } else if (em.shape === 'orbit_pod' && em.orbitCenterX !== null && em.orbitCenterZ !== null) {
        // AC-130 盘旋光电聚焦圈 + 机下窄扫掠
        this._rasterizeCapsule(em.orbitCenterX, em.orbitCenterZ, em.orbitCenterX, em.orbitCenterZ, em.liveRadiusM, em.writesExploredG, em.penetrationTier, false);
        this._rasterizeCapsule(em.prevX, em.prevZ, em.x, em.z, Math.max(70, em.corridorHalfWidthM || 120), em.writesExploredG, em.penetrationTier, false);
      } else if (em.shape === 'ew_pulse') {
        this._rasterizeFixedWingCorridor(em);
        if (em.ewPulseRadiusM > 0) {
          this.addEwPulse(em.x, em.z, em.ewPulseRadiusM, 0.85);
          ewAddedInTick = true;
        }
      } else {
        // UAV / 直升机 / 地面圆形视野
        const cx = em.orbitCenterX !== null ? em.orbitCenterX : em.x;
        const cz = em.orbitCenterZ !== null ? em.orbitCenterZ : em.z;
        const ax = em.orbitCenterX !== null ? em.orbitCenterX : em.prevX;
        const az = em.orbitCenterZ !== null ? em.orbitCenterZ : em.prevZ;
        const useOcc = !em.ignoresGroundOcclusion && em.role === 'ground';
        const rayInfo = useOcc ? this._getOrComputeGroundRays(em) : null;
        this._rasterizeCapsule(ax, az, cx, cz, em.liveRadiusM, em.writesExploredG, em.penetrationTier, useOcc, rayInfo);
      }
    }

    // 清理已离场单位的 prevPos 与射线复用缓存
    for (const key of this._prevUnitPos.keys()) {
      if (!seenIds.has(key)) this._prevUnitPos.delete(key);
    }
    for (const key of this._unitRayCache.keys()) {
      if (!seenIds.has(key)) this._unitRayCache.delete(key);
    }
    this._emitters = nextEmitters;

    // 4) 回写调试全图透视 / 从透视恢复探索 G / 本帧新增电战热区 A（稳态零整图二扫）
    if (this.revealAllDebug || !this.enabled) {
      const ewGrid = this._ewGrid;
      for (let i = 0; i < pixelCount; i += 1) {
        const off = i * 4;
        currRGBA[off] = 255;
        currRGBA[off + 1] = 255;
        currRGBA[off + 2] = 255;
        currRGBA[off + 3] = this._hasActiveEw ? Math.round(clamp(ewGrid[i], 0, 1) * 255) : 0;
      }
      this._wasFullReveal = true;
      this._ewDirtyAlpha = false;
    } else {
      if (this._wasFullReveal) {
        const explored = this._exploredGrid;
        for (let i = 0; i < pixelCount; i += 1) {
          currRGBA[i * 4 + 1] = explored[i];
        }
        this._wasFullReveal = false;
      }
      if (ewAddedInTick || this._ewDirtyAlpha) {
        const ewGrid = this._ewGrid;
        for (let i = 0; i < pixelCount; i += 1) {
          const ew = ewGrid[i];
          currRGBA[i * 4 + 3] = ew > 0 ? Math.round((ew > 1 ? 1 : ew) * 255) : 0;
        }
        this._ewDirtyAlpha = false;
      }
    }

    // 5) 若首帧刷新则让 prevRGBA 与 currRGBA 同步，避免开局从全黑突跳
    if (this._lastTickSec === -Infinity) {
      this.prevRGBA.set(this.currRGBA);
    }

    this._lastTickSec = t;
    this.updatedAtSec = t;
    this.gridVersion += 1;

    if (Array.isArray(enemies)) {
      this.syncContacts(enemies, t);
    }
    return true;
  }

  /**
   * 权威双线性查询任意世界坐标 (wx, wz) 的实时可见性、探索状态与穿透档位。
   * 供固定翼锁定武器（AGM-65 等）、敌情显隐与规则单测调用。
   */
  queryPoint(wx, wz, opts = {}) {
    const requireHighPenetration = Boolean(opts?.inCover || opts?.requireHighPenetration);
    if (!this.enabled || this.revealAllDebug) {
      return {
        visible: true,
        explored: true,
        liveAlpha: 1,
        exploredAlpha: 1,
        penetrationTier: 255,
        ewPulse: 0,
      };
    }

    const { fx, fz } = worldToFogCell(wx, wz, this.width, this.height, this.bounds);
    const x0 = clamp(Math.floor(fx), 0, this.width - 1);
    const z0 = clamp(Math.floor(fz), 0, this.height - 1);
    const x1 = clamp(x0 + 1, 0, this.width - 1);
    const z1 = clamp(z0 + 1, 0, this.height - 1);
    const tx = fx - x0;
    const tz = fz - z0;

    const i00 = (z0 * this.width + x0) * 4;
    const i10 = (z0 * this.width + x1) * 4;
    const i01 = (z1 * this.width + x0) * 4;
    const i11 = (z1 * this.width + x1) * 4;
    const d = this.currRGBA;

    const bilerp = (ch) => {
      const top = d[i00 + ch] * (1 - tx) + d[i10 + ch] * tx;
      const bot = d[i01 + ch] * (1 - tx) + d[i11 + ch] * tx;
      return (top * (1 - tz) + bot * tz) / 255;
    };

    const liveAlpha = bilerp(0);
    const exploredAlpha = bilerp(1);
    const penMax = Math.max(d[i00 + 2], d[i10 + 2], d[i01 + 2], d[i11 + 2]);
    const ewPulse = bilerp(3);

    // 若目标躲在密林/建筑掩体内，仅低空 UAV 或地面/直升机强穿透 (penetration >= 200) 可识别
    const visible = requireHighPenetration
      ? (liveAlpha >= 0.28 && penMax >= 200)
      : (liveAlpha >= 0.22);

    return {
      visible,
      explored: exploredAlpha >= 0.15,
      liveAlpha,
      exploredAlpha,
      penetrationTier: penMax,
      ewPulse,
    };
  }

  /**
   * 同步敌方目标实时显隐与失联幽灵记忆（敌人离开视野后留在最后发现坐标，不追踪隐藏移动）。
   */
  syncContacts(enemies = [], nowSec = this.updatedAtSec) {
    const t = Number.isFinite(nowSec) ? nowSec : this.updatedAtSec;
    const aliveIds = this._aliveEnemyIds;
    aliveIds.clear();

    for (let i = 0; i < enemies.length; i += 1) {
      const e = enemies[i];
      if (!e || e.dead || e.destroyed || (Number.isFinite(e.hp) && e.hp <= 0)) continue;
      const id = String(e.id || `enemy_${i}`);
      aliveIds.add(id);

      const pos = e.node?.position || e.position || e;
      const ex = Number(pos?.x);
      const ez = Number(pos?.z);
      if (!Number.isFinite(ex) || !Number.isFinite(ez)) continue;

      const q = this.queryPoint(ex, ez, { inCover: Boolean(e.inCover) });
      const prevVis = Boolean(e._fogVisible);
      e._fogVisible = q.visible;
      e.fogVisible = q.visible;
      if (e.node) {
        e.node.visible = q.visible;
      }

      if (q.visible) {
        // 实时可见：记录最后位置并移除失联幽灵
        e._fogLastSeen = { x: ex, z: ez, sec: t };
        this._ghostMap.delete(id);
      } else if (prevVis && e._fogLastSeen) {
        // 刚离开实时视野：冻结在最后发现坐标，不追踪后续移动
        this._ghostMap.set(id, {
          id,
          kind: e.kind || 'zombie_group',
          x: e._fogLastSeen.x,
          z: e._fogLastSeen.z,
          lastSeenSec: e._fogLastSeen.sec,
          ageSec: Math.max(0, t - e._fogLastSeen.sec),
          radiusM: Number(e.radiusM) || 18,
          countEstimate: Number(e.count) || Number(e.countEstimate) || 1,
        });
      }
    }

    // 刷新已有幽灵的年龄；若被消灭、超时、或该幽灵所在坐标再次被我方视野证实为空则清除
    const nextGhosts = [];
    for (const [id, ghost] of this._ghostMap.entries()) {
      if (!aliveIds.has(id)) {
        this._ghostMap.delete(id);
        continue;
      }
      const age = Math.max(0, t - ghost.lastSeenSec);
      const checkLastSpot = this.queryPoint(ghost.x, ghost.z);
      if (age > this.ghostExpireSec || checkLastSpot.visible) {
        // 超过记忆时限，或我方已重新看到最后发现点且敌人已不在那里 → 消除过期幽灵
        this._ghostMap.delete(id);
        continue;
      }
      ghost.ageSec = age;
      nextGhosts.push(ghost);
    }
    this._ghostList = nextGhosts;
    return this._ghostList;
  }

  /**
   * 导出零拷贝只读快照供 SCAV-9（3D FogOfWarPass / 小地图）与 2D 标绘层统一消费。
   */
  getSnapshot(nowSec = this.updatedAtSec) {
    const t = Number.isFinite(nowSec) ? nowSec : this.updatedAtSec;
    const blendT = this.tickIntervalSec > 1e-4
      ? clamp((t - this.updatedAtSec) / this.tickIntervalSec, 0, 1)
      : 1;
    return {
      specVersion: FOG_CONTRACT_VERSION,
      width: this.width,
      height: this.height,
      bounds: this.bounds,
      gridVersion: this.gridVersion,
      updatedAtSec: this.updatedAtSec,
      tickIntervalSec: this.tickIntervalSec,
      blendT,
      enabled: this.enabled && !this.revealAllDebug,
      prevRGBA: this.prevRGBA,
      currRGBA: this.currRGBA,
      emitters: this._emitters,
      ghostContacts: this._ghostList,
    };
  }
}
