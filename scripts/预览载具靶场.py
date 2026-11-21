"""仅向本机提供载具靶场源码、固定Three.js和清单登记媒体。"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
import webbrowser

from 资源优化.运行资源接入 import decoders, range_manifest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'local-data/原型/载具靶场'
PORT = 8768
STATIC = ['index.html','界面.css','入口.mjs','场景.mjs','输入.mjs','模拟.mjs','地图.mjs','寻路.mjs','飞行.mjs','声音.mjs','统计.mjs']
VENDOR = ['build/three.module.js','build/three.core.js','examples/jsm/loaders/GLTFLoader.js','examples/jsm/utils/BufferGeometryUtils.js','examples/jsm/utils/SkeletonUtils.js']
def read(path): return json.loads(path.read_text('utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.serve(False)
    def do_HEAD(self): self.serve(True)
    def serve(self,head):
        try:
            key=unquote(urlsplit(self.path).path,errors='strict')
            if '\\' in key or '..' in key.split('/') or key.startswith('//'): self.send_error(404); return
            if key=='/api/status':
                body=b'{"app":"scavenger-range","version":1}'
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
                if not head:self.wfile.write(body)
                return
            if key=='/manifest.json':
                # 各服务冻结自己的清单，避免其他端口验证覆盖或清理共享JSON后路由失配。
                body=self.server.manifest_bytes
                self.send_response(200)
                for name,value in {'Content-Type':'application/json','Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}.items():self.send_header(name,value)
                self.end_headers()
                if not head:self.wfile.write(body)
                return
            row=self.server.routes.get(key)
            path=Path(row['path']) if row else self.server.static.get(key)
            if not path:self.send_error(404);return
            size=path.stat().st_size
            if row and size!=row['bytes']:self.send_error(409);return
            start,end,status=0,size-1,200
            if self.headers.get('Range'):
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'])
                if not match or not any(match.groups()):raise IndexError()
                first,last=match.groups()
                if first:start=int(first);end=min(size-1,int(last)) if last else size-1
                else:
                    if int(last)==0:raise IndexError()
                    start=max(0,size-int(last))
                if start>end or start>=size:raise IndexError()
                status=206
            mime='text/javascript' if path.suffix in ('.js','.mjs') else mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(status)
            for name,value in {'Content-Type':mime,'Content-Length':str(end-start+1),'Cache-Control':'no-store','Accept-Ranges':'bytes',
                               'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                               'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(name,value)
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if head:return
            with path.open('rb') as stream:
                stream.seek(start);remaining=end-start+1
                while remaining:
                    chunk=stream.read(min(1048576,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except IndexError:
            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers()
        except FileNotFoundError:self.send_error(404)
        except (BrokenPipeError,ConnectionResetError):pass
    def log_message(self,*args):pass


def create_server(port=PORT):
    if not (DATA/'资源清单.json').exists():raise RuntimeError('先运行 python -B scripts/检查载具原型资源.py --prepare')
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True
    app=ROOT/'src/载具靶场'
    server.static={'/'+name:app/name for name in STATIC};server.static['/']=app/'index.html'
    server.static['/manifest.json']=DATA/'资源清单.json'
    server.static['/audio-runtime.mjs']=ROOT/'src/音效索引/音频运行时.mjs'
    tools=ROOT/'local-data/tools/three';pin=read(tools/'当前版本.json');folder=tools/pin['version']
    if sha(folder/'来源与校验.json')!=pin['metadata_sha256']:raise ValueError('Three.js元数据变化')
    frozen={row['path']:row for row in read(folder/'来源与校验.json')['files']}
    for name in VENDOR:
        path=folder/name
        if sha(path)!=frozen[name]['sha256']:raise ValueError('Three.js文件变化')
        server.static['/vendor/'+name]=path
    decoders(server.static,folder,frozen)
    unified=ROOT/'local-data/资源库'
    unified_ready=(unified/'当前版本.json').exists()
    if unified_ready:
        sys.path.insert(0,str(ROOT/'scripts/资源库'))
        from 统一资源 import current,routes_absolute
        _,_,_,index=current()
        server.routes=routes_absolute(read(index/'靶场资源路由.json'))
        input_manifest=read(index/'靶场运行清单.json')
    else:
        server.routes=read(DATA/'私有路由.json');input_manifest=read(DATA/'资源清单.json')
    for row in server.routes.values():
        path=Path(row['path']).resolve()
        if not path.is_relative_to((ROOT/'local-data').resolve()) or sha(path)!=row['sha256']:raise ValueError('媒体越界或摘要变化')
    server.manifest_bytes=json.dumps(input_manifest if unified_ready else range_manifest(input_manifest,server.routes),ensure_ascii=False,indent=2).encode('utf8')
    optimized=(unified/'验收/靶场服务清单.json') if unified_ready else ROOT/'local-data/网页资源库/全库第一版/靶场运行清单.json'
    optimized.parent.mkdir(parents=True,exist_ok=True)
    optimized.write_bytes(server.manifest_bytes)
    server.static['/manifest.json']=optimized
    return server


def open_range():
    url=f'http://127.0.0.1:{PORT}/'
    def ready():
        try:
            with urlopen(url+'api/status',timeout=1) as response:return json.load(response).get('app')=='scavenger-range'
        except (OSError,ValueError):return False
    if not ready():
        DATA.mkdir(parents=True,exist_ok=True)
        with (DATA/'服务日志.log').open('ab') as stream:
            subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--serve'],cwd=ROOT,stdout=stream,stderr=stream,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        deadline=time.time()+20;event=threading.Event()
        while time.time()<deadline and not ready():event.wait(.15)
        if not ready():raise RuntimeError('8768端口启动失败，请检查服务日志')
    webbrowser.open(url);print('载具靶场：'+url)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=PORT)
    args=parser.parse_args()
    if args.serve:
        server=create_server(args.port);print(f'载具靶场：http://127.0.0.1:{server.server_port}/',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
    else:open_range()
