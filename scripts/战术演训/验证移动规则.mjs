/** G01移动规则验证：纯规则、真实局部寻路及隔离地图租约，不启动服务或浏览器。 */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Simulation} from '../../src/战术演训/模拟.mjs';
import {Navigator} from '../../src/战术演训/寻路.mjs';
import {localPath} from '../../src/战术演训/地图适配/区域寻路工作器.mjs';

const checks=[];
const check=(name,run)=>checks.push({name,run});
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const flatMap=()=>({id:'移动规则沙盒',bounds:{minX:-600,maxX:600,minZ:-600,maxZ:600},cell:4,obstacles:[],targets:[],center:{x:0,z:0}});
function sample(composition={tank:3,heli:0,plane:0}){return new Simulation({map:flatMap(),composition,total:Object.values(composition).reduce((a,b)=>a+b,0)});}
const point=(x,z)=>({x,z});
const close=(actual,expected,message)=>assert.ok(Math.abs(actual-expected)<1e-6,message??`${actual}与${expected}不符`);

check('地形地图契约下待命与静态压力保留出生布局，不扫描锚点或产生自动战斗',()=>{
 for(const mode of ['idle','manual','static']){
  const sim=sample({tank:2,heli:1,plane:1}),ids=sim.units.map(u=>u.id);sim.map.heightAt=()=>0;sim.map.safeAltitude=72;sim.map.helicopterAltitude=35;
  sim.command(ids,{type:'move',point:point(100,100)});sim.command(ids,{type:'move',point:point(150,150)},true);sim.shots=3;sim.hits=2;sim.kills=1;
  const positions=sim.units.map(u=>({x:u.x,y:u.y,z:u.z})),targets=sim.targets;
  // 如误进旧负载网格扫描，立即失败；默认出生freePoint已在构造阶段完成。
  sim.nav.walkable=()=>{throw new Error('待命模式不应查询负载锚点');};
  assert.doesNotThrow(()=>sim.configureLoad(mode));assert.deepEqual(sim.units.map(u=>({x:u.x,y:u.y,z:u.z})),positions);assert.equal(sim.targets,targets);
  for(let i=0;i<80;i++)sim.step(.025);
  assert.equal(sim.shots,0);assert.equal(sim.hits,0);assert.equal(sim.kills,0);assert.equal(sim.projectiles.length,0);assert.equal(sim.recoverTargets,false);
  assert.ok(sim.units.every(u=>!u.job&&!u.queue.length&&[u.x,u.y,u.z].every(Number.isFinite)));
  assert.deepEqual(sim.units.filter(u=>u.kind!=='plane').map(u=>({x:u.x,z:u.z})),positions.slice(0,3).map(({x,z})=>({x,z})));
 }
});

check('预览不提交，普通新命令替换当前任务并清队，Shift保留前序',()=>{
 const sim=sample({tank:1}),u=sim.units[0],ids=[u.id];
 sim.command(ids,{type:'move',point:point(80,20)});const first=u.job;
 sim.command(ids,{type:'move',point:point(160,20)},true);const second=u.queue[0];
 const before=JSON.stringify({job:u.job,queue:u.queue,revision:u.revision});
 sim.planCommand(ids,{type:'move',point:point(230,30)},true);
 assert.equal(JSON.stringify({job:u.job,queue:u.queue,revision:u.revision}),before);
 sim.command(ids,{type:'move',point:point(240,20)},true);assert.equal(u.job,first);assert.equal(u.queue[0],second);assert.equal(u.queue.length,2);
 sim.command(ids,{type:'move',point:point(-80,20)});assert.deepEqual(u.job.point,point(-80,20));assert.equal(u.queue.length,0);assert.equal(first.status,'cancelled');assert.equal(second.status,'cancelled');
});

check('普通多车平移保持相对阵型并拉开近车间距，朝向拖动展开横队',()=>{
 const sim=sample(),[a,b,c]=sim.units;a.x=0;a.z=0;b.x=4;b.z=0;c.x=0;c.z=8;
 const rows=sim.planCommand(sim.units.map(u=>u.id),{type:'move',point:point(100,100)});
 assert.ok(rows.every(r=>r.valid));close(rows[1].job.point.x-rows[0].job.point.x,12);close(rows[2].job.point.z-rows[0].job.point.z,24);
 close(rows.reduce((sum,r)=>sum+r.job.point.x,0)/3,100);close(rows.reduce((sum,r)=>sum+r.job.point.z,0)/3,100);
 const line=sim.planCommand(sim.units.map(u=>u.id),{type:'move',point:point(100,100),facing:Math.PI/2});
 for(const row of line)close(row.job.point.x,100);close(line[1].job.point.z-line[0].job.point.z,-12);close(line[2].job.point.z-line[1].job.point.z,-12);
});

check('Shift编队依据队尾落点，普通新命令依据真实当前位置',()=>{
 const sim=sample({tank:2}),[a,b]=sim.units;a.x=-20;a.z=0;b.x=20;b.z=0;const ids=[a.id,b.id];
 sim.command(ids,{type:'move',point:point(100,100),facing:Math.PI/2});
 sim.command(ids,{type:'move',point:point(160,160)},true);
 a.x=-90;a.z=0;b.x=90;b.z=0;
 const appended=sim.planCommand(ids,{type:'move',point:point(200,200)},true),normal=sim.planCommand(ids,{type:'move',point:point(200,200)});
 close(appended[1].job.point.x-appended[0].job.point.x,0);close(appended[1].job.point.z-appended[0].job.point.z,-12);
 close(normal[1].job.point.x-normal[0].job.point.x,180);close(normal[1].job.point.z-normal[0].job.point.z,0);
});

check('三段移动真实依序执行，到达朝向后完成且普通指令不会恢复被替换队列',()=>{
 const sim=sample({tank:1}),u=sim.units[0],ids=[u.id];u.x=0;u.z=0;
 sim.command(ids,{type:'move',point:point(12,0)});const first=u.job;
 sim.command(ids,{type:'move',point:point(12,12)},true);const second=u.queue[0];
 sim.command(ids,{type:'move',point:point(24,12),facing:Math.PI/2},true);const third=u.queue[1],order=[];let previous=u.job.id;
 for(let i=0;i<2400&&u.job;i++){sim.step(.025);if(u.job?.id!==previous){order.push(previous);previous=u.job?.id;}}
 assert.equal(u.job,null);assert.deepEqual(order,[first.id,second.id,third.id]);assert.equal(first.status,'completed');assert.equal(second.status,'completed');assert.equal(third.status,'completed');assert.ok(Math.hypot(u.x-24,u.z-12)<1.1);assert.ok(Math.abs(Math.atan2(Math.sin(u.yaw-Math.PI/2),Math.cos(u.yaw-Math.PI/2)))<.026);
 sim.command(ids,{type:'move',point:point(80,0)});sim.command(ids,{type:'move',point:point(100,0)},true);sim.command(ids,{type:'move',point:point(24,24)});
 for(let i=0;i<1200&&u.job;i++)sim.step(.025);assert.equal(u.job,null);assert.equal(u.queue.length,0);assert.ok(Math.hypot(u.x-24,u.z-24)<1.1);
 assert.doesNotThrow(()=>JSON.stringify(sim.lastCommandResult));
});

check('混选分别布置地面和旋翼落点，固定翼接受目标附近盘旋',()=>{
 const sim=sample({tank:1,heli:1,plane:1}),ids=sim.units.map(u=>u.id),rows=sim.planCommand(ids,{type:'move',point:point(100,100)});
 assert.equal(rows.length,3);assert.ok(rows.every(r=>r.valid));const [tank,heli,plane]=rows;
 assert.notDeepEqual(tank.job.point,heli.job.point);assert.deepEqual(plane.job.point,point(100,100));
 sim.command(ids,{type:'move',point:point(100,100)});const aircraft=plane.unit;aircraft.x=100;aircraft.z=100;
 sim.step(.05);assert.equal(aircraft.job,null);assert.deepEqual(aircraft.flight.home,point(100,100));assert.equal(aircraft.moving,true);
});

check('停止清空全部移动队列；地面停车、旋翼悬停、固定翼持续飞行',()=>{
 const sim=sample({tank:1,heli:1,plane:1}),ids=sim.units.map(u=>u.id);
 sim.command(ids,{type:'move',point:point(100,100)});sim.command(ids,{type:'move',point:point(180,180)},true);
 sim.cancel(ids);const positions=sim.units.map(u=>point(u.x,u.z));sim.step(.2);
 for(const u of sim.units){assert.equal(u.job,null);assert.equal(u.queue.length,0);assert.equal(u.commandStatus.status,'stopped');}
 assert.deepEqual(point(sim.units[0].x,sim.units[0].z),positions[0]);assert.deepEqual(point(sim.units[1].x,sim.units[1].z),positions[1]);
 assert.notDeepEqual(point(sim.units[2].x,sim.units[2].z),positions[2]);assert.equal(sim.units[2].moving,true);
});

check('固定翼边界机动保留航点和后续队列，位置持续变化且不瞬移',()=>{
 const sim=sample({plane:1}),u=sim.units[0];sim.command([u.id],{type:'move',point:point(100,100)});sim.command([u.id],{type:'move',point:point(200,200)},true);const current=u.job,next=u.queue[0];u.x=1600;u.z=0;const before=point(u.x,u.z);
 sim.step(.1);assert.equal(u.job,current);assert.equal(u.queue[0],next);assert.equal(current.status,'executing');assert.equal(u.state,'边界回转');assert.ok(Math.hypot(u.x-before.x,u.z-before.z)<6);
});

check('非法终点不被改到近点，单项拒绝保留旧任务，其余可达继续',()=>{
 const sim=sample({tank:1,heli:1}),tank=sim.units[0],heli=sim.units[1];
 sim.command([tank.id],{type:'move',point:point(100,0)});const old=tank.job;
 sim.map.obstacles.push({x:200,z:190,w:30,d:30,h:10});sim.nav=new Navigator(sim.map);
 const rows=sim.planCommand([tank.id,heli.id],{type:'move',point:point(200,200)});assert.equal(rows[0].valid,false);assert.equal(rows[1].valid,true);
 assert.equal(sim.command([tank.id,heli.id],{type:'move',point:point(200,200)}),1);assert.equal(tank.job,old);assert.equal(tank.lastRejectedCommand.status,'rejected');assert.ok(tank.lastRejectedCommand.reason);assert.equal(heli.job.type,'move');
 assert.deepEqual(sim.nav.path(tank,point(200,190)),[]);assert.deepEqual(sim.nav.path(tank,point(900,0)),[]);
 assert.equal(sim.lastCommandResult.accepted,1);assert.equal(sim.lastCommandResult.rejected,1);
});

check('未知地形先准备，异步真正失败持续可读且不恢复旧任务',()=>{
 const sim=sample({tank:1}),u=sim.units[0];sim.command([u.id],{type:'move',point:point(50,0)});const old=u.job;
 let terrain='pending',runtime='pending';sim.map.querySupport=()=>({state:terrain,height:0});sim.map.commandStatus=()=>({status:runtime,reason:runtime==='pending'?'准备路径 · 等待目标区域':'准备路径失败 · 校验错误'});
 assert.equal(sim.command([u.id],{type:'move',point:point(200,0)}),1);assert.equal(old.status,'cancelled');assert.equal(sim.lastCommandResult.pending,1);assert.equal(u.job.status,'pending');
 sim.step(.05);assert.match(u.state,/准备路径/);terrain='ready';runtime='rejected';sim.step(.05);
 assert.equal(u.job.status,'rejected');assert.deepEqual(u.job.point,point(200,0));assert.equal(sim.lastCommandResult.accepted,0);assert.equal(sim.lastCommandResult.rejected,1);assert.equal(sim.lastCommandResult.pending,0);
 sim.step(.05);assert.equal(u.job.status,'rejected');assert.notEqual(u.job,old);
});

// 浏览器绝对模块路由在Node中替换为同一真实源码URL，算法和地图租约实现保持原字节。
const mapURL=new URL('../../src/战术演训/地图.mjs',import.meta.url).href,workerURL=new URL('../../src/战术演训/地图适配/区域寻路工作器.mjs',import.meta.url).href;
const adapterSource=(await readFile(new URL('../../src/战术演训/地图适配/分区寻路.mjs',import.meta.url),'utf8')).replace("from '/地图.mjs'",`from '${mapURL}'`).replace("new URL('./区域寻路工作器.mjs',import.meta.url)",`new URL('${workerURL}')`);
class TestWorker{
 constructor(){this.patches=new Map();queueMicrotask(()=>this.onmessage?.({data:{type:'ready'}}));}
 postMessage(m){queueMicrotask(()=>{let result={};if(m.type==='patch')this.patches.set(m.key,m.patch);else if(m.type==='release')this.patches.delete(m.key);else if(m.type==='path')result=localPath(this.patches.get(m.key),m.from,m.to,m.blocked);this.onmessage?.({data:{id:m.id,type:m.type,...result}});});}
 terminate(){}
}
globalThis.Worker=TestWorker;globalThis.requestAnimationFrame=fn=>setImmediate(()=>fn(performance.now()));
const {StreamingNavigator,attachStreamingSimulation}=await import('data:text/javascript;base64,'+Buffer.from(adapterSource).toString('base64'));
function streamingMap(){
 const map={...flatMap(),streaming:true,bounds:{minX:-2000,maxX:2000,minZ:-2000,maxZ:2000},stats:{startupTimings:{}},budgets:{simulation:64*1048576},gates:new Map(),pendingRegions:[],querySupport:()=>({state:'ready',height:0,normalY:1,waterDepth:0}),collisionsIn:()=>({state:'ready',shapes:[]}),heightAt:()=>0,dispose(){}};
 map.data={owners:new Map(),operations:new Map(),entries:new Map(),ids:box=>[Math.floor(box.minX/125),Math.floor(box.minZ/125)],canPrepare:()=>true};
 map.prepareRegion=(owner,box)=>{const gate=map.gates.get(owner);if(gate){map.gates.delete(owner);map.pendingRegions.push({owner,box,gate});return gate.promise.then(()=>{map.data.owners.set(owner,box);});}map.data.owners.set(owner,box);return Promise.resolve();};
 map.releaseRegion=owner=>map.data.owners.delete(owner);return map;
}

check('真实分区寻路保留精确终点，不把网格近点当最终落点',async()=>{
 const map=streamingMap(),nav=new StreamingNavigator(map);await nav.ready;
 const end=point(101.3,79.7),u={id:'tank-1',x:0,z:0,y:0,revision:1,job:{type:'move',point:end}};
 // 强制走真实工作器网格分支，最终连接仍使用实际连续复核。
 const original=nav.groundBlocked.bind(nav);let first=true;nav.groundBlocked=(a,b,...rest)=>{if(first){first=false;return true;}return original(a,b,...rest);};
 assert.deepEqual(nav.path(u,end),[]);await nav.workQueue;const route=nav.path(u,end);assert.ok(route.length);assert.deepEqual(route.at(-1),end);assert.deepEqual(u.job.point,end);
 assert.equal(nav.pathEnded({...u,x:100,z:80},end),false);nav.cancel(u);assert.equal(nav.tasks.has(u.id),false);nav.dispose();
});

check('分块中继继续求路，不把中继点误认成最终终点或不可达',async()=>{
 const map=streamingMap(),nav=new StreamingNavigator(map);await nav.ready;nav.groundBlocked=()=>false;nav.walkable=()=>false;
 nav.roughGoal=async()=>({point:point(180,0),edge:[0,1]});
 // 以平坦真实网格构建，只让最终直达判定走异步分支。
 const walkable=StreamingNavigator.prototype.walkable.bind(nav);nav.patchFor=async from=>{nav.walkable=walkable;const patch=await StreamingNavigator.prototype.patchFor.call(nav,from);nav.walkable=()=>false;return patch;};
 const u={id:'tank-1',x:0,z:0,y:0,revision:1},end=point(800,0);nav.path(u,end);await nav.workQueue;
 const task=nav.tasks.get(u.id);assert.equal(task.state,'ready');assert.equal(task.partial,true);assert.ok(task.path.length);assert.ok(Math.abs(task.path.at(-1).x-800)>100);
 Object.assign(u,task.path.at(-1));assert.equal(nav.pathEnded(u,end),false);assert.equal(nav.tasks.has(u.id),false);nav.dispose();
});

check('真实异步路线取消及替换隔离旧成功与失败回调',async()=>{
 const map=streamingMap(),nav=new StreamingNavigator(map);await nav.ready;nav.groundBlocked=()=>true;
 const gate=deferred();nav.roughGoal=async()=>{await gate.promise;return{point:point(100,0)};};const u={id:'tank-1',x:0,z:0,y:0,revision:1};
 nav.path(u,point(100,0));await flush();const old=nav.tasks.get(u.id);nav.cancel(u);u.revision++;
 nav.roughGoal=async(_,end)=>({point:end,unreachable:true});nav.path(u,point(200,0));const replacement=nav.tasks.get(u.id);gate.reject(new Error('旧请求失败'));await nav.workQueue;
 assert.notEqual(old,replacement);assert.equal(nav.tasks.get(u.id),replacement);assert.equal(replacement.state,'unreachable');assert.equal(nav.status(u).status,'rejected');nav.dispose();
});

check('工作器运行中故障显式失败，旧成功回调不覆盖替换后路线',async()=>{
 const map=streamingMap(),nav=new StreamingNavigator(map);await nav.ready;nav.groundBlocked=()=>true;
 const gate=deferred();nav.roughGoal=async(_,end)=>{await gate.promise;return{point:end};};const u={id:'tank-1',x:0,z:0,y:0,revision:1};
 nav.path(u,point(100,0));await flush();const old=nav.tasks.get(u.id);nav.cancel(u);u.revision++;nav.roughGoal=async(_,end)=>({point:end,unreachable:true});nav.path(u,point(200,0));const current=nav.tasks.get(u.id);gate.resolve();await nav.workQueue;
 assert.equal(nav.tasks.get(u.id),current);assert.notEqual(current,old);assert.equal(current.state,'unreachable');
 nav.worker.onerror({message:'工作器测试故障'});assert.equal(nav.status(u).status,'rejected');assert.match(nav.status(u).reason,/工作器测试故障/);nav.dispose();
});

check('命令区域真失败转为拒绝，旧命令失败不得删除新租约',async()=>{
 const map=streamingMap();await attachStreamingSimulation(map);const u={id:'tank-1',kind:'tank',x:0,z:85,y:0,revision:1,job:{type:'move',point:point(300,0)}};
 const old=deferred();map.gates.set('command:'+u.id,old);map.syncSimulation([u],[]);assert.equal(map.commandStatus(u).status,'pending');
 map.releaseCommandRegion(u.id);u.revision++;u.job={type:'move',point:point(700,0)};map.syncSimulation([u],[]);assert.equal(map.commandStatus(u).status,'ready');
 old.reject(new Error('旧下载失败'));await flush();assert.equal(map.commandStatus(u).status,'ready');assert.ok(map.data.owners.has('command:'+u.id));
 map.releaseCommandRegion(u.id);u.revision++;u.job={type:'move',point:point(1000,0)};const failure=deferred();map.gates.set('command:'+u.id,failure);map.syncSimulation([u],[]);failure.reject(new Error('摘要验证失败'));await flush();
 const result=map.commandStatus(u);assert.equal(result.status,'rejected');assert.match(result.reason,/摘要验证失败/);map.dispose();
});

let failures=0;
for(const {name,run} of checks){try{await run();console.log('通过：'+name);}catch(error){failures++;console.error('失败：'+name);console.error(error.stack);}}
console.log(`移动规则检查：${checks.length-failures}/${checks.length}通过`);if(failures)process.exitCode=1;
