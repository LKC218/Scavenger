// 资料库整图检验台：只显示源场景，加载和释放沿用有界区域运行模块。
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {disposeModelLoader} from '/compressed-loader.mjs';

export class StreamingSceneViewer{
 constructor(container,onState){this.container=container;this.onState=onState;this.baseSpan=58;this.disposed=false;this.abort=new AbortController();}
 initialize(){
  this.renderer=new THREE.WebGLRenderer({antialias:false});this.renderer.setPixelRatio(1);this.renderer.outputColorSpace=THREE.SRGBColorSpace;this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1;
  this.renderer.domElement.setAttribute('aria-label','整图三维预览：拖动旋转，右键平移，滚轮缩放');this.container.append(this.renderer.domElement);
  this.scene=new THREE.Scene();this.scene.background=new THREE.Color('#b9c5b4');this.scene.add(new THREE.HemisphereLight('#eef6ef','#76806a',1.4));const sun=new THREE.DirectionalLight('#fff2db',2.5);sun.position.set(220,420,180);this.scene.add(sun);
  this.camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,200000);this.camera.position.set(0,330,280);
  this.controls=new OrbitControls(this.camera,this.renderer.domElement);this.controls.enableDamping=false;this.controls.screenSpacePanning=false;this.controls.minPolarAngle=.01;this.controls.maxPolarAngle=Math.PI*.22;
  this.controls.addEventListener('change',()=>this.draw());
  this.toolbar=document.createElement('div');this.toolbar.className='scene-tools';this.toolbar.setAttribute('aria-label','场景视角');this.tier=document.createElement('span');this.tier.className='scene-tier';this.tier.setAttribute('aria-live','polite');this.toolbar.append(this.tier);
  for(const [action,label]of [['home','机场近景'],['overview','全图总览'],['retry','重试细节']]){const button=document.createElement('button');button.type='button';button.dataset.sceneAction=action;button.textContent=label;button.addEventListener('click',event=>{event.stopPropagation();action==='home'?this.reset():action==='overview'?this.overview():this.map?.retry();});this.toolbar.append(button);if(action==='retry'){this.retry=button;button.hidden=true;}}
  this.container.append(this.toolbar);this.resize=new ResizeObserver(()=>this.fit());this.resize.observe(this.container);this.fit();
 }
 fit(){if(this.disposed||!this.renderer)return;let rect=this.container.getBoundingClientRect();if(!rect.width||!rect.height)rect=this.container.parentElement.getBoundingClientRect();if(!rect.width||!rect.height)return;const height=Math.min(rect.height,rect.width/1.75);this.aspect=rect.width/height;this.renderer.setSize(640,Math.max(1,Math.round(640/this.aspect)),false);Object.assign(this.renderer.domElement.style,{position:'absolute',left:'0',top:(rect.height-height)/2+'px',width:rect.width+'px',height:height+'px'});this.camera.left=-this.baseSpan*this.aspect/2;this.camera.right=this.baseSpan*this.aspect/2;this.camera.top=this.baseSpan/2;this.camera.bottom=-this.baseSpan/2;this.camera.updateProjectionMatrix();this.draw();}
 pose(){
  if(!this.map)return;const map=this.map,b=map.bounds,target=this.controls.target,x=THREE.MathUtils.clamp(target.x,b.minX+7,b.maxX-7),z=THREE.MathUtils.clamp(target.z,b.minZ+7,b.maxZ-7),direction=this.camera.position.clone().sub(target).normalize();
  const ground=map.coarseHeightAt(x,z);target.set(x,Number.isFinite(ground)?ground:target.y,z);
  const span=this.baseSpan/this.camera.zoom,minimum=(map.safeAltitude??200)+40-target.y,desired=Math.max(span/180*Math.hypot(165,140),minimum/Math.max(.1,direction.y)),distance=Math.min(desired,(map.camera.max_height_m??9000)/Math.max(.1,direction.y));
  this.camera.position.copy(target).addScaledVector(direction,distance);this.camera.lookAt(target);this.camera.updateMatrixWorld();
 }
 view(){return{center:{x:this.controls.target.x,z:this.controls.target.z},span:this.baseSpan/this.camera.zoom*(this.aspect??1),height:this.camera.position.y-this.controls.target.y,camera:this.camera};}
 draw(){if(this.disposed||!this.renderer)return;if(this.map){this.pose();this.map.update(this.view());}this.renderer.render(this.scene,this.camera);}
 reset(){if(!this.map)return;const p=this.map.center,h=this.map.coarseHeightAt(p.x,p.z);this.controls.target.set(p.x,h,p.z);this.camera.zoom=1;this.camera.position.copy(this.controls.target).add(new THREE.Vector3(0,330,280));this.camera.updateProjectionMatrix();this.controls.update();this.draw();}
 overview(){if(!this.map)return;const b=this.map.bounds,x=(b.minX+b.maxX)/2,z=(b.minZ+b.maxZ)/2;this.controls.target.set(x,this.map.coarseHeightAt(x,z),z);this.camera.position.copy(this.controls.target).add(new THREE.Vector3(0,330,280));this.camera.zoom=this.baseSpan/(this.map.maxSpan??12150);this.camera.updateProjectionMatrix();this.controls.update();this.draw();}
 async load(url,manifest,parentSignal){
  this.initialize();const cancel=()=>this.dispose();parentSignal?.addEventListener('abort',cancel,{once:true});this.unlink=()=>parentSignal?.removeEventListener('abort',cancel);
  if(parentSignal?.aborted){this.dispose();return false;}
  const base=new URL(manifest.package_base??'.',url),moduleURL=new URL(manifest.runtime_module,base);
  const module=await import(moduleURL.href);if(this.disposed)return false;
  const supplied={...manifest,initial_view_span:this.baseSpan*(this.aspect??1)};
  try{
   const map=await module.loadFullScene(url,this.renderer,message=>{if(!this.disposed)this.onState(message,null);},supplied,{signal:this.abort.signal,onCreate:map=>this.pendingMap=map});
   if(this.disposed){map.dispose();return false;}this.map=map;this.pendingMap=null;this.scene.add(map.root);this.controls.minZoom=this.baseSpan/(map.maxSpan??12150);this.controls.maxZoom=1;this.reset();await map.waitForView();this.draw();
   let next=0;const tick=now=>{if(this.disposed)return;this.draw();if(now>=next){next=now+250;const s=map.stats;this.tier.textContent=s.pending?'准备细节':s.sceneLevel==='far'?'远景':s.sceneLevel==='middle'?'中景':'近景';this.retry.hidden=!!s.pending||!s.lastError;this.onState(s.lastError?'细节暂不可用，已保留总览；可重试。':s.pending?'正在准备当前区域，已保留总览。':'场景已就绪 · 拖动旋转，右键平移，滚轮缩放',1);}this.animation=requestAnimationFrame(tick);};this.animation=requestAnimationFrame(tick);return true;
  }catch(error){if(this.disposed||error.name==='AbortError')return false;this.onState('场景加载失败，可重新加载或查看预览图。',null);this.dispose();throw error;}
 }
 dispose(){if(this.disposed)return;this.disposed=true;this.unlink?.();cancelAnimationFrame(this.animation);this.abort.abort();this.map?.dispose();this.pendingMap?.dispose();this.map=this.pendingMap=null;this.resize?.disconnect();this.controls?.dispose();this.toolbar?.remove();if(this.renderer){disposeModelLoader(this.renderer);this.renderer.dispose();this.renderer.forceContextLoss();this.renderer.domElement.remove();this.renderer=null;}}
}
