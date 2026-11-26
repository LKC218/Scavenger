/**
 * Scavenger 固定翼通用底座与深度打磨 M1–M3 纯规则单元测试（Node ESM，无 Three.js/DOM 硬依赖）。
 */

import assert from 'node:assert/strict';
import {
  FixedWingBaseController,
  computeL1OrbitCommand,
  computeEnergyManeuverDeltaMps,
  sampleAheadMaxTerrainY,
} from '../../src/程序化地图/固定翼底座.mjs';
import { computeEngineMotionState } from '../../src/程序化地图/载具空间音效.mjs';
import { formatDragLineAmmoPreview } from '../../src/交互系统/指令与标绘层.mjs';

function makeMockPlaneUnit(slug = 'US_A10', id = 'fw_test_1') {
  return {
    id,
    slug,
    name: slug === 'US_A10' ? 'A-10C Thunderbolt II' : slug,
    shortName: slug === 'US_A10' ? 'A-10C' : slug,
    role: 'fixed_wing',
    alive: true,
    node: {
      visible: true,
      position: {
        x: 150,
        y: 215,
        z: 36,
        set(nx, ny, nz) {
          this.x = nx;
          this.y = ny;
          this.z = nz;
        },
      },
      rotation: { x: 0, y: 0, z: 0 },
      updateMatrixWorld() {},
    },
  };
}

function runTests() {
  const flatTerrain = {
    baseY: 2.0,
    heightAt() {
      return this.baseY;
    },
  };

  // 1. M1.1 computeEngineMotionState 油门与加力工况联动
  {
    const orbitMot = computeEngineMotionState({
      role: 'fixed_wing',
      flightAttitude: { customControlled: true, flightState: 'orbit', throttleLevel: 0.55, pitchDeg: 2.8, afterburnerActive: false },
    });
    assert.equal(orbitMot.name, '巡航盘旋');
    assert.equal(orbitMot.gain, 0.36);
    assert.equal(orbitMot.rate, 1.0);
    assert.equal(orbitMot.filterHz, 3200);
    assert.equal(orbitMot.throttleLevel, 0.55);
    assert.equal(orbitMot.afterburnerActive, false);

    const transitMot = computeEngineMotionState({
      role: 'fixed_wing',
      flightAttitude: { customControlled: true, flightState: 'strafe', throttleLevel: 0.70, pitchDeg: -4, afterburnerActive: false },
    });
    assert.equal(transitMot.name, '入航切入');
    assert.equal(transitMot.gain, 0.44);
    assert.equal(transitMot.rate, 1.05);
    assert.equal(transitMot.filterHz, 3800);

    const strafeMot = computeEngineMotionState({
      role: 'fixed_wing',
      flightAttitude: { customControlled: true, flightState: 'strafe', throttleLevel: 0.85, pitchDeg: -15, afterburnerActive: false },
    });
    assert.equal(strafeMot.name, '俯冲突击');
    assert.ok(strafeMot.gain >= 0.52 && strafeMot.gain <= 0.56);
    assert.ok(strafeMot.rate >= 1.09 && strafeMot.rate <= 1.13);
    assert.ok(strafeMot.filterHz >= 4500);

    const egressMot = computeEngineMotionState({
      role: 'fixed_wing',
      flightAttitude: { customControlled: true, flightState: 'egress', throttleLevel: 1.0, pitchDeg: 22, afterburnerActive: false },
    });
    assert.equal(egressMot.name, '满推拉升');
    assert.equal(egressMot.gain, 0.56);
    assert.equal(egressMot.rate, 1.14);
    assert.equal(egressMot.filterHz, 5000);

    const abMot = computeEngineMotionState({
      role: 'fixed_wing',
      flightAttitude: { customControlled: true, flightState: 'rtb', throttleLevel: 1.0, pitchDeg: 10, afterburnerActive: true },
    });
    assert.equal(abMot.name, '加力全推');
    assert.equal(abMot.gain, 0.64);
    assert.equal(abMot.rate, 1.18);
    assert.equal(abMot.filterHz, 5800);
    assert.equal(abMot.afterburnerActive, true);
  }

  // 2. M2.1 L1 连续盘旋收敛与协调坡度（圆外 / 圆内无硬吸附平滑归圈）
  {
    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
    const unit = makeMockPlaneUnit('US_A10', 'fw_l1');
    const st = ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });

    // 稳态圆周采样
    const steadyCmd = computeL1OrbitCommand({
      posX: 150,
      posZ: 0,
      yaw: 0,
      speedMps: 97.5,
      orbitCenter: { x: 0, z: 0 },
      orbitRadius: 150,
    });
    assert.ok(steadyCmd.bankDegCmd >= -42 && steadyCmd.bankDegCmd <= -18, `稳态坡度越界: ${steadyCmd.bankDegCmd}`);
    assert.ok(Math.abs(steadyCmd.bankDegCmd) < 40, '稳态盘旋不得误触发涡流阈值 40°');

    // 从偏离圆周 r+140m 平滑收敛至 150m±4m
    unit.node.position.set(290, 215, 0);
    unit.node.rotation.y = 0.25;
    let simT = 0;
    for (let i = 0; i < 600; i += 1) {
      simT += 1 / 60;
      ctrl.step(unit, 1 / 60, simT, { terrainSupport: flatTerrain });
    }
    const finalDist = Math.hypot(unit.node.position.x, unit.node.position.z);
    assert.ok(Math.abs(finalDist - 150) <= 5.0, `L1 盘旋收敛半径偏差过大: ${finalDist}`);
    assert.ok(st.rollDeg >= -42 && st.rollDeg <= -18, `L1 盘旋实测滚转角越界: ${st.rollDeg}`);
    assert.ok(Math.abs(unit.node.position.y - 215) <= 3.0, `L1 盘旋高程偏差: ${unit.node.position.y}`);
  }

  // 3. M2.1 轻量能量机动（俯冲加速 vs 拉起掉速）
  {
    const diveDv = computeEnergyManeuverDeltaMps(-18, 1.0);
    const climbDv = computeEnergyManeuverDeltaMps(22, 1.0);
    assert.ok(diveDv > 1.0, `俯冲应产生正加速度: ${diveDv}`);
    assert.ok(climbDv < -1.0, `爬升应产生负加速度: ${climbDv}`);
  }

  // 4. M1.3 Hydra 70 三挂点循环发射 & 打空 21 发保留 3 具空巢
  {
    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
    const unit = makeMockPlaneUnit('US_A10', 'fw_hydra');
    const st = ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });
    const hydra = st.weapons.find(w => w.id === 'hydra70_rocket');
    assert.equal(hydra.keepPylonWhenEmpty, true);
    assert.deepEqual(hydra.pylonIndices, [10, 11, 12]);

    const cycledPylons = [];
    for (let i = 0; i < 21; i += 1) {
      hydra.ammo -= 1;
      const p = ctrl._consumePylon(st, hydra);
      cycledPylons.push(p?.index);
    }
    assert.equal(hydra.ammo, 0);
    assert.deepEqual(cycledPylons.slice(0, 6), [10, 11, 12, 10, 11, 12]);
    const telAfter = ctrl.getWeaponsTelemetry(unit).find(w => w.id === 'hydra70_rocket');
    assert.equal(telAfter.visiblePylonCount, 3, 'Hydra 打空后 3 具 LAU-131 空巢应保留可见');
  }

  // 5. M2.2 2+2+2 双发脉冲热诱弹 & 坠毁渐进加速滚转 + 弹射弧 + 撞地
  {
    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
    const unit = makeMockPlaneUnit('US_A10', 'fw_flare_crash');
    const st = ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });

    ctrl._enterEgress(st, 10.0, null);
    assert.equal(st.flareBurstCount, 1);
    assert.equal(st.flareQueue.length, 6);
    assert.ok(Math.abs(st.flareQueue[0].fireAt - 10.22) < 1e-4);
    assert.equal(st.flareQueue[0].fireAt, st.flareQueue[1].fireAt, '第一拍应左右双发同步抛洒');
    assert.ok(Math.abs(st.flareQueue[2].fireAt - 10.40) < 1e-4, '第二拍应间隔 0.18s');
    assert.equal(st.flareQueue[2].sideSign, -st.flareQueue[3].sideSign);

    // 坠毁过程：滚转速率渐增 + 弹射座椅触发 + 撞地清空涡流
    let ejectCalls = 0;
    let skidCalls = 0;
    const mockVfx = {
      spawnEjectionArc: () => { ejectCalls += 1; return true; },
      spawnCrashSkidTrail: () => { skidCalls += 1; return true; },
      spawnExplosion: () => {},
      spawnDamageSmokePuff: () => {},
      emitCrashSmokeRibbon: () => {},
      fadeWingtipVortex: () => {},
    };
    ctrl.triggerCommand(unit, 'crash', { x: 0, z: 0 }, { simNowSec: 12.0, vfxEngine: mockVfx });
    let t = 12.0;
    for (let i = 0; i < 400 && !st.crashed; i += 1) {
      t += 1 / 60;
      ctrl.step(unit, 1 / 60, t, { terrainSupport: flatTerrain, vfxEngine: mockVfx });
    }
    assert.equal(st.ejectionTriggered, true, '高空坠毁初段应触发弹射座椅白烟弧');
    assert.equal(ejectCalls, 1, '弹射座椅每架次仅触发 1 次');
    assert.equal(st.crashed, true, '坠毁最终应落地进入 crashed');
    assert.equal(skidCalls, 1, '撞地瞬间应生成地面滑行拖痕');
    assert.equal(st.wingtipVortexActive, false, '撞地后翼尖涡流必须关闭');
  }

  // 6. M3.1 入航曲线导出 & M3.2 拖线预估耗弹预览
  {
    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
    const unit = makeMockPlaneUnit('US_A10', 'fw_path');
    ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });
    const res = ctrl.issueStrikeCommand(unit, {
      cmd: 'strafe',
      startPos: { x: 400, z: 200 },
      endPos: { x: 520, z: 200 },
    }, { simNowSec: 1, terrainSupport: flatTerrain });
    assert.equal(res.ok, true);
    const pPath = ctrl.getPredictedFlightPath(unit);
    assert.equal(pPath.phase, 'transit');
    assert.equal(pPath.goAroundCount, 0);
    assert.equal(pPath.phaseLabel, '绕飞入航');
    assert.ok(Array.isArray(pPath.approachCurve) && pPath.approachCurve.length >= 6, 'transit 应输出 >=6 个贝塞尔入航采样点');

    const strafeOk = formatDragLineAmmoPreview({ kind: 'autocannon', strafeLenMinM: 60, strafeLenMaxM: 240, roundsPerMeter: 0.85, ammo: 1150 }, 120);
    assert.equal(strafeOk.text, '120m · ~102发');
    assert.equal(strafeOk.insufficient, false);

    const strafeLow = formatDragLineAmmoPreview({ kind: 'autocannon', strafeLenMinM: 60, strafeLenMaxM: 240, roundsPerMeter: 0.85, ammo: 40 }, 120);
    assert.equal(strafeLow.text, '120m · 余弹仅 40');
    assert.equal(strafeLow.insufficient, true);

    const rocketOk = formatDragLineAmmoPreview({ kind: 'rocket', strafeLenMinM: 60, strafeLenMaxM: 180, salvoCount: 7, ammo: 21 }, 90);
    assert.equal(rocketOk.text, '90m · 7发齐射');
    assert.equal(rocketOk.insufficient, false);
  }

  // 7. M3.3 Winchester 弹罄事件、默认自动 RTB 与可召回留场 + Bingo 锁定隔离
  {
    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 }, autoRtbOnWinchester: true });
    const unit = makeMockPlaneUnit('US_A10', 'fw_winchester');
    const st = ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });
    let winEvents = 0;
    ctrl.depleteGroundAmmo(unit, {
      simNowSec: 5.0,
      onWinchester: () => { winEvents += 1; },
    });
    assert.equal(winEvents, 1, '全对地弹罄应触发一次 onWinchester 回调');
    assert.equal(st.winchesterState, 'rtb_winchester');
    assert.equal(st.flightState, 'rtb');

    const recallRes = ctrl.cancelEvacuate(unit);
    assert.equal(recallRes.ok, true, 'Winchester 自动 RTB 应允许玩家按 T 召回');
    assert.equal(st.flightState, 'orbit');
    assert.equal(st.winchesterState, 'winchester');
    // 召回后继续推进 step，不得再次自动拉起 RTB
    ctrl.step(unit, 0.1, 5.2, { terrainSupport: flatTerrain });
    assert.equal(st.flightState, 'orbit', 'Winchester 召回后应稳稳留场侦察不再自动返航');

    // 对照：Bingo Fuel 必须仍强制锁定不可召回
    ctrl.setFuelRatio(unit, 0.08);
    ctrl.step(unit, 0.1, 5.4, { terrainSupport: flatTerrain });
    assert.equal(st.flightState, 'rtb');
    const bingoRecall = ctrl.cancelEvacuate(unit);
    assert.equal(bingoRecall.ok, false);
    assert.equal(bingoRecall.bingoLocked, true);
  }

  // 8. M3.4 三点前方地形前瞻防撞 & A-10C 永不触发音爆/加力
  {
    const ridgeTerrain = {
      heightAt: (x, z) => (z > 120 ? 130 : 2),
    };
    const maxAhead = sampleAheadMaxTerrainY(ridgeTerrain, 0, 0, 0, 97.5);
    assert.equal(maxAhead, 130, '前方 120/240/360m 探测应捕获前方 130m 山脊');

    const ctrl = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
    const unit = makeMockPlaneUnit('US_A10', 'fw_lookahead');
    const st = ctrl.attachUnit(unit, { orbitCenter: { x: 0, z: 0 } });
    unit.node.position.set(0, 145, 0);
    unit.node.rotation.y = 0;
    st.orbitAltitude = 140;
    st.pitchDeg = -4;
    for (let i = 0; i < 45; i += 1) {
      ctrl.step(unit, 1 / 60, i / 60, { terrainSupport: ridgeTerrain });
    }
    assert.ok(st.pitchDeg > 2.5, `低空面向前方高地形应触发正俯仰平滑改出: ${st.pitchDeg}`);
    assert.ok(unit.node.position.y >= 150, `前瞻防撞应平滑抬升高度越障: ${unit.node.position.y}`);

    let boomCount = 0;
    const abRes = ctrl.triggerAfterburnerAndSonicBoom(unit, 100, {
      audioBridge: { playSonicBoom: () => { boomCount += 1; } },
    });
    assert.equal(abRes.triggered, false);
    assert.equal(abRes.reason, 'subsonic_disabled');
    assert.equal(boomCount, 0);
  }

  console.log('ALL_FIXED_WING_BASE_RULE_TESTS_PASSED (8/8)');
}

runTests();
