"""独立资源验证台；仅服务冻结清单媒体和固定Three.js目录。"""
import argparse, importlib.util, json, mimetypes, pathlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=ROOT/'local-data/网页资源库/全库第一版'

def catalog(include_lod=False):
    inventory=json.loads((OUT/'盘点.json').read_text(encoding='utf8'));by_hash={};names={r['id']:r['name'] for r in inventory['formal_records']}
    for r in inventory['files']:by_hash.setdefault(r['sha256'],[]).append(r)
    models=[];audio=[];routes={}
    for p in (OUT/'验证').glob('*.json'):
        if not p.name.endswith(('.model.json','.audio.json')):continue
        r=json.loads(p.read_text(encoding='utf8'))
        if not r.get('passed'):continue
        aliases=by_hash[r['source_sha256']];ids=sorted({f['id'] for a in aliases for f in a.get('formal',[])})
        url='/media/'+r['sha256']+pathlib.Path(r['path']).suffix;routes[url]=ROOT/r['path']
        original='/original/'+r['source_sha256']+('.glb' if r['kind']=='model' else pathlib.Path(r['source']).suffix);routes[original]=pathlib.Path(r['source'])
        first=pathlib.Path(aliases[0]['path']);fallback=first.parent.name if first.stem.lower() in ['model','模型','工程'] else first.stem
        entry={k:r[k] for k in ['source_sha256','source_bytes','bytes','sha256']};entry.update(url=url,original_url=original,name='、'.join(names.get(i,i) for i in ids) or fallback,ids=ids,aliases=[a['path'] for a in aliases])
        (models if r['kind']=='model' else audio).append(entry)
    if include_lod:
        variants=[]
        for entry in models:
            p=OUT/'验证'/(entry['source_sha256']+'.lod.json')
            if not p.exists():continue
            lod=json.loads(p.read_text(encoding='utf8'))
            if not lod.get('passed'):continue
            for v in lod['variants']:
                url='/media/'+v['sha256']+'.glb';routes[url]=ROOT/v['path']
                variants.append({**entry,'source_sha256':entry['source_sha256']+'.lod'+str(v['level']),'base_sha256':entry['source_sha256'],'level':v['level'],'name':entry['name']+' · 细节层'+str(v['level']),'url':url,'sha256':v['sha256'],'bytes':v['bytes']})
        models.extend(variants)
    return {'models':sorted(models,key=lambda x:x['name']),'audio':sorted(audio,key=lambda x:x['name'])},routes

def create_server(port=8770,include_lod=False):
    # 历史验证台复用当前统一入口，避免继续提供退役媒体。
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        spec=importlib.util.spec_from_file_location('unified_legacy_gallery',ROOT/'scripts/断箭/启动正式资料库.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.create_server(port)
    result,routes=catalog(include_lod);app=ROOT/'src/网页资源验证';vendor=ROOT/'local-data/tools/three/0.187.0'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            key=unquote(urlsplit(self.path).path)
            if '..' in key.split('/') or '\\' in key:self.send_error(404);return
            if key=='/catalog.json':body=json.dumps(result,ensure_ascii=False).encode();mime='application/json'
            else:
                p=routes.get(key)
                if key in ['/','/index.html','/app.mjs']:p=app/('index.html' if key=='/' else key[1:])
                elif key.startswith('/vendor/'):
                    p=(vendor/key[len('/vendor/'):]).resolve()
                    if not p.is_relative_to(vendor.resolve()):self.send_error(404);return
                if not p or not p.is_file():self.send_error(404);return
                body=p.read_bytes();mime='text/javascript' if p.suffix in ['.js','.mjs'] else mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers()
            try:self.wfile.write(body)
            except (ConnectionResetError,BrokenPipeError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True;return server
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8770);p.add_argument('--lod',action='store_true');a=p.parse_args();create_server(a.port,include_lod=a.lod).serve_forever()
