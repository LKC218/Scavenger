/**
 * Scavenger 操作交互系统 —— 原生高清 2D 战术标绘层、右键预览渲染、射程/攻地投影与 V2.2 8 槽战术轮盘。
 */

import {
  VEHICLE_META,
  readSemanticPalette,
  resolveRouteStyle,
  buildClippedRoute,
  chamferWaypoints,
  drawTacticalPolyline,
  drawRouteChevrons,
  drawWireframeGhost,
  drawAirLockBrackets,
  drawEtaBadge,
  resolveZoomIconTier,
  clusterScreenUnits,
  warmSilhouettePathCache,
  drawZoomTierUnitIcon,
  drawGroundOrbitZone,
  drawAirTurnArc,
} from '../界面规范/图标与标绘.mjs';
import { INTERACTION_MODES } from './契约与仲裁.mjs';
import { CAMERA_TILT_RAD } from './镜头控制.mjs';
import { evaluateRangeState } from '../程序化地图/三维战术标绘.mjs';
import {
  TACTICAL_WHEEL_CONFIG,
  resolveWheelSectorFromVector,
  clampWheelVisualCenter,
  canCommitWheelSlot,
  renderTacticalWheel,
  renderTacticalWheelRay,
} from '../界面规范/交互控件.mjs';

const SIN_TILT = Math.sin(CAMERA_TILT_RAD);

/**
 * 判定固定翼单位当前是否已进入绕盘旋中心闭合绕圈态（直线切入或执行未完航点期间返回 false）。
 */
export function isFixedWingOrbiting(unit, pos = null) {
  if (!unit || unit.role !== 'fixed_wing') return false;
  const hasActiveWaypoints = Array.isArray(unit.waypoints) && unit.waypoints.length > (unit.wpIndex || 0);
  if (hasActiveWaypoints) return false;
  const p = pos || unit.node?.position || unit;
  if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.z)) return false;
  const oc = unit.orbitCenter;
  if (!oc || !Number.isFinite(oc.x) || !Number.isFinite(oc.z)) return false;
  const orbitR = Number(unit.orbitRadius) || 85;
  const dist = Math.hypot(p.x - oc.x, p.z - oc.z);
  return Math.abs(dist - orbitR) <= Math.max(14, orbitR * 0.25);
}

/**
 * 纯规则：计算固定翼拖线预览的实时预估耗弹与余弹不足告警文案。
 */
export function formatDragLineAmmoPreview(sp, dragLen) {
  const rawLen = Math.max(0, Number(dragLen) || 0);
  const minLen = Number(sp?.strafeLenMinM) || 0;
  const maxLen = Number(sp?.strafeLenMaxM) || 0;
  const effLen = (minLen > 0 && maxLen >= minLen) ? Math.max(minLen, Math.min(maxLen, rawLen)) : rawLen;
  const ammoCap = Math.max(0, Number(sp?.ammo) || 0);

  if (sp?.kind === 'autocannon' || sp?.actionCmd === 'strafe') {
    const roundsPerM = Number(sp?.roundsPerMeter) || 0.85;
    const est = Math.max(1, Math.round(effLen * roundsPerM));
    if (est > ammoCap) {
      return {
        text: `${Math.round(effLen)}m · 余弹仅 ${ammoCap}`,
        insufficient: true,
        estimatedRounds: est,
      };
    }
    return {
      text: `${Math.round(effLen)}m · ~${est}发`,
      insufficient: false,
      estimatedRounds: est,
    };
  }

  if (sp?.kind === 'rocket' || sp?.actionCmd === 'rocket') {
    const salvo = Math.max(1, Number(sp?.salvoCount) || 7);
    if (salvo > ammoCap) {
      return {
        text: `${Math.round(effLen)}m · 余弹仅 ${ammoCap}`,
        insufficient: true,
        estimatedRounds: salvo,
      };
    }
    return {
      text: `${Math.round(effLen)}m · ${salvo}发齐射`,
      insufficient: false,
      estimatedRounds: salvo,
    };
  }

  return {
    text: `${Math.round(rawLen)}m`,
    insufficient: false,
    estimatedRounds: 0,
  };
}

function toGhostKind(iconKind, role) {
  if (role === 'helicopter') return 'heli';
  if (role === 'fixed_wing') return 'plane';
  if (iconKind === 'supply') return 'supply';
  return 'tank';
}

function drawGroundRing(ctx, center, radiusM, role, style, isSelected, isHovered) {
  const ppm = center.ppm || 1;
  const rx = Math.max(8, radiusM * ppm);
  const ry = Math.max(5.5, rx * SIN_TILT);
  ctx.save();
  ctx.globalAlpha = isSelected ? 0.96 : isHovered ? 0.88 : 0.62;
  ctx.strokeStyle = style.outline;
  ctx.lineWidth = isSelected ? 4.2 : 3.2;
  ctx.beginPath();
  ctx.ellipse(center.x, center.y, rx, ry, 0, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = isSelected ? style.selectColor : style.main;
  ctx.lineWidth = isSelected ? 2.1 : 1.5;
  ctx.beginPath();
  ctx.ellipse(center.x, center.y, rx, ry, 0, 0, Math.PI * 2);
  ctx.stroke();

  if (isSelected || isHovered) {
    ctx.strokeStyle = isSelected ? style.selectInner : style.main;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.ellipse(center.x, center.y, rx * 0.72, ry * 0.72, 0, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

function drawWorldCircle(ctx, worldCenter, radiusM, projectFn, strokeColor, lineWidth, dash = [], fillColor = null) {
  const segs = 48;
  const pts = [];
  for (let i = 0; i <= segs; i += 1) {
    const a = (i / segs) * Math.PI * 2;
    const wx = worldCenter.x + Math.cos(a) * radiusM;
    const wz = worldCenter.z + Math.sin(a) * radiusM;
    pts.push(projectFn(wx, worldCenter.y ?? 0, wz));
  }
  ctx.save();
  ctx.beginPath();
  pts.forEach((p, idx) => (idx === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  if (fillColor) {
    ctx.fillStyle = fillColor;
    ctx.fill();
  }
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dash);
  ctx.stroke();
  ctx.restore();
}

function drawQueueTag(ctx, pt, text, style) {
  ctx.save();
  ctx.font = '800 10px "Cascadia Mono","Consolas",monospace';
  const w = ctx.measureText(text).width + 10;
  const x = pt.x - w * 0.5;
  const y = pt.y - 26;
  ctx.fillStyle = style.badgeBg;
  ctx.strokeStyle = style.main;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.roundRect(x, y, w, 17, 4);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = style.badgeText;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, pt.x, y + 8.8);
  ctx.restore();
}

function drawStrikeBadge(ctx, pt, text, borderColor, textColor, bgColor = 'rgba(13, 14, 19, 0.92)', offsetY = -28) {
  if (!pt || !text) return;
  ctx.save();
  ctx.font = '800 10.5px "Cascadia Mono","Consolas","Microsoft YaHei UI",monospace';
  const w = ctx.measureText(text).width + 14;
  const h = 20;
  const x = pt.x - w * 0.5;
  const y = pt.y + offsetY;
  ctx.fillStyle = bgColor;
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, 4);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = textColor;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, pt.x, y + h * 0.5 + 0.3);
  ctx.restore();
}

function drawReticleIndexCircle(ctx, scrPt, numIndex, accentColor = '#e85a52') {
  if (!scrPt || numIndex <= 0) return;
  ctx.save();
  ctx.font = '800 9.5px "Cascadia Mono","Consolas",monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = 'rgba(13, 14, 19, 0.90)';
  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  ctx.arc(scrPt.x, scrPt.y - 14, 7.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#ffffff';
  ctx.fillText(String(numIndex), scrPt.x, scrPt.y - 13.7);
  ctx.restore();
}

/**
 * 汇总固定翼当前执飞及排队中的全量打击标记点，保证地面标绘与 Shift 预览共享同一口径。
 */
function collectFixedWingStrikeNodes(pPath) {
  if (!pPath || !pPath.active || pPath.kind === 'orbit_transit') return [];
  const nodes = [];
  const pushBatch = (batch, isActivePass) => {
    if (!batch) return;
    const wId = batch.weaponId || '';
    const wKind = batch.weaponKind || batch.cmd || 'bomb';
    const inMode = batch.inputMode || 'drag_line';
    const innerR = batch.splashInnerM || 6;
    const outerR = batch.splashOuterM || 18;
    const baseEta = Number(batch.etaSec) || 0;

    if (inMode === 'multi_point' && Array.isArray(batch.dropPoints) && batch.dropPoints.length > 0) {
      batch.dropPoints.forEach((dp, idx) => {
        nodes.push({
          weaponId: wId,
          weaponKind: 'bomb',
          isBox: false,
          pt: dp,
          innerR,
          outerR,
          etaSec: Number((baseEta + idx * 0.25).toFixed(1)),
          showEta: idx === 0,
          isActivePass,
        });
      });
    } else if (Array.isArray(batch.missileTargets) && batch.missileTargets.length > 0) {
      batch.missileTargets.forEach((mt, idx) => {
        nodes.push({
          weaponId: wId,
          weaponKind: wKind === 'agm' ? 'agm' : 'missile',
          isBox: false,
          pt: mt,
          innerR,
          outerR,
          etaSec: Number((baseEta + idx * 0.4).toFixed(1)),
          showEta: true,
          isActivePass,
        });
      });
    } else if (inMode === 'drag_line' && batch.startPos && batch.endPos) {
      nodes.push({
        weaponId: wId,
        weaponKind: wKind,
        isBox: true,
        startPos: batch.startPos,
        endPos: batch.endPos,
        halfW: Math.max(5, (batch.strafeWidthM || (outerR * 2)) * 0.5),
        pt: batch.targetPos || batch.startPos,
        innerR,
        outerR,
        etaSec: baseEta,
        showEta: true,
        isActivePass,
      });
    } else if (batch.targetPos) {
      nodes.push({
        weaponId: wId,
        weaponKind: wKind,
        isBox: false,
        pt: batch.targetPos,
        innerR,
        outerR,
        etaSec: baseEta,
        showEta: true,
        isActivePass,
      });
    }
  };

  if (pPath.kind === 'strike') {
    pushBatch(pPath, true);
  }
  if (Array.isArray(pPath.queuedStrikes)) {
    for (const q of pPath.queuedStrikes) {
      pushBatch(q, false);
    }
  }
  return nodes;
}

function drawAirStrikeStrip(ctx, startPt, endPt, halfWidthM, groundHeightFn, projectFn, {
  fillColor = 'rgba(255, 82, 102, 0.24)',
  strokeColor = '#ff5266',
  arrowColor = '#ffffff',
  dash = [],
} = {}) {
  const dx = endPt.x - startPt.x;
  const dz = endPt.z - startPt.z;
  const len = Math.hypot(dx, dz);
  if (len < 2) return;
  const ux = dx / len;
  const uz = dz / len;
  const px = -uz;
  const pz = ux;
  const hw = Math.max(6, halfWidthM);

  const cornersW = [
    { x: startPt.x + px * hw, z: startPt.z + pz * hw },
    { x: endPt.x + px * hw, z: endPt.z + pz * hw },
    { x: endPt.x - px * hw, z: endPt.z - pz * hw },
    { x: startPt.x - px * hw, z: startPt.z - pz * hw },
  ];
  const cornersS = cornersW.map(c => projectFn(c.x, groundHeightFn(c.x, c.z) + 0.22, c.z));

  ctx.save();
  ctx.beginPath();
  cornersS.forEach((p, idx) => (idx === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();
  ctx.strokeStyle = 'rgba(9, 10, 15, 0.85)';
  ctx.lineWidth = 3.2;
  ctx.setLineDash(dash);
  ctx.stroke();
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 1.6;
  ctx.stroke();
  ctx.setLineDash([]);

  // 中心白色进场方向箭头
  const s0 = projectFn(startPt.x, groundHeightFn(startPt.x, startPt.z) + 0.26, startPt.z);
  const s1 = projectFn(endPt.x, groundHeightFn(endPt.x, endPt.z) + 0.26, endPt.z);
  ctx.strokeStyle = 'rgba(9, 10, 15, 0.88)';
  ctx.lineWidth = 4.6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(s0.x, s0.y);
  ctx.lineTo(s1.x, s1.y);
  ctx.stroke();

  ctx.strokeStyle = arrowColor;
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  ctx.moveTo(s0.x, s0.y);
  ctx.lineTo(s1.x, s1.y);
  ctx.stroke();

  const ang = Math.atan2(s1.y - s0.y, s1.x - s0.x);
  const headLen = 11;
  ctx.fillStyle = arrowColor;
  ctx.beginPath();
  ctx.moveTo(s1.x, s1.y);
  ctx.lineTo(s1.x - Math.cos(ang - 0.48) * headLen, s1.y - Math.sin(ang - 0.48) * headLen);
  ctx.lineTo(s1.x - Math.cos(ang + 0.48) * headLen, s1.y - Math.sin(ang + 0.48) * headLen);
  ctx.closePath();
  ctx.fill();

  // 起点实心点
  ctx.beginPath();
  ctx.arc(s0.x, s0.y, 4, 0, Math.PI * 2);
  ctx.fillStyle = strokeColor;
  ctx.fill();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.restore();
}

function drawWorldRangeRings(ctx, footPos, minRangeM, maxRangeM, rangeState, groundHeightFn, projectFn, pal) {
  const gy = groundHeightFn(footPos.x, footPos.z) + 0.16;
  const center = { x: footPos.x, y: gy, z: footPos.z };
  const maxR = Math.max(40, maxRangeM || 400);
  const minR = Math.max(0, minRangeM || 0);
  const isOut = rangeState === 'out_of_range';
  const isClose = rangeState === 'too_close';

  // 外围最大可攻击距离环：《断箭》极细高清白实环（超距温和琥珀，无粗黑描边）
  drawWorldCircle(
    ctx,
    center,
    maxR,
    projectFn,
    isOut ? 'rgba(240, 184, 76, 0.78)' : (isClose ? 'rgba(235, 242, 255, 0.32)' : 'rgba(235, 242, 252, 0.56)'),
    isOut ? 1.4 : 1.15,
    [],
    null,
  );

  // 内侧最小安全/俯冲盲区环
  if (minR >= 18) {
    drawWorldCircle(
      ctx,
      center,
      minR,
      projectFn,
      isClose ? 'rgba(255, 92, 66, 0.88)' : 'rgba(220, 74, 70, 0.48)',
      isClose ? 1.5 : 1.0,
      [6, 5],
      isClose ? 'rgba(255, 72, 42, 0.10)' : 'rgba(180, 32, 38, 0.035)',
    );
  }
}

function drawBoundaryDiamond(ctx, scrPt, fillColor = '#f6b83d') {
  if (!scrPt) return;
  ctx.save();
  ctx.translate(scrPt.x, scrPt.y);
  ctx.rotate(Math.PI * 0.25);
  ctx.fillStyle = fillColor;
  ctx.strokeStyle = 'rgba(9, 10, 15, 0.92)';
  ctx.lineWidth = 1.6;
  ctx.fillRect(-4.5, -4.5, 9, 9);
  ctx.strokeRect(-4.5, -4.5, 9, 9);
  ctx.restore();
}

function drawTooCloseBypassArrow(ctx, footPos, targetPos, minRangeM, groundHeightFn, projectFn, color = '#ff6538') {
  const dx = targetPos.x - footPos.x;
  const dz = targetPos.z - footPos.z;
  const d = Math.hypot(dx, dz) || 1;
  const dirX = dx / d;
  const dirZ = dz / d;
  const perpX = -dirZ;
  const perpZ = dirX;
  const pullOut = Math.max(70, (minRangeM || 120) * 0.85);
  const p0 = { x: footPos.x, z: footPos.z };
  const pCtrl = { x: footPos.x - dirX * pullOut * 0.4 + perpX * pullOut * 0.78, z: footPos.z - dirZ * pullOut * 0.4 + perpZ * pullOut * 0.78 };
  const pEnd = { x: targetPos.x - dirX * (minRangeM + 35), z: targetPos.z - dirZ * (minRangeM + 35) };

  const arcScreen = [];
  for (let i = 0; i <= 14; i += 1) {
    const t = i / 14;
    const inv = 1 - t;
    const wx = inv * inv * p0.x + 2 * inv * t * pCtrl.x + t * t * pEnd.x;
    const wz = inv * inv * p0.z + 2 * inv * t * pCtrl.z + t * t * pEnd.z;
    arcScreen.push(projectFn(wx, groundHeightFn(wx, wz) + 0.28, wz));
  }
  ctx.save();
  drawTacticalPolyline(ctx, arcScreen, color, 1.25, [5, 4]);
  const a0 = arcScreen[arcScreen.length - 2];
  const a1 = arcScreen[arcScreen.length - 1];
  const ang = Math.atan2(a1.y - a0.y, a1.x - a0.x);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(a1.x, a1.y);
  ctx.lineTo(a1.x - Math.cos(ang - 0.45) * 6.5, a1.y - Math.sin(ang - 0.45) * 6.5);
  ctx.lineTo(a1.x - Math.cos(ang + 0.45) * 6.5, a1.y - Math.sin(ang + 0.45) * 6.5);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawWeaponReticle(ctx, ptGround, kind, innerR, outerR, groundHeightFn, projectFn, accentColor = '#e85a52', isLocked = false, numIndex = 0) {
  const gy = ptGround.y ?? groundHeightFn(ptGround.x, ptGround.z);
  const center = { x: ptGround.x, y: gy + 0.22, z: ptGround.z };
  const scr = projectFn(center.x, center.y, center.z);

  if (kind === 'rocket') {
    // 7 枚火箭蜂窝细红散点预览
    drawWorldCircle(ctx, center, Math.max(10, outerR), projectFn, accentColor, 1.15, [5, 4], 'rgba(232, 90, 82, 0.06)');
    const subR = Math.max(2.4, outerR * 0.22);
    const offsets = [[0, 0]];
    const ringDist = outerR * 0.54;
    for (let k = 0; k < 6; k += 1) {
      const a = (k * Math.PI) / 3;
      offsets.push([Math.cos(a) * ringDist, Math.sin(a) * ringDist]);
    }
    for (const [ox, oz] of offsets) {
      const cSub = { x: center.x + ox, y: groundHeightFn(center.x + ox, center.z + oz) + 0.24, z: center.z + oz };
      drawWorldCircle(ctx, cSub, subR, projectFn, 'rgba(246, 184, 76, 0.85)', 1.0, [], 'rgba(246, 184, 76, 0.12)');
    }
  } else if (kind === 'missile' || kind === 'agm') {
    // 《断箭》+《Ebbtide》极细同心红圈 + 轻量锁框
    drawWorldCircle(ctx, center, Math.max(8, outerR), projectFn, accentColor, 1.2, [5, 4], 'rgba(232, 90, 82, 0.06)');
    drawWorldCircle(ctx, center, Math.max(3, innerR), projectFn, 'rgba(255, 224, 140, 0.85)', 1.0, [], null);
    ctx.save();
    const box = isLocked ? 12 : 9.5;
    const arm = 4.5;
    ctx.strokeStyle = isLocked ? '#ffe074' : 'rgba(244, 248, 255, 0.9)';
    ctx.lineWidth = 1.3;
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      ctx.beginPath();
      ctx.moveTo(scr.x + sx * box, scr.y + sy * (box - arm));
      ctx.lineTo(scr.x + sx * box, scr.y + sy * box);
      ctx.lineTo(scr.x + sx * (box - arm), scr.y + sy * box);
      ctx.stroke();
    }
    ctx.restore();
  } else if (kind === 'autocannon' || kind === 'cannon') {
    // 《Ebbtide》极细弹着椭圈 + 微十字准星
    drawWorldCircle(ctx, center, Math.max(5, outerR), projectFn, accentColor, 1.15, [4, 4], 'rgba(232, 90, 82, 0.07)');
    ctx.save();
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 1.15;
    ctx.beginPath();
    ctx.moveTo(scr.x - 6, scr.y);
    ctx.lineTo(scr.x + 6, scr.y);
    ctx.moveTo(scr.x, scr.y - 6);
    ctx.lineTo(scr.x, scr.y + 6);
    ctx.stroke();
    ctx.restore();
  } else {
    // 《Ebbtide》航弹图 3/4/5 极细珊瑚红靶环 + 中心微十字（零粗黑描边不遮路面与尸群）
    drawWorldCircle(ctx, center, Math.max(8, outerR), projectFn, accentColor, 1.25, kind === 'bomb' ? [] : [5, 4], 'rgba(232, 90, 82, 0.065)');
    drawWorldCircle(ctx, center, Math.max(3, innerR), projectFn, 'rgba(255, 226, 224, 0.72)', 1.0, [], null);
    ctx.save();
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.moveTo(scr.x - 5, scr.y);
    ctx.lineTo(scr.x + 5, scr.y);
    ctx.moveTo(scr.x, scr.y - 5);
    ctx.lineTo(scr.x, scr.y + 5);
    ctx.stroke();
    ctx.restore();
  }

  if (numIndex > 0) {
    ctx.save();
    ctx.font = '800 9.5px "Cascadia Mono","Consolas",monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(13, 14, 19, 0.88)';
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 1.0;
    ctx.beginPath();
    ctx.arc(scr.x, scr.y - 14, 7.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.fillText(String(numIndex), scr.x, scr.y - 13.7);
    ctx.restore();
  }
}

function drawBombPipRow(ctx, scrPt, usedCount, maxCount) {
  if (!scrPt || maxCount <= 0) return;
  const total = Math.min(8, Math.max(1, maxCount));
  const used = Math.min(total, Math.max(0, usedCount));
  const dotR = 3.6;
  const gap = 10;
  const padX = 8;
  const label = `${used}/${total}`;
  ctx.save();
  ctx.font = '800 10px "Cascadia Mono","Consolas",monospace';
  const lblW = ctx.measureText(label).width;
  const boxW = padX * 2 + total * gap + lblW + 2;
  const boxH = 18;
  const bx = scrPt.x + 16;
  const by = scrPt.y - 26;
  ctx.fillStyle = 'rgba(13, 14, 19, 0.92)';
  ctx.strokeStyle = 'rgba(240, 83, 72, 0.85)';
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.roundRect(bx, by, boxW, boxH, 4);
  ctx.fill();
  ctx.stroke();
  for (let i = 0; i < total; i += 1) {
    const cx = bx + padX + i * gap + 3;
    const cy = by + boxH * 0.5;
    ctx.beginPath();
    ctx.arc(cx, cy, dotR, 0, Math.PI * 2);
    if (i < used) {
      ctx.fillStyle = '#f05348';
      ctx.fill();
    } else {
      ctx.strokeStyle = 'rgba(235, 240, 250, 0.45)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
  }
  ctx.fillStyle = '#eef4fa';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, bx + padX + total * gap + 2, by + boxH * 0.5 + 0.3);
  ctx.restore();
}

function drawHdAltitudeDropLine(ctx, planePos, groundY, projectFn) {
  if (!planePos || !Number.isFinite(planePos.x) || !Number.isFinite(planePos.z)) return;
  const topScr = projectFn(planePos.x, Number.isFinite(planePos.y) ? planePos.y : 185, planePos.z);
  const footScr = projectFn(planePos.x, (Number.isFinite(groundY) ? groundY : 0) + 0.16, planePos.z);
  if (!topScr || !footScr) return;
  ctx.save();
  ctx.strokeStyle = 'rgba(234, 240, 248, 0.62)';
  ctx.lineWidth = 1.05;
  ctx.setLineDash([2, 4]);
  ctx.beginPath();
  ctx.moveTo(topScr.x, topScr.y);
  ctx.lineTo(footScr.x, footScr.y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(234, 240, 248, 0.80)';
  ctx.beginPath();
  ctx.arc(footScr.x, footScr.y, 2.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawHdProjectedStrikeRay(ctx, planePos, targetPos, projectFn, {
  rangeState = 'in_range',
  boundaryPos = null,
  highlighted = true,
} = {}) {
  if (!planePos || !targetPos) return false;
  const pScr = projectFn(planePos.x, Number.isFinite(planePos.y) ? planePos.y : 185, planePos.z);
  const tScr = projectFn(targetPos.x, (Number.isFinite(targetPos.y) ? targetPos.y : 0) + 0.5, targetPos.z);
  if (!pScr || !tScr) return false;

  ctx.save();
  ctx.lineCap = 'round';
  if (rangeState === 'out_of_range' && boundaryPos) {
    const bScr = projectFn(boundaryPos.x, (Number.isFinite(boundaryPos.y) ? boundaryPos.y : 0) + 0.3, boundaryPos.z);
    if (bScr) {
      ctx.strokeStyle = 'rgba(240, 184, 76, 0.80)';
      ctx.lineWidth = 1.25;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(pScr.x, pScr.y);
      ctx.lineTo(bScr.x, bScr.y);
      ctx.stroke();

      ctx.strokeStyle = 'rgba(232, 90, 82, 0.90)';
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(bScr.x, bScr.y);
      ctx.lineTo(tScr.x, tScr.y);
      ctx.stroke();
      ctx.restore();
      return true;
    }
  }

  const dx = tScr.x - pScr.x;
  const dy = tScr.y - pScr.y;
  const distPx = Math.hypot(dx, dy);
  if (rangeState === 'too_close') {
    ctx.strokeStyle = 'rgba(255, 96, 68, 0.88)';
    ctx.setLineDash([4, 4]);
  } else if (distPx >= 2) {
    const grad = ctx.createLinearGradient(pScr.x, pScr.y, tScr.x, tScr.y);
    const aStart = highlighted ? 0.34 : 0.22;
    const aEnd = highlighted ? 0.88 : 0.60;
    grad.addColorStop(0, `rgba(236, 94, 86, ${aStart})`);
    grad.addColorStop(0.55, `rgba(236, 94, 86, ${(aStart + aEnd) * 0.52})`);
    grad.addColorStop(1, `rgba(240, 90, 82, ${aEnd})`);
    ctx.strokeStyle = grad;
    ctx.setLineDash([6, 5]);
  } else {
    ctx.strokeStyle = 'rgba(236, 94, 86, 0.86)';
    ctx.setLineDash([6, 5]);
  }
  ctx.lineWidth = highlighted ? 1.25 : 1.05;
  ctx.beginPath();
  ctx.moveTo(pScr.x, pScr.y);
  ctx.lineTo(tScr.x, tScr.y);
  ctx.stroke();
  ctx.restore();
  return true;
}

function drawHdOrbitTransitRay(ctx, planePos, entryPoint, groundHeightFn, projectFn, etaSec = 0, isRtb = false) {
  if (!planePos || !entryPoint) return false;
  const py = Number.isFinite(planePos.y) ? planePos.y : 185;
  const entryGroundY = groundHeightFn(entryPoint.x, entryPoint.z) + 0.22;
  const pScr = projectFn(planePos.x, py, planePos.z);
  const eAirScr = projectFn(entryPoint.x, py, entryPoint.z);
  const eGndScr = projectFn(entryPoint.x, entryGroundY, entryPoint.z);
  if (!pScr || !eAirScr || !eGndScr) return false;

  ctx.save();
  ctx.lineCap = 'round';
  const distPx = Math.hypot(eAirScr.x - pScr.x, eAirScr.y - pScr.y);
  if (isRtb) {
    if (distPx >= 2) {
      const grad = ctx.createLinearGradient(pScr.x, pScr.y, eAirScr.x, eAirScr.y);
      grad.addColorStop(0, 'rgba(240, 190, 96, 0.52)');
      grad.addColorStop(1, 'rgba(240, 190, 96, 0.94)');
      ctx.strokeStyle = grad;
    } else {
      ctx.strokeStyle = 'rgba(240, 190, 96, 0.88)';
    }
  } else if (distPx >= 2) {
    const grad = ctx.createLinearGradient(pScr.x, pScr.y, eAirScr.x, eAirScr.y);
    grad.addColorStop(0, 'rgba(143, 125, 255, 0.56)');
    grad.addColorStop(1, 'rgba(143, 125, 255, 0.94)');
    ctx.strokeStyle = grad;
  } else {
    ctx.strokeStyle = 'rgba(143, 125, 255, 0.88)';
  }
  ctx.lineWidth = 1.35;
  ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.moveTo(pScr.x, pScr.y);
  ctx.lineTo(eAirScr.x, eAirScr.y);
  ctx.stroke();

  // 接入点空地垂点引导线
  ctx.strokeStyle = isRtb ? 'rgba(240, 190, 96, 0.42)' : 'rgba(176, 162, 250, 0.40)';
  ctx.lineWidth = 1.0;
  ctx.setLineDash([2, 4]);
  ctx.beginPath();
  ctx.moveTo(eAirScr.x, eAirScr.y);
  ctx.lineTo(eGndScr.x, eGndScr.y);
  ctx.stroke();

  ctx.setLineDash([]);
  ctx.fillStyle = isRtb ? 'rgba(240, 190, 96, 0.95)' : 'rgba(176, 162, 250, 0.92)';
  ctx.beginPath();
  ctx.arc(eGndScr.x, eGndScr.y, 3.0, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  if (etaSec > 0.3) {
    drawStrikeBadge(
      ctx,
      eGndScr,
      isRtb ? `撤离 ${etaSec}s` : `${etaSec}s`,
      isRtb ? 'rgba(240, 190, 96, 0.82)' : 'rgba(176, 162, 250, 0.72)',
      '#eef4fa',
    );
  }
  return true;
}

function drawEbbtideKillBox(ctx, startPt, endPt, halfWidthM, groundHeightFn, projectFn, strokeColor = '#e85a52') {
  const dx = endPt.x - startPt.x;
  const dz = endPt.z - startPt.z;
  const len = Math.hypot(dx, dz);
  if (len < 2) return;
  const ux = dx / len;
  const uz = dz / len;
  const px = -uz;
  const pz = ux;
  const hw = Math.max(4, halfWidthM);

  const cornersW = [
    { x: startPt.x + px * hw, z: startPt.z + pz * hw },
    { x: endPt.x + px * hw, z: endPt.z + pz * hw },
    { x: endPt.x - px * hw, z: endPt.z - pz * hw },
    { x: startPt.x - px * hw, z: startPt.z - pz * hw },
  ];
  const cornersS = cornersW.map(c => projectFn(c.x, groundHeightFn(c.x, c.z) + 0.22, c.z));

  ctx.save();
  ctx.beginPath();
  cornersS.forEach((p, idx) => (idx === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
  ctx.closePath();
  ctx.fillStyle = 'rgba(232, 90, 82, 0.065)';
  ctx.fill();
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 1.25;
  ctx.stroke();

  // 中心微细进场流向虚线 + 轻量化终点小箭尖
  const s0 = projectFn(startPt.x, groundHeightFn(startPt.x, startPt.z) + 0.25, startPt.z);
  const s1 = projectFn(endPt.x, groundHeightFn(endPt.x, endPt.z) + 0.25, endPt.z);
  ctx.strokeStyle = 'rgba(244, 248, 255, 0.56)';
  ctx.lineWidth = 1.0;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(s0.x, s0.y);
  ctx.lineTo(s1.x, s1.y);
  ctx.stroke();
  ctx.setLineDash([]);

  const ang = Math.atan2(s1.y - s0.y, s1.x - s0.x);
  const headLen = 7;
  ctx.fillStyle = strokeColor;
  ctx.beginPath();
  ctx.moveTo(s1.x, s1.y);
  ctx.lineTo(s1.x - Math.cos(ang - 0.45) * headLen, s1.y - Math.sin(ang - 0.45) * headLen);
  ctx.lineTo(s1.x - Math.cos(ang + 0.45) * headLen, s1.y - Math.sin(ang + 0.45) * headLen);
  ctx.closePath();
  ctx.fill();

  // 起止微十字准星（无粗描边）
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 1.1;
  for (const pt of [s0, s1]) {
    ctx.beginPath();
    ctx.moveTo(pt.x - 4, pt.y);
    ctx.lineTo(pt.x + 4, pt.y);
    ctx.moveTo(pt.x, pt.y - 4);
    ctx.lineTo(pt.x, pt.y + 4);
    ctx.stroke();
  }
  ctx.restore();
}


export function createCommandAndOverlayLayer({
  host,
  adapter,
  selectionCtrl,
  arbiter,
  getWheelModel = null,
  onWheelAction = null,
} = {}) {
  const canvas = document.createElement('canvas');
  canvas.id = 'scv-tactical-overlay';
  canvas.className = 'scv-tactical-overlay';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:105;image-rendering:auto;filter:none;';

  if (host) {
    const firstCanvas = host.querySelector('canvas');
    if (firstCanvas && firstCanvas.nextSibling) {
      host.insertBefore(canvas, firstCanvas.nextSibling);
    } else {
      host.appendChild(canvas);
    }
  }
  const ctx = canvas.getContext('2d');

  // 模式横幅（预览态 / 编辑笔刷态 / 攻地瞄准态）
  const bannerEl = document.createElement('div');
  bannerEl.id = 'scv-mode-banner';
  bannerEl.className = 'ui-pod scv-mode-banner';
  bannerEl.hidden = true;
  host?.appendChild(bannerEl);

  // V2.2 方案 A：连续八扇区军工仪表盘 8 槽战术轮盘浮层（零 backdrop-filter、零 UI 提示音）
  const wheelBackdrop = document.createElement('div');
  wheelBackdrop.id = 'scv-wheel-host';
  wheelBackdrop.className = 'scv-wheel-host';
  wheelBackdrop.hidden = true;
  wheelBackdrop.innerHTML = `
    <svg class="ui-wheel-ray-svg" aria-hidden="true"></svg>
    <div class="ui-wheel" role="menu" aria-label="战术指令轮盘"></div>`;
  host?.appendChild(wheelBackdrop);

  const wheelBox = wheelBackdrop.querySelector('.ui-wheel');
  const wheelRaySvg = wheelBackdrop.querySelector('.ui-wheel-ray-svg');

  const wheelState = {
    open: false,
    mode: 'tap_click', // 'hold_flick' | 'tap_click'
    originPt: null,
    visualCenter: null,
    pointerPt: null,
    clamped: false,
    hoveredIndex: -1,
    pointerAngleDeg: 0,
    hasPointerAngle: false,
    passedDeadzone: false,
    lastModel: null,
    lastRenderSig: '',
    lastRefreshMs: 0,
  };

  function resolveCurrentWheelModel() {
    if (typeof getWheelModel === 'function') {
      const m = getWheelModel();
      if (m && Array.isArray(m.slots)) return m;
    }
    return {
      eyebrow: '战术轮盘',
      eyebrowTone: 'select',
      slots: [
        { id: 'attackGround', label: '强制攻地', key: 'K', modeTag: '瞄准', iconType: 'hud', iconName: 'attack', tone: 'ground', status: 'ready' },
        { id: 'guard', label: '警戒守备', key: 'Z', modeTag: '开关', iconType: 'hud', iconName: 'shield', tone: 'ground', status: 'ready' },
        { id: 'stop', label: '停航待命', key: 'X', modeTag: '瞬发', iconType: 'hud', iconName: 'stop', tone: 'neutral', status: 'ready' },
        { id: 'resetView', label: '视野复位', key: 'R', modeTag: '瞬发', iconType: 'hud', iconName: 'reset', tone: 'neutral', status: 'ready' },
        { id: 'follow', label: '持续跟随', key: 'G', modeTag: '开关', iconType: 'hud', iconName: 'follow', tone: 'neutral', status: 'ready' },
        { id: 'focus', label: '聚焦视野', key: 'F', modeTag: '瞬发', iconType: 'hud', iconName: 'focus', tone: 'neutral', status: 'ready' },
        null,
        null,
      ],
    };
  }

  function buildWheelModelSig(model) {
    const slots = Array.isArray(model?.slots) ? model.slots : [];
    const slotSig = slots.map(s => (
      s ? `${s.id}:${s.label}:${s.status}:${s.charge || ''}:${Math.round((Number(s.cooldownSec) || 0) * 10)}:${s.reason || ''}` : '_'
    )).join('|');
    const angBucket = wheelState.hasPointerAngle ? Math.round(wheelState.pointerAngleDeg / 3) : 'none';
    return `${model?.eyebrow || ''}|${model?.eyebrowTone || ''}|${wheelState.mode}|${wheelState.hoveredIndex}|${angBucket}|${slotSig}`;
  }

  function refreshWheel(force = false) {
    if (!wheelState.open || !wheelBox) return;
    const model = resolveCurrentWheelModel();
    wheelState.lastModel = model;
    const sig = buildWheelModelSig(model);
    if (force || sig !== wheelState.lastRenderSig) {
      wheelState.lastRenderSig = sig;
      renderTacticalWheel(wheelBox, model, {
        hoveredIndex: wheelState.hoveredIndex,
        pointerAngleDeg: wheelState.pointerAngleDeg,
        hasPointerAngle: wheelState.hasPointerAngle,
        interactionMode: wheelState.mode,
        onSelect: (slot, idx) => {
          const check = canCommitWheelSlot(slot);
          if (check.ok) {
            closeWheel();
            onWheelAction?.(slot, { trigger: 'click', index: idx, check });
          } else if (check.status !== 'empty') {
            onWheelAction?.(slot, { trigger: 'click_blocked', index: idx, check });
          }
        },
        onCancel: () => {
          closeWheel();
        },
      });
    }
    renderTacticalWheelRay(wheelRaySvg, {
      open: wheelState.open,
      originPt: wheelState.originPt,
      visualCenter: wheelState.visualCenter,
      pointerPt: wheelState.pointerPt,
      hoveredIndex: wheelState.hoveredIndex,
      slots: model.slots,
      isLiveFlick: wheelState.mode === 'hold_flick',
    });
  }

  wheelBackdrop.addEventListener('pointerdown', e => {
    if (e.target === wheelBackdrop) {
      e.stopPropagation();
      closeWheel();
    }
  });

  let previewState = null; // { rows, append, command }
  let targetingWorldPt = null;
  let airStrikePreview = null; // { spec, startPos, endPos, hoverPos }
  let pointerLocalPt = null;
  let shiftHeld = false;
  let canvasW = 0;
  let canvasH = 0;
  let canvasDpr = 1;
  let lastWasClean = true;
  let lastOverlayMetrics = {
    tier: 'none',
    span: 135,
    iconCount: 0,
    clusterCount: 0,
    fixedWingGroundZones: 0,
    airTurnArcCount: 0,
    queuedStrikeMarkerCount: 0,
    hasHighOrbitTrack: false,
    nodes: [],
  };

  warmSilhouettePathCache();

  let cachedPalette = readSemanticPalette();
  function refreshPalette() {
    cachedPalette = readSemanticPalette();
    return cachedPalette;
  }

  const isHostDomElement = Boolean(host && typeof Element === 'function' && host instanceof Element);
  let observedW = Math.max(1, Number(host?.clientWidth) || 1280);
  let observedH = Math.max(1, Number(host?.clientHeight) || 720);
  let canvasSizeDirty = true;

  function markSizeDirty(nextW = null, nextH = null) {
    if (Number.isFinite(nextW) && nextW > 0) observedW = Math.max(1, Math.round(nextW));
    if (Number.isFinite(nextH) && nextH > 0) observedH = Math.max(1, Math.round(nextH));
    canvasSizeDirty = true;
  }

  let hostResizeObserver = null;
  if (isHostDomElement && typeof ResizeObserver === 'function') {
    hostResizeObserver = new ResizeObserver(entries => {
      const entry = entries[entries.length - 1];
      const cr = entry?.contentRect;
      if (cr && cr.width > 0 && cr.height > 0) {
        markSizeDirty(cr.width, cr.height);
      } else {
        canvasSizeDirty = true;
      }
    });
    hostResizeObserver.observe(host);
  }
  const onWindowResize = () => {
    if (host) {
      const cw = Number(host.clientWidth);
      const ch = Number(host.clientHeight);
      markSizeDirty(cw > 0 ? cw : observedW, ch > 0 ? ch : observedH);
    } else {
      canvasSizeDirty = true;
    }
  };
  if (isHostDomElement && typeof window !== 'undefined') {
    window.addEventListener('resize', onWindowResize);
  }

  function syncCanvasSize(forceMeasure = false) {
    if (!host) return;
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    if (!forceMeasure && !canvasSizeDirty && dpr === canvasDpr && canvasW > 0 && canvasH > 0) return;
    if (forceMeasure || !hostResizeObserver || observedW <= 1 || observedH <= 1) {
      const cw = Number(host.clientWidth);
      const ch = Number(host.clientHeight);
      if (cw > 0) observedW = Math.max(1, Math.round(cw));
      if (ch > 0) observedH = Math.max(1, Math.round(ch));
    }
    const w = Math.max(1, observedW);
    const h = Math.max(1, observedH);
    canvasSizeDirty = false;
    if (w !== canvasW || h !== canvasH || dpr !== canvasDpr) {
      canvasW = w;
      canvasH = h;
      canvasDpr = dpr;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  function openWheel(localPt = null, options = {}) {
    if (!host) return;
    const w = observedW > 1 ? observedW : (host.clientWidth || 1208);
    const h = observedH > 1 ? observedH : (host.clientHeight || 680);
    const originPt = {
      x: Number.isFinite(localPt?.x) ? localPt.x : w * 0.5,
      y: Number.isFinite(localPt?.y) ? localPt.y : h * 0.5,
    };
    const clamped = clampWheelVisualCenter(originPt, w, h, {
      outerRadius: TACTICAL_WHEEL_CONFIG.visualRadius,
      safePadding: TACTICAL_WHEEL_CONFIG.edgePaddingPx,
    });
    wheelState.open = true;
    wheelState.mode = options.mode === 'hold_flick' ? 'hold_flick' : 'tap_click';
    wheelState.originPt = originPt;
    wheelState.visualCenter = { x: clamped.x, y: clamped.y };
    wheelState.pointerPt = { ...originPt };
    wheelState.clamped = clamped.clamped;
    wheelState.hoveredIndex = -1;
    wheelState.pointerAngleDeg = 0;
    wheelState.hasPointerAngle = false;
    wheelState.passedDeadzone = false;
    wheelState.lastRenderSig = '';
    if (wheelBox) {
      wheelBox.style.left = `${clamped.x}px`;
      wheelBox.style.top = `${clamped.y}px`;
    }
    wheelBackdrop.hidden = false;
    arbiter?.setMode?.(INTERACTION_MODES.WHEEL);
    refreshWheel(true);
  }

  function updateWheelPointer(localPt) {
    if (!wheelState.open || !localPt || !Number.isFinite(localPt.x) || !Number.isFinite(localPt.y)) return;
    wheelState.pointerPt = { x: localPt.x, y: localPt.y };
    const refCenter = wheelState.mode === 'hold_flick' ? wheelState.originPt : wheelState.visualCenter;
    if (!refCenter) return;
    const hit = resolveWheelSectorFromVector(
      localPt.x - refCenter.x,
      localPt.y - refCenter.y,
      TACTICAL_WHEEL_CONFIG.deadzonePx,
    );
    if (wheelState.mode === 'hold_flick' && hit.index >= 0) {
      wheelState.passedDeadzone = true;
    }
    wheelState.hoveredIndex = hit.index;
    wheelState.pointerAngleDeg = hit.angleDeg;
    wheelState.hasPointerAngle = !hit.inDeadzone;
    refreshWheel(false);
  }

  function handleWheelSpaceUp() {
    if (!wheelState.open || wheelState.mode !== 'hold_flick') {
      return { handled: false, outcome: 'ignored' };
    }
    const model = resolveCurrentWheelModel();
    wheelState.lastModel = model;
    const idx = wheelState.hoveredIndex;
    if (idx >= 0 && idx < 8) {
      const slot = model?.slots?.[idx] || null;
      const check = canCommitWheelSlot(slot);
      closeWheel();
      if (check.ok) {
        onWheelAction?.(slot, { trigger: 'flick', index: idx, check });
        return { handled: true, outcome: 'executed', slot, index: idx };
      }
      onWheelAction?.(slot, { trigger: 'flick_blocked', index: idx, check });
      return { handled: true, outcome: 'blocked', slot, index: idx, check };
    }
    if (wheelState.passedDeadzone) {
      closeWheel();
      return { handled: true, outcome: 'cancelled_deadzone' };
    }
    // 轻按 Space 且未离开死区：保留展开进入单击模式（保持无误选）
    wheelState.mode = 'tap_click';
    wheelState.hoveredIndex = -1;
    wheelState.hasPointerAngle = false;
    refreshWheel(true);
    return { handled: true, outcome: 'switched_to_click' };
  }

  function closeWheel() {
    if (!wheelState.open && wheelBackdrop.hidden) return;
    wheelState.open = false;
    wheelState.hoveredIndex = -1;
    wheelState.hasPointerAngle = false;
    wheelState.passedDeadzone = false;
    wheelState.lastRenderSig = '';
    if (wheelRaySvg) wheelRaySvg.innerHTML = '';
    wheelBackdrop.hidden = true;
    if (arbiter?.getMode?.() === INTERACTION_MODES.WHEEL) {
      arbiter.exitToPlay();
    }
  }

  function getWheelState() {
    const slots = Array.isArray(wheelState.lastModel?.slots) ? wheelState.lastModel.slots : [];
    const hovSlot = wheelState.hoveredIndex >= 0 ? (slots[wheelState.hoveredIndex] || null) : null;
    return {
      open: wheelState.open,
      mode: wheelState.mode,
      originPt: wheelState.originPt ? { ...wheelState.originPt } : null,
      visualCenter: wheelState.visualCenter ? { ...wheelState.visualCenter } : null,
      clamped: Boolean(wheelState.clamped),
      hoveredIndex: wheelState.hoveredIndex,
      hoveredSlotId: hovSlot?.id || null,
      passedDeadzone: wheelState.passedDeadzone,
      slotCount: TACTICAL_WHEEL_CONFIG.slotCount,
      validSlotCount: slots.filter(Boolean).length,
    };
  }

  function setPreview(rows, { append = false, command = null } = {}) {
    previewState = rows && rows.length ? { rows, append, command } : null;
    if (!previewState) {
      updateBanner();
      return;
    }
    const rejected = rows.filter(r => !r.valid || r.status === 'rejected');
    const pending = rows.filter(r => r.status === 'pending');
    const reasons = [...new Set(rejected.map(r => r.reason).filter(Boolean))];
    let msg = Number.isFinite(command?.orbitRadius)
      ? `右键松开设定固定翼盘旋区 · 当前半径 R${Math.round(command.orbitRadius)}m（继续向外拖可缩放）· Esc 取消`
      : `右键松开提交${append ? '追加队列' : '移动'} · 拖动设朝向/盘旋半径 · Esc 取消预览`;
    if (pending.length) msg += ` · ${pending.length} 个准备路径`;
    if (rejected.length) msg += ` · ${rejected.length} 个不可达：${reasons.join('；')}`;
    bannerEl.textContent = msg;
    bannerEl.dataset.tone = rejected.length ? 'danger' : pending.length ? 'warn' : 'select';
    bannerEl.hidden = false;
  }

  function clearPreview() {
    previewState = null;
    updateBanner();
  }

  function setTargetingPoint(pt) {
    targetingWorldPt = pt ? { ...pt } : null;
  }

  function setAirStrikePreview(preview) {
    airStrikePreview = preview && preview.spec ? { ...preview } : null;
    updateBanner();
  }

  function setPointerLocal(pt) {
    pointerLocalPt = pt && Number.isFinite(pt.x) && Number.isFinite(pt.y) ? { x: pt.x, y: pt.y } : null;
  }

  function setShiftHeld(flag) {
    const next = Boolean(flag);
    if (shiftHeld !== next) {
      shiftHeld = next;
      updateBanner();
    }
  }

  function updateBanner() {
    if (previewState) return;
    const mode = arbiter?.getMode?.();
    const brush = arbiter?.getActiveBrush?.();
    if (mode === INTERACTION_MODES.EDITOR) {
      const label = brush === 'raise_terrain' ? '地形抬升笔刷 · 左键拖刷高程' : '建楼笔刷 · 左键落点放楼';
      bannerEl.textContent = `${label} · 右键/Esc 退回指挥`;
      bannerEl.dataset.tone = 'safe';
      bannerEl.hidden = false;
      return;
    }
    if (mode === INTERACTION_MODES.TARGETING) {
      if (airStrikePreview?.spec) {
        const sp = airStrikePreview.spec;
        const name = sp.shortLabel || sp.name;
        if (sp.inputMode === 'drag_line') {
          bannerEl.textContent = `${name} · 按住左键拖出航向 · Shift 排队 · 右键/Esc 取消`;
        } else if (sp.inputMode === 'multi_point') {
          const placed = airStrikePreview.dropPoints?.length || 0;
          const cap = Math.max(1, Number(sp.ammo) || 1);
          bannerEl.textContent = `${name} · 左键连点 (${placed}/${cap}) · 右键/Space/Enter 提交 · Esc 取消`;
        } else {
          bannerEl.textContent = `${name} · 左键点选目标 · Shift 排队 · 右键/Esc 取消`;
        }
      } else {
        bannerEl.textContent = '攻地瞄准 · 左键落点 · 右键/Esc 取消';
      }
      bannerEl.dataset.tone = 'danger';
      bannerEl.hidden = false;
      return;
    }
    if (mode === INTERACTION_MODES.DEPLOY) {
      const slug = arbiter?.getDeploySlug?.() || '';
      bannerEl.textContent = `落点部署模式${slug ? ` (${slug})` : ''}：左键点击地图部署 · Shift 连投 · 右键或 Esc 退出`;
      bannerEl.dataset.tone = 'select';
      bannerEl.hidden = false;
      return;
    }
    bannerEl.hidden = true;
  }

  function drawSingleSegment(unit, points, yAlt, facing, style, isSelected, projectFn, isQueued = false, queueTag = '', etaSuffix = '') {
    if (!points || points.length < 2) return;
    const clipped = buildClippedRoute(points[0], unit.ringRadius ?? 4.8, points.slice(1));
    const routePts = clipped ? clipped.points : points;
    const chamfered = chamferWaypoints(routePts, 3.2);
    const screenPts = chamfered.map(p => projectFn(p.x, yAlt ?? p.y ?? 0, p.z));
    const softStroke = style.softOutline || 'rgba(8, 10, 16, 0.42)';

    ctx.save();
    ctx.globalAlpha = isQueued ? 0.48 : style.alpha;
    drawTacticalPolyline(ctx, screenPts, softStroke, isSelected ? 3.0 : 2.2, isQueued ? [8, 6] : []);
    drawTacticalPolyline(ctx, screenPts, style.main, isSelected ? 1.7 : 1.2, isQueued ? [8, 6] : []);
    if (!isQueued) {
      drawRouteChevrons(ctx, chamfered, yAlt ?? 0, projectFn, style, 24);
    }
    const last = points[points.length - 1];
    const prev = points[points.length - 2];
    const ghostYaw = Number.isFinite(facing)
      ? facing
      : Math.atan2(last.x - prev.x, last.z - prev.z);
    ctx.globalAlpha = isSelected ? 0.72 : 0.42;
    drawWireframeGhost(
      ctx,
      toGhostKind(unit.iconKind, unit.role),
      last,
      yAlt ?? last.y ?? 0,
      Math.PI - ghostYaw,
      projectFn,
      SIN_TILT,
      style,
      isSelected || !isQueued,
    );
    const endScreen = projectFn(last.x, yAlt ?? last.y ?? 0, last.z);
    ctx.globalAlpha = 0.88;
    if (queueTag) {
      drawQueueTag(ctx, endScreen, queueTag, style);
    } else if (clipped && (isSelected || etaSuffix)) {
      drawEtaBadge(ctx, endScreen, clipped.totalDist, unit.speed || 14, style, true, '', etaSuffix);
    }
    ctx.restore();
  }

  function render({ hudHidden = false, isEmbedMode = false, currentSpan = 135 } = {}) {
    if (wheelState.open) {
      const nowMs = performance.now();
      if (nowMs - wheelState.lastRefreshMs >= 80) {
        wheelState.lastRefreshMs = nowMs;
        refreshWheel(false);
      }
    }
    syncCanvasSize();
    if (!ctx) return;

    const units = adapter?.getUnits?.() || [];
    const selectedSet = new Set(selectionCtrl?.getSelectedIds?.() || []);
    const hoveredId = selectionCtrl?.getHoveredUnitId?.() || null;
    const mode = arbiter?.getMode?.();
    const combatMode = adapter?.getCombatMode?.() || 'g01_vehicles';
    const hasLiveContent = !isEmbedMode && !hudHidden && combatMode === 'g01_vehicles' && units.length > 0;

    if (!hasLiveContent) {
      if (!lastWasClean) {
        ctx.clearRect(0, 0, canvasW, canvasH);
        lastWasClean = true;
      }
      lastOverlayMetrics = {
        tier: resolveZoomIconTier(currentSpan),
        span: currentSpan,
        iconCount: 0,
        clusterCount: 0,
        fixedWingGroundZones: 0,
        airTurnArcCount: 0,
        hdProjected3DLineCount: 0,
        orbitTransitLineCount: 0,
        queuedStrikeMarkerCount: 0,
        hasHighOrbitTrack: false,
        nodes: [],
      };
      return;
    }

    ctx.clearRect(0, 0, canvasW, canvasH);
    lastWasClean = false;

    const pal = cachedPalette || refreshPalette();
    const projectFn = (wx, wy, wz) => adapter.projectWorld(wx, wy, wz);
    const groundHeightFn = (wx, wz) => adapter.queryGroundHeight(wx, wz);
    const nowMs = Date.now();
    const tier = resolveZoomIconTier(currentSpan);
    const showBodyRings = tier !== 'overview';
    let fixedWingGroundZones = 0;
    let airTurnArcCount = 0;
    let hdProjected3DLineCount = 0;
    let orbitTransitLineCount = 0;
    let queuedStrikeMarkerCount = 0;
    let approachCurveCount = 0;
    const tierItems = [];

    // 1. 主体贴地环、空中垂直投影虚线、固定翼贴地主范围圈与空中极简导引
    for (const u of units) {
      const isSel = selectedSet.has(u.id);
      const isHov = hoveredId === u.id;
      const pos = adapter.getUnitFocus(u.id);
      if (!pos) continue;

      const baseStyle = resolveRouteStyle(u.iconKind, 'active', isSel || isHov, pal);
      baseStyle.selectColor = pal.select;
      baseStyle.selectInner = pal.selectInner;

      const bodyScreen = projectFn(pos.x, pos.y, pos.z);
      if (tier !== 'none' && bodyScreen.visible) {
        tierItems.push({
          id: u.id,
          channel: u.channel || (u.role === 'helicopter' ? 'heli' : u.role === 'fixed_wing' ? 'plane' : 'ground'),
          iconKind: u.iconKind || 'mbt',
          role: u.role,
          x: bodyScreen.x,
          y: tier === 'compact' ? bodyScreen.y - 2 : bodyScreen.y,
          hp: u.hp ?? 100,
          selected: isSel,
          hovered: isHov,
          alive: u.alive !== false,
          visible: true,
        });
      }

      if (!bodyScreen.visible && !isSel && u.role !== 'fixed_wing') continue;

      if (u.role === 'ground') {
        if (showBodyRings || isSel || isHov) {
          const groundScreen = projectFn(pos.x, pos.y + 0.12, pos.z);
          drawGroundRing(ctx, groundScreen, u.ringRadius ?? VEHICLE_META[u.iconKind]?.ringRadius ?? 4.8, u.role, baseStyle, isSel, isHov);
        }
      } else if (u.role === 'helicopter') {
        const groundY = groundHeightFn(pos.x, pos.z);
        const foot = projectFn(pos.x, groundY + 0.12, pos.z);
        // 直升机垂直投影虚线 + 地面小十字（L0/L1/L2 均保留细垂线连至地面十字）
        if (tier !== 'overview' || isSel || isHov) {
          ctx.save();
          ctx.strokeStyle = isSel ? pal.select : pal.heli;
          ctx.globalAlpha = isSel || isHov ? 0.86 : 0.44;
          ctx.lineWidth = isSel || isHov ? 1.5 : 1.05;
          ctx.setLineDash([4, 4]);
          ctx.beginPath();
          ctx.moveTo(bodyScreen.x, bodyScreen.y);
          ctx.lineTo(foot.x, foot.y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(foot.x - 4.5, foot.y);
          ctx.lineTo(foot.x + 4.5, foot.y);
          ctx.moveTo(foot.x, foot.y - 4.5);
          ctx.lineTo(foot.x, foot.y + 4.5);
          ctx.stroke();
          ctx.restore();
        }
        if (showBodyRings && tier !== 'chip') {
          drawGroundRing(ctx, foot, u.ringRadius ?? 5.2, u.role, baseStyle, isSel, isHov);
        }
        if (isSel || isHov) {
          const rPx = Math.max(14, (u.pickRadius || 6.8) * (bodyScreen.ppm || 1));
          const boundPts = [
            { x: bodyScreen.x - rPx, y: bodyScreen.y - rPx * 0.7 },
            { x: bodyScreen.x + rPx, y: bodyScreen.y + rPx * 0.7 },
          ];
          drawAirLockBrackets(ctx, boundPts, baseStyle, isSel);
        }
      } else {
        // Stage 1B：固定翼贴地主范围圈 + 垂足实时滑动跟踪点 + 空中极简锁框与 35° 机头转向导引虚弧
        const oc = u.orbitCenter || { x: pos.x, z: pos.z };
        const orbitR = u.orbitRadius || 85;
        drawGroundOrbitZone(
          ctx,
          oc,
          orbitR,
          groundHeightFn,
          projectFn,
          baseStyle,
          isSel || isHov,
          {
            planePos: pos,
            segments: 56,
            drawArrows: true,
            drawCenterCross: true,
            drawFootLine: !u.fixedWingController,
            radiusLabel: (isSel || isHov) ? `R${Math.round(orbitR)}m` : '',
            palette: pal,
          },
        );
        fixedWingGroundZones += 1;

        if (isSel || isHov) {
          if (u.fixedWingController) {
            drawHdAltitudeDropLine(ctx, pos, groundHeightFn(pos.x, pos.z), projectFn);
          }
          const rPx = Math.max(14, (u.pickRadius || 7.6) * (bodyScreen.ppm || 1));
          const boundPts = [
            { x: bodyScreen.x - rPx, y: bodyScreen.y - rPx * 0.7 },
            { x: bodyScreen.x + rPx, y: bodyScreen.y + rPx * 0.7 },
          ];
          drawAirLockBrackets(ctx, boundPts, baseStyle, isSel);
        }
        if ((tier !== 'overview' || isSel || isHov) && isFixedWingOrbiting(u, pos)) {
          const drawn = drawAirTurnArc(ctx, pos, oc, orbitR, projectFn, baseStyle, {
            arcDeg: 35,
            yaw: u.node?.rotation?.y ?? 0,
            turnDir: 1,
            highlighted: isSel || isHov,
            onlyOnOrbit: true,
          });
          if (drawn) airTurnArcCount += 1;
        }
      }

      // 贴身名牌归属短实线（L0 全量、L1 仅选中/悬停；L2/L3 全交 2D 图标层）
      if (u.labelAnchorOffset && currentSpan <= 650 && (tier === 'none' || isSel || isHov)) {
        ctx.save();
        ctx.strokeStyle = isSel ? pal.select : isHov ? '#ffffff' : baseStyle.main;
        ctx.globalAlpha = isSel || isHov ? 0.90 : 0.45;
        ctx.lineWidth = isSel || isHov ? 1.5 : 1.0;
        ctx.beginPath();
        ctx.moveTo(bodyScreen.x, bodyScreen.y);
        ctx.lineTo(u.labelAnchorOffset.x, u.labelAnchorOffset.y);
        ctx.stroke();
        ctx.restore();
      }
    }

    // 2. 已提交航路、多段队列、射程圈与最近拒收提示（固定翼不再绘 234m 空中整圈）
    for (const u of units) {
      const isSel = selectedSet.has(u.id);
      const isHov = hoveredId === u.id;
      const pos = adapter.getUnitFocus(u.id);
      if (!pos) continue;

      const style = resolveRouteStyle(u.iconKind, u.commandStatus?.status === 'pending' ? 'pending' : 'active', isSel || isHov, pal);

      // Phase 4: 地面单位选中时或攻地态时绘制主武器射程圈（只作投影）
      if (u.role === 'ground' && isSel && (mode === INTERACTION_MODES.TARGETING || u.groundGuard)) {
        const rangeM = u.weaponRangeM ?? 140;
        drawWorldCircle(
          ctx,
          { x: pos.x, y: pos.y + 0.15, z: pos.z },
          rangeM,
          projectFn,
          mode === INTERACTION_MODES.TARGETING ? pal.danger : pal.ground,
          mode === INTERACTION_MODES.TARGETING ? 1.6 : 1.0,
          [8, 6],
          null,
        );
      }

      // 活动航路（已提交且未完成的路段；固定翼底座托管单位走真实物理航线，不再绘假 waypoints）
      if (!u.fixedWingController && (isSel || isHov || u.waypoints?.length > 0) && u.waypoints?.length > (u.wpIndex || 0)) {
        const remPts = [{ x: pos.x, y: pos.y, z: pos.z }];
        for (let i = u.wpIndex || 0; i < u.waypoints.length; i += 1) {
          const wp = u.waypoints[i];
          remPts.push({ x: wp.x, y: wp.y, z: wp.z });
        }
        const yAlt = u.role === 'fixed_wing' ? (u.node?.position?.y || 185) : u.role === 'helicopter' ? pos.y : null;
        drawSingleSegment(u, remPts, yAlt, u.targetFacing, style, isSel || isHov, projectFn, false);

        // 后续 Shift 队列段
        let prevEnd = remPts[remPts.length - 1];
        if (Array.isArray(u.queue) && u.queue.length > 0) {
          u.queue.forEach((qJob, qIdx) => {
            if (!qJob?.point) return;
            const gy = u.role === 'fixed_wing' ? (u.node?.position?.y || 185) : u.role === 'helicopter' ? (adapter.queryGroundHeight(qJob.point.x, qJob.point.z) + 22) : adapter.queryGroundHeight(qJob.point.x, qJob.point.z);
            const nextEnd = { x: qJob.point.x, y: gy, z: qJob.point.z };
            drawSingleSegment(
              u,
              [prevEnd, nextEnd],
              gy,
              qJob.facing,
              style,
              isSel,
              projectFn,
              true,
              `#${qIdx + 2}`,
            );
            prevEnd = nextEnd;
          });
        }
      }

      // 2A + V2.4：原生高清 DPR 投影固定翼移动路径线与全队列地面可视化（战机 → 1 → 2 → 3 → 4...）
      if (u.role === 'fixed_wing' && u.fixedWingController?.getPredictedFlightPath) {
        const pPath = u.fixedWingController.getPredictedFlightPath(u);
        if (pPath && pPath.active) {
          if (pPath.kind === 'orbit_transit' && pPath.entryPoint) {
            if (isSel || isHov) {
              const drawnOrbitLine = drawHdOrbitTransitRay(
                ctx,
                pPath.planePos || pos,
                pPath.entryPoint,
                groundHeightFn,
                projectFn,
                pPath.etaSec,
                Boolean(pPath.isRtb),
              );
              if (drawnOrbitLine) orbitTransitLineCount += 1;
            }
          } else {
            const strikeNodes = collectFixedWingStrikeNodes(pPath);
            if (strikeNodes.length > 0) {
              const firstNode = strikeNodes[0];
              const firstAnchor = firstNode.isBox ? firstNode.startPos : firstNode.pt;
              if (
                (pPath.phase === 'transit' || pPath.phase === 'align')
                && Array.isArray(pPath.approachCurve)
                && pPath.approachCurve.length >= 2
                && (isSel || isHov)
              ) {
                const curvePts = pPath.approachCurve.map(cp => projectFn(cp.x, groundHeightFn(cp.x, cp.z) + 0.24, cp.z));
                drawTacticalPolyline(ctx, curvePts, 'rgba(232, 90, 82, 0.58)', 1.35, [5, 4]);
                const badgeAnchorWorld = (pPath.phase === 'transit' && pPath.entryPoint)
                  ? pPath.entryPoint
                  : pPath.approachCurve[Math.floor(pPath.approachCurve.length * 0.5)];
                if (badgeAnchorWorld) {
                  const bScr = projectFn(badgeAnchorWorld.x, groundHeightFn(badgeAnchorWorld.x, badgeAnchorWorld.z) + 0.28, badgeAnchorWorld.z);
                  const aimPt = (pPath.phase === 'transit' && pPath.startPos) ? pPath.startPos : firstAnchor;
                  if (pPath.phase === 'transit' && aimPt) {
                    const aimScr = projectFn(aimPt.x, groundHeightFn(aimPt.x, aimPt.z) + 0.28, aimPt.z);
                    const adx = aimScr.x - bScr.x;
                    const ady = aimScr.y - bScr.y;
                    const aLen = Math.hypot(adx, ady);
                    if (aLen > 4) {
                      const ux = adx / aLen;
                      const uy = ady / aLen;
                      const px = -uy;
                      const py = ux;
                      ctx.save();
                      ctx.strokeStyle = 'rgba(232, 90, 82, 0.85)';
                      ctx.lineWidth = 1.5;
                      ctx.beginPath();
                      ctx.moveTo(bScr.x - ux * 5 + px * 4, bScr.y - uy * 5 + py * 4);
                      ctx.lineTo(bScr.x + ux * 4, bScr.y + uy * 4);
                      ctx.lineTo(bScr.x - ux * 5 - px * 4, bScr.y - uy * 5 - py * 4);
                      ctx.stroke();
                      ctx.restore();
                    }
                  }
                  const tagTxt = pPath.phase === 'align'
                    ? `对准 ${pPath.etaSec}s`
                    : `${pPath.goAroundCount > 0 ? '复飞' : '入航'} ${pPath.etaSec}s`;
                  drawStrikeBadge(ctx, bScr, tagTxt, 'rgba(232, 90, 82, 0.78)', '#eef4fa', 'rgba(13, 14, 19, 0.90)', -18);
                }
                approachCurveCount += 1;
              }
              if (firstAnchor && (isSel || isHov || pPath.phase === 'run')) {
                const drawnRay = drawHdProjectedStrikeRay(
                  ctx,
                  pPath.planePos || pos,
                  {
                    x: firstAnchor.x,
                    y: groundHeightFn(firstAnchor.x, firstAnchor.z),
                    z: firstAnchor.z,
                  },
                  projectFn,
                  { rangeState: 'in_range', highlighted: isSel || isHov },
                );
                if (drawnRay) hdProjected3DLineCount += 1;
              }

              // 串联地面全队列虚线（1 → 2 → 3 → 4...，扫射盒沿 start -> end 串联至下一趟起点）
              if (strikeNodes.length > 1) {
                const chainScreenPts = [];
                for (const sn of strikeNodes) {
                  if (sn.isBox && sn.startPos && sn.endPos) {
                    chainScreenPts.push(projectFn(sn.startPos.x, groundHeightFn(sn.startPos.x, sn.startPos.z) + 0.24, sn.startPos.z));
                    chainScreenPts.push(projectFn(sn.endPos.x, groundHeightFn(sn.endPos.x, sn.endPos.z) + 0.24, sn.endPos.z));
                  } else if (sn.pt) {
                    chainScreenPts.push(projectFn(sn.pt.x, groundHeightFn(sn.pt.x, sn.pt.z) + 0.24, sn.pt.z));
                  }
                }
                if (chainScreenPts.length >= 2) {
                  drawTacticalPolyline(ctx, chainScreenPts, 'rgba(232, 90, 82, 0.78)', 1.2, [5, 4]);
                }
              }

              strikeNodes.forEach((sn, idx) => {
                const numIndex = idx + 1;
                if (sn.isBox && sn.startPos && sn.endPos) {
                  drawEbbtideKillBox(
                    ctx,
                    sn.startPos,
                    sn.endPos,
                    sn.halfW,
                    groundHeightFn,
                    projectFn,
                    sn.isActivePass ? '#e85a52' : 'rgba(232, 90, 82, 0.85)',
                  );
                  const boxMid = {
                    x: (sn.startPos.x + sn.endPos.x) * 0.5,
                    z: (sn.startPos.z + sn.endPos.z) * 0.5,
                  };
                  const boxScr = projectFn(boxMid.x, groundHeightFn(boxMid.x, boxMid.z) + 0.30, boxMid.z);
                  if (strikeNodes.length > 1) {
                    drawReticleIndexCircle(ctx, boxScr, numIndex, '#e85a52');
                  }
                  drawStrikeBadge(ctx, boxScr, `${sn.etaSec}s`, 'rgba(232, 90, 82, 0.85)', '#eef4fa', 'rgba(13, 14, 19, 0.92)', 14);
                } else if (sn.pt) {
                  const showNum = strikeNodes.length > 1 || sn.weaponKind === 'bomb' ? numIndex : 0;
                  drawWeaponReticle(
                    ctx,
                    sn.pt,
                    sn.weaponKind,
                    sn.innerR,
                    sn.outerR,
                    groundHeightFn,
                    projectFn,
                    '#e85a52',
                    sn.isActivePass,
                    showNum,
                  );
                  if (sn.showEta) {
                    const tScr = projectFn(sn.pt.x, groundHeightFn(sn.pt.x, sn.pt.z) + 0.35, sn.pt.z);
                    drawStrikeBadge(
                      ctx,
                      tScr,
                      `${sn.etaSec}s`,
                      'rgba(232, 90, 82, 0.85)',
                      '#eef4fa',
                      'rgba(13, 14, 19, 0.92)',
                      16,
                    );
                  }
                }
              });

              queuedStrikeMarkerCount += strikeNodes.length;
            }
          }
        }
      }

      // 地面单位已提交的 Shift 多点强制攻地队列（单位 → 1 → 2 → 3...）
      if (u.role === 'ground' && (isSel || isHov) && Array.isArray(u.attackGroundQueue) && u.attackGroundQueue.length > 0) {
        const validAgPts = u.attackGroundQueue
          .map(item => item?.point)
          .filter(pt => pt && Number.isFinite(pt.x) && Number.isFinite(pt.z))
          .map(pt => ({ x: pt.x, y: groundHeightFn(pt.x, pt.z) + 0.22, z: pt.z }));
        if (validAgPts.length > 0) {
          const linkScr = [
            projectFn(pos.x, pos.y + 0.6, pos.z),
            ...validAgPts.map(pt => projectFn(pt.x, pt.y, pt.z)),
          ];
          drawTacticalPolyline(ctx, linkScr, 'rgba(240, 83, 72, 0.84)', 1.35, [5, 4]);
          validAgPts.forEach((pt, idx) => {
            drawWorldCircle(ctx, pt, 16, projectFn, pal.danger, 1.4, [6, 4], 'rgba(240, 83, 72, 0.06)');
            drawWorldCircle(ctx, pt, 6, projectFn, pal.select, 1.5, [], null);
            const ptScr = projectFn(pt.x, pt.y, pt.z);
            drawReticleIndexCircle(ctx, ptScr, idx + 1, pal.danger);
          });
          queuedStrikeMarkerCount += validAgPts.length;
        }
      }

      // 最近一次不可达拒收标红反馈（保持 3.5 秒）
      if (isSel && u.lastRejectedCommand?.point && (nowMs - u.lastRejectedCommand.atMs) < 3500) {
        const rejPt = u.lastRejectedCommand.point;
        const rejY = adapter.queryGroundHeight(rejPt.x, rejPt.z);
        const rejStyle = resolveRouteStyle(u.iconKind, 'rejected', true, pal);
        const startPt = { x: pos.x, y: pos.y, z: pos.z };
        const endPt = { x: rejPt.x, y: rejY, z: rejPt.z };
        const sPts = [projectFn(startPt.x, startPt.y, startPt.z), projectFn(endPt.x, endPt.y, endPt.z)];
        ctx.save();
        drawTacticalPolyline(ctx, sPts, rejStyle.main, 1.8, [4, 4]);
        drawWireframeGhost(ctx, toGhostKind(u.iconKind, u.role), endPt, rejY, Math.PI, projectFn, SIN_TILT, rejStyle, true);
        drawEtaBadge(ctx, sPts[1], 0, u.speed || 14, rejStyle, false, u.lastRejectedCommand.reason || '不可达');
        ctx.restore();
      }
    }

    // 3. 右键按住实时预览（最高优先级标绘）
    if (previewState?.rows?.length) {
      for (const row of previewState.rows) {
        const u = row.unit;
        const styleMode = !row.valid || row.status === 'rejected'
          ? 'rejected'
          : row.status === 'pending'
            ? 'pending'
            : 'preview';
        const style = resolveRouteStyle(u.iconKind, styleMode, true, pal);
        const startPos = previewState.append && (u.job?.point || u.queue?.length)
          ? (() => {
            const jobs = [u.job, ...(u.queue || [])].filter(Boolean);
            const last = jobs[jobs.length - 1]?.point || adapter.getUnitFocus(u.id);
            return { x: last.x, y: adapter.queryGroundHeight(last.x, last.z), z: last.z };
          })()
          : adapter.getUnitFocus(u.id);
        const pt = row.job?.point;
        if (!startPos || !pt) continue;
        const endY = u.role === 'fixed_wing'
          ? (u.node?.position?.y || 185)
          : u.role === 'helicopter'
            ? adapter.queryGroundHeight(pt.x, pt.z) + 22.0
            : adapter.queryGroundHeight(pt.x, pt.z);
        const segPts = row.waypoints && row.waypoints.length >= 2
          ? row.waypoints.map(p => ({ x: p[0], y: p[1], z: p[2] }))
          : [startPos, { x: pt.x, y: endY, z: pt.z }];

        if (row.valid) {
          if (u.role === 'fixed_wing' && u.fixedWingController) {
            const oldOc = u.orbitCenter || { x: startPos.x, z: startPos.z };
            const oldGy = groundHeightFn(oldOc.x, oldOc.z) + 0.15;
            const newGy = groundHeightFn(pt.x, pt.z) + 0.15;
            const customR = Number.isFinite(previewState.command?.orbitRadius);
            const orbitR = customR ? previewState.command.orbitRadius : (u.orbitRadius || 85);
            drawTacticalPolyline(
              ctx,
              [projectFn(oldOc.x, oldGy, oldOc.z), projectFn(pt.x, newGy, pt.z)],
              style.main,
              1.6,
              [6, 5],
            );
            drawGroundOrbitZone(
              ctx,
              pt,
              orbitR,
              groundHeightFn,
              projectFn,
              style,
              true,
              {
                drawArrows: true,
                drawCenterCross: true,
                drawFootLine: false,
                radiusLabel: `R${Math.round(orbitR)}m`,
                palette: pal,
              },
            );
          } else {
            drawSingleSegment(
              u,
              segPts,
              u.role === 'fixed_wing' ? (u.node?.position?.y || 185) : null,
              row.job?.facing,
              style,
              true,
              projectFn,
              false,
              '',
              previewState.append ? ' · 追加' : '',
            );
            if (u.role === 'fixed_wing') {
              const oldOc = u.orbitCenter || { x: startPos.x, z: startPos.z };
              const oldGy = groundHeightFn(oldOc.x, oldOc.z) + 0.15;
              const newGy = groundHeightFn(pt.x, pt.z) + 0.15;
              const customR = Number.isFinite(previewState.command?.orbitRadius);
              const orbitR = customR ? previewState.command.orbitRadius : (u.orbitRadius || 85);
              drawTacticalPolyline(
                ctx,
                [projectFn(oldOc.x, oldGy, oldOc.z), projectFn(pt.x, newGy, pt.z)],
                style.main,
                1.6,
                [6, 5],
              );
              drawGroundOrbitZone(
                ctx,
                pt,
                orbitR,
                groundHeightFn,
                projectFn,
                style,
                true,
                {
                  drawArrows: true,
                  drawCenterCross: true,
                  drawFootLine: false,
                  radiusLabel: `R${Math.round(orbitR)}m`,
                  palette: pal,
                },
              );
            }
          }
        } else {
          const s0 = projectFn(startPos.x, startPos.y, startPos.z);
          const s1 = projectFn(pt.x, endY, pt.z);
          ctx.save();
          drawTacticalPolyline(ctx, [s0, s1], style.outline, 3.6, [5, 5]);
          drawTacticalPolyline(ctx, [s0, s1], style.main, 2.0, [5, 5]);
          drawWireframeGhost(ctx, toGhostKind(u.iconKind, u.role), pt, endY, Math.PI - (row.job?.facing ?? 0), projectFn, SIN_TILT, style, true);
          drawEtaBadge(ctx, s1, 0, u.speed || 14, style, false, row.reason || '不可达');
          ctx.restore();
        }
      }
    }

    // 4. Phase 4 强制攻地落点爆炸圈投影 & 4A 固定翼全武器三态射程环 +《Ebbtide》连点航弹标绘
    if (mode === INTERACTION_MODES.TARGETING && airStrikePreview?.spec) {
      const sp = airStrikePreview.spec;
      const startP = airStrikePreview.startPos;
      const endP = airStrikePreview.endPos;
      const hovP = airStrikePreview.hoverPos || targetingWorldPt;
      const drops = Array.isArray(airStrikePreview.dropPoints) ? airStrikePreview.dropPoints : [];
      const outerR = Math.max(4, Number(sp.splashOuterM) || 12);
      const innerR = Math.max(2, Number(sp.splashInnerM) || 4);
      const minR = Math.max(0, Number(sp.minRangeM) || 0);
      const maxR = Math.max(minR + 20, Number(sp.maxRangeM) || 420);

      // 以选中固定翼实时地面投影点为圆心，判定三态距离（射程内 / 超距 / 过近）
      const selectedPlanes = units.filter(it => selectedSet.has(it.id) && it.role === 'fixed_wing');
      const evalRefPt = startP || hovP || drops[drops.length - 1] || null;
      let primaryFoot = null;
      let primaryPlanePos = null;
      let primaryRange = { state: 'in_range', distM: 0, minRangeM: minR, maxRangeM: maxR };

      selectedPlanes.forEach((plane, idx) => {
        const pPos = adapter.getUnitFocus(plane.id);
        if (!pPos) return;
        const foot = { x: pPos.x, z: pPos.z };
        const dGround = evalRefPt ? Math.hypot(evalRefPt.x - foot.x, evalRefPt.z - foot.z) : 0;
        const rEval = evalRefPt ? evaluateRangeState(dGround, minR, maxR) : { state: 'in_range', distM: 0, minRangeM: minR, maxRangeM: maxR };
        drawWorldRangeRings(ctx, foot, minR, maxR, rEval.state, groundHeightFn, projectFn, pal);
        if (idx === 0) {
          primaryFoot = foot;
          primaryPlanePos = { x: pPos.x, y: pPos.y, z: pPos.z };
          primaryRange = rEval;
        }
      });

      const accentByState = primaryRange.state === 'out_of_range'
        ? '#f0b84c'
        : (primaryRange.state === 'too_close' ? '#ff6044' : '#e85a52');

      // 高清 DPR 空间投影预览斜线 + 超距境界菱形 + 过近外绕弧箭头
      let boundaryWorld = null;
      if (primaryFoot && evalRefPt) {
        if (primaryRange.state === 'out_of_range' && primaryRange.distM > 1) {
          const tB = maxR / primaryRange.distM;
          const bx = primaryFoot.x + (evalRefPt.x - primaryFoot.x) * tB;
          const bz = primaryFoot.z + (evalRefPt.z - primaryFoot.z) * tB;
          const by = groundHeightFn(bx, bz);
          boundaryWorld = {
            x: bx,
            y: (primaryPlanePos?.y || 185) + ((by + 0.3) - (primaryPlanePos?.y || 185)) * tB,
            z: bz,
          };
          const bScr = projectFn(bx, by + 0.28, bz);
          drawBoundaryDiamond(ctx, bScr, '#f0b84c');
        } else if (primaryRange.state === 'too_close') {
          drawTooCloseBypassArrow(ctx, primaryFoot, evalRefPt, minR, groundHeightFn, projectFn, '#ff6044');
        }
        if (primaryPlanePos) {
          const refY = groundHeightFn(evalRefPt.x, evalRefPt.z);
          const drawnPrevRay = drawHdProjectedStrikeRay(
            ctx,
            primaryPlanePos,
            { x: evalRefPt.x, y: refY, z: evalRefPt.z },
            projectFn,
            { rangeState: primaryRange.state, boundaryPos: boundaryWorld, highlighted: true },
          );
          if (drawnPrevRay) hdProjected3DLineCount += 1;
        }
      }

      // V2.4：Shift 排队状态与既有队列末点信息，用于绘制串联预览虚线与「已排/余弹」微标
      const primaryPlane = selectedPlanes[0] || null;
      const existingNodes = primaryPlane?.fixedWingController?.getPredictedFlightPath
        ? collectFixedWingStrikeNodes(primaryPlane.fixedWingController.getPredictedFlightPath(primaryPlane))
        : [];
      const lastNode = existingNodes.length > 0 ? existingNodes[existingNodes.length - 1] : null;
      const tailAnchor = lastNode ? (lastNode.endPos || lastNode.pt) : null;
      const plannedForWeapon = primaryPlane?.fixedWingController?.getPlannedWeaponTargetCount
        ? primaryPlane.fixedWingController.getPlannedWeaponTargetCount(primaryPlane, sp.id)
        : existingNodes.filter(n => n.weaponId === sp.id).length;
      const ammoCap = Math.max(0, Number(sp.ammo) || 0);
      const isShiftChainActive = Boolean(shiftHeld || existingNodes.length > 0);
      const previewLinkTarget = startP || hovP;
      if (isShiftChainActive && tailAnchor && previewLinkTarget) {
        const sTail = projectFn(tailAnchor.x, groundHeightFn(tailAnchor.x, tailAnchor.z) + 0.24, tailAnchor.z);
        const sPreview = projectFn(previewLinkTarget.x, groundHeightFn(previewLinkTarget.x, previewLinkTarget.z) + 0.24, previewLinkTarget.z);
        drawTacticalPolyline(ctx, [sTail, sPreview], 'rgba(232, 90, 82, 0.85)', 1.25, [5, 4]);
      }

      if (sp.inputMode === 'drag_line' && startP && endP) {
        const dragLen = Math.hypot(endP.x - startP.x, endP.z - startP.z);
        if (dragLen >= 4) {
          const killBoxHalfW = Math.max(4, (Number(sp.strafeWidthM) || 20) * 0.5);
          drawEbbtideKillBox(ctx, startP, endP, killBoxHalfW, groundHeightFn, projectFn, accentByState);
          const midX = (startP.x + endP.x) * 0.5;
          const midZ = (startP.z + endP.z) * 0.5;
          const midScreen = projectFn(midX, groundHeightFn(midX, midZ) + 0.3, midZ);
          const dragPreviewInfo = formatDragLineAmmoPreview(sp, dragLen);
          const dragBadgeAccent = dragPreviewInfo.insufficient ? '#f0b84c' : accentByState;
          const dragBadgeTxtColor = dragPreviewInfo.insufficient ? '#fde68a' : '#ffffff';
          if (isShiftChainActive) drawReticleIndexCircle(ctx, midScreen, existingNodes.length + 1, dragBadgeAccent);
          drawStrikeBadge(ctx, midScreen, dragPreviewInfo.text, dragBadgeAccent, dragBadgeTxtColor);
        } else {
          drawWeaponReticle(ctx, startP, sp.kind || 'autocannon', innerR, outerR, groundHeightFn, projectFn, accentByState, false, isShiftChainActive ? existingNodes.length + 1 : 0);
          if (primaryFoot) {
            const sScr = projectFn(startP.x, groundHeightFn(startP.x, startP.z) + 0.25, startP.z);
            drawStrikeBadge(ctx, sScr, `${Math.round(primaryRange.distM)}m`, accentByState, '#eef4fa', 'rgba(13, 14, 19, 0.92)', 16);
          }
        }
      } else if (sp.inputMode === 'multi_point') {
        // 《Ebbtide》航弹连点：极细红双圈 + 淡珊瑚串联细虚线 + 右上方点阵计数 ●●○○
        const baseSeq = existingNodes.length;
        if (drops.length > 0) {
          const linkPts = drops.map(pt => projectFn(pt.x, groundHeightFn(pt.x, pt.z) + 0.24, pt.z));
          if (hovP && drops.length < Math.max(1, Number(sp.ammo) || 4)) {
            linkPts.push(projectFn(hovP.x, groundHeightFn(hovP.x, hovP.z) + 0.24, hovP.z));
          }
          if (linkPts.length >= 2) {
            drawTacticalPolyline(ctx, linkPts, 'rgba(232, 90, 82, 0.78)', 1.15, [4, 4]);
          }
          drops.forEach((pt, idx) => {
            drawWeaponReticle(ctx, pt, 'bomb', innerR, outerR, groundHeightFn, projectFn, '#e85a52', true, baseSeq + idx + 1);
          });
        }
        if (hovP) {
          const hg = { x: hovP.x, y: hovP.y ?? groundHeightFn(hovP.x, hovP.z), z: hovP.z };
          const hScr = projectFn(hg.x, hg.y, hg.z);
          if (drops.length < Math.max(1, Number(sp.ammo) || 4)) {
            drawWeaponReticle(ctx, hg, 'bomb', innerR, outerR, groundHeightFn, projectFn, accentByState, false, baseSeq + drops.length + 1);
          }
          drawBombPipRow(ctx, hScr, drops.length, Math.max(1, Number(sp.ammo) || 4));
          if (primaryFoot) {
            drawStrikeBadge(ctx, hScr, `${Math.round(primaryRange.distM)}m`, accentByState, '#eef4fa', 'rgba(13, 14, 19, 0.92)', 16);
          }
        }
      } else if (hovP) {
        const hg = { x: hovP.x, y: hovP.y ?? groundHeightFn(hovP.x, hovP.z), z: hovP.z };
        const hScr = projectFn(hg.x, hg.y, hg.z);
        const nextNum = isShiftChainActive ? existingNodes.length + 1 : 0;
        drawWeaponReticle(ctx, hg, sp.kind || 'missile', innerR, outerR, groundHeightFn, projectFn, accentByState, primaryRange.state === 'in_range', nextNum);
        if (shiftHeld || plannedForWeapon > 0) {
          if (ammoCap > 0 && ammoCap <= 8) {
            drawBombPipRow(ctx, hScr, plannedForWeapon, ammoCap);
          } else {
            drawStrikeBadge(ctx, hScr, `已排 ${plannedForWeapon}/${ammoCap}`, accentByState, '#eef4fa', 'rgba(13, 14, 19, 0.92)', -48);
          }
        }
        if (primaryFoot) {
          drawStrikeBadge(ctx, hScr, `${Math.round(primaryRange.distM)}m`, accentByState, '#eef4fa', 'rgba(13, 14, 19, 0.92)', 16);
        }
      }
    } else if (mode === INTERACTION_MODES.TARGETING && targetingWorldPt) {
      const targetGround = {
        x: targetingWorldPt.x,
        y: targetingWorldPt.y ?? adapter.queryGroundHeight(targetingWorldPt.x, targetingWorldPt.z),
        z: targetingWorldPt.z,
      };
      const targetScreen = projectFn(targetGround.x, targetGround.y, targetGround.z);
      let firstDist = null;
      let firstOut = false;
      let firstGroundQueueLen = 0;
      let firstGroundTailPt = null;
      for (const id of selectedSet) {
        const u = units.find(it => it.id === id && it.role === 'ground');
        if (!u) continue;
        const pos = adapter.getUnitFocus(u.id);
        if (!pos) continue;
        if (firstGroundQueueLen === 0 && Array.isArray(u.attackGroundQueue) && u.attackGroundQueue.length > 0) {
          firstGroundQueueLen = u.attackGroundQueue.length;
          firstGroundTailPt = u.attackGroundQueue[u.attackGroundQueue.length - 1]?.point || null;
        }
        const rangeM = u.weaponRangeM ?? 140;
        const dGround = Math.hypot(targetGround.x - pos.x, targetGround.z - pos.z);
        const rEval = evaluateRangeState(dGround, 12, rangeM);
        drawWorldRangeRings(ctx, pos, 12, rangeM, rEval.state, groundHeightFn, projectFn, pal);
        const uScreen = projectFn(pos.x, pos.y + 1.2, pos.z);
        drawTacticalPolyline(ctx, [uScreen, targetScreen], rEval.state === 'out_of_range' ? '#f6b83d' : pal.danger, 1.8, [6, 5]);
        if (rEval.state === 'out_of_range' && dGround > 1) {
          const tb = rangeM / dGround;
          const bx = pos.x + (targetGround.x - pos.x) * tb;
          const bz = pos.z + (targetGround.z - pos.z) * tb;
          drawBoundaryDiamond(ctx, projectFn(bx, groundHeightFn(bx, bz) + 0.25, bz), '#f6b83d');
          firstOut = true;
        }
        if (firstDist === null) firstDist = dGround;
      }
      if (shiftHeld && firstGroundTailPt) {
        const tScr = projectFn(firstGroundTailPt.x, groundHeightFn(firstGroundTailPt.x, firstGroundTailPt.z) + 0.24, firstGroundTailPt.z);
        drawTacticalPolyline(ctx, [tScr, targetScreen], 'rgba(240, 83, 72, 0.85)', 1.3, [5, 4]);
      }
      // 内核毁伤圈 6m + 外围溅射圈 16m
      drawWorldCircle(ctx, targetGround, 16, projectFn, firstOut ? '#f6b83d' : pal.danger, 1.8, [6, 4], pal.dangerFill);
      drawWorldCircle(ctx, targetGround, 6, projectFn, pal.select, 2.0, [], null);
      if (shiftHeld || firstGroundQueueLen > 0) {
        drawReticleIndexCircle(ctx, targetScreen, firstGroundQueueLen + 1, firstOut ? '#f6b83d' : pal.danger);
      }
      if (firstDist !== null) {
        drawStrikeBadge(ctx, targetScreen, `${Math.round(firstDist)}m`, firstOut ? '#f6b83d' : pal.danger, '#eef4fa', 'rgba(13, 14, 19, 0.92)', 16);
      }
    }

    // 4.5 战争迷雾 2D 原生高清标绘（选中/预警无人机光电侦察圈 + 失联幽灵标，零受 3D 迷雾暗化污染）
    const fogSnap = adapter?.getFogSnapshot?.();
    if (fogSnap && fogSnap.enabled) {
      // 4.5.1 选中、悬停或 Bingo 返航预警中的 UAV / AC-130 光电覆盖圈
      const emitters = fogSnap.emitters || [];
      for (let i = 0; i < emitters.length; i += 1) {
        const em = emitters[i];
        if (!em || !em.active) continue;
        const isReconPod = em.role === 'uav' || em.shape === 'orbit_pod';
        if (!isReconPod) continue;
        const isSel = selectedSet.has(em.unitId);
        const isHov = hoveredId === em.unitId;
        if (!isSel && !isHov && !em.bingoWarning) continue;

        const cx = em.orbitCenterX !== null ? em.orbitCenterX : em.x;
        const cz = em.orbitCenterZ !== null ? em.orbitCenterZ : em.z;
        const cy = groundHeightFn(cx, cz) + 0.25;
        const isWarn = Boolean(em.bingoWarning);
        const strokeCol = isWarn
          ? 'rgba(246, 184, 61, 0.86)'
          : (isSel ? 'rgba(86, 230, 216, 0.82)' : 'rgba(86, 230, 216, 0.50)');
        const fillCol = isWarn
          ? 'rgba(246, 184, 61, 0.05)'
          : 'rgba(86, 230, 216, 0.04)';
        drawWorldCircle(
          ctx,
          { x: cx, y: cy, z: cz },
          em.liveRadiusM,
          projectFn,
          strokeCol,
          isSel || isWarn ? 1.6 : 1.15,
          isWarn ? [6, 4] : [10, 6],
          fillCol,
        );
        if (isSel || isWarn) {
          const badgePt = projectFn(cx, cy, cz - Math.min(em.liveRadiusM * 0.72, 220));
          if (badgePt.visible) {
            const altLbl = em.altitudeMode === 'low' ? '低空穿透' : '高空广域';
            const txt = isWarn
              ? `BINGO 返航 · 视野收拢 (${Math.round(em.liveRadiusM)}m)`
              : `光电侦察 · ${altLbl} ${Math.round(em.liveRadiusM)}m`;
            drawStrikeBadge(
              ctx,
              badgePt,
              txt,
              isWarn ? '#f6b83d' : '#56e6d8',
              '#eef4fa',
              'rgba(10, 14, 20, 0.88)',
              0,
            );
          }
        }
      }

      // 4.5.2 失联幽灵标（仅停留在最后发现坐标，不追踪隐藏移动）
      const ghosts = fogSnap.ghostContacts || [];
      for (let i = 0; i < ghosts.length; i += 1) {
        const g = ghosts[i];
        if (!g || !Number.isFinite(g.x) || !Number.isFinite(g.z)) continue;
        const gy = groundHeightFn(g.x, g.z) + 1.2;
        const scr = projectFn(g.x, gy, g.z);
        if (!scr.visible) continue;
        const fade = Math.max(0.28, 1 - (Number(g.ageSec) || 0) / 45);
        ctx.save();
        ctx.globalAlpha = fade;
        ctx.translate(scr.x, scr.y);
        ctx.strokeStyle = '#f08c4a';
        ctx.fillStyle = 'rgba(240, 140, 74, 0.16)';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([3, 2]);
        ctx.beginPath();
        ctx.moveTo(0, -8);
        ctx.lineTo(8, 0);
        ctx.lineTo(0, 8);
        ctx.lineTo(-8, 0);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = '700 10px Consolas, monospace';
        ctx.fillStyle = '#ffd5b3';
        ctx.textAlign = 'center';
        ctx.fillText(`? ${Math.round(g.ageSec || 0)}s`, 0, -12);
        ctx.restore();
      }
    }

    // 5. Stage 1A：L1/L2/L3 2D 高清战术图标分层与密集簇聚合绘制（L0 <=260m 零图标）
    let iconNodes = [];
    if (tier !== 'none' && tierItems.length > 0) {
      if (tier === 'compact') {
        iconNodes = tierItems.map(it => ({
          isCluster: false,
          id: it.id,
          channel: it.channel,
          iconKind: it.iconKind,
          dominantKind: it.iconKind,
          count: 1,
          unitIds: [it.id],
          screenPt: { x: it.x, y: it.y },
          selectedCount: it.selected ? 1 : 0,
          hoveredCount: it.hovered ? 1 : 0,
          hp: it.hp,
          item: it,
        }));
      } else {
        iconNodes = clusterScreenUnits(tierItems, { radiusPx: 18, minClusterSize: 3 });
      }
      // 选中/悬停节点后绘，确保永远浮于最顶层
      const orderedNodes = [...iconNodes].sort(
        (a, b) => ((a.selectedCount > 0 || a.hoveredCount > 0) ? 1 : 0) - ((b.selectedCount > 0 || b.hoveredCount > 0) ? 1 : 0),
      );
      for (const node of orderedNodes) {
        const ptrNearCluster = Boolean(
          node.isCluster &&
          pointerLocalPt &&
          Math.abs(pointerLocalPt.x - node.screenPt.x) <= 17 &&
          Math.abs(pointerLocalPt.y - node.screenPt.y) <= 13,
        );
        drawZoomTierUnitIcon(ctx, node, tier, pal, {
          showExpandHint: ptrNearCluster || node.hoveredCount > 0,
        });
      }
    }

    lastOverlayMetrics = {
      tier,
      span: Number(currentSpan.toFixed(1)),
      iconCount: iconNodes.length,
      clusterCount: iconNodes.filter(n => n.isCluster).length,
      fixedWingGroundZones,
      airTurnArcCount,
      hdProjected3DLineCount,
      orbitTransitLineCount,
      queuedStrikeMarkerCount,
      approachCurveCount,
      hasHighOrbitTrack: false,
      nodes: iconNodes.map(n => ({
        id: n.id,
        isCluster: n.isCluster,
        channel: n.channel,
        iconKind: n.iconKind,
        count: n.count,
        unitIds: n.unitIds,
        x: Math.round(n.screenPt.x * 10) / 10,
        y: Math.round(n.screenPt.y * 10) / 10,
      })),
    };
  }

  function dispose() {
    if (hostResizeObserver) {
      hostResizeObserver.disconnect();
      hostResizeObserver = null;
    }
    if (isHostDomElement && typeof window !== 'undefined') {
      window.removeEventListener('resize', onWindowResize);
    }
    canvas.remove();
    bannerEl.remove();
    wheelBackdrop.remove();
  }

  const getMetricsSnapshot = () => ({
    ...lastOverlayMetrics,
    nodes: lastOverlayMetrics.nodes.map(n => ({ ...n, unitIds: [...n.unitIds] })),
  });

  return Object.freeze({
    canvas,
    render,
    refreshPalette,
    syncSize: (w = null, h = null) => {
      markSizeDirty(w, h);
      syncCanvasSize(true);
    },
    setPreview,
    clearPreview,
    setTargetingPoint,
    setAirStrikePreview,
    getAirStrikePreview: () => airStrikePreview,
    setPointerLocal,
    setShiftHeld,
    updateBanner,
    openWheel,
    closeWheel,
    updateWheelPointer,
    handleWheelSpaceUp,
    refreshWheel,
    getWheelState,
    isWheelOpen: () => wheelState.open && !wheelBackdrop.hidden,
    hasLiveOverlay: () => Boolean(previewState || targetingWorldPt || airStrikePreview || wheelState.open),
    getMetrics: getMetricsSnapshot,
    getDebugState: getMetricsSnapshot,
    dispose,
  });
}
