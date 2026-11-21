/** 单机规则：导航、交战、固定翼和连续弹丸均不依赖DOM或Three.js。 */
import {CLASSIC,CITY,CLASSIC_OBSTACLES,SpatialGrid,clamp,dist,wrap,yawTo,turn} from './地图.mjs';
import {navigatorFor,findPath,segmentBox} from './寻路.mjs';
import {FLIGHT,orbitHere,updateFlight} from './飞行.mjs';
export {findPath,segmentBox};
export const OBSTACLES=CLASSIC_OBSTACLES,BOUNDS=CLASSIC.bounds;
export {wrap,yawTo};
export const blocked=(a,b)=>navigatorFor(CLASSIC).blocked(a,b);
export const WEAPONS={
  tank:{name:'120毫米主炮',range:52,reload:3.2,aim:.45,speed:85,damage:180,blast:4,audio:'tank'},
  gun:{name:'30毫米机炮',range:43,reload:.30,aim:.25,speed:105,damage:22,blast:1,audio:'heli'},
  missile:{name:'制导导弹',range:74,reload:6,aim:.65,speed:32,damage:290,blast:5,audio:'missile'},
  planeGun:{name:'A-10C机炮',range:240,reload:.10,aim:0,speed:360,damage:28,blast:1.8,audio:'plane'},
  bomb:{name:'普通炸弹',range:320,reload:6,aim:0,speed:52,damage:680,blast:17,audio:'bomb'}
};
export class Simulation{
  constructor(options={}){this.options=options;this.reset();}
  reset(count){
    this.map=this.options.map??(this.options.city?CITY:CLASSIC);this.nav=navigatorFor(this.map);
    this.total=count===undefined?(this.options.total??(this.options.city?30:10)):(this.options.city?count:count*2);
    this.time=0;this.paused=false;this.projectiles=[];this.events=[];this.shots=0;this.hits=0;this.kills=0;this.serial=0;this.loadMode='manual';this.recoverTargets=false;
    this.unitGrid=new SpatialGrid(18);this.pathBudget=2;this.waitingPaths=0;this.units=[];
    const city=this.map!==CLASSIC;
    const planeCount=city?Math.max(1,Math.round(this.total*.04)):0;
    const heliCount=city?Math.round(this.total*.16):this.total/2;
    const counts=this.options.composition??{tank:this.total-heliCount-planeCount,heli:heliCount,plane:planeCount};
    for(const [kind,n] of Object.entries(counts))for(let i=0;i<n;i++){
      const columns=city?Math.min(12,Math.ceil(Math.sqrt(n*1.6))):5,spacing=kind==='tank'?(this.map.spawnSpacing??12):20;
      const x=city?(i%columns-(columns-1)/2)*spacing:-38+i*16;
      const z=city?(kind==='tank'?55:kind==='heli'?230:370)+Math.floor(i/columns)*spacing:(kind==='tank'?37:62);
      const u={id:kind+'-'+(i+1),kind,name:({tank:'铁砧',heli:'游隼',plane:'雷霆'})[kind]+' '+String(i+1).padStart(2,'0'),x,z,y:kind==='tank'?0:kind==='heli'?(city?35:11):FLIGHT.altitude+(i%3)*10,
        yaw:0,turretYaw:0,pitch:0,bank:0,weapon:kind==='tank'?'tank':kind==='heli'?'gun':'planeGun',queue:[],job:null,path:[],state:kind==='plane'?'盘旋待命':'待命',moving:false,readyAt:0,aim:0,blockedTime:0,revision:0,launchSide:-1,nextAuto:0};
      if(this.map.heightAt){const spawn=this.freePoint({x,z:kind==='tank'?75+Math.floor(i/columns)*spacing:kind==='heli'?170:0});u.x=spawn.x;u.z=spawn.z;u.y=kind==='tank'?this.groundHeight(u.x,u.z):kind==='heli'?this.map.safeAltitude-35+(i%3)*8:this.map.safeAltitude+(i%3)*10;}
      if(kind==='plane')orbitHere(u,{x:u.x+110,z:u.z});this.units.push(u);
    }
    this.targets=this.map.targets.map(([x,z],i)=>this.makeTarget(x,z,i));
    if(this.map.heightAt&&!this.targets.length)this.targets=[[-180,30],[-50,30],[90,30],[190,30]].map(([x,z],i)=>{const p=this.freePoint({x,z});return this.makeTarget(p.x,p.z,i);});
    this.events.push({type:'reset'});
  }
  groundHeight(x,z){return this.map.heightAt?this.map.heightAt(x,z):0;}
  makeTarget(x,z,i){const baseY=this.groundHeight(x,z);return{id:'target-'+(i+1),name:'靶标 '+String(i+1).padStart(2,'0'),x,z,y:baseY+2.1,baseY,w:5,d:5,h:4.2,hp:720,maxHp:720,reviveAt:0};}
  freePoint(point){const p=this.nav.clamp(point,8);if(this.nav.walkable(p.x,p.z,this.nav.radius))return p;let best=Infinity,result=null;for(let i=0;i<this.nav.passable.length;i++)if(this.nav.passable[i]){const q=this.nav.point(i),d=dist(p,q);if(d<best&&this.nav.walkable(q.x,q.z,this.nav.radius)){best=d;result=q;}}if(!result)throw new Error('地图没有安全出生点');return result;}
  settle(u){if(!this.map.heightAt)return;if(u.kind==='tank'){
    const h=this.groundHeight(u.x,u.z);if(!Number.isFinite(h))return;u.y=h;
    const s=Math.sin(u.yaw),c=Math.cos(u.yaw),sample=(dx,dz)=>this.groundHeight(u.x+dx,u.z+dz);
    u.bodyPitch=Math.atan2(sample(-s*3,-c*3)-sample(s*3,c*3),6);
    u.bodyRoll=-Math.atan2(sample(c*1.5,-s*1.5)-sample(-c*1.5,s*1.5),3);
  }else if(u.kind==='heli')u.y=Math.max(this.map.safeAltitude-35,this.groundHeight(u.x,u.z)+35);}
  emit(type,data={}){this.events.push({type,...data});}
  target(id){return this.targets.find(t=>t.id===id&&t.hp>0);}
  weapon(u){return (this.weapons??WEAPONS)[u.weapon];}
  muzzle(u,weapon=u.weapon){
    if(u.kind==='plane'){
      const side=weapon==='bomb'?u.launchSide*1.705:.3,len=weapon==='bomb'?-.385:7.8;
      return{x:u.x-Math.sin(u.yaw)*len+Math.cos(u.yaw)*side,y:u.y+(weapon==='bomb'?1.425:2.8),z:u.z-Math.cos(u.yaw)*len-Math.sin(u.yaw)*side};
    }
    if(u.kind==='heli'&&weapon==='gun'&&Number.isFinite(u.gunYaw))return{x:u.x-Math.sin(u.yaw)*2.804-Math.sin(u.gunYaw)*1.376*Math.cos(u.pitch),y:u.y+.88+1.376*Math.sin(u.pitch),z:u.z-Math.cos(u.yaw)*2.804-Math.cos(u.gunYaw)*1.376*Math.cos(u.pitch)};
    const yaw=u.kind==='tank'?u.turretYaw:u.yaw,length=u.kind==='tank'?1.5+4.13*Math.cos(u.pitch):u.weapon==='missile'?.802:2.804+1.376*Math.cos(u.pitch);
    const side=u.kind==='heli'&&u.weapon==='missile'?u.launchSide*2.422:0,y=u.kind==='tank'?1.83+4.13*Math.sin(u.pitch):u.weapon==='missile'?1.454:.88+1.376*Math.sin(u.pitch);
    if(this.map.heightAt&&u.kind==='tank'){
      const angle=yaw-u.yaw,roll=u.bodyRoll??0,pitch=u.bodyPitch??0;
      const x=-Math.sin(angle)*length+Math.cos(angle)*side,z=-Math.cos(angle)*length-Math.sin(angle)*side;
      const rx=Math.cos(roll)*x-Math.sin(roll)*y,ry=Math.sin(roll)*x+Math.cos(roll)*y;
      const yy=Math.cos(pitch)*ry-Math.sin(pitch)*z,zz=Math.sin(pitch)*ry+Math.cos(pitch)*z;
      return{x:u.x+Math.cos(u.yaw)*rx+Math.sin(u.yaw)*zz,y:u.y+yy,z:u.z-Math.sin(u.yaw)*rx+Math.cos(u.yaw)*zz};
    }
    return{x:u.x-Math.sin(yaw)*length+Math.cos(yaw)*side,y:u.y+y,z:u.z-Math.cos(yaw)*length-Math.sin(yaw)*side};
  }
  planCommand(ids,command){
    const set=new Set(ids),receiving=this.units.filter(u=>set.has(u.id)&&(command.type!=='reverse'||u.kind==='tank')&&(!['strafe','bomb'].includes(command.type)||u.kind==='plane')&&(!['attackMove','ground'].includes(command.type)||u.kind!=='plane'));
    const groups={};for(const u of receiving)(groups[u.kind]??=[]).push(u);
    return receiving.map(u=>{
      const job=structuredClone(command);job.started=false;
      if(['move','reverse','attackMove'].includes(job.type)&&u.kind!=='plane'){
        const g=groups[u.kind],i=g.indexOf(u),cols=this.map===CLASSIC?3:Math.min(12,Math.ceil(Math.sqrt(g.length))),spacing=u.kind==='tank'?8:16;
        const line=Number.isFinite(job.facing),columns=line?g.length:cols,angle=line?job.facing:0;
        const x=(i%columns-(Math.min(columns,g.length)-1)/2)*spacing,z=Math.floor(i/columns)*spacing;
        job.point=this.nav.clamp({x:job.point.x+x*Math.cos(angle)+z*Math.sin(angle),z:job.point.z-x*Math.sin(angle)+z*Math.cos(angle)},6);
      }
      const valid=!['move','reverse','attackMove'].includes(job.type)||u.kind!=='tank'||this.nav.walkable(job.point.x,job.point.z,this.nav.radius);
      return{unit:u,job,valid};
    });
  }
  command(ids,command,append=false){
    const planned=this.planCommand(ids,command),receiving=planned.filter(row=>row.valid);
    for(const {unit:u} of planned.filter(row=>!row.valid))u.state='目的地不可达 · 命令未提交';
    for(const {unit:u,job} of receiving){
      if(append&&u.job)u.queue.push(job);
      else{u.job=job;u.queue=append?u.queue:[];u.path=[];u.aim=0;u.blockedTime=0;u.revision++;if(u.flight)u.flight.plan=null;u.state=this.paused?'已下令 · 暂停中':'准备执行';}
    }
    return receiving.length;
  }
  cancel(ids){const set=new Set(ids);for(const u of this.units)if(set.has(u.id)){u.job=null;u.queue=[];u.path=[];u.moving=u.kind==='plane';u.aim=0;u.revision++;if(u.kind==='plane'){orbitHere(u);u.state='盘旋待命';}else u.state='已停止';}}
  setWeapon(ids,weapon){const set=new Set(ids);for(const u of this.units)if(set.has(u.id)&&u.kind==='heli'&&['gun','missile','auto'].includes(weapon)){
    u.weaponMode=weapon==='auto'?'auto':'manual';if(weapon==='auto')continue;
    u.weaponReady??={};u.weaponReady[u.weapon]=u.readyAt;u.weapon=weapon;u.readyAt=u.weaponReady[weapon]??0;u.aim=0;
  }}
  finish(u){u.job=u.queue.shift()??null;u.path=[];u.aim=0;u.blockedTime=0;if(u.flight)u.flight.plan=null;u.state=u.job?'准备执行':u.kind==='plane'?'盘旋待命':'待命';}
  navigate(u,point,dt,reverse=false){
    const air=u.kind==='heli',tuning=this.options.movement?.[air?'heli':'tank'];let speed=air?13:8;
    if(tuning){const desired=Math.min(reverse?(tuning.reverse??tuning.speed):tuning.speed,Math.sqrt(2*tuning.braking*dist(u,point)));u.travelSpeed??=0;u.travelSpeed+=clamp(desired-u.travelSpeed,-tuning.braking*dt,tuning.acceleration*dt);speed=u.travelSpeed;}
    if(dist(u,point)<1.1){u.path=[];return true;}
    if(!u.path.length){if(this.pathBudget<=0){this.waitingPaths++;u.state='等待路线计算';return false;}this.pathBudget--;u.path=this.nav.path(u,point,air);}
    if(!u.path.length){u.state='无可达路径';return false;}
    let next=u.path[0];if(dist(u,next)<1){u.path.shift();next=u.path[0];if(!next)return true;}
    const desired=yawTo(u,next)+(reverse?Math.PI:0);u.yaw=turn(u.yaw,desired,(air?1.5:1.1)*dt);
    const alignment=this.map.heightAt&&Math.abs(wrap(desired-u.yaw))>.13?0:Math.max(0,Math.cos(wrap(desired-u.yaw)));let dx=next.x-u.x,dz=next.z-u.z,len=Math.hypot(dx,dz);
    const step=Math.min(len,speed*dt*alignment);dx=dx/len*step;dz=dz/len*step;
    for(const other of this.unitGrid.query(u.x,u.z,air?10:6)){
      if(other===u||other.kind!==u.kind)continue;
      const gap=air?8:4.6,ox=u.x+dx-other.x,oz=u.z+dz-other.z,d=Math.hypot(ox,oz);
      if(d<gap&&d>.001){const push=Math.min((gap-d)*.55,speed*dt);dx+=ox/d*push;dz+=oz/d*push;}
    }
    const nx=u.x+dx,nz=u.z+dz;
    if(air||this.nav.walkable(nx,nz,this.map.heightAt?5.3:2.6)){const p=this.nav.clamp({x:nx,z:nz},4);u.x=p.x;u.z=p.z;u.moving=Math.hypot(dx,dz)>.005;u.blockedTime=0;}
    else{u.blockedTime+=dt;if(u.blockedTime>1){u.path=[];u.blockedTime=0;}}
    this.settle(u);u.state=reverse?'倒车中':air?'飞行中':'移动中';return false;
  }
  launch(u,point,target,weapon=u.weapon){
    const w=(this.weapons??WEAPONS)[weapon];if(this.time<u.readyAt)return false;
    const origin=this.muzzle(u,weapon),p={id:++this.serial,owner:u.id,weapon,origin:{...origin},position:{...origin},point:{...point},target:target?.id??null,guided:weapon==='missile',age:0};
    const distance=Math.hypot(point.x-origin.x,point.y-origin.y,point.z-origin.z)||1;
    p.direction={x:(point.x-origin.x)/distance,y:(point.y-origin.y)/distance,z:(point.z-origin.z)/distance};p.travelled=0;
    if(weapon==='bomb')p.velocity={x:-Math.sin(u.yaw)*FLIGHT.speed,y:0,z:-Math.cos(u.yaw)*FLIGHT.speed};
    this.projectiles.push(p);u.readyAt=this.time+w.reload;u.aim=0;u.state='已发射';this.shots++;if(p.guided||weapon==='bomb')u.launchSide*=-1;
    this.emit('shot',{unit:u.id,weapon,position:origin,projectile:p.id});return true;
  }
  engage(u,point,target,dt,ground=false){
    const w=this.weapon(u),range=dist(u,point);
    if(range>w.range-2){u.state='接近射程';this.navigate(u,point,dt);if(u.moving)u.state='接近射程';return false;}
    u.path=[];const desired=yawTo(u,point);if(u.kind==='heli')u.yaw=turn(u.yaw,desired,1.4*dt);u.turretYaw=turn(u.turretYaw,desired,1.65*dt);
    const height=u.y+(u.kind==='tank'?1.83:.88);u.pitch=clamp(Math.atan2(point.y-height,Math.max(1,range)),-.8,.35);
    if(this.map.heightAt&&u.kind==='tank'){const angle=u.turretYaw-u.yaw;u.pitch=clamp(u.pitch-(u.bodyPitch??0)*Math.cos(angle)+(u.bodyRoll??0)*Math.sin(angle),-.8,.35);}
    if(this.nav.blocked(this.muzzle(u),point)){u.aim=0;u.state='射线受阻 · 请重新选位';return false;}
    if(Math.abs(wrap(desired-(u.kind==='tank'?u.turretYaw:u.yaw)))>.055){u.aim=0;u.state='转向瞄准';return false;}
    u.aim+=dt;if(this.time<u.readyAt){u.state='装填中';return false;}if(u.aim<w.aim){u.state='稳定瞄准';return false;}
    return this.launch(u,point,target)&&ground;
  }
  updateUnit(u,dt){
    if(this.loadMode==='static'){u.moving=false;u.state='静态显示';return;}
    if(u.kind==='plane'){updateFlight(this,u,dt);return;}
    u.moving=false;if(!u.job){u.state='待命';return;}const job=u.job;
    if(['move','reverse'].includes(job.type)){
      if(this.navigate(u,job.point,dt,job.type==='reverse')){
        if(Number.isFinite(job.facing)&&Math.abs(wrap(u.yaw-job.facing))>.025){u.yaw=turn(u.yaw,job.facing,(u.kind==='heli'?1.5:1.1)*dt);u.state='调整到达朝向';u.moving=false;}
        else this.finish(u);
      }return;
    }
    if(job.type==='attackMove'){
      let t=this.target(job.engaged);if(!t){t=this.targets.filter(t=>t.hp>0&&dist(u,t)<=this.weapon(u).range).sort((a,b)=>dist(u,a)-dist(u,b))[0];job.engaged=t?.id;}
      if(t){this.engage(u,t,t,dt);return;}if(this.navigate(u,job.point,dt))this.finish(u);return;
    }
    if(job.type==='attack'){const t=this.target(job.target);if(!t){this.finish(u);return;}this.engage(u,t,t,dt);return;}
    if(job.type==='ground'&&this.engage(u,{...job.point,y:this.groundHeight(job.point.x,job.point.z)+.15},null,dt,true))this.finish(u);
  }
  impact(p,point,hitTarget,surface='entity'){
    const w=(this.weapons??WEAPONS)[p.weapon];for(const t of this.targets){if(t.hp<=0)continue;if(t.id!==hitTarget&&dist(t,point)>w.blast)continue;t.hp=Math.max(0,t.hp-(t.id===hitTarget?w.damage:w.damage*.55));this.hits++;if(!t.hp){this.kills++;t.reviveAt=this.time+2;this.emit('destroy',{target:t.id,position:{x:t.x,y:t.baseY+1,z:t.z}});}}
    this.emit('impact',{weapon:p.weapon,serial:p.id,owner:p.owner,target:hitTarget,surface,position:{...point}});
  }
  configureLoad(mode){
    this.loadMode=mode;this.recoverTargets=mode==='combat';
    this.cancel(this.units.map(u=>u.id));this.projectiles=[];this.events=[];this.shots=this.hits=this.kills=0;
    if(this.map.heightAt){
      this.targets=[];const anchors=[];
      for(let z=-this.map.size/2+25;z<this.map.size/2-25;z+=28)for(let x=-this.map.size/2+25;x<this.map.size/2-25;x+=28)if(this.nav.walkable(x,z,7)&&this.nav.walkable(x,z+16,7)&&this.nav.walkable(x,z-20,7))anchors.push({x,z});
      const nearby=anchors.sort((a,b)=>dist(a,this.map.center)-dist(b,this.map.center));
      this.units.forEach((u,i)=>{if(u.kind==='plane')return;const p=nearby[i%nearby.length];u.x=p.x;u.z=p.z;u.loadA={...p};u.loadB=this.freePoint({x:p.x+14,z:p.z+12});u.loadTarget=this.targets.length;this.targets.push(this.makeTarget(p.x,p.z-20,this.targets.length));u.loadRole=null;this.settle(u);});
      if(mode!=='combat')this.targets=this.targets.slice(0,4);
      for(const u of this.units){u.nextAuto=0;u.loadLeg=false;}return;
    }
    if(mode==='combat'){
      this.targets=[];let i=0;for(const z of [140,260,380])for(const x of [-360,-280,-200,-120,-40,40,120,200,280,360])this.targets.push({id:'load-'+(++i),name:'恢复靶 '+i,x,z,y:2.1,w:5,d:5,h:4.2,hp:720,maxHp:720,reviveAt:0});
      let g=0,h=0;for(const u of this.units){if(u.kind==='plane')continue;const n=u.kind==='tank'?g++:h++,t=this.targets[n%this.targets.length],rank=Math.floor(n/this.targets.length);u.x=t.x+(rank%3-1)*9;u.z=t.z+28+Math.floor(rank/3)*8;u.yaw=0;u.turretYaw=0;}
    }
    for(const u of this.units){u.nextAuto=0;u.loadLeg=false;}
  }
  step(dt){
    if(this.paused)return;this.time+=dt;this.pathBudget=2;this.waitingPaths=0;
    if(this.recoverTargets)for(const t of this.targets)if(t.hp<=0&&this.time>=t.reviveAt)t.hp=t.maxHp;
    this.unitGrid.clear();for(const u of this.units)if(u.kind!=='plane')this.unitGrid.insert(u);
    for(const u of this.units){
      if(this.map.heightAt&&this.loadMode==='combat'&&u.kind!=='plane'){
        const role=(Math.floor(this.time/18)+Number(u.id.split('-').at(-1)))%2;
        if(role!==u.loadRole){u.loadRole=role;u.job=null;u.path=[];}
        if(!u.job){if(role===0){const t=this.targets[u.loadTarget];if(t?.hp>0)u.job={type:'attack',target:t.id};}else{u.loadLeg=!u.loadLeg;u.job={type:'move',point:u.loadLeg?u.loadB:u.loadA};}}
      }
      if(!u.job&&this.loadMode!=='manual'&&this.time>=u.nextAuto){
        u.nextAuto=this.time+.5;
        if(this.loadMode==='combat'){const t=this.targets.filter(t=>t.hp>0).sort((a,b)=>dist(u,a)-dist(u,b))[0];if(t)u.job={type:u.kind==='plane'?'bomb':'attack',target:t.id,point:{x:t.x,z:t.z}};}
        if(this.loadMode==='move'){u.loadLeg=!u.loadLeg;u.job={type:'move',point:this.map.heightAt?(u.kind==='plane'?{x:0,z:0}:u.loadLeg?u.loadB:u.loadA):{x:u.x*.7,z:u.loadLeg?-330:210}};}
      }
      this.updateUnit(u,dt);
    }
    this.advanceProjectiles(dt);
  }
  projectileCandidates(){return this.targets;}
  projectileHit(a,b,target){return segmentBox(a,b,target,.12);}
  guideProjectile(p,dt){
    const target=this.target(p.target);if(target)p.point={x:target.x,y:target.y,z:target.z};
    const d=Math.hypot(p.point.x-p.position.x,p.point.y-p.position.y,p.point.z-p.position.z)||1;
    p.direction={x:(p.point.x-p.position.x)/d,y:(p.point.y-p.position.y)/d,z:(p.point.z-p.position.z)/d};
  }
  advanceProjectiles(dt){
    const alive=[];
    for(const p of this.projectiles){
      p.age+=dt;if(p.guided)this.guideProjectile(p,dt);
      const w=(this.weapons??WEAPONS)[p.weapon],a=p.position;let b;
      if(p.velocity){const v=p.velocity;b={x:a.x+v.x*dt,y:a.y+v.y*dt-FLIGHT.gravity*dt*dt/2,z:a.z+v.z*dt};v.y-=FLIGHT.gravity*dt;}
      else{const d=p.direction;b={x:a.x+d.x*w.speed*dt,y:a.y+d.y*w.speed*dt,z:a.z+d.z*w.speed*dt};}
      let earliest=2,target=null,surface=null;
      for(const o of this.nav.static.segment(a,b,.12)){const t=segmentBox(a,b,o,.08);if(t!==null&&t<earliest){earliest=t;target=null;surface='building';}}
      for(const o of this.projectileCandidates(a,b)){if(o.hp<=0)continue;const t=this.projectileHit(a,b,o);if(t!==null&&t<earliest){earliest=t;target=o.id;surface='entity';}}
      const terrain=this.map.heightAt?this.nav.terrainHit(a,b):b.y<=0?a.y/Math.max(.0001,a.y-b.y):null;
      if(terrain!==null&&terrain<earliest){earliest=terrain;target=null;surface='terrain';}
      if(earliest<=1){this.impact(p,{x:a.x+(b.x-a.x)*earliest,y:a.y+(b.y-a.y)*earliest,z:a.z+(b.z-a.z)*earliest},target,surface);continue;}
      p.travelled+=Math.hypot(b.x-a.x,b.y-a.y,b.z-a.z);
      if(p.age>35||p.travelled>Math.max(120,w.range*3)){this.emit('projectileEnd',{serial:p.id,owner:p.owner,weapon:p.weapon,reason:'expired'});continue;}
      p.position=b;alive.push(p);
    }
    this.projectiles=alive;
  }
}
