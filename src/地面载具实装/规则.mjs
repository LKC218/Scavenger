/** 地面共通试验规则。纯数据运行，不依赖 DOM 或 Three.js；新增参数尚待人工验收。 */
export const GROUND_PARAMETERS = Object.freeze({
  candidate: true,
  note: '耐久、速度、弹量、费用及补给值均为本原型候选参数，尚未人工验收；不代表现实车辆数据。',
  initialPoints: 5000, initialStock: 240, maxUnits: 16,
  hp: 100, speed: 6, cost: 100, tankCost: 160, supplyCost: 120,
  tankAmmo: 8, supplyCapacity: 80, supplyRange: 12, loadRate: 20,
  repairRate: 8, repairCost: 1, ammoInterval: 1, ammoCost: 2, stockPrice: 1,
  station: Object.freeze({x: -40, z: 40, radius: 12}),
  // 复制自 src/载具靶场/模拟.mjs 的 WEAPONS.tank，是既有游戏参数，并非现实装备参数。
  tankWeapon: Object.freeze({name: '120毫米主炮', range: 52, reload: 3.2, aim: .45, speed: 85, damage: 180, blast: 4}),
});

const TANK = 'model:US_M1A2', SUPPLY = 'model:US_M548A3';
const STATIC = new Set(['model:US_CRAM', 'model:US_PATRIOT_PAC2', 'model:US_PATRIOT_PAC3', 'model:US_PATRIOT_PAC3MSE']);
const EPSILON = 1e-8;
const distance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);
const validPoint = point => point && Number.isFinite(point.x) && Number.isFinite(point.z);
const tidy = number => Math.abs(number) < EPSILON ? 0 : Number(number.toFixed(9));
const copyPoint = point => ({x: point.x, z: point.z});

/** 完整登记地面型号；模型技术通过与武器功能接入分别显示。 */
export function normalizeCatalog(catalog) {
  const source = Array.isArray(catalog) ? catalog : catalog?.models ?? catalog?.entries ?? catalog?.items ?? catalog?.assets ?? [];
  if (!Array.isArray(source)) throw new TypeError('地面型号目录必须包含数组。');
  const seen = new Set();
  return source.filter(row => {
    const id = row.id ?? row.modelId ?? row.model_id;
    if (typeof id !== 'string' || !id.startsWith('model:') || seen.has(id)) return false;
    if (row.category === 'air' || row.domain === 'air') return false;
    seen.add(id); return true;
  }).map(row => {
    const id = row.id ?? row.modelId ?? row.model_id;
    const qualityStatus = row.quality_status ?? row.qualityStatus ?? row.status ?? 'pending';
    const deployable = qualityStatus === 'passed' && row.deployable !== false;
    const role = id === TANK ? 'combat' : id === SUPPLY ? 'supply' : 'pending';
    const mobility = STATIC.has(id) ? 'static' : 'ground';
    const readyStatus = !deployable ? '模型待修，禁止部署' : role === 'combat' ? '主炮已接入，其余专用功能待审' : role === 'supply' ? '补给已接入，其余专用功能待审' : '共通能力已接入，武器及专用功能待审';
    const capabilities = Object.freeze({deploy: deployable, movement: deployable && mobility === 'ground', durability: deployable, attack: deployable && role === 'combat', supply: deployable && role === 'supply'});
    const profile = Object.freeze({kind: 'ground', role, mobility, deployable, readyStatus, capabilities,
      hp: GROUND_PARAMETERS.hp, speed: mobility === 'static' ? 0 : GROUND_PARAMETERS.speed,
      cost: role === 'combat' ? GROUND_PARAMETERS.tankCost : role === 'supply' ? GROUND_PARAMETERS.supplyCost : GROUND_PARAMETERS.cost,
      maxAmmo: role === 'combat' ? GROUND_PARAMETERS.tankAmmo : 0,
      capacity: role === 'supply' ? GROUND_PARAMETERS.supplyCapacity : 0,
      weapon: role === 'combat' ? GROUND_PARAMETERS.tankWeapon : null,
      candidate: true});
    return {...row, id, name: row.name ?? id, quality_status: qualityStatus, deployable, role, mobility, readyStatus, capabilities, profile};
  });
}

export class GroundSimulation {
  constructor(catalog, options = {}) {
    this.catalog = normalizeCatalog(catalog); this.models = new Map(this.catalog.map(row => [row.id, row]));
    this.units = []; this.projectiles = []; this.events = []; this.time = 0;
    this.points = options.points ?? GROUND_PARAMETERS.initialPoints;
    this.stock = options.stock ?? GROUND_PARAMETERS.initialStock;
    if (![this.points, this.stock].every(value => Number.isFinite(value) && value >= 0)) throw new TypeError('点数及后方库存必须为非负数。');
    this.station = {...GROUND_PARAMETERS.station, ...options.station};
    if (!validPoint(this.station) || !Number.isFinite(this.station.radius) || this.station.radius <= 0) throw new TypeError('后方补给站坐标或范围无效。');
    const targets = options.targets ?? [
      {id: 'target-1', name: '近距耐久靶', x: 15, z: 15},
      {id: 'target-2', name: '中距耐久靶', x: 35, z: -15},
      {id: 'target-3', name: '远距耐久靶', x: 60, z: 30},
    ];
    this.targets = targets.map((target, index) => {
      const hp = target.hp ?? target.maxHp ?? 540;
      if (!validPoint(target) || !Number.isFinite(hp) || hp < 0) throw new TypeError('耐久靶参数无效。');
      return {...target, id: target.id ?? `target-${index + 1}`, name: target.name ?? `耐久靶${index + 1}`, hp, maxHp: target.maxHp ?? hp, alive: hp > 0};
    });
    this.serial = 0; this.eventSerial = 0; this.shots = 0; this.hits = 0; this.kills = 0; this.lastError = '';
  }

  result(ok, reason = '', details = {}) {
    this.lastError = ok ? '' : reason; return {ok, reason, ...details};
  }
  emit(type, details = {}) {
    this.events.push({type, serial: ++this.eventSerial, time: this.time, ...details});
    if (this.events.length > 200) this.events.splice(0, this.events.length - 200);
  }
  drainEvents() { return this.events.splice(0); }
  getUnit(id) { return this.units.find(unit => unit.id === id); }
  active(id) { const unit = this.getUnit(id); return unit?.alive ? unit : null; }
  stop(unit) { unit.command = null; unit.moving = false; unit.aimTime = 0; unit.supplyTime = 0; unit.status = unit.alive ? '待命' : '已摧毁'; }

  deploy(modelId, point = this.station) {
    const row = this.models.get(modelId);
    if (!row) { this.result(false, '型号未登记。'); return null; }
    if (!row.deployable) { this.result(false, '该型号模型尚未技术通过，禁止部署。'); return null; }
    if (!validPoint(point)) { this.result(false, '部署坐标无效。'); return null; }
    if (this.units.filter(unit => unit.alive).length >= GROUND_PARAMETERS.maxUnits) { this.result(false, '已达到16个在场单位的本轮试验上限。'); return null; }
    if (this.points < row.profile.cost) { this.result(false, '部署点数不足。'); return null; }
    const unit = {id: `ground-${++this.serial}`, modelId, name: row.name, kind: 'ground', role: row.role, mobility: row.mobility, profile: row.profile,
      x: point.x, z: point.z, yaw: 0, hp: row.profile.hp, maxHp: row.profile.hp, ammo: row.profile.maxAmmo, maxAmmo: row.profile.maxAmmo,
      cargo: 0, capacity: row.profile.capacity, alive: true, moving: false, command: null, status: '待命', aimTime: 0, supplyTime: 0, readyAt: this.time};
    this.points = tidy(this.points - row.profile.cost); this.units.push(unit); this.result(true);
    this.emit('deploy', {unitId: unit.id, modelId, position: copyPoint(unit)}); return unit;
  }

  commandMove(id, point) {
    const unit = this.active(id);
    if (!unit) return this.result(false, '单位不存在或已摧毁。');
    if (unit.mobility === 'static') return this.result(false, '该平台没有自行移动能力，当前仅允许部署。');
    if (!validPoint(point)) return this.result(false, '移动坐标无效。');
    this.stop(unit); unit.command = {type: 'move', point: copyPoint(point)}; unit.moving = distance(unit, point) > EPSILON; unit.status = unit.moving ? '移动中' : '待命';
    return this.result(true);
  }

  commandAttack(id, targetId) {
    const unit = this.active(id), target = this.targets.find(value => value.id === targetId && value.alive);
    if (!unit) return this.result(false, '单位不存在或已摧毁。');
    if (!unit.profile.weapon) return this.result(false, '该型号武器或专用功能尚待核实，不能攻击。');
    if (!target) return this.result(false, '耐久靶不存在或已摧毁。');
    if (distance(unit, target) > unit.profile.weapon.range) return this.result(false, '目标超出主炮试验射程。');
    if (unit.ammo <= 0) return this.result(false, '弹药已耗尽，请先补给。');
    this.stop(unit); unit.command = {type: 'attack', targetId}; unit.status = '瞄准中'; return this.result(true);
  }

  damage(id, amount) {
    const unit = this.active(id);
    if (!unit) return this.result(false, '单位不存在或已摧毁。');
    if (!Number.isFinite(amount) || amount <= 0) return this.result(false, '伤害必须为正数。');
    const actual = Math.min(unit.hp, amount); unit.hp = tidy(unit.hp - actual);
    if (unit.hp <= 0) {
      unit.alive = false; this.stop(unit); this.emit('destroy', {unitId: unit.id, position: copyPoint(unit)});
      for (const other of this.units) if (other.command?.targetId === id && other.command.type === 'supply') this.stop(other);
    }
    return this.result(true, '', {amount: actual, destroyed: !unit.alive});
  }

  loadSupply(id) {
    const unit = this.active(id);
    if (!unit) return this.result(false, '单位不存在或已摧毁。');
    if (unit.role !== 'supply') return this.result(false, '仅M548A3已接入补给运输。');
    if (unit.moving || distance(unit, this.station) > this.station.radius) return this.result(false, '请将补给车停在后方补给站范围内。');
    if (unit.cargo >= unit.capacity - EPSILON) return this.result(false, '补给车货舱已满。');
    if (this.stock <= 0) return this.result(false, '后方库存不足，请先购买物资。');
    this.stop(unit); unit.command = {type: 'load'}; unit.status = '后方装载中'; return this.result(true);
  }

  supply(id, targetId) {
    const unit = this.active(id), target = this.active(targetId);
    if (!unit || !target) return this.result(false, '补给车或目标不存在，或已摧毁。');
    if (unit.role !== 'supply') return this.result(false, '仅M548A3已接入补给运输。');
    if (id === targetId) return this.result(false, '补给车不能向自身交付物资。');
    if (unit.moving || target.moving) return this.result(false, '补给双方必须停稳。');
    if (distance(unit, target) > GROUND_PARAMETERS.supplyRange) return this.result(false, '目标超出近距补给范围。');
    if (unit.cargo <= 0) return this.result(false, '补给车物资已耗尽，请返回后方装载。');
    if (target.hp >= target.maxHp && target.ammo >= target.maxAmmo) return this.result(false, '目标无需维修或补弹。');
    this.stop(unit); unit.command = {type: 'supply', targetId}; unit.status = '近距补给中'; return this.result(true);
  }

  buyStock(quantity) {
    if (!Number.isInteger(quantity) || quantity <= 0) return this.result(false, '购买数量必须为正整数。');
    const cost = quantity * GROUND_PARAMETERS.stockPrice;
    if (!Number.isSafeInteger(quantity) || !Number.isFinite(cost) || this.points < cost) return this.result(false, '购买点数不足或数量过大。');
    this.points = tidy(this.points - cost); this.stock = tidy(this.stock + quantity);
    this.emit('stock', {quantity, cost}); return this.result(true, '', {quantity, cost});
  }

  update(dt) {
    if (!Number.isFinite(dt) || dt < 0 || dt > 3600) return this.result(false, '推进时间必须为0至3600秒。');
    // 直接使用调用方提供的时间差，分段仅用于数值稳定，不通过休眠累计计时。
    let remaining = dt;
    while (remaining > EPSILON) { const step = Math.min(.1, remaining); this.advance(step); remaining -= step; }
    return this.result(true);
  }

  advance(dt) {
    this.time += dt;
    for (const unit of this.units) {
      if (!unit.alive || !unit.command) continue;
      const command = unit.command;
      if (command.type === 'move') {
        const length = distance(unit, command.point), travel = unit.profile.speed * dt;
        if (length <= travel + EPSILON) { unit.x = command.point.x; unit.z = command.point.z; this.stop(unit); this.emit('moveComplete', {unitId: unit.id}); }
        else { unit.yaw = Math.atan2(command.point.x - unit.x, command.point.z - unit.z); unit.x += (command.point.x - unit.x) / length * travel; unit.z += (command.point.z - unit.z) / length * travel; }
      } else if (command.type === 'attack') this.advanceAttack(unit, dt);
      else if (command.type === 'load') this.advanceLoad(unit, dt);
    }
    // 移动先结算，补给再检查双方实际位置，避免数组先后顺序让离开的目标多收一帧物资。
    for (const unit of this.units) if (unit.alive && unit.command?.type === 'supply') this.advanceSupply(unit, dt);
    for (const projectile of this.projectiles) {
      projectile.remaining -= dt;
      if (projectile.remaining > EPSILON) continue;
      const target = this.targets.find(value => value.id === projectile.targetId);
      if (target?.alive) {
        target.hp = Math.max(0, target.hp - projectile.damage); this.hits++;
        if (target.hp <= 0) { target.alive = false; this.kills++; this.emit('targetDestroy', {targetId: target.id, position: copyPoint(target)}); }
      }
      this.emit('impact', {unitId: projectile.unitId, targetId: projectile.targetId, position: projectile.position});
    }
    this.projectiles = this.projectiles.filter(projectile => projectile.remaining > EPSILON);
  }

  advanceAttack(unit, dt) {
    const target = this.targets.find(value => value.id === unit.command.targetId && value.alive), weapon = unit.profile.weapon;
    if (!target || !weapon) { this.stop(unit); return; }
    if (unit.ammo <= 0) { this.stop(unit); unit.status = '弹药耗尽'; return; }
    if (distance(unit, target) > weapon.range || unit.moving) { this.stop(unit); return; }
    unit.yaw = Math.atan2(target.x - unit.x, target.z - unit.z); unit.aimTime += dt;
    if (this.time + EPSILON < unit.readyAt) { unit.status = '装填中'; return; }
    unit.status = '瞄准中';
    if (unit.aimTime + EPSILON < weapon.aim) return;
    // 仅成功生成实际炮弹后扣弹；无目标、越界、装填和空仓均不扣弹。
    this.projectiles.push({unitId: unit.id, targetId: target.id, remaining: Math.max(.01, distance(unit, target) / weapon.speed), position: copyPoint(target), damage: weapon.damage});
    unit.ammo--; unit.readyAt = this.time + weapon.reload; unit.aimTime = 0; unit.status = '装填中'; this.shots++;
    this.emit('shot', {unitId: unit.id, targetId: target.id, position: copyPoint(unit), destination: copyPoint(target), weapon: 'tank'});
  }

  advanceLoad(unit, dt) {
    if (unit.moving || distance(unit, this.station) > this.station.radius) { this.stop(unit); return; }
    const amount = Math.min(this.stock, unit.capacity - unit.cargo, GROUND_PARAMETERS.loadRate * dt);
    if (amount > EPSILON) { this.stock = tidy(this.stock - amount); unit.cargo = tidy(unit.cargo + amount); }
    if (this.stock <= EPSILON || unit.cargo >= unit.capacity - EPSILON) this.stop(unit);
  }

  advanceSupply(unit, dt) {
    const target = this.active(unit.command.targetId), parameters = GROUND_PARAMETERS;
    if (!target || unit.moving || target.moving || distance(unit, target) > parameters.supplyRange || unit.cargo <= EPSILON) { this.stop(unit); return; }
    // 弹药已满时不预存交付进度，避免日后开炮后瞬间补回多发。
    unit.supplyTime = target.ammo < target.maxAmmo ? Math.min(parameters.ammoInterval, unit.supplyTime + dt) : 0;
    // 补弹逐发交付；不足一发物资时不预扣，也不留下中断后的待扣债务。
    while (unit.supplyTime + EPSILON >= parameters.ammoInterval && target.ammo < target.maxAmmo && unit.cargo + EPSILON >= parameters.ammoCost) {
      target.ammo++; unit.cargo = tidy(unit.cargo - parameters.ammoCost); unit.supplyTime -= parameters.ammoInterval;
      this.emit('resupply', {unitId: unit.id, targetId: target.id, ammo: 1, cost: parameters.ammoCost});
    }
    const repair = Math.min(target.maxHp - target.hp, parameters.repairRate * dt, unit.cargo / parameters.repairCost);
    if (repair > EPSILON) { target.hp = tidy(target.hp + repair); unit.cargo = tidy(unit.cargo - repair * parameters.repairCost); }
    if ((target.hp >= target.maxHp - EPSILON && target.ammo >= target.maxAmmo) || unit.cargo <= EPSILON || (target.hp >= target.maxHp - EPSILON && unit.cargo < parameters.ammoCost)) this.stop(unit);
  }
}
