/** 全局战术 UI/UX 活体规范台交互逻辑（100% 同源读取 令牌.css 与 图标与标绘.mjs）。 */
import {
  BRAND_IDENTITY,
  BRAND_LOGO_LAYERS,
  VEHICLE_SILHOUETTES,
  VEHICLE_PRIMARY_CLASSES,
  VEHICLE_META,
  WEAPON_ICONS,
  HAZARD_ICONS,
  HUD_ICONS,
  createBrandLogoSvg,
  createBrandLockup,
  buildBrandFaviconSvgString,
  drawBrandLogoCanvas,
  createSilhouetteSvg,
  createWeaponIconSvg,
  createHazardIconSvg,
  createHudIconSvg,
  resolveWeaponIconKind,
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
  ZOOM_ICON_TIERS,
  resolveZoomIconTier,
  clusterScreenUnits,
  drawZoomTierUnitIcon,
  drawGroundOrbitZone,
  drawAirTurnArc,
  warmSilhouettePathCache,
} from '/ui/图标与标绘.mjs';
import {
  bindAllUiSelects,
  createEscStack,
  createUiSizeController,
  loadUiPrefs,
  saveUiPrefs,
  playUiSfx,
  syncCmdPagerIndicator,
  runCmdPageTransition,
  TACTICAL_WHEEL_CONFIG,
  resolveWheelSectorFromVector,
  clampWheelVisualCenter,
  canCommitWheelSlot,
  renderTacticalWheel,
  renderVehicleDetailCard,
  renderVehicleHoverCard,
  attachDeckHoverCardController,
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
brandSigil.append(createBrandLogoSvg({ variant: 'sigil', size: 22 }));

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

/* ====== 03 品牌标识（生化肃清三楔盾）、12 兵种剪影、4 武器剪影、4 情景警示与 26 HUD 图标展区 ====== */
function renderBrandIdentityShowcase() {
  const mount = document.getElementById('brand-identity-mount');
  const toneSeg = document.getElementById('brand-tone-seg');
  const animBtn = document.getElementById('brand-anim-toggle');
  if (!mount) return;

  let activeTone = 'a3';
  let activeAnimated = false;

  const paintStencilCanvas = (canvas) => {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cssW = 196;
    const cssH = 76;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#3b3e42';
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 1;
    for (let x = 24; x < cssW; x += 24) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssH);
      ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(240, 211, 114, 0.28)';
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(0, cssH - 8);
    ctx.lineTo(cssW, cssH - 8);
    ctx.stroke();
    ctx.setLineDash([]);
    drawBrandLogoCanvas(ctx, 56, 36, 52, {
      variant: 'stencil',
      tone: 'mono',
      color: 'rgba(245, 246, 249, 0.86)',
      alpha: 0.9,
    });
    drawBrandLogoCanvas(ctx, 140, 36, 52, {
      variant: 'mark',
      tone: activeTone,
      alpha: 0.96,
    });
  };

  const renderCards = () => {
    mount.replaceChildren();
    const bgSwatch = document.querySelector('#icon-bg-seg button.active')?.dataset?.bg || 'dark';

    const makeCard = (title, codeTag, noteText) => {
      const card = document.createElement('div');
      card.className = 'brand-spec-card';
      const head = document.createElement('div');
      head.className = 'swatch-meta';
      head.innerHTML = `<strong>${title}</strong><code class="swatch-var">${codeTag}</code>`;
      const stage = document.createElement('div');
      stage.className = 'brand-spec-stage';
      stage.dataset.bgSwatch = bgSwatch;
      const note = document.createElement('span');
      note.style.fontSize = 'var(--ui-fs-2xs)';
      note.style.color = 'var(--ui-text-muted)';
      note.textContent = noteText;
      card.append(head, stage, note);
      return { card, stage };
    };

    const markCard = makeCard(
      '主标与横版 Lockup（48px+）',
      `variant="mark" · tone="${activeTone}"`,
      '大厅主视觉、加载屏与战区简报页头；支持三域彩蛋（陆/旋/固）与加载向心锁定动效。',
    );
    markCard.stage.append(
      createBrandLogoSvg({ variant: 'mark', size: 58, tone: activeTone, animated: activeAnimated }),
      createBrandLockup({
        layout: 'horizontal',
        tone: activeTone,
        subtitle: BRAND_IDENTITY.tagline,
        markSize: 38,
        animated: activeAnimated,
      }),
    );
    mount.append(markCard.card);

    const sigilCard = makeCard(
      '顶栏单色徽章（28~48px）',
      'variant="sigil" · evenodd',
      '香槟金实底与烟炭描边双态，纯 currentColor 负空间实心渲染，供顶栏与 HUD 徽标直接复用。',
    );
    const goldChip = document.createElement('span');
    goldChip.className = 'brand-sigil-chip is-gold';
    goldChip.append(createBrandLogoSvg({ variant: 'sigil', size: 24, animated: activeAnimated }));
    const darkChip = document.createElement('span');
    darkChip.className = 'brand-sigil-chip is-dark';
    darkChip.append(createBrandLogoSvg({ variant: 'sigil', size: 24, animated: activeAnimated }));
    const mono24 = createBrandLogoSvg({ variant: 'sigil', size: 32, animated: activeAnimated });
    mono24.style.color = 'var(--ui-text-primary)';
    sigilCard.stage.append(goldChip, darkChip, mono24);
    mount.append(sigilCard.card);

    const favCard = makeCard(
      '浏览器标签光学精简版（16~24px）',
      'variant="favicon" · /favicon.svg',
      `内置 #0d0e13 暗底与粗化护盾三楔，前后端同源读取 src/界面规范/favicon.svg（${buildBrandFaviconSvgString().length} B）。`,
    );
    const tab16 = document.createElement('span');
    tab16.className = 'brand-fav-pill';
    tab16.append(createBrandLogoSvg({ variant: 'favicon', size: 16, tone: activeTone }), document.createTextNode('Scavenger · 16px'));
    const tab24 = document.createElement('span');
    tab24.className = 'brand-fav-pill';
    tab24.append(createBrandLogoSvg({ variant: 'favicon', size: 24, tone: activeTone }), document.createTextNode('24px'));
    favCard.stage.append(tab16, tab24);
    mount.append(favCard.card);

    const stencilCard = makeCard(
      '军工漏印与 Path2D 停机坪贴花',
      'variant="stencil" + drawBrandLogoCanvas',
      '保留 4 处结构桥接缺口，基于离屏 Path2D 缓存零开销绘入 2D 雷达与 3D 停机坪 CanvasTexture。',
    );
    const stencilSvg = createBrandLogoSvg({ variant: 'stencil', size: 52, animated: activeAnimated });
    stencilSvg.style.color = 'var(--ui-text-primary)';
    const stencilCanvas = document.createElement('canvas');
    stencilCanvas.id = 'brand-stencil-canvas';
    stencilCanvas.className = 'brand-stencil-canvas';
    stencilCanvas.setAttribute('aria-label', '品牌标识停机坪 Path2D 贴花实绘');
    stencilCard.stage.append(stencilSvg, stencilCanvas);
    paintStencilCanvas(stencilCanvas);
    mount.append(stencilCard.card);
  };

  toneSeg?.addEventListener('click', e => {
    const btn = e.target.closest('button[data-tone]');
    if (!btn) return;
    activeTone = btn.dataset.tone || 'a3';
    toneSeg.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
    playUiSfx('toggle', { resume: true });
    renderCards();
  });

  animBtn?.addEventListener('click', () => {
    activeAnimated = !activeAnimated;
    animBtn.setAttribute('aria-pressed', String(activeAnimated));
    animBtn.textContent = `加载锁定动效：${activeAnimated ? '开' : '关'}`;
    playUiSfx('toggle', { resume: true });
    renderCards();
  });

  renderCards();
}

function renderIconShowcase() {
  renderBrandIdentityShowcase();
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

  const weaponMount = document.getElementById('weapon-icon-mount');
  if (weaponMount) {
    for (const [key, cfg] of Object.entries(WEAPON_ICONS)) {
      const card = document.createElement('div');
      card.className = 'weapon-icon-card';
      const head = document.createElement('div');
      head.className = 'weapon-icon-head';
      head.innerHTML = `<strong style="font-size:12.5px">${cfg.label}</strong><code class="swatch-var">${key}</code>`;
      const stage = document.createElement('div');
      stage.className = 'weapon-icon-stage';
      const heroWrap = document.createElement('div');
      heroWrap.className = 'weapon-icon-sample is-hero';
      heroWrap.append(createWeaponIconSvg(key, { size: 36 }));
      const heroLbl = document.createElement('span');
      heroLbl.textContent = '36px';
      heroWrap.append(heroLbl);
      const hudWrap = document.createElement('div');
      hudWrap.className = 'weapon-icon-sample';
      hudWrap.append(createWeaponIconSvg(key, { size: 24 }));
      const hudLbl = document.createElement('span');
      hudLbl.textContent = '24px 实装';
      hudWrap.append(hudLbl);
      stage.append(heroWrap, hudWrap);
      const scope = document.createElement('span');
      scope.style.fontSize = 'var(--ui-fs-2xs)';
      scope.style.color = 'var(--ui-text-muted)';
      scope.textContent = cfg.scope;
      card.append(head, stage, scope);
      weaponMount.append(card);
    }
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
    document.querySelectorAll('.silhouette-stage, .brand-spec-stage').forEach(el => {
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
    const lowDpr = prefs.pixelSim ? (680 / Math.max(h, 1)) : nativeDpr;
    for (const c of [groundCanvas, vehicleCanvas]) {
      c.width = Math.max(1, Math.round(w * lowDpr));
      c.height = prefs.pixelSim ? 680 : Math.max(1, Math.round(h * nativeDpr));
    }
    airCanvas.width = Math.max(1, Math.round(w * nativeDpr));
    airCanvas.height = Math.max(1, Math.round(h * nativeDpr));
    ringSvg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    dprBadge.textContent = prefs.pixelSim
      ? `3D 680p 暖滤镜模拟 ｜ UI 原生 ${nativeDpr}x`
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
    pixelBtn.textContent = `680p 复古微缩暖滤镜模拟：${prefs.pixelSim ? '开' : '关'}`;
    stage.classList.toggle('is-pixel-3d', prefs.pixelSim);
    document.getElementById('drawer-quality').value = prefs.pixelSim ? '680p-16x9' : 'native';
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
      groundCanvasHeight: groundCanvas.height,
      vehicleCanvasWidth: vehicleCanvas.width,
      vehicleCanvasHeight: vehicleCanvas.height,
      airCanvasWidth: airCanvas.width,
      airCanvasHeight: airCanvas.height,
      groundFilter: getComputedStyle(groundCanvas).filter,
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
      const hint = document.getElementById('scale-aspect-hint');
      if (hint) {
        hint.textContent = asp === '16:9'
          ? '16:9 (1208×680) 锁定紧凑 680p 尺码 · 中央 60% 零遮挡'
          : asp === '21:9'
            ? '21:9 (1588×680 Hor+) 自动横向展开视野 · 保持紧凑安全区'
            : '全屏自由安全区 · 中央 60% 严禁常驻实心面板';
      }
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

/* ====== V2.2 完整组件实操初始化（输入框/开关/受击血条/8槽轮盘/扫光页签/模态弹窗/音效） ====== */
function initV22Showcase() {
  const input = document.getElementById('v22-callsign-input');
  const errEl = document.getElementById('v22-input-err');
  if (input && errEl) {
    input.addEventListener('input', () => {
      const val = input.value.trim();
      const bad = !val || val.length > 20;
      input.classList.toggle('is-error', bad);
      errEl.hidden = !bad;
    });
  }

  const sfxToggle = document.getElementById('v22-sfx-toggle');
  if (sfxToggle) {
    sfxToggle.addEventListener('click', () => {
      const next = sfxToggle.getAttribute('aria-checked') !== 'true';
      sfxToggle.setAttribute('aria-checked', String(next));
      playUiSfx('toggle', { enabled: next, resume: true });
      showToast(`UI 交互音效示例：${next ? '已开启' : '已静音'}`);
    });
  }

  document.querySelectorAll('[data-sfx]').forEach(btn => {
    btn.addEventListener('click', () => {
      const enabled = sfxToggle?.getAttribute('aria-checked') !== 'false';
      playUiSfx(btn.dataset.sfx, { enabled, volume: 0.45, resume: true });
      showToast(`已触发 Web Audio 音效契约：${btn.dataset.sfx}`);
    });
  });

  // 双段延迟血条与夺点条互动
  let hp = 76;
  const hpTrack = document.getElementById('v22-hp-track');
  const hpTrail = document.getElementById('v22-hp-trail');
  const hpFill = document.getElementById('v22-hp-fill');
  const hpBonus = document.getElementById('v22-hp-bonus');
  const capFill = document.getElementById('v22-cap-fill');
  const capPct = document.getElementById('v22-cap-pct');
  function syncHp() {
    if (!hpFill || !hpTrail || !hpBonus || !hpTrack) return;
    hpFill.style.width = `${hp}%`;
    hpTrail.style.width = `${hp}%`;
    hpFill.style.background = hp <= 25 ? 'var(--ui-state-danger)' : hp <= 50 ? 'var(--ui-state-warn)' : 'var(--ui-ch-ground)';
    hpTrack.classList.toggle('is-critical', hp <= 25);
    const bonus = Math.max(0, Math.min(14, 100 - hp));
    hpBonus.style.left = `${hp}%`;
    hpBonus.style.width = `${bonus}%`;
    if (capFill && capPct) {
      const cap = Math.max(12, Math.min(100, hp + 10));
      capFill.style.width = `${cap}%`;
      capPct.textContent = `${cap}%`;
    }
  }
  document.getElementById('v22-hp-hit')?.addEventListener('click', () => {
    hp = Math.max(8, hp - 28);
    syncHp();
    playUiSfx('alert', { resume: true });
  });
  document.getElementById('v22-hp-reset')?.addEventListener('click', () => {
    hp = 76;
    syncHp();
    playUiSfx('confirm', { resume: true });
  });

  // 8 槽战术指令轮盘构建（Code SSOT V2.2 方案 A：连续八扇区军工仪表盘，零提示音）
  const wheelEl = document.getElementById('v22-wheel');
  const wheelSpecState = {
    unit: 'ground',
    wstate: 'normal',
    hoveredIndex: 1,
    pointerAngleDeg: -45,
    hasPointerAngle: true,
    guardOn: true,
    followOn: false,
    lowAltOn: true,
  };

  function buildSpecWheelModel() {
    const isCool = wheelSpecState.wstate === 'cooling';
    const isDis = wheelSpecState.wstate === 'disabled';
    if (wheelSpecState.unit === 'plane') {
      return {
        eyebrow: 'A-10C · 固定翼',
        eyebrowTone: 'plane',
        slots: [
          { id: 'gau8', label: 'GAU-8 扫射', key: '1', modeTag: '拖线', iconType: 'weapon', iconName: 'cannon', tone: 'plane', status: isDis ? 'disabled' : 'ready', charge: isDis ? '0' : '1150', reason: isDis ? '主炮弹药耗尽（0 发）' : '' },
          { id: 'sprint', label: '高速直飞', key: 'M', modeTag: '点选', iconType: 'hud', iconName: 'sprint', tone: 'plane', status: isCool ? 'cooling' : 'ready', cooldownSec: isCool ? 4.2 : 0, cooldownTotalSec: 6.0 },
          { id: 'stop', label: '停航待命', key: 'X', modeTag: '瞬发', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: 'ready' },
          { id: 'evacuate', label: '撤离战区', key: 'T', modeTag: 'RTB', iconType: 'hud', iconName: 'evacuate', tone: 'danger', status: isDis ? 'disabled' : 'ready', reason: isDis ? '正在进场阶段' : '' },
          { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: wheelSpecState.followOn ? 'active' : 'ready', charge: wheelSpecState.followOn ? '跟随' : '' },
          { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '瞬发', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
          { id: 'flare', label: '热诱弹', key: 'C', modeTag: '瞬发', iconType: 'hud', iconName: 'flare', tone: 'plane', status: isCool ? 'cooling' : 'ready', cooldownSec: isCool ? 5.6 : 0, cooldownTotalSec: 8.0, charge: '6' },
          { id: 'altitude', label: wheelSpecState.lowAltOn ? '低空 135m' : '高空 215m', key: 'V', modeTag: '开关', iconType: 'hud', iconName: 'altitude', tone: 'plane', status: wheelSpecState.lowAltOn ? 'active' : 'ready' },
        ],
      };
    }
    if (wheelSpecState.unit === 'mixed') {
      return {
        eyebrow: '混选 · 3 单位',
        eyebrowTone: 'select',
        slots: [
          null,
          null,
          { id: 'stop', label: '停航待命', key: 'X', modeTag: '全组', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: isDis ? 'disabled' : 'ready', reason: isDis ? '选区待命无队列' : '' },
          { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
          { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: wheelSpecState.followOn ? 'active' : 'ready', charge: wheelSpecState.followOn ? '跟随' : '' },
          { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '选区', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
          null,
          null,
        ],
      };
    }
    const isHeli = wheelSpecState.unit === 'heli';
    const tone = isHeli ? 'heli' : 'ground';
    return {
      eyebrow: isHeli ? 'AH-64D · 旋翼' : 'M1A2 · 地面',
      eyebrowTone: tone,
      slots: [
        { id: 'attackGround', label: '强制攻地', key: 'K', modeTag: '瞄准', iconType: 'hud', iconName: 'attack', tone, status: isDis ? 'disabled' : (isCool ? 'cooling' : 'ready'), cooldownSec: isCool ? 3.5 : 0, cooldownTotalSec: 4.5, reason: isDis ? '无有效射界' : '' },
        { id: 'guard', label: wheelSpecState.guardOn ? '警戒中' : '警戒守备', key: 'Z', modeTag: '开关', iconType: 'hud', iconName: 'shield', tone, status: wheelSpecState.guardOn ? 'active' : 'ready', charge: wheelSpecState.guardOn ? '已开' : '' },
        { id: 'stop', label: '停航待命', key: 'X', modeTag: '瞬发', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: 'ready' },
        { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
        { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: wheelSpecState.followOn ? 'active' : 'ready', charge: wheelSpecState.followOn ? '跟随' : '' },
        { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '瞬发', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
        null,
        null,
      ],
    };
  }

  function renderSpecWheel() {
    if (!wheelEl) return;
    const model = buildSpecWheelModel();
    renderTacticalWheel(wheelEl, model, {
      hoveredIndex: wheelSpecState.hoveredIndex,
      pointerAngleDeg: wheelSpecState.pointerAngleDeg,
      hasPointerAngle: wheelSpecState.hasPointerAngle,
      interactionMode: 'tap_click',
      onSelect: (slot) => {
        const check = canCommitWheelSlot(slot);
        if (!check.ok) {
          if (check.status !== 'empty') showToast(`${slot?.label || '槽位'}：${check.reason}`);
          return;
        }
        if (slot.id === 'guard') wheelSpecState.guardOn = !wheelSpecState.guardOn;
        else if (slot.id === 'follow') wheelSpecState.followOn = !wheelSpecState.followOn;
        else if (slot.id === 'altitude') wheelSpecState.lowAltOn = !wheelSpecState.lowAltOn;
        renderSpecWheel();
        showToast(`轮盘指令：${slot.label} [${slot.key}]`);
      },
      onCancel: () => {
        wheelSpecState.hoveredIndex = -1;
        wheelSpecState.hasPointerAngle = false;
        renderSpecWheel();
        showToast('中心死区：已取消轮盘选定');
      },
    });
  }

  if (wheelEl) {
    renderSpecWheel();
    wheelEl.addEventListener('pointermove', e => {
      const r = wheelEl.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width * 0.5);
      const dy = e.clientY - (r.top + r.height * 0.5);
      const hit = resolveWheelSectorFromVector(dx, dy, TACTICAL_WHEEL_CONFIG.deadzonePx);
      if (hit.index !== wheelSpecState.hoveredIndex || Math.abs(hit.angleDeg - wheelSpecState.pointerAngleDeg) > 4) {
        wheelSpecState.hoveredIndex = hit.index;
        wheelSpecState.pointerAngleDeg = hit.angleDeg;
        wheelSpecState.hasPointerAngle = !hit.inDeadzone;
        renderSpecWheel();
      }
    });
    document.getElementById('v22-wheel-unit-seg')?.addEventListener('click', e => {
      const btn = e.target.closest('[data-unit]');
      if (!btn) return;
      wheelSpecState.unit = btn.dataset.unit;
      document.querySelectorAll('#v22-wheel-unit-seg button').forEach(b => b.classList.toggle('active', b === btn));
      renderSpecWheel();
    });
    document.getElementById('v22-wheel-state-seg')?.addEventListener('click', e => {
      const btn = e.target.closest('[data-wstate]');
      if (!btn) return;
      wheelSpecState.wstate = btn.dataset.wstate;
      document.querySelectorAll('#v22-wheel-state-seg button').forEach(b => b.classList.toggle('active', b === btn));
      renderSpecWheel();
    });
  }

  // 斜切标签页切换
  document.getElementById('v22-skew-tabs')?.addEventListener('click', e => {
    const tab = e.target.closest('[data-tab]');
    if (!tab) return;
    document.querySelectorAll('#v22-skew-tabs .ui-skew-tab').forEach(b => {
      const hit = b === tab;
      b.classList.toggle('is-active', hit);
      b.setAttribute('aria-selected', String(hit));
    });
    ['wheel', 'menu', 'empty'].forEach(k => {
      const pane = document.getElementById(`v22-panel-${k}`);
      if (pane) pane.hidden = tab.dataset.tab !== k;
    });
  });

  // 模态二次确认弹窗＋LIFO ESC 注册
  const modalBg = document.getElementById('v22-modal-backdrop');
  const openModal = open => {
    if (!modalBg) return;
    modalBg.hidden = !open;
  };
  document.getElementById('v22-open-modal')?.addEventListener('click', () => openModal(true));
  document.getElementById('v22-modal-close')?.addEventListener('click', () => openModal(false));
  document.getElementById('v22-modal-cancel')?.addEventListener('click', () => openModal(false));
  document.getElementById('v22-modal-confirm')?.addEventListener('click', () => {
    openModal(false);
    playUiSfx('denied', { resume: true });
    showToast('已模拟执行模态危险确认');
  });
  modalBg?.addEventListener('click', e => {
    if (e.target === modalBg) openModal(false);
  });
  escStack.register('v22-modal', () => modalBg && !modalBg.hidden, () => openModal(false));
}

/* ====== 08 V2.2 实战扩展基元、4 级视距图标与固定翼贴地标绘 ====== */
function initV22CombatHudSection() {
  warmSilhouettePathCache();
  const pal = readSemanticPalette();

  // 8.1 全战竖长条兵牌 + 10 格火力评定条
  const bannerUnits = [
    {
      id: 'm1a2',
      name: 'M1A2 SEP v3',
      fullName: 'M1A2 SEP v3 主战坦克',
      role: '地面装甲通道 · 重甲突击',
      channel: 'ground',
      iconKind: 'mbt',
      count: 3,
      hp: 88,
      ammo: 74,
      ratings: [
        { label: '反装甲穿深', score: 9, tone: 'select' },
        { label: '反尸群面杀伤', score: 6, tone: 'ground' },
        { label: '正面复合装甲', score: 10, tone: 'ground' },
        { label: '野战机动性', score: 7, tone: 'select' },
      ],
    },
    {
      id: 'ah64e',
      name: 'AH-64E',
      fullName: 'AH-64E 阿帕奇武装直升机',
      role: '旋翼通道 (30m) · 悬停舔地',
      channel: 'heli',
      iconKind: 'attack_heli',
      count: 2,
      hp: 76,
      ammo: 62,
      ratings: [
        { label: '反装甲穿深', score: 8, tone: 'select' },
        { label: '反尸群面杀伤', score: 8, tone: 'danger' },
        { label: '机身防护装甲', score: 4, tone: 'ground' },
        { label: '越障机动性', score: 9, tone: 'select' },
      ],
    },
    {
      id: 'a10c',
      name: 'A-10C',
      fullName: 'A-10C 雷电 II 攻击机',
      role: '固定翼通道 (234m) · 贴地盘旋',
      channel: 'plane',
      iconKind: 'cas',
      count: 1,
      hp: 94,
      ammo: 85,
      ratings: [
        { label: '30mm 机炮穿甲', score: 10, tone: 'plane' },
        { label: '反尸群扫射', score: 9, tone: 'danger' },
        { label: '钛合金浴缸甲', score: 7, tone: 'plane' },
        { label: '高空巡航速', score: 10, tone: 'plane' },
      ],
    },
  ];
  let activeBannerId = 'm1a2';
  const bannerStrip = document.getElementById('v22-banner-strip');
  const ratingRows = document.getElementById('v22-rating-rows');
  const ratingUnitName = document.getElementById('v22-rating-unit-name');
  const ratingUnitRole = document.getElementById('v22-rating-unit-role');
  const bannerBadge = document.getElementById('v22-banner-selected-badge');

  function renderBannerAndRatings() {
    if (!bannerStrip || !ratingRows) return;
    bannerStrip.innerHTML = '';
    const activeUnit = bannerUnits.find(u => u.id === activeBannerId) || bannerUnits[0];
    for (const u of bannerUnits) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = `ui-banner-card${u.id === activeUnit.id ? ' active' : ''}`;
      card.dataset.channel = u.channel;
      card.setAttribute('role', 'option');
      card.setAttribute('aria-selected', String(u.id === activeUnit.id));

      const art = document.createElement('div');
      art.className = 'ui-banner-card-art';
      const svg = createSilhouetteSvg(u.iconKind, { size: 52 });
      svg.style.color = u.channel === 'heli' ? pal.heliInner : u.channel === 'plane' ? pal.planeInner : pal.groundInner;
      art.append(svg);

      const cnt = document.createElement('span');
      cnt.className = 'v22-banner-count';
      cnt.textContent = `×${u.count}`;

      const foot = document.createElement('div');
      foot.className = 'v22-banner-foot';
      const hpColor = u.channel === 'heli' ? 'var(--ui-ch-heli)' : u.channel === 'plane' ? 'var(--ui-ch-plane)' : 'var(--ui-ch-ground)';
      foot.innerHTML = `
        <span class="v22-banner-name">${u.name}</span>
        <div class="v22-banner-bars">
          <span class="v22-banner-bar"><i style="width:${u.hp}%;background:${hpColor}"></i></span>
          <span class="v22-banner-bar"><i style="width:${u.ammo}%;background:var(--ui-state-select)"></i></span>
        </div>`;

      card.append(art, cnt, foot);
      card.addEventListener('click', () => {
        activeBannerId = u.id;
        renderBannerAndRatings();
        playUiSfx('toggle', { resume: true });
      });
      bannerStrip.append(card);
    }

    if (ratingUnitName) ratingUnitName.textContent = activeUnit.fullName;
    if (ratingUnitRole) ratingUnitRole.textContent = activeUnit.role;
    if (bannerBadge) bannerBadge.textContent = activeUnit.name;

    ratingRows.innerHTML = '';
    for (const r of activeUnit.ratings) {
      const row = document.createElement('div');
      row.className = 'v22-rating-row';
      const label = document.createElement('span');
      label.textContent = r.label;
      const pips = document.createElement('div');
      pips.className = 'ui-rating-pips';
      pips.setAttribute('aria-label', `${r.label} ${r.score}/10`);
      for (let i = 0; i < 10; i += 1) {
        const pip = document.createElement('span');
        pip.className = `ui-rating-pip${i < r.score ? ' is-lit' : ''}`;
        if (i < r.score && r.tone !== 'select') pip.dataset.tone = r.tone;
        pips.append(pip);
      }
      row.append(label, pips);
      ratingRows.append(row);
    }
  }
  renderBannerAndRatings();

  // 8.2 右下竖向翻页命令栏（全页统一方案 B + 方向切页动效与滑块指示条）
  const pagedDeckConfigs = {
    attack: {
      cards: [
        { kbd: '1', iconType: 'weapon', icon: 'autocannon', txt: 'GAU-8/A', charge: '1150', sub: '30mm 机炮', mode: '拖线', tone: 'plane', isWeapon: true, active: true },
        { kbd: '2', iconType: 'weapon', icon: 'rocket', txt: 'Hydra 70', charge: '21', sub: '火箭弹', mode: '拖线', tone: 'plane', isWeapon: true },
        { kbd: '3', iconType: 'weapon', icon: 'missile', txt: 'AGM-65E2', charge: '6', sub: '空地导弹', mode: '点选', tone: 'plane', isWeapon: true },
        { kbd: '4', iconType: 'weapon', icon: 'bomb', txt: 'GBU-31', charge: '4', sub: '制导航弹', mode: '连点', tone: 'plane', isWeapon: true },
      ],
      utils: [
        { kbd: 'C', ico: 'flare', txt: '热诱弹', planeCmd: true },
        { kbd: 'V', ico: 'altitude', txt: '高空 215m', planeCmd: true },
      ],
    },
    move: {
      cards: [
        { kbd: 'M', iconType: 'hud', icon: 'sprint', txt: '高速直飞', charge: '待命', chargeTone: 'muted', sub: '极速突防切入', mode: '点选', tone: 'plane' },
        { kbd: 'X', iconType: 'hud', icon: 'stop', txt: '停航待命', charge: '', sub: '清空航路与打击', mode: '中止', tone: 'neutral' },
        { kbd: 'R', iconType: 'hud', icon: 'reset', txt: '视野复位', charge: '', sub: '回归跑道核心视野', mode: '复位', tone: 'neutral' },
        { kbd: 'T', iconType: 'hud', icon: 'evacuate', txt: '撤离战区', charge: '返航', chargeTone: 'danger', sub: '抛焰爬升离场', mode: 'RTB', tone: 'danger' },
      ],
      utils: [
        { kbd: 'F', ico: 'focus', txt: '聚焦视野' },
        { kbd: 'G', ico: 'follow', txt: '持续跟随', active: true },
      ],
    },
    debug: {
      cards: [
        { kbd: '1', iconType: 'hud', icon: 'airstrike', txt: '盘旋测试', charge: '激活', chargeTone: 'on', sub: '重置跑道标准盘旋', mode: '工况', tone: 'plane', active: true },
        { kbd: '2', iconType: 'hud', icon: 'attack', txt: '舔地触发', charge: '待命', chargeTone: 'muted', sub: '触发模拟对地扫射', mode: '演训', tone: 'plane' },
        { kbd: '3', iconType: 'hud', icon: 'play', txt: '刷新尸群', charge: '就绪', chargeTone: 'on', sub: '重置地面靶区丧尸', mode: '靶场', tone: 'ground' },
        { kbd: '4', iconType: 'hud', icon: 'pause', txt: '冻结模拟', charge: '', sub: '步进冻结物理时间', mode: '调试', tone: 'neutral' },
      ],
      utils: [
        { kbd: '5', ico: 'wheel', txt: '战术轮盘' },
        { kbd: '6', ico: 'copy', txt: '导出快照' },
      ],
    },
  };
  const pageOrder = ['attack', 'move', 'debug'];
  let activeCmdPage = 'attack';
  const pagedGrid = document.getElementById('v22-paged-cmd-grid');
  const pagedExitLayer = document.getElementById('v22-paged-cmd-exit');
  const pagerEl = document.getElementById('v22-cmd-pager');
  const pagerBadge = document.getElementById('v22-pager-badge');
  const pageLabels = { attack: '攻击', move: '机动', debug: '调试' };

  function renderPagedCommands({ animateDir = null } = {}) {
    if (!pagedGrid) return;
    const cfg = pagedDeckConfigs[activeCmdPage] || pagedDeckConfigs.attack;
    const activeIdx = Math.max(0, pageOrder.indexOf(activeCmdPage));
    syncCmdPagerIndicator(pagerEl, {
      activeIndex: activeIdx,
      count: pageOrder.length,
      activeTone: activeCmdPage === 'debug' ? 'plane' : 'select',
    });

    const applyDom = () => {
      pagedGrid.classList.add('is-partitioned');
      pagedGrid.replaceChildren();
      const wepGrid = document.createElement('div');
      wepGrid.className = 'ui-cmd-wep-grid';
      for (let i = 0; i < cfg.cards.length; i += 1) {
        const c = cfg.cards[i];
        const toneCls = c.tone === 'plane'
          ? 'is-plane-cmd'
          : (c.tone === 'ground'
            ? 'is-ground-cmd'
            : (c.tone === 'danger' ? 'is-danger-cmd' : 'is-neutral-cmd'));
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = [
          'ui-cmd-wep-card',
          toneCls,
          c.isWeapon ? 'is-weapon-card' : '',
          c.active ? 'is-active-mode' : '',
        ].filter(Boolean).join(' ');
        btn.style.setProperty('--cmd-idx', String(i));
        btn.dataset.cmdKey = c.kbd;

        const well = document.createElement('div');
        well.className = 'ui-cmd-wep-well';
        const kbd = document.createElement('span');
        kbd.className = 'c-kbd';
        kbd.textContent = c.kbd;
        const ico = document.createElement('span');
        ico.className = 'c-ico';
        ico.setAttribute('aria-hidden', 'true');
        if (c.iconType === 'weapon') {
          const wepKind = resolveWeaponIconKind(c.icon);
          ico.append(createWeaponIconSvg(wepKind, { size: 24 }));
        } else {
          ico.append(createHudIconSvg(c.icon, { size: 22 }));
        }
        well.append(kbd, ico);

        const info = document.createElement('div');
        info.className = 'ui-cmd-wep-info';
        const row1 = document.createElement('div');
        row1.className = 'ui-cmd-wep-row1';
        const txtEl = document.createElement('span');
        txtEl.className = 'c-txt';
        txtEl.textContent = c.txt;
        row1.append(txtEl);
        if (c.charge) {
          const chgEl = document.createElement('span');
          chgEl.className = [
            'c-charge',
            c.chargeTone === 'muted' ? 'is-muted' : '',
            c.chargeTone === 'on' ? 'is-on' : '',
            c.chargeTone === 'danger' ? 'is-danger' : '',
          ].filter(Boolean).join(' ');
          chgEl.textContent = c.charge;
          row1.append(chgEl);
        }

        const row2 = document.createElement('div');
        row2.className = 'ui-cmd-wep-row2';
        const subEl = document.createElement('span');
        subEl.className = 'c-subtxt';
        subEl.textContent = c.sub;
        const modeEl = document.createElement('span');
        modeEl.className = 'ui-cmd-wep-mode';
        modeEl.textContent = c.mode;
        row2.append(subEl, modeEl);

        info.append(row1, row2);
        btn.append(well, info);
        btn.addEventListener('click', () => {
          playUiSfx('confirm', { resume: true });
          showToast(`方案 B [${pageLabels[activeCmdPage]}]：${c.txt} (${c.sub})`);
        });
        wepGrid.append(btn);
      }

      const utilBar = document.createElement('div');
      utilBar.className = 'ui-cmd-util-bar';
      for (const uItem of cfg.utils) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `ui-cmd-util-btn${uItem.planeCmd ? ' is-plane-cmd' : ''}${uItem.active ? ' is-active-mode' : ''}`;
        btn.dataset.cmdKey = uItem.kbd;
        const left = document.createElement('span');
        left.className = 'ui-cmd-util-left';
        const ico = document.createElement('span');
        ico.className = 'c-ico';
        ico.setAttribute('aria-hidden', 'true');
        ico.append(createHudIconSvg(uItem.ico, { size: 18 }));
        const txt = document.createElement('span');
        txt.className = 'c-txt';
        txt.textContent = uItem.txt;
        left.append(ico, txt);
        const kbd = document.createElement('span');
        kbd.className = 'c-kbd';
        kbd.textContent = uItem.kbd;
        btn.append(left, kbd);
        btn.addEventListener('click', () => {
          playUiSfx('confirm', { resume: true });
          showToast(`方案 B 辅助条 [${uItem.ico}]：${uItem.txt}`);
        });
        utilBar.append(btn);
      }
      pagedGrid.append(wepGrid, utilBar);
    };

    if (animateDir) {
      runCmdPageTransition(pagedGrid, {
        direction: animateDir,
        exitLayerEl: pagedExitLayer,
        renderContent: applyDom,
      });
    } else {
      applyDom();
    }
    if (pagerBadge) pagerBadge.textContent = `当前页：${pageLabels[activeCmdPage]}`;
  }
  pagerEl?.addEventListener('click', e => {
    const tab = e.target.closest('.ui-cmd-pager-tab');
    if (!tab) return;
    const nextPage = tab.dataset.page || 'attack';
    if (nextPage === activeCmdPage) return;
    const prevIdx = Math.max(0, pageOrder.indexOf(activeCmdPage));
    const nextIdx = Math.max(0, pageOrder.indexOf(nextPage));
    const dir = nextIdx >= prevIdx ? 'down' : 'up';
    activeCmdPage = nextPage;
    pagerEl.querySelectorAll('.ui-cmd-pager-tab').forEach(t => {
      const hit = t === tab;
      t.classList.toggle('active', hit);
      t.setAttribute('aria-selected', String(hit));
    });
    renderPagedCommands({ animateDir: dir });
    playUiSfx('toggle', { resume: true });
  });
  renderPagedCommands();

  // 8.3 4 级视距 span 战术图标分层与同通道簇聚合
  const zoomCanvas = document.getElementById('v22-zoom-canvas');
  const zoomRange = document.getElementById('v22-zoom-span-range');
  const zoomValEl = document.getElementById('v22-zoom-span-val');
  const zoomBadge = document.getElementById('v22-zoom-tier-badge');
  const zoomExpandBtn = document.getElementById('v22-zoom-expand-btn');
  let zoomSpan = Number(zoomRange?.value || 960);
  let showClusterExpand = true;
  let lastZoomTier = resolveZoomIconTier(zoomSpan);

  const zoomTierLabels = {
    [ZOOM_ICON_TIERS.NONE]: 'L0 · 贴身名牌（无2D徽章）',
    [ZOOM_ICON_TIERS.COMPACT]: 'L1 · 紧凑剪影 (22×16)',
    [ZOOM_ICON_TIERS.CHIP]: 'L2 · 战术筹码 + 簇聚合 (26×18)',
    [ZOOM_ICON_TIERS.OVERVIEW]: 'L3 · 战略点标 + 簇聚合 (18×14)',
  };

  function renderZoomTierCanvas() {
    if (!zoomCanvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cssW = 520;
    const cssH = 210;
    if (zoomCanvas.width !== cssW * dpr || zoomCanvas.height !== cssH * dpr) {
      zoomCanvas.width = cssW * dpr;
      zoomCanvas.height = cssH * dpr;
    }
    const ctx = zoomCanvas.getContext('2d');
    if (!ctx) return;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    // 战术底格
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    for (let x = 26; x < cssW; x += 40) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, cssH); ctx.stroke();
    }
    for (let y = 26; y < cssH; y += 40) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cssW, y); ctx.stroke();
    }

    const tier = resolveZoomIconTier(zoomSpan);
    lastZoomTier = tier;
    if (zoomValEl) zoomValEl.textContent = `${zoomSpan} m`;
    if (zoomBadge) zoomBadge.textContent = zoomTierLabels[tier] || tier;

    // 模拟 3 组同通道编队（随 span 增大自动向中心收缩以演示簇聚合）
    const spread = Math.max(4, Math.min(34, (650 / Math.max(120, zoomSpan)) * 22));
    const sampleUnits = [
      // 地面 4 车重装簇
      { id: 'g1', channel: 'ground', iconKind: 'mbt', x: 132 - spread * 0.5, y: 112 - spread * 0.3, hp: 92, selected: true },
      { id: 'g2', channel: 'ground', iconKind: 'mbt', x: 132 + spread * 0.4, y: 112 + spread * 0.2, hp: 78 },
      { id: 'g3', channel: 'ground', iconKind: 'ifv', x: 132 - spread * 0.2, y: 112 + spread * 0.45, hp: 64 },
      { id: 'g4', channel: 'ground', iconKind: 'supply', x: 132 + spread * 0.55, y: 112 - spread * 0.4, hp: 100 },
      // 旋翼 3 机编队
      { id: 'h1', channel: 'heli', iconKind: 'attack_heli', x: 288 - spread * 0.35, y: 82, hp: 85 },
      { id: 'h2', channel: 'heli', iconKind: 'attack_heli', x: 288 + spread * 0.35, y: 86, hp: 72 },
      { id: 'h3', channel: 'heli', iconKind: 'trans_heli', x: 288, y: 82 + spread * 0.45, hp: 95 },
      // 固定翼单机
      { id: 'p1', channel: 'plane', iconKind: 'cas', x: 422, y: 98, hp: 96, hovered: true },
    ];

    if (tier === ZOOM_ICON_TIERS.NONE) {
      ctx.fillStyle = 'rgba(245, 246, 249, 0.88)';
      ctx.font = '700 12px "Microsoft YaHei UI",sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('L0 近景 (span ≤ 260m)：2D 标绘层关闭战术图标，完整保留 680p 微缩实景与贴身 DOM 名牌', cssW * 0.5, 42);
      for (const u of sampleUnits) {
        const col = u.channel === 'heli' ? pal.heli : u.channel === 'plane' ? pal.plane : pal.ground;
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(u.x, u.y, 4.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = '700 10px "Cascadia Mono","Consolas",monospace';
        ctx.fillStyle = pal.textPrimary;
        ctx.fillText(u.id.toUpperCase(), u.x, u.y + 15);
      }
    } else {
      const nodes = (tier === ZOOM_ICON_TIERS.CHIP || tier === ZOOM_ICON_TIERS.OVERVIEW)
        ? clusterScreenUnits(sampleUnits, { radiusPx: 18, minClusterSize: 3 })
        : sampleUnits.map(u => ({
            isCluster: false,
            id: u.id,
            channel: u.channel,
            iconKind: u.iconKind,
            count: 1,
            screenPt: { x: u.x, y: u.y },
            selectedCount: u.selected ? 1 : 0,
            hoveredCount: u.hovered ? 1 : 0,
            hp: u.hp,
          }));
      for (const node of nodes) {
        drawZoomTierUnitIcon(ctx, node, tier, pal, { showExpandHint: showClusterExpand });
      }
      ctx.fillStyle = 'rgba(182, 186, 197, 0.86)';
      ctx.font = '700 11px "Cascadia Mono","Consolas",monospace';
      ctx.textAlign = 'left';
      ctx.fillText(`渲染节点: ${nodes.length} (原始 ${sampleUnits.length} 单位) · 档位: ${tier}`, 14, cssH - 12);
    }
    ctx.restore();
  }

  zoomRange?.addEventListener('input', () => {
    zoomSpan = Number(zoomRange.value);
    renderZoomTierCanvas();
  });
  zoomExpandBtn?.addEventListener('click', () => {
    showClusterExpand = !showClusterExpand;
    zoomExpandBtn.setAttribute('aria-pressed', String(showClusterExpand));
    zoomExpandBtn.textContent = `簇悬停放射：${showClusterExpand ? '开' : '关'}`;
    renderZoomTierCanvas();
  });
  renderZoomTierCanvas();

  // 8.4 固定翼贴地主范围圈 + 35°转向虚弧 + 独立载具空间音效偏好
  const orbitCanvas = document.getElementById('v22-orbit-canvas');
  const orbitHlBtn = document.getElementById('v22-orbit-hl-btn');
  let orbitHighlighted = true;
  let orbitPhase = 0.65;

  function renderOrbitCanvas() {
    if (!orbitCanvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cssW = 520;
    const cssH = 170;
    if (orbitCanvas.width !== cssW * dpr || orbitCanvas.height !== cssH * dpr) {
      orbitCanvas.width = cssW * dpr;
      orbitCanvas.height = cssH * dpr;
    }
    const ctx = orbitCanvas.getContext('2d');
    if (!ctx) return;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const projectOrbit = (wx, wy, wz) => ({
      x: cssW * 0.5 + wx * 1.45,
      y: cssH * 0.64 + wz * 0.52 - wy * 0.28,
    });
    const queryGround = (wx, wz) => Math.sin(wx * 0.04) * 2.5 + Math.cos(wz * 0.05) * 2.0;
    const center = { x: 0, z: 0 };
    const radius = 85;
    const planePos = {
      x: Math.cos(orbitPhase) * radius,
      y: 234,
      z: Math.sin(orbitPhase) * radius,
    };
    const style = resolveRouteStyle('plane', orbitHighlighted ? 'selected' : 'normal', pal);

    drawGroundOrbitZone(ctx, center, radius, queryGround, projectOrbit, style, orbitHighlighted, {
      planePos,
      radiusLabel: 'R 85m · 贴地主范围圈',
      palette: pal,
    });
    drawAirTurnArc(ctx, planePos, center, radius, projectOrbit, style, {
      arcDeg: 35,
      turnDir: 1,
      highlighted: orbitHighlighted,
      onlyOnOrbit: true,
    });

    // 高空机身剪影锚点
    const bodyScr = projectOrbit(planePos.x, planePos.y, planePos.z);
    ctx.fillStyle = orbitHighlighted ? pal.select : pal.planeInner;
    ctx.beginPath();
    ctx.arc(bodyScr.x, bodyScr.y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = '700 10.5px "Cascadia Mono","Consolas",monospace';
    ctx.fillStyle = pal.textPrimary;
    ctx.textAlign = 'left';
    ctx.fillText('A-10C · ALT 234m (35°转向虚弧)', bodyScr.x + 8, bodyScr.y + 3);
    ctx.restore();
  }

  orbitHlBtn?.addEventListener('click', () => {
    orbitHighlighted = !orbitHighlighted;
    orbitHlBtn.setAttribute('aria-pressed', String(orbitHighlighted));
    orbitHlBtn.textContent = `选中高亮：${orbitHighlighted ? '开' : '关'}`;
    renderOrbitCanvas();
  });
  renderOrbitCanvas();
  if (!REDUCED_MOTION) {
    window.setInterval(() => {
      orbitPhase = (orbitPhase + 0.03) % (Math.PI * 2);
      renderOrbitCanvas();
    }, 90);
  }

  // 独立载具空间引擎音偏好键（与菜单 sfxEnabled 解耦）
  const vehAudioToggle = document.getElementById('v22-veh-audio-toggle');
  const vehAudioVol = document.getElementById('v22-veh-audio-vol');
  const vehAudioVolVal = document.getElementById('v22-veh-audio-vol-val');
  const initPrefs = loadUiPrefs();
  if (vehAudioToggle) vehAudioToggle.setAttribute('aria-checked', String(initPrefs.vehicleAudioEnabled));
  if (vehAudioVol) vehAudioVol.value = String(Math.round(initPrefs.vehicleAudioVolume * 100));
  if (vehAudioVolVal) vehAudioVolVal.textContent = `${Math.round(initPrefs.vehicleAudioVolume * 100)}%`;

  vehAudioToggle?.addEventListener('click', () => {
    const next = vehAudioToggle.getAttribute('aria-checked') !== 'true';
    vehAudioToggle.setAttribute('aria-checked', String(next));
    saveUiPrefs({ vehicleAudioEnabled: next });
    playUiSfx('toggle', { resume: true });
    showToast(`载具空间引擎音偏好已保存：${next ? '开启' : '静音'}`);
  });
  vehAudioVol?.addEventListener('input', () => {
    const pct = Number(vehAudioVol.value);
    if (vehAudioVolVal) vehAudioVolVal.textContent = `${pct}%`;
    saveUiPrefs({ vehicleAudioVolume: pct / 100 });
  });

  // 680p 复古微缩移轴景深偏好键（dofMode: 'off' | 'soft' | 'standard'）
  const dofSeg = document.getElementById('v22-dof-seg');
  const syncV22DofSeg = (mode) => {
    if (!dofSeg) return;
    dofSeg.querySelectorAll('[data-dof]').forEach(btn => {
      const hit = btn.dataset.dof === mode;
      btn.classList.toggle('active', hit);
      btn.setAttribute('aria-pressed', String(hit));
    });
  };
  syncV22DofSeg(initPrefs.dofMode || 'soft');
  dofSeg?.addEventListener('click', e => {
    const btn = e.target.closest('[data-dof]');
    if (!btn) return;
    const mode = ['off', 'soft', 'standard'].includes(btn.dataset.dof) ? btn.dataset.dof : 'soft';
    saveUiPrefs({ dofMode: mode });
    syncV22DofSeg(mode);
    playUiSfx('toggle', { resume: true });
    showToast(`移轴微缩景深偏好已保存：${mode}`);
  });

  // 8.5 跨页共享载具参数详情组件（.ui-vd-card + .ui-vd-hover，覆盖地面/旋翼/固定翼无血量燃油制）
  const vdUnitSeg = document.getElementById('v22-vd-unit-seg');
  const vdBingoBtn = document.getElementById('v22-vd-bingo-btn');
  const vdFullMount = document.getElementById('v22-vd-full-mount');
  const vdHoverMount = document.getElementById('v22-vd-hover-mount');
  const vdArmoryMount = document.getElementById('v22-vd-armory-mount');
  let vdActiveUnit = 'ground';
  let vdBingoDemo = false;

  const buildSpecDemoModel = (unitKey, mode = 'play') => {
    if (unitKey === 'heli') {
      return {
        slug: 'US_AH64D',
        shortName: 'AH-64D',
        subTitle: '武装直升机 · 30m 贴地越障',
        iconKind: 'attack_heli',
        channel: 'heli',
        channelLabel: '旋翼',
        cost: 240,
        mode,
        usesFuel: false,
        hp: 15,
        maxHp: 15,
        bonusArmorPct: 0,
        kpis: [
          { k: '巡航高度', v: '低空 30m' },
          { k: '载荷挂点', v: '3 挂点' },
          { k: '机动航速', v: '220 km/h' },
          { k: '侦察视野', v: '2000m' },
        ],
        caps: [
          { k: '清尸扫射', score: 8, tone: 'heli' },
          { k: '精确反甲', score: 9, tone: 'select' },
          { k: '立体机动', score: 8, tone: 'ground' },
        ],
        weapons: [
          { id: 'm230', kind: 'cannon', name: 'M230 30mm 链炮', ammo: 1200, maxAmmo: 1200, minRangeM: 0, maxRangeM: 1100, splashInnerM: 2.5, splashOuterM: 6, damage: 2.4, roleTag: '压制清群' },
          { id: 'agm114', kind: 'missile', name: 'AGM-114 地狱火', ammo: 8, maxAmmo: 8, minRangeM: 260, maxRangeM: 1600, splashInnerM: 4, splashOuterM: 10, damage: 26, roleTag: '精确点杀' },
          { id: 'hydra_h', kind: 'rocket', name: 'Hydra 70 火箭巢', ammo: 38, maxAmmo: 38, minRangeM: 200, maxRangeM: 1300, splashInnerM: 5, splashOuterM: 14, damage: 7.5, roleTag: '面域覆盖' },
        ],
        foldMeta: [
          ['型号代号', 'US_AH64D'],
          ['实测尺度', '15.0 × 14.6 × 4.6 m'],
          ['可动机构', '主旋翼 · 尾桨 · 机炮'],
          ['基底来源', 'brokenarrowstats.com'],
        ],
        specVersion: 'ba-source-ssot-v1',
      };
    }
    if (unitKey === 'plane') {
      const isArm = mode === 'armory';
      const pct = isArm ? 100 : (vdBingoDemo ? 12 : 76);
      const bingoState = isArm ? 'nominal' : (vdBingoDemo ? 'rtb_bingo' : 'nominal');
      const burnRate = isArm ? 1.0 : (vdBingoDemo ? 1.45 : 1.0);
      const remainSec = Math.round((480 * (pct / 100)) / burnRate);
      return {
        slug: 'US_A10',
        shortName: 'A-10C',
        subTitle: '对地攻击机 · 燃油制无血量',
        iconKind: 'cas',
        channel: 'plane',
        channelLabel: '固定翼',
        cost: 345,
        mode,
        usesFuel: true,
        hp: null,
        maxHp: null,
        fuel: {
          pct,
          remainSec,
          maxSec: 480,
          burnRate,
          modeLabel: isArm ? '满油巡航' : (vdBingoDemo ? '突击' : '巡航'),
          bingoThresholdPct: 14,
          bingoState,
          rearmCooldownSec: 35,
        },
        kpis: [
          { k: isArm ? '满油留空' : '当前余油', v: isArm ? '8:00' : `${Math.floor(remainSec / 60)}:${String(remainSec % 60).padStart(2, '0')}` },
          { k: '工况油耗', v: isArm ? '1.0–1.45×' : `${vdBingoDemo ? '突击 1.45×' : '巡航 1.0×'}` },
          { k: '整备冷却', v: '35s' },
          { k: '侦察视野', v: '2500m' },
        ],
        caps: [
          { k: '轻甲清扫', score: 10, tone: 'ground' },
          { k: '中甲压制', score: 9, tone: 'select' },
          { k: '重型爆破', score: 9, tone: 'danger' },
        ],
        weapons: [
          { id: 'gau8', kind: 'cannon', name: 'GAU-8/A 30mm', ammo: 1150, maxAmmo: 1150, minRangeM: 0, maxRangeM: 1000, splashInnerM: 3, splashOuterM: 7, damage: 2.25, roleTag: '拖线扫射', actionCmd: 'strafe', clickable: !isArm },
          { id: 'hydra', kind: 'rocket', name: 'Hydra 70 火箭', ammo: 42, maxAmmo: 42, minRangeM: 140, maxRangeM: 1100, splashInnerM: 5, splashOuterM: 14, damage: 7.5, roleTag: '拖线覆盖', actionCmd: 'rocket', clickable: !isArm },
          { id: 'agm65', kind: 'missile', name: 'AGM-65 小牛', ammo: 6, maxAmmo: 6, minRangeM: 200, maxRangeM: 1250, splashInnerM: 4, splashOuterM: 11, damage: 22, roleTag: '点选精打', actionCmd: 'missile', clickable: !isArm },
          { id: 'jdam', kind: 'bomb', name: 'GBU-31 JDAM', ammo: 4, maxAmmo: 4, minRangeM: 180, maxRangeM: 1000, splashInnerM: 12, splashOuterM: 28, damage: 56, roleTag: '连点轰炸', actionCmd: 'bomb', clickable: !isArm },
        ],
        foldMeta: [
          ['型号代号', 'US_A10'],
          ['作战高度', '135–215m · 盘旋 R120–380m'],
          ['电子防护', 'ECM 20%'],
          ['基底来源', 'brokenarrowstats.com'],
        ],
        specVersion: 'ba-source-ssot-v1',
      };
    }
    return {
      slug: 'US_M1A2',
      shortName: 'M1A2 SEP v3',
      subTitle: '重型主战坦克 · 反应甲强化',
      iconKind: 'mbt',
      channel: 'ground',
      channelLabel: '地面',
      cost: 285,
      mode,
      usesFuel: false,
      hp: 20,
      maxHp: 20,
      bonusArmorPct: 12,
      kpis: [
        { k: '装甲防护', v: '700 / 1200' },
        { k: '公路机动', v: '67 km/h' },
        { k: '侦察视野', v: '1400m' },
        { k: '主炮弹药', v: '28/28' },
      ],
      caps: [
        { k: '装甲防护', score: 9, tone: 'ground' },
        { k: '清尸压制', score: 8, tone: 'select' },
        { k: '重甲攻坚', score: 9, tone: 'danger' },
      ],
      weapons: [
        { id: 'm256_he', kind: 'cannon', name: '120mm 高爆弹', ammo: 28, maxAmmo: 28, minRangeM: 0, maxRangeM: 1400, splashInnerM: 3, splashOuterM: 9, damage: 12, roleTag: '面伤清群' },
        { id: 'm1028', kind: 'cannon', name: 'M1028 霰弹', ammo: 14, maxAmmo: 14, minRangeM: 0, maxRangeM: 450, splashInnerM: 6, splashOuterM: 18, damage: 18, roleTag: '近距尸潮' },
        { id: 'm240', kind: 'cannon', name: '7.62mm 同轴机枪', ammo: 2000, maxAmmo: 2000, minRangeM: 0, maxRangeM: 600, splashInnerM: 0, splashOuterM: 0, damage: 1.5, roleTag: '持续补刀' },
      ],
      foldMeta: [
        ['型号代号', 'US_M1A2'],
        ['实测尺度', '9.8 × 3.7 × 2.4 m'],
        ['可动机构', '炮塔 · 炮管'],
        ['基底来源', 'brokenarrowstats.com'],
      ],
      specVersion: 'ba-source-ssot-v1',
    };
  };

  const renderVdShowcase = () => {
    if (!vdFullMount || !vdHoverMount || !vdArmoryMount) return;
    if (vdBingoBtn) {
      vdBingoBtn.hidden = vdActiveUnit !== 'plane';
      vdBingoBtn.setAttribute('aria-pressed', String(vdBingoDemo));
      vdBingoBtn.textContent = vdBingoDemo ? '恢复满油巡航' : '模拟低油 Bingo';
    }
    const playModel = buildSpecDemoModel(vdActiveUnit, 'play');
    const armoryModel = buildSpecDemoModel(vdActiveUnit, 'armory');
    renderVehicleDetailCard(vdFullMount, playModel, {
      narrow: false,
      showClose: true,
      onClose: () => showToast('演示卡收起回调已触发（快捷键 I / Esc）'),
      onWeaponClick: w => showToast(`点击固定翼武器直入瞄准：${w.name}`),
    });
    renderVehicleHoverCard(vdHoverMount, playModel, {
      isGroup: vdActiveUnit === 'ground',
      count: vdActiveUnit === 'ground' ? 3 : 1,
      avgPct: vdActiveUnit === 'ground' ? 86 : undefined,
    });
    renderVehicleDetailCard(vdArmoryMount, armoryModel, {
      narrow: true,
      showClose: false,
    });
  };

  vdUnitSeg?.addEventListener('click', e => {
    const btn = e.target.closest('[data-vd-unit]');
    if (!btn) return;
    vdActiveUnit = btn.dataset.vdUnit || 'ground';
    vdUnitSeg.querySelectorAll('[data-vd-unit]').forEach(b => b.classList.toggle('active', b === btn));
    renderVdShowcase();
    playUiSfx('toggle', { resume: true });
  });
  vdBingoBtn?.addEventListener('click', () => {
    vdBingoDemo = !vdBingoDemo;
    renderVdShowcase();
    playUiSfx('alert', { resume: true });
  });
  renderVdShowcase();

  // 确认 attachDeckHoverCardController 可正常挂载（零异常导出核验）
  const noopHoverCtrl = attachDeckHoverCardController({});
  noopHoverCtrl.destroy();

  return Object.freeze({
    getMetrics: () => ({
      bannerCount: document.querySelectorAll('#v22-banner-strip .ui-banner-card').length,
      ratingPipsCount: document.querySelectorAll('#v22-rating-rows .ui-rating-pips').length,
      pagerTabCount: document.querySelectorAll('#v22-cmd-pager .ui-cmd-pager-tab').length,
      vdCardCount: document.querySelectorAll('#sec-combat-hud .ui-vd-card').length,
      vdHoverCount: document.querySelectorAll('#sec-combat-hud .ui-vd-hover').length,
      vdActiveUnit,
      vdBingoDemo,
      activeCmdPage,
      zoomSpan,
      zoomTier: lastZoomTier,
      orbitHighlighted,
      vehicleAudioEnabled: loadUiPrefs().vehicleAudioEnabled,
      dofMode: loadUiPrefs().dofMode,
    }),
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
initV22Showcase();
const combatHudCtrl = initV22CombatHudSection();

window.uiSpec = Object.freeze({
  version: 'V2.2',
  ready: true,
  getStatusIdentity: () => statusIdentity,
  getPalette: () => readSemanticPalette(),
  getBrandIdentity: () => ({ ...BRAND_IDENTITY, layerKeys: Object.keys(BRAND_LOGO_LAYERS) }),
  getSilhouettes: () => ({ ...VEHICLE_SILHOUETTES }),
  getWeaponIcons: () => ({ ...WEAPON_ICONS }),
  isDrawerOpen: () => !drawerEl.hidden,
  getSandboxMetrics: () => sandboxCtrl.getMetrics(),
  getClusterMetrics: () => clusterCtrl.getMetrics(),
  setClusterSpacing: m => clusterCtrl.setSpacing(m),
  setHudScale: s => hudScaleCtrl.setSize(s),
  getHudScaleMetrics: () => hudScaleCtrl.getMetrics(),
  getV22CombatHudMetrics: () => combatHudCtrl.getMetrics(),
  getWheelMetrics: () => {
    const svg = document.querySelector('#v22-wheel .ui-wheel-svg');
    const slots = [...document.querySelectorAll('#v22-wheel .ui-wheel-slot')];
    const center = document.querySelector('#v22-wheel .ui-wheel-center');
    const clampSample = clampWheelVisualCenter({ x: 10, y: 10 }, 1208, 680);
    return {
      hasSvg: Boolean(svg),
      slotCount: slots.length,
      activeCount: slots.filter(el => el.dataset.slotState === 'active').length,
      emptyCount: slots.filter(el => el.dataset.slotState === 'empty').length,
      coolingCount: slots.filter(el => el.dataset.slotState === 'cooling').length,
      disabledCount: slots.filter(el => el.dataset.slotState === 'disabled').length,
      hasCenter: Boolean(center),
      clampCornerOk: clampSample.clamped && clampSample.x >= TACTICAL_WHEEL_CONFIG.visualRadius && clampSample.y >= TACTICAL_WHEEL_CONFIG.visualRadius,
    };
  },
  getV22ComponentsReady: () => Boolean(
    document.querySelector('.ui-input')
    && document.querySelector('.ui-toggle')
    && document.querySelector('.ui-wheel')
    && document.querySelector('#v22-wheel .ui-wheel-svg')
    && document.querySelectorAll('#v22-wheel .ui-wheel-slot').length === 8
    && document.querySelector('.ui-cap-bar')
    && document.querySelector('.ui-cur-action')
    && document.getElementById('v22-modal-backdrop')
    && document.querySelector('.ui-banner-card')
    && document.querySelector('.ui-rating-pips')
    && document.querySelector('.ui-cmd-shell-paged')
    && document.querySelector('.ui-cmd-viewport')
    && document.querySelector('.ui-cmd-pager-tab')
    && document.querySelector('.ui-cmd-pager-indicator')
    && document.querySelector('#brand-sigil svg[data-brand-logo="bio-purge-triad-shield"]')
    && document.querySelector('#brand-identity-mount .ui-brand-lockup')
    && document.querySelectorAll('#brand-identity-mount svg[data-brand-logo="bio-purge-triad-shield"]').length >= 6
    && document.getElementById('brand-stencil-canvas')
    && document.querySelectorAll('#weapon-icon-mount .weapon-icon-card').length === 4
    && document.querySelectorAll('#weapon-icon-mount svg[data-weapon-icon]').length === 8
    && document.querySelector('.ui-cmd-wep-grid')
    && document.querySelector('.ui-cmd-wep-card')
    && document.querySelector('.ui-cmd-util-bar')
    && document.querySelector('.ui-cmd-util-btn')
    && document.getElementById('v22-zoom-canvas')
    && document.getElementById('v22-orbit-canvas')
    && document.getElementById('v22-veh-audio-toggle')
    && document.querySelector('.ui-back-home')
    && document.querySelector('#v22-vd-full-mount .ui-vd-card')
    && document.querySelector('#v22-vd-hover-mount .ui-vd-hover')
    && document.querySelector('#v22-vd-armory-mount .ui-vd-card.is-narrow')
  ),
});
