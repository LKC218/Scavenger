// 验证真实分块查询，以及预算、引用、并发、取消、摘要和过期请求的失败边界。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {DownloadStore,RegionData,RequestPool} from '../../src/地图验证/区域数据.mjs';
import {TriangleSupportTile} from '../../src/地图验证/精细支撑.mjs';

const base=path.resolve(process.argv[2]??''),run=process.argv[3];
if(!base.startsWith(path.resolve('local-data')+path.sep)||!run||!/^[A-Za-z0-9_-]+$/.test(run))throw new Error('核验目录无效');
const output=path.join(base,'区域接口核验',run);fs.mkdirSync(path.dirname(output),{recursive:true});fs.mkdirSync(output);
const index=JSON.parse(fs.readFileSync(path.join(base,'地表索引.json'),'utf8'));
const tests=[],test=async(name,execute)=>{await execute();tests.push({name,passed:true});};
const b=index.bounds,box=(x,z=0)=>({minX:b.minX+x*125+1,maxX:b.minX+x*125+124,minZ:b.minZ+z*125+1,maxZ:b.minZ+z*125+124});
const array=data=>data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength);
const fetcher=async(url,{signal})=>{if(signal?.aborted)throw new DOMException('取消','AbortError');return {ok:true,arrayBuffer:async()=>array(fs.readFileSync(new URL(url)))};};
const downloads=new DownloadStore({fetcher}),regions=new RegionData(index,pathToFileURL(base+path.sep),downloads,{limit:1048576});
await test('未加载区域返回pending且高度NaN，越界为unsupported',async()=>{
 assert.equal(regions.query(b.minX+12,b.minZ+12).state,'pending');assert.ok(Number.isNaN(regions.query(b.minX+12,b.minZ+12).height));assert.equal(regions.query(b.minX-1,b.minZ+12).state,'unsupported');
});
await test('显示释放不解除单位引用；全图预算拒绝时保留已有区域',async()=>{
 await regions.prepare('unit',box(0));await regions.prepare('camera',box(0));regions.release('camera');regions.trim(1048576-regions.bytes);
 assert.equal(regions.query(b.minX+12,b.minZ+12).state,'ready');
 await assert.rejects(regions.prepare('too-large',b),/预算/);assert.equal(regions.query(b.minX+12,b.minZ+12).state,'ready');assert.equal(regions.stats().reserved,0);
});
await test('共享相同区域且两个owner分别释放，无非预期驻留',async()=>{
 await regions.prepare('camera',box(1));const before=regions.bytes;await regions.prepare('command',box(1));assert.equal(regions.bytes,before);regions.release('camera');assert.equal(regions.owners.size,2);regions.release('unit');regions.release('command');assert.ok(regions.trim(1048576));assert.equal(regions.bytes,0);
});
await test('同一区域快速重置和重取不会继承已取消请求',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);let first=true;
 const store=new RegionData(index,pathToFileURL(base+path.sep),downloads,{loadTile:async(row,signal)=>{
  if(first){first=false;await gate;}if(signal.aborted)throw new DOMException('取消','AbortError');return new TriangleSupportTile(array(fs.readFileSync(path.join(base,row.geometry))));
 }});
 const stale=store.prepare('camera',box(2));store.release('camera');const newest=store.prepare('camera',box(2));release();
 const results=await Promise.allSettled([stale,newest]);assert.equal(results[0].status,'rejected');assert.equal(results[1].status,'fulfilled');assert.equal(store.liveTokens.size,1);store.dispose();assert.equal(store.liveTokens.size,0);
});
await test('临时消费者反复准备及释放，不累积代次和操作元数据',async()=>{
 for(let i=0;i<1200;i++){const owner='projectile:'+i;await regions.prepare(owner,box(4));regions.release(owner);}
 assert.equal(regions.owners.size,0);assert.equal(regions.liveTokens.size,0);assert.equal(regions.operations.size,0);assert.equal(regions.generations.size,0);
});
await test('目标准备的预算余量不清除有效单位引用，缺少余量时明确拒绝',async()=>{
 await regions.prepare('unit:headroom',box(4));assert.equal(regions.canPrepare(box(5),regions.limit),false);assert.equal(regions.query(b.minX+4*125+12,b.minZ+12).state,'ready');
 assert.equal(regions.canPrepare(box(4),regions.limit),true);regions.release('unit:headroom');
});
await test('元数据回收后复用消费者名称，旧取消信号不撤销新引用',async()=>{
 const controller=new AbortController();await regions.prepare('reuse',box(5),{signal:controller.signal});regions.release('reuse');await regions.prepare('reuse',box(6));controller.abort();
 assert.ok(regions.owners.has('reuse'));assert.equal(regions.query(b.minX+6*125+12,b.minZ+12).state,'ready');regions.release('reuse');
 assert.equal(regions.operations.size,0);assert.equal(regions.generations.size,0);
});
await test('并发队列最大4，排队取消不执行，活动取消由signal传递',async()=>{
 const pool=new RequestPool(4),controllers=Array.from({length:12},()=>new AbortController());let finish;
 const gate=new Promise(resolve=>finish=resolve),jobs=controllers.map((c,i)=>pool.run(async()=>{await gate;if(c.signal.aborted)throw new DOMException('取消','AbortError');return i;},c.signal));
 controllers[10].abort();controllers[0].abort();finish();const results=await Promise.allSettled(jobs);assert.equal(pool.maximum,4);assert.equal(results.filter(r=>r.status==='rejected').length,2);
});
await test('HTTP错误、长度错误和摘要错误均拒绝，缓存不假报成功',async()=>{
 const buffer=new Uint8Array([1,2,3]).buffer,store=new DownloadStore({fetcher:async()=>({ok:true,arrayBuffer:async()=>buffer})});
 await assert.rejects(store.get('test',{bytes:4}),/长度/);await assert.rejects(store.get('test',{bytes:3,sha256:'0'.repeat(64)}),/摘要/);assert.equal(store.bytes,0);
 const failed=new DownloadStore({fetcher:async()=>({ok:false,status:404})});await assert.rejects(failed.get('404'),/HTTP 404/);
});
await test('下载缓存按真实字节LRU回收且不超限',async()=>{
 const store=new DownloadStore({limit:8,fetcher:async()=>({ok:true,arrayBuffer:async()=>new Uint8Array(5).buffer})});await store.get('one');await store.get('two');assert.equal(store.bytes,5);assert.equal(store.cache.size,1);store.clear();assert.equal(store.bytes,0);
});
await test('共享下载只请求一次，一个消费者取消不影响另一消费者',async()=>{
 let finish,calls=0;const gate=new Promise(resolve=>finish=resolve),controller=new AbortController();
 const store=new DownloadStore({fetcher:async()=>{calls++;await gate;return {ok:true,arrayBuffer:async()=>new Uint8Array([7,8]).buffer};}});
 const cancelled=store.get('shared',undefined,controller.signal),kept=store.get('shared');controller.abort();finish();
 const results=await Promise.allSettled([cancelled,kept]);assert.equal(results[0].status,'rejected');assert.equal(results[1].status,'fulfilled');assert.equal(calls,1);assert.equal(store.received,2);assert.equal(store.bytes,2);
});
await test('坏支撑页和无效查询均拒绝',async()=>{
 const source=fs.readFileSync(path.join(base,index.chunks[0].geometry));
 for(const mutate of [d=>d.setUint32(0,0,true),d=>d.setUint32(4,99,true),d=>d.setUint32(12,9,true),d=>d.setFloat32(32,NaN,true),d=>d.setUint32(72,99,true)]){
  const buffer=array(source);mutate(new DataView(buffer));assert.throws(()=>new TriangleSupportTile(buffer));
 }
 const tile=new TriangleSupportTile(array(source));assert.throws(()=>tile.query(NaN,0));
});
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
fs.writeFileSync(path.join(output,'核验凭证.json'),JSON.stringify({passed:true,tests,index_sha256:hash(path.join(base,'地表索引.json')),sources:{'区域数据.mjs':hash('src/地图验证/区域数据.mjs'),'精细支撑.mjs':hash('src/地图验证/精细支撑.mjs'),'验证区域数据.mjs':hash(import.meta.filename)},scope:'接口与失败边界核验；不替代实际浏览器、碰撞或性能验收'},null,2));
regions.dispose();downloads.clear();console.log(JSON.stringify({passed:true,tests:tests.length}));
