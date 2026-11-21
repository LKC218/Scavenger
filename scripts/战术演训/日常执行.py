"""日常测试子进程及其浏览器的限定收尾；不查杀外部服务。"""
from pathlib import Path
import os
import subprocess
import sys
import time


def process_library():
    try:
        import psutil
    except ModuleNotFoundError:
        # 复用项目既有本地工具，不自动安装或修改系统Python。
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'local-data/tools/python-unity/Lib/site-packages'))
        try:
            import psutil
        finally:
            sys.path.pop(0)
    return psutil


class ExecutionStopped(RuntimeError):
    def __init__(self, reason, confirmed):
        super().__init__(reason)
        self.reason = reason
        self.cleanup_confirmed = confirmed


def run_child(command, cwd, *, timeout=900):
    lib = process_library()
    tracked = {}
    uncertain = False
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def remember():
        nonlocal uncertain
        try:
            root = lib.Process(process.pid)
            if tracked and (root.pid, root.create_time()) not in tracked:
                return
            for item in [root, *root.children(recursive=True)]:
                tracked[(item.pid, item.create_time())] = item
        except lib.NoSuchProcess:
            pass
        except lib.AccessDenied:
            uncertain = True

    def remaining():
        nonlocal uncertain
        alive = []
        for (pid, born), item in tracked.items():
            try:
                if item.is_running() and item.create_time() == born and item.status() != lib.STATUS_ZOMBIE:
                    alive.append(item)
            except lib.NoSuchProcess:
                pass
            except lib.AccessDenied:
                uncertain = True
        return alive

    def stop_owned():
        nonlocal uncertain
        remember()
        for item in reversed(remaining()):
            try:
                item.terminate()
            except lib.NoSuchProcess:
                pass
            except lib.AccessDenied:
                uncertain = True
        _, alive = lib.wait_procs(remaining(), timeout=3)
        for item in alive:
            try:
                item.kill()
            except lib.NoSuchProcess:
                pass
            except lib.AccessDenied:
                uncertain = True
        lib.wait_procs(remaining(), timeout=3)
        process.wait(timeout=5)
        return not uncertain and not remaining()

    deadline = time.time() + timeout
    try:
        while True:
            remember()
            if time.time() >= deadline:
                raise ExecutionStopped('执行超时', False)
            try:
                stdout, stderr = process.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except (KeyboardInterrupt, ExecutionStopped) as error:
        try:
            confirmed = stop_owned()
        except (OSError, subprocess.TimeoutExpired, lib.Error):
            confirmed = False
        raise ExecutionStopped('执行中断' if isinstance(error, KeyboardInterrupt) else error.reason, confirmed) from error
    try:
        safe = not remaining() or stop_owned()
    except (OSError, subprocess.TimeoutExpired, lib.Error):
        safe = False
    if not safe or uncertain:
        raise ExecutionStopped('测试进程退出后仍有未确认的子进程；保持保护', False)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
