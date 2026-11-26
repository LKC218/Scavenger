/**
 * Scavenger 操作交互系统 —— 命令契约、CQS 规划/调度核心与模式/ESC 仲裁器。
 * 本模块无 DOM 与 Three.js 顶层依赖，可在浏览器与 Node ESM 规则单测间零修改共用。
 */

import { VEHICLE_META } from '../界面规范/图标与标绘.mjs';

export const INTERACTION_MODES = Object.freeze({
  PLAY: 'play',
  EDITOR: 'editor',
  TARGETING: 'targeting',
  DEPLOY: 'deploy',
  WHEEL: 'wheel',
  PAUSED: 'paused',
});

export const CURSOR_CLASSES = Object.freeze({
  default: 'ui-cur-default',
  move: 'ui-cur-move',
  attack: 'ui-cur-attack',
  invalid: 'ui-cur-invalid',
  deploy: 'ui-cur-action',
});

export const DEFAULT_ROSTER_SLUGS = Object.freeze(['US_M1A2', 'US_AH64D', 'US_A10']);

export const MAX_HERO_UNITS = 50;

const SHORT_MODEL_OVERRIDES = Object.freeze({
  US_M1A2: { shortModel: 'M1A2 SEP v3', fullModel: 'M1A2 SEP v3' },
  US_AH64D: { shortModel: 'AH-64D', fullModel: 'AH-64D Apache' },
  US_A10: { shortModel: 'A-10C', fullModel: 'A-10C Thunderbolt II' },
  US_AC130J: { shortModel: 'AC-130J', fullModel: 'AC-130J Ghostrider' },
  US_AC130U: { shortModel: 'AC-130U', fullModel: 'AC-130U Spooky II' },
  'US_LAV_AT-M_body': { shortModel: 'LAV-AT-M', fullModel: 'LAV-AT-M' },
  US_M1_ABV: { shortModel: 'M1150 ABV', fullModel: 'M1150 Assault Breacher' },
  US_PATRIOT_PAC3MSE: { shortModel: 'Patriot PAC-3', fullModel: 'Patriot PAC-3 MSE' },
  US_AV8B_NightAttack: { shortModel: 'AV-8B(NA)', fullModel: 'AV-8B Night attack' },
  US_AV8B_Plus: { shortModel: 'AV-8B+', fullModel: 'AV-8B Harrier II Plus' },
  US_B52H: { shortModel: 'B-52H', fullModel: 'B-52H Stratofortress' },
  US_C130_SuperHercules: { shortModel: 'C-130J-30', fullModel: 'C-130J-30 Hercules' },
  US_CH53: { shortModel: 'CH-53E', fullModel: 'CH-53E Super Stallion' },
  US_CH53K: { shortModel: 'CH-53K', fullModel: 'CH-53K King Stallion' },
  US_F15E: { shortModel: 'F-15E', fullModel: 'F-15E Strike Eagle' },
  US_F16block52: { shortModel: 'F-16C', fullModel: 'F-16C Fighting Falcon' },
  US_KC130_HarvestHawk: { shortModel: 'KC-130J', fullModel: 'KC-130J Harvest Hawk' },
  US_MH60X: { shortModel: 'MH-60X', fullModel: 'MH-60X Silent Hawk' },
  US_OH58Kiowa: { shortModel: 'OH-58D', fullModel: 'OH-58D Kiowa Warrior' },
});

const STRIP_NICKNAME_MAP = Object.freeze({
  'Thunderbolt II': '',
  'Stratofortress': '',
  'Super Stallion': '',
  'King Stallion': '',
  'Strike Eagle': '',
  'Fighting Falcon': '',
  'Harvest Hawk': '',
  'Silent Hawk': '',
  'Kiowa Warrior': '',
  'Apache': '',
  'Raptor': '',
  'Black Hawk': '',
  'Night attack': '',
  'Harrier II Plus': '',
});

/**
 * 统一 129 车纯军标型号清洗器：
 * - 移除一切中文外号/兵种后缀（如 铁砧/游隼/雷霆/阿帕奇/疣猪/· 主战坦克）；
 * - 清洗 US_ 前缀、_body 后缀与下划线，并将超长英文绰号精简为卡片友好的纯军标代号（1A）。
 */
export function formatVehicleModelDesignation(slug, rawName = '') {
  const cleanSlug = String(slug || '').trim();
  const preset = SHORT_MODEL_OVERRIDES[cleanSlug];
  if (preset) return { ...preset };

  let fullModel = String(rawName || cleanSlug || 'UNKNOWN').trim();
  // 若带中文外号/后缀（如 "铁砧 01 · M1A2 SEP v3" 或 "M1A2 · 主战坦克"），抽取纯英文军标部分
  if (/[\u4e00-\u9fff]/.test(fullModel)) {
    const parts = fullModel.split(/[·|｜]/).map(p => p.trim()).filter(Boolean);
    const nonCn = parts.filter(p => !/[\u4e00-\u9fff]/.test(p));
    if (nonCn.length) {
      fullModel = nonCn[nonCn.length - 1];
    } else {
      fullModel = fullModel.replace(/[\u4e00-\u9fff]+/g, ' ').replace(/\s+/g, ' ').trim();
    }
  }
  fullModel = fullModel
    .replace(/^US_/i, '')
    .replace(/_body$/i, '')
    .replace(/_/g, '-')
    .replace(/\s+/g, ' ')
    .trim();
  if (!fullModel) {
    fullModel = cleanSlug.replace(/^US_/i, '').replace(/_body$/i, '').replace(/_/g, '-') || 'UNKNOWN';
  }

  let shortModel = fullModel;
  for (const [nick, rep] of Object.entries(STRIP_NICKNAME_MAP)) {
    if (shortModel.includes(nick)) {
      shortModel = shortModel.replace(nick, rep).replace(/\s+/g, ' ').trim();
    }
  }
  if (shortModel.length > 18) {
    const tokens = shortModel.split(' ');
    if (tokens.length > 2 && /^[A-Z0-9-]+$/i.test(tokens[0])) {
      shortModel = tokens.slice(0, 2).join(' ');
    }
  }
  return { shortModel, fullModel };
}

/**
 * 为同型在场载具生成统一纯军标显示名：同型号仅 1 辆时不带序号，2 辆及以上自动加 #01 / #02。
 */
export function formatUnitDisplayNames(units = []) {
  const counts = new Map();
  for (const u of units) {
    const k = String(u?.slug || u?.id || '');
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  const seen = new Map();
  return units.map(u => {
    const k = String(u?.slug || u?.id || '');
    const total = counts.get(k) || 1;
    const idx = (seen.get(k) || 0) + 1;
    seen.set(k, idx);
    const { shortModel, fullModel } = formatVehicleModelDesignation(u?.slug, u?.fullModelName || u?.name || u?.shortName);
    const tagged = total > 1 ? `${shortModel} #${String(idx).padStart(2, '0')}` : shortModel;
    return { id: u?.id, shortModel, fullModel, displayName: tagged, sameSlugCount: total, index: idx };
  });
}

export const DEPLOY_CATEGORY_QUOTAS = Object.freeze({
  armor: 12,
  recon_ifv: 16,
  support: 8,
  heli: 8,
  plane: 6,
});

export const DEPLOY_CATEGORY_LABELS = Object.freeze({
  armor: '重甲',
  recon_ifv: '战车与侦察',
  support: '火炮与防空',
  heli: '旋翼',
  plane: '固定翼',
});

export function normalizeDeployCategoryKey(catKey) {
  if (!catKey || catKey === 'all') return 'all';
  if (catKey === 'heavy' || catKey === 'armor') return 'armor';
  if (catKey === 'ifv_recon' || catKey === 'recon_ifv') return 'recon_ifv';
  if (catKey === 'arty_aa' || catKey === 'support') return 'support';
  if (catKey === 'heli' || catKey === 'helicopter') return 'heli';
  if (catKey === 'plane' || catKey === 'fixed_wing') return 'plane';
  return 'recon_ifv';
}

export function resolveDeployCategory(unitOrRec) {
  if (!unitOrRec) return 'recon_ifv';
  const arch = (unitOrRec.iconKind && unitOrRec.role)
    ? unitOrRec
    : resolveVehicleArchetype(unitOrRec);
  const { iconKind, role } = arch;
  if (role === 'helicopter' || iconKind === 'attack_heli' || iconKind === 'trans_heli') return 'heli';
  if (role === 'fixed_wing' || iconKind === 'cas' || iconKind === 'fighter' || iconKind === 'uav') return 'plane';
  if (iconKind === 'mbt') return 'armor';
  if (iconKind === 'arty' || iconKind === 'aa') return 'support';
  return 'recon_ifv';
}

export function computeDeployQuotaSummary(units = []) {
  const counts = {
    armor: 0,
    recon_ifv: 0,
    support: 0,
    heli: 0,
    plane: 0,
  };
  for (const u of units || []) {
    const cat = resolveDeployCategory(u);
    if (counts[cat] !== undefined) counts[cat] += 1;
  }
  const categories = {};
  for (const [k, max] of Object.entries(DEPLOY_CATEGORY_QUOTAS)) {
    const count = counts[k] || 0;
    const isFull = count >= max;
    categories[k] = {
      key: k,
      label: DEPLOY_CATEGORY_LABELS[k],
      count,
      max,
      quota: max,
      remaining: Math.max(0, max - count),
      full: isFull,
      isFull,
    };
  }
  const totalCount = (units || []).length;
  return {
    total: totalCount,
    totalCount,
    maxTotal: MAX_HERO_UNITS,
    isTotalFull: totalCount >= MAX_HERO_UNITS,
    counts,
    quotas: { ...DEPLOY_CATEGORY_QUOTAS },
    categories,
  };
}

export const DEFAULT_MAP_BOUNDS = Object.freeze({
  minX: -4425,
  maxX: 4575,
  minZ: -4150,
  maxZ: 4850,
});

const ROLE_DEFAULT_SPEED = Object.freeze({
  ground: 14.0,
  helicopter: 26.0,
  fixed_wing: 52.0,
});

const KIND_SPEED_SCALE = Object.freeze({
  mbt: 14.0,
  ifv: 17.5,
  recon: 21.0,
  arty: 12.0,
  aa: 15.0,
  atgm: 16.0,
  supply: 13.5,
  attack_heli: 26.0,
  trans_heli: 23.0,
  cas: 52.0,
  fighter: 68.0,
  uav: 40.0,
});

const KIND_PICK_RADIUS = Object.freeze({
  mbt: 5.4,
  ifv: 4.8,
  recon: 4.2,
  arty: 5.4,
  aa: 5.0,
  atgm: 4.6,
  supply: 4.8,
  attack_heli: 6.4,
  trans_heli: 7.2,
  cas: 7.6,
  fighter: 7.8,
  uav: 6.0,
});

/**
 * 将载具元数据统一映射至 12 类标准兵种剪影、通道及运动角色。
 */
export function resolveVehicleArchetype(recOrSlug) {
  const rec = typeof recOrSlug === 'string' ? { slug: recOrSlug } : (recOrSlug || {});
  const slug = String(rec.slug || rec.id || '').toLowerCase();
  const isAir = rec.category === 'air'
    || slug.includes('ah64') || slug.includes('ah1') || slug.includes('oh58') || slug.includes('rah66')
    || slug.includes('uh60') || slug.includes('ch47') || slug.includes('ch53') || slug.includes('mh')
    || slug.includes('v22') || slug.includes('v-22') || slug.includes('a10') || slug.includes('ac130')
    || slug.includes('av8') || slug.includes('f15') || slug.includes('f16') || slug.includes('f22')
    || slug.includes('f35') || slug.includes('b1b') || slug.includes('b2') || slug.includes('b52') || slug.includes('mq')
    || slug.includes('rq') || slug.includes('c130') || slug.includes('c17') || slug.includes('kc130')
    || slug.includes('ea6') || slug.includes('ea18') || slug.includes('p8') || slug.includes('e3')
    || slug.includes('f18');

  let iconKind = 'ifv';
  if (isAir) {
    if (slug.includes('ah') || slug.includes('oh58') || slug.includes('rah66')) iconKind = 'attack_heli';
    else if (slug.includes('uh') || slug.includes('ch') || slug.includes('mh') || slug.includes('v22') || slug.includes('v-22')) iconKind = 'trans_heli';
    else if (slug.includes('mq') || slug.includes('rq')) iconKind = 'uav';
    else if (slug.includes('f15') || slug.includes('f16') || slug.includes('f22') || slug.includes('f35') || slug.includes('f18') || slug.includes('b1') || slug.includes('b2') || slug.includes('b52')) iconKind = 'fighter';
    else iconKind = 'cas';
  } else if (slug.includes('m1a') || slug.includes('m8') || slug.includes('booker') || slug.includes('m60') || slug.includes('abrams')) {
    iconKind = 'mbt';
  } else if (slug.includes('mlrs') || slug.includes('himars') || slug.includes('m109') || slug.includes('paladin') || slug.includes('arty')) {
    iconKind = 'arty';
  } else if (slug.includes('adats') || slug.includes('avenger') || slug.includes('shorad') || slug.includes('patriot') || slug.includes('cram') || slug.includes('slamraam')) {
    iconKind = 'aa';
  } else if (slug.includes('lav_at') || slug.includes('tow') || slug.includes('m901')) {
    iconKind = 'atgm';
  } else if (slug.includes('lmtv') || slug.includes('hemtt') || slug.includes('supply') || slug.includes('m548') || slug.includes('pls') || slug.includes('f350')) {
    iconKind = 'supply';
  } else if (slug.includes('hmmwv') || slug.includes('m1151') || slug.includes('flyer') || slug.includes('isv') || slug.includes('matv')) {
    iconKind = 'recon';
  }

  const meta = VEHICLE_META[iconKind] || VEHICLE_META.ifv;
  const role = meta.channel === 'heli'
    ? 'helicopter'
    : meta.channel === 'plane'
      ? 'fixed_wing'
      : 'ground';

  return {
    iconKind,
    channel: meta.channel,
    role,
    cat: meta.channel === 'ground' ? 'armor' : meta.channel,
    speed: KIND_SPEED_SCALE[iconKind] ?? ROLE_DEFAULT_SPEED[role] ?? 14.0,
    ringRadius: meta.ringRadius ?? 4.8,
    pickRadius: KIND_PICK_RADIUS[iconKind] ?? 5.2,
    label: meta.label,
  };
}

/**
 * 规范化朝向角至 [-PI, PI]。
 */
export function wrapAngleRad(rad) {
  let a = rad % (Math.PI * 2);
  if (a > Math.PI) a -= Math.PI * 2;
  if (a < -Math.PI) a += Math.PI * 2;
  return a;
}

/**
 * 按频道与筛选条件判断单位是否可参与当前选择与指令。
 */
export function isUnitAllowedByChannel(unit, channelFilter = 'all') {
  if (!unit || unit.alive === false || (unit.hp !== undefined && unit.hp <= 0)) return false;
  if (channelFilter === 'all') return true;
  if (channelFilter === 'ground') return unit.role === 'ground';
  if (channelFilter === 'heli') return unit.role === 'helicopter';
  if (channelFilter === 'plane') return unit.role === 'fixed_wing';
  if (channelFilter === 'air') return unit.role !== 'ground';
  return true;
}

/**
 * 读取单位当前位置坐标（兼容 Three.js node.position 与纯 {x, y, z} 规则对象）。
 */
export function getUnitWorldPos(unit) {
  if (unit?.node?.position) {
    return {
      x: Number(unit.node.position.x) || 0,
      y: Number(unit.node.position.y) || 0,
      z: Number(unit.node.position.z) || 0,
    };
  }
  return {
    x: Number(unit?.x) || 0,
    y: Number(unit?.y) || 0,
    z: Number(unit?.z) || 0,
  };
}

/**
 * 读取追加队列时的队形参考起点：优先使用队列末段目的地，否则取当前位置。
 */
export function getFormationReference(unit, append = false) {
  if (append) {
    const jobs = [unit.job, ...(Array.isArray(unit.queue) ? unit.queue : [])].filter(Boolean);
    for (let i = jobs.length - 1; i >= 0; i -= 1) {
      const pt = jobs[i]?.point;
      if (pt && Number.isFinite(pt.x) && Number.isFinite(pt.z)) {
        return { x: pt.x, z: pt.z };
      }
    }
  }
  const pos = getUnitWorldPos(unit);
  return { x: pos.x, z: pos.z };
}

/**
 * 仅作只读目的地可行性检查（绝不更改单位状态或擅自换点）。
 */
export function evaluateDestination({ unit, point, refPos, env }) {
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.z)) {
    return { status: 'rejected', reason: '目的地坐标无效' };
  }
  const bounds = env?.bounds || DEFAULT_MAP_BOUNDS;
  if (point.x < bounds.minX || point.x > bounds.maxX || point.z < bounds.minZ || point.z > bounds.maxZ) {
    return { status: 'rejected', reason: '目的地超出 9km 战场边界' };
  }
  if (unit.role === 'fixed_wing') {
    return { status: 'ready', reason: '', waypoints: [[point.x, 234.0, point.z]] };
  }

  const supportState = env?.querySupport ? env.querySupport(point.x, point.z) : { state: 'ready', height: 0 };
  if (supportState?.state === 'pending') {
    return { status: 'pending', reason: '准备路径 · 目标地形尚在加载', waypoints: [] };
  }
  if (!supportState || supportState.state !== 'ready') {
    return { status: 'rejected', reason: '目的地缺少有效地面支撑' };
  }

  if (unit.role === 'helicopter') {
    const waypoints = env?.buildHeliProfile
      ? env.buildHeliProfile(refPos.x, refPos.z, point.x, point.z)
      : [[point.x, (supportState.height || 0) + 22.0, point.z]];
    return { status: 'ready', reason: '', waypoints };
  }

  // 地面单位：水域 / 陡坡 / 障碍 OBB 阻挡 + 局部 A* 连通性核验
  const clearance = unit.clearance ?? 5.5;
  if (env?.isPointBlocked && env.isPointBlocked(point.x, point.z, clearance)) {
    return { status: 'rejected', reason: '目的地位于建筑障碍、水域或陡坡内' };
  }

  if (env?.findGroundPath) {
    const distXZ = Math.hypot(point.x - refPos.x, point.z - refPos.z);
    // 超过 560m 跨区远程机动时，若终点本身未被阻挡且局部 640m A* 框无法同时包住起点与终点，
    // 按接近方向分阶段生成目标段并保留局部避障；560m 内严格校验 A* 连通性。
    if (distXZ <= 560) {
      const nav = env.findGroundPath(refPos.x, refPos.z, point.x, point.z, clearance);
      if (nav?.status === 'pending') {
        return { status: 'pending', reason: '准备路径 · 寻路区域准备中', waypoints: [] };
      }
      if (!nav?.found) {
        return { status: 'rejected', reason: nav?.reason || '没有连通到目的地的地面路线' };
      }
      return { status: 'ready', reason: '', waypoints: nav.waypoints || [] };
    }
    const gy = Number.isFinite(supportState.height) ? supportState.height : 0;
    return {
      status: 'ready',
      reason: '',
      waypoints: [[refPos.x, Number.isFinite(refPos.y) ? refPos.y : gy, refPos.z], [point.x, gy + 0.25, point.z]],
    };
  }

  const gy = Number.isFinite(supportState.height) ? supportState.height : 0;
  return { status: 'ready', reason: '', waypoints: [[point.x, gy + 0.25, point.z]] };
}

/**
 * 纯只读指挥计划器（CQS Query）：
 * 保留多车相对队形、等比放大过密间距（地面 12m / 旋翼 20m）、拖朝向展开一字横队，
 * 空地混选时自动分道偏移（地面 -10m / 旋翼 +10m），固定翼不展开横队而在目标附近盘旋。
 */
export function planFleetCommand({ units, ids, command, append = false, env = {} }) {
  const idSet = new Set(ids || []);
  const receiving = (units || []).filter(u => idSet.has(u.id));
  const groups = { ground: [], helicopter: [], fixed_wing: [] };
  for (const u of receiving) {
    const key = u.role === 'helicopter' ? 'helicopter' : u.role === 'fixed_wing' ? 'fixed_wing' : 'ground';
    groups[key].push(u);
  }

  const offsets = new Map();
  const hasFacing = Number.isFinite(command?.facing);
  const facing = hasFacing ? command.facing : 0;

  for (const [roleKey, group] of Object.entries(groups)) {
    if (!group.length) continue;
    if (roleKey === 'fixed_wing') {
      // 多架固定翼共用空域目标点但微错开盘旋锚点，防止完全重合
      group.forEach((u, idx) => {
        const shift = (idx - (group.length - 1) * 0.5) * 28;
        offsets.set(u.id, { x: shift, z: 0 });
      });
      continue;
    }

    const spacing = roleKey === 'ground' ? 12 : 20;
    const refs = group.map(u => getFormationReference(u, append));
    const cx = refs.reduce((acc, p) => acc + p.x, 0) / group.length;
    const cz = refs.reduce((acc, p) => acc + p.z, 0) / group.length;

    let rel = refs.map(p => ({ x: p.x - cx, z: p.z - cz }));
    let minimum = Infinity;
    for (let i = 0; i < rel.length; i += 1) {
      for (let j = 0; j < i; j += 1) {
        minimum = Math.min(minimum, Math.hypot(rel[i].x - rel[j].x, rel[i].z - rel[j].z));
      }
    }
    if (minimum < 0.01) {
      rel = group.map((_, i) => ({ x: (i - (group.length - 1) * 0.5) * spacing, z: 0 }));
      minimum = spacing;
    }
    const scale = minimum > 0 ? Math.max(1, spacing / minimum) : 1;

    group.forEach((u, idx) => {
      if (hasFacing) {
        // 横队沿朝向垂直右轴 (cos(facing), -sin(facing)) 展开
        const lateral = (idx - (group.length - 1) * 0.5) * spacing;
        offsets.set(u.id, {
          x: lateral * Math.cos(facing),
          z: -lateral * Math.sin(facing),
        });
      } else {
        offsets.set(u.id, {
          x: rel[idx].x * scale,
          z: rel[idx].z * scale,
        });
      }
    });
  }

  const mixedGroundAndHeli = groups.ground.length > 0 && groups.helicopter.length > 0;

  return receiving.map(unit => {
    const job = {
      type: command?.type || 'move',
      point: command?.point ? { ...command.point } : null,
      facing: hasFacing ? facing : undefined,
      started: false,
    };

    const isMovement = job.type === 'move' || job.type === 'attackMove' || job.type === 'reverse';
    const unsupported =
      (job.type === 'reverse' && unit.role !== 'ground') ||
      (job.type === 'attackGround' && unit.role === 'fixed_wing');

    if (unsupported) {
      return {
        unit,
        job,
        valid: false,
        status: 'rejected',
        reason: '当前载具类型不支持该指令',
        waypoints: [],
      };
    }

    if (job.type === 'attackGround') {
      const bounds = env?.bounds || DEFAULT_MAP_BOUNDS;
      const pt = job.point;
      let status = 'accepted';
      let valid = true;
      let reason = '待 G04 战斗引擎接入实弹结算';
      if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.z)) {
        status = 'rejected';
        valid = false;
        reason = '攻地坐标无效';
      } else if (pt.x < bounds.minX || pt.x > bounds.maxX || pt.z < bounds.minZ || pt.z > bounds.maxZ) {
        status = 'rejected';
        valid = false;
        reason = '攻地坐标超出 9km 战场边界';
      } else if (env?.querySupport) {
        const sup = env.querySupport(pt.x, pt.z);
        if (sup?.state === 'pending') {
          status = 'pending';
          reason = '准备路径 · 目标地形尚在加载';
        } else if (!sup || sup.state !== 'ready') {
          status = 'rejected';
          valid = false;
          reason = '攻地坐标缺少有效地形基准';
        }
      }
      return {
        unit,
        job,
        valid,
        status,
        reason,
        waypoints: [],
      };
    }

    if (isMovement && job.point) {
      const off = offsets.get(unit.id) || { x: 0, z: 0 };
      const laneOffset = mixedGroundAndHeli
        ? (unit.role === 'ground' ? -10 : unit.role === 'helicopter' ? 10 : 0)
        : 0;
      // 车头朝向向量为 (sin(facing), cos(facing))，地面在前/后车道纵向分流
      const laneX = laneOffset * Math.sin(facing);
      const laneZ = laneOffset * Math.cos(facing);
      job.point = {
        ...job.point,
        x: job.point.x + off.x + laneX,
        z: job.point.z + off.z + laneZ,
      };
    }

    const refPos = getFormationReference(unit, append);
    const check = isMovement
      ? evaluateDestination({ unit, point: job.point, refPos, env })
      : { status: 'ready', reason: '', waypoints: [] };

    return {
      unit,
      job,
      valid: check.status !== 'rejected',
      status: check.status,
      reason: check.reason,
      waypoints: check.waypoints || [],
    };
  });
}

/**
 * 纯调度器：应用 planFleetCommand 结果，写回单位队列/状态与结构化回执。
 * 不可达单位保留原有 job 与 queue，绝不擅自换点或覆盖旧任务。
 */
export function dispatchFleetCommand({
  units,
  ids,
  command,
  append = false,
  env = {},
  runId = 'run-1',
  serial = 1,
  applyWaypoints = null,
  plannedRows = null,
}) {
  const planned = Array.isArray(plannedRows) && plannedRows.length > 0
    ? plannedRows
    : planFleetCommand({ units, ids, command, append, env });
  const batchId = `cmd-${serial}`;
  const result = {
    runId,
    commandId: serial,
    batchId,
    type: command?.type || 'move',
    append: Boolean(append),
    accepted: 0,
    rejected: 0,
    pending: 0,
    rows: [],
  };

  for (const row of planned) {
    const u = row.unit;
    const cmdId = `${batchId}:${u.id}`;
    row.job.id = cmdId;
    row.job.batchId = batchId;

    const feedback = {
      unitId: u.id,
      commandId: cmdId,
      status: row.status,
      reason: row.reason,
      point: row.job.point ? { ...row.job.point } : null,
      facing: row.job.facing,
      valid: row.valid,
    };
    result.rows.push(feedback);

    if (!row.valid) {
      result.rejected += 1;
      u.lastRejectedCommand = { ...feedback, atMs: Date.now() };
      if (!u.job) {
        u.commandStatus = { status: 'rejected', reason: row.reason, commandId: cmdId };
      }
      continue;
    }

    result.accepted += 1;
    if (row.status === 'pending') result.pending += 1;
    u.lastRejectedCommand = null;

    if (row.job.type === 'attackGround') {
      // 强制攻地占位：支持 Shift 多点攻地队列（append=true 追加，append=false 覆写），不覆写已有移动任务
      const intentEntry = { ...feedback, atMs: Date.now() };
      if (append) {
        u.attackGroundQueue = Array.isArray(u.attackGroundQueue) ? u.attackGroundQueue : [];
        u.attackGroundQueue.push(intentEntry);
      } else {
        u.attackGroundQueue = [intentEntry];
      }
      u.lastAttackGroundIntent = intentEntry;
      const qCnt = u.attackGroundQueue.length;
      u.commandStatus = {
        status: 'intent',
        reason: qCnt > 1 ? `已登记 ${qCnt} 个攻地队列点（待 G04 结算）` : '已登记攻地意图（待 G04 结算）',
        commandId: cmdId,
      };
      continue;
    }

    row.job.waypoints = row.waypoints;
    if (append && u.job) {
      u.queue = Array.isArray(u.queue) ? u.queue : [];
      u.queue.push(row.job);
    } else {
      u.queue = [];
      u.job = row.job;
      u.targetFacing = Number.isFinite(row.job.facing) ? row.job.facing : null;
      u.commandStatus = {
        status: row.status === 'pending' ? 'pending' : 'moving',
        reason: row.reason,
        commandId: cmdId,
      };
      if (applyWaypoints) {
        applyWaypoints(u, row.waypoints, row.job);
      } else {
        u.waypoints = row.waypoints;
        u.wpIndex = 0;
      }
      if (u.role === 'fixed_wing' && row.job.point) {
        u.orbitCenter = { x: row.job.point.x, z: row.job.point.z };
      }
    }
  }

  return result;
}

/**
 * 停止选中单位：地面原地停车、旋翼悬停、固定翼在当前位置空域盘旋，并清空待执行队列。
 */
export function cancelFleetUnits({ units, ids }) {
  const idSet = new Set(ids || []);
  const stoppedIds = [];
  for (const u of units || []) {
    if (!idSet.has(u.id)) continue;
    u.job = null;
    u.queue = [];
    u.waypoints = [];
    u.wpIndex = 0;
    u.targetFacing = null;
    u.lastRejectedCommand = null;
    u.attackGroundQueue = [];
    u.lastAttackGroundIntent = null;
    if (u.role === 'fixed_wing') {
      const pos = getUnitWorldPos(u);
      u.orbitCenter = { x: pos.x, z: pos.z };
      u.commandStatus = { status: 'stopped', reason: '已清空航路队列 · 保持高空盘旋', commandId: null };
    } else if (u.role === 'helicopter') {
      u.commandStatus = { status: 'stopped', reason: '已清空航路队列 · 原地悬停', commandId: null };
    } else {
      u.commandStatus = { status: 'stopped', reason: '已清空航路队列 · 原地待命', commandId: null };
    }
    stoppedIds.push(u.id);
  }
  return stoppedIds;
}

/**
 * 构建支持 5 级 LIFO 撤退优先级的交互模式仲裁器（可接入共享 escStack）。
 * 优先级（从顶到底）：
 * 1. 进行中右键预览 / 左键框选拖曳
 * 2. targeting 攻地模式 / editor 激活笔刷
 * 3. 已展开 8 槽战术轮盘 / 定制战术下拉 (.ui-select)
 * 4. 右侧演训与地图编辑抽屉 / 左侧名册抽屉
 * 5. 4A 二分暂停菜单
 */
export function createModeAndEscArbiter({
  escStack,
  hasActiveDrag = () => false,
  cancelActiveDrag = () => {},
  onModeChange = null,
  onCursorChange = null,
} = {}) {
  let mode = INTERACTION_MODES.PLAY;
  let activeBrush = null; // null | 'add_building' | 'raise_terrain'
  let deploySlug = null;
  let cursor = 'default';

  const setCursor = (next) => {
    const normalized = CURSOR_CLASSES[next] ? next : 'default';
    if (cursor !== normalized) {
      cursor = normalized;
      onCursorChange?.(cursor, CURSOR_CLASSES[cursor]);
    }
  };

  const setMode = (nextMode, brushOrSlug = null) => {
    const targetMode = Object.values(INTERACTION_MODES).includes(nextMode) ? nextMode : INTERACTION_MODES.PLAY;
    const nextBrush = targetMode === INTERACTION_MODES.EDITOR ? (brushOrSlug || activeBrush || 'add_building') : null;
    const nextDeploySlug = targetMode === INTERACTION_MODES.DEPLOY ? (brushOrSlug || deploySlug || 'US_M1A2') : null;
    if (mode === targetMode && activeBrush === nextBrush && deploySlug === nextDeploySlug) return;
    mode = targetMode;
    activeBrush = nextBrush;
    deploySlug = nextDeploySlug;
    if (mode === INTERACTION_MODES.TARGETING) setCursor('attack');
    else if (mode === INTERACTION_MODES.EDITOR || mode === INTERACTION_MODES.DEPLOY) setCursor('deploy');
    else setCursor('default');
    onModeChange?.({ mode, activeBrush, deploySlug });
  };

  // 若提供外部 escStack，按 3 -> 2 -> 1 由低到高追加注册（保证与既有的 5 -> 4 -> ui-select 严格衔接）
  if (escStack) {
    escStack.register(
      'interaction-wheel',
      () => mode === INTERACTION_MODES.WHEEL,
      () => setMode(INTERACTION_MODES.PLAY),
    );
    escStack.register(
      'interaction-submode',
      () => (
        mode === INTERACTION_MODES.TARGETING ||
        mode === INTERACTION_MODES.EDITOR ||
        mode === INTERACTION_MODES.DEPLOY
      ),
      () => setMode(INTERACTION_MODES.PLAY),
    );
    escStack.register(
      'interaction-transient-drag',
      () => Boolean(hasActiveDrag()),
      () => cancelActiveDrag(),
    );
  }

  return Object.freeze({
    getMode: () => mode,
    getActiveBrush: () => activeBrush,
    getDeploySlug: () => deploySlug,
    getCursor: () => cursor,
    setMode,
    setCursor,
    exitToPlay: () => setMode(INTERACTION_MODES.PLAY),
    enterEditorBrush: (brush = 'add_building') => setMode(INTERACTION_MODES.EDITOR, brush),
    enterDeployMode: (slug = 'US_M1A2') => setMode(INTERACTION_MODES.DEPLOY, slug),
    enterTargeting: () => setMode(INTERACTION_MODES.TARGETING),
    toggleWheel: () => setMode(mode === INTERACTION_MODES.WHEEL ? INTERACTION_MODES.PLAY : INTERACTION_MODES.WHEEL),
  });
}
