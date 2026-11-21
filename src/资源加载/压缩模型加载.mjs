// 同一渲染器共享转码器，兼容原版及KTX2、Meshopt派生模型。
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {KTX2Loader} from 'three/addons/loaders/KTX2Loader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';
const transcoders=new WeakMap();
export function createModelLoader(renderer){
 let ktx=transcoders.get(renderer);
 if(!ktx){ktx=new KTX2Loader().setTranscoderPath('/vendor/examples/jsm/libs/basis/').setWorkerLimit(2).detectSupport(renderer);transcoders.set(renderer,ktx);}
 return new GLTFLoader().setKTX2Loader(ktx).setMeshoptDecoder(MeshoptDecoder);
}
export function disposeModelLoader(renderer){transcoders.get(renderer)?.dispose();transcoders.delete(renderer);}
