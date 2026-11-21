"""读取真正渲染器投影误差；逐原型逐方向核验，不以包围盒或固定减面比例替代。"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright


def run(args):
    output = args.directory / '投影正式验证' / args.run / args.browser
    output.mkdir(parents=True, exist_ok=False)
    result = {'assets': [], 'errors': [], 'browser': args.browser, 'source_program_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.browser, headless=False)
        page = browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.on('console', lambda message: result['errors'].append(message.text) if message.type == 'error' else None)
        try:
            page.goto(f'http://127.0.0.1:{args.port}/')
            page.wait_for_function('window.range?.ready', timeout=90000)
            page.evaluate('range.sim.paused=true')
            result['identity'] = page.evaluate('fetch("/api/status").then(r=>r.json())')
            result['version'] = browser.version
            ids = page.evaluate('range.externalMap.prototypes.map((p,index)=>({index,category:p.category})).filter(p=>["building","decor"].includes(p.category)).map(p=>p.index)')
            assert len(ids) == 119, 'Airbase固定原型数量不完整'
            for i, index in enumerate(ids):
                row = page.evaluate('''async index=>{const module=await import('/原型投影核验.mjs');return module.checkPrototypeProjection(range,index);}''', index)
                result['assets'].append(row)
                (output / '进行中.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
                if i%8 == 7:
                    print(f'{args.browser}：投影核验 {i+1}/{len(ids)}', flush=True)
            result['passed'] = not result['errors'] and all(row['passed'] for row in result['assets'])
            result['failed_assets'] = [row['name'] for row in result['assets'] if not row['passed']]
        finally:
            browser.close()
            (output / '核验凭证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'passed': result['passed'], 'failed_assets': result['failed_assets']}, ensure_ascii=False))
    return result['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], required=True)
    parser.add_argument('--port', type=int, default=8784)
    raise SystemExit(0 if run(parser.parse_args()) else 1)
