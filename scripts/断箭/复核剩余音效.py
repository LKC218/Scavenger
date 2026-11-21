"""重新枚举全部bank元数据，确证剩余音效路径及型号关联；不猜配或批量渲染。"""

import argparse
import ast
from collections import defaultdict
import ctypes as C
import hashlib
import json
from pathlib import Path
import re
import struct
import time
import uuid

from 探测音效 import ROOT, FMOD, GUID, digest, local_batch, save


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def record(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def check(row):
    actual = record(Path(row["path"]))
    if any(actual[key] != row[key] for key in ("bytes", "sha256")):
        raise ValueError("冻结文件发生变化：" + row["path"])


def model_review(tasks, config, assembly, catalog):
    by_key = defaultdict(list)
    norm = lambda text: text.replace("\\", "/").strip("/").casefold()
    for location in catalog["locations"]:
        if location.get("primary_key"):
            by_key[norm(location["primary_key"])].append(location)
    results = []
    tables = config["database"]["tables"]
    for model_id in tasks["unresolved_models"]:
        plan = next(row for row in assembly["roots"] if Path(row["root_path"]).stem == model_id)
        target = norm(plan["root_path"])
        locations = [row for row in catalog["locations"] if norm(row.get("internal_id") or "") == target]
        identities = {norm(model_id), target} | {norm(row["primary_key"]) for row in locations}
        units, fields = [], []
        for unit in tables["Units"]:
            key = norm(unit.get("ModelFileName") or "")
            resolved = by_key.get(key, [])
            if any(norm(row.get("internal_id") or "") == target for row in resolved):
                units.append(unit["Id"])
            elif not resolved and key and key.rsplit("/", 1)[-1] == norm(model_id):
                units.append(unit["Id"])
            for field, value in unit.items():
                if isinstance(value, str) and norm(value) in identities:
                    fields.append({"unit_id": unit["Id"], "field": field, "value": value})
        hits = [{"table": name, "id": row["Id"], "field": field, "value": value}
                for name, rows in tables.items() for row in rows for field, value in row.items()
                if isinstance(value, str) and norm(model_id) in norm(value)]
        results.append({"model_id": model_id, "root_prefab": plan["root_path"],
                        "catalog_locations": locations, "exact_unit_ids": units, "exact_fields": fields,
                        "database_text_candidates": hits, "units_checked": len(tables["Units"]),
                        "tables_checked": len(tables), "previous_selected_unit_id": plan["selected_unit_id"],
                        "status": "candidate_requires_configuration_review" if units or fields else "unresolved_no_current_unit_reference",
                        "runtime_observed": False})
    return results


def run(batch):
    started = time.time()
    if batch.exists():
        raise FileExistsError("补查使用新批次，拒绝覆盖")
    previous = local_batch("BA-26273833-005")
    config_path = ROOT / "local-data/断箭/本地配置.json"
    local = read(config_path)
    game = Path(local["game_directory"])
    build = re.search(r'"buildid"\s+"(\d+)"', Path(local["steam_manifest"]).read_text()).group(1)
    if build != "26273833" or not batch.name.startswith("BA-" + build + "-"):
        raise ValueError("构建不符合批准范围")
    inputs = {}
    for row in read(previous / "输入清单.json")["files"]:
        check(row)
        inputs[row["path"]] = row
    protected = {row["path"]: row for row in read(previous / "证据/保留载荷清单.json")["files"]}
    for row in read(previous / "输出核验.json")["files"]:
        check({**row, "path": str(previous / row["path"])})
    for path in previous.rglob("*"):
        if path.is_file():
            protected[str(path.resolve())] = record(path)
    for row in protected.values():
        check(row)
    banks = sorted(game.rglob("*.bank"))
    strings_path = game / "BrokenArrow_Data/StreamingAssets/Master.strings.bank"
    if strings_path not in banks:
        raise ValueError("缺少strings bank")
    static = local_batch("BA-26273833-001")
    sfx_path = local_batch("BA-26273833-002") / "证据/SFX预设与集合.json"
    catalog_path = Path(read(sfx_path)["source"]).parent.parent / "catalog.bin"
    extra = [config_path, Path(local["steam_manifest"]), *banks, catalog_path,
             static / "证据/装配/源配置核验.json", static / "装配映射.json",
             previous / "事件任务清单.json", previous / "证据/正式映射快照.json",
             Path(__file__), Path(__file__).with_name("解析装配.py"), Path(__file__).with_name("探测音效.py")]
    for path in extra:
        inputs[str(path.resolve())] = record(path)
    batch.mkdir()
    save(batch / "输入清单.json", {"batch": batch.name, "source_batch": previous.name, "build": build,
         "unity_version": "6000.3.24f1", "unity_header_basis": "源输入与005已验证文件摘要一致",
         "created_at_epoch": started, "bank_count": len(banks), "files": list(inputs.values())})
    save(batch / "证据/保护清单.json", {"files": list(protected.values())})
    tasks = read(previous / "事件任务清单.json")
    events, strings, bank_receipts, queries = [], [], [], []
    runtime = FMOD(ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll")
    try:
        if int(runtime.version, 16) != 0x20111:
            raise ValueError("FMOD运行库版本不符")
        runtime.initialize()
        strings_bank = runtime.load(strings_path)
        count = C.c_int()
        runtime.call("FMOD_Studio_Bank_GetStringCount", strings_bank, C.byref(count))
        for ordinal in range(count.value):
            guid, buffer, length = GUID(), C.create_string_buffer(4096), C.c_int()
            runtime.call("FMOD_Studio_Bank_GetStringInfo", strings_bank, ordinal, C.byref(guid), buffer, 4096, C.byref(length))
            strings.append({"path": buffer.value.decode("utf-8"), "guid": str(uuid.UUID(bytes_le=bytes(guid.data)))})
        save(batch / "证据/strings全路径索引.json", strings)
        for path in [strings_path] + [path for path in banks if path != strings_path]:
            name = path.relative_to(game).as_posix()
            try:
                handle = strings_bank if path == strings_path else runtime.load(path)
                items = runtime.events(handle, name)
                events.extend(items)
                bank_receipts.append({"bank": name, "status": "passed", "event_count": len(items)})
            except Exception as error:
                bank_receipts.append({"bank": name, "status": "failed", "error": str(error)})
            save(batch / "证据/全bank完整事件索引.json", {"runtime_version": runtime.version, "banks": bank_receipts, "events": events})
            print("已枚举", len(bank_receipts), "/", len(banks), "bank，累计事件记录", len(events), flush=True)
        for path in tasks["missing_paths"]:
            guid = GUID()
            code = runtime.dll.FMOD_Studio_System_LookupID(runtime.system, path.encode(), C.byref(guid))
            value = str(uuid.UUID(bytes_le=bytes(guid.data))) if code == 0 else None
            candidates = [event for event in events if value and event["guid"] == value]
            queries.append({"requested_path": path, "lookup_result": code, "guid": value,
                            "exact_event_matches": [event for event in events if event["path"] == path],
                            "casefold_event_matches": [event for event in events if event["path"].casefold() == path.casefold()],
                            "casefold_string_matches": [entry for entry in strings if entry["path"].casefold() == path.casefold()],
                            "canonical_events": candidates,
                            "status": "resolved_native_guid" if candidates else "unresolved_no_native_guid"})
        save(batch / "证据/路径原生查询.json", queries)
    finally:
        runtime.close()
    # 只重用纯目录读取函数，避免Unity解析模块的导入副作用。
    source = Path(__file__).with_name("解析装配.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "read_catalog_locations")
    namespace = {"struct": struct, "hashlib": hashlib}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    catalog = namespace["read_catalog_locations"](catalog_path)
    configuration = read(static / "证据/装配/源配置核验.json")
    if catalog["sha256"] != configuration["catalog_provenance"]["sha256"]:
        raise ValueError("目录相对模型提取来源已变化")
    models = model_review(tasks, configuration, read(static / "装配映射.json"), catalog)
    save(batch / "证据/型号精确关联复核.json", models)
    known = []
    for job in tasks["jobs"]:
        matches = [event for event in events if event["guid"] == job["event_guid"] and event["path"] == job["event_path"]]
        known.append({"event_path": job["event_path"], "guid": job["event_guid"],
                      "status": "confirmed" if matches else "missing", "banks": sorted({event["bank"] for event in matches})})
    save(batch / "证据/已有事件身份复核.json", known)
    result = {"status": "complete" if all(row["status"] == "passed" for row in bank_receipts) and all(row["status"] == "confirmed" for row in known) else "incomplete",
              "build": build, "bank_files": len(banks), "banks_passed": sum(row["status"] == "passed" for row in bank_receipts),
              "event_records": len(events), "unique_event_guids": len({event["guid"] for event in events}), "string_records": len(strings),
              "existing_events_confirmed": sum(row["status"] == "confirmed" for row in known),
              "missing_paths_resolved": sum(row["status"] == "resolved_native_guid" for row in queries),
              "missing_paths_unresolved": [row["requested_path"] for row in queries if row["status"] != "resolved_native_guid"],
              "model_association_candidates": [row["model_id"] for row in models if row["status"] != "unresolved_no_current_unit_reference"],
              "new_rendered_events": 0, "runtime_game_observed": False, "elapsed_seconds": time.time() - started}
    save(batch / "剩余音效复核.json", result)
    (batch / "README.md").write_text("# 剩余音效完整事件复核\n\n全部bank仅枚举事件元数据，未批量渲染语音、音乐或环境。完整表位于证据/全bank完整事件索引.json；缺失路径、已有101事件及3个型号的逐项结果分别存放在证据目录。未按相似名称替代事件，正式库和005分类库不变。发现确证候选时由主对话继续补提。\n\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in inputs.values():
        check(row)
    for row in protected.values():
        check(row)
    save(batch / "输出核验.json", {"files": [{"path": path.relative_to(batch).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(batch.rglob("*")) if path.is_file()]})
    for row in read(batch / "输出核验.json")["files"]:
        check({**row, "path": str(batch / row["path"])})
    save(batch / "交付核验.json", {"status": "passed" if result["status"] == "complete" else "incomplete", "input_files": len(inputs),
         "protected_files": len(protected), "output_files": len(read(batch / "输出核验.json")["files"]), "source_payloads_unchanged": True,
         "elapsed_seconds": time.time() - started})
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    run(local_batch(parser.parse_args().batch))
