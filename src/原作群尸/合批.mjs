/** 通用片段元数据驱动的GPU骨骼实例化；原片段和骨架均只读。 */
import * as THREE from 'three';
import{createModelLoader}from'/compressed-loader.mjs';
import{clone}from'three/addons/utils/SkeletonUtils.js';

const SKIN=`
#ifdef USE_SKINNING
uniform mat4 bindMatrix;uniform mat4 bindMatrixInverse;uniform highp sampler2D actorAtlas;
attribute vec4 actorPoseA;attribute vec4 actorPoseB;varying float vActorOpacity;
mat4 nativeFrame(float bone,float row){int x=int(bone)*4,y=int(row);return mat4(texelFetch(actorAtlas,ivec2(x,y),0),texelFetch(actorAtlas,ivec2(x+1,y),0),texelFetch(actorAtlas,ivec2(x+2,y),0),texelFetch(actorAtlas,ivec2(x+3,y),0));}
mat4 nativeSample(float bone,vec4 p){return nativeFrame(bone,p.x)*(1.0-p.z)+nativeFrame(bone,p.y)*p.z;}
mat4 getBoneMatrix(const in float bone){if(actorPoseA.w>.999)return nativeSample(bone,actorPoseA);if(actorPoseA.w<.001)return nativeSample(bone,actorPoseB);return nativeSample(bone,actorPoseA)*actorPoseA.w+nativeSample(bone,actorPoseB)*(1.0-actorPoseA.w);}
#endif`;

function makeBatch(rig,capacity,transparent){
 const geometry=new THREE.InstancedBufferGeometry();geometry.setIndex(rig.base.geometry.index);for(const[k,a]of Object.entries(rig.base.geometry.attributes))geometry.setAttribute(k,a);
 const a=new THREE.InstancedBufferAttribute(new Float32Array(capacity*4),4),b=a.clone();a.setUsage(THREE.DynamicDrawUsage);b.setUsage(THREE.DynamicDrawUsage);geometry.setAttribute('actorPoseA',a);geometry.setAttribute('actorPoseB',b);
 const mat=rig.base.material.clone();mat.defines={...mat.defines,USE_SKINNING:''};mat.transparent=transparent;mat.depthWrite=!transparent;
 mat.onBeforeCompile=shader=>{shader.uniforms.actorAtlas={value:rig.atlas};shader.uniforms.bindMatrix={value:rig.base.bindMatrix};shader.uniforms.bindMatrixInverse={value:rig.base.bindMatrixInverse};shader.vertexShader=shader.vertexShader.replace('#include <skinning_pars_vertex>',SKIN).replace('#include <skinbase_vertex>','#include <skinbase_vertex>\nvActorOpacity=actorPoseB.w;');shader.fragmentShader='varying float vActorOpacity;\n'+shader.fragmentShader;shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>','#include <opaque_fragment>\ngl_FragColor.a*=vActorOpacity;');};
 mat.customProgramCacheKey=()=> 'native-crowd-v1-'+Number(transparent);
 const mesh=new THREE.InstancedMesh(geometry,mat,capacity);mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.frustumCulled=false;mesh.count=0;mesh.instanceColor=new THREE.InstancedBufferAttribute(new Float32Array(capacity*3),3);mesh.instanceColor.setUsage(THREE.DynamicDrawUsage);
 return{mesh,a,b,count:0,capacity};
}

async function bake(asset,base,renderer){
 const root=clone(asset.scene);root.updateMatrixWorld(true);let mesh;root.traverse(o=>{if(o.isSkinnedMesh)mesh=o;});
 const mixer=new THREE.AnimationMixer(root),clips={},width=THREE.MathUtils.ceilPowerOfTwo(mesh.skeleton.bones.length*4);let rows=0;
 for(const clip of asset.animations){const frames=Math.max(2,Math.ceil(clip.duration*96)+1);clips[clip.name]={name:clip.name,duration:clip.duration,offset:rows,frames};rows+=frames;}
 const values=new Float32Array(width*rows*4);
 for(const clip of asset.animations){const entry=clips[clip.name];mixer.stopAllAction();const action=mixer.clipAction(clip);action.setLoop(THREE.LoopOnce,1);action.clampWhenFinished=true;action.play();
  for(let i=0;i<entry.frames;i++){mixer.setTime(i/(entry.frames-1)*clip.duration);root.updateMatrixWorld(true);mesh.skeleton.update();values.set(mesh.skeleton.boneMatrices,(entry.offset+i)*width*4);}
 }
 const matrix=mesh.matrixWorld.clone();mixer.stopAllAction();mixer.uncacheRoot(root);mesh.skeleton.dispose();
 const atlas=new THREE.DataTexture(values,width,rows,THREE.RGBAFormat,THREE.FloatType);atlas.minFilter=atlas.magFilter=THREE.NearestFilter;atlas.needsUpdate=true;renderer.initTexture(atlas);return{atlas,values,width,clips,meshMatrix:matrix};
}

function frameRows(c,time,loop){const normalized=loop?((time%c.duration)+c.duration)%c.duration/c.duration:THREE.MathUtils.clamp(time/c.duration,0,1),f=normalized*(c.frames-1),i=Math.floor(f);return[c.offset+i,c.offset+Math.min(i+1,c.frames-1),f-i];}

export class NativeActors{
 constructor(scene){this.scene=scene;this.group=new THREE.Group();scene.add(this.group);this.items=[];this.temp=new THREE.Object3D();this.matrix=new THREE.Matrix4();this.color=new THREE.Color();this.stateOverrides=new Map();}
 async load(renderer,manifest){
  this.manifest=manifest;this.models=manifest.models;this.assets=[];this.rigs=[];const loader=createModelLoader(renderer);
  for(const model of this.models){
   const asset=await loader.loadAsync(model.url),meshes=[];asset.scene.traverse(o=>{if(o.isSkinnedMesh)meshes.push(o);});
   if(meshes.length!==1)throw Error(model.id+'必须只有一个有效LOD蒙皮网格');const base=meshes[0];asset.scene.updateMatrixWorld(true);
   if(base.skeleton.bones.length!==model.bones||(base.geometry.index?.count??base.geometry.attributes.position.count)/3!==model.triangles)throw Error(model.id+'骨架或几何身份不符');
   for(const expected of model.clips){const clip=asset.animations.find(c=>c.name===expected.name);if(!clip||Math.abs(clip.duration-expected.duration_s)>.001)throw Error(model.id+'动作缺失或时长不符：'+expected.name);}
   if(asset.animations.length!==model.clips.length)throw Error(model.id+'片段数量不符');
   const rig={model,asset,base,...await bake(asset,base,renderer)};rig.live=makeBatch(rig,2000,false);rig.dead=makeBatch(rig,80,true);this.group.add(rig.live.mesh,rig.dead.mesh);this.assets.push(rig);this.rigs.push(rig);
  }
  this.renderer=renderer;this.prepared=true;
 }
 async prepare(){return;}
 rebuild(agents,kind='mixed',seed=1){
  const assignment=Array.from({length:agents.length},(_,i)=>kind==='single'?0:i%this.models.length);let state=seed>>>0;
  for(let i=assignment.length-1;i>0;i--){state=(Math.imul(state,1664525)+1013904223)>>>0;const j=Math.floor(state/4294967296*(i+1));[assignment[i],assignment[j]]=[assignment[j],assignment[i]];}
  this.assignments=assignment;this.items=agents.map((agent,i)=>({modelIndex:assignment[i],height:this.models[assignment[i]].coordinate_system.rest_height_m,time:agent.phase??0,weight:1,root:{position:{x:agent.x,z:agent.z}},state:'move'}));
  this.distribution=this.models.map((model,i)=>({...model,count:assignment.filter(x=>x===i).length}));this.frameCounts=this.distribution.map(m=>({id:m.id,count:m.count,visible:0,animated:0,moving:0}));this.stateOverrides.clear();
 }
 setState(index,state){if(!['move','idle','attack','death'].includes(state))throw Error('未知动作状态');this.stateOverrides.set(index,{state,start:this.items[index]?.time??0});}
 animate(dt,agents,mode,paused){
  let total=0;for(const r of this.frameCounts){r.visible=0;r.animated=0;r.moving=0;}
  for(let i=0;i<this.items.length;i++){const item=this.items[i],a=agents[i],speed=Math.hypot(a.vx??0,a.vz??0);if(!paused&&mode!=='static'){item.time+=dt;this.frameCounts[item.modelIndex].animated++;total++;}if(speed>.1)this.frameCounts[item.modelIndex].moving++;
   const desired=mode==='animated'?1:Math.min(1,speed/.55);item.weight+=(desired-item.weight)*(1-Math.exp(-dt*8));}
  return total;
 }
 sync(agents,alpha=1){
  for(const r of this.rigs){r.live.count=r.dead.count=0;}
  for(let i=0;i<agents.length;i++){
   const a=agents[i],item=this.items[i],rig=this.rigs[item.modelIndex],override=this.stateOverrides.get(i),dead=a.hp!==undefined&&a.hp<=0,state=override?.state??(dead?'death':'move');
   const deathTime=a.deathAge??Math.max(0,item.time-(override?.start??item.time));const time=override?Math.max(0,item.time-override.start):dead?deathTime:item.time;
   const batch=state==='death'?rig.dead:rig.live;if(batch.count>=batch.capacity){if(state==='death')continue;throw Error('活体实例超过已分配容量，不能静默少画');}const index=batch.count++;
   const x=a.px===undefined?a.x:a.px+(a.x-a.px)*alpha,z=a.pz===undefined?a.z:a.pz+(a.z-a.pz)*alpha;item.root.position.x=x;item.root.position.z=z;
   this.temp.position.set(x,0,z);this.temp.rotation.set(0,a.heading??0,0);this.temp.scale.setScalar(a.scale??1);this.temp.updateMatrix();this.matrix.multiplyMatrices(this.temp.matrix,rig.meshMatrix);batch.mesh.setMatrixAt(index,this.matrix);
   const states=rig.model.states,idle=rig.clips[states.idle],move=rig.clips[states.move];let A,B,weight=item.weight,opacity=1;
   if(state==='attack'||state==='death'){const clip=rig.clips[states[state]]??idle;A=B=frameRows(clip,time,state==='attack');weight=1;if(state==='death')opacity=Math.max(0,1-Math.max(0,time-clip.duration)/.8);}
   else if(state==='idle'){A=B=frameRows(idle,time,true);weight=1;}
   else{A=frameRows(move,time,true);B=frameRows(idle,time,true);}
   batch.a.setXYZW(index,...A,weight);batch.b.setXYZW(index,...B,opacity);this.color.setScalar(rig.model.family==='native'?1:.9+(i%5)*.04);batch.mesh.setColorAt(index,this.color);
  }
  let calls=0;for(const r of this.rigs)for(const b of[r.live,r.dead]){b.mesh.count=b.count;if(!b.count)continue;calls++;for(const[attr,count]of[[b.mesh.instanceMatrix,b.count*16],[b.mesh.instanceColor,b.count*3],[b.a,b.count*4],[b.b,b.count*4]]){attr.clearUpdateRanges();attr.addUpdateRange(0,count);attr.needsUpdate=true;}}
  this.batchCalls=calls;
 }
 update(sim,dt,view){
  if(this.serial!==sim.waveSerial){this.rebuild(sim.targets,sim.options.modelMode,sim.options.seed+sim.wave*313);this.serial=sim.waveSerial;sim.setProfiles(this.assignments,this.models.map(m=>m.coordinate_system.rest_height_m));}
  const live=[],items=[],assignments=[];for(let i=0;i<sim.targets.length;i++){const a=sim.targets[i],rig=this.rigs[this.items[i].modelIndex],duration=rig.clips[rig.model.states.death]?.duration??3;if(a.hp<=0&&(a.leaked||sim.time-a.deathAt>duration+.8))continue;live.push({...a,deathAge:a.hp<=0?sim.time-a.deathAt:0});items.push(this.items[i]);assignments.push(i);}
  const original=this.items;this.items=items;this.animate(dt,live,'open',dt===0);this.sync(live,1);this.items=original;this.animated=dt>0?live.filter(a=>a.hp>0).length:0;this.visible=live.filter(a=>a.hp>0&&view.project(a).visible).length;this.corpses=Math.min(80,live.filter(a=>a.hp<=0).length);
 }
 evidence(){return{models:this.rigs.map(r=>({id:r.model.id,bones:r.base.skeleton.bones.length,triangles:r.model.triangles,clips:Object.values(r.clips).map(c=>({...c})),atlasBytes:r.values.byteLength})),instances:this.items.length,batchCalls:this.batchCalls,independentPhases:this.items.slice(0,8).map(i=>i.time)};}
}
