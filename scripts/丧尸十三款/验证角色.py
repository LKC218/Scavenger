"""双浏览器无窗口技术检查与多视角证据，不提供前台FPS结论。"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'scripts/资源库'),str(ROOT/'scripts/断箭'),str(ROOT/'scripts')]
from 统一资源 import current,read,record_path,sha,save

JS=r'''async item=>{
 const T=await import('three'),{createModelLoader,disposeModelLoader}=await import('/compressed-loader.mjs');
 const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setSize(420,420);renderer.setPixelRatio(1);renderer.outputColorSpace=T.SRGBColorSpace;
 document.body.appendChild(renderer.domElement);
 const scene=new T.Scene();scene.background=new T.Color('#171e25');scene.add(new T.HemisphereLight(0xe7f3ff,0x7d6c56,2.4));const light=new T.DirectionalLight(0xffffff,3);light.position.set(3,5,6);scene.add(light);
 const camera=new T.PerspectiveCamera(35,1,.01,100);camera.position.set(3,2.5,4);camera.lookAt(0,.9,0);
 const loader=createModelLoader(renderer),g=await loader.loadAsync(item.url);let mesh;g.scene.traverse(o=>{if(o.isSkinnedMesh)mesh=o;});
 if(!mesh||mesh.skeleton.bones.length!==item.bones||g.animations.length!==2)throw Error('骨架或动作数量不符');
 const reference=item.reference_url?await loader.loadAsync(item.reference_url):null;let refMesh;reference?.scene.traverse(o=>{if(o.isSkinnedMesh)refMesh=o;});
 scene.add(g.scene);const mixer=new T.AnimationMixer(g.scene),rm=reference?new T.AnimationMixer(reference.scene):null;
 const verts=()=>{g.scene.updateMatrixWorld(true);mesh.skeleton.update();const v=new T.Vector3();return Array.from({length:mesh.geometry.attributes.position.count},(_,i)=>mesh.getVertexPosition(i,v).clone().applyMatrix4(mesh.matrixWorld));};
 const refverts=()=>{reference.scene.updateMatrixWorld(true);refMesh.skeleton.update();const v=new T.Vector3();return Array.from({length:refMesh.geometry.attributes.position.count},(_,i)=>refMesh.getVertexPosition(i,v).clone().applyMatrix4(refMesh.matrixWorld));};
 const clips=[];let poseError=0;
 for(const clip of g.animations){
  mixer.stopAllAction();const action=mixer.clipAction(clip).setLoop(T.LoopOnce,1);action.clampWhenFinished=true;action.reset().play();
  if(rm){rm.stopAllAction();const ra=rm.clipAction(reference.animations.find(c=>c.name===clip.name)).setLoop(T.LoopOnce,1);ra.clampWhenFinished=true;ra.reset().play();}
  let first,last,maxChange=0;
  for(const factor of [0,.125,.25,.5,.75,1]){
   mixer.setTime(clip.duration*factor);rm?.setTime(clip.duration*factor);const vs=verts();
   if(vs.some(v=>!Number.isFinite(v.x+v.y+v.z)))throw Error('非有限蒙皮顶点');
   if(!first)first=vs;else maxChange=Math.max(maxChange,...vs.map((v,i)=>v.distanceTo(first[i])));last=vs;
   if(rm){const ref=refverts();poseError=Math.max(poseError,...vs.map((v,i)=>v.distanceTo(ref[i])));}
  }
  const seam=Math.max(...first.map((v,i)=>v.distanceTo(last[i])));if(seam>1e-4||maxChange<.005)throw Error('真实顶点变化或循环失败');
  clips.push({name:clip.name,duration_s:clip.duration,loop_max_error_m:seam,pose_max_change_m:maxChange});
 }
 if(poseError>1e-4)throw Error('派生姿态与制作GLB不一致：'+poseError);
 mixer.stopAllAction();const idle=mixer.clipAction(g.animations.find(c=>c.name==='待机')).setLoop(T.LoopRepeat,Infinity).reset().play(),walk=mixer.clipAction(g.animations.find(c=>c.name==='移动')).setLoop(T.LoopRepeat,Infinity).reset().play();
 for(let i=0;i<=24;i++){walk.setEffectiveWeight(i/24);idle.setEffectiveWeight(1-i/24);mixer.update(1/60);verts();}for(let i=0;i<=24;i++){walk.setEffectiveWeight(1-i/24);idle.setEffectiveWeight(i/24);mixer.update(1/60);verts();}
 mixer.stopAllAction();mixer.clipAction(g.animations.find(c=>c.name==='移动')).setLoop(T.LoopRepeat,Infinity).setEffectiveWeight(1).reset().play();mixer.setTime(.3125);g.scene.updateMatrixWorld(true);
 const box=new T.Box3().setFromObject(g.scene),center=box.getCenter(new T.Vector3());const views=[];
 for(const [name,angle] of [['正面',0],['侧面',Math.PI/2],['背面',Math.PI]]){camera.position.set(center.x+4*Math.sin(angle),center.y+.2,center.z+4*Math.cos(angle));camera.lookAt(center);renderer.render(scene,camera);views.push({name,png:renderer.domElement.toDataURL('image/png')});}
 mixer.stopAllAction();mixer.clipAction(g.animations.find(c=>c.name==='待机')).setLoop(T.LoopRepeat,Infinity).setEffectiveWeight(1).reset().play();mixer.setTime(.5);renderer.render(scene,camera);views.push({name:'待机',png:renderer.domElement.toDataURL('image/png')});
 const before={...renderer.info.memory};
 const dispose=root=>{const textures=new Set(),materials=new Set(),geometries=new Set();root.traverse(o=>{if(o.isSkinnedMesh)o.skeleton.dispose();if(o.geometry)geometries.add(o.geometry);for(const m of Array.isArray(o.material)?o.material:[o.material])if(m){materials.add(m);for(const v of Object.values(m))if(v?.isTexture)textures.add(v);}});for(const t of textures)t.dispose();for(const m of materials)m.dispose();for(const g of geometries)g.dispose();};
 mixer.stopAllAction();mixer.uncacheRoot(g.scene);rm?.stopAllAction();rm?.uncacheRoot(reference.scene);scene.remove(g.scene);dispose(g.scene);if(reference)dispose(reference.scene);renderer.render(scene,camera);const after={...renderer.info.memory};
 disposeModelLoader(renderer);renderer.dispose();const disposed={...renderer.info.memory};renderer.domElement.remove();
 if(disposed.geometries!==0||disposed.textures!==0)throw Error('资源未释放：'+JSON.stringify({after,disposed}));
 return {id:item.id,resolution:item.resolution,sha256:item.sha256,bones:mesh.skeleton.bones.length,vertices:mesh.geometry.attributes.position.count,clips,pose_error_m:poseError,transition_passed:true,release:{before,after,disposed},views,technical_passed:true};
}'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--only',type=int);p.add_argument('--label',default='浏览器验证');a=p.parse_args();batch=a.batch.resolve()
    spec=importlib.util.spec_from_file_location('zombie_qa_library',ROOT/'scripts/断箭/启动正式资料库.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    server=module.create_server(0)
    html=batch/'核验页.html';html.write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>十三款资源核验</title><script type="importmap">{"imports":{"three":"/vendor/build/three.module.js","three/addons/":"/vendor/examples/jsm/"}}</script><body></body></html>','utf8');server.static['/qa']=html
    items=[];_,catalog,routes,_=current();zero=next(m for m in catalog['models'] if m['id']=='model:zombie-runtime')
    if a.only in (None,0):
        for v in zero['runtime_variants']:
            items.append({'id':zero['id'],'index':0,'bones':23,'url':v['url'],**{k:v[k] for k in ['sha256','resolution']}})
    for i in range(1,13):
        if a.only is not None and i!=a.only:continue
        folder=batch/f'角色{i:02}';job=read(folder/'制作回执.json');derived=read(folder/'派生回执.json')
        for row in [job['asset'],*derived['variants']]:
            file=Path(row['path']);assert sha(file)==row['sha256'];server.routes['/stage/'+row['sha256']+'.glb']={'path':str(file),'sha256':row['sha256'],'bytes':file.stat().st_size}
        for v in derived['variants']:
            items.append({'id':job['id'],'index':i,'bones':job['bones'],'url':'/stage/'+v['sha256']+'.glb','reference_url':'/stage/'+job['asset']['sha256']+'.glb',**{k:v[k] for k in ['sha256','resolution']}})
    threading.Thread(target=server.serve_forever,daemon=True).start()
    result={'headless':True,'purpose':'技术与图像证据，不是硬件性能验收','browsers':[]}
    import base64
    try:
        with sync_playwright() as pw:
            for channel in ['chrome','msedge']:
                browser=pw.chromium.launch(channel=channel,headless=True);page=browser.new_page();page.goto(f'http://127.0.0.1:{server.server_port}/qa');errors=[];page.on('pageerror',lambda e:errors.append(str(e)));rows=[]
                for item in items:
                    row=page.evaluate(JS,item)
                    views=batch/a.label/channel/f"角色{item['index']:02}-{item['resolution']}";views.mkdir(parents=True,exist_ok=False)
                    for v in row['views']:
                        file=views/(v['name']+'.png');file.write_bytes(base64.b64decode(v.pop('png').split(',',1)[1]));v.update(path=str(file),sha256=sha(file))
                    save(views/'核验.json',row);rows.append(row);print(channel,item['id'],item['resolution'],'通过',flush=True)
                assert not errors,errors
                result['browsers'].append({'channel':channel,'version':browser.version,'roles':rows,'errors':errors,'passed':True});browser.close();save(batch/(a.label+'核验.json'),result)
        result['passed']=True;save(batch/(a.label+'核验.json'),result)
    finally:
        server.shutdown();server.server_close()


if __name__=='__main__':main()
