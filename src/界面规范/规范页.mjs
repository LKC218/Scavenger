/** 全局战术 UI/UX 活体规范台交互逻辑（100% 同源读取 令牌.css 与 图标与标绘.mjs）。 */
import {
  VEHICLE_SILHOUETTES,
  VEHICLE_PRIMARY_CLASSES,
  VEHICLE_META,
  HAZARD_ICONS,
  HUD_ICONS,
  createSilhouetteSvg,
  createHazardIconSvg,
  createHudIconSvg,
  readSemanticPalette,
  computeContrastRatio,
  resolveRouteStyle,
  buildClippedRoute,
  chamferWaypoints,
  drawTacticalPolyline,
  drawRouteChevrons,
  drawWireframeGhost,
  drawHighOrbitTrack,
  drawAirLockBrackets,
  drawEtaBadge,
  GroundNameplateCollision,
  cycleOverlappingUnits,
} from '/ui/图标与标绘.mjs';
import {
  bindAllUiSelects,
  createEscStack,
  createUiSizeController,
  UI_SIZE_LABELS,
} from '/ui/交互控件.mjs';

const SVG_NS = 'http://www.w3.org/2000/svg';
const TILT = (48 * Math.PI) / 180;
const SIN_TILT = Math.sin(TILT);
const COS_TILT = Math.cos(TILT);
const ORBIT_RADIUS = 40;
const OBSTACLE = { minX: -36, maxX: -10, minZ: -48, maxZ: -32, label: '机库障碍（地面禁行 · 空中可越）' };
const BOUNDS = { minX: -260, maxX: 260, minZ: -220, maxZ: 220 };
const REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const toastEl = document.getElementById('spec-toast');
let toastTimer = null;
function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2200);
}

/* ====== 顶栏徽标与同源摘要查询 ====== */
const brandSigil = document.getElementById('brand-sigil');
brandSigil.append(createHudIconSvg('focus', { size: 18 }));

let statusIdentity = null;
async function loadStatusIdentity() {
  const badge = document.getElementById('spec-sha-badge');
  const detail = document.getElementById('drawer-status-detail');
  try {
    const res = await fetch('/api/status', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    statusIdentity = await res.json();
    const shaShort = String(statusIdentity.source_sha256 || '').slice(0, 12);
    badge.textContent = `同源摘要 · ${shaShort || 'LIVE'}`;
    detail.textContent = `应用：${statusIdentity.app ?? 'ui-spec'} · 源码指纹：${statusIdentity.source_sha256 ?? '--'}`;
  } catch {
    badge.textContent = '静态同源模式';
    detail.textContent = '当前未检测到 /api/status 接口，已直接挂载本地共享源。';
  }
}

/* ====== 01 色彩语义分层矩阵（运行期 getComputedStyle 实时读取） ====== */
function renderColorMatrix() {
  const rootStyle = getComputedStyle(document.documentElement);
  const bgCanvas = rootStyle.getPropertyValue('--ui-bg-canvas').trim();
  const mount = document.getElementById('color-matrix-mount');
  mount.replaceChildren();

  const groups = [
    {
      title: '7 维零撞色全局语义通道与状态色（A3 烟炭双金 V2.0）',
      items: [
        { name: '地面载具通道', cssVar: '--ui-ch-ground', note: '坦克 / 步战 / 补给车贴地环与名牌' },
        { name: '地面高亮内环', cssVar: '--ui-ch-ground-inner', note: '地面近景内环与贴地字' },
        { name: '旋翼直升机通道', cssVar: '--ui-ch-heli', note: '30m 高度虚线与旋翼双环' },
        { name: '旋翼高亮内环', cssVar: '--ui-ch-heli-inner', note: '旋翼贴地名牌文字' },
        { name: '固定翼攻击机通道', cssVar: '--ui-ch-plane', note: '100m 盘旋圆轨与高空锚线' },
        { name: '固定翼高亮色', cssVar: '--ui-ch-plane-inner', note: '固定翼贴地名牌文字' },
        { name: '选中焦点 / 主激活', cssVar: '--ui-state-select', note: '香槟金 · 主 CTA 与选中高亮' },
        { name: '警戒 / 队列 / 补给', cssVar: '--ui-state-warn', note: '工业琥珀黄 · 警戒灯与排程待定' },
        { name: '敌对 / 非法拒收', cssVar: '--ui-state-danger', note: '猩红警报 · 尸潮红区与障碍拒收' },
        { name: '生化毒雾 / 洗消', cssVar: '--ui-state-toxic', note: '酸绿专控 · 毒雾污染与洗消指令' },
        { name: '安全 / 后勤就绪', cssVar: '--ui-state-safe', note: '翡翠绿 · 补给走廊与安全区状态' },
      ],
    },
    {
      title: '基础表面与文字阶梯（战斗 HUD 实底 60FPS / 菜单毛玻璃）',
      items: [
        { name: '深空画布底色', cssVar: '--ui-bg-canvas', note: '中性微冷烟炭底层基准' },
        { name: '战斗 HUD 实底舱', cssVar: '--ui-bg-pod-elevated', note: '高透暗实底，严禁大面积模糊' },
        { name: 'B2 精工细边框', cssVar: '--ui-line-base', note: '1px 常规按钮与舱体轮廓' },
        { name: '主阅读文字', cssVar: '--ui-text-primary', note: '主标题、呼号与核心读数' },
        { name: '次级说明文字', cssVar: '--ui-text-secondary', note: '副文本与列表说明' },
        { name: '辅助微标文字', cssVar: '--ui-text-muted', note: '英文微标与静默注释' },
      ],
    },
  ];

  for (const group of groups) {
    const h = document.createElement('h3');
    h.className = 'color-group-title';
    h.textContent = group.title;
    mount.append(h);

    const grid = document.createElement('div');
    grid.className = 'swatch-grid';
    for (const item of group.items) {
      const value = rootStyle.getPropertyValue(item.cssVar).trim();
      const ratio = computeContrastRatio(value, bgCanvas);
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'swatch-card';
      card.dataset.tokenName = item.cssVar;
      card.dataset.tokenValue = value;

      const chip = document.createElement('div');
      chip.className = 'swatch-chip';
      chip.style.background = value;
      const chipTextRatio = computeContrastRatio('#101a14', value);
      chip.style.color = (chipTextRatio && chipTextRatio >= 4.5) ? '#101a14' : '#eef3e6';
      const valSpan = document.createElement('span');
      valSpan.textContent = value;
      const ratioSpan = document.createElement('span');
      ratioSpan.textContent = ratio ? `对底 ${ratio.toFixed(1)}:1` : '复合透明度';
      chip.append(valSpan, ratioSpan);

      const meta = document.createElement('div');
      meta.className = 'swatch-meta';
      const title = document.createElement('strong');
      title.textContent = item.name;
      const varCode = document.createElement('code');
      varCode.className = 'swatch-var';
      varCode.textContent = item.cssVar;
      meta.append(title, varCode);

      const note = document.createElement('span');
      note.style.fontSize = 'var(--ui-fs-2xs)';
      note.style.color = 'var(--ui-text-muted)';
      note.textContent = item.note;

      card.append(chip, meta, note);
      card.addEventListener('click', () => {
        navigator.clipboard?.writeText(`var(${item.cssVar})`).catch(() => {});
        showToast(`已复制令牌引用：var(${item.cssVar})（${value}）`);
      });
      grid.append(card);
    }
    mount.append(grid);
  }
}

/* ====== 02 字阶标尺与等宽数字演示 ====== */
function renderTypeScale() {
  const rootStyle = getComputedStyle(document.documentElement);
  const mount = document.getElementById('type-scale-mount');
  const rows = [
    { cssVar: '--ui-fs-2xl', name: '展示巨字 · Display', sample: 'AIRBASE 全图演训态势' },
    { cssVar: '--ui-fs-xl', name: '区段主标题 · H1/H2', sample: '轻量化军事沙盘 × 贴地线框风' },
    { cssVar: '--ui-fs-lg', name: '舱体标题 · Pod Title', sample: '铁砧 01 · M1A2 主战坦克' },
    { cssVar: '--ui-fs-md', name: '标准正文 · Body', sample: '右键按住预览落点与朝向，松开后下达编队航路指令。' },
    { cssVar: '--ui-fs-sm', name: '贴地名牌 / 按钮 · Sm', sample: '游隼 02 · 悬停 30m · 72km/h' },
    { cssVar: '--ui-fs-xs', name: '战术控件 / 状态 · Xs', sample: '100 单位 · 60.0 FPS · P95 11.4ms' },
    { cssVar: '--ui-fs-2xs', name: '微标 / 键帽 · 2Xs', sample: 'CHANNEL GROUND // TABULAR-NUMS' },
  ];
  for (const r of rows) {
    const px = rootStyle.getPropertyValue(r.cssVar).trim();
    const row = document.createElement('div');
    row.className = 'type-row';
    const meta = document.createElement('div');
    meta.innerHTML = `<code class="swatch-var">${r.cssVar}</code><div style="font-size:11px;color:var(--ui-text-secondary)">${r.name}</div>`;
    const sample = document.createElement('div');
    sample.className = 'type-sample ui-tabular';
    sample.style.fontSize = `var(${r.cssVar})`;
    sample.style.fontWeight = r.cssVar === '--ui-fs-2xl' || r.cssVar === '--ui-fs-xl' ? '700' : '500';
    sample.textContent = r.sample;
    const size = document.createElement('span');
    size.className = 'ui-mono';
    size.style.fontSize = '11px';
    size.style.color = 'var(--ui-state-select)';
    size.style.textAlign = 'right';
    size.textContent = px;
    row.append(meta, sample, size);
    mount.append(row);
  }
}

let telStart = performance.now();
function tickTelemetry(now) {
  const t = (now - telStart) / 1000;
  const fps = 58.6 + Math.sin(t * 2.1) * 1.3;
  const dist = Math.round(84 + Math.sin(t * 0.9) * 28);
  const sec = Math.max(1, Math.round(dist / 11.5));
  const alt = 30.0 + Math.sin(t * 1.6) * 1.4;
  const mm = String(Math.floor(t / 60)).padStart(2, '0');
  const ss = String(Math.floor(t % 60)).padStart(2, '0');
  const ds = Math.floor((t * 10) % 10);
  document.getElementById('tel-fps').textContent = `${fps.toFixed(1)} FPS`;
  document.getElementById('tel-eta').textContent = `${String(dist).padStart(3, '0')}m · ${String(sec).padStart(2, '0')}s`;
  document.getElementById('tel-alt').textContent = `${alt.toFixed(1).padStart(5, '0')} m`;
  document.getElementById('tel-clock').textContent = `${mm}:${ss}.${ds}`;
}

/* ====== 03 武器库 12 兵种剪影、4 枚情景警示与 19 枚 HUD 图标展区 ====== */
function renderIconShowcase() {
  const pal = readSemanticPalette();
  const mount = document.getElementById('silhouette-mount');
  const channelColor = {
    ground: pal.ground,
    heli: pal.heli,
    plane: pal.plane,
  };
  const aliasTag = { mbt: 'tank', attack_heli: 'heli', cas: 'plane' };
  for (const kind of VEHICLE_PRIMARY_CLASSES) {
    const meta = VEHICLE_META[kind];
    const card = document.createElement('div');
    card.className = 'silhouette-card';
    const head = document.createElement('div');
    head.className = 'swatch-meta';
    const aliasText = aliasTag[kind] ? ` / ${aliasTag[kind]}` : '';
    head.innerHTML = `<strong>${meta.label}（${kind}${aliasText}）</strong><span class="ui-badge" data-tone="${meta.channel}">evenodd 负空间</span>`;

    const stage = document.createElement('div');
    stage.className = 'silhouette-stage';
    stage.style.color = channelColor[meta.channel];
    for (const px of [12, 15, 24, 48]) {
      const wrap = document.createElement('div');
      wrap.className = 'size-specimen';
      const svg = createSilhouetteSvg(kind, { size: px });
      if (aliasTag[kind]) svg.dataset.silhouetteAlias = aliasTag[kind];
      wrap.append(svg);
      const lbl = document.createElement('span');
      lbl.textContent = `${px}px`;
      wrap.append(lbl);
      stage.append(wrap);
    }

    const note = document.createElement('span');
    note.style.fontSize = 'var(--ui-fs-2xs)';
    note.style.color = 'var(--ui-text-muted)';
    note.textContent = `${meta.models} · 贴地环 ${meta.ringRadius}m · 通道 ${meta.channel}`;
    card.append(head, stage, note);
    mount.append(card);
  }

  const hazardMount = document.getElementById('hazard-icon-mount');
  const toneColor = { warn: pal.warn, select: pal.select, toxic: pal.toxic, danger: pal.danger };
  for (const [key, cfg] of Object.entries(HAZARD_ICONS)) {
    const card = document.createElement('div');
    card.className = 'hazard-card';
    const ico = createHazardIconSvg(key, { size: 30 });
    ico.style.color = toneColor[cfg.tone] || pal.select;
    ico.style.flex = '0 0 auto';
    const info = document.createElement('div');
    info.innerHTML = `<strong style="display:block;font-size:12.5px">${cfg.label} <code class="swatch-var">${key}</code></strong><span style="font-size:11px;color:var(--ui-text-muted)">${cfg.note}</span>`;
    card.append(ico, info);
    hazardMount.append(card);
  }

  document.getElementById('icon-bg-seg').addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    document.querySelectorAll('#icon-bg-seg button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.silhouette-stage').forEach(el => {
      el.dataset.bgSwatch = btn.dataset.bg;
    });
  });

  const hudMount = document.getElementById('hud-icon-mount');
  for (const name of Object.keys(HUD_ICONS)) {
    const item = document.createElement('div');
    item.className = 'hud-icon-item';
    item.append(createHudIconSvg(name, { size: 16 }));
    const label = document.createElement('code');
    label.className = 'swatch-var';
    label.textContent = name;
    item.append(label);
    hudMount.append(item);
  }
}

/* ====== 04 外围平面 HUD 编队卡示例与抽屉联动 ====== */
function renderUnitCardDemo() {
  const mount = document.getElementById('unit-card-mount');
  const demos = [
    { name: '铁砧 01', kind: 'tank', state: '移动中 · 队列 2', active: true },
    { name: '驼鹿 01', kind: 'supply', state: '后勤待命 · 满弹药', active: false },
    { name: '游隼 02', kind: 'heli', state: '30m 巡航飞行', active: true },
    { name: '雷霆 01', kind: 'plane', state: '100m 盘旋监视', active: false },
  ];
  for (const d of demos) {
    const meta = VEHICLE_META[d.kind];
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `ui-unit-card${d.active ? ' active' : ''}`;
    btn.dataset.channel = meta.channel;
    const svg = createSilhouetteSvg(d.kind, { size: 16, className: 'ui-unit-icon' });
    const title = document.createElement('strong');
    title.textContent = d.name;
    const sub = document.createElement('small');
    sub.textContent = d.state;
    const bar = document.createElement('i');
    bar.className = 'ready';
    bar.style.width = d.active ? '100%' : '0%';
    btn.append(svg, title, sub, bar);
    btn.addEventListener('click', () => {
      btn.classList.toggle('active');
      bar.style.width = btn.classList.contains('active') ? '100%' : '0%';
    });
    mount.append(btn);
  }
}

const drawerEl = document.getElementById('debug-drawer');
const drawerBackdrop = document.getElementById('drawer-backdrop');
const drawerBtn = document.getElementById('btn-toggle-drawer');

/* ====== 共享 LIFO ESC 栈 ＋ 定制战术下拉组件 ====== */
const escStack = createEscStack();
let selectGroupCtrl = null;
function initUiSelects() {
  selectGroupCtrl = bindAllUiSelects(document, { escStack, dropupThreshold: 190 });
  document.getElementById('spec-roe-select')?.addEventListener('ui-select-change', e => {
    showToast(`交战规则已切换：${e.detail.label}`);
  });
  document.getElementById('scale-roe-select')?.addEventListener('ui-select-change', e => {
    showToast(`07 实操台交战规则：${e.detail.label}`);
  });
}

function setDrawerOpen(open) {
  if (!open) selectGroupCtrl?.closeAll();
  drawerEl.hidden = !open;
  drawerBackdrop.hidden = !open;
  drawerBtn.setAttribute('aria-expanded', String(open));
}

window.addEventListener('keydown', e => { escStack.handleKeydown(e); });

drawerBtn.addEventListener('click', () => setDrawerOpen(drawerEl.hidden));
document.getElementById('btn-demo-drawer-inline').addEventListener('click', () => setDrawerOpen(true));
document.getElementById('drawer-close').addEventListener('click', () => setDrawerOpen(false));
drawerBackdrop.addEventListener('click', () => setDrawerOpen(false));
document.getElementById('drawer-copy-sha').addEventListener('click', () => {
  const sha = statusIdentity?.source_sha256 || '未连接 /api/status';
  navigator.clipboard?.writeText(sha).catch(() => {});
  showToast(`源码指纹：${sha}`);
});

/* ====== 05 世界内贴地 UI、线框幽灵与双层分离高清沙盒 ====== */
function initSandbox() {
  const stage = document.getElementById('sb-stage');
  const groundCanvas = document.getElementById('sb-ground-canvas');
  const vehicleCanvas = document.getElementById('sb-vehicle-canvas');
  const airCanvas = document.getElementById('sb-air-canvas');
  const ringSvg = document.getElementById('sb-ring-svg');
  const uiLayer = document.getElementById('sb-ui-layer');
  const dragBoxEl = document.getElementById('sb-drag-box');
  const statusLine = document.getElementById('sb-status-line');
  const spanBadge = document.getElementById('sb-span-badge');
  const dprBadge = document.getElementById('sb-dpr-badge');
  const pixelBtn = document.getElementById('sandbox-pixel-toggle');
  const gCtx = groundCanvas.getContext('2d');
  const vCtx = vehicleCanvas.getContext('2d');
  const aCtx = airCanvas.getContext('2d');

  const prefs = { layer: 'all', bg: 'split', pixelSim: false };
  const cam = { cx: 2, cz: 4, span: 220, targetSpan: 220, angle: 0, targetAngle: 0, anim: null };
  const keys = new Set();
  const selected = new Set(['anvil-1', 'falcon-2', 'thunder-1']);
  let hoveredId = null;
  let dragSelect = null;
  let rightDrag = null;
  let panDrag = null;
  let lastCommandText = '';
  const domCache = new Map();

  function initialUnits() {
    return [
      {
        id: 'anvil-1', name: '铁砧 01', short: '砧01', kind: 'tank', layer: 'ground', role: 'M1A2 主战坦克',
        x: -36, z: -12, y: 0, yaw: 1.25, turretYaw: 1.25, speed: 9, maxSpeed: 11.5,
        demoLoop: [{ x: -38, z: -12 }, { x: 18, z: -18, facing: 0.25 }, { x: 34, z: -2, facing: 1.3 }],
        queue: [{ x: 18, z: -18, facing: 0.25 }, { x: 34, z: -2, facing: 1.3 }], moveMode: 'demo', color: '#434d3d',
      },
      { id: 'anvil-2', name: '铁砧 02', short: '砧02', kind: 'tank', layer: 'ground', role: 'M1A2 主战坦克', x: 16, z: 28, y: 0, yaw: -0.12, turretYaw: -0.08, speed: 0, maxSpeed: 11.5, queue: [], moveMode: 'idle', color: '#434d3d' },
      { id: 'anvil-3', name: '铁砧 03', short: '砧03', kind: 'tank', layer: 'ground', role: 'M1A2 主战坦克', x: 29, z: 28, y: 0, yaw: 0, turretYaw: 0, speed: 0, maxSpeed: 11.5, queue: [], moveMode: 'idle', color: '#434d3d' },
      { id: 'moose-1', name: '驼鹿 01', short: '鹿01', kind: 'supply', layer: 'ground', role: 'M548 弹药补给车', x: -32, z: 22, y: 0, yaw: 0.35, turretYaw: 0.35, speed: 0, maxSpeed: 9.5, queue: [], moveMode: 'idle', color: '#565f4a' },
      { id: 'falcon-1', name: '游隼 01', short: '隼01', kind: 'heli', layer: 'air', role: 'AH-64D 阿帕奇', x: -18, z: 4, y: 30, baseAlt: 30, yaw: 0.45, speed: 0, maxSpeed: 26, bank: 0, queue: [], moveMode: 'idle', color: '#3e483d' },
      {
        id: 'falcon-2', name: '游隼 02', short: '隼02', kind: 'heli', layer: 'air', role: 'AH-64D 阿帕奇',
        x: 44, z: 4, y: 30, baseAlt: 30, yaw: -1.2, speed: 20, maxSpeed: 26, bank: 0,
        demoLoop: [{ x: 44, z: 4 }, { x: -8, z: -24, facing: -0.6 }, { x: 12, z: 12, facing: 0.4 }],
        queue: [{ x: -8, z: -24, facing: -0.6 }], moveMode: 'demo', color: '#3e483d',
      },
      {
        id: 'thunder-1', name: '雷霆 01', short: '雷01', kind: 'plane', layer: 'air', role: 'A-10C 疣猪',
        x: 40, z: -34, y: 100, baseAlt: 100, yaw: 0, speed: 46, maxSpeed: 48, bank: 0.34,
        orbit: { x: 0, z: -34, r: ORBIT_RADIUS, phase: 0 }, queue: [], moveMode: 'orbit', color: '#555d5b',
      },
    ];
  }
  let units = initialUnits();

  function resize() {
    const w = Math.max(1, stage.clientWidth);
    const h = Math.max(1, stage.clientHeight);
    const nativeDpr = Math.min(window.devicePixelRatio || 1, 2);
    const lowDpr = prefs.pixelSim ? (640 / w) : nativeDpr;
    for (const c of [groundCanvas, vehicleCanvas]) {
      c.width = Math.max(1, Math.round(w * lowDpr));
      c.height = Math.max(1, Math.round(h * lowDpr));
    }
    airCanvas.width = Math.max(1, Math.round(w * nativeDpr));
    airCanvas.height = Math.max(1, Math.round(h * nativeDpr));
    ringSvg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    dprBadge.textContent = prefs.pixelSim
      ? `3D 640px 模拟 ｜ UI 原生 ${nativeDpr}x`
      : `双层原生 ${nativeDpr}x 高清`;
    document.getElementById('sb-m-3d').textContent = `${groundCanvas.width} × ${groundCanvas.height}`;
    document.getElementById('sb-m-ui').textContent = `${airCanvas.width} × ${airCanvas.height}`;
  }
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(stage);

  function worldToScreen(wx, wy, wz) {
    const w = stage.clientWidth;
    const h = stage.clientHeight;
    const ppm = h / cam.span;
    const dx = wx - cam.cx;
    const dz = wz - cam.cz;
    const c = Math.cos(cam.angle);
    const s = Math.sin(cam.angle);
    const rx = dx * c - dz * s;
    const rz = dx * s + dz * c;
    return { x: w * 0.5 + rx * ppm, y: h * 0.5 + rz * SIN_TILT * ppm - wy * COS_TILT * ppm, ppm };
  }

  function screenToWorld(sx, sy) {
    const w = stage.clientWidth;
    const h = stage.clientHeight;
    const ppm = h / cam.span;
    const nx = (sx - w * 0.5) / ppm;
    const nz = (sy - h * 0.5) / (ppm * SIN_TILT);
    const c = Math.cos(cam.angle);
    const s = Math.sin(cam.angle);
    return { x: cam.cx + nx * c + nz * s, z: cam.cz - nx * s + nz * c };
  }

  function zoomLevel() {
    if (cam.span <= 135) return 'near';
    if (cam.span <= 340) return 'mid';
    return 'far';
  }

  function unitAllowed(u) {
    return prefs.layer === 'all' || u.layer === prefs.layer;
  }

  function checkPointReachable(u, x, z) {
    if (x < BOUNDS.minX || x > BOUNDS.maxX || z < BOUNDS.minZ || z > BOUNDS.maxZ) {
      return { valid: false, reason: '超出地图边界' };
    }
    if (u.layer === 'ground' && x >= OBSTACLE.minX - 2 && x <= OBSTACLE.maxX + 2 && z >= OBSTACLE.minZ - 2 && z <= OBSTACLE.maxZ + 2) {
      return { valid: false, reason: '地面目的地被静态障碍占用' };
    }
    return { valid: true, reason: '' };
  }

  function ensureDom(u) {
    if (domCache.has(u.id)) return domCache.get(u.id);
    const ringGroup = document.createElementNS(SVG_NS, 'g');
    ringGroup.setAttribute('class', 'chassis-ring-group');
    ringGroup.dataset.unitId = u.id;
    const ringOuter = document.createElementNS(SVG_NS, 'ellipse');
    ringOuter.setAttribute('class', 'chassis-ring-outer');
    const ringInner = document.createElementNS(SVG_NS, 'ellipse');
    ringInner.setAttribute('class', 'chassis-ring-inner');
    ringGroup.append(ringOuter, ringInner);
    ringSvg.append(ringGroup);

    const groundMark = document.createElement('div');
    groundMark.className = `ground-mark kind-${u.kind}`;
    groundMark.dataset.unitId = u.id;
    groundMark.append(createSilhouetteSvg(u.kind));
    const gmName = document.createElement('span');
    gmName.className = 'mark-name';
    const gmSub = document.createElement('span');
    gmSub.className = 'mark-sub';
    groundMark.append(gmName, gmSub);

    const farDot = document.createElement('div');
    farDot.className = `far-dot kind-${u.kind}`;
    farDot.dataset.unitId = u.id;
    farDot.append(createSilhouetteSvg(u.kind));

    uiLayer.append(groundMark, farDot);
    const entry = { ringGroup, ringOuter, ringInner, groundMark, gmName, gmSub, farDot, activeEl: null };
    domCache.set(u.id, entry);
    return entry;
  }

  function drawGround(pal) {
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    const scale = groundCanvas.width / sw;
    gCtx.save();
    gCtx.setTransform(scale, 0, 0, scale, 0, 0);

    if (prefs.bg === 'paper') { gCtx.fillStyle = '#f5efe2'; gCtx.fillRect(0, 0, sw, sh); }
    else if (prefs.bg === 'concrete') { gCtx.fillStyle = '#b9b8ae'; gCtx.fillRect(0, 0, sw, sh); }
    else if (prefs.bg === 'grass') { gCtx.fillStyle = '#536247'; gCtx.fillRect(0, 0, sw, sh); }
    else {
      const pTop = worldToScreen(4, 0, -500);
      const pBot = worldToScreen(4, 0, 500);
      gCtx.fillStyle = '#526146';
      gCtx.fillRect(0, 0, sw, sh);
      const far = 4000;
      const dx = pBot.x - pTop.x;
      const dy = pBot.y - pTop.y;
      const len = Math.hypot(dx, dy) || 1;
      const ux = dx / len;
      const uy = dy / len;
      const vx = -uy;
      const vy = ux;
      const side = (worldToScreen(-50, 0, 0).x - pTop.x) * vx + (worldToScreen(-50, 0, 0).y - pTop.y) * vy >= 0 ? 1 : -1;
      gCtx.beginPath();
      gCtx.moveTo(pTop.x - ux * far, pTop.y - uy * far);
      gCtx.lineTo(pTop.x + ux * far, pTop.y + uy * far);
      gCtx.lineTo(pTop.x + ux * far + vx * far * side, pTop.y + uy * far + vy * far * side);
      gCtx.lineTo(pTop.x - ux * far + vx * far * side, pTop.y - uy * far + vy * far * side);
      gCtx.closePath();
      gCtx.fillStyle = '#b9b8ae';
      gCtx.fill();
    }

    const step = cam.span > 340 ? 40 : 12;
    const half = Math.ceil((cam.span * 1.4) / step) * step;
    const gx0 = Math.floor((cam.cx - half) / step) * step;
    const gx1 = Math.ceil((cam.cx + half) / step) * step;
    const gz0 = Math.floor((cam.cz - half) / step) * step;
    const gz1 = Math.ceil((cam.cz + half) / step) * step;
    gCtx.strokeStyle = prefs.bg === 'paper' ? 'rgba(130,115,92,.18)' : 'rgba(255,255,255,.09)';
    gCtx.lineWidth = 1;
    gCtx.beginPath();
    for (let x = gx0; x <= gx1; x += step) {
      const p0 = worldToScreen(x, 0, gz0);
      const p1 = worldToScreen(x, 0, gz1);
      gCtx.moveTo(p0.x, p0.y);
      gCtx.lineTo(p1.x, p1.y);
    }
    for (let z = gz0; z <= gz1; z += step) {
      const p0 = worldToScreen(gx0, 0, z);
      const p1 = worldToScreen(gx1, 0, z);
      gCtx.moveTo(p0.x, p0.y);
      gCtx.lineTo(p1.x, p1.y);
    }
    gCtx.stroke();

    // 机库障碍区
    const obPts = [
      worldToScreen(OBSTACLE.minX, 0, OBSTACLE.minZ),
      worldToScreen(OBSTACLE.maxX, 0, OBSTACLE.minZ),
      worldToScreen(OBSTACLE.maxX, 0, OBSTACLE.maxZ),
      worldToScreen(OBSTACLE.minX, 0, OBSTACLE.maxZ),
    ];
    gCtx.fillStyle = pal.dangerFill;
    gCtx.strokeStyle = pal.danger;
    gCtx.lineWidth = 1.6;
    gCtx.setLineDash([6, 4]);
    gCtx.beginPath();
    obPts.forEach((p, i) => (i ? gCtx.lineTo(p.x, p.y) : gCtx.moveTo(p.x, p.y)));
    gCtx.closePath();
    gCtx.fill();
    gCtx.stroke();
    gCtx.setLineDash([]);
    const cPt = worldToScreen((OBSTACLE.minX + OBSTACLE.maxX) * 0.5, 0, (OBSTACLE.minZ + OBSTACLE.maxZ) * 0.5);
    gCtx.fillStyle = prefs.bg === 'paper' ? '#b42318' : pal.danger;
    gCtx.font = '600 10.5px "Microsoft YaHei UI",sans-serif';
    gCtx.textAlign = 'center';
    gCtx.textBaseline = 'middle';
    gCtx.fillText(OBSTACLE.label, cPt.x, cPt.y);

    // 空中单位地面软阴影
    for (const u of units) {
      if (u.layer !== 'air') continue;
      const sx = u.x + u.y * 0.42;
      const sz = u.z + u.y * 0.28;
      const sp = worldToScreen(sx, 0.05, sz);
      gCtx.fillStyle = `rgba(10,16,12,${Math.max(0.10, 0.28 - u.y * 0.0014)})`;
      const r = (u.kind === 'heli' ? 5.4 : 6.4) * sp.ppm;
      gCtx.beginPath();
      gCtx.ellipse(sp.x, sp.y, r, r * SIN_TILT, 0, 0, Math.PI * 2);
      gCtx.fill();
    }
    gCtx.restore();
  }

  function drawGroundVehicles() {
    const sw = stage.clientWidth;
    const scale = vehicleCanvas.width / sw;
    vCtx.clearRect(0, 0, vehicleCanvas.width, vehicleCanvas.height);
    vCtx.save();
    vCtx.setTransform(scale, 0, 0, scale, 0, 0);
    const groundUnits = units.filter(u => u.layer === 'ground').sort((a, b) => worldToScreen(a.x, 0, a.z).y - worldToScreen(b.x, 0, b.z).y);
    for (const u of groundUnits) {
      const s = worldToScreen(u.x, 0, u.z);
      const isTank = u.kind === 'tank';
      const hw = isTank ? 1.85 : 1.45;
      const hl = isTank ? 3.4 : 3.0;
      const bh = isTank ? 1.45 : 2.15;
      vCtx.save();
      if (!unitAllowed(u)) vCtx.globalAlpha = 0.35;
      const pLoc = (lx, ly, lz, yaw = u.yaw) => worldToScreen(
        u.x + lx * Math.cos(yaw) - lz * Math.sin(yaw),
        ly,
        u.z + lx * Math.sin(yaw) + lz * Math.cos(yaw),
      );
      const top = [[-hw, -hl], [hw, -hl], [hw, hl], [-hw, hl]].map(([x, z]) => pLoc(x, bh, z));
      vCtx.fillStyle = u.color;
      vCtx.strokeStyle = '#1b221a';
      vCtx.lineWidth = 1.2;
      vCtx.beginPath();
      top.forEach((p, i) => (i ? vCtx.lineTo(p.x, p.y) : vCtx.moveTo(p.x, p.y)));
      vCtx.closePath();
      vCtx.fill();
      vCtx.stroke();
      if (isTank) {
        const tur = [[-1.15, -1.25], [1.15, -1.25], [1.05, 1.5], [-1.05, 1.5]].map(([x, z]) => pLoc(x, bh + 0.78, z, u.turretYaw));
        vCtx.fillStyle = '#55614d';
        vCtx.beginPath();
        tur.forEach((p, i) => (i ? vCtx.lineTo(p.x, p.y) : vCtx.moveTo(p.x, p.y)));
        vCtx.closePath();
        vCtx.fill();
        vCtx.stroke();
        const g0 = pLoc(0, bh + 0.55, -1.1, u.turretYaw);
        const g1 = pLoc(0, bh + 0.62, -5.2, u.turretYaw);
        vCtx.strokeStyle = '#6c7962';
        vCtx.lineWidth = Math.max(1.4, 0.2 * s.ppm);
        vCtx.beginPath();
        vCtx.moveTo(g0.x, g0.y);
        vCtx.lineTo(g1.x, g1.y);
        vCtx.stroke();
      }
      vCtx.restore();
    }
    vCtx.restore();
  }

  /* 原生 DPR 高清战术标绘层（绘制线框幽灵航路、垂直高度线、100m 盘旋轨、空中实体与四角锁定框） */
  function drawTacticalOverlay(now, pal) {
    const sw = stage.clientWidth;
    const scale = airCanvas.width / sw;
    aCtx.clearRect(0, 0, airCanvas.width, airCanvas.height);
    aCtx.save();
    aCtx.setTransform(scale, 0, 0, scale, 0, 0);

    const drawRouteAndGhost = (u, waypoints, mode, highlighted, valid = true) => {
      const style = resolveRouteStyle(u.kind, valid ? mode : 'rejected', highlighted, pal);
      aCtx.save();
      aCtx.globalAlpha = style.alpha;
      if (u.kind === 'plane' && mode === 'active' && waypoints.length === 0 && u.orbit) {
        drawHighOrbitTrack(aCtx, u.orbit, u.baseAlt, u.orbit.r, worldToScreen, style, highlighted, highlighted);
        if (highlighted) {
          const gp = worldToScreen(u.orbit.x, 0, u.orbit.z);
          const ap = worldToScreen(u.orbit.x, u.baseAlt, u.orbit.z);
          drawTacticalPolyline(aCtx, [gp, ap], style.main, 1.4, [4, 4]);
          drawEtaBadge(aCtx, ap, 2 * Math.PI * u.orbit.r, u.maxSpeed, style, true, '', ' · 盘旋');
        }
        aCtx.restore();
        return;
      }
      const routeR = VEHICLE_META[u.kind]?.routeOffset ?? 5.1;
      const clipped = buildClippedRoute(u, routeR, waypoints);
      if (!clipped) { aCtx.restore(); return; }
      const chamfered = chamferWaypoints(clipped.points, 3.2);
      const groundPts = chamfered.map(p => worldToScreen(p.x, 0.08, p.z));
      drawTacticalPolyline(aCtx, groundPts, style.outline, highlighted ? 5.2 : 3.6);
      drawTacticalPolyline(aCtx, groundPts, style.main, highlighted ? 2.1 : 1.4, highlighted ? [] : [8, 6]);
      if (highlighted) drawRouteChevrons(aCtx, chamfered, 0.09, worldToScreen, style, 13);

      const last = waypoints[waypoints.length - 1];
      const prev = clipped.points[clipped.points.length - 2];
      const facing = Number.isFinite(last.facing) ? last.facing : Math.atan2(last.x - prev.x, -(last.z - prev.z));

      if (u.layer === 'ground') {
        drawWireframeGhost(aCtx, u.kind, last, 0.10, facing, worldToScreen, SIN_TILT, style, highlighted);
        if (highlighted || !valid) drawEtaBadge(aCtx, worldToScreen(last.x, 0.12, last.z), clipped.totalDist, u.maxSpeed, style, valid, '');
      } else {
        const gp = worldToScreen(last.x, 0.08, last.z);
        const ap = worldToScreen(last.x, u.baseAlt, last.z);
        drawTacticalPolyline(aCtx, [gp, ap], style.main, highlighted ? 1.8 : 1.2, [4, 4]);
        if (u.kind === 'heli') {
          drawWireframeGhost(aCtx, 'heli', last, u.baseAlt, facing, worldToScreen, SIN_TILT, style, highlighted);
          if (highlighted) drawEtaBadge(aCtx, ap, clipped.totalDist, u.maxSpeed, style, true, '', ' · 30m悬停');
        } else {
          // 固定翼只在高空 100m 绘制盘旋圆轨，地面只画十字锚点
          aCtx.strokeStyle = style.main;
          aCtx.lineWidth = 1.6;
          aCtx.beginPath();
          aCtx.moveTo(gp.x - 6, gp.y); aCtx.lineTo(gp.x + 6, gp.y);
          aCtx.moveTo(gp.x, gp.y - 4.5); aCtx.lineTo(gp.x, gp.y + 4.5);
          aCtx.stroke();
          drawHighOrbitTrack(aCtx, last, u.baseAlt, ORBIT_RADIUS, worldToScreen, style, highlighted, highlighted);
          drawWireframeGhost(aCtx, 'plane', { x: last.x + ORBIT_RADIUS, z: last.z }, u.baseAlt, 0, worldToScreen, SIN_TILT, style, highlighted);
          if (highlighted) drawEtaBadge(aCtx, ap, clipped.totalDist, u.maxSpeed, style, true, '', ' · 100m盘旋');
        }
      }
      aCtx.restore();
    };

    for (const u of units) {
      if (selected.has(u.id)) drawRouteAndGhost(u, u.queue, 'active', hoveredId === u.id, true);
    }
    if (rightDrag && rightDrag.targets.length > 0) {
      for (const t of rightDrag.targets) {
        const basePts = rightDrag.append ? [...t.unit.queue, { x: t.x, z: t.z, facing: t.facing }] : [{ x: t.x, z: t.z, facing: t.facing }];
        drawRouteAndGhost(t.unit, basePts, 'preview', true, t.valid);
        if (!t.valid) {
          const clipped = buildClippedRoute(t.unit, 5.1, basePts);
          if (clipped) {
            drawEtaBadge(aCtx, worldToScreen(t.x, 0.12, t.z), clipped.totalDist, t.unit.maxSpeed, resolveRouteStyle(t.unit.kind, 'rejected', true, pal), false, t.reason);
          }
        }
      }
    }

    // 空中单位垂直高度虚线 + 地面十字锚点 + 高空机身与选中四角框
    const airUnits = units.filter(u => u.layer === 'air').sort((a, b) => a.y - b.y);
    for (const u of airUnits) {
      const allowed = unitAllowed(u);
      const isSel = selected.has(u.id) && allowed;
      const isHov = hoveredId === u.id && allowed;
      const isActive = isSel || isHov;
      const gp = worldToScreen(u.x, 0, u.z);
      const ap = worldToScreen(u.x, u.y, u.z);
      const lineStyle = resolveRouteStyle(u.kind, 'active', isActive, pal);

      aCtx.save();
      aCtx.globalAlpha = !allowed ? 0.16 : (isActive ? 0.96 : 0.48);
      aCtx.strokeStyle = isActive ? pal.select : lineStyle.main;
      aCtx.lineWidth = isActive ? 2.0 : 1.2;
      aCtx.setLineDash([4, 4]);
      aCtx.beginPath();
      aCtx.moveTo(gp.x, gp.y);
      aCtx.lineTo(ap.x, ap.y);
      aCtx.stroke();
      aCtx.setLineDash([]);
      aCtx.beginPath();
      aCtx.moveTo(gp.x - 4.5, gp.y); aCtx.lineTo(gp.x + 4.5, gp.y);
      aCtx.moveTo(gp.x, gp.y - 3.5); aCtx.lineTo(gp.x, gp.y + 3.5);
      aCtx.stroke();
      aCtx.restore();

      aCtx.save();
      if (!allowed) aCtx.globalAlpha = 0.32;
      if (isActive) {
        aCtx.shadowColor = pal.select;
        aCtx.shadowBlur = isSel ? 12 : 8;
      }
      const pLoc = (lx, ly, lz) => {
        const c = Math.cos(u.yaw);
        const s = Math.sin(u.yaw);
        const bx = lx * Math.cos(u.bank || 0);
        const by = ly - lx * Math.sin(u.bank || 0);
        return worldToScreen(u.x + bx * c - lz * s, u.y + by, u.z + bx * s + lz * c);
      };
      let boundPts = [];
      if (u.kind === 'heli') {
        const tail = [pLoc(-0.25, 0.2, 1.2), pLoc(0.25, 0.2, 1.2), pLoc(0.14, 0.3, 6.2), pLoc(-0.14, 0.3, 6.2)];
        const body = [pLoc(0, 0.4, -4.4), pLoc(0.9, 0.5, -2.0), pLoc(0.8, 0.6, 1.6), pLoc(-0.8, 0.6, 1.6), pLoc(-0.9, 0.5, -2.0)];
        aCtx.fillStyle = u.color;
        aCtx.strokeStyle = '#1b221a';
        aCtx.lineWidth = 1.1;
        for (const poly of [tail, body]) {
          aCtx.beginPath();
          poly.forEach((p, i) => (i ? aCtx.lineTo(p.x, p.y) : aCtx.moveTo(p.x, p.y)));
          aCtx.closePath();
          aCtx.fill();
          aCtx.stroke();
        }
        const hub = pLoc(0, 1.35, -0.3);
        const rDisk = 6.3 * hub.ppm;
        aCtx.shadowBlur = 0;
        const spin = REDUCED_MOTION ? 0.5 : now * 0.015;
        aCtx.strokeStyle = 'rgba(22,28,22,.82)';
        aCtx.lineWidth = Math.max(1.5, 0.24 * hub.ppm);
        for (let b = 0; b < 4; b++) {
          const ang = spin + (b * Math.PI) / 2;
          const tip = pLoc(Math.cos(ang) * 6.2, 1.35, -0.3 + Math.sin(ang) * 6.2);
          aCtx.beginPath();
          aCtx.moveTo(hub.x, hub.y);
          aCtx.lineTo(tip.x, tip.y);
          aCtx.stroke();
        }
        boundPts = [...tail, ...body, { x: hub.x - rDisk, y: hub.y - rDisk * SIN_TILT }, { x: hub.x + rDisk, y: hub.y + rDisk * SIN_TILT }];
      } else {
        const wing = [pLoc(-9.0, 0, -1.5), pLoc(9.0, 0, -1.5), pLoc(8.6, 0, 1.4), pLoc(-8.6, 0, 1.4)];
        const fuse = [pLoc(0, 0.3, -7.8), pLoc(0.95, 0.4, -3.5), pLoc(0.85, 0.4, 5.8), pLoc(-0.85, 0.4, 5.8), pLoc(-0.95, 0.4, -3.5)];
        aCtx.fillStyle = '#6a7270';
        aCtx.strokeStyle = '#222826';
        aCtx.lineWidth = 1.2;
        for (const poly of [wing, fuse]) {
          aCtx.beginPath();
          poly.forEach((p, i) => (i ? aCtx.lineTo(p.x, p.y) : aCtx.moveTo(p.x, p.y)));
          aCtx.closePath();
          aCtx.fill();
          aCtx.stroke();
        }
        boundPts = [...wing, ...fuse];
      }

      if (isActive && boundPts.length) {
        aCtx.shadowBlur = 0;
        drawAirLockBrackets(aCtx, boundPts, { outline: pal.strokeHalo, main: pal.select }, isSel);
      }
      aCtx.restore();
    }
    aCtx.restore();
  }

  function syncUI(pal) {
    const lv = zoomLevel();
    const label = lv === 'near' ? '近景' : lv === 'mid' ? '中景' : '远景';
    spanBadge.textContent = `${label} ${Math.round(cam.span)}m`;
    document.querySelectorAll('#sb-zoom-seg button').forEach(b => {
      const s = Number(b.dataset.span);
      b.classList.toggle('active', (lv === 'near' && s === 85) || (lv === 'mid' && s === 220) || (lv === 'far' && s === 540));
    });

    const collisionItems = [];
    for (const u of units) {
      const d = ensureDom(u);
      const gp = worldToScreen(u.x, 0, u.z);
      const allowed = unitAllowed(u);
      const isSel = selected.has(u.id) && allowed;
      const isHov = hoveredId === u.id && allowed;
      const isActive = isSel || isHov;
      const meta = VEHICLE_META[u.kind];

      const outerCol = isSel ? pal.select : meta.channel === 'ground' ? pal.ground : meta.channel === 'heli' ? pal.heli : pal.plane;
      const innerCol = isSel ? pal.selectInner : meta.channel === 'ground' ? pal.groundInner : meta.channel === 'heli' ? pal.heliInner : pal.planeInner;
      const rxOuter = Math.max(14, meta.ringRadius * gp.ppm);
      const ryOuter = rxOuter * SIN_TILT;
      const rxInner = rxOuter * 0.77;
      const ryInner = rxInner * SIN_TILT;
      // 固定翼严格移除地面双环
      const showRing = u.kind !== 'plane' && (isActive || lv === 'near' || u.kind === 'heli');

      d.ringGroup.classList.toggle('is-selected', isSel);
      d.ringGroup.classList.toggle('is-layer-muted', !allowed);
      d.ringGroup.style.display = showRing ? '' : 'none';
      if (showRing) {
        d.ringOuter.setAttribute('cx', gp.x); d.ringOuter.setAttribute('cy', gp.y);
        d.ringOuter.setAttribute('rx', rxOuter); d.ringOuter.setAttribute('ry', ryOuter);
        d.ringOuter.setAttribute('stroke', outerCol);
        d.ringOuter.setAttribute('stroke-width', isActive ? '2.8' : '1.6');
        d.ringOuter.setAttribute('opacity', isActive ? '.96' : '.42');

        d.ringInner.setAttribute('cx', gp.x); d.ringInner.setAttribute('cy', gp.y);
        d.ringInner.setAttribute('rx', rxInner); d.ringInner.setAttribute('ry', ryInner);
        d.ringInner.setAttribute('stroke', innerCol);
        d.ringInner.setAttribute('stroke-width', isActive ? '1.5' : '1.0');
        d.ringInner.setAttribute('opacity', isActive ? '.86' : '.28');
      }

      d.groundMark.style.display = 'none';
      d.farDot.style.display = 'none';
      d.groundMark.classList.remove('is-dimmed', 'is-top', 'is-selected', 'is-hovered', 'is-compact', 'is-layer-muted');
      d.farDot.classList.remove('is-dimmed', 'is-top', 'is-active', 'is-layer-muted');

      const altTag = u.layer === 'air' ? ` · ${Math.round(u.y)}m` : '';
      const stateLabel = u.kind === 'plane' ? (u.queue.length ? '飞向航点' : '盘旋')
        : u.queue.length > 0 ? (u.queue.length > 1 ? `队列×${u.queue.length}` : '移动中')
          : u.layer === 'air' ? '悬停' : '待命';
      const kmh = Math.round(u.speed * 3.6);

      if (lv === 'far' && !isActive) {
        d.farDot.style.display = 'flex';
        d.farDot.style.transform = `translate3d(${gp.x}px,${gp.y}px,0) translate(-50%,-50%)`;
        d.farDot.classList.toggle('is-layer-muted', !allowed);
        d.activeEl = d.farDot;
      } else {
        d.gmName.textContent = (lv === 'mid' && !isActive) ? u.short : u.name;
        d.gmSub.textContent = isActive ? `· ${stateLabel}${altTag} ${kmh}km/h` : (u.layer === 'air' && lv === 'near' ? altTag : '');
        d.gmSub.style.display = d.gmSub.textContent ? 'inline' : 'none';
        d.groundMark.classList.toggle('is-mid', lv === 'mid' && !isActive);
        d.groundMark.classList.toggle('is-selected', isSel);
        d.groundMark.classList.toggle('is-hovered', isHov);
        d.groundMark.classList.toggle('is-layer-muted', !allowed);
        const y = gp.y + (u.kind === 'plane' ? 10 : Math.max(13, ryOuter + 4));
        d.groundMark.style.display = 'inline-flex';
        d.groundMark.style.transform = `translate3d(${gp.x}px,${y}px,0) translate(-50%,0) perspective(560px) rotateX(34deg)`;
        d.activeEl = d.groundMark;

        if (allowed) {
          collisionItems.push({
            id: u.id,
            anchor: { x: gp.x, y },
            fullSize: { width: isActive ? 132 : 76, height: 22 },
            compactSize: { width: 24, height: 20 },
            isHovered: isHov,
            isSelected: isSel,
            isActiveTask: u.queue.length > 0,
            dom: d,
          });
        }
      }
    }

    const resolved = GroundNameplateCollision.resolve(collisionItems, { margin: 3 });
    for (const r of resolved) {
      const item = collisionItems.find(it => it.id === r.id);
      if (!item) continue;
      if (r.isTop) item.dom.groundMark.classList.add('is-top');
      if (r.mode === 'compact') item.dom.groundMark.classList.add('is-compact');
      if (r.mode === 'dimmed') {
        item.dom.groundMark.classList.add('is-compact', 'is-dimmed');
      }
    }

    const planeRingVisible = domCache.get('thunder-1')?.ringGroup.style.display !== 'none';
    document.getElementById('sb-m-plane-ring').textContent = planeRingVisible ? '显示（异常）' : '已移除（仅地面十字+100m轨）';
  }

  function stepSim(dt) {
    for (const u of units) {
      if (u.kind === 'plane' && u.queue.length === 0 && u.orbit) {
        const angular = u.maxSpeed / u.orbit.r;
        u.orbit.phase -= angular * dt;
        u.x = u.orbit.x + Math.cos(u.orbit.phase) * u.orbit.r;
        u.z = u.orbit.z + Math.sin(u.orbit.phase) * u.orbit.r;
        u.yaw = Math.atan2(Math.sin(u.orbit.phase), Math.cos(u.orbit.phase));
        u.speed = u.maxSpeed;
        u.bank = 0.34;
        continue;
      }
      const target = u.queue.length > 0 ? u.queue[0] : null;
      if (target) {
        const dx = target.x - u.x;
        const dz = target.z - u.z;
        const dist = Math.hypot(dx, dz);
        const arriveR = u.kind === 'plane' ? 14 : 0.8;
        if (dist < arriveR) {
          if (u.kind === 'plane' && u.queue.length === 1) {
            const done = u.queue.shift();
            u.orbit = { x: done.x, z: done.z, r: ORBIT_RADIUS, phase: Math.atan2(u.z - done.z, u.x - done.x) };
            u.moveMode = 'orbit';
          } else {
            const done = u.queue.shift();
            if (Number.isFinite(done.facing) && u.layer === 'ground') u.yaw = done.facing;
            if (u.queue.length === 0) {
              if (u.moveMode === 'demo' && u.demoLoop) {
                u.x = u.demoLoop[0].x;
                u.z = u.demoLoop[0].z;
                u.queue = u.demoLoop.slice(1).map(p => ({ ...p }));
              } else {
                u.speed = 0;
                u.moveMode = 'idle';
              }
            }
          }
        } else {
          const desiredYaw = Math.atan2(dx, -dz);
          const diff = ((desiredYaw - u.yaw + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
          u.yaw += Math.sign(diff) * Math.min(Math.abs(diff), dt * (u.kind === 'plane' ? 2.6 : 3.6));
          if (u.kind === 'tank') u.turretYaw += Math.sign(((desiredYaw - u.turretYaw + Math.PI * 3) % (Math.PI * 2)) - Math.PI) * dt * 2.2;
          u.speed = Math.min(u.maxSpeed, u.speed + dt * (u.layer === 'air' ? 28 : 16));
          const step = Math.min(dist, u.speed * dt);
          u.x += (dx / dist) * step;
          u.z += (dz / dist) * step;
          u.bank = u.kind === 'plane' ? Math.max(-0.35, Math.min(0.35, diff * 0.5)) : 0;
        }
      } else {
        u.speed = Math.max(0, u.speed - dt * 20);
        u.bank = 0;
      }
    }
  }

  function computeTargets(anchor, facing) {
    const sel = units.filter(u => selected.has(u.id) && unitAllowed(u));
    if (!sel.length) return [];
    const results = [];
    const assign = (group, spacing, zOffset = 0) => {
      if (!group.length) return;
      if (Number.isFinite(facing)) {
        const rx = Math.cos(facing), rz = Math.sin(facing);
        const fx = -Math.sin(facing), fz = Math.cos(facing);
        group.forEach((u, i) => {
          const offset = (i - (group.length - 1) / 2) * spacing;
          const x = anchor.x + rx * offset + fx * zOffset;
          const z = anchor.z + rz * offset + fz * zOffset;
          results.push({ unit: u, x, z, facing, ...checkPointReachable(u, x, z) });
        });
      } else {
        const cx = group.reduce((s, u) => s + u.x, 0) / group.length;
        const cz = group.reduce((s, u) => s + u.z, 0) / group.length;
        for (const u of group) {
          const x = anchor.x + (u.x - cx);
          const z = anchor.z + (u.z - cz);
          results.push({ unit: u, x, z, facing: null, ...checkPointReachable(u, x, z) });
        }
      }
    };
    assign(sel.filter(u => u.layer === 'ground'), 12, 0);
    assign(sel.filter(u => u.kind === 'heli'), 18, 16);
    for (const p of sel.filter(u => u.kind === 'plane')) {
      results.push({ unit: p, x: anchor.x, z: anchor.z, facing: null, ...checkPointReachable(p, anchor.x, anchor.z) });
    }
    return results;
  }

  function pickUnitAt(sx, sy) {
    for (const u of units) {
      if (!unitAllowed(u) || u.layer !== 'air') continue;
      const ap = worldToScreen(u.x, u.y, u.z);
      if (Math.hypot(sx - ap.x, sy - ap.y) <= (u.kind === 'plane' ? 24 : 18)) return u;
    }
    const wPos = screenToWorld(sx, sy);
    let best = null;
    let bestD = 4.5;
    for (const u of units) {
      if (!unitAllowed(u)) continue;
      const d = Math.hypot(u.x - wPos.x, u.z - wPos.z);
      if (d < bestD) { bestD = d; best = u; }
    }
    return best;
  }

  stage.addEventListener('contextmenu', e => e.preventDefault());
  stage.addEventListener('pointermove', e => {
    const rect = stage.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    if (panDrag) {
      const dx = sx - panDrag.x;
      const dy = sy - panDrag.y;
      const ppm = stage.clientHeight / cam.span;
      const c = Math.cos(cam.angle), s = Math.sin(cam.angle);
      cam.cx -= (dx * c + (dy / SIN_TILT) * s) / ppm;
      cam.cz -= (-dx * s + (dy / SIN_TILT) * c) / ppm;
      panDrag = { x: sx, y: sy };
      return;
    }
    if (dragSelect) {
      dragSelect.x1 = sx;
      dragSelect.y1 = sy;
      const x = Math.min(dragSelect.x0, sx);
      const y = Math.min(dragSelect.y0, sy);
      const w = Math.abs(sx - dragSelect.x0);
      const h = Math.abs(sy - dragSelect.y0);
      dragBoxEl.hidden = !(w > 4 || h > 4);
      Object.assign(dragBoxEl.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
      return;
    }
    if (rightDrag) {
      const wPos = screenToWorld(sx, sy);
      const dist = Math.hypot(wPos.x - rightDrag.anchor.x, wPos.z - rightDrag.anchor.z);
      const facing = dist > 2.5 ? Math.atan2(wPos.x - rightDrag.anchor.x, -(wPos.z - rightDrag.anchor.z)) : null;
      rightDrag.facing = facing;
      rightDrag.targets = computeTargets(rightDrag.anchor, facing);
      return;
    }
    const labelEl = e.target.closest('[data-unit-id]');
    if (labelEl) {
      const u = units.find(item => item.id === labelEl.dataset.unitId);
      hoveredId = (u && unitAllowed(u)) ? u.id : null;
    } else {
      hoveredId = pickUnitAt(sx, sy)?.id ?? null;
    }
  });

  stage.addEventListener('pointerdown', e => {
    if (e.target.closest('.sandbox-hud-top, .sandbox-hud-bottom')) return;
    const rect = stage.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    lastCommandText = '';
    if (e.button === 1) { panDrag = { x: sx, y: sy }; return; }
    if (e.button === 2) {
      const activeSel = units.filter(u => selected.has(u.id) && unitAllowed(u));
      if (!activeSel.length) {
        const labelEl = e.target.closest('[data-unit-id]');
        const hit = labelEl ? units.find(u => u.id === labelEl.dataset.unitId && unitAllowed(u)) : pickUnitAt(sx, sy);
        if (hit) { selected.clear(); selected.add(hit.id); }
      }
      if (!units.some(u => selected.has(u.id) && unitAllowed(u))) {
        lastCommandText = '提示：当前图层下请先左键选定单位，再右键拖动预览航路';
        return;
      }
      const anchor = screenToWorld(sx, sy);
      rightDrag = { anchor, facing: null, append: e.shiftKey, targets: computeTargets(anchor, null) };
      return;
    }
    if (e.button === 0) {
      const labelEl = e.target.closest('[data-unit-id]');
      const hit = labelEl ? units.find(u => u.id === labelEl.dataset.unitId && unitAllowed(u)) : pickUnitAt(sx, sy);
      if (hit) {
        if (e.shiftKey) { selected.has(hit.id) ? selected.delete(hit.id) : selected.add(hit.id); }
        else { selected.clear(); selected.add(hit.id); }
        return;
      }
      dragSelect = { x0: sx, y0: sy, x1: sx, y1: sy, additive: e.shiftKey };
    }
  });

  window.addEventListener('pointerup', e => {
    if (e.button === 1) panDrag = null;
    if (e.button === 0 && dragSelect) {
      const x0 = Math.min(dragSelect.x0, dragSelect.x1), x1 = Math.max(dragSelect.x0, dragSelect.x1);
      const y0 = Math.min(dragSelect.y0, dragSelect.y1), y1 = Math.max(dragSelect.y0, dragSelect.y1);
      if (!dragSelect.additive) selected.clear();
      if (x1 - x0 > 5 || y1 - y0 > 5) {
        for (const u of units) {
          if (!unitAllowed(u)) continue;
          const ap = worldToScreen(u.x, u.y, u.z);
          const gp = worldToScreen(u.x, 0, u.z);
          const inBox = p => p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1;
          if (inBox(ap) || inBox(gp)) selected.add(u.id);
        }
      }
      dragSelect = null;
      dragBoxEl.hidden = true;
    }
    if (e.button === 2 && rightDrag) {
      let accepted = 0;
      let rejected = 0;
      for (const t of rightDrag.targets) {
        if (!t.valid) { rejected++; continue; }
        accepted++;
        if (!rightDrag.append) t.unit.queue.length = 0;
        t.unit.queue.push({ x: t.x, z: t.z, facing: t.facing });
        t.unit.moveMode = 'order';
      }
      lastCommandText = rejected && !accepted
        ? `指令拒收 · ${rejected} 个目标不可达（地面机库禁行）`
        : `线框幽灵指令已提交：${accepted} 个单位受理${rejected ? `，${rejected} 辆地面单位因障碍保留原任务` : ''}`;
      rightDrag = null;
    }
  });

  stage.addEventListener('wheel', e => {
    e.preventDefault();
    cam.anim = null;
    const rect = stage.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const before = screenToWorld(sx, sy);
    cam.span = Math.max(55, Math.min(680, cam.span * Math.pow(1.16, Math.sign(e.deltaY))));
    cam.targetSpan = cam.span;
    const after = screenToWorld(sx, sy);
    cam.cx += before.x - after.x;
    cam.cz += before.z - after.z;
  }, { passive: false });

  function setLayer(layer) {
    prefs.layer = layer;
    document.querySelectorAll('#sb-layer-seg button').forEach(b => b.classList.toggle('active', b.dataset.layer === layer));
    for (const id of [...selected]) {
      const u = units.find(item => item.id === id);
      if (u && !unitAllowed(u)) selected.delete(id);
    }
  }

  function setPixelSim(enabled) {
    prefs.pixelSim = Boolean(enabled);
    pixelBtn.setAttribute('aria-pressed', String(prefs.pixelSim));
    pixelBtn.textContent = `640px 3D 画布模拟：${prefs.pixelSim ? '开' : '关'}`;
    stage.classList.toggle('is-pixel-3d', prefs.pixelSim);
    document.getElementById('drawer-quality').value = prefs.pixelSim ? '640' : 'native';
    resize();
  }

  pixelBtn.addEventListener('click', () => setPixelSim(!prefs.pixelSim));
  document.getElementById('drawer-sync-pixel').addEventListener('click', () => setPixelSim(!prefs.pixelSim));
  document.getElementById('drawer-quality').addEventListener('ui-select-change', e => setPixelSim(e.detail.value !== 'native'));

  document.getElementById('sb-layer-seg').addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (btn) setLayer(btn.dataset.layer);
  });
  document.getElementById('sb-bg-seg').addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    document.querySelectorAll('#sb-bg-seg button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    prefs.bg = btn.dataset.bg;
  });
  document.getElementById('sb-zoom-seg').addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (btn) cam.anim = { from: cam.span, to: Number(btn.dataset.span), t0: performance.now(), dur: 320 };
  });
  document.getElementById('sb-rot-l').addEventListener('click', () => { cam.targetAngle -= Math.PI / 8; });
  document.getElementById('sb-rot-r').addEventListener('click', () => { cam.targetAngle += Math.PI / 8; });
  document.getElementById('sb-zoom-out').addEventListener('click', () => { cam.anim = { from: cam.span, to: Math.min(680, cam.span * 1.45), t0: performance.now(), dur: 240 }; });
  document.getElementById('sb-zoom-in').addEventListener('click', () => { cam.anim = { from: cam.span, to: Math.max(55, cam.span / 1.45), t0: performance.now(), dur: 240 }; });
  document.getElementById('sb-north').addEventListener('click', () => {
    cam.cx = 2; cam.cz = 4; cam.targetAngle = 0;
    cam.anim = { from: cam.span, to: 220, t0: performance.now(), dur: 320 };
  });
  document.getElementById('sb-reset').addEventListener('click', () => {
    units = initialUnits();
    selected.clear();
    selected.add('anvil-1');
    selected.add('falcon-2');
    selected.add('thunder-1');
    hoveredId = null;
    setLayer('all');
    lastCommandText = '已重置空地编队与示范航路';
  });

  escStack.register('sandbox-right-drag', () => Boolean(rightDrag), () => {
    rightDrag = null;
    lastCommandText = '已按 Esc 撤销移动预览';
  });

  window.addEventListener('keydown', e => {
    keys.add(e.code);
    if (e.code === 'Backquote') {
      e.preventDefault();
      setDrawerOpen(drawerEl.hidden);
    } else if (e.code === 'Digit1') setLayer('ground');
    else if (e.code === 'Digit2') setLayer('air');
    else if (e.code === 'Digit3') setLayer('all');
    else if (e.code === 'KeyQ') cam.targetAngle -= Math.PI / 12;
    else if (e.code === 'KeyE') cam.targetAngle += Math.PI / 12;
    else if (e.code === 'KeyX') {
      const act = units.filter(u => selected.has(u.id) && unitAllowed(u));
      for (const u of act) {
        u.queue.length = 0;
        if (u.kind === 'plane') { u.orbit = { x: u.x, z: u.z, r: ORBIT_RADIUS, phase: 0 }; u.moveMode = 'orbit'; }
        else { u.speed = 0; u.moveMode = 'idle'; }
      }
      if (act.length) lastCommandText = `已按 X 停止 ${act.length} 个单位`;
    }
  });
  window.addEventListener('keyup', e => keys.delete(e.code));

  let lastTime = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;
    if (document.activeElement === stage) {
      let px = (keys.has('KeyD') ? 1 : 0) - (keys.has('KeyA') ? 1 : 0);
      let pz = (keys.has('KeyS') ? 1 : 0) - (keys.has('KeyW') ? 1 : 0);
      if (px || pz) {
        const len = Math.hypot(px, pz);
        px /= len; pz /= len;
        const spd = cam.span * 0.55 * dt, c = Math.cos(cam.angle), s = Math.sin(cam.angle);
        cam.cx += (px * c + pz * s) * spd;
        cam.cz += (-px * s + pz * c) * spd;
      }
    }
    if (cam.anim) {
      const t = Math.min(1, (now - cam.anim.t0) / cam.anim.dur);
      const ease = 1 - Math.pow(1 - t, 3);
      cam.span = cam.anim.from + (cam.anim.to - cam.anim.from) * ease;
      cam.targetSpan = cam.span;
      if (t >= 1) cam.anim = null;
    } else {
      cam.span += (cam.targetSpan - cam.span) * Math.min(1, dt * 15);
    }
    cam.angle += (cam.targetAngle - cam.angle) * Math.min(1, dt * 16);

    const pal = readSemanticPalette();
    stepSim(dt);
    drawGround(pal);
    drawGroundVehicles();
    syncUI(pal);
    drawTacticalOverlay(now, pal);
    tickTelemetry(now);

    if (lastCommandText) statusLine.textContent = lastCommandText;
    else if (hoveredId) {
      const hov = units.find(u => u.id === hoveredId);
      if (hov) statusLine.textContent = `焦点高亮：${hov.name}（${hov.role}） · 速度 ${Math.round(hov.speed * 3.6)} km/h`;
    } else {
      const selNames = units.filter(u => selected.has(u.id) && unitAllowed(u)).map(u => u.name);
      statusLine.textContent = selNames.length
        ? `已选中 ${selNames.length} 个单位：${selNames.join('、')} · 右键拖动预览线框幽灵`
        : '就绪 · 左键点选或拖框选择单位';
    }
    requestAnimationFrame(frame);
  }

  resize();
  requestAnimationFrame(frame);

  return {
    getMetrics: () => ({
      pixelSim: prefs.pixelSim,
      groundCanvasWidth: groundCanvas.width,
      vehicleCanvasWidth: vehicleCanvas.width,
      airCanvasWidth: airCanvas.width,
      stageCssWidth: stage.clientWidth,
      devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      planeRingVisible: domCache.get('thunder-1')?.ringGroup.style.display !== 'none',
    }),
  };
}

/* ====== 06 密集编队车底锚定防叠字试验台 ====== */
function initClusterDemo() {
  const stage = document.getElementById('cluster-stage');
  const ringSvg = document.getElementById('cluster-ring-svg');
  const uiLayer = document.getElementById('cluster-ui-layer');
  const spacingInput = document.getElementById('cluster-spacing');
  const spacingVal = document.getElementById('cluster-spacing-val');
  const collisionToggle = document.getElementById('collision-toggle');
  const tabBtn = document.getElementById('cluster-tab-btn');
  const summaryBadge = document.getElementById('cluster-summary-badge');
  const diagBody = document.getElementById('cluster-diag-body');

  const clusterUnits = [
    { id: 'c-tank-1', name: '铁砧 11', kind: 'tank', task: true, gx: -1.8, gz: -0.7 },
    { id: 'c-tank-2', name: '铁砧 12', kind: 'tank', task: true, gx: -0.9, gz: -0.6 },
    { id: 'c-tank-3', name: '铁砧 13', kind: 'tank', task: false, gx: 0.0, gz: -0.5 },
    { id: 'c-tank-4', name: '铁砧 14', kind: 'tank', task: false, gx: 0.9, gz: -0.4 },
    { id: 'c-sup-1', name: '驼鹿 11', kind: 'supply', task: false, gx: 1.8, gz: -0.3 },
    { id: 'c-tank-5', name: '铁砧 15', kind: 'tank', task: true, gx: -1.3, gz: 0.5 },
    { id: 'c-tank-6', name: '铁砧 16', kind: 'tank', task: false, gx: -0.4, gz: 0.6 },
    { id: 'c-heli-1', name: '游隼 11', kind: 'heli', task: true, gx: 0.5, gz: 0.6 },
    { id: 'c-heli-2', name: '游隼 12', kind: 'heli', task: false, gx: 1.4, gz: 0.7 },
    { id: 'c-plane-1', name: '雷霆 11', kind: 'plane', task: true, gx: 0.0, gz: 1.4 },
  ];

  let selectedId = 'c-tank-1';
  let hoveredId = null;
  let lastSnapshot = [];

  const nodes = new Map();
  for (const u of clusterUnits) {
    const ringOuter = document.createElementNS(SVG_NS, 'ellipse');
    ringOuter.setAttribute('class', 'chassis-ring-outer');
    ringSvg.append(ringOuter);

    const mark = document.createElement('div');
    mark.className = `ground-mark kind-${u.kind}`;
    mark.dataset.clusterId = u.id;
    mark.append(createSilhouetteSvg(u.kind));
    const nm = document.createElement('span');
    nm.className = 'mark-name';
    nm.textContent = u.name;
    const sub = document.createElement('span');
    sub.className = 'mark-sub';
    sub.textContent = u.task ? '· 移动' : '· 待命';
    mark.append(nm, sub);
    mark.addEventListener('pointerenter', () => { hoveredId = u.id; renderCluster(); });
    mark.addEventListener('pointerleave', () => { if (hoveredId === u.id) hoveredId = null; renderCluster(); });
    mark.addEventListener('click', () => { selectedId = u.id; renderCluster(); });
    uiLayer.append(mark);
    nodes.set(u.id, { ringOuter, mark, sub });
  }

  function renderCluster() {
    const pal = readSemanticPalette();
    const w = Math.max(320, stage.clientWidth);
    const h = Math.max(240, stage.clientHeight);
    ringSvg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const spacingMeters = Number(spacingInput.value);
    spacingVal.textContent = `${spacingMeters} m`;
    const pxPerStep = spacingMeters * 3.1;
    const cx = w * 0.5;
    const cy = h * 0.45;

    const inputItems = [];
    for (const u of clusterUnits) {
      const node = nodes.get(u.id);
      const sx = Math.round(cx + u.gx * pxPerStep);
      const sy = Math.round(cy + u.gz * pxPerStep * 0.72);
      const isSel = selectedId === u.id;
      const isHov = hoveredId === u.id;
      const meta = VEHICLE_META[u.kind];
      const col = isSel ? pal.select : meta.channel === 'ground' ? pal.ground : meta.channel === 'heli' ? pal.heli : pal.plane;

      node.ringOuter.style.display = u.kind === 'plane' ? 'none' : '';
      node.ringOuter.setAttribute('cx', sx);
      node.ringOuter.setAttribute('cy', sy);
      node.ringOuter.setAttribute('rx', '16');
      node.ringOuter.setAttribute('ry', '11');
      node.ringOuter.setAttribute('stroke', col);
      node.ringOuter.setAttribute('stroke-width', isSel || isHov ? '2.4' : '1.4');
      node.ringOuter.setAttribute('opacity', isSel || isHov ? '0.95' : '0.45');

      const anchorY = sy + 14;
      node.mark.style.transform = `translate3d(${sx}px,${anchorY}px,0) translate(-50%,0) perspective(560px) rotateX(34deg)`;
      node.mark.classList.remove('is-selected', 'is-hovered', 'is-compact', 'is-dimmed', 'is-top');
      node.mark.classList.toggle('is-selected', isSel);
      node.mark.classList.toggle('is-hovered', isHov);
      node.sub.style.display = isSel || isHov ? 'inline' : 'none';

      inputItems.push({
        id: u.id,
        anchor: { x: sx, y: anchorY },
        fullSize: { width: isSel || isHov ? 116 : 74, height: 22 },
        compactSize: { width: 24, height: 20 },
        isHovered: isHov,
        isSelected: isSel,
        isActiveTask: u.task,
      });
    }

    const enabled = collisionToggle.checked;
    const resolved = enabled
      ? GroundNameplateCollision.resolve(inputItems, { margin: 3 })
      : inputItems.map(it => ({
        id: it.id,
        priority: it.isHovered ? 4 : it.isSelected ? 3 : it.isActiveTask ? 2 : 1,
        mode: 'full',
        isTop: it.isHovered || it.isSelected,
        anchor: it.anchor,
        anchorMoved: false,
      }));

    let fullN = 0, compN = 0, dimN = 0;
    const diagRows = [];
    for (const r of resolved) {
      const node = nodes.get(r.id);
      if (r.isTop) node.mark.classList.add('is-top');
      if (r.mode === 'compact') { node.mark.classList.add('is-compact'); compN++; }
      else if (r.mode === 'dimmed') { node.mark.classList.add('is-compact', 'is-dimmed'); dimN++; }
      else { fullN++; }
      const unitMeta = clusterUnits.find(u => u.id === r.id);
      diagRows.push({ ...r, name: unitMeta.name });
    }
    lastSnapshot = diagRows;
    summaryBadge.textContent = `展开 ${fullN} · 紧凑 ${compN} · 退让 ${dimN}`;

    diagBody.replaceChildren();
    for (const row of diagRows) {
      const tr = document.createElement('tr');
      const modeText = row.mode === 'full' ? '完整展开' : row.mode === 'compact' ? '紧凑剪影' : '低透退让';
      tr.innerHTML = `<td>${row.name}</td><td>P${row.priority}</td><td>${modeText}</td><td>${row.anchorMoved ? '异常偏移' : '0px (锚定)'}</td>`;
      diagBody.append(tr);
    }
  }

  function cycleNext(reverse = false) {
    const next = cycleOverlappingUnits(clusterUnits, selectedId, reverse);
    if (next) {
      selectedId = next.id;
      renderCluster();
    }
  }

  spacingInput.addEventListener('input', renderCluster);
  collisionToggle.addEventListener('change', renderCluster);
  tabBtn.addEventListener('click', () => cycleNext(false));
  stage.addEventListener('keydown', e => {
    if (e.code === 'Tab') {
      e.preventDefault();
      cycleNext(e.shiftKey);
    }
  });
  window.addEventListener('resize', renderCluster);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(renderCluster).observe(stage);
  renderCluster();

  return {
    getMetrics: () => ({
      spacingMeters: Number(spacingInput.value),
      collisionEnabled: collisionToggle.checked,
      selectedId,
      hoveredId,
      rows: lastSnapshot,
    }),
    setSpacing: meters => { spacingInput.value = String(meters); renderCluster(); },
    cycleNext,
  };
}

/* ====== 07 原生物理三档尺码与 4C 实战 HUD 骨架实操台 ====== */
function initHudScaleSection() {
  const scaleStage = document.getElementById('hud-scale-stage');
  const seg = document.getElementById('spec-size-seg');
  const aspectBtn = document.getElementById('scale-aspect-btn');
  const readout = document.getElementById('scale-metrics-readout');
  const demoBtn = document.getElementById('scale-demo-btn');
  const demoCmd = document.getElementById('scale-demo-cmd');
  const scaleRoe = document.getElementById('scale-roe-select');

  const aspects = ['full', '16:9', '21:9'];
  let aIdx = 0;

  function getMetrics() {
    const btnH = demoBtn ? Math.round(demoBtn.getBoundingClientRect().height * 10) / 10 : 0;
    const cmdH = demoCmd ? Math.round(demoCmd.getBoundingClientRect().height * 10) / 10 : 0;
    const selectH = scaleRoe
      ? Math.round(scaleRoe.querySelector('.ui-select-trigger').getBoundingClientRect().height * 10) / 10
      : 0;
    const stageTransform = scaleStage ? getComputedStyle(scaleStage).transform : 'none';
    return {
      uiSize: scaleStage?.dataset.uiSize || 'lg',
      aspect: scaleStage?.dataset.aspect || 'full',
      btnHeight: btnH,
      cmdHeight: cmdH,
      selectHeight: selectH,
      stageTransform,
      scaleRoeDropup: Boolean(scaleRoe?.classList.contains('is-dropup')),
    };
  }

  function updateReadout() {
    const m = getMetrics();
    if (readout) {
      readout.textContent = `${UI_SIZE_LABELS[m.uiSize] || m.uiSize} (${m.uiSize}) · 按钮 ${m.btnHeight}px · 命令格 ${m.cmdHeight}px · transform: ${m.stageTransform}`;
    }
  }

  const sizeCtrl = createUiSizeController(scaleStage, {
    defaultSize: 'lg',
    segContainer: seg,
    onChange: ({ size, label }) => {
      updateReadout();
      showToast(`HUD 原生物理尺码：${label} (${size})`);
    },
  });

  if (aspectBtn) {
    aspectBtn.addEventListener('click', () => {
      aIdx = (aIdx + 1) % aspects.length;
      const asp = aspects[aIdx];
      sizeCtrl.syncForAspect(asp, { silent: true });
      aspectBtn.textContent = `画幅：${asp === 'full' ? '全屏' : asp}`;
      updateReadout();
      showToast(`07 实操台画幅：${asp === 'full' ? '全屏自适应' : asp + ' (自动紧凑 680p)'}`);
    });
  }

  updateReadout();
  return Object.freeze({
    setSize: s => {
      const res = sizeCtrl.setSize(s, { silent: false, isAuto: false });
      updateReadout();
      return res;
    },
    getMetrics,
  });
}

/* ====== 初始化全部展区并暴露只读查询接口 ====== */
await loadStatusIdentity();
const sandboxCtrl = initSandbox();
escStack.register('debug-drawer', () => !drawerEl.hidden, () => setDrawerOpen(false));
initUiSelects();
renderColorMatrix();
renderTypeScale();
renderIconShowcase();
renderUnitCardDemo();
const clusterCtrl = initClusterDemo();
const hudScaleCtrl = initHudScaleSection();

window.uiSpec = Object.freeze({
  ready: true,
  getStatusIdentity: () => statusIdentity,
  getPalette: () => readSemanticPalette(),
  getSilhouettes: () => ({ ...VEHICLE_SILHOUETTES }),
  isDrawerOpen: () => !drawerEl.hidden,
  getSandboxMetrics: () => sandboxCtrl.getMetrics(),
  getClusterMetrics: () => clusterCtrl.getMetrics(),
  setClusterSpacing: m => clusterCtrl.setSpacing(m),
  setHudScale: s => hudScaleCtrl.setSize(s),
  getHudScaleMetrics: () => hudScaleCtrl.getMetrics(),
});
