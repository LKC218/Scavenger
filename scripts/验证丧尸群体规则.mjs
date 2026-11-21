// 固定步长长程规则检查；这些结果不作为真实浏览器帧率数据。
import assert from 'node:assert/strict';
import {Crowd} from '../src/丧尸群体测试/群体.mjs';
import {summarize} from '../src/丧尸群体测试/统计.mjs';

const checks=[];
for(const mode of ['open','channel']){
  const crowd=new Crowd(500,mode);crowd.patrol=false;
  if(mode==='channel')assert.equal(crowd.setTarget(0,10),false,'障碍内目标应拒绝');
  for(let tick=0;tick<3600;tick++){
    crowd.tick(1/30);
    if(tick%30===0){
      const h=crowd.health();assert(h.finite);assert.equal(h.insideObstacles,0);
      assert(crowd.agents.every(a=>Math.hypot(a.vx,a.vz)<=a.speed+.00001));
    }
  }
  const health=crowd.health();assert.equal(health.rightSide,500);assert(health.arrived>450);assert(health.moving<15);
  checks.push({name:mode+'：500只120秒全部到达目标侧并减速、无穿墙',health});
  crowd.setTarget(-17,0);
  for(let tick=0;tick<3600;tick++)crowd.tick(1/30);
  assert.equal(crowd.agents.filter(a=>a.x< -2).length,500);assert.equal(crowd.health().insideObstacles,0);
  checks.push({name:mode+'：更换反向目标后500只全部返回'});
}
for(const mode of ['static','animated']){
  const crowd=new Crowd(500,mode),before=crowd.agents.map(a=>[a.x,a.z]);
  for(let tick=0;tick<300;tick++)crowd.tick(1/30);
  assert.deepEqual(crowd.agents.map(a=>[a.x,a.z]),before);checks.push({name:mode+'：原地负载不偷偷位移'});
}
const crowd=new Crowd(500,'open');const initial=crowd.agents.map(a=>[a.x,a.z,a.phase]);crowd.tick(1);crowd.reset(500,'open');assert.deepEqual(crowd.agents.map(a=>[a.x,a.z,a.phase]),initial);
checks.push({name:'固定种子重置的坐标与动画相位一致'});
// 非均匀测试夹具，专门检查FPS不是瞬时FPS的算术平均。
const rows=[10,20,100].map(frame=>({frame,simulation:0,animation:0,sync:0,submit:0,gpu:null,visible:500,animated:500,moving:500,calls:506,triangles:400000,dropped:0}));
const summary=summarize(rows);assert(Math.abs(summary.fps-3000/130)<1e-9);assert.equal(summary.p95Ms,100);assert.equal(summary.gpuMs,null);
checks.push({name:'不均匀帧间隔统计、分位数和GPU缺项口径'});
for(const count of [750,1000,5000]){
  const large=new Crowd(count,'channel');
  assert.equal(large.health().insideObstacles,0,'扩容生成不能越界或生成在障碍内');
  for(let i=0;i<90;i++)large.tick(1/30);
  assert(large.health().finite);assert.equal(large.health().insideObstacles,0);
  assert.equal(large.agents.length,count);
  checks.push({name:count+'只扩容生成及3秒模拟坐标有效、不越界或穿障碍'});
}
assert.throws(()=>new Crowd(5001),/角色数量范围/);
checks.push({name:'界面保护上限同时由模拟模块约束'});
console.log(JSON.stringify({scope:'纯逻辑数值检查，不是浏览器性能实测',passed:true,checks},null,2));
