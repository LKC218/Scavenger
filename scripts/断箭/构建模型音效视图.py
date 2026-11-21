"""复用已核验关系，生成引用既有WAV的离线模型音效分类视图。"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "local-data/断箭/批次"
MODELS = BASE / "BA-26273833-001/美军模型库"
CLASSIFIED = BASE / "BA-26273833-005"
SOUNDS = BASE / "BA-26273833-007/音效浏览库"
SOURCE_KEYS = ("型号", "型号名称", "型号分类", "变体", "单位编号", "选项编号", "模型状态", "映射状态", "引用来源", "来源编号", "来源名称", "武器编号", "弹药编号", "用途字段", "源事件路径", "实际事件路径", "完整SHA256", "字段链", "命中分支复核")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def check(row):
    path = Path(row["path"])
    if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
        raise ValueError("文件指纹不符：" + str(path))
    return path


def inside(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("文件路径越界")
    return path


def csv_read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def csv_save(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def source_files():
    paths = []
    for row in read(SOUNDS / "输出核验.json")["files"]:
        path = inside(SOUNDS, row["path"])
        check({**row, "path": str(path)})
        paths.append(path)
    csv_names = {"音频分类清单.csv", "型号音频清单.csv"}
    expected = {r["path"]: r for r in read(CLASSIFIED / "输出核验.json")["files"]}
    for name in sorted(csv_names):
        path = CLASSIFIED / name
        check({**expected[name], "path": str(path)})
        paths.append(path)
    model = MODELS / "模型清单.json"
    frozen = next(r for r in read(CLASSIFIED / "输入清单.json")["files"] if Path(r["path"]).resolve() == model.resolve())
    check(frozen)
    paths += [model, CLASSIFIED / "输入清单.json", CLASSIFIED / "输出核验.json", SOUNDS / "输出核验.json",
              SOUNDS.parent / "交付核验.json", Path(__file__), Path(__file__).with_name("模型音效视图模板.html"),
              Path(__file__).with_name("验证模型音效视图.cjs")]
    if read(SOUNDS.parent / "交付核验.json")["status"] != "passed":
        raise ValueError("源声音库交付核验未通过")
    return [record(p) for p in paths]


def derive(batch):
    library = batch / "模型音效浏览库"
    catalog = deepcopy(read(SOUNDS / "音效目录.json"))
    original_models = read(MODELS / "模型清单.json")["entries"]
    audio_rows = csv_read(CLASSIFIED / "音频分类清单.csv")
    source_rows = csv_read(CLASSIFIED / "型号音频清单.csv")
    if len({r["name"] for r in original_models}) != 129 or len(source_rows) != 1615:
        raise ValueError("模型或引用范围变化")
    old_by_pair = {(r["事件路径"], r["完整SHA256"]): r for r in audio_rows}
    by_guid = {r["guid"]: r for r in catalog["events"]}
    if len(by_guid) != 250 or len(old_by_pair) != 101:
        raise ValueError("声音集合变化")
    for row in audio_rows:
        event = by_guid[row["事件GUID"]]
        if (event["path"], event["check"]["sha256"]) != (row["事件路径"], row["完整SHA256"]):
            raise ValueError("既有事件身份或音频摘要不一致")
    models = {r["name"]: {"id": r["name"], "name": r["name"], "category": r["category"], "variant": r["variant"],
              "model_status": r["status"], "event_guids": [], "reference_counts": {}} for r in original_models}
    grouped = defaultdict(list)
    for index, source in enumerate(source_rows):
        model = models[source["型号"]]
        if source["型号分类"] != model["category"] or source["变体"] != model["variant"] or source["模型状态"] != model["model_status"]:
            raise ValueError("型号来源与模型清单不一致")
        if source["型号名称"]:
            if model["name"] not in (model["id"], source["型号名称"]):
                raise ValueError("同一模型名称冲突")
            model["name"] = source["型号名称"]
        grouped[model["id"]].append(source)
        if source["音频路径"]:
            if source["映射状态"] != "confirmed":
                raise ValueError("非确认引用含有效声音")
            previous = old_by_pair[(source["实际事件路径"], source["完整SHA256"])]
            event = by_guid[previous["事件GUID"]]
            event.setdefault("bindings", []).append({"model_id": model["id"], "field": source["用途字段"], "source_row": index + 2})
    for model_id, model in models.items():
        model["reference_counts"] = dict(Counter(r["映射状态"] for r in grouped[model_id]))
        model["event_guids"] = sorted(r["guid"] for r in catalog["events"] if any(b["model_id"] == model_id for b in r.get("bindings", [])))
    for event in catalog["events"]:
        event.setdefault("bindings", [])
        event["model_ids"] = sorted({b["model_id"] for b in event["bindings"]})
        event["association_status"] = "confirmed" if event["bindings"] else "unassigned"
        source = inside(SOUNDS, event["audio_path"])
        event["source_audio_path"] = event["audio_path"]
        event["audio_path"] = Path(os.path.relpath(source, library)).as_posix()
    # 既有反向清单与逐引用正向清单相互核对，不以名称补齐关系。
    for old in audio_rows:
        if set(by_guid[old["事件GUID"]]["model_ids"]) != set(old["使用型号"].split("|")):
            raise ValueError("原声音使用型号与逐引用关系不一致")
    summary = {**catalog["summary"], "models": len(models), "models_with_audio": sum(bool(m["event_guids"]) for m in models.values()),
               "associated_events": sum(bool(e["bindings"]) for e in catalog["events"]),
               "unassigned_events": sum(not e["bindings"] for e in catalog["events"]),
               "confirmed_references": sum(len(e["bindings"]) for e in catalog["events"]),
               "reference_rows": len(source_rows), "reference_status_counts": dict(Counter(r["映射状态"] for r in source_rows))}
    if (summary["models_with_audio"], summary["associated_events"], summary["unassigned_events"], summary["confirmed_references"]) != (126, 101, 149, 884):
        raise ValueError("关联结果与批准范围不一致")
    catalog.update(schema_version=2, batch=batch.name, source_batch="BA-26273833-007", status="candidate",
                   source_scope=catalog["scope"], scope="保留全部250项声音用途浏览，复用101项声音与当前129个美军模型入口的既有确证关系，其余149项单列未关联",
                   association_source="BA-26273833-005/型号音频清单.csv", models=list(models.values()), summary=summary,
                   model_scope="129个美军资源入口的当前选用配置，不覆盖其他阵营模型")
    audit = [{"csv_row": i + 2, **{k: r[k] for k in SOURCE_KEYS}} for i, r in enumerate(source_rows)]
    return catalog, audit


def build(batch):
    if batch.exists():
        raise FileExistsError("模型音效视图批次拒绝覆盖")
    inputs = source_files()
    catalog, audit = derive(batch)
    batch.mkdir()
    save(batch / "输入清单.json", {"files": inputs})
    library = batch / "模型音效浏览库"
    library.mkdir()
    save(library / "模型音效目录.json", catalog)
    save(library / "引用复核.json", {"source": "BA-26273833-005/型号音频清单.csv", "rows": audit})
    csv_rows = [{"型号": m["id"], "型号名称": m["name"], "分类": "空中" if m["category"] == "air" else "地面", "变体": m["variant"],
                 "模型状态": m["model_status"], "声音数": len(m["event_guids"]), "已确认引用数": m["reference_counts"].get("confirmed", 0),
                 "空字段数": m["reference_counts"].get("empty_field", 0), "未索引引用数": m["reference_counts"].get("event_not_indexed", 0),
                 "无关联入口": m["reference_counts"].get("unresolved", 0), "事件GUID": "|".join(m["event_guids"])} for m in catalog["models"]]
    csv_save(library / "模型分类清单.csv", csv_rows, list(csv_rows[0]))
    links = [{"型号": model_id, "事件GUID": event["guid"], "事件路径": event["path"], "声音用途": event["category"],
              "用途字段": "|".join(sorted({b["field"] for b in event["bindings"] if b["model_id"] == model_id})),
              "关联状态": "已确认", "技术状态": event["status"], "音频路径": event["audio_path"], "完整SHA256": event["check"]["sha256"]}
             for event in catalog["events"] for model_id in event["model_ids"]]
    csv_save(library / "模型声音关联.csv", links, list(links[0]))
    template = Path(__file__).with_name("模型音效视图模板.html").read_text(encoding="utf-8")
    (library / "模型音效浏览库.html").write_text(template.replace("__CATALOG__", json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")), encoding="utf-8")
    (library / "README.md").write_text("# 模型音效分类浏览库\n\n打开模型音效浏览库.html，可按用途、模型和未关联事件查找声音。129个美军入口中126个有已确认声音，101事件已关联、149未关联；技术状态仍为247通过、3待复核。\n\n页面不自动播放。模型状态、关联状态、音频技术状态分别保存，空字段和旧缺项不补猜。\n\n音频直接引用相邻BA-26273833-007批次，不含WAV副本。请保留本目录与007批次的相对位置，单独移动此目录将无法试听。JSON、CSV和引用复核记录可离线查阅。当前为独立候选，003正式版不变；逐条听审、循环、真实浏览器布局和媒体验收仍待完成。\n", encoding="utf-8")
    outputs = [{**record(p), "path": p.relative_to(library).as_posix()} for p in sorted(library.iterdir()) if p.is_file()]
    save(library / "输出核验.json", {"files": outputs})
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))


def verify(batch):
    inputs = read(batch / "输入清单.json")["files"]
    for row in inputs:
        check(row)
    library = batch / "模型音效浏览库"
    outputs = read(library / "输出核验.json")["files"]
    for row in outputs:
        check({**row, "path": str(inside(library, row["path"]))})
    expected, audit = derive(batch)
    if read(library / "模型音效目录.json") != expected or read(library / "引用复核.json")["rows"] != audit:
        raise ValueError("派生关系或原引用记录不一致")
    for event in expected["events"]:
        path = (library / event["audio_path"]).resolve()
        source = inside(SOUNDS, event["source_audio_path"])
        if path != source or digest(path) != event["check"]["sha256"]:
            raise ValueError("媒体引用身份不符")
    if len(csv_read(library / "模型分类清单.csv")) != 129:
        raise ValueError("型号CSV条数错误")
    script_check = read(batch / "页面脚本核验.json")
    if script_check["status"] != "passed" or script_check["page_sha256"] != digest(library / "模型音效浏览库.html"):
        raise ValueError("缺少当前页面脚本检查")
    result = {"status": "passed", "input_files_unchanged": len(inputs), "output_files": len(outputs), "summary": expected["summary"],
              "audio_files_copied": 0, "browser_executed": False, "listening_status": "pending"}
    save(batch / "交付核验.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"BA-26273833-\d{3}", args.batch):
        raise ValueError("批次名不符")
    target = BASE / args.batch
    {"build": build, "verify": verify}[args.action](target)
