import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import assert from 'node:assert/strict';
import {decodeSimulationPackage} from '../../src/地图验证/模拟分区.mjs';
const directory=path.resolve(process.argv[2]??''),revision=process.argv[3];if(!directory.startsWith(path.resolve('local-data/断箭/批次')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('模拟分区核验目录无效');
const output=path.join(directory,'模拟分区/r01/独立核验',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const index=JSON.parse(fs.readFileSync(path.join(directory,'模拟分区/r01/模拟分区索引.json'),'utf8')),surface=new Map(JSON.parse(fs.readFileSync(path.join(directory,'全图分块/r02/地表索引.json'),'utf8')).chunks.map(c=>[c.id,c])),physics=new Map(JSON.parse(fs.readFileSync(path.join(directory,'碰撞分页/r01/碰撞索引.json'),'utf8')).chunks.map(c=>[c.id,c]));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),buffers=new Map();let count=0;
for(const [id,key] of Object.entries(index.cells)){
 let buffer=buffers.get(key);if(!buffer){const record=index.packages[key],data=fs.readFileSync(path.join(directory,record.file));assert.equal(sha(data),record.identity.sha256);buffer=data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength);buffers.set(key,buffer);}
 const [x,z]=id.split('-').map(Number),parts=decodeSimulationPackage(buffer,z*72+x);assert.equal(sha(new Uint8Array(parts.support)),surface.get(id).geometry_identity.sha256);assert.equal(sha(new Uint8Array(parts.collision)),physics.get(id).identity.sha256);count++;
}
const original=buffers.values().next().value;for(const mutate of [d=>d.setUint32(0,0,true),d=>d.setUint32(12,16,true),d=>d.setUint32(20,0,true),d=>d.setUint32(28,0,true)]){const copy=original.slice(0);mutate(new DataView(copy));assert.throws(()=>decodeSimulationPackage(copy,0));}assert.throws(()=>decodeSimulationPackage(original,5184));assert.throws(()=>decodeSimulationPackage(original.slice(0,-1),0));
fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify({passed:true,cells:count,packages:buffers.size,checks:['每个内部支撑及碰撞SHA256与冻结原件相同','头部／偏移／缺格／截断拒绝'],decoder_sha256:sha(fs.readFileSync('src/地图验证/模拟分区.mjs')),verifier_sha256:sha(fs.readFileSync(import.meta.filename))},null,2));console.log(JSON.stringify({passed:true,cells:count,packages:buffers.size}));
