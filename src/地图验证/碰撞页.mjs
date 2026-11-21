import {CollisionShape} from './碰撞形状.mjs';
const identity=new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);
export function decodeCollisionPage(buffer,{prototypeCount,expectedCount}={}){
 if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<16)throw new Error('碰撞页头部不完整');
 const data=new DataView(buffer),count=data.getUint32(8,true),stride=data.getUint32(12,true);
 if(data.getUint32(0,true)!==0x4f434142||data.getUint32(4,true)!==1||stride!==124||count>1000000||buffer.byteLength!==16+count*stride||expectedCount!==undefined&&count!==expectedCount)throw new Error('碰撞页身份、长度或计数错误');
 const shapes=[],ids=new Set();
 for(let i=0;i<count;i++){
  const at=16+i*stride,group=data.getUint32(at,true),sourceIndex=data.getUint32(at+4,true),prototype=data.getUint32(at+8,true),component=data.getUint32(at+12,true),flags=data.getUint32(at+16,true),kind=flags&7;
  if(flags&~31||kind<1||kind>3||prototypeCount!==undefined&&prototype>=prototypeCount)throw new Error('碰撞页类型或来源越界');
  const matrix=new Float32Array(buffer,at+20,16),params=new Float32Array(buffer,at+84,4),bounds=new Float32Array(buffer,at+100,6);
  if(![...matrix,...params,...bounds].every(Number.isFinite)||params[0]<0||params[1]<0||kind===1&&params[2]<0||matrix[3]!==0||matrix[7]!==0||matrix[11]!==0||matrix[15]!==1||bounds.some((v,i)=>i<3&&v>bounds[i+3]))throw new Error('碰撞页含无效几何或变换');
  const shape=new CollisionShape({kind:['','box','capsule','cylinder'][kind],params,matrix,movement:!!(flags&8),ballistics:!!(flags&16)},{group,sourceIndex,prototype,matrix:identity},component);
  const error=Math.max(...shape.bounds.map((v,i)=>Math.abs(v-bounds[i])));if(error>.02)throw new Error('碰撞页包围盒与实际形状不符');
  if(ids.has(shape.id))throw new Error('碰撞页来源形状重复');ids.add(shape.id);shapes.push(shape);
 }
 return {shapes,count,bytes:buffer.byteLength+count*1536+4096,accountingVersion:2};
}
