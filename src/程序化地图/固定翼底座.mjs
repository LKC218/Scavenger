/**
 * Scavenger 固定翼通用底座 V2（Unity Prefab 风格组件化架构 + 32 架 5 原型族 + A-10C 样板机）：
 * - 唯一数值基底收藏：https://brokenarrowstats.com/arsenal/?country=2&cat=6&lang=zh
 *   实行「原值留底 sourceRef → 统一缩放换算 → 对尸实战值 gameStats」三层分离；
 * - 完整支持《断箭》式零瞬移真实物理入航、拖线扫射/轰炸/火箭齐射、防区外导弹点射、Shift 攻击排队、
 *   自定义盘旋半径（按机型 [minOrbitRadiusM, maxOrbitRadiusM] 钳位）、高低空切换与预测航线 ETA；
 * - 无顶层 Three.js / DOM 硬依赖，纯规则与状态机可直接运行于 Node ESM 单元测试。
 */

import {
  BROKEN_ARROW_STATS_BASE_URL,
  GLOBAL_BALANCE_SCALES,
  deriveAircraftStatsFromSourceRef,
  deriveWeaponStatsFromSourceRef,
} from './数值基底与倍率规范.mjs';

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;
const GRAVITY = 9.81;
const BATTLEFIELD_EXIT_HALF_EXTENT = 4600;
const BATTLEFIELD_EXIT_TARGET_EXTENT = 4820;

export const BROKEN_ARROW_STATS_URL = BROKEN_ARROW_STATS_BASE_URL;

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function lerp(a, b, t) {
  return a + (b - a) * clamp(t, 0, 1);
}

function wrapAngleRad(rad) {
  let a = (Number(rad) || 0) % (Math.PI * 2);
  if (a > Math.PI) a -= Math.PI * 2;
  if (a < -Math.PI) a += Math.PI * 2;
  return a;
}

function expAlpha(rate, dt) {
  return 1 - Math.exp(-Math.max(0, rate) * Math.max(0, dt));
}

/**
 * 纯规则：计算连续 L1 前视盘旋导引的角速度与协调转弯坡度（消除双段切换硬吸附拐折）。
 */
export function computeL1OrbitCommand({
  posX,
  posZ,
  yaw,
  speedMps,
  orbitCenter,
  orbitRadius,
  minOrbitRadius = 80,
  maxOrbitRadius = 420,
  maxTurnRateRad = 1.15,
  maxBankDeg = 52,
  defaultOrbitRadius = 150,
  cruiseSpeedMps = 97.5,
} = {}) {
  const cx = Number(orbitCenter?.x) || 0;
  const cz = Number(orbitCenter?.z) || 0;
  const r = clamp(Number(orbitRadius) || defaultOrbitRadius, minOrbitRadius, maxOrbitRadius);
  const v = Math.max(20, Number(speedMps) || cruiseSpeedMps);
  const dxC = (Number(posX) || 0) - cx;
  const dzC = (Number(posZ) || 0) - cz;
  const distC = Math.hypot(dxC, dzC);
  const theta = distC > 1e-3 ? Math.atan2(dzC, dxC) : 0;

  const l1Arc = clamp(v * 0.95, 45, 0.85 * r);
  const dTheta = clamp(l1Arc / Math.max(40, r, distC * 0.75), 0.22, 0.95);
  const lookTheta = theta + dTheta;
  const lookX = cx + Math.cos(lookTheta) * r;
  const lookZ = cz + Math.sin(lookTheta) * r;

  const toLookX = lookX - (Number(posX) || 0);
  const toLookZ = lookZ - (Number(posZ) || 0);
  const lActual = Math.max(20, Math.hypot(toLookX, toLookZ));
  const desiredHeading = Math.atan2(toLookX, toLookZ);
  const eta = wrapAngleRad(desiredHeading - (Number(yaw) || 0));
  const etaEff = clamp(eta, -Math.PI * 0.5, Math.PI * 0.5);

  const rawOmega = (2 * v * Math.sin(etaEff)) / lActual;
  const yawRateCmd = clamp(rawOmega, -maxTurnRateRad, maxTurnRateRad);

  const refBankDeg = Math.min(30, maxBankDeg * 0.78);
  const gVis = Math.max(
    65,
    (cruiseSpeedMps * cruiseSpeedMps) / (Math.max(40, defaultOrbitRadius) * Math.tan(refBankDeg * DEG2RAD)),
  );
  const bankDegCmd = clamp(Math.atan((v * yawRateCmd) / gVis) * RAD2DEG, -maxBankDeg, maxBankDeg);

  return {
    lookPoint: { x: lookX, z: lookZ },
    yawRateCmd,
    bankDegCmd,
    distFromCenter: distC,
    orbitRadius: r,
  };
}

/**
 * 纯规则：计算轻量化能量机动空速增量（俯冲微加速、爬升微掉速）。
 */
export function computeEnergyManeuverDeltaMps(pitchDeg, dtSec) {
  const dt = Math.max(0, Number(dtSec) || 0);
  const pitchRad = (Number(pitchDeg) || 0) * DEG2RAD;
  return -GRAVITY * Math.sin(pitchRad) * 0.42 * dt;
}

/**
 * 纯规则：沿航向前方 3 个按空速缩放的探测点采样地形最大高程，供低空前瞻改出防撞使用（零堆分配）。
 */
export function sampleAheadMaxTerrainY(terrainSupport, x, z, yaw, speedMps = 97.5) {
  if (typeof terrainSupport?.heightAt !== 'function') return 2.0;
  const baseY = Number(terrainSupport.heightAt(x, z));
  let maxY = Number.isFinite(baseY) ? baseY : 2.0;
  const fwdX = Math.sin(Number(yaw) || 0);
  const fwdZ = Math.cos(Number(yaw) || 0);
  const spdScale = clamp((Number(speedMps) || 95) / 95, 0.75, 1.8);
  for (let i = 1; i <= 3; i += 1) {
    const dist = 120 * i * spdScale;
    const sy = Number(terrainSupport.heightAt(x + fwdX * dist, z + fwdZ * dist));
    if (Number.isFinite(sy) && sy > maxY) {
      maxY = sy;
    }
  }
  return maxY;
}

/**
 * 原生 GLB / meta 坐标（-Z 前 / -X 左）→ rigBodyGroup 对齐坐标（+Z 前 / +X 左）。
 */
export function nativeToRigCoords(pos) {
  return [-(Number(pos?.[0]) || 0), Number(pos?.[1]) || 0, -(Number(pos?.[2]) || 0)];
}

/**
 * 5 大固定翼功能原型族（Prefab 基础 FlightProfile 组件模板，覆盖 26 架 fixed_wing + 6 架 uav = 32 架）。
 */
export const FIXED_WING_ARCHETYPES = Object.freeze({
  cas_gunship: Object.freeze({
    id: 'cas_gunship',
    label: '近距对地支援与炮艇族',
    cruiseSpeedMps: 97.5,
    attackSpeedMps: 125,
    lowAltitudeM: 135,
    highAltitudeM: 215,
    cruiseAltitudeM: 185,
    minRecoveryClearanceM: 55,
    minTurnRadiusM: 88,
    orbitRadiusM: 150,
    minOrbitRadiusM: 120,
    maxOrbitRadiusM: 380,
    maxBankDeg: 52,
    maxRollRateDegPerSec: 64,
    maxDivePitchDeg: -32,
    maxClimbPitchDeg: 28,
    maxTurnRateRad: 1.15,
    cruiseAoaDeg: 2.8,
    wingtipVortexBankDeg: 40,
    defaultBehaviors: Object.freeze(['DiveStrafe', 'BombingRun', 'StandoffMissile', 'RocketSalvo']),
    defaultCapabilities: Object.freeze({
      supersonic: false,
      afterburner: false,
      contrail: false,
      sonicBoom: false,
    }),
    defaultHeatShimmer: Object.freeze({
      cruiseLengthM: 13.5,
      maxLengthM: 18.5,
      baseRadiusM: 0.78,
      tipRadiusM: 2.6,
      cruiseDistortionPx: 2.8,
      maxDistortionPx: 4.5,
      flowSpeed: 16.0,
      noiseFreq: 2.6,
    }),
  }),
  supersonic_multirole: Object.freeze({
    id: 'supersonic_multirole',
    label: '超音速制空与多用途战机族',
    cruiseSpeedMps: 92,
    attackSpeedMps: 148,
    lowAltitudeM: 165,
    highAltitudeM: 340,
    cruiseAltitudeM: 280,
    minRecoveryClearanceM: 85,
    minTurnRadiusM: 110,
    orbitRadiusM: 220,
    minOrbitRadiusM: 150,
    maxOrbitRadiusM: 420,
    maxBankDeg: 68,
    maxRollRateDegPerSec: 120,
    maxDivePitchDeg: -38,
    maxClimbPitchDeg: 42,
    maxTurnRateRad: 1.25,
    cruiseAoaDeg: 2.2,
    wingtipVortexBankDeg: 38,
    defaultBehaviors: Object.freeze(['DiveStrafe', 'BombingRun', 'StandoffMissile']),
    defaultCapabilities: Object.freeze({
      supersonic: true,
      afterburner: true,
      contrail: true,
      sonicBoom: true,
    }),
    defaultHeatShimmer: Object.freeze({
      cruiseLengthM: 14.0,
      maxLengthM: 24.0,
      baseRadiusM: 0.68,
      tipRadiusM: 2.65,
      cruiseDistortionPx: 3.4,
      maxDistortionPx: 7.0,
      flowSpeed: 22.0,
      noiseFreq: 3.1,
    }),
  }),
  strategic_stealth_bomber: Object.freeze({
    id: 'strategic_stealth_bomber',
    label: '战略轰炸与隐身突防族',
    cruiseSpeedMps: 78,
    attackSpeedMps: 96,
    lowAltitudeM: 240,
    highAltitudeM: 440,
    cruiseAltitudeM: 380,
    minRecoveryClearanceM: 150,
    minTurnRadiusM: 185,
    orbitRadiusM: 320,
    minOrbitRadiusM: 220,
    maxOrbitRadiusM: 560,
    maxBankDeg: 30,
    maxRollRateDegPerSec: 28,
    maxDivePitchDeg: -16,
    maxClimbPitchDeg: 16,
    maxTurnRateRad: 0.45,
    cruiseAoaDeg: 2.4,
    wingtipVortexBankDeg: 28,
    defaultBehaviors: Object.freeze(['BombingRun', 'StandoffMissile']),
    defaultCapabilities: Object.freeze({
      supersonic: false,
      afterburner: false,
      contrail: true,
      sonicBoom: false,
    }),
    defaultHeatShimmer: Object.freeze({
      cruiseLengthM: 11.0,
      maxLengthM: 18.5,
      baseRadiusM: 0.92,
      tipRadiusM: 2.8,
      cruiseDistortionPx: 2.4,
      maxDistortionPx: 5.2,
      flowSpeed: 18.0,
      noiseFreq: 2.5,
    }),
  }),
  heavy_support_ew: Object.freeze({
    id: 'heavy_support_ew',
    label: '重型运输、加油与电子支援族',
    cruiseSpeedMps: 60,
    attackSpeedMps: 70,
    lowAltitudeM: 195,
    highAltitudeM: 350,
    cruiseAltitudeM: 290,
    minRecoveryClearanceM: 120,
    minTurnRadiusM: 160,
    orbitRadiusM: 260,
    minOrbitRadiusM: 180,
    maxOrbitRadiusM: 520,
    maxBankDeg: 26,
    maxDivePitchDeg: -14,
    maxClimbPitchDeg: 14,
    maxTurnRateRad: 0.48,
    wingtipVortexBankDeg: 28,
    defaultBehaviors: Object.freeze(['AirdropSupport']),
    defaultCapabilities: Object.freeze({
      supersonic: false,
      afterburner: false,
      contrail: false,
      sonicBoom: false,
    }),
    defaultHeatShimmer: Object.freeze({
      cruiseLengthM: 8.5,
      maxLengthM: 12.5,
      baseRadiusM: 0.85,
      tipRadiusM: 2.4,
      cruiseDistortionPx: 1.9,
      maxDistortionPx: 3.2,
      flowSpeed: 14.0,
      noiseFreq: 2.4,
    }),
  }),
  fixed_wing_uav: Object.freeze({
    id: 'fixed_wing_uav',
    label: '固定翼无人侦察与察打一体族',
    cruiseSpeedMps: 40,
    attackSpeedMps: 48,
    lowAltitudeM: 140,
    highAltitudeM: 270,
    cruiseAltitudeM: 220,
    minRecoveryClearanceM: 90,
    minTurnRadiusM: 55,
    orbitRadiusM: 90,
    minOrbitRadiusM: 60,
    maxOrbitRadiusM: 240,
    maxBankDeg: 34,
    maxDivePitchDeg: -20,
    maxClimbPitchDeg: 20,
    maxTurnRateRad: 0.72,
    wingtipVortexBankDeg: 99,
    defaultBehaviors: Object.freeze(['ReconMark', 'StandoffMissile']),
    defaultCapabilities: Object.freeze({
      supersonic: false,
      afterburner: false,
      contrail: false,
      sonicBoom: false,
    }),
    defaultHeatShimmer: Object.freeze({
      cruiseLengthM: 5.5,
      maxLengthM: 8.0,
      baseRadiusM: 0.48,
      tipRadiusM: 1.45,
      cruiseDistortionPx: 1.5,
      maxDistortionPx: 2.4,
      flowSpeed: 13.0,
      noiseFreq: 2.8,
    }),
  }),
});

/**
 * `US_A10`（A-10C Thunderbolt II）精确硬点表（全部已转换为 rigBodyGroup 空间：+Z 机头 / +X 左翼 / +Y 上）。
 */
export const US_A10_HARDPOINTS = Object.freeze({
  muzzle: Object.freeze([0.069, 1.661, 7.208]),
  exhaustsNative: Object.freeze([
    Object.freeze([-1.42, 3.3313, 4.3]),
    Object.freeze([1.42, 3.3313, 4.3]),
  ]),
  exhaustsRig: Object.freeze([
    Object.freeze([1.42, 3.3313, -4.3]),
    Object.freeze([-1.42, 3.3313, -4.3]),
  ]),
  flareOrigin: Object.freeze([0.0, 2.4813, -4.51]),
  damageSmokeOrigin: Object.freeze([1.55, 3.05, -3.45]),
  wingtips: Object.freeze([
    Object.freeze([8.7959, 2.6347, -1.396]),
    Object.freeze([-8.7959, 2.6347, -1.396]),
  ]),
  pylons: Object.freeze([
    Object.freeze({ index: 0, turret: 'turret_3', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [3.677 - 0.4718, 1.743 - 0.1358, -0.385 + 0.3134] }),
    Object.freeze({ index: 1, turret: 'turret_3', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [3.677 + 0.4610, 1.743 - 0.1358, -0.385 + 0.3134] }),
    Object.freeze({ index: 2, turret: 'turret_3', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [3.677, 1.743 - 0.6160, -0.385 + 0.2713] }),
    Object.freeze({ index: 3, turret: 'turret_4', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [-3.673 + 0.4718, 1.743 - 0.1358, -0.385 + 0.3134] }),
    Object.freeze({ index: 4, turret: 'turret_4', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [-3.673 - 0.4610, 1.743 - 0.1358, -0.385 + 0.3134] }),
    Object.freeze({ index: 5, turret: 'turret_4', ordnanceType: 'agm65', weaponId: 'agm65_maverick', pos: [-3.673, 1.743 - 0.6160, -0.385 + 0.2713] }),
    Object.freeze({ index: 6, turret: 'turret_1', ordnanceType: 'gbu31', weaponId: 'gbu31_jdam', pos: [1.705, 1.653 - 0.32, -0.385] }),
    Object.freeze({ index: 7, turret: 'turret_2', ordnanceType: 'gbu31', weaponId: 'gbu31_jdam', pos: [-1.704, 1.653 - 0.32, -0.385] }),
    Object.freeze({ index: 8, turret: 'turret_9', ordnanceType: 'gbu31', weaponId: 'gbu31_jdam', pos: [0.592, 1.653 - 0.32, -0.385] }),
    Object.freeze({ index: 9, turret: 'turret_10', ordnanceType: 'gbu31', weaponId: 'gbu31_jdam', pos: [-0.593, 1.653 - 0.32, -0.385] }),
    Object.freeze({ index: 10, turret: 'turret_5', ordnanceType: 'rocket_pod', weaponId: 'hydra70_rocket', pos: [4.71, 1.8819 - 0.25, -0.5577] }),
    Object.freeze({ index: 11, turret: 'turret_6', ordnanceType: 'rocket_pod', weaponId: 'hydra70_rocket', pos: [-4.71, 1.8819 - 0.25, -0.5577] }),
    Object.freeze({ index: 12, turret: 'turret_8', ordnanceType: 'rocket_pod', weaponId: 'hydra70_rocket', pos: [-5.819, 1.9673 - 0.25, -0.5583] }),
    Object.freeze({ index: 13, turret: 'turret_7', ordnanceType: 'ecm_pod', weaponId: 'alq131_ecm', pos: [5.822, 1.9673 - 0.25, -0.5583] }),
  ]),
});

/**
 * A-10C 全对地武器配装（V2.2：严格经 GLOBAL_BALANCE_SCALES 由原站 sourceRef 派生 + 3D 扫射盒/爆破半径补充）。
 */
export function createA10WeaponsSpec() {
  return [
    deriveWeaponStatsFromSourceRef(
      { groundRangeM: 1000, minRangeM: 0, damage: 2.25, pen: 75, projectileSpeedMps: 1010 },
      {
        id: 'gau8_30mm',
        name: 'GAU-8/A 30mm 机炮',
        modelLabel: 'GAU-8/A',
        categoryLabel: '30mm 机炮',
        shortLabel: '机炮扫射',
        hotkey: '1',
        kind: 'autocannon',
        actionCmd: 'strafe',
        inputMode: 'drag_line',
        boostAccelSec: 0,
        gravityScale: 0,
        ammo: 1150,
        maxAmmo: 1150,
        salvoCount: 102,
        canTargetAir: false,
        canTargetGround: true,
        minRangeM: 160,
        burstAxisLenM: 120,
        defaultStrafeLenM: 120,
        strafeLenMinM: 60,
        strafeLenMaxM: 240,
        strafeWidthM: 20,
        roundsPerMeter: 0.85,
        splashInnerM: 2.2,
        splashOuterM: 5.5,
        edgeMultiplier: 0.25,
        pylonIndices: [],
        muzzleLocal: [...US_A10_HARDPOINTS.muzzle],
        fireAudioKey: 'SFX_AIR_PLANEGUN_30mm_GAU8_US',
        impactAudioKey: 'SFX_Explosion_20-30mm_SupFAST_HE',
        explosionTier: 'light',
        tracerColor: 'red',
        combatMixRatio: [4, 1], // 现实美军 4:1 Combat Mix：4 发 PGU-14/B API + 1 发 PGU-13/B HEI
        tracerEveryN: 5,        // 默认每 5 脉冲 1 发高亮 HDR 曳光，其余 4 发为暗金属实弹 + 低调 Fumer 气痕
        cooldownSec: 0.04,
        gunAcousticPreset: {
          presetId: 'gau8_30mm',
          rpm: 3970,
          caliberMm: 30,
          barrelCount: 7,
          shockStrength: 0.95,
          realTextureStrength: 0.75,
          thunderTailStrength: 0.95,
          primaryRealKey: 'SFX_AIR_PLANEGUN_30mm_GSH30_RU',
          secondaryRealKey: 'SFX_AIR_AAG_25mm_GAU12_US',
          impactKey: 'SFX_Explosion_20-30mm_SupFAST_HE',
          withMachCrack: true,
        },
      },
    ),
    deriveWeaponStatsFromSourceRef(
      { groundRangeM: 1100, minRangeM: 150, damage: 7.5, pen: 120, projectileSpeedMps: 450, realPeakSpeedMps: 739 },
      {
        id: 'hydra70_rocket',
        name: 'Hydra 70 火箭巢',
        modelLabel: 'Hydra 70',
        categoryLabel: '火箭弹',
        shortLabel: '火箭齐射',
        hotkey: '2',
        kind: 'rocket',
        actionCmd: 'rocket',
        inputMode: 'drag_line',
        boostAccelSec: 0.65,
        gravityScale: 0,
        ammo: 21,
        maxAmmo: 21,
        salvoCount: 7,
        canTargetAir: false,
        canTargetGround: true,
        minRangeM: 180,
        burstAxisLenM: 90,
        defaultStrafeLenM: 90,
        strafeLenMinM: 60,
        strafeLenMaxM: 180,
        strafeWidthM: 22,
        splashInnerM: 3.0,
        splashOuterM: 8.5,
        edgeMultiplier: 0.25,
        keepPylonWhenEmpty: true,
        pylonIndices: [10, 11, 12],
        muzzleLocal: [4.71, 1.63, -0.56],
        fireAudioKey: 'SFX_GENERIC_ROCKETPOD_Small',
        impactAudioKey: 'SFX_Expl_ROCKETPOD_62-77',
        explosionTier: 'medium',
        cooldownSec: 0.11,
      },
    ),
    deriveWeaponStatsFromSourceRef(
      { groundRangeM: 1250, minRangeM: 200, damage: 22.0, pen: 900, projectileSpeedMps: 320 },
      {
        id: 'agm65_maverick',
        name: 'AGM-65E2 小牛导弹',
        modelLabel: 'AGM-65E2',
        categoryLabel: '空地导弹',
        shortLabel: '发射导弹',
        hotkey: '3',
        kind: 'agm',
        actionCmd: 'missile',
        inputMode: 'point_target',
        boostAccelSec: 1.0,
        gravityScale: 0,
        ammo: 6,
        maxAmmo: 6,
        salvoCount: 1,
        canTargetAir: false,
        canTargetGround: true,
        minRangeM: 250,
        burstAxisLenM: 0,
        splashInnerM: 6.0,
        splashOuterM: 18.0,
        edgeMultiplier: 0.25,
        pylonIndices: [0, 3, 1, 4, 2, 5],
        muzzleLocal: [3.677, 1.5, -0.1],
        fireAudioKey: 'SFX_GENERIC_ATGM_Missile_US',
        impactAudioKey: 'SFX_Expl_MISSILE_AA-A2A-A2G_Medium',
        explosionTier: 'medium',
        cooldownSec: 0.40,
      },
    ),
    deriveWeaponStatsFromSourceRef(
      { groundRangeM: 1000, minRangeM: 0, damage: 56.0, pen: 300, projectileSpeedMps: 125, gravityScale: 1.0 },
      {
        id: 'gbu31_jdam',
        name: 'GBU-31 JDAM 航弹',
        modelLabel: 'GBU-31',
        categoryLabel: '制导航弹',
        shortLabel: '投掷航弹',
        hotkey: '4',
        kind: 'bomb',
        actionCmd: 'bomb',
        inputMode: 'multi_point',
        boostAccelSec: 0,
        gravityScale: 1.0,
        ammo: 4,
        maxAmmo: 4,
        salvoCount: 1,
        canTargetAir: false,
        canTargetGround: true,
        minRangeM: 160,
        burstAxisLenM: 105,
        splashInnerM: 12.0,
        splashOuterM: 32.0,
        edgeMultiplier: 0.25,
        pylonIndices: [6, 7, 8, 9],
        muzzleLocal: [1.705, 1.33, -0.385],
        fireAudioKey: 'BA_DropBomb',
        whistleAudioKey: 'BA_Whistle_Bomb_Big',
        impactAudioKey: 'SFX_Explosion_Cruise_Ballistic_Big',
        explosionTier: 'heavy',
        cooldownSec: 0.55,
      },
    ),
    {
      id: 'alq131_ecm',
      name: 'AN/ALQ-131 干扰吊舱',
      shortLabel: '干扰吊舱',
      hotkey: '',
      kind: 'ecm_pod',
      actionCmd: '',
      inputMode: 'passive',
      ammo: 0,
      maxAmmo: 0,
      salvoCount: 0,
      canTargetAir: false,
      canTargetGround: false,
      minRangeM: 0,
      maxRangeM: 0,
      burstAxisLenM: 0,
      damage: 0,
      splashInnerM: 0,
      splashOuterM: 0,
      edgeMultiplier: 0,
      pylonIndices: [13],
      muzzleLocal: [5.822, 1.72, -0.56],
      fireAudioKey: '',
      impactAudioKey: '',
      explosionTier: 'none',
      cooldownSec: 0,
      sourceRef: { ecmPct: 20 },
    },
  ];
}

/**
 * B-1B 枪骑兵战略重磅轰炸配装（8 枚 GBU-31 2000lb 重磅 JDAM，经 GLOBAL_BALANCE_SCALES 由原站 sourceRef 派生）。
 */
export function createB1BWeaponsSpec() {
  return [
    deriveWeaponStatsFromSourceRef(
      { groundRangeM: 1200, minRangeM: 0, damage: 56.0, pen: 300, projectileSpeedMps: 130, gravityScale: 1.0 },
      {
        id: 'b1b_gbu31_heavy',
        name: 'GBU-31 JDAM 2000lb 重磅航弹',
        shortLabel: '战略重轰',
        hotkey: '1',
        kind: 'bomb',
        actionCmd: 'bomb',
        inputMode: 'multi_point',
        boostAccelSec: 0,
        gravityScale: 1.0,
        ammo: 8,
        maxAmmo: 8,
        salvoCount: 2,
        canTargetAir: false,
        canTargetGround: true,
        minRangeM: 200,
        burstAxisLenM: 160,
        splashInnerM: 13.0,
        splashOuterM: 34.0,
        edgeMultiplier: 0.25,
        pylonIndices: [],
        muzzleLocal: [0, 2.2, 0.5],
        fireAudioKey: 'BA_DropBomb',
        whistleAudioKey: 'BA_Whistle_Bomb_Big',
        impactAudioKey: 'SFX_Explosion_Cruise_Ballistic_Big',
        explosionTier: 'heavy',
        cooldownSec: 0.45,
      },
    ),
  ];
}

function makeRegistryEntry({
  slug,
  name,
  cnName,
  role = 'fixed_wing',
  archetype,
  size,
  exhausts = [],
  defaultEngineAudio = '',
  capabilities = null,
  hardpoints = null,
  weaponsFactory = null,
  flightProfileOverride = null,
  attackBehaviors = null,
  sourceRef = null,
  gameStats = null,
}) {
  const arch = FIXED_WING_ARCHETYPES[archetype] || FIXED_WING_ARCHETYPES.cas_gunship;
  const caps = Object.freeze({ ...arch.defaultCapabilities, ...(capabilities || {}) });
  const flightProfile = Object.freeze({
    cruiseSpeedMps: arch.cruiseSpeedMps,
    attackSpeedMps: arch.attackSpeedMps,
    lowAltitudeM: arch.lowAltitudeM,
    highAltitudeM: arch.highAltitudeM,
    cruiseAltitudeM: arch.cruiseAltitudeM,
    minRecoveryClearanceM: arch.minRecoveryClearanceM,
    minTurnRadiusM: arch.minTurnRadiusM,
    orbitRadiusM: arch.orbitRadiusM,
    minOrbitRadiusM: arch.minOrbitRadiusM,
    maxOrbitRadiusM: arch.maxOrbitRadiusM,
    maxBankDeg: arch.maxBankDeg,
    maxDivePitchDeg: arch.maxDivePitchDeg,
    maxClimbPitchDeg: arch.maxClimbPitchDeg,
    maxTurnRateRad: arch.maxTurnRateRad,
    wingtipVortexBankDeg: arch.wingtipVortexBankDeg,
    ...(flightProfileOverride || {}),
  });
  const halfSpan = (size?.[0] || 14) * 0.5;
  const defaultHardpoints = hardpoints || Object.freeze({
    muzzle: Object.freeze([0, (size?.[1] || 4) * 0.42, (size?.[2] || 15) * 0.45]),
    exhaustsNative: Object.freeze(exhausts.map(p => Object.freeze([...p]))),
    exhaustsRig: Object.freeze(exhausts.map(p => Object.freeze(nativeToRigCoords(p)))),
    flareOrigin: Object.freeze([0, (size?.[1] || 4) * 0.5, -(size?.[2] || 15) * 0.3]),
    damageSmokeOrigin: Object.freeze([0.8, (size?.[1] || 4) * 0.6, -2.0]),
    wingtips: Object.freeze([
      Object.freeze([halfSpan, (size?.[1] || 4) * 0.55, -1.2]),
      Object.freeze([-halfSpan, (size?.[1] || 4) * 0.55, -1.2]),
    ]),
    pylons: Object.freeze([]),
  });

  const resolvedSourceRef = Object.freeze({
    url: BROKEN_ARROW_STATS_URL,
    cost: archetype === 'fixed_wing_uav' ? 140 : archetype === 'strategic_stealth_bomber' ? 420 : 320,
    speedKmh: Math.round(flightProfile.attackSpeedMps * 3.6),
    hp: archetype === 'fixed_wing_uav' ? 10 : 22,
    opticsM: archetype === 'fixed_wing_uav' ? 4000 : 2400,
    ecmPct: 15,
    stealth: 1.0,
    ...(sourceRef || {}),
  });

  const derivedStats = deriveAircraftStatsFromSourceRef(
    resolvedSourceRef,
    { ...flightProfile, archetype, slug, afterburner: caps.afterburner },
    GLOBAL_BALANCE_SCALES,
  );
  const resolvedGameStats = Object.freeze({
    ...derivedStats,
    firepowerVs: Object.freeze({ light: 7, medium: 7, heavy: 6 }),
    ...(gameStats || {}),
  });

  const isTurboprop = /AC130|C130/i.test(slug);
  const baseShimmer = arch.defaultHeatShimmer || FIXED_WING_ARCHETYPES.cas_gunship.defaultHeatShimmer;
  const shimmerScale = isTurboprop ? 0.45 : 1.0;
  const heatShimmerProfile = Object.freeze({
    enabled: defaultHardpoints.exhaustsRig.length > 0,
    cruiseLengthM: Number((baseShimmer.cruiseLengthM * (isTurboprop ? 0.7 : 1.0)).toFixed(2)),
    maxLengthM: Number((baseShimmer.maxLengthM * (isTurboprop ? 0.75 : 1.0)).toFixed(2)),
    baseRadiusM: baseShimmer.baseRadiusM,
    tipRadiusM: baseShimmer.tipRadiusM,
    cruiseDistortionPx: Number((baseShimmer.cruiseDistortionPx * shimmerScale).toFixed(2)),
    maxDistortionPx: Number((baseShimmer.maxDistortionPx * shimmerScale).toFixed(2)),
    flowSpeed: baseShimmer.flowSpeed,
    noiseFreq: baseShimmer.noiseFreq,
  });

  return Object.freeze({
    slug,
    name,
    cnName,
    role,
    archetype,
    size: Object.freeze([...size]),
    exhausts: Object.freeze(exhausts.map(p => Object.freeze([...p]))),
    defaultEngineAudio,
    capabilities: caps,
    heatShimmerProfile,
    flightProfile,
    attackBehaviors: Object.freeze(attackBehaviors || [...arch.defaultBehaviors]),
    orbitAutoFireCapable: false,
    manualFireOnly: true,
    sourceRef: resolvedSourceRef,
    gameStats: resolvedGameStats,
    hardpoints: defaultHardpoints,
    createWeapons: weaponsFactory || (() => [
      deriveWeaponStatsFromSourceRef(
        { groundRangeM: 1000, minRangeM: 0, damage: 36, pen: 240 },
        {
          id: `${slug.toLowerCase()}_ground_std`,
          name: '标准对地武器',
          shortLabel: '对地打击',
          hotkey: '1',
          kind: 'bomb',
          actionCmd: 'bomb',
          inputMode: 'multi_point',
          ammo: 4,
          maxAmmo: 4,
          salvoCount: 1,
          canTargetAir: false,
          canTargetGround: true,
          minRangeM: 160,
          burstAxisLenM: 100,
          splashInnerM: 8,
          splashOuterM: 22,
          edgeMultiplier: 0.25,
          pylonIndices: [],
          muzzleLocal: [...defaultHardpoints.muzzle],
          fireAudioKey: 'BA_DropBomb',
          impactAudioKey: 'SFX_Expl_MISSILE_AA-A2A-A2G_Medium',
          explosionTier: 'medium',
          cooldownSec: 0.6,
        },
      ),
    ]),
  });
}

/**
 * 全部 32 架固定翼（26 fixed_wing + 6 uav）Prefab 组件化注册表。
 */
export const FIXED_WING_REGISTRY = Object.freeze({
  US_A10: makeRegistryEntry({
    slug: 'US_A10',
    name: 'A-10C Thunderbolt II',
    cnName: 'A-10C 雷电 II 攻击机',
    role: 'fixed_wing',
    archetype: 'cas_gunship',
    size: [17.5918, 4.0153, 16.171],
    exhausts: [[-1.42, 3.3313, 4.3], [1.42, 3.3313, 4.3]],
    defaultEngineAudio: 'US_A10_Turbine_New',
    capabilities: { supersonic: false, afterburner: false, contrail: false, sonicBoom: false },
    attackBehaviors: ['DiveStrafe', 'RocketSalvo', 'StandoffMissile', 'BombingRun'],
    sourceRef: {
      url: 'https://brokenarrowstats.com/unit/a-10c-thunderbolt-ii-282/?lang=zh',
      cost: 345,
      speedKmh: 450,
      hp: 30,
      opticsM: 2500,
      ecmPct: 20,
      stealth: 1.0,
    },
    gameStats: {
      firepowerVs: Object.freeze({ light: 10, medium: 9, heavy: 9 }),
    },
    hardpoints: US_A10_HARDPOINTS,
    weaponsFactory: createA10WeaponsSpec,
  }),
  US_AV8B_NightAttack: makeRegistryEntry({
    slug: 'US_AV8B_NightAttack',
    name: 'AV-8B Night attack',
    cnName: 'AV-8B 夜袭型鹞式攻击机',
    archetype: 'cas_gunship',
    size: [9.2459, 3.2716, 14.2877],
    exhausts: [[-0.893, 1.597, 1], [0.891, 1.636, 1]],
    defaultEngineAudio: 'SFX_Harrier_A6_Turbine_New',
    attackBehaviors: ['DiveStrafe', 'BombingRun', 'VtolHover'],
    sourceRef: { cost: 260, speedKmh: 760, hp: 20, opticsM: 2500 },
  }),
  US_AV8B_Plus: makeRegistryEntry({
    slug: 'US_AV8B_Plus',
    name: 'AV-8B Harrier II Plus',
    cnName: 'AV-8B II+ 鹞式攻击机',
    archetype: 'cas_gunship',
    size: [9.2468, 3.3121, 14.555],
    exhausts: [[-0.893, 1.63, 1.486], [0.891, 1.643, 1.5]],
    defaultEngineAudio: 'SFX_Harrier_A6_Turbine_New',
    attackBehaviors: ['DiveStrafe', 'StandoffMissile', 'BombingRun', 'VtolHover'],
    sourceRef: { cost: 295, speedKmh: 780, hp: 20, opticsM: 2600 },
  }),
  US_AC130_Base: makeRegistryEntry({
    slug: 'US_AC130_Base',
    name: 'AC-130',
    cnName: 'AC-130 空中炮艇',
    archetype: 'cas_gunship',
    size: [40.3413, 11.4441, 29.6574],
    exhausts: [[-10.01, 3.8046, 0.72], [-4.572, 3.8696, 0.338], [10.06, 3.7146, -0.03], [4.57, 3.5446, -0.294]],
    defaultEngineAudio: 'US_AC-130',
    flightProfileOverride: { orbitRadiusM: 195, minOrbitRadiusM: 140, maxOrbitRadiusM: 340, cruiseAltitudeM: 270 },
    attackBehaviors: ['SideOrbitGunship'],
    sourceRef: { cost: 320, speedKmh: 460, hp: 32, opticsM: 3200 },
  }),
  US_AC130J: makeRegistryEntry({
    slug: 'US_AC130J',
    name: 'US_AC130J',
    cnName: 'AC-130J 幽灵骑士炮艇',
    archetype: 'cas_gunship',
    size: [40.3349, 11.7156, 29.9954],
    exhausts: [[-10.01, 3.8046, -1.92], [-4.572, 3.8696, -2.302], [10.06, 3.7146, -2.67], [4.57, 3.5446, -2.934]],
    defaultEngineAudio: 'US_AC-130',
    flightProfileOverride: { orbitRadiusM: 195, minOrbitRadiusM: 140, maxOrbitRadiusM: 340, cruiseAltitudeM: 270 },
    attackBehaviors: ['SideOrbitGunship', 'StandoffMissile'],
    sourceRef: { cost: 360, speedKmh: 480, hp: 32, opticsM: 3500 },
  }),
  US_AC130U: makeRegistryEntry({
    slug: 'US_AC130U',
    name: 'US_AC130U',
    cnName: 'AC-130U 幽灵 II 炮艇',
    archetype: 'cas_gunship',
    size: [40.3349, 11.7156, 29.9954],
    exhausts: [[-10.01, 3.8046, -1.92], [-4.57, 3.6046, -2.3], [10.06, 3.7146, -2.67], [4.57, 3.5446, -2.934]],
    defaultEngineAudio: 'US_AC-130',
    flightProfileOverride: { orbitRadiusM: 195, minOrbitRadiusM: 140, maxOrbitRadiusM: 340, cruiseAltitudeM: 270 },
    attackBehaviors: ['SideOrbitGunship'],
    sourceRef: { cost: 340, speedKmh: 460, hp: 32, opticsM: 3400 },
  }),
  US_F15C: makeRegistryEntry({ slug: 'US_F15C', name: 'F-15C Eagle', cnName: 'F-15C 鹰式战斗机', archetype: 'supersonic_multirole', size: [13.0163, 4.6981, 19.1424], exhausts: [[-0.66, 2.2507, 7], [0.76, 2.2507, 6.94]], defaultEngineAudio: 'US_F14-15_Turbine_New' }),
  US_F15E: makeRegistryEntry({ slug: 'US_F15E', name: 'F-15E Strike Eagle', cnName: 'F-15E 攻击鹰战斗轰炸机', archetype: 'supersonic_multirole', size: [13.2165, 5.1338, 19.436], exhausts: [[-0.65, 2.3007, 6.55], [0.77, 2.3107, 6.55]], defaultEngineAudio: 'US_F14-15_Turbine_New' }),
  US_F15EX: makeRegistryEntry({ slug: 'US_F15EX', name: 'F-15EX Eagle II', cnName: 'F-15EX 鹰 II 战斗机', archetype: 'supersonic_multirole', size: [13.2165, 5.1687, 19.4359], exhausts: [[-0.65, 2.3007, 6.52], [0.77, 2.3107, 6.52]], defaultEngineAudio: 'US_F14-15_Turbine_New' }),
  US_F16CJ: makeRegistryEntry({ slug: 'US_F16CJ', name: 'F-16CJ', cnName: 'F-16CJ 野鼬鼠战机', archetype: 'supersonic_multirole', size: [10.2433, 4.8686, 15.1006], exhausts: [[0, 1.904, 4.93]], defaultEngineAudio: 'US_F16_Turbine_New' }),
  US_F16block52: makeRegistryEntry({ slug: 'US_F16block52', name: 'F-16C Fighting Falcon', cnName: 'F-16C 战隼战斗机', archetype: 'supersonic_multirole', size: [10.2433, 4.7108, 15.1006], exhausts: [[0, 1.904, 4.88]], defaultEngineAudio: 'US_F16_Turbine_New' }),
  US_F16block70: makeRegistryEntry({ slug: 'US_F16block70', name: 'F-16V Viper', cnName: 'F-16V 蝰蛇战斗机', archetype: 'supersonic_multirole', size: [10.2433, 4.7105, 15.1006], exhausts: [[0, 1.904, 4.86]], defaultEngineAudio: 'US_F16_Turbine_New' }),
  US_F22: makeRegistryEntry({ slug: 'US_F22', name: 'F-22 Raptor', cnName: 'F-22 猛禽隐身战斗机', archetype: 'supersonic_multirole', size: [13.2501, 4.7369, 18.8906], exhausts: [[-0.65, 2.199, 6.05], [0.699, 2.209, 6.083]], defaultEngineAudio: 'US_F35_Turbine_New' }),
  US_F35A: makeRegistryEntry({ slug: 'US_F35A', name: 'F-35A', cnName: 'F-35A 闪电 II 战机', archetype: 'supersonic_multirole', size: [10.6285, 3.4444, 15.6926], exhausts: [[0.02, 2.0421, 5.61]], defaultEngineAudio: 'US_F35_Turbine_New' }),
  US_F35B: makeRegistryEntry({ slug: 'US_F35B', name: 'F-35B', cnName: 'F-35B 垂直起降战机', archetype: 'supersonic_multirole', size: [10.513, 3.298, 15.5377], exhausts: [[0.02, 2.0421, 5.51]], defaultEngineAudio: 'US_F35_Turbine_New', attackBehaviors: ['BombingRun', 'StandoffMissile', 'VtolHover'] }),
  US_FA18C: makeRegistryEntry({ slug: 'US_FA18C', name: 'F/A-18C Hornet', cnName: 'F/A-18C 大黄蜂战机', archetype: 'supersonic_multirole', size: [11.9793, 3.9198, 17.0492], exhausts: [[-0.487, 1.8664, 7.4878], [0.497, 1.8534, 7.4878]], defaultEngineAudio: 'US_F18_Turbine_New' }),
  US_FA18D: makeRegistryEntry({ slug: 'US_FA18D', name: 'F/A-18D Hornet', cnName: 'F/A-18D 双座大黄蜂战机', archetype: 'supersonic_multirole', size: [11.9793, 3.7459, 17.048], exhausts: [[-0.54, 1.7891, 7.046], [0.57, 1.7491, 7.046]], defaultEngineAudio: 'US_F18_Turbine_New' }),
  US_B1B: makeRegistryEntry({ slug: 'US_B1B', name: 'B-1B Lancer', cnName: 'B-1B 枪骑兵轰炸机', archetype: 'strategic_stealth_bomber', size: [42.2873, 9.2234, 44.759], exhausts: [[-3.39, 2.2111, 9.41], [-4.89, 2.2211, 9.38], [3.4, 2.2611, 9.36], [4.93, 2.2211, 9.42]], defaultEngineAudio: 'US_B1B_Turbine', capabilities: { supersonic: true, afterburner: true, contrail: true, sonicBoom: true }, weaponsFactory: createB1BWeaponsSpec }),
  US_B2: makeRegistryEntry({ slug: 'US_B2', name: 'B-2 Spirit', cnName: 'B-2 幽灵隐身轰炸机', archetype: 'strategic_stealth_bomber', size: [52.4, 3.4615, 21.0301], exhausts: [[-4.05, 2.8891, 5.0353], [4.1, 2.9391, 4.9853]], defaultEngineAudio: 'US_B2_Turbine', capabilities: { supersonic: false, afterburner: false, contrail: true, sonicBoom: false } }),
  US_B52H: makeRegistryEntry({ slug: 'US_B52H', name: 'B-52H Stratofortress', cnName: 'B-52H 同温层堡垒轰炸机', archetype: 'strategic_stealth_bomber', size: [56.6141, 12.9405, 50.0803], exhausts: [[-17.9297, 2.5838, 1.7381], [-9.9297, 2.7838, -3.8619], [10.0703, 2.8038, -3.7719], [18.1303, 2.5938, 1.7281]], defaultEngineAudio: 'US_B52_Strato_Turbine', capabilities: { supersonic: false, afterburner: false, contrail: true, sonicBoom: false } }),
  US_F117: makeRegistryEntry({ slug: 'US_F117', name: 'F-117 Nighthawk', cnName: 'F-117 夜鹰隐身攻击机', archetype: 'strategic_stealth_bomber', size: [12.8985, 2.5184, 19.4317], exhausts: [[-1.372, 2.0167, 6.662], [1.402, 2.0057, 6.612]], defaultEngineAudio: 'US_F117_Turbine', capabilities: { supersonic: false, afterburner: false, contrail: false, sonicBoom: false } }),
  US_C17_GLOBEMASTER: makeRegistryEntry({ slug: 'US_C17_GLOBEMASTER', name: 'C-17 Globemaster', cnName: 'C-17 环球霸王运输机', archetype: 'heavy_support_ew', size: [52.0175, 16.9772, 53.4619], exhausts: [[-14.12, 3.8246, -7.06], [-7.63, 4.3246, -10.622], [14.1, 3.8046, -7.07], [7.57, 4.3846, -10.635]], defaultEngineAudio: 'US_C17_Turbine', capabilities: { contrail: true } }),
  US_C130_SuperHercules: makeRegistryEntry({ slug: 'US_C130_SuperHercules', name: 'C-130J-30 Hercules', cnName: 'C-130J-30 超级大力神运输机', archetype: 'heavy_support_ew', size: [40.7543, 11.5946, 34.2937], exhausts: [[-10.01, 3.8046, -0.32], [-4.57, 3.6046, -0.7], [10.06, 3.7146, -0.696], [4.57, 3.5446, -0.96]], defaultEngineAudio: 'US_AC-130' }),
  US_KC130_HarvestHawk: makeRegistryEntry({ slug: 'US_KC130_HarvestHawk', name: 'KC-130J Harvest Hawk', cnName: 'KC-130J 收获鹰加油/武装机', archetype: 'heavy_support_ew', size: [40.7543, 11.5946, 29.9421], exhausts: [[-10.01, 3.8046, 0.65], [-4.57, 3.6046, 0.27], [10.06, 3.7146, -0.1], [4.57, 3.5446, -0.364]], defaultEngineAudio: 'US_AC-130' }),
  US_MC130H: makeRegistryEntry({ slug: 'US_MC130H', name: 'MC-130H', cnName: 'MC-130H 作战利爪特种运输机', archetype: 'heavy_support_ew', size: [40.7543, 11.734, 30.7522], exhausts: [[-10.01, 3.8046, -0.03], [-4.57, 3.6046, -0.41], [10.06, 3.7146, -0.78], [4.57, 3.5446, -1.044]], defaultEngineAudio: 'US_AC-130' }),
  US_EA6B_Prowler: makeRegistryEntry({ slug: 'US_EA6B_Prowler', name: 'EA-6B Prowler', cnName: 'EA-6B 徘徊者电子战机', archetype: 'heavy_support_ew', size: [15.9799, 5.1881, 18.332], exhausts: [[-0.893, 2.3172, 1.4891], [0.891, 2.3272, 1.4991]], defaultEngineAudio: 'SFX_Harrier_A6_Turbine_New' }),
  US_MQ1B: makeRegistryEntry({ slug: 'US_MQ1B', name: 'MQ-1 Predator', cnName: 'MQ-1B 捕食者无人机', role: 'uav', archetype: 'fixed_wing_uav', size: [16.8745, 2.4487, 8.6296], exhausts: [], defaultEngineAudio: 'Light_Propeller_Drone', sourceRef: { cost: 130, opticsM: 3400 } }),
  US_MQ1C: makeRegistryEntry({ slug: 'US_MQ1C', name: 'MQ-1C Gray Eagle', cnName: 'MQ-1C 灰鹰无人机', role: 'uav', archetype: 'fixed_wing_uav', size: [17.0229, 2.4704, 9.5525], exhausts: [], defaultEngineAudio: 'Light_Propeller_Drone', sourceRef: { cost: 150, opticsM: 3600 } }),
  US_MQ9: makeRegistryEntry({ slug: 'US_MQ9', name: 'MQ-9 Reaper', cnName: 'MQ-9 死神无人机', role: 'uav', archetype: 'fixed_wing_uav', size: [20.1374, 2.4843, 8.901], exhausts: [], defaultEngineAudio: 'US_Drone_MQ9', sourceRef: { cost: 185, opticsM: 3800 } }),
  US_RQ170: makeRegistryEntry({ slug: 'US_RQ170', name: 'RQ-170 Sentinel', cnName: 'RQ-170 哨兵隐身无人机', role: 'uav', archetype: 'fixed_wing_uav', size: [11.9901, 0.9392, 4.5038], exhausts: [], defaultEngineAudio: 'US_Drone_GlobHaw', sourceRef: { cost: 210, opticsM: 4200, stealth: 2.0 } }),
  US_RQ4B: makeRegistryEntry({ slug: 'US_RQ4B', name: 'RQ-4 Global Hawk', cnName: 'RQ-4B 全球鹰高空侦察机', role: 'uav', archetype: 'fixed_wing_uav', size: [39.7729, 4.2245, 14.5729], exhausts: [[0, 3.123, 5.97]], defaultEngineAudio: 'US_Drone_GlobHaw', capabilities: { contrail: true }, sourceRef: { cost: 260, opticsM: 4800 } }),
  US_RQ7: makeRegistryEntry({ slug: 'US_RQ7', name: 'RQ-7 Shadow', cnName: 'RQ-7 影子战术无人机', role: 'uav', archetype: 'fixed_wing_uav', size: [4.1446, 0.941, 3.4003], exhausts: [], defaultEngineAudio: 'Light_Propeller_Drone', sourceRef: { cost: 90, opticsM: 2400 } }),
});

export function getFixedWingSpec(slug) {
  return FIXED_WING_REGISTRY[slug] || FIXED_WING_REGISTRY.US_A10;
}

/**
 * 纯数值：计算 rigBodyGroup 局部点变换至世界坐标。
 */
export function localRigToWorld(unit, localPos, out = { x: 0, y: 0, z: 0 }) {
  const lx = Number(localPos?.[0]) || 0;
  const ly = Number(localPos?.[1]) || 0;
  const lz = Number(localPos?.[2]) || 0;
  const rigGrp = unit?.rig?.rigBodyGroup;
  if (rigGrp && typeof rigGrp.localToWorld === 'function' && rigGrp.matrixWorld) {
    // 必须从外层 unit.node 起同步更新世界矩阵，避免刚移动或重置坐标后读取到上一帧旧 matrixWorld 造成偏移
    unit?.node?.updateMatrixWorld?.(true);
    if (!unit?.node?.updateMatrixWorld) {
      rigGrp.updateWorldMatrix?.(true, false);
    }
    const e = rigGrp.matrixWorld.elements;
    out.x = e[0] * lx + e[4] * ly + e[8] * lz + e[12];
    out.y = e[1] * lx + e[5] * ly + e[9] * lz + e[13];
    out.z = e[2] * lx + e[6] * ly + e[10] * lz + e[14];
    return out;
  }
  const yaw = unit?.node?.rotation?.y || 0;
  const rx = unit?.flightAttitude?.pitch || 0;
  const rz = unit?.flightAttitude?.roll || 0;
  const cz = Math.cos(rz), sz = Math.sin(rz);
  const x1 = lx * cz - ly * sz, y1 = lx * sz + ly * cz, z1 = lz;
  const cx = Math.cos(rx), sx = Math.sin(rx);
  const x2 = x1, y2 = y1 * cx - z1 * sx, z2 = y1 * sx + z1 * cx;
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  out.x = (unit?.node?.position?.x || 0) + x2 * cy + z2 * sy;
  out.y = (unit?.node?.position?.y || 0) + y2;
  out.z = (unit?.node?.position?.z || 0) - x2 * sy + z2 * cy;
  return out;
}

/**
 * 构建紧贴机身 rigBodyGroup 的外挂挂架实装弹体（支持随发射可见隐藏与 Live PiP 观察）。
 */
export function buildFixedWingPylonVisuals(THREE, unit, spec) {
  if (!THREE || !unit?.rig?.rigBodyGroup || !spec?.hardpoints?.pylons?.length) return null;
  const existing = unit.rig.rigBodyGroup.getObjectByName?.('fixed_wing_pylons_root');
  if (existing) {
    existing.userData?.disposePylons?.();
    existing.removeFromParent();
  }

  const root = new THREE.Group();
  root.name = 'fixed_wing_pylons_root';
  const outlineMat = new THREE.MeshBasicMaterial({ color: 0x161a1d, side: THREE.BackSide });
  const makeOutlined = (geo, mat) => {
    const g = new THREE.Group();
    const m = new THREE.Mesh(geo, mat);
    m.castShadow = true;
    const o = new THREE.Mesh(geo, outlineMat);
    o.scale.setScalar(1.09);
    o.userData.isStylizedOutline = true;
    o.castShadow = false;
    o.receiveShadow = false;
    o.renderOrder = -1;
    g.add(m, o);
    return g;
  };

  const matAgm = new THREE.MeshStandardMaterial({ color: 0xd9dde0, roughness: 0.55, metalness: 0.15 });
  const matSeeker = new THREE.MeshStandardMaterial({ color: 0x1a1e22, roughness: 0.25, metalness: 0.6 });
  const matBomb = new THREE.MeshStandardMaterial({ color: 0x4b5542, roughness: 0.68, metalness: 0.2 });
  const matYellowBand = new THREE.MeshStandardMaterial({ color: 0xe3b838, roughness: 0.45, metalness: 0.1 });
  const matPod = new THREE.MeshStandardMaterial({ color: 0x626b70, roughness: 0.6, metalness: 0.35 });
  const matEcm = new THREE.MeshStandardMaterial({ color: 0x525b61, roughness: 0.55, metalness: 0.3 });

  const agmBodyGeo = new THREE.CylinderGeometry(0.14, 0.15, 2.35, 8); agmBodyGeo.rotateX(Math.PI / 2);
  const agmNoseGeo = new THREE.SphereGeometry(0.14, 8, 6);
  const agmFinGeo = new THREE.BoxGeometry(0.72, 0.04, 0.55);
  const bombBodyGeo = new THREE.CylinderGeometry(0.21, 0.22, 2.75, 8); bombBodyGeo.rotateX(Math.PI / 2);
  const bombNoseGeo = new THREE.ConeGeometry(0.21, 0.72, 8); bombNoseGeo.rotateX(Math.PI / 2);
  const bombBandGeo = new THREE.CylinderGeometry(0.228, 0.228, 0.16, 8); bombBandGeo.rotateX(Math.PI / 2);
  const bombTailGeo = new THREE.BoxGeometry(0.68, 0.04, 0.52);
  const podGeo = new THREE.CylinderGeometry(0.23, 0.23, 1.85, 10); podGeo.rotateX(Math.PI / 2);
  const ecmGeo = new THREE.BoxGeometry(0.42, 0.36, 2.45);

  const itemsByIndex = new Map();
  for (const p of spec.hardpoints.pylons) {
    const holder = new THREE.Group();
    holder.name = `pylon_${p.index}_${p.ordnanceType}`;
    holder.position.set(p.pos[0], p.pos[1], p.pos[2]);
    if (p.ordnanceType === 'agm65') {
      const body = makeOutlined(agmBodyGeo, matAgm);
      const nose = new THREE.Mesh(agmNoseGeo, matSeeker); nose.position.z = 1.18;
      const finH = new THREE.Mesh(agmFinGeo, matAgm); finH.position.z = -0.78;
      const finV = new THREE.Mesh(agmFinGeo, matAgm); finV.rotation.z = Math.PI / 2; finV.position.z = -0.78;
      holder.add(body, nose, finH, finV);
    } else if (p.ordnanceType === 'gbu31') {
      const body = makeOutlined(bombBodyGeo, matBomb);
      const nose = makeOutlined(bombNoseGeo, matBomb); nose.position.z = 1.72;
      const band = new THREE.Mesh(bombBandGeo, matYellowBand); band.position.z = 0.85;
      const finH = new THREE.Mesh(bombTailGeo, matBomb); finH.position.z = -1.15;
      const finV = new THREE.Mesh(bombTailGeo, matBomb); finV.rotation.z = Math.PI / 2; finV.position.z = -1.15;
      holder.add(body, nose, band, finH, finV);
    } else if (p.ordnanceType === 'rocket_pod') {
      const pod = makeOutlined(podGeo, matPod);
      const cap = new THREE.Mesh(agmNoseGeo, matSeeker); cap.scale.set(1.4, 1.4, 0.3); cap.position.z = 0.93;
      holder.add(pod, cap);
    } else {
      holder.add(makeOutlined(ecmGeo, matEcm));
    }
    root.add(holder);
    itemsByIndex.set(p.index, holder);
  }
  unit.rig.rigBodyGroup.add(root);
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    root.removeFromParent();
    for (const g of [agmBodyGeo, agmNoseGeo, agmFinGeo, bombBodyGeo, bombNoseGeo, bombBandGeo, bombTailGeo, podGeo, ecmGeo]) g.dispose();
    for (const m of [outlineMat, matAgm, matSeeker, matBomb, matYellowBand, matPod, matEcm]) m.dispose();
  };
  root.userData.disposePylons = dispose;
  return {
    root,
    itemsByIndex,
    setPylonVisible(index, visible) {
      const node = itemsByIndex.get(index);
      if (node) node.visible = Boolean(visible);
    },
    resetAllVisible() {
      for (const node of itemsByIndex.values()) node.visible = true;
    },
    dispose,
  };
}

/**
 * 为固定翼机身材质注入零 PointLight、零额外 DrawCall 的「发射时机首局部暖光照」：
 * - 仅照亮距离机炮口 worldPos 8.5m 半径内的机头雷达罩/前风挡下缘/前机腹，机尾与地面绝不误照；
 * - 同机型多架飞机自动克隆材质避免互相串光，`detachUnit` 时完整回收克隆材质。
 */
export function installFixedWingMuzzleGlow(THREE, unit) {
  if (!THREE || !unit?.rig?.rigBodyGroup) return null;
  const uniform = { value: new THREE.Vector4(0, 0, 0, 0) };
  const clonedBySrc = new Map();
  const meshBackups = [];

  const patchMaterial = (srcMat) => {
    if (!srcMat || !(srcMat.isMeshStandardMaterial || srcMat.isMeshPhysicalMaterial || srcMat.isMeshLambertMaterial || srcMat.isMeshPhongMaterial)) {
      return srcMat;
    }
    if (clonedBySrc.has(srcMat)) return clonedBySrc.get(srcMat);
    const mat = srcMat.clone();
    const prevOnBefore = mat.onBeforeCompile;
    const prevCacheKey = typeof mat.customProgramCacheKey === 'function' ? mat.customProgramCacheKey.bind(mat) : null;
    mat.customProgramCacheKey = () => `${prevCacheKey ? prevCacheKey() : ''}|scv_fw_muzzle_glow_v1`;
    mat.onBeforeCompile = (shader, renderer) => {
      if (typeof prevOnBefore === 'function') prevOnBefore(shader, renderer);
      shader.uniforms.uScvMuzzleGlow = uniform;
      if (!shader.vertexShader.includes('vScvMuzzleWPos')) {
        shader.vertexShader = shader.vertexShader
          .replace(
            '#include <common>',
            '#include <common>\nvarying vec3 vScvMuzzleWPos;',
          )
          .replace(
            '#include <begin_vertex>',
            '#include <begin_vertex>\nvScvMuzzleWPos = (modelMatrix * vec4(transformed, 1.0)).xyz;',
          );
      }
      if (!shader.fragmentShader.includes('uScvMuzzleGlow')) {
        shader.fragmentShader = shader.fragmentShader
          .replace(
            '#include <common>',
            '#include <common>\nuniform vec4 uScvMuzzleGlow;\nvarying vec3 vScvMuzzleWPos;',
          )
          .replace(
            '#include <emissivemap_fragment>',
            `#include <emissivemap_fragment>
if (uScvMuzzleGlow.w > 0.004) {
  float dMuz = length(uScvMuzzleGlow.xyz - vScvMuzzleWPos);
  float muzFall = pow(clamp(1.0 - dMuz / 8.5, 0.0, 1.0), 1.65);
  totalEmissiveRadiance += vec3(1.55, 0.80, 0.26) * (muzFall * uScvMuzzleGlow.w);
}`,
          );
      }
    };
    mat.needsUpdate = true;
    clonedBySrc.set(srcMat, mat);
    return mat;
  };

  unit.rig.rigBodyGroup.traverse((obj) => {
    if (!obj?.isMesh || obj.userData?.isStylizedOutline || !obj.material) return;
    if (Array.isArray(obj.material)) {
      const origArr = [...obj.material];
      const nextArr = origArr.map(patchMaterial);
      if (nextArr.some((m, i) => m !== origArr[i])) {
        meshBackups.push({ mesh: obj, orig: origArr });
        obj.material = nextArr;
      }
    } else {
      const orig = obj.material;
      const next = patchMaterial(orig);
      if (next !== orig) {
        meshBackups.push({ mesh: obj, orig });
        obj.material = next;
      }
    }
  });

  if (clonedBySrc.size === 0) return null;
  let disposed = false;
  return {
    uniform,
    pulse(worldPos, intensity = 1.0) {
      if (disposed || !worldPos) return;
      uniform.value.set(
        Number(worldPos.x) || 0,
        Number(worldPos.y) || 0,
        Number(worldPos.z) || 0,
        clamp(Number(intensity) || 0, 0, 1.4),
      );
    },
    decay(dt, worldPos = null) {
      if (disposed || uniform.value.w <= 0) return;
      const nextW = uniform.value.w * Math.exp(-26.0 * Math.max(0, dt));
      if (nextW < 0.004) {
        uniform.value.w = 0;
        return;
      }
      if (worldPos) {
        uniform.value.x = Number(worldPos.x) || 0;
        uniform.value.y = Number(worldPos.y) || 0;
        uniform.value.z = Number(worldPos.z) || 0;
      }
      uniform.value.w = nextW;
    },
    reset() {
      uniform.value.w = 0;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      uniform.value.w = 0;
      for (const b of meshBackups) {
        b.mesh.material = b.orig;
      }
      meshBackups.length = 0;
      for (const m of clonedBySrc.values()) {
        m.dispose?.();
      }
      clonedBySrc.clear();
    },
  };
}

/**
 * 固定翼通用底座控制器 V2：
 * - 零瞬移真实《断箭》入航与拉弧攻击（可选调试页 `fastApproachMode` 快捷入航）；
 * - 独立武器开火窗口、拖线方向精确对齐、Hydra 70 火箭齐射、自定义盘旋半径与高低空切换；
 * - 实时输出 `getPredictedFlightPath` 供高清 2D 标绘层画出预测航线与 ETA 倒计时。
 */
export class FixedWingBaseController {
  constructor({ THREE = null, defaultTargetPos = { x: -10, z: 36 }, autoRtbOnWinchester = true } = {}) {
    this.THREE = THREE;
    this.defaultTargetPos = { x: Number(defaultTargetPos?.x) || 0, z: Number(defaultTargetPos?.z) || 36 };
    this.fastApproachMode = false; // 4A：默认严格零瞬移，仅调试页显式开启时拉近入航点
    this.fuelConsumptionPaused = false;
    this.autoRtbOnWinchester = Boolean(autoRtbOnWinchester);
    this.tracerColorMode = 'red';
    this.units = new Map();
    this.rearmCooldowns = new Map();
    this._lastAudioBridge = null;
    this._tmpVecA = { x: 0, y: 0, z: 0 };
    this._tmpVecB = { x: 0, y: 0, z: 0 };
    this._tmpVecC = { x: 0, y: 0, z: 0 };
    this._tmpPos = { x: 0, y: 0, z: 0 };
    this._tmpVel = { x: 0, y: 0, z: 0 };
    this._tmpExhaustDir = { x: 0, y: 0, z: -1 };
    this._tmpLocalOrigin = [0, 0, 0];
    this._tmpEjectOrigin = [0, 3.1, 3.8];
    this._tmpEjectVel = { x: 0, y: 24, z: 0 };
    this._tmpSkidDir = { x: 1, z: 0 };
  }

  setAutoRtbOnWinchester(enabled) {
    this.autoRtbOnWinchester = Boolean(enabled);
    return this.autoRtbOnWinchester;
  }

  isAutoRtbOnWinchester() {
    return Boolean(this.autoRtbOnWinchester);
  }

  /**
   * 登记机型离场后的机场整备冷却（补油补弹，30–45s，按原型族经 GLOBAL_BALANCE_SCALES 派生）。
   */
  registerRearmCooldown(slug, simNowSec, durationSec = null, meta = null) {
    if (!slug) return null;
    const spec = getFixedWingSpec(slug);
    const dur = Math.max(5, Number.isFinite(durationSec) ? durationSec : (spec.gameStats?.rearmCooldownSec || 35));
    const now = Number.isFinite(simNowSec) ? simNowSec : 0;
    const entry = {
      slug,
      name: meta?.name || spec.name,
      shortName: meta?.shortName || spec.name,
      totalSec: dur,
      startedAtSec: now,
      readyAtSec: now + dur,
    };
    this.rearmCooldowns.set(slug, entry);
    return entry;
  }

  getRearmCooldown(slug, simNowSec = 0) {
    if (!slug) return null;
    const entry = this.rearmCooldowns.get(slug);
    if (!entry) return null;
    const now = Number.isFinite(simNowSec) ? simNowSec : 0;
    const remainSec = Math.max(0, entry.readyAtSec - now);
    if (remainSec <= 0.05) {
      this.rearmCooldowns.delete(slug);
      return null;
    }
    return {
      slug: entry.slug,
      name: entry.name,
      shortName: entry.shortName,
      totalSec: entry.totalSec,
      remainSec: Number(remainSec.toFixed(1)),
      progressPct: Math.round(((entry.totalSec - remainSec) / Math.max(1, entry.totalSec)) * 100),
    };
  }

  pruneRearmCooldowns(simNowSec = 0) {
    const completed = [];
    const now = Number.isFinite(simNowSec) ? simNowSec : 0;
    for (const [slug, entry] of this.rearmCooldowns.entries()) {
      if (entry.readyAtSec <= now + 0.05) {
        this.rearmCooldowns.delete(slug);
        completed.push(entry);
      }
    }
    return completed;
  }

  /**
   * 动态计算距 9km 战场最近边界的返航余油红线（钳位 10%–18%，含 25% 转向爬升安全余量）。
   */
  computeDynamicBingoThresholdPct(st) {
    if (!st) return 14;
    const pos = st.unit?.node?.position || { x: st.orbitCenter?.x || 0, z: st.orbitCenter?.z || 0 };
    const distEdge = Math.max(0, BATTLEFIELD_EXIT_HALF_EXTENT - Math.max(Math.abs(pos.x), Math.abs(pos.z)));
    const cruiseSpd = st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps || 90;
    const attackSpd = st.spec.gameStats?.attackSpeedMps || st.arch.attackSpeedMps || 120;
    const exitSpd = Math.max(40, attackSpd * 1.15, cruiseSpd * 1.38);
    const rates = st.spec.gameStats?.fuelBurnRates || { cruise: 1.0, transit: 1.2, attack: 1.45, sprint: 1.45, afterburner: 1.85 };
    const rtbRate = st.spec.capabilities?.afterburner ? (rates.afterburner || 1.85) : (rates.sprint || 1.45);
    const rtbNeedFuelSec = (distEdge / exitSpd) * rtbRate * 1.25;
    const fuelMaxSec = Math.max(45, st.fuelMaxSec || st.spec.gameStats?.fuelMaxSec || 480);
    return Number(clamp((rtbNeedFuelSec / fuelMaxSec) * 100, 10, 18).toFixed(1));
  }

  /**
   * 按 Q7-A 方案B 工况分档核算当前油耗倍率、推力档位与加力状态（SCAV-6 与 SCAV-7 共享单源）。
   */
  resolveFuelBurnState(st) {
    const rates = st.spec.gameStats?.fuelBurnRates || { cruise: 1.0, transit: 1.2, attack: 1.45, sprint: 1.45, afterburner: 1.85 };
    const fs = st.flightState;
    const hasAb = Boolean(st.spec.capabilities?.afterburner);
    if (fs === 'evacuated' || fs === 'crashed' || fs === 'crashing') {
      return { burnRate: 0, throttleLevel: 0, afterburnerActive: false, modeLabel: '关车' };
    }
    if (fs === 'rtb' || fs === 'sprint') {
      const abOn = hasAb;
      return {
        burnRate: abOn ? (rates.afterburner || 1.85) : (rates.sprint || 1.45),
        throttleLevel: 1.0,
        afterburnerActive: abOn,
        modeLabel: abOn ? '加力' : '满推',
      };
    }
    if (fs === 'strafe' || fs === 'rocket' || fs === 'bomb' || fs === 'missile' || fs === 'egress') {
      const strikePhase = st.activeStrike?.phase;
      if (fs !== 'egress' && (strikePhase === 'transit' || strikePhase === 'align')) {
        return {
          burnRate: rates.transit || 1.2,
          throttleLevel: 0.7,
          afterburnerActive: false,
          modeLabel: '入航',
        };
      }
      const abOn = hasAb && (fs === 'egress' || strikePhase === 'run');
      return {
        burnRate: abOn ? (rates.afterburner || 1.85) : (rates.attack || 1.45),
        throttleLevel: abOn ? 1.0 : 0.85,
        afterburnerActive: abOn,
        modeLabel: abOn ? '加力突击' : '突击',
      };
    }
    return {
      burnRate: rates.cruise || 1.0,
      throttleLevel: 0.55,
      afterburnerActive: false,
      modeLabel: '巡航',
    };
  }

  /**
   * 调试专用：冻结或恢复固定翼燃油消耗（冻结时手动设定低油/Bingo 仍立即生效）。
   */
  setFuelConsumptionPaused(paused) {
    this.fuelConsumptionPaused = Boolean(paused);
    return this.fuelConsumptionPaused;
  }

  isFuelConsumptionPaused() {
    return Boolean(this.fuelConsumptionPaused);
  }

  /**
   * 调试/单元测试专用：直接设定在场固定翼的剩余燃油比例（0.0–1.0）。
   */
  setFuelRatio(unitOrId, ratio = 1.0) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '未找到单位' };
    const clamped = clamp(Number(ratio) || 0, 0, 1);
    st.fuelRemainSec = Number((st.fuelMaxSec * clamped).toFixed(2));
    st.fuelPct = Number((clamped * 100).toFixed(1));
    st.bingoThresholdPct = this.computeDynamicBingoThresholdPct(st);
    if (clamped > (st.bingoThresholdPct + 12) / 100) {
      st.bingoState = 'nominal';
      st.lowFuelNotified = false;
      st.bingoAutoTriggered = false;
      st.bingoLockedRtb = false;
    } else if (clamped > st.bingoThresholdPct / 100) {
      st.bingoState = 'low';
      st.bingoAutoTriggered = false;
      st.bingoLockedRtb = false;
    } else {
      st.bingoState = st.flightState === 'rtb' ? 'rtb_bingo' : 'bingo';
    }
    this._syncUnitAttitude(st);
    return {
      ok: true,
      fuelPct: st.fuelPct,
      fuelRemainSec: st.fuelRemainSec,
      bingoThresholdPct: st.bingoThresholdPct,
      bingoState: st.bingoState,
    };
  }

  setFastApproachMode(enabled) {
    this.fastApproachMode = Boolean(enabled);
    return this.fastApproachMode;
  }

  setTracerColorMode(mode) {
    this.tracerColorMode = ['red', 'green', 'mixed', 'none', 'auto'].includes(mode) ? mode : 'red';
    return this.tracerColorMode;
  }

  getTracerColorMode() {
    return this.tracerColorMode;
  }

  attachUnit(unit, { orbitCenter = null, THREE = null } = {}) {
    if (!unit) return null;
    if (this.units.has(unit.id)) {
      this.detachUnit(unit.id);
    }
    const T = THREE || this.THREE;
    const spec = getFixedWingSpec(unit.slug);
    const fp = spec.flightProfile;
    const oc = orbitCenter || { x: this.defaultTargetPos.x, z: this.defaultTargetPos.z };

    const weapons = spec.createWeapons().map(w => ({
      ...w,
      pylonIndices: [...w.pylonIndices],
      visiblePylonIndices: [...w.pylonIndices],
      pylonCycleCursor: 0,
      cooldownRemaining: 0,
    }));

    const pylonVisuals = T ? buildFixedWingPylonVisuals(T, unit, spec) : null;
    const muzzleGlow = T ? installFixedWingMuzzleGlow(T, unit) : null;

    unit.hp = spec.gameStats.hp;
    unit.maxHp = spec.gameStats.hp;
    unit.usesFuelConstraint = true;
    unit.orbitRadius = fp.orbitRadiusM;
    unit.weaponRangeM = weapons.reduce((m, w) => Math.max(m, w.maxRangeM || 0), 0);

    const initHighAlt = spec.gameStats?.highAltitudeM || fp.cruiseAltitudeM;
    const fuelMaxSec = Math.max(45, spec.gameStats?.fuelMaxSec || 480);
    const st = {
      unit,
      spec,
      arch: fp,
      flightState: 'orbit',
      altitudeMode: 'high', // 'low' | 'high'
      orbitAutoFire: false, // 固定翼取消自动开火模式，默认且仅支持手操精准打击
      manualFireOnly: true,
      orbitCenter: { x: oc.x, z: oc.z },
      orbitRadius: fp.orbitRadiusM,
      orbitAltitude: initHighAlt,
      orbitPhase: 0.4,
      speedMps: spec.gameStats?.cruiseSpeedMps || fp.cruiseSpeedMps,
      pitchDeg: fp.cruiseAoaDeg ?? 2.8,
      rollDeg: -28,
      yawRate: -0.42,
      heaveY: 0,
      turbulenceSeed: (this.units.size + 1) * 1.618,
      egressTurnSign: -1,
      strafeRecoilActive: false,
      evacuated: false,
      rtbTarget: null,
      rtbHeading: 0,
      rtbTimer: 0,
      rtbReason: 'manual',
      fuelMaxSec,
      fuelRemainSec: fuelMaxSec,
      fuelPct: 100,
      fuelBurnRate: 1.0,
      fuelModeLabel: '巡航',
      bingoThresholdPct: 14,
      bingoState: 'nominal', // 'nominal' | 'low' | 'bingo' | 'rtb_bingo'
      bingoAutoTriggered: false,
      bingoLockedRtb: false,
      lowFuelNotified: false,
      winchesterState: 'nominal', // 'nominal' | 'winchester' | 'rtb_winchester'
      winchesterNotified: false,
      winchesterAutoRtbTriggered: false,
      winchesterRecalled: false,
      ejectionTriggered: false,
      throttleLevel: 0.55,
      afterburnerActive: false,
      targetPos: { x: oc.x, y: 0, z: oc.z },
      activeStrike: null, // { cmd, weaponId, startPos, endPos, targetPos, attackDir, entryPoint, releasePoint, phase: 'transit'|'align'|'run' }
      strikeQueue: [],
      runPhase: 'align',
      runTimer: 0,
      egressTimer: 0,
      strafeFireTimer: 0,
      strafeRoundsFired: 0,
      strafeBurstTick: 0,
      strafeTracerSeq: 0,
      rocketFireTimer: 0,
      rocketsFiredInRun: 0,
      strafeAudioActive: false,
      strafeBurstId: 0,
      lastAudioBridge: null,
      bombDroppedInRun: false,
      bombDropIntervalTimer: 0,
      bombPostDropTimer: 0,
      missileFiredInRun: false,
      missileFireTimer: 0,
      missilePostFireTimer: 0,
      flareQueue: [],
      flareBurstCount: 0,
      damagedSmokeActive: false,
      damageSmokeTimer: 0,
      wreckBurnAge: 0,
      lastCrashSmokePos: null,
      crashSpinDeg: 0,
      crashAudioStarted: false,
      crashed: false,
      wingtipVortexActive: false,
      wingtipFadeAlpha: 0,
      lastSonicBoomSec: -999,
      weapons,
      pylonVisuals,
      muzzleGlow,
      projectiles: [],
      lastEvents: [],
    };

    const startX = oc.x + Math.cos(st.orbitPhase) * st.orbitRadius;
    const startZ = oc.z + Math.sin(st.orbitPhase) * st.orbitRadius;
    const startYaw = Math.atan2(-Math.sin(st.orbitPhase), Math.cos(st.orbitPhase));
    if (unit.node?.position) {
      unit.node.position.set(startX, st.orbitAltitude, startZ);
      unit.node.rotation.y = startYaw;
    }
    unit.hp = spec.gameStats.hp;
    unit.maxHp = spec.gameStats.hp;
    unit.orbitCenter = { x: oc.x, z: oc.z };
    unit.orbitRadius = st.orbitRadius;
    unit.orbitRadiusRange = [fp.minOrbitRadiusM, fp.maxOrbitRadiusM];
    unit.orbitPhase = st.orbitPhase;
    unit.evacuated = false;
    unit.usesFuelConstraint = true;
    unit.roe = 'manual';
    unit.manualFireOnly = true;
    unit.fixedWingController = this;
    unit.fixedWingSpec = spec;
    st.bingoThresholdPct = this.computeDynamicBingoThresholdPct(st);
    this._syncUnitWeaponSummary(st);
    this._syncUnitAttitude(st);

    this.units.set(unit.id, st);
    return st;
  }

  _resolveAudioBridge(st, audioBridge = null) {
    if (audioBridge) {
      this._lastAudioBridge = audioBridge;
      if (st) st.lastAudioBridge = audioBridge;
      return audioBridge;
    }
    return st?.lastAudioBridge || this._lastAudioBridge || null;
  }

  _stopStrafeAudioIfNeeded(st, audioBridge = null) {
    if (!st || !st.strafeAudioActive) return;
    const bridge = this._resolveAudioBridge(st, audioBridge);
    bridge?.stopGau8Burst?.(st.unit.id);
    st.strafeAudioActive = false;
  }

  getUnitState(unitOrId) {
    const id = typeof unitOrId === 'string' ? unitOrId : unitOrId?.id;
    return this.units.get(id) || null;
  }

  detachUnit(unitOrId, { audioBridge = null, vfxEngine = null } = {}) {
    const id = typeof unitOrId === 'string' ? unitOrId : unitOrId?.id;
    const st = this.units.get(id);
    if (!st) return;
    const bridge = this._resolveAudioBridge(st, audioBridge);
    this._stopStrafeAudioIfNeeded(st, bridge);
    bridge?.stopAfterburnerAudio?.(id, { immediate: true });
    if (st.crashAudioStarted) {
      bridge?.stopFallingLoop?.(id);
      st.crashAudioStarted = false;
    }
    for (let i = 0; i < st.projectiles.length; i += 1) {
      const p = st.projectiles[i];
      if (p?.kind === 'gbu31') bridge?.stopOrdnanceWhistle?.(p.id, { immediate: true });
    }
    st.wingtipVortexActive = false;
    st.wingtipFadeAlpha = 0;
    vfxEngine?.fadeWingtipVortex?.(id, 0);
    st.pylonVisuals?.dispose?.();
    st.muzzleGlow?.dispose?.();
    st.projectiles.length = 0;
    if (st.unit) {
      delete st.unit.fixedWingController;
      delete st.unit.flightAttitude;
    }
    this.units.delete(id);
  }

  /**
   * 5A：动态设置固定翼盘旋圆心与半径（严格钳位在每机物理 [minOrbitRadiusM, maxOrbitRadiusM] 区间）。
   * 距离目标盘旋圈较远时（> orbitRadius + 120m）自动进入最高速度直飞 `sprint`，抵达切点后平顺降速进入盘旋。
   */
  setOrbitZone(unitOrId, center, radius = null, { sprint = 'auto', audioBridge = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '未找到单位' };
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已撤离战区，请先重新进场' };
    }
    if (st.bingoLockedRtb) {
      return { ok: false, bingoLocked: true, reason: '低油红线（BINGO FUEL）强制返航整备中，无法变更航线' };
    }
    const bridge = this._resolveAudioBridge(st, audioBridge);
    if (center && Number.isFinite(center.x) && Number.isFinite(center.z)) {
      st.orbitCenter = { x: center.x, z: center.z };
      st.unit.orbitCenter = { x: center.x, z: center.z };
    }
    if (Number.isFinite(radius)) {
      const clampedR = clamp(radius, st.arch.minOrbitRadiusM, st.arch.maxOrbitRadiusM);
      st.orbitRadius = clampedR;
      st.unit.orbitRadius = clampedR;
    }
    this._stopStrafeAudioIfNeeded(st, bridge);
    if (['approach', 'strafe', 'bomb', 'missile', 'rocket', 'sprint', 'rtb'].includes(st.flightState)) {
      if (st.flightState === 'rtb' && st.rtbReason === 'winchester') {
        st.winchesterRecalled = true;
        st.winchesterState = 'winchester';
        st.rtbReason = 'manual';
      }
      st.activeStrike = null;
      st.strikeQueue.length = 0;
      st.rtbTarget = null;
      st.rtbTimer = 0;
      st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
    }
    const pos = st.unit.node?.position;
    let enteredSprint = false;
    if (pos && !st.crashed && st.flightState !== 'crashing') {
      const distCenter = Math.hypot(pos.x - st.orbitCenter.x, pos.z - st.orbitCenter.z);
      const wantSprint = sprint === true || (sprint === 'auto' && distCenter > st.orbitRadius + 120);
      if (wantSprint && distCenter > st.orbitRadius + 24) {
        st.flightState = 'sprint';
        st.runTimer = 0;
        st.sprintEntryPoint = computeOrbitEntryPoint(pos, st.orbitCenter, st.orbitRadius);
        enteredSprint = true;
      } else {
        st.sprintEntryPoint = null;
      }
    }
    this._syncUnitAttitude(st);
    return {
      ok: true,
      flightState: st.flightState,
      sprinting: enteredSprint,
      orbitCenter: { ...st.orbitCenter },
      orbitRadius: st.orbitRadius,
      minRadius: st.arch.minOrbitRadiusM,
      maxRadius: st.arch.maxOrbitRadiusM,
    };
  }

  /**
   * 显式下达最高速度直飞指令：以机型最大极速沿直线飞往目标盘旋圈切点，抵达后自动平滑降速切入标准盘旋。
   */
  issueDirectSprint(unitOrId, targetPos, { orbitRadius = null, audioBridge = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '未找到单位' };
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已撤离战区，请先重新进场' };
    }
    if (!targetPos || !Number.isFinite(targetPos.x) || !Number.isFinite(targetPos.z)) {
      return { ok: false, reason: '无效直飞目的地' };
    }
    return this.setOrbitZone(unitOrId, targetPos, orbitRadius, { sprint: true, audioBridge });
  }

  /**
   * 固定翼紧急/主动撤离战区（RTB）：中止当前打击与排队、抛洒防御热诱弹、加速爬升并沿最短边线飞离 9km 战场边界。
   * 飞离边界前可随时通过 `cancelEvacuate`、右键重设盘旋圈、按 `X` 或新打击指令召回。
   */
  issueEvacuate(unitOrId, { simNowSec = 0, audioBridge = null, reason = 'manual', forceLocked = false } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '单位未绑定固定翼底座' };
    if (st.crashed || st.flightState === 'crashing') {
      return { ok: false, reason: '坠毁战机无法撤离' };
    }
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已撤离战区' };
    }
    if (st.flightState === 'rtb') {
      if (forceLocked) {
        st.bingoLockedRtb = true;
        st.rtbReason = reason || 'bingo_fuel';
        st.bingoState = 'rtb_bingo';
        this._syncUnitAttitude(st);
      }
      return {
        ok: true,
        alreadyRtb: true,
        flightState: 'rtb',
        bingoLocked: Boolean(st.bingoLockedRtb),
        rtbTarget: st.rtbTarget ? { ...st.rtbTarget } : null,
        etaSec: 0,
      };
    }
    const bridge = this._resolveAudioBridge(st, audioBridge);
    this._stopStrafeAudioIfNeeded(st, bridge);
    st.activeStrike = null;
    st.strikeQueue.length = 0;
    st.sprintEntryPoint = null;
    st.strafeRecoilActive = false;

    const pos = st.unit.node?.position || { x: st.orbitCenter.x, y: st.orbitAltitude, z: st.orbitCenter.z };
    const yaw = st.unit.node?.rotation?.y || 0;
    const fwdX = Math.sin(yaw);
    const fwdZ = Math.cos(yaw);
    const B = BATTLEFIELD_EXIT_TARGET_EXTENT;
    const candidates = [
      { x: B, z: clamp(pos.z + fwdZ * 320, -B * 0.85, B * 0.85) },
      { x: -B, z: clamp(pos.z + fwdZ * 320, -B * 0.85, B * 0.85) },
      { x: clamp(pos.x + fwdX * 320, -B * 0.85, B * 0.85), z: B },
      { x: clamp(pos.x + fwdX * 320, -B * 0.85, B * 0.85), z: -B },
    ];
    let best = candidates[0];
    let bestScore = Infinity;
    for (const c of candidates) {
      const dx = c.x - pos.x;
      const dz = c.z - pos.z;
      const dist = Math.hypot(dx, dz);
      const dirDot = dist > 1 ? ((dx / dist) * fwdX + (dz / dist) * fwdZ) : 0;
      // 距离为主，同时轻微偏向机头前半球避免在地图中心附近无谓 180° 急掉头
      const score = dist - dirDot * 420;
      if (score < bestScore) {
        bestScore = score;
        best = c;
      }
    }

    st.rtbTarget = { x: best.x, z: best.z };
    st.rtbHeading = Math.atan2(best.x - pos.x, best.z - pos.z);
    st.rtbTimer = 0;
    st.rtbReason = reason || 'manual';
    if (forceLocked || reason === 'bingo_fuel') {
      st.bingoLockedRtb = true;
      st.bingoAutoTriggered = true;
      st.bingoState = 'rtb_bingo';
    }
    st.evacuated = false;
    st.flightState = 'rtb';

    this._queueFlareBurst(st, simNowSec, 4);
    const origin = localRigToWorld(st.unit, st.spec.hardpoints.flareOrigin, this._tmpVecA);
    bridge?.scheduleWorldEvent?.('BA_Chaff_Trap', origin, { gain: 0.65, powerTier: 'small' });

    const cruiseSpd = st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps;
    const attackSpd = st.spec.gameStats?.attackSpeedMps || st.arch.attackSpeedMps;
    const exitSpd = Math.max(attackSpd * 1.15, cruiseSpd * 1.38);
    const distExit = Math.hypot(best.x - pos.x, best.z - pos.z);
    const etaSec = Number((distExit / Math.max(40, exitSpd)).toFixed(1));
    this._syncUnitAttitude(st);
    return {
      ok: true,
      flightState: 'rtb',
      rtbTarget: { ...st.rtbTarget },
      etaSec,
    };
  }

  /**
   * 飞离战场边界前取消撤离，恢复当前空域盘旋。
   */
  cancelEvacuate(unitOrId, { audioBridge = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '单位未绑定固定翼底座' };
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已飞离战场，无法召回' };
    }
    if (st.bingoLockedRtb) {
      return { ok: false, bingoLocked: true, reason: '燃油触及返航红线（BINGO FUEL），强制返航整备中无法召回' };
    }
    const bridge = this._resolveAudioBridge(st, audioBridge);
    this._stopStrafeAudioIfNeeded(st, bridge);
    if (st.rtbReason === 'winchester') {
      st.winchesterRecalled = true;
      st.winchesterState = 'winchester';
    }
    st.rtbReason = 'manual';
    st.rtbTarget = null;
    st.rtbTimer = 0;
    st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
    const pos = st.unit.node?.position;
    if (pos) {
      st.orbitPhase = Math.atan2(pos.z - st.orbitCenter.z, pos.x - st.orbitCenter.x);
      st.unit.orbitPhase = st.orbitPhase;
    }
    this._syncUnitAttitude(st);
    return { ok: true, flightState: st.flightState, cancelledRtb: true };
  }

  toggleAltitudeMode(unitOrId) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false };
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已撤离战区，请先重新进场' };
    }
    st.altitudeMode = st.altitudeMode === 'low' ? 'high' : 'low';
    st.orbitAltitude = st.altitudeMode === 'low' ? st.arch.lowAltitudeM : st.arch.highAltitudeM;
    return { ok: true, altitudeMode: st.altitudeMode, targetAltitudeM: st.orbitAltitude };
  }

  toggleOrbitAutoFire(unitOrId) {
    const st = this.getUnitState(unitOrId);
    if (!st) {
      return { ok: false, orbitAutoFire: false, manualOnly: true, reason: '未找到固定翼单位' };
    }
    st.orbitAutoFire = false;
    st.manualFireOnly = true;
    return {
      ok: false,
      orbitAutoFire: false,
      manualOnly: true,
      reason: '固定翼已取消自动开火模式，仅支持手操精准打击',
    };
  }

  _syncUnitAttitude(st) {
    const rigPitchRad = -st.pitchDeg * DEG2RAD;
    const rigRollRad = -st.rollDeg * DEG2RAD;
    const fuelPct = Number(clamp((st.fuelRemainSec / Math.max(1, st.fuelMaxSec)) * 100, 0, 100).toFixed(1));
    st.fuelPct = fuelPct;
    st.unit.flightAttitude = {
      customControlled: true,
      pitch: rigPitchRad,
      roll: rigRollRad,
      pitchDeg: st.pitchDeg,
      bankDeg: st.rollDeg,
      speedMps: st.speedMps,
      yawRate: st.yawRate,
      heaveY: st.heaveY || 0,
      flightState: st.flightState,
      altitudeMode: st.altitudeMode,
      strafeRecoilActive: Boolean(st.strafeRecoilActive),
      throttleLevel: st.throttleLevel ?? 0.55,
      afterburnerActive: Boolean(st.afterburnerActive),
    };
    st.unit.fuelTelemetry = {
      usesFuelConstraint: true,
      fuelPct,
      fuelRemainSec: Number(Math.max(0, st.fuelRemainSec).toFixed(1)),
      fuelMaxSec: st.fuelMaxSec,
      fuelBurnRate: Number((st.fuelBurnRate ?? 1.0).toFixed(2)),
      fuelModeLabel: st.fuelModeLabel || '巡航',
      bingoThresholdPct: st.bingoThresholdPct ?? 14,
      bingoState: st.bingoState || 'nominal',
      bingoLockedRtb: Boolean(st.bingoLockedRtb),
      fuelConsumptionPaused: Boolean(this.fuelConsumptionPaused),
      throttleLevel: st.throttleLevel ?? 0.55,
      afterburnerActive: Boolean(st.afterburnerActive),
      rearmCooldownSec: st.spec.gameStats?.rearmCooldownSec || 35,
      winchesterState: st.winchesterState || 'nominal',
      isWinchester: st.winchesterState === 'winchester' || st.winchesterState === 'rtb_winchester',
      rtbReason: st.rtbReason || 'manual',
      autoRtbOnWinchester: Boolean(this.autoRtbOnWinchester),
    };
  }

  _hasGroundAmmo(st) {
    if (!st || !Array.isArray(st.weapons)) return true;
    const groundWeapons = st.weapons.filter(w => w.canTargetGround && w.maxAmmo > 0);
    if (!groundWeapons.length) return true;
    return groundWeapons.some(w => (w.ammo || 0) > 0);
  }

  _evaluateWinchesterState(st, { simNowSec = 0, audioBridge = null, onWinchester = null } = {}) {
    if (!st) return st?.winchesterState || 'nominal';
    if (this._hasGroundAmmo(st)) {
      st.winchesterState = 'nominal';
      st.winchesterNotified = false;
      st.winchesterAutoRtbTriggered = false;
      st.winchesterRecalled = false;
      return st.winchesterState;
    }
    if (st.crashed || st.flightState === 'crashing' || st.evacuated || st.flightState === 'evacuated') {
      return st.winchesterState || 'winchester';
    }
    if (st.flightState === 'rtb') {
      st.winchesterState = st.rtbReason === 'winchester' ? 'rtb_winchester' : 'winchester';
      return st.winchesterState;
    }
    st.winchesterState = 'winchester';
    if (st.activeStrike || st.strikeQueue.length > 0 || st.flightState === 'egress') {
      return st.winchesterState;
    }
    if (!st.winchesterNotified) {
      st.winchesterNotified = true;
      st.lastEvents.push({
        type: 'winchester',
        unitId: st.unit.id,
        autoRtb: Boolean(this.autoRtbOnWinchester && !st.winchesterRecalled),
      });
      onWinchester?.(st.unit, st);
    }
    if (this.autoRtbOnWinchester && !st.winchesterAutoRtbTriggered && !st.winchesterRecalled) {
      st.winchesterAutoRtbTriggered = true;
      const bridge = this._resolveAudioBridge(st, audioBridge);
      const rtbRes = this.issueEvacuate(st.unit, {
        simNowSec,
        audioBridge: bridge,
        reason: 'winchester',
        forceLocked: false,
      });
      if (rtbRes?.ok) {
        st.winchesterState = 'rtb_winchester';
      }
    }
    return st.winchesterState;
  }

  /**
   * 调试/单元测试专用：一键清空在场固定翼全部对地弹药，验证 Winchester 弹罄提示与可召回自动返航闭环。
   */
  depleteGroundAmmo(unitOrId, { simNowSec = 0, audioBridge = null, onWinchester = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '未找到固定翼单位' };
    for (const w of st.weapons) {
      if (w.canTargetGround && w.maxAmmo > 0) {
        w.ammo = 0;
        if (!w.keepPylonWhenEmpty) {
          w.visiblePylonIndices = [];
          for (const idx of w.pylonIndices) {
            st.pylonVisuals?.setPylonVisible(idx, false);
          }
        }
      }
    }
    this._syncUnitWeaponSummary(st);
    this._evaluateWinchesterState(st, { simNowSec, audioBridge, onWinchester });
    this._syncUnitAttitude(st);
    return {
      ok: true,
      winchesterState: st.winchesterState,
      flightState: st.flightState,
      autoRtbOnWinchester: Boolean(this.autoRtbOnWinchester),
    };
  }

  _syncUnitWeaponSummary(st) {
    st.unit.weapons = st.weapons
      .filter(w => w.maxAmmo > 0)
      .map(w => `${w.name} ${w.ammo}/${w.maxAmmo}`);
  }

  _getWeapon(st, weaponId) {
    return st.weapons.find(w => w.id === weaponId) || null;
  }

  _getWeaponByCmd(st, cmd) {
    const map = { strafe: 'gau8_30mm', rocket: 'hydra70_rocket', missile: 'agm65_maverick', bomb: 'gbu31_jdam' };
    return (
      st.weapons.find(w => w.actionCmd === cmd && w.canTargetGround)
      || st.weapons.find(w => w.actionCmd === cmd)
      || this._getWeapon(st, map[cmd] || cmd)
    );
  }

  _consumePylon(st, weapon) {
    if (!weapon) return null;
    if (weapon.keepPylonWhenEmpty) {
      const indices = weapon.pylonIndices || [];
      if (!indices.length) return null;
      const idx = indices[(weapon.pylonCycleCursor || 0) % indices.length];
      weapon.pylonCycleCursor = ((weapon.pylonCycleCursor || 0) + 1) % indices.length;
      return st.spec.hardpoints.pylons.find(p => p.index === idx) || null;
    }
    if (!weapon.visiblePylonIndices.length) return null;
    const pylonIdx = weapon.visiblePylonIndices.shift();
    st.pylonVisuals?.setPylonVisible(pylonIdx, false);
    return st.spec.hardpoints.pylons.find(p => p.index === pylonIdx) || null;
  }

  _queueFlareBurst(st, simNowSec, count = 6, { delaySec = 0, pairIntervalSec = 0.18 } = {}) {
    const n = clamp(Math.round(count), 4, 8);
    const pairs = Math.max(2, Math.round(n / 2));
    st.flareBurstCount += 1;
    for (let p = 0; p < pairs; p += 1) {
      const fireAt = simNowSec + Math.max(0, delaySec) + p * pairIntervalSec;
      const spreadBias = (p - (pairs - 1) * 0.5) * 1.8;
      st.flareQueue.push({
        fireAt,
        sideSign: 1,
        indexInBurst: p * 2,
        spreadBias,
      });
      st.flareQueue.push({
        fireAt,
        sideSign: -1,
        indexInBurst: p * 2 + 1,
        spreadBias: -spreadBias,
      });
    }
  }

  /**
   * 构造一次规范的《断箭》物理攻击计划（不瞬移，精确计算入航切入点与攻击轴）。
   */
  _buildStrikePlan(st, { cmd, weaponId, startPos = null, endPos = null, targetPos = null, dropPoints = null, missileTargets = null, terrainSupport = null, forceTransit = false }) {
    const w = weaponId ? this._getWeapon(st, weaponId) : this._getWeaponByCmd(st, cmd);
    const resolvedCmd = w?.actionCmd || cmd;
    const curPos = st.unit.node?.position || { x: st.orbitCenter.x, y: st.orbitAltitude, z: st.orbitCenter.z };

    const normalizedDrops = Array.isArray(dropPoints) && dropPoints.length > 0
      ? dropPoints
          .filter(p => p && Number.isFinite(p.x) && Number.isFinite(p.z))
          .map(p => ({ x: p.x, y: terrainSupport?.heightAt?.(p.x, p.z) ?? (Number.isFinite(p.y) ? p.y : 2.0), z: p.z }))
      : null;

    const normalizedMissiles = resolvedCmd === 'missile'
      ? (Array.isArray(missileTargets) && missileTargets.length > 0
          ? missileTargets
              .filter(p => p && Number.isFinite(p.x) && Number.isFinite(p.z))
              .map(p => ({ x: p.x, y: terrainSupport?.heightAt?.(p.x, p.z) ?? (Number.isFinite(p.y) ? p.y : 2.0), z: p.z }))
          : (targetPos && Number.isFinite(targetPos.x) && Number.isFinite(targetPos.z)
              ? [{ x: targetPos.x, y: terrainSupport?.heightAt?.(targetPos.x, targetPos.z) ?? (Number.isFinite(targetPos.y) ? targetPos.y : 2.0), z: targetPos.z }]
              : null))
      : null;

    let sx, sz, ex, ez, tx, tz;
    if (normalizedDrops && normalizedDrops.length >= 2 && Math.hypot(normalizedDrops[normalizedDrops.length - 1].x - normalizedDrops[0].x, normalizedDrops[normalizedDrops.length - 1].z - normalizedDrops[0].z) >= 8) {
      sx = normalizedDrops[0].x; sz = normalizedDrops[0].z;
      ex = normalizedDrops[normalizedDrops.length - 1].x; ez = normalizedDrops[normalizedDrops.length - 1].z;
      tx = sx; tz = sz;
    } else if (startPos && endPos && Math.hypot(endPos.x - startPos.x, endPos.z - startPos.z) >= 8) {
      const rawDx = endPos.x - startPos.x;
      const rawDz = endPos.z - startPos.z;
      const rawLen = Math.hypot(rawDx, rawDz);
      const minLen = Number(w?.strafeLenMinM) || 0;
      const maxLen = Number(w?.strafeLenMaxM) || 0;
      const clampedLen = (minLen > 0 && maxLen >= minLen) ? clamp(rawLen, minLen, maxLen) : rawLen;
      const ux = rawDx / Math.max(1e-4, rawLen);
      const uz = rawDz / Math.max(1e-4, rawLen);
      sx = startPos.x; sz = startPos.z;
      ex = sx + ux * clampedLen; ez = sz + uz * clampedLen;
      tx = (sx + ex) * 0.5; tz = (sz + ez) * 0.5;
    } else {
      const anchorPt = normalizedDrops?.[0] || normalizedMissiles?.[0] || targetPos;
      tx = Number.isFinite(anchorPt?.x) ? anchorPt.x : st.orbitCenter.x;
      tz = Number.isFinite(anchorPt?.z) ? anchorPt.z : st.orbitCenter.z;
      const dx = tx - curPos.x;
      const dz = tz - curPos.z;
      const dist = Math.hypot(dx, dz);
      const dirX = dist > 5 ? dx / dist : Math.sin(st.unit.node?.rotation?.y || 0);
      const dirZ = dist > 5 ? dz / dist : Math.cos(st.unit.node?.rotation?.y || 0);
      const halfAxis = (w?.defaultStrafeLenM || w?.burstAxisLenM || 120) * 0.5;
      sx = tx - dirX * halfAxis; sz = tz - dirZ * halfAxis;
      ex = tx + dirX * halfAxis; ez = tz + dirZ * halfAxis;
    }

    const ax = ex - sx;
    const az = ez - sz;
    const axisLen = Math.max(1, Math.hypot(ax, az));
    const dirX = ax / axisLen;
    const dirZ = az / axisLen;
    const ty = terrainSupport?.heightAt?.(tx, tz) ?? 2.0;

    // 按武器真实开火距离窗及 1.0x 航速适配入航切入点（Entry Point）
    const entryLead = resolvedCmd === 'missile'
      ? Math.min((w?.maxRangeM || 1250) * 0.84, 1050)
      : resolvedCmd === 'rocket'
        ? Math.min((w?.maxRangeM || 1100) * 0.85, 920)
        : resolvedCmd === 'bomb'
          ? Math.min((w?.maxRangeM || 1000) * 0.72, 780)
          : Math.min((w?.maxRangeM || 1000) * 0.85, 850);

    const entryPoint = { x: sx - dirX * entryLead, z: sz - dirZ * entryLead };
    const attackYaw = Math.atan2(dirX, dirZ);

    // 严格判断当前飞机是否已经处于攻击走廊延长线后方且航向基本对齐（机炮为固定机鼻武器，要求更严）
    const curToStartX = sx - curPos.x;
    const curToStartZ = sz - curPos.z;
    const curDistToStart = Math.hypot(curToStartX, curToStartZ);
    const alongBack = curToStartX * dirX + curToStartZ * dirZ;
    const crossTrackErr = Math.abs(curToStartX * dirZ - curToStartZ * dirX);
    const curYaw = st.unit.node?.rotation?.y || 0;
    const headingErr = Math.abs(wrapAngleRad(attackYaw - curYaw));
    const curToTargetX = tx - curPos.x;
    const curToTargetZ = tz - curPos.z;
    const curDist = Math.hypot(curToTargetX, curToTargetZ);
    const dotAlign = curDist > 1 ? ((curToTargetX / curDist) * dirX + (curToTargetZ / curDist) * dirZ) : -1;
    const isStrafeLike = resolvedCmd === 'strafe' || resolvedCmd === 'rocket';
    const minAlignDist = Math.max((w?.minRangeM || 160) + 220, isStrafeLike ? 420 : 340);
    const alreadyAlignedFar = isStrafeLike
      ? (!forceTransit && alongBack >= minAlignDist && crossTrackErr <= 44 && headingErr <= 22 * DEG2RAD && curDistToStart <= (w?.maxRangeM || 1000) + 260)
      : (!forceTransit && dotAlign > 0.78 && curDist >= (w?.minRangeM || 160) + 180 && headingErr <= 32 * DEG2RAD);

    const strafeWidthM = Number(w?.strafeWidthM) || ((Number(w?.splashOuterM) || 10) * 2);
    const roundsPerM = Number(w?.roundsPerMeter) || 0.85;
    const plannedStrafeRounds = resolvedCmd === 'strafe'
      ? Math.min(w?.ammo || 1150, Math.max(48, Math.round(axisLen * roundsPerM)))
      : (w?.salvoCount || 1);

    return {
      cmd: resolvedCmd,
      weaponId: w?.id || 'gau8_30mm',
      startPos: { x: sx, y: ty, z: sz },
      endPos: { x: ex, y: ty, z: ez },
      targetPos: { x: tx, y: ty, z: tz },
      dropPoints: normalizedDrops,
      nextDropIdx: 0,
      missileTargets: resolvedCmd === 'missile' ? (normalizedMissiles || [{ x: tx, y: ty, z: tz }]) : null,
      nextMissileIdx: 0,
      axisLen,
      strafeWidthM,
      strafeHalfWidthM: strafeWidthM * 0.5,
      plannedStrafeRounds,
      attackDir: { x: dirX, z: dirZ },
      attackYaw,
      entryPoint,
      phase: alreadyAlignedFar ? (isStrafeLike ? 'align' : 'run') : 'transit',
      alignStableTimer: 0,
      goAroundCount: 0,
      maxRangeM: w?.maxRangeM || 1000,
      minRangeM: w?.minRangeM || 160,
    };
  }

  /**
   * 统计指定武器在当前执飞任务及排队队列中尚未发射的目标总数量。
   */
  getPlannedWeaponTargetCount(unitOrId, weaponId = 'agm65_maverick') {
    const st = (unitOrId && unitOrId.unit && unitOrId.spec) ? unitOrId : this.getUnitState(unitOrId);
    if (!st) return 0;
    const countPlan = (p) => {
      if (!p || (weaponId && p.weaponId !== weaponId)) return 0;
      if (p.cmd === 'missile' && Array.isArray(p.missileTargets)) {
        return Math.max(0, p.missileTargets.length - (p.nextMissileIdx || 0));
      }
      if (p.cmd === 'bomb' && Array.isArray(p.dropPoints)) {
        return Math.max(0, p.dropPoints.length - (p.nextDropIdx || 0));
      }
      return 1;
    };
    let total = countPlan(st.activeStrike);
    for (const q of st.strikeQueue) {
      total += countPlan(q);
    }
    return total;
  }

  /**
   * 3A + 4A + 7A + V2.4：正式下达固定翼精准打击指令（支持拖线、点选、多点连投、导弹同走廊单趟连射与大偏角自动拆趟、Shift 排队）。
   */
  issueStrikeCommand(unitOrId, {
    weaponId = null,
    cmd = null,
    startPos = null,
    endPos = null,
    targetPos = null,
    dropPoints = null,
    missileTargets = null,
    queue = false,
    fastApproach = null,
  } = {}, { simNowSec = 0, terrainSupport = null, audioBridge = null, vfxEngine = null, fogQuery = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '单位未绑定固定翼底座' };
    if (st.evacuated || st.flightState === 'evacuated') {
      return { ok: false, reason: '战机已撤离战区，请先重新进场' };
    }
    if (st.bingoLockedRtb) {
      return { ok: false, bingoLocked: true, reason: '燃油触及返航红线（BINGO FUEL），强制返航整备中无法执行新打击' };
    }
    const bridge = this._resolveAudioBridge(st, audioBridge);

    if (st.crashed) {
      this.triggerCommand(st.unit, 'reset', targetPos, { simNowSec, terrainSupport, audioBridge: bridge, vfxEngine });
    }

    const w = weaponId ? this._getWeapon(st, weaponId) : this._getWeaponByCmd(st, cmd);
    if (!w || !w.canTargetGround) return { ok: false, reason: '该武器不支持对地打击' };
    if (w.ammo <= 0) {
      return { ok: false, ammoExhausted: true, ammo: 0, plannedCount: 0, reason: `${w.name} 已无余弹，请先重置或返航补弹` };
    }

    // 战争迷雾制导锁定规则（fog-of-war-v1）：
    // - 锁定/制导类武器（missile / requiresLiveVision）强制要求目标位于实时己方视野 R 内（支持 UAV 照射协同）；
    // - 坐标面杀伤类武器（strafe / rocket / bomb）允许对灰雾区或未探索区盲射。
    const effectiveFogQuery = typeof fogQuery === 'function'
      ? fogQuery
      : (typeof this.fogQuery === 'function' ? this.fogQuery : null);
    const requiresVision = w.actionCmd === 'missile' || w.requiresLiveVision === true;
    if (requiresVision && effectiveFogQuery) {
      const checkPts = Array.isArray(missileTargets) && missileTargets.length > 0
        ? missileTargets
        : (targetPos ? [targetPos] : []);
      for (const pt of checkPts) {
        if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.z)) continue;
        const vis = effectiveFogQuery(pt.x, pt.z);
        if (vis && vis.visible === false) {
          return {
            ok: false,
            fogBlocked: true,
            reason: `${w.name} 为精确制导锁定武器，目标位于战争迷雾外（请先派无人机或侦察部队点亮目标区）`,
          };
        }
      }
    }

    const toWorldPt = (pt) => ({
      x: pt.x,
      y: terrainSupport?.heightAt?.(pt.x, pt.z) ?? (Number.isFinite(pt.y) ? pt.y : 2.0),
      z: pt.z,
    });

    // 导弹 Shift 排队入列：优先检查是否可并入同走廊（偏角 <= 40° 且单趟 <= 6 且不超余弹）
    if (queue && w.actionCmd === 'missile' && (st.activeStrike || st.strikeQueue.length > 0)) {
      const incomingRaw = Array.isArray(missileTargets) && missileTargets.length > 0
        ? missileTargets.filter(p => p && Number.isFinite(p.x) && Number.isFinite(p.z))
        : (targetPos && Number.isFinite(targetPos.x) && Number.isFinite(targetPos.z) ? [targetPos] : []);
      if (incomingRaw.length === 0) {
        return { ok: false, reason: '缺少有效的导弹打击目标点' };
      }

      const alreadyPlanned = this.getPlannedWeaponTargetCount(st, w.id);
      if (alreadyPlanned >= w.ammo) {
        return {
          ok: false,
          ammoExhausted: true,
          plannedCount: alreadyPlanned,
          ammo: w.ammo,
          reason: `${w.name} 已排满余弹上限 (${alreadyPlanned}/${w.ammo})`,
        };
      }

      const allowCount = Math.min(incomingRaw.length, Math.max(0, w.ammo - alreadyPlanned));
      let mergedCount = 0;
      let splitBatches = 0;
      let lastMergedPassCount = 1;

      for (let i = 0; i < allowCount; i += 1) {
        const ptWorld = toWorldPt(incomingRaw[i]);
        const tailQueue = st.strikeQueue.length > 0 ? st.strikeQueue[st.strikeQueue.length - 1] : null;
        const candidate = tailQueue || st.activeStrike;
        const isCurrentActive = !tailQueue && candidate === st.activeStrike;
        const planePos = st.unit.node?.position || { x: st.orbitCenter.x, z: st.orbitCenter.z };
        const refPos = (isCurrentActive && candidate?.phase === 'run') ? planePos : candidate?.entryPoint;
        const distFromPlane = Math.hypot(ptWorld.x - planePos.x, ptWorld.z - planePos.z);
        const canFitActiveRun = !isCurrentActive || candidate?.phase === 'transit' || distFromPlane >= (w.minRangeM || 250) + 35;

        if (
          candidate
          && candidate.cmd === 'missile'
          && candidate.weaponId === w.id
          && canFitActiveRun
          && canMergeMissileIntoPlan(candidate, ptWorld, refPos, { maxAngleDeg: 40, maxPerPass: 6 })
        ) {
          if (!Array.isArray(candidate.missileTargets)) {
            candidate.missileTargets = candidate.targetPos ? [{ ...candidate.targetPos }] : [];
          }
          candidate.missileTargets.push(ptWorld);
          if (isCurrentActive) {
            st.missileFiredInRun = false;
            st.missilePostFireTimer = 0;
            if ((candidate.nextMissileIdx || 0) >= candidate.missileTargets.length - 1) {
              candidate.targetPos = { ...ptWorld };
              st.targetPos = { ...ptWorld };
            }
          }
          mergedCount += 1;
          lastMergedPassCount = candidate.missileTargets.length;
        } else {
          const newPlan = this._buildStrikePlan(st, {
            cmd: 'missile',
            weaponId: w.id,
            targetPos: ptWorld,
            missileTargets: [ptWorld],
            terrainSupport,
            forceTransit: true,
          });
          st.strikeQueue.push(newPlan);
          splitBatches += 1;
          lastMergedPassCount = 1;
        }
      }

      const totalPlannedAfter = this.getPlannedWeaponTargetCount(st, w.id);
      return {
        ok: true,
        queued: true,
        mergedCorridor: mergedCount > 0 && splitBatches === 0,
        mergedCount,
        splitBatches,
        passTargetCount: lastMergedPassCount,
        queueLength: st.strikeQueue.length,
        plannedCount: totalPlannedAfter,
        ammo: w.ammo,
        truncatedByAmmo: allowCount < incomingRaw.length,
        flightState: st.flightState,
      };
    }

    const hasMultiDrops = Array.isArray(dropPoints) && dropPoints.length > 0;
    const hasMultiMissiles = w.actionCmd === 'missile' && Array.isArray(missileTargets) && missileTargets.length > 0;
    const plans = [];

    if (hasMultiDrops) {
      const capped = dropPoints.slice(0, Math.max(1, w.ammo));
      const batches = groupDropPointsIntoCorridors(capped, 42, 460);
      for (let bi = 0; bi < batches.length; bi += 1) {
        plans.push(this._buildStrikePlan(st, { cmd, weaponId: w.id, dropPoints: batches[bi], terrainSupport, forceTransit: bi > 0 }));
      }
    } else if (hasMultiMissiles) {
      const cappedMissiles = missileTargets
        .filter(p => p && Number.isFinite(p.x) && Number.isFinite(p.z))
        .slice(0, Math.max(1, w.ammo))
        .map(toWorldPt);
      for (const mPt of cappedMissiles) {
        const prev = plans[plans.length - 1];
        if (prev && canMergeMissileIntoPlan(prev, mPt, prev.entryPoint, { maxAngleDeg: 40, maxPerPass: 6 })) {
          prev.missileTargets.push(mPt);
        } else {
          plans.push(this._buildStrikePlan(st, {
            cmd: 'missile',
            weaponId: w.id,
            targetPos: mPt,
            missileTargets: [mPt],
            terrainSupport,
            forceTransit: plans.length > 0,
          }));
        }
      }
    }
    if (plans.length === 0) {
      plans.push(this._buildStrikePlan(st, { cmd, weaponId: w.id, startPos, endPos, targetPos, terrainSupport }));
    }

    if (queue && (st.activeStrike || st.strikeQueue.length > 0)) {
      for (const p of plans) {
        p.phase = 'transit';
        st.strikeQueue.push(p);
      }
      return {
        ok: true,
        queued: true,
        queueLength: st.strikeQueue.length,
        corridorBatches: plans.length,
        plannedCount: this.getPlannedWeaponTargetCount(st, w.id),
        ammo: w.ammo,
        flightState: st.flightState,
      };
    }

    st.strikeQueue.length = 0;
    this._stopStrafeAudioIfNeeded(st, bridge);
    this._activateStrikePlan(st, plans[0], {
      useFastApproach: fastApproach === null ? this.fastApproachMode : Boolean(fastApproach),
      terrainSupport,
      audioBridge: bridge,
    });
    for (let i = 1; i < plans.length; i += 1) {
      plans[i].phase = 'transit';
      st.strikeQueue.push(plans[i]);
    }
    return {
      ok: true,
      queued: false,
      corridorBatches: plans.length,
      queueLength: st.strikeQueue.length,
      plannedCount: this.getPlannedWeaponTargetCount(st, w.id),
      ammo: w.ammo,
      flightState: st.flightState,
      weaponId: plans[0].weaponId,
      phase: plans[0].phase,
    };
  }

  _activateStrikePlan(st, plan, { useFastApproach = false, terrainSupport = null, audioBridge = null } = {}) {
    this._stopStrafeAudioIfNeeded(st, audioBridge);
    st.activeStrike = plan;
    st.flightState = plan.cmd;
    st.targetPos = { ...plan.targetPos };
    st.runTimer = 0;
    st.strafeFireTimer = 0;
    st.strafeRoundsFired = 0;
    st.strafeBurstTick = 0;
    st.rocketFireTimer = 0;
    st.rocketsFiredInRun = 0;
    st.bombDroppedInRun = false;
    st.bombDropIntervalTimer = 0;
    st.bombPostDropTimer = 0;
    st.missileFiredInRun = false;
    st.missileFireTimer = 0;
    st.missilePostFireTimer = 0;

    // 当队列后续趟次激活时，根据当前实际飞机位置重新检查是否已处于入航对齐扇区
    if (!useFastApproach && st.unit.node?.position && plan.phase === 'transit') {
      const p = st.unit.node.position;
      const w = this._getWeapon(st, plan.weaponId);
      const isStrafeLike = plan.cmd === 'strafe' || plan.cmd === 'rocket';
      const dx = (plan.startPos?.x ?? plan.targetPos.x) - p.x;
      const dz = (plan.startPos?.z ?? plan.targetPos.z) - p.z;
      const alongBack = dx * plan.attackDir.x + dz * plan.attackDir.z;
      const crossTrackErr = Math.abs(dx * plan.attackDir.z - dz * plan.attackDir.x);
      const headingErr = Math.abs(wrapAngleRad(plan.attackYaw - (st.unit.node.rotation?.y || 0)));
      if (isStrafeLike) {
        if (alongBack >= Math.max((w?.minRangeM || 160) + 220, 420) && crossTrackErr <= 44 && headingErr <= 22 * DEG2RAD) {
          plan.phase = 'align';
          plan.alignStableTimer = 0;
        }
      } else {
        const d = Math.hypot(plan.targetPos.x - p.x, plan.targetPos.z - p.z);
        const dot = d > 1 ? (((plan.targetPos.x - p.x) / d) * plan.attackDir.x + ((plan.targetPos.z - p.z) / d) * plan.attackDir.z) : -1;
        if (dot > 0.78 && d >= (w?.minRangeM || 160) + 180 && headingErr <= 32 * DEG2RAD) {
          plan.phase = 'run';
        }
      }
    }

    // 4A：仅在调试页显式启用「快速近距入航」时才把飞机拉近到入航点，正式指令绝不瞬移！
    if (useFastApproach && st.unit.node?.position) {
      const ty = terrainSupport?.heightAt?.(plan.targetPos.x, plan.targetPos.z) ?? 2.0;
      const desiredAlt = plan.cmd === 'missile' ? 205 : plan.cmd === 'bomb' ? 210 : 185;
      const pullDist = plan.cmd === 'missile' ? 960 : plan.cmd === 'bomb' ? 660 : plan.cmd === 'rocket' ? 760 : 720;
      const anchor = (plan.cmd === 'strafe' && plan.startPos) ? plan.startPos : plan.targetPos;
      st.unit.node.position.set(
        anchor.x - plan.attackDir.x * pullDist,
        Math.max(desiredAlt, ty + desiredAlt - 2),
        anchor.z - plan.attackDir.z * pullDist,
      );
      st.unit.node.rotation.y = plan.attackYaw;
      st.rollDeg = 0;
      st.pitchDeg = plan.cmd === 'strafe' ? -8 : -4;
      plan.phase = 'run';
      plan.alignStableTimer = 0.5;
      st.unit.node.updateMatrixWorld?.(true);
    }
    this._syncUnitAttitude(st);
  }

  /**
   * 兼容快捷指令入口：approach | orbit | strafe | rocket | bomb | missile | flare | smoke | crash | resetAmmo | reset | toggleAltitude
   */
  triggerCommand(unitOrId, cmd, targetPos = null, {
    simNowSec = 0,
    terrainSupport = null,
    audioBridge = null,
    vfxEngine = null,
    startPos = null,
    endPos = null,
    dropPoints = null,
    queue = false,
    fastApproach = null,
  } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { ok: false, reason: '单位未绑定固定翼底座' };
    if (st.evacuated && !['reset', 'approach'].includes(cmd)) {
      return { ok: false, reason: '战机已撤离战区，请先重新进场' };
    }
    const bridge = this._resolveAudioBridge(st, audioBridge);

    if (cmd === 'toggleAltitude') return this.toggleAltitudeMode(st.unit);
    if (cmd === 'toggleAutoFire') {
      return {
        ok: false,
        orbitAutoFire: false,
        manualOnly: true,
        reason: '固定翼已取消自动开火模式，仅支持手操精准打击',
      };
    }

    const tx = Number.isFinite(targetPos?.x) ? targetPos.x : st.orbitCenter.x;
    const tz = Number.isFinite(targetPos?.z) ? targetPos.z : st.orbitCenter.z;
    const ty = Number.isFinite(targetPos?.y) ? targetPos.y : (terrainSupport?.heightAt?.(tx, tz) ?? 2.0);
    st.targetPos = { x: tx, y: ty, z: tz };

    if (cmd === 'reset' || cmd === 'resetAmmo') {
      this._stopStrafeAudioIfNeeded(st, bridge);
      bridge?.stopFallingLoop?.(st.unit.id);
      st.crashAudioStarted = false;

      for (const w of st.weapons) {
        w.ammo = w.maxAmmo;
        w.visiblePylonIndices = [...w.pylonIndices];
        w.pylonCycleCursor = 0;
        w.cooldownRemaining = 0;
      }
      st.pylonVisuals?.resetAllVisible?.();
      st.winchesterState = 'nominal';
      st.winchesterNotified = false;
      st.winchesterAutoRtbTriggered = false;
      st.winchesterRecalled = false;
      this._syncUnitWeaponSummary(st);

      if (cmd === 'reset') {
        bridge?.stopAfterburnerAudio?.(st.unit.id, { immediate: true });
        st.crashed = false;
        st.evacuated = false;
        st.unit.evacuated = false;
        st.rtbTarget = null;
        st.rtbTimer = 0;
        st.rtbReason = 'manual';
        st.fuelRemainSec = st.fuelMaxSec;
        st.fuelPct = 100;
        st.fuelBurnRate = 1.0;
        st.fuelModeLabel = '巡航';
        st.bingoState = 'nominal';
        st.bingoAutoTriggered = false;
        st.bingoLockedRtb = false;
        st.lowFuelNotified = false;
        st.ejectionTriggered = false;
        st.throttleLevel = 0.55;
        st.afterburnerActive = false;
        st.strafeRecoilActive = false;
        st.damagedSmokeActive = false;
        st.damageSmokeTimer = 0;
        st.wreckBurnAge = 0;
        st.lastCrashSmokePos = null;
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
        st.crashSpinDeg = 0;
        st.activeStrike = null;
        st.strikeQueue.length = 0;
        st.flareQueue.length = 0;
        for (let i = 0; i < st.projectiles.length; i += 1) {
          const p = st.projectiles[i];
          if (p?.kind === 'gbu31') bridge?.stopOrdnanceWhistle?.(p.id, { immediate: true });
        }
        st.projectiles.length = 0;
        st.unit.hp = st.spec.gameStats.hp;
        st.unit.maxHp = st.spec.gameStats.hp;
        st.unit.alive = true;
        if (st.unit.node) st.unit.node.visible = true;
        st.flightState = 'orbit';
        st.altitudeMode = 'high';
        st.orbitCenter = { x: this.defaultTargetPos.x, z: this.defaultTargetPos.z };
        st.orbitRadius = st.arch.orbitRadiusM;
        st.orbitAltitude = st.spec.gameStats?.highAltitudeM || st.arch.cruiseAltitudeM;
        st.orbitPhase = 0.4;
        st.unit.orbitCenter = { ...st.orbitCenter };
        st.unit.orbitRadius = st.orbitRadius;
        st.speedMps = st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps;
        st.pitchDeg = st.arch.cruiseAoaDeg ?? 2.8;
        st.rollDeg = -28;
        st.yawRate = -0.42;
        const sx = st.orbitCenter.x + Math.cos(st.orbitPhase) * st.orbitRadius;
        const sz = st.orbitCenter.z + Math.sin(st.orbitPhase) * st.orbitRadius;
        if (st.unit.node?.position) {
          st.unit.node.position.set(sx, st.orbitAltitude, sz);
          st.unit.node.rotation.y = Math.atan2(-Math.sin(st.orbitPhase), Math.cos(st.orbitPhase));
          st.unit.node.updateMatrixWorld?.(true);
        }
        this._syncUnitAttitude(st);
      }
      return { ok: true, flightState: st.flightState };
    }

    if (cmd === 'rtb' || cmd === 'evacuate') {
      return this.issueEvacuate(st.unit, { simNowSec, audioBridge: bridge });
    }
    if (cmd === 'cancelRtb') {
      return this.cancelEvacuate(st.unit, { audioBridge: bridge });
    }

    if (st.crashed && cmd !== 'crash') {
      this.triggerCommand(st.unit, 'reset', targetPos, { simNowSec, terrainSupport, audioBridge, vfxEngine });
    }

    if (cmd === 'flare') {
      this._queueFlareBurst(st, simNowSec, 6);
      const origin = localRigToWorld(st.unit, st.spec.hardpoints.flareOrigin, this._tmpVecA);
      audioBridge?.scheduleWorldEvent?.('BA_Chaff_Trap', origin, { gain: 0.72, powerTier: 'small' });
      return { ok: true, flightState: st.flightState, flareBurstCount: st.flareBurstCount };
    }

    if (cmd === 'smoke') {
      st.damagedSmokeActive = true;
      st.unit.hp = Math.min(st.unit.hp || 180, Math.round((st.unit.maxHp || 180) * 0.42));
      if (st.flightState === 'orbit' || st.flightState === 'approach') {
        st.flightState = 'damaged';
      }
      const smokePos = localRigToWorld(st.unit, st.spec.hardpoints.damageSmokeOrigin, this._tmpVecA);
      audioBridge?.scheduleWorldEvent?.('SFX_Expl_MISSILE_AA-A2A-A2G_Small', smokePos, { gain: 0.55, powerTier: 'small' });
      vfxEngine?.spawnDamageSmokePuff?.(smokePos, true);
      return { ok: true, flightState: st.flightState, damagedSmokeActive: true };
    }

    if (cmd === 'crash') {
      this._stopStrafeAudioIfNeeded(st, bridge);
      bridge?.stopAfterburnerAudio?.(st.unit.id, { immediate: true });
      st.activeStrike = null;
      st.strikeQueue.length = 0;
      st.damagedSmokeActive = true;
      st.damageSmokeTimer = 0;
      st.wreckBurnAge = 0;
      st.lastCrashSmokePos = null;
      st.ejectionTriggered = false;
      st.wingtipVortexActive = false;
      st.wingtipFadeAlpha = 0;
      vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
      st.crashed = false;
      st.flightState = 'crashing';
      st.unit.hp = 0;
      st.runTimer = 0;
      st.pitchDeg = -34;
      st.speedMps = Math.max(st.speedMps, 68);
      if (!st.crashAudioStarted) {
        st.crashAudioStarted = true;
        bridge?.startFallingLoop?.(st.unit.id, st.unit.node?.position || { x: tx, y: 180, z: tz });
      }
      this._syncUnitAttitude(st);
      return { ok: true, flightState: 'crashing' };
    }

    if (cmd === 'approach') {
      this._stopStrafeAudioIfNeeded(st, bridge);
      st.activeStrike = null;
      st.strikeQueue.length = 0;
      st.evacuated = false;
      st.unit.evacuated = false;
      st.rtbTarget = null;
      st.rtbTimer = 0;
      st.rtbReason = 'manual';
      st.fuelRemainSec = st.fuelMaxSec;
      st.fuelPct = 100;
      st.fuelBurnRate = 1.0;
      st.fuelModeLabel = '巡航';
      st.bingoState = 'nominal';
      st.bingoAutoTriggered = false;
      st.bingoLockedRtb = false;
      st.lowFuelNotified = false;
      st.winchesterState = 'nominal';
      st.winchesterNotified = false;
      st.winchesterAutoRtbTriggered = false;
      st.winchesterRecalled = false;
      st.ejectionTriggered = false;
      st.throttleLevel = 0.55;
      st.afterburnerActive = false;
      st.unit.alive = true;
      if (st.unit.node) st.unit.node.visible = true;
      st.flightState = 'approach';
      st.orbitCenter = { x: tx, z: tz };
      st.unit.orbitCenter = { x: tx, z: tz };
      st.runTimer = 0;
      const entryX = tx - 520;
      const entryZ = tz - 440;
      const entryYaw = Math.atan2(tx - entryX, tz - entryZ);
      if (st.unit.node?.position) {
        st.unit.node.position.set(entryX, Math.max(215, ty + 210), entryZ);
        st.unit.node.rotation.y = entryYaw;
      }
      st.speedMps = (st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps) + 10;
      st.pitchDeg = -2;
      st.rollDeg = 0;
      this._syncUnitAttitude(st);
      return { ok: true, flightState: 'approach' };
    }

    if (cmd === 'sprint') {
      return this.issueDirectSprint(st.unit, { x: tx, y: ty, z: tz }, { audioBridge: bridge });
    }

    if (cmd === 'orbit') {
      this._stopStrafeAudioIfNeeded(st, bridge);
      st.activeStrike = null;
      st.strikeQueue.length = 0;
      st.rtbTarget = null;
      st.rtbTimer = 0;
      st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
      st.orbitCenter = { x: tx, z: tz };
      st.unit.orbitCenter = { x: tx, z: tz };
      st.runTimer = 0;
      const curPos = st.unit.node?.position;
      if (curPos && this.fastApproachMode) {
        const distCenter = Math.hypot(curPos.x - tx, curPos.z - tz);
        if (Math.abs(distCenter - st.orbitRadius) > 160) {
          st.orbitPhase = Math.atan2(curPos.z - tz, curPos.x - tx);
          curPos.set(
            tx + Math.cos(st.orbitPhase) * st.orbitRadius,
            Math.max(st.orbitAltitude, ty + 170),
            tz + Math.sin(st.orbitPhase) * st.orbitRadius,
          );
          st.unit.node.rotation.y = Math.atan2(-Math.sin(st.orbitPhase), Math.cos(st.orbitPhase));
          st.rollDeg = -28;
          st.pitchDeg = st.arch.cruiseAoaDeg ?? 2.8;
          this._syncUnitAttitude(st);
        }
      }
      return { ok: true, flightState: st.flightState };
    }

    if (['strafe', 'rocket', 'bomb', 'missile'].includes(cmd)) {
      const w = this._getWeaponByCmd(st, cmd);
      // 快捷测试若弹药归零，自动重填该武器方便连续体验
      if (w && w.ammo <= 0) {
        w.ammo = w.maxAmmo;
        w.visiblePylonIndices = [...w.pylonIndices];
        w.pylonCycleCursor = 0;
        for (const idx of w.pylonIndices) st.pylonVisuals?.setPylonVisible(idx, true);
        st.winchesterState = 'nominal';
        st.winchesterNotified = false;
        st.winchesterAutoRtbTriggered = false;
        st.winchesterRecalled = false;
        this._syncUnitWeaponSummary(st);
      }
      return this.issueStrikeCommand(
        st.unit,
        { cmd, weaponId: w?.id, startPos, endPos, dropPoints, targetPos: { x: tx, y: ty, z: tz }, queue, fastApproach },
        { simNowSec, terrainSupport, audioBridge, vfxEngine },
      );
    }

    return { ok: false, reason: `未知固定翼指令: ${cmd}` };
  }

  /**
   * 导出当固定翼离新盘旋圈较远时的真实切线赶路路径（到达圆周自动返回 null 消隐）。
   */
  getOrbitTransitPath(unitOrId) {
    const st = (unitOrId && unitOrId.unit && unitOrId.spec) ? unitOrId : this.getUnitState(unitOrId);
    if (!st || !st.unit?.node?.position || st.crashed) return null;
    if (st.activeStrike || !['orbit', 'approach', 'damaged', 'sprint'].includes(st.flightState)) return null;
    const pos = st.unit.node.position;
    const dCenter = Math.hypot(pos.x - st.orbitCenter.x, pos.z - st.orbitCenter.z);
    const orbitR = clamp(st.orbitRadius, st.arch.minOrbitRadiusM, st.arch.maxOrbitRadiusM);
    if (st.flightState !== 'sprint' && Math.abs(dCenter - orbitR) <= 14) return null;
    const entryPoint = computeOrbitEntryPoint(pos, st.orbitCenter, orbitR);
    const spd = Math.max(30, st.speedMps || st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps);
    const dist = Math.hypot(entryPoint.x - pos.x, entryPoint.z - pos.z);
    return {
      active: true,
      kind: 'orbit_transit',
      flightState: st.flightState,
      isSprint: st.flightState === 'sprint',
      planePos: { x: pos.x, y: pos.y, z: pos.z },
      entryPoint: { x: entryPoint.x, y: pos.y, z: entryPoint.z },
      orbitCenter: { ...st.orbitCenter },
      orbitRadius: orbitR,
      etaSec: Number((dist / spd).toFixed(1)),
    };
  }

  /**
   * 序列化 `strikeQueue` 为带累加 ETA 的全队列地面标绘清单。
   */
  _serializeQueuedStrikes(st, startAnchorPos, startEtaSec, spd) {
    if (!Array.isArray(st.strikeQueue) || st.strikeQueue.length === 0) return [];
    const list = [];
    let prevX = Number.isFinite(startAnchorPos?.x) ? startAnchorPos.x : (st.unit.node?.position?.x || 0);
    let prevZ = Number.isFinite(startAnchorPos?.z) ? startAnchorPos.z : (st.unit.node?.position?.z || 0);
    let accEta = Math.max(0, Number(startEtaSec) || 0);
    const cruiseSpd = Math.max(30, spd || 97.5);

    for (const q of st.strikeQueue) {
      if (!q) continue;
      const w = this._getWeapon(st, q.weaponId);
      const qTarget = q.targetPos || q.startPos || { x: prevX, y: 2, z: prevZ };
      const qEntry = q.entryPoint || qTarget;
      const hopDist = Math.hypot(qEntry.x - prevX, qEntry.z - prevZ)
        + Math.hypot((q.startPos?.x ?? qTarget.x) - qEntry.x, (q.startPos?.z ?? qTarget.z) - qEntry.z);
      accEta += hopDist / cruiseSpd + 2.2;
      const remainingDrops = Array.isArray(q.dropPoints)
        ? q.dropPoints.slice(q.nextDropIdx || 0).map(pt => ({ ...pt }))
        : [];
      const remainingMissiles = Array.isArray(q.missileTargets)
        ? q.missileTargets.slice(q.nextMissileIdx || 0).map(pt => ({ ...pt }))
        : (q.cmd === 'missile' && qTarget ? [{ ...qTarget }] : []);
      list.push({
        cmd: q.cmd,
        weaponId: q.weaponId,
        weaponKind: w?.kind || q.cmd,
        inputMode: w?.inputMode || 'drag_line',
        startPos: q.startPos ? { ...q.startPos } : null,
        endPos: q.endPos ? { ...q.endPos } : null,
        targetPos: { ...qTarget },
        dropPoints: remainingDrops,
        missileTargets: remainingMissiles,
        strafeWidthM: q.strafeWidthM || w?.strafeWidthM || ((w?.splashOuterM || 10) * 2),
        splashOuterM: w?.splashOuterM || 12,
        splashInnerM: w?.splashInnerM || 4,
        etaSec: Number(accEta.toFixed(1)),
      });
      const tailPt = remainingMissiles.length > 0
        ? remainingMissiles[remainingMissiles.length - 1]
        : (remainingDrops.length > 0 ? remainingDrops[remainingDrops.length - 1] : (q.endPos || qTarget));
      prevX = tailPt.x;
      prevZ = tailPt.z;
    }
    return list;
  }

  /**
   * 导出当前攻击或盘旋赶路预测航路、当前趟余弹目标及完整排队队列（供高清 2D 标绘层全队列绘制）。
   */
  getPredictedFlightPath(unitOrId) {
    const st = this.getUnitState(unitOrId);
    if (!st || !st.unit?.node?.position) return null;
    const pos = st.unit.node.position;
    const spd = Math.max(30, st.speedMps || st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps);

    if (st.activeStrike) {
      const s = st.activeStrike;
      const pts = [{ x: pos.x, z: pos.z, kind: 'plane' }];
      let totalDist = 0;
      if (s.phase === 'transit') {
        pts.push({ x: s.entryPoint.x, z: s.entryPoint.z, kind: 'entry' });
        totalDist += Math.hypot(s.entryPoint.x - pos.x, s.entryPoint.z - pos.z);
        totalDist += Math.hypot(s.startPos.x - s.entryPoint.x, s.startPos.z - s.entryPoint.z);
      } else {
        totalDist += Math.hypot(s.targetPos.x - pos.x, s.targetPos.z - pos.z);
      }
      pts.push({ x: s.startPos.x, z: s.startPos.z, kind: 'start' });
      pts.push({ x: s.endPos.x, z: s.endPos.z, kind: 'end' });
      const weapon = this._getWeapon(st, s.weaponId);
      const remainingDrops = Array.isArray(s.dropPoints)
        ? s.dropPoints.slice(s.nextDropIdx || 0).map(pt => ({ ...pt }))
        : [];
      const remainingMissiles = Array.isArray(s.missileTargets)
        ? s.missileTargets.slice(s.nextMissileIdx || 0).map(pt => ({ ...pt }))
        : (s.cmd === 'missile' && s.targetPos ? [{ ...s.targetPos }] : []);
      const baseEta = Number((totalDist / spd).toFixed(1));
      const tailAnchor = remainingMissiles.length > 0
        ? remainingMissiles[remainingMissiles.length - 1]
        : (remainingDrops.length > 0 ? remainingDrops[remainingDrops.length - 1] : (s.endPos || s.targetPos));
      const queuedStrikes = this._serializeQueuedStrikes(st, tailAnchor, baseEta, spd);

      const goAroundCount = s.goAroundCount || 0;
      const phaseLabel = s.phase === 'transit'
        ? (goAroundCount > 0 ? '复飞重入' : '绕飞入航')
        : (s.phase === 'align' ? '姿态对准' : '打击中');
      const approachCurve = [];
      if (s.phase === 'transit' || s.phase === 'align') {
        const yaw = st.unit.node?.rotation?.y || 0;
        const fwdX = Math.sin(yaw);
        const fwdZ = Math.cos(yaw);
        const endAnchor = s.phase === 'transit' ? s.entryPoint : (s.startPos || s.targetPos);
        const dCurve = Math.hypot(endAnchor.x - pos.x, endAnchor.z - pos.z);
        const ctrlLead = clamp(dCurve * 0.42, 30, 210);
        const ctrlX = pos.x + fwdX * ctrlLead;
        const ctrlZ = pos.z + fwdZ * ctrlLead;
        for (let si = 0; si <= 5; si += 1) {
          const t = si / 5;
          const u = 1 - t;
          approachCurve.push({
            x: u * u * pos.x + 2 * u * t * ctrlX + t * t * endAnchor.x,
            z: u * u * pos.z + 2 * u * t * ctrlZ + t * t * endAnchor.z,
          });
        }
        if (s.phase === 'transit' && s.startPos) {
          approachCurve.push({ x: s.startPos.x, z: s.startPos.z });
        }
      }

      return {
        active: true,
        kind: 'strike',
        flightState: st.flightState,
        phase: s.phase,
        goAroundCount,
        phaseLabel,
        approachCurve,
        weaponId: s.weaponId,
        weaponKind: weapon?.kind || s.cmd,
        inputMode: weapon?.inputMode || 'drag_line',
        planePos: { x: pos.x, y: pos.y, z: pos.z },
        cruiseAltY: st.orbitAltitude || pos.y || 185,
        entryPoint: { x: s.entryPoint.x, z: s.entryPoint.z },
        points: pts,
        dropPoints: remainingDrops,
        missileTargets: remainingMissiles,
        queuedStrikes,
        startPos: s.startPos,
        endPos: s.endPos,
        targetPos: s.targetPos,
        strafeWidthM: s.strafeWidthM || weapon?.strafeWidthM || ((weapon?.splashOuterM || 10) * 2),
        minRangeM: weapon?.minRangeM || s.minRangeM || 0,
        maxRangeM: weapon?.maxRangeM || s.maxRangeM || 1000,
        splashOuterM: weapon?.splashOuterM || 12,
        splashInnerM: weapon?.splashInnerM || 4,
        etaSec: baseEta,
      };
    }

    if (st.strikeQueue.length > 0) {
      const queuedStrikes = this._serializeQueuedStrikes(st, pos, 1.5, spd);
      return {
        active: true,
        kind: 'strike_queued',
        flightState: st.flightState,
        phase: 'egress',
        weaponId: queuedStrikes[0]?.weaponId || '',
        weaponKind: queuedStrikes[0]?.weaponKind || '',
        inputMode: queuedStrikes[0]?.inputMode || 'point_target',
        planePos: { x: pos.x, y: pos.y, z: pos.z },
        cruiseAltY: st.orbitAltitude || pos.y || 185,
        dropPoints: [],
        missileTargets: [],
        queuedStrikes,
        etaSec: queuedStrikes[0]?.etaSec ?? 0,
      };
    }

    if (st.flightState === 'rtb' && st.rtbTarget) {
      const safeX = clamp(st.rtbTarget.x, -4380, 4520);
      const safeZ = clamp(st.rtbTarget.z, -4100, 4800);
      const distExit = Math.hypot(st.rtbTarget.x - pos.x, st.rtbTarget.z - pos.z);
      return {
        active: true,
        kind: 'orbit_transit',
        flightState: 'rtb',
        isRtb: true,
        isSprint: true,
        planePos: { x: pos.x, y: pos.y, z: pos.z },
        entryPoint: { x: safeX, y: pos.y, z: safeZ },
        rtbTarget: { ...st.rtbTarget },
        etaSec: Number((distExit / Math.max(40, spd)).toFixed(1)),
      };
    }

    return this.getOrbitTransitPath(st);
  }

  /**
   * 通用底座预留：超音速加力尾焰与音爆触发入口（A-10C `capabilities.sonicBoom === false` 严禁触发）。
   */
  triggerAfterburnerAndSonicBoom(unitOrId, simNowSec, { vfxEngine = null, audioBridge = null } = {}) {
    const st = this.getUnitState(unitOrId);
    if (!st) return { triggered: false, reason: 'missing_unit' };
    if (!st.spec.capabilities.sonicBoom && !st.spec.capabilities.afterburner) {
      return { triggered: false, reason: 'subsonic_disabled' };
    }
    const pos = st.unit.node?.position || { x: 0, y: 240, z: 0 };
    if (st.spec.capabilities.afterburner) {
      for (const ex of st.spec.hardpoints.exhaustsRig) {
        const wpos = localRigToWorld(st.unit, ex, this._tmpVecA);
        vfxEngine?.emitAfterburnerCone?.(wpos, st.unit.node?.rotation?.y || 0, -st.pitchDeg * DEG2RAD);
      }
    }
    if (st.spec.capabilities.sonicBoom && (simNowSec - st.lastSonicBoomSec) >= 12.0) {
      st.lastSonicBoomSec = simNowSec;
      vfxEngine?.spawnSonicBoomCone?.(pos, st.unit.node?.rotation?.y || 0, -st.pitchDeg * DEG2RAD, simNowSec);
      audioBridge?.playSonicBoom?.(pos);
      return { triggered: true, sonicBoom: true };
    }
    return { triggered: true, sonicBoom: false };
  }

  /**
   * 推进固定翼状态机、真实入航、武器弹道、热诱弹、涡流与损毁逻辑。
   */
  step(unit, dtSec, simNowSec, { terrainSupport = null, vfxEngine = null, audioBridge = null, targets = null, onTargetImpact = null, onUnitEvacuated = null, onBingoFuel = null, onWinchester = null } = {}) {
    const st = this.getUnitState(unit);
    if (!st || !st.unit?.node) return null;
    const bridge = this._resolveAudioBridge(st, audioBridge);
    if (st.flightState !== 'strafe' && st.strafeAudioActive) {
      this._stopStrafeAudioIfNeeded(st, bridge);
    }
    const dt = clamp(Number(dtSec) || 0, 0, 0.12);
    if (dt <= 1e-6) return st;

    st.lastEvents.length = 0;
    st.strafeRecoilActive = false;
    st.heaveY = 0;
    const pos = st.unit.node.position;
    const prevYaw = st.unit.node.rotation.y || 0;
    const groundUnder = terrainSupport?.heightAt?.(pos.x, pos.z) ?? 2.0;

    if (st.flightState === 'evacuated' || st.evacuated) {
      st.speedMps = 0;
      st.yawRate = 0;
      st.afterburnerActive = false;
      bridge?.syncAfterburnerAudio?.(st.unit.id, pos, false);
      if (st.wingtipVortexActive || st.wingtipFadeAlpha > 0) {
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
      }
      if (st.unit.node) st.unit.node.visible = false;
      this._stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge: bridge, targets, onTargetImpact });
      this._syncUnitAttitude(st);
      return st;
    }

    for (const w of st.weapons) {
      if (w.cooldownRemaining > 0) w.cooldownRemaining = Math.max(0, w.cooldownRemaining - dt);
    }

    // 1. 延迟热诱弹队列（2+2+2 双发脉冲扇面）
    while (st.flareQueue.length > 0 && st.flareQueue[0].fireAt <= simNowSec) {
      const f = st.flareQueue.shift();
      const origin = localRigToWorld(st.unit, st.spec.hardpoints.flareOrigin, this._tmpVecA);
      const fwdX = Math.sin(prevYaw), fwdZ = Math.cos(prevYaw);
      const leftX = Math.cos(prevYaw), leftZ = -Math.sin(prevYaw);
      const spread = Number(f.spreadBias) || 0;
      const latMag = 11.5 + (f.indexInBurst % 3) * 3.0 + Math.abs(spread) * 0.6;
      const vx = fwdX * (st.speedMps * 0.32) + leftX * (f.sideSign * latMag);
      const vy = -6.8 - (f.indexInBurst % 2) * 2.0 + spread * 0.45;
      const vz = fwdZ * (st.speedMps * 0.32) + leftZ * (f.sideSign * latMag);
      vfxEngine?.spawnFlareParticle?.(origin, { x: vx, y: vy, z: vz }, simNowSec);
      st.lastEvents.push({ type: 'flare_ejected', pos: { ...origin } });
    }

    // 2. 坠毁态
    if (st.flightState === 'crashed') {
      st.speedMps = 0;
      st.yawRate = 0;
      st.afterburnerActive = false;
      bridge?.syncAfterburnerAudio?.(st.unit.id, pos, false);
      if (st.wingtipVortexActive || st.wingtipFadeAlpha > 0) {
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
      }
      st.wreckBurnAge = (st.wreckBurnAge || 0) + dt;
      // 35s 燃烧衰减上限：前 25s 维持黑烟柱与底层跳跃明火，25-35s 逐步熄灭，35s 后停发保护粒子池槽位
      if (st.wreckBurnAge <= 35.0) {
        const decayT = st.wreckBurnAge <= 25.0 ? 0.0 : clamp((st.wreckBurnAge - 25.0) / 10.0, 0.0, 1.0);
        const emitInterval = lerp(0.15, 0.38, decayT);
        const flameStrength = lerp(1.25, 0.0, decayT);
        const smokeScale = lerp(1.42, 0.85, decayT);
        st.damageSmokeTimer += dt;
        if (st.damageSmokeTimer >= emitInterval) {
          st.damageSmokeTimer = 0;
          vfxEngine?.spawnDamageSmokePuff?.(pos, flameStrength > 0.12, {
            isCrashWreck: true,
            windVec: { x: 3.2, z: 1.3 },
            flameIntensity: flameStrength,
            scale: smokeScale,
            simNowSec,
          });
        }
      }
      this._stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge, targets, onTargetImpact });
      this._syncUnitAttitude(st);
      return st;
    }

    if (st.flightState === 'crashing') {
      st.afterburnerActive = false;
      bridge?.syncAfterburnerAudio?.(st.unit.id, pos, false);
      if (st.wingtipVortexActive || st.wingtipFadeAlpha > 0) {
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
      }
      st.runTimer += dt;
      st.speedMps = lerp(st.speedMps, 76, expAlpha(2.5, dt));
      st.pitchDeg = lerp(st.pitchDeg, -36, expAlpha(3.2, dt));
      const spinRateDeg = lerp(55, 185, clamp(st.runTimer / 2.2, 0, 1));
      st.crashSpinDeg = (st.crashSpinDeg + spinRateDeg * dt) % 360;
      st.rollDeg = st.crashSpinDeg;

      const crashYawRate = 0.35 + Math.sin(st.runTimer * 7.5) * 0.16;
      const yaw = wrapAngleRad(prevYaw + crashYawRate * dt);
      st.unit.node.rotation.y = yaw;
      st.yawRate = crashYawRate;

      const pitchRad = st.pitchDeg * DEG2RAD;
      const vxFall = Math.sin(yaw) * st.speedMps * Math.cos(pitchRad);
      const vyFall = st.speedMps * Math.sin(pitchRad);
      const vzFall = Math.cos(yaw) * st.speedMps * Math.cos(pitchRad);
      pos.x += vxFall * dt;
      pos.z += vzFall * dt;
      pos.y += vyFall * dt;

      if (!st.ejectionTriggered && st.runTimer >= 0.25 && (pos.y - groundUnder) > 45) {
        st.ejectionTriggered = true;
        const sz = st.spec.size || [17.6, 4.0, 16.2];
        this._tmpEjectOrigin[0] = 0;
        this._tmpEjectOrigin[1] = (sz[1] || 4.0) * 0.78;
        this._tmpEjectOrigin[2] = (sz[2] || 16.2) * 0.24;
        const ejPos = localRigToWorld(st.unit, this._tmpEjectOrigin, this._tmpVecB);
        const leftX = Math.cos(yaw);
        const leftZ = -Math.sin(yaw);
        this._tmpEjectVel.x = vxFall * 0.45 + leftX * 4.5;
        this._tmpEjectVel.y = 26.0;
        this._tmpEjectVel.z = vzFall * 0.45 + leftZ * 4.5;
        vfxEngine?.spawnEjectionArc?.(ejPos, this._tmpEjectVel, simNowSec);
        st.lastEvents.push({ type: 'pilot_ejected', pos: { x: ejPos.x, y: ejPos.y, z: ejPos.z } });
      }

      this._tmpVel.x = vxFall;
      this._tmpVel.y = vyFall;
      this._tmpVel.z = vzFall;
      audioBridge?.updateFallingLoop?.(st.unit.id, pos, this._tmpVel);
      const smokePos = localRigToWorld(st.unit, st.spec.hardpoints.damageSmokeOrigin, this._tmpVecA);
      // 池 5 连续炭黑烟带 + 位移插值重叠浓烟，杜绝高速下坠呈现离散串珠黑球
      vfxEngine?.emitCrashSmokeRibbon?.(`${st.unit.id}_crash`, smokePos, simNowSec);
      const prevSp = st.lastCrashSmokePos;
      if (!prevSp) {
        st.lastCrashSmokePos = { x: smokePos.x, y: smokePos.y, z: smokePos.z };
        vfxEngine?.spawnDamageSmokePuff?.(smokePos, true, {
          windVec: { x: 2.0, z: 0.8 },
          flameIntensity: 1.05,
          scale: 1.28,
          simNowSec,
        });
      } else {
        const segDist = Math.hypot(smokePos.x - prevSp.x, smokePos.y - prevSp.y, smokePos.z - prevSp.z);
        if (segDist >= 1.8) {
          const steps = Math.min(3, Math.max(1, Math.floor(segDist / 1.8)));
          for (let si = 1; si <= steps; si += 1) {
            const frac = si / steps;
            this._tmpVecB.x = prevSp.x + (smokePos.x - prevSp.x) * frac;
            this._tmpVecB.y = prevSp.y + (smokePos.y - prevSp.y) * frac;
            this._tmpVecB.z = prevSp.z + (smokePos.z - prevSp.z) * frac;
            vfxEngine?.spawnDamageSmokePuff?.(this._tmpVecB, si === steps, {
              windVec: { x: 2.0, z: 0.8 },
              flameIntensity: si === steps ? 1.05 : 0.45,
              scale: 1.22,
              simNowSec,
            });
          }
          prevSp.x = smokePos.x;
          prevSp.y = smokePos.y;
          prevSp.z = smokePos.z;
        }
      }

      const gy = terrainSupport?.heightAt?.(pos.x, pos.z) ?? groundUnder;
      if (pos.y <= gy + 2.5) {
        pos.y = gy + 0.6;
        st.flightState = 'crashed';
        st.crashed = true;
        st.wreckBurnAge = 0;
        st.damageSmokeTimer = 0;
        st.lastCrashSmokePos = null;
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
        st.unit.alive = false;
        if (st.unit.node) st.unit.node.visible = false;
        audioBridge?.stopFallingLoop?.(st.unit.id);
        audioBridge?.scheduleWorldEvent?.('Death_Exp_BigTurbine_Plane', pos, { gain: 1.0, shakeTier: 'crash', powerTier: 'massive' });
        audioBridge?.scheduleWorldEvent?.('Death_Exp_Vehicles_Small', pos, { delayOffsetMs: 55, gain: 0.78, shakeTier: 'heavy', powerTier: 'heavy' });
        vfxEngine?.spawnExplosion?.(pos, 'heavy', { x: 0, y: 1, z: 0 }, simNowSec);
        this._tmpSkidDir.x = Math.sin(yaw);
        this._tmpSkidDir.z = Math.cos(yaw);
        vfxEngine?.spawnCrashSkidTrail?.(pos, this._tmpSkidDir, simNowSec);
        st.lastEvents.push({ type: 'crashed', pos: { x: pos.x, y: pos.y, z: pos.z } });
      }

      this._stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge, targets, onTargetImpact });
      this._syncUnitAttitude(st);
      return st;
    }

    const cruiseSpd = st.spec.gameStats?.cruiseSpeedMps || st.arch.cruiseSpeedMps;
    const attackSpd = st.spec.gameStats?.attackSpeedMps || st.arch.attackSpeedMps;
    const cruiseAoaBase = st.arch.cruiseAoaDeg ?? 2.8;
    const lowAltFactor = clamp(1.32 - Math.max(0, pos.y - groundUnder) / 280, 0.45, 1.25);
    const turbPhase = simNowSec * 1.65 + (st.turbulenceSeed || 0);
    const turbRollDeg = (Math.sin(turbPhase) * 0.55 + Math.cos(turbPhase * 1.93) * 0.28) * lowAltFactor;
    const turbHeaveM = (Math.sin(turbPhase * 0.85) * 0.22 + Math.cos(turbPhase * 1.47) * 0.12) * lowAltFactor;
    const aheadGroundMaxY = sampleAheadMaxTerrainY(terrainSupport, pos.x, pos.z, prevYaw, st.speedMps);

    // 真实固定翼滚转先于偏航（Roll-Lead-Yaw）：机翼尚未压到位时限制瞬间硬拐，小修正保持高响应
    const computeCoordinatedYawStep = (yawDiff, maxRate) => {
      const absDiff = Math.abs(yawDiff);
      if (absDiff < 12 * DEG2RAD) {
        return clamp(yawDiff, -maxRate * dt, maxRate * dt);
      }
      const wantSign = yawDiff >= 0 ? 1 : -1;
      const bankAlign = clamp((st.rollDeg * wantSign) / 24, 0, 1);
      const authority = lerp(0.42, 1.0, bankAlign);
      return clamp(yawDiff, -maxRate * authority * dt, maxRate * authority * dt);
    };

    let targetPitchDeg = cruiseAoaBase;
    let targetRollDeg = 0;
    let targetSpeed = cruiseSpd;
    let isPullUpHighG = false;

    if (st.flightState === 'rtb') {
      // 战区撤离（RTB）：全速加速爬升并沿最短边界飞离 9km 战场
      st.rtbTimer = (st.rtbTimer || 0) + dt;
      if (!st.rtbTarget) {
        st.rtbTarget = { x: pos.x >= 0 ? BATTLEFIELD_EXIT_TARGET_EXTENT : -BATTLEFIELD_EXIT_TARGET_EXTENT, z: pos.z };
      }
      const dx = st.rtbTarget.x - pos.x;
      const dz = st.rtbTarget.z - pos.z;
      const desiredYaw = Math.atan2(dx, dz);
      const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
      const yawStep = computeCoordinatedYawStep(yawDiff, st.arch.maxTurnRateRad * 1.08);
      const nextYaw = wrapAngleRad(prevYaw + yawStep);
      st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
      st.unit.node.rotation.y = nextYaw;
      const desiredTurnBank = Math.abs(yawDiff) > 12 * DEG2RAD
        ? clamp(yawDiff * RAD2DEG * 0.95, -44, 44)
        : clamp(st.yawRate * RAD2DEG * 0.95, -44, 44);
      targetRollDeg = Math.abs(yawDiff) > 4 * DEG2RAD ? desiredTurnBank : turbRollDeg * 0.4;

      const maxExitSpd = Math.max(attackSpd * 1.15, cruiseSpd * 1.38);
      targetSpeed = maxExitSpd;
      st.speedMps = lerp(st.speedMps, targetSpeed, expAlpha(2.8, dt));

      const exitAlt = Math.max((st.spec.gameStats?.highAltitudeM || st.arch.highAltitudeM) + 140, groundUnder + 260);
      const altErr = exitAlt - pos.y;
      targetPitchDeg = clamp(cruiseAoaBase + altErr * 0.14, -4, 15);
      const climbRad = clamp(altErr * 0.008, -0.05, 0.24);
      pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(climbRad) * dt;
      pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(climbRad) * dt;
      pos.y = Math.min(exitAlt + 20, lerp(pos.y, exitAlt, expAlpha(1.1, dt)) + st.speedMps * Math.sin(climbRad) * dt * 0.35);

      if (st.spec.capabilities.afterburner || st.spec.capabilities.sonicBoom) {
        this.triggerAfterburnerAndSonicBoom(st.unit, simNowSec, { vfxEngine, audioBridge: bridge });
      }

      if (
        Math.abs(pos.x) >= BATTLEFIELD_EXIT_HALF_EXTENT
        || Math.abs(pos.z) >= BATTLEFIELD_EXIT_HALF_EXTENT
        || st.rtbTimer >= 45.0
      ) {
        this._stopStrafeAudioIfNeeded(st, bridge);
        bridge?.syncAfterburnerAudio?.(st.unit.id, pos, false);
        st.afterburnerActive = false;
        st.evacuated = true;
        st.unit.evacuated = true;
        st.flightState = 'evacuated';
        st.rtbTarget = null;
        st.speedMps = 0;
        st.yawRate = 0;
        st.wingtipVortexActive = false;
        st.wingtipFadeAlpha = 0;
        st.muzzleGlow?.reset?.();
        vfxEngine?.fadeWingtipVortex?.(st.unit.id, 0);
        if (st.unit.node) st.unit.node.visible = false;
        if (st.unit.labelEl) st.unit.labelEl.style.display = 'none';
        st.unit.labelAnchorOffset = null;
        st.lastEvents.push({ type: 'evacuated', unitId: st.unit.id, pos: { x: pos.x, y: pos.y, z: pos.z } });
        this._syncUnitAttitude(st);
        onUnitEvacuated?.(st.unit, st);
        this._stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge: bridge, targets, onTargetImpact });
        return st;
      }
    } else if (st.flightState === 'sprint') {
      // 高速直飞转场：最高速度直线切入新盘旋圈几何切点，临近切点平滑降速自动切入标准盘旋
      st.runTimer += dt;
      const oc = st.orbitCenter;
      const r = clamp(st.orbitRadius, st.arch.minOrbitRadiusM, st.arch.maxOrbitRadiusM);
      const entryPt = computeOrbitEntryPoint(pos, oc, r);
      st.sprintEntryPoint = entryPt;
      const dx = entryPt.x - pos.x;
      const dz = entryPt.z - pos.z;
      const distEntry = Math.hypot(dx, dz);
      const distCenter = Math.hypot(pos.x - oc.x, pos.z - oc.z);
      const desiredYaw = Math.atan2(dx, dz);
      const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
      const nextYaw = wrapAngleRad(prevYaw + computeCoordinatedYawStep(yawDiff, st.arch.maxTurnRateRad * 1.15));
      st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
      st.unit.node.rotation.y = nextYaw;
      targetRollDeg = Math.abs(yawDiff) > 12 * DEG2RAD
        ? clamp(yawDiff * RAD2DEG * 0.92, -st.arch.maxBankDeg, st.arch.maxBankDeg)
        : (Math.abs(yawDiff) > 3 * DEG2RAD
          ? clamp(st.yawRate * RAD2DEG * 0.95, -st.arch.maxBankDeg, st.arch.maxBankDeg)
          : turbRollDeg * 0.55);

      const maxSprintSpd = Math.max(attackSpd * 1.12, cruiseSpd * 1.32);
      const decelRatio = clamp((distEntry - 36) / 130, 0, 1);
      targetSpeed = lerp(cruiseSpd, maxSprintSpd, decelRatio);
      st.speedMps = lerp(st.speedMps, targetSpeed, expAlpha(3.4, dt));

      const desiredAlt = Math.max(st.orbitAltitude, groundUnder + 120);
      targetPitchDeg = clamp(cruiseAoaBase * 0.75 + (desiredAlt - pos.y) * 0.14, -12, 13);
      st.heaveY = turbHeaveM * 0.65;
      pos.x += Math.sin(nextYaw) * st.speedMps * dt;
      pos.z += Math.cos(nextYaw) * st.speedMps * dt;
      pos.y = lerp(pos.y, desiredAlt, expAlpha(2.0, dt));

      // 超音速具备加力能力的机型在高速冲刺段自动点亮加力尾焰与跨音速音爆
      if (decelRatio > 0.45 && (st.spec.capabilities.afterburner || st.spec.capabilities.sonicBoom)) {
        this.triggerAfterburnerAndSonicBoom(st.unit, simNowSec, { vfxEngine, audioBridge: bridge });
      }

      if (distEntry <= 38 || (Math.abs(distCenter - r) <= 18 && distEntry <= 62)) {
        st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
        st.sprintEntryPoint = null;
        st.orbitPhase = Math.atan2(pos.z - oc.z, pos.x - oc.x);
        st.unit.orbitPhase = st.orbitPhase;
      }
    } else if (st.flightState === 'approach') {
      st.runTimer += dt;
      const dx = st.orbitCenter.x - pos.x;
      const dz = st.orbitCenter.z - pos.z;
      const dist = Math.hypot(dx, dz);
      const desiredYaw = Math.atan2(dx, dz);
      const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
      const nextYaw = wrapAngleRad(prevYaw + computeCoordinatedYawStep(yawDiff, st.arch.maxTurnRateRad));
      st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
      st.unit.node.rotation.y = nextYaw;
      targetRollDeg = Math.abs(yawDiff) > 12 * DEG2RAD
        ? clamp(yawDiff * RAD2DEG * 0.85, -st.arch.maxBankDeg, st.arch.maxBankDeg)
        : (Math.abs(yawDiff) > 3 * DEG2RAD
          ? clamp(st.yawRate * RAD2DEG * 0.90, -st.arch.maxBankDeg, st.arch.maxBankDeg)
          : turbRollDeg * 0.6);

      targetSpeed = cruiseSpd + 8;
      st.speedMps = lerp(st.speedMps, targetSpeed, expAlpha(2.2, dt));
      const desiredAlt = Math.max(st.orbitAltitude, groundUnder + 130);
      targetPitchDeg = clamp(cruiseAoaBase + (desiredAlt - pos.y) * 0.14, -12, 13);
      st.heaveY = turbHeaveM * 0.7;
      pos.x += Math.sin(nextYaw) * st.speedMps * dt;
      pos.z += Math.cos(nextYaw) * st.speedMps * dt;
      pos.y = lerp(pos.y, desiredAlt, expAlpha(1.8, dt));

      if (dist <= st.orbitRadius + 25 || st.runTimer > 14) {
        st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
        st.orbitPhase = Math.atan2(pos.z - st.orbitCenter.z, pos.x - st.orbitCenter.x);
      }
    } else if (st.flightState === 'orbit' || st.flightState === 'damaged') {
      const oc = st.orbitCenter;
      const r = clamp(st.orbitRadius, st.arch.minOrbitRadiusM, st.arch.maxOrbitRadiusM);

      st.speedMps = lerp(st.speedMps, cruiseSpd, expAlpha(2.5, dt));
      const desiredAlt = Math.max(st.orbitAltitude, aheadGroundMaxY + st.arch.minRecoveryClearanceM, groundUnder + 110);

      // 统一 L1 前视连续引导控制律 + 协调转弯坡度（消除双段位置硬吸附拐折）
      const l1 = computeL1OrbitCommand({
        posX: pos.x,
        posZ: pos.z,
        yaw: prevYaw,
        speedMps: st.speedMps,
        orbitCenter: oc,
        orbitRadius: r,
        minOrbitRadius: st.arch.minOrbitRadiusM,
        maxOrbitRadius: st.arch.maxOrbitRadiusM,
        maxTurnRateRad: st.arch.maxTurnRateRad,
        maxBankDeg: st.arch.maxBankDeg,
        defaultOrbitRadius: st.arch.orbitRadiusM || 150,
        cruiseSpeedMps: cruiseSpd,
      });
      const yawStep = clamp(l1.yawRateCmd * dt, -st.arch.maxTurnRateRad * dt, st.arch.maxTurnRateRad * dt);
      const midYaw = wrapAngleRad(prevYaw + yawStep * 0.5);
      const nextYaw = wrapAngleRad(prevYaw + yawStep);
      st.yawRate = yawStep / Math.max(1e-5, dt);
      st.unit.node.rotation.y = nextYaw;
      pos.x += Math.sin(midYaw) * st.speedMps * dt;
      pos.z += Math.cos(midYaw) * st.speedMps * dt;
      st.orbitPhase = Math.atan2(pos.z - oc.z, pos.x - oc.x);
      st.unit.orbitPhase = st.orbitPhase;

      targetRollDeg = l1.bankDegCmd + turbRollDeg;
      pos.y = lerp(pos.y, desiredAlt, expAlpha(2.0, dt));
      st.heaveY = turbHeaveM;
      // 真实 A-10C 平直厚机翼盘旋保持 +2.6°–3.8° 正迎角（大坡度需略微抬机头补升力）
      const bankLiftAoa = clamp((Math.abs(st.rollDeg) - 18) / 34, 0, 1) * 1.05;
      targetPitchDeg = clamp(cruiseAoaBase + bankLiftAoa + (desiredAlt - pos.y) * 0.18, -10, 12);
    } else if (['strafe', 'rocket', 'bomb', 'missile'].includes(st.flightState)) {
      st.runTimer += dt;
      const plan = st.activeStrike || this._buildStrikePlan(st, { cmd: st.flightState, targetPos: st.targetPos, terrainSupport });
      st.activeStrike = plan;

      const tx = plan.targetPos.x;
      const tz = plan.targetPos.z;
      const ty = terrainSupport?.heightAt?.(tx, tz) ?? plan.targetPos.y;
      const safeMinY = Math.max(groundUnder, aheadGroundMaxY, ty) + st.arch.minRecoveryClearanceM;

      // 阶段 A：transit — 真实无瞬移拉圆弧飞向走廊延长线入口点 Entry Point
      if (plan.phase === 'transit') {
        const wT = this._getWeapon(st, plan.weaponId);
        const ex = plan.entryPoint.x - pos.x;
        const ez = plan.entryPoint.z - pos.z;
        const distEntry = Math.hypot(ex, ez);
        const desiredYaw = Math.atan2(ex, ez);
        const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
        const nextYaw = wrapAngleRad(prevYaw + clamp(yawDiff, -st.arch.maxTurnRateRad * 1.15 * dt, st.arch.maxTurnRateRad * 1.15 * dt));
        st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
        st.unit.node.rotation.y = nextYaw;
        st.speedMps = lerp(st.speedMps, attackSpd * 0.98, expAlpha(2.6, dt));

        const desiredAlt = Math.max(st.orbitAltitude, ty + 165);
        pos.x += Math.sin(nextYaw) * st.speedMps * dt;
        pos.z += Math.cos(nextYaw) * st.speedMps * dt;
        pos.y = lerp(pos.y, desiredAlt, expAlpha(1.8, dt));
        targetRollDeg = clamp(st.yawRate * RAD2DEG * 1.05, -st.arch.maxBankDeg, st.arch.maxBankDeg);
        targetPitchDeg = clamp((desiredAlt - pos.y) * 0.14, -10, 10);

        const toStartX = (plan.startPos?.x ?? tx) - pos.x;
        const toStartZ = (plan.startPos?.z ?? tz) - pos.z;
        const alongBack = toStartX * plan.attackDir.x + toStartZ * plan.attackDir.z;
        const crossErr = Math.abs(toStartX * plan.attackDir.z - toStartZ * plan.attackDir.x);
        const headingErr = Math.abs(wrapAngleRad(plan.attackYaw - nextYaw));
        const inBackCorridor = (plan.cmd === 'strafe' || plan.cmd === 'rocket')
          && alongBack >= Math.max((wT?.minRangeM || 160) + 220, 400)
          && crossErr <= 52
          && headingErr <= 30 * DEG2RAD;

        if (distEntry <= 78 || inBackCorridor || st.runTimer > 18) {
          plan.phase = (plan.cmd === 'strafe' || plan.cmd === 'rocket') ? 'align' : 'run';
          plan.alignStableTimer = 0;
          st.runTimer = 0;
        }
      } else if (plan.phase === 'align') {
        // 阶段 A2：align — 改平机翼 + 机头严格锁轴稳定段（Roll-in -> Wings Level & Alignment，未对准绝不开火）
        const wA = this._getWeapon(st, plan.weaponId);
        const ax = (plan.startPos?.x ?? tx) - pos.x;
        const az = (plan.startPos?.z ?? tz) - pos.z;
        const distStart = Math.hypot(ax, az);
        const crossErr = Math.abs(ax * plan.attackDir.z - az * plan.attackDir.x);
        // 渐进贴合中轴：先修横偏再锁攻击轴向，确保进入开火窗时机翼已基本改平
        const aimAheadX = (plan.startPos?.x ?? tx) - plan.attackDir.x * clamp(distStart * 0.25, 0, 160);
        const aimAheadZ = (plan.startPos?.z ?? tz) - plan.attackDir.z * clamp(distStart * 0.25, 0, 160);
        const desiredYaw = Math.atan2(aimAheadX - pos.x, aimAheadZ - pos.z);
        const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
        const nextYaw = wrapAngleRad(prevYaw + clamp(yawDiff, -st.arch.maxTurnRateRad * 1.25 * dt, st.arch.maxTurnRateRad * 1.25 * dt));
        st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
        st.unit.node.rotation.y = nextYaw;

        st.speedMps = lerp(st.speedMps, attackSpd, expAlpha(3.0, dt));
        const axisYawErr = Math.abs(wrapAngleRad(plan.attackYaw - nextYaw));
        const startYawErr = Math.abs(wrapAngleRad(Math.atan2(ax, az) - nextYaw));
        const nearAligned = startYawErr <= 10 * DEG2RAD && axisYawErr <= 16 * DEG2RAD;
        // 越接近对准越强力压平机翼（Wings Level）
        targetRollDeg = nearAligned
          ? clamp(st.yawRate * RAD2DEG * 0.55, -10, 10)
          : clamp(st.yawRate * RAD2DEG * 0.90, -34, 34);
        targetPitchDeg = nearAligned ? -9 : -4;
        const diveRad = targetPitchDeg * DEG2RAD;
        pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
        pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
        pos.y = Math.max(safeMinY + 12, pos.y + st.speedMps * Math.sin(diveRad) * dt);

        const wingsLevelAndLocked = startYawErr <= 8 * DEG2RAD
          && Math.abs(st.rollDeg) <= 13
          && crossErr <= 44;
        if (wingsLevelAndLocked) {
          plan.alignStableTimer = (plan.alignStableTimer || 0) + dt;
        } else {
          plan.alignStableTimer = Math.max(0, (plan.alignStableTimer || 0) - dt * 1.5);
        }

        const minR = wA?.minRangeM || 160;
        const maxR = wA?.maxRangeM || 1000;
        if (plan.alignStableTimer >= 0.18 && distStart <= maxR + 80) {
          plan.phase = 'run';
          st.runTimer = 0;
        } else if (distStart < minR + 75 && !wingsLevelAndLocked) {
          // 距离过近且尚未改平对准：严格拟真复飞绕弧重入（Go-around），绝不歪着机身斜射出框
          if ((plan.goAroundCount || 0) < 2) {
            plan.goAroundCount = (plan.goAroundCount || 0) + 1;
            plan.phase = 'transit';
            plan.alignStableTimer = 0;
            st.runTimer = 0;
          } else {
            plan.phase = 'run';
            st.runTimer = 0;
          }
        } else if (st.runTimer > 9.0) {
          plan.phase = 'run';
          st.runTimer = 0;
        }
      } else {
        // 阶段 B：run — 对准目标并按该武器真实 [minRangeM, maxRangeM] 执行俯冲扫射/齐射/投弹/导弹
        const curDropPt = plan.cmd === 'bomb' && Array.isArray(plan.dropPoints) && plan.dropPoints.length > 0
          ? plan.dropPoints[Math.min(plan.nextDropIdx || 0, plan.dropPoints.length - 1)]
          : null;
        const curMissilePt = plan.cmd === 'missile' && Array.isArray(plan.missileTargets) && plan.missileTargets.length > 0
          ? plan.missileTargets[Math.min(plan.nextMissileIdx || 0, plan.missileTargets.length - 1)]
          : null;
        const targetRounds = Math.max(1, plan.plannedStrafeRounds || 102);
        const aimX = plan.cmd === 'strafe'
          ? lerp(plan.startPos.x, plan.endPos.x, clamp(st.strafeRoundsFired / targetRounds, 0, 1))
          : (curDropPt ? curDropPt.x : (curMissilePt ? curMissilePt.x : tx));
        const aimZ = plan.cmd === 'strafe'
          ? lerp(plan.startPos.z, plan.endPos.z, clamp(st.strafeRoundsFired / targetRounds, 0, 1))
          : (curDropPt ? curDropPt.z : (curMissilePt ? curMissilePt.z : tz));
        const dx = aimX - pos.x;
        const dz = aimZ - pos.z;
        const horizDist = Math.hypot(dx, dz);
        const desiredYaw = Math.atan2(dx, dz);
        const yawDiff = wrapAngleRad(desiredYaw - prevYaw);
        const nextYaw = wrapAngleRad(prevYaw + clamp(yawDiff, -st.arch.maxTurnRateRad * 1.35 * dt, st.arch.maxTurnRateRad * 1.35 * dt));
        st.yawRate = wrapAngleRad(nextYaw - prevYaw) / dt;
        st.unit.node.rotation.y = nextYaw;
        const aligned = Math.abs(yawDiff) < 22 * DEG2RAD;

        const w = this._getWeapon(st, plan.weaponId);
        if (!w) {
          this._stopStrafeAudio(st, bridge);
          this._enterEgress(st, simNowSec, bridge);
        } else if (plan.cmd === 'strafe') {
          st.speedMps = lerp(st.speedMps, attackSpd, expAlpha(3.2, dt));
          const strafeAligned = Math.abs(yawDiff) <= 11 * DEG2RAD && Math.abs(st.rollDeg) <= 16;
          // 真实 LAS 低角扫射：-12° 稳定改平俯冲接近，触及安全高程附近自动改平拉扫直至完整扫完画出的区域
          const nearFloor = pos.y <= safeMinY + 14;
          targetPitchDeg = nearFloor
            ? (pos.y <= safeMinY + 4 ? 1.5 : -1.5)
            : (strafeAligned && horizDist < (w.maxRangeM + 100) ? -12 : -6);
          targetRollDeg = strafeAligned
            ? clamp(st.yawRate * RAD2DEG * 0.55, -12, 12)
            : clamp(st.yawRate * RAD2DEG * 0.85, -30, 30);
          const diveRad = targetPitchDeg * DEG2RAD;
          pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.y = Math.max(safeMinY, pos.y + st.speedMps * Math.sin(diveRad) * dt);

          const axisLen = Math.max(60, plan.axisLen || 120);
          const halfW = Math.max(4, plan.strafeHalfWidthM || 10);
          const perpX = -(plan.attackDir?.z || 0);
          const perpZ = plan.attackDir?.x || 1;
          const endHorizDist = Math.hypot(plan.endPos.x - pos.x, plan.endPos.z - pos.z);
          const inEnvelope = strafeAligned
            && horizDist <= w.maxRangeM
            && (horizDist >= w.minRangeM || endHorizDist >= w.minRangeM - 15)
            && pos.y >= safeMinY - 2;

          if (inEnvelope && w && w.ammo > 0 && st.strafeRoundsFired < targetRounds) {
            // 现实 GAU-8/A 约 45 kN 后坐力（相当于单台 TF34 推力）：开火时轻微减速并叠加机身高频微震
            st.strafeRecoilActive = true;
            st.speedMps = lerp(st.speedMps, Math.max(cruiseSpd * 0.92, attackSpd - 7.5), expAlpha(4.2, dt));
            targetPitchDeg += Math.sin(simNowSec * 52.0) * 0.36;
            targetRollDeg += Math.cos(simNowSec * 37.0) * 0.24;
            if (!st.strafeAudioActive) {
              st.strafeAudioActive = true;
              st.strafeBurstId += 1;
              const mPos = localRigToWorld(st.unit, w.muzzleLocal, this._tmpPos);
              audioBridge?.startGau8Burst?.(st.unit.id, mPos, st.strafeBurstId, w.gunAcousticPreset);
            }
            const mPosNow = localRigToWorld(st.unit, w.muzzleLocal, this._tmpPos);
            this._tmpVel.x = Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad);
            this._tmpVel.y = st.speedMps * Math.sin(diveRad);
            this._tmpVel.z = Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad);
            audioBridge?.updateGau8Burst?.(st.unit.id, mPosNow, this._tmpVel);

            const strafeDur = clamp(axisLen / Math.max(70, attackSpd * 0.72), 1.0, 2.2);
            const neededBursts = Math.max(14, Math.round(strafeDur / Math.max(0.025, w.cooldownSec)));
            const burstStep = clamp(Math.ceil(targetRounds / neededBursts), 3, 8);
            const tracerEveryN = Math.max(1, Math.round(Number(w.tracerEveryN) || 5));
            st.strafeFireTimer += dt;
            while (st.strafeFireTimer >= w.cooldownSec && w.ammo > 0 && st.strafeRoundsFired < targetRounds) {
              st.strafeFireTimer -= w.cooldownSec;
              const remainRounds = targetRounds - st.strafeRoundsFired;
              const consume = Math.min(w.ammo, remainRounds, burstStep);
              w.ammo -= consume;
              const prevFired = st.strafeRoundsFired;
              st.strafeRoundsFired += consume;
              this._syncUnitWeaponSummary(st);

              const mPos = localRigToWorld(st.unit, w.muzzleLocal, this._tmpPos);
              const planeVel = this._tmpVel;
              // 每次脉冲拆分为 2-3 发交错高密着弹点（保持总伤害不变，用类高斯横向散布铺满扫射覆盖带，告别单排黑芝麻点）
              const subImpacts = consume >= 5 ? 3 : 2;
              const totalPulseDmg = w.damage * Math.max(1, consume * 0.5);
              const perImpactDmg = Number((totalPulseDmg / subImpacts).toFixed(2));
              for (let sub = 0; sub < subImpacts; sub += 1) {
                st.strafeBurstTick = (st.strafeBurstTick || 0) + 1;
                const subFrac = (prevFired + consume * ((sub + 0.5) / subImpacts)) / targetRounds;
                const tAxis = clamp(subFrac, 0, 1);
                const sKey = st.strafeBurstTick * 1.73 + sub * 5.91;
                // 类高斯双频求和：中心弹道密集撕裂，外缘自然散布覆盖 ±halfW 宽带
                const gaussLat = (Math.sin(sKey * 2.137) + Math.cos(sKey * 3.913)) * 0.5;
                const latOffset = gaussLat * (halfW * 0.94);
                const longJitter = Math.sin(sKey * 5.17) * 3.4;
                const hitX = lerp(plan.startPos.x, plan.endPos.x, tAxis) + perpX * latOffset + (plan.attackDir?.x || 0) * longJitter;
                const hitZ = lerp(plan.startPos.z, plan.endPos.z, tAxis) + perpZ * latOffset + (plan.attackDir?.z || 0) * longJitter;
                const hitY = (terrainSupport?.heightAt?.(hitX, hitZ) ?? ty) + 0.3;

                // 4:1 Combat Mix 节律：每 tracerEveryN(默认5) 发 1 发高亮 HDR 曳光 + HEI 高爆溅射，其余为 API 贫铀自燃穿甲 + Fumer 气痕
                const isHeiTracerTick = ((st.strafeBurstTick - 1) % tracerEveryN) === 0;
                if (isHeiTracerTick) {
                  st.strafeTracerSeq = (st.strafeTracerSeq || 0) + 1;
                }
                const tracerIdx = isHeiTracerTick ? (st.strafeTracerSeq - 1) : st.strafeBurstTick;
                const staggerOriginM = sub * 11.5;
                const dirX = hitX - mPos.x;
                const dirY = hitY - mPos.y;
                const dirZ = hitZ - mPos.z;
                const dLen = Math.max(1, Math.hypot(dirX, dirY, dirZ));
                const oX = dLen > 50 ? (mPos.x + (dirX / dLen) * staggerOriginM) : mPos.x;
                const oY = dLen > 50 ? (mPos.y + (dirY / dLen) * staggerOriginM) : mPos.y;
                const oZ = dLen > 50 ? (mPos.z + (dirZ / dLen) * staggerOriginM) : mPos.z;

                this._spawnProjectile(st, {
                  kind: 'tracer30mm',
                  weapon: w,
                  damageOverride: perImpactDmg,
                  explosionTier: isHeiTracerTick ? 'light' : 'light_api',
                  impactAudioKey: (sub === 0 && (isHeiTracerTick || st.strafeBurstTick % 2 === 0)) ? (w.impactAudioKey || '') : '',
                  tracerColor: w.tracerColor || 'red',
                  isTracer: isHeiTracerTick,
                  origin: { x: oX, y: oY, z: oZ },
                  target: { x: hitX, y: hitY, z: hitZ },
                  speed: w.projectileSpeedMps || 1010,
                  roundIndex: tracerIdx,
                  simNowSec,
                });
              }
              vfxEngine?.spawnMuzzleFlash?.(
                mPos,
                { x: Math.sin(nextYaw) * Math.cos(diveRad), y: Math.sin(diveRad), z: Math.cos(nextYaw) * Math.cos(diveRad) },
                simNowSec,
                planeVel,
                `${st.unit.id}_muzzle`,
              );
              st.muzzleGlow?.pulse?.(mPos, 0.88 + ((st.strafeBurstTick % 3) * 0.16));
              audioBridge?.injectMicroShake?.(pos, 'strafe');
            }
          }
          const tCovered = st.strafeRoundsFired / targetRounds;
          if (
            st.strafeRoundsFired >= targetRounds
            || w.ammo <= 0
            || (endHorizDist < w.minRangeM - 15 && tCovered >= 0.85)
            || st.runTimer > 11.0
          ) {
            this._stopStrafeAudioIfNeeded(st, bridge);
            this._enterEgress(st, simNowSec, bridge);
          }
        } else if (plan.cmd === 'rocket') {
          st.speedMps = lerp(st.speedMps, attackSpd * 0.96, expAlpha(2.8, dt));
          targetPitchDeg = aligned && horizDist < (w.maxRangeM + 100) ? -19 : -8;
          targetRollDeg = clamp(st.yawRate * RAD2DEG * 0.9, -40, 40);
          const diveRad = targetPitchDeg * DEG2RAD;
          pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.y = Math.max(safeMinY + 18, pos.y + st.speedMps * Math.sin(diveRad) * dt);

          const inEnvelope = aligned && horizDist <= w.maxRangeM && horizDist >= w.minRangeM;
          if (inEnvelope && w && w.ammo > 0 && st.rocketsFiredInRun < w.salvoCount) {
            st.rocketFireTimer += dt;
            this._tmpVel.x = Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad);
            this._tmpVel.y = st.speedMps * Math.sin(diveRad);
            this._tmpVel.z = Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad);
            const planeVel = this._tmpVel;
            let frameSalvoIdx = 0;
            while (st.rocketFireTimer >= w.cooldownSec && w.ammo > 0 && st.rocketsFiredInRun < w.salvoCount) {
              st.rocketFireTimer -= w.cooldownSec;
              w.ammo -= 1;
              st.rocketsFiredInRun += 1;
              const pylon = this._consumePylon(st, w);
              this._syncUnitWeaponSummary(st);

              if (pylon?.pos) {
                this._tmpLocalOrigin[0] = pylon.pos[0];
                this._tmpLocalOrigin[1] = pylon.pos[1];
                this._tmpLocalOrigin[2] = pylon.pos[2] + 0.95;
              } else {
                const podSide = st.rocketsFiredInRun % 2 === 0 ? 1 : -1;
                this._tmpLocalOrigin[0] = podSide * 4.71;
                this._tmpLocalOrigin[1] = 1.63;
                this._tmpLocalOrigin[2] = -0.56 + 0.95;
              }
              const origin = localRigToWorld(st.unit, this._tmpLocalOrigin, this._tmpVecA);
              const salvoOffsetMs = frameSalvoIdx * Math.round((w.cooldownSec || 0.11) * 1000);
              bridge?.scheduleWorldEvent?.(w.fireAudioKey, origin, {
                delayOffsetMs: salvoOffsetMs,
                gain: 0.85,
                powerTier: 'medium',
                dedupKey: 'hydra70_launch',
                minIntervalMs: frameSalvoIdx === 0 ? 40 : 0,
              });
              frameSalvoIdx += 1;
              const tAxis = (st.rocketsFiredInRun - 1) / Math.max(1, w.salvoCount - 1);
              const hitX = lerp(plan.startPos.x, plan.endPos.x, tAxis) + Math.sin(st.rocketsFiredInRun * 1.9) * 7.5;
              const hitZ = lerp(plan.startPos.z, plan.endPos.z, tAxis) + Math.cos(st.rocketsFiredInRun * 2.7) * 7.5;
              const hitY = (terrainSupport?.heightAt?.(hitX, hitZ) ?? ty) + 0.3;
              this._spawnProjectile(st, {
                kind: 'rocket',
                weapon: w,
                origin: { ...origin },
                target: { x: hitX, y: hitY, z: hitZ },
                launchSpeed: Math.max(85, st.speedMps),
                speed: w.projectileSpeedMps || 450,
                boostAccelSec: w.boostAccelSec ?? 0.65,
                guided: true,
                simNowSec,
              });
              vfxEngine?.spawnMissileLaunchSmoke?.(origin, { x: Math.sin(nextYaw), y: Math.sin(diveRad), z: Math.cos(nextYaw) }, simNowSec, planeVel);
            }
          }
          if (st.rocketsFiredInRun >= w.salvoCount || horizDist < w.minRangeM - 10 || st.runTimer > 9.0) {
            this._enterEgress(st, simNowSec, bridge);
          }
        } else if (plan.cmd === 'bomb') {
          st.speedMps = lerp(st.speedMps, attackSpd * 0.94, expAlpha(2.5, dt));
          targetPitchDeg = aligned ? -12 : -5;
          targetRollDeg = clamp(st.yawRate * RAD2DEG * 0.85, -38, 38);
          const diveRad = targetPitchDeg * DEG2RAD;
          pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.y = Math.max(safeMinY + 45, pos.y + st.speedMps * Math.sin(diveRad) * dt);

          st.bombDropIntervalTimer = Math.max(0, (st.bombDropIntervalTimer || 0) - dt);
          if (st.bombDroppedInRun) {
            st.bombPostDropTimer = (st.bombPostDropTimer || 0) + dt;
          }

          const dropList = Array.isArray(plan.dropPoints) && plan.dropPoints.length > 0
            ? plan.dropPoints
            : [plan.targetPos];
          const nextIdx = plan.nextDropIdx || 0;
          const activeDrop = nextIdx < dropList.length ? dropList[nextIdx] : null;
          const dropTy = activeDrop ? (terrainSupport?.heightAt?.(activeDrop.x, activeDrop.z) ?? ty) : ty;
          const dy = Math.max(12, pos.y - dropTy);
          const gEff = GRAVITY * (w.gravityScale ?? 1.0);
          const initVy = st.speedMps * Math.sin(diveRad) - 4.0;
          const vDown = Math.max(0, -initVy);
          const tFall = (-vDown + Math.sqrt(vDown * vDown + 2 * gEff * dy)) / Math.max(1.0, gEff);
          const releaseLead = clamp(st.speedMps * Math.cos(diveRad) * tFall * 0.92, w.minRangeM, w.maxRangeM);

          const canDropNow = Boolean(activeDrop)
            && !st.bombDroppedInRun
            && st.bombDropIntervalTimer <= 0
            && w
            && w.ammo > 0
            && ((aligned && horizDist <= releaseLead + 32) || horizDist <= 96);

          if (canDropNow) {
            w.ammo -= 1;
            const pylon = this._consumePylon(st, w);
            this._syncUnitWeaponSummary(st);

            const origin = localRigToWorld(st.unit, pylon?.pos || w.muzzleLocal, this._tmpVecA);
            const planeVel = {
              x: Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad),
              y: initVy,
              z: Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad),
            };
            const bombProj = this._spawnProjectile(st, {
              kind: 'gbu31',
              weapon: w,
              origin: { ...origin },
              target: { x: activeDrop.x, y: dropTy, z: activeDrop.z },
              vel: planeVel,
              gravityScale: w.gravityScale ?? 1.0,
              ballistic: true,
              simNowSec,
            });
            audioBridge?.scheduleWorldEvent?.('BA_DropBomb', origin, { gain: 0.82, powerTier: 'small' });
            audioBridge?.startOrdnanceWhistle?.(bombProj.id, origin, planeVel, {
              key: w.whistleAudioKey || 'BA_Whistle_Bomb_Big',
              maxDurationSec: Math.min(6.5, tFall * 1.15),
            });
            st.lastEvents.push({ type: 'bomb_dropped', weaponId: w.id, pylonIndex: pylon?.index ?? -1, dropIndex: nextIdx });

            plan.nextDropIdx = nextIdx + 1;
            st.bombDropIntervalTimer = 0.22;
            if (plan.nextDropIdx < dropList.length && w.ammo > 0) {
              const upcoming = dropList[plan.nextDropIdx];
              const upY = terrainSupport?.heightAt?.(upcoming.x, upcoming.z) ?? ty;
              plan.targetPos = { x: upcoming.x, y: upY, z: upcoming.z };
              st.targetPos = { ...plan.targetPos };
            } else {
              st.bombDroppedInRun = true;
              st.bombPostDropTimer = 0;
            }
          }

          const maxBombRunSec = 10.0 + Math.max(0, dropList.length - 1) * 2.2;
          if ((st.bombDroppedInRun && st.bombPostDropTimer >= 0.85) || (!activeDrop && st.runTimer > 1.0) || st.runTimer > maxBombRunSec) {
            this._enterEgress(st, simNowSec, bridge);
          }
        } else if (plan.cmd === 'missile') {
          st.speedMps = lerp(st.speedMps, attackSpd * 0.92, expAlpha(2.5, dt));
          targetPitchDeg = aligned ? -9 : -4;
          targetRollDeg = clamp(st.yawRate * RAD2DEG * 0.85, -38, 38);
          const diveRad = targetPitchDeg * DEG2RAD;
          pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad) * dt;
          pos.y = Math.max(safeMinY + 55, pos.y + st.speedMps * Math.sin(diveRad) * dt);

          st.missileFireTimer = Math.max(0, (st.missileFireTimer || 0) - dt);
          if (st.missileFiredInRun) {
            st.missilePostFireTimer = (st.missilePostFireTimer || 0) + dt;
          }

          const mTargets = Array.isArray(plan.missileTargets) && plan.missileTargets.length > 0
            ? plan.missileTargets
            : [plan.targetPos];
          const nextMIdx = plan.nextMissileIdx || 0;
          const activeTarget = nextMIdx < mTargets.length ? mTargets[nextMIdx] : null;
          const mTy = activeTarget ? (terrainSupport?.heightAt?.(activeTarget.x, activeTarget.z) ?? activeTarget.y ?? ty) : ty;
          // 首枚要求对准机头 (< 22°)，同走廊连射后续枚支持 AGM-65 导引头 ±40° 扇区连续锁定发射
          const seekerAligned = nextMIdx === 0 ? aligned : (Math.abs(yawDiff) < 42 * DEG2RAD);

          if (
            !st.missileFiredInRun
            && activeTarget
            && st.missileFireTimer <= 0
            && seekerAligned
            && horizDist <= w.maxRangeM
            && horizDist >= w.minRangeM
            && w
            && w.ammo > 0
          ) {
            w.ammo -= 1;
            const pylon = this._consumePylon(st, w);
            this._syncUnitWeaponSummary(st);

            const origin = localRigToWorld(st.unit, pylon?.pos || w.muzzleLocal, this._tmpVecA);
            const planeVel = {
              x: Math.sin(nextYaw) * st.speedMps * Math.cos(diveRad),
              y: st.speedMps * Math.sin(diveRad),
              z: Math.cos(nextYaw) * st.speedMps * Math.cos(diveRad),
            };
            this._spawnProjectile(st, {
              kind: 'agm65',
              weapon: w,
              origin: { ...origin },
              target: { x: activeTarget.x, y: mTy + 1.0, z: activeTarget.z },
              launchSpeed: Math.max(85, st.speedMps),
              speed: w.projectileSpeedMps || 320,
              boostAccelSec: w.boostAccelSec ?? 1.0,
              guided: true,
              simNowSec,
            });
            vfxEngine?.spawnMissileLaunchSmoke?.(origin, { x: Math.sin(nextYaw), y: Math.sin(diveRad), z: Math.cos(nextYaw) }, simNowSec, planeVel);
            audioBridge?.scheduleWorldEvent?.(w.fireAudioKey, origin, { gain: 0.88, powerTier: 'medium' });
            st.lastEvents.push({
              type: 'missile_fired',
              weaponId: w.id,
              pylonIndex: pylon?.index ?? -1,
              targetIndex: nextMIdx,
              targetPos: { x: activeTarget.x, y: mTy, z: activeTarget.z },
            });

            plan.nextMissileIdx = nextMIdx + 1;
            st.missileFireTimer = w.cooldownSec || 0.40;

            if (plan.nextMissileIdx < mTargets.length && w.ammo > 0) {
              const upcoming = mTargets[plan.nextMissileIdx];
              const upY = terrainSupport?.heightAt?.(upcoming.x, upcoming.z) ?? ty;
              plan.targetPos = { x: upcoming.x, y: upY, z: upcoming.z };
              st.targetPos = { ...plan.targetPos };
            } else {
              st.missileFiredInRun = true;
              st.missilePostFireTimer = 0;
            }
          }

          const tooCloseAbort = !st.missileFiredInRun && horizDist < w.minRangeM - 15;
          if (tooCloseAbort && (plan.nextMissileIdx || 0) < mTargets.length && w && w.ammo > 0) {
            const leftover = mTargets.slice(plan.nextMissileIdx || 0);
            const requeuePlan = this._buildStrikePlan(st, {
              cmd: 'missile',
              weaponId: w.id,
              targetPos: leftover[0],
              missileTargets: leftover,
              terrainSupport,
              forceTransit: true,
            });
            st.strikeQueue.unshift(requeuePlan);
            st.missileFiredInRun = true;
          }

          const maxMissileRunSec = 10.0 + Math.max(0, mTargets.length - 1) * 1.5;
          if (
            (st.missileFiredInRun && st.missilePostFireTimer >= 0.65)
            || (w && w.ammo <= 0)
            || (!activeTarget && st.runTimer > 0.8)
            || tooCloseAbort
            || st.runTimer > maxMissileRunSec
          ) {
            this._enterEgress(st, simNowSec, bridge);
          }
        }
      }
    } else if (st.flightState === 'egress') {
      st.egressTimer += dt;
      isPullUpHighG = st.egressTimer <= 1.65;
      st.speedMps = lerp(st.speedMps, attackSpd * 0.9, expAlpha(2.0, dt));
      const egressSign = st.egressTurnSign >= 0 ? 1 : -1;
      targetPitchDeg = st.egressTimer < 1.8 ? 22 : 10;
      targetRollDeg = egressSign * (st.egressTimer < 1.45 ? 46 : 24);

      const turnRate = egressSign * (st.egressTimer < 1.45 ? 0.88 : 0.48);
      const nextYaw = wrapAngleRad(prevYaw + turnRate * dt);
      st.yawRate = turnRate;
      st.unit.node.rotation.y = nextYaw;

      const climbRad = targetPitchDeg * DEG2RAD;
      pos.x += Math.sin(nextYaw) * st.speedMps * Math.cos(climbRad) * dt;
      pos.z += Math.cos(nextYaw) * st.speedMps * Math.cos(climbRad) * dt;
      pos.y = Math.min(st.orbitAltitude + 26, pos.y + st.speedMps * Math.sin(climbRad) * dt);

      if (st.egressTimer >= 2.6 && pos.y >= st.orbitAltitude - 18) {
        if (st.strikeQueue.length > 0) {
          const nextPlan = st.strikeQueue.shift();
          this._activateStrikePlan(st, nextPlan, { useFastApproach: false, terrainSupport, audioBridge: bridge });
        } else {
          st.activeStrike = null;
          st.flightState = st.damagedSmokeActive ? 'damaged' : 'orbit';
          st.orbitPhase = Math.atan2(pos.z - st.orbitCenter.z, pos.x - st.orbitCenter.x);
        }
      }
    }

    // 轻量能量机动（俯冲借重力微加速，拉起爬升自然掉速）与前方三点地形前瞻防撞改出
    if (!['evacuated', 'crashed', 'crashing'].includes(st.flightState)) {
      const energyDv = computeEnergyManeuverDeltaMps(st.pitchDeg, dt);
      const minFlySpd = cruiseSpd * 0.78;
      const maxFlySpd = Math.max(attackSpd * 1.18, cruiseSpd * 1.42);
      st.speedMps = clamp(st.speedMps + energyDv, minFlySpd, maxFlySpd);

      if (st.flightState !== 'rtb') {
        const lookaheadFloor = aheadGroundMaxY + st.arch.minRecoveryClearanceM;
        if (pos.y < lookaheadFloor + 35) {
          const prox = clamp(1 - (pos.y - lookaheadFloor) / 35, 0, 1);
          const pullBias = lerp(6, 18, prox);
          targetPitchDeg = Math.max(targetPitchDeg, pullBias);
          if (pos.y < lookaheadFloor) {
            pos.y = lerp(pos.y, lookaheadFloor, expAlpha(6.0, dt));
          }
        }
      }
    }

    const clampedPitch = clamp(targetPitchDeg, st.arch.maxDivePitchDeg, st.arch.maxClimbPitchDeg);
    const clampedRoll = clamp(targetRollDeg, -st.arch.maxBankDeg, st.arch.maxBankDeg);
    st.pitchDeg = lerp(st.pitchDeg, clampedPitch, expAlpha(5.2, dt));
    // 滚转角速率限幅（A-10C 平直长机翼约 64°/s 稳重滚转惯性）+ 小误差指数阻尼防抖
    const maxRollStep = (st.arch.maxRollRateDegPerSec || 68) * dt;
    const smoothRollStep = (clampedRoll - st.rollDeg) * expAlpha(5.8, dt);
    st.rollDeg = clamp(st.rollDeg + clamp(smoothRollStep, -maxRollStep, maxRollStep), -st.arch.maxBankDeg, st.arch.maxBankDeg);

    // 2b. 工况分档燃油结算（Q7-A）与动态 Bingo Fuel 自动返航（Q6-A）
    const burnInfo = this.resolveFuelBurnState(st);
    st.fuelBurnRate = burnInfo.burnRate;
    st.fuelModeLabel = burnInfo.modeLabel;
    st.throttleLevel = burnInfo.throttleLevel;
    st.afterburnerActive = burnInfo.afterburnerActive;
    if (burnInfo.burnRate > 0 && !this.fuelConsumptionPaused) {
      const floorSec = st.flightState === 'rtb' ? 0.5 : 0;
      st.fuelRemainSec = Math.max(floorSec, st.fuelRemainSec - dt * burnInfo.burnRate);
    }
    st.fuelPct = Number(clamp((st.fuelRemainSec / Math.max(1, st.fuelMaxSec)) * 100, 0, 100).toFixed(1));
    st.bingoThresholdPct = this.computeDynamicBingoThresholdPct(st);

    if (st.flightState !== 'rtb' && !st.evacuated && !st.crashed) {
      if (st.fuelPct <= st.bingoThresholdPct) {
        st.bingoState = 'rtb_bingo';
        if (!st.bingoAutoTriggered) {
          st.bingoAutoTriggered = true;
          this.issueEvacuate(st.unit, { simNowSec, audioBridge: bridge, reason: 'bingo_fuel', forceLocked: true });
          st.lastEvents.push({
            type: 'bingo_fuel',
            unitId: st.unit.id,
            fuelPct: st.fuelPct,
            bingoThresholdPct: st.bingoThresholdPct,
          });
          onBingoFuel?.(st.unit, st);
        }
      } else if (st.fuelPct <= st.bingoThresholdPct + 12) {
        st.bingoState = 'low';
        if (!st.lowFuelNotified) {
          st.lowFuelNotified = true;
          st.lastEvents.push({
            type: 'low_fuel',
            unitId: st.unit.id,
            fuelPct: st.fuelPct,
            bingoThresholdPct: st.bingoThresholdPct,
          });
        }
      } else {
        st.bingoState = 'nominal';
      }
    } else if (st.bingoLockedRtb) {
      st.bingoState = 'rtb_bingo';
    }

    this._evaluateWinchesterState(st, { simNowSec, audioBridge: bridge, onWinchester });
    bridge?.syncAfterburnerAudio?.(
      st.unit.id,
      pos,
      Boolean(st.afterburnerActive && st.spec.capabilities.afterburner),
    );
    this._syncUnitAttitude(st);

    // 2c. 固定翼尾部喷口热浪折射扭曲推流（SCAV-7 共享单源，零堆分配）
    const shimmerProf = st.spec.heatShimmerProfile;
    if (
      shimmerProf?.enabled
      && vfxEngine?.pushHeatShimmerEmitter
      && st.spec.hardpoints.exhaustsRig.length > 0
      && st.throttleLevel > 0.05
    ) {
      const rigGrp = st.unit.rig?.rigBodyGroup;
      let bx = -Math.sin(st.unit.node.rotation.y || 0);
      let by = 0;
      let bz = -Math.cos(st.unit.node.rotation.y || 0);
      if (rigGrp?.matrixWorld) {
        st.unit.node.updateMatrixWorld?.(true);
        const me = rigGrp.matrixWorld.elements;
        // rigBodyGroup +Z 为机头前向，因此引擎喷口向后排气方向为局部 -Z
        bx = -me[8];
        by = -me[9];
        bz = -me[10];
        const bLen = Math.hypot(bx, by, bz) || 1;
        bx /= bLen;
        by /= bLen;
        bz /= bLen;
      }
      this._tmpExhaustDir.x = bx;
      this._tmpExhaustDir.y = by;
      this._tmpExhaustDir.z = bz;
      const throttleT = st.afterburnerActive ? 1.0 : clamp((st.throttleLevel - 0.5) / 0.5, 0, 1);
      const coneLen = lerp(shimmerProf.cruiseLengthM, shimmerProf.maxLengthM, throttleT) * (st.afterburnerActive ? 1.18 : 1.0);
      const distPx = lerp(shimmerProf.cruiseDistortionPx, shimmerProf.maxDistortionPx, throttleT) * (st.afterburnerActive ? 1.15 : 1.0);
      const flowSpd = shimmerProf.flowSpeed * (st.afterburnerActive ? 1.65 : lerp(0.95, 1.32, throttleT));
      const exList = st.spec.hardpoints.exhaustsRig;
      for (let ei = 0; ei < exList.length; ei += 1) {
        const exWorld = localRigToWorld(st.unit, exList[ei], this._tmpVecA);
        vfxEngine.pushHeatShimmerEmitter(
          exWorld,
          this._tmpExhaustDir,
          shimmerProf.baseRadiusM,
          shimmerProf.tipRadiusM,
          coneLen,
          distPx,
          flowSpd,
          shimmerProf.noiseFreq,
          st.turbulenceSeed + ei * 3.17,
        );
      }
    }

    // 3. 事件触发翼尖涡流
    const shouldEmitVortex = Math.abs(st.rollDeg) > st.arch.wingtipVortexBankDeg || isPullUpHighG;
    if (shouldEmitVortex) {
      st.wingtipVortexActive = true;
      st.wingtipFadeAlpha = Math.min(1.0, st.wingtipFadeAlpha + dt * 5.0);
      const wtL = localRigToWorld(st.unit, st.spec.hardpoints.wingtips[0], this._tmpVecB);
      const wtR = localRigToWorld(st.unit, st.spec.hardpoints.wingtips[1], this._tmpVecC);
      vfxEngine?.emitWingtipVortex?.(st.unit.id, wtL, wtR, simNowSec, st.wingtipFadeAlpha);
    } else {
      st.wingtipVortexActive = false;
      st.wingtipFadeAlpha = Math.max(0.0, st.wingtipFadeAlpha - dt * 1.0);
      vfxEngine?.fadeWingtipVortex?.(st.unit.id, st.wingtipFadeAlpha);
    }

    // 4. 高空凝结尾迹（A-10C 恒跳过）
    if (st.spec.capabilities.contrail && pos.y >= 260) {
      for (let i = 0; i < st.spec.hardpoints.exhaustsRig.length; i += 1) {
        const exW = localRigToWorld(st.unit, st.spec.hardpoints.exhaustsRig[i], this._tmpVecA);
        vfxEngine?.emitContrailPoint?.(`${st.unit.id}_ex${i}`, exW, simNowSec);
      }
    }

    // 5. 受击冒烟
    if (st.damagedSmokeActive) {
      st.damageSmokeTimer += dt;
      if (st.damageSmokeTimer >= 0.085) {
        st.damageSmokeTimer = 0;
        const smokePos = localRigToWorld(st.unit, st.spec.hardpoints.damageSmokeOrigin, this._tmpVecA);
        vfxEngine?.spawnDamageSmokePuff?.(smokePos, true);
      }
    }

    // 5b. 机首局部炮口暖光瞬态衰减（零 PointLight，约 40ms 平滑熄灭）
    if (st.muzzleGlow && st.muzzleGlow.uniform.value.w > 0) {
      const mPosNow = localRigToWorld(st.unit, st.spec.hardpoints.muzzle, this._tmpVecA);
      st.muzzleGlow.decay(dt, mPosNow);
    }

    // 6. 弹道结算与状态机机炮声源兜底自愈守卫
    if (st.flightState !== 'strafe' && st.strafeAudioActive) {
      this._stopStrafeAudioIfNeeded(st, bridge);
    }
    this._stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge: bridge, targets, onTargetImpact });
    return st;
  }

  _enterEgress(st, simNowSec, audioBridge) {
    st.activeStrike = null;
    st.flightState = 'egress';
    st.egressTimer = 0;
    // 动态决定左/右大 G 脱离拉升方向：优先朝向盘旋空域中心侧切拉起，避免机械固定单向左转
    const pos = st.unit.node?.position;
    const yaw = st.unit.node?.rotation?.y || 0;
    if (pos && st.orbitCenter) {
      const fwdX = Math.sin(yaw), fwdZ = Math.cos(yaw);
      const toX = st.orbitCenter.x - pos.x;
      const toZ = st.orbitCenter.z - pos.z;
      // 本地 +X 为左翼（cos(yaw), -sin(yaw)），点积 > 0 表示盘旋中心在左侧 -> 左转 (+1)
      const leftDot = toX * Math.cos(yaw) + toZ * (-Math.sin(yaw));
      if (Math.abs(leftDot) > 12) {
        st.egressTurnSign = leftDot >= 0 ? 1 : -1;
      } else {
        st.egressTurnSign = (st.egressTurnSign || -1) * -1;
      }
    } else {
      st.egressTurnSign = -1;
    }
    this._queueFlareBurst(st, simNowSec, 6, { delaySec: 0.22 });
    const origin = localRigToWorld(st.unit, st.spec.hardpoints.flareOrigin, this._tmpVecA);
    audioBridge?.scheduleWorldEvent?.('BA_Chaff_Trap', origin, { gain: 0.66, powerTier: 'small' });
  }

  _spawnProjectile(st, opts) {
    const w = opts.weapon || {};
    this._projSeq = (this._projSeq || 0) + 1;
    const maxSpeed = opts.speed || w.projectileSpeedMps || 180;
    const launchSpeed = opts.launchSpeed || maxSpeed;
    const p = {
      id: `${st.unit.id}_proj_${this._projSeq}`,
      kind: opts.kind,
      weaponId: w.id || opts.weaponId || 'gau8_30mm',
      x: opts.origin.x,
      y: opts.origin.y,
      z: opts.origin.z,
      originX: opts.origin.x,
      originY: opts.origin.y,
      originZ: opts.origin.z,
      vx: opts.vel?.x || 0,
      vy: opts.vel?.y || 0,
      vz: opts.vel?.z || 0,
      targetX: opts.target.x,
      targetY: opts.target.y,
      targetZ: opts.target.z,
      launchSpeed,
      maxSpeed,
      speed: launchSpeed,
      boostAccelSec: Number.isFinite(opts.boostAccelSec) ? opts.boostAccelSec : (w.boostAccelSec || 0),
      gravityScale: Number.isFinite(opts.gravityScale) ? opts.gravityScale : (w.gravityScale ?? 1.0),
      roundIndex: opts.roundIndex || st.projectiles.length,
      traveledDist: 0,
      ballistic: Boolean(opts.ballistic),
      guided: Boolean(opts.guided),
      explosionTier: opts.explosionTier ?? w.explosionTier ?? 'medium',
      impactAudioKey: opts.impactAudioKey ?? w.impactAudioKey ?? '',
      tracerColor: opts.tracerColor || (this.tracerColorMode !== 'red' ? this.tracerColorMode : w.tracerColor) || 'red',
      isTracer: opts.isTracer !== false,
      isCompanion: Boolean(opts.isCompanion),
      damage: Number.isFinite(opts.damageOverride) ? opts.damageOverride : (w.damage || 60),
      splashInnerM: w.splashInnerM || 4,
      splashOuterM: w.splashOuterM || 12,
      edgeMultiplier: w.edgeMultiplier ?? 0.25,
      age: 0,
      maxLife: opts.ballistic ? 12.0 : 6.0,
      trailTimer: 0,
      trailDistAcc: 0,
    };
    if (!p.ballistic) {
      const dx = p.targetX - p.x;
      const dy = p.targetY - p.y;
      const dz = p.targetZ - p.z;
      const len = Math.max(0.01, Math.hypot(dx, dy, dz));
      p.vx = (dx / len) * p.speed;
      p.vy = (dy / len) * p.speed;
      p.vz = (dz / len) * p.speed;
    }
    st.projectiles.push(p);
    return p;
  }

  _stepProjectiles(st, dt, simNowSec, { terrainSupport, vfxEngine, audioBridge, onTargetImpact }) {
    const alive = [];
    for (let i = 0; i < st.projectiles.length; i += 1) {
      const p = st.projectiles[i];
      p.age += dt;
      if (p.ballistic) {
        const gEff = GRAVITY * (p.gravityScale || 1.0);
        p.vy -= gEff * dt;
        const dx = p.targetX - p.x;
        const dz = p.targetZ - p.z;
        const remH = Math.max(0.5, p.y - p.targetY);
        const vDown = Math.max(0, -p.vy);
        const estT = Math.max(0.22, (-vDown + Math.sqrt(vDown * vDown + 2 * gEff * remH)) / Math.max(1.0, gEff));
        p.vx = lerp(p.vx, dx / estT, expAlpha(2.4, dt));
        p.vz = lerp(p.vz, dz / estT, expAlpha(2.4, dt));
      } else {
        // 火箭/导弹遵循高初始过载固体助推加速曲线：launchSpeed -> maxSpeed (boostAccelSec)，迅速甩离载机
        if (p.boostAccelSec > 0) {
          if (p.age <= p.boostAccelSec) {
            const u = clamp(p.age / p.boostAccelSec, 0, 1);
            const boostU = 1 - Math.pow(1 - u, 2.2);
            p.speed = lerp(p.launchSpeed, p.maxSpeed, boostU);
          } else {
            p.speed = p.maxSpeed * Math.max(0.86, 1.0 - (p.age - p.boostAccelSec) * 0.03);
          }
        }
        if (p.guided) {
          const dx = p.targetX - p.x;
          const dy = p.targetY - p.y;
          const dz = p.targetZ - p.z;
          const len = Math.max(0.01, Math.hypot(dx, dy, dz));
          p.vx = lerp(p.vx, (dx / len) * p.speed, expAlpha(9.0, dt));
          p.vy = lerp(p.vy, (dy / len) * p.speed, expAlpha(9.0, dt));
          p.vz = lerp(p.vz, (dz / len) * p.speed, expAlpha(9.0, dt));
        }
      }

      const prevX = p.x;
      const prevY = p.y;
      const prevZ = p.z;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.z += p.vz * dt;
      const segDx = p.x - prevX;
      const segDy = p.y - prevY;
      const segDz = p.z - prevZ;
      const stepDist = Math.hypot(segDx, segDy, segDz);
      p.traveledDist += stepDist;

      if (p.kind === 'agm65') {
        // 路线 A：AGM-65 沿弹道按位移密集插值发射 8×8 体积云翻滚烟团 + 轻柔中心雾芯带，彻底根除孤立圆点与纸带感
        const burnActive = p.age <= 3.6;
        if (burnActive) {
          vfxEngine?.emitMissileTrailRibbon?.(
            p.id,
            { x: p.x, y: p.y, z: p.z },
            'missile_smoke',
            simNowSec,
          );
          const spacingM = 2.6;
          p.trailDistAcc = (p.trailDistAcc || 0) + stepDist;
          if (p.trailDistAcc >= spacingM) {
            const puffs = Math.min(5, Math.floor(p.trailDistAcc / spacingM));
            p.trailDistAcc -= puffs * spacingM;
            for (let k = 1; k <= puffs; k += 1) {
              const frac = k / puffs;
              const px = prevX + segDx * frac;
              const py = prevY + segDy * frac;
              const pz = prevZ + segDz * frac;
              vfxEngine?.spawnMissileTrailSmoke?.(
                { x: px, y: py, z: pz },
                simNowSec - (1 - frac) * dt,
                { scale: 1.0, hotCore: k === puffs },
              );
            }
          }
        }
      } else if (p.kind === 'rocket') {
        // 路线 A：Hydra 70 密集齐射走 64 段连续共享顶点 Mk 66 细锐轻烟带（不再飞行途中撒零散圆烟泡）
        if (p.age <= 1.65) {
          vfxEngine?.emitMissileTrailRibbon?.(
            p.id,
            { x: p.x, y: p.y, z: p.z },
            'rocket_smoke',
            simNowSec,
          );
        }
      } else if (p.kind === 'gbu31') {
        this._tmpPos.x = p.x;
        this._tmpPos.y = p.y;
        this._tmpPos.z = p.z;
        this._tmpVel.x = p.vx;
        this._tmpVel.y = p.vy;
        this._tmpVel.z = p.vz;
        audioBridge?.updateOrdnanceWhistle?.(p.id, this._tmpPos, this._tmpVel);
      }

      const gy = terrainSupport?.heightAt?.(p.x, p.z) ?? p.targetY;
      const distToTarget = Math.hypot(p.x - p.targetX, p.y - p.targetY, p.z - p.targetZ);
      const baseTol = p.kind === 'tracer30mm' ? 7.5 : 4.5;
      const tol = Math.max(baseTol, stepDist * 0.55);

      // 高速弹道防穿隧判定：若本帧线段越过目标平面且横向贴近目标点即视作精准命中
      let crossedTarget = false;
      let hitX = p.x;
      let hitZ = p.z;
      if (!p.ballistic && stepDist > 0.1) {
        const toTgtX = p.targetX - prevX;
        const toTgtY = p.targetY - prevY;
        const toTgtZ = p.targetZ - prevZ;
        const projT = (toTgtX * segDx + toTgtY * segDy + toTgtZ * segDz) / (stepDist * stepDist);
        if (projT >= 0 && projT <= 1.05) {
          const cx = prevX + segDx * clamp(projT, 0, 1);
          const cy = prevY + segDy * clamp(projT, 0, 1);
          const cz = prevZ + segDz * clamp(projT, 0, 1);
          if (Math.hypot(cx - p.targetX, cy - p.targetY, cz - p.targetZ) <= Math.max(baseTol, 8.0)) {
            crossedTarget = true;
            hitX = p.targetX;
            hitZ = p.targetZ;
          }
        }
      }

      const hitGround = crossedTarget || p.y <= gy + 0.55 || distToTarget <= tol;

      if (hitGround || p.age >= p.maxLife) {
        if (distToTarget <= tol && !crossedTarget) {
          hitX = p.targetX;
          hitZ = p.targetZ;
        }
        const hitGy = terrainSupport?.heightAt?.(hitX, hitZ) ?? gy;
        const impactPos = { x: hitX, y: hitGy + 0.15, z: hitZ };
        if (p.kind === 'gbu31') {
          audioBridge?.stopOrdnanceWhistle?.(p.id, { impactPos });
        }
        if (p.explosionTier !== 'none') {
          vfxEngine?.spawnExplosion?.(impactPos, p.explosionTier, { x: p.vx, y: p.vy, z: p.vz }, simNowSec);
        }
        if (p.impactAudioKey) {
          const isTracer = p.kind === 'tracer30mm';
          if (isTracer) {
            if (i % 3 === 0) {
              audioBridge?.scheduleWorldEvent?.(p.impactAudioKey, impactPos, {
                maxDelayMs: 420,
                gain: 0.55,
                shakeTier: p.explosionTier,
                powerTier: 'medium',
              });
              audioBridge?.triggerGroundImpactCrack?.(impactPos, { gain: 0.42 });
            }
          } else {
            audioBridge?.scheduleWorldEvent?.(p.impactAudioKey, impactPos, {
              gain: p.explosionTier === 'heavy' ? 1.0 : p.explosionTier === 'medium' ? 0.85 : 0.55,
              shakeTier: p.explosionTier,
              powerTier: p.explosionTier === 'heavy' ? 'heavy' : 'medium',
            });
          }
        }
        if (typeof onTargetImpact === 'function' && p.damage > 0) {
          onTargetImpact({
            pos: impactPos,
            tier: p.explosionTier,
            weaponId: p.weaponId,
            radius: p.splashOuterM,
            innerRadius: p.splashInnerM,
            edgeMultiplier: p.edgeMultiplier,
            damage: p.damage,
          });
        }
        st.lastEvents.push({ type: 'impact', weaponId: p.weaponId, tier: p.explosionTier, pos: impactPos });
      } else {
        vfxEngine?.pushActiveOrdnanceInstance?.(p);
        alive.push(p);
      }
    }
    st.projectiles = alive;
  }

  getUnitTelemetry(unitOrId) {
    const st = this.getUnitState(unitOrId);
    if (!st) return null;
    const pos = st.unit.node?.position || { x: 0, y: 0, z: 0 };
    return {
      id: st.unit.id,
      slug: st.unit.slug,
      name: st.spec.name,
      cnName: st.spec.cnName,
      role: st.unit.role,
      archetype: st.spec.archetype,
      attackBehaviors: [...st.spec.attackBehaviors],
      position: [Number(pos.x.toFixed(2)), Number(pos.y.toFixed(2)), Number(pos.z.toFixed(2))],
      yaw: Number((st.unit.node?.rotation?.y || 0).toFixed(4)),
      anim: {
        pitch: Number((st.unit.flightAttitude?.pitch || 0).toFixed(4)),
        roll: Number((st.unit.flightAttitude?.roll || 0).toFixed(4)),
        pitchDeg: Number(st.pitchDeg.toFixed(2)),
        bankDeg: Number(st.rollDeg.toFixed(2)),
        speedMps: Number(st.speedMps.toFixed(2)),
        yawRate: Number(st.yawRate.toFixed(4)),
        heaveY: Number((st.heaveY || 0).toFixed(3)),
        strafeRecoilActive: Boolean(st.strafeRecoilActive),
      },
      flightState: st.flightState,
      evacuated: Boolean(st.evacuated),
      rtbTarget: st.rtbTarget ? { ...st.rtbTarget } : null,
      altitudeMode: st.altitudeMode,
      orbitRadius: Number(st.orbitRadius.toFixed(1)),
      orbitRadiusRange: [st.arch.minOrbitRadiusM, st.arch.maxOrbitRadiusM],
      orbitAutoFire: false,
      manualFireOnly: true,
      fastApproachMode: this.fastApproachMode,
      strikeQueueLength: st.strikeQueue.length,
      predictedPath: this.getPredictedFlightPath(st.unit),
      hp: st.unit.hp ?? st.spec.gameStats.hp,
      maxHp: st.unit.maxHp ?? st.spec.gameStats.hp,
      usesFuelConstraint: true,
      fuelPct: Number(clamp((st.fuelRemainSec / Math.max(1, st.fuelMaxSec)) * 100, 0, 100).toFixed(1)),
      fuelRemainSec: Number(Math.max(0, st.fuelRemainSec).toFixed(1)),
      fuelMaxSec: st.fuelMaxSec,
      fuelBurnRate: Number((st.fuelBurnRate ?? 1.0).toFixed(2)),
      fuelModeLabel: st.fuelModeLabel || '巡航',
      bingoThresholdPct: st.bingoThresholdPct ?? 14,
      bingoState: st.bingoState || 'nominal',
      bingoLockedRtb: Boolean(st.bingoLockedRtb),
      fuelConsumptionPaused: Boolean(this.fuelConsumptionPaused),
      winchesterState: st.winchesterState || 'nominal',
      isWinchester: st.winchesterState === 'winchester' || st.winchesterState === 'rtb_winchester',
      rtbReason: st.rtbReason || 'manual',
      autoRtbOnWinchester: Boolean(this.autoRtbOnWinchester),
      ejectionTriggered: Boolean(st.ejectionTriggered),
      throttleLevel: Number((st.throttleLevel ?? 0.55).toFixed(2)),
      afterburnerActive: Boolean(st.afterburnerActive),
      rearmCooldownSec: st.spec.gameStats?.rearmCooldownSec || 35,
      damagedSmokeActive: st.damagedSmokeActive,
      crashed: st.crashed,
      wingtipVortexActive: st.wingtipVortexActive,
      wingtipFadeAlpha: Number(st.wingtipFadeAlpha.toFixed(3)),
      flareBurstCount: st.flareBurstCount,
      capabilities: { ...st.spec.capabilities },
      sourceRef: { ...st.spec.sourceRef },
      gameStats: { ...st.spec.gameStats },
      exhaustsLocal: st.spec.hardpoints.exhaustsNative.map(a => [...a]),
      exhaustsRig: st.spec.hardpoints.exhaustsRig.map(a => [...a]),
    };
  }

  getWeaponsTelemetry(unitOrId) {
    const st = this.getUnitState(unitOrId);
    if (!st) return [];
    const fallbackCategory = {
      autocannon: '机炮',
      rocket: '火箭弹',
      agm: '空地导弹',
      aam: '空空导弹',
      bomb: '制导航弹',
      ecm_pod: '干扰吊舱',
    };
    return st.weapons.map(w => ({
      id: w.id,
      name: w.name,
      modelLabel: w.modelLabel || w.name || w.shortLabel,
      categoryLabel: w.categoryLabel || fallbackCategory[w.kind] || '',
      shortLabel: w.shortLabel || w.name,
      hotkey: w.hotkey || '',
      kind: w.kind,
      actionCmd: w.actionCmd || '',
      inputMode: w.inputMode || 'drag_line',
      ammo: w.ammo,
      maxAmmo: w.maxAmmo,
      salvoCount: w.salvoCount || 1,
      minRangeM: w.minRangeM || 0,
      maxRangeM: w.maxRangeM || 0,
      burstAxisLenM: w.burstAxisLenM || 0,
      defaultStrafeLenM: w.defaultStrafeLenM || w.burstAxisLenM || 0,
      strafeLenMinM: w.strafeLenMinM || 0,
      strafeLenMaxM: w.strafeLenMaxM || 0,
      strafeWidthM: w.strafeWidthM || 0,
      damage: w.damage || 0,
      projectileSpeedMps: w.projectileSpeedMps || 0,
      boostAccelSec: w.boostAccelSec || 0,
      gravityScale: w.gravityScale ?? 0,
      splashInnerM: w.splashInnerM || 0,
      splashOuterM: w.splashOuterM || 0,
      edgeMultiplier: w.edgeMultiplier ?? 0.25,
      explosionTier: w.explosionTier || 'medium',
      canTargetAir: Boolean(w.canTargetAir),
      canTargetGround: Boolean(w.canTargetGround),
      tracerEveryN: w.tracerEveryN || 0,
      combatMixRatio: Array.isArray(w.combatMixRatio) ? [...w.combatMixRatio] : null,
      pylonIndices: [...w.pylonIndices],
      visiblePylonCount: w.visiblePylonIndices.length,
      gunAcousticPreset: w.gunAcousticPreset ? { ...w.gunAcousticPreset } : null,
      sourceRef: w.sourceRef ? { ...w.sourceRef } : null,
    }));
  }
}

/**
 * 3A：将《Ebbtide》航弹连点序列按飞行走廊顺滑性分组（大拐角 > maxAngleDeg 或超跨距 > maxHopM 自动拆入下一趟队列）
 * @param {Array<{x:number,y?:number,z:number}>} points
 * @param {number} [maxAngleDeg=42]
 * @param {number} [maxHopM=460]
 * @returns {Array<Array<{x:number,y:number,z:number}>>}
 */
export function groupDropPointsIntoCorridors(points, maxAngleDeg = 42, maxHopM = 460) {
  if (!Array.isArray(points) || points.length === 0) return [];
  const valid = points
    .filter(p => p && Number.isFinite(p.x) && Number.isFinite(p.z))
    .map(p => ({ x: p.x, y: Number.isFinite(p.y) ? p.y : 2.0, z: p.z }));
  if (valid.length <= 1) return valid.length ? [valid] : [];

  const cosThreshold = Math.cos((Math.max(10, Math.min(120, maxAngleDeg)) * Math.PI) / 180);
  const batches = [];
  let cur = [valid[0]];
  let corridorDir = null;

  for (let i = 1; i < valid.length; i += 1) {
    const pt = valid[i];
    const prev = cur[cur.length - 1];
    const dx = pt.x - prev.x;
    const dz = pt.z - prev.z;
    const segLen = Math.hypot(dx, dz);

    if (segLen < 1.5) {
      cur.push(pt);
      continue;
    }

    const ux = dx / segLen;
    const uz = dz / segLen;

    if (!corridorDir) {
      if (segLen > maxHopM) {
        batches.push(cur);
        cur = [pt];
        corridorDir = null;
      } else {
        corridorDir = { x: ux, z: uz };
        cur.push(pt);
      }
      continue;
    }

    const dotMain = ux * corridorDir.x + uz * corridorDir.z;
    if (dotMain < cosThreshold || segLen > maxHopM) {
      batches.push(cur);
      cur = [pt];
      corridorDir = null;
    } else {
      cur.push(pt);
    }
  }

  if (cur.length > 0) batches.push(cur);
  return batches;
}

/**
 * V2.4：判断新增导弹点能否并入既有导弹趟次（前方 ±maxAngleDeg 扇区同走廊且单趟总数 <= maxPerPass 时合并连射；大偏角/反向点拆趟）。
 * @param {object} plan
 * @param {{ x: number, y?: number, z: number }} newTarget
 * @param {{ x: number, z: number } | null} refPos
 * @param {{ maxAngleDeg?: number, maxPerPass?: number }} [opts]
 * @returns {boolean}
 */
export function canMergeMissileIntoPlan(plan, newTarget, refPos = null, { maxAngleDeg = 40, maxPerPass = 6 } = {}) {
  if (!plan || plan.cmd !== 'missile' || !newTarget || !Number.isFinite(newTarget.x) || !Number.isFinite(newTarget.z)) {
    return false;
  }
  const existing = Array.isArray(plan.missileTargets) && plan.missileTargets.length > 0
    ? plan.missileTargets
    : (plan.targetPos ? [plan.targetPos] : []);
  if (existing.length >= Math.max(1, maxPerPass)) return false;

  const first = existing[0] || plan.targetPos;
  const anchor = (refPos && Number.isFinite(refPos.x) && Number.isFinite(refPos.z))
    ? refPos
    : (plan.entryPoint || first);
  if (!anchor || !first) return false;

  let dirX = Number(plan.attackDir?.x);
  let dirZ = Number(plan.attackDir?.z);
  const dLen = Math.hypot(dirX || 0, dirZ || 0);
  if (dLen > 1e-4) {
    dirX /= dLen;
    dirZ /= dLen;
  } else {
    const fdx = first.x - anchor.x;
    const fdz = first.z - anchor.z;
    const fl = Math.hypot(fdx, fdz);
    if (fl < 1e-4) return false;
    dirX = fdx / fl;
    dirZ = fdz / fl;
  }

  const dx = newTarget.x - anchor.x;
  const dz = newTarget.z - anchor.z;
  const dist = Math.hypot(dx, dz);
  if (dist < 1.0) return false;

  const forwardProj = dx * dirX + dz * dirZ;
  const minForwardM = Math.max(80, (Number(plan.minRangeM) || 250) * 0.45);
  if (forwardProj < minForwardM) return false;

  const dot = forwardProj / dist;
  const cosThreshold = Math.cos((clamp(maxAngleDeg, 5, 90) * Math.PI) / 180);
  return dot >= cosThreshold;
}

/**
 * 计算固定翼从当前空中位置切入目标盘旋圆周的逆时针切线接入点（供图 1 真实移动路径线使用）
 * @param {{ x: number, y?: number, z: number }} planePos
 * @param {{ x: number, z: number }} orbitCenter
 * @param {number} orbitRadius
 * @returns {{ x: number, z: number }}
 */
export function computeOrbitEntryPoint(planePos, orbitCenter, orbitRadius = 150) {
  const cx = Number(orbitCenter?.x) || 0;
  const cz = Number(orbitCenter?.z) || 0;
  const r = Math.max(40, Number(orbitRadius) || 150);
  const px = Number(planePos?.x) || cx;
  const pz = Number(planePos?.z) || cz;
  const dx = px - cx;
  const dz = pz - cz;
  const d = Math.hypot(dx, dz);
  if (d <= r + 8) {
    // 处于圈内或贴近圆周：顺着逆时针绕飞增量方向投影至前方圆周点
    const alpha = d > 1 ? Math.atan2(dz, dx) : 0;
    const theta = alpha + 0.45;
    return { x: cx + Math.cos(theta) * r, z: cz + Math.sin(theta) * r };
  }
  const alpha = Math.atan2(dz, dx);
  const delta = Math.acos(clamp(r / d, -1, 1));
  const theta = alpha + delta;
  return { x: cx + Math.cos(theta) * r, z: cz + Math.sin(theta) * r };
}
