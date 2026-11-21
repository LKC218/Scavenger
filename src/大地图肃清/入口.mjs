/** 主对话唯一入口：真实时间调度规则、分发结果、集成交互与只读表现。 */
import * as THREE from 'three';
import {CONFIG} from './配置.mjs';
import {createWorld} from './样本场景.mjs';
import {Simulation} from './规则.mjs';
import {createPresentation} from './表现.mjs';
import {createInteraction} from './交互.mjs';

const canvas=document.querySelector('#viewport'),ui=document.querySelector('#ui'),status=document.querySelector('#status');
const errors=[];
window.addEventListener('error',e=>errors.push({time:performance.now(),message:e.message}));
window.addEventListener('unhandledrejection',e=>errors.push({time:performance.now(),message:String(e.reason?.stack??e.reason)}));
window.scavenger={ready:false,errors};

function makeGround(scene,world){
  const segments=150,step=600/segments,positions=[],colors=[],indices=[];
  const color=new THREE.Color();
  for(let iz=0;iz<=segments;iz++)for(let ix=0;ix<=segments;ix++){
    const x=-300+ix*step,z=-300+iz*step,s=world.querySupport(x,z);
    positions.push(x,s.height??-.4,z);
    color.setHex(s.status==='pending'?0x423d2a:s.status==='unsupported'?0x152124:s.height>0?0x52634d:((Math.floor(x/30)+Math.floor(z/30))%2?0x485a43:0x45563f));
    colors.push(color.r,color.g,color.b);
  }
  for(let iz=0;iz<segments;iz++)for(let ix=0;ix<segments;ix++){
    const x=-300+(ix+.5)*step,z=-300+(iz+.5)*step;if(world.querySupport(x,z).status==='unsupported')continue;
    const a=iz*(segments+1)+ix,b=a+1,c=a+segments+1,d=c+1;indices.push(a,c,b,b,c,d);
  }
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));geometry.setIndex(indices);geometry.computeVertexNormals();
  const ground=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({vertexColors:true,roughness:1}));ground.receiveShadow=true;scene.add(ground);
  const grid=new THREE.GridHelper(600,60,0x94a38a,0x66785d);grid.position.y=.06;grid.material.transparent=true;grid.material.opacity=.18;scene.add(grid);
  for(const o of world.obstacles){const mesh=new THREE.Mesh(new THREE.BoxGeometry(o.width,o.maxY-o.minY,o.depth),new THREE.MeshStandardMaterial({color:0x777e74,roughness:1}));mesh.position.set(o.x,(o.maxY+o.minY)/2,o.z);mesh.castShadow=mesh.receiveShadow=true;scene.add(mesh);}
  for(const zone of world.zones){const ring=new THREE.Mesh(new THREE.RingGeometry(zone.radius-.35,zone.radius,96),new THREE.MeshBasicMaterial({color:0xbbca79,transparent:true,opacity:.35,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.set(zone.center.x,.09,zone.center.z);scene.add(ring);}
}

function summarize(frames){
  const intervals=frames.map(f=>f[0]).sort((a,b)=>a-b),n=intervals.length;
  if(!n)return{frames:0};
  const average=intervals.reduce((a,b)=>a+b,0)/n,p95=intervals[Math.min(n-1,Math.floor(n*.95))],long33=intervals.filter(v=>v>33.3).length;
  const maxLag=Math.max(...frames.map(f=>f[5]));
  return{frames:n,averageFPS:1000/average,p95Ms:p95,maxMs:intervals[n-1],long33Ratio:long33/n,long100:intervals.filter(v=>v>100).length,maxSimulationLag:maxLag,
    minLiving:Math.min(...frames.map(f=>f[1])),maxLiving:Math.max(...frames.map(f=>f[1])),
    pass:p95<=16.7&&long33/n<.001&&intervals[n-1]<=100&&maxLag<CONFIG.step};
}

async function start(){
  const response=await fetch('/api/manifest');if(!response.ok)throw Error('候选清单无法读取');const manifest=await response.json();
  const renderer=new THREE.WebGLRenderer({canvas,antialias:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(CONFIG.render.pixelRatio);renderer.setSize(CONFIG.render.width,CONFIG.render.height,false);
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.setClearColor(0x1b2922);
  const scene=new THREE.Scene();scene.fog=new THREE.Fog(0x1b2922,850,1450);
  const camera=new THREE.OrthographicCamera(-180,180,101.25,-101.25,.5,2000);
  camera.position.set(0,400,280);camera.lookAt(0,0,0);
  scene.add(new THREE.HemisphereLight(0xe2ead6,0x364230,2.4));
  const sun=new THREE.DirectionalLight(0xfff1cf,2.1);sun.position.set(-90,180,90);sun.castShadow=true;sun.shadow.mapSize.set(1024,1024);sun.shadow.camera.left=-140;sun.shadow.camera.right=140;sun.shadow.camera.top=140;sun.shadow.camera.bottom=-140;sun.shadow.camera.far=500;sun.shadow.bias=-.0004;sun.shadow.normalBias=.035;scene.add(sun);scene.add(sun.target);
  const world=createWorld();makeGround(scene,world);
  const sim=new Simulation({config:CONFIG,world});
  const presentation=await createPresentation({renderer,scene,camera,manifest,world,config:CONFIG,onProgress:text=>{status.textContent=text;}});
  let commandSerial=0,interaction,scheduleTime=0,lastFrame=performance.now()/1000,lastReset=lastFrame,paused=false;
  // 本入口统一签发命令序号，避免界面与测试控制器各自计数而发生碰撞。
  const dispatch=value=>sim.dispatch({...value,runId:value.runId??sim.snapshot().runId,commandId:++commandSerial});
  interaction=createInteraction({canvas,camera,world,config:CONFIG,getSnapshot:()=>sim.snapshot(),dispatch,presentation,ui});
  let sample=null,latestSample=null,recentFrames=[],lastStatus=0;
  const gl=renderer.getContext(),gpuInfo={vendor:gl.getParameter(gl.VENDOR),renderer:gl.getParameter(gl.RENDERER)};
  const debug=gl.getExtension('WEBGL_debug_renderer_info');if(debug){gpuInfo.unmaskedVendor=gl.getParameter(debug.UNMASKED_VENDOR_WEBGL);gpuInfo.unmaskedRenderer=gl.getParameter(debug.UNMASKED_RENDERER_WEBGL);}
  const gpuExt=gl.getExtension('EXT_disjoint_timer_query_webgl2'),gpuPending=[],gpuTimes=[];let frameNumber=0;

  function reset(options={}){
    sim.reset({preset:options.preset??'interaction',seed:options.seed??CONFIG.seed,...options});
    commandSerial=0;scheduleTime=0;lastFrame=performance.now()/1000;lastReset=lastFrame;
    presentation.update(sim.snapshot(),0,interaction.getState());presentation.handleEvents(sim.drainEvents());
    if(sample)sample.resets.push({time:performance.now()/1000,runId:sim.snapshot().runId,preset:sim.snapshot().preset});
    return sim.snapshot().runId;
  }
  ui.addEventListener('scenario-request',e=>reset(e.detail));
  function startSample(options={}){
    if(sample)throw Error('已有采样正在进行');
    if(options.preset)reset({preset:options.preset,seed:options.seed??CONFIG.seed});
    const now=performance.now()/1000;
    sample={state:'warming',started:now,begin:now+(options.warmup??30),duration:options.duration??180,frames:[],visibility:[],resets:[],options:{...options},gpuStart:gpuTimes.length,
      meta:{revision:manifest.revision,sourceSha256:manifest.sourceSha256,assets:[...Object.values(manifest.models),...manifest.native.models].map(m=>({id:m.id,sha256:m.sha256})),three:manifest.three,profile:sim.snapshot().preset,resolution:renderer.getDrawingBufferSize(new THREE.Vector2()).toArray(),cssViewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},gpu:gpuInfo,userAgent:navigator.userAgent}};
    latestSample=null;return sampleStatus();
  }
  function sampleStatus(){if(!sample)return{state:latestSample?'done':'idle'};return{state:sample.state,remaining:Math.max(0,sample.begin+sample.duration-performance.now()/1000),frames:sample.frames.length};}
  document.addEventListener('visibilitychange',()=>{if(sample)sample.visibility.push({time:performance.now()/1000,state:document.visibilityState});});
  function snapshotStats(){return{...presentation.getStats(),counts:sim.snapshot().counts,diagnostics:sim.snapshot().diagnostics,drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,gpuMs:gpuTimes.at(-1)??null,gpu:gpuInfo,sample:sampleStatus(),recentFrameMs:recentFrames.at(-1)??0};}
  Object.assign(window.scavenger,{sim,world,config:CONFIG,camera,renderer,scene,presentation,interaction,manifest,reset,dispatch,snapshot:()=>sim.snapshot(),getStats:snapshotStats,startSample,sampleStatus,sampleResult:()=>latestSample,setPaused:value=>{paused=!!value;lastFrame=performance.now()/1000;},ready:true});
  reset({preset:new URLSearchParams(location.search).get('preset')||'interaction'});
  status.textContent='首批V001候选 · G01—G04等待人工验收';
  function frame(timestamp){
    requestAnimationFrame(frame);
    const now=timestamp/1000,dt=Math.max(0,now-lastFrame);lastFrame=now;frameNumber++;
    let steps=0;const cpuBegin=performance.now();
    if(!paused){scheduleTime+=dt;while(sim.snapshot().time+CONFIG.step<=scheduleTime+1e-8&&steps<30){sim.step(CONFIG.step);steps++;}}
    const snapshot=sim.snapshot();
    const alpha=THREE.MathUtils.clamp((scheduleTime-snapshot.time)/CONFIG.step,0,1);
    presentation.update(snapshot,Math.min(dt,.1),{...interaction.getState(),alpha});
    interaction.update(Math.min(dt,.1));
    const iState=interaction.getState();presentation.handleEvents(sim.drainEvents());
    if(iState.focus){sun.position.set(iState.focus.x-90,180,iState.focus.z+90);sun.target.position.set(iState.focus.x,0,iState.focus.z);sun.target.updateMatrixWorld();}
    let query=null;
    if(gpuExt&&gpuPending.length<8&&frameNumber%4===0){query=gl.createQuery();gl.beginQuery(gpuExt.TIME_ELAPSED_EXT,query);}
    renderer.render(scene,camera);
    if(query){gl.endQuery(gpuExt.TIME_ELAPSED_EXT);gpuPending.push(query);}
    if(gpuExt){for(let i=gpuPending.length-1;i>=0;i--){const q=gpuPending[i];if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)){if(!gl.getParameter(gpuExt.GPU_DISJOINT_EXT))gpuTimes.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q);gpuPending.splice(i,1);}}}
    const cpuMs=performance.now()-cpuBegin,stats=presentation.getStats(),lag=paused?0:Math.max(0,scheduleTime-snapshot.time);
    recentFrames.push(dt*1000);if(recentFrames.length>180)recentFrames.shift();
    if(sample){
      if(now>=sample.begin){sample.state='running';sample.frames.push([dt*1000,snapshot.counts.alive,stats.individuals??0,stats.proxyMembers??0,stats.offscreenAlive??0,lag,renderer.info.render.calls,cpuMs]);}
      if(now>=sample.begin+sample.duration){const summary=summarize(sample.frames);latestSample={...sample,state:'done',summary,gpuTimes:gpuTimes.slice(sample.gpuStart),invalid:sample.visibility.some(v=>v.state!=='visible')||document.visibilityState!=='visible'||(!sample.options.allowResets&&sample.resets.length>0)};latestSample.summary.pass=summary.pass&&!latestSample.invalid;sample=null;}
    }
    if(now-lastStatus>.5){
      const fps=recentFrames.length?1000/(recentFrames.reduce((a,b)=>a+b,0)/recentFrames.length):0;
      status.textContent=`V001候选 · ${fps.toFixed(0)}帧 · 个体${stats.individuals??0}／替身代表${stats.proxyMembers??0}／隐藏${stats.hidden??0} · 绘制${renderer.info.render.calls}${lag>CONFIG.step?' · 模拟欠时'+lag.toFixed(2)+'秒':''} · 等待人工验收`;
      lastStatus=now;
    }
  }
  requestAnimationFrame(frame);
  window.addEventListener('beforeunload',()=>{interaction.dispose();presentation.dispose();renderer.dispose();});
}

start().catch(error=>{errors.push({message:error.stack??String(error)});status.textContent='候选载入失败：'+error.message;console.error(error);});
