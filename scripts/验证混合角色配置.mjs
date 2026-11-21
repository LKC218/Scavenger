// 仅验证配置与分配规则；合成元数据不代表任何GLB、动画或性能验收。
import assert from 'node:assert/strict';
import {modelDescriptors,assignModels,modelDistribution,summarizeModelRows} from '../src/丧尸群体测试/角色配置.mjs';
const models=Array.from({length:13},(_,i)=>({id:i?`model:zombie-${String(i).padStart(2,'0')}`:'model:zombie-runtime',source_mesh_index:i,
  bones:i===11?21:23,materials:1,url:`/actors/${i}.glb`,asset:{bytes:1,sha256:'a'.repeat(64)},
  clips:[{name:'待机',duration_s:2},{name:'移动',duration_s:1.25}]}));
const manifest={delivery:{batch_id:'纯数据规则测试'},models};
assert.equal(modelDescriptors(manifest)[11].bones,21);
for(const count of [100,300,500,700,5000]){
  const a=assignModels(count,models,20261103),distribution=modelDistribution(models,a),counts=distribution.map(r=>r.count);
  assert.equal(counts.reduce((s,n)=>s+n,0),count);assert.equal(counts.length,13);
  assert.ok(Math.max(...counts)-Math.min(...counts)<=1);assert.ok(Math.min(...counts)>0);
  assert.deepEqual(a,assignModels(count,models,20261103));assert.notDeepEqual(a,assignModels(count,models,20261104));
  assert.ok(assignModels(count,models,1,'single').every(x=>x===0));
}
const rejected=[];
function rejects(label,change){const copy=structuredClone(manifest);change(copy);assert.throws(()=>modelDescriptors(copy));rejected.push(label);}
rejects('重复稳定ID',m=>m.models[1].id=m.models[0].id);
rejects('重复源网格',m=>m.models[1].source_mesh_index=0);
rejects('遗漏源网格',m=>m.models[12].source_mesh_index=13);
rejects('未冻结身份',m=>delete m.delivery);
rejects('缺少动作',m=>m.models[8].clips.pop());
rejects('无效骨骼',m=>m.models[11].bones=0);
rejects('文件摘要缺失',m=>delete m.models[2].asset.sha256);
rejects('外部资源地址',m=>m.models[3].url='https://example.invalid/asset.glb');
assert.throws(()=>assignModels(5001,models,1));
const legacy={bones:23,asset:{bytes:1,sha256:'b'.repeat(64)},checks:{待机:{duration_s:2},移动:{duration_s:1.25}}};
assert.equal(modelDescriptors(legacy).length,1);assert.equal(modelDescriptors(legacy)[0].url,'/asset.glb');
const distribution=modelDistribution(models,assignModels(500,models,1));
const row={models:distribution.map(m=>({id:m.id,count:m.count,visible:m.count,animated:m.count,moving:m.count}))};
assert.ok(summarizeModelRows([row,row],distribution).every(m=>m.complete&&m.minAnimated===m.count&&m.minVisible===m.count));
const incomplete=structuredClone(row);incomplete.models.pop();
assert.equal(summarizeModelRows([row,incomplete],distribution).at(-1).complete,false);
const staticRow=structuredClone(row);staticRow.models.forEach(m=>m.animated=0);
assert.ok(summarizeModelRows([staticRow],distribution).every(m=>m.complete&&m.minAnimated===0));
assert.ok(summarizeModelRows([],distribution).every(m=>!m.complete&&m.minVisible===null));
console.log(JSON.stringify({scope:'纯数据配置与分配；未加载模型、未测FPS',balancedCounts:[100,300,500,700,5000],accepts21Bones:true,rejected,perModelMissingEvidenceRejected:true,legacySingleCompatible:true},null,2));
