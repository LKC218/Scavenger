// ============================================================
// 数值基底与全局倍率规范（Code SSOT — ba-source-ssot-v1）
// ------------------------------------------------------------
// 全项目统一以 https://brokenarrowstats.com 原站数据为唯一数值基底（sourceRef），
// 全部实战单位/武器/丧尸/靶车参数必须经由本模块 GLOBAL_BALANCE_SCALES 派生，
// 严禁在业务模块各自私自硬编码换算系数或手写覆盖最终伤害/射程/航速。
// ============================================================

export const BROKEN_ARROW_STATS_BASE_URL = 'https://brokenarrowstats.com/arsenal/?country=2&cat=6&lang=zh';

export const BROKEN_ARROW_UNIT_URL_MAP = Object.freeze({
  US_A10: 'https://brokenarrowstats.com/unit/a-10c-thunderbolt-ii-282/?lang=zh',
});

/**
 * 全局统一数值倍率表（全仓唯一缩放配置源，当前 1.0x 原站直出基底）
 */
export const GLOBAL_BALANCE_SCALES = Object.freeze({
  specVersion: 'ba-source-ssot-v1',
  hpScale: 1.0,             // 载具与战机 HP 倍率（原站留底，固定翼实战改由燃油限制替代血量结算）
  damageScale: 1.0,         // 武器单发伤害倍率（GAU-8=2.25, Hydra=7.5, AGM-65=22, JDAM=56）
  weaponRangeScale: 1.0,    // 武器对地最大射程倍率（1000 / 1100 / 1250 / 1000m）
  projectileSpeedScale: 1.0, // 弹药/导弹/火箭飞行速度统一缩放倍率
  airSpeedScale: 1.0,       // 固定翼突击攻击航速倍率（A-10C 450 km/h = 125 m/s）
  orbitSpeedFactor: 0.78,   // 常规盘旋相对突击航速观感倍率（A-10C ~351 km/h = 97.5 m/s）
  opticsScale: 1.0,         // 光学/侦察视野倍率（A-10C = 2500m）
  costScale: 1.0,           // 单位部署造价倍率
  fuelDurationScale: 1.0,   // 固定翼基准留空燃油续航倍率（默认 1.0×）
  rearmCooldownScale: 1.0,  // 固定翼返航整备冷却倍率（默认 1.0×，约 30–45s）
});

/**
 * 固定翼 5 大原型族燃油留空与机场整备基底配置（统一经 GLOBAL_BALANCE_SCALES 派生）
 * - 工况油耗口径（Q7-A 方案B）：盘旋巡航 1.0×，入航对准 1.2×，突击接敌/脱离 1.45×，超音速加力 1.85×
 * - 返航红线口径（Q6-A 方案B）：按距最近战场边界动态核算 10%–18% 余油，触线强制返航并整备冷却
 */
export const FIXED_WING_FUEL_PROFILES = Object.freeze({
  cas_gunship: Object.freeze({
    baseFuelSec: 480,
    rearmCooldownSec: 35,
    cruiseBurnRate: 1.0,
    transitBurnRate: 1.2,
    attackBurnRate: 1.45,
    sprintBurnRate: 1.45,
    afterburnerBurnRate: 1.85,
  }),
  supersonic_multirole: Object.freeze({
    baseFuelSec: 360,
    rearmCooldownSec: 40,
    cruiseBurnRate: 1.0,
    transitBurnRate: 1.2,
    attackBurnRate: 1.45,
    sprintBurnRate: 1.55,
    afterburnerBurnRate: 1.85,
  }),
  strategic_stealth_bomber: Object.freeze({
    baseFuelSec: 540,
    rearmCooldownSec: 45,
    cruiseBurnRate: 1.0,
    transitBurnRate: 1.2,
    attackBurnRate: 1.45,
    sprintBurnRate: 1.5,
    afterburnerBurnRate: 1.85,
  }),
  heavy_support_ew: Object.freeze({
    baseFuelSec: 600,
    rearmCooldownSec: 40,
    cruiseBurnRate: 1.0,
    transitBurnRate: 1.2,
    attackBurnRate: 1.4,
    sprintBurnRate: 1.45,
    afterburnerBurnRate: 1.7,
  }),
  fixed_wing_uav: Object.freeze({
    baseFuelSec: 900,
    rearmCooldownSec: 30,
    cruiseBurnRate: 1.0,
    transitBurnRate: 1.15,
    attackBurnRate: 1.35,
    sprintBurnRate: 1.45,
    afterburnerBurnRate: 1.45,
  }),
});

/**
 * 9km 战场战争迷雾分兵种/原型族侦察形态与视野映射基底（fog-of-war-v1，全部由 sourceRef.opticsM * opticsScale 派生）
 * - 无人机 (fixed_wing_uav)：唯一持续面状开图主力，高空广域 (penetration=128) / 低空精细穿透 (penetration=255)
 * - 有人固定翼 (cas_gunship / supersonic_multirole / strategic_stealth_bomber)：前向窄锥 + 翼下掠扫走廊，飞离即转失联
 * - AC-130 盘旋炮艇：盘旋态启用中等半径光电聚焦圈 (orbit_pod)
 * - 重型电战/支援 (heavy_support_ew)：极窄本体视野 + 大范围 A 通道宏观态势热区（不破除 3D 精细迷雾）
 */
export const FOG_RECON_PROFILES = Object.freeze({
  fixed_wing_uav: Object.freeze({
    shape: 'circle',
    highRadiusFactor: 0.28,
    lowRadiusFactor: 0.18,
    corridorHalfWidthFactor: 0.0,
    corridorForwardFactor: 0.0,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 255,
    ignoresGroundOcclusion: true,
  }),
  cas_gunship: Object.freeze({
    shape: 'corridor',
    highRadiusFactor: 0.09,
    lowRadiusFactor: 0.08,
    corridorHalfWidthFactor: 0.08,
    corridorForwardFactor: 0.22,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 255,
    ignoresGroundOcclusion: true,
  }),
  cas_gunship_orbit_pod: Object.freeze({
    shape: 'orbit_pod',
    highRadiusFactor: 0.20,
    lowRadiusFactor: 0.16,
    corridorHalfWidthFactor: 0.08,
    corridorForwardFactor: 0.18,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 255,
    ignoresGroundOcclusion: true,
  }),
  supersonic_multirole: Object.freeze({
    shape: 'corridor',
    highRadiusFactor: 0.07,
    lowRadiusFactor: 0.06,
    corridorHalfWidthFactor: 0.06,
    corridorForwardFactor: 0.18,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 128,
    ignoresGroundOcclusion: true,
  }),
  strategic_stealth_bomber: Object.freeze({
    shape: 'corridor',
    highRadiusFactor: 0.07,
    lowRadiusFactor: 0.06,
    corridorHalfWidthFactor: 0.06,
    corridorForwardFactor: 0.18,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 128,
    ignoresGroundOcclusion: true,
  }),
  heavy_support_ew: Object.freeze({
    shape: 'ew_pulse',
    highRadiusFactor: 0.06,
    lowRadiusFactor: 0.06,
    corridorHalfWidthFactor: 0.05,
    corridorForwardFactor: 0.12,
    ewPulseRadiusFactor: 0.65,
    writesExploredG: true,
    highPenetrationTier: 128,
    lowPenetrationTier: 128,
    ignoresGroundOcclusion: true,
  }),
  helicopter: Object.freeze({
    shape: 'circle',
    highRadiusFactor: 0.32,
    lowRadiusFactor: 0.28,
    corridorHalfWidthFactor: 0.0,
    corridorForwardFactor: 0.0,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 255,
    lowPenetrationTier: 255,
    ignoresGroundOcclusion: true,
  }),
  ground: Object.freeze({
    shape: 'circle',
    highRadiusFactor: 0.28,
    lowRadiusFactor: 0.28,
    corridorHalfWidthFactor: 0.0,
    corridorForwardFactor: 0.0,
    ewPulseRadiusFactor: 0.0,
    writesExploredG: true,
    highPenetrationTier: 255,
    lowPenetrationTier: 255,
    ignoresGroundOcclusion: false,
  }),
});

/**
 * 与 brokenarrowstats.com 同一量纲的丧尸/试验靶场基底参数
 * （对照原站单兵步兵 ~1 HP、轻装载具 12–16 HP、主力坦克 20–24 HP）
 */
export const ZOMBIE_TARGET_BASE_STATS = Object.freeze({
  specVersion: 'ba-source-ssot-v1',
  staticTargetHp: 18, // 调试中轻甲靶车基底血量
  zombieHp: 1.5,      // 模拟普通丧尸个体血量（GAU-8 2.25 伤可单发贯穿清扫）
});

function roundTo(val, digits = 2) {
  const f = 10 ** digits;
  return Math.round((Number(val) || 0) * f) / f;
}

/**
 * 从原站 sourceRef 与机型飞行包络按 GLOBAL_BALANCE_SCALES 唯一公式派生战机实战数值
 * @param {{ cost?: number, speedKmh?: number, hp?: number, opticsM?: number, ecmPct?: number, stealth?: number }} sourceRef
 * @param {{ lowAltitudeM?: number, highAltitudeM?: number, minOrbitRadiusM?: number, maxOrbitRadiusM?: number }} [flightProfile]
 * @param {typeof GLOBAL_BALANCE_SCALES} [scales]
 */
export function deriveAircraftStatsFromSourceRef(sourceRef = {}, flightProfile = {}, scales = GLOBAL_BALANCE_SCALES) {
  const s = scales || GLOBAL_BALANCE_SCALES;
  const costScale = Number.isFinite(s.costScale) ? s.costScale : 1.0;
  const hpScale = Number.isFinite(s.hpScale) ? s.hpScale : 1.0;
  const airSpeedScale = Number.isFinite(s.airSpeedScale) ? s.airSpeedScale : 1.0;
  const orbitFactor = Number.isFinite(s.orbitSpeedFactor) ? s.orbitSpeedFactor : 0.78;
  const opticsScale = Number.isFinite(s.opticsScale) ? s.opticsScale : 1.0;
  const fuelScale = Number.isFinite(s.fuelDurationScale) ? s.fuelDurationScale : 1.0;
  const rearmScale = Number.isFinite(s.rearmCooldownScale) ? s.rearmCooldownScale : 1.0;

  const baseCost = Number(sourceRef?.cost) || 250;
  const baseHp = Number(sourceRef?.hp) || 20;
  const baseSpeedKmh = Number(sourceRef?.speedKmh) || 450;
  const baseOpticsM = Number(sourceRef?.opticsM) || 2400;

  const attackSpeedKmh = Math.max(80, Math.round(baseSpeedKmh * airSpeedScale));
  const attackSpeedMps = roundTo(attackSpeedKmh / 3.6, 2);
  const cruiseSpeedKmh = Math.max(60, Math.round(attackSpeedKmh * orbitFactor));
  const cruiseSpeedMps = roundTo(cruiseSpeedKmh / 3.6, 2);

  const archKey = flightProfile?.archetype || 'cas_gunship';
  const fuelProf = FIXED_WING_FUEL_PROFILES[archKey] || FIXED_WING_FUEL_PROFILES.cas_gunship;
  const hasAfterburner = Boolean(flightProfile?.afterburner);
  const fuelMaxSec = Math.max(45, Math.round(fuelProf.baseFuelSec * fuelScale));
  const rearmCooldownSec = Math.max(10, Math.round(fuelProf.rearmCooldownSec * rearmScale));

  return Object.freeze({
    cost: Math.max(1, Math.round(baseCost * costScale)),
    hp: roundTo(Math.max(1, baseHp * hpScale), 2), // 原站留底，固定翼实战由燃油限制替代血量结算
    usesFuelConstraint: true,
    fuelMaxSec,
    rearmCooldownSec,
    fuelBurnRates: Object.freeze({
      cruise: fuelProf.cruiseBurnRate,
      transit: fuelProf.transitBurnRate,
      attack: fuelProf.attackBurnRate,
      sprint: hasAfterburner ? fuelProf.afterburnerBurnRate : fuelProf.sprintBurnRate,
      afterburner: fuelProf.afterburnerBurnRate,
    }),
    attackSpeedKmh,
    attackSpeedMps,
    cruiseSpeedKmh,
    cruiseSpeedMps,
    lowAltitudeM: Number(flightProfile?.lowAltitudeM) || 135,
    highAltitudeM: Number(flightProfile?.highAltitudeM) || 215,
    orbitRadiusRange: Object.freeze([
      Number(flightProfile?.minOrbitRadiusM) || 120,
      Number(flightProfile?.maxOrbitRadiusM) || 380,
    ]),
    visionRangeM: Math.max(200, Math.round(baseOpticsM * opticsScale)),
    reconProfile: deriveUnitReconProfile(
      { opticsM: baseOpticsM },
      {
        role: archKey === 'fixed_wing_uav' ? 'uav' : 'fixed_wing',
        archetype: archKey,
        slug: flightProfile?.slug || '',
      },
      s,
    ),
  });
}

/**
 * 基于唯一原站光学视野基底（sourceRef.opticsM * opticsScale）派生各兵种/原型族的战争迷雾侦察包络。
 * @param {{ opticsM?: number }} sourceRef
 * @param {{ role?: string, archetype?: string, slug?: string, altitudeMode?: 'low'|'high'|'ground', isOrbiting?: boolean }} [unitMeta]
 * @param {typeof GLOBAL_BALANCE_SCALES} [scales]
 */
export function deriveUnitReconProfile(sourceRef = {}, unitMeta = {}, scales = GLOBAL_BALANCE_SCALES) {
  const s = scales || GLOBAL_BALANCE_SCALES;
  const opticsScale = Number.isFinite(s.opticsScale) ? s.opticsScale : 1.0;
  const role = String(unitMeta?.role || 'ground');
  const slug = String(unitMeta?.slug || '');
  const archetype = String(unitMeta?.archetype || '');
  const isAc130 = /AC130/i.test(slug);

  let profileKey = 'ground';
  if (role === 'uav' || archetype === 'fixed_wing_uav') {
    profileKey = 'fixed_wing_uav';
  } else if (role === 'helicopter' || role === 'heli') {
    profileKey = 'helicopter';
  } else if (role === 'fixed_wing' || archetype) {
    if (isAc130) {
      profileKey = 'cas_gunship_orbit_pod';
    } else if (FOG_RECON_PROFILES[archetype]) {
      profileKey = archetype;
    } else {
      profileKey = 'cas_gunship';
    }
  }

  const prof = FOG_RECON_PROFILES[profileKey] || FOG_RECON_PROFILES.ground;
  const defaultBaseOptics =
    profileKey === 'fixed_wing_uav'
      ? 4000
      : profileKey === 'helicopter'
        ? 1800
        : profileKey === 'ground'
          ? 1400
          : 2400;
  const rawOptics = Number(sourceRef?.opticsM);
  const effectiveOpticsM = Math.max(200, (Number.isFinite(rawOptics) && rawOptics > 0 ? rawOptics : defaultBaseOptics) * opticsScale);

  const highRadiusM = Math.max(80, Math.round(effectiveOpticsM * prof.highRadiusFactor));
  const lowRadiusM = Math.max(70, Math.round(effectiveOpticsM * prof.lowRadiusFactor));
  const corridorHalfWidthM = prof.corridorHalfWidthFactor > 0
    ? Math.max(60, Math.round(effectiveOpticsM * prof.corridorHalfWidthFactor))
    : 0;
  const corridorForwardM = prof.corridorForwardFactor > 0
    ? Math.max(120, Math.round(effectiveOpticsM * prof.corridorForwardFactor))
    : 0;
  const ewPulseRadiusM = prof.ewPulseRadiusFactor > 0
    ? Math.max(300, Math.round(effectiveOpticsM * prof.ewPulseRadiusFactor))
    : 0;

  return Object.freeze({
    profileKey,
    shape: prof.shape,
    effectiveOpticsM: Math.round(effectiveOpticsM),
    highRadiusM,
    lowRadiusM,
    corridorHalfWidthM,
    corridorForwardM,
    ewPulseRadiusM,
    writesExploredG: prof.writesExploredG,
    highPenetrationTier: prof.highPenetrationTier,
    lowPenetrationTier: prof.lowPenetrationTier,
    ignoresGroundOcclusion: prof.ignoresGroundOcclusion,
  });
}

/**
 * 从原站武器 sourceRef 按 GLOBAL_BALANCE_SCALES 唯一公式派生射程、伤害与弹道速度，仅叠加 3D 物理补充字段
 * @param {{ groundRangeM?: number, minRangeM?: number, damage?: number, pen?: number, projectileSpeedMps?: number }} sourceRef
 * @param {Record<string, any>} [supplements]
 * @param {typeof GLOBAL_BALANCE_SCALES} [scales]
 */
export function deriveWeaponStatsFromSourceRef(sourceRef = {}, supplements = {}, scales = GLOBAL_BALANCE_SCALES) {
  const s = scales || GLOBAL_BALANCE_SCALES;
  const rangeScale = Number.isFinite(s.weaponRangeScale) ? s.weaponRangeScale : 1.0;
  const dmgScale = Number.isFinite(s.damageScale) ? s.damageScale : 1.0;
  const projScale = Number.isFinite(s.projectileSpeedScale) ? s.projectileSpeedScale : 1.0;

  const baseRange = Number(sourceRef?.groundRangeM) || 1000;
  const baseDamage = Number(sourceRef?.damage) || 0;
  const baseMinRange = Number(sourceRef?.minRangeM) || 0;

  const maxRangeM = Math.max(50, Math.round(baseRange * rangeScale));
  const suppMin = Number.isFinite(supplements?.minRangeM) ? supplements.minRangeM : baseMinRange;
  const minRangeM = Math.min(Math.max(0, Math.round(suppMin * rangeScale)), Math.max(0, maxRangeM - 40));
  const damage = roundTo(baseDamage * dmgScale, 2);

  const baseProjSpd = Number(sourceRef?.projectileSpeedMps);
  const projectileSpeedMps = Number.isFinite(baseProjSpd) && baseProjSpd > 0
    ? roundTo(Math.max(20, baseProjSpd * projScale), 1)
    : (Number(supplements?.projectileSpeedMps) || 0);

  return {
    ...supplements,
    minRangeM,
    maxRangeM,
    damage,
    projectileSpeedMps,
    sourceRef: Object.freeze({ ...sourceRef }),
  };
}

/**
 * 12 大武器库兵种原型参数基底（全 129 辆载具跨页共享统一规格源，均经 GLOBAL_BALANCE_SCALES 派生）。
 * - 地面 7 类突出：装甲、机动、视野、主武器弹药；
 * - 旋翼 2 类突出：巡航高度（30m 越障）、载荷挂点、航速、视野；
 * - 固定翼 3 类（含 UAV）对接 SCAV-6：彻底移除血量，突出战术燃油续航、工况油耗、动态 Bingo 与整备冷却。
 */
export const VEHICLE_ARCHETYPE_PROFILES = Object.freeze({
  mbt: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '主战坦克',
    subTitle: '重型主战坦克 · 正面复合反应甲',
    cost: 280,
    hp: 20,
    bonusArmorPct: 12,
    armorText: '650 / 1100',
    speedKmh: 66,
    opticsM: 1400,
    caps: Object.freeze([
      { k: '装甲防护', score: 9, tone: 'ground' },
      { k: '清尸压制', score: 8, tone: 'select' },
      { k: '重甲攻坚', score: 9, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'mbt_he', kind: 'cannon', name: '120mm 高爆弹', ammo: 24, maxAmmo: 28, groundRangeM: 1400, minRangeM: 0, splashInnerM: 3, splashOuterM: 9, damage: 12, roleTag: '面伤清群' },
      { id: 'mbt_can', kind: 'cannon', name: 'M1028 霰弹', ammo: 12, maxAmmo: 14, groundRangeM: 450, minRangeM: 0, splashInnerM: 6, splashOuterM: 18, damage: 18, roleTag: '近距尸潮' },
      { id: 'mbt_mg', kind: 'cannon', name: '7.62mm 同轴机枪', ammo: 1800, maxAmmo: 2000, groundRangeM: 600, minRangeM: 0, splashInnerM: 0, splashOuterM: 0, damage: 1.5, roleTag: '持续补刀' },
    ]),
  }),
  ifv: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '步战 / 装甲车',
    subTitle: '步兵战车 · 机炮压制与导弹协同',
    cost: 155,
    hp: 16,
    bonusArmorPct: 6,
    armorText: '240 / 480',
    speedKmh: 72,
    opticsM: 1600,
    caps: Object.freeze([
      { k: '装甲防护', score: 6, tone: 'ground' },
      { k: '清尸压制', score: 9, tone: 'select' },
      { k: '反甲支援', score: 7, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'ifv_ac', kind: 'cannon', name: '25mm 链炮', ammo: 600, maxAmmo: 600, groundRangeM: 1200, minRangeM: 0, splashInnerM: 2, splashOuterM: 5, damage: 2.2, roleTag: '持续清群' },
      { id: 'ifv_atgm', kind: 'missile', name: 'TOW-2B 反坦克导弹', ammo: 4, maxAmmo: 4, groundRangeM: 1600, minRangeM: 120, splashInnerM: 2.5, splashOuterM: 7, damage: 20, roleTag: '远程点杀' },
    ]),
  }),
  recon: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '侦察 / 轻型突击',
    subTitle: '高速侦察载具 · 广域光学前哨',
    cost: 95,
    hp: 10,
    bonusArmorPct: 0,
    armorText: '60 / 120',
    speedKmh: 96,
    opticsM: 2400,
    caps: Object.freeze([
      { k: '装甲防护', score: 3, tone: 'ground' },
      { k: '侦察视野', score: 10, tone: 'select' },
      { k: '机动穿插', score: 9, tone: 'heli' },
    ]),
    weapons: Object.freeze([
      { id: 'rec_hmg', kind: 'cannon', name: '12.7mm 重机枪', ammo: 800, maxAmmo: 800, groundRangeM: 900, minRangeM: 0, splashInnerM: 0, splashOuterM: 0, damage: 2.0, roleTag: '机动压制' },
      { id: 'rec_agl', kind: 'rocket', name: 'Mk19 40mm 榴弹', ammo: 96, maxAmmo: 96, groundRangeM: 850, minRangeM: 60, splashInnerM: 3, splashOuterM: 8, damage: 4.5, roleTag: '轻甲清扫' },
    ]),
  }),
  arty: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '自行火炮 / 火箭',
    subTitle: '远程曲射火力 · 大面积尸潮覆盖',
    cost: 220,
    hp: 12,
    bonusArmorPct: 0,
    armorText: '80 / 160',
    speedKmh: 58,
    opticsM: 1100,
    caps: Object.freeze([
      { k: '装甲防护', score: 4, tone: 'ground' },
      { k: '面域覆盖', score: 10, tone: 'select' },
      { k: '重型爆破', score: 9, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'arty_he', kind: 'rocket', name: '155mm / 227mm 齐射弹', ammo: 18, maxAmmo: 18, groundRangeM: 3400, minRangeM: 450, splashInnerM: 10, splashOuterM: 26, damage: 34, roleTag: '远程覆盖' },
    ]),
  }),
  aa: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '野战防空 / 导弹',
    subTitle: '防空掩护系统 · 雷达警戒与速射拦截',
    cost: 185,
    hp: 12,
    bonusArmorPct: 0,
    armorText: '90 / 180',
    speedKmh: 68,
    opticsM: 2200,
    caps: Object.freeze([
      { k: '防空拦截', score: 10, tone: 'heli' },
      { k: '态势预警', score: 9, tone: 'select' },
      { k: '近防压制', score: 6, tone: 'ground' },
    ]),
    weapons: Object.freeze([
      { id: 'aa_sam', kind: 'missile', name: '防空拦截弹组', ammo: 8, maxAmmo: 8, groundRangeM: 2400, minRangeM: 180, splashInnerM: 4, splashOuterM: 10, damage: 22, roleTag: '防空拦截' },
      { id: 'aa_gun', kind: 'cannon', name: '速射近防机炮', ammo: 1200, maxAmmo: 1200, groundRangeM: 1100, minRangeM: 0, splashInnerM: 1.5, splashOuterM: 4, damage: 2.0, roleTag: '近距补防' },
    ]),
  }),
  atgm: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '反坦克导弹车',
    subTitle: '远程反甲猎杀 · 高穿深制导火力',
    cost: 140,
    hp: 12,
    bonusArmorPct: 0,
    armorText: '110 / 220',
    speedKmh: 76,
    opticsM: 1800,
    caps: Object.freeze([
      { k: '重甲狙杀', score: 10, tone: 'danger' },
      { k: '隐蔽伏击', score: 8, tone: 'select' },
      { k: '装甲防护', score: 4, tone: 'ground' },
    ]),
    weapons: Object.freeze([
      { id: 'atgm_main', kind: 'missile', name: '重型反坦克导弹', ammo: 8, maxAmmo: 8, groundRangeM: 1800, minRangeM: 140, splashInnerM: 3, splashOuterM: 7, damage: 28, roleTag: '精确穿甲' },
    ]),
  }),
  supply: Object.freeze({
    channel: 'ground',
    channelLabel: '地面',
    roleLabel: '后勤补给 / 工程',
    subTitle: '野战弹药补给与载具抢修中枢',
    cost: 110,
    hp: 10,
    bonusArmorPct: 0,
    armorText: '40 / 80',
    speedKmh: 74,
    opticsM: 1100,
    caps: Object.freeze([
      { k: '弹药补给', score: 10, tone: 'ground' },
      { k: '野战抢修', score: 9, tone: 'select' },
      { k: '自卫火力', score: 2, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'sup_resupply', kind: 'cannon', name: '战地补给 / 抢修模组', ammo: 2400, maxAmmo: 2400, groundRangeM: 120, minRangeM: 0, splashInnerM: 0, splashOuterM: 0, damage: 0, roleTag: '后勤补给' },
    ]),
  }),
  attack_heli: Object.freeze({
    channel: 'heli',
    channelLabel: '旋翼',
    roleLabel: '武装直升机',
    subTitle: '武装直升机 · 30m 贴地越障火力支援',
    cost: 240,
    hp: 15,
    bonusArmorPct: 0,
    cruiseAltText: '低空 30m',
    payloadText: '3 挂点',
    speedKmh: 220,
    opticsM: 2000,
    caps: Object.freeze([
      { k: '清尸扫射', score: 8, tone: 'heli' },
      { k: '精确反甲', score: 9, tone: 'select' },
      { k: '立体机动', score: 8, tone: 'ground' },
    ]),
    weapons: Object.freeze([
      { id: 'heli_chain', kind: 'cannon', name: 'M230 30mm 链炮', ammo: 1200, maxAmmo: 1200, groundRangeM: 1100, minRangeM: 0, splashInnerM: 2.5, splashOuterM: 6, damage: 2.4, roleTag: '压制清群' },
      { id: 'heli_atgm', kind: 'missile', name: 'AGM-114 地狱火', ammo: 8, maxAmmo: 8, groundRangeM: 1600, minRangeM: 260, splashInnerM: 4, splashOuterM: 10, damage: 26, roleTag: '精确点杀' },
      { id: 'heli_rocket', kind: 'rocket', name: 'Hydra 70 火箭巢', ammo: 38, maxAmmo: 38, groundRangeM: 1300, minRangeM: 200, splashInnerM: 5, splashOuterM: 14, damage: 7.5, roleTag: '面域覆盖' },
    ]),
  }),
  trans_heli: Object.freeze({
    channel: 'heli',
    channelLabel: '旋翼',
    roleLabel: '运输 / 倾转机',
    subTitle: '空中机动投送 · 30m 越障补给与自卫',
    cost: 160,
    hp: 16,
    bonusArmorPct: 0,
    cruiseAltText: '低空 30m',
    payloadText: '重型运载舱',
    speedKmh: 245,
    opticsM: 1700,
    caps: Object.freeze([
      { k: '投送载荷', score: 10, tone: 'heli' },
      { k: '机动航速', score: 8, tone: 'select' },
      { k: '舱门自卫', score: 5, tone: 'ground' },
    ]),
    weapons: Object.freeze([
      { id: 'theli_mg', kind: 'cannon', name: 'M134 舱门速射机枪', ammo: 2000, maxAmmo: 2000, groundRangeM: 750, minRangeM: 0, splashInnerM: 0, splashOuterM: 0, damage: 1.6, roleTag: '自卫压制' },
    ]),
  }),
  cas: Object.freeze({
    channel: 'plane',
    channelLabel: '固定翼',
    roleLabel: '对地攻击 / 炮艇',
    subTitle: '近距空中支援 · 燃油制无血量',
    fuelArchetype: 'cas_gunship',
    afterburner: false,
    cost: 320,
    hp: 24,
    speedKmh: 520,
    opticsM: 2500,
    lowAltitudeM: 135,
    highAltitudeM: 215,
    ecmPct: 15,
    caps: Object.freeze([
      { k: '轻甲清扫', score: 9, tone: 'ground' },
      { k: '中甲压制', score: 8, tone: 'select' },
      { k: '重型爆破', score: 8, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'cas_gun', kind: 'cannon', name: '机载航炮扫射', ammo: 900, maxAmmo: 900, groundRangeM: 1000, minRangeM: 0, splashInnerM: 3, splashOuterM: 7, damage: 2.25, roleTag: '拖线扫射', inputMode: 'drag_line' },
      { id: 'cas_bomb', kind: 'bomb', name: '精确制导航弹', ammo: 4, maxAmmo: 4, groundRangeM: 1000, minRangeM: 160, splashInnerM: 10, splashOuterM: 24, damage: 44, roleTag: '连点轰炸', inputMode: 'multi_point' },
    ]),
  }),
  fighter: Object.freeze({
    channel: 'plane',
    channelLabel: '固定翼',
    roleLabel: '制空战斗 / 轰炸',
    subTitle: '高速突击战机 · 加力燃油制无血量',
    fuelArchetype: 'supersonic_multirole',
    afterburner: true,
    cost: 360,
    hp: 20,
    speedKmh: 920,
    opticsM: 2600,
    lowAltitudeM: 160,
    highAltitudeM: 260,
    ecmPct: 20,
    caps: Object.freeze([
      { k: '高速突防', score: 10, tone: 'plane' },
      { k: '精确点杀', score: 8, tone: 'select' },
      { k: '重型爆破', score: 8, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'ftr_agm', kind: 'missile', name: '空地精确导弹', ammo: 4, maxAmmo: 4, groundRangeM: 1350, minRangeM: 220, splashInnerM: 4, splashOuterM: 12, damage: 28, roleTag: '点选精打', inputMode: 'point_target' },
      { id: 'ftr_jdam', kind: 'bomb', name: '重型制导航弹', ammo: 4, maxAmmo: 4, groundRangeM: 1100, minRangeM: 180, splashInnerM: 11, splashOuterM: 26, damage: 50, roleTag: '连点轰炸', inputMode: 'multi_point' },
    ]),
  }),
  uav: Object.freeze({
    channel: 'plane',
    channelLabel: '固定翼',
    roleLabel: '侦察 / 察打无人机',
    subTitle: '长航时察打无人机 · 燃油制无血量',
    fuelArchetype: 'fixed_wing_uav',
    afterburner: false,
    cost: 150,
    hp: 10,
    speedKmh: 360,
    opticsM: 4000,
    lowAltitudeM: 170,
    highAltitudeM: 250,
    ecmPct: 10,
    caps: Object.freeze([
      { k: '广域侦察', score: 10, tone: 'plane' },
      { k: '超长留空', score: 10, tone: 'ground' },
      { k: '精确补刀', score: 7, tone: 'select' },
    ]),
    weapons: Object.freeze([
      { id: 'uav_agm', kind: 'missile', name: 'AGM-114 察打导弹', ammo: 4, maxAmmo: 4, groundRangeM: 1400, minRangeM: 180, splashInnerM: 3.5, splashOuterM: 9, damage: 24, roleTag: '点选精打', inputMode: 'point_target' },
    ]),
  }),
});

/**
 * 核心标杆车型精校覆写表（按 slug 对齐 brokenarrowstats.com 与 SCAV-6 实装口径）。
 */
export const VEHICLE_DETAIL_OVERRIDES = Object.freeze({
  US_M1A2: Object.freeze({
    shortName: 'M1A2 SEP v3',
    subTitle: '重型主战坦克 · 反应甲强化',
    cost: 285,
    hp: 20,
    bonusArmorPct: 12,
    armorText: '700 / 1200',
    speedKmh: 67,
    opticsM: 1400,
    sourceBadge: '原站精校',
    weapons: Object.freeze([
      { id: 'm256_he', kind: 'cannon', name: '120mm 高爆弹', ammo: 28, maxAmmo: 28, groundRangeM: 1400, minRangeM: 0, splashInnerM: 3, splashOuterM: 9, damage: 12, roleTag: '面伤清群' },
      { id: 'm1028', kind: 'cannon', name: 'M1028 霰弹', ammo: 14, maxAmmo: 14, groundRangeM: 450, minRangeM: 0, splashInnerM: 6, splashOuterM: 18, damage: 18, roleTag: '近距尸潮' },
      { id: 'm240', kind: 'cannon', name: '7.62mm 同轴机枪', ammo: 2000, maxAmmo: 2000, groundRangeM: 600, minRangeM: 0, splashInnerM: 0, splashOuterM: 0, damage: 1.5, roleTag: '持续补刀' },
    ]),
  }),
  US_AH64D: Object.freeze({
    shortName: 'AH-64D',
    subTitle: '武装直升机 · 30m 贴地越障',
    cost: 240,
    hp: 15,
    cruiseAltText: '低空 30m',
    payloadText: '3 挂点',
    speedKmh: 220,
    opticsM: 2000,
    sourceBadge: '原站精校',
  }),
  US_A10: Object.freeze({
    shortName: 'A-10C',
    subTitle: '对地攻击机 · 燃油制无血量',
    fuelArchetype: 'cas_gunship',
    afterburner: false,
    cost: 345,
    hp: 30,
    speedKmh: 450,
    opticsM: 2500,
    lowAltitudeM: 135,
    highAltitudeM: 215,
    ecmPct: 20,
    sourceBadge: '原站精校',
    caps: Object.freeze([
      { k: '轻甲清扫', score: 10, tone: 'ground' },
      { k: '中甲压制', score: 9, tone: 'select' },
      { k: '重型爆破', score: 9, tone: 'danger' },
    ]),
    weapons: Object.freeze([
      { id: 'gau8_30mm', kind: 'cannon', name: 'GAU-8/A 30mm', ammo: 1150, maxAmmo: 1150, groundRangeM: 1000, minRangeM: 0, splashInnerM: 3, splashOuterM: 7, damage: 2.25, roleTag: '拖线扫射', inputMode: 'drag_line', actionCmd: 'strafe' },
      { id: 'hydra70_rocket', kind: 'rocket', name: 'Hydra 70 火箭', ammo: 42, maxAmmo: 42, groundRangeM: 1100, minRangeM: 140, splashInnerM: 5, splashOuterM: 14, damage: 7.5, roleTag: '拖线覆盖', inputMode: 'drag_line', actionCmd: 'rocket' },
      { id: 'agm65_maverick', kind: 'missile', name: 'AGM-65 小牛', ammo: 6, maxAmmo: 6, groundRangeM: 1250, minRangeM: 200, splashInnerM: 4, splashOuterM: 11, damage: 22, roleTag: '点选精打', inputMode: 'point_target', actionCmd: 'missile' },
      { id: 'gbu31_jdam', kind: 'bomb', name: 'GBU-31 JDAM', ammo: 4, maxAmmo: 4, groundRangeM: 1000, minRangeM: 180, splashInnerM: 12, splashOuterM: 28, damage: 56, roleTag: '连点轰炸', inputMode: 'multi_point', actionCmd: 'bomb' },
    ]),
  }),
  US_B1B: Object.freeze({
    shortName: 'B-1B Lancer',
    subTitle: '战略轰炸机 · 加力燃油制无血量',
    fuelArchetype: 'strategic_stealth_bomber',
    afterburner: true,
    cost: 440,
    hp: 26,
    speedKmh: 960,
    opticsM: 2400,
    lowAltitudeM: 190,
    highAltitudeM: 300,
    ecmPct: 25,
    sourceBadge: '原站精校',
    caps: Object.freeze([
      { k: '战略轰炸', score: 10, tone: 'danger' },
      { k: '高速突防', score: 9, tone: 'plane' },
      { k: '留空续航', score: 9, tone: 'ground' },
    ]),
    weapons: Object.freeze([
      { id: 'b1b_jdam', kind: 'bomb', name: 'GBU-31 重型航弹舱', ammo: 24, maxAmmo: 24, groundRangeM: 1200, minRangeM: 240, splashInnerM: 14, splashOuterM: 34, damage: 56, roleTag: '地毯轰炸', inputMode: 'multi_point', actionCmd: 'bomb' },
    ]),
  }),
});

/**
 * 无 DOM / Three.js 依赖的 12 类兵种识别器（与主入口、实战 HUD、武器库完全同源）。
 */
export function resolveVehicleKindFromRecord(rec = {}) {
  const existing = String(rec?.iconKind || '').trim();
  if (existing && VEHICLE_ARCHETYPE_PROFILES[existing]) return existing;
  const slug = String(rec?.slug || rec?.id || '').toLowerCase();
  const isAir = rec?.category === 'air' || rec?.role === 'fixed_wing' || rec?.role === 'helicopter' || rec?.role === 'rotorcraft' || rec?.role === 'uav';
  if (isAir) {
    if (rec?.role === 'uav' || slug.includes('mq') || slug.includes('rq')) return 'uav';
    if (slug.includes('ah') || slug.includes('oh58') || slug.includes('rah66')) return 'attack_heli';
    if (rec?.role === 'helicopter' || rec?.role === 'rotorcraft' || slug.includes('uh') || slug.includes('ch') || slug.includes('mh') || slug.includes('v-22') || slug.includes('cv-22') || slug.includes('mv-22')) return 'trans_heli';
    if (slug.includes('f15') || slug.includes('f16') || slug.includes('f22') || slug.includes('f35') || slug.includes('fa18') || slug.includes('b1b') || slug.includes('b2') || slug.includes('b52')) return 'fighter';
    return 'cas';
  }
  if (slug.includes('m1a') || slug.includes('m1_abv') || slug.includes('m8') || slug.includes('m60') || slug.includes('booker')) return 'mbt';
  if (slug.includes('mlrs') || slug.includes('himars') || slug.includes('m109') || slug.includes('paladin') || slug.includes('arty')) return 'arty';
  if (slug.includes('adats') || slug.includes('avenger') || slug.includes('shorad') || slug.includes('patriot') || slug.includes('cram') || slug.includes('slamraam')) return 'aa';
  if (slug.includes('lav_at') || slug.includes('tow') || slug.includes('m901')) return 'atgm';
  if (slug.includes('lmtv') || slug.includes('hemtt') || slug.includes('supply') || slug.includes('m548') || slug.includes('mtvr')) return 'supply';
  if (slug.includes('hmmwv') || slug.includes('m1151') || slug.includes('flyer') || slug.includes('isv') || slug.includes('matv')) return 'recon';
  return 'ifv';
}

function resolveFuelArchetypeForSlug(slug, iconKind, baseArch) {
  const s = String(slug || '').toLowerCase();
  if (iconKind === 'uav' || s.includes('mq') || s.includes('rq')) return 'fixed_wing_uav';
  if (s.includes('b1b') || s.includes('b2') || s.includes('b52') || s.includes('f117')) return 'strategic_stealth_bomber';
  if (s.includes('c17') || s.includes('c130') || s.includes('kc130') || s.includes('ea6') || s.includes('ea18') || s.includes('prowler')) return 'heavy_support_ew';
  if (s.includes('f15') || s.includes('f16') || s.includes('f22') || s.includes('f35') || s.includes('fa18')) return 'supersonic_multirole';
  return baseArch || 'cas_gunship';
}

function formatClockSec(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const INPUT_MODE_ROLE_LABELS = Object.freeze({
  drag_line: '拖线攻击',
  multi_point: '连点打击',
  point_target: '点选精打',
  passive: '被动防护',
});

/**
 * 统一跨页载具参数详情视图模型派生器（供 /play 实战详情卡、兵牌悬停浮卡、/armory 右栏与 /ui-spec 共用）。
 * 严格经由 GLOBAL_BALANCE_SCALES 派生，确保固定翼零血量改显 SCAV-6 燃油/工况/Bingo/整备口径。
 */
export function deriveVehicleDetailSpec(recOrUnit = {}, options = {}) {
  const mode = options.mode === 'armory' ? 'armory' : 'play';
  const scales = options.scales || GLOBAL_BALANCE_SCALES;
  const rt = options.runtimeTelemetry || null;
  const rtWeapons = Array.isArray(options.runtimeWeapons) ? options.runtimeWeapons : null;
  const stageInfo = options.stageInfo || null;

  const slug = String(recOrUnit?.slug || recOrUnit?.short_id || recOrUnit?.id || 'UNKNOWN').trim();
  const iconKind = resolveVehicleKindFromRecord(recOrUnit);
  const proto = VEHICLE_ARCHETYPE_PROFILES[iconKind] || VEHICLE_ARCHETYPE_PROFILES.ifv;
  const ov = VEHICLE_DETAIL_OVERRIDES[slug] || null;
  const merged = ov ? { ...proto, ...ov } : proto;

  const channel = recOrUnit?.channel || merged.channel || 'ground';
  const isPlane = channel === 'plane' || recOrUnit?.role === 'fixed_wing' || iconKind === 'cas' || iconKind === 'fighter' || iconKind === 'uav';
  const isHeli = !isPlane && (channel === 'heli' || recOrUnit?.role === 'helicopter' || iconKind === 'attack_heli' || iconKind === 'trans_heli');
  const resolvedChannel = isPlane ? 'plane' : (isHeli ? 'heli' : 'ground');
  const channelLabel = isPlane ? '固定翼' : (isHeli ? '旋翼' : '地面');

  const shortName = String(recOrUnit?.shortName || ov?.shortName || recOrUnit?.name || slug).trim();
  const fullModelName = String(recOrUnit?.fullModelName || recOrUnit?.name || shortName).trim();
  const subTitle = merged.subTitle || merged.roleLabel;
  const costScale = Number.isFinite(scales.costScale) ? scales.costScale : 1.0;
  const hpScale = Number.isFinite(scales.hpScale) ? scales.hpScale : 1.0;
  const opticsScale = Number.isFinite(scales.opticsScale) ? scales.opticsScale : 1.0;

  // 武器统一派生（优先消费实战实时武器遥测，否则经 deriveWeaponStatsFromSourceRef 派生原型/精校武器）
  let weapons;
  if (rtWeapons && rtWeapons.length > 0) {
    weapons = rtWeapons.map((w, idx) => ({
      id: w.id || `wep_${idx}`,
      kind: w.kind || 'cannon',
      name: w.name || '武器系统',
      ammo: Number.isFinite(w.ammo) ? w.ammo : 0,
      maxAmmo: Number.isFinite(w.maxAmmo) ? w.maxAmmo : 0,
      minRangeM: Math.max(0, Math.round(Number(w.minRangeM) || 0)),
      maxRangeM: Math.max(0, Math.round(Number(w.maxRangeM) || 0)),
      splashInnerM: Number(w.splashInnerM) || 0,
      splashOuterM: Number(w.splashOuterM) || 0,
      damage: Number(w.damage) || 0,
      salvoCount: Number(w.salvoCount) || 1,
      inputMode: w.inputMode || 'point_target',
      roleTag: w.roleTag || INPUT_MODE_ROLE_LABELS[w.inputMode] || '装备',
      actionCmd: w.actionCmd || '',
      canTargetAir: Boolean(w.canTargetAir),
      clickable: Boolean(mode === 'play' && isPlane && w.actionCmd),
    }));
  } else {
    const rawList = Array.isArray(merged.weapons) ? merged.weapons : [];
    weapons = rawList.map((w, idx) => {
      const derived = deriveWeaponStatsFromSourceRef(
        { groundRangeM: w.groundRangeM, minRangeM: w.minRangeM, damage: w.damage },
        w,
        scales,
      );
      return {
        id: derived.id || `wep_${idx}`,
        kind: derived.kind || 'cannon',
        name: derived.name || '标准武器',
        ammo: Number.isFinite(derived.ammo) ? derived.ammo : (derived.maxAmmo || 0),
        maxAmmo: Number.isFinite(derived.maxAmmo) ? derived.maxAmmo : 0,
        minRangeM: derived.minRangeM,
        maxRangeM: derived.maxRangeM,
        splashInnerM: Number(derived.splashInnerM) || 0,
        splashOuterM: Number(derived.splashOuterM) || 0,
        damage: derived.damage,
        salvoCount: Number(derived.salvoCount) || 1,
        inputMode: derived.inputMode || 'point_target',
        roleTag: derived.roleTag || INPUT_MODE_ROLE_LABELS[derived.inputMode] || '常规',
        actionCmd: derived.actionCmd || '',
        canTargetAir: Boolean(derived.canTargetAir),
        clickable: false,
      };
    });
  }

  let cost = Math.max(1, Math.round((Number(merged.cost) || 180) * costScale));
  let usesFuel = false;
  let hp = null;
  let maxHp = null;
  let bonusArmorPct = 0;
  let fuel = null;
  let kpis = [];
  let caps = Array.isArray(merged.caps) ? merged.caps.map(c => ({ ...c })) : [];
  const foldMeta = [];

  if (isPlane) {
    usesFuel = true;
    const fuelArch = resolveFuelArchetypeForSlug(slug, iconKind, merged.fuelArchetype);
    const hasAfterburner = Boolean(merged.afterburner || fuelArch === 'supersonic_multirole' || slug.includes('B1B'));
    const derivedAir = deriveAircraftStatsFromSourceRef(
      { cost: merged.cost, hp: merged.hp, speedKmh: merged.speedKmh, opticsM: merged.opticsM },
      { archetype: fuelArch, afterburner: hasAfterburner, lowAltitudeM: merged.lowAltitudeM, highAltitudeM: merged.highAltitudeM },
      scales,
    );
    const gs = rt?.gameStats || derivedAir;
    cost = Number(gs.cost) || derivedAir.cost;
    const fuelMaxSec = Math.max(45, Math.round(Number(rt?.fuelMaxSec ?? gs.fuelMaxSec ?? derivedAir.fuelMaxSec)));
    const fuelPct = mode === 'armory' ? 100 : Math.max(0, Math.min(100, Math.round(Number(rt?.fuelPct ?? 100))));
    const fuelRemainSec = mode === 'armory' ? fuelMaxSec : Math.max(0, Math.round(Number(rt?.fuelRemainSec ?? fuelMaxSec)));
    const fuelBurnRate = mode === 'armory' ? 1.0 : Number(rt?.fuelBurnRate ?? 1.0);
    const fuelModeLabel = mode === 'armory' ? '满油巡航' : (rt?.fuelModeLabel || '巡航');
    const bingoThresholdPct = Math.round(Number(rt?.bingoThresholdPct ?? 14));
    const bingoState = mode === 'armory' ? 'nominal' : (rt?.bingoState || 'nominal');
    const rearmCooldownSec = Math.round(Number(rt?.rearmCooldownSec ?? gs.rearmCooldownSec ?? derivedAir.rearmCooldownSec));
    const visionM = Math.round(Number(gs.visionRangeM ?? derivedAir.visionRangeM));

    fuel = {
      pct: fuelPct,
      remainSec: fuelRemainSec,
      maxSec: fuelMaxSec,
      burnRate: fuelBurnRate,
      modeLabel: fuelModeLabel,
      bingoThresholdPct,
      bingoState,
      rearmCooldownSec,
    };

    kpis = [
      { k: mode === 'armory' ? '满油留空' : '当前余油', v: formatClockSec(mode === 'armory' ? fuelMaxSec : fuelRemainSec) },
      { k: '工况油耗', v: mode === 'armory' ? `1.0–${derivedAir.fuelBurnRates.sprint}×` : `${fuelModeLabel} ${fuelBurnRate.toFixed(2).replace(/\.00$/, '.0')}×` },
      { k: '整备冷却', v: `${rearmCooldownSec}s` },
      { k: '侦察视野', v: `${visionM}m` },
    ];

    if (rt?.gameStats?.firepowerVs) {
      const fp = rt.gameStats.firepowerVs;
      caps = [
        { k: '轻甲清扫', score: fp.light ?? 8, tone: 'ground' },
        { k: '中甲压制', score: fp.medium ?? 8, tone: 'select' },
        { k: '重型爆破', score: fp.heavy ?? 8, tone: 'danger' },
      ];
    }

    const lowAlt = gs.lowAltitudeM || derivedAir.lowAltitudeM;
    const highAlt = gs.highAltitudeM || derivedAir.highAltitudeM;
    const cruiseKmh = gs.cruiseSpeedKmh || derivedAir.cruiseSpeedKmh;
    const attackKmh = gs.attackSpeedKmh || derivedAir.attackSpeedKmh;
    const orbitR0 = rt?.orbitRadiusRange?.[0] || derivedAir.orbitRadiusRange[0];
    const orbitR1 = rt?.orbitRadiusRange?.[1] || derivedAir.orbitRadiusRange[1];
    foldMeta.push(
      ['型号代号', slug],
      ['巡航/突击', `${cruiseKmh} / ${attackKmh} km/h`],
      ['作战高度', `${lowAlt}–${highAlt}m`],
      ['盘旋半径', `${orbitR0}–${orbitR1}m`],
      ['电子防护', `ECM ${merged.ecmPct ?? 15}%`],
    );
  } else if (isHeli) {
    maxHp = roundTo(Math.max(1, (Number(recOrUnit?.maxHp) || Number(merged.hp) || 15) * (recOrUnit?.maxHp ? 1 : hpScale)), 1);
    hp = mode === 'armory' ? maxHp : roundTo(Math.max(0, Math.min(maxHp, Number.isFinite(recOrUnit?.hp) ? recOrUnit.hp : maxHp)), 1);
    const visionM = Math.max(200, Math.round((Number(merged.opticsM) || 1800) * opticsScale));
    const speedKmh = Math.round(Number(merged.speedKmh) || 220);
    const payloadTxt = merged.payloadText || `${weapons.length} 挂点`;
    kpis = [
      { k: '巡航高度', v: merged.cruiseAltText || '低空 30m' },
      { k: '载荷挂点', v: payloadTxt },
      { k: '机动航速', v: `${speedKmh} km/h` },
      { k: '侦察视野', v: `${visionM}m` },
    ];
    foldMeta.push(
      ['型号代号', slug],
      ['兵种定位', merged.roleLabel],
      ['越障机制', '30m 前瞻平滑抬升'],
    );
  } else {
    maxHp = roundTo(Math.max(1, (Number(recOrUnit?.maxHp) || Number(merged.hp) || 16) * (recOrUnit?.maxHp ? 1 : hpScale)), 1);
    hp = mode === 'armory' ? maxHp : roundTo(Math.max(0, Math.min(maxHp, Number.isFinite(recOrUnit?.hp) ? recOrUnit.hp : maxHp)), 1);
    bonusArmorPct = Number.isFinite(recOrUnit?.bonus) ? recOrUnit.bonus : (merged.bonusArmorPct || 0);
    const visionM = Math.max(200, Math.round((Number(merged.opticsM) || 1400) * opticsScale));
    const speedKmh = Math.round(Number(merged.speedKmh) || 64);
    const primaryAmmo = weapons[0]?.maxAmmo > 0 ? `${weapons[0].ammo}/${weapons[0].maxAmmo}` : `${weapons.length} 武器`;
    kpis = [
      { k: '装甲防护', v: merged.armorText || '220 / 420' },
      { k: '公路机动', v: `${speedKmh} km/h` },
      { k: '侦察视野', v: `${visionM}m` },
      { k: '主炮弹药', v: primaryAmmo },
    ];
    foldMeta.push(
      ['型号代号', slug],
      ['兵种定位', merged.roleLabel],
    );
  }

  // 合并武器库 3D 工程实测信息到折叠区（零主层噪音）
  if (stageInfo?.dimensions) {
    const d = stageInfo.dimensions;
    foldMeta.push(['实测尺度', `${d.len} × ${d.wid} × ${d.hei} m`]);
  } else if (mode === 'armory') {
    foldMeta.push(['实测尺度', '测量中…']);
  }
  if (stageInfo?.slotText) {
    foldMeta.push(['可动机构', stageInfo.slotText]);
  }
  if (stageInfo?.rosterText) {
    foldMeta.push(['出击状态', stageInfo.rosterText]);
  }
  foldMeta.push(
    ['数值口径', `${ov?.sourceBadge || '原型基底派生'} · 1.0×`],
    ['基底来源', 'brokenarrowstats.com'],
  );

  return {
    slug,
    shortName,
    fullModelName,
    subTitle,
    iconKind,
    channel: resolvedChannel,
    channelLabel,
    cost,
    mode,
    usesFuel,
    hp,
    maxHp,
    bonusArmorPct,
    fuel,
    kpis,
    caps,
    weapons,
    foldMeta,
    specVersion: scales.specVersion || 'ba-source-ssot-v1',
  };
}
