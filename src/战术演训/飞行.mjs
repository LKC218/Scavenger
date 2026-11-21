/** 游戏化固定翼：安全高度、持续前进、有限转向和单次攻击任务。 */
import {clamp,dist,wrap,yawTo,turn} from './地图.mjs';
export const FLIGHT={speed:52,turnRate:.55,radius:110,altitude:72,gravity:9.8};
export function orbitHere(u,center=null){const r=FLIGHT.radius,p=center??{x:u.x+Math.cos(u.yaw)*r,z:u.z-Math.sin(u.yaw)*r};const b=u.flightBounds;u.flight={home:{x:clamp(p.x,b?b.minX+130:-430,b?b.maxX-130:430),z:clamp(p.z,b?b.minZ+130:-430,b?b.maxZ-130:430)},plan:null};}

function steer(u,point,dt){const before=u.yaw,desired=yawTo(u,point);u.yaw=turn(u.yaw,desired,FLIGHT.turnRate*dt);u.bank=clamp(-wrap(u.yaw-before)/Math.max(dt,.001)*.8,-.42,.42);u.x-=Math.sin(u.yaw)*FLIGHT.speed*dt;u.z-=Math.cos(u.yaw)*FLIGHT.speed*dt;u.moving=true;}
function orbit(u,dt){const home=u.flight.home,a=Math.atan2(u.z-home.z,u.x-home.x)-.7;steer(u,{x:home.x+Math.cos(a)*FLIGHT.radius,z:home.z+Math.sin(a)*FLIGHT.radius},dt);u.state='盘旋待命';}

export function planRun(u,job,target){
  const center=target?{x:target.x,z:target.z}:job.point;
  let dir,start,end;
  if(job.start&&job.end){const n=Math.max(1,dist(job.start,job.end));dir={x:(job.end.x-job.start.x)/n,z:(job.end.z-job.start.z)/n};start={...job.start};end={...job.end};}
  else{const n=Math.max(1,dist(u,center));dir={x:(center.x-u.x)/n,z:(center.z-u.z)/n};start={x:center.x-dir.x*150,z:center.z-dir.z*150};end={x:center.x+dir.x*90,z:center.z+dir.z*90};}
  const aim=job.start?{x:(start.x+end.x)/2,z:(start.z+end.z)/2}:{...center};
  const bomb=job.type==='bomb',fall=Math.sqrt(2*(u.y+1.4)/FLIGHT.gravity);
  if(bomb)start={x:aim.x-dir.x*(FLIGHT.speed*fall+90),z:aim.z-dir.z*(FLIGHT.speed*fall+90)};
  return{phase:'approach',dir,start,end,aim,entry:{x:start.x-dir.x*480,z:start.z-dir.z*480},escape:{x:end.x+dir.x*200,z:end.z+dir.z*200},fired:false,age:0};
}

export function updateFlight(sim,u,dt){
  u.moving=true;u.bank??=0;if(sim.map.heightAt)u.y=Math.max(u.y,sim.map.safeAltitude+(Number(u.id.split('-').at(-1))%3)*10);u.flight??={home:{x:u.x+110,z:u.z},plan:null};
  // 外侧飞行缓冲区只用于机动；越界后转回场内，不瞬移或钳住飞机。
  const limit=sim.map.size?sim.map.size/2+1300:1500;const b=sim.map.streaming?sim.map.bounds:null;if(b?(u.x<b.minX-1300||u.x>b.maxX+1300||u.z<b.minZ-1300||u.z>b.maxZ+1300):(Math.abs(u.x)>limit||Math.abs(u.z)>limit)){u.flight.plan=null;if(u.job?.type==='move')sim.setJobStatus(u,u.job,'executing','边界回转 · 保留航点');else sim.finish(u);u.flight.home={x:clamp(u.x,b?b.minX+200:-400,b?b.maxX-200:400),z:clamp(u.z,b?b.minZ+200:-400,b?b.maxZ-200:400)};steer(u,u.flight.home,dt);u.state='边界回转';return;}
  const preparation=u.job?sim.preparationStatus(u):null;if(preparation&&preparation.status!=='ready'){orbit(u,dt);if(preparation.status==='pending')sim.setJobStatus(u,u.job,'pending',preparation.reason);else sim.rejectJob(u,preparation.reason);u.state=preparation.reason+' · 盘旋';return;}
  if(!u.job){orbit(u,dt);return;}
  const job=u.job;
  if(job.type==='move'){
    sim.setJobStatus(u,job,'executing');steer(u,job.point,dt);u.state='飞向航点';if(dist(u,job.point)<80){u.flight.home={...job.point};sim.finish(u);}return;
  }
  if(!['attack','strafe','bomb','ground'].includes(job.type)){sim.finish(u);orbit(u,dt);return;}
  const target=job.target?sim.target(job.target):null;
  if(job.target&&!target&&!u.flight.plan){sim.finish(u);orbit(u,dt);return;}
  const p=u.flight.plan??=planRun(u,job,target);p.age+=dt;
  if(job.target&&!target&&p.phase!=='egress')p.phase='egress';
  if(p.phase==='approach'){
    steer(u,p.entry,dt);u.state='进场对准';if(dist(u,p.entry)<55)p.phase='run';
  }else if(p.phase==='run'){
    const progress=(u.x-p.start.x)*p.dir.x+(u.z-p.start.z)*p.dir.z;
    const length=(p.end.x-p.start.x)*p.dir.x+(p.end.z-p.start.z)*p.dir.z;
    const lateral=Math.abs((u.x-p.start.x)*p.dir.z-(u.z-p.start.z)*p.dir.x);
    steer(u,{x:p.start.x+p.dir.x*(progress+100),z:p.start.z+p.dir.z*(progress+100)},dt);
    u.state=job.type==='bomb'?'投弹航线':'扫射航线';
    const angle=Math.abs(wrap(Math.atan2(-p.dir.x,-p.dir.z)-u.yaw));
    if(lateral<30&&angle<.2&&progress>0&&progress<length){
      if(job.type==='bomb'){
        const origin=sim.muzzle(u,'bomb'),fall=Math.sqrt(2*Math.max(0,origin.y-sim.groundHeight(p.aim.x,p.aim.z))/FLIGHT.gravity),landing={x:origin.x-Math.sin(u.yaw)*FLIGHT.speed*fall,z:origin.z-Math.cos(u.yaw)*FLIGHT.speed*fall};
        if(!p.fired&&dist(landing,p.aim)<8&&sim.launch(u,{...p.aim,y:sim.groundHeight(p.aim.x,p.aim.z)},null,'bomb')){p.fired=true;u.state='炸弹已投放';}
      }else{
        const valid=t=>t&&t.hp>0&&dist(u,t)<240&&((t.x-u.x)*p.dir.x+(t.z-u.z)*p.dir.z)>60&&Math.abs((t.x-p.start.x)*p.dir.z-(t.z-p.start.z)*p.dir.x)<24;
        const aim=valid(target)?target:job.target?null:sim.targets.find(valid);
        const point=aim??{x:u.x+p.dir.x*90,y:.1,z:u.z+p.dir.z*90};
        if(!sim.nav.blocked(sim.muzzle(u),point)){sim.launch(u,point,aim,'planeGun');u.state='机炮扫射';p.fired=true;}
      }
    }
    if(progress>length+25)p.phase='egress';
  }else{
    steer(u,p.escape,dt);u.state='攻击脱离';if(dist(u,p.escape)<65){u.flight.plan=null;sim.finish(u);}
  }
  if(p.age>75){u.flight.plan=null;sim.finish(u);u.state='航线重置 · 盘旋';}
}
