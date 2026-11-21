import * as THREE from 'three';
import {Crowd} from './群体.mjs';
import {Actors} from './角色.mjs';
import {View} from './场景.mjs';
import {Metrics,GpuTimer,summarize} from './统计.mjs';

const $=id=>document.getElementById(id),labels={static:'静态角色',animated:'原地骨骼动画',open:'开阔群体移动',channel:'障碍窄通道'};
let ready=false,crowd,actors,view,metrics,gpu,manifest,paused=false,last=0,accumulator=0,lastUi=0,currentRow=null,matrixQueue=[],matrixTotal=0,archiveState=null;
const step=1/30;
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3000);}
function config(){return {count:crowd.count,mode:crowd.mode,seed:crowd.seed,patrol:crowd.patrol,simulationHz:30,view:view.config(),actorSet:$('actor-set').value,models:actors.distribution,delivery:manifest.delivery,asset:manifest.asset,code_sha256:manifest.code_sha256,animation:'本项目自制待机与移动循环',bonesPerActor:actors.models.length===1?actors.models[0].bones:null};}
function environment(){
  const gl=view.renderer.getContext(),debug=gl.getExtension('WEBGL_debug_renderer_info');
  return {userAgent:navigator.userAgent,three:THREE.REVISION,webgl:gl.getParameter(gl.VERSION),gpu:debug?gl.getParameter(debug.UNMASKED_RENDERER_WEBGL):'浏览器未公开',
    viewport:[innerWidth,innerHeight],screen:[screen.width,screen.height],devicePixelRatio:devicePixelRatio,gpuTimer:!!gpu.ext,
    visible:!document.hidden,focused:document.hasFocus(),hardwareConcurrency:navigator.hardwareConcurrency,browserLabel:$('environment').value};
}
function reset(){
  crowd.reset(Number($('count').value),$('mode').value);actors.rebuild(crowd.agents,$('actor-set').value,crowd.seed);paused=false;accumulator=0;
  $('pause').textContent='暂停';$('pause-overlay').hidden=true;$('patrol').textContent='往返 已开';$('patrol').setAttribute('aria-pressed','true');
  $('active-count').textContent=crowd.count;$('mode-label').textContent=labels[crowd.mode];
}
function setBusy(busy){
  view.locked=busy;
  for(const id of ['mode','count','quality','actor-set','environment','matrix','reset','pause','patrol','focus','home','quick-sample','sample','long-sample'])$(id).disabled=busy;
  $('cancel').hidden=!busy;
}
function sample(duration){
  archiveState=null;
  reset();view.home(true);setBusy(true);metrics.start(config(),environment(),{warmup:10,duration});
  if(document.hidden)metrics.interrupt('开始时页面在后台');
  $('json').disabled=$('csv').disabled=true;
}
async function startMatrix(){
  const proof=await fetch('/api/foreground').then(r=>r.json());
  if(!proof[$('environment').value]){toast('当前测试浏览器未在Windows前台，请保持测试窗口可见后重试');return;}
  matrixQueue=[{count:500,mode:'open',actorSet:'single',seconds:60}];
  for(const count of [100,300,500])for(const mode of ['static','animated','open','channel'])matrixQueue.push({count,mode,actorSet:'mixed',seconds:60});
  for(const mode of ['open','channel'])matrixQueue.push({count:500,mode,actorSet:'mixed',seconds:180});
  matrixTotal=matrixQueue.length;nextMatrix();
}
function nextMatrix(){
  if(!matrixQueue.length){$('matrix-status').textContent='矩阵结束，原始样本已逐组归档';return;}
  const job=matrixQueue.shift();$('count').value=job.count;$('mode').value=job.mode;$('actor-set').value=job.actorSet;view.resize(640);$('quality').value='640';
  $('matrix-status').textContent=`矩阵 ${matrixTotal-matrixQueue.length} / ${matrixTotal} · ${job.actorSet==='single'?'单款':'十三款'} ${job.count}只 ${labels[job.mode]} ${job.seconds}秒`;
  sample(job.seconds);
}
function togglePause(){
  if(metrics.busy)return;paused=!paused;$('pause').textContent=paused?'继续':'暂停';$('pause-overlay').hidden=!paused;
}
function chart(rows){
  const canvas=$('chart'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#526640';ctx.setLineDash([6,6]);ctx.beginPath();ctx.moveTo(0,h-16.667/50*h);ctx.lineTo(w,h-16.667/50*h);ctx.stroke();ctx.setLineDash([]);
  ctx.strokeStyle='#cde980';ctx.lineWidth=2;ctx.beginPath();rows.forEach((r,i)=>{const x=i/Math.max(1,rows.length-1)*w,y=h-Math.min(50,r.frame)/50*h;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();
}
function ui(){
  const rows=metrics.recent.slice(-180);if(!rows.length)return;const s=summarize(rows),r=currentRow,h=crowd.health(),ms=x=>x===null?'不支持':x.toFixed(2)+' ms';
  $('fps').textContent=s.fps.toFixed(0);$('p95').textContent=ms(s.p95Ms);$('gpu').textContent=gpu.ext?ms(gpu.last):'不支持';
  $('sim-ms').textContent=ms(s.cpuMs.simulation);$('anim-ms').textContent=ms(s.cpuMs.animation);$('sync-ms').textContent=ms(s.cpuMs.sync);$('submit-ms').textContent=ms(s.cpuMs.submit);
  $('calls').textContent=r.calls.toLocaleString();$('triangles').textContent=(r.triangles/1000).toFixed(1)+' k';
  $('visible').textContent=`${r.visible} / ${crowd.count}`;$('animated-count').textContent=r.animated;
  $('moving').textContent=`${h.moving} / ${h.arrived}`;$('nav-ms').textContent=crowd.navMs.toFixed(2)+' ms';
  const mem=view.renderer.info.memory;$('resources').textContent=`${mem.geometries} / ${mem.textures}`;
  $('heap').textContent=performance.memory?(performance.memory.usedJSHeapSize/1048576).toFixed(1)+' MiB':'浏览器未公开';
  $('health').textContent=`视锥内 ${r.visible} / ${crowd.count} · ${r.animated} 只动画更新`;
  $('model-counts').textContent=r.models.filter(m=>m.count).map(m=>`${m.id==='model:zombie-runtime'?'00':m.id.slice(-2)}　${m.count} / ${m.visible} / ${m.animated}`).join('\n');
  $('camera-label').textContent=`${metrics.busy?'采样锁定总览':'正交观察'} / ${view.width} × ${view.width*9/16}`;
  const state=metrics.status();$('live-state').textContent=state.state==='warmup'?'预热':state.state==='recording'?'采样中':'实时';
  if(metrics.busy)$('sample-status').textContent=`${state.state==='warmup'?'预热剩余':'采样剩余'} ${Math.ceil(state.remaining)} 秒 · ${metrics.rows.length} 帧`;
  chart(rows);
}
function frame(now){
  const raw=last?(now-last)/1000:1/60;last=now;const dt=Math.min(raw,.25);let dropped=Math.max(0,raw-dt),simMs=0;
  const t0=performance.now();
  if(!paused){accumulator+=dt;let steps=0;while(accumulator>=step&&steps<7){crowd.tick(step);accumulator-=step;steps++;}if(accumulator>=step){dropped+=accumulator;accumulator=0;}}
  simMs=performance.now()-t0;
  const t1=performance.now(),animated=actors.animate(dt,crowd.agents,crowd.mode,paused),t2=performance.now();
  view.updateCamera(dt);actors.sync(crowd.agents,paused?1:accumulator/step,dt);const visible=view.sync(crowd,actors),t3=performance.now();
  gpu.begin();view.render();const t4=performance.now(),info=view.renderer.info.render;
  currentRow={t:now,frame:raw*1000,simulation:simMs,animation:t2-t1,sync:t3-t2,submit:t4-t3,gpu:null,
    calls:info.calls,triangles:info.triangles,visible,animated,moving:actors.frameCounts.reduce((s,m)=>s+m.moving,0),neighbors:crowd.neighborChecks,dropped,models:actors.frameCounts.map(m=>({...m}))};
  gpu.end(currentRow);metrics.add(currentRow);
  if(now-lastUi>250){ui();lastUi=now;}
  requestAnimationFrame(frame);
}

async function main(){
  manifest=await fetch('/manifest.json').then(r=>{if(!r.ok)throw new Error('资源清单读取失败');return r.json();});
  view=new View($('viewport'));crowd=new Crowd();actors=new Actors(view.scene);metrics=new Metrics();gpu=new GpuTimer(view.renderer);
  await actors.load(view.renderer,manifest);reset();
  view.onTarget=p=>{if(crowd.setTarget(p.x,p.z)){crowd.patrol=false;$('patrol').textContent='往返 已关';$('patrol').setAttribute('aria-pressed','false');toast('群体目标已更新');}else toast('目标位于障碍内，请选择空地');};
  for(const id of ['mode','count','actor-set'])$(id).addEventListener('change',()=>{reset();$(id).blur();});
  $('quality').addEventListener('change',()=>{view.resize(Number($('quality').value));$('quality').blur();});
  $('reset').onclick=reset;$('pause').onclick=togglePause;$('home').onclick=()=>view.home();$('focus').onclick=()=>view.focus(crowd.agents);
  $('patrol').onclick=()=>{crowd.patrol=!crowd.patrol;$('patrol').textContent=`往返 ${crowd.patrol?'已开':'已关'}`;$('patrol').setAttribute('aria-pressed',String(crowd.patrol));};
  $('quick-sample').onclick=()=>sample(20);$('sample').onclick=()=>sample(60);$('long-sample').onclick=()=>sample(180);
  $('matrix').onclick=()=>startMatrix().catch(e=>toast(e.message));
  $('cancel').onclick=()=>{matrixQueue=[];metrics.cancel();setBusy(false);$('sample-status').textContent='采样已取消';$('matrix-status').textContent='矩阵已停止，已归档样本保留';};
  $('json').onclick=()=>metrics.download('json');$('csv').onclick=()=>metrics.download('csv');
  metrics.onDone=async result=>{
    const s=result.summary;$('sample-status').textContent=`${result.valid?'采样完成':'页面焦点中断'} · ${s.fps.toFixed(1)} FPS · P95 ${s.p95Ms.toFixed(2)} ms`;
    try{
      const response=await fetch('/api/samples',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});
      if(!response.ok)throw new Error(await response.text());archiveState=await response.json();
      $('save-status').textContent=archiveState.nativeForegroundVerified?'原始JSON／CSV已归档，Windows前台核验通过':'已归档为窗口条件不合格，不能作为稳定60帧验收';
    }catch(e){archiveState={error:e.message};$('save-status').textContent='自动归档失败，请导出JSON／CSV：'+e.message;}
    setBusy(false);$('json').disabled=$('csv').disabled=false;
    if(matrixQueue.length){if(result.valid&&archiveState?.nativeForegroundVerified)nextMatrix();else{matrixQueue=[];$('matrix-status').textContent='窗口中断或归档失败，矩阵已停止；不自动抢前台';}}
    else if(matrixTotal)$('matrix-status').textContent='矩阵已结束，请查看归档及判定';
  };
  window.addEventListener('keydown',e=>{if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName))return;if(e.code==='Space'){e.preventDefault();togglePause();}if(e.code==='KeyC')view.focus(crowd.agents);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)metrics.interrupt('页面隐藏');});
  window.addEventListener('blur',()=>metrics.interrupt('窗口失焦'));
  $('engine').textContent=`Three.js ${manifest.three} · WebGL2`;$('loading').hidden=true;ready=true;requestAnimationFrame(frame);
}

window.horde={get ready(){return ready;},snapshot(){return {ready,config:config(),environment:environment(),health:crowd.health(),target:{...crowd.target},agents:crowd.agents.slice(0,8).map(a=>({x:a.x,z:a.z})),animation:actors.evidence(),row:currentRow,paused,metrics:metrics.status()};},
  result:()=>metrics.result,screen:(x,z)=>view.screen(x,z),sampleStatus:()=>({...metrics.status(),matrixRemaining:matrixQueue.length,archive:archiveState})};
main().catch(e=>{console.error(e);$('fatal').hidden=false;$('fatal').textContent='测试场初始化失败\n'+e.stack;});
