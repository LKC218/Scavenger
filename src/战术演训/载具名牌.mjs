/** 名牌只在机身附近布置；局部无空间时由调用者降级为兵种图标。 */
import {StableLabelLayout} from './标签布局.mjs';

export const UNIT_SYMBOLS={
  tank:'M3 8h14l3 4-2 4H5l-3-4z M8 8V5h6v3 M14 5h7 M6 12h10',
  heli:'M4 11h12l4 3h-9l-3-3 M12 11V6 M5 5h15 M4 11V7 M9 17h8',
  plane:'M12 2l2 7 7 5v2l-7-2v5l3 2H7l3-2v-5l-7 2v-2l7-5z'
};

export const rectDistance=(a,b)=>Math.hypot(Math.max(a.left-b.right,b.left-a.right,0),Math.max(a.top-b.bottom,b.top-a.bottom,0));

export class AttachedLabelLayout extends StableLabelLayout {
  place({key,unitId,kind,anchor,width,height,body,candidates,maxGap=30}){
    const old=this.entries.get(key),info={unitId,kind,anchor:{...anchor}},ordered=[...candidates];
    const previous=ordered.findIndex(c=>c.slot===old?.slot);
    // 只有当前候选仍在局部范围内才保留槽位，避免把旧远处位置带入新一帧。
    if(previous>0)ordered.unshift(...ordered.splice(previous,1));
    for(const c of ordered){
      const box=this.box(c.x,c.y,width,height);
      if(rectDistance(box,body)>maxGap||!this.available(box,old?.slot===c.slot?0:3))continue;
      info.placementReason=old?.slot===c.slot?'kept':'local-slot';
      return this.record(key,info,box,c.slot,'placed');
    }
    return this.hide(key,info,'local-space-exhausted');
  }
}

export function nearbySlots(body,width,height){
  const x=(body.left+body.right)/2,y=(body.top+body.bottom)/2,gap=8;
  return [
    ['above',x-width/2,body.top-gap-height],['below',x-width/2,body.bottom+gap],
    ['right',body.right+gap,y-height/2],['left',body.left-gap-width,y-height/2],
    ['above-right',body.right-width,body.top-gap-height],['above-left',body.left,body.top-gap-height],
    ['below-right',body.right-width,body.bottom+gap],['below-left',body.left,body.bottom+gap]
  ].map(([slot,x,y])=>({slot,x,y}));
}

/** 两端落在矩形边界，连接线不穿过名牌或机身主体。 */
export function attachmentLine(body,label){
  const bx=(body.left+body.right)/2,by=(body.top+body.bottom)/2,lx=(label.left+label.right)/2,ly=(label.top+label.bottom)/2;
  const edge=(r,x,y,tx,ty)=>{
    const dx=tx-x,dy=ty-y,t=Math.min(dx?Math.abs((r.right-r.left)/2/dx):Infinity,dy?Math.abs((r.bottom-r.top)/2/dy):Infinity);
    return{x:x+dx*t,y:y+dy*t};
  };
  return{from:edge(body,bx,by,lx,ly),to:edge(label,lx,ly,bx,by)};
}
