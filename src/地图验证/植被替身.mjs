// 原生半球图集网页适配。相邻四帧连续混合；源深度视差不在当前实现内。
import * as THREE from 'three';

export function impostorFrame(record,descriptor,cameraDirection){
 const local=cameraDirection.clone().transformDirection(new THREE.Matrix4().fromArray(record.matrix).invert());
 // 原图集参数来自Unity；原型GLB经过(x,y,z)→(x,y,-z)转换。
 local.z=-local.z;
 local.y=Math.max(0,local.y);const sum=Math.abs(local.x)+local.y+Math.abs(local.z)||1,x=local.x/sum,z=local.z/sum,n=descriptor.frames;
 // 半球八面体映射：上方落在中心，水平各方向落在菱形边界。
 const u=(x+z)*.5+.5,v=(z-x)*.5+.5;
 return [THREE.MathUtils.clamp(u,0,1)*(n-1),THREE.MathUtils.clamp(v,0,1)*(n-1)];
}

export function prepareImpostorMaterial(original,descriptor){
 const material=new THREE.MeshBasicMaterial({map:original.map,alphaTest:descriptor.alpha_cutoff,side:THREE.DoubleSide,depthWrite:true});
 // 原图集含原引擎烘焙照明；网页适配使用显式线性色阶，最终数值随版本留证。
 material.color.setRGB(...(descriptor.color_scale??[3,3,3]));
 material.onBeforeCompile=shader=>{
  shader.vertexShader='attribute vec2 impostorFrame;varying vec2 vImpostorFrame;\n'+shader.vertexShader;
  shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nvMapUv=vec2(vMapUv.x,1.0-vMapUv.y);vImpostorFrame=impostorFrame;');
  shader.vertexShader=shader.vertexShader.replace('#include <project_vertex>',`vec4 mvPosition=modelViewMatrix*vec4(instanceMatrix[3].xyz,1.0);mvPosition.xy+=position.xy*vec2(length(instanceMatrix[0].xyz),length(instanceMatrix[1].xyz));gl_Position=projectionMatrix*mvPosition;`);
  shader.fragmentShader='varying vec2 vImpostorFrame;\n'+shader.fragmentShader;
  const n=Number(descriptor.frames).toFixed(1);
  shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`vec2 frame0=floor(vImpostorFrame),frame1=min(frame0+1.0,vec2(${n}-1.0)),weight=fract(vImpostorFrame);vec4 a=texture2D(map,(vMapUv+frame0)/${n}),b=texture2D(map,(vMapUv+vec2(frame1.x,frame0.y))/${n}),c=texture2D(map,(vMapUv+vec2(frame0.x,frame1.y))/${n}),d=texture2D(map,(vMapUv+frame1)/${n});diffuseColor*=mix(mix(a,b,weight.x),mix(c,d,weight.x),weight.y);`);
 };
 material.customProgramCacheKey=()=>`airbase-native-impostor-blend-${descriptor.frames}`;
 return material;
}

/** 真实源LOD1与网页替身并排审图；返回像素图，不把来源身份当作画面通过。 */
export async function reviewImpostor(runtime,index,{calibrate=false}={}){
 const map=runtime.externalMap,renderer=runtime.view.renderer,p=map.prototypes[index],descriptor={...p.impostor};
 if(!descriptor)throw new Error('原型无原生替身描述');
 const source=await map.media.load(p.native_lod1_file),carrier=await map.media.load(p.vegetation_file),scene=new THREE.Scene(),target=new THREE.WebGLRenderTarget(320,320),pixels=new Uint8Array(320*320*4);
 target.texture.colorSpace=THREE.SRGBColorSpace;scene.background=new THREE.Color('#aab4a2');
 for(const object of runtime.view.scene.children)if(object.isLight)scene.add(object.clone());
 const camera=new THREE.OrthographicCamera(-descriptor.size*.7,descriptor.size*.7,descriptor.size*.7,-descriptor.size*.7,.01,1000),center=new THREE.Vector3(descriptor.offset[0],descriptor.offset[1],-descriptor.offset[2]),record={matrix:new THREE.Matrix4().toArray()},rows=[],materials=[];
 const impostor=new THREE.Group();carrier.traverse(object=>{if(!object.isMesh)return;const geometry=object.geometry.clone(),material=prepareImpostorMaterial(object.material,descriptor),mesh=new THREE.InstancedMesh(geometry,material,1);geometry.setAttribute('impostorFrame',new THREE.InstancedBufferAttribute(new Float32Array(2),2));mesh.setMatrixAt(0,impostorMatrix(record,descriptor));mesh.frustumCulled=false;impostor.add(mesh);materials.push([geometry,material,mesh]);});
 scene.add(source,impostor);
 const poses=[['正面',0,50],['右侧',Math.PI/2,50],['背面',Math.PI,50],['左侧',Math.PI*1.5,50],['俯视',0,90]];
 const pose=(angle,elevation)=>{const radians=THREE.MathUtils.degToRad(elevation),direction=new THREE.Vector3(Math.sin(angle)*Math.cos(radians),Math.sin(radians),Math.cos(angle)*Math.cos(radians));camera.position.copy(center).addScaledVector(direction,descriptor.size*3);camera.lookAt(center);camera.updateMatrixWorld();const frame=impostorFrame(record,descriptor,direction);for(const [geometry]of materials){geometry.attributes.impostorFrame.array.set(frame);geometry.attributes.impostorFrame.needsUpdate=true;}return frame;};
 const read=()=>{renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,320,320,pixels);renderer.setRenderTarget(null);};
 const mean=()=>{let count=0;const rgb=[0,0,0],bg=pixels.slice(0,3);for(let i=0;i<pixels.length;i+=4){if(Math.max(...[0,1,2].map(k=>Math.abs(pixels[i+k]-bg[k])))<=5)continue;count++;for(let k=0;k<3;k++)rgb[k]+=pixels[i+k];}return rgb.map(value=>value/Math.max(1,count));};
 let calibration;
 try{
  if(calibrate){
   const average=visible=>{source.visible=visible;impostor.visible=!visible;const sum=[0,0,0];for(const [,angle,elevation]of poses){pose(angle,elevation);read();const rgb=mean();for(let k=0;k<3;k++)sum[k]+=rgb[k]/poses.length;}return sum;};
   const targetRGB=average(true),scale=[3,3,3];
   for(let channel=0;channel<3;channel++){let low=0,high=32;for(let attempt=0;attempt<9;attempt++){scale[channel]=(low+high)/2;for(const [,material]of materials)material.color.setRGB(...scale);const value=average(false)[channel];if(value<targetRGB[channel])low=scale[channel];else high=scale[channel];}scale[channel]=(low+high)/2;}
   // 颜色均值不能以树干偏紫或整体灰化为代价：通道增益相对绿色限制在1.5倍内。
   const neutral=scale[1];for(let channel=0;channel<3;channel++)scale[channel]=THREE.MathUtils.clamp(scale[channel],neutral/1.5,neutral*1.5);
   descriptor.color_scale=scale;for(const [,material]of materials)material.color.setRGB(...scale);calibration={method:'固定五方向实际WebGL前景平均RGB有界二分，通道增益比最多1.5；Alpha、位置、大小及原图集不变，剩余色差如实保留',targetRGB,derivedRGB:average(false),color_scale:scale};
  }
  for(const [name,angle,elevation]of poses){
  const radians=THREE.MathUtils.degToRad(elevation),direction=new THREE.Vector3(Math.sin(angle)*Math.cos(radians),Math.sin(radians),Math.cos(angle)*Math.cos(radians));
  camera.position.copy(center).addScaledVector(direction,descriptor.size*3);camera.lookAt(center);camera.updateMatrixWorld();
  const frame=impostorFrame(record,descriptor,direction);for(const [geometry]of materials){geometry.attributes.impostorFrame.array.set(frame);geometry.attributes.impostorFrame.needsUpdate=true;}
  for(const [role,visible]of [['源LOD1',true],['网页替身',false]]){source.visible=visible;impostor.visible=!visible;renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,320,320,pixels);renderer.setRenderTarget(null);
   const canvas=document.createElement('canvas');canvas.width=canvas.height=320;const data=new Uint8ClampedArray(pixels.length);for(let y=0;y<320;y++)data.set(pixels.subarray((319-y)*1280,(320-y)*1280),y*1280);canvas.getContext('2d').putImageData(new ImageData(data,320,320),0,0);rows.push({name,role,frame,image:canvas.toDataURL('image/png')});}
 }}finally{renderer.setRenderTarget(null);target.dispose();for(const [geometry,material,mesh]of materials){mesh.dispose();geometry.dispose();material.dispose();}map.media.release(source);map.media.release(carrier);}
 return {index,id:p.id,name:p.name,descriptor,calibration,rows};
}

export function impostorMatrix(record,descriptor){
 const source=new THREE.Matrix4().fromArray(record.matrix),elements=source.elements,center=new THREE.Vector3(descriptor.offset[0],descriptor.offset[1],-descriptor.offset[2]).applyMatrix4(source);
 const sx=Math.hypot(elements[0],elements[1],elements[2]),sy=Math.hypot(elements[4],elements[5],elements[6]),sz=Math.hypot(elements[8],elements[9],elements[10]);
 return new THREE.Matrix4().makeScale((sx+sz)*.5*descriptor.size,sy*descriptor.size,1).setPosition(center);
}
