"""本地工作项看板独立服务（专属端口 18800，与 8800 游戏主服进程与数据彻底拆分）。

特性：
- 专属端口锁定 18800（绝不自动飘移端口，外部占口仅只读诊断不强杀）；
- 单人模式默认监听 127.0.0.1:18800（auth_mode=local_single，免登录零摩擦）；
- 局域网模式（--lan）监听 0.0.0.0:18800（auth_mode=lan_pin，人类 PIN Cookie + AI Agent X-Board-Token 鉴权）；
- 提供全套 /api/v1/* REST 接口、/api/v1/events SSE 多端实时同步与 SHA-256 去重附件托管；
- 支持前台常驻（关窗停服）以及 start / open / stop / restart / status / logs 生命周期控制。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from 数据库 import (  # noqa: E402
    BoardError,
    BoardStore,
    DEFAULT_MODULE_CATEGORIES,
    PRIORITIES,
    STATES,
    ValidationError,
    utc_now_iso,
)

ROOT = SCRIPT_DIR.parents[1]
FRONTEND_DIR = ROOT / "src" / "工作项看板"
UI_SPEC_DIR = ROOT / "src" / "界面规范"
DEFAULT_DATA_DIR = ROOT / "local-data" / "工作项看板"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18800
SERVICE_NAME = "scavenger-workboard"
SERVICE_VERSION = "V001"
SHUTDOWN_PATH = "/__internal/shutdown"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
LOG_MAX_BYTES = 512 * 1024

_NO_PROXY_OPENER = build_opener(ProxyHandler({}))
_WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_ATT_NAME_RE = re.compile(r"^([0-9a-f]{64})(\.[a-z0-9]{1,8})$")

ALLOWED_UPLOAD_EXTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
}
MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/pdf": ".pdf",
}

STATIC_ALLOW = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("样式.css", "text/css; charset=utf-8"),
    "/app.mjs": ("应用.mjs", "text/javascript; charset=utf-8"),
}

# 只读复用项目 Code SSOT V2.2 共享界面规范源（18800 仍保持完全独立进程，不依赖 8800）
UI_STATIC_ALLOW = {
    "/ui/令牌.css": ("令牌.css", "text/css; charset=utf-8"),
    "/ui/组件.css": ("组件.css", "text/css; charset=utf-8"),
    "/ui/交互控件.mjs": ("交互控件.mjs", "text/javascript; charset=utf-8"),
    "/ui/图标与标绘.mjs": ("图标与标绘.mjs", "text/javascript; charset=utf-8"),
    "/ui/favicon.svg": ("favicon.svg", "image/svg+xml; charset=utf-8"),
}

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect width="24" height="24" rx="5" fill="#0d0e13"/>'
    '<path d="M4.5 5.2 L12 3 L19.5 5.2 V13.2 C19.5 16.2 16.2 18.8 12 20.8 C7.8 18.8 4.5 16.2 4.5 13.2 Z" fill="none" stroke="#f0d372" stroke-width="1.6"/>'
    "</svg>"
).encode("utf-8")


def compute_data_dir_sha(data_dir: Path) -> str:
    return hashlib.sha256(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:16]


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def runtime_dir(data_dir: Path) -> Path:
    return data_dir / "运行状态"


def state_file_path(data_dir: Path, port: int) -> Path:
    return runtime_dir(data_dir) / f"server-{port}.json"


def log_file_path(data_dir: Path, port: int) -> Path:
    return runtime_dir(data_dir) / f"workboard-{port}.log"


def session_secret_path(data_dir: Path) -> Path:
    return runtime_dir(data_dir) / "session-secret.key"


def load_or_create_session_secret(data_dir: Path) -> bytes:
    path = session_secret_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        raw = path.read_bytes().strip()
        if len(raw) >= 32:
            return raw
    secret = secrets.token_hex(32).encode("ascii")
    path.write_bytes(secret)
    return secret


def sign_session(member_id: int, secret: bytes) -> str:
    issued = int(time.time())
    payload = f"{member_id}.{issued}"
    sig = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session(token: str, secret: bytes, max_age_sec: int = 30 * 86400) -> int | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    mid_s, ts_s, sig = parts
    if not mid_s.isdigit() or not ts_s.isdigit():
        return None
    if int(time.time()) - int(ts_s) > max_age_sec:
        return None
    expected = hmac.new(secret, f"{mid_s}.{ts_s}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return int(mid_s)


def query_healthz(port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    req = Request(
        f"http://127.0.0.1:{port}/healthz",
        headers={"Cache-Control": "no-cache", "Connection": "close"},
    )
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.load(resp)
            return data if isinstance(data, dict) else None
    except (URLError, ValueError, OSError, TimeoutError):
        return None


def is_port_open(port: int, timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_listening_pids(port: int) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=_WIN_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    seen: set[int] = set()
    pstr = str(port)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local_addr, _, st, pid_raw = parts[0].upper(), parts[1], parts[2], parts[3].upper(), parts[-1]
        if proto not in ("TCP", "TCPV6") or st != "LISTENING" or ":" not in local_addr:
            continue
        _, addr_port = local_addr.rsplit(":", 1)
        if addr_port == pstr and pid_raw.isdigit():
            pid = int(pid_raw)
            if pid > 0 and pid not in seen:
                seen.add(pid)
                pids.append(pid)
    return pids


def inspect_pid(pid: int) -> dict[str, Any]:
    info = {"pid": pid, "name": "", "command_line": ""}
    if sys.platform != "win32":
        return info
    cmd = f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object Name,CommandLine | ConvertTo-Json -Compress"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            creationflags=_WIN_NO_WINDOW,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            if isinstance(data, dict):
                info["name"] = data.get("Name") or ""
                info["command_line"] = data.get("CommandLine") or ""
    except Exception:
        pass
    return info


class EventBroker:
    """轻量线程安全 SSE 事件广播器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: set[queue.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    def close_all(self) -> None:
        self.publish({"type": "__shutdown__"})


class WorkboardServer(ThreadingHTTPServer):
    allow_reuse_address = False if sys.platform == "win32" else True
    daemon_threads = True

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        store: BoardStore,
        data_dir: Path,
        host: str,
        auth_mode: str,
        control_token: str,
        session_secret: bytes,
        run_mode: str = "foreground",
        verbose: bool = False,
    ):
        self.store = store
        self.data_dir = data_dir.resolve()
        self.attachments_dir = self.data_dir / "附件"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.bind_host = host
        self.auth_mode = auth_mode
        self.control_token = control_token
        self.session_secret = session_secret
        self.run_mode = run_mode
        self.data_dir_sha = compute_data_dir_sha(self.data_dir)
        self.started_at = utc_now_iso()
        self.started_mono = time.monotonic()
        self.verbose = verbose
        self.broker = EventBroker()
        self.lan_ip = get_lan_ip()
        super().__init__(server_address, WorkboardHandler)


class WorkboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: WorkboardServer

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.verbose:
            sys.stderr.write(f"[workboard] {self.address_string()} - {format % args}\n")

    def _send_bytes(self, status: int, body: bytes, content_type: str, *, extra_headers: dict[str, str] | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_json(self, status: int, data: Any, *, extra_headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {"Cache-Control": "no-store"}
        if extra_headers:
            headers.update(extra_headers)
        self._send_bytes(status, raw, "application/json; charset=utf-8", extra_headers=headers)

    def _check_csrf_headers(self) -> None:
        origin = (self.headers.get("Origin") or "").strip()
        if origin:
            host_hdr = (self.headers.get("Host") or "").strip().lower()
            parsed_origin = urlsplit(origin)
            if parsed_origin.netloc.lower() != host_hdr:
                raise BoardError("跨站请求被拒绝", status_code=403, error_code="csrf_origin_mismatch")

    def _read_json_body(self, max_bytes: int = MAX_UPLOAD_BYTES + 4 * 1024 * 1024) -> dict[str, Any]:
        self._check_csrf_headers()
        cl_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(cl_raw)
        except ValueError:
            raise ValidationError("无效的 Content-Length")
        if length < 0 or length > max_bytes:
            raise ValidationError("请求体体积超出允许上限")
        if length == 0:
            return {}
        ctype = (self.headers.get("Content-Type") or "").strip().lower()
        if ctype and not ctype.startswith("application/json"):
            raise ValidationError("写接口 Content-Type 必须为 application/json")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"JSON 解析失败：{exc}")
        if not isinstance(data, dict):
            raise ValidationError("请求体必须为 JSON 对象")
        return data

    def _resolve_actor(self, query_params: dict[str, list[str]] | None = None) -> tuple[dict[str, Any] | None, str]:
        """解析当前操作者身份（优先级：X-Board-Token > Cookie > 单人模式默认/视角切换）。"""
        # 1. AI Agent 或脚本 Token
        token = (self.headers.get("X-Board-Token") or "").strip()
        if not token and query_params and query_params.get("token"):
            token = query_params["token"][0].strip()
        if token:
            member = self.server.store.get_member_by_token(token)
            if member:
                return member, "token"
            return None, "invalid_token"

        # 2. 浏览器登录 Cookie
        cookie_hdr = self.headers.get("Cookie", "")
        if cookie_hdr:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_hdr)
                morsel = c.get("lb_session")
                if morsel:
                    mid = verify_session(morsel.value, self.server.session_secret)
                    if mid is not None:
                        member = self.server.store.get_member_by_id(mid)
                        if member:
                            return member, "session"
            except Exception:
                pass

        # 3. 单人本机模式默认免登（允许前端通过 X-Actor-Id 切换视角）
        if self.server.auth_mode == "local_single":
            actor_hdr = (self.headers.get("X-Actor-Id") or "").strip()
            if actor_hdr.isdigit():
                m = self.server.store.get_member_by_id(int(actor_hdr))
                if m:
                    return m, "local_switch"
            return self.server.store.get_default_human(), "local_default"

        return None, "unauthenticated"

    def _require_auth(self, query_params: dict[str, list[str]] | None = None) -> dict[str, Any] | None:
        actor, source = self._resolve_actor(query_params)
        if source == "invalid_token":
            self._send_json(401, {"ok": False, "error": "invalid_token", "message": "无效的 X-Board-Token"})
            return None
        if actor is None and self.server.auth_mode == "lan_pin":
            self._send_json(401, {"ok": False, "error": "auth_required", "message": "局域网团队模式已启用，请先选择成员并输入 PIN 登录"})
            return None
        return actor

    def _broadcast(self, seq: int, event_type: str, *, project_id: int | None = None, item_key: str = "") -> None:
        self.server.broker.publish({
            "type": "board_update",
            "seq": seq,
            "event_type": event_type,
            "project_id": project_id,
            "item_key": item_key,
            "ts": utc_now_iso(),
        })

    # ---------- HTTP 方法分发 ----------

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            self._handle_get(path, qs)
        except BoardError as exc:
            self._send_json(exc.status_code, {"ok": False, "error": exc.error_code, "message": exc.message, **exc.payload})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "internal_error", "message": str(exc)})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            self._handle_post(path, qs)
        except BoardError as exc:
            self._send_json(exc.status_code, {"ok": False, "error": exc.error_code, "message": exc.message, **exc.payload})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "internal_error", "message": str(exc)})

    def do_PATCH(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            self._handle_patch(path, qs)
        except BoardError as exc:
            self._send_json(exc.status_code, {"ok": False, "error": exc.error_code, "message": exc.message, **exc.payload})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "internal_error", "message": str(exc)})

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            self._handle_delete(path, qs)
        except BoardError as exc:
            self._send_json(exc.status_code, {"ok": False, "error": exc.error_code, "message": exc.message, **exc.payload})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "internal_error", "message": str(exc)})

    # ---------- GET 路由 ----------

    def _handle_get(self, path: str, qs: dict[str, list[str]]) -> None:
        if path == "/healthz":
            self._send_json(200, {
                "status": "ok",
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "pid": os.getpid(),
                "host": self.server.bind_host,
                "port": self.server.server_port,
                "auth_mode": self.server.auth_mode,
                "run_mode": self.server.run_mode,
                "lan_ip": self.server.lan_ip,
                "data_dir_sha256": self.server.data_dir_sha,
                "started_at": self.server.started_at,
                "uptime_seconds": round(time.monotonic() - self.server.started_mono, 1),
            })
            return

        if path == "/favicon.svg":
            fav_fp = UI_SPEC_DIR / "favicon.svg"
            fav_bytes = fav_fp.read_bytes() if fav_fp.is_file() else FAVICON_SVG
            self._send_bytes(200, fav_bytes, "image/svg+xml; charset=utf-8", extra_headers={"Cache-Control": "public, max-age=3600"})
            return

        if path in UI_STATIC_ALLOW:
            fname, ctype = UI_STATIC_ALLOW[path]
            fp = UI_SPEC_DIR / fname
            if not fp.is_file():
                self._send_json(404, {"ok": False, "error": "missing_ui_spec_asset", "message": f"缺少界面规范共享文件：{fname}"})
                return
            self._send_bytes(200, fp.read_bytes(), ctype, extra_headers={"Cache-Control": "no-cache"})
            return

        if path in STATIC_ALLOW:
            fname, ctype = STATIC_ALLOW[path]
            fp = FRONTEND_DIR / fname
            if not fp.is_file():
                self._send_json(404, {"ok": False, "error": "missing_frontend", "message": f"缺少前端文件：{fname}"})
                return
            self._send_bytes(200, fp.read_bytes(), ctype, extra_headers={"Cache-Control": "no-cache"})
            return

        if path == "/api/v1/auth/state":
            actor, source = self._resolve_actor(qs)
            humans = [m for m in self.server.store.list_members() if m["kind"] == "human"]
            self._send_json(200, {
                "ok": True,
                "auth_mode": self.server.auth_mode,
                "authenticated": actor is not None,
                "auth_source": source,
                "current_member": actor,
                "human_members": humans,
            })
            return

        # 附件静态分发
        if path.startswith("/api/v1/attachments/"):
            actor = self._require_auth(qs)
            if actor is None and self.server.auth_mode == "lan_pin":
                return
            name = path[len("/api/v1/attachments/"):]
            m = _ATT_NAME_RE.fullmatch(name)
            if not m:
                self._send_json(400, {"ok": False, "error": "invalid_attachment", "message": "非法附件标识"})
                return
            fp = self.server.attachments_dir / name
            if not fp.is_file():
                self._send_json(404, {"ok": False, "error": "not_found", "message": "附件不存在"})
                return
            ext = m.group(2)
            ctype = ALLOWED_UPLOAD_EXTS.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
            att_headers = {"Cache-Control": "public, max-age=31536000, immutable"}
            if ext in (".svg", ".html", ".htm") or not ctype.startswith("image/") or ctype == "image/svg+xml":
                att_headers["Content-Disposition"] = f'attachment; filename="{name}"'
                att_headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            self._send_bytes(200, fp.read_bytes(), ctype, extra_headers=att_headers)
            return

        # SSE 事件流
        if path == "/api/v1/events":
            actor = self._require_auth(qs)
            if actor is None and self.server.auth_mode == "lan_pin":
                return
            if self.command == "HEAD":
                self._send_bytes(200, b"", "text/event-stream; charset=utf-8", extra_headers={"Cache-Control": "no-cache"})
                return
            self._serve_sse()
            return

        # 以下均需鉴权
        actor = self._require_auth(qs)
        if actor is None and self.server.auth_mode == "lan_pin":
            return

        if path == "/api/v1/bootstrap":
            projects = self.server.store.list_projects()
            include_tok = bool(actor and actor.get("kind") == "human")
            members = self._filter_member_tokens(self.server.store.list_members(include_tokens=include_tok), actor)
            self._send_json(200, {
                "ok": True,
                "service": {
                    "name": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "host": self.server.bind_host,
                    "port": self.server.server_port,
                    "lan_ip": self.server.lan_ip,
                    "auth_mode": self.server.auth_mode,
                    "local_url": f"http://127.0.0.1:{self.server.server_port}",
                    "lan_url": f"http://{self.server.lan_ip}:{self.server.server_port}",
                },
                "current_member": actor,
                "states": STATES,
                "priorities": PRIORITIES,
                "module_categories": DEFAULT_MODULE_CATEGORIES,
                "projects": projects,
                "members": members,
                "latest_seq": self.server.store.latest_event_seq(),
            })
            return

        if path == "/api/v1/projects":
            self._send_json(200, {"ok": True, "projects": self.server.store.list_projects()})
            return

        if path == "/api/v1/members":
            include_tok = bool(actor and actor.get("kind") == "human")
            members = self._filter_member_tokens(self.server.store.list_members(include_tokens=include_tok), actor)
            self._send_json(200, {"ok": True, "members": members})
            return

        m_proj_items = re.fullmatch(r"/api/v1/projects/([^/]+)/items", path)
        if m_proj_items:
            proj = self.server.store.resolve_project(m_proj_items.group(1))
            data = self.server.store.list_items(proj["id"])
            modules = self.server.store.list_modules(proj["id"])
            labels = self.server.store.list_labels(proj["id"])
            self._send_json(200, {"ok": True, "project": proj, "modules": modules, "labels": labels, **data})
            return

        m_proj_mods = re.fullmatch(r"/api/v1/projects/([^/]+)/modules", path)
        if m_proj_mods:
            proj = self.server.store.resolve_project(m_proj_mods.group(1))
            self._send_json(200, {"ok": True, "modules": self.server.store.list_modules(proj["id"])})
            return

        m_item = re.fullmatch(r"/api/v1/items/([^/]+)", path)
        if m_item:
            item = self.server.store.get_item_detail(m_item.group(1))
            self._send_json(200, {"ok": True, "item": item})
            return

        self._send_json(404, {"ok": False, "error": "not_found", "message": f"未知接口：{path}"})

    def _require_human(self, actor: dict[str, Any] | None) -> bool:
        if actor and actor.get("kind") == "human":
            return True
        self._send_json(403, {"ok": False, "error": "forbidden", "message": "该管理操作仅限人类成员执行"})
        return False

    def _filter_member_tokens(self, members: list[dict[str, Any]], actor: dict[str, Any] | None) -> list[dict[str, Any]]:
        is_human = bool(actor and actor.get("kind") == "human")
        actor_id = actor.get("id") if actor else None
        out = []
        for m in members:
            row = dict(m)
            # 仅允许人类查看 AI Agent Token 与本人 Token，避免泄露其他人类成员明文 Token
            if not is_human or (row.get("kind") == "human" and row.get("id") != actor_id):
                row.pop("api_token", None)
            out.append(row)
        return out

    def _serve_sse(self) -> None:
        self.close_connection = True
        q = self.server.broker.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            init_payload = json.dumps({"seq": self.server.store.latest_event_seq(), "ts": utc_now_iso()}, ensure_ascii=False)
            self.wfile.write(f"event: connected\ndata: {init_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15.0)
                    if ev.get("type") == "__shutdown__":
                        break
                    data_str = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"event: board_update\ndata: {data_str}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.server.broker.unsubscribe(q)

    # ---------- POST 路由 ----------

    def _handle_post(self, path: str, qs: dict[str, list[str]]) -> None:
        if path == SHUTDOWN_PATH:
            token = (self.headers.get("X-Control-Token") or "").strip()
            if self.client_address[0] not in ("127.0.0.1", "::1") or not hmac.compare_digest(token, self.server.control_token):
                self._send_json(403, {"ok": False, "error": "forbidden", "message": "关停口令无效或来源非法"})
                return
            self._send_json(200, {"ok": True, "shutting_down": True})
            self.server.broker.close_all()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if path == "/api/v1/auth/login":
            body = self._read_json_body()
            mid = body.get("member_id")
            pin = str(body.get("pin") or "")
            if not mid:
                raise ValidationError("请选择成员")
            member = self.server.store.authenticate_pin(int(mid), pin)
            if not member:
                self._send_json(401, {"ok": False, "error": "invalid_pin", "message": "口令错误或成员无效"})
                return
            token = sign_session(member["id"], self.server.session_secret)
            cookie_val = f"lb_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={30 * 86400}"
            self._send_json(200, {"ok": True, "member": member}, extra_headers={"Set-Cookie": cookie_val})
            return

        if path == "/api/v1/auth/logout":
            self._send_json(200, {"ok": True}, extra_headers={"Set-Cookie": "lb_session=; Path=/; HttpOnly; Max-Age=0"})
            return

        actor = self._require_auth(qs)
        if actor is None and self.server.auth_mode == "lan_pin":
            return
        actor_id = actor["id"] if actor else None

        if path == "/api/v1/projects":
            if not self._require_human(actor):
                return
            body = self._read_json_body()
            proj, seq = self.server.store.create_project(body, actor_id)
            self._broadcast(seq, "project_created", project_id=proj["id"])
            self._send_json(201, {"ok": True, "project": proj})
            return

        if path == "/api/v1/members":
            if not self._require_human(actor):
                return
            body = self._read_json_body()
            member, seq = self.server.store.create_member(body, actor_id)
            self._broadcast(seq, "member_created")
            self._send_json(201, {"ok": True, "member": member})
            return

        m_proj_sync = re.fullmatch(r"/api/v1/projects/([^/]+)/modules/sync-presets", path)
        if m_proj_sync:
            proj = self.server.store.resolve_project(m_proj_sync.group(1))
            summary, seq = self.server.store.sync_preset_modules(proj["id"], actor_id)
            self._broadcast(seq, "modules_preset_synced", project_id=proj["id"])
            self._send_json(200, {"ok": True, **summary})
            return

        m_proj_mods = re.fullmatch(r"/api/v1/projects/([^/]+)/modules", path)
        if m_proj_mods:
            proj = self.server.store.resolve_project(m_proj_mods.group(1))
            body = self._read_json_body()
            mod, seq = self.server.store.create_module(proj["id"], body, actor_id)
            self._broadcast(seq, "module_created", project_id=proj["id"])
            self._send_json(201, {"ok": True, "module": mod})
            return

        m_proj_labels = re.fullmatch(r"/api/v1/projects/([^/]+)/labels", path)
        if m_proj_labels:
            proj = self.server.store.resolve_project(m_proj_labels.group(1))
            body = self._read_json_body()
            lb, seq = self.server.store.create_label(proj["id"], body, actor_id)
            self._broadcast(seq, "label_created", project_id=proj["id"])
            self._send_json(201, {"ok": True, "label": lb})
            return

        m_proj_items = re.fullmatch(r"/api/v1/projects/([^/]+)/items", path)
        if m_proj_items:
            proj = self.server.store.resolve_project(m_proj_items.group(1))
            body = self._read_json_body()
            item, seq = self.server.store.create_item(proj["id"], body, actor_id)
            self._broadcast(seq, "item_created", project_id=proj["id"], item_key=item["item_key"])
            self._send_json(201, {"ok": True, "item": item})
            return

        m_trans = re.fullmatch(r"/api/v1/items/([^/]+)/transition", path)
        if m_trans:
            body = self._read_json_body()
            action = body.get("action") or ""
            item, seq = self.server.store.transition_item(m_trans.group(1), action, body, actor_id)
            self._broadcast(seq, f"item_{action}", project_id=item["project_id"], item_key=item["item_key"])
            self._send_json(200, {"ok": True, "item": item})
            return

        m_hb = re.fullmatch(r"/api/v1/items/([^/]+)/heartbeat", path)
        if m_hb:
            body = self._read_json_body()
            item, seq = self.server.store.heartbeat_item(m_hb.group(1), body, actor_id)
            self._broadcast(seq, "item_heartbeat", project_id=item["project_id"], item_key=item["item_key"])
            self._send_json(200, {"ok": True, "item": item})
            return

        m_cmt = re.fullmatch(r"/api/v1/items/([^/]+)/comments", path)
        if m_cmt:
            body = self._read_json_body()
            cmt, seq = self.server.store.add_comment(
                m_cmt.group(1),
                body.get("body_md") or "",
                actor_id,
                comment_type=(body.get("comment_type") or "comment"),
            )
            detail = self.server.store.get_item_detail(m_cmt.group(1))
            self._broadcast(seq, "comment_added", project_id=detail["project_id"], item_key=detail["item_key"])
            self._send_json(201, {"ok": True, "comment": cmt})
            return

        m_att = re.fullmatch(r"/api/v1/items/([^/]+)/attachments", path)
        if m_att:
            body = self._read_json_body()
            b64 = (body.get("data_base64") or "").strip()
            if not b64:
                raise ValidationError("缺少附件内容 data_base64")
            if "," in b64 and b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            try:
                raw_bytes = base64.b64decode(b64, validate=True)
            except Exception:
                raise ValidationError("Base64 解码失败")
            if not raw_bytes or len(raw_bytes) > MAX_UPLOAD_BYTES:
                raise ValidationError(f"附件大小须在 1 字节至 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 之间")

            orig_name = Path((body.get("filename") or "pasted.png").strip()).name or "pasted.png"
            mime_raw = (body.get("mime_type") or "").split(";")[0].strip().lower()
            ext = Path(orig_name).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTS:
                ext = MIME_TO_EXT.get(mime_raw, ".png")
            if ext not in ALLOWED_UPLOAD_EXTS:
                raise ValidationError(f"不支持的附件扩展名：{ext}")
            mime_type = ALLOWED_UPLOAD_EXTS[ext]
            digest = hashlib.sha256(raw_bytes).hexdigest()
            dest = self.server.attachments_dir / f"{digest}{ext}"
            if not dest.is_file():
                tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:6]}.tmp")
                tmp.write_bytes(raw_bytes)
                os.replace(tmp, dest)

            att, seq = self.server.store.add_attachment(
                m_att.group(1),
                sha256=digest,
                ext=ext,
                filename=orig_name,
                mime_type=mime_type,
                size_bytes=len(raw_bytes),
                actor_id=actor_id,
            )
            detail = self.server.store.get_item_detail(m_att.group(1))
            self._broadcast(seq, "attachment_added", project_id=detail["project_id"], item_key=detail["item_key"])
            self._send_json(201, {"ok": True, "attachment": att})
            return

        self._send_json(404, {"ok": False, "error": "not_found", "message": f"未知接口：{path}"})

    # ---------- PATCH / DELETE 路由 ----------

    def _handle_patch(self, path: str, qs: dict[str, list[str]]) -> None:
        actor = self._require_auth(qs)
        if actor is None and self.server.auth_mode == "lan_pin":
            return
        actor_id = actor["id"] if actor else None

        m_proj = re.fullmatch(r"/api/v1/projects/(\d+)", path)
        if m_proj:
            if not self._require_human(actor):
                return
            body = self._read_json_body()
            proj, seq = self.server.store.update_project(int(m_proj.group(1)), body, actor_id)
            self._broadcast(seq, "project_updated", project_id=proj["id"])
            self._send_json(200, {"ok": True, "project": proj})
            return

        m_member = re.fullmatch(r"/api/v1/members/(\d+)", path)
        if m_member:
            if not self._require_human(actor):
                return
            body = self._read_json_body()
            member, seq = self.server.store.update_member(int(m_member.group(1)), body, actor_id)
            self._broadcast(seq, "member_updated")
            self._send_json(200, {"ok": True, "member": member})
            return

        m_mod = re.fullmatch(r"/api/v1/modules/(\d+)", path)
        if m_mod:
            body = self._read_json_body()
            mod, seq = self.server.store.update_module(int(m_mod.group(1)), body, actor_id)
            self._broadcast(seq, "module_updated", project_id=mod["project_id"])
            self._send_json(200, {"ok": True, "module": mod})
            return

        m_item = re.fullmatch(r"/api/v1/items/([^/]+)", path)
        if m_item:
            body = self._read_json_body()
            item, seq = self.server.store.update_item(m_item.group(1), body, actor_id)
            self._broadcast(seq, "item_updated", project_id=item["project_id"], item_key=item["item_key"])
            self._send_json(200, {"ok": True, "item": item})
            return

        self._send_json(404, {"ok": False, "error": "not_found", "message": f"未知接口：{path}"})

    def _handle_delete(self, path: str, qs: dict[str, list[str]]) -> None:
        self._check_csrf_headers()
        actor = self._require_auth(qs)
        if actor is None and self.server.auth_mode == "lan_pin":
            return
        if not self._require_human(actor):
            return
        actor_id = actor["id"] if actor else None

        m_item = re.fullmatch(r"/api/v1/items/([^/]+)", path)
        if m_item:
            detail = self.server.store.get_item_detail(m_item.group(1))
            seq = self.server.store.delete_item(detail["id"], actor_id)
            self._broadcast(seq, "item_deleted", project_id=detail["project_id"], item_key=detail["item_key"])
            self._send_json(200, {"ok": True, "deleted": detail["item_key"]})
            return

        m_mod = re.fullmatch(r"/api/v1/modules/(\d+)", path)
        if m_mod:
            seq = self.server.store.delete_module(int(m_mod.group(1)), actor_id)
            self._broadcast(seq, "module_deleted")
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"ok": False, "error": "not_found", "message": f"未知接口：{path}"})


# ---------- 运行态管理与启停控制 ----------

def write_state(data_dir: Path, port: int, payload: dict[str, Any]) -> None:
    sf = state_file_path(data_dir, port)
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_name(f"{sf.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, sf)


def read_state(data_dir: Path, port: int) -> dict[str, Any] | None:
    sf = state_file_path(data_dir, port)
    if not sf.is_file():
        return None
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def cleanup_state(data_dir: Path, port: int, expected_pid: int) -> None:
    st = read_state(data_dir, port)
    if st and st.get("pid") not in (None, expected_pid):
        return
    try:
        state_file_path(data_dir, port).unlink(missing_ok=True)
    except OSError:
        pass


def collect_status(data_dir: Path, port: int) -> dict[str, Any]:
    hz = query_healthz(port)
    expected_sha = compute_data_dir_sha(data_dir)
    own = isinstance(hz, dict) and hz.get("service") == SERVICE_NAME and hz.get("port") == port and hz.get("data_dir_sha256") == expected_sha
    occupied = is_port_open(port)
    owners = [inspect_pid(p) for p in find_listening_pids(port)] if (occupied and not own) else []
    st = read_state(data_dir, port)
    return {
        "running": own,
        "port": port,
        "port_occupied_by_other": occupied and not own,
        "external_owners": owners,
        "healthz": hz if own else None,
        "state": st,
        "url": f"http://127.0.0.1:{port}/",
    }


def stop_service(data_dir: Path, port: int) -> dict[str, Any]:
    status = collect_status(data_dir, port)
    if status["port_occupied_by_other"]:
        return {
            "ok": False,
            "error": "port_occupied_by_other",
            "message": f"端口 {port} 被外部进程占用，已按安全策略拒绝强杀",
            "external_owners": status["external_owners"],
        }
    if not status["running"]:
        return {"ok": True, "stopped": False, "port": port, "message": "看板服务当前未运行"}
    st = status.get("state") or {}
    token = st.get("control_token") or ""
    if not token:
        return {"ok": False, "error": "missing_token", "message": "缺少运行状态控制口令，无法优雅关停"}
    req = Request(
        f"http://127.0.0.1:{port}{SHUTDOWN_PATH}",
        method="POST",
        headers={"X-Control-Token": token, "Content-Length": "0"},
    )
    try:
        with _NO_PROXY_OPENER.open(req, timeout=3.0) as resp:
            _ = resp.read()
    except Exception as exc:
        return {"ok": False, "error": "shutdown_failed", "message": f"发送关停信号失败：{exc}"}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not is_port_open(port, timeout=0.15):
            return {"ok": True, "stopped": True, "port": port}
        time.sleep(0.15)
    return {"ok": False, "error": "shutdown_timeout", "message": f"端口 {port} 未在 5 秒内释放"}


def ensure_background_running(
    *,
    data_dir: Path,
    host: str,
    port: int,
    auth_mode: str,
    open_browser: bool,
    force_restart: bool = False,
) -> dict[str, Any]:
    st = collect_status(data_dir, port)
    if st["port_occupied_by_other"]:
        return {
            "ok": False,
            "error": "port_occupied_by_other",
            "port": port,
            "message": f"专属端口 {port} 被外部进程占用（只诊断不强杀，且不飘移端口）",
            "external_owners": st["external_owners"],
        }
    if st["running"] and not force_restart:
        hz = st["healthz"]
        if hz.get("host") == host and hz.get("auth_mode") == auth_mode:
            url = f"http://127.0.0.1:{port}/"
            if open_browser:
                webbrowser.open(url)
            return {"ok": True, "action": "reused", "pid": hz.get("pid"), "port": port, "host": host, "auth_mode": auth_mode, "url": url, "lan_url": f"http://{hz.get('lan_ip')}:{port}/"}
        force_restart = True

    if st["running"] and force_restart:
        stop_res = stop_service(data_dir, port)
        if not stop_res.get("ok"):
            return stop_res

    lf = log_file_path(data_dir, port)
    lf.parent.mkdir(parents=True, exist_ok=True)
    if lf.is_file() and lf.stat().st_size > LOG_MAX_BYTES:
        tail = lf.read_bytes()[-LOG_MAX_BYTES // 2 :]
        lf.write_bytes(tail)

    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        str(Path(__file__).resolve()),
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--auth-mode",
        auth_mode,
        "--run-mode",
        "background",
        "--data-dir",
        str(data_dir.resolve()),
    ]
    with lf.open("ab") as log_out:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_out,
            stderr=log_out,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        cur = collect_status(data_dir, port)
        if cur["running"]:
            hz = cur["healthz"]
            url = f"http://127.0.0.1:{port}/"
            if open_browser:
                webbrowser.open(url)
            return {
                "ok": True,
                "action": "restarted" if force_restart else "started",
                "pid": hz.get("pid"),
                "port": port,
                "host": host,
                "auth_mode": auth_mode,
                "url": url,
                "lan_url": f"http://{hz.get('lan_ip')}:{port}/",
            }
        time.sleep(0.15)
    return {"ok": False, "error": "start_timeout", "port": port, "message": f"看板服务启动超时，请检查日志：{lf}"}


def install_console_close_handler(on_shutdown: Any, stopped_event: threading.Event) -> dict[str, Any]:
    """注册 SIGTERM 与 Windows 控制台关闭事件，保证点右上角 X 时优雅释放端口与状态文件。"""
    triggered = threading.Event()

    def _trigger() -> None:
        if not triggered.is_set():
            triggered.set()
            try:
                on_shutdown()
            except Exception:
                pass

    prev_sigterm = None
    try:
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: _trigger())
    except (ValueError, OSError):
        prev_sigterm = None

    win_handle = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _ctrl_handler(ctrl_type: int) -> bool:
            # 0: CTRL_C, 1: CTRL_BREAK, 2: CTRL_CLOSE(关窗), 5: LOGOFF, 6: SHUTDOWN
            if ctrl_type in (0, 1, 2, 5, 6):
                _trigger()
                if ctrl_type in (2, 5, 6):
                    stopped_event.wait(timeout=3.5)
                return True
            return False

        cb = handler_type(_ctrl_handler)
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        if kernel32.SetConsoleCtrlHandler(cb, True):
            win_handle = (kernel32, cb)

    return {"win_handle": win_handle, "prev_sigterm": prev_sigterm, "trigger": _trigger}


def uninstall_console_close_handler(handle: dict[str, Any] | None) -> None:
    if not handle:
        return
    win_handle = handle.get("win_handle")
    if sys.platform == "win32" and win_handle:
        kernel32, cb = win_handle
        try:
            kernel32.SetConsoleCtrlHandler(cb, False)
        except Exception:
            pass
    prev_sigterm = handle.get("prev_sigterm")
    if prev_sigterm is not None:
        try:
            signal.signal(signal.SIGTERM, prev_sigterm)
        except (ValueError, OSError):
            pass


def print_foreground_panel(
    *,
    port: int,
    host: str,
    auth_mode: str,
    lan_ip: str,
    data_dir: Path,
    panel: bool,
    interactive_keys: bool,
) -> None:
    local_url = f"http://127.0.0.1:{port}/"
    lan_url = f"http://{lan_ip}:{port}/"
    mode_text = "局域网团队模式（PIN + Token 鉴权）" if auth_mode == "lan_pin" else "单人本机模式（免登录零摩擦）"
    lines = [
        "",
        "============================================================",
        f"  工作任务看板 {SERVICE_VERSION}  |  专属端口 {port}  (PID: {os.getpid()})",
        "------------------------------------------------------------",
        f"  运行模式   : {mode_text}",
        f"  本机访问   : {local_url}",
    ]
    if host == "0.0.0.0":
        lines.append(f"  局域网访问 : {lan_url}  (默认主理人初始 PIN: 1234)")
    lines.append(f"  数据目录   : {data_dir.resolve()}")
    if panel and interactive_keys:
        switch_label = "切回单人本机模式" if auth_mode == "lan_pin" else "切换局域网团队模式"
        lines.extend([
            "------------------------------------------------------------",
            f"  [O] 打开浏览器    [L] {switch_label}",
            "  [R] 重启服务      [Q] 优雅停止并退出（或直接关闭窗口 / Ctrl+C）",
        ])
    else:
        lines.append("  [提示] 按 Ctrl+C 或直接关闭此窗口即可优雅停止并释放端口。")
    lines.append("============================================================")
    print("\n".join(lines), flush=True)


def run_foreground(
    *,
    data_dir: Path,
    host: str,
    port: int,
    auth_mode: str,
    run_mode: str,
    open_browser: bool,
    panel: bool,
    takeover: bool,
    verbose: bool,
) -> int:
    st = collect_status(data_dir, port)
    if st["port_occupied_by_other"]:
        print(f"[端口冲突] 专属端口 {port} 已被外部进程占用（只诊断不强杀，不自动换口）：", file=sys.stderr)
        for owner in st["external_owners"]:
            print(f"  - PID {owner['pid']} | {owner['name']} | {owner['command_line']}", file=sys.stderr)
        return 1

    if st["running"]:
        hz = st["healthz"] or {}
        remote_mode = hz.get("run_mode") or "background"
        remote_pid = hz.get("pid")
        same_cfg = (hz.get("host") == host and hz.get("auth_mode") == auth_mode)
        url = f"http://127.0.0.1:{port}/"
        if run_mode == "foreground" and remote_mode == "foreground" and same_cfg and not takeover:
            print(f"[复用] 工作任务看板已在另一个前台窗口运行（PID: {remote_pid}）-> {url}", flush=True)
            if open_browser:
                webbrowser.open(url)
            return 0
        reason = "后台常驻" if remote_mode != "foreground" else ("配置变更" if not same_cfg else "强制接管")
        print(f"[接管] 检测到已有工作任务看板实例（PID: {remote_pid}，{reason}），正在平稳接管至当前窗口...", flush=True)
        stop_res = stop_service(data_dir, port)
        if not stop_res.get("ok"):
            print(f"[接管失败] {stop_res.get('message')}", file=sys.stderr)
            return 1

    db_path = data_dir / "数据" / "workboard.db"
    store = BoardStore(db_path, seed_workspace=True)
    secret = load_or_create_session_secret(data_dir)

    interactive_keys = bool(
        panel
        and run_mode == "foreground"
        and sys.platform == "win32"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get("WORKBOARD_NO_PAUSE") != "1"
        and os.environ.get("SCAVENGER_NO_PAUSE") != "1"
    )

    cur_host = host
    cur_auth = auth_mode
    should_open_browser = open_browser
    action_lock = threading.Lock()
    active_server_box: dict[str, WorkboardServer | None] = {"server": None}
    next_action: dict[str, str] = {"cmd": "quit"}
    exiting_event = threading.Event()
    stopped_event = threading.Event()

    def _request_server_stop(cmd: str) -> None:
        with action_lock:
            if next_action["cmd"] == "quit" and exiting_event.is_set():
                return
            next_action["cmd"] = cmd
            if cmd == "quit":
                exiting_event.set()
            srv = active_server_box["server"]
        if srv is not None:
            srv.broker.close_all()
            threading.Thread(target=srv.shutdown, daemon=True).start()

    close_handle = install_console_close_handler(lambda: _request_server_stop("quit"), stopped_event)

    def _hotkey_loop() -> None:
        import msvcrt

        while not exiting_event.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        if msvcrt.kbhit():
                            msvcrt.getwch()
                        continue
                    key = ch.lower()
                    if key == "o":
                        url = f"http://127.0.0.1:{port}/"
                        print(f"[操作] 正在浏览器打开 {url} ...", flush=True)
                        webbrowser.open(url)
                    elif key == "l":
                        print("[操作] 正在切换「单人本机 ↔ 局域网团队」运行模式...", flush=True)
                        _request_server_stop("toggle_lan")
                    elif key == "r":
                        print("[操作] 正在重启工作任务看板服务...", flush=True)
                        _request_server_stop("restart")
                    elif key in ("q", "\x03"):
                        print("[操作] 收到退出指令，正在优雅停止服务...", flush=True)
                        _request_server_stop("quit")
                        return
            except Exception:
                return
            exiting_event.wait(0.08)

    if interactive_keys:
        threading.Thread(target=_hotkey_loop, daemon=True).start()

    try:
        while True:
            with action_lock:
                next_action["cmd"] = "quit"
            control_token = secrets.token_hex(20)
            try:
                server = WorkboardServer(
                    (cur_host, port),
                    store=store,
                    data_dir=data_dir,
                    host=cur_host,
                    auth_mode=cur_auth,
                    control_token=control_token,
                    session_secret=secret,
                    run_mode=run_mode,
                    verbose=verbose,
                )
            except OSError as exc:
                print(f"[启动失败] 无法绑定 {cur_host}:{port}：{exc}", file=sys.stderr)
                return 1

            with action_lock:
                active_server_box["server"] = server

            write_state(data_dir, port, {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "pid": os.getpid(),
                "host": cur_host,
                "port": port,
                "auth_mode": cur_auth,
                "run_mode": run_mode,
                "started_at": server.started_at,
                "data_dir": str(data_dir.resolve()),
                "data_dir_sha256": server.data_dir_sha,
                "control_token": control_token,
            })

            print_foreground_panel(
                port=port,
                host=cur_host,
                auth_mode=cur_auth,
                lan_ip=server.lan_ip,
                data_dir=data_dir,
                panel=panel,
                interactive_keys=interactive_keys,
            )

            if should_open_browser:
                local_url = f"http://127.0.0.1:{port}/"
                threading.Timer(0.35, lambda u=local_url: (not exiting_event.is_set()) and webbrowser.open(u)).start()
                should_open_browser = False

            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                with action_lock:
                    next_action["cmd"] = "quit"
                    exiting_event.set()
            finally:
                with action_lock:
                    active_server_box["server"] = None
                server.broker.close_all()
                server.server_close()

            with action_lock:
                cmd = next_action["cmd"]
            if cmd == "toggle_lan":
                if cur_auth == "lan_pin":
                    cur_host, cur_auth = DEFAULT_HOST, "local_single"
                else:
                    cur_host, cur_auth = "0.0.0.0", "lan_pin"
                continue
            if cmd == "restart":
                continue
            break
    finally:
        exiting_event.set()
        cleanup_state(data_dir, port, os.getpid())
        stopped_event.set()
        uninstall_console_close_handler(close_handle)
        print(f"\n[已停止] 工作任务看板已关闭，端口 {port} 已释放。", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", choices=("serve", "start", "open", "stop", "restart", "status", "logs"), default="serve")
    parser.add_argument("--host", default="", help="绑定地址（默认 127.0.0.1；--lan 时自动为 0.0.0.0）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_BOARD_PORT", DEFAULT_PORT)), help=f"专属端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--lan", action="store_true", help="启用局域网团队模式（绑定 0.0.0.0 并开启 PIN + Token 鉴权）")
    parser.add_argument("--auth-mode", choices=("local_single", "lan_pin"), default="", help="显式指定鉴权模式")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="运行态数据目录（SQLite、附件、状态与日志）")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--panel", action="store_true", help="前台常驻时显示交互式快捷键控制面板")
    parser.add_argument("--takeover", action="store_true", help="前台启动时强制接管已有前台常驻实例")
    parser.add_argument("--run-mode", choices=("foreground", "background"), default="foreground", help=argparse.SUPPRESS)
    parser.add_argument("--lines", type=int, default=40, help="logs 子命令显示的末尾行数")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    parser.add_argument("--verbose", action="store_true", help="输出 HTTP 访问日志")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    host = args.host or ("0.0.0.0" if args.lan else DEFAULT_HOST)
    is_loopback = host in ("127.0.0.1", "::1", "localhost")
    auth_mode = args.auth_mode or ("lan_pin" if (args.lan or not is_loopback) else "local_single")
    if not is_loopback and auth_mode != "lan_pin":
        print("[拒绝启动] 非回环监听地址禁止使用 local_single 免登模式，请改用 --auth-mode lan_pin 或监听 127.0.0.1。", file=sys.stderr)
        return 2

    if args.action == "status":
        info = collect_status(data_dir, args.port)
        print(json.dumps(info, ensure_ascii=False, indent=None if args.json else 2), flush=True)
        return 0 if info["running"] else 1

    if args.action == "logs":
        lf = log_file_path(data_dir, args.port)
        if not lf.is_file():
            print("[提示] 暂无运行日志。")
            return 0
        lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, args.lines) :]
        print("\n".join(lines), flush=True)
        return 0

    if args.action == "stop":
        res = stop_service(data_dir, args.port)
        print(json.dumps(res, ensure_ascii=False), flush=True)
        return 0 if res.get("ok") else 1

    if args.action in ("start", "open", "restart"):
        res = ensure_background_running(
            data_dir=data_dir,
            host=host,
            port=args.port,
            auth_mode=auth_mode,
            open_browser=(args.action == "open") or bool(args.open),
            force_restart=(args.action == "restart"),
        )
        if args.json:
            print(json.dumps(res, ensure_ascii=False), flush=True)
        else:
            if res.get("ok"):
                print(f"[就绪] 工作任务看板 {res['action']} (PID: {res['pid']}, mode: {res['auth_mode']}) -> {res['url']}", flush=True)
                if host == "0.0.0.0":
                    print(f"[局域网] {res['lan_url']}", flush=True)
            else:
                print(f"[失败] {res.get('message')}", file=sys.stderr, flush=True)
        return 0 if res.get("ok") else 1

    return run_foreground(
        data_dir=data_dir,
        host=host,
        port=args.port,
        auth_mode=auth_mode,
        run_mode=args.run_mode,
        open_browser=bool(args.open),
        panel=bool(args.panel),
        takeover=bool(args.takeover),
        verbose=bool(args.verbose),
    )


if __name__ == "__main__":
    sys.exit(main())
