/** 复用冻结角色加载和分配，适配战斗生命状态及程序倒地；不修改独立群体实验。 */
import * as THREE from 'three';
import {Actors} from '/crowd/角色.mjs';
import {Battlefield} from './场景.mjs';
import {COMBAT} from './防线配置.mjs';
import {BatchedActors} from './尸群合批.mjs';

class BattleActors extends Actors{
  rebuild(agents,kind,seed){for(const o of this.items)o.deathMaterial?.dispose();super.rebuild(agents.map(a=>({...a,id:a.index})),kind,seed);for(const o of this.items){o.weight=0;o.walk.setEffectiveWeight(0);o.idle.setEffectiveWeight(1);o.mixer.update(0);}this.serial=null;}
  update(sim,dt,view){
    if(this.serial!==sim.waveSerial){this.rebuild(sim.targets,sim.options.modelMode,sim.options.seed+sim.wave*313);this.serial=sim.waveSerial;sim.setProfiles(this.assignments,this.assets.map(a=>a.model.coordinate_system?.rest_height_m??1.75));}
    let animated=0,visible=0,corpses=0;const dead=sim.targets.filter(a=>a.hp<=0&&!a.leaked&&sim.time-a.deathAt<3).sort((a,b)=>b.deathAt-a.deathAt).slice(0,80),shown=new Set(dead.map(a=>a.id));
    for(let i=0;i<this.items.length;i++){
      const a=sim.targets[i],o=this.items[i];o.root.position.set(a.x,0,a.z);o.root.rotation.set(0,a.heading,0);o.root.visible=a.hp>0||shown.has(a.id);
      if(a.hp>0){
        const speed=Math.hypot(a.vx,a.vz),weight=Math.min(1,speed/.55);o.weight+=(weight-o.weight)*(1-Math.exp(-dt*8));o.walk.setEffectiveWeight(o.weight);o.idle.setEffectiveWeight(1-o.weight);o.walk.setEffectiveTimeScale(Math.max(.25,speed/1.2));
        if(dt>0){o.mixer.update(dt);animated++;}
        if(view.project({x:a.x,y:a.y,z:a.z}).visible)visible++;
      }else if(o.root.visible){
        if(!o.deathMaterial){o.deathMaterial=o.mesh.material.clone();o.deathMaterial.transparent=true;o.deathMaterial.depthWrite=false;o.mesh.material=o.deathMaterial;}
        const age=sim.time-a.deathAt;o.root.rotation.z=Math.min(1,age/.35)*Math.PI/2;o.root.position.y=.12;o.deathMaterial.opacity=Math.min(1,Math.max(0,(3-age)/.6));corpses++;
      }
    }
    this.animated=animated;this.visible=visible;this.corpses=corpses;
  }
}

export class DefenseBattlefield extends Battlefield{
  async loadActors(manifest,progress,sim){progress('正在准备十三款丧尸与两套动作…');this.actors=new (new URLSearchParams(location.search).get('renderer')==='legacy'?BattleActors:BatchedActors)(this.scene);await this.actors.load(this.renderer,manifest.actors);await this.actors.prepare?.(sim,this,progress);}
  setup(sim){this.sim=sim;if(this.actors)this.actors.serial=null;super.setup(sim);if(this.defenseDecor){this.defenseDecor.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});this.defenseDecor.removeFromParent();}
    this.defenseDecor=new THREE.Group();this.scene.add(this.defenseDecor);
    const add=(geometry,material)=>{const m=new THREE.Mesh(geometry,material);this.defenseDecor.add(m);return m;};
    const zone=add(new THREE.PlaneGeometry(300,16),new THREE.MeshBasicMaterial({color:0xa66b3b,transparent:true,opacity:.18,depthWrite:false}));zone.rotation.x=-Math.PI/2;zone.position.set(0,.045,COMBAT.line+8);
    const line=add(new THREE.PlaneGeometry(300,1),new THREE.MeshBasicMaterial({color:0xefbd75,side:THREE.DoubleSide}));line.rotation.x=-Math.PI/2;line.position.set(0,.06,COMBAT.line);
    for(const x of [-42,42]){const strip=add(new THREE.PlaneGeometry(24,160),new THREE.MeshBasicMaterial({color:0xb3c698,transparent:true,opacity:.08,depthWrite:false}));strip.rotation.x=-Math.PI/2;strip.position.set(x,.04,5);}
    this.guardRings=[];for(const u of sim.units.filter(u=>u.kind!=='plane')){const ring=add(new THREE.RingGeometry(.995,1,96),new THREE.MeshBasicMaterial({color:0xd7e997,transparent:true,opacity:.32,side:THREE.DoubleSide,depthWrite:false}));ring.rotation.x=-Math.PI/2;this.guardRings.push({u,ring});}
    this.enemyRing=add(new THREE.RingGeometry(.45,.65,24),new THREE.MeshBasicMaterial({color:0xf6a17e,side:THREE.DoubleSide,depthTest:false}));this.enemyRing.rotation.x=-Math.PI/2;this.enemyRing.visible=false;
    if(!this.attackLabel){this.attackLabel=document.createElement('div');this.attackLabel.className='target-label attack-focus';this.labels.append(this.attackLabel);}
    this.sync(sim,new Set(),null,0);
  }
  pick(x,y){const base=super.pick(x,y);if(base?.type==='unit')return base;let best=9,target=null;for(const a of this.sim.targets){if(a.hp<=0)continue;const p=this.project(a),distance=Math.hypot(p.x-x,p.y-y);if(p.visible&&distance<best&&!this.sim.nav.blocked(this.camera.position,a)){best=distance;target=a;}}
    if(target){this.inspected=target.id;return{type:'target',id:target.id};}return base;
  }
  sync(sim,selected,focus,dt){super.sync(sim,selected,focus,dt);if(!this.actors)return;
    this.actors.update(sim,sim.paused||!sim.started||sim.result?0:dt,this);
    for(const {u,ring} of this.guardRings??[]){ring.position.set(u.x,.11,u.z);ring.scale.setScalar(sim.weapon(u).range);ring.visible=selected.has(u.id)&&sim.guard&&u.guard;}
    const unit=sim.units.find(u=>u.id===focus)??sim.units.find(u=>selected.has(u.id));
    const t=sim.target(unit?.job?.target??unit?.guardTarget??this.inspected);if(this.enemyRing){this.enemyRing.visible=!!t;if(t)this.enemyRing.position.set(t.x,.12,t.z);}
    if(this.attackLabel){const p=t?this.project({x:t.x,y:t.h+1,z:t.z}):null;this.attackLabel.hidden=!p?.visible;if(t&&p?.visible){this.attackLabel.style.left=p.x+'px';this.attackLabel.style.top=p.y+'px';const text=`目标 ${Math.ceil(t.hp)} / ${t.maxHp}${unit?' · '+unit.state:''}`;if(this.attackLabel.textContent!==text)this.attackLabel.textContent=text;}}
  }
  event(e){if(e.type==='death')this.effect(e.position,.18,.7,0xc4b1a0);else super.event(e);}
  minimap(canvas,sim,selected){super.minimap(canvas,sim,selected);const c=canvas.getContext('2d'),b=sim.map.bounds;c.strokeStyle='#ffc27b';c.lineWidth=2;const y=(COMBAT.line-b.minZ)/(b.maxZ-b.minZ)*canvas.height;c.beginPath();c.moveTo((-150-b.minX)/(b.maxX-b.minX)*canvas.width,y);c.lineTo((150-b.minX)/(b.maxX-b.minX)*canvas.width,y);c.stroke();}
}
