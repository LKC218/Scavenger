"""复核音频交付、字段依据、原模型载荷和实际读取输入的结束摘要。"""

import argparse
import csv
import json
from pathlib import Path
import time
from 探测音效 import ROOT, local_batch, save, digest, resolve_input
from 检查音效 import inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--source-batch", help="第二轮复用音频与配置证据的只读批次")
    parser.add_argument("--archive-manifest", type=Path, help="显式只读核对已迁移的历史载荷，不重写旧验收记录")
    args = parser.parse_args()
    batch = local_batch(args.batch)
    if args.archive_manifest:
        from 历史资源解析 import ArchiveResolver
        resolver = ArchiveResolver(args.archive_manifest, require_verified=True)
        rows = [r for r in resolver.rows.values() if Path(r["old"]["path"]).is_relative_to(batch)]
        if not rows: raise ValueError("该批次没有已验证的归档映射")
        for row in rows: resolver.resolve(row["old"]["path"])
        print(json.dumps({"status": "passed", "mode": "archive_payload_integrity", "batch": batch.name, "mapped_files": len(rows), "original_records_written": False, "limits": "仅回读历史载荷，不重跑字段链、FMOD、视觉或听审验收"}, ensure_ascii=False, indent=2))
        return
    source = local_batch(args.source_batch) if args.source_batch else batch
    output = batch / "音效配套库"
    start = time.time()
    errors, checks = [], {}
    inputs = json.loads((batch / "输入清单.json").read_text(encoding="utf-8"))["files"]
    inputs += json.loads((source / "证据/事件路径原生复核.json").read_text(encoding="utf-8"))["files"]
    main_source = json.loads((source / "证据/音效预设原字段.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8-sig"))
    static_evidence = json.loads((local_batch("BA-26273833-001") / "证据/装配/源配置核验.json").read_text(encoding="utf-8"))
    inputs.append({"path": str(Path(config["game_directory"]) / static_evidence["catalog_provenance"]["source"]),
                   "sha256": static_evidence["catalog_provenance"]["sha256"]})
    inputs.append({"path": str(Path(config["game_directory"]) / main_source["source"]), "sha256": main_source["source_sha256"],
                   "bytes": main_source["source_bytes"], "mtime_ns": main_source["source_mtime_ns"]})
    sfx = json.loads((source / "证据/SFX预设与集合.json").read_text(encoding="utf-8"))
    inputs.append({"path": sfx["source"], "sha256": sfx["sha256"]})
    receipts = []
    for path_string, record in {row["path"]: row for row in inputs}.items():
        path = resolve_input(batch, path_string)
        actual = digest(path)
        valid = actual == record["sha256"] and ("bytes" not in record or path.stat().st_size == record["bytes"]) and ("mtime_ns" not in record or path.stat().st_mtime_ns == record["mtime_ns"])
        receipts.append({"path": path_string, "resolved_path": str(path), "sha256": actual, "unchanged": valid})
        if not valid:
            errors.append("输入发生变化：" + path_string)
    checks["inputs"] = len(receipts)
    delivery = json.loads((output / "型号音效映射.json").read_text(encoding="utf-8"))
    if source != batch:
        if delivery.get("source_audio_batch") != source.name:
            errors.append("来源批次记录不符")
        old_manifest = json.loads(resolve_input(batch, source / "音效配套库/输出核验.json").read_text(encoding="utf-8"))
        if (source / "音效配套库").exists():
            for record in old_manifest["files"]:
                path = source / "音效配套库" / record["path"]
                if digest(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
                    errors.append("上一版试听库变化：" + record["path"])
            checks["unchanged_previous_library_files"] = len(old_manifest["files"])
        else:
            migration = json.loads((batch / "输入迁移.json").read_text(encoding="utf-8"))["retirement"]
            frozen_path = Path(migration["manifest_path"]).resolve()
            receipt_path = Path(migration["receipt_path"]).resolve()
            if not all(p.is_relative_to(batch.resolve()) for p in (frozen_path, receipt_path)):
                raise ValueError("退役凭证必须位于当前批次内")
            if digest(frozen_path) != migration["manifest_sha256"]:
                raise ValueError("旧版删除清单摘要变化")
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
            retired = json.loads(receipt_path.read_text(encoding="utf-8"))
            if retired["status"] != "complete" or retired["manifest_sha256"] != migration["manifest_sha256"]:
                errors.append("旧版删除未完成或回执不符")
            old_rows = {Path(row["path"]).resolve(): row for row in frozen["files"]}
            for record in old_manifest["files"]:
                old = old_rows.get((source / "音效配套库" / record["path"]).resolve(), {})
                if any(old.get(key) != record[key] for key in ("sha256", "bytes")):
                    errors.append("退役清单与旧交付指纹不符：" + record["path"])
            if any(Path(row["path"]).exists() for row in frozen["files"]) or any(Path(row["path"]).exists() for row in frozen["roots"]):
                errors.append("已退役旧库仍有文件或目录")
            checks["retired_previous_library_files"] = len(old_manifest["files"])
            checks["deleted_obsolete_files"] = len(frozen["files"])
        previous = json.loads((source / "型号音效映射.json").read_text(encoding="utf-8"))
        previous_refs = {(m["model_id"], i): r for m in previous["models"] for i, r in enumerate(m["references"])}
        review_count = 0
        for model in delivery["models"]:
            for i, ref in enumerate(model["references"]):
                old = previous_refs[(model["model_id"], i)]
                if any(old.get(key) != ref.get(key) for key in ("source_event_path", "event_path", "mapping_status", "origin", "field_chain")):
                    errors.append("复核改变了原始引用：" + model["model_id"])
                review = ref.get("playback_review")
                if not review:
                    continue
                review_count += 1
                for route in review["ammunition_routes"]:
                    ammunition = model["references"][route["reference_index"]]
                    if not (ammunition["origin"]["id"] == route["ammunition_id"] and ammunition["origin"]["weapon_id"] == ref["origin"]["id"] and ammunition["event_path"] == route["event_path"] and ammunition.get("sample_sha256") == route["sample_sha256"]):
                        errors.append("复核弹药路线或音频指纹不符")
        checks["reviewed_unchanged_references"] = review_count
    original_library = local_batch(delivery["static_batch"]) / "美军模型库"
    original = json.loads((original_library / "模型清单.json").read_text(encoding="utf-8"))
    payload_count = 0
    for model in original["entries"]:
        for record in model["files"].values():
            path = original_library / record["path"]
            payload_count += 1
            if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
                errors.append("原模型载荷与清单不符：" + record["path"])
    checks["original_model_files"] = payload_count
    manifest = json.loads((output / "输出核验.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        path = output / record["path"]
        if digest(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            errors.append("输出摘要不符：" + record["path"])
    checks["output_files"] = len(manifest["files"])
    for event_path, audio in delivery["audio"].items():
        check = inspect(output / audio["path"])
        if check["sha256"] != audio["check"]["sha256"] or check["decode_status"] != "passed":
            errors.append("音频技术检查未通过：" + event_path)
    checks["audio_events"] = len(delivery["audio"])
    checks["clip_samples"] = sum(audio["check"]["clip_samples"] for audio in delivery["audio"].values())
    entries = {entry["path"]: entry for entry in sfx["entries"]}
    resolutions = json.loads((source / "证据/事件路径原生复核.json").read_text(encoding="utf-8"))["resolutions"]
    native = {row["requested_path"]: row for row in resolutions}
    field_count = 0
    for model in delivery["models"]:
        for link in model["model_links"].values():
            from urllib.parse import unquote
            if not (output / unquote(link)).resolve().is_file():
                errors.append("模型相对链接不存在：" + model["model_id"])
        for ref in model["references"]:
            if ref["mapping_status"] != "confirmed":
                continue
            field_count += 1
            current = entries[ref["preset_path"]]["data"]
            chain = ref["field_chain"]
            for item in chain[1:]:
                if "field" in item:
                    current = current[item["field"]]
                elif "object_id" in item:
                    if current.get("m_PathID") != item["object_id"] or current.get("m_FileID") != 0:
                        errors.append("集合引用不符")
                    current = sfx["objects"][str(item["object_id"]) ]
            if current != ref["source_event_path"]:
                errors.append("源事件字段不符")
            if ref["source_event_path"] != ref["event_path"]:
                resolution = native.get(ref["source_event_path"], {})
                if resolution.get("lookup_result") != 0 or resolution.get("guid") != ref["event"]["guid"]:
                    errors.append("别名没有原生GUID证据")
            if ref.get("audio_path") and digest(output / ref["audio_path"]) != ref["sample_sha256"]:
                errors.append("引用的音频摘要不符")
    checks["confirmed_field_chains"] = field_count
    with (output / "型号音效映射.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_models = {row["model_id"] for row in csv.DictReader(stream)}
    if csv_models != {model["model_id"] for model in delivery["models"]}:
        errors.append("CSV型号覆盖不完整")
    checks["csv_models"] = len(csv_models)
    release = delivery.get("release", {})
    if release:
        release_record = json.loads((output / "正式版本.json").read_text(encoding="utf-8"))
        if release_record != release or release.get("status") != "formal" or release.get("acceptance") != "user_accepted_library":
            errors.append("正式版本记录与交付映射不符")
        checks["formal_version"] = release.get("version")
    result = {"status": "passed" if not errors else "failed", "checks": checks, "errors": errors,
              "input_end_receipts": receipts, "elapsed_seconds": time.time() - start,
              "browser_status": "not_verified_file_protocol_blocked", "listening_status": "pending",
              "library_acceptance": release.get("acceptance", "pending")}
    save(batch / "交付完成核验.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "input_end_receipts"}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
