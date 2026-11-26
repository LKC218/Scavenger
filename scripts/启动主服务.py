"""Scavenger 全项目唯一统一开发与运行服务（默认绑定 127.0.0.1:8800）。

收口原分散端口并按同源 URL 路由分发全部正式模块与开发工具：
- /            : 4A 主菜单大厅（src/主入口/index.html）
- /armory      : 4B 129 车武器库 680p 3D 整备台（支持 ?unit=<slug>）
- /settings    : 系统与渲染偏好设置
- /play        : 4C 9km 程序化大地图实战 HUD
- /editor      : 9km 地图编辑器与实时物理派生抽屉
- /air-lab     : 固定翼单机试飞台快捷路由（重定向至 /play?mode=air-lab&unit=US_A10，透传 unit/aspect）
- /ui-spec     : Code SSOT V2.2 全局 UI/UX 活体规范台
- /ui/*        : 全局唯一 UI/UX 样式、标绘与交互控件单源
- /dev/*       : 载具混编演训(/dev/battlefield)、前线资产库(/dev/library/)、音效索引(/dev/audio/)、空战音效试听(/dev/air-audio)
- /b0..b4/* /v0..v5/* /vendor/* /vehicles/* /media/* : 底层资产 HTTP 206 Range / Gzip / ETag 只读分发
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import hmac
import io
import json
import mimetypes
import os
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
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

if sys.platform == "win32":
    import msvcrt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8800
LAUNCHER_VERSION = "V1.2"
LOG_MAX_BYTES = 512 * 1024
SHUTDOWN_PATH = "/__internal/shutdown"

RUNTIME_DIR = ROOT / "local-data" / "主服务" / "运行状态"
UI_SPEC_DIR = ROOT / "src" / "界面规范"
MAIN_APP_DIR = ROOT / "src" / "主入口"
MAP_APP_DIR = ROOT / "src" / "程序化地图"
VEH_APP_DIR = ROOT / "src" / "程序化载具"
LIB_APP_DIR = ROOT / "src" / "资料库"
AUDIO_APP_DIR = ROOT / "src" / "音效索引"

MAP_DATA_DIR = ROOT / "local-data" / "程序化地图" / "V001-fcf46de3"
VEH_DATA_DIR = ROOT / "local-data" / "程序化载具" / "V001-b4570e71"
VEH_PORTRAIT_DIR = VEH_DATA_DIR / "V6-兵牌插画"
VEH_TMP_ORIG_DIR = ROOT / "tmp" / "程序化载具-V001-b4570e71" / "originals"
VEH_REPORT_DIR = ROOT / "reports" / "程序化载具"
FORMAL_VEH_MODEL_DIR = ROOT / "local-data" / "资源库" / "当前资源" / "模型"
AUDIO_BATCH_DIR = ROOT / "local-data" / "断箭" / "批次" / "BA-26273833-004" / "音效索引库"
PUBLIC_ASSETS_DIR = ROOT / "public" / "assets"

ROUTE_ALIASES: dict[str, str] = {
    "home": "/",
    "lobby": "/",
    "main": "/",
    "/": "/",
    "armory": "/armory",
    "settings": "/settings",
    "play": "/play",
    "editor": "/editor",
    "air-lab": "/air-lab",
    "airlab": "/air-lab",
    "ui-spec": "/ui-spec",
    "spec": "/ui-spec",
    "battlefield": "/dev/battlefield",
    "library": "/dev/library/",
    "audio": "/dev/audio/",
    "air-audio": "/dev/air-audio",
}
QUICK_ROUTE_ACTIONS = ("play", "editor", "air-lab", "armory", "settings", "ui-spec", "spec", "battlefield", "library", "audio", "air-audio")
CONTROL_ACTIONS = ("open", "start", "stop", "restart", "status", "menu", "logs", "serve", "help")
ALL_ACTIONS = CONTROL_ACTIONS + QUICK_ROUTE_ACTIONS

_NO_PROXY_OPENER = build_opener(ProxyHandler({}))
_WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

UI_SPEC_ALLOW = {
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
    """从唯一品牌源 src/界面规范/favicon.svg 读取浏览器图标，避免前后端硬编码漂移。"""
    if UI_FAVICON_FILE.is_file():
        return UI_FAVICON_FILE.read_bytes()
    return UI_FAVICON_FALLBACK_SVG

HERO_VEHICLES_MAP = {
    "/vehicles/US_M1A2.glb": FORMAL_VEH_MODEL_DIR / "地面载具/US_M1A2-M1A2-Abrams/M1A2-Abrams-256默认.glb",
    "/vehicles/US_AH64D.glb": FORMAL_VEH_MODEL_DIR / "空中载具/US_AH64D-AH-64D-Apache/AH-64D-Apache-256默认.glb",
    "/vehicles/US_A10.glb": FORMAL_VEH_MODEL_DIR / "空中载具/US_A10-A-10C-Thunderbolt-II/A-10C-Thunderbolt-II-256默认.glb",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".glb": "model/gltf-binary",
    ".ktx2": "image/ktx2",
    ".wasm": "application/wasm",
    ".scvmap": "application/octet-stream",
    ".scvveh": "application/octet-stream",
    ".scvi": "application/octet-stream",
    ".scvh": "application/octet-stream",
    ".scvp": "application/octet-stream",
    ".scvs": "application/octet-stream",
    ".scvt": "application/octet-stream",
    ".scvb": "application/octet-stream",
    ".scvc": "application/octet-stream",
    ".bin": "application/octet-stream",
}
GZIP_EXTS = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".wasm"}
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

_GZIP_CACHE: dict[tuple[str, int], bytes] = {}
_CACHE_LOCK = threading.Lock()


def safe_under(base: Path, rel: str) -> Path | None:
    try:
        if not isinstance(rel, str) or "\x00" in rel or "\\" in rel or ":" in rel:
            return None
        cleaned = rel.lstrip("/")
        if not cleaned:
            return None
        parts = [p for p in cleaned.split("/") if p]
        if any(p in (".", "..") for p in parts):
            return None
        base_resolved = base.resolve()
        target = base_resolved.joinpath(*parts).resolve()
        if not target.is_relative_to(base_resolved):
            return None
        return target
    except Exception:
        return None


def compute_ui_spec_sha() -> str:
    names = sorted([
        "index.html",
        "令牌.css",
        "组件.css",
        "场内视觉.css",
        "图标与标绘.mjs",
        "交互控件.mjs",
        "规范页.css",
        "规范页.mjs",
    ])
    files = {}
    for name in names:
        fp = UI_SPEC_DIR / name
        if fp.is_file():
            with fp.open("rb") as stream:
                files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def get_gzipped_bytes(file_path: Path, mtime_ns: int) -> bytes:
    key = (str(file_path), mtime_ns)
    with _CACHE_LOCK:
        cached = _GZIP_CACHE.get(key)
        if cached is not None:
            return cached
    data = gzip.compress(file_path.read_bytes(), compresslevel=6)
    with _CACHE_LOCK:
        if len(_GZIP_CACHE) > 160:
            _GZIP_CACHE.clear()
        _GZIP_CACHE[key] = data
    return data


def _resolve_three_dir() -> Path:
    pin = json.loads((ROOT / "local-data/tools/three/当前版本.json").read_text("utf-8"))
    return (ROOT / "local-data/tools/three" / pin["version"]).resolve()


def runtime_state_file(port: int) -> Path:
    """返回指定端口对应主服务运行状态文件路径。"""
    return RUNTIME_DIR / f"server-{port}.json"


def runtime_log_file(port: int) -> Path:
    """返回指定端口对应主服务日志文件路径。"""
    name = "unified-server.log" if port == DEFAULT_PORT else f"unified-server-{port}.log"
    return RUNTIME_DIR / name


def runtime_lock_file(port: int) -> Path:
    """返回指定端口启动互斥锁文件路径。"""
    return RUNTIME_DIR / f"launcher-{port}.lock"


def resolve_route_path(route_arg: str) -> str:
    """将别名或相对路径解析为规范的同源 URL 路径。"""
    text = (route_arg or "").strip()
    if not text:
        return "/"
    aliased = ROUTE_ALIASES.get(text.lower())
    if aliased is not None:
        return aliased
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text) or "\\" in text or text.startswith("//"):
        raise ValueError(f"非法路由路径：{route_arg}")
    if not text.startswith("/"):
        raise ValueError(f"不支持的路由或别名：{route_arg}")
    return text


def compute_script_sha256() -> str:
    """计算当前主服务脚本字节的 SHA-256 摘要，用于识别后台过期实例。"""
    script_path = Path(__file__).resolve()
    with script_path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compute_root_sha256() -> str:
    """计算当前项目根目录规范路径摘要，用于进程归属校验且不暴露绝对路径。"""
    return hashlib.sha256(str(ROOT.resolve()).lower().encode("utf-8")).hexdigest()


def rotate_log_if_needed(log_file: Path, max_bytes: int = LOG_MAX_BYTES) -> bool:
    """当日志文件超过上限时保留后半段完整行，失败时安全跳过。"""
    try:
        if not log_file.is_file():
            return False
        size = log_file.stat().st_size
        if size <= max_bytes:
            return False
        keep_bytes = max(1024, max_bytes // 2)
        with log_file.open("rb") as stream:
            stream.seek(max(0, size - keep_bytes))
            tail = stream.read()
        newline_idx = tail.find(b"\n")
        if 0 <= newline_idx < len(tail) - 1:
            tail = tail[newline_idx + 1 :]
        tmp_file = log_file.with_name(f"{log_file.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp_file.write_bytes(tail)
        os.replace(tmp_file, log_file)
        return True
    except OSError:
        return False


def read_log_tail(log_file: Path, max_lines: int = 30, from_offset: int | None = None) -> str:
    """按 UTF-8 容错读取日志末尾指定行数，可选从指定字节偏移起截取。"""
    if not log_file.is_file():
        return ""
    try:
        size = log_file.stat().st_size
        if size == 0:
            return ""
        read_start = 0
        if from_offset is not None and 0 <= from_offset < size:
            read_start = max(from_offset, size - 128 * 1024)
        else:
            read_start = max(0, size - 128 * 1024)
        with log_file.open("rb") as stream:
            stream.seek(read_start)
            raw = stream.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines)
    except OSError:
        return ""


class UnifiedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = False if sys.platform == "win32" else True

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, server_address, handler_cls, verbose: bool = False, run_mode: str = "background"):
        super().__init__(server_address, handler_cls)
        self.verbose = verbose
        self.run_mode = run_mode
        self.three_dir = _resolve_three_dir()
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.script_sha256 = compute_script_sha256()
        self.root_sha256 = compute_root_sha256()
        self.control_token = secrets.token_hex(24)
        self._ctx_lock = threading.Lock()
        self._lib_ctx = None
        self._audio_routes = None
        self._vehicle_audio_map = None

    def _get_library_ctx_locked(self):
        if self._lib_ctx is not None:
            return self._lib_ctx
        for p in (str(ROOT / "scripts/断箭"), str(ROOT / "scripts")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from 资料库核心 import current, query  # noqa: PLC0415
        from 资源优化.运行资源接入 import library  # noqa: PLC0415

        pointer, catalog, routes, _ = current()
        dummy = type("LibHolder", (), {"catalog": catalog, "routes": routes})()
        library(dummy)
        self._lib_ctx = {
            "revision": pointer.get("revision"),
            "catalog": dummy.catalog,
            "routes": dummy.routes,
            "query_fn": query,
        }
        return self._lib_ctx

    def get_library_ctx(self):
        with self._ctx_lock:
            return self._get_library_ctx_locked()

    def get_vehicle_audio_map(self, slugs_filter: set[str] | None = None) -> dict:
        """从正式统一资源库索引与129车meta只读生成轻量载具循环音效映射。"""
        with self._ctx_lock:
            ctx = self._get_library_ctx_locked()
            rev = ctx.get("revision")
            if self._vehicle_audio_map is None or self._vehicle_audio_map.get("revision") != rev:
                catalog = ctx["catalog"]
                routes = ctx["routes"]
                loop_by_model: dict[str, tuple[dict, str]] = {}
                for a in catalog.get("audio", []):
                    if a.get("kind") != "derived_loop":
                        continue
                    is_v2 = (a.get("extensions") or {}).get("loop_version") == "v2"
                    if a.get("url") not in routes:
                        continue
                    for b in a.get("bindings", []):
                        if b.get("field") != "MoveEvent":
                            continue
                        mid = b.get("model_id")
                        if not mid:
                            continue
                        prev = loop_by_model.get(mid)
                        if prev is None or (is_v2 and (prev[0].get("extensions") or {}).get("loop_version") != "v2"):
                            loop_by_model[mid] = (a, b.get("evidence", ""))

                meta_dir = VEH_DATA_DIR / "V2-全库编译" / "meta"
                vehicles: dict[str, dict] = {}
                unique_audio_ids: set[str] = set()
                for mp in sorted(meta_dir.glob("*.json")):
                    mj = json.loads(mp.read_text(encoding="utf-8"))
                    slug = mj.get("slug") or mp.stem
                    mid = mj.get("id") or ("model:" + slug)
                    hit = loop_by_model.get(mid) or loop_by_model.get("model:" + slug)
                    if not hit:
                        continue
                    a, evidence = hit
                    chk = a.get("check") or {}
                    unique_audio_ids.add(a["id"])
                    vehicles[slug] = {
                        "slug": slug,
                        "model_id": mid,
                        "role": mj.get("role", "ground"),
                        "audio_id": a["id"],
                        "url": a["url"],
                        "sha256": a["sha256"],
                        "bytes": a["bytes"],
                        "duration_seconds": chk.get("duration_seconds", 6.38),
                        "sample_rate": chk.get("sample_rate", 48000),
                        "channels": chk.get("channels", 1),
                        "source_audio_id": a.get("source_audio_id", ""),
                        "source_name": (a.get("aliases") or [a.get("name", "")])[0],
                        "evidence": evidence,
                    }
                self._vehicle_audio_map = {
                    "schema_version": 1,
                    "revision": rev,
                    "asset_library_revision": catalog.get("asset_library_revision", 1),
                    "total_vehicles": len(vehicles),
                    "unique_loops": len(unique_audio_ids),
                    "vehicles": vehicles,
                }
            payload = self._vehicle_audio_map
        if not slugs_filter:
            return payload
        filtered = {k: v for k, v in payload["vehicles"].items() if k in slugs_filter}
        return {**payload, "filtered_vehicles": len(filtered), "vehicles": filtered}

    def get_audio_route(self, rel_key: str) -> Path | None:
        with self._ctx_lock:
            if self._audio_routes is None:
                routes: dict[str, Path] = {}
                local_root = (ROOT / "local-data").resolve()
                # 统一库按 sha256 与资源别名兜底映射（避免旧批次迁移后 /dev/audio/ 失链）
                sha_map: dict[str, Path] = {}
                try:
                    lib_ctx = self._get_library_ctx_locked()
                    for row in lib_ctx["routes"].values():
                        rp = Path(row["path"]).resolve()
                        if rp.is_file() and rp.is_relative_to(local_root):
                            sha_map[row.get("sha256", "")] = rp
                except Exception:
                    sha_map = {}

                preview_dir = ROOT / "local-data" / "资源库" / "当前资源" / "预览"
                preview_by_prefix: dict[str, Path] = {}
                if preview_dir.is_dir():
                    for png in preview_dir.rglob("*.png"):
                        if png.is_file():
                            key_id = png.stem.split("-", 1)[0]
                            preview_by_prefix.setdefault(key_id, png.resolve())

                audio_dir = ROOT / "local-data" / "资源库" / "当前资源" / "音效"
                audio_by_label: dict[str, Path] = {}
                if audio_dir.is_dir():
                    for af in audio_dir.rglob("*.ogg"):
                        if af.is_file():
                            parts = af.stem.split("-")
                            if len(parts) >= 3:
                                lbl = "-".join(parts[1:-1])
                                audio_by_label.setdefault(lbl, af.resolve())

                url_to_label: dict[str, str] = {}
                ar_file = AUDIO_BATCH_DIR / "数据" / "音频资源.json"
                if ar_file.is_file():
                    try:
                        assets = json.loads(ar_file.read_text(encoding="utf-8")).get("assets", {})
                        for item in assets.values():
                            if item.get("url") and item.get("label"):
                                url_to_label[item["url"]] = item["label"]
                    except Exception:
                        url_to_label = {}

                rr_file = AUDIO_BATCH_DIR.parent / "资源路由.json"
                if rr_file.is_file():
                    raw = json.loads(rr_file.read_text(encoding="utf-8")).get("routes", {})
                    for url_key, row in raw.items():
                        p = Path(row["path"])
                        if p.is_file() and p.resolve().is_relative_to(local_root):
                            routes[url_key] = p.resolve()
                            continue
                        sha = row.get("sha256", "")
                        if sha in sha_map:
                            routes[url_key] = sha_map[sha]
                            continue
                        if url_key.startswith("资源/模型/"):
                            hero_path = HERO_VEHICLES_MAP.get("/vehicles/" + Path(url_key).name)
                            if hero_path and hero_path.is_file():
                                routes[url_key] = hero_path.resolve()
                                continue
                        if url_key.startswith("资源/预览/"):
                            stem = Path(url_key).stem
                            if stem in preview_by_prefix:
                                routes[url_key] = preview_by_prefix[stem]
                                continue
                        if url_key.startswith("资源/音频/"):
                            lbl = url_to_label.get(url_key, "")
                            if lbl and lbl in audio_by_label:
                                routes[url_key] = audio_by_label[lbl]
                                continue
                self._audio_routes = routes
        if rel_key in self._audio_routes:
            return self._audio_routes[rel_key]
        p = safe_under(AUDIO_APP_DIR, rel_key)
        if p and p.is_file():
            return p
        p2 = safe_under(AUDIO_BATCH_DIR, rel_key)
        if p2 and p2.is_file():
            return p2
        return None


class UnifiedHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send_json(
        self,
        payload_obj: object,
        status: int = 200,
        send_body: bool = True,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
        try:
            if close_connection:
                self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if close_connection:
                self.send_header("Connection", "close")
            if extra_headers:
                for key, val in extra_headers.items():
                    self.send_header(key, val)
            self.end_headers()
            if send_body:
                self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _redirect(self, location: str, send_body: bool = True) -> None:
        try:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _resolve_static(self, raw_path: str) -> Path | None:
        # 1. 正式主入口（4A 大厅 / 4B 武器库 / 4C 设置）
        if raw_path in ("/", "/index.html", "/armory", "/armory/", "/settings", "/settings/"):
            return MAIN_APP_DIR / "index.html"

        # 2. 4C 9km 实战与地图编辑器
        if raw_path in ("/play", "/play/", "/editor", "/editor/", "/预览入口.html"):
            return MAP_APP_DIR / "预览入口.html"

        # 3. Code SSOT V2.2 全局 UI/UX 活体规范台与共享模块
        if raw_path in ("/ui-spec", "/ui-spec/"):
            return UI_SPEC_DIR / "index.html"
        if raw_path.startswith("/ui/"):
            name = UI_SPEC_ALLOW.get(raw_path[len("/ui/") :])
            return (UI_SPEC_DIR / name).resolve() if name else None

        # 4. 129 车混编演训研发路由
        if raw_path in ("/battlefield", "/dev/battlefield", "/dev/battlefield/", "/混编演训场.html"):
            return VEH_APP_DIR / "混编演训场.html"
        if raw_path in ("/report", "/dev/report"):
            return VEH_REPORT_DIR / "程序化载具优化与视觉验收报告-V001.html"

        # 5. 开发工具保留视图：前线资产库 & 音效索引
        if raw_path == "/dev/library/":
            return LIB_APP_DIR / "index.html"
        if raw_path.startswith("/dev/library/"):
            return safe_under(LIB_APP_DIR, raw_path[len("/dev/library/") :])
        if raw_path in ("/style.css", "/app.mjs", "/viewer.mjs", "/search.mjs"):
            return safe_under(LIB_APP_DIR, raw_path.lstrip("/"))

        if raw_path == "/dev/audio/":
            return AUDIO_APP_DIR / "索引验证.html"
        if raw_path.startswith("/dev/audio/vendor/three/"):
            return safe_under(self.server.three_dir, raw_path[len("/dev/audio/vendor/three/") :])
        if raw_path.startswith("/dev/audio/"):
            return self.server.get_audio_route(raw_path[len("/dev/audio/") :])

        # 6. 共享库与源代码树
        if raw_path == "/compressed-loader.mjs":
            return ROOT / "src/资源加载/压缩模型加载.mjs"
        if raw_path in HERO_VEHICLES_MAP:
            return HERO_VEHICLES_MAP[raw_path]
        if raw_path.startswith("/vendor/"):
            return safe_under(self.server.three_dir, raw_path[len("/vendor/") :])
        if raw_path.startswith("/src/"):
            return safe_under(ROOT / "src", raw_path[len("/src/") :])

        # 7. 地图、载具与研究报告底层资产映射
        if raw_path.startswith("/b0/"):
            return safe_under(MAP_DATA_DIR / "B0-基线冻结", raw_path[4:])
        if raw_path.startswith("/b1/"):
            return safe_under(MAP_DATA_DIR / "B1-地形与道路", raw_path[4:])
        if raw_path.startswith("/b2/"):
            return safe_under(MAP_DATA_DIR / "B2-植被与草丛", raw_path[4:])
        if raw_path.startswith("/b3/"):
            return safe_under(MAP_DATA_DIR / "B3-建筑与编辑器", raw_path[4:])
        if raw_path.startswith("/b4/"):
            return safe_under(MAP_DATA_DIR / "B4-单包归档", raw_path[4:])

        if raw_path.startswith("/v0/"):
            return safe_under(VEH_DATA_DIR / "V0-基线与规则", raw_path[4:])
        if raw_path.startswith("/v2/"):
            return safe_under(VEH_DATA_DIR / "V2-全库编译", raw_path[4:])
        if raw_path.startswith("/v3/"):
            return safe_under(VEH_DATA_DIR / "V3-归档打包", raw_path[4:])
        if raw_path.startswith("/v5/"):
            return safe_under(VEH_DATA_DIR / "V5-全矩阵验证", raw_path[4:])
        if raw_path.startswith("/portraits/"):
            return safe_under(VEH_PORTRAIT_DIR, raw_path[11:])
        if raw_path.startswith("/orig/"):
            return safe_under(VEH_TMP_ORIG_DIR, raw_path[6:])
        if raw_path.startswith("/reports/"):
            return safe_under(VEH_REPORT_DIR, raw_path[9:])
        if raw_path.startswith("/assets/"):
            return safe_under(VEH_REPORT_DIR / "assets", raw_path[8:])
        if raw_path.startswith("/public/assets/"):
            return safe_under(PUBLIC_ASSETS_DIR, raw_path[len("/public/assets/"):])

        if raw_path.startswith("/media/"):
            try:
                ctx = self.server.get_library_ctx()
                rec = ctx["routes"].get(raw_path)
                if rec:
                    p = Path(rec["path"]).resolve()
                    if p.is_relative_to((ROOT / "local-data").resolve()) and p.is_file():
                        return p
            except Exception:
                return None

        return None

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def do_POST(self):
        parsed = urlsplit(self.path)
        try:
            raw_path = unquote(parsed.path, errors="strict")
        except UnicodeDecodeError:
            self._send_json({"ok": False, "error": "Invalid path"}, status=400, close_connection=True)
            return

        try:
            content_len = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            content_len = 0
        if 0 < content_len <= 64 * 1024:
            self.rfile.read(content_len)

        if raw_path != SHUTDOWN_PATH:
            self._send_json(
                {"ok": False, "error": "Method not allowed"},
                status=405,
                close_connection=True,
                extra_headers={"Allow": "GET, HEAD"},
            )
            return

        token = self.headers.get("X-Scavenger-Token", "")
        if self.client_address[0] not in ("127.0.0.1", "::1") or not hmac.compare_digest(token, self.server.control_token):
            self._send_json({"ok": False, "error": "Forbidden"}, status=403, close_connection=True)
            return

        self._send_json(
            {"ok": True, "stopping": True, "pid": os.getpid(), "port": self.server.server_port},
            status=200,
            close_connection=True,
        )
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _serve(self, send_body: bool) -> None:
        parsed = urlsplit(self.path)
        try:
            raw_path = unquote(parsed.path, errors="strict")
        except UnicodeDecodeError:
            self._send_json({"ok": False, "error": "Invalid URL encoding"}, status=400, close_connection=True)
            return

        if raw_path in ("/dev/library", "/dev/audio"):
            self._redirect(raw_path + "/", send_body=send_body)
            return

        if raw_path in ("/air-lab", "/air-lab/", "/dev/air-lab", "/dev/air-lab/"):
            params = parse_qs(parsed.query, keep_blank_values=False)
            unit = (params.get("unit", ["US_A10"])[0] or "US_A10").strip() or "US_A10"
            extra_pairs = [
                (k, v)
                for k, vals in params.items()
                if k not in ("mode", "unit")
                for v in vals
            ]
            target_qs = urlencode([("mode", "air-lab"), ("unit", unit)] + extra_pairs)
            self._redirect(f"/play?{target_qs}", send_body=send_body)
            return

        if raw_path in ("/dev/air-audio", "/dev/air-audio/"):
            self._redirect("/dev/audio/%E7%A9%BA%E6%88%98%E9%9F%B3%E6%95%88%E5%AF%B9%E6%AF%94%E8%AF%95%E5%90%AC.html", send_body=send_body)
            return

        if raw_path == "/healthz":
            self._send_json({
                "status": "ok",
                "service": "scavenger-unified-server",
                "version": "V1.0",
                "launcher_version": LAUNCHER_VERSION,
                "mode": self.server.run_mode,
                "port": self.server.server_port,
                "pid": os.getpid(),
                "started_at": self.server.started_at,
                "uptime_seconds": round(max(0.0, time.monotonic() - self.server.started_monotonic), 3),
                "script_sha256": self.server.script_sha256,
                "root_sha256": self.server.root_sha256,
                "ui_spec_version": "V2.2",
                "render_baseline": "stylized-680p-v1",
                "source_sha256": compute_ui_spec_sha(),
                "routes": ["/", "/armory", "/settings", "/play", "/editor", "/air-lab", "/ui-spec", "/dev/battlefield", "/dev/library/", "/dev/audio/", "/dev/air-audio"],
            }, send_body=send_body)
            return

        if raw_path in ("/api/status", "/api/ui-spec/status"):
            self._send_json({
                "app": "scavenger-ui-ux-live-spec",
                "version": "V2.2",
                "unified_server": True,
                "port": self.server.server_port,
                "source_sha256": compute_ui_spec_sha(),
            }, send_body=send_body)
            return

        if raw_path == "/api/catalog":
            try:
                ctx = self.server.get_library_ctx()
                self._send_json(ctx["catalog"], send_body=send_body)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500, send_body=send_body)
            return

        if raw_path == "/api/vehicle-audio-map":
            try:
                params = parse_qs(parsed.query)
                raw_slugs = params.get("slugs", [""])[0].strip()
                slugs_filter = {s.strip() for s in raw_slugs.split(",") if s.strip()} if raw_slugs else None
                payload = self.server.get_vehicle_audio_map(slugs_filter=slugs_filter)
                self._send_json(payload, send_body=send_body)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500, send_body=send_body)
            return

        if raw_path == "/api/search":
            try:
                ctx = self.server.get_library_ctx()
                params = parse_qs(parsed.query)
                val = lambda k, d=None: params.get(k, [d])[0]
                limit = int(val("limit", 20))
                offset = int(val("offset", 0))
                qtype = val("type", "all")
                if not 1 <= limit <= 200 or offset < 0 or qtype not in ("all", "model", "audio", "scene"):
                    raise ValueError("查询参数无效")
                res = ctx["query_fn"](ctx["catalog"], ctx["routes"], val("q", ""), qtype, val("id"), val("related_to"), val("status"), limit, offset, False)
                self._send_json(res, send_body=send_body)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400, send_body=send_body)
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
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        file_path = self._resolve_static(raw_path)
        if file_path is None or not file_path.is_file():
            self._send_json({"ok": False, "error": "Not found", "path": raw_path}, status=404, send_body=send_body)
            return

        st = file_path.stat()
        file_size = st.st_size
        ext = file_path.suffix.lower()
        ctype = CONTENT_TYPES.get(ext, mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        etag = f'W/"{file_size:x}-{st.st_mtime_ns:x}"'
        has_query = bool(parsed.query)

        is_immutable = (
            ext in (".scvmap", ".scvveh", ".scvi", ".scvh", ".scvp", ".scvs", ".scvt", ".scvb", ".scvc", ".glb", ".webp", ".png", ".ktx2", ".wasm", ".bin", ".js", ".mjs", ".ogg", ".flac")
            and (
                raw_path.startswith(("/vendor/", "/vehicles/", "/v3/", "/orig/", "/media/", "/portraits/"))
                or (has_query and raw_path.startswith(("/b0/", "/b1/", "/b2/", "/b3/", "/b4/", "/public/assets/")))
            )
        )
        cache_ctrl = "public, max-age=31536000, immutable" if is_immutable else "no-cache"

        range_header = self.headers.get("Range")
        if range_header:
            m = RANGE_RE.match(range_header.strip())
            if not m or not any(m.groups()):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            first, last = m.groups()
            if first:
                start = int(first)
                end = min(file_size - 1, int(last)) if last else file_size - 1
            else:
                if int(last) == 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = max(0, file_size - int(last))
                end = file_size - 1
            if start >= file_size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            length = end - start + 1
            try:
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", cache_ctrl)
                self.send_header("ETag", etag)
                self.send_header("X-Content-Type-Options", "nosniff")
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
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return

        if self.headers.get("If-None-Match") == etag:
            try:
                self.send_response(304)
                self.send_header("Cache-Control", cache_ctrl)
                self.send_header("ETag", etag)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return

        accept_enc = self.headers.get("Accept-Encoding", "")
        if ext in GZIP_EXTS and "gzip" in accept_enc and file_size >= 1024:
            gz_data = get_gzipped_bytes(file_path, st.st_mtime_ns)
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Content-Length", str(len(gz_data)))
                self.send_header("Cache-Control", cache_ctrl)
                self.send_header("ETag", etag)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if send_body:
                    self.wfile.write(gz_data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", cache_ctrl)
            self.send_header("ETag", etag)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                with file_path.open("rb") as f:
                    while True:
                        chunk = f.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def write_runtime_state(server: UnifiedServer, log_file: Path) -> Path:
    """原子写入当前服务运行状态文件，记录 PID、运行模式、脚本摘要与本地控制口令。"""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state_file = runtime_state_file(server.server_port)
    try:
        rel_log = log_file.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        rel_log = log_file.name
    payload = {
        "schema_version": 1,
        "service": "scavenger-unified-server",
        "launcher_version": LAUNCHER_VERSION,
        "mode": server.run_mode,
        "pid": os.getpid(),
        "port": server.server_port,
        "started_at": server.started_at,
        "script_sha256": server.script_sha256,
        "root_sha256": server.root_sha256,
        "control_token": server.control_token,
        "log_file": rel_log,
    }
    tmp_file = state_file.with_name(f"{state_file.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_file, state_file)
    return state_file


def read_runtime_state(port: int) -> dict | None:
    """安全读取端口对应的状态元数据；文件缺失或损坏时返回 None。"""
    state_file = runtime_state_file(port)
    if not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def cleanup_runtime_state(port: int, expected_pid: int | None = None) -> None:
    """清理状态文件；若指定 expected_pid，则仅在 PID 匹配时删除。"""
    state_file = runtime_state_file(port)
    if not state_file.is_file():
        return
    if expected_pid is not None:
        state = read_runtime_state(port)
        if state is not None and state.get("pid") not in (None, expected_pid):
            return
    try:
        state_file.unlink()
    except OSError:
        pass


def query_healthz(host: str, port: int, timeout: float = 1.5) -> dict | None:
    """绕过系统代理直连回环 /healthz 检查，防止本地代理劫持导致误判超时。"""
    req = Request(
        f"http://{host}:{port}/healthz",
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


def is_own_service_healthz(info: dict | None, port: int) -> bool:
    """校验 /healthz 响应是否属于当前仓库与目标端口的 Scavenger 主服务。"""
    if not isinstance(info, dict):
        return False
    if info.get("status") != "ok" or info.get("service") != "scavenger-unified-server":
        return False
    if info.get("port") != port:
        return False
    remote_root = info.get("root_sha256")
    if remote_root and remote_root != compute_root_sha256():
        return False
    return True


def is_stale_instance(healthz: dict | None, state: dict | None, current_sha: str) -> bool:
    """判断后台运行实例是否为代码已更新或早期无热更新标记的旧版本。"""
    if isinstance(healthz, dict):
        remote_sha = healthz.get("script_sha256")
        if remote_sha:
            return remote_sha != current_sha
        # 无 script_sha256 说明是 V1.0 早期常驻实例，需要自动重启以升级到当前版本
        return True
    if isinstance(state, dict) and state.get("script_sha256"):
        return state.get("script_sha256") != current_sha
    return False


def is_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    """快速探测指定回环 TCP 端口当前是否可连接。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def find_listening_pids(port: int) -> list[int]:
    """枚举当前正在指定端口上处于 LISTENING 状态的进程 PID（去重保序）。"""
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
    port_str = str(port)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local_addr, _, state, pid_raw = parts[0].upper(), parts[1], parts[2], parts[3].upper(), parts[-1]
        if proto not in ("TCP", "TCPV6") or state != "LISTENING":
            continue
        if ":" not in local_addr:
            continue
        _, addr_port = local_addr.rsplit(":", 1)
        if addr_port != port_str or not pid_raw.isdigit():
            continue
        pid = int(pid_raw)
        if pid > 0 and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def inspect_process(pid: int) -> dict:
    """查询指定 PID 的进程名、可执行文件路径与命令行（仅只读查询）。"""
    pid_int = int(pid)
    info = {
        "pid": pid_int,
        "name": "",
        "executable_path": "",
        "command_line": "",
    }
    if pid_int <= 0 or sys.platform != "win32":
        return info

    ps_script = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid_int}' -ErrorAction SilentlyContinue; "
        "if ($p) { $p | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=_WIN_NO_WINDOW,
        )
        raw = proc.stdout.strip()
        if proc.returncode == 0 and raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                info["name"] = (parsed.get("Name") or "").strip()
                info["executable_path"] = (parsed.get("ExecutablePath") or "").strip()
                info["command_line"] = (parsed.get("CommandLine") or "").strip()
                return info
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    # PowerShell 不可用或超时时的轻量降级查询
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid_int}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            creationflags=_WIN_NO_WINDOW,
        )
        for row in csv.reader(io.StringIO(proc.stdout)):
            if len(row) >= 2 and row[1].strip().isdigit() and int(row[1].strip()) == pid_int:
                info["name"] = row[0].strip()
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def verify_owned_server_pid(
    pid: int,
    port: int,
    healthz: dict | None,
    state: dict | None,
) -> tuple[bool, dict, str]:
    """严格核验指定 PID 是否确认为本仓库 Scavenger 主服务实例，防止误杀外部进程。"""
    proc_info = inspect_process(pid)
    proc_name = (proc_info.get("name") or "").strip().lower()
    if not proc_name.startswith("python"):
        return False, proc_info, "进程非 Python 解释器"

    cmd_norm = (proc_info.get("command_line") or "").replace("\\", "/").lower()
    abs_script = str((ROOT / "scripts" / "启动主服务.py").resolve()).replace("\\", "/").lower()
    cur_root_sha = compute_root_sha256()

    if abs_script in cmd_norm:
        return True, proc_info, "命令行匹配本仓库主服务绝对路径"

    has_rel_script = "启动主服务.py" in cmd_norm
    state_matches = isinstance(state, dict) and state.get("pid") == pid and state.get("root_sha256") == cur_root_sha
    healthz_matches = is_own_service_healthz(healthz, port) and healthz.get("pid") in (None, pid)

    if has_rel_script and (state_matches or healthz_matches):
        return True, proc_info, "命令行及服务身份均匹配本仓库主服务"

    if not cmd_norm and state_matches and healthz_matches:
        return True, proc_info, "状态文件 PID 与 /healthz 身份双匹配本仓库主服务"

    return False, proc_info, "非本仓库 Scavenger 主服务进程"


@contextmanager
def launcher_mutex(port: int, timeout: float = 10.0):
    """跨进程文件互斥锁，防止短时间内多次触发造成端口启动竞态。"""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_lock_file(port)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        locked = False
        if sys.platform == "win32":
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"等待主服务端口 {port} 启动锁超时（{timeout:.1f}s）")
                    time.sleep(0.05)
        try:
            yield
        finally:
            if locked and sys.platform == "win32":
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass


def request_graceful_shutdown(port: int, token: str, timeout: float = 2.0) -> bool:
    """绕过系统代理向本机主服务发送带口令的优雅停机请求。"""
    if not token:
        return False
    req = Request(
        f"http://{DEFAULT_HOST}:{port}{SHUTDOWN_PATH}",
        data=b"{}",
        method="POST",
        headers={
            "X-Scavenger-Token": token,
            "Content-Type": "application/json",
            "Connection": "close",
        },
    )
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            if getattr(resp, "status", 0) != 200:
                return False
            body = json.load(resp)
            return bool(isinstance(body, dict) and body.get("ok"))
    except (URLError, ValueError, OSError, TimeoutError):
        return False


def wait_port_closed(port: int, timeout: float = 4.0) -> bool:
    """使用单调时钟等待指定端口彻底释放。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_listening_pids(port) and not is_port_open(DEFAULT_HOST, port, timeout=0.15):
            return True
        time.sleep(0.08)
    return not find_listening_pids(port) and not is_port_open(DEFAULT_HOST, port, timeout=0.15)


def _build_foreign_owner_result(port: int, owners: list[dict], message: str | None = None) -> dict:
    return {
        "ok": False,
        "error": "foreign_port_owner",
        "port": port,
        "message": message or f"端口 {DEFAULT_HOST}:{port} 被外部进程占用，按安全策略只诊断不强杀",
        "owners": owners,
    }


def _stop_service_impl(port: int) -> dict:
    pids = find_listening_pids(port)
    port_open = is_port_open(DEFAULT_HOST, port, timeout=0.2)
    if not pids and not port_open:
        cleanup_runtime_state(port)
        return {"ok": True, "stopped": False, "reason": "not_running", "port": port}

    state = read_runtime_state(port)
    healthz = query_healthz(DEFAULT_HOST, port, timeout=1.0)

    # 1. 优先通过本地控制口令优雅关停（不强杀）
    state_token = state.get("control_token") if isinstance(state, dict) else None
    state_pid = state.get("pid") if isinstance(state, dict) else None
    if state_token and is_own_service_healthz(healthz, port) and (not pids or state_pid in pids):
        if request_graceful_shutdown(port, state_token, timeout=2.0) and wait_port_closed(port, timeout=4.0):
            cleanup_runtime_state(port, expected_pid=state_pid)
            return {"ok": True, "stopped": True, "method": "graceful", "pid": state_pid, "port": port}

    # 2. 针对旧版本实例或优雅关停未释放情况：逐 PID 严格核验身份，绝不触碰外部进程
    pids = find_listening_pids(port)
    if not pids:
        if wait_port_closed(port, timeout=1.5):
            cleanup_runtime_state(port)
            return {"ok": True, "stopped": True, "method": "graceful", "pid": state_pid, "port": port}
        return {
            "ok": False,
            "error": "port_busy_unknown_owner",
            "port": port,
            "message": f"端口 {DEFAULT_HOST}:{port} 仍处于占用状态但未能枚举到监听 PID，请人工核查。",
            "owners": [],
        }

    verified_pids: list[int] = []
    foreign_owners: list[dict] = []
    for pid in pids:
        owned, proc_info, reason = verify_owned_server_pid(pid, port, healthz, state)
        if owned:
            verified_pids.append(pid)
        else:
            foreign_owners.append({**proc_info, "reason": reason})

    if foreign_owners:
        return _build_foreign_owner_result(port, foreign_owners)

    for pid in verified_pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=_WIN_NO_WINDOW,
        )

    if wait_port_closed(port, timeout=4.0):
        cleanup_runtime_state(port)
        return {"ok": True, "stopped": True, "method": "taskkill_owned", "pids": verified_pids, "port": port}

    remaining = [inspect_process(p) for p in find_listening_pids(port)]
    return {
        "ok": False,
        "error": "stop_timeout",
        "port": port,
        "message": f"尝试停止端口 {DEFAULT_HOST}:{port} 的主服务超时，请人工检查残留进程。",
        "owners": remaining,
    }


def stop_service(port: int) -> dict:
    """加锁停止当前项目在指定端口运行的 Scavenger 主服务；外部进程绝不强杀。"""
    with launcher_mutex(port):
        return _stop_service_impl(port)


def _start_background_impl(port: int, verbose: bool = False) -> dict:
    pids = find_listening_pids(port)
    if pids or is_port_open(DEFAULT_HOST, port, timeout=0.15):
        owners = [inspect_process(p) for p in pids]
        return {
            "ok": False,
            "error": "port_in_use",
            "port": port,
            "message": f"端口 {DEFAULT_HOST}:{port} 已被占用，无法重复绑定。",
            "owners": owners,
        }

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_file = runtime_log_file(port)
    rotate_log_if_needed(log_file)
    log_offset = log_file.stat().st_size if log_file.is_file() else 0

    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        str(Path(__file__).resolve()),
        "--port",
        str(port),
        "--run-mode",
        "background",
    ]
    if verbose:
        cmd.append("--verbose")

    with log_file.open("ab") as stream:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            close_fds=True,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0,
        )

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            tail = read_log_tail(log_file, max_lines=30, from_offset=log_offset)
            owners = [inspect_process(p) for p in find_listening_pids(port)]
            return {
                "ok": False,
                "error": "process_exited_early",
                "exit_code": exit_code,
                "port": port,
                "log_file": log_file.relative_to(ROOT).as_posix(),
                "log_tail": tail,
                "owners": owners,
                "message": f"主服务后台子进程提前退出（退出码 {exit_code}）。",
            }
        info = query_healthz(DEFAULT_HOST, port, timeout=0.8)
        if is_own_service_healthz(info, port):
            return {
                "ok": True,
                "port": port,
                "pid": info.get("pid") or proc.pid,
                "healthz": info,
                "log_file": log_file.relative_to(ROOT).as_posix(),
            }
        time.sleep(0.10)

    tail = read_log_tail(log_file, max_lines=30, from_offset=log_offset)
    owners = [inspect_process(p) for p in find_listening_pids(port)]
    return {
        "ok": False,
        "error": "start_timeout",
        "port": port,
        "log_file": log_file.relative_to(ROOT).as_posix(),
        "log_tail": tail,
        "owners": owners,
        "message": f"主服务 {DEFAULT_HOST}:{port} 启动等待超时（12s）。",
    }


def ensure_running(
    port: int,
    route: str = "/",
    open_browser: bool = True,
    auto_restart_stale: bool = True,
    force_restart: bool = False,
    verbose: bool = False,
) -> dict:
    """确保指定端口运行着最新代码的 Scavenger 主服务，并按需打开指定页面路由。"""
    route_path = resolve_route_path(route)
    url = f"http://{DEFAULT_HOST}:{port}{route_path}"

    with launcher_mutex(port):
        current_sha = compute_script_sha256()
        healthz = query_healthz(DEFAULT_HOST, port, timeout=1.0)
        state = read_runtime_state(port)
        pids = find_listening_pids(port)
        port_open = bool(pids) or is_port_open(DEFAULT_HOST, port, timeout=0.2)

        action_taken = "reused"
        restarted_reason = None

        if is_own_service_healthz(healthz, port):
            stale = is_stale_instance(healthz, state, current_sha)
            if force_restart or (auto_restart_stale and stale):
                stop_res = _stop_service_impl(port)
                if not stop_res.get("ok"):
                    return stop_res
                start_res = _start_background_impl(port, verbose=verbose)
                if not start_res.get("ok"):
                    return start_res
                healthz = start_res["healthz"]
                action_taken = "restarted"
                restarted_reason = "force" if force_restart else "code_updated"
        elif not port_open:
            start_res = _start_background_impl(port, verbose=verbose)
            if not start_res.get("ok"):
                return start_res
            healthz = start_res["healthz"]
            action_taken = "started"
        else:
            # 端口有占用，但未返回有效的本仓库 /healthz
            foreign_owners: list[dict] = []
            owned_pids: list[int] = []
            for pid in pids:
                owned, proc_info, reason = verify_owned_server_pid(pid, port, healthz, state)
                if owned:
                    owned_pids.append(pid)
                else:
                    foreign_owners.append({**proc_info, "reason": reason})

            if foreign_owners or not owned_pids:
                owners = foreign_owners or ([inspect_process(p) for p in pids] if pids else [])
                return _build_foreign_owner_result(port, owners)

            if not (force_restart or auto_restart_stale):
                return {
                    "ok": False,
                    "error": "unhealthy_own_instance",
                    "port": port,
                    "message": f"端口 {DEFAULT_HOST}:{port} 上存在未响应 /healthz 的旧主服务进程，可使用 restart 恢复。",
                    "owners": [inspect_process(p) for p in owned_pids],
                }

            stop_res = _stop_service_impl(port)
            if not stop_res.get("ok"):
                return stop_res
            start_res = _start_background_impl(port, verbose=verbose)
            if not start_res.get("ok"):
                return start_res
            healthz = start_res["healthz"]
            action_taken = "recovered"
            restarted_reason = "unresponsive_stale"

    if open_browser:
        webbrowser.open(url)

    return {
        "ok": True,
        "status": "ready",
        "action": action_taken,
        "restarted_reason": restarted_reason,
        "port": port,
        "pid": healthz.get("pid") if isinstance(healthz, dict) else None,
        "url": url,
        "route": route_path,
        "healthz": healthz,
        "log_file": runtime_log_file(port).relative_to(ROOT).as_posix(),
    }


def collect_status(port: int) -> dict:
    """收集主服务状态（保持向后兼容字段，并在外部占用时附带占口进程诊断）。"""
    healthz = query_healthz(DEFAULT_HOST, port, timeout=1.0)
    own_running = is_own_service_healthz(healthz, port)
    pids = find_listening_pids(port)
    port_occupied = bool(pids) or is_port_open(DEFAULT_HOST, port, timeout=0.15)
    state = read_runtime_state(port)

    code_outdated = False
    pid = None
    mode = None
    owners: list[dict] = []
    if own_running and isinstance(healthz, dict):
        code_outdated = is_stale_instance(healthz, state, compute_script_sha256())
        pid = healthz.get("pid") or (state.get("pid") if isinstance(state, dict) else None) or (pids[0] if pids else None)
        mode = healthz.get("mode") or (state.get("mode") if isinstance(state, dict) else None) or "background"
    elif port_occupied:
        owners = [inspect_process(p) for p in pids]

    return {
        "running": own_running,
        "url": f"http://{DEFAULT_HOST}:{port}/",
        "healthz": healthz if own_running else None,
        "port": port,
        "port_occupied": port_occupied,
        "code_outdated": code_outdated,
        "pid": pid,
        "mode": mode,
        "owners": owners,
        "log_file": runtime_log_file(port).relative_to(ROOT).as_posix(),
    }


def format_owners_lines(owners: list[dict]) -> list[str]:
    """格式化端口占用进程清单，便于终端清晰排查。"""
    lines: list[str] = []
    for owner in owners:
        pid = owner.get("pid", "未知")
        name = owner.get("name") or "未知进程"
        exe = owner.get("executable_path") or ""
        cmd = owner.get("command_line") or ""
        lines.append(f"  - PID: {pid} | 进程名: {name}")
        if exe:
            lines.append(f"    路径: {exe}")
        if cmd:
            lines.append(f"    命令行: {cmd}")
    return lines


def format_failure_message(result: dict) -> str:
    """将启动/停机错误字典转换为可读中文诊断文本。"""
    port = result.get("port", DEFAULT_PORT)
    err = result.get("error", "unknown")
    lines = [f"[错误] Scavenger 主服务操作失败（127.0.0.1:{port}）：{result.get('message') or err}"]

    owners = result.get("owners") or []
    if err == "foreign_port_owner":
        lines.append("当前端口被以下外部进程占用（只诊断不强杀，请核对后人工关闭）：")
        if owners:
            lines.extend(format_owners_lines(owners))
        else:
            lines.append("  - 未能通过 netstat 解析出监听 PID，请使用管理员权限或资源监视器检查端口占用。")
    elif owners:
        lines.append("当前占用该端口的进程：")
        lines.extend(format_owners_lines(owners))

    log_file = result.get("log_file")
    if log_file:
        lines.append(f"日志文件：{log_file}")
    log_tail = (result.get("log_tail") or "").strip()
    if log_tail:
        lines.append("--- 最近日志输出 ---")
        lines.append(log_tail)
    return "\n".join(lines)


def create_server(port: int = DEFAULT_PORT, verbose: bool = False, run_mode: str = "background") -> UnifiedServer:
    """创建并独占绑定 127.0.0.1:<port> 的主服务实例。"""
    if is_port_open(DEFAULT_HOST, port, timeout=0.15):
        raise OSError(f"端口 {DEFAULT_HOST}:{port} 已被占用，请确认无重复主服务实例")
    try:
        return UnifiedServer((DEFAULT_HOST, port), UnifiedHandler, verbose=verbose, run_mode=run_mode)
    except OSError as exc:
        raise OSError(f"绑定端口 {DEFAULT_HOST}:{port} 失败：{exc}") from exc


def install_console_close_handler(server: UnifiedServer, stopped_event: threading.Event) -> dict:
    """注册 Windows 控制台关闭事件与 SIGTERM 处理器，确保关闭窗口时释放端口并清理状态文件。"""
    shutdown_started = threading.Event()

    def _trigger_shutdown() -> None:
        if not shutdown_started.is_set():
            shutdown_started.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    prev_sigterm = None
    try:
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: _trigger_shutdown())
    except (ValueError, OSError):
        prev_sigterm = None

    win_handle = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _ctrl_handler(ctrl_type: int) -> bool:
            # 0: CTRL_C, 1: CTRL_BREAK, 2: CTRL_CLOSE(点右上角关闭), 5: LOGOFF, 6: SHUTDOWN
            if ctrl_type in (0, 1, 2, 5, 6):
                _trigger_shutdown()
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

    return {"win_handle": win_handle, "prev_sigterm": prev_sigterm, "trigger": _trigger_shutdown}


def uninstall_console_close_handler(handle: dict | None) -> None:
    """卸载控制台与信号关闭处理器，避免引用残留。"""
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


def print_foreground_panel(port: int, pid: int, target_url: str, verbose: bool) -> None:
    """在前台控制台输出简洁明了的常驻运行状态板。"""
    base = f"http://{DEFAULT_HOST}:{port}"
    log_mode = "开启（打印 HTTP 请求流水）" if verbose else "简洁（排障可加 --verbose）"
    lines = [
        "",
        "============================================================",
        f"  Scavenger 统一主服务 · 前台常驻模式 (PID: {pid})",
        "============================================================",
        f"  当前打开 : {target_url}",
        f"  主菜单   : {base}/",
        f"  武器库   : {base}/armory",
        f"  9km 实战 : {base}/play",
        f"  地图编辑 : {base}/editor",
        f"  固定翼台 : {base}/air-lab",
        f"  UI 规范  : {base}/ui-spec",
        f"  日志输出 : {log_mode}",
        "------------------------------------------------------------",
        f"  [提示] 关闭此控制台窗口或按 Ctrl+C 即停止服务并释放 {port} 端口。",
        "============================================================",
    ]
    print("\n".join(lines), flush=True)


def _open_browser_when_ready(port: int, url: str, cancel_event: threading.Event) -> None:
    """在服务端点就绪后打开浏览器，若窗口已提前关闭则放弃打开。"""
    deadline = time.monotonic() + 4.0
    while not cancel_event.is_set() and time.monotonic() < deadline:
        if is_own_service_healthz(query_healthz(DEFAULT_HOST, port, timeout=0.4), port):
            if not cancel_event.is_set():
                webbrowser.open(url)
            return
        cancel_event.wait(0.08)


def run_foreground_service(
    port: int,
    route: str = "/",
    open_browser: bool = False,
    run_mode: str = "foreground",
    panel: bool = False,
    takeover: bool = False,
    verbose: bool = False,
    as_json: bool = False,
) -> int:
    """在前台运行常驻主服务（关闭窗口或中断时自动释放端口与状态文件）。"""
    route_path = resolve_route_path(route)
    url = f"http://{DEFAULT_HOST}:{port}{route_path}"

    if run_mode == "background":
        try:
            server = create_server(port=port, verbose=verbose, run_mode="background")
        except OSError as exc:
            print(f"[启动失败] {exc}", file=sys.stderr, flush=True)
            return 1
        write_runtime_state(server, runtime_log_file(server.server_port))
        print(json.dumps({
            "service": "scavenger-unified-server",
            "version": "V1.0",
            "launcher_version": LAUNCHER_VERSION,
            "mode": "background",
            "pid": os.getpid(),
            "host": DEFAULT_HOST,
            "port": server.server_port,
            "url_main": f"http://{DEFAULT_HOST}:{server.server_port}/",
            "url_armory": f"http://{DEFAULT_HOST}:{server.server_port}/armory",
            "url_play": f"http://{DEFAULT_HOST}:{server.server_port}/play",
            "url_editor": f"http://{DEFAULT_HOST}:{server.server_port}/editor",
            "url_ui_spec": f"http://{DEFAULT_HOST}:{server.server_port}/ui-spec",
        }, ensure_ascii=False), flush=True)
        stopped_event = threading.Event()
        ctrl_handle = install_console_close_handler(server, stopped_event)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            cleanup_runtime_state(server.server_port, expected_pid=os.getpid())
            server.server_close()
            stopped_event.set()
            uninstall_console_close_handler(ctrl_handle)
        return 0

    server: UnifiedServer | None = None
    reuse_pid: int | None = None
    with launcher_mutex(port):
        current_sha = compute_script_sha256()
        healthz = query_healthz(DEFAULT_HOST, port, timeout=1.0)
        state = read_runtime_state(port)
        pids = find_listening_pids(port)
        port_open = bool(pids) or is_port_open(DEFAULT_HOST, port, timeout=0.2)

        if is_own_service_healthz(healthz, port):
            stale = is_stale_instance(healthz, state, current_sha)
            remote_mode = (
                (healthz.get("mode") if isinstance(healthz, dict) else None)
                or (state.get("mode") if isinstance(state, dict) else None)
                or "background"
            )
            remote_pid = (
                (healthz.get("pid") if isinstance(healthz, dict) else None)
                or (state.get("pid") if isinstance(state, dict) else None)
                or (pids[0] if pids else None)
            )
            if remote_mode == "foreground" and not stale and not takeover:
                reuse_pid = remote_pid
            else:
                if not as_json:
                    reason_text = "旧版/已更新" if stale else "后台无窗"
                    print(f"[提示] 检测到{reason_text} Scavenger 实例（PID: {remote_pid}），正在接管为当前窗口前台常驻...", flush=True)
                stop_res = _stop_service_impl(port)
                if not stop_res.get("ok"):
                    _print_result(stop_res, as_json=as_json)
                    return 1
        elif port_open:
            foreign_owners: list[dict] = []
            owned_pids: list[int] = []
            for pid in pids:
                owned, proc_info, reason = verify_owned_server_pid(pid, port, healthz, state)
                if owned:
                    owned_pids.append(pid)
                else:
                    foreign_owners.append({**proc_info, "reason": reason})
            if foreign_owners or not owned_pids:
                owners = foreign_owners or ([inspect_process(p) for p in pids] if pids else [])
                _print_result(_build_foreign_owner_result(port, owners), as_json=as_json)
                return 1
            if not as_json:
                print(f"[提示] 检测到未响应的本仓库主服务进程（PID: {owned_pids}），正在清理接管...", flush=True)
            stop_res = _stop_service_impl(port)
            if not stop_res.get("ok"):
                _print_result(stop_res, as_json=as_json)
                return 1

        if reuse_pid is None:
            try:
                server = create_server(port=port, verbose=verbose, run_mode="foreground")
            except OSError as exc:
                print(f"[启动失败] {exc}", file=sys.stderr, flush=True)
                return 1
            write_runtime_state(server, runtime_log_file(server.server_port))

    if reuse_pid is not None:
        if open_browser:
            webbrowser.open(url)
        if as_json:
            print(json.dumps({
                "ok": True,
                "status": "reused_foreground",
                "mode": "foreground",
                "port": port,
                "pid": reuse_pid,
                "url": url,
                "route": route_path,
            }, ensure_ascii=False), flush=True)
            return 0
        print(f"[提示] Scavenger 主服务已在另一个前台窗口（PID: {reuse_pid}）常驻运行，本次不会关闭原窗口。", flush=True)
        print(f"[就绪] 访问地址 -> {url}（关闭原控制台窗口即可停止 {port} 端口）", flush=True)
        if panel and os.environ.get("SCAVENGER_NO_PAUSE") != "1" and sys.stdout.isatty():
            time.sleep(2.0)
        return 0

    assert server is not None
    stopped_event = threading.Event()
    cancel_browser = threading.Event()
    ctrl_handle = install_console_close_handler(server, stopped_event)

    if panel and not as_json:
        print_foreground_panel(server.server_port, os.getpid(), url, verbose)
    else:
        print(json.dumps({
            "service": "scavenger-unified-server",
            "version": "V1.0",
            "launcher_version": LAUNCHER_VERSION,
            "mode": "foreground",
            "pid": os.getpid(),
            "host": DEFAULT_HOST,
            "port": server.server_port,
            "url_main": f"http://{DEFAULT_HOST}:{server.server_port}/",
            "url_armory": f"http://{DEFAULT_HOST}:{server.server_port}/armory",
            "url_play": f"http://{DEFAULT_HOST}:{server.server_port}/play",
            "url_editor": f"http://{DEFAULT_HOST}:{server.server_port}/editor",
            "url_ui_spec": f"http://{DEFAULT_HOST}:{server.server_port}/ui-spec",
        }, ensure_ascii=False), flush=True)

    if open_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(server.server_port, url, cancel_browser),
            daemon=True,
        ).start()

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        cancel_browser.set()
        cleanup_runtime_state(server.server_port, expected_pid=os.getpid())
        server.server_close()
        stopped_event.set()
        uninstall_console_close_handler(ctrl_handle)
        if panel and not as_json:
            print(f"\n[已关闭] Scavenger 主服务已停止，127.0.0.1:{port} 端口已释放。", flush=True)
    return 0


def interactive_menu(port: int) -> int:
    """轻量控制台交互菜单：实时展示状态并提供模块直达、热重启与停服快捷操作。"""
    menu_routes = {
        "1": ("主菜单大厅", "/"),
        "2": ("129 车武器库", "/armory"),
        "3": ("9km 实战战场", "/play"),
        "4": ("9km 地图编辑器", "/editor"),
        "5": ("全局 UI 规范台", "/ui-spec"),
        "6": ("前线资产库", "/dev/library/"),
        "7": ("系统设置", "/settings"),
    }
    while True:
        st = collect_status(port)
        print("\n=== Scavenger 主服务控制台 ===", flush=True)
        print(f"监听地址: http://{DEFAULT_HOST}:{port}/", flush=True)
        if st["running"]:
            hz = st.get("healthz") or {}
            uptime = hz.get("uptime_seconds", "-")
            mode_label = "前台常驻" if st.get("mode") == "foreground" else "后台运行"
            outdated = " [代码已更新，将在打开/重启时平滑热更新]" if st.get("code_outdated") else " [代码最新]"
            print(f"当前状态: 运行中（{mode_label}） | PID: {st.get('pid')} | 运行时长: {uptime}s{outdated}", flush=True)
        elif st["port_occupied"]:
            print("当前状态: 端口被非本仓库外部进程占用（只诊断不强杀）", flush=True)
            for line in format_owners_lines(st.get("owners") or []):
                print(line, flush=True)
        else:
            print("当前状态: 未运行", flush=True)

        print("快捷键: [1]大厅 [2]武器库 [3]实战 [4]编辑器 [5]UI规范 [6]资产库 [7]设置", flush=True)
        print("        [r]平滑重启 [s]停止服务 [l]查看最近日志 [回车]刷新 [q]退出", flush=True)
        try:
            choice = input("请选择操作: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice in ("0", "q", "exit"):
            return 0
        if not choice:
            continue
        if choice in menu_routes:
            label, rt = menu_routes[choice]
            res = ensure_running(port=port, route=rt, open_browser=True)
            if res.get("ok"):
                print(f"[就绪] 已打开 {label} -> {res['url']} (PID: {res.get('pid')})")
            else:
                print(format_failure_message(res), file=sys.stderr)
        elif choice == "r":
            res = ensure_running(port=port, open_browser=False, force_restart=True)
            if res.get("ok"):
                print(f"[完成] 服务已平滑重启 (PID: {res.get('pid')})")
            else:
                print(format_failure_message(res), file=sys.stderr)
        elif choice == "s":
            res = stop_service(port=port)
            if res.get("ok"):
                msg = "已优雅停止" if res.get("stopped") else "当前未运行"
                print(f"[完成] 服务{msg}")
            else:
                print(format_failure_message(res), file=sys.stderr)
        elif choice == "l":
            tail = read_log_tail(runtime_log_file(port), max_lines=30)
            print(tail if tail else "[提示] 暂无运行日志输出。")
        else:
            print(f"[提示] 无效指令：{choice}")


def _print_result(res: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res, ensure_ascii=False), flush=True)
        return
    if not res.get("ok"):
        print(format_failure_message(res), file=sys.stderr, flush=True)
        return
    if "stopped" in res:
        if res["stopped"]:
            print(f"[成功] 已停止 Scavenger 主服务（127.0.0.1:{res['port']}，方式：{res.get('method')}）。", flush=True)
        else:
            print(f"[提示] Scavenger 主服务（127.0.0.1:{res['port']}）当前未运行。", flush=True)
        return
    action_labels = {
        "started": "已启动",
        "reused": "已复用",
        "restarted": "已平滑重启",
        "recovered": "已自动恢复",
    }
    label = action_labels.get(res.get("action", ""), "已就绪")
    print(f"[就绪] Scavenger 主服务{label}（PID: {res.get('pid')}）-> {res.get('url')}", flush=True)


def _resolve_cli_action(args: argparse.Namespace) -> str:
    flag_actions = []
    for name in ("open", "status", "start", "stop", "restart", "menu", "logs"):
        if getattr(args, name, False):
            flag_actions.append(name)

    if len(flag_actions) > 1:
        raise ValueError(f"互斥的控制参数不能同时指定：{', '.join('--' + f for f in flag_actions)}")

    if args.action and flag_actions:
        flag = flag_actions[0]
        if args.action == flag:
            return args.action
        if args.action == "serve" and flag == "open":
            return "serve"
        if args.action in QUICK_ROUTE_ACTIONS and flag in ("open", "restart"):
            return args.action
        raise ValueError(f"子命令 {args.action} 与参数 --{flag} 冲突")

    if args.action:
        return args.action
    if flag_actions:
        return flag_actions[0]
    return "serve"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用子命令：\n"
            "  serve / play / editor / armory / settings / ui-spec / library / audio  前台常驻运行并打开对应页面（关窗即停服）\n"
            "  start / open / stop / restart / status / logs / menu                   后台启停、状态与日志维护\n"
        ),
    )
    parser.add_argument("action", nargs="?", choices=ALL_ACTIONS, help="可选子命令或快捷路由别名")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="唯一主服务端口（默认 8800）")
    parser.add_argument("--open", action="store_true", help="启动或复用主服务后在浏览器打开目标路由")
    parser.add_argument("--panel", action="store_true", help="前台常驻时显示中文状态面板")
    parser.add_argument("--takeover", action="store_true", help="前台启动时强制接管已有前台常驻实例")
    parser.add_argument("--run-mode", choices=["foreground", "background"], default="foreground", help=argparse.SUPPRESS)
    parser.add_argument("--start", action="store_true", help="后台启动或复用最新主服务（不打开浏览器）")
    parser.add_argument("--stop", action="store_true", help="优雅停止当前项目主服务")
    parser.add_argument("--restart", action="store_true", help="平滑重启当前项目主服务")
    parser.add_argument("--status", action="store_true", help="只读查询主服务当前状态（JSON）")
    parser.add_argument("--menu", action="store_true", help="打开交互式中文控制台菜单")
    parser.add_argument("--logs", action="store_true", help="查看主服务最近日志输出")
    parser.add_argument("--route", default="/", help="指定打开的路由或别名（默认 /）")
    parser.add_argument("--lines", type=int, default=30, help="查看日志的末尾行数（默认 30）")
    parser.add_argument("--no-auto-restart", action="store_true", help="禁用检测到脚本变更时自动平滑重启")
    parser.add_argument("--json", action="store_true", help="以单行 JSON 格式输出结果")
    parser.add_argument("--verbose", action="store_true", help="输出 HTTP 请求日志")
    args = parser.parse_args()

    try:
        action = _resolve_cli_action(args)
    except ValueError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        return 2

    if action == "help":
        parser.print_help()
        return 0

    if action == "status":
        info = collect_status(args.port)
        print(json.dumps(info, ensure_ascii=False), flush=True)
        return 0 if info["running"] else 1

    if action == "logs":
        log_file = runtime_log_file(args.port)
        tail = read_log_tail(log_file, max_lines=max(1, args.lines))
        rel_log = log_file.relative_to(ROOT).as_posix()
        if args.json:
            print(json.dumps({"ok": True, "port": args.port, "log_file": rel_log, "lines": tail.splitlines()}, ensure_ascii=False), flush=True)
        elif tail:
            print(f"=== {rel_log}（最近 {max(1, args.lines)} 行）===\n{tail}", flush=True)
        else:
            print(f"[提示] 暂无日志记录（{rel_log}）。", flush=True)
        return 0

    if action == "menu":
        return interactive_menu(args.port)

    if action == "stop":
        res = stop_service(port=args.port)
        _print_result(res, as_json=args.json)
        return 0 if res.get("ok") else 1

    if action in ("start", "open", "restart"):
        try:
            target_route = resolve_route_path(args.route)
            open_browser = action != "start" and (action != "restart" or bool(args.open))
            res = ensure_running(
                port=args.port,
                route=target_route,
                open_browser=open_browser,
                auto_restart_stale=not args.no_auto_restart,
                force_restart=(action == "restart"),
                verbose=args.verbose,
            )
        except Exception as exc:
            res = {"ok": False, "error": "launcher_exception", "port": args.port, "message": str(exc)}
        _print_result(res, as_json=args.json)
        return 0 if res.get("ok") else 1

    # 默认与快捷页面子命令：前台常驻模式（关闭控制台窗口即停止服务并释放端口）
    target_route = args.route if args.route != "/" else ROUTE_ALIASES.get(action, "/")
    is_quick_route = action in QUICK_ROUTE_ACTIONS
    return run_foreground_service(
        port=args.port,
        route=target_route,
        open_browser=bool(args.open) or is_quick_route,
        run_mode=args.run_mode,
        panel=bool(args.panel) or is_quick_route,
        takeover=bool(args.takeover),
        verbose=args.verbose,
        as_json=args.json,
    )


if __name__ == "__main__":
    sys.exit(main())
