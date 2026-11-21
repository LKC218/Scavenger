/** 单机规则：导航、交战、固定翼和连续弹丸均不依赖DOM或Three.js。 */
import {CLASSIC,CITY,CLASSIC_OBSTACLES,SpatialGrid,clamp,dist,wrap,yawTo,turn} from './地图.mjs';
import {navigatorFor,findPath,segmentBox} from './寻路.mjs';
import {FLIGHT,orbitHere,updateFlight} from './飞行.mjs';
import {HelicopterAltitudePlanner} from './直升机高度.mjs';
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
    this.helicopterHeight?.reset();
    this.map?.resetSimulation?.();
    this.map=this.options.map??(this.options.city?CITY:CLASSIC);this.nav=navigatorFor(this.map);
    this.helicopterHeight=new HelicopterAltitudePlanner(this.map,this.options.helicopterHeight);
    this.total=count===undefined?(this.options.total??(this.options.city?30:10)):(this.options.city?count:count*2);
    this.time=0;this.paused=false;this.projectiles=[];this.events=[];this.shots=0;this.hits=0;this.kills=0;this.serial=0;this.loadMode='manual';this.recoverTargets=false;
    this.unitGrid=new SpatialGrid(18);this.pathBudget=2;this.waitingPaths=0;this.units=[];this.commandSerial=0;this.commandRows=new Map();this.lastCommandResult=null;
    const city=this.map!==CLASSIC;
    const planeCount=city?Math.max(1,Math.round(this.total*.04)):0;
    const heliCount=city?Math.round(this.total*.16):this.total/2;
    const counts=this.options.composition??{tank:this.total-heliCount-planeCount,heli:heliCount,plane:planeCount};
    for(const [kind,n] of Object.entries(counts))for(let i=0;i<n;i++){
      const columns=city?Math.min(12,Math.ceil(Math.sqrt(n*1.6))):5,spacing=kind==='tank'?(this.map.spawnSpacing??12):kind==='heli'?24:20;
      const x=city?(i%columns-(columns-1)/2)*spacing:-38+i*16;
      const z=city?(kind==='tank'?55:kind==='heli'?230:370)+Math.floor(i/columns)*spacing:(kind==='tank'?37:62);
      const u={id:kind+'-'+(i+1),kind,name:({tank:'铁砧',heli:'游隼',plane:'雷霆'})[kind]+' '+String(i+1).padStart(2,'0'),x,z,y:kind==='tank'?0:kind==='heli'?(city?35:11):FLIGHT.altitude+(i%3)*10,
        yaw:0,turretYaw:0,pitch:0,bank:0,weapon:kind==='tank'?'tank':kind==='heli'?'gun':'planeGun',queue:[],job:null,path:[],state:kind==='plane'?'盘旋待命':'待命',moving:false,readyAt:0,aim:0,blockedTime:0,revision:0,launchSide:-1,nextAuto:0};
      if(this.map.heightAt){const desired={x:x+(this.map.spawnOffset?.x??0),z:(this.map.spawnOffset?.z??0)+(kind==='tank'?(75+Math.floor(i/columns)*spacing):kind==='heli'?170+Math.floor(i/columns)*spacing:0)},spawn=kind==='heli'?this.helicopterSpawn(desired):this.freePoint(desired);u.x=spawn.x;u.z=spawn.z;u.y=kind==='tank'?this.groundHeight(u.x,u.z):kind==='heli'?u.y:this.map.safeAltitude+(i%3)*10;}
      if(kind==='heli')this.helicopterHeight.initialize(u);
      if(this.map.streaming)u.flightBounds=this.map.bounds;if(kind==='plane')orbitHere(u,{x:u.x+110,z:u.z});this.units.push(u);
    }
    this.targets=this.map.targets.map(([x,z],i)=>this.makeTarget(x,z,i));
    if(this.map.heightAt&&!this.targets.length)this.targets=[[-180,30],[-50,30],[90,30],[190,30]].map(([x,z],i)=>{const p=this.freePoint({x:x+(this.map.spawnOffset?.x??0),z:z+(this.map.spawnOffset?.z??0)});return this.makeTarget(p.x,p.z,i);});
    this.events.push({type:'reset'});
  }
  groundHeight(x,z,referenceY){return this.map.groundForUnit?this.map.groundForUnit(x,z,referenceY):this.map.heightAt?this.map.heightAt(x,z):0;}
  makeTarget(x,z,i){const baseY=this.groundHeight(x,z);return{id:'target-'+(i+1),name:'靶标 '+String(i+1).padStart(2,'0'),x,z,y:baseY+2.1,baseY,w:5,d:5,h:4.2,hp:720,maxHp:720,reviveAt:0};}
  freePoint(point){if(this.nav.freePoint)return this.nav.freePoint(point);const p=this.nav.clamp(point,8);if(this.nav.walkable(p.x,p.z,this.nav.radius))return p;let best=Infinity,result=null;for(let i=0;i<this.nav.passable.length;i++)if(this.nav.passable[i]){const q=this.nav.point(i),d=dist(p,q);if(d<best&&this.nav.walkable(q.x,q.z,this.nav.radius)){best=d;result=q;}}if(!result)throw new Error('地图没有安全出生点');return result;}
  helicopterSpawn(desired){
    const existing=this.units.filter(u=>u.kind==='heli'),spacing=24,clear=p=>existing.every(u=>dist(u,p)>=spacing-.001);
    const first=this.freePoint(desired);if(clear(first))return first;
    // 只在摆场时去重：freePoint可能把多个请求投到同一安全点，改试有限局部格位。
    for(let ring=1;ring<=6;ring++)for(let z=-ring;z<=ring;z++)for(let x=-ring;x<=ring;x++)if(Math.abs(x)===ring||Math.abs(z)===ring){const p=this.freePoint({x:desired.x+x*spacing,z:desired.z+z*spacing});if(clear(p))return p;}
    throw new Error('已准备局部区域内没有足够分离的直升机出生位置');
  }
  settle(u){if(!this.map.heightAt)return;if(u.kind==='tank'){
    const h=this.groundHeight(u.x,u.z,u.y);if(!Number.isFinite(h))return;u.y=h;
    const s=Math.sin(u.yaw),c=Math.cos(u.yaw),sample=(dx,dz)=>this.groundHeight(u.x+dx,u.z+dz,u.y);
    u.bodyPitch=Math.atan2(sample(-s*3,-c*3)-sample(s*3,c*3),6);
    u.bodyRoll=-Math.atan2(sample(c*1.5,-s*1.5)-sample(-c*1.5,s*1.5),3);
  }}
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
  formationReference(u,append){
    if(append)for(const job of [u.job,...u.queue].reverse())if(job?.point&&Number.isFinite(job.point.x)&&Number.isFinite(job.point.z))return job.point;
    return u;
  }
  destinationStatus(u,point){
    if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.z))return{status:'rejected',reason:'目的地坐标无效'};
    const b=this.map.bounds;if(point.x<b.minX||point.x>b.maxX||point.z<b.minZ||point.z>b.maxZ)return{status:'rejected',reason:'目的地超出地图范围'};
    if(u.kind==='plane')return{status:'ready',reason:''};
    const support=this.map.querySupport?.(point.x,point.z);
    if(support?.state==='pending')return{status:'pending',reason:'准备路径 · 目标地形尚未加载'};
    if(support&&support.state!=='ready')return{status:'rejected',reason:'目的地没有有效地面支撑'};
    if(u.kind==='heli')return{status:'ready',reason:''};
    if(this.nav.destinationStatus)return this.nav.destinationStatus(point);
    const state=this.nav.destinationState?.(point);
    if(state==='pending')return{status:'pending',reason:'准备路径 · 目标区域尚未就绪'};
    if(state==='unreachable'||(!state&&!this.nav.walkable(point.x,point.z,this.nav.radius)))return{status:'rejected',reason:'目的地不可通行 · 障碍、坡度或水域限制'};
    return{status:'ready',reason:''};
  }
  planCommand(ids,command,append=false){
    const set=new Set(ids),receiving=this.units.filter(u=>set.has(u.id)),groups={};
    for(const u of receiving)(groups[u.kind]??=[]).push(u);
    const offsets=new Map();
    for(const [kind,group] of Object.entries(groups)){
      const spacing=kind==='tank'?12:kind==='heli'?20:0,refs=group.map(u=>this.formationReference(u,append));
      const center={x:refs.reduce((n,p)=>n+p.x,0)/group.length,z:refs.reduce((n,p)=>n+p.z,0)/group.length};
      let points=refs.map(p=>({x:p.x-center.x,z:p.z-center.z})),minimum=Infinity;
      for(let i=0;i<points.length;i++)for(let j=0;j<i;j++)minimum=Math.min(minimum,dist(points[i],points[j]));
      // 完全重叠时没有可保留的间距，按稳定选择顺序展开；其余阵型只等比放大。
      if(minimum<.01){points=group.map((_,i)=>({x:(i-(group.length-1)/2)*spacing,z:0}));minimum=spacing;}
      const scale=minimum>0?Math.max(1,spacing/minimum):1;
      group.forEach((u,i)=>{
        const line=Number.isFinite(command?.facing),x=line?(i-(group.length-1)/2)*spacing:points[i].x*scale,z=line?0:points[i].z*scale;
        offsets.set(u.id,{x:line?x*Math.cos(command.facing):x,z:line?-x*Math.sin(command.facing):z});
      });
    }
    return receiving.map(u=>{
      const job=structuredClone(command??{});job.started=false;
      const movement=['move','reverse','attackMove'].includes(job.type),unsupported=(job.type==='reverse'&&u.kind!=='tank')||(['strafe','bomb'].includes(job.type)&&u.kind!=='plane')||(['attackMove','ground'].includes(job.type)&&u.kind==='plane');
      if(unsupported)return{unit:u,job,valid:false,status:'rejected',reason:'该单位不支持此命令'};
      if(movement&&job.point&&u.kind!=='plane'){
        const offset=offsets.get(u.id),mixed=groups.tank?.length&&groups.heli?.length;
        // 空地落点分组；同类的相对阵型和整组指令中心保持稳定。
        const lane=mixed?(u.kind==='tank'?-10:10):0,angle=Number.isFinite(job.facing)?job.facing:0;
        job.point={...job.point,x:job.point.x+offset.x+lane*Math.sin(angle),z:job.point.z+offset.z+lane*Math.cos(angle)};
      }
      const result=movement?this.destinationStatus(u,job.point):{status:'ready',reason:''};
      // 静态小地图可立即核对连通性；流式地图的异步路线不得由预览创建租约。
      if(movement&&u.kind==='tank'&&result.status==='ready'&&!this.nav.partialRoutes&&!this.nav.path(this.formationReference(u,append),job.point).length){result.status='rejected';result.reason='没有连通到目的地的路线';}
      return{unit:u,job,valid:result.status!=='rejected',...result};
    });
  }
  refreshCommandResult(){
    const result=this.lastCommandResult;if(!result)return;
    result.rejected=result.rows.filter(row=>row.status==='rejected').length;
    result.accepted=result.rows.length-result.rejected;
    result.pending=result.rows.filter(row=>row.status==='pending'||(row.status==='queued'&&row.preparationStatus==='pending')).length;
  }
  setJobStatus(u,job,status,reason=''){
    if(!job||(job.status===status&&job.reason===reason))return;job.status=status;job.reason=reason;
    const row=this.commandRows.get(job.id);if(row){row.status=status;row.reason=reason;row.preparationStatus=status==='pending'?'pending':status==='queued'?row.preparationStatus:'ready';}
    if(u.job===job)u.commandStatus={status,reason,commandId:job.id};this.refreshCommandResult();
  }
  pruneCommandRows(){const live=new Set(this.lastCommandResult?.rows.map(row=>row.commandId)??[]);for(const u of this.units)for(const job of [u.job,...u.queue])if(job?.id)live.add(job.id);for(const id of this.commandRows.keys())if(!live.has(id))this.commandRows.delete(id);}
  discardJobs(u,status='cancelled',reason='已被新命令替换'){
    for(const job of [u.job,...u.queue])this.setJobStatus(u,job,status,reason);
    this.nav.cancel?.(u);if(u.kind==='heli')this.helicopterHeight.cancel(u);u.queue=[];u.path=[];u.moving=u.kind==='plane';u.aim=0;u.blockedTime=0;u.travelSpeed=0;u.revision++;if(u.flight)u.flight.plan=null;
  }
  command(ids,command,append=false){
    const planned=this.planCommand(ids,command,append),id='command-'+(++this.commandSerial),result={id,type:command?.type,append,accepted:0,rejected:0,pending:0,rows:[]};
    for(const row of planned){
      const {unit:u,job}=row;job.id=id+':'+u.id;job.batchId=id;
      const feedback={unitId:u.id,status:row.status,reason:row.reason,point:job.point?{...job.point}:null,facing:job.facing,commandId:job.id,preparationStatus:row.status};result.rows.push(feedback);this.commandRows.set(job.id,feedback);
      if(!row.valid){u.lastRejectedCommand=feedback;if(!u.job){u.commandStatus={status:'rejected',reason:row.reason,commandId:job.id};u.state=row.reason;}continue;}
      u.lastRejectedCommand=null;
      if(append&&u.job){u.queue.push(job);this.setJobStatus(u,job,'queued',row.status==='pending'?row.reason:'等待前序命令');}
      else{this.discardJobs(u);u.job=job;this.setJobStatus(u,job,row.status,row.reason);u.state=row.status==='pending'?row.reason:this.paused?'已下令 · 暂停中':'准备执行';}
    }
    this.lastCommandResult=result;this.refreshCommandResult();this.pruneCommandRows();return result.accepted;
  }
  cancel(ids){const set=new Set(ids);for(const u of this.units)if(set.has(u.id)){this.discardJobs(u,'cancelled','停止指令已清空移动队列');u.job=null;u.lastRejectedCommand=null;u.commandStatus={status:'stopped',reason:'移动队列已清空',commandId:null};if(u.kind==='plane'){orbitHere(u);u.state='盘旋待命';}else u.state=u.kind==='heli'?'悬停待命':'已停止';}this.pruneCommandRows();}
  setWeapon(ids,weapon){const set=new Set(ids);for(const u of this.units)if(set.has(u.id)&&u.kind==='heli'&&['gun','missile','auto'].includes(weapon)){
    u.weaponMode=weapon==='auto'?'auto':'manual';if(weapon==='auto')continue;
    u.weaponReady??={};u.weaponReady[u.weapon]=u.readyAt;u.weapon=weapon;u.readyAt=u.weaponReady[weapon]??0;u.aim=0;
  }}
  finish(u){this.setJobStatus(u,u.job,'completed');this.nav.cancel?.(u);if(u.kind==='heli')this.helicopterHeight.cancel(u,{preserve:true});u.job=u.queue.shift()??null;u.path=[];u.aim=0;u.blockedTime=0;u.travelSpeed=0;u.revision++;if(u.flight)u.flight.plan=null;if(u.job)this.setJobStatus(u,u.job,'ready');u.state=u.job?'准备执行':u.kind==='plane'?'盘旋待命':'待命';this.pruneCommandRows();}
  preparationStatus(u){
    if(u.job?.status==='rejected')return{status:'rejected',reason:u.job.reason};
    if(this.map.commandStatus)return this.map.commandStatus(u);
    const state=this.map.commandState?.(u);return state==='pending'?{status:'pending',reason:'准备路径 · 等待目标区域'}:state==='unreachable'||state==='error'?{status:'rejected',reason:'目的地不可达 · 目标区域准备失败'}:{status:'ready',reason:''};
  }
  rejectJob(u,reason){this.setJobStatus(u,u.job,'rejected',reason);this.nav.cancel?.(u);if(u.kind==='heli')this.helicopterHeight.cancel(u,{preserve:true});u.path=[];u.moving=u.kind==='plane';u.travelSpeed=0;u.state=reason;}
  navigateHelicopter(u,point,dt){
    const tuning=this.options.movement?.heli??{},near=dist(u,point)<1.1,desired=yawTo(u,point);if(!near)u.yaw=turn(u.yaw,desired,1.5*Math.min(dt,1));
    const angle=Math.abs(wrap(desired-u.yaw)),alignment=near?1:this.map.heightAt&&angle>.13?0:Math.max(0,Math.cos(angle));u.path=[{...point}];
    const result=this.helicopterHeight.step(u,point,dt,{speed:tuning.speed??13,alignment,acceleration:tuning.acceleration??4,braking:tuning.braking??7});
    if(result.status==='error'){this.rejectJob(u,result.reason);return false;}
    if(result.status==='pending'){this.setJobStatus(u,u.job,'pending',result.reason);u.state=result.reason+' · 悬停等待';return false;}
    this.setJobStatus(u,u.job,'executing',u.altitudeState.status==='cruise'?'':u.altitudeState.reason);u.state=u.altitudeState.status==='climbing'?'提前爬升避障':u.altitudeState.status==='descending'?'下降至局部巡航':u.moving?'飞行中':'悬停调整';
    if(result.arrived)u.path=[];return result.arrived;
  }
  navigate(u,point,dt,reverse=false){
    if(u.kind==='heli')return this.navigateHelicopter(u,point,dt);
    const air=u.kind==='heli',tuning=this.options.movement?.[air?'heli':'tank'];let speed=air?13:8;
    if(tuning){const desired=Math.min(reverse?(tuning.reverse??tuning.speed):tuning.speed,Math.sqrt(2*tuning.braking*dist(u,point)));u.travelSpeed??=0;u.travelSpeed+=clamp(desired-u.travelSpeed,-tuning.braking*dt,tuning.acceleration*dt);speed=u.travelSpeed;}
    if(dist(u,point)<1.1){u.path=[];return true;}
    if(!u.path.length){if(this.pathBudget<=0){this.waitingPaths++;this.setJobStatus(u,u.job,'pending','准备路径 · 等待路线计算');u.state='准备路径 · 等待路线计算';return false;}this.pathBudget--;u.path=this.nav.path(u,point,air);}
    if(!u.path.length){
      const state=this.nav.status?.(u);
      if(state?.status==='rejected'){this.rejectJob(u,state.reason);return false;}
      if(this.nav.pending?.(u)||state?.status==='pending'){this.setJobStatus(u,u.job,'pending',state?.reason??'准备路径 · 等待区域路线');u.state=u.job?.reason??'准备路径';}
      else this.rejectJob(u,'没有连通到目的地的路线');return false;
    }
    this.setJobStatus(u,u.job,'executing');
    let next=u.path[0];if(dist(u,next)<1){u.path.shift();next=u.path[0];if(!next)return this.nav.partialRoutes?this.nav.pathEnded(u,point):true;}
    const desired=yawTo(u,next)+(reverse?Math.PI:0);u.yaw=turn(u.yaw,desired,(air?1.5:1.1)*dt);
    const alignment=this.map.heightAt&&Math.abs(wrap(desired-u.yaw))>.13?0:Math.max(0,Math.cos(wrap(desired-u.yaw)));let dx=next.x-u.x,dz=next.z-u.z,len=Math.hypot(dx,dz);
    const step=Math.min(len,speed*dt*alignment);dx=dx/len*step;dz=dz/len*step;
    for(const other of this.unitGrid.query(u.x,u.z,air?10:6)){
      if(other===u||other.kind!==u.kind)continue;
      const gap=air?8:4.6,ox=u.x+dx-other.x,oz=u.z+dz-other.z,d=Math.hypot(ox,oz);
      if(d<gap&&d>.001){const push=Math.min((gap-d)*.55,speed*dt);dx+=ox/d*push;dz+=oz/d*push;}
    }
    const nx=u.x+dx,nz=u.z+dz;
    if(air||this.nav.walkable(nx,nz,this.map.heightAt?5.3:2.6,{from:u})){const p=this.nav.clamp({x:nx,z:nz},4);u.x=p.x;u.z=p.z;u.moving=Math.hypot(dx,dz)>.005;u.blockedTime=0;}
    else{u.blockedTime+=dt;if(u.blockedTime>1){this.nav.cancel?.(u);u.path=[];u.blockedTime=0;}}
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
    if(u.kind!=='plane'&&u.job){const state=this.preparationStatus(u);if(state.status!=='ready'){u.moving=false;if(u.kind==='heli')this.helicopterHeight.pause(u,state.status==='pending'?'pending':'error',state.reason);if(state.status==='pending'){this.setJobStatus(u,u.job,'pending',state.reason);u.state=state.reason;}else this.rejectJob(u,state.reason);return;}}
    if(this.loadMode==='static'){u.moving=false;u.state='静态显示';return;}
    if(u.kind==='plane'){updateFlight(this,u,dt);return;}
    u.moving=false;if(!u.job){u.state=u.commandStatus?.status==='stopped'?(u.kind==='heli'?'悬停待命':'已停止'):'待命';return;}const job=u.job;
    if(['move','reverse'].includes(job.type)){
      if(this.navigate(u,job.point,dt,job.type==='reverse')){
        if(Number.isFinite(job.facing)&&Math.abs(wrap(u.yaw-job.facing))>.025){this.setJobStatus(u,job,'executing','调整到达朝向');u.yaw=turn(u.yaw,job.facing,(u.kind==='heli'?1.5:1.1)*dt);u.state='调整到达朝向';u.moving=false;}
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
    // 指挥待命和静态压力均保留真实出生布局，不进入旧交战锚点重排。
    if(['idle','manual','static'].includes(mode))return;
    if(this.map.heightAt){
      this.targets=[];const anchors=[];
      const bounds=this.map.streaming?{minX:(this.map.spawnOffset?.x??0)-260,maxX:(this.map.spawnOffset?.x??0)+260,minZ:(this.map.spawnOffset?.z??0)-260,maxZ:(this.map.spawnOffset?.z??0)+260}:{minX:-this.map.size/2,maxX:this.map.size/2,minZ:-this.map.size/2,maxZ:this.map.size/2};for(let z=bounds.minZ+25;z<bounds.maxZ-25;z+=28)for(let x=bounds.minX+25;x<bounds.maxX-25;x+=28)if(this.nav.walkable(x,z,7)&&this.nav.walkable(x,z+16,7)&&this.nav.walkable(x,z-20,7))anchors.push({x,z});
      const nearby=anchors.sort((a,b)=>dist(a,this.map.center)-dist(b,this.map.center));
      if(!nearby.length){this.loadMode='idle';this.recoverTargets=false;return;}
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
    if(this.paused)return;this.map.syncSimulation?.(this.units,this.projectiles,this.targets);this.time+=dt;this.pathBudget=2;this.waitingPaths=0;
    if(this.recoverTargets)for(const t of this.targets)if(t.hp<=0&&this.time>=t.reviveAt)t.hp=t.maxHp;
    this.unitGrid.clear();for(const u of this.units)if(u.kind!=='plane')this.unitGrid.insert(u);
    for(const u of this.units){
      if(this.map.heightAt&&this.loadMode==='combat'&&u.kind!=='plane'){
        const role=(Math.floor(this.time/18)+Number(u.id.split('-').at(-1)))%2;
        if(role!==u.loadRole){u.loadRole=role;u.job=null;u.path=[];}
        if(!u.job){if(role===0){const t=this.targets[u.loadTarget];if(t?.hp>0)u.job={type:'attack',target:t.id};}else{u.loadLeg=!u.loadLeg;u.job={type:'move',point:u.loadLeg?u.loadB:u.loadA};}}
      }
      if(!u.job&&['combat','move'].includes(this.loadMode)&&this.time>=u.nextAuto){
        u.nextAuto=this.time+.5;
        if(this.loadMode==='combat'){const t=this.targets.filter(t=>t.hp>0).sort((a,b)=>dist(u,a)-dist(u,b))[0];if(t)u.job={type:u.kind==='plane'?'bomb':'attack',target:t.id,point:{x:t.x,z:t.z}};}
        if(this.loadMode==='move'){u.loadLeg=!u.loadLeg;u.job={type:'move',point:this.map.heightAt?(u.kind==='plane'?{x:this.map.spawnOffset?.x??0,z:this.map.spawnOffset?.z??0}:u.loadLeg?u.loadB:u.loadA):{x:u.x*.7,z:u.loadLeg?-330:210}};}
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
      if(this.nav.segmentReady&&!this.nav.segmentReady(a,b)){p.age-=dt;if(p.velocity)p.velocity.y+=FLIGHT.gravity*dt;alive.push(p);continue;}
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
