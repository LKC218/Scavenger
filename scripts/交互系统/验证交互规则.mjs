/**
 * Scavenger 操作交互系统纯规则单元测试（Node ESM，无 DOM / Three.js 依赖）。
 * 验证队形保持、等比间距放大、横队展开、空地分道、Shift 队列、取消按兵种退场、
 * CQS 不可达拒收保原任务、pending 准备路径、Tab 重叠循环、5 级 LIFO ESC 仲裁与 12 类载具映射。
 */

import assert from 'node:assert/strict';
import {
  INTERACTION_MODES,
  CURSOR_CLASSES,
  DEFAULT_MAP_BOUNDS,
  resolveVehicleArchetype,
  wrapAngleRad,
  isUnitAllowedByChannel,
  planFleetCommand,
  dispatchFleetCommand,
  cancelFleetUnits,
  createModeAndEscArbiter,
  MAX_HERO_UNITS,
  DEPLOY_CATEGORY_QUOTAS,
  resolveDeployCategory,
  computeDeployQuotaSummary,
  formatVehicleModelDesignation,
  formatUnitDisplayNames,
} from '../../src/交互系统/契约与仲裁.mjs';
import {
  cycleOverlappingUnits,
  resolveZoomIconTier,
  getZoomTierIconSize,
  clusterScreenUnits,
  sampleGroundOrbitPoints,
  computeGroundFootProjection,
  resolveRouteStyle,
} from '../../src/界面规范/图标与标绘.mjs';
import {
  ASPECT_CYCLE,
  nextAspectKey,
  getStandardAspectSpec,
  computeHorPlusCameraFrustum,
} from '../../src/程序化地图/渲染基线.mjs';
import { createSelectionAndGroupController } from '../../src/交互系统/选区与编组.mjs';
import {
  computeGroundTerrainTilt,
  computeAircraftBankRoll,
  stepVehicleAnimations,
} from '../../src/程序化地图/载具动效控制.mjs';
import {
  computeSpanAudioGain,
  computeEngineMotionState,
  chooseSpatialEngines,
} from '../../src/程序化地图/载具空间音效.mjs';
import {
  CAMERA_TILT_RAD,
  computeCameraAxes,
  projectAirAnchorToGroundFocus,
  createCameraController,
} from '../../src/交互系统/镜头控制.mjs';
import {
  computeMinimapViewport,
  computeAnchoredZoomViewport,
  computeMinimapScaleBar,
  computeDynamicMgrsGrid,
  worldToMinimapPx,
  minimapPxToWorld,
  filterDeckUnits,
  aggregateDeckUnitsBySlug,
  classifyBuildingRadarTier,
  stylizeTacticalTerrainRgb,
  extractRoadTriangles2D,
  rasterizeRoadTrianglesToMask,
} from '../../src/程序化地图/战术小地图.mjs';
import {
  computePipCameraPose,
  wrapAngleRad as wrapPipAngleRad,
  stepPipSmoothAzimuth,
} from '../../src/程序化地图/实时实况镜头.mjs';
import { isFixedWingOrbiting } from '../../src/交互系统/指令与标绘层.mjs';
import { AutoPhysicsAndNavEngine } from '../../src/程序化地图/自动物理派生.mjs';
import {
  createEscStack,
  DEFAULT_UI_PREFS,
  TACTICAL_WHEEL_CONFIG,
  resolveWheelSectorFromVector,
  clampWheelVisualCenter,
  canCommitWheelSlot,
} from '../../src/界面规范/交互控件.mjs';
import {
  FixedWingBaseController,
  canMergeMissileIntoPlan,
} from '../../src/程序化地图/固定翼底座.mjs';

const cases = [];
function test(name, fn) {
  cases.push({ name, fn });
}

function makeReadyEnv(overrides = {}) {
  return {
    bounds: DEFAULT_MAP_BOUNDS,
    querySupport: () => ({ state: 'ready', height: 2.0 }),
    isPointBlocked: () => false,
    findGroundPath: (sx, sz, gx, gz) => ({
      status: 'ready',
      found: true,
      waypoints: [[sx, 2.25, sz], [gx, 2.25, gz]],
    }),
    buildHeliProfile: (sx, sz, gx, gz) => [[sx, 24, sz], [gx, 24, gz]],
    ...overrides,
  };
}

test('01. 单车普通移动与带朝向移动写入 job、targetFacing 与航路', () => {
  const unit = { id: 'U_M1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const env = makeReadyEnv();
  const r1 = dispatchFleetCommand({
    units: [unit],
    ids: ['U_M1'],
    command: { type: 'move', point: { x: 120, z: -80 } },
    env,
    serial: 1,
  });
  assert.equal(r1.accepted, 1);
  assert.equal(r1.rejected, 0);
  assert.equal(unit.job.type, 'move');
  assert.deepEqual(unit.job.point, { x: 120, z: -80 });
  assert.equal(unit.targetFacing, null);
  assert.equal(unit.waypoints.length, 2);

  const facing = Math.PI * 0.25;
  const r2 = dispatchFleetCommand({
    units: [unit],
    ids: ['U_M1'],
    command: { type: 'attackMove', point: { x: 200, z: 50 }, facing },
    env,
    serial: 2,
  });
  assert.equal(r2.accepted, 1);
  assert.equal(unit.job.type, 'attackMove');
  assert.equal(unit.targetFacing, facing);
  assert.deepEqual(unit.queue, []);
});

test('02. 多地面车无朝向移动保留相对方位且等比放大过密间距至 >=12m', () => {
  const u1 = { id: 'G1', role: 'ground', x: 10, y: 0, z: 0, job: null, queue: [] };
  const u2 = { id: 'G2', role: 'ground', x: 12, y: 0, z: 0, job: null, queue: [] }; // 仅距 2m
  const plan = planFleetCommand({
    units: [u1, u2],
    ids: ['G1', 'G2'],
    command: { type: 'move', point: { x: 300, z: 200 } },
    env: makeReadyEnv(),
  });
  assert.equal(plan.length, 2);
  const d = Math.hypot(plan[0].job.point.x - plan[1].job.point.x, plan[0].job.point.z - plan[1].job.point.z);
  assert.ok(d >= 11.99, `地面最小编队间距应放大到 >=12m，实际 ${d}`);
  // 维持 G1 在左、G2 在右的相对关系
  assert.ok(plan[0].job.point.x < plan[1].job.point.x);
});

test('03. 多旋翼无朝向移动保持相对队形且最小间距放大至 >=20m', () => {
  const h1 = { id: 'H1', role: 'helicopter', x: 0, y: 20, z: 0, job: null, queue: [] };
  const h2 = { id: 'H2', role: 'helicopter', x: 0, y: 20, z: 4, job: null, queue: [] };
  const plan = planFleetCommand({
    units: [h1, h2],
    ids: ['H1', 'H2'],
    command: { type: 'move', point: { x: 100, z: 100 } },
    env: makeReadyEnv(),
  });
  const d = Math.hypot(plan[0].job.point.x - plan[1].job.point.x, plan[0].job.point.z - plan[1].job.point.z);
  assert.ok(d >= 19.99, `旋翼最小间距应放大到 >=20m，实际 ${d}`);
  assert.ok(plan[0].job.point.z < plan[1].job.point.z);
});

test('04. 右键拖朝向展开横队：沿垂直右轴排列且严格赋予指定朝向', () => {
  const g1 = { id: 'G1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const g2 = { id: 'G2', role: 'ground', x: 5, y: 0, z: 25, job: null, queue: [] };
  const g3 = { id: 'G3', role: 'ground', x: -12, y: 0, z: -18, job: null, queue: [] };
  const facing = 0; // 朝 +Z，横队垂轴为 +X
  const plan = planFleetCommand({
    units: [g1, g2, g3],
    ids: ['G1', 'G2', 'G3'],
    command: { type: 'move', point: { x: 500, z: 300 }, facing },
    env: makeReadyEnv(),
  });
  assert.deepEqual(plan.map(p => p.job.point), [
    { x: 488, z: 300 },
    { x: 500, z: 300 },
    { x: 512, z: 300 },
  ]);
  for (const row of plan) assert.equal(row.job.facing, facing);
});

test('05. 空地混选纵向分道偏移且固定翼各自独立盘旋锚点', () => {
  const g1 = { id: 'G1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const h1 = { id: 'H1', role: 'helicopter', x: 0, y: 20, z: 0, job: null, queue: [] };
  const p1 = { id: 'P1', role: 'fixed_wing', x: -100, y: 234, z: -100, job: null, queue: [] };
  const p2 = { id: 'P2', role: 'fixed_wing', x: 100, y: 234, z: -100, job: null, queue: [] };

  const units = [g1, h1, p1, p2];
  const res = dispatchFleetCommand({
    units,
    ids: ['G1', 'H1', 'P1', 'P2'],
    command: { type: 'move', point: { x: 200, z: 200 }, facing: 0 }, // 车头 +Z
    env: makeReadyEnv(),
    serial: 5,
  });
  assert.equal(res.accepted, 4);
  // facing=0 时地面纵向 -10m -> z=190；旋翼 +10m -> z=210
  assert.equal(g1.job.point.z, 190);
  assert.equal(h1.job.point.z, 210);
  // 两架固定翼相对错开 28m，并写入各自盘旋中心
  assert.equal(p1.job.point.x, 186);
  assert.equal(p2.job.point.x, 214);
  assert.deepEqual(p1.orbitCenter, { x: 186, z: 200 });
  assert.deepEqual(p2.orbitCenter, { x: 214, z: 200 });
});

test('06. Shift 追加三段队列保留顺序并以上一段终点作队形起点', () => {
  const g1 = { id: 'G1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const g2 = { id: 'G2', role: 'ground', x: 12, y: 0, z: 0, job: null, queue: [] };
  const env = makeReadyEnv();

  dispatchFleetCommand({ units: [g1, g2], ids: ['G1', 'G2'], command: { type: 'move', point: { x: 100, z: 0 } }, env, serial: 1 });
  dispatchFleetCommand({ units: [g1, g2], ids: ['G1', 'G2'], command: { type: 'move', point: { x: 200, z: 100 } }, append: true, env, serial: 2 });
  dispatchFleetCommand({ units: [g1, g2], ids: ['G1', 'G2'], command: { type: 'attackMove', point: { x: 300, z: 200 } }, append: true, env, serial: 3 });

  assert.equal(g1.queue.length, 2);
  assert.equal(g2.queue.length, 2);
  assert.equal(g1.queue[0].type, 'move');
  assert.equal(g1.queue[1].type, 'attackMove');
  // 新普通移动命令覆盖清空旧队列
  dispatchFleetCommand({ units: [g1, g2], ids: ['G1', 'G2'], command: { type: 'move', point: { x: 400, z: 0 } }, append: false, env, serial: 4 });
  assert.equal(g1.queue.length, 0);
  assert.equal(g1.job.batchId, 'cmd-4');
});

test('07. cancelFleetUnits 按兵种退场：地停/旋悬/固盘并清空队列', () => {
  const g1 = { id: 'G1', role: 'ground', x: 35, y: 1, z: 45, job: { id: 'a' }, queue: [{ id: 'b' }], waypoints: [[0, 0, 0]], targetFacing: 1 };
  const h1 = { id: 'H1', role: 'helicopter', x: 60, y: 22, z: -30, job: { id: 'c' }, queue: [{ id: 'd' }], waypoints: [[0, 22, 0]] };
  const p1 = { id: 'P1', role: 'fixed_wing', x: -320, y: 234, z: 410, orbitCenter: { x: 0, z: 0 }, job: { id: 'e' }, queue: [{ id: 'f' }], waypoints: [[0, 234, 0]] };

  const stopped = cancelFleetUnits({ units: [g1, h1, p1], ids: ['G1', 'H1', 'P1'] });
  assert.deepEqual(stopped, ['G1', 'H1', 'P1']);
  for (const u of [g1, h1, p1]) {
    assert.equal(u.job, null);
    assert.deepEqual(u.queue, []);
    assert.deepEqual(u.waypoints, []);
    assert.equal(u.commandStatus.status, 'stopped');
  }
  assert.deepEqual(p1.orbitCenter, { x: -320, z: 410 });
});

test('08. 出界与障碍拒收不换点并完整保留原任务与队列', () => {
  const env = makeReadyEnv({
    isPointBlocked: (x, z) => Math.hypot(x - 600, z - 600) <= 40,
  });
  const unit = { id: 'U1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  dispatchFleetCommand({ units: [unit], ids: ['U1'], command: { type: 'move', point: { x: 100, z: 100 } }, env, serial: 1 });
  dispatchFleetCommand({ units: [unit], ids: ['U1'], command: { type: 'move', point: { x: 180, z: 100 } }, append: true, env, serial: 2 });
  const savedJob = unit.job;
  const savedQueueLen = unit.queue.length;

  const outRes = dispatchFleetCommand({
    units: [unit],
    ids: ['U1'],
    command: { type: 'move', point: { x: 9999, z: 0 } },
    env,
    serial: 3,
  });
  assert.equal(outRes.accepted, 0);
  assert.equal(outRes.rejected, 1);
  assert.equal(unit.job, savedJob);
  assert.equal(unit.queue.length, savedQueueLen);
  assert.ok(unit.lastRejectedCommand?.reason.includes('边界'));

  const blkRes = dispatchFleetCommand({
    units: [unit],
    ids: ['U1'],
    command: { type: 'move', point: { x: 605, z: 605 } },
    env,
    serial: 4,
  });
  assert.equal(blkRes.accepted, 0);
  assert.equal(blkRes.rejected, 1);
  assert.equal(unit.job, savedJob);
  assert.ok(unit.lastRejectedCommand?.reason.includes('障碍'));
});

test('09. 无连通地面路线、逆行/攻地兵种受限检查与攻地意图保留原移动任务', () => {
  const g1 = { id: 'G1', role: 'ground', x: 0, y: 0, z: 0, job: { type: 'move', point: { x: 50, z: 0 } }, queue: [] };
  const h1 = { id: 'H1', role: 'helicopter', x: 0, y: 20, z: 0, job: null, queue: [] };
  const p1 = { id: 'P1', role: 'fixed_wing', x: 0, y: 234, z: 0, job: null, queue: [] };
  const env = makeReadyEnv({
    findGroundPath: () => ({ status: 'ready', found: false, reason: '海峡阻断无连通路线' }),
  });

  const noPath = dispatchFleetCommand({ units: [g1], ids: ['G1'], command: { type: 'move', point: { x: 200, z: 0 } }, env, serial: 1 });
  assert.equal(noPath.rejected, 1);
  assert.equal(noPath.rows[0].reason, '海峡阻断无连通路线');
  assert.equal(g1.job.point.x, 50);

  const revRes = dispatchFleetCommand({ units: [h1, p1], ids: ['H1', 'P1'], command: { type: 'reverse', point: { x: 50, z: 50 } }, env, serial: 2 });
  assert.equal(revRes.rejected, 2);

  const agRes = dispatchFleetCommand({ units: [g1, p1], ids: ['G1', 'P1'], command: { type: 'attackGround', point: { x: 220, z: 220 } }, env, serial: 3 });
  assert.equal(agRes.accepted, 1);
  assert.equal(agRes.rejected, 1);
  assert.ok(g1.lastAttackGroundIntent);
  assert.equal(g1.job.point.x, 50, '攻地占位应记录意图而不覆写当前移动任务');
});

test('10. 地形未就绪返回 pending 准备路径且不擅自落到其它点', () => {
  const unit = { id: 'G1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const env = makeReadyEnv({
    querySupport: () => ({ state: 'pending', height: null }),
  });
  const res = dispatchFleetCommand({ units: [unit], ids: ['G1'], command: { type: 'move', point: { x: 320, z: -240 } }, env, serial: 9 });
  assert.equal(res.accepted, 1);
  assert.equal(res.pending, 1);
  assert.equal(res.rows[0].status, 'pending');
  assert.equal(unit.commandStatus.status, 'pending');
  assert.deepEqual(unit.job.point, { x: 320, z: -240 });
});

test('11. >560m 远距离地面移动保持目标可达而不被局部 640m 寻路框截断拒收', () => {
  const unit = { id: 'G1', role: 'ground', x: -1000, y: 0, z: 0, job: null, queue: [] };
  let findCalled = 0;
  const env = makeReadyEnv({
    findGroundPath: () => {
      findCalled += 1;
      return { status: 'ready', found: false };
    },
  });
  const res = dispatchFleetCommand({ units: [unit], ids: ['G1'], command: { type: 'move', point: { x: 800, z: 0 } }, env, serial: 10 });
  assert.equal(res.accepted, 1);
  assert.equal(findCalled, 0);
  assert.equal(unit.waypoints.length, 2);
});

test('12. Tab / Shift+Tab 重叠点选循环引擎按候选顺序往返轮转', () => {
  const list = [{ id: 'US_M1A2_01' }, { id: 'US_AH64D_01' }, { id: 'US_A10C_01' }];
  assert.equal(cycleOverlappingUnits(list, null, false)?.id, 'US_M1A2_01');
  assert.equal(cycleOverlappingUnits(list, 'US_M1A2_01', false)?.id, 'US_AH64D_01');
  assert.equal(cycleOverlappingUnits(list, 'US_A10C_01', false)?.id, 'US_M1A2_01');
  assert.equal(cycleOverlappingUnits(list, 'US_M1A2_01', true)?.id, 'US_A10C_01');
  assert.equal(cycleOverlappingUnits(list, 'US_AH64D_01', true)?.id, 'US_M1A2_01');
});

test('13. 5 级 LIFO ESC 撤退状态机严格按 1->2->3->4->5 逐层退出与同步光标', () => {
  const escStack = createEscStack();
  let pauseOpen = true;
  let drawerOpen = true;
  let dragActive = true;
  const cursorLogs = [];

  // 先注册低层级：5 暂停 -> 4 抽屉
  escStack.register('pause-menu', () => pauseOpen, () => { pauseOpen = false; });
  escStack.register('drawer-panel', () => drawerOpen, () => { drawerOpen = false; });

  // 再接入交互仲裁器：自动注册 3 wheel -> 2 targeting/editor -> 1 transient drag
  const arbiter = createModeAndEscArbiter({
    escStack,
    hasActiveDrag: () => dragActive,
    cancelActiveDrag: () => { dragActive = false; },
    onCursorChange: (c, cls) => cursorLogs.push(`${c}:${cls}`),
  });

  arbiter.enterTargeting();
  assert.equal(arbiter.getMode(), INTERACTION_MODES.TARGETING);
  assert.equal(arbiter.getCursor(), 'attack');

  // 第 1 次 ESC：撤回进行中拖曳 (level 1)
  assert.equal(escStack.closeTop(), 'interaction-transient-drag');
  assert.equal(dragActive, false);
  assert.equal(arbiter.getMode(), INTERACTION_MODES.TARGETING);

  // 第 2 次 ESC：退出 targeting 回 play (level 2)
  assert.equal(escStack.closeTop(), 'interaction-submode');
  assert.equal(arbiter.getMode(), INTERACTION_MODES.PLAY);
  assert.equal(arbiter.getCursor(), 'default');

  // 打开 wheel 后再按 ESC：关闭 wheel (level 3)
  arbiter.toggleWheel();
  assert.equal(arbiter.getMode(), INTERACTION_MODES.WHEEL);
  assert.equal(escStack.closeTop(), 'interaction-wheel');
  assert.equal(arbiter.getMode(), INTERACTION_MODES.PLAY);

  // 第 4 次 ESC：收起抽屉 (level 4)
  assert.equal(escStack.closeTop(), 'drawer-panel');
  assert.equal(drawerOpen, false);
  assert.equal(pauseOpen, true);

  // 第 5 次 ESC：收起暂停菜单 (level 5)
  assert.equal(escStack.closeTop(), 'pause-menu');
  assert.equal(pauseOpen, false);

  // 空栈返回 null
  assert.equal(escStack.closeTop(), null);
  assert.ok(cursorLogs.includes(`attack:${CURSOR_CLASSES.attack}`));
  assert.ok(cursorLogs.includes(`default:${CURSOR_CLASSES.default}`));
});

test('14. 编辑器笔刷子模式激活与退出恢复默认光标', () => {
  const arbiter = createModeAndEscArbiter();
  arbiter.enterEditorBrush('raise_terrain');
  assert.equal(arbiter.getMode(), INTERACTION_MODES.EDITOR);
  assert.equal(arbiter.getActiveBrush(), 'raise_terrain');
  assert.equal(arbiter.getCursor(), 'deploy');
  arbiter.exitToPlay();
  assert.equal(arbiter.getMode(), INTERACTION_MODES.PLAY);
  assert.equal(arbiter.getActiveBrush(), null);
  assert.equal(arbiter.getCursor(), 'default');
});

test('15. resolveVehicleArchetype 准确映射全 12 类武器库兵种与通道', () => {
  const samples = [
    [{ slug: 'US_M1A2', category: 'ground' }, 'mbt', 'ground', 'ground'],
    [{ slug: 'US_LAV25', category: 'ground' }, 'ifv', 'ground', 'ground'],
    [{ slug: 'US_M1151_HMMWV', category: 'ground' }, 'recon', 'ground', 'ground'],
    [{ slug: 'US_M109A7_Paladin', category: 'ground' }, 'arty', 'ground', 'ground'],
    [{ slug: 'US_M270_MLRS', category: 'ground' }, 'arty', 'ground', 'ground'],
    [{ slug: 'US_Avenger', category: 'ground' }, 'aa', 'ground', 'ground'],
    [{ slug: 'US_LAV_AT', category: 'ground' }, 'atgm', 'ground', 'ground'],
    [{ slug: 'US_HEMTT_M977', category: 'ground' }, 'supply', 'ground', 'ground'],
    [{ slug: 'US_AH64D', category: 'air' }, 'attack_heli', 'heli', 'helicopter'],
    [{ slug: 'US_UH60M', category: 'air' }, 'trans_heli', 'heli', 'helicopter'],
    [{ slug: 'US_A10', category: 'air' }, 'cas', 'plane', 'fixed_wing'],
    [{ slug: 'US_F35A', category: 'air' }, 'fighter', 'plane', 'fixed_wing'],
    [{ slug: 'US_MQ9_Reaper', category: 'air' }, 'uav', 'plane', 'fixed_wing'],
  ];
  for (const [rec, iconKind, channel, role] of samples) {
    const info = resolveVehicleArchetype(rec);
    assert.equal(info.iconKind, iconKind, `${rec.slug} iconKind`);
    assert.equal(info.channel, channel, `${rec.slug} channel`);
    assert.equal(info.role, role, `${rec.slug} role`);
    assert.ok(info.speed > 0 && info.ringRadius > 0 && info.pickRadius > 0);
  }
});

test('16. isUnitAllowedByChannel 与 alive/hp 过滤语义正确', () => {
  const g = { id: '1', role: 'ground', alive: true, hp: 100 };
  const h = { id: '2', role: 'helicopter', alive: true, hp: 100 };
  const p = { id: '3', role: 'fixed_wing', alive: true, hp: 100 };
  const dead = { id: '4', role: 'ground', alive: false, hp: 0 };

  assert.equal(isUnitAllowedByChannel(g, 'all'), true);
  assert.equal(isUnitAllowedByChannel(dead, 'all'), false);
  assert.equal(isUnitAllowedByChannel(g, 'ground'), true);
  assert.equal(isUnitAllowedByChannel(h, 'ground'), false);
  assert.equal(isUnitAllowedByChannel(h, 'heli'), true);
  assert.equal(isUnitAllowedByChannel(p, 'plane'), true);
  assert.equal(isUnitAllowedByChannel(h, 'air'), true);
  assert.equal(isUnitAllowedByChannel(p, 'air'), true);
  assert.equal(isUnitAllowedByChannel(g, 'air'), false);
});

test('17. wrapAngleRad 正确归一化超范围角至 [-PI, PI]', () => {
  assert.ok(Math.abs(wrapAngleRad(Math.PI * 3) - Math.PI) < 1e-9);
  assert.ok(Math.abs(wrapAngleRad(-Math.PI * 2.5) + Math.PI * 0.5) < 1e-9);
});

test('18. 未知命令/无效坐标拒收并返回明确中文错误原因', () => {
  const unit = { id: 'U1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const res = dispatchFleetCommand({
    units: [unit],
    ids: ['U1'],
    command: { type: 'move', point: { x: Number.NaN, z: 10 } },
    env: makeReadyEnv(),
  });
  assert.equal(res.accepted, 0);
  assert.equal(res.rejected, 1);
  assert.equal(unit.commandStatus.status, 'rejected');
  assert.ok(unit.lastRejectedCommand?.reason.includes('无效'));
});

test('19. resolveZoomIconTier 严格按 L0(<=260) / L1(260~650) / L2(650~2200) / L3(>2200) 分级与尺寸映射', () => {
  assert.equal(resolveZoomIconTier(65), 'none');
  assert.equal(resolveZoomIconTier(135), 'none');
  assert.equal(resolveZoomIconTier(260), 'none');
  assert.equal(resolveZoomIconTier(260.1), 'compact');
  assert.equal(resolveZoomIconTier(460), 'compact');
  assert.equal(resolveZoomIconTier(650), 'compact');
  assert.equal(resolveZoomIconTier(650.1), 'chip');
  assert.equal(resolveZoomIconTier(1200), 'chip');
  assert.equal(resolveZoomIconTier(2200), 'chip');
  assert.equal(resolveZoomIconTier(2200.1), 'overview');
  assert.equal(resolveZoomIconTier(4500), 'overview');
  assert.equal(resolveZoomIconTier(9000), 'overview');
  assert.deepEqual(getZoomTierIconSize('none'), { w: 0, h: 0 });
  assert.deepEqual(getZoomTierIconSize('compact'), { w: 22, h: 16 });
  assert.deepEqual(getZoomTierIconSize('chip'), { w: 26, h: 18 });
  assert.deepEqual(getZoomTierIconSize('overview'), { w: 18, h: 14 });
});

test('20. clusterScreenUnits 同通道 >=3 密集聚合且跨通道/不足3辆绝不误聚', () => {
  const items = [
    // 3 辆紧邻地面单位 -> 聚为 1 簇（主兵种 mbt，含 1 辆选中）
    { id: 'G1', channel: 'ground', iconKind: 'mbt', x: 100, y: 100, hp: 90, selected: true, alive: true },
    { id: 'G2', channel: 'ground', iconKind: 'mbt', x: 108, y: 104, hp: 80, selected: false, alive: true },
    { id: 'G3', channel: 'ground', iconKind: 'ifv', x: 104, y: 112, hp: 100, selected: false, alive: true },
    // 2 辆紧邻旋翼（<3 辆）-> 保持独立，不聚合
    { id: 'H1', channel: 'heli', iconKind: 'attack_heli', x: 102, y: 103, hp: 100, selected: false, alive: true },
    { id: 'H2', channel: 'heli', iconKind: 'trans_heli', x: 106, y: 105, hp: 100, selected: false, alive: true },
    // 1 架固定翼同位置 -> 跨通道不聚合
    { id: 'P1', channel: 'plane', iconKind: 'cas', x: 104, y: 104, hp: 100, selected: false, alive: true },
  ];
  const nodes = clusterScreenUnits(items, { radiusPx: 18, minClusterSize: 3 });
  const clusters = nodes.filter(n => n.isCluster);
  const singles = nodes.filter(n => !n.isCluster);
  assert.equal(clusters.length, 1);
  assert.equal(singles.length, 3);
  assert.equal(clusters[0].channel, 'ground');
  assert.equal(clusters[0].dominantKind, 'mbt');
  assert.equal(clusters[0].count, 3);
  assert.equal(clusters[0].selectedCount, 1);
  assert.deepEqual(clusters[0].unitIds, ['G1', 'G2', 'G3']);
});

test('21. sampleGroundOrbitPoints 与 computeGroundFootProjection 精确贴合起伏地形与圆周半径', () => {
  const terrainFn = (x, z) => 2.5 + 0.04 * x - 0.02 * z;
  const center = { x: 100, z: -60 };
  const radius = 85;
  const pts = sampleGroundOrbitPoints(center, radius, terrainFn, 56, 0.15);
  assert.equal(pts.length, 57);
  for (const p of pts) {
    const dist = Math.hypot(p.x - center.x, p.z - center.z);
    assert.ok(Math.abs(dist - radius) < 1e-6, `贴地圆周点距离偏差 ${dist}`);
    const expectedY = terrainFn(p.x, p.z) + 0.15;
    assert.ok(Math.abs(p.y - expectedY) < 1e-6, `贴地圆周高程偏差 ${p.y} vs ${expectedY}`);
  }
  const foot = computeGroundFootProjection({ x: center.x + radius, y: 234, z: center.z }, terrainFn, 0.15);
  assert.ok(Math.abs(foot.y - (terrainFn(center.x + radius, center.z) + 0.15)) < 1e-6);
});

test('22. computeGroundTerrainTilt 4 点差分贴坡方向正确且严格限幅 ±22°/±18°', () => {
  const flat = computeGroundTerrainTilt({ heightAt: () => 5.0 }, 0, 0, 0);
  assert.equal(flat.pitch, 0);
  assert.equal(flat.roll, 0);

  // yaw = 0 时前方为 +Z、左侧为 +X；前方高于后方（上坡）应返回负 pitch 抬头，左侧高于右侧应返回正 roll
  const uphillLeftHigh = computeGroundTerrainTilt({ heightAt: (x, z) => z * 0.2 + x * 0.15 }, 0, 0, 0, 2.4, 1.4);
  assert.ok(uphillLeftHigh.pitch < -0.15 && uphillLeftHigh.pitch > -22 * Math.PI / 180);
  assert.ok(uphillLeftHigh.roll > 0.10 && uphillLeftHigh.roll < 18 * Math.PI / 180);

  // 极端陡崖限幅保护
  const cliff = computeGroundTerrainTilt({ heightAt: (x, z) => z * 50 + x * 50 }, 0, 0, 0, 2.4, 1.4);
  assert.ok(Math.abs(cliff.pitch + (22 * Math.PI / 180)) < 1e-6);
  assert.ok(Math.abs(cliff.roll - (18 * Math.PI / 180)) < 1e-6);
});

test('23. computeAircraftBankRoll 与 stepVehicleAnimations 刚体姿态及部件角更新正确', () => {
  const steadyBank = computeAircraftBankRoll(0.42, 1.65, 46);
  assert.ok(Math.abs(steadyBank) >= (35 * Math.PI) / 180 && Math.abs(steadyBank) <= (46 * Math.PI) / 180 + 1e-9);
  assert.ok(Math.abs(computeAircraftBankRoll(5.0, 1.65, 46) + (46 * Math.PI) / 180) < 1e-9);

  const gUnit = {
    id: 'US_M1A2_01', role: 'ground', iconKind: 'mbt', speed: 14, groundGuard: true,
    x: 0, y: 0, z: 0, yaw: 0,
    lastAttackGroundIntent: { point: { x: 100, z: 0 } },
    rig: { turretPivot: { rotation: { y: 0 } }, barrelPivot: { rotation: { x: 0 } }, rigBodyGroup: { position: { y: 0 }, rotation: { x: 0, z: 0 } } },
  };
  const hUnit = {
    id: 'US_AH64D_01', role: 'helicopter', iconKind: 'attack_heli', speed: 26,
    x: 20, y: 24, z: 0, yaw: 0,
    rig: { mainRotorPivot: { rotation: { y: 0 } }, tailRotorPivot: { rotation: { x: 0 } }, tailAxis: 'x', rigBodyGroup: { position: { y: 0 }, rotation: { x: 0, z: 0 } } },
  };
  const pUnit = {
    id: 'US_A10C_01', role: 'fixed_wing', iconKind: 'cas', speed: 52,
    x: 45, y: 234, z: 85, yaw: 0,
    rig: { rigBodyGroup: { position: { y: 0 }, rotation: { x: 0, z: 0 } } },
  };
  const sup = { heightAt: () => 0 };
  for (let i = 0; i < 15; i += 1) {
    pUnit.yaw += 0.42 * 0.03;
    hUnit.x += 0.4;
    stepVehicleAnimations([gUnit, hUnit, pUnit], { dt: 0.03, nowSec: i * 0.03, support: sup });
  }
  assert.ok(gUnit.animState.turretYaw > 0.6, `炮塔应向右方(+X)攻地目标平滑旋转，实际 ${gUnit.animState.turretYaw}`);
  assert.ok(gUnit.animState.barrelPitch > 0.08, `炮管应随攻地意图微仰角，实际 ${gUnit.animState.barrelPitch}`);
  assert.ok(hUnit.rig.mainRotorPivot.rotation.y > 0.5, '直升机主旋翼应持续高速转动');
  assert.ok(Math.abs(hUnit.animState.heaveY) > 0 || hUnit.animState.pitch > 0, '直升机应有悬停呼吸或前倾姿态');
  assert.ok(Math.abs(pUnit.animState.bankRoll) > 0.35, `固定翼盘旋应压出明显滚转坡度，实际 ${pUnit.animState.bankRoll}`);
});

test('24. 载具空间引擎音效纯规则：span 视距衰减、状态调制、8 声部配额与三兵种保底', () => {
  assert.equal(DEFAULT_UI_PREFS.vehicleAudioEnabled, true);
  assert.equal(DEFAULT_UI_PREFS.vehicleAudioVolume, 0.45);
  assert.equal(computeSpanAudioGain(135), 1.0);
  assert.equal(computeSpanAudioGain(350), 1.0);
  assert.ok(Math.abs(computeSpanAudioGain(1200) - 0.30) < 1e-6);
  assert.equal(computeSpanAudioGain(2000), 0.0);
  assert.equal(computeSpanAudioGain(2500), 0.0);

  const gIdle = computeEngineMotionState({ role: 'ground', waypoints: [], wpIndex: 0 });
  const gMove = computeEngineMotionState({ role: 'ground', waypoints: [[0, 0, 10]], wpIndex: 0 });
  assert.ok(gMove.gain > gIdle.gain && gMove.rate > gIdle.rate);

  const pCruise = computeEngineMotionState({ role: 'fixed_wing', waypoints: [], wpIndex: 0 });
  assert.ok(pCruise.filterHz <= 4000, '高空固定翼应启用柔和低通滤波');

  const fleet = [];
  for (let i = 0; i < 12; i += 1) {
    fleet.push({ id: `G_${i}`, role: 'ground', x: 10 + i * 15, z: 0 });
  }
  fleet.push({ id: 'H_far', role: 'helicopter', x: 640, z: 0 });
  fleet.push({ id: 'P_far', role: 'fixed_wing', x: 660, z: 0 });
  const picked = chooseSpatialEngines(fleet, new Set(['G_11']), { x: 0, z: 0 }, new Set(), 8);
  assert.equal(picked.length, 8);
  assert.ok(picked.some(u => u.id === 'H_far'), '三兵种保底应保留最近旋翼代表');
  assert.ok(picked.some(u => u.id === 'P_far'), '三兵种保底应保留最近固定翼代表');
  assert.ok(picked.some(u => u.id === 'G_11'), '选中单位应获得 120m 优先加权');
});

test('25. projectAirAnchorToGroundFocus 在 0 / PI/2 / PI 旋转角正交视线投影闭环误差 < 1e-4', () => {
  const heightAt = () => 12.0;
  // 地面单位 h0 <= 0.5 不做前向偏移
  const gProj = projectAirAnchorToGroundFocus({ x: -38, y: 12.2, z: 85 }, 0, heightAt);
  assert.equal(gProj.offsetForward, 0);
  assert.equal(gProj.x, -38);
  assert.equal(gProj.z, 85);

  // 直升机 (34m) 与固定翼 (234m) 在 0, PI/2, PI 三个旋转角下正交投影回屏幕中心误差 < 1e-4
  for (const unitPos of [
    { x: -55, y: 34.0, z: 102 },
    { x: 42, y: 234.0, z: -60 },
  ]) {
    for (const angle of [0, Math.PI * 0.5, Math.PI]) {
      const focus = projectAirAnchorToGroundFocus(unitPos, angle, heightAt);
      const gy = heightAt(focus.x, focus.z);
      const { rightX, rightZ, forwardX, forwardZ } = computeCameraAxes(angle);
      // 镜头视线方向向量 V = (forwardX * cos(TILT), -sin(TILT), forwardZ * cos(TILT))
      // 单位相对地面焦点向量 D = (unitPos.x - focus.x, unitPos.y - gy, unitPos.z - focus.z)
      const dx = unitPos.x - focus.x;
      const dy = unitPos.y - gy;
      const dz = unitPos.z - focus.z;
      // 正交相机水平屏幕 X 坐标 = D · Right
      const screenRightErr = Math.abs(dx * rightX + dz * rightZ);
      // 正交相机垂直屏幕 Y 坐标 = D · Up_cam = (D · Forward) * sin(TILT) + dy * cos(TILT)
      const dFwd = dx * forwardX + dz * forwardZ;
      const screenUpErr = Math.abs(dFwd * Math.sin(CAMERA_TILT_RAD) + dy * Math.cos(CAMERA_TILT_RAD));
      assert.ok(screenRightErr < 1e-4, `angle=${angle} 水平投影偏差过大: ${screenRightErr}`);
      assert.ok(screenUpErr < 1e-4, `angle=${angle} 垂直投影偏差过大: ${screenUpErr}`);
    }
  }
});

test('26. isFixedWingOrbiting 严格区分固定翼直线飞行与绕圈盘旋态', () => {
  const oc = { x: -40, z: 85 };
  // 1) 盘旋态：无未完航点且处于圆周半径 R=85m 附近
  const orbitingUnit = {
    role: 'fixed_wing',
    orbitCenter: oc,
    orbitRadius: 85,
    waypoints: [],
    wpIndex: 0,
  };
  assert.equal(isFixedWingOrbiting(orbitingUnit, { x: -40 + 85, y: 234, z: 85 }), true);

  // 2) 直线飞行切入态（尚有未完成航点）
  const flyingWithWp = {
    ...orbitingUnit,
    waypoints: [{ x: 300, y: 234, z: 300 }],
    wpIndex: 0,
  };
  assert.equal(isFixedWingOrbiting(flyingWithWp, { x: -40 + 85, y: 234, z: 85 }), false);

  // 3) 航点刚清空但机身距离新盘旋圆周仍远 (> max(14, 85*0.25)=21.25m)
  assert.equal(isFixedWingOrbiting(orbitingUnit, { x: 180, y: 234, z: 260 }), false);

  // 4) 非固定翼永远返回 false
  assert.equal(isFixedWingOrbiting({ ...orbitingUnit, role: 'helicopter' }, { x: -40 + 85, y: 22, z: 85 }), false);
});

test('27. findLocalPath4m OBB 反向光栅化 + 最小二叉堆 + 共线抽稀 + 动态 OBB 阻断', () => {
  const engine = new AutoPhysicsAndNavEngine({
    b3Manifest: {
      building_prototypes: [
        { id: 'test_wall', prototype_index: 0, bounds_min: [-12, 0, -4], bounds_max: [12, 16, 4] },
      ],
    },
    b2Manifest: { tree_prototypes: [] },
    buildingIndex: { gridX: 1, gridZ: 1, minX: -500, minZ: -500, chunkM: 1000, cellOffsets: new Uint32Array(1), cellCounts: new Uint32Array(1), recordsView: new DataView(new ArrayBuffer(16)) },
    collisionIndex: { gridX: 1, gridZ: 1, minX: -500, minZ: -500, chunkM: 1000, cellOffsets: new Uint32Array(1), cellCounts: new Uint32Array(1), recordsView: new DataView(new ArrayBuffer(16)) },
    treeIndex: { gridX: 1, gridZ: 1 },
    support: {
      query: () => ({ state: 'ready', height: 0, waterDepth: 0, role: 1 }),
      heightAt: () => 0,
    },
  });

  // 1) 无障碍直线 160m：共线抽稀后航点数远小于原始 40 格
  const freeNav = engine.findLocalPath4m(-80, 0, 80, 0, 5.5);
  assert.equal(freeNav.found, true);
  assert.ok(freeNav.expanded > 0);
  assert.ok(freeNav.waypoints.length <= 6, `无障碍共线抽稀应大幅合并冗余中间点，实际 ${freeNav.waypoints.length}`);

  // 2) 中途放置动态建筑阻挡直线：自动绕行且路径不穿过 OBB
  engine.addedInstances.set('blocker_1', {
    id: 'blocker_1',
    prototype_id: 'test_wall',
    obb: {
      center: [0, 0, 0],
      halfExtents: [6, 8, 14],
      axisX: [1, 0],
      axisZ: [0, 1],
      aabb: [-6, 0, -14, 6, 16, 14],
    },
  });
  const detourNav = engine.findLocalPath4m(-45, 0, 45, 0, 5.5);
  assert.equal(detourNav.found, true);
  assert.ok(detourNav.waypoints.some(p => Math.abs(p[2]) > 14), '动态 OBB 阻挡直线时应自动绕行');
});

test('28. DEPLOY 落点部署模式与 Esc 第 2 级仲裁及 plannedRows 直传复用', () => {
  const escStack = createEscStack();
  let drawerOpen = true;
  escStack.register('deploy-drawer', () => drawerOpen, () => { drawerOpen = false; });

  const arbiter = createModeAndEscArbiter({ escStack });
  arbiter.enterDeployMode('US_M2A3');
  assert.equal(arbiter.getMode(), INTERACTION_MODES.DEPLOY);
  assert.equal(arbiter.getDeploySlug(), 'US_M2A3');
  assert.equal(arbiter.getCursor(), 'deploy');

  // 第 1 次 Esc：退出 DEPLOY 落点子模式回到 PLAY，部署抽屉保持开启
  assert.equal(escStack.closeTop(), 'interaction-submode');
  assert.equal(arbiter.getMode(), INTERACTION_MODES.PLAY);
  assert.equal(arbiter.getDeploySlug(), null);
  assert.equal(drawerOpen, true);

  // 第 2 次 Esc：关闭部署抽屉
  assert.equal(escStack.closeTop(), 'deploy-drawer');
  assert.equal(drawerOpen, false);

  // dispatchFleetCommand 传入 plannedRows 时不再触发 env.findGroundPath
  let pathCallCount = 0;
  const env = makeReadyEnv({
    findGroundPath: () => {
      pathCallCount += 1;
      return { found: true, waypoints: [[0, 0, 0], [30, 0, 40]] };
    },
  });
  const u = { id: 'M1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const previewRows = planFleetCommand({
    units: [u],
    ids: ['M1'],
    command: { type: 'move', point: { x: 30, z: 40 } },
    append: false,
    env,
  });
  assert.equal(pathCallCount, 1);

  const res = dispatchFleetCommand({
    units: [u],
    ids: ['M1'],
    command: { type: 'move', point: { x: 30, z: 40 } },
    append: false,
    env,
    plannedRows: previewRows,
  });
  assert.equal(res.accepted, 1);
  assert.equal(pathCallCount, 1, '传入 plannedRows 提交时应零重复调用 findGroundPath');
});

test('29. full 全屏自适应 680p 渲染基线与 16:9 -> 21:9 -> full -> 16:9 三态循环', () => {
  assert.deepEqual(ASPECT_CYCLE, ['16:9', '21:9', 'full']);
  assert.equal(nextAspectKey('16:9'), '21:9');
  assert.equal(nextAspectKey('21:9'), 'full');
  assert.equal(nextAspectKey('full'), '16:9');

  const spec1080 = getStandardAspectSpec('full', 1920, 1080);
  assert.equal(spec1080.key, 'full');
  assert.equal(spec1080.height, 680);
  assert.equal(spec1080.width % 2, 0);
  assert.equal(spec1080.width, 1208);

  const specUw = getStandardAspectSpec('full', 2560, 1080);
  assert.equal(specUw.height, 680);
  assert.equal(specUw.width % 2, 0);
  assert.equal(specUw.width, 1612);
});

test('30. 5 大分类独立部署配额 (MAX_HERO_UNITS=50)：固定翼满 6 架不影响重甲/旋翼继续部署', () => {
  assert.equal(MAX_HERO_UNITS, 50);
  assert.deepEqual(DEPLOY_CATEGORY_QUOTAS, {
    armor: 12,
    recon_ifv: 16,
    support: 8,
    heli: 8,
    plane: 6,
  });
  assert.equal(resolveDeployCategory({ role: 'ground', iconKind: 'mbt' }), 'armor');
  assert.equal(resolveDeployCategory({ role: 'ground', iconKind: 'ifv' }), 'recon_ifv');
  assert.equal(resolveDeployCategory({ role: 'ground', iconKind: 'arty' }), 'support');
  assert.equal(resolveDeployCategory({ role: 'helicopter', iconKind: 'attack_heli' }), 'heli');
  assert.equal(resolveDeployCategory({ role: 'fixed_wing', iconKind: 'cas' }), 'plane');

  const units = [];
  for (let i = 0; i < 6; i += 1) {
    units.push({ id: `P_${i}`, role: 'fixed_wing', iconKind: 'fighter' });
  }
  units.push({ id: 'G_1', role: 'ground', iconKind: 'mbt' });
  units.push({ id: 'H_1', role: 'helicopter', iconKind: 'attack_heli' });

  const summary = computeDeployQuotaSummary(units);
  assert.equal(summary.totalCount, 8);
  assert.equal(summary.maxTotal, 50);
  assert.equal(summary.isTotalFull, false);
  assert.equal(summary.categories.plane.count, 6);
  assert.equal(summary.categories.plane.isFull, true);
  assert.equal(summary.categories.armor.count, 1);
  assert.equal(summary.categories.armor.isFull, false);
  assert.equal(summary.categories.heli.count, 1);
  assert.equal(summary.categories.heli.isFull, false);
});

test('31. followModeActive 持久跟随开关与切换选中单位自动接力跟随', () => {
  const units = [
    { id: 'U1', shortName: 'M1A2', role: 'ground', channel: 'ground', alive: true },
    { id: 'U2', shortName: 'AH-64D', role: 'helicopter', channel: 'heli', alive: true },
    { id: 'U3', shortName: 'A-10C', role: 'fixed_wing', channel: 'plane', alive: true },
  ];
  let selectedIds = new Set(['U1']);
  let primaryId = 'U1';
  let followId = null;

  const adapter = {
    getUnits: () => units,
    getUnitById: id => units.find(u => u.id === id) || null,
    getSelection: () => ({ ids: [...selectedIds], primaryId }),
    setSelection: (ids, pId) => {
      selectedIds = new Set(ids);
      primaryId = pId || ids[0] || null;
      return { ids: [...selectedIds], primaryId };
    },
    setHoverUnit: () => {},
  };
  const cameraCtrl = {
    getFollowUnitId: () => followId,
    setFollowUnit: id => { followId = id || null; },
    stopFollowing: () => { followId = null; },
    flyTo: () => {},
  };
  const selCtrl = createSelectionAndGroupController({ adapter, cameraCtrl });
  selCtrl.setSelection(['U1']);

  // 1) 开启跟随 -> 锁定 U1
  assert.equal(selCtrl.toggleFollow(), 'U1');
  assert.equal(selCtrl.isFollowModeActive(), true);
  assert.equal(followId, 'U1');

  // 2) 切换选中 U2 -> 保持跟随模式开启并自动接力到 U2
  selCtrl.setSelection(['U2']);
  assert.equal(selCtrl.isFollowModeActive(), true);
  assert.equal(followId, 'U2');

  // 3) 多选 ['U2', 'U3'] -> 保持跟随模式并跟随主焦点
  selCtrl.setSelection(['U2', 'U3']);
  assert.equal(selCtrl.isFollowModeActive(), true);
  assert.equal(followId, primaryId);

  // 4) 相机主动平移打断跟随 -> 同步关闭 followModeActive
  selCtrl.notifyCameraFollowStopped();
  assert.equal(selCtrl.isFollowModeActive(), false);

  // 5) 再次开启跟随，点空地清空选区 -> 自动退出跟随
  selCtrl.setSelection(['U2']);
  selCtrl.toggleFollow();
  assert.equal(selCtrl.isFollowModeActive(), true);
  selCtrl.setSelection([]);
  assert.equal(selCtrl.isFollowModeActive(), false);
  assert.equal(followId, null);
});

test('32. resolveRouteStyle 路径线透明化分层 (选中 0.62 / 未选中 0.28 / 预览 0.76 / softOutline)', () => {
  const selStyle = resolveRouteStyle('mbt', 'committed', true);
  assert.equal(selStyle.alpha, 0.62);
  assert.equal(selStyle.softOutline, 'rgba(8, 10, 16, 0.42)');

  const unselStyle = resolveRouteStyle('mbt', 'committed', false);
  assert.equal(unselStyle.alpha, 0.28);

  const previewStyle = resolveRouteStyle('attack_heli', 'preview', true);
  assert.equal(previewStyle.alpha, 0.76);

  const pendingStyle = resolveRouteStyle('mbt', 'pending', true);
  assert.equal(pendingStyle.alpha, 0.78);

  const rejectedStyle = resolveRouteStyle('mbt', 'rejected', true);
  assert.equal(rejectedStyle.alpha, 0.90);
});

test('33. 持续跟随新规则：WASD / 中键平移 / Q·E 旋转均保持跟随并累加 followOffset，按 F 归中，仅 G/R/清选区退出', () => {
  const unitPos = { x: 0, y: 0, z: 85, role: 'ground' };
  const state = {
    center: { x: 0, y: 0, z: 85 },
    span: 160,
    angle: 0,
    forcedAspect: 16 / 9,
    paused: false,
  };
  const camera = {
    left: 0, right: 0, top: 0, bottom: 0,
    position: { set() {} },
    updateProjectionMatrix() {},
    lookAt() {},
    updateMatrixWorld() {},
  };
  const camCtrl = createCameraController({
    host: { clientWidth: 1600, clientHeight: 900 },
    camera,
    sun: null,
    state,
    support: { heightAt: () => 0 },
    computeHorPlusCameraFrustum,
  });
  camCtrl.setUnitAnchorProvider(id => (id === 'U1' ? { ...unitPos } : null));

  // 1) 开启跟随 U1
  assert.equal(camCtrl.setFollowUnit('U1'), 'U1');
  assert.deepEqual(camCtrl.getFollowOffset(), { x: 0, z: 0 });

  // 2) WASD (W 键) 平移 -> 绝不退出跟随，followOffset 非零
  camCtrl.step(50, 100, new Set(['w']));
  assert.equal(camCtrl.getFollowUnitId(), 'U1');
  const offAfterW = camCtrl.getFollowOffset();
  assert.ok(Math.hypot(offAfterW.x, offAfterW.z) > 1.0, `按 W 平移后应累加非零 followOffset，实际 ${JSON.stringify(offAfterW)}`);

  // 3) 中键拖拽 panByPixels + Q/E 旋转 -> 绝不退出跟随，offset 继续更新且 yaw 变化
  camCtrl.panByPixels(-60, 40);
  camCtrl.rotateByPixels(-100);
  camCtrl.step(50, 150, new Set(['q']));
  assert.equal(camCtrl.getFollowUnitId(), 'U1');
  const offAfterPan = camCtrl.getFollowOffset();
  assert.ok(Math.hypot(offAfterPan.x - offAfterW.x, offAfterPan.z - offAfterW.z) > 0.5);
  assert.ok(Math.abs(camCtrl.camTarget.angle) > 0.1);

  // 4) 单位继续移动 -> 相机目标保持相对偏移同步随车前进
  const targetBeforeMove = { x: camCtrl.camTarget.x, z: camCtrl.camTarget.z };
  unitPos.x += 40;
  camCtrl.step(50, 200, new Set());
  assert.equal(camCtrl.getFollowUnitId(), 'U1');
  assert.ok(camCtrl.camTarget.x - targetBeforeMove.x > 35, '单位移动时跟随目标应保留 offset 同步跟进');

  // 5) resetFollowOffset (按 F 归中) -> followOffset 归零但仍保持跟随
  assert.equal(camCtrl.resetFollowOffset(), true);
  assert.equal(camCtrl.getFollowUnitId(), 'U1');
  assert.deepEqual(camCtrl.getFollowOffset(), { x: 0, z: 0 });

  // 6) R 键复位 (flyTo 默认 keepFollow=false) -> 正式退出跟随
  camCtrl.flyTo(0, 85, 135, 0);
  assert.equal(camCtrl.getFollowUnitId(), null);
});

test('34. 方案 B 卡片坞同型聚合与 1:1 战术雷达 9KM/3KM 正逆坐标闭环', () => {
  const units = [
    { id: 'G1', slug: 'US_M1A2', modelCode: 'M1A2 SEP v3', shortName: 'M1A2 SEP v3', role: 'ground', channel: 'ground', iconKind: 'mbt', hp: 100, y: 0 },
    { id: 'G2', slug: 'US_PATRIOT_PAC3MSE', modelCode: 'Patriot PAC-3', shortName: 'Patriot PAC-3', role: 'ground', channel: 'ground', iconKind: 'aa', hp: 100, y: 0 },
    { id: 'P1', slug: 'US_A10', modelCode: 'A-10C', shortName: 'A-10C #01', role: 'fixed_wing', channel: 'plane', iconKind: 'cas', hp: 100, y: 234 },
    { id: 'P2', slug: 'US_A10', modelCode: 'A-10C', shortName: 'A-10C #02', role: 'fixed_wing', channel: 'plane', iconKind: 'cas', hp: 80, y: 234 },
    { id: 'P3', slug: 'US_A10', modelCode: 'A-10C', shortName: 'A-10C #03', role: 'fixed_wing', channel: 'plane', iconKind: 'cas', hp: 90, y: 234 },
    { id: 'H1', slug: 'US_AH64D', modelCode: 'AH-64D', shortName: 'AH-64D', role: 'helicopter', channel: 'heli', iconKind: 'attack_heli', hp: 95, y: 22 },
  ];

  const allGroups = aggregateDeckUnitsBySlug(units, { filterTab: 'all', selectedIds: new Set(['P2']) });
  assert.equal(allGroups.length, 4);
  const a10Grp = allGroups.find(g => g.slug === 'US_A10');
  assert.equal(a10Grp.count, 3);
  assert.equal(a10Grp.avgHp, 90);
  assert.equal(a10Grp.anySelected, true);
  assert.equal(a10Grp.name, 'A-10C');
  const patGrp = allGroups.find(g => g.slug === 'US_PATRIOT_PAC3MSE');
  assert.equal(patGrp.name, 'Patriot PAC-3', '型号尾数 PAC-3 不得被误切');
  assert.equal(filterDeckUnits(units, { filterTab: 'plane' }).length, 3);

  const cg1Groups = aggregateDeckUnitsBySlug(units, { filterTab: 'cg1', controlGroups: { 1: ['G1', 'H1'] } });
  assert.equal(cg1Groups.length, 2);
  assert.deepEqual(cg1Groups.map(g => g.slug), ['US_M1A2', 'US_AH64D']);

  // 9KM 与 3KM 视窗边界夹紧与像素<->世界坐标正逆映射误差 < 1e-6
  const vp9 = computeMinimapViewport('9km', 0, 85);
  assert.equal(vp9.viewRangeM, 9000);
  const p9 = worldToMinimapPx(1234, -890, vp9, 204, 204);
  const w9 = minimapPxToWorld(p9.x, p9.y, vp9, 204, 204);
  assert.ok(Math.hypot(w9.x - 1234, w9.z + 890) < 1e-6);

  const vp3 = computeMinimapViewport('3km', 4400, 4700);
  assert.equal(vp3.viewRangeM, 3000);
  assert.ok(vp3.maxX <= 4575 + 1e-6 && vp3.maxZ <= 4850 + 1e-6, '3KM 局部视窗应严格夹紧在 9km 地图边界内');
  const p3 = worldToMinimapPx(3800, 4100, vp3, 204, 204);
  const w3 = minimapPxToWorld(p3.x, p3.y, vp3, 204, 204);
  assert.ok(Math.hypot(w3.x - 3800, w3.z - 4100) < 1e-6);
});

test('35. 战术雷达军图轮廓重构纯规则：9KM/3KM 建筑分级降噪、地貌分色提纯与道路世界矩阵投影裁剪', () => {
  // 1) 建筑分级降噪：大型机库进双缓存、小型民房仅进 3KM、微小装饰物全过滤
  assert.deepEqual(classifyBuildingRadarTier(28, 18, 9), { tier9km: true, tier3km: true });
  assert.deepEqual(classifyBuildingRadarTier(8, 6, 3.2), { tier9km: false, tier3km: true });
  assert.deepEqual(classifyBuildingRadarTier(3, 3, 1.5), { tier9km: false, tier3km: false });

  // 2) 地貌分色提纯：林地绿通道占优，停机坪水泥偏亮中性灰，农田偏暗橄榄褐
  const forest = stylizeTacticalTerrainRgb(65, 118, 60);
  assert.ok(forest[1] > forest[0] + 15 && forest[1] > forest[2], `林地应呈沉稳军图深青绿，实际 ${forest}`);
  const apron = stylizeTacticalTerrainRgb(168, 166, 164);
  assert.ok(apron[0] >= 90 && Math.abs(apron[0] - apron[2]) <= 14, `停机坪应呈清晰中灰，实际 ${apron}`);
  const soil = stylizeTacticalTerrainRgb(140, 120, 88);
  assert.ok(soil[0] > soil[2] && soil[1] > soil[2], `农田/裸土应呈低饱和暗橄榄褐，实际 ${soil}`);

  // 3) 道路世界矩阵投影与 9km 边界悬空三角形自动裁剪
  const pos = new Float32Array([
    0, 0, 0,
    90, 0, 0,
    90, 0, 90,
    12000, 0, 12000,
    12100, 0, 12000,
    12100, 0, 12100,
  ]);
  const matTranslate = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    75, 0, 350, 1,
  ];
  const tris = extractRoadTriangles2D(pos, null, matTranslate, { minX: -4425, maxX: 4575, minZ: -4150, maxZ: 4850 }, 900);
  assert.equal(tris.length, 6, '越界悬空三角形应被裁剪，仅保留界内 1 个三角形（6 个 2D 坐标）');
  // (0+75 - (-4425)) * (900 / 9000) = 4500 * 0.1 = 450
  assert.ok(Math.abs(tris[0] - 450) < 1e-4 && Math.abs(tris[1] - 450) < 1e-4);
  assert.ok(Math.abs(tris[2] - 459) < 1e-4 && Math.abs(tris[5] - 459) < 1e-4);

  // 4) 纯 TypedArray 道路三角形掩码光栅化（快速点亮且绝不越界）
  const mask = new Uint8Array(900 * 900);
  const litPx = rasterizeRoadTrianglesToMask(tris, mask, 900, 3);
  assert.ok(litPx >= 30, `9x9 直角三角形应点亮至少 30 个掩码像素，实际 ${litPx}`);
  assert.equal(mask[453 * 900 + 455], 3);
});

test('36. 129 车纯精简军标型号清洗器：去中文外号与 US_ 前缀、长型号精简及同型自动编号', () => {
  const dirtySamples = [
    ['US_M1A2', '铁砧 01 · M1A2 SEP v3', 'M1A2 SEP v3'],
    ['US_AH64D', '游隼 01 · AH-64D 阿帕奇', 'AH-64D'],
    ['US_A10', 'A-10C Thunderbolt II', 'A-10C'],
    ['US_AC130J', 'US_AC130J', 'AC-130J'],
    ['US_AC130U', 'US_AC130U', 'AC-130U'],
    ['US_LAV_AT-M_body', 'US_LAV_AT-M_body', 'LAV-AT-M'],
    ['US_AV8B_NightAttack', 'AV-8B Night attack', 'AV-8B(NA)'],
    ['US_AV8B_Plus', 'AV-8B Harrier II Plus', 'AV-8B+'],
    ['US_B52H', 'B-52H Stratofortress', 'B-52H'],
    ['US_C130_SuperHercules', 'C-130J-30 Hercules', 'C-130J-30'],
    ['US_CH53K', 'CH-53K King Stallion', 'CH-53K'],
    ['US_M1_ABV', 'M1150 Assault Breacher', 'M1150 ABV'],
    ['US_PATRIOT_PAC3MSE', 'Patriot PAC-3 MSE', 'Patriot PAC-3'],
    ['US_M109A7', 'M109A7 Paladin · 自行火炮 / 火箭', 'M109A7 Paladin'],
  ];
  for (const [slug, raw, expected] of dirtySamples) {
    const res = formatVehicleModelDesignation(slug, raw);
    assert.equal(res.shortModel, expected, `${slug} 应清洗为 ${expected}，实际 ${res.shortModel}`);
    assert.ok(!/[\u4e00-\u9fff]/.test(res.shortModel) && !/[\u4e00-\u9fff]/.test(res.fullModel), `清洗后不应含中文: ${JSON.stringify(res)}`);
    assert.ok(!/^US_/i.test(res.shortModel), `不应含裸 US_ 前缀: ${res.shortModel}`);
  }

  // 同型仅 1 台无序号，2 台及以上自动带 #01/#02
  const labeled = formatUnitDisplayNames([
    { id: 'u1', slug: 'US_M1A2', name: 'M1A2 SEP v3' },
    { id: 'u2', slug: 'US_AH64D', name: 'AH-64D' },
    { id: 'u3', slug: 'US_AH64D', name: 'AH-64D' },
  ]);
  assert.deepEqual(labeled.map(x => x.displayName), ['M1A2 SEP v3', 'AH-64D #01', 'AH-64D #02']);
});

test('37. 场景内 Live PiP 实时实况机位计算：纯模型无环境模式、斜前/追尾切换、大机型自适拉远与防钻地钳制', () => {
  const tankIsolated = computePipCameraPose({ x: 10, y: 2, z: 20, yaw: 0 }, {
    mode: 'front', role: 'ground', boundingRadius: 5.5, groundHeightAt: null,
  });
  const tankFront = computePipCameraPose({ x: 10, y: 2, z: 20, yaw: 0 }, {
    mode: 'front', role: 'ground', boundingRadius: 5.5, groundHeightAt: () => 2.0,
  });
  const tankRear = computePipCameraPose({ x: 10, y: 2, z: 20, yaw: 0 }, {
    mode: 'rear', role: 'ground', boundingRadius: 5.5, groundHeightAt: () => 2.0,
  });
  assert.deepEqual(tankIsolated.position, tankFront.position, '纯模型无环境模式在平地应保持与地面一致的标准斜前构图');
  assert.ok(tankIsolated.position[2] > 20, 'yaw=0 (+Z) 斜前机位应在载具前方 (+Z)');
  assert.ok(tankRear.position[2] < 20, 'yaw=0 (+Z) 追尾机位应在载具后方 (-Z)');
  assert.ok(tankFront.position[1] >= 4.2, '离地高度应满足防钻地安全净空');

  const bomber = computePipCameraPose({ x: 0, y: 234, z: 0, yaw: 0.5 }, {
    mode: 'front', role: 'fixed_wing', boundingRadius: 16.0, groundHeightAt: null,
  });
  assert.ok(bomber.dist > tankFront.dist * 1.8, `大型固定翼距离应显著大于地面车辆 (${bomber.dist} vs ${tankFront.dist})`);

  // 显式传入 groundHeightAt 时仍兼容防钻地钳位测试：若相机地上有 30m 高坡，camY 应被钳位至 >= 32.2m
  const cliffSafe = computePipCameraPose({ x: 0, y: 2, z: 0, yaw: 0 }, {
    mode: 'front', role: 'ground', boundingRadius: 5.5, groundHeightAt: () => 30.0,
  });
  assert.ok(cliffSafe.position[1] >= 32.2 - 1e-6, `相机高程应防钻地钳位到 32.2m+，实际 ${cliffSafe.position[1]}`);
});

test('38. 场景内 Live PiP 球面最短弧平滑跟拍：跨 ±PI 零跳变与 180° 反复换向单调收敛', () => {
  // 1) 跨越 ±PI 边界（+3.0 rad -> -3.0 rad）：最短弧差为 +0.283 rad（逆时针越过 PI），绝不能反向绕远 ~6.0 rad
  let az = 3.0;
  const targetCross = -3.0;
  for (let i = 0; i < 6; i++) {
    const next = stepPipSmoothAzimuth(az, targetCross, 0.033, 5.5);
    const stepDelta = Math.abs(wrapPipAngleRad(next - az));
    assert.ok(stepDelta > 0 && stepDelta <= 0.12, `跨 ±PI 单步角位移必须平顺且微小，实际 ${stepDelta}`);
    assert.ok(Math.abs(next) > 2.9, `应直接跨过 ±PI 边界而非绕回 0，实际 az=${next}`);
    az = next;
  }
  assert.ok(Math.abs(wrapPipAngleRad(az - targetCross)) < 0.15, `6 帧后应平顺收敛接近目标方位角`);

  // 2) 模拟玩家反复点击相反方向目的地导致目标瞬间反转 180°：每步角位移严格受限，且球面距离恒定不穿模
  const poseA = computePipCameraPose({ x: 0, y: 0, z: 0, yaw: 0.4 }, { mode: 'front', role: 'ground', boundingRadius: 5.5 });
  const poseB = computePipCameraPose({ x: 0, y: 0, z: 0, yaw: 0.4 + Math.PI }, { mode: 'front', role: 'ground', boundingRadius: 5.5 });
  assert.equal(poseA.horizDist, poseB.horizDist);
  let orbitAz = poseA.worldAzimuth;
  for (let step = 0; step < 10; step++) {
    const nxt = stepPipSmoothAzimuth(orbitAz, poseB.worldAzimuth, 0.033, 5.2);
    const dAz = Math.abs(wrapPipAngleRad(nxt - orbitAz));
    assert.ok(dAz <= 0.55, `180° 换向单步角变化应平滑受控，实际 ${dAz}`);
    orbitAz = nxt;
  }
});

test('39. 战术小地图 M1 无级锚定缩放、动态比例尺与世界坐标三级 MGRS 网格纯规则', () => {
  // 1) 连续数值跨度与夹紧（1000m ~ 9000m）
  const vp22 = computeMinimapViewport(2200, 300, -400);
  assert.equal(vp22.viewRangeM, 2200);
  assert.equal(vp22.zoomMode, 'custom');
  const vpClampedMin = computeMinimapViewport(400, 0, 0);
  assert.equal(vpClampedMin.viewRangeM, 1000);

  // 2) 指哪放哪锚定缩放：指针下世界坐标在缩放前后的归一化位置应零漂移（<1e-6）
  const vp9 = computeMinimapViewport(9000, 75, 350);
  const anchorNx = 0.62;
  const anchorNy = 0.38;
  const targetWx = vp9.minX + anchorNx * vp9.viewRangeM;
  const targetWz = vp9.minZ + anchorNy * vp9.viewRangeM;
  const vpZoomed = computeAnchoredZoomViewport(vp9, 2500, anchorNx, anchorNy);
  assert.equal(vpZoomed.viewRangeM, 2500);
  const backPx = worldToMinimapPx(targetWx, targetWz, vpZoomed, 1000, 1000);
  assert.ok(Math.abs(backPx.x - anchorNx * 1000) < 1e-4 && Math.abs(backPx.y - anchorNy * 1000) < 1e-4, `锚点坐标应精确保真 (${JSON.stringify(backPx)})`);

  // 边角安全夹紧
  const vpCorner = computeAnchoredZoomViewport(vp9, 1800, 0.02, 0.98);
  assert.ok(vpCorner.minX >= -4425 && vpCorner.maxZ <= 4850);

  // 3) 军用比例尺刻度自适应
  assert.equal(computeMinimapScaleBar(9000, 248).label, '2KM');
  assert.equal(computeMinimapScaleBar(3000, 248).label, '1KM');
  assert.equal(computeMinimapScaleBar(1200, 248).label, '250M');

  // 4) 世界坐标绑定三级 MGRS 网格步长切换 (1500m / 500m / 250m)
  assert.equal(computeDynamicMgrsGrid(vp9).stepM, 1500);
  assert.equal(computeDynamicMgrsGrid(computeMinimapViewport(3000, 0, 0)).stepM, 500);
  assert.equal(computeDynamicMgrsGrid(computeMinimapViewport(1500, 0, 0)).stepM, 250);
});

function makeMockPlaneNode(x = 0, y = 205, z = -1000) {
  return {
    position: {
      x, y, z,
      set(nx, ny, nz) { this.x = nx; this.y = ny; this.z = nz; },
      copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; },
    },
    rotation: {
      x: 0, y: 0, z: 0,
      set(rx, ry, rz) { this.x = rx; this.y = ry; this.z = rz; },
    },
    updateMatrixWorld() {},
  };
}

test('40. AGM-65 同走廊 ±40° 合趟连射、大偏角/满 6 枚自动拆趟与余弹上限保护', () => {
  const fw = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
  const plane = {
    id: 'A10_TEST_1',
    slug: 'US_A10',
    role: 'fixed_wing',
    node: makeMockPlaneNode(0, 205, -1000),
  };
  fw.attachUnit(plane, { orbitCenter: { x: 0, z: 0 } });
  const st = fw.getUnitState(plane);
  assert.equal(fw._getWeapon(st, 'agm65_maverick').cooldownSec, 0.40);
  assert.equal(fw._getWeapon(st, 'gau8_30mm').tracerColor, 'red');

  // 1) 首点在盘旋中心 (0, 0)，随后在 ±40° 扇区同走廊 Shift 连点第 2/3 点 -> 合并进同一趟 missileTargets
  const r1 = fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 0, z: 0 }, fastApproach: true });
  assert.equal(r1.ok, true);
  const r2 = fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: -45, z: 25 }, queue: true });
  assert.equal(r2.mergedCorridor, true);
  assert.equal(r2.passTargetCount, 2);
  assert.equal(r2.queueLength, 0);
  const r3 = fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: -85, z: -30 }, queue: true });
  assert.equal(r3.mergedCorridor, true);
  assert.equal(r3.passTargetCount, 3);
  assert.equal(r3.queueLength, 0);

  // 2) 大偏角 (>40°) / 反向侧点 -> 自动拆入 strikeQueue 下一趟
  const r4 = fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 680, z: -900 }, queue: true });
  assert.equal(r4.mergedCorridor, false);
  assert.equal(r4.queueLength, 1);

  // 3) 校验 canMergeMissileIntoPlan 单趟上限 6 枚保护
  const dummyPlan = {
    cmd: 'missile',
    entryPoint: { x: 0, z: -1050 },
    attackDir: { x: 0, z: 1 },
    minRangeM: 250,
    missileTargets: [{ x: 0, z: 0 }, { x: 1, z: 0 }, { x: 2, z: 0 }, { x: 3, z: 0 }, { x: 4, z: 0 }, { x: 5, z: 0 }],
  };
  assert.equal(canMergeMissileIntoPlan(dummyPlan, { x: 6, z: 0 }, dummyPlan.entryPoint), false);

  // 4) 余弹上限保护：A-10C 共 6 枚 AGM-65，再加 2 点占满 6/6 后第 7 点返回 ammoExhausted
  fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 660, z: -860 }, queue: true });
  fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 640, z: -830 }, queue: true });
  assert.equal(fw.getPlannedWeaponTargetCount(plane, 'agm65_maverick'), 6);
  const rOver = fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 620, z: -800 }, queue: true });
  assert.equal(rOver.ok, false);
  assert.equal(rOver.ammoExhausted, true);
});

test('41. getPredictedFlightPath 完整导出 missileTargets 与 queuedStrikes 且 0.40s 连射后切入拆趟目标', () => {
  const fw = new FixedWingBaseController({ defaultTargetPos: { x: 0, z: 0 } });
  const plane = {
    id: 'A10_TEST_2',
    slug: 'US_A10',
    role: 'fixed_wing',
    node: makeMockPlaneNode(0, 205, -960),
  };
  fw.attachUnit(plane, { orbitCenter: { x: 0, z: 0 } });

  fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 0, z: 0 }, fastApproach: true });
  fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: -40, z: 20 }, queue: true });
  fw.issueStrikeCommand(plane, { cmd: 'missile', targetPos: { x: 680, z: -900 }, queue: true });

  const p0 = fw.getPredictedFlightPath(plane);
  assert.equal(p0.missileTargets.length, 2);
  assert.equal(p0.queuedStrikes.length, 1);
  assert.equal(p0.queuedStrikes[0].missileTargets.length, 1);
  assert.ok(p0.queuedStrikes[0].etaSec > p0.etaSec);

  // 推进模拟直到 2 枚连射发射并进入 egress（确认 egress 期间 getPredictedFlightPath 仍返回 queuedStrikes 不隐身）
  const firedEvents = [];
  let sawEgressWithQueue = false;
  for (let i = 0; i < 400; i += 1) {
    const st = fw.step(plane, 0.05, i * 0.05);
    for (const ev of st.lastEvents) {
      if (ev.type === 'missile_fired') firedEvents.push({ t: i * 0.05, ...ev });
    }
    if (st.flightState === 'egress') {
      const pEg = fw.getPredictedFlightPath(plane);
      if (pEg?.kind === 'strike_queued' && pEg.queuedStrikes?.length >= 1) {
        sawEgressWithQueue = true;
      }
    }
    if (firedEvents.length >= 3) break;
  }
  assert.equal(firedEvents.length, 3, `两趟共 3 枚应全部发射，实际 ${firedEvents.length}`);
  assert.ok(firedEvents[1].t - firedEvents[0].t >= 0.35 && firedEvents[1].t - firedEvents[0].t <= 0.60, `连射间隔应约为 0.40s，实际 ${(firedEvents[1].t - firedEvents[0].t).toFixed(2)}s`);
  assert.equal(sawEgressWithQueue, true, 'egress 拉起阶段仍应导出 queuedStrikes 供地面标绘不断线');
});

test('42. 地面强制攻地 attackGround 支持 Shift 多点队列追加、非追加覆写与 cancelFleetUnits 清空', () => {
  const tank = { id: 'M1_1', role: 'ground', x: 0, y: 0, z: 0, job: null, queue: [] };
  const env = makeReadyEnv();

  dispatchFleetCommand({ units: [tank], ids: ['M1_1'], command: { type: 'attackGround', point: { x: 80, z: 40 } }, append: false, env, serial: 1 });
  dispatchFleetCommand({ units: [tank], ids: ['M1_1'], command: { type: 'attackGround', point: { x: 140, z: 60 } }, append: true, env, serial: 2 });
  dispatchFleetCommand({ units: [tank], ids: ['M1_1'], command: { type: 'attackGround', point: { x: 200, z: 80 } }, append: true, env, serial: 3 });

  assert.equal(tank.attackGroundQueue.length, 3);
  assert.deepEqual(tank.attackGroundQueue.map(q => q.point.x), [80, 140, 200]);

  // 非追加覆写为 1 项
  dispatchFleetCommand({ units: [tank], ids: ['M1_1'], command: { type: 'attackGround', point: { x: 300, z: 120 } }, append: false, env, serial: 4 });
  assert.equal(tank.attackGroundQueue.length, 1);
  assert.equal(tank.attackGroundQueue[0].point.x, 300);

  // cancelFleetUnits 清空攻地队列
  cancelFleetUnits({ units: [tank], ids: ['M1_1'] });
  assert.deepEqual(tank.attackGroundQueue, []);
  assert.equal(tank.lastAttackGroundIntent, null);
});

test('43. 方案 A 8 槽轮盘纯几何规则：死区取消、8 方向 45° 扇区与 800px 无限外延命中', () => {
  assert.equal(TACTICAL_WHEEL_CONFIG.slotCount, 8);
  assert.equal(TACTICAL_WHEEL_CONFIG.deadzonePx, 38);

  // 死区内全部返回 index = -1
  assert.equal(resolveWheelSectorFromVector(0, 0).index, -1);
  assert.equal(resolveWheelSectorFromVector(20, -25).inDeadzone, true);

  // 8 个标准方位（含超出外环 800px 无限外延）精准映射到 0..7
  const dirs = [
    [0, -120, 0],
    [120, -120, 1],
    [800, 0, 2],
    [240, 240, 3],
    [0, 500, 4],
    [-160, 160, 5],
    [-900, 0, 6],
    [-180, -180, 7],
  ];
  for (const [dx, dy, expected] of dirs) {
    const hit = resolveWheelSectorFromVector(dx, dy);
    assert.equal(hit.inDeadzone, false);
    assert.equal(hit.index, expected, `向量 (${dx},${dy}) 应命中扇区 ${expected}，实际 ${hit.index}`);
  }
});

test('44. 方案 A 轮盘 16:9/21:9 四角内推防裁切与槽位提交四态拦截校验', () => {
  const r = TACTICAL_WHEEL_CONFIG.visualRadius;
  const pad = TACTICAL_WHEEL_CONFIG.edgePaddingPx;
  for (const [w, h] of [[1208, 680], [1588, 680]]) {
    for (const origin of [{ x: 4, y: 4 }, { x: w - 4, y: 4 }, { x: 4, y: h - 4 }, { x: w - 4, y: h - 4 }]) {
      const c = clampWheelVisualCenter(origin, w, h);
      assert.equal(c.clamped, true);
      assert.ok(c.x - r >= pad - 0.1 && c.x + r <= w - pad + 0.1, `${w}x${h} 横向越界: ${JSON.stringify(c)}`);
      assert.ok(c.y - r >= pad - 0.1 && c.y + r <= h - pad + 0.1, `${w}x${h} 纵向越界: ${JSON.stringify(c)}`);
    }
    const mid = clampWheelVisualCenter({ x: w * 0.5, y: h * 0.5 }, w, h);
    assert.equal(mid.clamped, false);
  }

  assert.equal(canCommitWheelSlot(null).ok, false);
  assert.equal(canCommitWheelSlot({ id: 'a', status: 'empty' }).status, 'empty');
  assert.equal(canCommitWheelSlot({ id: 'b', status: 'disabled', reason: '无弹药' }).ok, false);
  assert.equal(canCommitWheelSlot({ id: 'c', status: 'cooling', cooldownSec: 2.5 }).ok, false);
  assert.equal(canCommitWheelSlot({ id: 'd', status: 'ready' }).ok, true);
  assert.equal(canCommitWheelSlot({ id: 'e', status: 'active' }).ok, true);
});

let passed = 0;
for (const { name, fn } of cases) {
  try {
    fn();
    passed += 1;
    console.log(`PASS  ${name}`);
  } catch (err) {
    console.error(`FAIL  ${name}\n`, err);
    process.exitCode = 1;
  }
}

console.log(`\n规则单测汇总：${passed}/${cases.length} 通过`);
