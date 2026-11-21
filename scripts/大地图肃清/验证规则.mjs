/** 纯Node规则技术检验，不创建服务、不访问资源库、不代替画面和人工验收。 */
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { Simulation, calculateMuzzle } from '../../src/大地图肃清/规则.mjs';
import { CONFIG } from '../../src/大地图肃清/配置.mjs';
import { createWorld } from '../../src/大地图肃清/样本场景.mjs';

const results=[];
function test(name,fn){const begin=performance.now();try{fn();results.push({name,ok:true,ms:+(performance.now()-begin).toFixed(3)});}catch(error){results.push({name,ok:false,error:error.stack});}}
const close=(actual,expected,tolerance=1e-6)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} 应接近 ${expected}`);
const advance=(sim,seconds)=>{const n=Math.round(seconds/CONFIG.step);for(let i=0;i<n;i++)sim.step(CONFIG.step);};
function configFixture(){
  const config=structuredClone(CONFIG);
  // 标明这是规则夹具炮口；不把简化坐标当作冻结模型测量结果。
  for(const id of ['main','m2'])config.weapons[id].muzzle={verified:true,fixture:true,position:{x:0,y:1,z:-1},pivot:{x:0,y:1,z:0},pitchPivot:{x:0,y:1,z:0}};
  return config;
}
function fixture({config=configFixture(),preset='behavior',tank={x:0,y:0,z:0}}={}){
  const sim=new Simulation({config,world:createWorld()});sim.reset({preset,count:0,seed:721});
  const u=sim._state.units.find(u=>u.kind==='tank');Object.assign(u.position,tank);u.groundY=tank.y;u.yaw=0;
  sim.drainEvents();let commandId=0;
  return{sim,u,command:(type,extra={})=>sim.dispatch({runId:sim.snapshot().runId,commandId:++commandId,unitIds:[u.id],type,...extra})};
}
function addZombie(sim,{id=`fixture-${sim._allZombies.length+1}`,x=0,y=0,z=-30,hp=100,home='A',band='near'}={}){
  const g={id:`fixture-group-${id}`,center:{x,y,z},home:{x,y,z},homeZoneId:home,members:[],band,noiseId:null,targetPoint:null,path:[],pathKey:null,searchUntil:0};
  const enemy={id,modelId:'model:ba-walker-lod2',homeZoneId:home,position:{x,y,z},yaw:0,phase:.137,behaviorState:'idle',animationState:'idle',stateSince:0,hp,maxHp:hp,alive:true,deathAt:null,visible:false,groupId:g.id,spawnBand:band,targetId:null,lastTargetPoint:null,searchUntil:0,nextContactAt:0,wanderAt:1e9,wanderPoint:null,routeIndex:0};
  g.members.push(enemy);sim._groups.push(g);sim._allZombies.push(enemy);sim._byId.set(id,enemy);sim._state.counts.total++;sim._perceive();sim._syncPublicZombies();sim._updateCounts();return enemy;
}
function enable(f,id,ammo){assert.ok(f.command('selectAmmo',{weaponId:id,ammoId:ammo}).ok);assert.ok(f.command('enableWeapon',{weaponId:id,enabled:true}).ok);}
function digest(sim){const s=sim.snapshot();return{time:s.time,units:s.units.map(u=>({id:u.id,p:[u.position.x,u.position.y,u.position.z],hp:u.hp,alive:u.alive,ammo:Object.fromEntries(Object.entries(u.weapons).map(([id,w])=>[id,{...w.ammo}]))})),enemies:sim._allZombies.map(z=>[z.id,z.position.x,z.position.y,z.position.z,z.hp,z.alive,z.homeZoneId]),counts:JSON.parse(JSON.stringify(s.counts))};}

test('首场手选与只读实时视图',()=>{
  const {sim,u}=fixture();for(const w of Object.values(u.weapons)){assert.equal(w.enabled,false);assert.equal(w.ammoId,null);}assert.equal(u.guardEnabled,true);
  const view=sim.snapshot();assert.equal(view,sim.snapshot());assert.throws(()=>view.units[0].hp=999,TypeError);assert.throws(()=>view.units.push({}),TypeError);sim.step(CONFIG.step);close(view.time,CONFIG.step);
});
test('命令去重、本轮身份、重开及增量事件',()=>{
  const {sim,u}=fixture();const runId=sim.snapshot().runId;
  const command={runId,commandId:8,unitIds:[u.id],type:'guard',enabled:false};assert.ok(sim.dispatch(command).ok);assert.equal(sim.dispatch(command).duplicate,true);
  assert.equal(sim.snapshot().diagnostics.commandsAccepted,1);sim.reset({preset:'behavior',count:0});assert.notEqual(sim.snapshot().runId,runId);assert.equal(sim.dispatch(command).ok,false);
  const events=sim.drainEvents();assert.ok(events.every(e=>e.runId===sim.snapshot().runId));assert.equal(new Set(events.map(e=>e.seq)).size,events.length);assert.deepEqual(sim.drainEvents(),[]);
});
test('未知支撑不补零；地车绕墙并保持合法半径',()=>{
  const {sim,u,command}=fixture({tank:{x:0,y:0,z:0}});
  assert.equal(command('move',{point:{x:250,z:240}}).ok,false);assert.equal(command('move',{point:{x:-210,z:180}}).ok,false);assert.equal(command('move',{point:{x:25,z:0}}).ok,false);
  assert.ok(command('move',{point:{x:55,z:0}}).ok);
  for(let i=0;i<900;i++){sim.step(CONFIG.step);assert.ok(sim.world.isWalkable(u.position.x,u.position.z,CONFIG.tank.radius));assert.ok(u.speed<=8+1e-8);}
  assert.ok(Math.hypot(u.position.x-55,u.position.z)<=1.5);close(u.groundY,sim.world.querySupport(u.position.x,u.position.z).height);
});
test('地车加速及转向上限',()=>{
  const {sim,u,command}=fixture({tank:{x:-100,y:0,z:-100}});assert.ok(command('move',{point:{x:-100,z:-200}}).ok);sim.step(CONFIG.step);close(u.speed,4/30);close(u.position.z,-100-4/900);
  assert.ok(command('move',{point:{x:-50,z:-100}}).ok);const yaw=u.yaw;sim.step(CONFIG.step);assert.ok(Math.abs(u.yaw-yaw)<=Math.PI/90+1e-8);
});
test('位置前态只供显示插值且对象复用',()=>{
  const {sim,u,command}=fixture({tank:{x:-100,y:0,z:-100}});const z=addZombie(sim,{x:-100,z:-80});
  command('move',{point:{x:-100,z:-200}});const before={...u.position};sim.step(CONFIG.step);assert.deepEqual({...u.previousPosition},before);const previousRef=u.previousPosition;
  const publicZombie=sim.snapshot().zombies.find(record=>record.id===z.id);assert.ok(publicZombie.previousPosition);const publicPrevious=publicZombie.previousPosition;
  const position={...u.position};sim.step(CONFIG.step);assert.equal(u.previousPosition,previousRef);assert.deepEqual({...u.previousPosition},position);assert.equal(publicZombie.previousPosition,publicPrevious);assert.throws(()=>publicZombie.previousPosition.x=90,TypeError);
});
test('直升机高度与固定翼持续盘旋',()=>{
  const {sim}=fixture();const h=sim._state.units.find(u=>u.kind==='heli'),p=sim._state.units.find(u=>u.kind==='plane');
  const command={runId:sim.snapshot().runId,commandId:900,unitIds:[h.id],type:'altitude',mode:'work'};assert.ok(sim.dispatch(command).ok);const y=h.position.y;sim.step(CONFIG.step);close(y-h.position.y,6/30);advance(sim,5);close(h.position.y-h.groundY,12);
  let traveled=0;for(let i=0;i<1800;i++){const before={...p.position};sim.step(CONFIG.step);const d=Math.hypot(p.position.x-before.x,p.position.z-before.z);assert.ok(d>1.49&&d<1.51);traveled+=d;close(p.position.y,100);}assert.ok(traveled>2699);
});
test('低主探索真实空间构型、外观各半和画外接触结算',()=>{
  for(const preset of ['low','main','explore']){
    const sim=new Simulation({world:createWorld()});sim.reset({preset});const view=sim.snapshot(),expected=CONFIG.presets[preset].distribution;
    assert.equal(view.units.length,10);assert.equal(view.zombies.length,CONFIG.presets[preset].count);assert.deepEqual(Object.values(view.counts.initialDistribution),expected);
    const bands={near:[],far:[],offscreen:[]};for(const z of view.zombies)bands[z.spawnBand].push(Math.hypot(z.position.x+70,z.position.z+30));assert.ok(Math.max(...bands.near)<100);assert.ok(Math.min(...bands.far)>160);assert.ok(Math.min(...bands.offscreen)>250);
    assert.ok(Math.min(...view.zombies.filter(z=>z.spawnBand==='far').map(z=>Math.hypot(z.position.x,z.position.z)))>CONFIG.render.far);
    const models=view.zombies.reduce((m,z)=>(m[z.modelId]=(m[z.modelId]??0)+1,m),{});assert.equal(Object.values(models)[0],Object.values(models)[1]);assert.ok(new Set(view.zombies.map(z=>z.phase)).size>view.zombies.length*.99);
    const offscreen=sim._state.units.find(u=>u.id==='tank-7'),hp=offscreen.hp;advance(sim,1.2);assert.ok(offscreen.hp<hp);assert.ok(view.counts.fixtureOffscreenCombat>0);assert.equal(view.counts.alive+view.counts.dead,view.counts.total);
  }
});
test('近车优先、家区稳定、接触外无隔空伤害',()=>{
  const f=fixture();const z=addZombie(f.sim,{x:0,z:-20,home:'B'});assert.ok(f.command('bait',{point:{x:-50,z:-20},unitIds:[]}).ok);advance(f.sim,5.2);assert.equal(z.targetId,f.u.id);assert.equal(z.homeZoneId,'B');close(f.u.hp,1200);
  z.position={x:0,y:0,z:-5.4};z.wanderAt=1e9;f.sim._perceive();const before=f.u.hp;advance(f.sim,1.2);close(before-f.u.hp,2);assert.equal(z.homeZoneId,'B');
});
test('诱饵5秒启动、30秒发声、近接耐久和搜索12秒',()=>{
  const f=fixture({tank:{x:-250,y:0,z:-250}});const z=addZombie(f.sim,{x:90,z:0});assert.ok(f.command('bait',{point:{x:120,z:0},unitIds:[]}).ok);
  advance(f.sim,4.9);assert.equal(f.sim.snapshot().noises.some(n=>n.kind==='bait'),false);advance(f.sim,.2);assert.equal(f.sim.snapshot().noises.some(n=>n.kind==='bait'),true);assert.equal(f.sim.drainEvents().filter(e=>e.type==='baitStarted').length,1);
  advance(f.sim,30);assert.equal(f.sim.snapshot().noises.some(n=>n.kind==='bait'),false);assert.equal(z.behaviorState,'search');const until=f.sim._groups[0].searchUntil;assert.ok(until>f.sim.snapshot().time);advance(f.sim,12.2);assert.notEqual(z.behaviorState,'search');
  const b=fixture({tank:{x:-250,y:0,z:-250}});addZombie(b.sim,{x:100,z:0});b.command('bait',{point:{x:100,z:0},unitIds:[]});advance(b.sim,6.2);assert.ok(b.sim.snapshot().baits[0].hp<50);
});
test('尸群绕墙受支撑且归属不随跨区变化',()=>{
  const f=fixture({tank:{x:-250,y:0,z:-250}}),z=addZombie(f.sim,{x:0,z:0,home:'A'});f.command('bait',{point:{x:60,z:0},unitIds:[]});
  let maxZ=0;for(let i=0;i<1800;i++){f.sim.step(CONFIG.step);assert.ok(f.sim.world.isWalkable(z.position.x,z.position.z,CONFIG.zombie.radius));maxZ=Math.max(maxZ,Math.abs(z.position.z));}assert.ok(maxZ>45);assert.equal(z.homeZoneId,'A');
});
test('实际AP弹丸单体、友军不拦弹、死亡唯一',()=>{
  const f=fixture();const one=addZombie(f.sim,{z:-30}),two=addZombie(f.sim,{z:-35});const friend=f.sim._state.units.find(u=>u.kind==='heli');friend.position={x:0,y:1,z:-15};
  enable(f,'main','ap');f.command('guard',{enabled:false});assert.ok(f.command('attackGround',{point:{x:0,z:-45}}).ok);advance(f.sim,1.5);
  assert.equal(one.alive,false);assert.equal(two.hp,100);assert.equal(friend.hp,friend.maxHp);assert.equal(f.u.weapons.main.ammo.ap,3);
  const events=f.sim.drainEvents();assert.equal(events.filter(e=>e.type==='zombieDied'&&e.targetId===one.id).length,1);assert.ok(events.some(e=>e.type==='impact'&&Math.abs(e.position.z-one.position.z)<=CONFIG.zombie.radius+.01));
});
test('HEAT三维2/4.5/7/圈外伤害、隔墙和直击不叠满',()=>{
  const f=fixture({tank:{x:-100,y:0,z:-100}}),point={x:25,y:1,z:0};
  const enemies=[2,4.5,7,7.01].map((d,i)=>addZombie(f.sim,{id:`ring-${i}`,x:25+d,y:1-CONFIG.zombie.height/2,z:0,hp:1000}));
  const above=addZombie(f.sim,{id:'above',x:25,y:1+4.5-CONFIG.zombie.height/2,z:0,hp:1000});
  f.sim._impact({id:'fixture-heat',sourceId:f.u.id,weaponId:'main',ammoId:'heat'},point,enemies[0],'wall-main');
  [180,112.5,45,0].forEach((damage,i)=>close(1000-enemies[i].hp,damage));close(1000-above.hp,112.5);
});
test('真实墙体首次命中点与墙后HEAT范围伤',()=>{
  const f=fixture({tank:{x:0,y:0,z:0}}),enemy=addZombie(f.sim,{x:28,z:0,hp:1000});
  enable(f,'main','heat');f.command('guard',{enabled:false});f.command('attackGround',{point:{x:40,z:0}});advance(f.sim,2);
  const impact=f.sim.drainEvents().find(e=>e.type==='impact');assert.ok(impact);close(impact.position.x,23);assert.equal(impact.result.obstacleId,'wall-main');assert.ok(enemy.hp<1000);
});
test('主炮共享装填、不自动切弹、M2独立并行与每发扣弹',()=>{
  const f=fixture();addZombie(f.sim,{z:-40,hp:100000});enable(f,'main','ap');enable(f,'m2','ball');advance(f.sim,.8);
  const shots=f.sim.drainEvents().filter(e=>e.type==='weaponFired');assert.ok(shots.some(e=>e.weaponId==='main'));assert.ok(shots.some(e=>e.weaponId==='m2'));
  const ready=f.u.weapons.main.readyAt;f.command('selectAmmo',{weaponId:'main',ammoId:'heat'});close(f.u.weapons.main.readyAt,ready);advance(f.sim,1);assert.equal(f.u.weapons.main.ammo.heat,8);
  const main=f.u.weapons.main.ammo.heat;f.command('enableWeapon',{weaponId:'m2',enabled:false});advance(f.sim,3);assert.ok(f.u.weapons.main.ammo.heat<main);const m2=f.u.weapons.m2.ammo.ball;advance(f.sim,2);assert.equal(f.u.weapons.m2.ammo.ball,m2);
  f.u.weapons.main.ammo.heat=0;advance(f.sim,4);assert.equal(f.u.weapons.main.ammoId,'heat');assert.equal(f.u.weapons.main.ammo.ap,3);
});
test('M2五发组节奏、30发箱、6秒换箱与120总量守恒',()=>{
  const f=fixture();addZombie(f.sim,{z:-40,hp:100000});enable(f,'m2','ball');advance(f.sim,45);
  const shots=f.sim.drainEvents().filter(e=>e.type==='weaponFired'&&e.weaponId==='m2');assert.equal(shots.length,120);assert.equal(f.u.weapons.m2.ammo.ball,0);assert.equal(f.u.weapons.m2.magazine,0);
  close(shots[1].time-shots[0].time,.1);close(shots[5].time-shots[4].time,.6);close(shots[30].time-shots[29].time,6);assert.equal(shots.length+f.u.weapons.m2.ammo.ball,120);
});
test('视野锁定、射前失联、既发弹不追踪及旧记录不泄漏',()=>{
  const f=fixture(),enemy=addZombie(f.sim,{x:0,z:-60});enable(f,'main','ap');f.command('guard',{enabled:false});assert.ok(f.command('attack',{targetId:enemy.id}).ok);
  const oldRecord=f.sim.snapshot().zombies.find(z=>z.id===enemy.id);const oldPoint={...oldRecord.position};enemy.position={x:80,y:0,z:0};f.sim._perceive();f.sim._syncPublicZombies();advance(f.sim,1);assert.equal(f.u.weapons.main.ammo.ap,4);assert.equal(f.u.manualTargetId,null);assert.deepEqual({...oldRecord.position},oldPoint);assert.equal(oldRecord.visible,false);assert.ok(!f.sim.snapshot().zombies.some(z=>z.id===enemy.id));assert.equal(f.command('attack',{targetId:enemy.id}).ok,false);
  const g=fixture(),e=addZombie(g.sim,{x:0,z:-70});enable(g,'main','ap');g.command('guard',{enabled:false});g.command('attack',{targetId:e.id});advance(g.sim,.5);assert.equal(g.u.weapons.main.ammo.ap,3);const original={...g.sim.snapshot().projectiles[0].direction};e.position.x=80;g.sim._perceive();advance(g.sim,.1);assert.deepEqual({...g.sim.snapshot().projectiles[0].direction},original);advance(g.sim,1);assert.equal(e.hp,100);
});
test('未核炮口、机械俯仰射界和真实转轴',()=>{
  const config=configFixture();config.weapons.main.muzzle.verified=false;const f=fixture({config});addZombie(f.sim,{z:-40});enable(f,'main','ap');advance(f.sim,2);assert.equal(f.u.weapons.main.ammo.ap,4);assert.match(f.u.weapons.main.reason,/炮口/);
  const g=fixture();enable(g,'main','ap');g.command('attackGround',{point:{x:0,z:-2}});advance(g.sim,2);assert.equal(g.u.weapons.main.ammo.ap,4);assert.match(g.u.weapons.main.reason,/俯仰/);
  const h=fixture({config:structuredClone(CONFIG)});const before=calculateMuzzle(h.u,'main',h.sim.config);h.u.turretYaw=Math.PI/2;const after=calculateMuzzle(h.u,'main',h.sim.config);assert.ok(Math.abs(after.x-before.x)>5);assert.notEqual(after.z,before.z);
});
test('耐久归零永久损失、尸体预算与时限',()=>{
  const f=fixture();const z=addZombie(f.sim,{z:-5});f.u.hp=2;advance(f.sim,1.2);assert.equal(f.u.alive,false);assert.equal(f.command('move',{point:{x:-50,z:0}}).ok,false);advance(f.sim,2);assert.equal(f.sim.drainEvents().filter(e=>e.type==='unitDestroyed').length,1);
  const g=fixture();const dead=addZombie(g.sim,{z:-30});g.sim._damageZombie(dead,120,{sourceId:g.u.id,weaponId:'main',ammoId:'ap'});advance(g.sim,18.5);assert.ok(dead.opacity>0&&dead.opacity<1);advance(g.sim,1.6);assert.equal(dead.remainsVisible,false);
});
test('静音和表现裁剪不改变玩法、固定步分块输入一致',()=>{
  const a=fixture(),b=fixture();for(const f of [a,b]){addZombie(f.sim,{z:-40,hp:10000});enable(f,'main','heat');enable(f,'m2','ball');}
  a.sim.config.audio.volume=0;a.sim.config.render.effectLimit=0;a.sim.config.render.corpseLimit=0;
  advance(a.sim,8);for(let i=0;i<80;i++)b.sim.step(.1);assert.deepEqual(digest(a.sim),digest(b.sim));assert.equal(a.sim.snapshot().diagnostics.debtSeconds,0);assert.equal(b.sim.snapshot().diagnostics.debtSeconds,0);
});

const performanceRuns=[];
for(const preset of ['low','main','explore','individuals']){
  const sim=new Simulation({world:createWorld()});sim.reset({preset,seed:1105});const samples=[];
  for(let i=0;i<600;i++){const begin=performance.now();sim.step(CONFIG.step);samples.push(performance.now()-begin);}
  samples.sort((a,b)=>a-b);performanceRuns.push({preset,steps:600,simulatedSeconds:sim.snapshot().time,total:sim.snapshot().counts.total,alive:sim.snapshot().counts.alive,dead:sim.snapshot().counts.dead,p95Ms:+samples[Math.floor(samples.length*.95)].toFixed(3),maxMs:+samples.at(-1).toFixed(3),debtSeconds:sim.snapshot().diagnostics.debtSeconds});
}
const failed=results.filter(r=>!r.ok);
console.log(JSON.stringify({scope:'纯规则技术测试；非浏览器整帧或人工验收',passed:results.length-failed.length,failed:failed.length,results,performanceRuns},null,2));
if(failed.length)process.exitCode=1;
