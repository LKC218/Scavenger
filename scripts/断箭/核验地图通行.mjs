/** 单进程重放真实运行高度与作者碰撞轮廓；不代替浏览器键鼠审验。 */
import fs from 'node:fs';
import path from 'node:path';
import {Simulation} from '../../src/载具靶场/模拟.mjs';
import {heightQuery,collisionFrom} from '../../src/地图验证/地图契约.mjs';
const directory=path.resolve(process.argv[2]),map=JSON.parse(fs.readFileSync(path.join(directory,'地图运行.json'),'utf8'));
const bytes=fs.readFileSync(path.join(directory,map.height.file));
const grid=new Float32Array(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength));
map.heightAt=heightQuery(map.height,grid);map.obstacles=map.instances.map(collisionFrom);map.cell=4;map.center={x:0,z:85};map.targets=[];map.safeAltitude=130;
const sim=new Simulation({map,city:true,total:1,composition:{tank:1,heli:0,plane:0}}),k=map.size/500;
// 独立四邻基础网格连通域；网格隔离还须结合实际轮廓及必要细化解释。
const nav=sim.nav,component=new Int32Array(nav.passable.length);component.fill(-1);const queue=new Int32Array(component.length),areas=[];
for(let seed=0;seed<component.length;seed++)if(nav.passable[seed]&&component[seed]<0){let head=0,tail=1;queue[0]=seed;const id=areas.length;component[seed]=id;
  while(head<tail){const at=queue[head++],x=at%nav.nx,z=Math.floor(at/nav.nx);for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){const xx=x+dx,zz=z+dz,next=zz*nav.nx+xx;if(xx<0||zz<0||xx>=nav.nx||zz>=nav.nz||!nav.passable[next]||component[next]>=0)continue;component[next]=id;queue[tail++]=next;}}
  areas.push(tail);
}
const cases=[['连续道面',[-220,25],[220,25]],['道面转弯',[210,180],[210,-180]],['斜向建筑绕行',[-220,180],[180,-200]],['长距离绕行',[-230,-220],[220,210]],['机库侧路',[-180,100],[-180,-180]],['塔台周边',[-130,160],[20,150]],['平缓地面',[-200,220],[180,220]],['反向路线',[160,210],[-160,210]],['边界截断',[0,210],[800,210]],['不可达建筑中心',[0,220],null]];
const result={schema:1,map:map.id,radius_m:5.3,planning_radius_m:nav.radius,grid_components:areas,routes:[]};
if(map.size===2000){
  // 原长路线落在边界外侧18米高台。保留该输入，按现有坡度/车体余量规则细化到一米核实，另加真正跨区可达路线。
  const x0=-994,z0=-994,nx=350,nz=500,seen=new Uint8Array(nx*nz),q=new Int32Array(nx*nz);
  let head=0,tail=1;q[0]=114*nx+74;seen[q[0]]=1;const bounds=[Infinity,Infinity,-Infinity,-Infinity];let exit=false;
  while(head<tail){const at=q[head++],x=at%nx,z=Math.floor(at/nx);bounds[0]=Math.min(bounds[0],x+x0);bounds[1]=Math.min(bounds[1],z+z0);bounds[2]=Math.max(bounds[2],x+x0);bounds[3]=Math.max(bounds[3],z+z0);if(x===nx-1||z===nz-1)exit=true;
    for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1]]){const xx=x+dx,zz=z+dz,next=zz*nx+xx;if(xx<0||zz<0||xx>=nx||zz>=nz||seen[next]||!nav.walkable(xx+x0,zz+z0,nav.radius))continue;seen[next]=1;q[tail++]=next;}}
  result.island_refinement={cell_m:1,start:{x:-920,z:-880},reachable_cells:tail,bounds,escaped_to_main_area:exit,
    note:'源地形角落高台；在30度坡度和5.8米规划余量下不通主区域。此结论针对Demo通行规则。',
    height_profile:Array.from({length:17},(_,i)=>{const x=-980+i*10;return{x,z:-880,height:map.heightAt(x,-880),walkable:nav.walkable(x,-880,nav.radius)};})};
  cases.push(['长距离跨区可达',[-230,-190],[220,210]]);
}
for(let index=0;index<cases.length;index++){
  const [name,a,b]=cases[index],u=sim.units[0],start=sim.freePoint({x:a[0]*k,z:a[1]*k});
  const obstacle=map.obstacles.find(o=>o.x>map.bounds.minX+20&&o.x<map.bounds.maxX-20)??map.obstacles[0];
  const goal=b?{x:b[0]*k,z:b[1]*k}:{x:obstacle.x,z:obstacle.z};
  sim.cancel([u.id]);u.x=start.x;u.z=start.z;u.yaw=0;sim.settle(u);
  sim.command([u.id],{type:index===7?'reverse':'move',point:goal});
  const goalIndex=nav.index(nav.clamp(goal));
  const route={name,start,requested:goal,start_component:component[nav.index(start)],goal_component:component[goalIndex],violations:0,height_max_error:0,steps:0,travel_m:0};
  const limit=map.size/3+180;
  for(let t=0;t<limit&&u.job;t+=1/30){const before={x:u.x,z:u.z};sim.step(1/30);route.steps++;route.travel_m+=Math.hypot(u.x-before.x,u.z-before.z);if(!sim.nav.walkable(u.x,u.z,5))route.violations++;route.height_max_error=Math.max(route.height_max_error,Math.abs(u.y-map.heightAt(u.x,u.z)));}
  route.end={x:u.x,z:u.z};route.finished=!u.job;route.state=u.state;
  route.expected=map.size===2000&&name==='长距离绕行'?'blocked_by_slope':'arrive_or_clamp';
  const correct=route.expected==='blocked_by_slope'
    ?route.travel_m===0&&route.state==='无可达路径'&&route.start_component!==route.goal_component&&!result.island_refinement.escaped_to_main_area
    :route.finished;
  route.status=correct&&route.violations===0&&route.height_max_error<.1?'passed':'failed';result.routes.push(route);
}
// 平地老地图回归和未知支撑边界。
const classic=new Simulation({city:false}),city=new Simulation({city:true,total:100});
result.flat_regression={classic:classic.units.length===10&&classic.units.filter(u=>u.kind==='tank').every(u=>u.y===0),city:city.units.length===100&&city.units.filter(u=>u.kind==='tank').every(u=>u.y===0)};
result.unknown_blocked=!sim.nav.walkable(map.bounds.maxX+1,0);
result.status=result.routes.every(r=>r.status==='passed')&&Object.values(result.flat_regression).every(Boolean)&&result.unknown_blocked?'passed':'failed';
const receipt=path.join(directory,'固定路线核验.json');
if(fs.existsSync(receipt)){let version=1,dst;do{dst=path.join(directory,'固定路线核验-前次-'+String(version++).padStart(3,'0')+'.json');}while(fs.existsSync(dst));fs.copyFileSync(receipt,dst);}
fs.writeFileSync(receipt,JSON.stringify(result,null,2));
console.log(JSON.stringify({status:result.status,routes:result.routes.map(r=>({name:r.name,finished:r.finished,violations:r.violations,travel_m:Math.round(r.travel_m)}))}));
if(result.status!=='passed')process.exitCode=1;
