"""启动或复用本机 127.0.0.1:8766 全局 UI/UX 活体规范路由，仅以白名单方式提供 src/界面规范/ 共享文件。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = ROOT / "src/界面规范"
OUTPUT = ROOT / "local-data/界面规范/V001-d6febf34"
PORT = 8766
HOST = "127.0.0.1"
APP = "scavenger-ui-ux-live-spec"
TASK = "全局UI规范路由服务-8766-V001-d6febf34"

STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/ui-spec": "index.html",
    "/ui/令牌.css": "令牌.css",
    "/ui/组件.css": "组件.css",
    "/ui/场内视觉.css": "场内视觉.css",
    "/ui/图标与标绘.mjs": "图标与标绘.mjs",
    "/ui/交互控件.mjs": "交互控件.mjs",
    "/ui/规范页.css": "规范页.css",
    "/ui/规范页.mjs": "规范页.mjs",
}

FAVICON_FILE = SPEC_DIR / "favicon.svg"
FAVICON_FALLBACK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect width="24" height="24" rx="4" fill="#0d0e13"/>'
    '<path fill="#f0d372" fill-opacity="0.16" stroke="#f0d372" stroke-width="1.6" stroke-linejoin="round" '
    'd="M4.5 5.2 L12 3 L19.5 5.2 V13.2 C19.5 16.2 16.2 18.8 12 20.8 C7.8 18.8 4.5 16.2 4.5 13.2 Z"/>'
    '<path fill="#f0d372" d="M9.1 7.6 L12 6.5 L14.9 7.6 L13.4 10.6 L12 10.0 L10.6 10.6 Z '
    'M17.5 12.1 L17.0 15.1 L14.6 17.0 L13.0 14.3 L14.2 13.4 L14.3 12.0 Z '
    'M6.5 12.1 L9.7 12.0 L9.8 13.4 L11.0 14.3 L9.4 17.0 L7.0 15.1 Z"/>'
    '<path fill="#ffb800" d="M12 10.6 L13.5 12.1 L12 13.6 L10.5 12.1 Z"/></svg>'
).encode("utf-8")


def read_favicon_svg() -> bytes:
    if FAVICON_FILE.is_file():
        return FAVICON_FILE.read_bytes()
    return FAVICON_FALLBACK_SVG

mimetypes.add_type("application/javascript; charset=utf-8", ".mjs")
mimetypes.add_type("text/css; charset=utf-8", ".css")


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def collect_source_identity() -> tuple[dict[str, str], str]:
    tracked = sorted(set(STATIC_ROUTES.values()))
    files: dict[str, str] = {}
    for name in tracked:
        file_path = SPEC_DIR / name
        if not file_path.is_file():
            raise FileNotFoundError(f"缺少 UI 规范共享源文件：{file_path.relative_to(ROOT).as_posix()}")
        files[name] = sha256_file(file_path)
    digest = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return files, digest


def query_status() -> dict | None:
    try:
        with urlopen(f"http://{HOST}:{PORT}/api/status", timeout=2) as response:
            return json.load(response)
    except (OSError, URLError):
        return None


def register_service_protection() -> None:
    command = [
        sys.executable, "-X", "utf8", "-B",
        str(ROOT / "scripts/维护清理/清理工具.py"),
        "protect", "--task", TASK,
        "--path", "src/界面规范",
        "--path", "scripts/界面规范/启动规范路由.py",
        "--path", "local-data/界面规范/V001-d6febf34",
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def verify_running_identity(identity: dict, expected_digest: str) -> dict:
    if identity.get("app") != APP or identity.get("port") != PORT:
        raise RuntimeError(f"端口 {PORT} 已被其他应用占用，保留现场未强行接管")
    if identity.get("source_sha256") != expected_digest:
        raise RuntimeError(
            f"端口 {PORT} 正在运行旧版规范路由缓存或源码已变化，请先核对 PID={identity.get('pid')} 后再重新启动"
        )
    return identity


class SpecHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self._serve(head_only=True)

    def do_GET(self) -> None:
        self._serve(head_only=False)

    def log_message(self, format: str, *args) -> None:
        pass

    def _send_bytes(self, code: int, content_type: str, body: bytes, head_only: bool) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve(self, head_only: bool) -> None:
        raw_path = urlsplit(self.path).path
        try:
            route = unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            self.send_error(400)
            return

        if route in ("/favicon.svg", "/ui/favicon.svg"):
            self._send_bytes(200, "image/svg+xml; charset=utf-8", read_favicon_svg(), head_only)
            return
        if route == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if route == "/api/status":
            files, digest = collect_source_identity()
            payload = {
                "app": APP,
                "port": PORT,
                "pid": os.getpid(),
                "source_sha256": digest,
                "files": files,
                "launcher_sha256": sha256_file(Path(__file__)),
                "started_at": self.server.started_at,
            }
            self._send_bytes(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                head_only,
            )
            return

        rel_name = STATIC_ROUTES.get(route)
        if not rel_name:
            self.send_error(404)
            return
        target = (SPEC_DIR / rel_name).resolve()
        if not target.is_relative_to(SPEC_DIR.resolve()) or not target.is_file():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(target.name)
        if not mime:
            mime = "application/octet-stream"
        elif mime.startswith("text/") and "charset" not in mime:
            mime = f"{mime}; charset=utf-8"
        self._send_bytes(200, mime, target.read_bytes(), head_only)


def serve_foreground() -> None:
    collect_source_identity()
    server = ThreadingHTTPServer((HOST, PORT), SpecHandler)
    server.daemon_threads = True
    server.started_at = datetime.now(timezone.utc).isoformat()
    print(f"全局 UI/UX 活体规范路由已启动：http://{HOST}:{PORT}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def start_background() -> dict:
    _, expected_digest = collect_source_identity()
    current = query_status()
    if current:
        return verify_running_identity(current, expected_digest)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    register_service_protection()

    lock = OUTPUT / "启动锁"
    deadline = time.monotonic() + 30.0
    wait_event = threading.Event()
    while True:
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            break
        except FileExistsError:
            current = query_status()
            if current:
                return verify_running_identity(current, expected_digest)
            if time.monotonic() >= deadline:
                raise RuntimeError("存在未释放的启动锁，保留现场未强行接管，请核对锁内 PID")
            wait_event.wait(0.15)

    try:
        current = query_status()
        if current:
            return verify_running_identity(current, expected_digest)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        log_path = OUTPUT / f"服务-{stamp}.log"
        command = [sys.executable, "-X", "utf8", "-B", str(Path(__file__)), "--serve"]
        with log_path.open("xb") as stream:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=stream,
                stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        while time.monotonic() < deadline:
            current = query_status()
            if current:
                identity = verify_running_identity(current, expected_digest)
                (OUTPUT / "服务状态.json").write_text(
                    json.dumps(
                        {
                            **identity,
                            "url": f"http://{HOST}:{PORT}/",
                            "ui_spec_url": f"http://{HOST}:{PORT}/ui-spec",
                            "log": log_path.relative_to(ROOT).as_posix(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )
                return identity
            if process.poll() is not None:
                raise RuntimeError(f"规范路由后台启动失败，请查看日志：{log_path.relative_to(ROOT).as_posix()}")
            wait_event.wait(0.15)
        raise RuntimeError("规范路由启动超时，请查看日志定位问题")
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == str(os.getpid()):
            lock.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", action="store_true", help="检查、保护并在后台启动或复用 8766 规范路由")
    mode.add_argument("--serve", action="store_true", help="在前台直接运行 8766 规范路由服务")
    mode.add_argument("--status", action="store_true", help="只读查询 8766 规范路由当前运行状态")
    args = parser.parse_args()

    if args.serve:
        serve_foreground()
        return 0
    if args.status:
        current = query_status()
        print(json.dumps({"running": bool(current), "identity": current}, ensure_ascii=False, indent=2))
        return 0 if current else 1

    identity = start_background()
    print(json.dumps({"url": f"http://{HOST}:{PORT}/", **identity}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
