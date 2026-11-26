/**
 * Scavenger 操作交互系统 —— 多选集合、框选矩形、悬停高亮、重叠循环与战术编组。
 */

import { cycleOverlappingUnits } from '../界面规范/图标与标绘.mjs';
import { isUnitAllowedByChannel } from './契约与仲裁.mjs';
import { projectAirAnchorToGroundFocus } from './镜头控制.mjs';

const VALID_CHANNELS = new Set(['all', 'ground', 'heli', 'plane', 'air']);

export function resolveSelectionChannelForUnits(units) {
  if (!units || !units.length) return 'all';
  const hasGround = units.some(u => u.role === 'ground');
  const hasHeli = units.some(u => u.role === 'helicopter');
  const hasPlane = units.some(u => u.role === 'fixed_wing');
  if (hasGround && !hasHeli && !hasPlane) return 'ground';
  if (!hasGround && hasHeli && !hasPlane) return 'heli';
  if (!hasGround && !hasHeli && hasPlane) return 'plane';
  if (!hasGround && (hasHeli || hasPlane)) return 'air';
  return 'all';
}

export function createSelectionAndGroupController({
  host,
  adapter,
  cameraCtrl = null,
  onChange = null,
  onHoverChange = null,
  onToast = null,
} = {}) {
  const selectedIds = new Set();
  const controlGroups = new Map();
  let primaryUnitId = null;
  let hoveredUnitId = null;
  let channelFilter = 'all';
  let followModeActive = false;
  let overlapIds = [];
  let lastClick = null; // { id, time }
  let lastSlotPress = null; // { slotIdx, time }
  let lastHoverSampleMs = 0;

  // 左键框选矩形节点（挂载在画布容器内，z-index 严守全局层级）
  let dragBoxEl = null;
  if (host && typeof document !== 'undefined') {
    dragBoxEl = document.getElementById('scv-drag-box');
    if (!dragBoxEl) {
      dragBoxEl = document.createElement('div');
      dragBoxEl.id = 'scv-drag-box';
      dragBoxEl.className = 'tactical-drag-box scv-drag-box';
      dragBoxEl.hidden = true;
      host.appendChild(dragBoxEl);
    }
  }

  function getAliveUnits() {
    return (adapter?.getUnits?.() || []).filter(u => isUnitAllowedByChannel(u, 'all'));
  }

  function getAllowedUnits() {
    return (adapter?.getUnits?.() || []).filter(u => isUnitAllowedByChannel(u, channelFilter));
  }

  function syncFollowRelay() {
    if (!cameraCtrl) return;
    if (!followModeActive) {
      if (cameraCtrl.getFollowUnitId?.()) {
        cameraCtrl.stopFollowing({ notify: true });
      }
      return;
    }
    if (!primaryUnitId || selectedIds.size === 0) {
      followModeActive = false;
      cameraCtrl.stopFollowing({ notify: true });
      return;
    }
    if (cameraCtrl.getFollowUnitId?.() !== primaryUnitId) {
      cameraCtrl.setFollowUnit(primaryUnitId, {
        anchorFn: id => adapter?.getUnitFocus?.(id) || null,
        notify: true,
      });
    }
  }

  function notifyCameraFollowStopped() {
    if (followModeActive) {
      followModeActive = false;
      onChange?.({
        selectedIds: [...selectedIds],
        primaryUnitId,
        channelFilter,
        followModeActive,
      });
    }
  }

  function syncPrimaryAndAdapter() {
    const allowedSet = new Set(getAllowedUnits().map(u => u.id));
    for (const id of [...selectedIds]) {
      if (!allowedSet.has(id)) selectedIds.delete(id);
    }
    if (!primaryUnitId || !selectedIds.has(primaryUnitId)) {
      primaryUnitId = selectedIds.values().next().value || null;
    }
    adapter?.setSelection?.([...selectedIds], primaryUnitId);
    syncFollowRelay();
    onChange?.({
      selectedIds: [...selectedIds],
      primaryUnitId,
      channelFilter,
      followModeActive,
    });
  }

  function setSelection(ids, { silent = false } = {}) {
    const allowedSet = new Set(getAllowedUnits().map(u => u.id));
    selectedIds.clear();
    for (const id of ids || []) {
      if (allowedSet.has(id)) selectedIds.add(id);
    }
    primaryUnitId = selectedIds.values().next().value || null;
    adapter?.setSelection?.([...selectedIds], primaryUnitId);
    syncFollowRelay();
    if (!silent) {
      onChange?.({
        selectedIds: [...selectedIds],
        primaryUnitId,
        channelFilter,
        followModeActive,
      });
    }
    return [...selectedIds];
  }

  function setHoveredUnit(unitId) {
    const next = unitId || null;
    if (hoveredUnitId === next) return;
    hoveredUnitId = next;
    adapter?.setHoveredUnit?.(hoveredUnitId);
    onHoverChange?.(hoveredUnitId);
  }

  function sampleHoverAt(localX, localY, nowMs = performance.now()) {
    if (nowMs - lastHoverSampleMs < 50) return hoveredUnitId;
    lastHoverSampleMs = nowMs;
    const candidates = adapter?.pickUnits?.(localX, localY, channelFilter) || [];
    setHoveredUnit(candidates[0] || null);
    return hoveredUnitId;
  }

  function setChannelFilter(nextFilter) {
    if (!VALID_CHANNELS.has(nextFilter)) return channelFilter;
    channelFilter = nextFilter;
    overlapIds = [];
    adapter?.setChannelFilter?.(channelFilter);
    syncPrimaryAndAdapter();
    return channelFilter;
  }

  function toggleUnit(unitId, append = false) {
    const unit = getAllowedUnits().find(u => u.id === unitId);
    if (!unit) return [...selectedIds];
    if (!append) {
      setSelection([unitId]);
      return [...selectedIds];
    }
    if (selectedIds.has(unitId)) {
      selectedIds.delete(unitId);
    } else {
      selectedIds.add(unitId);
    }
    syncPrimaryAndAdapter();
    return [...selectedIds];
  }

  function selectSameTypeInViewport(unitId, append = false) {
    const target = getAllowedUnits().find(u => u.id === unitId);
    if (!target) return [];
    const matchKey = target.slug || target.iconKind || target.role;
    const matching = getAllowedUnits().filter(u => {
      const sameType = (u.slug || u.iconKind || u.role) === matchKey;
      if (!sameType) return false;
      const proj = adapter?.projectUnit?.(u.id);
      return Boolean(proj?.visible);
    }).map(u => u.id);

    const next = append ? [...new Set([...selectedIds, ...matching])] : matching;
    setSelection(next);
    if (next.length) {
      onToast?.(`已选中视口内 ${next.length} 辆同类载具`, target.shortName || target.name, 'select');
    }
    return next;
  }

  function updateDragBox(startPt, curPt) {
    if (!dragBoxEl) return;
    dragBoxEl.hidden = false;
    const left = Math.min(startPt.x, curPt.x);
    const top = Math.min(startPt.y, curPt.y);
    const width = Math.abs(curPt.x - startPt.x);
    const height = Math.abs(curPt.y - startPt.y);
    dragBoxEl.style.left = `${left}px`;
    dragBoxEl.style.top = `${top}px`;
    dragBoxEl.style.width = `${width}px`;
    dragBoxEl.style.height = `${height}px`;
  }

  function hideDragBox() {
    if (dragBoxEl) dragBoxEl.hidden = true;
  }

  function commitBoxSelection(startPt, endPt, append = false) {
    hideDragBox();
    const minX = Math.min(startPt.x, endPt.x);
    const maxX = Math.max(startPt.x, endPt.x);
    const minY = Math.min(startPt.y, endPt.y);
    const maxY = Math.max(startPt.y, endPt.y);
    const picked = getAllowedUnits().filter(u => {
      const p = adapter?.projectUnit?.(u.id);
      return Boolean(p?.visible && p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY);
    }).map(u => u.id);

    overlapIds = [];
    const next = append ? [...new Set([...selectedIds, ...picked])] : picked;
    setSelection(next);
    if (picked.length > 0) {
      onToast?.(`已框选 ${next.length} 辆载具`, '右键拖动设定编队队形与朝向', 'select');
    }
    return next;
  }

  function commitPointClick(localPt, { append = false, explicitUnitId = null, nowMs = performance.now() } = {}) {
    hideDragBox();
    const candidates = explicitUnitId
      ? [explicitUnitId, ...(adapter?.pickUnits?.(localPt.x, localPt.y, channelFilter) || []).filter(id => id !== explicitUnitId)]
      : (adapter?.pickUnits?.(localPt.x, localPt.y, channelFilter) || []);
    overlapIds = candidates;

    if (!candidates.length) {
      lastClick = null;
      if (!append && selectedIds.size > 0) {
        setSelection([]);
      }
      return [];
    }

    const hitId = candidates[0];
    const isDouble = Boolean(lastClick && lastClick.id === hitId && (nowMs - lastClick.time) < 350);
    lastClick = { id: hitId, time: nowMs };

    if (isDouble) {
      return selectSameTypeInViewport(hitId, append);
    }

    toggleUnit(hitId, append);
    if (candidates.length > 1) {
      onToast?.(`指针位置重叠 ${candidates.length} 个单位`, '保持鼠标原地按 Tab 循环切换', 'select');
    }
    return [...selectedIds];
  }

  function cycleOverlap(localPt, reverse = false) {
    if (!localPt) return null;
    const pickedIds = adapter?.pickUnits?.(localPt.x, localPt.y, channelFilter) || [];
    if (!pickedIds.length) return null;
    const candidates = pickedIds.map(id => ({ id }));
    const nextItem = cycleOverlappingUnits(candidates, primaryUnitId, reverse);
    if (!nextItem) return null;
    overlapIds = pickedIds;
    setSelection([nextItem.id]);
    const idx = pickedIds.indexOf(nextItem.id) + 1;
    const unit = getAllowedUnits().find(u => u.id === nextItem.id);
    onToast?.(`重叠循环 ${idx}/${pickedIds.length}`, unit?.name || nextItem.id, 'select');
    return nextItem.id;
  }

  function projectUnitFocusToGround(pos) {
    if (!pos) return null;
    if (cameraCtrl?.projectAnchorToGround) {
      return cameraCtrl.projectAnchorToGround(pos, cameraCtrl.camTarget.angle);
    }
    return projectAirAnchorToGroundFocus(
      pos,
      cameraCtrl?.camTarget?.angle ?? 0,
      (x, z) => adapter?.queryGroundHeight?.(x, z) ?? 0,
    );
  }

  function resolveFocusSpan(units, spanLimit = 260) {
    const curSpan = cameraCtrl?.camTarget?.span ?? 135;
    if (Array.isArray(units) && units.length > 0) {
      if (units.every(u => u.role === 'fixed_wing')) {
        return Math.max(180, Math.min(curSpan, 320));
      }
      if (units.every(u => u.role === 'helicopter')) {
        return Math.min(curSpan, 240);
      }
    }
    return Math.min(curSpan, spanLimit);
  }

  function getControlGroupAliveIds(slotKey) {
    const key = String(slotKey);
    const saved = controlGroups.get(key);
    if (!saved || !saved.length) return [];
    const aliveSet = new Set(getAliveUnits().map(u => u.id));
    return saved.filter(id => aliveSet.has(id));
  }

  function selectRosterSlot(slotOneBased, { nowMs = performance.now(), focusCam = false } = {}) {
    const idx = Number(slotOneBased) - 1;
    const roster = adapter?.getUnits?.() || [];
    const target = roster[idx];
    if (!target || !isUnitAllowedByChannel(target, 'all')) return null;

    if (channelFilter !== 'all' && !isUnitAllowedByChannel(target, channelFilter)) {
      setChannelFilter('all');
    }

    const isDoublePress = Boolean(lastSlotPress && lastSlotPress.slotIdx === idx && (nowMs - lastSlotPress.time) < 350);
    lastSlotPress = { slotIdx: idx, time: nowMs };
    setSelection([target.id]);

    if ((focusCam || isDoublePress) && cameraCtrl) {
      const pos = adapter?.getUnitFocus?.(target.id);
      const proj = projectUnitFocusToGround(pos);
      if (proj) {
        const targetSpan = resolveFocusSpan([target], 260);
        cameraCtrl.flyTo(proj.x, proj.z, targetSpan, cameraCtrl.camTarget.angle, {
          keepFollow: followModeActive,
          focusFn: () => projectUnitFocusToGround(adapter?.getUnitFocus?.(target.id)),
        });
      }
    }
    return target.id;
  }

  function saveControlGroup(slotKey) {
    const key = String(slotKey);
    const ids = [...selectedIds];
    controlGroups.set(key, ids);
    onChange?.({
      selectedIds: [...selectedIds],
      primaryUnitId,
      channelFilter,
      followModeActive,
    });
    onToast?.(`已保存编组 ${key}`, `${ids.length} 辆载具 · 点击底栏槽位或 Alt+${key} 召回`, 'select');
    return ids;
  }

  function recallControlGroup(slotKey) {
    const key = String(slotKey);
    const saved = controlGroups.get(key);
    if (!saved || !saved.length) {
      onToast?.(`编组 ${key} 尚未保存`, `选中单位后按 Shift+${key} 保存`, 'warn');
      return [];
    }
    const aliveMap = new Map(getAliveUnits().map(u => [u.id, u]));
    const validUnits = saved.map(id => aliveMap.get(id)).filter(Boolean);
    if (!validUnits.length) {
      onToast?.(`编组 ${key} 中没有可用单位`, '', 'warn');
      return [];
    }
    const autoChannel = resolveSelectionChannelForUnits(validUnits);
    channelFilter = autoChannel;
    adapter?.setChannelFilter?.(channelFilter);
    setSelection(validUnits.map(u => u.id));
    onToast?.(`已召回编组 ${key}`, `${validUnits.length} 辆载具 · 频道 ${autoChannel}`, 'select');
    return validUnits.map(u => u.id);
  }

  function focusSelection({ spanLimit = 260 } = {}) {
    const chosen = getAllowedUnits().filter(u => selectedIds.has(u.id));
    if (!chosen.length) {
      onToast?.('请先选择需要聚焦的载具', '', 'warn');
      return null;
    }
    const chosenIds = chosen.map(u => u.id);
    const computeCentroid = () => {
      const pts = chosenIds
        .map(id => projectUnitFocusToGround(adapter?.getUnitFocus?.(id)))
        .filter(Boolean);
      if (!pts.length) return null;
      return {
        x: pts.reduce((a, p) => a + p.x, 0) / pts.length,
        z: pts.reduce((a, p) => a + p.z, 0) / pts.length,
      };
    };
    const centroid = computeCentroid();
    if (!centroid) return null;
    const targetSpan = resolveFocusSpan(chosen, spanLimit);
    if (followModeActive) {
      cameraCtrl?.resetFollowOffset?.();
    }
    cameraCtrl?.flyTo?.(centroid.x, centroid.z, targetSpan, cameraCtrl.camTarget.angle, {
      keepFollow: followModeActive,
      focusFn: computeCentroid,
    });
    onToast?.(
      `已聚焦 ${chosen.length} 辆载具`,
      followModeActive ? '跟随偏移已归零 · 机身回归正中央' : '按 G 或点「跟随」开启持续跟随',
      'select',
    );
    return { x: centroid.x, z: centroid.z, span: targetSpan };
  }

  function toggleFollow() {
    if (!cameraCtrl) return null;
    if (followModeActive || cameraCtrl.getFollowUnitId?.()) {
      followModeActive = false;
      cameraCtrl.stopFollowing({ notify: true });
      onChange?.({
        selectedIds: [...selectedIds],
        primaryUnitId,
        channelFilter,
        followModeActive,
      });
      onToast?.('已退出持续跟随', '再次按 G 或点击「跟随」可重新开启', 'select');
      return null;
    }
    const targetId = primaryUnitId || [...selectedIds][0] || null;
    if (!targetId) {
      onToast?.('请先选择需要跟随的载具', '支持单选或多选主车持续跟随', 'warn');
      return null;
    }
    followModeActive = true;
    cameraCtrl.setFollowUnit(targetId, {
      anchorFn: id => adapter?.getUnitFocus?.(id) || null,
      notify: true,
    });
    onChange?.({
      selectedIds: [...selectedIds],
      primaryUnitId,
      channelFilter,
      followModeActive,
    });
    const unit = getAliveUnits().find(u => u.id === targetId);
    onToast?.(`持续跟随：${unit?.shortName || targetId}`, '移动/旋转均保持跟随 · 按 F 归中 · 再按 G 或 R 退出', 'select');
    return targetId;
  }

  return Object.freeze({
    getSelectedIds: () => [...selectedIds],
    getPrimaryUnitId: () => primaryUnitId,
    getHoveredUnitId: () => hoveredUnitId,
    getChannelFilter: () => channelFilter,
    isFollowModeActive: () => followModeActive,
    getControlGroup: key => [...(controlGroups.get(String(key)) || [])],
    getControlGroupAliveIds,
    notifyCameraFollowStopped,
    setSelection,
    setHoveredUnit,
    sampleHoverAt,
    setChannelFilter,
    toggleUnit,
    selectSameTypeInViewport,
    updateDragBox,
    hideDragBox,
    commitBoxSelection,
    commitPointClick,
    cycleOverlap,
    selectRosterSlot,
    saveControlGroup,
    recallControlGroup,
    focusSelection,
    toggleFollow,
  });
}
