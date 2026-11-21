/** DOM输入到规则命令；界面与战场监听分离，避免透传。 */
export class Commander {
  constructor({field,view,sim,onChange,toast,togglePause}){
    Object.assign(this,{field,view,sim,onChange,toast,togglePause});this.selected=new Set();this.focus=null;this.mode=null;this.keys=new Set();this.drag=null;this.controlGroups=new Map();this.lastClick=null;
    field.addEventListener('contextmenu',e=>e.preventDefault());
    field.addEventListener('pointerdown',e=>this.down(e));field.addEventListener('pointermove',e=>this.move(e));field.addEventListener('pointerup',e=>this.up(e));
    field.addEventListener('pointercancel',()=>this.clearTransient());
    field.addEventListener('wheel',e=>{
      e.preventDefault();const unit=e.deltaMode===1?16:e.deltaMode===2?view.height:1;
      const delta=Math.max(-350,Math.min(350,e.deltaY*unit));view.zoom(Math.exp(delta*.001),this.local(e));
    },{passive:false});
    window.addEventListener('keydown',e=>this.keyDown(e));window.addEventListener('keyup',e=>this.keys.delete(e.code));window.addEventListener('blur',()=>this.clearTransient());
    document.querySelectorAll('[data-group]').forEach(b=>b.addEventListener('click',()=>this.group(b.dataset.group)));
    document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>this.setMode(b.dataset.mode)));
    document.querySelectorAll('[data-weapon]').forEach(b=>b.addEventListener('click',()=>{
      const ids=this.ids(),count=sim.units.filter(u=>ids.includes(u.id)&&u.kind==='heli').length;
      sim.setWeapon(ids,b.dataset.weapon);toast(count?`${count} 架直升机切换至${({gun:'机炮手选',missile:'导弹手选',auto:'自动择优'})[b.dataset.weapon]}`:'先选择直升机');onChange();
    }));
    document.querySelectorAll('[data-enable]').forEach(b=>b.addEventListener('click',()=>{const ids=this.ids(),units=sim.units.filter(u=>ids.includes(u.id)&&u.kind==='heli'),key=b.dataset.enable;if(!units.length)return;sim.setWeaponEnabled?.(ids,key,!units.every(u=>u.weaponEnabled[key]));onChange();}));
    document.querySelector('#stop').addEventListener('click',()=>this.stop());
    document.querySelector('#focus').addEventListener('click',()=>this.focusSelection());
    document.querySelector('#minimap').addEventListener('pointerdown',e=>{const r=e.currentTarget.getBoundingClientRect(),b=sim.map.bounds;view.focusAt({x:b.minX+(e.clientX-r.left)/r.width*(b.maxX-b.minX),z:b.minZ+(e.clientY-r.top)/r.height*(b.maxZ-b.minZ)});});
  }
  ids(){return this.focus?[this.focus]:[...this.selected];}
  local(e){const r=this.field.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};}
  group(kind){const units=this.sim.units.filter(u=>kind==='all'||u.kind===kind);if(!units.length){this.toast('当前地图没有此类单位');return;}this.selected=new Set(units.map(u=>u.id));this.focus=null;this.onChange();}
  choose(id,append=false){if(!append)this.selected.clear();if(append&&this.selected.has(id))this.selected.delete(id);else this.selected.add(id);this.focus=null;this.onChange();}
  setMode(mode){
    if(!this.selected.size&&this.mode!==mode){this.toast('先选择需要接收命令的单位');return;}
    if(mode==='reverse'&&!this.sim.units.some(u=>this.ids().includes(u.id)&&u.kind==='tank')){this.toast('倒车只适用于坦克，当前接收者为 0');return;}
    if(['strafe','bomb'].includes(mode)&&!this.sim.units.some(u=>this.ids().includes(u.id)&&u.kind==='plane')){this.toast('此命令只适用于固定翼，请按 4 选择');return;}
    if(['ground','attackMove'].includes(mode)&&!this.sim.units.some(u=>this.ids().includes(u.id)&&u.kind!=='plane')){this.toast('固定翼请使用右键目标、K扫射或B轰炸');return;}
    this.mode=this.mode===mode?null:mode;
    const banner=document.querySelector('#mode-banner');banner.hidden=!this.mode;
    banner.textContent=({attackMove:'攻击移动：左键选择终点 · 固定翼不接收',ground:'攻击地面：左键选择落点 · 单轮',reverse:'坦克倒车：左键选择终点',strafe:'固定翼扫射：左键拖出至少60米航线，起点 → 终点',bomb:'普通轰炸：左键拖出至少60米航线，中点为落弹区'})[this.mode]??'';
    if(this.mode)banner.textContent+=' · Esc 退出';
    if(!this.mode){const drag=this.drag;this.drag=null;if(drag&&this.field.hasPointerCapture(drag.pointer))this.field.releasePointerCapture(drag.pointer);this.view.showPreview(null,null);}this.onChange();
  }
  clearTransient(){const drag=this.drag;this.keys.clear();this.drag=null;if(drag&&this.field.hasPointerCapture(drag.pointer))this.field.releasePointerCapture(drag.pointer);this.view.showCommandPreview?.([]);this.view.stopCamera();document.querySelector('#drag-box').hidden=true;if(this.mode)this.setMode(this.mode);}
  focusSelection(){
    const units=this.sim.units.filter(u=>this.ids().includes(u.id));
    if(!units.length){this.toast('先选择需要聚焦的单位');return;}
    const mid=axis=>(Math.min(...units.map(u=>u[axis]))+Math.max(...units.map(u=>u[axis])))/2;
    const height=mid('y')+2.3;
    this.view.focusAt({x:mid('x')-Math.sin(this.view.angle)*140/165*height,z:mid('z')-Math.cos(this.view.angle)*140/165*height});
    this.toast(`聚焦 ${units.length} 个单位 · 镜头保持在此位置，不自动跟随`);
  }
  stop(){this.sim.cancel(this.ids());this.toast(`已取消 ${this.ids().length} 个单位的任务与队列；固定翼继续盘旋，已发弹丸继续飞行${this.sim.defense?'；开启的警戒仍生效，停火请关闭警戒':''}`);this.onChange();}
  issue(command,append){
    if(!this.ids().length){this.toast('先选择载具，再下达命令');return;}
    const count=this.sim.command(this.ids(),command,append);
    const name=({move:'移动',attack:'指定攻击',attackMove:'攻击移动',ground:'攻击地面一轮',reverse:'倒车',strafe:'单次扫射航线',bomb:'单次轰炸航线'})[command.type];
    if(count)this.view.commandFeedback?.(command.point??this.sim.target(command.target),command.type);
    this.toast(count?`${count} 个单位${append?'已排队':'接收'}${name}命令${command.type==='reverse'?' · 仅坦克':''}${this.sim.paused?' · 恢复后执行':''}`:'命令未提交：目的地不可达或没有适用载具');this.onChange();
  }
  down(e){
    if(e.target.closest('button,a,input,select,summary'))return;
    this.field.focus({preventScroll:true});const p=this.local(e);
    if(e.button===2){e.preventDefault();if(this.mode){this.setMode(this.mode);return;}const hit=this.view.pick(p.x,p.y),point=this.view.ground(p.x,p.y);
      this.drag={button:2,start:p,last:p,world:point,hit,append:e.shiftKey,pointer:e.pointerId};this.field.setPointerCapture(e.pointerId);this.previewMove(p);return;
    }
    if(e.button===0&&['strafe','bomb'].includes(this.mode)){const start=this.view.ground(p.x,p.y);if(start){this.drag={button:0,start:p,last:p,world:start,run:this.mode,append:e.shiftKey,pointer:e.pointerId};this.field.setPointerCapture(e.pointerId);e.preventDefault();}return;}
    if(e.button===0&&this.mode){const point=this.view.ground(p.x,p.y);if(point){this.issue({type:this.mode,point},e.shiftKey);if(!e.shiftKey)this.setMode(this.mode);}return;}
    if(e.button!==0&&e.button!==1)return;
    this.drag={button:e.button,start:p,last:p,append:e.shiftKey,pointer:e.pointerId};this.field.setPointerCapture(e.pointerId);e.preventDefault();
  }
  move(e){
    const p=this.local(e);
    this.updateCursor?.(p,e);
    if(this.mode)this.view.showPreview(this.view.ground(p.x,p.y),this.mode,this.drag?.world);
    if(!this.drag)return;
    const d=this.drag;if(d.button===2){d.last=p;this.previewMove(p);return;}if(d.run){d.last=p;return;}if(d.button===1)this.view.rotate((p.x-d.last.x)*.004);
    else if(Math.hypot(p.x-d.start.x,p.y-d.start.y)>5){const box=document.querySelector('#drag-box');box.hidden=false;Object.assign(box.style,{left:Math.min(p.x,d.start.x)+'px',top:Math.min(p.y,d.start.y)+'px',width:Math.abs(p.x-d.start.x)+'px',height:Math.abs(p.y-d.start.y)+'px'});}
    d.last=p;
  }
  moveCommand(p){
    const d=this.drag;if(!d?.world)return null;
    if(d.hit?.type==='target'&&this.sim.target(d.hit.id)){const t=this.sim.target(d.hit.id);return{type:'attack',target:d.hit.id,point:{x:t.x,y:t.y,z:t.z}};}
    const end=this.view.ground(p.x,p.y),oriented=end&&Math.hypot(p.x-d.start.x,p.y-d.start.y)>8&&Math.hypot(end.x-d.world.x,end.z-d.world.z)>1;
    return{type:'move',point:d.world,...(oriented?{facing:Math.atan2(d.world.x-end.x,d.world.z-end.z)}:{})};
  }
  previewMove(p){const command=this.moveCommand(p);this.view.showCommandPreview?.(command?this.sim.planCommand(this.ids(),command):[]);}
  up(e){
    if(!this.drag||e.pointerId!==this.drag.pointer)return;
    const d=this.drag,p=this.local(e);this.drag=null;document.querySelector('#drag-box').hidden=true;
    if(this.field.hasPointerCapture(e.pointerId))this.field.releasePointerCapture(e.pointerId);
    if(d.button===2){this.drag=d;const command=this.moveCommand(p);this.drag=null;this.view.showCommandPreview?.([]);if(command)this.issue(command,e.shiftKey||d.append);return;}
    if(d.run){const end=this.view.ground(p.x,p.y);if(end&&Math.hypot(end.x-d.world.x,end.z-d.world.z)>=60&&Math.hypot(p.x-d.start.x,p.y-d.start.y)>=8){this.issue({type:d.run,start:d.world,end,point:{x:(d.world.x+end.x)/2,z:(d.world.z+end.z)/2}},d.append);if(!d.append)this.setMode(this.mode);}else this.toast('航线过短：请先拉远，再拖出至少60米航线');this.view.showPreview(null,null);return;}
    if(d.button!==0)return;
    if(Math.hypot(p.x-d.start.x,p.y-d.start.y)>5){
      if(!d.append)this.selected.clear();for(const u of this.sim.units){const q=this.view.project({x:u.x,y:u.y+2,z:u.z});if(q.x>=Math.min(p.x,d.start.x)&&q.x<=Math.max(p.x,d.start.x)&&q.y>=Math.min(p.y,d.start.y)&&q.y<=Math.max(p.y,d.start.y))this.selected.add(u.id);}this.focus=null;this.onChange();
    }else{const hit=this.view.pick(p.x,p.y);if(hit?.type==='unit'){
      const now=performance.now(),double=this.lastClick?.id===hit.id&&now-this.lastClick.time<350;this.lastClick={id:hit.id,time:now};
      if(double){const unit=this.sim.units.find(u=>u.id===hit.id);if(!d.append)this.selected.clear();for(const u of this.sim.units)if(u.kind===unit.kind&&this.view.project({x:u.x,y:u.y+2,z:u.z}).visible)this.selected.add(u.id);this.focus=null;this.onChange();}
      else this.choose(hit.id,d.append);
    }else if(hit?.type==='target'){const t=this.sim.targets.find(t=>t.id===hit.id);this.toast(`${t.name} · 耐久 ${Math.ceil(t.hp)} / ${t.maxHp}`);}else if(!d.append){this.selected.clear();this.focus=null;this.onChange();}}
  }
  keyDown(e){
    if(e.target.matches('input,select,textarea'))return;
    if(['Space','Tab','KeyW','KeyA','KeyS','KeyD','KeyQ','KeyG','KeyR','KeyE','KeyF','KeyC','KeyK','KeyB','Digit1','Digit2','Digit3','Digit4','Escape'].includes(e.code))e.preventDefault();
    this.keys.add(e.code);if(e.repeat)return;
    if(/^Digit[0-9]$/.test(e.code)&&(e.ctrlKey||e.altKey||e.shiftKey)){
      e.preventDefault();const key=e.code.slice(5);
      if(e.ctrlKey||e.shiftKey){this.controlGroups.set(key,[...this.selected]);this.toast(`已保存编组 ${key} · ${this.selected.size} 个单位；Alt+${key} 调用`);}
      else{const ids=this.controlGroups.get(key);if(ids){this.selected=new Set(ids.filter(id=>this.sim.units.some(u=>u.id===id)));this.focus=null;this.onChange();this.toast(`编组 ${key} · ${this.selected.size} 个单位`);}else this.toast(`编组 ${key} 尚未保存`);}return;
    }
    const modes={KeyQ:'attackMove',KeyG:'ground',KeyR:'reverse',KeyK:'strafe',KeyB:'bomb'};
    if(modes[e.code])this.setMode(modes[e.code]);
    if(e.code==='Escape'){if(this.drag){const d=this.drag;this.drag=null;if(this.field.hasPointerCapture(d.pointer))this.field.releasePointerCapture(d.pointer);this.view.showCommandPreview?.([]);document.querySelector('#drag-box').hidden=true;}if(this.mode)this.setMode(this.mode);}
    if(e.code==='KeyE')this.stop();if(e.code==='Space')this.togglePause();if(e.code==='KeyF')this.view.home();
    if(e.code==='KeyC')this.focusSelection();
    const groups={Digit1:'tank',Digit2:'heli',Digit3:'all',Digit4:'plane'};if(groups[e.code])this.group(groups[e.code]);
    if(e.code==='Tab'&&this.selected.size){const list=[...this.selected];this.focus=list[(list.indexOf(this.focus)+1)%list.length];this.toast(`子选择：${this.sim.units.find(u=>u.id===this.focus).name} · 命令仅发给此单位，按编组键恢复全组`);this.onChange();}
  }
  step(dt){this.view.pan((this.keys.has('KeyD')?1:0)-(this.keys.has('KeyA')?1:0),(this.keys.has('KeyS')?1:0)-(this.keys.has('KeyW')?1:0));this.view.stepCamera(dt);}
}
