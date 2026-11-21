import {CLASSIC,SpatialGrid,clamp,dist} from './地图.mjs';

/** 线段连续碰撞，弹道和导航共用。 */
export function segmentBox(a,b,box,pad=0){if(typeof box.segment==='function')return box.segment(a,b,pad);let lo=0,hi=1;const base=box.baseY??0;
  for(const [axis,min,max] of [['x',box.x-box.w/2-pad,box.x+box.w/2+pad],['y',base-pad,base+box.h+pad],['z',box.z-box.d/2-pad,box.z+box.d/2+pad]]){const delta=b[axis]-a[axis];if(Math.abs(delta)<1e-8){if(a[axis]<min||a[axis]>max)return null;continue;}let t1=(min-a[axis])/delta,t2=(max-a[axis])/delta;if(t1>t2)[t1,t2]=[t2,t1];lo=Math.max(lo,t1);hi=Math.min(hi,t2);if(lo>hi)return null;}
  // 作者旋转后的凸占地棱柱；包围盒只作快速预检。
  if(box.polygon){const p=box.polygon;let area=0;for(let i=0;i<p.length;i++){const q=p[(i+1)%p.length];area+=p[i][0]*q[1]-q[0]*p[i][1];}const sign=Math.sign(area);
    for(let i=0;i<p.length;i++){const [x,z]=p[i],[xx,zz]=p[(i+1)%p.length],dx=xx-x,dz=zz-z;
      const at=sign*(dx*(a.z-z)-dz*(a.x-x))+pad*Math.hypot(dx,dz),delta=sign*(dx*(b.z-a.z)-dz*(b.x-a.x));
      if(Math.abs(delta)<1e-10){if(at<0)return null;continue;}const t=-at/delta;if(delta>0)lo=Math.max(lo,t);else hi=Math.min(hi,t);if(lo>hi)return null;
    }
  }return lo;
}

const cached=new WeakMap();
export class Navigator{
  constructor(map){
    this.map=map;this.static=new SpatialGrid(48);for(const o of map.obstacles)this.static.insert(o,Math.max(o.w,o.d)/2+4);
    this.cell=map.cell;const b=map.bounds;this.x0=b.minX+4;this.z0=b.minZ+4;
    this.nx=Math.floor((b.maxX-b.minX-8)/this.cell)+1;this.nz=Math.floor((b.maxZ-b.minZ-8)/this.cell)+1;
    this.radius=map.heightAt?5.8:3;this.passable=new Uint8Array(this.nx*this.nz);
    for(let z=0;z<this.nz;z++)for(let x=0;x<this.nx;x++){const p=this.point(z*this.nx+x);this.passable[z*this.nx+x]=this.walkable(p.x,p.z,this.radius)?1:0;}
  }
  point(id){return{x:this.x0+id%this.nx*this.cell,z:this.z0+Math.floor(id/this.nx)*this.cell};}
  index(p){return clamp(Math.round((p.z-this.z0)/this.cell),0,this.nz-1)*this.nx+clamp(Math.round((p.x-this.x0)/this.cell),0,this.nx-1);}
  clamp(p,pad=5){const b=this.map.bounds;return{x:clamp(p.x,b.minX+pad,b.maxX-pad),z:clamp(p.z,b.minZ+pad,b.maxZ-pad)};}
  walkable(x,z,pad=3){const b=this.map.bounds;if(x<=b.minX+pad||x>=b.maxX-pad||z<=b.minZ+pad||z>=b.maxZ-pad)return false;
    if(this.map.heightAt){const h=this.map.heightAt(x,z);if(!Number.isFinite(h))return false;for(const [dx,dz] of [[pad,0],[-pad,0],[0,pad],[0,-pad]]){const a=this.map.heightAt(x+dx,z+dz);if(!Number.isFinite(a)||Math.abs(a-h)>Math.tan(Math.PI/6)*pad)return false;}}
    return !this.static.query(x,z,pad).some(o=>segmentBox({x,y:(o.baseY??0)+.1,z},{x,y:(o.baseY??0)+.1,z},o,pad)!==null);
  }
  blocked(a,b,pad=.12){for(const o of this.static.segment(a,b,pad))if(segmentBox(a,b,o,pad)!==null)return true;
    return this.terrainHit(a,b)!==null;
  }
  terrainHit(a,b){if(!this.map.heightAt)return null;const n=Math.max(1,Math.ceil(dist(a,b)));let prev=0;
    const gap=t=>a.y+(b.y-a.y)*t-this.map.heightAt(a.x+(b.x-a.x)*t,a.z+(b.z-a.z)*t);
    for(let i=0;i<=n;i++){const t=i/n,g=gap(t);if(Number.isFinite(g)&&g<=0){let lo=prev,hi=t;for(let j=0;j<14;j++){const m=(lo+hi)/2;if(gap(m)>0)lo=m;else hi=m;}return hi;}prev=t;}return null;
  }
  groundBlocked(a,b,pad=3){if(!this.map.heightAt)return this.blocked({...a,y:1},{...b,y:1},pad);
    const n=Math.max(1,Math.ceil(dist(a,b)/2));for(let i=0;i<=n;i++){const t=i/n;if(!this.walkable(a.x+(b.x-a.x)*t,a.z+(b.z-a.z)*t,pad))return true;}return false;
  }
  path(from,to,air=false){
    const end={...to},bounds=this.map.bounds;if(!Number.isFinite(end.x)||!Number.isFinite(end.z)||end.x<bounds.minX||end.x>bounds.maxX||end.z<bounds.minZ||end.z>bounds.maxZ)return[];if(air)return[end];
    if(!this.walkable(end.x,end.z,this.radius))return[];
    if(this.walkable(end.x,end.z,this.radius)&&!this.groundBlocked(from,end,this.radius))return[end];
    const start=this.index(from);let finish=this.index(end);
    if(!this.passable[finish])return[];
    const cost=new Map([[start,0]]),parent=new Map(),heap=[],closed=new Set();
    const heuristic=id=>Math.hypot(id%this.nx-finish%this.nx,Math.floor(id/this.nx)-Math.floor(finish/this.nx));
    const push=(id,f)=>{let i=heap.length;heap.push({id,f});while(i){const p=(i-1)>>1;if(heap[p].f<=f)break;heap[i]=heap[p];i=p;}heap[i]={id,f};};
    const pop=()=>{const first=heap[0],last=heap.pop();if(heap.length){let i=0;while(i*2+1<heap.length){let j=i*2+1;if(j+1<heap.length&&heap[j+1].f<heap[j].f)j++;if(heap[j].f>=last.f)break;heap[i]=heap[j];i=j;}heap[i]=last;}return first.id;};
    push(start,heuristic(start));let found=false;
    while(heap.length){const id=pop();if(closed.has(id))continue;if(id===finish){found=true;break;}closed.add(id);const x=id%this.nx,z=Math.floor(id/this.nx);
      for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,1],[1,-1],[-1,-1]]){const xx=x+dx,zz=z+dz,next=zz*this.nx+xx;if(xx<0||zz<0||xx>=this.nx||zz>=this.nz||!this.passable[next]||closed.has(next))continue;if(dx&&dz&&(!this.passable[z*this.nx+xx]||!this.passable[zz*this.nx+x]))continue;const g=cost.get(id)+Math.hypot(dx,dz);if(g<(cost.get(next)??Infinity)){cost.set(next,g);parent.set(next,id);push(next,g+heuristic(next));}}
    }
    if(!found)return[];const nodes=[end];for(let id=finish;id!==start;){nodes.push(this.point(id));id=parent.get(id);if(id===undefined)return[];}nodes.reverse();
    const result=[];let a=from;for(let i=0;i<nodes.length;i++){if(this.groundBlocked(a,nodes[i],this.radius))return[];let far=i;for(let j=i;j<nodes.length;j++){if(this.groundBlocked(a,nodes[j],this.radius))break;far=j;}result.push(nodes[far]);a=nodes[far];i=far;}return result;
  }
}
export const navigatorFor=map=>{let n=cached.get(map);if(!n){n=map.createNavigator?.()??new Navigator(map);cached.set(map,n);}return n;};
export const findPath=(from,to,air=false,map=CLASSIC)=>navigatorFor(map).path(from,to,air);
