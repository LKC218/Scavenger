/** 工程师已批准的V001网页候选参数；不是原作数值表。 */
export const CONFIG = Object.freeze({
  version: 'V001', step: 1 / 30, perceptionStep: .1, seed: 1105,
  camera: { tilt: 55 * Math.PI / 180, width: 360, minWidth: 120, maxWidth: 900 },
  render: { width: 1280, height: 720, pixelRatio: 1, shadowSize: 1024, corpseLimit: 200, corpseLife: 20, corpseFade: 2, wreckLimit: 20, wreckLife: 30, wreckFade: 3, effectLimit: 16, near: 100, far: 160, tinyPixels: 5 },
  audio: { volume: .25, groupVoices: 8, battleVoices: 16 },
  tank: { speed: 8, acceleration: 4, turnRate: Math.PI / 3, stopRadius: 1.5, hp: 1200, radius: 4.3, height: 2.8, engineRange: 90, idleRange: 40, vision: 120 },
  heli: { speed: 18, climb: 6, cruise: 35, work: 12, clearance: 10 },
  plane: { speed: 45, orbitRadius: 80, clearance: 80 },
  zombie: { hp: 100, speed: 1.8, radius: .38, height: 1.86, wanderRadius: 20, searchTime: 12, detectRange: 30, contactRange: 1, contactDamage: 2, contactInterval: 1 },
  bait: { delay: 5, duration: 30, range: 220, hp: 50 },
  weapons: {
    main: { id: 'main', name: 'M256主炮', sourceId: 342, aim: .45, reload: 3.2, range: 90, speed: 85, turnRate: 75 * Math.PI / 180, soundRange: 200, allowedAmmo: ['ap','heat'], muzzle: { verified: true, node: 'shell_spawn_0_0', yawNode: 'turret_0', pitchNode: 'weapon_0_1_2', position: {x:-.002437221043,y:1.820108902182,z:-5.589732875871}, pivot: {x:-.004767961635,y:1.528556754458,z:-.228991672181}, pitchPivot: {x:-.000657221608,y:1.856599459609,z:-1.402282586693}, forward:{x:0,y:0,z:-1}, sourceSha256: '2232103474c1d04f3c4c67854cfb6e7427f34e97bea40cacad001933fbcdeb94' } },
    m2: { id: 'm2', name: 'M2顶置机枪', sourceId: 18, mountId: 450, aim: .25, reload: 6, range: 60, speed: 150, turnRate: Math.PI / 2, soundRange: 120, magazineSize: 30, burst: 5, shotInterval: .1, burstInterval: .6, allowedAmmo: ['ball'], muzzle: { verified: true, node: 'shell_spawn_0', yawNode: 'turret_0_0', pitchNode: 'weapon_0', position: {x:.512972227970,y:2.739341141138,z:-1.475126744529}, pivot: {x:.500156793690,y:2.299378963705,z:.448972750950}, pitchPivot: {x:.568152265967,y:2.723321075775,z:-.302106799880}, forward:{x:0,y:0,z:-1}, sourceSha256: '2232103474c1d04f3c4c67854cfb6e7427f34e97bea40cacad001933fbcdeb94' } },
  },
  ammunition: {
    ap: { id: 'ap', sourceId: 256, name: 'AP穿甲弹', initial: 4, damage: 120, inner: 0, outer: 0, edge: 0 },
    heat: { id: 'heat', sourceId: 253, name: 'HEAT破甲弹', initial: 8, damage: 180, inner: 2, outer: 7, edge: .25 },
    ball: { id: 'ball', sourceId: 19, name: '12.7毫米', initial: 120, damage: 18, inner: 0, outer: 0, edge: 0 },
  },
  presets: {
    interaction: { label: 'G01交互', count: 120, tanks: 1, helis: 1, planes: 1, vision: 'combat' },
    behavior: { label: 'G03行为', count: 120, tanks: 1, helis: 1, planes: 1, vision: 'combat' },
    low: { label: '500分级', count: 500, tanks: 8, helis: 1, planes: 1, vision: 'all', distribution: [128,256,116] },
    main: { label: '2000主验', count: 2000, tanks: 8, helis: 1, planes: 1, vision: 'all', distribution: [256,1024,720] },
    explore: { label: '5000探索', count: 5000, tanks: 8, helis: 1, planes: 1, vision: 'all', distribution: [512,2048,2440] },
    individuals: { label: '500全个体', count: 500, tanks: 1, helis: 1, planes: 1, vision: 'all', forceIndividuals: true },
  },
});

// 本机冻结原始Weapons表的角度字段；仅使用已核的机械俯仰边界。
CONFIG.weapons.main.minPitch = -10 * Math.PI / 180;
CONFIG.weapons.main.maxPitch = 20 * Math.PI / 180;
CONFIG.weapons.m2.minPitch = -20 * Math.PI / 180;
CONFIG.weapons.m2.maxPitch = 60 * Math.PI / 180;
