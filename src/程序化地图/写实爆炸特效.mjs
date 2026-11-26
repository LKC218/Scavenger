/**
 * Scavenger 《Defilade》风格写实开火、三型差异化 5 层写实爆炸、低模弹体库与事件飞行特效引擎（V3.0）：
 * - 统一挂于 scene 顶层 `ScavengerVfxRoot`（严禁含 PointLight / SpotLight，天然与 Live PiP 隔离）；
 * - 严格锁定 ≤ 6 个预分配固定 DrawCall 池，热路径零运行期 Geometry/Material/对象/闭包分配与 GC 抖动；
 * - V3.0 全面写实化升级（P1 + P0 + P2）：
 *   1) P1 去风格化重塑：彻底移除池 2 科幻菲涅尔半球护盾与规则格子双圆环，改写为 Premultiplied Alpha 瞬时暖白爆闪核
 *      + 贴地假光晕 + 非发光贴地外卷破碎扬尘；池 3 `layerKind=1` 纯化为非发光深褐泥块；池 6 `kind=5` 收窄为细长冷白/灰白
 *      破片烟丝射流，新增 `kind=6` 极细 HDR 拉丝火花带（含 680p 最小像素宽防闪烁能量守恒钳位）；2–4 主瓣非对称簇状发射与
 *      三段式爆炸动力学（0–80ms 爆轰 -> 80–300ms 强阻尼骤停 -> 0.3s+ 浮力缓慢飘散）；
 *   2) P0 体积云质变：池 3 从 12 面体升级为全向 Billboard + 离线烘焙 8×8 多通道体积云图集（双帧插值法线/透明/热度/厚度/腐蚀，
 *      构造期同步 64×64 DataTexture 兜底，强制 NoColorSpace 与无 Mipmap），结合真实太阳方向自阴影、900K–4200K 黑体辐射曲线与
 *      光学厚度缝隙透光照亮外烟，年龄加权柔边溶解替代硬边 `discard`；
 *   3) P2 战场融合：接入 `setSceneDepth` 正交线性深度 Soft Particles 羽化、`setSunDirection` 真实太阳方向同步、宏观云团高度
 *      受光梯度、池 4 FBM 撕裂焦坑定向翻土与初期余烬热点，以及 `spawnDamageSmokePuff` 坠毁残骸长时斜风黑烟柱与底层跳跃明火。
 */

const MAX_ORDNANCE = 256;
const MAX_BLAST_SHELLS = 320;
const MAX_SMOKE_CHUNKS = 1536;
const MAJOR_CRATER_SLOTS = 192; // 重/中型弹坑专用持久区（GBU-31/AGM-65/Hydra/坠机）
const LIGHT_CRATER_SLOTS = 320; // 轻型弹点专用循环区（30mm 机炮与破片，永不覆盖重弹坑）
const MAX_CRATERS = MAJOR_CRATER_SLOTS + LIGHT_CRATER_SLOTS; // 512 槽，仍严格 1 DrawCall
const MAX_RIBBONS = 64;
const RIBBON_SEGMENTS = 64;
const MAX_CONE_FX = 384;
const MAX_FLARES = 64;
const VALID_TRACER_MODES = new Set(['red', 'green', 'mixed', 'none', 'auto']);

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function fract(x) {
  return x - Math.floor(x);
}

function hash11(p) {
  return fract(Math.sin(p * 127.1 + 311.7) * 43758.5453123);
}

function easeOutExpo(t, k = 6.0) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  return (1 - Math.exp(-k * t)) / (1 - Math.exp(-k));
}

/**
 * 纯规则：依据当前视距 span（米）返回三档特效 LOD 级别。
 */
export function resolveVfxLodTier(spanMeters) {
  const s = Number(spanMeters);
  if (!Number.isFinite(s) || s <= 450) return 'near';
  if (s <= 1200) return 'mid';
  return 'far';
}

const GLSL_TONEMAP_HELPER = /* glsl */`
  vec3 vfxAcesFilm(vec3 x) {
    float a = 2.51;
    float b = 0.03;
    float c = 2.43;
    float d = 0.59;
    float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
  }
  vec4 finalizeVfxColor(vec3 linCol, float alpha, float linearOut) {
    if (linearOut > 0.5) {
      return vec4(linCol, alpha);
    }
    vec3 mapped = pow(vfxAcesFilm(linCol * 1.18), vec3(1.0 / 2.2));
    return vec4(mapped, alpha);
  }
  vec4 finalizeVfxPremulColor(vec3 premulLinCol, float alpha, float linearOut) {
    if (linearOut > 0.5) {
      return vec4(premulLinCol, alpha);
    }
    if (alpha <= 0.001) {
      vec3 mappedAdd = pow(vfxAcesFilm(max(premulLinCol, vec3(0.0)) * 1.18), vec3(1.0 / 2.2));
      return vec4(mappedAdd, 0.0);
    }
    float safeA = max(alpha, 1e-4);
    vec3 straight = premulLinCol / safeA;
    vec3 mapped = pow(vfxAcesFilm(max(straight, vec3(0.0)) * 1.18), vec3(1.0 / 2.2));
    return vec4(mapped * alpha, alpha);
  }
`;

const GLSL_SOFT_DEPTH_HELPER = /* glsl */`
  float computeSoftParticleFade(
    sampler2D depthTex,
    float hasDepth,
    vec2 resolution,
    float camNear,
    float camFar,
    float featherMeters
  ) {
    if (hasDepth < 0.5) return 1.0;
    vec2 screenUv = gl_FragCoord.xy / max(resolution, vec2(1.0));
    float sceneDepth = texture2D(depthTex, clamp(screenUv, vec2(0.001), vec2(0.999))).r;
    float depthDiffMeters = (sceneDepth - gl_FragCoord.z) * max(1.0, camFar - camNear);
    return smoothstep(0.0, max(0.25, featherMeters), depthDiffMeters);
  }
`;

/**
 * 构造期同步生成 64×64 兜底多通道体积云 DataTexture（单格重复 8×8 采样兼容，首帧零黑块、零未绑定警告）。
 */
function createFallbackVolumeTextures(THREE) {
  const size = 64;
  const cell = 8; // 8×8 网格，每格 8×8 像素
  const normalData = new Uint8Array(size * size * 4);
  const heatData = new Uint8Array(size * size * 4);

  for (let row = 0; row < 8; row += 1) {
    for (let col = 0; col < 8; col += 1) {
      const frame = row * 8 + col;
      const t = frame / 63;
      const cool = clamp(1.0 - t * 1.25, 0.0, 1.0);
      for (let py = 0; py < cell; py += 1) {
        for (let px = 0; px < cell; px += 1) {
          const u = (px + 0.5) / cell;
          const v = (py + 0.5) / cell;
          const dx = u * 2.0 - 1.0;
          const dy = v * 2.0 - 1.0;
          const r = Math.hypot(dx, dy);
          const gx = col * cell + px;
          // DataTexture flipY=true 下，行 0 在底，确保顶行对应 row=0
          const gy = (7 - row) * cell + (cell - 1 - py);
          const idx = (gy * size + gx) * 4;

          if (r >= 0.85) {
            normalData[idx] = 128;
            normalData[idx + 1] = 128;
            normalData[idx + 2] = 255;
            normalData[idx + 3] = 0;
            heatData[idx] = 0;
            heatData[idx + 1] = 0;
            heatData[idx + 2] = Math.round(clamp(hash11(gx * 13.7 + gy * 7.3) * 220 + 20, 6, 250));
            heatData[idx + 3] = 255;
          } else {
            const rn = r / 0.85;
            const nz = Math.sqrt(Math.max(0.08, 1.0 - rn * rn));
            const invL = 1.0 / Math.hypot(dx, dy, nz);
            const alpha = Math.pow(clamp(1.0 - rn * rn, 0.0, 1.0), 1.35) * (0.95 - t * 0.25);
            const thick = clamp(Math.pow(1.0 - rn, 0.85), 0.05, 0.95);
            const seam = Math.abs(Math.sin(dx * 5.0 + frame * 0.12) * Math.cos(dy * 5.0 - frame * 0.09));
            const heat = clamp((1.0 - rn * 0.85) * cool + seam * 0.25 * cool, 0.0, 1.0);
            const erode = clamp(0.25 + 0.55 * (1.0 - rn * 0.5) + 0.20 * Math.sin(dx * 7.0 + dy * 6.0), 0.05, 0.96);

            normalData[idx] = Math.round(((dx * invL) * 0.5 + 0.5) * 255);
            normalData[idx + 1] = Math.round(((-dy * invL) * 0.5 + 0.5) * 255);
            normalData[idx + 2] = Math.round(((nz * invL) * 0.5 + 0.5) * 255);
            normalData[idx + 3] = Math.round(clamp(alpha, 0, 1) * 255);

            heatData[idx] = Math.round(heat * 255);
            heatData[idx + 1] = Math.round(thick * 255);
            heatData[idx + 2] = Math.round(erode * 255);
            heatData[idx + 3] = 255;
          }
        }
      }
    }
  }

  const configureLinearTex = (tex) => {
    tex.flipY = true;
    tex.colorSpace = THREE.NoColorSpace;
    tex.premultiplyAlpha = false;
    tex.generateMipmaps = false;
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.needsUpdate = true;
    return tex;
  };

  const normalAlphaTex = configureLinearTex(new THREE.DataTexture(normalData, size, size, THREE.RGBAFormat));
  const heatThickTex = configureLinearTex(new THREE.DataTexture(heatData, size, size, THREE.RGBAFormat));

  const dummyDepthData = new Uint8Array([255, 255, 255, 255]);
  const dummyDepthTex = new THREE.DataTexture(dummyDepthData, 1, 1, THREE.RGBAFormat);
  dummyDepthTex.minFilter = THREE.NearestFilter;
  dummyDepthTex.magFilter = THREE.NearestFilter;
  dummyDepthTex.generateMipmaps = false;
  dummyDepthTex.needsUpdate = true;

  return { normalAlphaTex, heatThickTex, dummyDepthTex };
}

export class RealisticVfxEngine {
  constructor({ THREE, scene, terrainSupport = null, linearOutput = true, atlasBaseUrl = '/public/assets/vfx-explosion' }) {
    if (!THREE || !scene) {
      throw new Error('RealisticVfxEngine 需要传入 { THREE, scene }');
    }
    this.THREE = THREE;
    this.scene = scene;
    this.terrainSupport = terrainSupport;
    this.linearOutput = Boolean(linearOutput);
    this.atlasBaseUrl = String(atlasBaseUrl || '/public/assets/vfx-explosion').replace(/\/+$/, '');
    this.vfxVersion = '3.0';

    // 统一挂于 scene 顶层的世界特效根节点（不含任何光源，与 Live PiP 天然隔离）
    this.vfxRoot = new THREE.Group();
    this.vfxRoot.name = 'ScavengerVfxRoot';
    this.scene.add(this.vfxRoot);

    // 预分配复用临时对象（热路径零运行期 GC）
    this._mat4 = new THREE.Matrix4();
    this._quat = new THREE.Quaternion();
    this._tmpQuat = new THREE.Quaternion();
    this._pos = new THREE.Vector3();
    this._scale = new THREE.Vector3();
    this._up = new THREE.Vector3(0, 1, 0);
    this._forwardAxis = new THREE.Vector3(0, 0, 1);
    this._dir = new THREE.Vector3();
    this._camLookDir = new THREE.Vector3(0, -0.74, -0.67).normalize();
    this._viewVec = new THREE.Vector3(0, 0.74, 0.67).normalize();
    this._color = new THREE.Color();
    this._projScreenMat = new THREE.Matrix4();
    this._frustum = new THREE.Frustum();
    this._cullSphere = new THREE.Sphere();
    this._hasCameraFrustum = false;
    this._lobeAngles = new Float32Array(4);
    this._lobeWeights = new Float32Array(4);

    // 默认太阳方向与主场景 镜头控制.mjs 一致：normalize(-0.85, +1.45, +0.55)
    this._sunDir = new THREE.Vector3(-0.85, 1.45, 0.55).normalize();

    // 公共共享 Uniforms（全池自动联动，零运行期查找）
    this._linearUniforms = [];
    this._sunDirUniforms = [];
    this._depthUniformTargets = [];
    this._uMeterPerPixel = { value: 260 / 680 };

    // 同步兜底 DataTexture + 异步离线 8×8 体积云图集
    const fallbacks = createFallbackVolumeTextures(THREE);
    this._fallbackNormalAlphaTex = fallbacks.normalAlphaTex;
    this._fallbackHeatThickTex = fallbacks.heatThickTex;
    this._dummyDepthTex = fallbacks.dummyDepthTex;
    this._loadedNormalAlphaTex = null;
    this._loadedHeatThickTex = null;
    this.atlasLoadedCount = 0;
    this.atlasReady = false;
    this.hasSceneDepth = false;

    this.spanMeters = 260;
    this.lodTier = 'near';
    this.frustumCulledCount = 0;
    this.activeExplosionLayers = new Set();
    this.contrailEmittedCount = 0;
    this.sonicBoomEmittedCount = 0;
    this.wingtipVortexActive = false;
    this.wingtipFadeAlpha = 0;
    this.flareActiveCount = 0;
    this.tracerColorMode = 'red';
    this.tracerEmittedRed = 0;
    this.tracerEmittedGreen = 0;
    this.fumerEmittedCount = 0;
    this.radialFragmentJetCount = 0;
    this.lightSplashRingCount = 0;
    this.apiImpactCount = 0;
    this.sparkStreakEmittedCount = 0;

    // 运行状态数组（全部定长预分配，热路径零堆分配）
    this._ordnanceFrameCount = 0;
    this._frameConeCount = 0;
    this._ribbonVertCursor = 0;
    this._coneWriteCount = 0;

    this._flareSlots = Array.from({ length: MAX_FLARES }, () => ({
      active: false,
      x: 0, y: 0, z: 0,
      vx: 0, vy: 0, vz: 0,
      age: 0,
      life: 2.1,
      ribbonKey: '',
    }));
    this._flareCursor = 0;
    this._flareSeq = 0;

    this._blastSlots = Array.from({ length: MAX_BLAST_SHELLS }, () => ({
      active: false,
      age: 0,
      life: 0.2,
      delay: 0,
      tag: '',
      layerKind: 0, // 0=暖白爆闪核(Additive), 3=贴地假光晕(Additive), 4=非发光贴地外卷破碎扬尘(Alpha)
      x: 0, y: 0, z: 0,
      baseScale: 1,
      endScale: 10,
      intensity: 1,
      seed: 0,
      rotY: 0,
    }));
    this._blastCursor = 0;

    this._smokeSlots = Array.from({ length: MAX_SMOKE_CHUNKS }, () => ({
      active: false,
      age: 0,
      life: 1.0,
      layerKind: 0, // 0=黑体火核, 1=非发光深褐泥块, 2=黑灰翻滚浓烟/斜风残烟, 3=导弹白灰烟, 4=机炮火药废气, 5=浅灰白气浪尘墙, 6=残骸明火瓣
      x: 0, y: 0, z: 0,
      vx: 0, vy: 0, vz: 0,
      windX: 0, windZ: 0,
      gravity: 0,
      drag: 1.2,
      burstDrag: 0,       // 三段式动力学前期强阻尼（80–300ms 急剧骤停）
      burstWindowSec: 0,  // 强阻尼持续窗口（秒）
      buoyancy: 0,        // 后期浮力加速（m/s^2）
      expGrowK: 0,        // >0 时使用 easeOutExpo 快速膨胀骤停曲线
      baseScale: 1,
      endScale: 4,
      rot: 0,
      rotSpeed: 0,
      seed: 0,
      clusterHeightNorm: 0.5,
      heatGain: 1.0,
    }));
    this._smokeCursor = 0;

    this._craterSlots = Array.from({ length: MAX_CRATERS }, () => ({
      active: false,
      x: 0, y: 0, z: 0,
      radius: 4,
      rotY: 0,
      seed: 0,
      ageSec: 0,
      intensity: 1,
    }));
    this._majorCraterCursor = 0;
    this._lightCraterCursor = 0;
    this._craterDirty = false;
    this._activeCraterCount = 0;
    this._activeMajorCraterCount = 0;
    this._activeLightCraterCount = 0;
    this._dedupedCraterCount = 0;
    this._uCraterSpan = { value: 260 };

    // 池 5：共享 Billboard 软边条带（连续共享顶点 Miter 拼接，零裂片零百叶窗）
    this._ribbons = Array.from({ length: MAX_RIBBONS }, () => ({
      active: false,
      key: '',
      kind: 'vortex',
      width: 0.42,
      endWidth: 0.9,
      alpha: 0,
      life: 1.0,
      age: 0,
      count: 0,
      points: new Float32Array(RIBBON_SEGMENTS * 3),
      pointAges: new Float32Array(RIBBON_SEGMENTS),
      lastEmitSec: -999,
    }));
    this._ribbonByKey = new Map();
    // 预分配零 GC 共享节点缓存（每个节点仅计算唯一一对左右边界点，相邻四边形严格共享顶点）
    this._ribLX = new Float32Array(RIBBON_SEGMENTS);
    this._ribLY = new Float32Array(RIBBON_SEGMENTS);
    this._ribLZ = new Float32Array(RIBBON_SEGMENTS);
    this._ribRX = new Float32Array(RIBBON_SEGMENTS);
    this._ribRY = new Float32Array(RIBBON_SEGMENTS);
    this._ribRZ = new Float32Array(RIBBON_SEGMENTS);
    this._ribA = new Float32Array(RIBBON_SEGMENTS);
    this._ribT = new Float32Array(RIBBON_SEGMENTS);

    // 池 6：持久事件槽位 + 每帧瞬时直推槽位
    // kind: 0=加力锥片, 1=音爆蒸汽锥, 2=固体火箭尾焰, 3=GAU-8枪口焰, 4=30mm红/绿HDR曳光与Fumer, 5=细长冷白破片烟丝, 6=极细HDR拉丝火花
    this._coneSlots = Array.from({ length: MAX_CONE_FX }, () => ({
      active: false,
      kind: 0,
      age: 0,
      life: 0.45,
      x: 0, y: 0, z: 0,
      vx: 0, vy: 0, vz: 0,
      gravity: 0,
      drag: 0,
      dx: 0, dy: 0, dz: 1,
      sx: 1, sy: 1, sz: 1,
      seed: 0,
    }));
    this._coneCursor = 0;

    this._frameCones = Array.from({ length: MAX_CONE_FX }, () => ({
      kind: 0,
      ageNorm: 0,
      x: 0, y: 0, z: 0,
      dx: 0, dy: 0, dz: 1,
      sx: 1, sy: 1, sz: 1,
      seed: 0,
    }));

    // 预分配零 GC 透明深度排序缓冲（池 2 / 池 3 / 池 6 远→近 + 层桶排序，杜绝遮挡反转与明火闪烁）
    this._blastOrder = new Int32Array(MAX_BLAST_SHELLS);
    this._blastKeys = new Float32Array(MAX_BLAST_SHELLS);
    this._smokeOrder = new Int32Array(MAX_SMOKE_CHUNKS);
    this._smokeKeys = new Float32Array(MAX_SMOKE_CHUNKS);
    this._coneOrder = new Int32Array(MAX_CONE_FX);
    this._coneKeys = new Float32Array(MAX_CONE_FX);
    this._coneMetaRad = new Float32Array(MAX_CONE_FX);
    this._coneMetaLen = new Float32Array(MAX_CONE_FX);
    this._coneMetaNorm = new Float32Array(MAX_CONE_FX);

    // 池 7（独立屏幕折射掩码场景，不加入主 vfxRoot，零增加常规 VFX DrawCall）：固定翼喷口热浪折射锥
    this.heatShimmerScene = new THREE.Scene();
    this.heatShimmerEnabled = true;
    this._maxHeatShimmers = 128;
    this._shimmerFrameCount = 0;
    this._shimmerActiveCount = 0;
    this._shimmerSlots = Array.from({ length: this._maxHeatShimmers }, () => ({
      x: 0, y: 0, z: 0,
      dx: 0, dy: 0, dz: -1,
      baseR: 0.78, tipR: 2.3, len: 8.5,
      distPx: 2.4, flowSpeed: 16.0, noiseFreq: 2.6, seed: 0,
    }));
    this._simClockSec = 0;
    this.lastFrameUsedDepth = false;

    this._initPool1Ordnance();
    this._initPool2BlastShells();
    this._initPool3SmokeChunks();
    this._initPool4Craters();
    this._initPool5SharedRibbon();
    this._initPool6ConeFx();
    this._initPool7HeatShimmer();

    this._loadVolumeAtlasesAsync();
  }

  _registerDepthUniforms(uniforms) {
    uniforms.uSceneDepthTex = { value: this._dummyDepthTex };
    uniforms.uHasSceneDepth = { value: 0.0 };
    uniforms.uResolution = { value: new this.THREE.Vector2(1208, 680) };
    uniforms.uCamNear = { value: 0.1 };
    uniforms.uCamFar = { value: 30000.0 };
    this._depthUniformTargets.push(uniforms);
  }

  /**
   * 异步加载 P0 离线烘焙 8×8 体积云双图集，强制线性色彩空间、关闭 Mipmap 与 PremultiplyAlpha。
   */
  _loadVolumeAtlasesAsync() {
    if (typeof window === 'undefined' || typeof Image === 'undefined' || !this.THREE?.TextureLoader) {
      return;
    }
    const loader = new this.THREE.TextureLoader();
    const applyTexParams = (tex) => {
      tex.flipY = true;
      tex.colorSpace = this.THREE.NoColorSpace;
      tex.premultiplyAlpha = false;
      tex.generateMipmaps = false;
      tex.minFilter = this.THREE.LinearFilter;
      tex.magFilter = this.THREE.LinearFilter;
      tex.wrapS = this.THREE.ClampToEdgeWrapping;
      tex.wrapT = this.THREE.ClampToEdgeWrapping;
      tex.needsUpdate = true;
      return tex;
    };

    const normUrl = `${this.atlasBaseUrl}/smoke_vol_normal_alpha.png`;
    const heatUrl = `${this.atlasBaseUrl}/smoke_vol_heat_thick.png`;

    loader.load(
      normUrl,
      (tex) => {
        if (this._disposed) {
          tex.dispose();
          return;
        }
        applyTexParams(tex);
        this._loadedNormalAlphaTex = tex;
        if (this._smokeUniforms?.uSmokeNormalAlphaTex) {
          this._smokeUniforms.uSmokeNormalAlphaTex.value = tex;
        }
        this.atlasLoadedCount += 1;
        this.atlasReady = Boolean(this._loadedNormalAlphaTex && this._loadedHeatThickTex);
      },
      undefined,
      () => { /* 加载失败静默保持同步 DataTexture 兜底 */ },
    );

    loader.load(
      heatUrl,
      (tex) => {
        if (this._disposed) {
          tex.dispose();
          return;
        }
        applyTexParams(tex);
        this._loadedHeatThickTex = tex;
        if (this._smokeUniforms?.uSmokeHeatThickTex) {
          this._smokeUniforms.uSmokeHeatThickTex.value = tex;
        }
        this.atlasLoadedCount += 1;
        this.atlasReady = Boolean(this._loadedNormalAlphaTex && this._loadedHeatThickTex);
      },
      undefined,
      () => { /* 加载失败静默保持同步 DataTexture 兜底 */ },
    );
  }

  /**
   * 同步主场景太阳方向（保证爆炸烟云与弹坑翻土的明暗受光面与建筑/树木严格同向）。
   */
  setSunDirection(vec3) {
    if (!vec3) return;
    const x = Number(vec3.x);
    const y = Number(vec3.y);
    const z = Number(vec3.z);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
    const len = Math.hypot(x, y, z);
    if (len < 1e-5) return;
    this._sunDir.set(x / len, y / len, z / len);
    for (let i = 0; i < this._sunDirUniforms.length; i += 1) {
      this._sunDirUniforms[i].value.copy(this._sunDir);
    }
  }

  /**
   * 绑定或解绑只读场景深度纹理（P2 正交线性 Soft Particles 接地羽化；传 null 时自动平滑退化为 1.0）。
   */
  setSceneDepth(depthTexture, camera = null, width = 1208, height = 680) {
    const enabled = Boolean(depthTexture);
    this.hasSceneDepth = enabled;
    if (enabled) this.lastFrameUsedDepth = true;
    const tex = enabled ? depthTexture : this._dummyDepthTex;
    const w = Math.max(1, Number(width) || 1208);
    const h = Math.max(1, Number(height) || 680);
    const near = Number.isFinite(camera?.near) ? camera.near : 0.1;
    const far = Number.isFinite(camera?.far) ? camera.far : 30000.0;
    for (let i = 0; i < this._depthUniformTargets.length; i += 1) {
      const u = this._depthUniformTargets[i];
      u.uSceneDepthTex.value = tex;
      u.uHasSceneDepth.value = enabled ? 1.0 : 0.0;
      u.uResolution.value.set(w, h);
      u.uCamNear.value = near;
      u.uCamFar.value = far;
    }
  }

  /**
   * 零分配热路径判断：是否存在任意活跃 VFX 池需要叠加渲染（替代渲染循环高频调用 getStats()）。
   */
  hasActiveVfx() {
    return (
      (this.ordnancePool.visible && this.ordnancePool.count > 0)
      || (this.blastShellPool.visible && this.blastShellPool.count > 0)
      || (this.smokeChunkPool.visible && this.smokeChunkPool.count > 0)
      || (this.craterPool.visible && this.craterPool.count > 0)
      || Boolean(this.sharedRibbonMesh.visible)
      || (this.coneFxPool.visible && this.coneFxPool.count > 0)
    );
  }

  /**
   * 开关固定翼喷口热浪折射特效（默认开启；关闭后零收集、零提交）。
   */
  setHeatShimmerEnabled(flag = true) {
    this.heatShimmerEnabled = Boolean(flag);
    if (!this.heatShimmerEnabled) {
      this._shimmerFrameCount = 0;
      this._shimmerActiveCount = 0;
      if (this.heatShimmerPool) {
        this.heatShimmerPool.count = 0;
        this.heatShimmerPool.visible = false;
      }
    }
    return this.heatShimmerEnabled;
  }

  getHeatShimmerEnabled() {
    return Boolean(this.heatShimmerEnabled);
  }

  /**
   * 零分配热路径判断：当前帧是否存在可见的固定翼喷口热浪折射锥（战略高空 > 1800m 自动关闭省性能）。
   */
  hasActiveHeatShimmer() {
    return Boolean(this.heatShimmerEnabled && this.heatShimmerPool?.visible && this._shimmerActiveCount > 0 && this.spanMeters <= 1800);
  }

  getHeatShimmerCount() {
    return this.heatShimmerEnabled ? (this._shimmerActiveCount || 0) : 0;
  }

  /**
   * 池 7：初始化固定翼喷口热浪折射锥 InstancedMesh（1 DrawCall，写入独立 heatShimmerScene 掩码缓冲）。
   */
  _initPool7HeatShimmer() {
    const { THREE } = this;
    // 单位开口圆柱轴线默认沿 +Y（-0.5..+0.5），rotateX(+π/2) + translate(+0.5Z) 后：
    // z=0（uv.y=0）严格对应喷口根部，z=1（uv.y=1）严格对应尾端；截锥半径与长度由 aShimmerGeo 逐实例精确塑形
    const geo = new THREE.CylinderGeometry(1.0, 1.0, 1.0, 12, 1, true);
    geo.rotateX(Math.PI * 0.5);
    geo.translate(0, 0, 0.5);

    const paramsArr = new Float32Array(this._maxHeatShimmers * 4);
    this._shimmerAttrParams = new THREE.InstancedBufferAttribute(paramsArr, 4);
    this._shimmerAttrParams.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('aShimmerParams', this._shimmerAttrParams);

    const geoArr = new Float32Array(this._maxHeatShimmers * 3);
    this._shimmerAttrGeo = new THREE.InstancedBufferAttribute(geoArr, 3);
    this._shimmerAttrGeo.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('aShimmerGeo', this._shimmerAttrGeo);

    const uniforms = {
      uTime: { value: 0 },
      uSpanMeters: { value: 260 },
    };
    this._registerDepthUniforms(uniforms);
    this._shimmerUniforms = uniforms;

    const mat = new THREE.ShaderMaterial({
      uniforms,
      transparent: false,
      depthTest: false,
      depthWrite: false,
      side: THREE.FrontSide,
      vertexShader: /* glsl */ `
        attribute vec4 aShimmerParams;
        attribute vec3 aShimmerGeo;
        varying vec2 vUv;
        varying vec3 vViewNormal;
        varying vec4 vParams;
        void main() {
          vUv = uv;
          vParams = aShimmerParams;
          float tAlong = clamp(position.z, 0.0, 1.0);
          float baseR = max(0.12, aShimmerGeo.x);
          float tipR = max(baseR, aShimmerGeo.y);
          float coneLen = max(1.0, aShimmerGeo.z);
          float rad = mix(baseR, tipR, tAlong);
          vec3 localPos = vec3(position.xy * rad, tAlong * coneLen);
          // 纯刚体 instanceMatrix（缩放恒为 1）+ 解析截锥外法线，彻底消除非等比缩放法线畸变
          vec3 localNormal = normalize(vec3(normal.xy * coneLen, -(tipR - baseR)));
          vec4 mvPos = modelViewMatrix * instanceMatrix * vec4(localPos, 1.0);
          mat3 instRot = mat3(instanceMatrix);
          vViewNormal = normalize(normalMatrix * (instRot * localNormal));
          gl_Position = projectionMatrix * mvPos;
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uSceneDepthTex;
        uniform float uHasSceneDepth;
        uniform vec2 uResolution;
        uniform float uCamNear;
        uniform float uCamFar;
        uniform float uTime;
        uniform float uSpanMeters;
        varying vec2 vUv;
        varying vec3 vViewNormal;
        varying vec4 vParams;

        float shimmerHash(vec2 p) {
          p = fract(p * vec2(123.34, 456.21));
          p += dot(p, p + 45.32);
          return fract(p.x * p.y);
        }

        float shimmerNoise(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          float a = shimmerHash(i);
          float b = shimmerHash(i + vec2(1.0, 0.0));
          float c = shimmerHash(i + vec2(0.0, 1.0));
          float d = shimmerHash(i + vec2(1.0, 1.0));
          return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
        }

        void main() {
          // 战略高空平滑淡出：span > 1800m 直接丢弃
          float spanFade = 1.0 - smoothstep(900.0, 1800.0, uSpanMeters);
          if (spanFade <= 0.005) discard;

          // 精确米制正交深度遮挡（far-near=30000m，避免固定 NDC 容差放大为数米穿模）
          vec2 screenUv = gl_FragCoord.xy / max(uResolution, vec2(1.0));
          if (uHasSceneDepth > 0.5) {
            float sceneDepth = texture2D(uSceneDepthTex, clamp(screenUv, vec2(0.001), vec2(0.999))).r;
            float depthRange = max(1.0, uCamFar - uCamNear);
            float behindOpaqueM = (gl_FragCoord.z - sceneDepth) * depthRange;
            if (behindOpaqueM > 0.15) discard;
          }

          // CylinderGeometry 旋转平移后 vUv.y=0 严格对应喷口根部(z=0)、vUv.y=1 对应尾端(z=1)
          float along = clamp(vUv.y, 0.0, 1.0);
          float axialFade = smoothstep(0.0, 0.10, along) * (1.0 - smoothstep(0.50, 1.0, along));
          float fresnel = pow(clamp(abs(normalize(vViewNormal).z), 0.0, 1.0), 0.75);
          float envelope = axialFade * fresnel * spanFade;
          if (envelope <= 0.01) discard;

          float distPx = vParams.x;
          float flowSpd = vParams.y;
          float freq = vParams.z;
          float seed = vParams.w;

          // 680p 中远景可见度补偿（220m–900m 略微放大像素波幅防量化吞噬）
          float midBoost = mix(1.0, 1.40, smoothstep(220.0, 760.0, uSpanMeters));

          // 沿排气轴从喷口(along=0)高速向尾端(along=1)平流，提升噪声有效幅值使 distPx 切实兑现为屏幕折射位移
          vec2 flowUv1 = vec2(vUv.x * freq * 2.2 + seed, along * freq * 3.8 - uTime * flowSpd * 0.45);
          vec2 flowUv2 = vec2(vUv.x * freq * 4.4 - seed * 1.7, along * freq * 6.8 - uTime * flowSpd * 0.78);
          float n1 = shimmerNoise(flowUv1) * 2.0 - 1.0;
          float n2 = shimmerNoise(flowUv2) * 2.0 - 1.0;
          float n3 = shimmerNoise(flowUv1 + vec2(3.1, 1.7)) * 2.0 - 1.0;

          vec2 dir = clamp(vec2(n1 * 0.72 + n2 * 0.48, n3 * 0.72 - n2 * 0.48) * 1.55, vec2(-1.0), vec2(1.0));
          float ampNorm = clamp((distPx * midBoost * envelope) / 10.0, 0.0, 1.0);
          vec2 enc = clamp(dir * ampNorm * 0.5 + 0.5, 0.0, 1.0);
          // R/G: 编码屏幕偏移向量，B: 有效强度，A: 热流深度（供合成 Pass 防前景轮廓拉丝）
          gl_FragColor = vec4(enc.x, enc.y, ampNorm, gl_FragCoord.z);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, this._maxHeatShimmers);
    mesh.name = 'vfx_pool7_heat_shimmer';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.heatShimmerPool = mesh;
    this.heatShimmerScene.add(mesh);
  }

  /**
   * 固定翼每帧零分配推流单个引擎喷口热浪发射器（由 FixedWingBaseController.step 调用）。
   */
  pushHeatShimmerEmitter(
    worldPos,
    backDir,
    baseRadiusM = 0.78,
    tipRadiusM = 2.3,
    lengthM = 8.5,
    distortionPx = 2.4,
    flowSpeed = 16.0,
    noiseFreq = 2.6,
    seed = 0,
  ) {
    if (!this.heatShimmerEnabled || !worldPos || !backDir || this._shimmerFrameCount >= this._maxHeatShimmers) return;
    if (this.spanMeters > 1800) return;
    const slot = this._shimmerSlots[this._shimmerFrameCount];
    this._shimmerFrameCount += 1;
    slot.x = worldPos.x;
    slot.y = worldPos.y;
    slot.z = worldPos.z;
    slot.dx = backDir.x;
    slot.dy = backDir.y;
    slot.dz = backDir.z;
    slot.baseR = baseRadiusM;
    slot.tipR = tipRadiusM;
    slot.len = lengthM;
    slot.distPx = distortionPx;
    slot.flowSpeed = flowSpeed;
    slot.noiseFreq = noiseFreq;
    slot.seed = seed;
  }

  /**
   * 切换 30mm 曳光弹全局配色模式：'red'（北约红 4:1）| 'green'（俄系绿 4:1）| 'mixed'（红绿混编 4:1）| 'none'（纯写实无曳光 Fumer）| 'auto'（读取弹药自身属性）。
   */
  setTracerColorMode(mode) {
    this.tracerColorMode = VALID_TRACER_MODES.has(mode) ? mode : 'red';
    return this.tracerColorMode;
  }

  /**
   * 查询当前 30mm 曳光弹配色模式。
   */
  getTracerColorMode() {
    return this.tracerColorMode;
  }

  /**
   * 切换 HDR Bloom（线性离屏缓冲，交由 OutputPass 统一 ACES+sRGB）与直渲 sRGB 模式，保证明暗一致。
   */
  setBloomLinearMode(isLinear) {
    this.linearOutput = Boolean(isLinear);
    const val = this.linearOutput ? 1.0 : 0.0;
    for (let i = 0; i < this._linearUniforms.length; i += 1) {
      this._linearUniforms[i].value = val;
    }
    if (this.ordnancePool?.material) {
      const wantToneMap = !this.linearOutput;
      if (this.ordnancePool.material.toneMapped !== wantToneMap) {
        this.ordnancePool.material.toneMapped = wantToneMap;
        this.ordnancePool.material.needsUpdate = true;
      }
    }
  }

  /**
   * 池 1：低模飞行弹体（AGM-65 / Hydra / JDAM / 30mm 实弹头）与超亮 HDR 热诱弹（1 DrawCall InstancedMesh）。
   */
  _initPool1Ordnance() {
    const { THREE } = this;
    const geo = new THREE.CylinderGeometry(0.18, 0.22, 1.0, 6);
    geo.rotateX(Math.PI / 2); // 沿 +Z 对齐速度方向
    const mat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      toneMapped: !this.linearOutput,
    });
    const mesh = new THREE.InstancedMesh(geo, mat, MAX_ORDNANCE);
    mesh.name = 'vfx_pool_1_ordnance';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // 构造期预置 instanceColor 缓冲，防止首弹发射时触发材质 USE_INSTANCING_COLOR 重编译卡顿
    this._color.setHex(0xffffff);
    mesh.setColorAt(0, this._color);
    if (mesh.instanceColor) mesh.instanceColor.setUsage(THREE.DynamicDrawUsage);
    this.ordnancePool = mesh;
    this.vfxRoot.add(mesh);
  }

  /**
   * 池 2：Premultiplied Alpha 瞬时暖白爆闪核（kind=0）+ 贴地假光晕（kind=3）+ 非发光贴地外卷破碎扬尘气浪（kind=4）。
   * 彻底移除 V2.4 的科幻半球护盾（旧 kind=1）与规则格子双同心发光环（旧 kind=2）。
   */
  _initPool2BlastShells() {
    const { THREE } = this;
    const geo = new THREE.PlaneGeometry(2.0, 2.0, 1, 1);
    const aKind = new Float32Array(MAX_BLAST_SHELLS);
    const aAge = new Float32Array(MAX_BLAST_SHELLS);
    const aInt = new Float32Array(MAX_BLAST_SHELLS);
    const aSeed = new Float32Array(MAX_BLAST_SHELLS);
    const aMeta = new Float32Array(MAX_BLAST_SHELLS * 2); // x=scaleMeters, y=rotY
    geo.setAttribute('aLayerKind', new THREE.InstancedBufferAttribute(aKind, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aAgeNorm', new THREE.InstancedBufferAttribute(aAge, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aIntensity', new THREE.InstancedBufferAttribute(aInt, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aSeed', new THREE.InstancedBufferAttribute(aSeed, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aMeta', new THREE.InstancedBufferAttribute(aMeta, 2).setUsage(THREE.DynamicDrawUsage));

    const uLinearOutput = { value: this.linearOutput ? 1.0 : 0.0 };
    const uSunDir = { value: this._sunDir.clone() };
    this._linearUniforms.push(uLinearOutput);
    this._sunDirUniforms.push(uSunDir);

    const uniforms = { uLinearOutput, uSunDir };
    this._registerDepthUniforms(uniforms);

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      side: THREE.DoubleSide,
      uniforms,
      vertexShader: /* glsl */`
        attribute float aLayerKind;
        attribute float aAgeNorm;
        attribute float aIntensity;
        attribute float aSeed;
        attribute vec2 aMeta;
        varying float vKind;
        varying float vAge;
        varying float vInt;
        varying float vSeed;
        varying float vScale;
        varying vec2 vLocalXY;

        void main() {
          vKind = aLayerKind;
          vAge = aAgeNorm;
          vInt = aIntensity;
          vSeed = aSeed;
          vScale = aMeta.x;
          vLocalXY = position.xy;

          vec3 centerW = (instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
          vec3 worldPos;
          if (aLayerKind < 0.5) {
            // kind=0: 全向相机对齐 Billboard 瞬时暖白爆闪核
            vec3 camRight = vec3(viewMatrix[0][0], viewMatrix[1][0], viewMatrix[2][0]);
            vec3 camUp = vec3(viewMatrix[0][1], viewMatrix[1][1], viewMatrix[2][1]);
            float c = cos(aMeta.y);
            float s = sin(aMeta.y);
            vec2 rp = vec2(position.x * c - position.y * s, position.x * s + position.y * c);
            worldPos = centerW + (camRight * rp.x + camUp * rp.y) * aMeta.x;
          } else {
            // kind=3 (贴地假光晕) & kind=4 (贴地非发光破碎外卷扬尘气浪): 水平铺展圆盘
            float c = cos(aMeta.y);
            float s = sin(aMeta.y);
            vec2 rp = vec2(position.x * c - position.y * s, position.x * s + position.y * c);
            worldPos = centerW + vec3(rp.x * aMeta.x, 0.0, rp.y * aMeta.x);
          }

          gl_Position = projectionMatrix * viewMatrix * vec4(worldPos, 1.0);
        }
      `,
      fragmentShader: /* glsl */`
        uniform float uLinearOutput;
        uniform vec3 uSunDir;
        uniform sampler2D uSceneDepthTex;
        uniform float uHasSceneDepth;
        uniform vec2 uResolution;
        uniform float uCamNear;
        uniform float uCamFar;
        varying float vKind;
        varying float vAge;
        varying float vInt;
        varying float vSeed;
        varying float vScale;
        varying vec2 vLocalXY;

        ${GLSL_TONEMAP_HELPER}
        ${GLSL_SOFT_DEPTH_HELPER}

        float hash21(vec2 p) {
          p = fract(p * vec2(123.34, 456.21));
          p += dot(p, p + 45.32);
          return fract(p.x * p.y);
        }

        float noise2(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          float a = hash21(i);
          float b = hash21(i + vec2(1.0, 0.0));
          float c = hash21(i + vec2(0.0, 1.0));
          float d = hash21(i + vec2(1.0, 1.0));
          return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
        }

        float fbm2(vec2 p) {
          float s = 0.0;
          float amp = 0.52;
          for (int i = 0; i < 3; i++) {
            s += noise2(p) * amp;
            p = p * 2.07 + vec2(13.1, 7.7);
            amp *= 0.5;
          }
          return s;
        }

        void main() {
          float r = length(vLocalXY);
          if (r > 1.0) discard;

          if (vKind < 0.5) {
            // 层 1：瞬时白橙过曝爆闪核（极短高能脉冲；关 Bloom 直渲施加陡峭羽化与 FBM 破碎边界，杜绝 sRGB Gamma 抬升出正圆亮盘）
            float ang = atan(vLocalXY.y, vLocalXY.x);
            float nFlash = fbm2(vLocalXY * 4.2 + vec2(vSeed * 5.7, -vSeed * 3.1));
            float rays = 0.78 + 0.22 * sin(ang * 7.0 + vSeed * 5.0) * cos(ang * 4.0 - vSeed * 3.0);
            float rBreak = clamp(r + (nFlash - 0.5) * 0.22, 0.0, 1.0);
            float edgeFalloff = smoothstep(0.96, 0.14, rBreak);
            float expCore = mix(3.6, 2.45, uLinearOutput);
            float core = pow(max(0.0, 1.0 - rBreak), expCore) * rays * edgeFalloff;
            float halo = pow(max(0.0, 1.0 - rBreak), mix(4.2, 1.85, uLinearOutput)) * mix(0.10, 0.22, uLinearOutput) * edgeFalloff;
            float fade = pow(max(0.0, 1.0 - vAge), 2.35);
            float softF = computeSoftParticleFade(uSceneDepthTex, uHasSceneDepth, uResolution, uCamNear, uCamFar, clamp(vScale * 0.48, 2.2, 8.5));
            float directAtten = mix(0.58, 1.0, uLinearOutput);
            float w = (core + halo) * fade * vInt * softF * directAtten;
            if (w < 0.01) discard;
            vec3 col = mix(vec3(3.6, 1.35, 0.28), vec3(6.2, 5.1, 3.5), smoothstep(0.18, 0.82, core));
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          if (vKind < 3.5) {
            // 层 1：贴地假光晕圆盘（0 PointLight 照亮爆心周边地表与余烬暖红，alpha=0.0 精确 Additive；直渲下平滑压暗外缘避免正圆光斑）
            float nGlow = fbm2(vLocalXY * 3.4 + vec2(vSeed * 4.3, vSeed * 2.9));
            float rGlow = clamp(r + (nGlow - 0.5) * 0.18, 0.0, 1.0);
            float glow = pow(max(0.0, 1.0 - rGlow), mix(3.8, 2.45, uLinearOutput)) * smoothstep(0.95, 0.12, rGlow);
            float fade = pow(max(0.0, 1.0 - vAge), 1.9);
            float w = glow * fade * mix(0.28, 0.56, uLinearOutput) * vInt;
            if (w < 0.008) discard;
            vec3 col = mix(vec3(2.4, 0.72, 0.14), vec3(3.8, 1.85, 0.60), pow(max(0.0, 1.0 - vAge), 1.3));
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          // 层 2（kind=4）：非发光贴地外卷破碎扬尘气浪（强非对称撕裂边界，彻底消除正圆灰盘感）
          float ang = atan(vLocalXY.y, vLocalXY.x);
          vec2 dir = vLocalXY / max(r, 1e-4);
          float lobeWarp = 0.16 * sin(ang * 3.0 + vSeed * 4.1)
            + 0.11 * cos(ang * 5.0 - vSeed * 2.7)
            + 0.07 * sin(ang * 7.0 + vSeed * 8.3);
          float nBillow = fbm2(vLocalXY * 4.8 - dir * (vAge * 2.6) + vec2(vSeed * 9.3, vSeed * 4.7));
          float nBreak = fbm2(vLocalXY * 9.2 + vec2(vSeed * 3.1, -vSeed * 6.4));
          float rWarped = r + lobeWarp + (nBillow - 0.5) * 0.34;
          float frontBand = smoothstep(0.24, 0.72, rWarped) * (1.0 - smoothstep(0.72, 1.0, rWarped));
          float wisps = smoothstep(0.28, 0.82, nBillow * 0.65 + nBreak * 0.45);
          float density = clamp(frontBand * wisps * 1.15, 0.0, 1.0);
          float fade = smoothstep(0.0, 0.10, vAge) * pow(max(0.0, 1.0 - vAge), 1.35);
          float softF = computeSoftParticleFade(uSceneDepthTex, uHasSceneDepth, uResolution, uCamNear, uCamFar, clamp(vScale * 0.04, 0.25, 0.9));
          float alpha = clamp(density * fade * 0.42 * vInt * softF, 0.0, 0.55);
          if (alpha < 0.012) discard;

          // 按主太阳方向对贴地尘浪施加微弱方向性明暗层次（线性色严格 <= 0.36，绝不触发 Bloom）
          vec3 pseudoN = normalize(vec3(dir.x * 0.48, 0.88, dir.y * 0.48));
          vec3 safeSunDir = length(uSunDir) > 1e-4 ? normalize(uSunDir) : vec3(-0.48, 0.82, 0.31);
          float sunLit = clamp(dot(pseudoN, safeSunDir) * 0.45 + 0.55, 0.32, 1.0);
          vec3 soilDust = vec3(0.16, 0.145, 0.125);
          vec3 paleWave = vec3(0.34, 0.33, 0.305);
          vec3 dustCol = mix(soilDust, paleWave, clamp(frontBand * 0.7 + nBillow * 0.35, 0.0, 1.0)) * sunLit;
          gl_FragColor = finalizeVfxPremulColor(dustCol * alpha, alpha, uLinearOutput);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, MAX_BLAST_SHELLS);
    mesh.name = 'vfx_pool_2_blast_shells';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.renderOrder = 14;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.blastShellPool = mesh;
    this._blastAttrKind = geo.getAttribute('aLayerKind');
    this._blastAttrAge = geo.getAttribute('aAgeNorm');
    this._blastAttrInt = geo.getAttribute('aIntensity');
    this._blastAttrSeed = geo.getAttribute('aSeed');
    this._blastAttrMeta = geo.getAttribute('aMeta');
    this.vfxRoot.add(mesh);
  }

  /**
   * 池 3：P0 8×8 多通道体积云 Billboard + 方向性太阳自阴影 + 黑体辐射缝隙内焰 + 柔边溶解 + Soft Particles（1 DrawCall）。
   */
  _initPool3SmokeChunks() {
    const { THREE } = this;
    const geo = new THREE.PlaneGeometry(1.0, 1.0, 1, 1);
    const aKind = new Float32Array(MAX_SMOKE_CHUNKS);
    const aAge = new Float32Array(MAX_SMOKE_CHUNKS);
    const aSeed = new Float32Array(MAX_SMOKE_CHUNKS);
    const aMeta = new Float32Array(MAX_SMOKE_CHUNKS * 4); // x=rotRad, y=scaleM, z=clusterHeightNorm, w=heatGain
    geo.setAttribute('aLayerKind', new THREE.InstancedBufferAttribute(aKind, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aAgeNorm', new THREE.InstancedBufferAttribute(aAge, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aSeed', new THREE.InstancedBufferAttribute(aSeed, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aMeta', new THREE.InstancedBufferAttribute(aMeta, 4).setUsage(THREE.DynamicDrawUsage));

    const uLinearOutput = { value: this.linearOutput ? 1.0 : 0.0 };
    const uSunDir = { value: this._sunDir.clone() };
    this._linearUniforms.push(uLinearOutput);
    this._sunDirUniforms.push(uSunDir);

    const uniforms = {
      uSunDir,
      uLinearOutput,
      uSmokeNormalAlphaTex: { value: this._fallbackNormalAlphaTex },
      uSmokeHeatThickTex: { value: this._fallbackHeatThickTex },
    };
    this._registerDepthUniforms(uniforms);
    this._smokeUniforms = uniforms;

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      side: THREE.DoubleSide,
      uniforms,
      vertexShader: /* glsl */`
        attribute float aLayerKind;
        attribute float aAgeNorm;
        attribute float aSeed;
        attribute vec4 aMeta;
        varying float vKind;
        varying float vAge;
        varying float vSeed;
        varying vec2 vLocalUv;
        varying float vRot;
        varying float vFlipX;
        varying float vScale;
        varying float vClusterHeightNorm;
        varying float vHeatGain;
        varying vec3 vCamRight;
        varying vec3 vCamUp;
        varying vec3 vCamFwd;

        void main() {
          vKind = aLayerKind;
          vAge = aAgeNorm;
          vSeed = aSeed;
          vRot = aMeta.x;
          vScale = aMeta.y;
          vClusterHeightNorm = aMeta.z;
          vHeatGain = aMeta.w;
          vFlipX = fract(aSeed * 17.31) > 0.5 ? -1.0 : 1.0;

          vLocalUv = vec2(vFlipX > 0.0 ? uv.x : (1.0 - uv.x), uv.y);

          // 从 viewMatrix 提取正交/透视通用的相机世界正交基
          vec3 camRight = normalize(vec3(viewMatrix[0][0], viewMatrix[1][0], viewMatrix[2][0]));
          vec3 camUp = normalize(vec3(viewMatrix[0][1], viewMatrix[1][1], viewMatrix[2][1]));
          vec3 camFwd = normalize(vec3(viewMatrix[0][2], viewMatrix[1][2], viewMatrix[2][2]));
          vCamRight = camRight;
          vCamUp = camUp;
          vCamFwd = camFwd;

          float c = cos(vRot);
          float s = sin(vRot);
          vec2 rp = vec2(position.x * c - position.y * s, position.x * s + position.y * c);
          // 泥块保留轻微纵横拉长感
          if (aLayerKind > 0.5 && aLayerKind < 1.5) {
            rp.y *= 1.18;
          }

          vec3 centerW = (instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
          vec3 worldPos = centerW + (camRight * rp.x + camUp * rp.y) * vScale;
          gl_Position = projectionMatrix * viewMatrix * vec4(worldPos, 1.0);
        }
      `,
      fragmentShader: /* glsl */`
        uniform vec3 uSunDir;
        uniform float uLinearOutput;
        uniform sampler2D uSmokeNormalAlphaTex;
        uniform sampler2D uSmokeHeatThickTex;
        uniform sampler2D uSceneDepthTex;
        uniform float uHasSceneDepth;
        uniform vec2 uResolution;
        uniform float uCamNear;
        uniform float uCamFar;

        varying float vKind;
        varying float vAge;
        varying float vSeed;
        varying vec2 vLocalUv;
        varying float vRot;
        varying float vFlipX;
        varying float vScale;
        varying float vClusterHeightNorm;
        varying float vHeatGain;
        varying vec3 vCamRight;
        varying vec3 vCamUp;
        varying vec3 vCamFwd;

        ${GLSL_TONEMAP_HELPER}
        ${GLSL_SOFT_DEPTH_HELPER}

        vec2 computeAtlasUV(float frameIdx, vec2 localUv) {
          float f = clamp(floor(frameIdx + 0.5), 0.0, 63.0);
          float col = mod(f, 8.0);
          float row = floor(f / 8.0);
          return vec2((col + localUv.x) / 8.0, ((7.0 - row) + localUv.y) / 8.0);
        }

        // 连续物理黑体辐射色温映射（900K 暗红余烬 -> 2200K 炽橘 -> 3800K 白炽核心）
        vec3 blackbodyColor(float t) {
          vec3 emberRed = vec3(1.15, 0.16, 0.02);
          vec3 hotOrange = vec3(3.45, 1.08, 0.14);
          vec3 whiteHot = vec3(5.65, 4.15, 2.15);
          vec3 c1 = mix(emberRed, hotOrange, smoothstep(0.0, 0.58, t));
          return mix(c1, whiteHot, smoothstep(0.54, 1.0, t));
        }

        void main() {
          // --- 分支 A：layerKind=1 非发光深褐泥土碎石块（彻底脱离发光宝石火星） ---
          if (vKind > 0.5 && vKind < 1.5) {
            vec2 p = vLocalUv * 2.0 - 1.0;
            float r = length(p);
            if (r > 0.96) discard;
            float ang = atan(p.y, p.x);
            float clodShape = 0.62
              + 0.16 * sin(ang * 3.0 + vSeed * 7.1)
              + 0.11 * cos(ang * 5.0 - vSeed * 4.3)
              + 0.07 * sin(ang * 7.0 + vSeed * 11.7);
            float edge = 1.0 - smoothstep(clodShape - 0.14, clodShape + 0.04, r);
            float fade = mix(1.0, 0.0, smoothstep(0.76, 1.0, vAge));
            float alpha = edge * fade * 0.96;
            if (alpha < 0.02) discard;

            // 碎块柔和法线 + 真实主太阳方向受光（严格低饱和暗褐/黑灰泥块，绝不进 Bloom）
            vec2 nXY = p / max(clodShape, 0.25);
            float nz = sqrt(max(0.12, 1.0 - clamp(dot(nXY, nXY), 0.0, 0.92)));
            vec3 nWorld = normalize(vCamRight * (nXY.x * vFlipX) + vCamUp * nXY.y + vCamFwd * nz);
            float sunLit = clamp(dot(nWorld, normalize(uSunDir)) * 0.52 + 0.48, 0.24, 1.0);
            vec3 darkEarth = vec3(0.026, 0.020, 0.015);
            vec3 dryMud = vec3(0.072, 0.056, 0.040);
            vec3 dirtCol = mix(darkEarth, dryMud, fract(vSeed * 9.13)) * sunLit;
            gl_FragColor = finalizeVfxPremulColor(dirtCol * alpha, alpha, uLinearOutput);
            return;
          }

          // --- 分支 B：8×8 多通道体积烟云双帧混合采样 ---
          float startFrame = fract(vSeed * 3.71) * 5.0;
          float frameSpan = (vKind < 0.5 || vKind > 5.5) ? 46.0 : 58.0;
          float frameF = clamp(startFrame + vAge * frameSpan, 0.0, 63.0);
          float f0 = floor(frameF);
          float f1 = min(63.0, f0 + 1.0);
          float fBlend = fract(frameF);

          vec2 uv0 = computeAtlasUV(f0, vLocalUv);
          vec2 uv1 = computeAtlasUV(f1, vLocalUv);

          vec4 na0 = texture2D(uSmokeNormalAlphaTex, uv0);
          vec4 na1 = texture2D(uSmokeNormalAlphaTex, uv1);
          vec4 na = mix(na0, na1, fBlend);

          vec4 ht0 = texture2D(uSmokeHeatThickTex, uv0);
          vec4 ht1 = texture2D(uSmokeHeatThickTex, uv1);
          vec4 ht = mix(ht0, ht1, fBlend);

          float volAlpha = na.a;
          if (volAlpha < 0.015) discard;

          float heat = ht.r;
          float thick = ht.g;
          float erode = ht.b;

          // 年龄加权柔边溶解（彻底消除中早期挖洞与硬边 discard 锯齿）
          float erodeStart = (vKind > 3.5 && vKind < 4.5) ? 0.28 : 0.48;
          float cut = smoothstep(erodeStart, 0.98, vAge) * 0.88;
          float erodeWeight = smoothstep(erodeStart, erodeStart + 0.14, vAge);
          float erodeFade = mix(1.0, smoothstep(cut - 0.13, cut + 0.13, erode), erodeWeight);

          // 切线空间法线镜像与旋转校正 -> 世界空间方向性太阳受光
          vec3 nTan = na.rgb * 2.0 - 1.0;
          nTan.x *= vFlipX;
          float c = cos(vRot);
          float s = sin(vRot);
          vec2 nRotXY = vec2(nTan.x * c - nTan.y * s, nTan.x * s + nTan.y * c);
          vec3 nWorld = normalize(vCamRight * nRotXY.x + vCamUp * nRotXY.y + vCamFwd * max(0.12, nTan.z));

          vec3 sunDir = length(uSunDir) > 1e-4 ? normalize(uSunDir) : vec3(-0.48, 0.82, 0.31);
          float wrapNdl = clamp(dot(nWorld, sunDir) * 0.54 + 0.46, 0.0, 1.0);
          float skyLit = clamp(nWorld.y * 0.42 + 0.58, 0.22, 1.0);
          // 光学厚度自阴影：厚凸瓣中心自遮蔽更深，迎阳侧受暖光、背光侧压暗炭黑
          float selfShadow = mix(1.06, 0.62, thick * (1.0 - wrapNdl * 0.68));
          float macroGrad = mix(0.60, 1.18, clamp(vClusterHeightNorm, 0.0, 1.0));
          vec3 sunTint = mix(vec3(0.64, 0.68, 0.76), vec3(1.14, 1.06, 0.95), wrapNdl);
          float lightFactor = (0.28 * skyLit + 0.72 * wrapNdl) * selfShadow * macroGrad;

          // 正交线性深度 Soft Particles 接地羽化
          float featherM = (vKind > 4.5 && vKind < 5.5)
            ? clamp(vScale * 0.22, 0.65, 2.8)
            : clamp(vScale * 0.34, 0.85, 4.5);
          float softFade = computeSoftParticleFade(uSceneDepthTex, uHasSceneDepth, uResolution, uCamNear, uCamFar, featherM);

          vec3 smokeCol = vec3(0.06);
          vec3 fireCol = vec3(0.0);
          float alpha = 0.0;

          if (vKind < 0.5) {
            // 层 3 (kind=0)：炭黑外烟包裹 900K–4200K 缝隙黑体火核（外烟不透明遮蔽，凹陷缝隙透出炽热内焰）
            vec3 sootDark = vec3(0.014, 0.013, 0.013);
            vec3 sootWarm = vec3(0.068, 0.056, 0.048);
            smokeCol = mix(sootDark, sootWarm, clamp(wrapNdl * 0.65 + (1.0 - thick) * 0.35, 0.0, 1.0)) * sunTint * lightFactor;

            float coolThreshold = mix(0.08, 0.86, clamp(vAge * 1.18, 0.0, 1.0));
            float creviceTrans = mix(0.24, 1.0, pow(max(0.0, 1.0 - thick), 1.55));
            float fireMask = clamp(smoothstep(coolThreshold, 0.98, heat + (1.0 - vAge) * 0.22) * creviceTrans * vHeatGain, 0.0, 1.0);
            float tempNorm = clamp((heat * 0.85 + (1.0 - vAge) * 0.32 - 0.12) * creviceTrans, 0.0, 1.0);
            // 内部橙光由内向外微弱染亮邻近烟壁（0 PointLight 体积散射感）
            smokeCol += vec3(0.55, 0.18, 0.03) * (1.0 - vAge) * creviceTrans * 0.45 * vHeatGain;
            fireCol = blackbodyColor(tempNorm) * fireMask * (0.95 + 0.35 * (1.0 - vAge));

            float ageFade = smoothstep(0.0, 0.05, vAge) * (1.0 - smoothstep(0.68, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.96, 0.0, 0.96);
          } else if (vKind < 2.5) {
            // 层 4 (kind=2)：重磅翻滚炭黑灰蘑菇烟柱 & 坠毁斜风黑烟柱（线性色严格 <= 0.18，绝不触发 Bloom）
            vec3 charBlack = vec3(0.016, 0.016, 0.018);
            vec3 ashWarmGrey = vec3(0.115, 0.108, 0.098);
            smokeCol = mix(charBlack, ashWarmGrey, clamp(vAge * 0.58 + (1.0 - thick) * 0.32 + wrapNdl * 0.25, 0.0, 1.0)) * sunTint * lightFactor;
            // 早期近爆心烟团底部受内焰微弱暖色透照（由 vHeatGain 控制，绝不过曝）
            if (vHeatGain > 0.01) {
              float warmGlow = pow(max(0.0, 1.0 - vAge * 2.2), 1.6) * (1.0 - thick * 0.72) * (1.0 - vClusterHeightNorm * 0.75) * vHeatGain;
              smokeCol += vec3(0.46, 0.14, 0.025) * warmGlow;
            }
            float ageFade = smoothstep(0.0, 0.06, vAge) * (1.0 - smoothstep(0.52, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.92, 0.0, 0.92);
          } else if (vKind < 3.5) {
            // kind=3：AGM-65 导弹翻滚 8×8 体积云烟柱 & 挂架发射烟团（线性 <= 0.48，柔和受光灰白零 Bloom）
            vec3 coreGrey = vec3(0.25, 0.245, 0.232);
            vec3 puffWhite = vec3(0.47, 0.46, 0.438);
            smokeCol = mix(coreGrey, puffWhite, clamp(wrapNdl * 0.62 + (1.0 - thick) * 0.38, 0.0, 1.0)) * sunTint * (0.46 + 0.54 * lightFactor);
            if (vHeatGain > 0.01) {
              float tailWarm = pow(max(0.0, 1.0 - vAge * 6.5), 1.5) * (1.0 - thick * 0.55) * vHeatGain;
              smokeCol += vec3(0.68, 0.28, 0.06) * tailWarm;
            }
            float ageFade = smoothstep(0.0, 0.04, vAge) * (1.0 - smoothstep(0.42, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.78, 0.0, 0.80);
          } else if (vKind < 4.5) {
            // kind=4：GAU-8 机炮标志性浅灰白/暖乳灰火药烟雾（对标实拍图1/图2：俯视饱满清晰、迎阳乳灰白、背阴柔中灰、近炮口暖橘内透光，零 Bloom）
            vec3 shadowGrey = vec3(0.32, 0.33, 0.35);
            vec3 litLightGrey = vec3(0.74, 0.74, 0.72);
            smokeCol = mix(shadowGrey, litLightGrey, clamp(wrapNdl * 0.62 + (1.0 - thick) * 0.38, 0.0, 1.0)) * sunTint * (0.50 + 0.50 * lightFactor);
            if (vHeatGain > 0.01) {
              // 炮口刚喷出的浅灰火药烟云被火光由内向外照亮，呈现真实暖米橘透光感
              float muzzleWarm = pow(max(0.0, 1.0 - vAge * 7.0), 1.4) * (1.0 - thick * 0.45) * vHeatGain;
              smokeCol += vec3(0.95, 0.48, 0.14) * muzzleWarm;
            }
            float ageFade = smoothstep(0.0, 0.03, vAge) * (1.0 - smoothstep(0.36, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.86, 0.0, 0.88);
          } else if (vKind < 5.5) {
            // kind=5：贴地浅灰白/暖砂翻滚气浪尘墙（与炭黑中轴烟柱拉开真实冷暖明暗反差，线性 <= 0.44 零 Bloom）
            vec3 sandDust = vec3(0.22, 0.20, 0.175);
            vec3 paleSteam = vec3(0.42, 0.415, 0.395);
            smokeCol = mix(sandDust, paleSteam, clamp(wrapNdl * 0.55 + (1.0 - thick) * 0.45, 0.0, 1.0)) * sunTint * (0.48 + 0.52 * lightFactor);
            float ageFade = smoothstep(0.0, 0.07, vAge) * (1.0 - smoothstep(0.36, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.76, 0.0, 0.80);
          } else {
            // kind=6：受损/坠毁载具底层跳跃明火瓣（明黄/炽橘黑体残火，自然过渡至上方黑烟柱）
            vec3 darkBase = vec3(0.025, 0.020, 0.018) * lightFactor;
            float flicker = 0.82 + 0.18 * sin(vSeed * 41.0 + vAge * 24.0);
            float flameShape = clamp(smoothstep(0.18, 0.92, heat * 0.9 + (1.0 - vAge) * 0.28) * (1.0 - thick * 0.45), 0.0, 1.0);
            float tempNorm = clamp(heat * 0.82 * flicker + (1.0 - vAge) * 0.24, 0.0, 0.92);
            smokeCol = darkBase;
            fireCol = blackbodyColor(tempNorm) * flameShape * vHeatGain * flicker * 0.85;
            float ageFade = smoothstep(0.0, 0.10, vAge) * (1.0 - smoothstep(0.55, 1.0, vAge));
            alpha = clamp(volAlpha * erodeFade * ageFade * softFade * 0.88, 0.0, 0.90);
          }

          if (alpha < 0.014) discard;
          vec3 premulLin = (smokeCol + fireCol) * alpha;
          gl_FragColor = finalizeVfxPremulColor(premulLin, alpha, uLinearOutput);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, MAX_SMOKE_CHUNKS);
    mesh.name = 'vfx_pool_3_smoke_chunks';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.renderOrder = 12;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.smokeChunkPool = mesh;
    this._smokeAttrKind = geo.getAttribute('aLayerKind');
    this._smokeAttrAge = geo.getAttribute('aAgeNorm');
    this._smokeAttrSeed = geo.getAttribute('aSeed');
    this._smokeAttrMeta = geo.getAttribute('aMeta');
    this.vfxRoot.add(mesh);
  }

  /**
   * 池 4：层 5 P2 FBM 非规则撕裂焦坑、定向受光翻土棱缘与初期余烬热点
   * （1 DrawCall，512 槽双分区：192 重/中主弹坑持久区 + 320 轻型弹点区 + 空间去重 + 远景轻弹点分级淡出）。
   */
  _initPool4Craters() {
    const { THREE } = this;
    const geo = new THREE.PlaneGeometry(2.0, 2.0, 1, 1);
    geo.rotateX(-Math.PI / 2);

    const aSeed = new Float32Array(MAX_CRATERS);
    const aAgeSec = new Float32Array(MAX_CRATERS);
    const aRadius = new Float32Array(MAX_CRATERS);
    geo.setAttribute('aSeed', new THREE.InstancedBufferAttribute(aSeed, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aAgeSec', new THREE.InstancedBufferAttribute(aAgeSec, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aRadius', new THREE.InstancedBufferAttribute(aRadius, 1).setUsage(THREE.DynamicDrawUsage));

    const uLinearOutput = { value: this.linearOutput ? 1.0 : 0.0 };
    const uSunDir = { value: this._sunDir.clone() };
    this._linearUniforms.push(uLinearOutput);
    this._sunDirUniforms.push(uSunDir);

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -8,
      uniforms: { uLinearOutput, uSunDir, uCameraSpan: this._uCraterSpan },
      vertexShader: /* glsl */`
        attribute float aSeed;
        attribute float aAgeSec;
        attribute float aRadius;
        varying vec2 vUv;
        varying float vSeed;
        varying float vAgeSec;
        varying float vRadius;
        varying vec3 vWorldTangentX;
        varying vec3 vWorldTangentZ;
        void main() {
          vUv = uv;
          vSeed = aSeed;
          vAgeSec = aAgeSec;
          vRadius = aRadius;
          vWorldTangentX = normalize(instanceMatrix[0].xyz);
          vWorldTangentZ = normalize(instanceMatrix[2].xyz);
          gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */`
        uniform float uLinearOutput;
        uniform vec3 uSunDir;
        uniform float uCameraSpan;
        varying vec2 vUv;
        varying float vSeed;
        varying float vAgeSec;
        varying float vRadius;
        varying vec3 vWorldTangentX;
        varying vec3 vWorldTangentZ;
        ${GLSL_TONEMAP_HELPER}

        float hash21(vec2 p) {
          p = fract(p * vec2(234.34, 435.345));
          p += dot(p, p + 34.23);
          return fract(p.x * p.y);
        }

        float vNoise(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(
            mix(hash21(i), hash21(i + vec2(1.0, 0.0)), f.x),
            mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x),
            f.y
          );
        }

        float fbm(vec2 p) {
          float acc = 0.0;
          float amp = 0.52;
          for (int i = 0; i < 4; i++) {
            acc += vNoise(p) * amp;
            p = mat2(1.62, -1.21, 1.21, 1.62) * p + vec2(4.7, 9.2);
            amp *= 0.48;
          }
          return acc;
        }

        float craterProfile(vec2 p, float seed) {
          float r = length(p);
          float n1 = fbm(p * 3.1 + vec2(seed * 7.3, seed * 3.9));
          float n2 = fbm(p * 6.8 - vec2(seed * 4.1, seed * 11.2));
          float warpR = r + (n1 - 0.5) * 0.24 + (n2 - 0.5) * 0.09;
          // 中心凹陷深坑 + 中圈隆起翻土棱缘
          float pit = -smoothstep(0.58, 0.08, warpR) * 0.65;
          float rim = smoothstep(0.30, 0.56, warpR) * (1.0 - smoothstep(0.58, 0.90, warpR)) * 0.48;
          return pit + rim + (n2 - 0.5) * 0.14 * (1.0 - smoothstep(0.65, 1.0, warpR));
        }

        void main() {
          vec2 p = vUv * 2.0 - 1.0;
          float r = length(p);
          if (r > 1.0) discard;

          float nEdge = fbm(p * 3.6 + vec2(vSeed * 9.1, vSeed * 5.7));
          float nRay = fbm(vec2(atan(p.y, p.x) * 2.8 + vSeed * 5.3, r * 3.2));
          float edgeR = r + (nEdge - 0.5) * 0.28 + (nRay - 0.5) * 0.16;
          if (edgeR > 0.98) discard;

          // 有限差分估算翻土隆起法线并点乘真实主太阳方向
          float eps = 0.035;
          float hC = craterProfile(p, vSeed);
          float hX = craterProfile(p + vec2(eps, 0.0), vSeed);
          float hZ = craterProfile(p + vec2(0.0, eps), vSeed);
          float dhdx = (hX - hC) / eps;
          float dhdz = (hZ - hC) / eps;
          vec3 upCross = cross(vWorldTangentZ, vWorldTangentX);
          vec3 upW = length(upCross) > 1e-4 ? normalize(upCross) : vec3(0.0, 1.0, 0.0);
          vec3 rimCand = upW - vWorldTangentX * (dhdx * 0.55) - vWorldTangentZ * (dhdz * 0.55);
          vec3 rimN = length(rimCand) > 1e-4 ? normalize(rimCand) : vec3(0.0, 1.0, 0.0);
          vec3 safeSun = length(uSunDir) > 1e-4 ? normalize(uSunDir) : vec3(-0.48, 0.82, 0.31);
          float sunLit = clamp(dot(rimN, safeSun) * 0.55 + 0.45, 0.22, 1.15);

          float pitMask = 1.0 - smoothstep(0.14, 0.54, edgeR);
          float scorchMask = 1.0 - smoothstep(0.42, 0.95, edgeR);
          float rimMask = smoothstep(0.34, 0.58, edgeR) * (1.0 - smoothstep(0.64, 0.96, edgeR));
          float ejectaSpeck = smoothstep(0.56, 0.82, nEdge * 0.7 + nRay * 0.45) * (1.0 - smoothstep(0.55, 0.98, edgeR));

          vec3 freshSoil = vec3(0.068, 0.052, 0.036) * sunLit;
          vec3 charredPit = vec3(0.007, 0.006, 0.006);
          vec3 sootRing = vec3(0.020, 0.017, 0.014) * (0.65 + 0.35 * sunLit);

          vec3 col = mix(freshSoil, sootRing, clamp(scorchMask * 0.78, 0.0, 1.0));
          col = mix(col, charredPit, clamp(pitMask * 0.92, 0.0, 1.0));
          col += freshSoil * (rimMask * 0.55 + ejectaSpeck * 0.42);

          // 新建弹坑初期（前 ~4 秒）爆心焦缝微弱暗红余烬热点（绝不过曝）
          float emberDecay = exp(-max(0.0, vAgeSec) * 0.62);
          float emberMask = pitMask * smoothstep(0.58, 0.86, nEdge) * emberDecay;
          col += vec3(0.82, 0.14, 0.02) * emberMask;

          float alpha = clamp(scorchMask * 0.88 + rimMask * 0.52 + ejectaSpeck * 0.38, 0.0, 0.95);
          alpha *= (1.0 - smoothstep(0.84, 0.98, edgeR));
          // 战略高空视距下温和淡出 30mm 碎小弹点，保留中/重型主弹坑作为战场毁伤地标
          if (vRadius < 4.0) {
            alpha *= (1.0 - smoothstep(580.0, 960.0, uCameraSpan));
          }
          if (alpha < 0.025) discard;
          gl_FragColor = finalizeVfxColor(col, alpha, uLinearOutput);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, MAX_CRATERS);
    mesh.name = 'vfx_pool_4_craters';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.renderOrder = 4;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.craterPool = mesh;
    this._craterAttrSeed = geo.getAttribute('aSeed');
    this._craterAttrAgeSec = geo.getAttribute('aAgeSec');
    this._craterAttrRadius = geo.getAttribute('aRadius');
    this.vfxRoot.add(mesh);
  }

  /**
   * 池 5：合并共享 Billboard 软边条带网格（导弹/火箭 FBM 湍流蓬松长烟带 + 热诱弹烟弧 + 翼尖涡流 + 高空凝结尾迹 + 坠毁炭黑烟带，1 DrawCall）。
   */
  _initPool5SharedRibbon() {
    const { THREE } = this;
    const maxQuads = MAX_RIBBONS * (RIBBON_SEGMENTS - 1);
    const maxVerts = maxQuads * 6;
    const positions = new Float32Array(maxVerts * 3);
    const colors = new Float32Array(maxVerts * 4);
    const acrossArr = new Float32Array(maxVerts);
    const metaArr = new Float32Array(maxVerts * 3); // x: alongFrac(0=最新端,1=尾端), y: mode(0=流线,1=湍流导弹/诱饵烟,2=炭黑坠机烟), z: headGlow(飞行近喷口橙暖散射)

    const geo = new THREE.BufferGeometry();
    const posAttr = new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage);
    const colAttr = new THREE.BufferAttribute(colors, 4).setUsage(THREE.DynamicDrawUsage);
    const acrossAttr = new THREE.BufferAttribute(acrossArr, 1).setUsage(THREE.DynamicDrawUsage);
    const metaAttr = new THREE.BufferAttribute(metaArr, 3).setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('position', posAttr);
    geo.setAttribute('aRibbonColor', colAttr);
    geo.setAttribute('aAcross', acrossAttr);
    geo.setAttribute('aRibbonMeta', metaAttr);
    geo.setDrawRange(0, 0);

    const uLinearOutput = { value: this.linearOutput ? 1.0 : 0.0 };
    const uSunDir = { value: this._sunDir.clone() };
    this._linearUniforms.push(uLinearOutput);
    this._sunDirUniforms.push(uSunDir);

    const uniforms = { uLinearOutput, uSunDir };
    this._registerDepthUniforms(uniforms);

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      uniforms,
      vertexShader: /* glsl */`
        attribute vec4 aRibbonColor;
        attribute float aAcross;
        attribute vec3 aRibbonMeta;
        varying vec4 vColor;
        varying float vAcross;
        varying vec3 vMeta;
        varying vec3 vWPos;
        void main() {
          vColor = aRibbonColor;
          vAcross = aAcross;
          vMeta = aRibbonMeta;
          vWPos = position;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */`
        uniform float uLinearOutput;
        uniform vec3 uSunDir;
        uniform sampler2D uSceneDepthTex;
        uniform float uHasSceneDepth;
        uniform vec2 uResolution;
        uniform float uCamNear;
        uniform float uCamFar;
        varying vec4 vColor;
        varying float vAcross;
        varying vec3 vMeta;
        varying vec3 vWPos;
        ${GLSL_TONEMAP_HELPER}
        ${GLSL_SOFT_DEPTH_HELPER}
        float hash21(vec2 p) {
          p = fract(p * vec2(127.1, 311.7));
          p += dot(p, p + 19.19);
          return fract(p.x * p.y);
        }
        float vnoise2(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          float a = hash21(i);
          float b = hash21(i + vec2(1.0, 0.0));
          float c = hash21(i + vec2(0.0, 1.0));
          float d = hash21(i + vec2(1.0, 1.0));
          return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
        }
        float fbmRibbon(vec2 p) {
          float s = 0.0;
          float amp = 0.54;
          for (int i = 0; i < 3; i++) {
            s += vnoise2(p) * amp;
            p = p * 2.08 + vec2(11.3, 7.1);
            amp *= 0.48;
          }
          return s;
        }
        void main() {
          float along = vMeta.x;
          float mode = vMeta.y;
          float headGlow = vMeta.z;
          float softFade = computeSoftParticleFade(uSceneDepthTex, uHasSceneDepth, uResolution, uCamNear, uCamFar, mode > 0.5 ? 1.8 : 1.0);

          if (mode < 0.5) {
            // 流线型高空凝结尾迹与翼尖涡流：柔和丝滑边缘
            float edgeSoft = smoothstep(0.0, 0.34, vAcross) * smoothstep(1.0, 0.66, vAcross);
            float micro = 0.84 + 0.16 * vnoise2(vWPos.xz * 0.42 + vec2(vWPos.y * 0.25));
            float alpha = vColor.a * edgeSoft * micro * softFade;
            if (alpha < 0.014) discard;
            gl_FragColor = finalizeVfxColor(vColor.rgb, alpha, uLinearOutput);
            return;
          }

          // 导弹/火箭/诱饵/坠机烟带：世界空间双层连续 FBM 湍流卷曲撕裂边缘 + 太阳方向受光 + 喷口橙暖散射
          float centered = abs(vAcross * 2.0 - 1.0); // 0=中心轴, 1=左右外缘
          vec2 pMacro = vWPos.xz * 0.34 + vec2(vWPos.y * 0.26, -vWPos.y * 0.19);
          vec2 pMicro = vWPos.xz * 0.88 - vec2(vWPos.y * 0.52, vWPos.y * 0.41);
          float nMacro = fbmRibbon(pMacro);
          float nMicro = fbmRibbon(pMicro);
          float turb = nMacro * 0.60 + nMicro * 0.40;

          // 前端紧实细锐，中后段随沿程膨胀出现明显卷云鼓包与断续碎裂（参考实拍导弹尾烟云絮边界）
          float centerShift = (nMacro - 0.5) * mix(0.10, 0.36, smoothstep(0.05, 0.65, along));
          float shiftedCentered = abs((vAcross * 2.0 - 1.0) + centerShift);
          float warpAmp = mix(0.30, 0.88, smoothstep(0.03, 0.55, along));
          float radialWarped = shiftedCentered + (turb - 0.5) * warpAmp;
          float edgeSoft = 1.0 - smoothstep(0.18, 0.90, radialWarped);
          float puffLobes = smoothstep(mix(0.16, 0.48, along), mix(0.64, 0.90, along), turb + (1.0 - shiftedCentered) * 0.34);
          float density = edgeSoft * mix(0.46, 1.18, puffLobes);

          // 圆柱横截面伪法线 + 主太阳方向受光（迎阳暖灰白，背阴冷深灰，彻底告别平面纸带）
          float sideSign = (vAcross * 2.0 - 1.0);
          vec3 pseudoN = normalize(vec3(sideSign * 0.58 + (nMicro - 0.5) * 0.42, 0.78, (nMacro - 0.5) * 0.45));
          vec3 safeSunDirR = length(uSunDir) > 1e-4 ? normalize(uSunDir) : vec3(-0.48, 0.82, 0.31);
          float sunN = clamp(dot(pseudoN, safeSunDirR) * 0.5 + 0.5, 0.0, 1.0);
          float shade = mix(0.56, 1.14, sunN) * mix(0.84, 1.08, nMacro);
          vec3 col = vColor.rgb * shade;

          // 飞行中紧邻喷口的 0~10% 烟柱内腔被固体火箭尾焰照亮（实拍导弹照核心灵魂）
          if (headGlow > 0.01 && mode < 1.5) {
            float glowZone = smoothstep(0.14, 0.0, along) * (1.0 - centered * 0.7) * headGlow;
            col += vec3(1.15, 0.52, 0.14) * glowZone * 0.62;
          }

          float alpha = clamp(vColor.a * density * softFade, 0.0, mode > 1.5 ? 0.90 : 0.86);
          if (alpha < 0.014) discard;
          gl_FragColor = finalizeVfxColor(col, alpha, uLinearOutput);
        }
      `,
    });

    const mesh = new THREE.Mesh(geo, mat);
    mesh.name = 'vfx_pool_5_shared_ribbon';
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    // 严格低于池 3 爆炸黑烟(13)，防止导弹落地后白烟盖过爆心炭黑烟云
    mesh.renderOrder = 12;
    this.sharedRibbonMesh = mesh;
    this._ribbonPosArr = positions;
    this._ribbonColArr = colors;
    this._ribbonAcrossArr = acrossArr;
    this._ribbonMetaArr = metaArr;
    this._ribbonPosAttr = posAttr;
    this._ribbonColAttr = colAttr;
    this._ribbonAcrossAttr = acrossAttr;
    this._ribbonMetaAttr = metaAttr;
    this.vfxRoot.add(mesh);
  }

  /**
   * 池 6：多功能 Premultiplied Alpha 锥/光束/破片烟丝/拉丝火花池（1 DrawCall）：
   * - kind 0: 加力马赫环尾焰（alpha=0 Additive）
   * - kind 1: 音爆白色蒸汽圆锥云（非发光 Premultiplied Alpha）
   * - kind 2: AGM-65 / Hydra 超高温固体火箭尾焰（alpha=0 Additive，HDR 5.6）
   * - kind 3: GAU-8 机鼻枪口星形喷焰（alpha=0 Additive，HDR 5.4）
   * - kind 4: GAU-8 30mm 真实红/绿高饱和 HDR 曳光光丝（alpha=0 Additive）+ 低调 Fumer 减阻空气白痕（abs(seed)>=50）
   * - kind 5: 《Defilade》细长冷白/灰白破片烟丝射流（非发光 Premultiplied Alpha，绝不超 Bloom 阈值）
   * - kind 6: 极细 HDR 拉丝火花带（alpha=0 Additive，含 680p 最小像素宽防闪烁与能量守恒衰减）
   */
  _initPool6ConeFx() {
    const { THREE } = this;
    const geo = new THREE.ConeGeometry(1.0, 1.0, 12, 1, true);
    geo.translate(0, 0.5, 0);    // 底圆在 y=0 (vUv.y=0)，尖端在 y=+1 (vUv.y=1)
    geo.rotateX(Math.PI / 2);    // 底圆在 z=0，尖端在 z=+1
    const aKind = new Float32Array(MAX_CONE_FX);
    const aAge = new Float32Array(MAX_CONE_FX);
    const aSeed = new Float32Array(MAX_CONE_FX);
    geo.setAttribute('aConeKind', new THREE.InstancedBufferAttribute(aKind, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aAgeNorm', new THREE.InstancedBufferAttribute(aAge, 1).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute('aSeed', new THREE.InstancedBufferAttribute(aSeed, 1).setUsage(THREE.DynamicDrawUsage));

    const uLinearOutput = { value: this.linearOutput ? 1.0 : 0.0 };
    this._linearUniforms.push(uLinearOutput);

    const uniforms = {
      uLinearOutput,
      uMeterPerPixel: this._uMeterPerPixel,
    };
    this._registerDepthUniforms(uniforms);

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneMinusSrcAlphaFactor,
      side: THREE.DoubleSide,
      uniforms,
      vertexShader: /* glsl */`
        uniform float uMeterPerPixel;
        attribute float aConeKind;
        attribute float aAgeNorm;
        attribute float aSeed;
        varying float vKind;
        varying float vAge;
        varying float vSeed;
        varying vec2 vUv;
        varying vec3 vLocalPos;
        varying vec3 vNormalView;
        varying vec3 vViewDir;
        varying float vEnergyComp;

        void main() {
          vKind = aConeKind;
          vAge = aAgeNorm;
          vSeed = aSeed;
          vUv = uv;
          vLocalPos = position;
          vEnergyComp = 1.0;

          vec3 posLocal = position;
          // kind=2（导弹/火箭马赫尾焰）、kind=5（破片烟丝）与 kind=6（拉丝火花）高空最小像素宽保底，防止 680p 亚像素消失
          if (aConeKind > 1.5 && aConeKind < 2.5) {
            float isRocket = step(19.5, aSeed);
            float baseR = max(0.05, length(instanceMatrix[0].xyz));
            float pxFactor = mix(1.15, 0.85, isRocket);
            float minVisR = max(baseR, pxFactor * uMeterPerPixel);
            posLocal.xy *= (minVisR / baseR);
            // 远距视口略微保底长度确保马赫焰清晰可辨，同时近距保持真实比例
            float baseL = max(0.5, length(instanceMatrix[2].xyz));
            float minVisL = max(baseL, mix(8.5, 5.0, isRocket) * uMeterPerPixel);
            posLocal.z *= clamp(minVisL / baseL, 1.0, 1.9);
          } else if (aConeKind > 4.5) {
            float baseR = max(0.01, length(instanceMatrix[0].xyz));
            float pxFactor = aConeKind > 5.5 ? 0.72 : 0.62;
            float minVisR = max(baseR, pxFactor * uMeterPerPixel);
            float scaleBoost = minVisR / baseR;
            posLocal.xy *= scaleBoost;
            vEnergyComp = clamp(baseR / minVisR, 0.22, 1.0);
          }

          vec4 mvPos = modelViewMatrix * instanceMatrix * vec4(posLocal, 1.0);
          vec3 rawRight = instanceMatrix[0].xyz;
          vec3 rawUp = instanceMatrix[1].xyz;
          vec3 worldRight = length(rawRight) > 1e-5 ? normalize(rawRight) : vec3(1.0, 0.0, 0.0);
          vec3 worldUp = length(rawUp) > 1e-5 ? normalize(rawUp) : vec3(0.0, 1.0, 0.0);
          vec2 nXY = normal.xy;
          float nLen = length(nXY);
          vec2 r2 = nLen > 1e-4 ? (nXY / nLen) : vec2(1.0, 0.0);
          vec3 radialCand = worldRight * r2.x + worldUp * r2.y;
          vec3 worldRadial = length(radialCand) > 1e-5 ? normalize(radialCand) : vec3(0.0, 1.0, 0.0);
          vec3 viewNormCand = (viewMatrix * vec4(worldRadial, 0.0)).xyz;
          vNormalView = length(viewNormCand) > 1e-5 ? normalize(viewNormCand) : vec3(0.0, 0.0, 1.0);
          vec3 viewDirCand = -mvPos.xyz;
          vViewDir = length(viewDirCand) > 1e-5 ? normalize(viewDirCand) : vec3(0.0, 0.0, 1.0);
          gl_Position = projectionMatrix * mvPos;
        }
      `,
      fragmentShader: /* glsl */`
        uniform float uLinearOutput;
        uniform sampler2D uSceneDepthTex;
        uniform float uHasSceneDepth;
        uniform vec2 uResolution;
        uniform float uCamNear;
        uniform float uCamFar;
        varying float vKind;
        varying float vAge;
        varying float vSeed;
        varying vec2 vUv;
        varying vec3 vLocalPos;
        varying vec3 vNormalView;
        varying vec3 vViewDir;
        varying float vEnergyComp;

        ${GLSL_TONEMAP_HELPER}
        ${GLSL_SOFT_DEPTH_HELPER}

        void main() {
          float fade = clamp(1.0 - vAge, 0.0, 1.0);
          vec3 safeNorm = length(vNormalView) > 1e-5 ? normalize(vNormalView) : vec3(0.0, 0.0, 1.0);
          vec3 safeView = length(vViewDir) > 1e-5 ? normalize(vViewDir) : vec3(0.0, 0.0, 1.0);
          float coreCos = clamp(abs(dot(safeNorm, safeView)), 0.0, 1.0);
          float radialCore = pow(max(coreCos, 1e-4), 1.35);
          float softCone = computeSoftParticleFade(uSceneDepthTex, uHasSceneDepth, uResolution, uCamNear, uCamFar, 1.15);

          if (vKind < 0.5) {
            // kind 0: 加力马赫环尾焰（Additive）
            vec3 col = mix(vec3(0.55, 0.85, 3.2), vec3(3.4, 1.35, 0.35), vUv.y);
            float w = (1.0 - vUv.y) * fade * 0.88;
            if (w < 0.015) discard;
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          if (vKind < 1.5) {
            // kind 1: 0.45s 音爆蒸汽圆锥云（非发光 Premultiplied Alpha）
            float edge = sin(vUv.y * 3.14159);
            vec3 col = vec3(0.68, 0.72, 0.78);
            float a = edge * fade * 0.62 * softCone;
            if (a < 0.015) discard;
            gl_FragColor = finalizeVfxPremulColor(col * a, a, uLinearOutput);
            return;
          }

          if (vKind < 2.5) {
            // kind 2: AGM-65 / Hydra 固体火箭马赫尾焰（680p 像素保底 + 喷口亮白微 Bloom + 鲜明橙琥珀马赫激波节）
            float isRocket = step(19.5, vSeed);
            float seedFrac = fract(vSeed * 0.37);
            float shockFreq = mix(22.0, 16.0, isRocket);
            float machDiamonds = 0.76 + 0.24 * cos(vUv.y * shockFreq - seedFrac * 18.0);
            float tipFlicker = 0.84 + 0.16 * sin(seedFrac * 47.0 + vUv.y * 29.0);
            float radialTight = pow(radialCore, mix(1.05, 1.48, vUv.y));
            float nozzleCore = smoothstep(0.28, 0.0, vUv.y) * smoothstep(0.32, 0.92, radialCore);
            float midPlume = smoothstep(0.78, 0.06, vUv.y);
            vec3 nozzleWhite = vec3(3.6, 2.75, 1.45);
            vec3 machOrange = vec3(2.85, 1.05, 0.18);
            vec3 tailCrimson = vec3(1.45, 0.36, 0.05);
            vec3 col = mix(tailCrimson, machOrange, midPlume) * machDiamonds;
            col = mix(col, nozzleWhite, nozzleCore);
            col *= tipFlicker * mix(1.0, 0.86, isRocket);
            float axialFade = pow(max(0.0, 1.0 - vUv.y), 1.08);
            float w = axialFade * radialTight * mix(0.92, 0.82, isRocket);
            if (w < 0.015) discard;
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          if (vKind < 3.5) {
            // kind 3: GAU-8 短促、不规则撕裂炮口亮光（告别巨型对称白灯泡：高频随机非对称尖刺+克制HDR暖白橙）
            float ang = atan(vLocalPos.y, vLocalPos.x) + vSeed * 6.2831;
            float spikeA = 0.50 + 0.50 * cos(ang * 3.0 + vSeed * 11.3);
            float spikeB = 0.50 + 0.50 * cos(ang * 5.0 - vSeed * 17.7);
            float spikeC = 0.50 + 0.50 * sin(ang * 7.0 + vSeed * 23.1);
            float irregPetals = clamp(0.28 + 0.42 * spikeA + 0.28 * spikeB + 0.22 * spikeC, 0.0, 1.25);
            // 沿轴向不规则撕裂尖端，防止圆锥平头感
            float tipCut = clamp(1.0 - vUv.y / max(0.32, 0.55 + 0.45 * irregPetals), 0.0, 1.0);
            float core = smoothstep(0.52, 0.0, vUv.y) * smoothstep(0.25, 0.92, radialCore);
            vec3 hotCore = vec3(3.45, 2.50, 1.12);
            vec3 sharpAmber = vec3(2.55, 0.92, 0.14);
            vec3 tipOrange = vec3(1.35, 0.32, 0.04);
            vec3 col = mix(tipOrange, sharpAmber, smoothstep(0.0, 0.65, tipCut));
            col = mix(col, hotCore, core);
            float pulseFade = pow(max(0.0, fade), 1.45);
            float w = pow(max(0.0, tipCut), 1.15) * pow(max(0.0, radialCore), 0.95) * pulseFade * 0.88;
            if (w < 0.015) discard;
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          if (vKind < 4.5) {
            // kind 4: GAU-8 30mm 实弹 Fumer 减阻空气白痕（abs(seed)>=50）与高饱和 HDR 红/绿曳光
            if (abs(vSeed) >= 49.5) {
              vec3 fumerCol = vec3(0.46, 0.48, 0.50);
              float a = pow(max(0.0, 1.0 - vUv.y), 1.35) * mix(0.25, 0.72, radialCore) * 0.36;
              if (a < 0.012) discard;
              gl_FragColor = finalizeVfxPremulColor(fumerCol * a, a, uLinearOutput);
              return;
            }
            float isGreen = step(vSeed, -0.0001);
            float headMask = smoothstep(0.14, 0.0, vUv.y);
            float coreMask = smoothstep(0.45, 0.95, radialCore);
            vec3 redHead = vec3(4.4, 0.24, 0.04);
            vec3 redBody = vec3(3.15, 0.015, 0.005);
            vec3 greenHead = vec3(0.18, 4.3, 0.36);
            vec3 greenBody = vec3(0.012, 2.95, 0.06);
            vec3 headCol = mix(redHead, greenHead, isGreen);
            vec3 bodyCol = mix(redBody, greenBody, isGreen);
            vec3 col = mix(bodyCol, headCol, headMask * (0.28 + 0.72 * coreMask));
            float w = pow(max(0.0, 1.0 - vUv.y), 1.08) * mix(0.40, 0.96, radialCore) * 0.94;
            if (w < 0.015) discard;
            gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
            return;
          }

          if (vKind < 5.5) {
            // kind 5: 《Defilade》破碎云絮冷灰白破片烟缕（非发光 Premultiplied Alpha + Soft Particles，告别笔直蜘蛛白网）
            float ang = atan(vLocalPos.y, vLocalPos.x);
            float n1 = 0.5 + 0.5 * sin(vSeed * 19.3 + vUv.y * 16.0 + ang * 2.0);
            float n2 = 0.5 + 0.5 * cos(vSeed * 37.1 - vUv.y * 31.0 + ang * 3.0);
            float puffs = smoothstep(0.16, 0.88, n1 * 0.58 + n2 * 0.42);
            float taper = smoothstep(0.0, 0.16, vUv.y) * pow(max(0.0, 1.0 - vUv.y), 0.72);
            float headTip = smoothstep(0.68, 0.94, vUv.y);
            vec3 tailWisp = vec3(0.28, 0.29, 0.31);
            vec3 headWisp = vec3(0.56, 0.57, 0.60);
            vec3 col = mix(tailWisp, headWisp, headTip * 0.55 + n1 * 0.25);
            float a = taper * mix(0.22, 0.92, radialCore) * pow(fade, 1.25) * (0.35 + 0.65 * puffs) * 0.56 * softCone * vEnergyComp;
            if (a < 0.014) discard;
            gl_FragColor = finalizeVfxPremulColor(col * a, a, uLinearOutput);
            return;
          }

          // kind 6: 极细 HDR 拉丝火花带（头白炽、尾炽橘，能量守恒防 680p 亚像素闪烁 + Soft Particles）
          float head = smoothstep(0.35, 0.0, vUv.y);
          vec3 tailCol = vec3(3.4, 0.95, 0.12);
          vec3 tipCol = vec3(5.8, 3.9, 1.85);
          vec3 col = mix(tailCol, tipCol, head);
          float w = pow(max(0.0, 1.0 - vUv.y), 0.95) * mix(0.45, 1.0, radialCore) * pow(fade, 1.25) * vEnergyComp * softCone;
          if (w < 0.012) discard;
          gl_FragColor = finalizeVfxPremulColor(col * w, 0.0, uLinearOutput);
        }
      `,
    });

    const mesh = new THREE.InstancedMesh(geo, mat, MAX_CONE_FX);
    mesh.name = 'vfx_pool_6_cone_fx';
    mesh.count = 0;
    mesh.visible = false;
    mesh.frustumCulled = false;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.renderOrder = 15;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.coneFxPool = mesh;
    this._coneAttrKind = geo.getAttribute('aConeKind');
    this._coneAttrAge = geo.getAttribute('aAgeNorm');
    this._coneAttrSeed = geo.getAttribute('aSeed');
    this.vfxRoot.add(mesh);
  }

  /**
   * 每帧开始前同步相机视锥、观察方向、三档视距与 680p 单位像素尺寸。
   */
  setFrameCamera(camera, spanMeters) {
    this.spanMeters = Number.isFinite(spanMeters) ? spanMeters : this.spanMeters;
    this.lodTier = resolveVfxLodTier(this.spanMeters);
    this._uMeterPerPixel.value = clamp(this.spanMeters / 680.0, 0.08, 4.0);
    if (this._uCraterSpan) this._uCraterSpan.value = this.spanMeters;
    if (camera) {
      camera.updateMatrixWorld();
      this._projScreenMat.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      this._frustum.setFromProjectionMatrix(this._projScreenMat);
      camera.getWorldDirection(this._camLookDir);
      this._viewVec.copy(this._camLookDir).negate().normalize();
      this._hasCameraFrustum = true;
    }
    this._ordnanceFrameCount = 0;
    this._frameConeCount = 0;
  }

  _isInFrustum(worldPos, radius = 16) {
    if (!this._hasCameraFrustum) return true;
    this._cullSphere.center.set(worldPos.x || 0, worldPos.y || 0, worldPos.z || 0);
    this._cullSphere.radius = radius;
    const hit = this._frustum.intersectsSphere(this._cullSphere);
    if (!hit) this.frustumCulledCount += 1;
    return hit;
  }

  _allocBlast() {
    let oldestIdx = this._blastCursor;
    let oldestRatio = -1;
    for (let i = 0; i < MAX_BLAST_SHELLS; i += 1) {
      const idx = (this._blastCursor + i) % MAX_BLAST_SHELLS;
      const s = this._blastSlots[idx];
      if (!s.active) {
        this._blastCursor = (idx + 1) % MAX_BLAST_SHELLS;
        s.delay = 0;
        s.tag = '';
        s.seed = 0;
        s.rotY = 0;
        return s;
      }
      const ratio = s.age / Math.max(0.05, s.life);
      if (ratio > oldestRatio) {
        oldestRatio = ratio;
        oldestIdx = idx;
      }
    }
    const slot = this._blastSlots[oldestIdx];
    this._blastCursor = (oldestIdx + 1) % MAX_BLAST_SHELLS;
    slot.delay = 0;
    slot.tag = '';
    slot.seed = 0;
    slot.rotY = 0;
    return slot;
  }

  _allocSmoke() {
    let oldestIdx = this._smokeCursor;
    let oldestRatio = -1;
    for (let i = 0; i < MAX_SMOKE_CHUNKS; i += 1) {
      const idx = (this._smokeCursor + i) % MAX_SMOKE_CHUNKS;
      const s = this._smokeSlots[idx];
      if (!s.active) {
        this._smokeCursor = (idx + 1) % MAX_SMOKE_CHUNKS;
        s.windX = 0;
        s.windZ = 0;
        s.burstDrag = 0;
        s.burstWindowSec = 0;
        s.buoyancy = 0;
        s.expGrowK = 0;
        s.clusterHeightNorm = 0.5;
        s.heatGain = 0;
        return s;
      }
      const ratio = s.age / Math.max(0.05, s.life);
      if (ratio > oldestRatio) {
        oldestRatio = ratio;
        oldestIdx = idx;
      }
    }
    const slot = this._smokeSlots[oldestIdx];
    this._smokeCursor = (oldestIdx + 1) % MAX_SMOKE_CHUNKS;
    slot.windX = 0;
    slot.windZ = 0;
    slot.burstDrag = 0;
    slot.burstWindowSec = 0;
    slot.buoyancy = 0;
    slot.expGrowK = 0;
    slot.clusterHeightNorm = 0.5;
    slot.heatGain = 0;
    return slot;
  }

  /**
   * 零 GC 原地梳排序+插入排序（升序：远→近绘制半透明粒子，兼顾层桶偏置）。
   */
  _sortIndicesAsc(order, keys, count) {
    if (count <= 1) return;
    let gap = count;
    while (gap > 8) {
      gap = Math.floor((gap * 10) / 13);
      if (gap < 1) gap = 1;
      for (let i = 0; i + gap < count; i += 1) {
        const j = i + gap;
        if (keys[i] > keys[j]) {
          const tk = keys[i]; keys[i] = keys[j]; keys[j] = tk;
          const to = order[i]; order[i] = order[j]; order[j] = to;
        }
      }
    }
    for (let i = 1; i < count; i += 1) {
      const k = keys[i];
      const o = order[i];
      let j = i - 1;
      while (j >= 0 && keys[j] > k) {
        keys[j + 1] = keys[j];
        order[j + 1] = order[j];
        j -= 1;
      }
      keys[j + 1] = k;
      order[j + 1] = o;
    }
  }

  _allocCone() {
    for (let i = 0; i < MAX_CONE_FX; i += 1) {
      const idx = (this._coneCursor + i) % MAX_CONE_FX;
      if (!this._coneSlots[idx].active) {
        this._coneCursor = (idx + 1) % MAX_CONE_FX;
        const slot = this._coneSlots[idx];
        slot.vx = 0; slot.vy = 0; slot.vz = 0;
        slot.gravity = 0; slot.drag = 0;
        return slot;
      }
    }
    const slot = this._coneSlots[this._coneCursor];
    this._coneCursor = (this._coneCursor + 1) % MAX_CONE_FX;
    slot.vx = 0; slot.vy = 0; slot.vz = 0;
    slot.gravity = 0; slot.drag = 0;
    return slot;
  }

  _allocFlare() {
    for (let i = 0; i < MAX_FLARES; i += 1) {
      const idx = (this._flareCursor + i) % MAX_FLARES;
      if (!this._flareSlots[idx].active) {
        this._flareCursor = (idx + 1) % MAX_FLARES;
        return this._flareSlots[idx];
      }
    }
    const slot = this._flareSlots[this._flareCursor];
    this._flareCursor = (this._flareCursor + 1) % MAX_FLARES;
    return slot;
  }

  _pushFrameCone(kind, x, y, z, dx, dy, dz, sx, sy, sz, ageNorm = 0, seed = 0) {
    if (this._frameConeCount >= MAX_CONE_FX) return;
    const fc = this._frameCones[this._frameConeCount];
    this._frameConeCount += 1;
    fc.kind = kind;
    fc.x = x; fc.y = y; fc.z = z;
    fc.dx = dx; fc.dy = dy; fc.dz = dz;
    fc.sx = sx; fc.sy = sy; fc.sz = sz;
    fc.ageNorm = ageNorm;
    fc.seed = seed;
  }

  _allocRibbon(key, kind, width, life, endWidth = width * 1.8) {
    let r = this._ribbonByKey.get(key);
    if (r) {
      r.active = true;
      r.kind = kind;
      r.width = width;
      r.endWidth = endWidth;
      r.life = life;
      return r;
    }
    for (let i = 0; i < MAX_RIBBONS; i += 1) {
      if (!this._ribbons[i].active) {
        r = this._ribbons[i];
        break;
      }
    }
    if (!r) {
      r = this._ribbons[0];
      for (let i = 1; i < MAX_RIBBONS; i += 1) {
        if (this._ribbons[i].age > r.age) r = this._ribbons[i];
      }
      if (r.key) this._ribbonByKey.delete(r.key);
    }
    r.active = true;
    r.key = key;
    r.kind = kind;
    r.width = width;
    r.endWidth = endWidth;
    r.alpha = 1.0;
    r.life = life;
    r.age = 0;
    r.count = 0;
    r.lastEmitSec = -999;
    this._ribbonByKey.set(key, r);
    return r;
  }

  _pushRibbonPoint(ribbon, x, y, z, simNowSec, minStepM = 1.1) {
    if (ribbon.count > 0) {
      const lx = ribbon.points[0];
      const ly = ribbon.points[1];
      const lz = ribbon.points[2];
      if (Math.hypot(x - lx, y - ly, z - lz) < minStepM) return;
    }
    const maxShift = Math.min(ribbon.count, RIBBON_SEGMENTS - 1);
    for (let i = maxShift; i >= 1; i -= 1) {
      ribbon.points[i * 3] = ribbon.points[(i - 1) * 3];
      ribbon.points[i * 3 + 1] = ribbon.points[(i - 1) * 3 + 1];
      ribbon.points[i * 3 + 2] = ribbon.points[(i - 1) * 3 + 2];
      ribbon.pointAges[i] = ribbon.pointAges[i - 1];
    }
    ribbon.points[0] = x;
    ribbon.points[1] = y;
    ribbon.points[2] = z;
    ribbon.pointAges[0] = 0;
    if (ribbon.count < RIBBON_SEGMENTS) ribbon.count += 1;
    ribbon.lastEmitSec = simNowSec;
  }

  _pushRibbonVert(vx, vy, vz, r, g, b, va, across, along = 0.5, mode = 0.0, headGlow = 0.0) {
    const idx = this._ribbonVertCursor;
    const pOff = idx * 3;
    const cOff = idx * 4;
    this._ribbonPosArr[pOff] = vx;
    this._ribbonPosArr[pOff + 1] = vy;
    this._ribbonPosArr[pOff + 2] = vz;
    this._ribbonColArr[cOff] = r;
    this._ribbonColArr[cOff + 1] = g;
    this._ribbonColArr[cOff + 2] = b;
    this._ribbonColArr[cOff + 3] = va;
    this._ribbonAcrossArr[idx] = across;
    this._ribbonMetaArr[pOff] = along;
    this._ribbonMetaArr[pOff + 1] = mode;
    this._ribbonMetaArr[pOff + 2] = headGlow;
    this._ribbonVertCursor = idx + 1;
  }

  _writeConeMatrix(kind, x, y, z, dx, dy, dz, sx, sy, sz, ageNorm, seed) {
    const idx = this._coneWriteCount;
    if (idx >= MAX_CONE_FX) return;
    this._pos.set(x, y, z);
    this._dir.set(
      Number.isFinite(dx) ? dx : 0,
      Number.isFinite(dy) ? dy : 0,
      Number.isFinite(dz) ? dz : 0,
    );
    if (this._dir.lengthSq() < 1e-6) this._dir.set(0, 1, 0);
    else this._dir.normalize();
    this._quat.setFromUnitVectors(this._forwardAxis, this._dir);
    this._scale.set(sx, sy, sz);
    this._mat4.compose(this._pos, this._quat, this._scale);
    this.coneFxPool.setMatrixAt(idx, this._mat4);
    this._coneAttrKind.array[idx] = kind;
    this._coneAttrAge.array[idx] = ageNorm;
    this._coneAttrSeed.array[idx] = seed;
    this._coneWriteCount = idx + 1;
  }

  /**
   * 准备 2–4 个非对称主喷射瓣中心角与能量权重（彻底消灭均匀对称圆环感）。
   */
  _prepareAsymmetricLobes(seedBase, lobeCount) {
    const n = clamp(lobeCount, 2, 4);
    const baseAng = hash11(seedBase * 1.13 + 0.7) * Math.PI * 2;
    let accum = baseAng;
    for (let i = 0; i < n; i += 1) {
      const step = (Math.PI * 2 / n) * (0.62 + 0.76 * hash11(seedBase * 3.7 + i * 19.1));
      accum += step;
      this._lobeAngles[i] = accum;
      // 第 0 主瓣能量较强，其余副瓣形成错落层次
      this._lobeWeights[i] = i === 0 ? 1.24 : (0.72 + 0.42 * hash11(seedBase * 5.3 + i * 11.7));
    }
    return n;
  }

  /**
   * 写入层 5 贴合地形 FBM 焦黑弹坑（512 双分区 + 空间重叠去重合并，重型主弹坑永不被 30mm 小弹点顶掉）。
   */
  _addCrater(worldPos, radius) {
    const wx = Number(worldPos?.x) || 0;
    const wz = Number(worldPos?.z) || 0;
    const r = Math.max(0.8, Number(radius) || 2.0);
    const isMajor = r >= 4.0;

    if (!isMajor) {
      // 1) 轻型弹点若落在现有主弹坑范围内，直接跳过不浪费槽位且避免叠黑过绘
      for (let i = 0; i < MAJOR_CRATER_SLOTS; i += 1) {
        const c = this._craterSlots[i];
        if (!c.active) continue;
        const dx = wx - c.x;
        const dz = wz - c.z;
        const thr = c.radius * 0.78;
        if (dx * dx + dz * dz <= thr * thr) {
          this._dedupedCraterCount += 1;
          return;
        }
      }
      // 2) 若极近处已有轻型弹点，仅刷新余烬年龄并略微扩张半径，不重复占槽
      for (let i = MAJOR_CRATER_SLOTS; i < MAX_CRATERS; i += 1) {
        const c = this._craterSlots[i];
        if (!c.active) continue;
        const dx = wx - c.x;
        const dz = wz - c.z;
        const thr = Math.max(r, c.radius) * 0.62;
        if (dx * dx + dz * dz <= thr * thr) {
          c.radius = Math.min(3.4, Math.max(c.radius, r) * 1.06);
          c.ageSec = 0;
          this._craterDirty = true;
          this._dedupedCraterCount += 1;
          return;
        }
      }
    } else {
      // 主弹坑空间同心去重（允许边缘叠压形成连片弹坑群，仅合并几乎同心重叠）
      for (let i = 0; i < MAJOR_CRATER_SLOTS; i += 1) {
        const c = this._craterSlots[i];
        if (!c.active) continue;
        const dx = wx - c.x;
        const dz = wz - c.z;
        const thr = Math.max(r, c.radius) * 0.36;
        if (dx * dx + dz * dz <= thr * thr) {
          c.radius = Math.max(c.radius, r);
          c.ageSec = 0;
          this._craterDirty = true;
          this._dedupedCraterCount += 1;
          return;
        }
      }
      // 吸收覆盖范围内的旧轻型小弹点，腾出轻型槽位并降低 GPU 过绘
      const absorbR2 = (r * 0.72) * (r * 0.72);
      for (let i = MAJOR_CRATER_SLOTS; i < MAX_CRATERS; i += 1) {
        const c = this._craterSlots[i];
        if (!c.active) continue;
        const dx = wx - c.x;
        const dz = wz - c.z;
        if (dx * dx + dz * dz <= absorbR2) {
          c.active = false;
        }
      }
    }

    let slotIdx;
    if (isMajor) {
      slotIdx = this._majorCraterCursor;
      this._majorCraterCursor = (this._majorCraterCursor + 1) % MAJOR_CRATER_SLOTS;
    } else {
      slotIdx = MAJOR_CRATER_SLOTS + this._lightCraterCursor;
      this._lightCraterCursor = (this._lightCraterCursor + 1) % LIGHT_CRATER_SLOTS;
    }
    const slot = this._craterSlots[slotIdx];
    const gy = this.terrainSupport?.heightAt?.(wx, wz) ?? (worldPos.y || 0);
    slot.active = true;
    slot.x = wx;
    slot.y = gy + 0.16;
    slot.z = wz;
    slot.radius = r;
    slot.rotY = ((wx * 13.7 + wz * 7.3) % (Math.PI * 2));
    slot.seed = fract(Math.abs(wx * 0.371 + wz * 0.619 + r * 0.17)) * 10.0;
    slot.ageSec = 0;
    this._craterDirty = true;
  }

  /**
   * Phase 2 精密 2m 高度场就绪后重新将既有弹坑贴合到精确地面高度与坡度。
   */
  realignCratersToTerrain() {
    if (!this.terrainSupport) return;
    for (let i = 0; i < MAX_CRATERS; i += 1) {
      const c = this._craterSlots[i];
      if (!c.active) continue;
      const gy = this.terrainSupport.heightAt?.(c.x, c.z);
      if (Number.isFinite(gy)) c.y = gy + 0.16;
    }
    this._craterDirty = true;
  }

  _syncCraterPool(dt) {
    let anyEmberAging = false;
    for (let i = 0; i < MAX_CRATERS; i += 1) {
      const c = this._craterSlots[i];
      if (!c.active) continue;
      if (c.ageSec < 8.0) {
        c.ageSec += dt;
        anyEmberAging = true;
      }
    }
    if (!this._craterDirty && !anyEmberAging) return;
    const needMatrix = this._craterDirty;
    this._craterDirty = false;
    let count = 0;
    let majorCount = 0;
    let lightCount = 0;
    const eps = 2.0;
    for (let i = 0; i < MAX_CRATERS; i += 1) {
      const c = this._craterSlots[i];
      if (!c.active) continue;
      if (i < MAJOR_CRATER_SLOTS) majorCount += 1;
      else lightCount += 1;
      if (needMatrix) {
        const hL = this.terrainSupport?.heightAt?.(c.x - eps, c.z) ?? c.y;
        const hR = this.terrainSupport?.heightAt?.(c.x + eps, c.z) ?? c.y;
        const hD = this.terrainSupport?.heightAt?.(c.x, c.z - eps) ?? c.y;
        const hU = this.terrainSupport?.heightAt?.(c.x, c.z + eps) ?? c.y;
        this._dir.set(hL - hR, 2.0 * eps, hD - hU).normalize();
        this._quat.setFromUnitVectors(this._up, this._dir);
        this._tmpQuat.setFromAxisAngle(this._dir, c.rotY);
        this._quat.premultiply(this._tmpQuat);
        this._pos.set(c.x, c.y, c.z);
        this._scale.set(c.radius, 1, c.radius);
        this._mat4.compose(this._pos, this._quat, this._scale);
        this.craterPool.setMatrixAt(count, this._mat4);
        this._craterAttrSeed.array[count] = c.seed;
        this._craterAttrRadius.array[count] = c.radius;
      }
      this._craterAttrAgeSec.array[count] = c.ageSec;
      count += 1;
    }
    this._activeCraterCount = count;
    this._activeMajorCraterCount = majorCount;
    this._activeLightCraterCount = lightCount;
    this.craterPool.count = count;
    this.craterPool.visible = count > 0;
    if (count > 0) {
      if (needMatrix) {
        this.craterPool.instanceMatrix.needsUpdate = true;
        this._craterAttrSeed.needsUpdate = true;
        this._craterAttrRadius.needsUpdate = true;
      }
      this._craterAttrAgeSec.needsUpdate = true;
    }
  }

  /**
   * 批量发射池 6 `kind=5` 细长冷白/灰白破片烟丝射流（复刻《Defilade》高压破片高速撕裂空气的白烟丝轨迹）。
   */
  _spawnRadialFragmentJets(bx, gy, bz, {
    count = 8,
    minLen = 14,
    maxLen = 28,
    minLife = 0.42,
    maxLife = 0.72,
    minRadius = 0.14,
    maxRadius = 0.28,
    minElevDeg = 12,
    maxElevDeg = 52,
    lobeCount = 3,
    simNowSec = 0,
  }) {
    const n = Math.max(0, Math.round(count));
    for (let i = 0; i < n; i += 1) {
      const c = this._allocCone();
      const h1 = hash11(simNowSec * 19.3 + i * 7.13 + bx * 0.11);
      const h2 = hash11(simNowSec * 11.7 + i * 13.37 + bz * 0.17);
      const h3 = hash11(simNowSec * 5.9 + i * 23.71);
      const lobeIdx = i % Math.max(1, lobeCount);
      const azim = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 0.95;
      const elevDeg = minElevDeg + h2 * (maxElevDeg - minElevDeg);
      const elevRad = (elevDeg * Math.PI) / 180;
      const cosE = Math.cos(elevRad);
      const wMul = this._lobeWeights[lobeIdx] || 1.0;

      c.active = true;
      c.kind = 5;
      c.age = 0;
      c.life = minLife + h3 * (maxLife - minLife);
      c.x = bx + Math.cos(azim) * cosE * 0.9;
      c.y = gy + 0.45 + Math.sin(elevRad) * 0.6;
      c.z = bz + Math.sin(azim) * cosE * 0.9;
      c.dx = Math.cos(azim) * cosE;
      c.dy = Math.sin(elevRad);
      c.dz = Math.sin(azim) * cosE;
      const rad = minRadius + h1 * (maxRadius - minRadius);
      c.sx = rad;
      c.sy = rad;
      c.sz = (minLen + h2 * (maxLen - minLen)) * (0.82 + 0.22 * wMul);
      c.seed = (i * 0.53 + simNowSec) % 10;
      this.radialFragmentJetCount += 1;
    }
  }

  /**
   * 批量发射池 6 `kind=6` 极细 HDR 拉丝火花带（真实重力抛物线运动 + 遇地弹跳衰减 + 680p 最小像素宽防闪烁）。
   */
  _spawnSparkStreaks(bx, by, bz, {
    count = 12,
    minSpeed = 16,
    maxSpeed = 38,
    minElevDeg = 18,
    maxElevDeg = 68,
    minLife = 0.26,
    maxLife = 0.62,
    radius = 0.075,
    lengthScale = 0.095,
    lobeCount = 3,
    simNowSec = 0,
  }) {
    const n = Math.max(0, Math.round(count));
    for (let i = 0; i < n; i += 1) {
      const c = this._allocCone();
      const h1 = hash11(simNowSec * 31.1 + i * 17.7 + bx * 0.23);
      const h2 = hash11(simNowSec * 17.3 + i * 29.3 + bz * 0.19);
      const h3 = hash11(simNowSec * 9.7 + i * 41.9);
      const lobeIdx = i % Math.max(1, lobeCount);
      const azim = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.45;
      const elevRad = ((minElevDeg + h2 * (maxElevDeg - minElevDeg)) * Math.PI) / 180;
      const spd = minSpeed + h3 * (maxSpeed - minSpeed);
      const cosE = Math.cos(elevRad);

      c.active = true;
      c.kind = 6;
      c.age = 0;
      c.life = minLife + h1 * (maxLife - minLife);
      c.x = bx + Math.cos(azim) * 0.45;
      c.y = by + 0.35 + h2 * 0.4;
      c.z = bz + Math.sin(azim) * 0.45;
      c.vx = Math.cos(azim) * cosE * spd;
      c.vy = Math.sin(elevRad) * spd;
      c.vz = Math.sin(azim) * cosE * spd;
      c.gravity = 24.0;
      c.drag = 0.72;
      c.dx = -c.vx;
      c.dy = -c.vy;
      c.dz = -c.vz;
      c.sx = radius * (0.8 + 0.45 * h2);
      c.sy = c.sx;
      c.sz = clamp(spd * lengthScale, 1.1, 4.4);
      c.seed = (i * 0.71 + simNowSec) % 10;
      this.sparkStreakEmittedCount += 1;
    }
  }

  /**
   * 触发三型差异化《Defilade》写实 5 层爆炸（V3.0）：
   * - `heavy` (2000 磅 GBU-31 JDAM): 瞬时白橙过曝爆闪 + 非发光破碎外卷扬尘气浪 + 细长冷白破片烟丝 + 极细拉丝火花 +
   *   非发光暗褐泥土喷柱 + 黑体辐射缝隙火球 + 三段动力学翻滚立体自阴影炭黑蘑菇云 + FBM 焦坑
   * - `medium` (AGM-65 / Hydra 70): 锐利闪爆 + 贴地破碎扬尘 + 细长冷白破片烟丝 + 密集拉丝火花 + 紧凑翻滚立体黑灰烟柱
   * - `light` (GAU-8 30mm HEI) / `light_api` (30mm API): 干脆碰击闪光 + 极细金白拉丝火花 + 深褐碎土 + 贴地浅灰尘烟
   */
  spawnExplosion(worldPos, tier = 'medium', dir = null, simNowSec = 0) {
    const jitterCrater = fract(Math.abs((worldPos.x || 0) * 7.1 + (worldPos.z || 0) * 3.7));
    const craterR = tier === 'heavy'
      ? 14.5
      : tier === 'medium'
        ? 6.5
        : tier === 'light_api'
          ? (1.25 + jitterCrater * 0.55)
          : (2.1 + jitterCrater * 0.85);
    this._addCrater(worldPos, craterR);

    const cullRadius = tier === 'heavy' ? 88 : tier === 'medium' ? 48 : 14;
    const inFrustum = this._isInFrustum(worldPos, cullRadius);
    const gy = this.terrainSupport?.heightAt?.(worldPos.x, worldPos.z) ?? (worldPos.y || 0);
    const bx = worldPos.x;
    const by = Math.max(gy + 0.2, worldPos.y || gy + 0.2);
    const bz = worldPos.z;
    const seedBase = bx * 0.137 + bz * 0.293 + simNowSec * 3.17;

    // QA-05：视锥外重/中型爆炸跳过瞬态爆闪与碎片，但保留长寿炭黑蘑菇烟柱，彻底杜绝移镜回看“有坑无烟”
    if (!inFrustum) {
      if (tier === 'heavy' || tier === 'medium') {
        const lobeCount = this._prepareAsymmetricLobes(seedBase, tier === 'heavy' ? 3 : 2);
        const offPlumeCount = tier === 'heavy' ? 8 : 4;
        for (let i = 0; i < offPlumeCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 19.3 + 11.7);
          const h2 = hash11(seedBase + i * 29.7 + 4.3);
          const fracY = i / Math.max(1, offPlumeCount - 1);
          const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.3;
          s.active = true; s.age = 0;
          s.life = tier === 'heavy' ? (5.4 + h1 * 2.4) : (3.0 + h1 * 1.2);
          s.layerKind = 2;
          s.x = bx + Math.cos(ang) * (tier === 'heavy' ? 3.2 : 1.4);
          s.y = by + (tier === 'heavy' ? (2.2 + fracY * 8.5) : (1.2 + fracY * 3.5));
          s.z = bz + Math.sin(ang) * (tier === 'heavy' ? 3.2 : 1.4);
          s.vx = Math.cos(ang) * (tier === 'heavy' ? 5.5 : 3.2);
          s.vy = (tier === 'heavy' ? 12.5 : 7.5) + fracY * 5.0;
          s.vz = Math.sin(ang) * (tier === 'heavy' ? 5.5 : 3.2);
          s.windX = 2.2; s.windZ = 0.9;
          s.gravity = 0.0; s.drag = 1.0;
          s.burstDrag = 5.2; s.burstWindowSec = 0.25;
          s.buoyancy = tier === 'heavy' ? 1.5 : 1.1;
          s.expGrowK = 4.2;
          s.baseScale = tier === 'heavy' ? (5.6 + h1 * 2.2) : (2.8 + h1 * 1.1);
          s.endScale = tier === 'heavy' ? (20.5 + h2 * 7.0) : (10.2 + h2 * 3.6);
          s.rot = h2 * Math.PI * 2;
          s.rotSpeed = (h1 - 0.5) * 0.6;
          s.seed = (i * 0.43 + simNowSec) % 10;
          s.clusterHeightNorm = 0.2 + fracY * 0.78;
          s.heatGain = (1.0 - fracY) * 0.5;
        }
      }
      return;
    }

    const lod = this.lodTier;
    const density = lod === 'near' ? 1.0 : lod === 'mid' ? 0.55 : 0.25;

    if (tier === 'heavy') {
      // --- 2000 磅 GBU-31 重磅航弹：非对称 4 瓣爆轰 + 三段动力学 + 8×8 立体翻滚炭黑烟云 ---
      const lobeCount = this._prepareAsymmetricLobes(seedBase, 4);

      // L1：极短白橙过曝爆闪核（0–90ms）+ 贴地假光晕（零 PointLight）
      const flash = this._allocBlast();
      flash.active = true; flash.age = 0; flash.life = 0.095;
      flash.layerKind = 0;
      flash.x = bx; flash.y = by + 2.8; flash.z = bz;
      flash.baseScale = 4.2; flash.endScale = 24.0; flash.intensity = 1.48;
      flash.seed = seedBase; flash.rotY = hash11(seedBase) * Math.PI * 2;

      const glow = this._allocBlast();
      glow.active = true; glow.age = 0; glow.life = 0.28;
      glow.layerKind = 3;
      glow.x = bx; glow.y = gy + 0.26; glow.z = bz;
      glow.baseScale = 11.0; glow.endScale = 58.0; glow.intensity = 1.25;

      const ember = this._allocBlast();
      ember.active = true; ember.age = 0; ember.life = 2.6;
      ember.layerKind = 3;
      ember.x = bx; ember.y = gy + 0.20; ember.z = bz;
      ember.baseScale = 4.5; ember.endScale = 14.0; ember.intensity = 0.42;

      if (lod !== 'far') {
        // L2：非发光贴地外卷破碎扬尘气浪（彻底替代科幻半球护盾与规则双圆环）
        const dustWave = this._allocBlast();
        dustWave.active = true; dustWave.age = 0; dustWave.life = 0.85;
        dustWave.layerKind = 4; dustWave.tag = 'heavy_wave_1';
        dustWave.x = bx; dustWave.y = gy + 0.24; dustWave.z = bz;
        dustWave.baseScale = 5.5; dustWave.endScale = 64.0; dustWave.intensity = 1.15;
        dustWave.seed = seedBase + 1.3; dustWave.rotY = hash11(seedBase + 2.1) * Math.PI * 2;

        // L2：破碎云絮冷灰白破片烟缕（8–10 条，适度缩短避免满屏笔直蜘蛛白网）
        this._spawnRadialFragmentJets(bx, gy, bz, {
          count: lod === 'near' ? 9 : 5,
          minLen: 15,
          maxLen: 29,
          minLife: 0.42,
          maxLife: 0.72,
          minRadius: 0.24,
          maxRadius: 0.42,
          minElevDeg: 14,
          maxElevDeg: 46,
          lobeCount,
          simNowSec,
        });

        // L3：极细 HDR 拉丝火花雨（独立于暗褐泥块）
        this._spawnSparkStreaks(bx, by, bz, {
          count: Math.max(8, Math.round(24 * density)),
          minSpeed: 18,
          maxSpeed: 42,
          minElevDeg: 16,
          maxElevDeg: 64,
          minLife: 0.34,
          maxLife: 0.78,
          radius: 0.085,
          lengthScale: 0.105,
          lobeCount,
          simNowSec,
        });

        // L2/L4：非对称浅灰白贴地翻滚气浪尘墙（与中轴炭黑烟柱形成《Defilade》高反差）
        const dustCloudCount = Math.max(5, Math.round(18 * density));
        for (let i = 0; i < dustCloudCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 7.31);
          const h2 = hash11(seedBase + i * 13.7);
          const lobeIdx = i % lobeCount;
          const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.15;
          const spd = (34 + h2 * 22) * this._lobeWeights[lobeIdx];
          s.active = true; s.age = 0;
          s.life = 2.0 + h1 * 1.05;
          s.layerKind = 5;
          s.x = bx + Math.cos(ang) * (2.5 + h2 * 3.0);
          s.y = gy + 1.4 + h1 * 1.2;
          s.z = bz + Math.sin(ang) * (2.5 + h2 * 3.0);
          s.vx = Math.cos(ang) * spd;
          s.vy = 4.2 + h2 * 3.4;
          s.vz = Math.sin(ang) * spd;
          s.windX = 1.6; s.windZ = 0.7;
          s.gravity = 0.8;
          s.drag = 1.45;
          s.burstDrag = 7.8;
          s.burstWindowSec = 0.26;
          s.buoyancy = 0.45;
          s.expGrowK = 5.5;
          s.baseScale = 4.2 + h1 * 2.2;
          s.endScale = 16.5 + h2 * 7.5;
          s.rot = h1 * Math.PI * 2;
          s.rotSpeed = (h2 - 0.5) * 1.1;
          s.seed = (i * 0.23 + simNowSec) % 10;
          s.clusterHeightNorm = 0.22 + h2 * 0.24;
          s.heatGain = 0;
        }
      }

      // L3：炭黑外皮包裹黑体辐射翻滚火球（非对称簇状 + 骤停动力学）
      const fireCount = Math.max(5, Math.round(18 * density));
      for (let i = 0; i < fireCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 11.1 + 3.1);
        const h2 = hash11(seedBase + i * 19.7 + 5.3);
        const lobeIdx = i % lobeCount;
        const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.05;
        const spd = (14.0 + h2 * 14.0) * this._lobeWeights[lobeIdx];
        s.active = true; s.age = 0;
        s.life = 0.85 + h1 * 0.65;
        s.layerKind = 0;
        s.x = bx + Math.cos(ang) * (1.2 + h1 * 2.2);
        s.y = by + 1.4 + h2 * 2.4;
        s.z = bz + Math.sin(ang) * (1.2 + h1 * 2.2);
        s.vx = Math.cos(ang) * spd;
        s.vy = 20.0 + h2 * 18.0;
        s.vz = Math.sin(ang) * spd;
        s.windX = 1.1; s.windZ = 0.45;
        s.gravity = 2.0;
        s.drag = 1.6;
        s.burstDrag = 8.4;
        s.burstWindowSec = 0.24;
        s.buoyancy = 1.4;
        s.expGrowK = 6.2;
        s.baseScale = 4.4 + h1 * 2.4;
        s.endScale = 14.5 + h2 * 6.5;
        s.rot = h2 * Math.PI * 2;
        s.rotSpeed = (h1 - 0.5) * 1.25;
        s.seed = (i * 0.173 + simNowSec) % 10;
        s.clusterHeightNorm = 0.35 + h2 * 0.45;
        s.heatGain = 1.05 + h1 * 0.35;
      }

      // L3：纯化非发光深褐泥土碎块喷柱（彻底去除发光宝石火星）
      if (lod !== 'far') {
        const debrisCount = Math.max(8, Math.round(28 * density));
        for (let i = 0; i < debrisCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 23.3 + 9.7);
          const h2 = hash11(seedBase + i * 31.7 + 2.3);
          const lobeIdx = i % lobeCount;
          const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.25;
          const isHighSpout = i % 3 !== 0;
          const hSpd = isHighSpout ? (3.5 + h2 * 9.0) : (10.0 + h2 * 14.0);
          s.active = true; s.age = 0;
          s.life = isHighSpout ? (1.45 + h1 * 0.65) : (0.95 + h1 * 0.45);
          s.layerKind = 1;
          s.x = bx + Math.cos(ang) * (1.0 + h1 * 1.6);
          s.y = by + 0.7;
          s.z = bz + Math.sin(ang) * (1.0 + h1 * 1.6);
          s.vx = Math.cos(ang) * hSpd;
          s.vy = isHighSpout ? (25.0 + h2 * 16.0) : (14.0 + h2 * 10.0);
          s.vz = Math.sin(ang) * hSpd;
          s.gravity = 24.5;
          s.drag = 0.25;
          s.baseScale = 0.62 + h1 * 0.72;
          s.endScale = s.baseScale * 0.75;
          s.rot = h2 * Math.PI * 2;
          s.rotSpeed = (h1 - 0.5) * 10.0;
          s.seed = (i * 0.371 + simNowSec) % 10;
          s.clusterHeightNorm = 0.5;
          s.heatGain = 0;
        }
      }

      // L4：两段式立体翻滚炭黑蘑菇云（底部暗茎干 4.8–6.8s + 顶部迎阳伞盖 6.2–8.5s）
      const stemCount = Math.max(3, Math.round(12 * density));
      for (let i = 0; i < stemCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 17.9 + 14.1);
        const h2 = hash11(seedBase + i * 29.1 + 4.7);
        const fracY = i / Math.max(1, stemCount - 1);
        const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.5;
        s.active = true; s.age = 0;
        s.life = 4.8 + h1 * 2.0;
        s.layerKind = 2;
        s.x = bx + Math.cos(ang) * (1.4 + h2 * 2.6);
        s.y = by + 1.8 + fracY * 7.5;
        s.z = bz + Math.sin(ang) * (1.4 + h2 * 2.6);
        s.vx = Math.cos(ang) * (2.5 + h2 * 3.5);
        s.vy = 10.0 + fracY * 6.5 + h1 * 3.0;
        s.vz = Math.sin(ang) * (2.5 + h2 * 3.5);
        s.windX = 2.1; s.windZ = 0.85;
        s.gravity = 0.0;
        s.drag = 0.95;
        s.burstDrag = 4.5;
        s.burstWindowSec = 0.28;
        s.buoyancy = 1.65;
        s.expGrowK = 3.8;
        s.baseScale = 5.2 + h1 * 2.2;
        s.endScale = 16.0 + h2 * 5.5;
        s.rot = h2 * Math.PI * 2;
        s.rotSpeed = (h1 - 0.5) * 0.55;
        s.seed = (i * 0.43 + simNowSec) % 10;
        s.clusterHeightNorm = 0.12 + fracY * 0.48;
        s.heatGain = (1.0 - fracY) * 0.85;
      }

      const capCount = Math.max(4, Math.round(16 * density));
      for (let i = 0; i < capCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 37.3 + 21.7);
        const h2 = hash11(seedBase + i * 43.1 + 8.9);
        const lobeIdx = i % lobeCount;
        const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.15;
        const wMul = this._lobeWeights[lobeIdx];
        s.active = true; s.age = 0;
        s.life = 6.2 + h1 * 2.4;
        s.layerKind = 2;
        s.x = bx + Math.cos(ang) * (2.8 + h2 * 3.8);
        s.y = by + 6.5 + h2 * 5.2;
        s.z = bz + Math.sin(ang) * (2.8 + h2 * 3.8);
        s.vx = Math.cos(ang) * (12.0 + h2 * 10.0) * wMul;
        s.vy = 20.0 + h1 * 11.0;
        s.vz = Math.sin(ang) * (12.0 + h2 * 10.0) * wMul;
        s.windX = 2.6; s.windZ = 1.05;
        s.gravity = 0.25;
        s.drag = 1.05;
        s.burstDrag = 6.8;
        s.burstWindowSec = 0.28;
        s.buoyancy = 1.35;
        s.expGrowK = 4.6;
        s.baseScale = 6.5 + h1 * 2.8;
        s.endScale = 24.0 + h2 * 8.5;
        s.rot = h1 * Math.PI * 2;
        s.rotSpeed = (h2 - 0.5) * 0.62;
        s.seed = (i * 0.61 + simNowSec) % 10;
        s.clusterHeightNorm = 0.68 + h2 * 0.32;
        s.heatGain = 0.45;
      }
      return;
    }

    if (tier === 'medium') {
      // --- AGM-65 导弹 / Hydra 70 火箭：非对称 3 瓣聚能破片爆轰 ---
      const lobeCount = this._prepareAsymmetricLobes(seedBase, 3);

      const flash = this._allocBlast();
      flash.active = true; flash.age = 0; flash.life = 0.075;
      flash.layerKind = 0;
      flash.x = bx; flash.y = by + 1.2; flash.z = bz;
      flash.baseScale = 2.2; flash.endScale = 12.5; flash.intensity = 1.32;
      flash.seed = seedBase; flash.rotY = hash11(seedBase) * Math.PI * 2;

      const glow = this._allocBlast();
      glow.active = true; glow.age = 0; glow.life = 0.20;
      glow.layerKind = 3;
      glow.x = bx; glow.y = gy + 0.22; glow.z = bz;
      glow.baseScale = 5.0; glow.endScale = 24.0; glow.intensity = 1.08;

      if (lod !== 'far') {
        const dustWave = this._allocBlast();
        dustWave.active = true; dustWave.age = 0; dustWave.life = 0.56;
        dustWave.layerKind = 4; dustWave.tag = 'medium_wave';
        dustWave.x = bx; dustWave.y = gy + 0.18; dustWave.z = bz;
        dustWave.baseScale = 2.6; dustWave.endScale = 30.0; dustWave.intensity = 1.0;
        dustWave.seed = seedBase + 2.7; dustWave.rotY = hash11(seedBase + 1.7) * Math.PI * 2;

        // 破碎云絮冷灰白破片烟缕（4–6 条）
        this._spawnRadialFragmentJets(bx, gy, bz, {
          count: lod === 'near' ? 6 : 4,
          minLen: 9,
          maxLen: 18,
          minLife: 0.32,
          maxLife: 0.54,
          minRadius: 0.18,
          maxRadius: 0.30,
          minElevDeg: 14,
          maxElevDeg: 48,
          lobeCount,
          simNowSec,
        });

        // 极细拉丝火花
        this._spawnSparkStreaks(bx, by, bz, {
          count: Math.max(5, Math.round(14 * density)),
          minSpeed: 14,
          maxSpeed: 32,
          minElevDeg: 18,
          maxElevDeg: 66,
          minLife: 0.26,
          maxLife: 0.56,
          radius: 0.072,
          lengthScale: 0.095,
          lobeCount,
          simNowSec,
        });

        // 非对称浅灰白翻滚气浪尘团
        const medDustCount = Math.max(3, Math.round(9 * density));
        for (let i = 0; i < medDustCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 9.1);
          const h2 = hash11(seedBase + i * 17.3);
          const lobeIdx = i % lobeCount;
          const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.1;
          const spd = (20 + h2 * 14) * this._lobeWeights[lobeIdx];
          s.active = true; s.age = 0;
          s.life = 1.35 + h1 * 0.65;
          s.layerKind = 5;
          s.x = bx + Math.cos(ang) * 1.6;
          s.y = gy + 0.9 + h2 * 0.7;
          s.z = bz + Math.sin(ang) * 1.6;
          s.vx = Math.cos(ang) * spd;
          s.vy = 2.6 + h2 * 2.2;
          s.vz = Math.sin(ang) * spd;
          s.windX = 1.3; s.windZ = 0.5;
          s.gravity = 0.6;
          s.drag = 1.65;
          s.burstDrag = 8.0;
          s.burstWindowSec = 0.22;
          s.buoyancy = 0.35;
          s.expGrowK = 5.4;
          s.baseScale = 2.4 + h1 * 1.2;
          s.endScale = 9.2 + h2 * 3.8;
          s.rot = h1 * Math.PI * 2;
          s.rotSpeed = (h2 - 0.5) * 1.2;
          s.seed = (i * 0.31 + simNowSec) % 10;
          s.clusterHeightNorm = 0.25 + h2 * 0.25;
          s.heatGain = 0;
        }
      }

      // 爆心黑体火团
      const fireballCount = Math.max(3, Math.round(9 * density));
      for (let i = 0; i < fireballCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 15.7 + 4.3);
        const h2 = hash11(seedBase + i * 21.9 + 1.1);
        const lobeIdx = i % lobeCount;
        const ang = this._lobeAngles[lobeIdx] + (h1 - 0.5) * 1.05;
        const spd = (8.0 + h2 * 8.5) * this._lobeWeights[lobeIdx];
        s.active = true; s.age = 0;
        s.life = 0.48 + h1 * 0.32;
        s.layerKind = 0;
        s.x = bx + Math.cos(ang) * 0.8; s.y = by + 0.7 + h2 * 0.9; s.z = bz + Math.sin(ang) * 0.8;
        s.vx = Math.cos(ang) * spd; s.vy = 11.5 + h2 * 9.0; s.vz = Math.sin(ang) * spd;
        s.windX = 0.9; s.windZ = 0.35;
        s.gravity = 2.5;
        s.drag = 1.8;
        s.burstDrag = 8.6;
        s.burstWindowSec = 0.20;
        s.buoyancy = 1.1;
        s.expGrowK = 6.0;
        s.baseScale = 2.4 + h1 * 1.1; s.endScale = 7.8 + h2 * 3.2;
        s.rot = h2 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 1.5;
        s.seed = (i * 0.173 + simNowSec) % 10;
        s.clusterHeightNorm = 0.38 + h2 * 0.42;
        s.heatGain = 1.0 + h1 * 0.3;
      }

      // 非发光深褐泥土碎块
      if (lod !== 'far') {
        const debrisCount = Math.max(4, Math.round(14 * density));
        for (let i = 0; i < debrisCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 27.1 + 7.9);
          const h2 = hash11(seedBase + i * 33.7 + 1.9);
          const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.3;
          const hSpd = 6.0 + h2 * 12.0;
          s.active = true; s.age = 0;
          s.life = 0.62 + h1 * 0.38;
          s.layerKind = 1;
          s.x = bx; s.y = by + 0.45; s.z = bz;
          s.vx = Math.cos(ang) * hSpd; s.vy = 11.0 + h2 * 10.5; s.vz = Math.sin(ang) * hSpd;
          s.gravity = 24.5; s.drag = 0.30;
          s.baseScale = 0.38 + h1 * 0.40; s.endScale = s.baseScale * 0.72;
          s.rot = h2 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 10.5;
          s.seed = (i * 0.41 + simNowSec) % 10;
          s.clusterHeightNorm = 0.5;
          s.heatGain = 0;
        }
      }

      // 立体翻滚灰黑主烟柱
      const plumeCount = Math.max(3, Math.round(10 * density));
      for (let i = 0; i < plumeCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 19.3 + 12.7);
        const h2 = hash11(seedBase + i * 23.9 + 6.1);
        const fracY = i / Math.max(1, plumeCount - 1);
        const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.35;
        s.active = true; s.age = 0;
        s.life = 2.7 + h1 * 1.35;
        s.layerKind = 2;
        s.x = bx + Math.cos(ang) * (0.9 + h2 * 1.6);
        s.y = by + 1.0 + fracY * 3.2;
        s.z = bz + Math.sin(ang) * (0.9 + h2 * 1.6);
        s.vx = Math.cos(ang) * (4.0 + h2 * 4.5);
        s.vy = 7.5 + fracY * 5.0 + h1 * 2.5;
        s.vz = Math.sin(ang) * (4.0 + h2 * 4.5);
        s.windX = 1.8; s.windZ = 0.75;
        s.gravity = 0.0;
        s.drag = 1.05;
        s.burstDrag = 5.6;
        s.burstWindowSec = 0.24;
        s.buoyancy = 1.25;
        s.expGrowK = 4.4;
        s.baseScale = 2.8 + h1 * 1.2; s.endScale = 10.5 + h2 * 4.2;
        s.rot = h1 * Math.PI * 2; s.rotSpeed = (h2 - 0.5) * 0.85;
        s.seed = (i * 0.41 + simNowSec) % 10;
        s.clusterHeightNorm = 0.18 + fracY * 0.82;
        s.heatGain = (1.0 - fracY) * 0.65;
      }
      return;
    }

    if (tier === 'light_api') {
      // --- `light_api`: GAU-8 PGU-14/B 贫铀穿甲燃烧弹 (API, 具强自燃性 Pyrophoric：白热火花簇 + 高速泥尘柱) ---
      this.apiImpactCount += 1;
      const lobeCount = this._prepareAsymmetricLobes(seedBase, 2);

      const apiFlash = this._allocBlast();
      apiFlash.active = true; apiFlash.age = 0; apiFlash.life = 0.056;
      apiFlash.layerKind = 0;
      apiFlash.x = bx; apiFlash.y = by + 0.36; apiFlash.z = bz;
      apiFlash.baseScale = 0.85; apiFlash.endScale = 2.9; apiFlash.intensity = 1.24;
      apiFlash.seed = seedBase; apiFlash.rotY = hash11(seedBase) * Math.PI * 2;

      if (lod !== 'far') {
        // 贫铀自燃撞击耀眼白金拉丝火花簇（池 6 kind=6）
        this._spawnSparkStreaks(bx, by, bz, {
          count: Math.max(4, Math.round(8 * density)),
          minSpeed: 14,
          maxSpeed: 28,
          minElevDeg: 32,
          maxElevDeg: 76,
          minLife: 0.24,
          maxLife: 0.50,
          radius: 0.062,
          lengthScale: 0.095,
          lobeCount,
          simNowSec,
        });

        // 暗褐碎土飞溅（池 3 layerKind=1 非发光）
        const clodCount = Math.max(3, Math.round(5 * density));
        for (let i = 0; i < clodCount; i += 1) {
          const s = this._allocSmoke();
          const h1 = hash11(seedBase + i * 13.1);
          const h2 = hash11(seedBase + i * 21.3);
          const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.5;
          const elevRad = ((44 + h2 * 32) * Math.PI) / 180;
          const spd = 11.5 + h1 * 7.5;
          s.active = true; s.age = 0;
          s.life = 0.42 + h2 * 0.22;
          s.layerKind = 1;
          s.x = bx + Math.cos(ang) * 0.35; s.y = by + 0.24; s.z = bz + Math.sin(ang) * 0.35;
          s.vx = Math.cos(ang) * Math.cos(elevRad) * spd;
          s.vy = Math.sin(elevRad) * spd;
          s.vz = Math.sin(ang) * Math.cos(elevRad) * spd;
          s.gravity = 24.5; s.drag = 0.35;
          s.baseScale = 0.36 + h1 * 0.24; s.endScale = 0.24;
          s.rot = h2 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 11.0;
          s.seed = (simNowSec + i * 0.29) % 10;
          s.clusterHeightNorm = 0.5;
          s.heatGain = 0;
        }

        // 翻滚灰褐冲击泥烟柱（构建扫射连片硝烟幕）
        const dirt = this._allocSmoke();
        dirt.active = true; dirt.age = 0;
        dirt.life = 1.75 + hash11(seedBase + 1.9) * 0.55;
        dirt.layerKind = 2;
        dirt.x = bx; dirt.y = gy + 0.75; dirt.z = bz;
        dirt.vx = Math.cos(simNowSec * 3.1) * 1.8; dirt.vy = 5.6; dirt.vz = Math.sin(simNowSec * 3.1) * 1.8;
        dirt.windX = 1.25; dirt.windZ = 0.5;
        dirt.gravity = 0.0; dirt.drag = 1.45;
        dirt.burstDrag = 5.2; dirt.burstWindowSec = 0.18;
        dirt.buoyancy = 0.65; dirt.expGrowK = 4.2;
        dirt.baseScale = 1.65; dirt.endScale = 5.4;
        dirt.rot = hash11(seedBase + 4.1) * Math.PI * 2; dirt.rotSpeed = 0.85;
        dirt.seed = (simNowSec * 0.73) % 10;
        dirt.clusterHeightNorm = 0.52;
        dirt.heatGain = 0.15;

        if (lod === 'near') {
          const dust = this._allocSmoke();
          dust.active = true; dust.age = 0;
          dust.life = 1.45;
          dust.layerKind = 5;
          dust.x = bx; dust.y = gy + 0.55; dust.z = bz;
          dust.vx = Math.cos(simNowSec * 4.7) * 5.2; dust.vy = 1.8; dust.vz = Math.sin(simNowSec * 4.7) * 5.2;
          dust.windX = 1.1; dust.windZ = 0.45;
          dust.gravity = 0.42; dust.drag = 1.8;
          dust.burstDrag = 5.8; dust.burstWindowSec = 0.18;
          dust.expGrowK = 4.6;
          dust.baseScale = 1.75; dust.endScale = 5.8;
          dust.rot = hash11(seedBase + 7.7) * Math.PI * 2; dust.rotSpeed = 0.9;
          dust.seed = (simNowSec * 0.91) % 10;
          dust.clusterHeightNorm = 0.3;
          dust.heatGain = 0;
        }
      }
      return;
    }

    // --- `light`: GAU-8 30mm PGU-13/B HEI 高爆燃烧弹 (4:1 Combat Mix 20% 高能连环爆轰，强化扫射覆盖壮观度) ---
    const lobeCount = this._prepareAsymmetricLobes(seedBase, 2);

    const flash = this._allocBlast();
    flash.active = true; flash.age = 0; flash.life = 0.072;
    flash.layerKind = 0;
    flash.x = bx; flash.y = by + 0.52; flash.z = bz;
    flash.baseScale = 1.35; flash.endScale = 4.6; flash.intensity = 1.32;
    flash.seed = seedBase; flash.rotY = hash11(seedBase) * Math.PI * 2;

    const glow = this._allocBlast();
    glow.active = true; glow.age = 0; glow.life = 0.14;
    glow.layerKind = 3;
    glow.x = bx; glow.y = gy + 0.18; glow.z = bz;
    glow.baseScale = 2.4; glow.endScale = 8.4; glow.intensity = 1.02;

    // 贴地非发光溅射扬尘波（1.4 -> 8.5m, 0.36s，强化扫射走廊连片翻滚冲击感）
    const splashDust = this._allocBlast();
    splashDust.active = true; splashDust.age = 0; splashDust.life = 0.36;
    splashDust.layerKind = 4; splashDust.tag = 'light_splash';
    splashDust.x = bx; splashDust.y = gy + 0.18; splashDust.z = bz;
    splashDust.baseScale = 1.4; splashDust.endScale = 8.5; splashDust.intensity = 0.96;
    splashDust.seed = seedBase + 1.9; splashDust.rotY = hash11(seedBase + 0.9) * Math.PI * 2;
    this.lightSplashRingCount += 1;

    // 接地微爆燃黑体火团（3 枚错落连爆）
    const microFireCount = lod === 'far' ? 1 : 3;
    for (let i = 0; i < microFireCount; i += 1) {
      const s = this._allocSmoke();
      const h1 = hash11(seedBase + i * 11.3);
      const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.1;
      s.active = true; s.age = 0;
      s.life = 0.30 + i * 0.13;
      s.layerKind = 0;
      s.x = bx + Math.cos(ang) * (0.55 + i * 0.25); s.y = by + 0.48 + i * 0.32; s.z = bz + Math.sin(ang) * (0.55 + i * 0.25);
      s.vx = Math.cos(ang) * (3.6 + h1 * 2.2); s.vy = 5.4 + i * 2.0; s.vz = Math.sin(ang) * (3.6 + h1 * 2.2);
      s.gravity = 2.0; s.drag = 1.9;
      s.burstDrag = 6.8; s.burstWindowSec = 0.16;
      s.expGrowK = 5.6;
      s.baseScale = 1.35 + h1 * 0.4; s.endScale = 4.4 + h1 * 1.0;
      s.rot = h1 * Math.PI * 2; s.rotSpeed = 1.6;
      s.seed = (i * 0.29 + simNowSec) % 10;
      s.clusterHeightNorm = 0.42 + i * 0.18;
      s.heatGain = 1.08;
    }

    if (lod !== 'far') {
      // 高能拉丝火花雨（池 6 kind=6）
      this._spawnSparkStreaks(bx, by, bz, {
        count: Math.max(5, Math.round(12 * density)),
        minSpeed: 14,
        maxSpeed: 30,
        minElevDeg: 30,
        maxElevDeg: 75,
        minLife: 0.26,
        maxLife: 0.56,
        radius: 0.068,
        lengthScale: 0.095,
        lobeCount,
        simNowSec,
      });

      // 非发光高抛深褐碎土柱（池 3 layerKind=1）
      const clodCount = Math.max(4, Math.round(9 * density));
      for (let i = 0; i < clodCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 19.7);
        const h2 = hash11(seedBase + i * 29.1);
        const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.35;
        const elevRad = ((42 + h2 * 34) * Math.PI) / 180;
        const spd = 12.0 + h1 * 9.0;
        s.active = true; s.age = 0;
        s.life = 0.52 + h2 * 0.26;
        s.layerKind = 1;
        s.x = bx + Math.cos(ang) * 0.42; s.y = by + 0.30; s.z = bz + Math.sin(ang) * 0.42;
        s.vx = Math.cos(ang) * Math.cos(elevRad) * spd;
        s.vy = Math.sin(elevRad) * spd;
        s.vz = Math.sin(ang) * Math.cos(elevRad) * spd;
        s.gravity = 24.5; s.drag = 0.36;
        s.baseScale = 0.40 + h1 * 0.28; s.endScale = 0.26;
        s.rot = h2 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 10.0;
        s.seed = (i * 0.31 + simNowSec) % 10;
        s.clusterHeightNorm = 0.5;
        s.heatGain = 0;
      }

      // 3 团高耸上涌深褐泥烟（2.1 -> 7.4m, 2.1-2.9s，形成扫射连片硝烟墙）
      const mudSmokeCount = lod === 'near' ? 3 : 1;
      for (let i = 0; i < mudSmokeCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 13.9 + 5.1);
        const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.3;
        s.active = true; s.age = 0;
        s.life = 2.1 + i * 0.45;
        s.layerKind = 2;
        s.x = bx + Math.cos(ang) * 0.75; s.y = gy + 0.85 + i * 0.45; s.z = bz + Math.sin(ang) * 0.75;
        s.vx = Math.cos(ang) * 2.8; s.vy = 6.4 + i * 2.0; s.vz = Math.sin(ang) * 2.8;
        s.windX = 1.4; s.windZ = 0.6;
        s.gravity = 0.0; s.drag = 1.25;
        s.burstDrag = 5.2; s.burstWindowSec = 0.20;
        s.buoyancy = 0.85; s.expGrowK = 4.4;
        s.baseScale = 2.05 + h1 * 0.4; s.endScale = 7.2 + h1 * 1.4;
        s.rot = h1 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 0.9;
        s.seed = (i * 0.43 + simNowSec) % 10;
        s.clusterHeightNorm = 0.36 + i * 0.26;
        s.heatGain = 0.32;
      }

      // 4 团贴地横向翻滚尘幕（2.2 -> 7.8m, 1.6-2.4s）
      const groundDustCount = lod === 'near' ? 4 : 1;
      for (let i = 0; i < groundDustCount; i += 1) {
        const s = this._allocSmoke();
        const h1 = hash11(seedBase + i * 23.1 + 9.3);
        const ang = this._lobeAngles[i % lobeCount] + (h1 - 0.5) * 1.55;
        const hSpd = 7.5 + h1 * 5.2;
        s.active = true; s.age = 0;
        s.life = 1.6 + i * 0.28;
        s.layerKind = 5;
        s.x = bx + Math.cos(ang) * 0.85; s.y = gy + 0.65; s.z = bz + Math.sin(ang) * 0.85;
        s.vx = Math.cos(ang) * hSpd; s.vy = 1.9 + h1 * 1.2; s.vz = Math.sin(ang) * hSpd;
        s.windX = 1.2; s.windZ = 0.5;
        s.gravity = 0.45; s.drag = 1.7;
        s.burstDrag = 6.0; s.burstWindowSec = 0.20;
        s.expGrowK = 4.8;
        s.baseScale = 2.15 + h1 * 0.45; s.endScale = 7.8 + h1 * 1.5;
        s.rot = h1 * Math.PI * 2; s.rotSpeed = (h1 - 0.5) * 1.0;
        s.seed = (i * 0.57 + simNowSec) % 10;
        s.clusterHeightNorm = 0.24;
        s.heatGain = 0;
      }
    }
  }

  /**
   * 为机炮持续开火在机腹后缘推送浅灰白柔雾连续运动拖尾（复用池 5 64段连续共享顶点）。
   */
  emitMuzzleSmokeRibbon(key, worldPos, simNowSec = 0) {
    if (this.lodTier === 'far') return;
    const r = this._allocRibbon(key, 'muzzle_smoke', 1.55, 2.45, 7.2);
    r.alpha = 0.60;
    this._pushRibbonPoint(r, worldPos.x, worldPos.y, worldPos.z, simNowSec, 2.8);
  }

  /**
   * GAU-8 机鼻开火特效（对标真实 A-10C 实拍图1/图2）：
   * - 短促、紧凑、不规则撕裂的炮口亮光（无连帧叠爆大白球）；
   * - 三段饱满浅灰白火药体积云（炮口前喷缓冲团 + 贴机腹裹挟流 + 随飞机运动连续柔雾拖尾）。
   */
  spawnMuzzleFlash(worldPos, dir, simNowSec = 0, aircraftVel = null, ribbonKey = 'gau8_muzzle') {
    const dx = dir?.x || 0;
    const dy = dir?.y || 0;
    const dz = dir?.z || 1;
    const dLen = Math.max(0.01, Math.hypot(dx, dy, dz));
    const ux = dx / dLen;
    const uy = dy / dLen;
    const uz = dz / dLen;
    // 始终持续记录机腹浅灰运动拖尾，防止镜头从靶区切回战机时缺失刚拉出的烟尾
    if (this.lodTier !== 'far') {
      const key = ribbonKey || 'gau8_muzzle';
      this.emitMuzzleSmokeRibbon(
        key,
        {
          x: worldPos.x - ux * 4.2,
          y: worldPos.y - 0.85,
          z: worldPos.z - uz * 4.2,
        },
        simNowSec,
      );
    }
    if (!this._isInFrustum(worldPos, 280)) return;
    // 水平右向基向量，用于机身两侧自然气动扰动
    const sideLen = Math.max(0.01, Math.hypot(uz, ux));
    const rx = uz / sideLen;
    const rz = -ux / sideLen;

    const h1 = hash11(simNowSec * 37.3 + worldPos.x * 0.13);
    const h2 = hash11(simNowSec * 61.7 + worldPos.z * 0.17);
    const h3 = hash11(simNowSec * 89.1 + worldPos.y * 0.29);

    // 1. 极紧凑短促的炮口核心闪点（仅 0.42->1.05m、28ms，彻底消除把机头吞白的大灯泡）
    const flash = this._allocBlast();
    flash.active = true;
    flash.age = 0;
    flash.life = 0.028;
    flash.layerKind = 0;
    flash.x = worldPos.x + ux * 0.35;
    flash.y = worldPos.y + uy * 0.35;
    flash.z = worldPos.z + uz * 0.35;
    flash.baseScale = 0.42 + h1 * 0.16;
    flash.endScale = 0.98 + h2 * 0.28;
    flash.intensity = 0.68 + h3 * 0.24;
    flash.seed = simNowSec * 13.7;
    flash.rotY = h1 * Math.PI * 2;

    // 2. 短促、不规则撕裂炮口喷射亮光（池 6 kind=3，寿命 32-44ms 绝不连帧叠爆）
    const cone = this._allocCone();
    cone.active = true;
    cone.kind = 3;
    cone.age = 0;
    cone.life = 0.032 + h2 * 0.012;
    cone.x = worldPos.x + ux * 0.22;
    cone.y = worldPos.y + uy * 0.22;
    cone.z = worldPos.z + uz * 0.22;
    cone.dx = ux + (h1 - 0.5) * 0.06;
    cone.dy = uy + (h2 - 0.5) * 0.06;
    cone.dz = uz + (h3 - 0.5) * 0.06;
    const muzzleRad = 0.58 + h1 * 0.28;
    const muzzleLen = 1.9 + h3 * 1.35;
    cone.sx = muzzleRad;
    cone.sy = muzzleRad;
    cone.sz = muzzleLen;
    cone.seed = (simNowSec * 29.3 + h1 * 7.1) % 10;

    // 3. 饱满浅灰白火药烟雾：机首前喷包覆团 + 贴机腹裹挟冲刷团（俯视清晰饱满）
    if (this.lodTier !== 'far') {
      const hasPlaneVel = aircraftVel && Number.isFinite(aircraftVel.x);
      const pvX = hasPlaneVel ? aircraftVel.x : ux * 110;
      const pvY = hasPlaneVel ? aircraftVel.y : uy * 110;
      const pvZ = hasPlaneVel ? aircraftVel.z : uz * 110;

      // (a) 机首前方高密浅灰包覆团（瞬间涌出炮口包住机鼻下巴，带炮口暖光内透散射）
      const nosePuff = this._allocSmoke();
      nosePuff.active = true;
      nosePuff.age = 0;
      nosePuff.life = 1.25 + h1 * 0.35;
      nosePuff.layerKind = 4;
      nosePuff.x = worldPos.x + ux * (0.75 + h2 * 0.55) + rx * (h1 - 0.5) * 0.55;
      nosePuff.y = worldPos.y - 0.22 + (h3 - 0.5) * 0.35;
      nosePuff.z = worldPos.z + uz * (0.75 + h2 * 0.55) + rz * (h1 - 0.5) * 0.55;
      nosePuff.vx = pvX * 0.84 + ux * 6.5 + rx * (h2 - 0.5) * 4.2;
      nosePuff.vy = pvY * 0.80 - 2.0;
      nosePuff.vz = pvZ * 0.84 + uz * 6.5 + rz * (h2 - 0.5) * 4.2;
      nosePuff.windX = 1.1; nosePuff.windZ = 0.45;
      nosePuff.gravity = 0.0;
      nosePuff.drag = 3.0;
      nosePuff.buoyancy = 0.28;
      nosePuff.expGrowK = 3.8;
      nosePuff.baseScale = 2.15 + h1 * 0.45;
      nosePuff.endScale = 5.8 + h2 * 1.4;
      nosePuff.rot = h3 * Math.PI * 2;
      nosePuff.rotSpeed = (h1 - 0.5) * 2.2;
      nosePuff.seed = (simNowSec * 17.3 + h2 * 5.7) % 10;
      nosePuff.clusterHeightNorm = 0.78;
      nosePuff.heatGain = 0.95;

      // (b) 贴机腹向后裹挟滚涌的饱满浅灰体积云（离开机尾后受风拉长扩散形成连贯烟幕）
      const bellyCount = this.lodTier === 'near' ? 3 : 1;
      for (let i = 0; i < bellyCount; i += 1) {
        const hi1 = hash11(simNowSec * 41.1 + i * 11.7);
        const hi2 = hash11(simNowSec * 53.9 + i * 19.3);
        const backDist = 1.4 + i * 3.1 + hi1 * 0.9;
        const latOff = (hi2 - 0.5) * (1.4 + i * 0.75);
        const smoke = this._allocSmoke();
        smoke.active = true;
        smoke.age = 0;
        smoke.life = 2.0 + i * 0.36 + hi1 * 0.35;
        smoke.layerKind = 4;
        smoke.x = worldPos.x - ux * backDist + rx * latOff;
        smoke.y = worldPos.y - 0.60 - i * 0.22 + (hi1 - 0.5) * 0.35;
        smoke.z = worldPos.z - uz * backDist + rz * latOff;
        const carryFactor = 0.66 - i * 0.14;
        smoke.vx = pvX * carryFactor - ux * (11 + i * 4.5) + rx * latOff * 2.4;
        smoke.vy = pvY * carryFactor - 1.5 - i * 0.35;
        smoke.vz = pvZ * carryFactor - uz * (11 + i * 4.5) + rz * latOff * 2.4;
        smoke.windX = 1.35; smoke.windZ = 0.55;
        smoke.gravity = 0.0;
        smoke.drag = 2.2;
        smoke.buoyancy = 0.35;
        smoke.expGrowK = 3.0;
        smoke.baseScale = 2.45 + i * 0.58;
        smoke.endScale = 7.8 + i * 1.65;
        smoke.rot = hi2 * Math.PI * 2;
        smoke.rotSpeed = (hi1 - 0.5) * 1.6;
        smoke.seed = (simNowSec * 13.7 + i * 1.37) % 10;
        smoke.clusterHeightNorm = 0.70 + hi1 * 0.24;
        smoke.heatGain = i === 0 ? 0.52 : 0.0;
      }
    }
  }

  /**
   * 导弹/火箭发射瞬间挂架点火火焰 + 浓郁灰白发射烟团。
   */
  spawnMissileLaunchSmoke(worldPos, dir, simNowSec = 0, aircraftVel = null) {
    if (!this._isInFrustum(worldPos, 90)) return;
    const dx = dir?.x || 0;
    const dy = dir?.y || 0;
    const dz = dir?.z || 1;
    const dLen = Math.max(0.01, Math.hypot(dx, dy, dz));
    const ux = dx / dLen;
    const uz = dz / dLen;

    const flash = this._allocBlast();
    flash.active = true; flash.age = 0; flash.life = 0.085;
    flash.layerKind = 0;
    flash.x = worldPos.x; flash.y = worldPos.y; flash.z = worldPos.z;
    flash.baseScale = 1.35; flash.endScale = 3.5; flash.intensity = 1.15;
    flash.seed = simNowSec; flash.rotY = simNowSec * 9.0;

    const pvX = aircraftVel ? aircraftVel.x * 0.35 : 0;
    const pvZ = aircraftVel ? aircraftVel.z * 0.35 : 0;
    const count = this.lodTier === 'near' ? 8 : 4;
    for (let i = 0; i < count; i += 1) {
      const s = this._allocSmoke();
      s.active = true;
      s.age = 0;
      s.life = 1.35 + i * 0.14;
      s.layerKind = 3;
      s.x = worldPos.x - ux * i * 1.15;
      s.y = worldPos.y - 0.1 * i;
      s.z = worldPos.z - uz * i * 1.15;
      s.vx = pvX - ux * (11 + i * 1.2) + Math.sin(i * 2.1) * 1.4;
      s.vy = 0.9 + (i % 3) * 0.4;
      s.vz = pvZ - uz * (11 + i * 1.2) + Math.cos(i * 2.1) * 1.4;
      s.gravity = -0.15;
      s.drag = 2.0;
      s.baseScale = 1.75;
      s.endScale = 6.6;
      s.rot = i * 1.3;
      s.rotSpeed = 0.85;
      s.seed = (i * 0.27 + simNowSec) % 10;
      s.clusterHeightNorm = 0.72;
      s.heatGain = 0;
    }
  }

  /**
   * 路线 A 核心：AGM-65 重型导弹密集 8×8 体积云翻滚尾烟团（与爆炸黑烟共享同一体积云受光风格，天然无缝融入爆心）。
   */
  spawnMissileTrailSmoke(worldPos, simNowSec = 0, options = null) {
    if (this.lodTier === 'far' || !this._isInFrustum(worldPos, 65)) return;
    const isNear = this.lodTier === 'near';
    const seed = (worldPos.x * 0.19 + worldPos.z * 0.23 + simNowSec * 11.7) % 10;
    const h1 = hash11(seed * 7.3 + 1.1);
    const h2 = hash11(seed * 13.7 + 4.3);
    const s = this._allocSmoke();
    s.active = true;
    s.age = 0;
    s.life = (isNear ? 3.8 : 2.6) + h1 * 1.15;
    s.layerKind = 3;
    s.x = worldPos.x + (h1 - 0.5) * 0.65;
    s.y = worldPos.y + (h2 - 0.5) * 0.45;
    s.z = worldPos.z + (h2 - 0.5) * 0.65;
    s.vx = (h1 - 0.5) * 1.1;
    s.vy = 0.62 + h2 * 0.45;
    s.vz = (h2 - 0.5) * 1.1;
    s.windX = 1.1;
    s.windZ = 0.42;
    s.gravity = 0.0;
    s.drag = 1.3;
    s.buoyancy = 0.32;
    s.expGrowK = 2.6;
    const scaleMul = Number.isFinite(options?.scale) ? options.scale : 1.0;
    s.baseScale = (1.95 + h1 * 0.55) * scaleMul;
    s.endScale = (6.0 + h2 * 2.4) * scaleMul;
    s.rot = h1 * Math.PI * 2;
    s.rotSpeed = (h2 - 0.5) * 0.95;
    s.seed = seed;
    s.clusterHeightNorm = 0.66 + h2 * 0.24;
    s.heatGain = options?.hotCore === false ? 0.0 : 0.75;
  }

  /**
   * 路线 A 分工：
   * - AGM-65 (`missile_smoke`): 纤细轻柔衔接雾芯（配合密集 8×8 体积云团串联，不抢戏不穿插黑烟）；
   * - Hydra 70 (`rocket_smoke`): 连续共享顶点 Mk 66 细锐轻烟齐射流（64 段覆盖 ~230m 完整弹道）。
   */
  emitMissileTrailRibbon(key, worldPos, kind = 'missile_smoke', simNowSec = 0) {
    if (this.lodTier === 'far') return;
    const isAgm = kind === 'missile_smoke';
    const r = this._allocRibbon(
      key,
      kind,
      isAgm ? 0.24 : 0.22,
      isAgm ? 2.8 : 3.4,
      isAgm ? 1.35 : 2.15,
    );
    r.alpha = isAgm ? 0.34 : 0.58;
    this._pushRibbonPoint(r, worldPos.x, worldPos.y, worldPos.z, simNowSec, isAgm ? 3.4 : 3.6);
  }

  /**
   * 为失控下坠战机发射连续炭黑坠落烟带（复用池 5，彻底消除高速下坠时的离散黑珠感）。
   */
  emitCrashSmokeRibbon(key, worldPos, simNowSec = 0) {
    if (this.lodTier === 'far') return;
    const r = this._allocRibbon(key, 'crash_smoke', 1.45, 4.2, 6.2);
    r.alpha = 0.88;
    this._pushRibbonPoint(r, worldPos.x, worldPos.y, worldPos.z, simNowSec, 1.6);
  }

  /**
   * 受损冒烟 / 坠毁残骸长时斜风黑烟柱与底层跳跃明火（向后兼容 2 参数老调用，使用仿真时钟确定性种子）。
   */
  spawnDamageSmokePuff(worldPos, withSpark = true, options = null) {
    if (!this._isInFrustum(worldPos, 24)) return;
    const isCrashWreck = Boolean(options?.isCrashWreck);
    const scale = Number.isFinite(options?.scale) ? options.scale : (isCrashWreck ? 1.45 : 1.0);
    const windX = Number.isFinite(options?.windVec?.x) ? options.windVec.x : (isCrashWreck ? 3.2 : 1.4);
    const windZ = Number.isFinite(options?.windVec?.z) ? options.windVec.z : (isCrashWreck ? 1.3 : 0.6);
    const flameIntensity = Number.isFinite(options?.flameIntensity) ? options.flameIntensity : (isCrashWreck ? 1.25 : (withSpark ? 0.85 : 0.0));
    const simClock = Number.isFinite(options?.simNowSec) ? options.simNowSec : this._simClockSec;
    const tSeed = (worldPos.x * 0.31 + worldPos.z * 0.17 + simClock * 7.3) % 100;
    const h1 = hash11(tSeed * 3.7);
    const h2 = hash11(tSeed * 9.3 + 1.7);

    // 主斜风浓炭黑烟柱团（提高垂向浮力与重叠尺度，连成厚重挺拔斜风黑烟柱，彻底消灭等距串珠黑球）
    const puffCount = isCrashWreck ? 2 : 1;
    for (let k = 0; k < puffCount; k += 1) {
      const s = this._allocSmoke();
      const hk1 = k === 0 ? h1 : hash11(tSeed * 13.1 + 5.3);
      const hk2 = k === 0 ? h2 : hash11(tSeed * 17.9 + 2.9);
      s.active = true;
      s.age = 0;
      s.life = isCrashWreck ? (4.4 + hk1 * 1.8) : (2.3 + hk1 * 0.7);
      s.layerKind = 2;
      s.x = worldPos.x + (isCrashWreck ? windX * 0.35 : 0) + (hk1 - 0.5) * (isCrashWreck ? 2.4 : 1.1) * scale;
      s.y = worldPos.y + (isCrashWreck ? (2.1 + k * 1.1) : 0.3);
      s.z = worldPos.z + (isCrashWreck ? windZ * 0.35 : 0) + (hk2 - 0.5) * (isCrashWreck ? 2.4 : 1.1) * scale;
      s.vx = windX * 0.25 + (hk1 - 0.5) * 1.6;
      s.vy = (isCrashWreck ? 7.8 : 3.4) + hk2 * 2.4;
      s.vz = windZ * 0.25 + (hk2 - 0.5) * 1.6;
      s.windX = windX;
      s.windZ = windZ;
      s.gravity = 0.0;
      s.drag = 0.72;
      s.buoyancy = isCrashWreck ? 2.6 : 0.75;
      s.expGrowK = 2.6;
      s.baseScale = (isCrashWreck ? (3.6 + k * 0.7) : 2.15) * scale;
      s.endScale = (isCrashWreck ? (13.8 + hk2 * 3.2) : 7.2) * scale;
      s.rot = hk1 * Math.PI * 2;
      s.rotSpeed = (hk2 - 0.5) * 0.7;
      s.seed = fract(tSeed + k * 0.37) * 10.0;
      s.clusterHeightNorm = isCrashWreck ? (0.45 + hk2 * 0.45) : 0.58;
      s.heatGain = isCrashWreck ? 0.65 : (withSpark ? 0.40 : 0.0);
    }

    // 残骸/受损底层跳跃明火瓣（layerKind=6，位于迎风根部且排序优先于根部新黑烟防止被盖死）
    if (flameIntensity > 0.05 && this.lodTier !== 'far') {
      const f = this._allocSmoke();
      f.active = true;
      f.age = 0;
      f.life = isCrashWreck ? (0.68 + h2 * 0.36) : 0.34;
      f.layerKind = 6;
      f.x = worldPos.x - (isCrashWreck ? windX * 0.16 : 0) + (h2 - 0.5) * 1.5 * scale;
      f.y = worldPos.y + (isCrashWreck ? 1.35 : 0.75);
      f.z = worldPos.z - (isCrashWreck ? windZ * 0.16 : 0) + (h1 - 0.5) * 1.5 * scale;
      f.vx = windX * 0.16 + (h2 - 0.5) * 0.7;
      f.vy = (isCrashWreck ? 4.2 : 1.6) + h1 * 1.5;
      f.vz = windZ * 0.16 + (h1 - 0.5) * 0.7;
      f.windX = windX * 0.25;
      f.windZ = windZ * 0.25;
      f.gravity = 0;
      f.drag = 1.15;
      f.baseScale = (isCrashWreck ? 3.0 : 1.25) * scale;
      f.endScale = (isCrashWreck ? 5.6 : 2.1) * scale;
      f.rot = h2 * Math.PI * 2;
      f.rotSpeed = (h1 - 0.5) * 1.8;
      f.seed = fract(tSeed * 1.7) * 10.0;
      f.clusterHeightNorm = 0.22;
      f.heatGain = Math.max(flameIntensity, isCrashWreck ? 1.45 : flameIntensity);
    }

    // 残骸/受击偶发极细上升火星（池 6 kind=6）
    if ((withSpark || isCrashWreck) && this.lodTier === 'near' && h1 > 0.45) {
      const sp = this._allocCone();
      sp.active = true;
      sp.kind = 6;
      sp.age = 0;
      sp.life = 0.45 + h2 * 0.35;
      sp.x = worldPos.x + (h1 - 0.5) * 0.8;
      sp.y = worldPos.y + 0.5;
      sp.z = worldPos.z + (h2 - 0.5) * 0.8;
      sp.vx = windX * 0.55 + (h1 - 0.5) * 4.2;
      sp.vy = 6.5 + h2 * 5.0;
      sp.vz = windZ * 0.55 + (h2 - 0.5) * 4.2;
      sp.gravity = 12.0;
      sp.drag = 0.65;
      sp.dx = -sp.vx; sp.dy = -sp.vy; sp.dz = -sp.vz;
      sp.sx = 0.06; sp.sy = 0.06; sp.sz = 1.4;
      sp.seed = fract(tSeed * 2.3) * 10.0;
      this.sparkStreakEmittedCount += 1;
    }
  }

  /**
   * 发射一颗高亮暖白热诱弹及配套细烟弧（4–8 颗连射，零运行期数组重建）。
   */
  spawnFlareParticle(origin, vel, simNowSec = 0) {
    if (!this._isInFrustum(origin, 16)) return;
    this._flareSeq += 1;
    const key = `flare_${Math.floor(simNowSec * 1000)}_${this._flareSeq}`;
    const ribbon = this.lodTier !== 'far' ? this._allocRibbon(key, 'flare', 0.52, 2.2, 1.35) : null;
    if (ribbon) this._pushRibbonPoint(ribbon, origin.x, origin.y, origin.z, simNowSec);
    const f = this._allocFlare();
    f.active = true;
    f.x = origin.x;
    f.y = origin.y;
    f.z = origin.z;
    f.vx = vel.x;
    f.vy = vel.y;
    f.vz = vel.z;
    f.age = 0;
    f.life = 2.1;
    f.ribbonKey = key;
  }

  /**
   * 坠毁初段弹射座椅白烟短弧（复用既有 flare 与 contrail ribbon 槽位，严格 0 新增 DrawCall）。
   */
  spawnEjectionArc(origin, vel, simNowSec = 0) {
    if (!origin || !this._isInFrustum(origin, 24)) return false;
    this._flareSeq += 1;
    const key = `eject_${Math.floor(simNowSec * 1000)}_${this._flareSeq}`;
    const ribbon = this.lodTier !== 'far' ? this._allocRibbon(key, 'contrail', 0.34, 1.45, 0.95) : null;
    if (ribbon) {
      ribbon.alpha = 0.88;
      this._pushRibbonPoint(ribbon, origin.x, origin.y, origin.z, simNowSec);
    }
    const f = this._allocFlare();
    f.active = true;
    f.x = origin.x;
    f.y = origin.y;
    f.z = origin.z;
    f.vx = Number(vel?.x) || 0;
    f.vy = Number(vel?.y) || 24;
    f.vz = Number(vel?.z) || 0;
    f.age = 0;
    f.life = 1.45;
    f.ribbonKey = key;
    return true;
  }

  /**
   * 坠毁撞地瞬间沿水平动量方向落下 2 段递减焦黑拖痕弹坑与碎泥余烟（与主爆弹坑组成 3 段滑行拖痕，0 新增 DC）。
   */
  spawnCrashSkidTrail(origin, horizDir = null, simNowSec = 0) {
    if (!origin) return false;
    let hx = Number(horizDir?.x) || 0;
    let hz = Number(horizDir?.z) || 0;
    const hLen = Math.hypot(hx, hz);
    if (hLen > 1e-4) {
      hx /= hLen;
      hz /= hLen;
    } else {
      hx = 1;
      hz = 0;
    }
    const offsets = [14, 26];
    const radii = [4.6, 3.2];
    for (let i = 0; i < offsets.length; i += 1) {
      const px = origin.x + hx * offsets[i];
      const pz = origin.z + hz * offsets[i];
      const py = (this.terrainSupport?.heightAt?.(px, pz) ?? origin.y) + 0.12;
      const pt = { x: px, y: py, z: pz };
      this._addCrater(pt, radii[i]);
      this.spawnDamageSmokePuff(pt, false, {
        scale: i === 0 ? 0.78 : 0.60,
        flameIntensity: i === 0 ? 0.42 : 0.20,
        simNowSec,
      });
    }
    return true;
  }

  /**
   * 翼尖涡流发射（仅 `|bankDeg| > 26°` 或拉起高 G 时调用，远景 far 自动关闭）。
   */
  emitWingtipVortex(unitId, leftPos, rightPos, simNowSec, fadeAlpha = 1.0) {
    this.wingtipVortexActive = true;
    this.wingtipFadeAlpha = clamp(fadeAlpha, 0, 1);
    if (this.lodTier === 'far') return;
    const rL = this._allocRibbon(`${unitId}_wt_L`, 'vortex', 0.38, 1.05, 0.72);
    const rR = this._allocRibbon(`${unitId}_wt_R`, 'vortex', 0.38, 1.05, 0.72);
    rL.alpha = this.wingtipFadeAlpha;
    rR.alpha = this.wingtipFadeAlpha;
    this._pushRibbonPoint(rL, leftPos.x, leftPos.y, leftPos.z, simNowSec);
    this._pushRibbonPoint(rR, rightPos.x, rightPos.y, rightPos.z, simNowSec);
  }

  fadeWingtipVortex(unitId, fadeAlpha) {
    this.wingtipVortexActive = false;
    this.wingtipFadeAlpha = clamp(fadeAlpha, 0, 1);
    const rL = this._ribbonByKey.get(`${unitId}_wt_L`);
    const rR = this._ribbonByKey.get(`${unitId}_wt_R`);
    if (rL) {
      rL.alpha = this.wingtipFadeAlpha;
      if (this.wingtipFadeAlpha <= 0.005) {
        rL.active = false;
        rL.count = 0;
      }
    }
    if (rR) {
      rR.alpha = this.wingtipFadeAlpha;
      if (this.wingtipFadeAlpha <= 0.005) {
        rR.active = false;
        rR.count = 0;
      }
    }
  }

  /**
   * 底座预留高空凝结尾迹（A-10C `capabilities.contrail === false` 永不调用）。
   */
  emitContrailPoint(key, worldPos, simNowSec) {
    this.contrailEmittedCount += 1;
    if (this.lodTier === 'far') return;
    const r = this._allocRibbon(key, 'contrail', 0.68, 2.8, 1.65);
    r.alpha = 0.52;
    this._pushRibbonPoint(r, worldPos.x, worldPos.y, worldPos.z, simNowSec);
  }

  /**
   * 底座预留超音速加力尾焰与音爆蒸汽圆锥云（A-10C 永不调用）。
   */
  emitAfterburnerCone(worldPos, yaw, pitch) {
    const c = this._allocCone();
    c.active = true; c.kind = 0; c.age = 0; c.life = 0.06;
    c.x = worldPos.x; c.y = worldPos.y; c.z = worldPos.z;
    const cosP = Math.cos(pitch);
    c.dx = -Math.sin(yaw) * cosP; c.dy = -Math.sin(pitch); c.dz = -Math.cos(yaw) * cosP;
    c.sx = 1.1; c.sy = 1.1; c.sz = 3.2; c.seed = 0;
  }

  spawnSonicBoomCone(worldPos, yaw, pitch) {
    this.sonicBoomEmittedCount += 1;
    const c = this._allocCone();
    c.active = true; c.kind = 1; c.age = 0; c.life = 0.45;
    c.x = worldPos.x; c.y = worldPos.y; c.z = worldPos.z;
    const cosP = Math.cos(pitch);
    c.dx = Math.sin(yaw) * cosP; c.dy = Math.sin(pitch); c.dz = Math.cos(yaw) * cosP;
    c.sx = 6.5; c.sy = 6.5; c.sz = 12.0; c.seed = 0;
  }

  /**
   * 将当前帧在飞弹药、HDR 曳光光丝与导弹固体火箭尾焰分别压入池 1 和池 6（热路径零对象分配）。
   */
  pushActiveOrdnanceInstance(proj) {
    const vx = proj.vx || 0;
    const vy = proj.vy || 0;
    const vz = proj.vz || 1;
    const vLen = Math.max(0.01, Math.hypot(vx, vy, vz));
    const ux = vx / vLen;
    const uy = vy / vLen;
    const uz = vz / vLen;

    // 30mm GAU-8 弹链：按 4:1 Combat Mix 区分高亮 HDR 红/绿曳光与低调 Fumer 暗金属实弹+气痕
    if (proj.kind === 'tracer30mm') {
      const traveled = Number.isFinite(proj.traveledDist) ? proj.traveledDist : 34;
      const rIdx = Math.floor(Number(proj.roundIndex) || 0);
      const mode = this.tracerColorMode;
      const isTracerRound = mode !== 'none' && proj.isTracer !== false;

      if (!isTracerRound) {
        this.fumerEmittedCount += 1;
        if (this._ordnanceFrameCount < MAX_ORDNANCE) {
          const idx = this._ordnanceFrameCount;
          this._ordnanceFrameCount += 1;
          this._pos.set(proj.x, proj.y, proj.z);
          this._dir.set(ux, uy, uz);
          this._quat.setFromUnitVectors(this._forwardAxis, this._dir);
          this._scale.set(0.18, 0.18, 1.95);
          this._color.setHex(0x3a3630);
          this._mat4.compose(this._pos, this._quat, this._scale);
          this.ordnancePool.setMatrixAt(idx, this._mat4);
          this.ordnancePool.setColorAt(idx, this._color);
        }
        const fumerLen = clamp(traveled * 0.52, 2.0, 16.0);
        this._pushFrameCone(
          4,
          proj.x, proj.y, proj.z,
          -ux, -uy, -uz,
          0.22, 0.22, fumerLen,
          0,
          60.0 + (rIdx % 7) * 0.1,
        );
        return;
      }

      const traceLen = clamp(traveled * 0.85, 2.5, 30.0);
      let isGreen = false;
      if (mode === 'green') {
        isGreen = true;
      } else if (mode === 'red') {
        isGreen = false;
      } else if (mode === 'mixed') {
        isGreen = ((rIdx + (proj.isCompanion ? 1 : 0)) % 2) === 1;
      } else {
        const own = proj.tracerColor;
        isGreen = own === 'green'
          ? true
          : own === 'mixed'
            ? (((rIdx + (proj.isCompanion ? 1 : 0)) % 2) === 1)
            : false;
      }
      if (isGreen) this.tracerEmittedGreen += 1;
      else this.tracerEmittedRed += 1;

      this._pushFrameCone(
        4,
        proj.x, proj.y, proj.z,
        -ux, -uy, -uz,
        0.42, 0.42, traceLen,
        0,
        isGreen ? -(1.0 + (rIdx % 7) * 0.1) : (1.0 + (rIdx % 7) * 0.1),
      );
      return;
    }

    // 实体低模弹体压入池 1 ordnancePool
    if (this._ordnanceFrameCount < MAX_ORDNANCE) {
      const idx = this._ordnanceFrameCount;
      this._ordnanceFrameCount += 1;

      this._pos.set(proj.x, proj.y, proj.z);
      this._dir.set(ux, uy, uz);
      this._quat.setFromUnitVectors(this._forwardAxis, this._dir);

      if (proj.kind === 'agm65') {
        this._scale.set(0.95, 0.95, 3.4);
        this._color.setHex(0xe5e8eb);
      } else if (proj.kind === 'gbu31') {
        this._scale.set(1.25, 1.25, 3.8);
        this._color.setHex(0x4b5742);
      } else {
        this._scale.set(0.68, 0.68, 2.3);
        this._color.setHex(0xb8c0c4);
      }
      this._mat4.compose(this._pos, this._quat, this._scale);
      this.ordnancePool.setMatrixAt(idx, this._mat4);
      this.ordnancePool.setColorAt(idx, this._color);
    }

    // AGM-65 / Hydra 70 发动机燃烧期内在弹尾压入细长马赫激波固体火箭尾焰（池 6 kind=2，按弹种区分粗细长度且燃尽平滑收细）
    if (proj.kind === 'agm65' || proj.kind === 'rocket') {
      const isAgm = proj.kind === 'agm65';
      const burnDur = isAgm ? 3.5 : 1.55;
      const pAge = proj.age || 0;
      if (pAge <= burnDur) {
        const isBoosting = pAge <= (proj.boostAccelSec || (isAgm ? 0.9 : 0.45));
        const burnoutTail = pAge > burnDur * 0.82 ? clamp(1.0 - (pAge - burnDur * 0.82) / (burnDur * 0.18), 0.15, 1.0) : 1.0;
        const tailOffset = isAgm ? 1.72 : 1.15;
        const radius = (isAgm ? (isBoosting ? 0.38 : 0.28) : (isBoosting ? 0.22 : 0.17)) * burnoutTail;
        const len = (isAgm ? (isBoosting ? 6.2 : 4.4) : (isBoosting ? 3.4 : 2.3)) * (0.45 + 0.55 * burnoutTail);
        const seedBase = (isAgm ? 0.0 : 20.0) + ((pAge * 29.0) % 9.5);
        this._pushFrameCone(
          2,
          proj.x - ux * tailOffset,
          proj.y - uy * tailOffset,
          proj.z - uz * tailOffset,
          -ux, -uy, -uz,
          radius, radius, len,
          0,
          seedBase,
        );
      }
    }
  }

  /**
   * 每帧按 simDt 推进全部 6 个 GPU 实例化池与热诱弹物理（零堆分配、远→近+层桶深度排序）。
   */
  update(simDt, simNowSec = 0) {
    const dt = clamp(Number(simDt) || 0, 0, 0.12);
    this._simClockSec = Number.isFinite(simNowSec) && simNowSec > 0 ? simNowSec : (this._simClockSec + dt);
    this.activeExplosionLayers.clear();
    const lookX = this._camLookDir.x;
    const lookY = this._camLookDir.y;
    const lookZ = this._camLookDir.z;

    // 1. 更新定长预分配热诱弹槽并压入 ordnancePool
    let aliveFlareCount = 0;
    for (let i = 0; i < MAX_FLARES; i += 1) {
      const f = this._flareSlots[i];
      if (!f.active) continue;
      f.age += dt;
      if (f.age >= f.life) {
        f.active = false;
        const rb = this._ribbonByKey.get(f.ribbonKey);
        if (rb) rb.alpha = 0;
        continue;
      }
      f.vy -= 11.5 * dt;
      const fDamp = Math.exp(-1.1 * dt);
      f.vx *= fDamp;
      f.vz *= fDamp;
      f.x += f.vx * dt;
      f.y += f.vy * dt;
      f.z += f.vz * dt;
      const rb = this._ribbonByKey.get(f.ribbonKey);
      if (rb && rb.active) {
        rb.alpha = clamp(1.0 - f.age / f.life, 0, 1);
        this._pushRibbonPoint(rb, f.x, f.y, f.z, simNowSec);
      }
      if (this._ordnanceFrameCount < MAX_ORDNANCE) {
        const idx = this._ordnanceFrameCount;
        this._ordnanceFrameCount += 1;
        this._pos.set(f.x, f.y, f.z);
        this._quat.identity();
        const s = 1.65 * (1.0 - 0.35 * (f.age / f.life));
        this._scale.set(s, s, s);
        this._mat4.compose(this._pos, this._quat, this._scale);
        this.ordnancePool.setMatrixAt(idx, this._mat4);
        this._color.setRGB(3.8, 2.95, 1.55);
        this.ordnancePool.setColorAt(idx, this._color);
      }
      aliveFlareCount += 1;
    }
    this.flareActiveCount = aliveFlareCount;

    this.ordnancePool.count = this._ordnanceFrameCount;
    this.ordnancePool.visible = this._ordnanceFrameCount > 0;
    if (this._ordnanceFrameCount > 0) {
      this.ordnancePool.instanceMatrix.needsUpdate = true;
      if (this.ordnancePool.instanceColor) this.ordnancePool.instanceColor.needsUpdate = true;
    }

    // 2. 更新池 2（按层桶 + 相机深度远→近零 GC 排序：先非发光尘浪，再贴地假光晕，最后爆闪核防压暗）
    let blastCount = 0;
    for (let i = 0; i < MAX_BLAST_SHELLS; i += 1) {
      const b = this._blastSlots[i];
      if (!b.active) continue;
      if (b.delay > 0) {
        b.delay -= dt;
        continue;
      }
      b.age += dt;
      if (b.age >= b.life) {
        b.active = false;
        continue;
      }
      const depthKey = -(b.x * lookX + b.y * lookY + b.z * lookZ);
      const layerBias = b.layerKind === 0 ? 50000.0 : (b.layerKind === 3 ? 25000.0 : 0.0);
      this._blastOrder[blastCount] = i;
      this._blastKeys[blastCount] = depthKey + layerBias;
      blastCount += 1;
    }
    this._sortIndicesAsc(this._blastOrder, this._blastKeys, blastCount);
    const blastMetaArr = this._blastAttrMeta.array;
    for (let out = 0; out < blastCount; out += 1) {
      const b = this._blastSlots[this._blastOrder[out]];
      const norm = clamp(b.age / b.life, 0, 1);
      const easeOut = b.layerKind === 4 ? easeOutExpo(norm, 5.2) : (1 - Math.pow(1 - norm, 2.4));
      const sc = b.baseScale + (b.endScale - b.baseScale) * easeOut;
      this._pos.set(b.x, b.y, b.z);
      this._quat.identity();
      this._scale.set(1, 1, 1);
      this._mat4.compose(this._pos, this._quat, this._scale);
      this.blastShellPool.setMatrixAt(out, this._mat4);
      this._blastAttrKind.array[out] = b.layerKind;
      this._blastAttrAge.array[out] = norm;
      this._blastAttrInt.array[out] = b.intensity;
      this._blastAttrSeed.array[out] = b.seed;
      blastMetaArr[out * 2] = sc;
      blastMetaArr[out * 2 + 1] = b.rotY;
      this.activeExplosionLayers.add(b.layerKind === 0 || b.layerKind === 3 ? 'L1_flash_glow' : 'L2_shockwave_dust');
    }
    this.blastShellPool.count = blastCount;
    this.blastShellPool.visible = blastCount > 0;
    if (blastCount > 0) {
      this.blastShellPool.instanceMatrix.needsUpdate = true;
      this._blastAttrKind.needsUpdate = true;
      this._blastAttrAge.needsUpdate = true;
      this._blastAttrInt.needsUpdate = true;
      this._blastAttrSeed.needsUpdate = true;
      this._blastAttrMeta.needsUpdate = true;
    }

    // 3. 更新池 3（物理推进 + 零 GC 相机远→近与层桶排序，保证残骸明火与火核不被同处新黑烟盖死闪烁）
    let smokeCount = 0;
    for (let i = 0; i < MAX_SMOKE_CHUNKS; i += 1) {
      const s = this._smokeSlots[i];
      if (!s.active) continue;
      s.age += dt;
      if (s.age >= s.life) {
        s.active = false;
        continue;
      }
      let effDrag = s.drag;
      if (s.burstWindowSec > 0 && s.age > 0.065 && s.age < s.burstWindowSec) {
        effDrag = s.burstDrag;
      }
      const damp = Math.exp(-effDrag * dt);
      s.vx *= damp;
      s.vz *= damp;
      s.vy = s.vy * damp - s.gravity * dt + (s.age > 0.16 ? s.buoyancy * dt : 0);
      const windFactor = s.age > 0.20 ? 1.0 : (s.age / 0.20);
      s.x += (s.vx + s.windX * windFactor) * dt;
      s.y += s.vy * dt;
      s.z += (s.vz + s.windZ * windFactor) * dt;
      s.rot += s.rotSpeed * dt;

      if (s.layerKind === 1) {
        const gy = this.terrainSupport?.heightAt?.(s.x, s.z) ?? 0;
        if (s.y < gy + 0.15) {
          s.y = gy + 0.15;
          s.vx *= 0.28;
          s.vy = Math.abs(s.vy) * 0.16;
          s.vz *= 0.28;
          // 泥块落地后加速衰减消失，彻底消灭 30mm / 重爆地面残留孤立黑点串
          s.age = Math.max(s.age, s.life * 0.80);
        }
      }

      const depthKey = -(s.x * lookX + s.y * lookY + s.z * lookZ);
      // 残骸明火瓣(kind=6)和爆心火核(kind=0)赋予柔和层内偏置，避免被同位置及正上方烟柱根部遮蔽闪烁
      const kindBias = s.layerKind === 6 ? 24.0 : (s.layerKind === 0 ? 5.0 : 0.0);
      this._smokeOrder[smokeCount] = i;
      this._smokeKeys[smokeCount] = depthKey + kindBias;
      smokeCount += 1;
    }
    this._sortIndicesAsc(this._smokeOrder, this._smokeKeys, smokeCount);
    const smokeMetaArr = this._smokeAttrMeta.array;
    for (let out = 0; out < smokeCount; out += 1) {
      const s = this._smokeSlots[this._smokeOrder[out]];
      const norm = clamp(s.age / s.life, 0, 1);
      const grow = s.expGrowK > 0 ? easeOutExpo(norm, s.expGrowK) : Math.pow(norm, 0.72);
      const sc = s.baseScale + (s.endScale - s.baseScale) * grow;
      this._pos.set(s.x, s.y, s.z);
      this._quat.identity();
      this._scale.set(1, 1, 1);
      this._mat4.compose(this._pos, this._quat, this._scale);
      this.smokeChunkPool.setMatrixAt(out, this._mat4);
      this._smokeAttrKind.array[out] = s.layerKind;
      this._smokeAttrAge.array[out] = norm;
      this._smokeAttrSeed.array[out] = s.seed;
      const mOff = out * 4;
      smokeMetaArr[mOff] = s.rot;
      smokeMetaArr[mOff + 1] = sc;
      smokeMetaArr[mOff + 2] = s.clusterHeightNorm;
      smokeMetaArr[mOff + 3] = s.heatGain;

      if (s.layerKind === 0 || s.layerKind === 1 || s.layerKind === 6) {
        this.activeExplosionLayers.add('L3_firecore_debris');
      } else if (s.layerKind === 2 || s.layerKind === 5) {
        this.activeExplosionLayers.add('L4_smoke_plume');
      }
    }
    this.smokeChunkPool.count = smokeCount;
    this.smokeChunkPool.visible = smokeCount > 0;
    if (smokeCount > 0) {
      this.smokeChunkPool.instanceMatrix.needsUpdate = true;
      this._smokeAttrKind.needsUpdate = true;
      this._smokeAttrAge.needsUpdate = true;
      this._smokeAttrSeed.needsUpdate = true;
      this._smokeAttrMeta.needsUpdate = true;
    }

    // 4. 更新池 4：FBM 非规则弹坑与余烬时间衰减
    this._syncCraterPool(dt);
    if (this._activeCraterCount > 0) this.activeExplosionLayers.add('L5_crater');

    // 5. 更新池 5：共享 Billboard 软边条带网格（零每帧闭包 + 翼尖涡流空闲自动衰减回收 + 坠落炭黑烟带）
    this._ribbonVertCursor = 0;
    let activeRibbonCount = 0;
    let anyVortexAlive = false;
    for (let rIdx = 0; rIdx < MAX_RIBBONS; rIdx += 1) {
      const r = this._ribbons[rIdx];
      if (!r.active) continue;
      r.age += dt;
      const isMissileSmoke = r.kind === 'missile_smoke' || r.kind === 'rocket_smoke';
      const isMuzzleSmoke = r.kind === 'muzzle_smoke';
      const isCrashSmoke = r.kind === 'crash_smoke';
      const isFlareSmoke = r.kind === 'flare';
      const idleSec = Math.max(0, simNowSec - r.lastEmitSec);
      const ribbonMode = isCrashSmoke ? 2.0 : ((isMissileSmoke || isMuzzleSmoke || isFlareSmoke) ? 1.0 : 0.0);
      const isDetached = (isMissileSmoke || isMuzzleSmoke) && idleSec > 0.045;
      for (let j = 0; j < r.count; j += 1) {
        r.pointAges[j] += dt;
        if (isMissileSmoke || isMuzzleSmoke || isCrashSmoke) {
          const driftStart = isDetached ? 0 : 1;
          if (j >= driftStart) {
            // 低频连续世界空间风场漂移（严禁按离散下标 j 高频震荡，彻底根除锯齿折线与条状分裂）
            const px = r.points[j * 3];
            const pz = r.points[j * 3 + 2];
            const pAge = r.pointAges[j];
            const waveX = Math.sin(px * 0.028 + pz * 0.019 + pAge * 0.75);
            const waveZ = Math.cos(pz * 0.026 - px * 0.017 + pAge * 0.68);
            const turbMul = isCrashSmoke ? 0.55 : (isMuzzleSmoke ? 0.32 : 0.26);
            r.points[j * 3 + 1] += (isCrashSmoke ? 1.45 : (isMuzzleSmoke ? 0.35 : 0.46)) * dt;
            r.points[j * 3] += ((isCrashSmoke ? 1.25 : 0.45) + waveX * turbMul) * dt;
            r.points[j * 3 + 2] += ((isCrashSmoke ? 0.50 : 0.18) + waveZ * turbMul) * dt;
          }
        }
      }
      if (r.kind === 'vortex') {
        // 翼尖涡流超过 0.45s 未推点即平滑淡出，防止坠毁/退场后涡流 alpha=1 永久悬空（QA-02）
        if (idleSec > 0.45) {
          r.alpha = Math.max(0, r.alpha - dt * 1.45);
          if (r.alpha <= 0.01 || idleSec > 2.2) {
            r.active = false;
            r.count = 0;
            if (r.key) this._ribbonByKey.delete(r.key);
            continue;
          }
        }
        anyVortexAlive = true;
      } else if (idleSec > r.life) {
        r.active = false;
        r.count = 0;
        if (r.key) this._ribbonByKey.delete(r.key);
        continue;
      }
      if (r.count < 2 || r.alpha <= 0.01) continue;
      activeRibbonCount += 1;

      const baseR = isCrashSmoke ? 0.032 : (isMuzzleSmoke ? 0.62 : (r.kind === 'missile_smoke' ? 0.56 : (r.kind === 'rocket_smoke' ? 0.52 : (isFlareSmoke ? 0.68 : 0.50))));
      const baseG = isCrashSmoke ? 0.030 : (isMuzzleSmoke ? 0.62 : (r.kind === 'missile_smoke' ? 0.54 : (r.kind === 'rocket_smoke' ? 0.51 : (isFlareSmoke ? 0.62 : 0.52))));
      const baseB = isCrashSmoke ? 0.030 : (isMuzzleSmoke ? 0.60 : (r.kind === 'missile_smoke' ? 0.52 : (r.kind === 'rocket_smoke' ? 0.49 : (isFlareSmoke ? 0.52 : 0.56))));
      const endWidth = Number.isFinite(r.endWidth) ? r.endWidth : r.width * 1.8;
      // 停推点后随停发时间整体平滑加速扩散变淡，且首/尾两端均用 smoothstep 强制归零
      const detachedFade = isDetached ? clamp(1.0 - idleSec / Math.max(1.0, r.life * 0.72), 0.0, 1.0) : 1.0;
      const headGlow = (isMissileSmoke && !isDetached)
        ? (r.kind === 'missile_smoke' ? 0.85 : 0.60)
        : ((isMuzzleSmoke && !isDetached) ? 0.42 : 0.0);
      const expandPow = (isMissileSmoke || isMuzzleSmoke) ? 0.72 : 0.85;
      const denomCount = Math.max(1, r.count - 1);

      // Pass 1：为每个节点计算平滑平均切线与唯一一对左右顶点 (L_j, R_j)，数学上 100% 消除百叶窗裂片与重叠黑线
      let lastSx = 1;
      let lastSy = 0;
      let lastSz = 0;
      for (let j = 0; j < r.count; j += 1) {
        const jPrev = j > 0 ? j - 1 : 0;
        const jNext = j < r.count - 1 ? j + 1 : r.count - 1;
        const dx = r.points[jNext * 3] - r.points[jPrev * 3];
        const dy = r.points[jNext * 3 + 1] - r.points[jPrev * 3 + 1];
        const dz = r.points[jNext * 3 + 2] - r.points[jPrev * 3 + 2];
        const segLen = Math.hypot(dx, dy, dz);
        let sx = lastSx;
        let sy = lastSy;
        let sz = lastSz;
        if (segLen > 1e-3) {
          const tx = dx / segLen, ty = dy / segLen, tz = dz / segLen;
          sx = ty * this._viewVec.z - tz * this._viewVec.y;
          sy = tz * this._viewVec.x - tx * this._viewVec.z;
          sz = tx * this._viewVec.y - ty * this._viewVec.x;
          const sLen = Math.hypot(sx, sy, sz);
          if (sLen < 1e-3) {
            const invH = 1 / Math.max(0.001, Math.hypot(dx, dz));
            sx = -dz * invH; sy = 0; sz = dx * invH;
          } else {
            sx /= sLen; sy /= sLen; sz /= sLen;
          }
          // 保持相邻节点侧向量同侧连续，防止大拐角法线翻转扭结
          if (j > 0 && (sx * lastSx + sy * lastSy + sz * lastSz) < 0) {
            sx = -sx; sy = -sy; sz = -sz;
          }
          lastSx = sx; lastSy = sy; lastSz = sz;
        }

        const t = j / denomCount;
        const ageF = Math.max(0, 1 - r.pointAges[j] / Math.max(0.2, r.life));
        // 尾端强制 smoothstep 归零（最后 32% 平滑淡出至 0.0，100% 根治图 1 尾部一刀裁剪硬切口）
        const tailLin = clamp((1.0 - t) / 0.32, 0.0, 1.0);
        const tailSoft = tailLin * tailLin * (3.0 - 2.0 * tailLin);
        // 首端羽化：停推点后首端 0~22% 平滑归零，飞行中前 4% 柔和衔接喷口
        let headSoft = 1.0;
        if (isDetached) {
          const hs = clamp(t / 0.22, 0.0, 1.0);
          headSoft = hs * hs * (3.0 - 2.0 * hs);
        } else if (isMissileSmoke || isMuzzleSmoke) {
          headSoft = clamp(0.16 + t / 0.05, 0.16, 1.0);
        }
        const a = clamp(tailSoft * (1.0 - t * 0.45) * r.alpha * ageF * headSoft * detachedFade, 0, isCrashSmoke ? 0.88 : 0.84);
        const bloomHead = isDetached ? (1.0 + Math.min(1.5, idleSec * 1.1) * (1.0 - t)) : 1.0;
        const w = (r.width + (endWidth - r.width) * Math.pow(t, expandPow)) * bloomHead;

        const px = r.points[j * 3], py = r.points[j * 3 + 1], pz = r.points[j * 3 + 2];
        this._ribLX[j] = px + sx * w;
        this._ribLY[j] = py + sy * w;
        this._ribLZ[j] = pz + sz * w;
        this._ribRX[j] = px - sx * w;
        this._ribRY[j] = py - sy * w;
        this._ribRZ[j] = pz - sz * w;
        this._ribA[j] = a;
        this._ribT[j] = t;
      }

      // Pass 2：严格复用共享节点顶点拼接连续条带（过滤超长异常跳变段）
      for (let j = 0; j < r.count - 1; j += 1) {
        const dx = r.points[(j + 1) * 3] - r.points[j * 3];
        const dy = r.points[(j + 1) * 3 + 1] - r.points[j * 3 + 1];
        const dz = r.points[(j + 1) * 3 + 2] - r.points[j * 3 + 2];
        const segLen = Math.hypot(dx, dy, dz);
        if (segLen < 0.05 || segLen > 180) continue;

        const a0 = this._ribA[j], a1 = this._ribA[j + 1];
        if (a0 < 0.008 && a1 < 0.008) continue;
        const t0 = this._ribT[j], t1 = this._ribT[j + 1];

        this._pushRibbonVert(this._ribLX[j], this._ribLY[j], this._ribLZ[j], baseR, baseG, baseB, a0, 0.0, t0, ribbonMode, headGlow);
        this._pushRibbonVert(this._ribRX[j], this._ribRY[j], this._ribRZ[j], baseR, baseG, baseB, a0, 1.0, t0, ribbonMode, headGlow);
        this._pushRibbonVert(this._ribLX[j + 1], this._ribLY[j + 1], this._ribLZ[j + 1], baseR, baseG, baseB, a1, 0.0, t1, ribbonMode, headGlow);
        this._pushRibbonVert(this._ribLX[j + 1], this._ribLY[j + 1], this._ribLZ[j + 1], baseR, baseG, baseB, a1, 0.0, t1, ribbonMode, headGlow);
        this._pushRibbonVert(this._ribRX[j], this._ribRY[j], this._ribRZ[j], baseR, baseG, baseB, a0, 1.0, t0, ribbonMode, headGlow);
        this._pushRibbonVert(this._ribRX[j + 1], this._ribRY[j + 1], this._ribRZ[j + 1], baseR, baseG, baseB, a1, 1.0, t1, ribbonMode, headGlow);
      }
    }
    if (!anyVortexAlive) {
      this.wingtipVortexActive = false;
      this.wingtipFadeAlpha = 0;
    }
    this._activeRibbonCount = activeRibbonCount;
    this.sharedRibbonMesh.geometry.setDrawRange(0, this._ribbonVertCursor);
    this.sharedRibbonMesh.visible = this._ribbonVertCursor > 0;
    if (this._ribbonVertCursor > 0) {
      this._ribbonPosAttr.needsUpdate = true;
      this._ribbonColAttr.needsUpdate = true;
      this._ribbonAcrossAttr.needsUpdate = true;
      this._ribbonMetaAttr.needsUpdate = true;
    }

    // 6. 更新池 6（先非发光烟缕/音爆锥按远→近排序，后 Additive 曳光/尾焰/拉丝火花，杜绝遮挡反转）
    let activeConeSlots = 0;
    for (let i = 0; i < MAX_CONE_FX; i += 1) {
      const c = this._coneSlots[i];
      if (!c.active) continue;
      c.age += dt;
      if (c.age >= c.life) {
        c.active = false;
        continue;
      }
      const norm = clamp(c.age / c.life, 0, 1);
      let radMul = 1.0;
      let lenMul = 1.0;
      if (c.kind === 1) {
        radMul = 0.7 + norm * 1.1;
        lenMul = radMul;
      } else if (c.kind === 5) {
        radMul = 0.78 + 0.85 * norm;
        lenMul = 0.28 + 0.72 * easeOutExpo(norm, 5.8);
        this.activeExplosionLayers.add('L2_shockwave_dust');
      } else if (c.kind === 6) {
        const damp = Math.exp(-c.drag * dt);
        c.vx *= damp;
        c.vz *= damp;
        c.vy = c.vy * damp - c.gravity * dt;
        c.x += c.vx * dt;
        c.y += c.vy * dt;
        c.z += c.vz * dt;
        const gy = this.terrainSupport?.heightAt?.(c.x, c.z) ?? 0;
        if (c.y < gy + 0.12) {
          c.y = gy + 0.12;
          c.vx *= 0.48;
          c.vy = Math.abs(c.vy) * 0.35;
          c.vz *= 0.48;
        }
        const spd = Math.hypot(c.vx, c.vy, c.vz);
        if (spd > 0.2) {
          c.dx = -c.vx / spd;
          c.dy = -c.vy / spd;
          c.dz = -c.vz / spd;
        }
        lenMul = clamp(spd / 22.0, 0.28, 1.35) * (1.0 - norm * 0.45);
        this.activeExplosionLayers.add('L3_firecore_debris');
      }
      const depthKey = -(c.x * lookX + c.y * lookY + c.z * lookZ);
      const isAlphaLayer = c.kind === 1 || c.kind === 5;
      this._coneOrder[activeConeSlots] = i;
      this._coneKeys[activeConeSlots] = depthKey + (isAlphaLayer ? 0.0 : 50000.0);
      this._coneMetaRad[i] = radMul;
      this._coneMetaLen[i] = lenMul;
      this._coneMetaNorm[i] = norm;
      activeConeSlots += 1;
    }
    this._sortIndicesAsc(this._coneOrder, this._coneKeys, activeConeSlots);
    this._coneWriteCount = 0;
    for (let out = 0; out < activeConeSlots; out += 1) {
      const idx = this._coneOrder[out];
      const c = this._coneSlots[idx];
      this._writeConeMatrix(
        c.kind, c.x, c.y, c.z, c.dx, c.dy, c.dz,
        c.sx * this._coneMetaRad[idx], c.sy * this._coneMetaRad[idx], c.sz * this._coneMetaLen[idx],
        this._coneMetaNorm[idx], c.seed,
      );
    }

    for (let i = 0; i < this._frameConeCount; i += 1) {
      const fc = this._frameCones[i];
      this._writeConeMatrix(fc.kind, fc.x, fc.y, fc.z, fc.dx, fc.dy, fc.dz, fc.sx, fc.sy, fc.sz, fc.ageNorm, fc.seed);
    }
    this._frameConeCount = 0;

    this.coneFxPool.count = this._coneWriteCount;
    this.coneFxPool.visible = this._coneWriteCount > 0;
    if (this._coneWriteCount > 0) {
      this.coneFxPool.instanceMatrix.needsUpdate = true;
      this._coneAttrKind.needsUpdate = true;
      this._coneAttrAge.needsUpdate = true;
      this._coneAttrSeed.needsUpdate = true;
    }

    // 同步池 7：固定翼喷口热浪折射锥（零堆分配，战略高空 > 1800m 自动归零省性能）
    if (this.heatShimmerPool) {
      if (this._shimmerUniforms) {
        this._shimmerUniforms.uTime.value = this._simClockSec;
        this._shimmerUniforms.uSpanMeters.value = this.spanMeters;
      }
      const nShimmer = (this.heatShimmerEnabled && this.spanMeters <= 1800) ? this._shimmerFrameCount : 0;
      const midWidthBoost = this.spanMeters > 220 ? Math.min(1.55, 1.0 + (this.spanMeters - 220) / 900) : 1.0;
      // 680p 中远景最小屏幕半宽补偿（>= 1.5px 半径，即锥宽 >= 3px 防像素化吞噬）
      const minMidRadiusM = this.spanMeters > 220 ? Math.min(3.2, (this.spanMeters / 680.0) * 1.5) : 0.0;
      const pArr = this._shimmerAttrParams.array;
      const gArr = this._shimmerAttrGeo.array;
      for (let i = 0; i < nShimmer; i += 1) {
        const s = this._shimmerSlots[i];
        this._pos.set(s.x, s.y, s.z);
        this._dir.set(s.dx, s.dy, s.dz);
        if (this._dir.lengthSq() < 1e-5) this._dir.set(0, 0, -1);
        else this._dir.normalize();
        this._quat.setFromUnitVectors(this._forwardAxis, this._dir);
        this._scale.set(1, 1, 1);
        this._mat4.compose(this._pos, this._quat, this._scale);
        this.heatShimmerPool.setMatrixAt(i, this._mat4);
        const baseR = Math.max(0.2, s.baseR * midWidthBoost, minMidRadiusM * 0.55);
        const tipR = Math.max(baseR * 1.25, s.tipR * midWidthBoost, minMidRadiusM);
        const coneLen = Math.max(2.0, s.len);
        const gOff = i * 3;
        gArr[gOff] = baseR;
        gArr[gOff + 1] = tipR;
        gArr[gOff + 2] = coneLen;
        const o = i * 4;
        pArr[o] = s.distPx;
        pArr[o + 1] = s.flowSpeed;
        pArr[o + 2] = s.noiseFreq;
        pArr[o + 3] = s.seed;
      }
      this._shimmerActiveCount = nShimmer;
      this._shimmerFrameCount = 0;
      this.heatShimmerPool.count = nShimmer;
      this.heatShimmerPool.visible = nShimmer > 0;
      if (nShimmer > 0) {
        this.heatShimmerPool.instanceMatrix.needsUpdate = true;
        this._shimmerAttrParams.needsUpdate = true;
        this._shimmerAttrGeo.needsUpdate = true;
      }
    }
  }

  clearAll() {
    this._ordnanceFrameCount = 0;
    this._frameConeCount = 0;
    this._shimmerFrameCount = 0;
    this._shimmerActiveCount = 0;
    if (this.heatShimmerPool) {
      this.heatShimmerPool.count = 0;
      this.heatShimmerPool.visible = false;
    }
    this.tracerEmittedRed = 0;
    this.tracerEmittedGreen = 0;
    this.fumerEmittedCount = 0;
    this.radialFragmentJetCount = 0;
    this.lightSplashRingCount = 0;
    this.apiImpactCount = 0;
    this.sparkStreakEmittedCount = 0;
    this.contrailEmittedCount = 0;
    this.sonicBoomEmittedCount = 0;
    this.frustumCulledCount = 0;
    this.wingtipVortexActive = false;
    this.wingtipFadeAlpha = 0;
    this.flareActiveCount = 0;
    for (const f of this._flareSlots) f.active = false;
    for (const b of this._blastSlots) { b.active = false; b.delay = 0; b.tag = ''; }
    for (const s of this._smokeSlots) s.active = false;
    for (const c of this._craterSlots) { c.active = false; c.ageSec = 0; c.isMajor = false; }
    this._majorCraterCursor = 0;
    this._lightCraterCursor = 0;
    this._activeCraterCount = 0;
    this._activeMajorCraterCount = 0;
    this._activeLightCraterCount = 0;
    this._dedupedCraterCount = 0;
    for (const r of this._ribbons) { r.active = false; r.count = 0; r.alpha = 0; }
    this._ribbonByKey.clear();
    for (const c of this._coneSlots) c.active = false;
    this._craterDirty = true;
    this.update(0.001, 0);
  }

  getStats(includeSamples = true) {
    const p1 = this.ordnancePool.visible && this.ordnancePool.count > 0 ? 1 : 0;
    const p2 = this.blastShellPool.visible && this.blastShellPool.count > 0 ? 1 : 0;
    const p3 = this.smokeChunkPool.visible && this.smokeChunkPool.count > 0 ? 1 : 0;
    const p4 = this.craterPool.visible && this.craterPool.count > 0 ? 1 : 0;
    const p5 = this.sharedRibbonMesh.visible ? 1 : 0;
    const p6 = this.coneFxPool.visible && this.coneFxPool.count > 0 ? 1 : 0;
    const res = {
      vfxVersion: this.vfxVersion,
      poolDrawCalls: p1 + p2 + p3 + p4 + p5 + p6,
      maxPoolCount: 6,
      pools: {
        ordnance: this.ordnancePool.count,
        blastShell: this.blastShellPool.count,
        smokeChunk: this.smokeChunkPool.count,
        crater: this.craterPool.count,
        ribbon: this._activeRibbonCount || 0,
        coneFx: this.coneFxPool.count,
      },
      activeParticles: this.ordnancePool.count + this.blastShellPool.count + this.smokeChunkPool.count + this.coneFxPool.count,
      activeRibbons: this._activeRibbonCount || 0,
      activeCraters: this._activeCraterCount,
      maxCraters: MAX_CRATERS,
      majorCraterSlots: MAJOR_CRATER_SLOTS,
      lightCraterSlots: LIGHT_CRATER_SLOTS,
      activeMajorCraters: this._activeMajorCraterCount,
      activeLightCraters: this._activeLightCraterCount,
      dedupedCraters: this._dedupedCraterCount,
      activeExplosionLayers: [...this.activeExplosionLayers],
      wingtipVortexActive: this.wingtipVortexActive,
      wingtipFadeAlpha: Number(this.wingtipFadeAlpha.toFixed(3)),
      flareActiveCount: this.flareActiveCount,
      contrailEmittedCount: this.contrailEmittedCount,
      sonicBoomEmittedCount: this.sonicBoomEmittedCount,
      lodTier: this.lodTier,
      spanMeters: Number(this.spanMeters.toFixed(1)),
      frustumCulledCount: this.frustumCulledCount,
      linearOutput: this.linearOutput,
      tracerColorMode: this.tracerColorMode,
      atlasReady: this.atlasReady,
      atlasLoadedCount: this.atlasLoadedCount,
      hasSceneDepth: this.hasSceneDepth,
      lastFrameUsedDepth: this.lastFrameUsedDepth,
      sunDir: [Number(this._sunDir.x.toFixed(4)), Number(this._sunDir.y.toFixed(4)), Number(this._sunDir.z.toFixed(4))],
      vfxRootInMainSceneOnly: this.vfxRoot.parent === this.scene,
      heatShimmerEnabled: this.heatShimmerEnabled,
      heatShimmerCount: this.heatShimmerEnabled ? (this._shimmerActiveCount || 0) : 0,
      heatShimmerDrawCalls: this.hasActiveHeatShimmer() ? 1 : 0,
    };
    if (includeSamples) {
      res.samples = this.getDebugSamples();
    }
    return res;
  }

  getDebugSamples() {
    const smokes = [];
    for (let i = 0; i < MAX_SMOKE_CHUNKS && smokes.length < 4; i += 1) {
      const s = this._smokeSlots[i];
      if (s.active) smokes.push({ kind: s.layerKind, x: Number(s.x.toFixed(1)), y: Number(s.y.toFixed(1)), z: Number(s.z.toFixed(1)), age: Number(s.age.toFixed(2)), baseScale: s.baseScale });
    }
    const cones = [];
    for (let i = 0; i < Math.min(4, this.coneFxPool.count); i += 1) {
      this.coneFxPool.getMatrixAt(i, this._mat4);
      const e = this._mat4.elements;
      cones.push({ kind: this._coneAttrKind.array[i], x: Number(e[12].toFixed(1)), y: Number(e[13].toFixed(1)), z: Number(e[14].toFixed(1)) });
    }
    let activeFragmentJets = 0;
    let activeSparkStreaks = 0;
    for (let i = 0; i < this.coneFxPool.count; i += 1) {
      const k = this._coneAttrKind.array[i];
      if (k === 5) activeFragmentJets += 1;
      else if (k === 6) activeSparkStreaks += 1;
    }
    let activeLightSplashRings = 0;
    for (let i = 0; i < MAX_BLAST_SHELLS; i += 1) {
      const b = this._blastSlots[i];
      if (b.active && b.delay <= 0 && b.tag === 'light_splash') activeLightSplashRings += 1;
    }
    const ords = [];
    for (let i = 0; i < Math.min(4, this.ordnancePool.count); i += 1) {
      this.ordnancePool.getMatrixAt(i, this._mat4);
      const e = this._mat4.elements;
      ords.push({ x: Number(e[12].toFixed(1)), y: Number(e[13].toFixed(1)), z: Number(e[14].toFixed(1)) });
    }
    const ribbons = [];
    for (let i = 0; i < MAX_RIBBONS && ribbons.length < 3; i += 1) {
      const r = this._ribbons[i];
      if (r.active) ribbons.push({ kind: r.kind, count: r.count, alpha: Number(r.alpha.toFixed(2)), width: r.width, p0: r.count > 0 ? [Number(r.points[0].toFixed(1)), Number(r.points[1].toFixed(1)), Number(r.points[2].toFixed(1))] : null });
    }
    return {
      tracerColorMode: this.tracerColorMode,
      tracerStats: {
        red: this.tracerEmittedRed,
        green: this.tracerEmittedGreen,
        fumer: this.fumerEmittedCount,
      },
      activeFragmentJets,
      totalFragmentJetsSpawned: this.radialFragmentJetCount,
      activeSparkStreaks,
      totalSparkStreaksSpawned: this.sparkStreakEmittedCount,
      activeLightSplashRings,
      totalLightSplashRingsSpawned: this.lightSplashRingCount,
      totalApiImpactsSpawned: this.apiImpactCount,
      smokes,
      cones,
      ords,
      ribbons,
    };
  }

  dispose() {
    if (this._disposed) return;
    this._disposed = true;
    this.setSceneDepth(null);
    this.vfxRoot.removeFromParent();
    for (const m of [this.ordnancePool, this.blastShellPool, this.smokeChunkPool, this.craterPool, this.sharedRibbonMesh, this.coneFxPool, this.heatShimmerPool]) {
      m?.geometry?.dispose?.();
      m?.material?.dispose?.();
      if (m?.isInstancedMesh && typeof m.dispose === 'function') {
        m.dispose();
      }
    }
    this._fallbackNormalAlphaTex?.dispose?.();
    this._fallbackHeatThickTex?.dispose?.();
    this._dummyDepthTex?.dispose?.();
    this._loadedNormalAlphaTex?.dispose?.();
    this._loadedHeatThickTex?.dispose?.();
  }
}
