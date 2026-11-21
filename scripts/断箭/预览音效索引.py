"""仅在127.0.0.1提供索引页与显式资源路由，不开放项目目录浏览。"""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from 探测音效 import ROOT, digest, local_batch


def resource_map(batch, archive_manifest=None):
    output = batch / "音效索引库"
    manifest = json.loads((batch / "输出核验.json").read_text(encoding="utf-8"))
    allowed, expected = {}, {}
    for row in manifest["files"]:
        path = (batch / row["path"]).resolve()
        if not path.is_relative_to(batch.resolve()) or digest(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("索引输出发生变化，请重新生成或检查来源")
        if path.is_relative_to(output.resolve()) and path.suffix in (".html", ".mjs", ".js", ".json", ".css", ".md"):
            key = path.relative_to(output).as_posix()
            allowed[key] = (path, "text/javascript" if path.suffix in (".mjs", ".js") else mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            expected[key] = row
    table = json.loads((batch / "资源路由.json").read_text(encoding="utf-8"))["routes"]
    parent = (ROOT / "local-data/断箭/批次").resolve()
    resolver = None
    if archive_manifest:
        from 历史资源解析 import ArchiveResolver
        resolver = ArchiveResolver(archive_manifest)
    for url, row in table.items():
        path = Path(row["path"]).resolve()
        if not url.startswith("资源/") or ".." in url.split("/") or "\\" in url or not path.is_relative_to(parent):
            raise ValueError("资源路由超出允许边界")
        if resolver and resolver.has(path): path = resolver.resolve(path)
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("资源载荷与路由摘要不符：" + url)
        allowed[url] = (path, row["type"])
        expected[url] = row
    allowed[""] = allowed["索引验证.html"]
    expected[""] = expected["索引验证.html"]
    return allowed, expected


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, allowed, expected, **kwargs):
        self.allowed, self.expected = allowed, expected
        super().__init__(*args, **kwargs)

    def do_HEAD(self):
        self.serve(head=True)

    def do_GET(self):
        self.serve()

    def serve(self, head=False):
        try:
            key = unquote(urlsplit(self.path).path, errors="strict").removeprefix("/")
        except (UnicodeError, ValueError):
            self.send_error(400)
            return
        if "\\" in key or ".." in key.split("/") or key.startswith("/") or key not in self.allowed:
            self.send_error(404)
            return
        path, mime = self.allowed[key]
        try:
            size = path.stat().st_size
        except OSError:
            self.send_error(404)
            return
        if size != self.expected[key]["bytes"]:
            self.send_error(409, "Resource changed")
            return
        start, end, status = 0, size - 1, 200
        ranges = self.headers.get("Range")
        if ranges:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranges)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            first, last = match.groups()
            if first:
                start, end = int(first), min(size - 1, int(last)) if last else size - 1
            else:
                start = max(0, size - int(last))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/json" else ""))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self' blob: data:; media-src 'self' blob:; worker-src 'none'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


def create_server(batch, port, archive_manifest=None):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 启动正式资料库 import create_server as unified_server
        return unified_server(port)
    allowed, expected = resource_map(batch, archive_manifest)
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, allowed=allowed, expected=expected))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-004")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--archive-manifest", type=Path, help="显式使用历史媒体迁移对照，不修改原路由")
    args = parser.parse_args()
    server = create_server(local_batch(args.batch), args.port, args.archive_manifest)
    print(f"本地索引验证：http://127.0.0.1:{server.server_port}/", flush=True)
    print("仅服务当前索引和已登记资源；按Ctrl+C结束。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
