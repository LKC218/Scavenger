/** 米制高度和碰撞契约；无浏览器依赖，空洞保持未知。 */
export function heightQuery(data,grid){
  const {width,height,spacing,origin}=data;
  return (x,z)=>{const fx=(x-origin[0])/spacing,fz=(z-origin[1])/spacing;
    if(fx<0||fz<0||fx>width-1||fz>height-1)return NaN;
    const ix=Math.min(width-2,Math.floor(fx)),iz=Math.min(height-2,Math.floor(fz)),a=fx-ix,b=fz-iz;
    const v=[grid[iz*width+ix],grid[iz*width+ix+1],grid[(iz+1)*width+ix],grid[(iz+1)*width+ix+1]];
    if(!v.every(Number.isFinite))return NaN;
    return (v[0]*(1-a)+v[1]*a)*(1-b)+(v[2]*(1-a)+v[3]*a)*b;
  };
}
export function collisionFrom(instance){
  const p=instance.footprint,x=p.map(v=>v[0]),z=p.map(v=>v[1]);
  const minX=Math.min(...x),maxX=Math.max(...x),minZ=Math.min(...z),maxZ=Math.max(...z);
  return {id:instance.id,x:(minX+maxX)/2,z:(minZ+maxZ)/2,w:maxX-minX,d:maxZ-minZ,h:instance.height_bounds[1]-instance.height_bounds[0],baseY:instance.height_bounds[0],polygon:p,building:true};
}
export function onPolygon(x,z,polygon,radius=0){
  let inside=false,nearest=Infinity;
  for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
    const [ax,az]=polygon[j],[bx,bz]=polygon[i],dx=bx-ax,dz=bz-az;
    if((az>z)!==(bz>z)&&x<(bx-ax)*(z-az)/(bz-az)+ax)inside=!inside;
    const t=Math.max(0,Math.min(1,((x-ax)*dx+(z-az)*dz)/(dx*dx+dz*dz)));
    nearest=Math.min(nearest,Math.hypot(x-ax-dx*t,z-az-dz*t));
  }
  return inside||nearest<=radius;
}
export function segmentTerrain(a,b,map){
  if(!map.heightAt)return null;
  const n=Math.max(1,Math.ceil(Math.hypot(b.x-a.x,b.z-a.z)/1));let previous=0;
  const gap=t=>{const x=a.x+(b.x-a.x)*t,z=a.z+(b.z-a.z)*t;return a.y+(b.y-a.y)*t-map.heightAt(x,z);};
  for(let i=0;i<=n;i++){const t=i/n,g=gap(t);if(Number.isFinite(g)&&g<=0){let lo=previous,hi=t;for(let k=0;k<16;k++){const mid=(lo+hi)/2;if(gap(mid)>0)lo=mid;else hi=mid;}return hi;}previous=t;}
  return null;
}
