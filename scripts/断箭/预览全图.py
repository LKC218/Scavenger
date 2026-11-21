"""冻结全图显示候选与精确媒体白名单，在独立回环端口预览；不修改正式库。"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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


def read(path):
    return json.loads(path.read_text('utf-8'))


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            result.update(block)
    return result.hexdigest()


class Handler(base.Handler):
    def send_header(self, keyword, value):
        if keyword.lower() == 'cache-control' and self.path.startswith('/map/'):
            value = 'public,max-age=31536000,immutable'
        super().send_header(keyword, value)

    def serve(self, head):
        key = unquote(urlsplit(self.path).path)
        if key in self.server.encoded and 'gzip' in self.headers.get('Accept-Encoding', '') and not self.headers.get('Range'):
            body = self.server.encoded[key]
            self.send_response(200)
            for name, value in {'Content-Type': 'application/json;charset=utf-8', 'Content-Encoding': 'gzip', 'Content-Length': str(len(body)), 'Vary': 'Accept-Encoding', 'Cache-Control': 'public,max-age=31536000,immutable', 'X-Content-Type-Options': 'nosniff'}.items():
                self.send_header(name, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if self.path.split('?')[0] == '/api/status':
            body = json.dumps(self.server.identity, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if not head:
                self.wfile.write(body)
        else:
            super().serve(head)


def create(directory, port, revision):
    directory = directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    source = directory / '地图运行.json'
    manifest = read(source)
    if manifest['schema'] != 'airbase-streaming-scene-v1':
        raise ValueError('仅接受全图区域运行候选')
    package = (directory / manifest.get('package_base', '.')).resolve()
    package.relative_to(ROOT / 'local-data/断箭/批次')
    freeze = directory / ('浏览器源码-' + revision)
    freeze.mkdir(exist_ok=False)
    static, records = {}, {}
    sources = {p.name: p for p in (ROOT / 'src/地图验证').glob('*.mjs')}
    sources['index.html'] = ROOT / 'src/地图验证/全图预览.html'
    sources['compressed-loader.mjs'] = ROOT / 'src/资源加载/压缩模型加载.mjs'
    for name, file in sources.items():
        content = file.read_bytes()
        (freeze / name).write_bytes(content)
        static['/' + name] = freeze / name
        records[name] = {'source': file.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(content).hexdigest()}
    static['/'] = freeze / 'index.html'
    tools = ROOT / 'local-data/tools/three'
    pin = read(tools / '当前版本.json')
    folder = tools / pin['version']
    if sha(folder / '来源与校验.json') != pin['metadata_sha256']:
        raise ValueError('Three.js工具元数据变化')
    frozen = {r['path']: r for r in read(folder / '来源与校验.json')['files']}
    for name in [*base.VENDOR, 'examples/jsm/controls/OrbitControls.js']:
        file = folder / name
        if sha(file) != frozen[name]['sha256']:
            raise ValueError('Three.js依赖摘要变化：' + name)
        static['/vendor/' + name] = file
    decoders(static, folder, frozen)
    static['/compressed-loader.mjs'] = freeze / 'compressed-loader.mjs'
    # 内容寻址的完整候选清单控制HTTP缓存，源码快照另行绑定。
    manifest['package_base'] = '.'
    content = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(content).hexdigest()
    prefix = '/map/' + digest + '/'
    (freeze / '地图运行.json').write_bytes(content)
    routes = {}
    for name, identity in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('运行清单含非规范路径')
        file = (package / relative).resolve()
        file.relative_to(package)
        if file.stat().st_size != identity['bytes'] or sha(file) != identity['sha256']:
            raise ValueError('运行媒体摘要变化：' + name)
        routes[prefix + name] = {'path': str(file), **identity}
    routes[prefix + '地图运行.json'] = {'path': str(freeze / '地图运行.json'), 'bytes': len(content), 'sha256': digest}
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.static, server.routes = static, routes
    server.encoded = {prefix + '地图运行.json': gzip.compress(content, compresslevel=6, mtime=0)}
    server.manifest_bytes = json.dumps({'mapURL': prefix + '地图运行.json'}, ensure_ascii=False).encode('utf-8')
    server.identity = {'app': 'scavenger-airbase-full-scene', 'pid': os.getpid(), 'port': port, 'revision': revision,
                       'started_epoch': time.time(), 'source_manifest_sha256': sha(source), 'http_manifest_sha256': digest,
                       'manifest_decoded_bytes': len(content), 'manifest_gzip_bytes': len(server.encoded[prefix + '地图运行.json']),
                       'scope': '全图静态显示候选，载具及性能尚未验收'}
    (freeze / '服务身份.json').write_text(json.dumps({**server.identity, 'sources': records, 'routes': routes}, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(server.identity, ensure_ascii=False), flush=True)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8782)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.revision):
        raise ValueError('无效服务修订')
    server = create(args.directory, args.port, args.revision)
    try:
        server.serve_forever()
    finally:
        server.server_close()
