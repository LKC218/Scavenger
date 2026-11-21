// 局部A*在真实工作器同一函数上检查绕障、封闭区、对角穿角和被拒绝边。
import fs from 'node:fs';import path from 'node:path';import assert from 'node:assert/strict';import crypto from 'node:crypto';
import {localPath} from '../../src/地图验证/区域寻路工作器.mjs';
const directory=path.resolve(process.argv[2]??''),run=process.argv[3];if(!directory.startsWith(path.resolve('local-data')+path.sep)||!run||!/^[A-Za-z0-9_-]+$/.test(run))throw new Error('寻路核验输出边界错误');
const output=path.join(directory,'区域寻路核验',run);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const patch={nx:13,nz:13,cell:4,x0:0,z0:0,passable:new Uint8Array(169).fill(1)};for(let z=0;z<13;z++)if(z!==10)patch.passable[z*13+6]=0;
let result=localPath(patch,{x:4,z:4},{x:44,z:4});assert.equal(result.state,'ready');assert.ok(result.path.some(p=>p.z===40));for(const id of result.ids)assert.equal(patch.passable[id],1);
patch.passable[10*13+6]=0;assert.equal(localPath(patch,{x:4,z:4},{x:44,z:4}).state,'unreachable');
const corner={nx:2,nz:2,cell:4,x0:0,z0:0,passable:new Uint8Array([1,0,0,1])};assert.equal(localPath(corner,{x:0,z:0},{x:4,z:4}).state,'unreachable');
const plain={nx:3,nz:3,cell:4,x0:0,z0:0,passable:new Uint8Array(9).fill(1)};result=localPath(plain,{x:0,z:0},{x:8,z:0},[[0,1],[1,0]]);for(let i=1;i<result.ids.length;i++)assert.notDeepEqual([result.ids[i-1],result.ids[i]],[0,1]);result=localPath(plain,{x:0,z:0},{x:8,z:0},[[-1,0]]);assert.notEqual(result.ids[0],0);
assert.equal(localPath({...plain,passable:new Uint8Array(9)},{x:0,z:0},{x:8,z:0}).state,'unreachable');
const sha=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify({passed:true,checks:['经过真实缺口绕障','封闭区不可达','不切过对角障碍角','动态拒绝边重寻路','拒绝不安全起点','全阻挡保持不可达'],worker_sha256:sha('src/地图验证/区域寻路工作器.mjs'),verifier_sha256:sha(import.meta.filename),scope:'工作器局部网格；真实全图、长路线及数据租约另验'},null,2));console.log(JSON.stringify({passed:true,checks:6}));
