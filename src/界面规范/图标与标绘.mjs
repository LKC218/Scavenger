/** Scavenger 全局唯一矢量图标源（品牌标识生化肃清三楔盾 + 12 类武器库兵种剪影 + 4 大通用武器剪影 + 4 枚情景警示 + 26 枚 HUD 图标）、语义调色读取器、线框幽灵标绘与车底防叠字引擎。 */

const SVG_NS = 'http://www.w3.org/2000/svg';
let hazardMaskUid = 0;

/** 品牌标识唯一元数据注册表（Option B · 生化肃清三楔盾，Code SSOT V2.2）。 */
export const BRAND_IDENTITY = Object.freeze({
  id: 'bio-purge-triad-shield',
  name: 'SCAVENGER',
  cnName: '生化清道夫',
  tagline: 'BIO-PURGE // 3-ZONE OPS',
  cnTagline: '生化肃清 · 三域合围指挥体系',
  viewBox: '0 0 64 64',
  faviconViewBox: '0 0 24 24',
  variants: Object.freeze(['mark', 'sigil', 'favicon', 'stencil', 'lockup']),
  tones: Object.freeze(['a3', 'mono', 'tridomain', 'toxic']),
});

/** 品牌标识分层纯几何路径源（零 transform 依赖，同时兼容 DOM SVG evenodd 与 Canvas Path2D 离屏绘制）。 */
export const BRAND_LOGO_LAYERS = Object.freeze({
  shieldOuter: 'M10.5 13.6 L32 7.0 L53.5 13.6 V36.6 C53.5 44.0 44.5 51.2 32 57.0 C19.5 51.2 10.5 44.0 10.5 36.6 Z',
  shieldRing: 'M10.5 13.6 L32 7.0 L53.5 13.6 V36.6 C53.5 44.0 44.5 51.2 32 57.0 C19.5 51.2 10.5 44.0 10.5 36.6 Z M15.0 16.9 L32 11.7 L49.0 16.9 V35.8 C49.0 41.4 41.5 46.8 32 51.6 C22.5 46.8 15.0 41.4 15.0 35.8 Z',
  shieldStencil: 'M10.5 25.5 V13.6 L28.5 8.1 V12.8 L15.0 16.9 V25.5 Z M53.5 25.5 V13.6 L35.5 8.1 V12.8 L49.0 16.9 V25.5 Z M10.5 30.5 V36.6 C10.5 43.8 18.6 50.2 28.5 55.3 V50.0 C21.2 46.0 15.0 41.1 15.0 35.8 V30.5 Z M53.5 30.5 V36.6 C53.5 43.8 45.4 50.2 35.5 55.3 V50.0 C42.8 46.0 49.0 41.1 49.0 35.8 V30.5 Z',
  wedges: Object.freeze([
    'M24.64 18.58 L32 15.82 L39.36 18.58 L35.50 25.94 L32 24.38 L28.50 25.94 Z',
    'M46.44 30.84 L45.15 38.59 L39.08 43.58 L34.63 36.56 L37.73 34.31 L38.13 30.50 Z',
    'M17.56 30.84 L25.87 30.50 L26.27 34.31 L29.37 36.56 L24.92 43.58 L18.85 38.59 Z',
  ]),
  core: 'M32 27.1 L35.9 31 L32 34.9 L28.1 31 Z',
  faviconShield: 'M4.5 5.2 L12 3 L19.5 5.2 V13.2 C19.5 16.2 16.2 18.8 12 20.8 C7.8 18.8 4.5 16.2 4.5 13.2 Z',
  faviconWedges: 'M9.1 7.6 L12 6.5 L14.9 7.6 L13.4 10.6 L12 10.0 L10.6 10.6 Z M17.5 12.1 L17.0 15.1 L14.6 17.0 L13.0 14.3 L14.2 13.4 L14.3 12.0 Z M6.5 12.1 L9.7 12.0 L9.8 13.4 L11.0 14.3 L9.4 17.0 L7.0 15.1 Z',
  faviconCore: 'M12 10.6 L13.5 12.1 L12 13.6 L10.5 12.1 Z',
});

const PATH_MBT = 'M2.2 14.4 L4.2 11.8 L8.0 11.8 L9.4 8.6 L14.8 8.6 L16.0 10.1 L18.2 10.1 L18.2 9.6 L20.0 9.6 L20.0 10.1 L23.0 10.1 L23.0 11.4 L15.8 11.4 L15.4 12.1 L19.8 12.1 L21.6 14.3 L19.8 17.5 L3.8 17.5 Z M5.6 14.4h2.1v1.7H5.6Z M9.2 14.4h2.1v1.7H9.2Z M12.8 14.4h2.1v1.7H12.8Z M16.4 14.4h2.1v1.7H16.4Z';
const PATH_ATTACK_HELI = 'M3.0 6.0 H21.6 V7.4 H13.6 V9.4 H12.0 V7.4 H3.0 Z M2.0 10.6 L3.6 9.4 L4.4 11.8 L9.8 11.8 L11.4 9.4 L15.6 9.4 L18.2 11.2 L21.8 12.6 L22.2 13.8 L18.4 14.8 L15.0 15.4 L14.0 16.6 H9.6 L8.8 14.6 L4.2 13.6 L3.0 15.0 L2.0 14.0 Z M12.2 10.4 H15.2 L16.8 11.8 H12.2 Z M9.6 17.4 H15.8 V18.4 H9.6 Z';
const PATH_CAS = 'M12.0 1.8 L13.4 5.2 L13.6 9.4 L22.2 10.0 V13.0 L13.6 12.8 L13.4 15.4 L14.6 15.8 V17.8 H13.2 L13.0 19.0 L17.0 19.6 V21.8 L12.6 21.0 L12.0 22.2 L11.4 21.0 L7.0 21.8 V19.6 L11.0 19.0 L10.8 17.8 H9.4 V15.8 L10.6 15.4 L10.4 12.8 L1.8 13.0 V10.0 L10.4 9.4 L10.6 5.2 Z M11.4 5.2 H12.6 V8.4 H11.4 Z';

/** 12 类武器库兵种实心负空间军事剪影（+ tank/heli/plane 兼容别名，固定 24x24，强制 fill-rule="evenodd"）。 */
export const VEHICLE_SILHOUETTES = Object.freeze({
  mbt: PATH_MBT,
  ifv: 'M2.4 15.6 L3.2 10.2 L7.6 10.2 L8.8 7.5 L14.2 7.5 L15.0 8.8 L21.2 8.2 L21.4 9.3 L15.4 10.0 L18.8 10.5 L21.6 13.4 L21.0 16.0 L19.2 17.6 L4.2 17.6 Z M10.2 8.4h2.4v1.1h-2.4Z M5.6 14.8a1.4 1.4 0 1 0 0.01 0Z M10.0 14.8a1.4 1.4 0 1 0 0.01 0Z M14.4 14.8a1.4 1.4 0 1 0 0.01 0Z M18.4 14.8a1.4 1.4 0 1 0 0.01 0Z',
  recon: 'M2.6 15.2 L3.4 10.6 L6.8 10.6 L8.4 7.6 L14.2 7.6 L16.2 10.6 L21.4 11.4 L21.6 15.2 L19.8 15.6 A2.3 2.3 0 0 1 15.2 15.6 L9.0 15.6 A2.3 2.3 0 0 1 4.4 15.6 Z M11.2 5.4 L17.6 5.4 L17.6 6.5 L12.4 6.5 L12.4 7.6 L11.2 7.6 Z M9.2 8.7 L13.4 8.7 L14.8 10.6 L8.2 10.6 Z M6.7 13.8a1.7 1.7 0 1 0 0.01 3.4a1.7 1.7 0 1 0-0.01-3.4Z M17.5 13.8a1.7 1.7 0 1 0 0.01 3.4a1.7 1.7 0 1 0-0.01-3.4Z',
  arty: 'M2.2 15.2 L3.4 12.2 L6.4 12.2 L6.8 9.2 L12.2 9.2 L19.8 3.6 L20.9 5.0 L13.8 10.4 L14.4 12.2 L19.6 12.2 L21.2 14.6 L19.4 17.8 L3.6 17.8 Z M1.8 17.8 L4.0 15.6 L5.0 16.5 L3.2 18.6 Z M6.0 14.8h2.2v1.6H6.0Z M10.6 14.8h2.2v1.6H10.6Z M15.2 14.8h2.2v1.6H15.2Z',
  aa: 'M2.6 15.2 L3.4 12.2 L18.8 12.2 L21.4 14.2 L20.0 17.6 L4.0 17.6 Z M5.2 11.4 L11.6 5.0 L13.6 7.0 L7.8 12.2 Z M14.8 12.2 L14.8 8.8 L16.2 8.8 L16.2 12.2 Z M13.0 5.4 A4.2 4.2 0 0 1 19.6 6.2 L18.4 7.3 A2.6 2.6 0 0 0 14.2 6.8 Z M6.2 14.8a1.4 1.4 0 1 0 0.01 0Z M11.6 14.8a1.4 1.4 0 1 0 0.01 0Z M17.0 14.8a1.4 1.4 0 1 0 0.01 0Z',
  atgm: 'M2.6 15.0 L3.8 11.6 L19.0 11.6 L21.4 14.2 L19.8 17.6 L4.0 17.6 Z M9.6 11.6 L9.6 8.4 L7.6 8.4 L7.6 5.2 L17.6 5.2 L17.6 8.4 L11.4 8.4 L11.4 11.6 Z M18.4 5.8 L21.6 6.8 L18.4 7.8 Z M6.2 14.8a1.4 1.4 0 1 0 0.01 0Z M11.6 14.8a1.4 1.4 0 1 0 0.01 0Z M17.0 14.8a1.4 1.4 0 1 0 0.01 0Z',
  supply: 'M2.4 6.6 L15.2 6.6 L15.2 11.4 L16.2 11.4 L16.2 8.2 L19.4 8.2 L21.6 11.6 L21.6 15.8 L19.6 17.8 L3.6 17.8 L2.4 15.6 Z M17.2 9.4 h1.8 l1.2 2.2 h-3.0 Z M7.8 8.3 H9.8 V10.1 H11.6 V12.1 H9.8 V13.9 H7.8 V12.1 H6.0 V10.1 H7.8 Z M5.4 15.2 a1.4 1.4 0 1 0 0.01 0 Z M11.4 15.2 a1.4 1.4 0 1 0 0.01 0 Z M17.4 15.2 a1.4 1.4 0 1 0 0.01 0 Z',
  attack_heli: PATH_ATTACK_HELI,
  trans_heli: 'M1.8 6.4 H10.8 V7.6 H7.0 V9.8 H5.6 V7.6 H1.8 Z M13.2 6.4 H22.2 V7.6 H18.4 V9.8 H17.0 V7.6 H13.2 Z M3.2 11.2 L5.4 9.8 H18.6 L21.4 11.8 L21.6 14.8 L18.8 16.4 H5.2 L2.6 14.6 Z M16.4 11.2 H19.2 L20.2 12.6 H16.4 Z M7.0 12.0 H9.6 V13.6 H7.0 Z M11.0 12.0 H13.6 V13.6 H11.0 Z',
  cas: PATH_CAS,
  fighter: 'M12.0 1.6 L13.8 7.4 L21.8 14.2 L21.8 16.4 L14.2 14.6 L14.0 17.6 L17.4 20.0 L17.4 21.6 L12.8 20.4 L12.0 22.2 L11.2 20.4 L6.6 21.6 L6.6 20.0 L10.0 17.6 L9.8 14.6 L2.2 16.4 L2.2 14.2 L10.2 7.4 Z M11.4 6.0 H12.6 V9.4 H11.4 Z',
  uav: 'M12.0 2.0 C13.2 2.0 13.8 3.2 13.5 5.4 L13.1 10.6 L23.0 11.4 V12.8 L13.0 12.6 L12.8 18.0 L16.2 20.8 L15.4 22.0 L12.0 19.8 L8.6 22.0 L7.8 20.8 L11.2 18.0 L11.0 12.6 L1.0 12.8 V11.4 L10.9 10.6 L10.5 5.4 C10.2 3.2 10.8 2.0 12.0 2.0 Z',
  tank: PATH_MBT,
  heli: PATH_ATTACK_HELI,
  plane: PATH_CAS,
});

export const VEHICLE_PRIMARY_CLASSES = Object.freeze([
  'mbt', 'ifv', 'recon', 'arty', 'aa', 'atgm', 'supply',
  'attack_heli', 'trans_heli', 'cas', 'fighter', 'uav',
]);

/** 兵种元数据与标准贴地底盘环半径（米）。 */
export const VEHICLE_META = Object.freeze({
  mbt: Object.freeze({ label: '主战坦克', models: 'M1A2 / M1A1 / M10 / M60', layer: 'ground', channel: 'ground', ringRadius: 4.8, routeOffset: 5.1 }),
  ifv: Object.freeze({ label: '步战 / 装甲车', models: 'M2A3 / Stryker / LAV-25 / AAVP', layer: 'ground', channel: 'ground', ringRadius: 4.4, routeOffset: 4.7 }),
  recon: Object.freeze({ label: '侦察 / 轻型突击', models: 'Humvee / M-ATV / ISV / M3 CFV', layer: 'ground', channel: 'ground', ringRadius: 3.8, routeOffset: 4.1 }),
  arty: Object.freeze({ label: '自行火炮 / 火箭', models: 'M109A7 / HIMARS / M270 MLRS', layer: 'ground', channel: 'ground', ringRadius: 4.8, routeOffset: 5.1 }),
  aa: Object.freeze({ label: '野战防空 / 导弹', models: 'Patriot / Avenger / SHORAD / C-RAM', layer: 'ground', channel: 'ground', ringRadius: 4.6, routeOffset: 4.9 }),
  atgm: Object.freeze({ label: '反坦克导弹车', models: 'TOW-2 / LAV-AT / M901 / Stryker AT', layer: 'ground', channel: 'ground', ringRadius: 4.2, routeOffset: 4.5 }),
  supply: Object.freeze({ label: '后勤补给 / 工程', models: 'M548A3 / HEMTT / LMTV / PLS', layer: 'ground', channel: 'ground', ringRadius: 4.4, routeOffset: 4.7 }),
  attack_heli: Object.freeze({ label: '武装直升机', models: 'AH-64D/E / AH-1Z / RAH-66 / OH-58', layer: 'air', channel: 'heli', ringRadius: 5.2, routeOffset: 5.5 }),
  trans_heli: Object.freeze({ label: '运输 / 倾转机', models: 'CH-47 / UH-60M / CH-53K / V-22', layer: 'air', channel: 'heli', ringRadius: 5.6, routeOffset: 5.9 }),
  cas: Object.freeze({ label: '对地攻击 / 炮艇', models: 'A-10C / AC-130 / AV-8B Harrier', layer: 'air', channel: 'plane', ringRadius: 5.6, routeOffset: 6.2 }),
  fighter: Object.freeze({ label: '制空战斗 / 轰炸', models: 'F-22 / F-35 / F-15EX / F-16 / B-2', layer: 'air', channel: 'plane', ringRadius: 5.6, routeOffset: 6.2 }),
  uav: Object.freeze({ label: '侦察 / 察打无人机', models: 'MQ-9 Reaper / MQ-1C / RQ-4 / RQ-7', layer: 'air', channel: 'plane', ringRadius: 4.8, routeOffset: 5.4 }),
  tank: Object.freeze({ label: '主战坦克', models: 'M1A2 / M1A1 / M10 / M60', layer: 'ground', channel: 'ground', ringRadius: 4.8, routeOffset: 5.1 }),
  heli: Object.freeze({ label: '武装直升机', models: 'AH-64D/E / AH-1Z / RAH-66 / OH-58', layer: 'air', channel: 'heli', ringRadius: 5.2, routeOffset: 5.5 }),
  plane: Object.freeze({ label: '固定翼攻击机', models: 'A-10C / AC-130 / AV-8B Harrier', layer: 'air', channel: 'plane', ringRadius: 5.6, routeOffset: 6.2 }),
});

/** 4 枚现实原型情景警示图标（ISO 7010 精确几何与高直觉防化/尸群轮廓）。 */
export const HAZARD_ICONS = Object.freeze({
  biohazard: Object.freeze({ label: '生化危情', note: 'ISO 7010 标准三环尖爪', tone: 'warn', custom: 'biohazard' }),
  radiation: Object.freeze({
    label: '核辐射警示', note: 'ISO 7010 标准三叶草', tone: 'select',
    d: 'M12 9.7 A2.3 2.3 0 1 0 12 14.3 A2.3 2.3 0 1 0 12 9.7 Z M1.60 12.00 A10.4 10.4 0 0 1 6.80 2.99 L9.95 8.45 A4.1 4.1 0 0 0 7.90 12.00 Z M17.20 2.99 A10.4 10.4 0 0 1 22.40 12.00 L16.10 12.00 A4.1 4.1 0 0 0 14.05 8.45 Z M17.20 21.01 A10.4 10.4 0 0 1 6.80 21.01 L9.95 15.55 A4.1 4.1 0 0 0 14.05 15.55 Z',
  }),
  gasmask: Object.freeze({
    label: '毒雾防化', note: '军用防毒面具轮廓', tone: 'toxic',
    d: 'M12 2.4 C7.4 2.4 4.4 5.6 4.4 10.2 L4.8 14.2 L2.6 15.4 L3.4 18.6 L6.8 18.2 L8.8 20.6 H15.2 L17.2 18.2 L20.6 18.6 L21.4 15.4 L19.2 14.2 L19.6 10.2 C19.6 5.6 16.6 2.4 12 2.4 Z M8.5 8.6 A2.3 2.0 0 1 0 8.51 12.6 A2.3 2.0 0 1 0 8.5 8.6 Z M15.5 8.6 A2.3 2.0 0 1 0 15.51 12.6 A2.3 2.0 0 1 0 15.5 8.6 Z M12 14.2 A2.3 2.3 0 1 0 12 18.8 A2.3 2.3 0 1 0 12 14.2 Z',
  }),
  horde: Object.freeze({
    label: '尸群高危区', note: '沦陷区 / 致命威胁警示', tone: 'danger',
    d: 'M12 2.4 C6.9 2.4 3.6 6.0 3.6 10.6 C3.6 13.4 5.1 15.6 7.0 16.8 V20.4 C7.0 21.0 7.5 21.4 8.1 21.4 H15.9 C16.5 21.4 17.0 21.0 17.0 20.4 V16.8 C18.9 15.6 20.4 13.4 20.4 10.6 C20.4 6.0 17.1 2.4 12 2.4 Z M8.6 9.4 A2.0 2.0 0 1 0 8.61 13.4 A2.0 2.0 0 1 0 8.6 9.4 Z M15.4 9.4 A2.0 2.0 0 1 0 15.41 13.4 A2.0 2.0 0 1 0 15.4 9.4 Z M12 13.2 L13.4 16.0 H10.6 Z M9.6 18.0 H10.8 V20.4 H9.6 Z M13.2 18.0 H14.4 V20.4 H13.2 Z',
  }),
});

/** 固定 4 大类通用武器矢量剪影源（viewBox="0 0 24 24"，fill-rule="evenodd" 实心负空间军工剪影，覆盖全游戏地面/旋翼/固定翼武器）。 */
export const WEAPON_ICON_KINDS = Object.freeze(['cannon', 'rocket', 'missile', 'bomb']);

export const WEAPON_ICONS = Object.freeze({
  cannon: Object.freeze({
    label: '机炮 / 火炮',
    scope: 'GAU-8/A · 30mm 链炮 · 步战机炮 · 坦克主炮',
    d: 'M12 2.1 C13.5 4.2 14.1 6.3 14.1 8.4 H9.9 C9.9 6.3 10.5 4.2 12 2.1 Z M9.6 9.5 H14.4 V18.7 H9.6 Z M9.0 19.6 H15.0 V21.4 H9.0 Z M5.2 4.8 C6.5 6.6 7.0 8.4 7.0 10.2 H3.4 C3.4 8.4 3.9 6.6 5.2 4.8 Z M3.2 11.2 H7.2 V18.9 H3.2 Z M2.7 19.7 H7.7 V21.4 H2.7 Z M18.8 4.8 C20.1 6.6 20.6 8.4 20.6 10.2 H17.0 C17.0 8.4 17.5 6.6 18.8 4.8 Z M16.8 11.2 H20.8 V18.9 H16.8 Z M16.3 19.7 H21.3 V21.4 H16.3 Z',
  }),
  rocket: Object.freeze({
    label: '火箭弹 / 火箭巢',
    scope: 'Hydra 70 · Zuni · M270 / HIMARS 齐射',
    d: 'M15.41 3.79 L14.49 6.83 L13.57 7.75 L11.45 5.63 L12.37 4.71 Z M10.81 6.27 L12.93 8.39 L9.26 12.06 L9.15 14.08 L8.44 14.79 L7.91 12.98 L6.22 11.29 L4.41 10.76 L5.12 10.05 L7.14 9.94 Z M5.76 12.24 L6.96 13.44 L3.39 15.81 Z M20.41 8.79 L19.49 11.83 L18.57 12.75 L16.45 10.63 L17.37 9.71 Z M15.81 11.27 L17.93 13.39 L14.26 17.06 L14.15 19.08 L13.44 19.79 L12.91 17.98 L11.22 16.29 L9.41 15.76 L10.12 15.05 L12.14 14.94 Z M10.76 17.24 L11.96 18.44 L8.39 20.81 Z',
  }),
  missile: Object.freeze({
    label: '精确制导导弹',
    scope: 'AGM-65 · 地狱火 · TOW · AIM 空空 / 防空',
    d: 'M20.63 3.37 L20.38 5.25 L19.53 6.8 L17.2 4.47 L18.75 3.62 Z M16.49 5.03 L18.97 7.51 L16.49 9.98 L15.54 16.1 L14.19 17.44 L11.61 14.86 L9.35 17.13 L9.74 20.63 L8.54 21.83 L7.01 19.04 L5.81 19.67 L4.33 18.19 L4.96 16.99 L2.17 15.46 L3.37 14.26 L6.87 14.65 L9.14 12.39 L6.56 9.81 L7.9 8.46 L14.02 7.51 Z M15.73 7.53 L16.47 8.27 L8.27 16.47 L7.53 15.73 Z',
  }),
  bomb: Object.freeze({
    label: '航弹 / 制导炸弹',
    scope: 'GBU-31 JDAM · Mk-82 · SDB · 集束航弹',
    d: 'M20.49 20.49 L18.72 19.99 L18.22 19.5 L19.5 18.22 L19.99 18.72 Z M19.74 17.83 L19.88 16.28 L19.18 14.44 L17.2 12.18 L14.69 10.16 L9.84 7.65 L7.65 9.84 L10.16 14.69 L12.18 17.2 L14.44 19.18 L16.28 19.88 L17.83 19.74 Z M16.91 13.59 L13.59 16.91 L12.74 16.07 L16.07 12.74 Z M9.38 7.26 L9.53 3.73 L7.4 1.61 L1.61 7.4 L3.73 9.53 L7.26 9.38 Z M8.68 4.86 L7.26 3.44 L5.85 4.86 L7.69 6.7 Z M4.86 8.68 L6.7 7.69 L4.86 5.85 L3.44 7.26 Z',
  }),
});

/** 将细粒度武器类型统一映射至 4 大固定通用武器图标键。 */
export function resolveWeaponIconKind(rawKind) {
  const k = String(rawKind || '').trim().toLowerCase();
  if (!k) return 'cannon';
  if (k === 'cannon' || k.includes('autocannon') || k.includes('gun') || k.includes('howitzer') || k === 'mg') {
    return 'cannon';
  }
  if (k === 'rocket' || k.includes('rocket') || k.includes('mlrs')) {
    return 'rocket';
  }
  if (k === 'missile' || k.includes('agm') || k.includes('aam') || k.includes('atgm') || k.includes('sam') || k.includes('missile') || k.includes('cruise')) {
    return 'missile';
  }
  if (k === 'bomb' || k.includes('bomb') || k.includes('jdam') || k.includes('cluster') || k.includes('sdb')) {
    return 'bomb';
  }
  return 'cannon';
}

/** 外围平面 HUD 线性战术图标（26 枚，24x24，统一 1.75px 圆角线宽，含专属命令语义）。 */
export const HUD_ICONS = Object.freeze({
  move: ['M5 12h13', 'M13 6l6 6-6 6'],
  attack: ['M12 3v4', 'M12 17v4', 'M3 12h4', 'M17 12h4', 'M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0'],
  stop: ['M8.3 3.5h7.4L20.5 8.3v7.4l-4.8 4.8H8.3l-4.8-4.8V8.3Z', 'M8.5 12h7'],
  rally: ['M6 20V5l11 4-11 4'],
  airstrike: ['M12 3l3 7h-2v6l3 2v2l-4-1-4 1v-2l3-2v-6H9Z'],
  repair: ['M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.3 2.3-2-2Z'],
  shield: ['M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6Z'],
  lock: ['M7 11V8a5 5 0 0 1 10 0v3', 'M6 11h12v9H6Z'],
  alert: ['M12 4l9 16H3Z', 'M12 10v4', 'M12 17h.01'],
  drawer: ['M4 5h16v14H4Z', 'M14 5v14'],
  focus: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5', 'M12 9.5v5', 'M9.5 12h5'],
  soundOn: ['M4 9h4l5-4v14l-5-4H4Z', 'M16.5 8.5a5 5 0 0 1 0 7', 'M19 6a8.5 8.5 0 0 1 0 12'],
  soundOff: ['M4 9h4l5-4v14l-5-4H4Z', 'M16 9l5 6', 'M21 9l-5 6'],
  pause: ['M7 5h3v14H7Z', 'M14 5h3v14h-3Z'],
  play: ['M7 5l12 7-12 7Z'],
  reset: ['M4 12a8 8 0 1 0 2.4-5.7L4 8.5', 'M4 4v4.5h4.5'],
  layers: ['M12 3l9 4.5-9 4.5-9-4.5Z', 'M3 12l9 4.5 9-4.5', 'M3 16.5l9 4.5 9-4.5'],
  compass: ['M12 2l3.5 10L12 22l-3.5-10Z', 'M8.5 12h7'],
  copy: ['M9 9h11v11H9Z', 'M4 15V4h11'],
  flare: [
    'M12 3l3.6 5.2L12 7 8.4 8.2Z',
    'M9.8 10.6c-2.8 1.5-4.8 3.8-5.6 6.8',
    'M14.2 10.6c2.8 1.5 4.8 3.8 5.6 6.8',
    'M12 10.8v6.2',
    'M3.8 19.6h.01',
    'M12 20.2h.01',
    'M20.2 19.6h.01',
    'M7.4 16.8h.01',
    'M16.6 16.8h.01',
  ],
  altitude: [
    'M6.2 4v16',
    'M3.6 6.6L6.2 4l2.6 2.6',
    'M3.6 17.4L6.2 20l2.6-2.6',
    'M11.5 5.5h9',
    'M11.5 18.5h9',
    'M12 12h2.8l1.2-1.8 1.2 1.8H20',
    'M16 12v2.4',
  ],
  evacuate: ['M10 4H5v16h5', 'M9 12h11', 'M15.5 7.5L20 12l-4.5 4.5', 'M13 8.5l2.5 3.5-2.5 3.5'],
  sprint: ['M3.5 9h4.5', 'M2.5 12h5.5', 'M3.5 15h4.5', 'M10.5 6.5L16 12l-5.5 5.5', 'M15.5 6.5L21 12l-5.5 5.5'],
  autofire: [
    'M12 5a7 7 0 0 1 6.8 5.2',
    'M18.8 10.2L20.5 8',
    'M18.8 10.2L16.3 9.4',
    'M12 19a7 7 0 0 1-6.8-5.2',
    'M5.2 13.8L3.5 16',
    'M5.2 13.8L7.7 14.6',
    'M12 9v6',
    'M9 12h6',
  ],
  follow: [
    'M4 8V4h4',
    'M20 8V4h-4',
    'M4 16v4h4',
    'M20 16v4h-4',
    'M12 8.2a3.8 3.8 0 1 0 0 7.6a3.8 3.8 0 1 0 0-7.6',
    'M12 12h.01',
  ],
  wheel: [
    'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18',
    'M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7',
    'M12 3v5.5',
    'M12 15.5V21',
    'M3 12h5.5',
    'M15.5 12H21',
  ],
});

const TOKEN_FALLBACK = Object.freeze({
  bgCanvas: '#0d0e13',
  bgPod: 'rgba(19, 20, 28, 0.92)',
  textPrimary: '#f5f6f9',
  textSecondary: '#b6bac5',
  textMuted: '#838896',
  strokeHalo: 'rgba(9, 10, 15, 0.96)',
  ground: '#38e5c0',
  groundInner: '#9bf5e0',
  groundFill: 'rgba(56, 229, 192, 0.15)',
  heli: '#68c8ff',
  heliInner: '#bae4ff',
  heliFill: 'rgba(104, 200, 255, 0.15)',
  plane: '#b197fc',
  planeInner: '#dcd0ff',
  planeFill: 'rgba(177, 151, 252, 0.15)',
  select: '#f0d372',
  selectInner: '#f6de86',
  selectFill: 'rgba(240, 211, 114, 0.16)',
  warn: '#ffb800',
  warnFill: 'rgba(255, 184, 0, 0.16)',
  danger: '#ff5266',
  dangerFill: 'rgba(255, 82, 102, 0.18)',
  toxic: '#a8f22c',
  toxicFill: 'rgba(168, 242, 44, 0.15)',
  safe: '#3cd69f',
  safeFill: 'rgba(60, 214, 159, 0.15)',
});

/** 生成实心负空间军事剪影 SVG 节点（自动携带 fill-rule="evenodd"）。 */
export function createSilhouetteSvg(kind, { size = null, className = '' } = {}) {
  const pathData = VEHICLE_SILHOUETTES[kind] ?? VEHICLE_SILHOUETTES.mbt;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.dataset.silhouette = kind;
  if (className) svg.setAttribute('class', className);
  if (size) {
    svg.setAttribute('width', String(size));
    svg.setAttribute('height', String(size));
  }
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', pathData);
  path.setAttribute('fill', 'currentColor');
  path.setAttribute('fill-rule', 'evenodd');
  svg.append(path);
  return svg;
}

/** 生成 4 枚现实原型情景警示图标 SVG 节点。 */
export function createHazardIconSvg(kind, { size = 24, className = '' } = {}) {
  const cfg = HAZARD_ICONS[kind] ?? HAZARD_ICONS.biohazard;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.dataset.hazard = kind;
  if (className) svg.setAttribute('class', className);
  if (cfg.custom === 'biohazard') {
    const mid = `sc-bio-mask-${++hazardMaskUid}`;
    svg.innerHTML = `
      <defs>
        <mask id="${mid}">
          <rect width="24" height="24" fill="white"/>
          <circle cx="12" cy="6.6" r="3.6" fill="black"/>
          <circle cx="16.9" cy="15.2" r="3.6" fill="black"/>
          <circle cx="7.1" cy="15.2" r="3.6" fill="black"/>
          <circle cx="12" cy="12.3" r="1.7" fill="black"/>
          <path d="M12 12.3 L12 16.4 M12 12.3 L8.4 10.2 M12 12.3 L15.6 10.2" stroke="black" stroke-width="1.05" stroke-linecap="round"/>
        </mask>
      </defs>
      <g mask="url(#${mid})" fill="currentColor">
        <circle cx="12" cy="8.1" r="5.1"/>
        <circle cx="15.6" cy="14.4" r="5.1"/>
        <circle cx="8.4" cy="14.4" r="5.1"/>
        <circle cx="12" cy="12.3" r="4.0" fill="none" stroke="currentColor" stroke-width="1.3"/>
      </g>`;
    return svg;
  }
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', cfg.d);
  path.setAttribute('fill', 'currentColor');
  path.setAttribute('fill-rule', 'evenodd');
  svg.append(path);
  return svg;
}

/** 生成固定 4 大类通用武器实心负空间剪影 SVG 节点（自动携带 fill-rule="evenodd"）。 */
export function createWeaponIconSvg(kind, { size = 24, className = '' } = {}) {
  const resolved = resolveWeaponIconKind(kind);
  const cfg = WEAPON_ICONS[resolved] ?? WEAPON_ICONS.cannon;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.dataset.weaponIcon = resolved;
  if (className) svg.setAttribute('class', className);
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', cfg.d);
  path.setAttribute('fill', 'currentColor');
  path.setAttribute('fill-rule', 'evenodd');
  svg.append(path);
  return svg;
}

/** 生成外围平面 HUD 线性战术图标 SVG 节点。 */
export function createHudIconSvg(name, { size = 16, className = '' } = {}) {
  const strokes = HUD_ICONS[name] ?? HUD_ICONS.focus;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.75');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.dataset.hudIcon = HUD_ICONS[name] ? name : 'focus';
  if (className) svg.setAttribute('class', className);
  for (const d of strokes) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

const BRAND_PATH_CACHE = new Map();

function getCachedBrandPath2D(key, d) {
  if (typeof Path2D !== 'function') return null;
  let path = BRAND_PATH_CACHE.get(key);
  if (!path) {
    path = new Path2D(d);
    BRAND_PATH_CACHE.set(key, path);
  }
  return path;
}

function resolveBrandToneColors(tone, pal) {
  if (tone === 'tridomain') {
    return {
      shield: pal.textSecondary,
      wedges: [pal.ground, pal.heli, pal.plane],
      core: pal.select,
      plateFill: 'rgba(13, 14, 19, 0.56)',
    };
  }
  if (tone === 'toxic') {
    return {
      shield: pal.textSecondary,
      wedges: [pal.select, pal.select, pal.select],
      core: pal.toxic,
      plateFill: 'rgba(13, 14, 19, 0.56)',
    };
  }
  if (tone === 'mono') {
    return {
      shield: 'currentColor',
      wedges: ['currentColor', 'currentColor', 'currentColor'],
      core: 'currentColor',
      plateFill: 'none',
    };
  }
  return {
    shield: pal.textSecondary,
    wedges: [pal.select, pal.select, pal.select],
    core: pal.warn,
    plateFill: 'rgba(13, 14, 19, 0.56)',
  };
}

/** 生成品牌标识 SVG 节点（支持 mark / sigil / favicon / stencil 四级光学尺码与 a3 / mono / tridomain / toxic 色调）。 */
export function createBrandLogoSvg({
  variant = 'sigil',
  size = 24,
  tone = 'a3',
  animated = false,
  className = '',
} = {}) {
  const safeVariant = ['mark', 'sigil', 'favicon', 'stencil'].includes(variant) ? variant : 'sigil';
  const safeTone = safeVariant === 'sigil' || safeVariant === 'stencil'
    ? 'mono'
    : (['a3', 'mono', 'tridomain', 'toxic'].includes(tone) ? tone : 'a3');
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', safeVariant === 'favicon' ? BRAND_IDENTITY.faviconViewBox : BRAND_IDENTITY.viewBox);
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.dataset.brandLogo = BRAND_IDENTITY.id;
  svg.dataset.brandVariant = safeVariant;
  svg.dataset.brandTone = safeTone;
  const classes = ['ui-brand-svg', `is-variant-${safeVariant}`];
  if (animated) classes.push('is-brand-animated');
  if (className) classes.push(className);
  svg.setAttribute('class', classes.join(' '));

  const pal = readSemanticPalette();
  const colors = resolveBrandToneColors(safeTone, pal);
  const appendPath = (d, attrs = {}) => {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== null && v !== undefined) p.setAttribute(k, String(v));
    }
    svg.append(p);
    return p;
  };

  if (safeVariant === 'favicon') {
    const bg = document.createElementNS(SVG_NS, 'rect');
    bg.setAttribute('width', '24');
    bg.setAttribute('height', '24');
    bg.setAttribute('rx', '4');
    bg.setAttribute('fill', pal.bgCanvas || '#0d0e13');
    svg.append(bg);
    appendPath(BRAND_LOGO_LAYERS.faviconShield, {
      fill: pal.select,
      'fill-opacity': '0.16',
      stroke: safeTone === 'mono' ? 'currentColor' : pal.select,
      'stroke-width': '1.6',
      'stroke-linejoin': 'round',
    });
    appendPath(BRAND_LOGO_LAYERS.faviconWedges, {
      fill: safeTone === 'mono' ? 'currentColor' : pal.select,
      class: 'brand-wedge',
    });
    appendPath(BRAND_LOGO_LAYERS.faviconCore, {
      fill: safeTone === 'toxic' ? pal.toxic : (safeTone === 'mono' ? 'currentColor' : pal.warn),
      class: 'brand-core',
    });
    return svg;
  }

  if (safeVariant === 'mark' && colors.plateFill !== 'none') {
    appendPath(BRAND_LOGO_LAYERS.shieldOuter, {
      fill: colors.plateFill,
      class: 'brand-plate',
    });
  }

  appendPath(
    safeVariant === 'stencil' ? BRAND_LOGO_LAYERS.shieldStencil : BRAND_LOGO_LAYERS.shieldRing,
    {
      fill: colors.shield,
      'fill-rule': 'evenodd',
      class: 'brand-shield',
    },
  );

  BRAND_LOGO_LAYERS.wedges.forEach((d, idx) => {
    appendPath(d, {
      fill: colors.wedges[idx] || colors.wedges[0],
      class: `brand-wedge brand-wedge--${idx}`,
      style: animated ? `--wedge-idx:${idx}` : null,
    });
  });

  appendPath(BRAND_LOGO_LAYERS.core, {
    fill: colors.core,
    class: 'brand-core',
  });

  return svg;
}

/** 生成标准化品牌组合标（横版 / 竖版 Lockup，严禁使用 transform: scale）。 */
export function createBrandLockup({
  layout = 'horizontal',
  tone = 'a3',
  subtitle = BRAND_IDENTITY.tagline,
  markSize = 36,
  animated = false,
  className = '',
} = {}) {
  const isVertical = layout === 'vertical';
  const root = document.createElement('div');
  root.className = `ui-brand-lockup${isVertical ? ' ui-brand-lockup--vertical' : ''}${className ? ` ${className}` : ''}`;
  root.dataset.brandLockup = BRAND_IDENTITY.id;
  root.dataset.brandLayout = isVertical ? 'vertical' : 'horizontal';

  const markWrap = document.createElement('span');
  markWrap.className = 'ui-brand-mark-wrap';
  markWrap.append(createBrandLogoSvg({
    variant: markSize <= 28 ? 'sigil' : 'mark',
    size: markSize,
    tone,
    animated,
  }));

  const textWrap = document.createElement('div');
  textWrap.className = 'ui-brand-copy';
  const title = document.createElement('strong');
  title.className = 'ui-display ui-brand-wordmark';
  title.textContent = BRAND_IDENTITY.name;
  textWrap.append(title);
  if (subtitle) {
    const sub = document.createElement('span');
    sub.className = 'ui-brand-sub';
    sub.textContent = subtitle;
    textWrap.append(sub);
  }

  root.append(markWrap, textWrap);
  return root;
}

/** 输出与 src/界面规范/favicon.svg 完全同源的独立 SVG 文本。 */
export function buildBrandFaviconSvgString({ tone = 'a3' } = {}) {
  const coreColor = tone === 'toxic' ? '#a8f22c' : '#ffb800';
  return (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    + '<rect width="24" height="24" rx="4" fill="#0d0e13"/>'
    + `<path fill="#f0d372" fill-opacity="0.16" stroke="#f0d372" stroke-width="1.6" stroke-linejoin="round" d="${BRAND_LOGO_LAYERS.faviconShield}"/>`
    + `<path fill="#f0d372" d="${BRAND_LOGO_LAYERS.faviconWedges}"/>`
    + `<path fill="${coreColor}" d="${BRAND_LOGO_LAYERS.faviconCore}"/>`
    + '</svg>'
  );
}

/** 基于 Path2D 离屏缓存在 2D 雷达 / 3D 沙盘停机坪 CanvasTexture 上绘制品牌标识或军工漏印贴花。 */
export function drawBrandLogoCanvas(ctx, x, y, size, {
  variant = 'stencil',
  tone = 'mono',
  color = null,
  alpha = 1,
  rotation = 0,
} = {}) {
  if (!ctx || !Number.isFinite(size) || size <= 0) return false;
  const pal = readSemanticPalette();
  const safeVariant = ['mark', 'sigil', 'stencil'].includes(variant) ? variant : 'stencil';
  const colors = resolveBrandToneColors(tone, pal);
  const monoFill = color || pal.select;
  const isMono = safeVariant === 'stencil' || safeVariant === 'sigil' || tone === 'mono';

  const shieldKey = safeVariant === 'stencil' ? 'shieldStencil' : 'shieldRing';
  const shieldPath = getCachedBrandPath2D(shieldKey, BRAND_LOGO_LAYERS[shieldKey]);
  const platePath = safeVariant === 'mark' && !isMono
    ? getCachedBrandPath2D('shieldOuter', BRAND_LOGO_LAYERS.shieldOuter)
    : null;
  const wedgePaths = BRAND_LOGO_LAYERS.wedges.map((d, i) => getCachedBrandPath2D(`wedge-${i}`, d));
  const corePath = getCachedBrandPath2D('core', BRAND_LOGO_LAYERS.core);
  if (!shieldPath || !corePath) return false;

  const scale = size / 64;
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, Number(alpha) || 0));
  ctx.translate(x, y);
  if (rotation) ctx.rotate(rotation);
  ctx.scale(scale, scale);
  ctx.translate(-32, -32);

  if (platePath && colors.plateFill !== 'none') {
    ctx.fillStyle = colors.plateFill;
    ctx.fill(platePath);
  }
  ctx.fillStyle = isMono ? monoFill : colors.shield;
  ctx.fill(shieldPath, 'evenodd');

  wedgePaths.forEach((wp, idx) => {
    if (!wp) return;
    ctx.fillStyle = isMono ? monoFill : (colors.wedges[idx] || colors.wedges[0]);
    ctx.fill(wp);
  });

  ctx.fillStyle = isMono ? monoFill : colors.core;
  ctx.fill(corePath);
  ctx.restore();
  return true;
}

/** 从挂载后的 CSS 变量实时读取语义调色盘（确保与 令牌.css 100% 同源）。 */
export function readSemanticPalette(root = typeof document !== 'undefined' ? document.documentElement : null) {
  if (!root || typeof getComputedStyle !== 'function') {
    return { ...TOKEN_FALLBACK };
  }
  const cs = getComputedStyle(root);
  const pick = (cssVar, fallback) => cs.getPropertyValue(cssVar).trim() || fallback;
  return {
    bgCanvas: pick('--ui-bg-canvas', TOKEN_FALLBACK.bgCanvas),
    bgPod: pick('--ui-bg-pod', TOKEN_FALLBACK.bgPod),
    textPrimary: pick('--ui-text-primary', TOKEN_FALLBACK.textPrimary),
    textSecondary: pick('--ui-text-secondary', TOKEN_FALLBACK.textSecondary),
    textMuted: pick('--ui-text-muted', TOKEN_FALLBACK.textMuted),
    strokeHalo: pick('--ui-stroke-halo', TOKEN_FALLBACK.strokeHalo),
    ground: pick('--ui-ch-ground', TOKEN_FALLBACK.ground),
    groundInner: pick('--ui-ch-ground-inner', TOKEN_FALLBACK.groundInner),
    groundFill: pick('--ui-ch-ground-fill', TOKEN_FALLBACK.groundFill),
    heli: pick('--ui-ch-heli', TOKEN_FALLBACK.heli),
    heliInner: pick('--ui-ch-heli-inner', TOKEN_FALLBACK.heliInner),
    heliFill: pick('--ui-ch-heli-fill', TOKEN_FALLBACK.heliFill),
    plane: pick('--ui-ch-plane', TOKEN_FALLBACK.plane),
    planeInner: pick('--ui-ch-plane-inner', TOKEN_FALLBACK.planeInner),
    planeFill: pick('--ui-ch-plane-fill', TOKEN_FALLBACK.planeFill),
    select: pick('--ui-state-select', TOKEN_FALLBACK.select),
    selectInner: pick('--ui-state-select-inner', TOKEN_FALLBACK.selectInner),
    selectFill: pick('--ui-state-select-fill', TOKEN_FALLBACK.selectFill),
    warn: pick('--ui-state-warn', TOKEN_FALLBACK.warn),
    warnFill: pick('--ui-state-warn-fill', TOKEN_FALLBACK.warnFill),
    danger: pick('--ui-state-danger', TOKEN_FALLBACK.danger),
    dangerFill: pick('--ui-state-danger-fill', TOKEN_FALLBACK.dangerFill),
    toxic: pick('--ui-state-toxic', TOKEN_FALLBACK.toxic),
    toxicFill: pick('--ui-state-toxic-fill', TOKEN_FALLBACK.toxicFill),
    safe: pick('--ui-state-safe', TOKEN_FALLBACK.safe),
    safeFill: pick('--ui-state-safe-fill', TOKEN_FALLBACK.safeFill),
  };
}

/** 解析 #RRGGBB 或 rgb()/rgba() 颜色并计算相对亮度与 WCAG 2.1 对比度。 */
export function parseCssColor(value) {
  const s = String(value || '').trim();
  if (s.startsWith('#')) {
    const hex = s.slice(1);
    const full = hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex.slice(0, 6);
    if (full.length === 6) {
      return {
        r: parseInt(full.slice(0, 2), 16),
        g: parseInt(full.slice(2, 4), 16),
        b: parseInt(full.slice(4, 6), 16),
      };
    }
  }
  const m = s.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i);
  if (m) {
    return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) };
  }
  return null;
}

/** 从语义调色值派生带透明度的 RGBA 颜色串（保持同源调色）。 */
export function colorWithAlpha(cssColor, alpha) {
  const rgb = parseCssColor(cssColor);
  const a = Math.max(0, Math.min(1, Number(alpha) || 0));
  if (!rgb) return `rgba(177, 151, 252, ${a})`;
  return `rgba(${Math.round(rgb.r)}, ${Math.round(rgb.g)}, ${Math.round(rgb.b)}, ${a})`;
}

export function computeContrastRatio(fgColor, bgColor) {
  const fg = parseCssColor(fgColor);
  const bg = parseCssColor(bgColor);
  if (!fg || !bg) return null;
  const lum = ({ r, g, b }) => {
    const ch = [r, g, b].map(v => {
      const n = v / 255;
      return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const l1 = lum(fg);
  const l2 = lum(bg);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

/** 根据单位兵种与状态生成方案 2「线框幽灵」绘制调色方案。 */
export function resolveRouteStyle(kind, mode, highlighted, palette) {
  const pal = palette ?? readSemanticPalette();
  const softOutline = 'rgba(8, 10, 16, 0.42)';
  if (mode === 'rejected') {
    return {
      outline: pal.strokeHalo,
      softOutline,
      main: pal.danger,
      fill: pal.dangerFill,
      badgeBg: pal.bgPod,
      badgeText: pal.danger,
      alpha: 0.90,
    };
  }
  if (mode === 'pending') {
    return {
      outline: pal.strokeHalo,
      softOutline,
      main: pal.warn,
      fill: pal.warnFill,
      badgeBg: pal.bgPod,
      badgeText: pal.warn,
      alpha: 0.78,
    };
  }
  const channel = VEHICLE_META[kind]?.channel ?? 'ground';
  const main = channel === 'ground'
    ? (highlighted || mode === 'preview' ? pal.select : pal.ground)
    : channel === 'heli'
      ? pal.heli
      : pal.plane;
  const fill = channel === 'ground'
    ? (highlighted || mode === 'preview' ? pal.selectFill : pal.groundFill)
    : channel === 'heli'
      ? pal.heliFill
      : pal.planeFill;
  return {
    outline: pal.strokeHalo,
    softOutline,
    main,
    fill,
    badgeBg: pal.bgPod,
    badgeText: main,
    alpha: mode === 'preview' ? 0.76 : (highlighted ? 0.62 : 0.28),
  };
}

/** 从底盘外环边缘切点起笔裁剪航路，避免压住车体与车下文字。 */
export function buildClippedRoute(unitPos, ringRadius, waypoints) {
  if (!waypoints || !waypoints.length) return null;
  const first = waypoints[0];
  const dx = first.x - unitPos.x;
  const dz = first.z - unitPos.z;
  const dist0 = Math.hypot(dx, dz);
  if (dist0 <= ringRadius + 0.5) return null;
  const start = { x: unitPos.x + (dx / dist0) * ringRadius, z: unitPos.z + (dz / dist0) * ringRadius };
  const points = [start, ...waypoints];
  let totalDist = 0;
  for (let i = 0; i < points.length - 1; i++) {
    totalDist += Math.hypot(points[i + 1].x - points[i].x, points[i + 1].z - points[i].z);
  }
  return { points, totalDist };
}

/** 拐点斜切倒角处理，营造硬朗战棋航路感。 */
export function chamferWaypoints(points, cutMeters = 3.2) {
  if (!points || points.length < 3) return points ?? [];
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1];
    const b = points[i];
    const c = points[i + 1];
    const d1 = Math.hypot(b.x - a.x, b.z - a.z);
    const d2 = Math.hypot(c.x - b.x, c.z - b.z);
    if (d1 > 8 && d2 > 8) {
      out.push({ x: b.x - ((b.x - a.x) / d1) * cutMeters, z: b.z - ((b.z - a.z) / d1) * cutMeters });
      out.push({ x: b.x + ((c.x - b.x) / d2) * cutMeters, z: b.z + ((c.z - b.z) / d2) * cutMeters });
    } else {
      out.push(b);
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

export function drawTacticalPolyline(ctx, screenPts, strokeStyle, lineWidth, dash = []) {
  if (!screenPts || screenPts.length < 2) return;
  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.setLineDash(dash);
  ctx.beginPath();
  screenPts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.stroke();
  ctx.setLineDash([]);
}

export function drawRouteChevrons(ctx, worldPoints, y, projectFn, style, spacingMeters = 24) {
  let carry = 6;
  for (let i = 0; i < worldPoints.length - 1; i++) {
    const a = worldPoints[i];
    const b = worldPoints[i + 1];
    const dx = b.x - a.x;
    const dz = b.z - a.z;
    const len = Math.hypot(dx, dz);
    if (len < 2) continue;
    const ux = dx / len;
    const uz = dz / len;
    const nx = -uz;
    const nz = ux;
    let d = carry;
    while (d < len - 3) {
      const sx = a.x + ux * d;
      const sz = a.z + uz * d;
      const tip = projectFn(sx + ux * 1.9, y, sz + uz * 1.9);
      const bL = projectFn(sx - ux * 1.0 + nx * 1.4, y, sz - uz * 1.0 + nz * 1.4);
      const bR = projectFn(sx - ux * 1.0 - nx * 1.4, y, sz - uz * 1.0 - nz * 1.4);
      ctx.fillStyle = style.main;
      ctx.strokeStyle = style.softOutline || 'rgba(8, 10, 16, 0.42)';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(tip.x, tip.y);
      ctx.lineTo(bL.x, bL.y);
      ctx.lineTo(bR.x, bR.y);
      ctx.closePath();
      ctx.stroke();
      ctx.fill();
      d += spacingMeters;
    }
    carry = Math.max(0, d - len);
  }
}

/** 绘制终点 1:1 线框幽灵轮廓（地面贴地 / 直升机 30m / 固定翼 100m 切入）。 */
export function drawWireframeGhost(ctx, kind, worldPt, y, facing, projectFn, sinTilt, style, highlighted) {
  const rot = (lx, lz) => projectFn(
    worldPt.x + lx * Math.cos(facing) - lz * Math.sin(facing),
    y,
    worldPt.z + lx * Math.sin(facing) + lz * Math.cos(facing),
  );
  if (kind === 'tank' || kind === 'supply') {
    const hw = kind === 'tank' ? 1.95 : 1.55;
    const hl = kind === 'tank' ? 3.5 : 3.1;
    const hull = [rot(-hw, -hl), rot(hw, -hl), rot(hw, hl), rot(-hw, hl)];
    if (highlighted) {
      ctx.fillStyle = style.fill;
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 3.8;
      ctx.beginPath();
      hull.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.8;
      ctx.stroke();
      if (kind === 'tank') {
        const tur = [rot(-1.15, -1.2), rot(1.15, -1.2), rot(1.05, 1.4), rot(-1.05, 1.4)];
        ctx.strokeStyle = style.main;
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        tur.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
        ctx.closePath();
        ctx.stroke();
      }
      const g0 = rot(0, -1);
      const g1 = rot(0, -hl - 3.5);
      const aL = rot(-1.2, -hl - 1.7);
      const aR = rot(1.2, -hl - 1.7);
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 3.6;
      ctx.beginPath();
      ctx.moveTo(g0.x, g0.y);
      ctx.lineTo(g1.x, g1.y);
      ctx.moveTo(aL.x, aL.y);
      ctx.lineTo(g1.x, g1.y);
      ctx.lineTo(aR.x, aR.y);
      ctx.stroke();
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.8;
      ctx.stroke();
    } else {
      const corner = 1.1;
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.6;
      for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        const p0 = rot(sx * hw, sz * hl);
        const px = rot(sx * (hw - corner), sz * hl);
        const pz = rot(sx * hw, sz * (hl - corner));
        ctx.beginPath();
        ctx.moveTo(px.x, px.y);
        ctx.lineTo(p0.x, p0.y);
        ctx.lineTo(pz.x, pz.y);
        ctx.stroke();
      }
    }
    return;
  }

  if (kind === 'heli') {
    const center = projectFn(worldPt.x, y, worldPt.z);
    const rr = 6.2 * (center.ppm ?? 1);
    if (highlighted) {
      ctx.fillStyle = style.fill;
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 3.2;
      ctx.beginPath();
      ctx.ellipse(center.x, center.y, rr, rr * sinTilt, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      const fuse = [rot(-0.9, -4.2), rot(0.9, -4.2), rot(0.6, 1.2), rot(0.25, 5.8), rot(-0.25, 5.8), rot(-0.6, 1.2)];
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 3.2;
      ctx.beginPath();
      fuse.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.stroke();
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.7;
      ctx.stroke();

      const wL = rot(-2.6, -0.5);
      const wR = rot(2.6, -0.5);
      ctx.beginPath();
      ctx.moveTo(wL.x, wL.y);
      ctx.lineTo(wR.x, wR.y);
      ctx.stroke();

      const n0 = rot(0, -4.2);
      const n1 = rot(0, -7.5);
      const aL = rot(-1.2, -5.8);
      const aR = rot(1.2, -5.8);
      ctx.beginPath();
      ctx.moveTo(n0.x, n0.y);
      ctx.lineTo(n1.x, n1.y);
      ctx.moveTo(aL.x, aL.y);
      ctx.lineTo(n1.x, n1.y);
      ctx.lineTo(aR.x, aR.y);
      ctx.stroke();
    } else {
      ctx.strokeStyle = style.main;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.ellipse(center.x, center.y, rr * 0.65, rr * 0.65 * sinTilt, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    return;
  }

  // 固定翼 A-10C 1:1 线框幽灵
  const pts = [
    rot(0, -7.6), rot(-1.2, -3.0), rot(-8.6, -1.6), rot(-8.6, 1.2), rot(-1.1, 0.8),
    rot(-0.8, 5.8), rot(-2.8, 6.6), rot(-2.8, 7.4), rot(2.8, 7.4), rot(2.8, 6.6),
    rot(0.8, 5.8), rot(1.1, 0.8), rot(8.6, 1.2), rot(8.6, -1.6), rot(1.2, -3.0),
  ];
  ctx.fillStyle = highlighted ? style.fill : 'transparent';
  ctx.strokeStyle = style.outline;
  ctx.lineWidth = highlighted ? 3.4 : 2.4;
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
  if (highlighted) ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = style.main;
  ctx.lineWidth = highlighted ? 1.8 : 1.3;
  ctx.stroke();
  if (highlighted) {
    const n0 = rot(0, -7.6);
    const n1 = rot(0, -11.2);
    const aL = rot(-1.4, -9.2);
    const aR = rot(1.4, -9.2);
    ctx.beginPath();
    ctx.moveTo(n0.x, n0.y);
    ctx.lineTo(n1.x, n1.y);
    ctx.moveTo(aL.x, aL.y);
    ctx.lineTo(n1.x, n1.y);
    ctx.lineTo(aR.x, aR.y);
    ctx.stroke();
  }
}

/** 固定翼高空盘旋圆轨（仅在真实高空绘制，地面绝不画盘旋圈）。 */
export function drawHighOrbitTrack(ctx, centerWorld, altY, radiusMeters, projectFn, style, highlighted, drawArrows = true) {
  const segs = 40;
  const screenPts = [];
  for (let i = 0; i <= segs; i++) {
    const a = (i / segs) * Math.PI * 2;
    screenPts.push(projectFn(centerWorld.x + Math.cos(a) * radiusMeters, altY, centerWorld.z + Math.sin(a) * radiusMeters));
  }
  drawTacticalPolyline(ctx, screenPts, style.outline, highlighted ? 4.0 : 2.6, [8, 6]);
  drawTacticalPolyline(ctx, screenPts, style.main, highlighted ? 1.8 : 1.2, [8, 6]);
  if (drawArrows) {
    for (const a of [0, Math.PI * 0.5, Math.PI, Math.PI * 1.5]) {
      const cx = centerWorld.x + Math.cos(a) * radiusMeters;
      const cz = centerWorld.z + Math.sin(a) * radiusMeters;
      const tx = Math.sin(a);
      const tz = -Math.cos(a);
      const nx = Math.cos(a);
      const nz = Math.sin(a);
      const tip = projectFn(cx + tx * 2.6, altY, cz + tz * 2.6);
      const bL = projectFn(cx - tx * 1.2 + nx * 1.6, altY, cz - tz * 1.2 + nz * 1.6);
      const bR = projectFn(cx - tx * 1.2 - nx * 1.6, altY, cz - tz * 1.2 - nz * 1.6);
      ctx.fillStyle = style.main;
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(tip.x, tip.y);
      ctx.lineTo(bL.x, bL.y);
      ctx.lineTo(bR.x, bR.y);
      ctx.closePath();
      ctx.stroke();
      ctx.fill();
    }
  }
}

/** 空中单位选中/悬停：高空机身战术四角锁定框。 */
export function drawAirLockBrackets(ctx, boundPts, style, isSelected) {
  if (!boundPts || !boundPts.length) return;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of boundPts) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  const cx = (minX + maxX) * 0.5;
  const cy = (minY + maxY) * 0.5;
  const hw = Math.max(15, (maxX - minX) * 0.5 + 6);
  const hh = Math.max(13, (maxY - minY) * 0.5 + 6);
  const x0 = cx - hw;
  const x1 = cx + hw;
  const y0 = cy - hh;
  const y1 = cy + hh;
  const cLen = Math.max(6, Math.min(11, Math.min(hw, hh) * 0.52));
  const strokeCorners = () => {
    ctx.beginPath();
    ctx.moveTo(x0, y0 + cLen); ctx.lineTo(x0, y0); ctx.lineTo(x0 + cLen, y0);
    ctx.moveTo(x1 - cLen, y0); ctx.lineTo(x1, y0); ctx.lineTo(x1, y0 + cLen);
    ctx.moveTo(x0, y1 - cLen); ctx.lineTo(x0, y1); ctx.lineTo(x0 + cLen, y1);
    ctx.moveTo(x1 - cLen, y1); ctx.lineTo(x1, y1); ctx.lineTo(x1, y1 - cLen);
    ctx.stroke();
  };
  ctx.save();
  ctx.globalAlpha = isSelected ? 0.96 : 0.78;
  ctx.lineCap = 'square';
  ctx.lineJoin = 'miter';
  ctx.strokeStyle = style.outline;
  ctx.lineWidth = 3.8;
  strokeCorners();
  ctx.strokeStyle = style.main;
  ctx.lineWidth = isSelected ? 2.0 : 1.5;
  strokeCorners();
  ctx.restore();
}

export function drawEtaBadge(ctx, screenPt, totalDistMeters, maxSpeedMps, style, valid, reason, extraSuffix = '') {
  const sec = Math.max(1, Math.round(totalDistMeters / Math.max(1, maxSpeedMps)));
  const label = valid ? `${Math.round(totalDistMeters)}m · ${sec}s${extraSuffix}` : `✕ ${reason}`;
  ctx.save();
  ctx.font = '700 11px "Cascadia Mono","Consolas","Microsoft YaHei UI",sans-serif';
  const tw = ctx.measureText(label).width + 14;
  const bx = screenPt.x + 16;
  const by = screenPt.y - 24;
  ctx.fillStyle = style.badgeBg;
  ctx.strokeStyle = style.main;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.roundRect(bx, by, tw, 20, 3);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = style.badgeText;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, bx + 7, by + 10.5);
  ctx.restore();
}

function rectsOverlap(a, b, margin) {
  return !(
    a.right + margin <= b.left ||
    a.left - margin >= b.right ||
    a.bottom + margin <= b.top ||
    a.top - margin >= b.bottom
  );
}

/**
 * 密集编队车底防叠字碰撞引擎：
 * 严格保持每个单位的车底锚点坐标绝不偏移（anchorMoved: false），
 * 按「悬停 > 选中 > 移动或交战 > 待命」顺序决定原地完整展开、原地收缩剪影点或低透退让。
 */
export class GroundNameplateCollision {
  static resolve(items, { margin = 3 } = {}) {
    const rank = item => (item.isHovered ? 4 : item.isSelected ? 3 : item.isActiveTask ? 2 : 1);
    const ordered = [...items].sort((a, b) => rank(b) - rank(a) || String(a.id).localeCompare(String(b.id)));
    const occupiedFull = [];
    const occupiedHighFull = [];
    const occupiedCompact = [];
    const results = new Map();

    for (const item of ordered) {
      const priority = rank(item);
      const fullW = item.fullSize?.width ?? 76;
      const fullH = item.fullSize?.height ?? 22;
      const compW = item.compactSize?.width ?? 24;
      const compH = item.compactSize?.height ?? 20;
      const ax = item.anchor.x;
      const ay = item.anchor.y;
      const fullRect = { left: ax - fullW * 0.5, right: ax + fullW * 0.5, top: ay, bottom: ay + fullH };
      const compactRect = { left: ax - compW * 0.5, right: ax + compW * 0.5, top: ay, bottom: ay + compH };

      if (priority >= 3) {
        occupiedFull.push(fullRect);
        occupiedHighFull.push(fullRect);
        results.set(item.id, { id: item.id, priority, mode: 'full', isTop: true, anchor: { x: ax, y: ay }, rect: fullRect, anchorMoved: false });
        continue;
      }

      const fullBlocked = occupiedFull.some(r => rectsOverlap(fullRect, r, margin));
      if (!fullBlocked) {
        occupiedFull.push(fullRect);
        results.set(item.id, { id: item.id, priority, mode: 'full', isTop: false, anchor: { x: ax, y: ay }, rect: fullRect, anchorMoved: false });
        continue;
      }

      const compactHitsHigh = occupiedHighFull.some(r => rectsOverlap(compactRect, r, margin));
      const compactTooClose = occupiedCompact.some(c => Math.hypot(ax - c.x, ay - c.y) < 10);
      if (!compactHitsHigh && !compactTooClose) {
        occupiedCompact.push({ x: ax, y: ay, rect: compactRect });
        results.set(item.id, { id: item.id, priority, mode: 'compact', isTop: false, anchor: { x: ax, y: ay }, rect: compactRect, anchorMoved: false });
      } else {
        results.set(item.id, { id: item.id, priority, mode: 'dimmed', isTop: false, anchor: { x: ax, y: ay }, rect: compactRect, anchorMoved: false });
      }
    }

    return items.map(item => results.get(item.id));
  }
}

/** Tab 循环重叠单位辅助函数。 */
export function cycleOverlappingUnits(candidates, currentId, reverse = false) {
  if (!candidates || !candidates.length) return null;
  const idx = candidates.findIndex(item => item.id === currentId);
  if (idx < 0) return candidates[0];
  const step = reverse ? -1 : 1;
  return candidates[(idx + step + candidates.length) % candidates.length];
}

/* ====== 4 级视距战术图标分层、密集簇聚合与离屏剪影缓存 ====== */

export const ZOOM_ICON_TIERS = Object.freeze({
  NONE: 'none',
  COMPACT: 'compact',
  CHIP: 'chip',
  OVERVIEW: 'overview',
});

export const ZOOM_TIER_SIZES = Object.freeze({
  none: Object.freeze({ w: 0, h: 0 }),
  compact: Object.freeze({ w: 22, h: 16 }),
  chip: Object.freeze({ w: 26, h: 18 }),
  overview: Object.freeze({ w: 18, h: 14 }),
});

/**
 * 纯规则：按相机视距 span（米）判定 2D 标绘层图标分层档位。
 * - L0 (<=260m): none（近景不绘 2D 图标，保留 680p 微缩实景与贴身名牌）
 * - L1 (260~650m): compact（22x16 紧凑 12 类剪影徽章）
 * - L2 (650~2200m): chip（26x18 标准战术筹码 + 簇聚合）
 * - L3 (>2200m): overview（18x14 极简战略点标 + 簇聚合）
 */
export function resolveZoomIconTier(span) {
  if (!Number.isFinite(span) || span <= 260) return ZOOM_ICON_TIERS.NONE;
  if (span <= 650) return ZOOM_ICON_TIERS.COMPACT;
  if (span <= 2200) return ZOOM_ICON_TIERS.CHIP;
  return ZOOM_ICON_TIERS.OVERVIEW;
}

export function getZoomTierIconSize(tier) {
  return ZOOM_TIER_SIZES[tier] || ZOOM_TIER_SIZES.none;
}

/**
 * 纯规则：中远景/总览（L2/L3）同通道屏幕密集簇聚合。
 * - 严格按通道（ground / heli / plane）隔离，跨通道永不误聚；
 * - 屏幕欧氏距离 < radiusPx 且成员数 >= minClusterSize 时合并为带 ×N 计数的主兵种簇节点；
 * - 不满足数量门槛时保留为独立单车节点，不破坏 Tab 重叠循环。
 */
export function clusterScreenUnits(projectedItems, { radiusPx = 18, minClusterSize = 3 } = {}) {
  const valid = (projectedItems || []).filter(
    it => it && it.visible !== false && it.alive !== false && Number.isFinite(it.x) && Number.isFinite(it.y),
  );
  if (!valid.length) return [];

  const byChannel = new Map();
  for (const item of valid) {
    const ch = item.channel || VEHICLE_META[item.iconKind]?.channel || 'ground';
    if (!byChannel.has(ch)) byChannel.set(ch, []);
    byChannel.get(ch).push(item);
  }

  const rLimit = Math.max(1, Number(radiusPx) || 18);
  const minSz = Math.max(2, Number(minClusterSize) || 3);
  const nodes = [];

  for (const [channel, bucket] of byChannel.entries()) {
    const used = new Uint8Array(bucket.length);
    for (let i = 0; i < bucket.length; i += 1) {
      if (used[i]) continue;
      const seed = bucket[i];
      const memberIndices = [i];
      for (let j = i + 1; j < bucket.length; j += 1) {
        if (used[j]) continue;
        const cand = bucket[j];
        if (Math.hypot(cand.x - seed.x, cand.y - seed.y) < rLimit) {
          memberIndices.push(j);
        }
      }

      if (memberIndices.length >= minSz) {
        let sumX = 0;
        let sumY = 0;
        let sumHp = 0;
        let selectedCount = 0;
        let hoveredCount = 0;
        const kindFreq = new Map();
        const unitIds = [];
        const members = [];
        for (const idx of memberIndices) {
          used[idx] = 1;
          const m = bucket[idx];
          members.push(m);
          unitIds.push(m.id);
          sumX += m.x;
          sumY += m.y;
          sumHp += Number.isFinite(m.hp) ? m.hp : 100;
          if (m.selected) selectedCount += 1;
          if (m.hovered) hoveredCount += 1;
          const k = m.iconKind || 'mbt';
          kindFreq.set(k, (kindFreq.get(k) || 0) + 1);
        }
        let dominantKind = members[0].iconKind || 'mbt';
        let maxFreq = -1;
        for (const [k, cnt] of kindFreq.entries()) {
          if (cnt > maxFreq) {
            maxFreq = cnt;
            dominantKind = k;
          }
        }
        nodes.push({
          isCluster: true,
          id: `cluster:${channel}:${unitIds.join(',')}`,
          channel,
          iconKind: dominantKind,
          dominantKind,
          count: members.length,
          unitIds,
          screenPt: { x: sumX / members.length, y: sumY / members.length },
          selectedCount,
          hoveredCount,
          hp: sumHp / members.length,
          items: members,
        });
      } else {
        used[i] = 1;
        nodes.push({
          isCluster: false,
          id: seed.id,
          channel,
          iconKind: seed.iconKind || 'mbt',
          dominantKind: seed.iconKind || 'mbt',
          count: 1,
          unitIds: [seed.id],
          screenPt: { x: seed.x, y: seed.y },
          selectedCount: seed.selected ? 1 : 0,
          hoveredCount: seed.hovered ? 1 : 0,
          hp: Number.isFinite(seed.hp) ? seed.hp : 100,
          item: seed,
        });
      }
    }
  }
  return nodes;
}

const _path2DCache = new Map();
const _spriteCanvasCache = new Map();

/** 惰性预编译并缓存 12 类军事负空间剪影 Path2D（Node 无 Path2D 时安全返回 null）。 */
export function getSilhouettePath2D(kind) {
  if (typeof Path2D === 'undefined') return null;
  const key = VEHICLE_SILHOUETTES[kind] ? kind : 'mbt';
  if (!_path2DCache.has(key)) {
    _path2DCache.set(key, new Path2D(VEHICLE_SILHOUETTES[key]));
  }
  return _path2DCache.get(key);
}

/** 预热全部 12 类标准兵种与别名 Path2D 缓存（启动阶段调用，零运行期开销）。 */
export function warmSilhouettePathCache() {
  if (typeof Path2D === 'undefined') return 0;
  for (const key of Object.keys(VEHICLE_SILHOUETTES)) {
    getSilhouettePath2D(key);
  }
  return _path2DCache.size;
}

function getSilhouetteSprite(kind, fillStyle, sizePx) {
  if (typeof document === 'undefined' || typeof Path2D === 'undefined') return null;
  const normKey = `${VEHICLE_SILHOUETTES[kind] ? kind : 'mbt'}|${fillStyle}|${sizePx}`;
  if (_spriteCanvasCache.has(normKey)) {
    return _spriteCanvasCache.get(normKey);
  }
  const p2d = getSilhouettePath2D(kind);
  if (!p2d) return null;
  const scale = 2; // 2x 超采样离屏精灵，在 DPR 高清标绘层保持锯齿零模糊
  const off = document.createElement('canvas');
  off.width = Math.max(8, Math.round(sizePx * scale));
  off.height = Math.max(8, Math.round(sizePx * scale));
  const octx = off.getContext('2d');
  if (!octx) return null;
  const s = (sizePx * scale) / 24;
  octx.scale(s, s);
  octx.fillStyle = fillStyle;
  octx.fill(p2d, 'evenodd');
  if (_spriteCanvasCache.size >= 256) {
    const firstKey = _spriteCanvasCache.keys().next().value;
    if (firstKey !== undefined) _spriteCanvasCache.delete(firstKey);
  }
  _spriteCanvasCache.set(normKey, off);
  return off;
}

/** 使用预编译离屏精灵/Path2D 高速绘制 12 类负空间剪影（零 DOM 分配）。 */
export function drawCachedSilhouette(ctx, kind, cx, cy, sizePx, fillStyle) {
  const sprite = getSilhouetteSprite(kind, fillStyle, sizePx);
  if (sprite) {
    ctx.drawImage(sprite, cx - sizePx * 0.5, cy - sizePx * 0.5, sizePx, sizePx);
    return true;
  }
  const p2d = getSilhouettePath2D(kind);
  if (!p2d) return false;
  const s = sizePx / 24;
  ctx.save();
  ctx.translate(cx - sizePx * 0.5, cy - sizePx * 0.5);
  ctx.scale(s, s);
  ctx.fillStyle = fillStyle;
  ctx.fill(p2d, 'evenodd');
  ctx.restore();
  return true;
}

function resolveChannelColors(channel, pal) {
  if (channel === 'heli') {
    return { main: pal.heli, inner: pal.heliInner, fill: pal.heliFill };
  }
  if (channel === 'plane') {
    return { main: pal.plane, inner: pal.planeInner, fill: pal.planeFill };
  }
  return { main: pal.ground, inner: pal.groundInner, fill: pal.groundFill };
}

function buildOverviewMarkerPath(ctx, channel, cx, cy, w, h) {
  const hw = w * 0.5;
  const hh = h * 0.5;
  ctx.beginPath();
  if (channel === 'plane') {
    // 固定翼/无人机：上指战术菱三角徽
    ctx.moveTo(cx, cy - hh);
    ctx.lineTo(cx + hw, cy + hh * 0.45);
    ctx.lineTo(cx + hw * 0.42, cy + hh);
    ctx.lineTo(cx - hw * 0.42, cy + hh);
    ctx.lineTo(cx - hw, cy + hh * 0.45);
    ctx.closePath();
    return;
  }
  if (channel === 'heli') {
    // 旋翼机：横菱战术六边徽
    ctx.moveTo(cx - hw, cy);
    ctx.lineTo(cx - hw * 0.45, cy - hh);
    ctx.lineTo(cx + hw * 0.45, cy - hh);
    ctx.lineTo(cx + hw, cy);
    ctx.lineTo(cx + hw * 0.45, cy + hh);
    ctx.lineTo(cx - hw * 0.45, cy + hh);
    ctx.closePath();
    return;
  }
  // 地面：切角军用筹码框
  const cut = 2.5;
  ctx.moveTo(cx - hw + cut, cy - hh);
  ctx.lineTo(cx + hw - cut, cy - hh);
  ctx.lineTo(cx + hw, cy - hh + cut);
  ctx.lineTo(cx + hw, cy + hh - cut);
  ctx.lineTo(cx + hw - cut, cy + hh);
  ctx.lineTo(cx - hw + cut, cy + hh);
  ctx.lineTo(cx - hw, cy + hh - cut);
  ctx.lineTo(cx - hw, cy - hh + cut);
  ctx.closePath();
}

/**
 * 绘制 L1(compact 22x16) / L2(chip 26x18) / L3(overview 18x14) 2D 高清视距图标及簇聚合徽标。
 */
export function drawZoomTierUnitIcon(ctx, node, tier, palette, { showExpandHint = false } = {}) {
  const size = getZoomTierIconSize(tier);
  if (!size.w || !size.h || !node?.screenPt) return null;

  const pal = palette ?? readSemanticPalette();
  const chCol = resolveChannelColors(node.channel, pal);
  const isSelected = (node.selectedCount || 0) > 0;
  const isHovered = (node.hoveredCount || 0) > 0;
  const cx = Math.round(node.screenPt.x);
  const cy = Math.round(node.screenPt.y);
  const { w, h } = size;
  const x0 = cx - w * 0.5;
  const y0 = cy - h * 0.5;
  const hpRatio = Math.max(0, Math.min(1, (Number.isFinite(node.hp) ? node.hp : 100) / 100));
  const strokeCol = isSelected ? pal.select : isHovered ? '#ffffff' : chCol.main;

  ctx.save();
  if (tier === ZOOM_ICON_TIERS.OVERVIEW) {
    if (isSelected) {
      ctx.strokeStyle = colorWithAlpha(pal.select, 0.48);
      ctx.lineWidth = 1.6;
      buildOverviewMarkerPath(ctx, node.channel, cx, cy, w + 6, h + 6);
      ctx.stroke();
    }
    buildOverviewMarkerPath(ctx, node.channel, cx, cy, w, h);
    ctx.fillStyle = pal.bgPod;
    ctx.fill();
    ctx.fillStyle = colorWithAlpha(isSelected ? pal.select : chCol.main, isSelected ? 0.24 : 0.16);
    ctx.fill();
    ctx.strokeStyle = pal.strokeHalo;
    ctx.lineWidth = 2.8;
    ctx.stroke();
    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = isSelected ? 1.7 : 1.25;
    ctx.stroke();
    drawCachedSilhouette(ctx, node.iconKind, cx, cy, 10, isSelected ? pal.selectInner : chCol.inner);
  } else {
    const radius = tier === ZOOM_ICON_TIERS.COMPACT ? 3 : 3.5;
    // 外黑描边防止浅色草地/水泥地面对比度流失
    ctx.beginPath();
    ctx.roundRect(x0, y0, w, h, radius);
    ctx.fillStyle = pal.bgPod;
    ctx.fill();
    if (tier === ZOOM_ICON_TIERS.COMPACT) {
      ctx.fillStyle = colorWithAlpha(isSelected ? pal.select : chCol.main, isSelected ? 0.26 : 0.18);
      ctx.fill();
    }
    ctx.strokeStyle = pal.strokeHalo;
    ctx.lineWidth = 2.8;
    ctx.stroke();
    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = isSelected ? 1.8 : 1.25;
    ctx.stroke();

    const silSize = tier === ZOOM_ICON_TIERS.CHIP ? 13 : 11;
    drawCachedSilhouette(ctx, node.iconKind, cx, cy - 1.2, silSize, isSelected ? pal.selectInner : chCol.inner);

    // 底部 2px 血条
    const barW = w - 6;
    const barX = x0 + 3;
    const barY = y0 + h - 3.2;
    ctx.fillStyle = 'rgba(255, 255, 255, 0.14)';
    ctx.fillRect(barX, barY, barW, 1.8);
    ctx.fillStyle = hpRatio < 0.25 ? pal.danger : (isSelected ? pal.select : chCol.main);
    ctx.fillRect(barX, barY, Math.max(1.5, barW * hpRatio), 1.8);
  }

  // 密集簇右上角高对比 ×N 徽标
  if (node.isCluster && node.count >= 2) {
    const badgeTxt = `×${node.count}`;
    ctx.font = '800 9.5px "Cascadia Mono","Consolas",monospace';
    const bw = Math.max(16, ctx.measureText(badgeTxt).width + 6);
    const bh = 12;
    const bx = x0 + w - bw * 0.45;
    const by = y0 - bh * 0.55;
    ctx.fillStyle = isSelected ? pal.select : pal.bgPod;
    ctx.strokeStyle = isSelected ? pal.strokeHalo : strokeCol;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.roundRect(bx, by, bw, bh, 4);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = isSelected ? pal.bgCanvas : pal.textPrimary;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(badgeTxt, bx + bw * 0.5, by + bh * 0.52);

    // 悬停簇筹码时轻量放射展开提示成员位置
    if (showExpandHint && Array.isArray(node.items) && node.items.length > 0) {
      const n = Math.min(node.items.length, 8);
      const ringR = Math.max(w * 0.9, 20);
      for (let idx = 0; idx < n; idx += 1) {
        const ang = (idx / n) * Math.PI * 2 - Math.PI * 0.5;
        const px = cx + Math.cos(ang) * ringR;
        const py = cy + Math.sin(ang) * ringR;
        ctx.strokeStyle = colorWithAlpha(strokeCol, 0.58);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(ang) * (w * 0.55), cy + Math.sin(ang) * (h * 0.55));
        ctx.lineTo(px, py);
        ctx.stroke();
        ctx.fillStyle = chCol.inner;
        ctx.beginPath();
        ctx.arc(px, py, 2.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
  ctx.restore();

  return {
    left: x0,
    top: y0,
    right: x0 + w,
    bottom: y0 + h,
    cx,
    cy,
    w,
    h,
  };
}

/* ====== Stage 1B：固定翼贴地主范围圈、垂足实时跟踪点与机头 35° 转向弧 ====== */

/** 采样贴地盘旋范围圈（按 48~64 段查询真实地面高程 + 0.15m 抬升防 Z-fighting）。 */
export function sampleGroundOrbitPoints(centerWorld, radiusMeters, queryGroundHeight, segments = 56, heightOffset = 0.15) {
  const segs = Math.max(48, Math.min(64, Math.round(segments || 56)));
  const r = Math.max(1, Number(radiusMeters) || 85);
  const cx = Number(centerWorld?.x) || 0;
  const cz = Number(centerWorld?.z) || 0;
  const qFn = typeof queryGroundHeight === 'function' ? queryGroundHeight : () => 0;
  const pts = [];
  for (let i = 0; i <= segs; i += 1) {
    const a = (i / segs) * Math.PI * 2;
    const wx = cx + Math.cos(a) * r;
    const wz = cz + Math.sin(a) * r;
    const gy = qFn(wx, wz);
    pts.push({
      x: wx,
      y: (Number.isFinite(gy) ? gy : 0) + heightOffset,
      z: wz,
    });
  }
  return pts;
}

/** 计算高空飞机中心在真实地形上的实时地面垂足坐标。 */
export function computeGroundFootProjection(pos, queryGroundHeight, heightOffset = 0.15) {
  const x = Number(pos?.x) || 0;
  const z = Number(pos?.z) || 0;
  const qFn = typeof queryGroundHeight === 'function' ? queryGroundHeight : () => 0;
  const gy = qFn(x, z);
  return {
    x,
    y: (Number.isFinite(gy) ? gy : 0) + heightOffset,
    z,
  };
}

/**
 * 绘制固定翼贴地主范围圈（Stage 1B 核心原语）：
 * 1) 48~64 段起伏地面高程闭合环 + 淡紫微填充 + 深描边双段虚线 + 4 象限切线箭头；
 * 2) 地面圆心十字靶标与半径标；
 * 3) 若提供 planePos：高空垂足虚线落至地面实时投影点 + 同步滑动淡紫菱形双环光点。
 */
export function drawGroundOrbitZone(
  ctx,
  centerWorld,
  radiusMeters,
  queryGroundHeight,
  projectFn,
  style,
  highlighted,
  {
    planePos = null,
    segments = 56,
    drawArrows = true,
    drawCenterCross = true,
    drawFootLine = true,
    radiusLabel = '',
    palette = null,
  } = {},
) {
  const pal = palette ?? readSemanticPalette();
  const qFn = typeof queryGroundHeight === 'function' ? queryGroundHeight : () => 0;
  const worldPts = sampleGroundOrbitPoints(centerWorld, radiusMeters, qFn, segments, 0.15);
  const screenPts = worldPts.map(p => projectFn(p.x, p.y, p.z));
  const r = Math.max(1, Number(radiusMeters) || 85);
  const fillCol = colorWithAlpha(pal.plane, highlighted ? 0.10 : 0.05);
  const strokeCol = highlighted ? style.main : pal.plane;

  ctx.save();
  // 1) 淡紫微填充 + 双层描边闭合圈
  ctx.beginPath();
  screenPts.forEach((p, idx) => (idx === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  ctx.fillStyle = fillCol;
  ctx.fill();

  ctx.globalAlpha = highlighted ? 0.58 : 0.24;
  ctx.strokeStyle = style.softOutline || style.outline || pal.strokeHalo;
  ctx.lineWidth = highlighted ? 3.0 : 2.0;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.setLineDash([8, 6]);
  ctx.stroke();

  ctx.strokeStyle = strokeCol;
  ctx.lineWidth = highlighted ? 1.65 : 1.15;
  ctx.stroke();
  ctx.setLineDash([]);

  // 2) 4 象限贴地切线方向小箭头（匹配 orbitPhase 递增方向）
  if (drawArrows) {
    for (const a of [0, Math.PI * 0.5, Math.PI, Math.PI * 1.5]) {
      const cx = centerWorld.x + Math.cos(a) * r;
      const cz = centerWorld.z + Math.sin(a) * r;
      const cy = (Number.isFinite(qFn(cx, cz)) ? qFn(cx, cz) : 0) + 0.15;
      const tx = -Math.sin(a);
      const tz = Math.cos(a);
      const nx = Math.cos(a);
      const nz = Math.sin(a);
      const tip = projectFn(cx + tx * 2.8, cy, cz + tz * 2.8);
      const bL = projectFn(cx - tx * 1.4 + nx * 1.7, cy, cz - tz * 1.4 + nz * 1.7);
      const bR = projectFn(cx - tx * 1.4 - nx * 1.7, cy, cz - tz * 1.4 - nz * 1.7);
      ctx.fillStyle = strokeCol;
      ctx.strokeStyle = style.softOutline || style.outline || pal.strokeHalo;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(tip.x, tip.y);
      ctx.lineTo(bL.x, bL.y);
      ctx.lineTo(bR.x, bR.y);
      ctx.closePath();
      ctx.stroke();
      ctx.fill();
    }
  }

  // 3) 地面圆心十字靶标与半径标注
  if (drawCenterCross) {
    const ocY = (Number.isFinite(qFn(centerWorld.x, centerWorld.z)) ? qFn(centerWorld.x, centerWorld.z) : 0) + 0.15;
    const ocScr = projectFn(centerWorld.x, ocY, centerWorld.z);
    const arm = highlighted ? 7 : 5;
    ctx.strokeStyle = style.outline || pal.strokeHalo;
    ctx.lineWidth = 3.0;
    ctx.beginPath();
    ctx.moveTo(ocScr.x - arm, ocScr.y); ctx.lineTo(ocScr.x + arm, ocScr.y);
    ctx.moveTo(ocScr.x, ocScr.y - arm); ctx.lineTo(ocScr.x, ocScr.y + arm);
    ctx.stroke();

    ctx.strokeStyle = strokeCol;
    ctx.lineWidth = 1.45;
    ctx.beginPath();
    ctx.moveTo(ocScr.x - arm, ocScr.y); ctx.lineTo(ocScr.x + arm, ocScr.y);
    ctx.moveTo(ocScr.x, ocScr.y - arm); ctx.lineTo(ocScr.x, ocScr.y + arm);
    ctx.stroke();

    ctx.fillStyle = pal.planeInner;
    ctx.beginPath();
    ctx.arc(ocScr.x, ocScr.y, 2.2, 0, Math.PI * 2);
    ctx.fill();

    if (highlighted && radiusLabel) {
      ctx.font = '700 10px "Cascadia Mono","Consolas",monospace';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.strokeStyle = pal.strokeHalo;
      ctx.lineWidth = 3;
      ctx.strokeText(radiusLabel, ocScr.x + 9, ocScr.y - 7);
      ctx.fillStyle = pal.planeInner;
      ctx.fillText(radiusLabel, ocScr.x + 9, ocScr.y - 7);
    }
  }

  // 4) 高空垂足虚线 + 地面圆周同步滑动的淡紫菱形双环光点
  if (planePos && Number.isFinite(planePos.x) && Number.isFinite(planePos.z)) {
    const footWorld = computeGroundFootProjection(planePos, qFn, 0.15);
    const footScr = projectFn(footWorld.x, footWorld.y, footWorld.z);
    const bodyScr = projectFn(planePos.x, planePos.y ?? 234, planePos.z);

    if (drawFootLine) {
      ctx.save();
      ctx.strokeStyle = highlighted ? pal.select : pal.plane;
      ctx.globalAlpha = highlighted ? 0.78 : 0.36;
      ctx.lineWidth = highlighted ? 1.4 : 1.05;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(bodyScr.x, bodyScr.y);
      ctx.lineTo(footScr.x, footScr.y);
      ctx.stroke();
      ctx.restore();
    }

    // 淡紫描边实心菱形 + 外光环
    const dr = highlighted ? 5.6 : 4.2;
    ctx.save();
    ctx.globalAlpha = highlighted ? 0.98 : 0.76;
    ctx.strokeStyle = colorWithAlpha(pal.plane, 0.65);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.arc(footScr.x, footScr.y, dr + 2.6, 0, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(footScr.x, footScr.y - dr);
    ctx.lineTo(footScr.x + dr, footScr.y);
    ctx.lineTo(footScr.x, footScr.y + dr);
    ctx.lineTo(footScr.x - dr, footScr.y);
    ctx.closePath();
    ctx.fillStyle = pal.planeInner;
    ctx.fill();
    ctx.strokeStyle = style.outline || pal.strokeHalo;
    ctx.lineWidth = 2.6;
    ctx.stroke();
    ctx.strokeStyle = highlighted ? pal.select : pal.plane;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}

/**
 * 绘制固定翼高空极简 35° 机头转向导引虚弧（替代旧 234m 高空整圈）。
 */
export function drawAirTurnArc(ctx, planePos, orbitCenter, orbitRadius, projectFn, style, {
  arcDeg = 35,
  yaw = 0,
  turnDir = 1,
  highlighted = false,
  onlyOnOrbit = true,
} = {}) {
  if (!planePos || !Number.isFinite(planePos.x) || !Number.isFinite(planePos.z)) return false;
  const altY = Number.isFinite(planePos.y) ? planePos.y : 234.0;
  const r = Math.max(12, Number(orbitRadius) || 85);
  const arcRad = ((Number(arcDeg) || 35) * Math.PI) / 180;
  const segs = 10;
  const pts = [];

  if (orbitCenter && Number.isFinite(orbitCenter.x) && Number.isFinite(orbitCenter.z)) {
    const dx = planePos.x - orbitCenter.x;
    const dz = planePos.z - orbitCenter.z;
    const dist = Math.hypot(dx, dz);
    if (dist > 4 && Math.abs(dist - r) <= Math.max(14, r * 0.25)) {
      const phi0 = Math.atan2(dz, dx);
      for (let i = 0; i <= segs; i += 1) {
        const a = phi0 + (i / segs) * arcRad * (turnDir >= 0 ? 1 : -1);
        const radiusAtStep = dist + (r - dist) * (i / segs);
        pts.push(projectFn(orbitCenter.x + Math.cos(a) * radiusAtStep, altY, orbitCenter.z + Math.sin(a) * radiusAtStep));
      }
    }
  }

  // 当启用 onlyOnOrbit 时，直线切入期间不再绘制机头分叉引导弧
  if (!pts.length) {
    if (onlyOnOrbit) return false;
    const arcLen = Math.min(34, r * arcRad * 0.65);
    const dir = turnDir >= 0 ? 1 : -1;
    for (let i = 0; i <= segs; i += 1) {
      const t = i / segs;
      const a = yaw + dir * arcRad * 0.5 * t;
      const dist = t * arcLen;
      pts.push(projectFn(planePos.x + Math.sin(a) * dist, altY, planePos.z + Math.cos(a) * dist));
    }
  }

  if (pts.length < 2) return false;
  ctx.save();
  ctx.globalAlpha = highlighted ? 0.92 : 0.62;
  drawTacticalPolyline(ctx, pts, style.outline, highlighted ? 3.2 : 2.2, [5, 4]);
  drawTacticalPolyline(ctx, pts, style.main, highlighted ? 1.6 : 1.2, [5, 4]);
  const pPrev = pts[pts.length - 2];
  const pEnd = pts[pts.length - 1];
  const ang = Math.atan2(pEnd.y - pPrev.y, pEnd.x - pPrev.x);
  ctx.fillStyle = style.main;
  ctx.beginPath();
  ctx.moveTo(pEnd.x + Math.cos(ang) * 3.5, pEnd.y + Math.sin(ang) * 3.5);
  ctx.lineTo(pEnd.x + Math.cos(ang + 2.5) * 4.5, pEnd.y + Math.sin(ang + 2.5) * 4.5);
  ctx.lineTo(pEnd.x + Math.cos(ang - 2.5) * 4.5, pEnd.y + Math.sin(ang - 2.5) * 4.5);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  return true;
}
