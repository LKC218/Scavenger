"""冻结G01标签与景别过渡V003，在独立8813提供人工候选。"""
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
OUTPUT = ROOT / "local-data/战术演训/G01标签与景别过渡/V003-01a1ac6a"
BASELINE = OUTPUT / "调整基线.json"
POINTER = OUTPUT / "当前候选.json"
PORT = 8813
APP = "scavenger-airbase-g01-label-transition"
TASK = "G01标签过渡服务-8813-V003-01a1ac6a"
MAPPING = {"分区寻路.mjs": "地图适配/分区寻路.mjs", "区域寻路工作器.mjs": "地图适配/区域寻路工作器.mjs", "直升机高度.mjs": "直升机高度.mjs", "全图显示.mjs": "地图适配/全图显示.mjs", "景别过渡.mjs": "景别过渡.mjs", "标签布局.mjs": "标签布局.mjs", "镜头过渡.mjs": "镜头过渡.mjs"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_identity():
    baseline = read(BASELINE)
    names = ["index.html", "界面.css"] + [p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*.mjs")]
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
        raise RuntimeError("8813被其他应用占用，保留现场，未接管或结束进程")
    if identity.get("source_sha256") != digest or identity.get("candidate_program_sha256") != sha(Path(__file__)):
        raise RuntimeError("8813正在运行本轮另一修订，须先核对并结束该候选进程再重启；旧8784不受影响")
    if identity.get("map_sha256") != read(BASELINE)["source_config"]["map_sha256"]:
        raise RuntimeError("候选地图身份不同")
    return identity


def launch():
    baseline = read(BASELINE)
    config = baseline["source_config"]
    for name, expected in baseline["protected"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError("受保护旧文件发生变化：" + name)
    if read(ROOT / "local-data/资源库/当前版本.json")["revision"] != 7:
        raise RuntimeError("统一库已变化，须先核对资源身份")
    files, digest = source_identity()
    current = status()
    if current:
        return verify(current, digest)
    revision = "g01-label-v003-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    directory = ROOT / config["directory"]
    snapshots = directory.parents[1] / "载具候选源码/G01标签过渡V003-01a1ac6a"
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
    directory = ROOT / baseline["source_config"]["directory"]
    snapshot.relative_to(directory.parents[1] / "载具候选源码/G01标签过渡V003-01a1ac6a")
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
        if not (snapshot / name).is_file():
            continue
        target = freeze / name
        target.write_bytes((snapshot / name).read_bytes())
        server.static["/" + name] = target
        if target.stat().st_size > 2048:
            server.encoded["/" + name] = gzip.compress(target.read_bytes(), compresslevel=6, mtime=0)
        else:
            server.encoded.pop("/" + name, None)
        frozen_identity["sources"][name] = {"source": (SOURCE / original).relative_to(ROOT).as_posix(), "source_sha256": sha(target), "http_sha256": sha(target), "frozen_source": (snapshot / name).relative_to(ROOT).as_posix()}
    server.identity.update(app=APP, source_sha256=receipt["source_sha256"], candidate_program_sha256=sha(Path(__file__)), candidate="G01标签稳定与景别过渡", manual_acceptance="待工程师人工测试")
    save(freeze / "服务身份.json", {**frozen_identity, **server.identity})
    print(json.dumps(server.identity, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def restore_v002():
    """从V002不可变快照恢复旧对照，不依赖已经回改的工作源码。"""
    baseline = read(BASELINE)
    old_identity = baseline["old_service"]
    old_program = ROOT / "scripts/战术演训/高度镜头候选.py"
    if sha(old_program) != baseline["protected"]["scripts/战术演训/高度镜头候选.py"]:
        raise RuntimeError("V002服务程序身份变化，未启动")

    def old_status():
        try:
            with urlopen("http://127.0.0.1:8812/api/status", timeout=2) as response:
                identity = json.load(response)
        except (OSError, URLError):
            return None
        if identity.get("app") != old_identity["app"] or identity.get("source_sha256") != baseline["old_sha256"]:
            raise RuntimeError("8812不是保留的V002对照，未接管")
        return identity

    current = old_status()
    if current:
        return current
    old_output = ROOT / "local-data/战术演训/G01高度与镜头调整/V002-01a1ac6a"
    pointer = read(old_output / "当前候选.json")
    original = (ROOT / pointer["snapshot"]).resolve()
    allowed = ROOT / baseline["source_config"]["directory"]
    parent = allowed.parents[1] / "载具候选源码/G01高度镜头V002-01a1ac6a"
    original.relative_to(parent)
    receipt = read(original / "候选来源.json")
    if receipt["source_sha256"] != baseline["old_sha256"]:
        raise RuntimeError("旧快照来源不匹配")
    revision = "g01-height-v002-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    snapshot = parent / revision
    command = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/维护清理/清理工具.py"), "protect", "--task", "G01旧高度对照服务-8812-V003-01a1ac6a"]
    for path in [original, snapshot, allowed.parents[1], old_output, old_program, ROOT / "src/地图验证", ROOT / "local-data/资源库", ROOT / "local-data/tools/three"]:
        command.extend(["--path", path.relative_to(ROOT).as_posix()])
    subprocess.run(command, cwd=ROOT, check=True)
    snapshot.mkdir(parents=True, exist_ok=False)
    for name, expected in receipt["files"].items():
        source = original / Path(name).name
        if sha(source) != expected:
            raise RuntimeError("旧快照文件变化：" + name)
        (snapshot / source.name).write_bytes(source.read_bytes())
    save(snapshot / "候选来源.json", {**receipt, "revision": revision})
    log = old_output / ("服务-" + revision + ".log")
    with log.open("xb") as stream:
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-B", str(old_program), "--serve", "--snapshot", str(snapshot), "--revision", revision], cwd=ROOT, stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.time() + 180
    event = threading.Event()
    while time.time() < deadline:
        current = old_status()
        if current:
            if current["pid"] != process.pid:
                raise RuntimeError("恢复端口进程与本轮启动不同，未结束其他进程")
            save(old_output / "当前候选.json", {**current, "url": "http://127.0.0.1:8812/", "snapshot": snapshot.relative_to(ROOT).as_posix(), "log": log.relative_to(ROOT).as_posix(), "manual_acceptance": "旧V002候选保留，未登记整体通过"})
            return current
        if process.poll() is not None:
            raise RuntimeError("旧对照启动失败，查看 " + log.relative_to(ROOT).as_posix())
        event.wait(.2)
    raise RuntimeError("旧对照恢复超时，保留日志和进程待核对")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="启动或核对8813标签过渡候选，不打开浏览器")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--restore-v002", action="store_true", help="核对或从不可变快照恢复旧8812")
    parser.add_argument("--snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--revision", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.restore_v002:
        print(json.dumps({"url": "http://127.0.0.1:8812/", **restore_v002()}, ensure_ascii=False), flush=True)
    elif args.serve:
        serve(args.snapshot, args.revision)
    else:
        print(json.dumps({"url": f"http://127.0.0.1:{PORT}/", **launch()}, ensure_ascii=False), flush=True)
