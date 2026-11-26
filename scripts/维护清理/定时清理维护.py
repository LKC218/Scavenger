"""Scavenger 系统级定时清理与防堆积体检维护入口（仅使用标准库）。"""

import argparse
import datetime
import json
import locale
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CLEANER = Path(__file__).resolve().with_name("清理工具.py")
AUTO_DIR = ROOT / "local-data/维护清理/自动维护"
TICK_RESULT_PATH = AUTO_DIR / "最近执行结果.json"
AUDIT_RESULT_PATH = AUTO_DIR / "最近体检结果.json"
LOG_PATH = AUTO_DIR / "定时维护.log"

DEFAULT_TASK_NAME = "Scavenger-定时清理维护"
DEFAULT_TIME = "04:00"
MAX_LOG_BYTES = 512 * 1024
KEEP_LOG_LINES = 200
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$")


def _sys_encoding():
    return locale.getpreferredencoding(False) or "utf-8"


def _run_cleaner(subcommand, extra_args=None):
    cmd = [sys.executable, "-X", "utf8", "-B", str(CLEANER), subcommand]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _append_log(line):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    lines.append(line)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
        lines = lines[-KEEP_LOG_LINES:]
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt_gib(num_bytes):
    return f"{num_bytes / (2**30):.2f} GiB"


def run_once(stale_days=7, orphan_mb=50, big_json_mb=50):
    """串联执行 tick（自动清理）与 audit（只读防堆积体检）。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tick_res = _run_cleaner("tick")
    if tick_res.returncode != 0:
        err = (tick_res.stderr or tick_res.stdout).strip()
        _append_log(f"[{ts}] TICK_FAILED rc={tick_res.returncode} err={err}")
        print(f"定时维护中断：tick 执行失败 -> {err}", file=sys.stderr)
        return tick_res.returncode

    audit_args = [
        "--stale-days", str(stale_days),
        "--orphan-mb", str(orphan_mb),
        "--big-json-mb", str(big_json_mb),
    ]
    audit_res = _run_cleaner("audit", audit_args)
    if audit_res.returncode != 0:
        err = (audit_res.stderr or audit_res.stdout).strip()
        _append_log(f"[{ts}] AUDIT_FAILED rc={audit_res.returncode} err={err}")
        print(f"定时维护中断：audit 执行失败 -> {err}", file=sys.stderr)
        return audit_res.returncode

    tick_info = {}
    if TICK_RESULT_PATH.exists():
        tick_info = json.loads(TICK_RESULT_PATH.read_text(encoding="utf-8"))
    audit_info = json.loads(audit_res.stdout)

    moved = tick_info.get("moved_files", 0)
    purged = tick_info.get("purged_files", 0)
    summary = audit_info.get("summary", {})
    warnings = audit_info.get("warnings", [])

    log_line = (
        f"[{ts}] OK moved={moved} purged={purged} "
        f"project={_fmt_gib(summary.get('project_bytes', 0))} "
        f"tmp={_fmt_gib(summary.get('tmp_bytes', 0))} "
        f"warnings={len(warnings)}"
    )
    _append_log(log_line)

    print(f"定时维护完成：{ts}")
    print(f"  自动清理：回收 {moved} 个文件，永久清除 {purged} 个文件")
    print(
        f"  空间体检：项目总计 {_fmt_gib(summary.get('project_bytes', 0))}（{summary.get('project_files', 0)} 文件），"
        f"local-data {_fmt_gib(summary.get('local_data_bytes', 0))}，tmp {_fmt_gib(summary.get('tmp_bytes', 0))}"
    )
    if warnings:
        print(f"  堆积预警（{len(warnings)} 项，只读提醒）：")
        for msg in warnings:
            print(f"    - {msg}")
    else:
        print("  堆积预警：无新增异常")
    return 0


def _validate_schedule_args(task_name, run_time=None):
    if not TASK_NAME_RE.fullmatch(task_name):
        raise ValueError(f"计划任务名称不合法：{task_name}")
    if run_time is not None and not TIME_RE.fullmatch(run_time):
        raise ValueError(f"时间格式须为 HH:MM（24小时制）：{run_time}")


def _run_schtasks(args):
    return subprocess.run(
        ["schtasks", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding=_sys_encoding(),
        errors="replace",
    )


def install_schedule(task_name, run_time):
    _validate_schedule_args(task_name, run_time)
    tr_cmd = subprocess.list2cmdline(
        [sys.executable, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--run-now"]
    )
    res = _run_schtasks(["/Create", "/F", "/SC", "DAILY", "/ST", run_time, "/TN", task_name, "/TR", tr_cmd])
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()
        print(f"注册 Windows 计划任务失败：{detail}", file=sys.stderr)
        print(f"手动执行参考：schtasks /Create /F /SC DAILY /ST {run_time} /TN {task_name} /TR \"{tr_cmd}\"", file=sys.stderr)
        return res.returncode
    print(f"已注册每日 {run_time} 定时清理任务：{task_name}")
    print(f"执行命令：{tr_cmd}")
    return 0


def uninstall_schedule(task_name):
    _validate_schedule_args(task_name)
    res = _run_schtasks(["/Delete", "/F", "/TN", task_name])
    if res.returncode != 0:
        print(f"卸载计划任务失败：{(res.stderr or res.stdout).strip()}", file=sys.stderr)
        return res.returncode
    print(f"已卸载计划任务：{task_name}")
    return 0


def query_status(task_name):
    _validate_schedule_args(task_name)
    res = _run_schtasks(["/Query", "/TN", task_name, "/FO", "LIST"])
    if res.returncode == 0:
        print(f"计划任务已注册：{task_name}")
        for line in res.stdout.splitlines():
            stripped = line.strip()
            if any(k in stripped for k in ("任务名", "TaskName", "下次运行时间", "Next Run Time", "状态", "Status", "上次运行时间", "Last Run Time", "上次结果", "Last Result")):
                print("  " + stripped)
    else:
        print(f"计划任务未注册或不可读：{task_name}（{(res.stderr or res.stdout).strip()}）")

    for label, p in [("最近执行结果", TICK_RESULT_PATH), ("最近体检结果", AUDIT_RESULT_PATH)]:
        if p.exists():
            mt = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            data = json.loads(p.read_text(encoding="utf-8"))
            if label == "最近执行结果":
                print(f"  {label}（{mt}）：moved={data.get('moved_files', 0)}, purged={data.get('purged_files', 0)}, skipped={len(data.get('skipped', []))}")
            else:
                s = data.get("summary", {})
                print(f"  {label}（{mt}）：project={_fmt_gib(s.get('project_bytes', 0))}, warnings={len(data.get('warnings', []))}")
        else:
            print(f"  {label}：尚未生成")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-now", action="store_true", help="立即执行一次 tick + audit（默认动作）")
    group.add_argument("--install", action="store_true", help="注册每日 Windows 计划任务")
    group.add_argument("--status", action="store_true", help="查看计划任务状态与最近清理/体检摘要")
    group.add_argument("--uninstall", action="store_true", help="卸载 Windows 计划任务")
    parser.add_argument("--time", default=DEFAULT_TIME, help="每日执行时间 HH:MM（默认 04:00）")
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="Windows 计划任务名称")
    parser.add_argument("--stale-days", type=int, default=7)
    parser.add_argument("--orphan-mb", type=int, default=50)
    parser.add_argument("--big-json-mb", type=int, default=50)
    args = parser.parse_args()

    if args.install:
        return install_schedule(args.task_name, args.time)
    if args.uninstall:
        return uninstall_schedule(args.task_name)
    if args.status:
        return query_status(args.task_name)
    return run_once(args.stale_days, args.orphan_mb, args.big_json_mb)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(1)
