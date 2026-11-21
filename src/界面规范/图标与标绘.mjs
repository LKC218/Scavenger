/** Scavenger 全局唯一矢量图标源（12 类武器库兵种剪影 + 4 枚情景警示 + 19 枚 HUD 图标）、语义调色读取器、线框幽灵标绘与车底防叠字引擎。 */

const SVG_NS = 'http://www.w3.org/2000/svg';
let hazardMaskUid = 0;

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

/** 外围平面 HUD 线性战术图标（19 枚，24x24，统一 1.75px 线宽）。 */
export const HUD_ICONS = Object.freeze({
  move: ['M5 12h13', 'M13 6l6 6-6 6'],
  attack: ['M12 3v4', 'M12 17v4', 'M3 12h4', 'M17 12h4', 'M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0'],
  stop: ['M6 6h12v12H6Z'],
  rally: ['M6 20V5l11 4-11 4'],
  airstrike: ['M12 3l3 7h-2v6l3 2v2l-4-1-4 1v-2l3-2v-6H9Z'],
  repair: ['M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.3 2.3-2-2Z'],
  shield: ['M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6Z'],
  lock: ['M7 11V8a5 5 0 0 1 10 0v3', 'M6 11h12v9H6Z'],
  alert: ['M12 4l9 16H3Z', 'M12 10v4', 'M12 17h.01'],
  drawer: ['M4 5h16v14H4Z', 'M14 5v14'],
  focus: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5', 'M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0'],
  soundOn: ['M4 9h4l5-4v14l-5-4H4Z', 'M16.5 8.5a5 5 0 0 1 0 7', 'M19 6a8.5 8.5 0 0 1 0 12'],
  soundOff: ['M4 9h4l5-4v14l-5-4H4Z', 'M16 9l5 6', 'M21 9l-5 6'],
  pause: ['M7 5h3v14H7Z', 'M14 5h3v14h-3Z'],
  play: ['M7 5l12 7-12 7Z'],
  reset: ['M4 12a8 8 0 1 0 2.4-5.7L4 8.5', 'M4 4v4.5h4.5'],
  layers: ['M12 3l9 4.5-9 4.5-9-4.5Z', 'M3 12l9 4.5 9-4.5', 'M3 16.5l9 4.5 9-4.5'],
  compass: ['M12 2l3.5 10L12 22l-3.5-10Z', 'M8.5 12h7'],
  copy: ['M9 9h11v11H9Z', 'M4 15V4h11'],
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
  if (className) svg.setAttribute('class', className);
  for (const d of strokes) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
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
  if (mode === 'rejected') {
    return {
      outline: pal.strokeHalo,
      main: pal.danger,
      fill: pal.dangerFill,
      badgeBg: pal.bgPod,
      badgeText: pal.danger,
      alpha: 0.96,
    };
  }
  if (mode === 'pending') {
    return {
      outline: pal.strokeHalo,
      main: pal.warn,
      fill: pal.warnFill,
      badgeBg: pal.bgPod,
      badgeText: pal.warn,
      alpha: 0.88,
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
    main,
    fill,
    badgeBg: pal.bgPod,
    badgeText: main,
    alpha: mode === 'preview' ? 0.96 : (highlighted ? 0.95 : 0.48),
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

export function drawRouteChevrons(ctx, worldPoints, y, projectFn, style, spacingMeters = 13) {
  let carry = 4;
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
      ctx.strokeStyle = style.outline;
      ctx.lineWidth = 1.8;
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
