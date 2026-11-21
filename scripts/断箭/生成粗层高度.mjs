// 小型全局高度只服务相机与粗路由；单位和碰撞始终读取精细区域页。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {TriangleSupportTile} from '../../src/地图验证/精细支撑.mjs';
const directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.resolve('local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('粗层输出边界无效');
const output=path.join(directory,'粗层',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const source=path.join(directory,'全图分块/r02'),indexFile=path.join(source,'地表索引.json'),index=JSON.parse(fs.readFileSync(indexFile,'utf8')),rows=new Map(index.chunks.map(c=>[c.id,c])),tiles=new Map();
const hash=b=>crypto.createHash('sha256').update(b).digest('hex'),width=513,height=513,b=index.bounds,dx=(b.maxX-b.minX)/(width-1),dz=(b.maxZ-b.minZ)/(height-1),values=new Float32Array(width*height);let unsupported=0;
for(let z=0;z<height;z++)for(let x=0;x<width;x++){
 const wx=b.minX+x*dx,wz=b.minZ+z*dz,xx=Math.min(71,Math.floor((wx-b.minX)/125)),zz=Math.min(71,Math.floor((wz-b.minZ)/125)),id=`${String(xx).padStart(2,'0')}-${String(zz).padStart(2,'0')}`;
 let tile=tiles.get(id);if(!tile){const row=rows.get(id),data=fs.readFileSync(path.join(source,row.geometry));if(hash(data)!==row.geometry_identity.sha256)throw new Error('粗层来源摘要变化');tile=new TriangleSupportTile(data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength));tiles.set(id,tile);}
 const result=tile.query(wx,wz);values[z*width+x]=result.height;if(result.state!=='ready')unsupported++;
}
const buffer=new ArrayBuffer(32+values.byteLength),head=new DataView(buffer);head.setUint32(0,0x48434142,true);head.setUint32(4,1,true);head.setUint32(8,width,true);head.setUint32(12,height,true);[b.minX,b.minZ,dx,dz].forEach((v,i)=>head.setFloat32(16+i*4,v,true));new Float32Array(buffer,32).set(values);
const file=path.join(output,'粗层高度.bin');fs.writeFileSync(file,new Uint8Array(buffer));
fs.writeFileSync(path.join(output,'粗层索引.json'),JSON.stringify({schema:'airbase-coarse-height-v1',file:'粗层高度.bin',width,height,bounds:b,spacing:[dx,dz],unsupported,identity:{bytes:buffer.byteLength,sha256:hash(new Uint8Array(buffer))},source_sha256:hash(fs.readFileSync(indexFile)),source_program_sha256:hash(fs.readFileSync(import.meta.filename)),scope:'相机和粗路由，不能代替精细碰撞或通行'},null,2));console.log(JSON.stringify({width,height,bytes:buffer.byteLength,unsupported}));
