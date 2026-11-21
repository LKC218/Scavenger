// 独立游戏派生：不改源文件，保留层级、机械节点、骨骼和动作。
import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import {pathToFileURL} from 'node:url';import {createRequire} from 'node:module';import {execFileSync} from 'node:child_process';
const progress=console.log.bind(console);console.log=(...args)=>{if(String(args[0]).startsWith('游戏版'))progress(...args);};
const root=path.resolve(import.meta.dirname,'../..'),unified=fs.existsSync(path.join(root,'local-data/资源库/当前版本.json')),previous=unified?execFileSync('python',['-X','utf8','-B',path.join(root,'scripts/资源库/准备重建.py')],{encoding:'utf8'}).trim():path.join(root,'local-data/网页资源库/全库第一版'),out=path.join(root,unified?'local-data/资源库/待入库/重建':'local-data/网页资源库/游戏版第一版');
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=async p=>import(pathToFileURL(path.join(modules,p)).href),require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp'),{NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js'),{simplify,weld,cloneDocument}=await load('@gltf-transform/functions/dist/index.js'),{MeshoptEncoder,MeshoptDecoder,MeshoptSimplifier}=await load('meshoptimizer/index.js'),{encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready,MeshoptSimplifier.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),read=p=>JSON.parse(fs.readFileSync(p,'utf8')),write=(p,x)=>fs.writeFileSync(p,JSON.stringify(x,null,2));
for(const d of ['模型','验证','贴图缓存'])fs.mkdirSync(path.join(out,d),{recursive:true});
const recipe={version:1,resolutions:[256,512],normal:false,metallicRoughness:'像素通道均值乘原系数',occlusionMax:128,geometry:'不量化，不重排；静态细节层沿用已核验保守比例；骨骼保留拓扑',source:'全库第一版的原始输入'};write(path.join(out,'处理参数.json'),recipe);
const only=process.argv.find(x=>x.startsWith('--only='))?.slice(7).split(',');
const jobs=fs.readdirSync(path.join(previous,'验证')).filter(x=>x.endsWith('.model.json')).map(f=>read(path.join(previous,'验证',f))).filter(x=>x.passed&&(!only||only.includes(x.source_sha256)));if(!only)write(path.join(out,'输入清单.json'),jobs.map(j=>({source:j.source,source_sha256:j.source_sha256,source_bytes:j.source_bytes})));
const bytes=a=>a?sha(Buffer.from(a.getArray().buffer,a.getArray().byteOffset,a.getArray().byteLength)):null;
function structure(doc){const r=doc.getRoot(),ns=r.listNodes();return {nodes:ns.map(n=>[n.getName(),n.getTranslation(),n.getRotation(),n.getScale(),n.getWeights(),n.listChildren().map(c=>ns.indexOf(c))]),skins:r.listSkins().map(s=>[s.listJoints().map(j=>ns.indexOf(j)),bytes(s.getInverseBindMatrices())]),animations:r.listAnimations().map(a=>[a.getName(),a.listChannels().map(c=>[ns.indexOf(c.getTargetNode()),c.getTargetPath()]),a.listSamplers().map(s=>[s.getInterpolation(),bytes(s.getInput()),bytes(s.getOutput())])])};}
function equivalent(a,b){if(typeof a==='number'&&typeof b==='number')return Math.abs(a-b)<1e-5;if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((x,i)=>equivalent(x,b[i]));if(a&&b&&typeof a==='object'&&typeof b==='object')return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>equivalent(a[k],b[k]));return a===b;}
const triangles=d=>d.getRoot().listMeshes().reduce((n,m)=>n+m.listPrimitives().reduce((a,p)=>a+(p.getIndices()?.getCount()||p.getAttribute('POSITION').getCount())/3,0),0);
const imageDecoder=async b=>{const {data,info}=await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject:true});return{data:new Uint8Array(data),width:info.width,height:info.height};};
let count=0;
for(const job of jobs){
 const receipt=path.join(out,'验证',job.source_sha256+'.json');
 if(fs.existsSync(receipt)){const old=read(receipt);if(old.passed&&old.recipe===sha(Buffer.from(JSON.stringify(recipe)))&&old.variants.every(v=>fs.existsSync(path.join(root,v.path))&&sha(fs.readFileSync(path.join(root,v.path)))===v.sha256)){console.log('游戏版缓存',++count,'/',jobs.length);continue;}}
 const result={source_sha256:job.source_sha256,source:job.source,source_bytes:job.source_bytes,recipe:sha(Buffer.from(JSON.stringify(recipe))),variants:[],passed:false};
 try{
  if(sha(fs.readFileSync(job.source))!==job.source_sha256)throw Error('原始输入摘要变化');
  const source=await io.read(job.source),baseline=structure(source),before=triangles(source),animated=source.getRoot().listSkins().length>0||source.getRoot().listAnimations().length>0||source.getRoot().listMeshes().some(m=>m.listPrimitives().some(p=>p.listTargets().length));
  const lodfile=path.join(previous,'验证',job.source_sha256+'.lod.json'),oldlod=fs.existsSync(lodfile)?read(lodfile):null;
  for(const resolution of [256,512]){
   const doc=cloneDocument(source),materials=[];
   for(const m of doc.getRoot().listMaterials()){
    const texture=m.getMetallicRoughnessTexture();let metallic=m.getMetallicFactor(),roughness=m.getRoughnessFactor();
    if(texture?.getImage()){const stats=await sharp(texture.getImage()).removeAlpha().stats();metallic*=stats.channels[2].mean/255;roughness*=stats.channels[1].mean/255;}
    materials.push({name:m.getName(),normalRemoved:!!m.getNormalTexture(),metallicRoughnessRemoved:!!texture,metallic,roughness});m.setNormalTexture(null).setMetallicRoughnessTexture(null).setMetallicFactor(metallic).setRoughnessFactor(roughness);
   }
   const textures=[];
   for(const t of doc.getRoot().listTextures()){
    const parents=t.listParents().filter(p=>p.propertyType!=='Root');if(!parents.length){t.dispose();continue;}
    const original=t.getImage();if(!original)continue;
    const linear=parents.some(p=>p.propertyType==='Material'&&p.getOcclusionTexture?.()===t),limit=linear?128:resolution;
    const {data:png,info}=await sharp(original).resize({width:limit,height:limit,fit:'inside',withoutEnlargement:true}).png().toBuffer({resolveWithObject:true});
    const key=sha(Buffer.concat([png,Buffer.from(linear?'linear-uastc-v1':'srgb-etc1s-v1')])),cache=path.join(out,'贴图缓存',key+'.ktx2');let encoded;
    if(fs.existsSync(cache))encoded=fs.readFileSync(cache);else{encoded=await encodeToKTX2(new Uint8Array(png),{imageDecoder,isUASTC:linear,qualityLevel:200,generateMipmap:true,isInputSRGB:!linear,isSetKTX2SRGBTransferFunc:!linear,enableDebug:false});fs.writeFileSync(cache,encoded);}
    t.setImage(new Uint8Array(encoded)).setMimeType('image/ktx2');textures.push({name:t.getName(),width:info.width,height:info.height,bytes:encoded.length,linear});
   }
   doc.createExtension(KHRTextureBasisu).setRequired(true);doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
   for(const level of animated?[0]:unified?(resolution===256?[1,2]:[1]):[0,1,2]){
    const variant=cloneDocument(doc),old=oldlod?.variants?.find(v=>v.level===level),ratio=level?(old?.ratio??(level===1?.75:.55)):1,error=level?(old?.error??.0001):0;
    if(level&&ratio<1)await variant.transform(weld(),simplify({simplifier:MeshoptSimplifier,ratio,error,lockBorder:true}));
    const target=path.join(out,'模型',`${job.source_sha256}.${resolution}.lod${level}.glb`);await io.write(target,variant);const back=await io.read(target),after=triangles(back);
    if(!equivalent(baseline,structure(back))||after>before||after<=0)throw Error('节点、蒙皮、动作或几何计数回读失败');
    const b=fs.readFileSync(target);result.variants.push({resolution,level,ratio,error,animated:!!animated,before_triangles:before,triangles:after,path:path.relative(root,target).split(path.sep).join('/'),bytes:b.length,sha256:sha(b),textures,materials,structure_passed:true});
   }
  }
  result.passed=true;
 }catch(e){result.error=String(e.stack||e);}
 write(receipt,result);console.log('游戏版',++count,'/',jobs.length,result.passed?'通过':result.error);
}
