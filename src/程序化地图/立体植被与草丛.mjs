import * as THREE from 'three';
import {LOD_TRANSITION_CONFIG, createSharedTransitionUniforms} from './过渡动画管线.mjs?v=680p-v9';

/**
 * 解码 SCVT 全图 343,234 棵树 72x72 (125m 格) 空间分块索引。
 */
export async function decodeSCVTTreeIndex(compressedBuffer) {
  let rawBuffer = compressedBuffer;
  const check = new DataView(compressedBuffer);
  if (check.byteLength < 32 || check.getUint32(0, true) !== 0x54564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compressedBuffer));
    writer.close();
    rawBuffer = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuffer);
  if (dv.getUint32(0, true) !== 0x54564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVT 树木空间索引头部无效');
  }
  const gridX = dv.getUint32(8, true);
  const gridZ = dv.getUint32(12, true);
  const totalTrees = dv.getUint32(16, true);
  const minX = dv.getFloat32(20, true);
  const minZ = dv.getFloat32(24, true);
  const chunkM = dv.getFloat32(28, true);

  const cellCount = gridX * gridZ;
  const cellOffsets = new Uint32Array(cellCount);
  const cellCounts = new Uint32Array(cellCount);
  for (let i = 0; i < cellCount; i++) {
    cellOffsets[i] = dv.getUint32(32 + i * 8, true);
    cellCounts[i] = dv.getUint32(32 + i * 8 + 4, true);
  }
  const recordsOffset = 32 + cellCount * 8;
  const recordsBytes = new Uint8Array(rawBuffer, recordsOffset, totalTrees * 10);
  const recordsView = new DataView(rawBuffer, recordsOffset, totalTrees * 10);

  return {
    gridX,
    gridZ,
    totalTrees,
    minX,
    minZ,
    chunkM,
    cellOffsets,
    cellCounts,
    recordsBytes,
    recordsView,
    bytes: rawBuffer.byteLength,
  };
}

/**
 * 构建草地权重单通道纹理（来自 loadSCVSSplatControl 的 grassData）。
 */
export function createGrassMaskTexture(splatControl) {
  const {width, height, grassData} = splatControl;
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    const g = grassData[i];
    rgba[i * 4] = g;
    rgba[i * 4 + 1] = g;
    rgba[i * 4 + 2] = g;
    rgba[i * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = THREE.NoColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.flipY = false;
  tex.needsUpdate = true;
  return tex;
}

/**
 * 为树木/代理/远景林冠材质注入：
 * 1) 8x8 Bayer 屏幕空间距离环带连续交叉溶解（0 闪屏、0 突兀跳变）；
 * 2) 世界坐标相位微风摆动；
 * 3) 球形法线冠层明暗体积感与底部 AO 增强。
 */
export function enhanceFoliageMaterial(material, {
  fadeMode = 'near', // 'near' | 'mid' | 'canopy'
  crownHeight = 14.0,
  calibratedRGB = null,
  sharedUniforms,
}) {
  const fCfg = LOD_TRANSITION_CONFIG.foliage;
  const nd0 = fCfg.nearDist[0].toFixed(1), nd1 = fCfg.nearDist[1].toFixed(1);
  const ns0 = fCfg.nearSpan[0].toFixed(1), ns1 = fCfg.nearSpan[1].toFixed(1);
  const md0 = fCfg.midDist[0].toFixed(1), md1 = fCfg.midDist[1].toFixed(1);
  const ms0 = fCfg.midSpan[0].toFixed(1), ms1 = fCfg.midSpan[1].toFixed(1);
  const cd0 = fCfg.canopyDist[0].toFixed(1), cd1 = fCfg.canopyDist[1].toFixed(1);
  const cs0 = fCfg.canopySpan[0].toFixed(1), cs1 = fCfg.canopySpan[1].toFixed(1);
  const ht = fCfg.handoffT.toFixed(3);

  material.side = fadeMode === 'mid' ? THREE.FrontSide : THREE.DoubleSide;
  if (material.roughness !== undefined) material.roughness = 0.90;
  if (material.metalness !== undefined) material.metalness = 0.0;
  if (fadeMode === 'mid') {
    material.transparent = false;
    material.alphaTest = 0.0;
  } else if (fadeMode === 'canopy') {
    material.transparent = false;
    material.alphaTest = 0.5;
  } else {
    material.alphaTest = Math.max(0.26, material.alphaTest || 0.30);
  }

  material.onBeforeCompile = shader => {
    shader.uniforms.uCameraCenter = sharedUniforms.uCameraCenter;
    shader.uniforms.uCameraSpan = sharedUniforms.uCameraSpan;
    shader.uniforms.uSmoothSpan = sharedUniforms.uSmoothSpan || sharedUniforms.uCameraSpan;
    shader.uniforms.uHazeStrength = sharedUniforms.uHazeStrength || {value: 0.0};
    shader.uniforms.uWindTime = sharedUniforms.uWindTime;
    shader.uniforms.uDitherEnabled = sharedUniforms.uDitherEnabled;
    shader.uniforms.uCrownHeight = {value: crownHeight};
    shader.uniforms.uCalibratedTint = {
      value: calibratedRGB
        ? new THREE.Vector3(calibratedRGB[0], calibratedRGB[1], calibratedRGB[2])
        : new THREE.Vector3(1, 1, 1),
    };

    shader.vertexShader = `
      uniform vec3 uCameraCenter;
      uniform float uCameraSpan;
      uniform float uSmoothSpan;
      uniform float uWindTime;
      uniform float uCrownHeight;
      uniform float uDitherEnabled;
      varying vec3 vFoliageWorldPos;
      varying float vLocalHeightFrac;
      varying float vFoliageMorph;
    ` + shader.vertexShader;

    const vertMorph = fadeMode === 'near'
      ? `
      #ifdef USE_INSTANCING
        vec4 instOriginF = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
        float fDist = length(instOriginF.xz - uCameraCenter.xz);
        float fT = uDitherEnabled > 0.5 ? max(smoothstep(${nd0}, ${nd1}, fDist), smoothstep(${ns0}, ${ns1}, uSmoothSpan)) : 0.0;
        vFoliageMorph = fT;
        float windWeight = smoothstep(0.15, 0.85, vLocalHeightFrac) * (1.0 - smoothstep(0.65, ${ht}, fT));
        float phase = uWindTime * 1.85 + instOriginF.x * 0.43 + instOriginF.z * 0.31;
        transformed.x += sin(phase) * 0.16 * windWeight;
        transformed.z += cos(phase * 0.87) * 0.12 * windWeight;
        // 近景树在切换末段柔和向核心收缩，消除瞬间切换锯齿感
        if (fT > 0.62) {
          float crownShrink = 1.0 - 0.32 * smoothstep(0.62, ${ht}, fT);
          transformed *= crownShrink;
        }
        vec4 instW = modelMatrix * instanceMatrix * vec4(transformed, 1.0);
        vFoliageWorldPos = instW.xyz;
      #else
        vFoliageMorph = 0.0;
        vec4 wPosF = modelMatrix * vec4(transformed, 1.0);
        vFoliageWorldPos = wPosF.xyz;
      #endif
      `
      : fadeMode === 'mid'
      ? `
      #ifdef USE_INSTANCING
        vec4 instOriginF = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
        float fDist = length(instOriginF.xz - uCameraCenter.xz);
        float nearT = max(smoothstep(${nd0}, ${nd1}, fDist), smoothstep(${ns0}, ${ns1}, uSmoothSpan));
        float farT = max(smoothstep(${md0}, ${md1}, fDist), smoothstep(${ms0}, ${ms1}, uSmoothSpan));
        // 与 near 严格在 ${ht} 互补接棒（0 重叠 Z-fighting 打架）
        vFoliageMorph = uDitherEnabled > 0.5 ? (nearT < ${ht} ? -1.0 : farT) : 0.0;
        if (uDitherEnabled > 0.5 && farT > 0.01) {
          float sink = 1.0 - smoothstep(0.0, 0.96, farT);
          transformed.y *= max(0.01, sink);
          transformed.xz *= mix(0.35, 1.0, sink);
        }
        vec4 instW = modelMatrix * instanceMatrix * vec4(transformed, 1.0);
        vFoliageWorldPos = instW.xyz;
      #else
        vFoliageMorph = 0.0;
        vec4 wPosF = modelMatrix * vec4(transformed, 1.0);
        vFoliageWorldPos = wPosF.xyz;
      #endif
      `
      : `
      vec4 wPos0 = modelMatrix * vec4(transformed, 1.0);
      float cDist = length(wPos0.xz - uCameraCenter.xz);
      float rise = uDitherEnabled > 0.5
        ? max(smoothstep(${cd0}, ${cd1}, cDist), smoothstep(${cs0}, ${cs1}, uSmoothSpan))
        : 1.0;
      vFoliageMorph = rise;
      transformed.y -= (1.0 - rise) * 16.0;
      vec4 wPosF = modelMatrix * vec4(transformed, 1.0);
      vFoliageWorldPos = wPosF.xyz;
      `;

    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
      vLocalHeightFrac = clamp(position.y / max(uCrownHeight, 2.0), 0.0, 1.0);
      ${vertMorph}
      `
    );

    shader.fragmentShader = `
      uniform vec3 uCameraCenter;
      uniform float uCameraSpan;
      uniform float uDitherEnabled;
      uniform float uHazeStrength;
      uniform vec3 uCalibratedTint;
      varying vec3 vFoliageWorldPos;
      varying float vLocalHeightFrac;
      varying float vFoliageMorph;
    ` + shader.fragmentShader;

    const fadeLogic = fadeMode === 'near'
      ? `
        if (uDitherEnabled > 0.5 && vFoliageMorph >= ${ht}) discard;
        float sssGlow = smoothstep(0.18, 0.95, vLocalHeightFrac);
        diffuseColor.rgb *= mix(0.86, 1.12, sssGlow);
        // 交接末段柔和向校准色调融合，平滑衔接中景代理
        if (uDitherEnabled > 0.5 && vFoliageMorph > 0.35) {
          vec3 nearEndTint = max(uCalibratedTint * uCalibratedTint * 1.12, vec3(0.045, 0.095, 0.025));
          diffuseColor.rgb = mix(diffuseColor.rgb, nearEndTint, smoothstep(0.35, ${ht}, vFoliageMorph) * 0.42);
        }
      `
      : fadeMode === 'mid'
      ? `
        if (uDitherEnabled > 0.5 && (vFoliageMorph < -0.5 || vFoliageMorph >= 0.95)) discard;
        vec3 canopyTint = max(uCalibratedTint * uCalibratedTint * 1.15, vec3(0.045, 0.095, 0.025));
        diffuseColor.rgb = mix(diffuseColor.rgb * mix(0.92, 1.16, vLocalHeightFrac), canopyTint, clamp(vFoliageMorph, 0.0, 1.0) * 0.65);
        if (uDitherEnabled > 0.5 && uHazeStrength > 0.002) {
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.68, 0.72, 0.64), clamp(uHazeStrength * 0.55, 0.0, 0.20));
        }
      `
      : `
        if (uDitherEnabled > 0.5 && vFoliageMorph < 0.04) discard;
        float canopyMicro = 0.92 + 0.14 * sin(vFoliageWorldPos.x * 0.22 + cos(vFoliageWorldPos.z * 0.19))
                                  + 0.08 * cos(vFoliageWorldPos.z * 0.55 - vFoliageWorldPos.x * 0.47);
        diffuseColor.rgb *= canopyMicro;
        if (uDitherEnabled > 0.5 && uHazeStrength > 0.002) {
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.68, 0.72, 0.64), clamp(uHazeStrength * 0.55, 0.0, 0.20));
        }
      `;

    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <alphatest_fragment>',
      `#include <alphatest_fragment>
      ${fadeLogic}
      `
    );

    // 针对近景与中景树冠/灌木：
    // 1) 禁用 DoubleSide 对烘焙球形法线的背面取反，彻底消除交叉/外倾树叶卡片背面变黑形成“迷彩斑”的问题！
    // 2) 注入温和次表面透光 (Warm Subsurface Scattering)，中景略加背光托底消除 460m 死暗。
    if (fadeMode === 'near' || fadeMode === 'mid') {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <normal_fragment_begin>',
        `
        float faceDirection = 1.0;
        float scvFoliageNLenSq = dot( vNormal, vNormal );
        vec3 normal = scvFoliageNLenSq > 1e-10 ? vNormal * inversesqrt( scvFoliageNLenSq ) : vec3( 0.0, 0.0, 1.0 );
        vec3 nonPerturbedNormal = normal;
        `
      );
      const sssFloor = fadeMode === 'mid' ? '0.10 + 0.08 * vLocalHeightFrac' : '0.05 + 0.06 * vLocalHeightFrac';
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <emissivemap_fragment>',
        `#include <emissivemap_fragment>
        vec3 sssTint = diffuseColor.rgb * vec3(1.06, 1.12, 0.86);
        totalEmissiveRadiance += sssTint * (${sssFloor});
        `
      );
    }
  };
  material.customProgramCacheKey = () => `scavenger-b2-foliage-${fadeMode}-v8-solidmid`;
}

/**
 * 为高大近景乔木生成与主体裁剪同步的 DepthMaterial，使每棵树阴影随距离渐收退场，消除 385m 整屏阴影硬切闪烁。
 */
function createFoliageDepthMaterial(srcMaterial, sharedUniforms) {
  const fCfg = LOD_TRANSITION_CONFIG.foliage;
  const nd0 = fCfg.nearDist[0].toFixed(1), nd1 = fCfg.nearDist[1].toFixed(1);
  const ns0 = fCfg.nearSpan[0].toFixed(1), ns1 = fCfg.nearSpan[1].toFixed(1);
  const ht = fCfg.handoffT.toFixed(3);
  const dm = new THREE.MeshDepthMaterial({
    depthPacking: THREE.RGBADepthPacking,
    map: srcMaterial.map || null,
    alphaMap: srcMaterial.alphaMap || null,
    alphaTest: srcMaterial.alphaTest || 0.26,
    side: THREE.DoubleSide,
  });
  dm.onBeforeCompile = shader => {
    shader.uniforms.uCameraCenter = sharedUniforms.uCameraCenter;
    shader.uniforms.uSmoothSpan = sharedUniforms.uSmoothSpan || sharedUniforms.uCameraSpan;
    shader.uniforms.uDitherEnabled = sharedUniforms.uDitherEnabled;
    shader.vertexShader = `
      uniform vec3 uCameraCenter;
      uniform float uSmoothSpan;
      uniform float uDitherEnabled;
      varying float vDepthNearT;
    ` + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
      #ifdef USE_INSTANCING
        vec4 instOriginFD = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
        float fdDist = length(instOriginFD.xz - uCameraCenter.xz);
        float fdNearT = uDitherEnabled > 0.5 ? max(smoothstep(${nd0}, ${nd1}, fdDist), smoothstep(${ns0}, ${ns1}, uSmoothSpan)) : 0.0;
        vDepthNearT = fdNearT;
        if (fdNearT > 0.62) {
          float crownShrinkD = 1.0 - 0.32 * smoothstep(0.62, ${ht}, fdNearT);
          transformed *= crownShrinkD;
        }
      #else
        vDepthNearT = 0.0;
      #endif
      `
    );
    shader.fragmentShader = `
      uniform float uDitherEnabled;
      varying float vDepthNearT;
    ` + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <alphatest_fragment>',
      `#include <alphatest_fragment>
      if (uDitherEnabled > 0.5 && vDepthNearT >= ${ht}) discard;
      `
    );
  };
  dm.customProgramCacheKey = () => 'scavenger-b2-foliage-depth-v8';
  return dm;
}

/**
 * 构建 3 片外展微弯宽叶组合而成的饱满微缩 3D 聚簇草团几何体（每团仅 15 顶点 / 9 三角面，0 AlphaTest，纯不透明 Opaque Early-Z）。
 */
function createClusteredGrassClumpGeometry(bladeW = 0.34, bladeH = 0.72) {
  const leafAngles = [0.0, 2.06, 4.22]; // ~0° / 118° / 242°
  const leafScales = [
    {h: 1.00, w: 1.05, rOff: 0.00, lean: 0.42},
    {h: 0.86, w: 0.95, rOff: 0.16, lean: 0.48},
    {h: 0.78, w: 0.90, rOff: 0.15, lean: 0.52},
  ];
  const positions = new Float32Array(3 * 5 * 3);
  const uvs = new Float32Array(3 * 5 * 2);
  const indices = [];

  for (let li = 0; li < 3; li++) {
    const yaw = leafAngles[li];
    const spec = leafScales[li];
    const cy = Math.cos(yaw);
    const sy = Math.sin(yaw);
    const ox = Math.sin(yaw + 0.5) * spec.rOff;
    const oz = Math.cos(yaw + 0.5) * spec.rOff;
    const w0 = bladeW * spec.w;
    const h0 = bladeH * spec.h;
    const l0 = h0 * spec.lean;

    const pts = [
      [-w0 * 0.50, 0.0,       0.0],
      [ w0 * 0.50, 0.0,       0.0],
      [-w0 * 0.34, h0 * 0.54, l0 * 0.38],
      [ w0 * 0.34, h0 * 0.54, l0 * 0.38],
      [ 0.0,       h0,        l0],
    ];
    const uvPts = [
      [0.0, 0.0],
      [1.0, 0.0],
      [0.0, 0.54],
      [1.0, 0.54],
      [0.5, 1.0],
    ];
    const vBase = li * 5;
    for (let k = 0; k < 5; k++) {
      const [lx, ly, lz] = pts[k];
      const pi = (vBase + k) * 3;
      positions[pi + 0] = ox + lx * cy - lz * sy;
      positions[pi + 1] = ly;
      positions[pi + 2] = oz + lx * sy + lz * cy;
      const ui = (vBase + k) * 2;
      uvs[ui + 0] = uvPts[k][0];
      uvs[ui + 1] = uvPts[k][1];
    }
    indices.push(
      vBase + 0, vBase + 1, vBase + 2,
      vBase + 1, vBase + 3, vBase + 2,
      vBase + 2, vBase + 3, vBase + 4,
    );
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

/**
 * CPU 预烘焙 256×256 RGBA 无缝平铺有机草绒与生态噪声纹理（仅占 256 KiB 显存，初始化 < 6 ms）：
 * - R 通道：周期性有机草绒锥体高度场 A（24×24 晶胞，每晶胞 3 簇随机抖动草茎）；
 * - G 通道：错位周期有机草绒锥体高度场 B（30×30 晶胞，独立相位，消除平铺重复感）；
 * - B 通道：低频宏观生态斑块噪声（替代片元实时 fBm 斑块计算）；
 * - A 通道：路边碎草柔边双频噪声（替代片元实时路边碎边噪声计算）。
 */
export function createPrebakedGrassFieldTexture(size = 256) {
  const data = new Uint8Array(size * size * 4);
  const wrapMod = (v, m) => ((v % m) + m) % m;
  const hash2 = (ix, iy, seed) => {
    let h = Math.imul(ix, 374761393) ^ Math.imul(iy, 668265263) ^ Math.imul(seed, 1442695041);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  };

  // 预建晶胞平坦表，避免在 256×256×9×3 内层循环中重复计算数百万次哈希
  const buildTuftField = (gridN, seed) => {
    const table = new Float32Array(gridN * gridN * 3 * 5);
    let ptr = 0;
    for (let cy = 0; cy < gridN; cy++) {
      for (let cx = 0; cx < gridN; cx++) {
        for (let k = 0; k < 3; k++) {
          const sBase = seed + k * 7919;
          const jx = 0.12 + 0.76 * hash2(cx, cy, sBase + 11);
          const jy = 0.12 + 0.76 * hash2(cx, cy, sBase + 29);
          const hCell = 0.44 + 0.56 * hash2(cx, cy, sBase + 47);
          const rCell = 0.38 + 0.14 * hash2(cx, cy, sBase + 71);
          table[ptr++] = jx;
          table[ptr++] = jy;
          table[ptr++] = hCell;
          table[ptr++] = rCell;
          table[ptr++] = rCell * rCell;
        }
      }
    }
    return {gridN, table};
  };

  const sampleTuftField = (u, v, field) => {
    const gridN = field.gridN;
    const table = field.table;
    const px = u * gridN;
    const py = v * gridN;
    const bx = Math.floor(px);
    const by = Math.floor(py);
    let maxH = 0;
    for (let oy = -1; oy <= 1; oy++) {
      const cy = wrapMod(by + oy, gridN);
      const cellOriginY = by + oy;
      for (let ox = -1; ox <= 1; ox++) {
        const cx = wrapMod(bx + ox, gridN);
        const cellOriginX = bx + ox;
        let ptr = (cy * gridN + cx) * 15;
        for (let k = 0; k < 3; k++) {
          const dx = px - (cellOriginX + table[ptr]);
          const dy = py - (cellOriginY + table[ptr + 1]);
          const distSq = dx * dx + dy * dy;
          const rSq = table[ptr + 4];
          if (distSq < rSq) {
            const normD = Math.sqrt(distSq) / table[ptr + 3];
            const radial = normD <= 0.26 ? 1.0 : Math.max(0, 1.0 - (normD - 0.26) / 0.74);
            const coneH = table[ptr + 2] * Math.pow(radial, 0.56);
            if (coneH > maxH) maxH = coneH;
          }
          ptr += 5;
        }
      }
    }
    return maxH >= 1 ? 255 : Math.max(0, Math.round(maxH * 255));
  };

  const buildNoiseGrid = (freq, seed) => {
    const vals = new Float32Array(freq * freq);
    for (let y = 0; y < freq; y++) {
      for (let x = 0; x < freq; x++) {
        vals[y * freq + x] = hash2(x, y, seed);
      }
    }
    return {freq, vals};
  };

  const sampleNoiseGrid = (u, v, grid) => {
    const freq = grid.freq;
    const vals = grid.vals;
    const fx = u * freq;
    const fy = v * freq;
    const i0x = Math.floor(fx);
    const i0y = Math.floor(fy);
    const tx = fx - i0x;
    const ty = fy - i0y;
    const sx = tx * tx * (3 - 2 * tx);
    const sy = ty * ty * (3 - 2 * ty);
    const x0 = wrapMod(i0x, freq);
    const y0 = wrapMod(i0y, freq);
    const x1 = x0 + 1 < freq ? x0 + 1 : 0;
    const y1 = y0 + 1 < freq ? y0 + 1 : 0;
    const n00 = vals[y0 * freq + x0];
    const n10 = vals[y0 * freq + x1];
    const n01 = vals[y1 * freq + x0];
    const n11 = vals[y1 * freq + x1];
    const nx0 = n00 + (n10 - n00) * sx;
    const nx1 = n01 + (n11 - n01) * sx;
    return nx0 + (nx1 - nx0) * sy;
  };

  const fieldA = buildTuftField(24, 17);
  const fieldB = buildTuftField(30, 53);
  const noiseMacro1 = buildNoiseGrid(4, 101);
  const noiseMacro2 = buildNoiseGrid(11, 202);
  const noiseEdge1 = buildNoiseGrid(8, 303);
  const noiseEdge2 = buildNoiseGrid(24, 404);

  for (let py = 0; py < size; py++) {
    const v = (py + 0.5) / size;
    for (let px = 0; px < size; px++) {
      const u = (px + 0.5) / size;
      const idx = (py * size + px) * 4;
      data[idx + 0] = sampleTuftField(u, v, fieldA);
      data[idx + 1] = sampleTuftField(u, v, fieldB);
      const macroB = 0.58 * sampleNoiseGrid(u, v, noiseMacro1) + 0.42 * sampleNoiseGrid(u, v, noiseMacro2);
      const edgeA = 0.62 * sampleNoiseGrid(u, v, noiseEdge1) + 0.38 * sampleNoiseGrid(u, v, noiseEdge2);
      data[idx + 2] = Math.max(0, Math.min(255, Math.round(macroB * 255)));
      data[idx + 3] = Math.max(0, Math.min(255, Math.round(edgeA * 255)));
    }
  }

  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.magFilter = THREE.LinearFilter;
  // 关闭 mipmap：保持 680p 复古微缩下 70m–214m 细腻草绒肌理，避免远距被均值化糊成平面，且少 1 次 mip 计算更省带宽
  tex.minFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.colorSpace = THREE.NoColorSpace;
  tex.needsUpdate = true;
  return tex;
}

/**
 * 方案 A 全新风格化高性能 GPU 草地系统（彻底移除 4 层 Shell 大草毯，0 discard / 0 锯齿 / 0 白沙噪点）：
 * - 底层：交由程序化地形着色器融合 `256×256` 微缩草绒笔触、2m 精确路边柔化与柔风草浪（0 额外草毯面数与 Overdraw）；
 * - 表层：仅在近景（span <= 155m）按 130m/175m 动态滑窗提交 2,200–3,200 个 3 片交叉饱满 3D 聚簇草团（每团仅 9 面，0 AlphaTest）；
 * - 5 点十字 2m 路肩安全退缩 + 视距圆框零纹理早剔除 + 同源地形根部色调自适应。
 */
export class ProceduralGrassField {
  constructor({
    heightTex,
    grassMaskTex,
    macroTintTex,
    bounds,
    sharedUniforms,
    grassFieldTex = null,
    terrainGrassStrengthUniform = null,
    maxInstances = 3600,
    linearOutput = false,
  }) {
    this.maxInstances = maxInstances;
    this.tier = 'high'; // 'high' (地表草绒+3D草团) | 'medium' (仅地表草绒) | 'off' (全关)
    this.activeBlades = maxInstances;
    this.activeShellLayers = 1; // 1 表示地表融合草绒已启用，0 表示已关闭
    this.terrainGrassStrengthUniform = terrainGrassStrengthUniform;
    this.linearOutput = Boolean(linearOutput);

    this.root = new THREE.Group();
    this.root.name = 'ProceduralStylizedGrassRoot';
    // 兼容外部 this.grassField.mesh 的访问习惯
    this.mesh = this.root;

    this._ownsGrassFieldTex = !grassFieldTex;
    this.grassFieldTex = grassFieldTex || createPrebakedGrassFieldTexture(256);

    this.uniforms = {
      uCameraCenter: sharedUniforms.uCameraCenter,
      uCameraSpan: sharedUniforms.uCameraSpan,
      uWindTime: sharedUniforms.uWindTime,
      uHeightMap: {value: heightTex.texture},
      uHeightTexSize: {value: new THREE.Vector2(heightTex.width, heightTex.height)},
      uMapBounds: {value: new THREE.Vector4(bounds.minX, bounds.minZ, bounds.maxX - bounds.minX, bounds.maxZ - bounds.minZ)},
      uHeightRange: {value: new THREE.Vector2(bounds.minH ?? -40.0, (bounds.maxH ?? 120.0) - (bounds.minH ?? -40.0))},
      uGrassMask: {value: grassMaskTex},
      uMacroTint: {value: macroTintTex},
      uGrassFieldTex: {value: this.grassFieldTex},
      uBladeWindowSize: {value: LOD_TRANSITION_CONFIG.grass.fixedWindowSize},
      uLinearOutput: {value: this.linearOutput ? 1.0 : 0.0},
    };

    this._buildClusteredBladeMesh();
    this.root.add(this.bladeMesh);
    this.updateDistanceLOD(sharedUniforms.uCameraSpan?.value ?? 135);
  }

  _fastHeightSamplerGLSL() {
    return `
      float sampleRoadRoleFast(vec2 worldXZ) {
        vec2 uv = clamp((worldXZ - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
        return texture2D(uHeightMap, uv).a * 255.0;
      }
      bool isRoadOrWaterRole(float gRole) {
        return (gRole >= 1.5 && gRole <= 3.5) || (gRole >= 4.5 && gRole <= 5.5);
      }
      float sampleGroundHeightFast(vec2 worldXZ, out float outRole) {
        vec2 uv = clamp((worldXZ - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
        vec4 s = texture2D(uHeightMap, uv);
        float q = floor(s.r * 255.0 + 0.5) + floor(s.g * 255.0 + 0.5) * 256.0;
        outRole = s.a * 255.0;
        return uHeightRange.x + (min(q, 65534.0) / 65534.0) * uHeightRange.y;
      }
    `;
  }

  _buildClusteredBladeMesh() {
    // 2,200–3,200 个 3 片交叉饱满 3D 聚簇草团（130m/175m 动态滑窗，仅 1 Draw Call，纯不透明 Opaque）
    const clumpBase = createClusteredGrassClumpGeometry(0.34, 0.72);
    this.bladeGeo = new THREE.InstancedBufferGeometry();
    this.bladeGeo.index = clumpBase.index;
    this.bladeGeo.attributes.position = clumpBase.attributes.position;
    this.bladeGeo.attributes.uv = clumpBase.attributes.uv;
    this.bladeGeo.instanceCount = this.maxInstances;

    const slots = new Float32Array(this.maxInstances * 4);  // u, v in [0,1), yaw, tintRand
    const clumps = new Float32Array(this.maxInstances * 2); // scaleY, lean

    let seed = 42917;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed / 4294967296;
    };

    // 在 [0, 1) 环形空间内先散布 520 个自然聚簇中心，并保留 32% 均匀散点兼顾聚落感与铺展自然度
    const centers = [];
    for (let c = 0; c < 520; c++) {
      centers.push([rnd(), rnd(), 0.015 + rnd() * 0.032]);
    }

    for (let i = 0; i < this.maxInstances; i++) {
      let u, v;
      if (rnd() < 0.68) {
        const cl = centers[Math.floor(rnd() * centers.length)];
        const ang = rnd() * Math.PI * 2.0;
        const rad = Math.sqrt(rnd()) * cl[2];
        u = (cl[0] + Math.cos(ang) * rad + 1.0) % 1.0;
        v = (cl[1] + Math.sin(ang) * rad + 1.0) % 1.0;
      } else {
        u = rnd();
        v = rnd();
      }
      slots[i * 4 + 0] = u;
      slots[i * 4 + 1] = v;
      slots[i * 4 + 2] = rnd() * Math.PI * 2.0;
      slots[i * 4 + 3] = rnd();
      clumps[i * 2 + 0] = 0.68 + rnd() * 0.66;
      clumps[i * 2 + 1] = 0.72 + rnd() * 0.56;
    }

    this.bladeGeo.setAttribute('aGrassSlot', new THREE.InstancedBufferAttribute(slots, 4));
    this.bladeGeo.setAttribute('aGrassClump', new THREE.InstancedBufferAttribute(clumps, 2));
    clumpBase.dispose();

    this.bladeMat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      side: THREE.DoubleSide,
      vertexShader: `
        attribute vec4 aGrassSlot;
        attribute vec2 aGrassClump;
        uniform vec3 uCameraCenter;
        uniform float uCameraSpan;
        uniform float uWindTime;
        uniform sampler2D uHeightMap;
        uniform vec2 uHeightTexSize;
        uniform vec4 uMapBounds;
        uniform vec2 uHeightRange;
        uniform sampler2D uGrassMask;
        uniform sampler2D uMacroTint;
        uniform sampler2D uGrassFieldTex;
        uniform float uBladeWindowSize;

        varying float vHeightFrac;
        varying vec3 vBaseLin;
        varying float vWindWave;
        varying float vTintRand;
        varying float vShade;

        ${this._fastHeightSamplerGLSL()}

        float windNoise(vec2 p) {
          return sin(p.x * 0.14 + uWindTime * 2.05 + cos(p.y * 0.11 + uWindTime * 1.35)) * 0.55
               + sin(p.x * 0.29 - p.y * 0.23 + uWindTime * 3.10) * 0.30
               + cos(p.y * 0.40 + uWindTime * 1.85) * 0.15;
        }

        void main() {
          // Toroidal 环形取模锁定世界网格：镜头在 9km 任意移动 0 CPU 更新
          float win = uBladeWindowSize;
          vec2 cellOrigin = floor((uCameraCenter.xz - win * 0.5) / win) * win;
          vec2 wxz = cellOrigin + aGrassSlot.xy * win;
          vec2 dCam = wxz - uCameraCenter.xz;
          if (dCam.x < -win * 0.5) wxz.x += win;
          if (dCam.x >  win * 0.5) wxz.x -= win;
          if (dCam.y < -win * 0.5) wxz.y += win;
          if (dCam.y >  win * 0.5) wxz.y -= win;

          // 第 1 级零纹理视距圆框早剔除：超出当前视距半径直接移出 NDC 裁剪体
          vec2 dCamFinal = wxz - uCameraCenter.xz;
          float safeMaxR = min(82.0, win * 0.48);
          float maxActiveR = min(safeMaxR + 1.0, max(54.0, uCameraSpan * 0.90));
          float distSq = dot(dCamFinal, dCamFinal);
          if (distSq > maxActiveR * maxActiveR) {
            gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
            return;
          }

          float gRole = 0.0;
          float groundY = sampleGroundHeightFast(wxz, gRole);
          // 5 点十字 2.6m 道面/水域安全退缩检查，彻底杜绝路肩黄土带上长孤草
          if (isRoadOrWaterRole(gRole)
              || isRoadOrWaterRole(sampleRoadRoleFast(wxz + vec2(2.6, 0.0)))
              || isRoadOrWaterRole(sampleRoadRoleFast(wxz - vec2(2.6, 0.0)))
              || isRoadOrWaterRole(sampleRoadRoleFast(wxz + vec2(0.0, 2.6)))
              || isRoadOrWaterRole(sampleRoadRoleFast(wxz - vec2(0.0, 2.6)))) {
            gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
            return;
          }

          vec2 mapUV = clamp((wxz - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
          float rawGrassWeight = texture2D(uGrassMask, mapUV).r;
          vec4 lowNoise = texture2D(uGrassFieldTex, wxz * 0.028);
          float grassWeight = rawGrassWeight + (lowNoise.a - 0.50) * 0.16 * smoothstep(0.10, 0.42, rawGrassWeight);

          float dist = sqrt(distSq);
          float distFade = 1.0 - smoothstep(safeMaxR * 0.66, safeMaxR, dist);
          float spanFade = 1.0 - smoothstep(${LOD_TRANSITION_CONFIG.grass.fadeSpan[0].toFixed(1)}, ${LOD_TRANSITION_CONFIG.grass.fadeSpan[1].toFixed(1)}, uCameraSpan);
          float activeScale = smoothstep(0.28, 0.56, grassWeight) * distFade * spanFade;

          if (activeScale < 0.04) {
            gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
            return;
          }

          vHeightFrac = uv.y;
          vTintRand = aGrassSlot.w;

          // 与地形融合微缩草绒严格同源的根部生态基色，并校准至场景 PBR 实际受光亮度，消除深色孤刺反差
          vec3 mt = max(texture2D(uMacroTint, mapUV).rgb, vec3(0.03));
          vec3 groundLin = mt * mt;
          float macroPatch = lowNoise.b;
          vec3 freshLush = vec3(0.062, 0.156, 0.025);
          vec3 freshWarm = vec3(0.112, 0.162, 0.036);
          vec3 stylizedBase = mix(freshLush, freshWarm, smoothstep(0.24, 0.80, macroPatch));
          vec3 ecoBase = groundLin * vec3(0.74, 1.06, 0.56) + vec3(0.014, 0.042, 0.008);
          vBaseLin = mix(ecoBase, stylizedBase, 0.68) * 1.48;

          float yaw = aGrassSlot.z;
          float scY = aGrassClump.x * (0.76 + 0.46 * grassWeight) * activeScale;
          float lean = aGrassClump.y;

          vec3 local = position;
          local.x *= 1.18 * (0.72 + 0.28 * activeScale);
          local.y *= scY;
          local.z *= 1.18 * (0.72 + 0.28 * activeScale) * lean;

          float cy = cos(yaw), sy = sin(yaw);
          vec3 rotated = vec3(
            local.x * cy - local.z * sy,
            local.y,
            local.x * sy + local.z * cy
          );

          float w = windNoise(wxz);
          vWindWave = w;
          float bend = vHeightFrac * vHeightFrac * 0.48 * activeScale;
          rotated.x += (0.24 + w * 0.65) * bend;
          rotated.z += (0.14 + w * 0.35) * bend;
          rotated.y -= abs(w) * 0.10 * bend;

          vec3 worldPos = vec3(wxz.x, groundY - 0.05, wxz.y) + rotated;
          vec3 sunDir = normalize(vec3(-0.62, 0.72, 0.32));
          vec3 fakeNorm = normalize(vec3(sy * 0.38, 0.90, cy * 0.38));
          vShade = 0.88 + 0.16 * max(0.0, dot(fakeNorm, sunDir));

          gl_Position = projectionMatrix * viewMatrix * vec4(worldPos, 1.0);
        }
      `,
      fragmentShader: `
        uniform float uLinearOutput;
        varying float vHeightFrac;
        varying vec3 vBaseLin;
        varying float vWindWave;
        varying float vTintRand;
        varying float vShade;

        void main() {
          vec3 baseLin = vBaseLin;
          vec3 lushTip = mix(vec3(0.095, 0.215, 0.032), vec3(0.185, 0.255, 0.048), vTintRand);
          vec3 dryTip = vec3(0.240, 0.215, 0.058);
          vec3 tipLin = mix(lushTip, dryTip, smoothstep(0.86, 0.98, vTintRand)) * 1.18;

          float h = vHeightFrac;
          vec3 colLin = mix(baseLin, tipLin, smoothstep(0.14, 0.92, h));
          float ao = mix(0.96, 1.14, smoothstep(0.0, 0.90, h));
          float gust = smoothstep(0.25, 0.85, vWindWave) * h * 0.12;
          colLin = colLin * ao * vShade + vec3(0.050, 0.068, 0.015) * gust;

          if (uLinearOutput > 0.5) {
            // 680p HDR Bloom 管线：直接输出物理线性光交由 OutputPass 统一 ACES + sRGB，根治双伽马惨白过曝
            gl_FragColor = vec4(max(colLin * 1.16, vec3(0.0)), 1.0);
          } else {
            // 直渲 sRGB 管线：单次柔和压抑高光并与地形 MeshStandardMaterial 输出色调自然接轨
            vec3 mapped = colLin / (colLin + vec3(0.62));
            vec3 colSRGB = sqrt(max(mapped * 1.34, vec3(0.0)));
            gl_FragColor = vec4(colSRGB, 1.0);
          }
        }
      `,
    });

    this.bladeMesh = new THREE.Mesh(this.bladeGeo, this.bladeMat);
    this.bladeMesh.frustumCulled = false;
    this.bladeMesh.renderOrder = 3;
    this.bladeMesh.name = 'G4ClusteredGrassClumps';
  }

  /**
   * 同步切换线性 HDR / 直渲 sRGB 输出模式，避免 EffectComposer OutputPass 对 3D 草团二次伽马编码发白。
   */
  setLinearOutput(flag) {
    this.linearOutput = Boolean(flag);
    if (this.uniforms?.uLinearOutput) {
      this.uniforms.uLinearOutput.value = this.linearOutput ? 1.0 : 0.0;
    }
  }

  /**
   * 同步刷新 1152x1152 精确草地权重掩码（流式升级完成后调用，确保跑道/道路/水泥地零杂草）。
   */
  setGrassMaskTexture(tex) {
    this.uniforms.uGrassMask.value = tex;
  }

  /**
   * 方案 A 远距离裁剪与固定滑窗（消除 115m 临界全屏草团洗牌瞬移）：
   * - 'off'：关闭地形草绒与 3D 草团（0 DC / 0 面）；
   * - 'medium' 或 span > hardCullSpan (185m)：草团高度已平滑归 0，关闭提交省下无效开销；
   * - 永久锁定 162.0m 环形窗口与固定实例总数，随相机跨度在 120–175m 平滑压低淡出。
   */
  updateDistanceLOD(span) {
    const gCfg = LOD_TRANSITION_CONFIG.grass;
    if (this.tier === 'off') {
      if (this.terrainGrassStrengthUniform) this.terrainGrassStrengthUniform.value = 0.0;
      this.bladeMesh.visible = false;
      this.activeShellLayers = 0;
      this.activeBlades = 0;
      return;
    }

    if (this.terrainGrassStrengthUniform) this.terrainGrassStrengthUniform.value = 1.0;
    this.activeShellLayers = 1;

    if (this.tier === 'medium' || span > gCfg.hardCullSpan) {
      this.bladeMesh.visible = false;
      this.activeBlades = 0;
    } else {
      this.bladeMesh.visible = true;
      this.uniforms.uBladeWindowSize.value = gCfg.fixedWindowSize;
      this.bladeGeo.instanceCount = this.maxInstances;
      this.activeBlades = this.maxInstances;
    }
  }

  setTier(tier) {
    this.tier = tier;
    this.updateDistanceLOD(this.uniforms.uCameraSpan.value);
  }

  getActiveCount() {
    return this.activeBlades + (this.activeShellLayers > 0 ? 1000 : 0);
  }

  dispose() {
    if (this._ownsGrassFieldTex && this.grassFieldTex) this.grassFieldTex.dispose();
    this.bladeGeo.dispose();
    this.bladeMat.dispose();
  }
}

/**
 * 三级立体植被与草丛统一运行时系统（近景球形法线低模树 + 中景立体冠层代理 + 远景常驻集群林冠壳 + 近景 GPU 草丛）。
 */
export class ThreeTierFoliageSystem {
  constructor({
    b2Manifest,
    nearGlbs,
    midGlbs,
    canopyGlb,
    treeSpatialIndex,
    grassAtlasTex,
    heightTex,
    splatControl,
    macroTintTex,
    support,
    grassFieldTex = null,
    terrainGrassStrengthUniform = null,
    sharedUniforms = null,
    linearOutput = false,
  }) {
    this.manifest = b2Manifest;
    this.treeIndex = treeSpatialIndex;
    this.support = support;
    this.root = new THREE.Group();
    this.root.name = 'ThreeTierFoliageRoot';

    this.sharedUniforms = sharedUniforms || createSharedTransitionUniforms();

    // 1. 远景与总览常驻立体集群林冠壳 (> 220m 至 9000m 全程常驻)
    this.canopyRoot = canopyGlb.scene;
    this.canopyRoot.name = 'ForestCanopyClusters';
    this.canopyRoot.traverse(o => {
      if (o.isMesh) {
        o.receiveShadow = true;
        o.castShadow = false;
        o.frustumCulled = false;
        for (const m of [o.material].flat()) {
          if (m) {
            enhanceFoliageMaterial(m, {
              fadeMode: 'canopy',
              crownHeight: 22.0,
              sharedUniforms: this.sharedUniforms,
            });
          }
        }
      }
    });
    this.root.add(this.canopyRoot);

    // 2. 近景 8 种定制球形法线低模树 InstancedMesh + 中景 8 种立体冠层代理 InstancedMesh
    this.nearMaxPerProto = 1600;
    this.midMaxPerProto = 3500;
    this.nearMeshes = [];
    this.midMeshes = [];
    this.protoMeta = b2Manifest.tree_prototypes;

    for (let i = 0; i < 8; i++) {
      const meta = this.protoMeta[i];
      const crownH = Math.max(3.0, meta.bounds_max[1] - meta.bounds_min[1]);

      let nearSrcMesh = null;
      nearGlbs[i].scene.traverse(o => { if (o.isMesh && !nearSrcMesh) nearSrcMesh = o; });
      const nearMat = nearSrcMesh.material.clone();
      enhanceFoliageMaterial(nearMat, {
        fadeMode: 'near',
        crownHeight: crownH,
        calibratedRGB: meta.calibrated_rgb,
        sharedUniforms: this.sharedUniforms,
      });
      const nearInst = new THREE.InstancedMesh(nearSrcMesh.geometry, nearMat, this.nearMaxPerProto);
      // 高大乔木投射真实树影并绑定同步逐棵渐缩 DepthMaterial（消除 385m 整屏阴影硬切）
      nearInst.castShadow = meta.kind !== 'shrub';
      if (nearInst.castShadow) {
        nearInst.customDepthMaterial = createFoliageDepthMaterial(nearMat, this.sharedUniforms);
      }
      nearInst.receiveShadow = false;
      nearInst.frustumCulled = false;
      nearInst.count = 0;
      nearInst.name = `NearTree_${meta.name}`;
      this.root.add(nearInst);
      this.nearMeshes.push(nearInst);

      let midSrcMesh = null;
      midGlbs[i].scene.traverse(o => { if (o.isMesh && !midSrcMesh) midSrcMesh = o; });
      const midMat = midSrcMesh.material.clone();
      enhanceFoliageMaterial(midMat, {
        fadeMode: 'mid',
        crownHeight: crownH,
        calibratedRGB: meta.calibrated_rgb,
        sharedUniforms: this.sharedUniforms,
      });
      const midInst = new THREE.InstancedMesh(midSrcMesh.geometry, midMat, this.midMaxPerProto);
      midInst.castShadow = false;
      midInst.receiveShadow = false;
      midInst.frustumCulled = false;
      midInst.count = 0;
      midInst.name = `MidProxy_${meta.name}`;
      this.root.add(midInst);
      this.midMeshes.push(midInst);
    }

    // 3. 方案 A 高性能微缩草地系统（地形融合草绒 + 130m/175m 动态滑窗 2,200–3,200 聚簇 3D 草团）
    this.grassMaskTex = createGrassMaskTexture(splatControl);
    this.grassField = new ProceduralGrassField({
      heightTex,
      grassMaskTex: this.grassMaskTex,
      macroTintTex,
      bounds: b2Manifest.bounds,
      sharedUniforms: this.sharedUniforms,
      grassFieldTex,
      terrainGrassStrengthUniform,
      maxInstances: 3600,
      linearOutput,
    });
    this.root.add(this.grassField.mesh);

    // 编辑器手工新增/删除树木覆盖表
    this.addedTrees = []; // [{id, proto, x, y, z, yaw, sxz, sy}]
    this.deletedTreeKeys = new Set(); // `${cellIdx}:${localIdx}`

    this._lastCellX = -999;
    this._lastCellZ = -999;
    this._lastSpanBucket = -1;
    this.stats = {
      nearTreeInstances: 0,
      midProxyInstances: 0,
      nearTriangles: 0,
      midTriangles: 0,
      canopyTriangles: b2Manifest.forest_canopy_clusters.simplified_triangles,
      grassTufts: this.grassField.activeBlades,
      grassShellLayers: this.grassField.activeShellLayers,
    };
  }

  setWindTime(t) {
    this.sharedUniforms.uWindTime.value = t;
  }

  setLinearOutput(flag) {
    this.grassField?.setLinearOutput?.(flag);
  }

  /**
   * 同步更新 1152x1152 真实草地权重掩码（供流式升级后调用，避免跑道/道路/水泥广场长草）。
   */
  updateGrassMask(splatControl) {
    const nextTex = createGrassMaskTexture(splatControl);
    this.grassField.setGrassMaskTexture(nextTex);
    if (this.grassMaskTex) this.grassMaskTex.dispose();
    this.grassMaskTex = nextTex;
  }

  setGrassTier(tier) {
    this.grassField.setTier(tier);
    this.stats.grassTufts = this.grassField.activeBlades;
    this.stats.grassShellLayers = this.grassField.activeShellLayers;
  }

  /**
   * 根据相机焦点与视野跨度更新近景/中景树木实例缓冲与 G4 草地四级远距离裁剪。
   */
  update(center, span, force = false) {
    const fCfg = LOD_TRANSITION_CONFIG.foliage;
    this.sharedUniforms.uCameraCenter.value.set(center.x, center.y ?? 0, center.z);
    this.sharedUniforms.uCameraSpan.value = span;
    if (this.canopyRoot) {
      this.canopyRoot.visible = span >= fCfg.canopyMinSpan || (this.sharedUniforms.uDitherEnabled?.value ?? 1.0) < 0.5;
    }
    this.grassField.updateDistanceLOD(span);
    this.stats.grassTufts = this.grassField.activeBlades;
    this.stats.grassShellLayers = this.grassField.activeShellLayers;

    const {gridX, gridZ, minX, minZ, chunkM, cellOffsets, cellCounts, recordsView} = this.treeIndex;
    const subCellX = Math.floor(((center.x - minX) / chunkM) * 2);
    const subCellZ = Math.floor(((center.z - minZ) / chunkM) * 2);
    const spanBucket = span > fCfg.midMaxSpan ? 2 : span > fCfg.nearMaxSpan ? 1 : span > 210 ? 0 : -1;

    if (!force && subCellX === this._lastCellX && subCellZ === this._lastCellZ && spanBucket === this._lastSpanBucket) {
      return this.stats;
    }
    this._lastCellX = subCellX;
    this._lastCellZ = subCellZ;
    this._lastSpanBucket = spanBucket;

    if (span > fCfg.midMaxSpan) {
      for (let i = 0; i < 8; i++) {
        this.nearMeshes[i].count = 0;
        this.nearMeshes[i].visible = false;
        this.midMeshes[i].count = 0;
        this.midMeshes[i].visible = false;
      }
      this.stats.nearTreeInstances = 0;
      this.stats.midProxyInstances = 0;
      this.stats.nearTriangles = 0;
      this.stats.midTriangles = 0;
      return this.stats;
    }

    const cxCenter = Math.floor((center.x - minX) / chunkM);
    const czCenter = Math.floor((center.z - minZ) / chunkM);
    const cellRadius = 7;

    const nearCounts = new Int32Array(8);
    const midCounts = new Int32Array(8);
    const dummy = new THREE.Matrix4();
    const te = dummy.elements;

    // 按由近到远顺序遍历格子，确保近处树木最先填入
    const cells = [];
    for (let dz = -cellRadius; dz <= cellRadius; dz++) {
      const cz = czCenter + dz;
      if (cz < 0 || cz >= gridZ) continue;
      for (let dx = -cellRadius; dx <= cellRadius; dx++) {
        const cx = cxCenter + dx;
        if (cx < 0 || cx >= gridX) continue;
        cells.push({cx, cz, d2: dx * dx + dz * dz});
      }
    }
    cells.sort((a, b) => a.d2 - b.d2);

    const showNear = span <= fCfg.nearMaxSpan;
    // 动态近景视距：覆盖过 nearDist[1] (390m) 及 21:9 远角
    const nearRadius = Math.min(430.0, Math.max(260.0, span * 1.65));

    for (const {cx, cz} of cells) {
      const cellIdx = cz * gridX + cx;
      const count = cellCounts[cellIdx];
      if (count === 0) continue;
      const startRec = cellOffsets[cellIdx];
      const cellMinX = minX + cx * chunkM;
      const cellMinZ = minZ + cz * chunkM;

      for (let k = 0; k < count; k++) {
        if (this.deletedTreeKeys.has(`${cellIdx}:${k}`)) continue;
        const byteOff = (startRec + k) * 10;
        const proto = recordsView.getUint8(byteOff);
        const yawU8 = recordsView.getUint8(byteOff + 1);
        const sxzU8 = recordsView.getUint8(byteOff + 2);
        const syU8 = recordsView.getUint8(byteOff + 3);
        const lxU16 = recordsView.getUint16(byteOff + 4, true);
        const lzU16 = recordsView.getUint16(byteOff + 6, true);
        const wyI16 = recordsView.getInt16(byteOff + 8, true);

        const wx = cellMinX + (lxU16 / 65535.0) * chunkM;
        const wz = cellMinZ + (lzU16 / 65535.0) * chunkM;
        const dist = Math.hypot(wx - center.x, wz - center.z);
        if (dist > 900.0) continue;

        const wy = wyI16 / 200.0;
        const yaw = (yawU8 / 255.0) * Math.PI * 2.0 - Math.PI;
        const sxz = sxzU8 / 64.0;
        const sy = syU8 / 64.0;
        const cy = Math.cos(yaw) * sxz;
        const syaw = Math.sin(yaw) * sxz;

        te[0] = cy;   te[4] = 0;  te[8] = -syaw; te[12] = wx;
        te[1] = 0;    te[5] = sy; te[9] = 0;     te[13] = wy;
        te[2] = syaw; te[6] = 0;  te[10] = cy;   te[14] = wz;
        te[3] = 0;    te[7] = 0;  te[11] = 0;    te[15] = 1;

        if (showNear && dist <= nearRadius && nearCounts[proto] < this.nearMaxPerProto) {
          this.nearMeshes[proto].setMatrixAt(nearCounts[proto]++, dummy);
        }
        if ((dist >= 260.0 || span > 290.0) && dist <= 900.0 && midCounts[proto] < this.midMaxPerProto) {
          this.midMeshes[proto].setMatrixAt(midCounts[proto]++, dummy);
        }
      }
    }

    // 追加编辑器手工新增树木
    for (const t of this.addedTrees) {
      const dist = Math.hypot(t.x - center.x, t.z - center.z);
      if (dist > 900.0) continue;
      const proto = t.proto & 7;
      const cy = Math.cos(t.yaw) * t.sxz;
      const syaw = Math.sin(t.yaw) * t.sxz;
      te[0] = cy;   te[4] = 0;    te[8] = -syaw; te[12] = t.x;
      te[1] = 0;    te[5] = t.sy; te[9] = 0;     te[13] = t.y;
      te[2] = syaw; te[6] = 0;    te[10] = cy;   te[14] = t.z;
      te[3] = 0;    te[7] = 0;    te[11] = 0;    te[15] = 1;
      if (showNear && dist <= nearRadius && nearCounts[proto] < this.nearMaxPerProto) {
        this.nearMeshes[proto].setMatrixAt(nearCounts[proto]++, dummy);
      }
      if (dist >= 250.0 && dist <= 900.0 && midCounts[proto] < this.midMaxPerProto) {
        this.midMeshes[proto].setMatrixAt(midCounts[proto]++, dummy);
      }
    }

    let totalNear = 0;
    let totalMid = 0;
    let nearTris = 0;
    let midTris = 0;
    for (let i = 0; i < 8; i++) {
      const nc = nearCounts[i];
      const mc = midCounts[i];
      this.nearMeshes[i].count = nc;
      this.nearMeshes[i].visible = nc > 0;
      if (nc > 0) this.nearMeshes[i].instanceMatrix.needsUpdate = true;

      this.midMeshes[i].count = mc;
      this.midMeshes[i].visible = mc > 0;
      if (mc > 0) this.midMeshes[i].instanceMatrix.needsUpdate = true;

      totalNear += nc;
      totalMid += mc;
      nearTris += nc * this.protoMeta[i].near_triangles;
      midTris += mc * this.protoMeta[i].mid_triangles;
    }

    this.stats.nearTreeInstances = totalNear;
    this.stats.midProxyInstances = totalMid;
    this.stats.nearTriangles = nearTris;
    this.stats.midTriangles = midTris;
    return this.stats;
  }

  dispose() {
    this.grassField.dispose();
    this.grassMaskTex.dispose();
    for (const m of this.nearMeshes) {
      m.geometry.dispose();
      m.material.dispose();
      m.customDepthMaterial?.dispose?.();
    }
    for (const m of this.midMeshes) {
      m.geometry.dispose();
      m.material.dispose();
    }
  }
}
