"""真实WebGL源LOD1与原生图集网页适配的四向及俯视并排审图。"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont


def run(args):
    output = args.directory / '植被替身审图' / args.run
    output.mkdir(parents=True, exist_ok=False)
    result = {'assets': [], 'errors': [], 'ai_visual': '待实际审图', 'human': '待人工验收',
              'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=False)
        page = browser.new_page(viewport={'width': 1280, 'height': 720})
        page.on('pageerror', lambda error: result['errors'].append(error.stack or str(error)))
        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready', timeout=90000)
            result['identity'] = page.evaluate('fetch("/api/status").then(r=>r.json())')
            page.evaluate('range.sim.paused=true')
            indices = page.evaluate('range.externalMap.prototypes.map((p,i)=>p.impostor?i:-1).filter(i=>i>=0)')
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
            for index in indices:
                row = page.evaluate('async p=>(await import("/植被替身.mjs")).reviewImpostor(range,p.index,{calibrate:p.calibrate})', {'index': index, 'calibrate': args.calibrate})
                canvas = Image.new('RGB', (1600, 690), '#dddeda')
                draw = ImageDraw.Draw(canvas)
                for i, view in enumerate(row['rows']):
                    name = row['name']+'-'+view['name']+'-'+view['role']+'.png'
                    data = base64.b64decode(view.pop('image').split(',', 1)[1])
                    (output / name).write_bytes(data)
                    view.update(file=name, sha256=hashlib.sha256(data).hexdigest())
                    x,y = (i//2)*320, (i%2)*345
                    with Image.open(output / name) as image:
                        canvas.paste(image, (x,y+25))
                    draw.text((x+4,y+2), view['name']+' '+view['role'], font=font, fill='#111111')
                file = output / (row['name']+'-对照.png')
                canvas.save(file)
                row['contact'] = {'file': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
                result['assets'].append(row)
                print(row['name']+'：五方向对照完成', flush=True)
        finally:
            browser.close()
            (output / '审图输入.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--run', required=True)
    parser.add_argument('--port', type=int, default=8784)
    parser.add_argument('--calibrate', action='store_true')
    run(parser.parse_args())
