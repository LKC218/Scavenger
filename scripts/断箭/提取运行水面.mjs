// 从已验证的背景水面包分离原水面；不修改三角形、属性、世界变换或材质。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root=path.resolve(import.meta.dirname,'../..'),directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.join(root,'local-data/断箭/批次')+path.sep)||!/^[A-Za-z0-9_-]+$/.test(revision??''))throw Error('水面输出范围或修订无效');
const output=path.join(directory,'水面运行',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules'),load=p=>import(pathToFileURL(path.join(modules,p)).href);
const {NodeIO}=await load('@gltf-transform/core/dist/index.js'),{ALL_EXTENSIONS}=await load('@gltf-transform/extensions/dist/index.js'),{prune}=await load('@gltf-transform/functions/dist/index.js'),{MeshoptEncoder,MeshoptDecoder}=await load('meshoptimizer/index.js');
await Promise.all([MeshoptEncoder.ready,MeshoptDecoder.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder':MeshoptEncoder,'meshopt.decoder':MeshoptDecoder});
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),identity=file=>{const b=fs.readFileSync(file);return{bytes:b.length,sha256:sha(b)};};
const manifest=JSON.parse(fs.readFileSync(path.join(directory,'全图运行/r01/地图运行.json'),'utf8')),source=path.join(directory,'全图运行/r01',manifest.background),doc=await io.read(source);
const water=p=>p.getMaterial()?.getExtras().render_role===5;
const signature=document=>JSON.stringify(document.getRoot().listNodes().filter(n=>n.getMesh()).flatMap(n=>n.getMesh().listPrimitives().filter(water).map(p=>({source:p.getExtras().source_key,matrix:n.getWorldMatrix(),
  attributes:p.listSemantics().sort().map(s=>{const a=p.getAttribute(s).getArray();return[s,sha(Buffer.from(a.buffer,a.byteOffset,a.byteLength))];}),indices:(()=>{const a=p.getIndices().getArray();return sha(Buffer.from(a.buffer,a.byteOffset,a.byteLength));})(),
  material:{color:p.getMaterial().getBaseColorFactor(),alpha:p.getMaterial().getAlphaMode(),side:p.getMaterial().getDoubleSided(),extras:p.getMaterial().getExtras()}}))).sort((a,b)=>a.source.localeCompare(b.source)));
const before=signature(doc),keys=[],counts=[];
for(const mesh of doc.getRoot().listMeshes())for(const primitive of [...mesh.listPrimitives()]){
 if(water(primitive)){keys.push(primitive.getExtras().source_key);counts.push(primitive.getIndices().getCount()/3);}
 else{mesh.removePrimitive(primitive);primitive.dispose();}
}
if(keys.length!==2||new Set(keys).size!==2||counts.reduce((a,b)=>a+b,0)!==10417)throw Error('两处原水面身份或面数不符');
await doc.transform(prune({keepAttributes:true,keepSolidTextures:true}));
const target=path.join(output,'水面.glb');await io.write(target,doc);
const readback=await io.read(target);if(signature(readback)!==before||readback.getRoot().listTextures().length){fs.writeFileSync(path.join(output,'回读差异.json'),JSON.stringify({before:JSON.parse(before),after:JSON.parse(signature(readback))},null,2));throw Error('水面独立回读不符或出现意外纹理');}
const receipt={passed:true,status:'geometry_exact_water_layer',source_file:path.relative(directory,source).replaceAll('\\','/'),source:identity(source),source_keys:keys,triangles:counts.reduce((a,b)=>a+b,0),
 geometry_signature:sha(Buffer.from(before)),assets:{'水面.glb':identity(target)},source_program:identity(import.meta.filename),scope:'两处真实水面完整属性、绕序、世界矩阵及材质严格回读；显示及资源另验',human_acceptance:'待人工验收'};
fs.writeFileSync(path.join(output,'水面核验.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({passed:true,triangles:receipt.triangles,bytes:receipt.assets['水面.glb'].bytes}));
