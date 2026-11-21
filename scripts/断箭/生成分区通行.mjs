// 全局只保留125米候选连接成本，具体通行仍由已就绪的局部4米网格与源形状验证。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {TriangleSupportTile} from '../../src/地图验证/精细支撑.mjs';
const directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.resolve('local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('粗通行输出边界无效');
const output=path.join(directory,'通行粗层',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const source=path.join(directory,'全图分块/r02'),index=JSON.parse(fs.readFileSync(path.join(source,'地表索引.json'),'utf8')),physics=JSON.parse(fs.readFileSync(path.join(directory,'碰撞分页/r01/碰撞索引.json'),'utf8')),counts=new Map(physics.chunks.map(c=>[c.id,c.count]));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex'),nodes=[];let bridgeCandidates=0;
for(const chunk of index.chunks){
 const buffer=fs.readFileSync(path.join(source,chunk.geometry));if(hash(buffer)!==chunk.geometry_identity.sha256)throw new Error('粗通行来源变化');const tile=new TriangleSupportTile(buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.byteLength));
 let water=0,steep=0,unsupported=0,road=false;for(const x of [.2,.5,.8])for(const z of [.2,.5,.8]){const q=tile.query(chunk.bounds[0]+125*x,chunk.bounds[2]+125*z);water+=q.waterDepth>.25?1:0;steep+=q.normalY<Math.cos(Math.PI/6)?1:0;unsupported+=q.state!=='ready'?1:0;}
 for(let i=0;i<tile.count;i++)if(tile.data.getUint32(32+i*44+40,true)===2){road=true;const at=32+i*44,d=tile.data,x=tile.x+(d.getFloat32(at,true)+d.getFloat32(at+12,true)+d.getFloat32(at+24,true))/3,z=tile.z+(d.getFloat32(at+8,true)+d.getFloat32(at+20,true)+d.getFloat32(at+32,true))/3,y=(d.getFloat32(at+4,true)+d.getFloat32(at+16,true)+d.getFloat32(at+28,true))/3,ground=tile.query(x,z,{roles:[1,4]});if(ground.state==='ready'&&y-ground.height>2&&tile.intersections(x,z,{roles:[5]}).length)bridgeCandidates++;}
 nodes.push({id:chunk.id,x:tile.x,z:tile.z,height:tile.query(tile.x,tile.z).height,cost:1+(road?0:Math.min(12,(counts.get(chunk.id)??0)/12))+water*20+steep*3+unsupported*100,road,water_samples:water,unsupported});
}
const data={schema:'airbase-coarse-navigation-v1',bounds:index.bounds,grid:index.grid,chunk_m:125,nodes,bridge_water_candidates:bridgeCandidates,source:hash(fs.readFileSync(path.join(source,'地表索引.json'))),physics_source:hash(fs.readFileSync(path.join(directory,'碰撞分页/r01/碰撞索引.json'))),source_program:hash(fs.readFileSync(import.meta.filename)),policy:'候选路线成本，不把粗点或未知区域认定为精细可通行；每段由源支撑和Collider验证'};
const file=path.join(output,'分区通行.json');fs.writeFileSync(file,JSON.stringify(data));console.log(JSON.stringify({nodes:nodes.length,bytes:fs.statSync(file).size,bridge_water_candidates:bridgeCandidates}));
