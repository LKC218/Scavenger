// 原三角形按区域驻留，保留多层命中；未命中与尚未加载由上层分别报告。
export class TriangleSupportTile {
  constructor(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 32) throw new Error('支撑页头部不完整');
    const data=this.data=new DataView(buffer);
    if(data.getUint32(0,true)!==0x45474142||data.getUint32(4,true)!==1)throw new Error('支撑页身份或版本错误');
    const count=this.count=data.getUint32(8,true),bins=this.bins=data.getUint32(12,true),references=data.getUint32(16,true);
    this.x=data.getFloat32(20,true);this.z=data.getFloat32(24,true);this.size=data.getFloat32(28,true);
    if(count>1000000||bins!==8||references>count*64||this.size!==125||![this.x,this.z].every(Number.isFinite))throw new Error('支撑页计数或边界无效');
    const start=32+44*count;
    if(buffer.byteLength!==start+4*(bins*bins+1+references))throw new Error('支撑页长度不符');
    this.offsets=new Uint32Array(buffer,start,bins*bins+1);
    this.references=new Uint32Array(buffer,start+4*(bins*bins+1),references);
    if(this.offsets[0]!==0||this.offsets.at(-1)!==references)throw new Error('支撑页空间索引不完整');
    for(let i=1;i<this.offsets.length;i++)if(this.offsets[i]<this.offsets[i-1])throw new Error('支撑页空间索引逆序');
    if(this.references.some(i=>i>=count))throw new Error('支撑页三角形引用越界');
    this.rolesMask=0;
    for(let i=0;i<count;i++){
      const start=32+i*44,role=data.getUint32(start+40,true);
      if(role<1||role>7)throw new Error('支撑页表面角色无效');
      this.rolesMask|=1<<role;
      for(let j=0;j<9;j++)if(!Number.isFinite(data.getFloat32(start+j*4,true)))throw new Error('支撑页几何含非有限值');
    }
    this.bytes=buffer.byteLength;
  }
  intersections(x,z,{roles=[1,2,3,4],tolerance=1e-7}={}) {
    if(![x,z,tolerance].every(Number.isFinite)||tolerance<0)throw new Error('支撑查询坐标或容差无效');
    const px=x-this.x,pz=z-this.z,half=this.size/2;
    if(px < -half-1e-6||px > half+1e-6||pz < -half-1e-6||pz > half+1e-6)return [];
    const ix=Math.max(0,Math.min(7,Math.floor((px+half)/this.size*8))),iz=Math.max(0,Math.min(7,Math.floor((pz+half)/this.size*8))),cell=iz*8+ix;
    const hits=[],data=this.data;
    for(let i=this.offsets[cell];i<this.offsets[cell+1];i++){
      const triangle=this.references[i],start=32+triangle*44,role=data.getUint32(start+40,true);
      if(!roles.includes(role))continue;
      const ax=data.getFloat32(start,true),ay=data.getFloat32(start+4,true),az=data.getFloat32(start+8,true);
      const bx=data.getFloat32(start+12,true),by=data.getFloat32(start+16,true),bz=data.getFloat32(start+20,true);
      const cx=data.getFloat32(start+24,true),cy=data.getFloat32(start+28,true),cz=data.getFloat32(start+32,true);
      const area=(bx-ax)*(cz-az)-(bz-az)*(cx-ax);if(Math.abs(area)<1e-10)continue;
      const b=((px-ax)*(cz-az)-(pz-az)*(cx-ax))/area,c=((bx-ax)*(pz-az)-(bz-az)*(px-ax))/area,a=1-b-c;
      if(Math.min(a,b,c)<-tolerance)continue;
      const height=a*ay+b*by+c*cy;
      const nx=(by-ay)*(cz-az)-(bz-az)*(cy-ay),ny=(bz-az)*(cx-ax)-(bx-ax)*(cz-az),nz=(bx-ax)*(cy-ay)-(by-ay)*(cx-ax);
      const normalY=Math.abs(ny)/Math.hypot(nx,ny,nz);
      hits.push({height,role,normalY,source:data.getUint32(start+36,true),triangle});
    }
    hits.sort((a,b)=>b.height-a.height||a.role-b.role);
    return hits;
  }
  query(x,z,{referenceY,roles=[1,2,3,4],layer}={}) {
    let hits=this.intersections(x,z,{roles});
    if(layer!==undefined)hits=hits.filter(hit=>(hit.source>>>20)===layer);
    const primary=hits.filter(hit=>hit.role!==4);if(primary.length)hits=primary;
    if(!hits.length)return {state:'unsupported',height:NaN};
    const chosen=Number.isFinite(referenceY)?hits.reduce((best,hit)=>Math.abs(hit.height-referenceY)<Math.abs(best.height-referenceY)?hit:best):hits[0];
    const water=this.intersections(x,z,{roles:[5]})[0];
    return {state:'ready',...chosen,layers:hits,waterHeight:water?.height??NaN,waterDepth:water?Math.max(0,water.height-chosen.height):0};
  }
  heightAt(x,z,hint) {return this.query(x,z,hint).height;}
  segment(a,b){
    const ax=a.x-this.x,ay=a.y,az=a.z-this.z,dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z,half=this.size/2;
    const lowX=Math.max(0,Math.min(7,Math.floor((Math.min(ax,ax+dx)+half)/this.size*8))),highX=Math.max(0,Math.min(7,Math.floor((Math.max(ax,ax+dx)+half)/this.size*8)));
    const lowZ=Math.max(0,Math.min(7,Math.floor((Math.min(az,az+dz)+half)/this.size*8))),highZ=Math.max(0,Math.min(7,Math.floor((Math.max(az,az+dz)+half)/this.size*8))),seen=new Set();let nearest=null;
    for(let z=lowZ;z<=highZ;z++)for(let x=lowX;x<=highX;x++)for(let k=this.offsets[z*8+x];k<this.offsets[z*8+x+1];k++){
      const triangle=this.references[k];if(seen.has(triangle))continue;seen.add(triangle);const at=32+triangle*44,d=this.data,role=d.getUint32(at+40,true);if(![1,2,3,4,5].includes(role))continue;
      const vx=d.getFloat32(at,true),vy=d.getFloat32(at+4,true),vz=d.getFloat32(at+8,true),e1x=d.getFloat32(at+12,true)-vx,e1y=d.getFloat32(at+16,true)-vy,e1z=d.getFloat32(at+20,true)-vz,e2x=d.getFloat32(at+24,true)-vx,e2y=d.getFloat32(at+28,true)-vy,e2z=d.getFloat32(at+32,true)-vz;
      const px=dy*e2z-dz*e2y,py=dz*e2x-dx*e2z,pz=dx*e2y-dy*e2x,det=e1x*px+e1y*py+e1z*pz;if(Math.abs(det)<1e-12)continue;
      const tx=ax-vx,ty=ay-vy,tz=az-vz,u=(tx*px+ty*py+tz*pz)/det;if(u<-1e-7||u>1+1e-7)continue;
      const qx=ty*e1z-tz*e1y,qy=tz*e1x-tx*e1z,qz=tx*e1y-ty*e1x,v=(dx*qx+dy*qy+dz*qz)/det;if(v<-1e-7||u+v>1+1e-7)continue;
      const t=(e2x*qx+e2y*qy+e2z*qz)/det;if(t<0||t>1||nearest!==null&&t>=nearest)continue;
      if(role===4&&this.query(a.x+dx*t,a.z+dz*t).role!==4)continue;nearest=t;
    }return nearest;
  }
}
