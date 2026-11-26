/**
 * Scavenger 操作交互系统 —— 正交战术镜头、鼠标地面锚点缩放、平滑飞镜与持续跟随控制器。
 */

import { DEFAULT_MAP_BOUNDS } from './契约与仲裁.mjs';

export const CAMERA_TILT_RAD = (48 * Math.PI) / 180;

export function shortestAngleDiff(fromAngle, toAngle) {
  let diff = (toAngle - fromAngle) % (Math.PI * 2);
  if (diff > Math.PI) diff -= Math.PI * 2;
  if (diff < -Math.PI) diff += Math.PI * 2;
  return diff;
}

export function computeCameraAxes(angleRad) {
  const c = Math.cos(angleRad);
  const s = Math.sin(angleRad);
  return {
    rightX: c,
    rightZ: -s,
    forwardX: -s,
    forwardZ: -c,
  };
}

/**
 * 将三维单位坐标 (x, y, z) 沿正交俯角视线方向 (CAMERA_TILT_RAD = 48°) 投射到地面中心焦点，
 * 使直升机 (22m) 与固定翼 (234m) 在任意视角旋转角 angleRad 下均能精确锚定于屏幕正中央。
 */
export function projectAirAnchorToGroundFocus(anchor, angleRad = 0, heightAtFn = null) {
  if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.z)) {
    return { x: 0, z: 0, offsetForward: 0 };
  }
  const queryH = typeof heightAtFn === 'function' ? heightAtFn : () => 0;
  const rawGy0 = queryH(anchor.x, anchor.z);
  const gy0 = Number.isFinite(rawGy0) ? rawGy0 : 0;
  const visualCenterBias = (anchor.role === 'helicopter' || anchor.role === 'fixed_wing') ? 1.8 : 0;
  const uy = (Number.isFinite(anchor.y) ? anchor.y : gy0) + visualCenterBias;
  const h0 = Math.max(0, uy - gy0);
  if (h0 <= 0.5) {
    return { x: anchor.x, z: anchor.z, offsetForward: 0 };
  }
  const tanTilt = Math.tan(CAMERA_TILT_RAD);
  const { forwardX, forwardZ } = computeCameraAxes(angleRad);
  const d0 = h0 / tanTilt;
  const fx = anchor.x + forwardX * d0;
  const fz = anchor.z + forwardZ * d0;
  const rawGy1 = queryH(fx, fz);
  const gy1 = Number.isFinite(rawGy1) ? rawGy1 : gy0;
  const h1 = Math.max(0, uy - gy1);
  const d1 = h1 / tanTilt;
  return {
    x: anchor.x + forwardX * d1,
    z: anchor.z + forwardZ * d1,
    offsetForward: d1,
  };
}

/**
 * 创建统一战术镜头控制器，接管 9km 大地图视口缩放、平移、旋转、飞镜与单车跟随。
 */
export function createCameraController({
  host,
  camera,
  sun,
  state,
  support,
  bounds = DEFAULT_MAP_BOUNDS,
  computeHorPlusCameraFrustum,
  transitionCtrl,
  transitionPipeline,
  buildingSystem,
  queryGroundAt = null,
  onStreamSync = null,
  onCameraUpdate = null,
  onFollowChange = null,
  onToast = null,
}) {
  const camTarget = {
    x: state.center.x,
    z: state.center.z,
    span: state.span,
    logSpan: Math.log(Math.max(65, state.span)),
    angle: state.angle,
  };

  const camStreamState = {
    lastX: state.center.x,
    lastZ: state.center.z,
    lastSpan: state.span,
    lastFlushMs: 0,
    dirty: false,
  };

  let isTransitionUpdating = false;
  let queuedTransitionArgs = null;
  let flyMode = false;
  let flyFocusFn = null;
  let dragKind = null; // null | 'pan' | 'rotate'
  let followUnitId = null;
  let followOffset = { x: 0, z: 0 };
  let getUnitAnchorFn = null;

  const isHostDomElement = Boolean(host && typeof Element === 'function' && host instanceof Element);
  let observedHostW = Math.max(1, Number(host?.clientWidth) || 1280);
  let observedHostH = Math.max(1, Number(host?.clientHeight) || 720);
  let hostResizeObserver = null;

  function syncHostSize() {
    if (!host) return;
    const cw = Number(host.clientWidth);
    const ch = Number(host.clientHeight);
    if (cw > 0) observedHostW = cw;
    if (ch > 0) observedHostH = ch;
  }

  if (isHostDomElement && typeof ResizeObserver === 'function') {
    hostResizeObserver = new ResizeObserver(entries => {
      const entry = entries[entries.length - 1];
      const cr = entry?.contentRect;
      if (cr && cr.width > 0 && cr.height > 0) {
        observedHostW = Math.max(1, cr.width);
        observedHostH = Math.max(1, cr.height);
      }
    });
    hostResizeObserver.observe(host);
  }
  if (isHostDomElement && typeof window !== 'undefined') {
    window.addEventListener('resize', syncHostSize);
  }

  function getHostDimensions() {
    if (!isHostDomElement) {
      return {
        w: Math.max(1, Number(host?.clientWidth) || 1280),
        h: Math.max(1, Number(host?.clientHeight) || 720),
      };
    }
    return { w: observedHostW, h: observedHostH };
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

  function clampCoords(x, z, span) {
    const clampedSpan = Math.max(65, Math.min(9000, Number.isFinite(span) ? span : 135));
    const aspect = state.forcedAspect || (16 / 9);
    const { halfW, halfH } = computeHorPlusCameraFrustum(clampedSpan, aspect);
    const padX = clampedSpan < 6500 ? Math.min(3600, Math.max(80, halfW * 0.72)) : 4000;
    const padZ = clampedSpan < 6500 ? Math.min(3600, Math.max(80, halfH * 0.72)) : 4000;
    const minCX = (bounds.minX ?? -4425) + padX;
    const maxCX = (bounds.maxX ?? 4575) - padX;
    const minCZ = (bounds.minZ ?? -4150) + padZ;
    const maxCZ = (bounds.maxZ ?? 4850) - padZ;
    return {
      x: minCX <= maxCX ? Math.max(minCX, Math.min(maxCX, x)) : 75,
      z: minCZ <= maxCZ ? Math.max(minCZ, Math.min(maxCZ, z)) : 350,
      span: clampedSpan,
    };
  }

  function updateCameraAndSun() {
    let aspect = state.forcedAspect;
    if (!aspect) {
      const { w, h } = getHostDimensions();
      aspect = w / h;
    }
    const { halfW, halfH } = computeHorPlusCameraFrustum(state.span, aspect);
    camera.left = -halfW;
    camera.right = halfW;
    camera.top = halfH;
    camera.bottom = -halfH;
    camera.updateProjectionMatrix();

    const dist = Math.max(380, state.span * 1.6);
    const horiz = Math.cos(CAMERA_TILT_RAD) * dist;
    const vert = Math.sin(CAMERA_TILT_RAD) * dist;
    camera.position.set(
      state.center.x + Math.sin(state.angle) * horiz,
      state.center.y + vert,
      state.center.z + Math.cos(state.angle) * horiz,
    );
    camera.lookAt(state.center);
    camera.updateMatrixWorld();

    if (sun) {
      const shadowHalf = Math.max(95, Math.min(360, state.span * 0.95));
      sun.target.position.copy(state.center);
      sun.position.set(
        state.center.x - shadowHalf * 0.85,
        state.center.y + shadowHalf * 1.45,
        state.center.z + shadowHalf * 0.55,
      );
      sun.shadow.camera.left = -shadowHalf;
      sun.shadow.camera.right = shadowHalf;
      sun.shadow.camera.top = shadowHalf;
      sun.shadow.camera.bottom = -shadowHalf;
      sun.shadow.camera.near = 1.0;
      sun.shadow.camera.far = shadowHalf * 3.5;
      sun.shadow.camera.updateProjectionMatrix();
    }
    onCameraUpdate?.();
  }

  async function flushTransitionUpdate() {
    if (isTransitionUpdating || !transitionCtrl) return;
    isTransitionUpdating = true;
    try {
      while (queuedTransitionArgs) {
        const { cx, cy, cz, sp } = queuedTransitionArgs;
        queuedTransitionArgs = null;
        await transitionCtrl.update({ x: cx, y: cy, z: cz }, sp);
      }
    } finally {
      isTransitionUpdating = false;
    }
  }

  function syncTargetToCurrent() {
    camTarget.x = state.center.x;
    camTarget.z = state.center.z;
    camTarget.span = state.span;
    camTarget.logSpan = Math.log(Math.max(65, state.span));
    camTarget.angle = state.angle;
    flyMode = false;
    flyFocusFn = null;
  }

  function stopFollowing({ notify = true } = {}) {
    flyFocusFn = null;
    if (!followUnitId) return false;
    followUnitId = null;
    followOffset = { x: 0, z: 0 };
    if (notify) onFollowChange?.(null);
    return true;
  }

  function projectAnchorToGround(anchor, angleRad = camTarget.angle) {
    return projectAirAnchorToGroundFocus(anchor, angleRad, (x, z) => support?.heightAt?.(x, z));
  }

  function resetFollowOffset({ snap = false } = {}) {
    followOffset = { x: 0, z: 0 };
    if (!followUnitId || !getUnitAnchorFn) return false;
    const anchor = getUnitAnchorFn(followUnitId);
    if (!anchor) return false;
    const proj = projectAnchorToGround(anchor, camTarget.angle);
    const clamped = clampCoords(proj.x, proj.z, camTarget.span);
    camTarget.x = clamped.x;
    camTarget.z = clamped.z;
    if (snap) {
      state.center.x = clamped.x;
      state.center.z = clamped.z;
      const gy = support?.heightAt?.(clamped.x, clamped.z);
      state.center.y = Number.isFinite(gy) ? gy : 0;
      updateCameraAndSun();
    } else {
      flyMode = true;
    }
    camStreamState.dirty = true;
    return true;
  }

  function setFollowUnit(unitId, { anchorFn = null, notify = true } = {}) {
    if (anchorFn) getUnitAnchorFn = anchorFn;
    if (!unitId) {
      stopFollowing({ notify });
      return null;
    }
    followUnitId = unitId;
    followOffset = { x: 0, z: 0 };
    flyMode = true;
    const anchor = getUnitAnchorFn?.(unitId);
    if (anchor) {
      const proj = projectAnchorToGround(anchor, camTarget.angle);
      const clamped = clampCoords(proj.x, proj.z, camTarget.span);
      camTarget.x = clamped.x;
      camTarget.z = clamped.z;
      camStreamState.dirty = true;
    }
    if (notify) onFollowChange?.(followUnitId);
    return followUnitId;
  }

  function flyTo(x, z, span = camTarget.span, angle = camTarget.angle, { keepFollow = false, focusFn = null } = {}) {
    if (!keepFollow) stopFollowing({ notify: true });
    const clamped = clampCoords(x, z, span);
    camTarget.x = clamped.x;
    camTarget.z = clamped.z;
    camTarget.span = clamped.span;
    camTarget.logSpan = Math.log(clamped.span);
    camTarget.angle = angle;
    flyMode = true;
    flyFocusFn = typeof focusFn === 'function' ? focusFn : null;
    camStreamState.dirty = true;
  }

  async function setView({ focus, span, viewWidth, yaw, immediate = true } = {}) {
    if (focus !== undefined && (!focus || !Number.isFinite(focus.x) || !Number.isFinite(focus.z))) {
      throw new TypeError('setView focus 需包含有限 x/z 坐标');
    }
    const targetSpan = span ?? viewWidth ?? state.span;
    if (!Number.isFinite(targetSpan)) throw new TypeError('setView span 需为有限数值');
    if (yaw !== undefined && !Number.isFinite(yaw)) throw new TypeError('setView yaw 需为有限弧度');

    stopFollowing({ notify: true });
    dragKind = null;

    const targetX = focus ? focus.x : state.center.x;
    const targetZ = focus ? focus.z : state.center.z;
    const targetAngle = yaw !== undefined ? Math.atan2(Math.sin(yaw), Math.cos(yaw)) : state.angle;

    if (!immediate) {
      flyTo(targetX, targetZ, targetSpan, targetAngle);
      return getViewState();
    }

    const clamped = clampCoords(targetX, targetZ, targetSpan);
    const posOrSpanChanged =
      Math.hypot(clamped.x - camStreamState.lastX, clamped.z - camStreamState.lastZ) > 0.05 ||
      Math.abs(clamped.span - camStreamState.lastSpan) > 0.05;

    state.center.x = clamped.x;
    state.center.z = clamped.z;
    const gy = support?.heightAt?.(state.center.x, state.center.z);
    state.center.y = Number.isFinite(gy) ? gy : (focus?.y ?? 0);
    state.span = clamped.span;
    state.angle = targetAngle;

    camTarget.x = clamped.x;
    camTarget.z = clamped.z;
    camTarget.span = clamped.span;
    camTarget.logSpan = Math.log(clamped.span);
    camTarget.angle = targetAngle;
    flyMode = false;

    updateCameraAndSun();
    transitionPipeline?.syncFrame?.(state.center, state.span, 0, {
      immediate: true,
      buildingReady: buildingSystem?.isNearReady ?? false,
    });
    if (posOrSpanChanged || !camStreamState.lastFlushMs) {
      camStreamState.lastX = state.center.x;
      camStreamState.lastZ = state.center.z;
      camStreamState.lastSpan = state.span;
      camStreamState.lastFlushMs = performance.now();
      camStreamState.dirty = false;
      queuedTransitionArgs = { cx: state.center.x, cy: state.center.y, cz: state.center.z, sp: state.span };
      await flushTransitionUpdate();
      transitionPipeline?.syncFrame?.(state.center, state.span, 0, {
        immediate: true,
        buildingReady: buildingSystem?.isNearReady ?? false,
      });
    }
    onStreamSync?.();
    return getViewState();
  }

  function panByPixels(deltaPxX, deltaPxY) {
    // 持续跟随期间拖拽平移不退出跟随，改为累加相对偏移 followOffset
    if (flyMode && !followUnitId) syncTargetToCurrent();
    dragKind = 'pan';
    const { w, h } = getHostDimensions();
    const aspect = state.forcedAspect || (w / h);
    const { halfW, halfH } = computeHorPlusCameraFrustum(camTarget.span, aspect);
    const { rightX, rightZ, forwardX, forwardZ } = computeCameraAxes(state.angle);
    const worldDX = -(deltaPxX / w) * (halfW * 2.0);
    const worldDFwd = (deltaPxY / h) * ((halfH * 2.0) / Math.sin(CAMERA_TILT_RAD));
    const dWX = worldDX * rightX + worldDFwd * forwardX;
    const dWZ = worldDX * rightZ + worldDFwd * forwardZ;

    if (followUnitId) {
      const anchor = getUnitAnchorFn?.(followUnitId);
      if (anchor) {
        const proj = projectAnchorToGround(anchor, camTarget.angle);
        const clamped = clampCoords(
          proj.x + followOffset.x + dWX,
          proj.z + followOffset.z + dWZ,
          camTarget.span,
        );
        followOffset.x = clamped.x - proj.x;
        followOffset.z = clamped.z - proj.z;
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
      } else {
        followOffset.x += dWX;
        followOffset.z += dWZ;
        const clamped = clampCoords(camTarget.x + dWX, camTarget.z + dWZ, camTarget.span);
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
      }
      flyMode = false;
      camStreamState.dirty = true;
      return;
    }

    const clamped = clampCoords(
      camTarget.x + dWX,
      camTarget.z + dWZ,
      camTarget.span,
    );
    camTarget.x = clamped.x;
    camTarget.z = clamped.z;
    camStreamState.dirty = true;
  }

  function rotateByPixels(deltaPxX) {
    // 旋转保留持续跟随状态，不解除单车锁定
    dragKind = 'rotate';
    camTarget.angle -= deltaPxX * 0.0055;
  }

  function endPointerDrag() {
    dragKind = null;
  }

  function zoomAtPointer(deltaY, deltaMode, clientX, clientY) {
    flyMode = false;
    const rect = host.getBoundingClientRect();
    const w = Math.max(1, rect.width);
    const h = Math.max(1, rect.height);
    const ndcX = Math.max(-1.0, Math.min(1.0, ((clientX - rect.left) / w) * 2.0 - 1.0));
    const ndcY = Math.max(-1.0, Math.min(1.0, 1.0 - ((clientY - rect.top) / h) * 2.0));

    const rawDelta = deltaMode === 1 ? deltaY * 28 : deltaMode === 2 ? deltaY * 180 : deltaY;
    const delta = Math.max(-180, Math.min(180, rawDelta));
    const zoomScale = Math.pow(1.00125, delta);

    const oldSpan = camTarget.span;
    const newSpan = Math.max(65, Math.min(9000, oldSpan * zoomScale));
    if (Math.abs(newSpan - oldSpan) < 1e-4) return;

    let cursorOffX = null;
    let cursorOffZ = null;
    if (queryGroundAt) {
      const hit = queryGroundAt(clientX - rect.left, clientY - rect.top);
      if (hit?.status === 'ready' && hit.point) {
        cursorOffX = hit.point.x - state.center.x;
        cursorOffZ = hit.point.z - state.center.z;
      }
    }

    if (cursorOffX === null || cursorOffZ === null) {
      const aspect = state.forcedAspect || (w / h);
      const { halfW, halfH } = computeHorPlusCameraFrustum(oldSpan, aspect);
      const { rightX, rightZ, forwardX, forwardZ } = computeCameraAxes(camTarget.angle);
      const groundYOffset = (ndcY * halfH) / Math.sin(CAMERA_TILT_RAD);
      cursorOffX = rightX * (ndcX * halfW) + forwardX * groundYOffset;
      cursorOffZ = rightZ * (ndcX * halfW) + forwardZ * groundYOffset;
    }

    const ratio = newSpan / oldSpan;
    let nextX = camTarget.x + (1.0 - ratio) * cursorOffX;
    let nextZ = camTarget.z + (1.0 - ratio) * cursorOffZ;
    if (newSpan >= 6500) {
      const pull = Math.min(1.0, (newSpan - 6500) / 2500);
      nextX = nextX + (75 - nextX) * pull * 0.45;
      nextZ = nextZ + (350 - nextZ) * pull * 0.45;
    }

    const clamped = clampCoords(nextX, nextZ, newSpan);
    if (followUnitId) {
      followOffset.x += clamped.x - camTarget.x;
      followOffset.z += clamped.z - camTarget.z;
    }
    camTarget.x = clamped.x;
    camTarget.z = clamped.z;
    camTarget.span = clamped.span;
    camTarget.logSpan = Math.log(clamped.span);
    camStreamState.dirty = true;
  }

  function step(dtMs, nowMs, pressedKeys, edgePan = { x: 0, z: 0 }) {
    if (state.paused) return;
    const dtSec = Math.min(0.05, Math.max(0.0005, dtMs * 0.001));

    let moveRight = edgePan.x || 0;
    let moveFwd = edgePan.z || 0;
    let rotInput = 0;

    if (pressedKeys?.has('d') || pressedKeys?.has('arrowright')) moveRight += 1;
    if (pressedKeys?.has('a') || pressedKeys?.has('arrowleft')) moveRight -= 1;
    if (pressedKeys?.has('w') || pressedKeys?.has('arrowup')) moveFwd += 1;
    if (pressedKeys?.has('s') || pressedKeys?.has('arrowdown')) moveFwd -= 1;
    if (pressedKeys?.has('q')) rotInput -= 1;
    if (pressedKeys?.has('e')) rotInput += 1;

    if (moveRight !== 0 || moveFwd !== 0) {
      // 持续跟随期间键盘/边缘平移不退出跟随，改为累加相对偏移 followOffset
      const len = Math.hypot(moveRight, moveFwd);
      const nr = moveRight / len;
      const nf = moveFwd / len;
      const { rightX, rightZ, forwardX, forwardZ } = computeCameraAxes(camTarget.angle);
      const speed = Math.max(60.0, camTarget.span * 0.95) * dtSec;
      const dWX = (nr * rightX + nf * forwardX) * speed;
      const dWZ = (nr * rightZ + nf * forwardZ) * speed;

      if (followUnitId) {
        flyMode = false;
        const anchor = getUnitAnchorFn?.(followUnitId);
        if (anchor) {
          const proj = projectAnchorToGround(anchor, camTarget.angle);
          const clamped = clampCoords(
            proj.x + followOffset.x + dWX,
            proj.z + followOffset.z + dWZ,
            camTarget.span,
          );
          followOffset.x = clamped.x - proj.x;
          followOffset.z = clamped.z - proj.z;
          camTarget.x = clamped.x;
          camTarget.z = clamped.z;
        } else {
          followOffset.x += dWX;
          followOffset.z += dWZ;
          const clamped = clampCoords(camTarget.x + dWX, camTarget.z + dWZ, camTarget.span);
          camTarget.x = clamped.x;
          camTarget.z = clamped.z;
        }
        camStreamState.dirty = true;
      } else {
        if (flyMode) syncTargetToCurrent();
        const clamped = clampCoords(
          camTarget.x + dWX,
          camTarget.z + dWZ,
          camTarget.span,
        );
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
        camStreamState.dirty = true;
      }
    }

    if (rotInput !== 0) {
      camTarget.angle += rotInput * 1.55 * dtSec;
    }

    // 持续跟随：每帧按当前 camTarget.angle 实时补偿空中三维视线地面落点，保证旋转视角时 3D 机身始终钉在屏幕中心
    if (followUnitId && getUnitAnchorFn) {
      const anchor = getUnitAnchorFn(followUnitId);
      if (!anchor) {
        stopFollowing({ notify: true });
      } else {
        const proj = projectAnchorToGround(anchor, camTarget.angle);
        const clamped = clampCoords(proj.x + followOffset.x, proj.z + followOffset.z, camTarget.span);
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
        camStreamState.dirty = true;
      }
    } else if (flyMode && typeof flyFocusFn === 'function') {
      const dynamicPt = flyFocusFn(camTarget.angle);
      if (dynamicPt && Number.isFinite(dynamicPt.x) && Number.isFinite(dynamicPt.z)) {
        const clamped = clampCoords(dynamicPt.x, dynamicPt.z, camTarget.span);
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
        camStreamState.dirty = true;
      }
    }

    const posLambda = dragKind === 'pan' ? 28.0 : (followUnitId ? 15.0 : flyMode ? 11.0 : 16.5);
    const posAlpha = 1.0 - Math.exp(-posLambda * dtSec);
    const zoomAlpha = 1.0 - Math.exp(-(flyMode ? 11.0 : 14.0) * dtSec);
    const rotAlpha = 1.0 - Math.exp(-(dragKind === 'rotate' ? 26.0 : (flyMode ? 11.0 : 16.5)) * dtSec);

    const dx = camTarget.x - state.center.x;
    const dz = camTarget.z - state.center.z;
    const curLogSpan = Math.log(Math.max(65, state.span));
    const dLog = camTarget.logSpan - curLogSpan;
    const dAng = flyMode
      ? shortestAngleDiff(state.angle, camTarget.angle)
      : (camTarget.angle - state.angle);

    const hasMove = Math.abs(dx) > 1e-3 || Math.abs(dz) > 1e-3 || Math.abs(dLog) > 1e-5 || Math.abs(dAng) > 1e-5;
    if (hasMove) {
      if (Math.hypot(dx, dz) < 0.03) {
        state.center.x = camTarget.x;
        state.center.z = camTarget.z;
      } else {
        state.center.x += dx * posAlpha;
        state.center.z += dz * posAlpha;
      }
      const gy = support?.heightAt?.(state.center.x, state.center.z);
      state.center.y = Number.isFinite(gy) ? gy : 0;

      if (Math.abs(dLog) < 2e-4) {
        state.span = camTarget.span;
      } else {
        state.span = Math.exp(curLogSpan + dLog * zoomAlpha);
      }

      if (Math.abs(dAng) < 2e-4) {
        state.angle = camTarget.angle;
      } else {
        state.angle += dAng * rotAlpha;
      }

      if (
        !followUnitId &&
        Math.hypot(camTarget.x - state.center.x, camTarget.z - state.center.z) < 0.25 &&
        Math.abs(camTarget.logSpan - Math.log(state.span)) < 1e-3
      ) {
        flyMode = false;
        flyFocusFn = null;
      }
      updateCameraAndSun();
    }

    const streamMoved = Math.hypot(state.center.x - camStreamState.lastX, state.center.z - camStreamState.lastZ);
    const streamSpanRel = Math.abs(state.span - camStreamState.lastSpan) / Math.max(65, camStreamState.lastSpan);
    const isIdleConverged =
      camStreamState.dirty &&
      Math.hypot(camTarget.x - state.center.x, camTarget.z - state.center.z) < 0.15 &&
      Math.abs(camTarget.logSpan - Math.log(state.span)) < 1e-3;

    if ((streamMoved > 0.6 || streamSpanRel > 0.008 || isIdleConverged) && (nowMs - camStreamState.lastFlushMs >= 45 || isIdleConverged)) {
      camStreamState.lastX = state.center.x;
      camStreamState.lastZ = state.center.z;
      camStreamState.lastSpan = state.span;
      camStreamState.lastFlushMs = nowMs;
      if (isIdleConverged) camStreamState.dirty = false;
      queuedTransitionArgs = { cx: state.center.x, cy: state.center.y, cz: state.center.z, sp: state.span };
      flushTransitionUpdate();
      onStreamSync?.();
    }
  }

  function bindMinimapDrag(minimapBox, boundsRef, opts = {}) {
    if (!minimapBox) return () => {};
    let activePointerId = null;
    const screenToWorldFn = typeof opts?.screenToWorld === 'function' ? opts.screenToWorld : null;
    const onRightClickWorld = typeof opts?.onRightClickWorld === 'function' ? opts.onRightClickWorld : null;

    const eventToWorld = (e) => {
      if (screenToWorldFn) {
        const pt = screenToWorldFn(e, minimapBox);
        if (pt && Number.isFinite(pt.x) && Number.isFinite(pt.z)) return pt;
      }
      const r = minimapBox.getBoundingClientRect();
      const b = boundsRef || bounds || DEFAULT_MAP_BOUNDS;
      const nx = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)));
      const ny = Math.max(0, Math.min(1, (e.clientY - r.top) / Math.max(1, r.height)));
      return {
        x: b.minX + nx * (b.maxX - b.minX),
        z: b.minZ + ny * (b.maxZ - b.minZ),
      };
    };

    const jumpFromEvent = (e, immediate = true) => {
      const { x: wx, z: wz } = eventToWorld(e);
      const clamped = clampCoords(wx, wz, camTarget.span);

      if (followUnitId) {
        // 持续跟随期间点击/拖拽小地图不退跟随，改为更新相对偏移 followOffset
        const anchor = getUnitAnchorFn?.(followUnitId);
        if (anchor) {
          const proj = projectAnchorToGround(anchor, camTarget.angle);
          followOffset.x = clamped.x - proj.x;
          followOffset.z = clamped.z - proj.z;
        } else {
          followOffset.x += clamped.x - camTarget.x;
          followOffset.z += clamped.z - camTarget.z;
        }
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
        state.center.x = clamped.x;
        state.center.z = clamped.z;
        const gy = support?.heightAt?.(clamped.x, clamped.z);
        state.center.y = Number.isFinite(gy) ? gy : 0;
        flyMode = false;
        camStreamState.dirty = true;
        updateCameraAndSun();
        return { x: clamped.x, z: clamped.z };
      }

      if (immediate) {
        camTarget.x = clamped.x;
        camTarget.z = clamped.z;
        state.center.x = clamped.x;
        state.center.z = clamped.z;
        const gy = support?.heightAt?.(clamped.x, clamped.z);
        state.center.y = Number.isFinite(gy) ? gy : 0;
        flyMode = false;
        camStreamState.dirty = true;
        updateCameraAndSun();
      } else {
        flyTo(wx, wz, camTarget.span, camTarget.angle);
      }
      return { x: wx, z: wz };
    };

    const onContextMenu = (e) => {
      e.preventDefault();
      e.stopPropagation();
    };

    const onDown = (e) => {
      if (e.target?.closest?.('button')) return;
      if (e.button === 2 && onRightClickWorld) {
        e.preventDefault();
        e.stopPropagation();
        const pt = eventToWorld(e);
        onRightClickWorld(pt, e);
        return;
      }
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      activePointerId = e.pointerId;
      minimapBox.setPointerCapture?.(e.pointerId);
      const pt = jumpFromEvent(e, true);
      onToast?.(
        followUnitId ? '雷达偏移侦察（跟随中）' : '雷达视角跳转',
        followUnitId
          ? `偏移定位 (${pt.x.toFixed(0)}, ${pt.z.toFixed(0)}) · 按 F 归中`
          : `定位至 (${pt.x.toFixed(0)}, ${pt.z.toFixed(0)})`,
      );
    };

    const onMove = (e) => {
      if (activePointerId !== e.pointerId) return;
      jumpFromEvent(e, true);
    };

    const onUp = (e) => {
      if (activePointerId !== e.pointerId) return;
      activePointerId = null;
      if (minimapBox.hasPointerCapture?.(e.pointerId)) {
        minimapBox.releasePointerCapture(e.pointerId);
      }
    };

    minimapBox.addEventListener('contextmenu', onContextMenu);
    minimapBox.addEventListener('pointerdown', onDown);
    minimapBox.addEventListener('pointermove', onMove);
    minimapBox.addEventListener('pointerup', onUp);
    minimapBox.addEventListener('pointercancel', onUp);

    return () => {
      minimapBox.removeEventListener('contextmenu', onContextMenu);
      minimapBox.removeEventListener('pointerdown', onDown);
      minimapBox.removeEventListener('pointermove', onMove);
      minimapBox.removeEventListener('pointerup', onUp);
      minimapBox.removeEventListener('pointercancel', onUp);
    };
  }

  function getViewState() {
    return {
      focus: { x: state.center.x, y: state.center.y, z: state.center.z },
      span: state.span,
      yaw: state.angle,
      followUnitId,
      followOffset: { x: followOffset.x, z: followOffset.z },
      target: { ...camTarget },
    };
  }

  return Object.freeze({
    camTarget,
    clampCoords,
    projectAnchorToGround,
    updateCameraAndSun,
    syncTargetToCurrent,
    flyTo,
    setView,
    panByPixels,
    rotateByPixels,
    endPointerDrag,
    zoomAtPointer,
    step,
    setFollowUnit,
    resetFollowOffset,
    getFollowOffset: () => ({ x: followOffset.x, z: followOffset.z }),
    stopFollowing,
    getFollowUnitId: () => followUnitId,
    setUnitAnchorProvider: (fn) => { getUnitAnchorFn = fn; },
    bindMinimapDrag,
    syncHostSize,
    getViewState,
    dispose,
  });
}
