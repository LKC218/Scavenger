// 原生BaseAlpha图集的无损几何四边形载体；镜头方向由网页适配，不声称复刻原Shader。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const root=path.resolve(import.meta.dirname,'../..'),directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.join(root,'local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('植被替身编码目录无效');
const output=path.join(directory,'植被替身',revision);fs.mkdirSync(output);
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=p=>import(pathToFileURL(path.join(modules,p)).href),require=createRequire(path.join(modules,'../package.json'));
const sharp=require('sharp'),{Document,NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS,EXTMeshoptCompression,KHRTextureBasisu}=await load('@gltf-transform/extensions/dist/index.js'),{MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js'),{encodeToKTX2}=await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);
const read=p=>JSON.parse(fs.readFileSync(p,'utf8')),sha=b=>crypto.createHash('sha256').update(b).digest('hex'),identity=p=>{const b=fs.readFileSync(p);return {bytes:b.length,sha256:sha(b)};};
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sourceDir=path.join(directory,'植被替身/source-r01'),source=read(path.join(sourceDir,'替身来源.json')),index=read(path.join(directory,'实例分页/r01/实例索引.json')),rows=[];
for(const row of source.rows){
 const input=path.join(sourceDir,row.textures.BaseAlpha.file);if(identity(input).sha256!==row.textures.BaseAlpha.sha256)throw new Error('替身图集原件变化');
 const png=fs.readFileSync(input),imageDecoder=async bytes=>{const {data,info}=await sharp(bytes).ensureAlpha().raw().toBuffer({resolveWithObject:true});return {data:new Uint8Array(data),width:info.width,height:info.height};};
 const encoded=await encodeToKTX2(new Uint8Array(png),{imageDecoder,isUASTC:false,qualityLevel:200,generateMipmap:true,isInputSRGB:true,isSetKTX2SRGBTransferFunc:true,enableDebug:false});
 const doc=new Document(),buffer=doc.createBuffer(),accessor=(type,array)=>doc.createAccessor().setType(type).setArray(array).setBuffer(buffer);
 const texture=doc.createTexture(row.name+'-原生BaseAlpha').setImage(new Uint8Array(encoded)).setMimeType('image/ktx2');
 const material=doc.createMaterial(row.name+'-网页原生图集适配').setBaseColorTexture(texture).setAlphaMode('MASK').setAlphaCutoff(row.alpha_cutoff).setDoubleSided(true);
 const primitive=doc.createPrimitive().setAttribute('POSITION',accessor('VEC3',new Float32Array([-.5,-.5,0,.5,-.5,0,.5,.5,0,-.5,.5,0]))).setAttribute('TEXCOORD_0',accessor('VEC2',new Float32Array([0,0,1,0,1,1,0,1]))).setIndices(accessor('SCALAR',new Uint16Array([0,1,2,0,2,3]))).setMaterial(material);
 doc.createScene().addChild(doc.createNode(row.name).setMesh(doc.createMesh().addPrimitive(primitive)));
 doc.createExtension(KHRTextureBasisu).setRequired(true);doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
 const file=row.id.slice(6)+'.glb';await io.write(path.join(output,file),doc);
 const back=await io.read(path.join(output,file));if(back.getRoot().listMeshes()[0].listPrimitives()[0].getIndices().getCount()!==6)throw new Error('替身载体回读不符');
 rows.push({id:row.id,name:row.name,prototype:index.prototypes.findIndex(p=>p.id===row.id),file,frames:row.frames,size:row.size,offset:row.offset,alpha_cutoff:row.alpha_cutoff,triangles:2,source:row.textures.BaseAlpha});
 console.log(JSON.stringify({name:row.name,triangles:2}));
}
execFileSync('python',['-X','utf8','-B','-c',"import sys,pathlib,json\nsys.path.insert(0,str(pathlib.Path('scripts/断箭').resolve()))\nfrom 地图媒体 import externalize\np=pathlib.Path(sys.argv[1]);a=set()\nfor f in p.glob('*.glb'):a.update(externalize(f,p))\n(p/'媒体资产.json').write_text(json.dumps(sorted(a),ensure_ascii=False),encoding='utf8')",output],{cwd:root,stdio:'inherit'});
const assets={};for(const file of [...rows.map(r=>r.file),...read(path.join(output,'媒体资产.json'))])assets[file]=identity(path.join(output,file));
fs.writeFileSync(path.join(output,'替身编码.json'),JSON.stringify({rows,assets,source:identity(path.join(sourceDir,'替身来源.json')),source_program:identity(import.meta.filename),status:'载体回读完成，朝向与网页画面待验',policy:'原生半球图集最近方向采样；网页适配未重建原Shader的深度视差和帧混合，完整源LOD0及LOD1仍保留'},null,2));
