import * as THREE from 'three';

/**
 * 方案 1 唯一 LOD 过渡动画参数配置源（统一驱动建筑、树木、林冠、草团、地形与高空过渡轻雾）。
 * 说明：所有远/中/近景临界半径与时间缓动阻尼系数均集中在此处，严禁在渲染器或着色器内分散硬编码冲突阈值。
 */
export const LOD_TRANSITION_CONFIG = Object.freeze({
  building: Object.freeze({
    preloadSpan: 840.0,
    unloadSpan: 920.0,
    macroHideSpan: 340.0,
    morphDist: Object.freeze([520.0, 720.0]),
    morphSpan: Object.freeze([520.0, 740.0]),
    radiusMin: 300.0,
    radiusMax: 760.0,
    handoffT: 0.96,
  }),
  foliage: Object.freeze({
    nearDist: Object.freeze([300.0, 390.0]),
    nearSpan: Object.freeze([310.0, 410.0]),
    midDist: Object.freeze([490.0, 650.0]),
    midSpan: Object.freeze([500.0, 660.0]),
    canopyDist: Object.freeze([430.0, 560.0]),
    canopySpan: Object.freeze([440.0, 580.0]),
    nearMaxSpan: 440.0,
    midMaxSpan: 720.0,
    canopyMinSpan: 270.0,
    handoffT: 0.95,
  }),
  grass: Object.freeze({
    fixedWindowSize: 162.0,
    fadeSpan: Object.freeze([120.0, 175.0]),
    hardCullSpan: 185.0,
  }),
  terrain: Object.freeze({
    detailOnSpan: 1530.0,
    detailOffSpan: 2070.0,
  }),
  haze: Object.freeze({
    bellSpan: Object.freeze([430.0, 820.0]),
    maxOpacity: 0.11,
  }),
  timing: Object.freeze({
    spanLambda: 13.5,
    readyLambda: 11.5,
  }),
});

function smoothstep(edge0, edge1, x) {
  const t = Math.max(0.0, Math.min(1.0, (x - edge0) / Math.max(1e-5, edge1 - edge0)));
  return t * t * (3.0 - 2.0 * t);
}

/**
 * 创建地形/植被/建筑共享的连续相机与时间缓动 Uniform 字典。
 */
export function createSharedTransitionUniforms() {
  return {
    uCameraCenter: {value: new THREE.Vector3(0, 0, 85)},
    uCameraSpan: {value: 140.0},
    uSmoothSpan: {value: 140.0},
    uBuildingNearReady: {value: 1.0},
    uHazeStrength: {value: 0.0},
    uWindTime: {value: 0.0},
    uDitherEnabled: {value: 1.0},
  };
}

/**
 * 唯一 LOD 过渡动画时间缓动驱动器：
 * 1) 每帧零延迟同步真实相机中心与跨度，消灭 45ms 流式节流带来的着色器阶梯跳变；
 * 2) 维护帧率无关时间缓动 uSmoothSpan 与原型就绪锁 uBuildingNearReady（0 建筑消失空窗）；
 * 3) 在 430–820m 过渡带施加极轻的高空战术微雾，柔化远近几何替换对比度（仅作用于 3D 层）。
 */
export class LODTransitionPipeline {
  constructor({sharedUniforms, terrainUniforms = null, hazeElement = null} = {}) {
    this.sharedUniforms = sharedUniforms || createSharedTransitionUniforms();
    this.terrainUniforms = terrainUniforms;
    this.hazeElement = hazeElement;
  }

  /**
   * 每帧或瞬时切镜调用，更新全部相机、缓动跨度、就绪锁及高空微雾参数。
   */
  syncFrame(center, realSpan, dtSec = 0.016, opts = {}) {
    const {immediate = false, buildingReady = true} = opts;
    const u = this.sharedUniforms;
    const safeSpan = Math.max(65.0, Number.isFinite(realSpan) ? realSpan : 140.0);

    u.uCameraCenter.value.set(center.x, center.y ?? 0, center.z);
    u.uCameraSpan.value = safeSpan;

    if (this.terrainUniforms) {
      if (this.terrainUniforms.uCameraCenter) {
        this.terrainUniforms.uCameraCenter.value.set(center.x, center.y ?? 0, center.z);
      }
      if (this.terrainUniforms.uCameraSpan) {
        this.terrainUniforms.uCameraSpan.value = safeSpan;
      }
    }

    const clampDt = Math.max(0.0, Math.min(0.08, dtSec));
    const readyTarget = buildingReady ? 1.0 : 0.0;

    if (immediate || clampDt <= 1e-5) {
      u.uSmoothSpan.value = safeSpan;
      u.uBuildingNearReady.value = readyTarget;
    } else {
      const spanAlpha = 1.0 - Math.exp(-LOD_TRANSITION_CONFIG.timing.spanLambda * clampDt);
      const readyAlpha = 1.0 - Math.exp(-LOD_TRANSITION_CONFIG.timing.readyLambda * clampDt);
      u.uSmoothSpan.value += (safeSpan - u.uSmoothSpan.value) * spanAlpha;
      u.uBuildingNearReady.value += (readyTarget - u.uBuildingNearReady.value) * readyAlpha;
      if (Math.abs(safeSpan - u.uSmoothSpan.value) < 0.05) u.uSmoothSpan.value = safeSpan;
      if (Math.abs(readyTarget - u.uBuildingNearReady.value) < 0.002) u.uBuildingNearReady.value = readyTarget;
    }

    const [hazeStart, hazeEnd] = LOD_TRANSITION_CONFIG.haze.bellSpan;
    const bellT = smoothstep(hazeStart, hazeEnd, u.uSmoothSpan.value);
    const hazeStrength = Math.sin(bellT * Math.PI) * LOD_TRANSITION_CONFIG.haze.maxOpacity;
    u.uHazeStrength.value = hazeStrength;

    if (this.hazeElement) {
      this.hazeElement.style.opacity = hazeStrength > 0.002 ? hazeStrength.toFixed(4) : '0';
    }
    return {
      smoothSpan: u.uSmoothSpan.value,
      buildingNearReady: u.uBuildingNearReady.value,
      hazeStrength,
    };
  }
}
