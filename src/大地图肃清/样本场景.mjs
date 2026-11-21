/** 600米受控场景；几何显示与规则共读同一规格，未知支撑不补零。 */
import {clamp} from './契约.mjs';

export function createWorld() {
  const bounds={minX:-300,maxX:300,minZ:-300,maxZ:300};
  const obstacles=[
    {id:'wall-main',x:25,z:0,width:4,depth:90,minY:0,maxY:18},
    {id:'wall-side',x:-85,z:70,width:80,depth:4,minY:0,maxY:8},
  ];
  const zones=[{id:'A',center:{x:-170,y:0,z:-80},radius:60},{id:'B',center:{x:150,y:0,z:-100},radius:60},{id:'C',center:{x:0,y:0,z:170},radius:60}];
  function querySupport(x,z) {
    if(!Number.isFinite(x)||!Number.isFinite(z)||x<bounds.minX||x>bounds.maxX||z<bounds.minZ||z>bounds.maxZ)return{status:'unsupported'};
    if(x>-230&&x<-190&&z>150&&z<210)return{status:'unsupported'};
    if(x>230&&z>210)return{status:'pending'};
    const height=x>=90&&x<=190&&z>=60&&z<=180?20*clamp((z-60)/100,0,1):0;
    return{status:'ready',height};
  }
  function isWalkable(x,z,radius=0) {
    if(querySupport(x,z).status!=='ready')return false;
    if(x<bounds.minX+radius||x>bounds.maxX-radius||z<bounds.minZ+radius||z>bounds.maxZ-radius)return false;
    return !obstacles.some(o=>Math.abs(x-o.x)<=o.width/2+radius&&Math.abs(z-o.z)<=o.depth/2+radius);
  }
  function segmentHit(a,b,padding=0) {
    let best=null;
    for(const o of obstacles){
      let low=0,high=1,axis=null,sign=0;
      const ranges={x:[o.x-o.width/2-padding,o.x+o.width/2+padding],y:[o.minY-padding,o.maxY+padding],z:[o.z-o.depth/2-padding,o.z+o.depth/2+padding]};
      let valid=true;
      for(const k of ['x','y','z']){
        const av=a[k]??0,bv=b[k]??0,d=bv-av,[min,max]=ranges[k];
        if(Math.abs(d)<1e-10){if(av<min||av>max){valid=false;break;}continue;}
        let near=(min-av)/d,far=(max-av)/d,side=-Math.sign(d);
        if(near>far)[near,far]=[far,near];
        if(near>low){low=near;axis=k;sign=side;}
        high=Math.min(high,far);if(low>high){valid=false;break;}
      }
      if(valid&&low>=0&&low<=1&&(!best||low<best.t)){
        const point={x:a.x+(b.x-a.x)*low,y:(a.y??0)+((b.y??0)-(a.y??0))*low,z:a.z+(b.z-a.z)*low};
        const normal={x:0,y:0,z:0};if(axis)normal[axis]=sign;
        best={t:low,point,normal,obstacleId:o.id};
      }
    }
    return best;
  }
  return{bounds,zones,obstacles,maxObstacleHeight:20,querySupport,isWalkable,segmentHit,
    heightAt(x,z){const s=querySupport(x,z);return s.status==='ready'?s.height:NaN;},
    lineClear(a,b){return !segmentHit(a,b);},
    segmentBlocked(a,b,radius=0){return !!segmentHit({...a,y:1},{...b,y:1},radius);},
  };
}
