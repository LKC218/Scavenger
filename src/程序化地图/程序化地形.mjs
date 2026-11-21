import * as THREE from 'three';
import {decodeSCVHHeightfield, decodeSCVPPatch, HybridTerrainSupport} from './高度支撑.mjs?v=680p-v7';
export {decodeSCVHHeightfield, decodeSCVPPatch, HybridTerrainSupport};

/**
 * 构建带向下裙边（Skirt）的共享平面几何体，消除不同分辨率地形块之间的 T 型接缝裂纹。
 * 局部坐标 x, z 范围 [-0.5, 0.5]，skirt 顶点 y = -1.0（在顶点着色器按裙边深度下沉）。
 */
export function createSkirtedPlaneGeometry(segments = 32) {
  const n = segments + 1;
  const mainCount = n * n;
  const skirtCount = 4 * n;
  const totalVerts = mainCount + skirtCount;
  const positions = new Float32Array(totalVerts * 3);
  const normals = new Float32Array(totalVerts * 3);
  const uvs = new Float32Array(totalVerts * 2);

  let vi = 0;
  for (let iz = 0; iz < n; iz++) {
    const v = iz / segments;
    const z = v - 0.5;
    for (let ix = 0; ix < n; ix++) {
      const u = ix / segments;
      const x = u - 0.5;
      positions[vi * 3] = x;
      positions[vi * 3 + 1] = 0;
      positions[vi * 3 + 2] = z;
      normals[vi * 3 + 1] = 1;
      uvs[vi * 2] = u;
      uvs[vi * 2 + 1] = v;
      vi++;
    }
  }

  // 4 条边裙边顶点：top (iz=0), bottom (iz=segments), left (ix=0), right (ix=segments)
  const borderIndices = [];
  // top: iz=0, ix=0..segments
  for (let ix = 0; ix < n; ix++) borderIndices.push(ix);
  // right: ix=segments, iz=0..segments
  for (let iz = 0; iz < n; iz++) borderIndices.push(iz * n + segments);
  // bottom: iz=segments, ix=segments..0
  for (let ix = segments; ix >= 0; ix--) borderIndices.push(segments * n + ix);
  // left: ix=0, iz=segments..0
  for (let iz = segments; iz >= 0; iz--) borderIndices.push(iz * n);

  const skirtStart = mainCount;
  for (let i = 0; i < borderIndices.length; i++) {
    const src = borderIndices[i];
    positions[vi * 3] = positions[src * 3];
    positions[vi * 3 + 1] = -1; // 标记为裙边顶点
    positions[vi * 3 + 2] = positions[src * 3 + 2];
    normals[vi * 3 + 1] = 1;
    uvs[vi * 2] = uvs[src * 2];
    uvs[vi * 2 + 1] = uvs[src * 2 + 1];
    vi++;
  }

  const mainQuads = segments * segments;
  const skirtQuads = 4 * segments;
  const indices = new Uint32Array((mainQuads + skirtQuads) * 6);
  let ii = 0;
  for (let iz = 0; iz < segments; iz++) {
    for (let ix = 0; ix < segments; ix++) {
      const a = iz * n + ix;
      const b = a + 1;
      const c = a + n;
      const d = c + 1;
      indices[ii++] = a;
      indices[ii++] = c;
      indices[ii++] = b;
      indices[ii++] = b;
      indices[ii++] = c;
      indices[ii++] = d;
    }
  }

  for (let edge = 0; edge < 4; edge++) {
    const offset = edge * n;
    for (let k = 0; k < segments; k++) {
      const top0 = borderIndices[offset + k];
      const top1 = borderIndices[offset + k + 1];
      const bot0 = skirtStart + offset + k;
      const bot1 = skirtStart + offset + k + 1;
      indices[ii++] = top0;
      indices[ii++] = bot0;
      indices[ii++] = top1;
      indices[ii++] = top1;
      indices[ii++] = bot0;
      indices[ii++] = bot1;
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(new THREE.BufferAttribute(indices, 1));
  return geo;
}

/**
 * 从 4x3 图集 PNG 解码构造 WebGL2 DataArrayTexture (512x512x12)。
 */
export async function loadTerrainArrayTexture(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('无法加载地表纹理数组图集: ' + url);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);
  const tileSize = 512;
  const layers = 12;
  const canvas = document.createElement('canvas');
  canvas.width = tileSize;
  canvas.height = tileSize;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  const arrayData = new Uint8Array(tileSize * tileSize * 4 * layers);

  for (let i = 0; i < layers; i++) {
    const row = Math.floor(i / 4);
    const col = i % 4;
    ctx.clearRect(0, 0, tileSize, tileSize);
    ctx.drawImage(bitmap, col * tileSize, row * tileSize, tileSize, tileSize, 0, 0, tileSize, tileSize);
    const imgData = ctx.getImageData(0, 0, tileSize, tileSize).data;
    arrayData.set(imgData, i * tileSize * tileSize * 4);
  }
  bitmap.close?.();

  const tex = new THREE.DataArrayTexture(arrayData, tileSize, tileSize, layers);
  tex.format = THREE.RGBAFormat;
  tex.type = THREE.UnsignedByteType;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.needsUpdate = true;
  return tex;
}

/**
 * 解码无损 SCVS Splatmap 控制文件（top4_idx RGBA8 + top4_wgt RGBA8 + grass_channel R8），
 * 避免 HTML5 Canvas 2D 预乘 Alpha 导致 Alpha=0 时 RGB 归零变黑。
 */
export async function loadSCVSSplatControl(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('无法加载 SCVS Splat 控制包: ' + url);
  const compBuf = await response.arrayBuffer();
  let rawBuf = compBuf;
  const check = new DataView(compBuf);
  if (check.byteLength < 16 || check.getUint32(0, true) !== 0x53564353) {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(new Uint8Array(compBuf));
    writer.close();
    rawBuf = await new Response(ds.readable).arrayBuffer();
  }
  const dv = new DataView(rawBuf);
  if (dv.getUint32(0, true) !== 0x53564353 || dv.getUint32(4, true) !== 1) {
    throw new Error('SCVS Splat 控制包头部无效');
  }
  const width = dv.getUint32(8, true);
  const height = dv.getUint32(12, true);
  const pixels = width * height;
  const idxData = new Uint8Array(rawBuf, 16, pixels * 4);
  const wgtData = new Uint8Array(rawBuf, 16 + pixels * 4, pixels * 4);
  const grassData = new Uint8Array(rawBuf, 16 + pixels * 8, pixels);

  const makeTex = (arr, nearest) => {
    const tex = new THREE.DataTexture(new Uint8Array(arr), width, height, THREE.RGBAFormat, THREE.UnsignedByteType);
    tex.colorSpace = THREE.NoColorSpace;
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.minFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
    tex.magFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
    tex.generateMipmaps = false;
    tex.flipY = false;
    tex.needsUpdate = true;
    return tex;
  };

  const indexTexture = makeTex(idxData, false);
  const weightTexture = makeTex(wgtData, false);
  return {
    width,
    height,
    indexTexture,
    weightTexture,
    indexData: indexTexture.image.data,
    weightData: weightTexture.image.data,
    grassData: new Uint8Array(grassData),
  };
}

/**
 * 加载图像并同时保留 Canvas 像素缓冲（供宏观底色贴图使用）。
 */
export async function loadImageTextureWithData(url, {nearest = false, srgb = false} = {}) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('无法加载地表控制贴图: ' + url);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);

  const tex = new THREE.DataTexture(new Uint8Array(imgData.data.buffer), canvas.width, canvas.height, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.minFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
  tex.magFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.flipY = false;
  tex.needsUpdate = true;
  return {texture: tex, data: tex.image.data, width: canvas.width, height: canvas.height};
}

/**
 * 将 4501x4501 uint16 高度场编码为 RG8 无损打包纹理（R=低8位, G=高8位），
 * 并在顶点着色器中精确还原 16-bit 高度与中心差分法线。
 */
export function createPackedHeightTexture(hf) {
  // 采用 2251x2251（4m 步距 GPU 纹理，仅 ~10 MiB 显存）RGBA8 (RG=高度uint16, B=normalY, A=role)
  const w = 2251;
  const h = 2251;
  const data = new Uint8Array(w * h * 4);
  const {width, height, qGrid, nyGrid, roleGrid} = hf;
  for (let iz = 0; iz < h; iz++) {
    const sz = Math.min(height - 1, Math.round((iz / (h - 1)) * (height - 1)));
    const srcRow = sz * width;
    const dstRow = iz * w;
    for (let ix = 0; ix < w; ix++) {
      const sx = Math.min(width - 1, Math.round((ix / (w - 1)) * (width - 1)));
      const q = qGrid[srcRow + sx];
      const di = (dstRow + ix) * 4;
      data[di] = q & 0xff;
      data[di + 1] = (q >> 8) & 0xff;
      data[di + 2] = nyGrid ? nyGrid[srcRow + sx] : 250;
      data[di + 3] = roleGrid ? roleGrid[srcRow + sx] : 1;
    }
  }
  const tex = new THREE.DataTexture(data, w, h, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.colorSpace = THREE.NoColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.minFilter = THREE.NearestFilter;
  tex.magFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  tex.flipY = false;
  tex.needsUpdate = true;
  return {texture: tex, data, width: w, height: h, step: 4.0};
}

/**
 * 同步更新 GPU 打包高度纹理（用于编辑器地形笔刷和道路压平实时反馈）
 */
export function syncPackedHeightTexture(packed, hf, region = null) {
  const {data, width: w, height: h} = packed;
  const {width: sw, height: sh, qGrid, nyGrid, roleGrid, roleGridHalf, halfWidth} = hf;
  const iz0 = region ? Math.max(0, Math.floor(region.iz0 / 2)) : 0;
  const iz1 = region ? Math.min(h - 1, Math.ceil(region.iz1 / 2)) : h - 1;
  const ix0 = region ? Math.max(0, Math.floor(region.ix0 / 2)) : 0;
  const ix1 = region ? Math.min(w - 1, Math.ceil(region.ix1 / 2)) : w - 1;
  for (let iz = iz0; iz <= iz1; iz++) {
    const sz = Math.min(sh - 1, iz * 2);
    const srcRow = sz * sw;
    const dstRow = iz * w;
    for (let ix = ix0; ix <= ix1; ix++) {
      const sx = Math.min(sw - 1, ix * 2);
      const q = qGrid[srcRow + sx];
      const di = (dstRow + ix) * 4;
      data[di] = q & 0xff;
      data[di + 1] = (q >> 8) & 0xff;
      if (nyGrid) data[di + 2] = nyGrid[srcRow + sx];
      if (roleGrid) data[di + 3] = roleGrid[srcRow + sx];
      else if (roleGridHalf) data[di + 3] = roleGridHalf[Math.min(halfWidth - 1, iz) * halfWidth + Math.min(halfWidth - 1, ix)];
    }
  }
  packed.texture.needsUpdate = true;
}

/**
 * 创建基于 WebGL2 DataArrayTexture + Top-4 Splatmap + 随机哈希防平铺（Stochastic Tiling）
 * + GPU 16-bit 顶点位移的程序化地形材质。
 */
export function createProceduralTerrainMaterial({
  heightTex,
  terrainArrayTex,
  splatIndexTex,
  splatWeightTex,
  macroTintGrassTex,
  bounds,
  splatConfig,
  grassFieldTex = null,
  grassMaskTex = null,
  windTimeUniform = null,
  terrainGrassStrength = 1.0,
}) {
  const uvScales = new Float32Array(12);
  for (let i = 0; i < 12; i++) {
    uvScales[i] = splatConfig.layers[i]?.world_uv_scale?.[0] ?? 0.16;
  }

  let effectiveGrassFieldTex = grassFieldTex;
  if (!effectiveGrassFieldTex) {
    const fallbackData = new Uint8Array([
      165, 165, 128, 128, 165, 165, 128, 128,
      165, 165, 128, 128, 165, 165, 128, 128,
    ]);
    effectiveGrassFieldTex = new THREE.DataTexture(fallbackData, 2, 2, THREE.RGBAFormat);
    effectiveGrassFieldTex.wrapS = THREE.RepeatWrapping;
    effectiveGrassFieldTex.wrapT = THREE.RepeatWrapping;
    effectiveGrassFieldTex.magFilter = THREE.LinearFilter;
    effectiveGrassFieldTex.minFilter = THREE.LinearFilter;
    effectiveGrassFieldTex.colorSpace = THREE.NoColorSpace;
    effectiveGrassFieldTex.needsUpdate = true;
  }

  let effectiveGrassMaskTex = grassMaskTex;
  if (!effectiveGrassMaskTex) {
    const fallbackMask = new Uint8Array(2 * 2 * 4).fill(180);
    effectiveGrassMaskTex = new THREE.DataTexture(fallbackMask, 2, 2, THREE.RGBAFormat);
    effectiveGrassMaskTex.magFilter = THREE.LinearFilter;
    effectiveGrassMaskTex.minFilter = THREE.LinearFilter;
    effectiveGrassMaskTex.colorSpace = THREE.NoColorSpace;
    effectiveGrassMaskTex.needsUpdate = true;
  }

  const uniforms = {
    uHeightMap: {value: heightTex.texture},
    uHeightTexSize: {value: new THREE.Vector2(heightTex.width, heightTex.height)},
    uMapBounds: {value: new THREE.Vector4(bounds.minX, bounds.minZ, bounds.maxX - bounds.minX, bounds.maxZ - bounds.minZ)},
    uHeightRange: {value: new THREE.Vector2(bounds.minH ?? -40.0, (bounds.maxH ?? 120.0) - (bounds.minH ?? -40.0))},
    uTerrainArray: {value: terrainArrayTex},
    uSplatIndex: {value: splatIndexTex},
    uSplatWeight: {value: splatWeightTex},
    uMacroTint: {value: macroTintGrassTex},
    uGrassFieldTex: {value: effectiveGrassFieldTex},
    uGrassMask: {value: effectiveGrassMaskTex},
    uWindTime: windTimeUniform || {value: 0.0},
    uTerrainGrassStrength: {value: terrainGrassStrength},
    uLayerScales: {value: uvScales},
    uCameraCenter: {value: new THREE.Vector3(0, 0, 85)},
    uCameraSpan: {value: 140.0},
    uSkirtDepth: {value: 5.5},
  };

  const material = new THREE.MeshStandardMaterial({
    roughness: 0.92,
    metalness: 0.02,
  });
  material.userData.render_role = 1;
  material.userData.proceduralUniforms = uniforms;

  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);

    shader.vertexShader = `
      uniform sampler2D uHeightMap;
      uniform vec2 uHeightTexSize;
      uniform vec4 uMapBounds;
      uniform vec2 uHeightRange;
      uniform float uSkirtDepth;
      varying vec3 vWorldPos;
      varying float vSlope;

      float decodeHeightAt(vec2 worldXZ, out float outRole) {
        vec2 uv = clamp((worldXZ - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
        vec2 texel = uv * (uHeightTexSize - 1.0);
        vec2 i0 = floor(texel);
        vec2 f = texel - i0;
        vec2 i1 = min(i0 + 1.0, uHeightTexSize - 1.0);
        vec4 s00 = texture2D(uHeightMap, (i0 + 0.5) / uHeightTexSize);
        vec4 s10 = texture2D(uHeightMap, (vec2(i1.x, i0.y) + 0.5) / uHeightTexSize);
        vec4 s01 = texture2D(uHeightMap, (vec2(i0.x, i1.y) + 0.5) / uHeightTexSize);
        vec4 s11 = texture2D(uHeightMap, (i1 + 0.5) / uHeightTexSize);
        float q00 = floor(s00.r * 255.0 + 0.5) + floor(s00.g * 255.0 + 0.5) * 256.0;
        float q10 = floor(s10.r * 255.0 + 0.5) + floor(s10.g * 255.0 + 0.5) * 256.0;
        float q01 = floor(s01.r * 255.0 + 0.5) + floor(s01.g * 255.0 + 0.5) * 256.0;
        float q11 = floor(s11.r * 255.0 + 0.5) + floor(s11.g * 255.0 + 0.5) * 256.0;
        float r00 = floor(s00.a * 255.0 + 0.5);
        float r10 = floor(s10.a * 255.0 + 0.5);
        float r01 = floor(s01.a * 255.0 + 0.5);
        float r11 = floor(s11.a * 255.0 + 0.5);
        outRole = 0.0;
        if ( (r00 >= 1.5 && r00 <= 3.5) || (r10 >= 1.5 && r10 <= 3.5) || (r01 >= 1.5 && r01 <= 3.5) || (r11 >= 1.5 && r11 <= 3.5) ) {
          outRole = 2.0;
        }
        float q = mix(mix(q00, q10, f.x), mix(q01, q11, f.x), f.y);
        return uHeightRange.x + (min(q, 65534.0) / 65534.0) * uHeightRange.y;
      }
    ` + shader.vertexShader;

    shader.vertexShader = shader.vertexShader.replace(
      '#include <beginnormal_vertex>',
      `#include <beginnormal_vertex>
      vec4 worldProbe = modelMatrix * vec4(position.x, 0.0, position.z, 1.0);
      float dummyR;
      float hL = decodeHeightAt(worldProbe.xz + vec2(-3.0, 0.0), dummyR);
      float hR = decodeHeightAt(worldProbe.xz + vec2(3.0, 0.0), dummyR);
      float hD = decodeHeightAt(worldProbe.xz + vec2(0.0, -3.0), dummyR);
      float hU = decodeHeightAt(worldProbe.xz + vec2(0.0, 3.0), dummyR);
      vec3 worldN = normalize(vec3(hL - hR, 6.0, hD - hU));
      vSlope = 1.0 - clamp(worldN.y, 0.0, 1.0);
      // 抵消 modelMatrix 的非均匀缩放 (250, 1, 250)，使 normalMatrix * objectNormal 恢复精确世界法线
      objectNormal = normalize(vec3(worldN.x * modelMatrix[0][0], worldN.y, worldN.z * modelMatrix[2][2]));
      `
    );

    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `vec3 transformed = vec3(position.x, 0.0, position.z);
      vec4 wPos = modelMatrix * vec4(transformed, 1.0);
      float vRole = 0.0;
      float terrainY = decodeHeightAt(wPos.xz, vRole);
      if (vRole > 1.5) {
        // 道面与停机坪覆盖层下方微沉 0.14m，彻底消除道面与地形网格斜面穿插
        terrainY -= 0.14;
      }
      if (position.y < -0.5) {
        terrainY -= uSkirtDepth;
      }
      transformed.y = terrainY;
      vWorldPos = vec3(wPos.x, terrainY, wPos.z);
      `
    );

    shader.fragmentShader = `
      precision highp sampler2DArray;
      uniform sampler2D uHeightMap;
      uniform vec2 uHeightTexSize;
      uniform sampler2DArray uTerrainArray;
      uniform sampler2D uSplatIndex;
      uniform sampler2D uSplatWeight;
      uniform sampler2D uMacroTint;
      uniform sampler2D uGrassFieldTex;
      uniform sampler2D uGrassMask;
      uniform float uWindTime;
      uniform float uTerrainGrassStrength;
      uniform vec4 uMapBounds;
      uniform float uLayerScales[12];
      uniform vec3 uCameraCenter;
      uniform float uCameraSpan;
      varying vec3 vWorldPos;
      varying float vSlope;

      // 片元级 2m 高精度道面/水域掩码双线性插值（彻底消灭 8.75m 顶点级草边锯齿，0 discard）
      float sampleRoadClearance2m(vec2 worldXZ) {
        vec2 uv = clamp((worldXZ - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
        vec2 texel = uv * (uHeightTexSize - 1.0);
        vec2 i0 = floor(texel);
        vec2 f = texel - i0;
        vec2 i1 = min(i0 + 1.0, uHeightTexSize - 1.0);
        float r00 = floor(texture2D(uHeightMap, (i0 + 0.5) / uHeightTexSize).a * 255.0 + 0.5);
        float r10 = floor(texture2D(uHeightMap, (vec2(i1.x, i0.y) + 0.5) / uHeightTexSize).a * 255.0 + 0.5);
        float r01 = floor(texture2D(uHeightMap, (vec2(i0.x, i1.y) + 0.5) / uHeightTexSize).a * 255.0 + 0.5);
        float r11 = floor(texture2D(uHeightMap, (i1 + 0.5) / uHeightTexSize).a * 255.0 + 0.5);
        float c00 = ((r00 >= 1.5 && r00 <= 3.5) || (r00 >= 4.5 && r00 <= 5.5)) ? 0.0 : 1.0;
        float c10 = ((r10 >= 1.5 && r10 <= 3.5) || (r10 >= 4.5 && r10 <= 5.5)) ? 0.0 : 1.0;
        float c01 = ((r01 >= 1.5 && r01 <= 3.5) || (r01 >= 4.5 && r01 <= 5.5)) ? 0.0 : 1.0;
        float c11 = ((r11 >= 1.5 && r11 <= 3.5) || (r11 >= 4.5 && r11 <= 5.5)) ? 0.0 : 1.0;
        return mix(mix(c00, c10, f.x), mix(c01, c11, f.x), f.y);
      }

      // 双尺度无缝抗平铺采样（消除网格重复感与分块边界锯齿）
      vec4 sampleStochasticLayer(int layerIdx, vec2 worldXZ, float scale, vec2 ddxW, vec2 ddyW) {
        vec2 uv1 = worldXZ * scale;
        vec2 uv2 = worldXZ * (scale * 0.27) + vec2(0.37 * float(layerIdx), 0.71 * float(layerIdx));
        vec4 s1 = textureGrad(uTerrainArray, vec3(uv1, float(layerIdx)), ddxW * scale, ddyW * scale);
        vec4 s2 = textureGrad(uTerrainArray, vec3(uv2, float(layerIdx)), ddxW * (scale * 0.27), ddyW * (scale * 0.27));
        return mix(s1, s2, 0.35);
      }
    ` + shader.fragmentShader;

    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <map_fragment>',
      `
      vec2 mapUV = clamp((vWorldPos.xz - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
      vec4 macroSample = texture2D(uMacroTint, mapUV);
      // 转换宏观底色到线性空间
      vec3 macroLinear = pow(max(macroSample.rgb, vec3(0.02)), vec3(2.2));
      vec2 ddxW = dFdx(vWorldPos.xz);
      vec2 ddyW = dFdy(vWorldPos.xz);

      float camDist = length(vWorldPos.xz - uCameraCenter.xz);
      float spanBlend = 1.0 - smoothstep(650.0, 1600.0, uCameraSpan);
      // 在近中景 (< 950m) 使用双线性插值的 8 固定生态通道混合 TextureArray，远景平滑过渡至宏观底色
      float detailBlend = (1.0 - smoothstep(420.0, 980.0, camDist)) * spanBlend;
      vec3 finalAlbedo = macroLinear;

      if (detailBlend > 0.01) {
        // 加入基于世界坐标的程序化 Perlin/Simplex 双频微扰（复刻原控制器 PerlinNoiseTile=5.0 自然边缘交融）
        vec2 pNoiseUV = vWorldPos.xz * 0.035;
        float n1 = sin(pNoiseUV.x * 1.7 + cos(pNoiseUV.y * 1.9)) * 0.5 + sin(pNoiseUV.y * 3.1 - cos(pNoiseUV.x * 2.7)) * 0.5;
        vec2 warpedMapUV = clamp(mapUV + vec2(n1, -n1) * 0.00055, 0.0, 1.0);

        vec4 w0_3 = texture2D(uSplatIndex, warpedMapUV);
        vec4 w4_7 = texture2D(uSplatWeight, warpedMapUV);
        // BlendMultiplay=2.0 幂次锐化与纹理高度混合
        w0_3 = pow(max(w0_3, vec4(0.0)), vec4(1.35));
        w4_7 = pow(max(w4_7, vec4(0.0)), vec4(1.35));
        float rawSum = dot(w0_3, vec4(1.0)) + dot(w4_7, vec4(1.0));
        float wSum = max(rawSum, 1e-4);
        w0_3 /= wSum;
        w4_7 /= wSum;

        vec3 splatColor = macroLinear;
        if (rawSum > 1e-3) {
          splatColor = vec3(0.0);
          if (w0_3.r > 0.01) splatColor += sampleStochasticLayer(0, vWorldPos.xz, uLayerScales[0], ddxW, ddyW).rgb * w0_3.r;
          if (w0_3.g > 0.01) splatColor += sampleStochasticLayer(1, vWorldPos.xz, uLayerScales[1], ddxW, ddyW).rgb * w0_3.g;
          if (w0_3.b > 0.01) splatColor += sampleStochasticLayer(2, vWorldPos.xz, uLayerScales[2], ddxW, ddyW).rgb * w0_3.b;
          if (w0_3.a > 0.01) splatColor += sampleStochasticLayer(3, vWorldPos.xz, uLayerScales[3], ddxW, ddyW).rgb * w0_3.a;
          if (w4_7.r > 0.01) splatColor += sampleStochasticLayer(4, vWorldPos.xz, uLayerScales[4], ddxW, ddyW).rgb * w4_7.r;
          if (w4_7.g > 0.01) splatColor += sampleStochasticLayer(6, vWorldPos.xz, uLayerScales[6], ddxW, ddyW).rgb * w4_7.g;
          if (w4_7.b > 0.01) splatColor += sampleStochasticLayer(8, vWorldPos.xz, uLayerScales[8], ddxW, ddyW).rgb * w4_7.b;
          if (w4_7.a > 0.01) splatColor += sampleStochasticLayer(10, vWorldPos.xz, uLayerScales[10], ddxW, ddyW).rgb * w4_7.a;
        }

        float luma = dot(splatColor, vec3(0.299, 0.587, 0.114));
        // 结合宏观色温标定与微观高频纹理，消除远近切换色差与边界锯齿
        vec3 calibratedDetail = mix(splatColor, macroLinear * clamp(0.76 + 0.48 * luma, 0.55, 1.45), 0.35);
        // 方案 A 第 1 层：地表融合微缩草绒 + 2m 精确路边柔边 + 柔风草浪（0 discard / 0 白沙噪点）
        float roadClearance = sampleRoadClearance2m(vWorldPos.xz);
        vec4 lowNoise = texture2D(uGrassFieldTex, vWorldPos.xz * 0.028);
        float roadSoft = smoothstep(0.14, 0.86, roadClearance + (lowNoise.a - 0.5) * 0.28);
        // 联合精确 grassMask、生态 Splat 通道及宏观绿意（兼顾城镇绿地与路边 2m 精准防锯齿切割）
        float greenness = clamp((macroSample.g * 1.15 - macroSample.r * 0.65 - macroSample.b * 0.40) * 4.2, 0.0, 1.0);
        float grassSplats = clamp(w0_3.r * 0.95 + w0_3.g * 1.0 + w0_3.b * 0.55 + w4_7.r * 0.75, 0.0, 1.0);
        float rawGrassWeight = max(texture2D(uGrassMask, warpedMapUV).r, clamp(grassSplats * 0.62 + greenness * 0.55, 0.0, 1.0));
        float grassCover = smoothstep(0.14, 0.48, rawGrassWeight + (lowNoise.a - 0.50) * 0.20) * roadSoft * uTerrainGrassStrength;

        if (grassCover > 0.005) {
          float macroPatch = lowNoise.b;
          vec3 freshLush = vec3(0.062, 0.156, 0.025);
          vec3 freshWarm = vec3(0.112, 0.162, 0.036);
          vec3 stylizedBase = mix(freshLush, freshWarm, smoothstep(0.24, 0.80, macroPatch));

          // 近景微缩草绒笔触（中远距平滑归 1.0，彻底消灭 180m–350m 子像素白点噪点）
          float microFade = (1.0 - smoothstep(105.0, 235.0, camDist)) * (1.0 - smoothstep(135.0, 285.0, uCameraSpan));
          float tuftTone = 1.0;
          if (microFade > 0.01) {
            float tA = texture2D(uGrassFieldTex, vWorldPos.xz * 0.135).r;
            mat2 rot37 = mat2(0.7986, -0.6018, 0.6018, 0.7986);
            float tB = texture2D(uGrassFieldTex, (rot37 * vWorldPos.xz) * 0.118 + vec2(0.37, 0.71)).g;
            tuftTone = mix(1.0, mix(0.76, 1.18, max(tA, tB)), microFade);
          }

          float wavePhase = vWorldPos.x * 0.045 + vWorldPos.z * 0.032 - uWindTime * 1.45;
          float windShadow = mix(1.0, 0.93 + 0.12 * sin(wavePhase), grassCover * (1.0 - smoothstep(180.0, 420.0, uCameraSpan)));
          vec3 ecoBase = calibratedDetail * vec3(0.74, 1.06, 0.56) + vec3(0.014, 0.042, 0.008);
          vec3 targetGrass = mix(ecoBase, stylizedBase, 0.68) * tuftTone * windShadow;
          calibratedDetail = mix(calibratedDetail, targetGrass, grassCover * 0.92);
        }
        // 坡度微调加深
        calibratedDetail *= (1.0 - vSlope * 0.25);
        finalAlbedo = mix(macroLinear, calibratedDetail, detailBlend);
      }

      diffuseColor.rgb = finalAlbedo;
      `
    );
  };
  material.customProgramCacheKey = () => 'scavenger-procedural-terrain-splat-v1';
  return {material, uniforms};
}

/**
 * 共享地形网格池（PlaneGeometry Pool）：
 * - 全图常驻 1 个 192x192 宏观带裙边基底网格（确保从 70m 到 9000m 永远无空洞、无闪屏）；
 * - 围绕相机焦点动态重定位 25 个（5x5）近中景高细分 250m 地形块（中心 3x3 采用 64x64 分段，外环采用 32x32 分段）。
 */
export class ProceduralTerrainPool {
  constructor(material, bounds) {
    this.material = material;
    this.bounds = bounds;
    this.root = new THREE.Group();
    this.root.name = 'ProceduralTerrainRoot';

    this.geoHigh = createSkirtedPlaneGeometry(64);
    this.geoMid = createSkirtedPlaneGeometry(32);
    this.geoBase = createSkirtedPlaneGeometry(192);

    // 常驻全图底座（y 微降 0.08m 防止与近景高精度块 Z-fight，视野 > 1200m 时作为主力连续网格）
    this.baseMesh = new THREE.Mesh(this.geoBase, material);
    const w = bounds.maxX - bounds.minX;
    const d = bounds.maxZ - bounds.minZ;
    this.baseMesh.scale.set(w, 1, d);
    this.baseMesh.position.set((bounds.minX + bounds.maxX) * 0.5, -0.06, (bounds.minZ + bounds.maxZ) * 0.5);
    this.baseMesh.receiveShadow = true;
    this.baseMesh.frustumCulled = false;
    this.root.add(this.baseMesh);

    this.poolSize = 25; // 5x5 of 250m tiles = 1250m x 1250m high-detail ring around camera
    this.tiles = [];
    for (let i = 0; i < this.poolSize; i++) {
      const mesh = new THREE.Mesh(this.geoHigh, material);
      mesh.scale.set(250, 1, 250);
      mesh.receiveShadow = true;
      mesh.frustumCulled = false;
      this.root.add(mesh);
      this.tiles.push(mesh);
    }
  }

  update(center, span) {
    const {minX, maxX, minZ, maxZ} = this.bounds;
    this.material.userData.proceduralUniforms.uCameraCenter.value.set(center.x, center.y ?? 0, center.z);
    this.material.userData.proceduralUniforms.uCameraSpan.value = span;

    // 当视野跨度很大（> 1800m）时，仅使用全图连续网格底座即可，省去 25 个局部块提交
    if (span > 1800) {
      this.baseMesh.position.y = 0;
      for (const mesh of this.tiles) mesh.visible = false;
      return 1;
    }

    // 近中景时下沉全图粗底座 2.5m，防止 47m 粗三角形在山坡凸起处刺穿 3.9m 精细块
    this.baseMesh.position.y = -2.5;
    const baseTx = Math.max(2, Math.min(33, Math.floor((center.x - minX) / 250)));
    const baseTz = Math.max(2, Math.min(33, Math.floor((center.z - minZ) / 250)));

    let idx = 0;
    for (let dz = -2; dz <= 2; dz++) {
      for (let dx = -2; dx <= 2; dx++) {
        const tx = baseTx + dx;
        const tz = baseTz + dz;
        const mesh = this.tiles[idx++];
        const isInner = Math.abs(dx) <= 1 && Math.abs(dz) <= 1;
        mesh.geometry = isInner ? this.geoHigh : this.geoMid;
        mesh.position.set(minX + (tx + 0.5) * 250, 0, minZ + (tz + 0.5) * 250);
        mesh.visible = true;
      }
    }
    return 1 + this.poolSize;
  }

  visibleMeshes() {
    const list = [this.baseMesh];
    for (const t of this.tiles) if (t.visible) list.push(t);
    return list;
  }

  dispose() {
    this.geoHigh.dispose();
    this.geoMid.dispose();
    this.geoBase.dispose();
  }
}

/**
 * 根据道路中心线样条控制点动态生成贴地路面带状网格（供编辑器修改道路样条或新增道路实时渲染）。
 */
export function buildCustomRoadRibbonMesh(splines, heightAt, existingMaterial = null) {
  const positions = [];
  const normals = [];
  const uvs = [];
  const indices = [];
  let vOffset = 0;

  for (const sp of splines) {
    if (!sp.custom && !sp.modified) continue;
    const pts = sp.points;
    if (!Array.isArray(pts) || pts.length < 2) continue;
    const halfW = (sp.width ?? 10) * 0.5;
    let distAccum = 0;

    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      let tx = 0, tz = 1;
      if (i < pts.length - 1) {
        tx = pts[i + 1][0] - p[0];
        tz = pts[i + 1][2] - p[2];
      } else {
        tx = p[0] - pts[i - 1][0];
        tz = p[2] - pts[i - 1][2];
      }
      const len = Math.hypot(tx, tz) || 1;
      tx /= len;
      tz /= len;
      const nx = -tz;
      const nz = tx;

      if (i > 0) {
        distAccum += Math.hypot(p[0] - pts[i - 1][0], p[2] - pts[i - 1][2]);
      }
      const lx = p[0] - nx * halfW;
      const lz = p[2] - nz * halfW;
      const rx = p[0] + nx * halfW;
      const rz = p[2] + nz * halfW;
      const ly = (Number.isFinite(heightAt?.(lx, lz)) ? heightAt(lx, lz) : p[1]) + 0.06;
      const ry = (Number.isFinite(heightAt?.(rx, rz)) ? heightAt(rx, rz) : p[1]) + 0.06;

      positions.push(lx, ly, lz, rx, ry, rz);
      normals.push(0, 1, 0, 0, 1, 0);
      const vCoord = distAccum / Math.max(4, sp.width ?? 10);
      uvs.push(0, vCoord, 1, vCoord);

      if (i < pts.length - 1) {
        const a = vOffset + i * 2;
        const b = a + 1;
        const c = a + 2;
        const d = a + 3;
        indices.push(a, c, b, b, c, d);
      }
    }
    vOffset += pts.length * 2;
  }

  const geo = new THREE.BufferGeometry();
  if (positions.length > 0) {
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geo.setIndex(indices);
  }
  const mat = existingMaterial ?? new THREE.MeshStandardMaterial({
    color: 0x4b4e52,
    roughness: 0.85,
    metalness: 0.05,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  });
  mat.userData.render_role = 2;
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.name = 'CustomRoadRibbons';
  return mesh;
}

/**
 * 构建 9km 作战区外围 4.5km 海拔无缝衔接 + 镜像噪声去拉丝远景丘陵裙边 + 战术禁区细界标（仅 1 DC）：
 * - 几何体采用 72 段 × 7 环共角回字形细分网格（4,032 三角面），直接读取主地形 16-bit RG 高度图实现内缘 0 落差咬合；
 * - 片元禁用边缘 1px clamp 拉伸，改为镜像折叠 UV + 双频有机噪声打散，叠加低对比远丘微纹理与克制 45° 禁区斜纹；
 * - 采用与主地形同源的 MeshStandardMaterial PBR 光照管线，外缘在 2200m–4450m 平滑渐隐融入场景背景。
 */
export function createMapOutskirtsSkirtMesh(bounds, macroTintTex, skirtWidth = 4500.0, heightTex = null, grassFieldTex = null) {
  const {minX, maxX, minZ, maxZ} = bounds;
  const ringDists = [0.0, 60.0, 180.0, 420.0, 900.0, 1800.0, 3100.0, skirtWidth];
  const segPerSide = 72;
  const ringCount = ringDists.length;
  const perimeterCount = segPerSide * 4;

  const positions = new Float32Array(ringCount * perimeterCount * 3);
  const uvs = new Float32Array(ringCount * perimeterCount * 2);
  const indices = [];

  for (let r = 0; r < ringCount; r++) {
    const d = ringDists[r];
    const x0 = minX - d;
    const x1 = maxX + d;
    const z0 = minZ - d;
    const z1 = maxZ + d;
    const rowBase = r * perimeterCount;

    for (let s = 0; s < perimeterCount; s++) {
      const side = Math.floor(s / segPerSide);
      const t = (s % segPerSide) / segPerSide;
      let wx = 0;
      let wz = 0;
      if (side === 0) {
        // 北边：西 -> 东
        wx = x0 + (x1 - x0) * t;
        wz = z0;
      } else if (side === 1) {
        // 东边：北 -> 南
        wx = x1;
        wz = z0 + (z1 - z0) * t;
      } else if (side === 2) {
        // 南边：东 -> 西
        wx = x1 - (x1 - x0) * t;
        wz = z1;
      } else {
        // 西边：南 -> 北
        wx = x0;
        wz = z1 - (z1 - z0) * t;
      }
      const vi = rowBase + s;
      positions[vi * 3] = wx;
      positions[vi * 3 + 1] = 0.0;
      positions[vi * 3 + 2] = wz;
      uvs[vi * 2] = (wx - minX) / Math.max(1.0, maxX - minX);
      uvs[vi * 2 + 1] = (wz - minZ) / Math.max(1.0, maxZ - minZ);
    }
  }

  for (let r = 0; r < ringCount - 1; r++) {
    const r0 = r * perimeterCount;
    const r1 = (r + 1) * perimeterCount;
    for (let s = 0; s < perimeterCount; s++) {
      const sNext = (s + 1) % perimeterCount;
      const a = r0 + s;
      const b = r0 + sNext;
      const c = r1 + s;
      const d = r1 + sNext;
      indices.push(a, b, d, a, d, c);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(indices);

  // 兜底安全纹理，确保向后兼容旧版 3 参数调用不产生 WebGL 告警
  const createFallbackTex = (rgba) => {
    const t = new THREE.DataTexture(new Uint8Array(rgba), 2, 2, THREE.RGBAFormat);
    t.colorSpace = THREE.NoColorSpace;
    t.needsUpdate = true;
    return t;
  };
  const hasHeightTex = Boolean(heightTex && heightTex.texture);
  const effHeightTex = hasHeightTex ? heightTex.texture : createFallbackTex([0, 128, 255, 1, 0, 128, 255, 1, 0, 128, 255, 1, 0, 128, 255, 1]);
  const effHeightSize = hasHeightTex ? new THREE.Vector2(heightTex.width, heightTex.height) : new THREE.Vector2(2, 2);
  const hasGrassField = Boolean(grassFieldTex);
  const effGrassFieldTex = hasGrassField ? grassFieldTex : createFallbackTex([160, 160, 128, 128, 160, 160, 128, 128, 160, 160, 128, 128, 160, 160, 128, 128]);

  const uniforms = {
    uMacroTint: {value: macroTintTex},
    uHeightMap: {value: effHeightTex},
    uHeightTexSize: {value: effHeightSize},
    uHasHeightTex: {value: hasHeightTex ? 1.0 : 0.0},
    uGrassFieldTex: {value: effGrassFieldTex},
    uHasGrassField: {value: hasGrassField ? 1.0 : 0.0},
    uMapBounds: {value: new THREE.Vector4(minX, minZ, maxX - minX, maxZ - minZ)},
    uHeightRange: {value: new THREE.Vector2(bounds.minH ?? -40.0, (bounds.maxH ?? 120.0) - (bounds.minH ?? -40.0))},
    uSkirtWidth: {value: skirtWidth},
  };

  const mat = new THREE.MeshStandardMaterial({
    roughness: 0.94,
    metalness: 0.02,
    transparent: true,
    depthWrite: true,
    side: THREE.DoubleSide,
  });
  mat.userData.proceduralSkirtUniforms = uniforms;

  mat.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);

    shader.vertexShader = `
      uniform sampler2D uHeightMap;
      uniform vec2 uHeightTexSize;
      uniform float uHasHeightTex;
      uniform vec4 uMapBounds;
      uniform vec2 uHeightRange;
      varying vec3 vSkirtWorldPos;

      float decodeSkirtEdgeHeight(vec2 clampedXZ) {
        if (uHasHeightTex < 0.5) return 0.0;
        vec2 uv = clamp((clampedXZ - uMapBounds.xy) / uMapBounds.zw, 0.0, 1.0);
        vec2 texel = uv * (uHeightTexSize - 1.0);
        vec2 i0 = floor(texel);
        vec2 f = texel - i0;
        vec2 i1 = min(i0 + 1.0, uHeightTexSize - 1.0);
        vec4 s00 = texture2D(uHeightMap, (i0 + 0.5) / uHeightTexSize);
        vec4 s10 = texture2D(uHeightMap, (vec2(i1.x, i0.y) + 0.5) / uHeightTexSize);
        vec4 s01 = texture2D(uHeightMap, (vec2(i0.x, i1.y) + 0.5) / uHeightTexSize);
        vec4 s11 = texture2D(uHeightMap, (i1 + 0.5) / uHeightTexSize);
        float q00 = floor(s00.r * 255.0 + 0.5) + floor(s00.g * 255.0 + 0.5) * 256.0;
        float q10 = floor(s10.r * 255.0 + 0.5) + floor(s10.g * 255.0 + 0.5) * 256.0;
        float q01 = floor(s01.r * 255.0 + 0.5) + floor(s01.g * 255.0 + 0.5) * 256.0;
        float q11 = floor(s11.r * 255.0 + 0.5) + floor(s11.g * 255.0 + 0.5) * 256.0;
        float q = mix(mix(q00, q10, f.x), mix(q01, q11, f.x), f.y);
        return uHeightRange.x + (min(q, 65534.0) / 65534.0) * uHeightRange.y;
      }

      float sampleOutskirtsHeight(vec2 pXZ) {
        vec2 maxB = uMapBounds.xy + uMapBounds.zw;
        vec2 clampedXZ = clamp(pXZ, uMapBounds.xy, maxB);
        vec2 outVec = max(vec2(0.0), max(uMapBounds.xy - pXZ, pXZ - maxB));
        float distOut = length(outVec);
        float edgeY = decodeSkirtEdgeHeight(clampedXZ);
        float baseEdgeY = mix(edgeY, 6.0, smoothstep(0.0, 2200.0, distOut));
        float hillAmp = smoothstep(140.0, 1100.0, distOut) * (1.0 - smoothstep(2600.0, 4400.0, distOut));
        float hill = (
          sin(pXZ.x * 0.00145) * cos(pXZ.y * 0.00135) +
          0.52 * sin((pXZ.x - pXZ.y) * 0.00215) +
          0.25 * cos((pXZ.x + pXZ.y) * 0.00340)
        ) * 18.0 * hillAmp;
        // 统一微沉 0.03m 贴合地形外沿，避免 0–50m 局部折面产生高亮带
        return baseEdgeY + hill - 0.03;
      }
    ` + shader.vertexShader;

    shader.vertexShader = shader.vertexShader.replace(
      '#include <beginnormal_vertex>',
      `#include <beginnormal_vertex>
      float hL = sampleOutskirtsHeight(position.xz + vec2(-18.0, 0.0));
      float hR = sampleOutskirtsHeight(position.xz + vec2(18.0, 0.0));
      float hD = sampleOutskirtsHeight(position.xz + vec2(0.0, -18.0));
      float hU = sampleOutskirtsHeight(position.xz + vec2(0.0, 18.0));
      objectNormal = normalize(vec3(hL - hR, 36.0, hD - hU));
      `
    );

    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `float outY = sampleOutskirtsHeight(position.xz);
      vec3 transformed = vec3(position.x, outY, position.z);
      vSkirtWorldPos = (modelMatrix * vec4(transformed, 1.0)).xyz;
      `
    );

    shader.fragmentShader = `
      uniform sampler2D uMacroTint;
      uniform sampler2D uGrassFieldTex;
      uniform float uHasGrassField;
      uniform vec4 uMapBounds;
      uniform float uSkirtWidth;
      varying vec3 vSkirtWorldPos;

      vec2 skirtMirrorUV(vec2 rawUV) {
        return 1.0 - abs(fract(rawUV * 0.5) * 2.0 - 1.0);
      }

      float skirtOrganicNoise(vec2 p) {
        return 0.55 * sin(p.x * 0.0031 + cos(p.y * 0.0027))
             + 0.30 * cos(p.y * 0.0067 - sin(p.x * 0.0059))
             + 0.15 * sin((p.x + p.y) * 0.0115);
      }
    ` + shader.fragmentShader;

    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <map_fragment>',
      `#include <map_fragment>
      vec2 maxB = uMapBounds.xy + uMapBounds.zw;
      vec2 outVec = max(vec2(0.0), max(uMapBounds.xy - vSkirtWorldPos.xz, vSkirtWorldPos.xz - maxB));
      float distOut = length(outVec);

      vec2 rawUV = (vSkirtWorldPos.xz - uMapBounds.xy) / uMapBounds.zw;
      float nA = skirtOrganicNoise(vSkirtWorldPos.xz);
      float nB = skirtOrganicNoise(vSkirtWorldPos.xz * 1.7 + vec2(137.0, 293.0));
      // 边界 0m 处零扰动精确接色，2m–160m 快速展开强有机偏移破除镜像对称感
      float warpAmp = 0.085 * smoothstep(2.0, 160.0, distOut);
      vec2 mUV = skirtMirrorUV(rawUV + vec2(nA, nB) * warpAmp);
      vec2 mUVFar = skirtMirrorUV(rawUV.yx * 0.72 + vec2(0.31, 0.57) + vec2(nB, -nA) * 0.12);

      vec3 macroNear = pow(max(texture2D(uMacroTint, clamp(mUV, 0.002, 0.998)).rgb, vec3(0.02)), vec3(2.2));
      vec3 macroCross = pow(max(texture2D(uMacroTint, clamp(mUVFar, 0.002, 0.998)).rgb, vec3(0.02)), vec3(2.2));
      float crossBlend = smoothstep(18.0, 280.0, distOut) * 0.48;
      vec3 macroLinear = mix(macroNear, macroCross, crossBlend);

      // 叠加低对比郊野大色块变化与轻量草绒质感，融合微缩手绘感
      float farVariation = clamp(0.86 + 0.18 * nA + 0.10 * nB, 0.72, 1.14);
      vec3 baseLinear = macroLinear * farVariation;
      if (uHasGrassField > 0.5) {
        float gDetail = texture2D(uGrassFieldTex, vSkirtWorldPos.xz * 0.016).b;
        baseLinear *= mix(0.92, 1.08, gDetail);
      }

      // 作战区外围柔和降饱和/压暗约 14% 与主战场形成克制区分
      float outerWeight = smoothstep(0.0, 80.0, distOut);
      float baseLuma = dot(baseLinear, vec3(0.299, 0.587, 0.114));
      vec3 mutedLinear = mix(baseLinear, vec3(baseLuma) * 0.84, outerWeight * 0.40);

      // 界外 10–650m 叠加极克制 45° 战术禁区微斜纹
      float stripePhase = fract((vSkirtWorldPos.x + vSkirtWorldPos.z) * 0.036);
      float stripeBand = smoothstep(0.45, 0.50, stripePhase) * (1.0 - smoothstep(0.56, 0.61, stripePhase));
      float stripeZone = smoothstep(8.0, 42.0, distOut) * (1.0 - smoothstep(380.0, 860.0, distOut));
      mutedLinear = mix(mutedLinear, mutedLinear * 0.84, stripeBand * stripeZone * 0.42);

      // 紧贴 9km 作战界线的精致香槟金细虚线战术边界线（收紧至 6m 宽，绝不泛白）
      float dashMask = step(0.42, fract((vSkirtWorldPos.x - vSkirtWorldPos.z) * 0.026));
      float borderBand = smoothstep(0.0, 1.5, distOut) * (1.0 - smoothstep(4.0, 7.5, distOut));
      vec3 borderColLinear = vec3(0.76, 0.62, 0.22);
      vec3 finalCol = mix(mutedLinear, borderColLinear, borderBand * dashMask * 0.62);

      // 外缘 2200m 至裙边外端平滑透明渐隐，无缝融入场景大气底色
      float alphaFade = 1.0 - smoothstep(uSkirtWidth * 0.48, uSkirtWidth * 0.985, distOut);
      diffuseColor.rgb = finalCol;
      diffuseColor.a = alphaFade;
      `
    );
  };
  mat.customProgramCacheKey = () => `scavenger-outskirts-hills-v2-${hasHeightTex ? 'h' : 'noh'}`;

  const mesh = new THREE.Mesh(geo, mat);
  mesh.frustumCulled = false;
  mesh.receiveShadow = true;
  mesh.renderOrder = -2;
  mesh.name = 'MapOutskirtsEndlessSkirt';
  return mesh;
}
