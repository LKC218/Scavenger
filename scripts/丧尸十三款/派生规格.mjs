// 双档贴图和Meshopt压缩；保留完整拓扑、骨架、权重和动作。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const root=path.resolve(import.meta.dirname,'../..');
const batch=path.resolve(process.argv[2]);
if(!batch.startsWith(path.join(root,'local-data/丧尸模型/十三款优化')+path.sep))throw Error('批次路径越界');
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules');
const load=p=>import(pathToFileURL(path.join(modules,p)).href);
const require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp');
const {NodeIO}=await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS,EXTMeshoptCompression}=await load('@gltf-transform/extensions/dist/index.js');
const {cloneDocument}=await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
// Meshopt允许三角形索引循环移位；按保持绕序的三元组比较真实拓扑。
function topology(p){const a=p.getIndices().getArray(),rows=[];for(let i=0;i<a.length;i+=3){const t=Array.from(a.slice(i,i+3));const k=t.indexOf(Math.min(...t));rows.push([...t.slice(k),...t.slice(0,k)]);}return rows.sort((a,b)=>a[0]-b[0]||a[1]-b[1]||a[2]-b[2]);}
function signature(d){const r=d.getRoot(),ns=r.listNodes();const a=v=>v?Array.from(v.getArray()):null;return {nodes:ns.map(n=>[n.getName(),n.getTranslation(),n.getRotation(),n.getScale(),n.listChildren().map(c=>ns.indexOf(c))]),skins:r.listSkins().map(s=>[s.listJoints().map(j=>ns.indexOf(j)),a(s.getInverseBindMatrices())]),animations:r.listAnimations().map(x=>[x.getName(),x.listChannels().map(c=>[ns.indexOf(c.getTargetNode()),c.getTargetPath()]),x.listSamplers().map(s=>[s.getInterpolation(),a(s.getInput()),a(s.getOutput())])]),meshes:r.listMeshes().map(m=>m.listPrimitives().map(p=>[topology(p),...p.listSemantics().sort().map(k=>[k,a(p.getAttribute(k))])]))};}
function difference(a,b,key=''){if(typeof a==='number'&&typeof b==='number')return Math.abs(a-b)<=1e-5?null:{key,a,b};if(a===b)return null;if(!a||!b||typeof a!==typeof b)return{key,a,b};if(Object.keys(a).length!==Object.keys(b).length)return{key,lengthA:Object.keys(a).length,lengthB:Object.keys(b).length};for(const k of Object.keys(a)){const x=difference(a[k],b[k],key+'/'+k);if(x)return x;}return null;}
for(const i of (process.argv[3]?process.argv[3].split(',').map(Number):Array.from({length:12},(_,i)=>i+1))){
 const dir=path.join(batch,`角色${String(i).padStart(2,'0')}`),job=read(path.join(dir,'制作回执.json'));
 if(sha(fs.readFileSync(job.asset.path))!==job.asset.sha256)throw Error('制作原件摘要变化');
 let run=1;while(fs.existsSync(path.join(dir,`双档${String(run).padStart(3,'0')}`)))run++;
 const out=path.join(dir,`双档${String(run).padStart(3,'0')}`);fs.mkdirSync(out);
 const source=await io.read(job.asset.path),baseline=signature(source),variants=[];
 for(const resolution of [256,512]){
  const doc=cloneDocument(source);
  for(const m of doc.getRoot().listMaterials()){
   const mr=m.getMetallicRoughnessTexture();
   if(mr?.getImage()){const s=await sharp(mr.getImage()).removeAlpha().stats();m.setMetallicFactor(m.getMetallicFactor()*s.channels[2].mean/255);m.setRoughnessFactor(m.getRoughnessFactor()*s.channels[1].mean/255);}
   m.setNormalTexture(null).setMetallicRoughnessTexture(null);
  }
  const textures=[];
  for(const t of doc.getRoot().listTextures()){
   if(!t.listParents().some(p=>p.propertyType!=='Root')){t.dispose();continue;}
   const {data,info}=await sharp(t.getImage()).resize({width:resolution,height:resolution,fit:'inside',withoutEnlargement:true}).png().toBuffer({resolveWithObject:true});
   t.setImage(new Uint8Array(data)).setMimeType('image/png');textures.push({width:info.width,height:info.height,bytes:data.length});
  }
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  const file=path.join(out,`角色-${resolution}.glb`);await io.write(file,doc);
  const diff=difference(signature(await io.read(file)),baseline);if(diff)throw Error('结构或访问器回读不一致：'+JSON.stringify(diff));
  const bytes=fs.readFileSync(file);variants.push({resolution,level:0,path:file,bytes:bytes.length,sha256:sha(bytes),triangles:job.triangles,before_triangles:job.triangles,animated:true,textures,structure_passed:true,encoding:'Meshopt+PNG'});
 }
 fs.writeFileSync(path.join(dir,'派生回执.json'),JSON.stringify({id:job.id,index:i,source_sha256:job.asset.sha256,variants,passed:true},null,2));
 console.log('双档',i,variants.map(v=>[v.resolution,v.bytes]));
}
