export function decodeSimulationPackage(buffer,cell){
 if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<16)throw new Error('模拟分区头部不完整');const data=new DataView(buffer),count=data.getUint32(8,true),stride=data.getUint32(12,true);
 if(data.getUint32(0,true)!==0x50534142||data.getUint32(4,true)!==1||count<1||count>4||stride!==20||buffer.byteLength<16+count*20)throw new Error('模拟分区身份或表错误');
 const ids=new Set(),ranges=[];let selected;
 for(let i=0;i<count;i++){
  const at=16+i*20,id=data.getUint32(at,true),ao=data.getUint32(at+4,true),an=data.getUint32(at+8,true),bo=data.getUint32(at+12,true),bn=data.getUint32(at+16,true);
  if(id>=5184||ids.has(id)||ao%4||bo%4||an<32||bn<16||ao<16+count*20||bo<ao+an||bo+bn>buffer.byteLength)throw new Error('模拟分区条目越界');
  ids.add(id);ranges.push([ao,ao+an],[bo,bo+bn]);if(id===cell)selected={support:buffer.slice(ao,ao+an),collision:buffer.slice(bo,bo+bn)};
 }
 ranges.sort((a,b)=>a[0]-b[0]);let end=16+count*20;for(const [a,b] of ranges){if(a!==end)throw new Error('模拟分区有重叠或未声明载荷');end=b;}if(end!==buffer.byteLength||!selected)throw new Error('模拟分区未包含请求格');return selected;
}
