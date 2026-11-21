import * as THREE from 'three';
import {createModelLoader} from '/compressed-loader.mjs';
import {clone} from 'three/addons/utils/SkeletonUtils.js';
import {modelDescriptors,assignModels,modelDistribution} from './角色配置.mjs';

export class Actors {
  constructor(scene){this.scene=scene;this.group=new THREE.Group();scene.add(this.group);this.items=[];}
  async load(renderer,manifest){
    this.models=modelDescriptors(manifest);this.assets=[];
    const loader=createModelLoader(renderer);
    for(const model of this.models){
      const asset=await loader.loadAsync(model.url),meshes=[];
      asset.scene.traverse(o=>{if(o.isSkinnedMesh)meshes.push(o);});
      const base=meshes[0];
      if(meshes.length!==1||base.skeleton.bones.length!==model.bones)throw new Error(model.id+'独立骨架不匹配');
      if((Array.isArray(base.material)?base.material.length:1)!==model.materials||model.materials!==1)throw new Error(model.id+'材质数不匹配');
      if((base.geometry.index?.count??base.geometry.attributes.position.count)/3!==model.triangles)throw new Error(model.id+'三角形不匹配');
      if(asset.animations.length!==2)throw new Error(model.id+'片段数量不匹配');
      for(const expected of model.clips){const c=asset.animations.find(c=>c.name===expected.name);if(!c||Math.abs(c.duration-expected.duration_s)>.001)throw new Error(model.id+'片段时长不匹配');}
      const palette=Array.from({length:8},(_,i)=>{const m=base.material.clone();m.color.setHSL(.08+i*.035,.08,.78+(i%3)*.05);return m;});
      this.assets.push({asset,base,palette,model});
    }
    this.asset=this.assets[0].asset;
  }
  rebuild(agents,kind='mixed',seed=1){
    for(const item of this.items){item.mixer.stopAllAction();item.mixer.uncacheRoot(item.root);item.mesh.skeleton.dispose();this.group.remove(item.root);}
    this.items=[];this.assignments=assignModels(agents.length,this.models,seed,kind);
    this.distribution=modelDistribution(this.models,this.assignments);
    this.frameCounts=this.distribution.map(r=>({id:r.id,count:r.count,animated:0,visible:0,moving:0}));
    for(const a of agents){
      const modelIndex=this.assignments[a.id],source=this.assets[modelIndex],root=clone(source.asset.scene);let mesh;
      root.traverse(o=>{if(o.isSkinnedMesh){mesh=o;o.frustumCulled=false;o.material=source.palette[a.id%8];}});
      root.scale.multiplyScalar(a.scale);const mixer=new THREE.AnimationMixer(root);
      const idle=mixer.clipAction(source.asset.animations.find(c=>c.name==='待机'));
      const walk=mixer.clipAction(source.asset.animations.find(c=>c.name==='移动'));
      idle.play();walk.play();idle.time=a.phase*idle.getClip().duration;walk.time=a.phase*walk.getClip().duration;
      idle.setEffectiveWeight(0);walk.setEffectiveWeight(1);mixer.update(0);
      root.position.set(a.x,0,a.z);root.rotation.y=a.heading;this.group.add(root);
      this.items.push({root,mesh,mixer,idle,walk,weight:1,modelIndex,height:source.model.coordinate_system?.rest_height_m??1.75});
    }
    this.group.updateMatrixWorld(true);
  }
  animate(dt,agents,mode,paused){
    for(const row of this.frameCounts){row.animated=0;row.visible=0;row.moving=0;}
    if(mode==='static'||paused)return 0;
    for(let i=0;i<this.items.length;i++){
      const o=this.items[i],a=agents[i],speed=Math.hypot(a.vx,a.vz);
      const desired=mode==='animated'?1:Math.min(1,speed/.55);
      o.weight+=(desired-o.weight)*(1-Math.exp(-dt*8));
      o.walk.setEffectiveWeight(o.weight);o.idle.setEffectiveWeight(1-o.weight);
      o.walk.setEffectiveTimeScale(mode==='animated'?1:Math.max(.25,speed/1.2));
      o.mixer.update(dt);
      const counts=this.frameCounts[o.modelIndex];counts.animated++;if(speed>.1)counts.moving++;
    }
    return this.items.length;
  }
  sync(agents,alpha,dt){
    for(let i=0;i<this.items.length;i++){
      const a=agents[i],o=this.items[i];o.root.position.set(a.px+(a.x-a.px)*alpha,0,a.pz+(a.z-a.pz)*alpha);
      const diff=Math.atan2(Math.sin(a.heading-o.root.rotation.y),Math.cos(a.heading-o.root.rotation.y));
      o.root.rotation.y+=diff*(1-Math.exp(-dt*12));
    }
    this.group.updateMatrixWorld(true);
  }
  evidence(){
    const o=this.items[0],v=new THREE.Vector3();
    o.mesh.getVertexPosition(100,v);v.applyMatrix4(o.mesh.matrixWorld);
    return {bones:o.mesh.skeleton.bones.length,vertices:o.mesh.geometry.attributes.position.count,
      clips:this.asset.animations.map(c=>({name:c.name,duration:c.duration,tracks:c.tracks.length})),
      vertex:v.toArray(),boneQuaternion:o.mesh.skeleton.bones[4].quaternion.toArray(),
      phases:this.items.slice(0,5).map(x=>x.walk.time),
      independentSkeletons:new Set(this.items.map(x=>x.mesh.skeleton)).size,
      sharedGeometry:new Set(this.items.map(x=>x.mesh.geometry)).size,
      models:this.models.map((m,i)=>{
        const items=this.items.filter(o=>o.modelIndex===i),first=items[0],v=new THREE.Vector3();
        if(first){first.mesh.getVertexPosition(100,v);v.applyMatrix4(first.mesh.matrixWorld);}
        return {id:m.id,count:items.length,bones:m.bones,height:m.coordinate_system?.rest_height_m,
          geometryCount:new Set(items.map(o=>o.mesh.geometry)).size,skeletonCount:new Set(items.map(o=>o.mesh.skeleton)).size,
          vertex:first?v.toArray():null,phase:first?.walk.time,weight:first?.weight,
          clips:this.assets[i].asset.animations.map(c=>({name:c.name,duration:c.duration,tracks:c.tracks.length})),
          boneQuaternion:first?.mesh.skeleton.bones[4].quaternion.toArray()};
      })};
  }
}
