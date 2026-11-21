/** 纯屏幕标签布局：固定优先级、可用槽位保持、较宽的重新进入间隙。 */
export const intersects=(a,b,gap=0)=>a.left<b.right+gap&&a.right>b.left-gap&&a.top<b.bottom+gap&&a.bottom>b.top-gap;
export function rotationEnvelope(box,axis){
  const out={min:{...box.min},max:{...box.max}},a=axis==='x'?'y':'x',b='z',radius=Math.hypot(Math.max(Math.abs(box.min[a]),Math.abs(box.max[a])),Math.max(Math.abs(box.min[b]),Math.abs(box.max[b])));
  out.min[a]=out.min[b]=-radius;out.max[a]=out.max[b]=radius;return out;
}
export class StableLabelLayout {
  constructor(){this.entries=new Map();this.frame=0;this.placementChanges=0;this.visibilityChanges=0;}
  clear(){this.entries.clear();this.frame=0;this.placementChanges=0;this.visibilityChanges=0;}
  begin({width,height,blockers=[],time=0}){Object.assign(this,{width,height,blockers,time});this.occupied=[];this.active=new Set();this.frame++;}
  box(left,top,width,height,clamp=true){
    if(clamp){left=Math.max(4,Math.min(left,this.width-width-4));top=Math.max(4,Math.min(top,this.height-height-4));}
    return{left,top,right:left+width,bottom:top+height,width,height};
  }
  failure(box,gap=0){if(!(box.left>=4&&box.top>=4&&box.right<=this.width-4&&box.bottom<=this.height-4))return'out-of-bounds';if(this.blockers.some(b=>intersects(box,b,gap)))return'geometry-or-hud';if(this.occupied.some(b=>intersects(box,b,gap)))return'label-overlap';return null;}
  available(box,gap=0){return this.failure(box,gap)===null;}
  record(key,info,box,slot,reason){
    const old=this.entries.get(key),visible=!!box,changed=!!(visible&&old?.visible&&old.slot!==slot);
    const entry={...old,...info,key,visible,slot:slot??old?.slot??null,reason,frame:this.frame,revision:(old?.revision??0)+Number(!old||changed||old.visible!==visible),slotChanges:(old?.slotChanges??0)+Number(changed),visibilityChanges:(old?.visibilityChanges??0)+Number(!!old&&old.visible!==visible)};
    if(box){Object.assign(entry,{x:box.left,y:box.top,width:box.width,height:box.height,dx:box.left+box.width/2-info.anchor.x,dy:box.top+box.height/2-info.anchor.y});this.occupied.push(box);}
    this.placementChanges+=Number(changed);this.visibilityChanges+=Number(!!old&&old.visible!==visible);this.entries.set(key,entry);this.active.add(key);return entry;
  }
  hide(key,info,reason){return this.record(key,info,null,null,reason);}
  place({key,unitId,kind,anchor,width,height,candidates}){
    const info={unitId,kind,anchor:{...anchor},placementReason:'initial'},old=this.entries.get(key);let box=null,slot=null;
    if(old){
      const candidate=candidates.find(c=>c.slot===old.slot);
      const ratio=(anchor.scale??1)/(old.anchor?.scale??1),angle=(anchor.yaw??0)-(old.anchor?.yaw??0),tilt=Math.sin(anchor.tilt??Math.PI/2),dx=old.dx*ratio,dy=old.dy*ratio;
      const offsetX=dx*Math.cos(angle)-dy/tilt*Math.sin(angle),offsetY=dx*Math.sin(angle)*tilt+dy*Math.cos(angle);
      const previous=candidate?this.box(candidate.x,candidate.y,width,height):this.box(anchor.x+offsetX-width/2,anchor.y+offsetY-height/2,width,height,false);
      const failure=this.failure(previous,old.visible?0:6);info.placementReason=failure??'kept';if(!failure){box=previous;slot=old.slot;}
    }
    if(!box)for(const candidate of candidates){const next=this.box(candidate.x,candidate.y,width,height);if(this.available(next,6)){box=next;slot=candidate.slot;break;}}
    if(!box){
      const free=[];for(let top=4;top<=this.height-height-4;top+=24)for(let left=4;left<=this.width-width-4;left+=24){const dx=left+width/2-anchor.x,dy=top+height/2-anchor.y;free.push({left,top,d:dx*dx+dy*dy});}
      free.sort((a,b)=>a.d-b.d||a.top-b.top||a.left-b.left);
      for(const candidate of free){const next=this.box(candidate.left,candidate.top,width,height,false);if(this.available(next,6)){box=next;slot='free:'+Math.round(next.left+width/2-anchor.x)+':'+Math.round(next.top+height/2-anchor.y);break;}}
    }
    return this.record(key,info,box,slot,box?'placed':'no-space');
  }
  finish(){
    for(const [key,entry]of this.entries)if(!this.active.has(key)&&entry.visible)this.hide(key,entry,'not-eligible');
    return{frame:this.frame,time:this.time,visibleCount:[...this.entries.values()].filter(e=>e.visible).length,placementChanges:this.placementChanges,visibilityChanges:this.visibilityChanges,rows:[...this.entries.values()].map(e=>({...e,anchor:{...e.anchor}}))};
  }
}
