// 使用浏览器同一支撑解码器逐块检查样点和边界，并保存独立BVH的抽样输入。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {TriangleSupportTile} from '../../src/地图验证/精细支撑.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),directory=path.resolve(process.argv[2]||''),revision=process.argv[3];
if(!directory.startsWith(path.join(root,'local-data')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('核验目录或修订无效');
const indexFile=path.join(directory,'地表索引.json'),index=JSON.parse(fs.readFileSync(indexFile,'utf8'));
const output=path.join(directory,'支撑核验',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');
const samples=[],seams=new Map();let checked=0,unsupported=0,maxSeam=0,multipleLayers=0,bytes=0,roadFeatures=0,lowerLayers=0;
for(const chunk of index.chunks){
 if(chunk.empty)continue;
 const buffer=fs.readFileSync(path.join(directory,chunk.geometry));
 if(hash(buffer)!==chunk.geometry_identity.sha256)throw new Error('支撑文件摘要变化');
 const tile=new TriangleSupportTile(buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.byteLength));bytes+=buffer.byteLength;
 const [minX,maxX,minZ,maxZ]=chunk.bounds;
 for(const az of [.1,.5,.9])for(const ax of [.1,.5,.9]){
  const x=minX+(maxX-minX)*ax,z=minZ+(maxZ-minZ)*az,result=tile.query(x,z);
  checked++;if(result.state!=='ready')unsupported++;if(result.layers?.some(h=>Math.abs(h.height-result.height)>.3)){
   multipleLayers++;
   const lower=result.layers.find(h=>result.height-h.height>.3),layer=lower.source>>>20;
   const selected=tile.query(x,z,{referenceY:lower.height,layer});
   if(Math.abs(selected.height-lower.height)>.001)throw new Error('多层支撑提示被错误提升到最高层');
   samples.push({chunk:chunk.id,x,z,state:selected.state,height:selected.height,query_layer:layer,referenceY:lower.height,feature:'下层支撑'});lowerLayers++;
  }
  samples.push({chunk:chunk.id,x,z,state:result.state,height:Number.isFinite(result.height)?result.height:null,source:result.source,role:result.role});
 }
 // 各块道路三角形的分布样点，包含跨块裁切边缘；不只验机场平地。
 const road=[];
 for(let i=0;i<tile.count;i++)if(tile.data.getUint32(32+i*44+40,true)===2){
  const s=32+i*44,d=tile.data,ax=d.getFloat32(s,true),az=d.getFloat32(s+8,true),bx=d.getFloat32(s+12,true),bz=d.getFloat32(s+20,true),cx=d.getFloat32(s+24,true),cz=d.getFloat32(s+32,true);
  if(Math.abs((bx-ax)*(cz-az)-(bz-az)*(cx-ax))>1e-6)road.push(i);
 }
 const selectedRoad=new Set(road.filter((_,i)=>i%Math.max(1,Math.floor(road.length/6))===0).slice(0,6));
 for(const triangle of selectedRoad){
  const start=32+triangle*44,coords=Array.from({length:9},(_,i)=>tile.data.getFloat32(start+i*4,true));
  const x=tile.x+(coords[0]+coords[3]+coords[6])/3,z=tile.z+(coords[2]+coords[5]+coords[8])/3,referenceY=(coords[1]+coords[4]+coords[7])/3,layer=tile.data.getUint32(start+36,true)>>>20;
  const result=tile.query(x,z,{referenceY,layer});if(result.state!=='ready'||Math.abs(result.height-referenceY)>.001)throw new Error('道路交接源层查询不符');
  samples.push({chunk:chunk.id,x,z,state:result.state,height:result.height,query_layer:layer,referenceY,feature:'道路分层与交接'});roadFeatures++;
 }
 for(const axis of [0,1])for(const side of [0,1])for(const fraction of [.1,.3,.5,.7,.9]){
  const x=axis===0?(side?maxX:minX):minX+125*fraction,z=axis===1?(side?maxZ:minZ):minZ+125*fraction;
  const result=tile.query(x,z);if(result.state!=='ready')continue;
  const key=`${x.toFixed(5)}:${z.toFixed(5)}:${result.source>>>20}`;
  const previous=seams.get(key);
  if(previous&&previous.chunk!==chunk.id)maxSeam=Math.max(maxSeam,Math.abs(previous.height-result.height));
  else seams.set(key,{chunk:chunk.id,height:result.height});
 }
}
const result={status:'分块解码与样点完成，待独立BVH比较',checked_samples:checked,unsupported,multiple_layer_samples:multipleLayers,
 max_same_source_seam_error_m:maxSeam,geometry_bytes:bytes,road_features:roadFeatures,lower_layer_features:lowerLayers,index_sha256:hash(fs.readFileSync(indexFile)),
 sources:{verifier:hash(fs.readFileSync(import.meta.filename)),decoder:hash(fs.readFileSync(path.join(root,'src/地图验证/精细支撑.mjs')))},scope:'精细原三角形查询；无支撑保持未知，不把解码成功当作独立高度验收'};
fs.writeFileSync(path.join(output,'样点.json'),JSON.stringify(samples));fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));if(maxSeam>.001)throw new Error('同源精细支撑接缝超过0.001米');
