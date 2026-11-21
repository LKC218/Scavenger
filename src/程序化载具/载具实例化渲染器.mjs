/**
 * 程序化载具统一组件级 InstancedMesh 渲染器：
 * - 彻底摒弃“每辆车独立 Object3D 树”的开销，按 (车型 × LOD × 可动槽位 × 材质) 维护共享 InstancedMesh 池；
 * - 开局常驻 LOD2 底座零等待，视锥近/中景按需异步流式升级 LOD1 → LOD0，绝不出现白模/真空；
 * - 阴影通道解耦：近景实时 ShadowMap 统一由低面 LOD1 投射，降低 5–8 倍阴影顶点开销；
 * - 距离驱动三档 LOD 平滑切换 + ±15% 滞回保护，防临界抖动。
 */
import * as THREE from 'three';

const _matHull = new THREE.Matrix4();
const _matPart = new THREE.Matrix4();
const _matLocal = new THREE.Matrix4();
const _matPivot = new THREE.Matrix4();
const _matRot = new THREE.Matrix4();
const _quat = new THREE.Quaternion();
const _euler = new THREE.Euler(0, 0, 0, 'YXZ');
const _pos = new THREE.Vector3();
const _scale = new THREE.Vector3(1, 1, 1);

export class ProceduralVehicleRenderer {
  constructor({scene, loader, shadowLayer = 0}) {
    this.scene = scene;
    this.loader = loader;
    this.shadowLayer = shadowLayer;

    this.root = new THREE.Group();
    this.root.name = 'ProceduralVehicleInstancedRoot';
    this.scene.add(this.root);

    // slug -> { capacity, meta, lodMeshes: {0: PartEntry[], 1: PartEntry[], 2: PartEntry[]}, shadowMeshes: PartEntry[], loading: Set<number> }
    this.typePools = new Map();
    this.units = [];
    this.unitLodState = new Map(); // unitId -> 0 | 1 | 2
    this.stats = {
      unitCount: 0,
      vehicleTypeCount: 0,
      lod0Count: 0,
      lod1Count: 0,
      lod2Count: 0,
      shadowCasterCount: 0,
      activeBatches: 0,
    };
  }

  async setUnits(units) {
    this.units = units;
    this.unitLodState.clear();
    const countsBySlug = new Map();
    for (const u of units) {
      countsBySlug.set(u.slug, (countsBySlug.get(u.slug) || 0) + 1);
    }

    // 清理不再出场的车型池
    for (const [slug, pool] of [...this.typePools.entries()]) {
      if (!countsBySlug.has(slug) || pool.capacity < countsBySlug.get(slug)) {
        this._disposeTypePool(pool);
        this.typePools.delete(slug);
      }
    }

    // 为每种出场车型确保 LOD2 远景底座就绪（开局 0 等待显示实心模型）
    const initTasks = [];
    for (const [slug, cnt] of countsBySlug.entries()) {
      if (!this.typePools.has(slug)) {
        const cap = Math.max(4, Math.ceil(cnt * 1.25));
        const pool = {
          slug,
          capacity: cap,
          meta: null,
          lodMeshes: {0: null, 1: null, 2: null},
          shadowMeshes: null,
          loading: new Set(),
        };
        this.typePools.set(slug, pool);
        initTasks.push(this._ensureLodLoaded(pool, 2));
      }
    }
    await Promise.all(initTasks);
  }

  async _ensureLodLoaded(pool, lodLevel) {
    if (pool.lodMeshes[lodLevel] || pool.loading.has(lodLevel)) return;
    pool.loading.add(lodLevel);
    try {
      if (!pool.meta) {
        pool.meta = await this.loader.loadVehicleMeta(pool.slug);
      }
      const parsed = await this.loader.loadVehicleLod(pool.slug, lodLevel);
      const entries = this._buildInstancedEntries(parsed, pool.capacity, lodLevel, false, pool.meta);
      pool.lodMeshes[lodLevel] = entries;

      // LOD1 同时建立轻量实时阴影投射池（只投射阴影不写入颜色缓冲，大幅降低近景阴影开销）
      if (lodLevel === 1 && !pool.shadowMeshes) {
        pool.shadowMeshes = this._buildInstancedEntries(parsed, pool.capacity, 1, true, pool.meta);
      }
    } catch (err) {
      console.warn(`[载具渲染器] 加载 ${pool.slug} LOD${lodLevel} 失败:`, err);
    } finally {
      pool.loading.delete(lodLevel);
    }
  }

  _buildInstancedEntries(parsed, capacity, lodLevel, isShadowOnly, meta = null) {
    const entries = [];
    const axisBySlot = new Map();
    if (meta?.sl) {
      for (const s of meta.sl) axisBySlot.set(s[0], s[2]);
    } else if (meta?.slots) {
      for (const s of meta.slots) axisBySlot.set(s.slot, s.axis);
    }
    parsed.scene.updateMatrixWorld(true);
    for (const slotNode of parsed.scene.children) {
      const slotName = slotNode.name || 'hull';
      const pivot = slotNode.position.clone();
      const axis = axisBySlot.get(slotName) || (slotName === 'tail_rotor' || slotName === 'barrel' ? 'x' : 'y');
      slotNode.traverse(obj => {
        if (!obj.isMesh) return;
        const mat = isShadowOnly ? obj.material : obj.material;
        const im = new THREE.InstancedMesh(obj.geometry, mat, capacity);
        im.frustumCulled = false;
        im.count = 0;
        im.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        if (isShadowOnly) {
          im.castShadow = true;
          im.receiveShadow = false;
          im.colorWrite = false;
          // 使用独立阴影层或双通道：若未分层，则在主通道隐藏、由专用阴影材质/标志控制；这里采用 LOD0 禁 castShadow + LOD1 投射
          if (this.shadowLayer > 0) {
            im.layers.set(this.shadowLayer);
          } else {
            // 同层情况下用不可见实体会跳过阴影，故用 colorWrite=false depthWrite=false 并在自定义 depthMaterial 投射，或直接启用 castShadow
            const shadowMat = new THREE.MeshBasicMaterial({colorWrite: false, depthWrite: false});
            im.material = shadowMat;
            im.userData.ownedMat = shadowMat;
          }
        } else {
          im.receiveShadow = lodLevel < 2;
          // LOD1 直接投射阴影，LOD0 改由 LOD1 阴影代理投射（省 5-8 倍阴影顶点数），LOD2 远景不投实时阴影
          im.castShadow = lodLevel === 1;
        }
        this.root.add(im);
        entries.push({slot: slotName, axis, pivot, mesh: im, count: 0});
      });
    }
    return entries;
  }

  _selectLodWithHysteresis(unitId, dist, span) {
    // 兼顾相机缩放 span 与单位到观察焦点的水平距离，±15% 滞回区间防止抖动
    const effective = Math.max(dist, span * 0.9);
    const prev = this.unitLodState.get(unitId);
    let next;
    if (prev === undefined) {
      next = effective < 165 ? 0 : effective < 480 ? 1 : 2;
    } else if (prev === 0) {
      next = effective > 195 ? (effective > 540 ? 2 : 1) : 0;
    } else if (prev === 1) {
      next = effective < 145 ? 0 : effective > 540 ? 2 : 1;
    } else {
      next = effective < 145 ? 0 : effective < 425 ? 1 : 2;
    }
    this.unitLodState.set(unitId, next);
    return next;
  }

  _writeUnitToEntries(entries, unit, hullMatrix) {
    let turretPivot = null;
    for (const e of entries) {
      if (e.slot === 'turret') {
        turretPivot = e.pivot;
        break;
      }
    }
    for (const ent of entries) {
      if (ent.count >= ent.mesh.instanceMatrix.count) continue;
      if (ent.slot === 'hull') {
        ent.mesh.setMatrixAt(ent.count, hullMatrix);
      } else if (ent.slot === 'barrel' && turretPivot) {
        // 炮管随炮塔绕 turretPivot 公转，再绕自身相对炮塔支点俯仰，确保炮盾绝不脱节
        _matPivot.makeTranslation(turretPivot.x, turretPivot.y, turretPivot.z);
        _matRot.makeRotationY(unit.turretYaw || 0);
        _matPart.copy(hullMatrix).multiply(_matPivot).multiply(_matRot);
        _matPivot.makeTranslation(
          ent.pivot.x - turretPivot.x,
          ent.pivot.y - turretPivot.y,
          ent.pivot.z - turretPivot.z,
        );
        _matLocal.makeRotationX(unit.barrelPitch || 0);
        _matPart.multiply(_matPivot).multiply(_matLocal);
        ent.mesh.setMatrixAt(ent.count, _matPart);
      } else {
        // 按槽位绕精确 pivot 施加局部旋转
        _matPivot.makeTranslation(ent.pivot.x, ent.pivot.y, ent.pivot.z);
        if (ent.slot === 'turret' || ent.slot === 'radar') {
          _matRot.makeRotationY(unit.turretYaw || 0);
        } else if (ent.slot === 'barrel') {
          _matRot.makeRotationX(unit.barrelPitch || 0);
        } else if (ent.slot === 'main_rotor') {
          _matRot.makeRotationY(unit.mainRotorAngle || 0);
        } else if (ent.slot === 'tail_rotor') {
          if (ent.axis === 'y') _matRot.makeRotationY(-(unit.mainRotorAngle || 0));
          else _matRot.makeRotationX(unit.tailRotorAngle || 0);
        } else if (ent.slot === 'gun') {
          _matRot.makeRotationY(unit.gunYaw || 0);
        } else {
          _matRot.identity();
        }
        _matPart.copy(hullMatrix).multiply(_matPivot).multiply(_matRot);
        ent.mesh.setMatrixAt(ent.count, _matPart);
      }
      ent.count += 1;
    }
  }

  update(span, focusPoint) {
    // 重置所有实例池计数
    for (const pool of this.typePools.values()) {
      for (const lv of [0, 1, 2]) {
        const list = pool.lodMeshes[lv];
        if (list) for (const e of list) e.count = 0;
      }
      if (pool.shadowMeshes) {
        for (const e of pool.shadowMeshes) e.count = 0;
      }
    }

    let c0 = 0, c1 = 0, c2 = 0, cShad = 0;
    const fx = focusPoint?.x ?? 0;
    const fz = focusPoint?.z ?? 0;

    for (const u of this.units) {
      const pool = this.typePools.get(u.slug);
      if (!pool) continue;

      const dist = Math.hypot(u.pos[0] - fx, u.pos[2] - fz);
      const desired = this._selectLodWithHysteresis(u.id, dist, span);

      // 按需触发异步流式升级（绝不阻塞当前帧！）
      if (desired <= 1 && !pool.lodMeshes[1]) this._ensureLodLoaded(pool, 1);
      if (desired === 0 && !pool.lodMeshes[0]) this._ensureLodLoaded(pool, 0);

      // 无缝降级回退：目标档未就绪时自动展示已有最精细档，零白模真空
      let actual = desired;
      while (actual < 2 && !pool.lodMeshes[actual]) actual += 1;
      const entries = pool.lodMeshes[actual];
      if (!entries) continue;

      _pos.set(u.pos[0], u.pos[1], u.pos[2]);
      _euler.set(u.pitch || 0, u.yaw || 0, u.roll || 0, 'YXZ');
      _quat.setFromEuler(_euler);
      _matHull.compose(_pos, _quat, _scale);

      this._writeUnitToEntries(entries, u, _matHull);
      if (actual === 0) {
        c0 += 1;
        // LOD0 使用轻量 LOD1 代理投射近景实时阴影
        if (pool.shadowMeshes) {
          this._writeUnitToEntries(pool.shadowMeshes, u, _matHull);
          cShad += 1;
        }
      } else if (actual === 1) {
        c1 += 1;
        cShad += 1;
      } else {
        c2 += 1;
      }
    }

    // 提交 GPU 实例矩阵缓冲
    let activeBatches = 0;
    for (const pool of this.typePools.values()) {
      for (const lv of [0, 1, 2]) {
        const list = pool.lodMeshes[lv];
        if (!list) continue;
        for (const e of list) {
          e.mesh.count = e.count;
          e.mesh.visible = e.count > 0;
          if (e.count > 0) {
            e.mesh.instanceMatrix.needsUpdate = true;
            activeBatches += 1;
          }
        }
      }
      if (pool.shadowMeshes) {
        for (const e of pool.shadowMeshes) {
          e.mesh.count = e.count;
          e.mesh.visible = e.count > 0;
          if (e.count > 0) {
            e.mesh.instanceMatrix.needsUpdate = true;
          }
        }
      }
    }

    this.stats.unitCount = this.units.length;
    this.stats.vehicleTypeCount = this.typePools.size;
    this.stats.lod0Count = c0;
    this.stats.lod1Count = c1;
    this.stats.lod2Count = c2;
    this.stats.shadowCasterCount = cShad;
    this.stats.activeBatches = activeBatches;
  }

  _disposeTypePool(pool) {
    for (const lv of [0, 1, 2]) {
      const list = pool.lodMeshes[lv];
      if (!list) continue;
      for (const e of list) {
        this.root.remove(e.mesh);
        e.mesh.dispose();
      }
    }
    if (pool.shadowMeshes) {
      for (const e of pool.shadowMeshes) {
        this.root.remove(e.mesh);
        e.mesh.userData.ownedMat?.dispose();
        e.mesh.dispose();
      }
    }
  }

  dispose() {
    for (const pool of this.typePools.values()) {
      this._disposeTypePool(pool);
    }
    this.typePools.clear();
    this.unitLodState.clear();
    this.scene.remove(this.root);
  }
}
