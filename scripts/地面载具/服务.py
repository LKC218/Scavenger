"""地面载具首版独立检验台：冻结源码与登记，只读提供回环白名单。"""

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
import time
from urllib.parse import unquote, urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "local-data/资源库"
APP = ROOT / "src/地面载具实装"
OUTPUT = ROOT / "local-data/原型/地空载具实装/V001-01a1ac6a"
APP_ID = "scavenger-ground-workbench"
VERSION = "V001"
STATIC_NAMES = ("index.html", "入口.mjs", "规则.mjs", "界面.css")
VENDOR_NAMES = (
    "build/three.module.js", "build/three.core.js",
    "examples/jsm/loaders/GLTFLoader.js", "examples/jsm/loaders/KTX2Loader.js",
    "examples/jsm/controls/OrbitControls.js", "examples/jsm/utils/BufferGeometryUtils.js",
    "examples/jsm/utils/SkeletonUtils.js", "examples/jsm/utils/WorkerPool.js",
    "examples/jsm/libs/meshopt_decoder.module.js", "examples/jsm/libs/ktx-parse.module.js",
    "examples/jsm/libs/zstddec.module.js", "examples/jsm/math/ColorSpaces.js",
    "examples/jsm/libs/basis/basis_transcoder.js", "examples/jsm/libs/basis/basis_transcoder.wasm",
)
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_json(path):
    return json.loads(Path(path).read_text("utf-8-sig"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


def bounded_path(path, boundary):
    """除解析后的边界外，再拒绝边界内的链接与目录联接。"""
    path = Path(path).absolute()
    boundary = Path(boundary).resolve()
    path.resolve().relative_to(boundary)
    probe = path
    while probe != boundary:
        attributes = getattr(probe.lstat(), "st_file_attributes", 0) if probe.exists() else 0
        if probe.is_symlink() or attributes & 0x400:
            raise ValueError("白名单路径含链接或目录联接")
        parent = probe.parent
        if parent == probe:
            raise ValueError("白名单路径越界")
        probe = parent
    return path


def build_manifest():
    """只核验索引元数据；媒体在实际请求时按打开的文件句柄核验。"""
    pointer_path = LIBRARY / "当前版本.json"
    pointer_body = pointer_path.read_bytes()
    pointer_sha = hashlib.sha256(pointer_body).hexdigest()
    pointer = json.loads(pointer_body.decode("utf-8-sig"))
    if pointer.get("revision") != 7:
        raise ValueError("首版检验台仅接受已冻结的资料库revision7")
    index = bounded_path(LIBRARY / pointer["directory"], LIBRARY)
    metadata = pointer.get("metadata", {})
    if not {"资源索引.json", "资源路由.json"}.issubset(metadata):
        raise ValueError("资料库指针缺少索引或路由摘要")
    verified_metadata = {}
    for name, expected in metadata.items():
        body = bounded_path(index / name, index).read_bytes()
        if not HASH_PATTERN.fullmatch(expected) or hashlib.sha256(body).hexdigest() != expected:
            raise ValueError("资料库元数据摘要不符：" + name)
        if name in {"资源索引.json", "资源路由.json"}:
            verified_metadata[name] = json.loads(body.decode("utf-8-sig"))
    catalog = verified_metadata["资源索引.json"]
    source_routes = verified_metadata["资源路由.json"]
    models, routes = [], {}
    for source in catalog["models"]:
        if source.get("category") != "ground":
            continue
        url = source.get("model_url", "")
        expected = source["sha256"]
        if not HASH_PATTERN.fullmatch(expected) or url != "/media/" + expected + ".glb":
            raise ValueError("地面模型URL身份无效：" + source["id"])
        route = source_routes.get(url)
        if not route or route["sha256"] != expected or route["bytes"] != source["bytes"]:
            raise ValueError("地面模型路由与索引不符：" + source["id"])
        path = bounded_path(ROOT / route["path"], LIBRARY)
        routes[url] = {"path": path, "sha256": expected, "bytes": route["bytes"]}
        row = {key: source[key] for key in ("id", "name", "quality_status", "sha256", "bytes")}
        row.update(url=url, model_url=url, source_id=source.get("source_id"),
                   category="ground", deployable=source["quality_status"] == "passed",
                   geometry=source.get("geometry", {}), limitations=source.get("limitations", []))
        models.append(row)
    counts = {state: sum(row["quality_status"] == state for row in models) for state in ("passed", "partial")}
    if len(models) != 77 or counts != {"passed": 75, "partial": 2}:
        raise ValueError("地面目录身份变化，预期77条、75通过及2部分完成")
    if digest(pointer_path) != pointer_sha:
        raise ValueError("读取过程中资料库指针发生变化")
    manifest = {"app": APP_ID, "version": VERSION, "catalog_revision": 7, "revision": 7,
                "source_pointer_sha256": pointer_sha, "source_metadata": metadata,
                "model_count": len(models), "deployable_count": counts["passed"], "models": models,
                "scope": "独立地面载具共通能力检验台，尚未接入Airbase或尸群主线"}
    return manifest, routes


def vendor_routes():
    home = ROOT / "local-data/tools/three"
    pinned = read_json(home / "当前版本.json")
    folder = bounded_path(home / pinned["version"], home)
    metadata = folder / "来源与校验.json"
    metadata_body = metadata.read_bytes()
    if hashlib.sha256(metadata_body).hexdigest() != pinned["metadata_sha256"]:
        raise ValueError("固定Three.js元数据摘要不符")
    recorded = {row["path"]: row for row in json.loads(metadata_body.decode("utf-8-sig"))["files"]}
    routes = {}
    for name in VENDOR_NAMES:
        row = recorded[name]
        path = bounded_path(folder / name, folder)
        if digest(path) != row["sha256"]:
            raise ValueError("固定Three.js文件摘要不符：" + name)
        record = {"path": path, "sha256": row["sha256"], "bytes": row["bytes"]}
        routes["/vendor/" + name] = record
        if name.startswith("examples/jsm/"):
            routes["/vendor/addons/" + name[len("examples/jsm/"):]] = record
    routes["/vendor/three.module.js"] = routes["/vendor/build/three.module.js"]
    routes["/vendor/three.core.js"] = routes["/vendor/build/three.core.js"]
    return routes, pinned


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def log_request_record(self, record):
        with self.log_lock:
            with (self.run_directory / "访问日志.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": time.time(), **record}, ensure_ascii=False) + "\n")

    def open_verified(self, record):
        boundary = LIBRARY if record["path"].is_relative_to(LIBRARY) else ROOT / "local-data/tools/three"
        path = bounded_path(record["path"], boundary)
        stream = path.open("rb")
        try:
            stat = os.fstat(stream.fileno())
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if stat.st_size != record["bytes"]:
                raise ValueError("白名单文件大小变化")
            with self.verify_lock:
                if self.verified.get(str(path)) != signature:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                        raise ValueError("白名单文件摘要变化")
                    after = os.fstat(stream.fileno())
                    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != signature:
                        raise ValueError("核验期间白名单文件变化")
                    self.verified[str(path)] = signature
            stream.seek(0)
            return stream
        except Exception:
            stream.close()
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = "GroundWorkbench/1"

    def send_error(self, code, message=None, explain=None):
        # HTTP状态行使用标准ASCII原因；中文提示保留在UTF-8错误正文中。
        reason = self.responses.get(code, ("Error", ""))[0]
        detail = explain if explain is not None else message
        super().send_error(code, message=reason, explain=detail)

    def log_message(self, format_string, *args):
        self.server.log_request_record({"method": self.command, "request": self.path,
                                        "message": format_string % args})

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self' blob: data:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def serve(self, head):
        host = self.headers.get("Host", "")
        if host not in {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}:
            self.send_error(403, "主机不在回环白名单")
            return
        key = unquote(urlsplit(self.path).path)
        if "\\" in key or "\x00" in key or any(part in {".", ".."} for part in key.split("/")):
            self.send_error(400, "无效路径")
            return
        if key == "/api/status":
            self.send_bytes(json_bytes(self.server.identity), "application/json; charset=utf-8", head)
            return
        if key == "/manifest.json":
            self.send_bytes(self.server.manifest_body, "application/json; charset=utf-8", head)
            return
        if key in self.server.static:
            body, mime = self.server.static[key]
            self.send_bytes(body, mime, head)
            return
        record = self.server.routes.get(key)
        if not record:
            self.send_error(404, "文件不在白名单")
            return
        try:
            with self.server.open_verified(record) as stream:
                size = record["bytes"]
                start, end, partial = 0, size - 1, False
                requested = self.headers.get("Range")
                if requested:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
                    if not match or not any(match.groups()):
                        self.send_error(416, "无效字节范围")
                        return
                    first, last = match.groups()
                    start = int(first) if first else max(0, size - int(last))
                    end = min(size - 1, int(last)) if first and last else size - 1
                    if start > end or start >= size or (not first and int(last) == 0):
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */" + str(size))
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    partial = True
                self.send_response(206 if partial else 200)
                mime = "model/gltf-binary" if key.endswith(".glb") else self.mime(key)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"' + record["sha256"] + '"')
                self.send_header("Cache-Control", "public, max-age=31536000, immutable" if key.startswith("/media/") else "no-cache")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if not head:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except (OSError, ValueError) as error:
            self.server.log_request_record({"event": "文件核验拒绝", "url": key, "error": str(error)})
            self.send_error(409, "白名单文件身份变化或不可用")

    @staticmethod
    def mime(path):
        suffix = Path(path).suffix
        return {".mjs": "text/javascript; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
                ".wasm": "application/wasm"}.get(suffix, mimetypes.guess_type(path)[0] or "application/octet-stream")

    def send_bytes(self, body, mime, head):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)


def create_server(port=8809, output=OUTPUT):
    manifest, media = build_manifest()
    vendors, pinned = vendor_routes()
    sources = {name: APP / name for name in STATIC_NAMES}
    sources["compressed-loader.mjs"] = ROOT / "src/资源加载/压缩模型加载.mjs"
    bodies = {name: path.read_bytes() for name, path in sources.items()}
    identity_sources = {name: {"source": sources[name].relative_to(ROOT).as_posix(),
                              "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                        for name, body in bodies.items()}
    identity_sources["服务.py"] = {"source": Path(__file__).relative_to(ROOT).as_posix(),
                                   "sha256": digest(__file__), "bytes": Path(__file__).stat().st_size}
    source_sha = hashlib.sha256(json_bytes(identity_sources)).hexdigest()
    output = bounded_path(output, OUTPUT)
    server = WorkbenchServer(("127.0.0.1", port), Handler)
    try:
        run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        run_directory = output / "运行" / run
        run_directory.mkdir(parents=True, exist_ok=False)
        for name, body in bodies.items():
            (run_directory / name).write_bytes(body)
        server.run_directory = run_directory
        server.log_lock, server.verify_lock = threading.Lock(), threading.Lock()
        server.verified = {}
        server.static = {"/" + name: (body, Handler.mime(name)) for name, body in bodies.items()}
        server.static["/"] = server.static["/index.html"]
        server.routes = {**media, **vendors}
        server.manifest_body = json_bytes(manifest)
        server.identity = {"app": APP_ID, "version": VERSION, "pid": os.getpid(),
                           "host": "127.0.0.1", "port": server.server_port, "started_at": time.time(),
                           "revision": run, "catalog_revision": 7, "source_sha256": source_sha,
                           "source_pointer_sha256": manifest["source_pointer_sha256"],
                           "manifest_sha256": hashlib.sha256(server.manifest_body).hexdigest(),
                           "vendor": pinned, "model_count": 77, "deployable_count": 75,
                           "run_directory": run_directory.relative_to(ROOT).as_posix()}
        save_json(run_directory / "源码身份.json", {**server.identity, "sources": identity_sources})
        (run_directory / "运行清单.json").write_bytes(server.manifest_body)
        save_json(run_directory / "服务状态.json", server.identity)
        return server
    except Exception:
        server.server_close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="在当前进程持续运行，不打开浏览器")
    parser.add_argument("--port", type=int, default=8809, help="独立回环端口，已占用时拒绝启动")
    args = parser.parse_args()
    if not args.serve:
        parser.error("请显式使用--serve启动；本工具不派生后台服务")
    server = create_server(args.port)
    print(json.dumps(server.identity, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        save_json(server.run_directory / "服务结束.json", {"app": APP_ID, "ended_at": time.time(), "revision": server.identity["revision"]})


if __name__ == "__main__":
    main()
