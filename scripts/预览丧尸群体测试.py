"""丧尸群体实验专用回环白名单服务与启动器，不暴露项目目录。"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
import webbrowser

from 资源优化.运行资源接入 import decoders
from 群体资源交接 import load_delivery
from 群体采样归档 import ForegroundObserver, store_sample

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'local-data/丧尸模型/性能测试/十三款混合/ZOM-MIX-261103-001'
APP = ROOT / 'src/丧尸群体测试'
PORT = 8769
STATIC = ['index.html','界面.css','入口.mjs','群体.mjs','角色.mjs','角色配置.mjs','场景.mjs','统计.mjs']
SOURCES = [*(APP/n for n in STATIC), ROOT/'src/资源加载/压缩模型加载.mjs', Path(__file__), ROOT/'scripts/群体资源交接.py', ROOT/'scripts/群体采样归档.py']
VENDOR = ['build/three.module.js','build/three.core.js','examples/jsm/loaders/GLTFLoader.js','examples/jsm/utils/BufferGeometryUtils.js','examples/jsm/utils/SkeletonUtils.js']


def read(p):return json.loads(p.read_text('utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest():
    record,routes=load_delivery()
    record['three']='0.187.0'
    record['code_sha256']=hashlib.sha256(b''.join(p.read_bytes() for p in SOURCES)).hexdigest()
    record['asset']=record['models'][0]['asset']
    return record,routes


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path!='/api/samples' or self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}':self.send_error(403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<64*1024*1024:raise ValueError('样本大小超出范围')
            if self.server.runtime_manifest['code_sha256']!=hashlib.sha256(b''.join(p.read_bytes() for p in SOURCES)).hexdigest():raise ValueError('运行期间源码变化，请重启并重新采样')
            result=json.loads(self.rfile.read(length));receipt=store_sample(result,self.server.runtime_manifest,self.server.observer,DATA)
            body=json.dumps(receipt,ensure_ascii=False).encode('utf-8');self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (ValueError,KeyError,TypeError) as exc:self.send_error(400,str(exc).encode('ascii','backslashreplace').decode())
    def do_GET(self):self.serve(False)
    def do_HEAD(self):self.serve(True)
    def serve(self,head):
        try:
            key=unquote(urlsplit(self.path).path,errors='strict')
            if '\\' in key or '..' in key.split('/') or key.startswith('//'):self.send_error(404);return
            if key=='/api/status':body=b'{"app":"scavenger-horde","version":2}';mime='application/json'
            elif key=='/api/foreground':body=json.dumps(self.server.observer.proof()).encode();mime='application/json'
            elif key=='/manifest.json':body=json.dumps(self.server.runtime_manifest,ensure_ascii=False).encode();mime='application/json'
            elif key in self.server.source_bytes:body=self.server.source_bytes[key];mime='text/css' if key.endswith('.css') else 'text/html' if key in ('/','/index.html') else 'text/javascript'
            else:
                path=self.server.routes.get(key)
                if not path or not path.is_file():self.send_error(404);return
                body=path.read_bytes();mime='text/javascript' if path.suffix in ('.mjs','.js') else mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200)
            for k,v in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                         'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
            self.end_headers()
            if not head:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass
    def log_message(self,*args):pass


def create_server(port=PORT):
    record,media=manifest()
    tools=ROOT/'local-data/tools/three';pin=read(tools/'当前版本.json');folder=tools/pin['version']
    if pin['version']!='0.187.0' or sha(folder/'来源与校验.json')!=pin['metadata_sha256']:raise ValueError('引擎固定版本或元数据变化')
    frozen={r['path']:r for r in read(folder/'来源与校验.json')['files']}
    routes={'/'+n:APP/n for n in STATIC};routes['/']=APP/'index.html';routes.update(media)
    routes['/report']=ROOT/'reports/丧尸模型/十三款混合群体性能报告.html'
    for n in VENDOR:
        p=folder/n
        if sha(p)!=frozen[n]['sha256']:raise ValueError('引擎文件摘要变化')
        routes['/vendor/'+n]=p
    decoders(routes,folder,frozen)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True;server.routes=routes;server.runtime_manifest=record
    server.source_bytes={url:path.read_bytes() for url,path in routes.items() if path in SOURCES or url=='/'}
    snapshot=DATA/'源码'/record['code_sha256'];snapshot.mkdir(parents=True,exist_ok=True)
    for path in SOURCES:
        target=snapshot/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=path.read_bytes():raise ValueError('同摘要源码快照出现冲突')
        if not target.exists():target.write_bytes(path.read_bytes())
    (snapshot/'运行清单.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    server.observer=ForegroundObserver(DATA);return server


def start(open_page=True):
    url=f'http://127.0.0.1:{PORT}/'
    def ready():
        try:
            with urlopen(url+'api/status',timeout=1) as r:return json.load(r).get('app')=='scavenger-horde'
        except (OSError,ValueError):return False
    if not ready():
        DATA.mkdir(parents=True,exist_ok=True)
        with (DATA/'服务日志.log').open('ab') as log:
            subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--serve'],cwd=ROOT,stdout=log,stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        deadline=time.time()+20;event=threading.Event()
        while time.time()<deadline and not ready():event.wait(.15)
        if not ready():raise RuntimeError('8769未能启动，请检查端口占用及日志；不会终止其他服务')
    if open_page:webbrowser.open(url)
    print('丧尸群体测试：'+url)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--serve',action='store_true');parser.add_argument('--no-open',action='store_true');parser.add_argument('--port',type=int,default=PORT)
    args=parser.parse_args()
    if args.serve:
        server=create_server(args.port)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.observer.close();server.server_close()
    else:start(not args.no_open)
