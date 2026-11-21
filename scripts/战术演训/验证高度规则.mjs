/** G01高度纯规则专项；不启动服务，不读取或修改游戏载荷。 */
import assert from 'node:assert/strict';
import {Simulation} from '../../src/战术演训/模拟.mjs';
import {HELICOPTER_HEIGHT as H,HelicopterAltitudePlanner} from '../../src/战术演训/直升机高度.mjs';
import {decodeInstancePage} from '../../src/地图验证/实例页.mjs';
const checks=[],check=(name,run)=>checks.push({name,run});
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const box=(x,z,w,d,top)=>({id:'障碍',bounds:[x-w/2,0,z-d/2,x+w/2,top,z+d/2],movement:true});
const mapFor=(height=()=>0,shapes=[])=>({bounds:{minX:-500,maxX:1500,minZ:-500,maxZ:1500},cell:4,center:{x:0,z:0},targets:[],obstacles:[],heightAt:height,querySupport:(x,z)=>({state:'ready',height:height(x,z)}),collisionsIn:()=>({state:'ready',shapes}),safeAltitude:233.606842041,helicopterAltitude:181.721786499});
const unit=(x=0,z=0)=>({id:'heli-1',kind:'heli',x,z,y:0,yaw:-Math.PI/2,revision:1});
function trajectory(planner,u,target,{dt=.05,steps=2400}={}){const frames=[];for(let i=0;i<steps;i++){const before={x:u.x,y:u.y,z:u.z},result=planner.step(u,target,dt);frames.push({before,x:u.x,y:u.y,z:u.z,state:{...u.altitudeState},result});if(result.arrived)break;}return frames;}
function rates(frames,dt){for(const frame of frames){const dy=frame.y-frame.before.y;assert.ok(dy<=H.climbRate*dt+1e-7,'上升不得越过速率上限');assert.ok(dy>=-H.descentRate*dt-1e-7,'下降不得越过速率上限');}}

check('局部出生巡航30米，不再使用全图绝对高度或8米错层',()=>{
 const map=mapFor(()=>63),sim=new Simulation({map,composition:{tank:1,heli:3,plane:1},total:5});
 for(const u of sim.units.filter(u=>u.kind==='heli')){assert.equal(u.y,93);assert.equal(u.altitudeState.baseGround,63);assert.equal(u.y-u.altitudeState.baseGround,30);}
 assert.equal(sim.units.find(u=>u.kind==='tank').y,63);assert.equal(sim.units.find(u=>u.kind==='plane').y,map.safeAltitude);
});

check('默认五架局部二维分行且间距24米，安全点投射后仍去重并保持当地AGL30',()=>{
 for(const obstructed of [false,true]){
  const map=mapFor((x,z)=>.001*x+.002*z);if(obstructed)map.obstacles=[{x:0,z:170,w:48,d:36,h:20}];
  const sim=new Simulation({map,composition:{heli:5},total:5}),helicopters=sim.units.filter(u=>u.kind==='heli');
  assert.equal(helicopters.length,5);assert.equal(new Set(helicopters.map(u=>u.x+':'+u.z)).size,5);assert.ok(new Set(helicopters.map(u=>u.z)).size>=2);
  for(let i=0;i<helicopters.length;i++){const u=helicopters[i];assert.ok(Math.abs(u.y-map.heightAt(u.x,u.z)-30)<1e-8);for(let j=0;j<i;j++)assert.ok(Math.hypot(u.x-helicopters[j].x,u.z-helicopters[j].z)>=24-.001);}
 }
});

check('平地连续移动保持运行基准离地30米并准确到达原终点',()=>{
 const planner=new HelicopterAltitudePlanner(mapFor(()=>17)),u=unit();planner.initialize(u);const end={x:120,z:0},frames=trajectory(planner,u,end);
 assert.ok(frames.at(-1).result.arrived);for(const f of frames){assert.equal(f.y,47);assert.equal(f.state.baseGround,17);assert.equal(f.state.targetAltitude,47);}assert.deepEqual(end,{x:120,z:0});assert.ok(u.x>118.9&&u.x<=120);assert.doesNotThrow(()=>JSON.stringify(u.altitudeState));
});

check('直升机到达朝向与队列保持，命令区准备时高度状态和悬停一致',()=>{
 const map=mapFor(),sim=new Simulation({map,composition:{heli:1},total:1}),u=sim.units[0];u.x=0;u.z=0;u.yaw=-Math.PI/2;
 sim.command([u.id],{type:'move',point:{x:20,z:0},facing:Math.PI/2});const first=u.job;sim.command([u.id],{type:'move',point:{x:20,z:20},facing:Math.PI},true);const second=u.queue[0];
 let ready=false;map.commandStatus=()=>({status:ready?'ready':'pending',reason:'准备目标区域'});sim.step(.1);assert.equal(u.altitudeState.status,'pending');assert.equal(u.moving,false);assert.equal(u.travelSpeed,0);assert.equal(u.job,first);
 ready=true;for(let i=0;i<3000&&u.job;i++)sim.step(.025);assert.equal(u.job,null);assert.equal(first.status,'completed');assert.equal(second.status,'completed');assert.ok(Math.abs(Math.atan2(Math.sin(u.yaw-Math.PI),Math.cos(u.yaw-Math.PI)))<.026);assert.ok(Math.abs(u.y-30)<.25);
});

check('正常到达必须完成平滑尾段，巡航稳定且不遗留下降；障碍上方可在安全高度完成',()=>{
 for(const obstacle of [null,box(45,0,15,15,62)]){
  const map=mapFor(()=>.00999,obstacle?[obstacle]:[]),sim=new Simulation({map,composition:{heli:1},total:1}),u=sim.units[0];u.x=0;u.z=0;u.y=90;u.yaw=-Math.PI/2;
  sim.command([u.id],{type:'move',point:{x:obstacle?45:100,z:0}});const goal=obstacle?62+H.clearance:30.00999;let previous=u.y,completed=false,final;
  for(let i=0;i<2200;i++){sim.step(.025);const dy=u.y-previous;assert.ok(dy>=-H.descentRate*.025-1e-7&&dy<=H.climbRate*.025+1e-7,'最后一帧也不得跳高度');previous=u.y;if(!u.job){completed=true;final=u.y;break;}}
  assert.equal(completed,true);assert.ok(Math.abs(final-goal)<=1e-6);assert.equal(u.altitudeState.status,'cruise');assert.ok(Math.abs(u.altitudeState.targetAltitude-u.y)<=1e-6);
  for(let i=0;i<80;i++)sim.step(.025);assert.equal(u.y,final);assert.equal(u.altitudeState.status,'cruise');assert.equal(u.job,null);
 }
});

check('地形起伏提前升降、越过后恢复当地30米，升降速率有界',()=>{
 const height=x=>x<40||x>120?0:x<80?(x-40)*.6:(120-x)*.6,planner=new HelicopterAltitudePlanner(mapFor(height)),u=unit();planner.initialize(u);const frames=trajectory(planner,u,{x:220,z:0});
 assert.ok(frames.at(-1).result.arrived);assert.ok(frames.some(f=>f.x<40&&f.y>35),'到坡前应提前抬升');for(const f of frames)assert.ok(f.y>=height(f.x)+H.clearance-1e-6);assert.ok(Math.abs(u.y-30)<.25);rates(frames,.05);
});

check('高障碍前制动悬停爬升，不穿包络，越过后沿原航路下降',()=>{
 const wall=box(65,0,18,30,90),planner=new HelicopterAltitudePlanner(mapFor(()=>0,[wall])),u=unit();planner.initialize(u);const target={x:220,z:0},frames=trajectory(planner,u,target);
 assert.ok(frames.at(-1).result.arrived);assert.ok(frames.some(f=>f.x<wall.bounds[0]-H.radius&&f.y>40));
 for(const f of frames)if(f.x>=wall.bounds[0]-H.radius&&f.x<=wall.bounds[3]+H.radius)assert.ok(f.y>=wall.bounds[4]+H.clearance-.151,'进入障碍水平安全包络前高度必须足够');
 assert.ok(frames.some(f=>f.state.status==='climbing'&&!f.result.arrived&&Math.abs(f.x-f.before.x)<1e-6));assert.ok(frames.every(f=>Math.abs(f.z)<1e-9));assert.ok(Math.abs(u.y-30)<.25);rates(frames,.05);assert.deepEqual(target,{x:220,z:0});
});

check('实模13.933米角半径被14.5米覆盖，斜向和转向均核对侧缘障碍',()=>{
 for(let yaw=0;yaw<Math.PI*2;yaw+=.1)for(const x of [-9.5441,9.516])for(const z of [-9.4723,10.1508]){const xx=x*Math.cos(yaw)+z*Math.sin(yaw),zz=z*Math.cos(yaw)-x*Math.sin(yaw);assert.ok(Math.hypot(xx,zz)<H.radius);}
 const obstacle=box(40,60,1,1,60),planner=new HelicopterAltitudePlanner(mapFor(()=>0,[obstacle])),u=unit();planner.initialize(u);
 const frames=trajectory(planner,u,{x:180,z:180});assert.ok(frames.at(-1).result.arrived);assert.ok(frames.some(f=>f.y>67),'斜向路线14米侧缘须纳入飞行安全包络');assert.ok(frames.every(f=>Math.abs(f.x-f.z)<1e-7));rates(frames,.05);
 const turning=new HelicopterAltitudePlanner(mapFor(()=>0,[box(30,30,1,1,60)])),v=unit();turning.initialize(v);turning.step(v,{x:180,z:0},.2);assert.equal(v.y,30);
 const afterTurn=trajectory(turning,v,{x:180,z:180});assert.ok(afterTurn.some(f=>f.y>67),'转向后须重新建立包络，不能沿用旧前瞻方向');assert.ok(afterTurn.at(-1).result.arrived);
});

check('未知前方立即悬停，恢复后继续原命令，准备请求不逐帧重复',async()=>{
 const map=mapFor(),gate=deferred();let pending=true,calls=0,released=0;map.querySupport=(x,z)=>pending&&x>15?{state:'pending'}:{state:'ready',height:0};map.prepareRegion=()=>{calls++;return gate.promise;};map.releaseRegion=()=>released++;
 const planner=new HelicopterAltitudePlanner(map),u=unit();planner.initialize(u);const target={x:100,z:0};
 for(let i=0;i<40;i++){const result=planner.step(u,target,.05);assert.equal(result.status,'pending');assert.equal(u.x,0);assert.equal(u.y,30);}assert.equal(calls,1);
 pending=false;gate.resolve();await flush();const frames=trajectory(planner,u,target);assert.ok(frames.at(-1).result.arrived);assert.deepEqual(target,{x:100,z:0});planner.cancel(u);assert.ok(released>=1);assert.equal(planner.records.size,0);
});

check('前方真正准备失败保持位置与明确错误，取消后旧异步失败不污染新命令',async()=>{
 const map=mapFor(),gates=[deferred(),deferred()];let calls=0;map.prepareRegion=()=>gates[calls++].promise;map.releaseRegion=()=>{};
 const planner=new HelicopterAltitudePlanner(map),u=unit();planner.initialize(u);planner.step(u,{x:90,z:0},.05);planner.cancel(u);u.revision++;
 planner.step(u,{x:0,z:90},.05);gates[0].reject(new Error('旧路线摘要失败'));await flush();assert.notEqual(u.altitudeState.status,'error');
 gates[1].reject(new Error('新路线下载失败'));await flush();const before={x:u.x,y:u.y,z:u.z},result=planner.step(u,{x:0,z:90},.2);assert.equal(result.status,'error');assert.match(u.altitudeState.reason,/新路线下载失败/);assert.deepEqual({x:u.x,y:u.y,z:u.z},before);
});

check('停止、新命令和reset释放自己的旧租约，悬停不改变高度',async()=>{
 const map=mapFor(),owners=new Set(),gates=[];map.prepareRegion=owner=>{owners.add(owner);const gate=deferred();gates.push(gate);return gate.promise;};map.releaseRegion=owner=>owners.delete(owner);
 const sim=new Simulation({map,composition:{heli:1},total:1}),u=sim.units[0];u.x=0;u.z=0;u.y=60;
 sim.command([u.id],{type:'move',point:{x:120,z:0}});sim.step(.1);const firstOwner=[...owners][0];assert.ok(firstOwner);
 sim.command([u.id],{type:'move',point:{x:0,z:120}});assert.equal(owners.has(firstOwner),false);sim.step(.1);assert.equal(sim.helicopterHeight.records.size,1);
 sim.cancel([u.id]);const hover=u.y;assert.equal(owners.size,0);for(let i=0;i<20;i++)sim.step(.1);assert.equal(u.y,hover);assert.equal(u.altitudeState.status,'holding');assert.equal(u.job,null);
 sim.command([u.id],{type:'move',point:{x:150,z:0}});sim.step(.1);const previous=sim.helicopterHeight;sim.reset();assert.equal(previous.records.size,0);assert.equal(owners.size,0);
 for(const gate of gates)gate.reject(new Error('停止后的旧失败'));await flush();assert.ok(sim.units.every(v=>v.altitudeState.status!=='error'));
});

function envelopeBuffer(bounds=[40,0,-10,60,65,10]){const bytes=new ArrayBuffer(120),v=new DataView(bytes);for(const [at,n] of [[0,0x50494142],[4,1],[8,1],[12,104],[28,2]])v.setUint32(at,n,true);for(let i=0;i<16;i++)v.setFloat32(32+i*4,i%5===0?1:0,true);bounds.forEach((n,i)=>v.setFloat32(96+i*4,n,true));return bytes;}
function withEnvelopes({bounds=[40,0,-10,60,65,10],get,decode,count=1}={}){
 const map=mapFor(()=>0,[box(50,0,2,2,6)]);map.streaming=true;map.packageBase='https://local.invalid/';map.chunk_m=125;map.chunks=[];for(const x of [3,4])for(const z of [3,4])map.chunks.push({id:`0${x}-0${z}`,instance_pages:['tree']});map.pages=[{id:'tree',file:'tree.bin',count,identity:{bytes:120}}];map.prototypes=[{enabled:true,category:'tree'}];map.group_count=1;map.downloads={get:get??(async()=>envelopeBuffer(bounds))};map.prepareRegion=async()=>{};map.releaseRegion=()=>{};
 const decodeInstances=decode??decodeInstancePage;return{map,decodeInstances};
}

check('树干6米不替代完整树冠包络，实例页未知时等待、就绪后提前抬升',async()=>{
 const gate=deferred(),{map,decodeInstances}=withEnvelopes({get:()=>gate.promise}),planner=new HelicopterAltitudePlanner(map,{decodeInstances}),u=unit();planner.initialize(u);
 const target={x:100,z:0};assert.equal(planner.step(u,target,.05).status,'pending');assert.equal(u.x,0);gate.resolve(envelopeBuffer());await flush();const frames=trajectory(planner,u,target);
 assert.ok(frames.some(f=>f.y>70));for(const f of frames)if(f.x>=40-H.radius&&f.x<=60+H.radius)assert.ok(f.y>=65+H.clearance-.151);assert.ok(frames.at(-1).result.arrived);assert.ok(planner.stats().envelopeBytes<=H.envelopeBytes);
});

check('树冠数据缺失、校验失败和缓存预算不足均明确悬停，不盲穿',async()=>{
 const missing=mapFor();missing.streaming=true;const p=new HelicopterAltitudePlanner(missing),u=unit();p.initialize(u);assert.equal(p.step(u,{x:100,z:0},.05).status,'error');assert.equal(u.x,0);
 const {map,decodeInstances}=withEnvelopes({get:async()=>{throw new Error('实例页摘要失败');}}),planner=new HelicopterAltitudePlanner(map,{decodeInstances}),v=unit();planner.initialize(v);planner.step(v,{x:100,z:0},.05);await flush();assert.equal(planner.step(v,{x:100,z:0},.05).status,'error');assert.match(v.altitudeState.reason,/摘要失败/);assert.equal(v.x,0);
 const fixture=withEnvelopes(),small=new HelicopterAltitudePlanner(fixture.map,{decodeInstances:fixture.decodeInstances,envelopeBytes:100}),w=unit();small.initialize(w);assert.equal(small.step(w,{x:100,z:0},.05).status,'error');assert.equal(small.stats().envelopeBytes,0);
});

check('实例页失败同命令不无限重试，重新下令与reset可清除失败缓存恢复',async()=>{
 let reads=0;const {map,decodeInstances}=withEnvelopes({get:async()=>{if(++reads===1)throw new Error('一次摘要失败');return envelopeBuffer();}}),sim=new Simulation({map,composition:{heli:1},total:1,helicopterHeight:{decodeInstances}}),u=sim.units[0];u.x=0;u.z=0;u.yaw=-Math.PI/2;
 sim.command([u.id],{type:'move',point:{x:100,z:0}});sim.step(.05);await flush();sim.step(.05);assert.equal(u.job.status,'rejected');assert.equal(u.altitudeState.status,'error');assert.match(u.state,/摘要失败/);assert.deepEqual(u.job.point,{x:100,z:0});assert.equal(u.moving,false);
 for(let i=0;i<20;i++)sim.step(.05);assert.equal(reads,1);assert.equal(sim.helicopterHeight.stats().envelopePages,0,'最后一个失败消费者释放后不保留永久错误页');
 sim.command([u.id],{type:'move',point:{x:110,z:0}});sim.step(.05);await flush();sim.step(.05);assert.equal(reads,2);assert.equal(u.job.status,'executing');assert.notEqual(u.altitudeState.status,'error');assert.deepEqual(u.job.point,{x:110,z:0});
 const old=sim.helicopterHeight;sim.reset();assert.equal(old.stats().envelopePages,0);assert.equal(sim.helicopterHeight.stats().envelopePages,0);assert.ok(sim.units.every(v=>v.altitudeState.status!=='error'));
});

check('连续地形复核发现采样间窄坡，先悬停爬升再通过并恢复巡航',()=>{
 const map=mapFor(x=>x>=2-1e-7&&x<=2.1?50:0);map.supportSegment=(a,b)=>({state:'ready',hit:a.x<2&&b.x>=2&&Math.min(a.y,b.y)<50?(2-a.x)/(b.x-a.x):null});const planner=new HelicopterAltitudePlanner(map),u=unit();planner.initialize(u);
 const frames=trajectory(planner,u,{x:100,z:0});assert.ok(frames.at(-1).result.arrived);assert.ok(frames.some(f=>/连续地形/.test(f.state.reason)));for(const f of frames)if(f.before.x<2&&f.x>=2)assert.ok(Math.min(f.y,f.before.y)>=50+H.clearance);assert.ok(Math.abs(u.y-30)<.25);
});

check('跨格实例引用去重，未知格不静默略过，取消旧页请求不污染新读取',async()=>{
 const gates=[deferred(),deferred()];let reads=0;const {map,decodeInstances}=withEnvelopes({get:()=>gates[reads++].promise}),planner=new HelicopterAltitudePlanner(map,{decodeInstances}),u=unit();planner.initialize(u);
 assert.equal(planner.step(u,{x:100,z:0},.05).status,'pending');await flush();assert.equal(reads,1,'多个相交格引用同一页只下载一次');planner.cancel(u);u.revision++;
 assert.equal(planner.step(u,{x:100,z:0},.05).status,'pending');await flush();assert.equal(reads,2);gates[0].reject(new Error('已取消的旧实例页'));gates[1].resolve(envelopeBuffer());await flush();assert.equal(planner.step(u,{x:100,z:0},.05).status,'ready');
 planner.cancel(u);map.chunks=map.chunks.filter(row=>row.id!=='03-03');const broken=new HelicopterAltitudePlanner(map,{decodeInstances}),v=unit();broken.initialize(v);assert.equal(broken.step(v,{x:100,z:0},.05).status,'error');assert.match(v.altitudeState.reason,/缺少完整/);
});

check('完整BAIP回读、共享页引用与有界LRU不因单单位取消破坏其他消费者',async()=>{
 const gate=deferred();let signal,reads=0;const fixture=withEnvelopes({get:async(_,__,s)=>{reads++;signal=s;return gate.promise;}}),planner=new HelicopterAltitudePlanner(fixture.map,{decodeInstances:fixture.decodeInstances}),a=unit(),b={...unit(),id:'heli-2'};planner.initialize(a);planner.initialize(b);
 planner.step(a,{x:100,z:0},.05);planner.step(b,{x:100,z:0},.05);await flush();assert.equal(reads,1);planner.cancel(a);assert.equal(signal.aborted,false);gate.resolve(envelopeBuffer());await flush();assert.equal(planner.step(b,{x:100,z:0},.05).status,'ready');planner.cancel(b);
 const bounded=withEnvelopes();bounded.map.obstacles=[];bounded.map.collisionsIn=()=>({state:'ready',shapes:[]});bounded.map.chunks=[];bounded.map.pages=[];
 for(let x=4;x<=7;x++){const id='page-'+x;for(const z of [3,4])bounded.map.chunks.push({id:`${String(x).padStart(2,'0')}-0${z}`,instance_pages:[id]});bounded.map.pages.push({id,file:id,count:1,identity:{bytes:120}});}
 const lru=new HelicopterAltitudePlanner(bounded.map,{decodeInstances:bounded.decodeInstances,cachedEnvelopePages:2,envelopeBytes:700}),u=unit();
 for(let x=4;x<=7;x++){u.x=-500+x*125+62;u.z=0;u.revision++;lru.initialize(u);lru.step(u,{x:u.x+20,z:0},.05);await flush();assert.equal(lru.step(u,{x:u.x+20,z:0},.05).status,'ready');assert.ok(lru.stats().envelopePages<=2);assert.ok(lru.stats().envelopeBytes<=700);lru.cancel(u);}
 assert.equal(lru.stats().envelopePages,2);lru.reset();assert.equal(lru.stats().envelopeBytes,0);
});

check('帧步长分割一致、长帧位移有界、零或非法步长不改变位置',()=>{
 const map=mapFor(()=>0,[box(70,0,20,20,65)]),a=unit(),b=unit(),one=new HelicopterAltitudePlanner(map),many=new HelicopterAltitudePlanner(map);one.initialize(a);many.initialize(b);
 one.step(a,{x:150,z:0},.5);for(let i=0;i<10;i++)many.step(b,{x:150,z:0},.05);assert.ok(Math.abs(a.x-b.x)<1e-6);assert.ok(Math.abs(a.y-b.y)<1e-6);
 const before={x:a.x,y:a.y,z:a.z};for(const dt of [0,-2,NaN])one.step(a,{x:150,z:0},dt);assert.deepEqual({x:a.x,y:a.y,z:a.z},before);
 one.step(a,{x:150,z:0},20);assert.ok(a.x-before.x<=13*H.maxFrame+1e-6);assert.ok(a.y-before.y<=H.climbRate*H.maxFrame+1e-6);
});

let failed=0;for(const {name,run} of checks){try{await run();console.log('通过：'+name);}catch(error){failed++;console.error('失败：'+name);console.error(error.stack);}}
console.log(`高度规则检查：${checks.length-failed}/${checks.length}通过`);if(failed)process.exitCode=1;
