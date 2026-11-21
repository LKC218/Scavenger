// 群体规则仅使用数值；不依赖Three.js、DOM或资源文件。
export const WORLD = {minX:-29,maxX:29,minZ:-22,maxZ:22,cell:1};
export const OBSTACLES = [
  {x:0,z:-12.5,w:2.6,d:21}, {x:0,z:12.5,w:2.6,d:21},
  {x:12,z:-15,w:5,d:3}, {x:-12,z:15,w:5,d:3}
];
const W=58,H=44,SIZE=W*H;
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));

export class Crowd {
  constructor(count=500,mode='open',seed=261103){
    this.seed=seed;this.reset(count,mode);
  }
  reset(count=this.count,mode=this.mode){
    if(!Number.isInteger(count)||count<100||count>5000)throw new Error('角色数量范围为100至5000');
    this.count=count;this.mode=mode;this.elapsed=0;this.patrol=true;this.leg=0;
    this.target={x:17,z:0};this.navMs=0;this.neighborChecks=0;this.ticks=0;
    this.blocked=new Uint8Array(SIZE);this.dist=new Int32Array(SIZE);this.queue=new Int32Array(SIZE);
    this.head=new Int32Array(SIZE);this.next=new Int32Array(count);
    let seed=this.seed;const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296;};
    // 超过500只时收紧左侧生成间距，保证所有角色仍在场地内；拥挤是容量负载的一部分。
    const cols=Math.ceil(Math.sqrt(count*21/30)),rows=Math.ceil(count/cols);
    const sx=Math.min(.78,21/Math.max(1,cols-1)),sz=Math.min(.78,30/Math.max(1,rows-1));
    this.agents=Array.from({length:count},(_,i)=>{
      const x=count<=500?-25+(i%23)*.78:-15.5+(i%cols-(cols-1)/2)*sx;
      const z=count<=500?(Math.floor(i/23)-Math.ceil(count/23)/2)*.78:-3.5+(Math.floor(i/cols)-(rows-1)/2)*sz;
      return {id:i,x,z,px:x,pz:z,vx:0,vz:0,heading:Math.PI/2,speed:1.2+random()*.3,
        phase:random(),scale:.95+random()*.1,tx:0,tz:0,arrived:false};
    });
    for(let iz=0;iz<H;iz++)for(let ix=0;ix<W;ix++){
      const x=WORLD.minX+ix+.5,z=WORLD.minZ+iz+.5;
      if(mode==='channel'&&OBSTACLES.some(o=>Math.abs(x-o.x)<o.w/2+.42&&Math.abs(z-o.z)<o.d/2+.42))this.blocked[iz*W+ix]=1;
    }
    this.setTarget(17,0);
  }
  cell(x,z){return clamp(Math.floor(z-WORLD.minZ),0,H-1)*W+clamp(Math.floor(x-WORLD.minX),0,W-1);}
  free(x,z){
    if(x<WORLD.minX+.4||x>WORLD.maxX-.4||z<WORLD.minZ+.4||z>WORLD.maxZ-.4)return false;
    return this.mode!=='channel'||!OBSTACLES.some(o=>Math.abs(x-o.x)<o.w/2+.34&&Math.abs(z-o.z)<o.d/2+.34);
  }
  lineFree(x,z,tx,tz){
    if(this.mode!=='channel')return true;
    const steps=Math.ceil(Math.hypot(tx-x,tz-z)/.65);
    for(let n=1;n<=steps;n++)if(!this.free(x+(tx-x)*n/steps,z+(tz-z)*n/steps))return false;
    return true;
  }
  setTarget(x,z){
    x=clamp(x,-25,25);z=clamp(z,-18,18);
    if(!this.free(x,z))return false;
    const start=performance.now();this.target={x,z};
    this.dist.fill(1e8);let first=0,last=1;const goal=this.cell(x,z);
    this.dist[goal]=0;this.queue[0]=goal;
    while(first<last){
      const c=this.queue[first++],cx=c%W,cz=Math.floor(c/W);
      for(const k of [-1,1,-W,W]){
        const n=c+k;if(n<0||n>=SIZE||this.blocked[n]||this.dist[n]!==1e8)continue;
        if(Math.abs((n%W)-cx)+Math.abs(Math.floor(n/W)-cz)!==1)continue;
        this.dist[n]=this.dist[c]+1;this.queue[last++]=n;
      }
    }
    const cols=Math.ceil(Math.sqrt(this.count*1.1)),rows=Math.ceil(this.count/cols);
    const sx=this.count<=500?.76:Math.min(.76,19/(cols-1)),sz=this.count<=500?.76:Math.min(.76,30/(rows-1));
    for(const a of this.agents){
      a.tx=clamp(x+(a.id%cols-(cols-1)/2)*sx,-27,27);
      a.tz=clamp(z+(Math.floor(a.id/cols)-(rows-1)/2)*sz,-20,20);
      if(!this.free(a.tx,a.tz)){a.tx=x;a.tz=z;}
      a.arrived=false;
    }
    this.navMs=performance.now()-start;return true;
  }
  direction(a){
    if(this.lineFree(a.x,a.z,a.tx,a.tz))return [a.tx-a.x,a.tz-a.z];
    const c=this.cell(a.x,a.z),cx=c%W,cz=Math.floor(c/W);let best=c,score=this.dist[c]+.8;
    for(let dz=-1;dz<=1;dz++)for(let dx=-1;dx<=1;dx++){
      const nx=cx+dx,nz=cz+dz;if(nx<0||nx>=W||nz<0||nz>=H)continue;
      const n=nz*W+nx;if(this.blocked[n])continue;
      if(dx&&dz&&(this.blocked[cz*W+nx]||this.blocked[nz*W+cx]))continue;
      const s=this.dist[n]+Math.hypot(WORLD.minX+nx+.5-a.x,WORLD.minZ+nz+.5-a.z)*.25;
      if(s<score){score=s;best=n;}
    }
    return [WORLD.minX+best%W+.5-a.x,WORLD.minZ+Math.floor(best/W)+.5-a.z];
  }
  tick(dt){
    this.elapsed+=dt;this.ticks++;
    if(this.patrol&&Math.floor(this.elapsed/24)!==this.leg){this.leg=Math.floor(this.elapsed/24);this.setTarget(this.leg%2?-17:17,0);}
    for(const a of this.agents){a.px=a.x;a.pz=a.z;}
    if(!['open','channel'].includes(this.mode))return;
    this.head.fill(-1);this.neighborChecks=0;
    for(const a of this.agents){const c=this.cell(a.x,a.z);this.next[a.id]=this.head[c];this.head[c]=a.id;}
    for(const a of this.agents){
      let [dx,dz]=this.direction(a);const len=Math.hypot(dx,dz),remain=Math.hypot(a.tx-a.x,a.tz-a.z);
      a.arrived=remain<.35;const speed=a.arrived?0:a.speed*Math.min(1,remain/1.25);
      if(len>.001){dx=dx/len*speed;dz=dz/len*speed;}else{dx=0;dz=0;}
      const c=this.cell(a.x,a.z),cx=c%W,cz=Math.floor(c/W);let sx=0,sz=0,neighbors=0;
      for(let oz=-1;oz<=1;oz++)for(let ox=-1;ox<=1;ox++){
        if(cx+ox<0||cx+ox>=W||cz+oz<0||cz+oz>=H)continue;
        for(let j=this.head[(cz+oz)*W+cx+ox];j!==-1;j=this.next[j]){
          if(j===a.id||neighbors>=16)continue;
          const b=this.agents[j],vx=a.x-b.x,vz=a.z-b.z,d2=vx*vx+vz*vz;this.neighborChecks++;
          if(d2>.00001&&d2<.85*.85){const d=Math.sqrt(d2),force=(.85-d)/.85;sx+=vx/d*force;sz+=vz/d*force;neighbors++;}
        }
      }
      dx+=sx*1.8;dz+=sz*1.8;const desired=Math.hypot(dx,dz);
      if(desired>a.speed){dx=dx/desired*a.speed;dz=dz/desired*a.speed;}
      const smooth=1-Math.exp(-dt*9);a.vx+=(dx-a.vx)*smooth;a.vz+=(dz-a.vz)*smooth;
      const nx=a.x+a.vx*dt,nz=a.z+a.vz*dt;
      if(this.free(nx,nz)){a.x=nx;a.z=nz;}
      else if(this.free(nx,a.z)){a.x=nx;a.vz=0;}
      else if(this.free(a.x,nz)){a.z=nz;a.vx=0;}
      else{a.vx=0;a.vz=0;}
      if(Math.hypot(a.vx,a.vz)>.08)a.heading=Math.atan2(a.vx,a.vz);
    }
  }
  health(){
    return {count:this.count,moving:this.agents.filter(a=>Math.hypot(a.vx,a.vz)>.1).length,
      arrived:this.agents.filter(a=>a.arrived).length,neighbors:this.neighborChecks,
      finite:this.agents.every(a=>Number.isFinite(a.x+a.z+a.vx+a.vz)),
      insideObstacles:this.agents.filter(a=>!this.free(a.x,a.z)).length,
      rightSide:this.agents.filter(a=>a.x>2).length,elapsed:this.elapsed,ticks:this.ticks};
  }
}
