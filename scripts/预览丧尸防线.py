"""独立回环端口提供丧尸防线原型；只读统一资源，冻结本轮运行源码。"""
import argparse
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

from 预览载具靶场 import Handler,VENDOR
from 群体资源交接 import load_delivery
from 资源优化.运行资源接入 import decoders

ROOT=Path(__file__).resolve().parents[1]
VERSION='V003'
OUT=ROOT/'local-data/原型/丧尸防线/V003-01a1a2af'
PORT=8794
FILES=['index.html','界面.css','入口.mjs','场景.mjs','输入.mjs','模拟.mjs','地图.mjs','寻路.mjs','飞行.mjs','声音.mjs','统计.mjs','丧尸规则.mjs','防线配置.mjs','尸群表现.mjs','尸群合批.mjs']

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class DefenseHandler(Handler):
    def serve(self,head):
        if self.path.split('?')[0]=='/api/status':
            body=json.dumps({'app':'scavenger-defense','version':VERSION,'source':self.server.source_sha}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
            if not head:self.wfile.write(body)
        else:super().serve(head)

def create_server(port=PORT,live=False):
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import current,routes_absolute
    _,_,_,index=current();manifest=read(index/'靶场运行清单.json');routes=routes_absolute(read(index/'靶场资源路由.json'))
    actors,actor_routes=load_delivery()
    for key,path in actor_routes.items():routes[key]={'path':str(path),'sha256':digest(path),'bytes':path.stat().st_size}
    for row in routes.values():
        p=Path(row['path']).resolve()
        if not p.is_relative_to((ROOT/'local-data/资源库/当前资源').resolve()) or digest(p)!=row['sha256']:raise ValueError('资源身份或路径无效')
    # 仅开放本轮清单明确列出的原生事件和GUI图像，不扩大到整个研究目录。
    native=OUT/'原生事件核验/尝试/GAU8循环100-四秒/event.wav'
    expected='af4e56852f6f521648c65268a52dc022188dbc30c5e9c3d4305b271d9b59bbac'
    if digest(native)!=expected:raise ValueError('GAU8连续原生事件身份变化')
    import wave
    with wave.open(str(native)) as wav:
        seconds=wav.getnframes()/wav.getframerate();rate=wav.getframerate();channels=wav.getnchannels()
    url='/candidate-audio/gau8-native.wav'
    routes[url]={'path':str(native),'sha256':expected,'bytes':native.stat().st_size}
    manifest['audio']['planeShot']={**manifest['audio']['planeShot'],'audio_id':manifest['audio']['planeShot']['audio_id']+':loop100-native-v3',
        'url':url,'sha256':expected,'duration_seconds':seconds,'sample_rate':rate,'channels':channels}
    manifest['airAudioProfile']={'attackStart':.2,'loopStart':1.0,'loopEnd':3.6,'releaseStart':4.1,'releaseEnd':5.25,
        'derivation':'原FMOD事件Loop_Count_MG=100，4秒Stop(ALLOWFADEOUT)；循环区间与停声衔接为网页适配值，未声称为原作循环点'}
    ui={}
    for row in read(OUT/'交互资源/图像来源.json'):
        p=(OUT/'交互资源/图像'/(row['name']+'.png')).resolve()
        if not p.is_relative_to((OUT/'交互资源/图像').resolve()) or digest(p)!=row['sha256']:raise ValueError('GUI图像身份变化')
        url='/reference-ui/'+row['name']+'.png';routes[url]={'path':str(p),'sha256':row['sha256'],'bytes':row['bytes']};ui[row['name']]=url
    manifest['referenceUI']=ui
    sources={'/'+name:ROOT/'src/载具靶场'/name for name in FILES}
    sources.update({'/crowd/'+name:ROOT/'src/丧尸群体测试'/name for name in ['角色.mjs','角色配置.mjs']})
    sources['/audio-runtime.mjs']=ROOT/'src/音效索引/音频运行时.mjs'
    identities=[{'url':url,'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)} for url,p in sources.items()]
    source_sha=hashlib.sha256(json.dumps(identities,ensure_ascii=False).encode()).hexdigest()
    folder=OUT/'运行源码'/source_sha[:16]
    if not live:
        for row in identities:
            p=sources[row['url']];dest=folder/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():shutil.copy2(p,dest)
            if digest(dest)!=row['sha256']:raise ValueError('冻结源码摘要冲突')
            sources[row['url']]=dest
    sources['/']=sources['/index.html']
    tools=ROOT/'local-data/tools/three';pin=read(tools/'当前版本.json');vendor=tools/pin['version'];frozen={r['path']:r for r in read(vendor/'来源与校验.json')['files']}
    for name in VENDOR:
        p=vendor/name
        if digest(p)!=frozen[name]['sha256']:raise ValueError('Three.js依赖摘要变化')
        sources['/vendor/'+name]=p
    decoders(sources,vendor,frozen)
    manifest.update(defense=True,actors=actors,source_identity=identities,source_sha256=source_sha,version=VERSION,frozen_source=not live)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'服务资源清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'资源路由身份.json').write_text(json.dumps(routes,ensure_ascii=False,indent=2),encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',port),DefenseHandler);server.daemon_threads=True;server.static=sources;server.routes=routes;server.manifest_bytes=json.dumps(manifest,ensure_ascii=False).encode();server.source_sha=source_sha
    return server

def ensure_service(port=PORT):
    url=f'http://127.0.0.1:{port}/'
    def status():
        try:
            with urlopen(url+'api/status',timeout=1) as r:return json.load(r)
        except (OSError,ValueError):return None
    old=status()
    if old and (old.get('app')!='scavenger-defense' or old.get('version')!=VERSION):raise RuntimeError('端口由其他应用或版本使用，未接管')
    if not old:
        OUT.mkdir(parents=True,exist_ok=True)
        with (OUT/'服务日志.log').open('ab') as log:
            subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__).resolve()),'--serve','--port',str(port)],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        deadline=time.time()+30;event=threading.Event()
        while time.time()<deadline and not status():event.wait(.15)
        if not status():raise RuntimeError('防线服务未就绪，请检查本轮日志')
    print(url,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--serve',action='store_true');p.add_argument('--live',action='store_true');p.add_argument('--port',type=int,default=PORT);p.add_argument('--open',action='store_true');args=p.parse_args()
    if args.serve:
        server=create_server(args.port,args.live);print(f'丧尸防线：http://127.0.0.1:{server.server_port}/',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
    else:
        ensure_service(args.port)
        if args.open:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{args.port}/?mode=defense')
