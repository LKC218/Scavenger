"""丧尸群体分享包的本机回环服务，不依赖源项目统一资源库。"""
from collections import deque
import argparse
import csv
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
import webbrowser

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


VERSION = "1.0.0"
RELEASE_FILE = Path("data/发布资源清单.json")
RUNTIME_SOURCES = [
    "src/丧尸群体测试/index.html",
    "src/丧尸群体测试/界面.css",
    "src/丧尸群体测试/入口.mjs",
    "src/丧尸群体测试/群体.mjs",
    "src/丧尸群体测试/角色.mjs",
    "src/丧尸群体测试/角色配置.mjs",
    "src/丧尸群体测试/场景.mjs",
    "src/丧尸群体测试/统计.mjs",
    "src/资源加载/压缩模型加载.mjs",
    "scripts/发布服务.py",
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def code_digest(root):
    digest = hashlib.sha256()
    for relative in RUNTIME_SOURCES:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def checked_asset(root, relative, expected_sha, expected_bytes):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"发布资源不存在或越界：{relative}")
    if path.stat().st_size != expected_bytes or digest_file(path) != expected_sha:
        raise ValueError(f"发布资源摘要不匹配：{relative}")
    return path


def load_release(root, requested_resolution):
    release = read_json(root / RELEASE_FILE)
    if release.get("schema") != 1:
        raise ValueError("发布资源清单协议无效")
    if release.get("code_sha256") != code_digest(root):
        raise ValueError("发布源码摘要不匹配，请重新生成发布包")
    models = release.get("models")
    if not isinstance(models, list) or len(models) != 13:
        raise ValueError("发布资源清单必须包含十三款角色")
    expected_ids = {"model:zombie-runtime"} | {
        f"model:zombie-{index:02d}" for index in range(1, 13)
    }
    if {model.get("id") for model in models} != expected_ids:
        raise ValueError("十三款角色稳定ID不完整")
    if {model.get("source_mesh_index") for model in models} != set(range(13)):
        raise ValueError("十三款源网格编号不完整")

    routes = {}
    public_models = []
    for model in sorted(models, key=lambda row: row["source_mesh_index"]):
        variants = model.get("variants", [])
        if {variant.get("resolution") for variant in variants} != {256, 512}:
            raise ValueError(f"角色双档规格不完整：{model.get('id')}")
        selected = next(
            variant for variant in variants
            if variant.get("resolution") == requested_resolution
        )
        asset_path = checked_asset(
            root,
            selected["path"],
            selected["sha256"],
            selected["bytes"],
        )
        url = f"/actors/{model['source_mesh_index']:02d}-{requested_resolution}.glb"
        routes[url] = asset_path
        public_models.append({
            "id": model["id"],
            "source_mesh_index": model["source_mesh_index"],
            "source_mesh_name": model["source_mesh_name"],
            "bones": model["bones"],
            "materials": model["materials"],
            "triangles": model["triangles"],
            "clips": model["clips"],
            "coordinate_system": model["coordinate_system"],
            "url": url,
            "asset": {
                "sha256": selected["sha256"],
                "bytes": selected["bytes"],
                "resolution": requested_resolution,
            },
        })
    runtime_manifest = {
        "app": "scavenger-horde",
        "three": "0.187.0",
        "delivery": release["delivery"],
        "models": public_models,
        "asset": public_models[0]["asset"],
        "code_sha256": release["code_sha256"],
        "package": release["package"],
    }
    return release, runtime_manifest, routes


class ForegroundObserver:
    """只读记录本机可见浏览器是否位于Windows前台，不记录其他窗口标题。"""

    def __init__(self, directory):
        self.directory = Path(directory) / "观察"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events = deque(maxlen=28800)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def proof(self):
        result = {"epoch": time.time(), "iab": False, "chrome": False, "edge": False}
        if sys.platform != "win32":
            return result
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(),
            ctypes.byref(process_id),
        )
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return result
        try:
            buffer = ctypes.create_unicode_buffer(2048)
            size = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                name = Path(buffer.value).name.casefold()
                result.update(
                    iab=name == "chatgpt.exe",
                    chrome=name == "chrome.exe",
                    edge=name == "msedge.exe",
                )
        finally:
            kernel32.CloseHandle(handle)
        return result

    def run(self):
        started = time.monotonic()
        log_path = self.directory / "Windows前台观察.jsonl"
        with log_path.open("a", encoding="utf-8", buffering=1) as stream:
            while not self.stop.is_set():
                row = self.proof()
                self.events.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                now = time.monotonic()
                next_at = started + (int((now - started) / 0.5) + 1) * 0.5
                self.stop.wait(max(0, next_at - time.monotonic()))

    def coverage(self, start, end, browser):
        rows = [
            row for row in list(self.events)
            if start - 0.6 <= row["epoch"] <= end + 0.6
        ]
        covered = (
            bool(rows)
            and rows[0]["epoch"] <= start + 0.6
            and rows[-1]["epoch"] >= end - 0.6
        )
        gaps = [right["epoch"] - left["epoch"] for left, right in zip(rows, rows[1:])]
        verified = (
            covered
            and max(gaps, default=0) <= 1.2
            and all(row.get(browser, False) for row in rows)
        )
        foreground_count = sum(row.get(browser, False) for row in rows)
        return {
            "nativeForegroundVerified": verified,
            "nativeForegroundChecks": len(rows),
            "foregroundObservation": {
                "covered": covered,
                "maxGapS": max(gaps, default=0),
                "foregroundPct": 100 * foreground_count / len(rows) if rows else 0,
                "scope": "预热及正式采样，Windows前台进程只读观察；不记录其他应用名称或标题",
            },
        }

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def qualification(result):
    config = result["config"]
    summary = result["summary"]
    return bool(
        result["valid"]
        and result["nativeForegroundVerified"]
        and result["requestedDurationS"] >= 180
        and summary["elapsedS"] >= 179.5
        and summary["fps"] >= 60
        and summary["p95Ms"] <= 16.7
        and summary["over33Pct"] < 0.1
        and summary["simulationDroppedS"] == 0
        and summary["minVisible"] == config["count"]
        and summary["minAnimated"] == config["count"]
        and config["mode"] in ("open", "channel")
        and all(
            model["complete"]
            and model["minVisible"] == model["count"]
            and model["minAnimated"] == model["count"]
            for model in result["models"]
        )
    )


def store_sample(result, manifest, observer, directory):
    if not isinstance(result, dict) or result.get("schema") != 2:
        raise ValueError("样本协议无效")
    config = result.get("config", {})
    delivery = config.get("delivery") or {}
    if (
        config.get("code_sha256") != manifest["code_sha256"]
        or delivery.get("sha256") != manifest["delivery"]["sha256"]
    ):
        raise ValueError("样本源码或资源身份不匹配")
    if config.get("actorSet") not in ("single", "mixed"):
        raise ValueError("样本角色集合无效")
    if config.get("mode") not in ("static", "animated", "open", "channel"):
        raise ValueError("样本模式无效")
    models = config.get("models") or []
    rows = result.get("rows") or []
    if sum(model.get("count", 0) for model in models) != config.get("count"):
        raise ValueError("逐款样本分母不完整")
    if len(result.get("models") or []) != 13 or not rows:
        raise ValueError("十三款逐帧记录不完整")
    if result.get("endEpoch", 0) < result.get("startEpoch", 0):
        raise ValueError("样本时间无效")
    browser = (result.get("environment") or {}).get("browserLabel")
    if browser not in ("iab", "chrome", "edge"):
        raise ValueError("采样浏览器未标明")

    result.update(observer.coverage(result["startEpoch"], result["endEpoch"], browser))
    result["browser"] = browser
    result["headless"] = False
    result["windowMode"] = "内置可见浏览器" if browser == "iab" else "独立可见浏览器"
    result["stable60Passed"] = qualification(result)

    sample_dir = Path(directory) / "样本"
    sample_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    name = (
        f"{browser}-{config['actorSet']}-{config['count']}-{config['mode']}-"
        f"{config['view']['internal'][0]}-{result['requestedDurationS']}s-{stamp}"
    )
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    json_path = sample_dir / f"{name}.json"
    with json_path.open("xb") as stream:
        stream.write(encoded)

    columns = [
        "t",
        "frame",
        "simulation",
        "animation",
        "sync",
        "submit",
        "gpu",
        "calls",
        "triangles",
        "visible",
        "animated",
        "moving",
        "neighbors",
        "dropped",
    ]
    with (sample_dir / f"{name}.csv").open(
        "x",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        for label, value in [
            ("配置", config),
            ("环境", result["environment"]),
            (
                "判定",
                {
                    "valid": result["valid"],
                    "nativeForegroundVerified": result["nativeForegroundVerified"],
                    "stable60Passed": result["stable60Passed"],
                },
            ),
        ]:
            stream.write(
                "# " + label + " " + json.dumps(value, ensure_ascii=False) + "\n"
            )
        writer = csv.writer(stream)
        writer.writerow(
            columns
            + [
                f"{model['id']}:{key}"
                for model in config["models"]
                for key in ("count", "visible", "animated", "moving")
            ]
        )
        for row in rows:
            writer.writerow(
                [row.get(key) for key in columns]
                + [
                    model[key]
                    for model in row["models"]
                    for key in ("count", "visible", "animated", "moving")
                ]
            )
    return {
        "file": json_path.name,
        "sha256": digest_bytes(encoded),
        "nativeForegroundVerified": result["nativeForegroundVerified"],
        "stable60Passed": result["stable60Passed"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def do_POST(self):
        path = unquote(urlsplit(self.path).path, errors="strict")
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        if path != "/api/samples" or self.headers.get("Origin") != expected_origin:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 64 * 1024 * 1024:
                raise ValueError("样本大小超出范围")
            current_digest = code_digest(self.server.root)
            if current_digest != self.server.manifest["code_sha256"]:
                raise ValueError("运行期间源码变化，请重启并重新采样")
            result = json.loads(self.rfile.read(length))
            receipt = store_sample(
                result,
                self.server.manifest,
                self.server.observer,
                self.server.data_dir,
            )
            body = json.dumps(receipt, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc).encode("ascii", "backslashreplace").decode())

    def serve(self, head):
        try:
            request_path = unquote(urlsplit(self.path).path, errors="strict")
            if (
                "\\" in request_path
                or ".." in request_path.split("/")
                or request_path.startswith("//")
            ):
                self.send_error(404)
                return
            if request_path == "/api/status":
                body = json.dumps(
                    {
                        "app": "scavenger-horde",
                        "version": VERSION,
                        "package": self.server.release["package"]["id"],
                        "resolution": self.server.resolution,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                mime = "application/json; charset=utf-8"
            elif request_path == "/api/foreground":
                body = json.dumps(
                    self.server.observer.proof(),
                    ensure_ascii=False,
                ).encode("utf-8")
                mime = "application/json; charset=utf-8"
            elif request_path == "/manifest.json":
                body = json.dumps(
                    self.server.manifest,
                    ensure_ascii=False,
                ).encode("utf-8")
                mime = "application/json; charset=utf-8"
            elif request_path in self.server.source_bytes:
                body = self.server.source_bytes[request_path]
                mime = (
                    "text/css; charset=utf-8"
                    if request_path.endswith(".css")
                    else "text/html; charset=utf-8"
                    if request_path in ("/", "/index.html")
                    else "text/javascript; charset=utf-8"
                )
            else:
                path = self.server.routes.get(request_path)
                if not path or not path.is_file():
                    self.send_error(404)
                    return
                body = path.read_bytes()
                mime = (
                    "text/javascript; charset=utf-8"
                    if path.suffix in (".mjs", ".js")
                    else mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream"
                )
            self.send_response(200)
            for key, value in {
                "Content-Type": mime,
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' 'unsafe-eval'; "
                    "worker-src 'self' blob:; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; "
                    "connect-src 'self' data: blob:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                ),
            }.items():
                self.send_header(key, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def create_server(root, data_dir, port, resolution):
    root = Path(root).resolve()
    release, manifest, asset_routes = load_release(root, resolution)
    app_root = root / "src" / "丧尸群体测试"
    static_files = [
        "index.html",
        "界面.css",
        "入口.mjs",
        "群体.mjs",
        "角色.mjs",
        "角色配置.mjs",
        "场景.mjs",
        "统计.mjs",
    ]
    routes = {
        "/": app_root / "index.html",
        **{f"/{name}": app_root / name for name in static_files},
        **asset_routes,
    }
    vendor_root = root / "runtime" / "vendor"
    for path in vendor_root.rglob("*"):
        if path.is_file():
            routes["/vendor/" + path.relative_to(vendor_root).as_posix()] = path
    routes["/compressed-loader.mjs"] = (
        root / "src" / "资源加载" / "压缩模型加载.mjs"
    )

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.root = root
    server.data_dir = Path(data_dir).resolve()
    server.data_dir.mkdir(parents=True, exist_ok=True)
    server.release = release
    server.manifest = manifest
    server.resolution = resolution
    server.routes = routes
    server.source_bytes = {
        url: routes[url].read_bytes()
        for url in ("/", *[f"/{name}" for name in static_files])
    }
    server.observer = ForegroundObserver(server.data_dir)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--resolution", type=int, choices=(256, 512), default=256)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    data_dir = args.data_dir or root / "运行数据"
    server = create_server(root, data_dir, args.port, args.resolution)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"丧尸群体测试：{url}")
    print("关闭本窗口或按 Ctrl+C 可停止服务。")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.observer.close()
        server.server_close()


if __name__ == "__main__":
    main()
