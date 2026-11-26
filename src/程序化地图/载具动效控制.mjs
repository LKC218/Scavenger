/**
 * Scavenger 9km 战场载具动效控制模块（Stage 2）：
 * - 129 车通用刚体姿态动画（地面 4 点高程贴坡 + 加减速点头抬头；旋翼悬停呼吸 + 速度前倾/侧倾；固定翼/无人机角速度压坡度滚转）；
 * - 已有 37 车可动部件驱动（main_rotor + 防频闪模糊圆盘、tail_rotor、turret、barrel、gun、radar）；
 * - 采用外层 u.node（世界位置 + 水平航向 +Z）与内层 rigBodyGroup/modelAlignGroup（-Z -> +Z 校准 + 局部姿态）防万向锁分层。
 * - 无顶层 Three.js / DOM 依赖，支持在 Node ESM 纯规则测试中直接导入求值。
 */

import { wrapAngleRad } from '../交互系统/契约与仲裁.mjs';

const DEG2RAD = Math.PI / 180;

function clamp(v, min, max) {
  return v < min ? min : v > max ? max : v;
}

function expDampFactor(lambda, dt) {
  return 1 - Math.exp(-Math.max(0, lambda) * Math.max(0, dt));
}

function lerp(a, b, alpha) {
  return a + (b - a) * alpha;
}

function lerpAngle(current, target, alpha) {
  return wrapAngleRad(current + wrapAngleRad(target - current) * alpha);
}

/**
 * 纯数值 4 点高程差分贴坡求解器（供地面载具使用，可在 Node 单测）。
 * 约定外层容器局部 +Z 为行进前方，+X 为车身左侧（Right-Hand Y-up）。
 * - 上坡（前方高于后方）时车头上仰 -> rotation.x < 0；
 * - 左侧高于右侧时左侧抬起 -> rotation.z > 0。
 */
export function computeGroundTerrainTilt(support, x, z, yaw = 0, halfLen = 2.4, halfWid = 1.4) {
  const queryH = (sx, sz) => {
    const h = support?.heightAt?.(sx, sz);
    return Number.isFinite(h) ? h : null;
  };
  const hCenter = queryH(x, z) ?? 0;
  const fwdX = Math.sin(yaw);
  const fwdZ = Math.cos(yaw);
  const leftX = Math.cos(yaw);
  const leftZ = -Math.sin(yaw);

  const hF = queryH(x + fwdX * halfLen, z + fwdZ * halfLen) ?? hCenter;
  const hB = queryH(x - fwdX * halfLen, z - fwdZ * halfLen) ?? hCenter;
  const hL = queryH(x + leftX * halfWid, z + leftZ * halfWid) ?? hCenter;
  const hR = queryH(x - leftX * halfWid, z - leftZ * halfWid) ?? hCenter;

  const rawPitch = -Math.atan2(hF - hB, Math.max(0.5, 2 * halfLen));
  const rawRoll = Math.atan2(hL - hR, Math.max(0.5, 2 * halfWid));
  const maxPitch = 22 * DEG2RAD;
  const maxRoll = 18 * DEG2RAD;

  return {
    pitch: clamp(rawPitch, -maxPitch, maxPitch) || 0,
    roll: clamp(rawRoll, -maxRoll, maxRoll) || 0,
    hCenter,
    hF,
    hB,
    hL,
    hR,
  };
}

/**
 * 纯数值固定翼与无人机压坡度滚转求解器。
 * 当向右/左盘旋（yawRate != 0）时，按角速度压出 35°~46° 内侧坡度：
 * 左转 yawRate > 0 -> 左翼 (+X) 下沉 -> rotation.z < 0。
 */
export function computeAircraftBankRoll(yawRate, gain = 1.65, maxDeg = 46) {
  const maxRad = Math.max(1, Number(maxDeg) || 46) * DEG2RAD;
  const yr = Number.isFinite(yawRate) ? yawRate : 0;
  return clamp(-yr * gain, -maxRad, maxRad);
}

function extractSlotMetaMap(unitMeta) {
  const map = new Map();
  if (Array.isArray(unitMeta?.sl)) {
    for (const item of unitMeta.sl) {
      if (Array.isArray(item) && item[0]) {
        map.set(item[0], {
          pivot: Array.isArray(item[1]) ? item[1] : [0, 0, 0],
          axis: item[2] || 'y',
        });
      }
    }
  }
  if (Array.isArray(unitMeta?.slots)) {
    for (const s of unitMeta.slots) {
      if (s?.slot && !map.has(s.slot)) {
        map.set(s.slot, {
          pivot: Array.isArray(s.pivot) ? s.pivot : [0, 0, 0],
          axis: s.axis || 'y',
        });
      }
    }
  }
  return map;
}

function estimateRotorRadius(THREE, slotNode) {
  if (!THREE || !slotNode) return 5.4;
  try {
    const box = new THREE.Box3().setFromObject(slotNode);
    const rx = Math.max(Math.abs(box.min.x), Math.abs(box.max.x));
    const rz = Math.max(Math.abs(box.min.z), Math.abs(box.max.z));
    const r = Math.max(rx, rz);
    return clamp(Number.isFinite(r) ? r : 5.4, 2.2, 12.5);
  } catch {
    return 5.4;
  }
}

function createRotorBlurDisc(THREE, radiusMeters) {
  const geo = new THREE.RingGeometry(radiusMeters * 0.16, radiusMeters * 0.96, 36);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshBasicMaterial({
    color: 0x1e2422,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'rotor_blur_disc';
  mesh.position.y = 0.04;
  mesh.castShadow = false;
  mesh.receiveShadow = false;
  mesh.userData.ownsGeoMat = true;
  return mesh;
}

function buildPivotWrapper(THREE, slotNode, slotName, pivotArr) {
  const piv = new THREE.Group();
  piv.name = `pivot_${slotName}`;
  const px = Number(pivotArr?.[0]) || 0;
  const py = Number(pivotArr?.[1]) || 0;
  const pz = Number(pivotArr?.[2]) || 0;
  piv.position.set(px, py, pz);
  if (slotNode) {
    slotNode.position.set(
      slotNode.position.x - px,
      slotNode.position.y - py,
      slotNode.position.z - pz,
    );
    piv.add(slotNode);
  }
  return piv;
}

/**
 * 构建防万向锁两层容器及 `.scvveh` / 降级模型的精确枢轴部件树：
 * - `rigBodyGroup`：负责地形贴坡 pitch/roll、加减速俯仰、悬停呼吸与固定翼压坡度滚转；
 * - `modelAlignGroup`：负责将 Unity 导出 `.scvveh` 局部车头 `-Z` 旋转 `Math.PI` 对齐外层航向 `+Z`；
 * - 可动部件：`turretPivot` -> `barrelPivot`（炮盾零脱节联动）、`mainRotorPivot`（+ 防频闪模糊圆盘）、`tailRotorPivot`、`gunPivot`、`radarPivot`。
 */
export function buildVehicleRigHierarchy(parsedOrScene, unitMeta = null, { THREE = null, isScvveh = true } = {}) {
  const T = THREE || (typeof globalThis !== 'undefined' ? globalThis.THREE : null);
  if (!T) {
    throw new Error('buildVehicleRigHierarchy 需要传入 Three.js 实例 { THREE }');
  }

  const rigBodyGroup = new T.Group();
  rigBodyGroup.name = 'rigBodyGroup';
  rigBodyGroup.rotation.order = 'YXZ';

  const modelAlignGroup = new T.Group();
  modelAlignGroup.name = 'modelAlignGroup';
  modelAlignGroup.rotation.y = isScvveh ? Math.PI : 0;
  rigBodyGroup.add(modelAlignGroup);

  const sourceRoot = parsedOrScene?.scene ? parsedOrScene.scene.clone(true) : parsedOrScene;
  if (!sourceRoot) {
    return {
      rigBodyGroup,
      modelAlignGroup,
      hull: null,
      turretPivot: null,
      barrelPivot: null,
      mainRotorPivot: null,
      tailRotorPivot: null,
      tailAxis: 'x',
      gunPivot: null,
      radarPivot: null,
      rotorBlurDisc: null,
      tailBlurDisc: null,
      slotsPresent: [],
    };
  }

  sourceRoot.traverse(obj => {
    if (!obj.isMesh) return;
    if (obj.userData?.isStylizedOutline) {
      obj.castShadow = false;
      obj.receiveShadow = false;
      obj.renderOrder = -1;
      return;
    }
    obj.castShadow = true;
    obj.receiveShadow = true;
  });

  if (!isScvveh) {
    modelAlignGroup.add(sourceRoot);
    return {
      rigBodyGroup,
      modelAlignGroup,
      hull: sourceRoot,
      turretPivot: null,
      barrelPivot: null,
      mainRotorPivot: null,
      tailRotorPivot: null,
      tailAxis: 'x',
      gunPivot: null,
      radarPivot: null,
      rotorBlurDisc: null,
      tailBlurDisc: null,
      slotsPresent: ['hull'],
    };
  }

  const slotMetaMap = extractSlotMetaMap(unitMeta);
  const directChildren = [...sourceRoot.children];
  const slotNodes = new Map();
  const extraNodes = [];
  for (const child of directChildren) {
    if (['hull', 'turret', 'barrel', 'main_rotor', 'tail_rotor', 'gun', 'radar'].includes(child.name)) {
      slotNodes.set(child.name, child);
    } else {
      extraNodes.push(child);
    }
  }

  const slotsPresent = [];
  const hullNode = slotNodes.get('hull') || null;
  if (hullNode) {
    modelAlignGroup.add(hullNode);
    slotsPresent.push('hull');
  }
  for (const extra of extraNodes) {
    modelAlignGroup.add(extra);
  }

  let turretPivot = null;
  let turretMetaPivot = [0, 0, 0];
  const turretNode = slotNodes.get('turret');
  if (turretNode) {
    const info = slotMetaMap.get('turret');
    turretMetaPivot = info?.pivot || [turretNode.position.x, turretNode.position.y, turretNode.position.z];
    turretPivot = buildPivotWrapper(T, turretNode, 'turret', turretMetaPivot);
    modelAlignGroup.add(turretPivot);
    slotsPresent.push('turret');
  }

  let barrelPivot = null;
  const barrelNode = slotNodes.get('barrel');
  if (barrelNode) {
    const info = slotMetaMap.get('barrel');
    const bPiv = info?.pivot || [barrelNode.position.x, barrelNode.position.y, barrelNode.position.z];
    if (turretPivot) {
      // 炮管作为炮塔子节点挂载于相对支点，公转+俯仰绝不脱节
      barrelPivot = new T.Group();
      barrelPivot.name = 'pivot_barrel';
      barrelPivot.position.set(
        bPiv[0] - turretMetaPivot[0],
        bPiv[1] - turretMetaPivot[1],
        bPiv[2] - turretMetaPivot[2],
      );
      barrelNode.position.set(
        barrelNode.position.x - bPiv[0],
        barrelNode.position.y - bPiv[1],
        barrelNode.position.z - bPiv[2],
      );
      barrelPivot.add(barrelNode);
      turretPivot.add(barrelPivot);
    } else {
      barrelPivot = buildPivotWrapper(T, barrelNode, 'barrel', bPiv);
      modelAlignGroup.add(barrelPivot);
    }
    slotsPresent.push('barrel');
  }

  let mainRotorPivot = null;
  let rotorBlurDisc = null;
  const mainRotorNode = slotNodes.get('main_rotor');
  if (mainRotorNode) {
    const info = slotMetaMap.get('main_rotor');
    const mPiv = info?.pivot || [mainRotorNode.position.x, mainRotorNode.position.y, mainRotorNode.position.z];
    const rotorRadius = estimateRotorRadius(T, mainRotorNode);
    mainRotorPivot = buildPivotWrapper(T, mainRotorNode, 'main_rotor', mPiv);
    rotorBlurDisc = createRotorBlurDisc(T, rotorRadius);
    mainRotorPivot.add(rotorBlurDisc);
    modelAlignGroup.add(mainRotorPivot);
    slotsPresent.push('main_rotor');
  }

  let tailRotorPivot = null;
  let tailBlurDisc = null;
  let tailAxis = 'x';
  const tailRotorNode = slotNodes.get('tail_rotor');
  if (tailRotorNode) {
    const info = slotMetaMap.get('tail_rotor');
    tailAxis = info?.axis || 'x';
    const tPiv = info?.pivot || [tailRotorNode.position.x, tailRotorNode.position.y, tailRotorNode.position.z];
    const tailRadius = tailAxis === 'y' ? estimateRotorRadius(T, tailRotorNode) : 0;
    tailRotorPivot = buildPivotWrapper(T, tailRotorNode, 'tail_rotor', tPiv);
    if (tailAxis === 'y') {
      tailBlurDisc = createRotorBlurDisc(T, tailRadius);
      tailRotorPivot.add(tailBlurDisc);
    }
    modelAlignGroup.add(tailRotorPivot);
    slotsPresent.push('tail_rotor');
  }

  let gunPivot = null;
  const gunNode = slotNodes.get('gun');
  if (gunNode) {
    const info = slotMetaMap.get('gun');
    const gPiv = info?.pivot || [gunNode.position.x, gunNode.position.y, gunNode.position.z];
    gunPivot = buildPivotWrapper(T, gunNode, 'gun', gPiv);
    modelAlignGroup.add(gunPivot);
    slotsPresent.push('gun');
  }

  let radarPivot = null;
  const radarNode = slotNodes.get('radar');
  if (radarNode) {
    const info = slotMetaMap.get('radar');
    const rPiv = info?.pivot || [radarNode.position.x, radarNode.position.y, radarNode.position.z];
    radarPivot = buildPivotWrapper(T, radarNode, 'radar', rPiv);
    modelAlignGroup.add(radarPivot);
    slotsPresent.push('radar');
  }

  return {
    rigBodyGroup,
    modelAlignGroup,
    hull: hullNode,
    turretPivot,
    barrelPivot,
    mainRotorPivot,
    tailRotorPivot,
    tailAxis,
    gunPivot,
    radarPivot,
    rotorBlurDisc,
    tailBlurDisc,
    slotsPresent,
  };
}

function readUnitPose(u) {
  if (u?.node?.position) {
    return {
      x: Number(u.node.position.x) || 0,
      y: Number(u.node.position.y) || 0,
      z: Number(u.node.position.z) || 0,
      yaw: Number(u.node.rotation?.y) || 0,
    };
  }
  return {
    x: Number(u?.x) || 0,
    y: Number(u?.y) || 0,
    z: Number(u?.z) || 0,
    yaw: Number(u?.yaw) || 0,
  };
}

/**
 * 每帧驱动编队英雄载具刚体姿态与已有可动部件动画（严格隔离 100 混编 instanced 集群）。
 */
export function stepVehicleAnimations(units, { dt = 0.016, nowSec = 0, support = null } = {}) {
  const safeDt = clamp(Number(dt) || 0.016, 0.001, 0.1);
  const attAlpha = expDampFactor(8.0, safeDt);
  const velAlpha = expDampFactor(10.0, safeDt);
  const turretAlpha = expDampFactor(5.2, safeDt);

  for (let idx = 0; idx < (units || []).length; idx += 1) {
    const u = units[idx];
    if (!u || u.alive === false) continue;
    const pose = readUnitPose(u);
    const phase = Number.isFinite(u.orbitPhase) ? u.orbitPhase : idx * 1.15;

    if (!u._anim) {
      u._anim = {
        prevX: pose.x,
        prevY: pose.y,
        prevZ: pose.z,
        prevYaw: pose.yaw,
        speedMps: 0,
        prevSpeedMps: 0,
        yawRate: 0,
        pitch: 0,
        roll: 0,
        heaveY: 0,
        mainRotorAngle: idx * 0.7,
        tailRotorAngle: idx * 0.9,
        turretYaw: 0,
        barrelPitch: 0.02,
        gunYaw: 0,
        radarAngle: 0,
      };
    }

    const st = u._anim;
    const dx = pose.x - st.prevX;
    const dy = pose.y - st.prevY;
    const dz = pose.z - st.prevZ;
    const instSpeed = Math.hypot(dx, dz) / safeDt;
    const instVy = dy / safeDt;
    const rawYawRate = clamp(wrapAngleRad(pose.yaw - st.prevYaw) / safeDt, -4.0, 4.0);

    st.prevSpeedMps = st.speedMps;
    st.speedMps = lerp(st.speedMps, instSpeed, velAlpha);
    const accel = (st.speedMps - st.prevSpeedMps) / safeDt;
    st.yawRate = lerp(st.yawRate, rawYawRate, velAlpha);

    st.prevX = pose.x;
    st.prevY = pose.y;
    st.prevZ = pose.z;
    st.prevYaw = pose.yaw;

    const rig = u.rig || null;

    if (u.role === 'ground') {
      const tilt = computeGroundTerrainTilt(support, pose.x, pose.z, pose.yaw, 2.4, 1.4);
      // 正 rotation.x 为车头(+Z)向下点头；起步加速抬头取负，刹车点头取正
      const accelPitch = clamp(-accel * 0.0024, -2.5 * DEG2RAD, 3.0 * DEG2RAD);
      const turnRoll = clamp(-st.yawRate * st.speedMps * 0.0028, -4.0 * DEG2RAD, 4.0 * DEG2RAD);
      const targetPitch = clamp(tilt.pitch + accelPitch, -24 * DEG2RAD, 24 * DEG2RAD);
      const targetRoll = clamp(tilt.roll + turnRoll, -20 * DEG2RAD, 20 * DEG2RAD);

      st.pitch = lerp(st.pitch, targetPitch, attAlpha);
      st.roll = lerp(st.roll, targetRoll, attAlpha);
      st.heaveY = 0;

      // 炮塔、炮管与防空雷达动画
      const agPt = u.lastAttackGroundIntent?.point || null;
      const hasWp = Array.isArray(u.waypoints) && u.wpIndex < u.waypoints.length;
      const wp = hasWp ? u.waypoints[u.wpIndex] : null;
      let targetRelTurretYaw = 0;
      let targetBarrelPitch = 0.025;

      if (agPt && Number.isFinite(agPt.x) && Number.isFinite(agPt.z)) {
        const worldAimYaw = Math.atan2(agPt.x - pose.x, agPt.z - pose.z);
        targetRelTurretYaw = wrapAngleRad(worldAimYaw - pose.yaw);
        targetBarrelPitch = 0.14;
      } else if (u.iconKind === 'aa' && rig?.turretPivot) {
        // 含一体化雷达炮塔的防空车持续扇区/周向扫描
        targetRelTurretYaw = wrapAngleRad(st.turretYaw + 1.8 * safeDt);
        targetBarrelPitch = 0.22;
      } else if (wp && Math.hypot(wp.x - pose.x, wp.z - pose.z) > 2.5) {
        const wpYaw = Math.atan2(wp.x - pose.x, wp.z - pose.z);
        targetRelTurretYaw = wrapAngleRad(wpYaw - pose.yaw + Math.sin(nowSec * 0.7 + phase) * 0.10);
        targetBarrelPitch = 0.04;
      } else if (Number.isFinite(u.targetFacing)) {
        targetRelTurretYaw = wrapAngleRad(u.targetFacing - pose.yaw);
        targetBarrelPitch = 0.03;
      } else if (u.groundGuard) {
        targetRelTurretYaw = Math.sin(nowSec * 0.55 + phase) * 0.22;
        targetBarrelPitch = 0.03 + Math.sin(nowSec * 1.1 + phase) * 0.015;
      }

      if (u.iconKind === 'aa' && !agPt && rig?.turretPivot) {
        st.turretYaw = targetRelTurretYaw;
      } else {
        st.turretYaw = lerpAngle(st.turretYaw, targetRelTurretYaw, turretAlpha);
      }
      st.barrelPitch = lerp(st.barrelPitch, targetBarrelPitch, turretAlpha);
      st.radarAngle = wrapAngleRad(st.radarAngle + 1.8 * safeDt);

      if (rig?.turretPivot) rig.turretPivot.rotation.y = st.turretYaw;
      if (rig?.barrelPivot) rig.barrelPivot.rotation.x = st.barrelPitch;
      if (rig?.radarPivot) rig.radarPivot.rotation.y = st.radarAngle;
    } else if (u.role === 'helicopter') {
      const hoverHeave = Math.sin(nowSec * Math.PI * 2 * 0.9 + phase) * 0.28;
      const hoverPitch = Math.cos(nowSec * 0.65 + phase) * (1.2 * DEG2RAD);
      const hoverRoll = Math.sin(nowSec * 0.78 + phase) * (1.6 * DEG2RAD);
      const speedRatio = clamp(st.speedMps / Math.max(1, u.speed || 26), 0, 1.1);
      // 前飞时旋翼机低头前倾（正 rotation.x 倾斜至 0°~14°）
      const movePitch = speedRatio * (13.0 * DEG2RAD);
      const moveRoll = clamp(-st.yawRate * 0.55, -22 * DEG2RAD, 22 * DEG2RAD);

      st.heaveY = hoverHeave;
      st.pitch = lerp(st.pitch, hoverPitch + movePitch, attAlpha);
      st.roll = lerp(st.roll, hoverRoll + moveRoll, attAlpha);

      st.mainRotorAngle = (st.mainRotorAngle + 26.0 * safeDt) % (Math.PI * 2);
      st.tailRotorAngle = (st.tailRotorAngle + 48.0 * safeDt) % (Math.PI * 2);
      st.gunYaw = Math.sin(nowSec * 1.05 + phase) * 0.42;

      if (rig?.mainRotorPivot) rig.mainRotorPivot.rotation.y = st.mainRotorAngle;
      if (rig?.tailRotorPivot) {
        if (rig.tailAxis === 'y') rig.tailRotorPivot.rotation.y = -st.mainRotorAngle;
        else rig.tailRotorPivot.rotation.x = st.tailRotorAngle;
      }
      if (rig?.gunPivot) rig.gunPivot.rotation.y = st.gunYaw;
    } else {
      // 固定翼与无人机：若由固定翼通用底座托管姿态（大迎角俯冲/拉起/失控翻滚），直接透传底座计算值，不施加 ±8°/±46° 钳位
      const fa = u.flightAttitude;
      if (fa && fa.customControlled) {
        st.heaveY = Number.isFinite(fa.heaveY) ? fa.heaveY : 0;
        st.pitch = Number.isFinite(fa.pitch) ? fa.pitch : st.pitch;
        st.roll = Number.isFinite(fa.roll) ? fa.roll : st.roll;
        if (Number.isFinite(fa.speedMps)) st.speedMps = fa.speedMps;
        if (Number.isFinite(fa.yawRate)) st.yawRate = fa.yawRate;
      } else {
        // 默认固定翼与无人机：角速度压坡度滚转（35°~46°）与轻量爬升俯仰
        const targetBankRoll = computeAircraftBankRoll(st.yawRate, 1.65, 46);
        const targetPitch = clamp(-instVy * 0.03 - accel * 0.003, -8 * DEG2RAD, 8 * DEG2RAD);
        st.heaveY = 0;
        st.roll = lerp(st.roll, targetBankRoll, expDampFactor(6.5, safeDt));
        st.pitch = lerp(st.pitch, targetPitch, expDampFactor(6.5, safeDt));
      }
    }

    if (rig?.rigBodyGroup) {
      rig.rigBodyGroup.position.y = st.heaveY;
      rig.rigBodyGroup.rotation.x = st.pitch;
      rig.rigBodyGroup.rotation.z = st.roll;
    }

    u.animState = {
      pitch: Number(st.pitch.toFixed(4)),
      roll: Number(st.roll.toFixed(4)),
      bankRoll: Number(st.roll.toFixed(4)),
      heaveY: Number(st.heaveY.toFixed(4)),
      mainRotorAngle: Number(st.mainRotorAngle.toFixed(4)),
      tailRotorAngle: Number(st.tailRotorAngle.toFixed(4)),
      turretYaw: Number(st.turretYaw.toFixed(4)),
      barrelPitch: Number(st.barrelPitch.toFixed(4)),
      gunYaw: Number(st.gunYaw.toFixed(4)),
      radarAngle: Number(st.radarAngle.toFixed(4)),
      speedMps: Number(st.speedMps.toFixed(2)),
      yawRate: Number(st.yawRate.toFixed(4)),
    };
  }
}
