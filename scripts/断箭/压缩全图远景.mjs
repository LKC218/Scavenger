// 独立远景细节层；源和近景保持不变，误差配置不能替代实际投影审图。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const outputLog=console.log.bind(console);console.log=(...args)=>{if(String(args[0]).startsWith('{'))outputLog(...args);};
const root=path.resolve(import.meta.dirname,'../..'),directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.join(root,'local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('远景编码目录无效');
const plant=process.argv[4]==='vegetation';
const quality=process.argv[4]==='quality';
const background=process.argv[4]==='background';
const output=path.join(directory,background?'背景运行':plant?'植被运行':'远景运行',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);fs.mkdirSync(path.join(output,'编码缓存'));
const read=p=>JSON.parse(fs.readFileSync(p,'utf8')),write=(p,r)=>fs.writeFileSync(p,JSON.stringify(r,null,2)),sha=b=>crypto.createHash('sha256').update(b).digest('hex'),identity=p=>{const b=fs.readFileSync(p);return {bytes:b.length,sha256:sha(b)};};
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=p=>import(pathToFileURL(path.join(modules,p)).href),require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp'),{NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js'),{weld,simplify}=await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder,MeshoptDecoder,MeshoptSimplifier}=await load('meshoptimizer/index.js'),{encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready,MeshoptSimplifier.ready]);
const farSimplifier={...MeshoptSimplifier,simplify:(indices,positions,stride,target,error,flags)=>{
 const count=positions.length/stride,locks=new Uint8Array(count),extrema=[Infinity,Infinity,Infinity,-Infinity,-Infinity,-Infinity],vertices=new Int32Array(6);
 for(const i of indices)for(let axis=0;axis<3;axis++){const v=positions[i*stride+axis];if(v<extrema[axis]){extrema[axis]=v;vertices[axis]=i;}if(v>extrema[axis+3]){extrema[axis+3]=v;vertices[axis+3]=i;}}
 for(const i of vertices)locks[i]=1;
 const scale=MeshoptSimplifier.getScale(positions,stride);
 return MeshoptSimplifier.simplifySloppy(indices,positions,stride,locks,Math.min(indices.length,Math.max(target,12)),scale?error/scale:0);
}};
const topologySimplifier={...MeshoptSimplifier,simplify:(indices,positions,stride,target,error,flags)=>{
 const scale=MeshoptSimplifier.getScale(positions,stride);return MeshoptSimplifier.simplify(indices,positions,stride,target,scale?error/scale:0,flags);
}};
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const signature=doc=>JSON.stringify(doc.getRoot().listMeshes().map(m=>m.listPrimitives().map(p=>({mode:p.getMode(),attributes:p.listSemantics().sort().map(s=>{const a=p.getAttribute(s).getArray();return [s,sha(Buffer.from(a.buffer,a.byteOffset,a.byteLength))];}),triangles:(()=>{const a=p.getIndices()?.getArray();if(!a)return null;const b=Uint32Array.from(a);for(let i=0;i<b.length;i+=3){const v=[...b.slice(i,i+3)],min=v.indexOf(Math.min(...v));b.set([...v.slice(min),...v.slice(0,min)],i);}return sha(Buffer.from(b.buffer));})()}))));
const bounds=doc=>{
 const result=[Infinity,Infinity,Infinity,-Infinity,-Infinity,-Infinity];
 for(const node of doc.getRoot().listNodes())if(node.getMesh()){
  const matrix=node.getWorldMatrix();
  for(const primitive of node.getMesh().listPrimitives()){
   const a=primitive.getAttribute('POSITION')?.getArray();if(!a)continue;
   const indices=primitive.getIndices()?.getArray()??Array.from({length:a.length/3},(_,i)=>i);
   for(const vertex of indices){const i=vertex*3;for(let k=0;k<3;k++){const p=matrix[k]*a[i]+matrix[4+k]*a[i+1]+matrix[8+k]*a[i+2]+matrix[12+k];result[k]=Math.min(result[k],p);result[k+3]=Math.max(result[k+3],p);}}
  }
 }return result;
};
const decoder=async b=>{const {data,info}=await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject:true});return {data:new Uint8Array(data),width:info.width,height:info.height};};
const index=read(path.join(directory,'实例分页/r01/实例索引.json')),jobs=background?[{input:path.join(directory,'背景来源/r01/背景.glb'),file:'背景.glb',size:512,background:true}]:plant?[]:[{input:path.join(directory,'远景来源/r01/道面与直接表面.glb'),file:'道面与直接表面.glb',size:512}];
if(plant){for(const row of read(path.join(directory,'植被细节/project-r01/build-汇总.json'))){const pi=index.prototypes.findIndex(p=>p.id===row.asset_id);if(pi<0)throw new Error('植被LOD1来源失联');jobs.push({input:path.join(directory,'植被细节/project-r01',row.asset_id.slice(6),'原型.glb'),file:`原型/${row.asset_id.slice(6)}.glb`,size:512,prototype:pi,level:'vegetation'});}}
else if(!background)index.prototypes.forEach((p,pi)=>{if(p.category==='building'||p.category==='decor')for(const level of ['middle','far'])jobs.push({input:path.join(directory,p.glb),file:`${level==='middle'?'中景':'原型'}/${p.id.slice(6)}.glb`,size:level==='middle'?256:128,prototype:pi,level,error:quality?(level==='middle'?.05:.5):(level==='middle'?.25:4),boundsLimit:quality?.1:(level==='middle'?.5:6)});});
const result={status:'running',files:[],recipe:{prototype:'独立中景与远景空间简化simplifySloppy ratio0.005，锁定每个primitive六轴极值顶点且目标至少4面；中景误差参数0.25米与包围盒0.5米门限，远景参数4米与包围盒6米门限；空网格回退，投影及材质仍须独立审图',surface:'完整真实道路及直接表面，不减面',texture:'ETC1S质量200，中景原型最长边256、远景128，道面512，二次幂和mip'}};
const started=Date.now();let current=0,active='准备';const timer=setInterval(()=>{fs.writeFileSync(path.join(output,'进行中.json.writing'),JSON.stringify({current,total:jobs.length,active,elapsed_s:(Date.now()-started)/1000}));fs.renameSync(path.join(output,'进行中.json.writing'),path.join(output,'进行中.json'));},15000);
try{
 for(const job of jobs){
  active=job.file;let doc=await io.read(job.input);const sourceBounds=bounds(doc);let fallback=null;
  if(job.prototype!==undefined&&!plant){
   for(const mesh of doc.getRoot().listMeshes())for(const primitive of mesh.listPrimitives())for(const semantic of primitive.listSemantics())if(semantic.startsWith('_')||semantic==='TEXCOORD_1')primitive.setAttribute(semantic,null);
   const dimensions=sourceBounds.slice(3).map((v,i)=>v-sourceBounds[i]),maximum=Math.max(...dimensions),minimum=Math.min(...dimensions.filter(v=>v>1e-5)),preserve=quality&&(maximum<12||maximum/minimum>40);
   if(!preserve)await doc.transform(weld(),simplify({simplifier:quality&&job.level==='middle'?topologySimplifier:farSimplifier,ratio:quality?(job.level==='middle'?.05:.01):.005,error:job.error}));
   const candidate=bounds(doc),error=Math.max(...candidate.map((v,i)=>Math.abs(v-sourceBounds[i])));
   if(!candidate.every(Number.isFinite)||error>job.boundsLimit){fallback={reason:'简化后为空或作者包围盒超过本级误差门限',bounds_error_m:Number.isFinite(error)?error:null};doc=await io.read(job.input);}
  }
  const before=signature(doc);let triangles=0;for(const m of doc.getRoot().listMeshes())for(const p of m.listPrimitives())triangles+=(p.getIndices()?.getCount()??p.getAttribute('POSITION').getCount())/3;
  for(const texture of doc.getRoot().listTextures()){
   const image=texture.getImage(),info=await sharp(image).metadata(),alphaSensitive=quality&&doc.getRoot().listMaterials().some(m=>m.getBaseColorTexture()===texture&&m.getAlphaMode()==='MASK'),size=job.background&&texture.getName().includes('背景源十二层底色')?1024:alphaSensitive?Math.max(512,job.size):job.size,scale=Math.min(1,size/Math.max(info.width,info.height)),width=2**Math.ceil(Math.log2(Math.max(1,Math.round(info.width*scale)))),height=2**Math.ceil(Math.log2(Math.max(1,Math.round(info.height*scale))));
   const key=sha(Buffer.concat([Buffer.from(image),Buffer.from(`|${width}:${height}|ETC1S200mip`)])),cache=path.join(output,'编码缓存',key+'.ktx2');
   if(!fs.existsSync(cache)){
    const reused=[path.join(directory,'远景运行/r01/编码缓存',key+'.ktx2'),path.join(directory,'全图运行/r01/编码缓存',key+'.ktx2')].find(p=>p!==cache&&fs.existsSync(p));
    if(reused)fs.copyFileSync(reused,cache);
    else{const png=await sharp(image).resize(width,height,{fit:'fill',kernel:'cubic'}).png().toBuffer(),encoded=await encodeToKTX2(new Uint8Array(png),{imageDecoder:decoder,isUASTC:false,qualityLevel:200,generateMipmap:true,isInputSRGB:true,isSetKTX2SRGBTransferFunc:true,enableDebug:false});fs.writeFileSync(cache,encoded);}
   }
   texture.setImage(new Uint8Array(fs.readFileSync(cache))).setMimeType('image/ktx2');
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  const target=path.join(output,job.file);fs.mkdirSync(path.dirname(target),{recursive:true});await io.write(target,doc);if(signature(await io.read(target))!==before)throw new Error('远景编码回读不符: '+job.file);
  if(!triangles)throw new Error('远景禁止空几何: '+job.file);
  result.files.push({file:job.file,prototype:job.prototype,level:job.level,triangles,fallback,source_bounds:sourceBounds,derived_bounds:bounds(doc),source:identity(job.input),geometry_signature:sha(Buffer.from(before))});current++;if(current%16===0)console.log(JSON.stringify({current,total:jobs.length}));
 }
 const python="import sys,pathlib,json\nsys.path.insert(0,str(pathlib.Path('scripts/断箭').resolve()))\nfrom 地图媒体 import externalize\np=pathlib.Path(sys.argv[1]);a=set()\nfor f in p.rglob('*.glb'):a.update(externalize(f,p))\n(p/'媒体资产.json').write_text(json.dumps(sorted(a),ensure_ascii=False),encoding='utf8')\n";
 execFileSync('python',['-X','utf8','-B','-c',python,output],{cwd:root,stdio:'inherit'});
 const files={};for(const row of result.files){if(sha(Buffer.from(signature(await io.read(path.join(output,row.file)))))!==row.geometry_signature)throw new Error('远景外置回读不符');files[row.file]=identity(path.join(output,row.file));}
 for(const name of read(path.join(output,'媒体资产.json')))files[name]=identity(path.join(output,name));if(!plant&&!background){fs.copyFileSync(path.join(directory,'远景来源/r01/固定布景.bin'),path.join(output,'固定布景.bin'));files['固定布景.bin']=identity(path.join(output,'固定布景.bin'));}
 result.status='geometry_passed_pending_projection_visual';if(plant)result.recipe.prototype='源LOD1完整几何无损Meshopt编码，不进行空间简化；材质沿用正常白天语义，512底色，压缩画面另验';if(quality){result.recipe.prototype='中景拓扑约束简化0.05米，远景空间简化0.5米；小于12米或长宽比大于40的原型保留几何；包围盒误差0.1米，失败回退';result.recipe.texture='MASK底色至少512以保留Alpha；其余中景256、远景128；ETC1S200及Mip';}if(background)result.recipe={geometry:'源背景完整位置、法线和绕序，原UV另存；编码前后有绕序几何及全部属性严格一致',texture:'真实Splat底色1024，其余背景512；ETC1S200及Mip，完整原件另存'};result.assets=files;result.source_program=identity(import.meta.filename);write(path.join(output,background?'背景核验.json':'远景核验.json'),result);console.log(JSON.stringify({status:result.status,files:Object.keys(files).length}));
}catch(error){write(path.join(output,'失败.json'),{current,active,error:String(error)});throw error;}finally{clearInterval(timer);}
