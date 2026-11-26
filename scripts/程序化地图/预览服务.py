"""B4/B5阶段：支持标准 HTTP Range (206 Partial Content)、内容摘要缓存及散文件/归档双模式的独立回环预览服务 (默认绑定 127.0.0.1:8820)。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B0_DIR = WORK_DIR / "B0-基线冻结"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"
B4_DIR = WORK_DIR / "B4-单包归档"

LOADER_MJS = ROOT / "src/资源加载/压缩模型加载.mjs"
UI_SPEC_DIR = ROOT / "src/界面规范"
UI_SPEC_FILES = {
    "令牌.css": "令牌.css",
    "组件.css": "组件.css",
    "场内视觉.css": "场内视觉.css",
    "图标与标绘.mjs": "图标与标绘.mjs",
    "交互控件.mjs": "交互控件.mjs",
    "规范页.css": "规范页.css",
    "规范页.mjs": "规范页.mjs",
    "favicon.svg": "favicon.svg",
}
UI_FAVICON_FILE = UI_SPEC_DIR / "favicon.svg"
UI_FAVICON_FALLBACK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect width="24" height="24" rx="4" fill="#0d0e13"/>'
    '<path fill="#f0d372" fill-opacity="0.16" stroke="#f0d372" stroke-width="1.6" stroke-linejoin="round" '
    'd="M4.5 5.2 L12 3 L19.5 5.2 V13.2 C19.5 16.2 16.2 18.8 12 20.8 C7.8 18.8 4.5 16.2 4.5 13.2 Z"/>'
    '<path fill="#f0d372" d="M9.1 7.6 L12 6.5 L14.9 7.6 L13.4 10.6 L12 10.0 L10.6 10.6 Z '
    'M17.5 12.1 L17.0 15.1 L14.6 17.0 L13.0 14.3 L14.2 13.4 L14.3 12.0 Z '
    'M6.5 12.1 L9.7 12.0 L9.8 13.4 L11.0 14.3 L9.4 17.0 L7.0 15.1 Z"/>'
    '<path fill="#ffb800" d="M12 10.6 L13.5 12.1 L12 13.6 L10.5 12.1 Z"/></svg>'
).encode("utf-8")


def read_ui_favicon_svg() -> bytes:
    if UI_FAVICON_FILE.is_file():
        return UI_FAVICON_FILE.read_bytes()
    return UI_FAVICON_FALLBACK_SVG
VEHICLES_MAP = {
    "/vehicles/US_M1A2.glb": ROOT / "local-data/资源库/当前资源/模型/地面载具/US_M1A2-M1A2-Abrams/M1A2-Abrams-256默认.glb",
    "/vehicles/US_AH64D.glb": ROOT / "local-data/资源库/当前资源/模型/空中载具/US_AH64D-AH-64D-Apache/AH-64D-Apache-256默认.glb",
    "/vehicles/US_A10.glb": ROOT / "local-data/资源库/当前资源/模型/空中载具/US_A10-A-10C-Thunderbolt-II/A-10C-Thunderbolt-II-256默认.glb",
}

PROTECTED_PORTS = {8767, 8768, 8769, 8780, 8781, 8784, 8811, 8812, 8813, 8814}
RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


class ProceduralMapHTTPHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _resolve_route(self, raw_path: str) -> Path | None:
        if raw_path in ("/", "/index.html", "/预览入口.html"):
            return ROOT / "src/程序化地图/预览入口.html"
        if raw_path in ("/ui-spec", "/ui-spec/"):
            return UI_SPEC_DIR / "index.html"
        if raw_path.startswith("/ui/"):
            rel_name = UI_SPEC_FILES.get(raw_path[len("/ui/") :])
            if rel_name:
                return (UI_SPEC_DIR / rel_name).resolve()
            return None
        if raw_path == "/compressed-loader.mjs":
            return LOADER_MJS
        if raw_path in VEHICLES_MAP:
            return VEHICLES_MAP[raw_path]
        if raw_path.startswith("/vendor/"):
            return (self.server.three_dir / raw_path[len("/vendor/") :]).resolve()
        if raw_path.startswith("/src/"):
            return (ROOT / raw_path.lstrip("/")).resolve()
        if raw_path.startswith("/b0/"):
            return (B0_DIR / raw_path[len("/b0/") :]).resolve()
        if raw_path.startswith("/b1/"):
            return (B1_DIR / raw_path[len("/b1/") :]).resolve()
        if raw_path.startswith("/b2/"):
            return (B2_DIR / raw_path[len("/b2/") :]).resolve()
        if raw_path.startswith("/b3/"):
            return (B3_DIR / raw_path[len("/b3/") :]).resolve()
        if raw_path.startswith("/b4/"):
            return (B4_DIR / raw_path[len("/b4/") :]).resolve()
        return None

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def _serve(self, send_body: bool):
        raw_path = unquote(urlsplit(self.path).path)
        if raw_path == "/healthz":
            payload = json.dumps({"status": "ok", "service": "scavenger-procedural-map-b0-b5", "port": self.server.server_port}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return

        if raw_path == "/favicon.svg":
            fav_bytes = read_ui_favicon_svg()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(fav_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(fav_bytes)
            return
        if raw_path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
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
            ".svg": "image/svg+xml; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".webp": "image/webp",
            ".glb": "model/gltf-binary",
            ".ktx2": "image/ktx2",
            ".wasm": "application/wasm",
            ".scvmap": "application/octet-stream",
            ".scvi": "application/octet-stream",
            ".scvh": "application/octet-stream",
            ".scvp": "application/octet-stream",
            ".scvs": "application/octet-stream",
            ".scvt": "application/octet-stream",
            ".scvb": "application/octet-stream",
            ".scvc": "application/octet-stream",
            ".bin": "application/octet-stream",
        }.get(ext, "application/octet-stream")

        etag = f'W/"{file_size:x}-{st.st_mtime_ns:x}"'
        has_query = bool(urlsplit(self.path).query)
        is_immutable_asset = (
            ext in (".scvmap", ".scvi", ".scvh", ".scvp", ".scvs", ".scvt", ".scvb", ".scvc", ".glb", ".webp", ".png", ".ktx2", ".wasm", ".bin", ".js", ".mjs")
            and (
                raw_path.startswith(("/vendor/", "/vehicles/"))
                or (has_query and raw_path.startswith(("/b0/", "/b1/", "/b2/", "/b3/", "/b4/")))
            )
        )
        cache_ctrl = "public, max-age=31536000, immutable" if is_immutable_asset else "no-cache"

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
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(256 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            return

        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("Cache-Control", cache_ctrl)
            self.send_header("ETag", etag)
            self.end_headers()
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


def create_server(port: int = 8820, verbose: bool = False) -> ThreadingHTTPServer:
    if port in PROTECTED_PORTS:
        raise ValueError(f"严禁绑定受保护的既有服务端口: {port}")
    pin = json.loads((ROOT / "local-data/tools/three/当前版本.json").read_text("utf-8"))
    three_dir = ROOT / "local-data/tools/three" / pin["version"]

    server = ThreadingHTTPServer(("127.0.0.1", port), ProceduralMapHTTPHandler)
    server.three_dir = three_dir
    server.verbose = verbose
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="程序化大地图与分级平滑过渡独立预览服务")
    parser.add_argument("--port", type=int, default=8820, help="独立回环端口 (默认 8820)")
    parser.add_argument("--verbose", action="store_true", help="输出 HTTP 请求日志")
    args = parser.parse_args()

    server = create_server(port=args.port, verbose=args.verbose)
    print(json.dumps({
        "service": "scavenger-procedural-map-preview",
        "host": "127.0.0.1",
        "port": args.port,
        "render_baseline": "stylized-680p-v1",
        "url_play_16x9": f"http://127.0.0.1:{args.port}/?aspect=16:9",
        "url_play_21x9": f"http://127.0.0.1:{args.port}/?aspect=21:9",
        "url_editor": f"http://127.0.0.1:{args.port}/?mode=editor",
    }, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
