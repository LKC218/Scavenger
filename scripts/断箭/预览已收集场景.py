"""只读提供已收集场景闭包及固定宿主别名，不暴露包内未登记文件。"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
import webbrowser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--entry', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = root / '依赖收集凭证.json'
    if not inventory.is_file():
        inventory = root / args.manifest
    document = json.loads(inventory.read_text('utf-8'))
    files = document['files']
    routes = {}
    for key, identity in files.items():
        file = (root / key).resolve();file.relative_to(root)
        if any(parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()) for parent in [file, *file.parents] if parent != root.parent):
            raise ValueError('场景闭包含链接')
        if not file.is_file() or file.stat().st_size != identity['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != identity['sha256']:
            raise ValueError('收集资源摘要变化：'+key)
        routes['/'+key] = file
        if '/vendor/' in key:
            routes['/vendor/'+key.split('/vendor/', 1)[1]] = file
        if key.endswith('/资源加载/压缩模型加载.mjs'):
            routes['/compressed-loader.mjs'] = file
    routes['/'] = routes['/'+args.entry]

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.serve(True)
        def do_GET(self):
            self.serve(False)
        def serve(self, head):
            key = unquote(urlsplit(self.path).path)
            if key == '/favicon.ico':
                self.send_response(204);self.send_header('Content-Length', '0');self.end_headers();return
            file = routes.get(key)
            if not file:
                self.send_error(404);return
            size = file.stat().st_size
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript' if file.suffix in ['.mjs', '.js'] else mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            # 固定Basis解码器的Emscripten绑定使用Function；与正式本地服务保持同一许可。
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; object-src 'none'")
            self.end_headers()
            if not head:
                try:
                    with file.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024*1024), b''):
                            self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}/'
    print(json.dumps({'app': 'scavenger-scene-collection', 'pid': os.getpid(), 'url': url, 'files': len(files)}, ensure_ascii=False), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
