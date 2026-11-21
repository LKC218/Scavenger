// 仅由全图载具适配器加载；局部网格与工作器有界，镜头不持有单位模拟租约。
import {SpatialGrid,clamp,dist} from '/地图.mjs';
const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
const rectangle=(p,r)=>({minX:p.x-r,maxX:p.x+r,minZ:p.z-r,maxZ:p.z+r});
export class StreamingNavigator {
 constructor(map){
  this.map=map;this.radius=5.8;this.partialRoutes=true;this.patches=new Map();this.tasks=new Map();this.requests=new Map();this.coarseCache=new Map();this.blockedCoarse=new Set();this.serial=0;this.epoch=0;this.clock=0;this.disposed=false;this.workQueue=Promise.resolve();
  this.worker=new Worker(new URL('./区域寻路工作器.mjs',import.meta.url),{type:'module'});
  this.ready=new Promise((resolve,reject)=>{
   const failed=e=>{this.workerError=new Error(e.message??'区域寻路工作器通信失败');reject(this.workerError);for(const task of this.tasks.values()){task.state='error';task.error=this.workerError.message;}for(const waiter of this.requests.values())waiter({state:'error',error:this.workerError.message,path:[]});this.requests.clear();};
   this.worker.onerror=failed;this.worker.onmessageerror=failed;
   this.worker.onmessage=e=>{if(e.data.type==='ready')resolve();else{const waiter=this.requests.get(e.data.id);if(waiter){this.requests.delete(e.data.id);waiter(e.data);}}};
  });
  this.static={query:(x,z,r)=>this.near(rectangle({x,z},r)).filter(s=>s.movement),segment:(a,b,r=0)=>this.near({minX:Math.min(a.x,b.x)-r,maxX:Math.max(a.x,b.x)+r,minZ:Math.min(a.z,b.z)-r,maxZ:Math.max(a.z,b.z)+r}).filter(s=>s.ballistics)};
 }
 clamp(p,pad=5){const b=this.map.bounds;return {...p,x:clamp(p.x,b.minX+pad,b.maxX-pad),z:clamp(p.z,b.minZ+pad,b.maxZ-pad)};}
 ground(x,z,referenceY){
  const highest=this.map.querySupport(x,z);if(highest.state!=='ready')return highest;
  // 0.3米公园和地表台面合并为同一行走层，保留真正分离表面的参考高度选择。
  return Number.isFinite(referenceY)&&highest.layers?.some(h=>highest.height-h.height>1)?this.map.querySupport(x,z,{referenceY}):highest;
 }
 near(bounds){const query=this.map.collisionsIn(bounds);return query.state==='ready'?query.shapes:[];}
 walkable(x,z,pad=5.8,hint){
  const b=this.map.bounds;if(x<=b.minX+pad||x>=b.maxX-pad||z<=b.minZ+pad||z>=b.maxZ-pad)return false;
  const h=this.ground(x,z,hint?.from?.y??hint?.referenceY);if(h.state!=='ready'||h.normalY<Math.cos(Math.PI/6)||h.waterDepth>.25)return false;
  if(hint?.from&&Math.abs(h.height-hint.from.y)>.35+Math.tan(Math.PI/6)*dist(hint.from,{x,z}))return false;
  for(const [dx,dz] of [[pad,0],[-pad,0],[0,pad],[0,-pad]]){const q=this.ground(x+dx,z+dz,h.height);if(q.state!=='ready'||Math.abs(q.height-h.height)>Math.tan(Math.PI/6)*pad)return false;}
  const obstacles=this.map.collisionsIn(rectangle({x,z},pad));if(obstacles.state!=='ready')return false;
  return !obstacles.shapes.some(shape=>shape.overlaps(x,z,h.height,pad));
 }
 destinationStatus(p){
  const b=this.map.bounds,r=this.radius;if(p.x<=b.minX+r||p.x>=b.maxX-r||p.z<=b.minZ+r||p.z>=b.maxZ-r)return{status:'rejected',reason:'目的地距地图边界过近'};
  const q=this.ground(p.x,p.z,p.y);if(q.state==='pending')return{status:'pending',reason:'准备路径 · 目标地形尚未加载'};
  if(q.state!=='ready')return{status:'rejected',reason:'目的地没有有效地面支撑'};
  if(q.normalY<Math.cos(Math.PI/6))return{status:'rejected',reason:'目的地坡度超过30度'};
  if(q.waterDepth>.25)return{status:'rejected',reason:'目的地水域不可通行'};
  for(const [dx,dz] of [[r,0],[-r,0],[0,r],[0,-r]]){const side=this.ground(p.x+dx,p.z+dz,q.height);if(side.state==='pending')return{status:'pending',reason:'准备路径 · 目标周边地形尚未加载'};if(side.state!=='ready'||Math.abs(side.height-q.height)>Math.tan(Math.PI/6)*r)return{status:'rejected',reason:'目的地周边无连续可通行支撑'};}
  const collision=this.map.collisionsIn(rectangle(p,r));if(collision.state!=='ready')return{status:'pending',reason:'准备路径 · 目标碰撞数据尚未加载'};
  return collision.shapes.some(shape=>shape.overlaps(p.x,p.z,q.height,r))?{status:'rejected',reason:'目的地被静态障碍占用'}:{status:'ready',reason:''};
 }
 destinationState(p){const result=this.destinationStatus(p);return result.status==='rejected'?'unreachable':result.status;}
 freePoint(point){
  const p=this.clamp(point,8);if(this.walkable(p.x,p.z,this.radius))return p;
  for(let r=4;r<=220;r+=4)for(let i=0,n=Math.max(8,Math.ceil(r*1.5));i<n;i++){const angle=i/n*Math.PI*2,q=this.clamp({x:p.x+Math.cos(angle)*r,z:p.z+Math.sin(angle)*r},8);if(this.walkable(q.x,q.z,this.radius))return q;}
  throw new Error('已准备区域内没有安全出生点');
 }
 groundBlocked(a,b,pad=this.radius){
  const n=Math.max(1,Math.ceil(dist(a,b)/2));let previous=this.ground(a.x,a.z,a.y);
  if(previous.state!=='ready')return true;
  for(let i=1;i<=n;i++){const t=i/n,x=a.x+(b.x-a.x)*t,z=a.z+(b.z-a.z)*t,q=this.ground(x,z,previous.height);
   if(q.state!=='ready'||!this.walkable(x,z,pad,{referenceY:q.height})||Math.abs(q.height-previous.height)>.35+Math.tan(Math.PI/6)*dist(a,b)/n)return true;previous=q;
  }return false;
 }
 terrainHit(a,b){
  if(this.map.supportSegment)return this.map.supportSegment(a,b).hit;
  const n=Math.max(1,Math.ceil(dist(a,b)));let previous=0;
  for(let i=0;i<=n;i++){const t=i/n,x=a.x+(b.x-a.x)*t,z=a.z+(b.z-a.z)*t,q=this.map.querySupport(x,z);if(q.state!=='ready'){previous=t;continue;}
   const h=Math.max(q.height,Number.isFinite(q.waterHeight)?q.waterHeight:-Infinity);if(a.y+(b.y-a.y)*t<=h){let lo=previous,hi=t;for(let k=0;k<14;k++){const m=(lo+hi)/2,p=this.map.querySupport(a.x+(b.x-a.x)*m,a.z+(b.z-a.z)*m),y=Math.max(p.height,Number.isFinite(p.waterHeight)?p.waterHeight:-Infinity);if(a.y+(b.y-a.y)*m>y)lo=m;else hi=m;}return hi;}previous=t;
  }return null;
 }
 blocked(a,b,pad=.12){
  const bounds={minX:Math.min(a.x,b.x)-pad,maxX:Math.max(a.x,b.x)+pad,minZ:Math.min(a.z,b.z)-pad,maxZ:Math.max(a.z,b.z)+pad},data=this.map.collisionsIn(bounds);
  if(data.state!=='ready')return true;
  for(const shape of data.shapes)if(shape.ballistics&&shape.segment(a,b,pad)!==null)return true;
  const n=Math.max(1,Math.ceil(dist(a,b)/16));for(let i=0;i<=n;i++){const t=i/n,q=this.map.querySupport(a.x+(b.x-a.x)*t,a.z+(b.z-a.z)*t);if(q.state==='pending')return true;}
  return this.terrainHit(a,b)!==null;
 }
 segmentReady(a,b){return this.map.supportSegment(a,b).state==='ready';}
 async message(data){await this.ready;if(this.workerError)throw this.workerError;const id=++this.serial;return new Promise(resolve=>{this.requests.set(id,resolve);this.worker.postMessage({...data,id});});}
 async roughGoal(from,end){
  if(dist(from,end)<=180||!this.map.navigation_graph)return {point:end};
  if(!this.graphPromise)this.graphPromise=this.map.downloads.get(new URL(this.map.navigation_graph.file,this.map.packageBase).href,this.map.navigation_graph.identity).then(buffer=>{this.graph=JSON.parse(new TextDecoder().decode(buffer));return this.graph;}).catch(error=>{this.graphPromise=null;throw error;});
  const graph=await this.graphPromise,[nx,nz]=graph.grid,b=graph.bounds,index=p=>clamp(Math.floor((p.z-b.minZ)/125),0,nz-1)*nx+clamp(Math.floor((p.x-b.minX)/125),0,nx-1),start=index(from),finish=index(end),cacheKey=start+':'+finish;
  let route=this.coarseCache.get(cacheKey);
  if(!route){
   const cost=new Float64Array(nx*nz).fill(Infinity),parent=new Int32Array(nx*nz).fill(-1),closed=new Uint8Array(nx*nz),open=[start];cost[start]=0;
   const heuristic=id=>Math.hypot(id%nx-finish%nx,Math.floor(id/nx)-Math.floor(finish/nx));
   while(open.length){let best=0;for(let i=1;i<open.length;i++)if(cost[open[i]]+heuristic(open[i])<cost[open[best]]+heuristic(open[best]))best=i;const id=open.splice(best,1)[0];if(closed[id])continue;if(id===finish)break;closed[id]=1;
    const x=id%nx,z=Math.floor(id/nx);for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,1],[1,-1],[-1,-1]]){const xx=x+dx,zz=z+dz,next=zz*nx+xx;if(xx<0||zz<0||xx>=nx||zz>=nz||closed[next]||this.blockedCoarse.has(id+':'+next))continue;const g=cost[id]+Math.hypot(dx,dz)*graph.nodes[next].cost;if(g<cost[next]){cost[next]=g;parent[next]=id;open.push(next);}}
   }
   if(!Number.isFinite(cost[finish]))return {point:end,unreachable:true};route=[];for(let id=finish;id>=0;id=parent[id]){route.push(id);if(id===start)break;}route.reverse();this.coarseCache.set(cacheKey,route);if(this.coarseCache.size>64)this.coarseCache.delete(this.coarseCache.keys().next().value);
  }
  let next=route[1];if(next===undefined)return {point:end};for(const id of route.slice(1)){const node=graph.nodes[id];if(dist(from,node)>180)break;next=id;}
  return {point:{x:graph.nodes[next].x,z:graph.nodes[next].z},edge:[route[0],route[1]]};
 }
 async patchFor(from){
  const cx=Math.round(from.x/250)*250,cz=Math.round(from.z/250)*250,key=`${cx}:${cz}:${Math.round((from.y??0)/2)}`;let row=this.patches.get(key);if(row){row.used=++this.clock;return row.promise;}
  const b=this.map.bounds,bounds={minX:Math.max(b.minX,cx-250),maxX:Math.min(b.maxX,cx+250),minZ:Math.max(b.minZ,cz-250),maxZ:Math.min(b.maxZ,cz+250)},owner='navigation:'+this.epoch+':'+key,epoch=this.epoch;
  row={owner,used:++this.clock,key,bounds};this.patches.set(key,row);
  row.promise=(async()=>{
   await this.map.prepareRegion(owner,{minX:bounds.minX-8,maxX:bounds.maxX+8,minZ:bounds.minZ-8,maxZ:bounds.maxZ+8});
   if(epoch!==this.epoch||this.disposed)throw new DOMException('路线已取消','AbortError');
   const cell=4,nx=Math.floor((bounds.maxX-bounds.minX-8)/cell)+1,nz=Math.floor((bounds.maxZ-bounds.minZ-8)/cell)+1,x0=bounds.minX+4,z0=bounds.minZ+4,passable=new Uint8Array(nx*nz);
   let start=performance.now();for(let id=0;id<passable.length;id++){passable[id]=this.walkable(x0+id%nx*cell,z0+Math.floor(id/nx)*cell,this.radius,{referenceY:from.y})?1:0;
    if(id%64===0&&performance.now()-start>(from.startup?8:1.5)){await frame();start=performance.now();if(epoch!==this.epoch)throw new DOMException('路线已取消','AbortError');}
   }
   row.patch={cell,nx,nz,x0,z0,passable};await this.message({type:'patch',key,patch:row.patch});if(epoch!==this.epoch)throw new DOMException('路线已取消','AbortError');this.trimPatches(key);return row;
  })().catch(error=>{if(this.patches.get(key)===row)this.patches.delete(key);this.map.releaseRegion(owner);throw error;});return row.promise;
 }
 trimPatches(keep){for(const [key,row] of [...this.patches].sort((a,b)=>a[1].used-b[1].used)){if(this.patches.size<=6)break;if(key===keep||!row.patch)continue;this.patches.delete(key);this.map.releaseRegion(row.owner);this.worker.postMessage({type:'release',key});}}
 path(from,to,air=false){
  const end={...to},owner=from.id??'probe',b=this.map.bounds;
  if(!Number.isFinite(end.x)||!Number.isFinite(end.z)||end.x<b.minX||end.x>b.maxX||end.z<b.minZ||end.z>b.maxZ)return[];
  if(air)return[end];
  if(this.walkable(end.x,end.z,this.radius)&&!this.groundBlocked(from,end,this.radius)){this.tasks.delete(owner);return[end];}
  const signature=`${from.revision??0}:${end.x.toFixed(2)}:${end.z.toFixed(2)}`,existing=this.tasks.get(owner);
  if(existing?.signature===signature&&existing.state==='ready'&&dist(from,existing.from)<5)return existing.path.map(p=>({...p}));
  if(existing?.signature===signature&&existing.state==='pending')return [];
  if(existing?.signature===signature&&existing.state==='unreachable')return [];
  if(existing?.signature===signature&&existing.state==='error')return [];
  if(existing?.signature===signature&&existing.state==='reroute'&&performance.now()<existing.retryAt)return [];
  const task={signature,state:'pending',from:{x:from.x,y:from.y,z:from.z},end,path:[],epoch:this.epoch};this.tasks.set(owner,task);
  this.workQueue=this.workQueue.catch(()=>{}).then(async()=>{
   if(this.tasks.get(owner)!==task||task.epoch!==this.epoch)return;
   const rough=await this.roughGoal(from,end);if(this.tasks.get(owner)!==task||task.epoch!==this.epoch)return;if(rough.unreachable){task.state='unreachable';return;}
   const row=await this.patchFor(from);if(this.tasks.get(owner)!==task||task.epoch!==this.epoch)return;const b=row.bounds,goal={...end,x:clamp(rough.point.x,b.minX+8,b.maxX-8),z:clamp(rough.point.z,b.minZ+8,b.maxZ-8)},blocked=[];
   for(let attempt=0;attempt<16;attempt++){
    const result=await this.message({type:'path',key:row.key,from:task.from,to:goal,blocked});if(this.tasks.get(owner)!==task||task.epoch!==this.epoch)return;
    if(result.state!=='ready'){if(result.state==='error'){task.state='error';task.error=result.error??'区域路线计算失败';}else this.blockRough(task,rough);return;}
    let a=task.from,failed=false,start=performance.now();
    for(let i=0;i<result.path.length;i++){
     const point=result.path[i];if(this.groundBlocked(a,point)){const previous=i?result.ids[i-1]:-1,current=result.ids[i];blocked.push([previous,current],[current,previous]);failed=true;}
     a=point;if(performance.now()-start>1.5){await frame();start=performance.now();}
    }
    if(failed)continue;
    const partial=dist(goal,end)>4;
    // 网格端点只用于中继和搜索；最终一段必须复核并连接工程师指定的原终点。
    if(!partial&&this.groundBlocked(result.effectiveEnd,end)){this.blockRough(task,rough);return;}
    const path=[];for(let i=0;i<result.path.length;i++){const p=result.path[i],prev=result.path[i-1],next=result.path[i+1];if(prev&&next&&(p.x-prev.x)*(next.z-p.z)===(p.z-prev.z)*(next.x-p.x))continue;path.push(p);}
    if(!partial&&(path.length===0||dist(path.at(-1),end)>.001))path.push({...end});
    if(dist(task.from,result.effectiveEnd)<2&&dist(task.from,end)>8){this.blockRough(task,rough);return;}
    task.path=path;task.effectiveEnd=partial?result.effectiveEnd:{...end};task.partial=partial;task.state='ready';return;
   }this.blockRough(task,rough);
  }).catch(error=>{if(this.tasks.get(owner)!==task||task.epoch!==this.epoch)return;task.state=error.name==='AbortError'?'cancelled':'error';task.error=String(error);});return [];
 }
 pending(unit){return ['pending','reroute'].includes(this.tasks.get(unit.id)?.state);}
 status(unit){const task=this.tasks.get(unit.id);if(!task)return{status:'ready',reason:''};if(task.state==='pending'||(task.state==='reroute'&&performance.now()<task.retryAt))return{status:'pending',reason:task.state==='reroute'?'准备路径 · 正在核对绕行路线':'准备路径 · 区域路线计算中'};if(task.state==='unreachable')return{status:'rejected',reason:'没有连通到目的地的路线'};if(task.state==='error')return{status:'rejected',reason:'准备路径失败 · '+(task.error??'区域数据或路线计算错误')};return{status:'ready',reason:''};}
 blockRough(task,rough){if(rough.edge){this.blockedCoarse.add(rough.edge[0]+':'+rough.edge[1]);if(this.blockedCoarse.size>1024)this.blockedCoarse.delete(this.blockedCoarse.keys().next().value);this.coarseCache.clear();task.state='reroute';task.retryAt=performance.now()+100;task.error='准备绕行路线';}else task.state='unreachable';}
 pathEnded(unit,to){const task=this.tasks.get(unit.id);if(!task)return false;if(task.partial){this.tasks.delete(unit.id);return false;}if(dist(unit,to)>1.1){task.state='unreachable';return false;}return true;}
 cancel(unit){this.tasks.delete(unit.id);this.map.releaseCommandRegion?.(unit.id);}
 reset(keepPatches=true){this.epoch++;this.tasks.clear();for(const [key,row] of this.patches)if(!keepPatches||!row.patch){this.map.releaseRegion(row.owner);this.patches.delete(key);}if(!keepPatches)this.worker.postMessage({type:'reset'});}
 stats(){return {patches:this.patches.size,pending:[...this.tasks.values()].filter(t=>t.state==='pending').length,bytes:[...this.patches.values()].reduce((n,p)=>n+(p.patch?.passable.byteLength??0)*2,0)+[...this.tasks.values()].reduce((n,t)=>n+t.path.length*64,0)+(this.graph?700000:0)+500000};}
 dispose(){this.disposed=true;this.reset(false);for(const resolve of this.requests.values())resolve({state:'cancelled',path:[]});this.requests.clear();this.worker.terminate();}
}

export async function attachStreamingSimulation(map){
 const started=performance.now();
 map.data.limit=map.budgets.simulation-6*1048576;
 const navigator=new StreamingNavigator(map);await navigator.ready;
 await map.prepareRegion('spawn',{minX:-430,maxX:430,minZ:-150,maxZ:600});
 await navigator.patchFor({x:0,z:85,y:map.heightAt(0,85),startup:true});
 map.stats.startupTimings.navigationReady=performance.now()-started;
 map.createNavigator=()=>navigator;map.groundForUnit=(x,z,y)=>navigator.ground(x,z,y).height;
 const owners=new Map(),failedCommands=new Map();let epoch=0,targets=[],commandAdmissionAfter=0;
 const commandPoint=u=>{const job=u.job;if(!job)return null;const p=job.point??targets.find(t=>t.id===job.target);return p&&Number.isFinite(p.x)&&Number.isFinite(p.z)?p:null;};
 const goalKey=(u,p)=>`${u.revision??0}:${u.job?.type}:${p.x.toFixed(3)}:${p.z.toFixed(3)}`;
 const inside=p=>p.x>=map.bounds.minX&&p.x<=map.bounds.maxX&&p.z>=map.bounds.minZ&&p.z<=map.bounds.maxZ;
 const release=owner=>{owners.delete(owner);map.releaseRegion(owner);};
 map.releaseCommandRegion=id=>{release('command:'+id);failedCommands.delete(id);};
 const releaseCommands=()=>{const names=new Set([...owners.keys(),...map.data.owners.keys(),...map.data.operations.keys()]);for(const name of names)if(name.startsWith('command:'))release(name);commandAdmissionAfter=performance.now()+1000;};
 const prepare=(owner,box,desired,command=false,signature)=>{
  desired.add(owner);const key=epoch+':'+map.data.ids(box).join(','),previous=owners.get(owner);
  if(previous?.key===key){previous.signature=signature;return;}
  if(command){if(previous)release(owner);if(performance.now()<commandAdmissionAfter||!map.data.canPrepare(box,12*1048576))return;}
  const lease={key,signature};owners.set(owner,lease);
  map.prepareRegion(owner,box).catch(error=>{
   // 被新命令替代的旧请求不得删除新租约，也不得把失败写入新命令。
   if(owners.get(owner)!==lease)return;owners.delete(owner);
   if(error.name!=='AbortError'){map.stats.dataWait=String(error);if(owner.startsWith('command:'))failedCommands.set(owner.slice(8),{signature:lease.signature,reason:'准备路径失败 · '+String(error.message??error)});if(owner.startsWith('unit:')&&String(error).includes('预算'))releaseCommands();}
  });
 };
 map.commandStatus=u=>{
  const point=commandPoint(u);if(!point)return{status:'ready',reason:''};const signature=goalKey(u,point),failure=failedCommands.get(u.id);
  if(!inside(point))return{status:'rejected',reason:'目的地超出地图范围'};
  if(failure?.signature===signature)return{status:'rejected',reason:failure.reason};
  const lease=owners.get('command:'+u.id);if(lease?.signature!==signature||!map.data.owners.has('command:'+u.id))return{status:'pending',reason:'准备路径 · 等待目标区域数据'};
  const support=map.querySupport(point.x,point.z);if(support.state==='pending')return{status:'pending',reason:'准备路径 · 目标地形尚未加载'};
  const groundMove=u.kind==='tank'&&['move','reverse','attackMove'].includes(u.job.type);
  const task=navigator.tasks.get(u.id),taskSignature=`${u.revision??0}:${point.x.toFixed(2)}:${point.z.toFixed(2)}`;
  let result={status:'ready',reason:''};
  if(support.state!=='ready'&&!(u.kind==='plane'&&u.job.type==='move'))result={status:'rejected',reason:'目的地没有有效地面支撑'};
  else if(groundMove){result=navigator.destinationStatus(point);if(result.status==='ready'&&task?.signature===taskSignature)result=navigator.status(u);}
  if(result.status==='rejected')failedCommands.set(u.id,{signature,reason:result.reason});return result;
 };
 map.commandState=u=>{const state=map.commandStatus(u).status;return state==='rejected'?'unreachable':state;};
 map.syncSimulation=(units,projectiles,newTargets=targets)=>{
  targets=newTargets;const desired=new Set();let pendingCommands=0,readyCommands=0;
  for(const u of units)prepare('unit:'+u.id,rectangle(u,u.kind==='plane'?420:u.kind==='heli'?128:96),desired);
  for(const p of projectiles){const position=p.position??p;if(Number.isFinite(position.x)&&Number.isFinite(position.z))prepare('projectile:'+p.id,rectangle(position,12),desired);}
  for(const u of units){const point=commandPoint(u);if(!point||u.job?.status==='rejected')continue;const signature=goalKey(u,point);if(!inside(point)||failedCommands.get(u.id)?.signature===signature)continue;prepare('command:'+u.id,rectangle(point,8),desired,true,signature);const lease=owners.get('command:'+u.id);if(lease?.signature===signature&&map.data.owners.has('command:'+u.id))readyCommands++;else pendingCommands++;}
  for(const owner of owners.keys())if(!desired.has(owner))release(owner);
  const stats=navigator.stats();Object.assign(map.stats,{navigationBytes:stats.bytes,navigationPatches:stats.patches,waitingRoutes:stats.pending,commandTargetsPending:pendingCommands,commandTargetsReady:readyCommands});
 };
 map.resetSimulation=()=>{epoch++;navigator.reset();const names=new Set([...owners.keys(),...map.data.owners.keys(),...map.data.operations.keys()]);for(const owner of names)if(owner.startsWith('unit:')||owner.startsWith('projectile:')||owner.startsWith('command:'))release(owner);owners.clear();failedCommands.clear();targets=[];commandAdmissionAfter=0;};
 map.waitForSimulationReady=async(units,projectiles)=>{
  const deadline=performance.now()+45000;
  while(true){map.syncSimulation(units,projectiles);const jobs=[...map.data.entries.values()].map(e=>e.promise).filter(Boolean);
   if(!jobs.length&&units.every(u=>map.data.owners.has('unit:'+u.id)))return;
   if(performance.now()>deadline)throw new Error('默认单位模拟数据未能准备完成');
   if(jobs.length)await Promise.allSettled(jobs);else await new Promise(resolve=>setTimeout(resolve,100));
  }
 };
 const dispose=map.dispose;map.dispose=()=>{map.resetSimulation();navigator.dispose();map.releaseRegion('spawn');dispose();};
 map.navigation=navigator;return map;
}
