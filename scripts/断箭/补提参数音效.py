"""仅对已有静音事件的真实可写参数做一次有界补提，保留首次回执。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from 探测音效 import digest, local_batch, save
from 检查音效 import inspect
from 提取音效 import render


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(batch):
    original = batch / "提取回执.json"
    backup = batch / "首次提取回执.json"
    if backup.exists():
        raise FileExistsError("参数补提已执行，禁止重复覆盖")
    if read(batch / "执行锁.json")["status"] != "complete":
        raise ValueError("主提取尚未完成")
    shutil.copyfile(original, backup)
    results = read(original)
    tasks = {row["guid"]: row for row in read(batch / "事件任务清单.json")["tasks"]}
    inputs = {"tool": str(Path(__file__).resolve()), "tool_sha256": digest(Path(__file__)),
              "queue_sha256": digest(batch / "事件任务清单.json"), "first_results_sha256": digest(backup)}
    save(batch / "证据/参数补提输入.json", inputs)
    receipts = []
    for row in results:
        if row["status"] != "review" or row["check"]["peak"] != 0:
            continue
        source_event = tasks[row["guid"]]["event"]
        parameters = [param for param in source_event["parameters"]
                      if param["name"] in ("Hydraulic parameter", "RPM") and param["type"] == 0 and param["flags"] == 0
                      and param["maximum"] > param["minimum"] and param["default"] == 0]
        if not parameters:
            receipts.append({"guid": row["guid"], "event_path": row["path"], "action": "not_retried", "reason": "没有已枚举的可写激活参数，不盲目重渲染"})
            continue
        event = deepcopy(source_event)
        overrides = {}
        for target in event["parameters"]:
            if target in parameters:
                value = (target["minimum"] + target["maximum"]) / 2
                target["default"] = value
                overrides[target["name"]] = value
        attempt = "参数复核-" + row["guid"][:8]
        rendering = render(batch, event, attempt, 8)
        # 现有渲染器从传入描述读取参数值；还原源元数据，另记请求覆盖值，避免将覆盖冒充事件默认值。
        rendering["event"] = deepcopy(source_event)
        rendering["requested_parameter_overrides"] = overrides
        rendering["parameter_basis"] = "完整事件索引给出的真实可写范围中点；用于激活试听，不声称原游戏运行值"
        save(batch / "尝试" / attempt / "渲染记录.json", rendering)
        receipt = {"guid": row["guid"], "event_path": row["path"], "attempt": attempt,
                   "overrides": overrides, "render_status": rendering["render_status"], "first_attempt_preserved": True}
        if rendering["render_status"] == "passed":
            path = batch / rendering["output"]
            details = inspect(path)
            save(path.parent / "技术检查.json", details)
            receipt["check"] = details
            if details["decode_status"] == "passed" and details["clip_samples"] == 0:
                row.update(status="ready", issue="", check=details, private_audio_source=str(path.resolve()),
                           source={"batch": batch.name, "attempt": attempt, "kind": "event_render", "operation": "真实参数范围内激活补提"},
                           render_profile={key: rendering.get(key) for key in ("sample_rate", "channels", "dsp_frames", "duration_request_seconds", "duration_effective_seconds", "stop", "tail_seconds", "position", "parameters", "requested_parameter_overrides", "parameter_basis")})
            else:
                row["issue"] = "默认参数静音；有界激活补提后仍未通过，保留待复核"
        else:
            row["issue"] = "默认参数静音；激活补提失败，保留首次WAV与错误"
            receipt["error"] = rendering.get("error")
        receipts.append(receipt)
        save(batch / "参数补提回执.json", receipts)
        print(json.dumps({"event": row["name"], "overrides": overrides, "status": row["status"]}, ensure_ascii=False), flush=True)
    save(batch / "参数补提回执.json", receipts)
    save(original, results)
    save(batch / "进度.json", {"status": "complete", "finished": len(results), "total": len(results),
         "ready": sum(row["status"] == "ready" for row in results), "review": sum(row["status"] == "review" for row in results),
         "parameter_retry_events": sum(row["action"] != "not_retried" if "action" in row else True for row in receipts)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    main(local_batch(parser.parse_args().batch))
