"""冻结分工、复核现有事件并生成按用途分类的WAV与型号清单；不制作运行资源包。"""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from 探测音效 import ROOT, FMOD, digest, local_batch, resolve_input, save
from 检查音效 import inspect

PARTS = {"air": "空中", "ground": "地面", "shared": "缺项"}
ROLES = {"MoveEvent": "运动与发动机", "Shot": "射击与发射", "Impact": "命中与爆炸", "MissileInterception": "拦截爆炸", "DeathEvent": "载具毁伤"}
SOURCE = "BA-26273833-003"
RAW = "BA-26273833-002"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def token(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def record(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def check(row):
    path = Path(row["path"])
    if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
        raise ValueError("输入文件与冻结清单不符：" + str(path))
    return path


def queue(batch):
    info = read(batch / "任务发布.json")
    if digest(batch / "事件任务清单.json") != info["queue_sha256"]:
        raise ValueError("任务清单摘要变化")
    return read(batch / "事件任务清单.json")


def mapping(batch):
    manifest = read(batch / "输入清单.json")
    path = batch / "证据/正式映射快照.json"
    if digest(path) != manifest["mapping_sha256"]:
        raise ValueError("映射快照变化")
    return read(path)


def scope_name(categories):
    return "空地共用" if len(categories) > 1 else "空中专用" if categories == {"air"} else "地面专用"


def role_name(roles):
    roles = set(roles)
    if roles == {"Impact", "MissileInterception"}:
        return "命中与拦截共用"
    if len(roles) == 1:
        return ROLES.get(next(iter(roles)), "其他确证用途")
    return "多用途共享"


def prepare(batch):
    if batch.exists():
        raise FileExistsError("新分类批次拒绝覆盖")
    raw, source, static = local_batch(RAW), local_batch(SOURCE), local_batch("BA-26273833-001")
    config_path = ROOT / "local-data/断箭/本地配置.json"
    config = read(config_path)
    build = re.search(r'"buildid"\s+"(\d+)"', Path(config["steam_manifest"]).read_text()).group(1)
    if build != "26273833" or not batch.name.startswith("BA-" + build + "-"):
        raise ValueError("游戏构建与本轮批准基线不符")
    if shutil.disk_usage(ROOT).free < 1024**3:
        raise ValueError("可用空间不足本轮预留量")
    # 仅在与已验证输入摘要一致时复用包头和原生布局结论。
    inputs = {}
    for previous in [read(raw / "输入清单.json")["files"], read(source / "输入清单.json")["files"], read(raw / "证据/事件路径原生复核.json")["files"]]:
        for row in previous:
            path = resolve_input(source, row["path"])
            actual = record(path)
            if actual["sha256"] != row["sha256"]:
                raise ValueError("旧证据输入已变更：" + row["path"])
            inputs[str(path.resolve())] = actual
    library = source / "音效配套库"
    formal = read(ROOT / "local-data/断箭/正式音效库.json")
    if formal["batch"] != SOURCE or formal["output_manifest_sha256"] != digest(library / "输出核验.json"):
        raise ValueError("正式库指针变化")
    original = read(library / "型号音效映射.json")
    protected = []
    for row in read(library / "输出核验.json")["files"]:
        path = (library / row["path"]).resolve()
        if not path.is_relative_to(library.resolve()):
            raise ValueError("正式库路径越界")
        actual = record(path)
        if any(actual[key] != row[key] for key in ("bytes", "sha256")):
            raise ValueError("正式库与交付摘要不符")
        protected.append(actual)
    model_library = static / "美军模型库"
    model_manifest = read(model_library / "模型清单.json")
    for model in model_manifest["entries"]:
        for row in model["files"].values():
            path = (model_library / row["path"]).resolve()
            if not path.is_relative_to(model_library.resolve()):
                raise ValueError("模型路径越界")
            actual = record(path)
            if any(actual[key] != row[key] for key in ("bytes", "sha256")):
                raise ValueError("原模型载荷与清单不符")
            protected.append(actual)
    extra = [config_path, library / "输出核验.json", ROOT / "local-data/断箭/正式音效库.json",
             raw / "证据/音频设置原始对象.json", raw / "证据/兼容性与事件索引.json",
             raw / "证据/音效目录位置.json", raw / "证据/SFX预设与集合.json", raw / "证据/事件路径原生复核.json",
             source / "命中音效复核.json", source / "证据/音效调用反汇编-含展开链.json",
             ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll", ROOT / "local-data/tools/FMOD/2.01.11/resonanceaudio.dll",
             Path(__file__).resolve(), Path(__file__).with_name("提取音效.py"), Path(__file__).with_name("检查音效.py"), Path(__file__).with_name("映射音效.py")]
    for path in extra:
        inputs[str(path.resolve())] = record(path)
    runtime = FMOD(ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll")
    try:
        version = runtime.version
    finally:
        runtime.close()
    if int(version, 16) != 0x20111:
        raise ValueError("FMOD实际版本与基线不符")
    batch.mkdir()
    save(batch / "输入清单.json", {"batch": batch.name, "build": build, "source_audio_batch": SOURCE, "raw_batch": RAW,
        "unity_version": "6000.3.24f1", "unity_header_basis": "与已验证源文件逐文件SHA-256一致", "fmod_version": version,
        "mapping_sha256": digest(library / "型号音效映射.json"), "created_at_epoch": time.time(), "files": list(inputs.values())})
    save(batch / "证据/保留载荷清单.json", {"files": protected})
    shutil.copyfile(library / "型号音效映射.json", batch / "证据/正式映射快照.json")
    users = defaultdict(list)
    for model in original["models"]:
        for index, ref in enumerate(model["references"]):
            if ref.get("audio_path"):
                users[ref["event_path"]].append({"model_id": model["model_id"], "category": model["category"], "reference_index": index, "role_field": ref["role_field"]})
    jobs = []
    for event_path, audio in original["audio"].items():
        categories = {row["category"] for row in users[event_path]}
        owner = "shared" if len(categories) > 1 else next(iter(categories))
        profile = {key: audio["render"].get(key) for key in ("parameters", "position", "duration_request_seconds", "stop", "tail_seconds", "sample_rate", "channels", "dsp_frames")}
        jobs.append({"job_id": audio["render"]["event"]["guid"] + ":" + token(profile), "event_path": event_path,
                     "event_guid": audio["render"]["event"]["guid"], "owner": owner, "scope": scope_name(categories),
                     "role": role_name({row["role_field"] for row in users[event_path]}), "roles": sorted({row["role_field"] for row in users[event_path]}),
                     "source": record(library / audio["path"]), "render_profile": profile, "source_check": audio["check"], "users": users[event_path], "operation": "verify_and_reuse"})
    assert len(jobs) == 101 and len({job["job_id"] for job in jobs}) == 101
    save(batch / "事件任务清单.json", {"version": 1, "jobs": jobs, "model_ids": {part: [model["model_id"] for model in original["models"] if model["category"] == part] for part in ("air", "ground")},
         "unresolved_models": [model["model_id"] for model in original["models"] if not model["references"]], "missing_paths": original["summary"]["missing_event_paths"]})
    save(batch / "准备核验.json", {"status": "passed", "input_files": len(inputs), "protected_files": len(protected), "model_files": 1290,
         "jobs": len(jobs), "ownership": dict(Counter(job["owner"] for job in jobs)), "free_bytes": shutil.disk_usage(ROOT).free})
    print(json.dumps(read(batch / "准备核验.json"), ensure_ascii=False, indent=2), flush=True)


def probe(batch):
    if (batch / "任务发布.json").exists():
        raise ValueError("任务已经发布，不能重新写首测")
    original = mapping(batch)
    audio = next(audio for path, audio in original["audio"].items() if any(model["model_id"] == "US_A10" and any(ref.get("event_path") == path and ref.get("role_field") == "MoveEvent" for ref in model["references"]) for model in original["models"]))
    from 提取音效 import render
    sandbox = batch / "证据/受控提取"
    settings = sandbox / "证据/音频设置原始对象.json"
    settings.parent.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(local_batch(RAW) / "证据/音频设置原始对象.json", settings)
    details = render(sandbox, audio["render"]["event"], "A10运动事件首测", 8)
    if details["render_status"] != "passed":
        raise ValueError("受控提取失败：" + details.get("error", "未知错误"))
    technical = inspect(sandbox / details["output"])
    if technical["decode_status"] != "passed" or technical["clip_samples"]:
        raise ValueError("受控提取技术检查不通过")
    save(batch / "证据/受控提取关口.json", {"status": "passed", "event_path": audio["render"]["event"]["path"], "new_unique_events": 0,
         "rerendered_verification_events": 1, "classification_policy": "分类库复用原通过载荷，首测仅作当前运行库验证", "check": technical,
         "source_pcm_file_sha256": audio["check"]["sha256"], "same_wave_hash": technical["sha256"] == audio["check"]["sha256"]})
    save(batch / "任务发布.json", {"status": "ready", "version": 1, "queue_sha256": digest(batch / "事件任务清单.json"), "gate_sha256": digest(batch / "证据/受控提取关口.json"), "published_at_epoch": time.time()})
    for owner, name in PARTS.items():
        path = batch / "分工" / name / "任务说明.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# {name}音频任务\n\n仅写本目录及其子目录。执行主脚本work子命令：`--batch {batch.name} --owner {owner}`。不得修改公共源码、任务清单、其他分片、原库或游戏目录。完成后留下分片核验和人工复核说明；新事件仅提交候选证据，等待主对话分配。\n", encoding="utf-8")
    print("受控提取技术关口通过，三个分片任务已发布", flush=True)


def work(batch, owner):
    jobs = queue(batch)
    original = mapping(batch)
    output = batch / "分工" / PARTS[owner]
    lock = output / "执行锁.json"
    with lock.open("x", encoding="utf-8") as stream:
        json.dump({"owner": owner, "started_at_epoch": time.time()}, stream)
    started = time.time()
    from 映射音效 import build
    sandbox = output / "重新映射"
    evidence = sandbox / "证据"
    evidence.mkdir(parents=True)
    for name in ("SFX预设与集合.json", "音效目录位置.json", "兼容性与事件索引.json", "事件路径原生复核.json"):
        shutil.copyfile(local_batch(RAW) / "证据" / name, evidence / name)
    rebuilt = build(sandbox)
    rebuilt_by_id = {model["model_id"]: model for model in rebuilt["models"]}
    selected = [model for model in original["models"] if model["category"] == owner] if owner != "shared" else [model for model in original["models"] if not model["references"]]
    compared = []
    for model in selected:
        fresh = rebuilt_by_id[model["model_id"]]
        if any(fresh[key] != model[key] for key in ("unit_id", "selected_option_ids", "mapping_status", "model_status")) or len(fresh["references"]) != len(model["references"]):
            raise ValueError("重新映射配置或状态变化：" + model["model_id"])
        for left, right in zip(fresh["references"], model["references"]):
            if any(left.get(key) != right.get(key) for key in ("origin", "domain", "role_field", "preset_path", "field_chain", "source_event_path", "event_path", "mapping_status")):
                raise ValueError("重新映射引用变化：" + model["model_id"])
        compared.append({"model_id": model["model_id"], "status": "unchanged", "references": len(model["references"]), "mapping_status": model["mapping_status"]})
    receipts = []
    for job in jobs["jobs"]:
        if job["owner"] != owner:
            continue
        technical = inspect(check(job["source"]))
        if technical["decode_status"] != "passed" or technical["clip_samples"] or technical["channels"] != 2 or technical["sample_rate"] != 48000:
            raise ValueError("现有WAV未通过技术检查：" + job["event_path"])
        if technical["sha256"] != job["source_check"]["sha256"]:
            raise ValueError("源音频摘要变化")
        receipts.append({"job_id": job["job_id"], "event_path": job["event_path"], "owner": owner, "operation": "verified_reuse", "check": technical})
    result = {"status": "passed", "owner": owner, "queue_sha256": digest(batch / "事件任务清单.json"), "models": compared,
              "audio_receipts": receipts, "new_confirmed_events": [], "elapsed_seconds": time.time() - started,
              "listening_status": "not_individually_recorded"}
    save(output / "分片核验.json", result)
    save(output / "执行锁.json", {"owner": owner, "status": "complete", "completed_at_epoch": time.time()})
    print(json.dumps({"status": result["status"], "owner": owner, "models": len(compared), "audio": len(receipts), "seconds": result["elapsed_seconds"]}, ensure_ascii=False, indent=2), flush=True)


def safe_filename(role, short, sha, parent):
    short = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", short).strip(" .")
    room = min(70, 225 - len(str(parent.resolve())) - len(role) - 20)
    if room < 12:
        raise ValueError("分类输出路径过长")
    return f"{role}-{short[:room]}-{sha[:12]}.wav"


def csv_write(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def merge(batch):
    tasks = queue(batch)
    original = mapping(batch)
    output = batch / "分类音频"
    if output.exists() or (batch / "输出核验.json").exists():
        raise ValueError("分类结果拒绝覆盖")
    receipts = {}
    for owner, name in PARTS.items():
        result = read(batch / "分工" / name / "分片核验.json")
        if result["status"] != "passed" or result["owner"] != owner or result["queue_sha256"] != digest(batch / "事件任务清单.json"):
            raise ValueError("分片核验身份不符")
        expected_models = set(tasks["model_ids"][owner] if owner != "shared" else tasks["unresolved_models"])
        if {model["model_id"] for model in result["models"]} != expected_models:
            raise ValueError("分片型号不完整")
        if result.get("new_confirmed_events"):
            raise ValueError("新候选需主对话单独审查和冻结后合并")
        if {row["job_id"] for row in result["audio_receipts"]} != {row["job_id"] for row in tasks["jobs"] if row["owner"] == owner}:
            raise ValueError("分片事件所有权不符")
        for row in result["audio_receipts"]:
            if row["job_id"] in receipts:
                raise ValueError("事件重复执行")
            receipts[row["job_id"]] = row
    investigation = read(batch / "分工/缺项/缺项复核.json")
    if investigation["status"] != "complete" or set(investigation["reviewed_missing_paths"]) != set(tasks["missing_paths"]) or set(investigation["reviewed_unresolved_models"]) != set(tasks["unresolved_models"]):
        raise ValueError("缺项复核范围不完整")
    if investigation.get("proposed_new_events"):
        raise ValueError("存在尚未审批的新事件候选")
    grouped = defaultdict(list)
    for job in tasks["jobs"]:
        row = receipts[job["job_id"]]
        if row["check"]["sha256"] != job["source"]["sha256"]:
            raise ValueError("分片音频摘要不符")
        grouped[job["source"]["sha256"]].append(job)
    audio_rows, locations = [], {}
    for sha, members in sorted(grouped.items()):
        categories = {user["category"] for member in members for user in member["users"]}
        roles = {role for member in members for role in member["roles"]}
        scope, role = scope_name(categories), role_name(roles)
        parent = output / scope / role
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / safe_filename(role, members[0]["event_path"].rsplit("/", 1)[-1], sha, parent)
        if target.exists():
            raise FileExistsError("分类名称冲突")
        shutil.copyfile(check(members[0]["source"]), target)
        if digest(target) != sha:
            raise ValueError("分类复制摘要不符")
        relative = target.relative_to(batch).as_posix()
        for member in members:
            locations[member["event_path"]] = relative
            audio_rows.append({"使用范围": scope, "用途": role, "用途原字段": "|".join(member["roles"]), "事件路径": member["event_path"], "事件GUID": member["event_guid"],
                "渲染身份": member["job_id"], "来源批次": SOURCE, "操作": "核验后复用", "音频路径": relative, "文件字节": target.stat().st_size,
                "完整SHA256": sha, "时长秒": member["source_check"]["duration_seconds"], "格式": "48000Hz-双声道-PCM16",
                "循环状态": "未知有限渲染", "听审状态": "未逐条登记", "渲染参数": json.dumps(member["render_profile"], ensure_ascii=False), "使用型号": "|".join(sorted({user["model_id"] for user in member["users"]}))})
    fields = list(audio_rows[0])
    csv_write(batch / "音频分类清单.csv", fields, audio_rows)
    model_rows = []
    for model in original["models"]:
        model_dir = batch / "型号清单" / ("空中" if model["category"] == "air" else "地面")
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / (model["model_id"] + ".md")
        text = [f"# {model.get('unit_name', model['model_id'])}", "", f"型号：{model['model_id']}；配置：{model['model_variant']}；单位：{model['unit_id']}；选项：{model['selected_option_ids']}", "", f"模型状态：{model['model_status']}；原始映射：{model['mapping_status']}。", "", "| 用途 | 武器或弹药 | 状态 | 音频 |", "| --- | --- | --- | --- |"]
        refs = model["references"] or [{"mapping_status": "unresolved", "origin": {}, "domain": "unit", "event_path": "", "role_field": ""}]
        for ref in refs:
            origin = ref["origin"]
            location = locations.get(ref.get("event_path"), "") if ref["mapping_status"] == "confirmed" else ""
            row = {"型号": model["model_id"], "型号名称": model.get("unit_name", ""), "型号分类": model["category"], "变体": model["model_variant"], "单位编号": model["unit_id"],
                   "选项编号": "|".join(map(str, model["selected_option_ids"])), "模型状态": model["model_status"], "映射状态": ref["mapping_status"], "引用来源": ref["domain"],
                   "来源编号": origin.get("id", ""), "来源名称": origin.get("name", ""), "武器编号": origin.get("weapon_id", origin.get("id", "") if ref["domain"] == "weapon" else ""),
                   "弹药编号": origin.get("id", "") if ref["domain"] == "ammunition" else "", "用途字段": ref.get("role_field", ""), "源事件路径": ref.get("source_event_path", ""),
                   "实际事件路径": ref.get("event_path", ""), "音频路径": location, "完整SHA256": ref.get("sample_sha256", ""), "来源批次": SOURCE,
                   "解码状态": ref.get("decode_status", "not_available"), "循环状态": ref.get("loop_status", "unknown"), "听审状态": ref.get("listening_status", "not_available"),
                   "字段链": json.dumps(ref.get("field_chain", []), ensure_ascii=False), "命中分支复核": json.dumps(ref.get("playback_review", {}), ensure_ascii=False)}
            model_rows.append(row)
            label = ROLES.get(ref.get("role_field"), ref.get("role_field") or "无当前单位引用")
            link = ""
            if location:
                from urllib.parse import quote
                import os
                link = "[试听WAV](" + quote(Path(os.path.relpath(batch / location, model_dir)).as_posix(), safe="/.") + ")"
            text.append(f"| {label} | {str(origin.get('name', origin.get('id', ''))).replace('|', '／')} | {ref['mapping_status']} | {link or '缺项或空字段保留'} |")
        text += ["", "原始样本未恢复，文件为有限时长事件渲染；循环与逐条听审未验收。模型待修状态独立保留。"]
        model_path.write_text("\n".join(text) + "\n", encoding="utf-8")
    csv_write(batch / "型号音频清单.csv", list(model_rows[0]), model_rows)
    missing = "# 缺项说明\n\n7条未解析武器回退路径、3个无当前单位引用入口保留；672个空字段不改写为提取失败。\n\n## 武器回退路径\n\n" + "\n".join("- `" + path + "`" for path in tasks["missing_paths"])
    missing += "\n\n## 无当前单位引用入口\n\n" + "\n".join("- `" + model + "`" for model in tasks["unresolved_models"])
    missing += "\n\n详细检查范围和证据见分工/缺项/缺项复核.json及该对话的复核说明。正常弹药命中分支已存在的声音继续复用，未把旧回退路径标成已恢复。\n"
    (batch / "缺项说明.md").write_text(missing, encoding="utf-8")
    summary = {"status": "classified_candidate", "models": len(original["models"]), "models_with_audio": original["summary"]["models_with_audio"], "reused_events": len(tasks["jobs"]),
               "unique_wav": len(grouped), "classification_wav_bytes": sum(Path(batch / path).stat().st_size for path in set(locations.values())),
               "new_unique_events": 0, "rerendered_verification_events": 1, "categories": dict(Counter(row["使用范围"] for row in audio_rows)),
               "roles": dict(Counter(row["用途"] for row in audio_rows)), "missing_paths": len(tasks["missing_paths"]), "unresolved_models": tasks["unresolved_models"], "model_csv_rows": len(model_rows)}
    save(batch / "分类汇总.json", summary)
    (batch / "README.md").write_text("# 载具分类音频候选库\n\n打开分类音频目录，按使用范围和用途直接试听WAV；型号清单为129个入口提供相对音频链接。音频分类清单.csv用于按声音查看，型号音频清单.csv用于按型号查看。\n\n此批次只进行音频提取验证、复用与分类，不是开发资源包。原正式音效库V1保持原样。本库未自动升级为正式版。\n\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n\n全部音频为event_render，不是raw_sample。共享文件只保存一次，分类专用性仅对当前129入口范围成立。循环、响度与逐条听审不因技术检查通过而改变。证据/受控提取为一次重新渲染验证，未混入分类音频。\n", encoding="utf-8")
    save(batch / "输出核验.json", {"files": [{"path": path.relative_to(batch).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(batch.rglob("*")) if path.is_file()]})
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def verify(batch):
    started = time.time()
    tasks, original = queue(batch), mapping(batch)
    for row in read(batch / "输入清单.json")["files"]:
        check(row)
    protected = read(batch / "证据/保留载荷清单.json")["files"]
    for row in protected:
        check(row)
    outputs = read(batch / "输出核验.json")["files"]
    for row in outputs:
        path = (batch / row["path"]).resolve()
        if not path.is_relative_to(batch.resolve()) or path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("分类输出与清单不符")
    with (batch / "音频分类清单.csv").open(encoding="utf-8-sig", newline="") as stream:
        audio = list(csv.DictReader(stream))
    with (batch / "型号音频清单.csv").open(encoding="utf-8-sig", newline="") as stream:
        models = list(csv.DictReader(stream))
    assert len(audio) == 101 and {row["事件路径"] for row in audio} == set(original["audio"])
    assert {row["型号"] for row in models} == {model["model_id"] for model in original["models"]}
    assert Counter(row["映射状态"] for row in models) == Counter(confirmed=884, empty_field=672, event_not_indexed=56, unresolved=3)
    for row in audio:
        details = inspect(batch / row["音频路径"])
        assert details["sha256"] == row["完整SHA256"] and details["decode_status"] == "passed" and details["clip_samples"] == 0
    by_event = {row["事件路径"]: row for row in audio}
    for row in models:
        if row["音频路径"]:
            asset = by_event[row["实际事件路径"]]
            assert row["音频路径"] == asset["音频路径"] and row["完整SHA256"] == asset["完整SHA256"] and row["映射状态"] == "confirmed"
    for path in (batch / "型号清单").rglob("*.md"):
        from urllib.parse import unquote
        for link in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding="utf-8")):
            resolved = (path.parent / unquote(link)).resolve()
            assert resolved.is_relative_to(batch.resolve()) and resolved.is_file()
    summary = read(batch / "分类汇总.json")
    assert len(list((batch / "分类音频").rglob("*.wav"))) == summary["unique_wav"] == 101
    result = {"status": "passed", "models": 129, "classified_events": 101, "output_files": len(outputs), "protected_files": len(protected),
              "field_references": 1612, "model_csv_rows": len(models), "wav_clip_samples": 0, "elapsed_seconds": time.time() - started,
              "new_unique_events": summary["new_unique_events"], "rerendered_verification_events": 1, "listening_status": "not_individually_recorded", "formal_source_unchanged": True}
    save(batch / "交付完成核验.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "probe", "work", "merge", "verify"))
    parser.add_argument("--batch", required=True)
    parser.add_argument("--owner", choices=tuple(PARTS))
    args = parser.parse_args()
    batch = local_batch(args.batch)
    if args.action == "work":
        if not args.owner:
            parser.error("分片任务必须声明owner")
        work(batch, args.owner)
    else:
        {"prepare": prepare, "probe": probe, "merge": merge, "verify": verify}[args.action](batch)


if __name__ == "__main__":
    main()
