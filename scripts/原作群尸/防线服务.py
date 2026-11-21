"""从8794冻结身份建立原作防线独立候选，不写回V003或接管旧服务。"""
import argparse,hashlib,json,os,sys,threading,time,subprocess,shutil
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import quote
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48/防线V004';PORT=8798
sys.path.insert(0,str(ROOT/'scripts'))
from 预览载具靶场 import Handler,VENDOR
from 资源优化.运行资源接入 import decoders
from 资源清单 import manifests,sha

class NativeDefenseHandler(Handler):
    def serve(self,head):
        if self.path.split('?')[0]=='/api/status':
            body=json.dumps({'app':'scavenger-native-defense','version':'V004','source':self.server.source_sha,'base_v003':self.server.base_sha}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
            if not head:self.wfile.write(body)
        else:super().serve(head)

def create_server(port=PORT,write_identity=True):
    OUT.mkdir(parents=True,exist_ok=True)
    expected='00ca74b615ee1b898d82cedb52590c136b5565121d4a1195b563919767d02594'
    input_record=OUT/'V003来源冻结.json'
    if input_record.exists():
        frozen_input=json.loads(input_record.read_text('utf-8'));manifest=frozen_input['manifest'];original=frozen_input['routes']
        if frozen_input['source_sha256']!=expected:raise ValueError('本轮V003来源身份不符')
    else:
        with urlopen('http://127.0.0.1:8794/manifest.json',timeout=10) as r:manifest=json.load(r)
        with urlopen('http://127.0.0.1:8794/api/status',timeout=10) as r:status=json.load(r)
        if status.get('version')!='V003' or status.get('source')!=expected:raise ValueError('8794不是已确认的V003身份')
        original=json.loads((ROOT/'local-data/原型/丧尸防线/V003-01a1a2af/资源路由身份.json').read_text('utf-8'))
        with input_record.open('x',encoding='utf-8') as f:json.dump({'source_sha256':expected,'manifest':manifest,'routes':original},f,ensure_ascii=False,indent=2)
    old_actor_urls={m['url'] for m in manifest['actors']['models']};routes={k:v for k,v in original.items() if k not in old_actor_urls}
    static={};snapshot=OUT/'V003冻结输入'
    for row in manifest['source_identity']:
        p=snapshot/row['path'];p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():
            with urlopen('http://127.0.0.1:8794/'+quote(row['url'].lstrip('/'),safe='/'),timeout=10) as r:data=r.read()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('V003源输入身份变化')
            p.write_bytes(data)
        if sha(p)!=row['sha256']:raise ValueError('本轮冻结源冲突')
        static[row['url']]=p
        if row['url'].startswith('/') and not row['url'].startswith('/crowd/'):
            static['/legacy-defense'+row['url']]=p
    families,native_routes=manifests();manifest['actors']=families['native']
    for url,p in native_routes.items():
        if url.startswith('/native-assets/'):routes[url]={'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
    original_base=(ROOT/'local-data/原型/丧尸防线/V003-01a1a2af').resolve()
    for url,record in list(routes.items()):
        original_path=Path(record['path']).resolve()
        if original_path.is_relative_to(original_base):
            own=OUT/'配套原生媒体'/(record['sha256']+original_path.suffix);own.parent.mkdir(exist_ok=True)
            if not own.exists():
                if sha(original_path)!=record['sha256']:raise ValueError('配套原生媒体来源身份变化')
                shutil.copyfile(original_path,own)
            if sha(own)!=record['sha256']:raise ValueError('配套原生媒体冻结副本不符')
            routes[url]={**record,'path':str(own)}
    static['/尸群表现.mjs']=ROOT/'src/原作群尸/防线适配.mjs';static['/native/合批.mjs']=ROOT/'src/原作群尸/合批.mjs'
    page=(static['/index.html']).read_text('utf-8').replace('十三款','原作两款');candidate=OUT/'防线原作候选.html'
    if not candidate.exists():candidate.write_text(page,encoding='utf-8')
    static['/index.html']=candidate;static['/']=candidate
    vendor=ROOT/'local-data/tools/three/0.187.0';frozen={r['path']:r for r in json.loads((vendor/'来源与校验.json').read_text('utf-8'))['files']}
    for name in VENDOR:static['/vendor/'+name]=vendor/name
    decoders(static,vendor,frozen)
    identities=[{'url':url,'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for url,p in sorted(static.items())];source_sha=hashlib.sha256(json.dumps(identities,ensure_ascii=False).encode()).hexdigest()
    # 自有适配及依赖也冻结，后续开发不会改变已运行身份。
    for url,p in list(static.items()):
        dest=OUT/'运行源码'/source_sha[:16]/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():dest.write_bytes(p.read_bytes())
        if sha(dest)!=sha(p):raise ValueError('候选冻结身份冲突')
        static[url]=dest
    manifest.update(version='V004',source_sha256=source_sha,source_identity=identities,base_v003_sha256=expected,frozen_source=True)
    server=ThreadingHTTPServer(('127.0.0.1',port),NativeDefenseHandler);server.daemon_threads=True;server.static=static;server.routes=routes;server.manifest_bytes=json.dumps(manifest,ensure_ascii=False).encode();server.source_sha=source_sha;server.base_sha=expected
    if write_identity:
        (OUT/'服务身份.json').write_text(json.dumps({'pid':os.getpid(),'port':port,'version':'V004','source_sha256':source_sha,'manifest':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'资源路由身份.json').write_text(json.dumps(routes,ensure_ascii=False,indent=2),encoding='utf-8')
    return server

def ensure_service(port=PORT):
    def status():
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/status',timeout=1) as r:return json.load(r)
        except OSError:return None
    old=status()
    if old and old.get('app')!='scavenger-native-defense':raise RuntimeError('候选端口由其他应用使用')
    if not old:
        OUT.mkdir(parents=True,exist_ok=True)
        with (OUT/'服务.log').open('ab') as f:subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--serve','--port',str(port)],cwd=ROOT,stdout=f,stderr=f,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.time()+30;event=threading.Event()
        while time.time()<deadline and not status():event.wait(.15)
        if not status():raise RuntimeError('原作防线服务启动失败')
    return f'http://127.0.0.1:{port}/?mode=defense'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=PORT);parser.add_argument('--open',action='store_true');parser.add_argument('--verify-offline',action='store_true');args=parser.parse_args()
    if args.verify_offline:
        def forbid_network(*a,**k):raise RuntimeError('离线核验禁止网络读取')
        urlopen=forbid_network;server=create_server(0,write_identity=False);server.server_close();print(json.dumps({'offline_boot_verified':True,'source_sha256':server.source_sha},ensure_ascii=False))
    elif args.serve:
        server=create_server(args.port);print(f'原作防线候选：http://127.0.0.1:{server.server_port}/',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
    else:
        url=ensure_service(args.port);print(url,flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(url)
