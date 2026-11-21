export class CoarseHeight {
 constructor(buffer){
  const data=new DataView(buffer);if(buffer.byteLength<32||data.getUint32(0,true)!==0x48434142||data.getUint32(4,true)!==1)throw new Error('粗层高度页身份错误');
  this.width=data.getUint32(8,true);this.height=data.getUint32(12,true);this.x=data.getFloat32(16,true);this.z=data.getFloat32(20,true);this.dx=data.getFloat32(24,true);this.dz=data.getFloat32(28,true);
  if(this.width<2||this.height<2||this.width>2048||this.height>2048||buffer.byteLength!==32+this.width*this.height*4||![this.x,this.z,this.dx,this.dz].every(Number.isFinite)||this.dx<=0||this.dz<=0)throw new Error('粗层高度尺寸错误');
  this.values=new Float32Array(buffer,32);this.bytes=buffer.byteLength;
 }
 at(x,z){
  const px=(x-this.x)/this.dx,pz=(z-this.z)/this.dz;if(![px,pz].every(Number.isFinite)||px<0||pz<0||px>this.width-1||pz>this.height-1)return undefined;
  const ix=Math.min(this.width-2,Math.floor(px)),iz=Math.min(this.height-2,Math.floor(pz)),a=px-ix,b=pz-iz,i=iz*this.width+ix,v=this.values;
  const h=(v[i]*(1-a)+v[i+1]*a)*(1-b)+(v[i+this.width]*(1-a)+v[i+this.width+1]*a)*b;return Number.isFinite(h)?h:undefined;
 }
}
