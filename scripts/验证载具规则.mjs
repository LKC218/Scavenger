/** 以真实模拟推进交战规则，覆盖异步弹丸与命令生命周期。 */
import assert from 'node:assert/strict';
import {Simulation,findPath,OBSTACLES,segmentBox} from '../src/载具靶场/模拟.mjs';
const results=[];
function check(name,run){run();results.push(name);}
const advance=(sim,seconds)=>{for(let i=0;i<seconds*60;i++)sim.step(1/60);};
const solo=kind=>{const s=new Simulation();s.units=s.units.filter(u=>u.kind===kind).slice(0,1);s.units[0].x=-44;s.units[0].z=12;return s;};
check('闲置与普通移动不主动开火',()=>{const s=solo('tank');advance(s,3);s.command(['tank-1'],{type:'move',point:{x:-42,z:0}});advance(s,6);assert.equal(s.shots,0);});
check('坦克指定攻击产生独立飞行弹丸并伤害靶标',()=>{const s=solo('tank');s.command(['tank-1'],{type:'attack',target:'target-1'});advance(s,.6);assert.equal(s.shots,1);assert(s.projectiles.length);assert.equal(s.targets[0].hp,720);advance(s,1);assert(s.targets[0].hp<720);});
check('超射程自动接近并停车攻击',()=>{const s=solo('tank');s.units[0].z=60;s.command(['tank-1'],{type:'attack',target:'target-1'});advance(s,12);assert(s.units[0].z<30);assert(s.shots>0);assert.equal(s.units[0].moving,false);});
check('受遮挡停止提示，玩家负责重新选位',()=>{const s=solo('tank');s.units[0].x=-4;s.units[0].z=14;s.command(['tank-1'],{type:'attack',target:'target-2'});advance(s,8);assert.equal(s.shots,0);assert(s.units[0].state.includes('受阻'));assert.equal(s.units[0].z,14);});
check('取消旧待发任务，但已发弹丸继续',()=>{const s=solo('tank');s.command(['tank-1'],{type:'attack',target:'target-1'});advance(s,.6);s.cancel(['tank-1']);advance(s,8);assert.equal(s.shots,1);assert(s.targets[0].hp<720);assert.equal(s.units[0].job,null);});
check('导弹发射后自主追踪，射手可立即移动',()=>{const s=solo('heli');s.units[0].weapon='missile';s.command(['heli-1'],{type:'attack',target:'target-1'});advance(s,.8);assert(s.projectiles[0].guided);s.command(['heli-1'],{type:'move',point:{x:-15,z:20}});advance(s,2);assert(s.units[0].x>-42);assert(s.targets[0].hp<720);});
check('攻击地面每单位仅发射一轮，之后队列继续',()=>{const s=solo('tank');s.command(['tank-1'],{type:'ground',point:{x:-44,z:-17}});s.command(['tank-1'],{type:'move',point:{x:-50,z:10}},true);advance(s,10);assert.equal(s.shots,1);assert.equal(s.units[0].job,null);});
check('暂停冻结时间与弹丸，允许下令后恢复',()=>{const s=solo('tank');s.paused=true;s.command(['tank-1'],{type:'attack',target:'target-1'});advance(s,10);assert.equal(s.time,0);assert.equal(s.shots,0);s.paused=false;advance(s,2);assert(s.shots>0);});
check('友军不拦截直接或爆炸伤害',()=>{const s=solo('tank'),friend={...s.units[0],id:'friend',x:-44,z:-10,job:null,queue:[],path:[]};s.units.push(friend);s.command(['tank-1'],{type:'attack',target:'target-1'});advance(s,2);assert(s.targets[0].hp<720);assert.equal(friend.state,'待命');assert(!('hp' in friend));});
check('Q攻击移动交战后继续路线，普通移动不交战',()=>{const s=solo('tank');s.targets=s.targets.slice(0,1);s.targets[0].hp=100;s.command(['tank-1'],{type:'attackMove',point:{x:-58,z:-30}});advance(s,16);assert.equal(s.kills,1);assert.equal(s.units[0].job,null);assert(s.units[0].z<-28);});
check('路径不穿墙，倒车仅作用坦克，重置清空旧代状态',()=>{const path=findPath({x:-4,z:24},{x:-4,z:-38});assert(path.length>1);let previous={x:-4,y:1,z:24};for(const p of path){const next={...p,y:1};assert(!OBSTACLES.some(o=>segmentBox(previous,next,o,2.5)!==null));previous=next;}
const s=new Simulation();assert.equal(s.command(s.units.map(u=>u.id),{type:'reverse',point:{x:0,z:50}}),5);s.reset();assert.equal(s.units.length,10);assert.equal(s.projectiles.length,0);assert(s.units.every(u=>!u.job&&!u.queue.length));});
console.log(JSON.stringify({passed:results.length,checks:results},null,2));
