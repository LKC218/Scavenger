/**
 * Scavenger 全局共享交互控件库 (Code SSOT V2.2)
 * 职责：严格 LIFO ESC 栈、定制战术下拉 .ui-select（自动底部空间探测 .is-dropup）、三档原生物理尺码联动、方案 B 翻页动效与方案 A 连续八扇区战术轮盘引擎。
 */

import {
  HUD_ICONS,
  VEHICLE_SILHOUETTES,
  WEAPON_ICONS,
  resolveWeaponIconKind,
  readSemanticPalette,
} from './图标与标绘.mjs';

export const UI_SIZE_LABELS = Object.freeze({
  compact: '紧凑 680p',
  lg: '标准大号',
  xl: '超大高分',
});

const VALID_SIZES = new Set(['compact', 'lg', 'xl']);

/**
 * 创建严格后进先出（LIFO）的 ESC 回退栈。
 * 后注册（或更靠栈顶）的交互浮层会优先响应 ESC 并被关闭。
 */
export function createEscStack() {
  const entries = [];

  function unregister(id) {
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      if (entries[i].id === id) entries.splice(i, 1);
    }
  }

  function register(id, isOpenFn, closeFn) {
    unregister(id);
    entries.push({ id, isOpenFn, closeFn });
    return () => unregister(id);
  }

  function closeTop() {
    for (let i = entries.length - 1; i >= 0; i -= 1) {
      const item = entries[i];
      if (item.isOpenFn && item.isOpenFn()) {
        item.closeFn?.();
        return item.id;
      }
    }
    return null;
  }

  function handleKeydown(event) {
    if (event.key !== 'Escape' && event.code !== 'Escape') return false;
    const closedId = closeTop();
    if (closedId) {
      event.preventDefault();
      return true;
    }
    return false;
  }

  return Object.freeze({
    register,
    unregister,
    closeTop,
    handleKeydown,
  });
}

/**
 * 绑定单个定制战术下拉框 `.ui-select`。
 * 自动在视口/容器底沿空间不足时添加 `.is-dropup` 向上弹出，并支持键盘与鼠标双导航。
 */
export function bindUiSelect(rootEl, options = {}) {
  const {
    dropupThreshold = 190,
    boundaryEl = null,
    onChange = null,
    escStack = null,
    escId = null,
    onOpen = null,
  } = options;

  const trig = rootEl.querySelector('.ui-select-trigger');
  const menu = rootEl.querySelector('.ui-select-menu');
  const opts = Array.from(rootEl.querySelectorAll('.ui-select-option'));
  const txtEl = rootEl.querySelector('.ui-select-txt');
  const dotEl = rootEl.querySelector('.ui-select-trigger .ui-select-dot');
  if (!trig || !menu || !opts.length) return null;

  let focusIdx = Math.max(0, opts.findIndex(o => o.getAttribute('aria-selected') === 'true'));

  const setFocus = idx => {
    focusIdx = (idx + opts.length) % opts.length;
    opts.forEach((opt, i) => opt.classList.toggle('is-focused', i === focusIdx));
  };

  const close = () => {
    rootEl.classList.remove('is-open');
    menu.hidden = true;
    trig.setAttribute('aria-expanded', 'false');
    opts.forEach(opt => opt.classList.remove('is-focused'));
  };

  const open = () => {
    onOpen?.(rootEl);
    const tRect = trig.getBoundingClientRect();
    const bBottom = boundaryEl
      ? Math.min(window.innerHeight, boundaryEl.getBoundingClientRect().bottom)
      : window.innerHeight;
    rootEl.classList.toggle('is-dropup', bBottom - tRect.bottom < dropupThreshold);
    rootEl.classList.add('is-open');
    menu.hidden = false;
    trig.setAttribute('aria-expanded', 'true');
    setFocus(Math.max(0, opts.findIndex(o => o.getAttribute('aria-selected') === 'true')));
  };

  const setValue = (val, emit = false) => {
    const target = opts.find(o => o.dataset.val === String(val)) || opts[0];
    opts.forEach(o => o.setAttribute('aria-selected', String(o === target)));
    rootEl.dataset.value = target.dataset.val || '';
    const title = target.querySelector('.ui-select-option-title')?.textContent?.trim()
      || target.querySelector('span')?.textContent?.trim()
      || target.textContent.trim();
    if (txtEl) txtEl.textContent = title;
    if (dotEl && target.dataset.dot) dotEl.style.color = target.dataset.dot;
    if (emit) {
      const payload = { value: rootEl.dataset.value, label: title };
      onChange?.(payload.value, payload.label);
      rootEl.dispatchEvent(new CustomEvent('ui-select-change', { detail: payload }));
    }
    return rootEl.dataset.value;
  };

  Object.defineProperty(rootEl, 'value', {
    configurable: true,
    get: () => rootEl.dataset.value || '',
    set: v => setValue(v, false),
  });

  trig.addEventListener('click', e => {
    e.stopPropagation();
    if (menu.hidden) open();
    else close();
  });

  opts.forEach((opt, idx) => {
    opt.addEventListener('pointerenter', () => setFocus(idx));
    opt.addEventListener('click', e => {
      e.stopPropagation();
      setValue(opt.dataset.val, true);
      close();
      trig.focus();
    });
  });

  rootEl.addEventListener('keydown', e => {
    if (menu.hidden && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      open();
      return;
    }
    if (menu.hidden) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setFocus(focusIdx + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setFocus(focusIdx - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setValue(opts[focusIdx].dataset.val, true);
      close();
      trig.focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      trig.focus();
    }
  });

  const api = Object.freeze({
    el: rootEl,
    open,
    close,
    isOpen: () => !menu.hidden,
    setValue,
    getValue: () => rootEl.dataset.value || '',
    focusTrigger: () => trig.focus(),
  });

  if (escStack) {
    const key = escId || rootEl.id || `ui-select-${Math.random().toString(36).slice(2, 8)}`;
    escStack.register(key, api.isOpen, () => {
      api.close();
      api.focusTrigger();
    });
  }

  return api;
}

/**
 * 批量绑定容器内的全部 `.ui-select` 实例，并挂载一次全局空白点击收起。
 */
export function bindAllUiSelects(root = document, options = {}) {
  const instances = [];
  const closeAll = (except = null) => {
    for (const inst of instances) {
      if (inst.el !== except) inst.close();
    }
  };

  root.querySelectorAll('.ui-select').forEach((box, idx) => {
    const inst = bindUiSelect(box, {
      ...options,
      escId: box.id ? `select:${box.id}` : `select:#${idx}`,
      onOpen: target => closeAll(target),
    });
    if (inst) instances.push(inst);
  });

  if (options.escStack && instances.length) {
    options.escStack.register(
      'ui-select-global',
      () => instances.some(s => s.isOpen()),
      () => {
        const opened = instances.find(s => s.isOpen());
        if (opened) {
          opened.close();
          opened.focusTrigger();
        }
      },
    );
  }

  const onDocClick = () => closeAll();
  document.addEventListener('click', onDocClick);

  return Object.freeze({
    instances,
    closeAll,
    destroy: () => document.removeEventListener('click', onDocClick),
  });
}

/**
 * 创建三档原生物理尺码控制器（compact / lg / xl）。
 * 在全屏下默认标准大号 `lg`；切入 16:9 / 21:9 680p 框时自动切换 `compact` 防止挤压。
 */
export function createUiSizeController(stageEl, options = {}) {
  const {
    defaultSize = 'lg',
    segContainer = null,
    onChange = null,
  } = options;

  let currentSize = VALID_SIZES.has(stageEl.dataset.uiSize) ? stageEl.dataset.uiSize : defaultSize;
  let savedDesktopSize = currentSize === 'compact' ? 'lg' : currentSize;

  function syncSegButtons(activeSize) {
    if (!segContainer) return;
    segContainer.querySelectorAll('[data-size]').forEach(btn => {
      const hit = btn.dataset.size === activeSize;
      btn.classList.toggle('active', hit);
      btn.setAttribute('aria-pressed', String(hit));
    });
  }

  function setSize(size, opts = {}) {
    const { silent = false, isAuto = false } = opts;
    if (!VALID_SIZES.has(size)) return currentSize;
    currentSize = size;
    stageEl.dataset.uiSize = size;
    if (!isAuto && (stageEl.dataset.aspect || 'full') === 'full') {
      savedDesktopSize = size;
    }
    syncSegButtons(size);
    if (!silent) {
      onChange?.({ size, label: UI_SIZE_LABELS[size], isAuto });
    }
    return currentSize;
  }

  function syncForAspect(aspect, opts = {}) {
    stageEl.dataset.aspect = aspect;
    const next = aspect === 'full' ? savedDesktopSize : 'compact';
    return setSize(next, { silent: opts.silent ?? false, isAuto: true });
  }

  if (segContainer) {
    segContainer.addEventListener('click', e => {
      const btn = e.target.closest('[data-size]');
      if (btn) setSize(btn.dataset.size, { silent: false, isAuto: false });
    });
  }

  setSize(currentSize, { silent: true, isAuto: true });

  return Object.freeze({
    getSize: () => currentSize,
    getSavedDesktopSize: () => savedDesktopSize,
    setSize,
    syncForAspect,
  });
}

/* ==========================================================================
   V2.2 持久化偏好、分级 Toast 通知管理与离线 Web Audio 交互音效契约
   ========================================================================== */
export const UI_PREF_STORAGE_KEY = 'scavenger.ui.prefs.v2';
const VALID_DOF_MODES = new Set(['off', 'soft', 'standard']);
export const DEFAULT_UI_PREFS = Object.freeze({
  uiSize: 'lg',
  aspect: '16:9',
  bloomEnabled: true,
  dofMode: 'soft',
  sfxEnabled: true,
  volume: 0.35,
  vehicleAudioEnabled: true,
  vehicleAudioVolume: 0.45,
});

function normalizeUiPrefs(raw = {}) {
  return {
    uiSize: VALID_SIZES.has(raw.uiSize) ? raw.uiSize : DEFAULT_UI_PREFS.uiSize,
    aspect: raw.aspect === '21:9' ? '21:9' : '16:9',
    bloomEnabled: typeof raw.bloomEnabled === 'boolean' ? raw.bloomEnabled : DEFAULT_UI_PREFS.bloomEnabled,
    dofMode: VALID_DOF_MODES.has(raw.dofMode) ? raw.dofMode : DEFAULT_UI_PREFS.dofMode,
    sfxEnabled: typeof raw.sfxEnabled === 'boolean' ? raw.sfxEnabled : DEFAULT_UI_PREFS.sfxEnabled,
    volume: Number.isFinite(Number(raw.volume)) ? Math.min(1, Math.max(0, Number(raw.volume))) : DEFAULT_UI_PREFS.volume,
    vehicleAudioEnabled: typeof raw.vehicleAudioEnabled === 'boolean' ? raw.vehicleAudioEnabled : DEFAULT_UI_PREFS.vehicleAudioEnabled,
    vehicleAudioVolume: Number.isFinite(Number(raw.vehicleAudioVolume)) ? Math.min(1, Math.max(0, Number(raw.vehicleAudioVolume))) : DEFAULT_UI_PREFS.vehicleAudioVolume,
  };
}

export function loadUiPrefs() {
  try {
    const raw = localStorage.getItem(UI_PREF_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_UI_PREFS };
    return normalizeUiPrefs(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_UI_PREFS };
  }
}

export function saveUiPrefs(patch = {}) {
  const next = normalizeUiPrefs({ ...loadUiPrefs(), ...patch });
  try {
    localStorage.setItem(UI_PREF_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 隐私模式或只读上下文静默忽略
  }
  return next;
}

export function createToastManager(containerEl, options = {}) {
  const { maxItems = 4, durationMs = 2600 } = options;

  function push(title, desc = '', tone = 'select') {
    if (!containerEl) return null;
    while (containerEl.children.length >= maxItems) {
      containerEl.firstElementChild?.remove();
    }
    const item = document.createElement('div');
    item.className = 'ui-toast';
    item.dataset.tone = tone;
    const dot = document.createElement('span');
    dot.className = 'ui-toast-dot';
    dot.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = String(title || '');
    body.append(strong);
    if (desc) {
      const span = document.createElement('span');
      span.textContent = String(desc);
      body.append(span);
    }
    item.append(dot, body);
    containerEl.append(item);
    window.setTimeout(() => {
      item.remove();
    }, durationMs);
    return item;
  }

  return Object.freeze({ push });
}

let sharedAudioCtx = null;
const SFX_RECIPES = Object.freeze({
  hover:   { freq: 620, endFreq: 760, dur: 0.045, type: 'triangle', gain: 0.16 },
  confirm: { freq: 520, endFreq: 880, dur: 0.11,  type: 'triangle', gain: 0.28 },
  toggle:  { freq: 440, endFreq: 660, dur: 0.075, type: 'sine',     gain: 0.24 },
  denied:  { freq: 190, endFreq: 130, dur: 0.14,  type: 'sawtooth', gain: 0.24 },
  alert:   { freq: 740, endFreq: 520, dur: 0.18,  type: 'square',   gain: 0.20 },
  back:    { freq: 420, endFreq: 260, dur: 0.085, type: 'sine',     gain: 0.22 },
});

export function playUiSfx(kind = 'confirm', options = {}) {
  try {
    const prefs = loadUiPrefs();
    const enabled = options.enabled ?? prefs.sfxEnabled;
    const vol = Number.isFinite(Number(options.volume)) ? Number(options.volume) : prefs.volume;
    if (!enabled || vol <= 0.001) return false;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return false;
    if (!sharedAudioCtx) sharedAudioCtx = new Ctor();
    if (sharedAudioCtx.state === 'suspended' && options.resume) {
      sharedAudioCtx.resume().catch(() => {});
    }
    if (sharedAudioCtx.state !== 'running' && !options.resume) return false;
    const r = SFX_RECIPES[kind] || SFX_RECIPES.confirm;
    const t0 = sharedAudioCtx.currentTime;
    const osc = sharedAudioCtx.createOscillator();
    const gain = sharedAudioCtx.createGain();
    osc.type = r.type;
    osc.frequency.setValueAtTime(r.freq, t0);
    osc.frequency.exponentialRampToValueAtTime(Math.max(40, r.endFreq), t0 + r.dur);
    gain.gain.setValueAtTime(Math.max(0.0001, r.gain * vol), t0);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + r.dur);
    osc.connect(gain);
    gain.connect(sharedAudioCtx.destination);
    osc.start(t0);
    osc.stop(t0 + r.dur + 0.01);
    return true;
  } catch {
    return false;
  }
}

const cmdPageAnimStates = new WeakMap();

/**
 * 同步右下角竖向翻页器滑动指示条的轨道数量、当前索引与通道色调。
 * 就地更新 CSS 变量而非重建节点，以保留平滑过渡动画。
 */
export function syncCmdPagerIndicator(pagerEl, { activeIndex = 0, count = 2, activeTone = 'select' } = {}) {
  if (!pagerEl) return null;
  const total = Math.max(1, Number(count) || 1);
  const clamped = Math.max(0, Math.min(total - 1, Number(activeIndex) || 0));
  let indicator = pagerEl.querySelector('.ui-cmd-pager-indicator');
  if (!indicator) {
    indicator = document.createElement('span');
    indicator.className = 'ui-cmd-pager-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    pagerEl.prepend(indicator);
  }
  pagerEl.classList.add('has-indicator');
  pagerEl.style.setProperty('--pager-count', String(total));
  pagerEl.style.setProperty('--pager-index', String(clamped));
  pagerEl.dataset.activeTone = activeTone || 'select';
  return indicator;
}

/**
 * 为方案 B 命令盘执行可中断的方向性切页动画（纯 translateY + opacity，支持 reduced-motion）。
 */
export function runCmdPageTransition(gridEl, { direction = 'down', exitLayerEl = null, renderContent }) {
  if (!gridEl || typeof renderContent !== 'function') return;
  const dir = direction === 'up' ? 'up' : 'down';
  let state = cmdPageAnimStates.get(gridEl);
  if (!state) {
    state = { enterTimer: 0, exitTimer: 0, token: 0 };
    cmdPageAnimStates.set(gridEl, state);
  }
  state.token += 1;
  const myToken = state.token;
  if (state.enterTimer) {
    window.clearTimeout(state.enterTimer);
    state.enterTimer = 0;
  }
  if (state.exitTimer) {
    window.clearTimeout(state.exitTimer);
    state.exitTimer = 0;
  }
  if (exitLayerEl) {
    exitLayerEl.replaceChildren();
  }

  const prefersReduced = Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);

  if (!prefersReduced && exitLayerEl && gridEl.children.length > 0) {
    const snap = document.createElement('div');
    snap.className = 'ui-cmd-exit-snapshot ui-cmd-grid is-partitioned';
    snap.setAttribute('aria-hidden', 'true');
    for (const child of Array.from(gridEl.children)) {
      const cloned = child.cloneNode(true);
      if (cloned.removeAttribute) cloned.removeAttribute('id');
      cloned.querySelectorAll?.('[id]').forEach(node => node.removeAttribute('id'));
      if (cloned.tagName === 'BUTTON') cloned.setAttribute('tabindex', '-1');
      cloned.querySelectorAll?.('button').forEach(btn => btn.setAttribute('tabindex', '-1'));
      snap.append(cloned);
    }
    snap.classList.add(dir === 'down' ? 'is-exiting-up' : 'is-exiting-down');
    exitLayerEl.append(snap);
    const cleanupExit = () => {
      if (state.token !== myToken) return;
      if (state.exitTimer) {
        window.clearTimeout(state.exitTimer);
        state.exitTimer = 0;
      }
      if (snap.parentNode === exitLayerEl) snap.remove();
    };
    snap.addEventListener('animationend', cleanupExit, { once: true });
    state.exitTimer = window.setTimeout(cleanupExit, 240);
  }

  renderContent();

  gridEl.querySelectorAll('.ui-cmd-wep-card').forEach((card, idx) => {
    card.style.setProperty('--cmd-idx', String(idx));
  });
  gridEl.dataset.cmdPageDir = dir;
  gridEl.classList.remove('is-entering-from-bottom', 'is-entering-from-top');

  if (prefersReduced) {
    gridEl.dataset.cmdPageAnim = 'idle';
    return;
  }

  void gridEl.offsetWidth;
  const enterCls = dir === 'down' ? 'is-entering-from-bottom' : 'is-entering-from-top';
  gridEl.classList.add(enterCls);
  gridEl.dataset.cmdPageAnim = 'running';
  state.enterTimer = window.setTimeout(() => {
    if (state.token !== myToken) return;
    state.enterTimer = 0;
    gridEl.classList.remove('is-entering-from-bottom', 'is-entering-from-top');
    gridEl.dataset.cmdPageAnim = 'idle';
  }, 260);
}

/* ==========================================================================
   V2.2 方案 A：连续八扇区军工仪表盘战术轮盘（纯几何规则 + 共享 SVG 渲染器）
   ========================================================================== */
export const TACTICAL_WHEEL_CONFIG = Object.freeze({
  slotCount: 8,
  deadzonePx: 38,
  innerRadius: 55,
  outerRadius: 134,
  visualRadius: 148,
  edgePaddingPx: 16,
  gapDeg: 2.0,
  sectorAngles: Object.freeze([-90, -45, 0, 45, 90, 135, 180, -135]),
  compassLabels: Object.freeze(['000°', '045°', '090°', '135°', '180°', '225°', '270°', '315°']),
});

function degToRad(deg) {
  return (deg * Math.PI) / 180;
}

function escapeXmlText(raw) {
  return String(raw ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * 纯规则：由相对向量 (dx, dy) 计算无限外延 45° 扇区索引（死区内返回 index: -1）。
 */
export function resolveWheelSectorFromVector(dx, dy, deadzonePx = TACTICAL_WHEEL_CONFIG.deadzonePx) {
  const vx = Number(dx) || 0;
  const vy = Number(dy) || 0;
  const dz = Math.max(0, Number(deadzonePx) || 0);
  const dist = Math.hypot(vx, vy);
  if (dist < dz) {
    return { index: -1, dist, angleDeg: 0, inDeadzone: true };
  }
  const angleDeg = (Math.atan2(vy, vx) * 180) / Math.PI;
  const rel = (((angleDeg + 90 + 22.5) % 360) + 360) % 360;
  const index = Math.floor(rel / 45) % 8;
  return { index, dist, angleDeg, inDeadzone: false };
}

/**
 * 纯规则：按轮盘实际外径与视口安全边距自动内推视觉圆心，确保 16:9 / 21:9 四角零裁切。
 */
export function clampWheelVisualCenter(originPt, boundsW, boundsH, options = {}) {
  const w = Math.max(1, Number(boundsW) || 1208);
  const h = Math.max(1, Number(boundsH) || 680);
  const ox = Number.isFinite(originPt?.x) ? originPt.x : w * 0.5;
  const oy = Number.isFinite(originPt?.y) ? originPt.y : h * 0.5;
  const outerRadius = Number.isFinite(options.outerRadius) ? options.outerRadius : TACTICAL_WHEEL_CONFIG.visualRadius;
  const safePadding = Number.isFinite(options.safePadding) ? options.safePadding : TACTICAL_WHEEL_CONFIG.edgePaddingPx;
  const pad = outerRadius + safePadding;
  const minX = Math.min(w * 0.5, pad);
  const maxX = Math.max(w * 0.5, w - pad);
  const minY = Math.min(h * 0.5, pad);
  const maxY = Math.max(h * 0.5, h - pad);
  const x = Math.max(minX, Math.min(maxX, ox));
  const y = Math.max(minY, Math.min(maxY, oy));
  const offsetX = x - ox;
  const offsetY = y - oy;
  return {
    x,
    y,
    offsetX,
    offsetY,
    clamped: Math.abs(offsetX) > 0.5 || Math.abs(offsetY) > 0.5,
  };
}

/**
 * 纯规则：释放或点击提交前重新校验槽位可执行性（拒绝空槽、禁用态、冷却态）。
 */
export function canCommitWheelSlot(slot) {
  if (!slot || typeof slot !== 'object' || slot.empty || slot.status === 'empty') {
    return { ok: false, status: 'empty', reason: '当前兵种无对应指令' };
  }
  if (slot.status === 'disabled' || slot.disabled) {
    return { ok: false, status: 'disabled', reason: slot.reason || '当前指令不可用' };
  }
  const cdRem = Number(slot.cooldownSec) || 0;
  if (slot.status === 'cooling' || cdRem > 0.05) {
    return {
      ok: false,
      status: 'cooling',
      reason: slot.reason || `冷却中 ${Math.max(0.1, cdRem).toFixed(1)}s`,
    };
  }
  return { ok: true, status: slot.status === 'active' ? 'active' : 'ready', reason: '' };
}

/**
 * 纯几何：生成 SVG 环形扇区路径（rIn ~ rOut，startDeg -> endDeg）。
 */
export function buildWheelAnnularSectorPath(rIn, rOut, startDeg, endDeg) {
  const a0 = degToRad(startDeg);
  const a1 = degToRad(endDeg);
  const x0 = rOut * Math.cos(a0);
  const y0 = rOut * Math.sin(a0);
  const x1 = rOut * Math.cos(a1);
  const y1 = rOut * Math.sin(a1);
  const x2 = rIn * Math.cos(a1);
  const y2 = rIn * Math.sin(a1);
  const x3 = rIn * Math.cos(a0);
  const y3 = rIn * Math.sin(a0);
  const largeArc = Math.abs(endDeg - startDeg) > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${rOut} ${rOut} 0 ${largeArc} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} L ${x2.toFixed(2)} ${y2.toFixed(2)} A ${rIn} ${rIn} 0 ${largeArc} 0 ${x3.toFixed(2)} ${y3.toFixed(2)} Z`;
}

/**
 * 纯几何：生成 SVG 圆弧路径。
 */
export function buildWheelArcPath(r, startDeg, endDeg) {
  const a0 = degToRad(startDeg);
  const a1 = degToRad(endDeg);
  const x0 = r * Math.cos(a0);
  const y0 = r * Math.sin(a0);
  const x1 = r * Math.cos(a1);
  const y1 = r * Math.sin(a1);
  const largeArc = Math.abs(endDeg - startDeg) > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function buildWheelIconGroupMarkup(iconType, iconName, cx, cy, size, color) {
  const scale = size / 24;
  const tx = (cx - size * 0.5).toFixed(2);
  const ty = (cy - size * 0.5).toFixed(2);
  if (iconType === 'weapon') {
    const resolved = resolveWeaponIconKind(iconName);
    const d = WEAPON_ICONS[resolved]?.d || WEAPON_ICONS.cannon.d;
    return `<g transform="translate(${tx},${ty}) scale(${scale.toFixed(4)})"><path d="${d}" fill="${color}" fill-rule="evenodd"/></g>`;
  }
  const strokes = HUD_ICONS[iconName] || HUD_ICONS.focus;
  const inner = strokes.map(d => `<path d="${d}"/>`).join('');
  return `<g transform="translate(${tx},${ty}) scale(${scale.toFixed(4)})" fill="none" stroke="${color}" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">${inner}</g>`;
}

function resolveWheelToneColor(tone, pal) {
  if (tone === 'ground') return pal.ground;
  if (tone === 'heli') return pal.heli;
  if (tone === 'plane') return pal.plane;
  if (tone === 'danger') return pal.danger;
  if (tone === 'warn') return pal.warn;
  if (tone === 'safe') return pal.safe;
  if (tone === 'select') return pal.select;
  return pal.textSecondary;
}

function resolveWheelCenterReadout(model, slots, hoveredIndex, interactionMode, pal) {
  const eyebrowBase = model?.eyebrow || '战术轮盘';
  const eyebrowColor = resolveWheelToneColor(model?.eyebrowTone || 'select', pal);
  if (hoveredIndex < 0 || hoveredIndex >= 8) {
    const isFlick = interactionMode === 'hold_flick';
    return {
      eyebrow: eyebrowBase,
      eyebrowColor,
      title: isFlick ? '死区取消' : '战术轮盘',
      sub: isFlick ? '松开取消 · 划向扇区' : '点击触发 · ESC',
      subColor: pal.textMuted,
      ringColor: 'rgba(255, 255, 255, 0.28)',
    };
  }
  const slot = slots[hoveredIndex];
  if (!slot || slot.empty || slot.status === 'empty') {
    return {
      eyebrow: `${eyebrowBase} · ${TACTICAL_WHEEL_CONFIG.compassLabels[hoveredIndex]}`,
      eyebrowColor: pal.textMuted,
      title: '空置槽位',
      sub: '当前兵种无对应指令',
      subColor: pal.textMuted,
      ringColor: 'rgba(255, 255, 255, 0.16)',
    };
  }
  const commit = canCommitWheelSlot(slot);
  if (commit.status === 'disabled') {
    return {
      eyebrow: `${slot.key || '-'} · 不可用`,
      eyebrowColor: pal.danger,
      title: slot.label || '',
      sub: commit.reason,
      subColor: pal.danger,
      ringColor: pal.danger,
    };
  }
  if (commit.status === 'cooling') {
    return {
      eyebrow: `${slot.key || '-'} · 冷却中`,
      eyebrowColor: pal.warn,
      title: slot.label || '',
      sub: `${commit.reason} · 不可触发`,
      subColor: pal.warn,
      ringColor: pal.warn,
    };
  }
  if (commit.status === 'active') {
    return {
      eyebrow: `${slot.key || '-'} · 已激活`,
      eyebrowColor: pal.safe,
      title: slot.label || '',
      sub: slot.activeHint || (interactionMode === 'hold_flick' ? '松开 Space 切换' : '点击切换状态'),
      subColor: pal.safe,
      ringColor: pal.select,
    };
  }
  return {
    eyebrow: `${slot.key || '-'} · ${slot.modeTag || '指令'}`,
    eyebrowColor: pal.select,
    title: slot.label || '',
    sub: slot.readyHint || (interactionMode === 'hold_flick' ? '松开 Space 执行' : '点击立即执行'),
    subColor: pal.selectInner,
    ringColor: pal.select,
  };
}

let wheelHatchUid = 0;

/**
 * 共享方案 A（连续八扇区军工仪表盘）SVG 渲染器。
 * 供 `/play` 实战轮盘与 `/ui-spec` 活体规范台 100% 同源复用；零 backdrop-filter、零 transform:scale、零 UI 提示音。
 */
export function renderTacticalWheel(containerEl, model = {}, options = {}) {
  if (!containerEl) return null;
  const pal = readSemanticPalette();
  const slots = Array.from({ length: 8 }, (_, i) => (Array.isArray(model?.slots) ? model.slots[i] || null : null));
  const hoveredIndex = Number.isInteger(options.hoveredIndex) ? options.hoveredIndex : -1;
  const interactionMode = options.interactionMode || 'tap_click';
  const hasPointerAngle = Boolean(options.hasPointerAngle);
  const pointerAngleDeg = Number.isFinite(options.pointerAngleDeg) ? options.pointerAngleDeg : 0;
  const hatchId = containerEl.dataset.wheelHatchId || `ui-wheel-hatch-${++wheelHatchUid}`;
  containerEl.dataset.wheelHatchId = hatchId;

  const { innerRadius: rIn, outerRadius: rOut, gapDeg, sectorAngles } = TACTICAL_WHEEL_CONFIG;
  const center = resolveWheelCenterReadout(model, slots, hoveredIndex, interactionMode, pal);

  let outerTicks = `<circle cx="0" cy="0" r="${rOut + 6}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>`;
  for (let a = 0; a < 360; a += 15) {
    const isBoundary = ((a + 22.5) % 45) === 0;
    const r1 = rOut + 3;
    const r2 = rOut + (isBoundary ? 9 : 6);
    const rad = degToRad(a);
    outerTicks += `<line x1="${(r1 * Math.cos(rad)).toFixed(1)}" y1="${(r1 * Math.sin(rad)).toFixed(1)}" x2="${(r2 * Math.cos(rad)).toFixed(1)}" y2="${(r2 * Math.sin(rad)).toFixed(1)}" stroke="${isBoundary ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.10)'}" stroke-width="${isBoundary ? 1.2 : 1}"/>`;
  }

  const sectorMarkup = sectorAngles.map((cDeg, idx) => {
    const slot = slots[idx];
    const a0 = cDeg - 22.5 + gapDeg * 0.5;
    const a1 = cDeg + 22.5 - gapDeg * 0.5;
    const isHov = hoveredIndex === idx;
    const pathD = buildWheelAnnularSectorPath(rIn, rOut, a0, a1);

    if (!slot || slot.empty || slot.status === 'empty') {
      return `
        <g class="ui-wheel-slot is-empty${isHov ? ' is-hovered' : ''}" data-sector-index="${idx}" data-slot-state="empty" role="menuitem" aria-disabled="true">
          <path class="ui-wheel-sector-bg" d="${pathD}" fill="rgba(13,14,20,0.52)" stroke="${isHov ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.06)'}" stroke-width="1"/>
          <path d="${buildWheelArcPath((rIn + rOut) * 0.5, cDeg - 7, cDeg + 7)}" fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="1.5" stroke-dasharray="2 3"/>
        </g>`;
    }

    const commit = canCommitWheelSlot(slot);
    const isDis = commit.status === 'disabled';
    const isCool = commit.status === 'cooling';
    const isAct = commit.status === 'active';
    const isDanger = slot.tone === 'danger';

    let fill = 'rgba(19, 20, 28, 0.94)';
    let stroke = 'rgba(255, 255, 255, 0.16)';
    let strokeW = 1;
    if (isDis) {
      fill = 'rgba(14, 15, 22, 0.92)';
      stroke = 'rgba(255, 82, 102, 0.26)';
    } else if (isAct) {
      fill = 'rgba(20, 34, 34, 0.95)';
      stroke = 'rgba(60, 214, 159, 0.58)';
    } else if (isDanger) {
      fill = 'rgba(34, 18, 24, 0.94)';
      stroke = 'rgba(255, 82, 102, 0.36)';
    }
    if (isHov) {
      if (isDis || isCool) {
        fill = 'rgba(42, 19, 26, 0.96)';
        stroke = pal.danger;
        strokeW = 1.8;
      } else {
        fill = 'rgba(46, 41, 24, 0.96)';
        stroke = pal.select;
        strokeW = 1.8;
      }
    }

    const rad = degToRad(cDeg);
    const midR = 91;
    const mx = midR * Math.cos(rad);
    const my = midR * Math.sin(rad);
    // 严格 8 方位同心正八点外弧骑缝微键帽（r = rOut = 134，零角度偏移）
    const kbdR = rOut;
    const kx = kbdR * Math.cos(rad);
    const ky = kbdR * Math.sin(rad);

    const iconColor = isDis
      ? '#646a7a'
      : (isHov && !isCool)
        ? pal.selectInner
        : isAct
          ? pal.safe
          : isDanger
            ? pal.danger
            : (slot.tone && slot.tone !== 'neutral' ? resolveWheelToneColor(slot.tone, pal) : '#d8dce6');
    const textColor = isDis ? '#757b8c' : (isHov && !isCool ? pal.selectInner : pal.textPrimary);

    const activeInnerArc = isAct
      ? `<path class="ui-wheel-active-arc" d="${buildWheelAnnularSectorPath(rIn + 1.5, rIn + 6.5, a0 + 1.5, a1 - 1.5)}" fill="${pal.safe}"/>`
      : '';
    const hoverOuterArc = isHov
      ? `<path class="ui-wheel-hover-arc" d="${buildWheelArcPath(rOut + 1.5, a0, a1)}" fill="none" stroke="${(isDis || isCool) ? pal.danger : pal.select}" stroke-width="3.2" stroke-linecap="round"/>`
      : '';

    let coolOverlay = '';
    if (isCool) {
      const cdRem = Math.max(0.05, Number(slot.cooldownSec) || 0.1);
      const cdTotal = Math.max(cdRem, Number(slot.cooldownTotalSec) || cdRem);
      const ratio = Math.max(0.05, Math.min(1, cdRem / cdTotal));
      const sweepEnd = a0 + (a1 - a0) * ratio;
      coolOverlay = `
        <path d="${buildWheelAnnularSectorPath(rIn + 1, rOut - 1, a0, sweepEnd)}" fill="rgba(255, 184, 0, 0.22)"/>
        <path d="${buildWheelArcPath(rIn + 4, a0, sweepEnd)}" fill="none" stroke="${pal.warn}" stroke-width="3"/>`;
    }

    const hatchOverlay = isDis ? `<path d="${pathD}" fill="url(#${hatchId})"/>` : '';
    const kbdBg = isHov && !isDis && !isCool ? pal.select : '#0d0e13';
    const kbdFg = isHov && !isDis && !isCool ? '#0d0e13' : pal.textSecondary;
    const kbdStroke = isHov
      ? ((isDis || isCool) ? pal.danger : pal.select)
      : (isAct ? pal.safe : 'rgba(255,255,255,0.28)');
    const subText = isCool
      ? `${(Number(slot.cooldownSec) || 0).toFixed(1)}s`
      : isDis
        ? (slot.charge === '0' ? '0 发' : '禁用')
        : (slot.charge || '');
    const subColor = isCool ? pal.warn : isDis ? pal.danger : (isAct ? pal.safe : '#9aa0b0');

    const cls = [
      'ui-wheel-slot',
      isHov ? 'is-hovered' : '',
      isAct ? 'is-active' : '',
      isCool ? 'is-cooling' : '',
      isDis ? 'is-disabled' : '',
      isDanger ? 'is-danger' : '',
    ].filter(Boolean).join(' ');

    const keyLabel = escapeXmlText(slot.key || '');
    const mainLabel = escapeXmlText(slot.label || '');
    const subLabel = escapeXmlText(subText);
    const hasSub = Boolean(subLabel);
    const iconY = hasSub ? my - 13 : my - 8;
    const labelY = hasSub ? my + 6 : my + 11;
    const subY = my + 18;
    const kbdW = keyLabel.length >= 2 ? 19 : 14;
    const kbdH = 13;

    return `
      <g class="${cls}" data-sector-index="${idx}" data-wheel-action="${escapeXmlText(slot.id || '')}" data-slot-state="${commit.status}" role="menuitem" aria-disabled="${isDis || isCool ? 'true' : 'false'}">
        <path class="ui-wheel-sector-bg" d="${pathD}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeW}"/>
        ${hatchOverlay}
        ${coolOverlay}
        ${activeInnerArc}
        ${hoverOuterArc}
        ${buildWheelIconGroupMarkup(slot.iconType || 'hud', slot.iconName || 'focus', mx, iconY, 17, iconColor)}
        <text x="${mx.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle" fill="${textColor}" font-size="11" font-weight="800" font-family="var(--ui-font-sans)">${mainLabel}</text>
        ${hasSub ? `<text x="${mx.toFixed(1)}" y="${subY.toFixed(1)}" text-anchor="middle" fill="${subColor}" font-size="9.5" font-weight="800" font-family="var(--ui-font-mono)">${subLabel}</text>` : ''}
        ${keyLabel ? `<g class="ui-wheel-keycap" transform="translate(${kx.toFixed(1)},${ky.toFixed(1)})">
          <rect x="${(-kbdW * 0.5).toFixed(1)}" y="${(-kbdH * 0.5).toFixed(1)}" width="${kbdW}" height="${kbdH}" rx="3" fill="${kbdBg}" stroke="${kbdStroke}" stroke-width="1"/>
          <text x="0" y="2.9" text-anchor="middle" fill="${kbdFg}" font-size="8.5" font-weight="900" font-family="var(--ui-font-mono)">${keyLabel}</text>
        </g>` : ''}
      </g>`;
  }).join('');

  let pointerNeedle = '';
  if (hasPointerAngle && hoveredIndex >= 0) {
    const pr = degToRad(pointerAngleDeg);
    const p1x = (50 * Math.cos(pr)).toFixed(1);
    const p1y = (50 * Math.sin(pr)).toFixed(1);
    const p2x = (43 * Math.cos(pr - 0.14)).toFixed(1);
    const p2y = (43 * Math.sin(pr - 0.14)).toFixed(1);
    const p3x = (43 * Math.cos(pr + 0.14)).toFixed(1);
    const p3y = (43 * Math.sin(pr + 0.14)).toFixed(1);
    pointerNeedle = `<polygon class="ui-wheel-needle" points="${p1x},${p1y} ${p2x},${p2y} ${p3x},${p3y}" fill="${center.ringColor}"/>`;
  }

  const hubMarkup = `
    <g class="ui-wheel-center" data-wheel-cancel="1" role="button" aria-label="中心取消死区">
      <circle cx="0" cy="0" r="49" fill="#12131b" stroke="${center.ringColor}" stroke-width="1.6"/>
      <circle cx="0" cy="0" r="${TACTICAL_WHEEL_CONFIG.deadzonePx}" fill="none" stroke="rgba(255,255,255,0.09)" stroke-dasharray="3 3" stroke-width="1"/>
      ${pointerNeedle}
      <text x="0" y="-15" text-anchor="middle" fill="${center.eyebrowColor}" font-size="8.8" font-weight="800" font-family="var(--ui-font-mono)">${escapeXmlText(String(center.eyebrow || '').slice(0, 12))}</text>
      <text x="0" y="4" text-anchor="middle" fill="${pal.textPrimary}" font-size="13" font-weight="800" font-family="var(--ui-font-sans)">${escapeXmlText(center.title)}</text>
      <text x="0" y="18" text-anchor="middle" fill="${center.subColor}" font-size="8.6" font-weight="700" font-family="var(--ui-font-mono)">${escapeXmlText(String(center.sub || '').slice(0, 12))}</text>
    </g>`;

  containerEl.innerHTML = `
    <svg class="ui-wheel-svg" width="296" height="296" viewBox="-148 -148 296 296" role="menu" aria-label="8 槽战术指令轮盘">
      <defs>
        <pattern id="${hatchId}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
          <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(255,255,255,0.09)" stroke-width="2"/>
        </pattern>
      </defs>
      ${outerTicks}
      ${sectorMarkup}
      ${hubMarkup}
    </svg>`;

  if (typeof options.onSelect === 'function') {
    containerEl.querySelectorAll('.ui-wheel-slot').forEach(node => {
      node.addEventListener('click', e => {
        e.stopPropagation();
        const idx = Number(node.dataset.sectorIndex);
        options.onSelect(slots[idx] || null, idx);
      });
    });
  }
  if (typeof options.onCancel === 'function') {
    containerEl.querySelector('[data-wheel-cancel]')?.addEventListener('click', e => {
      e.stopPropagation();
      options.onCancel();
    });
  }
  return containerEl.querySelector('.ui-wheel-svg');
}

/**
 * 绘制战术轮盘边角内推「按键原点 → 视觉圆心」偏移虚线与无限外延甩选方向射线。
 */
export function renderTacticalWheelRay(raySvgEl, {
  open = false,
  originPt = null,
  visualCenter = null,
  pointerPt = null,
  hoveredIndex = -1,
  slots = [],
  isLiveFlick = false,
} = {}) {
  if (!raySvgEl) return;
  if (!open || !originPt || !visualCenter) {
    raySvgEl.innerHTML = '';
    return;
  }
  const pal = readSemanticPalette();
  const parts = [];
  const offsetDist = Math.hypot(visualCenter.x - originPt.x, visualCenter.y - originPt.y);
  if (offsetDist > 2) {
    parts.push(`
      <line x1="${originPt.x.toFixed(1)}" y1="${originPt.y.toFixed(1)}" x2="${visualCenter.x.toFixed(1)}" y2="${visualCenter.y.toFixed(1)}"
            stroke="${pal.ground}" stroke-opacity="0.52" stroke-width="1.2" stroke-dasharray="4 3"/>
      <g class="ui-wheel-origin-mark" transform="translate(${originPt.x.toFixed(1)},${originPt.y.toFixed(1)})">
        <circle cx="0" cy="0" r="3.5" fill="none" stroke="${pal.ground}" stroke-width="1.4"/>
        <line x1="-7" y1="0" x2="7" y2="0" stroke="${pal.ground}" stroke-width="1.1"/>
        <line x1="0" y1="-7" x2="0" y2="7" stroke="${pal.ground}" stroke-width="1.1"/>
      </g>`);
  }
  if (pointerPt && hoveredIndex >= 0) {
    const refCenter = isLiveFlick ? originPt : visualCenter;
    const dist = Math.hypot(pointerPt.x - refCenter.x, pointerPt.y - refCenter.y);
    if (dist >= TACTICAL_WHEEL_CONFIG.deadzonePx) {
      const slot = Array.isArray(slots) ? slots[hoveredIndex] : null;
      const commit = canCommitWheelSlot(slot);
      const rayCol = commit.status === 'empty'
        ? 'rgba(255,255,255,0.22)'
        : (commit.status === 'disabled' || commit.status === 'cooling')
          ? pal.danger
          : pal.select;
      parts.push(`
        <line x1="${refCenter.x.toFixed(1)}" y1="${refCenter.y.toFixed(1)}" x2="${pointerPt.x.toFixed(1)}" y2="${pointerPt.y.toFixed(1)}"
              stroke="${rayCol}" stroke-opacity="0.72" stroke-width="1.5" stroke-dasharray="5 4"/>
        <circle cx="${pointerPt.x.toFixed(1)}" cy="${pointerPt.y.toFixed(1)}" r="3.5" fill="${rayCol}"/>`);
    }
  }
  raySvgEl.innerHTML = parts.join('');
}

/* ==========================================================================
   V2.2 跨页共享载具参数详情组件（.ui-vd-card 完整/窄栏档 + .ui-vd-hover 悬停精简档）
   ========================================================================== */
function formatVdClock(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function buildVdVehicleSvg(kind, size = 15) {
  const d = VEHICLE_SILHOUETTES[kind] || VEHICLE_SILHOUETTES.mbt;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="${d}"/></svg>`;
}

function buildVdWeaponSvg(rawKind, size = 14) {
  const key = resolveWeaponIconKind(rawKind);
  const d = (WEAPON_ICONS[key] || WEAPON_ICONS.cannon).d;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="${d}"/></svg>`;
}

function buildVdPipsHtml(score, tone = 'select') {
  const lit = Math.max(1, Math.min(5, Math.round((Number(score) || 0) / 2)));
  const t = escapeXmlText(tone || 'select');
  let html = `<span class="ui-vd-pips" aria-label="${Math.round(Number(score) || 0)} / 10">`;
  for (let i = 0; i < 5; i += 1) {
    html += `<i class="${i < lit ? 'is-lit' : ''}" data-tone="${t}"></i>`;
  }
  return `${html}</span>`;
}

/**
 * 渲染共享载具参数详情卡（同时服务 /play 右下舱上浮宽体卡、/armory 右侧窄栏与 /ui-spec 活体演示）。
 */
export function renderVehicleDetailCard(containerEl, spec, options = {}) {
  if (!containerEl || !spec) return null;
  const {
    narrow = false,
    showClose = true,
    foldOpen = false,
    onClose = null,
    onWeaponClick = null,
  } = options;

  const isArmory = spec.mode === 'armory';
  const isPlane = Boolean(spec.usesFuel && spec.fuel);
  const channel = spec.channel || 'ground';
  const fuel = isPlane ? spec.fuel : null;
  const fuelIsBingo = Boolean(isPlane && !isArmory && (fuel.bingoState === 'bingo' || fuel.bingoState === 'rtb_bingo'));
  const fuelIsLow = Boolean(isPlane && !isArmory && !fuelIsBingo && fuel.bingoState === 'low');

  let statusLbl = '机体耐久 (HP)';
  let statusVal = '';
  let statusValCls = '';
  let barPct = 100;
  let barColor = `var(--ui-ch-${channel})`;
  let bonusHtml = '';
  let bingoTickHtml = '';

  if (isPlane && fuel) {
    barPct = Math.max(0, Math.min(100, Math.round(fuel.pct ?? 100)));
    const rateTxt = Number(fuel.burnRate || 1).toFixed(2).replace(/\.00$/, '.0');
    statusLbl = isArmory
      ? '战术燃油 · 满油巡航'
      : `战术燃油 · ${escapeXmlText(fuel.modeLabel || '巡航')} ${rateTxt}×`;
    statusVal = isArmory
      ? `100% · ${formatVdClock(fuel.maxSec)} (红线 ${Math.round(fuel.bingoThresholdPct)}%)`
      : `${barPct}% · ${formatVdClock(fuel.remainSec)}${fuelIsBingo ? ' [BINGO]' : ` (红线 ${Math.round(fuel.bingoThresholdPct)}%)`}`;
    barColor = fuelIsBingo ? 'var(--ui-state-danger)' : (fuelIsLow ? 'var(--ui-state-warn)' : 'var(--ui-ch-plane)');
    statusValCls = fuelIsBingo ? 'is-danger' : (fuelIsLow ? 'is-warn' : 'is-plane');
    const tickLeft = Math.max(5, Math.min(95, Number(fuel.bingoThresholdPct) || 14));
    bingoTickHtml = `<div class="ui-vd-bingo-tick" style="left:${tickLeft}%" title="动态 Bingo 返航红线 ${Math.round(tickLeft)}%"></div>`;
  } else {
    const maxHp = Math.max(1, Number(spec.maxHp) || 100);
    const curHp = Math.max(0, Math.min(maxHp, Number.isFinite(spec.hp) ? spec.hp : maxHp));
    barPct = Math.round((curHp / maxHp) * 100);
    const bonus = Math.max(0, Math.min(40, Number(spec.bonusArmorPct) || 0));
    statusVal = `${curHp} / ${maxHp}${bonus > 0 ? ` +${bonus}%` : ''}`;
    barColor = barPct < 25 ? 'var(--ui-state-danger)' : `var(--ui-ch-${channel})`;
    statusValCls = barPct < 25 ? 'is-danger' : '';
    if (bonus > 0) {
      bonusHtml = `<div class="ui-vd-bonus" style="left:${Math.max(0, 100 - bonus)}%;width:${bonus}%"></div>`;
    }
  }

  const extraBadge = (isPlane && !narrow)
    ? `<span class="ui-badge" data-tone="${fuelIsBingo ? 'danger' : (fuelIsLow ? 'warn' : 'plane')}">${fuelIsBingo ? '低油返航' : `整备 ${fuel.rearmCooldownSec}s`}</span>`
    : '';

  const kpis = Array.isArray(spec.kpis) ? spec.kpis.slice(0, 4) : [];
  const caps = Array.isArray(spec.caps) ? spec.caps.slice(0, 3) : [];
  const weapons = Array.isArray(spec.weapons) ? spec.weapons : [];
  const foldMeta = Array.isArray(spec.foldMeta) ? spec.foldMeta : [];

  const innerHtml = `
    <div class="ui-vd-head">
      <div class="ui-vd-id">
        <span class="ui-vd-ico">${buildVdVehicleSvg(spec.iconKind, 15)}</span>
        <div class="ui-vd-titles">
          <strong>${escapeXmlText(spec.shortName || spec.slug)}</strong>
          <small>${escapeXmlText(spec.subTitle || spec.channelLabel || '')}</small>
        </div>
      </div>
      <div class="ui-vd-badges">
        <span class="ui-badge" data-tone="${escapeXmlText(channel)}">${escapeXmlText(spec.channelLabel || channel)}</span>
        <span class="ui-badge" data-tone="select">${Math.round(Number(spec.cost) || 0)} pt</span>
        ${extraBadge}
        ${showClose ? '<button type="button" class="ui-vd-close" data-vd-close="1" title="收起 [I / Esc]" aria-label="收起载具详情卡">✕</button>' : ''}
      </div>
    </div>

    <div class="ui-vd-status">
      <div class="ui-vd-status-head">
        <span class="ui-vd-status-lbl">${statusLbl}</span>
        <span class="ui-vd-status-val ${statusValCls}">${escapeXmlText(statusVal)}</span>
      </div>
      <div class="ui-vd-track">
        <div class="ui-vd-fill" style="width:${barPct}%;background:${barColor}"></div>
        ${bonusHtml}
        ${bingoTickHtml}
      </div>
    </div>

    <div class="ui-vd-kpi-row">
      ${kpis.map(it => `<div class="ui-vd-kpi"><span>${escapeXmlText(it.k)}</span><b>${escapeXmlText(it.v)}</b></div>`).join('')}
    </div>

    ${caps.length ? `
    <div class="ui-vd-caps">
      ${caps.map(c => `
        <div class="ui-vd-cap">
          <div class="ui-vd-cap-top"><span>${escapeXmlText(c.k)}</span><b>${Math.round(Number(c.score) || 0)}/10</b></div>
          ${buildVdPipsHtml(c.score, c.tone)}
        </div>
      `).join('')}
    </div>` : ''}

    <div class="ui-vd-weps">
      ${weapons.map(w => {
        const rangeTxt = w.maxRangeM > 0 ? `${w.minRangeM > 0 ? `${w.minRangeM}–` : ''}${w.maxRangeM}m` : '电子对抗';
        const splashTxt = w.splashOuterM > 0 ? ` · 爆${w.splashInnerM}/${w.splashOuterM}m` : (w.damage > 0 ? ` · 伤${w.damage}` : '');
        const ammoTxt = w.maxAmmo > 0 ? `${w.ammo}/${w.maxAmmo}` : '常驻吊舱';
        const clickable = Boolean(w.clickable && typeof onWeaponClick === 'function');
        const tag = clickable ? 'button' : 'div';
        const typeAttr = clickable ? 'type="button"' : '';
        return `
          <${tag} ${typeAttr} class="ui-vd-wep${clickable ? ' is-clickable' : ''}"
            data-airlab-weapon-id="${escapeXmlText(w.id || '')}"
            data-ammo="${Number(w.ammo) || 0}"
            data-max-ammo="${Number(w.maxAmmo) || 0}"
            data-can-target-air="${String(Boolean(w.canTargetAir))}"
            data-detail-weapon-cmd="${escapeXmlText(w.actionCmd || '')}">
            <span class="ui-vd-wep-ico">${buildVdWeaponSvg(w.kind, 14)}</span>
            <span class="ui-vd-wep-body">
              <span class="ui-vd-wep-r1">
                <strong>${escapeXmlText(w.name)}</strong>
                <b>${escapeXmlText(ammoTxt)}</b>
              </span>
              <span class="ui-vd-wep-r2">
                <span>${escapeXmlText(`${rangeTxt}${splashTxt}`)}</span>
                <span class="ui-vd-role-tag">${escapeXmlText(w.roleTag || '常规')}</span>
              </span>
            </span>
          </${tag}>`;
      }).join('')}
    </div>

    ${foldMeta.length ? `
    <details class="ui-vd-fold"${foldOpen ? ' open' : ''}>
      <summary>
        <span>▸ 更多原始参数与基底</span>
        <span class="ui-mono">${escapeXmlText(spec.specVersion || 'ba-source-ssot-v1')}</span>
      </summary>
      <div class="ui-vd-fold-grid">
        ${foldMeta.map(([k, v]) => `<div class="ui-vd-fold-row"><span>${escapeXmlText(k)}</span><b>${escapeXmlText(v)}</b></div>`).join('')}
      </div>
    </details>` : ''}
  `;

  const cardEl = containerEl.classList.contains('ui-vd-card')
    ? containerEl
    : (() => {
        let inner = containerEl.querySelector(':scope > .ui-vd-card');
        if (!inner) {
          inner = document.createElement('div');
          inner.className = 'ui-vd-card';
          containerEl.replaceChildren(inner);
        }
        return inner;
      })();

  cardEl.dataset.channel = channel;
  cardEl.classList.toggle('is-narrow', Boolean(narrow));
  cardEl.classList.toggle('is-fuel-low', fuelIsLow);
  cardEl.classList.toggle('is-bingo', fuelIsBingo);
  cardEl.innerHTML = innerHtml;

  if (typeof onClose === 'function') {
    cardEl.querySelector('[data-vd-close]')?.addEventListener('click', () => onClose());
  }
  if (typeof onWeaponClick === 'function') {
    cardEl.querySelectorAll('.ui-vd-wep.is-clickable').forEach(btn => {
      const wid = btn.getAttribute('data-airlab-weapon-id');
      btn.addEventListener('click', () => {
        const target = weapons.find(it => it.id === wid);
        if (target) onWeaponClick(target);
      });
    });
  }
  return cardEl;
}

/**
 * 渲染共享兵牌悬停精简浮卡（用于 /play 底部单机卡或 ×N 聚合卡悬停速览，以及 /ui-spec 活体演示）。
 */
export function renderVehicleHoverCard(containerEl, spec, options = {}) {
  if (!containerEl || !spec) return null;
  const isGroup = Boolean(options.isGroup && (options.count || 0) > 1);
  const count = Math.max(1, Number(options.count) || 1);
  const isPlane = Boolean(spec.usesFuel && spec.fuel);
  const channel = spec.channel || 'ground';
  const fuel = isPlane ? spec.fuel : null;
  const fuelIsBingo = Boolean(isPlane && (fuel.bingoState === 'bingo' || fuel.bingoState === 'rtb_bingo'));
  const fuelIsLow = Boolean(isPlane && !fuelIsBingo && fuel.bingoState === 'low');

  const barPct = Number.isFinite(options.avgPct)
    ? Math.max(0, Math.min(100, Math.round(options.avgPct)))
    : (isPlane
        ? Math.max(0, Math.min(100, Math.round(fuel.pct ?? 100)))
        : Math.max(0, Math.min(100, Math.round(((spec.hp ?? spec.maxHp ?? 100) / Math.max(1, spec.maxHp ?? 100)) * 100))));

  const barColor = isPlane
    ? (fuelIsBingo ? 'var(--ui-state-danger)' : (fuelIsLow ? 'var(--ui-state-warn)' : 'var(--ui-ch-plane)'))
    : (barPct < 25 ? 'var(--ui-state-danger)' : `var(--ui-ch-${channel})`);

  const rateTxt = isPlane ? Number(fuel.burnRate || 1).toFixed(2).replace(/\.00$/, '.0') : '';
  const statusRight = isPlane
    ? (isGroup
        ? `均油 ${barPct}% · ×${count} 架`
        : `${barPct}% · ${formatVdClock(fuel.remainSec)} (${fuel.modeLabel || '巡航'} ${rateTxt}×)`)
    : (isGroup
        ? `均血 ${barPct}% · 在场 ×${count}`
        : `HP ${spec.hp ?? 0} / ${spec.maxHp ?? 0}`);

  const quickStats = isPlane
    ? [['留空', formatVdClock(fuel.maxSec)], ['红线', `${Math.round(fuel.bingoThresholdPct)}%`], ['整备', `${fuel.rearmCooldownSec}s`]]
    : (Array.isArray(spec.kpis) ? spec.kpis.slice(0, 3).map(it => [String(it.k || '').slice(-2) || it.k, it.v]) : []);

  const topWeapons = Array.isArray(spec.weapons) ? spec.weapons.slice(0, 3) : [];
  const cardEl = containerEl.classList.contains('ui-vd-card')
    ? containerEl
    : (() => {
        let inner = containerEl.querySelector(':scope > .ui-vd-card');
        if (!inner) {
          inner = document.createElement('div');
          inner.className = 'ui-vd-card ui-vd-hover';
          containerEl.replaceChildren(inner);
        }
        return inner;
      })();

  cardEl.classList.add('ui-vd-card', 'ui-vd-hover');
  cardEl.dataset.channel = channel;
  cardEl.classList.toggle('is-fuel-low', fuelIsLow);
  cardEl.classList.toggle('is-bingo', fuelIsBingo);
  cardEl.innerHTML = `
    <div class="ui-vd-head" style="padding-bottom:4px">
      <div class="ui-vd-id">
        <span class="ui-vd-ico" style="width:22px;height:22px">${buildVdVehicleSvg(spec.iconKind, 13)}</span>
        <div class="ui-vd-titles">
          <strong style="font-size:12px">${escapeXmlText(spec.shortName || spec.slug)}${isGroup ? ` ×${count}` : ''}</strong>
          <small>${escapeXmlText(isGroup ? '同型聚合编队 · 点击 ×N 展开单机' : (spec.subTitle || spec.channelLabel || ''))}</small>
        </div>
      </div>
      <span class="ui-badge" data-tone="${escapeXmlText(channel)}">${escapeXmlText(spec.channelLabel || channel)}</span>
    </div>

    <div class="ui-vd-status">
      <div class="ui-vd-status-head" style="font-size:9.5px">
        <span class="ui-vd-status-lbl">${isPlane ? '战术燃油' : '耐久状态'}</span>
        <span class="ui-vd-status-val ${fuelIsBingo ? 'is-danger' : (fuelIsLow ? 'is-warn' : '')}" style="font-size:10px">${escapeXmlText(statusRight)}</span>
      </div>
      <div class="ui-vd-track" style="height:6px">
        <div class="ui-vd-fill" style="width:${barPct}%;background:${barColor}"></div>
        ${isPlane ? `<div class="ui-vd-bingo-tick" style="left:${Math.max(5, Math.min(95, Number(fuel.bingoThresholdPct) || 14))}%"></div>` : ''}
      </div>
    </div>

    <div class="ui-vd-hover-stats">
      ${quickStats.map(([k, v]) => `<span>${escapeXmlText(k)} <b>${escapeXmlText(v)}</b></span>`).join('')}
    </div>

    ${topWeapons.length ? `
    <div class="ui-vd-hover-weps">
      ${topWeapons.map(w => {
        const ammoStr = w.maxAmmo > 0 ? `${w.ammo}/${w.maxAmmo}` : '';
        const rightStr = [ammoStr, w.roleTag].filter(Boolean).join(' · ');
        return `
          <div class="ui-vd-hover-wep">
            <span class="ui-vd-hover-wep-l">
              <span style="color:var(--vd-ch);display:inline-flex">${buildVdWeaponSvg(w.kind, 11)}</span>
              <span>${escapeXmlText(w.name)}</span>
            </span>
            <span class="ui-vd-hover-wep-r">${escapeXmlText(rightStr)}</span>
          </div>`;
      }).join('')}
    </div>` : ''}
  `;
  return cardEl;
}

/**
 * 底部兵牌悬停精简浮卡控制器（180ms 防扫过误闪 + 自动横向边界钳制 + 外部弹窗互斥）。
 */
export function attachDeckHoverCardController({
  deckEl,
  hoverCardEl,
  resolvePayload,
  shouldSuppress = null,
  delayMs = 180,
} = {}) {
  if (!deckEl || !hoverCardEl || typeof resolvePayload !== 'function') {
    return Object.freeze({ hide: () => {}, destroy: () => {} });
  }
  let timer = 0;
  let activeCardEl = null;

  const hide = () => {
    if (timer) {
      window.clearTimeout(timer);
      timer = 0;
    }
    activeCardEl = null;
    hoverCardEl.hidden = true;
  };

  const showForCard = (cardEl) => {
    if (!cardEl || !document.contains(cardEl) || (shouldSuppress && shouldSuppress())) {
      hide();
      return;
    }
    const payload = resolvePayload(cardEl);
    if (!payload || !payload.spec) {
      hide();
      return;
    }
    renderVehicleHoverCard(hoverCardEl, payload.spec, payload.options || {});
    hoverCardEl.hidden = false;
    const cRect = cardEl.getBoundingClientRect();
    const dRect = deckEl.getBoundingClientRect();
    const hW = hoverCardEl.offsetWidth || 260;
    const rawLeft = (cRect.left - dRect.left) + (cRect.width - hW) * 0.5;
    const maxLeft = Math.max(0, dRect.width - hW);
    const clampedLeft = Math.max(0, Math.min(maxLeft, rawLeft));
    hoverCardEl.style.left = `${Math.round(clampedLeft)}px`;
  };

  const onPointerOver = (e) => {
    if (shouldSuppress && shouldSuppress()) {
      hide();
      return;
    }
    const cardEl = e.target?.closest?.('.scv-unit-card, .ui-banner-card');
    if (!cardEl || !deckEl.contains(cardEl)) return;
    if (cardEl === activeCardEl && !hoverCardEl.hidden) return;
    activeCardEl = cardEl;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = 0;
      showForCard(cardEl);
    }, Math.max(0, Number(delayMs) || 0));
  };

  const onPointerOut = (e) => {
    if (!activeCardEl) return;
    const rel = e.relatedTarget;
    if (rel && activeCardEl.contains(rel)) return;
    const nextCard = rel?.closest?.('.scv-unit-card, .ui-banner-card');
    if (nextCard && deckEl.contains(nextCard)) return;
    hide();
  };

  const onPointerDown = () => hide();

  deckEl.addEventListener('pointerover', onPointerOver);
  deckEl.addEventListener('pointerout', onPointerOut);
  deckEl.addEventListener('pointerdown', onPointerDown);

  return Object.freeze({
    hide,
    destroy: () => {
      hide();
      deckEl.removeEventListener('pointerover', onPointerOver);
      deckEl.removeEventListener('pointerout', onPointerOut);
      deckEl.removeEventListener('pointerdown', onPointerDown);
    },
  });
}
