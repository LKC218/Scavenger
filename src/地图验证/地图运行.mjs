import * as THREE from 'three';
import {createModelLoader} from '/compressed-loader.mjs';
import {heightQuery,collisionFrom} from './地图契约.mjs';

/** 只读取候选批次白名单，不发布到统一资料库。 */
export async function loadMap(url,renderer,progress=()=>{}){
  // 本页只加载冻结白名单；内容寻址贴图在多个GLB之间复用下载与转码结果。
  THREE.Cache.enabled=true;
  const response=await fetch(url);if(!response.ok)throw new Error('地图清单不可读');
  const map=await response.json(),base=new URL('.',new URL(url,location.href));
  if(map.schema==='airbase-streaming-scene-v1'){
    const {loadFullScene}=await import('./全图显示.mjs'),{attachStreamingSimulation}=await import('./分区寻路.mjs');
    map.initial_view_span=map.defaultSpan*renderer.domElement.clientWidth/renderer.domElement.clientHeight;
    const scene=await loadFullScene(url,renderer,progress,map);scene.streaming=true;
    progress('准备出生区与局部寻路…');return attachStreamingSimulation(scene);
  }
  const file=name=>new URL(name,base).href;
  const grid=new Float32Array(await (await fetch(file(map.height.file))).arrayBuffer());
  if(grid.length!==map.height.width*map.height.height)throw new Error('高度场长度不符');
  map.heightAt=heightQuery(map.height,grid);map.obstacles=map.instances.map(collisionFrom);map.cell=4;
  map.spawnSpacing=map.size>=2000?24:12;
  map.center={x:0,z:85};map.maxSpan=map.size*1.35;map.defaultSpan=58;
  map.safeAltitude=Math.max(0,...map.instances.map(i=>i.height_bounds[1]))+72;
  map.targets=[];map.root=new THREE.Group();map.groundMeshes=[];
  map.stats={loadedChunks:0,totalChunks:map.chunks?.length??1,pending:0,evicted:0,errors:[],prototypeCount:Object.keys(map.prototypes).length,instanceCount:map.instances.length,mirroredPrototypeMeshes:0};
  const loader=createModelLoader(renderer);
  const sharedTextures=new Map();
  const share=root=>root.traverse(o=>{for(const material of [o.material].flat().filter(Boolean))for(const [slot,texture] of Object.entries(material)){
    if(!texture?.isTexture||!texture.name.startsWith('共享图像-'))continue;
    const key=texture.name+'|'+[texture.colorSpace,texture.wrapS,texture.wrapT,texture.repeat.x,texture.repeat.y,texture.offset.x,texture.offset.y].join(',');
    const existing=sharedTextures.get(key);if(existing&&existing!==texture){material[slot]=existing;texture.dispose();}else sharedTextures.set(key,texture);
  }});
  progress('加载真实地表与道面…');
  const ground=(await loader.loadAsync(file(map.ground))).scene;
  share(ground);
  ground.traverse(o=>{if(o.isMesh){o.receiveShadow=true;map.groundMeshes.push(o);}});map.root.add(ground);
  map.stats.loadedChunks=map.chunks?0:1;
  for(const [key,row] of Object.entries(map.prototypes)){
    progress('加载机场建筑 '+key+'…');const model=(await loader.loadAsync(file(row.file))).scene;share(model);model.updateMatrixWorld(true);
    const groups=new Map();for(const i of map.instances.filter(i=>i.prototype===key)){
      const chunk=Math.floor((i.matrix[12]+map.size/2)/125)+','+Math.floor((i.matrix[14]+map.size/2)/125);
      if(!groups.has(chunk))groups.set(chunk,[]);groups.get(chunk).push(i);
    }
    model.traverse(o=>{if(!o.isMesh)return;
      let geometry=o.geometry,local=o.matrixWorld;
      // InstancedMesh不使用负尺度实例；将源子节点镜像烘焙到原型并校正绕序。
      if(local.determinant()<0){
        geometry=geometry.clone().applyMatrix4(local);local=new THREE.Matrix4();map.stats.mirroredPrototypeMeshes++;
        if(geometry.index){const index=geometry.index.array;for(let i=0;i<index.length;i+=3)[index[i+1],index[i+2]]=[index[i+2],index[i+1]];geometry.index.needsUpdate=true;}
        else for(const attribute of Object.values(geometry.attributes)){const a=attribute.array,s=attribute.itemSize;for(let i=0;i<attribute.count;i+=3)for(let k=0;k<s;k++){const x=(i+1)*s+k,y=(i+2)*s+k;[a[x],a[y]]=[a[y],a[x]];}attribute.needsUpdate=true;}
      }
      for(const instances of groups.values()){
      const mesh=new THREE.InstancedMesh(geometry,o.material,instances.length),m=new THREE.Matrix4();
      instances.forEach((i,n)=>mesh.setMatrixAt(n,m.fromArray(i.matrix).clone().multiply(local)));
      mesh.castShadow=mesh.receiveShadow=true;mesh.layers.enable(1);mesh.computeBoundingSphere();map.root.add(mesh);
    }});
  }
  // 转码器属于渲染器，后续载具继续复用；本模块不提前释放共享依赖。
  map.root.updateMatrixWorld(true);
  if(map.chunks){
    const manager=new ChunkManager(map,ground,loader,file,share);map.chunkManager=manager;
    map.update=view=>manager.update(view);progress('加载机场局部高细节分块…');await manager.initial();
  }
  map.visibleGroundMeshes=()=>{
    if(!map.chunkManager||ground.visible)return map.groundMeshes;
    const meshes=[];for(const row of map.chunkManager.resident.values())if(row.root.visible)row.root.traverse(o=>{if(o.isMesh&&o.visible)meshes.push(o);});return meshes;
  };
  const overlay=map.overlay=new THREE.Group();overlay.visible=false;map.root.add(overlay);
  const lines=[],lift=(x,z)=>{const h=map.heightAt(x,z);return Number.isFinite(h)?h+.35:.35;};
  for(const i of map.instances)for(let j=0;j<i.footprint.length;j++){const a=i.footprint[j],b=i.footprint[(j+1)%i.footprint.length];lines.push(a[0],lift(...a),a[1],b[0],lift(...b),b[1]);}
  const b=map.bounds;for(let x=b.minX;x<=b.maxX;x+=125)lines.push(x,lift(x,b.minZ),b.minZ,x,lift(x,b.maxZ),b.maxZ);for(let z=b.minZ;z<=b.maxZ;z+=125)lines.push(b.minX,lift(b.minX,z),z,b.maxX,lift(b.maxX,z),z);
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(lines,3));overlay.add(new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0x72eeff,depthTest:false,transparent:true,opacity:.8})));
  for(const ground of map.groundMeshes)if(ground.userData.category==='road'){const wire=new THREE.Mesh(ground.geometry,new THREE.MeshBasicMaterial({color:0xffce63,wireframe:true,depthTest:false}));wire.matrix.copy(ground.matrixWorld);wire.matrixAutoUpdate=false;overlay.add(wire);}
  return map;
}

/** 地表块按需加载；全局高度与静态碰撞不随渲染回收。 */
class ChunkManager{
  constructor(map,overview,loader,file,share){Object.assign(this,{map,overview,loader,file,share});this.resident=new Map();this.desired=new Set();this.queue=[];this.loading=false;this.tick=0;this.last=null;this.overviewMode=false;}
  async initial(){this.choose({x:this.map.center.x,z:this.map.center.z},58);await this.pump();this.display();}
  choose(center,span){
    const radiusX=span*.95+125,radiusZ=span*.8+125;
    const candidates=this.map.chunks.filter(c=>c.bounds.minX<center.x+radiusX&&c.bounds.maxX>center.x-radiusX&&c.bounds.minZ<center.z+radiusZ&&c.bounds.maxZ>center.z-radiusZ);
    candidates.sort((a,b)=>Math.hypot((a.bounds.minX+a.bounds.maxX)/2-center.x,(a.bounds.minZ+a.bounds.maxZ)/2-center.z)-Math.hypot((b.bounds.minX+b.bounds.maxX)/2-center.x,(b.bounds.minZ+b.bounds.maxZ)/2-center.z));
    this.desired=new Set(candidates.slice(0,this.map.streaming.maxResident).map(c=>c.id));this.queue=candidates.filter(c=>this.desired.has(c.id)&&!this.resident.has(c.id));
    for(const id of this.desired){const row=this.resident.get(id);if(row)row.used=++this.tick;}
    this.map.stats.pending=this.queue.length;
  }
  async pump(){
    if(this.loading)return;this.loading=true;
    try{while(this.queue.length){const chunk=this.queue.shift();if(this.resident.has(chunk.id)||!this.desired.has(chunk.id))continue;
      const root=(await this.loader.loadAsync(this.file(chunk.file))).scene;this.share(root);root.traverse(o=>{if(o.isMesh)o.receiveShadow=true;});
      root.visible=false;this.map.root.add(root);this.resident.set(chunk.id,{root,chunk,used:++this.tick});this.trim();this.display();
    }}catch(error){this.map.stats.errors.push(error.message);console.error('地图块加载失败',error);}
    finally{this.loading=false;this.display();}
  }
  trim(){
    while(this.resident.size>this.map.streaming.maxResident){const candidate=[...this.resident.entries()].filter(([id])=>!this.desired.has(id)).sort((a,b)=>a[1].used-b[1].used)[0];if(!candidate)break;
      const [id,row]=candidate;row.root.removeFromParent();const geometries=new Set(),materials=new Set();row.root.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of [o.material].flat().filter(Boolean))materials.add(m);});for(const g of geometries)g.dispose();for(const m of materials)m.dispose();this.resident.delete(id);this.map.stats.evicted++;
    }
  }
  display(){
    const ready=[...this.desired].every(id=>this.resident.has(id));
    this.overview.visible=this.overviewMode||!ready;
    for(const [id,row] of this.resident)row.root.visible=!this.overviewMode&&ready&&this.desired.has(id);
    this.map.stats.loadedChunks=this.resident.size;this.map.stats.visibleChunks=this.overviewMode||!ready?0:this.desired.size;this.map.stats.pending=[...this.desired].filter(id=>!this.resident.has(id)).length;this.map.stats.overview=this.overview.visible;
  }
  update(view){
    const mode=view.span>=this.map.streaming.overviewSpan;this.overviewMode=mode;
    const p=view.center,old=this.last;
    if(!mode&&(!old||Math.hypot(p.x-old.x,p.z-old.z)>16||Math.abs(view.span-old.span)>12||old.mode!==mode)){
      this.last={x:p.x,z:p.z,span:view.span,mode};this.choose(p,view.span);void this.pump();
    }else if(mode&&old)old.mode=mode;
    this.display();
  }
}
