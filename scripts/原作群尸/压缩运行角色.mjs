// 仅压缩容器，独立回读核对骨架、动画与全部几何属性，保留未压缩试导。
import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import{pathToFileURL}from'node:url';
const root=path.resolve(import.meta.dirname,'../..'),batch=path.join(root,'local-data/断箭/批次/BA-26273833-014'),modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules');
const load=p=>import(pathToFileURL(path.join(modules,p)).href);
const{NodeIO}=await load('@gltf-transform/core/dist/index.js');const{ALL_EXTENSIONS,EXTMeshoptCompression}=await load('@gltf-transform/extensions/dist/index.js');const{MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function signature(doc){const r=doc.getRoot(),nodes=r.listNodes(),a=x=>x?Array.from(x.getArray()):null;return{
 nodes:nodes.map(n=>[n.getName(),n.getTranslation(),n.getRotation(),n.getScale(),n.listChildren().map(x=>nodes.indexOf(x))]),
 skins:r.listSkins().map(s=>[s.listJoints().map(j=>nodes.indexOf(j)),a(s.getInverseBindMatrices())]),
 animations:r.listAnimations().map(x=>[x.getName(),x.listChannels().map(c=>[nodes.indexOf(c.getTargetNode()),c.getTargetPath()]),x.listSamplers().map(s=>[s.getInterpolation(),a(s.getInput()),a(s.getOutput())])]),
 meshes:r.listMeshes().map(m=>m.listPrimitives().map(p=>{const ix=p.getIndices().getArray(),tris=[];for(let i=0;i<ix.length;i+=3){const t=Array.from(ix.slice(i,i+3)),k=t.indexOf(Math.min(...t));tris.push([...t.slice(k),...t.slice(0,k)]);}return[tris.sort((x,y)=>x[0]-y[0]||x[1]-y[1]||x[2]-y[2]),p.listSemantics().sort().map(k=>[k,a(p.getAttribute(k))])];}))};}
function diff(a,b,p=''){if(typeof a==='number'&&typeof b==='number')return Math.abs(a-b)>1e-5?{p,a,b}:null;if(a===b)return null;if(!a||!b||typeof a!==typeof b||Object.keys(a).length!==Object.keys(b).length)return{p};for(const k of Object.keys(a)){const d=diff(a[k],b[k],p+'/'+k);if(d)return d;}return null;}
for(const role of ['Walker','Runner']){
 const source=path.join(batch,role,role+'-LOD2-256.glb'),target=path.join(batch,role,role+'-LOD2-256-Meshopt-r03.glb');if(fs.existsSync(target))throw Error('运行候选已存在');
 const doc=await io.read(source),inputs=new Map(),timeOrigins=[];
 for(const animation of doc.getRoot().listAnimations()){const origin=Math.min(...animation.listSamplers().map(s=>s.getInput().getArray()[0]));timeOrigins.push({name:animation.getName(),removed_offset_s:origin});for(const sampler of animation.listSamplers()){const input=sampler.getInput();if(inputs.has(input)&&Math.abs(inputs.get(input)-origin)>1e-6)throw Error('共享时间访问器的起点不一致');inputs.set(input,origin);}}
 for(const[input,origin]of inputs)input.setArray(input.getArray().map(t=>t-origin));
 const removed=[];
 // 原作网格没有顶点色；Blender留档的浮点切线被导出器自动当成COLOR属性，运行材质不得使用它。
 for(const mesh of doc.getRoot().listMeshes())for(const primitive of mesh.listPrimitives())for(const semantic of primitive.listSemantics())if(semantic.startsWith('COLOR_')){const accessor=primitive.getAttribute(semantic);primitive.setAttribute(semantic,null);if(!accessor.listParents().some(p=>p.propertyType!=='Root'))accessor.dispose();removed.push(semantic);}
 const before=signature(doc);doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});await io.write(target,doc);
 const difference=diff(before,signature(await io.read(target)));if(difference)throw Error('压缩回读不符：'+JSON.stringify(difference));const bytes=fs.readFileSync(target);
 const oldReceipt=path.join(batch,role,'压缩回执.json'),history=path.join(batch,role,'压缩回执-r01.json');if(fs.existsSync(oldReceipt)&&!fs.existsSync(history))fs.copyFileSync(oldReceipt,history);
 const receipt={passed:true,encoding:'Meshopt+PNG',revision:'r03',source_sha256:sha(fs.readFileSync(source)),path:target,bytes:bytes.length,sha256:sha(bytes),skeleton_and_animation_preserved:true,time_origins:timeOrigins,removed_non_color_auxiliary_attributes:removed};fs.writeFileSync(path.join(batch,role,'压缩回执-r03.json'),JSON.stringify(receipt,null,2));fs.writeFileSync(oldReceipt,JSON.stringify(receipt,null,2));console.log(role,bytes.length);
}
