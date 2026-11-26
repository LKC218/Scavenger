/**
 * Scavenger 场景内实时实况画中画相机（Live PiP Cam · 纯载具离屏隔离极速版）：
 * - 在 9km 主战场上直接对主场景内真实载具模型实时跟拍（零模型克隆、零额外 WebGLContext）；
 * - 采用独立离屏 `WebGLRenderTarget`（HDR 渲染目标 + ACES Filmic/sRGB Y 翻转后处理目标）+ `readRenderTargetPixels`，
 *   渲染小窗前原子级隔离场景环境（剔除 9km 地形/道路/植被/草丛/建筑/水面/其他单位与 2048px 阴影贴图重算），
 *   严禁清屏/裁剪或回读主屏默认帧缓冲 `renderer.domElement`，彻底杜绝 `preserveDrawingBuffer` 交替清空导致的闪黑；
 * - 采用球面短弧方位角阻尼 + 恒距锁心跟拍算法，配合静态冻结 0 FPS 省电切换，彻底杜绝换向穿模与相机跳动；
 * - 纯数学函数 wrapAngleRad / stepPipSmoothAzimuth / computePipCameraPose 无 Three.js 依赖，供规则单测验证。
 */

function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

/**
 * 将任意弧度归一化至 [-Math.PI, Math.PI] 最短有向角差区间。
 */
export function wrapAngleRad(rad) {
  let a = (Number(rad) || 0) % (Math.PI * 2);
  if (a > Math.PI) a -= Math.PI * 2;
  if (a < -Math.PI) a += Math.PI * 2;
  return a;
}

/**
 * 沿最短弧以帧率无关指数阻尼平滑推进小窗相机球面方位角，消除 ±PI 跳变与直线穿模。
 */
export function stepPipSmoothAzimuth(currentAz, targetAz, dtSec, dampRate = 5.5) {
  const dt = clamp(Number(dtSec) || 0.016, 0.001, 0.25);
  const alpha = 1 - Math.exp(-Math.max(0.1, Number(dampRate) || 5.5) * dt);
  const cur = Number(currentAz) || 0;
  const diff = wrapAngleRad((Number(targetAz) || 0) - cur);
  return wrapAngleRad(cur + diff * alpha);
}

/**
 * 计算主选载具旁 Live PiP 相机的世界位置、球面参数与注视点。
 * 约定单位局部前向为 +Z（绕 Y 轴旋转 yaw 后，前向单位矢量为 [sin(yaw), cos(yaw)]）。
 */
export function computePipCameraPose(unitPose, {
  mode = 'front',
  role = 'ground',
  boundingRadius = 5.5,
  groundHeightAt = null,
} = {}) {
  const px = Number(unitPose?.x) || 0;
  const py = Number(unitPose?.y) || 0;
  const pz = Number(unitPose?.z) || 0;
  const yaw = Number(unitPose?.yaw) || 0;

  const r = clamp(Number(boundingRadius) || 5.5, 3.5, 22.0);
  const baseDist = role === 'fixed_wing'
    ? clamp(r * 2.55, 18.0, 44.0)
    : role === 'helicopter'
      ? clamp(r * 2.45, 15.0, 32.0)
      : clamp(r * 2.65, 11.5, 24.0);

  const isRear = mode === 'rear';
  const dist = isRear ? baseDist * 1.06 : baseDist;
  // 斜前 3/4 (+36° 右前方) 与 斜后追尾 (+166° 左后上方)
  const relAngle = isRear ? (Math.PI * 0.92) : (Math.PI * 0.20);
  const worldAzimuth = wrapAngleRad(yaw + relAngle);

  const elevRad = isRear
    ? (role === 'ground' ? 0.34 : 0.30)
    : (role === 'fixed_wing' ? 0.28 : 0.32);

  const horizDist = Math.cos(elevRad) * dist;
  const vertOffset = Math.sin(elevRad) * dist + (role === 'ground' ? 1.4 : 1.0);

  const camX = px + Math.sin(worldAzimuth) * horizDist;
  const camZ = pz + Math.cos(worldAzimuth) * horizDist;
  let camY = py + vertOffset;

  const lookYOffset = role === 'ground' ? clamp(r * 0.24, 1.0, 2.4) : 0.6;
  const lookY = py + lookYOffset;
  if (typeof groundHeightAt === 'function') {
    const gy = Number(groundHeightAt(camX, camZ));
    if (Number.isFinite(gy)) {
      const minClearanceY = gy + (role === 'ground' ? 2.2 : 3.2);
      if (camY < minClearanceY) camY = minClearanceY;
    }
  }

  return {
    position: [camX, camY, camZ],
    target: [px, lookY, pz],
    dist,
    mode: isRear ? 'rear' : 'front',
    worldAzimuth,
    horizDist,
    vertOffset,
    lookYOffset,
  };
}

/**
 * 判断挂在 scene 顶层的节点是否属于光照或空定位目标辅助节点（如 sun.target），
 * 在隔离环境渲染时必须保留，确保主选载具模型获得完整的半球天光与直射日光照明。
 */
function isPreservedTopLevelHelper(node) {
  if (node.isLight === true) return true;
  if (
    !node.isMesh
    && !node.isInstancedMesh
    && !node.isSkinnedMesh
    && !node.isLine
    && !node.isPoints
    && !node.isSprite
    && (!node.children || node.children.length === 0)
  ) {
    return true;
  }
  return false;
}

/**
 * 收集从 targetNode 到 scene 的完整可见父链 [scene, ..., targetNode]。
 * 若任一祖先不在 scene 中或原本不可见，则返回 false，避免修改任何可见性。
 */
function collectVisibleAncestorChain(scene, targetNode, outChain) {
  outChain.length = 0;
  let cur = targetNode;
  while (cur) {
    if (!cur.visible) {
      outChain.length = 0;
      return false;
    }
    outChain.push(cur);
    if (cur === scene) {
      outChain.reverse();
      return true;
    }
    cur = cur.parent;
  }
  outChain.length = 0;
  return false;
}

/**
 * 仅保留 keepChain 路径及顶层光照辅助节点，临时隐藏其余环境与同袍单位，
 * 将被隐藏节点推入 outHidden，由调用方在 finally 中原子还原。
 */
function isolateUnitBranchInScene(scene, keepChain, outHidden) {
  outHidden.length = 0;
  // keepChain[last] 为 targetUnit.node 本身，其全部子部件（车身/炮塔/旋翼/描边）均需完整保留
  for (let depth = 0; depth < keepChain.length - 1; depth++) {
    const parent = keepChain[depth];
    const nextRequired = keepChain[depth + 1];
    const children = parent.children;
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (child === nextRequired || !child.visible) continue;
      if (parent === scene && isPreservedTopLevelHelper(child)) continue;
      child.visible = false;
      outHidden.push(child);
    }
  }
}

/**
 * 在 2D HUD 画布上绘制极轻量军工传感器四角 L 刻度、底部水平标尺与固定翼光电人工地平线（总计 <16 条路径）。
 */
function drawSensorReticleOverlay(ctx2d, w, h, isStatic = false, unit = null) {
  ctx2d.save();
  ctx2d.strokeStyle = isStatic ? 'rgba(218, 198, 134, 0.16)' : 'rgba(148, 179, 214, 0.14)';
  ctx2d.lineWidth = 1;
  const pad = 7;
  const arm = 10;
  ctx2d.beginPath();
  // 左上 / 右上 / 左下 / 右下四角取景刻度
  ctx2d.moveTo(pad, pad + arm);
  ctx2d.lineTo(pad, pad);
  ctx2d.lineTo(pad + arm, pad);
  ctx2d.moveTo(w - pad - arm, pad);
  ctx2d.lineTo(w - pad, pad);
  ctx2d.lineTo(w - pad, pad + arm);
  ctx2d.moveTo(pad, h - pad - arm);
  ctx2d.lineTo(pad, h - pad);
  ctx2d.lineTo(pad + arm, h - pad);
  ctx2d.moveTo(w - pad - arm, h - pad);
  ctx2d.lineTo(w - pad, h - pad);
  ctx2d.lineTo(w - pad, h - pad - arm);
  // 底部极细水平基准线
  ctx2d.moveTo(w * 0.28, h - 10);
  ctx2d.lineTo(w * 0.72, h - 10);
  ctx2d.stroke();

  if (!isStatic && unit?.role === 'fixed_wing' && unit?.flightAttitude) {
    const att = unit.flightAttitude;
    const pitchDeg = clamp(Number(att.pitchDeg) || 0, -30, 30);
    const bankDeg = clamp(Number(att.bankDeg) || 0, -75, 75);
    const speedKmh = Math.max(0, Math.round((Number(att.speedMps) || 0) * 3.6));
    const altM = Math.max(0, Math.round(Number(unit.node?.position?.y) || 0));

    const cx = w * 0.5;
    const cy = h * 0.5 + pitchDeg * 0.72;
    ctx2d.save();
    ctx2d.translate(cx, cy);
    ctx2d.rotate((-bankDeg * Math.PI) / 180);
    ctx2d.strokeStyle = att.strafeRecoilActive
      ? 'rgba(240, 211, 114, 0.48)'
      : 'rgba(130, 214, 178, 0.36)';
    ctx2d.lineWidth = 1.15;
    ctx2d.beginPath();
    ctx2d.moveTo(-26, 0);
    ctx2d.lineTo(-9, 0);
    ctx2d.moveTo(9, 0);
    ctx2d.lineTo(26, 0);
    ctx2d.moveTo(-4, 2);
    ctx2d.lineTo(0, -1.5);
    ctx2d.lineTo(4, 2);
    ctx2d.stroke();
    ctx2d.restore();

    ctx2d.font = '700 8.5px monospace';
    ctx2d.textBaseline = 'middle';
    const spdTxt = `${speedKmh}km/h`;
    const altTxt = `${altM}m`;
    ctx2d.fillStyle = 'rgba(10, 14, 20, 0.52)';
    ctx2d.fillRect(6, h * 0.5 - 7, 46, 14);
    ctx2d.fillRect(w - 42, h * 0.5 - 7, 36, 14);
    ctx2d.fillStyle = 'rgba(190, 224, 208, 0.78)';
    ctx2d.textAlign = 'left';
    ctx2d.fillText(spdTxt, 9, h * 0.5);
    ctx2d.textAlign = 'right';
    ctx2d.fillText(altTxt, w - 9, h * 0.5);
  }
  ctx2d.restore();
}

/**
 * 创建主场景共用的实时实况 PiP 相机控制器（纯载具隔离 + 球面防跳跟拍 + 静态 0 FPS 省电模式）。
 */
export function createLivePipCameraController({
  THREE,
  width = 224,
  height = 176,
  targetFps = 30,
  far = 240,
  backgroundHex = 0x161d28,
} = {}) {
  const pipCam = new THREE.PerspectiveCamera(40, width / height, 0.8, far);
  const pipBgColor = new THREE.Color(backgroundHex);
  const tmpPos = new THREE.Vector3();
  const tmpTarget = new THREE.Vector3();
  const hiddenNodes = [];
  const keepChain = [];

  let cameraMode = 'front';
  let staticMode = false;
  let staticDirty = false;
  let staticRenderedUnitId = null;

  let lastUnitId = null;
  let lastRenderMs = -9999;
  let frameCount = 0;
  let lastDurationMs = 0;
  let lastDrawCalls = 0;
  let lastTriangles = 0;
  let lastCamDist = 0;
  let lastPose = null;
  let lastAttitudeHudActive = false;
  let lastRecoilShakeActive = false;

  let smoothAzimuth = 0;
  let smoothHorizDist = 12;
  let smoothVertOffset = 6;
  let smoothLookYOffset = 1.4;
  let hasSmoothState = false;
  const environmentExcluded = true;
  const shadowMapSkipped = true;
  const offscreenIsolated = true;

  // 独立离屏 RenderTarget 与 ACES+sRGB Y翻转后处理全屏 Quad（零污染主屏默认帧缓冲）
  let pipHdrRt = null;
  let pipOutRt = null;
  let targetW = 0;
  let targetH = 0;
  let pixelBuf = null;
  let offCanvas = null;
  let offCtx = null;
  let offImageData = null;

  const postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const postScene = new THREE.Scene();
  const postQuadGeo = new THREE.PlaneGeometry(2, 2);
  const postMat = new THREE.ShaderMaterial({
    uniforms: {
      tDiffuse: { value: null },
      uExposure: { value: 1.18 },
    },
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
    vertexShader: /* glsl */`
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = vec4(position.xy, 0.0, 1.0);
      }
    `,
    fragmentShader: /* glsl */`
      uniform sampler2D tDiffuse;
      uniform float uExposure;
      varying vec2 vUv;
      vec3 pipRRTAndODTFit(vec3 v) {
        vec3 a = v * (v + 0.0245786) - 0.000090537;
        vec3 b = v * (0.983729 * v + 0.4329510) + 0.238081;
        return a / max(b, vec3(1e-5));
      }
      vec3 pipACESFilmic(vec3 color, float exposure) {
        const mat3 ACESInputMat = mat3(
          vec3(0.59719, 0.07600, 0.02840),
          vec3(0.35458, 0.90834, 0.13383),
          vec3(0.04823, 0.01566, 0.83777)
        );
        const mat3 ACESOutputMat = mat3(
          vec3( 1.60475, -0.10208, -0.00327),
          vec3(-0.53108,  1.10813, -0.07276),
          vec3(-0.07367, -0.00605,  1.07602)
        );
        vec3 c = max(color, vec3(0.0)) * (exposure / 0.6);
        c = ACESInputMat * c;
        c = pipRRTAndODTFit(c);
        c = ACESOutputMat * c;
        return clamp(c, 0.0, 1.0);
      }
      vec3 pipLinearToSRGB(vec3 lin) {
        vec3 lo = lin * 12.92;
        vec3 hi = pow(max(lin, vec3(0.0)), vec3(1.0 / 2.4)) * 1.055 - 0.055;
        return mix(lo, hi, step(vec3(0.0031308), lin));
      }
      void main() {
        // readRenderTargetPixels Y=0 在底行，putImageData Y=0 在顶行：此处先按垂直翻转采样
        vec4 texel = texture2D(tDiffuse, vec2(vUv.x, 1.0 - vUv.y));
        vec3 srgb = pipLinearToSRGB(pipACESFilmic(texel.rgb, uExposure));
        gl_FragColor = vec4(srgb, 1.0);
      }
    `,
  });
  const postMesh = new THREE.Mesh(postQuadGeo, postMat);
  postMesh.frustumCulled = false;
  postScene.add(postMesh);

  function disposeRenderTargets() {
    pipHdrRt?.dispose?.();
    pipOutRt?.dispose?.();
    pipHdrRt = null;
    pipOutRt = null;
    pixelBuf = null;
    offCanvas = null;
    offCtx = null;
    offImageData = null;
    targetW = 0;
    targetH = 0;
  }

  function ensureOffscreenTargets(renderer, w, h) {
    if (pipHdrRt && pipOutRt && targetW === w && targetH === h) return true;
    disposeRenderTargets();
    const isWebGL2 = Boolean(renderer.capabilities?.isWebGL2);
    pipHdrRt = new THREE.WebGLRenderTarget(w, h, {
      type: isWebGL2 ? THREE.HalfFloatType : THREE.UnsignedByteType,
      samples: 0,
      depthBuffer: true,
      stencilBuffer: false,
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
    });
    pipHdrRt.texture.name = 'ScavengerLivePipHdrRT';
    pipOutRt = new THREE.WebGLRenderTarget(w, h, {
      type: THREE.UnsignedByteType,
      samples: 0,
      depthBuffer: false,
      stencilBuffer: false,
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
    });
    pipOutRt.texture.name = 'ScavengerLivePipOutRT';
    pixelBuf = new Uint8Array(w * h * 4);
    if (typeof document !== 'undefined') {
      offCanvas = document.createElement('canvas');
      offCanvas.width = w;
      offCanvas.height = h;
      offCtx = offCanvas.getContext('2d');
      offImageData = offCtx ? offCtx.createImageData(w, h) : null;
    }
    targetW = w;
    targetH = h;
    return Boolean(pipHdrRt && pipOutRt && offCtx && offImageData);
  }

  function setCameraMode(nextMode) {
    cameraMode = nextMode === 'rear' ? 'rear' : 'front';
    hasSmoothState = false;
    return cameraMode;
  }

  function toggleCameraMode() {
    return setCameraMode(cameraMode === 'front' ? 'rear' : 'front');
  }

  function setStaticMode(flag) {
    const next = Boolean(flag);
    if (next === staticMode) return staticMode;
    staticMode = next;
    hasSmoothState = false;
    if (staticMode) {
      staticDirty = true;
      staticRenderedUnitId = null;
    } else {
      staticDirty = false;
      staticRenderedUnitId = null;
    }
    return staticMode;
  }

  function toggleStaticMode() {
    return setStaticMode(!staticMode);
  }

  function renderBeforeMainFrame({
    renderer,
    scene,
    pipCanvas,
    unit,
    state,
    nowMs = performance.now(),
    force = false,
    forceStaticRefresh = false,
  }) {
    if (!renderer || !scene || !pipCanvas || !unit?.node || !unit.node.visible) return false;
    if (state?.hudHidden || state?.paused) return false;

    const minIntervalMs = 1000 / Math.max(1, targetFps);
    const unitChanged = unit.id !== lastUnitId;

    if (staticMode) {
      // 静态冻结 0 FPS 省电模式：仅切入静态或切换选中新单位时渲染 1 帧，其余帧彻底跳过 WebGL 与拷贝
      const needsStaticFrame = staticDirty
        || unitChanged
        || staticRenderedUnitId !== unit.id
        || Boolean(forceStaticRefresh);
      if (!needsStaticFrame) {
        return false;
      }
    } else if (!force && !unitChanged && nowMs - lastRenderMs < minIntervalMs) {
      return false;
    }

    if (!collectVisibleAncestorChain(scene, unit.node, keepChain)) {
      return false;
    }

    const mainW = Math.max(1, Number(state?.internalWidth) || renderer.domElement.width || 1208);
    const mainH = Math.max(1, Number(state?.internalHeight) || renderer.domElement.height || 680);
    const drawW = Math.max(1, Math.min(width, mainW));
    const drawH = Math.max(1, Math.min(height, mainH));
    if (!ensureOffscreenTargets(renderer, drawW, drawH)) {
      keepChain.length = 0;
      return false;
    }

    const t0 = performance.now();

    const px = Number(unit.node.position.x) || 0;
    const py = Number(unit.node.position.y) || 0;
    const pz = Number(unit.node.position.z) || 0;

    // 无环境隔离模式下不按山坡地形偏移；静态模式固定标准斜前 3/4 展示姿态
    const pose = computePipCameraPose(
      {
        x: px,
        y: py,
        z: pz,
        yaw: unit.node.rotation?.y || 0,
      },
      {
        mode: staticMode ? 'front' : cameraMode,
        role: unit.role,
        boundingRadius: unit.boundingRadius || 5.5,
        groundHeightAt: null,
      },
    );

    if (staticMode || unitChanged || !hasSmoothState) {
      smoothAzimuth = pose.worldAzimuth;
      smoothHorizDist = pose.horizDist;
      smoothVertOffset = pose.vertOffset;
      smoothLookYOffset = pose.lookYOffset;
      hasSmoothState = true;
      lastUnitId = unit.id;
    } else {
      const dtSec = clamp((nowMs - lastRenderMs) * 0.001, 0.001, 0.25);
      const radiusAlpha = 1 - Math.exp(-6.0 * dtSec);
      smoothAzimuth = stepPipSmoothAzimuth(smoothAzimuth, pose.worldAzimuth, dtSec, 5.2);
      smoothHorizDist += (pose.horizDist - smoothHorizDist) * radiusAlpha;
      smoothVertOffset += (pose.vertOffset - smoothVertOffset) * radiusAlpha;
      smoothLookYOffset += (pose.lookYOffset - smoothLookYOffset) * radiusAlpha;
    }

    const recoilShakeActive = Boolean(!staticMode && unit.role === 'fixed_wing' && unit.flightAttitude?.strafeRecoilActive);
    const attitudeHudActive = Boolean(!staticMode && unit.role === 'fixed_wing' && unit.flightAttitude);
    const shakeX = recoilShakeActive ? (Math.sin(nowMs * 0.065) * 0.11 + Math.cos(nowMs * 0.113) * 0.04) : 0;
    const shakeY = recoilShakeActive ? (Math.cos(nowMs * 0.079) * 0.10 + Math.sin(nowMs * 0.131) * 0.04) : 0;
    const shakeZ = recoilShakeActive ? (Math.sin(nowMs * 0.053 + 1.1) * 0.09) : 0;

    tmpPos.set(
      px + Math.sin(smoothAzimuth) * smoothHorizDist + shakeX,
      py + smoothVertOffset + shakeY,
      pz + Math.cos(smoothAzimuth) * smoothHorizDist + shakeZ,
    );
    tmpTarget.set(px, py + smoothLookYOffset, pz);
    lastCamDist = Number(tmpPos.distanceTo(tmpTarget).toFixed(4));
    lastAttitudeHudActive = attitudeHudActive;
    lastRecoilShakeActive = recoilShakeActive;

    pipCam.aspect = drawW / Math.max(1, drawH);
    pipCam.updateProjectionMatrix();
    pipCam.position.copy(tmpPos);
    pipCam.lookAt(tmpTarget);
    pipCam.updateMatrixWorld(true);

    const prevRt = renderer.getRenderTarget();
    const prevScissorTest = typeof renderer.getScissorTest === 'function' ? Boolean(renderer.getScissorTest()) : false;
    const prevAutoClear = renderer.autoClear;
    const prevShadowEnabled = Boolean(renderer.shadowMap?.enabled);
    const prevBackground = scene.background;
    const prevFog = scene.fog;
    let renderedOk = false;

    try {
      // 跳过整张太阳阴影贴图重算，并原子隔离全部环境/其他载具，渲染至独立离屏 RenderTarget（零污染主屏帧缓冲）
      if (renderer.shadowMap) renderer.shadowMap.enabled = false;
      scene.background = pipBgColor;
      scene.fog = null;
      isolateUnitBranchInScene(scene, keepChain, hiddenNodes);

      renderer.autoClear = true;
      renderer.setScissorTest(false);
      renderer.setRenderTarget(pipHdrRt);
      renderer.clear(true, true, false);
      renderer.render(scene, pipCam);

      lastDrawCalls = Number(renderer.info?.render?.calls || 0);
      lastTriangles = Number(renderer.info?.render?.triangles || 0);

      postMat.uniforms.tDiffuse.value = pipHdrRt.texture;
      postMat.uniforms.uExposure.value = Number.isFinite(renderer.toneMappingExposure) ? renderer.toneMappingExposure : 1.18;
      renderer.setRenderTarget(pipOutRt);
      renderer.clear(true, false, false);
      renderer.render(postScene, postCam);
      renderer.readRenderTargetPixels(pipOutRt, 0, 0, drawW, drawH, pixelBuf);
      renderedOk = true;
    } finally {
      for (let i = 0; i < hiddenNodes.length; i++) {
        hiddenNodes[i].visible = true;
      }
      hiddenNodes.length = 0;
      keepChain.length = 0;

      scene.background = prevBackground;
      scene.fog = prevFog;
      if (renderer.shadowMap) renderer.shadowMap.enabled = prevShadowEnabled;
      renderer.setRenderTarget(prevRt || null);
      renderer.setScissorTest(prevScissorTest);
      if (!prevRt) {
        renderer.setViewport(0, 0, mainW, mainH);
      }
      renderer.autoClear = prevAutoClear;
    }

    const ctx2d = pipCanvas.getContext('2d');
    if (ctx2d && renderedOk && offCtx && offImageData && offCanvas) {
      offImageData.data.set(pixelBuf);
      offCtx.putImageData(offImageData, 0, 0);
      ctx2d.clearRect(0, 0, pipCanvas.width, pipCanvas.height);
      ctx2d.drawImage(
        offCanvas,
        0,
        0,
        drawW,
        drawH,
        0,
        0,
        pipCanvas.width,
        pipCanvas.height,
      );
      drawSensorReticleOverlay(ctx2d, pipCanvas.width, pipCanvas.height, staticMode, unit);
    }

    lastRenderMs = nowMs;
    frameCount += 1;
    lastDurationMs = Number((performance.now() - t0).toFixed(3));
    lastPose = pose;
    if (staticMode) {
      staticDirty = false;
      staticRenderedUnitId = unit.id;
    }
    return true;
  }

  function getDebugState() {
    return {
      cameraMode,
      staticMode,
      staticFrozen: staticMode && !staticDirty && staticRenderedUnitId === lastUnitId,
      lastUnitId,
      frameCount,
      lastDurationMs,
      lastDrawCalls,
      lastTriangles,
      lastCamDist,
      smoothAzimuth: Number(smoothAzimuth.toFixed(4)),
      environmentExcluded,
      shadowMapSkipped,
      offscreenIsolated,
      backgroundHex,
      attitudeHudActive: lastAttitudeHudActive,
      recoilShakeActive: lastRecoilShakeActive,
      lastPose,
    };
  }

  function dispose() {
    disposeRenderTargets();
    postQuadGeo?.dispose?.();
    postMat?.dispose?.();
  }

  return {
    camera: pipCam,
    setCameraMode,
    toggleCameraMode,
    getCameraMode: () => cameraMode,
    setStaticMode,
    toggleStaticMode,
    isStaticMode: () => staticMode,
    renderBeforeMainFrame,
    getDebugState,
    dispose,
  };
}
