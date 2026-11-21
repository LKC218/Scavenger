"""用隔离的小资源包核验 AssetRipper 2.0.0 的本机 HTTP 接口。"""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]


def request(base, path, data=None, timeout=120):
    body = urlencode(data, doseq=True).encode() if data is not None else None
    with urlopen(Request(base + path, data=body), timeout=timeout) as response:
        return response.read(), response.status


def links(page, prefix):
    return sorted(set(html.unescape(value) for value in re.findall(r'href="([^"]+)"', page) if value.startswith(prefix)))


def asset_rows(page):
    """集合页包含类名、对象名和对象链接，可构造离线索引。"""
    rows = []
    for class_name, row in re.findall(r'<tr data-class="([^"]+)">(.*?)</tr>', page, re.S):
        anchor = re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if anchor:
            rows.append({"class": html.unescape(class_name), "name": html.unescape(re.sub(r'<[^>]+>', '', anchor[2])), "link": html.unescape(anchor[1])})
    return rows


def glb_summary(data):
    magic, version, total = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise ValueError("模型接口未返回有效 GLB 2 文件")
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError("GLB 第一块不是 JSON")
    document = json.loads(data[20:20 + length])
    vertex_count = sum(accessor.get("count", 0) for accessor in document.get("accessors", []) if accessor.get("type") == "VEC3")
    mesh_count = len(document.get("meshes", []))
    return {"bytes": len(data), "meshes": mesh_count, "materials": len(document.get("materials", [])), "nodes": len(document.get("nodes", [])), "accessors": len(document.get("accessors", [])), "vec3_element_count": vertex_count, "contains_geometry": mesh_count > 0 and vertex_count > 0}


def bundle_header(path):
    with path.open("rb") as stream:
        data = stream.read(512)
    signature, rest = data.split(b"\0", 1)
    version = struct.unpack_from(">I", rest)[0]
    engine, revision, _ = rest[4:].split(b"\0", 2)
    return {"signature": signature.decode("ascii"), "file_version": version, "engine": engine.decode("ascii"), "revision": revision.decode("ascii")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--包", type=Path)
    args = parser.parse_args()
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
    game = Path(config["game_directory"])
    source = args.包 or next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("*_unitybuiltinassets_*.bundle"))
    source = source.resolve()
    if source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("接口探针只允许不超过 2 MiB 的小包")
    folder = ROOT / "tmp/断箭/AssetRipper探针" / f"运行-{int(time.time() * 1000)}"
    inputs = folder / "输入"
    inputs.mkdir(parents=True)
    isolated = inputs / source.name
    shutil.copyfile(source, isolated)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    program = ROOT / config["tools"]["AssetRipper"]
    result = {"input": str(source), "input_bytes": source.stat().st_size, "input_sha256": source_hash, "bundle_header": bundle_header(source), "base_url": base, "observations": {}}
    wait_event = threading.Event()
    with (folder / "服务标准输出.log").open("wb") as stdout:
        process = subprocess.Popen(
            [str(program), "--headless", "--port", str(port), "--log-path", str(folder / "AssetRipper.log")],
            cwd=folder, stdout=stdout, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result["process_id"] = process.pid
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    home, _ = request(base, "/", timeout=1)
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("AssetRipper 服务启动后退出")
                    wait_event.wait(min(.2, max(0, deadline - time.time())))
            else:
                raise TimeoutError("AssetRipper 服务未在预算时间内就绪")
            start = time.time()
            loaded, status = request(base, "/LoadFile", {"Path": str(isolated)}, timeout=180)
            result["observations"]["load"] = {"http_status": status, "seconds": time.time() - start}
            (folder / "加载页面.html").write_bytes(loaded)
            root_page, _ = request(base, "/Bundles/View?" + urlencode({"Path": json.dumps({"P": []})}))
            (folder / "根资源包.html").write_bytes(root_page)
            queue = links(root_page.decode(), "/Bundles/View")
            collections = links(root_page.decode(), "/Collections/View")
            seen = set()
            while queue:
                link = queue.pop(0)
                if link in seen:
                    continue
                seen.add(link)
                page, _ = request(base, link)
                (folder / f"包-{len(seen):02}.html").write_bytes(page)
                queue.extend(value for value in links(page.decode(), "/Bundles/View") if value not in seen)
                collections.extend(links(page.decode(), "/Collections/View"))
            asset_links = []
            assets = []
            for index, link in enumerate(sorted(set(collections))):
                page, _ = request(base, link)
                (folder / f"集合-{index:02}.html").write_bytes(page)
                asset_links.extend(links(page.decode(), "/Assets/View"))
                assets.extend(asset_rows(page.decode()))
            result["observations"]["bundle_pages"] = len(seen)
            result["observations"]["collection_links"] = sorted(set(collections))
            result["observations"]["asset_links"] = sorted(set(asset_links))
            result["observations"]["assets"] = assets
            for index, link in enumerate(sorted(set(asset_links))[:5]):
                query = urlsplit(link).query
                page, _ = request(base, "/Assets/Json?" + query)
                (folder / f"对象-{index:02}.json").write_bytes(page)
            meshes = [asset for asset in assets if asset["class"] == "Mesh"]
            if meshes:
                source_meshes = [asset for asset in meshes if json.loads(parse_qs(urlsplit(asset["link"]).query)["Path"][0])["C"]["B"]["P"] == [0]]
                mesh = source_meshes[0] if source_meshes else next((asset for asset in meshes if asset["name"] == "Cube"), meshes[0])
                query = urlsplit(mesh["link"]).query
                mesh_json, _ = request(base, "/Assets/Json?" + query)
                (folder / "选定网格.json").write_bytes(mesh_json)
                glb, _ = request(base, "/Assets/Model.glb?" + query)
                (folder / "选定网格.glb").write_bytes(glb)
                result["observations"]["mesh_export"] = {"asset": mesh, **glb_summary(glb)}
            textures = [asset for asset in assets if asset["class"] == "Texture2D"]
            if textures:
                texture = textures[0]
                query = urlsplit(texture["link"]).query
                png, _ = request(base, "/Assets/Image?" + query + "&Extension=png")
                if png[:8] != b"\x89PNG\r\n\x1a\n":
                    raise ValueError("图片接口未返回 PNG")
                (folder / "选定纹理.png").write_bytes(png)
                result["observations"]["texture_export"] = {"asset": texture, "bytes": len(png)}
            result["source_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            result["service_stopped"] = process.poll() is not None
            (folder / "探针结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"folder": str(folder), "input_bytes": result["input_bytes"], "asset_count": len(result["observations"].get("assets", [])), "mesh_export": result["observations"].get("mesh_export"), "texture_export": result["observations"].get("texture_export"), "source_unchanged": result.get("source_unchanged"), "service_stopped": result["service_stopped"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
