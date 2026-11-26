/**
 * Scavenger 武器库（/armory）Airbase 微缩沙盘 3D 展台环境构建模块。
 * - 默认启用与 4C 9km 战场同源的「沙盘日光」风格：天蓝暖米梯度天空球 + 远距柔雾 + 混凝土停机坪 + 沙土草地基底 + 远景机库/油桶/松树丛；
 * - 支持切换次选「机库夜光」棚拍模式，突出载具金属边棱与镀膜玻璃反光；
 * - 全部几何与程序化纹理本地生成，零外部模型下载；支持根据当前载具包围盒半径动态收紧阴影视锥。
 */
import * as THREE from 'three';
import { createStylizedVehicleEnvMaps } from '/src/程序化载具/载具风格化材质.mjs';

function createGradientSkyMesh() {
  const geo = new THREE.SphereGeometry(260, 28, 18);
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      uTop: { value: new THREE.Color(0x85b0d8) },
      uMid: { value: new THREE.Color(0xc7dbe6) },
      uBot: { value: new THREE.Color(0xe2dac7) },
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
          ? mix(uMid, uTop, pow(h, 0.58))
          : mix(uMid, uBot, pow(-h, 0.42));
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'armory_sky_dome';
  return { mesh, geo, mat };
}

function createSandTerrainTexture() {
  const cv = document.createElement('canvas');
  cv.width = 512;
  cv.height = 512;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#c8bd9e';
  ctx.fillRect(0, 0, 512, 512);

  // 柔和微缩沙土与浅苔草杂点（确定性伪随机，保证贴图四方连续）
  let seed = 1337;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  for (let i = 0; i < 3600; i++) {
    const x = rnd() * 512;
    const y = rnd() * 512;
    const r = 1.2 + rnd() * 3.8;
    const t = rnd();
    ctx.fillStyle = t < 0.45 ? 'rgba(179, 166, 134, 0.24)' : t < 0.85 ? 'rgba(218, 208, 182, 0.22)' : 'rgba(138, 152, 108, 0.14)';
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  const tex = new THREE.CanvasTexture(cv);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(24, 24);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function createApronConcreteTexture() {
  const cv = document.createElement('canvas');
  cv.width = 1024;
  cv.height = 1024;
  const ctx = cv.getContext('2d');
  const cx = 512;
  const cy = 512;

  ctx.fillStyle = '#b9b4a4';
  ctx.fillRect(0, 0, 1024, 1024);

  // 细腻水泥骨料质感
  let seed = 4242;
  const rnd = () => {
    seed = (seed * 48271) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  for (let i = 0; i < 5200; i++) {
    const x = rnd() * 1024;
    const y = rnd() * 1024;
    ctx.fillStyle = rnd() > 0.5 ? 'rgba(235, 232, 222, 0.14)' : 'rgba(128, 124, 112, 0.14)';
    ctx.fillRect(x, y, 2, 2);
  }

  // 水泥板块伸缩缝
  ctx.strokeStyle = 'rgba(92, 88, 78, 0.34)';
  ctx.lineWidth = 2;
  const step = 128;
  for (let p = step; p < 1024; p += step) {
    ctx.beginPath();
    ctx.moveTo(p, 0);
    ctx.lineTo(p, 1024);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, p);
    ctx.lineTo(1024, p);
    ctx.stroke();
  }

  // 中心细微胎痕与机油晕斑
  const wear = ctx.createRadialGradient(cx, cy, 24, cx, cy, 340);
  wear.addColorStop(0, 'rgba(90, 86, 78, 0.14)');
  wear.addColorStop(0.55, 'rgba(90, 86, 78, 0.05)');
  wear.addColorStop(1, 'rgba(90, 86, 78, 0)');
  ctx.fillStyle = wear;
  ctx.beginPath();
  ctx.arc(cx, cy, 340, 0, Math.PI * 2);
  ctx.fill();

  // 外围双圈淡黄战术警戒线
  ctx.strokeStyle = 'rgba(222, 184, 72, 0.85)';
  ctx.lineWidth = 8;
  ctx.beginPath();
  ctx.arc(cx, cy, 466, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = 'rgba(236, 206, 108, 0.52)';
  ctx.lineWidth = 3;
  ctx.setLineDash([20, 14]);
  ctx.beginPath();
  ctx.arc(cx, cy, 444, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);

  // 内圈与 4 方位校准刻度标线
  ctx.strokeStyle = 'rgba(238, 234, 220, 0.55)';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(cx, cy, 245, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = 'rgba(238, 234, 220, 0.44)';
  ctx.lineWidth = 2.5;
  for (let a = 0; a < 4; a++) {
    const ang = (a * Math.PI) / 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(ang) * 70, cy + Math.sin(ang) * 70);
    ctx.lineTo(cx + Math.cos(ang) * 432, cy + Math.sin(ang) * 432);
    ctx.stroke();
  }

  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

function createTaxiwayTexture() {
  const cv = document.createElement('canvas');
  cv.width = 512;
  cv.height = 512;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#757774';
  ctx.fillRect(0, 0, 512, 512);
  ctx.fillStyle = 'rgba(226, 192, 82, 0.85)';
  ctx.fillRect(14, 0, 8, 512);
  ctx.fillRect(490, 0, 8, 512);
  ctx.fillStyle = 'rgba(244, 244, 238, 0.86)';
  for (let y = 18; y < 512; y += 96) {
    ctx.fillRect(251, y, 10, 54);
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 10);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function buildMiniHangar(disposables) {
  const group = new THREE.Group();
  const roofMat = new THREE.MeshStandardMaterial({ color: 0xb7b2a5, roughness: 0.84, metalness: 0.08 });
  const wallMat = new THREE.MeshStandardMaterial({ color: 0x999589, roughness: 0.9, metalness: 0.04 });
  const gateMat = new THREE.MeshStandardMaterial({ color: 0x2f3430, roughness: 0.95, metalness: 0.02 });
  disposables.push(roofMat, wallMat, gateMat);

  // 拱形半圆柱机库顶棚
  const archGeo = new THREE.CylinderGeometry(8.5, 8.5, 22, 20, 1, false, 0, Math.PI);
  archGeo.rotateZ(Math.PI / 2);
  archGeo.rotateY(Math.PI / 2);
  const arch = new THREE.Mesh(archGeo, roofMat);
  arch.position.set(0, 2.2, 0);
  arch.castShadow = true;
  arch.receiveShadow = true;

  // 底层侧墙基座
  const baseGeo = new THREE.BoxGeometry(17.1, 2.4, 22.1);
  const base = new THREE.Mesh(baseGeo, wallMat);
  base.position.set(0, 1.2, 0);
  base.castShadow = true;
  base.receiveShadow = true;

  // 前后大门凹影块
  const doorGeo = new THREE.BoxGeometry(10.8, 5.4, 22.3);
  const door = new THREE.Mesh(doorGeo, gateMat);
  door.position.set(0, 2.7, 0);

  disposables.push(archGeo, baseGeo, doorGeo);
  group.add(base, arch, door);
  return group;
}

function buildSupplyCluster(disposables) {
  const group = new THREE.Group();
  const drumGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.96, 10);
  const crateGeo = new THREE.BoxGeometry(1.4, 0.88, 1.1);
  const palette = [0xbf4a34, 0xd4a438, 0x456b42, 0x3b6787];
  const drumMats = palette.map(hex => new THREE.MeshStandardMaterial({ color: hex, roughness: 0.72, metalness: 0.08 }));
  const crateMat = new THREE.MeshStandardMaterial({ color: 0xaa8353, roughness: 0.88, metalness: 0.02 });
  disposables.push(drumGeo, crateGeo, crateMat, ...drumMats);

  const offsets = [
    [-1.4, 0.48, -0.5], [-0.6, 0.48, -0.4], [0.2, 0.48, -0.6], [-1.0, 0.48, 0.35],
    [-0.2, 0.48, 0.4], [0.55, 0.48, 0.2], [1.1, 0.48, -0.35], [0.95, 0.48, 0.5],
  ];
  offsets.forEach(([ox, oy, oz], idx) => {
    const m = new THREE.Mesh(drumGeo, drumMats[idx % drumMats.length]);
    m.position.set(ox, oy, oz);
    m.castShadow = true;
    m.receiveShadow = true;
    group.add(m);
  });

  const crate1 = new THREE.Mesh(crateGeo, crateMat);
  crate1.position.set(-2.3, 0.44, 0.25);
  crate1.rotation.y = 0.22;
  crate1.castShadow = true;
  crate1.receiveShadow = true;

  const crate2 = new THREE.Mesh(crateGeo, crateMat);
  crate2.position.set(-2.2, 1.3, 0.25);
  crate2.scale.setScalar(0.76);
  crate2.rotation.y = -0.35;
  crate2.castShadow = true;
  group.add(crate1, crate2);
  return group;
}

function buildPineTree(scale, disposables) {
  const group = new THREE.Group();
  const trunkGeo = new THREE.CylinderGeometry(0.22, 0.34, 1.6, 7);
  const trunkMat = new THREE.MeshStandardMaterial({ color: 0x5c4632, roughness: 0.94, metalness: 0.0 });
  const tierColors = [0x477a38, 0x568d41, 0x67a04b];
  disposables.push(trunkGeo, trunkMat);

  const trunk = new THREE.Mesh(trunkGeo, trunkMat);
  trunk.position.y = 0.8;
  trunk.castShadow = true;
  group.add(trunk);

  const tiers = [
    { r: 1.95, h: 2.8, y: 2.5 },
    { r: 1.5, h: 2.5, y: 4.1 },
    { r: 1.0, h: 2.1, y: 5.5 },
  ];
  tiers.forEach((t, i) => {
    const cg = new THREE.ConeGeometry(t.r, t.h, 8);
    const cm = new THREE.MeshStandardMaterial({ color: tierColors[i], roughness: 0.9, metalness: 0.0 });
    disposables.push(cg, cm);
    const cone = new THREE.Mesh(cg, cm);
    cone.position.y = t.y;
    cone.rotation.y = i * 0.45;
    cone.castShadow = true;
    cone.receiveShadow = true;
    group.add(cone);
  });

  group.scale.setScalar(scale);
  return group;
}

/**
 * 构建并绑定武器库（/armory）3D 沙盘展台场景与双模式灯光。
 */
export function createArmorySandboxStage(scene, renderer) {
  const disposables = [];
  const envMaps = createStylizedVehicleEnvMaps(renderer);
  const sky = createGradientSkyMesh();
  disposables.push(sky.geo, sky.mat);
  scene.add(sky.mesh);

  const hemiLight = new THREE.HemisphereLight(0xeef4e2, 0x68765c, 1.55);
  const keyLight = new THREE.DirectionalLight(0xffeacc, 3.25);
  keyLight.position.set(-26, 44, 22);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  keyLight.shadow.camera.near = 2;
  keyLight.shadow.camera.far = 160;
  keyLight.shadow.camera.left = -22;
  keyLight.shadow.camera.right = 22;
  keyLight.shadow.camera.top = 22;
  keyLight.shadow.camera.bottom = -22;
  keyLight.shadow.bias = -0.00035;
  keyLight.shadow.normalBias = 0.025;

  const rimLight = new THREE.DirectionalLight(0xbcd8f5, 0.25);
  rimLight.position.set(22, 18, -20);
  scene.add(hemiLight, keyLight, rimLight);

  // 中心混凝土停机整备坪（沙盘与机库模式共用并自适应切换色调）
  const apronTex = createApronConcreteTexture();
  const apronGeo = new THREE.CircleGeometry(18.5, 64);
  const apronMat = new THREE.MeshStandardMaterial({
    map: apronTex,
    roughness: 0.91,
    metalness: 0.02,
    envMapIntensity: 0.3,
  });
  const apronMesh = new THREE.Mesh(apronGeo, apronMat);
  apronMesh.rotation.x = -Math.PI / 2;
  apronMesh.position.y = 0;
  apronMesh.receiveShadow = true;

  const curbGeo = new THREE.CylinderGeometry(18.5, 19.6, 0.05, 64, 1, true);
  const curbMat = new THREE.MeshStandardMaterial({ color: 0x9b9586, roughness: 0.94, metalness: 0.02 });
  const curbMesh = new THREE.Mesh(curbGeo, curbMat);
  curbMesh.position.y = -0.025;
  curbMesh.receiveShadow = true;
  disposables.push(apronTex, apronGeo, apronMat, curbGeo, curbMat);
  scene.add(apronMesh, curbMesh);

  // 户外沙盘组（默认开启；切至机库夜光时隐藏外场日光景，聚焦机身金属质感）
  const outdoorGroup = new THREE.Group();
  outdoorGroup.name = 'armory_outdoor_sandbox';

  const sandTex = createSandTerrainTexture();
  const groundGeo = new THREE.CircleGeometry(250, 48);
  const groundMat = new THREE.MeshStandardMaterial({
    map: sandTex,
    roughness: 0.96,
    metalness: 0.0,
    envMapIntensity: 0.16,
  });
  const groundMesh = new THREE.Mesh(groundGeo, groundMat);
  groundMesh.rotation.x = -Math.PI / 2;
  groundMesh.position.y = -0.05;
  groundMesh.receiveShadow = true;
  disposables.push(sandTex, groundGeo, groundMat);
  outdoorGroup.add(groundMesh);

  const taxiTex = createTaxiwayTexture();
  const taxiGeo = new THREE.PlaneGeometry(14, 180);
  const taxiMat = new THREE.MeshStandardMaterial({
    map: taxiTex,
    roughness: 0.92,
    metalness: 0.02,
    envMapIntensity: 0.22,
  });
  const taxiMesh = new THREE.Mesh(taxiGeo, taxiMat);
  taxiMesh.rotation.x = -Math.PI / 2;
  taxiMesh.rotation.z = Math.PI / 2;
  taxiMesh.position.set(0, -0.025, -28);
  taxiMesh.receiveShadow = true;
  disposables.push(taxiTex, taxiGeo, taxiMat);
  outdoorGroup.add(taxiMesh);

  // 后侧拱形机库与前线补给堆（位于轨道安全圈 r > 22m 外侧）
  const hangar = buildMiniHangar(disposables);
  hangar.position.set(-28, -0.03, -44);
  hangar.rotation.y = 0.22;
  outdoorGroup.add(hangar);

  const supplyRight = buildSupplyCluster(disposables);
  supplyRight.position.set(22, -0.03, -24);
  supplyRight.rotation.y = -0.4;
  const supplyLeft = buildSupplyCluster(disposables);
  supplyLeft.position.set(-23, -0.03, -19);
  supplyLeft.rotation.y = 0.55;
  outdoorGroup.add(supplyRight, supplyLeft);

  // 远角微缩松树丛
  const treeSpecs = [
    [29, -36, 1.05], [34, -33, 0.9], [32, -41, 1.15], [39, -38, 0.85],
    [35, -25, 0.95], [42, -29, 1.1], [-44, -36, 1.0], [-48, -30, 0.88],
    [-41, -24, 1.12], [24, -47, 0.95], [14, -49, 1.08], [-10, -52, 0.92],
  ];
  for (const [tx, tz, sc] of treeSpecs) {
    const tree = buildPineTree(sc, disposables);
    tree.position.set(tx, -0.04, tz);
    outdoorGroup.add(tree);
  }
  scene.add(outdoorGroup);

  // 机库夜光模式专属地面基底与战术校准网格（沙盘模式隐藏）
  const armoryFloorGeo = new THREE.CircleGeometry(220, 48);
  const armoryFloorMat = new THREE.MeshStandardMaterial({
    color: 0x181d1a,
    roughness: 0.94,
    metalness: 0.04,
  });
  const armoryFloor = new THREE.Mesh(armoryFloorGeo, armoryFloorMat);
  armoryFloor.rotation.x = -Math.PI / 2;
  armoryFloor.position.y = -0.05;
  armoryFloor.receiveShadow = true;
  armoryFloor.visible = false;

  const armoryGrid = new THREE.GridHelper(36, 18, 0x4b5c4e, 0x2b342d);
  armoryGrid.position.y = 0.008;
  armoryGrid.visible = false;
  disposables.push(armoryFloorGeo, armoryFloorMat);
  scene.add(armoryFloor, armoryGrid);

  let currentMode = 'sandbox';

  function setMode(mode) {
    const isSand = mode !== 'armory';
    currentMode = isSand ? 'sandbox' : 'armory';

    if (isSand) {
      sky.mat.uniforms.uTop.value.setHex(0x85b0d8);
      sky.mat.uniforms.uMid.value.setHex(0xc7dbe6);
      sky.mat.uniforms.uBot.value.setHex(0xe2dac7);
      scene.background = new THREE.Color(0xc7dbe6);
      scene.fog = new THREE.Fog(0xdcd4c1, 72, 225);
      scene.environment = envMaps.sandboxEnv;
      scene.environmentIntensity = 0.48;
      hemiLight.color.setHex(0xeef4e2);
      hemiLight.groundColor.setHex(0x68765c);
      hemiLight.intensity = 1.55;
      keyLight.color.setHex(0xffeacc);
      keyLight.intensity = 3.25;
      keyLight.position.set(-26, 44, 22);
      rimLight.intensity = 0.25;
      apronMat.color.setHex(0xffffff);
      curbMat.color.setHex(0x9b9586);
    } else {
      sky.mat.uniforms.uTop.value.setHex(0x121820);
      sky.mat.uniforms.uMid.value.setHex(0x1c242b);
      sky.mat.uniforms.uBot.value.setHex(0x262e29);
      scene.background = new THREE.Color(0x181e1c);
      scene.fog = new THREE.Fog(0x1d2320, 58, 185);
      scene.environment = envMaps.armoryEnv;
      scene.environmentIntensity = 0.72;
      hemiLight.color.setHex(0xdce6f0);
      hemiLight.groundColor.setHex(0x343a34);
      hemiLight.intensity = 0.95;
      keyLight.color.setHex(0xfff1dc);
      keyLight.intensity = 2.55;
      keyLight.position.set(18, 30, 16);
      rimLight.intensity = 0.9;
      apronMat.color.setHex(0x414842);
      curbMat.color.setHex(0x2b312c);
    }

    outdoorGroup.visible = isSand;
    armoryFloor.visible = !isSand;
    armoryGrid.visible = !isSand;
    return currentMode;
  }

  function fitShadowToRadius(radius) {
    const half = Math.max(14, Math.min(42, (Number(radius) || 6) * 2.2));
    keyLight.shadow.camera.left = -half;
    keyLight.shadow.camera.right = half;
    keyLight.shadow.camera.top = half;
    keyLight.shadow.camera.bottom = -half;
    keyLight.shadow.camera.updateProjectionMatrix();
  }

  setMode('sandbox');

  return Object.freeze({
    setMode,
    getMode: () => currentMode,
    getEnvMap: () => (currentMode === 'sandbox' ? envMaps.sandboxEnv : envMaps.armoryEnv),
    hasBothEnvs: () => Boolean(envMaps.sandboxEnv && envMaps.armoryEnv),
    fitShadowToRadius,
    dispose() {
      envMaps.dispose();
      armoryGrid.geometry?.dispose?.();
      if (Array.isArray(armoryGrid.material)) armoryGrid.material.forEach(m => m?.dispose?.());
      else armoryGrid.material?.dispose?.();
      for (const d of disposables) d?.dispose?.();
    },
  });
}
