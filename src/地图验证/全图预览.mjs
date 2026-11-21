import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {disposeModelLoader} from '/compressed-loader.mjs';
import {loadFullScene} from './全图显示.mjs';
const $=s=>document.querySelector(s),canvas=$('#scene');
const renderer=new THREE.WebGLRenderer({canvas,antialias:false});renderer.setPixelRatio(1);renderer.setSize(640,364,false);
renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1;
const scene=new THREE.Scene();scene.background=new THREE.Color('#b9c5b4');
scene.add(new THREE.HemisphereLight('#eef6ef','#76806a',1.4));const sun=new THREE.DirectionalLight('#fff2db',2.5);sun.position.set(220,420,180);scene.add(sun);
const camera=new THREE.PerspectiveCamera(40,innerWidth/innerHeight,.1,200000),controls=new OrbitControls(camera,canvas);
controls.minDistance=15;controls.maxDistance=25000;controls.maxPolarAngle=Math.PI*.47;controls.enableDamping=false;controls.screenSpacePanning=false;
let map,frame=0,last=performance.now(),samples=[],alive=true;
const limitCamera=()=>{
 if(map){const b=map.bounds,x=THREE.MathUtils.clamp(controls.target.x,b.minX,b.maxX),z=THREE.MathUtils.clamp(controls.target.z,b.minZ,b.maxZ);camera.position.x+=x-controls.target.x;camera.position.z+=z-controls.target.z;controls.target.x=x;controls.target.z=z;
  const exact=map.querySupport(x,z),ground=exact.state==='ready'?exact.height:map.coarseHeightAt(x,z);if(Number.isFinite(ground)){camera.position.y+=ground-controls.target.y;controls.target.y=ground;}
  const under=map.coarseHeightAt(camera.position.x,camera.position.z);if(Number.isFinite(under))camera.position.y=Math.max(camera.position.y,under+12);
 }
 const max=map?.camera?.max_height_m??9000,height=camera.position.y-controls.target.y;if(height>max){const offset=camera.position.clone().sub(controls.target).multiplyScalar(max/height);camera.position.copy(controls.target).add(offset);}
};
const setCamera=(x,z,span)=>{const distance=span/(2*Math.tan(THREE.MathUtils.degToRad(20))*camera.aspect),ground=map?.coarseHeightAt(x,z);if(!Number.isFinite(ground))throw new Error('该位置尚无有效相机地面');controls.target.set(x,ground,z);camera.position.set(x,ground+distance*.8,z+distance*.6);limitCamera();controls.update();map.update(state());return map.waitForView();};
controls.addEventListener('change',limitCamera);
const state=()=>({center:{x:controls.target.x,z:controls.target.z},height:camera.position.y-controls.target.y,span:camera.position.distanceTo(controls.target)*2*Math.tan(THREE.MathUtils.degToRad(20))*camera.aspect,camera});
const perf=()=>{const sorted=[...samples].sort((a,b)=>a-b),q=p=>sorted[Math.min(sorted.length-1,Math.floor(sorted.length*p))]??0;return {frames:samples.length,p95:q(.95),p99:q(.99),drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,geometries:renderer.info.memory.geometries,textures:renderer.info.memory.textures,jsHeap:performance.memory?.usedJSHeapSize??null,camera:{center:state().center,height:state().height,span:state().span},mapStats:{...map?.stats},internal:[canvas.width,canvas.height],viewport:[innerWidth,innerHeight]};};
try{
 const manifest=await (await fetch('/manifest.json')).json();
 map=await loadFullScene(manifest.mapURL,renderer,text=>$('#load-status').textContent=text);scene.add(map.root);setCamera(map.center.x,map.center.z,map.defaultSpan);
 for(let z=0;z<9;z++)for(let x=0;x<9;x++)$('#region').add(new Option(`${String(x+1).padStart(2,'0')} / ${String(z+1).padStart(2,'0')} 分区`,`${x}:${z}`));
 $('#home').onclick=()=>setCamera(map.center.x,map.center.z,map.defaultSpan);
 $('#overview').onclick=()=>setCamera((map.bounds.minX+map.bounds.maxX)/2,(map.bounds.minZ+map.bounds.maxZ)/2,14000);
 $('#retry').onclick=()=>map.retry();
 $('#region').onchange=()=>{if(!$('#region').value)return;const [x,z]=$('#region').value.split(':').map(Number);setCamera(map.bounds.minX+x*1000+500,map.bounds.minZ+z*1000+500,1250);};
 window.fullMap={ready:true,map,scene,camera,controls,renderer,perf,setCamera,resetMetrics:()=>{samples=[];},dispose:()=>{alive=false;map.dispose();controls.dispose();disposeModelLoader(renderer);renderer.dispose();}};
 $('#loading').hidden=true;
 const animate=now=>{if(!alive)return;const dt=now-last;last=now;if(dt>0&&samples.length<60000)samples.push(dt);map.update(state());renderer.render(scene,camera);
  if(frame++%15===0){const s=map.stats;$('#status').textContent=s.pending?`正在准备区域 · ${s.pending} 块`:s.overview?'正在显示全图总览':'局部细节已就绪';$('#metrics').textContent=`地表 ${s.visibleChunks} / 49 块 · 静态实例 ${s.instanceCount} · 树木 ${s.visibleTrees}\n几何 ${(s.geometryBytes/1048576).toFixed(1)} MiB · 纹理 ${(s.textureBytes/1048576).toFixed(1)} MiB\n精细数据 ${(s.dataBytes/1048576).toFixed(1)} MiB · 请求 ${s.requests} / 4`;$('#error').textContent=s.lastError??'';}
  requestAnimationFrame(animate);
 };requestAnimationFrame(animate);
 addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();});addEventListener('pagehide',()=>window.fullMap.dispose(),{once:true});
}catch(error){console.error(error);$('#load-status').textContent='预览加载失败：'+error.message;$('#status').textContent='请检查本轮服务及资源错误';}
