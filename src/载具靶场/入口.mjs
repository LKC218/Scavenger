import {Battlefield} from './场景.mjs';
import {Commander} from './输入.mjs';
import {Simulation,WEAPONS} from './模拟.mjs';
import {BattlefieldAudio} from './声音.mjs';
import {Benchmark} from './统计.mjs';

const $=s=>document.querySelector(s),quantile=(values,n)=>{const a=[...values].sort((a,b)=>a-b);return a[Math.min(a.length-1,Math.floor(a.length*n))]??0;};
const fatal=error=>{console.error(error);$('#fatal').hidden=false;$('#fatal').textContent='靶场未能启动\n'+error.message+'\n请从项目根目录的“打开载具靶场.cmd”启动，检查本机服务日志。';};
async function start(){
  const response=await fetch('/manifest.json');if(!response.ok)throw new Error('固定资源清单不可用');const manifest=await response.json();
  const mode=new URLSearchParams(location.search).get('mode')??(manifest.defense?'defense':'city'),defense=manifest.defense&&mode==='defense';
  const defenseModule=defense?await import('./丧尸规则.mjs'):null,defenseView=defense?await import('./尸群表现.mjs'):null;
  const view=new (defense?defenseView.DefenseBattlefield:Battlefield)($('#canvas-host'),$('#labels'));
  const externalMap=manifest.mapURL?await (await import('/地图运行.mjs')).loadMap(manifest.mapURL,view.renderer,message=>$('#load-status').textContent=message):null;
  function defenseSettings(){
    const value=(id,min,max,fallback)=>{const n=Number($('#'+id).value);return Number.isFinite(n)?Math.min(max,Math.max(min,n)):fallback;};
    const n=Math.round(value('zombie-count',1,1000,100)),endless=$('#defense-endless').checked;
    return{composition:({tank:{tank:1,heli:0,plane:0},trio:{tank:1,heli:1,plane:1},ten:{tank:6,heli:3,plane:1},thirty:{tank:20,heli:8,plane:2}})[$('#defense-force').value],
      zombies:n,waves:[n,Math.round(n*1.5),n*2],integrity:endless?1000000:Math.round(value('defense-health',1,1000,30)),interval:value('wave-interval',0,60,10),speed:value('zombie-speed',.1,5,1.6),seed:Math.round(value('defense-seed',1,99999999,261104)),modelMode:$('#zombie-model').value,endless};
  }
  const sim=defense?new defenseModule.DefenseSimulation(defenseSettings()):new Simulation({city:mode!=='classic',total:mode==='classic'?10:30,map:externalMap??undefined});
  if(manifest.defense){$('#scenario').prepend(new Option('丧尸防线 · 三车协同','defense',defense,defense));if(mode==='classic')$('#scenario').value='classic';}
  if(defense){document.body.classList.add('defense-mode');document.title='丧尸防线 · 三车战斗原型 '+manifest.version;$('#defense-panel').hidden=false;$('#loading h1').textContent='建立丧尸防线';$('.brand small').textContent='守住防线 · 三车战斗 '+manifest.version;$('.scope-note').textContent='普通丧尸100生命 · 无友伤 · 无限储备与装填 · 原作参数仅作参考';$('.score>div').firstChild.textContent='已击杀丧尸';$('#unit-count').closest('label').hidden=true;$('#load-mode').closest('label').hidden=true;$('#recover').closest('label').hidden=true;$('#reset').textContent='重新布防';}
  if(externalMap){const option=new Option(externalMap.name,externalMap.id,true,true);$('#scenario').add(option);document.title=externalMap.name+' · 地图验证';$('#loading h1').textContent=externalMap.name;const debug=document.createElement('button');debug.id='map-debug';debug.textContent='核验叠加';debug.style.pointerEvents='auto';debug.addEventListener('click',()=>{externalMap.overlay.visible=!externalMap.overlay.visible;debug.setAttribute('aria-pressed',String(externalMap.overlay.visible));});$('.field-top').append(debug);}
  await view.load(manifest,message=>$('#load-status').textContent=message);if(defense)await view.loadActors(manifest,message=>$('#load-status').textContent=message,sim);view.setup(sim);if(defense){$('#load-status').textContent='准备场景着色器与首次缓冲…';await view.renderer.compileAsync(view.scene,view.camera);view.render();}
  let uiNext=0,last=performance.now(),accumulator=0,frames=[],cpuFrames=[],toastUntil=0,page=0,soundTouched=false,droppedSimulation=0;
  const toast=message=>{$('#toast').textContent=message;toastUntil=performance.now()+6500;};
  const mixer=new BattlefieldAudio(view,manifest,toast),benchmark=new Benchmark(),cards=new Map();
  const gl=view.renderer.getContext(),extension=gl.getExtension('WEBGL_debug_renderer_info');
  const renderer=extension?gl.getParameter(extension.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
  const abortBench=reason=>{if(benchmark.run){benchmark.cancel();$('#bench-status').textContent='采样已中止：'+reason;}};
  const togglePause=()=>{abortBench('暂停状态改变');sim.paused=!sim.paused;if(sim.paused)mixer.stop();last=performance.now();accumulator=0;updateUI();};
  const commander=new Commander({field:$('#battlefield'),view,sim,toast,togglePause,onChange:()=>updateUI()});
  if(manifest.referenceUI){
    const icons={attackMove:'Order_Attack',ground:'Order_ForceFire',reverse:'Order_Reverse',strafe:'Order_StrafingRun',bomb:'Order_BombingRun'};
    for(const [mode,name] of Object.entries(icons)){const image=document.createElement('img');image.src=manifest.referenceUI[name];image.alt='';document.querySelector(`[data-mode="${mode}"] span`).prepend(image);}
    const image=document.createElement('img');image.src=manifest.referenceUI.Order_Stop;image.alt='';$('#stop span').prepend(image);
    const cursor=document.createElement('img');cursor.className='battle-cursor';cursor.alt='';cursor.hidden=true;$('#battlefield').append(cursor);
    commander.updateCursor=p=>{if(commander.drag?.button===1)return;const hit=view.pick(p.x,p.y),key=hit?.type==='target'&&commander.selected.size?'CursorAttack':commander.mode==='reverse'?'CursorReverse':['ground','bomb','strafe'].includes(commander.mode)?'CursorFirePos':null;
      cursor.hidden=!key||!manifest.referenceUI[key];$('#battlefield').style.cursor=cursor.hidden?'':'none';if(!cursor.hidden){cursor.src=manifest.referenceUI[key];cursor.style.left=p.x+'px';cursor.style.top=p.y+'px';}};
    $('#battlefield').addEventListener('pointerleave',()=>{cursor.hidden=true;$('#battlefield').style.cursor='';});
  }
  if(defense){$('[data-weapon="auto"]').hidden=false;$('#weapon-enable').hidden=false;}
  function rebuildRoster(){
    const filtered=sim.units.filter(u=>$('#roster-filter').value==='all'||u.kind===$('#roster-filter').value),pages=Math.max(1,Math.ceil(filtered.length/40));page=Math.max(0,Math.min(page,pages-1));
    $('#roster').replaceChildren();cards.clear();
    for(const u of filtered.slice(page*40,(page+1)*40)){const button=document.createElement('button');button.className='unit-card';button.dataset.unit=u.id;button.innerHTML=`<strong><span class="unit-kind">${{tank:'▰',heli:'✣',plane:'▲'}[u.kind]}</span>${u.name}</strong><small>待命</small><i class="ready"></i>`;button.addEventListener('click',e=>commander.choose(u.id,e.shiftKey));$('#roster').append(button);cards.set(u.id,button);}
    $('#roster-page').textContent=`${page+1} / ${pages} 页`;
    $('#roster-prev').disabled=page===0;$('#roster-next').disabled=page===pages-1;
  }
  function updateUI(){
    const ids=new Set(commander.ids()),units=sim.units.filter(u=>ids.has(u.id)),counts=kind=>units.filter(u=>u.kind===kind).length,tank=counts('tank'),heli=counts('heli'),plane=counts('plane');
    $('#selection-title').textContent=units.length===1?units[0].name:units.length?`${units.length} 个单位已选中`:'尚未选择单位';
    $('#selection-sub').textContent=units.length?`${tank} 坦克 · ${heli} 旋翼 · ${plane} 固定翼${commander.focus?' · 子选择':''} · 队列 ${units.reduce((n,u)=>n+u.queue.length,0)}`:'点选载具，1地面 / 2旋翼 / 4固定翼 / 3全部';
    $('#pause').textContent=sim.paused?'▶ 恢复':'Ⅱ 暂停';$('#running').textContent=sim.paused?'演训已暂停':'演训进行中';$('#pause-overlay').hidden=!sim.paused;
    $('#clock').textContent=`${String(Math.floor(sim.time/60)).padStart(2,'0')}:${String(Math.floor(sim.time%60)).padStart(2,'0')}`;
    $('#kills').innerHTML=`${sim.kills}<span>${defense?' / '+sim.spawned:sim.recoverTargets?'累计':'/ '+sim.targets.length}</span>`;$('#combat-stats').textContent=`${sim.shots} 次发射 · ${sim.hits} 次伤害命中`;
    $('#map-name').textContent=defense?'丧尸防线 · 两条进攻通道 · 固定翼自由进场':sim.map.heightAt?sim.map.name+' · '+sim.map.size+' × '+sim.map.size+'米':sim.map.id==='city'?'城市演训区 · 1200 × 1200米':'小靶场对照 · 144 × 174米';$('#unit-total').textContent=`共 ${sim.units.length} 单位`;
    if(defense){const s=sim.stats(),receivers=units.filter(u=>u.kind!=='plane'),guarded=receivers.filter(u=>u.guard).length;$('#defense-phase').textContent=sim.result?(sim.result==='victory'?'防守成功':'防线失守'):sim.phase==='ready'?'部署完成 · 等待开始':sim.phase==='interval'?`下一波 ${Math.ceil(sim.nextWaveAt-sim.time)} 秒`:`第 ${s.wave} 波 / ${sim.options.endless?'∞':s.waveCount}`;$('#defense-ledger').textContent=`存活 ${s.alive} · 击杀 ${s.killed} · 漏防 ${s.leaked}`;$('#defense-integrity').textContent=sim.options.endless?'持续压力 · 不判输赢':`防线耐久 ${s.integrity} / ${sim.maxIntegrity}`;$('#integrity-bar').style.width=sim.integrity/sim.maxIntegrity*100+'%';$('#defense-start').disabled=sim.started;$('#defense-start').textContent=sim.started?'防守进行中':'开始防守';$('#guard-toggle').disabled=!receivers.length;$('#guard-toggle').setAttribute('aria-pressed',String(guarded===receivers.length&&guarded>0));$('#guard-toggle').textContent=guarded===receivers.length?'警戒 开':guarded===0?'警戒 关':'警戒 部分';$('#guard-hint').textContent=receivers.length?`作用于选中 ${receivers.length} 个地面／旋翼单位；手动指令优先，不追出射程。`:'选择地面或旋翼切换警戒；固定翼使用手动攻击航线。';$('#running').textContent=sim.paused?'防守已暂停':$('#defense-phase').textContent;$('#defense-result').hidden=!sim.result;$('#defense-result-title').textContent=sim.result==='victory'?'防线守住了':'防线被突破';$('#defense-result-summary').textContent=`完成 ${s.wave} 波 · 击杀 ${s.killed} · 漏过 ${s.leaked} · 防线剩余 ${s.integrity}。可重新布防，或调整数量与速度进行对照。`;}
    document.querySelectorAll('[data-group]').forEach(b=>{b.querySelector('b').textContent=b.dataset.group==='all'?sim.units.length:sim.units.filter(u=>u.kind===b.dataset.group).length;});
    document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode===commander.mode);b.disabled=!units.length||(['strafe','bomb'].includes(b.dataset.mode)&&!plane)||(b.dataset.mode==='reverse'&&!tank)||(['attackMove','ground'].includes(b.dataset.mode)&&!tank&&!heli);});$('#stop').disabled=!units.length;$('#focus').disabled=!units.length;
    const heliUnits=units.filter(u=>u.kind==='heli');document.querySelectorAll('[data-weapon]').forEach(b=>{b.disabled=!heli;b.classList.toggle('active',heli>0&&heliUnits.every(u=>b.dataset.weapon==='auto'?u.weaponMode==='auto':u.weapon===b.dataset.weapon&&u.weaponMode!=='auto'));});
    document.querySelectorAll('[data-enable]').forEach(b=>{const enabled=heli>0&&heliUnits.every(u=>u.weaponEnabled?.[b.dataset.enable]);b.disabled=!heli;b.textContent=(b.dataset.enable==='gun'?'机炮':'导弹')+(enabled?' 开':' 关');b.setAttribute('aria-pressed',String(enabled));});
    $('#weapon-status').textContent=heli?heliUnits.every(u=>u.weapon===heliUnits[0].weapon)?`${heliUnits[0].weaponMode==='auto'?'自动 → ':''}${sim.weapon(heliUnits[0]).name} · 射程 ${sim.weapon(heliUnits[0]).range}`:'不同直升机武器不同':'选择直升机后切换';
    for(const [id,card] of cards){const u=sim.units.find(u=>u.id===id);card.classList.toggle('active',commander.selected.has(id));card.classList.toggle('focused',commander.focus===id);card.querySelector('small').textContent=u.state+(u.queue.length?` · 后续${u.queue.length}`:'');card.querySelector('.ready').style.width=Math.max(0,1-Math.max(0,u.readyAt-sim.time)/sim.weapon(u).reload)*100+'%';}
    view.minimap($('#minimap'),sim,commander.selected);
    $('#bench-stop').disabled=!benchmark.run;$('#bench-export').disabled=!benchmark.last;
  }
  function reset(){
    abortBench('场景或负载改变');mixer.stop();mixer.resetStats();commander.clearTransient();commander.selected.clear();commander.focus=null;
    if(defense)Object.assign(sim.options,defenseSettings());else{sim.options.map=$('#scenario').value===externalMap?.id?externalMap:undefined;sim.options.city=$('#scenario').value!=='classic';sim.options.total=sim.options.city?Number($('#unit-count').value):10;}
    sim.reset();if(!defense)sim.configureLoad($('#load-mode').value);$('#recover').checked=sim.recoverTargets;
    $('#unit-count').disabled=!sim.options.city;view.setup(sim);view.home(true);page=0;rebuildRoster();accumulator=0;last=performance.now();frames=[];cpuFrames=[];updateUI();toast(`已重置：${sim.units.length} 单位 · ${sim.targets.length} 靶标 · ${$('#load-mode').selectedOptions[0].textContent}`);
  }
  $('#pause').addEventListener('click',togglePause);$('#reset').addEventListener('click',reset);
  for(const id of ['unit-count','load-mode'])$('#'+id).addEventListener('change',reset);
  $('#scenario').addEventListener('change',()=>{if(defense||$('#scenario').value==='defense'){location.href='/?mode='+$('#scenario').value;return;}$('#load-mode').value='manual';reset();});
  if(defense){$('#defense-start').addEventListener('click',async()=>{if(sim.paused)togglePause();if(sim.begin()){toast('防守开始：警戒自动开火，固定翼按4选中后K扫射／B轰炸');if(!soundTouched&&!mixer.active){try{await mixer.toggle();$('#sound').textContent=mixer.active?'声音 开':'声音 关';$('#sound').setAttribute('aria-pressed',String(mixer.active));}catch(e){toast(e.message);}}updateUI();}});$('#guard-toggle').addEventListener('click',()=>{abortBench('警戒改变');const ids=commander.ids(),units=sim.units.filter(u=>ids.includes(u.id)&&u.kind!=='plane'),value=!units.every(u=>u.guard);sim.setGuard(ids,value);toast(value?'选中单位警戒开启 · 手动指令优先':'选中单位警戒关闭 · 仍可指定攻击');updateUI();});$('#defense-apply').addEventListener('click',reset);$('#defense-retry').addEventListener('click',reset);}
  $('#recover').addEventListener('change',e=>{abortBench('靶标恢复规则改变');sim.recoverTargets=e.target.checked;});
  $('#quality').addEventListener('change',e=>{abortBench('画质改变');view.quality=Number(e.target.value);view.resize();frames=[];});
  $('#shadow-quality').addEventListener('change',e=>{abortBench('阴影模式改变');view.shadowMode=e.target.value;frames=[];});
  $('#roster-filter').addEventListener('change',()=>{page=0;rebuildRoster();updateUI();});
  $('#roster-prev').addEventListener('click',()=>{page--;rebuildRoster();updateUI();});$('#roster-next').addEventListener('click',()=>{page++;rebuildRoster();updateUI();});
  $('#overview').addEventListener('click',()=>{view.focusAt(sim.map.center);view.targetSpan=sim.map.maxSpan;});
  $('#volume').addEventListener('input',e=>{abortBench('音量改变');mixer.setVolume(Number(e.target.value)/100);$('#volume-value').textContent=e.target.value+'%';});
  $('#enhancement').addEventListener('change',e=>{abortBench('响度增强改变');const db=mixer.setEnhancement(e.target.value);toast(db?`响度增强 +${db}dB · 总音量 ${Math.round(mixer.runtime.masterVolume*100)}%`:'已恢复原始响度');});
  $('#sound').addEventListener('click',async()=>{soundTouched=true;abortBench('声音开关改变');$('#sound').disabled=true;try{await mixer.toggle();$('#sound').textContent=mixer.active?'声音 开':'声音 关';$('#sound').setAttribute('aria-pressed',String(mixer.active));toast(mixer.active?'声音已开启 · 待机、移动与飞行机械声持续播放':'声音已关闭');}catch(error){toast(error.message);}finally{$('#sound').disabled=false;}});
  function perf(){const a=mixer.stats();return{actorUpdateMs:view.actors?.updateMs??null,actorRebuildMs:view.actors?.rebuildMs??null,actorBatches:view.actors?.batchCalls??null,defense:defense?sim.stats():null,animatedActors:view.actors?.animated??0,visibleActors:view.actors?.visible??0,visibleCorpses:view.actors?.corpses??0,droppedSimulationSeconds:droppedSimulation,mapStats:sim.map.stats?{...sim.map.stats,errors:[...sim.map.stats.errors]}:null,camera:{x:view.center.x,z:view.center.z,span:view.span},shadow:{...view.shadowStats},frames:frames.length,p50:quantile(frames,.5),p95:quantile(frames,.95),p99:quantile(frames,.99),cpuP95:quantile(cpuFrames,.95),over20ms:frames.filter(t=>t>20).length,drawCalls:view.renderer.info.render.calls,triangles:view.renderer.info.render.triangles,geometries:view.renderer.info.memory.geometries,textures:view.renderer.info.memory.textures,textureEstimateBytes:view.textureEstimate(),jsHeap:performance.memory?.usedJSHeapSize??null,internal:[view.renderer.domElement.width,view.renderer.domElement.height],viewport:[view.width,view.height],renderer,browser:navigator.userAgent,paused:sim.paused,time:sim.time,totalUnits:sim.units.length,visibleUnits:view.visibleUnits,simulatedUnits:sim.loadMode==='static'?0:sim.units.length,movingUnits:sim.units.filter(u=>u.moving).length,buildings:sim.map.obstacles.filter(o=>o.building).length,projectiles:sim.projectiles.length,shots:sim.shots,hits:sim.hits,waitingPaths:sim.waitingPaths,voices:a.voices,engines:a.engines,enhancementDb:a.boostDb,audioRms:a.rms,audioRmsDb:a.rmsDb,audioPeak:a.peak,audioDropped:a.dropped,visualAmmoDropped:view.visualAmmoDropped};}
  function startBench(seconds){if(defense&&(!sim.started||sim.result)){toast('请先开始防守；长时间采样建议开启持续战斗压力');return;}if(sim.paused){toast('请先恢复演训');return;}mixer.resetStats();benchmark.start(performance.now(),seconds,{...perf(),map:sim.map.id,load:sim.loadMode,audio:mixer.active,volume:mixer.runtime.masterVolume,recover:sim.recoverTargets});$('#bench-status').textContent='预热10秒；请保持此页面在前台，保持镜头不动';updateUI();}
  $('#bench60').addEventListener('click',()=>startBench(60));$('#bench180').addEventListener('click',()=>startBench(180));$('#bench-stop').addEventListener('click',()=>{abortBench('手动停止');updateUI();});
  $('#bench-export').addEventListener('click',()=>{if(!benchmark.last)return;const url=URL.createObjectURL(new Blob([JSON.stringify(benchmark.last,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='载具靶场性能-'+Date.now()+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){abortBench('页面进入后台');commander.clearTransient();sim.paused=true;mixer.stop();updateUI();}last=performance.now();accumulator=0;});
  window.addEventListener('pagehide',()=>mixer.dispose());
  view.renderer.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();abortBench('图形上下文丢失');sim.paused=true;mixer.stop();toast('图形上下文丢失，请重载靶场');});
  // 输入验收使用真实键鼠；此入口供只读观察与独立采样。
  window.range={defense,externalMap,sim,view,commander,audio:mixer.runtime,mixer,benchmark,manifest,perf,resetMetrics:()=>{frames=[];cpuFrames=[];},ready:true};
  view.home(true);$('#loading').hidden=true;rebuildRoster();if(defense)commander.group('all');updateUI();toast(defense?'布防就绪：点击开始防守；警戒自动开火，4选飞机后K扫射／B轰炸':(externalMap?externalMap.name:'城市靶场')+'就绪：1地面 · 2旋翼 · 4固定翼 · 全场总览后拖线投弹');
  function frame(now){
    const realDt=Math.max(0,(now-last)/1000);last=now;
    if(!document.hidden){
      commander.step(Math.min(realDt,.06));const begin=performance.now();
      if(!sim.paused){droppedSimulation+=Math.max(0,realDt-.2);accumulator+=Math.min(realDt,.2);while(accumulator>=1/60){sim.step(1/60);accumulator-=1/60;}}else accumulator=0;
      const cpu=performance.now()-begin,events=sim.events.splice(0);mixer.step(sim,commander.selected,now);
      for(const event of events){view.event(event);if(!sim.paused)mixer.event(event);}
      view.sync(sim,commander.selected,commander.focus,sim.paused?0:Math.min(realDt,.1));view.render();
      if(realDt>0){frames.push(realDt*1000);cpuFrames.push(cpu);if(frames.length>3600){frames.shift();cpuFrames.shift();}}
      if(defense&&sim.result&&benchmark.run)abortBench('防守结束；压力采样请开启持续战斗');
      if(benchmark.run){const r=benchmark.run,state=(now-r.start)/1000-r.warmup>=r.next?perf():{};const status=benchmark.step(now,realDt*1000,cpu,state);if(status)$('#bench-status').textContent=status;}
      if(now>=uiNext){uiNext=now+200;updateUI();const p=perf(),a=mixer.stats();$('#stats').textContent=`${p.p50?Math.round(1000/p.p50):'—'} FPS · ${p.drawCalls} 绘制 · ${(p.triangles/1000).toFixed(0)}K 三角形`;$('#resolution').textContent=p.internal.join(' × ');$('#population-stats').textContent=`场内 ${p.totalUnits} · 入画中心 ${p.visibleUnits} · 模拟 ${p.simulatedUnits} · 移动 ${p.movingUnits} · 弹丸 ${p.projectiles} · CPU P95 ${p.cpuP95.toFixed(2)}ms`;$('#audio-stats').textContent=`发声 ${a.voices}/48 · 引擎 ${a.engines}/8 · 峰值 ${a.peak.toFixed(3)} · 压缩 ${a.compressionDb.toFixed(1)}dB`;}
      if(now>toastUntil&&toastUntil!==0){$('#toast').textContent=commander.mode?'左键确认／拖线 · Shift 排队 · Esc 取消预览':'左键选择 · 右键下令 · 4固定翼 · K扫射 / B投弹';toastUntil=0;}
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}
start().catch(fatal);
