// 源形状页完整回读与旋转盒、胶囊、细树干、桥下和坏输入边界。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {CollisionShape} from '../../src/地图验证/碰撞形状.mjs';
import {decodeCollisionPage} from '../../src/地图验证/碰撞页.mjs';
const directory=path.resolve(process.argv[2]??''),run=process.argv[3];
if(!directory.startsWith(path.resolve('local-data')+path.sep)||!run||!/^[A-Za-z0-9_-]+$/.test(run))throw new Error('核验目录或修订无效');
const output=path.join(directory,'独立核验',run);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],record={group:1,sourceIndex:2,prototype:0,matrix:identity};
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} / ${b}`),tests=[];
const c=Math.SQRT1_2,rotation=[c,0,-c,0,0,1,0,0,c,0,c,0,10,3,-4,1];
const box=new CollisionShape({kind:'box',params:[2,3,1],matrix:rotation,movement:true,ballistics:true},record,0);
const transform=p=>({x:rotation[0]*p.x+rotation[8]*p.z+10,y:p.y+3,z:rotation[2]*p.x+rotation[10]*p.z-4});
close(box.segment(transform({x:-10,y:0,z:0}),transform({x:10,y:0,z:0})),.4);
assert.equal(box.overlaps(11.9,-2.1,2,0),false);tests.push('旋转盒连续射线和AABB角落不误挡');
const capsule=new CollisionShape({kind:'capsule',params:[2,3,0],matrix:identity,movement:true,ballistics:true},record,1);
close(capsule.segment({x:-10,y:0,z:0},{x:10,y:0,z:0}),.4);close(capsule.segment({x:0,y:-10,z:0},{x:0,y:10,z:0}),.25);tests.push('胶囊侧面及半球端点');
const trunk=new CollisionShape({kind:'cylinder',params:[.4,2,0],matrix:identity,movement:true,ballistics:true},record,2);
assert.equal(trunk.segment({x:3,y:0,z:0},{x:5,y:0,z:0}),null);assert.equal(trunk.segment({x:.3,y:0,z:0},{x:.3,y:1,z:0}),0);tests.push('真实细树干阻挡，树冠范围不自动实心');
const elevated=[...identity];elevated[13]=10;
const bridge=new CollisionShape({kind:'box',params:[10,1,10],matrix:elevated,movement:true,ballistics:true},record,3);
assert.equal(bridge.overlaps(0,0,0,5.8),false);assert.equal(bridge.overlaps(0,0,9,0),true);tests.push('桥下和桥上分别按高度求交');
assert.throws(()=>new CollisionShape({kind:'box',params:[1,1,1],matrix:new Array(16).fill(0)},record,4));tests.push('不可逆变换拒绝');
const indexFile=path.join(directory,'碰撞索引.json'),index=JSON.parse(fs.readFileSync(indexFile,'utf8'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),unique=new Map();let count=0,references=0;
for(const chunk of index.chunks){
 const buffer=fs.readFileSync(path.join(directory,chunk.file));if(sha(buffer)!==chunk.identity.sha256)throw new Error('碰撞文件摘要变化');
 const array=buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.byteLength),page=decodeCollisionPage(array,{prototypeCount:index.templates.length,expectedCount:chunk.count});
 assert.ok(page.bytes>=chunk.decoded_budget_bytes);references+=page.count;
 for(const shape of page.shapes){
  const old=unique.get(shape.id),signature=sha(Buffer.from([...shape.matrix,...shape.params,...shape.bounds].join(',')));
  if(old){assert.equal(signature,old.signature);old.pages.add(chunk.id);}else{unique.set(shape.id,{signature,pages:new Set([chunk.id]),bounds:shape.bounds});count++;}
 }
}
assert.equal(count,Object.values(index.unique_shapes_by_kind).reduce((a,b)=>a+b,0));tests.push('全部真实碰撞页摘要、来源及形状包围盒完整回读');
for(const shape of unique.values()){
 const b=shape.bounds,bounds=index.bounds,x0=Math.max(0,Math.floor((b[0]-bounds.minX)/125)),x1=Math.min(71,Math.floor((b[3]-bounds.minX)/125)),z0=Math.max(0,Math.floor((b[2]-bounds.minZ)/125)),z1=Math.min(71,Math.floor((b[5]-bounds.minZ)/125));
 // 编码float32恰落边界的极小误差允许额外相邻引用，禁止缺少确定内侧的页。
 for(let z=z0;z<=z1;z++)for(let x=x0;x<=x1;x++){
  const minX=bounds.minX+x*125,minZ=bounds.minZ+z*125;if(b[0]<minX+125-.002&&b[3]>minX+.002&&b[2]<minZ+125-.002&&b[5]>minZ+.002)assert.ok(shape.pages.has(`${String(x).padStart(2,'0')}-${String(z).padStart(2,'0')}`));
 }
}tests.push('跨块完整包围盒引用无缺项');
const nonempty=index.chunks.find(c=>c.count),original=fs.readFileSync(path.join(directory,nonempty.file));
for(const mutate of [d=>d.setUint32(0,0,true),d=>d.setUint32(12,120,true),d=>d.setUint32(32,99,true),d=>d.setFloat32(36,NaN,true),d=>d.setFloat32(116,999999,true)]){
 const array=original.buffer.slice(original.byteOffset,original.byteOffset+original.byteLength);mutate(new DataView(array));assert.throws(()=>decodeCollisionPage(array));
}assert.throws(()=>decodeCollisionPage(original.buffer.slice(original.byteOffset,original.byteOffset+original.byteLength-1)));tests.push('坏身份、类型、矩阵、包围盒和截断全部拒绝');
const sourceFiles=['src/地图验证/碰撞形状.mjs','src/地图验证/碰撞页.mjs',import.meta.filename];
const result={passed:true,tests,unique_shapes:count,spatial_references:references,index_sha256:sha(fs.readFileSync(indexFile)),sources:Object.fromEntries(sourceFiles.map(f=>[path.basename(f),sha(fs.readFileSync(f))])),scope:'真实形状、分页引用及连续求交；载具通行和浏览器负载另验',human_acceptance:'待人工验收'};
fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({passed:true,unique_shapes:count,tests:tests.length}));
