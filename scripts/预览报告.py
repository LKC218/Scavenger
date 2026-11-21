"""在本机提供报告预览，不自动打开浏览器。"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser(description="仅在本机预览研究报告")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as error:
        parser.exit(1, f"无法启动本机预览：{error}\n")
    page = quote("reports/RESTAND玩法交互研究.html")
    print(f"报告地址：http://127.0.0.1:{server.server_port}/{page}", flush=True)
    print("服务仅绑定本机；按 Ctrl+C 结束。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n预览已结束。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
