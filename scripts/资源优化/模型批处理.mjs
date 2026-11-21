// 保留场景图和浮点几何；以内容摘要缓存贴图编码，逐份回读核对。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const progressLog=console.log.bind(console);
console.log=(...args)=>{if(String(args[0]).startsWith('模型'))progressLog(...args);};
const root=path.resolve(import.meta.dirname,'../..');
if(fs.existsSync(path.join(root,'local-data/资源库/当前版本.json')))throw Error('旧保真编码阶段已退役，请使用 scripts/资源库/维护资源库.py rebuild。');
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules');
const load=async p=>import(pathToFileURL(path.join(modules,p)).href);
const {NodeIO}=await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js');
const {MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js');
const requireTool=createRequire(path.join(modules,'../package.json'));
const sharp=requireTool('sharp');
const {encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);
const [jobsFile,out]=process.argv.slice(2);
const jobs=JSON.parse(fs.readFileSync(jobsFile,'utf8')).filter(x=>x.kind==='model');
for(const d of ['模型','验证','贴图缓存'])fs.mkdirSync(path.join(out,d),{recursive:true});
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function indexHash(accessor,mode){
 if(!accessor)return null;
 const a=Array.from(accessor.getArray());
 if(mode===4)for(let i=0;i<a.length;i+=3){const t=a.slice(i,i+3);const m=t.indexOf(Math.min(...t));a.splice(i,3,...t.slice(m),...t.slice(0,m));}
 return hash(Buffer.from(new Uint32Array(a).buffer));
}
function equivalent(a,b){
 if(typeof a==='number'&&typeof b==='number')return Math.abs(a-b)<=1e-5;
 if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((x,i)=>equivalent(x,b[i]));
 if(a&&b&&typeof a==='object'&&typeof b==='object')return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>equivalent(a[k],b[k]));
 return a===b;
}
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const imageDecoder=async buffer=>{const {data,info}=await sharp(buffer).ensureAlpha().raw().toBuffer({resolveWithObject:true});return {data:new Uint8Array(data),width:info.width,height:info.height};};
function signature(doc){
 const r=doc.getRoot();
 return {
  nodes:r.listNodes().map(n=>({name:n.getName(),translation:n.getTranslation(),rotation:n.getRotation(),scale:n.getScale(),children:n.listChildren().map(c=>r.listNodes().indexOf(c)),weights:n.getWeights()})),
  skins:r.listSkins().map(s=>({name:s.getName(),joints:s.listJoints().map(j=>r.listNodes().indexOf(j)),inverseBind:s.getInverseBindMatrices()?hash(Buffer.from(s.getInverseBindMatrices().getArray().buffer,s.getInverseBindMatrices().getArray().byteOffset,s.getInverseBindMatrices().getArray().byteLength)):null})),
  animations:r.listAnimations().map(a=>({name:a.getName(),channels:a.listChannels().map(c=>({target:r.listNodes().indexOf(c.getTargetNode()),path:c.getTargetPath()})),samplers:a.listSamplers().map(s=>({interpolation:s.getInterpolation(),input:hash(Buffer.from(s.getInput().getArray().buffer,s.getInput().getArray().byteOffset,s.getInput().getArray().byteLength)),output:hash(Buffer.from(s.getOutput().getArray().buffer,s.getOutput().getArray().byteOffset,s.getOutput().getArray().byteLength))}))})),
  geometry:r.listMeshes().map(m=>({name:m.getName(),primitives:m.listPrimitives().map(p=>({mode:p.getMode(),indices:indexHash(p.getIndices(),p.getMode()),attributes:p.listSemantics().sort().map(s=>{const a=p.getAttribute(s);return [s,a.getType(),a.getNormalized(),hash(Buffer.from(a.getArray().buffer,a.getArray().byteOffset,a.getArray().byteLength))];}),morphs:p.listTargets().map(t=>t.listSemantics().sort().map(s=>{const a=t.getAttribute(s);return [s,hash(Buffer.from(a.getArray().buffer,a.getArray().byteOffset,a.getArray().byteLength))];}))}))}))
 };
}
let index=0;
for(const job of jobs){
 const dst=path.join(out,'模型',job.source_sha256+'.glb');
 const receipt=path.join(out,'验证',job.source_sha256+'.model.json');
 if(fs.existsSync(receipt)&&fs.existsSync(dst)){const old=JSON.parse(fs.readFileSync(receipt));if(old.passed&&hash(fs.readFileSync(dst))===old.sha256){index++;continue;}}
 let result;
 try{
  const doc=await io.read(job.source),before=signature(doc);const textures=[];
  for(const t of doc.getRoot().listTextures()){
   const original=t.getImage();if(!original)continue;
   if(t.getMimeType()==='image/ktx2')continue;
   const parents=t.listParents();
   const linear=parents.some(p=>p.propertyType==='Material'&&(p.getNormalTexture?.()===t||p.getMetallicRoughnessTexture?.()===t||p.getOcclusionTexture?.()===t));
   const meta=await sharp(original).metadata();
   const resized=await sharp(original).resize({width:1024,height:1024,fit:'inside',withoutEnlargement:true}).png().toBuffer();
   const key=hash(Buffer.concat([resized,Buffer.from(linear?'linear-uastc-v1':'srgb-etc1s-v1')]));
   const cache=path.join(out,'贴图缓存',key+'.ktx2');
   let encoded;
   if(fs.existsSync(cache))encoded=fs.readFileSync(cache);
   else{
    encoded=await encodeToKTX2(new Uint8Array(resized),{imageDecoder,isUASTC:linear,qualityLevel:200,generateMipmap:true,isInputSRGB:!linear,isSetKTX2SRGBTransferFunc:!linear,enableDebug:false});
    const temp=cache+'.'+process.pid+'.writing';fs.writeFileSync(temp,encoded);fs.renameSync(temp,cache);
   }
   textures.push({name:t.getName(),before:original.byteLength,after:encoded.byteLength,width:meta.width,height:meta.height,linear});
   t.setImage(new Uint8Array(encoded)).setMimeType('image/ktx2');
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);
  // 不量化、不重排，保证已有机械节点和自定义几何读取不会受到整数属性影响。
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  await io.write(dst,doc);
  const restored=await io.read(dst),after=signature(restored);
  if(!equivalent(before,after)){
   fs.writeFileSync(path.join(out,'验证',job.source_sha256+'.diff.json'),JSON.stringify({before,after},null,2));
   throw new Error('层级、几何、蒙皮或动画回读摘要不一致');
  }
  const buffer=fs.readFileSync(dst);
  result={...job,path:path.relative(root,dst).split(path.sep).join('/'),bytes:buffer.byteLength,sha256:hash(buffer),passed:true,verification:'几何属性、拓扑、节点变换、蒙皮关节、动作与变形数据逐项摘要一致；贴图为有损KTX2，另需视觉验收',textures,structure:{nodes:before.nodes.length,skins:before.skins.length,animations:before.animations.length}};
 }catch(e){result={...job,passed:false,error:String(e.stack||e)};}
 fs.writeFileSync(receipt,JSON.stringify(result,null,2));
 console.log('模型',++index,'/',jobs.length,result.passed?'通过':result.error.split('\n')[0]);
}
