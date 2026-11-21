// 只对源Collider/导航形状和明确派生树干求交；可视包围盒不自动成为碰撞体。
export function multiply(a,b){
  const c=new Float64Array(16);for(let col=0;col<4;col++)for(let row=0;row<4;row++)for(let k=0;k<4;k++)c[col*4+row]+=a[k*4+row]*b[col*4+k];return c;
}
export function inverse(a){
  const x=a[0],y=a[4],z=a[8],u=a[1],v=a[5],w=a[9],p=a[2],q=a[6],r=a[10];
  const det=x*(v*r-w*q)-y*(u*r-w*p)+z*(u*q-v*p);if(!Number.isFinite(det)||Math.abs(det)<1e-12)throw new Error('碰撞形状变换不可逆');
  const m=new Float64Array([v*r-w*q,w*p-u*r,u*q-v*p,0,z*q-y*r,x*r-z*p,y*p-x*q,0,y*w-z*v,z*u-x*w,x*v-y*u,0,0,0,0,det]);
  for(let i=0;i<16;i++)m[i]/=det;
  for(let row=0;row<3;row++)m[12+row]=-(m[row]*a[12]+m[4+row]*a[13]+m[8+row]*a[14]);return m;
}
const point=(m,p)=>({x:m[0]*p.x+m[4]*p.y+m[8]*p.z+m[12],y:m[1]*p.x+m[5]*p.y+m[9]*p.z+m[13],z:m[2]*p.x+m[6]*p.y+m[10]*p.z+m[14]});
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
export class CollisionShape {
  constructor(template,record,index){
    this.id=`${record.group}:${record.sourceIndex}:${index}`;this.template=template;this.prototype=record.prototype;
    this.matrix=multiply(record.matrix,template.matrix);this.inverse=inverse(this.matrix);
    this.movement=template.movement;this.ballistics=template.ballistics;this.kind=template.kind;this.params=template.params;
    const p=this.params;
    const half=this.kind==='box'?p:[p[0],p[1]+(this.kind==='capsule'?p[0]:0),p[0]];
    this.bounds=[Infinity,Infinity,Infinity,-Infinity,-Infinity,-Infinity];
    for(const x of [-half[0],half[0]])for(const y of [-half[1],half[1]])for(const z of [-half[2],half[2]]){
      const q=point(this.matrix,{x,y,z});for(const [i,k] of ['x','y','z'].entries()){this.bounds[i]=Math.min(this.bounds[i],q[k]);this.bounds[i+3]=Math.max(this.bounds[i+3],q[k]);}
    }
    this.x=(this.bounds[0]+this.bounds[3])/2;this.z=(this.bounds[2]+this.bounds[5])/2;this.w=this.bounds[3]-this.bounds[0];this.d=this.bounds[5]-this.bounds[2];this.baseY=this.bounds[1];this.h=this.bounds[4]-this.bounds[1];
  }
  segment(from,to,pad=0){
    const a=point(this.inverse,from),b=point(this.inverse,to),d={x:b.x-a.x,y:b.y-a.y,z:b.z-a.z};
    // 世界球形余量转换为局部轴的保守范围，用于车辆安全余量。
    const padding=[0,1,2].map(i=>pad*Math.hypot(this.inverse[i],this.inverse[i+4],this.inverse[i+8]));
    if(this.kind==='box'){
      let lo=0,hi=1;for(const [i,k] of ['x','y','z'].entries()){
        const half=this.params[i]+padding[i];if(Math.abs(d[k])<1e-12){if(Math.abs(a[k])>half)return null;continue;}
        let x=(-half-a[k])/d[k],y=(half-a[k])/d[k];if(x>y)[x,y]=[y,x];lo=Math.max(lo,x);hi=Math.min(hi,y);if(lo>hi)return null;
      }return lo;
    }
    const radius=this.params[0]+Math.max(padding[0],padding[2]),height=this.params[1]+padding[1],candidates=[];
    const inside=q=>q.x*q.x+q.z*q.z+(this.kind==='capsule'?Math.max(0,Math.abs(q.y)-height)**2:0)<=radius*radius&& (this.kind==='capsule'||Math.abs(q.y)<=height);
    if(inside(a))return 0;
    const aa=d.x*d.x+d.z*d.z,bb=2*(a.x*d.x+a.z*d.z),cc=a.x*a.x+a.z*a.z-radius*radius,disc=bb*bb-4*aa*cc;
    if(aa>1e-16&&disc>=0)for(const t of [(-bb-Math.sqrt(disc))/(2*aa),(-bb+Math.sqrt(disc))/(2*aa)])if(t>=0&&t<=1&&Math.abs(a.y+d.y*t)<=height)candidates.push(t);
    if(this.kind==='capsule'){
      for(const center of [-height,height]){
        const y=a.y-center,aa=d.x*d.x+d.y*d.y+d.z*d.z,bb=2*(a.x*d.x+y*d.y+a.z*d.z),cc=a.x*a.x+y*y+a.z*a.z-radius*radius,disc=bb*bb-4*aa*cc;
        if(aa>1e-16&&disc>=0){const t=(-bb-Math.sqrt(disc))/(2*aa);if(t>=0&&t<=1)candidates.push(t);}
      }
    }else if(Math.abs(d.y)>1e-12)for(const plane of [-height,height]){const t=(plane-a.y)/d.y;if(t>=0&&t<=1&&(a.x+d.x*t)**2+(a.z+d.z*t)**2<=radius*radius)candidates.push(t);}
    return candidates.length?Math.min(...candidates):null;
  }
  overlaps(x,z,ground,pad){
    if(!this.movement||ground+3<this.bounds[1]||ground>this.bounds[4]+.2)return false;
    if(x+pad<this.bounds[0]||x-pad>this.bounds[3]||z+pad<this.bounds[2]||z-pad>this.bounds[5])return false;
    // 水平车体圆及3米高度；桥下不把高处结构投影成贯穿地面的墙。
    return this.segment({x,y:ground+1.3,z},{x,y:ground+1.3,z},pad)!==null;
  }
}
