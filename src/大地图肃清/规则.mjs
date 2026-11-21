/** V001受控场景规则。只有本模块拥有可变玩法状态；表现和交互读取实时只读视图。 */
import { CONFIG } from './配置.mjs';
import { KINDS, NATIVE_MODELS, validCommand, clamp, distanceXZ, wrapAngle, yawTo, turnToward } from './契约.mjs';

const EPS = 1e-8;
let runSerial = 0;
const copyPoint = p => ({ x:p.x, y:p.y, z:p.z });
const dist3 = (a,b) => Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z);
const rotateY = (p,a) => ({x:p.x*Math.cos(a)+p.z*Math.sin(a), y:p.y, z:-p.x*Math.sin(a)+p.z*Math.cos(a)});
function aroundY(p,pivot,a) { const q=rotateY({x:p.x-pivot.x,y:p.y-pivot.y,z:p.z-pivot.z},a); return{x:q.x+pivot.x,y:q.y+pivot.y,z:q.z+pivot.z}; }
function aroundX(p,pivot,a) { const y=p.y-pivot.y,z=p.z-pivot.z;return{x:p.x,y:pivot.y+y*Math.cos(a)-z*Math.sin(a),z:pivot.z+y*Math.sin(a)+z*Math.cos(a)}; }
function randomGenerator(seed) { let n=Number(seed)>>>0; return()=>{ n+=0x6D2B79F5;let t=n;t=Math.imul(t^t>>>15,t|1);t^=t+Math.imul(t^t>>>7,t|61);return((t^t>>>14)>>>0)/4294967296; }; }

/** 从已核炮口和转轴计算真实三维位置，不提供车体中心回退。角度相对车体。 */
export function calculateMuzzle(unit,weaponId,config=CONFIG) {
  const m=config.weapons[weaponId]?.muzzle;
  if(!m?.verified || !m.position || !m.pivot)return null;
  if(![m.position,m.pivot,m.pitchPivot??m.pivot].every(p=>['x','y','z'].every(k=>Number.isFinite(p[k]))))return null;
  let p=aroundX(m.position,m.pitchPivot??m.pivot,weaponId==='main'?unit.gunPitch:unit.m2Pitch);
  if(weaponId==='main')p=aroundY(p,m.pivot,unit.turretYaw);
  else {
    p=aroundY(p,m.pivot,unit.m2Yaw-unit.turretYaw);
    const mainPivot=config.weapons.main.muzzle?.pivot;
    if(mainPivot)p=aroundY(p,mainPivot,unit.turretYaw);
    else p=aroundY(p,m.pivot,unit.turretYaw);
  }
  const q=rotateY(p,unit.yaw);
  return{x:unit.position.x+q.x,y:unit.position.y+q.y,z:unit.position.z+q.z};
}

/** 线段对轴对齐受击盒，返回首次实际进入点比例。 */
function segmentBox(a,b,box) {
  let lo=0,hi=1;
  for(const k of ['x','y','z']){
    const d=b[k]-a[k];
    if(Math.abs(d)<EPS){if(a[k]<box[k][0]||a[k]>box[k][1])return null;continue;}
    let x=(box[k][0]-a[k])/d,y=(box[k][1]-a[k])/d;if(x>y)[x,y]=[y,x];
    lo=Math.max(lo,x);hi=Math.min(hi,y);if(lo>hi)return null;
  }
  return lo>=0&&lo<=1?lo:null;
}
function readonlyView(value,cache=new WeakMap()) {
  if(value===null||typeof value!=='object')return value;
  if(cache.has(value))return cache.get(value);
  const proxy=new Proxy(value,{
    get:(target,key)=>readonlyView(Reflect.get(target,key),cache),
    set(){throw new TypeError('规则视图只读');},deleteProperty(){throw new TypeError('规则视图只读');},
    defineProperty(){throw new TypeError('规则视图只读');},setPrototypeOf(){throw new TypeError('规则视图只读');},
  });cache.set(value,proxy);return proxy;
}

class SpatialGrid {
  constructor(size=16){this.size=size;this.cells=new Map();}
  key(x,z){return `${Math.floor(x/this.size)},${Math.floor(z/this.size)}`;}
  rebuild(items){this.cells.clear();for(const item of items){if(!item.alive)continue;const key=this.key(item.position.x,item.position.z);let list=this.cells.get(key);if(!list)this.cells.set(key,list=[]);list.push(item);}}
  query(x,z,r,out=[]){out.length=0;const s=this.size;for(let ix=Math.floor((x-r)/s);ix<=Math.floor((x+r)/s);ix++)for(let iz=Math.floor((z-r)/s);iz<=Math.floor((z+r)/s);iz++){const list=this.cells.get(`${ix},${iz}`);if(list)out.push(...list);}return out;}
}

export class Simulation {
  constructor({config=CONFIG,world}={}) {
    if(!world?.querySupport||!world?.segmentHit||!world?.isWalkable)throw new TypeError('需要合法世界支撑和碰撞接口');
    this.config=config;this.world=world;this._grid=new SpatialGrid();this._query=[];this._events=[];
    this.reset({preset:'interaction',seed:config.seed});
  }

  reset({preset='interaction',seed=this.config.seed,count}={}) {
    if(!this.config.presets[preset])throw new RangeError(`未知场景：${preset}`);
    const spec=this.config.presets[preset],n=count??spec.count;
    if(!Number.isSafeInteger(n)||n<0||n>this.config.presets.explore.count)throw new RangeError('尸群数量超出本批范围');
    this._random=randomGenerator(seed);this._seenCommands=new Set();this._byId=new Map();this._groups=[];this._allZombies=[];
    this._accumulator=0;this._nextPerception=this.config.perceptionStep;this._seq=0;this._projectileSeq=0;this._baitSeq=0;this._events=[];this._lastSeen=new Map();this._publicZombies=new Map();
    this._state={runId:`肃清-${seed}-${++runSerial}`,tick:0,time:0,preset,seed,units:[],zombies:[],projectiles:[],baits:[],noises:[],heardGroups:[],lastSeen:[],
      counts:{total:n,alive:n,dead:0,visibleAlive:0,visibleDead:0,hidden:0,byZone:{},byHome:{},initialDistribution:{near:0,far:0,offscreen:0},distribution:{near:0,far:0,offscreen:0},offscreenCombat:0,fixtureOffscreenCombat:0},
      diagnostics:{fixedSteps:0,perceptionUpdates:0,debtSeconds:0,maxDebtSeconds:0,commandsAccepted:0,commandsRejected:0,duplicateCommands:0,shots:0,damageEvents:0,physicsCandidates:0,navigationFailures:0}};
    this._spawnUnits(spec);this._spawnZombies(spec,n);this._grid.rebuild(this._allZombies);this._perceive();this._syncPublicZombies();this._updateCounts();
    this._view=readonlyView(this._state);this._event('reset',null,null,null,{preset,seed,count:n});return this.snapshot();
  }

  snapshot(){return this._view;}
  drainEvents(){const events=this._events;this._events=[];return events;}
  _event(type,sourceId,targetId,position,result={},weaponId=null,ammoId=null){const s=this._state;this._events.push({runId:s.runId,seq:++this._seq,tick:s.tick,time:s.time,type,sourceId,targetId,position:position?copyPoint(position):null,weaponId,ammoId,result});}
  _reject(command,reason){this._state.diagnostics.commandsRejected++;this._event('commandRejected',null,null,null,{commandId:command?.commandId,reason});return{ok:false,reason,runId:this._state.runId};}

  dispatch(command) {
    if(!validCommand(command,this._state.runId))return this._reject(command,'命令格式或本轮ID无效');
    if(this._seenCommands.has(command.commandId)){this._state.diagnostics.duplicateCommands++;return{ok:false,duplicate:true,reason:'命令已执行',runId:this._state.runId};}
    this._seenCommands.add(command.commandId);
    if(command.type==='bait'){
      const p=this._groundPoint(command.point);if(!p.ok)return this._reject(command,p.reason);
      const id=`bait-${++this._baitSeq}`,now=this._state.time;
      this._state.baits.push({id,position:p.point,hp:this.config.bait.hp,maxHp:this.config.bait.hp,alive:true,startsAt:now+this.config.bait.delay,endsAt:now+this.config.bait.delay+this.config.bait.duration,started:false});
      this._state.diagnostics.commandsAccepted++;return{ok:true,runId:this._state.runId};
    }
    const units=command.unitIds.map(id=>this._byId.get(id));
    if(!units.length||units.some(u=>!u?.alive||!u.kind))return this._reject(command,'单位不存在或已永久损失');
    let point=null;
    if(['move','attackGround'].includes(command.type)){const p=this._groundPoint(command.point);if(!p.ok)return this._reject(command,p.reason);point=p.point;}
    if(command.type==='attack'){
      const target=this._byId.get(command.targetId);
      if(!target?.alive||target.kind||!target.visible)return this._reject(command,'只能锁定当前可见敌人');
      if(!units.some(u=>u.domain==='ground'&&this._canSee(u,target)))return this._reject(command,'目标不在所选地面单位视野内');
    }
    if(['selectAmmo','enableWeapon'].includes(command.type)){
      const w=this.config.weapons[command.weaponId];if(!w||units.some(u=>u.kind!=='tank'))return this._reject(command,'所选单位不具备该武器');
      if(command.type==='selectAmmo'&&!w.allowedAmmo.includes(command.ammoId))return this._reject(command,'武器不接受该弹种');
      if(command.type==='enableWeapon'&&typeof command.enabled!=='boolean')return this._reject(command,'武器开关值无效');
    }
    if(command.type==='guard'&&typeof command.enabled!=='boolean')return this._reject(command,'警戒开关值无效');
    if(command.type==='altitude'&&(!['cruise','work'].includes(command.mode)||units.some(u=>u.kind!=='heli')))return this._reject(command,'仅直升机支持巡航或作业高度');
    if(command.type==='altitude'&&command.mode==='work'&&units.some(u=>!this.world.isWalkable(u.position.x,u.position.z,1)))return this._reject(command,'当前位置不属于合法作业空地');
    const routes=new Map();
    if(command.type==='move')for(const u of units)if(u.domain==='ground'){
      if(!this.world.isWalkable(point.x,point.z,this.config.tank.radius))return this._reject(command,'目标地面不可通行');
      const route=this._route(u.position,point,this.config.tank.radius);if(!route)return this._reject(command,'找不到受支撑且可通行的路径');routes.set(u.id,route);
    }
    for(const u of units){
      switch(command.type){
        case'move':u.destination=copyPoint(point);u.route=routes.get(u.id)??[];u.moveState='moving';u.manualTargetId=null;u.manualGround=null;break;
        case'stop':if(u.kind!=='plane'){u.destination=null;u.route=[];u.speed=0;u.moveState='idle';}u.manualTargetId=null;u.manualGround=null;break;
        case'attack':u.manualTargetId=command.targetId;u.manualGround=null;break;
        case'attackGround':u.manualGround=copyPoint(point);u.manualTargetId=null;break;
        case'selectAmmo':u.weapons[command.weaponId].ammoId=command.ammoId;break;
        case'enableWeapon':u.weapons[command.weaponId].enabled=command.enabled;break;
        case'guard':u.guardEnabled=command.enabled;break;
        case'altitude':u.altitudeMode=command.mode;break;
      }
    }
    this._state.diagnostics.commandsAccepted++;return{ok:true,runId:this._state.runId};
  }

  _groundPoint(p){
    if(!p||!Number.isFinite(p.x)||!Number.isFinite(p.z))return{ok:false,reason:'需要有限地面坐标'};
    const support=this.world.querySupport(p.x,p.z);
    if(support.status!=='ready')return{ok:false,reason:support.status==='pending'?'地面尚未加载':'地面没有合法支撑'};
    if(!Number.isFinite(support.height))return{ok:false,reason:'地面支撑高度无效'};
    return{ok:true,point:{x:p.x,y:support.height,z:p.z}};
  }

  _spawnUnits(spec){
    const c=this.config;
    const spots=[[-70,-30],[-110,-80],[-130,-120],[-90,-140],[-160,-120],[-160,40],[150,225],[220,-180]];
    for(const [kind,amount] of [['tank',spec.tanks],['heli',spec.helis],['plane',spec.planes]])for(let i=0;i<amount;i++){
      const [x,z]=kind==='tank'?spots[i%spots.length]:spots[0],support=this._groundPoint({x,z});
      if(!support.ok)throw new Error(`样本出生地无支撑：${kind}`);
      const hp=kind==='tank'?c.tank.hp:1;
      const makeWeapon=(id)=>({enabled:false,ammoId:null,ammo:Object.fromEntries(c.weapons[id].allowedAmmo.map(a=>[a,c.ammunition[a].initial])),magazine:id==='m2'?c.weapons.m2.magazineSize:0,readyAt:0,targetId:null,aimAt:null,reason:'未选择弹种',aimReadyAt:0,aimKey:null,burstShots:0,reloadPending:false});
      const u={id:`${kind}-${i+1}`,kind,modelId:KINDS[kind],domain:kind==='tank'?'ground':'air',position:support.point,groundY:support.point.y,yaw:0,hp,maxHp:hp,alive:true,moveState:kind==='plane'?'flying':'idle',guardEnabled:true,altitudeMode:'cruise',turretYaw:0,gunPitch:0,m2Yaw:0,m2Pitch:0,
        weapons:kind==='tank'?{main:makeWeapon('main'),m2:makeWeapon('m2')}:{},speed:0,destination:null,route:[],manualTargetId:null,manualGround:null,deathAt:null,visible:true};
      if(kind==='heli')u.position.y+=c.heli.cruise;
      if(kind==='plane'){u.position.y=(this.world.maxObstacleHeight??Math.max(...this.world.obstacles.map(o=>o.maxY)))+c.plane.clearance;u.orbitCenter={x:x+c.plane.orbitRadius,y:u.position.y,z};u.orbitAngle=Math.PI;u.destination=copyPoint(u.orbitCenter);u.speed=c.plane.speed;}
      u.previousPosition=copyPoint(u.position);
      this._state.units.push(u);this._byId.set(u.id,u);
    }
  }

  _spawnZombies(spec,count){
    let distribution=spec.distribution??[count,0,0];
    if(count!==spec.count&&spec.distribution){const a=Math.floor(count*distribution[0]/spec.count),b=Math.floor(count*distribution[1]/spec.count);distribution=[a,b,count-a-b];}
    const bands=['near','far','offscreen'],combat=Math.min(distribution[2],[32,128,256][['low','main','explore'].indexOf(this._state.preset)]??0);
    let ordinal=0;
    for(let band=0;band<3;band++){
      let left=distribution[band],inBand=0;
      while(left>0){
        const size=Math.min(left,band===2&&inBand<combat?Math.min(combat-inBand,32):this._groups.length%7===6?96:24+Math.floor(this._random()*17));
        const index=this._groups.length,angle=this._random()*Math.PI*2;
        let center;
        if(band===0){const r=35+this._random()*35;center={x:-70+Math.cos(angle)*r,y:0,z:-30+Math.sin(angle)*r};}
        else if(band===1)center={x:125+this._random()*75,y:0,z:-150+this._random()*40};
        else if(inBand<combat)center={x:150+Math.cos(angle)*5.5,y:0,z:225+Math.sin(angle)*5.5};
        else center={x:180+this._random()*40,y:0,z:225+this._random()*30};
        center=this._safeSpawn(center,.4);
        const zone=this.world.zones.reduce((best,z)=>!best||distanceXZ(center,z.center)<distanceXZ(center,best.center)?z:best,null);
        const group={id:`group-${index+1}`,center:copyPoint(center),home:copyPoint(center),homeZoneId:zone?.id??'样本区',members:[],band:bands[band],noiseId:null,targetPoint:null,path:[],pathKey:null,searchUntil:0};
        this._groups.push(group);
        for(let j=0;j<size;j++){
          const a=this._random()*Math.PI*2,r=Math.sqrt(this._random())*(band===2&&inBand<combat?1.5:7);
          const p=this._safeSpawn({x:center.x+Math.cos(a)*r,z:center.z+Math.sin(a)*r},this.config.zombie.radius);
          const z={id:`zombie-${++ordinal}`,modelId:NATIVE_MODELS[(ordinal-1)%NATIVE_MODELS.length],homeZoneId:group.homeZoneId,position:p,yaw:this._random()*Math.PI*2,phase:this._random(),behaviorState:'idle',animationState:'idle',stateSince:0,hp:this.config.zombie.hp,maxHp:this.config.zombie.hp,alive:true,deathAt:null,visible:false,
            groupId:group.id,spawnBand:bands[band],targetId:null,lastTargetPoint:null,searchUntil:0,nextContactAt:0,wanderAt:this._random()*4,wanderPoint:null,routeIndex:0};
          z.previousPosition=copyPoint(z.position);
          group.members.push(z);this._allZombies.push(z);this._byId.set(z.id,z);
        }
        inBand+=size;left-=size;
      }
    }
  }
  _safeSpawn(p,radius){
    for(let i=0;i<100;i++){
      const x=clamp(p.x+(i?Math.cos(i*2.4)*i:0),this.world.bounds.minX+radius+1,this.world.bounds.maxX-radius-1),z=clamp(p.z+(i?Math.sin(i*2.4)*i:0),this.world.bounds.minZ+radius+1,this.world.bounds.maxZ-radius-1);
      const s=this._groundPoint({x,z});if(s.ok&&this.world.isWalkable(x,z,radius))return s.point;
    }
    throw new Error('无法找到合法样本出生点');
  }

  step(dt){
    if(!Number.isFinite(dt)||dt<0)throw new RangeError('步长必须是有限非负秒数');
    this._accumulator+=dt;const fixed=this.config.step,d=this._state.diagnostics;
    d.maxDebtSeconds=Math.max(d.maxDebtSeconds,this._accumulator);
    while(this._accumulator+EPS>=fixed){this._fixed(fixed);this._accumulator=Math.max(0,this._accumulator-fixed);}
    if(this._accumulator<EPS)this._accumulator=0;
    d.debtSeconds=this._accumulator;return this.snapshot();
  }
  _fixed(dt){
    const s=this._state;s.tick++;s.time=s.tick*this.config.step;s.diagnostics.fixedSteps++;
    for(const u of s.units)Object.assign(u.previousPosition??(u.previousPosition=copyPoint(u.position)),u.position);
    for(const z of this._allZombies)Object.assign(z.previousPosition??(z.previousPosition=copyPoint(z.position)),z.position);
    this._moveUnits(dt);this._updateNoises();
    if(s.time+EPS>=this._nextPerception){this._perceive();this._nextPerception=s.time+this.config.perceptionStep;}
    this._moveZombies(dt);this._updateWeapons(dt);this._moveProjectiles(dt);this._expireRemains();this._syncPublicZombies();this._updateCounts();
  }

  _clearSegment(a,b,radius){
    if(this.world.segmentBlocked(a,b,radius))return false;
    const length=distanceXZ(a,b),steps=Math.max(1,Math.ceil(length/2));
    for(let i=1;i<=steps;i++){const t=i/steps;if(!this.world.isWalkable(a.x+(b.x-a.x)*t,a.z+(b.z-a.z)*t,radius))return false;}
    return true;
  }
  _route(a,b,radius){
    if(this._clearSegment(a,b,radius))return[copyPoint(b)];
    const nodes=[copyPoint(a),copyPoint(b)],padding=radius+.5;
    for(const o of this.world.obstacles)for(const [sx,sz] of [[-1,-1],[-1,1],[1,-1],[1,1]]){
      const p={x:o.x+sx*(o.width/2+padding),z:o.z+sz*(o.depth/2+padding)},s=this._groundPoint(p);if(s.ok&&this.world.isWalkable(p.x,p.z,radius))nodes.push(s.point);
    }
    const costs=nodes.map(()=>Infinity),prev=nodes.map(()=>-1),done=new Set();costs[0]=0;
    for(let iteration=0;iteration<nodes.length;iteration++){
      let best=-1;for(let i=0;i<nodes.length;i++)if(!done.has(i)&&(best<0||costs[i]<costs[best]))best=i;
      if(best<0||!Number.isFinite(costs[best]))break;if(best===1){const route=[];for(let i=1;i!==0;i=prev[i])route.unshift(nodes[i]);return route;}
      done.add(best);
      for(let i=1;i<nodes.length;i++)if(!done.has(i)&&this._clearSegment(nodes[best],nodes[i],radius)){
        const next=costs[best]+distanceXZ(nodes[best],nodes[i]);if(next<costs[i]){costs[i]=next;prev[i]=best;}
      }
    }
    this._state.diagnostics.navigationFailures++;return null;
  }

  _moveUnits(dt){
    const c=this.config;
    for(const u of this._state.units){
      if(!u.alive)continue;
      if(u.kind==='plane'){
        const center=u.destination??u.orbitCenter,r=distanceXZ(u.position,center);
        if(r>c.plane.orbitRadius+2){const yaw=yawTo(u.position,center);u.yaw=yaw;u.position.x-=Math.sin(yaw)*c.plane.speed*dt;u.position.z-=Math.cos(yaw)*c.plane.speed*dt;u.moveState='flying';}
        else {
          if(u.moveState!=='orbiting'){u.orbitCenter=copyPoint(center);u.orbitAngle=Math.atan2(u.position.z-center.z,u.position.x-center.x);u.moveState='orbiting';}
          u.orbitAngle+=c.plane.speed/c.plane.orbitRadius*dt;
          u.position.x=u.orbitCenter.x+Math.cos(u.orbitAngle)*c.plane.orbitRadius;u.position.z=u.orbitCenter.z+Math.sin(u.orbitAngle)*c.plane.orbitRadius;
          u.yaw=yawTo(u.position,{x:u.position.x-Math.sin(u.orbitAngle),z:u.position.z+Math.cos(u.orbitAngle)});
        }
        u.position.y=this.world.maxObstacleHeight+c.plane.clearance;const support=this.world.querySupport(u.position.x,u.position.z);u.groundY=support.status==='ready'?support.height:null;continue;
      }
      if(u.kind==='heli'){
        const previous=copyPoint(u.position);
        if(u.destination){const d=distanceXZ(u.position,u.destination),amount=Math.min(d,c.heli.speed*dt);if(d>EPS){u.yaw=yawTo(u.position,u.destination);u.position.x+=(u.destination.x-u.position.x)/d*amount;u.position.z+=(u.destination.z-u.position.z)/d*amount;}if(d<=c.heli.speed*dt){u.destination=null;u.moveState='idle';}}
        const support=this.world.querySupport(u.position.x,u.position.z);
        if(support.status==='ready'){
          u.groundY=support.height;let desired=support.height+(u.altitudeMode==='work'?c.heli.work:c.heli.cruise);
          const ahead={x:u.position.x-Math.sin(u.yaw)*c.heli.speed*3,z:u.position.z-Math.cos(u.yaw)*c.heli.speed*3};
          for(const o of this.world.obstacles)if((Math.abs(u.position.x-o.x)<o.width/2+c.heli.clearance&&Math.abs(u.position.z-o.z)<o.depth/2+c.heli.clearance)||(Math.abs(ahead.x-o.x)<o.width/2+c.heli.clearance&&Math.abs(ahead.z-o.z)<o.depth/2+c.heli.clearance))desired=Math.max(desired,o.maxY+c.heli.clearance);
          u.position.y+=clamp(desired-u.position.y,-c.heli.climb*dt,c.heli.climb*dt);
          // 实际穿越前若爬升不足，水平运动退回安全边缘，不能穿进障碍净空。
          for(const o of this.world.obstacles)if(Math.abs(u.position.x-o.x)<o.width/2&&Math.abs(u.position.z-o.z)<o.depth/2&&u.position.y<o.maxY+c.heli.clearance-EPS){u.position.x=previous.x;u.position.z=previous.z;u.moveState='climbing';}
        }else {u.position.x=previous.x;u.position.z=previous.z;u.moveState=support.status==='pending'?'pending':'unsupported';}
        continue;
      }
      if(!u.destination){u.speed=Math.max(0,u.speed-c.tank.acceleration*dt);continue;}
      while(u.route.length&&distanceXZ(u.position,u.route[0])<=(u.route.length>1?.1:c.tank.stopRadius))u.route.shift();
      const target=u.route[0]??u.destination,d=distanceXZ(u.position,target);
      if(!u.route.length&&d<=c.tank.stopRadius){u.destination=null;u.speed=0;u.moveState='idle';continue;}
      const wanted=yawTo(u.position,target);u.yaw=turnToward(u.yaw,wanted,c.tank.turnRate*dt);
      const alignment=Math.max(0,Math.cos(wrapAngle(wanted-u.yaw)));u.speed=Math.min(c.tank.speed,u.speed+c.tank.acceleration*dt);
      const amount=Math.min(d,u.speed*dt*alignment),next={x:u.position.x+(target.x-u.position.x)/d*amount,z:u.position.z+(target.z-u.position.z)/d*amount};
      const support=this._groundPoint(next);
      if(!support.ok||!this._clearSegment(u.position,support.point,c.tank.radius)){u.speed=0;u.moveState='blocked';continue;}
      Object.assign(u.position,support.point);u.groundY=support.point.y;u.moveState='moving';
    }
  }

  _updateNoises(){
    const s=this._state,now=s.time;s.noises=s.noises.filter(n=>n.expiresAt>now&&!n.continuous);
    for(const u of s.units)if(u.alive&&u.kind==='tank')s.noises.push({id:`engine-${u.id}`,sourceId:u.id,kind:'engine',position:copyPoint(u.position),range:u.speed>.1?this.config.tank.engineRange:this.config.tank.idleRange,expiresAt:now+this.config.step*2,continuous:true});
    for(const b of s.baits){
      if(!b.alive)continue;if(now+EPS>=b.endsAt){b.alive=false;continue;}
      if(now+EPS>=b.startsAt){if(!b.started){b.started=true;this._event('baitStarted',b.id,null,b.position,{endsAt:b.endsAt});}s.noises.push({id:`noise-${b.id}`,sourceId:b.id,kind:'bait',position:copyPoint(b.position),range:this.config.bait.range,expiresAt:b.endsAt,continuous:true});}
    }
  }
  _canSee(u,z){return u.alive&&u.domain==='ground'&&distanceXZ(u.position,z.position)<=this.config.tank.vision&&this.world.lineClear({x:u.position.x,y:u.position.y+this.config.tank.height,z:u.position.z},this._hitCenter(z));}
  _hitCenter(z){return{x:z.position.x,y:z.position.y+this.config.zombie.height/2,z:z.position.z};}
  _perceive(){
    const s=this._state,now=s.time,ground=s.units.filter(u=>u.alive&&u.domain==='ground'),all=this.config.presets[s.preset].vision==='all';
    s.diagnostics.perceptionUpdates++;this._grid.rebuild(this._allZombies);
    for(const group of this._groups){
      let x=0,y=0,z=0,n=0;for(const member of group.members)if(member.alive){x+=member.position.x;y+=member.position.y;z+=member.position.z;n++;}
      if(n)group.center={x:x/n,y:y/n,z:z/n};
      let chosen=null,best=-Infinity;
      for(const noise of s.noises){const distance=distanceXZ(group.center,noise.position);if(distance>noise.range)continue;let score=1-distance/noise.range;if(noise.id===group.noiseId)score+=1e-5;if(score>best){best=score;chosen=noise;}}
      if(chosen){group.noiseId=chosen.id;group.targetPoint=copyPoint(chosen.position);group.searchUntil=0;}
      else if(group.noiseId){group.noiseId=null;group.searchUntil=now+this.config.zombie.searchTime;}
      if(!chosen&&now>=group.searchUntil)group.targetPoint=null;
      const target=group.targetPoint??group.home,key=`${Math.round(target.x/3)},${Math.round(target.z/3)}`;
      if(key!==group.pathKey){group.path=this._route(group.center,target,this.config.zombie.radius)??[];group.pathKey=key;for(const member of group.members)member.routeIndex=0;}
    }
    const visible=[];
    for(const z of this._allZombies){
      z.visible=all||ground.some(u=>this._canSee(u,z));
      if(z.visible){let record=this._publicZombies.get(z.id);if(!record){record={id:z.id,position:copyPoint(z.position),previousPosition:copyPoint(z.previousPosition??z.position)};this._publicZombies.set(z.id,record);}visible.push(record);this._lastSeen.set(z.id,{id:z.id,modelId:z.modelId,homeZoneId:z.homeZoneId,position:copyPoint(z.position),time:now,alive:z.alive});}
      else {const record=this._publicZombies.get(z.id);if(record)record.visible=false;}
      if(!z.alive)continue;
      let nearby=null,best=this.config.zombie.detectRange;
      for(const u of ground){const d=distanceXZ(z.position,u.position);if(d<=best&&this.world.lineClear(this._hitCenter(z),{x:u.position.x,y:u.position.y+1,z:u.position.z})){nearby=u;best=d;}}
      if(nearby){z.targetId=nearby.id;z.lastTargetPoint=copyPoint(nearby.position);z.searchUntil=0;}
      else if(z.targetId){z.targetId=null;z.searchUntil=now+this.config.zombie.searchTime;}
      if(z.searchUntil&&now>=z.searchUntil){z.searchUntil=0;z.lastTargetPoint=null;}
    }
    s.zombies=visible;s.lastSeen=[...this._lastSeen.values()].filter(record=>!this._byId.get(record.id)?.visible);
    s.heardGroups=this._groups.filter(g=>g.members.some(z=>z.alive&&!z.visible)).flatMap(g=>{
      const listener=ground.reduce((best,u)=>!best||distanceXZ(u.position,g.center)<distanceXZ(best.position,g.center)?u:best,null);
      if(!listener||distanceXZ(listener.position,g.center)>this.config.tank.vision)return[];
      const angle=yawTo(listener.position,g.center),sector=((Math.round(angle/(Math.PI/4))%8)+8)%8;
      return[{id:g.id,listenerId:listener.id,homeZoneId:g.homeZoneId,sector,bearing:sector*Math.PI/4,time:now,intensity:'群体'}];
    });
  }

  _syncPublicZombies(){
    // 公开对象身份稳定；失去视野后停止同步，旧引用也不能继续追踪隐敌。
    for(const record of this._state.zombies){const z=this._byId.get(record.id);if(!z?.visible)continue;
      Object.assign(record,{modelId:z.modelId,homeZoneId:z.homeZoneId,yaw:z.yaw,phase:z.phase,behaviorState:z.behaviorState,animationState:z.animationState,stateSince:z.stateSince,hp:z.hp,maxHp:z.maxHp,alive:z.alive,deathAt:z.deathAt,visible:true,remainsVisible:z.remainsVisible,opacity:z.opacity,spawnBand:z.spawnBand});
      Object.assign(record.position,z.position);
      Object.assign(record.previousPosition,z.previousPosition??z.position);
    }
  }

  _setBehavior(z,state){if(z.behaviorState!==state){z.behaviorState=state;z.stateSince=this._state.time;}z.animationState=state==='attack'?'attack':state==='idle'?'idle':state==='dead'?'death':'walk';}
  _moveZombies(dt){
    const c=this.config.zombie,now=this._state.time;
    for(const group of this._groups)for(const z of group.members){
      if(!z.alive)continue;
      let target=this._byId.get(z.targetId),destination=null,state='idle';
      if(target?.alive){
        destination=target.position;state='chase';
        const distance=dist3(this._hitCenter(z),{x:target.position.x,y:target.position.y+c.height/2,z:target.position.z});
        const edge=this.config.tank.radius+c.radius+c.contactRange;
        if(distance<=edge&&this.world.lineClear(this._hitCenter(z),{x:target.position.x,y:target.position.y+1,z:target.position.z})){
          state='attack';destination=null;
          if(!z.nextContactAt)z.nextContactAt=now+c.contactInterval;
          if(now+EPS>=z.nextContactAt){this._damageUnit(target,c.contactDamage,z.id);z.nextContactAt+=c.contactInterval;}
        }else z.nextContactAt=0;
      }else{
        z.nextContactAt=0;
        if(z.searchUntil>now&&z.lastTargetPoint){destination=z.lastTargetPoint;state='search';}
        else if(group.targetPoint){destination=group.targetPoint;state=group.noiseId?'chase':'search';}
        else if(distanceXZ(z.position,group.home)>c.wanderRadius){destination=group.home;state='return';}
        else {
          if(now>=z.wanderAt){const a=this._random()*Math.PI*2,r=this._random()*c.wanderRadius;const p=this._groundPoint({x:group.home.x+Math.cos(a)*r,z:group.home.z+Math.sin(a)*r});z.wanderPoint=p.ok&&this.world.isWalkable(p.point.x,p.point.z,c.radius)?p.point:null;z.wanderAt=now+4+this._random()*8;}
          if(z.wanderPoint&&distanceXZ(z.position,z.wanderPoint)>1){destination=z.wanderPoint;state='wander';}
        }
      }
      // 发声诱饵的接触者独立消耗耐久，未启动诱饵不提前引导。
      if(!target&&group.noiseId?.startsWith('noise-bait-')){
        const bait=this._state.baits.find(b=>`noise-${b.id}`===group.noiseId&&b.alive);
        if(bait&&distanceXZ(z.position,bait.position)<=c.radius+c.contactRange){destination=null;state='attack';if(!z.nextBaitContactAt)z.nextBaitContactAt=now+c.contactInterval;if(now+EPS>=z.nextBaitContactAt){bait.hp=Math.max(0,bait.hp-c.contactDamage);z.nextBaitContactAt+=c.contactInterval;if(!bait.hp)bait.alive=false;}}
        else z.nextBaitContactAt=0;
      }
      if(destination){
        let goal=destination;
        if(group.path.length&&this.world.segmentBlocked(z.position,destination,c.radius)){
          while(z.routeIndex<group.path.length-1&&distanceXZ(z.position,group.path[z.routeIndex])<.1)z.routeIndex++;
          goal=group.path[z.routeIndex]??destination;
        }
        const d=distanceXZ(z.position,goal);
        if(d>EPS){const amount=Math.min(d,c.speed*dt),next={x:z.position.x+(goal.x-z.position.x)/d*amount,z:z.position.z+(goal.z-z.position.z)/d*amount},support=this._groundPoint(next);
          if(support.ok&&this.world.isWalkable(next.x,next.z,c.radius)&&!this.world.segmentBlocked(z.position,support.point,c.radius)){z.yaw=yawTo(z.position,goal);Object.assign(z.position,support.point);}
          else {
            // 局部受阻时复用组路线；不穿墙、也不以高度0补齐未知地面。
            const key=`${Math.round(destination.x/3)},${Math.round(destination.z/3)}`;
            if(group.pathKey!==key||!group.path.length){group.path=this._route(z.position,destination,c.radius)??[];group.pathKey=key;z.routeIndex=0;}
            state='idle';
          }
        }
      }
      this._setBehavior(z,state);
    }
  }

  _targetFor(u,w,cfg){
    if(u.manualTargetId){const z=this._byId.get(u.manualTargetId);if(z?.alive&&this._canSee(u,z))return{target:z,point:this._hitCenter(z),key:z.id};u.manualTargetId=null;w.targetId=null;w.aimAt=null;w.aimKey=null;w.reason='目标失联，锁定已取消';return null;}
    if(u.manualGround)return{target:null,point:u.manualGround,key:`ground:${u.manualGround.x},${u.manualGround.y},${u.manualGround.z}`};
    if(!u.guardEnabled)return null;
    let best=null,dist=cfg.range;
    for(const z of this._grid.query(u.position.x,u.position.z,cfg.range+.6,this._query))if(z.alive&&this._canSee(u,z)){const d=distanceXZ(u.position,z.position);if(d<dist){best=z;dist=d;}}
    return best?{target:best,point:this._hitCenter(best),key:best.id}:null;
  }
  _updateWeapons(dt){
    const now=this._state.time;
    for(const u of this._state.units)if(u.alive&&u.kind==='tank')for(const id of ['main','m2']){
      const w=u.weapons[id],cfg=this.config.weapons[id];
      if(w.reloadPending&&now+EPS>=w.readyAt){w.magazine=Math.min(cfg.magazineSize,w.ammo.ball);w.reloadPending=false;}
      if(!w.enabled){w.reason='武器未启用';continue;}if(!w.ammoId){w.reason='未选择弹种';continue;}
      if(!calculateMuzzle(u,id,this.config)){w.reason='炮口或转轴尚未核实，禁止发射';continue;}
      if(w.ammo[w.ammoId]<=0){w.reason='所选弹种已耗尽';continue;}
      const selected=this._targetFor(u,w,cfg);
      if(!selected){if(w.reason!=='目标失联，锁定已取消')w.reason=u.guardEnabled?'没有当前可见目标':'警戒关闭，等待手动目标';continue;}
      w.targetId=selected.target?.id??null;w.aimAt=copyPoint(selected.point);
      if(w.aimKey!==selected.key){w.aimKey=selected.key;w.aimReadyAt=now+cfg.aim;w.burstShots=0;}
      const muzzle=calculateMuzzle(u,id,this.config),desiredYaw=wrapAngle(yawTo(u.position,selected.point)-u.yaw),pitch=Math.atan2(selected.point.y-muzzle.y,distanceXZ(muzzle,selected.point));
      if(id==='main'){u.turretYaw=turnToward(u.turretYaw,desiredYaw,cfg.turnRate*dt);u.gunPitch=pitch;}
      else {u.m2Yaw=turnToward(u.m2Yaw,desiredYaw,cfg.turnRate*dt);u.m2Pitch=pitch;}
      if(cfg.minPitch!==undefined&&(pitch<cfg.minPitch||pitch>cfg.maxPitch)){w.reason='目标超出俯仰射界';continue;}
      if(distanceXZ(u.position,selected.point)>cfg.range){w.reason='目标超出射程';continue;}
      if(now+EPS<w.readyAt){w.reason=w.reloadPending?'正在换箱':'正在装填';continue;}
      if(now+EPS<w.aimReadyAt||Math.abs(wrapAngle((id==='main'?u.turretYaw:u.m2Yaw)-desiredYaw))>.01){w.reason='正在瞄准';continue;}
      if(id==='m2'&&w.magazine===0){w.readyAt=now+cfg.reload;w.reloadPending=true;w.reason='正在换箱';continue;}
      this._fire(u,id,w,cfg,selected);
    }
  }
  _fire(u,id,w,cfg,selected){
    const origin=calculateMuzzle(u,id,this.config),distance=dist3(origin,selected.point);if(distance<EPS)return;
    w.ammo[w.ammoId]--;if(id==='m2')w.magazine--;
    const direction={x:(selected.point.x-origin.x)/distance,y:(selected.point.y-origin.y)/distance,z:(selected.point.z-origin.z)/distance};
    const p={id:`projectile-${++this._projectileSeq}`,sourceId:u.id,weaponId:id,ammoId:w.ammoId,position:origin,direction,speed:cfg.speed,remaining:distance,targetPoint:copyPoint(selected.point),targetId:selected.target?.id??null};
    this._state.projectiles.push(p);const now=this._state.time;
    if(id==='main')w.readyAt=now+cfg.reload;
    else {w.burstShots++;if(!w.magazine&&w.ammo.ball>0){w.readyAt=now+cfg.reload;w.reloadPending=true;w.burstShots=0;}else if(w.burstShots>=cfg.burst){w.burstShots=0;w.readyAt=now+cfg.burstInterval;}else w.readyAt=now+cfg.shotInterval;}
    w.reason='已发射';this._state.diagnostics.shots++;
    this._state.noises.push({id:`shot-${p.id}`,sourceId:u.id,kind:'weapon',position:copyPoint(origin),range:cfg.soundRange,expiresAt:now+1,continuous:false});
    this._event('weaponFired',u.id,selected.target?.id??null,origin,{projectileId:p.id,destination:copyPoint(selected.point)},id,w.ammoId);
  }

  _moveProjectiles(dt){
    const survivors=[],c=this.config.zombie;
    for(const p of this._state.projectiles){
      const a=p.position,amount=Math.min(p.remaining,p.speed*dt),b={x:a.x+p.direction.x*amount,y:a.y+p.direction.y*amount,z:a.z+p.direction.z*amount};
      const wall=this.world.segmentHit(a,b);let best=wall?.t??Infinity,target=null;
      const candidates=this._grid.query((a.x+b.x)/2,(a.z+b.z)/2,distanceXZ(a,b)/2+c.radius+.8,this._query);
      this._state.diagnostics.physicsCandidates+=candidates.length;
      for(const z of candidates)if(z.alive){const t=segmentBox(a,b,{x:[z.position.x-c.radius,z.position.x+c.radius],y:[z.position.y,z.position.y+c.height],z:[z.position.z-c.radius,z.position.z+c.radius]});if(t!==null&&t<best){best=t;target=z;}}
      if(best!==Infinity){const point={x:a.x+(b.x-a.x)*best,y:a.y+(b.y-a.y)*best,z:a.z+(b.z-a.z)*best};this._impact(p,point,target,wall&&!target?wall.obstacleId:null);}
      else if(p.remaining<=amount+EPS)this._impact(p,b,null,null);
      else {p.position=b;p.remaining-=amount;survivors.push(p);}
    }
    this._state.projectiles=survivors;
  }
  _impact(p,point,direct,obstacleId){
    const ammo=this.config.ammunition[p.ammoId];let hits=0,kills=0;
    if(ammo.outer>0){
      for(const z of this._grid.query(point.x,point.z,ammo.outer+.8,this._query))if(z.alive){
        const distance=dist3(point,this._hitCenter(z));if(distance>ammo.outer+EPS)continue;
        const damage=distance<=ammo.inner?ammo.damage:ammo.damage*(1-(1-ammo.edge)*(distance-ammo.inner)/(ammo.outer-ammo.inner));
        if(damage>0){const alive=z.alive;this._damageZombie(z,damage,p);hits++;if(alive&&!z.alive)kills++;}
      }
    }else if(direct?.alive){this._damageZombie(direct,ammo.damage,p);hits=1;if(!direct.alive)kills=1;}
    this._event('impact',p.sourceId,direct?.visible?direct.id:null,point,{projectileId:p.id,obstacleId,hits,kills,direct:Boolean(direct)},p.weaponId,p.ammoId);
  }
  _damageZombie(z,damage,p){
    if(!z.alive)return;z.hp=Math.max(0,z.hp-damage);this._state.diagnostics.damageEvents++;
    if(!z.hp){z.alive=false;z.deathAt=this._state.time;this._setBehavior(z,'dead');this._event('zombieDied',p.sourceId,z.id,z.visible?z.position:null,{damage,homeZoneId:z.homeZoneId},p.weaponId,p.ammoId);}
  }
  _damageUnit(u,damage,sourceId){
    if(!u.alive)return;u.hp=Math.max(0,u.hp-damage);this._state.diagnostics.damageEvents++;
    if(!u.hp){u.alive=false;u.deathAt=this._state.time;u.moveState='destroyed';u.destination=null;u.speed=0;for(const w of Object.values(u.weapons)){w.enabled=false;w.reason='载具已永久损失';}this._event('unitDestroyed',sourceId,u.id,u.position,{permanent:true});}
  }
  _expireRemains(){
    const now=this._state.time,r=this.config.render;
    const corpses=this._allZombies.filter(z=>!z.alive&&z.deathAt!==null).sort((a,b)=>b.deathAt-a.deathAt);
    corpses.forEach((z,i)=>{z.remainsVisible=i<r.corpseLimit&&now-z.deathAt<r.corpseLife;z.opacity=z.remainsVisible?clamp((r.corpseLife-(now-z.deathAt))/r.corpseFade,0,1):0;});
    const wrecks=this._state.units.filter(u=>!u.alive).sort((a,b)=>b.deathAt-a.deathAt);
    wrecks.forEach((u,i)=>{u.remainsVisible=i<r.wreckLimit&&now-u.deathAt<r.wreckLife;u.opacity=u.remainsVisible?clamp((r.wreckLife-(now-u.deathAt))/r.wreckFade,0,1):0;});
  }
  _updateCounts(){
    const counts=this._state.counts;counts.alive=0;counts.dead=0;counts.visibleAlive=0;counts.visibleDead=0;counts.byZone={};counts.distribution={near:0,far:0,offscreen:0};counts.initialDistribution={near:0,far:0,offscreen:0};counts.offscreenCombat=0;
    for(const z of this._allZombies){let zone=counts.byZone[z.homeZoneId];if(!zone)counts.byZone[z.homeZoneId]=zone={total:0,alive:0,dead:0};zone.total++;counts.initialDistribution[z.spawnBand]++;if(z.alive){counts.alive++;zone.alive++;counts.distribution[z.spawnBand]++;if(z.visible)counts.visibleAlive++;if(z.spawnBand==='offscreen'&&z.behaviorState==='attack')counts.offscreenCombat++;}else {counts.dead++;zone.dead++;if(z.visible)counts.visibleDead++;}}
    counts.hidden=counts.alive-counts.visibleAlive;counts.byHome=counts.byZone;counts.fixtureOffscreenCombat=counts.offscreenCombat;
  }
}
