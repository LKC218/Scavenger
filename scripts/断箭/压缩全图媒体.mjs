// 全图运行媒体：基础几何无损编码、独立粗总览和共享KTX2。草丛不进入运行依赖。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const root=path.resolve(import.meta.dirname,'../..'),directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.join(root,'local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('全图编码目录或版本无效');
const output=path.join(directory,'全图运行',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const read=p=>JSON.parse(fs.readFileSync(p,'utf8')),write=(p,j)=>{fs.writeFileSync(p+'.writing',JSON.stringify(j,null,2));fs.renameSync(p+'.writing',p);};
const hash=b=>crypto.createHash('sha256').update(b).digest('hex'),identity=p=>{const b=fs.readFileSync(p);return {bytes:b.byteLength,sha256:hash(b)};};
const source=path.join(directory,'全图分块/r02'),surface=read(path.join(source,'地表索引.json')),instances=read(path.join(directory,'实例分页/r01/实例索引.json'));
const adjustment=path.join(directory,'范围冻结/运行调整-r01.json');if(read(adjustment).grass.render!==false)throw new Error('缺少草丛省略的批准配置');
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=p=>import(pathToFileURL(path.join(modules,p)).href),require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp'),{NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js');
const {simplify,weld}=await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder,MeshoptDecoder,MeshoptSimplifier}=await load('meshoptimizer/index.js'),{encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready,MeshoptSimplifier.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const arrayHash=a=>hash(Buffer.from(a.getArray().buffer,a.getArray().byteOffset,a.getArray().byteLength));
const indexHash=p=>{const input=p.getIndices()?.getArray();if(!input)return null;const data=Uint32Array.from(input);if(p.getMode()===4)for(let i=0;i<data.length;i+=3){const a=data[i],b=data[i+1],c=data[i+2];if(b<a&&b<c){data[i]=b;data[i+1]=c;data[i+2]=a;}else if(c<a&&c<b){data[i]=c;data[i+1]=a;data[i+2]=b;}}return hash(Buffer.from(data.buffer));};
const signature=doc=>JSON.stringify(doc.getRoot().listMeshes().map(m=>m.listPrimitives().map(p=>({mode:p.getMode(),indices:indexHash(p),attributes:p.listSemantics().sort().map(s=>[s,arrayHash(p.getAttribute(s))])}))));
const decoder=async b=>{const {data,info}=await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject:true});return {data:new Uint8Array(data),width:info.width,height:info.height};};
const cache=path.join(output,'编码缓存');fs.mkdirSync(cache);
const result={status:'running',sources:{surface:identity(path.join(source,'地表索引.json')),instances:identity(path.join(directory,'实例分页/r01/实例索引.json')),adjustment:identity(adjustment)},files:[],textures:{},recipe:{geometry:'基础层Meshopt，不量化、不简化；完整有绕序索引及所有顶点属性独立回读一致',texture:'ETC1S质量200及mip；地表258上采样至512，其余最长边1024；有损画面另验',overview:'单独粗地形weld+simplify，ratio0.10,error0.001,lockBorder；不替换源地形或精细支撑',grass:'六类草丛不编码、不加载；源与工程仍保留'}};
const jobs=[{input:path.join(source,surface.overview.file),file:'总览/地形.glb',coarse:true},{input:path.join(source,surface.background.file),file:'总览/背景水面.glb'}];
for(const p of instances.prototypes)if(p.category!=='grass')jobs.push({input:path.join(directory,p.glb),file:`原型/${p.id.slice(6)}.glb`,prototype:p});
for(const chunk of surface.chunks)if(chunk.file)jobs.push({input:path.join(source,chunk.file),file:`地表/${chunk.id}.glb`,chunk});
let current=0;const started=Date.now();let active='准备';
const timer=setInterval(()=>write(path.join(output,'进行中.json'),{status:'running',current,total:jobs.length,active,started_epoch:started/1000,elapsed_s:(Date.now()-started)/1000}),15000);
try{
 for(const job of jobs){
  active=job.file;const doc=await io.read(job.input);let sourceTriangles=0;
  for(const mesh of doc.getRoot().listMeshes())for(const p of mesh.listPrimitives())sourceTriangles+=(p.getIndices()?.getCount()??p.getAttribute('POSITION').getCount())/3;
  if(job.coarse)await doc.transform(weld(),simplify({simplifier:MeshoptSimplifier,ratio:.10,error:.001,lockBorder:true}));
  const before=signature(doc);
  for(const texture of doc.getRoot().listTextures()){
   const image=texture.getImage();if(!image)continue;const info=await sharp(image).metadata();
   const max=job.chunk?512:1024,scale=Math.min(1,max/Math.max(info.width,info.height));
   const width=2**Math.ceil(Math.log2(Math.max(1,Math.round(info.width*scale)))),height=2**Math.ceil(Math.log2(Math.max(1,Math.round(info.height*scale))));
   const key=hash(Buffer.concat([Buffer.from(image),Buffer.from(`|${width}:${height}|ETC1S200mip`)])),file=path.join(cache,key+'.ktx2');
   if(!fs.existsSync(file)){
    const sourceImage=width===info.width&&height===info.height?image:await sharp(image).resize(width,height,{fit:'fill',kernel:'cubic'}).png().toBuffer();
    const encoded=await encodeToKTX2(new Uint8Array(sourceImage),{imageDecoder:decoder,isUASTC:false,qualityLevel:200,generateMipmap:true,isInputSRGB:true,isSetKTX2SRGBTransferFunc:true,enableDebug:false});
    fs.writeFileSync(file,encoded);result.textures[key]={source_sha256:hash(image),source_size:[info.width,info.height],size:[width,height],bytes:encoded.byteLength,sha256:hash(encoded)};
   }
   texture.setImage(new Uint8Array(fs.readFileSync(file))).setMimeType('image/ktx2');
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  const target=path.join(output,job.file);fs.mkdirSync(path.dirname(target),{recursive:true});await io.write(target,doc);
  if(signature(await io.read(target))!==before)throw new Error('编码几何回读漂移: '+job.file);
  result.files.push({file:job.file,source:identity(job.input),geometry_exact:true,geometry_signature:hash(Buffer.from(before)),coarse:!!job.coarse,source_triangles:sourceTriangles});
  current++;if(current%32===0||current<=3)console.log(JSON.stringify({current,total:jobs.length,file:job.file,elapsed_s:(Date.now()-started)/1000}));
 }
 const python="import sys,pathlib,json\nsys.path.insert(0,str(pathlib.Path('scripts/断箭').resolve()))\nfrom 地图媒体 import externalize\np=pathlib.Path(sys.argv[1]);a=set()\nfor f in p.rglob('*.glb'):a.update(externalize(f,p))\n(p/'媒体资产.json').write_text(json.dumps(sorted(a),ensure_ascii=False),encoding='utf8')\n";
 execFileSync('python',['-X','utf8','-B','-c',python,output],{cwd:root,stdio:'inherit'});
 const files={};
 for(const row of result.files){const file=path.join(output,row.file);if(hash(Buffer.from(signature(await io.read(file))))!==row.geometry_signature)throw new Error('外置后几何回读漂移: '+row.file);row.external_roundtrip=true;files[row.file]=identity(file);}
 for(const name of read(path.join(output,'媒体资产.json')))files[name]=identity(path.join(output,name));
 const chunks=surface.chunks.map(c=>{
  const geometry='支撑/'+c.id+'.bin';fs.mkdirSync(path.join(output,'支撑'),{recursive:true});fs.copyFileSync(path.join(source,c.geometry),path.join(output,geometry));files[geometry]=identity(path.join(output,geometry));
  const file=c.file?'地表/'+c.id+'.glb':null;
  return {id:c.id,bounds:c.bounds,geometry,geometry_identity:files[geometry],file,identity:files[file],triangles:c.render?.triangles??0,instance_pages:instances.chunk_references[c.id]??[]};
 });
 const pages=instances.pages.map(p=>{const file='实例/'+p.id+'.bin';fs.mkdirSync(path.join(output,'实例'),{recursive:true});fs.copyFileSync(path.join(directory,'实例分页/r01',p.file),path.join(output,file));files[file]=identity(path.join(output,file));return {id:p.id,file,count:p.count,identity:files[file]};});
 const prototypes=instances.prototypes.map(p=>({...p,file:p.category==='grass'?null:`原型/${p.id.slice(6)}.glb`,glb:undefined,glb_identity:undefined,enabled:p.category!=='grass'}));
 const manifest={schema:'airbase-streaming-scene-v1',id:'airbase-full',name:'Airbase全图静态场景V001',size:9000,bounds:surface.bounds,grid:surface.grid,chunk_m:125,
  center:{x:0,z:85},defaultSpan:58,maxSpan:12150,safeAltitude:240,spawnSpacing:24,cell:4,targets:[],chunks,pages,prototypes,group_count:instances.groups.length,
  overview:'总览/地形.glb',background:'总览/背景水面.glb',files,scope_adjustment:result.sources.adjustment,grass_enabled:false,
  vegetation:{distance_lod:true,density_adjustment:true,source_count:343234},budgets:{download:64*1048576,simulation:64*1048576,cpu:256*1048576,textures:256*1048576,geometry:192*1048576,visible_chunks:49,requests:4,transcodes:2},
  acceptance:{technical:'制作中',ai_visual:'待逐区及编码画面核验',human:'待人工验收'}};
 write(path.join(output,'地图运行.json'),manifest);result.status='geometry_passed_pending_visual_runtime';result.manifest=identity(path.join(output,'地图运行.json'));result.total_runtime_bytes=Object.values(files).reduce((sum,r)=>sum+r.bytes,0);result.source_program=identity(import.meta.filename);write(path.join(output,'压缩核验.json'),result);
 console.log(JSON.stringify({status:result.status,files:Object.keys(files).length,bytes:result.total_runtime_bytes,manifest:result.manifest}));
}catch(error){write(path.join(output,'失败.json'),{active,current,error:String(error),source_program:identity(import.meta.filename)});throw error;}
finally{clearInterval(timer);}
