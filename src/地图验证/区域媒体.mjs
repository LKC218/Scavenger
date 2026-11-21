// 下载统一排队并核对摘要；GLB外置依赖先取回，解析阶段只读取本地Blob。
import * as THREE from 'three';
import {createModelLoader} from '/compressed-loader.mjs';
import {RequestPool} from './区域数据.mjs';
import {geometryAllocations} from './资源字节.mjs';

const textureBytes=t=>{
  if(t.isCompressedTexture)return t.mipmaps.reduce((sum,mip)=>sum+(mip.data?.byteLength??0),0);
  const image=t.source?.data??t.image;return (image?.width??0)*(image?.height??0)*4*4/3;
};

export class RegionMedia {
  constructor(renderer,manifest,base,downloads){
    Object.assign(this,{renderer,manifest,base,downloads});this.loader=createModelLoader(renderer);this.decodePool=new RequestPool(2);
    this.textures=new Map();this.transcoded=new Map();this.graphs=new WeakMap();this.gpuAttributes=new Map();this.cpuBuffers=new Map();this.geometryBytes=0;this.geometryCpuBytes=0;this.textureBytes=0;this.stagingBytes=0;this.graphCount=0;this.disposed=false;this.transcodeTasks=0;this.transcodeHits=0;
    // 本实例用有界DownloadStore；不采用整页无限文件缓存。
    THREE.Cache.enabled=false;
  }
  async initialize(){await this.loader.ktx2Loader?.init();}
  async prefetch(name,signal){
    const url=new URL(name,this.base),buffer=await this.downloads.get(url.href,this.manifest.files[name],signal),view=new DataView(buffer);
    if(buffer.byteLength<20||view.getUint32(0,true)!==0x46546c67)throw new Error('预取GLB身份错误');
    const doc=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,20,view.getUint32(12,true))));
    await Promise.all((doc.images??[]).filter(image=>image.uri).map(image=>{const dependency=new URL(image.uri,url),relative=decodeURI(dependency.href.slice(this.base.href.length));if(!dependency.href.startsWith(this.base.href)||!this.manifest.files[relative])throw new Error('预取外置依赖未登记');return this.downloads.get(dependency.href,this.manifest.files[relative],signal);}));
  }
  async load(name,signal){
    if(this.disposed)throw new DOMException('地图已释放','AbortError');
    const url=new URL(name,this.base),buffer=await this.downloads.get(url.href,this.manifest.files[name],signal);
    const view=new DataView(buffer);
    if(view.getUint32(0,true)!==0x46546c67||view.getUint32(4,true)!==2||view.getUint32(8,true)!==buffer.byteLength||view.getUint32(16,true)!==0x4e4f534a)throw new Error('GLB结构或长度错误: '+name);
    const jsonLength=view.getUint32(12,true),doc=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,20,jsonLength)));
    const blobs=[],usage=new Set();let stagingBytes=0;
    try{
      for(const image of doc.images??[]){
        if(!image.uri)continue;
        const dependency=new URL(image.uri,url),relative=decodeURI(dependency.href.slice(this.base.href.length));
        if(!dependency.href.startsWith(this.base.href)||!this.manifest.files[relative])throw new Error('GLB存在未登记外置依赖: '+image.uri);
        const pixels=await this.downloads.get(dependency.href,this.manifest.files[relative],signal);
        stagingBytes+=pixels.byteLength;this.stagingBytes+=pixels.byteLength;
        const blob=URL.createObjectURL(new Blob([pixels],{type:image.mimeType??'application/octet-stream'}));blobs.push(blob);image.uri=blob;
      }
      if((doc.buffers??[]).some(b=>b.uri))throw new Error('当前场景不接受外置几何缓冲');
      const text=new TextEncoder().encode(JSON.stringify(doc)),length=(text.byteLength+3)&~3,tail=new Uint8Array(buffer,20+jsonLength),rebuilt=new Uint8Array(20+length+tail.byteLength);
      stagingBytes+=rebuilt.byteLength;this.stagingBytes+=rebuilt.byteLength;
      rebuilt.fill(32,20,20+length);rebuilt.set(new Uint8Array(buffer,0,20));rebuilt.set(text,20);rebuilt.set(tail,20+length);
      const header=new DataView(rebuilt.buffer);header.setUint32(8,rebuilt.byteLength,true);header.setUint32(12,length,true);
      const loader=createModelLoader(this.renderer);
      // 固定Three.js 0.187.0的GLTFParser.loadTextureImage由Basisu和普通纹理路径共同调用。
      // 相同图像和采样器复用正在进行的转码，不先重复转码再只合并GPU对象。
      loader.register(parser=>{
        const original=parser.loadTextureImage.bind(parser);
        parser.loadTextureImage=(textureIndex,sourceIndex,imageLoader)=>{
          const def=parser.json.textures[textureIndex],image=parser.json.images[sourceIndex],sampler=(parser.json.samplers??[])[def.sampler]??{};
          const key=[image.name,JSON.stringify(sampler)].join('|');if(!image.name)return original(textureIndex,sourceIndex,imageLoader);
          let row=this.transcoded.get(key);
          if(!row){
            row={refs:0,users:0,texture:null};this.transcoded.set(key,row);this.transcodeTasks++;
            row.promise=original(textureIndex,sourceIndex,imageLoader).then(texture=>{if(!texture)throw new Error('必要纹理未能转码');texture.userData.regionTextureKey=key;row.texture=texture;this.trimTranscode(key,row);return texture;}).catch(error=>{this.transcoded.delete(key);throw error;});
          }else this.transcodeHits++;
          if(!usage.has(key)){usage.add(key);row.users++;}
          return row.promise.then(texture=>{parser.associations.set(texture,{textures:textureIndex});return texture;});
        };
        return {name:'AirbaseRegionTextureReuse'};
      });
      const gltf=await this.decodePool.run(()=>loader.parseAsync(rebuilt.buffer,url.href),signal);
      if(signal?.aborted||this.disposed){this.destroyUntracked(gltf.scene);throw new DOMException('过期场景资源','AbortError');}
      this.register(gltf.scene);return gltf.scene;
    }finally{
      this.stagingBytes-=stagingBytes;
      for(const blob of blobs)URL.revokeObjectURL(blob);
      for(const key of usage){const row=this.transcoded.get(key);if(row){row.users--;this.trimTranscode(key,row);}}
    }
  }
  trimTranscode(key,row){if(!row.refs&&!row.users&&row.texture){row.texture.dispose();row.texture.source?.data?.close?.();this.transcoded.delete(key);}}
  destroyUntracked(root){
    const geometries=new Set(),materials=new Set(),textures=new Set();root.traverse(o=>{if(o.geometry)geometries.add(o.geometry);for(const m of [o.material].flat().filter(Boolean)){materials.add(m);for(const t of Object.values(m))if(t?.isTexture)textures.add(t);}});
    for(const g of geometries)g.dispose();for(const m of materials)m.dispose();for(const t of textures)if(!t.userData.regionTextureKey){t.dispose();t.source?.data?.close?.();}
  }
  register(root){
    const geometries=new Set(),materials=new Set(),keys=new Set(),transcodes=new Set();
    root.traverse(o=>{
      if(o.geometry)geometries.add(o.geometry);
      for(const material of [o.material].flat().filter(Boolean)){
        materials.add(material);
        for(const [slot,t] of Object.entries(material))if(t?.isTexture){
          const sourceKey=t.userData.regionTextureKey;if(sourceKey&&!transcodes.has(sourceKey)){const row=this.transcoded.get(sourceKey);if(row){row.refs++;transcodes.add(sourceKey);}}
          const key=[t.name,t.colorSpace,t.channel,t.wrapS,t.wrapT,t.repeat.x,t.repeat.y,t.offset.x,t.offset.y,t.rotation,t.center.x,t.center.y,t.magFilter,t.minFilter,t.flipY,t.premultiplyAlpha].join('|');
          let shared=this.textures.get(key);
          if(shared&&shared.texture!==t){material[slot]=shared.texture;t.dispose();t.source?.data?.close?.();}
          else if(!shared){shared={texture:t,refs:0,bytes:textureBytes(t)};this.textures.set(key,shared);this.textureBytes+=shared.bytes;}
          if(!keys.has(key)){keys.add(key);shared.refs++;}
        }
      }
    });
    const allocation=geometryAllocations(geometries);
    for(const [object,bytes] of allocation.gpu){let row=this.gpuAttributes.get(object);if(!row){row={refs:0,bytes};this.gpuAttributes.set(object,row);this.geometryBytes+=bytes;}row.refs++;}
    for(const [buffer,bytes] of allocation.cpu){let row=this.cpuBuffers.get(buffer);if(!row){row={refs:0,bytes};this.cpuBuffers.set(buffer,row);this.geometryCpuBytes+=bytes;}row.refs++;}
    this.graphCount++;this.graphs.set(root,{geometries,materials,keys,transcodes,allocation});
  }
  release(root){
    const resources=this.graphs.get(root);if(!resources)return;root.removeFromParent();
    for(const g of resources.geometries)g.dispose();for(const m of resources.materials)m.dispose();
    for(const key of resources.keys){const row=this.textures.get(key);if(!--row.refs){row.texture.dispose();row.texture.source?.data?.close?.();this.textureBytes-=row.bytes;this.textures.delete(key);}}
    for(const key of resources.transcodes){const row=this.transcoded.get(key);if(row){row.refs--;this.trimTranscode(key,row);}}
    for(const [object] of resources.allocation.gpu){const row=this.gpuAttributes.get(object);if(!--row.refs){this.geometryBytes-=row.bytes;this.gpuAttributes.delete(object);}}
    for(const [buffer] of resources.allocation.cpu){const row=this.cpuBuffers.get(buffer);if(!--row.refs){this.geometryCpuBytes-=row.bytes;this.cpuBuffers.delete(buffer);}}
    this.graphCount--;this.graphs.delete(root);
  }
  stats(){const extra=[...this.transcoded.values()].filter(r=>!r.refs&&r.texture).reduce((sum,r)=>sum+textureBytes(r.texture),0);return {geometryBytes:this.geometryBytes,geometryCpuBytes:this.geometryCpuBytes,textureBytes:this.textureBytes,stagingBytes:this.stagingBytes,transcodeExtraBytes:extra,transcodeTasks:this.transcodeTasks,transcodeHits:this.transcodeHits,textures:this.textures.size,graphs:this.graphCount,decoding:this.decodePool.active,maxDecoding:this.decodePool.maximum};}
  dispose(){this.disposed=true;}
}
