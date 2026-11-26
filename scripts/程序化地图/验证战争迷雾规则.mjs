/**
 * Scavenger 9km 战场战争迷雾（SCAV-8 / fog-of-war-v1）纯规则单元测试（Node ESM）。
 * 验证内容：
 * 1. ba-source-ssot-v1 派生一致性（6 型 UAV 分级 + AC-130 盘旋光电 + opticsScale 缩放）；
 * 2. 双通道 R（实时）与 G（探索单调累积）及 10Hz 节流刷新；
 * 3. UAV 高低空半径与密林掩体穿透档位（high=128 / low=255）；
 * 4. 有人固定翼窄走廊 + 胶囊扫掠防断节 + 飞离转失联；
 * 5. AC-130 盘旋聚焦与 heavy_support_ew 态势热区 A 通道；
 * 6. Bingo Fuel 返航预警与离场断视；
 * 7. 地面山脊/建筑粗遮挡 vs 直升机穿透；
 * 8. 敌情显隐、失联幽灵冻结不追踪与再查证清除；
 * 9. 固定翼 AGM-65 制导锁定强制实时视野（UAV 照射协同）vs JDAM/机炮允许盲射；
 * 10. 性能预算（20 单位 10Hz 刷新耗时检查）。
 */

import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';

import {
  GLOBAL_BALANCE_SCALES,
  FOG_RECON_PROFILES,
  deriveUnitReconProfile,
} from '../../src/程序化地图/数值基底与倍率规范.mjs';
import {
  FOG_CONTRACT_VERSION,
  FogOfWarEngine,
  extractUnitVisionEmitter,
} from '../../src/程序化地图/战争迷雾引擎.mjs';
import { AutoPhysicsAndNavEngine } from '../../src/程序化地图/自动物理派生.mjs';
import {
  FIXED_WING_REGISTRY,
  FixedWingBaseController,
} from '../../src/程序化地图/固定翼底座.mjs';

function runTests() {
  // 1) ba-source-ssot-v1 派生规范与 6 架 UAV 梯队校验
  const uavSlugs = ['US_RQ7', 'US_MQ1B', 'US_MQ1C', 'US_MQ9', 'US_RQ170', 'US_RQ4B'];
  const uavRadii = uavSlugs.map(slug => {
    const spec = FIXED_WING_REGISTRY[slug];
    assert.ok(spec, `应存在无人机注册项 ${slug}`);
    assert.equal(spec.gameStats.reconProfile.shape, 'circle', `${slug} 应为持续圆形光电视野`);
    return spec.gameStats.reconProfile.highRadiusM;
  });
  for (let i = 1; i < uavRadii.length; i += 1) {
    assert.ok(
      uavRadii[i] > uavRadii[i - 1],
      `UAV 侦察半径应按梯队递增: ${uavSlugs[i - 1]}(${uavRadii[i - 1]}m) < ${uavSlugs[i]}(${uavRadii[i]}m)`,
    );
  }
  assert.equal(
    FIXED_WING_REGISTRY.US_AC130J.gameStats.reconProfile.profileKey,
    'cas_gunship_orbit_pod',
    'AC-130J 应派生为盘旋炮艇光电配置',
  );
  const scaledRecon = deriveUnitReconProfile(
    { opticsM: 4000 },
    { role: 'uav', archetype: 'fixed_wing_uav' },
    { ...GLOBAL_BALANCE_SCALES, opticsScale: 0.5 },
  );
  assert.equal(
    scaledRecon.highRadiusM,
    Math.round(4000 * 0.5 * FOG_RECON_PROFILES.fixed_wing_uav.highRadiusFactor),
    'opticsScale 必须等比缩放派生视野半径',
  );

  // 2) 探索保留 G 与实时视野 R 切换 + 10Hz 节流
  const fog = new FogOfWarEngine({ width: 256, height: 256, tickIntervalSec: 0.1 });
  const groundUnit = { id: 'TANK_1', role: 'ground', opticsM: 1400, position: { x: 0, z: 0 } };
  assert.equal(fog.update({ units: [groundUnit], nowSec: 0.0 }), true, '首帧应执行刷新');
  assert.equal(fog.update({ units: [groundUnit], nowSec: 0.04 }), false, '未满 100ms 应节流跳过');
  const qCenter = fog.queryPoint(0, 0);
  assert.equal(qCenter.visible, true);
  assert.equal(qCenter.explored, true);

  // 单位移走 1800m 后，原点应变为已探索灰雾（explored=true, visible=false）
  groundUnit.position.x = 1800;
  assert.equal(fog.update({ units: [groundUnit], nowSec: 0.2 }), true);
  const qOld = fog.queryPoint(0, 0);
  assert.equal(qOld.visible, false, '单位离开后原点不应维持实时视野 R');
  assert.equal(qOld.explored, true, '单位离开后原点必须保留已探索地形 G');

  // 3) UAV 高低空半径差异与密林掩体穿透档位
  fog.reset();
  const mq9 = {
    id: 'UAV_MQ9',
    slug: 'US_MQ9',
    role: 'uav',
    archetype: 'fixed_wing_uav',
    fixedWingSpec: FIXED_WING_REGISTRY.US_MQ9,
    altitudeMode: 'high',
    position: { x: 500, z: 500 },
  };
  fog.update({ units: [mq9], nowSec: 1.0, force: true });
  const highOuter = fog.queryPoint(500 + 820, 500);
  const highCover = fog.queryPoint(500, 500, { inCover: true });
  assert.equal(highOuter.visible, true, 'MQ-9 高空应能看到 820m 处开阔地');
  assert.equal(highCover.visible, false, 'MQ-9 高空广域模式不应直接看穿密林精细掩体');

  mq9.altitudeMode = 'low';
  fog.update({ units: [mq9], nowSec: 1.2, force: true });
  const lowOuter = fog.queryPoint(500 + 820, 500);
  const lowCover = fog.queryPoint(500, 500, { inCover: true });
  assert.equal(lowOuter.visible, false, 'MQ-9 切低空后半径收窄，820m 处不再可见');
  assert.equal(lowCover.visible, true, 'MQ-9 切低空后 penetration=255，应可穿透密林查漏');

  // 4) 有人固定翼窄走廊 + 胶囊扫掠防断节
  fog.reset();
  const f15 = {
    id: 'JET_F15E',
    slug: 'US_F15E',
    role: 'fixed_wing',
    archetype: 'supersonic_multirole',
    fixedWingSpec: FIXED_WING_REGISTRY.US_F15E,
    yaw: Math.PI / 2, // 沿 +X 飞行
    position: { x: -400, z: 0 },
  };
  fog.update({ units: [f15], nowSec: 2.0, force: true });
  // 模拟高速跃进 320m，胶囊扫掠必须覆盖中点 (-240, 0)，但 450m 侧翼保持暗影（不洗全图）
  f15.position.x = -80;
  fog.update({ units: [f15], nowSec: 2.12, force: true });
  assert.equal(fog.queryPoint(-240, 0).visible, true, '高速固定翼航迹中点必须由胶囊扫掠连续覆盖');
  assert.equal(fog.queryPoint(-240, 0).explored, true, '固定翼窄带允许写入 G 地形轮廓');
  assert.equal(fog.queryPoint(-80, 450).visible, false, '有人固定翼侧翼 450m 外不应被洗亮');
  assert.equal(fog.queryPoint(-80 + 360, 0).visible, true, '有人固定翼机头前向窄锥应能探测前方');

  // 5) AC-130 盘旋光电聚焦 + EW 态势热区 A 通道
  fog.reset();
  const ac130 = {
    id: 'AC130_1',
    slug: 'US_AC130J',
    role: 'fixed_wing',
    archetype: 'cas_gunship',
    fixedWingSpec: FIXED_WING_REGISTRY.US_AC130J,
    orbitCenter: { x: 1000, z: 1000 },
    isOrbiting: true,
    position: { x: 1180, z: 1000 },
  };
  const ea6b = {
    id: 'EW_EA6B',
    slug: 'US_EA6B_Prowler',
    role: 'fixed_wing',
    archetype: 'heavy_support_ew',
    fixedWingSpec: FIXED_WING_REGISTRY.US_EA6B_Prowler,
    position: { x: -1500, z: -1500 },
  };
  fog.update({ units: [ac130, ea6b], nowSec: 3.0, force: true });
  assert.equal(fog.queryPoint(1000, 1000).visible, true, 'AC-130 盘旋态应以盘旋中心开启中等光电聚焦圈');
  const ewEdge = fog.queryPoint(-1500 + 800, -1500);
  assert.equal(ewEdge.visible, false, 'EW 电战机 800m 处不应破除 3D 精细实时迷雾');
  assert.ok(ewEdge.ewPulse > 0.2, `EW 电战机 800m 处应写入 A 通道宏观态势热区 (实测 ${ewEdge.ewPulse.toFixed(2)})`);

  // 6) SCAV-6 Bingo Fuel 返航预警与视野收拢/断视
  fog.reset();
  const uavBingo = {
    id: 'UAV_BINGO',
    slug: 'US_MQ9',
    role: 'uav',
    archetype: 'fixed_wing_uav',
    fixedWingSpec: FIXED_WING_REGISTRY.US_MQ9,
    altitudeMode: 'high',
    position: { x: 0, z: 0 },
    fuelTelemetry: { fuelPct: 14, bingoState: 'rtb_bingo' },
    flightAttitude: { flightState: 'rtb' },
  };
  fog.update({ units: [uavBingo], nowSec: 4.0, force: true });
  const snapBingo = fog.getSnapshot(4.0);
  assert.equal(snapBingo.specVersion, FOG_CONTRACT_VERSION);
  assert.equal(snapBingo.emitters[0].bingoWarning, true, '触发 Bingo 返航时必须挂 bingoWarning 预警标志');
  assert.ok(snapBingo.emitters[0].liveRadiusM < FIXED_WING_REGISTRY.US_MQ9.gameStats.reconProfile.highRadiusM, '返航途中 UAV 光电半径应收拢');
  uavBingo.evacuated = true;
  fog.update({ units: [uavBingo], nowSec: 4.2, force: true });
  assert.equal(fog.queryPoint(0, 0).visible, false, 'UAV 返航离场后前线实时视野必须合拢');

  // 7) 地面单位粗遮挡 vs 直升机穿透低障
  const wallFog = new FogOfWarEngine({
    width: 256,
    height: 256,
    occlusionQuery: (_x0, _z0, x1, _z1) => x1 >= 120 && x1 <= 160 && Math.abs(_z1) <= 200,
  });
  const tank = { id: 'T_OCC', role: 'ground', opticsM: 1400, position: { x: 0, z: 0 } };
  wallFog.update({ units: [tank], nowSec: 5.0, force: true });
  assert.equal(wallFog.queryPoint(60, 0).visible, true, '遮挡物前侧应可见');
  assert.equal(wallFog.queryPoint(260, 0).visible, false, '大型建筑后方 260m 地面单位视线应被截断');

  const heli = { id: 'H_PASS', role: 'helicopter', opticsM: 1800, position: { x: 0, z: 0 } };
  wallFog.update({ units: [heli], nowSec: 5.2, force: true });
  assert.equal(wallFog.queryPoint(260, 0).visible, true, '直升机应穿透低矮地面建筑遮挡');

  // 8) 敌情显隐与失联幽灵冻结（不追踪隐藏移动 + 重新核查清除）
  fog.reset();
  const scout = { id: 'SCOUT_1', role: 'ground', opticsM: 1200, position: { x: 0, z: 0 } };
  const horde = { id: 'ZOMBIE_A', kind: 'zombie_group', x: 100, z: 0, count: 42, node: { visible: true } };
  fog.update({ units: [scout], enemies: [horde], nowSec: 10.0, force: true });
  assert.equal(horde.fogVisible, true);
  assert.equal(horde.node.visible, true);
  assert.equal(fog.getSnapshot(10.0).ghostContacts.length, 0);

  // 敌群跑出视野，随后继续偷偷向 (900, 0) 移动；失联幽灵必须冻结在 (100, 0) 而不追踪到 (900, 0)
  scout.position.x = -1200;
  horde.x = 600;
  fog.update({ units: [scout], enemies: [horde], nowSec: 12.0, force: true });
  assert.equal(horde.fogVisible, false);
  assert.equal(horde.node.visible, false, '迷雾外敌群 3D 节点必须隐藏');
  let ghosts = fog.getSnapshot(12.0).ghostContacts;
  assert.equal(ghosts.length, 1, '刚离开视野应生成 1 个失联幽灵标');
  assert.equal(ghosts[0].x, 100, '幽灵标必须冻结在最后发现 X=100');
  assert.equal(ghosts[0].lastSeenSec, 10.0);

  horde.x = 900;
  fog.update({ units: [scout], enemies: [horde], nowSec: 16.0, force: true });
  ghosts = fog.getSnapshot(16.0).ghostContacts;
  assert.equal(ghosts.length, 1);
  assert.equal(ghosts[0].x, 100, '敌人暗中移动后，失联幽灵绝不能追踪其新坐标');
  assert.equal(ghosts[0].ageSec, 6.0, '失联幽灵年龄应随时间递增');

  // 侦察车回到 (100, 0) 证实敌人已经不在那里 → 清除过时幽灵
  scout.position.x = 100;
  fog.update({ units: [scout], enemies: [horde], nowSec: 18.0, force: true });
  assert.equal(fog.getSnapshot(18.0).ghostContacts.length, 0, '重新观测最后发现点为空后应消除失联幽灵');

  // 9) 固定翼 AGM-65 制导锁定强制实时视野（UAV 照射协同）vs JDAM 盲射
  fog.reset();
  const fwCtrl = new FixedWingBaseController({ scene: null });
  const a10 = {
    id: 'US_A10_TEST',
    slug: 'US_A10',
    role: 'fixed_wing',
    node: {
      position: {
        x: -600,
        y: 185,
        z: -600,
        set(nx, ny, nz) { this.x = nx; this.y = ny; this.z = nz; },
      },
      rotation: { x: 0, y: 0, z: 0 },
      visible: true,
    },
  };
  fwCtrl.attachUnit(a10, { orbitCenter: { x: -600, z: -600 } });
  fwCtrl.fogQuery = (wx, wz) => fog.queryPoint(wx, wz);

  // 目标 (1200, 1200) 在迷雾外：AGM-65 必须被拒绝，JDAM 坐标轰炸允许盲射
  const maverickBlocked = fwCtrl.issueStrikeCommand(
    a10,
    { cmd: 'missile', targetPos: { x: 1200, z: 1200 } },
    { simNowSec: 20.0 },
  );
  assert.equal(maverickBlocked.ok, false, 'AGM-65 不应允许锁定迷雾外目标');
  assert.equal(maverickBlocked.fogBlocked, true, '拒绝原因应标明 fogBlocked');

  const jdamBlind = fwCtrl.issueStrikeCommand(
    a10,
    { cmd: 'bomb', targetPos: { x: 1200, z: 1200 } },
    { simNowSec: 20.0 },
  );
  assert.equal(jdamBlind.ok, true, 'GBU-31 JDAM 坐标类武器应允许对迷雾区盲射');

  // 派 UAV 照亮 (1200, 1200) → 同一目标区立刻允许 A-10C 远距 AGM-65 锁定！
  const spotterUav = {
    id: 'UAV_SPOTTER',
    slug: 'US_MQ9',
    role: 'uav',
    archetype: 'fixed_wing_uav',
    fixedWingSpec: FIXED_WING_REGISTRY.US_MQ9,
    altitudeMode: 'high',
    position: { x: 1200, z: 1200 },
  };
  fog.update({ units: [spotterUav], nowSec: 20.5, force: true });
  const maverickGuided = fwCtrl.issueStrikeCommand(
    a10,
    { cmd: 'missile', targetPos: { x: 1200, z: 1200 } },
    { simNowSec: 20.6 },
  );
  assert.equal(maverickGuided.ok, true, 'UAV 实时照亮目标区后，A-10C AGM-65 应允许远程锁定发射');

  // 10) 10Hz 性能预算自测（20 单位混合，连续 40 次全刷）
  fog.reset();
  const benchUnits = [];
  for (let i = 0; i < 20; i += 1) {
    const isUav = i < 4;
    const isJet = i >= 4 && i < 10;
    benchUnits.push({
      id: `B_${i}`,
      slug: isUav ? 'US_MQ9' : isJet ? 'US_F15E' : 'US_M1A2',
      role: isUav ? 'uav' : isJet ? 'fixed_wing' : 'ground',
      archetype: isUav ? 'fixed_wing_uav' : isJet ? 'supersonic_multirole' : '',
      fixedWingSpec: isUav ? FIXED_WING_REGISTRY.US_MQ9 : isJet ? FIXED_WING_REGISTRY.US_F15E : null,
      opticsM: isUav ? 3800 : isJet ? 2400 : 1400,
      yaw: (i * 0.7) % (Math.PI * 2),
      position: { x: ((i * 530) % 6000) - 3000, z: ((i * 710) % 6000) - 3000 },
    });
  }
  const t0 = performance.now();
  const iters = 40;
  for (let k = 0; k < iters; k += 1) {
    for (const u of benchUnits) {
      u.position.x += 12;
      u.position.z += 7;
    }
    fog.update({ units: benchUnits, nowSec: 30 + k * 0.12, force: true });
  }
  const avgMs = (performance.now() - t0) / iters;
  assert.ok(avgMs < 8.0, `20 单位 10Hz 单次刷新耗时过高: ${avgMs.toFixed(2)}ms`);

  // 11) 混编样本 index 回退与空 unitId 禁写 _unitRayCache 防碰撞校验
  const emIdx = extractUnitVisionEmitter({ index: 7, role: 'ground', position: { x: 10, z: 20 } });
  assert.equal(emIdx?.unitId, 'idx_7', '缺失 id/unitId 且有有限 index 时应回退为 idx_<index>');
  const anonFog = new FogOfWarEngine({
    width: 256,
    height: 256,
    occlusionQuery: (_x0, _z0, x1, _z1) => x1 >= 90 && x1 <= 130 && Math.abs(_z1) <= 120,
  });
  const anonBlocked = { role: 'ground', opticsM: 1400, position: { x: 0, z: 0 } };
  const anonOpen = { role: 'ground', opticsM: 1400, position: { x: 2000, z: 2000 } };
  anonFog.update({ units: [anonBlocked, anonOpen], nowSec: 1.0, force: true });
  assert.equal(anonFog._unitRayCache.size, 0, '空 unitId 严禁写入 _unitRayCache');
  assert.equal(anonFog.queryPoint(240, 0).visible, false, '匿名受遮挡单位后方仍应被遮挡');
  assert.equal(anonFog.queryPoint(2240, 2000).visible, true, '匿名开阔地单位不得受空键缓存污染');

  // 12) 纯建筑 OBB 遮挡位图光栅化（含 <11.6m 薄墙命中）与编辑器局部失效闭环
  const flatSupport = { heightAt: () => 0, query: () => ({ state: 'ready', height: 0, waterDepth: 0, role: 1 }) };
  const mockPhysics = new AutoPhysicsAndNavEngine({
    b3Manifest: {
      bounds: { minX: -4425, maxX: 4575, minZ: -4150, maxZ: 4850 },
      building_prototypes: [
        { id: 'thin_wall', prototype_index: 0, bounds_min: [-2, 0, -40], bounds_max: [2, 16, 40] },
        { id: 'block_house', prototype_index: 1, bounds_min: [-12, 0, -12], bounds_max: [12, 14, 12] },
      ],
    },
    b2Manifest: { tree_prototypes: [] },
    buildingIndex: null,
    collisionIndex: null,
    treeIndex: null,
    support: flatSupport,
  });
  const obbFog = new FogOfWarEngine({
    width: 256,
    height: 256,
    terrainSupport: flatSupport,
    enableStaticOcclusionCache: true,
    buildingOcclusionRasterizer: (outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1) => {
      mockPhysics.rasterizeBuildingOcclusionRegion(outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1, 1.5);
    },
  });
  mockPhysics.onOcclusionDirty(({ x, z, radiusM }) => {
    if (Number.isFinite(x) && Number.isFinite(z)) {
      obbFog.invalidateOcclusionRegion(x, z, radiusM || 48);
    } else {
      obbFog.invalidateOcclusionCache();
    }
  });
  const scoutTank = { id: 'T_THIN', role: 'ground', opticsM: 1400, position: { x: 0, z: 0 } };
  obbFog.update({ units: [scoutTank], nowSec: 50.0, force: true });
  assert.equal(obbFog.queryPoint(240, 0).visible, true, '放墙前 (240, 0) 应可见');

  // 放置厚度仅 4m (<11.6m) 的薄壁建筑：必须被 512×512 SAT 覆盖命中且立即生效
  const addedWall = mockPhysics.addInstance({
    id: 'wall_thin_1',
    prototype_id: 'thin_wall',
    position: [110, 0, 0],
    yaw_rad: 0,
    scale: [1, 1, 1],
  });
  obbFog.update({ units: [scoutTank], nowSec: 50.2, force: true });
  assert.equal(obbFog.queryPoint(240, 0).visible, false, '宽度仅 4m 的薄墙放置后应立即截断后方视线');

  // 删除薄壁建筑后局部失效生效，后方视野立即恢复
  mockPhysics.deleteInstance(addedWall.id);
  obbFog.update({ units: [scoutTank], nowSec: 50.4, force: true });
  assert.equal(obbFog.queryPoint(240, 0).visible, true, '删除薄墙后局部失效并应立即恢复后方视野');

  // 13) 正式 50 车全地面满编 & 100 混编（50 己方）带真实旋转 OBB 压测（硬门槛 <= 2.0ms）
  const benchPhysics = new AutoPhysicsAndNavEngine({
    b3Manifest: {
      bounds: { minX: -4425, maxX: 4575, minZ: -4150, maxZ: 4850 },
      building_prototypes: [
        { id: 'block_house', prototype_index: 0, bounds_min: [-10, 0, -14], bounds_max: [10, 15, 14] },
      ],
    },
    b2Manifest: { tree_prototypes: [] },
    buildingIndex: null,
    collisionIndex: null,
    treeIndex: null,
    support: flatSupport,
  });
  for (let b = 0; b < 180; b += 1) {
    const gx = ((b % 15) - 7) * 260 + 55;
    const gz = (Math.floor(b / 15) - 6) * 260 + 40;
    benchPhysics.addInstance({
      id: `bench_bldg_${b}`,
      prototype_id: 'block_house',
      position: [gx, 0, gz],
      yaw_rad: (b * 0.37) % Math.PI,
      scale: [1, 1, 1],
    });
  }
  const benchFog = new FogOfWarEngine({
    width: 256,
    height: 256,
    terrainSupport: flatSupport,
    enableStaticOcclusionCache: true,
    buildingOcclusionRasterizer: (outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1) => {
      benchPhysics.rasterizeBuildingOcclusionRegion(outGrid, gridW, gridH, bounds, ix0, iz0, ix1, iz1, 1.5);
    },
  });

  // 13A: 50 车全地面满编（正式上限 MAX_HERO_UNITS = 50）
  const full50Ground = [];
  for (let i = 0; i < 50; i += 1) {
    full50Ground.push({
      id: `HERO_${i}`,
      role: 'ground',
      opticsM: 1400,
      position: { x: ((i % 10) - 4.5) * 180, z: (Math.floor(i / 10) - 2) * 180 },
    });
  }
  // 预热 2 帧填充局部 8×8 静态块缓存，再测连续移动 + 静止混合 50 次 10Hz 刷新
  benchFog.update({ units: full50Ground, nowSec: 100.0, force: true });
  benchFog.update({ units: full50Ground, nowSec: 100.2, force: true });
  const t50 = performance.now();
  const iters50 = 50;
  for (let k = 0; k < iters50; k += 1) {
    // 模拟 30% 单位持续行进（每 tick 移动 2m）+ 70% 驻守
    for (let i = 0; i < 15; i += 1) {
      full50Ground[i].position.x += 2.0;
    }
    benchFog.update({ units: full50Ground, nowSec: 101 + k * 0.12, force: true });
  }
  const avg50Ms = (performance.now() - t50) / iters50;
  assert.ok(avg50Ms <= 2.0, `50 车全地面满编 10Hz 单次刷新均值超标: ${avg50Ms.toFixed(3)}ms (> 2.0ms)`);

  // 13B: 100 混编（过滤后 50 己方：38 地面 + 8 直升机 + 4 固定翼）全移动压测
  benchFog.reset();
  const mixed50Friendly = [];
  for (let i = 0; i < 100; i += 1) {
    const team = i % 2;
    if (team !== 0) continue;
    const isAir = i >= 76;
    const isHeli = isAir && i < 92;
    mixed50Friendly.push({
      id: `squad_${i}`,
      index: i,
      team,
      role: isHeli ? 'helicopter' : isAir ? 'fixed_wing' : 'ground',
      opticsM: isHeli ? 1800 : isAir ? 2400 : 1400,
      yaw: 0,
      position: { x: ((i % 10) - 4.5) * 45, z: (Math.floor(i / 10) - 4.5) * 45 },
    });
  }
  benchFog.update({ units: mixed50Friendly, nowSec: 200.0, force: true });
  const tMix = performance.now();
  const itersMix = 50;
  for (let k = 0; k < itersMix; k += 1) {
    for (let i = 0; i < mixed50Friendly.length; i += 1) {
      const u = mixed50Friendly[i];
      const ang = (200 + k * 0.1) * 0.55 + i * 0.47;
      u.position.x = ((i % 10) - 4.5) * 45 + Math.cos(ang) * 16;
      u.position.z = (Math.floor(i / 10) - 4.5) * 45 + Math.sin(ang) * 16;
    }
    benchFog.update({ units: mixed50Friendly, nowSec: 201 + k * 0.12, force: true });
  }
  const avgMixMs = (performance.now() - tMix) / itersMix;
  assert.ok(avgMixMs <= 2.0, `100 混编(50己方全移动) 10Hz 单次刷新均值超标: ${avgMixMs.toFixed(3)}ms (> 2.0ms)`);

  console.log(
    `[PASS] SCAV-8 战争迷雾规则与性能全套单测通过 (13/13 组, 20 单位 ${avgMs.toFixed(2)}ms, 50 满编地面 ${avg50Ms.toFixed(3)}ms, 100 混编己方 ${avgMixMs.toFixed(3)}ms)`,
  );
}

runTests();
