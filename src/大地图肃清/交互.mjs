/** A线只维护镜头、选择和界面；所有玩法修改均经带本轮身份的命令进入规则。 */
import * as THREE from 'three';
import {clamp} from './契约.mjs';

const NAMES = {tank:'M1A2',heli:'AH-64D',plane:'A-10C'};
const DOMAINS = {all:'全部',ground:'地面',air:'空中'};
const STATE_NAMES = {idle:'待命',moving:'移动',flying:'飞行',turning:'转向',orbit:'盘旋',orbiting:'盘旋',cruise:'巡航',work:'作业',blocked:'受阻',destroyed:'已毁伤',stopped:'已停止'};
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = v => Number.isFinite(v) ? v : 0;
const fmt = (v,d=0) => Number.isFinite(v) ? v.toFixed(d) : '—';
const isAlive = u => u && u.alive !== false && u.hp > 0;
const editable = target => target?.closest?.('input,select,textarea,[contenteditable="true"]');
const SVG_NS = 'http://www.w3.org/2000/svg';

export function createInteraction({canvas,camera,world,config,getSnapshot,dispatch,presentation,ui}) {
  if (!canvas || !camera?.isOrthographicCamera || !ui) throw new Error('交互需要画布、正交镜头与界面根节点');
  const stage = canvas.closest('#stage') || canvas.parentElement;
  const status = stage.querySelector('#status') || document.querySelector('#status');
  const state = {selectedIds:[],filter:'all',followId:null,hoverId:null,commandMode:null,focus:{x:0,y:0,z:0},viewWidth:config.camera.width};
  let azimuth=0, runId=null, commandId=0, drag=null, overlapIds=[], overlapIndex=-1;
  let followOffset={x:0,y:0,z:0};
  let lastPointer=null, previewWeapon=null, hudClock=Infinity, lastHUD='', disposed=false, hintUntil=0;
  const keys=new Set(), removers=[], labelNodes=new Map();
  const raycaster=new THREE.Raycaster(), mouse=new THREE.Vector2(), temporary=new THREE.Vector3();
  const rayPoint=new THREE.Vector3(), groundPlane=new THREE.Plane(new THREE.Vector3(0,1,0),0);
  const labels=document.createElement('div'); labels.className='unit-labels'; labels.setAttribute('aria-label','单位标签');
  const selectionBox=document.createElement('div'); selectionBox.className='selection-box'; selectionBox.hidden=true;
  const overlay=document.createElementNS(SVG_NS,'svg'); overlay.classList.add('tactical-overlay'); overlay.setAttribute('aria-hidden','true');
  const rangePath=document.createElementNS(SVG_NS,'path'); rangePath.classList.add('range-preview');
  const aimPath=document.createElementNS(SVG_NS,'path'); aimPath.classList.add('aim-preview');
  const blastPath=document.createElementNS(SVG_NS,'path'); blastPath.classList.add('blast-preview');
  const linkPath=document.createElementNS(SVG_NS,'path'); linkPath.classList.add('altitude-links');
  overlay.append(rangePath,aimPath,blastPath,linkPath);
  const marker=document.createElement('div'); marker.className='ground-marker'; marker.hidden=true;
  let markerPoint=null, markerUntil=0;
  stage.append(overlay,labels,selectionBox,marker);
  const presetOptions=Object.entries(config.presets).map(([id,p])=>`<option value="${id}">${escapeHTML(p.label)}</option>`).join('');
  ui.innerHTML=`
    <header class="command-top"><div class="identity"><span class="identity-mark">肃</span><div><h1>大地图肃清</h1><p>受控场景 · <strong>候选 ${escapeHTML(config.version)}</strong></p></div></div><div class="goal-state">G01—G04 <span>等待人工验收</span></div></header>
    <aside class="scenario-panel tactical-panel"><div class="panel-eyebrow">场景与观察</div><label class="scenario-label">检验场景<select data-action="preset" aria-label="检验场景">${presetOptions}</select></label><button data-action="reset" class="quiet">重置本轮</button><p class="reset-note">重置会重新开始当前样本</p><div class="audio-controls"><button data-action="audio" aria-pressed="false">声音 关</button><label>音量<input data-action="volume" aria-label="声音音量" type="range" min="0" max="100" value="25"><output data-role="volume">25%</output></label></div><div class="scene-counts" data-role="counts">正在读取场景</div><div class="origin-counts" data-role="origins" aria-label="按原属统计的剩余与初始数量"></div></aside>
    <section class="selection-toolbar tactical-panel" aria-label="选择与镜头"><div class="filter-group" role="group" aria-label="单位筛选">${Object.entries(DOMAINS).map(([id,label])=>`<button data-action="filter" data-value="${id}" aria-pressed="${id==='all'}">${label}</button>`).join('')}</div><div class="camera-actions"><button data-action="focus" title="F：一次聚焦">聚焦 <kbd>F</kbd></button><button data-action="follow" aria-pressed="false">跟随 关</button></div><span class="view-width" data-role="view-width"></span></section>
    <aside class="roster-panel tactical-panel"><div class="panel-heading"><span>在场部队</span><span data-role="selection-count">未选择</span></div><div data-role="roster" class="unit-roster"></div></aside>
    <section class="unit-panel tactical-panel"><div data-role="detail"></div></section>
    <section class="command-help"><span><b>左键</b> 点选／框选</span><span><b>Shift</b> 增减</span><span><b>Tab</b> 重叠循环</span><span><b>右键</b> 移动／攻击</span><span><b>WASD／中键</b> 平移</span><span><b>Q E</b> 旋转</span><span><b>滚轮</b> 缩放</span></section>
    <div class="mode-banner" data-role="mode" hidden></div><div class="interaction-notice" data-role="notice" role="status" aria-live="polite"></div>`;
  const dom=role=>ui.querySelector(`[data-role="${role}"]`);
  const on=(target,type,listener,options)=>{target.addEventListener(type,listener,options);removers.push(()=>target.removeEventListener(type,listener,options));};
  const snapshot=()=>getSnapshot();
  const units=()=>snapshot()?.units || [];
  const selected=()=>units().filter(u=>state.selectedIds.includes(u.id)&&isAlive(u));
  const allowed=u=>state.filter==='all'||(state.filter==='ground'?u.domain==='ground':u.domain!=='ground');
  function hint(text,error=false) {
    const notice=dom('notice');notice.textContent=text;notice.classList.toggle('error',error);notice.hidden=false;hintUntil=performance.now()+4500;
    if(status){status.textContent=text;status.dataset.interactionMessage=text;}
  }
  function cameraUpdate() {
    const rect=canvas.getBoundingClientRect(), aspect=Math.max(1,rect.width)/Math.max(1,rect.height), half=state.viewWidth/2;
    camera.left=-half;camera.right=half;camera.top=half/aspect;camera.bottom=-half/aspect;
    const distance=700, horizontal=distance*Math.cos(config.camera.tilt);
    camera.position.set(state.focus.x+Math.sin(azimuth)*horizontal,state.focus.y+Math.sin(config.camera.tilt)*distance,state.focus.z+Math.cos(azimuth)*horizontal);
    camera.up.set(0,1,0);camera.lookAt(state.focus.x,state.focus.y,state.focus.z);camera.updateProjectionMatrix();camera.updateMatrixWorld();
  }
  /** 仅供固定检验构型使用，不接入玩家面板；角度单位为弧度。 */
  function setView({focus,viewWidth,yaw}={}) {
    if(focus!==undefined&&(!focus||!['x','y','z'].every(axis=>Number.isFinite(focus[axis]))))throw new TypeError('镜头焦点须包含有限的x、y、z坐标');
    if(viewWidth!==undefined&&!Number.isFinite(viewWidth))throw new TypeError('镜头可视宽度须为有限数值');
    if(yaw!==undefined&&!Number.isFinite(yaw))throw new TypeError('镜头偏航须为有限弧度');
    state.followId=null;followOffset={x:0,y:0,z:0};keys.clear();drag=null;selectionBox.hidden=true;
    if(focus)state.focus={x:clamp(focus.x,world.bounds.minX,world.bounds.maxX),y:clamp(focus.y,0,(world.maxObstacleHeight||0)+config.plane.clearance),z:clamp(focus.z,world.bounds.minZ,world.bounds.maxZ)};
    if(viewWidth!==undefined)state.viewWidth=clamp(viewWidth,config.camera.minWidth,config.camera.maxWidth);
    if(yaw!==undefined)azimuth=Math.atan2(Math.sin(yaw),Math.cos(yaw));
    cameraUpdate();syncLabels();syncOverlay();renderHUD();hudClock=0;
    return{focus:{...state.focus},viewWidth:state.viewWidth,yaw:azimuth,followId:state.followId};
  }
  function project(position) {
    const rect=canvas.getBoundingClientRect();temporary.set(finite(position.x),finite(position.y),finite(position.z)).project(camera);
    return{x:(temporary.x+1)*rect.width/2,y:(1-temporary.y)*rect.height/2,visible:temporary.z>=-1&&temporary.z<=1&&Math.abs(temporary.x)<=1.04&&Math.abs(temporary.y)<=1.04};
  }
  function bodyProjection(u) {
    const p=presentation.projectUnit?.(u.id);return p&&Number.isFinite(p.x)&&Number.isFinite(p.y)?p:project(u.position);
  }
  function visibleCenter(u) {
    const center=presentation.getPickables?.().find(p=>p.id===u.id)?.center;
    return center&&['x','y','z'].every(axis=>Number.isFinite(center[axis]))?center:u.position;
  }
  function pointerLocal(event) {const r=canvas.getBoundingClientRect();return{x:event.clientX-r.left,y:event.clientY-r.top};}
  function groundAt(point) {
    const rect=canvas.getBoundingClientRect();mouse.set(point.x/rect.width*2-1,1-point.y/rect.height*2);raycaster.setFromCamera(mouse,camera);
    let height=0, support=null;
    for(let n=0;n<10;n++) {
      groundPlane.constant=-height;
      if(!raycaster.ray.intersectPlane(groundPlane,rayPoint))return{status:'unsupported'};
      support=world.querySupport(rayPoint.x,rayPoint.z);
      if(support.status!=='ready')return{status:support.status,point:{x:rayPoint.x,z:rayPoint.z}};
      if(Math.abs(height-support.height)<.001)return{status:'ready',point:{x:rayPoint.x,y:support.height,z:rayPoint.z},walkable:world.isWalkable(rayPoint.x,rayPoint.z,config.tank.radius)};
      height=support.height;
    }
    return{status:'pending'};
  }
  function supportError(result) {return result.status==='pending'?'落点地面尚未加载，未下令':'落点没有有效地面支撑，未下令';}
  function pickUnits(point) {
    const live=new Map(units().filter(u=>isAlive(u)&&allowed(u)).map(u=>[u.id,u]));
    const pickables=presentation.getPickables?.() || [...live.values()].map(u=>({id:u.id,radius:4,center:u.position}));
    return pickables.filter(p=>live.has(p.id)).map(p=>{
      const u=live.get(p.id),screen=bodyProjection(u),center=p.center||u.position;
      const edge=project({...center,x:center.x+Math.max(p.radius||3,2)});
      return{id:p.id,d:Math.hypot(screen.x-point.x,screen.y-point.y),r:clamp(Math.hypot(edge.x-screen.x,edge.y-screen.y)+5,12,42),visible:screen.visible};
    }).filter(p=>p.visible&&p.d<=p.r).sort((a,b)=>a.d-b.d).map(p=>p.id);
  }
  function pickEnemy(point) {
    let best=null;
    for(const z of snapshot()?.zombies||[]) {
      if(!z.alive||!z.visible)continue;
      const p=project({...z.position,y:z.position.y+config.zombie.height*.5}),d=Math.hypot(p.x-point.x,p.y-point.y);
      if(p.visible&&d<=14&&(!best||d<best.d))best={id:z.id,d};
    }
    return best?.id;
  }
  function setSelection(ids) {
    const live=new Set(units().filter(u=>isAlive(u)&&allowed(u)).map(u=>u.id));
    state.selectedIds=[...new Set(ids)].filter(id=>live.has(id));
    if(state.followId&&!state.selectedIds.includes(state.followId))state.followId=null;
    previewWeapon=null;hudClock=Infinity;lastHUD='';
  }
  function selectCandidates(ids,shift) {
    if(!shift){setSelection(ids);return;}
    const next=new Set(state.selectedIds);for(const id of ids){if(next.has(id))next.delete(id);else next.add(id);}setSelection([...next]);
  }
  function issue(type,extra={},targetUnits=selected()) {
    const s=snapshot();if(!s)return;
    if(!targetUnits.length&&type!=='bait'){hint('请先选择可用单位',true);return;}
    const command={runId:s.runId,commandId:++commandId,type,unitIds:targetUnits.map(u=>u.id),...extra};
    const result=dispatch(command);
    if(result?.ok===false){hint(`命令未执行：${result.reason||'规则拒绝'}`,true);return false;}
    hudClock=Infinity;return true;
  }
  function showMarker(point,text) {markerPoint={...point};markerUntil=performance.now()+3000;marker.textContent=text;marker.hidden=false;}
  function commitPoint(point) {
    const ground=groundAt(point);if(ground.status!=='ready'){hint(supportError(ground),true);return;}
    const mode=state.commandMode;
    if(mode==='bait'&&!ground.walkable){hint('诱饵落点不可通行，未放置',true);return;}
    const type=mode==='bait'?'bait':'attackGround';
    if(issue(type,{point:ground.point},type==='bait'?[]:selected().filter(u=>u.domain==='ground'))) {
      showMarker(ground.point,type==='bait'?'诱饵 · 5秒后启动':'攻击地面');hint(type==='bait'?'已放置受控诱饵：5秒后发声，持续30秒':'已下达攻击地面；仅已选弹并启用的地面武器参与');
      setMode(null);
    }
  }
  function rightCommand(point) {
    const targetId=pickEnemy(point);
    if(targetId){if(issue('attack',{targetId},selected().filter(u=>u.domain==='ground')))hint('已下达可见目标攻击');return;}
    const ground=groundAt(point);if(ground.status!=='ready'){hint(supportError(ground),true);return;}
    const all=selected(),valid=all.filter(u=>u.domain!=='ground'||world.isWalkable(ground.point.x,ground.point.z,config.tank.radius));
    if(all.length&&!valid.length){hint('落点不可通行，未下达地面移动',true);return;}
    if(issue('move',{point:ground.point},valid)){showMarker(ground.point,`移动 · 地面${fmt(ground.point.y)}米`);hint(valid.length<all.length?'已向空中单位下令；地面单位落点不可通行':'已下达地面落点移动；高度由规则计算');}
  }
  function setMode(mode) {
    state.commandMode=mode;canvas.classList.toggle('point-command',!!mode);const banner=dom('mode');banner.hidden=!mode;
    banner.textContent=mode==='bait'?'放置受控诱饵：左键选地面 · Esc取消':'攻击地面：左键选落点 · Esc取消';hudClock=Infinity;
  }
  function focusSelection() {
    const group=selected();if(!group.length){hint('请先选择单位',true);return;}
    state.focus={x:0,y:0,z:0};for(const u of group){const center=visibleCenter(u);for(const axis of ['x','y','z'])state.focus[axis]+=center[axis]/group.length;}
    if(state.followId)followOffset={x:0,y:0,z:0};
    cameraUpdate();hint('已聚焦选中单位；持续跟随需单独开启');
  }
  function pan(dx,dz) {
    state.followId=null;
    state.focus.x=clamp(state.focus.x+dx,world.bounds.minX,world.bounds.maxX);
    state.focus.z=clamp(state.focus.z+dz,world.bounds.minZ,world.bounds.maxZ);
  }
  function syncLabels() {
    const live=units().filter(isAlive),ids=new Set(live.map(u=>u.id)),rect=canvas.getBoundingClientRect();
    const pickables=new Map((presentation.getPickables?.()||[]).map(p=>[p.id,p]));
    const bodies=live.map(u=>({unit:u,point:bodyProjection(u),radius:clamp((pickables.get(u.id)?.radius||4)*rect.width/state.viewWidth+5,14,44)}));
    const reserved=bodies.filter(row=>row.point.visible).map(({point:p,radius:r})=>({left:p.x-r-6,top:p.y-r-6,right:p.x+r+6,bottom:p.y+r+6}));
    const occupied=[],overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
    for(const[id,node]of labelNodes)if(!ids.has(id)){node.remove();labelNodes.delete(id);}
    for(const {unit:u,point:p,radius:r} of bodies){
      let node=labelNodes.get(u.id);if(!node){node=document.createElement('button');node.className='unit-label';node.dataset.unitId=u.id;labels.append(node);labelNodes.set(u.id,node);}
      node.textContent=`${NAMES[u.kind]||u.kind} · ${u.id.replace(/^.*?[-:]/,'')}`;node.classList.toggle('selected',state.selectedIds.includes(u.id));node.setAttribute('aria-pressed',String(state.selectedIds.includes(u.id)));
      node.hidden=!p.visible||!allowed(u);if(node.hidden)continue;
      // 标签既避让自己的机身，也避让其他高度的机身，保证远景地空重叠仍能直接拾取。
      const width=Math.max(72,node.textContent.length*6+18),height=24,gap=r+10;
      const candidates=[[-width/2,-gap-height],[gap,-height/2],[-gap-width,-height/2],[-width/2,gap],
        [gap,-gap-height],[-gap-width,-gap-height],[gap,gap],[-gap-width,gap],
        [gap+90,-height/2],[-gap-width-90,-height/2],[-width/2,-gap-height-70],[-width/2,gap+70]];
      const boxes=candidates.map(([dx,dy])=>{const left=clamp(p.x+dx,4,Math.max(4,rect.width-width-4)),top=clamp(p.y+dy,4,Math.max(4,rect.height-height-4));return{left,top,right:left+width,bottom:top+height};});
      const position=boxes.find(box=>!reserved.some(body=>overlaps(box,body))&&!occupied.some(label=>overlaps(box,label)))
        ||boxes.find(box=>!reserved.some(body=>overlaps(box,body)));
      if(!position){node.hidden=true;continue;}
      occupied.push(position);node.style.transform=`translate(${position.left}px,${position.top}px)`;
    }
  }
  function circlePath(center,radius) {
    if(!radius)return'';const points=[];
    for(let i=0;i<=64;i++){const a=i/64*Math.PI*2,p=project({x:center.x+Math.cos(a)*radius,y:center.y,z:center.z+Math.sin(a)*radius});points.push(`${i?'L':'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`);}return points.join(' ')+' Z';
  }
  function syncOverlay() {
    const rect=canvas.getBoundingClientRect();overlay.setAttribute('viewBox',`0 0 ${rect.width} ${rect.height}`);
    let ranges='',aims='',blasts='',links='';
    for(const u of selected()) {
      const ground=world.querySupport(u.position.x,u.position.z),body=bodyProjection(u);
      if(ground.status==='ready') {
        const foot=project({...u.position,y:ground.height});if(u.domain!=='ground')links+=`M${body.x},${body.y} L${foot.x},${foot.y} M${foot.x-5},${foot.y}h10 M${foot.x},${foot.y-5}v10 `;
      }
      if(u.domain!=='ground'||(!previewWeapon&&state.commandMode!=='attackGround'))continue;
      const weaponId=previewWeapon||'main',spec=config.weapons[weaponId];if(!spec)continue;
      const center={...u.position,y:ground.status==='ready'?ground.height+.15:u.position.y};ranges+=circlePath(center,spec.range);
      const yaw=finite(u.yaw)+finite(weaponId==='m2'?u.m2Yaw:u.turretYaw),base=project(center),tip=project({x:center.x-Math.sin(yaw)*spec.range,y:center.y,z:center.z-Math.cos(yaw)*spec.range});
      aims+=`M${base.x},${base.y}L${tip.x},${tip.y} `;
      if(state.commandMode==='attackGround'&&lastPointer){const result=groundAt(lastPointer),ammo=config.ammunition[u.weapons?.[weaponId]?.ammoId];if(result.status==='ready'&&ammo?.outer){blasts+=circlePath(result.point,ammo.outer)+circlePath(result.point,ammo.inner);}}
    }
    rangePath.setAttribute('d',ranges);aimPath.setAttribute('d',aims);blastPath.setAttribute('d',blasts);linkPath.setAttribute('d',links);
    if(markerPoint&&performance.now()<markerUntil){const p=project(markerPoint);marker.hidden=!p.visible;marker.style.transform=`translate(${p.x}px,${p.y}px) translate(-50%,-50%)`;}else marker.hidden=true;
  }
  function weaponHTML(u,id) {
    const spec=config.weapons[id],w=u.weapons?.[id];if(!spec||!w)return'';
    const remaining=Math.max(0,finite(w.readyAt)-finite(snapshot().time));
    const reason=!w.ammoId?'请手动选弹，再启用武器':!w.enabled?'武器已禁用':remaining>0?`${w.reloadPending?'换箱':'装填'} ${fmt(remaining,1)}秒`:w.reason||'已启用';
    const ammoOptions=spec.allowedAmmo.map(ammoId=>{const a=config.ammunition[ammoId];return`<button data-action="ammo" data-unit="${escapeHTML(u.id)}" data-weapon="${id}" data-ammo="${ammoId}" aria-pressed="${w.ammoId===ammoId}"><span>${escapeHTML(a.name)}</span><b>${w.ammo?.[ammoId]??'—'}</b></button>`;}).join('');
    return`<div class="weapon-block ${previewWeapon===id?'previewing':''}"><div class="weapon-heading"><button class="weapon-name" data-action="preview" data-weapon="${id}" aria-pressed="${previewWeapon===id}">${escapeHTML(spec.name)} <small>${spec.range}米</small></button><button data-action="enable" data-unit="${escapeHTML(u.id)}" data-weapon="${id}" aria-pressed="${!!w.enabled}" ${!w.ammoId?'disabled':''}>${w.enabled?'已启用':'启用'}</button></div><div class="ammo-options">${ammoOptions}</div><p class="weapon-state">${escapeHTML(reason)}${id==='m2'?` · 当前箱 ${w.magazine??0}/30`:''}</p></div>`;
  }
  function baitStatusHTML(s) {
    return`<div class="bait-status" data-role="bait-status">${(s.baits||[]).slice(-3).reverse().map(b=>{
      const destroyed=b.hp<=0,ended=!b.alive||s.time>=b.endsAt,waiting=s.time<b.startsAt;
      const phase=destroyed?'destroyed':ended?'expired':waiting?'waiting':'active';
      const label=destroyed?'已损毁':ended?'时限结束':waiting?'等待启动':'正在发声';
      const seconds=destroyed||ended?0:Math.ceil(Math.max(0,(waiting?b.startsAt:b.endsAt)-s.time));
      return`<div class="bait-status-row" data-bait-id="${escapeHTML(b.id)}" data-state="${phase}"><span>诱饵${escapeHTML(b.id.replace(/^bait-/,''))}</span><b>${label}</b><span>剩余${seconds}秒</span><span>耐久${fmt(b.hp)}</span></div>`;
    }).join('')}</div>`;
  }
  function renderHUD() {
    const s=snapshot();if(!s)return;
    const group=selected(),u=group[0];
    ui.querySelector('[data-action="preset"]').value=s.preset||'interaction';
    dom('selection-count').textContent=group.length?`选中 ${group.length}`:'未选择';
    const roster=units().map(unit=>`<button class="unit-card ${state.selectedIds.includes(unit.id)?'selected':''} ${!isAlive(unit)?'destroyed':''}" data-action="unit" data-unit="${escapeHTML(unit.id)}" aria-pressed="${state.selectedIds.includes(unit.id)}" ${!isAlive(unit)||!allowed(unit)?'disabled':''}><span class="unit-domain">${unit.domain==='ground'?'地':'空'}</span><span><b>${NAMES[unit.kind]||escapeHTML(unit.kind)}</b><small>${!isAlive(unit)?'已毁伤':STATE_NAMES[unit.moveState]||'待命'} · ${escapeHTML(unit.id)}</small></span><span class="unit-health">${fmt(unit.hp)}</span></button>`).join('');
    const counts=s.counts||{};dom('counts').textContent=`丧尸 ${counts.alive??counts.zombiesAlive??(s.zombies||[]).filter(z=>z.alive).length} · 部队 ${units().filter(isAlive).length} · ${fmt(s.time,1)}秒`;
    const homes=counts.byHome||counts.byZone||{};dom('origins').textContent='原属 '+['A','B','C'].map(id=>`${id} ${homes[id]?.alive??0}/${homes[id]?.total??0}`).join(' · ');
    ui.querySelectorAll('[data-action="filter"]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.value===state.filter)));
    const follow=ui.querySelector('[data-action="follow"]');follow.setAttribute('aria-pressed',String(!!state.followId));follow.textContent=state.followId?'跟随 开':'跟随 关';follow.disabled=group.length!==1;
    dom('view-width').textContent=`视野 ${fmt(state.viewWidth)}米`;
    let detail='<div class="empty-selection"><span class="empty-cross">＋</span><h2>选择部队</h2><p>在战场点选、拖框，或点击部队卡片</p><p>选择与下令不会移动镜头</p></div>';
    if(u){
      const height=u.position.y-finite(u.groundY),health=clamp(u.hp/u.maxHp*100,0,100);
      detail=`<div class="panel-eyebrow">${group.length>1?`混合指挥 · ${group.length}单位`:'单位指挥'}</div><div class="unit-heading"><h2>${NAMES[u.kind]||escapeHTML(u.kind)}</h2><span>${u.domain==='ground'?'地面':'空中样本'}</span></div><div class="health-line"><span style="width:${health}%"></span></div><div class="unit-facts"><span>耐久 <b>${fmt(u.hp)}/${fmt(u.maxHp)}</b></span><span>${u.domain==='ground'?'状态':'离地'} <b>${u.domain==='ground'?(STATE_NAMES[u.moveState]||'待命'):`${fmt(height,1)}米`}</b></span></div>`;
      if(group.length>1)detail+='<p class="selection-note">移动、停止与警戒作用于选中部队；武器面板操作首个单位。</p>';
      if(u.domain==='ground')detail+=`${weaponHTML(u,'main')}${weaponHTML(u,'m2')}<div class="command-row"><button data-action="attack-ground" aria-pressed="${state.commandMode==='attackGround'}">攻击地面</button><button data-action="guard" aria-pressed="${!!u.guardEnabled}">警戒 ${u.guardEnabled?'开':'关'}</button><button data-action="stop">停止</button></div><p class="selection-note">主炮共用炮身与装填，M2独立。范围线为地面投影，伤害按三维距离结算。</p>`;
      else if(u.kind==='heli')detail+=`<div class="command-row"><button data-action="altitude" data-mode="cruise" aria-pressed="${u.altitudeMode==='cruise'}">巡航高度</button><button data-action="altitude" data-mode="work" aria-pressed="${u.altitudeMode==='work'}">作业高度</button><button data-action="stop">停止</button></div><p class="selection-note">只验移动与高度；本批未接空中武器。</p>`;
      else detail+='<p class="selection-note">持续飞行 · 到点盘旋<br>本批只验移动、选择与跟随。</p>';
    }
    detail+='<div class="sample-tools"><button data-action="bait" aria-pressed="'+(state.commandMode==='bait')+'">放置受控诱饵</button><span>5秒延时 · 发声30秒</span></div>'+baitStatusHTML(s);
    const signature=roster+'|'+detail;
    if(signature!==lastHUD&&!ui.querySelector('input:focus')){
      const focused=ui.contains(document.activeElement)?document.activeElement:null;
      const attributes=focused?.dataset?Object.entries(focused.dataset):[];
      dom('roster').innerHTML=roster;dom('detail').innerHTML=detail;lastHUD=signature;
      if(focused&&!focused.isConnected&&attributes.length){
        const match=[...ui.querySelectorAll('[data-action]')].find(node=>attributes.every(([key,value])=>node.dataset[key]===value));
        match?.focus({preventScroll:true});
      }
    }
    const audio=presentation.getAudioState?.();const audioButton=ui.querySelector('[data-action="audio"]');audioButton.disabled=!presentation.setAudioEnabled;
    if(audio){audioButton.textContent=`声音 ${audio.enabled?'开':'关'}`;audioButton.setAttribute('aria-pressed',String(!!audio.enabled));}
  }
  async function handleUI(event) {
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
    const action=button.dataset.action,u=units().find(v=>v.id===button.dataset.unit)||selected()[0];
    if(action==='unit')selectCandidates([u.id],event.shiftKey);
    else if(action==='filter'){state.filter=button.dataset.value;setSelection(state.selectedIds);overlapIds=[];}
    else if(action==='focus')focusSelection();
    else if(action==='follow'){if(state.followId){state.followId=null;hint('已关闭持续跟随');}else if(selected().length===1){const unit=selected()[0];state.followId=unit.id;followOffset={x:0,y:0,z:0};state.focus={...visibleCenter(unit)};cameraUpdate();hint('持续跟随已开启；手动平移立即退出');}}
    else if(action==='preview')previewWeapon=previewWeapon===button.dataset.weapon?null:button.dataset.weapon;
    else if(action==='ammo'&&u){previewWeapon=button.dataset.weapon;if(issue('selectAmmo',{weaponId:button.dataset.weapon,ammoId:button.dataset.ammo},[u]))hint('弹种已选择；启用由玩家单独控制');}
    else if(action==='enable'&&u)issue('enableWeapon',{weaponId:button.dataset.weapon,enabled:!u.weapons?.[button.dataset.weapon]?.enabled},[u]);
    else if(action==='guard'&&u)issue('guard',{enabled:!u.guardEnabled},selected().filter(v=>v.domain==='ground'));
    else if(action==='stop')issue('stop');
    else if(action==='altitude')issue('altitude',{mode:button.dataset.mode},selected().filter(v=>v.kind==='heli'));
    else if(action==='attack-ground'){if(!selected().some(v=>v.domain==='ground'))hint('请选择地面战斗单位',true);else setMode(state.commandMode==='attackGround'?null:'attackGround');}
    else if(action==='bait')setMode(state.commandMode==='bait'?null:'bait');
    else if(action==='reset'){const preset=ui.querySelector('[data-action="preset"]').value;ui.dispatchEvent(new CustomEvent('scenario-request',{bubbles:true,detail:{preset,seed:config.seed}}));hint('已请求重置当前样本');}
    else if(action==='audio'){try{const enabled=!(presentation.getAudioState?.().enabled);await presentation.setAudioEnabled(enabled);hint(enabled?'声音已开启；玩法声源独立计算':'声音已关闭；玩法声源仍正常计算');}catch(error){hint(`声音开启失败：${error.message}`,true);}}
    hudClock=Infinity;
  }
  on(ui,'click',handleUI);
  on(ui,'change',event=>{const input=event.target;if(input.dataset.action==='preset')ui.dispatchEvent(new CustomEvent('scenario-request',{bubbles:true,detail:{preset:input.value,seed:config.seed}}));});
  on(ui,'input',event=>{if(event.target.dataset.action==='volume'){const volume=Number(event.target.value)/100;presentation.setAudioVolume?.(volume);dom('volume').textContent=`${Math.round(volume*100)}%`;}});
  on(labels,'click',event=>{const button=event.target.closest('[data-unit-id]');if(button){selectCandidates([button.dataset.unitId],event.shiftKey);hint('已按机身标签选择单位');}});
  on(canvas,'contextmenu',event=>event.preventDefault());
  on(canvas,'pointerdown',event=>{
    if(event.button!==0&&event.button!==1&&event.button!==2)return;event.preventDefault();canvas.focus({preventScroll:true});lastPointer=pointerLocal(event);
    if(event.button===2){setMode(null);rightCommand(lastPointer);return;}
    if(event.button===0&&state.commandMode){commitPoint(lastPointer);return;}
    drag={button:event.button,start:{...lastPointer},last:{...lastPointer},moved:false,shift:event.shiftKey};
    if(event.button===1)state.followId=null;canvas.setPointerCapture?.(event.pointerId);
  });
  on(canvas,'pointermove',event=>{
    const point=pointerLocal(event);lastPointer=point;
    if(drag){const dx=point.x-drag.last.x,dy=point.y-drag.last.y;drag.moved ||= Math.hypot(point.x-drag.start.x,point.y-drag.start.y)>4;
      if(drag.button===1){const scale=state.viewWidth/canvas.getBoundingClientRect().width;pan((-dx*Math.cos(azimuth)-dy*Math.sin(azimuth)/Math.sin(config.camera.tilt))*scale,(dx*Math.sin(azimuth)-dy*Math.cos(azimuth)/Math.sin(config.camera.tilt))*scale);cameraUpdate();}
      else if(drag.moved){selectionBox.hidden=false;Object.assign(selectionBox.style,{left:`${Math.min(point.x,drag.start.x)}px`,top:`${Math.min(point.y,drag.start.y)}px`,width:`${Math.abs(point.x-drag.start.x)}px`,height:`${Math.abs(point.y-drag.start.y)}px`});}
      drag.last=point;
    }else{state.hoverId=pickUnits(point)[0]||null;canvas.classList.toggle('unit-hover',!!state.hoverId);}
  });
  on(canvas,'pointerup',event=>{
    if(!drag)return;const active=drag;drag=null;selectionBox.hidden=true;canvas.releasePointerCapture?.(event.pointerId);if(active.button!==0)return;
    const point=pointerLocal(event);
    if(active.moved){const minX=Math.min(point.x,active.start.x),maxX=Math.max(point.x,active.start.x),minY=Math.min(point.y,active.start.y),maxY=Math.max(point.y,active.start.y);
      const ids=units().filter(u=>isAlive(u)&&allowed(u)).filter(u=>{const p=bodyProjection(u);return p.visible&&p.x>=minX&&p.x<=maxX&&p.y>=minY&&p.y<=maxY;}).map(u=>u.id);selectCandidates(ids,active.shift);overlapIds=[];
    }else{overlapIds=pickUnits(point);overlapIndex=0;selectCandidates(overlapIds.slice(0,1),active.shift);if(overlapIds.length>1)hint(`重叠 ${overlapIds.length}个单位：按Tab循环`);}
  });
  on(canvas,'pointercancel',()=>{drag=null;selectionBox.hidden=true;});
  on(canvas,'wheel',event=>{
    event.preventDefault();const point=pointerLocal(event),before=groundAt(point);state.viewWidth=clamp(state.viewWidth*Math.exp(clamp(event.deltaY,-300,300)*.0015),config.camera.minWidth,config.camera.maxWidth);cameraUpdate();
    const after=groundAt(point);if(before.status==='ready'&&after.status==='ready'){
      const dx=before.point.x-after.point.x,dz=before.point.z-after.point.z;
      state.focus.x+=dx;state.focus.z+=dz;
      if(state.followId){followOffset.x+=dx;followOffset.z+=dz;}
      cameraUpdate();
    }hudClock=Infinity;
  },{passive:false});
  on(window,'keydown',event=>{
    if(event.code==='Escape'){setMode(null);drag=null;selectionBox.hidden=true;hint('已取消落点操作');return;}
    const inUI=ui.contains(event.target)||editable(event.target);
    if(inUI)return;
    if(['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE'].includes(event.code)){event.preventDefault();keys.add(event.code);}
    if(event.code==='KeyF'&&!event.repeat){event.preventDefault();focusSelection();}
    if(event.code==='Tab'){event.preventDefault();if(lastPointer){const candidates=pickUnits(lastPointer);if(candidates.join('|')!==overlapIds.join('|')){overlapIds=candidates;overlapIndex=-1;}}if(overlapIds.length){overlapIndex=(overlapIndex+(event.shiftKey?-1:1)+overlapIds.length)%overlapIds.length;setSelection([overlapIds[overlapIndex]]);hint(`重叠循环 ${overlapIndex+1}/${overlapIds.length}`);}}
  });
  on(window,'keyup',event=>keys.delete(event.code));
  on(window,'blur',()=>{keys.clear();drag=null;selectionBox.hidden=true;});
  on(ui,'focusin',()=>keys.clear());
  on(window,'resize',cameraUpdate);
  canvas.tabIndex=0;canvas.setAttribute('aria-label','三维战场：左键选择，右键下令');cameraUpdate();renderHUD();
  function update(dt) {
    if(disposed)return;const s=snapshot();if(!s)return;
    if(s.runId!==runId){runId=s.runId;commandId=0;state.selectedIds=[];state.followId=null;state.hoverId=null;overlapIds=[];setMode(null);markerPoint=null;lastHUD='';hudClock=Infinity;hint('本轮已就绪；请手动选择单位、武器和弹种');}
    state.selectedIds=state.selectedIds.filter(id=>units().some(u=>u.id===id&&isAlive(u)));
    const delta=clamp(finite(dt),0,.1),right=Number(keys.has('KeyD'))-Number(keys.has('KeyA')),forward=Number(keys.has('KeyW'))-Number(keys.has('KeyS'));
    if(right||forward){const length=Math.hypot(right,forward),speed=state.viewWidth*.55*delta/length;pan((right*Math.cos(azimuth)-forward*Math.sin(azimuth))*speed,(-right*Math.sin(azimuth)-forward*Math.cos(azimuth))*speed);}
    azimuth+=(Number(keys.has('KeyE'))-Number(keys.has('KeyQ')))*delta*.85;
    if(state.followId){const u=units().find(v=>v.id===state.followId&&isAlive(v));if(u){const center=visibleCenter(u);state.focus={x:center.x+followOffset.x,y:center.y+followOffset.y,z:center.z+followOffset.z};}else state.followId=null;}
    cameraUpdate();syncLabels();syncOverlay();if(performance.now()>hintUntil)dom('notice').hidden=true;hudClock+=delta;if(hudClock>=.1){hudClock=0;renderHUD();}
  }
  return{update,getState:()=>({...state,selectedIds:[...state.selectedIds],focus:{...state.focus},yaw:azimuth}),setSelection,focusSelection,setView,
    dispose(){if(disposed)return;disposed=true;for(const remove of removers)remove();for(const node of[labels,overlay,selectionBox,marker])node.remove();ui.replaceChildren();keys.clear();}};
}
