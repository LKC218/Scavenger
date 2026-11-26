// ============================================================
// 三维战术标绘引擎 — 3D 空间线 UI（零 GC · 双深度通道 · ≤2 DrawCall）
// ------------------------------------------------------------
// 对标《Ebbtide》图 4 / 图 2 与《Broken Arrow》空间战术线：
//   1. 垂直高度 3D 虚线（真实飞行高度 pos.y -> 地面高度 groundY）
//   2. 机身 -> 目标红白交替 3D 空间斜向打击射线
//   3. 高空拉弧入航走廊 3D 虚线（经 entryPoint 切入点）
//   4. 瞄准态 3 态（射程内 / 超距分段 / 过近盲区）3D 斜向预瞄射线
//   5. 《Ebbtide》多点航弹地面串联虚线
// 隔离策略：ScavengerTacticalLines3DRoot 含 Mesh 子节点，
//   实时实况镜头.mjs 的 isolateUnitBranchInScene 会自动隐藏，不污染 Live PiP。
// ============================================================

const DEFAULT_MAX_QUADS = 2400;

const COLOR_ALT_GOLD = [0.95, 0.80, 0.38];
const COLOR_ALT_SOFT = [0.68, 0.72, 0.86];
const COLOR_STRIKE_RED = [0.96, 0.31, 0.27];
const COLOR_STRIKE_WHITE = [0.95, 0.97, 1.0];
const COLOR_CORRIDOR_AMBER = [0.98, 0.68, 0.25];
const COLOR_AMBER_OUT = [0.98, 0.72, 0.20];
const COLOR_TOO_CLOSE = [1.0, 0.36, 0.16];
const COLOR_BOMB_LINK = [0.96, 0.29, 0.26];

/**
 * 评估目标地面距离与当前飞机投影点之间的三态射程状态
 * @param {number} distM
 * @param {number} minRangeM
 * @param {number} maxRangeM
 * @returns {{ state: 'in_range'|'out_of_range'|'too_close', distM: number, minRangeM: number, maxRangeM: number, boundaryDistM: number }}
 */
export function evaluateRangeState(distM, minRangeM = 0, maxRangeM = 600) {
  const d = Number.isFinite(distM) ? Math.max(0, distM) : 0;
  const minR = Number.isFinite(minRangeM) ? Math.max(0, minRangeM) : 0;
  const maxR = Number.isFinite(maxRangeM) ? Math.max(minR + 10, maxRangeM) : 600;
  if (minR > 1 && d < minR - 0.5) {
    return { state: 'too_close', distM: d, minRangeM: minR, maxRangeM: maxR, boundaryDistM: minR };
  }
  if (d > maxR + 0.5) {
    return { state: 'out_of_range', distM: d, minRangeM: minR, maxRangeM: maxR, boundaryDistM: maxR };
  }
  return { state: 'in_range', distM: d, minRangeM: minR, maxRangeM: maxR, boundaryDistM: d };
}

/**
 * 采样飞机垂直高度 3D 垂线点对
 */
export function sampleAltitudeDropLine3D(pos, groundY = 0) {
  if (!pos || !Number.isFinite(pos.x) || !Number.isFinite(pos.z)) return [];
  const gy = Number.isFinite(groundY) ? groundY : 0;
  const topY = Number.isFinite(pos.y) ? Math.max(gy + 1.0, pos.y - 1.2) : gy + 185;
  return [
    { x: pos.x, y: topY, z: pos.z },
    { x: pos.x, y: gy + 0.45, z: pos.z },
  ];
}

/**
 * 二次贝塞尔平滑采样 3D 空中拉弧入航走廊
 */
export function sampleArcCorridor3D(planePos, entryPoint, targetStartPos, cruiseAltY = 185, segments = 14) {
  if (!planePos || !targetStartPos) return [];
  const count = Math.max(4, Math.min(32, segments | 0));
  const p0x = planePos.x;
  const p0y = Number.isFinite(planePos.y) ? planePos.y : cruiseAltY;
  const p0z = planePos.z;

  const p2x = targetStartPos.x;
  const p2y = (Number.isFinite(targetStartPos.y) ? targetStartPos.y : 0) + 6.0;
  const p2z = targetStartPos.z;

  const ctrl = entryPoint && Number.isFinite(entryPoint.x) && Number.isFinite(entryPoint.z)
    ? { x: entryPoint.x, y: Math.max(p0y, cruiseAltY), z: entryPoint.z }
    : { x: (p0x + p2x) * 0.5, y: Math.max(p0y, cruiseAltY) * 0.85 + p2y * 0.15, z: (p0z + p2z) * 0.5 };

  const out = [];
  for (let i = 0; i <= count; i++) {
    const t = i / count;
    const inv = 1 - t;
    out.push({
      x: inv * inv * p0x + 2 * inv * t * ctrl.x + t * t * p2x,
      y: inv * inv * p0y + 2 * inv * t * ctrl.y + t * t * p2y,
      z: inv * inv * p0z + 2 * inv * t * ctrl.z + t * t * p2z,
    });
  }
  return out;
}

/**
 * 生成 3D 线段虚线/交替双色段（纯数据，便于单测与 CPU 零 GC 写缓冲复用）
 */
export function buildDashedSegments3D(p0, p1, dashLenM = 8, gapLenM = 3, phaseM = 0, alternateColors = false) {
  const dx = p1.x - p0.x;
  const dy = p1.y - p0.y;
  const dz = p1.z - p0.z;
  const totalLen = Math.hypot(dx, dy, dz);
  if (totalLen < 0.2) return [];

  const dash = Math.max(1.0, dashLenM);
  const gap = Math.max(0.4, gapLenM);
  const period = dash + gap;
  const invLen = 1 / totalLen;
  const ux = dx * invLen;
  const uy = dy * invLen;
  const uz = dz * invLen;

  let offset = -(phaseM % period);
  if (offset > 0) offset -= period;

  const segments = [];
  let segIndex = 0;
  while (offset < totalLen) {
    const s = Math.max(0, offset);
    const e = Math.min(totalLen, offset + dash);
    if (e - s > 0.35) {
      segments.push({
        a: { x: p0.x + ux * s, y: p0.y + uy * s, z: p0.z + uz * s },
        b: { x: p0.x + ux * e, y: p0.y + uy * e, z: p0.z + uz * e },
        colorSlot: alternateColors ? (segIndex % 2) : 0,
      });
    }
    offset += period;
    segIndex++;
    if (segments.length > 180) break;
  }
  return segments;
}

export class TacticalLines3DEngine {
  /**
   * @param {{ THREE: any, scene: any, maxQuads?: number }} opts
   */
  constructor({ THREE, scene, maxQuads = DEFAULT_MAX_QUADS, enabled = false }) {
    if (!THREE || !scene) {
      throw new Error('TacticalLines3DEngine requires { THREE, scene }');
    }
    this.THREE = THREE;
    this.scene = scene;
    this.enabled = Boolean(enabled);
    this.maxQuads = Math.max(256, maxQuads | 0);
    this._quadCount = 0;
    this._phaseSec = 0;

    // 预分配静态 TypedArray，保证渲染主循环零 GC
    const maxVerts = this.maxQuads * 4;
    const maxIndices = this.maxQuads * 6;
    this._positions = new Float32Array(maxVerts * 3);
    this._colors = new Float32Array(maxVerts * 3);
    const indices = new Uint16Array(maxIndices);
    for (let i = 0; i < this.maxQuads; i++) {
      const vBase = i * 4;
      const iBase = i * 6;
      indices[iBase + 0] = vBase + 0;
      indices[iBase + 1] = vBase + 1;
      indices[iBase + 2] = vBase + 2;
      indices[iBase + 3] = vBase + 0;
      indices[iBase + 4] = vBase + 2;
      indices[iBase + 5] = vBase + 3;
    }

    this.geometry = new THREE.BufferGeometry();
    this._posAttr = new THREE.BufferAttribute(this._positions, 3);
    this._colAttr = new THREE.BufferAttribute(this._colors, 3);
    this._posAttr.setUsage(THREE.DynamicDrawUsage);
    this._colAttr.setUsage(THREE.DynamicDrawUsage);
    this.geometry.setAttribute('position', this._posAttr);
    this.geometry.setAttribute('color', this._colAttr);
    this.geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    this.geometry.setDrawRange(0, 0);

    // 通道 1：前景锐利线（LessEqualDepth）
    this._frontMaterial = new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.96,
      depthTest: true,
      depthFunc: THREE.LessEqualDepth,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    });
    // 通道 2：楼宇/山体遮挡半透幽灵线（GreaterDepth）
    this._occludedMaterial = new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.24,
      depthTest: true,
      depthFunc: THREE.GreaterDepth,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    });

    this.frontMesh = new THREE.Mesh(this.geometry, this._frontMaterial);
    this.frontMesh.name = 'TacticalLines3DFront';
    this.frontMesh.renderOrder = 18;
    this.frontMesh.frustumCulled = false;

    this.occludedMesh = new THREE.Mesh(this.geometry, this._occludedMaterial);
    this.occludedMesh.name = 'TacticalLines3DOccluded';
    this.occludedMesh.renderOrder = 17;
    this.occludedMesh.frustumCulled = false;

    // 顶层 Group 含有 Mesh 子节点，实时实况镜头会整组隐藏它，天然隔离 Live PiP
    this.root = new THREE.Group();
    this.root.name = 'ScavengerTacticalLines3DRoot';
    this.root.visible = false;
    this.root.add(this.occludedMesh);
    this.root.add(this.frontMesh);
    this.scene.add(this.root);

    // 复用向量避免临时对象分配
    this._camFwd = new THREE.Vector3(0, -1, 0);
    this._camRight = new THREE.Vector3(1, 0, 0);
    this._pxToWorld = 0.25;
  }

  _updateCameraBasis(camera, viewportHeight = 680) {
    if (!camera) return;
    camera.getWorldDirection(this._camFwd).normalize();
    this._camRight.set(1, 0, 0);
    if (camera.matrixWorld) {
      this._camRight.setFromMatrixColumn(camera.matrixWorld, 0).normalize();
    }
    let span = 240;
    if (camera.isOrthographicCamera) {
      span = Math.max(40, Math.abs((camera.top || 120) - (camera.bottom || -120)) / Math.max(0.05, camera.zoom || 1));
    } else if (camera.position) {
      span = Math.max(60, camera.position.y * 0.9);
    }
    this._pxToWorld = span / Math.max(480, viewportHeight || 680);
  }

  _pushQuad(ax, ay, az, bx, by, bz, widthPx, colorRgb) {
    if (this._quadCount >= this.maxQuads) return;
    const dx = bx - ax;
    const dy = by - ay;
    const dz = bz - az;
    const len = Math.hypot(dx, dy, dz);
    if (len < 0.05) return;

    // side = normalize(cross(segDir, camFwd))
    const fx = this._camFwd.x;
    const fy = this._camFwd.y;
    const fz = this._camFwd.z;
    let sx = dy * fz - dz * fy;
    let sy = dz * fx - dx * fz;
    let sz = dx * fy - dy * fx;
    let sLen = Math.hypot(sx, sy, sz);
    if (sLen < 1e-4) {
      sx = this._camRight.x;
      sy = this._camRight.y;
      sz = this._camRight.z;
      sLen = Math.hypot(sx, sy, sz) || 1;
    }
    const halfW = Math.max(0.18, widthPx * 0.5 * this._pxToWorld) / sLen;
    sx *= halfW;
    sy *= halfW;
    sz *= halfW;

    const vBase = this._quadCount * 12;
    const pos = this._positions;
    const col = this._colors;

    pos[vBase + 0] = ax - sx; pos[vBase + 1] = ay - sy; pos[vBase + 2] = az - sz;
    pos[vBase + 3] = ax + sx; pos[vBase + 4] = ay + sy; pos[vBase + 5] = az + sz;
    pos[vBase + 6] = bx + sx; pos[vBase + 7] = by + sy; pos[vBase + 8] = bz + sz;
    pos[vBase + 9] = bx - sx; pos[vBase + 10] = by - sy; pos[vBase + 11] = bz - sz;

    const r = colorRgb[0];
    const g = colorRgb[1];
    const b = colorRgb[2];
    for (let k = 0; k < 12; k += 3) {
      col[vBase + k] = r;
      col[vBase + k + 1] = g;
      col[vBase + k + 2] = b;
    }
    this._quadCount++;
  }

  _emitDashed3D(p0, p1, {
    dashLenM = 8.0,
    gapLenM = 3.2,
    phaseM = 0,
    widthPx = 2.6,
    colorA = COLOR_STRIKE_RED,
    colorB = null,
  } = {}) {
    const dx = p1.x - p0.x;
    const dy = p1.y - p0.y;
    const dz = p1.z - p0.z;
    const totalLen = Math.hypot(dx, dy, dz);
    if (totalLen < 0.25) return;

    const dash = Math.max(0.8, dashLenM);
    const gap = Math.max(0.35, gapLenM);
    const period = dash + gap;
    const invLen = 1 / totalLen;
    const ux = dx * invLen;
    const uy = dy * invLen;
    const uz = dz * invLen;

    let offset = -(phaseM % period);
    if (offset > 0) offset -= period;

    let segIndex = 0;
    while (offset < totalLen && this._quadCount < this.maxQuads) {
      const s = Math.max(0, offset);
      const e = Math.min(totalLen, offset + dash);
      if (e - s > 0.3) {
        const c = colorB && (segIndex & 1) === 1 ? colorB : colorA;
        this._pushQuad(
          p0.x + ux * s, p0.y + uy * s, p0.z + uz * s,
          p0.x + ux * e, p0.y + uy * e, p0.z + uz * e,
          widthPx,
          c,
        );
      }
      offset += period;
      segIndex++;
    }
  }

  _emitSolid3D(p0, p1, widthPx, colorRgb) {
    this._pushQuad(p0.x, p0.y, p0.z, p1.x, p1.y, p1.z, widthPx, colorRgb);
  }

  _emitPolylineDashed3D(points, opts) {
    if (!points || points.length < 2) return;
    let accumPhase = opts.phaseM || 0;
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      this._emitDashed3D(a, b, { ...opts, phaseM: accumPhase });
      accumPhase -= Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
    }
  }

  _emitTargetingPreview({
    planePos,
    preview,
    queryGroundHeight,
    flowPhase,
  }) {
    const spec = preview?.spec;
    if (!planePos || !spec) return;
    const drops = Array.isArray(preview.dropPoints) ? preview.dropPoints : [];
    const anchor = preview.startPos || preview.hoverPos || drops[0];
    if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.z)) return;

    const anchorGroundY = queryGroundHeight ? queryGroundHeight(anchor.x, anchor.z) : (anchor.y || 0);
    const target3D = { x: anchor.x, y: anchorGroundY + 1.0, z: anchor.z };
    const dx = anchor.x - planePos.x;
    const dz = anchor.z - planePos.z;
    const groundDist = Math.hypot(dx, dz);
    const rangeEval = evaluateRangeState(groundDist, spec.minRangeM, spec.maxRangeM);

    if (rangeEval.state === 'out_of_range') {
      const tBound = rangeEval.maxRangeM / Math.max(1e-3, groundDist);
      const bx = planePos.x + dx * tBound;
      const bz = planePos.z + dz * tBound;
      const bGroundY = queryGroundHeight ? queryGroundHeight(bx, bz) : anchorGroundY;
      const boundary3D = {
        x: bx,
        y: planePos.y + (bGroundY + 1.0 - planePos.y) * tBound,
        z: bz,
      };
      this._emitDashed3D(planePos, boundary3D, {
        dashLenM: 9.0,
        gapLenM: 2.8,
        phaseM: flowPhase,
        widthPx: 2.8,
        colorA: COLOR_AMBER_OUT,
        colorB: COLOR_STRIKE_WHITE,
      });
      this._emitDashed3D(boundary3D, target3D, {
        dashLenM: 7.0,
        gapLenM: 2.5,
        phaseM: flowPhase,
        widthPx: 2.6,
        colorA: COLOR_STRIKE_RED,
        colorB: COLOR_STRIKE_WHITE,
      });
    } else if (rangeEval.state === 'too_close') {
      this._emitDashed3D(planePos, target3D, {
        dashLenM: 5.0,
        gapLenM: 4.5,
        phaseM: flowPhase * 1.4,
        widthPx: 2.8,
        colorA: COLOR_TOO_CLOSE,
        colorB: COLOR_STRIKE_WHITE,
      });
    } else {
      // 射程内：图 4 红白高对比斜向空间线
      this._emitDashed3D(planePos, target3D, {
        dashLenM: 7.5,
        gapLenM: 2.2,
        phaseM: flowPhase,
        widthPx: 3.0,
        colorA: COLOR_STRIKE_RED,
        colorB: COLOR_STRIKE_WHITE,
      });
    }

    // 拖线模式预瞄轴线（startPos -> endPos / hoverPos）
    if (spec.inputMode === 'drag_line' && preview.startPos && (preview.endPos || preview.hoverPos)) {
      const endPt = preview.endPos || preview.hoverPos;
      const gy0 = queryGroundHeight ? queryGroundHeight(preview.startPos.x, preview.startPos.z) : 0;
      const gy1 = queryGroundHeight ? queryGroundHeight(endPt.x, endPt.z) : 0;
      this._emitDashed3D(
        { x: preview.startPos.x, y: gy0 + 1.2, z: preview.startPos.z },
        { x: endPt.x, y: gy1 + 1.2, z: endPt.z },
        {
          dashLenM: 6.5,
          gapLenM: 2.8,
          phaseM: flowPhase,
          widthPx: 3.0,
          colorA: COLOR_CORRIDOR_AMBER,
          colorB: COLOR_STRIKE_RED,
        },
      );
    }

    // 《Ebbtide》航弹连点序列地面串联虚线（含连向当前鼠标悬停点预览）
    if (spec.inputMode === 'multi_point' && drops.length > 0) {
      const linkPts = [];
      for (const p of drops) {
        const gy = queryGroundHeight ? queryGroundHeight(p.x, p.z) : (p.y || 0);
        linkPts.push({ x: p.x, y: gy + 0.85, z: p.z });
      }
      if (preview.hoverPos && drops.length < Math.max(1, spec.ammo || 4)) {
        const hy = queryGroundHeight ? queryGroundHeight(preview.hoverPos.x, preview.hoverPos.z) : 0;
        linkPts.push({ x: preview.hoverPos.x, y: hy + 0.85, z: preview.hoverPos.z });
      }
      if (linkPts.length >= 2) {
        this._emitPolylineDashed3D(linkPts, {
          dashLenM: 4.8,
          gapLenM: 2.6,
          phaseM: flowPhase * 0.65,
          widthPx: 2.3,
          colorA: COLOR_BOMB_LINK,
          colorB: COLOR_STRIKE_WHITE,
        });
      }
    }
  }

  /**
   * 每帧更新全部 3D 战术标绘线条
   */
  updateFrame({
    dt = 0.016,
    camera,
    viewportHeight = 680,
    units = [],
    selectedIds = null,
    hoveredId = null,
    interactionMode = 'play',
    airStrikePreview = null,
    queryGroundHeight = null,
  } = {}) {
    this._quadCount = 0;
    if (!this.enabled) {
      this.root.visible = false;
      this.geometry.setDrawRange(0, 0);
      return;
    }
    this._phaseSec += Math.min(0.08, Math.max(0, Number(dt) || 0));
    this._updateCameraBasis(camera, viewportHeight);

    const selSet = selectedIds instanceof Set
      ? selectedIds
      : new Set(Array.isArray(selectedIds) ? selectedIds : []);
    const flowPhase = this._phaseSec * 18.0;
    const altPhase = this._phaseSec * 4.0;
    let primarySelectedPlanePos = null;

    for (const u of units) {
      if (!u || u.role !== 'fixed_wing' || !u.node?.position) continue;
      if (u.fixedWingController?.getUnitState) {
        const st = u.fixedWingController.getUnitState(u);
        if (st?.crashed) continue;
      }
      const isSel = selSet.has(u.id);
      const isHov = hoveredId && u.id === hoveredId;
      const pos = u.node.position;
      if (isSel && !primarySelectedPlanePos) {
        primarySelectedPlanePos = { x: pos.x, y: pos.y, z: pos.z };
      }

      const groundY = queryGroundHeight ? queryGroundHeight(pos.x, pos.z) : 0;

      // 1. 垂直高度 3D 虚线（选中或悬停显示）
      if (isSel || isHov) {
        const altPair = sampleAltitudeDropLine3D(pos, groundY);
        if (altPair.length === 2) {
          this._emitDashed3D(altPair[0], altPair[1], {
            dashLenM: 5.5,
            gapLenM: 3.5,
            phaseM: altPhase,
            widthPx: 2.1,
            colorA: COLOR_ALT_GOLD,
            colorB: COLOR_ALT_SOFT,
          });
        }
      }

      // 2. 执行中的精准打击：图 4 斜向红白射线 + 空中拉弧走廊 + 航弹连点线
      const predicted = u.fixedWingController?.getPredictedFlightPath?.(u);
      if (predicted && predicted.active && predicted.targetPos && (isSel || isHov || predicted.phase === 'run')) {
        const tPos = predicted.targetPos;
        const targetGroundY = queryGroundHeight ? queryGroundHeight(tPos.x, tPos.z) : (tPos.y || 0);
        const target3D = { x: tPos.x, y: targetGroundY + 1.0, z: tPos.z };

        // 机身 -> 目标 3D 斜向空间线（红白交替流动）
        this._emitDashed3D(pos, target3D, {
          dashLenM: 8.2,
          gapLenM: 2.4,
          phaseM: flowPhase,
          widthPx: isSel ? 3.1 : 2.3,
          colorA: COLOR_STRIKE_RED,
          colorB: COLOR_STRIKE_WHITE,
        });

        // 空中拉弧入航走廊
        if (predicted.startPos) {
          const sy = queryGroundHeight ? queryGroundHeight(predicted.startPos.x, predicted.startPos.z) : 0;
          const arcPts = sampleArcCorridor3D(
            pos,
            predicted.phase === 'transit' ? predicted.entryPoint : null,
            { x: predicted.startPos.x, y: sy, z: predicted.startPos.z },
            predicted.cruiseAltY || pos.y || 185,
            14,
          );
          if (arcPts.length >= 2) {
            this._emitPolylineDashed3D(arcPts, {
              dashLenM: 9.0,
              gapLenM: 4.5,
              phaseM: flowPhase * 0.9,
              widthPx: isSel ? 2.4 : 1.8,
              colorA: COLOR_CORRIDOR_AMBER,
              colorB: COLOR_ALT_GOLD,
            });
          }
        }

        // 拖线轴向打击走廊地面上方引导线（start -> end）
        if (predicted.startPos && predicted.endPos) {
          const dAxis = Math.hypot(
            predicted.endPos.x - predicted.startPos.x,
            predicted.endPos.z - predicted.startPos.z,
          );
          if (dAxis > 8) {
            const gyA = queryGroundHeight ? queryGroundHeight(predicted.startPos.x, predicted.startPos.z) : 0;
            const gyB = queryGroundHeight ? queryGroundHeight(predicted.endPos.x, predicted.endPos.z) : 0;
            this._emitDashed3D(
              { x: predicted.startPos.x, y: gyA + 1.1, z: predicted.startPos.z },
              { x: predicted.endPos.x, y: gyB + 1.1, z: predicted.endPos.z },
              {
                dashLenM: 7.0,
                gapLenM: 2.5,
                phaseM: flowPhase,
                widthPx: 2.9,
                colorA: COLOR_STRIKE_RED,
                colorB: COLOR_CORRIDOR_AMBER,
              },
            );
          }
        }

        // 《Ebbtide》连点航弹多点地面串联虚线
        if (Array.isArray(predicted.dropPoints) && predicted.dropPoints.length > 1) {
          const drop3Ds = predicted.dropPoints.map((pt) => {
            const gy = queryGroundHeight ? queryGroundHeight(pt.x, pt.z) : (pt.y || 0);
            return { x: pt.x, y: gy + 0.9, z: pt.z };
          });
          this._emitPolylineDashed3D(drop3Ds, {
            dashLenM: 4.8,
            gapLenM: 2.6,
            phaseM: flowPhase * 0.7,
            widthPx: 2.4,
            colorA: COLOR_BOMB_LINK,
            colorB: COLOR_STRIKE_WHITE,
          });
        }
      }
    }

    // 3. TARGETING 瞄准态 3D 射程预览斜线与《Ebbtide》待投点虚线
    if (String(interactionMode || '').toLowerCase() === 'targeting' && airStrikePreview?.spec && primarySelectedPlanePos) {
      this._emitTargetingPreview({
        planePos: primarySelectedPlanePos,
        preview: airStrikePreview,
        queryGroundHeight,
        flowPhase,
      });
    }

    if (this._quadCount > 0) {
      this.root.visible = true;
      this.geometry.setDrawRange(0, this._quadCount * 6);
      this._posAttr.needsUpdate = true;
      this._colAttr.needsUpdate = true;
    } else {
      this.root.visible = false;
      this.geometry.setDrawRange(0, 0);
    }
  }

  getStats() {
    const active = this.enabled && this.root.visible && this._quadCount > 0;
    return {
      activeQuads: this._quadCount,
      activeVertices: this._quadCount * 4,
      drawCalls: active ? 2 : 0,
      visible: active,
      renderPath: this.enabled ? '3d-quad-ribbon' : 'hd-2d-projected-3d',
    };
  }

  dispose() {
    if (this.root && this.scene) {
      this.scene.remove(this.root);
    }
    this.geometry?.dispose?.();
    this._frontMaterial?.dispose?.();
    this._occludedMaterial?.dispose?.();
  }
}
