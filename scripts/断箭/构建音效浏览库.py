"""串行扩充载具相关声音，生成不依赖型号匹配的离线浏览库。"""

import argparse
from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import re
import shutil
import time

from 探测音效 import ROOT, FMOD, digest, local_batch, save
from 检查音效 import inspect
from 提取音效 import render

CATEGORIES = {
    "BA_Aircraft_Turbine": "飞机涡轮", "BA_Helicopter_Rotor": "直升机旋翼",
    "BA_Vehicles_Engine": "载具发动机", "BA_Vehicle_Shooting": "载具射击",
    "BA_Artillery_Shooting": "火炮射击", "BA_Shell_Explosion_Impact": "命中与爆炸",
    "BA_Rockets": "火箭与导弹", "BA_Vehicle_Death": "载具毁伤",
    "BA_MechanicsSounds": "机械操作", "BA_Ammo": "弹药辅助", "SFX_Referenced": "引用辅助声",
}
SOURCE = "BA-26273833-003"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_record(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def check(row):
    path = Path(row["path"])
    if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
        raise ValueError("输入或保护文件变化：" + row["path"])
    return path


def folder(event):
    return event["path"].split("/")[2]


def prepare(batch):
    if batch.exists():
        raise FileExistsError("独立浏览库批次拒绝覆盖")
    config_path = ROOT / "local-data/断箭/本地配置.json"
    config = read(config_path)
    build = re.search(r'"buildid"\s+"(\d+)"', Path(config["steam_manifest"]).read_text()).group(1)
    if build != "26273833" or not batch.name.startswith("BA-" + build + "-"):
        raise ValueError("构建与批准范围不符")
    if shutil.disk_usage(ROOT).free < 2 * 1024**3:
        raise ValueError("可用空间不足2GiB")
    baseline = local_batch("BA-26273833-006")
    index_path = baseline / "证据/全bank完整事件索引.json"
    expected_index = next(row for row in read(baseline / "输出核验.json")["files"] if row["path"] == "证据/全bank完整事件索引.json")
    if digest(index_path) != expected_index["sha256"]:
        raise ValueError("完整事件索引变化")
    formal = local_batch(SOURCE) / "音效配套库"
    mapping = read(formal / "型号音效映射.json")
    old = {audio["render"]["event"]["guid"]: audio for audio in mapping["audio"].values()}
    all_events = defaultdict(list)
    for event in read(index_path)["events"]:
        if folder(event) in CATEGORIES or event["guid"] in old:
            all_events[event["guid"]].append(event)
    tasks = []
    for guid, members in all_events.items():
        event = dict(members[0])
        banks = sorted({Path(member["bank"]).name for member in members})
        if guid in old:
            event = dict(old[guid]["render"]["event"])
        event["bank"] = Path(event["bank"]).name
        category = CATEGORIES.get(folder(event), "已确证共享武器")
        tasks.append({"guid": guid, "event": event, "banks": banks, "category": category,
                      "operation": "reused" if guid in old else "rendered",
                      "source_audio": file_record(formal / old[guid]["path"]) if guid in old else None,
                      "previous_audio": old.get(guid)})
    rank = {name: index for index, name in enumerate([*CATEGORIES.values(), "已确证共享武器"])}
    tasks.sort(key=lambda row: (rank[row["category"]], row["event"]["path"]))
    if len(tasks) != 250 or Counter(task["operation"] for task in tasks) != Counter(reused=101, rendered=149):
        raise ValueError("任务范围与批准的250/101/149不一致")
    protected = []
    for row in read(formal / "输出核验.json")["files"]:
        path = (formal / row["path"]).resolve()
        if not path.is_relative_to(formal.resolve()):
            raise ValueError("正式库路径越界")
        check({**row, "path": str(path)})
        protected.append(file_record(path))
    protected.append(file_record(formal / "输出核验.json"))
    previous_inputs = {str(Path(row["path"]).resolve()).casefold(): row for row in read(baseline / "输入清单.json")["files"]}
    game = Path(config["game_directory"])
    banks = {"Master.bank", "Master.strings.bank"} | {task["event"]["bank"] for task in tasks}
    paths = [game / "BrokenArrow_Data/StreamingAssets" / name for name in sorted(banks)]
    settings = local_batch("BA-26273833-002") / "证据/音频设置原始对象.json"
    paths += [settings, config_path, Path(config["steam_manifest"]), index_path,
              formal / "型号音效映射.json", formal / "输出核验.json",
              ROOT / "local-data/断箭/正式音效库.json",
              ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll",
              ROOT / "local-data/tools/FMOD/2.01.11/resonanceaudio.dll",
              Path(__file__), Path(__file__).with_name("音效浏览模板.html"),
              Path(__file__).with_name("提取音效.py"), Path(__file__).with_name("检查音效.py"),
              Path(__file__).with_name("探测音效.py")]
    inputs = []
    for path in paths:
        row = file_record(path)
        expected = previous_inputs.get(str(path.resolve()).casefold())
        if expected and expected["sha256"] != row["sha256"]:
            raise ValueError("相对006基线输入已变化：" + str(path))
        inputs.append(row)
    runtime = FMOD(ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll")
    try:
        if int(runtime.version, 16) != 0x20111:
            raise ValueError("FMOD实际版本不符")
    finally:
        runtime.close()
    batch.mkdir()
    save(batch / "输入清单.json", {"batch": batch.name, "build": build, "unity_version": "6000.3.24f1",
         "unity_header_basis": "与006已核验源文件摘要一致", "fmod_version": "2.01.11", "created_at_epoch": time.time(), "files": inputs})
    save(batch / "证据/保护清单.json", {"files": protected})
    save(batch / "事件任务清单.json", {"scope": "全部阵营及DLC载具相关事件；型号匹配后置", "tasks": tasks})
    shutil.copyfile(settings, batch / "证据/音频设置原始对象.json")
    save(batch / "准备核验.json", {"status": "passed", "events": len(tasks), "operations": dict(Counter(task["operation"] for task in tasks)),
         "input_files": len(inputs), "protected_files": len(protected), "queue_sha256": digest(batch / "事件任务清单.json")})
    print(json.dumps(read(batch / "准备核验.json"), ensure_ascii=False), flush=True)


def queue(batch):
    if digest(batch / "事件任务清单.json") != read(batch / "准备核验.json")["queue_sha256"]:
        raise ValueError("任务清单摘要变化")
    return read(batch / "事件任务清单.json")["tasks"]


def run(batch):
    tasks = queue(batch)
    if (batch / "执行锁.json").exists():
        raise ValueError("执行锁已存在，请先核对已有进程和逐项回执，禁止重复启动")
    with (batch / "执行锁.json").open("x", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "status": "running", "started_at_epoch": time.time()}, stream)
    for row in read(batch / "输入清单.json")["files"]:
        check(row)
    started = time.time()
    results = []
    for index, task in enumerate(tasks):
        item = {"guid": task["guid"], "path": task["event"]["path"], "name": task["event"]["path"].rsplit("/", 1)[-1],
                "category": task["category"], "banks": task["banks"], "operation": task["operation"], "status": "failed"}
        try:
            if task["operation"] == "reused":
                path = check(task["source_audio"])
                details = inspect(path)
                rendering = task["previous_audio"]["render"]
                origin = {"batch": SOURCE, "kind": "event_render", "operation": "核验后复用"}
            else:
                attempt = f"{index + 1:03d}-{task['guid'][:8]}"
                rendering = render(batch, task["event"], attempt, 8)
                if rendering["render_status"] != "passed":
                    raise ValueError(rendering.get("error", "渲染失败"))
                path = batch / rendering["output"]
                details = inspect(path)
                save(path.parent / "技术检查.json", details)
                origin = {"batch": batch.name, "attempt": attempt, "kind": "event_render", "operation": "本轮提取"}
            status = "ready" if details["decode_status"] == "passed" and details["clip_samples"] == 0 else "review"
            issue = "" if status == "ready" else "静音或削波，需核查参数与实际听审"
            profile = {key: rendering.get(key) for key in ("sample_rate", "channels", "dsp_frames", "duration_request_seconds", "duration_effective_seconds", "stop", "tail_seconds", "position", "parameters")}
            item.update(status=status, issue=issue, check=details, source=origin, render_profile=profile, private_audio_source=str(path.resolve()))
        except Exception as error:
            item["issue"] = str(error)
        results.append(item)
        save(batch / "提取回执.json", results)
        save(batch / "进度.json", {"status": "running", "finished": len(results), "total": len(tasks), "states": dict(Counter(row["status"] for row in results)), "elapsed_seconds": time.time() - started})
        if len(results) % 10 == 0 or item["status"] != "ready" or len(results) == len(tasks):
            print(json.dumps({"finished": len(results), "total": len(tasks), "states": dict(Counter(row["status"] for row in results)), "current": item["name"], "issue": item.get("issue", "")}, ensure_ascii=False), flush=True)
    save(batch / "执行锁.json", {"pid": os.getpid(), "status": "complete", "completed_at_epoch": time.time()})
    save(batch / "进度.json", {"status": "complete", "finished": len(results), "total": len(tasks), "states": dict(Counter(row["status"] for row in results)), "elapsed_seconds": time.time() - started})


def build(batch):
    tasks, results = queue(batch), read(batch / "提取回执.json")
    if len(results) != len(tasks) or {row["guid"] for row in results} != {task["guid"] for task in tasks}:
        raise ValueError("提取回执集合不完整")
    target = batch / "音效浏览库"
    target.mkdir(exist_ok=False)
    stored, events = {}, []
    for result in results:
        row = {key: value for key, value in result.items() if not key.startswith("private_")}
        row["audio_path"] = ""
        if result.get("private_audio_source"):
            source = Path(result["private_audio_source"])
            sha = result["check"]["sha256"]
            if digest(source) != sha:
                raise ValueError("声音来源摘要变化")
            if sha not in stored:
                path = target / "音频" / result["category"] / (sha + ".wav")
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)
                if digest(path) != sha:
                    raise ValueError("声音复制摘要不符")
                stored[sha] = path.relative_to(target).as_posix()
            row["audio_path"] = stored[sha]
        events.append(row)
    summary = {"events": len(events), "ready": sum(row["status"] == "ready" for row in events),
               "review": sum(row["status"] == "review" for row in events), "failed": sum(row["status"] == "failed" for row in events),
               "reused": sum(row["operation"] == "reused" for row in events),
               "new_ready": sum(row["operation"] == "rendered" and row["status"] == "ready" for row in events),
               "new_attempted": sum(row["operation"] == "rendered" for row in events), "unique_wav": len(stored),
               "wav_bytes": sum((target / path).stat().st_size for path in stored.values())}
    catalog = {"schema_version": 1, "batch": batch.name, "build": read(batch / "输入清单.json")["build"], "status": "candidate",
               "scope": "全部阵营及DLC载具相关音效素材，不执行型号匹配", "categories": list(dict.fromkeys(row["category"] for row in events)),
               "summary": summary, "events": events}
    save(target / "音效目录.json", catalog)
    with (target / "音效清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["用途", "事件名", "事件路径", "GUID", "来源bank", "操作", "状态", "问题", "WAV路径", "SHA256", "时长秒", "渲染参数", "听审", "循环"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in events:
            writer.writerow(dict(zip(fields, [row["category"], row["name"], row["path"], row["guid"], "|".join(row["banks"]), row["operation"], row["status"], row.get("issue", ""), row["audio_path"], row.get("check", {}).get("sha256", ""), row.get("check", {}).get("duration_seconds", ""), json.dumps(row.get("render_profile", {}), ensure_ascii=False), "逐条待听审", "有限渲染循环点未知"])))
    template = Path(__file__).with_name("音效浏览模板.html").read_text(encoding="utf-8")
    payload = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    (target / "音效浏览库.html").write_text(template.replace("__CATALOG__", payload), encoding="utf-8")
    text = "# 全部阵营载具音效浏览候选库\n\n双击音效浏览库.html，按用途分类或事件名、路径、bank、GUID搜索，再点击原生播放器试听。音效清单.csv为独立声音索引；本库不执行型号匹配，也不要求模型文件。\n\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n\n现有101份核验后复用，新事件均为默认参数、固定距离和有限时长event_render，不是bank原始raw_sample，不覆盖全部参数/RPM/随机层。待复核与失败项保留问题，循环与逐条听审未自动通过。7条旧回退路径和3个无引用型号不因本库扩充恢复；003正式V1及其他旧批次保持原样。无自动播放，默认音量35%，主动循环只重复片段。来源设置及密钥不进入本目录。\n"
    (target / "README.md").write_text(text, encoding="utf-8")
    save(batch / "浏览库汇总.json", summary)
    save(target / "输出核验.json", {"files": [{"path": path.relative_to(target).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(target.rglob("*")) if path.is_file()]})
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def verify(batch):
    for row in read(batch / "输入清单.json")["files"]:
        check(row)
    protected = read(batch / "证据/保护清单.json")["files"]
    for row in protected:
        check(row)
    library = batch / "音效浏览库"
    outputs = read(library / "输出核验.json")["files"]
    for row in outputs:
        path = (library / row["path"]).resolve()
        if not path.is_relative_to(library.resolve()):
            raise ValueError("输出越界")
        check({**row, "path": str(path)})
    catalog = read(library / "音效目录.json")
    tasks = queue(batch)
    if len(catalog["events"]) != 250 or {row["guid"] for row in catalog["events"]} != {row["guid"] for row in tasks}:
        raise ValueError("浏览事件集合不符")
    for row in catalog["events"]:
        if row["audio_path"]:
            details = inspect(library / row["audio_path"])
            if details["sha256"] != row["check"]["sha256"]:
                raise ValueError("媒体摘要变化")
            if row["status"] == "ready" and (details["decode_status"] != "passed" or details["clip_samples"]):
                raise ValueError("技术通过状态不实")
    with (library / "音效清单.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != 250 or {row["GUID"] for row in csv_rows} != {row["guid"] for row in tasks}:
        raise ValueError("CSV与事件清单不符")
    result = {"status": "passed", "source_unchanged": True, "protected_files": len(protected), "output_files": len(outputs),
              "summary": catalog["summary"], "browser_status": "not_executed", "listening_status": "pending", "model_matching": "deferred"}
    save(batch / "交付核验.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "build", "verify"))
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "build": build, "verify": verify}[args.action](local_batch(args.batch))
