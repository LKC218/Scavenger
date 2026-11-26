/**
 * Scavenger 正式统一主入口控制器（4A 大厅 + 4B 129 车 680p 全景沙盘武器库 + 系统设置）。
 * 完全遵循 Code SSOT V2.2，所有 UI 样式与交互源自 /ui/* 单源，3D 展台复用 /src/程序化载具/载具流式加载器.mjs。
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { applyStylized680pCanvas } from '/src/程序化地图/渲染基线.mjs';
import { deriveVehicleDetailSpec } from '/src/程序化地图/数值基底与倍率规范.mjs';
import { buildVehicleRigHierarchy } from '/src/程序化地图/载具动效控制.mjs';
import { ProceduralVehicleLoader } from '/src/程序化载具/载具流式加载器.mjs';
import { createArmorySandboxStage } from './武器库沙盘环境.mjs';
import { createBrandLogoSvg, createSilhouetteSvg, readSemanticPalette } from '/ui/图标与标绘.mjs';
import {
  bindAllUiSelects,
  createEscStack,
  createToastManager,
  createUiSizeController,
  loadUiPrefs,
  playUiSfx,
  renderVehicleDetailCard,
  saveUiPrefs,
  UI_SIZE_LABELS,
} from '/ui/交互控件.mjs';

const ROSTER_STORAGE_KEY = 'scavenger.roster.v1';
const DEV_WORKBENCH_STORAGE_KEY = 'scavenger.lobby.devOpen.v1';
const DEFAULT_ROSTER_SLUGS = ['US_M1A2', 'US_AH64D', 'US_A10'];
const ROUTES = new Set(['/', '/armory', '/settings']);

const KIND_LABELS = Object.freeze({
  mbt: '主战坦克',
  ifv: '装甲战车',
  arty: '自行火炮',
  aa: '防空系统',
  atgm: '反坦克载具',
  supply: '后勤补给',
  recon: '侦察突击车',
  attack_heli: '武装直升机',
  trans_heli: '运输直升机',
  fighter: '制空战斗机',
  cas: '对地攻击机',
  uav: '无人机',
});

const SLOT_LABELS = Object.freeze({
  hull: '车体',
  turret: '炮塔',
  barrel: '炮管',
  main_rotor: '主旋翼',
  tail_rotor: '尾桨',
  gun: '机炮',
  radar: '雷达',
});

const appShell = document.getElementById('app-shell');
const toastCtrl = createToastManager(document.getElementById('app-toast-stack'));
const escStack = createEscStack();

const sigilEl = document.getElementById('app-brand-sigil');
if (sigilEl) sigilEl.append(createBrandLogoSvg({ variant: 'sigil', size: 22 }));

/* ====== 偏好与全局尺度联动 ====== */
let prefs = loadUiPrefs();
const sizeCtrl = createUiSizeController(appShell, {
  defaultSize: prefs.uiSize,
  segContainer: document.getElementById('set-size-seg'),
  onChange: ({ size, label }) => {
    prefs = saveUiPrefs({ uiSize: size });
    syncPrefBadges();
    playUiSfx('toggle', { resume: true });
    toastCtrl.push(`界面尺码已设为 ${label}`, `原生 CSS [${size}] 已同步至全站`, 'select');
  },
});

bindAllUiSelects(document, { escStack, dropupThreshold: 190 });

function syncPrefBadges() {
  const sum = document.getElementById('kpi-pref-summary');
  if (sum) sum.textContent = `${prefs.aspect} · ${prefs.uiSize}`;
  const aspectSel = document.getElementById('set-aspect-select');
  if (aspectSel && aspectSel.value !== prefs.aspect) aspectSel.value = prefs.aspect;
  const bloomToggle = document.getElementById('set-bloom-toggle');
  if (bloomToggle) bloomToggle.setAttribute('aria-checked', String(Boolean(prefs.bloomEnabled)));
  const dofSeg = document.getElementById('set-dof-seg');
  if (dofSeg) {
    const activeDof = prefs.dofMode || 'soft';
    dofSeg.querySelectorAll('[data-dof]').forEach(btn => {
      const hit = btn.dataset.dof === activeDof;
      btn.classList.toggle('active', hit);
      btn.setAttribute('aria-pressed', String(hit));
    });
  }
  const sfxBtn = document.getElementById('set-sfx-toggle');
  if (sfxBtn) sfxBtn.setAttribute('aria-checked', String(prefs.sfxEnabled));
  const volRange = document.getElementById('set-vol-range');
  const volTxt = document.getElementById('set-vol-txt');
  const volPct = Math.round(prefs.volume * 100);
  if (volRange) volRange.value = String(volPct);
  if (volTxt) volTxt.textContent = `${volPct}%`;
  const vehToggle = document.getElementById('set-veh-audio-toggle');
  if (vehToggle) vehToggle.setAttribute('aria-checked', String(prefs.vehicleAudioEnabled));
  const vehVolRange = document.getElementById('set-veh-vol-range');
  const vehVolTxt = document.getElementById('set-veh-vol-txt');
  const vehPct = Math.round((prefs.vehicleAudioVolume ?? 0.45) * 100);
  if (vehVolRange) vehVolRange.value = String(vehPct);
  if (vehVolTxt) vehVolTxt.textContent = `${vehPct}%`;
}

/* ====== 编队持久化状态 ====== */
function loadRoster() {
  try {
    const raw = localStorage.getItem(ROSTER_STORAGE_KEY);
    if (!raw) return [...DEFAULT_ROSTER_SLUGS];
    const arr = JSON.parse(raw);
    if (Array.isArray(arr) && arr.length) return arr.slice(0, 6);
  } catch {}
  return [...DEFAULT_ROSTER_SLUGS];
}

function saveRoster(slugs) {
  const next = slugs.slice(0, 6);
  try {
    localStorage.setItem(ROSTER_STORAGE_KEY, JSON.stringify(next));
  } catch {}
  return next;
}

let rosterSlugs = loadRoster();
let catalogVehicles = [];
let selectedSlug = rosterSlugs[0] || 'US_M1A2';
let filterCat = 'all';
let filterQuery = '';
let sortMode = 'index';
let currentView = ROUTES.has(location.pathname) ? location.pathname : '/';

/* ====== 载具兵种分类映射到 12 类负空间剪影 ====== */
function resolveVehicleKind(rec) {
  const slug = String(rec?.slug || rec?.id || '').toLowerCase();
  if (rec?.category === 'air') {
    if (slug.includes('ah') || slug.includes('oh58') || slug.includes('rah66')) return 'attack_heli';
    if (slug.includes('uh') || slug.includes('ch') || slug.includes('mh') || slug.includes('v-22') || slug.includes('cv-22') || slug.includes('mv-22')) return 'trans_heli';
    if (slug.includes('mq') || slug.includes('rq')) return 'uav';
    if (slug.includes('f15') || slug.includes('f16') || slug.includes('f22') || slug.includes('f35') || slug.includes('fa18')) return 'fighter';
    return 'cas';
  }
  if (slug.includes('m1a') || slug.includes('m8') || slug.includes('booker')) return 'mbt';
  if (slug.includes('mlrs') || slug.includes('himars') || slug.includes('m109') || slug.includes('paladin')) return 'arty';
  if (slug.includes('adats') || slug.includes('avenger') || slug.includes('shorad') || slug.includes('patriot') || slug.includes('cram')) return 'aa';
  if (slug.includes('lav_at') || slug.includes('tow')) return 'atgm';
  if (slug.includes('lmtv') || slug.includes('hemtt') || slug.includes('supply') || slug.includes('m548')) return 'supply';
  if (slug.includes('hmmwv') || slug.includes('m1151') || slug.includes('flyer')) return 'recon';
  return 'ifv';
}

function resolveChannel(rec) {
  if (rec?.category !== 'air') return 'ground';
  const k = resolveVehicleKind(rec);
  return (k === 'attack_heli' || k === 'trans_heli') ? 'heli' : 'plane';
}

function formatChannelLabel(ch) {
  if (ch === 'ground') return '地面装甲';
  if (ch === 'heli') return '旋翼航空';
  return '固定翼';
}

/* ====== 680p full 复古微缩 + 默认 Airbase 沙盘日光 3D 展台 ====== */
const armoryStage = (() => {
  const canvas = document.getElementById('armory-3d-canvas');
  const wrap = document.getElementById('armory-canvas-wrap');
  const loadingEl = document.getElementById('armory-loading');
  const lightingBtn = document.getElementById('armory-lighting-toggle');
  const resetBtn = document.getElementById('armory-cam-reset');
  const demoBtn = document.getElementById('armory-demo-toggle');
  const autoRotateBtn = document.getElementById('armory-autorotate-toggle');
  const camViewButtons = [...document.querySelectorAll('[data-cam-view]')];

  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let previewRoot = null;
  let loader = null;
  let sandboxStage = null;
  let activeRig = null;
  let readyPromise = null;
  let lightingMode = 'sandbox';
  let camView = 'hero';
  let demoEnabled = false;
  let autoRotateEnabled = true;
  let demoPhase = 0;
  let lastFrameMs = performance.now();
  let loadSeq = 0;
  let currentMeshCount = 0;
  let currentOutlineCount = 0;
  let currentSlotsPresent = [];
  let currentDimensions = null;
  let currentBoundsSize = new THREE.Vector3(6.5, 2.8, 9.6);
  let canvasSize = [1208, 680];
  let wrapSize = [1208, 680];
  let rafId = 0;
  let wrapResizeObserver = null;

  function stopLoop() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    if (controls) controls.enabled = false;
  }

  function startLoop() {
    if (!renderer || !scene || !camera || rafId) return;
    if (controls) controls.enabled = true;
    lastFrameMs = performance.now();
    const tick = (now) => {
      if (currentView !== '/armory' || !renderer || !scene || !camera) {
        rafId = 0;
        return;
      }
      const dt = Math.min(0.08, Math.max(0.001, (now - lastFrameMs) / 1000));
      lastFrameMs = now;
      updateDemoAnimation(dt);
      controls?.update();
      renderer.render(scene, camera);
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  function disposePreviewRoot() {
    if (!previewRoot) return;
    previewRoot.traverse(o => {
      if (o.isMesh && o.userData?.ownsGeoMat) {
        o.geometry?.dispose?.();
        if (Array.isArray(o.material)) o.material.forEach(m => m?.dispose?.());
        else o.material?.dispose?.();
      }
    });
    previewRoot.clear();
    activeRig = null;
  }

  function syncViewportLayout() {
    if (!wrap || !renderer || currentView !== '/armory') return;
    if (!wrap.clientWidth || !wrap.clientHeight) return;
    const w = Math.max(320, wrap.clientWidth);
    const h = Math.max(240, wrap.clientHeight);
    const spec = applyStylized680pCanvas(renderer, 'full', w, h);
    canvasSize = [spec.width, spec.height];
    wrapSize = [w, h];
    if (camera) {
      camera.aspect = spec.ratio;
      camera.updateProjectionMatrix();
    }
    loader?.setResolution?.(spec.width, spec.height);
  }

  function syncToolbarState() {
    camViewButtons.forEach(btn => {
      const active = btn.dataset.camView === camView;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    if (demoBtn) {
      demoBtn.classList.toggle('is-active', demoEnabled);
      demoBtn.setAttribute('aria-pressed', String(demoEnabled));
    }
    if (autoRotateBtn) {
      autoRotateBtn.classList.toggle('is-active', autoRotateEnabled);
      autoRotateBtn.setAttribute('aria-pressed', String(autoRotateEnabled));
    }
    if (lightingBtn) {
      const isSand = lightingMode !== 'armory';
      lightingBtn.textContent = isSand ? '沙盘日光' : '机库夜光';
      lightingBtn.classList.toggle('ui-btn--primary', isSand);
    }
  }

  function applyLightingMode(mode) {
    lightingMode = mode === 'armory' ? 'armory' : 'sandbox';
    if (sandboxStage) {
      sandboxStage.setMode(lightingMode);
      loader?.setEnvMap?.(sandboxStage.getEnvMap());
    }
    syncToolbarState();
  }

  function setCameraView(viewKey = 'hero', { preserveAutoRotate = false } = {}) {
    camView = ['hero', 'side', 'top'].includes(viewKey) ? viewKey : 'hero';
    if (!camera || !controls) {
      syncToolbarState();
      return;
    }
    const sz = currentBoundsSize;
    const diag = Math.max(2.6, sz.length());
    const spread = Math.max(sz.x * 1.18, sz.z * 1.05, diag);
    const targetY = Math.max(0.55, sz.y * 0.44);

    if (camView === 'side') {
      const sideDist = Math.max(7.6, Math.max(sz.z, sz.y * 1.7) * 1.92);
      controls.target.set(0, targetY, 0);
      camera.position.set(sideDist, targetY + sz.y * 0.22, 0.01);
    } else if (camView === 'top') {
      const topDist = Math.max(8.5, Math.max(sz.x, sz.z) * 1.56);
      controls.target.set(0, targetY * 0.35, 0);
      camera.position.set(0.01, targetY + topDist, topDist * 0.28);
    } else {
      const heroDist = Math.max(6.4, spread * 1.38);
      controls.target.set(0, targetY, 0);
      camera.position.set(heroDist * 0.72, targetY + heroDist * 0.36, heroDist * 0.72);
    }

    if (!preserveAutoRotate) {
      autoRotateEnabled = camView === 'hero';
      controls.autoRotate = autoRotateEnabled;
    }
    controls.minDistance = Math.max(2.6, diag * 0.42);
    controls.maxDistance = Math.max(68, diag * 4.5);
    controls.update();
    syncToolbarState();
  }

  function resetCamera() {
    autoRotateEnabled = true;
    if (controls) controls.autoRotate = true;
    setCameraView('hero');
  }

  function setDemoEnabled(enabled) {
    demoEnabled = Boolean(enabled);
    if (!demoEnabled && activeRig) {
      if (activeRig.turretPivot) activeRig.turretPivot.rotation.y = 0;
      if (activeRig.barrelPivot) activeRig.barrelPivot.rotation.x = 0;
      if (activeRig.gunPivot) activeRig.gunPivot.rotation.y = 0;
      if (activeRig.rotorBlurDisc) activeRig.rotorBlurDisc.visible = false;
      if (activeRig.tailBlurDisc) activeRig.tailBlurDisc.visible = false;
    }
    syncToolbarState();
  }

  function updateDemoAnimation(dt) {
    if (!activeRig) return;
    if (!demoEnabled) {
      if (activeRig.rotorBlurDisc) activeRig.rotorBlurDisc.visible = false;
      if (activeRig.tailBlurDisc) activeRig.tailBlurDisc.visible = false;
      return;
    }
    demoPhase += dt;
    if (activeRig.turretPivot) {
      activeRig.turretPivot.rotation.y = Math.sin(demoPhase * 0.85) * 0.65;
    }
    if (activeRig.barrelPivot) {
      activeRig.barrelPivot.rotation.x = 0.08 + Math.sin(demoPhase * 1.45) * 0.10;
    }
    if (activeRig.mainRotorPivot) {
      activeRig.mainRotorPivot.rotation.y += dt * 14.5;
      if (activeRig.rotorBlurDisc) activeRig.rotorBlurDisc.visible = true;
    }
    if (activeRig.tailRotorPivot) {
      if (activeRig.tailAxis === 'y') {
        activeRig.tailRotorPivot.rotation.y -= dt * 16.0;
        if (activeRig.tailBlurDisc) activeRig.tailBlurDisc.visible = true;
      } else {
        activeRig.tailRotorPivot.rotation.x += dt * 24.0;
      }
    }
    if (activeRig.gunPivot) {
      activeRig.gunPivot.rotation.y = Math.sin(demoPhase * 1.4) * 0.55;
    }
    if (activeRig.radarPivot) {
      activeRig.radarPivot.rotation.y += dt * 2.2;
    }
  }

  function ensureRenderer() {
    if (renderer || !canvas) return;
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.18;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(34, 16 / 9, 0.15, 900);
    sandboxStage = createArmorySandboxStage(scene, renderer);
    sandboxStage.setMode(lightingMode);

    previewRoot = new THREE.Group();
    scene.add(previewRoot);

    const w = Math.max(320, wrap?.clientWidth || 1208);
    const h = Math.max(240, wrap?.clientHeight || 680);
    const spec = applyStylized680pCanvas(renderer, 'full', w, h);
    canvasSize = [spec.width, spec.height];
    wrapSize = [w, h];
    camera.aspect = spec.ratio;
    camera.updateProjectionMatrix();

    loader = new ProceduralVehicleLoader(renderer, {
      mode: 'archive',
      archiveBaseUrl: '/v3',
      baseUrl: '/v2',
      envMap: sandboxStage.getEnvMap(),
      width: spec.width,
      height: spec.height,
    });

    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = false;
    controls.maxPolarAngle = Math.PI * 0.482;
    controls.autoRotate = autoRotateEnabled;
    controls.autoRotateSpeed = 0.65;
    controls.addEventListener('start', () => {
      if (autoRotateEnabled) {
        autoRotateEnabled = false;
        controls.autoRotate = false;
        syncToolbarState();
      }
    });

    setCameraView('hero', { preserveAutoRotate: true });
    if (wrap && typeof ResizeObserver !== 'undefined') {
      wrapResizeObserver = new ResizeObserver(syncViewportLayout);
      wrapResizeObserver.observe(wrap);
    }
    syncToolbarState();

    window.addEventListener('beforeunload', () => {
      stopLoop();
      wrapResizeObserver?.disconnect?.();
      wrapResizeObserver = null;
      controls?.dispose?.();
      disposePreviewRoot();
      sandboxStage?.dispose?.();
      loader?.dispose?.();
      renderer?.dispose?.();
    });

    if (currentView === '/armory') {
      startLoop();
    }
  }

  lightingBtn?.addEventListener('click', () => {
    playUiSfx('toggle', { resume: true });
    applyLightingMode(lightingMode === 'sandbox' ? 'armory' : 'sandbox');
  });

  resetBtn?.addEventListener('click', () => {
    playUiSfx('confirm', { resume: true });
    resetCamera();
  });

  demoBtn?.addEventListener('click', () => {
    playUiSfx('toggle', { resume: true });
    setDemoEnabled(!demoEnabled);
  });

  autoRotateBtn?.addEventListener('click', () => {
    playUiSfx('toggle', { resume: true });
    autoRotateEnabled = !autoRotateEnabled;
    if (controls) controls.autoRotate = autoRotateEnabled;
    syncToolbarState();
  });

  camViewButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      playUiSfx('confirm', { resume: true });
      setCameraView(btn.dataset.camView);
    });
  });

  async function initLoader() {
    ensureRenderer();
    if (!readyPromise && loader) {
      const p = (async () => {
        await loader.loadIndex();
        await loader.ensureFarBase();
      })();
      readyPromise = p;
      p.catch(() => {
        if (readyPromise === p) readyPromise = null;
      });
    }
    if (readyPromise) await readyPromise;
  }

  async function showVehicle(slug, onLoadedInfo = null) {
    const seq = ++loadSeq;
    if (loadingEl) loadingEl.hidden = false;
    try {
      await initLoader();
      syncViewportLayout();
      if (seq !== loadSeq || !loader || !previewRoot) return;
      const [parsed, meta] = await Promise.all([
        loader.loadVehicleLod(slug, 0),
        loader.loadVehicleMeta(slug).catch(() => null),
      ]);
      if (seq !== loadSeq) return;

      disposePreviewRoot();
      const rig = buildVehicleRigHierarchy(parsed, meta, { THREE, isScvveh: true });
      if (rig.rotorBlurDisc) rig.rotorBlurDisc.visible = demoEnabled;
      if (rig.tailBlurDisc) rig.tailBlurDisc.visible = demoEnabled;

      let meshCount = 0;
      let outlineCount = 0;
      rig.rigBodyGroup.traverse(o => {
        if (!o.isMesh) return;
        if (o.userData?.isStylizedOutline) outlineCount += 1;
        else if (!o.userData?.ownsGeoMat) meshCount += 1;
      });

      rig.rigBodyGroup.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(rig.rigBodyGroup);
      const size = new THREE.Vector3();
      const center = new THREE.Vector3();
      box.getSize(size);
      box.getCenter(center);
      rig.rigBodyGroup.position.set(-center.x, -box.min.y, -center.z);

      previewRoot.add(rig.rigBodyGroup);
      activeRig = rig;
      currentMeshCount = meshCount;
      currentOutlineCount = outlineCount;
      currentSlotsPresent = [...(rig.slotsPresent || [])];
      currentBoundsSize.copy(size);
      currentDimensions = {
        len: Number(size.z.toFixed(1)),
        wid: Number(size.x.toFixed(1)),
        hei: Number(size.y.toFixed(1)),
      };

      sandboxStage?.fitShadowToRadius(Math.max(size.x, size.z, size.y) * 0.55);
      setCameraView(camView, { preserveAutoRotate: true });
      if (onLoadedInfo) {
        onLoadedInfo({ dimensions: currentDimensions, slotsPresent: currentSlotsPresent });
      }
    } catch (err) {
      toastCtrl.push('载具视图加载受阻', String(err?.message || err), 'warn');
    } finally {
      if (loadingEl) loadingEl.hidden = true;
    }
  }

  function setRouteActive(active) {
    if (active) {
      startLoop();
    } else {
      loadSeq += 1;
      stopLoop();
      if (loadingEl) loadingEl.hidden = true;
    }
  }

  return Object.freeze({
    showVehicle,
    syncLayout: syncViewportLayout,
    setRouteActive,
    setLightingMode: applyLightingMode,
    setCameraView,
    setDemoEnabled,
    resetCamera,
    getState: () => ({
      meshCount: currentMeshCount,
      outlineCount: currentOutlineCount,
      lightingMode,
      envReady: Boolean(sandboxStage?.hasBothEnvs?.()),
      demoEnabled,
      autoRotate: autoRotateEnabled,
      camView,
      canvasSize: [...canvasSize],
      wrapSize: [...wrapSize],
      dimensions: currentDimensions ? { ...currentDimensions } : null,
      slotsPresent: [...currentSlotsPresent],
      rangeRequests: loader?.stats?.rangeRequests ?? 0,
    }),
  });
})();

/* ====== 目录折叠交互 ====== */
let catalogCollapsed = false;
function setCatalogCollapsed(collapsed) {
  catalogCollapsed = Boolean(collapsed);
  const wrap = document.getElementById('armory-canvas-wrap');
  const drawer = document.getElementById('armory-drawer-left');
  const openBtn = document.getElementById('armory-catalog-open');
  if (wrap) wrap.classList.toggle('is-catalog-collapsed', catalogCollapsed);
  if (drawer) drawer.classList.toggle('is-collapsed', catalogCollapsed);
  if (openBtn) openBtn.hidden = !catalogCollapsed;
}

document.getElementById('armory-catalog-close')?.addEventListener('click', () => {
  playUiSfx('toggle', { resume: true });
  setCatalogCollapsed(true);
});

document.getElementById('armory-catalog-open')?.addEventListener('click', () => {
  playUiSfx('toggle', { resume: true });
  setCatalogCollapsed(false);
});

/* ====== 视图与渲染刷新 ====== */
function renderRosterViews() {
  const bySlug = new Map(catalogVehicles.map(v => [v.slug, v]));
  const lobbyMount = document.getElementById('lobby-roster-preview');
  const armorySlots = document.getElementById('armory-roster-slots');
  const countBadge = document.getElementById('armory-roster-count');
  if (countBadge) countBadge.textContent = String(rosterSlugs.length);

  if (lobbyMount) {
    lobbyMount.replaceChildren();
    for (const slug of rosterSlugs) {
      const rec = bySlug.get(slug) || { slug, name: slug, category: 'ground' };
      const kind = resolveVehicleKind(rec);
      const ch = resolveChannel(rec);
      const wrap = document.createElement('div');
      wrap.className = `lobby-roster-item${ch === 'plane' ? ' has-airlab' : ''}`;
      const card = document.createElement('button');
      card.type = 'button';
      card.className = `ui-unit-card${selectedSlug === slug ? ' active' : ''}`;
      card.dataset.channel = ch;
      const ico = createSilhouetteSvg(kind, { size: 16, className: 'ui-unit-icon' });
      const title = document.createElement('strong');
      title.textContent = rec.name || slug;
      const sub = document.createElement('small');
      sub.textContent = `${KIND_LABELS[kind] || ch} · ${slug}`;
      card.append(ico, title, sub);
      card.addEventListener('click', () => {
        selectedSlug = slug;
        if (currentView !== '/armory') setView('/armory', true, slug);
        else selectVehicle(slug, { syncUrl: true });
      });
      wrap.append(card);
      if (ch === 'plane') {
        const flyLink = document.createElement('a');
        flyLink.className = 'lobby-airlab-chip';
        flyLink.href = `/air-lab?unit=${encodeURIComponent(slug)}`;
        flyLink.title = `在固定翼单机试飞台启动 ${rec.name || slug}`;
        flyLink.textContent = '试飞 ↗';
        flyLink.addEventListener('click', e => e.stopPropagation());
        wrap.append(flyLink);
      }
      lobbyMount.append(wrap);
    }
    for (let i = rosterSlugs.length; i < 6; i += 1) {
      const emptyBtn = document.createElement('button');
      emptyBtn.type = 'button';
      emptyBtn.className = 'lobby-roster-empty';
      emptyBtn.textContent = '+ 前往武器库选编载具';
      emptyBtn.addEventListener('click', () => {
        playUiSfx('confirm', { resume: true });
        setView('/armory');
      });
      lobbyMount.append(emptyBtn);
    }
  }

  if (armorySlots) {
    armorySlots.replaceChildren();
    for (const slug of rosterSlugs) {
      const rec = bySlug.get(slug) || { slug, name: slug, category: 'ground' };
      const kind = resolveVehicleKind(rec);
      const ch = resolveChannel(rec);
      const card = document.createElement('button');
      card.type = 'button';
      card.className = `ui-unit-card${selectedSlug === slug ? ' active' : ''}`;
      card.dataset.channel = ch;
      const ico = createSilhouetteSvg(kind, { size: 16, className: 'ui-unit-icon' });
      const title = document.createElement('strong');
      title.textContent = rec.name || slug;
      const sub = document.createElement('small');
      sub.textContent = `${KIND_LABELS[kind] || ch} · ${slug}`;
      card.append(ico, title, sub);
      card.addEventListener('click', () => {
        selectedSlug = slug;
        selectVehicle(slug, { syncUrl: true });
      });
      armorySlots.append(card);
    }
  }
}

function getFilteredVehicles() {
  const q = filterQuery.trim().toLowerCase();
  const list = catalogVehicles.filter(v => {
    const ch = resolveChannel(v);
    if (filterCat === 'ground' && ch !== 'ground') return false;
    if (filterCat === 'heli' && ch !== 'heli') return false;
    if (filterCat === 'plane' && ch !== 'plane') return false;
    if (!q) return true;
    return [v.slug, v.name, v.id, v.category, KIND_LABELS[resolveVehicleKind(v)] || ''].join(' ').toLowerCase().includes(q);
  });
  if (sortMode === 'name') {
    return list.sort((a, b) => String(a.name || a.slug).localeCompare(String(b.name || b.slug)));
  }
  if (sortMode === 'ground') {
    return list.sort((a, b) => Number(a.category === 'air') - Number(b.category === 'air') || ((a.index ?? 0) - (b.index ?? 0)));
  }
  if (sortMode === 'air') {
    return list.sort((a, b) => Number(b.category === 'air') - Number(a.category === 'air') || ((a.index ?? 0) - (b.index ?? 0)));
  }
  return list.sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
}

function renderArmoryList() {
  const listEl = document.getElementById('armory-list');
  const emptyEl = document.getElementById('armory-empty');
  const badgeEl = document.getElementById('armory-count-badge');
  if (!listEl || !emptyEl) return;
  const rows = getFilteredVehicles();
  if (badgeEl) badgeEl.textContent = `${rows.length} / ${catalogVehicles.length} 辆`;
  listEl.replaceChildren();
  emptyEl.hidden = rows.length > 0;
  listEl.hidden = rows.length === 0;

  for (const rec of rows) {
    const kind = resolveVehicleKind(rec);
    const ch = resolveChannel(rec);
    const inRoster = rosterSlugs.includes(rec.slug);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `ui-unit-card${selectedSlug === rec.slug ? ' active' : ''}`;
    btn.dataset.channel = ch;
    btn.dataset.slug = rec.slug;
    const ico = createSilhouetteSvg(kind, { size: 16, className: 'ui-unit-icon' });
    const title = document.createElement('strong');
    title.textContent = rec.name || rec.slug;
    const sub = document.createElement('small');
    sub.textContent = `${KIND_LABELS[kind] || ch} · ${rec.slug}`;
    btn.append(ico, title, sub);
    if (inRoster) {
      const flag = document.createElement('span');
      flag.className = 'armory-roster-flag';
      flag.textContent = '★ 已编入';
      btn.append(flag);
    }
    btn.addEventListener('click', () => selectVehicle(rec.slug));
    listEl.append(btn);
  }
}

function renderDossier(rec, stageInfo = null) {
  const box = document.getElementById('armory-dossier');
  const toggleBtn = document.getElementById('armory-toggle-roster');
  const titleEl = document.getElementById('armory-stage-title');
  const subEl = document.getElementById('armory-stage-sub');
  const heroIconEl = document.getElementById('armory-hero-icon');
  if (!rec || !box) return;

  const kind = resolveVehicleKind(rec);
  const ch = resolveChannel(rec);
  const kindLabel = KIND_LABELS[kind] || formatChannelLabel(ch);
  const inRoster = rosterSlugs.includes(rec.slug);
  const pal = readSemanticPalette();
  const chColor = ch === 'ground' ? pal.ground : ch === 'heli' ? pal.heli : pal.plane;

  if (titleEl) titleEl.textContent = rec.name || rec.slug;
  if (subEl) subEl.textContent = `${kindLabel} · ${rec.slug}`;
  if (heroIconEl) {
    const hSvg = createSilhouetteSvg(kind, { size: 18 });
    hSvg.style.color = chColor;
    heroIconEl.replaceChildren(hSvg);
  }

  const airLabBtn = document.getElementById('armory-airlab-btn');
  if (airLabBtn) {
    const isPlane = ch === 'plane';
    airLabBtn.hidden = !isPlane;
    if (isPlane) {
      airLabBtn.href = `/air-lab?unit=${encodeURIComponent(rec.slug)}`;
    }
  }

  if (toggleBtn) {
    toggleBtn.textContent = inRoster ? '移出作战编队' : '加入作战编队';
    toggleBtn.classList.toggle('ui-btn--danger', inRoster);
    toggleBtn.classList.toggle('ui-btn--primary', !inRoster);
  }

  const rawSlots = (stageInfo?.slotsPresent || []).filter(s => s !== 'hull');
  const slotText = rawSlots.length ? rawSlots.map(s => SLOT_LABELS[s] || s).join(' · ') : '一体化机体';
  const spec = deriveVehicleDetailSpec(
    {
      ...rec,
      iconKind: kind,
      channel: ch,
      shortName: rec.name || rec.slug,
      fullModelName: rec.name || rec.slug,
    },
    {
      mode: 'armory',
      stageInfo: {
        dimensions: stageInfo?.dimensions || null,
        slotText,
        rosterText: inRoster ? '已编入出击队列' : '待命在库',
      },
    },
  );
  renderVehicleDetailCard(box, spec, {
    narrow: true,
    showClose: false,
    foldOpen: false,
  });
}

async function selectVehicle(slug, { syncUrl = false } = {}) {
  const rec = catalogVehicles.find(v => v.slug === slug) || catalogVehicles[0];
  if (!rec) return;
  selectedSlug = rec.slug;
  renderArmoryList();
  renderRosterViews();
  renderDossier(rec);
  if (syncUrl && currentView === '/armory') {
    const nextUrl = `/armory?unit=${encodeURIComponent(rec.slug)}`;
    if (`${location.pathname}${location.search}` !== nextUrl) {
      history.replaceState({ view: '/armory', unit: rec.slug }, '', nextUrl);
    }
  }
  if (currentView === '/armory') {
    await armoryStage.showVehicle(rec.slug, info => {
      if (selectedSlug === rec.slug) renderDossier(rec, info);
    });
  }
}

function setView(route, push = true, unitSlug = '') {
  const target = ROUTES.has(route) ? route : '/';
  currentView = target;
  appShell?.classList.toggle('is-armory', target === '/armory');
  if (unitSlug && catalogVehicles.some(v => v.slug === unitSlug)) {
    selectedSlug = unitSlug;
  }
  const targetUrl = (target === '/armory' && (unitSlug || selectedSlug))
    ? `/armory?unit=${encodeURIComponent(unitSlug || selectedSlug)}`
    : target;
  if (push && `${location.pathname}${location.search}` !== targetUrl) {
    history.pushState({ view: target, unit: unitSlug || selectedSlug }, '', targetUrl);
  }
  document.querySelectorAll('#app-nav-tabs .ui-skew-tab').forEach(btn => {
    const active = btn.dataset.route === target;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-current', active ? 'page' : 'false');
  });
  document.querySelectorAll('[data-view]').forEach(el => {
    el.hidden = el.dataset.view !== target;
  });
  if (target === '/armory') {
    armoryStage.setRouteActive(true);
    if (selectedSlug) {
      requestAnimationFrame(() => armoryStage.syncLayout());
      selectVehicle(selectedSlug, { syncUrl: Boolean(unitSlug) });
    }
  } else {
    armoryStage.setRouteActive(false);
  }
}

/* ====== 事件绑定与 LIFO ESC 栈 ====== */
document.getElementById('app-nav-tabs')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-route]');
  if (!btn) return;
  playUiSfx('confirm', { resume: true });
  setView(btn.dataset.route);
});

document.addEventListener('click', e => {
  const gotoBtn = e.target.closest('[data-goto]');
  if (gotoBtn) {
    if (gotoBtn.tagName === 'A') e.preventDefault();
    playUiSfx('confirm', { resume: true });
    setView(gotoBtn.dataset.goto);
  }
});

window.addEventListener('popstate', () => {
  const unitParam = new URLSearchParams(location.search).get('unit') || '';
  setView(location.pathname, false, unitParam);
});

document.getElementById('armory-cat-seg')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-cat]');
  if (!btn) return;
  filterCat = btn.dataset.cat;
  document.querySelectorAll('#armory-cat-seg button').forEach(b => b.classList.toggle('active', b === btn));
  renderArmoryList();
});

document.getElementById('armory-search')?.addEventListener('input', e => {
  filterQuery = e.target.value || '';
  renderArmoryList();
});

document.getElementById('armory-reset-filter')?.addEventListener('click', () => {
  filterCat = 'all';
  filterQuery = '';
  const inp = document.getElementById('armory-search');
  if (inp) inp.value = '';
  document.querySelectorAll('#armory-cat-seg button').forEach(b => b.classList.toggle('active', b.dataset.cat === 'all'));
  renderArmoryList();
});

document.getElementById('armory-sort-select')?.addEventListener('ui-select-change', e => {
  sortMode = e.detail.value || 'index';
  renderArmoryList();
});

document.getElementById('armory-toggle-roster')?.addEventListener('click', () => {
  const idx = rosterSlugs.indexOf(selectedSlug);
  if (idx >= 0) {
    if (rosterSlugs.length <= 1) {
      playUiSfx('denied', { resume: true });
      toastCtrl.push('至少保留 1 辆主战单位', '作战编队不能为空', 'warn');
      return;
    }
    rosterSlugs.splice(idx, 1);
    playUiSfx('back', { resume: true });
  } else {
    if (rosterSlugs.length >= 6) {
      playUiSfx('denied', { resume: true });
      toastCtrl.push('编队已满 6 槽', '请先移出 1 辆载具后再添加', 'warn');
      return;
    }
    rosterSlugs.push(selectedSlug);
    playUiSfx('confirm', { resume: true });
  }
  rosterSlugs = saveRoster(rosterSlugs);
  renderArmoryList();
  renderRosterViews();
  const st = armoryStage.getState();
  renderDossier(catalogVehicles.find(v => v.slug === selectedSlug), {
    dimensions: st.dimensions,
    slotsPresent: st.slotsPresent,
  });
});

document.getElementById('armory-clear-roster')?.addEventListener('click', () => {
  rosterSlugs = saveRoster([...DEFAULT_ROSTER_SLUGS]);
  playUiSfx('toggle', { resume: true });
  renderArmoryList();
  renderRosterViews();
  const st = armoryStage.getState();
  renderDossier(catalogVehicles.find(v => v.slug === selectedSlug), {
    dimensions: st.dimensions,
    slotsPresent: st.slotsPresent,
  });
  toastCtrl.push('已恢复默认核心编队', 'US_M1A2 / US_AH64D / US_A10', 'ground');
});

document.getElementById('set-aspect-select')?.addEventListener('ui-select-change', e => {
  prefs = saveUiPrefs({ aspect: e.detail.value === '21:9' ? '21:9' : '16:9' });
  syncPrefBadges();
  playUiSfx('toggle', { resume: true });
  toastCtrl.push(`默认 680p 画幅已设为 ${prefs.aspect}`, '进入 9km 战场时将自动应用', 'select');
});

document.getElementById('set-bloom-toggle')?.addEventListener('click', e => {
  const btn = e.currentTarget;
  const next = btn.getAttribute('aria-checked') !== 'true';
  prefs = saveUiPrefs({ bloomEnabled: next });
  syncPrefBadges();
  playUiSfx('toggle', { resume: true });
  toastCtrl.push(`自发光后期辉光已${next ? '开启' : '关闭'}`, next ? '爆炸/曳光/导弹尾焰启用 680p HDR Bloom' : '已回退至纯直渲输出模式', next ? 'ground' : 'warn');
});

document.getElementById('set-dof-seg')?.addEventListener('click', e => {
  const btn = e.target.closest('[data-dof]');
  if (!btn) return;
  const mode = ['off', 'soft', 'standard'].includes(btn.dataset.dof) ? btn.dataset.dof : 'soft';
  const labels = { off: '关闭', soft: '柔和', standard: '标准' };
  prefs = saveUiPrefs({ dofMode: mode });
  syncPrefBadges();
  playUiSfx('toggle', { resume: true });
  toastCtrl.push(
    `微缩移轴景深已设为：${labels[mode]}`,
    mode === 'off' ? '已旁路景深后处理，全视角纯锐利输出' : '近中景微缩景深生效，拉高至战略视角自动衰减至 0%',
    mode === 'off' ? 'warn' : 'select',
  );
});

document.getElementById('set-sfx-toggle')?.addEventListener('click', e => {
  const btn = e.currentTarget;
  const next = btn.getAttribute('aria-checked') !== 'true';
  prefs = saveUiPrefs({ sfxEnabled: next });
  syncPrefBadges();
  playUiSfx('toggle', { enabled: next, resume: true });
});

document.getElementById('set-vol-range')?.addEventListener('input', e => {
  const v = Math.min(100, Math.max(0, Number(e.target.value || 0))) / 100;
  prefs = saveUiPrefs({ volume: v });
  syncPrefBadges();
});

document.getElementById('set-veh-audio-toggle')?.addEventListener('click', e => {
  const btn = e.currentTarget;
  const next = btn.getAttribute('aria-checked') !== 'true';
  prefs = saveUiPrefs({ vehicleAudioEnabled: next });
  syncPrefBadges();
  playUiSfx('toggle', { resume: true });
  toastCtrl.push(`载具引擎环境音已${next ? '开启' : '静音'}`, '进入 9km 战场后即时生效', next ? 'ground' : 'warn');
});

document.getElementById('set-veh-vol-range')?.addEventListener('input', e => {
  const v = Math.min(100, Math.max(0, Number(e.target.value || 0))) / 100;
  prefs = saveUiPrefs({ vehicleAudioVolume: v });
  syncPrefBadges();
});

document.getElementById('set-test-sfx')?.addEventListener('click', () => {
  const ok = playUiSfx('confirm', { resume: true });
  toastCtrl.push(ok ? '确认音效试听' : '音效当前已静音', `音量 ${Math.round(prefs.volume * 100)}%`, 'select');
});

document.getElementById('set-reset-prefs')?.addEventListener('click', () => {
  prefs = saveUiPrefs({
    uiSize: 'lg',
    aspect: '16:9',
    bloomEnabled: true,
    dofMode: 'soft',
    sfxEnabled: true,
    volume: 0.35,
    vehicleAudioEnabled: true,
    vehicleAudioVolume: 0.45,
  });
  sizeCtrl.setSize('lg', { silent: true, isAuto: false });
  syncPrefBadges();
  playUiSfx('confirm', { resume: true });
  toastCtrl.push('系统设置已恢复默认', '16:9 · 标准大号 (lg) · UI 35% · 载具引擎 45%', 'ground');
});

escStack.register('sub-view-to-lobby', () => currentView !== '/', () => {
  playUiSfx('back', { resume: true });
  setView('/');
});

window.addEventListener('keydown', e => {
  escStack.handleKeydown(e);
  if (e.key === 'Enter' && !e.defaultPrevented && !e.ctrlKey && !e.metaKey && !e.altKey && currentView === '/') {
    const tag = document.activeElement?.tagName;
    if (!tag || tag === 'BODY' || tag === 'HTML') {
      playUiSfx('confirm', { resume: true });
      location.href = '/play';
    }
  }
});

const devToggleBtn = document.getElementById('lobby-dev-toggle');
const devGridEl = document.getElementById('lobby-dev-grid');
function setDevWorkbenchOpen(open) {
  const isOpen = Boolean(open);
  if (devGridEl) devGridEl.hidden = !isOpen;
  if (devToggleBtn) {
    devToggleBtn.textContent = isOpen ? '收起 ▴' : '展开 ▾';
    devToggleBtn.setAttribute('aria-expanded', String(isOpen));
  }
  try {
    localStorage.setItem(DEV_WORKBENCH_STORAGE_KEY, isOpen ? '1' : '0');
  } catch {}
}
if (devToggleBtn && devGridEl) {
  let savedOpen = true;
  try {
    const raw = localStorage.getItem(DEV_WORKBENCH_STORAGE_KEY);
    if (raw === '0') savedOpen = false;
  } catch {}
  setDevWorkbenchOpen(savedOpen);
  devToggleBtn.addEventListener('click', () => {
    playUiSfx('toggle', { resume: true });
    setDevWorkbenchOpen(Boolean(devGridEl.hidden));
  });
}

/* ====== 初始化加载底层简报与 129 车清单 ====== */
async function boot() {
  syncPrefBadges();
  const healthBadge = document.getElementById('app-health-badge');
  const shaBadge = document.getElementById('brief-sha-badge');
  try {
    const h = await (await fetch('/healthz', { cache: 'no-store' })).json();
    if (healthBadge) healthBadge.textContent = `单端口 ${h.port} 在线 · ${h.ui_spec_version || 'V2.2'}`;
    if (shaBadge) shaBadge.textContent = `SHA ${String(h.source_sha256 || '').slice(0, 10)}`;
  } catch {
    if (healthBadge) healthBadge.textContent = '离线模式';
  }

  try {
    const b0 = await (await fetch('/v0/基线凭证.json', { cache: 'no-store' })).json();
    catalogVehicles = Array.isArray(b0.vehicles) ? b0.vehicles : [];
    const kpi = document.getElementById('kpi-veh-total');
    if (kpi) kpi.textContent = `${catalogVehicles.length} 辆`;
  } catch {
    catalogVehicles = [];
  }

  const initUnitParam = new URLSearchParams(location.search).get('unit') || '';
  if (initUnitParam && catalogVehicles.some(v => v.slug === initUnitParam)) {
    selectedSlug = initUnitParam;
  }
  renderRosterViews();
  renderArmoryList();
  const initRec = catalogVehicles.find(v => v.slug === selectedSlug) || catalogVehicles[0];
  if (initRec) renderDossier(initRec);
  setView(currentView, false, initUnitParam);
}

await boot();

window.__SCV_APP__ = Object.freeze({
  ready: true,
  getView: () => currentView,
  setView: v => setView(v),
  getArmoryState: () => ({
    total: catalogVehicles.length,
    filtered: getFilteredVehicles().length,
    selectedSlug,
    rosterSlugs: [...rosterSlugs],
    catalogCollapsed,
    stage: armoryStage.getState(),
  }),
  selectVehicle,
  setArmoryLightingMode: mode => armoryStage.setLightingMode(mode),
  setArmoryCameraView: (view, opts) => armoryStage.setCameraView(view, opts),
  setArmoryDemoEnabled: on => armoryStage.setDemoEnabled(on),
  setArmoryCatalogCollapsed: collapsed => setCatalogCollapsed(collapsed),
  resetArmoryCamera: () => armoryStage.resetCamera(),
  getPrefs: () => ({ ...prefs }),
  getUiSizeLabel: () => UI_SIZE_LABELS[sizeCtrl.getSize()] || sizeCtrl.getSize(),
});
