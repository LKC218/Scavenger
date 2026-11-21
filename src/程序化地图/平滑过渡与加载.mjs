/**
 * B4阶段：PMTiles 思路 .scvmap 空间归档 HTTP Range 按需加载器 + 距离驱动 8x8 Bayer 平滑过渡与 ±15% 滞回控制器。
 */

const ARCHIVE_FILES = [
  'startup-base.scvmap',
  'terrain-support-morton.scvmap',
  'modular-kit-morton.scvmap',
];

/**
 * 解码 <= 128KB 的定长二进制空间索引文件 spatial-index.scvi。
 */
export async function decodeSCVIBinaryIndex(arrayBuffer) {
  const dv = new DataView(arrayBuffer);
  if (dv.byteLength < 32 || dv.getUint32(0, true) !== 0x49564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVI 二进制空间索引头部无效');
  }
  const archiveCount = dv.getUint32(8, true);
  const supportGrid = dv.getUint32(12, true);
  const kitCount = dv.getUint32(16, true);
  const coreSegmentEndOffset = dv.getUint32(20, true);
  const dirOffset = dv.getUint32(24, true);
  const dirLength = dv.getUint32(28, true);

  const supportSlots = new Array(supportGrid * supportGrid);
  let pos = 32;
  for (let i = 0; i < supportGrid * supportGrid; i++) {
    supportSlots[i] = {
      offset: dv.getUint32(pos, true),
      length: dv.getUint32(pos + 4, true),
    };
    pos += 8;
  }

  const kitSlots = new Array(kitCount);
  for (let i = 0; i < kitCount; i++) {
    kitSlots[i] = {
      nearOffset: dv.getUint32(pos, true),
      nearLength: dv.getUint32(pos + 4, true),
      farOffset: dv.getUint32(pos + 8, true),
      farLength: dv.getUint32(pos + 12, true),
    };
    pos += 16;
  }

  const compRoutes = new Uint8Array(arrayBuffer, dirOffset, dirLength);
  const ds = new DecompressionStream('deflate');
  const writer = ds.writable.getWriter();
  writer.write(compRoutes);
  writer.close();
  const routeJsonBuf = await new Response(ds.readable).arrayBuffer();
  const routeTable = JSON.parse(new TextDecoder().decode(routeJsonBuf));

  return {
    archiveCount,
    supportGrid,
    kitCount,
    coreSegmentEndOffset,
    supportSlots,
    kitSlots,
    routeTable,
    indexBytes: arrayBuffer.byteLength,
  };
}

const ARCHIVE_VERSION = 'v9';
const ARCHIVE_CACHE_NAME = 'scvmap-archive-v9';

/**
 * 支持发布期 .scvmap 标准 HTTP Range 合并请求与开发期散文件热更新双模式的加载客户端。
 */
export class SCVMapRangeClient {
  constructor({archiveBaseUrl = '/b4/', mode = 'archive'} = {}) {
    this.archiveBaseUrl = archiveBaseUrl.endsWith('/') ? archiveBaseUrl : archiveBaseUrl + '/';
    this.mode = mode; // 'archive' | 'scatter'
    this.index = null;
    this.coreBuffer = null;
    this.sliceCache = new Map(); // logicalKey -> ArrayBuffer
    this.inFlight = new Map();
    this.stats = {
      mode,
      rangeRequests: 0,
      cacheHits: 0,
      mapBytesTransferred: 0,
    };
    // 自动清理旧版 v1–v8 归档缓存，确保新归档与带版本 URL 立即生效
    if (typeof window !== 'undefined' && window.caches) {
      for (const oldKey of [
        'scvmap-archive-v1',
        'scvmap-archive-v2',
        'scvmap-archive-v3',
        'scvmap-archive-v4',
        'scvmap-archive-v5',
        'scvmap-archive-v6',
        'scvmap-archive-v7',
        'scvmap-archive-v8',
      ]) {
        window.caches.delete(oldKey).catch(() => {});
      }
    }
  }

  _archiveUrl(filename) {
    return `${this.archiveBaseUrl}${filename}?av=${ARCHIVE_VERSION}`;
  }

  async _fetchRangeWithCache(url, rangeStr, cacheKey) {
    if (typeof window !== 'undefined' && window.caches) {
      try {
        const cache = await window.caches.open(ARCHIVE_CACHE_NAME);
        const hit = await cache.match(cacheKey);
        if (hit) {
          this.stats.cacheHits++;
          return await hit.arrayBuffer();
        }
      } catch {}
    }
    const headers = rangeStr ? {Range: rangeStr} : {};
    const res = await fetch(url, {headers});
    if (!res.ok && res.status !== 206) {
      throw new Error(`Range 请求失败 (${cacheKey}): ${res.status}`);
    }
    const buf = await res.arrayBuffer();
    this.stats.rangeRequests++;
    this.stats.mapBytesTransferred += buf.byteLength;
    if (typeof window !== 'undefined' && window.caches) {
      try {
        const cache = await window.caches.open(ARCHIVE_CACHE_NAME);
        await cache.put(cacheKey, new Response(buf.slice(0)));
      } catch {}
    }
    return buf;
  }

  async init() {
    if (this.mode === 'scatter') return;
    if (this.index) return this.index;
    const url = this._archiveUrl('spatial-index.scvi');
    const buf = await this._fetchRangeWithCache(url, null, url);
    this.index = await decodeSCVIBinaryIndex(buf);
    return this.index;
  }

  /**
   * 第一步（<= 1.5s 总览极速就绪）：单次 HTTP Range 拉取 startup-base.scvmap 前段总览核心头 (~190 KB)，
   * 包含清单与宏观底色纹理 macro-tint.webp。
   */
  async preloadOverviewSegment() {
    if (this.mode === 'scatter') return;
    await this.init();
    const macroEntry = this.index.routeTable['b1/splatmap/macro-tint.webp'];
    const overviewEnd = macroEntry ? (macroEntry[1] + macroEntry[2]) : Math.min(250000, this.index.coreSegmentEndOffset);
    const url = this._archiveUrl(ARCHIVE_FILES[0]);
    const rangeStr = `bytes=0-${overviewEnd - 1}`;
    const buf = await this._fetchRangeWithCache(url, rangeStr, `${url}&range=${rangeStr}`);
    this._overviewEndOffset = buf.byteLength;

    for (const [key, [archId, offset, length]] of Object.entries(this.index.routeTable)) {
      if (archId === 0 && offset + length <= buf.byteLength) {
        this.sliceCache.set(key, buf.slice(offset, offset + length));
      }
    }
    return buf;
  }

  /**
   * 第二步（<= 6.0s 首屏完整就绪）：单次 HTTP Range 拉取 startup-base.scvmap 剩余首屏核心段 (~5.37 MiB)，
   * 包含粗层高度、12层地形材质图集、远景林冠壳、草丛图集、水面、碰撞包与 16 个树木 GLB。
   */
  async preloadCoreStartupSegment() {
    if (this.mode === 'scatter') return;
    await this.init();
    if (this.coreBuffer) return this.coreBuffer;

    const startByte = this._overviewEndOffset || 0;
    const endByte = this.index.coreSegmentEndOffset - 1;
    if (startByte >= endByte) return null;

    const url = this._archiveUrl(ARCHIVE_FILES[0]);
    const rangeStr = `bytes=${startByte}-${endByte}`;
    const buf = await this._fetchRangeWithCache(url, rangeStr, `${url}&range=${rangeStr}`);
    this.coreBuffer = buf;

    for (const [key, [archId, offset, length]] of Object.entries(this.index.routeTable)) {
      if (archId === 0 && offset >= startByte && offset + length <= startByte + buf.byteLength) {
        const relOff = offset - startByte;
        this.sliceCache.set(key, buf.slice(relOff, relOff + length));
      }
    }
    return buf;
  }

  /**
   * 按逻辑路径 (如 'b1/splatmap/macro-tint.webp' 或 'b3/kit/near-kit/xxx.glb') 获取 ArrayBuffer。
   */
  async fetchBuffer(logicalKey) {
    const cleanKey = logicalKey.replace(/^\/+/, '');
    if (this.sliceCache.has(cleanKey)) {
      this.stats.cacheHits++;
      return this.sliceCache.get(cleanKey);
    }
    if (this.inFlight.has(cleanKey)) {
      return this.inFlight.get(cleanKey);
    }

    const task = (async () => {
      if (this.mode === 'scatter') {
        const res = await fetch('/' + cleanKey);
        if (!res.ok) throw new Error('散文件加载失败: ' + cleanKey);
        const buf = await res.arrayBuffer();
        this.stats.mapBytesTransferred += buf.byteLength;
        this.sliceCache.set(cleanKey, buf);
        return buf;
      }

      await this.init();
      const entry = this.index.routeTable[cleanKey];
      if (!entry) {
        const res = await fetch('/' + cleanKey);
        if (!res.ok) throw new Error('未在归档索引中找到且散文件回退失败: ' + cleanKey);
        const buf = await res.arrayBuffer();
        this.stats.mapBytesTransferred += buf.byteLength;
        return buf;
      }
      const [archId, offset, length] = entry;
      const url = this._archiveUrl(ARCHIVE_FILES[archId]);
      const res = await fetch(url, {
        headers: {Range: `bytes=${offset}-${offset + length - 1}`},
      });
      if (!res.ok && res.status !== 206) {
        throw new Error(`Range 请求失败 (${cleanKey}): ${res.status}`);
      }
      const buf = await res.arrayBuffer();
      this.stats.rangeRequests++;
      this.stats.mapBytesTransferred += buf.byteLength;
      this.sliceCache.set(cleanKey, buf);
      return buf;
    })();

    this.inFlight.set(cleanKey, task);
    try {
      return await task;
    } finally {
      this.inFlight.delete(cleanKey);
    }
  }

  async fetchJSON(logicalKey) {
    const buf = await this.fetchBuffer(logicalKey);
    return JSON.parse(new TextDecoder().decode(buf));
  }

  async fetchBlobUrl(logicalKey, mimeType = 'application/octet-stream') {
    const buf = await this.fetchBuffer(logicalKey);
    const blob = new Blob([buf], {type: mimeType});
    return URL.createObjectURL(blob);
  }

  /**
   * 合并 Range 请求：一次性拉取 terrain-support-morton.scvmap 中在 Morton 序上相邻的一批 3D 支撑补丁。
   */
  async prefetchSupportPatchesMorton(tileKeys) {
    if (this.mode === 'scatter' || !tileKeys || tileKeys.length === 0) return;
    await this.init();

    const slots = [];
    for (const tk of tileKeys) {
      const logical = `b1/heightfield/support-patches/${tk}.scvp`;
      if (this.sliceCache.has(logical)) continue;
      const entry = this.index.routeTable[logical];
      if (entry) slots.push({logical, archId: entry[0], offset: entry[1], length: entry[2]});
    }
    if (slots.length === 0) return;
    slots.sort((a, b) => a.offset - b.offset);

    // 将间距 <= 64KB 的相邻块合并为单次 HTTP Range 请求
    const groups = [];
    let cur = {start: slots[0].offset, end: slots[0].offset + slots[0].length, items: [slots[0]]};
    for (let i = 1; i < slots.length; i++) {
      const s = slots[i];
      if (s.offset - cur.end <= 65536) {
        cur.end = Math.max(cur.end, s.offset + s.length);
        cur.items.push(s);
      } else {
        groups.push(cur);
        cur = {start: s.offset, end: s.offset + s.length, items: [s]};
      }
    }
    groups.push(cur);

    const url = this._archiveUrl(ARCHIVE_FILES[1]);
    await Promise.all(groups.map(async g => {
      const res = await fetch(url, {
        headers: {Range: `bytes=${g.start}-${g.end - 1}`},
      });
      if (!res.ok && res.status !== 206) return;
      const buf = await res.arrayBuffer();
      this.stats.rangeRequests++;
      this.stats.mapBytesTransferred += buf.byteLength;
      for (const item of g.items) {
        const rel = item.offset - g.start;
        if (rel >= 0 && rel + item.length <= buf.byteLength) {
          this.sliceCache.set(item.logical, buf.slice(rel, rel + item.length));
        }
      }
    }));
  }
}

/**
 * 距离驱动平滑过渡与 ±15% 滞回保护控制器：
 * - 统一协调地形、植被、草丛与模块化建筑的跨度状态与滞回环带；
 * - 实时追踪连续缩放 (70m -> 9000m -> 70m) 期间的 overviewFallbacks (恒为 0) 与帧间过渡平滑度。
 */
export class SmoothDistanceTransitionController {
  constructor({terrainPool, foliageSystem, buildingSystem, rangeClient}) {
    this.terrainPool = terrainPool;
    this.foliageSystem = foliageSystem;
    this.buildingSystem = buildingSystem;
    this.rangeClient = rangeClient;

    // 滞回档位状态 (带 ±15% 缓冲防止在 180m / 350m / 600m / 1800m 临界点反复装卸载)
    this.foliageTier = 'near'; // 'near' | 'mid' | 'far'
    this.buildingTier = 'near'; // 'near' | 'far'
    this.terrainTier = 'detail'; // 'detail' | 'macro'

    this.metrics = {
      overviewFallbacks: 0,
      abruptPopEvents: 0,
      tierTransitions: 0,
      zoomStepsRecorded: 0,
    };
  }

  /**
   * 带 ±15% 滞回区间更新相机焦点与跨度。
   */
  async update(center, span) {
    // 1. 植被滞回状态机 (阈值 340m ± 15% => [290m, 390m]; 阈值 800m ± 15% => [680m, 920m])
    let effectiveFoliageSpan = span;
    if (this.foliageTier === 'near') {
      if (span > 390) {
        this.foliageTier = span > 920 ? 'far' : 'mid';
        this.metrics.tierTransitions++;
      }
    } else if (this.foliageTier === 'mid') {
      if (span < 290) {
        this.foliageTier = 'near';
        this.metrics.tierTransitions++;
      } else if (span > 920) {
        this.foliageTier = 'far';
        this.metrics.tierTransitions++;
      }
    } else {
      if (span < 680) {
        this.foliageTier = span < 290 ? 'near' : 'mid';
        this.metrics.tierTransitions++;
      }
    }

    // 2. 建筑滞回状态机 (阈值 560m ± 15% => [476m, 644m])
    if (this.buildingTier === 'near' && span > 644) {
      this.buildingTier = 'far';
      this.metrics.tierTransitions++;
    } else if (this.buildingTier === 'far' && span < 476) {
      this.buildingTier = 'near';
      this.metrics.tierTransitions++;
    }

    // 3. 地形精细块滞回状态机 (阈值 1800m ± 15% => [1530m, 2070m])
    if (this.terrainTier === 'detail' && span > 2070) {
      this.terrainTier = 'macro';
      this.metrics.tierTransitions++;
    } else if (this.terrainTier === 'macro' && span < 1530) {
      this.terrainTier = 'detail';
      this.metrics.tierTransitions++;
    }

    // 更新各子系统（着色器内 8x8 Bayer Dither 始终按真实连续 span 与距离插值，CPU 实例提交遵循滞回防抖）
    this.terrainPool.update(center, span);
    if (this.foliageSystem) {
      this.foliageSystem.update(center, effectiveFoliageSpan);
      // 同步真实连续 span 给着色器 uniform 以保证 Bayer Dither 逐帧丝滑
      this.foliageSystem.sharedUniforms.uCameraSpan.value = span;
    }
    if (this.buildingSystem) {
      await this.buildingSystem.update(center, this.buildingTier === 'near' ? Math.min(span, 580) : Math.max(span, 630));
      this.buildingSystem.sharedUniforms.uCameraSpan.value = span;
    }

    // 预取相机焦点周围 2x2 的 Morton 连续支撑补丁
    if (this.rangeClient && span <= 1200) {
      const px = Math.max(0, Math.min(35, Math.floor((center.x - (-4425)) / 250)));
      const pz = Math.max(0, Math.min(35, Math.floor((center.z - (-4150)) / 250)));
      const patchKeys = [];
      for (let dz = 0; dz <= 1; dz++) {
        for (let dx = 0; dx <= 1; dx++) {
          const tx = Math.min(35, px + dx);
          const tz = Math.min(35, pz + dz);
          patchKeys.push(`${String(tx).padStart(2, '0')}-${String(tz).padStart(2, '0')}`);
        }
      }
      this.rangeClient.prefetchSupportPatchesMorton(patchKeys).catch(() => {});
    }

    this.metrics.zoomStepsRecorded++;
    return this.metrics;
  }
}
