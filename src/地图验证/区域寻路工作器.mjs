// 局部4米网格的寻路在工作器执行；不创建全图高精度网格，不读取DOM或Three.js。
const patches=new Map();
const heapPush=(heap,id,f)=>{let i=heap.length;heap.push({id,f});while(i){const p=(i-1)>>1;if(heap[p].f<=f)break;heap[i]=heap[p];i=p;}heap[i]={id,f};};
const heapPop=heap=>{const first=heap[0],last=heap.pop();if(heap.length){let i=0;while(i*2+1<heap.length){let j=i*2+1;if(j+1<heap.length&&heap[j+1].f<heap[j].f)j++;if(heap[j].f>=last.f)break;heap[i]=heap[j];i=j;}heap[i]=last;}return first.id;};
export function localPath(patch,from,to,blocked=[]){
 const {nx,nz,cell,x0,z0,passable}=patch,total=nx*nz;
 const point=id=>({x:x0+id%nx*cell,z:z0+Math.floor(id/nx)*cell});
 const invalidStart=new Set(blocked.filter(e=>e[0]===-1).map(e=>e[1]));
 const nearest=(p,exclude=new Set())=>{let best=-1,distance=Infinity;for(let i=0;i<total;i++)if(passable[i]&&!exclude.has(i)){const q=point(i),d=(q.x-p.x)**2+(q.z-p.z)**2;if(d<distance){best=i;distance=d;}}return best;};
 const start=nearest(from,invalidStart),finish=nearest(to);if(start<0||finish<0)return {state:'unreachable',path:[]};
 const forbidden=new Set(blocked.map(e=>e[0]+':'+e[1])),cost=new Float64Array(total).fill(Infinity),parent=new Int32Array(total).fill(-1),closed=new Uint8Array(total),heap=[];
 const heuristic=id=>Math.hypot(id%nx-finish%nx,Math.floor(id/nx)-Math.floor(finish/nx));cost[start]=0;heapPush(heap,start,heuristic(start));let found=false;
 while(heap.length){
  const id=heapPop(heap);if(closed[id])continue;if(id===finish){found=true;break;}closed[id]=1;const x=id%nx,z=Math.floor(id/nx);
  for(const [dx,dz] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[-1,1],[1,-1],[-1,-1]]){
   const xx=x+dx,zz=z+dz,next=zz*nx+xx;if(xx<0||zz<0||xx>=nx||zz>=nz||!passable[next]||closed[next]||forbidden.has(id+':'+next))continue;
   if(dx&&dz&&(!passable[z*nx+xx]||!passable[zz*nx+x]))continue;
   const g=cost[id]+Math.hypot(dx,dz);if(g<cost[next]){cost[next]=g;parent[next]=id;heapPush(heap,next,g+heuristic(next));}
  }
 }
 if(!found)return {state:'unreachable',path:[]};
 const ids=[];for(let id=finish;id>=0;id=parent[id]){ids.push(id);if(id===start)break;}ids.reverse();
 return {state:'ready',path:ids.map(point),ids,effectiveEnd:point(finish)};
}
if(typeof self!=='undefined'&&typeof self.postMessage==='function'&&typeof document==='undefined'){
 self.onmessage=event=>{
  const m=event.data;
  if(m.type==='patch'){patches.set(m.key,m.patch);self.postMessage({type:'patch',id:m.id});}
  else if(m.type==='release')patches.delete(m.key);
  else if(m.type==='reset')patches.clear();
  else if(m.type==='path'){try{const patch=patches.get(m.key);if(!patch)throw new Error('区域网格未准备');self.postMessage({type:'path',id:m.id,...localPath(patch,m.from,m.to,m.blocked)});}catch(error){self.postMessage({type:'path',id:m.id,state:'error',error:String(error),path:[]});}}
 };
 self.postMessage({type:'ready'});
}
