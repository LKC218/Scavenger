"""提取与Blender共用的标准库进度、原子JSON和输入指纹；不加载资源解析依赖。"""

import hashlib
import json
import os
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]

TASK = "Airbase全图双层入库-V001-01a1a602"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class Journal:
    def __init__(self, directory):
        self.path = directory / "执行状态.json"
        self.started = time.time()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state = {"task": TASK, "pid": os.getpid(), "started_epoch": self.started,
                      "phase": "A0来源审计", "status": "进行中",
                      "gates": {"A0": "进行中", "A1": "未开始", "A2": "未开始", "A3": "未开始", "A4": "未开始"},
                      "human_acceptance": "待人工验收"}
        if self.path.exists():
            previous = json.loads(self.path.read_text("utf-8"))
            self.state["gates"] = previous.get("gates", self.state["gates"])
            atomic_json(directory / "进度历史" / f"{time.time_ns()}.json", previous)
        self.thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.update(operation="初始化")
        self.thread.start()

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            now = time.time()
            self.state.update(updated_epoch=now, elapsed_s=round(now - self.started, 3))
            atomic_json(self.path, self.state)

    def heartbeat(self):
        last = time.time()
        while not self.stop.wait(.25):
            now = time.time()
            if now - last >= 15:
                self.update()
                last = now

    def close(self, **values):
        self.stop.set()
        self.thread.join()
        self.update(**values)


def fingerprint(path, journal=None):
    before = path.stat()
    sha = hashlib.sha256()
    read = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            sha.update(block)
            read += len(block)
            if journal:
                with journal.lock:
                    journal.state["hash_progress"] = {"file": path.name, "read": read, "total": before.st_size}
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"摘要计算期间输入变化：{path.name}")
    return {"bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": sha.hexdigest()}

