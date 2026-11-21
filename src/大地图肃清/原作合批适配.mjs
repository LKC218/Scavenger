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

/** 只在加载时烘焙16个原作姿态画面；运行期不为替身逐只计算骨架。 */
function bakeSilhouettes(rig,renderer){
 const target=new THREE.WebGLRenderTarget(512,256,{format:THREE.RGBAFormat,type:THREE.UnsignedByteType,depthBuffer:true});target.texture.minFilter=THREE.LinearFilter;target.texture.magFilter=THREE.LinearFilter;target.texture.generateMipmaps=false;
 const preview=new THREE.Scene(),root=clone(rig.asset.scene),mixer=new THREE.AnimationMixer(root),camera=new THREE.OrthographicCamera(-1.15,1.15,1.15,-1.15,.1,20);preview.add(root,new THREE.HemisphereLight(0xe2ead6,0x364230,2.4));const sun=new THREE.DirectionalLight(0xfff1cf,2.1);sun.position.set(-3,5,4);preview.add(sun);camera.position.set(0,1.08,5);camera.lookAt(0,1.05,0);
 const oldTarget=renderer.getRenderTarget(),oldViewport=renderer.getViewport(new THREE.Vector4()),oldScissor=renderer.getScissor(new THREE.Vector4()),oldScissorTest=renderer.getScissorTest(),oldColor=renderer.getClearColor(new THREE.Color()),oldAlpha=renderer.getClearAlpha(),autoClear=renderer.autoClear;
 try{renderer.setRenderTarget(target);renderer.setClearColor(0x000000,0);renderer.setScissorTest(true);renderer.autoClear=false;
  for(let pose=0;pose<2;pose++)for(let direction=0;direction<4;direction++){
   mixer.stopAllAction();const name=rig.model.states[pose?'move':'idle'],clip=rig.asset.animations.find(c=>c.name===name),action=mixer.clipAction(clip);action.play();mixer.setTime(pose?clip.duration*.28:clip.duration*.17);root.rotation.y=direction*Math.PI/2;root.updateMatrixWorld(true);renderer.setViewport(direction*128,pose*128,128,128);renderer.setScissor(direction*128,pose*128,128,128);renderer.clear(true,true,true);renderer.render(preview,camera);
  }
 }finally{renderer.setRenderTarget(oldTarget);renderer.setViewport(oldViewport);renderer.setScissor(oldScissor);renderer.setScissorTest(oldScissorTest);renderer.setClearColor(oldColor,oldAlpha);renderer.autoClear=autoClear;mixer.stopAllAction();mixer.uncacheRoot(root);root.traverse(o=>{if(o.isSkinnedMesh)o.skeleton.dispose();});}
 return target;
}
function makeSilhouetteBatch(rig){
 const geometry=new THREE.PlaneGeometry(2.3,2.3),tile=new THREE.InstancedBufferAttribute(new Float32Array(5000*2),2);tile.setUsage(THREE.DynamicDrawUsage);geometry.setAttribute('proxyTile',tile);
 const material=new THREE.MeshBasicMaterial({map:rig.silhouettes.texture,alphaTest:.18,depthWrite:true,toneMapped:false});material.onBeforeCompile=shader=>{shader.vertexShader='attribute vec2 proxyTile;\n'+shader.vertexShader;shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nvMapUv=(vMapUv+proxyTile)/vec2(4.0,2.0);');};material.customProgramCacheKey=()=> '肃清原作轮廓图集V001';
 const mesh=new THREE.InstancedMesh(geometry,material,5000);mesh.frustumCulled=false;mesh.count=0;mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);return{mesh,tile,count:0};
}
function makeFootShadows(){
 const geometry=new THREE.PlaneGeometry(1,1),material=new THREE.ShaderMaterial({transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1,vertexShader:'varying vec2 footUv;void main(){footUv=uv;gl_Position=projectionMatrix*modelViewMatrix*instanceMatrix*vec4(position,1.0);}',fragmentShader:'varying vec2 footUv;void main(){float radial=length(footUv-vec2(.5))*2.0;float opacity=(1.0-smoothstep(.15,1.0,radial))*.23;gl_FragColor=vec4(.025,.04,.02,opacity);}' });
 const mesh=new THREE.InstancedMesh(geometry,material,5200);mesh.name='尸群近景合批接地阴影';mesh.frustumCulled=false;mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);mesh.count=0;mesh.renderOrder=1;return mesh;
}

/** 源合批SHA3283abd9753e644c944e66dac4246d69297a47526a42deb648bbcf2faa6e7f76。
 * 只复用姿态烘焙和批材质；本类按稳定逻辑ID读取同一快照，不回写规则。 */
export class NativeCrowd {
 constructor(scene,config,world){this.config=config;this.world=world;this.group=new THREE.Group();scene.add(this.group);this.temp=new THREE.Object3D();this.matrix=new THREE.Matrix4();this.color=new THREE.Color();this.byId=new Map();this.proxyMembership=new Map();this.footShadows=makeFootShadows();this.group.add(this.footShadows);this.footTemp=new THREE.Object3D();this.footNormal=new THREE.Vector3();this.footAxis=new THREE.Vector3(0,0,1);}
 async load(renderer,manifest){
  this.rigs=[];const loader=createModelLoader(renderer);
  for(const model of manifest.models){
   const asset=await loader.loadAsync(model.url);asset.scene.updateMatrixWorld(true);const meshes=[];asset.scene.traverse(o=>{if(o.isSkinnedMesh)meshes.push(o);});
   if(meshes.length!==1)throw Error(model.id+'必须只有一个有效LOD蒙皮网格');const base=meshes[0];
   if(base.skeleton.bones.length!==model.bones||(base.geometry.index?.count??base.geometry.attributes.position.count)/3!==model.triangles)throw Error(model.id+'骨架或几何身份不符');
   for(const expected of model.clips){const clip=asset.animations.find(c=>c.name===expected.name);if(!clip||Math.abs(clip.duration-expected.duration_s)>.001)throw Error(model.id+'动作不符');}
   const rig={model,base,asset,...await bake(asset,base,renderer)};rig.live=makeBatch(rig,5000,false);rig.dead=makeBatch(rig,this.config.render.corpseLimit,true);rig.silhouettes=bakeSilhouettes(rig,renderer);rig.proxy=makeSilhouetteBatch(rig);this.group.add(rig.live.mesh,rig.dead.mesh,rig.proxy.mesh);this.rigs.push(rig);
  }
 }
 reset(){this.byId.clear();this.proxyMembership.clear();this.footShadows.count=0;for(const r of this.rigs)r.live.mesh.count=r.dead.mesh.count=r.proxy.mesh.count=0;}
 update(snapshot,view){
  const settings=this.config.render,time=snapshot.time,force=this.config.presets[snapshot.preset]?.forceIndividuals||view.forceIndividuals;
  const corpses=snapshot.zombies.filter(z=>!z.alive&&z.deathAt!=null&&time-z.deathAt<=settings.corpseLife&&z.visible!==false).sort((a,b)=>b.deathAt-a.deathAt).slice(0,settings.corpseLimit);
  const corpsesIds=new Set(corpses.map(z=>z.id)),groups=new Map();let individuals=0,animated=0,hidden=0,live=0,onScreenIndividuals=0,onScreenProxyMembers=0,offscreenAlive=0,offscreenCombat=0,footShadowCount=0;
  for(const r of this.rigs)r.live.count=r.dead.count=r.proxy.count=0;this.proxyMembership.clear();
  const activeIds=new Set();
  for(const z of snapshot.zombies){
   activeIds.add(z.id);if(z.visible===false){hidden++;continue;}if(!z.alive&&!corpsesIds.has(z.id))continue;
   const rig=this.rigs.find(r=>r.model.id===z.modelId);if(!rig)throw Error('未登记原作角色：'+z.modelId);
   const current=z.position,previous=z.previousPosition??current,alpha=view.alpha??1,p={x:previous.x+(current.x-previous.x)*alpha,y:previous.y+(current.y-previous.y)*alpha,z:previous.z+(current.z-previous.z)*alpha};if(!Number.isFinite(p.y))throw Error('尸群高度必须来自规则支撑：'+z.id);
   if(z.alive)live++;
   const d=Math.hypot(p.x-view.focus.x,p.z-view.focus.z),pixels=view.pixelsPerMeter*rig.model.coordinate_system.rest_height_m,onScreen=view.isOnScreen?.(p,1.2)??true;
   let item=this.byId.get(z.id);if(!item){item={state:null,start:time,lod:d>settings.far||pixels<settings.tinyPixels?'proxy':'individual'};this.byId.set(z.id,item);}
   if(force)item.lod='individual';else if(item.lod==='individual'&&(d>settings.far||pixels<settings.tinyPixels))item.lod='proxy';else if(item.lod==='proxy'&&d<settings.near&&pixels>=settings.tinyPixels*1.3)item.lod='individual';
   if(z.alive&&!onScreen){offscreenAlive++;if(z.behaviorState==='attack')offscreenCombat++;}
   if(z.alive&&!force&&item.lod==='proxy'){
    if(onScreen)onScreenProxyMembers++;
    const key=rig.model.id+':'+Math.floor(p.x/12)+':'+Math.floor(p.z/12);let g=groups.get(key);if(!g){g={ids:[]};groups.set(key,g);}g.ids.push(z.id);
    const proxy=rig.proxy,index=proxy.count++;this.temp.position.set(p.x,p.y+1.05,p.z);this.temp.quaternion.copy(view.billboardQuaternion);this.temp.scale.setScalar(z.scale??1);this.temp.updateMatrix();proxy.mesh.setMatrixAt(index,this.temp.matrix);const direction=((Math.round(((z.yaw??0)+Math.PI-view.cameraYaw)/(Math.PI/2))%4)+4)%4,pose=z.behaviorState==='idle'?0:1;proxy.tile.setXY(index,direction,pose);continue;
   }
   const state=!z.alive?'death':z.behaviorState==='attack'?'attack':(['idle','dormant','stopped','rest'].includes(z.behaviorState)?'idle':'move');
   if(d<=settings.near){
    const hx=this.world.heightAt(p.x+.4,p.z),hz=this.world.heightAt(p.x,p.z+.4),h=this.world.heightAt(p.x,p.z);
    if([hx,hz,h].every(Number.isFinite)){this.footNormal.set(-(hx-h)/.4,1,-(hz-h)/.4).normalize();this.footTemp.position.set(p.x,p.y+.035,p.z);this.footTemp.quaternion.setFromUnitVectors(this.footAxis,this.footNormal);this.footTemp.scale.set(z.alive?.9:1.6,z.alive?.68:.9,1);this.footTemp.updateMatrix();this.footShadows.setMatrixAt(footShadowCount++,this.footTemp.matrix);}
   }
   if(item.state!==state){item.state=state;item.start=time;}if(z.alive&&onScreen)onScreenIndividuals++;
   let local=state==='death'?time-z.deathAt:time-(z.stateSince??item.start)+(z.phase??0);if(d>settings.near&&!force&&state!=='death')local=Math.floor(local*10)/10;
   const batch=state==='death'?rig.dead:rig.live,index=batch.count++;if(index>=batch.capacity)throw Error('实例容量不足，禁止静默少画');
   this.temp.position.set(p.x,p.y,p.z);this.temp.rotation.set(0,(z.yaw??0)+Math.PI,0);this.temp.scale.setScalar(z.scale??1);this.temp.updateMatrix();this.matrix.multiplyMatrices(this.temp.matrix,rig.meshMatrix);batch.mesh.setMatrixAt(index,this.matrix);
   const clip=rig.clips[rig.model.states[state]];if(!clip)throw Error('缺少原作动作：'+state);const rows=frameRows(clip,local,state!=='death');
   const opacity=state==='death'?Math.min(1,Math.max(0,(settings.corpseLife-local)/settings.corpseFade)):1;
   batch.a.setXYZW(index,...rows,1);batch.b.setXYZW(index,...rows,opacity);this.color.setScalar(1);batch.mesh.setColorAt(index,this.color);individuals++;if(z.alive)animated++;
  }
  for(const id of this.byId.keys())if(!activeIds.has(id))this.byId.delete(id);
  let members=0;for(const[key,g]of groups){this.proxyMembership.set(key,g.ids);members+=g.ids.length;}
  this.footShadows.count=footShadowCount;let calls=footShadowCount?1:0;if(footShadowCount){const attr=this.footShadows.instanceMatrix;attr.clearUpdateRanges();attr.addUpdateRange(0,footShadowCount*16);attr.needsUpdate=true;}
  for(const r of this.rigs){const p=r.proxy;p.mesh.count=p.count;if(p.count){calls++;for(const[attr,count]of[[p.mesh.instanceMatrix,p.count*16],[p.tile,p.count*2]]){attr.clearUpdateRanges();attr.addUpdateRange(0,count);attr.needsUpdate=true;}}for(const b of[r.live,r.dead]){b.mesh.count=b.count;if(!b.count)continue;calls++;for(const[attr,count]of[[b.mesh.instanceMatrix,b.count*16],[b.mesh.instanceColor,b.count*3],[b.a,b.count*4],[b.b,b.count*4]]){attr.clearUpdateRanges();attr.addUpdateRange(0,count);attr.needsUpdate=true;}}}
  this.stats={live,individuals,animated,corpses:corpses.length,footShadows:footShadowCount,footShadowCalls:footShadowCount?1:0,footShadowMethod:'近100米支撑面合批软圆片',proxyGroups:groups.size,proxyMembers:members,hidden:snapshot.counts?.hidden??hidden,offscreenAlive,offscreenCombat,onScreenIndividuals,onScreenProxyMembers,batchCalls:calls,forceIndividuals:!!force,identityPreserved:individuals+members===live+corpses.length,atlasBytes:this.rigs.reduce((s,r)=>s+r.values.byteLength,0),proxyAtlasBytes:this.rigs.length*512*256*4,proxyTriangles:members*2,proxyMethod:'原作4朝向2姿态RGBA图集实例平面'};
 }
 dispose(){for(const r of this.rigs){r.atlas.dispose();r.silhouettes.dispose();for(const b of[r.live,r.dead,r.proxy]){b.mesh.geometry.dispose();b.mesh.material.dispose();}}this.footShadows.geometry.dispose();this.footShadows.material.dispose();this.group.removeFromParent();}
}

