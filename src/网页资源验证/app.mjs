import * as T from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {KTX2Loader} from 'three/addons/loaders/KTX2Loader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const renderer=new T.WebGLRenderer({antialias:false,preserveDrawingBuffer:true});renderer.setSize(640,360);renderer.setPixelRatio(1);renderer.outputColorSpace=T.SRGBColorSpace;
document.querySelector('#viewport').append(renderer.domElement);
const scene=new T.Scene();scene.background=new T.Color('#26343b');scene.add(new T.HemisphereLight(0xffffff,0x586468,3));const key=new T.DirectionalLight(0xfff2df,4);key.position.set(10,20,10);scene.add(key);
const camera=new T.PerspectiveCamera(40,640/360,.01,10000);const controls=new OrbitControls(camera,renderer.domElement);
const ktx=new KTX2Loader().setTranscoderPath('/vendor/examples/jsm/libs/basis/').detectSupport(renderer);
const loader=new GLTFLoader().setKTX2Loader(ktx).setMeshoptDecoder(MeshoptDecoder);
let current,mixer,clip;const clock=new T.Clock();
function dispose(root){if(!root)return;const done=new Set();root.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:[o.material]){if(!m)continue;for(const v of Object.values(m))if(v?.isTexture&&!done.has(v)){done.add(v);v.dispose();v.source?.data?.close?.();}m.dispose();}});scene.remove(root);}
renderer.setAnimationLoop(()=>{mixer?.update(Math.min(clock.getDelta(),.05));controls.update();renderer.render(scene,camera);});
const catalog=await(await fetch('/catalog.json')).json();window.catalog=catalog;
const select=document.querySelector('#model'),audio=document.querySelector('#audio'),status=document.querySelector('#status');
for(const r of catalog.models){const o=document.createElement('option');o.value=r.source_sha256;o.textContent=r.name;select.append(o);}
for(const r of catalog.audio){const o=document.createElement('option');o.value=r.url;o.textContent=r.name;audio.append(o);}
audio.onchange=()=>{document.querySelector('#player').src=audio.value;};audio.onchange();
async function show(id,optimized=true){
 const item=catalog.models.find(x=>x.source_sha256===id);if(!item)throw Error('未找到模型');
 dispose(current);current=null;mixer=null;
 const started=performance.now(),g=await loader.loadAsync(optimized?item.url:item.original_url);current=g.scene;scene.add(current);scene.updateMatrixWorld(true);
 const box=new T.Box3().setFromObject(current),center=box.getCenter(new T.Vector3()),size=box.getSize(new T.Vector3());
 const scale=Math.max(size.x,size.y,size.z);if(!Number.isFinite(scale)||scale<=0)throw Error('包围盒无效');
 controls.target.copy(center);camera.position.copy(center).add(new T.Vector3(scale*.95,scale*.8,scale*.95));camera.near=Math.max(scale/1000,.001);camera.far=Math.max(scale*50,100);camera.updateProjectionMatrix();controls.update();
 if(g.animations.length){mixer=new T.AnimationMixer(current);clip=g.animations[0];mixer.clipAction(clip).play();mixer.setTime(clip.duration*.37);}
 renderer.render(scene,camera);
 let meshes=0,skinned=0,triangles=0,textures=new Set();current.traverse(o=>{if(o.isMesh){meshes++;if(o.isSkinnedMesh)skinned++;triangles+=(o.geometry.index?.count||o.geometry.attributes.position.count)/3;for(const m of Array.isArray(o.material)?o.material:[o.material])for(const v of Object.values(m||{}))if(v?.isTexture)textures.add(v);}});
 const result={id,optimized,load_ms:performance.now()-started,meshes,skinned,triangles,animations:g.animations.map(a=>({name:a.name,duration:a.duration,tracks:a.tracks.length})),textures:textures.size,size:size.toArray(),renderer:renderer.info.render};
 status.textContent=`${item.name}\n${optimized?'优化版':'原版'} · ${(optimized?item.bytes:item.source_bytes)/1048576|0} MiB\n网格 ${meshes} · 三角形 ${triangles|0}\n蒙皮 ${skinned} · 动作 ${g.animations.length}\n加载 ${result.load_ms.toFixed(1)} ms\n原有待修状态请见报告`;
 window.lastResult=result;return result;
}
document.querySelector('#optimized').onclick=()=>show(select.value).catch(e=>status.textContent=e.stack);
document.querySelector('#original').onclick=()=>show(select.value,false).catch(e=>status.textContent=e.stack);
window.validation={show,renderer,scene,camera,get current(){return current;},stable(){renderer.setAnimationLoop(null);},sample(t){mixer?.setTime(t);scene.updateMatrixWorld(true);renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png');},pose(t){mixer?.setTime(t);scene.updateMatrixWorld(true);const result=[];current.traverse(o=>{if(!o.isSkinnedMesh)return;o.skeleton.update();const a=o.geometry.attributes.position;for(let i=0;i<a.count;i++){const v=new T.Vector3().fromBufferAttribute(a,i);o.applyBoneTransform(i,v);v.applyMatrix4(o.matrixWorld);result.push(v.x,v.y,v.z);}});return result;},pixels(){const gl=renderer.getContext(),p=new Uint8Array(640*360*4);gl.readPixels(0,0,640,360,gl.RGBA,gl.UNSIGNED_BYTE,p);return Array.from(p);}};
window.ready=true;if(select.value)await show(select.value);
window.validation.silhouette=(t)=>{
 mixer?.setTime(t);scene.updateMatrixWorld(true);
 const previous=scene.background,material=new T.MeshBasicMaterial({color:0xffffff,side:T.DoubleSide});
 scene.background=new T.Color(0x000000);scene.overrideMaterial=material;renderer.render(scene,camera);
 const image=renderer.domElement.toDataURL('image/png');scene.overrideMaterial=null;scene.background=previous;material.dispose();renderer.render(scene,camera);return image;
};
