// 使用实际WebGL、实际GLB及Alpha贴图，比较基础层和运行细节层的四向投影轮廓。
import * as THREE from 'three';
function edges(pixels,width,height){
 const mask=new Uint8Array(width*height),points=[];for(let i=0;i<mask.length;i++)mask[i]=pixels[i*4]!==255||pixels[i*4+1]!==0||pixels[i*4+2]!==255?1:0;
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=y*width+x;if(mask[i]&&(!x||!y||x===width-1||y===height-1||!mask[i-1]||!mask[i+1]||!mask[i-width]||!mask[i+width]))points.push([x,y]);}return points;
}
function distance(a,b){if(!a.length&&!b.length)return 0;if(!a.length||!b.length)return Infinity;let maximum=0;for(const [x,y]of a){let minimum=Infinity;for(const [xx,yy]of b)minimum=Math.min(minimum,(x-xx)**2+(y-yy)**2);maximum=Math.max(maximum,Math.sqrt(minimum));}return maximum;}
function unlit(root){const swaps=[];root.traverse(object=>{if(!object.isMesh)return;const original=object.material,convert=m=>new THREE.MeshBasicMaterial({map:m.map,alphaMap:m.alphaMap,alphaTest:m.alphaTest,side:m.side,transparent:m.transparent,opacity:m.opacity,toneMapped:false});object.material=Array.isArray(original)?original.map(convert):convert(original);swaps.push([object,original]);});return()=>{for(const [object,original]of swaps){for(const m of [object.material].flat())m.dispose();object.material=original;}};}
export async function checkPrototypeProjection(runtime,index){
 const renderer=runtime.renderer??runtime.view.renderer,map=runtime.externalMap??runtime.map,p=map.prototypes[index];
 if(!['building','decor'].includes(p.category))return {index,skipped:true};
 const width=640,height=364,target=new THREE.WebGLRenderTarget(width,height),pixels=new Uint8Array(width*height*4),scene=new THREE.Scene();scene.background=new THREE.Color(0xff00ff);
 const base=await map.media.load(p.file),originalBox=new THREE.Box3().setFromObject(base),center=originalBox.getCenter(new THREE.Vector3()),camera=new THREE.OrthographicCamera(-1,1,1,-1,.01,20000),rows=[];
 const restoreBase=unlit(base);scene.add(base);
 const render=(root,span,angle)=>{camera.left=-span/2;camera.right=span/2;camera.top=span*height/width/2;camera.bottom=-camera.top;camera.updateProjectionMatrix();camera.position.copy(center).add(new THREE.Vector3(Math.sin(angle)*650,760,Math.cos(angle)*650));camera.lookAt(center);camera.updateMatrixWorld();root.visible=true;renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,width,height,pixels);renderer.setRenderTarget(null);return edges(pixels,width,height);};
 try{
  for(const [level,file,span]of [['middle',p.middle_file,180],['far',p.far_file,1290]]){
   const derived=await map.media.load(file),restoreDerived=unlit(derived);scene.add(derived);
   try{for(let view=0;view<4;view++){
    derived.visible=false;base.visible=true;const a=render(base,span,view*Math.PI/2);base.visible=false;derived.visible=true;const b=render(derived,span,view*Math.PI/2),error=Math.max(distance(a,b),distance(b,a));
    rows.push({level,view,span,source_edge_pixels:a.length,derived_edge_pixels:b.length,error_pixels:Number.isFinite(error)?error:null,passed:error<=2});
   }}finally{restoreDerived();map.media.release(derived);}
  }
 }finally{restoreBase();map.media.release(base);target.dispose();renderer.setRenderTarget(null);}
 return {index,id:p.id,name:p.name,rows,passed:rows.every(row=>row.passed)};
}
