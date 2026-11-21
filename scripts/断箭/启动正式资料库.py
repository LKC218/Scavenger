"""前线资产库固定本机服务及一键启动；只开放登记资源和界面文件。"""
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import urlopen
import webbrowser

from 资料库核心 import ROOT, DATA, LIB, current, query, read, sha, save

sys.path.insert(0,str(ROOT/'scripts'))
from 资源优化.运行资源接入 import decoders, library

PORT = 8767
APP = ROOT / "src/资料库"
STATIC = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.mjs": "app.mjs", "/viewer.mjs": "viewer.mjs", "/search.mjs": "search.mjs", "/favicon.svg": "favicon.svg"}
VENDOR = ["build/three.module.js", "build/three.core.js", "examples/jsm/loaders/GLTFLoader.js", "examples/jsm/controls/OrbitControls.js", "examples/jsm/utils/BufferGeometryUtils.js", "examples/jsm/utils/SkeletonUtils.js", "LICENSE"]


class LibraryServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def refresh(self):
        unified=ROOT/'local-data/资源库/当前版本.json'
        revision = ('unified',sha(unified)) if unified.exists() else read(LIB / "当前版本.json")["revision"]
        if revision != getattr(self, "refresh_identity", None):
            pointer, catalog, routes, _ = current()
            for row in routes.values():
                if not Path(row["path"]).resolve().is_relative_to((ROOT/'local-data').resolve()): raise ValueError("资源路由越界")
            self.catalog, self.routes, self.revision = catalog, routes, pointer["revision"]
            if getattr(self, 'candidate_manifest', None) is not None:
                from 发布场景 import candidate_index
                self.catalog, self.routes = candidate_index(self.candidate_manifest, catalog, routes)
            self.refresh_identity=revision
            library(self)


class RevealError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def resolve_reveal(catalog, routes, identity, url):
    row = next((item for item in catalog["models"] + catalog["audio"] + catalog.get("scenes", []) if item["id"] == identity), None)
    if not row:
        raise RevealError(404, "资产不在当前索引")
    if row["type"] in ("model", "scene"):
        allowed = [row.get("model_url"), row.get("blend_url"), row.get("scene_url")] + [variant.get("url") for variant in row.get("runtime_variants", [])]
    else:
        allowed = [row.get("url")]
    if url not in [value for value in allowed if value]:
        raise RevealError(403, "文件不属于该资产")
    record = routes.get(url)
    if not record:
        raise RevealError(404, "文件未登记在当前路由")
    source = Path(record["path"])
    if source.is_symlink() or not source.is_file():
        raise RevealError(404, "文件不存在或不是普通文件")
    path = source.resolve()
    if not path.is_relative_to((ROOT / "local-data").resolve()):
        raise RevealError(403, "文件不在资料库边界内")
    if path.stat().st_size != record["bytes"]:
        raise RevealError(409, "文件摘要大小已变化")
    return path


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self): self.serve(True)
    def do_GET(self): self.serve(False)
    def do_POST(self): self.reveal()

    def json_response(self, value, head):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head: self.wfile.write(body)

    def json_error(self, status, message):
        body = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def reveal(self):
        parsed = urlsplit(self.path)
        if unquote(parsed.path, errors="strict") != "/api/reveal":
            self.send_error(404); return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.json_error(415, "只接受 JSON 请求"); return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.json_error(403, "资料库只接受本机固定地址"); return
        if origin and origin not in (f"http://{host}", f"https://{host}"):
            self.json_error(403, "请求来源不在本机资料库"); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self.json_error(413, "请求体大小无效"); return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            identity = str(payload.get("id", ""))
            url = str(payload.get("url", ""))
            if not identity or not url:
                raise ValueError("缺少资产ID或文件URL")
            self.server.refresh()
            try:
                path = resolve_reveal(self.server.catalog, self.server.routes, identity, url)
            except RevealError as error:
                self.json_error(error.status, str(error)); return
            if os.name != "nt":
                self.json_error(501, "当前系统不支持资源管理器定位"); return
            lock = self.server.reveal_lock
            with lock:
                now = time.monotonic()
                if now - self.server.last_reveal < .8:
                    self.json_error(429, "操作过于频繁，请稍后重试"); return
                self.server.last_reveal = now
                subprocess.Popen(["explorer.exe", f"/select,{path}"], close_fds=True)
            self.json_response({"ok": True, "file": path.name}, False)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.json_error(400, "请求格式无效")
        except (KeyError, FileNotFoundError):
            self.json_error(404, "文件或索引不可用")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve(self, head):
        try:
            parsed = urlsplit(self.path); key = unquote(parsed.path, errors="strict")
            if "\\" in key or ".." in key.split("/") or key.startswith("//"): self.send_error(404); return
            self.server.refresh()
            if key == "/api/status":
                self.json_response({"app": "scavenger-library-candidate" if self.server.candidate_manifest else "scavenger-library",
                    "revision": self.server.revision, "candidate_sha256": self.server.catalog.get('candidate_sha256'),
                    "summary": self.server.catalog["summary"]}, head); return
            if key == "/api/catalog": self.json_response(self.server.catalog, head); return
            if key == "/api/search":
                params = parse_qs(parsed.query)
                value = lambda k, default=None: params.get(k, [default])[0]
                limit, offset = int(value("limit", 20)), int(value("offset", 0))
                if not 1 <= limit <= 200 or offset < 0 or value("type", "all") not in ("all", "model", "audio", "scene"): raise ValueError("查询参数无效")
                self.json_response(query(self.server.catalog, self.server.routes, value("q", ""), value("type", "all"), value("id"), value("related_to"), value("status"), limit, offset, False), head); return
            record = self.server.routes.get(key)
            if record:
                path = Path(record["path"])
                if path.stat().st_size != record["bytes"]: self.send_error(409); return
            else:
                path = self.server.static.get(key)
                if not path: self.send_error(404); return
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            ranges = self.headers.get("Range")
            if ranges:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranges)
                if not match or not any(match.groups()): raise IndexError()
                first, last = match.groups()
                if first:
                    start = int(first); end = min(size - 1, int(last)) if last else size - 1
                else:
                    if int(last) == 0: raise IndexError()
                    start = max(0, size - int(last))
                if start > end or start >= size: raise IndexError()
                status = 206
            mime = "text/javascript" if path.suffix in (".js", ".mjs") else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(status)
            for name, value in {"Content-Type": mime + ("; charset=utf-8" if mime.startswith("text/") else ""), "Content-Length": str(end - start + 1),
              "Cache-Control": "private, max-age=86400, immutable" if record else "no-store", "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff",
              "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self' blob: data:; media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"}.items(): self.send_header(name, value)
            if status == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head: return
            with path.open("rb") as stream:
                stream.seek(start); remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)
        except IndexError:
            self.send_response(416); self.send_header("Content-Length", "0"); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers()
        except (ValueError, KeyError): self.send_error(400)
        except FileNotFoundError: self.send_error(404)
        except (BrokenPipeError, ConnectionResetError): pass

    def log_message(self, *args): pass


def create_server(port=PORT, candidate_manifest=None):
    server = LibraryServer(("127.0.0.1", port), Handler)
    server.candidate_manifest = candidate_manifest
    server.reveal_lock = threading.Lock()
    server.last_reveal = 0
    server.static = {url: APP / name for url, name in STATIC.items()}
    tool = ROOT / "local-data/tools/three"
    pinned = read(tool / "当前版本.json")
    folder = tool / pinned["version"]
    if sha(folder / "来源与校验.json") != pinned["metadata_sha256"]: raise ValueError("Three.js版本记录变化")
    frozen = {row["path"]: row for row in read(folder / "来源与校验.json")["files"]}
    for name in VENDOR:
        path = folder / name
        if sha(path) != frozen[name]["sha256"]: raise ValueError("Three.js文件摘要不符")
        server.static["/vendor/" + name] = path
    decoders(server.static,folder,frozen)
    server.refresh()
    library(server)
    return server


def open_library(open_browser=True):
    url = f"http://127.0.0.1:{PORT}/"
    def available():
        try:
            with urlopen(url + "api/status", timeout=1) as response:
                return json.load(response).get("app") == "scavenger-library"
        except (URLError, ValueError, OSError): return False
    if not available():
        log = LIB / "服务日志.log"
        with log.open("ab") as stream:
            subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--serve"], cwd=ROOT, stdout=stream, stderr=stream,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        deadline = time.time() + 15
        event = threading.Event()
        while not available() and time.time() < deadline: event.wait(0.1)
        if not available(): raise RuntimeError("固定端口未启动，请检查服务日志或8767端口占用")
    if open_browser: webbrowser.open(url)
    print("前线资产库：" + url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--start", action="store_true", help="只启动服务，随后从内置浏览器访问固定地址")
    parser.add_argument('--candidate', type=Path, help='只读场景候选；必须同时指定独立端口及--serve')
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if args.candidate and (not args.serve or args.port == PORT):
        parser.error('候选必须使用--serve及独立端口，不能接管正式端口')
    if not args.candidate and args.port != PORT:
        parser.error('正式入口保持固定端口；独立端口仅供明确候选')
    if args.serve:
        server = create_server(args.port, read(args.candidate) if args.candidate else None)
        if not args.candidate:
            save(LIB / "服务进程.json", {"pid": __import__('os').getpid(), "port": PORT})
        print(f"前线资产库{'候选' if args.candidate else ''}：http://127.0.0.1:{args.port}/", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
    else: open_library(open_browser=not args.start)
