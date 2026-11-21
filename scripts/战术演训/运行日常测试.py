"""复用已运行的战术演训候选，执行六条日常回归；不启停服务或清理文件。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import sys
import time
from uuid import uuid4

from 日常留存 import ROUTINE_ROOT, record_failure, write_summary
from 日常执行 import ExecutionStopped, run_child
from 日常登记 import Registration, observe


ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
REGISTRY = ROOT / "local-data/维护清理/登记.json"
CASES = {
    "move-v3": ("验证标签版移动交互.py", "交互验证.json", ("msedge", "chrome"), 8813),
    "labels-v3": ("验证标签与过渡.py", "串行专项汇总.json", ("msedge", "chrome", "both"), 8813),
    "move-v2": ("验证镜头版移动交互.py", "交互验证.json", ("msedge", "chrome"), 8812),
    "altitude-v2": ("验证高度与镜头.py", "串行专项汇总.json", ("msedge", "chrome", "both"), 8812),
    "move-v1": ("验证移动交互.py", "交互验证.json", ("msedge", "chrome"), 8811),
    "name-8784": ("../验证战术演训原型命名.py", "验证结果.json", ("both",), 8784),
}
VERSIONS = {
    8813: ("G01标签与景别过渡/V003-01a1ac6a", "G01标签过渡V003-01a1ac6a", "scavenger-airbase-g01-label-transition"),
    8812: ("G01高度与镜头调整/V002-01a1ac6a", "G01高度镜头V002-01a1ac6a", "scavenger-airbase-g01-altitude-camera"),
    8811: ("G01移动指挥闭环/V001-01a1ac6a", "G01移动指挥V001-01a1ac6a", "scavenger-airbase-g01-movement"),
}
PIN = ("app", "port", "map", "revision", "directory", "source_sha256", "map_sha256", "candidate_program_sha256")
SERVICE = (*PIN, "pid", "started_at")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pinned_candidate(case="move-v3"):
    port = CASES[case][3]
    if port == 8784:
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            import 启动战术演训原型 as launcher
        finally:
            sys.path.pop(0)
        config, _, _ = launcher.configuration()
        from 只读核对演训入口 import check_only
        identity = check_only()
        return {key: identity[key] for key in ('app', 'port', 'map', 'revision', 'directory', 'map_sha256', 'transport')}
    version, snapshot_name, app = VERSIONS[port]
    pointer = read(ROOT / 'local-data/战术演训' / version / '当前候选.json')
    if pointer["app"] != app or pointer["port"] != port or pointer["map"] != "airbase-full":
        raise ValueError(f"本地候选记录不是{port}的对应Airbase版本")
    directory = (ROOT / pointer["directory"]).resolve()
    directory.relative_to(ROOT / "local-data/断箭/批次")
    snapshot = (ROOT / pointer["snapshot"]).resolve()
    snapshot.relative_to(directory.parents[1] / "载具候选源码" / snapshot_name)
    receipt = read(snapshot / "候选来源.json")
    if snapshot.name != pointer["revision"] or any(receipt[key] != pointer[key] for key in ("revision", "source_sha256", "map_sha256")) or receipt["map"] != pointer["directory"]:
        raise ValueError("本地候选记录与冻结凭证不一致")
    files = receipt["files"]
    if not files or hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode()).hexdigest() != pointer["source_sha256"] or any(sha(snapshot / Path(name).name) != expected for name, expected in files.items()):
        raise ValueError("冻结源码摘要变化")
    if sha(directory / "地图运行.json") != pointer["map_sha256"]:
        raise ValueError("冻结地图摘要变化")
    return {key: pointer[key] for key in PIN}


def live_service(expected):
    port = expected['port']
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/api/status")
        with conn.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"{port}身份接口返回HTTP {response.status}")
            value = json.loads(response.read())
    finally:
        conn.close()
    if not isinstance(value, dict):
        raise ValueError(f"{port}身份接口未返回对象")
    mismatches = [key for key, wanted in expected.items() if value.get(key) != wanted]
    if mismatches:
        raise RuntimeError(f"{port}服务身份不符：" + "、".join(mismatches))
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0 or not isinstance(value.get("started_at"), (int, float)):
        raise RuntimeError(f"{port}未返回可核对的进程身份")
    return {key: value[key] for key in (*expected, 'pid', 'started_at')}


def output_path(case, run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id) or PureWindowsPath(run_id).is_reserved():
        raise ValueError("运行ID只允许1至64个字母、数字、下划线或连字符，首位为字母或数字")
    case_root = ROUTINE_ROOT / case
    output = case_root / run_id
    cursor = ROOT
    for part in output.relative_to(ROOT).parts:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("输出路径不能经过链接或目录联接")
    return output


def reserve(case, run_id, task_name=None):
    output = output_path(case, run_id)
    case_root = ROUTINE_ROOT / case
    protected = any(
        task["status"] == "active" and any(
            (ROOT / path).is_relative_to(case_root) and output.is_relative_to(ROOT / path)
            for path in task["paths"]
        )
        for name, task in read(REGISTRY)["tasks"].items() if task_name is None or name == task_name
    )
    if not protected:
        raise ValueError(f"须先手工登记本轮输出保护：{output.relative_to(ROOT)}")
    output.mkdir(parents=True, exist_ok=False)
    return output


def dependencies(case):
    """六条既有用例的项目内读依赖；外部Python及浏览器由既有安装提供。"""
    paths = ['scripts/战术演训', 'scripts/维护清理', 'scripts/资源库', 'scripts/断箭/预览地图.py',
             'scripts/验证战术演训原型命名.py', 'scripts/启动战术演训原型.py',
             '打开战术演训原型.cmd', '打开载具靶场.cmd', 'local-data/资源库',
             'local-data/tools/three', 'local-data/tools/python-unity',
             'local-data/原型/战术演训原型',
             'src/地图验证', 'src/资源加载', 'src/音效索引']
    port = CASES[case][3]
    if port in VERSIONS:
        paths.append('local-data/战术演训/' + VERSIONS[port][0])
    return [ROOT / path for path in paths]


def candidate_dependencies(case):
    port = CASES[case][3]
    if port in VERSIONS:
        pointer = read(ROOT / 'local-data/战术演训' / VERSIONS[port][0] / '当前候选.json')
        names = (pointer['directory'], pointer['snapshot'])
    else:
        pointer = read(ROOT / 'local-data/原型/战术演训原型/当前版本.json')
        names = (pointer['directory'], pointer['source_override'])
    paths = [ROOT / name for name in names]
    for path in paths:
        path.relative_to(ROOT / 'local-data/断箭/批次')
    # 该地图批次还包含按冻结路由读取的地形包及网页源码。
    return [paths[0].parents[1], *paths]


def run_case(case, channel, run_id, *, automatic=True, retained=False):
    script_name, report_name, _, port = CASES[case]
    lifecycle = Registration(ROOT, case, channel, run_id, output_path(case, run_id), retained=retained) if automatic else None
    if lifecycle:
        lifecycle.start(dependencies(case))
    try:
        output = reserve(case, run_id, lifecycle.task if lifecycle else None)
    except FileExistsError:
        if lifecycle:
            lifecycle.cancel_empty()
        raise
    child_dir = output / "检查"
    child_report = child_dir / report_name
    command = [sys.executable, "-X", "utf8", "-B", str(TOOLS / script_name), "--routine"]
    if case == 'name-8784':
        command.extend(['--output', str(child_dir)])
    else:
        command.extend(['--channel', channel, '--output-dir', str(child_dir)])
    result = {"run_id": run_id, "case": case, "channel": channel, "mode": "日常回归", "passed": False, "started_epoch": time.time(), "cleanup_confirmed": True}
    if lifecycle:
        result['retention'] = lifecycle.descriptor(retained)
    detail = {}
    completed = None
    reason = "服务预检"
    try:
        if lifecycle:
            scopes = candidate_dependencies(case)
            reason = '依赖保护登记'
            result['cleanup_confirmed'] = False
            lifecycle.extend(scopes)
            result['cleanup_confirmed'] = True
            reason = '服务预检'
        expected = pinned_candidate(case)
        result["identity"] = initial = live_service(expected)
        result["test_sha256"] = sha(TOOLS / script_name)
        reason = "执行检查"
        if case in ("labels-v3", "altitude-v2"):
            command.extend(["--expect-source-sha", expected["source_sha256"]])
        result['cleanup_confirmed'] = False
        completed = run_child(command, cwd=ROOT)
        result['cleanup_confirmed'] = True
        result["exit_code"] = completed.returncode
        if child_report.is_file():
            detail = read(child_report)
        reason = "结束时服务核对"
        if live_service(expected) != initial:
            raise RuntimeError(f"{port}服务在运行期间发生变化")
        reports = detail.get("channels", detail.get('browsers', [detail]))
        for row in reports:
            identity = row.get("identity", detail.get('identity'))
            if ((identity is None and row.get("passed")) or
                    (identity is not None and any(identity.get(key) != value for key, value in initial.items()))):
                raise RuntimeError("检查记录中的服务身份与启动前不一致")
        reason = "检查失败"
        if completed.returncode or detail.get("passed") is not True:
            raise RuntimeError("日常检查未通过，见失败日志")
        channels = ('chrome', 'msedge') if channel == 'both' else (channel,)
        if tuple(row.get('channel', row.get('browser')) for row in reports) != channels or any(row.get('passed') is not True for row in reports):
            raise RuntimeError('日常检查没有交付所选浏览器的完整通过记录')
        if any(path.is_file() and path != child_report for path in output.rglob("*")):
            reason = "留存结果核对"
            raise RuntimeError("通过用例出现摘要之外的文件，已保留现场")
        reason = "通过"
        result["passed"] = True
    except (OSError, HTTPException, ImportError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        if isinstance(error, ExecutionStopped):
            reason = error.reason
            result['cleanup_confirmed'] = error.cleanup_confirmed
        if isinstance(error, KeyboardInterrupt):
            reason = "执行中断"
        if isinstance(error, ImportError):
            # run_child在创建任何子进程前加载进程库，缺失时不存在待收尾浏览器。
            result['cleanup_confirmed'] = True
        result["error"] = str(error) or type(error).__name__
        if not any(output.rglob("失败日志.txt")):
            context = {"errors": [completed.stderr[-8000:]]} if completed and completed.stderr else {}
            record_failure(output, context, error=error)
    result.update(status=reason, result=detail, finished_epoch=time.time())
    files = sorted(path for path in output.rglob("*") if path.is_file() and path != child_report)
    result["artifacts"] = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size} for path in files]
    summary = output / "结果摘要.json"
    if child_report.is_file():
        child_report.rename(summary)
    write_summary(summary, result)
    retention = {'ok': True, 'state': '手工登记模式'}
    if lifecycle:
        try:
            retention = lifecycle.finish(result, retained=retained)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            # 输出可能已经被清理工具冻结，不能再改摘要或补写日志使指纹失效。
            retention = {'ok': False, 'state': '交接失败，保留保护', 'task': lifecycle.task,
                         'error': str(error) or type(error).__name__}
    checks = sum(len(row.get("checks", [])) for row in detail.get("channels", detail.get('browsers', [detail])))
    print(json.dumps({"passed": result["passed"], "status": reason, "checks": checks, "files": 1 + len(files),
                      "bytes": summary.stat().st_size + sum(row["bytes"] for row in result["artifacts"]),
                      "summary": summary.relative_to(ROOT).as_posix(), 'retention': retention}, ensure_ascii=False))
    return 0 if result["passed"] and retention['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="列出六条可用的日常用例")
    mode.add_argument('--observation', action='store_true', help='核对本入口任务、产物及连续3个工作日5次运行的观察进度')
    mode.add_argument("--case", choices=CASES)
    parser.add_argument("--channel", choices=("msedge", "chrome", "both"), help="默认msedge；专项支持both，命名用例固定both")
    parser.add_argument("--run-id", help="指定未使用的运行ID；省略时生成唯一ID并自动登记")
    parser.add_argument('--retain', action='store_true', help='结果供人工复核或持续消费者使用；不登记缓存，不关闭本轮保护')
    parser.add_argument('--manual-registration', action='store_true', help='回退为原有手工protect/enroll/close模式')
    args = parser.parse_args()
    if args.retain and args.manual_registration:
        parser.error('--retain须使用自动登记模式，避免保留意图被手工流程忽略')
    if args.observation:
        if args.channel or args.run_id or args.retain or args.manual_registration:
            parser.error('--observation不接受测试运行参数')
        try:
            print(json.dumps(observe(ROOT), ensure_ascii=False, indent=2))
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            parser.exit(2, f'观察核对失败：{error}\n')
        return 0
    if args.list:
        if args.channel or args.run_id or args.retain or args.manual_registration:
            parser.error("--list 不接受运行参数")
        for key, (script, _, channels, port) in CASES.items():
            print(f"{key}：{Path(script).name}（{port}，{', '.join(channels)}）")
        return 0
    channel = args.channel or ('both' if args.case == 'name-8784' else 'msedge')
    if channel not in CASES[args.case][2]:
        parser.error("该用例不支持所选浏览器通道")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    try:
        return run_case(args.case, channel, run_id, automatic=not args.manual_registration, retained=args.retain)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f"无法开始日常测试：{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
