/**
 * 项目唯一风格化渲染基线配置源（680p + 复古微缩 Stylized + 16:9 / 21:9 Hor+ 双画幅）。
 * 适用范围：程序化大地图及今后全部新开发、测试与实战 3D 场景模块（不回溯修改历史已冻结旧端口）。
 * 对应规范文档：docs/渲染风格与分辨率基线规范.md（由 scripts/检查项目结构.py 自动校验同步性）。
 */

export const ASPECT_CYCLE = Object.freeze(['16:9', '21:9', 'full']);

export const RENDER_STYLE_BASELINE = Object.freeze({
  id: 'stylized-680p-v1',
  name: '680p 复古微缩 Stylized',
  verticalHeight: 680,
  defaultAspect: '16:9',
  aspects: Object.freeze({
    '16:9': Object.freeze({
      key: '16:9',
      width: 1208,
      height: 680,
      ratio: 16 / 9,
      label: '16:9 (1208×680)',
      shortLabel: '16:9',
    }),
    '21:9': Object.freeze({
      key: '21:9',
      width: 1588,
      height: 680,
      ratio: 2560 / 1080,
      label: '21:9 (1588×680)',
      shortLabel: '21:9',
    }),
    full: Object.freeze({
      key: 'full',
      width: 1208,
      height: 680,
      ratio: 16 / 9,
      label: '全屏自适应 (680p)',
      shortLabel: '全屏',
    }),
  }),
  webgl: Object.freeze({
    antialias: true,
    pixelRatio: 1,
    toneMapping: 'ACESFilmic',
    exposure: 1.18,
  }),
  postprocess: Object.freeze({
    bloomEnabledByDefault: true,
    bloomThreshold: 1.35, // 高于普通白天地形/机棚线性亮度，避免非发光体误泛光
    bloomStrength: 0.46,
    bloomRadius: 0.36,
    msaaSamples: 4,
    tiltShiftDof: Object.freeze({
      kind: 'far-depth-3d', // 仅远景 3D 深度自然景深（前景与高空固定翼 CoC=0 绝对锐利，同栋建筑零腰斩）
      defaultMode: 'soft', // 'off' | 'soft' | 'standard'
      modes: Object.freeze({
        off: Object.freeze({
          label: '关闭',
          maxBlurPx: 0,
          blurSpread: 0,
          farStartRatio: 1.0,
          farEndRatio: 1.0,
          farHazeStrength: 0.0,
        }),
        soft: Object.freeze({
          label: '柔和',
          maxBlurPx: 3.6,
          blurSpread: 0.58,
          farStartRatio: 0.22,
          farEndRatio: 1.08,
          farHazeStrength: 0.035,
        }),
        standard: Object.freeze({
          label: '标准',
          maxBlurPx: 6.2,
          blurSpread: 0.98,
          farStartRatio: 0.14,
          farEndRatio: 0.88,
          farHazeStrength: 0.055,
        }),
      }),
      spanFadeStartM: 360,  // <= 360m 近中景全效自然远景景深
      spanFadeEndM: 1200,   // >= 1200m 全图战略视角平滑降至 0% 并自动旁路
    }),
  }),
  canvasStyle: Object.freeze({
    imageRendering: 'pixelated',
    filter: 'contrast(1.08) saturate(1.10) sepia(0.06)',
  }),
  uiLayerIsolation: true,
});

/**
 * 按 16:9 -> 21:9 -> full -> 16:9 顺序返回下一档画幅键。
 */
export function nextAspectKey(currentKey) {
  const idx = ASPECT_CYCLE.indexOf(currentKey);
  if (idx === -1) return '21:9';
  return ASPECT_CYCLE[(idx + 1) % ASPECT_CYCLE.length];
}

/**
 * 规范化并返回指定画幅比例（'16:9' | '21:9' | 'full'）的 680p 精确输出规格。
 * 当 aspectKey === 'full' 时，锁定 height = 680，按当前视口宽高比自适应计算偶数宽度并铺满视口。
 */
export function getStandardAspectSpec(aspectKey, viewportW = null, viewportH = null) {
  if (aspectKey === 'full') {
    const vw = Number.isFinite(viewportW) && viewportW > 0
      ? viewportW
      : (typeof window !== 'undefined' && window.innerWidth ? window.innerWidth : 1208);
    const vh = Number.isFinite(viewportH) && viewportH > 0
      ? viewportH
      : (typeof window !== 'undefined' && window.innerHeight ? window.innerHeight : 680);
    const ratio = Math.max(1.2, Math.min(3.5, vw / Math.max(1, vh)));
    const width = Math.max(816, Math.round((680 * ratio) / 2) * 2);
    return Object.freeze({
      key: 'full',
      width,
      height: 680,
      ratio: width / 680,
      label: `全屏 (${width}×680)`,
      shortLabel: '全屏',
    });
  }
  return aspectKey === '21:9'
    ? RENDER_STYLE_BASELINE.aspects['21:9']
    : RENDER_STYLE_BASELINE.aspects['16:9'];
}

/**
 * 将唯一 680p 复古微缩 Stylized 样式严格绑定至 3D WebGL <canvas>：
 * - 开启硬件 MSAA (antialias: true) 抑制镜头平移闪烁，锁定 pixelRatio = 1；
 * - 3D 画布应用 pixelated 最近邻上采样与复古微缩调色滤镜；
 * - 上层 DOM HUD、按钮与载具名牌保持 100% 显示器原生超清且不叠加复古滤镜。
 */
export function applyStylized680pCanvas(
  renderer,
  aspectKey = RENDER_STYLE_BASELINE.defaultAspect,
  viewportW = null,
  viewportH = null,
) {
  const spec = getStandardAspectSpec(aspectKey, viewportW, viewportH);
  const targetPr = RENDER_STYLE_BASELINE.webgl.pixelRatio;
  if (renderer.getPixelRatio() !== targetPr) {
    renderer.setPixelRatio(targetPr);
  }
  const cv = renderer.domElement;
  const expectedW = Math.floor(spec.width * targetPr);
  const expectedH = Math.floor(spec.height * targetPr);
  if (cv.width !== expectedW || cv.height !== expectedH) {
    renderer.setSize(spec.width, spec.height, false);
  }
  cv.style.imageRendering = RENDER_STYLE_BASELINE.canvasStyle.imageRendering;
  cv.style.filter = RENDER_STYLE_BASELINE.canvasStyle.filter;
  cv.dataset.renderBaseline = RENDER_STYLE_BASELINE.id;
  cv.dataset.aspect = spec.key;
  return spec;
}

/**
 * 按 Hor+ 垂直一致原则计算正交相机视锥半宽与半高：
 * - 无论 16:9 还是 21:9，垂直视野高度均以 16:9 为统一基准锁定；
 * - 21:9 向左右自然拓展约 31% 横向战场视野，上下不裁切、单位屏幕像素高度完全一致。
 */
export function computeHorPlusCameraFrustum(baseSpan16x9, aspectRatio) {
  const halfH = (baseSpan16x9 * 0.5) / (16 / 9);
  const halfW = halfH * aspectRatio;
  return {halfW, halfH};
}

/**
 * 在浏览器视口内计算精确保持 16:9 或 21:9 比例的居中显示区域尺寸（不拉伸变形）。
 */
export function layoutAspectFrameBox(aspectRatio, viewportW, viewportH) {
  const availW = Math.max(320, viewportW || 1280);
  const availH = Math.max(200, viewportH || 720);
  let width = availW;
  let height = width / aspectRatio;
  if (height > availH) {
    height = availH;
    width = height * aspectRatio;
  }
  return {
    width: Math.round(width),
    height: Math.round(height),
  };
}
