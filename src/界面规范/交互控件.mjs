/**
 * Scavenger 全局共享交互控件库 (Code SSOT V2.1)
 * 职责：严格 LIFO ESC 栈、定制战术下拉 .ui-select（自动底部空间探测 .is-dropup）与三档原生物理尺码联动。
 */

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
