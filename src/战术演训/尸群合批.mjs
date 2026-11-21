/** 将原骨骼片段烘入内存姿态纹理，按型号实例绘制；不改原模型或战斗碰撞。 */
import * as THREE from 'three';
import {clone} from 'three/addons/utils/SkeletonUtils.js';
import {Actors} from '/crowd/角色.mjs';
import {assignModels} from '/crowd/角色配置.mjs';

const FRAMES=64,CAPACITY=2000;
const SKIN=`
#ifdef USE_SKINNING
uniform mat4 bindMatrix;
uniform mat4 bindMatrixInverse;
uniform highp sampler2D actorAtlas;
attribute vec4 actorPose;
varying float vActorOpacity;
mat4 actorFrame(float bone,float row){
 int x=int(bone)*4;int y=int(row);
 return mat4(texelFetch(actorAtlas,ivec2(x,y),0),texelFetch(actorAtlas,ivec2(x+1,y),0),texelFetch(actorAtlas,ivec2(x+2,y),0),texelFetch(actorAtlas,ivec2(x+3,y),0));
}
mat4 actorClip(float bone,float phase,float offset){
 float frame=fract(phase)*64.0;float a=floor(frame),b=mod(a+1.0,64.0);
 return actorFrame(bone,offset+a)*(1.0-fract(frame))+actorFrame(bone,offset+b)*fract(frame);
}
mat4 getBoneMatrix(const in float bone){
 if(actorPose.z<0.001)return actorClip(bone,actorPose.y,64.0);
 if(actorPose.z>0.999)return actorClip(bone,actorPose.x,0.0);
 return actorClip(bone,actorPose.y,64.0)*(1.0-actorPose.z)+actorClip(bone,actorPose.x,0.0)*actorPose.z;
}
#endif`;

function materialFor(source,atlas,transparent){
  const material=source.base.material.clone();material.color.setHex(0xffffff);material.defines={...material.defines,USE_SKINNING:''};
  material.transparent=transparent;material.depthWrite=!transparent;
  material.onBeforeCompile=shader=>{
    shader.uniforms.actorAtlas={value:atlas};shader.uniforms.bindMatrix={value:source.base.bindMatrix};shader.uniforms.bindMatrixInverse={value:source.base.bindMatrixInverse};
    shader.vertexShader=shader.vertexShader.replace('#include <skinning_pars_vertex>',SKIN).replace('#include <skinbase_vertex>','#include <skinbase_vertex>\nvActorOpacity=actorPose.w;');
    shader.fragmentShader='varying float vActorOpacity;\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>','#include <opaque_fragment>\ngl_FragColor.a*=vActorOpacity;');
  };
  material.customProgramCacheKey=()=> 'defense-actor-atlas-v2-'+Number(transparent);
  return material;
}

function batchFor(source,atlas,capacity,transparent){
  const g=new THREE.InstancedBufferGeometry();g.setIndex(source.base.geometry.index);
  for(const [key,attribute] of Object.entries(source.base.geometry.attributes))g.setAttribute(key,attribute);
  const pose=new THREE.InstancedBufferAttribute(new Float32Array(capacity*4),4);pose.setUsage(THREE.DynamicDrawUsage);g.setAttribute('actorPose',pose);
  const mesh=new THREE.InstancedMesh(g,materialFor(source,atlas,transparent),capacity);mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.frustumCulled=false;mesh.count=0;
  mesh.instanceColor=new THREE.InstancedBufferAttribute(new Float32Array(capacity*3),3);mesh.instanceColor.setUsage(THREE.DynamicDrawUsage);
  return{mesh,pose,capacity,count:0};
}

async function bake(source,renderer){
  source.asset.scene.updateMatrixWorld(true);
  const root=clone(source.asset.scene);let mesh;root.traverse(o=>{if(o.isSkinnedMesh)mesh=o;});root.updateMatrixWorld(true);
  const meshMatrix=mesh.matrixWorld.clone(),width=THREE.MathUtils.ceilPowerOfTwo(mesh.skeleton.bones.length*4),height=FRAMES*2;
  const values=new Float32Array(width*height*4),mixer=new THREE.AnimationMixer(root);
  for(const [clipIndex,name] of ['移动','待机'].entries()){
    mixer.stopAllAction();const clip=source.asset.animations.find(c=>c.name===name),action=mixer.clipAction(clip);action.play();
    for(let f=0;f<FRAMES;f++){
      mixer.setTime(f/FRAMES*clip.duration);root.updateMatrixWorld(true);mesh.skeleton.update();values.set(mesh.skeleton.boneMatrices,(clipIndex*FRAMES+f)*width*4);
    }
  }
  mixer.stopAllAction();mixer.uncacheRoot(root);mesh.skeleton.dispose();
  const atlas=new THREE.DataTexture(values,width,height,THREE.RGBAFormat,THREE.FloatType);atlas.magFilter=atlas.minFilter=THREE.NearestFilter;atlas.needsUpdate=true;renderer.initTexture(atlas);
  return{atlas,values,width,meshMatrix};
}

export class BatchedActors extends Actors{
  async prepare(sim,view,progress){
    this.rigs=[];this.dummy=new THREE.Object3D();this.matrix=new THREE.Matrix4();this.poseTemps=new Float32Array(4);
    this.preparation={bakeMs:0,shaderMs:0};const before=performance.now();
    for(let i=0;i<this.assets.length;i++){
      progress(`准备骨骼合批 ${i+1} / ${this.assets.length}…`);
      const source=this.assets[i],animation=await bake(source,view.renderer),live=batchFor(source,animation.atlas,CAPACITY,false),dead=batchFor(source,animation.atlas,80,true);
      this.group.add(live.mesh,dead.mesh);this.rigs.push({...animation,live,dead,source});
      await new Promise(resolve=>requestAnimationFrame(resolve));
    }
    this.preparation.bakeMs=performance.now()-before;
    // 启动前准备着色器和缓冲，波次只写既有实例，不触发骨架克隆或GPU初次分配。
    const shaderBegin=performance.now();
    for(const rig of this.rigs)for(const b of [rig.live,rig.dead]){b.mesh.count=1;this.dummy.position.set(0,0,-10000);this.dummy.updateMatrix();b.mesh.setMatrixAt(0,this.dummy.matrix);b.mesh.setColorAt(0,new THREE.Color(1,1,1));b.pose.setXYZW(0,0,0,0,1);}
    await view.renderer.compileAsync(view.scene,view.camera);view.renderer.render(view.scene,view.camera);
    for(const rig of this.rigs){rig.live.mesh.count=0;rig.dead.mesh.count=0;}
    this.preparation.shaderMs=performance.now()-shaderBegin;this.serial=null;
  }
  rebuild(agents,kind,seed){
    const before=performance.now();this.assignments=assignModels(agents.length,this.models,seed,kind);this.items.length=agents.length;
    for(let i=0;i<agents.length;i++){const a=agents[i],o=this.items[i]??={};Object.assign(o,{modelIndex:this.assignments[i],walkTime:a.phase,idleTime:a.phase,weight:0,deathFrozen:false});}
    this.rebuildMs=performance.now()-before;this.rebuildCount=(this.rebuildCount??0)+1;
  }
  update(sim,dt,view){
    const begin=performance.now();
    if(this.serial!==sim.waveSerial){this.rebuild(sim.targets,sim.options.modelMode,sim.options.seed+sim.wave*313);this.serial=sim.waveSerial;sim.setProfiles(this.assignments,this.assets.map(a=>a.model.coordinate_system?.rest_height_m??1.75));}
    for(const r of this.rigs){r.live.count=0;r.dead.count=0;}
    let animated=0,visible=0,corpses=0;
    const dead=sim.targets.filter(a=>a.hp<=0&&!a.leaked&&sim.time-a.deathAt<3).sort((a,b)=>b.deathAt-a.deathAt).slice(0,80),shown=new Set(dead.map(a=>a.id));
    for(let i=0;i<sim.targets.length;i++){
      const a=sim.targets[i],o=this.items[i],rig=this.rigs[o.modelIndex],alive=a.hp>0;if(!alive&&!shown.has(a.id))continue;
      const batch=alive?rig.live:rig.dead,index=batch.count++;if(index>=batch.capacity)throw new Error('尸群实例容量不足，不能静默少画');
      this.dummy.position.set(a.x,alive?0:.12,a.z);this.dummy.rotation.set(0,a.heading,alive?0:Math.min(1,(sim.time-a.deathAt)/.35)*Math.PI/2);this.dummy.scale.setScalar(a.scale);this.dummy.updateMatrix();this.matrix.multiplyMatrices(this.dummy.matrix,rig.meshMatrix);batch.mesh.setMatrixAt(index,this.matrix);
      let alpha=1;
      if(alive){
        const speed=Math.hypot(a.vx,a.vz),weight=Math.min(1,speed/.55);o.weight+=(weight-o.weight)*(1-Math.exp(-dt*8));
        if(dt>0){o.walkTime=(o.walkTime+dt*Math.max(.25,speed/1.2)/rig.source.asset.animations.find(c=>c.name==='移动').duration)%1;o.idleTime=(o.idleTime+dt/rig.source.asset.animations.find(c=>c.name==='待机').duration)%1;animated++;}
        if(view.project(a).visible)visible++;
      }else{alpha=Math.min(1,Math.max(0,(3-(sim.time-a.deathAt))/.6));corpses++;}
      batch.pose.setXYZW(index,o.walkTime,o.idleTime,o.weight,alpha);batch.mesh.setColorAt(index,rig.source.palette[i%8].color);
    }
    let calls=0;
    for(const r of this.rigs)for(const b of [r.live,r.dead]){
      b.mesh.count=b.count;if(!b.count)continue;calls++;
      for(const [attribute,count] of [[b.mesh.instanceMatrix,b.count*16],[b.mesh.instanceColor,b.count*3],[b.pose,b.count*4]]){attribute.clearUpdateRanges();attribute.addUpdateRange(0,count);attribute.needsUpdate=true;}
    }
    this.animated=animated;this.visible=visible;this.corpses=corpses;this.batchCalls=calls;this.updateMs=performance.now()-begin;
  }
}
