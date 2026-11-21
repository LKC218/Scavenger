"""比较GPU姿态与原骨骼计算，核对机炮PCM及真实触发；独立浏览器运行。"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

OUT=Path(__file__).resolve().parents[1]/'local-data/原型/丧尸防线/V003-01a1a2af/整合回归'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

POSE_CHECK=r'''async()=>{
 const T=await import('/vendor/build/three.module.js'),{clone}=await import('/vendor/examples/jsm/utils/SkeletonUtils.js');
 const results=[];
 for(const rig of range.view.actors.rigs){
  const root=clone(rig.source.asset.scene);let mesh;root.traverse(o=>{if(o.isSkinnedMesh)mesh=o});const mixer=new T.AnimationMixer(root);let maximum=0,exact=0,blendMax=0;
  const bone=(i,phase,offset)=>{const f=phase*64,a=Math.floor(f)%64,b=(a+1)%64;const x=new T.Matrix4().fromArray(rig.values,((offset+a)*rig.width+i*4)*4),y=new T.Matrix4().fromArray(rig.values,((offset+b)*rig.width+i*4)*4);for(let k=0;k<16;k++)x.elements[k]=x.elements[k]*(1-(f-Math.floor(f)))+y.elements[k]*(f-Math.floor(f));return x;};
  const positions=mesh.geometry.attributes.position,indices=mesh.geometry.attributes.skinIndex,weights=mesh.geometry.attributes.skinWeight;
  for(const [phase,blend] of [[0,0],[13/64,1],[31/64,0],[.371,1],[.713,0],[.371,.5]]){
   mixer.stopAllAction();for(const [name,weight] of [['移动',blend],['待机',1-blend]]){const clip=rig.source.asset.animations.find(c=>c.name===name),action=mixer.clipAction(clip);action.reset().play();action.time=phase*clip.duration;action.setEffectiveWeight(weight);}mixer.update(0);root.updateMatrixWorld(true);
   for(const i of [0,Math.floor(positions.count*.19),Math.floor(positions.count*.49),Math.floor(positions.count*.79),positions.count-1]){
    const original=new T.Vector3();mesh.getVertexPosition(i,original);const input=new T.Vector3().fromBufferAttribute(positions,i).applyMatrix4(rig.source.base.bindMatrix),v=new T.Vector3();
    for(let c=0;c<4;c++){const id=indices.getComponent(i,c),w=weights.getComponent(i,c);if(!w)continue;const idle=bone(id,phase,64),walk=bone(id,phase,0);for(let k=0;k<16;k++)idle.elements[k]=idle.elements[k]*(1-blend)+walk.elements[k]*blend;v.addScaledVector(input.clone().applyMatrix4(idle),w);}v.applyMatrix4(rig.source.base.bindMatrixInverse);
    original.applyMatrix4(mesh.matrixWorld);v.applyMatrix4(rig.meshMatrix);const error=v.distanceTo(original);maximum=Math.max(maximum,error);if(blend===.5)blendMax=Math.max(blendMax,error);else if(Number.isInteger(phase*64))exact=Math.max(exact,error);
   }
  }
  mixer.stopAllAction();mixer.uncacheRoot(root);mesh.skeleton.dispose();results.push({id:rig.source.model.id,maximum,exact,blendMax});
 }
 return results;
}'''

with sync_playwright() as pw:
    OUT.mkdir(parents=True,exist_ok=False);browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1600,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));request_ports=set();page.on('request',lambda r:request_ports.add(urlsplit(r.url).port) if urlsplit(r.url).scheme in ('http','https') else None)
    page.goto('http://127.0.0.1:8794/');page.wait_for_function('window.range?.ready',timeout=60000)
    manifest=page.evaluate('range.manifest');assert manifest['version']=='V003' and manifest['frozen_source']
    identity=page.evaluate('range.manifest.source_identity');frozen=page.evaluate('range.manifest.frozen_source')
    pose=page.evaluate(POSE_CHECK)
    (OUT/'姿态对照.json').write_text(json.dumps(pose,ensure_ascii=False,indent=2),encoding='utf-8')
    assert max(r['exact'] for r in pose)<.0001,pose
    assert max(r['maximum'] for r in pose)<.01,pose
    page.locator('#defense-start').click();page.wait_for_function('(range.mixer.heliShotAlignment?.starts??0)>=10',timeout=45000)
    sound=page.evaluate('''()=>{const a=range.mixer.heliShotAlignment,r=range.mixer.runtime,source=r.cache.get(a.sourceId).buffer,derived=r.cache.get(range.mixer.heliShotAsset.audio_id).buffer,offset=Math.round(a.leadingTrimSeconds*source.sampleRate);let changed=0;for(let c=0;c<source.numberOfChannels;c++){const x=source.getChannelData(c),y=derived.getChannelData(c);for(let i=0;i<y.length;i++)if(y[i]!==x[offset+i])changed++;}return{...a,changedSamples:changed,stats:range.mixer.stats()}}''')
    assert sound['changedSamples']==0 and sound['starts']>=10 and sound['voiceSteals']>0
    assert sound['maxVoicesPerUnit']<=2 and sound['starts']==sound['attempts']
    assert sound['leadingTrimSeconds']>.15 and sound['leadingTrimSeconds']<.3
    page.locator('#pause').click();page.wait_for_function('range.mixer.stats().voices===0')
    sound['pause_released']=True
    page.locator('#pause').click();page.screenshot(path=str(OUT/'V003交战.png'))
    # 当前GPU姿态仍与原骨骼计算比较；历史截图经归档映射读取，不再启动旧版。
    page.evaluate('range.view.center.set(-50,0,-65);range.view.span=58;range.view.targetSpan=58;range.view.centerTarget=null;range.view.updateCamera()')
    page.wait_for_timeout(150);page.locator('#canvas-host canvas').screenshot(path=str(OUT/'V003-当前姿态.png'))
    assert request_ports=={8794},request_ports
    result={'passed':not errors,'errors':errors,'source_identity':identity,'source_sha256':manifest['source_sha256'],'version':manifest['version'],'frozen_source':frozen,'request_ports':sorted(request_ports),'pose':pose,'sound':sound,'note':'GPU烘焙关键帧、帧间插值和片段混合均与原SkinnedMesh实际顶点计算比较；声音为PCM与调度技术核验，不等同人工听审；历史资料按V003历史资料归档映射追溯'}
    (OUT/'合批与机炮验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');assert not errors
    print(json.dumps({'passed':True,'output':OUT.as_posix(),'request_ports':sorted(request_ports),'models':len(pose),'maxPoseError':max(r['maximum'] for r in pose),'maxExactError':max(r['exact'] for r in pose),'sound':{k:sound[k] for k in ['leadingTrimSeconds','trailingTrimSeconds','duration','starts','voiceSteals','changedSamples']}},ensure_ascii=False));browser.close()
