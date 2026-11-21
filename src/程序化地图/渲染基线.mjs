/**
 * 项目唯一风格化渲染基线配置源（680p + 复古微缩 Stylized + 16:9 / 21:9 Hor+ 双画幅）。
 * 适用范围：程序化大地图及今后全部新开发、测试与实战 3D 场景模块（不回溯修改历史已冻结旧端口）。
 * 对应规范文档：docs/渲染风格与分辨率基线规范.md（由 scripts/检查项目结构.py 自动校验同步性）。
 */

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
    }),
    '21:9': Object.freeze({
      key: '21:9',
      width: 1588,
      height: 680,
      ratio: 2560 / 1080,
      label: '21:9 (1588×680)',
    }),
  }),
  webgl: Object.freeze({
    antialias: true,
    pixelRatio: 1,
    toneMapping: 'ACESFilmic',
    exposure: 1.18,
  }),
  canvasStyle: Object.freeze({
    imageRendering: 'pixelated',
    filter: 'contrast(1.08) saturate(1.10) sepia(0.06)',
  }),
  uiLayerIsolation: true,
});

/**
 * 规范化并返回指定画幅比例（仅允许 '16:9' 或 '21:9'）的 680p 精确输出规格。
 */
export function getStandardAspectSpec(aspectKey) {
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
export function applyStylized680pCanvas(renderer, aspectKey = RENDER_STYLE_BASELINE.defaultAspect) {
  const spec = getStandardAspectSpec(aspectKey);
  renderer.setPixelRatio(RENDER_STYLE_BASELINE.webgl.pixelRatio);
  renderer.setSize(spec.width, spec.height, false);
  const cv = renderer.domElement;
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
