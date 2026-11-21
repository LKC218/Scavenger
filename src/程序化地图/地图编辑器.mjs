import * as THREE from 'three';
import {syncPackedHeightTexture, buildCustomRoadRibbonMesh} from './程序化地形.mjs?v=680p-v7';
import {deriveWorldOBB} from './自动物理派生.mjs?v=680p-v7';

/**
 * T3 实体无缝形变（100% 实心不透明、0 挖洞、0 Bayer 纱窗）：
 * - 主体建筑近景在过渡区间 (dist 400–560m / span 420–600m) 保持 100% 实心不透明，贴图高对比度细节平滑柔化收敛至
 *   该原型的远景主色调 uOverviewColor，并在色调完全一致的 600m 处无感交接给常驻远景立体包络 macroMesh；
 * - 不进入远景包络的小型道具在过渡后半段平滑向下沉降收拢进地表，彻底告别图 3 白色点阵纱窗！
 */
export function enhanceBuildingMaterial(material, sharedUniforms, fadeMode = 'near', opts = {}) {
  const overviewColor = opts.overviewColor || [0.55, 0.54, 0.52];
  const isSmallProp = Boolean(opts.isSmallProp);
  const protoKey = opts.protoKey || 'default';

  material.side = THREE.DoubleSide;
  material.onBeforeCompile = shader => {
    shader.uniforms.uCameraCenter = sharedUniforms.uCameraCenter;
    shader.uniforms.uCameraSpan = sharedUniforms.uCameraSpan;
    shader.uniforms.uDitherEnabled = sharedUniforms.uDitherEnabled;
    shader.uniforms.uOverviewColor = {
      value: new THREE.Color(overviewColor[0], overviewColor[1], overviewColor[2]),
    };
    shader.uniforms.uIsSmallProp = {value: isSmallProp ? 1.0 : 0.0};

    shader.vertexShader = `
      uniform vec3 uCameraCenter;
      uniform float uCameraSpan;
      uniform float uDitherEnabled;
      uniform float uIsSmallProp;
      varying vec3 vBldgWorldPos;
      varying float vMorphT;
    ` + shader.vertexShader;

    shader.vertexShader = shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
      #ifdef USE_INSTANCING
        vec4 instOrigin = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
      #else
        vec4 instOrigin = modelMatrix * vec4(0.0, 0.0, 0.0, 1.0);
      #endif
      float bDist = length(instOrigin.xz - uCameraCenter.xz);
      float morphD = smoothstep(500.0, 680.0, bDist);
      float morphS = smoothstep(520.0, 740.0, uCameraSpan);
      vMorphT = uDitherEnabled > 0.5 ? clamp(max(morphD, morphS), 0.0, 1.0) : 0.0;

      // 小型道具（路障/货箱/围栏等）在过渡带平滑向下沉降收拢进地表，无打孔、无突变
      if (uIsSmallProp > 0.5 && vMorphT > 0.40) {
        float sink = 1.0 - smoothstep(0.40, 0.94, vMorphT);
        transformed *= max(0.001, sink);
      }

      #ifdef USE_INSTANCING
        vec4 wPosB = modelMatrix * instanceMatrix * vec4(transformed, 1.0);
      #else
        vec4 wPosB = modelMatrix * vec4(transformed, 1.0);
      #endif
      vBldgWorldPos = wPosB.xyz;
      `
    );

    shader.fragmentShader = `
      uniform vec3 uCameraCenter;
      uniform float uCameraSpan;
      uniform float uDitherEnabled;
      uniform vec3 uOverviewColor;
      uniform float uIsSmallProp;
      varying vec3 vBldgWorldPos;
      varying float vMorphT;
    ` + shader.fragmentShader;

    if (fadeMode === 'near') {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <map_fragment>',
        `#include <map_fragment>
        if (uDitherEnabled > 0.5) {
          if (uIsSmallProp > 0.5) {
            if (vMorphT >= 0.94) discard;
          } else {
            // T3 实体无缝形变：100% 实心不打孔！高对比度门窗/墙面纹理平滑柔化至远景体块主色调
            if (vMorphT >= 0.992) discard;
            float colorMelt = smoothstep(0.12, 0.92, vMorphT);
            diffuseColor.rgb = mix(diffuseColor.rgb, uOverviewColor, colorMelt);
          }
        }
        `
      );
    } else {
      // 远景常驻建筑立体包络：仅在近景建筑已平滑柔化到同色终点 (morphT >= 0.99) 处无缝接棒，0 纱窗重叠
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <alphatest_fragment>',
        `#include <alphatest_fragment>
        if (uDitherEnabled > 0.5) {
          float dist = length(vBldgWorldPos.xz - uCameraCenter.xz);
          float mD = smoothstep(500.0, 680.0, dist);
          float mS = smoothstep(520.0, 740.0, uCameraSpan);
          float mT = max(mD, mS);
          if (mT < 0.985) discard;
        }
        `
      );
    }
  };
  material.customProgramCacheKey = () => `scavenger-b3-bldg-t3-${fadeMode}-${isSmallProp ? 'prop' : 'main'}`;
}

/**
 * 模块化建筑与道具三级渲染系统：
 * 1) 常驻全图建筑立体屋顶简模 (Macro Building Envelopes)：将全图主建筑合批在 1 个常驻 InstancedMesh 中，
 *    确保从 500m 拉远到 9000m 总览时全图城镇、机场机库与工业区 100% 连续立体呈现，0 闪屏；
 * 2) 近中景 (< 580m) 动态加载并实例化渲染带 KTX2 贴图的模块化建筑 GLB (near-kit)，与常驻远景简模通过 8x8 Bayer Dither 无缝交叉溶解；
 * 3) 实时响应编辑器的建筑新增、移动、旋转、缩放、替换原型与删除操作。
 */
export class ModularBuildingSystem {
  constructor({
    b3Manifest,
    buildingIndex,
    physicsEngine,
    loader,
    baseUrl = '/b3/',
    rangeClient = null,
    sharedUniforms,
  }) {
    this.manifest = b3Manifest;
    this.buildingIndex = buildingIndex;
    this.physics = physicsEngine;
    this.loader = loader;
    this.baseUrl = baseUrl;
    this.rangeClient = rangeClient;
    this.sharedUniforms = sharedUniforms || {
      uCameraCenter: {value: new THREE.Vector3(0, 0, 85)},
      uCameraSpan: {value: 140.0},
      uDitherEnabled: {value: 1.0},
    };

    this.root = new THREE.Group();
    this.root.name = 'ModularBuildingSystemRoot';
    this.nearGroup = new THREE.Group();
    this.nearGroup.name = 'NearKitInstances';
    this.macroFarKitGroup = new THREE.Group();
    this.macroFarKitGroup.name = 'MacroNaturalFarKitEnvelopes';
    this.dynamicGroup = new THREE.Group();
    this.dynamicGroup.name = 'EditorDynamicInstances';
    this.root.add(this.macroFarKitGroup, this.nearGroup, this.dynamicGroup);

    this.protoMeta = b3Manifest.building_prototypes;
    this.loadedNearProtos = new Map(); // protoIdx -> Promise<THREE.Group>
    this.nearBatches = new Map();      // protoIdx -> THREE.Group of InstancedMeshes
    this._macroFarKitMatrices = new Map(); // protoIdx -> Array<THREE.Matrix4>

    // 构建全图常驻远景立体建筑包络：方正建筑合批为 1 次 DrawCall 方盒，岩石/油罐等非方形原型分流至真实低模轮廓 far-kit
    this._buildMacroBuildingEnvelopeMesh();
    this.macroFarKitReady = this._buildMacroFarKitMeshes();

    this._lastCellX = -999;
    this._lastCellZ = -999;
    this._lastSpanBucket = -1;
    this.stats = {
      macroBuildings: this.macroCount || 0,
      macroFarKitInstances: this.macroFarKitCount || 0,
      nearActivePrototypes: 0,
      nearBuildingInstances: 0,
      editorDynamicInstances: 0,
    };
  }

  _buildMacroBuildingEnvelopeMesh() {
    const {recordsView} = this.buildingIndex;
    const total = this.buildingIndex.totalInstances;
    // 分流：岩石/球形圆柱油罐 (macro_shape === 'far_kit') 走真实低模轮廓简模；方正建筑 (max(w,d)>=4.2m, h>=2.2m) 走 1 DC 合批包络盒
    // 同时严格过滤超出 9km 作战区边界的装饰性悬空建筑，杜绝边界外悬空街区
    const eligibleIndices = [];
    this._macroFarKitMatrices.clear();
    let farKitCount = 0;

    for (let i = 0; i < total; i++) {
      const off = i * 56;
      const protoIdx = recordsView.getUint16(off, true);
      const meta = this.protoMeta[protoIdx];
      if (!meta || !meta.enabled) continue;
      const txCheck = recordsView.getFloat32(off + 44, true);
      const tzCheck = recordsView.getFloat32(off + 52, true);
      if (txCheck < -4425.0 || txCheck > 4575.0 || tzCheck < -4150.0 || tzCheck > 4850.0) continue;
      const isFarKitShape = meta.macro_shape === 'far_kit' || (meta.name && meta.name.toLowerCase().includes('rock'));
      if (isFarKitShape) {
        const m0 = recordsView.getFloat32(off + 8, true);
        const m1 = recordsView.getFloat32(off + 12, true);
        const m2 = recordsView.getFloat32(off + 16, true);
        const m4 = recordsView.getFloat32(off + 20, true);
        const m5 = recordsView.getFloat32(off + 24, true);
        const m6 = recordsView.getFloat32(off + 28, true);
        const m8 = recordsView.getFloat32(off + 32, true);
        const m9 = recordsView.getFloat32(off + 36, true);
        const m10 = recordsView.getFloat32(off + 40, true);
        const tx = recordsView.getFloat32(off + 44, true);
        const ty = recordsView.getFloat32(off + 48, true);
        const tz = recordsView.getFloat32(off + 52, true);
        const mat4 = new THREE.Matrix4().set(
          m0, m4, m8, tx,
          m1, m5, m9, ty,
          m2, m6, m10, tz,
          0, 0, 0, 1
        );
        if (!this._macroFarKitMatrices.has(protoIdx)) this._macroFarKitMatrices.set(protoIdx, []);
        this._macroFarKitMatrices.get(protoIdx).push(mat4);
        farKitCount++;
        continue;
      }
      const w = meta.bounds_max[0] - meta.bounds_min[0];
      const d = meta.bounds_max[2] - meta.bounds_min[2];
      const h = meta.bounds_max[1] - meta.bounds_min[1];
      if (Math.max(w, d) >= 4.2 && h >= 2.2) {
        eligibleIndices.push(i);
      }
    }
    this.macroFarKitCount = farKitCount;

    const boxGeo = new THREE.BoxGeometry(1, 1, 1);
    boxGeo.translate(0, 0.5, 0); // 底面中心在 (0,0,0)
    const macroMat = new THREE.MeshStandardMaterial({
      roughness: 0.88,
      metalness: 0.08,
    });
    enhanceBuildingMaterial(macroMat, this.sharedUniforms, 'far');

    const count = eligibleIndices.length;
    this.macroCount = count;
    this.macroMesh = new THREE.InstancedMesh(boxGeo, macroMat, count);
    this.macroMesh.frustumCulled = false;
    this.macroMesh.receiveShadow = true;
    this.macroMesh.castShadow = false;
    this.macroMesh.name = 'MacroBuildingEnvelopes';

    const dummy = new THREE.Matrix4();
    const color = new THREE.Color();
    for (let k = 0; k < count; k++) {
      const recIdx = eligibleIndices[k];
      const off = recIdx * 56;
      const protoIdx = recordsView.getUint16(off, true);
      const meta = this.protoMeta[protoIdx];

      const m0 = recordsView.getFloat32(off + 8, true);
      const m1 = recordsView.getFloat32(off + 12, true);
      const m2 = recordsView.getFloat32(off + 16, true);
      const m4 = recordsView.getFloat32(off + 20, true);
      const m5 = recordsView.getFloat32(off + 24, true);
      const m6 = recordsView.getFloat32(off + 28, true);
      const m8 = recordsView.getFloat32(off + 32, true);
      const m9 = recordsView.getFloat32(off + 36, true);
      const m10 = recordsView.getFloat32(off + 40, true);
      const tx = recordsView.getFloat32(off + 44, true);
      const ty = recordsView.getFloat32(off + 48, true);
      const tz = recordsView.getFloat32(off + 52, true);

      const bMin = meta.bounds_min;
      const bMax = meta.bounds_max;
      const bw = Math.max(1.0, bMax[0] - bMin[0]);
      const bh = Math.max(1.0, bMax[1] - bMin[1]);
      const bd = Math.max(1.0, bMax[2] - bMin[2]);
      const lcx = (bMin[0] + bMax[0]) * 0.5;
      const lcz = (bMin[2] + bMax[2]) * 0.5;

      const wcx = tx + m0 * lcx + m8 * lcz;
      const wcy = ty + bMin[1] * Math.abs(m5);
      const wcz = tz + m2 * lcx + m10 * lcz;

      // 保持正行列式旋转缩放，防止镜像实例 (如 Farm_BarnO_L) 在总览包络盒上翻转法线变黑
      const sx = Math.hypot(m0, m2) || 1;
      const sy = Math.abs(m5) || 1;
      const sz = Math.hypot(m8, m10) || 1;
      const yaw = Math.atan2(m2, m0);
      const cy = Math.cos(yaw);
      const syaw = Math.sin(yaw);

      dummy.set(
        cy * sx * bw, 0, -syaw * sz * bd, wcx,
        0, sy * bh, 0, wcy,
        syaw * sx * bw, 0, cy * sz * bd, wcz,
        0, 0, 0, 1
      );
      this.macroMesh.setMatrixAt(k, dummy);
      let oc = meta.overview_color || [0.55, 0.54, 0.52];
      if (Math.max(oc[0], oc[1], oc[2]) < 0.16) oc = [0.48, 0.45, 0.41];
      color.setRGB(oc[0], oc[1], oc[2]);
      this.macroMesh.setColorAt(k, color);
    }
    this.macroMesh.instanceMatrix.needsUpdate = true;
    if (this.macroMesh.instanceColor) this.macroMesh.instanceColor.needsUpdate = true;
    this.root.add(this.macroMesh);
  }

  /**
   * 为岩石、山崖与圆柱/球形储罐等非方形地貌加载极轻真实低模 far-kit (总计 ~48 KB)，
   * 按原型各自合批为 1 个 InstancedMesh，彻底告别图 4 远景黑色方块并保持真实山石轮廓！
   */
  async _buildMacroFarKitMeshes() {
    while (this.macroFarKitGroup.children.length > 0) {
      const ch = this.macroFarKitGroup.children[0];
      this.macroFarKitGroup.remove(ch);
      ch.material?.dispose?.();
    }
    const entries = Array.from(this._macroFarKitMatrices.entries());
    if (entries.length === 0) return;

    await Promise.all(entries.map(async ([protoIdx, matrices]) => {
      const meta = this.protoMeta[protoIdx];
      if (!meta || !meta.far_file || matrices.length === 0) return;
      try {
        let gltf;
        if (this.rangeClient && this.rangeClient.mode === 'archive') {
          const key = 'b3/' + meta.far_file;
          const blobUrl = await this.rangeClient.fetchBlobUrl(key, 'model/gltf-binary');
          try {
            gltf = await this.loader.loadAsync(blobUrl);
          } finally {
            URL.revokeObjectURL(blobUrl);
          }
        } else {
          gltf = await this.loader.loadAsync(this.baseUrl + meta.far_file);
        }
        gltf.scene.updateMatrixWorld(true);
        let oc = meta.overview_color || [0.28, 0.26, 0.23];
        if (Math.max(oc[0], oc[1], oc[2]) < 0.14) oc = [0.27, 0.25, 0.22];
        // 对 4 种岩石原型的线性色再做一次安全对齐，保证既不惨白也不发黑
        const isRockProto = meta.name && meta.name.toLowerCase().includes('rock');
        if (isRockProto && Math.max(oc[0], oc[1], oc[2]) > 0.36) {
          oc = [oc[0] * 0.56, oc[1] * 0.56, oc[2] * 0.56];
        }

        const combined = new THREE.Matrix4();
        gltf.scene.traverse(o => {
          if (!o.isMesh || !o.geometry) return;
          const mat = new THREE.MeshStandardMaterial({
            color: new THREE.Color(oc[0], oc[1], oc[2]),
            roughness: 0.90,
            metalness: 0.04,
            side: THREE.DoubleSide,
          });
          enhanceBuildingMaterial(mat, this.sharedUniforms, 'far', {
            overviewColor: oc,
            isSmallProp: false,
            protoKey: `${meta.id}-farkit`,
          });
          const im = new THREE.InstancedMesh(o.geometry, mat, matrices.length);
          im.frustumCulled = false;
          im.receiveShadow = true;
          im.castShadow = false;
          im.name = `MacroFarKit_${meta.name}`;
          const localMat = o.matrixWorld.clone();
          for (let i = 0; i < matrices.length; i++) {
            combined.multiplyMatrices(matrices[i], localMat);
            im.setMatrixAt(i, combined);
          }
          im.instanceMatrix.needsUpdate = true;
          this.macroFarKitGroup.add(im);
        });
      } catch (err) {
        console.warn('远景真实轮廓 far-kit 加载失败，已跳过:', meta.name, err);
      }
    }));
  }

  async ensurePrototypeLoaded(protoIdx) {
    if (this.loadedNearProtos.has(protoIdx)) return this.loadedNearProtos.get(protoIdx);
    const meta = this.protoMeta[protoIdx];
    if (!meta) return null;

    const loadGltf = async () => {
      if (this.rangeClient && this.rangeClient.mode === 'archive') {
        const key = 'b3/' + meta.near_file;
        const blobUrl = await this.rangeClient.fetchBlobUrl(key, 'model/gltf-binary');
        this.rangeClient.sliceCache?.delete(key);
        try {
          return await this.loader.loadAsync(blobUrl);
        } finally {
          URL.revokeObjectURL(blobUrl);
        }
      }
      return await this.loader.loadAsync(this.baseUrl + meta.near_file);
    };

    const promise = loadGltf().then(gltf => {
      const subMeshes = [];
      gltf.scene.updateMatrixWorld(true);
      gltf.scene.traverse(o => {
        if (o.isMesh && o.geometry) {
          // 严禁对 KHR_mesh_quantization 的 Int16BufferAttribute 直接调用 geo.applyMatrix4 (会导致顶点坐标被缩小 32767 倍)；
          // 保留原量化几何体，将节点解量化矩阵 o.matrixWorld 存入 localMatrix，在实例矩阵乘法中右乘即可！
          // 防御核查：若自定义替换件误含负行列式变换 (det < 0)，在 InstancedMesh + DoubleSide 下会使法线翻转变黑
          if (o.matrixWorld.determinant() < 0) {
            console.warn('建筑原型网格存在负行列式节点变换，InstancedMesh 渲染可能发黑:', meta.name, o.name);
          }
          const mat = o.material.clone();
          const bw = (meta.bounds_max?.[0] ?? 2) - (meta.bounds_min?.[0] ?? -2);
          const bh = (meta.bounds_max?.[1] ?? 2) - (meta.bounds_min?.[1] ?? 0);
          const bd = (meta.bounds_max?.[2] ?? 2) - (meta.bounds_min?.[2] ?? -2);
          const isSmallProp = Math.max(bw, bd) < 4.2 || bh < 2.2;
          let ocNear = meta.overview_color || [0.55, 0.54, 0.52];
          if (meta.name && meta.name.toLowerCase().includes('rock') && Math.max(ocNear[0], ocNear[1], ocNear[2]) > 0.36) {
            ocNear = [ocNear[0] * 0.56, ocNear[1] * 0.56, ocNear[2] * 0.56];
          }
          enhanceBuildingMaterial(mat, this.sharedUniforms, 'near', {
            overviewColor: ocNear,
            isSmallProp,
            protoKey: meta.id,
          });
          subMeshes.push({
            geometry: o.geometry,
            material: mat,
            localMatrix: o.matrixWorld.clone(),
          });
        }
      });
      return subMeshes;
    }).catch(() => []);

    this.loadedNearProtos.set(protoIdx, promise);
    return promise;
  }

  /**
   * 更新相机焦点周围近中景 (< 580m) 的模块化建筑实例批，以及编辑器新增/修改实例。
   */
  async update(center, span, force = false) {
    this.sharedUniforms.uCameraCenter.value.set(center.x, center.y ?? 0, center.z);
    this.sharedUniforms.uCameraSpan.value = span;

    // 零视觉损耗远景裁剪：当 span < 460m 时，全视口最大半径 < 280m，远小于远景包络接管下限 (500m)，
    // 直接关闭远景方盒与 far-kit 岩石群提交，近景可省下 150 万+ 无效顶点与 8 个 Draw Call！
    const showMacro = span >= 460 || (this.sharedUniforms.uDitherEnabled?.value ?? 1.0) < 0.5;
    if (this.macroMesh) this.macroMesh.visible = showMacro;
    if (this.macroFarKitGroup) this.macroFarKitGroup.visible = showMacro;

    const {gridX, gridZ, minX, minZ, chunkM, cellOffsets, cellCounts, recordsView} = this.buildingIndex;
    const cxCenter = Math.floor((center.x - minX) / chunkM);
    const czCenter = Math.floor((center.z - minZ) / chunkM);
    // 按视距动态调整建筑加载半径：214m 城镇区从 580m 收紧至 ~395m (扫描格数降 65%，高密城镇平移建筑数降 60%+，零画质损失)
    const bldgRadius = Math.min(580.0, Math.max(260.0, span * 1.85));
    const spanBucket = span > 760 ? 3 : span > 360 ? 2 : span > 200 ? 1 : 0;

    if (!force && cxCenter === this._lastCellX && czCenter === this._lastCellZ && spanBucket === this._lastSpanBucket) {
      return this.stats;
    }
    this._lastCellX = cxCenter;
    this._lastCellZ = czCenter;
    this._lastSpanBucket = spanBucket;

    if (span > 760) {
      this.nearGroup.visible = false;
      this.stats.nearBuildingInstances = 0;
      return this.stats;
    }
    this.nearGroup.visible = true;

    // 复用矩阵对象池消除平移跨格时的大量 Matrix4 GC 抖动
    if (!this._matPool) this._matPool = [];
    let poolCursor = 0;
    const allocMat4 = () => {
      if (poolCursor < this._matPool.length) return this._matPool[poolCursor++];
      const m = new THREE.Matrix4();
      this._matPool.push(m);
      poolCursor++;
      return m;
    };

    const byProto = new Map(); // protoIdx -> Array<THREE.Matrix4>
    const cellRadius = Math.min(4, Math.max(2, Math.ceil(bldgRadius / chunkM)));

    for (let dz = -cellRadius; dz <= cellRadius; dz++) {
      const cz = czCenter + dz;
      if (cz < 0 || cz >= gridZ) continue;
      for (let dx = -cellRadius; dx <= cellRadius; dx++) {
        const cx = cxCenter + dx;
        if (cx < 0 || cx >= gridX) continue;
        const cellIdx = cz * gridX + cx;
        const count = cellCounts[cellIdx];
        const start = cellOffsets[cellIdx];

        for (let k = 0; k < count; k++) {
          const recIdx = start + k;
          const instKey = `bldg:${cellIdx}:${k}`;
          if (this.physics.deletedKeys.has(instKey) || this.physics.modifiedInstances.has(instKey)) continue;

          const off = recIdx * 56;
          const protoIdx = recordsView.getUint16(off, true);
          const meta = this.protoMeta[protoIdx];
          if (!meta || !meta.enabled) continue;

          const tx = recordsView.getFloat32(off + 44, true);
          const ty = recordsView.getFloat32(off + 48, true);
          const tz = recordsView.getFloat32(off + 52, true);
          // 严格裁剪 9km 作战区外悬空装饰建筑
          if (tx < -4425.0 || tx > 4575.0 || tz < -4150.0 || tz > 4850.0) continue;

          // 检查是否被坐标提示删除或修改覆盖
          let suppressed = false;
          for (const delItem of this.physics.deletedKeys) {
            if (typeof delItem === 'object' && Math.hypot(delItem.x - tx, delItem.z - tz) < 5.0) {
              suppressed = true;
              break;
            }
          }
          if (suppressed) continue;

          if (Math.hypot(tx - center.x, tz - center.z) > bldgRadius) continue;

          const m0 = recordsView.getFloat32(off + 8, true);
          const m1 = recordsView.getFloat32(off + 12, true);
          const m2 = recordsView.getFloat32(off + 16, true);
          const m4 = recordsView.getFloat32(off + 20, true);
          const m5 = recordsView.getFloat32(off + 24, true);
          const m6 = recordsView.getFloat32(off + 28, true);
          const m8 = recordsView.getFloat32(off + 32, true);
          const m9 = recordsView.getFloat32(off + 36, true);
          const m10 = recordsView.getFloat32(off + 40, true);

          const mat4 = allocMat4().set(
            m0, m4, m8, tx,
            m1, m5, m9, ty,
            m2, m6, m10, tz,
            0, 0, 0, 1
          );
          if (!byProto.has(protoIdx)) byProto.set(protoIdx, []);
          byProto.get(protoIdx).push(mat4);
        }
      }
    }

    // 并行加载当前视区所需的原型 GLB
    const neededProtos = Array.from(byProto.keys());
    await Promise.all(neededProtos.map(pIdx => this.ensurePrototypeLoaded(pIdx)));

    // 隐藏不在当前视区的旧批次
    for (const [pIdx, grp] of this.nearBatches.entries()) {
      if (!byProto.has(pIdx)) grp.visible = false;
    }

    let totalNearInst = 0;
    for (const [pIdx, matrices] of byProto.entries()) {
      const subMeshes = await this.ensurePrototypeLoaded(pIdx);
      if (!subMeshes || subMeshes.length === 0) continue;

      let grp = this.nearBatches.get(pIdx);
      const cap = Math.max(32, matrices.length + 16);
      if (!grp || grp.userData.capacity < matrices.length) {
        if (grp) {
          this.nearGroup.remove(grp);
        }
        grp = new THREE.Group();
        grp.userData.capacity = cap;
        const metaP = this.protoMeta[pIdx];
        const pbw = (metaP?.bounds_max?.[0] ?? 4) - (metaP?.bounds_min?.[0] ?? 0);
        const pbh = (metaP?.bounds_max?.[1] ?? 4) - (metaP?.bounds_min?.[1] ?? 0);
        const pbd = (metaP?.bounds_max?.[2] ?? 4) - (metaP?.bounds_min?.[2] ?? 0);
        const castSh = Math.max(pbw, pbd) >= 4.2 && pbh >= 2.2;
        for (const sm of subMeshes) {
          const im = new THREE.InstancedMesh(sm.geometry, sm.material, cap);
          im.castShadow = castSh;
          im.receiveShadow = true;
          im.frustumCulled = false;
          grp.add(im);
        }
        this.nearBatches.set(pIdx, grp);
        this.nearGroup.add(grp);
      }

      grp.visible = true;
      const combinedMat = new THREE.Matrix4();
      for (let sIdx = 0; sIdx < grp.children.length; sIdx++) {
        const im = grp.children[sIdx];
        const localMat = subMeshes[sIdx].localMatrix;
        im.count = matrices.length;
        for (let i = 0; i < matrices.length; i++) {
          combinedMat.multiplyMatrices(matrices[i], localMat);
          im.setMatrixAt(i, combinedMat);
        }
        im.instanceMatrix.needsUpdate = true;
      }
      totalNearInst += matrices.length;
    }

    await this.rebuildEditorDynamicMeshes();
    this.stats.nearActivePrototypes = neededProtos.length;
    this.stats.nearBuildingInstances = totalNearInst;
    return this.stats;
  }

  /**
   * 实时重建编辑器新增与修改实例的 3D 模型节点。
   */
  async rebuildEditorDynamicMeshes() {
    while (this.dynamicGroup.children.length > 0) {
      this.dynamicGroup.remove(this.dynamicGroup.children[0]);
    }

    const allDynamic = [
      ...Array.from(this.physics.addedInstances.values()),
      ...Array.from(this.physics.modifiedInstances.values()),
    ];

    for (const item of allDynamic) {
      const protoMeta = this.physics.protoById.get(item.prototype_id);
      if (!protoMeta) continue;
      let node = null;
      if (protoMeta.prototype_index !== undefined && protoMeta.prototype_index < 125) {
        const subMeshes = await this.ensurePrototypeLoaded(protoMeta.prototype_index);
        if (subMeshes && subMeshes.length > 0) {
          node = new THREE.Group();
          for (const sm of subMeshes) {
            const m = new THREE.Mesh(sm.geometry, sm.material);
            m.matrixAutoUpdate = false;
            m.matrix.copy(sm.localMatrix);
            m.castShadow = true;
            m.receiveShadow = true;
            node.add(m);
          }
        }
      }
      if (!node) {
        // 回退立体包围盒模型
        const he = item.obb.halfExtents;
        const g = new THREE.BoxGeometry(he[0] * 2, he[1] * 2, he[2] * 2);
        g.translate(0, he[1], 0);
        const m = new THREE.MeshStandardMaterial({color: 0xc8b89a, roughness: 0.75});
        node = new THREE.Mesh(g, m);
        node.castShadow = true;
        node.receiveShadow = true;
      }
      node.position.set(item.position[0], item.position[1], item.position[2]);
      node.rotation.y = item.yaw_rad || 0;
      const sc = item.scale || [1, 1, 1];
      node.scale.set(sc[0], sc[1], sc[2]);
      node.userData.instanceId = item.id || item.key;
      this.dynamicGroup.add(node);
    }

    this.stats.editorDynamicInstances = allDynamic.length;
  }
}

/**
 * 浏览器内轻量地图编辑器控制器 (`?mode=editor`)：
 * - 支持建筑/道具/树木实例的新增、地面拖拽移动(自动贴地)、旋转、缩放、替换原型 ID、切换原作/自研插槽、删除；
 * - 支持地形高度笔刷、地表 Splatmap 8 通道材质涂抹笔刷、道路样条控制点拖拽；
 * - 实时可视化渲染：旋转建筑 OBB 碰撞线框、4m 寻路网格禁行区与 M1A2 坦克绕行绿线、AH-64D 直升机 30m 提前越障三维包络轨迹。
 */
export class LightweightMapEditor {
  constructor({
    scene,
    camera,
    renderer,
    support,
    packedHeight,
    splatCtrl,
    roadsData,
    foliageSystem,
    buildingSystem,
    physicsEngine,
  }) {
    this.scene = scene;
    this.camera = camera;
    this.renderer = renderer;
    this.support = support;
    this.packedHeight = packedHeight;
    this.splatCtrl = splatCtrl;
    this.roadsData = roadsData;
    this.foliageSystem = foliageSystem;
    this.buildingSystem = buildingSystem;
    this.physics = physicsEngine;

    this.overlayRoot = new THREE.Group();
    this.overlayRoot.name = 'EditorPhysicsAndNavOverlay';
    this.scene.add(this.overlayRoot);

    this.customRoadMesh = null;
    this.selectedInstanceId = null;
    this.activeSlotGlobal = 'original_refactored';

    // 默认战术验证起点与终点（用于实时展示并验证新建筑对 4m 寻路绕行和直升机 30m 越障的影响）
    this.tankStart = [-45.0, 85.0];
    this.tankGoal = [45.0, 85.0];
    this.heliStart = [-65.0, 85.0];
    this.heliGoal = [65.0, 85.0];

    this.lastNavResult = null;
    this.lastHeliProfile = null;
  }

  /**
   * 1. 在指定世界坐标新增建筑或树木实例，自动贴地并触发碰撞、4m寻路与30m直升机越障重算。
   */
  async addInstanceAt({id, prototype_id, x, z, yaw_rad = 0, scale = [1, 1, 1]}) {
    const gy = this.support.heightAt(x, z);
    const y = Number.isFinite(gy) ? gy : 0;
    const rec = this.physics.addInstance({
      id,
      prototype_id,
      position: [x, y, z],
      yaw_rad,
      scale,
    });
    this.selectedInstanceId = rec.id;
    await this.buildingSystem.rebuildEditorDynamicMeshes();
    this.refreshPhysicsAndNavOverlay();
    return rec;
  }

  /**
   * 2. 移动/旋转/缩放/替换已选中的实例，自动贴地并重算物理与导航。
   */
  async transformInstance(instanceId, {prototype_id, x, z, yaw_rad, scale, origPos}) {
    const position = (x !== undefined && z !== undefined)
      ? [x, this.support.heightAt(x, z) || 0, z]
      : undefined;
    const rec = this.physics.updateInstance(instanceId, {
      prototype_id,
      position,
      yaw_rad,
      scale,
      origPos,
    });
    this.selectedInstanceId = instanceId;
    await this.buildingSystem.rebuildEditorDynamicMeshes();
    this.refreshPhysicsAndNavOverlay();
    return rec;
  }

  /**
   * 3. 删除指定实例并立即更新碰撞、寻路与越障包络。
   */
  async deleteInstance(instanceId, worldPosHint = null) {
    this.physics.deleteInstance(instanceId, worldPosHint);
    if (this.selectedInstanceId === instanceId) this.selectedInstanceId = null;
    await this.buildingSystem.rebuildEditorDynamicMeshes();
    this.refreshPhysicsAndNavOverlay();
  }

  /**
   * 4. 地形高度笔刷（抬升/降低/平滑），实时更新 CPU 高度场与 GPU 顶点位移纹理。
   */
  applyTerrainHeightBrush({x, z, radius = 24.0, deltaH = 3.5, mode = 'raise'}) {
    const region = this.support.applyHeightBrush(x, z, radius, deltaH, mode);
    if (region) {
      syncPackedHeightTexture(this.packedHeight, this.support.hf, region);
      this.physics.terrainStrokes.push({
        x: Number(x.toFixed(2)),
        z: Number(z.toFixed(2)),
        radius: Number(radius.toFixed(2)),
        delta_h: Number(deltaH.toFixed(3)),
        mode,
      });
      // 让受影响区域内的动态实例重新贴合新地形高度
      for (const item of this.physics.addedInstances.values()) {
        if (Math.hypot(item.position[0] - x, item.position[2] - z) <= radius + 4.0) {
          this.physics.updateInstance(item.id, {position: [item.position[0], 0, item.position[2]]});
        }
      }
      this.buildingSystem.rebuildEditorDynamicMeshes();
      this.refreshPhysicsAndNavOverlay();
    }
    return region;
  }

  /**
   * 5. 地表 Splatmap 8 生态通道权重涂抹笔刷，实时刷新 GPU TextureArray 混合。
   */
  applySplatmapBrush({x, z, radius = 22.0, channel = 0, strength = 0.85}) {
    const {width, height, indexData, weightData, indexTexture, weightTexture} = this.splatCtrl;
    const {minX, maxX, minZ, maxZ} = this.support.hf;
    const spanX = maxX - minX;
    const spanZ = maxZ - minZ;

    const uCenter = ((x - minX) / spanX) * width;
    const vCenter = ((z - minZ) / spanZ) * height;
    const rPx = Math.ceil((radius / spanX) * width);

    const u0 = Math.max(0, Math.floor(uCenter - rPx));
    const u1 = Math.min(width - 1, Math.ceil(uCenter + rPx));
    const v0 = Math.max(0, Math.floor(vCenter - rPx));
    const v1 = Math.min(height - 1, Math.ceil(vCenter + rPx));

    const targetCh = Math.max(0, Math.min(7, channel));
    for (let iv = v0; iv <= v1; iv++) {
      for (let iu = u0; iu <= u1; iu++) {
        const dist = Math.hypot(iu - uCenter, iv - vCenter) / Math.max(1, rPx);
        if (dist > 1.0) continue;
        const falloff = (0.5 + 0.5 * Math.cos(dist * Math.PI)) * strength;
        const pOff = (iv * width + iu) * 4;

        const w = new Float32Array(8);
        for (let c = 0; c < 4; c++) {
          w[c] = indexData[pOff + c] * (1.0 - falloff);
          w[c + 4] = weightData[pOff + c] * (1.0 - falloff);
        }
        w[targetCh] += 255.0 * falloff;
        let sum = 0;
        for (let c = 0; c < 8; c++) sum += w[c];
        sum = Math.max(1e-4, sum);
        for (let c = 0; c < 4; c++) {
          indexData[pOff + c] = Math.min(255, Math.round((w[c] / sum) * 255));
          weightData[pOff + c] = Math.min(255, Math.round((w[c + 4] / sum) * 255));
        }
      }
    }
    indexTexture.image.data.set(indexData);
    weightTexture.image.data.set(weightData);
    indexTexture.needsUpdate = true;
    weightTexture.needsUpdate = true;

    this.physics.splatStrokes.push({
      x: Number(x.toFixed(2)),
      z: Number(z.toFixed(2)),
      radius: Number(radius.toFixed(2)),
      channel: targetCh,
      strength: Number(strength.toFixed(2)),
    });
  }

  /**
   * 6. 拖拽或新增道路中心线样条控制点，自动压平路基并生成贴地路面带状网格。
   */
  editRoadSpline({splineId, points, width = 12.0}) {
    const splineObj = {
      id: splineId || `custom-road-${this.physics.modifiedRoadSplines.size + 1}`,
      width,
      points: points.map(p => [p[0], this.support.heightAt(p[0], p[2]) || p[1] || 0, p[2]]),
      custom: true,
      modified: true,
    };
    const region = this.support.applyRoadSplineFlattening(splineObj.points, width);
    if (region) {
      syncPackedHeightTexture(this.packedHeight, this.support.hf, region);
    }
    this.physics.modifiedRoadSplines.set(splineObj.id, splineObj);

    if (this.customRoadMesh) {
      this.scene.remove(this.customRoadMesh);
      this.customRoadMesh.geometry.dispose();
    }
    const allCustomSplines = Array.from(this.physics.modifiedRoadSplines.values());
    this.customRoadMesh = buildCustomRoadRibbonMesh(allCustomSplines, (rx, rz) => this.support.heightAt(rx, rz));
    this.scene.add(this.customRoadMesh);
    return splineObj;
  }

  /**
   * 7. 刷新 3D 物理碰撞盒、4m 坦克绕行寻路绿线与 AH-64D 直升机 30m 提前越障三维包络线框。
   */
  refreshPhysicsAndNavOverlay() {
    while (this.overlayRoot.children.length > 0) {
      const ch = this.overlayRoot.children[0];
      this.overlayRoot.remove(ch);
      ch.geometry?.dispose?.();
    }

    const navRes = this.physics.findLocalPath4m(
      this.tankStart[0],
      this.tankStart[1],
      this.tankGoal[0],
      this.tankGoal[1],
      5.5
    );
    const heliProfile = this.physics.buildHelicopterFlightProfile(
      this.heliStart[0],
      this.heliStart[1],
      this.heliGoal[0],
      this.heliGoal[1],
      48
    );
    this.lastNavResult = navRes;
    this.lastHeliProfile = heliProfile;

    // A. 绘制活动旋转建筑 OBB 碰撞盒线框 (橙红色)
    const boxEdgesMat = new THREE.LineBasicMaterial({color: 0xff5e36, linewidth: 2});
    for (const obb of navRes.activeOBBs) {
      const [hx, hy, hz] = obb.halfExtents;
      const g = new THREE.BoxGeometry(hx * 2, hy * 2, hz * 2);
      const edges = new THREE.EdgesGeometry(g);
      g.dispose();
      const line = new THREE.LineSegments(edges, boxEdgesMat);
      line.position.set(obb.center[0], obb.center[1], obb.center[2]);
      line.rotation.y = obb.yawRad || 0;
      this.overlayRoot.add(line);
    }

    // B. 绘制 4m 寻路网格中受阻挡的 4mx4m 禁行格底面 (半透明红色方块)
    const {minX, minZ, step, cols, rows} = navRes.gridBounds;
    const blockedPositions = [];
    for (let iz = 0; iz < rows; iz++) {
      for (let ix = 0; ix < cols; ix++) {
        if (navRes.blockedGrid[iz * cols + ix]) {
          const wx = minX + (ix + 0.5) * step;
          const wz = minZ + (iz + 0.5) * step;
          const wy = (this.support.heightAt(wx, wz) || 0) + 0.18;
          blockedPositions.push(wx, wy, wz);
        }
      }
    }
    if (blockedPositions.length > 0) {
      const bCount = blockedPositions.length / 3;
      const cellGeo = new THREE.PlaneGeometry(step * 0.86, step * 0.86);
      cellGeo.rotateX(-Math.PI / 2);
      const cellMat = new THREE.MeshBasicMaterial({color: 0xff3b30, transparent: true, opacity: 0.34, depthWrite: false});
      const inst = new THREE.InstancedMesh(cellGeo, cellMat, bCount);
      const dummy = new THREE.Matrix4();
      for (let i = 0; i < bCount; i++) {
        dummy.makeTranslation(blockedPositions[i * 3], blockedPositions[i * 3 + 1], blockedPositions[i * 3 + 2]);
        inst.setMatrixAt(i, dummy);
      }
      inst.instanceMatrix.needsUpdate = true;
      this.overlayRoot.add(inst);
    }

    // C. 绘制 M1A2 地面单位 4m A* 绕行路径 (亮绿色粗轨线 + 路径节点球)
    if (navRes.waypoints.length >= 2) {
      const curvePts = navRes.waypoints.map(p => new THREE.Vector3(p[0], p[1] + 0.45, p[2]));
      const pathCurve = new THREE.CatmullRomCurve3(curvePts);
      const tubeGeo = new THREE.TubeGeometry(pathCurve, Math.max(24, curvePts.length * 4), 0.65, 6, false);
      const tubeMat = new THREE.MeshBasicMaterial({color: 0x32e875});
      this.overlayRoot.add(new THREE.Mesh(tubeGeo, tubeMat));
    }

    // D. 绘制 AH-64D 直升机 30m 提前越障三维包络轨迹 (青蓝色高空航迹管 + 垂直高度标尺线)
    if (heliProfile.length >= 2) {
      const heliVecs = heliProfile.map(p => new THREE.Vector3(p[0], p[1], p[2]));
      const heliCurve = new THREE.CatmullRomCurve3(heliVecs);
      const hTubeGeo = new THREE.TubeGeometry(heliCurve, 64, 0.65, 6, false);
      const hTubeMat = new THREE.MeshBasicMaterial({color: 0x38d6ff});
      this.overlayRoot.add(new THREE.Mesh(hTubeGeo, hTubeMat));

      const dropVerts = [];
      for (let i = 0; i < heliProfile.length; i += 2) {
        const [hx, hy, hz] = heliProfile[i];
        const gy = this.support.heightAt(hx, hz) || 0;
        dropVerts.push(hx, gy, hz, hx, hy, hz);
      }
      const dropGeo = new THREE.BufferGeometry();
      dropGeo.setAttribute('position', new THREE.Float32BufferAttribute(dropVerts, 3));
      const dropMat = new THREE.LineBasicMaterial({color: 0x38d6ff, transparent: true, opacity: 0.45});
      this.overlayRoot.add(new THREE.LineSegments(dropGeo, dropMat));
    }

    return {
      navFound: navRes.found,
      navWaypointsCount: navRes.waypoints.length,
      navWaypoints: navRes.waypoints,
      heliProfile,
      maxHeliAltitudeY: Math.max(...heliProfile.map(p => p[1])),
      activeOBBsCount: navRes.activeOBBs.length,
    };
  }
}
