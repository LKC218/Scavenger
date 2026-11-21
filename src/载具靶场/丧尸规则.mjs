/** 纯数值战斗：流场、空间查询、扫掠、唯一死亡和防线波次。 */
import {Simulation} from './模拟.mjs';
import {SpatialGrid,clamp,dist,turn,yawTo,wrap} from './地图.mjs';
import {segmentBox} from './寻路.mjs';
import {orbitHere} from './飞行.mjs';
import {DEFENSE_WEAPONS,DEFENSE_MAP,DEFENSE_DEFAULTS,COMBAT} from './防线配置.mjs';
export {DEFENSE_WEAPONS,DEFENSE_MAP};

export function blastDamage(weapon,distance){
  if(distance>weapon.blast)return 0;
  if(distance<=weapon.inner)return weapon.damage;
  return weapon.damage*(1-(1-weapon.edge)*(distance-weapon.inner)/(weapon.blast-weapon.inner));
}
export function steerDirection(current,desired,maximum){
  const dot=clamp(current.x*desired.x+current.y*desired.y+current.z*desired.z,-1,1),angle=Math.acos(dot);
  if(angle<1e-7)return {...desired};
  let tangent={x:desired.x-dot*current.x,y:desired.y-dot*current.y,z:desired.z-dot*current.z},n=Math.hypot(...Object.values(tangent));
  if(n<1e-7){tangent={x:-current.z,y:0,z:current.x};n=Math.hypot(tangent.x,tangent.z);if(n<1e-7){tangent={x:1,y:0,z:0};n=1;}}
  const step=Math.min(angle,maximum),result={};for(const k of ['x','y','z'])result[k]=current[k]*Math.cos(step)+tangent[k]/n*Math.sin(step);return result;
}
/** 垂直圆柱加球端帽，线段连续相交，不依赖模型三角形或显示帧。 */
export function segmentCapsule(a,b,t,pad=.08){
  const r=t.radius+pad,h=t.h,low=Math.min(r,h/2),high=Math.max(low,h-r),dx=b.x-a.x,dz=b.z-a.z,dy=b.y-a.y;
  let first=2;const ox=a.x-t.x,oz=a.z-t.z,A=dx*dx+dz*dz,B=2*(ox*dx+oz*dz),C=ox*ox+oz*oz-r*r;
  if(C<=0&&a.y>=low&&a.y<=high)first=0;
  if(A>1e-12){const disc=B*B-4*A*C;if(disc>=0)for(const s of [-1,1]){const q=(-B+s*Math.sqrt(disc))/(2*A),y=a.y+dy*q;if(q>=0&&q<=1&&y>=low&&y<=high)first=Math.min(first,q);}}
  const length=A+dy*dy;
  for(const cy of [low,high]){const oy=a.y-cy,c=C+oy*oy,bb=B+2*oy*dy;if(c<=0)first=0;if(length>1e-12){const d=bb*bb-4*length*c;if(d>=0){const q=(-bb-Math.sqrt(d))/(2*length);if(q>=0&&q<=1)first=Math.min(first,q);}}}
  return first<=1?first:null;
}

class FlowField{
  constructor(map){
    this.b=COMBAT;this.w=Math.ceil((COMBAT.maxX-COMBAT.minX)/COMBAT.cell);this.h=Math.ceil((COMBAT.maxZ-COMBAT.minZ)/COMBAT.cell);
    const n=this.w*this.h;this.blocked=new Uint8Array(n);this.distance=new Int32Array(n).fill(-1);const queue=new Int32Array(n);let head=0,tail=0;
    for(let i=0;i<n;i++){const p=this.point(i);if(map.obstacles.some(o=>segmentBox({...p,y:1},{...p,y:1},o,.5)!==null))this.blocked[i]=1;}
    for(let i=0;i<n;i++){const p=this.point(i);if(p.z>=COMBAT.line+1&&!this.blocked[i]){this.distance[i]=0;queue[tail++]=i;}}
    while(head<tail){const id=queue[head++],x=id%this.w,z=Math.floor(id/this.w);for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){const xx=x+dx,zz=z+dz,k=zz*this.w+xx;if(xx<0||xx>=this.w||zz<0||zz>=this.h||this.blocked[k]||this.distance[k]>=0)continue;this.distance[k]=this.distance[id]+1;queue[tail++]=k;}}
  }
  index(x,z){return clamp(Math.floor((z-COMBAT.minZ)/COMBAT.cell),0,this.h-1)*this.w+clamp(Math.floor((x-COMBAT.minX)/COMBAT.cell),0,this.w-1);}
  point(i){return{x:COMBAT.minX+(i%this.w+.5)*COMBAT.cell,z:COMBAT.minZ+(Math.floor(i/this.w)+.5)*COMBAT.cell};}
  free(x,z){return x>COMBAT.minX+.5&&x<COMBAT.maxX-.5&&z>COMBAT.minZ+.5&&z<COMBAT.maxZ-.5&&!this.blocked[this.index(x,z)];}
  direction(a){const id=this.index(a.x,a.z),x=id%this.w,z=Math.floor(id/this.w);let best=this.distance[id],result={x:0,z:1};
    // 等价的左右绕行只能确定选一路，不能求和抵消后再撞向正前墙体。
    for(const [dx,dz] of [[0,1],[1,0],[-1,0],[0,-1]]){const xx=x+dx,zz=z+dz;if(xx<0||xx>=this.w||zz<0||zz>=this.h)continue;const next=zz*this.w+xx;if(this.distance[next]>=0&&this.distance[next]<best){best=this.distance[next];result={x:dx,z:dz};}}
    return result;
  }
}

export class DefenseSimulation extends Simulation{
  constructor(options={}){super({...DEFENSE_DEFAULTS,...options,city:true,map:DEFENSE_MAP});}
  reset(){
    this.defense=true;super.reset();this.weapons=DEFENSE_WEAPONS;this.guard=true;this.started=false;this.phase='ready';this.result=null;
    this.integrity=this.options.integrity;this.maxIntegrity=this.integrity;this.wave=0;this.waveSerial=0;this.spawned=0;this.leaked=0;this.damageTotal=0;this.damageByWeapon={};this.deathByWeapon={};this.queries=0;this.candidates=0;this.neighborChecks=0;
    this.zombieGrid=new SpatialGrid(4);this.targetIndex=new Map();this.flow??=new FlowField(this.map);this.heightProfiles??=Array(13).fill(1.75);this.assignments=[];
    this.waves=this.options.waves??[this.options.zombies,Math.round(this.options.zombies*1.5),this.options.zombies*2];
    let tank=0,heli=0;for(const u of this.units){u.guard=true;u.travelSpeed=0;if(u.kind==='tank'){u.x=(tank++%6-2.5)*11;u.z=24+Math.floor((tank-1)/6)*11;u.y=0;}else if(u.kind==='heli'){u.x=28+(heli++%5)*14;u.z=48+Math.floor((heli-1)/5)*18;u.y=35;}else{u.x=-200;u.z=180;u.y=72;orbitHere(u,{x:0,z:160});}}
    for(const u of this.units){u.weaponMode=u.kind==='heli'?'auto':'manual';u.weaponReady={};u.weaponEnabled={gun:true,missile:true,tank:true};u.gunYaw=u.yaw;}
    this.spawnWave();
  }
  spawnWave(){
    const count=clamp(this.waves[this.wave%this.waves.length]??100,1,2000),seed=(this.options.seed+this.wave*313)>>>0;let state=seed;
    const random=()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/4294967296;};
    this.targets=Array.from({length:count},(_,i)=>{const lane=i%2?1:-1,x=lane*(42+random()*22)+(random()-.5)*12,z=-55-Math.floor(i/24)*1.1-random()*13,scale=.95+random()*.1;
      const a={id:`z-${this.wave+1}-${i}`,index:i,name:'丧尸 '+(i+1),x,z,px:x,pz:z,y:.85,baseY:0,vx:0,vz:0,heading:0,phase:random(),scale,alive:true,hp:100,maxHp:100,radius:.32*scale,h:1.75*scale,w:.64*scale,d:.64*scale,deathAt:null,leaked:false};
      if(!this.flow.free(x,z)||this.flow.distance[this.flow.index(x,z)]<0)throw new Error('丧尸出生点不可达');return a;});
    this.spawned+=count;this.waveSerial++;this.reindex();this.emit('wave',{wave:this.wave+1,count});
  }
  setProfiles(assignments,heights){this.assignments=assignments;this.heightProfiles=heights;for(const a of this.targets){a.h=(heights[assignments[a.index]]??1.75)*a.scale;a.y=a.h*.5;a.radius=clamp(a.h*.18,.19,.4);a.w=a.d=a.radius*2;}}
  reindex(){this.zombieGrid.clear();this.targetIndex.clear();for(const a of this.targets){this.targetIndex.set(a.id,a);if(a.hp>0)this.zombieGrid.insert(a);}}
  begin(){if(this.phase==='ready'){this.started=true;this.phase='combat';return true;}return false;}
  target(id){const t=this.targetIndex?.get(id);return t?.hp>0?t:null;}
  setGuard(ids,value){for(const u of this.units)if(ids.includes(u.id)){u.guard=value;if(!value)u.guardTarget=null;}}
  setWeaponEnabled(ids,key,value){for(const u of this.units)if(ids.includes(u.id)&&u.kind==='heli'){u.weaponEnabled[key]=value;u.aim=0;}}
  guardRange(u){return u.kind==='heli'&&u.weaponMode==='auto'?Math.max(...['gun','missile'].filter(k=>u.weaponEnabled[k]).map(k=>this.weapons[k].range),0):this.weapon(u).range;}
  pickGuard(u){const range=this.guardRange(u);let chosen=null,best=Infinity;this.queries++;for(const t of this.zombieGrid.query(u.x,u.z,range)){this.candidates++;const d=dist(u,t);if(t.hp<=0||d>range-2||d>=best||this.nav.blocked(this.muzzle(u),t))continue;chosen=t;best=d;}return chosen;}
  updateUnit(u,dt){
    if(u.kind==='plane'){super.updateUnit(u,dt);return;}
    if(u.job){
      const travel=['move','reverse'].includes(u.job.type);super.updateUnit(u,dt);
      if(!travel||!u.moving||!this.guard||!u.guard)return;
      const movingTarget=this.target(u.guardTarget);let target=movingTarget;
      if(!target||dist(u,target)>this.guardRange(u)-2){if(this.time>=(u.guardSearchAt??0)){target=this.pickGuard(u);u.guardTarget=target?.id;u.guardSearchAt=this.time+.18;}else target=null;}
      if(target){this.engage(u,target,target,dt,false,true);u.state='移动中 · '+u.state;}return;
    }
    u.moving=false;if(!this.guard||!u.guard){u.state='警戒关闭';return;}
    let t=this.target(u.guardTarget);if(!t||dist(u,t)>this.guardRange(u)-2||this.nav.blocked(this.muzzle(u),t)){t=null;if(this.time>=(u.guardSearchAt??0)){t=this.pickGuard(u);u.guardSearchAt=this.time+.18;if(u.guardTarget!==t?.id){u.aim=0;u.burstShot=0;}u.guardTarget=t?.id;}}
    if(t)this.engage(u,t,t,dt);else u.state='警戒待命';
  }
  selectWeapon(u,point){
    if(u.kind!=='heli'||u.weaponMode!=='auto')return;
    const range=dist(u,point),available=['gun','missile'].filter(k=>u.weaponEnabled[k]&&range<=this.weapons[k].range-2);
    // 普通丧尸优先低过量机炮；超机炮射程才使用导弹。此择优策略为网页适配，不冒称原作评分公式。
    const chosen=available[0];if(!chosen||chosen===u.weapon)return;
    u.weaponReady[u.weapon]=u.readyAt;u.weapon=chosen;u.readyAt=u.weaponReady[chosen]??0;u.aim=0;
  }
  engage(u,point,target,dt,ground=false,moving=false){
    this.selectWeapon(u,point);const w=this.weapon(u),range=dist(u,point),desired=yawTo(u,point);
    if(!u.weaponEnabled[u.weapon]){u.state='武器已关闭';u.aim=0;return false;}
    if(range>w.range-2){u.aim=0;u.state='超出射程';if(!moving){this.navigate(u,point,dt);u.state='接近射程';}return false;}
    if(moving&&!w.shootOnMove){u.aim=0;u.state='武器需要停车';return false;}
    if(!moving)u.path=[];
    if(u.kind==='heli'&&u.weapon==='gun'){
      const limit=w.sector*Math.PI/180,relative=wrap(desired-u.yaw);
      if(Math.abs(relative)>limit&&!moving)u.yaw=turn(u.yaw,desired,1.4*dt);
      const direction=u.yaw+clamp(wrap(desired-u.yaw),-limit,limit);u.gunYaw=turn(u.gunYaw??u.yaw,direction,Math.PI/2*dt);
      if(Math.abs(wrap(desired-u.gunYaw))>.055){u.aim=0;u.state=Math.abs(relative)>limit?'目标超出机炮射界':'转向瞄准';return false;}
    }else{
      if(u.kind==='heli'&&!moving)u.yaw=turn(u.yaw,desired,1.4*dt);
      u.turretYaw=turn(u.turretYaw,desired,1.65*dt);
      if(Math.abs(wrap(desired-(u.kind==='tank'?u.turretYaw:u.yaw)))>.055){u.aim=0;u.state='转向瞄准';return false;}
    }
    const pitch=Math.atan2(point.y-(u.y+(u.kind==='tank'?1.83:.88)),Math.max(1,range));
    if(w.lowerPitch!==undefined&&(pitch<w.lowerPitch*Math.PI/180||pitch>w.upperPitch*Math.PI/180)){u.aim=0;u.state='目标超出俯仰射界';return false;}
    u.pitch=u.weapon==='missile'?0:pitch;
    if(this.nav.blocked(this.muzzle(u),point)){u.aim=0;u.state='射线受阻 · 请重新选位';return false;}
    if(u.aimTarget!==target?.id){u.aimTarget=target?.id;u.aim=0;}
    u.aim+=dt;if(this.time<u.readyAt){u.state='装填中';return false;}if(u.aim<w.aim){u.state='捕获目标 · 稳定瞄准';return false;}
    return this.launch(u,point,target)&&ground;
  }
  launch(u,point,target,weapon=u.weapon){
    let aim=point;
    if(target&&weapon!=='missile'&&weapon!=='bomb'){
      const origin=this.muzzle(u,weapon),w=this.weapons[weapon],rx=target.x-origin.x,ry=target.y-origin.y,rz=target.z-origin.z,vx=target.vx??0,vz=target.vz??0;
      const a=vx*vx+vz*vz-w.speed*w.speed,b=2*(rx*vx+rz*vz),c=rx*rx+ry*ry+rz*rz,disc=b*b-4*a*c;
      const roots=Math.abs(a)>1e-8&&disc>=0?[(-b-Math.sqrt(disc))/(2*a),(-b+Math.sqrt(disc))/(2*a)]:[];
      const time=Math.min(5,...roots.filter(t=>t>0),Math.sqrt(c)/w.speed*1.2);
      aim={x:target.x+vx*time,y:target.y,z:target.z+vz*time};
    }
    const previous=this.shots,ok=super.launch(u,aim,target,weapon);if(!ok)return false;
    const p=this.projectiles.at(-1),w=this.weapons[weapon];p.direction={x:p.point.x-p.position.x,y:p.point.y-p.position.y,z:p.point.z-p.position.z};let n=Math.hypot(...Object.values(p.direction));for(const k of ['x','y','z'])p.direction[k]/=n||1;
    if(w.burst){u.burstShot=(u.burstShot??0)+1;if(u.burstShot>=w.burst){u.readyAt=this.time+w.rest;u.burstShot=0;}}
    u.weaponReady??={};u.weaponReady[weapon]=u.readyAt;
    if(weapon==='gun'&&u.burstShot>0)u.aim=this.weapon(u).aim;
    if(this.shots!==previous+1)throw new Error('重复发射计数');return true;
  }
  projectileCandidates(a,b){this.queries++;const rows=this.zombieGrid.segment(a,b,.65);this.candidates+=rows.size;return rows;}
  projectileHit(a,b,t){return segmentCapsule(a,b,t);}
  impact(p,point,hitId,surface='entity'){
    const w=this.weapons[p.weapon],seen=new Set();const rows=this.zombieGrid.query(point.x,point.z,w.blast+.5);const direct=this.target(hitId);if(direct)rows.push(direct);this.queries++;
    const check={...point,y:Math.max(.12,point.y)},previous=p.position??p.origin;
    if(previous){const d=Math.hypot(previous.x-point.x,previous.y-point.y,previous.z-point.z);if(d>.001){check.x+=(previous.x-point.x)/d*.1;check.y=Math.max(.12,check.y+(previous.y-point.y)/d*.1);check.z+=(previous.z-point.z)/d*.1;}}
    for(const t of rows){this.candidates++;if(t.hp<=0||seen.has(t.id))continue;seen.add(t.id);
      const distance=Math.hypot(t.x-point.x,t.y-point.y,t.z-point.z);let amount=blastDamage(w,distance);
      if(amount>0&&this.nav.blocked(check,t,.025))amount=0;
      if(t.id===hitId)amount=Math.max(amount,w.damage);if(amount<=0)continue;
      const actual=Math.min(t.hp,amount);t.hp=Math.max(0,t.hp-amount);t.hitAt=this.time;this.hits++;this.damageTotal+=actual;this.damageByWeapon[p.weapon]=(this.damageByWeapon[p.weapon]??0)+actual;
      if(!t.hp){t.alive=false;t.deathAt=this.time;this.kills++;this.deathByWeapon[p.weapon]=(this.deathByWeapon[p.weapon]??0)+1;this.emit('death',{target:t.id,weapon:p.weapon,position:{x:t.x,y:t.y,z:t.z}});}
    }
    this.emit('impact',{weapon:p.weapon,serial:p.id,owner:p.owner,target:hitId,surface,position:{...point}});
  }
  moveZombies(dt){
    for(const a of this.targets){a.px=a.x;a.pz=a.z;if(a.hp<=0)continue;const v=this.flow.direction(a);let vx=v.x*this.options.speed,vz=v.z*this.options.speed,neighbors=0;
      for(const other of this.zombieGrid.query(a.x,a.z,1.2)){if(other===a||other.hp<=0)continue;const x=a.x-other.x,z=a.z-other.z,d=Math.hypot(x,z),r=a.radius+other.radius+.12;if(d>0&&d<r){const push=(r-d)*2.4;vx+=x/d*push;vz+=z/d*push;if(++neighbors>=16)break;}}
      this.neighborChecks+=neighbors;const n=Math.hypot(vx,vz),limit=this.options.speed*1.4;if(n>limit){vx*=limit/n;vz*=limit/n;}
      a.vx+=(vx-a.vx)*(1-Math.exp(-dt*8));a.vz+=(vz-a.vz)*(1-Math.exp(-dt*8));const x=a.x+a.vx*dt,z=a.z+a.vz*dt;
      if(this.flow.free(x,z)){a.x=x;a.z=z;}else if(this.flow.free(x,a.z))a.x=x;else if(this.flow.free(a.x,z))a.z=z;
      a.y=a.h*.5;if(Math.hypot(a.vx,a.vz)>.1)a.heading=Math.atan2(a.vx,a.vz);
      if(a.z>=COMBAT.line){a.hp=0;a.alive=false;a.leaked=true;a.deathAt=this.time;this.leaked++;this.integrity=Math.max(0,this.integrity-1);this.emit('leak',{target:a.id});}
    }
  }
  /** 导弹有有限转向；非制导弹离膛后直线飞行到落点，不追随目标。 */
  guideProjectile(p,dt){
    const t=this.target(p.target);if(t)p.point={x:t.x,y:t.y,z:t.z};
    const desired={x:p.point.x-p.position.x,y:p.point.y-p.position.y,z:p.point.z-p.position.z},n=Math.hypot(...Object.values(desired));
    if(n>0){for(const k of ['x','y','z'])desired[k]/=n;p.direction=steerDirection(p.direction,desired,2.1*dt);}
  }
  step(dt){
    if(this.paused||!this.started||this.result)return;
    this.time+=dt;this.pathBudget=2;this.waitingPaths=0;this.queries=this.candidates=this.neighborChecks=0;
    if(this.phase==='interval'&&this.time>=this.nextWaveAt){this.wave++;this.spawnWave();this.phase='combat';}
    this.moveZombies(dt);this.reindex();this.unitGrid.clear();for(const u of this.units)if(u.kind!=='plane')this.unitGrid.insert(u);
    for(const u of this.units)this.updateUnit(u,dt);this.advanceProjectiles(dt);
    if(this.integrity<=0){this.result='defeat';this.emit('result',{result:this.result});}
    else if(this.phase==='combat'&&!this.targets.some(t=>t.hp>0)){
      if(this.wave===this.waves.length-1&&!this.options.endless){this.result='victory';this.emit('result',{result:this.result});}
      else{this.phase='interval';this.nextWaveAt=this.time+this.options.interval;}
    }
  }
  stats(){const alive=this.targets.filter(t=>t.hp>0).length;return{wave:this.wave+1,waveCount:this.waves.length,spawned:this.spawned,alive,killed:this.kills,leaked:this.leaked,integrity:this.integrity,phase:this.phase,result:this.result,
    conserved:this.spawned===alive+this.kills+this.leaked,damage:this.damageTotal,damageByWeapon:{...this.damageByWeapon},queries:this.queries,candidates:this.candidates,neighbors:this.neighborChecks,corpses:this.targets.filter(t=>t.hp<=0&&!t.leaked&&this.time-t.deathAt<3).length};}
}
