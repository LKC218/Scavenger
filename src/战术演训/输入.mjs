/** G01输入只产生移动意图；镜头、选择与未提交预览不修改规则任务。 */
const alive=u=>u&&u.alive!==false&&(u.hp===undefined||u.hp>0);
const controls='button,a,input,select,textarea,summary,[contenteditable="true"],[role="dialog"]';
export class Commander {
  constructor({field,view,sim,onChange,toast,togglePause}){
    Object.assign(this,{field,view,sim,onChange,toast,togglePause});
    this.selected=new Set();this.focus=null;this.mode=null;this.keys=new Set();this.drag=null;this.controlGroups=new Map();this.lastClick=null;
    this.layerFilter='all';this.following=null;this.followOffset={x:0,z:0};this.pointer=null;this.pointerInside=false;this.overlapIds=[];this.overlapIndex=-1;
    field.addEventListener('contextmenu',e=>e.preventDefault());
    field.addEventListener('pointerdown',e=>this.down(e));field.addEventListener('pointermove',e=>this.move(e));field.addEventListener('pointerup',e=>this.up(e));
    field.addEventListener('pointerleave',()=>{this.pointerInside=false;view.setHoveredUnit?.(null);});field.addEventListener('pointercancel',()=>this.clearTransient());
    field.addEventListener('wheel',e=>{const label=e.target.closest?.('[data-unit-id]');if(e.target.closest?.(controls)&&!(label&&view.labels?.contains(label)))return;e.preventDefault();const unit=e.deltaMode===1?16:e.deltaMode===2?view.height:1,delta=Math.max(-350,Math.min(350,e.deltaY*unit));view.zoom(Math.exp(delta*.0015),this.local(e));},{passive:false});
    view.labels?.addEventListener('focusin',e=>view.setHoveredUnit?.(e.target.closest('[data-unit-id]')?.dataset.unitId));
    view.labels?.addEventListener('focusout',()=>view.setHoveredUnit?.(null));
    window.addEventListener('keydown',e=>this.keyDown(e));window.addEventListener('keyup',e=>this.keys.delete(e.code));window.addEventListener('blur',()=>this.clearTransient());
    document.addEventListener('focusin',e=>{if(e.target.closest?.(controls)){this.keys.clear();this.view.pan(0,0);}});
    document.querySelectorAll('[data-group]').forEach(b=>b.addEventListener('click',()=>this.group(b.dataset.group)));
    document.querySelector('#stop')?.addEventListener('click',()=>this.stop());document.querySelector('#focus')?.addEventListener('click',()=>this.focusSelection());
    view.labels?.addEventListener('click',e=>{const label=e.target.closest('[data-unit-id]');if(label){e.stopPropagation();this.choose(label.dataset.unitId,e.shiftKey);}});
    const minimap=document.querySelector('#minimap'),panMap=e=>{const r=minimap.getBoundingClientRect(),b=sim.map.bounds;this.stopFollowing();view.focusAt({x:b.minX+Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*(b.maxX-b.minX),z:b.minZ+Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))*(b.maxZ-b.minZ)});};
    minimap?.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();this.clearPreview();panMap(e);minimap.setPointerCapture(e.pointerId);});
    minimap?.addEventListener('pointermove',e=>{if(minimap.hasPointerCapture(e.pointerId))panMap(e);});
    minimap?.addEventListener('pointerup',e=>{if(minimap.hasPointerCapture(e.pointerId))minimap.releasePointerCapture(e.pointerId);});
  }
  allowed(u){return alive(u)&&(this.layerFilter==='all'||(this.layerFilter==='ground'?u.kind==='tank':u.kind!=='tank'));}
  ids(){return [...this.selected].filter(id=>this.allowed(this.sim.units.find(u=>u.id===id)));}
  local(e){const r=this.field.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};}
  setSelection(ids){this.selected=new Set(ids.filter(id=>this.allowed(this.sim.units.find(u=>u.id===id))));this.focus=null;if(this.following&&!this.selected.has(this.following))this.stopFollowing(false);this.onChange();}
  setLayerFilter(value){if(!['all','ground','air'].includes(value))return;this.clearPreview();this.layerFilter=value;this.view.setLayerFilter?.(value);this.overlapIds=[];this.overlapIndex=-1;this.setSelection([...this.selected]);}
  group(kind){const units=this.sim.units.filter(u=>alive(u)&&(kind==='all'||u.kind===kind));if(!units.length){this.toast('当前地图没有此类单位');return;}this.layerFilter=kind==='all'?'all':kind==='tank'?'ground':'air';this.view.setLayerFilter?.(this.layerFilter);this.clearPreview();this.setSelection(units.map(u=>u.id));}
  choose(id,append=false){if(!this.allowed(this.sim.units.find(u=>u.id===id)))return;this.clearPreview();const next=new Set(append?this.selected:[]);if(append&&next.has(id))next.delete(id);else next.add(id);this.setSelection([...next]);}
  // 保留旧公开入口，本步不接受攻击、倒车及飞行攻击模式。
  setMode(mode){this.mode=null;if(mode)this.toast('G01第一步仅开放右键移动指挥');this.clearPreview();}
  clearPreview(){const d=this.drag;this.drag=null;if(d&&this.field.hasPointerCapture(d.pointer))this.field.releasePointerCapture(d.pointer);this.view.showCommandPreview?.([]);this.view.showPreview?.(null,null);for(const id of ['drag-box','mode-banner']){const node=document.querySelector('#'+id);if(node)node.hidden=true;}}
  clearTransient(){this.clearPreview();this.keys.clear();this.view.stopCamera();this.mode=null;}
  stopFollowing(notify=true){if(!this.following)return;this.following=null;this.followOffset={x:0,z:0};if(notify)this.onChange();}
  focusSelection(){const units=this.sim.units.filter(u=>this.ids().includes(u.id));if(!units.length){this.toast('先选择需要聚焦的单位');return;}this.stopFollowing(false);const points=units.map(u=>this.view.unitFocus(u.id,u)),mid=axis=>points.reduce((n,p)=>n+p[axis],0)/points.length;this.view.focusAt({x:mid('x'),y:mid('y'),z:mid('z')});this.toast(`聚焦 ${units.length} 个单位 · 持续跟随需单独开启`);this.onChange();}
  toggleFollow(){if(this.following){this.stopFollowing();this.toast('已关闭持续跟随');return;}const ids=this.ids();if(ids.length!==1){this.toast('持续跟随需要选择一个单位');return;}this.following=ids[0];this.followOffset={x:0,z:0};this.view.stopCamera();this.followUnit();this.toast('持续跟随已开启 · 手动平移或小地图平移退出');this.onChange();}
  followUnit(){const u=this.sim.units.find(u=>u.id===this.following&&this.allowed(u));if(!u||!this.selected.has(u.id)){this.stopFollowing();return;}const p=this.view.unitFocus(u.id,u);this.view.trackAt({x:p.x+this.followOffset.x,y:p.y,z:p.z+this.followOffset.z});}
  stop(){const ids=this.ids();if(!ids.length){this.toast('先选择需要停止的单位');return;}this.clearPreview();this.sim.cancel(ids);this.toast(`已停止 ${ids.length} 个单位并清空移动队列；固定翼继续盘旋`);this.onChange();}
  issue(command,append){
    const ids=this.ids();if(!ids.length){this.toast('先选择载具，再下达命令');return;}if(command.type!=='move')return;
    const count=this.sim.command(ids,command,append),result=this.sim.lastCommandResult,rows=result?.rows??[],rejected=result?.rejected??Math.max(0,ids.length-count),pending=result?.pending??0;
    const reason=[...new Set(rows.filter(r=>r.status==='rejected').map(r=>r.reason).filter(Boolean))].join('；');
    if(count){for(const row of rows.filter(r=>r.status!=='rejected').slice(0,8))this.view.commandFeedback?.(row.point,'move');if(!rows.length)this.view.commandFeedback?.(command.point,'move');}
    this.toast(`${count?`${count} 个单位${append?'已追加移动队列':'接收移动命令'}`:'命令未提交'}${pending?` · ${pending} 个准备路径`:''}${rejected?` · ${rejected} 个拒绝${reason?'：'+reason:''}`:''}${this.sim.paused?' · 恢复后执行':''}`);this.onChange();
  }
  down(e){
    if(e.target.closest?.(controls)||this.drag)return;this.field.focus({preventScroll:true});const p=this.local(e);this.pointer=p;this.pointerInside=true;
    if(e.button===2){e.preventDefault();if(!this.ids().length){this.toast('先选择载具，再下达移动命令');return;}const point=this.view.ground(p.x,p.y);if(!point){this.toast('落点缺少有效地面支撑，未下令');return;}this.drag={button:2,start:p,last:p,world:point,append:e.shiftKey,pointer:e.pointerId};this.field.setPointerCapture(e.pointerId);this.previewMove(p);return;}
    if(e.button!==0&&e.button!==1)return;if(e.button===1){this.stopFollowing();this.view.stopCamera();}this.drag={button:e.button,start:p,last:p,append:e.shiftKey,pointer:e.pointerId};this.field.setPointerCapture(e.pointerId);e.preventDefault();
  }
  move(e){
    const p=this.local(e);this.pointer=p;this.pointerInside=p.x>=0&&p.y>=0&&p.x<=this.view.width&&p.y<=this.view.height;this.updateCursor?.(p,e);
    const label=e.target.closest?.('[data-unit-id]'),now=performance.now();
    if(label&&this.view.labels.contains(label))this.view.setHoveredUnit?.(label.dataset.unitId);
    else if(!this.drag&&(!this.lastHoverPick||now-this.lastHoverPick>50)){this.lastHoverPick=now;this.view.setHoveredUnit?.(this.view.pickUnits(p.x,p.y,u=>this.allowed(u))[0]);}
    const d=this.drag;if(!d||d.pointer!==e.pointerId)return;
    if(d.button===2){d.last=p;this.previewMove(p);return;}if(d.button===1)this.view.dragPan(p.x-d.last.x,p.y-d.last.y);
    else if(Math.hypot(p.x-d.start.x,p.y-d.start.y)>5){const box=document.querySelector('#drag-box');if(box){box.hidden=false;Object.assign(box.style,{left:Math.min(p.x,d.start.x)+'px',top:Math.min(p.y,d.start.y)+'px',width:Math.abs(p.x-d.start.x)+'px',height:Math.abs(p.y-d.start.y)+'px'});}}d.last=p;
  }
  moveCommand(p){const d=this.drag;if(!d?.world)return null;const end=this.view.ground(p.x,p.y),oriented=end&&Math.hypot(p.x-d.start.x,p.y-d.start.y)>8&&Math.hypot(end.x-d.world.x,end.z-d.world.z)>1;return{type:'move',point:{...d.world},...(oriented?{facing:Math.atan2(d.world.x-end.x,d.world.z-end.z)}:{})};}
  previewMove(p){const command=this.moveCommand(p),rows=command?this.sim.planCommand(this.ids(),command,this.drag?.append??false):[];this.view.showCommandPreview?.(rows,{append:this.drag?.append??false});const banner=document.querySelector('#mode-banner');if(!banner)return;const rejected=rows.filter(r=>r.status==='rejected'||!r.valid),pending=rows.filter(r=>r.status==='pending'),reasons=[...new Set(rejected.map(r=>r.reason).filter(Boolean))];banner.hidden=false;banner.textContent=`右键松开提交${this.drag?.append?'追加队列':'移动'} · 拖动设朝向 · Esc取消预览${pending.length?` · ${pending.length} 个准备路径`:''}${rejected.length?` · ${rejected.length} 个不可达：${reasons.join('；')||'缺少有效路径'}`:''}`;}
  mayCommit(e){const p=this.local(e);if(p.x<0||p.y<0||p.x>this.view.width||p.y>this.view.height)return false;const element=document.elementFromPoint?.(e.clientX,e.clientY);return !element||(this.field.contains(element)&&!element.closest(controls));}
  up(e){
    if(!this.drag||e.pointerId!==this.drag.pointer)return;const d=this.drag,p=this.local(e),commit=this.mayCommit(e),command=d.button===2?this.moveCommand(p):null;this.clearPreview();if(d.button===2){if(command&&commit)this.issue(command,e.shiftKey||d.append);return;}if(d.button!==0||!commit)return;
    if(Math.hypot(p.x-d.start.x,p.y-d.start.y)>5){const minX=Math.min(p.x,d.start.x),maxX=Math.max(p.x,d.start.x),minY=Math.min(p.y,d.start.y),maxY=Math.max(p.y,d.start.y);const ids=this.sim.units.filter(u=>this.allowed(u)).filter(u=>{const q=this.view.projectUnit(u.id);return q.visible&&q.x>=minX&&q.x<=maxX&&q.y>=minY&&q.y<=maxY;}).map(u=>u.id);this.setSelection(d.append?[...new Set([...this.selected,...ids])]:ids);this.overlapIds=[];return;}
    const candidates=this.view.pickUnits(p.x,p.y,u=>this.allowed(u));this.overlapIds=candidates;this.overlapIndex=0;
    if(candidates.length){const id=candidates[0],now=performance.now(),double=this.lastClick?.id===id&&now-this.lastClick.time<350;this.lastClick={id,time:now};if(double){const kind=this.sim.units.find(u=>u.id===id)?.kind,ids=this.sim.units.filter(u=>this.allowed(u)&&u.kind===kind&&this.view.projectUnit(u.id).visible).map(u=>u.id);this.setSelection(d.append?[...new Set([...this.selected,...ids])]:ids);}else this.choose(id,d.append);if(candidates.length>1)this.toast(`重叠 ${candidates.length} 个单位 · 鼠标留在此处按Tab循环`);}else if(!d.append)this.setSelection([]);
  }
  keyDown(e){
    if(e.ctrlKey||e.metaKey)return;
    if(e.code==='Escape'){if(this.drag){e.preventDefault();this.clearPreview();this.toast('已取消未提交预览，已提交的任务保持执行');}return;}
    if(e.target.closest?.(controls)||(!this.field.contains(e.target)&&e.target!==document.body))return;
    if(['Space','Tab','KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE','KeyF','KeyX','Digit1','Digit2','Digit3','Digit4'].includes(e.code))e.preventDefault();if(['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE'].includes(e.code))this.keys.add(e.code);if(e.repeat&&e.code!=='Tab')return;
    if(/^Digit[0-9]$/.test(e.code)&&(e.altKey||e.shiftKey)){e.preventDefault();const key=e.code.slice(5);if(e.shiftKey){this.controlGroups.set(key,this.ids());this.toast(`已保存编组 ${key} · ${this.ids().length} 个单位；Alt+${key} 调用`);}else{const ids=this.controlGroups.get(key);if(ids){const units=this.sim.units.filter(u=>ids.includes(u.id)&&alive(u)),ground=units.some(u=>u.kind==='tank'),air=units.some(u=>u.kind!=='tank');this.layerFilter=ground&&air?'all':ground?'ground':'air';this.view.setLayerFilter?.(this.layerFilter);this.clearPreview();this.setSelection(units.map(u=>u.id));}else this.toast(`编组 ${key} 尚未保存`);}return;}
    if(e.code==='KeyX')this.stop();if(e.code==='Space')this.togglePause();if(e.code==='KeyF')this.focusSelection();const groups={Digit1:'tank',Digit2:'heli',Digit3:'all',Digit4:'plane'};if(groups[e.code])this.group(groups[e.code]);
    if(e.code==='Tab'&&this.pointer&&this.pointerInside){const ids=this.view.pickUnits(this.pointer.x,this.pointer.y,u=>this.allowed(u));if(ids.join('|')!==this.overlapIds.join('|')){this.overlapIds=ids;this.overlapIndex=e.shiftKey?0:-1;}if(ids.length){this.overlapIndex=(this.overlapIndex+(e.shiftKey?-1:1)+ids.length)%ids.length;this.clearPreview();this.setSelection([ids[this.overlapIndex]]);this.toast(`重叠循环 ${this.overlapIndex+1}/${ids.length}`);}}
  }
  step(dt){const x=Number(this.keys.has('KeyD'))-Number(this.keys.has('KeyA')),z=Number(this.keys.has('KeyS'))-Number(this.keys.has('KeyW'));if(x||z)this.stopFollowing();this.view.pan(x,z);const rotation=Number(this.keys.has('KeyE'))-Number(this.keys.has('KeyQ'));if(rotation)this.view.rotate(rotation*Math.min(dt,.1)*.85);const before={x:this.view.center.x,z:this.view.center.z},anchoring=!!this.view.zoomAnchor;this.view.stepCamera(dt);if(this.following){if(anchoring){this.followOffset.x+=this.view.center.x-before.x;this.followOffset.z+=this.view.center.z-before.z;}this.followUnit();}if(this.drag?.button===2)this.previewMove(this.drag.last);}
  afterSync(){if(this.following){this.followUnit();this.view.syncLabels?.(this.sim,this.selected);}}
}
