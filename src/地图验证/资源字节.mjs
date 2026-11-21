// CPU按实际ArrayBuffer计一次；GPU按Three.js实际上传的属性对象及视图长度计一次。
export function geometryAllocations(geometries){
 const gpu=new Map(),cpu=new Map();
 for(const geometry of geometries)for(const attribute of [...Object.values(geometry.attributes),geometry.index].filter(Boolean)){
  const upload=attribute.isInterleavedBufferAttribute?attribute.data:attribute,array=upload.array;
  if(!array?.buffer)continue;gpu.set(upload,array.byteLength);cpu.set(array.buffer,array.buffer.byteLength);
 }
 return {gpu,cpu};
}
