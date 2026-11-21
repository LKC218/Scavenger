// 仅用于探索浏览器：追踪真实WebGL纹理，以及上传时的材料和释放调用栈。
(()=>{
  let next=0,scope=null;
  const live=new Map(),ids=new WeakMap(),disposals=new Map();
  const textureInfo=texture=>({name:texture.name,uuid:texture.uuid,source:texture.source?.uuid,key:texture.userData.regionTextureKey});
  for(const kind of ['WebGLRenderingContext','WebGL2RenderingContext']){
    const proto=window[kind]?.prototype;if(!proto)continue;
    const create=proto.createTexture,remove=proto.deleteTexture;
    proto.createTexture=function(...args){
      const texture=create.apply(this,args),row={id:++next,stack:new Error().stack,scope,
        seconds:performance.now()/1000,center:window.range?{x:range.view.center.x,z:range.view.center.z}:null};
      ids.set(texture,row.id);live.set(texture,row);return texture;
    };
    proto.deleteTexture=function(texture){live.delete(texture);return remove.call(this,texture);};
  }
  window.installTextureMaterialDiagnostic=async()=>{
    const THREE=await import('three'),renderer=range.view.renderer;
    const dispose=THREE.Texture.prototype.dispose,render=renderer.renderBufferDirect;
    THREE.Texture.prototype.dispose=function(...args){
      disposals.set(this.uuid,{...textureInfo(this),seconds:performance.now()/1000,stack:new Error().stack});
      return dispose.apply(this,args);
    };
    renderer.renderBufferDirect=function(...args){
      const material=args[3],object=args[4],previous=scope;
      scope={material:material.name,materialType:material.type,object:object.name,
        originalMaterials:[object.material].flat().map(m=>({name:m?.name,uuid:m?.uuid})),
        textures:Object.entries(material).filter(([,t])=>t?.isTexture).map(([slot,t])=>({slot,...textureInfo(t)}))};
      try{return render.apply(this,args);}finally{scope=previous;}
    };
    window.textureDiagnostic=()=>{
      const owned=new Map();
      const add=(texture,owner)=>{
        if(!texture?.isTexture)return;
        const id=ids.get(renderer.properties.get(texture).__webglTexture);if(!id)return;
        let row=owned.get(id);if(!row){row=[];owned.set(id,row);}row.push({owner,...textureInfo(texture)});
      };
      range.view.scene.traverse(object=>{for(const material of [object.material].flat().filter(Boolean))for(const [slot,t]of Object.entries(material))add(t,object.type+':'+slot);});
      for(const [key,row]of range.externalMap.media.textures)add(row.texture,'media:'+key);
      for(const [key,row]of range.externalMap.media.transcoded)add(row.texture,'transcode:'+key);
      for(const object of range.view.scene.children)if(object.shadow?.map){add(object.shadow.map.texture,'shadow');add(object.shadow.map.depthTexture,'shadowDepth');}
      return {rendererCount:renderer.info.memory.textures,tracked:range.externalMap.media.stats(),
        live:[...live.values()].map(row=>({...row,owners:owned.get(row.id)??[],
          disposals:(row.scope?.textures??[]).map(texture=>disposals.get(texture.uuid)).filter(Boolean)}))};
    };
  };
})();
