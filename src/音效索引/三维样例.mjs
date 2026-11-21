import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

function disposeTree(root) {
  const disposed = new Set();
  root.traverse(object => {
    object.geometry?.dispose();
    for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
      if (!material || disposed.has(material)) continue;
      disposed.add(material);
      for (const value of Object.values(material)) if (value?.isTexture && !disposed.has(value)) {
        disposed.add(value); value.dispose(); value.source?.data?.close?.();
      }
      material.dispose();
    }
  });
  root.removeFromParent();
}

/** 只负责独立样例场景；射击点为明确标注的可调示意点。 */
export class SampleStage {
  constructor(container, placeholder, onState) {
    Object.assign(this, {container, placeholder, onState});
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 400);
    this.camera.position.set(22, 15, 23);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#101a13');
    this.vehicle = new THREE.Group();
    this.body = new THREE.Object3D();
    this.muzzle = new THREE.Object3D();
    this.impact = new THREE.Object3D();
    this.vehicle.add(this.body, this.muzzle);
    this.scene.add(this.vehicle, this.impact);
    this.body.position.y = 1.2;
    this.muzzle.position.set(0, 1.4, -4);
    this.impact.position.set(10, 0.25, -6);
    this.model = null;
    this.loadGeneration = 0;
    this.flashUntil = 0;
    this.motionStarted = 0;
  }

  initialize() {
    if (this.renderer) return;
    const renderer = new THREE.WebGLRenderer({antialias: true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.4;
    this.container.append(renderer.domElement);
    this.renderer = renderer;
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.target.set(0, 2, 0);
    this.controls.minDistance = 6;
    this.controls.maxDistance = 65;
    this.controls.maxPolarAngle = Math.PI / 2.02;
    this.scene.add(new THREE.HemisphereLight('#d9e3ce', '#3c4832', 3));
    const light = new THREE.DirectionalLight('#fff0d6', 3.5);
    light.position.set(10, 20, 12);
    this.scene.add(light);
    this.scene.add(new THREE.GridHelper(100, 50, '#607653', '#293d2d'));
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), new THREE.MeshBasicMaterial({visible: false}));
    this.ground.rotation.x = -Math.PI / 2;
    this.scene.add(this.ground);
    for (const [anchor, color] of [[this.body, '#b5d4a0'], [this.muzzle, '#eabb64'], [this.impact, '#e68f77']]) {
      const marker = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 8), new THREE.MeshBasicMaterial({color, depthTest: false}));
      marker.renderOrder = 10;
      anchor.add(marker);
    }
    let down;
    renderer.domElement.addEventListener('pointerdown', event => { down = [event.clientX, event.clientY]; });
    renderer.domElement.addEventListener('pointerup', event => {
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), this.camera);
      const hit = ray.intersectObject(this.ground)[0];
      if (hit) { this.impact.position.copy(hit.point); this.impact.position.y = 0.25; this.onState('命中点已更新'); }
    });
    this.resize = new ResizeObserver(() => {
      const {width, height} = this.container.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    });
    this.resize.observe(this.container);
    this.animate = () => {
      this.frame = requestAnimationFrame(this.animate);
      const now = performance.now();
      if (this.motionStarted) this.vehicle.position.x = Math.sin((now - this.motionStarted) / 2200) * 4;
      this.muzzle.children[0].scale.setScalar(now < this.flashUntil ? 2.4 : 1);
      this.controls.update();
      renderer.render(this.scene, this.camera);
    };
    this.animate();
  }

  async load(vehicle, baseUrl) {
    if (!vehicle.sample_model_url) throw new Error('当前型号不在三款三维样例范围内');
    this.initialize();
    this.clear();
    const generation = this.loadGeneration;
    this.placeholder.hidden = false;
    this.onState('正在读取真实 GLB…');
    const gltf = await new GLTFLoader().loadAsync(new URL(vehicle.sample_model_url, baseUrl).href);
    if (generation !== this.loadGeneration) { disposeTree(gltf.scene); return false; }
    const root = gltf.scene;
    const bounds = new THREE.Box3().setFromObject(root);
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    if (![size.x, size.y, size.z].every(Number.isFinite) || Math.max(size.x, size.y, size.z) <= 0) { disposeTree(root); throw new Error('模型包围盒无效'); }
    const scale = 14 / Math.max(size.x, size.y, size.z);
    root.position.sub(center);
    root.position.y += size.y / 2;
    const normalized = new THREE.Group();
    normalized.add(root);
    normalized.scale.setScalar(scale);
    this.vehicle.add(normalized);
    this.model = normalized;
    this.body.position.y = Math.max(0.5, size.y * scale / 2);
    this.placeholder.hidden = true;
    this.onState(`${vehicle.name} · 真实静态模型已加载`);
    return true;
  }

  move() { this.motionStarted = performance.now(); }
  stopMotion() { this.motionStarted = 0; this.vehicle.position.x = 0; }
  flash() { this.flashUntil = performance.now() + 130; }

  clear() {
    this.loadGeneration += 1;
    this.stopMotion();
    if (this.model) disposeTree(this.model);
    this.model = null;
    this.placeholder.hidden = false;
  }

  dispose() {
    this.clear();
    cancelAnimationFrame(this.frame);
    this.resize?.disconnect();
    this.controls?.dispose();
    disposeTree(this.scene);
    this.renderer?.dispose();
    this.renderer?.domElement.remove();
  }
}
