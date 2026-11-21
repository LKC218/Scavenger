// 静态模型独立细节层。骨骼或变形模型保持原拓扑，不套用静态减面。
import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import {pathToFileURL} from 'node:url';
const root=path.resolve(import.meta.dirname,'../..'),out=path.join(root,'local-data/网页资源库/全库第一版'),modules=path.join(root,'local-data/tools/网页资源优化/1/node_modules');
if(fs.existsSync(path.join(root,'local-data/资源库/当前版本.json')))throw Error('旧几何层阶段已退役，请使用 scripts/资源库/维护资源库.py rebuild，在统一待入库目录重建。');
const load=async p=>import(pathToFileURL(path.join(modules,p)).href);
const {NodeIO}=await load('@gltf-transform/core/dist/index.js');const {ALL_EXTENSIONS}=await load('@gltf-transform/extensions/dist/index.js');const {simplify,weld}=await load('@gltf-transform/functions/dist/index.js');const {MeshoptDecoder,MeshoptEncoder,MeshoptSimplifier}=await load('meshoptimizer/index.js');await Promise.all([MeshoptDecoder.ready,MeshoptEncoder.ready,MeshoptSimplifier.ready]);
const io=new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder,'meshopt.encoder':MeshoptEncoder});
const repair=process.argv.includes('--repair'),fallback=process.argv.includes('--fallback');
const triangles=doc=>doc.getRoot().listMeshes().reduce((n,m)=>n+m.listPrimitives().reduce((s,p)=>s+(p.getIndices()?.getCount()||p.getAttribute('POSITION').getCount())/3,0),0);
for(const file of fs.readdirSync(path.join(out,'验证')).filter(x=>x.endsWith('.model.json'))){
 const source=JSON.parse(fs.readFileSync(path.join(out,'验证',file)));if(!source.passed)continue;
 const target=path.join(out,'验证',source.source_sha256+'.lod.json');if(fs.existsSync(target)&&!repair&&!fallback)continue;
 const old=fs.existsSync(target)?JSON.parse(fs.readFileSync(target)):null;
 const failed=new Set();
 if(repair||fallback){for(const level of [1,2])for(const browser of ['chrome','msedge']){const p=path.join(out,'浏览器验证',browser,source.source_sha256+`.lod${level}.json`);if(fs.existsSync(p)&&!JSON.parse(fs.readFileSync(p)).visual_threshold_passed)failed.add(level);}if(failed.has(2))failed.add(1);if(!failed.size)continue;}
 const result={source_sha256:source.source_sha256,variants:[]};
 try{
  const base=await io.read(path.join(root,source.path));
  if(base.getRoot().listSkins().length||base.getRoot().listMeshes().some(m=>m.listPrimitives().some(p=>p.listTargets().length))){result.skipped='骨骼或变形模型保留基础拓扑';}
  else for(const [level,baseRatio,baseError] of [[1,.55,.001],[2,.25,.003]]){
   if((repair||fallback)&&!failed.has(level)){result.variants.push(old.variants.find(v=>v.level===level));continue;}
   const ratio=repair?(level===1?.75:.55):baseRatio,error=repair?baseError/10:baseError;
   const doc=await io.read(path.join(root,source.path)),before=triangles(doc);const nodes=doc.getRoot().listNodes().map(n=>n.getName());
   if(!fallback)await doc.transform(weld(),simplify({simplifier:MeshoptSimplifier,ratio,error,lockBorder:true}));
   const dst=path.join(out,'模型',source.source_sha256+`.lod${level}.glb`);await io.write(dst,doc);const restored=await io.read(dst),after=triangles(restored);
   if(JSON.stringify(nodes)!==JSON.stringify(restored.getRoot().listNodes().map(n=>n.getName()))||after>before||after<=0)throw Error('细节层回读结构或三角形数不符');
   const buf=fs.readFileSync(dst);result.variants.push({level,ratio:fallback?1:ratio,error:fallback?0:error,visual_repair:repair,base_fallback:fallback,lockBorder:true,before_triangles:before,triangles:after,path:path.relative(root,dst).split(path.sep).join('/'),bytes:buf.byteLength,sha256:crypto.createHash('sha256').update(buf).digest('hex')});
  }
  result.passed=true;
 }catch(e){result.passed=false;result.error=String(e.stack||e);}
 fs.writeFileSync(target,JSON.stringify(result,null,2));console.log(source.source_sha256.slice(0,12),result.passed,result.variants.map(v=>v.triangles));
}
