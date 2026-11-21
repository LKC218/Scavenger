"""原作群尸独立回环白名单服务，冻结源码、资源与前台观察，保留逐帧样本。"""
import argparse,csv,hashlib,json,mimetypes,os,shutil,subprocess,sys,threading,time,ctypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48';PORT=8797
sys.path.insert(0,str(ROOT/'scripts'))
from 群体采样归档 import ForegroundObserver
from 资源优化.运行资源接入 import decoders
from 资源清单 import manifests,sha

class TestObserver(ForegroundObserver):
    expected_pid=None
    def proof(self):
        row=super().proof()
        if self.expected_pid is not None and sys.platform=='win32':
            from ctypes import wintypes
            pid=wintypes.DWORD();ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(),ctypes.byref(pid));row['chrome']=row['chrome'] and pid.value==self.expected_pid
        return row

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def json(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode();self.send_response(status);self.send_header('Content-Type','application/json;charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        parsed=urlsplit(self.path);path=unquote(parsed.path)
        if path=='/api/status':return self.json({'app':'scavenger-native-crowd','version':'V001','source_sha256':self.server.source_sha,'frozen':not self.server.live})
        if path=='/api/foreground':return self.json(self.server.observer.proof())
        if path=='/manifest.json':
            family=parse_qs(parsed.query).get('family',['native'])[0]
            if family not in self.server.manifests:return self.json({'error':'未知资源集'},400)
            return self.json({**self.server.manifests[family],'code_sha256':self.server.source_sha,'three':'0.187.0'})
        p=self.server.static.get(path) or self.server.routes.get(path)
        if not p:return self.json({'error':'资源不在白名单'},404)
        data=p.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        if urlsplit(self.path).path=='/api/test-window':
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));self.server.observer.expected_pid=int(data['pid']);return self.json({'target_window_registered':True})
        if urlsplit(self.path).path!='/api/samples':return self.json({'error':'未知写入接口'},404)
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<80_000_000:raise ValueError('样本长度无效')
            result=json.loads(self.rfile.read(size));config=result['config'];family=config['family'];expected=self.server.manifests[family]
            if config['code_sha256']!=self.server.source_sha or sum(m['count'] for m in config['models'])!=config['count'] or len(result['models'])!=len(expected['models']):raise ValueError('来源或逐款计数不符')
            result.update(self.server.observer.coverage(result['startEpoch'],result['endEpoch'],'chrome'))
            result['headless']=bool(result['environment'].get('headless'));s=result['summary']
            result['stable60Passed']=bool(not result['headless'] and result['valid'] and result['nativeForegroundVerified'] and result['requestedDurationS']>=180 and s['elapsedS']>=179.5 and s['fps']>=60 and s['p95Ms']<=16.7 and s['over33Pct']<.1 and s['simulationDroppedS']==0 and s['minVisible']==config['count'] and s['minAnimated']==config['count'] and all(m['complete'] and m['minVisible']==m['count'] and m['minAnimated']==m['count'] for m in result['models']))
            directory=OUT/'样本';directory.mkdir(exist_ok=True);name=f'{family}-{config["count"]}-{config["mode"]}-{result["requestedDurationS"]}s-{time.time_ns()}'
            p=directory/(name+'.json')
            with p.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,separators=(',',':'))
            fields=['t','frame','simulation','animation','sync','submit','gpu','calls','triangles','visible','animated','moving','neighbors','dropped']
            with (directory/(name+'.csv')).open('x',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f);writer.writerow(fields);writer.writerows([r.get(k) for k in fields] for r in result['rows'])
            self.json({'file':name,'nativeForegroundVerified':result['nativeForegroundVerified'],'stable60Passed':result['stable60Passed'],'summary':s})
        except Exception as error:self.json({'error':str(error)},400)

def create_server(port=PORT,live=False):
    OUT.mkdir(parents=True,exist_ok=True);families,routes=manifests()
    static={'/':ROOT/'src/原作群尸/index.html','/index.html':ROOT/'src/原作群尸/index.html','/界面.css':ROOT/'src/原作群尸/界面.css','/入口.mjs':ROOT/'src/原作群尸/入口.mjs','/native/合批.mjs':ROOT/'src/原作群尸/合批.mjs'}
    for name in ['群体.mjs','场景.mjs','统计.mjs','角色配置.mjs']:static['/crowd/'+name]=ROOT/'src/丧尸群体测试'/name
    vendor=ROOT/'local-data/tools/three/0.187.0';frozen={r['path']:r for r in json.loads((vendor/'来源与校验.json').read_text('utf-8'))['files']}
    for name in ['build/three.module.js','build/three.core.js','examples/jsm/loaders/GLTFLoader.js','examples/jsm/utils/BufferGeometryUtils.js','examples/jsm/utils/SkeletonUtils.js']:static['/vendor/'+name]=vendor/name
    decoders(static,vendor,frozen)
    identities=[{'url':url,'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for url,p in sorted(static.items())]
    source_sha=hashlib.sha256(json.dumps(identities,ensure_ascii=False).encode()).hexdigest()
    if not live:
        folder=OUT/'冻结源码'/source_sha[:16]
        for url,p in list(static.items()):
            dest=folder/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():shutil.copyfile(p,dest)
            if sha(dest)!=sha(p):raise ValueError('冻结源码冲突')
            static[url]=dest
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True;server.static=static;server.routes=routes;server.manifests=families;server.source_sha=source_sha;server.live=live;server.observer=TestObserver(OUT/'前台观察')
    (OUT/'服务身份.json').write_text(json.dumps({'app':'scavenger-native-crowd','pid':os.getpid(),'port':port,'source_sha256':source_sha,'sources':identities,'assets':families},ensure_ascii=False,indent=2),encoding='utf-8')
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=PORT);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    server=create_server(args.port,args.live);print(f'原作群尸验证：http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.observer.close();server.server_close()
