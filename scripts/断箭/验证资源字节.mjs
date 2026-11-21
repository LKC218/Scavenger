// 使用共享缓冲、子视图和交错属性，独立验证CPU/GPU口径，避免错误预算拒绝。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {geometryAllocations} from '../../src/地图验证/资源字节.mjs';
const directory=path.resolve(process.argv[2]??''),revision=process.argv[3];
if(!directory.startsWith(path.resolve('local-data')+path.sep)||!revision||!/^[A-Za-z0-9_-]+$/.test(revision))throw new Error('核验输出边界无效');
const output=path.join(directory,'资源字节核验',revision);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const sum=map=>[...map.values()].reduce((a,b)=>a+b,0),buffer=new ArrayBuffer(256),index={array:new Uint16Array(6)};
const a={attributes:{position:{array:new Float32Array(buffer,0,12)},normal:{array:new Float32Array(buffer,48,12)}},index},b={attributes:{uv:{array:new Float32Array(buffer,96,8)}}};
let result=geometryAllocations([a,b]);assert.equal(sum(result.gpu),140);assert.equal(sum(result.cpu),268);
const shared={array:new Float32Array(8)},p={isInterleavedBufferAttribute:true,data:shared},n={isInterleavedBufferAttribute:true,data:shared};
result=geometryAllocations([{attributes:{position:p,normal:n}},{attributes:{position:p}}]);assert.equal(sum(result.gpu),32);assert.equal(sum(result.cpu),32);
const x={array:new Float32Array(buffer,0,4)},y={array:new Float32Array(buffer,0,4)};
result=geometryAllocations([{attributes:{x,y}}]);assert.equal(sum(result.gpu),32);assert.equal(sum(result.cpu),256);
const sha=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify({passed:true,checks:['共享大缓冲的子视图按上传长度估GPU、按底层缓冲估CPU','交错属性只上传一次','不同属性包装分别上传但共享CPU数据'],source_sha256:sha('src/地图验证/资源字节.mjs'),verifier_sha256:sha(import.meta.filename)},null,2));console.log(JSON.stringify({passed:true,checks:3}));
