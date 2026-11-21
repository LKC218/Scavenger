/** 同一尺度的地图、静态碰撞体与靶标配置。 */
export const CLASSIC_OBSTACLES=[{x:-6,z:-7,w:28,d:7,h:7},{x:34,z:10,w:13,d:17,h:5},{x:-43,z:-52,w:16,d:7,h:5},{x:15,z:-57,w:12,d:8,h:6}];
export const CLASSIC={id:'classic',name:'小靶场对照',bounds:{minX:-72,maxX:72,minZ:-91,maxZ:83},obstacles:CLASSIC_OBSTACLES,cell:4,center:{x:0,z:-4},maxSpan:180,targets:[[-44,-27],[-4,-29],[37,-32],[-38,-73],[-4,-76],[38,-76]]};
const buildings=[];let seed=714;
const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
for(const cx of [-135,-45,45,135])for(const cz of [-265,-175,-85])for(const dx of [-22,0,22])for(const dz of [-22,0,22]){
  const x=cx+dx,z=cz+dz,w=13+random()*5,d=13+random()*5;
  // 中央留旧靶场的对照火线，街道不铺满碰撞体。
  if(Math.abs(x)<68&&z>-100)continue;
  buildings.push({x,z,w,d,h:8+random()*14,building:true,tint:random()});
}
const targets=[...CLASSIC.targets];
for(const x of [-180,-90,0,90,180])for(const z of [-320,-220,-130])targets.push([x,z]);
for(const x of [-240,0,240])targets.push([x,230]);
export const CITY={id:'city',name:'城市联合演训',bounds:{minX:-600,maxX:600,minZ:-600,maxZ:600},obstacles:[...CLASSIC_OBSTACLES,...buildings],cell:8,center:{x:0,z:-4},maxSpan:1450,targets};
export const MAPS={classic:CLASSIC,city:CITY};
export const clamp=(x,a,b)=>Math.min(b,Math.max(a,x));
export const dist=(a,b)=>Math.hypot(a.x-b.x,a.z-b.z);
export const wrap=a=>Math.atan2(Math.sin(a),Math.cos(a));
export const yawTo=(a,b)=>Math.atan2(-(b.x-a.x),-(b.z-a.z));
export const turn=(a,b,s)=>a+clamp(wrap(b-a),-s,s);

/** 稀疏格网：只查询附近单位或线段包围范围内的静态碰撞体。 */
export class SpatialGrid{
  constructor(size){this.size=size;this.cells=new Map();}
  clear(){this.cells.clear();}
  insert(item,pad=0){const s=this.size;for(let x=Math.floor((item.x-pad)/s);x<=Math.floor((item.x+pad)/s);x++)for(let z=Math.floor((item.z-pad)/s);z<=Math.floor((item.z+pad)/s);z++){const key=x+','+z;let a=this.cells.get(key);if(!a)this.cells.set(key,a=[]);a.push(item);}}
  query(x,z,r){const result=[];const s=this.size;for(let a=Math.floor((x-r)/s);a<=Math.floor((x+r)/s);a++)for(let b=Math.floor((z-r)/s);b<=Math.floor((z+r)/s);b++){const cell=this.cells.get(a+','+b);if(cell)result.push(...cell);}return result;}
  segment(a,b,pad=0){const found=new Set(),s=this.size;for(let x=Math.floor((Math.min(a.x,b.x)-pad)/s);x<=Math.floor((Math.max(a.x,b.x)+pad)/s);x++)for(let z=Math.floor((Math.min(a.z,b.z)-pad)/s);z<=Math.floor((Math.max(a.z,b.z)+pad)/s);z++)for(const o of this.cells.get(x+','+z)??[])found.add(o);return found;}
}
