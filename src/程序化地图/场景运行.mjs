import * as THREE from 'three';
import { createSilhouetteSvg } from '/ui/图标与标绘.mjs';

/**
 * 从 513x513 BACH 粗层高度二进制构造快速启动高度场，使首屏在 1.5s 内无需等待 4.8MB 精细高度场即可立即呈现立体地形！
 */
export function buildCoarseHeightfieldFallback(bachBuffer, bounds) {
  const dv = new DataView(bachBuffer);
  // BACH 头部: 4s magic, u32 ver, u32 w(513), u32 h(513), f32 minX, f32 minZ, f32 stepX, f32 stepZ
  const cw = dv.getUint32(8, true) || 513;
  const ch = dv.getUint32(12, true) || 513;
  const floats = new Float32Array(bachBuffer, 32, cw * ch);

  const minH = bounds.minH ?? -40.0;
  const maxH = bounds.maxH ?? 120.0;
  const invRange = 65534.0 / (maxH - minH);

  // 直接采用 513x513 粗层网格 (~526 KB) 极速初始化首屏，避免分配 81MB 临时缓冲
  const qGrid = new Uint16Array(cw * ch);
  for (let i = 0; i < cw * ch; i++) {
    const hVal = floats[i];
    qGrid[i] = Number.isFinite(hVal)
      ? Math.max(0, Math.min(65534, Math.round((hVal - minH) * invRange)))
      : 16383;
  }

  return {
    width: cw,
    height: ch,
    minX: bounds.minX,
    minZ: bounds.minZ,
    maxX: bounds.maxX,
    maxZ: bounds.maxZ,
    stepX: (bounds.maxX - bounds.minX) / (cw - 1),
    stepZ: (bounds.maxZ - bounds.minZ) / (ch - 1),
    minH,
    maxH,
    qGrid,
    roleGrid: null,
    nyGrid: null,
    bytes: qGrid.byteLength,
  };
}

/**
 * G01 兼容空地载具与 100 单位混编交战控制器：
 * - 支持 M1A2 地面主战坦克 (4m A* 寻路绕行 + 坡面贴地朝向)；
 * - 支持 AH-64D 武装直升机 (30m 前瞻平滑抬升越障)；
 * - 支持 A-10C 固定翼攻击机 (234m 安全飞行高度闭合机动)；
 * - 支持 100 混编单位连续移动与弹道射击仿真 (单 InstancedMesh 合批绘制，极低 CPU/GPU 开销)。
 */
export class TacticalVehicleAndCombatSystem {
  constructor({scene, camera, hostElement, support, physicsEngine, loader}) {
    this.scene = scene;
    this.camera = camera;
    this.hostElement = hostElement;
    this.support = support;
    this.physics = physicsEngine;
    this.loader = loader;

    this.root = new THREE.Group();
    this.root.name = 'TacticalVehicleAndCombatRoot';
    this.scene.add(this.root);

    // 贴身名牌 DOM 容器（挂载在画布宿主内，严密对齐 16:9 / 21:9 居中框）
    this.nameplateHost = document.createElement('div');
    this.nameplateHost.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:100;overflow:hidden;';
    (this.hostElement || document.body).appendChild(this.nameplateHost);

    // G01 三型主角载具
    this.heroUnits = [];
    this.selectedUnitId = 'US_M1A2_01';
    this.onSelectUnit = null;

    // 100 混编负载与弹道特效池
    this.combatMode = 'g01_vehicles'; // 'empty' | '10_tanks' | '100_mixed_combat' | '100_onscreen' | 'g01_vehicles'
    this.squadUnits = [];
    this.projectiles = [];
    this._buildBatchCombatMeshes();
  }

  _buildBatchCombatMeshes() {
    // 100 单位高辨识度战术装甲车体 + 炮塔合批 InstancedMesh + 弹道曳光弹 InstancedMesh
    const hullGeo = new THREE.BoxGeometry(3.6, 1.65, 7.8);
    hullGeo.translate(0, 0.85, 0);
    const hullMat = new THREE.MeshStandardMaterial({roughness: 0.72, metalness: 0.22});
    this.squadInstMesh = new THREE.InstancedMesh(hullGeo, hullMat, 120);
    this.squadInstMesh.castShadow = true;
    this.squadInstMesh.receiveShadow = true;
    this.squadInstMesh.frustumCulled = false;
    this.squadInstMesh.setColorAt(0, new THREE.Color(0x7b8863));
    if (this.squadInstMesh.instanceColor) this.squadInstMesh.instanceColor.needsUpdate = true;
    this.squadInstMesh.count = 0;

    const tracerGeo = new THREE.BoxGeometry(0.28, 0.28, 3.8);
    const tracerMat = new THREE.MeshBasicMaterial({color: 0xffb84d});
    this.tracerInstMesh = new THREE.InstancedMesh(tracerGeo, tracerMat, 64);
    this.tracerInstMesh.frustumCulled = false;
    this.tracerInstMesh.count = 0;

    this.root.add(this.squadInstMesh, this.tracerInstMesh);
  }

  async initHeroVehicles(loadRealGlb = true) {
    for (const u of this.heroUnits) {
      this.root.remove(u.node);
      u.labelEl?.remove();
    }
    this.heroUnits = [];

    const specs = [
      {
        id: 'US_M1A2_01',
        name: '铁砧 01 · M1A2 SEP v3',
        shortName: '铁砧 01',
        role: 'ground',
        iconKind: 'mbt',
        channel: 'ground',
        cat: 'armor',
        url: '/vehicles/US_M1A2.glb',
        pos: [-38.0, 0.0, 85.0],
        yaw: Math.PI * 0.5,
        speed: 14.0,
        color: 0x8d8266,
        hp: 100,
        bonus: 12,
        weapons: ['120mm 高爆 28', 'M1028 霰弹 14'],
        roe: 'free',
        groundGuard: true,
      },
      {
        id: 'US_AH64D_01',
        name: '游隼 01 · AH-64D 阿帕奇',
        shortName: '游隼 01',
        role: 'helicopter',
        iconKind: 'attack_heli',
        channel: 'heli',
        cat: 'heli',
        url: '/vehicles/US_AH64D.glb',
        pos: [-55.0, 22.0, 102.0],
        yaw: Math.PI * 0.5,
        speed: 26.0,
        color: 0x4b5742,
        hp: 100,
        bonus: 0,
        weapons: ['30mm 链炮 1200', 'AGM-114 ×8'],
        roe: 'free',
        groundGuard: true,
      },
      {
        id: 'US_A10C_01',
        name: '雷霆 01 · A-10C 疣猪',
        shortName: '雷霆 01',
        role: 'fixed_wing',
        iconKind: 'cas',
        channel: 'plane',
        cat: 'plane',
        url: '/vehicles/US_A10.glb',
        pos: [-110.0, 234.0, 40.0],
        yaw: Math.PI * 0.35,
        speed: 52.0,
        color: 0x6c767d,
        hp: 100,
        bonus: 0,
        weapons: ['GAU-8 30mm 1150', 'JDAM ×4'],
        roe: 'free',
        groundGuard: true,
      },
    ];

    for (const sp of specs) {
      let node = new THREE.Group();
      if (loadRealGlb && this.loader) {
        try {
          const gltf = await this.loader.loadAsync(sp.url);
          node.add(gltf.scene);
        } catch {
          node = this._makeFallbackVehicleMesh(sp.role, sp.color);
        }
      } else {
        node = this._makeFallbackVehicleMesh(sp.role, sp.color);
      }
      node.traverse(o => {
        if (o.isMesh) {
          o.castShadow = true;
          o.receiveShadow = true;
        }
      });

      const gy = this.support.heightAt(sp.pos[0], sp.pos[2]) || 0;
      const startY = sp.role === 'ground'
        ? gy
        : sp.role === 'helicopter'
        ? this.physics.queryHelicopterSafeAltitude(sp.pos[0], sp.pos[2], 1, 0).safeAltitudeY
        : 234.0;

      node.position.set(sp.pos[0], startY, sp.pos[2]);
      node.rotation.y = sp.yaw;
      this.root.add(node);

      const labelEl = document.createElement('div');
      labelEl.className = `ground-mark kind-${sp.iconKind}`;
      labelEl.dataset.unitId = sp.id;
      labelEl.style.pointerEvents = 'auto';
      const svgEl = createSilhouetteSvg(sp.iconKind, { size: 14 });
      const titleSpan = document.createElement('span');
      titleSpan.className = 'mark-name';
      titleSpan.textContent = sp.shortName;
      const metaSpan = document.createElement('span');
      metaSpan.className = 'mark-sub';
      metaSpan.textContent = `${startY.toFixed(0)}m`;
      labelEl.append(svgEl, titleSpan, metaSpan);
      labelEl.addEventListener('click', e => {
        e.stopPropagation();
        this.selectedUnitId = sp.id;
        this.onSelectUnit?.(sp.id);
      });
      this.nameplateHost.appendChild(labelEl);

      this.heroUnits.push({
        ...sp,
        node,
        labelEl,
        titleSpan,
        metaSpan,
        activeWep: 0,
        waypoints: [],
        wpIndex: 0,
        orbitPhase: 0,
        maxRecordedY: startY,
        minObstacleClearanceM: 999.0,
      });
    }
  }

  _makeFallbackVehicleMesh(role, colorHex) {
    const grp = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial({color: colorHex, roughness: 0.65, metalness: 0.25});
    if (role === 'ground') {
      const hull = new THREE.Mesh(new THREE.BoxGeometry(3.6, 1.3, 7.8), mat);
      hull.position.y = 0.7;
      const turret = new THREE.Mesh(new THREE.BoxGeometry(2.8, 0.9, 3.8), mat);
      turret.position.set(0, 1.65, -0.3);
      grp.add(hull, turret);
    } else if (role === 'helicopter') {
      const body = new THREE.Mesh(new THREE.BoxGeometry(2.2, 2.0, 11.5), mat);
      body.position.y = 1.2;
      const rotor = new THREE.Mesh(new THREE.BoxGeometry(12.5, 0.12, 0.6), mat);
      rotor.position.y = 2.5;
      grp.add(body, rotor);
    } else {
      const fuselage = new THREE.Mesh(new THREE.BoxGeometry(2.0, 1.8, 15.5), mat);
      const wing = new THREE.Mesh(new THREE.BoxGeometry(17.0, 0.25, 2.8), mat);
      grp.add(fuselage, wing);
    }
    return grp;
  }

  stopUnit(unitId) {
    const unit = this.heroUnits.find(u => u.id === unitId);
    if (!unit) return false;
    unit.waypoints = [];
    unit.wpIndex = 0;
    return true;
  }

  damageUnit(unitId, delta = 28) {
    const unit = this.heroUnits.find(u => u.id === unitId);
    if (!unit) return null;
    unit.hp = Math.max(10, Math.min(100, (unit.hp ?? 100) - delta));
    return unit.hp;
  }

  /**
   * 为 G01 载具下达移动命令：
   * - M1A2 自动执行 4m A* 寻路绕行；
   * - AH-64D 自动执行 30m 前瞻高度包络飞行；
   * - A-10C 自动转向目标空域高空巡航。
   */
  issueMoveCommand(unitId, targetX, targetZ) {
    const unit = this.heroUnits.find(u => u.id === unitId) || this.heroUnits[0];
    if (!unit) return null;

    const sx = unit.node.position.x;
    const sz = unit.node.position.z;

    if (unit.role === 'ground') {
      const nav = this.physics.findLocalPath4m(sx, sz, targetX, targetZ, 5.5);
      unit.waypoints = nav.waypoints.map(p => new THREE.Vector3(p[0], p[1], p[2]));
      unit.wpIndex = 0;
      return {
        unitId: unit.id,
        role: unit.role,
        found: nav.found,
        waypoints: nav.waypoints,
      };
    } else if (unit.role === 'helicopter') {
      const profile = this.physics.buildHelicopterFlightProfile(sx, sz, targetX, targetZ, 36);
      unit.waypoints = profile.map(p => new THREE.Vector3(p[0], p[1], p[2]));
      unit.wpIndex = 0;
      unit.maxRecordedY = unit.node.position.y;
      return {
        unitId: unit.id,
        role: unit.role,
        found: true,
        waypoints: profile,
        maxAltitudeY: Math.max(...profile.map(p => p[1])),
      };
    } else {
      unit.waypoints = [new THREE.Vector3(targetX, 234.0, targetZ)];
      unit.wpIndex = 0;
      return {
        unitId: unit.id,
        role: unit.role,
        found: true,
        waypoints: [[targetX, 234.0, targetZ]],
      };
    }
  }

  /**
   * 切换性能矩阵负载档位：
   * - 'empty': 0 单位
   * - '10_tanks': 10 辆坦克移动
   * - '100_mixed_combat': 100 混编单位连续移动与射击交战 (28m 战术锚点阵列)
   * - '100_onscreen': 100 单位紧凑同屏移动
   * - 'g01_vehicles': G01 三型载具代表模式
   */
  setLoadMode(mode, center = new THREE.Vector3(0, 0, 85)) {
    this.combatMode = mode;
    this.squadUnits = [];
    this.projectiles = [];

    const showHeroes = mode === 'g01_vehicles';
    for (const u of this.heroUnits) {
      u.node.visible = showHeroes;
      if (u.labelEl) u.labelEl.style.display = showHeroes ? 'block' : 'none';
    }

    if (mode === 'empty' || mode === 'g01_vehicles') {
      this.squadInstMesh.count = 0;
      this.squadInstMesh.visible = false;
      this.tracerInstMesh.count = 0;
      this.tracerInstMesh.visible = false;
      return {activeUnits: showHeroes ? this.heroUnits.length : 0};
    }

    const count = mode === '10_tanks' ? 10 : 100;
    const cols = mode === '10_tanks' ? 5 : 10;
    const spacing = mode === '100_onscreen' ? 9.5 : 22.0;

    this.squadInstMesh.visible = true;
    this.squadInstMesh.count = count;
    const color = new THREE.Color();

    for (let i = 0; i < count; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const isAir = mode === '100_mixed_combat' && i >= 76;
      const isHeli = isAir && i < 92;
      const team = i % 2; // 0: 蓝方, 1: 红方

      const wx = center.x + (col - (cols - 1) * 0.5) * spacing;
      const wz = center.z + (row - (Math.ceil(count / cols) - 1) * 0.5) * spacing;
      const gy = this.support.heightAt(wx, wz) || 0;
      const wy = isHeli ? gy + 24.0 : isAir ? 145.0 : gy;

      this.squadUnits.push({
        index: i,
        team,
        kind: isHeli ? 'heli' : isAir ? 'jet' : 'tank',
        baseX: wx,
        baseZ: wz,
        x: wx,
        y: wy,
        z: wz,
        yaw: team === 0 ? 0 : Math.PI,
        phase: (i * 0.47) % (Math.PI * 2),
        speed: isAir ? 28.0 : 10.5,
      });

      color.setHex(team === 0 ? (isAir ? 0x628b68 : 0x7b8863) : (isAir ? 0x8f6b58 : 0x8a7459));
      this.squadInstMesh.setColorAt(i, color);
    }
    if (this.squadInstMesh.instanceColor) this.squadInstMesh.instanceColor.needsUpdate = true;

    if (mode === '100_mixed_combat') {
      this.tracerInstMesh.visible = true;
      this.tracerInstMesh.count = 32;
      for (let k = 0; k < 32; k++) {
        this.projectiles.push({
          x: center.x + ((k % 8) - 3.5) * 20.0,
          y: (this.support.heightAt(center.x, center.z) || 0) + 2.2 + (k % 3) * 4.0,
          z: center.z - 60.0 + k * 3.5,
          vx: (k % 2 === 0 ? 1 : -1) * 65.0,
          vz: (k % 2 === 0 ? 1 : -1) * 45.0,
          progress: (k / 32.0),
        });
      }
    } else {
      this.tracerInstMesh.count = 0;
      this.tracerInstMesh.visible = false;
    }
    return {activeUnits: count};
  }

  update(dtSeconds, nowSeconds, currentSpan = 135) {
    const dt = Math.min(0.1, Math.max(0.001, dtSeconds));

    // 1. 更新 G01 三型主角载具移动、越障与贴身名牌
    if (this.combatMode === 'g01_vehicles') {
      const width = this.hostElement.clientWidth || 1280;
      const height = this.hostElement.clientHeight || 720;
      const projVec = new THREE.Vector3();

      for (const u of this.heroUnits) {
        if (u.waypoints.length > 0 && u.wpIndex < u.waypoints.length) {
          const target = u.waypoints[u.wpIndex];
          const dx = target.x - u.node.position.x;
          const dz = target.z - u.node.position.z;
          const distXZ = Math.hypot(dx, dz);
          const step = u.speed * dt;

          if (distXZ <= step + 0.35) {
            u.node.position.x = target.x;
            u.node.position.z = target.z;
            u.wpIndex++;
          } else {
            const ux = dx / distXZ;
            const uz = dz / distXZ;
            u.node.position.x += ux * step;
            u.node.position.z += uz * step;
            u.node.rotation.y = Math.atan2(ux, uz);
          }

          if (u.role === 'ground') {
            const gy = this.support.heightAt(u.node.position.x, u.node.position.z);
            if (Number.isFinite(gy)) u.node.position.y = gy;
          } else if (u.role === 'helicopter') {
            const safeQ = this.physics.queryHelicopterSafeAltitude(
              u.node.position.x,
              u.node.position.z,
              dx || Math.sin(u.node.rotation.y),
              dz || Math.cos(u.node.rotation.y)
            );
            const targetY = Math.max(target.y, safeQ.safeAltitudeY);
            u.node.position.y += (targetY - u.node.position.y) * Math.min(1.0, dt * 6.5);
            u.maxRecordedY = Math.max(u.maxRecordedY, u.node.position.y);
            if (safeQ.hasObstacleAhead) {
              u.minObstacleClearanceM = Math.min(u.minObstacleClearanceM, u.node.position.y - safeQ.highestObstacleTopY);
            }
          } else {
            u.node.position.y = 234.0;
          }
        } else if (u.role === 'fixed_wing') {
          // A-10C 连续盘旋巡航
          u.orbitPhase += dt * 0.35;
          const ox = -40.0 + Math.cos(u.orbitPhase) * 85.0;
          const oz = 85.0 + Math.sin(u.orbitPhase) * 55.0;
          const dx = ox - u.node.position.x;
          const dz = oz - u.node.position.z;
          u.node.position.set(ox, 234.0, oz);
          if (Math.hypot(dx, dz) > 1e-3) {
            u.node.rotation.y = Math.atan2(dx, dz);
          }
        }

        // 更新贴身名牌屏幕坐标（严格在非 embed 且视跨度 <= 750m 时展示去框通道名牌）
        if (u.labelEl) {
          const isEmbed = this.hostElement?.dataset?.embed === '1';
          if (isEmbed || currentSpan > 750) {
            u.labelEl.style.display = 'none';
          } else {
            u.labelEl.style.display = 'inline-flex';
            projVec.copy(u.node.position);
            projVec.y += u.role === 'ground' ? 3.8 : 5.2;
            projVec.project(this.camera);
            const sx = (projVec.x * 0.5 + 0.5) * width;
            const sy = (-projVec.y * 0.5 + 0.5) * height;
            u.labelEl.style.transform = `translate3d(${Math.round(sx)}px,${Math.round(sy)}px,0) translate(-50%,-100%)`;
            const isSel = u.id === this.selectedUnitId;
            u.labelEl.classList.toggle('is-selected', isSel);
            if (u.titleSpan) u.titleSpan.textContent = u.shortName || u.name;
            if (u.metaSpan) u.metaSpan.textContent = `· ${u.node.position.y.toFixed(0)}m`;
          }
        }
      }
    }

    // 2. 更新 10 / 100 混编单位连续机动与弹道
    if (this.squadUnits.length > 0) {
      const dummy = new THREE.Matrix4();
      const te = dummy.elements;
      for (let i = 0; i < this.squadUnits.length; i++) {
        const u = this.squadUnits[i];
        const angle = nowSeconds * 0.55 + u.phase;
        const radius = u.kind === 'tank' ? 16.0 : 32.0;
        const nx = u.baseX + Math.cos(angle) * radius;
        const nz = u.baseZ + Math.sin(angle) * radius;
        const dx = nx - u.x;
        const dz = nz - u.z;
        u.x = nx;
        u.z = nz;
        if (Math.hypot(dx, dz) > 1e-4) {
          u.yaw = Math.atan2(dx, dz);
        }
        const gy = this.support.heightAt(u.x, u.z) || 0;
        u.y = u.kind === 'heli' ? gy + 24.0 : u.kind === 'jet' ? gy + 85.0 : gy;

        const cy = Math.cos(u.yaw);
        const sy = Math.sin(u.yaw);
        te[0] = cy;  te[4] = 0; te[8] = sy;   te[12] = u.x;
        te[1] = 0;   te[5] = 1; te[9] = 0;    te[13] = u.y;
        te[2] = -sy; te[6] = 0; te[10] = cy;  te[14] = u.z;
        te[3] = 0;   te[7] = 0; te[11] = 0;   te[15] = 1;
        this.squadInstMesh.setMatrixAt(i, dummy);
      }
      this.squadInstMesh.instanceMatrix.needsUpdate = true;
    }

    if (this.projectiles.length > 0) {
      const dummy = new THREE.Matrix4();
      for (let i = 0; i < this.projectiles.length; i++) {
        const p = this.projectiles[i];
        p.progress = (p.progress + dt * 0.85) % 1.0;
        const px = p.x + Math.sin(nowSeconds * 2.0 + i) * 35.0;
        const pz = p.z + (p.progress - 0.5) * 90.0;
        dummy.makeTranslation(px, p.y, pz);
        this.tracerInstMesh.setMatrixAt(i, dummy);
      }
      this.tracerInstMesh.instanceMatrix.needsUpdate = true;
    }
  }
}
