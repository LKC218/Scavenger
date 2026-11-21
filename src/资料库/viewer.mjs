import * as THREE from 'three';
import {createModelLoader,disposeModelLoader} from '/compressed-loader.mjs';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';

export function disposeTree(root){if(!root)return;const seen=new Set();root.traverse(object=>{if(object.geometry&&!seen.has(object.geometry)){seen.add(object.geometry);object.geometry.dispose();}for(const material of Array.isArray(object.material)?object.material:[object.material]){if(!material||seen.has(material))continue;seen.add(material);for(const value of Object.values(material)){if(value?.isTexture&&!seen.has(value)){seen.add(value);value.dispose();value.source?.data?.close?.();}}material.dispose();}});root.removeFromParent();}

export class ModelViewer{
 constructor(container,onState){this.container=container;this.onState=onState;this.generation=0;}
  initialize(){if(this.renderer)return;const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.35;this.renderer=renderer;this.container.append(renderer.domElement);renderer.domElement.setAttribute('aria-label','模型三维预览，拖动旋转，滚轮缩放');
  this.scene=new THREE.Scene();this.scene.background=new THREE.Color('#dfe6dc');this.camera=new THREE.PerspectiveCamera(40,1,.05,300);this.controls=new OrbitControls(this.camera,renderer.domElement);this.controls.minDistance=3;this.controls.maxDistance=70;this.controls.maxPolarAngle=Math.PI*.95;this.render=()=>renderer.render(this.scene,this.camera);this.controls.addEventListener('change',this.render);
  this.scene.add(new THREE.HemisphereLight('#f4fff7','#8b978b',3));const key=new THREE.DirectionalLight('#fff6e7',4);key.position.set(10,20,14);this.scene.add(key);const fill=new THREE.DirectionalLight('#dceeff',2);fill.position.set(-12,8,-12);this.scene.add(fill);this.grid=new THREE.GridHelper(80,40,'#adbbb3','#cbd4ce');this.scene.add(this.grid);
  this.resize=new ResizeObserver(()=>{const rect=this.container.getBoundingClientRect();if(rect.width&&rect.height){renderer.setSize(rect.width,rect.height,false);this.camera.aspect=rect.width/rect.height;this.camera.updateProjectionMatrix();this.render();}});this.resize.observe(this.container);this.reset();
 }
 profile(sceneMode){
  this.sceneMode=sceneMode;this.renderer.toneMappingExposure=sceneMode?1:1.35;this.grid.visible=!sceneMode;
  const lights=this.scene.children.filter(object=>object.isLight),levels=sceneMode?[1.4,2.5,.8]:[3,4,2];
  lights.forEach((light,index)=>{light.intensity=levels[index];});
 }
 reset(){if(this.streamingViewer){this.streamingViewer.reset();return;}if(!this.camera)return;if(this.sceneMode){this.camera.position.set(12,13,14);this.controls.target.set(0,0,0);}else{this.camera.position.set(20,13,22);this.controls.target.set(0,3,0);}this.controls.update();this.render();}
 clear(){this.generation++;this.abort?.abort();this.abort=null;this.streamingViewer?.dispose();this.streamingViewer=null;disposeTree(this.model);disposeTree(this.pendingRoot);this.model=null;this.pendingRoot=null;if(this.renderer)this.render();}
 releaseRenderer(){this.resize?.disconnect();this.controls?.dispose();disposeTree(this.scene);if(this.renderer){disposeModelLoader(this.renderer);this.renderer.dispose();this.renderer.forceContextLoss();this.renderer.domElement.remove();}this.renderer=null;this.scene=null;this.camera=null;this.controls=null;}
  async load(model){if(model.type==="scene")return this.loadScene(model);this.initialize();this.clear();this.profile(false);const generation=this.generation;const controller=new AbortController();this.abort=controller;this.onState('正在读取三维模型…',0);try{const response=await fetch(model.model_url,{signal:controller.signal});if(!response.ok)throw new Error('模型文件读取失败');const reader=response.body.getReader();const total=Number(response.headers.get('Content-Length'));const chunks=[];let received=0;while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);received+=value.length;if(generation!==this.generation){await reader.cancel();return false;}this.onState(total?`正在读取模型 ${Math.round(received/total*100)}%`:'正在读取模型…',total?received/total:null);}const buffer=new Uint8Array(received);let offset=0;for(const chunk of chunks){buffer.set(chunk,offset);offset+=chunk.length;}this.onState('正在还原材质与模型…',.94);const gltf=await createModelLoader(this.renderer).parseAsync(buffer.buffer,new URL('.',new URL(model.model_url,location.href)).href);if(generation!==this.generation){disposeTree(gltf.scene);return false;}
   const root=gltf.scene;const box=new THREE.Box3().setFromObject(root);const size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());const max=Math.max(size.x,size.y,size.z);if(!Number.isFinite(max)||max<=0){disposeTree(root);throw new Error('模型尺寸无效');}root.position.sub(center);root.position.y+=size.y/2;const normalized=new THREE.Group();normalized.add(root);normalized.scale.setScalar(14/max);this.model=normalized;this.scene.add(normalized);this.reset();this.onState('模型已就绪 · 拖动旋转，滚轮缩放',1);return true;
  }catch(error){if(generation!==this.generation||error.name==='AbortError')return false;this.onState('三维加载失败，可重试或继续查看预览图。',null);throw error;}
 }
 async loadScene(model){
  this.initialize();this.clear();this.profile(true);const generation=this.generation,controller=new AbortController();this.abort=controller;
  const root=new THREE.Group();this.pendingRoot=root;
  const alive=()=>generation===this.generation;
  const parse=async url=>{
   const response=await fetch(url,{signal:controller.signal});if(!response.ok)throw new Error(`场景资源读取失败：${response.status}`);
   const buffer=await response.arrayBuffer();if(!alive())return null;
   const gltf=await createModelLoader(this.renderer).parseAsync(buffer,new URL('.',url).href);
   if(!alive()){disposeTree(gltf.scene);return null;}return gltf.scene;
  };
  this.onState('正在读取场景装配…',0);
  try{
   const url=new URL(model.scene_url,location.href),response=await fetch(url,{signal:controller.signal});
   if(!response.ok)throw new Error('场景清单读取失败');const manifest=await response.json();if(!alive())return false;
   if(manifest.schema==='airbase-streaming-scene-v1'){
    const base=new URL(manifest.package_base||'.',url);if(!manifest.viewer_module||!manifest.runtime_module)throw new Error('场景缺少冻结的区域预览模块');
    const {StreamingSceneViewer}=await import(new URL(manifest.viewer_module,base).href);if(!alive())return false;
    this.pendingRoot=null;this.releaseRenderer();const viewer=new StreamingSceneViewer(this.container,(text,ratio)=>{if(alive())this.onState(text,ratio);});this.streamingViewer=viewer;return await viewer.load(url,manifest,controller.signal);
   }
   if(manifest.schema!=='airbase-reference-scene-v1')throw new Error('当前场景格式需要对应的区域运行预览器');
   const base=new URL(manifest.package_base||'.',url),ground=await parse(new URL(manifest.ground,base));
   if(!alive())return false;root.add(ground);
   const prototypes=Object.entries(manifest.prototypes),instances=new Map(prototypes.map(([key])=>[key,[]]));
   for(const item of manifest.instances){if(!instances.has(item.prototype))throw new Error('场景实例引用未闭合');instances.get(item.prototype).push(item);}
   let loaded=0;
   // A1引用样本按四个请求一组加载；全图流式场景使用独立格式，不预载到本入口。
   for(let start=0;start<prototypes.length;start+=4){
    const results=await Promise.allSettled(prototypes.slice(start,start+4).map(async([key,entry])=>{
     const prototype=await parse(new URL(entry.file,base));if(!prototype)return;
     for(const item of instances.get(key)){
      const object=prototype.clone(true);object.matrixAutoUpdate=false;object.matrix.fromArray(item.matrix);
      object.name=item.id;object.userData.assetId=item.asset_id;root.add(object);
     }
     if(!instances.get(key).length)disposeTree(prototype);
     loaded++;if(alive())this.onState(`正在装配场景 ${loaded}/${prototypes.length}`,loaded/(prototypes.length+1));
    }));
    const failure=results.find(row=>row.status==='rejected');if(failure)throw failure.reason;if(!alive())return false;
   }
   const box=new THREE.Box3().setFromObject(root),size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
   const maximum=Math.max(size.x,size.y,size.z);if(!Number.isFinite(maximum)||maximum<=0)throw new Error('场景尺寸无效');
   root.position.sub(center);root.position.y+=size.y/2;const normalized=new THREE.Group();normalized.add(root);normalized.scale.setScalar(14/maximum);
   this.pendingRoot=null;this.model=normalized;this.scene.add(normalized);this.reset();this.onState('引用场景已就绪 · 拖动旋转，滚轮缩放',1);return true;
  }catch(error){
   disposeTree(root);if(this.pendingRoot===root)this.pendingRoot=null;
   if(!alive()||error.name==='AbortError')return false;this.onState('场景加载失败，可重试并查看资源错误。',null);throw error;
  }
 }
 dispose(){this.clear();this.releaseRenderer();}
}
