"""冻结候选地图、当前载具和源码，在独立回环端口验证；不改正式库或旧服务。"""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import mimetypes
from pathlib import Path
import os
import sys
import time
from http.server import ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from 资源优化.运行资源接入 import decoders
spec = importlib.util.spec_from_file_location('range_service', ROOT / 'scripts/预览载具靶场.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def read(p): return json.loads(p.read_text('utf8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, data): p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


class MapHTTPServer(ThreadingHTTPServer):
    # 同一页面仍由4并发队列约束；监听队列容纳取消、重连及其他已打开窗口的突发连接。
    request_queue_size = 128


class Handler(base.Handler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # 客户端取消过期区域请求属于正常关闭；真实HTTP错误仍按原路由返回。
            return

    def send_header(self, name, value):
        if name.lower() == 'cache-control' and getattr(self.server, 'streaming', False) and (self.path.startswith('/map/') or self.path.startswith('/media/') or self.path.startswith(getattr(self.server,'vendor_prefix','/未登记版本/'))):
            value = 'public,max-age=31536000,immutable'
        super().send_header(name, value)

    def serve(self, head):
        key = unquote(urlsplit(self.path).path)
        if key in self.server.encoded and 'gzip' in self.headers.get('Accept-Encoding', '') and not self.headers.get('Range'):
            body = self.server.encoded[key]
            self.send_response(200)
            path = self.server.static.get(key)
            mime = ('text/javascript' if path.suffix in ['.js', '.mjs'] else mimetypes.guess_type(path.name)[0]) if path else ('application/json' if key.endswith('.json') else 'application/octet-stream')
            cache = 'public,max-age=31536000,immutable' if key.startswith(('/map/', '/media/')) or key.startswith(self.server.vendor_prefix) else 'no-store'
            for name, value in {'Content-Type': mime or 'application/octet-stream', 'Content-Encoding': 'gzip', 'Content-Length': str(len(body)), 'Vary': 'Accept-Encoding', 'Cache-Control': cache, 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():
                self.send_header(name, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if self.path.split('?')[0] == '/api/status':
            body = json.dumps(self.server.identity, ensure_ascii=False).encode('utf8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if not head: self.wfile.write(body)
        else: super().serve(head)


def create(directory, port, revision, source_override=None):
    directory = directory.resolve()
    directory.relative_to((ROOT / 'local-data/断箭/批次').resolve())
    runtime = read(directory / '地图运行.json')
    freeze = directory / ('浏览器源码-' + revision)
    freeze.mkdir(exist_ok=False)
    static = {}
    game_source = source_override.resolve() if source_override else ROOT / 'src/载具靶场'
    if source_override:
        game_source.relative_to(directory.parents[1] / '载具候选源码')
    sources = {name: game_source / name for name in base.STATIC}
    for p in (ROOT / 'src/地图验证').glob('*.mjs'): sources[p.name] = p
    sources['audio-runtime.mjs'] = ROOT / 'src/音效索引/音频运行时.mjs'
    sources['compressed-loader.mjs'] = ROOT / 'src/资源加载/压缩模型加载.mjs'
    records = {}
    for name, path in sources.items():
        data = path.read_bytes()
        if name == 'index.html':
            data = data.replace('城市联合演训 · 操作原型 02'.encode(), 'Airbase候选 · 独立验证'.encode())
        target = freeze / name
        target.write_bytes(data)
        static['/' + name] = target
        records[name] = {'source': path.relative_to(ROOT).as_posix(), 'source_sha256': sha(path), 'http_sha256': sha(target)}
    static['/'] = freeze / 'index.html'
    tools = ROOT / 'local-data/tools/three'
    pin = read(tools / '当前版本.json')
    folder = tools / pin['version']
    if sha(folder / '来源与校验.json') != pin['metadata_sha256']: raise ValueError('Three.js元数据变化')
    frozen = {r['path']: r for r in read(folder / '来源与校验.json')['files']}
    for name in base.VENDOR:
        path = folder / name
        if sha(path) != frozen[name]['sha256']: raise ValueError('Three.js摘要变化')
        static['/vendor/' + name] = path
    decoders(static, folder, frozen)
    static['/compressed-loader.mjs'] = freeze / 'compressed-loader.mjs'
    vendor_prefix='/vendor/'+pin['metadata_sha256']+'/'
    for key,path in list(static.items()):
        if key.startswith('/vendor/'):
            static[vendor_prefix+key[len('/vendor/'):]]=path
    # 固定第三方代码使用带完整校验身份的URL；升级版本会自然换URL，不缓存未版本化代码。
    for name in ['index.html','compressed-loader.mjs']:
        path=freeze/name
        path.write_bytes(path.read_bytes().replace(b'/vendor/',vendor_prefix.encode()))
        records[name]['http_sha256']=sha(path)
    sys.path.insert(0, str(ROOT / 'scripts/资源库'))
    from 统一资源 import current, routes_absolute
    _, _, _, index = current()
    routes = routes_absolute(read(index / '靶场资源路由.json'))
    manifest = read(index / '靶场运行清单.json')
    streaming = runtime.get('schema') == 'airbase-streaming-scene-v1'
    encoded = {}
    if streaming:
        package = (directory / runtime.get('package_base', '.')).resolve()
        package.relative_to((ROOT / 'local-data/断箭/批次').resolve())
        served = copy.deepcopy(runtime)
        served['package_base'] = '.'
        # 完整文件表仍是服务白名单及交付依据；客户端不重复接收不会下载的包内原件记录。
        if runtime.get('simulation_packages'):
            unused = set()
            for chunk in served['chunks']:
                unused.update([chunk['geometry'], chunk['collision']])
                chunk['geometry_identity'] = {'bytes': chunk['geometry_identity']['bytes']}
                chunk['collision_identity'] = {'bytes': chunk['collision_identity']['bytes']}
                chunk.pop('identity', None)
            unused.update(page['file'] for page in served['pages'])
            unused.update(packet['file'] for packet in served['simulation_packages'].values())
            served['files'] = {name: {'bytes': row['bytes'], 'sha256': row['sha256']} for name, row in served['files'].items() if name not in unused}
        body = json.dumps(served, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(body).hexdigest()
        prefix = '/map/' + digest + '/'
        target = freeze / '地图运行.json'
        target.write_bytes(body)
        manifest['mapURL'] = prefix + '地图运行.json'
        for name, identity in runtime['files'].items():
            path = (package / name).resolve()
            path.relative_to(package)
            routes[prefix + name] = {'path': str(path), **identity}
        routes[manifest['mapURL']] = {'path': str(target), 'bytes': len(body), 'sha256': digest}
        encoded[manifest['mapURL']] = gzip.compress(body, compresslevel=6, mtime=0)
    else:
        manifest['mapURL'] = '/map/地图运行.json'
        names = ['地图运行.json', runtime['height']['file'], runtime['ground']]
        names += [r['file'] for r in runtime['prototypes'].values()]
        for chunk in runtime.get('chunks', []): names.append(chunk['file'])
        names += runtime.get('assets', [])
        for name in dict.fromkeys(names):
            path = (directory / name).resolve()
            path.relative_to(directory)
            routes['/map/' + name] = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}
    for row in routes.values():
        path = Path(row['path']).resolve()
        path.relative_to((ROOT / 'local-data').resolve())
        if sha(path) != row['sha256']: raise ValueError('媒体摘要变化')
    server = MapHTTPServer(('127.0.0.1', port), Handler)
    server.vendor_prefix=vendor_prefix
    server.daemon_threads = True
    server.static, server.routes = static, routes
    if streaming:
        for key, path in static.items():
            if path.suffix in ['.js', '.mjs', '.css', '.html'] and path.stat().st_size > 2048:
                encoded[key] = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
        for packet in runtime.get('simulation_packages', {}).values():
            key = prefix + packet['file']
            encoded[key] = gzip.compress(Path(routes[key]['path']).read_bytes(), compresslevel=6, mtime=0)
        if runtime.get('coarse_height'):
            key = prefix + runtime['coarse_height']['file']
            encoded[key] = gzip.compress(Path(routes[key]['path']).read_bytes(), compresslevel=6, mtime=0)
    server.encoded, server.streaming = encoded, streaming
    server.manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode('utf8')
    server.identity = {'app': 'scavenger-airbase-map', 'pid': os.getpid(), 'port': port, 'map': runtime['id'],
                       'directory': directory.relative_to(ROOT).as_posix(), 'revision': revision,
                       'map_sha256': sha(directory / '地图运行.json'), 'started_at': time.time(),
                       'transport': {'request_queue_size': server.request_queue_size, 'program_sha256': sha(Path(__file__)), 'vendor_prefix':vendor_prefix}}
    save(freeze / '服务身份.json', {**server.identity, 'sources': records, 'routes': routes,
                                'manifest_sha256': hashlib.sha256(server.manifest_bytes).hexdigest()})
    print(json.dumps(server.identity, ensure_ascii=False), flush=True)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8780)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--source-override', type=Path)
    args = parser.parse_args()
    server = create(args.directory, args.port, args.revision, args.source_override)
    try: server.serve_forever()
    finally: server.server_close()
