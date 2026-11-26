/**
 * Scavenger 操作交互系统 —— 统一总入口聚合与生命周期管理。
 * 聚合契约与仲裁、镜头控制、选区与编组、高清标绘层、战场适配器与 V2.2 交互反馈。
 */

import {
  INTERACTION_MODES,
  createModeAndEscArbiter,
} from './契约与仲裁.mjs';
import { createCameraController, shortestAngleDiff } from './镜头控制.mjs';
import { createSelectionAndGroupController } from './选区与编组.mjs';
import { createCommandAndOverlayLayer } from './指令与标绘层.mjs';
import { createBattlefieldAdapter } from './战场适配器.mjs';

const HUD_BLOCK_SELECTOR = [
  '.ui-pod:not(.scv-mode-banner)',
  '.scv-roster-flyout',
  '.scv-editor-drawer',
  '.scv-deploy-drawer',
  '.scv-pause-overlay',
  '.scv-minimap-box',
  '.scv-radar-dock',
  '.scv-card-deck',
  '.scv-deck-popover',
  '.scv-right-dock',
  '.scv-unit-detail-card',
  '.ui-prompt-btn',
  '.ui-btn',
  '.ui-select',
  '.scv-wheel-host',
  'a',
  'button',
  'input',
].join(', ');

export function createTacticalInteraction({
  host,
  canvas,
  camera,
  sun,
  state,
  terrainPool,
  support,
  physics,
  combatSystem,
  editor,
  b1Manifest,
  b3Manifest,
  transitionCtrl,
  transitionPipeline,
  buildingSystem,
  computeHorPlusCameraFrustum,
  THREE,
  hudBridge = {},
  escStack = null,
  minimapBox = null,
  minimapScreenToWorld = null,
  initialMode = INTERACTION_MODES.PLAY,
} = {}) {
  const toast = (title, desc = '', toneKey = 'select') => {
    const colorMap = {
      select: 'var(--ui-state-select)',
      ground: 'var(--ui-ch-ground)',
      heli: 'var(--ui-ch-heli)',
      plane: 'var(--ui-ch-plane)',
      warn: 'var(--ui-state-warn)',
      danger: 'var(--ui-state-danger)',
      safe: 'var(--ui-state-safe)',
    };
    hudBridge.pushToast?.(title, desc, colorMap[toneKey] || toneKey);
  };

  const adapter = createBattlefieldAdapter({
    host,
    camera,
    terrainPool,
    support,
    physics,
    combatSystem,
    THREE,
    getSpan: () => state?.span,
  });

  let selectionCtrl = null;

  const cameraCtrl = createCameraController({
    host,
    camera,
    sun,
    state,
    support,
    bounds: b1Manifest?.bounds,
    computeHorPlusCameraFrustum,
    transitionCtrl,
    transitionPipeline,
    buildingSystem,
    queryGroundAt: (lx, ly) => adapter.queryGroundAt(lx, ly),
    onStreamSync: () => {
      if (state.editorMode && editor) {
        const ov = editor.refreshPhysicsAndNavOverlay();
        hudBridge.syncEditorDrawer?.(ov);
      }
      hudBridge.updateHudText?.();
    },
    onCameraUpdate: () => {
      hudBridge.updateHudText?.();
    },
    onFollowChange: (followId) => {
      if (!followId) {
        selectionCtrl?.notifyCameraFollowStopped?.();
      }
      hudBridge.syncCombatHud?.();
    },
    onToast: toast,
  });

  cameraCtrl.setUnitAnchorProvider(id => adapter.getUnitFocus(id));

  selectionCtrl = createSelectionAndGroupController({
    host,
    adapter,
    cameraCtrl,
    onChange: () => {
      hudBridge.syncCombatHud?.();
    },
    onHoverChange: () => {
      // 悬停高亮同步交由每帧标绘层与名牌读取，无需整套 DOM 重建
    },
    onToast: toast,
  });

  // 将 combatSystem 初始编队中的首选单位同步给选区控制器
  if (combatSystem?.selectedUnitId) {
    selectionCtrl.setSelection([combatSystem.selectedUnitId], { silent: true });
  }

  // 接管名牌的左键点选（支持多选/Shift/双击同类）与双向悬停
  if (combatSystem) {
    combatSystem.onLabelPointerDown = (unitId, event) => {
      const local = toLocal(event);
      selectionCtrl.commitPointClick(local, { append: event.shiftKey, explicitUnitId: unitId });
    };
    combatSystem.onLabelHover = (unitId) => {
      selectionCtrl.setHoveredUnit(unitId);
    };
  }

  let drag = null;
  let lastPointerLocal = null;
  let lastBrushWorld = null;
  let activeAirStrikeSpec = null;
  let pendingDropPoints = [];
  let pendingDropQueue = false;
  let pendingHoverWorld = null;
  let shiftHeld = false;
  let airStrikeQueuedInSession = false;
  let groundAgQueuedInSession = false;
  const pressedKeys = new Set();
  const cleanups = [];

  const syncShiftHeld = (next) => {
    const val = Boolean(next);
    if (shiftHeld !== val) {
      shiftHeld = val;
      overlayLayer?.setShiftHeld?.(shiftHeld);
    }
  };

  const arbiter = createModeAndEscArbiter({
    escStack,
    hasActiveDrag: () => Boolean(drag),
    cancelActiveDrag: () => {
      cancelActiveDrag();
      toast('已取消未提交的预览操作', '已提交执行中的任务保持不变', 'select');
    },
    onModeChange: ({ mode }) => {
      if (mode !== INTERACTION_MODES.WHEEL) {
        overlayLayer?.closeWheel?.();
      }
      if (combatSystem?.nameplateHost) {
        combatSystem.nameplateHost.style.pointerEvents = mode === INTERACTION_MODES.PLAY ? 'none' : 'none';
      }
      if (mode !== INTERACTION_MODES.TARGETING) {
        activeAirStrikeSpec = null;
        pendingDropPoints = [];
        pendingDropQueue = false;
        pendingHoverWorld = null;
        airStrikeQueuedInSession = false;
        groundAgQueuedInSession = false;
        overlayLayer?.setAirStrikePreview?.(null);
        overlayLayer?.setTargetingPoint?.(null);
      }
      if (host) {
        host.dataset.interactionMode = mode;
      }
      overlayLayer?.updateBanner?.();
      hudBridge.onInteractionModeChange?.(mode, arbiter.getActiveBrush(), arbiter.getDeploySlug());
      hudBridge.syncCombatHud?.();
    },
    onCursorChange: (_cursorKey, cssClass) => {
      if (!host) return;
      host.classList.remove('ui-cur-default', 'ui-cur-move', 'ui-cur-attack', 'ui-cur-invalid', 'ui-cur-action');
      host.classList.add(cssClass);
    },
  });

  function buildFallbackWheelModel() {
    const ids = selectionCtrl.getSelectedIds();
    const selUnits = adapter.getUnits().filter(u => ids.includes(u.id));
    const u = selUnits[0] || null;
    const roles = new Set(selUnits.map(it => it.role));
    const followOn = Boolean(selectionCtrl.isFollowModeActive?.() || cameraCtrl.getFollowUnitId?.());
    const targetingOn = arbiter.getMode() === INTERACTION_MODES.TARGETING && !activeAirStrikeSpec;

    if (!u) {
      return {
        eyebrow: '待命 · 无选中',
        eyebrowTone: 'select',
        slots: [
          null,
          null,
          null,
          { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
          null,
          null,
          null,
          null,
        ],
      };
    }

    if (roles.size > 1) {
      return {
        eyebrow: `混选 · ${selUnits.length} 单位`,
        eyebrowTone: 'select',
        slots: [
          null,
          null,
          { id: 'stop', label: '停航待命', key: 'X', modeTag: '全组', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: 'ready' },
          { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
          { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: followOn ? 'active' : 'ready', charge: followOn ? '跟随' : '' },
          { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '选区', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
          null,
          null,
        ],
      };
    }

    const isHeli = u.role === 'helicopter';
    const tone = isHeli ? 'heli' : 'ground';
    const isGuard = Boolean(u.groundGuard);
    return {
      eyebrow: selUnits.length > 1 ? `${isHeli ? '旋翼' : '地面'} · ${selUnits.length} 单位` : `${u.shortName || u.id} · ${isHeli ? '旋翼' : '地面'}`,
      eyebrowTone: tone,
      slots: [
        { id: 'attackGround', label: '强制攻地', key: 'K', modeTag: '瞄准', iconType: 'hud', iconName: 'attack', tone, status: targetingOn ? 'active' : 'ready', charge: targetingOn ? '瞄准' : '' },
        { id: 'guard', label: '警戒守备', key: 'Z', modeTag: '开关', iconType: 'hud', iconName: 'shield', tone, status: isGuard ? 'active' : 'ready', charge: isGuard ? '已开' : '' },
        { id: 'stop', label: '停航待命', key: 'X', modeTag: '瞬发', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: 'ready' },
        { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
        { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: followOn ? 'active' : 'ready', charge: followOn ? '跟随' : '' },
        { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '瞬发', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
        null,
        null,
      ],
    };
  }

  const overlayLayer = createCommandAndOverlayLayer({
    host,
    adapter,
    selectionCtrl,
    arbiter,
    getWheelModel: () => hudBridge.getWheelModel?.() || buildFallbackWheelModel(),
    onWheelAction: (slot, meta = {}) => {
      if (meta.trigger === 'flick_blocked' || meta.trigger === 'click_blocked') {
        if (meta.check?.status === 'cooling') {
          toast(`${slot?.label || '指令'} 冷却中`, meta.check.reason || '冷却结束后可再次触发', 'warn');
        } else if (meta.check?.status === 'disabled') {
          toast(`${slot?.label || '指令'} 不可用`, meta.check.reason || '当前状态不满足触发条件', 'danger');
        }
        return;
      }
      if (!slot) return;
      if (hudBridge.executeWheelCommand?.(slot)) {
        hudBridge.syncCombatHud?.();
        return;
      }
      if (slot.id === 'attackGround') {
        enterTargetingMode();
      } else if (slot.id === 'stop') {
        cancelSelectedUnits();
      } else if (slot.id === 'guard') {
        const primaryId = selectionCtrl.getPrimaryUnitId();
        const u = adapter.getUnits().find(it => it.id === primaryId);
        if (!u) {
          toast('请先选择载具', '', 'warn');
          return;
        }
        u.groundGuard = !u.groundGuard;
        hudBridge.syncCombatHud?.();
        toast(`警戒守备：${u.groundGuard ? '开启' : '关闭'}`, u.shortName || u.id, 'ground');
      } else if (slot.id === 'follow') {
        selectionCtrl.toggleFollow();
      } else if (slot.id === 'focus') {
        selectionCtrl.focusSelection();
      } else if (slot.id === 'resetView') {
        cameraCtrl.flyTo(0, 85, 135, 0);
        toast('视野已复位至 9km 核心交战点', '', 'select');
      }
    },
  });

  if (initialMode && initialMode !== INTERACTION_MODES.PLAY) {
    arbiter.setMode(initialMode);
  } else {
    host?.classList.add('ui-cur-default');
  }

  function toLocal(e) {
    const r = host.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function isHudBlockingTarget(target, button = 0) {
    if (!target || typeof target.closest !== 'function') return false;
    if (target.closest('.scv-mode-banner')) return false;
    // 子模式（落点部署/强制攻地/编辑笔刷）下名牌完全透传至画布；常规指挥态下名牌左键点击由点选接管
    if (target.closest('.ground-mark')) {
      return button === 0 && arbiter.getMode() === INTERACTION_MODES.PLAY;
    }
    return Boolean(target.closest(HUD_BLOCK_SELECTOR));
  }

  function mayCommitPointer(e) {
    const p = toLocal(e);
    const w = host?.clientWidth || 0;
    const h = host?.clientHeight || 0;
    if (p.x < 0 || p.y < 0 || p.x > w || p.y > h) return false;
    const el = document.elementFromPoint?.(e.clientX, e.clientY);
    if (!el) return true;
    if (isHudBlockingTarget(el, e.button)) return false;
    return host.contains(el);
  }

  function cancelActiveDrag() {
    if (drag?.previewTimer) {
      clearTimeout(drag.previewTimer);
      drag.previewTimer = null;
    }
    if (drag && host?.hasPointerCapture?.(drag.pointerId)) {
      host.releasePointerCapture(drag.pointerId);
    }
    drag = null;
    selectionCtrl.hideDragBox();
    overlayLayer.clearPreview();
    cameraCtrl.endPointerDrag();
    const m = arbiter.getMode();
    arbiter.setCursor(
      m === INTERACTION_MODES.TARGETING
        ? 'attack'
        : (m === INTERACTION_MODES.EDITOR || m === INTERACTION_MODES.DEPLOY)
          ? 'deploy'
          : 'default',
    );
  }

  function buildRightMoveCommand(localPt) {
    if (!drag || drag.kind !== 'right_move' || !drag.world) return null;
    const endRes = adapter.queryGroundAt(localPt.x, localPt.y);
    const endPt = endRes?.point;
    const pixelMoved = Math.hypot(localPt.x - drag.start.x, localPt.y - drag.start.y);
    const worldMoved = endPt ? Math.hypot(endPt.x - drag.world.x, endPt.z - drag.world.z) : 0;
    const ids = selectionCtrl.getSelectedIds();
    const selUnits = adapter.getUnits().filter(u => ids.includes(u.id));
    const onlyFixedWings = selUnits.length > 0 && selUnits.every(u => u.role === 'fixed_wing');

    // 5A：单选固定翼时右键向外拖拽实时调节盘旋圈半径（按机型物理 [min, max] 钳位），地空混选不冲突
    if (onlyFixedWings && endPt && pixelMoved > 14 && worldMoved > 18) {
      const range = selUnits[0].orbitRadiusRange || [100, 350];
      const orbitRadius = Math.max(range[0], Math.min(range[1], Math.round(worldMoved)));
      return {
        type: 'move',
        point: { ...drag.world },
        orbitRadius,
      };
    }

    const hasFacing = Boolean(endPt && pixelMoved > 8 && worldMoved > 1.0);
    const facing = hasFacing ? Math.atan2(endPt.x - drag.world.x, endPt.z - drag.world.z) : undefined;
    return {
      type: 'move',
      point: { ...drag.world },
      ...(hasFacing ? { facing } : {}),
    };
  }

  function refreshRightPreview(localPt, appendFlag, { force = false } = {}) {
    const cmd = buildRightMoveCommand(localPt);
    if (!cmd) return null;
    const ids = selectionCtrl.getSelectedIds();
    const nowMs = performance.now();
    const baseKey = `${ids.join(',')}|${appendFlag ? 1 : 0}|${cmd.point.x.toFixed(2)},${cmd.point.z.toFixed(2)}`;
    const hasFacing = Number.isFinite(cmd.facing);

    // 单车拖动仅改变朝向而落点与选区未变：直接复用已计算的 A* waypoints 仅改朝向
    if (
      drag &&
      ids.length === 1 &&
      drag.lastBaseKey === baseKey &&
      Array.isArray(drag.lastPlanRows) &&
      drag.lastPlanRows.length === 1
    ) {
      const patchedRows = drag.lastPlanRows.map(r => ({
        ...r,
        job: {
          ...r.job,
          facing: hasFacing ? cmd.facing : undefined,
        },
      }));
      drag.lastPlanRows = patchedRows;
      drag.lastFacing = cmd.facing;
      drag.lastAppend = Boolean(appendFlag);
      drag.lastPreviewPt = localPt;
      drag.lastPlanKey = `${baseKey}|${hasFacing ? cmd.facing.toFixed(3) : 'none'}`;
      overlayLayer.setPreview(patchedRows, { append: appendFlag, command: cmd });
      const hasValid = patchedRows.some(r => r.valid);
      arbiter.setCursor(hasValid ? 'move' : 'invalid');
      return patchedRows;
    }

    // 多车拖动：朝向变化 < 0.06 rad 且状态一致时直接复用上次规划
    if (
      drag &&
      !force &&
      drag.lastBaseKey === baseKey &&
      Array.isArray(drag.lastPlanRows) &&
      Boolean(hasFacing) === Boolean(Number.isFinite(drag.lastFacing)) &&
      (!hasFacing || Math.abs(shortestAngleDiff(drag.lastFacing, cmd.facing)) < 0.06)
    ) {
      return drag.lastPlanRows;
    }

    // 65ms 节流窗口（首次按下或 baseKey 变更立即放行）
    if (drag && !force && drag.lastBaseKey === baseKey && (nowMs - drag.lastPlanAtMs) < 65) {
      return drag.lastPlanRows;
    }

    const rows = adapter.planCommand(ids, cmd, appendFlag);
    if (drag) {
      drag.lastBaseKey = baseKey;
      drag.lastPlanRows = rows;
      drag.lastFacing = cmd.facing;
      drag.lastAppend = Boolean(appendFlag);
      drag.lastPlanAtMs = nowMs;
      drag.lastPreviewPt = localPt;
      drag.lastPlanKey = `${baseKey}|${hasFacing ? cmd.facing.toFixed(3) : 'none'}`;
    }
    overlayLayer.setPreview(rows, { append: appendFlag, command: cmd });
    const hasValid = rows.some(r => r.valid);
    arbiter.setCursor(hasValid ? 'move' : 'invalid');
    return rows;
  }

  function commitMoveCommand(command, append = false, plannedRows = null) {
    const ids = selectionCtrl.getSelectedIds();
    if (!ids.length) {
      toast('请先选择载具，再下达移动命令', '', 'warn');
      return null;
    }
    const selUnits = adapter.getUnits().filter(u => ids.includes(u.id));
    const hasFixedWing = selUnits.some(u => u.role === 'fixed_wing');
    const onlyFixedWing = selUnits.length > 0 && selUnits.every(u => u.role === 'fixed_wing');
    const res = adapter.dispatchCommand(ids, command, append, plannedRows);
    let fwRes = null;
    if (hasFixedWing && command?.point) {
      fwRes = hudBridge.setFixedWingOrbitZone?.(ids, command.point, Number.isFinite(command?.orbitRadius) ? command.orbitRadius : null);
    }
    hudBridge.syncCombatHud?.();
    if (!res) return null;
    if (onlyFixedWing && fwRes?.toasted) {
      return res;
    }

    const reasons = [...new Set((res.rows || []).filter(r => r.status === 'rejected').map(r => r.reason).filter(Boolean))];
    if (res.accepted > 0) {
      const title = `${res.accepted} 辆载具${append ? '已追加队列' : '接收航路指令'}`;
      const descParts = [`落点 (${command.point.x.toFixed(0)}, ${command.point.z.toFixed(0)})`];
      if (res.pending > 0) descParts.push(`${res.pending} 个准备路径`);
      if (res.rejected > 0) descParts.push(`${res.rejected} 个不可达：${reasons.join('；')}`);
      toast(title, descParts.join(' · '), res.rejected > 0 ? 'warn' : 'ground');
    } else {
      toast('航路指令被拒收', reasons.join('；') || '目的地不可达，保留原任务', 'danger');
    }
    return res;
  }

  function cancelSelectedUnits() {
    const ids = selectionCtrl.getSelectedIds();
    if (!ids.length) {
      toast('请先选择需要停止的载具', '', 'warn');
      return [];
    }
    const stopped = adapter.cancelUnits(ids);
    hudBridge.cancelFixedWingStrike?.(ids);
    hudBridge.syncCombatHud?.();
    toast(`已停止 ${stopped.length} 辆载具并清空队列`, '地面停车 · 旋翼悬停 · 固定翼继续盘旋', 'warn');
    return stopped;
  }

  async function applyEditorBrushAt(worldPt, brushKind) {
    if (!editor || !worldPt) return;
    if (brushKind === 'raise_terrain') {
      if (lastBrushWorld && Math.hypot(worldPt.x - lastBrushWorld.x, worldPt.z - lastBrushWorld.z) < 6) {
        return;
      }
      lastBrushWorld = { x: worldPt.x, z: worldPt.z };
      editor.applyTerrainHeightBrush({ x: worldPt.x, z: worldPt.z, radius: 22, deltaH: 4.5, mode: 'raise' });
      toast('地形抬升笔刷已应用', `(${worldPt.x.toFixed(0)}, ${worldPt.z.toFixed(0)}) 高度 +4.5m`, 'safe');
      return;
    }
    const protos = b3Manifest?.building_prototypes || [];
    const tall = protos.find(p => (p.bounds_max[1] - p.bounds_min[1]) >= 14.0) || protos[0];
    if (!tall) return;
    const bldgId = `editor_bldg_${Date.now().toString(36)}_${(physics?.addedInstances?.size || 0) + 1}`;
    await editor.addInstanceAt({
      id: bldgId,
      prototype_id: tall.id,
      x: worldPt.x,
      z: worldPt.z,
      yaw_rad: 0.38,
    });
    hudBridge.syncEditorDrawer?.(editor.lastNavResult);
    toast('已于落点部署建筑', `(${worldPt.x.toFixed(0)}, ${worldPt.z.toFixed(0)}) 物理包络实时更新`, 'safe');
  }

  function enterTargetingMode() {
    activeAirStrikeSpec = null;
    airStrikeQueuedInSession = false;
    groundAgQueuedInSession = false;
    overlayLayer.setAirStrikePreview?.(null);
    const groundSelected = adapter.getUnits().some(u => selectionCtrl.getSelectedIds().includes(u.id) && u.role === 'ground');
    if (!groundSelected) {
      toast('请先选择地面作战单位', '固定翼精准打击请从右下角攻击页签选择对应武器', 'warn');
      return false;
    }
    arbiter.enterTargeting();
    toast('进入强制攻地模式', '左键点击地面投影爆炸圈（Shift 连续追加多点）· 右键/Esc 取消', 'danger');
    return true;
  }

  function commitPendingMultiDrop(overrideQueue = null, keepTargeting = false) {
    if (!activeAirStrikeSpec || pendingDropPoints.length === 0) return false;
    const specRef = activeAirStrikeSpec;
    const pts = pendingDropPoints.map(p => ({ ...p }));
    const useQueue = overrideQueue === null
      ? Boolean(pendingDropQueue || airStrikeQueuedInSession)
      : Boolean(overrideQueue || airStrikeQueuedInSession);
    pendingDropPoints = [];
    pendingDropQueue = false;
    if (!keepTargeting) {
      arbiter.exitToPlay();
    } else {
      airStrikeQueuedInSession = true;
      overlayLayer?.setAirStrikePreview?.({
        spec: specRef,
        startPos: null,
        endPos: null,
        hoverPos: pendingHoverWorld,
        dropPoints: [],
      });
    }
    hudBridge.issueFixedWingStrike?.({
      spec: specRef,
      dropPoints: pts,
      targetPos: pts[0],
      queue: useQueue,
    });
    hudBridge.syncCombatHud?.();
    toast('航弹连投轨迹已提交', `共 ${pts.length} 枚投弹坐标已下达`, 'plane');
    return true;
  }

  function enterAirStrikeTargeting(weaponSpec) {
    if (!weaponSpec || !weaponSpec.actionCmd) return false;
    const planeSelected = adapter.getUnits().some(u => selectionCtrl.getSelectedIds().includes(u.id) && u.role === 'fixed_wing');
    if (!planeSelected) {
      toast('请先选择固定翼战机', '', 'warn');
      return false;
    }
    activeAirStrikeSpec = { ...weaponSpec };
    pendingDropPoints = [];
    pendingDropQueue = false;
    airStrikeQueuedInSession = false;
    groundAgQueuedInSession = false;
    overlayLayer.setAirStrikePreview?.({
      spec: activeAirStrikeSpec,
      startPos: null,
      endPos: null,
      hoverPos: pendingHoverWorld,
      dropPoints: [],
    });
    arbiter.enterTargeting();
    const hint = activeAirStrikeSpec.inputMode === 'drag_line'
      ? `按住左键拖拽画出【${activeAirStrikeSpec.shortLabel || activeAirStrikeSpec.name}】进场方向与覆盖带 · Shift 排队 · 右键/Esc 取消`
      : activeAirStrikeSpec.inputMode === 'multi_point'
        ? `左键连续点击设定 ${Math.max(1, activeAirStrikeSpec.ammo || 1)} 枚落弹点 · 右键/Space/Enter 立即确认 · Esc 取消`
        : `左键点击地面指定【${activeAirStrikeSpec.shortLabel || activeAirStrikeSpec.name}】打击点（射程 ${activeAirStrikeSpec.minRangeM}–${activeAirStrikeSpec.maxRangeM}m）· Shift 排队`;
    toast(`精准打击瞄准：${activeAirStrikeSpec.shortLabel || activeAirStrikeSpec.name}`, hint, 'plane');
    return true;
  }

  // --- 事件绑定 ---
  const on = (target, type, handler, opts) => {
    if (!target) return;
    target.addEventListener(type, handler, opts);
    cleanups.push(() => target.removeEventListener(type, handler, opts));
  };

  if (minimapBox) {
    cleanups.push(
      cameraCtrl.bindMinimapDrag(minimapBox, b1Manifest?.bounds, {
        screenToWorld: minimapScreenToWorld,
        onRightClickWorld: (pt, ev) => {
          if (state.paused) return;
          commitMoveCommand({ type: 'move', point: { x: pt.x, z: pt.z } }, Boolean(ev?.shiftKey));
        },
      }),
    );
  }

  on(host, 'mousedown', e => {
    if (e.button === 1 && !isHudBlockingTarget(e.target, 1)) {
      e.preventDefault();
    }
  });

  on(host, 'contextmenu', e => {
    e.preventDefault();
  });

  if (typeof document !== 'undefined') {
    on(document, 'contextmenu', e => {
      if (!hudBridge.isEditableTarget?.(e.target)) {
        e.preventDefault();
      }
    });
  }

  on(host, 'pointerdown', e => {
    syncShiftHeld(e.shiftKey);
    if (state.paused || isHudBlockingTarget(e.target, e.button)) return;
    if (arbiter.getMode() === INTERACTION_MODES.WHEEL) {
      overlayLayer.closeWheel();
    }
    const local = toLocal(e);
    lastPointerLocal = local;

    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      e.preventDefault();
      if (e.button === 1 && !e.altKey) {
        drag = { kind: 'cam_pan', pointerId: e.pointerId, start: local, last: { x: e.clientX, y: e.clientY } };
      } else {
        drag = { kind: 'cam_rotate', pointerId: e.pointerId, start: local, last: { x: e.clientX, y: e.clientY } };
      }
      host.setPointerCapture?.(e.pointerId);
      return;
    }

    if (e.button === 2) {
      e.preventDefault();
      const curMode = arbiter.getMode();
      if (
        curMode === INTERACTION_MODES.TARGETING
        && activeAirStrikeSpec?.inputMode === 'multi_point'
        && pendingDropPoints.length > 0
      ) {
        commitPendingMultiDrop(Boolean(e.shiftKey), Boolean(e.shiftKey));
        return;
      }
      if (
        curMode === INTERACTION_MODES.EDITOR ||
        curMode === INTERACTION_MODES.TARGETING ||
        curMode === INTERACTION_MODES.DEPLOY
      ) {
        arbiter.exitToPlay();
        toast('已退出当前子模式', '返回作战指挥态', 'select');
        return;
      }
      if (!selectionCtrl.getSelectedIds().length) {
        toast('请先选择载具，再下达移动命令', '', 'warn');
        return;
      }
      const ground = adapter.queryGroundAt(local.x, local.y);
      if (!ground || ground.status === 'unsupported' || !ground.point) {
        toast('落点缺少有效地面支撑，未下令', '', 'warn');
        return;
      }
      drag = {
        kind: 'right_move',
        pointerId: e.pointerId,
        start: local,
        last: local,
        lastPreviewPt: local,
        lastPlanAtMs: 0,
        lastPlanKey: '',
        lastBaseKey: '',
        lastPlanRows: null,
        lastFacing: undefined,
        lastAppend: Boolean(e.shiftKey),
        world: ground.point,
        append: e.shiftKey,
        previewShown: false,
        previewTimer: null,
      };
      host.setPointerCapture?.(e.pointerId);
      const activeRightDrag = drag;
      // 仅在长按 (>110ms) 或拖动 (>6px) 时显示直角光标与横幅预览，避免短按快点触发单帧合成层闪烁
      drag.previewTimer = setTimeout(() => {
        if (drag !== activeRightDrag || activeRightDrag.previewShown) return;
        activeRightDrag.previewTimer = null;
        activeRightDrag.previewShown = true;
        refreshRightPreview(activeRightDrag.last || activeRightDrag.start, Boolean(activeRightDrag.lastAppend || activeRightDrag.append), { force: true });
      }, 110);
      return;
    }

    if (e.button === 0) {
      const curMode = arbiter.getMode();
      if (curMode === INTERACTION_MODES.DEPLOY) {
        e.preventDefault();
        const slug = arbiter.getDeploySlug() || 'US_M1A2';
        const ground = adapter.queryGroundAt(local.x, local.y);
        if (!ground || ground.status === 'unsupported' || !ground.point) {
          toast('部署落点无效', '请指向 9km 有效地形内部', 'warn');
          return;
        }
        const keepMode = Boolean(e.shiftKey);
        if (!keepMode) {
          arbiter.exitToPlay();
        }
        Promise.resolve(hudBridge.deployVehicleAt?.(slug, ground.point, { keepMode }));
        return;
      }

      if (curMode === INTERACTION_MODES.TARGETING) {
        e.preventDefault();
        const ground = adapter.queryGroundAt(local.x, local.y);
        if (!ground || ground.status === 'unsupported' || !ground.point) {
          toast('攻地落点无效', '请选择有效地面位置', 'danger');
          return;
        }
        // 3A：固定翼《断箭》+《Ebbtide》精准打击（拖线扫射/火箭 vs 多点连投航弹 vs 点选导弹）
        if (activeAirStrikeSpec) {
          if (activeAirStrikeSpec.inputMode === 'drag_line') {
            drag = {
              kind: 'air_strike_drag',
              pointerId: e.pointerId,
              start: local,
              last: local,
              startWorld: { ...ground.point },
              endWorld: { ...ground.point },
              spec: activeAirStrikeSpec,
              append: Boolean(e.shiftKey),
            };
            overlayLayer.setAirStrikePreview?.({
              spec: activeAirStrikeSpec,
              startPos: drag.startWorld,
              endPos: drag.endWorld,
              hoverPos: drag.endWorld,
              dropPoints: [],
            });
            host.setPointerCapture?.(e.pointerId);
            return;
          }
          if (activeAirStrikeSpec.inputMode === 'multi_point') {
            const maxDrops = Math.max(1, Number(activeAirStrikeSpec.ammo) || 1);
            if (e.shiftKey) pendingDropQueue = true;
            pendingDropPoints.push({ ...ground.point });
            pendingHoverWorld = { ...ground.point };
            overlayLayer.setAirStrikePreview?.({
              spec: activeAirStrikeSpec,
              startPos: null,
              endPos: null,
              hoverPos: pendingHoverWorld,
              dropPoints: pendingDropPoints.map(p => ({ ...p })),
            });
            if (pendingDropPoints.length >= maxDrops) {
              commitPendingMultiDrop(Boolean(e.shiftKey || pendingDropQueue || airStrikeQueuedInSession), Boolean(e.shiftKey));
            }
            return;
          }
          const keepMode = Boolean(e.shiftKey);
          const shouldQueue = Boolean(keepMode || airStrikeQueuedInSession);
          const specRef = activeAirStrikeSpec;
          const res = hudBridge.issueFixedWingStrike?.({
            spec: specRef,
            targetPos: ground.point,
            queue: shouldQueue,
          });
          if (res?.ammoExhausted) {
            toast('导弹排队已达余弹上限', res.reason || '已排满当前挂载余弹，按右键或 Esc 退出瞄准', 'warn');
            hudBridge.syncCombatHud?.();
            return;
          }
          if (keepMode) {
            airStrikeQueuedInSession = true;
          } else {
            arbiter.exitToPlay();
          }
          hudBridge.syncCombatHud?.();
          return;
        }
        const ids = selectionCtrl.getSelectedIds();
        const keepMode = Boolean(e.shiftKey);
        const shouldAppend = Boolean(keepMode || groundAgQueuedInSession);
        adapter.dispatchCommand(ids, { type: 'attackGround', point: ground.point }, shouldAppend);
        if (keepMode) {
          groundAgQueuedInSession = true;
          const primaryU = adapter.getUnits().find(it => ids.includes(it.id) && it.role === 'ground');
          const qCount = Array.isArray(primaryU?.attackGroundQueue) ? primaryU.attackGroundQueue.length : 1;
          toast(
            `已追加强制攻地点 (#${qCount})`,
            `目标 (${ground.point.x.toFixed(0)}, ${ground.point.z.toFixed(0)}) · 继续 Shift+左键连点或松开 Shift 点末点`,
            'danger',
          );
        } else {
          arbiter.exitToPlay();
          toast(
            '强制攻地意图已登记',
            `目标 (${ground.point.x.toFixed(0)}, ${ground.point.z.toFixed(0)}) · 待 G04 战斗引擎结算`,
            'danger',
          );
        }
        hudBridge.syncCombatHud?.();
        return;
      }

      if (curMode === INTERACTION_MODES.EDITOR) {
        e.preventDefault();
        const brush = arbiter.getActiveBrush() || 'add_building';
        const ground = adapter.queryGroundAt(local.x, local.y);
        if (!ground || !ground.point) {
          toast('编辑落点无效', '请指向 9km 有效地形内部', 'warn');
          return;
        }
        lastBrushWorld = null;
        applyEditorBrushAt(ground.point, brush);
        if (brush === 'raise_terrain') {
          drag = { kind: 'editor_brush', pointerId: e.pointerId, brush };
          host.setPointerCapture?.(e.pointerId);
        }
        return;
      }

      drag = {
        kind: 'left_select',
        pointerId: e.pointerId,
        start: local,
        last: local,
        append: e.shiftKey,
        moved: false,
      };
      host.setPointerCapture?.(e.pointerId);
    }
  });

  on(window, 'pointermove', e => {
    syncShiftHeld(e.shiftKey);
    if (state.paused) return;
    const rect = host?.getBoundingClientRect?.();
    const inside = Boolean(rect && e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom);
    const local = rect ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : null;
    if (inside && local) lastPointerLocal = local;

    // 轮盘展开期支持全视口无限外延甩选（即使指针越过轮盘或经过 HUD 顶栏/底栏）
    if (overlayLayer.isWheelOpen() && local) {
      overlayLayer.updateWheelPointer?.(local);
      return;
    }

    if (!drag) {
      if (inside && local && !isHudBlockingTarget(e.target, -1)) {
        overlayLayer.setPointerLocal?.(local);
        const curMode = arbiter.getMode();
        if (curMode === INTERACTION_MODES.TARGETING) {
          const ground = adapter.queryGroundAt(local.x, local.y);
          overlayLayer.setTargetingPoint(ground?.point || null);
          if (activeAirStrikeSpec) {
            pendingHoverWorld = ground?.point ? { ...ground.point } : null;
            overlayLayer.setAirStrikePreview?.({
              spec: activeAirStrikeSpec,
              startPos: null,
              endPos: null,
              hoverPos: pendingHoverWorld,
              dropPoints: pendingDropPoints.map(p => ({ ...p })),
            });
          }
          arbiter.setCursor(ground?.point ? 'attack' : 'invalid');
        } else if (curMode === INTERACTION_MODES.DEPLOY) {
          const ground = adapter.queryGroundAt(local.x, local.y);
          arbiter.setCursor(ground?.point ? 'deploy' : 'invalid');
        } else if (curMode === INTERACTION_MODES.PLAY) {
          selectionCtrl.sampleHoverAt(local.x, local.y);
        }
      } else if (!inside) {
        overlayLayer.setPointerLocal?.(null);
        selectionCtrl.setHoveredUnit(null);
      }
      return;
    }

    if (drag.pointerId !== e.pointerId) return;

    if (drag.kind === 'cam_pan') {
      cameraCtrl.panByPixels(e.clientX - drag.last.x, e.clientY - drag.last.y);
      drag.last = { x: e.clientX, y: e.clientY };
      return;
    }
    if (drag.kind === 'cam_rotate') {
      cameraCtrl.rotateByPixels(e.clientX - drag.last.x);
      drag.last = { x: e.clientX, y: e.clientY };
      return;
    }
    if (drag.kind === 'left_select' && local) {
      drag.last = local;
      if (drag.moved || Math.hypot(local.x - drag.start.x, local.y - drag.start.y) > 5) {
        drag.moved = true;
        selectionCtrl.updateDragBox(drag.start, local);
      }
      return;
    }
    if (drag.kind === 'right_move' && local) {
      drag.last = local;
      const nextAppend = Boolean(e.shiftKey || drag.append);
      if (!drag.previewShown) {
        drag.lastAppend = nextAppend;
        if (Math.hypot(local.x - drag.start.x, local.y - drag.start.y) > 6) {
          if (drag.previewTimer) {
            clearTimeout(drag.previewTimer);
            drag.previewTimer = null;
          }
          drag.previewShown = true;
          refreshRightPreview(local, nextAppend, { force: true });
        }
        return;
      }
      const movedPx = drag.lastPreviewPt
        ? Math.hypot(local.x - drag.lastPreviewPt.x, local.y - drag.lastPreviewPt.y)
        : Infinity;
      if (movedPx >= 6 || nextAppend !== drag.lastAppend) {
        refreshRightPreview(local, nextAppend);
      }
      return;
    }
    if (drag.kind === 'air_strike_drag' && local) {
      drag.last = local;
      const ground = adapter.queryGroundAt(local.x, local.y);
      if (ground?.point) {
        let endPt = { ...ground.point };
        const maxL = Number(drag.spec?.strafeLenMaxM) || 0;
        if (maxL > 0) {
          const ddx = endPt.x - drag.startWorld.x;
          const ddz = endPt.z - drag.startWorld.z;
          const len = Math.hypot(ddx, ddz);
          if (len > maxL) {
            const s = maxL / len;
            endPt = {
              x: drag.startWorld.x + ddx * s,
              y: endPt.y,
              z: drag.startWorld.z + ddz * s,
            };
          }
        }
        drag.endWorld = endPt;
        overlayLayer.setAirStrikePreview?.({
          spec: drag.spec,
          startPos: drag.startWorld,
          endPos: drag.endWorld,
          hoverPos: drag.endWorld,
        });
      }
      return;
    }
    if (drag.kind === 'editor_brush' && local) {
      const ground = adapter.queryGroundAt(local.x, local.y);
      if (ground?.point) applyEditorBrushAt(ground.point, drag.brush);
    }
  });

  const handlePointerFinish = (e, cancelled = false) => {
    syncShiftHeld(e.shiftKey);
    if (!drag || drag.pointerId !== e.pointerId) return;
    const finished = drag;
    const local = toLocal(e);
    const commitAllowed = !cancelled && mayCommitPointer(e);
    const cmd = finished.kind === 'right_move' ? buildRightMoveCommand(local) : null;
    cancelActiveDrag();

    if (finished.kind === 'left_select' && !cancelled) {
      if (finished.moved) {
        selectionCtrl.commitBoxSelection(finished.start, local, finished.append);
      } else if (commitAllowed) {
        const hitCluster = adapter.pickClusterAt?.(local.x, local.y, selectionCtrl.getChannelFilter());
        if (hitCluster?.isCluster && Array.isArray(hitCluster.unitIds) && hitCluster.unitIds.length > 0) {
          if (finished.append) {
            const cur = new Set(selectionCtrl.getSelectedIds());
            const allIn = hitCluster.unitIds.every(id => cur.has(id));
            if (allIn) {
              for (const id of hitCluster.unitIds) cur.delete(id);
            } else {
              for (const id of hitCluster.unitIds) cur.add(id);
            }
            selectionCtrl.setSelection([...cur]);
          } else {
            selectionCtrl.setSelection(hitCluster.unitIds);
          }
          toast(`已选中密集战术簇 ${hitCluster.count} 辆单位`, '可右键拖动统一部署或按 Tab 微调', hitCluster.channel);
        } else {
          selectionCtrl.commitPointClick(local, { append: finished.append });
        }
      }
      return;
    }

    if (finished.kind === 'air_strike_drag' && !cancelled && commitAllowed) {
      const keepMode = Boolean(e.shiftKey);
      const shouldQueue = Boolean(keepMode || finished.append || airStrikeQueuedInSession);
      const specRef = finished.spec;
      if (!keepMode) {
        arbiter.exitToPlay();
      } else {
        airStrikeQueuedInSession = true;
        overlayLayer.setAirStrikePreview?.({ spec: specRef, startPos: null, endPos: null, hoverPos: finished.endWorld, dropPoints: [] });
      }
      const dxD = finished.endWorld.x - finished.startWorld.x;
      const dzD = finished.endWorld.z - finished.startWorld.z;
      const distDrag = Math.hypot(dxD, dzD);
      let finalStart = distDrag >= 8 ? { ...finished.startWorld } : null;
      let finalEnd = distDrag >= 8 ? { ...finished.endWorld } : null;
      if (finalStart && finalEnd) {
        const minL = Number(specRef?.strafeLenMinM) || 0;
        const maxL = Number(specRef?.strafeLenMaxM) || 0;
        if (minL > 0 && distDrag < minL) {
          const s = minL / distDrag;
          finalEnd = { x: finalStart.x + dxD * s, y: finalEnd.y, z: finalStart.z + dzD * s };
        } else if (maxL > 0 && distDrag > maxL) {
          const s = maxL / distDrag;
          finalEnd = { x: finalStart.x + dxD * s, y: finalEnd.y, z: finalStart.z + dzD * s };
        }
      }
      hudBridge.issueFixedWingStrike?.({
        spec: specRef,
        startPos: finalStart,
        endPos: finalEnd,
        targetPos: finalStart && finalEnd
          ? { x: (finalStart.x + finalEnd.x) * 0.5, z: (finalStart.z + finalEnd.z) * 0.5 }
          : finished.startWorld,
        queue: shouldQueue,
      });
      hudBridge.syncCombatHud?.();
      return;
    }

    if (finished.kind === 'right_move' && cmd && commitAllowed) {
      const finalAppend = Boolean(e.shiftKey || finished.append);
      const ids = selectionCtrl.getSelectedIds();
      const baseKey = `${ids.join(',')}|${finalAppend ? 1 : 0}|${cmd.point.x.toFixed(2)},${cmd.point.z.toFixed(2)}`;
      let plannedRows = null;
      if (finished.lastBaseKey === baseKey && Array.isArray(finished.lastPlanRows) && finished.lastPlanRows.length > 0) {
        if (ids.length === 1) {
          plannedRows = finished.lastPlanRows.map(r => ({
            ...r,
            job: {
              ...r.job,
              facing: Number.isFinite(cmd.facing) ? cmd.facing : undefined,
            },
          }));
        } else if (
          Boolean(Number.isFinite(cmd.facing)) === Boolean(Number.isFinite(finished.lastFacing)) &&
          (!Number.isFinite(cmd.facing) || Math.abs(shortestAngleDiff(finished.lastFacing, cmd.facing)) < 0.06)
        ) {
          plannedRows = finished.lastPlanRows;
        }
      }
      commitMoveCommand(cmd, finalAppend, plannedRows);
    }
  };

  on(window, 'pointerup', e => handlePointerFinish(e, false));
  on(window, 'pointercancel', e => handlePointerFinish(e, true));

  on(window, 'blur', () => {
    cancelActiveDrag();
    pressedKeys.clear();
    syncShiftHeld(false);
  });

  on(host, 'wheel', e => {
    if (state.paused || isHudBlockingTarget(e.target, -1)) return;
    e.preventDefault();
    cameraCtrl.zoomAtPointer(e.deltaY, e.deltaMode, e.clientX, e.clientY);
  }, { passive: false });

  const MOVE_KEY_SET = new Set(['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'q', 'e']);

  on(window, 'keydown', e => {
    syncShiftHeld(e.shiftKey || e.key === 'Shift');
    if (e.key === 'Escape' || e.code === 'Escape') {
      e.preventDefault();
      if (!escStack || !escStack.handleKeydown(e)) {
        hudBridge.setPaused?.(true);
      }
      return;
    }

    if (e.code === 'F5') {
      e.preventDefault();
      hudBridge.setHudHidden?.(!state.hudHidden);
      return;
    }

    if (state.paused) return;
    if (hudBridge.isEditableTarget?.(e.target)) return;
    if (e.ctrlKey || e.metaKey) return;

    const lk = e.key.toLowerCase();

    // Alt + A / K：进入或退出强制攻地落点模式（避开 WASD 左移）
    if ((e.code === 'KeyK' || (e.code === 'KeyA' && e.altKey)) && !e.repeat) {
      e.preventDefault();
      if (arbiter.getMode() === INTERACTION_MODES.TARGETING) {
        arbiter.exitToPlay();
      } else {
        enterTargetingMode();
      }
      return;
    }

    if (MOVE_KEY_SET.has(lk) && !e.altKey) {
      pressedKeys.add(lk);
      return;
    }

    // Shift+1~9 保存自定义编组；Alt+1~9 召回自定义编组（避开 Ctrl+数字）
    const digitMatch = /^Digit([1-9])$/.exec(e.code);
    if (digitMatch && (e.shiftKey || e.altKey)) {
      e.preventDefault();
      if (e.repeat) return;
      const slot = digitMatch[1];
      if (e.shiftKey) selectionCtrl.saveControlGroup(slot);
      else selectionCtrl.recallControlGroup(slot);
      return;
    }

    if (e.code === 'Space') {
      e.preventDefault();
      if (e.repeat) return;
    }
    if (e.repeat && e.code !== 'Tab') return;

    // 数字 1~6 快选当前出击编队槽位（兼容 1/2/3 默认回归）
    if (/^Digit([1-6])$/.test(e.code) && !e.shiftKey && !e.altKey) {
      selectionCtrl.selectRosterSlot(Number(e.code.slice(5)));
      return;
    }

    if (e.code === 'Tab') {
      e.preventDefault();
      if (lastPointerLocal) {
        selectionCtrl.cycleOverlap(lastPointerLocal, e.shiftKey);
      }
      return;
    }

    if (e.code === 'Space' || e.code === 'Enter') {
      const curMode = arbiter.getMode();
      if (
        curMode === INTERACTION_MODES.TARGETING
        && activeAirStrikeSpec?.inputMode === 'multi_point'
        && pendingDropPoints.length > 0
      ) {
        e.preventDefault();
        commitPendingMultiDrop(Boolean(e.shiftKey), Boolean(e.shiftKey));
        return;
      }
      if (e.code === 'Space') {
        if (
          curMode === INTERACTION_MODES.EDITOR ||
          curMode === INTERACTION_MODES.TARGETING ||
          curMode === INTERACTION_MODES.DEPLOY
        ) {
          arbiter.exitToPlay();
          return;
        }
        if (overlayLayer.isWheelOpen()) {
          overlayLayer.closeWheel();
        } else {
          overlayLayer.openWheel(lastPointerLocal, { mode: 'hold_flick' });
        }
        return;
      }
    }

    if (lk === 'b') {
      hudBridge.setDeployDrawerOpen?.(!state.deployOpen);
    } else if (lk === 'p') {
      hudBridge.setEditorDrawerOpen?.(!state.editorMode);
    } else if (lk === 'u') {
      const nextAsp = state.aspectKey === '16:9'
        ? '21:9'
        : state.aspectKey === '21:9'
          ? 'full'
          : '16:9';
      hudBridge.setAspectProfile?.(nextAsp);
    } else if (lk === 'f') {
      selectionCtrl.focusSelection();
    } else if (lk === 'g') {
      selectionCtrl.toggleFollow();
    } else if (lk === 'x') {
      cancelSelectedUnits();
    } else if (lk === 'h') {
      const id = selectionCtrl.getPrimaryUnitId();
      if (id) {
        combatSystem?.damageUnit?.(id, 28);
        hudBridge.syncCombatHud?.();
      }
    } else if (lk === 'r') {
      cameraCtrl.flyTo(0, 85, 135, 0);
      toast('视野已复位至 9km 核心交战点', '', 'select');
    } else if (lk === 'm') {
      hudBridge.toggleMinimapExpanded?.();
    } else if (lk === 'i') {
      hudBridge.toggleUnitDetailCard?.();
    } else if (e.code === 'Backquote') {
      hudBridge.toggleAirStrip?.();
    }
  });

  on(window, 'keyup', e => {
    pressedKeys.delete(e.key.toLowerCase());
    syncShiftHeld(e.shiftKey && e.key !== 'Shift');
    if (e.code === 'Space' && overlayLayer.isWheelOpen()) {
      e.preventDefault();
      overlayLayer.handleWheelSpaceUp?.();
      hudBridge.syncCombatHud?.();
    }
  });

  on(window, 'blur', () => {
    pressedKeys.clear();
    syncShiftHeld(false);
    if (overlayLayer.isWheelOpen()) {
      overlayLayer.closeWheel();
      hudBridge.syncCombatHud?.();
    }
  });

  function step(dtMs, nowMs) {
    cameraCtrl.step(dtMs, nowMs, pressedKeys);
  }

  function renderOverlay() {
    overlayLayer.render({
      hudHidden: state.hudHidden,
      isEmbedMode: host?.dataset?.embed === '1',
      currentSpan: state.span,
    });
  }

  function dispose() {
    cancelActiveDrag();
    overlayLayer.dispose();
    cameraCtrl?.dispose?.();
    adapter?.dispose?.();
    if (combatSystem) {
      combatSystem.onLabelPointerDown = null;
      combatSystem.onLabelHover = null;
    }
    for (const off of cleanups) {
      try {
        off();
      } catch {
        // 清理时忽略已销毁节点错误
      }
    }
    cleanups.length = 0;
  }

  const publicApi = Object.freeze({
    cameraCtrl,
    selectionCtrl,
    overlayLayer,
    adapter,
    arbiter,
    step,
    renderOverlay,
    cancelSelectedUnits,
    enterTargetingMode,
    getState: () => ({
      mode: arbiter.getMode(),
      activeBrush: arbiter.getActiveBrush(),
      deploySlug: arbiter.getDeploySlug(),
      cursor: arbiter.getCursor(),
      selectedIds: selectionCtrl.getSelectedIds(),
      primaryUnitId: selectionCtrl.getPrimaryUnitId(),
      hoveredUnitId: selectionCtrl.getHoveredUnitId(),
      channelFilter: selectionCtrl.getChannelFilter(),
      followUnitId: cameraCtrl.getFollowUnitId(),
      followModeActive: Boolean(selectionCtrl.isFollowModeActive?.()),
      wheelOpen: overlayLayer.isWheelOpen(),
      wheelState: overlayLayer.getWheelState?.() || null,
      overlay: overlayLayer.getMetrics?.() || null,
      lastCommandResult: adapter.getLastCommandResult(),
      view: cameraCtrl.getViewState(),
      units: adapter.getUnits().map(u => ({
        id: u.id,
        role: u.role,
        jobType: u.job?.type || null,
        queueLength: Array.isArray(u.queue) ? u.queue.length : 0,
        remainingWaypoints: Math.max(0, (u.waypoints?.length || 0) - (u.wpIndex || 0)),
        targetFacing: Number.isFinite(u.targetFacing) ? u.targetFacing : null,
        commandStatus: u.commandStatus?.status || null,
        hasRejected: Boolean(u.lastRejectedCommand),
        groundGuard: Boolean(u.groundGuard),
        hasAttackGroundIntent: Boolean(u.lastAttackGroundIntent),
        attackGroundQueueLength: Array.isArray(u.attackGroundQueue) ? u.attackGroundQueue.length : 0,
        orbitCenter: u.orbitCenter ? { x: u.orbitCenter.x, z: u.orbitCenter.z } : null,
      })),
    }),
    setView: async opts => {
      const res = await cameraCtrl.setView(opts);
      renderOverlay();
      return res;
    },
    setSelection: ids => selectionCtrl.setSelection(ids),
    setChannelFilter: filter => selectionCtrl.setChannelFilter(filter),
    planCommand: (ids, command, append = false) => adapter.planCommand(ids, command, append),
    dispatchCommand: (ids, command, append = false) => commitMoveCommandByIds(ids, command, append),
    cancelUnits: ids => {
      const stopped = adapter.cancelUnits(ids);
      hudBridge.syncCombatHud?.();
      return stopped;
    },
    toggleFollow: () => selectionCtrl.toggleFollow(),
    resetFollowOffset: () => cameraCtrl.resetFollowOffset(),
    focusSelection: () => selectionCtrl.focusSelection(),
    openWheel: (pt, opts) => overlayLayer.openWheel(pt, opts),
    closeWheel: () => overlayLayer.closeWheel(),
    enterEditorBrush: brush => arbiter.enterEditorBrush(brush),
    enterDeployMode: slug => arbiter.enterDeployMode(slug),
    enterAirStrikeTargeting,
    getActiveAirStrikeSpec: () => activeAirStrikeSpec,
    getAirStrikePreview: () => overlayLayer?.getAirStrikePreview?.() || null,
    commitPendingMultiDrop,
    exitToPlay: () => arbiter.exitToPlay(),
    saveControlGroup: key => selectionCtrl.saveControlGroup(key),
    recallControlGroup: key => selectionCtrl.recallControlGroup(key),
    queryGroundAt: (lx, ly) => adapter.queryGroundAt(lx, ly),
    pickUnits: (lx, ly, filter) => adapter.pickUnits(lx, ly, filter),
    projectUnit: id => adapter.projectUnit(id),
    dispose,
  });

  function commitMoveCommandByIds(ids, command, append = false) {
    const res = adapter.dispatchCommand(ids, command, append);
    hudBridge.syncCombatHud?.();
    return res;
  }

  return publicApi;
}
