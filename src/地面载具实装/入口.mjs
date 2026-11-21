import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {clone as cloneSkeleton} from 'three/addons/utils/SkeletonUtils.js';
import {createModelLoader} from '/compressed-loader.mjs';
import {GroundSimulation} from './规则.mjs';

// 独立平地检验场：不替代当前Airbase演训与既有防线。
const $=id=>document.getElementById(id),host=$('viewport');
const renderer=new THREE.WebGLRenderer({antialias:false,powerPreference:'high-performance'});
renderer.setPixelRatio(1);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
host.append(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color(0x394431);
scene.add(new THREE.HemisphereLight(0xf1f0d6,0x667554,2.1));
const sun=new THREE.DirectionalLight(0xffefd5,2.8);sun.position.set(70,160,90);scene.add(sun);
const camera=new THREE.PerspectiveCamera(42,1,.1,1500);camera.position.set(45,105,125);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(-5,0,20);controls.enableDamping=true;controls.maxPolarAngle=Math.PI*.46;controls.minDistance=15;controls.maxDistance=400;
controls.mouseButtons={LEFT:null,MIDDLE:THREE.MOUSE.PAN,RIGHT:null};
const ground=new THREE.Mesh(new THREE.PlaneGeometry(320,280),new THREE.MeshStandardMaterial({color:0x596447,roughness:1}));ground.rotation.x=-Math.PI/2;ground.position.y=-.03;scene.add(ground);
const grid=new THREE.GridHelper(300,30,0x889569,0x657454);grid.position.y=.01;scene.add(grid);
const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2(),plane=new THREE.Plane(new THREE.Vector3(0,1,0),0);
const modelLoader=createModelLoader(renderer),templates=new Map(),visuals=new Map(),targetVisuals=new Map(),pendingLoads=new Set();
const selectionRing=new THREE.Mesh(new THREE.RingGeometry(5.5,5.8,48),new THREE.MeshBasicMaterial({color:0xd7e990,side:THREE.DoubleSide,depthTest:false}));selectionRing.rotation.x=-Math.PI/2;selectionRing.renderOrder=4;selectionRing.visible=false;scene.add(selectionRing);
let manifest,sim,selectedId=null,selectedModel='model:US_M1A2',generation=0,loading=false,last=performance.now(),nextUI=0,stationRoot;
function disposeTree(root){const geometries=new Set(),materials=new Set(),textures=new Set();root.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of o.material?(Array.isArray(o.material)?o.material:[o.material]):[]){materials.add(m);for(const value of Object.values(m))if(value?.isTexture)textures.add(value);}});for(const t of textures)t.dispose();for(const m of materials)m.dispose();for(const g of geometries)g.dispose();}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function current(){return sim?.units.find(u=>u.id===selectedId);}
function result(value,message){if(value?.ok===false){notice(value.reason||sim.lastError||'操作未完成',true);return false;}notice(message);return true;}
function resize(){const {width,height}=host.getBoundingClientRect();renderer.setSize(Math.max(1,Math.round(width*.8)),Math.max(1,Math.round(height*.8)),false);camera.aspect=width/Math.max(1,height);camera.updateProjectionMatrix();}
new ResizeObserver(resize).observe(host);resize();
function labelSprite(text,color='#d7e990'){
  const canvas=document.createElement('canvas');canvas.width=256;canvas.height=64;const ctx=canvas.getContext('2d');ctx.fillStyle='#18251ad9';ctx.fillRect(0,0,256,64);ctx.fillStyle=color;ctx.font='24px Microsoft YaHei';ctx.textAlign='center';ctx.fillText(text,128,41);
  const texture=new THREE.CanvasTexture(canvas),sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false}));sprite.scale.set(20,5,1);sprite.renderOrder=3;return sprite;
}
function setupField(){
  if(stationRoot){scene.remove(stationRoot);disposeTree(stationRoot);}
  stationRoot=new THREE.Group();stationRoot.position.set(sim.station.x,0,sim.station.z);
  const pad=new THREE.Mesh(new THREE.CylinderGeometry(sim.station.radius,sim.station.radius,.12,48),new THREE.MeshBasicMaterial({color:0x527b78,transparent:true,opacity:.5}));stationRoot.add(pad);
  const box=new THREE.Mesh(new THREE.BoxGeometry(8,3,6),new THREE.MeshStandardMaterial({color:0x8a9f72}));box.position.set(0,1.5,0);stationRoot.add(box);
  const label=labelSprite('后方补给站','#a6e4df');label.position.y=8;stationRoot.add(label);scene.add(stationRoot);
  for(const v of targetVisuals.values()){scene.remove(v);disposeTree(v);}targetVisuals.clear();
  for(const t of sim.targets){const root=new THREE.Group();root.position.set(t.x,0,t.z);const body=new THREE.Mesh(new THREE.BoxGeometry(4,4,4),new THREE.MeshStandardMaterial({color:0xd09a75}));body.position.y=2;body.userData.targetId=t.id;root.add(body);const label=labelSprite(t.name||'耐久靶','#ffd6b0');label.position.y=8;root.add(label);scene.add(root);targetVisuals.set(t.id,root);}
}
function renderCatalog(){
  const q=$('search').value.trim().toLowerCase(),filter=$('filter').value;
  $('catalog').replaceChildren();
  for(const row of sim.catalog.filter(r=>(!q||`${r.id} ${r.name}`.toLowerCase().includes(q))&&(filter==='all'||filter==='ready'&&r.deployable||filter==='partial'&&!r.deployable))){
    const button=document.createElement('button');button.className='model-row'+(row.id===selectedModel?' active':'');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected',String(row.id===selectedModel));
    const name=document.createElement('span');name.textContent=row.name;const sub=document.createElement('small');sub.textContent=row.id.replace('model:','');name.append(sub);
    const state=document.createElement('span');state.className='state-chip'+(row.deployable?'':' partial');state.textContent=row.deployable?'可部署':'参考 / 待修';button.append(name,state);button.onclick=()=>{selectedModel=row.id;renderCatalog();renderModel();};$('catalog').append(button);
  }
}
function renderModel(){const row=sim.catalog.find(r=>r.id===selectedModel);$('model-name').textContent=row?.name||'选择型号';$('model-state').textContent=row?.readyStatus||(!row?.deployable?'资源参考或待修，暂不部署。':row.id==='model:US_M1A2'?'共通能力与主炮检验。':row.id==='model:US_M548A3'?'共通能力与物资运输检验。':'共通能力检验；专用玩法待逐车接入。');$('deploy').disabled=loading||!row?.deployable;}
async function template(row){if(!templates.has(row.id)){const promise=modelLoader.loadAsync(row.url||row.model_url).then(gltf=>gltf.scene);templates.set(row.id,promise);promise.catch(()=>templates.delete(row.id));}const model=await templates.get(row.id);for(const [id,promise] of templates){if(templates.size<=8)break;if(id!==row.id&&!sim.units.some(u=>u.modelId===id&&visuals.has(u.id))){templates.delete(id);promise.then(disposeTree);}}return model;}
async function deploy(){
  const row=sim.catalog.find(r=>r.id===selectedModel);if(!row?.deployable||loading)return;loading=true;renderModel();const epoch=generation;
  try{notice('正在准备 '+row.name+'…');const source=await template(row);if(epoch!==generation)return;
    const i=sim.units.filter(u=>u.alive).length,point={x:sim.station.x-15+(i%4)*11,z:sim.station.z-18-Math.floor(i/4)*12};const u=sim.deploy(row.id,point);if(!u){notice(sim.lastError||'部署未完成',true);return;}
    const root=cloneSkeleton(source),box=new THREE.Box3().setFromObject(root),center=box.getCenter(new THREE.Vector3());root.position.set(-center.x,-box.min.y,-center.z);const group=new THREE.Group();group.add(root);
    group.traverse(o=>{if(o.isMesh){o.userData.unitId=u.id;o.frustumCulled=false;}});scene.add(group);visuals.set(u.id,group);selectedId=u.id;notice(row.name+'已部署，先检查移动与状态。');updateUI();
  }catch(error){console.error(error);notice('模型加载失败：'+error.message,true);}finally{loading=false;renderModel();}
}
function updateUI(){
  $('points').textContent=Math.floor(sim.points);$('stock').textContent=sim.stock.toFixed(1);$('force').textContent=`${sim.units.filter(u=>u.alive).length} / 16`;
  const u=current();$('unit-name').textContent=u?.name||sim.catalog.find(r=>r.id===u?.modelId)?.name||'尚未选择单位';$('unit-state').textContent=u?(u.status||'待命'):'部署单位后，可在场景或下方列表选择。';$('selected-kind').textContent=u?(u.alive?'存活':'已损失'):'—';$('selection-title').textContent=u?`${$('unit-name').textContent} · ${u.alive?'已选择':'本局永久损失'}`:'等待选择';
  $('hp').textContent=u?`${u.hp.toFixed(0)} / ${u.maxHp}`:'—';$('ammo').textContent=u?(u.maxAmmo?`${u.ammo.toFixed(0)} / ${u.maxAmmo}`:'无已接入武器'):'—';$('cargo').textContent=u?.capacity?`${u.cargo.toFixed(1)} / ${u.capacity}`:'—';
  for(const id of ['attack','supply','reload','damage'])$(id).disabled=!u?.alive;
  $('attack').disabled=!u?.alive||u.modelId!=='model:US_M1A2';$('supply').disabled=$('reload').disabled=!u?.alive||u.modelId!=='model:US_M548A3';
  const targetValue=$('target').value;$('target').replaceChildren(...sim.targets.filter(t=>t.hp>0).map(t=>new Option(`${t.name||t.id} · ${Math.ceil(t.hp)}耐久`,t.id)));if([...$('target').options].some(o=>o.value===targetValue))$('target').value=targetValue;
  const supplyValue=$('supply-target').value;$('supply-target').replaceChildren(...sim.units.filter(t=>t.alive&&t.id!==selectedId).map(t=>new Option(t.name||sim.catalog.find(r=>r.id===t.modelId)?.name||t.id,t.id)));if([...$('supply-target').options].some(o=>o.value===supplyValue))$('supply-target').value=supplyValue;
  $('units').replaceChildren(...sim.units.map(t=>{const b=document.createElement('button');b.className='unit-row'+(t.id===selectedId?' active':'')+(t.alive?'':' dead');b.textContent=`${t.name||sim.catalog.find(r=>r.id===t.modelId)?.name||t.id} · ${t.alive?Math.ceil(t.hp)+'耐久':'永久损失'}`;b.onclick=()=>{selectedId=t.id;updateUI();};return b;}));
}
renderer.domElement.addEventListener('contextmenu',event=>event.preventDefault());
renderer.domElement.addEventListener('pointerup',event=>{
  if(!sim)return;const rect=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);
  if(event.button===2){const u=current(),point=new THREE.Vector3();if(u&&raycaster.ray.intersectPlane(plane,point))result(sim.commandMove(u.id,{x:Math.max(-145,Math.min(145,point.x)),z:Math.max(-125,Math.min(125,point.z))}),'移动命令已提交。');}
  else if(event.button===0){const hits=raycaster.intersectObjects([...visuals.values(),...targetVisuals.values()],true);const data=hits[0]?.object.userData;if(data?.unitId)selectedId=data.unitId;else if(data?.targetId){$('target').value=data.targetId;notice('已指定耐久靶；点击攻击按钮下令。');}}
  updateUI();
});
$('search').oninput=renderCatalog;$('filter').onchange=renderCatalog;$('deploy').onclick=deploy;
$('attack').onclick=()=>{const u=current();if(u)result(sim.commandAttack(u.id,$('target').value),'攻击命令已提交。');};
$('supply').onclick=()=>{const u=current();if(u)result(sim.supply(u.id,$('supply-target').value),'补给命令已提交，双方需停稳并保持近距。');};
$('reload').onclick=()=>{const u=current();if(u&&result(sim.commandMove(u.id,{x:sim.station.x+7,z:sim.station.z}),'正在返回后方站。'))pendingLoads.add(u.id);};
$('buy-stock').onclick=()=>result(sim.buyStock(20),'已购入20物资，存放于后方，仍需运输。');
$('damage').onclick=()=>{const u=current();if(u)result(sim.damage(u.id,25),'已施加25点检验伤害。');updateUI();};
$('focus').onclick=()=>{const u=current();if(u){const delta=camera.position.clone().sub(controls.target);controls.target.set(u.x,0,u.z);camera.position.copy(controls.target).add(delta);}};
function reset(){generation++;for(const root of visuals.values()){root.traverse(o=>{if(o.isSkinnedMesh)o.skeleton.dispose();});scene.remove(root);}visuals.clear();pendingLoads.clear();selectedId=null;sim=new GroundSimulation(manifest.models);setupField();renderCatalog();renderModel();updateUI();notice('新一轮检验已就绪；本轮损失与库存已重置。');window.__ground={get sim(){return sim;},get visuals(){return visuals;},get camera(){return camera;},get renderer(){return renderer;}};}
$('reset').onclick=()=>{if(manifest)reset();};
try{const response=await fetch('/manifest.json');if(!response.ok)throw new Error('资源清单读取失败');manifest=await response.json();reset();$('catalog-count').textContent=`${sim.catalog.length} 条 / ${sim.catalog.filter(r=>r.deployable).length} 可部署`;await deploy();}
catch(error){console.error(error);notice('检验场启动失败：'+error.message,true);}
function frame(now){requestAnimationFrame(frame);const dt=Math.min(.1,(now-last)/1000);last=now;if(sim){sim.update(dt);for(const id of [...pendingLoads]){const u=sim.units.find(u=>u.id===id);if(!u?.alive){pendingLoads.delete(id);continue;}if(!u.moving&&Math.hypot(u.x-sim.station.x,u.z-sim.station.z)<=sim.station.radius){result(sim.loadSupply(id),'正在后方装载。');pendingLoads.delete(id);}}
    for(const u of sim.units){const root=visuals.get(u.id);if(!root)continue;root.position.set(u.x,0,u.z);root.rotation.y=(u.yaw||0)+Math.PI;root.rotation.z=u.alive?0:.12;root.scale.y=u.alive?1:.65;}
    for(const t of sim.targets){const root=targetVisuals.get(t.id);if(root)root.visible=t.hp>0;}
    const u=current();selectionRing.visible=!!u?.alive;if(u){selectionRing.position.set(u.x,.15,u.z);}
    if(now>=nextUI){updateUI();nextUI=now+200;}
  }controls.update();renderer.render(scene,camera);}
requestAnimationFrame(frame);
