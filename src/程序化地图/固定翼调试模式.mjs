/**
 * Scavenger 固定翼单机调试模式 V2（复用 127.0.0.1:8800/play?mode=air-lab&unit=US_A10）：
 * - 严格隔离于 `mode=air-lab`，正常 `/play` 零 DOM 注入、`window.__SCAVENGER_AIR_LAB__ === undefined`；
 * - 独立可拖拽可折叠【AIR LAB 调试控制台】（`#airlab-debug-panel`），与右下角纯粹实战【攻击/机动】双页签命令盘解耦，
 *   支持刷靶/刷尸群/冒烟/坠毁/0.25x 慢放/快速近距入航/进场/盘旋/重置/4 档曳光一键切换与实时遥测；
 * - 7A 精确爆破范围衰减结算（innerRadius 满伤 → outerRadius 线性衰减至 edgeMultiplier）；
 * - 暴露冻结的 `window.__SCAVENGER_AIR_LAB__` 只读遥测对象，覆盖设计卡与 V2 E2E 全项可观测契约。
 */

import { ZOMBIE_TARGET_BASE_STATS } from './数值基底与倍率规范.mjs';

const HORDE_CAPACITY = 100;
const DEFAULT_RANGE_CENTER = Object.freeze({ x: -10, z: 36 });

export const FIXED_WING_FLIGHT_STATE_LABELS = Object.freeze({
  approach: '进场切入',
  sprint: '高速直飞',
  align: '姿态对准',
  orbit: '标准盘旋',
  strafe: '机炮扫射',
  rocket: '火箭齐射',
  bomb: '航弹轰炸',
  missile: '导弹发射',
  egress: '拉起脱离',
  rtb: '撤离返航',
  evacuated: '已撤离',
  damaged: '受击冒烟',
  crashing: '失控坠毁',
  crashed: '撞地损毁',
});

export class FixedWingAirLabController {
  constructor({
    THREE,
    scene,
    camera,
    renderer,
    terrainSupport,
    combatSystem,
    fixedWingBase,
    vfxEngine,
    tacticalLines3D = null,
    airAudio,
    spatialAudio,
    interaction,
    state,
    unitSlug = 'US_A10',
    onTelemetryChange = null,
    onUnitEvacuated = null,
    onBingoFuel = null,
    onWinchester = null,
  }) {
    this.THREE = THREE;
    this.scene = scene;
    this.camera = camera;
    this.renderer = renderer;
    this.terrainSupport = terrainSupport;
    this.combatSystem = combatSystem;
    this.fixedWingBase = fixedWingBase;
    this.vfxEngine = vfxEngine;
    this.tacticalLines3D = tacticalLines3D;
    this.airAudio = airAudio;
    this.spatialAudio = spatialAudio;
    this.interaction = interaction;
    this.state = state;
    this.unitSlug = unitSlug || 'US_A10';
    this.onTelemetryChange = onTelemetryChange;
    this.onUnitEvacuated = onUnitEvacuated;
    this.onBingoFuel = onBingoFuel;
    this.onWinchester = onWinchester;

    this.timeScale = 1.0;
    this.simNowSec = 0;
    this.rangeCenter = { x: DEFAULT_RANGE_CENTER.x, z: DEFAULT_RANGE_CENTER.z };

    // 靶区与尸群全部挂于 scene 顶层独立组（天然与右下 Live PiP 隔离）
    this.targetsRoot = new THREE.Group();
    this.targetsRoot.name = 'ScavengerAirLabTargetsRoot';
    this.scene.add(this.targetsRoot);

    this.staticTargets = [];
    this.hordeData = [];
    this.hordeMesh = null;
    this._hordeMat = new THREE.Matrix4();
    this._hordePos = new THREE.Vector3();
    this._hordeQuat = new THREE.Quaternion();
    this._hordeTumbleQuat = new THREE.Quaternion();
    this._hordeScale = new THREE.Vector3();
    this._hordeColor = new THREE.Color();
    this._hordeUp = new THREE.Vector3(0, 1, 0);
    this._hordeAxis = new THREE.Vector3(1, 0, 0);

    this._initHordeInstancedMesh();
    this._installGlobalTelemetry();

    // 默认在跑道主轴靶区预设 1 组静态靶，开局即可直接体验拖线扫射/投弹/点射导弹
    this.spawnStaticTargets();
  }

  _getPrimaryUnit() {
    const units = this.combatSystem?.heroUnits || [];
    const selectedId = this.combatSystem?.selectedUnitId;
    const sel = selectedId ? units.find(u => u.id === selectedId && u.role === 'fixed_wing' && !u.evacuated) : null;
    return (
      sel
      || units.find(u => u.slug === this.unitSlug && u.role === 'fixed_wing' && !u.evacuated)
      || units.find(u => u.role === 'fixed_wing' && !u.evacuated)
      || units.find(u => u.slug === this.unitSlug)
      || units.find(u => u.role === 'fixed_wing')
      || units[0]
      || null
    );
  }

  _initHordeInstancedMesh() {
    const { THREE } = this;
    const geo = new THREE.CapsuleGeometry(0.36, 1.05, 4, 6);
    geo.translate(0, 0.9, 0);
    const mat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.85,
      metalness: 0.05,
    });
    const mesh = new THREE.InstancedMesh(geo, mat, HORDE_CAPACITY);
    mesh.name = 'airlab_horde_1dc';
    mesh.count = 0;
    mesh.visible = false;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.hordeMesh = mesh;
    this.targetsRoot.add(mesh);
  }

  /**
   * 在开阔混凝土主跑道正中 (-10, 36) 一键刷新 6 座高对比装甲标靶。
   */
  spawnStaticTargets() {
    for (const t of this.staticTargets) {
      t.group.removeFromParent();
      t.dispose();
    }
    this.staticTargets = [];

    const { THREE } = this;
    const offsets = [
      { x: 0, z: 0, label: '跑道中线靶车 #1' },
      { x: -26, z: -2, label: '跑道西侧靶车 #2' },
      { x: 26, z: 2, label: '跑道东侧靶车 #3' },
      { x: -14, z: 10, label: '跑道南西标靶 #4' },
      { x: 14, z: -10, label: '跑道北东标靶 #5' },
      { x: -48, z: 4, label: '跑道西端标靶 #6' },
    ];

    for (let i = 0; i < offsets.length; i += 1) {
      const off = offsets[i];
      const wx = this.rangeCenter.x + off.x;
      const wz = this.rangeCenter.z + off.z;
      const gy = this.terrainSupport?.heightAt?.(wx, wz) ?? 2.0;

      const group = new THREE.Group();
      group.name = `airlab_static_target_${i + 1}`;
      group.position.set(wx, gy, wz);

      const hullGeo = new THREE.BoxGeometry(3.8, 1.5, 6.4);
      const hullMat = new THREE.MeshStandardMaterial({ color: 0xb04838, roughness: 0.65, metalness: 0.2 });
      const hull = new THREE.Mesh(hullGeo, hullMat);
      hull.position.y = 0.85;
      hull.castShadow = true;
      hull.receiveShadow = true;

      const turretGeo = new THREE.BoxGeometry(2.4, 0.9, 2.8);
      const turretMat = new THREE.MeshStandardMaterial({ color: 0xdfd3b4, roughness: 0.55, metalness: 0.25 });
      const turret = new THREE.Mesh(turretGeo, turretMat);
      turret.position.set(0, 1.95, 0.2);
      turret.castShadow = true;

      const ringGeo = new THREE.RingGeometry(4.4, 5.2, 24);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0xff6b57,
        transparent: true,
        opacity: 0.68,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.y = 0.12;

      group.add(hull, turret, ring);
      this.targetsRoot.add(group);

      this.staticTargets.push({
        id: `static_${i + 1}`,
        label: off.label,
        x: wx,
        y: gy,
        z: wz,
        hp: ZOMBIE_TARGET_BASE_STATS.staticTargetHp,
        maxHp: ZOMBIE_TARGET_BASE_STATS.staticTargetHp,
        alive: true,
        destroyedAtSec: -1,
        smokeTimer: 0,
        smokeDurationSec: 18.0,
        group,
        hull,
        turret,
        hullMat,
        turretMat,
        ringMat,
        dispose() {
          hullGeo.dispose();
          hullMat.dispose();
          turretGeo.dispose();
          turretMat.dispose();
          ringGeo.dispose();
          ringMat.dispose();
        },
      });
    }
    this._notifyChanged();
    return this.staticTargets.length;
  }

  /**
   * 跑道主轴一键刷新 100 只单 DrawCall 模拟尸群。
   */
  spawnSimulatedHorde(count = HORDE_CAPACITY) {
    const n = Math.max(20, Math.min(HORDE_CAPACITY, Math.round(count)));
    this.hordeData = [];
    for (let i = 0; i < n; i += 1) {
      const ang = i * 2.3999632;
      const r = 4.0 + Math.sqrt(i / n) * 24.0;
      const wx = this.rangeCenter.x + Math.cos(ang) * r * 1.45;
      const wz = this.rangeCenter.z + Math.sin(ang) * r * 0.45;
      const gy = this.terrainSupport?.heightAt?.(wx, wz) ?? 2.0;
      this.hordeData.push({
        index: i,
        x: wx,
        y: gy,
        z: wz,
        baseX: wx,
        baseY: gy,
        baseZ: wz,
        phase: (i * 0.73) % (Math.PI * 2),
        hp: ZOMBIE_TARGET_BASE_STATS.zombieHp,
        alive: true,
        dying: false,
        deathTimer: 0,
        deathDur: 0.45,
        vx: 0,
        vy: 0,
        vz: 0,
        tumbleSpeed: 0,
        tumbleAxisX: 1,
        tumbleAxisZ: 0,
      });
      const shade = 0.48 + (i % 5) * 0.08;
      this._hordeColor.setRGB(shade * 0.68, shade * 0.78, shade * 0.54);
      this.hordeMesh.setColorAt(i, this._hordeColor);
    }
    if (this.hordeMesh.instanceColor) this.hordeMesh.instanceColor.needsUpdate = true;
    this._syncHordeMatrices(0, 0);
    this._notifyChanged();
    return n;
  }

  _syncHordeMatrices(simNowSec, simDt = 0) {
    if (!this.hordeMesh) return;
    let instanceCount = 0;
    for (let i = 0; i < this.hordeData.length; i += 1) {
      const z = this.hordeData[i];
      if (z.alive) {
        const swayX = Math.sin(simNowSec * 1.4 + z.phase) * 0.9;
        const swayZ = Math.cos(simNowSec * 1.15 + z.phase) * 0.9;
        const wx = z.baseX + swayX;
        const wz = z.baseZ + swayZ;
        const gy = z.baseY;
        z.x = wx;
        z.y = gy;
        z.z = wz;
        this._hordePos.set(wx, gy, wz);
        this._hordeQuat.setFromAxisAngle(this._hordeUp, z.phase + simNowSec * 0.4);
        this._hordeScale.set(1, 1, 1);
        this._hordeMat.compose(this._hordePos, this._hordeQuat, this._hordeScale);
        this.hordeMesh.setMatrixAt(instanceCount, this._hordeMat);
        instanceCount += 1;
      } else if (z.dying) {
        z.deathTimer += simDt;
        if (z.deathTimer >= z.deathDur) {
          z.dying = false;
          continue;
        }
        const u = z.deathTimer / Math.max(0.01, z.deathDur);
        z.x += z.vx * simDt;
        z.z += z.vz * simDt;
        z.vy -= 14.5 * simDt;
        z.y = Math.max(z.baseY ?? 2.0, z.y + z.vy * simDt);
        const s = u < 0.45 ? 1.0 : Math.max(0.02, 1.0 - (u - 0.45) / 0.55);
        this._hordePos.set(z.x, z.y, z.z);
        this._hordeQuat.setFromAxisAngle(this._hordeUp, z.phase);
        this._hordeAxis.set(z.tumbleAxisX || 1, 0, z.tumbleAxisZ || 0);
        if (this._hordeAxis.lengthSq() > 1e-4) this._hordeAxis.normalize();
        else this._hordeAxis.set(1, 0, 0);
        this._hordeTumbleQuat.setFromAxisAngle(
          this._hordeAxis,
          Math.min(Math.PI * 0.52, z.deathTimer * (z.tumbleSpeed || 8.0)),
        );
        this._hordeQuat.premultiply(this._hordeTumbleQuat);
        this._hordeScale.set(s, s, s);
        this._hordeMat.compose(this._hordePos, this._hordeQuat, this._hordeScale);
        this.hordeMesh.setMatrixAt(instanceCount, this._hordeMat);
        instanceCount += 1;
      }
    }
    this.hordeMesh.count = instanceCount;
    this.hordeMesh.visible = instanceCount > 0;
    if (instanceCount > 0) this.hordeMesh.instanceMatrix.needsUpdate = true;
  }

  /**
   * Phase 2 精确 2m 高度场就绪后重新对齐静态靶、尸群与所有既有弹坑的高程。
   */
  realignAllToTerrain() {
    if (!this.terrainSupport) return;
    for (const t of this.staticTargets) {
      const gy = this.terrainSupport.heightAt?.(t.x, t.z);
      if (Number.isFinite(gy)) {
        t.y = gy;
        t.group.position.y = gy;
      }
    }
    for (const z of this.hordeData) {
      const gy = this.terrainSupport.heightAt?.(z.baseX, z.baseZ);
      if (Number.isFinite(gy)) {
        z.baseY = gy;
        if (z.alive) z.y = gy;
      }
    }
    this._syncHordeMatrices(this.simNowSec, 0);
    this.vfxEngine?.realignCratersToTerrain?.();
  }

  /**
   * 7A：结算爆炸对静态靶与模拟尸群的真实范围伤害（内圈满伤 → 外圈线性衰减至 edgeMultiplier）。
   */
  handleImpact({ pos, radius = 16, innerRadius = 4, edgeMultiplier = 0.25, damage = 60 }) {
    if (!pos) return;
    const outerR = Math.max(0.5, Number(radius) || 16);
    const innerR = Math.min(outerR, Math.max(0, Number(innerRadius) || 0));
    const edgeMul = Math.max(0.1, Math.min(1, Number(edgeMultiplier) || 0.25));

    const calcFalloff = (d) => {
      if (d <= innerR) return 1.0;
      const t = (d - innerR) / Math.max(0.01, outerR - innerR);
      return Math.max(edgeMul, 1.0 - t * (1.0 - edgeMul));
    };

    let changed = false;
    for (const t of this.staticTargets) {
      if (!t.alive) continue;
      const d = Math.hypot(t.x - pos.x, t.z - pos.z);
      if (d <= outerR) {
        t.hp = Math.max(0, t.hp - damage * calcFalloff(d));
        changed = true;
        if (t.hp <= 0) {
          t.alive = false;
          t.destroyedAtSec = this.simNowSec;
          t.smokeTimer = 0;
          t.hullMat.color.setHex(0x23201e);
          t.turretMat.color.setHex(0x2f2b28);
          t.turret.rotation.z = 0.28;
          t.turret.position.y = 1.65;
          t.ringMat.color.setHex(0x555555);
          t.ringMat.opacity = 0.25;
        } else {
          t.hullMat.color.setHex(0xd96b27);
        }
      }
    }
    let hordeHit = false;
    for (const z of this.hordeData) {
      if (!z.alive) continue;
      const dx = z.x - pos.x;
      const dz = z.z - pos.z;
      const d = Math.hypot(dx, dz);
      if (d <= outerR) {
        z.hp -= damage * calcFalloff(d);
        if (z.hp <= 0) {
          z.alive = false;
          z.dying = true;
          z.deathTimer = 0;
          z.deathDur = 0.45;
          const rx = d > 0.05 ? dx / d : Math.cos(z.phase);
          const rz = d > 0.05 ? dz / d : Math.sin(z.phase);
          const impulse = 6.5 + (z.index % 5) * 1.6;
          z.vx = rx * impulse;
          z.vz = rz * impulse;
          z.vy = 4.0 + (z.index % 4) * 0.7;
          z.tumbleSpeed = 7.5 + (z.index % 4) * 1.1;
          z.tumbleAxisX = -rz;
          z.tumbleAxisZ = rx;
          hordeHit = true;
          changed = true;
        }
      }
    }
    if (hordeHit) this._syncHordeMatrices(this.simNowSec, 0);
    if (changed) this._notifyChanged();
  }

  _pickActiveStrikePoint() {
    const aliveStatic = this.staticTargets.find(t => t.alive);
    if (aliveStatic) return { x: aliveStatic.x, y: aliveStatic.y, z: aliveStatic.z };
    const aliveHorde = this.hordeData.find(z => z.alive);
    if (aliveHorde) return { x: aliveHorde.x, y: aliveHorde.y, z: aliveHorde.z };
    const gy = this.terrainSupport?.heightAt?.(this.rangeCenter.x, this.rangeCenter.z) ?? 2.0;
    return { x: this.rangeCenter.x, y: gy, z: this.rangeCenter.z };
  }

  _notifyChanged() {
    this.onTelemetryChange?.();
  }

  setTracerColorMode(mode = 'red') {
    const valid = ['red', 'green', 'mixed', 'none', 'auto'].includes(mode) ? mode : 'red';
    this.vfxEngine?.setTracerColorMode?.(valid);
    this.fixedWingBase?.setTracerColorMode?.(valid);
    this._notifyChanged();
    return valid;
  }

  getTracerColorMode() {
    return this.vfxEngine?.getTracerColorMode?.() || this.fixedWingBase?.getTracerColorMode?.() || 'red';
  }

  /**
   * 统一触发全部调试操作（收编于右下角命令盘「调试页」及 E2E 驱动）。
   */
  executeAction(action, extraOpts = {}) {
    const unit = this._getPrimaryUnit();
    if (this.spatialAudio?.enabled && !this.spatialAudio?.runtime?.unlocked) {
      this.spatialAudio.unlockByGesture?.();
    }
    this.airAudio?.preloadA10CoreAudio?.();

    if (action === 'tracer-red') {
      return { ok: true, tracerColorMode: this.setTracerColorMode('red') };
    }
    if (action === 'tracer-green') {
      return { ok: true, tracerColorMode: this.setTracerColorMode('green') };
    }
    if (action === 'tracer-mixed') {
      return { ok: true, tracerColorMode: this.setTracerColorMode('mixed') };
    }
    if (action === 'tracer-none') {
      return { ok: true, tracerColorMode: this.setTracerColorMode('none') };
    }
    if (action === 'set-tracer-mode') {
      return { ok: true, tracerColorMode: this.setTracerColorMode(extraOpts.mode || 'red') };
    }

    if (action === 'fuel-full') {
      const res = this.fixedWingBase.setFuelRatio(unit, 1.0);
      this._notifyChanged();
      return res;
    }
    if (action === 'fuel-low') {
      const th = this.fixedWingBase.getUnitTelemetry(unit)?.bingoThresholdPct ?? 14;
      const res = this.fixedWingBase.setFuelRatio(unit, Math.min(0.95, (th + 5) / 100));
      this._notifyChanged();
      return res;
    }
    if (action === 'fuel-bingo') {
      const th = this.fixedWingBase.getUnitTelemetry(unit)?.bingoThresholdPct ?? 14;
      const res = this.fixedWingBase.setFuelRatio(unit, Math.max(0.05, (th - 1) / 100));
      this._notifyChanged();
      return res;
    }
    if (action === 'fuel-freeze') {
      const next = !this.fixedWingBase.isFuelConsumptionPaused();
      this.fixedWingBase.setFuelConsumptionPaused(next);
      this._notifyChanged();
      return { ok: true, fuelConsumptionPaused: next };
    }
    if (action === 'toggle-auto-rtb-winchester') {
      const next = !this.fixedWingBase.isAutoRtbOnWinchester();
      this.fixedWingBase.setAutoRtbOnWinchester(next);
      this._notifyChanged();
      return { ok: true, autoRtbOnWinchester: next };
    }
    if (action === 'deplete-ammo') {
      const res = this.fixedWingBase.depleteGroundAmmo(unit, {
        simNowSec: this.simNowSec,
        audioBridge: this.airAudio,
        onWinchester: (wUnit, st) => this.onWinchester?.(wUnit, st),
      });
      this._notifyChanged();
      return res;
    }

    if (action === 'slowmo') {
      this.timeScale = this.timeScale < 0.5 ? 1.0 : 0.25;
      this._notifyChanged();
      return { ok: true, timeScale: this.timeScale };
    }
    if (action === 'fast-approach') {
      if (this.fixedWingBase) {
        this.fixedWingBase.fastApproachMode = !this.fixedWingBase.fastApproachMode;
      }
      this._notifyChanged();
      return { ok: true, fastApproachMode: Boolean(this.fixedWingBase?.fastApproachMode) };
    }
    if (action === 'spawn-targets') {
      const count = this.spawnStaticTargets();
      return { ok: true, staticTargetCount: count };
    }
    if (action === 'spawn-horde') {
      const count = this.spawnSimulatedHorde(HORDE_CAPACITY);
      return { ok: true, hordeTotalCount: count };
    }
    if (action === 'focus-plane') {
      this.focusCamera('plane');
      return { ok: true };
    }
    if (action === 'focus-range') {
      this.focusCamera('range');
      return { ok: true };
    }
    if (action === 'reset') {
      this.timeScale = 1.0;
      this.vfxEngine?.clearAll?.();
      this.spawnStaticTargets();
      if (unit) {
        this.fixedWingBase?.triggerCommand(unit, 'reset', this.rangeCenter, {
          simNowSec: this.simNowSec,
          terrainSupport: this.terrainSupport,
          audioBridge: this.airAudio,
          vfxEngine: this.vfxEngine,
        });
        unit.evacuated = false;
        this.combatSystem?.setSelection?.([unit.id], unit.id);
        this.interaction?.setSelection?.([unit.id], { silent: true });
      }
      this._notifyChanged();
      return { ok: true, flightState: 'orbit', timeScale: 1.0 };
    }

    if (!unit || !this.fixedWingBase) return { ok: false, reason: '未找到目标固定翼单位' };

    if (action === 'toggle-altitude') {
      const res = this.fixedWingBase.toggleAltitudeMode(unit);
      this._notifyChanged();
      return res;
    }
    if (action === 'toggle-autofire') {
      return {
        ok: false,
        orbitAutoFire: false,
        manualOnly: true,
        reason: '固定翼已取消自动开火模式，仅支持手操精准打击',
      };
    }
    if (action === 'approach') {
      const res = this.fixedWingBase.triggerCommand(unit, 'approach', extraOpts.targetPos || this.rangeCenter, {
        simNowSec: this.simNowSec,
        terrainSupport: this.terrainSupport,
        audioBridge: this.airAudio,
        vfxEngine: this.vfxEngine,
      });
      if (res?.ok) {
        unit.evacuated = false;
        this.combatSystem?.setSelection?.([unit.id], unit.id);
        this.interaction?.setSelection?.([unit.id], { silent: true });
      }
      this._notifyChanged();
      return res;
    }
    if (action === 'rtb' || action === 'evacuate') {
      const cur = this.fixedWingBase.getUnitTelemetry(unit);
      if (cur?.flightState === 'rtb' && !extraOpts.force) {
        const res = this.fixedWingBase.cancelEvacuate(unit, { audioBridge: this.airAudio });
        this._notifyChanged();
        return res;
      }
      if (cur?.flightState === 'evacuated') {
        const res = this.fixedWingBase.triggerCommand(unit, 'approach', this.rangeCenter, {
          simNowSec: this.simNowSec,
          terrainSupport: this.terrainSupport,
          audioBridge: this.airAudio,
          vfxEngine: this.vfxEngine,
        });
        if (res?.ok) {
          unit.evacuated = false;
          this.combatSystem?.setSelection?.([unit.id], unit.id);
          this.interaction?.setSelection?.([unit.id], { silent: true });
        }
        this._notifyChanged();
        return res;
      }
      if (this.interaction?.getState?.()?.mode === 'targeting' || this.interaction?.getState?.()?.wheelOpen) {
        this.interaction?.exitToPlay?.();
      }
      if (this.interaction?.cameraCtrl?.getFollowUnitId?.() === unit.id) {
        this.interaction.cameraCtrl.stopFollowing({ notify: true });
      }
      const res = this.fixedWingBase.issueEvacuate(unit, {
        simNowSec: this.simNowSec,
        audioBridge: this.airAudio,
        vfxEngine: this.vfxEngine,
      });
      this._notifyChanged();
      return res;
    }

    const targetPt = extraOpts.targetPos || this._pickActiveStrikePoint();
    const res = this.fixedWingBase.triggerCommand(unit, action, targetPt, {
      simNowSec: this.simNowSec,
      terrainSupport: this.terrainSupport,
      audioBridge: this.airAudio,
      vfxEngine: this.vfxEngine,
      startPos: extraOpts.startPos || null,
      endPos: extraOpts.endPos || null,
      queue: Boolean(extraOpts.queue),
      fastApproach: extraOpts.fastApproach ?? null,
    });
    this._notifyChanged();
    return res;
  }

  focusCamera(mode = 'plane') {
    const unit = this._getPrimaryUnit();
    const camCtrl = this.interaction?.cameraCtrl;
    if (mode === 'plane' && unit?.node?.position) {
      const getProjectedFocus = (angleRad) => {
        const p = unit.node?.position;
        if (!p) return null;
        if (typeof camCtrl?.projectAnchorToGround === 'function') {
          return camCtrl.projectAnchorToGround({ x: p.x, y: p.y, z: p.z, role: unit.role || 'fixed_wing' }, angleRad);
        }
        return { x: p.x, z: p.z };
      };
      const initProj = getProjectedFocus(camCtrl?.camTarget?.angle ?? 0);
      if (initProj) {
        camCtrl?.flyTo?.(initProj.x, initProj.z, 220, undefined, { focusFn: getProjectedFocus });
      }
    } else {
      camCtrl?.flyTo?.(this.rangeCenter.x, this.rangeCenter.z, 260);
    }
  }

  getSummaryBadgeData() {
    const unit = this._getPrimaryUnit();
    const summary = unit ? this.fixedWingBase?.getUnitTelemetry(unit) : null;
    const vfx = this.vfxEngine?.getStats?.() || { poolDrawCalls: 0, maxPoolCount: 6 };
    const staticAlive = this.staticTargets.filter(t => t.alive).length;
    const hordeAlive = this.hordeData.filter(z => z.alive).length;
    const flightState = summary?.flightState || 'orbit';
    return {
      flightState,
      flightStateLabel: FIXED_WING_FLIGHT_STATE_LABELS[flightState] || flightState,
      timeScale: this.timeScale,
      fastApproachMode: Boolean(this.fixedWingBase?.fastApproachMode),
      vfxDrawCallsText: `${vfx.poolDrawCalls}/${vfx.maxPoolCount}`,
      targetsText: hordeAlive > 0 ? `${staticAlive}靶+${hordeAlive}尸` : `${staticAlive}靶`,
      damagedSmokeActive: Boolean(summary?.damagedSmokeActive),
      tracerColorMode: this.getTracerColorMode(),
      fuelPaused: Boolean(this.fixedWingBase?.isFuelConsumptionPaused?.()),
      bingoState: summary?.bingoState || 'nominal',
      fuelPct: Number(summary?.fuelPct ?? 100),
      autoRtbOnWinchester: Boolean(this.fixedWingBase?.isAutoRtbOnWinchester?.()),
      winchesterState: summary?.winchesterState || 'nominal',
    };
  }

  step(realDtSec) {
    const simDt = Math.min(0.1, Math.max(0, Number(realDtSec) || 0)) * this.timeScale;
    this.simNowSec += simDt;

    this.vfxEngine?.setFrameCamera?.(this.camera, this.state?.span ?? 260);

    const units = this.combatSystem?.heroUnits || [];
    for (const u of units) {
      if (u.role === 'fixed_wing' && u.fixedWingController === this.fixedWingBase) {
        this.fixedWingBase.step(u, simDt, this.simNowSec, {
          terrainSupport: this.terrainSupport,
          vfxEngine: this.vfxEngine,
          audioBridge: this.airAudio,
          onTargetImpact: (impact) => this.handleImpact(impact),
          onUnitEvacuated: (evacUnit, st) => this.onUnitEvacuated?.(evacUnit, st),
          onBingoFuel: (bingoUnit, st) => this.onBingoFuel?.(bingoUnit, st),
          onWinchester: (winUnit, st) => this.onWinchester?.(winUnit, st),
        });
      }
    }

    if (simDt > 0) {
      for (let i = 0; i < this.staticTargets.length; i += 1) {
        const t = this.staticTargets[i];
        if (!t.alive && t.destroyedAtSec >= 0 && (this.simNowSec - t.destroyedAtSec) <= (t.smokeDurationSec || 18.0)) {
          t.smokeTimer = (t.smokeTimer || 0) + simDt;
          if (t.smokeTimer >= 0.45) {
            t.smokeTimer = 0;
            this.vfxEngine?.spawnDamageSmokePuff?.(
              { x: t.x, y: t.y + 1.45, z: t.z },
              false,
              { scale: 0.62, flameIntensity: 0.25, simNowSec: this.simNowSec },
            );
          }
        }
      }
    }

    this.vfxEngine?.update?.(simDt, this.simNowSec);
    if (this.hordeData.length > 0 && simDt > 0) {
      this._syncHordeMatrices(this.simNowSec, simDt);
    }

    const shake = this.airAudio?.step?.(
      this.camera?.position,
      this.state?.span ?? 260,
      realDtSec,
      { camera: this.camera, groundCenter: this.state?.center || null },
    ) || { x: 0, y: 0, z: 0, magnitude: 0 };
    return { simDt, simNowSec: this.simNowSec, shake };
  }

  refreshTelemetryUI() {
    this._notifyChanged();
  }

  _measureRenderAudit() {
    const lights = [];
    let pointLightCount = 0;
    let spotLightCount = 0;
    let vfxRootLightCount = 0;
    this.scene.traverse(obj => {
      if (obj.isLight) {
        lights.push(obj.type);
        if (obj.isPointLight) pointLightCount += 1;
        if (obj.isSpotLight) spotLightCount += 1;
      }
    });
    if (this.vfxEngine?.vfxRoot) {
      this.vfxEngine.vfxRoot.traverse(obj => {
        if (obj.isLight) vfxRootLightCount += 1;
      });
    }

    const measureVfxDrawCallDelta = () => {
      if (!this.renderer || !this.vfxEngine?.vfxRoot) return 0;
      const prevVis = this.vfxEngine.vfxRoot.visible;
      const prevRt = this.renderer.getRenderTarget();
      try {
        this.vfxEngine.setSceneDepth?.(null);
        this.vfxEngine.vfxRoot.visible = false;
        this.renderer.render(this.scene, this.camera);
        const callsOff = this.renderer.info.render.calls;
        this.vfxEngine.vfxRoot.visible = true;
        this.renderer.render(this.scene, this.camera);
        const callsOn = this.renderer.info.render.calls;
        return Math.max(0, callsOn - callsOff);
      } finally {
        this.vfxEngine.vfxRoot.visible = prevVis;
        this.renderer.setRenderTarget(prevRt);
      }
    };

    const vfxStats = this.vfxEngine?.getStats?.() || {};
    const airShadowStats = this.combatSystem?.getAirShadowStats?.() || { activeCount: 0, drawCalls: 0 };
    const opaqueDc = Number(this.state?.lastOpaqueDrawCalls) || 0;
    const overlayDc = Number(this.state?.lastVfxOverlayDrawCalls) || 0;
    const rawDc = this.renderer?.info?.render?.calls ?? 0;
    return {
      mainSceneDrawCalls: opaqueDc > 0 ? (opaqueDc + overlayDc) : rawDc,
      opaqueSceneDrawCalls: opaqueDc,
      vfxOverlayDrawCalls: overlayDc,
      mainSceneTriangles: this.renderer?.info?.render?.triangles ?? 0,
      lights,
      pointLightCount,
      spotLightCount,
      vfxRootLightCount,
      vfxVersion: vfxStats.vfxVersion || '3.0',
      vfxPoolDrawCalls: vfxStats.poolDrawCalls ?? 0,
      airShadowDrawCalls: airShadowStats.drawCalls ?? 0,
      airShadowActiveCount: airShadowStats.activeCount ?? 0,
      vfxAtlasReady: Boolean(vfxStats.atlasReady),
      lastFrameUsedDepth: Boolean(vfxStats.lastFrameUsedDepth),
      rendererToneMapping: this.renderer?.toneMapping,
      rendererExposure: this.renderer?.toneMappingExposure,
      rendererPixelRatio: this.renderer?.getPixelRatio?.(),
      measureVfxDrawCallDelta,
    };
  }

  _installGlobalTelemetry() {
    const api = Object.freeze({
      getConfig: () => ({
        mode: 'air-lab',
        unitSlug: this.unitSlug,
        timeScale: this.timeScale,
        fastApproachMode: Boolean(this.fixedWingBase?.fastApproachMode),
        simNowSec: Number(this.simNowSec.toFixed(3)),
        isCoreReady: Boolean(this.state?.coreReady),
        isFullPrecisionReady: Boolean(this.state?.fullPrecisionReady),
      }),
      getUnitSummary: () => {
        const unit = this._getPrimaryUnit();
        const base = unit ? this.fixedWingBase?.getUnitTelemetry(unit) : null;
        return base ? { ...base, heroCount: this.combatSystem?.heroUnits?.length ?? 0 } : null;
      },
      getWeaponsState: () => {
        const unit = this._getPrimaryUnit();
        return unit ? (this.fixedWingBase?.getWeaponsTelemetry(unit) || []) : [];
      },
      getTargetsState: () => ({
        staticTargetCount: this.staticTargets.length,
        staticAliveCount: this.staticTargets.filter(t => t.alive).length,
        hordeTotalCount: this.hordeData.length,
        hordeAliveCount: this.hordeData.filter(z => z.alive).length,
        hordeDrawCalls: (this.hordeMesh?.visible && this.hordeMesh.count > 0) ? 1 : 0,
        targets: this.staticTargets.map(t => ({
          id: t.id,
          label: t.label,
          hp: Number(t.hp.toFixed(1)),
          maxHp: t.maxHp,
          alive: t.alive,
          position: [t.x, Number(t.y.toFixed(2)), t.z],
        })),
      }),
      getVfxStats: () => {
        const stats = this.vfxEngine?.getStats?.() || {};
        const unit = this._getPrimaryUnit();
        const uTel = unit ? this.fixedWingBase?.getUnitTelemetry(unit) : null;
        return {
          ...stats,
          flareBurstCount: uTel?.flareBurstCount ?? 0,
        };
      },
      getTacticalLines3DStats: () => this.tacticalLines3D?.getStats?.() || {
        activeQuads: 0,
        activeVertices: 0,
        drawCalls: 0,
        visible: false,
      },
      getRenderAudit: () => this._measureRenderAudit(),
      getAudioEventsTelemetry: () => this.airAudio?.getTelemetry?.() || {
        unlocked: false,
        loadedKeysCount: 0,
        decodedKeysCount: 0,
        loadedAssets: [],
        pendingDelayedCount: 0,
        recentEvents: [],
        microShakeMagnitude: 0,
        sonicBoomPlayedCount: 0,
        busInitialized: false,
        convolverCount: 0,
        bucketStats: { near: 0, mid: 0, far: 0 },
        dedupDroppedCount: 0,
        subPunchCount: 0,
        nodeAllocationStats: { transientEvents: 0, lastEventNodes: { bufferSources: 0, gainNodes: 0, biquadFilters: 0, convolvers: 0 } },
        activeGau8Count: 0,
        activeGau8Details: [],
        lastGau8Snapshot: null,
        retiringVoicesCount: 0,
        peak: 0,
        maxObservedPeak: 0,
      },
      getSpatialAudioState: () => this.spatialAudio?.getDebugState?.() || null,
      setTracerColorMode: (mode) => this.setTracerColorMode(mode),
      getTracerColorMode: () => this.getTracerColorMode(),
      executeAction: (action, opts) => this.executeAction(action, opts),
    });

    if (typeof window !== 'undefined') {
      window.__SCAVENGER_AIR_LAB__ = api;
      window.__SCV_AIRLAB__ = api;
    }
  }

  dispose() {
    for (const t of this.staticTargets) t.dispose();
    this.staticTargets = [];
    this.hordeMesh?.geometry?.dispose?.();
    this.hordeMesh?.material?.dispose?.();
    this.targetsRoot.removeFromParent();
    if (typeof window !== 'undefined') {
      if (window.__SCAVENGER_AIR_LAB__) delete window.__SCAVENGER_AIR_LAB__;
      if (window.__SCV_AIRLAB__) delete window.__SCV_AIRLAB__;
    }
  }
}
