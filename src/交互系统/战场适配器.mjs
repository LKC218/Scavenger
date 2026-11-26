/**
 * Scavenger 操作交互系统 —— 战场适配器（连接交互输入层与 Three.js 场景 / TacticalVehicleAndCombatSystem）。
 * 严守 CQS 分离：所有状态改变经由 dispatchCommand / cancelUnits 路由，不直接篡改规则内部数据。
 */

import { isUnitAllowedByChannel } from './契约与仲裁.mjs';
import {
  resolveZoomIconTier,
  getZoomTierIconSize,
  clusterScreenUnits,
} from '../界面规范/图标与标绘.mjs';

export function createBattlefieldAdapter({
  host,
  camera,
  terrainPool,
  support,
  physics,
  combatSystem,
  THREE,
  getSpan = null,
} = {}) {
  const raycaster = THREE ? new THREE.Raycaster() : null;
  const ndcVec = THREE ? new THREE.Vector2() : null;
  const projVec = THREE ? new THREE.Vector3() : null;
  const groundPlane = THREE ? new THREE.Plane(new THREE.Vector3(0, 1, 0), 0) : null;
  const planeHit = THREE ? new THREE.Vector3() : null;

  const isHostDomElement = Boolean(host && typeof Element === 'function' && host instanceof Element);
  let observedW = Math.max(1, Number(host?.clientWidth) || 1208);
  let observedH = Math.max(1, Number(host?.clientHeight) || 680);
  let hostResizeObserver = null;

  function syncHostSize() {
    if (!host) return;
    const cw = Number(host.clientWidth);
    const ch = Number(host.clientHeight);
    if (cw > 0) observedW = Math.max(1, Math.round(cw));
    if (ch > 0) observedH = Math.max(1, Math.round(ch));
  }

  if (isHostDomElement && typeof ResizeObserver === 'function') {
    hostResizeObserver = new ResizeObserver(entries => {
      const entry = entries[entries.length - 1];
      const cr = entry?.contentRect;
      if (cr && cr.width > 0 && cr.height > 0) {
        observedW = Math.max(1, Math.round(cr.width));
        observedH = Math.max(1, Math.round(cr.height));
      }
    });
    hostResizeObserver.observe(host);
  }
  if (isHostDomElement && typeof window !== 'undefined') {
    window.addEventListener('resize', syncHostSize);
  }

  function dispose() {
    if (hostResizeObserver) {
      hostResizeObserver.disconnect();
      hostResizeObserver = null;
    }
    if (isHostDomElement && typeof window !== 'undefined') {
      window.removeEventListener('resize', syncHostSize);
    }
  }

  function getHostSize() {
    if (!isHostDomElement) {
      const w = Math.max(1, Number(host?.clientWidth) || 1208);
      const h = Math.max(1, Number(host?.clientHeight) || 680);
      return { width: w, height: h };
    }
    return { width: observedW, height: observedH };
  }

  function getPixelsPerMeter(widthOverride = null) {
    const width = Number.isFinite(widthOverride) && widthOverride > 0 ? widthOverride : getHostSize().width;
    const viewW = Math.max(1, (camera?.right ?? 100) - (camera?.left ?? -100));
    return width / viewW;
  }

  function projectWorld(wx, wy, wz) {
    if (!camera || !projVec) return { x: 0, y: 0, visible: false, ppm: 1 };
    const { width, height } = getHostSize();
    projVec.set(wx, wy, wz).project(camera);
    const sx = (projVec.x * 0.5 + 0.5) * width;
    const sy = (-projVec.y * 0.5 + 0.5) * height;
    const visible =
      projVec.z >= -1.05 &&
      projVec.z <= 1.05 &&
      Math.abs(projVec.x) <= 1.04 &&
      Math.abs(projVec.y) <= 1.04;
    return { x: sx, y: sy, visible, ppm: getPixelsPerMeter(width) };
  }

  function getUnits() {
    return (combatSystem?.heroUnits || []).filter(u => !u?.evacuated);
  }

  function getUnitFocus(id) {
    const heroList = combatSystem?.heroUnits;
    if (!heroList) return null;
    for (let i = 0; i < heroList.length; i += 1) {
      const u = heroList[i];
      if (!u || u.evacuated || u.id !== id) continue;
      const p = u.node?.position;
      if (!p) return null;
      return { x: p.x, y: p.y, z: p.z, role: u.role };
    }
    return null;
  }

  function getPickables() {
    return getUnits().map(u => ({
      id: u.id,
      role: u.role,
      iconKind: u.iconKind,
      radius: u.pickRadius ?? 5.4,
      center: {
        x: u.node.position.x,
        y: u.node.position.y + (u.role === 'ground' ? 1.2 : 1.8),
        z: u.node.position.z,
      },
    }));
  }

  function projectUnit(id) {
    const u = getUnits().find(it => it.id === id);
    if (!u) return { x: 0, y: 0, visible: false, ppm: 1 };
    const cy = u.node.position.y + (u.role === 'ground' ? 1.2 : 1.8);
    return projectWorld(u.node.position.x, cy, u.node.position.z);
  }

  function getCurrentSpan() {
    if (typeof getSpan === 'function') {
      const sp = getSpan();
      if (Number.isFinite(sp) && sp > 0) return sp;
    }
    const { width, height } = getHostSize();
    const aspect = width / Math.max(1, height);
    const viewW = Math.max(1, (camera?.right ?? 100) - (camera?.left ?? -100));
    return viewW / Math.max(0.5, aspect);
  }

  function buildIconNodes(channelFilter = 'all') {
    const span = getCurrentSpan();
    const tier = resolveZoomIconTier(span);
    if (tier === 'none') {
      return { span, tier, nodes: [] };
    }
    const projectedItems = [];
    for (const u of getUnits()) {
      if (!isUnitAllowedByChannel(u, channelFilter)) continue;
      const pos = u.node?.position;
      if (!pos) continue;
      const scr = projectWorld(pos.x, pos.y, pos.z);
      if (!scr.visible) continue;
      projectedItems.push({
        id: u.id,
        channel: u.channel || (u.role === 'helicopter' ? 'heli' : u.role === 'fixed_wing' ? 'plane' : 'ground'),
        iconKind: u.iconKind || 'mbt',
        x: scr.x,
        y: scr.y,
        hp: u.hp ?? 100,
        alive: u.alive !== false,
        visible: true,
        role: u.role,
      });
    }
    if (tier === 'compact') {
      return {
        span,
        tier,
        nodes: projectedItems.map(it => ({
          isCluster: false,
          id: it.id,
          channel: it.channel,
          iconKind: it.iconKind,
          count: 1,
          unitIds: [it.id],
          screenPt: { x: it.x, y: it.y - 2 },
        })),
      };
    }
    return {
      span,
      tier,
      nodes: clusterScreenUnits(projectedItems, { radiusPx: 18, minClusterSize: 3 }),
    };
  }

  function pickClusterAt(localX, localY, channelFilter = 'all') {
    const { tier, nodes } = buildIconNodes(channelFilter);
    if (tier !== 'chip' && tier !== 'overview') return null;
    const { w, h } = getZoomTierIconSize(tier);
    const hw = w * 0.5 + 4;
    const hh = h * 0.5 + 4;
    let best = null;
    let bestDist = Infinity;
    for (const node of nodes) {
      if (!node.isCluster) continue;
      const dx = Math.abs(localX - node.screenPt.x);
      const dy = Math.abs(localY - node.screenPt.y);
      if (dx <= hw && dy <= hh) {
        const d = Math.hypot(dx, dy);
        if (d < bestDist) {
          bestDist = d;
          best = node;
        }
      }
    }
    return best;
  }

  /**
   * 真实 3D 机身投影 + L1~L3 2D 战术图标/簇矩形热区双重拾取：
   * 支持地面与 234m 固定翼、30m 直升机及中远景聚合筹码点击。
   */
  function pickUnits(localX, localY, channelFilter = 'all') {
    const ppm = getPixelsPerMeter();
    const live = new Map(
      getUnits()
        .filter(u => isUnitAllowedByChannel(u, channelFilter))
        .map(u => [u.id, u]),
    );
    const hits = new Map();
    const recordHit = (id, dist, role) => {
      const prev = hits.get(id);
      if (!prev || dist < prev.d) {
        hits.set(id, { id, d: dist, role });
      }
    };

    for (const p of getPickables()) {
      if (!live.has(p.id)) continue;
      const scr = projectWorld(p.center.x, p.center.y, p.center.z);
      if (!scr.visible) continue;
      const hitRadiusPx = Math.max(15, Math.min(46, p.radius * ppm + 7));
      const d = Math.hypot(scr.x - localX, scr.y - localY);
      if (d <= hitRadiusPx) {
        recordHit(p.id, d, p.role);
      }
    }

    const { tier, nodes } = buildIconNodes(channelFilter);
    if (tier !== 'none') {
      const { w, h } = getZoomTierIconSize(tier);
      const hw = w * 0.5 + 4;
      const hh = h * 0.5 + 4;
      for (const node of nodes) {
        const dx = Math.abs(localX - node.screenPt.x);
        const dy = Math.abs(localY - node.screenPt.y);
        if (dx <= hw && dy <= hh) {
          const d = Math.hypot(dx, dy);
          for (const uid of node.unitIds) {
            const u = live.get(uid);
            if (u) recordHit(uid, d, u.role);
          }
        }
      }
    }

    return [...hits.values()].sort((a, b) => a.d - b.d).map(h => h.id);
  }

  function queryGroundHeight(wx, wz) {
    const h = support?.heightAt?.(wx, wz);
    return Number.isFinite(h) ? h : 0;
  }

  function queryGroundAt(localX, localY) {
    if (!camera || !raycaster || !ndcVec) return { status: 'unsupported', point: null };
    const { width, height } = getHostSize();
    ndcVec.set((localX / width) * 2 - 1, -(localY / height) * 2 + 1);
    raycaster.setFromCamera(ndcVec, camera);

    const meshes = terrainPool?.visibleMeshes?.() || [];
    if (meshes.length > 0) {
      const hits = raycaster.intersectObjects(meshes, false);
      if (hits.length > 0) {
        const pt = hits[0].point;
        const sup = support?.query?.(pt.x, pt.z);
        if (sup?.state === 'pending') {
          return { status: 'pending', point: { x: pt.x, y: pt.y, z: pt.z } };
        }
        return {
          status: 'ready',
          point: { x: pt.x, y: Number.isFinite(sup?.height) ? sup.height : pt.y, z: pt.z },
          walkable: !physics?.isPointBlocked?.(pt.x, pt.z, 5.5),
        };
      }
    }

    // 高度场迭代平面交点回退（兼顾粗层边界与高程未命中处）
    if (groundPlane && planeHit && support?.query) {
      let h = 0;
      for (let i = 0; i < 8; i += 1) {
        groundPlane.constant = -h;
        if (!raycaster.ray.intersectPlane(groundPlane, planeHit)) break;
        const sup = support.query(planeHit.x, planeHit.z);
        if (sup?.state === 'pending') {
          return { status: 'pending', point: { x: planeHit.x, y: h, z: planeHit.z } };
        }
        if (!sup || sup.state !== 'ready') {
          return { status: 'unsupported', point: { x: planeHit.x, y: h, z: planeHit.z } };
        }
        if (Math.abs(h - sup.height) < 0.05) {
          return {
            status: 'ready',
            point: { x: planeHit.x, y: sup.height, z: planeHit.z },
            walkable: !physics?.isPointBlocked?.(planeHit.x, planeHit.z, 5.5),
          };
        }
        h = sup.height;
      }
    }
    return { status: 'unsupported', point: null };
  }

  return Object.freeze({
    getUnits,
    getCombatMode: () => combatSystem?.combatMode || 'g01_vehicles',
    getLastCommandResult: () => combatSystem?.lastCommandResult || null,
    getFogSnapshot: (nowSec) => combatSystem?.getFogSnapshot?.(nowSec) || null,
    queryFogAt: (wx, wz, opts) => combatSystem?.queryFogAt?.(wx, wz, opts) || null,
    getUnitFocus,
    getUnitAnchor: getUnitFocus,
    getPickables,
    projectWorld,
    projectUnit,
    getCurrentSpan,
    pickClusterAt,
    pickUnits,
    queryGroundAt,
    queryGroundHeight,
    setSelection: (ids, primaryId) => combatSystem?.setSelection?.(ids, primaryId),
    setHoveredUnit: (id) => combatSystem?.setHoveredUnit?.(id),
    setChannelFilter: (filter) => combatSystem?.setChannelFilter?.(filter),
    planCommand: (ids, command, append = false) => combatSystem?.planCommand?.(ids, command, append) || [],
    dispatchCommand: (ids, command, append = false, plannedRows = null) => (
      combatSystem?.dispatchCommand?.(ids, command, append, plannedRows) || null
    ),
    cancelUnits: (ids) => combatSystem?.cancelUnits?.(ids) || [],
    syncHostSize,
    dispose,
  });
}
