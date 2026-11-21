/**
 * 程序化载具统一流式加载器（单源三档 LOD + 去重共享 KTX2 纹理池 + HTTP Range 单包/散文件双模式）。
 * - 解析定长二进制索引 vehicles-index.scvi (SCVI v1) 或开发期 JSON 汇总；
 * - 维护全局 KTX2 纹理缓存（同一共享贴图跨 129 车、跨 LOD0/LOD1 在 GPU 显存中仅上传 1 次）；
 * - 支持全库 129 车 LOD2 远景底座一次性常驻加载、单车 LOD0/LOD1 按需 Range 拉取与 LRU 释放。
 */
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {KTX2Loader} from 'three/addons/loaders/KTX2Loader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';

export class ProceduralVehicleLoader {
  constructor(renderer, options = {}) {
    this.renderer = renderer;
    this.mode = options.mode || 'loose'; // 'loose' | 'archive'
    this.baseUrl = (options.baseUrl || '/v2').replace(/\/$/, '');
    this.archiveBaseUrl = (options.archiveBaseUrl || '/v3').replace(/\/$/, '');
    this.maxConcurrentDownloads = options.maxConcurrentDownloads || 6;
    this.maxCachedDetailVehicles = options.maxCachedDetailVehicles || 64;

    this.ktx2Loader = new KTX2Loader()
      .setTranscoderPath(options.basisPath || '/vendor/examples/jsm/libs/basis/')
      .setWorkerLimit(2)
      .detectSupport(renderer);

    this.texPromiseCache = new Map();
    this.texResolved = new Map();

    this.lodCache = new Map();
    this.lodResolved = new Map();
    this.lruOrder = [];

    this.index = null;
    this.farArchiveBuffer = null;
    this._indexPromise = null;
    this._farPromise = null;
    this._cachePromise =
      typeof caches !== 'undefined'
        ? Promise.all([
            caches.delete('scv-veh-archive-v001').catch(() => {}),
            caches.delete('scv-veh-archive-v001-r2').catch(() => {}),
          ])
            .then(() => caches.open('scv-veh-archive-v001-r3'))
            .catch(() => null)
        : Promise.resolve(null);
    this._pendingBatches = new Map();
    this._batchTimer = null;
    this.inflight = 0;
    this.queue = [];
    this.stats = {
      rangeRequests: 0,
      bytesFetched: 0,
      uniqueTexturesLoaded: 0,
      cacheHits: 0,
    };

    // 归档模式下提前并行预拉索引与全库 129 车远景底座，消除首屏串行 RTT 瀑布
    if (this.mode === 'archive') {
      this._indexPromise = this._fetchBootFile('vehicles-index.scvi', `${this.archiveBaseUrl}/vehicles-index.scvi`);
      this._farPromise = this._fetchBootFile('vehicles-far.scvveh', `${this.archiveBaseUrl}/vehicles-far.scvveh`);
    }
  }

  async _cacheGet(key) {
    try {
      const c = await this._cachePromise;
      if (!c) return null;
      const hit = await c.match(`/__scv_veh_cache__/${key}`);
      if (!hit) return null;
      this.stats.cacheHits += 1;
      return await hit.arrayBuffer();
    } catch {
      return null;
    }
  }

  _cachePut(key, ab) {
    this._cachePromise
      .then(c => {
        if (!c) return;
        const copy = ab.slice(0);
        return c.put(`/__scv_veh_cache__/${key}`, new Response(copy, {headers: {'Content-Type': 'application/octet-stream'}}));
      })
      .catch(() => {});
  }

  async _fetchBootFile(cacheKey, url) {
    const cached = await this._cacheGet(cacheKey);
    if (cached) return cached;
    const resp = await fetch(url, {cache: 'no-store'});
    if (!resp.ok) throw new Error(`加载归档引导文件失败 ${url}: HTTP ${resp.status}`);
    const ab = await resp.arrayBuffer();
    this.stats.bytesFetched += ab.byteLength;
    this._cachePut(cacheKey, ab);
    return ab;
  }

  async _enqueue(fn) {
    if (this.inflight >= this.maxConcurrentDownloads) {
      await new Promise(resolve => this.queue.push(resolve));
    }
    this.inflight += 1;
    try {
      return await fn();
    } finally {
      this.inflight -= 1;
      const next = this.queue.shift();
      if (next) next();
    }
  }

  async loadIndex() {
    if (this.index) return this.index;
    if (this.mode === 'archive') {
      const buf = await (this._indexPromise || this._fetchBootFile('vehicles-index.scvi', `${this.archiveBaseUrl}/vehicles-index.scvi`));
      this.index = await this._parseScvi(buf);
      return this.index;
    }
    const resp = await fetch(`${this.baseUrl}/编译汇总.json`);
    if (!resp.ok) throw new Error(`加载编译汇总失败: HTTP ${resp.status}`);
    const summary = await resp.json();
    const vehiclesById = new Map();
    const vehiclesBySlug = new Map();
    const vehicles = summary.vehicles.map((v, i) => {
      const rec = {...v, index: i};
      vehiclesById.set(rec.id, rec);
      vehiclesBySlug.set(rec.slug, rec);
      return rec;
    });
    this.index = {
      mode: 'loose',
      vehicles,
      vehiclesById,
      vehiclesBySlug,
      entriesByKey: new Map(),
      archives: [],
    };
    return this.index;
  }

  async _parseScvi(buf) {
    const dv = new DataView(buf);
    const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
    if (magic !== 'SCVI') throw new Error(`无效二进制索引魔数: ${magic}`);
    const version = dv.getUint32(4, true);
    const vehicleCount = dv.getUint32(8, true);
    const entryCount = dv.getUint32(12, true);
    const jsonByteLen = dv.getUint32(16, true);
    const flags = dv.getUint32(20, true);
    const tableOff = 24;
    const jsonOff = tableOff + entryCount * 24;
    const rawSlice = new Uint8Array(buf, jsonOff, jsonByteLen);
    let jsonText;
    if ((flags & 1) !== 0) {
      const ds = new DecompressionStream('deflate');
      const stream = new Blob([rawSlice]).stream().pipeThrough(ds);
      jsonText = await new Response(stream).text();
    } else {
      jsonText = new TextDecoder().decode(rawSlice);
    }
    const metaJson = JSON.parse(jsonText);

    const vehiclesById = new Map();
    const vehiclesBySlug = new Map();
    const entriesByKey = new Map();

    for (let i = 0; i < metaJson.entries.length; i++) {
      const eOff = tableOff + i * 24;
      const archiveId = dv.getUint32(eOff + 4, true);
      const offset = dv.getUint32(eOff + 8, true);
      const length = dv.getUint32(eOff + 12, true);
      const kind = dv.getUint32(eOff + 16, true);
      const vehIndex = dv.getUint32(eOff + 20, true);
      const key = metaJson.entries[i];
      entriesByKey.set(key, {key, archiveId, offset, length, kind, vehIndex});
    }

    for (const v of metaJson.vehicles) {
      vehiclesById.set(v.id, v);
      vehiclesBySlug.set(v.slug, v);
    }
    return {
      mode: 'archive',
      version,
      vehicleCount,
      entryCount,
      archives: metaJson.archives,
      vehicles: metaJson.vehicles,
      vehiclesById,
      vehiclesBySlug,
      entriesByKey,
    };
  }

  async ensureFarBase() {
    if (this.mode !== 'archive') return true;
    if (this.farArchiveBuffer) return true;
    const [idx, farBuf] = await Promise.all([
      this.loadIndex(),
      this._farPromise || this._fetchBootFile('vehicles-far.scvveh', `${this.archiveBaseUrl}/vehicles-far.scvveh`),
    ]);
    if (!idx) return false;
    this.farArchiveBuffer = farBuf;
    return true;
  }

  async _maybeDecompress(ab, kind) {
    if ((kind & 0x80) === 0) return ab;
    const ds = new DecompressionStream('deflate');
    const stream = new Blob([ab]).stream().pipeThrough(ds);
    return await new Response(stream).arrayBuffer();
  }

  _queueCoalescedRange(arc, rec) {
    return new Promise((resolve, reject) => {
      let list = this._pendingBatches.get(arc.id);
      if (!list) {
        list = [];
        this._pendingBatches.set(arc.id, list);
      }
      list.push({arc, rec, resolve, reject});
      if (!this._batchTimer) {
        this._batchTimer = setTimeout(() => {
          this._batchTimer = null;
          this._flushCoalescedRanges();
        }, 4);
      }
    });
  }

  _flushCoalescedRanges() {
    const batches = [...this._pendingBatches.values()];
    this._pendingBatches.clear();
    const MAX_GAP = 96 * 1024;
    const MAX_SPAN = 1400 * 1024;

    for (const items of batches) {
      items.sort((a, b) => a.rec.offset - b.rec.offset);
      const groups = [];
      let cur = null;
      for (const it of items) {
        const s = it.rec.offset;
        const e = s + it.rec.length;
        if (!cur) {
          cur = {arc: it.arc, start: s, end: e, items: [it]};
          groups.push(cur);
        } else if (s - cur.end <= MAX_GAP && Math.max(cur.end, e) - cur.start <= MAX_SPAN) {
          cur.end = Math.max(cur.end, e);
          cur.items.push(it);
        } else {
          cur = {arc: it.arc, start: s, end: e, items: [it]};
          groups.push(cur);
        }
      }

      for (const g of groups) {
        this._enqueue(async () => {
          const endByte = g.end - 1;
          this.stats.rangeRequests += 1;
          const resp = await fetch(`${this.archiveBaseUrl}/${g.arc.file}`, {
            headers: {Range: `bytes=${g.start}-${endByte}`},
            cache: 'no-store',
          });
          if (resp.status !== 206 && resp.status !== 200) {
            throw new Error(`HTTP Range 批量拉取失败 ${g.arc.file}: ${resp.status}`);
          }
          const ab = await resp.arrayBuffer();
          this.stats.bytesFetched += ab.byteLength;
          const baseOff = resp.status === 200 ? 0 : g.start;
          for (const it of g.items) {
            const relStart = it.rec.offset - baseOff;
            const slice = ab.slice(relStart, relStart + it.rec.length);
            this._cachePut(it.rec.key, slice);
            it.resolve(slice);
          }
        }).catch(err => {
          for (const it of g.items) it.reject(err);
        });
      }
    }
  }

  async _fetchEntryBytes(entryKey, fallbackLooseUrl) {
    const idx = await this.loadIndex();
    if (this.mode === 'archive') {
      const rec = idx.entriesByKey.get(entryKey);
      if (!rec) throw new Error(`归档索引缺少条目: ${entryKey}`);
      if (rec.archiveId === 0 && this.farArchiveBuffer) {
        const sliced = this.farArchiveBuffer.slice(rec.offset, rec.offset + rec.length);
        return await this._maybeDecompress(sliced, rec.kind);
      }
      const cached = await this._cacheGet(entryKey);
      if (cached) {
        return await this._maybeDecompress(cached, rec.kind);
      }
      const arc = idx.archives.find(a => a.id === rec.archiveId);
      if (!arc) throw new Error(`未知归档号: ${rec.archiveId}`);
      const raw = await this._queueCoalescedRange(arc, rec);
      return await this._maybeDecompress(raw, rec.kind);
    }
    return this._enqueue(async () => {
      const resp = await fetch(fallbackLooseUrl);
      if (!resp.ok) throw new Error(`散文件拉取失败 ${fallbackLooseUrl}: ${resp.status}`);
      const ab = await resp.arrayBuffer();
      this.stats.bytesFetched += ab.byteLength;
      return ab;
    });
  }

  async loadTextureByKey(texKey, isLinear = false) {
    if (this.texPromiseCache.has(texKey)) {
      this.stats.cacheHits += 1;
      return this.texPromiseCache.get(texKey);
    }
    const p = (async () => {
      const entryKey = `tex/${texKey}.ktx2`;
      const bytes = await this._fetchEntryBytes(entryKey, `${this.baseUrl}/tex/${texKey}.ktx2`);
      const blob = new Blob([bytes], {type: 'image/ktx2'});
      const objUrl = URL.createObjectURL(blob);
      try {
        const texture = await new Promise((resolve, reject) => {
          this.ktx2Loader.load(objUrl, resolve, undefined, reject);
        });
        texture.colorSpace = isLinear ? THREE.NoColorSpace : THREE.SRGBColorSpace;
        texture.wrapS = THREE.RepeatWrapping;
        texture.wrapT = THREE.RepeatWrapping;
        texture.flipY = false;
        texture.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
        texture.needsUpdate = true;
        this.texResolved.set(texKey, texture);
        this.stats.uniqueTexturesLoaded = this.texResolved.size;
        return texture;
      } finally {
        URL.revokeObjectURL(objUrl);
      }
    })();
    this.texPromiseCache.set(texKey, p);
    return p;
  }

  async loadVehicleMeta(slug) {
    const idx = await this.loadIndex();
    const rec = idx.vehiclesBySlug.get(slug);
    if (this.mode === 'archive' && rec) return rec;
    if (rec && rec._fullMeta) return rec._fullMeta;
    const resp = await fetch(`${this.baseUrl}/meta/${slug}.json`);
    if (!resp.ok) throw new Error(`加载载具元数据失败: ${slug}`);
    const full = await resp.json();
    if (rec) rec._fullMeta = full;
    return full;
  }

  async loadVehicleLod(slug, lodLevel = 0) {
    const cacheKey = `${slug}:lod${lodLevel}`;
    if (this.lodCache.has(cacheKey)) {
      this.stats.cacheHits += 1;
      this._touchLru(cacheKey);
      return this.lodCache.get(cacheKey);
    }

    const p = (async () => {
      const entryKey = `models/${slug}/lod${lodLevel}.glb`;
      const glbBuf = await this._fetchEntryBytes(entryKey, `${this.baseUrl}/models/${slug}/lod${lodLevel}.glb`);
      const {cleanBuffer, externalTextures} = this._extractAndStripExternalTextures(glbBuf);

      const loadedTexMap = new Map();
      if (lodLevel < 2 && externalTextures.length > 0) {
        const idx = await this.loadIndex();
        const neededTex = lodLevel === 1 ? externalTextures.filter(t => t.slot === 'baseColor') : externalTextures;
        await Promise.all(
          neededTex.map(async tInfo => {
            let fetchKey = tInfo.key;
            // 中景战场 LOD1 优先拉取轻量 256px 同源 Mip 底色（每张 ~12KB）；若 1024 高清底色已在内存缓存中则直接零开销复用，避免展示台切换 LOD1 变糊跳变
            if (lodLevel === 1 && !tInfo.linear && this.mode === 'archive' && !this.texResolved.has(tInfo.key)) {
              const mipKey = `${tInfo.key}_m256`;
              if (idx.entriesByKey.has(`tex/${mipKey}.ktx2`)) {
                fetchKey = mipKey;
              }
            }
            const tex = await this.loadTextureByKey(fetchKey, tInfo.linear);
            loadedTexMap.set(tInfo.matIndex + ':' + tInfo.slot, tex);
          }),
        );
      }

      const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
      const gltf = await new Promise((resolve, reject) => {
        loader.parse(cleanBuffer, '', resolve, reject);
      });

      if (lodLevel < 2 && loadedTexMap.size > 0) {
        const matList = [];
        const seenMat = new Set();
        gltf.scene.traverse(obj => {
          if (!obj.isMesh) return;
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          for (const m of mats) {
            if (m && !seenMat.has(m)) {
              seenMat.add(m);
              matList.push(m);
            }
          }
        });
        for (const tInfo of externalTextures) {
          const mat = matList.find(m => m.name === tInfo.matName) || matList[tInfo.matIndex];
          const tex = loadedTexMap.get(tInfo.matIndex + ':' + tInfo.slot);
          if (mat && tex) {
            if (tInfo.slot === 'baseColor') mat.map = tex;
            if (tInfo.slot === 'normal') {
              mat.normalMap = tex;
              mat.normalScale = new THREE.Vector2(1.0, 1.0);
            }
            mat.needsUpdate = true;
          }
        }
      }

      if (lodLevel === 2) {
        gltf.scene.traverse(obj => {
          if (obj.isMesh && obj.geometry.getAttribute('color')) {
            obj.material.vertexColors = true;
            obj.material.needsUpdate = true;
          }
        });
      }

      const slots = {};
      for (const ch of gltf.scene.children) {
        slots[ch.name] = ch;
      }

      const result = {
        slug,
        lodLevel,
        scene: gltf.scene,
        slots,
        bytes: glbBuf.byteLength,
      };
      this.lodResolved.set(cacheKey, result);
      this._touchLru(cacheKey);
      this._evictIfNeeded();
      return result;
    })();

    this.lodCache.set(cacheKey, p);
    return p;
  }

  _extractAndStripExternalTextures(glbBuf) {
    const u8 = new Uint8Array(glbBuf);
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const jlen = dv.getUint32(12, true);
    const gltf = JSON.parse(new TextDecoder().decode(u8.subarray(20, 20 + jlen)));
    const binTail = u8.subarray(20 + jlen);

    const externalTextures = [];
    const images = gltf.images || [];
    const textures = gltf.textures || [];

    const resolveTexKey = texIdx => {
      const t = textures[texIdx];
      if (!t) return null;
      const srcIdx = t.source ?? t.extensions?.KHR_texture_basisu?.source;
      const im = images[srcIdx];
      if (!im || !im.uri) return null;
      const m = im.uri.match(/tex\/([^/]+)\.ktx2$/);
      return m ? m[1] : im.name;
    };

    const mats = gltf.materials || [];
    for (let mi = 0; mi < mats.length; mi++) {
      const m = mats[mi];
      const bcRef = m.pbrMetallicRoughness?.baseColorTexture;
      if (bcRef && bcRef.index !== undefined) {
        const key = resolveTexKey(bcRef.index);
        if (key) externalTextures.push({matIndex: mi, matName: m.name, slot: 'baseColor', key, linear: false});
        delete m.pbrMetallicRoughness.baseColorTexture;
      }
      if (m.normalTexture && m.normalTexture.index !== undefined) {
        const key = resolveTexKey(m.normalTexture.index);
        if (key) externalTextures.push({matIndex: mi, matName: m.name, slot: 'normal', key, linear: true});
        delete m.normalTexture;
      }
    }

    if (externalTextures.length === 0) {
      return {cleanBuffer: glbBuf, externalTextures};
    }

    delete gltf.images;
    delete gltf.textures;
    if (Array.isArray(gltf.extensionsUsed)) {
      gltf.extensionsUsed = gltf.extensionsUsed.filter(e => e !== 'KHR_texture_basisu');
    }
    if (Array.isArray(gltf.extensionsRequired)) {
      gltf.extensionsRequired = gltf.extensionsRequired.filter(e => e !== 'KHR_texture_basisu');
    }

    const jsonBytes = new TextEncoder().encode(JSON.stringify(gltf));
    const pad = (4 - (jsonBytes.length % 4)) % 4;
    const total = 20 + jsonBytes.length + pad + binTail.length;
    const out = new Uint8Array(total);
    const odv = new DataView(out.buffer);
    out.set([0x67, 0x6c, 0x54, 0x46], 0);
    odv.setUint32(4, 2, true);
    odv.setUint32(8, total, true);
    odv.setUint32(12, jsonBytes.length + pad, true);
    odv.setUint32(16, 0x4e4f534a, true);
    out.set(jsonBytes, 20);
    for (let i = 0; i < pad; i++) out[20 + jsonBytes.length + i] = 0x20;
    out.set(binTail, 20 + jsonBytes.length + pad);
    return {cleanBuffer: out.buffer, externalTextures};
  }

  _touchLru(key) {
    if (key.endsWith(':lod2')) return;
    const idx = this.lruOrder.indexOf(key);
    if (idx >= 0) this.lruOrder.splice(idx, 1);
    this.lruOrder.push(key);
  }

  _evictIfNeeded() {
    while (this.lruOrder.length > this.maxCachedDetailVehicles) {
      const victim = this.lruOrder.shift();
      const parsed = this.lodResolved.get(victim);
      if (parsed) {
        this._disposeSceneOnlyGeometryAndMaterials(parsed.scene);
        this.lodResolved.delete(victim);
      }
      this.lodCache.delete(victim);
    }
  }

  _disposeSceneOnlyGeometryAndMaterials(scene) {
    scene.traverse(obj => {
      if (!obj.isMesh) return;
      obj.geometry?.dispose();
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of mats) m?.dispose();
    });
  }

  clearDetailCache(releaseTextures = true) {
    for (const key of [...this.lruOrder]) {
      const parsed = this.lodResolved.get(key);
      if (parsed) {
        this._disposeSceneOnlyGeometryAndMaterials(parsed.scene);
        this.lodResolved.delete(key);
      }
      this.lodCache.delete(key);
    }
    this.lruOrder.length = 0;
    if (releaseTextures) {
      for (const tex of this.texResolved.values()) {
        tex.dispose();
      }
      this.texResolved.clear();
      this.texPromiseCache.clear();
      this.stats.uniqueTexturesLoaded = 0;
    }
  }

  dispose() {
    for (const parsed of this.lodResolved.values()) {
      this._disposeSceneOnlyGeometryAndMaterials(parsed.scene);
    }
    this.lodResolved.clear();
    this.lodCache.clear();
    this.lruOrder.length = 0;
    for (const tex of this.texResolved.values()) {
      tex.dispose();
    }
    this.texResolved.clear();
    this.texPromiseCache.clear();
    this.ktx2Loader.dispose();
    this.farArchiveBuffer = null;
  }
}
