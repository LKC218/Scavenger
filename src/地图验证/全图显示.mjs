import * as THREE from 'three';
import {DownloadStore,RegionData,RequestPool} from './区域数据.mjs';
import {RegionMedia} from './区域媒体.mjs';
import {decodeInstancePage} from './实例页.mjs';
import {TriangleSupportTile} from './精细支撑.mjs';
import {decodeCollisionPage} from './碰撞页.mjs';
import {CoarseHeight} from './粗层高度.mjs';
import {decodeSimulationPackage} from './模拟分区.mjs';
import {impostorFrame,impostorMatrix,prepareImpostorMaterial} from './植被替身.mjs';

const intersects=(a,b)=>a[0]<=b.maxX&&a[3]>=b.minX&&a[2]<=b.maxZ&&a[5]>=b.minZ;
const determinant=a=>a[0]*(a[5]*a[10]-a[9]*a[6])-a[4]*(a[1]*a[10]-a[9]*a[2])+a[8]*(a[1]*a[6]-a[5]*a[2]);
const reverse=geometry=>{
  const index=geometry.index;if(!index)throw new Error('镜像原型缺少索引');
  for(let i=0;i<index.count;i+=3){const b=index.getX(i+1);index.setX(i+1,index.getX(i+2));index.setX(i+2,b);}index.needsUpdate=true;
};

/** 全图显示与区域查询入口；单位与命令通过prepareRegion持有各自数据租约。 */
export async function loadFullScene(url,renderer,progress=()=>{},supplied,options={}){
  const response=supplied?null:await fetch(url);if(response&&!response.ok)throw new Error('全图清单不可读');
  const manifest=supplied??await response.json();if(manifest.schema!=='airbase-streaming-scene-v1')throw new Error('不是全图区域清单');
  const base=new URL(manifest.package_base??'.',new URL(url,location.href));
  const downloads=new DownloadStore({limit:manifest.budgets.download,pool:new RequestPool(4)}),media=new RegionMedia(renderer,manifest,base,downloads);
  const data=new RegionData(manifest,base,downloads,{limit:manifest.budgets.simulation,loadTile:async(row,signal)=>{
    let supportBuffer,collisionBuffer;
    if(row.simulation_package){const packet=manifest.simulation_packages[row.simulation_package],buffer=await downloads.get(new URL(packet.file,base).href,packet.identity,signal),[x,z]=row.id.split('-').map(Number),parts=decodeSimulationPackage(buffer,z*72+x);supportBuffer=parts.support;collisionBuffer=parts.collision;if(supportBuffer.byteLength!==row.geometry_identity.bytes||collisionBuffer.byteLength!==row.collision_identity.bytes)throw new Error('模拟分区原件大小与清单不符');}
    else [supportBuffer,collisionBuffer]=await Promise.all([downloads.get(new URL(row.geometry,base).href,row.geometry_identity,signal),row.collision?downloads.get(new URL(row.collision,base).href,row.collision_identity,signal):null]);
    const support=new TriangleSupportTile(supportBuffer),collision=collisionBuffer?decodeCollisionPage(collisionBuffer,{prototypeCount:manifest.prototypes.length,expectedCount:row.collision_count}):{shapes:[],bytes:0};
    const b=row.bounds;let backgroundFallback=false;if(manifest.background_layering&&support.rolesMask&(1<<4))for(const u of [.1,.5,.9])for(const v of [.1,.5,.9])if(support.query(b[0]+(b[1]-b[0])*u,b[2]+(b[3]-b[2])*v).role===4)backgroundFallback=true;
    return {query:(x,z,hint)=>support.query(x,z,hint),segment:(a,b)=>support.segment(a,b),hasWater:!!(support.rolesMask&(1<<5)),backgroundFallback,shapes:collision.shapes,bytes:support.bytes+collision.bytes};
  }});
  const map={...manifest,packageBase:base,root:new THREE.Group(),overlay:new THREE.Group(),groundMeshes:[],targets:[],obstacles:[],data,downloads,media};map.root.add(map.overlay);map.overlay.visible=false;
  const stats=map.stats={loadedChunks:0,visibleChunks:0,pending:0,evicted:0,errors:[],prototypeCount:0,instanceCount:0,visibleTrees:0,sourceTrees:manifest.vegetation.source_count,grassEnabled:false,overview:true,mirroredPrototypeMeshes:0};
  const startup=performance.now();stats.startupTimings={};
  const lifetime=new AbortController(),chunks=new Map(),pages=new Map(),models=new Map(),plantModels=new Map(),pageRows=new Map(manifest.pages.map(p=>[p.id,p]));
  let generation=0,controller=null,last=null,disposed=false,overview,background,water,backgroundPromise,coarse,batches=[],instanceBytes=0,pageBytes=0,farBytes=0,farRecords=null,farRoot=null,clock=0,loading=null;
  const proxyRoot=new THREE.Group(),canopy=new THREE.Mesh(new THREE.OctahedronGeometry(1,0),new THREE.MeshStandardMaterial({color:'#455b32',roughness:1})),trunk=new THREE.Mesh(new THREE.CylinderGeometry(1,1,1,5),new THREE.MeshStandardMaterial({color:'#665849',roughness:1})),aggregate=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({roughness:1}));
  proxyRoot.add(canopy,trunk,aggregate);media.register(proxyRoot);
  const groundList=root=>{const meshes=[];root.traverse(o=>{if(o.isMesh&&o.visible&&(!o.material?.userData.render_role||[1,2,3,4].includes(o.material.userData.render_role)))meshes.push(o);});return meshes;};
  const limits=()=>{
    const m=media.stats(),simulation=data.stats();
    const navigation=map.navigation?.stats().bytes??0;
    return {geometry:m.geometryBytes+instanceBytes,textures:m.textureBytes,simulation:simulation.bytes+simulation.reserved+navigation+(coarse?.bytes??0),cpu:m.geometryCpuBytes+m.textureBytes+m.transcodeExtraBytes+m.stagingBytes+instanceBytes+pageBytes+farBytes+(coarse?.bytes??0)+simulation.bytes+simulation.reserved+navigation};
  };
  const checkBudget=()=>{const used=limits();if(used.geometry>manifest.budgets.geometry||used.textures>manifest.budgets.textures||used.cpu>manifest.budgets.cpu||used.simulation>manifest.budgets.simulation)throw new Error('当前区域超过资源预算 '+JSON.stringify(used)+'，保留总览并等待重试');};
  const removeBatches=()=>{for(const mesh of batches){mesh.removeFromParent();mesh.dispose();if(mesh.userData.ownedGeometry)mesh.geometry.dispose();}batches=[];instanceBytes=0;};
  const releaseModel=row=>{media.release(row.root);if(row.mirror)media.release(row.mirror);};
  // 固定版本默认阴影材质跨对象复用时可保留已释放贴图的uniform；按源材质持有稳定深度材质。
  const depthMaterials=new WeakMap();
  const depthMaterialFor=material=>{
    if(Array.isArray(material))return undefined;
    let depth=depthMaterials.get(material);if(depth)return depth;
    depth=new THREE.MeshDepthMaterial();depthMaterials.set(material,depth);
    const release=()=>{depth.dispose();depthMaterials.delete(material);material.removeEventListener('dispose',release);};
    material.addEventListener('dispose',release);return depth;
  };
  const prepareModelRoot=(source,impostor)=>{
    source.updateMatrixWorld(true);const root=new THREE.Group();
    source.traverse(o=>{if(!o.isMesh)return;const geometry=o.geometry.clone().applyMatrix4(o.matrixWorld);if(o.matrixWorld.determinant()<0){reverse(geometry);stats.mirroredPrototypeMeshes++;}
      const convert=m=>impostor?prepareImpostorMaterial(m,impostor):m.clone(),material=Array.isArray(o.material)?o.material.map(convert):convert(o.material);root.add(new THREE.Mesh(geometry,material));});
    media.register(root);media.release(source);return root;
  };
  const getModel=async(pi,signal,level='near',prepared)=>{
    const cache=prepared??(level==='vegetation'?plantModels:models);
    let row=cache.get(pi);if(row&&row.level!==level){releaseModel(row);cache.delete(pi);row=null;}if(row){row.used=++clock;return row;}
    const prototype=manifest.prototypes[pi];if(!prototype.enabled||!prototype.file)throw new Error('运行实例引用了省略的原型');
    const filename=level==='vegetation'?prototype.vegetation_file:level==='far'?prototype.far_file:level==='middle'?prototype.middle_file:prototype.file;
    const source=await media.load(filename,signal),root=prepareModelRoot(source,level==='vegetation'?prototype.impostor:undefined);
    row={root,level,used:++clock,triangles:0};root.traverse(o=>{if(o.isMesh)row.triangles+=(o.geometry.index?.count??o.geometry.attributes.position.count)/3;});cache.set(pi,row);
    try{checkBudget();return row;}catch(error){cache.delete(pi);releaseModel(row);throw error;}
  };
  const getPage=async(id,signal)=>{
    let row=pages.get(id);if(row){row.used=++clock;return row;}
    const source=pageRows.get(id);if(!source)throw new Error('场景跨块引用未闭合: '+id);
    const buffer=await downloads.get(new URL(source.file,base).href,source.identity,signal);
    if(signal?.aborted)throw new DOMException('过期实例页','AbortError');
    if(pages.has(id))return pages.get(id);
    const decoded=decodeInstancePage(buffer,{prototypeCount:manifest.prototypes.length,groupCount:manifest.group_count,expectedCount:source.count});
    row={...decoded,used:++clock,accounted:decoded.bytes+decoded.count*320};pages.set(id,row);pageBytes+=row.accounted;return row;
  };
  const trimPages=keep=>{for(const [id,row] of [...pages].sort((a,b)=>a[1].used-b[1].used))if(!keep.has(id)&&pageBytes>8*1048576){pages.delete(id);pageBytes-=row.accounted;}};
  const refreshStats=()=>{
    const m=media.stats(),r=data.stats(),navigation=map.navigation?.stats().bytes??0;Object.assign(stats,{loadedChunks:chunks.size,prototypeCount:models.size+plantModels.size,downloadCacheBytes:downloads.bytes,downloadBytes:downloads.received,
      dataBytes:r.bytes,dataReservedBytes:r.reserved,dataRegions:r.regions,dataPending:r.pending,dataOwners:r.owners,geometryBytes:m.geometryBytes+instanceBytes,textureBytes:m.textureBytes,
      decodedBytes:m.geometryCpuBytes+m.textureBytes+m.transcodeExtraBytes+m.stagingBytes+instanceBytes+pageBytes+farBytes+(coarse?.bytes??0)+r.bytes+navigation,simulationBytes:r.bytes+r.reserved+navigation+(coarse?.bytes??0),coarseBytes:coarse?.bytes??0,stagingBytes:m.stagingBytes,geometryCpuBytes:m.geometryCpuBytes,pageBytes,farBytes,requests:downloads.pool.active,maxRequests:downloads.pool.maximum,queuedRequests:downloads.pool.queue.length,transcodes:m.decoding,maxTranscodes:m.maxDecoding,transcodeTasks:m.transcodeTasks,transcodeHits:m.transcodeHits});
  };
  const ensureBackground=()=>{
    if(background&&(!manifest.water||water))return Promise.resolve(background);
    if(!backgroundPromise)backgroundPromise=(async()=>{
      if(!background){background=await media.load(manifest.background,lifetime.signal);map.root.add(background);}
      if(manifest.water&&!water){water=await media.load(manifest.water,lifetime.signal);map.root.add(water);}
      stats.waterReady=!!water;stats.waterMeshes=water?groundWaterMeshes().length:0;checkBudget();return background;
    })().catch(error=>{backgroundPromise=null;throw error;});
    return backgroundPromise;
  };
  const groundWaterMeshes=()=>{const meshes=[];water?.traverse(o=>{if(o.isMesh&&o.visible&&[o.material].flat().some(m=>m.userData.render_role===5))meshes.push(o);});return meshes;};
  const createBatches=(records,cache=models)=>{
    const groups=new Map();for(const r of records){const mirrored=determinant(r.matrix)<0,key=r.prototype+':'+Number(mirrored);if(!groups.has(key))groups.set(key,{pi:r.prototype,mirrored,records:[]});groups.get(key).records.push(r);}
    for(const group of groups.values()){
      const row=cache.get(group.pi);let root=row.root;
      if(group.mirrored){
        if(!row.mirror){row.mirror=new THREE.Group();row.root.traverse(o=>{if(!o.isMesh)return;const g=o.geometry.clone().scale(-1,1,1);reverse(g);const material=Array.isArray(o.material)?o.material.map(m=>m.clone()):o.material.clone();row.mirror.add(new THREE.Mesh(g,material));});media.register(row.mirror);}
        root=row.mirror;
      }
      root.traverse(o=>{if(!o.isMesh)return;const mesh=new THREE.InstancedMesh(o.geometry,o.material,group.records.length),matrix=new THREE.Matrix4(),reflection=new THREE.Matrix4().makeScale(-1,1,1);
        mesh.customDepthMaterial=depthMaterialFor(o.material);
        group.records.forEach((r,i)=>{matrix.fromArray(r.matrix);if(group.mirrored)matrix.multiply(reflection);mesh.setMatrixAt(i,matrix);});
        mesh.castShadow=mesh.receiveShadow=true;mesh.layers.enable(1);mesh.computeBoundingSphere();map.root.add(mesh);batches.push(mesh);instanceBytes+=mesh.instanceMatrix.array.byteLength;
      });
    }
  };
  const createImpostors=(records,direction)=>{
    const groups=new Map();for(const record of records){let group=groups.get(record.prototype);if(!group){group=[];groups.set(record.prototype,group);}group.push(record);}
    for(const [pi,group] of groups){const descriptor=manifest.prototypes[pi].impostor,row=plantModels.get(pi);
      row.root.traverse(object=>{if(!object.isMesh)return;const geometry=object.geometry.clone(),frames=new Float32Array(group.length*2),mesh=new THREE.InstancedMesh(geometry,object.material,group.length);
        group.forEach((record,i)=>{frames.set(impostorFrame(record,descriptor,direction),i*2);mesh.setMatrixAt(i,impostorMatrix(record,descriptor));});
        geometry.setAttribute('impostorFrame',new THREE.InstancedBufferAttribute(frames,2));mesh.userData.ownedGeometry=true;mesh.userData.impostors={records:group,descriptor};mesh.castShadow=false;mesh.receiveShadow=false;mesh.computeBoundingSphere();map.root.add(mesh);batches.push(mesh);instanceBytes+=mesh.instanceMatrix.array.byteLength+frames.byteLength+group.length*8;
      });
    }
  };
  const updateImpostorDirections=direction=>{
    for(const mesh of batches){const row=mesh.userData.impostors;if(!row)continue;const attribute=mesh.geometry.attributes.impostorFrame;row.records.forEach((record,i)=>attribute.array.set(impostorFrame(record,row.descriptor,direction),i*2));attribute.needsUpdate=true;}
  };
  const createAggregates=records=>{
    if(!records.length)return;const mesh=new THREE.InstancedMesh(aggregate.geometry,aggregate.material,records.length),matrix=new THREE.Matrix4(),color=new THREE.Color();
    records.forEach((record,i)=>{const b=record.bounds;matrix.makeScale(b[3]-b[0],b[4]-b[1],b[5]-b[2]);matrix.setPosition((b[0]+b[3])/2,(b[1]+b[4])/2,(b[2]+b[5])/2);mesh.setMatrixAt(i,matrix);color.setRGB(...(manifest.prototypes[record.prototype].overview_color??[.55,.55,.52]),THREE.SRGBColorSpace);mesh.setColorAt(i,color);});
    mesh.computeBoundingSphere();mesh.castShadow=mesh.receiveShadow=false;map.root.add(mesh);batches.push(mesh);instanceBytes+=mesh.instanceMatrix.array.byteLength+mesh.instanceColor.array.byteLength;
  };
  const sceneLevelFor=(height,span)=>{
    const previous=stats.sceneLevel??'near',hysteresis=manifest.camera?.lod_hysteresis_m??15,mid=manifest.camera?.middle_height_m??100,far=manifest.camera?.far_height_m??800;
    if(span>=(manifest.camera?.far_min_span_m??1290)&&height>=far+(previous==='far'?-hysteresis:hysteresis))return 'far';
    if(span>=(manifest.camera?.middle_min_span_m??180)&&height>=mid+(previous==='middle'?-hysteresis:hysteresis))return 'middle';
    return 'near';
  };
  // 125米区块已有预留边缘。仍覆盖视口时保留已提交画面，避免每16米切回总览。
  const coversView=view=>{
    if(stats.overview||!chunks.size||!view.camera)return false;
    const ray=new THREE.Raycaster(),point=new THREE.Vector3();let minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity;
    for(const x of [-1,0,1])for(const y of [-1,0,1]){
      ray.setFromCamera(new THREE.Vector2(x,y),view.camera);let height=view.center.y??map.coarseHeightAt(view.center.x,view.center.z);
      for(let step=0;step<6;step++){const distance=(height-ray.ray.origin.y)/ray.ray.direction.y;if(!Number.isFinite(distance)||distance<0)return false;ray.ray.at(distance,point);const next=map.coarseHeightAt(point.x,point.z);if(!Number.isFinite(next))break;height=next;}
      minX=Math.min(minX,point.x);maxX=Math.max(maxX,point.x);minZ=Math.min(minZ,point.z);maxZ=Math.max(maxZ,point.z);
    }
    return manifest.chunks.filter(c=>c.file&&c.bounds[0]<maxX&&c.bounds[1]>minX&&c.bounds[2]<maxZ&&c.bounds[3]>minZ).every(c=>chunks.get(c.id)?.root.visible);
  };
  const showOverview=()=>{if(!stats.overview)stats.overviewFallbacks=(stats.overviewFallbacks??0)+1;stats.overview=true;overview.visible=true;stats.visibleChunks=0;for(const row of chunks.values())row.root.visible=false;};
  const replaceCache=(cache,next,release)=>{for(const [key,row]of cache)if(next.get(key)!==row)release(row);cache.clear();for(const [key,row]of next)cache.set(key,row);};
  const setView=async(view)=>{
    if(disposed)return;const ticket=++generation;controller?.abort();controller=new AbortController();const signal=controller.signal;
    if(!coversView(view))showOverview();
    const preparedModels=new Map(),preparedPlants=new Map(),preparedChunks=new Map();
    const prepareModel=async(pi,level)=>{const source=level==='vegetation'?plantModels:models,target=level==='vegetation'?preparedPlants:preparedModels,row=source.get(pi);if(row?.level===level){target.set(pi,row);return row;}return getModel(pi,signal,level,target);};
    const releasePrepared=()=>{for(const [id,row]of preparedChunks)if(chunks.get(id)!==row)media.release(row.root);for(const [id,row]of preparedModels)if(models.get(id)!==row)releaseModel(row);for(const [id,row]of preparedPlants)if(plantModels.get(id)!==row)releaseModel(row);};
    const height=view.height??view.span*.618,level=sceneLevelFor(height,view.span);stats.sceneLevel=level;stats.cameraHeight=height;
    if(view.span>=500){
      data.release('camera');
      if(chunks.size){
        // 跨入总览时先提交粗层，释放已不再显示的近景；两套完整层不同时占用预算。
        showOverview();removeBatches();replaceCache(chunks,new Map(),row=>{media.release(row.root);stats.evicted++;});
        replaceCache(models,new Map(),releaseModel);replaceCache(plantModels,new Map(),releaseModel);pages.clear();pageBytes=0;
        stats.instanceCount=stats.visibleTrees=stats.fullDetailTrees=stats.simplifiedTrees=0;refreshStats();
      }
      if(!manifest.far){removeBatches();replaceCache(models,new Map(),releaseModel);replaceCache(plantModels,new Map(),releaseModel);replaceCache(chunks,new Map(),row=>media.release(row.root));stats.pending=0;refreshStats();return;}
      stats.pending=1;
      try{
        await ensureBackground();if(ticket!==generation||signal.aborted)return;
        if(!farRoot){farRoot=await media.load(manifest.far.surface,signal);map.root.add(farRoot);}
        if(!farRecords){const buffer=await downloads.get(new URL(manifest.far.instances,base).href,manifest.files[manifest.far.instances],signal);if(signal.aborted)return;const page=decodeInstancePage(buffer,{prototypeCount:manifest.prototypes.length,groupCount:manifest.group_count});farRecords=page.records;farBytes=page.bytes+page.count*320;}
        const box={minX:view.center.x-view.span*.75,maxX:view.center.x+view.span*.75,minZ:view.center.z-view.span*.65,maxZ:view.center.z+view.span*.65};
        const allRecords=farRecords.filter(r=>intersects(r.bounds,box)&&(manifest.prototypes[r.prototype].category==='building'||Math.max(r.bounds[3]-r.bounds[0],r.bounds[4]-r.bounds[1],r.bounds[5]-r.bounds[2])/view.span*640>=1.5));
        // 投影完整世界包围盒的八角；原形和派生方盒均在同一屏幕范围内。
        const projected=new THREE.Vector3(),diameter=r=>{if(!view.camera)return Infinity;let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;const b=r.bounds;for(const x of [b[0],b[3]])for(const y of [b[1],b[4]])for(const z of [b[2],b[5]]){projected.set(x,y,z).project(view.camera);if(!Number.isFinite(projected.x)||projected.z< -1||projected.z>1)return Infinity;minX=Math.min(minX,projected.x);maxX=Math.max(maxX,projected.x);minY=Math.min(minY,projected.y);maxY=Math.max(maxY,projected.y);}return Math.hypot((maxX-minX)*renderer.domElement.width/2,(maxY-minY)*renderer.domElement.height/2);};
        const aggregated=[],records=[];stats.aggregateMaxDiameterPixels=0;for(const record of allRecords){const pixels=manifest.far.aggregate_max_diameter_pixels&&manifest.prototypes[record.prototype].category==='building'?diameter(record):Infinity;if(pixels<=manifest.far.aggregate_max_diameter_pixels){aggregated.push(record);stats.aggregateMaxDiameterPixels=Math.max(stats.aggregateMaxDiameterPixels,pixels);}else records.push(record);}
        const lod=level==='near'?'middle':level,required=new Set(records.map(r=>r.prototype));
        for(const pi of required){await prepareModel(pi,lod);if(ticket!==generation||signal.aborted)return;}
        removeBatches();replaceCache(models,preparedModels,releaseModel);replaceCache(plantModels,new Map(),releaseModel);replaceCache(chunks,new Map(),row=>{media.release(row.root);stats.evicted++;});pages.clear();pageBytes=0;
        createBatches(records);createAggregates(aggregated);checkBudget();stats.instanceCount=allRecords.length;stats.aggregateInstances=aggregated.length;stats.visibleTrees=stats.fullDetailTrees=stats.simplifiedTrees=0;stats.pending=0;stats.lastError=null;stats.displayCommits=(stats.displayCommits??0)+1;refreshStats();
      }catch(error){if(ticket===generation&&!signal.aborted){stats.errors.push(String(error));stats.lastError=String(error);stats.pending=0;removeBatches();showOverview();console.error('全图远景准备失败',error);refreshStats();}}finally{releasePrepared();}
      return;
    }
    if(farRoot||farRecords){
      // 返回近景同样先撤下远景层；同层相邻区域仍沿用准备完成后的同步替换。
      showOverview();removeBatches();media.release(farRoot);farRoot=null;farRecords=null;farBytes=0;
      replaceCache(models,new Map(),releaseModel);replaceCache(plantModels,new Map(),releaseModel);
      stats.instanceCount=stats.visibleTrees=stats.fullDetailTrees=stats.simplifiedTrees=0;refreshStats();
    }
    const radiusX=view.span*.95+65,radiusZ=view.span*.80+65,box={minX:view.center.x-radiusX,maxX:view.center.x+radiusX,minZ:view.center.z-radiusZ,maxZ:view.center.z+radiusZ};
    const chosen=manifest.chunks.filter(c=>c.file&&c.bounds[0]<box.maxX&&c.bounds[1]>box.minX&&c.bounds[2]<box.maxZ&&c.bounds[3]>box.minZ)
      .sort((a,b)=>Math.hypot((a.bounds[0]+a.bounds[1])/2-view.center.x,(a.bounds[2]+a.bounds[3])/2-view.center.z)-Math.hypot((b.bounds[0]+b.bounds[1])/2-view.center.x,(b.bounds[2]+b.bounds[3])/2-view.center.z)).slice(0,manifest.budgets.visible_chunks);
    const desired=new Set(chosen.map(c=>c.id));stats.pending=Math.max(1,chosen.filter(c=>!chunks.has(c.id)).length);
    for(const c of chosen)if(chunks.has(c.id))preparedChunks.set(c.id,chunks.get(c.id));
    try{
      const allPages=new Set(chosen.flatMap(c=>c.instance_pages));
      const pageDownloads=Promise.all([...allPages].map(id=>getPage(id,signal))).then(async()=>{
        if(signal.aborted||ticket!==generation)return;const files=new Set();
        for(const id of allPages)for(const r of pages.get(id).records)if(intersects(r.bounds,box)){
          const p=manifest.prototypes[r.prototype],size=Math.max(r.bounds[3]-r.bounds[0],r.bounds[4]-r.bounds[1],r.bounds[5]-r.bounds[2]);
          if(p.category==='tree'){if(p.vegetation_file)files.add(p.vegetation_file);if((r.bounds[4]-r.bounds[1])*640/view.span*.65>=80)files.add(p.file);}
          else if(p.category==='building'||size/view.span*640>=1.5)files.add(level==='near'?p.file:level==='middle'?p.middle_file:p.far_file);
        }
        await Promise.all([...files].filter(Boolean).map(file=>media.prefetch(file,signal)));
      });
      // 下载最大4、解码最大2；过期视区统一取消，任一失败均保留粗地形。
      const settled=await Promise.allSettled([
        data.prepare('camera',box,{signal}),
        ...chosen.filter(c=>!chunks.has(c.id)).map(async c=>{
          const root=await media.load(c.file,signal);if(ticket!==generation||signal.aborted){media.release(root);return;}
          root.visible=false;root.traverse(o=>{if(!o.isMesh)return;o.receiveShadow=true;if(o.material?.userData.render_role===7)o.visible=false;});
          preparedChunks.set(c.id,{root});checkBudget();refreshStats();
        }),
        pageDownloads
      ]);
      const failure=settled.find(r=>r.status==='rejected');if(failure)throw failure.reason;if(ticket!==generation||signal.aborted)return;
      const bounds=manifest.bounds;
      if(box.minX<bounds.minX||box.maxX>bounds.maxX||box.minZ<bounds.minZ||box.maxZ>bounds.maxZ||chosen.some(c=>{const tile=data.entries.get(c.id)?.tile;return tile?.hasWater||tile?.backgroundFallback;}))await ensureBackground();
      if(ticket!==generation||signal.aborted)return;
      const records=[];
      for(const id of allPages)for(const r of pages.get(id).records)if(intersects(r.bounds,box))records.push(r);
      const fixed=[],trees=[];
      for(const r of records){const p=manifest.prototypes[r.prototype],size=Math.max(r.bounds[3]-r.bounds[0],r.bounds[4]-r.bounds[1],r.bounds[5]-r.bounds[2]);
        if(p.category==='tree')trees.push(r);else if(p.category!=='grass'&&(p.category==='building'||size/view.span*640>=1.5))fixed.push(r);
      }
      trees.sort((a,b)=>Math.hypot(a.matrix[12]-view.center.x,a.matrix[14]-view.center.z)-Math.hypot(b.matrix[12]-view.center.x,b.matrix[14]-view.center.z));
      // 工程师已授权植被调整：真实源树位优先近处，限制近景全细节树数量和提交面数。
      const highDetailTrees=manifest.vegetation.native_lod?trees.filter(r=>(r.bounds[4]-r.bounds[1])*640/view.span*.65>=80):trees;
      const selected=[...fixed,...highDetailTrees.slice(0,view.span<180?96:48)],required=new Set(selected.map(r=>r.prototype));
      // 按原型串行解码，限制瞬时几何与纹理峰值；外置贴图请求仍走共享4并发队列。
      const modelResults=await Promise.allSettled([...required].map(pi=>prepareModel(pi,manifest.prototypes[pi].category==='tree'?'near':level))),modelFailure=modelResults.find(r=>r.status==='rejected');if(modelFailure)throw modelFailure.reason;if(ticket!==generation||signal.aborted)return;
      let treeTriangles=0;const rendered=selected.filter(r=>{
        if(manifest.prototypes[r.prototype].category!=='tree')return true;
        const next=preparedModels.get(r.prototype).triangles;if(treeTriangles+next>900000)return false;treeTriangles+=next;return true;
      });
      // 被细节预算降级的树仍有低面冠层与树干表示，避免留下肉眼不可见的碰撞柱。
      const detailedTrees=new Set(rendered.filter(r=>manifest.prototypes[r.prototype].category==='tree').map(r=>r.sourceId));
      const simplified=trees.filter(r=>!detailedTrees.has(r.sourceId));
      if(simplified.length&&manifest.vegetation.native_lod){
        const requiredPlant=new Set(simplified.map(r=>r.prototype));
        const plantResults=await Promise.allSettled([...requiredPlant].map(pi=>prepareModel(pi,'vegetation'))),plantFailure=plantResults.find(r=>r.status==='rejected');if(plantFailure)throw plantFailure.reason;if(ticket!==generation||signal.aborted)return;
      }
      if(ticket!==generation||signal.aborted)return;
      // 全部异步准备结束后，在同一同步片段提交地表、材料和实例，旧引用此时才释放。
      removeBatches();replaceCache(chunks,preparedChunks,row=>{media.release(row.root);stats.evicted++;});replaceCache(models,preparedModels,releaseModel);replaceCache(plantModels,preparedPlants,releaseModel);
      for(const row of chunks.values())if(!row.root.parent)map.root.add(row.root);
      createBatches(rendered);
      if(simplified.length&&manifest.vegetation.native_lod){
        if(manifest.vegetation.native_impostor){
          createImpostors(simplified.filter(record=>manifest.prototypes[record.prototype].impostor),last?.direction??view.direction??new THREE.Vector3(0,165,140).normalize());
          createBatches(simplified.filter(record=>!manifest.prototypes[record.prototype].impostor),plantModels);
        }else createBatches(simplified,plantModels);
      }else if(simplified.length){
        const stemRecords=simplified.filter(r=>manifest.prototypes[r.prototype].vegetation_kind!=='shrub'),crowns=new THREE.InstancedMesh(canopy.geometry,canopy.material,simplified.length),stems=new THREE.InstancedMesh(trunk.geometry,trunk.material,stemRecords.length),matrix=new THREE.Matrix4();
        simplified.forEach((r,i)=>{
          const b=r.bounds,h=b[4]-b[1];matrix.makeScale((b[3]-b[0])*.40,h*.35,(b[5]-b[2])*.40);matrix.setPosition((b[0]+b[3])/2,b[1]+h*.62,(b[2]+b[5])/2);crowns.setMatrixAt(i,matrix);
        });
        stemRecords.forEach((r,i)=>{
          const b=r.bounds,h=b[4]-b[1];
          const descriptor=manifest.vegetation.trunks?.[r.prototype];
          if(descriptor){matrix.fromArray(r.matrix);const local=new THREE.Matrix4().fromArray(descriptor.matrix);matrix.multiply(local);matrix.scale(new THREE.Vector3(descriptor.params[0],descriptor.params[1]*2,descriptor.params[0]));}
          else{matrix.makeScale(.4,Math.min(6,h*.35),.4);matrix.setPosition(r.matrix[12],b[1]+Math.min(6,h*.35)/2,r.matrix[14]);}
          stems.setMatrixAt(i,matrix);
        });
        for(const mesh of [crowns,stems]){mesh.receiveShadow=true;mesh.computeBoundingSphere();map.root.add(mesh);batches.push(mesh);instanceBytes+=mesh.instanceMatrix.array.byteLength;}
      }
      checkBudget();if(ticket!==generation||signal.aborted)return;
      for(const [id,row] of chunks){row.root.visible=desired.has(id);if(background&&manifest.background_layering?.exclusive_source_background)row.root.traverse(mesh=>{if(mesh.isMesh&&mesh.material?.userData.render_role===4)mesh.visible=false;});}
      overview.visible=false;stats.overview=false;stats.lastError=null;stats.visibleChunks=desired.size;stats.instanceCount=rendered.length+simplified.length;stats.visibleTrees=trees.length;stats.fullDetailTrees=detailedTrees.size;stats.simplifiedTrees=simplified.length;stats.pending=0;stats.displayCommits=(stats.displayCommits??0)+1;trimPages(allPages);refreshStats();
    }catch(error){
      if(ticket!==generation||signal.aborted||disposed)return;
      stats.errors.push(String(error));stats.lastError=String(error);stats.pending=0;showOverview();removeBatches();console.error('全图区域加载失败',error);refreshStats();
    }finally{releasePrepared();}
  };
  map.prepareRegion=(owner,bounds,options)=>data.prepare(owner,bounds,options);map.releaseRegion=owner=>data.release(owner);
  map.collisionsIn=bounds=>data.collisions(bounds);
  map.supportSegment=(a,b)=>data.segment(a,b);
  map.querySupport=(x,z,hint)=>data.query(x,z,hint);map.heightAt=(x,z,hint)=>data.query(x,z,hint).height;
  const coarseRay=new THREE.Raycaster();map.coarseHeightAt=(x,z)=>{
    if(coarse)return coarse.at(x,z);
    coarseRay.set(new THREE.Vector3(x,20000,z),new THREE.Vector3(0,-1,0));
    const hits=coarseRay.intersectObjects([...map.groundMeshes,...(background?groundList(background):[])],false);return hits[0]?.point.y;
  };
  map.update=view=>{
    if(disposed)return;const old=last,height=view.height??view.span*.618,camera=view.camera??old?.camera,direction=camera?.getWorldDirection(new THREE.Vector3()).negate()??old?.direction?.clone()??new THREE.Vector3(0,165,140).normalize();
    const rotated=old&&direction.angleTo(old.direction)>.01;
    if(!old||Math.hypot(old.x-view.center.x,old.z-view.center.z)>16||Math.abs(old.span-view.span)>Math.max(12,view.span*.1)||sceneLevelFor(height,view.span)!==stats.sceneLevel||(old.span>=500)!==(view.span>=500)||(old.span>=500&&rotated)){
      last={x:view.center.x,z:view.center.z,span:view.span,height,direction,camera};loading=setView({center:{...view.center},span:view.span,height,direction,camera:camera?.clone()});
    }else if(rotated){updateImpostorDirections(direction);old.direction=direction;old.camera=camera;}
    stats.cameraHeight=height;refreshStats();
  };
  map.retry=()=>{last=null;};map.waitForView=()=>loading;
  map.compact=()=>{data.trimUnreferenced();pages.clear();pageBytes=0;downloads.clear();refreshStats();};
  map.visibleGroundMeshes=()=>[...(stats.overview?map.groundMeshes:[...chunks.values()].flatMap(row=>row.root.visible?groundList(row.root):[])),...(background?groundList(background):[])];
  map.visibleWaterMeshes=groundWaterMeshes;
  map.dispose=()=>{
    if(disposed)return;disposed=true;generation++;controller?.abort();lifetime.abort();data.dispose();removeBatches();
    for(const row of chunks.values())media.release(row.root);chunks.clear();for(const row of models.values())releaseModel(row);models.clear();for(const row of plantModels.values())releaseModel(row);plantModels.clear();
    media.release(overview);media.release(background);media.release(water);media.release(proxyRoot);media.release(farRoot);farRecords=null;coarse=null;map.groundMeshes=[];media.dispose();pages.clear();downloads.clear();map.root.removeFromParent();map.root.clear();
  };
  if(options.signal){const dispose=map.dispose,onAbort=()=>dispose();map.dispose=()=>{options.signal.removeEventListener('abort',onAbort);dispose();};if(options.signal.aborted){map.dispose();throw new DOMException('场景加载已取消','AbortError');}options.signal.addEventListener('abort',onAbort,{once:true});}
  options.onCreate?.(map);
  try{
    progress('准备本地纹理解码器…');await media.initialize();stats.startupTimings.decoderReady=performance.now()-startup;
    if(manifest.coarse_height)coarse=new CoarseHeight(await downloads.get(new URL(manifest.coarse_height.file,base).href,manifest.files[manifest.coarse_height.file],lifetime.signal));
    stats.startupTimings.coarseReady=performance.now()-startup;progress('加载全图总览…');
    overview=await media.load(manifest.overview,lifetime.signal);map.root.add(overview);map.groundMeshes=groundList(overview);
    stats.startupTimings.overviewReady=performance.now()-startup;
    checkBudget();progress('准备默认区域…');map.update({center:manifest.center,span:manifest.initial_view_span??manifest.defaultSpan});await loading;
    stats.startupTimings.detailReady=performance.now()-startup;
    map.root.updateMatrixWorld(true);progress('全图显示候选已就绪');return map;
  }catch(error){map.dispose();throw error;}
}
