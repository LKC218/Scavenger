"""V1–V3 离线编译与回读脚本专用临时只读 Range 回环服务（正式武器库与混编场已统一挂载至 127.0.0.1:8800）。"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from 公共配置 import (
    BATCH_ID,
    DEFAULT_PORT,
    ORIGINALS_CACHE_DIR,
    PROTECTED_PORTS,
    REPORT_DIR,
    ROOT,
    THREE_DIR,
    V0_DIR,
    V1_DIR,
    V2_DIR,
    V3_DIR,
)

RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")
UI_SPEC_DIR = ROOT / "src" / "界面规范"
FORMAL_VEH_MODEL_DIR = ROOT / "local-data" / "资源库" / "当前资源" / "模型"
GZIP_EXTS = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".wasm"}
_GZIP_CACHE: dict[tuple[str, int], bytes] = {}


def get_gzipped_bytes(file_path: Path, mtime_ns: int) -> bytes:
    k = (str(file_path), mtime_ns)
    cached = _GZIP_CACHE.get(k)
    if cached is not None:
        return cached
    data = gzip.compress(file_path.read_bytes(), compresslevel=6)
    if len(_GZIP_CACHE) > 128:
        _GZIP_CACHE.clear()
    _GZIP_CACHE[k] = data
    return data


def safe_under(base: Path, rel: str) -> Path | None:
    try:
        p = (base / rel.lstrip("/")).resolve()
        if not p.is_relative_to(base.resolve()):
            return None
        return p
    except Exception:
        return None


class ProceduralVehicleHTTPHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _resolve_route(self, raw_path: str) -> Path | None:
        if raw_path in ("/", "/index.html", "/battlefield", "/混编演训场.html"):
            return ROOT / "src/程序化载具/混编演训场.html"
        if raw_path in ("/report", "/验收报告"):
            return REPORT_DIR / "程序化载具优化与视觉验收报告-V001.html"
        if raw_path == "/compressed-loader.mjs":
            return ROOT / "src/资源加载/压缩模型加载.mjs"
        if raw_path.startswith("/ui/"):
            return safe_under(UI_SPEC_DIR, raw_path[len("/ui/") :])
        if raw_path.startswith("/vendor/"):
            return safe_under(THREE_DIR, raw_path[len("/vendor/") :])
        if raw_path.startswith("/src/"):
            return safe_under(ROOT / "src", raw_path[len("/src/") :])
        if raw_path.startswith("/v0/"):
            return safe_under(V0_DIR, raw_path[len("/v0/") :])
        if raw_path.startswith("/v2/"):
            return safe_under(V2_DIR, raw_path[len("/v2/") :])
        if raw_path.startswith("/v3/"):
            return safe_under(V3_DIR, raw_path[len("/v3/") :])
        if raw_path.startswith("/reports/"):
            return safe_under(REPORT_DIR, raw_path[len("/reports/") :])
        if raw_path.startswith("/assets/"):
            return safe_under(REPORT_DIR / "assets", raw_path[len("/assets/") :])
        if raw_path.startswith("/orig/"):
            return safe_under(ORIGINALS_CACHE_DIR, raw_path[len("/orig/") :])
        return None

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def _serve(self, send_body: bool):
        raw_path = unquote(urlsplit(self.path).path)
        if raw_path == "/healthz":
            payload = json.dumps(
                {
                    "status": "ok",
                    "service": "scavenger-procedural-vehicles-v001",
                    "batch_id": BATCH_ID,
                    "port": self.server.server_port,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return

        file_path = self._resolve_route(raw_path)
        if file_path is None or not file_path.is_file():
            self.send_error(404, f"Not found: {raw_path}")
            return

        st = file_path.stat()
        file_size = st.st_size
        ext = file_path.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".glb": "model/gltf-binary",
            ".ktx2": "image/ktx2",
            ".wasm": "application/wasm",
            ".scvveh": "application/octet-stream",
            ".scvi": "application/octet-stream",
            ".bin": "application/octet-stream",
        }.get(ext, "application/octet-stream")

        etag = f'W/"{file_size:x}-{st.st_mtime_ns:x}"'
        is_immutable = (
            raw_path.startswith(("/vendor/", "/v3/", "/orig/"))
            and ext in (".scvveh", ".scvi", ".glb", ".ktx2", ".wasm", ".js", ".mjs", ".png")
        )
        cache_ctrl = "public, max-age=31536000, immutable" if is_immutable else "no-cache"

        range_header = self.headers.get("Range")
        if range_header:
            m = RANGE_RE.match(range_header.strip())
            if not m:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            if start >= file_size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache_ctrl)
            self.send_header("ETag", etag)
            self.end_headers()
            if send_body:
                with file_path.open("rb") as f:
                    f.seek(start)
                    rem = length
                    while rem > 0:
                        chunk = f.read(min(256 * 1024, rem))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        rem -= len(chunk)
            return

        accept_enc = self.headers.get("Accept-Encoding", "")
        if ext in GZIP_EXTS and "gzip" in accept_enc and file_size >= 1024:
            gz_data = get_gzipped_bytes(file_path, st.st_mtime_ns)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(gz_data)))
            self.send_header("Cache-Control", cache_ctrl)
            self.send_header("ETag", etag)
            self.end_headers()
            if send_body:
                self.wfile.write(gz_data)
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", cache_ctrl)
        self.send_header("ETag", etag)
        self.end_headers()
        if send_body:
            with file_path.open("rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)


def start_server(port: int, verbose: bool = False) -> ThreadingHTTPServer:
    if port in PROTECTED_PORTS:
        raise ValueError(f"端口 {port} 属于受保护历史/并行端口，禁止占用！")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise OSError(f"端口 127.0.0.1:{port} 已被占用，拒绝覆盖或终止其他服务。")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ProceduralVehicleHTTPHandler)
    httpd.verbose = verbose
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    httpd = start_server(args.port, verbose=args.verbose)
    print(f"[载具预览服务] 已启动只读回环服务: http://127.0.0.1:{args.port}/ (混编场 /battlefield · 报告 /report)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
