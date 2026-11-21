/** 地图淡变状态、真实适配提交路径及材质拼接验证；不启动服务或浏览器。 */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {MapLevelTransition,createMapCoverageBinding,COVERAGE_FRAGMENT} from '../../src/战术演训/景别过渡.mjs';
const checks=[],check=(name,run)=>checks.push({name,run});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
function clock(){let time=0,serial=0;const frames=new Map();return{now:()=>time,requestFrame:fn=>{frames.set(++serial,fn);return serial;},cancelFrame:id=>frames.delete(id),advance(ms){time+=ms;const current=[...frames.values()];frames.clear();for(const fn of current)fn(time);},get queued(){return frames.size;}};}

check('绝对时间推进120ms淡出与淡入，下载等待单列且地图覆盖不断',async()=>{
 const c=clock(),transition=new MapLevelTransition(c);transition.request(1,'near','detail');await transition.committed(1,'near','detail',undefined,{instant:true});
 transition.request(2,'middle','detail');const out=transition.fadeOut(2),samples=[];for(let i=0;i<8;i++){c.advance(16);samples.push(transition.uniform.value);assert.equal(transition.snapshot({baseReady:true,baseVisible:true,detailVisible:true}).coverage,1);}assert.equal(await out,true);assert.ok(samples.every((n,i)=>i===0||n<=samples[i-1]));transition.retired(2);
 c.advance(640);const pending=transition.snapshot({baseReady:true,baseVisible:true,detailVisible:false});assert.equal(pending.pending,true);assert.equal(pending.phase,'waiting');assert.equal(pending.timings.waitMs,640);assert.equal(pending.coverage,1);
 const incoming=transition.committed(2,'middle','detail');const inSamples=[];for(let i=0;i<8;i++){c.advance(16);inSamples.push(transition.uniform.value);}assert.equal(await incoming,true);assert.ok(inSamples.every((n,i)=>i===0||n>=inSamples[i-1]));const state=transition.snapshot({baseReady:true,baseVisible:false,detailVisible:true});assert.equal(state.active,false);assert.equal(state.detailCoverage,1);assert.equal(state.timings.waitMs,640);assert.equal(state.timings.outMs,128);assert.equal(state.timings.inMs,128);assert.equal(state.timings.totalMs,896);
});

check('反向从当前覆盖续接，旧代提交与取消回调不能回闪',async()=>{
 const c=clock(),t=new MapLevelTransition(c);t.request(1,'near','detail');await t.committed(1,'near','detail',undefined,{instant:true});
 const first=new AbortController();t.request(2,'far','overview');const out=t.fadeOut(2,first.signal);c.advance(45);const partial=t.uniform.value;assert.ok(partial>0&&partial<1);first.abort();t.request(3,'near','detail');assert.equal(await out,false);assert.equal(t.uniform.value,partial);
 const incoming=t.committed(3,'near','detail');c.advance(200);assert.equal(await incoming,true);assert.equal(await t.committed(2,'far','overview'),false);assert.equal(t.displayedGeneration,3);assert.equal(t.displayedLevel,'near');assert.equal(t.uniform.value,1);assert.ok(t.history.some(row=>row.event==='cancel-out'&&row.generation===2));assert.equal(c.queued,0);
});

check('释放、重开、资源峰值及历史长度有界，不留下动画监听',async()=>{
 const c=clock(),t=new MapLevelTransition(c),controller=new AbortController();t.request(1,'far','overview');const inFlight=t.committed(1,'far','overview',controller.signal);c.advance(40);t.dispose();assert.equal(await inFlight,false);c.advance(1000);assert.equal(t.snapshot({baseReady:true,baseVisible:true,detailVisible:true}).coverage,0);assert.equal(t.phase,'disposed');assert.equal(c.queued,0);
 const next=new MapLevelTransition(c);for(let i=1;i<=90;i++){next.request(i,'near','detail');next.resources({chunks:i%50,geometry:i*10});}assert.equal(next.history.length,64);assert.equal(next.peaks.chunks,49);assert.equal(next.peaks.geometry,900);next.baseOnly(90,'准备失败');assert.equal(next.reason,'准备失败');assert.equal(next.snapshot({baseReady:true,baseVisible:true}).coverage,1);
});

check('减少动态效果偏好保持真实覆盖与提交语义，不遗留计时器',async()=>{
 const c=clock(),t=new MapLevelTransition({...c,reducedMotion:true});t.request(1,'near','detail');await t.committed(1,'near','detail');t.request(2,'middle','detail');assert.equal(await t.fadeOut(2),true);t.retired(2);assert.equal(t.snapshot({baseReady:true,baseVisible:true}).coverage,1);await t.committed(2,'middle','detail');assert.equal(t.uniform.value,1);assert.equal(c.queued,0);assert.equal(t.timings.outMs,0);assert.equal(t.timings.inMs,0);
});

check('固定8×8阈值无时间噪声，表面图集和阴影shader链保持共用实际uniform',()=>{
 const listeners=new Map(),uniform={value:.5},binding=createMapCoverageBinding(uniform),material={onBeforeCompile(shader){shader.fragmentShader='//原图集采样\n'+shader.fragmentShader;shader.vertexShader='//原实例顶点\n'+shader.vertexShader;},customProgramCacheKey(){return'原图集-6';},addEventListener:(event,fn)=>listeners.set(event,fn),removeEventListener:event=>listeners.delete(event)},depth={onBeforeCompile(){},customProgramCacheKey:()=> '源深度'};
 const root={traverse(fn){fn({isMesh:true,material});}};binding.bind(root);binding.bind(root);binding.bindMaterial(depth);assert.equal(binding.materialCount,2);
 for(const m of [material,depth]){const shader={uniforms:{},vertexShader:'void main(){}',fragmentShader:'void main() { gl_FragColor=vec4(1.0); }'};m.onBeforeCompile(shader);assert.equal(shader.uniforms.g01MapCoverage,uniform);assert.match(shader.fragmentShader,/g01Threshold\(gl_FragCoord.xy\)/);assert.equal((shader.fragmentShader.match(/uniform float g01MapCoverage/g)??[]).length,1);}
 const compiled={uniforms:{},vertexShader:'void main(){}',fragmentShader:'void main(){}'};material.onBeforeCompile(compiled);assert.match(compiled.fragmentShader,/原图集采样/);assert.match(compiled.vertexShader,/原实例顶点/);assert.equal(material.customProgramCacheKey(),'原图集-6|g01-map-coverage-v1');assert.doesNotMatch(COVERAGE_FRAGMENT,/time|random|sin\(/i);
 const bayer2=(x,y)=>2*x+3*y-4*x*y,threshold=(x,y)=>(16*bayer2(x%2,y%2)+4*bayer2(Math.floor(x/2)%2,Math.floor(y/2)%2)+bayer2(Math.floor(x/4),Math.floor(y/4))+.5)/64;
 const values=[];for(let y=0;y<8;y++)for(let x=0;x<8;x++)values.push(threshold(x,y));assert.equal(new Set(values).size,64);assert.equal(values.filter(n=>n<0).length,0);assert.equal(values.filter(n=>n<1).length,64);assert.equal(values.filter(n=>n<.5).length,32);listeners.get('dispose')();assert.equal(binding.materialCount,1);
});

// 用轻量资源替身驱动真实全图适配源码的prepare/commit/finally分支；不替换其控制逻辑。
const fakeThree=`
export class Group{constructor(){this.children=[];this.visible=true;this.parent=null;this.renderOrder=0;}add(...items){for(const item of items){item.removeFromParent?.();item.parent=this;this.children.push(item);}return this;}removeFromParent(){if(this.parent)this.parent.children=this.parent.children.filter(o=>o!==this);this.parent=null;}traverse(fn){fn(this);for(const child of this.children)child.traverse?child.traverse(fn):fn(child);}clear(){for(const o of this.children)o.parent=null;this.children=[];}updateMatrixWorld(){}}
class Material{constructor(options={}){Object.assign(this,options);this.depthWrite=true;this.userData={};this.uuid='材质';this.events=new Map();}onBeforeCompile(){}customProgramCacheKey(){return'源材质';}addEventListener(e,f){this.events.set(e,f);}removeEventListener(e){this.events.delete(e);}dispose(){this.events.get('dispose')?.();}clone(){return new this.constructor();}}
export class MeshStandardMaterial extends Material{}export class MeshDepthMaterial extends Material{}
export class Mesh extends Group{constructor(geometry,material){super();this.isMesh=true;this.geometry=geometry;this.material=material;this.userData={};}dispose(){}}
class Geometry{constructor(){this.attributes={position:{count:3}};this.index=null;}clone(){return new Geometry();}dispose(){}applyMatrix4(){return this;}computeBoundingBox(){}}
export class OctahedronGeometry extends Geometry{}export class CylinderGeometry extends Geometry{}export class BoxGeometry extends Geometry{}
export class Raycaster{constructor(){this.ray={origin:{y:100},direction:{y:-1},at:(t,p)=>{p.x=0;p.y=0;p.z=0;}};}setFromCamera(){}set(){}intersectObjects(){return[{point:{y:0}}];}}
export class Vector3{constructor(x=0,y=0,z=0){Object.assign(this,{x,y,z});}clone(){return new Vector3(this.x,this.y,this.z);}normalize(){return this;}negate(){return this;}angleTo(){return 0;}}
export class Vector2{constructor(x,y){this.x=x;this.y=y;}}
export class Matrix4{}export class Color{}
`;
const dataURL=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const threeURL=dataURL(fakeThree),fixtureKey='__g01TransitionFixture';
const mediaURL=dataURL(`import {Group,Mesh,BoxGeometry,MeshStandardMaterial} from '${threeURL}';export class RegionMedia{constructor(){this.roots=new Set();globalThis.${fixtureKey}.media=this;}async initialize(){}async prefetch(){}async load(name,signal){const f=globalThis.${fixtureKey};f.loads.push(name);const gate=f.gates.get(name);if(gate){f.gates.delete(name);await gate.promise;}if(signal?.aborted)throw new DOMException('已取消','AbortError');const root=new Group();root.name=name;root.add(new Mesh(new BoxGeometry(),new MeshStandardMaterial()));this.register(root);return root;}register(root){this.roots.add(root);}release(root){if(!root)return;root.removeFromParent();if(this.roots.delete(root))root.traverse(o=>o.material?.dispose?.());}stats(){return{geometryBytes:this.roots.size*100,geometryCpuBytes:this.roots.size*100,textureBytes:0,transcodeExtraBytes:0,stagingBytes:0,decoding:0,maxDecoding:1};}dispose(){}}`);
const dataModule=dataURL(`export class RequestPool{constructor(){this.active=this.maximum=0;this.queue=[];}}export class DownloadStore{constructor({pool}){this.pool=pool;this.bytes=this.received=0;}async get(){return new ArrayBuffer(16);}clear(){}}export class RegionData{constructor(){this.entries=new Map();}async prepare(){}release(){}stats(){return{bytes:0,reserved:0,regions:0,pending:0,owners:0};}query(){return{state:'ready',height:0};}collisions(){return{state:'ready',shapes:[]};}segment(){return{state:'ready',hit:null};}trimUnreferenced(){}dispose(){}}`);
const dummyModule=dataURL(`export function decodeInstancePage(){return{records:[],count:0,bytes:0};}export class TriangleSupportTile{}export function decodeCollisionPage(){}export class CoarseHeight{}export function decodeSimulationPackage(){}export function impostorFrame(){}export function impostorMatrix(){}export function prepareImpostorMaterial(){}`);
const transitionURL=new URL('../../src/战术演训/景别过渡.mjs',import.meta.url).href,source=await readFile(new URL('../../src/战术演训/地图适配/全图显示.mjs',import.meta.url),'utf8');
const rewritten=source.replace(/from '([^']+)'/g,(all,name)=>`from '${name==='three'?threeURL:name==='/景别过渡.mjs'?transitionURL:name==='./区域媒体.mjs'?mediaURL:name==='./区域数据.mjs'?dataModule:dummyModule}'`);
const {loadFullScene}=await import(dataURL(rewritten));
function manifest(){return{schema:'airbase-streaming-scene-v1',package_base:'.',bounds:{minX:-1000,maxX:1000,minZ:-1000,maxZ:1000},center:{x:0,z:0},defaultSpan:70,budgets:{download:10000,simulation:10000,geometry:10000,textures:10000,cpu:100000,visible_chunks:2},vegetation:{source_count:0},pages:[],prototypes:[],group_count:0,chunks:[-250,-125,0,125,250,375].map((x,i)=>({id:String(i),file:'chunk-'+i,bounds:[x-60,x+60,-60,60],instance_pages:[]})),overview:'overview',background:'background',far:{surface:'far-surface',instances:'far-records'},files:{'far-records':{}},camera:{middle_height_m:100,far_height_m:800,middle_min_span_m:180,far_min_span_m:1290,lod_hysteresis_m:15}};}
async function withMap(run){
 const c=clock(),saved={performance:globalThis.performance,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame,location:globalThis.location};Object.assign(globalThis,{performance:{now:c.now},requestAnimationFrame:c.requestFrame,cancelAnimationFrame:c.cancelFrame,location:{href:'https://local.invalid/'}});globalThis[fixtureKey]={gates:new Map(),loads:[]};
 const frames=[];let map;
 const tick=async(ms=16)=>{c.advance(ms);await flush();if(map)frames.push(map.getTransitionState());};
 const settle=async promise=>{let done=false,result,error;Promise.resolve(promise).then(value=>{done=true;result=value;},e=>{done=true;error=e;});for(let i=0;i<500&&!done;i++)await tick();assert.equal(done,true,'地图作业必须在有限虚拟帧内结束');if(error)throw error;return result;};
 try{map=await settle(loadFullScene('https://local.invalid/地图.json',{domElement:{width:800,height:600}},()=>{},manifest()));await run({map,c,tick,settle,frames,fixture:globalThis[fixtureKey]});}finally{map?.dispose();Object.assign(globalThis,saved);delete globalThis[fixtureKey];}
}
const view=(span,height,x=0)=>({center:{x,z:0,y:0},span,height});

check('真实适配近中远准备和提交有淡变，所有采样连续覆盖且未双驻完整分块',async()=>withMap(async({map,settle,frames})=>{
 assert.equal(map.getTransitionState().displayedLevel,'near');map.update(view(220,180));await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'middle');map.update(view(1600,1000));await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'far');map.update(view(70,300));await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'near');
 assert.ok(frames.some(s=>s.phase==='out'&&s.detailCoverage>0&&s.detailCoverage<1));assert.ok(frames.some(s=>s.phase==='in'&&s.detailCoverage>0&&s.detailCoverage<1));assert.ok(frames.every(s=>s.coverage===1));assert.ok(map.getTransitionState().peaks.chunks<=2);assert.ok(map.getTransitionState().peaks.geometry<=map.budgets.geometry);assert.ok(map.stats.displayCommits>=4);assert.doesNotThrow(()=>JSON.stringify(map.getTransitionState()));
}));

check('真实适配连续反向缩放和过期远景请求只提交最新代，等待仍保留总览',async()=>withMap(async({map,tick,settle,frames,fixture})=>{
 map.update(view(1600,1000));await tick();await tick(40);const partial=map.getTransitionState();assert.equal(partial.phase,'out');assert.ok(partial.detailCoverage>0&&partial.detailCoverage<1);const superseded=partial.generation;
 map.update(view(70,300));await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'near');assert.equal(map.transitionHistory().some(e=>e.event==='commit'&&e.generation===superseded),false);
 const gate=deferred();fixture.gates.set('far-surface',gate);map.update(view(1600,1000));for(let i=0;i<15;i++)await tick();assert.equal(map.getTransitionState().phase,'waiting');assert.equal(map.getTransitionState().coverage,1);const old=map.getTransitionState().generation;
 map.update(view(70,300));gate.resolve();await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'near');assert.equal(map.transitionHistory().some(e=>e.event==='commit'&&e.generation===old),false);assert.ok(frames.every(s=>s.coverage===1));assert.ok(map.getTransitionState().peaks.chunks<=2);
}));

check('真实适配同层平移限制prepared与resident总块数，dispose和重开不回闪',async()=>withMap(async({map,tick,settle,frames})=>{
 for(const x of [125,250,375,-125]){map.update(view(70,300,x));await settle(map.waitForView());assert.ok(map.stats.residentChunks<=2);}assert.ok(map.getTransitionState().peaks.chunks<=2);
 map.retry();map.update(view(1600,1000));await tick(40);const pending=map.waitForView();map.dispose();await settle(pending);for(let i=0;i<6;i++)await tick();assert.equal(map.getTransitionState().phase,'disposed');assert.equal(map.getTransitionState().coverage,0);assert.ok(frames.filter(s=>s.phase!=='disposed').every(s=>s.coverage===1));
}));

check('真实适配下载失败保留总览，显式重试可恢复且旧准备件不泄漏',async()=>withMap(async({map,tick,settle,frames,fixture})=>{
 const failed=deferred();fixture.gates.set('far-surface',failed);map.update(view(1600,1000));for(let i=0;i<15;i++)await tick();failed.reject(new Error('测试地图准备失败'));const original=console.error;console.error=()=>{};try{await settle(map.waitForView());}finally{console.error=original;}
 const state=map.getTransitionState();assert.equal(state.coverage,1);assert.equal(state.displayedLevel,'overview');assert.match(state.reason,/准备失败/);assert.equal(state.pending,false);
 map.retry();map.update(view(70,300));await settle(map.waitForView());assert.equal(map.getTransitionState().displayedLevel,'near');assert.equal(map.getTransitionState().reason,'');assert.ok(frames.every(s=>s.coverage===1));assert.ok(fixture.media.roots.size<=5,'总览、代理与最多两块地表之外不残留失败载荷');
}));

let failed=0;for(const {name,run}of checks){try{await run();console.log('通过：'+name);}catch(error){failed++;console.error('失败：'+name);console.error(error.stack);}}console.log(`景别过渡规则检查：${checks.length-failed}/${checks.length}通过`);if(failed)process.exitCode=1;
