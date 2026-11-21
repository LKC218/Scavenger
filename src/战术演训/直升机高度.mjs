/** 局部直升机高度规则：米制运行基准点、有限前瞻、独立读租约；不依赖显示模块。 */
// AH-64D实模运行原点水平包围角半径13.933米；14.5米覆盖任意机头朝向。
export const HELICOPTER_HEIGHT=Object.freeze({cruiseAGL:30,clearance:8,radius:14.5,lookAhead:90,sample:4,climbRate:6,descentRate:3,verticalAcceleration:4,acceleration:4,braking:7,scanInterval:.15,leaseGrid:32,substep:.05,maxFrame:1,envelopePages:16,cachedEnvelopePages:32,envelopeRecords:4096,envelopeBytes:8*1048576});
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n)),distance=(a,b)=>Math.hypot(a.x-b.x,a.z-b.z);
const intersects=(a,b)=>a[0]<=b.maxX&&a[3]>=b.minX&&a[2]<=b.maxZ&&a[5]>=b.minZ;
const rectangle=(a,b,r)=>({minX:Math.min(a.x,b.x)-r,maxX:Math.max(a.x,b.x)+r,minZ:Math.min(a.z,b.z)-r,maxZ:Math.max(a.z,b.z)+r});
function entryRange(a,b,bounds,pad){let lo=0,hi=1;for(const [axis,min,max] of [['x',bounds[0]-pad,bounds[3]+pad],['z',bounds[2]-pad,bounds[5]+pad]]){const d=b[axis]-a[axis];if(Math.abs(d)<1e-9){if(a[axis]<min||a[axis]>max)return null;}else{let t=(min-a[axis])/d,s=(max-a[axis])/d;if(t>s)[t,s]=[s,t];lo=Math.max(lo,t);hi=Math.min(hi,s);if(lo>hi)return null;}}return[lo,hi];}

/** BAIP世界包围盒只作保守飞行安全包络；不冒充原Collider或精确树冠碰撞。 */
class FlightEnvelopes{
 constructor(map,config,decode){this.map=map;this.config=config;this.decode=decode;this.rows=new Map((map.pages??[]).map(row=>[row.id,row]));this.chunks=new Map((map.chunks??[]).map(row=>[row.id,row]));this.pages=new Map();this.clock=0;this.bytes=0;}
 release(token){if(!token)return;for(const id of token.ids){const row=this.pages.get(id);if(!row)continue;row.refs.delete(token);if(!row.refs.size&&row.status!=='ready'){row.controller.abort();this.pages.delete(id);this.bytes-=row.bytes;}}}
 reset(){for(const row of this.pages.values())row.controller.abort();this.pages.clear();this.bytes=0;}
 trim(extra=0){for(const [id,row] of [...this.pages].sort((a,b)=>a[1].used-b[1].used)){if(this.bytes+extra<=this.config.envelopeBytes&&this.pages.size<this.config.cachedEnvelopePages)break;if(row.refs.size)continue;row.controller.abort();this.pages.delete(id);this.bytes-=row.bytes;}}
 query(bounds,record){
  if(!this.map.streaming)return{status:'ready',shapes:[]};
  if(!this.chunks.size||!this.rows.size||!this.map.downloads)return{status:'error',reason:'缺少局部实例页，无法核对树冠安全包络'};
  const b=this.map.bounds,size=this.map.chunk_m??125,ids=new Set();
  for(let z=Math.max(0,Math.floor((bounds.minZ-b.minZ)/size));z<=Math.floor((bounds.maxZ-b.minZ)/size);z++)for(let x=Math.max(0,Math.floor((bounds.minX-b.minX)/size));x<=Math.floor((bounds.maxX-b.minX)/size);x++){
   const row=this.chunks.get(String(x).padStart(2,'0')+'-'+String(z).padStart(2,'0'));if(!row||!Array.isArray(row.instance_pages))return{status:'error',reason:'前方实例格缺少完整安全包络元数据'};for(const id of row.instance_pages)ids.add(id);
  }
  if(ids.size>this.config.envelopePages)return{status:'error',reason:'局部树冠安全包络超出页数预算'};
  const key=[...ids].sort().join(',');if(record.envelope?.key!==key){this.release(record.envelope);record.envelope={key,ids:[...ids]};}
  const token=record.envelope,shapes=[];let pending=false;
  for(const id of token.ids){
   let row=this.pages.get(id);
   if(!row){
    const source=this.rows.get(id);if(!source||!Number.isInteger(source.count)||source.count<0||source.count>this.config.envelopeRecords||!Number.isFinite(source.identity?.bytes)||!this.map.prototypes)return{status:'error',reason:'局部实例页缺失或超过单页记录预算'};
    const bytes=source.identity.bytes+source.count*192;this.trim(bytes);if(this.bytes+bytes>this.config.envelopeBytes||this.pages.size>=this.config.cachedEnvelopePages)return{status:'error',reason:'局部飞行安全包络超过缓存预算'};
    row={refs:new Set(),controller:new AbortController(),status:'pending',used:++this.clock,bytes};this.pages.set(id,row);this.bytes+=bytes;const current=row;
    Promise.resolve().then(async()=>{
     const buffer=await this.map.downloads.get(new URL(source.file,this.map.packageBase).href,source.identity,current.controller.signal);
     const decode=this.decode??(await import('/实例页.mjs')).decodeInstancePage;
     const decoded=decode(buffer,{prototypeCount:this.map.prototypes.length,groupCount:this.map.group_count,expectedCount:source.count});
     if(this.pages.get(id)!==current||current.controller.signal.aborted)return;
     current.shapes=decoded.records.filter(item=>this.map.prototypes[item.prototype]?.enabled!==false).map(item=>({bounds:Array.from(item.bounds),flightEnvelope:true,id:'flight:'+item.sourceId,prototype:item.prototype,kind:this.map.prototypes[item.prototype]?.category}));current.status='ready';
    }).catch(error=>{if(this.pages.get(id)!==current||current.controller.signal.aborted)return;current.status='error';current.reason='树冠安全包络准备失败 · '+String(error.message??error);});
   }
   row.refs.add(token);row.used=++this.clock;if(row.status==='error')return{status:'error',reason:row.reason};if(row.status==='pending')pending=true;else for(const shape of row.shapes)if(intersects(shape.bounds,bounds))shapes.push(shape);
  }
  return pending?{status:'pending',reason:'准备前方树冠与建筑安全包络'}:{status:'ready',shapes};
 }
}

export class HelicopterAltitudePlanner{
 constructor(map,options={}){this.map=map;this.config={...HELICOPTER_HEIGHT,...options};this.records=new Map();this.epoch=0;this.serial=0;this.envelopes=new FlightEnvelopes(map,this.config,options.decodeInstances);}
 ground(x,z){if(this.map.querySupport){const q=this.map.querySupport(x,z);return q.state==='ready'&&Number.isFinite(q.height)?{status:'ready',height:q.height}:q.state==='pending'?{status:'pending'}:{status:'error',reason:'局部地面没有有效支撑'};}const h=this.map.heightAt?.(x,z)??0;return Number.isFinite(h)?{status:'ready',height:h}:{status:'pending'};}
 setState(u,status,targetAltitude,reason='',ground=this.ground(u.x,u.z)){u.altitudeState={status,baseGround:ground.status==='ready'?ground.height:null,targetAltitude:Number.isFinite(targetAltitude)?targetAltitude:null,reason};}
 initialize(u){const q=this.ground(u.x,u.z);if(q.status==='ready')u.y=q.height+this.config.cruiseAGL;this.setState(u,q.status==='ready'?'cruise':q.status==='pending'?'pending':'error',q.status==='ready'?u.y:null,q.status==='ready'?'':q.reason??'准备脚下地面',q);}
 pause(u,status,reason){const record=this.records.get(u.id);if(record){record.speed=0;record.verticalSpeed=0;record.age=Infinity;}this.setState(u,status,u.y,reason);u.travelSpeed=0;u.moving=false;}
 releaseRecord(record){if(record.lease){record.lease.controller.abort();this.map.releaseRegion?.(record.lease.owner);}this.envelopes.release(record.envelope);}
 cancel(u,{preserve=false}={}){const record=this.records.get(u.id);if(record){this.releaseRecord(record);this.records.delete(u.id);}if(!preserve)this.setState(u,'holding',u.y,'悬停待命');}
 reset(){for(const row of this.records.values())this.releaseRecord(row);this.records.clear();this.envelopes.reset();this.epoch++;}
 stats(){return{units:this.records.size,envelopePages:this.envelopes.pages.size,envelopeBytes:this.envelopes.bytes};}
 record(u){let row=this.records.get(u.id);if(!row||row.revision!==u.revision){if(row)this.releaseRecord(row);row={revision:u.revision,speed:0,verticalSpeed:0,age:Infinity};this.records.set(u.id,row);}return row;}
 lease(record,bounds){
  if(!this.map.prepareRegion)return;
  const g=this.config.leaseGrid,box={minX:Math.floor(bounds.minX/g)*g,maxX:Math.ceil(bounds.maxX/g)*g,minZ:Math.floor(bounds.minZ/g)*g,maxZ:Math.ceil(bounds.maxZ/g)*g},key=Object.values(box).join(':');
  if(record.lease?.key===key)return;
  if(record.lease){record.lease.controller.abort();this.map.releaseRegion?.(record.lease.owner);}
  const lease={key,owner:'helicopter-height:'+this.epoch+':'+(++this.serial),controller:new AbortController(),status:'pending'};record.lease=lease;
  try{Promise.resolve(this.map.prepareRegion(lease.owner,box,{signal:lease.controller.signal})).then(()=>{if(record.lease===lease&&!lease.controller.signal.aborted)lease.status='ready';}).catch(error=>{if(record.lease!==lease||lease.controller.signal.aborted)return;lease.status='error';lease.reason='前方地形准备失败 · '+String(error.message??error);});}catch(error){lease.status='error';lease.reason='前方地形准备失败 · '+String(error.message??error);}
 }
 scan(u,target,record){
  const c=this.config,range=Math.min(c.lookAhead,distance(u,target)),dir=range>.001?{x:(target.x-u.x)/distance(u,target),z:(target.z-u.z)/distance(u,target)}:{x:1,z:0},end={x:u.x+dir.x*range,z:u.z+dir.z*range},bounds=rectangle(u,end,c.radius);
  const b=this.map.bounds;if(b&&(bounds.minX<b.minX||bounds.maxX>b.maxX||bounds.minZ<b.minZ||bounds.maxZ>b.maxZ))return{status:'error',reason:'前方机体安全范围超出已知地图'};
  this.lease(record,bounds);if(record.lease?.status==='error')return{status:'error',reason:record.lease.reason};
  const envelope=this.envelopes.query(bounds,record);if(envelope.status!=='ready')return envelope;
  const collision=this.map.collisionsIn?.(bounds)??{state:'ready',shapes:this.map.obstacles??[]};if(collision.state!=='ready')return{status:'pending',reason:'准备前方碰撞数据'};
  const constraints=[];
  for(let i=0,n=Math.max(1,Math.ceil(range/c.sample));i<=n;i++){
   const d=range*i/n;
   for(const side of [-c.radius,0,c.radius]){const q=this.ground(u.x+dir.x*d-dir.z*side,u.z+dir.z*d+dir.x*side);if(q.status!=='ready')return{status:q.status,reason:q.reason??'准备前方地形'};constraints.push({entry:d,exit:d+c.sample,height:q.height+c.cruiseAGL,reason:'沿局部地形巡航'});}
  }
  for(const shape of [...collision.shapes,...envelope.shapes]){
   if(shape.movement===false&&!shape.flightEnvelope)continue;
   const sb=shape.bounds??[shape.x-shape.w/2,shape.baseY??0,shape.z-shape.d/2,shape.x+shape.w/2,(shape.baseY??0)+shape.h,shape.z+shape.d/2];
   if(!sb.every(Number.isFinite))return{status:'error',reason:'前方障碍缺少有效安全范围'};
   const at=entryRange(u,end,sb,c.radius);if(at)constraints.push({entry:at[0]*range,exit:at[1]*range,height:sb[4]+c.clearance,reason:shape.flightEnvelope?'提前越过建筑或树冠安全包络':'提前越过源碰撞障碍'});
  }
  return{status:'ready',origin:{x:u.x,z:u.z},dir,constraints};
 }
 swept(u,next,nextY){
  const c=this.config,collision=this.map.collisionsIn?.(rectangle(u,next,c.radius));if(collision&&collision.state!=='ready')return{status:'pending',reason:'准备即将飞越区域的碰撞数据'};
  if(this.map.supportSegment){const length=distance(u,next),dir=length>.001?{x:(next.x-u.x)/length,z:(next.z-u.z)/length}:{x:1,z:0};for(const side of [-c.radius,0,c.radius]){const a={x:u.x-dir.z*side,y:u.y-c.clearance,z:u.z+dir.x*side},b={x:next.x-dir.z*side,y:nextY-c.clearance,z:next.z+dir.x*side},q=this.map.supportSegment(a,b);if(q.state!=='ready')return{status:'pending',reason:'准备连续地形复核'};if(q.hit!==null&&q.hit!==undefined){const at={x:a.x+(b.x-a.x)*q.hit,z:a.z+(b.z-a.z)*q.hit},support=this.ground(at.x,at.z);if(support.status!=='ready')return{status:support.status,reason:support.reason??'准备连续地形支撑'};return{status:'blocked',reason:'连续地形受阻 · 悬停爬升',observation:{...at,height:Math.max(support.height+c.cruiseAGL,u.y+1)}};}}}
  const q=this.ground(next.x,next.z);if(q.status!=='ready')return{status:q.status,reason:q.reason??'准备脚下地面'};if(nextY<q.height+c.clearance)return{status:'blocked',reason:'地形安全余量不足 · 悬停爬升'};
  return{status:'ready',ground:q};
 }
 step(u,target,dt,{speed=13,alignment=1,acceleration=this.config.acceleration,braking=this.config.braking}={}){
  const c=this.config,record=this.record(u),duration=clamp(Number.isFinite(dt)?dt:0,0,c.maxFrame);let remaining=duration,moved=false;
  while(remaining>1e-8){
   const tick=Math.min(c.substep,remaining);remaining-=tick;record.age+=tick;
   const gap=distance(u,target),dir=gap>.001?{x:(target.x-u.x)/gap,z:(target.z-u.z)/gap}:{x:1,z:0};
   if(!record.profile||record.lease?.status==='error'||record.age>=c.scanInterval||distance(u,record.profile.origin)>2||dir.x*record.profile.dir.x+dir.z*record.profile.dir.z<.99){record.profile=this.scan(u,target,record);record.age=0;}
   const profile=record.profile;
   if(profile.status!=='ready'){
    record.speed=0;record.verticalSpeed=0;record.age=Infinity;this.setState(u,profile.status==='error'?'error':'pending',u.y,profile.reason);u.moving=false;return{status:profile.status,reason:profile.reason,arrived:false};
   }
   const foot=this.ground(u.x,u.z);if(foot.status!=='ready'){this.setState(u,foot.status==='error'?'error':'pending',u.y,foot.reason??'准备脚下地面',foot);u.moving=false;record.speed=0;return{status:foot.status,reason:u.altitudeState.reason,arrived:false};}
   const travelled=(u.x-profile.origin.x)*profile.dir.x+(u.z-profile.origin.z)*profile.dir.z,active=profile.constraints.filter(row=>row.exit>=travelled-.001);
   let goal=foot.height+c.cruiseAGL,reason='局部离地30米巡航',limit=gap;
   for(const row of active){if(row.height>goal){goal=row.height;reason=row.reason;}if(row.height>u.y+.15)limit=Math.min(limit,Math.max(0,row.entry-travelled-2));}
   if(record.observation){const ahead=(record.observation.x-u.x)*dir.x+(record.observation.z-u.z)*dir.z;if(ahead < -c.radius)record.observation=null;else{goal=Math.max(goal,record.observation.height);reason='连续地形受阻 · 先爬升后继续';if(record.observation.height>u.y+.15)limit=0;}}
   const delta=goal-u.y,rate=delta>=0?c.climbRate:c.descentRate,wanted=Math.sign(delta)*Math.min(rate,Math.sqrt(2*c.verticalAcceleration*Math.abs(delta)));
   record.verticalSpeed+=clamp(wanted-record.verticalSpeed,-c.verticalAcceleration*tick,c.verticalAcceleration*tick);
   let nextY=u.y+record.verticalSpeed*tick;if((goal-u.y)*(goal-nextY)<=0){nextY=goal;record.verticalSpeed=0;}
   const maxSpeed=Math.min(speed*alignment,Math.sqrt(2*braking*Math.max(0,gap-.6)),Math.sqrt(2*braking*limit));record.speed+=clamp(maxSpeed-record.speed,-braking*tick,acceleration*tick);
   const advance=Math.min(gap,Math.max(0,record.speed*tick),limit),next={x:u.x+dir.x*advance,z:u.z+dir.z*advance},sweep=this.swept(u,next,nextY);
   if(sweep.status==='pending'||sweep.status==='error'){record.speed=0;record.verticalSpeed=0;record.age=Infinity;this.setState(u,sweep.status==='error'?'error':'pending',goal,sweep.reason,foot);u.moving=false;return{status:sweep.status,reason:sweep.reason,arrived:false};}
   // 连续复核失败时保留水平位置，仅执行安全方向的爬升；不改指令终点。
   if(sweep.status==='blocked'){record.speed=0;record.age=Infinity;record.verticalSpeed=Math.max(0,record.verticalSpeed);next.x=u.x;next.z=u.z;nextY=Math.max(u.y,nextY);reason=sweep.reason;if(sweep.observation){record.observation=sweep.observation;goal=Math.max(goal,sweep.observation.height);}}
   moved||=distance(u,next)>.0001;u.x=next.x;u.z=next.z;u.y=nextY;u.travelSpeed=record.speed;
   const status=goal-u.y>.2?'climbing':u.y-goal>.2?'descending':advance<.001&&gap>1.1?'holding':'cruise';this.setState(u,status,goal,reason);
  }
  // 正常到达须完成最后一段升降；手动停止另走cancel，保留工程师指定的悬停高度。
  u.moving=moved;const arrived=distance(u,target)<1.1&&Number.isFinite(u.altitudeState?.targetAltitude)&&Math.abs(u.y-u.altitudeState.targetAltitude)<=1e-6&&Math.abs(record.verticalSpeed)<=1e-6;
  return{status:'ready',reason:u.altitudeState?.reason??'',arrived};
 }
}
