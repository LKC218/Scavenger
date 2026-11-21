/** 复用固定的Meshopt与KTX2工具；保留源工程、作者变换和几何拓扑。 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const progress=console.log.bind(console);console.log=(...args)=>{if(String(args[0]).startsWith('地图'))progress(...args);};
const root=path.resolve(import.meta.dirname,'../..'),input=path.resolve(process.argv[2]),output=path.resolve(process.argv[3]),reuse=process.argv[4]?path.resolve(process.argv[4]):null;
const local=path.join(root,'local-data/断箭/批次')+path.sep;
if(!input.startsWith(local)||!output.startsWith(local)||fs.existsSync(output))throw Error('输出必须是未占用本地批次子目录');
if(reuse&&!reuse.startsWith(local))throw Error('编码复用缓存必须在本地批次内');
fs.mkdirSync(output,{recursive:true});fs.mkdirSync(path.join(output,'编码缓存'));
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=async p=>import(pathToFileURL(path.join(modules,p)).href),require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp'),{NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js'),{MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js'),{encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),read=p=>JSON.parse(fs.readFileSync(p,'utf8')),write=(p,d)=>fs.writeFileSync(p,JSON.stringify(d,null,2));
const runtime=read(path.join(input,'地图运行.json')),files=[runtime.ground,...Object.values(runtime.prototypes).map(p=>p.file),...runtime.chunks.map(c=>c.file)];
const arrayHash=a=>sha(Buffer.from(a.getArray().buffer,a.getArray().byteOffset,a.getArray().byteLength));
const indexHash=(a,mode)=>{if(!a)return null;const data=Array.from(a.getArray());if(mode===4)for(let i=0;i<data.length;i+=3){const t=data.slice(i,i+3),m=t.indexOf(Math.min(...t));data.splice(i,3,...t.slice(m),...t.slice(0,m));}return sha(Buffer.from(new Uint32Array(data).buffer));};
const signature=doc=>JSON.stringify(doc.getRoot().listMeshes().map(m=>m.listPrimitives().map(p=>({mode:p.getMode(),indices:indexHash(p.getIndices(),p.getMode()),attributes:p.listSemantics().sort().map(s=>[s,arrayHash(p.getAttribute(s))])}))));
const decoder=async b=>{const {data,info}=await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject:true});return{data:new Uint8Array(data),width:info.width,height:info.height};};
const result={status:'running',recipe:{geometry:'Meshopt压缩，不简化、不量化属性、不改变拓扑',textures:'固定ETC1S质量200并生成mip；非二次幂图像向上采样到二次幂，保留裁切UV和边框，避免BC3驱动尺寸限制；视觉另验'},files:[],textures:{}};
let number=0;
for(const filename of files){
  const source=path.join(input,filename),target=path.join(output,filename);fs.mkdirSync(path.dirname(target),{recursive:true});
  const doc=await io.read(source),before=signature(doc);
  for(const texture of doc.getRoot().listTextures()){
    const image=texture.getImage();if(!image)continue;const digest=sha(image),cache=path.join(output,'编码缓存',digest+'.ktx2');
    if(!fs.existsSync(cache)){
      const info=await sharp(image).metadata(),width=2**Math.ceil(Math.log2(info.width)),height=2**Math.ceil(Math.log2(info.height));
      const old=reuse?path.join(reuse,digest+'.ktx2'):null;let encoded;
      if(old&&fs.existsSync(old)){const candidate=fs.readFileSync(old);if(candidate.readUInt32LE(20)===width&&candidate.readUInt32LE(24)===height)encoded=candidate;}
      const reused=!!encoded;
      if(!encoded){const sourceImage=width===info.width&&height===info.height?image:await sharp(image).resize(width,height,{fit:'fill',kernel:'cubic'}).png().toBuffer();encoded=await encodeToKTX2(new Uint8Array(sourceImage),{imageDecoder:decoder,isUASTC:false,qualityLevel:200,generateMipmap:true,isInputSRGB:true,isSetKTX2SRGBTransferFunc:true,enableDebug:false});}
      fs.writeFileSync(cache,encoded);
      result.textures[digest]={source_bytes:image.byteLength,encoded_bytes:encoded.byteLength,source_width:info.width,source_height:info.height,width,height,reused,encoded_sha256:sha(encoded)};
    }
    texture.setImage(new Uint8Array(fs.readFileSync(cache))).setMimeType('image/ktx2');
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  await io.write(target,doc);const restored=await io.read(target);if(signature(restored)!==before)throw Error('几何属性或拓扑回读变化：'+filename);
  result.files.push({file:filename,source_sha256:sha(fs.readFileSync(source)),geometry_exact:true,signature_sha256:sha(Buffer.from(before))});
  if(++number%16===0)console.log('地图压缩',number,'/',files.length);
}
// 外置压缩图像并共享；压缩缓冲及必需扩展由同一转换器保留。
const python=`import sys,json,pathlib\nsys.path.insert(0,str(pathlib.Path('scripts/断箭').resolve()))\nfrom 地图媒体 import externalize\np=pathlib.Path(sys.argv[1]);assets=set()\nfor f in p.rglob('*.glb'):assets.update(externalize(f,p))\n(p/'媒体资产.json').write_text(json.dumps(sorted(assets),ensure_ascii=False),encoding='utf8')\n`;
execFileSync('python',['-X','utf8','-B','-c',python,output],{cwd:root,stdio:'inherit'});
for(const row of result.files){const restored=await io.read(path.join(output,row.file));if(sha(Buffer.from(signature(restored)))!==row.signature_sha256)throw Error('外置后几何回读变化：'+row.file);row.external_roundtrip=true;}
runtime.assets=read(path.join(output,'媒体资产.json'));fs.copyFileSync(path.join(input,runtime.height.file),path.join(output,runtime.height.file));
runtime.runtime_recipe=result.recipe;write(path.join(output,'地图运行.json'),runtime);
result.status='passed_geometry_pending_visual';result.finalFiles={};for(const name of [runtime.ground,...Object.values(runtime.prototypes).map(p=>p.file),...runtime.chunks.map(c=>c.file),...runtime.assets,runtime.height.file]){const b=fs.readFileSync(path.join(output,name));result.finalFiles[name]={bytes:b.byteLength,sha256:sha(b)};}
write(path.join(output,'压缩核验.json'),result);console.log('地图几何无损回读通过，贴图等待实际审图；最终媒体MiB',Object.values(result.finalFiles).reduce((n,r)=>n+r.bytes,0)/1048576);
