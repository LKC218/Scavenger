/**
 * Scavenger 9km 实战 1:1 战术小地图引擎：
 * - 支持 9KM ↔ 1KM 无级滚轮指哪放哪缩放（144Hz 指数阻尼平滑过渡）与顶栏/微控按钮；
 * - 1024×1024 离屏真实地形烘焙 + <=3600m 局部空间分桶原生 DPR 高清矢量建筑/路网 LOD 重绘；
 * - 真实世界坐标绑定三级动态 MGRS 网格（1500m / 500m / 250m）、动态军用比例尺条与同步旋转视野框；
 * - 与方案 B 底部卡片指挥台同型聚合纯规则。
 * 本模块无 Three.js 顶层依赖，纯函数部分可在 Node ESM 规则单测中直接导入校验。
 */

export const DEFAULT_MINIMAP_BOUNDS = Object.freeze({
  minX: -4425,
  maxX: 4575,
  minZ: -4150,
  maxZ: 4850,
});

export const MINIMAP_MIN_RANGE_M = 1000;
export const MINIMAP_MAX_RANGE_M = 9000;
export const MINIMAP_VECTOR_LOD_THRESHOLD_M = 3600;

const TILT_SIN = Math.sin((48 * Math.PI) / 180);

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

/**
 * 9KM 宏观 vs 3KM 局部放大建筑分级筛选纯规则（消除 9KM 3.7 万细白点雪花噪点）。
 */
export function classifyBuildingRadarTier(rawW, rawD, rawH) {
  const maxSpan = Math.max(Number(rawW) || 0, Number(rawD) || 0);
  const h = Number(rawH) || 0;
  const tier3km = maxSpan >= 5.0 && h >= 2.2;
  const tier9km = maxSpan >= 14.0 && h >= 3.8;
  return { tier9km, tier3km };
}

/**
 * 军图地貌分色提纯纯规则：
 * - 林地/草地：沉稳军图深青绿（高可辨森林与绿地分层）
 * - 停机坪/水泥硬化地：清晰中灰
 * - 农田/裸土/沙地：低饱和暗橄榄褐
 */
export function stylizeTacticalTerrainRgb(r0, g0, b0) {
  const luma = r0 * 0.299 + g0 * 0.587 + b0 * 0.114;
  const maxC = Math.max(r0, g0, b0);
  const minC = Math.min(r0, g0, b0);
  const chroma = maxC - minC;

  // 1) 停机坪 / 水泥硬化带（低彩度、偏亮中性灰）
  if (chroma <= 18 && luma >= 116) {
    const base = clamp(Math.round(luma * 0.56 + 18), 72, 134);
    return [base - 3, base + 1, base + 7];
  }

  // 2) 林地 / 绿地 / 植被（绿色主导）
  if (g0 >= r0 + 4 && g0 >= b0 + 2) {
    const greenStrength = clamp((g0 - Math.max(r0, b0)) / 58, 0, 1);
    const shade = clamp((luma - 28) / 170, 0, 1);
    const r = Math.round(22 + shade * 24 - greenStrength * 6);
    const g = Math.round(44 + shade * 42 + greenStrength * 14);
    const b = Math.round(37 + shade * 28 + greenStrength * 6);
    return [clamp(r, 14, 72), clamp(g, 34, 108), clamp(b, 28, 84)];
  }

  // 3) 农田 / 裸土 / 滩涂（偏暖黄褐低饱和军图暗橄榄褐）
  const warmShift = clamp((r0 - b0) / 72, 0, 1);
  const shade = clamp((luma - 22) / 185, 0, 1);
  const r = Math.round(38 + shade * 38 + warmShift * 12);
  const g = Math.round(42 + shade * 36 + warmShift * 4);
  const b = Math.round(33 + shade * 24 - warmShift * 4);
  return [clamp(r, 24, 96), clamp(g, 28, 92), clamp(b, 22, 72)];
}

/**
 * 纯数学 4×4 列主序矩阵乘法：将道路 Mesh 顶点转换至世界 (wx, wz) 并映射为离屏像素三角形。
 * 无 Three.js 依赖，供浏览器烘焙与 Node 规则测试共用。
 */
export function extractRoadTriangles2D(posArray, indexArray, matrixElements, bounds = DEFAULT_MINIMAP_BOUNDS, canvasSize = 512) {
  if (!posArray || posArray.length < 9) return [];
  const m = matrixElements && matrixElements.length >= 16
    ? matrixElements
    : [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const b = bounds || DEFAULT_MINIMAP_BOUNDS;
  const spanX = Math.max(1, (b.maxX ?? 4575) - (b.minX ?? -4425));
  const spanZ = Math.max(1, (b.maxZ ?? 4850) - (b.minZ ?? -4150));
  const mapSpan = Math.max(spanX, spanZ);
  const scalePx = canvasSize / mapSpan;

  const pad = 40;
  const minX = b.minX - pad;
  const maxX = b.maxX + pad;
  const minZ = b.minZ - pad;
  const maxZ = b.maxZ + pad;

  const tx = (vi) => {
    const o = vi * 3;
    const x = posArray[o];
    const y = posArray[o + 1];
    const z = posArray[o + 2];
    const wx = m[0] * x + m[4] * y + m[8] * z + m[12];
    const wz = m[2] * x + m[6] * y + m[10] * z + m[14];
    return [wx, wz];
  };

  const triCount = indexArray ? Math.floor(indexArray.length / 3) : Math.floor(posArray.length / 9);
  const out = [];
  for (let t = 0; t < triCount; t++) {
    const i0 = indexArray ? indexArray[t * 3] : t * 3;
    const i1 = indexArray ? indexArray[t * 3 + 1] : t * 3 + 1;
    const i2 = indexArray ? indexArray[t * 3 + 2] : t * 3 + 2;
    const [x0, z0] = tx(i0);
    const [x1, z1] = tx(i1);
    const [x2, z2] = tx(i2);
    // 跳过完全在 9km 边界之外的悬空道路三角形
    if (
      (x0 < minX && x1 < minX && x2 < minX) ||
      (x0 > maxX && x1 > maxX && x2 > maxX) ||
      (z0 < minZ && z1 < minZ && z2 < minZ) ||
      (z0 > maxZ && z1 > maxZ && z2 > maxZ)
    ) {
      continue;
    }
    out.push(
      (x0 - b.minX) * scalePx,
      (z0 - b.minZ) * scalePx,
      (x1 - b.minX) * scalePx,
      (z1 - b.minZ) * scalePx,
      (x2 - b.minX) * scalePx,
      (z2 - b.minZ) * scalePx,
    );
  }
  return out;
}

/**
 * 纯 TypedArray 高速光栅化道路/跑道三角形至 Uint8Array 掩码（避免 Skia Canvas2D 巨型路径 stroke 卡顿）。
 * 返回被点亮的掩码像素总数。
 */
export function rasterizeRoadTrianglesToMask(tris, mask, S, value = 2) {
  if (!tris || !tris.length || !mask || S <= 0) return 0;
  let lit = 0;

  const mark = (px, py) => {
    if (px < 0 || px >= S || py < 0 || py >= S) return;
    const idx = py * S + px;
    if (mask[idx] < value) {
      mask[idx] = value;
      lit += 1;
    }
  };

  const drawEdge = (ax, ay, bx, by) => {
    const dx = bx - ax;
    const dy = by - ay;
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy))));
    if (steps > S * 2) return;
    const inv = 1 / steps;
    for (let s = 0; s <= steps; s++) {
      mark(Math.round(ax + dx * s * inv), Math.round(ay + dy * s * inv));
    }
  };

  for (let i = 0; i < tris.length; i += 6) {
    const x0 = tris[i];
    const y0 = tris[i + 1];
    const x1 = tris[i + 2];
    const y1 = tris[i + 3];
    const x2 = tris[i + 4];
    const y2 = tris[i + 5];

    // 始终描 3 条边确保细长 6m 公路在 9km 全图下不断裂
    drawEdge(x0, y0, x1, y1);
    drawEdge(x1, y1, x2, y2);
    drawEdge(x2, y2, x0, y0);

    const minX = Math.max(0, Math.floor(Math.min(x0, x1, x2)));
    const maxX = Math.min(S - 1, Math.ceil(Math.max(x0, x1, x2)));
    const minY = Math.max(0, Math.floor(Math.min(y0, y1, y2)));
    const maxY = Math.min(S - 1, Math.ceil(Math.max(y0, y1, y2)));
    if (maxX - minX < 1 || maxY - minY < 1) continue;

    const area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0);
    if (Math.abs(area) < 0.25) continue;
    const sign = area > 0 ? 1 : -1;

    for (let py = minY; py <= maxY; py++) {
      const cy = py + 0.5;
      const row = py * S;
      for (let px = minX; px <= maxX; px++) {
        const cx = px + 0.5;
        const w0 = ((x1 - x0) * (cy - y0) - (y1 - y0) * (cx - x0)) * sign;
        if (w0 < -0.001) continue;
        const w1 = ((x2 - x1) * (cy - y1) - (y2 - y1) * (cx - x1)) * sign;
        if (w1 < -0.001) continue;
        const w2 = ((x0 - x2) * (cy - y2) - (y0 - y2) * (cx - x2)) * sign;
        if (w2 < -0.001) continue;
        const idx = row + px;
        if (mask[idx] < value) {
          mask[idx] = value;
          lit += 1;
        }
      }
    }
  }
  return lit;
}

/**
 * 计算 9KM 全域、3KM 或任意连续倍率（1000m ~ 9000m）下的 1:1 战术雷达视窗世界范围。
 */
export function computeMinimapViewport(zoomInput, centerX = 0, centerZ = 0, bounds = DEFAULT_MINIMAP_BOUNDS) {
  const b = bounds || DEFAULT_MINIMAP_BOUNDS;
  const spanX = Math.max(1, (b.maxX ?? 4575) - (b.minX ?? -4425));
  const spanZ = Math.max(1, (b.maxZ ?? 4850) - (b.minZ ?? -4150));
  const mapSpan = Math.max(spanX, spanZ);

  let reqRange;
  if (typeof zoomInput === 'number' && Number.isFinite(zoomInput)) {
    reqRange = clamp(zoomInput, Math.min(MINIMAP_MIN_RANGE_M, mapSpan), mapSpan);
  } else if (zoomInput === '3km') {
    reqRange = Math.min(3000, mapSpan);
  } else {
    reqRange = mapSpan;
  }

  const maxMinX = Math.max(b.minX, b.maxX - reqRange);
  const maxMinZ = Math.max(b.minZ, b.maxZ - reqRange);
  const cx = Number.isFinite(centerX) ? centerX : 0;
  const cz = Number.isFinite(centerZ) ? centerZ : 0;
  const minX = clamp(cx - reqRange * 0.5, b.minX, maxMinX);
  const minZ = clamp(cz - reqRange * 0.5, b.minZ, maxMinZ);

  const zoomMode = reqRange >= mapSpan - 50
    ? '9km'
    : Math.abs(reqRange - Math.min(3000, mapSpan)) <= 60
      ? '3km'
      : 'custom';

  return {
    zoomMode,
    viewRangeM: reqRange,
    minX,
    minZ,
    maxX: minX + reqRange,
    maxZ: minZ + reqRange,
    centerX: minX + reqRange * 0.5,
    centerZ: minZ + reqRange * 0.5,
    mapMinX: b.minX,
    mapMinZ: b.minZ,
    mapSpan,
  };
}

/**
 * 以画布内归一化落点 (anchorNormX, anchorNormY) 为锚点进行缩放，保证指哪放哪，不漂移鼠标下方世界点。
 */
export function computeAnchoredZoomViewport(
  curVp,
  nextRangeM,
  anchorNormX = 0.5,
  anchorNormY = 0.5,
  bounds = DEFAULT_MINIMAP_BOUNDS,
) {
  const b = bounds || DEFAULT_MINIMAP_BOUNDS;
  const spanX = Math.max(1, (b.maxX ?? 4575) - (b.minX ?? -4425));
  const spanZ = Math.max(1, (b.maxZ ?? 4850) - (b.minZ ?? -4150));
  const mapSpan = Math.max(spanX, spanZ);

  const vp = curVp || computeMinimapViewport(mapSpan, 0, 0, b);
  const nx = clamp(Number.isFinite(anchorNormX) ? anchorNormX : 0.5, 0, 1);
  const ny = clamp(Number.isFinite(anchorNormY) ? anchorNormY : 0.5, 0, 1);
  const curRange = Math.max(1, vp.viewRangeM || mapSpan);

  const worldX = vp.minX + nx * curRange;
  const worldZ = vp.minZ + ny * curRange;

  const clampedRange = clamp(
    Number.isFinite(nextRangeM) ? nextRangeM : curRange,
    Math.min(MINIMAP_MIN_RANGE_M, mapSpan),
    mapSpan,
  );
  const maxMinX = Math.max(b.minX, b.maxX - clampedRange);
  const maxMinZ = Math.max(b.minZ, b.maxZ - clampedRange);
  const newMinX = clamp(worldX - nx * clampedRange, b.minX, maxMinX);
  const newMinZ = clamp(worldZ - ny * clampedRange, b.minZ, maxMinZ);

  return computeMinimapViewport(
    clampedRange,
    newMinX + clampedRange * 0.5,
    newMinZ + clampedRange * 0.5,
    b,
  );
}

const SCALE_BAR_STEPS = [100, 200, 250, 500, 1000, 1500, 2000, 3000];

/**
 * 按当前视野跨度与画布像素宽挑选最整洁的军用比例尺刻度（如 2KM / 1KM / 500M / 200M）。
 */
export function computeMinimapScaleBar(viewRangeM, canvasWidthPx) {
  const range = Math.max(100, Number(viewRangeM) || 9000);
  const wPx = Math.max(60, Number(canvasWidthPx) || 248);
  const targetM = range * 0.26;

  let bestM = SCALE_BAR_STEPS[0];
  let bestDiff = Math.abs(bestM - targetM);
  for (let i = 1; i < SCALE_BAR_STEPS.length; i++) {
    const step = SCALE_BAR_STEPS[i];
    if (step > range * 0.52) break;
    const diff = Math.abs(step - targetM);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestM = step;
    }
  }

  const widthPx = Math.max(18, Math.round((bestM / range) * wPx));
  const label = bestM >= 1000
    ? (bestM % 1000 === 0 ? `${bestM / 1000}KM` : `${(bestM / 1000).toFixed(1)}KM`)
    : `${bestM}M`;

  return { meters: bestM, widthPx, label };
}

/**
 * 计算与真实世界坐标绑定的动态三级 MGRS 网格（9km->1500m / 3km->500m / 1km->250m）。
 */
export function computeDynamicMgrsGrid(vp) {
  const v = vp || computeMinimapViewport(9000, 0, 0);
  const range = Math.max(1, v.viewRangeM || 9000);
  const mapMinX = Number.isFinite(v.mapMinX) ? v.mapMinX : DEFAULT_MINIMAP_BOUNDS.minX;
  const mapMinZ = Number.isFinite(v.mapMinZ) ? v.mapMinZ : DEFAULT_MINIMAP_BOUNDS.minZ;
  const majorStep = 1500;
  const stepM = range > 4800 ? 1500 : (range > 2100 ? 500 : 250);
  const letters = ['A', 'B', 'C', 'D', 'E', 'F'];

  const xLines = [];
  const startKx = Math.ceil((v.minX - mapMinX - 1e-4) / stepM);
  const endKx = Math.floor((v.maxX - mapMinX + 1e-4) / stepM);
  for (let k = startKx; k <= endKx; k++) {
    const worldX = mapMinX + k * stepM;
    const norm = (worldX - v.minX) / range;
    const offsetM = Math.round(k * stepM);
    const isMajor = offsetM % majorStep === 0;
    const colIdx = Math.floor((offsetM + 1) / majorStep);
    const letter = letters[colIdx] || '';
    const subCol = Math.floor((offsetM - colIdx * majorStep + 1) / stepM);
    const label = isMajor
      ? (colIdx >= 0 && colIdx < letters.length ? `${letter}` : '')
      : (stepM <= 500 && letter ? `${letter}.${subCol}` : '');
    xLines.push({ world: worldX, norm, isMajor, colIdx, label });
  }

  const zLines = [];
  const startKz = Math.ceil((v.minZ - mapMinZ - 1e-4) / stepM);
  const endKz = Math.floor((v.maxZ - mapMinZ + 1e-4) / stepM);
  for (let k = startKz; k <= endKz; k++) {
    const worldZ = mapMinZ + k * stepM;
    const norm = (worldZ - v.minZ) / range;
    const offsetM = Math.round(k * stepM);
    const isMajor = offsetM % majorStep === 0;
    const rowIdx = Math.floor((offsetM + 1) / majorStep);
    const label = isMajor && rowIdx >= 0 && rowIdx < 6 ? String(rowIdx + 1) : '';
    zLines.push({ world: worldZ, norm, isMajor, rowIdx, label });
  }

  return { stepM, majorStep, xLines, zLines };
}

/**
 * 将世界坐标 (wx, wz) 映射至 1:1 雷达画布坐标 (x, y)。
 */
export function worldToMinimapPx(wx, wz, viewport, width, height) {
  const range = Math.max(1, viewport?.viewRangeM || 9000);
  return {
    x: ((wx - viewport.minX) / range) * width,
    y: ((wz - viewport.minZ) / range) * height,
  };
}

/**
 * 将雷达画布坐标 (px, py) 逆映射至世界坐标 (x, z)。
 */
export function minimapPxToWorld(px, py, viewport, width, height) {
  const nx = clamp(px / Math.max(1, width), 0, 1);
  const ny = clamp(py / Math.max(1, height), 0, 1);
  const range = Math.max(1, viewport?.viewRangeM || 9000);
  return {
    x: viewport.minX + nx * range,
    z: viewport.minZ + ny * range,
  };
}

/**
 * 方案 B 底部卡片指挥台通用筛选纯函数：支持全军 / 地面 / 旋翼 / 固定翼及 1~6 编组筛选。
 */
export function filterDeckUnits(units = [], { filterTab = 'all', controlGroups = null } = {}) {
  const tab = String(filterTab || 'all');
  let pool = Array.isArray(units) ? units.filter(u => !u?.evacuated) : [];
  if (tab === 'ground') {
    pool = pool.filter(u => u.role === 'ground');
  } else if (tab === 'heli') {
    pool = pool.filter(u => u.role === 'helicopter' || u.role === 'heli');
  } else if (tab === 'plane') {
    pool = pool.filter(u => u.role === 'fixed_wing' || u.role === 'plane');
  } else if (tab.startsWith('cg')) {
    const slotKey = tab.slice(2);
    let slotIds = [];
    if (controlGroups instanceof Map) {
      slotIds = controlGroups.get(slotKey) || controlGroups.get(Number(slotKey)) || [];
    } else if (controlGroups && typeof controlGroups === 'object') {
      slotIds = controlGroups[slotKey] || controlGroups[Number(slotKey)] || [];
    }
    const idSet = new Set(slotIds);
    pool = pool.filter(u => idSet.has(u.id));
  }
  return pool;
}

/**
 * 方案 B 底部紧凑卡片指挥台：按 slug 聚合在场载具列表并支持兵种与 1~6 编组页签过滤。
 */
export function aggregateDeckUnitsBySlug(units = [], {
  filterTab = 'all',
  controlGroups = null,
  selectedIds = null,
} = {}) {
  const selSet = selectedIds instanceof Set ? selectedIds : new Set(selectedIds || []);
  const pool = filterDeckUnits(units, { filterTab, controlGroups });

  const groups = new Map();
  for (const u of pool) {
    const key = String(u.slug || u.shortName || u.id);
    if (!groups.has(key)) {
      const explicitModel = String(u.modelCode || '').trim();
      const rawShort = String(u.shortName || u.name || key).trim();
      const baseTitle = explicitModel || rawShort.replace(/\s+#\d+$/, '').trim() || rawShort;
      groups.set(key, {
        key,
        slug: u.slug || key,
        name: baseTitle,
        iconKind: u.iconKind || 'mbt',
        channel: u.channel || (u.role === 'helicopter' ? 'heli' : u.role === 'fixed_wing' ? 'plane' : 'ground'),
        role: u.role || 'ground',
        units: [],
      });
    }
    groups.get(key).units.push(u);
  }

  const result = [];
  for (const g of groups.values()) {
    let hpSum = 0;
    let selCnt = 0;
    let movingCnt = 0;
    let altSum = 0;
    for (const u of g.units) {
      const maxHp = Number.isFinite(u.maxHp) && u.maxHp > 0 ? u.maxHp : 100;
      const curHp = Number.isFinite(u.hp) ? u.hp : maxHp;
      hpSum += Math.max(0, Math.min(100, (curHp / maxHp) * 100));
      if (selSet.has(u.id)) selCnt += 1;
      const remWp = Array.isArray(u.waypoints) ? Math.max(0, u.waypoints.length - (u.wpIndex || 0)) : 0;
      if (remWp > 0) movingCnt += 1;
      const py = u.node?.position?.y ?? u.y ?? 0;
      altSum += Number.isFinite(py) ? py : 0;
    }
    const count = g.units.length;
    result.push({
      ...g,
      count,
      selectedCount: selCnt,
      anySelected: selCnt > 0,
      allSelected: count > 0 && selCnt === count,
      avgHp: Math.round(hpSum / Math.max(1, count)),
      altitudeM: Math.round(altSum / Math.max(1, count)),
      movingCount: movingCnt,
    });
  }
  return result;
}

function channelColorHex(channel) {
  if (channel === 'heli') return '#68c8ff';
  if (channel === 'plane') return '#b197fc';
  return '#38e5c0';
}

function createOffscreenCanvas(size) {
  const cv = document.createElement('canvas');
  cv.width = size;
  cv.height = size;
  return cv;
}

/**
 * 1:1 真实 9km 离屏烘焙 + 无级阻尼缩放 + R1 局部矢量高清 LOD + 144Hz 同步旋转雷达引擎。
 */
export class TacticalMinimapEngine {
  constructor({ bounds = DEFAULT_MINIMAP_BOUNDS, offscreenSize = 1024 } = {}) {
    this.bounds = { ...DEFAULT_MINIMAP_BOUNDS, ...(bounds || {}) };
    this.offscreenSize = Math.max(256, Math.min(1536, offscreenSize));
    const spanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
    const spanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);
    this.mapSpan = Math.max(spanX, spanZ);

    this.zoomMode = '9km';
    this.targetRangeM = this.mapSpan;
    this.currentRangeM = this.mapSpan;
    this.targetCenterX = (this.bounds.minX + this.bounds.maxX) * 0.5;
    this.targetCenterZ = (this.bounds.minZ + this.bounds.maxZ) * 0.5;
    this.currentCenterX = this.targetCenterX;
    this.currentCenterZ = this.targetCenterZ;
    this.expanded = false;

    this._manualAnchorActive = false;
    this._anchorCamX = null;
    this._anchorCamZ = null;
    this._lastRenderMs = 0;

    this.layers = {
      terrain: true,
      grid: true,
      routes: true,
      fog: true,
    };

    this.terrainCanvas = null;
    this.bakedCanvas9km = null;
    this.bakedCanvas3km = null;
    this.bakedCanvas = null; // 兼容指向 9KM 主缓存
    this.vectorCacheCanvas = null;
    this._fogCanvas = null;
    this._fogCtx = null;
    this._fogImgData = null;
    this._lastFogGridVersion = -1;
    this._lastFogW = 0;
    this._lastFogH = 0;
    this._vectorCacheKey = '';
    this._buildingVersion = 0;
    this._lastVectorBuildMs = 0;
    this._lastVectorBuildRange = 0;
    this._lastVectorRoadsDrawn = 0;
    this._lastVectorBuildingsDrawn = 0;

    this._bucketN = 16;
    this._buildingBuckets = [];
    this._dynamicBuildings = [];
    this._roadBucketN = 8;
    this._roadBuckets = [];
    this._roadStore = null;
    this._roadDrawStamp = 0;

    this.lastViewport = computeMinimapViewport(this.zoomMode, 0, 0, this.bounds);
    this._lastTextSyncMs = 0;
    this._hzAccFrames = 0;
    this._hzAccStartMs = 0;

    this.stats = {
      baseReady: false,
      usedMacroTintImage: false,
      roadsBaked: false,
      roadMeshesBaked: 0,
      fogActive: false,
      fogGridVersion: 0,
      fogEmitterCount: 0,
      fogGhostCount: 0,
      roadTrianglesBaked: 0,
      roleRoadCellsBaked: 0,
      staticBuildings9kmCount: 0,
      staticBuildings3kmCount: 0,
      staticBuildingsDrawn: 0,
      dynamicBuildingsDrawn: 0,
      vectorLodActive: false,
      vectorBuildingsDrawn: 0,
      vectorRoadsDrawn: 0,
      frameCount: 0,
      liveHz: 60,
    };
  }

  _syncCompatZoomMode() {
    if (this.targetRangeM >= this.mapSpan - 50) {
      this.zoomMode = '9km';
    } else if (Math.abs(this.targetRangeM - Math.min(3000, this.mapSpan)) <= 60) {
      this.zoomMode = '3km';
    } else {
      this.zoomMode = 'custom';
    }
    return this.zoomMode;
  }

  setExpanded(flag) {
    this.expanded = Boolean(flag);
    return this.expanded;
  }

  setTargetRange(rangeM, { centerX = null, centerZ = null, immediate = false, manualAnchor = false, state = null } = {}) {
    const minR = Math.min(MINIMAP_MIN_RANGE_M, this.mapSpan);
    const nextRange = clamp(Number.isFinite(rangeM) ? rangeM : this.mapSpan, minR, this.mapSpan);
    this.targetRangeM = nextRange;

    if (Number.isFinite(centerX) && Number.isFinite(centerZ)) {
      this.targetCenterX = centerX;
      this.targetCenterZ = centerZ;
    } else if (state?.center && !this._manualAnchorActive) {
      this.targetCenterX = state.center.x;
      this.targetCenterZ = state.center.z;
    }

    if (nextRange >= this.mapSpan - 60) {
      this._manualAnchorActive = false;
    } else if (manualAnchor) {
      this._manualAnchorActive = true;
      this._anchorCamX = state?.center?.x ?? this._anchorCamX ?? 0;
      this._anchorCamZ = state?.center?.z ?? this._anchorCamZ ?? 0;
    }

    if (immediate) {
      this.snapAnimation();
    }
    this._syncCompatZoomMode();
    return this.targetRangeM;
  }

  snapAnimation() {
    this.currentRangeM = this.targetRangeM;
    this.currentCenterX = this.targetCenterX;
    this.currentCenterZ = this.targetCenterZ;
    this._lastVectorBuildMs = 0;
    this.lastViewport = computeMinimapViewport(
      this.currentRangeM,
      this.currentCenterX,
      this.currentCenterZ,
      this.bounds,
    );
  }

  setZoomMode(mode, { state = null, immediate = false } = {}) {
    const targetR = mode === '3km' ? Math.min(3000, this.mapSpan) : this.mapSpan;
    this._manualAnchorActive = false;
    this.setTargetRange(targetR, {
      centerX: state?.center?.x,
      centerZ: state?.center?.z,
      immediate,
      state,
    });
    return this.zoomMode;
  }

  toggleZoomMode(state = null) {
    const go3km = this.targetRangeM >= this.mapSpan - 80;
    this._manualAnchorActive = false;
    this.setTargetRange(go3km ? Math.min(3000, this.mapSpan) : this.mapSpan, {
      centerX: state?.center?.x,
      centerZ: state?.center?.z,
      state,
    });
    return this.zoomMode;
  }

  stepZoom(direction, { state = null } = {}) {
    const factor = 1.55;
    const nextR = direction > 0
      ? this.targetRangeM / factor
      : this.targetRangeM * factor;
    this.setTargetRange(nextR, {
      centerX: this._manualAnchorActive ? this.targetCenterX : (state?.center?.x ?? this.targetCenterX),
      centerZ: this._manualAnchorActive ? this.targetCenterZ : (state?.center?.z ?? this.targetCenterZ),
      manualAnchor: this._manualAnchorActive,
      state,
    });
    return this.targetRangeM;
  }

  /**
   * 在归一化坐标 (normX, normY) 处响应鼠标滚轮无级指哪放哪缩放。
   */
  zoomAtNormalizedPoint(deltaY, normX = 0.5, normY = 0.5, state = null) {
    const factor = Math.exp(clamp(Number(deltaY) || 0, -240, 240) * 0.0022);
    const minR = Math.min(MINIMAP_MIN_RANGE_M, this.mapSpan);
    const nextRange = clamp(this.targetRangeM * factor, minR, this.mapSpan);

    const refVp = this.lastViewport || computeMinimapViewport(
      this.currentRangeM,
      this.currentCenterX,
      this.currentCenterZ,
      this.bounds,
    );
    const anchoredVp = computeAnchoredZoomViewport(refVp, nextRange, normX, normY, this.bounds);
    const isFull = nextRange >= this.mapSpan - 60;
    this.setTargetRange(nextRange, {
      centerX: anchoredVp.centerX,
      centerZ: anchoredVp.centerZ,
      manualAnchor: !isFull,
      state,
    });
    this._lastTextSyncMs = 0;
    return this.targetRangeM;
  }

  toggleLayer(layerName) {
    if (layerName === 'routes' || layerName === 'grid' || layerName === 'terrain') {
      this.layers[layerName] = !this.layers[layerName];
      return this.layers[layerName];
    }
    return false;
  }

  getState() {
    return {
      zoomMode: this.zoomMode,
      targetRangeM: Math.round(this.targetRangeM),
      currentRangeM: Math.round(this.currentRangeM),
      zoomRatio: Number((this.mapSpan / Math.max(1, this.currentRangeM)).toFixed(2)),
      expanded: this.expanded,
      layers: { ...this.layers },
      stats: { ...this.stats },
    };
  }

  /**
   * 将真实 9km 宏观底色军图分色提纯、水域岸线、12m 等高线与真实公路/跑道骨架一次性烘焙至离屏缓存。
   */
  bakeBaseLayer({ macroTint = null, support = null, bounds = null, roadsScene = null, splatCtrl = null } = {}) {
    if (bounds) this.bounds = { ...this.bounds, ...bounds };
    const S = this.offscreenSize;
    if (!this.terrainCanvas) this.terrainCanvas = createOffscreenCanvas(S);
    if (!this.bakedCanvas9km) this.bakedCanvas9km = createOffscreenCanvas(S);
    if (!this.bakedCanvas3km) this.bakedCanvas3km = createOffscreenCanvas(S);
    this.bakedCanvas = this.bakedCanvas9km;

    const ctx = this.terrainCanvas.getContext('2d');
    const outImg = ctx.createImageData(S, S);
    const dst = outImg.data;
    const roadMask = new Uint8Array(S * S);

    const rawData = macroTint?.data || macroTint?.texture?.image?.data || null;
    const rawW = Number(macroTint?.width || macroTint?.texture?.image?.width || 0);
    const rawH = Number(macroTint?.height || macroTint?.texture?.image?.height || 0);
    const img = macroTint?.texture?.image || macroTint?.image || null;
    const isDomDrawable = Boolean(
      img &&
      (
        (typeof HTMLImageElement !== 'undefined' && img instanceof HTMLImageElement && img.naturalWidth > 0) ||
        (typeof HTMLCanvasElement !== 'undefined' && img instanceof HTMLCanvasElement && img.width > 0) ||
        (typeof ImageBitmap !== 'undefined' && img instanceof ImageBitmap && img.width > 0) ||
        (typeof OffscreenCanvas !== 'undefined' && img instanceof OffscreenCanvas && img.width > 0)
      )
    );
    const imgValid = Boolean((rawData && rawW > 0 && rawH > 0) || isDomDrawable);

    if (rawData && rawW > 0 && rawH > 0 && rawData.length >= rawW * rawH * 4) {
      // Three.js DataTexture (flipY=false)：row 0 对应 minZ（正北上方），与 Canvas 2D 坐标一致
      // 按军图地貌分色提纯（林地深青绿 / 农田裸土暗橄榄褐 / 停机坪清晰中灰）
      const scaleX = rawW / S;
      const scaleY = rawH / S;
      for (let py = 0; py < S; py++) {
        const sy = Math.min(rawH - 1, Math.floor((py + 0.5) * scaleY));
        const srcRow = sy * rawW;
        const dstRow = py * S;
        for (let px = 0; px < S; px++) {
          const sx = Math.min(rawW - 1, Math.floor((px + 0.5) * scaleX));
          const si = (srcRow + sx) * 4;
          const [r, g, b] = stylizeTacticalTerrainRgb(rawData[si], rawData[si + 1], rawData[si + 2]);
          const di = (dstRow + px) * 4;
          dst[di] = r;
          dst[di + 1] = g;
          dst[di + 2] = b;
          dst[di + 3] = 255;
        }
      }
      this.stats.usedMacroTintImage = true;
    } else if (isDomDrawable) {
      ctx.clearRect(0, 0, S, S);
      ctx.drawImage(img, 0, 0, S, S);
      const srcImg = ctx.getImageData(0, 0, S, S).data;
      for (let i = 0; i < S * S; i++) {
        const o = i * 4;
        const [r, g, b] = stylizeTacticalTerrainRgb(srcImg[o], srcImg[o + 1], srcImg[o + 2]);
        dst[o] = r;
        dst[o + 1] = g;
        dst[o + 2] = b;
        dst[o + 3] = 255;
      }
      this.stats.usedMacroTintImage = true;
    } else {
      for (let i = 0; i < S * S; i++) {
        const o = i * 4;
        dst[o] = 24;
        dst[o + 1] = 38;
        dst[o + 2] = 34;
        dst[o + 3] = 255;
      }
      this.stats.usedMacroTintImage = false;
    }

    // 基于真实高度支撑像素级写入：深藏蓝水域 + 浅青岸线 + 12m 等高线 + 2m 真实硬化路基掩码
    let roleRoadCells = 0;
    if (support && typeof support.heightAt === 'function') {
      const N = 192;
      const b = this.bounds;
      const spanX = Math.max(1, b.maxX - b.minX);
      const spanZ = Math.max(1, b.maxZ - b.minZ);
      const heights = new Float32Array(N * N);
      const waters = new Uint8Array(N * N);
      const roadRoles = new Uint8Array(N * N);

      for (let iz = 0; iz < N; iz++) {
        const wz = b.minZ + ((iz + 0.5) / N) * spanZ;
        for (let ix = 0; ix < N; ix++) {
          const wx = b.minX + ((ix + 0.5) / N) * spanX;
          const idx = iz * N + ix;
          let h = 0;
          let isWater = 0;
          let isRoad = 0;
          if (typeof support.query === 'function') {
            const q = support.query(wx, wz);
            h = Number.isFinite(q?.height) ? q.height : (support.heightAt(wx, wz) || 0);
            if (q?.role === 5 || (q?.waterDepth ?? 0) > 0.08) isWater = 1;
            else if (q?.role === 2 || q?.role === 3) isRoad = 1;
          } else {
            h = support.heightAt(wx, wz) || 0;
          }
          heights[idx] = h;
          waters[idx] = isWater;
          roadRoles[idx] = isRoad;
          if (isRoad) roleRoadCells += 1;
        }
      }

      const contourInterval = 12.0;
      const bandGrid = new Int16Array(N * N);
      const shoreGrid = new Uint8Array(N * N);
      for (let i = 0; i < N * N; i++) bandGrid[i] = Math.floor(heights[i] / contourInterval);
      for (let iz = 0; iz < N; iz++) {
        for (let ix = 0; ix < N; ix++) {
          const idx = iz * N + ix;
          if (!waters[idx]) continue;
          if (
            (ix > 0 && !waters[idx - 1]) ||
            (ix + 1 < N && !waters[idx + 1]) ||
            (iz > 0 && !waters[idx - N]) ||
            (iz + 1 < N && !waters[idx + N])
          ) {
            shoreGrid[idx] = 1;
          }
        }
      }

      const ratio = N / S;
      for (let py = 0; py < S; py++) {
        const iz = Math.min(N - 1, Math.floor(py * ratio));
        const gRow = iz * N;
        const pRow = py * S;
        for (let px = 0; px < S; px++) {
          const ix = Math.min(N - 1, Math.floor(px * ratio));
          const gIdx = gRow + ix;
          const pIdx = pRow + px;
          const o = pIdx * 4;

          if (!imgValid) {
            const t = clamp((heights[gIdx] + 5) / 65, 0, 1);
            dst[o] = Math.round(24 + t * 28);
            dst[o + 1] = Math.round(44 + t * 34);
            dst[o + 2] = Math.round(36 + t * 20);
          }

          if (waters[gIdx]) {
            if (shoreGrid[gIdx]) {
              dst[o] = Math.round(dst[o] * 0.26 + 98 * 0.74);
              dst[o + 1] = Math.round(dst[o + 1] * 0.26 + 196 * 0.74);
              dst[o + 2] = Math.round(dst[o + 2] * 0.26 + 228 * 0.74);
            } else {
              dst[o] = Math.round(dst[o] * 0.14 + 18 * 0.86);
              dst[o + 1] = Math.round(dst[o + 1] * 0.14 + 62 * 0.86);
              dst[o + 2] = Math.round(dst[o + 2] * 0.14 + 114 * 0.86);
            }
            continue;
          }

          if (roadRoles[gIdx] && roadMask[pIdx] < 1) {
            roadMask[pIdx] = 1;
          }

          // 12m 等高线（仅在格边界像素轻描，清晰不杂乱）
          const band = bandGrid[gIdx];
          const rightDiff = ix + 1 < N && bandGrid[gIdx + 1] !== band && Math.floor((px + 1) * ratio) !== ix;
          const downDiff = iz + 1 < N && bandGrid[gIdx + N] !== band && Math.floor((py + 1) * ratio) !== iz;
          if (rightDiff || downDiff) {
            dst[o] = clamp(Math.round(dst[o] * 0.84 + 178 * 0.16), 0, 255);
            dst[o + 1] = clamp(Math.round(dst[o + 1] * 0.84 + 216 * 0.16), 0, 255);
            dst[o + 2] = clamp(Math.round(dst[o + 2] * 0.84 + 192 * 0.16), 0, 255);
          }
        }
      }
    }

    // 真实公路网与机场跑道骨架层（roadsGlb.scene 一次性像素掩码光栅化 + 8×8 世界空间分桶供 R1 局部高清矢量重绘）
    const rbN = this._roadBucketN;
    this._roadBuckets = Array.from({ length: rbN * rbN }, () => []);
    this._roadStore = null;
    const rbSpanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
    const rbSpanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);
    const invScale = this.mapSpan / Math.max(1, S);

    let roadMeshesBaked = 0;
    let roadTrianglesBaked = 0;
    if (roadsScene && typeof roadsScene.traverse === 'function') {
      roadsScene.updateMatrixWorld?.(true);
      roadsScene.traverse(obj => {
        if (!obj || !obj.isMesh || !obj.geometry?.attributes?.position) return;
        const posArr = obj.geometry.attributes.position.array;
        const idxArr = obj.geometry.index?.array || null;
        const matElems = obj.matrixWorld?.elements || null;
        const tris = extractRoadTriangles2D(posArr, idxArr, matElems, this.bounds, S);
        if (!tris.length) return;
        const meshName = String(obj.name || '').toLowerCase();
        const val = /runway|apron|airfield|tarmac|marking|line/.test(meshName) ? 3 : 2;
        rasterizeRoadTrianglesToMask(tris, roadMask, S, val);

        // 收集精确世界坐标三角形入 8×8 空间分桶，供局部放大 <=3600m 矢量重绘
        for (let t = 0; t < tris.length; t += 6) {
          const x0 = this.bounds.minX + tris[t] * invScale;
          const z0 = this.bounds.minZ + tris[t + 1] * invScale;
          const x1 = this.bounds.minX + tris[t + 2] * invScale;
          const z1 = this.bounds.minZ + tris[t + 3] * invScale;
          const x2 = this.bounds.minX + tris[t + 4] * invScale;
          const z2 = this.bounds.minZ + tris[t + 5] * invScale;
          const tMinX = Math.min(x0, x1, x2);
          const tMaxX = Math.max(x0, x1, x2);
          const tMinZ = Math.min(z0, z1, z2);
          const tMaxZ = Math.max(z0, z1, z2);
          const rec = { x0, z0, x1, z1, x2, z2, val, minX: tMinX, maxX: tMaxX, minZ: tMinZ, maxZ: tMaxZ, _stamp: 0 };
          const bx0 = clamp(Math.floor(((tMinX - this.bounds.minX) / rbSpanX) * rbN), 0, rbN - 1);
          const bx1 = clamp(Math.floor(((tMaxX - this.bounds.minX) / rbSpanX) * rbN), 0, rbN - 1);
          const bz0 = clamp(Math.floor(((tMinZ - this.bounds.minZ) / rbSpanZ) * rbN), 0, rbN - 1);
          const bz1 = clamp(Math.floor(((tMaxZ - this.bounds.minZ) / rbSpanZ) * rbN), 0, rbN - 1);
          for (let bz = bz0; bz <= bz1; bz++) {
            for (let bx = bx0; bx <= bx1; bx++) {
              this._roadBuckets[bz * rbN + bx].push(rec);
            }
          }
        }

        roadMeshesBaked += 1;
        roadTrianglesBaked += tris.length / 6;
      });
    }

    // 将 roadMask 与 1px 暗描边轮廓统一混合进地形像素缓冲（颜色平滑均匀无叠白）
    for (let py = 0; py < S; py++) {
      const row = py * S;
      for (let px = 0; px < S; px++) {
        const idx = row + px;
        const mv = roadMask[idx];
        const o = idx * 4;
        if (mv === 3) {
          dst[o] = Math.round(dst[o] * 0.22 + 216 * 0.78);
          dst[o + 1] = Math.round(dst[o + 1] * 0.22 + 224 * 0.78);
          dst[o + 2] = Math.round(dst[o + 2] * 0.22 + 232 * 0.78);
        } else if (mv === 2) {
          dst[o] = Math.round(dst[o] * 0.28 + 198 * 0.72);
          dst[o + 1] = Math.round(dst[o + 1] * 0.28 + 206 * 0.72);
          dst[o + 2] = Math.round(dst[o + 2] * 0.28 + 214 * 0.72);
        } else if (mv === 1) {
          dst[o] = Math.round(dst[o] * 0.58 + 166 * 0.42);
          dst[o + 1] = Math.round(dst[o + 1] * 0.58 + 176 * 0.42);
          dst[o + 2] = Math.round(dst[o + 2] * 0.58 + 186 * 0.42);
        } else {
          const adjRoad =
            (px > 0 && roadMask[idx - 1] >= 2) ||
            (px + 1 < S && roadMask[idx + 1] >= 2) ||
            (py > 0 && roadMask[idx - S] >= 2) ||
            (py + 1 < S && roadMask[idx + S] >= 2);
          if (adjRoad) {
            dst[o] = Math.round(dst[o] * 0.68);
            dst[o + 1] = Math.round(dst[o + 1] * 0.68);
            dst[o + 2] = Math.round(dst[o + 2] * 0.68);
          }
        }
      }
    }

    ctx.putImageData(outImg, 0, 0);

    // 若缺少 roadsScene 与 roleRoadCells 但携带 fullSplat（1152×1152 SCVS），补充低绿意硬化带兜底
    if (splatCtrl?.grassData && splatCtrl.width > 0 && splatCtrl.height > 0 && roadTrianglesBaked === 0 && roleRoadCells === 0) {
      const sw = splatCtrl.width;
      const sh = splatCtrl.height;
      const step = 4;
      ctx.fillStyle = 'rgba(148, 158, 168, 0.16)';
      for (let iz = 0; iz < sh; iz += step) {
        const py = (iz / sh) * S;
        const row = iz * sw;
        for (let ix = 0; ix < sw; ix += step) {
          if (splatCtrl.grassData[row + ix] === 0) {
            ctx.fillRect((ix / sw) * S, py, (step / sw) * S + 0.4, (step / sh) * S + 0.4);
          }
        }
      }
    }

    this.stats.roleRoadCellsBaked = roleRoadCells;
    this.stats.roadMeshesBaked = roadMeshesBaked;
    this.stats.roadTrianglesBaked = roadTrianglesBaked;
    this.stats.roadsBaked = roadTrianglesBaked > 0 || roleRoadCells > 0;

    // 拷贝无楼含路基底至 9KM / 3KM 双缓存
    for (const targetCv of [this.bakedCanvas9km, this.bakedCanvas3km]) {
      const bCtx = targetCv.getContext('2d');
      bCtx.clearRect(0, 0, S, S);
      bCtx.drawImage(this.terrainCanvas, 0, 0);
    }
    this._buildingVersion += 1;
    this._vectorCacheKey = '';
    this._lastVectorBuildMs = 0;
    this.stats.baseReady = true;
    return this.stats;
  }

  /**
   * 增量刷新静态全图建筑（SCVB 双缓存分级降噪）与编辑器动态建筑（addedInstances）至离屏雷达缓存。
   * - bakedCanvas9km：仅绘大中型主建筑/机库（>=14m 且高 >=3.8m），沉稳暖灰根除细白雪花噪点；
   * - bakedCanvas3km：保留精细街区建筑（>=5m 且高 >=2.2m），亮灰配细暗描边。
   */
  refreshBuildings(physicsEngine) {
    if (!this.terrainCanvas || !this.bakedCanvas9km || !this.bakedCanvas3km) {
      this.bakeBaseLayer({ bounds: this.bounds });
    }
    const S = this.offscreenSize;
    const ctx9 = this.bakedCanvas9km.getContext('2d');
    const ctx3 = this.bakedCanvas3km.getContext('2d');

    ctx9.clearRect(0, 0, S, S);
    ctx9.drawImage(this.terrainCanvas, 0, 0);
    ctx3.clearRect(0, 0, S, S);
    ctx3.drawImage(this.terrainCanvas, 0, 0);

    const b = this.bounds;
    const spanX = Math.max(1, b.maxX - b.minX);
    const spanZ = Math.max(1, b.maxZ - b.minZ);
    const mapSpan = Math.max(spanX, spanZ);
    const scalePx = S / mapSpan;

    // 重建 16×16 建筑世界空间分桶与动态建筑列表，供 <=3600m R1 矢量高清层实时重绘
    const bbN = this._bucketN;
    this._buildingBuckets = Array.from({ length: bbN * bbN }, () => []);
    this._dynamicBuildings = [];

    let static9kmCount = 0;
    let static3kmCount = 0;
    let dynamicDrawn = 0;

    // 1) 静态 SCVB 建筑双缓存分级烘焙 + 空间分桶收录
    const bIdx = physicsEngine?.buildingIndex;
    const protos = physicsEngine?.bldgProtoByIndex;
    if (bIdx && bIdx.totalInstances > 0 && bIdx.recordsView && Array.isArray(protos)) {
      const rv = bIdx.recordsView;
      const total = bIdx.totalInstances;

      ctx9.fillStyle = 'rgba(182, 178, 166, 0.44)';
      ctx9.strokeStyle = 'rgba(18, 22, 26, 0.34)';
      ctx9.lineWidth = 0.6;

      ctx3.fillStyle = 'rgba(196, 200, 210, 0.62)';
      ctx3.strokeStyle = 'rgba(16, 20, 28, 0.50)';
      ctx3.lineWidth = 0.65;

      for (let i = 0; i < total; i++) {
        const off = i * 56;
        if (off + 56 > rv.byteLength) break;
        const protoIdx = rv.getUint16(off, true);
        const meta = protos[protoIdx];
        if (!meta || meta.enabled === false) continue;

        const tx = rv.getFloat32(off + 44, true);
        const tz = rv.getFloat32(off + 52, true);
        if (tx < b.minX || tx > b.maxX || tz < b.minZ || tz > b.maxZ) continue;

        const bMin = meta.bounds_min;
        const bMax = meta.bounds_max;
        if (!bMin || !bMax) continue;
        const rawW = bMax[0] - bMin[0];
        const rawD = bMax[2] - bMin[2];
        const rawH = bMax[1] - bMin[1];
        const tier = classifyBuildingRadarTier(rawW, rawD, rawH);
        if (!tier.tier3km) continue;

        const m0 = rv.getFloat32(off + 8, true);
        const m2 = rv.getFloat32(off + 16, true);
        const m8 = rv.getFloat32(off + 32, true);
        const m10 = rv.getFloat32(off + 40, true);
        const sx = Math.hypot(m0, m2) || 1;
        const sz = Math.hypot(m8, m10) || 1;
        const worldW = rawW * sx;
        const worldD = rawD * sz;
        const pw = Math.max(1.5, worldW * scalePx);
        const pd = Math.max(1.5, worldD * scalePx);
        const px = (tx - b.minX) * scalePx;
        const py = (tz - b.minZ) * scalePx;
        const yaw = Math.atan2(m8, m0);

        const bx = clamp(Math.floor(((tx - b.minX) / spanX) * bbN), 0, bbN - 1);
        const bz = clamp(Math.floor(((tz - b.minZ) / spanZ) * bbN), 0, bbN - 1);
        this._buildingBuckets[bz * bbN + bx].push({
          x: tx,
          z: tz,
          w: worldW,
          d: worldD,
          yaw,
          major: tier.tier9km,
        });

        // 3KM 精细街区缓存（含描边）
        if (pw <= 2.4 && pd <= 2.4) {
          ctx3.fillRect(px - pw * 0.5, py - pd * 0.5, pw, pd);
        } else {
          ctx3.save();
          ctx3.translate(px, py);
          ctx3.rotate(yaw);
          ctx3.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx3.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx3.restore();
        }
        static3kmCount += 1;

        // 9KM 宏观降噪缓存（仅大中型主建筑/机库）
        if (tier.tier9km) {
          ctx9.save();
          ctx9.translate(px, py);
          ctx9.rotate(yaw);
          ctx9.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          if (pw >= 2.8 || pd >= 2.8) {
            ctx9.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          }
          ctx9.restore();
          static9kmCount += 1;
        }
      }
    }

    // 2) 编辑器动态放置建筑（双缓存 + R1 矢量队列同步亮琥珀标出）
    if (physicsEngine?.addedInstances instanceof Map && physicsEngine.addedInstances.size > 0) {
      for (const targetCtx of [ctx9, ctx3]) {
        targetCtx.fillStyle = 'rgba(246, 222, 134, 0.90)';
        targetCtx.strokeStyle = 'rgba(10, 12, 18, 0.88)';
        targetCtx.lineWidth = 0.9;
      }
      for (const inst of physicsEngine.addedInstances.values()) {
        const obb = inst?.obb;
        if (!obb || !obb.center || !obb.halfExtents) continue;
        const wx = obb.center[0];
        const wz = obb.center[2];
        const ww = obb.halfExtents[0] * 2;
        const wd = obb.halfExtents[2] * 2;
        const wyaw = obb.yawRad || 0;
        this._dynamicBuildings.push({ x: wx, z: wz, w: ww, d: wd, yaw: wyaw });

        const px = (wx - b.minX) * scalePx;
        const py = (wz - b.minZ) * scalePx;
        const pw = Math.max(3.2, ww * scalePx);
        const pd = Math.max(3.2, wd * scalePx);
        for (const targetCtx of [ctx9, ctx3]) {
          targetCtx.save();
          targetCtx.translate(px, py);
          targetCtx.rotate(wyaw);
          targetCtx.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          targetCtx.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          targetCtx.restore();
        }
        dynamicDrawn += 1;
      }
    }

    this._buildingVersion += 1;
    this._vectorCacheKey = '';
    this._lastVectorBuildMs = 0;
    this.stats.staticBuildings9kmCount = static9kmCount;
    this.stats.staticBuildings3kmCount = static3kmCount;
    this.stats.staticBuildingsDrawn = static9kmCount;
    this.stats.dynamicBuildingsDrawn = dynamicDrawn;
    return {
      static9kmCount,
      static3kmCount,
      staticDrawn: static9kmCount,
      dynamicDrawn,
    };
  }

  /**
   * 异步分片烘焙真实 9km 宏观底色军图、水域岸线、12m 等高线与公路/跑道骨架（每 ~8ms 让帧避免后台升级卡顿）。
   * 原同步 bakeBaseLayer(...) 100% 保留兼容既有调用。
   */
  async bakeBaseLayerAsync(
    { macroTint = null, support = null, bounds = null, roadsScene = null, splatCtrl = null } = {},
    { yieldFn = null, budgetMs = 8, shouldAbort = null } = {},
  ) {
    if (bounds) this.bounds = { ...this.bounds, ...bounds };
    const S = this.offscreenSize;
    if (!this.terrainCanvas) this.terrainCanvas = createOffscreenCanvas(S);
    if (!this.bakedCanvas9km) this.bakedCanvas9km = createOffscreenCanvas(S);
    if (!this.bakedCanvas3km) this.bakedCanvas3km = createOffscreenCanvas(S);
    this.bakedCanvas = this.bakedCanvas9km;

    const doYield = typeof yieldFn === 'function'
      ? yieldFn
      : () => new Promise(r => setTimeout(r, 0));
    const maxSliceMs = Math.max(2, Number(budgetMs) || 8);
    let sliceStart = performance.now();
    const maybeYield = async () => {
      if (shouldAbort?.()) return false;
      if (performance.now() - sliceStart >= maxSliceMs) {
        await doYield();
        if (shouldAbort?.()) return false;
        sliceStart = performance.now();
      }
      return true;
    };

    const workCanvas = createOffscreenCanvas(S);
    const ctx = workCanvas.getContext('2d');
    const outImg = ctx.createImageData(S, S);
    const dst = outImg.data;
    const roadMask = new Uint8Array(S * S);

    const rawData = macroTint?.data || macroTint?.texture?.image?.data || null;
    const rawW = Number(macroTint?.width || macroTint?.texture?.image?.width || 0);
    const rawH = Number(macroTint?.height || macroTint?.texture?.image?.height || 0);
    const img = macroTint?.texture?.image || macroTint?.image || null;
    const isDomDrawable = Boolean(
      img &&
      (
        (typeof HTMLImageElement !== 'undefined' && img instanceof HTMLImageElement && img.naturalWidth > 0) ||
        (typeof HTMLCanvasElement !== 'undefined' && img instanceof HTMLCanvasElement && img.width > 0) ||
        (typeof ImageBitmap !== 'undefined' && img instanceof ImageBitmap && img.width > 0) ||
        (typeof OffscreenCanvas !== 'undefined' && img instanceof OffscreenCanvas && img.width > 0)
      )
    );
    const imgValid = Boolean((rawData && rawW > 0 && rawH > 0) || isDomDrawable);

    if (rawData && rawW > 0 && rawH > 0 && rawData.length >= rawW * rawH * 4) {
      const scaleX = rawW / S;
      const scaleY = rawH / S;
      for (let py = 0; py < S; py++) {
        const sy = Math.min(rawH - 1, Math.floor((py + 0.5) * scaleY));
        const srcRow = sy * rawW;
        const dstRow = py * S;
        for (let px = 0; px < S; px++) {
          const sx = Math.min(rawW - 1, Math.floor((px + 0.5) * scaleX));
          const si = (srcRow + sx) * 4;
          const [r, g, b] = stylizeTacticalTerrainRgb(rawData[si], rawData[si + 1], rawData[si + 2]);
          const di = (dstRow + px) * 4;
          dst[di] = r;
          dst[di + 1] = g;
          dst[di + 2] = b;
          dst[di + 3] = 255;
        }
        if ((py & 63) === 63 && !(await maybeYield())) return this.stats;
      }
      this.stats.usedMacroTintImage = true;
    } else if (isDomDrawable) {
      ctx.clearRect(0, 0, S, S);
      ctx.drawImage(img, 0, 0, S, S);
      const srcImg = ctx.getImageData(0, 0, S, S).data;
      for (let py = 0; py < S; py++) {
        const rowOff = py * S;
        for (let px = 0; px < S; px++) {
          const o = (rowOff + px) * 4;
          const [r, g, b] = stylizeTacticalTerrainRgb(srcImg[o], srcImg[o + 1], srcImg[o + 2]);
          dst[o] = r;
          dst[o + 1] = g;
          dst[o + 2] = b;
          dst[o + 3] = 255;
        }
        if ((py & 63) === 63 && !(await maybeYield())) return this.stats;
      }
      this.stats.usedMacroTintImage = true;
    } else {
      for (let i = 0; i < S * S; i++) {
        const o = i * 4;
        dst[o] = 24;
        dst[o + 1] = 38;
        dst[o + 2] = 34;
        dst[o + 3] = 255;
      }
      this.stats.usedMacroTintImage = false;
    }

    if (!(await maybeYield())) return this.stats;

    let roleRoadCells = 0;
    if (support && typeof support.heightAt === 'function') {
      const N = 192;
      const b = this.bounds;
      const spanX = Math.max(1, b.maxX - b.minX);
      const spanZ = Math.max(1, b.maxZ - b.minZ);
      const heights = new Float32Array(N * N);
      const waters = new Uint8Array(N * N);
      const roadRoles = new Uint8Array(N * N);

      for (let iz = 0; iz < N; iz++) {
        const wz = b.minZ + ((iz + 0.5) / N) * spanZ;
        for (let ix = 0; ix < N; ix++) {
          const wx = b.minX + ((ix + 0.5) / N) * spanX;
          const idx = iz * N + ix;
          let h = 0;
          let isWater = 0;
          let isRoad = 0;
          if (typeof support.query === 'function') {
            const q = support.query(wx, wz);
            h = Number.isFinite(q?.height) ? q.height : (support.heightAt(wx, wz) || 0);
            if (q?.role === 5 || (q?.waterDepth ?? 0) > 0.08) isWater = 1;
            else if (q?.role === 2 || q?.role === 3) isRoad = 1;
          } else {
            h = support.heightAt(wx, wz) || 0;
          }
          heights[idx] = h;
          waters[idx] = isWater;
          roadRoles[idx] = isRoad;
          if (isRoad) roleRoadCells += 1;
        }
        if ((iz & 31) === 31 && !(await maybeYield())) return this.stats;
      }

      const contourInterval = 12.0;
      const bandGrid = new Int16Array(N * N);
      const shoreGrid = new Uint8Array(N * N);
      for (let i = 0; i < N * N; i++) bandGrid[i] = Math.floor(heights[i] / contourInterval);
      for (let iz = 0; iz < N; iz++) {
        for (let ix = 0; ix < N; ix++) {
          const idx = iz * N + ix;
          if (!waters[idx]) continue;
          if (
            (ix > 0 && !waters[idx - 1]) ||
            (ix + 1 < N && !waters[idx + 1]) ||
            (iz > 0 && !waters[idx - N]) ||
            (iz + 1 < N && !waters[idx + N])
          ) {
            shoreGrid[idx] = 1;
          }
        }
      }

      const ratio = N / S;
      for (let py = 0; py < S; py++) {
        const iz = Math.min(N - 1, Math.floor(py * ratio));
        const gRow = iz * N;
        const pRow = py * S;
        for (let px = 0; px < S; px++) {
          const ix = Math.min(N - 1, Math.floor(px * ratio));
          const gIdx = gRow + ix;
          const pIdx = pRow + px;
          const o = pIdx * 4;

          if (!imgValid) {
            const t = clamp((heights[gIdx] + 5) / 65, 0, 1);
            dst[o] = Math.round(24 + t * 28);
            dst[o + 1] = Math.round(44 + t * 34);
            dst[o + 2] = Math.round(36 + t * 20);
          }

          if (waters[gIdx]) {
            if (shoreGrid[gIdx]) {
              dst[o] = Math.round(dst[o] * 0.26 + 98 * 0.74);
              dst[o + 1] = Math.round(dst[o + 1] * 0.26 + 196 * 0.74);
              dst[o + 2] = Math.round(dst[o + 2] * 0.26 + 228 * 0.74);
            } else {
              dst[o] = Math.round(dst[o] * 0.14 + 18 * 0.86);
              dst[o + 1] = Math.round(dst[o + 1] * 0.14 + 62 * 0.86);
              dst[o + 2] = Math.round(dst[o + 2] * 0.14 + 114 * 0.86);
            }
            continue;
          }

          if (roadRoles[gIdx] && roadMask[pIdx] < 1) {
            roadMask[pIdx] = 1;
          }

          const band = bandGrid[gIdx];
          const rightDiff = ix + 1 < N && bandGrid[gIdx + 1] !== band && Math.floor((px + 1) * ratio) !== ix;
          const downDiff = iz + 1 < N && bandGrid[gIdx + N] !== band && Math.floor((py + 1) * ratio) !== iz;
          if (rightDiff || downDiff) {
            dst[o] = clamp(Math.round(dst[o] * 0.84 + 178 * 0.16), 0, 255);
            dst[o + 1] = clamp(Math.round(dst[o + 1] * 0.84 + 216 * 0.16), 0, 255);
            dst[o + 2] = clamp(Math.round(dst[o + 2] * 0.84 + 192 * 0.16), 0, 255);
          }
        }
        if ((py & 63) === 63 && !(await maybeYield())) return this.stats;
      }
    }

    const rbN = this._roadBucketN;
    const nextRoadBuckets = Array.from({ length: rbN * rbN }, () => []);
    let nextRoadStore = null;
    const rbSpanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
    const rbSpanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);

    let roadMeshesBaked = 0;
    let roadTrianglesBaked = 0;
    if (roadsScene && typeof roadsScene.traverse === 'function') {
      roadsScene.updateMatrixWorld?.(true);
      const roadMeshes = [];
      let totalTriCap = 0;
      roadsScene.traverse(obj => {
        if (obj && obj.isMesh && obj.geometry?.attributes?.position) {
          const posArr = obj.geometry.attributes.position.array;
          if (!posArr || posArr.length < 9) return;
          const idxArr = obj.geometry.index?.array || null;
          const tc = idxArr ? Math.floor(idxArr.length / 3) : Math.floor(posArr.length / 9);
          if (tc > 0) {
            roadMeshes.push(obj);
            totalTriCap += tc;
          }
        }
      });

      // 紧凑 TypedArray 平面缓冲替代 ~42 万个 JS 三角形对象，将雷达路网索引 JS 堆占用从 ~100 MiB 压至 ~13 MiB
      const coords = new Float32Array(Math.max(1, totalTriCap) * 6);
      const vals = new Uint8Array(Math.max(1, totalTriCap));
      let triCursor = 0;

      const b = this.bounds;
      const scalePx = S / Math.max(1, this.mapSpan);
      const pad = 40;
      const clipMinX = b.minX - pad;
      const clipMaxX = b.maxX + pad;
      const clipMinZ = b.minZ - pad;
      const clipMaxZ = b.maxZ + pad;

      const markMask = (px, py, val) => {
        if (px < 0 || px >= S || py < 0 || py >= S) return;
        const idx = py * S + px;
        if (roadMask[idx] < val) roadMask[idx] = val;
      };
      const drawEdgeMask = (ax, ay, bx, by, val) => {
        const dx = bx - ax;
        const dy = by - ay;
        const steps = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy))));
        if (steps > S * 2) return;
        const inv = 1 / steps;
        for (let s = 0; s <= steps; s++) {
          markMask(Math.round(ax + dx * s * inv), Math.round(ay + dy * s * inv), val);
        }
      };

      for (const obj of roadMeshes) {
        if (!(await maybeYield())) return this.stats;
        const posArr = obj.geometry.attributes.position.array;
        const idxArr = obj.geometry.index?.array || null;
        const m = obj.matrixWorld?.elements && obj.matrixWorld.elements.length >= 16
          ? obj.matrixWorld.elements
          : [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
        const m0 = m[0], m2 = m[2], m4 = m[4], m6 = m[6], m8 = m[8], m10 = m[10], m12 = m[12], m14 = m[14];
        const triCount = idxArr ? Math.floor(idxArr.length / 3) : Math.floor(posArr.length / 9);
        if (triCount <= 0) continue;

        const meshName = String(obj.name || '').toLowerCase();
        const val = /runway|apron|airfield|tarmac|marking|line/.test(meshName) ? 3 : 2;
        let meshTrisBaked = 0;

        // 单遍分片执行：变换 + 边界裁剪 + 像素掩码光栅化 + 紧凑 TypedArray 8×8 空间分桶
        for (let t = 0; t < triCount; t++) {
          const t3 = t * 3;
          const i0 = (idxArr ? idxArr[t3] : t3) * 3;
          const i1 = (idxArr ? idxArr[t3 + 1] : t3 + 1) * 3;
          const i2 = (idxArr ? idxArr[t3 + 2] : t3 + 2) * 3;

          const x0 = m0 * posArr[i0] + m4 * posArr[i0 + 1] + m8 * posArr[i0 + 2] + m12;
          const z0 = m2 * posArr[i0] + m6 * posArr[i0 + 1] + m10 * posArr[i0 + 2] + m14;
          const x1 = m0 * posArr[i1] + m4 * posArr[i1 + 1] + m8 * posArr[i1 + 2] + m12;
          const z1 = m2 * posArr[i1] + m6 * posArr[i1 + 1] + m10 * posArr[i1 + 2] + m14;
          const x2 = m0 * posArr[i2] + m4 * posArr[i2 + 1] + m8 * posArr[i2 + 2] + m12;
          const z2 = m2 * posArr[i2] + m6 * posArr[i2 + 1] + m10 * posArr[i2 + 2] + m14;

          if (
            (x0 < clipMinX && x1 < clipMinX && x2 < clipMinX) ||
            (x0 > clipMaxX && x1 > clipMaxX && x2 > clipMaxX) ||
            (z0 < clipMinZ && z1 < clipMinZ && z2 < clipMinZ) ||
            (z0 > clipMaxZ && z1 > clipMaxZ && z2 > clipMaxZ)
          ) {
            if ((t & 1023) === 1023 && !(await maybeYield())) return this.stats;
            continue;
          }

          const px0 = (x0 - b.minX) * scalePx;
          const py0 = (z0 - b.minZ) * scalePx;
          const px1 = (x1 - b.minX) * scalePx;
          const py1 = (z1 - b.minZ) * scalePx;
          const px2 = (x2 - b.minX) * scalePx;
          const py2 = (z2 - b.minZ) * scalePx;

          drawEdgeMask(px0, py0, px1, py1, val);
          drawEdgeMask(px1, py1, px2, py2, val);
          drawEdgeMask(px2, py2, px0, py0, val);

          const minPx = Math.max(0, Math.floor(Math.min(px0, px1, px2)));
          const maxPx = Math.min(S - 1, Math.ceil(Math.max(px0, px1, px2)));
          const minPy = Math.max(0, Math.floor(Math.min(py0, py1, py2)));
          const maxPy = Math.min(S - 1, Math.ceil(Math.max(py0, py1, py2)));
          if (maxPx - minPx >= 1 && maxPy - minPy >= 1) {
            const area = (px1 - px0) * (py2 - py0) - (py1 - py0) * (px2 - px0);
            if (Math.abs(area) >= 0.25) {
              const sign = area > 0 ? 1 : -1;
              for (let py = minPy; py <= maxPy; py++) {
                const cy = py + 0.5;
                const row = py * S;
                for (let px = minPx; px <= maxPx; px++) {
                  const cx = px + 0.5;
                  const w0 = ((px1 - px0) * (cy - py0) - (py1 - py0) * (cx - px0)) * sign;
                  if (w0 < -0.001) continue;
                  const w1 = ((px2 - px1) * (cy - py1) - (py2 - py1) * (cx - px1)) * sign;
                  if (w1 < -0.001) continue;
                  const w2 = ((px0 - px2) * (cy - py2) - (py0 - py2) * (cx - px2)) * sign;
                  if (w2 < -0.001) continue;
                  const idx = row + px;
                  if (roadMask[idx] < val) roadMask[idx] = val;
                }
              }
            }
          }

          const triIdx = triCursor++;
          const cOff = triIdx * 6;
          coords[cOff] = x0;
          coords[cOff + 1] = z0;
          coords[cOff + 2] = x1;
          coords[cOff + 3] = z1;
          coords[cOff + 4] = x2;
          coords[cOff + 5] = z2;
          vals[triIdx] = val;

          const tMinX = x0 < x1 ? (x0 < x2 ? x0 : x2) : (x1 < x2 ? x1 : x2);
          const tMaxX = x0 > x1 ? (x0 > x2 ? x0 : x2) : (x1 > x2 ? x1 : x2);
          const tMinZ = z0 < z1 ? (z0 < z2 ? z0 : z2) : (z1 < z2 ? z1 : z2);
          const tMaxZ = z0 > z1 ? (z0 > z2 ? z0 : z2) : (z1 > z2 ? z1 : z2);
          const bx0 = clamp(Math.floor(((tMinX - b.minX) / rbSpanX) * rbN), 0, rbN - 1);
          const bx1 = clamp(Math.floor(((tMaxX - b.minX) / rbSpanX) * rbN), 0, rbN - 1);
          const bz0 = clamp(Math.floor(((tMinZ - b.minZ) / rbSpanZ) * rbN), 0, rbN - 1);
          const bz1 = clamp(Math.floor(((tMaxZ - b.minZ) / rbSpanZ) * rbN), 0, rbN - 1);
          for (let bz = bz0; bz <= bz1; bz++) {
            for (let bx = bx0; bx <= bx1; bx++) {
              nextRoadBuckets[bz * rbN + bx].push(triIdx);
            }
          }
          meshTrisBaked += 1;

          if ((t & 1023) === 1023 && !(await maybeYield())) return this.stats;
        }

        if (meshTrisBaked > 0) {
          roadMeshesBaked += 1;
          roadTrianglesBaked += meshTrisBaked;
        }
      }

      if (triCursor > 0) {
        nextRoadStore = {
          count: triCursor,
          coords: triCursor < totalTriCap ? coords.slice(0, triCursor * 6) : coords,
          vals: triCursor < totalTriCap ? vals.slice(0, triCursor) : vals,
          stamps: new Int32Array(triCursor),
        };
        for (let bi = 0; bi < nextRoadBuckets.length; bi++) {
          nextRoadBuckets[bi] = Uint32Array.from(nextRoadBuckets[bi]);
        }
      }
    }

    for (let py = 0; py < S; py++) {
      const row = py * S;
      for (let px = 0; px < S; px++) {
        const idx = row + px;
        const mv = roadMask[idx];
        const o = idx * 4;
        if (mv === 3) {
          dst[o] = Math.round(dst[o] * 0.22 + 216 * 0.78);
          dst[o + 1] = Math.round(dst[o + 1] * 0.22 + 224 * 0.78);
          dst[o + 2] = Math.round(dst[o + 2] * 0.22 + 232 * 0.78);
        } else if (mv === 2) {
          dst[o] = Math.round(dst[o] * 0.28 + 198 * 0.72);
          dst[o + 1] = Math.round(dst[o + 1] * 0.28 + 206 * 0.72);
          dst[o + 2] = Math.round(dst[o + 2] * 0.28 + 214 * 0.72);
        } else if (mv === 1) {
          dst[o] = Math.round(dst[o] * 0.58 + 166 * 0.42);
          dst[o + 1] = Math.round(dst[o + 1] * 0.58 + 176 * 0.42);
          dst[o + 2] = Math.round(dst[o + 2] * 0.58 + 186 * 0.42);
        } else {
          const adjRoad =
            (px > 0 && roadMask[idx - 1] >= 2) ||
            (px + 1 < S && roadMask[idx + 1] >= 2) ||
            (py > 0 && roadMask[idx - S] >= 2) ||
            (py + 1 < S && roadMask[idx + S] >= 2);
          if (adjRoad) {
            dst[o] = Math.round(dst[o] * 0.68);
            dst[o + 1] = Math.round(dst[o + 1] * 0.68);
            dst[o + 2] = Math.round(dst[o + 2] * 0.68);
          }
        }
      }
      if ((py & 63) === 63 && !(await maybeYield())) return this.stats;
    }

    ctx.putImageData(outImg, 0, 0);
    if (splatCtrl?.grassData && splatCtrl.width > 0 && splatCtrl.height > 0 && roadTrianglesBaked === 0 && roleRoadCells === 0) {
      const sw = splatCtrl.width;
      const sh = splatCtrl.height;
      const step = 4;
      ctx.fillStyle = 'rgba(148, 158, 168, 0.16)';
      for (let iz = 0; iz < sh; iz += step) {
        const py = (iz / sh) * S;
        const row = iz * sw;
        for (let ix = 0; ix < sw; ix += step) {
          if (splatCtrl.grassData[row + ix] === 0) {
            ctx.fillRect((ix / sw) * S, py, (step / sw) * S + 0.4, (step / sh) * S + 0.4);
          }
        }
      }
    }

    if (shouldAbort?.()) return this.stats;

    // 整张烘焙完成后一次性提交至 terrainCanvas 与 9KM / 3KM 双缓存，防止分片期间雷达出现半绘残影
    const tCtx = this.terrainCanvas.getContext('2d');
    tCtx.clearRect(0, 0, S, S);
    tCtx.drawImage(workCanvas, 0, 0);
    for (const targetCv of [this.bakedCanvas9km, this.bakedCanvas3km]) {
      const bCtx = targetCv.getContext('2d');
      bCtx.clearRect(0, 0, S, S);
      bCtx.drawImage(this.terrainCanvas, 0, 0);
    }

    this._roadBuckets = nextRoadBuckets;
    this._roadStore = nextRoadStore;
    this.stats.roleRoadCellsBaked = roleRoadCells;
    this.stats.roadMeshesBaked = roadMeshesBaked;
    this.stats.roadTrianglesBaked = roadTrianglesBaked;
    this.stats.roadsBaked = roadTrianglesBaked > 0 || roleRoadCells > 0;
    this._buildingVersion += 1;
    this._vectorCacheKey = '';
    this._lastVectorBuildMs = 0;
    this.stats.baseReady = true;
    return this.stats;
  }

  /**
   * 异步分片刷新 9.3 万栋 SCVB 静态建筑与动态建筑至 9KM / 3KM 离屏缓存（零长任务阻塞，原同步接口保持兼容）。
   */
  async refreshBuildingsAsync(physicsEngine, { yieldFn = null, budgetMs = 8, shouldAbort = null } = {}) {
    if (!this.terrainCanvas || !this.bakedCanvas9km || !this.bakedCanvas3km) {
      await this.bakeBaseLayerAsync({ bounds: this.bounds }, { yieldFn, budgetMs, shouldAbort });
    }
    if (shouldAbort?.()) return null;

    const S = this.offscreenSize;
    const work9 = createOffscreenCanvas(S);
    const work3 = createOffscreenCanvas(S);
    const ctx9 = work9.getContext('2d');
    const ctx3 = work3.getContext('2d');
    ctx9.drawImage(this.terrainCanvas, 0, 0);
    ctx3.drawImage(this.terrainCanvas, 0, 0);

    const doYield = typeof yieldFn === 'function'
      ? yieldFn
      : () => new Promise(r => setTimeout(r, 0));
    const maxSliceMs = Math.max(2, Number(budgetMs) || 8);
    let sliceStart = performance.now();

    const b = this.bounds;
    const spanX = Math.max(1, b.maxX - b.minX);
    const spanZ = Math.max(1, b.maxZ - b.minZ);
    const mapSpan = Math.max(spanX, spanZ);
    const scalePx = S / mapSpan;

    const bbN = this._bucketN;
    const nextBuckets = Array.from({ length: bbN * bbN }, () => []);
    const nextDynamic = [];

    let static9kmCount = 0;
    let static3kmCount = 0;
    let dynamicDrawn = 0;

    const bIdx = physicsEngine?.buildingIndex;
    const protos = physicsEngine?.bldgProtoByIndex;
    if (bIdx && bIdx.totalInstances > 0 && bIdx.recordsView && Array.isArray(protos)) {
      const rv = bIdx.recordsView;
      const total = bIdx.totalInstances;

      ctx9.fillStyle = 'rgba(182, 178, 166, 0.44)';
      ctx9.strokeStyle = 'rgba(18, 22, 26, 0.34)';
      ctx9.lineWidth = 0.6;

      ctx3.fillStyle = 'rgba(196, 200, 210, 0.62)';
      ctx3.strokeStyle = 'rgba(16, 20, 28, 0.50)';
      ctx3.lineWidth = 0.65;

      for (let i = 0; i < total; i++) {
        const off = i * 56;
        if (off + 56 > rv.byteLength) break;
        const protoIdx = rv.getUint16(off, true);
        const meta = protos[protoIdx];
        if (!meta || meta.enabled === false) continue;

        const tx = rv.getFloat32(off + 44, true);
        const tz = rv.getFloat32(off + 52, true);
        if (tx < b.minX || tx > b.maxX || tz < b.minZ || tz > b.maxZ) continue;

        const bMin = meta.bounds_min;
        const bMax = meta.bounds_max;
        if (!bMin || !bMax) continue;
        const rawW = bMax[0] - bMin[0];
        const rawD = bMax[2] - bMin[2];
        const rawH = bMax[1] - bMin[1];
        const tier = classifyBuildingRadarTier(rawW, rawD, rawH);
        if (!tier.tier3km) continue;

        const m0 = rv.getFloat32(off + 8, true);
        const m2 = rv.getFloat32(off + 16, true);
        const m8 = rv.getFloat32(off + 32, true);
        const m10 = rv.getFloat32(off + 40, true);
        const sx = Math.hypot(m0, m2) || 1;
        const sz = Math.hypot(m8, m10) || 1;
        const worldW = rawW * sx;
        const worldD = rawD * sz;
        const pw = Math.max(1.5, worldW * scalePx);
        const pd = Math.max(1.5, worldD * scalePx);
        const px = (tx - b.minX) * scalePx;
        const py = (tz - b.minZ) * scalePx;
        const yaw = Math.atan2(m8, m0);

        const bx = clamp(Math.floor(((tx - b.minX) / spanX) * bbN), 0, bbN - 1);
        const bz = clamp(Math.floor(((tz - b.minZ) / spanZ) * bbN), 0, bbN - 1);
        nextBuckets[bz * bbN + bx].push({
          x: tx,
          z: tz,
          w: worldW,
          d: worldD,
          yaw,
          major: tier.tier9km,
        });

        if (pw <= 2.4 && pd <= 2.4) {
          ctx3.fillRect(px - pw * 0.5, py - pd * 0.5, pw, pd);
        } else {
          ctx3.save();
          ctx3.translate(px, py);
          ctx3.rotate(yaw);
          ctx3.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx3.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx3.restore();
        }
        static3kmCount += 1;

        if (tier.tier9km) {
          ctx9.save();
          ctx9.translate(px, py);
          ctx9.rotate(yaw);
          ctx9.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          if (pw >= 2.8 || pd >= 2.8) {
            ctx9.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          }
          ctx9.restore();
          static9kmCount += 1;
        }

        if ((i & 511) === 511 && (performance.now() - sliceStart) >= maxSliceMs) {
          await doYield();
          if (shouldAbort?.()) return null;
          sliceStart = performance.now();
        }
      }
    }

    if (physicsEngine?.addedInstances instanceof Map && physicsEngine.addedInstances.size > 0) {
      for (const targetCtx of [ctx9, ctx3]) {
        targetCtx.fillStyle = 'rgba(246, 222, 134, 0.90)';
        targetCtx.strokeStyle = 'rgba(10, 12, 18, 0.88)';
        targetCtx.lineWidth = 0.9;
      }
      for (const inst of physicsEngine.addedInstances.values()) {
        const obb = inst?.obb;
        if (!obb || !obb.center || !obb.halfExtents) continue;
        const wx = obb.center[0];
        const wz = obb.center[2];
        const ww = obb.halfExtents[0] * 2;
        const wd = obb.halfExtents[2] * 2;
        const wyaw = obb.yawRad || 0;
        nextDynamic.push({ x: wx, z: wz, w: ww, d: wd, yaw: wyaw });

        const px = (wx - b.minX) * scalePx;
        const py = (wz - b.minZ) * scalePx;
        const pw = Math.max(3.2, ww * scalePx);
        const pd = Math.max(3.2, wd * scalePx);
        for (const targetCtx of [ctx9, ctx3]) {
          targetCtx.save();
          targetCtx.translate(px, py);
          targetCtx.rotate(wyaw);
          targetCtx.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          targetCtx.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          targetCtx.restore();
        }
        dynamicDrawn += 1;
      }
    }

    if (shouldAbort?.()) return null;

    const dst9 = this.bakedCanvas9km.getContext('2d');
    dst9.clearRect(0, 0, S, S);
    dst9.drawImage(work9, 0, 0);
    const dst3 = this.bakedCanvas3km.getContext('2d');
    dst3.clearRect(0, 0, S, S);
    dst3.drawImage(work3, 0, 0);

    this._buildingBuckets = nextBuckets;
    this._dynamicBuildings = nextDynamic;
    this._buildingVersion += 1;
    this._vectorCacheKey = '';
    this._lastVectorBuildMs = 0;
    this.stats.staticBuildings9kmCount = static9kmCount;
    this.stats.staticBuildings3kmCount = static3kmCount;
    this.stats.staticBuildingsDrawn = static9kmCount;
    this.stats.dynamicBuildingsDrawn = dynamicDrawn;
    return {
      static9kmCount,
      static3kmCount,
      staticDrawn: static9kmCount,
      dynamicDrawn,
    };
  }

  _drawVectorRoadsInViewport(ctx, vp, W, H) {
    if (!this._roadBuckets.length) return 0;
    const rbN = this._roadBucketN;
    const spanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
    const spanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);
    const bx0 = clamp(Math.floor(((vp.minX - this.bounds.minX) / spanX) * rbN), 0, rbN - 1);
    const bx1 = clamp(Math.floor(((vp.maxX - this.bounds.minX) / spanX) * rbN), 0, rbN - 1);
    const bz0 = clamp(Math.floor(((vp.minZ - this.bounds.minZ) / spanZ) * rbN), 0, rbN - 1);
    const bz1 = clamp(Math.floor(((vp.maxZ - this.bounds.minZ) / spanZ) * rbN), 0, rbN - 1);

    const pxPerM = W / Math.max(1, vp.viewRangeM);
    const stamp = ++this._roadDrawStamp;
    const roads = [];
    const runways = [];
    const store = this._roadStore;

    if (store && store.coords && store.stamps && store.vals) {
      const { coords, vals, stamps } = store;
      for (let bz = bz0; bz <= bz1; bz++) {
        for (let bx = bx0; bx <= bx1; bx++) {
          const list = this._roadBuckets[bz * rbN + bx];
          for (let i = 0; i < list.length; i++) {
            const triIdx = list[i];
            if (stamps[triIdx] === stamp) continue;
            stamps[triIdx] = stamp;
            const cOff = triIdx * 6;
            const x0 = coords[cOff];
            const z0 = coords[cOff + 1];
            const x1 = coords[cOff + 2];
            const z1 = coords[cOff + 3];
            const x2 = coords[cOff + 4];
            const z2 = coords[cOff + 5];
            const minX = x0 < x1 ? (x0 < x2 ? x0 : x2) : (x1 < x2 ? x1 : x2);
            const maxX = x0 > x1 ? (x0 > x2 ? x0 : x2) : (x1 > x2 ? x1 : x2);
            const minZ = z0 < z1 ? (z0 < z2 ? z0 : z2) : (z1 < z2 ? z1 : z2);
            const maxZ = z0 > z1 ? (z0 > z2 ? z0 : z2) : (z1 > z2 ? z1 : z2);
            if (maxX < vp.minX || minX > vp.maxX || maxZ < vp.minZ || minZ > vp.maxZ) {
              continue;
            }
            const bwPx = (maxX - minX) * pxPerM;
            const bhPx = (maxZ - minZ) * pxPerM;
            if (bwPx < 0.55 && bhPx < 0.55) continue;
            if (vals[triIdx] === 3) runways.push(triIdx);
            else roads.push(triIdx);
          }
        }
      }

      const fillBatchPassPacked = (items, fillStyle) => {
        if (!items.length) return;
        ctx.fillStyle = fillStyle;
        const BATCH_SIZE = 512;
        for (let start = 0; start < items.length; start += BATCH_SIZE) {
          const end = Math.min(items.length, start + BATCH_SIZE);
          ctx.beginPath();
          for (let i = start; i < end; i++) {
            const cOff = items[i] * 6;
            ctx.moveTo((coords[cOff] - vp.minX) * pxPerM, (coords[cOff + 1] - vp.minZ) * pxPerM);
            ctx.lineTo((coords[cOff + 2] - vp.minX) * pxPerM, (coords[cOff + 3] - vp.minZ) * pxPerM);
            ctx.lineTo((coords[cOff + 4] - vp.minX) * pxPerM, (coords[cOff + 5] - vp.minZ) * pxPerM);
            ctx.closePath();
          }
          ctx.fill();
        }
      };

      fillBatchPassPacked(roads, 'rgba(196, 206, 216, 0.88)');
      fillBatchPassPacked(runways, 'rgba(220, 228, 236, 0.94)');
      return roads.length + runways.length;
    }

    for (let bz = bz0; bz <= bz1; bz++) {
      for (let bx = bx0; bx <= bx1; bx++) {
        const list = this._roadBuckets[bz * rbN + bx];
        for (let i = 0; i < list.length; i++) {
          const tri = list[i];
          if (tri._stamp === stamp) continue;
          tri._stamp = stamp;
          if (tri.maxX < vp.minX || tri.minX > vp.maxX || tri.maxZ < vp.minZ || tri.minZ > vp.maxZ) {
            continue;
          }
          const bwPx = (tri.maxX - tri.minX) * pxPerM;
          const bhPx = (tri.maxZ - tri.minZ) * pxPerM;
          if (bwPx < 0.55 && bhPx < 0.55) continue;
          if (tri.val === 3) runways.push(tri);
          else roads.push(tri);
        }
      }
    }

    // 分批纯 fill() 彻底移除 Skia 巨型路径 stroke() 阻塞（每 512 个三角形提交一次路径）
    const fillBatchPass = (items, fillStyle) => {
      if (!items.length) return;
      ctx.fillStyle = fillStyle;
      const BATCH_SIZE = 512;
      for (let start = 0; start < items.length; start += BATCH_SIZE) {
        const end = Math.min(items.length, start + BATCH_SIZE);
        ctx.beginPath();
        for (let i = start; i < end; i++) {
          const t = items[i];
          ctx.moveTo((t.x0 - vp.minX) * pxPerM, (t.z0 - vp.minZ) * pxPerM);
          ctx.lineTo((t.x1 - vp.minX) * pxPerM, (t.z1 - vp.minZ) * pxPerM);
          ctx.lineTo((t.x2 - vp.minX) * pxPerM, (t.z2 - vp.minZ) * pxPerM);
          ctx.closePath();
        }
        ctx.fill();
      }
    };

    fillBatchPass(roads, 'rgba(196, 206, 216, 0.88)');
    fillBatchPass(runways, 'rgba(220, 228, 236, 0.94)');
    return roads.length + runways.length;
  }

  _drawVectorBuildingsInViewport(ctx, vp, W, H) {
    if (!this._buildingBuckets.length && !this._dynamicBuildings.length) return 0;
    const pxPerM = W / Math.max(1, vp.viewRangeM);
    const padM = 80;
    const qMinX = vp.minX - padM;
    const qMaxX = vp.maxX + padM;
    const qMinZ = vp.minZ - padM;
    const qMaxZ = vp.maxZ + padM;

    let drawn = 0;
    if (this._buildingBuckets.length) {
      const bbN = this._bucketN;
      const spanX = Math.max(1, this.bounds.maxX - this.bounds.minX);
      const spanZ = Math.max(1, this.bounds.maxZ - this.bounds.minZ);
      const bx0 = clamp(Math.floor(((qMinX - this.bounds.minX) / spanX) * bbN), 0, bbN - 1);
      const bx1 = clamp(Math.floor(((qMaxX - this.bounds.minX) / spanX) * bbN), 0, bbN - 1);
      const bz0 = clamp(Math.floor(((qMinZ - this.bounds.minZ) / spanZ) * bbN), 0, bbN - 1);
      const bz1 = clamp(Math.floor(((qMaxZ - this.bounds.minZ) / spanZ) * bbN), 0, bbN - 1);

      const minorFast = [];
      const majorFast = [];
      const minorRot = [];
      const majorRot = [];

      for (let bz = bz0; bz <= bz1; bz++) {
        for (let bx = bx0; bx <= bx1; bx++) {
          const list = this._buildingBuckets[bz * bbN + bx];
          for (let i = 0; i < list.length; i++) {
            const b = list[i];
            if (b.x < qMinX || b.x > qMaxX || b.z < qMinZ || b.z > qMaxZ) continue;
            const px = (b.x - vp.minX) * pxPerM;
            const py = (b.z - vp.minZ) * pxPerM;
            const pw = Math.max(1.8, b.w * pxPerM);
            const pd = Math.max(1.8, b.d * pxPerM);
            const item = [px, py, pw, pd, b.yaw];
            if (pw <= 3.6 && pd <= 3.6) {
              if (b.major) majorFast.push(item);
              else minorFast.push(item);
            } else {
              if (b.major) majorRot.push(item);
              else minorRot.push(item);
            }
            drawn += 1;
          }
        }
      }

      if (minorFast.length) {
        ctx.fillStyle = 'rgba(188, 196, 208, 0.64)';
        for (let i = 0; i < minorFast.length; i++) {
          const [px, py, pw, pd] = minorFast[i];
          ctx.fillRect(px - pw * 0.5, py - pd * 0.5, pw, pd);
        }
      }
      if (majorFast.length) {
        ctx.fillStyle = 'rgba(214, 220, 232, 0.78)';
        for (let i = 0; i < majorFast.length; i++) {
          const [px, py, pw, pd] = majorFast[i];
          ctx.fillRect(px - pw * 0.5, py - pd * 0.5, pw, pd);
        }
      }

      ctx.strokeStyle = 'rgba(12, 16, 22, 0.68)';
      ctx.lineWidth = clamp(pxPerM * 3.8, 0.75, 1.45);
      const drawRotList = (items, fillStyle) => {
        if (!items.length) return;
        ctx.fillStyle = fillStyle;
        for (let i = 0; i < items.length; i++) {
          const [px, py, pw, pd, yaw] = items[i];
          ctx.save();
          ctx.translate(px, py);
          ctx.rotate(yaw);
          ctx.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
          ctx.restore();
        }
      };
      drawRotList(minorRot, 'rgba(188, 196, 208, 0.64)');
      drawRotList(majorRot, 'rgba(214, 220, 232, 0.78)');
    }

    if (this._dynamicBuildings.length) {
      ctx.fillStyle = 'rgba(246, 222, 134, 0.92)';
      ctx.strokeStyle = 'rgba(10, 12, 18, 0.90)';
      ctx.lineWidth = 1.05;
      for (let i = 0; i < this._dynamicBuildings.length; i++) {
        const b = this._dynamicBuildings[i];
        if (b.x < qMinX || b.x > qMaxX || b.z < qMinZ || b.z > qMaxZ) continue;
        const px = (b.x - vp.minX) * pxPerM;
        const py = (b.z - vp.minZ) * pxPerM;
        const pw = Math.max(3.6, b.w * pxPerM);
        const pd = Math.max(3.6, b.d * pxPerM);
        ctx.save();
        ctx.translate(px, py);
        ctx.rotate(b.yaw);
        ctx.fillRect(-pw * 0.5, -pd * 0.5, pw, pd);
        ctx.strokeRect(-pw * 0.5, -pd * 0.5, pw, pd);
        ctx.restore();
        drawn += 1;
      }
    }

    return drawn;
  }

  /**
   * 将雷达画布上的鼠标事件映射为当前渲染视口对应的精确世界坐标 (x, z)。
   */
  screenEventToWorld(e, boxEl, state) {
    const r = boxEl.getBoundingClientRect();
    const vp = this.lastViewport || computeMinimapViewport(
      this.currentRangeM,
      state?.center?.x ?? this.currentCenterX,
      state?.center?.z ?? this.currentCenterZ,
      this.bounds,
    );
    return minimapPxToWorld(e.clientX - r.left, e.clientY - r.top, vp, r.width, r.height);
  }

  /**
   * 每帧 144Hz 前景实时渲染（接入主 requestAnimationFrame，零 140ms 节流，支持无级阻尼缩放与 R1 矢量高清层）。
   */
  renderFrame({ canvas, state, combatSystem, host, nowMs = performance.now(), domRefs = null }) {
    if (!canvas || !state) return;

    const cssW = Math.max(120, Math.round(canvas.clientWidth || 248));
    const cssH = Math.max(120, Math.round(canvas.clientHeight || cssW));
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const targetW = Math.round(cssW * dpr);
    const targetH = Math.round(cssH * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const cx = state.center?.x ?? 0;
    const cz = state.center?.z ?? 0;

    // 144Hz 无级阻尼缩放与中心追踪推进（若主镜头主动移动，自动解除鼠标缩放锚定追踪新视口）
    if (
      this._manualAnchorActive
      && Number.isFinite(this._anchorCamX)
      && Number.isFinite(this._anchorCamZ)
      && Math.hypot(cx - this._anchorCamX, cz - this._anchorCamZ) > 2.0
    ) {
      this._manualAnchorActive = false;
    }
    if (!this._manualAnchorActive) {
      this.targetCenterX = cx;
      this.targetCenterZ = cz;
    }

    const dtSec = this._lastRenderMs > 0
      ? clamp((nowMs - this._lastRenderMs) * 0.001, 0.001, 0.08)
      : 0.016;
    this._lastRenderMs = nowMs;
    const dampAlpha = 1 - Math.exp(-13.0 * dtSec);

    if (Math.abs(this.targetRangeM - this.currentRangeM) < 1.5) {
      this.currentRangeM = this.targetRangeM;
    } else {
      this.currentRangeM += (this.targetRangeM - this.currentRangeM) * dampAlpha;
      this._lastTextSyncMs = 0;
    }
    if (Math.abs(this.targetCenterX - this.currentCenterX) < 0.8) {
      this.currentCenterX = this.targetCenterX;
    } else {
      this.currentCenterX += (this.targetCenterX - this.currentCenterX) * dampAlpha;
    }
    if (Math.abs(this.targetCenterZ - this.currentCenterZ) < 0.8) {
      this.currentCenterZ = this.targetCenterZ;
    } else {
      this.currentCenterZ += (this.targetCenterZ - this.currentCenterZ) * dampAlpha;
    }

    ctx.save();
    ctx.scale(dpr, dpr);
    const W = cssW;
    const H = cssH;

    const vp = computeMinimapViewport(this.currentRangeM, this.currentCenterX, this.currentCenterZ, this.bounds);
    this.lastViewport = vp;

    // 1) 按倍率选用离屏 9KM 烘焙或 R1 <=3600m 局部矢量高清缓存层
    ctx.fillStyle = '#0f181a';
    ctx.fillRect(0, 0, W, H);
    const useVectorLod = vp.viewRangeM <= MINIMAP_VECTOR_LOD_THRESHOLD_M
      && (this._buildingBuckets.length > 0 || this._roadBuckets.length > 0);
    const isAnimating =
      Math.abs(this.targetRangeM - this.currentRangeM) > 8 ||
      Math.abs(this.targetCenterX - this.currentCenterX) > 2.5 ||
      Math.abs(this.targetCenterZ - this.currentCenterZ) > 2.5;

    if (this.layers.terrain && useVectorLod) {
      if (isAnimating) {
        const animSrc = this.bakedCanvas3km || this.terrainCanvas || this.bakedCanvas9km;
        if (animSrc) {
          const srcScale = animSrc.width / Math.max(1, vp.mapSpan);
          const sx = (vp.minX - vp.mapMinX) * srcScale;
          const sy = (vp.minZ - vp.mapMinZ) * srcScale;
          const sw = vp.viewRangeM * srcScale;
          ctx.imageSmoothingEnabled = true;
          ctx.drawImage(animSrc, sx, sy, sw, sw, 0, 0, W, H);
        }
        this.stats.vectorLodActive = false;
        this.stats.vectorRoadsDrawn = 0;
        this.stats.vectorBuildingsDrawn = 0;
      } else {
        const cacheKey = `${targetW}x${targetH}:${dpr}:${this._buildingVersion}:${Math.round(vp.minX * 2)},${Math.round(vp.minZ * 2)},${Math.round(vp.viewRangeM * 2)}`;
        const sizeMatched = Boolean(
          this.vectorCacheCanvas
          && this.vectorCacheCanvas.width === targetW
          && this.vectorCacheCanvas.height === targetH
        );
        const inThrottleWindow =
          sizeMatched
          && cacheKey !== this._vectorCacheKey
          && this._lastVectorBuildMs > 0
          && nowMs >= this._lastVectorBuildMs
          && (nowMs - this._lastVectorBuildMs) < 140
          && this._lastVectorBuildRange === Math.round(vp.viewRangeM);

        if (inThrottleWindow) {
          const animSrc = this.bakedCanvas3km || this.terrainCanvas || this.bakedCanvas9km;
          if (animSrc) {
            const srcScale = animSrc.width / Math.max(1, vp.mapSpan);
            const sx = (vp.minX - vp.mapMinX) * srcScale;
            const sy = (vp.minZ - vp.mapMinZ) * srcScale;
            const sw = vp.viewRangeM * srcScale;
            ctx.imageSmoothingEnabled = true;
            ctx.drawImage(animSrc, sx, sy, sw, sw, 0, 0, W, H);
          }
          this.stats.vectorLodActive = false;
          this.stats.vectorRoadsDrawn = 0;
          this.stats.vectorBuildingsDrawn = 0;
        } else {
          if (!sizeMatched || cacheKey !== this._vectorCacheKey) {
            if (!this.vectorCacheCanvas) {
              this.vectorCacheCanvas = createOffscreenCanvas(targetW);
            }
            if (this.vectorCacheCanvas.width !== targetW) this.vectorCacheCanvas.width = targetW;
            if (this.vectorCacheCanvas.height !== targetH) this.vectorCacheCanvas.height = targetH;
            const vctx = this.vectorCacheCanvas.getContext('2d');
            if (vctx) {
              vctx.setTransform(dpr, 0, 0, dpr, 0, 0);
              vctx.clearRect(0, 0, W, H);
              const tSrc = this.terrainCanvas || this.bakedCanvas3km || this.bakedCanvas9km;
              if (tSrc) {
                const srcScale = tSrc.width / Math.max(1, vp.mapSpan);
                const sx = (vp.minX - vp.mapMinX) * srcScale;
                const sy = (vp.minZ - vp.mapMinZ) * srcScale;
                const sw = vp.viewRangeM * srcScale;
                vctx.imageSmoothingEnabled = true;
                vctx.drawImage(tSrc, sx, sy, sw, sw, 0, 0, W, H);
              }
              this._lastVectorRoadsDrawn = this._drawVectorRoadsInViewport(vctx, vp, W, H);
              this._lastVectorBuildingsDrawn = this._drawVectorBuildingsInViewport(vctx, vp, W, H);
              this._vectorCacheKey = cacheKey;
              this._lastVectorBuildMs = nowMs;
              this._lastVectorBuildRange = Math.round(vp.viewRangeM);
            }
          }
          if (this.vectorCacheCanvas) {
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(this.vectorCacheCanvas, 0, 0, targetW, targetH, 0, 0, W, H);
            ctx.imageSmoothingEnabled = true;
          }
          this.stats.vectorLodActive = true;
          this.stats.vectorRoadsDrawn = this._lastVectorRoadsDrawn;
          this.stats.vectorBuildingsDrawn = this._lastVectorBuildingsDrawn;
        }
      }
    } else {
      const baseSrc = this.bakedCanvas9km || this.bakedCanvas3km || this.bakedCanvas;
      if (this.layers.terrain && baseSrc) {
        const srcScale = baseSrc.width / Math.max(1, vp.mapSpan);
        const sx = (vp.minX - vp.mapMinX) * srcScale;
        const sy = (vp.minZ - vp.mapMinZ) * srcScale;
        const sw = vp.viewRangeM * srcScale;
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(baseSrc, sx, sy, sw, sw, 0, 0, W, H);
      }
      this.stats.vectorLodActive = false;
      this.stats.vectorRoadsDrawn = 0;
      this.stats.vectorBuildingsDrawn = 0;
    }

    // 2) 真实世界坐标绑定动态三级 MGRS 网格（1500m -> 500m -> 250m，避让顶部徽章防遮挡）
    if (this.layers.grid) {
      const gridSpec = computeDynamicMgrsGrid(vp);
      ctx.lineWidth = 1;
      for (const xl of gridSpec.xLines) {
        const px = Math.round(xl.norm * W) + 0.5;
        if (px < 0 || px > W) continue;
        ctx.strokeStyle = xl.isMajor ? 'rgba(164, 212, 230, 0.17)' : 'rgba(164, 212, 230, 0.08)';
        ctx.beginPath();
        ctx.moveTo(px, 0);
        ctx.lineTo(px, H);
        ctx.stroke();
      }
      for (const zl of gridSpec.zLines) {
        const py = Math.round(zl.norm * H) + 0.5;
        if (py < 0 || py > H) continue;
        ctx.strokeStyle = zl.isMajor ? 'rgba(164, 212, 230, 0.17)' : 'rgba(164, 212, 230, 0.08)';
        ctx.beginPath();
        ctx.moveTo(0, py);
        ctx.lineTo(W, py);
        ctx.stroke();
      }

      ctx.font = '600 9px Consolas, monospace';
      for (const xl of gridSpec.xLines) {
        if (!xl.label) continue;
        const px = Math.round(xl.norm * W) + 4;
        if (px < 86 || px > W - 36) continue;
        ctx.fillStyle = xl.isMajor ? 'rgba(216, 228, 238, 0.54)' : 'rgba(182, 204, 218, 0.34)';
        ctx.fillText(xl.label, px, 24);
      }
      for (const zl of gridSpec.zLines) {
        if (!zl.label) continue;
        const py = Math.round(zl.norm * H) + 11;
        if (py < 30 || py > H - 28) continue;
        ctx.fillStyle = 'rgba(216, 228, 238, 0.50)';
        ctx.fillText(zl.label, 5, py);
      }
    }

    // 2.5) 战争迷雾双通道遮罩层（10Hz 同步刷新离屏 256×256 迷雾画布，144Hz 裁切叠画，与 3D 视野边界严丝合缝）
    const fogSnap = (this.layers.fog && !state?.editorMode)
      ? combatSystem?.getFogSnapshot?.()
      : null;
    const fogActive = Boolean(fogSnap && fogSnap.enabled && fogSnap.currRGBA && fogSnap.width > 0 && fogSnap.height > 0);
    this.stats.fogActive = fogActive;
    this.stats.fogGridVersion = fogActive ? (fogSnap.gridVersion || 0) : 0;
    this.stats.fogEmitterCount = fogActive ? (fogSnap.emitters?.length || 0) : 0;
    this.stats.fogGhostCount = fogActive ? (fogSnap.ghostContacts?.length || 0) : 0;

    if (fogActive) {
      const fw = fogSnap.width;
      const fh = fogSnap.height;
      if (!this._fogCanvas || this._lastFogW !== fw || this._lastFogH !== fh) {
        this._fogCanvas = createOffscreenCanvas(fw);
        if (this._fogCanvas) {
          this._fogCanvas.width = fw;
          this._fogCanvas.height = fh;
          this._fogCtx = this._fogCanvas.getContext('2d');
          this._fogImgData = this._fogCtx ? this._fogCtx.createImageData(fw, fh) : null;
        }
        this._lastFogW = fw;
        this._lastFogH = fh;
        this._lastFogGridVersion = -1;
      }
      if (this._fogCtx && this._fogImgData && this._lastFogGridVersion !== fogSnap.gridVersion) {
        const src = fogSnap.currRGBA;
        const dst = this._fogImgData.data;
        const totalPx = fw * fh;
        for (let i = 0; i < totalPx; i += 1) {
          const off = i * 4;
          const live = src[off] / 255;
          const explored = src[off + 1] / 255;
          const ew = src[off + 3] / 255;

          // 未探索深暗遮罩 (0.86) → 已探索半暗灰雾 (0.52) → 实时清晰 (0.0)
          const baseAlpha = (0.86 * (1 - explored) + 0.52 * explored) * (1 - live);
          let r = 6 + explored * 7;
          let g = 11 + explored * 11;
          let b = 18 + explored * 14;
          let a = baseAlpha;

          // A 通道电战/声源宏观态势热区（不破除迷雾，仅在小地图叠绘柔和青琥珀信号）
          if (ew > 0.08 && live < 0.7) {
            const ewBoost = ew * (1 - live * 0.75);
            r = Math.min(255, r + ewBoost * 155);
            g = Math.min(255, g + ewBoost * 112);
            b = Math.min(255, b + ewBoost * 42);
            a = Math.max(a, ewBoost * 0.42);
          }

          dst[off] = r;
          dst[off + 1] = g;
          dst[off + 2] = b;
          dst[off + 3] = Math.round(clamp(a, 0, 1) * 255);
        }
        this._fogCtx.putImageData(this._fogImgData, 0, 0);
        this._lastFogGridVersion = fogSnap.gridVersion;
      }

      if (this._fogCanvas) {
        const fogBounds = fogSnap.bounds || this.bounds;
        const fogSpanX = Math.max(1, (fogBounds.maxX ?? 4575) - (fogBounds.minX ?? -4425));
        const fogSpanZ = Math.max(1, (fogBounds.maxZ ?? 4850) - (fogBounds.minZ ?? -4150));
        const sx = ((vp.minX - fogBounds.minX) / fogSpanX) * fw;
        const sy = ((vp.minZ - fogBounds.minZ) / fogSpanZ) * fh;
        const sw = (vp.viewRangeM / fogSpanX) * fw;
        const sh = (vp.viewRangeM / fogSpanZ) * fh;
        ctx.save();
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(this._fogCanvas, sx, sy, sw, sh, 0, 0, W, H);
        ctx.restore();
      }
    }

    const heroUnits = (combatSystem?.heroUnits || []).filter(u => !u?.evacuated);
    const selectedIds = combatSystem?.selectedIds || new Set();
    const primaryId = combatSystem?.selectedUnitId || null;

    // 2.6) UAV / AC-130 实时侦察覆盖虚线圈 + 失联幽灵标（位于迷雾层之上，保持清晰可读）
    if (fogActive) {
      const emitters = fogSnap.emitters || [];
      for (let i = 0; i < emitters.length; i += 1) {
        const em = emitters[i];
        if (!em || !em.active) continue;
        if (em.role !== 'uav' && em.shape !== 'orbit_pod') continue;
        const cxEm = em.orbitCenterX !== null ? em.orbitCenterX : em.x;
        const czEm = em.orbitCenterZ !== null ? em.orbitCenterZ : em.z;
        const ep = worldToMinimapPx(cxEm, czEm, vp, W, H);
        const rPx = Math.max(5, (em.liveRadiusM / vp.viewRangeM) * W);
        if (ep.x + rPx < -6 || ep.x - rPx > W + 6 || ep.y + rPx < -6 || ep.y - rPx > H + 6) continue;
        const isSel = selectedIds.has(em.unitId) || em.unitId === primaryId;
        const isWarn = Boolean(em.bingoWarning);
        const pulse = isWarn ? (0.55 + 0.35 * Math.sin(nowMs * 0.008)) : 1.0;

        ctx.save();
        ctx.strokeStyle = isWarn
          ? `rgba(246, 184, 61, ${(0.88 * pulse).toFixed(3)})`
          : (isSel ? 'rgba(86, 230, 216, 0.85)' : 'rgba(86, 230, 216, 0.44)');
        ctx.lineWidth = isSel || isWarn ? 1.35 : 0.95;
        ctx.setLineDash(isWarn ? [3, 2] : [4, 3]);
        ctx.beginPath();
        ctx.arc(ep.x, ep.y, rPx, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      const ghosts = fogSnap.ghostContacts || [];
      for (let i = 0; i < ghosts.length; i += 1) {
        const g = ghosts[i];
        if (!g || !Number.isFinite(g.x) || !Number.isFinite(g.z)) continue;
        const gp = worldToMinimapPx(g.x, g.z, vp, W, H);
        if (gp.x < -8 || gp.x > W + 8 || gp.y < -8 || gp.y > H + 8) continue;
        const fade = Math.max(0.32, 1 - (Number(g.ageSec) || 0) / 45);
        ctx.save();
        ctx.globalAlpha = fade;
        ctx.translate(gp.x, gp.y);
        ctx.strokeStyle = '#f08c4a';
        ctx.fillStyle = 'rgba(240, 140, 74, 0.24)';
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(0, -4.2);
        ctx.lineTo(4.2, 0);
        ctx.lineTo(0, 4.2);
        ctx.lineTo(-4.2, 0);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
    }

    // 3) 选中单位半透航线与固定翼盘旋圈图层
    if (this.layers.routes && combatSystem?.combatMode === 'g01_vehicles') {
      for (const u of heroUnits) {
        const isSel = selectedIds.has(u.id) || u.id === primaryId;
        if (!isSel || !u.node?.position) continue;
        const p0 = worldToMinimapPx(u.node.position.x, u.node.position.z, vp, W, H);

        if (u.role === 'fixed_wing' && u.orbitCenter) {
          const oc = worldToMinimapPx(u.orbitCenter.x, u.orbitCenter.z, vp, W, H);
          const pr = Math.max(4, ((u.orbitRadius || 85) / vp.viewRangeM) * W);
          ctx.save();
          ctx.strokeStyle = 'rgba(177, 151, 252, 0.54)';
          ctx.lineWidth = 1.15;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.arc(oc.x, oc.y, pr, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }

        const remWps = Array.isArray(u.waypoints) ? u.waypoints.slice(u.wpIndex || 0) : [];
        if (remWps.length > 0) {
          ctx.save();
          ctx.strokeStyle = 'rgba(240, 211, 114, 0.72)';
          ctx.lineWidth = 1.25;
          ctx.beginPath();
          ctx.moveTo(p0.x, p0.y);
          for (const wp of remWps) {
            const p = worldToMinimapPx(wp.x, wp.z, vp, W, H);
            ctx.lineTo(p.x, p.y);
          }
          ctx.stroke();
          const lastWp = remWps[remWps.length - 1];
          const pLast = worldToMinimapPx(lastWp.x, lastWp.z, vp, W, H);
          ctx.fillStyle = '#f0d372';
          ctx.fillRect(pLast.x - 2, pLast.y - 2, 4, 4);
          ctx.restore();
        }
      }
    }

    // 4) 单位矢量方向标（兼容 100 混编 squadUnits 与 G01 heroUnits）
    if (combatSystem && combatSystem.combatMode !== 'g01_vehicles') {
      for (const s of combatSystem.squadUnits || []) {
        const mp = worldToMinimapPx(s.x, s.z, vp, W, H);
        if (mp.x < -4 || mp.x > W + 4 || mp.y < -4 || mp.y > H + 4) continue;
        ctx.fillStyle = s.kind === 'heli' ? '#68c8ff' : s.kind === 'jet' ? '#b197fc' : '#38e5c0';
        ctx.fillRect(mp.x - 1.5, mp.y - 1.5, 3, 3);
      }
    }

    for (const u of heroUnits) {
      if (!u.node?.position) continue;
      const mp = worldToMinimapPx(u.node.position.x, u.node.position.z, vp, W, H);
      if (mp.x < -8 || mp.x > W + 8 || mp.y < -8 || mp.y > H + 8) continue;
      const isSel = selectedIds.has(u.id) || u.id === primaryId;
      const col = isSel ? '#f0d372' : channelColorHex(u.channel);

      ctx.save();
      ctx.translate(mp.x, mp.y);
      if (isSel) {
        ctx.strokeStyle = 'rgba(240, 211, 114, 0.72)';
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        ctx.arc(0, 0, 6.0, 0, Math.PI * 2);
        ctx.stroke();
      }
      // 场景单位前向为 +Z，yaw=0 朝 +Z（地图下方）；将局部绘制朝向对齐为 +Z 方向
      const yaw = Number.isFinite(u.node.rotation?.y) ? u.node.rotation.y : 0;
      ctx.rotate(-yaw + Math.PI);
      ctx.fillStyle = col;
      ctx.strokeStyle = '#090b10';
      ctx.lineWidth = 1.05;

      if (u.role === 'fixed_wing') {
        ctx.beginPath();
        ctx.moveTo(0, -4.6);
        ctx.lineTo(3.8, 3.2);
        ctx.lineTo(0, 1.5);
        ctx.lineTo(-3.8, 3.2);
        ctx.closePath();
        ctx.stroke();
        ctx.fill();
      } else if (u.role === 'helicopter') {
        ctx.beginPath();
        ctx.moveTo(0, -4.0);
        ctx.lineTo(3.2, 0.4);
        ctx.lineTo(0, 3.2);
        ctx.lineTo(-3.2, 0.4);
        ctx.closePath();
        ctx.stroke();
        ctx.fill();
      } else {
        ctx.fillRect(-2.3, -2.3, 4.6, 4.6);
        ctx.strokeRect(-2.3, -2.3, 4.6, 4.6);
        ctx.fillRect(-0.8, -4.2, 1.6, 2.0);
      }
      ctx.restore();
    }

    // 5) 144Hz 同步旋转聚焦视野黄框（随 state.angle 实时旋转，内透高光 + L 型四角靶标 + 微十字）
    const cp = worldToMinimapPx(cx, cz, vp, W, H);
    const aspect = state.forcedAspect || ((host?.clientWidth || 1280) / Math.max(1, host?.clientHeight || 720));
    const baseHalfH = (state.span * 0.5) / (16 / 9);
    const viewW_m = baseHalfH * 2 * aspect;
    const viewH_m = (baseHalfH * 2) / TILT_SIN;
    const rw = Math.max(12, (viewW_m / vp.viewRangeM) * W);
    const rh = Math.max(10, (viewH_m / vp.viewRangeM) * H);

    ctx.save();
    ctx.translate(cp.x, cp.y);
    ctx.rotate(-(state.angle || 0));

    ctx.fillStyle = 'rgba(240, 211, 114, 0.09)';
    ctx.fillRect(-rw * 0.5, -rh * 0.5, rw, rh);

    ctx.strokeStyle = 'rgba(240, 211, 114, 0.56)';
    ctx.lineWidth = 1.05;
    ctx.strokeRect(-rw * 0.5, -rh * 0.5, rw, rh);

    const cl = Math.min(7, rw * 0.28, rh * 0.28);
    const hw = rw * 0.5;
    const hh = rh * 0.5;
    ctx.strokeStyle = '#f6de86';
    ctx.lineWidth = 1.9;
    ctx.beginPath();
    ctx.moveTo(-hw, -hh + cl); ctx.lineTo(-hw, -hh); ctx.lineTo(-hw + cl, -hh);
    ctx.moveTo(hw - cl, -hh); ctx.lineTo(hw, -hh); ctx.lineTo(hw, -hh + cl);
    ctx.moveTo(-hw, hh - cl); ctx.lineTo(-hw, hh); ctx.lineTo(-hw + cl, hh);
    ctx.moveTo(hw - cl, hh); ctx.lineTo(hw, hh); ctx.lineTo(hw, hh - cl);
    ctx.stroke();

    ctx.strokeStyle = 'rgba(246, 222, 134, 0.78)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(-3, 0); ctx.lineTo(3, 0);
    ctx.moveTo(0, -3); ctx.lineTo(0, 3);
    ctx.stroke();
    ctx.restore();

    // 6) 右下角实时动态军用比例尺条（随无级缩放实时伸缩刻度长度与读数）
    const scaleBar = computeMinimapScaleBar(vp.viewRangeM, W);
    const sbRight = W - 7;
    const sbLeft = sbRight - scaleBar.widthPx;
    const sbY = H - 9;
    ctx.save();
    ctx.fillStyle = 'rgba(9, 12, 18, 0.78)';
    ctx.fillRect(sbLeft - 5, sbY - 13, scaleBar.widthPx + 10, 17);
    ctx.strokeStyle = 'rgba(210, 232, 240, 0.90)';
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    ctx.moveTo(sbLeft, sbY - 4);
    ctx.lineTo(sbLeft, sbY);
    ctx.lineTo(sbRight, sbY);
    ctx.lineTo(sbRight, sbY - 4);
    ctx.stroke();
    ctx.font = '700 9.5px Consolas, monospace';
    ctx.fillStyle = '#dbeaf0';
    ctx.textAlign = 'right';
    ctx.fillText(scaleBar.label, sbRight - 1, sbY - 4);
    ctx.restore();

    ctx.restore();

    // 实时同步指北针旋转角（每帧 144Hz）与节流/缩放实时文本角标更新
    this.stats.frameCount += 1;
    this._hzAccFrames += 1;
    if (!this._hzAccStartMs) this._hzAccStartMs = nowMs;
    if (nowMs - this._hzAccStartMs >= 500) {
      const hz = Math.round((this._hzAccFrames * 1000) / Math.max(1, nowMs - this._hzAccStartMs));
      this.stats.liveHz = Math.min(240, Math.max(1, hz));
      this._hzAccFrames = 0;
      this._hzAccStartMs = nowMs;
    }

    if (domRefs?.compassSvg) {
      const deg = ((-state.angle || 0) * 180) / Math.PI;
      domRefs.compassSvg.style.transform = `rotate(${deg.toFixed(1)}deg)`;
    }
    if (nowMs - this._lastTextSyncMs >= 110) {
      this._lastTextSyncMs = nowMs;
      if (domRefs?.modeLbl) {
        const rangeKm = vp.viewRangeM >= this.mapSpan - 40
          ? '9KM'
          : `${(vp.viewRangeM / 1000).toFixed(1)}KM`;
        const ratioTxt = (this.mapSpan / Math.max(1, vp.viewRangeM)).toFixed(1);
        domRefs.modeLbl.textContent = `${rangeKm} · ${ratioTxt}×`;
      }
      if (domRefs?.hintEl) {
        domRefs.hintEl.textContent = `(${cx.toFixed(0)}, ${cz.toFixed(0)}) · ${state.span.toFixed(0)}m`;
      }
      if (domRefs?.hzEl) {
        domRefs.hzEl.textContent = `${this.stats.liveHz}Hz 同步`;
      }
    }
  }
}
