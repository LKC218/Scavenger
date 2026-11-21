"""冻结全图81个1公里审图格，逐格保存实际网页俯视与斜视，并验证可见地表支撑。"""

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont


def run(args):
    output = args.directory / '分区网页审图' / args.run
    output.mkdir(parents=True, exist_ok=False)
    url = f'http://127.0.0.1:{args.port}/'
    with urlopen(url+'api/status') as response:
        identity = json.load(response)
    result = {'identity': identity, 'browser': args.browser, 'views': [], 'errors': [], 'human_acceptance': '待人工验收',
              'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False)
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
        try:
            page.goto(url)
            page.wait_for_function('window.range?.ready', timeout=90000)
            page.evaluate('()=>range.configureBenchmark({count:0,mode:"static",center:{x:0,z:85},span:700,composition:{tank:0,heli:0,plane:0}})')
            page.evaluate('range.sim.paused=true')
            page.add_style_tag(content='.field-top,.field-bottom,#labels,#pause-overlay,#toast,.north-arrow{visibility:hidden!important}')
            for z in range(9):
                for x in range(9):
                    name = f'{x+1:02}-{z+1:02}'
                    for role in ['斜视', '俯视']:
                        snapshot = page.evaluate('''async p=>{const v=range.view,m=range.externalMap,b=m.bounds,x=b.minX+(p.x+.5)*1000,z=b.minZ+(p.z+.5)*1000;v.stopCamera();v.center.set(x,m.coarseHeightAt(x,z),z);v.span=v.targetSpan=1350;v.angle=v.targetAngle=Math.PI/4;v.updateCamera();if(p.role==='俯视'){v.camera.position.set(x,v.center.y+1800,z+.001);v.camera.lookAt(v.center);v.camera.updateMatrixWorld();}m.update({center:v.center,span:v.span*v.width/v.height,height:v.camera.position.y-v.center.y});await m.waitForView();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));v.render();let misses=0,checked=0;for(const ax of [.1,.3,.5,.7,.9])for(const az of [.1,.3,.5,.7,.9]){const wx=b.minX+(p.x+ax)*1000,wz=b.minZ+(p.z+az)*1000,h=m.coarseHeightAt(wx,wz),q=v.project({x:wx,y:h,z:wz});if(q.x<0||q.y<0||q.x>v.width||q.y>v.height)continue;checked++;if(!v.ray(q.x,q.y).intersectObjects(m.visibleGroundMeshes(),false).length)misses++;}return {center:{x,z},height:v.camera.position.y-v.center.y,span:v.span,checked,misses,stats:{...m.stats},drawCalls:v.renderer.info.render.calls,triangles:v.renderer.info.render.triangles};}''', {'x': x, 'z': z, 'role': role})
                        assert not snapshot['stats']['errors'], snapshot['stats']['errors']
                        assert snapshot['checked'] >= 9 and snapshot['misses'] == 0, snapshot
                        file = output / (name+'-'+role+'.png')
                        page.locator('#canvas-host').screenshot(path=str(file))
                        result['views'].append({'cell': name, 'role': role, 'file': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), **snapshot})
                    (output / '进行中.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
                print(f'网页审图：第{z+1}/9行完成', flush=True)
            result['technical_passed'] = not result['errors'] and len(result['views']) == 162
            result['ai_visual'] = '联系图和原图待实际查看，不自动升级'
        finally:
            browser.close()
            (output / '审图输入.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    contacts = []
    for role in ['俯视', '斜视']:
        rows = [v for v in result['views'] if v['role'] == role]
        for start in range(0, len(rows), 9):
            canvas = Image.new('RGB', (1290, 810), '#eeeeea')
            draw = ImageDraw.Draw(canvas)
            for i, item in enumerate(rows[start:start+9]):
                with Image.open(output / item['file']) as image:
                    image.thumbnail((420, 240))
                    canvas.paste(image, ((i%3)*430, (i//3)*270+25))
                draw.text(((i%3)*430+5, (i//3)*270+2), item['cell']+' '+role, font=font, fill='#111111')
            file = output / f'联系图-{role}-{start//9+1:02}.png'
            canvas.save(file)
            contacts.append({'file': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    (output / '联系图索引.json').write_text(json.dumps(contacts, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'technical_passed': result['technical_passed'], 'views': len(result['views']), 'contacts': len(contacts)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], default='chrome')
    parser.add_argument('--port', type=int, default=8784)
    run(parser.parse_args())
