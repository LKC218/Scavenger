"""冻结G01移动候选并在独立8811启动Airbase演训，不接管旧入口。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/战术演训"
OUTPUT = ROOT / "local-data/战术演训/G01移动指挥闭环/V001-01a1ac6a"
BASELINE = OUTPUT / "受保护基线.json"
POINTER = OUTPUT / "当前候选.json"
PORT = 8811
APP = "scavenger-airbase-g01-movement"
TASK = "G01移动候选服务-8811-V001-01a1ac6a"
MAPPING = {"分区寻路.mjs": "地图适配/分区寻路.mjs", "区域寻路工作器.mjs": "地图适配/区域寻路工作器.mjs"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_identity():
    baseline = read(BASELINE)
    names = list(baseline["来源配置"]["source_files"])
    names += list(MAPPING.values())
    files = {name: sha(SOURCE / name) for name in sorted(names)}
    digest = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return files, digest


def status():
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2) as response:
            return json.load(response)
    except (OSError, URLError):
        return None


def protect(paths):
    command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/维护清理/清理工具.py"), "protect", "--task", TASK]
    for path in paths:
        command.extend(["--path", path.relative_to(ROOT).as_posix()])
    subprocess.run(command, cwd=ROOT, check=True)


def verify(identity, digest):
    if identity.get("app") != APP or identity.get("port") != PORT:
        raise RuntimeError("8811被其他应用占用，保留现场，未接管或结束进程")
    if identity.get("source_sha256") != digest or identity.get("candidate_program_sha256") != sha(Path(__file__)):
        raise RuntimeError("8811正在运行本轮另一修订，须先核对并结束该候选进程再重启；旧8784不受影响")
    if identity.get("map_sha256") != read(BASELINE)["来源配置"]["map_sha256"]:
        raise RuntimeError("候选地图身份不同")
    return identity


def launch():
    baseline = read(BASELINE)
    config = baseline["来源配置"]
    for name, expected in baseline["sha256"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError("受保护旧文件发生变化：" + name)
    if read(ROOT / "local-data/资源库/当前版本.json")["revision"] != 7:
        raise RuntimeError("统一库已变化，须先核对资源身份")
    files, digest = source_identity()
    current = status()
    if current:
        return verify(current, digest)
    revision = "g01-v001-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    directory = ROOT / config["directory"]
    snapshots = directory.parents[1] / "载具候选源码/G01移动指挥V001-01a1ac6a"
    snapshot = snapshots / revision
    protect([SOURCE, OUTPUT, snapshot, directory.parents[1], ROOT / "local-data/资源库", ROOT / "local-data/tools/three", ROOT / "src/地图验证", ROOT / "src/音效索引/音频运行时.mjs", ROOT / "src/资源加载/压缩模型加载.mjs", Path(__file__), ROOT / "scripts/断箭/预览地图.py"])
    snapshot.mkdir(parents=True, exist_ok=False)
    for name, expected in files.items():
        data = (SOURCE / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("冻结期间源码变化，请在完成编辑后重试")
        target = snapshot / Path(name).name
        if target.exists():
            raise RuntimeError("源码冻结发生同名冲突")
        target.write_bytes(data)
    receipt = {"revision": revision, "source_sha256": digest, "files": files, "map": config["directory"], "map_sha256": config["map_sha256"], "manual_acceptance": "待工程师人工测试"}
    save(snapshot / "候选来源.json", receipt)
    log = OUTPUT / ("服务-" + revision + ".log")
    command = [sys.executable, "-X", "utf8", "-B", str(Path(__file__)), "--serve", "--revision", revision, "--snapshot", str(snapshot)]
    with log.open("xb") as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.time() + 180
    event = threading.Event()
    while time.time() < deadline:
        current = status()
        if current:
            identity = verify(current, digest)
            if identity.get("pid") != process.pid:
                raise RuntimeError("候选端口进程身份与本次启动不一致，未操作其他进程")
            save(POINTER, {**identity, "url": f"http://127.0.0.1:{PORT}/", "log": log.relative_to(ROOT).as_posix(), "snapshot": snapshot.relative_to(ROOT).as_posix(), "manual_acceptance": "待工程师人工测试"})
            return identity
        if process.poll() is not None:
            raise RuntimeError("候选未能启动，请查看 " + log.relative_to(ROOT).as_posix())
        event.wait(.2)
    raise RuntimeError("启动超时，保留候选进程与日志，请核对，避免重复启动")


def serve(snapshot, revision):
    snapshot = snapshot.resolve()
    baseline = read(BASELINE)
    directory = ROOT / baseline["来源配置"]["directory"]
    snapshot.relative_to(directory.parents[1] / "载具候选源码/G01移动指挥V001-01a1ac6a")
    receipt = read(snapshot / "候选来源.json")
    if receipt["revision"] != revision:
        raise ValueError("冻结修订不一致")
    for name, expected in receipt["files"].items():
        if sha(snapshot / Path(name).name) != expected:
            raise ValueError("冻结源码变化：" + name)
    spec = importlib.util.spec_from_file_location("g01_map_service", ROOT / "scripts/断箭/预览地图.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.create(directory, PORT, revision, snapshot)
    freeze = directory / ("浏览器源码-" + revision)
    frozen_identity = read(freeze / "服务身份.json")
    for name, original in MAPPING.items():
        target = freeze / name
        target.write_bytes((snapshot / name).read_bytes())
        server.static["/" + name] = target
        if target.stat().st_size > 2048:
            server.encoded["/" + name] = gzip.compress(target.read_bytes(), compresslevel=6, mtime=0)
        else:
            server.encoded.pop("/" + name, None)
        frozen_identity["sources"][name] = {"source": (SOURCE / original).relative_to(ROOT).as_posix(), "source_sha256": sha(target), "http_sha256": sha(target), "frozen_source": (snapshot / name).relative_to(ROOT).as_posix()}
    server.identity.update(app=APP, source_sha256=receipt["source_sha256"], candidate_program_sha256=sha(Path(__file__)), candidate="G01第一步移动指挥闭环", manual_acceptance="待工程师人工测试")
    save(freeze / "服务身份.json", {**frozen_identity, **server.identity})
    print(json.dumps(server.identity, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="启动或核对本轮8811候选，不打开浏览器")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--revision", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.snapshot, args.revision)
    else:
        identity = launch()
        print(json.dumps({"url": f"http://127.0.0.1:{PORT}/", **identity}, ensure_ascii=False), flush=True)
