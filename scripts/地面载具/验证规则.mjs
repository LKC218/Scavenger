/** 只验证纯数据规则，不读取游戏文件、不启动服务或改写现有原型。 */
import assert from 'node:assert/strict';
import {GroundSimulation, normalizeCatalog, GROUND_PARAMETERS as P} from '../../src/地面载具实装/规则.mjs';

const MODEL = {tank: 'model:US_M1A2', supply: 'model:US_M548A3', pending: 'model:US_M2A3', partial: 'model:US_TEST_PARTIAL', static: 'model:US_PATRIOT_PAC3MSE'};
const catalog = {models: Object.entries(MODEL).map(([key, id]) => ({id, name: key, quality_status: key === 'partial' ? 'partial' : 'passed', deployable: key !== 'partial'}))};
let passed = 0;
const test = (name, run) => { run(); passed++; process.stdout.write(`通过：${name}\n`); };
const sim = options => new GroundSimulation(catalog, options);
const point = {x: -40, z: 40};

test('完整登记但部分通过与显式禁用项不能部署', () => {
  const rows = normalizeCatalog(catalog); assert.equal(rows.length, 5);
  const world = sim(); const before = world.points;
  assert.equal(world.deploy(MODEL.partial, point), null); assert.equal(world.points, before);
  const disabled = new GroundSimulation([{id: MODEL.tank, quality_status: 'passed', deployable: false}]);
  assert.equal(disabled.deploy(MODEL.tank, point), null);
});
test('型号、地面分类、角色和移动方式分开记录', () => {
  const world = sim(); const tank = world.deploy(MODEL.tank, point), pending = world.deploy(MODEL.pending, point);
  assert.equal(tank.kind, 'ground'); assert.equal(pending.kind, 'ground'); assert.equal(pending.role, 'pending');
  assert.equal(world.catalog.find(row => row.id === MODEL.pending).profile.weapon, null);
  assert.equal(world.commandAttack(pending.id, world.targets[0].id).ok, false);
  const platform = world.deploy(MODEL.static, point); assert.equal(platform.mobility, 'static');
  assert.equal(world.commandMove(platform.id, {x: 10, z: 10}).ok, false);
});
test('四类固定平台都能部署且不能自行移动', () => {
  const ids = ['model:US_CRAM', 'model:US_PATRIOT_PAC2', 'model:US_PATRIOT_PAC3', 'model:US_PATRIOT_PAC3MSE'];
  const world = new GroundSimulation(ids.map(id => ({id, quality_status: 'passed', deployable: true})));
  for (const id of ids) {
    const platform = world.deploy(id, point); assert.ok(platform); assert.equal(platform.role, 'pending');
    assert.equal(world.commandMove(platform.id, {x: 10, z: 10}).ok, false);
    assert.equal(world.commandAttack(platform.id, world.targets[0].id).ok, false);
  }
});
test('只有成功发射才扣最后一发且不会负弹量', () => {
  const world = sim({targets: [{id: 'far', x: 1000, z: 0, hp: 10000}, {id: 'near', x: 10, z: 0, hp: 10000}]});
  const tank = world.deploy(MODEL.tank, {x: 0, z: 0}); tank.ammo = 1;
  assert.equal(world.commandAttack(tank.id, 'far').ok, false); assert.equal(tank.ammo, 1);
  assert.equal(world.commandAttack(tank.id, 'near').ok, true); world.update(.3); assert.equal(tank.ammo, 1);
  world.update(.3); assert.equal(tank.ammo, 0); assert.equal(world.shots, 1);
  world.update(20); assert.equal(world.shots, 1); assert.equal(tank.ammo, 0);
});
test('摧毁清命令且永久不能移动、攻击、装载或维修复活', () => {
  const world = sim(); const tank = world.deploy(MODEL.tank, point), truck = world.deploy(MODEL.supply, point);
  world.commandMove(tank.id, {x: -20, z: 40}); world.damage(tank.id, 1000);
  assert.equal(tank.alive, false); assert.equal(tank.command, null); assert.equal(tank.moving, false);
  assert.equal(world.commandMove(tank.id, point).ok, false); assert.equal(world.commandAttack(tank.id, world.targets[0].id).ok, false);
  assert.equal(world.supply(truck.id, tank.id).ok, false); world.update(30); assert.equal(tank.hp, 0);
  world.damage(truck.id, 1000); assert.equal(world.loadSupply(truck.id).ok, false);
});
test('后方装载转移库存且中途移动不会继续扣库存', () => {
  const world = sim({stock: 17}); const truck = world.deploy(MODEL.supply, point);
  assert.equal(world.loadSupply(truck.id).ok, true); world.update(.5);
  assert.equal(truck.cargo, 10); assert.equal(world.stock, 7);
  world.commandMove(truck.id, {x: 0, z: 40}); world.update(1);
  assert.equal(truck.cargo, 10); assert.equal(world.stock, 7); assert.equal(world.loadSupply(truck.id).ok, false);
});
test('多辆车同时装载不会透支有限库存', () => {
  const world = sim({stock: 7}); const a = world.deploy(MODEL.supply, point), b = world.deploy(MODEL.supply, point);
  world.loadSupply(a.id); world.loadSupply(b.id); world.update(5);
  assert.equal(world.stock, 0); assert.equal(a.cargo + b.cargo, 7); assert.ok(a.cargo >= 0 && b.cargo >= 0);
});
test('补弹逐发、维修部分交付按准确物资扣除', () => {
  const world = sim(); const tank = world.deploy(MODEL.tank, point), truck = world.deploy(MODEL.supply, point);
  tank.ammo -= 1; truck.cargo = 3; world.supply(truck.id, tank.id); world.update(.5);
  assert.equal(tank.ammo, P.tankAmmo - 1); assert.equal(truck.cargo, 3);
  world.update(.5); assert.equal(tank.ammo, P.tankAmmo); assert.equal(truck.cargo, 1);
  world.damage(tank.id, 10); world.supply(truck.id, tank.id); world.update(1);
  assert.equal(tank.hp, 91); assert.equal(truck.cargo, 0);
});
test('补给中断不多扣，不累积未交付的整发物资', () => {
  const world = sim(); const tank = world.deploy(MODEL.tank, point), truck = world.deploy(MODEL.supply, point);
  tank.ammo = 0; truck.cargo = 20; world.supply(truck.id, tank.id); world.update(.5);
  world.commandMove(truck.id, {x: -35, z: 40}); world.update(1);
  assert.equal(tank.ammo, 0); assert.equal(truck.cargo, 20);
  world.supply(truck.id, tank.id); world.update(.5); assert.equal(tank.ammo, 0);
  world.update(.5); assert.equal(tank.ammo, 1); assert.equal(truck.cargo, 18);
});
test('目标移动立即中断补给且多车不会过量维修或补弹', () => {
  const world = sim(); const a = world.deploy(MODEL.supply, point), tank = world.deploy(MODEL.tank, point), b = world.deploy(MODEL.supply, point);
  a.cargo = 20; b.cargo = 20; tank.ammo--; world.damage(tank.id, 1);
  world.supply(a.id, tank.id); world.supply(b.id, tank.id); world.update(1);
  assert.equal(tank.hp, tank.maxHp); assert.equal(tank.ammo, tank.maxAmmo); assert.equal(a.cargo + b.cargo, 37);
  tank.ammo--; world.supply(a.id, tank.id); world.commandMove(tank.id, {x: 0, z: 0});
  const before = a.cargo; world.update(2); assert.equal(a.cargo, before); assert.equal(a.command, null);
});
test('买物资只增加后方库存且点数与数量受限', () => {
  const world = sim(); const truck = world.deploy(MODEL.supply, point), tank = world.deploy(MODEL.tank, point);
  tank.ammo = 0; world.damage(tank.id, 20); const beforePoints = world.points, beforeStock = world.stock;
  assert.equal(world.buyStock(10).ok, true); assert.equal(world.stock, beforeStock + 10); assert.equal(world.points, beforePoints - 10 * P.stockPrice);
  assert.equal(truck.cargo, 0); assert.equal(tank.ammo, 0); assert.equal(tank.hp, 80);
  assert.equal(world.buyStock(-1).ok, false); assert.equal(world.buyStock(1e12).ok, false);
});
test('后方范围、近距停稳与不自补规则有效', () => {
  const world = sim(); const truck = world.deploy(MODEL.supply, point), tank = world.deploy(MODEL.tank, {x: 0, z: 0});
  truck.cargo = 10; tank.ammo--;
  assert.equal(world.supply(truck.id, tank.id).ok, false); assert.equal(world.supply(truck.id, truck.id).ok, false);
  world.commandMove(truck.id, {x: -35, z: 40}); assert.equal(world.loadSupply(truck.id).ok, false);
});
test('时间差推进、有限点数与上限有效，靶摧毁后不重生', () => {
  const world = sim({targets: [{id: 'one', x: 10, z: 0, hp: 1}]}); const tank = world.deploy(MODEL.tank, {x: 0, z: 0});
  world.commandMove(tank.id, {x: 6, z: 0}); world.update(.5); assert.ok(Math.abs(tank.x - 3) < 1e-7);
  world.update(.5); assert.equal(tank.x, 6); assert.equal(tank.moving, false);
  world.commandAttack(tank.id, 'one'); world.update(1); assert.equal(world.targets[0].alive, false);
  world.update(30); assert.equal(world.targets[0].hp, 0);
  assert.equal(new GroundSimulation(catalog, {points: 0}).deploy(MODEL.tank, point), null);
  const capped = sim({points: 1e5}); for (let i = 0; i < P.maxUnits; i++) assert.ok(capped.deploy(MODEL.pending, point));
  assert.equal(capped.deploy(MODEL.pending, point), null);
});
process.stdout.write(`地面共通规则验证完成：${passed}项通过。\n`);
