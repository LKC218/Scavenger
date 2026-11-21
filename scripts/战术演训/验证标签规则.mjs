/** 标签稳定性纯规则与Three场景适配回归，不启动浏览器或服务。 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as THREE from '../../local-data/tools/three/0.187.0/build/three.module.js';
import {StableLabelLayout,rotationEnvelope,intersects} from '../../src/战术演训/标签布局.mjs';
import {AttachedLabelLayout,UNIT_SYMBOLS,nearbySlots,attachmentLine} from '../../src/战术演训/载具名牌.mjs';
const results=[];
function test(name,body){try{const detail=body();results.push({name,passed:true,...detail});console.log('通过 '+name);}catch(error){results.push({name,passed:false,error:error.message});console.error('失败 '+name+'：'+error.message);}}
const rect=(left,top,width,height)=>({left,top,right:left+width,bottom:top+height,width,height});
test('10秒候选重新开放时保留已有可用槽位',()=>{
 const layout=new StableLabelLayout();for(let i=0;i<600;i++){const blockers=i%30<15?[rect(80,15,140,60)]:[];layout.begin({width:500,height:300,blockers,time:i/60});const row=layout.place({key:'u',unitId:'u',kind:'unit',anchor:{x:150,y:110},width:90,height:24,candidates:[{slot:'upper',x:100,y:40},{slot:'right',x:260,y:100}]});assert.equal(row.visible,true);assert.equal(row.slot,'right');layout.finish();}assert.equal(layout.placementChanges,0);assert.equal(layout.visibilityChanges,0);
});
test('主旋翼及尾桨全角度包络不缩小真实边界',()=>{
 for(const axis of ['y','x']){const box=new THREE.Box3(new THREE.Vector3(-8,-.2,-.4),new THREE.Vector3(8,.2,.4)),raw=rotationEnvelope(box,axis),envelope=new THREE.Box3(new THREE.Vector3().copy(raw.min),new THREE.Vector3().copy(raw.max));for(let n=0;n<720;n++){const m=axis==='y'?new THREE.Matrix4().makeRotationY(n/720*Math.PI*2):new THREE.Matrix4().makeRotationX(n/720*Math.PI*2);for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z])assert.ok(envelope.containsPoint(new THREE.Vector3(x,y,z).applyMatrix4(m)));}}
});
test('缓存位置真遮挡时重新布局且不来回抢回首位',()=>{
 const l=new StableLabelLayout(),p={key:'u',unitId:'u',kind:'unit',anchor:{x:150,y:150},width:90,height:24,candidates:[{slot:'upper',x:100,y:50},{slot:'right',x:260,y:120}]};l.begin({width:500,height:300});assert.equal(l.place(p).slot,'upper');l.finish();l.begin({width:500,height:300,blockers:[rect(90,40,120,50)]});assert.equal(l.place(p).slot,'right');l.finish();l.begin({width:500,height:300});assert.equal(l.place(p).slot,'right');l.finish();assert.equal(l.placementChanges,1);assert.equal(l.visibilityChanges,0);
});
test('确实无空位保留no-space诊断而非覆盖机身',()=>{const l=new StableLabelLayout();l.begin({width:500,height:300,blockers:[rect(0,0,500,300)]});const p=l.place({key:'u',unitId:'u',kind:'unit',anchor:{x:150,y:150},width:90,height:24,candidates:[]});assert.equal(p.visible,false);assert.equal(p.reason,'no-space');});
test('贴身名牌拒绝远处空位并保留局部降级原因',()=>{
 const layout=new AttachedLabelLayout(),body=rect(200,150,40,30);
 layout.begin({width:800,height:600});
 const distant=layout.place({key:'u',unitId:'u',kind:'unit',anchor:{x:220,y:165},width:100,height:25,body,candidates:[{slot:'distant',x:500,y:400}]});
 assert.equal(distant.visible,false);assert.equal(distant.reason,'local-space-exhausted');
 layout.begin({width:800,height:600,blockers:[rect(180,95,140,45)]});
 const nearby=layout.place({key:'u',unitId:'u',kind:'unit',anchor:{x:220,y:165},width:100,height:25,body,candidates:nearbySlots(body,100,25)});
 assert.equal(nearby.visible,true);assert.ok(Math.hypot(Math.max(body.left-nearby.x-nearby.width,nearby.x-body.right,0),Math.max(body.top-nearby.y-nearby.height,nearby.y-body.bottom,0))<=30);
});
let hud=[];
function element(){let hidden=false,changes=0;return{style:{},dataset:{},classList:{add(){},toggle(){}},children:[],get hidden(){return hidden;},set hidden(v){if(hidden!==v)changes++;hidden=v;},get hiddenChanges(){return changes;},setAttribute(){},replaceChildren(...c){this.children=c;},append(...c){this.children.push(...c);},remove(){}};}
const document={createElementNS:()=>element(),createElement:()=>element(),createTextNode:text=>({text}),querySelectorAll:()=>hud.map(box=>({hidden:false,getBoundingClientRect:()=>box}))};
const scope={THREE,StableLabelLayout,AttachedLabelLayout,UNIT_SYMBOLS,nearbySlots,attachmentLine,rotationEnvelope,document,performance:{now:()=>scope.time*1000},time:0};
const source=readFileSync(new URL('../../src/战术演训/场景.mjs',import.meta.url),'utf8').replace(/^import .*;$/gm,'').replace('export const CAMERA_TUNING','const CAMERA_TUNING').replace('export class Battlefield','class Battlefield');vm.runInNewContext(source+';globalThis.Battlefield=Battlefield;',scope);
function fixture(){
 const v=Object.create(scope.Battlefield.prototype),units=[{id:'heli-1',kind:'heli',name:'游隼01',x:-12,y:50,z:-8,yaw:0,state:'悬停',altitudeState:{status:'holding',baseGround:20}},{id:'heli-2',kind:'heli',name:'游隼02',x:12,y:50,z:8,yaw:0,state:'悬停',altitudeState:{status:'holding',baseGround:20}}],map=new Map();
 for(const u of units)map.set(u.id,{rig:{center:new THREE.Vector3(0,2.6,0)},worldBounds:new THREE.Box3(),labelWorldBounds:new THREE.Box3(),label:element(),altitudeLabel:element(),leader:element(),highlight:element(),nameplateWorldBounds:new THREE.Box3(),displayIndex:0});
 Object.assign(v,{width:1290,height:696,span:70*696/1290,targetSpan:70*696/1290,angle:0,targetAngle:0,center:new THREE.Vector3(),centerTarget:null,panInput:new THREE.Vector2(),panVelocity:new THREE.Vector2(),camera:new THREE.OrthographicCamera(),groundPlane:new THREE.Plane(new THREE.Vector3(0,1,0),0),map:{streaming:true,safeAltitude:0,camera:{max_height_m:20000},bounds:{minX:-4500,maxX:4500,minZ:-4500,maxZ:4500},heightAt:()=>20,coarseHeightAt:()=>20},units:map,sim:{units,time:0},labels:{getBoundingClientRect:()=>({left:0,top:0})},airFocus:new Map(),svgNS:'http://www.w3.org/2000/svg',nameplateLines:element(),labelMeasure:{measureText:text=>({width:text.length*8})},hoveredUnit:null,labelLayout:new AttachedLabelLayout(),labelStats:{rows:[]},pathLabels:new Map(),commandMarkers:[],layerFilter:'all',altitudeLine:{geometry:new THREE.BufferGeometry(),computeLineDistances(){}}});
 return{v,units,selected:new Set(['heli-2'])};
}
function updateBounds(v,units,time){
 const rotorBox=new THREE.Box3(new THREE.Vector3(-8,-.15,-.4),new THREE.Vector3(8,.15,.4)),stable=rotationEnvelope(rotorBox,'y');
 for(const u of units){const row=v.units.get(u.id),pose=new THREE.Matrix4().makeTranslation(u.x,u.y,u.z),spin=new THREE.Matrix4().makeRotationY(time*26),rotorPose=new THREE.Matrix4().makeTranslation(0,3.77,0).multiply(spin),bodyBox=new THREE.Box3(new THREE.Vector3(-2,0,-6),new THREE.Vector3(2,5,9));row.nameplateWorldBounds.copy(bodyBox).applyMatrix4(pose);row.worldBounds.copy(bodyBox).union(rotorBox.clone().applyMatrix4(rotorPose)).applyMatrix4(pose);row.labelWorldBounds.copy(bodyBox).union(new THREE.Box3(new THREE.Vector3().copy(stable.min),new THREE.Vector3().copy(stable.max)).translate(new THREE.Vector3(0,3.77,0))).applyMatrix4(pose).union(row.worldBounds);assert.ok(row.labelWorldBounds.containsBox(row.worldBounds));}
}
function series(mode){
 hud=[rect(430,619,430,37)];const{v,units,selected}=fixture(),seen=new Map();let maxStep=0,maxBodyStep=0,lastBody=null,minBodyY=Infinity,maxBodyY=-Infinity;
 for(let n=0;n<600;n++){const t=n/60;scope.time=t;v.sim.time=t;
  if(mode==='移动'){units.forEach(u=>{const i=u.id==='heli-1'?0:1;u.x=(-12+i*24)+t*.6;u.z=(-8+i*16)+t*.25;});}
  if(mode==='旋转')v.angle=t*.85;
  if(mode==='缩放')v.span=(145+75*Math.cos(t/10*Math.PI*2))*v.height/v.width;
  updateBounds(v,units,t);v.trackAt(v.unitFocus('heli-2'));if(n%2)v.sim.units.reverse();v.syncLabels(v.sim,selected);
  const currentBody=v.projectUnit('heli-2');if(lastBody)maxBodyStep=Math.max(maxBodyStep,Math.hypot(currentBody.x-lastBody.x,currentBody.y-lastBody.y));lastBody=currentBody;minBodyY=Math.min(minBodyY,currentBody.y);maxBodyY=Math.max(maxBodyY,currentBody.y);
  for(const row of v.getLabelState('heli-2').filter(r=>r.kind==='unit')){assert.equal(row.visible,true,mode+' '+row.key+' '+row.reason);const box=rect(row.x,row.y,row.width,row.height);for(const u of units){const body=v.projectNameplateBounds(u.id);assert.equal(intersects(box,body),false,mode+' 标签不可盖机身主体');}for(const h of hud)assert.equal(intersects(box,h),false,mode+' 标签不可盖HUD');const old=seen.get(row.key);if(old&&old.slot===row.slot)maxStep=Math.max(maxStep,Math.hypot(row.x-old.x,row.y-old.y));if(old&&old.slot!==row.slot)assert.ok(['geometry-or-hud','label-overlap','out-of-bounds','local-slot'].includes(row.placementReason),'换位须使用受局部避让约束的槽位');seen.set(row.key,{...row});}
  assert.equal(v.altitudeLinks[0].height,30);assert.equal(v.altitudeLinks[0].runPoint.y,50);assert.equal(v.altitudeLinks[0].groundPoint.y,20);
 }
 const selectedRows=v.getLabelState('heli-2').filter(r=>r.kind==='unit'),visibility=selectedRows.reduce((a,r)=>a+r.visibilityChanges,0),slots=selectedRows.reduce((a,r)=>a+r.slotChanges,0);assert.equal(visibility,0,mode+' 不应周期显隐');if(mode==='悬停'){assert.equal(slots,0);assert.ok(maxBodyY-minBodyY<1e-7,'静止机身锚点不能因旋翼转动抖动');}assert.ok(slots<30,mode+' 不应频繁换位');assert.ok(maxStep<8,mode+' 同槽位应连续跟随，实际最大单帧位移 '+maxStep);assert.ok(maxBodyStep<8,mode+' 机身锚点不能被标签平滑掩盖');return{mode,seconds:10,frames:600,visibilityChanges:visibility,slotChanges:slots,maxSameSlotStep:maxStep,maxBodyStep,bodyYRange:maxBodyY-minBodyY};
}
for(const mode of ['悬停','移动','旋转','缩放'])test('10秒实际Three投影标签'+mode,()=>series(mode));
console.log(JSON.stringify({passed:results.filter(r=>r.passed).length,total:results.length,results},null,2));if(results.some(r=>!r.passed))process.exitCode=1;
