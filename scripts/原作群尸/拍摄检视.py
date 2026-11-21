"""拍摄真实GPU运行网格的双角色姿态夹具及原作防线，不参与性能计数。"""
import json,time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48/画面检视'
def main():
    target=OUT/str(time.time_ns());target.mkdir(parents=True,exist_ok=False);errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:8797/?family=native&headless=1');page.wait_for_function('window.crowdBench?.ready',timeout=90000)
        await_setup=r'''async()=>{const T=await import('/vendor/build/three.module.js'),actors=crowdBench.actors(),view=crowdBench.view();actors.group.visible=false;view.shadows.visible=false;view.center.set(0,0,0);view.targetCenter=null;view.span=view.targetSpan=5;view.updateCamera(0);window.poseFixtures=[];for(let i=0;i<actors.rigs.length;i++){const rig=actors.rigs[i],mesh=rig.live.mesh.clone();mesh.geometry=rig.live.mesh.geometry.clone();mesh.count=1;const obj=new T.Object3D();obj.position.set(i?1:-1,0,0);obj.updateMatrix();mesh.setMatrixAt(0,new T.Matrix4().multiplyMatrices(obj.matrix,rig.meshMatrix));mesh.instanceMatrix.needsUpdate=true;mesh.setColorAt(0,new T.Color(1,1,1));mesh.instanceColor.needsUpdate=true;view.scene.add(mesh);poseFixtures.push({mesh,rig});}return true;}'''
        page.evaluate(await_setup)
        for state,fraction in [('idle',.35),('move',.37),('attack',.45),('death',.9)]:
            page.evaluate('''({state,fraction})=>{for(const{mesh,rig}of poseFixtures){const clip=rig.clips[rig.model.states[state]],f=fraction*(clip.frames-1),a=Math.floor(f),b=Math.min(a+1,clip.frames-1);mesh.geometry.attributes.actorPoseA.setXYZW(0,clip.offset+a,clip.offset+b,f-a,1);mesh.geometry.attributes.actorPoseB.setXYZW(0,clip.offset+a,clip.offset+b,f-a,1);mesh.geometry.attributes.actorPoseA.needsUpdate=true;mesh.geometry.attributes.actorPoseB.needsUpdate=true;}}''',{'state':state,'fraction':fraction})
            page.wait_for_timeout(120);page.locator('#viewport canvas').screenshot(path=str(target/f'双角色-{state}.png'))
        page.goto('http://127.0.0.1:8798/?mode=defense');page.wait_for_function('window.range?.ready',timeout=90000);page.locator('#defense-start').click();page.wait_for_timeout(2500);page.locator('#canvas-host canvas').screenshot(path=str(target/'原作防线交战.png'))
        record=page.evaluate('({version:range.manifest.version,source:range.manifest.source_sha256,actors:range.view.actors.evidence(),ready:range.ready})');record['errors']=errors;record['portrait_scope']='双角色GPU姿态夹具，不能替代500或800同屏证据';(target/'检视回执.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');assert not errors,errors;browser.close()
    print(str(target),flush=True)
if __name__=='__main__':main()
