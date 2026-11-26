import * as THREE from 'three';
import { createSilhouetteSvg, GroundNameplateCollision } from '/ui/图标与标绘.mjs';
import { ProceduralVehicleLoader } from '/src/程序化载具/载具流式加载器.mjs';
import {
  buildVehicleRigHierarchy,
  stepVehicleAnimations,
} from '/src/程序化地图/载具动效控制.mjs';
import { FogOfWarEngine } from '/src/程序化地图/战争迷雾引擎.mjs';
import {
  DEFAULT_ROSTER_SLUGS,
  MAX_HERO_UNITS,
  DEPLOY_CATEGORY_QUOTAS,
  DEPLOY_CATEGORY_LABELS,
  normalizeDeployCategoryKey,
  resolveDeployCategory,
  computeDeployQuotaSummary,
  formatVehicleModelDesignation,
  formatUnitDisplayNames,
  resolveVehicleArchetype,
  isUnitAllowedByChannel,
  wrapAngleRad,
  planFleetCommand,
  dispatchFleetCommand,
  cancelFleetUnits,
} from '/src/交互系统/契约与仲裁.mjs';

export const ROSTER_STORAGE_KEY = 'scavenger.roster.v1';
export {
  MAX_HERO_UNITS,
  DEPLOY_CATEGORY_QUOTAS,
  DEPLOY_CATEGORY_LABELS,
  normalizeDeployCategoryKey,
  resolveDeployCategory,
  computeDeployQuotaSummary,
};

/**
 * 读取 4B 武器库持久化的出击编队 slug 列表（最多 6 辆，默认 3 车）。
 */
export function readStoredRosterSlugs() {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(ROSTER_STORAGE_KEY) : null;
    if (!raw) return [...DEFAULT_ROSTER_SLUGS];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) {
      return parsed.filter(s => typeof s === 'string' && s.trim()).slice(0, 6);
    }
  } catch {
    // 隐私模式或无存储时回退默认核心 3 车
  }
  return [...DEFAULT_ROSTER_SLUGS];
}

/**
 * 从 513x513 BACH 粗层高度二进制构造快速启动高度场，使首屏在 1.5s 内无需等待 4.8MB 精细高度场即可立即呈现立体地形！
 */
export function buildCoarseHeightfieldFallback(bachBuffer, bounds) {
  const dv = new DataView(bachBuffer);
  // BACH 头部: 4s magic, u32 ver, u32 w(513), u32 h(513), f32 minX, f32 minZ, f32 stepX, f32 stepZ
  const cw = dv.getUint32(8, true) || 513;
  const ch = dv.getUint32(12, true) || 513;
  const floats = new Float32Array(bachBuffer, 32, cw * ch);

  const minH = bounds.minH ?? -40.0;
  const maxH = bounds.maxH ?? 120.0;
  const invRange = 65534.0 / (maxH - minH);

  // 直接采用 513x513 粗层网格 (~526 KB) 极速初始化首屏，避免分配 81MB 临时缓冲
  const qGrid = new Uint16Array(cw * ch);
  for (let i = 0; i < cw * ch; i++) {
    const hVal = floats[i];
    qGrid[i] = Number.isFinite(hVal)
      ? Math.max(0, Math.min(65534, Math.round((hVal - minH) * invRange)))
      : 16383;
  }

  return {
    width: cw,
    height: ch,
    minX: bounds.minX,
    minZ: bounds.minZ,
    maxX: bounds.maxX,
    maxZ: bounds.maxZ,
    stepX: (bounds.maxX - bounds.minX) / (cw - 1),
    stepZ: (bounds.maxZ - bounds.minZ) / (ch - 1),
    minH,
    maxH,
    qGrid,
    roleGrid: null,
    nyGrid: null,
    bytes: qGrid.byteLength,
  };
}

const FW_STATE_LABELS = Object.freeze({
  orbit: '盘旋',
  approach: '进场',
  sprint: '高速直飞',
  transit: '切入',
  align: '对准',
  strafe: '扫射',
  rocket: '齐射',
  bomb: '投弹',
  missile: '导弹',
  egress: '脱离',
  rtb: '低油返航',
  damaged: '战损',
  crashing: '失控坠毁',
  crashed: '撞地损毁',
  crash: '坠毁',
});

const DEFAULT_HERO_PRESETS = Object.freeze({
  US_M1A2: {
    id: 'US_M1A2_01',
    name: 'M1A2 SEP v3',
    shortName: 'M1A2 SEP v3',
    fullModelName: 'M1A2 SEP v3',
    url: '/vehicles/US_M1A2.glb',
    color: 0x8d8266,
    bonus: 12,
    weapons: ['120mm 高爆 28', 'M1028 霰弹 14'],
    weaponRangeM: 180,
  },
  US_AH64D: {
    id: 'US_AH64D_01',
    name: 'AH-64D',
    shortName: 'AH-64D',
    fullModelName: 'AH-64D Apache',
    url: '/vehicles/US_AH64D.glb',
    color: 0x4b5742,
    bonus: 0,
    weapons: ['30mm 链炮 1200', 'AGM-114 ×8'],
    weaponRangeM: 220,
  },
  US_A10: {
    id: 'US_A10C_01',
    name: 'A-10C',
    shortName: 'A-10C',
    fullModelName: 'A-10C Thunderbolt II',
    url: '/vehicles/US_A10.glb',
    color: 0x6c767d,
    bonus: 0,
    weapons: ['GAU-8 30mm 1150', 'JDAM ×4'],
    weaponRangeM: 320,
  },
});

/**
 * G01 兼容空地载具（支持 4B 武器库最多 6 槽 129 车流式进场）与 100 单位混编交战控制器：
 * - 支持地面单位 4m A* 寻路绕行 + 多段队列 + 终点朝向转向；
 * - 支持旋翼直升机 30m 前瞻平滑抬升越障 + 原地悬停；
 * - 支持固定翼高空安全盘旋 + 目标点附近自动转入闭合盘旋；
 * - 集成 GroundNameplateCollision 贴身名牌避让与 100 混编性能隔离。
 */
export class TacticalVehicleAndCombatSystem {
  constructor({ scene, camera, hostElement, support, physicsEngine, loader, renderer = null, vehicleEnvMap = null, renderResolution = null }) {
    this.scene = scene;
    this.camera = camera;
    this.hostElement = hostElement;
    this.support = support;
    this.physics = physicsEngine;
    this.loader = loader;
    this.renderer = renderer;
    this.vehicleEnvMap = vehicleEnvMap;
    this._renderResolution = {
      width: Math.max(1, Number(renderResolution?.width) || 1208),
      height: Math.max(1, Number(renderResolution?.height) || 680),
    };

    this.root = new THREE.Group();
    this.root.name = 'TacticalVehicleAndCombatRoot';
    this.scene.add(this.root);

    // 独立 1 DrawCall 固定翼贴地投影软影根组（与 this.root 同级，绝不挂载于单机 unit.node 下，天然隔离 Live PiP 与 VFX 6 池）
    this.airShadowRoot = new THREE.Group();
    this.airShadowRoot.name = 'FixedWingGroundShadowRoot';
    this.scene.add(this.airShadowRoot);
    this._sunDir = new THREE.Vector3(-0.85, 1.45, 0.55).normalize();
    this._airShadowMat4 = new THREE.Matrix4();
    this._airShadowPos = new THREE.Vector3();
    this._airShadowQuat = new THREE.Quaternion();
    this._airShadowScale = new THREE.Vector3();
    this._airShadowUp = new THREE.Vector3(0, 1, 0);
    this._airShadowAlphas = new Float32Array(16);
    this.airGroundShadowMesh = null;
    this._airShadowAlphaAttr = null;
    this._airShadowTexture = null;
    this._buildAirGroundShadowMesh();

    // 贴身名牌 DOM 容器（挂载在画布宿主内，严密对齐 16:9 / 21:9 居中框，位于标绘层上方）
    this.nameplateHost = document.createElement('div');
    this.nameplateHost.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:110;overflow:hidden;';
    (this.hostElement || document.body).appendChild(this.nameplateHost);

    this.heroUnits = [];
    this.selectedUnitId = 'US_M1A2_01';
    this.selectedIds = new Set(['US_M1A2_01']);
    this.hoveredUnitId = null;
    this.channelFilter = 'all';
    this.onSelectUnit = null;
    this.onLabelPointerDown = null;
    this.onLabelHover = null;

    this.runId = `scv-${Date.now().toString(36)}`;
    this.commandSerial = 0;
    this.lastCommandResult = null;
    this._catalogPromise = null;
    this._catalogBySlug = new Map();
    this._vehicleStreamLoader = null;

    // 100 混编负载与弹道特效池
    this.combatMode = 'g01_vehicles';
    this.squadUnits = [];
    this._friendlySquadUnits = [];
    this.projectiles = [];
    this._buildBatchCombatMeshes();

    // 热路径预分配数学对象与宿主尺寸缓存（清零每帧 clientWidth/Height 回流与临时 GC）
    this._projVec = new THREE.Vector3();
    this._tmpMat4 = new THREE.Matrix4();
    this._nameplateItems = [];
    this._isHostDomElement = Boolean(this.hostElement && typeof Element === 'function' && this.hostElement instanceof Element);
    this._hostW = Math.max(1, Number(this.hostElement?.clientWidth) || 1280);
    this._hostH = Math.max(1, Number(this.hostElement?.clientHeight) || 720);
    this._hostResizeObserver = null;
    this._onWindowResize = () => this.syncHostSize();
    if (this._isHostDomElement && typeof ResizeObserver === 'function') {
      this._hostResizeObserver = new ResizeObserver(entries => {
        const entry = entries[entries.length - 1];
        const cr = entry?.contentRect;
        if (cr && cr.width > 0 && cr.height > 0) {
          this._hostW = Math.max(1, Math.round(cr.width));
          this._hostH = Math.max(1, Math.round(cr.height));
        }
      });
      this._hostResizeObserver.observe(this.hostElement);
    }
    if (this._isHostDomElement && typeof window !== 'undefined') {
      window.addEventListener('resize', this._onWindowResize);
    }

    // 9km 战场战争迷雾权威数据层（fog-of-war-v1：256×256 10Hz 双通道 + 纯建筑 OBB 静态缓存）
    this.fogOfWar = new FogOfWarEngine({
      width: 256,
      height: 256,
      tickIntervalSec: 0.1,
      terrainSupport: this.support,
      enableStaticOcclusionCache: true,
      buildingOcclusionRasterizer: (outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1) => {
        if (!this.physics || typeof this.physics.rasterizeBuildingOcclusionRegion !== 'function') return;
        this.physics.rasterizeBuildingOcclusionRegion(outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1, 1.5);
      },
      occlusionQuery: (x0, z0, x1, z1) => {
        if (!this.physics) return false;
        if (typeof this.physics.isPointBlockedByBuildingOBB === 'function') {
          return Boolean(this.physics.isPointBlockedByBuildingOBB(x1, z1, 1.5));
        }
        if (typeof this.physics.isPointBlocked === 'function') {
          return Boolean(this.physics.isPointBlocked(x1, z1, 3.0));
        }
        return false;
      },
    });
    this._fogLastNowSec = 0;
  }

  syncHostSize() {
    if (!this.hostElement) return;
    const cw = Number(this.hostElement.clientWidth);
    const ch = Number(this.hostElement.clientHeight);
    if (cw > 0) this._hostW = Math.round(cw);
    if (ch > 0) this._hostH = Math.round(ch);
  }

  dispose() {
    if (this._hostResizeObserver) {
      this._hostResizeObserver.disconnect();
      this._hostResizeObserver = null;
    }
    if (this._isHostDomElement && typeof window !== 'undefined') {
      window.removeEventListener('resize', this._onWindowResize);
    }
  }

  _getActiveFogUnits() {
    return this.combatMode === 'g01_vehicles' ? this.heroUnits : this._friendlySquadUnits;
  }

  getFogSnapshot(nowSec = this._fogLastNowSec) {
    return this.fogOfWar ? this.fogOfWar.getSnapshot(nowSec) : null;
  }

  queryFogAt(wx, wz, opts = null) {
    return this.fogOfWar ? this.fogOfWar.queryPoint(wx, wz, opts) : { visible: true, explored: true, liveAlpha: 1, exploredAlpha: 1, penetrationTier: 255, ewPulse: 0 };
  }

  setFogEnabled(enabled) {
    if (!this.fogOfWar) return;
    this.fogOfWar.enabled = Boolean(enabled);
    this.fogOfWar.update({ units: this._getActiveFogUnits(), nowSec: this._fogLastNowSec, force: true });
  }

  setFogRevealAllDebug(revealAll) {
    if (!this.fogOfWar) return;
    this.fogOfWar.revealAllDebug = Boolean(revealAll);
    this.fogOfWar.update({ units: this._getActiveFogUnits(), nowSec: this._fogLastNowSec, force: true });
  }

  syncFogEnemies(enemies = [], nowSec = this._fogLastNowSec) {
    return this.fogOfWar ? this.fogOfWar.syncContacts(enemies, nowSec) : [];
  }

  _buildBatchCombatMeshes() {
    const hullGeo = new THREE.BoxGeometry(3.6, 1.65, 7.8);
    hullGeo.translate(0, 0.85, 0);
    const hullMat = new THREE.MeshStandardMaterial({ roughness: 0.72, metalness: 0.22 });
    this.squadInstMesh = new THREE.InstancedMesh(hullGeo, hullMat, 120);
    this.squadInstMesh.castShadow = true;
    this.squadInstMesh.receiveShadow = true;
    this.squadInstMesh.frustumCulled = false;
    this.squadInstMesh.setColorAt(0, new THREE.Color(0x7b8863));
    if (this.squadInstMesh.instanceColor) this.squadInstMesh.instanceColor.needsUpdate = true;
    this.squadInstMesh.count = 0;

    const tracerGeo = new THREE.BoxGeometry(0.28, 0.28, 3.8);
    const tracerMat = new THREE.MeshBasicMaterial({ color: 0xffb84d });
    this.tracerInstMesh = new THREE.InstancedMesh(tracerGeo, tracerMat, 64);
    this.tracerInstMesh.frustumCulled = false;
    this.tracerInstMesh.count = 0;

    this.root.add(this.squadInstMesh, this.tracerInstMesh);
  }

  _buildAirGroundShadowMesh() {
    if (this.airGroundShadowMesh) return;
    let tex = null;
    if (typeof document !== 'undefined') {
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 64;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, 64, 64);
        ctx.save();
        ctx.filter = 'blur(3px)';
        ctx.fillStyle = 'rgba(0, 0, 0, 0.92)';
        // 微缩固定翼十字轮廓：机头朝 +Z 对应 UV 纵向（中心对齐，边缘高斯柔化至透明）
        ctx.beginPath();
        ctx.ellipse(32, 32, 4.2, 21.5, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(7, 26, 50, 9.5, 4.5) : ctx.rect(7, 26, 50, 9.5);
        ctx.fill();
        ctx.beginPath();
        ctx.ellipse(32, 12.5, 11.5, 3.2, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        tex = new THREE.CanvasTexture(canvas);
        tex.colorSpace = THREE.NoColorSpace;
        tex.needsUpdate = true;
      }
    }
    this._airShadowTexture = tex;
    const geo = new THREE.PlaneGeometry(1, 1);
    geo.rotateX(-Math.PI / 2);
    const alphaAttr = new THREE.InstancedBufferAttribute(this._airShadowAlphas, 1);
    alphaAttr.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('aAlpha', alphaAttr);
    this._airShadowAlphaAttr = alphaAttr;

    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uMap: { value: tex },
        uHasMap: { value: tex ? 1.0 : 0.0 },
      },
      transparent: true,
      depthWrite: false,
      depthTest: true,
      polygonOffset: true,
      polygonOffsetFactor: -2,
      polygonOffsetUnits: -2,
      toneMapped: false,
      vertexShader: /* glsl */ `
        attribute float aAlpha;
        varying vec2 vUv;
        varying float vAlpha;
        void main() {
          vUv = uv;
          vAlpha = aAlpha;
          vec4 mvPosition = modelViewMatrix * instanceMatrix * vec4(position, 1.0);
          gl_Position = projectionMatrix * mvPosition;
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uMap;
        uniform float uHasMap;
        varying vec2 vUv;
        varying float vAlpha;
        void main() {
          float mask = 0.0;
          if (uHasMap > 0.5) {
            mask = texture2D(uMap, vUv).a;
          } else {
            vec2 p = vUv * 2.0 - 1.0;
            float wing = exp(-dot(vec2(p.x * 1.35, (p.y + 0.05) * 4.5), vec2(p.x * 1.35, (p.y + 0.05) * 4.5)));
            float fuse = exp(-dot(vec2(p.x * 5.2, p.y * 1.45), vec2(p.x * 5.2, p.y * 1.45)));
            mask = clamp(max(wing, fuse), 0.0, 1.0);
          }
          float edgeFade = smoothstep(0.0, 0.14, vUv.x) * smoothstep(1.0, 0.86, vUv.x)
            * smoothstep(0.0, 0.14, vUv.y) * smoothstep(1.0, 0.86, vUv.y);
          float alpha = mask * edgeFade * vAlpha;
          if (alpha < 0.01) discard;
          gl_FragColor = vec4(0.05, 0.06, 0.08, alpha);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, 16);
    mesh.name = 'fixed_wing_ground_shadow_1dc';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.renderOrder = 2;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.airGroundShadowMesh = mesh;
    this.airShadowRoot.add(mesh);
  }

  setSunDirection(dirVec) {
    if (!dirVec) return;
    const x = Number(dirVec.x);
    const y = Number(dirVec.y);
    const z = Number(dirVec.z);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
    const len = Math.hypot(x, y, z);
    if (len > 1e-4) {
      this._sunDir.set(x / len, y / len, z / len);
    }
  }

  _updateFixedWingGroundShadows() {
    const mesh = this.airGroundShadowMesh;
    if (!mesh) return;
    let cnt = 0;
    const sunY = Math.max(0.35, this._sunDir.y);
    const dirOverY_X = this._sunDir.x / sunY;
    const dirOverY_Z = this._sunDir.z / sunY;

    for (let i = 0; i < this.heroUnits.length && cnt < 16; i += 1) {
      const u = this.heroUnits[i];
      if (!u || u.role !== 'fixed_wing' || u.alive === false || u.evacuated || !u.node?.visible) continue;
      const pos = u.node.position;
      const groundUnder = this.support?.heightAt?.(pos.x, pos.z) ?? 0;
      const agl = Math.max(0, pos.y - groundUnder);
      const sx = pos.x - agl * dirOverY_X;
      const sz = pos.z - agl * dirOverY_Z;
      const rawSy = this.support?.heightAt?.(sx, sz);
      const sy = (Number.isFinite(rawSy) ? rawSy : groundUnder) + 0.14;

      const size = u.fixedWingController?.getUnitState?.(u)?.spec?.size || u.fixedWingSpec?.size;
      const wingSpan = Number(size?.[0]) || 17.6;
      const fuselageLen = Number(size?.[2]) || 16.2;

      const hT = Math.max(0, Math.min(1, (agl - 50) / 370));
      const altScale = 1.0 + 0.45 * hT;
      const alpha = 0.42 - 0.30 * hT;
      const rollRad = (Number(u.flightAttitude?.bankDeg) || 0) * (Math.PI / 180);
      const rollSpanScale = Math.max(0.38, Math.abs(Math.cos(rollRad)));

      this._airShadowPos.set(sx, sy, sz);
      this._airShadowQuat.setFromAxisAngle(this._airShadowUp, u.node.rotation?.y || 0);
      this._airShadowScale.set(wingSpan * altScale * rollSpanScale, 1, fuselageLen * altScale);
      this._airShadowMat4.compose(this._airShadowPos, this._airShadowQuat, this._airShadowScale);
      mesh.setMatrixAt(cnt, this._airShadowMat4);
      this._airShadowAlphas[cnt] = alpha;
      cnt += 1;
    }

    mesh.count = cnt;
    mesh.visible = cnt > 0;
    if (cnt > 0) {
      mesh.instanceMatrix.needsUpdate = true;
      if (this._airShadowAlphaAttr) this._airShadowAlphaAttr.needsUpdate = true;
    }
  }

  getAirShadowStats() {
    const m = this.airGroundShadowMesh;
    return {
      activeCount: m ? m.count : 0,
      drawCalls: (m && m.visible && m.count > 0) ? 1 : 0,
      maxCapacity: 16,
    };
  }

  async _ensureVehicleCatalog() {
    if (this._catalogBySlug.size >= 120) return this._catalogBySlug;
    if (!this._catalogPromise) {
      this._catalogPromise = (async () => {
        try {
          const [baseRes, v2Res] = await Promise.allSettled([
            fetch('/v0/基线凭证.json', { cache: 'no-store' }),
            fetch('/v2/编译汇总.json', { cache: 'no-store' }),
          ]);
          if (baseRes.status === 'fulfilled' && baseRes.value.ok) {
            const data = await baseRes.value.json();
            for (const rec of data.vehicles || []) {
              if (rec?.slug) {
                const slotCounts = rec.original_stats?.slot_counts || {};
                const baseSlots = Object.keys(slotCounts).length ? Object.keys(slotCounts) : ['hull'];
                this._catalogBySlug.set(rec.slug, {
                  ...rec,
                  slots: rec.slots || baseSlots,
                });
              }
            }
          }
          if (v2Res.status === 'fulfilled' && v2Res.value.ok) {
            const v2Data = await v2Res.value.json();
            for (const rec of v2Data.vehicles || []) {
              if (rec?.slug) {
                const prev = this._catalogBySlug.get(rec.slug) || {};
                this._catalogBySlug.set(rec.slug, {
                  ...prev,
                  ...rec,
                  name: prev.name || rec.name || rec.slug,
                  category: prev.category || (rec.role === 'ground' ? 'ground' : 'air'),
                  slots: Array.isArray(rec.slots) && rec.slots.length ? rec.slots : (prev.slots || ['hull']),
                });
              }
            }
          }
        } catch {
          // 离线或无目录时依靠 slug 启发式分类
        }
        return this._catalogBySlug;
      })();
    }
    return this._catalogPromise;
  }

  async getVehicleCatalog() {
    await this._ensureVehicleCatalog();
    const list = [];
    for (const rec of this._catalogBySlug.values()) {
      const arch = resolveVehicleArchetype(rec);
      const deployCat = resolveDeployCategory({ ...rec, ...arch });
      const { shortModel, fullModel } = formatVehicleModelDesignation(rec.slug, rec.name || rec.slug);
      list.push({
        slug: rec.slug,
        name: shortModel,
        shortModel,
        fullModel,
        role: arch.role,
        iconKind: arch.iconKind,
        channel: arch.channel,
        cat: arch.cat,
        deployCategory: deployCat,
        label: arch.label,
        slots: Array.isArray(rec.slots) && rec.slots.length ? rec.slots : ['hull'],
      });
    }
    return list;
  }

  _refreshSameSlugDisplayNames() {
    const formatted = formatUnitDisplayNames(this.heroUnits);
    const byId = new Map(formatted.map(it => [it.id, it]));
    for (const u of this.heroUnits) {
      const info = byId.get(u.id);
      if (!info) continue;
      u.modelCode = info.shortModel;
      u.fullModelName = info.fullModel;
      u.shortName = info.displayName;
      u.name = info.displayName;
      if (u.titleSpan) u.titleSpan.textContent = info.displayName;
    }
  }

  getDeployQuotaSummary() {
    return computeDeployQuotaSummary(this.heroUnits);
  }

  clearDeployCategory(categoryKey = 'all') {
    const normCat = normalizeDeployCategoryKey(categoryKey);
    const toRemove = this.heroUnits.filter(u => normCat === 'all' || resolveDeployCategory(u) === normCat);
    if (!toRemove.length) {
      return {
        ok: true,
        category: normCat,
        removedCount: 0,
        heroCount: this.heroUnits.length,
        quota: this.getDeployQuotaSummary(),
      };
    }
    const removeIds = new Set(toRemove.map(u => u.id));
    for (const u of toRemove) {
      this._disposeHeroUnitResources(u);
      this.selectedIds.delete(u.id);
    }
    this.heroUnits = this.heroUnits.filter(u => !removeIds.has(u.id));
    this._refreshSameSlugDisplayNames();
    if (this.hoveredUnitId && removeIds.has(this.hoveredUnitId)) {
      this.hoveredUnitId = null;
    }
    if (!this.selectedUnitId || removeIds.has(this.selectedUnitId)) {
      this.selectedUnitId = this.selectedIds.values().next().value || this.heroUnits[0]?.id || null;
    }
    if (this.selectedUnitId && this.selectedIds.size === 0) {
      this.selectedIds.add(this.selectedUnitId);
    }
    this.onRosterChanged?.({
      action: 'clearCategory',
      category: normCat,
      removedCount: toRemove.length,
      ids: [...this.selectedIds],
    });
    return {
      ok: true,
      category: normCat,
      removedCount: toRemove.length,
      heroCount: this.heroUnits.length,
      selectedIds: [...this.selectedIds],
      quota: this.getDeployQuotaSummary(),
    };
  }

  async _loadScvvehVehicleAsset(slug) {
    if (!this.renderer) return null;
    if (this._catalogBySlug.size > 0 && !this._catalogBySlug.has(slug)) {
      return null;
    }
    if (!this._vehicleStreamLoader) {
      this._vehicleStreamLoader = new ProceduralVehicleLoader(this.renderer, {
        mode: 'archive',
        archiveBaseUrl: '/v3',
        baseUrl: '/v2',
        stylePreset: 'miniature_sand',
        envMap: this.vehicleEnvMap,
        width: this._renderResolution.width,
        height: this._renderResolution.height,
      });
    }
    await this._vehicleStreamLoader.loadIndex();
    await this._vehicleStreamLoader.ensureFarBase();
    const [parsed, meta] = await Promise.all([
      this._vehicleStreamLoader.loadVehicleLod(slug, 0),
      this._vehicleStreamLoader.loadVehicleMeta(slug).catch(() => null),
    ]);
    return parsed?.scene ? { parsed, meta } : null;
  }

  setVehicleRenderResolution(width, height) {
    const w = Math.max(1, Number(width) || 1208);
    const h = Math.max(1, Number(height) || 680);
    this._renderResolution = { width: w, height: h };
    this._vehicleStreamLoader?.setResolution?.(w, h);
    for (const u of this.heroUnits) {
      u.node?.traverse?.(obj => {
        if (obj.userData?.isStylizedOutline) {
          obj.material?.uniforms?.uResolution?.value?.set?.(w, h);
        }
      });
    }
  }

  setVehicleEnvMap(envMap) {
    this.vehicleEnvMap = envMap || null;
    this._vehicleStreamLoader?.setEnvMap?.(this.vehicleEnvMap);
  }

  _disposeHeroUnitResources(u) {
    this.root.remove(u.node);
    u.labelEl?.remove();
    u.node.traverse(obj => {
      if (!obj.isMesh) return;
      if (u._ownsDisposables || obj.userData?.ownsGeoMat) {
        obj.geometry?.dispose?.();
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        for (const m of mats) m?.dispose?.();
      }
    });
  }

  _computeSpawnPos(role, roleIndex) {
    if (role === 'ground') {
      const cols = 3;
      const col = roleIndex % cols;
      const row = Math.floor(roleIndex / cols);
      let sx = -38.0 + col * 16.0;
      let sz = 85.0 + row * 18.0;
      if (roleIndex > 0 && this.physics?.isPointBlocked?.(sx, sz, 5.5)) {
        sz += 16.0;
      }
      return { x: sx, z: sz, yaw: Math.PI * 0.5 };
    }
    if (role === 'helicopter') {
      return {
        x: -55.0 + roleIndex * 24.0,
        z: 102.0 + (roleIndex % 2) * 18.0,
        yaw: Math.PI * 0.5,
      };
    }
    return {
      x: -110.0 + roleIndex * 32.0,
      z: 40.0 + roleIndex * 24.0,
      yaw: Math.PI * 0.35,
    };
  }

  _allocateHeroUnitId(slug, preset = null) {
    const existingIds = new Set(this.heroUnits.map(u => u.id));
    for (let n = 1; n <= 99; n++) {
      const candidate = (preset && n === 1)
        ? preset.id
        : `${slug}_${String(n).padStart(2, '0')}`;
      if (!existingIds.has(candidate)) return candidate;
    }
    return `${slug}_${Date.now().toString(36)}`;
  }

  async _spawnSingleHeroVehicle(slug, {
    spawn,
    orbitCenter,
    orbitRadius = 85.0,
    orbitPhase = 0,
    loadRealGlb = true,
  } = {}) {
    const preset = DEFAULT_HERO_PRESETS[slug] || null;
    const catalogRec = this._catalogBySlug.get(slug) || { slug, name: slug };
    const arch = resolveVehicleArchetype(catalogRec);
    const deployCategory = resolveDeployCategory({ ...catalogRec, ...arch });
    const unitId = this._allocateHeroUnitId(slug, preset);
    const designation = formatVehicleModelDesignation(slug, preset?.fullModelName || catalogRec.name || slug);
    const shortName = designation.shortModel;
    const displayName = designation.shortModel;

    const node = new THREE.Group();
    let ownsDisposables = false;
    let rig = null;
    if (loadRealGlb) {
      // 优先加载包含拆件枢纽（turret/barrel/main_rotor/tail_rotor/gun）的 .scvveh LOD0
      try {
        const scvveh = await this._loadScvvehVehicleAsset(slug);
        if (scvveh) {
          rig = buildVehicleRigHierarchy(scvveh.parsed, scvveh.meta, { THREE, isScvveh: true });
        }
      } catch {
        rig = null;
      }
      if (!rig && preset?.url && this.loader) {
        try {
          const gltf = await this.loader.loadAsync(preset.url);
          rig = buildVehicleRigHierarchy(gltf.scene, null, { THREE, isScvveh: true });
        } catch {
          rig = null;
        }
      }
    }
    if (!rig) {
      const fallbackMesh = this._makeFallbackVehicleMesh(arch.role, preset?.color || 0x6f7c61);
      rig = buildVehicleRigHierarchy(fallbackMesh, null, { THREE, isScvveh: false });
      ownsDisposables = true;
    }
    node.add(rig.rigBodyGroup);

    const gy = this.support.heightAt(spawn.x, spawn.z) || 0;
    const startY = arch.role === 'ground'
      ? gy
      : arch.role === 'helicopter'
        ? this.physics.queryHelicopterSafeAltitude(spawn.x, spawn.z, 1, 0).safeAltitudeY
        : 234.0;

    node.position.set(spawn.x, startY, spawn.z);
    node.rotation.y = spawn.yaw ?? (Math.PI * 0.5);
    node.visible = this.combatMode === 'g01_vehicles';
    this.root.add(node);

    const labelEl = document.createElement('div');
    labelEl.className = `ground-mark kind-${arch.iconKind}`;
    labelEl.dataset.unitId = unitId;
    labelEl.style.pointerEvents = 'auto';
    const svgEl = createSilhouetteSvg(arch.iconKind, { size: 14 });
    const titleSpan = document.createElement('span');
    titleSpan.className = 'mark-name';
    titleSpan.textContent = shortName;
    const metaSpan = document.createElement('span');
    metaSpan.className = 'mark-sub';
    metaSpan.textContent = '';
    labelEl.append(svgEl, titleSpan, metaSpan);

    labelEl.addEventListener('pointerdown', e => {
      // 仅在常规指挥态且左键点击名牌时触发选择；子模式（部署/攻地/编辑）与右键/中键透传画布
      if (e.button !== 0) return;
      if (this.hostElement?.dataset?.interactionMode && this.hostElement.dataset.interactionMode !== 'play') {
        return;
      }
      e.stopPropagation();
      if (this.onLabelPointerDown) {
        this.onLabelPointerDown(unitId, e);
      } else {
        this.setSelection([unitId], unitId);
        this.onSelectUnit?.(unitId);
      }
    });
    labelEl.addEventListener('pointerenter', () => {
      this.hoveredUnitId = unitId;
      this.onLabelHover?.(unitId);
    });
    labelEl.addEventListener('pointerleave', () => {
      if (this.hoveredUnitId === unitId) {
        this.hoveredUnitId = null;
        this.onLabelHover?.(null);
      }
    });
    this.nameplateHost.appendChild(labelEl);

    const defaultWeapons = preset?.weapons || (
      arch.role === 'ground'
        ? ['主炮高爆 24', '同轴机枪 800']
        : arch.role === 'helicopter'
          ? ['30mm 链炮 800', '空地导弹 ×8']
          : ['航炮 1000', '制导炸弹 ×4']
    );

    let boundingRadius = arch.role === 'fixed_wing' ? 11.5 : (arch.role === 'helicopter' ? 8.2 : 5.6);
    try {
      const box = new THREE.Box3().setFromObject(rig.rigBodyGroup);
      if (!box.isEmpty()) {
        const sph = new THREE.Sphere();
        box.getBoundingSphere(sph);
        if (Number.isFinite(sph.radius) && sph.radius > 1.2) {
          boundingRadius = Math.max(3.5, Math.min(22.0, sph.radius));
        }
      }
    } catch {
      // 保底半径即可
    }

    const unit = {
      id: unitId,
      slug,
      modelCode: designation.shortModel,
      fullModelName: designation.fullModel,
      archLabel: arch.label,
      boundingRadius,
      name: displayName,
      shortName,
      role: arch.role,
      iconKind: arch.iconKind,
      channel: arch.channel,
      cat: arch.cat,
      deployCategory,
      speed: arch.speed,
      ringRadius: arch.ringRadius,
      pickRadius: arch.pickRadius,
      weaponRangeM: preset?.weaponRangeM ?? (arch.role === 'ground' ? 160 : 220),
      color: preset?.color ?? 0x6f7c61,
      hp: 100,
      bonus: preset?.bonus ?? 0,
      weapons: defaultWeapons,
      roe: 'free',
      groundGuard: true,
      node,
      labelEl,
      titleSpan,
      metaSpan,
      activeWep: 0,
      waypoints: [],
      wpIndex: 0,
      job: null,
      queue: [],
      targetFacing: null,
      commandStatus: { status: 'idle', reason: '', commandId: null },
      lastRejectedCommand: null,
      lastAttackGroundIntent: null,
      orbitCenter: orbitCenter || { x: spawn.x, z: spawn.z },
      orbitRadius,
      orbitPhase,
      rig,
      animState: null,
      _anim: null,
      maxRecordedY: startY,
      minObstacleClearanceM: 999.0,
      labelAnchorOffset: null,
      _ownsDisposables: ownsDisposables,
    };
    this.heroUnits.push(unit);
    this._refreshSameSlugDisplayNames();
    return unit;
  }

  _findSafeDeploySpot(role, anchorX, anchorZ, { isExplicitPoint = false } = {}) {
    const bounds = this.physics?.b3Manifest?.bounds || { minX: -4425, maxX: 4575, minZ: -4150, maxZ: 4850 };
    const margin = 20.0;
    const minSep = role === 'ground' ? 9.5 : 14.0;

    const isSpotClear = (cx, cz) => {
      if (!Number.isFinite(cx) || !Number.isFinite(cz)) return false;
      if (cx < bounds.minX + margin || cx > bounds.maxX - margin || cz < bounds.minZ + margin || cz > bounds.maxZ - margin) {
        return false;
      }
      if (role === 'ground' && this.physics?.isPointBlocked?.(cx, cz, 5.5)) {
        return false;
      }
      for (const u of this.heroUnits) {
        if (u.role === role) {
          const d = Math.hypot(cx - u.node.position.x, cz - u.node.position.z);
          if (d < minSep) return false;
        }
      }
      return true;
    };

    if (isSpotClear(anchorX, anchorZ)) {
      return { ok: true, x: anchorX, z: anchorZ, slid: false, offsetM: 0 };
    }

    const radii = isExplicitPoint
      ? [4, 8, 12, 16, 22, 28, 36, 45, 56]
      : [6, 12, 18, 24, 30, 36, 44, 52, 60];
    const angleSteps = 12;
    const basePhase = (this.heroUnits.length * 0.75) % (Math.PI * 2);

    for (const r of radii) {
      for (let k = 0; k < angleSteps; k++) {
        const a = basePhase + (k / angleSteps) * Math.PI * 2;
        const cx = anchorX + Math.cos(a) * r;
        const cz = anchorZ + Math.sin(a) * r;
        if (isSpotClear(cx, cz)) {
          return { ok: true, x: cx, z: cz, slid: true, offsetM: r };
        }
      }
    }

    if (isExplicitPoint) {
      return { ok: false, reason: '目标落点及周边 28m 均位于障碍、水域或陡坡内' };
    }
    return { ok: true, x: anchorX + 14.0, z: anchorZ + 14.0, slid: true, offsetM: 20 };
  }

  async deployVehicle(slug, worldPos = null, { autoSelect = true, loadRealGlb = true, centerHint = null } = {}) {
    if (!slug) return { ok: false, reason: '未指定载具型号 slug' };
    if (this.combatMode !== 'g01_vehicles') {
      this.setLoadMode('g01_vehicles');
    }

    await this._ensureVehicleCatalog();
    const catalogRec = this._catalogBySlug.get(slug) || { slug, name: slug };
    const arch = resolveVehicleArchetype(catalogRec);
    const deployCat = resolveDeployCategory({ ...catalogRec, ...arch });
    const quotaSummary = this.getDeployQuotaSummary();
    const catQuota = quotaSummary.categories[deployCat];

    if (catQuota && catQuota.full) {
      return {
        ok: false,
        category: deployCat,
        reason: `【${catQuota.label}】类别已达部署上限 (${catQuota.count} / ${catQuota.max})，可继续部署其他未满类别`,
        quota: quotaSummary,
      };
    }
    if (this.heroUnits.length >= MAX_HERO_UNITS) {
      return {
        ok: false,
        category: deployCat,
        reason: `场内载具总数已达上限 (${MAX_HERO_UNITS} / ${MAX_HERO_UNITS})`,
        quota: quotaSummary,
      };
    }

    const isExplicitPoint = Boolean(worldPos && Number.isFinite(worldPos.x) && Number.isFinite(worldPos.z));
    const primaryUnit = this.heroUnits.find(u => u.id === this.selectedUnitId) || this.heroUnits[0];
    const ax = isExplicitPoint
      ? worldPos.x
      : Number.isFinite(centerHint?.x)
        ? centerHint.x
        : (primaryUnit?.node?.position?.x ?? -20.0);
    const az = isExplicitPoint
      ? worldPos.z
      : Number.isFinite(centerHint?.z)
        ? centerHint.z
        : (primaryUnit?.node?.position?.z ?? 85.0);

    const spot = this._findSafeDeploySpot(arch.role, ax, az, { isExplicitPoint });
    if (!spot.ok) {
      return { ok: false, reason: spot.reason };
    }

    const orbitRadius = 85.0;
    const planeCount = this.heroUnits.filter(u => u.role === 'fixed_wing').length;
    const orbitPhase = (planeCount * 1.4) % (Math.PI * 2);
    const orbitCenter = { x: spot.x, z: spot.z };
    const spawn = arch.role === 'fixed_wing'
      ? {
        x: orbitCenter.x + Math.cos(orbitPhase) * orbitRadius,
        z: orbitCenter.z + Math.sin(orbitPhase) * orbitRadius,
        yaw: Math.atan2(-Math.sin(orbitPhase), Math.cos(orbitPhase)),
      }
      : {
        x: spot.x,
        z: spot.z,
        yaw: Math.PI * 0.5,
      };

    const unit = await this._spawnSingleHeroVehicle(slug, {
      spawn,
      orbitCenter,
      orbitRadius,
      orbitPhase,
      loadRealGlb,
    });

    stepVehicleAnimations([unit], {
      dt: 0.016,
      nowSec: performance.now() * 0.001,
      support: this.support,
    });

    if (autoSelect) {
      this.setSelection([unit.id], unit.id);
    }
    this.onRosterChanged?.({
      action: 'deploy',
      unit,
      unitId: unit.id,
      ids: [...this.selectedIds],
    });

    return {
      ok: true,
      unitId: unit.id,
      slug: unit.slug,
      name: unit.name,
      shortName: unit.shortName,
      role: unit.role,
      iconKind: unit.iconKind,
      channel: unit.channel,
      deployCategory: unit.deployCategory,
      position: [unit.node.position.x, unit.node.position.y, unit.node.position.z],
      slid: Boolean(spot.slid),
      offsetM: spot.offsetM || 0,
      heroCount: this.heroUnits.length,
      quota: this.getDeployQuotaSummary(),
    };
  }

  removeVehicle(unitId) {
    const targetId = unitId || this.selectedUnitId;
    if (!targetId) return { ok: false, reason: '当前未选中任何可移除载具' };
    const idx = this.heroUnits.findIndex(u => u.id === targetId);
    if (idx === -1) return { ok: false, reason: `未找到载具 ${targetId}` };

    const [removed] = this.heroUnits.splice(idx, 1);
    this._disposeHeroUnitResources(removed);
    this._refreshSameSlugDisplayNames();
    this.selectedIds.delete(targetId);
    if (this.hoveredUnitId === targetId) {
      this.hoveredUnitId = null;
    }
    if (this.selectedUnitId === targetId) {
      this.selectedUnitId = this.selectedIds.values().next().value || this.heroUnits[0]?.id || null;
    }
    if (this.selectedUnitId && this.selectedIds.size === 0) {
      this.selectedIds.add(this.selectedUnitId);
    }

    this.onRosterChanged?.({
      action: 'remove',
      removedId: targetId,
      removedName: removed.shortName || removed.name,
      ids: [...this.selectedIds],
    });

    return {
      ok: true,
      removedId: targetId,
      removedName: removed.shortName || removed.name,
      heroCount: this.heroUnits.length,
      selectedIds: [...this.selectedIds],
    };
  }

  async resetDefaultRoster(loadRealGlb = true) {
    await this.initHeroVehicles(loadRealGlb, ['US_M1A2', 'US_AH64D', 'US_A10']);
    this.onRosterChanged?.({
      action: 'reset',
      ids: [...this.selectedIds],
    });
    return {
      ok: true,
      heroCount: this.heroUnits.length,
      ids: this.heroUnits.map(u => u.id),
      selectedIds: [...this.selectedIds],
    };
  }

  async initHeroVehicles(loadRealGlb = true, rosterSlugsOverride = null) {
    for (const u of this.heroUnits) {
      this._disposeHeroUnitResources(u);
    }
    this.heroUnits = [];

    const rawSlugs = (Array.isArray(rosterSlugsOverride) && rosterSlugsOverride.length > 0
      ? rosterSlugsOverride
      : readStoredRosterSlugs()).slice(0, MAX_HERO_UNITS);
    const slugs = rawSlugs
      .map(s => String(s ?? '').trim())
      .filter(s => /^[A-Za-z0-9_-]{1,64}$/.test(s));
    if (slugs.length === 0) slugs.push('US_M1A2', 'US_AH64D', 'US_A10');

    const hasCustomSlug = slugs.some(s => !DEFAULT_HERO_PRESETS[s]);
    if (hasCustomSlug) {
      await this._ensureVehicleCatalog();
    }

    const roleCounters = { ground: 0, helicopter: 0, fixed_wing: 0 };

    for (const slug of slugs) {
      const catalogRec = this._catalogBySlug.get(slug) || { slug, name: slug };
      const arch = resolveVehicleArchetype(catalogRec);
      const roleIdx = roleCounters[arch.role]++;
      const orbitCenter = { x: -40.0 + roleIdx * 36.0, z: 85.0 + roleIdx * 24.0 };
      const orbitRadius = 85.0;
      const orbitPhase = roleIdx * 1.4;
      const spawn = arch.role === 'fixed_wing'
        ? {
          x: orbitCenter.x + Math.cos(orbitPhase) * orbitRadius,
          z: orbitCenter.z + Math.sin(orbitPhase) * orbitRadius,
          yaw: Math.atan2(-Math.sin(orbitPhase), Math.cos(orbitPhase)),
        }
        : this._computeSpawnPos(arch.role, roleIdx);

      await this._spawnSingleHeroVehicle(slug, {
        spawn,
        orbitCenter,
        orbitRadius,
        orbitPhase,
        loadRealGlb,
      });
    }

    const firstId = this.heroUnits[0]?.id || null;
    this.selectedUnitId = firstId;
    this.selectedIds = firstId ? new Set([firstId]) : new Set();
    stepVehicleAnimations(this.heroUnits, { dt: 0.016, nowSec: 0, support: this.support });
  }

  _makeFallbackVehicleMesh(role, colorHex) {
    const grp = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.65, metalness: 0.25 });
    if (role === 'ground') {
      const hull = new THREE.Mesh(new THREE.BoxGeometry(3.6, 1.3, 7.8), mat);
      hull.position.y = 0.7;
      const turret = new THREE.Mesh(new THREE.BoxGeometry(2.8, 0.9, 3.8), mat);
      turret.position.set(0, 1.65, -0.3);
      grp.add(hull, turret);
    } else if (role === 'helicopter') {
      const body = new THREE.Mesh(new THREE.BoxGeometry(2.2, 2.0, 11.5), mat);
      body.position.y = 1.2;
      const rotor = new THREE.Mesh(new THREE.BoxGeometry(12.5, 0.12, 0.6), mat);
      rotor.position.y = 2.5;
      grp.add(body, rotor);
    } else {
      const fuselage = new THREE.Mesh(new THREE.BoxGeometry(2.0, 1.8, 15.5), mat);
      const wing = new THREE.Mesh(new THREE.BoxGeometry(17.0, 0.25, 2.8), mat);
      grp.add(fuselage, wing);
    }
    return grp;
  }

  _buildPlanningEnv() {
    const bounds = this.physics?.b3Manifest?.bounds || undefined;
    return {
      bounds,
      querySupport: (x, z) => this.support.query(x, z),
      isPointBlocked: (x, z, clearance = 5.5) => this.physics.isPointBlocked(x, z, clearance),
      findGroundPath: (sx, sz, gx, gz, clearance = 5.5) => this.physics.findLocalPath4m(sx, sz, gx, gz, clearance),
      buildHeliProfile: (sx, sz, gx, gz) => this.physics.buildHelicopterFlightProfile(sx, sz, gx, gz, 36),
    };
  }

  _applyWaypointsToUnit(unit, rawWaypoints) {
    unit.waypoints = (rawWaypoints || []).map(p => (
      Array.isArray(p)
        ? new THREE.Vector3(p[0], p[1], p[2])
        : new THREE.Vector3(p.x, p.y ?? 0, p.z)
    ));
    unit.wpIndex = 0;
    if (unit.role === 'helicopter') {
      unit.maxRecordedY = unit.node.position.y;
    }
  }

  setSelection(ids, primaryId = null) {
    const allowed = new Set(this.heroUnits.map(u => u.id));
    this.selectedIds = new Set((ids || []).filter(id => allowed.has(id)));
    this.selectedUnitId = (primaryId && this.selectedIds.has(primaryId))
      ? primaryId
      : (this.selectedIds.values().next().value || null);
  }

  setHoveredUnit(unitId) {
    this.hoveredUnitId = unitId || null;
  }

  setChannelFilter(filter) {
    this.channelFilter = filter || 'all';
  }

  /**
   * 纯只读指令预览计划（CQS Query）。
   */
  planCommand(ids, command, append = false) {
    return planFleetCommand({
      units: this.heroUnits,
      ids,
      command,
      append,
      env: this._buildPlanningEnv(),
    });
  }

  /**
   * 执行调度指令（支持覆盖/追加多段队列、朝向设置与不可达拒收保原任务）。
   */
  dispatchCommand(ids, command, append = false, plannedRows = null) {
    const res = dispatchFleetCommand({
      units: this.heroUnits,
      ids,
      command,
      append,
      env: this._buildPlanningEnv(),
      runId: this.runId,
      serial: ++this.commandSerial,
      applyWaypoints: (unit, waypoints) => this._applyWaypointsToUnit(unit, waypoints),
      plannedRows,
    });
    this.lastCommandResult = res;
    return res;
  }

  /**
   * 停止指定单位并清空任务队列（地面停车、旋翼悬停、固定翼在原地空域盘旋）。
   */
  cancelUnits(ids) {
    return cancelFleetUnits({ units: this.heroUnits, ids });
  }

  stopUnit(unitId) {
    const stopped = this.cancelUnits([unitId]);
    return stopped.length > 0;
  }

  damageUnit(unitId, delta = 28) {
    const unit = this.heroUnits.find(u => u.id === unitId);
    if (!unit) return null;
    unit.hp = Math.max(10, Math.min(100, (unit.hp ?? 100) - delta));
    return unit.hp;
  }

  /**
   * 兼容既有自动化脚本的便捷单车移动入口。
   */
  issueMoveCommand(unitId, targetX, targetZ) {
    const unit = this.heroUnits.find(u => u.id === unitId) || this.heroUnits[0];
    if (!unit) return null;

    const sx = unit.node.position.x;
    const sz = unit.node.position.z;

    if (unit.role === 'ground') {
      const nav = this.physics.findLocalPath4m(sx, sz, targetX, targetZ, 5.5);
      this._applyWaypointsToUnit(unit, nav.waypoints);
      unit.job = { type: 'move', point: { x: targetX, z: targetZ } };
      unit.queue = [];
      unit.targetFacing = null;
      unit.commandStatus = { status: 'moving', reason: '' };
      return {
        unitId: unit.id,
        role: unit.role,
        found: nav.found,
        waypoints: nav.waypoints,
      };
    }
    if (unit.role === 'helicopter') {
      const profile = this.physics.buildHelicopterFlightProfile(sx, sz, targetX, targetZ, 36);
      this._applyWaypointsToUnit(unit, profile);
      unit.job = { type: 'move', point: { x: targetX, z: targetZ } };
      unit.queue = [];
      unit.targetFacing = null;
      unit.commandStatus = { status: 'moving', reason: '' };
      return {
        unitId: unit.id,
        role: unit.role,
        found: true,
        waypoints: profile,
        maxAltitudeY: Math.max(...profile.map(p => p[1])),
      };
    }
    const wp = [[targetX, 234.0, targetZ]];
    this._applyWaypointsToUnit(unit, wp);
    unit.job = { type: 'move', point: { x: targetX, z: targetZ } };
    unit.queue = [];
    unit.orbitCenter = { x: targetX, z: targetZ };
    if (unit.fixedWingController) {
      unit.fixedWingController.triggerCommand?.(unit, 'orbit', { x: targetX, z: targetZ });
    }
    unit.commandStatus = { status: 'moving', reason: '' };
    return {
      unitId: unit.id,
      role: unit.role,
      found: true,
      waypoints: wp,
    };
  }

  _advanceNextQueuedJob(unit) {
    if (unit.queue && unit.queue.length > 0) {
      const nextJob = unit.queue.shift();
      unit.job = nextJob;
      unit.targetFacing = Number.isFinite(nextJob.facing) ? nextJob.facing : null;
      if (nextJob.point) {
        const ref = { x: unit.node.position.x, z: unit.node.position.z };
        if (unit.role === 'ground') {
          const nav = this.physics.findLocalPath4m(ref.x, ref.z, nextJob.point.x, nextJob.point.z, 5.5);
          if (nav.found && !this.physics.isPointBlocked(nextJob.point.x, nextJob.point.z, 5.5)) {
            this._applyWaypointsToUnit(unit, nav.waypoints);
            unit.commandStatus = { status: 'moving', reason: '', commandId: nextJob.id };
            return;
          }
          unit.commandStatus = { status: 'rejected', reason: '队列中下一段目的地受阻', commandId: nextJob.id };
          unit.job = null;
          unit.queue = [];
          return;
        }
        if (unit.role === 'helicopter') {
          const profile = this.physics.buildHelicopterFlightProfile(ref.x, ref.z, nextJob.point.x, nextJob.point.z, 36);
          this._applyWaypointsToUnit(unit, profile);
          unit.commandStatus = { status: 'moving', reason: '', commandId: nextJob.id };
          return;
        }
        unit.orbitCenter = { x: nextJob.point.x, z: nextJob.point.z };
        this._applyWaypointsToUnit(unit, [[nextJob.point.x, 234.0, nextJob.point.z]]);
        unit.commandStatus = { status: 'moving', reason: '', commandId: nextJob.id };
        return;
      }
    }
    unit.job = null;
    unit.waypoints = [];
    unit.wpIndex = 0;
    unit.targetFacing = null;
    unit.commandStatus = { status: 'idle', reason: '', commandId: null };
  }

  setLoadMode(mode, center = new THREE.Vector3(0, 0, 85)) {
    this.combatMode = mode;
    this.squadUnits = [];
    this._friendlySquadUnits = [];
    this.projectiles = [];

    const showHeroes = mode === 'g01_vehicles';
    for (const u of this.heroUnits) {
      u.node.visible = showHeroes;
      if (u.labelEl) {
        u.labelEl.style.display = showHeroes ? 'inline-flex' : 'none';
        if (u._lblCache) u._lblCache.visible = showHeroes;
      }
    }

    if (mode === 'empty' || mode === 'g01_vehicles') {
      this.squadInstMesh.count = 0;
      this.squadInstMesh.visible = false;
      this.tracerInstMesh.count = 0;
      this.tracerInstMesh.visible = false;
      return { activeUnits: showHeroes ? this.heroUnits.length : 0 };
    }

    const count = mode === '10_tanks' ? 10 : 100;
    const cols = mode === '10_tanks' ? 5 : 10;
    const spacing = mode === '100_onscreen' ? 9.5 : 22.0;

    this.squadInstMesh.visible = true;
    this.squadInstMesh.count = count;
    const color = new THREE.Color();

    for (let i = 0; i < count; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const isAir = mode === '100_mixed_combat' && i >= 76;
      const isHeli = isAir && i < 92;
      const team = i % 2;
      const kind = isHeli ? 'heli' : isAir ? 'jet' : 'tank';
      const role = isHeli ? 'helicopter' : isAir ? 'fixed_wing' : 'ground';

      const wx = center.x + (col - (cols - 1) * 0.5) * spacing;
      const wz = center.z + (row - (Math.ceil(count / cols) - 1) * 0.5) * spacing;
      const gy = this.support.heightAt(wx, wz) || 0;
      const wy = isHeli ? gy + 24.0 : isAir ? 145.0 : gy;

      const squadUnit = {
        id: `squad_${i}`,
        index: i,
        team,
        kind,
        role,
        baseX: wx,
        baseZ: wz,
        x: wx,
        y: wy,
        z: wz,
        yaw: team === 0 ? 0 : Math.PI,
        phase: (i * 0.47) % (Math.PI * 2),
        speed: isAir ? 28.0 : 10.5,
      };
      this.squadUnits.push(squadUnit);
      if (team === 0) {
        this._friendlySquadUnits.push(squadUnit);
      }

      color.setHex(team === 0 ? (isAir ? 0x628b68 : 0x7b8863) : (isAir ? 0x8f6b58 : 0x8a7459));
      this.squadInstMesh.setColorAt(i, color);
    }
    if (this.squadInstMesh.instanceColor) this.squadInstMesh.instanceColor.needsUpdate = true;

    if (mode === '100_mixed_combat') {
      this.tracerInstMesh.visible = true;
      this.tracerInstMesh.count = 32;
      for (let k = 0; k < 32; k++) {
        this.projectiles.push({
          x: center.x + ((k % 8) - 3.5) * 20.0,
          y: (this.support.heightAt(center.x, center.z) || 0) + 2.2 + (k % 3) * 4.0,
          z: center.z - 60.0 + k * 3.5,
          vx: (k % 2 === 0 ? 1 : -1) * 65.0,
          vz: (k % 2 === 0 ? 1 : -1) * 45.0,
          progress: (k / 32.0),
        });
      }
    } else {
      this.tracerInstMesh.count = 0;
      this.tracerInstMesh.visible = false;
    }
    return { activeUnits: count };
  }

  update(dtSeconds, nowSeconds, currentSpan = 135) {
    const dt = Math.min(0.1, Math.max(0.001, dtSeconds));

    // 1. 更新 G01 / 编队主角载具移动、转向、越障与 V004 贴身名牌避让
    if (this.combatMode === 'g01_vehicles') {
      const width = this._hostW;
      const height = this._hostH;
      const projVec = this._projVec;
      const nameplateItems = this._nameplateItems;
      nameplateItems.length = 0;

      for (const u of this.heroUnits) {
        if (u.fixedWingController) continue;
        if (u.waypoints.length > 0 && u.wpIndex < u.waypoints.length) {
          const target = u.waypoints[u.wpIndex];
          const dx = target.x - u.node.position.x;
          const dz = target.z - u.node.position.z;
          const distXZ = Math.hypot(dx, dz);
          const step = u.speed * dt;

          if (distXZ <= step + 0.35) {
            u.node.position.x = target.x;
            u.node.position.z = target.z;
            u.wpIndex++;
          } else {
            const ux = dx / distXZ;
            const uz = dz / distXZ;
            u.node.position.x += ux * step;
            u.node.position.z += uz * step;
            // 按兵种角速度沿最短弧平滑转向，杜绝反复下令时 180° 瞬切导致主车体与 PiP 小窗跳动
            const desiredYaw = Math.atan2(ux, uz);
            const curYaw = Number.isFinite(u.node.rotation.y) ? u.node.rotation.y : desiredYaw;
            const yawDiff = wrapAngleRad(desiredYaw - curYaw);
            const turnRate = u.role === 'ground' ? 3.4 : (u.role === 'helicopter' ? 2.6 : 1.8);
            const maxStepYaw = turnRate * dt;
            if (Math.abs(yawDiff) <= maxStepYaw) {
              u.node.rotation.y = desiredYaw;
            } else {
              u.node.rotation.y = wrapAngleRad(curYaw + Math.sign(yawDiff) * maxStepYaw);
            }
          }

          if (u.role === 'ground') {
            const gy = this.support.heightAt(u.node.position.x, u.node.position.z);
            if (Number.isFinite(gy)) u.node.position.y = gy;
          } else if (u.role === 'helicopter') {
            const safeQ = this.physics.queryHelicopterSafeAltitude(
              u.node.position.x,
              u.node.position.z,
              dx || Math.sin(u.node.rotation.y),
              dz || Math.cos(u.node.rotation.y)
            );
            const targetY = Math.max(target.y, safeQ.safeAltitudeY);
            u.node.position.y += (targetY - u.node.position.y) * Math.min(1.0, dt * 6.5);
            u.maxRecordedY = Math.max(u.maxRecordedY, u.node.position.y);
            if (safeQ.hasObstacleAhead) {
              u.minObstacleClearanceM = Math.min(u.minObstacleClearanceM, u.node.position.y - safeQ.highestObstacleTopY);
            }
          } else {
            u.node.position.y = 234.0;
          }

          // 到达本段航路终点后：执行终末朝向转向并推进 Shift 队列
          if (u.wpIndex >= u.waypoints.length) {
            if (u.role !== 'fixed_wing' && Number.isFinite(u.targetFacing)) {
              const diff = wrapAngleRad(u.targetFacing - u.node.rotation.y);
              const maxTurn = 3.2 * dt;
              if (Math.abs(diff) <= maxTurn) {
                u.node.rotation.y = u.targetFacing;
                this._advanceNextQueuedJob(u);
              } else {
                u.node.rotation.y += Math.sign(diff) * maxTurn;
              }
            } else {
              this._advanceNextQueuedJob(u);
            }
          }
        } else if (u.role !== 'fixed_wing' && Number.isFinite(u.targetFacing)) {
          const diff = wrapAngleRad(u.targetFacing - u.node.rotation.y);
          const maxTurn = 3.2 * dt;
          if (Math.abs(diff) <= maxTurn) {
            u.node.rotation.y = u.targetFacing;
            this._advanceNextQueuedJob(u);
          } else {
            u.node.rotation.y += Math.sign(diff) * maxTurn;
          }
        } else if (u.role === 'fixed_wing') {
          // 固定翼始终围绕最新盘旋中心正圆巡航（永不悬停，且地面投影精确落于贴地范围圈圆周上）
          const oc = u.orbitCenter || { x: -40.0, z: 85.0 };
          const r = u.orbitRadius || 85.0;
          u.orbitPhase += dt * 0.42;
          const targetX = oc.x + Math.cos(u.orbitPhase) * r;
          const targetZ = oc.z + Math.sin(u.orbitPhase) * r;
          const dx = targetX - u.node.position.x;
          const dz = targetZ - u.node.position.z;
          const distToCircle = Math.hypot(dx, dz);
          const maxStep = Math.max(u.speed || 52.0, 42.0) * dt;

          if (distToCircle > Math.max(maxStep * 1.8, 3.0)) {
            const ux = dx / distToCircle;
            const uz = dz / distToCircle;
            u.node.position.x += ux * maxStep;
            u.node.position.z += uz * maxStep;
            u.node.position.y = 234.0;
            const desiredYaw = Math.atan2(ux, uz);
            const diff = wrapAngleRad(desiredYaw - u.node.rotation.y);
            u.node.rotation.y = wrapAngleRad(u.node.rotation.y + Math.sign(diff) * Math.min(Math.abs(diff), 1.6 * dt));
          } else {
            u.node.position.set(targetX, 234.0, targetZ);
            u.node.rotation.y = Math.atan2(-Math.sin(u.orbitPhase), Math.cos(u.orbitPhase));
          }
        }
      }

      // Stage 2：129 车通用刚体姿态 + 已有 37 车旋翼/炮塔/雷达部件驱动
      stepVehicleAnimations(this.heroUnits, {
        dt,
        nowSec: nowSeconds,
        support: this.support,
        currentSpan,
      });

      const hideUnitLabel = (u) => {
        if (!u.labelEl) return;
        const cache = u._lblCache || (u._lblCache = { visible: null, tx: NaN, ty: NaN, clsBits: -1, title: null, meta: null });
        if (cache.visible !== false) {
          u.labelEl.style.display = 'none';
          cache.visible = false;
        }
      };

      for (const u of this.heroUnits) {
        if (u.evacuated || !u.node?.visible) {
          hideUnitLabel(u);
          u.labelAnchorOffset = null;
          continue;
        }
        if (u.labelEl) {
          const isSel = this.selectedIds.has(u.id) || u.id === this.selectedUnitId;
          const isHov = u.id === this.hoveredUnitId;
          // L0 (<=260m) 全量显示 DOM 名牌；L1 (260~650m) 仅显示选中或悬停单位呼号；L2/L3 (>650m) 全交 2D 图标层
          const hideNameplate = currentSpan > 650 || (currentSpan > 260 && !isSel && !isHov);
          if (hideNameplate) {
            hideUnitLabel(u);
            u.labelAnchorOffset = null;
          } else {
            projVec.copy(u.node.position);
            projVec.y += u.role === 'ground' ? 1.5 : 2.8;
            projVec.project(this.camera);
            const sx = (projVec.x * 0.5 + 0.5) * width;
            const sy = (-projVec.y * 0.5 + 0.5) * height;
            const inScreen =
              projVec.z >= -1.02 &&
              projVec.z <= 1.02 &&
              sx >= -28 &&
              sx <= width + 28 &&
              sy >= -20 &&
              sy <= height + 20;
            if (!inScreen) {
              hideUnitLabel(u);
              u.labelAnchorOffset = null;
            } else {
              const isActiveTask = Boolean(u.job || (u.waypoints.length - u.wpIndex > 0));
              const anchorY = Math.max(48, Math.min(height - 30, sy + (u.role === 'ground' ? 16 : 20)));
              const anchorX = Math.max(40, Math.min(width - 40, sx));
              nameplateItems.push({
                unit: u,
                id: u.id,
                isSelected: isSel,
                isHovered: isHov,
                isActiveTask,
                anchor: { x: anchorX, y: anchorY },
                fullSize: { width: isSel || isHov ? 114 : 74, height: 22 },
                compactSize: { width: 24, height: 20 },
              });
            }
          }
        }
      }

      if (nameplateItems.length > 0) {
        const resolved = GroundNameplateCollision.resolve(nameplateItems, { margin: 3 });
        for (let i = 0; i < nameplateItems.length; i += 1) {
          const item = nameplateItems[i];
          const u = item.unit;
          const res = resolved[i];
          const el = u.labelEl;
          if (!el || !res) continue;

          const cache = u._lblCache || (u._lblCache = { visible: null, tx: NaN, ty: NaN, clsBits: -1, title: null, meta: null });
          const tx = Math.round(res.anchor.x);
          const ty = Math.round(res.anchor.y);

          if (cache.visible !== true) {
            el.style.display = 'inline-flex';
            cache.visible = true;
          }
          if (cache.tx !== tx || cache.ty !== ty) {
            el.style.transform = `translate3d(${tx}px,${ty}px,0) translate(-50%,0)`;
            cache.tx = tx;
            cache.ty = ty;
          }

          const fwFuel = u.role === 'fixed_wing' ? u.fuelTelemetry : null;
          const fwLowFuel = Boolean(fwFuel && (fwFuel.bingoState === 'low' || fwFuel.bingoState === 'bingo' || fwFuel.bingoState === 'rtb_bingo'));
          const fwBingo = Boolean(fwFuel && (fwFuel.bingoState === 'bingo' || fwFuel.bingoState === 'rtb_bingo'));
          const fwWinchester = Boolean(fwFuel && (fwFuel.winchesterState === 'winchester' || fwFuel.winchesterState === 'rtb_winchester'));

          const isCompact = res.mode === 'compact';
          const isDimmed = res.mode === 'dimmed';
          const isTop = Boolean(res.isTop);
          const isLayerMuted = !isUnitAllowedByChannel(u, this.channelFilter);
          const isMid = currentSpan > 320 && !item.isSelected && !item.isHovered;
          const isFuelLowOnly = fwLowFuel && !fwBingo;

          const clsBits =
            (item.isSelected ? 1 : 0) |
            (item.isHovered ? 2 : 0) |
            (isCompact ? 4 : 0) |
            (isDimmed ? 8 : 0) |
            (isTop ? 16 : 0) |
            (isLayerMuted ? 32 : 0) |
            (isMid ? 64 : 0) |
            (isFuelLowOnly ? 128 : 0) |
            (fwBingo ? 256 : 0) |
            (fwWinchester ? 512 : 0);

          if (cache.clsBits !== clsBits) {
            const diff = cache.clsBits < 0 ? 0x3ff : (cache.clsBits ^ clsBits);
            if (diff & 1) el.classList.toggle('is-selected', item.isSelected);
            if (diff & 2) el.classList.toggle('is-hovered', item.isHovered);
            if (diff & 4) el.classList.toggle('is-compact', isCompact);
            if (diff & 8) el.classList.toggle('is-dimmed', isDimmed);
            if (diff & 16) el.classList.toggle('is-top', isTop);
            if (diff & 32) el.classList.toggle('is-layer-muted', isLayerMuted);
            if (diff & 64) el.classList.toggle('is-mid', isMid);
            if (diff & 128) el.classList.toggle('is-fuel-low', isFuelLowOnly);
            if (diff & 256) el.classList.toggle('is-fuel-bingo', fwBingo);
            if (diff & 512) el.classList.toggle('is-winchester', fwWinchester);
            cache.clsBits = clsBits;
          }

          u.labelAnchorOffset = { x: res.anchor.x, y: res.anchor.y + 10 };

          if (u.titleSpan) {
            const nextTitle = u.shortName || u.name || '';
            if (cache.title !== nextTitle) {
              u.titleSpan.textContent = nextTitle;
              cache.title = nextTitle;
            }
          }
          if (u.metaSpan) {
            let nextMeta = '';
            if (item.isSelected || item.isHovered || fwLowFuel || fwWinchester) {
              const fwState = u.role === 'fixed_wing'
                ? (u.fixedWingController?.getUnitState?.(u)?.flightState || u.flightAttitude?.flightState)
                : null;
              let stateTxt = '';
              if (fwState) {
                stateTxt = (fwState === 'rtb' && !fwBingo)
                  ? (fwFuel?.rtbReason === 'winchester' ? '弹罄返航' : '返航')
                  : (FW_STATE_LABELS[fwState] || '盘旋');
              } else {
                const remQ = u.queue?.length || 0;
                const moving = u.waypoints.length - u.wpIndex > 0;
                stateTxt = u.commandStatus?.status === 'pending'
                  ? '准备路径'
                  : moving
                    ? (remQ > 0 ? `机动(${remQ + 1}段)` : '机动')
                    : (u.role === 'fixed_wing' ? '盘旋' : '');
              }
              if (fwFuel) {
                const pctInt = Math.round(fwFuel.fuelPct ?? 100);
                const fuelStr = fwBingo ? `BINGO ${pctInt}%` : `油${pctInt}%`;
                const winTxt = (fwWinchester && stateTxt !== '弹罄返航') ? ' · 弹罄' : '';
                if (item.isSelected || item.isHovered) {
                  nextMeta = `· ${stateTxt}${winTxt} · ${fuelStr}`;
                } else if (fwLowFuel && fwWinchester) {
                  nextMeta = `· 弹罄 · ${fuelStr}`;
                } else if (fwWinchester) {
                  nextMeta = stateTxt === '弹罄返航' ? '· 弹罄返航' : '· 弹罄';
                } else {
                  nextMeta = `· ${fuelStr}`;
                }
              } else {
                const altDisplay = Math.abs(u.node.position.y) < 0.5 ? '0' : u.node.position.y.toFixed(0);
                nextMeta = `· ${altDisplay}m${stateTxt ? ` · ${stateTxt}` : ''}`;
              }
            }
            if (cache.meta !== nextMeta) {
              u.metaSpan.textContent = nextMeta;
              cache.meta = nextMeta;
            }
          }
        }
      }
      this._updateFixedWingGroundShadows();
    } else if (this.airGroundShadowMesh) {
      this.airGroundShadowMesh.count = 0;
      this.airGroundShadowMesh.visible = false;
    }

    // 1.5 驱动 10Hz 战争迷雾双通道视野刷新（仅己方单位发射视野，零热路径 GC）
    this._fogLastNowSec = nowSeconds;
    if (this.fogOfWar) {
      this.fogOfWar.update({
        units: this._getActiveFogUnits(),
        nowSec: nowSeconds,
      });
    }

    // 2. 更新 10 / 100 混编单位连续机动与弹道（严密隔离，不运行逐车名牌）
    if (this.squadUnits.length > 0) {
      const dummy = this._tmpMat4;
      const te = dummy.elements;
      for (let i = 0; i < this.squadUnits.length; i++) {
        const u = this.squadUnits[i];
        const angle = nowSeconds * 0.55 + u.phase;
        const radius = u.kind === 'tank' ? 16.0 : 32.0;
        const nx = u.baseX + Math.cos(angle) * radius;
        const nz = u.baseZ + Math.sin(angle) * radius;
        const dx = nx - u.x;
        const dz = nz - u.z;
        u.x = nx;
        u.z = nz;
        if (Math.hypot(dx, dz) > 1e-4) {
          u.yaw = Math.atan2(dx, dz);
        }
        const gy = this.support.heightAt(u.x, u.z) || 0;
        u.y = u.kind === 'heli' ? gy + 24.0 : u.kind === 'jet' ? gy + 85.0 : gy;

        const cy = Math.cos(u.yaw);
        const sy = Math.sin(u.yaw);
        te[0] = cy; te[4] = 0; te[8] = sy; te[12] = u.x;
        te[1] = 0; te[5] = 1; te[9] = 0; te[13] = u.y;
        te[2] = -sy; te[6] = 0; te[10] = cy; te[14] = u.z;
        te[3] = 0; te[7] = 0; te[11] = 0; te[15] = 1;
        this.squadInstMesh.setMatrixAt(i, dummy);
      }
      this.squadInstMesh.instanceMatrix.needsUpdate = true;
    }

    if (this.projectiles.length > 0) {
      const dummy = this._tmpMat4;
      for (let i = 0; i < this.projectiles.length; i++) {
        const p = this.projectiles[i];
        p.progress = (p.progress + dt * 0.85) % 1.0;
        const px = p.x + Math.sin(nowSeconds * 2.0 + i) * 35.0;
        const pz = p.z + (p.progress - 0.5) * 90.0;
        dummy.makeTranslation(px, p.y, pz);
        this.tracerInstMesh.setMatrixAt(i, dummy);
      }
      this.tracerInstMesh.instanceMatrix.needsUpdate = true;
    }
  }
}
