/**
 * Scavenger 载具风格化材质与轮廓描边唯一共享模块（清透微缩沙盘 miniature_sand）。
 * - 亮度自适应暗部提拉（Luminance-Adaptive Shadow Lift），告别黑炭感同时保护浅色涂装细节；
 * - 高度明度梯度 + 暖色托底 + 棱边干扫 + 物理镀膜玻璃（coated_clear）；
 * - 基于 1mm 空间焊接平滑法线 + 裁剪空间恒定像素外扩 + AlphaTest 纹理裁剪的防开裂描边；
 * - 轻量程序化 PMREM 环境贴图预设（户外沙盘日光 sandboxEnv / 机库夜光 armoryEnv）。
 */
import * as THREE from 'three';

// 模块级 WeakMap：隔离所有非 JSON 运行时对象引用，保障 Object3D.clone(true) 与 JSON.stringify(userData) 安全
const _smoothNormalAttrMap = new WeakMap();
const _styledMatsMap = new WeakMap();
const _rootOutlineMatsMap = new WeakMap();
const _replacedOrigMatsMap = new WeakMap();

export const VEHICLE_STYLE_DEFAULT = Object.freeze({
  key: 'miniature_sand',
  name: '清透微缩沙盘',
  albedoPow: 0.72,
  albedoLift: 0.032,
  saturation: 1.22,
  warmTint: [1.03, 1.01, 0.94],
  metalnessScale: 0.36,
  metalnessMax: 0.12,
  roughnessMin: 0.66,
  roughnessMax: 0.88,
  heightGradLow: 0.86,
  heightGradHigh: 1.04,
  shadowFloor: 0.055,
  rimStrength: 0.12,
  rimColor: [1.0, 0.96, 0.86],
  normalScale: 1.28,
  anisotropy: 8,
  envIntensity: 0.45,
  outlineMode: 'hull',
  outlinePx: 1.15,
  outlineColor: '#1e221b',
});

export const VEHICLE_STYLE_PRESETS = Object.freeze({
  miniature_sand: VEHICLE_STYLE_DEFAULT,
});

export const VEHICLE_STYLE_PRESET_LIST = Object.freeze([VEHICLE_STYLE_DEFAULT]);

function makeGradientDomeMaterial(topHex, midHex, botHex) {
  return new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      uTop: { value: new THREE.Color(topHex) },
      uMid: { value: new THREE.Color(midHex) },
      uBot: { value: new THREE.Color(botHex) },
    },
    vertexShader: `
      varying vec3 vDir;
      void main() {
        vDir = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uTop;
      uniform vec3 uMid;
      uniform vec3 uBot;
      varying vec3 vDir;
      void main() {
        float h = clamp(vDir.y, -1.0, 1.0);
        vec3 col = h >= 0.0
          ? mix(uMid, uTop, pow(h, 0.65))
          : mix(uMid, uBot, pow(-h, 0.55));
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
}

/**
 * 构建与游戏风格一致的两套轻量 PMREM 环境贴图：
 * - sandboxEnv：对齐 Airbase 晴日沙盘（天蓝穹顶 + 柔和暖米地平线 + 暖沙土地反）；
 * - armoryEnv：对齐机库夜光棚拍（双柔光灯带 + 冷侧补光 + 深炭停机坪地反）。
 */
export function createStylizedVehicleEnvMaps(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();

  const buildEnv = buildSceneFn => {
    const envScene = new THREE.Scene();
    const disposables = [];
    buildSceneFn(envScene, disposables);
    const rt = pmrem.fromScene(envScene, 0.035);
    for (const d of disposables) d.dispose?.();
    return rt;
  };

  const sandboxRt = buildEnv((s, disp) => {
    const domeGeo = new THREE.SphereGeometry(40, 24, 16);
    const domeMat = makeGradientDomeMaterial(0x9bc0df, 0xd6cdb9, 0x8e7f66);
    disp.push(domeGeo, domeMat);
    s.add(new THREE.Mesh(domeGeo, domeMat));

    const sunGeo = new THREE.PlaneGeometry(12, 12);
    const sunMat = new THREE.MeshBasicMaterial({ color: 0xfff0d2, side: THREE.DoubleSide });
    sunMat.color.multiplyScalar(1.35);
    const sunCard = new THREE.Mesh(sunGeo, sunMat);
    sunCard.position.set(-14, 22, 11);
    sunCard.lookAt(0, 0, 0);
    disp.push(sunGeo, sunMat);
    s.add(sunCard);

    const fillGeo = new THREE.PlaneGeometry(22, 9);
    const fillMat = new THREE.MeshBasicMaterial({ color: 0xc5dcef, side: THREE.DoubleSide });
    const fillCard = new THREE.Mesh(fillGeo, fillMat);
    fillCard.position.set(16, 12, -16);
    fillCard.lookAt(0, 0, 0);
    disp.push(fillGeo, fillMat);
    s.add(fillCard);
  });

  const armoryRt = buildEnv((s, disp) => {
    const domeGeo = new THREE.SphereGeometry(40, 24, 16);
    const domeMat = makeGradientDomeMaterial(0x3a4452, 0x5d6670, 0x383630);
    disp.push(domeGeo, domeMat);
    s.add(new THREE.Mesh(domeGeo, domeMat));

    const stripGeo = new THREE.PlaneGeometry(22, 5.5);
    const keyMat = new THREE.MeshBasicMaterial({ color: 0xfff3db, side: THREE.DoubleSide });
    keyMat.color.multiplyScalar(2.3);
    const strip1 = new THREE.Mesh(stripGeo, keyMat);
    strip1.position.set(-9, 18, 10);
    strip1.lookAt(0, 0, 0);

    const rimMat = new THREE.MeshBasicMaterial({ color: 0xd6e8ff, side: THREE.DoubleSide });
    rimMat.color.multiplyScalar(1.65);
    const strip2 = new THREE.Mesh(stripGeo, rimMat);
    strip2.position.set(14, 11, -13);
    strip2.lookAt(0, 0, 0);

    const sideGeo = new THREE.PlaneGeometry(16, 8);
    const sideMat = new THREE.MeshBasicMaterial({ color: 0xf4dec0, side: THREE.DoubleSide });
    sideMat.color.multiplyScalar(1.2);
    const strip3 = new THREE.Mesh(sideGeo, sideMat);
    strip3.position.set(12, 6, 14);
    strip3.lookAt(0, 0, 0);

    disp.push(stripGeo, sideGeo, keyMat, rimMat, sideMat);
    s.add(strip1, strip2, strip3);
  });

  pmrem.dispose();

  return Object.freeze({
    sandboxEnv: sandboxRt.texture,
    armoryEnv: armoryRt.texture,
    dispose() {
      sandboxRt.dispose();
      armoryRt.dispose();
    },
  });
}

/**
 * 基于空间坐标焊接（1mm 格网）生成平滑平均法线属性，消除直角装甲反转壳描边开裂。
 */
export function buildSmoothNormalAttribute(geometry) {
  if (!geometry) return null;
  const posAttr = geometry.getAttribute('position');
  if (!posAttr) return null;

  const existingAttr = geometry.getAttribute('aSmoothNormal');
  if (existingAttr && existingAttr.count === posAttr.count) {
    _smoothNormalAttrMap.set(geometry, existingAttr);
    return existingAttr;
  }
  const cached = _smoothNormalAttrMap.get(geometry);
  if (cached && cached.count === posAttr.count) {
    geometry.setAttribute('aSmoothNormal', cached);
    return cached;
  }

  let normAttr = geometry.getAttribute('normal');
  if (!normAttr) {
    geometry.computeVertexNormals();
    normAttr = geometry.getAttribute('normal');
  }

  const count = posAttr.count;
  const buckets = new Map();
  const vertKeys = new Array(count);

  for (let i = 0; i < count; i++) {
    const kx = Math.round(posAttr.getX(i) * 1000);
    const ky = Math.round(posAttr.getY(i) * 1000);
    const kz = Math.round(posAttr.getZ(i) * 1000);
    const key = `${kx},${ky},${kz}`;
    vertKeys[i] = key;

    const nx = normAttr ? normAttr.getX(i) : 0;
    const ny = normAttr ? normAttr.getY(i) : 1;
    const nz = normAttr ? normAttr.getZ(i) : 0;

    let acc = buckets.get(key);
    if (!acc) {
      acc = [nx, ny, nz];
      buckets.set(key, acc);
    } else {
      acc[0] += nx;
      acc[1] += ny;
      acc[2] += nz;
    }
  }

  for (const acc of buckets.values()) {
    const len = Math.hypot(acc[0], acc[1], acc[2]);
    if (len > 1e-6) {
      acc[0] /= len;
      acc[1] /= len;
      acc[2] /= len;
    } else {
      acc[0] = 0;
      acc[1] = 1;
      acc[2] = 0;
    }
  }

  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const acc = buckets.get(vertKeys[i]);
    out[i * 3] = acc[0];
    out[i * 3 + 1] = acc[1];
    out[i * 3 + 2] = acc[2];
  }

  const attr = new THREE.BufferAttribute(out, 3);
  _smoothNormalAttrMap.set(geometry, attr);
  if (!geometry.getAttribute('aSmoothNormal')) {
    geometry.setAttribute('aSmoothNormal', attr);
  }
  return attr;
}

/**
 * 创建裁剪空间恒定像素宽度的反转壳描边材质（兼容透视与正交相机、InstancedMesh 与 AlphaTest 贴图裁剪）。
 */
export function createClipSpaceOutlineMaterial(params = {}) {
  const hasAlphaTex = Boolean(params.alphaMapTex && params.alphaTest > 0.05);
  return new THREE.ShaderMaterial({
    name: hasAlphaTex ? 'scv_stylized_outline_alpha' : 'scv_stylized_outline',
    side: THREE.BackSide,
    depthWrite: true,
    depthTest: true,
    transparent: false,
    defines: hasAlphaTex ? { USE_OUTLINE_ALPHA: 1 } : {},
    uniforms: {
      uOutlinePx: { value: Number(params.outlinePx ?? VEHICLE_STYLE_DEFAULT.outlinePx) },
      uOutlineColor: { value: new THREE.Color(params.outlineColor || VEHICLE_STYLE_DEFAULT.outlineColor) },
      uResolution: { value: new THREE.Vector2(params.width || 1208, params.height || 680) },
      uDepthBias: { value: 0.00035 },
      uAlphaMapTex: { value: params.alphaMapTex || null },
      uAlphaTest: { value: Number(params.alphaTest || 0.5) },
    },
    vertexShader: `
      attribute vec3 aSmoothNormal;
      uniform float uOutlinePx;
      uniform vec2 uResolution;
      uniform float uDepthBias;
      #ifdef USE_OUTLINE_ALPHA
        varying vec2 vOutUv;
      #endif
      void main() {
        #ifdef USE_OUTLINE_ALPHA
          vOutUv = uv;
        #endif
        vec3 objN = normalize(aSmoothNormal);
        vec4 localPos = vec4(position, 1.0);
        #ifdef USE_INSTANCING
          mat3 im = mat3(instanceMatrix);
          objN = normalize(im * objN);
          localPos = instanceMatrix * localPos;
        #endif
        vec4 mvPos = modelViewMatrix * localPos;
        vec4 clipPos = projectionMatrix * mvPos;
        if (uOutlinePx > 0.01) {
          vec3 viewN = normalize(normalMatrix * objN);
          vec4 clipTip = projectionMatrix * vec4(mvPos.xyz + viewN * 0.05, 1.0);
          vec2 ndc0 = clipPos.xy / max(abs(clipPos.w), 1e-5);
          vec2 ndc1 = clipTip.xy / max(abs(clipTip.w), 1e-5);
          vec2 ndcDir = ndc1 - ndc0;
          float lenSq = dot(ndcDir, ndcDir);
          if (lenSq > 1e-12) {
            vec2 screenDir = ndcDir * inversesqrt(lenSq);
            vec2 pxStep = screenDir * (uOutlinePx * 2.0 / max(uResolution, vec2(1.0)));
            clipPos.xy += pxStep * clipPos.w;
            clipPos.z += uDepthBias * clipPos.w;
          }
        }
        gl_Position = clipPos;
      }
    `,
    fragmentShader: `
      uniform vec3 uOutlineColor;
      #ifdef USE_OUTLINE_ALPHA
        uniform sampler2D uAlphaMapTex;
        uniform float uAlphaTest;
        varying vec2 vOutUv;
      #endif
      void main() {
        #ifdef USE_OUTLINE_ALPHA
          if (texture2D(uAlphaMapTex, vOutUv).a < uAlphaTest) discard;
        #endif
        gl_FragColor = vec4(uOutlineColor, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });
}

function createGlassMaterial(origMat, envIntensity, envMap) {
  const mat = new THREE.MeshPhysicalMaterial({
    name: `${origMat?.name || 'm_glass'}_stylized`,
    map: origMat?.map || null,
    color: 0x476b82,
    emissive: 0x0f1e29,
    emissiveIntensity: 0.28,
    metalness: 0.22,
    roughness: 0.08,
    transparent: true,
    opacity: 0.52,
    depthWrite: false,
    side: THREE.DoubleSide,
    clearcoat: 1.0,
    clearcoatRoughness: 0.06,
    reflectivity: 0.92,
    ior: 1.52,
    envMapIntensity: Math.max(1.15, envIntensity * 1.55),
  });
  if (envMap !== undefined) {
    mat.envMap = envMap || null;
  }
  return mat;
}

function patchStandardMaterial(mat, origMat, sharedUniforms, relRowY, boundsY, params, envMap) {
  const origMetal = Number.isFinite(origMat?.metalness) ? origMat.metalness : 0.15;
  const origRough = Number.isFinite(origMat?.roughness) ? origMat.roughness : 0.72;

  mat.metalness = Math.min(params.metalnessMax, origMetal * params.metalnessScale);
  mat.roughness = Math.max(params.roughnessMin, Math.min(params.roughnessMax, origRough));
  mat.envMapIntensity = params.envIntensity;
  if (envMap !== undefined) {
    mat.envMap = envMap || null;
  }

  if (mat.normalMap) {
    const ns = params.normalScale;
    mat.normalScale = new THREE.Vector2(ns, ns);
    mat.normalMap.anisotropy = params.anisotropy;
  }
  if (mat.map) {
    mat.map.anisotropy = params.anisotropy;
  }

  mat.customProgramCacheKey = () => 'scv-veh-stylized-pbr-v3';
  mat.onBeforeCompile = shader => {
    shader.uniforms.uVehRelRowY = { value: relRowY };
    shader.uniforms.uVehBoundsY = { value: boundsY };
    shader.uniforms.uAlbedoPow = sharedUniforms.uAlbedoPow;
    shader.uniforms.uAlbedoLift = sharedUniforms.uAlbedoLift;
    shader.uniforms.uSaturation = sharedUniforms.uSaturation;
    shader.uniforms.uWarmTint = sharedUniforms.uWarmTint;
    shader.uniforms.uShadowFloor = sharedUniforms.uShadowFloor;
    shader.uniforms.uHeightLow = sharedUniforms.uHeightLow;
    shader.uniforms.uHeightHigh = sharedUniforms.uHeightHigh;
    shader.uniforms.uRimStrength = sharedUniforms.uRimStrength;
    shader.uniforms.uRimColor = sharedUniforms.uRimColor;

    shader.vertexShader =
      `
      uniform vec4 uVehRelRowY;
      uniform vec2 uVehBoundsY;
      varying float vVehHeightFrac;
      ` + shader.vertexShader;

    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
      float scvLocalY = dot(uVehRelRowY, vec4(transformed, 1.0));
      vVehHeightFrac = clamp((scvLocalY - uVehBoundsY.x) / max(0.25, uVehBoundsY.y), 0.0, 1.0);
      `
    );

    shader.fragmentShader =
      `
      uniform float uAlbedoPow;
      uniform float uAlbedoLift;
      uniform float uSaturation;
      uniform vec3 uWarmTint;
      uniform float uShadowFloor;
      uniform float uHeightLow;
      uniform float uHeightHigh;
      uniform float uRimStrength;
      uniform vec3 uRimColor;
      varying float vVehHeightFrac;
      ` + shader.fragmentShader;

    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <map_fragment>',
      `#include <map_fragment>
      vec3 scvLin = max(diffuseColor.rgb, vec3(0.0));
      float scvSrcLum = dot(scvLin, vec3(0.2126, 0.7152, 0.0722));
      // 亮度自适应提亮：只重点抬起暗黑/深橄榄涂装 (lum<0.36)，浅色机体 (如 A-10C) 保留原始色彩密度不漂白
      float scvDarkMask = 1.0 - smoothstep(0.05, 0.38, scvSrcLum);
      float scvEffPow = mix(1.0, uAlbedoPow, scvDarkMask);
      scvLin = pow(max(scvLin, vec3(1e-4)), vec3(scvEffPow)) + vec3(uAlbedoLift) * scvDarkMask;
      float scvLum2 = dot(scvLin, vec3(0.2126, 0.7152, 0.0722));
      scvLin = mix(vec3(scvLum2), scvLin, uSaturation);
      scvLin *= mix(vec3(1.0), uWarmTint, 0.5 + 0.5 * scvDarkMask);
      scvLin *= mix(uHeightLow, uHeightHigh, clamp(vVehHeightFrac, 0.0, 1.0));
      diffuseColor.rgb = clamp(scvLin, 0.0, 0.92);
      `
    );

    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <emissivemap_fragment>',
      `#include <emissivemap_fragment>
      vec3 scvViewDir = normalize(vViewPosition);
      vec3 scvViewNorm = normalize(normal);
      float scvNdotV = clamp(abs(dot(scvViewNorm, scvViewDir)), 0.0, 1.0);
      float scvGrazing = 1.0 - scvNdotV;
      float scvDiffLum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
      float scvShadowMask = (1.0 - smoothstep(0.16, 0.52, scvDiffLum)) * mix(1.15, 0.65, clamp(vVehHeightFrac, 0.0, 1.0));
      totalEmissiveRadiance += diffuseColor.rgb * (uShadowFloor * scvShadowMask);
      float scvDryBrush = smoothstep(0.30, 0.74, scvGrazing) * (0.50 + 0.50 * vVehHeightFrac) * (1.0 - 0.55 * smoothstep(0.32, 0.75, scvDiffLum));
      totalEmissiveRadiance += mix(diffuseColor.rgb, uRimColor, 0.50) * (uRimStrength * scvDryBrush);
      `
    );
  };
  mat.needsUpdate = true;
}

function cleanupAppliedStyle(root) {
  const toRemove = [];
  const clonedMats = new Set();
  const replacedOrigs = _replacedOrigMatsMap.get(root);

  root.traverse(obj => {
    if (obj.userData?.isStylizedOutline) {
      toRemove.push(obj);
      return;
    }
    if (!obj.isMesh) return;
    const list = _styledMatsMap.get(obj);
    if (Array.isArray(list)) {
      for (const m of list) {
        if (m) clonedMats.add(m);
      }
      _styledMatsMap.delete(obj);
    }
  });

  for (const child of toRemove) {
    child.parent?.remove(child);
  }
  const outlineMats = _rootOutlineMatsMap.get(root);
  if (Array.isArray(outlineMats)) {
    for (const om of new Set(outlineMats)) om?.dispose?.();
    _rootOutlineMatsMap.delete(root);
  }
  for (const m of clonedMats) m?.dispose?.();
  if (Array.isArray(replacedOrigs)) {
    for (const om of replacedOrigs) om?.dispose?.();
    _replacedOrigMatsMap.delete(root);
  }
}

/**
 * 将唯一正式清透微缩沙盘风格（miniature_sand）应用到已加载的载具模型树。
 * 支持传 (root, options) 或 (root, preset, options)。
 */
export function applyVehicleStyleToObject(root, presetOrOptions = null, maybeOptions = null) {
  let options;
  if (maybeOptions && typeof maybeOptions === 'object') {
    options = { ...(presetOrOptions || {}), ...maybeOptions };
  } else {
    options = presetOrOptions || {};
  }

  const params = VEHICLE_STYLE_DEFAULT;
  const width = options.width || 1208;
  const height = options.height || 680;
  const castShadow = options.castShadow !== false;
  const receiveShadow = options.receiveShadow !== false;
  const envMapOpt = options.envMap;
  const wantOutline = options.outlineEnabled !== false && options.outlineMode !== 'none' && options.outlinePx !== 0;

  root.updateMatrixWorld(true);
  const invRoot = root.matrixWorld.clone().invert();
  const rootBox = new THREE.Box3().setFromObject(root);
  const minY = Number.isFinite(rootBox.min.y) ? rootBox.min.y - root.position.y : 0;
  const maxY = Number.isFinite(rootBox.max.y) ? rootBox.max.y - root.position.y : 2;
  const boundsY = new THREE.Vector2(minY, Math.max(0.25, maxY - minY));

  const sharedUniforms = {
    uAlbedoPow: { value: params.albedoPow },
    uAlbedoLift: { value: params.albedoLift },
    uSaturation: { value: params.saturation },
    uWarmTint: { value: new THREE.Vector3(...params.warmTint) },
    uShadowFloor: { value: params.shadowFloor },
    uHeightLow: { value: params.heightGradLow },
    uHeightHigh: { value: params.heightGradHigh },
    uRimStrength: { value: params.rimStrength },
    uRimColor: { value: new THREE.Color(...params.rimColor) },
  };

  const outlineMats = [];
  let sharedSolidOutlineMat = null;
  if (wantOutline) {
    sharedSolidOutlineMat = createClipSpaceOutlineMaterial({
      outlinePx: params.outlinePx,
      outlineColor: params.outlineColor,
      width,
      height,
    });
    outlineMats.push(sharedSolidOutlineMat);
  }
  _rootOutlineMatsMap.set(root, outlineMats);

  const styledMeshes = [];
  const outlineMeshes = [];
  const sourceMeshes = [];
  const replacedOrigMats = new Set();

  root.traverse(obj => {
    if (obj.isMesh && !obj.userData?.isStylizedOutline) {
      sourceMeshes.push(obj);
    }
  });

  for (const obj of sourceMeshes) {
    obj.castShadow = castShadow;
    obj.receiveShadow = receiveShadow;

    const origEntry = obj.material;
    const origList = Array.isArray(origEntry) ? origEntry : [origEntry];

    const relMat = invRoot.clone().multiply(obj.matrixWorld);
    const e = relMat.elements;
    const relRowY = new THREE.Vector4(e[1], e[5], e[9], e[13]);

    const styledList = origList.map(origMat => {
      if (!origMat) return origMat;
      replacedOrigMats.add(origMat);
      const isGlass = Boolean(origMat.transparent || origMat.alphaMode === 'BLEND' || /glass/i.test(origMat.name || ''));
      if (isGlass) {
        return createGlassMaterial(origMat, params.envIntensity, envMapOpt);
      }
      const cm = origMat.clone();
      if (cm.isMeshStandardMaterial) {
        patchStandardMaterial(cm, origMat, sharedUniforms, relRowY, boundsY, params, envMapOpt);
      }
      return cm;
    });

    _styledMatsMap.set(obj, styledList);
    obj.material = Array.isArray(origEntry) ? styledList : styledList[0];
    styledMeshes.push(styledList);

    if (wantOutline && obj.geometry) {
      const primaryOrig = origList.find(m => m && !(m.transparent || m.alphaMode === 'BLEND' || /glass/i.test(m.name || '')));
      if (primaryOrig) {
        buildSmoothNormalAttribute(obj.geometry);
        let meshOutlineMat = sharedSolidOutlineMat;
        if (primaryOrig.alphaTest > 0.05 && primaryOrig.map && obj.geometry.getAttribute('uv')) {
          meshOutlineMat = createClipSpaceOutlineMaterial({
            outlinePx: params.outlinePx,
            outlineColor: params.outlineColor,
            width,
            height,
            alphaMapTex: primaryOrig.map,
            alphaTest: primaryOrig.alphaTest,
          });
          outlineMats.push(meshOutlineMat);
        }
        const outMesh = new THREE.Mesh(obj.geometry, meshOutlineMat);
        outMesh.name = `${obj.name || 'part'}_outline`;
        outMesh.userData = { isStylizedOutline: true };
        outMesh.castShadow = false;
        outMesh.receiveShadow = false;
        outMesh.renderOrder = -1;
        obj.add(outMesh);
        outlineMeshes.push(outMesh);
      }
    }
  }

  _replacedOrigMatsMap.set(root, [...replacedOrigMats]);

  return Object.freeze({
    presetKey: params.key,
    meshCount: sourceMeshes.length,
    outlineCount: outlineMeshes.length,
    setResolution(w, h) {
      for (const om of outlineMats) {
        om.uniforms.uResolution.value.set(Math.max(1, w), Math.max(1, h));
      }
    },
    setEnvMap(nextEnvMap) {
      const targetEnv = nextEnvMap || null;
      for (const styledList of styledMeshes) {
        for (const sm of styledList) {
          if (sm && sm.isMeshStandardMaterial && 'envMap' in sm && sm.envMap !== targetEnv) {
            sm.envMap = targetEnv;
            sm.needsUpdate = true;
          }
        }
      }
    },
    getOutlineMaterials() {
      return outlineMats.slice();
    },
    dispose() {
      cleanupAppliedStyle(root);
    },
  });
}
