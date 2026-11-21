"""战术演训的日常验证仅留摘要；失败时在本轮目录留一张图和日志。"""
import json
from pathlib import Path
import traceback


ROUTINE_ROOT = Path(__file__).resolve().parents[2] / "tmp/运行/战术演训日常测试"


def output_directory(path, full_root, routine, *, legacy_routine_roots=()):
    path = path.resolve()
    roots = (ROUTINE_ROOT, *legacy_routine_roots) if routine else (full_root,)
    if not any(path != root.resolve() and path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("请为本次回归指定独立输出子目录")
    path.mkdir(parents=True, exist_ok=False)
    return path


def compact(result):
    keys = ("passed", "channel", "browser", "browser_version", "version", "headless",
            "identity", "test_sha256", "started_epoch", "finished_epoch", "scope",
            "manual_acceptance", "human_acceptance", "failure", "capture_error")
    summary = {key: result[key] for key in keys if key in result}
    for key in ('failure', 'capture_error'):
        if isinstance(summary.get(key), str) and len(summary[key]) > 4000:
            summary[key] = summary[key][:4000] + '（已截断，见失败日志）'
    if "checks" in result:
        summary["checks"] = [{k: row[k] for k in ("name", "passed")} for row in result["checks"]]
    if result.get("boundaries"):
        summary["boundaries"] = [{k: row[k] for k in ("name", "status", "reason")} for row in result["boundaries"]]
    return summary


def write_summary(path, result):
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_failure(directory, result, *, error=None, page=None):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "失败日志.txt"
    if log.exists():
        return
    if error is not None:
        result["failure"] = repr(error)
    if page is not None:
        try:
            if not page.is_closed():
                page.screenshot(path=str(directory / "失败.png"), timeout=5000)
        except Exception as capture_error:
            result["capture_error"] = repr(capture_error)
    details = {k: repr(result[k])[:4000] for k in ("failure", "capture_error", "errors", "console_errors") if result.get(k)}
    details["failed_checks"] = [
        {"name": row["name"], "evidence": repr(row.get("evidence"))[:3000]}
        for row in result.get("checks", []) if not row["passed"]
    ][:20]
    trace = "".join(traceback.format_exception(error))[-12000:] if error is not None else ""
    text = trace + json.dumps(details, ensure_ascii=False, indent=2) + "\n"
    if len(text.encode('utf-8')) > 32768:
        text = text.encode('utf-8')[:32000].decode('utf-8',errors='ignore') + '\n日志已截断；失败检查见摘要。\n'
    log.write_text(text, encoding="utf-8")


def routine_channel(run, channel, directory, args):
    """处理出现在页面创建以前的异常，例如服务身份或浏览器启动失败。"""
    try:
        return run(channel, directory, args)
    except Exception as error:
        record_failure(directory, {}, error=error)
        raise
