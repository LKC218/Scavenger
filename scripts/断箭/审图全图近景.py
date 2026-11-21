"""采集机场、池塘、边缘和植被坡面的正常近景及真实地表射线；AI观察另行登记。"""
import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from 验证全图故障 import VIEW


def run(args):
    output=args.directory/'近景网页审图'/args.run
    output.mkdir(parents=True,exist_ok=False)
    result={'views':[],'errors':[],'ai_visual_status':'待实际查看','human_acceptance':'待人工验收',
            'source_program_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=False)
        page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        page.on('console',lambda message:result['errors'].append(message.text) if message.type=='error' else None)
        try:
            page.goto(f'http://127.0.0.1:{args.port}/');page.bring_to_front()
            page.wait_for_function('window.range?.ready',timeout=90000)
            result['identity']=page.evaluate('fetch("/api/status").then(r=>r.json())');result['version']=browser.version
            page.evaluate('()=>range.configureBenchmark({count:0,mode:"static",composition:{tank:0,heli:0,plane:0},span:140,center:{x:0,z:85}})')
            page.evaluate('range.sim.paused=true')
            for name,x,z,angle in [('机场道路与机库',0,85,0),('真实池塘',2732,3608,0),('西北边缘',-4400,4700,0),('植被与坡面',4262.5,-2712.5,.7)]:
                state=page.evaluate(VIEW,{'x':x,'z':z,'span':140,'angle':angle})
                page.wait_for_function('!range.externalMap.stats.pending&&!range.externalMap.stats.transcodes&&!range.externalMap.stats.dataPending',timeout=90000)
                assert not state['mapStats'].get('lastError') and not state['mapStats']['overview']
                rays=page.evaluate('''()=>{const v=range.view,m=range.externalMap,meshes=m.visibleGroundMeshes();let checked=0,missing=0;for(const x of [.1,.3,.5,.7,.9])for(const y of [.1,.3,.5,.7,.9]){const p=v.ground(x*v.width,y*v.height),b=m.bounds;if(!p||p.x<b.minX||p.x>b.maxX||p.z<b.minZ||p.z>b.maxZ)continue;checked++;if(!v.ray(x*v.width,y*v.height).intersectObjects(meshes,false).length)missing++;}return{checked,missing};}''')
                assert rays['checked']>=9 and rays['missing']==0,rays
                water=None
                if name=='真实池塘':
                    water=page.evaluate('''async()=>{const T=await import('three'),m=range.externalMap,meshes=m.visibleWaterMeshes();m.root.updateMatrixWorld(true);const hits=new T.Raycaster(new T.Vector3(2728,1000,3571),new T.Vector3(0,-1,0)).intersectObjects(meshes,false);return{meshes:meshes.length,height:hits[0]?.point.y,sourceHeight:m.querySupport(2728,3571).waterHeight};}''')
                    assert water['meshes']==2 and abs(water['height']-water['sourceHeight'])<.001,water
                page.wait_for_timeout(250)
                file=name+'.png';page.screenshot(path=str(output/file))
                result['views'].append({'name':name,'file':file,'sha256':hashlib.sha256((output/file).read_bytes()).hexdigest(),'state':state,'rays':rays,'water':water})
            assert not result['errors'],result['errors']
            result['technical_passed']=True
        finally:
            browser.close();(output/'审图输入.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--run',required=True)
    parser.add_argument('--port',type=int,default=8784)
    run(parser.parse_args())
