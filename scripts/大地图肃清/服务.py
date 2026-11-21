"""首批肃清候选的冻结与回环白名单服务，不接管旧原型。"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'src/大地图肃清'
HOME = ROOT / 'local-data/大地图肃清/首批/V001-01a1ac6a'
LIBRARY = ROOT / 'local-data/资源库'
NATIVE = ROOT / 'local-data/断箭/批次/BA-26273833-014'
APP_ID = 'scavenger-clearance-first-batch'
SOURCE_NAMES = ('index.html', '入口.mjs', '配置.mjs', '契约.mjs', '样本场景.mjs', '交互.mjs', '界面.css', '表现.mjs', '声音.mjs', '原作合批适配.mjs', '规则.mjs')
VENDOR_NAMES = ('build/three.module.js', 'build/three.core.js', 'examples/jsm/loaders/GLTFLoader.js', 'examples/jsm/loaders/KTX2Loader.js', 'examples/jsm/utils/BufferGeometryUtils.js', 'examples/jsm/utils/SkeletonUtils.js', 'examples/jsm/utils/WorkerPool.js', 'examples/jsm/libs/meshopt_decoder.module.js', 'examples/jsm/libs/ktx-parse.module.js', 'examples/jsm/libs/zstddec.module.js', 'examples/jsm/math/ColorSpaces.js', 'examples/jsm/libs/basis/basis_transcoder.js', 'examples/jsm/libs/basis/basis_transcoder.wasm')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def bounded(path, boundary=ROOT):
    path = Path(path).absolute()
    path.resolve().relative_to(Path(boundary).resolve())
    probe = path
    while probe != Path(boundary).absolute():
        if probe.is_symlink() or (probe.exists() and getattr(probe.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('白名单路径不得包含链接或目录联接')
        if probe.parent == probe:
            raise ValueError('路径未处于边界内')
        probe = probe.parent
    return path


def media_record(path, sha=None, size=None):
    path = bounded(path)
    actual = digest(path)
    actual_size = path.stat().st_size
    if (sha and actual != sha) or (size is not None and actual_size != size):
        raise ValueError('媒体身份不一致：' + path.name)
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': actual, 'bytes': actual_size}


def source_assets():
    """只读取精确索引和本批五个模型，不收录整个媒体库。"""
    pointer_file = LIBRARY / '当前版本.json'
    pointer_sha = digest(pointer_file)
    pointer = read_json(pointer_file)
    if pointer['revision'] != 7:
        raise ValueError('本批资源基线要求revision7')
    directory = bounded(LIBRARY / pointer['directory'], LIBRARY)
    payloads = {}
    for name in ('资源索引.json', '资源路由.json'):
        path = bounded(directory / name, directory)
        if digest(path) != pointer['metadata'][name]:
            raise ValueError('统一库索引摘要不一致：' + name)
        payloads[name] = read_json(path)
    catalog, source_routes = payloads['资源索引.json'], payloads['资源路由.json']
    models, routes = {}, {}
    for kind, ident in {'tank':'model:US_M1A2', 'heli':'model:US_AH64D', 'plane':'model:US_A10'}.items():
        row = next(r for r in catalog['models'] if r['id'] == ident)
        if row['quality_status'] != 'passed':
            raise ValueError('本批样本静态状态变化：' + ident)
        source = source_routes[row['model_url']]
        record = media_record(ROOT / source['path'], row['sha256'], row['bytes'])
        routes[row['model_url']] = record
        models[kind] = {k: row[k] for k in ('id','name','sha256','bytes')}
        models[kind]['url'] = row['model_url']
    native = []
    for role in ('Walker','Runner'):
        folder = NATIVE / role
        receipt, raw = read_json(folder / '压缩回执.json'), read_json(folder / '派生记录.json')
        if receipt['revision'] != 'r03' or not receipt['passed']:
            raise ValueError('原作候选不再是r03')
        record = media_record(bounded(receipt['path'], folder), receipt['sha256'], receipt['bytes'])
        url = '/media/' + record['sha256'] + '.glb'
        routes[url] = record
        native.append({'id':'model:ba-'+role.lower()+'-lod2', 'name':role, 'family':'native', 'url':url,
            'sha256':record['sha256'], 'bytes':record['bytes'], 'asset':{'sha256':record['sha256'],'bytes':record['bytes']},
            'bones':46, 'triangles':raw['triangles'], 'clips':raw['animations'],
            'coordinate_system':{'unit':'米','up':'+Y','forward':'+Z','rest_height_m':1.86},
            'states':{'idle':'ZombieIdle','move':'ZombieRunning' if role=='Walker' else 'ZombieSprint','attack':'ZombieAttack','death':'ZombieDying'}})
    if digest(pointer_file) != pointer_sha:
        raise ValueError('核对期间统一库指针变化')
    manifest = {'app':APP_ID, 'version':'V001', 'models':models, 'native':{'models':native}, 'audio':{}, 'assets':{},
                'sourceIdentity':{'libraryRevision':7,'pointerSha256':pointer_sha,'nativeRevision':'r03'}}
    material_file = HOME / '视听/素材清单.json'
    if material_file.exists():
        material = read_json(material_file)
        def resolve_record(row):
            if isinstance(row, list):
                return [resolve_record(item) for item in row]
            if not isinstance(row, dict) or 'path' not in row:
                return row
            p = bounded(ROOT / row['path'])
            allowed = (HOME / '视听', ROOT / 'local-data/断箭/批次/肃清视听-V001-01a1ac6a', LIBRARY)
            if not any(p.resolve().is_relative_to(a.resolve()) for a in allowed):
                raise ValueError('视听载荷超出本批明确范围')
            record = media_record(p, row['sha256'], row['bytes'])
            url = '/media/' + record['sha256'] + p.suffix.lower()
            routes[url] = record
            return {k:v for k,v in row.items() if k!='path'} | {'url':url}
        manifest['audio'] = {key:resolve_record(value) for key,value in material.get('audio',{}).items()}
        manifest['assets'] = {key:resolve_record(value) for key,value in material.get('textures',{}).items()}
        manifest['materialStatus'] = {'provenance':material.get('provenance',[]),'missing':material.get('missing',[])}
    return manifest, routes


def vendor_assets():
    home = ROOT / 'local-data/tools/three'
    pointer = read_json(home / '当前版本.json')
    if pointer['version'] != '0.187.0':
        raise ValueError('本批不升级Three.js')
    directory = bounded(home / pointer['version'], home)
    metadata_file = directory / '来源与校验.json'
    if digest(metadata_file) != pointer['metadata_sha256']:
        raise ValueError('Three.js元数据摘要变化')
    rows = {row['path']:row for row in read_json(metadata_file)['files']}
    routes = {}
    for name in VENDOR_NAMES:
        row = rows[name]
        record = media_record(bounded(directory / name,directory), row['sha256'], row['bytes'])
        routes['/vendor/' + name] = record
        if name.startswith('examples/jsm/'):
            routes['/vendor/addons/' + name[len('examples/jsm/'):]] = record
    routes['/vendor/three.module.js'] = routes['/vendor/build/three.module.js']
    routes['/vendor/three.core.js'] = routes['/vendor/build/three.core.js']
    return routes, pointer


def prepare():
    manifest, routes = source_assets()
    vendors, pinned = vendor_assets()
    routes.update(vendors)
    source_paths = {name:APP/name for name in SOURCE_NAMES}
    source_paths['compressed-loader.mjs'] = ROOT/'src/资源加载/压缩模型加载.mjs'
    for path in source_paths.values():
        if not path.is_file():
            raise FileNotFoundError('执行线尚未交付：' + path.name)
    revision = datetime.now(timezone.utc).strftime('r%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6]
    folder = HOME / '集成' / revision
    folder.mkdir(parents=True, exist_ok=False)
    sources = {}
    for name,path in source_paths.items():
        body = path.read_bytes()
        (folder/name).write_bytes(body)
        sources[name] = {'source':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
    source_sha = hashlib.sha256(encoded(sources)).hexdigest()
    manifest.update(revision=revision, sourceSha256=source_sha, three=pinned)
    identity = {'app':APP_ID,'version':'V001','revision':revision,'sourceSha256':source_sha,
                'sources':sources,'serverSha256':digest(__file__),'manifestSha256':hashlib.sha256(encoded(manifest)).hexdigest(),
                'approvalSha256':'0f242224730faf559e9a495d17acd0fbd3a85ab2ef01678e23f7757677202dfa',
                'manualAcceptance':'等待工程师逐目标验收'}
    save_json(folder/'清单.json',manifest)
    save_json(folder/'媒体路由.json',routes)
    save_json(folder/'身份.json',identity)
    save_json(HOME/'当前候选.json',{'directory':folder.relative_to(ROOT).as_posix(),'revision':revision,'sourceSha256':source_sha})
    return folder, identity


class CandidateServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


class Handler(BaseHTTPRequestHandler):
    server_version = 'ScavengerClearance/1'

    def log_message(self,*args):
        return

    def send_error(self,code,message=None,explain=None):
        super().send_error(code,self.responses.get(code,('Error',))[0],explain=explain or message)

    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' blob: data:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def send_body(self,body,mime,head):
        self.send_response(200)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def serve(self,head):
        if self.headers.get('Host','') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            return self.send_error(403,'只允许本机回环主机')
        key=unquote(urlsplit(self.path).path)
        if '\\' in key or '\x00' in key or any(p in {'.','..'} for p in key.split('/')):
            return self.send_error(400,'无效路径')
        if key=='/api/status':
            return self.send_body(encoded(self.server.identity),'application/json; charset=utf-8',head)
        if key in {'/api/manifest','/manifest.json'}:
            return self.send_body(self.server.manifest_body,'application/json; charset=utf-8',head)
        if key in self.server.static:
            body,mime=self.server.static[key]
            return self.send_body(body,mime,head)
        record=self.server.routes.get(key)
        if record is None:
            return self.send_error(404,'文件不在本批白名单')
        try:
            path=bounded(ROOT/record['path'])
            with path.open('rb') as stream:
                stat=os.fstat(stream.fileno());signature=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
                if stat.st_size!=record['bytes']:
                    raise ValueError('媒体大小变化')
                with self.server.verify_lock:
                    if self.server.verified.get(str(path))!=signature:
                        if hashlib.file_digest(stream,'sha256').hexdigest()!=record['sha256']:
                            raise ValueError('媒体摘要变化')
                        current=os.fstat(stream.fileno())
                        if (current.st_size,current.st_mtime_ns,current.st_ctime_ns)!=signature:
                            raise ValueError('核对期间媒体变化')
                        self.server.verified[str(path)]=signature
                size=record['bytes'];start=0;end=size-1;partial=False
                if self.headers.get('Range'):
                    match=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'].strip())
                    if not match or not any(match.groups()):
                        return self.send_error(416,'无效字节范围')
                    first,last=match.groups();start=int(first) if first else max(0,size-int(last));end=min(size-1,int(last)) if first and last else size-1
                    if start>end or start>=size or (not first and int(last)==0):
                        return self.send_error(416,'字节范围超出文件')
                    partial=True
                self.send_response(206 if partial else 200)
                mime={'.glb':'model/gltf-binary','.mjs':'text/javascript; charset=utf-8','.js':'text/javascript; charset=utf-8','.wasm':'application/wasm'}.get(path.suffix.lower(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(end-start+1))
                self.send_header('Accept-Ranges','bytes')
                self.send_header('ETag','"'+record['sha256']+'"')
                self.send_header('Cache-Control','public, max-age=31536000, immutable')
                if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                if not head:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        data=stream.read(min(1024*1024,remaining))
                        if not data:break
                        self.wfile.write(data);remaining-=len(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            return
        except (ValueError,OSError):
            self.send_error(409,'本批媒体不可用或身份已变')


def create_server(folder,port):
    folder=bounded(folder,HOME/'集成')
    identity=read_json(folder/'身份.json')
    manifest=read_json(folder/'清单.json')
    if digest(__file__)!=identity['serverSha256'] or hashlib.sha256(encoded(manifest)).hexdigest()!=identity['manifestSha256']:
        raise ValueError('冻结服务或清单身份变化，须重新生成候选')
    static={}
    for name,row in identity['sources'].items():
        body=(folder/name).read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['sha256']:
            raise ValueError('冻结源码摘要不一致：'+name)
        key='/'+name;mime='text/css; charset=utf-8' if name.endswith('.css') else 'text/html; charset=utf-8' if name.endswith('.html') else 'text/javascript; charset=utf-8'
        static[key]=(body,mime)
        if name=='index.html':static['/']=(body,mime)
    server=CandidateServer(('127.0.0.1',port),Handler)
    server.identity={k:v for k,v in identity.items() if k!='sources'} | {'port':server.server_port}
    server.static=static;server.routes=read_json(folder/'媒体路由.json');server.manifest_body=encoded(manifest)
    server.verify_lock=threading.Lock();server.verified={}
    save_json(HOME/'服务身份.json',server.identity | {'pid':os.getpid(),'directory':folder.relative_to(ROOT).as_posix()})
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--port',type=int,default=8810)
    args=parser.parse_args()
    if args.preflight:
        manifest,routes=source_assets();vendors,pinned=vendor_assets()
        save_json(HOME/'资源预检.json',{'manifest':manifest,'media':routes,'vendor':vendors,'three':pinned})
        print(json.dumps({'asset_preflight':True,'models':len(manifest['models']),'native':len(manifest['native']['models']),'media':len(routes),'vendor_urls':len(vendors)},ensure_ascii=False))
    elif args.prepare:
        folder,identity=prepare();print(json.dumps({'candidate':folder.relative_to(ROOT).as_posix(),'identity':identity},ensure_ascii=False))
    elif args.serve:
        folder=args.candidate or ROOT/read_json(HOME/'当前候选.json')['directory']
        server=create_server(folder,args.port)
        print(json.dumps(server.identity,ensure_ascii=False),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
    else:
        parser.error('请指定--preflight、--prepare或--serve')
