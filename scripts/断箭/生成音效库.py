"""核验并复制共享事件渲染，生成无在线依赖的型号音效目录。"""

import argparse
from collections import Counter
import csv
import html
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote
from 探测音效 import ROOT, local_batch, save, digest
from 检查音效 import inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--output-name", default="音效配套库")
    parser.add_argument("--source-batch", help="复用已交付事件渲染的只读来源批次；型号映射仍读取本批次")
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name:
        raise ValueError("输出目录名必须为单层名称")
    batch = local_batch(args.batch)
    source_batch = local_batch(args.source_batch) if args.source_batch else batch
    output = batch / args.output_name
    output.mkdir(exist_ok=False)
    mapping = json.loads((batch / "型号音效映射.json").read_text(encoding="utf-8"))
    renders = json.loads((source_batch / "事件渲染结果.json").read_text(encoding="utf-8"))
    if source_batch != batch and mapping.get("source_audio_batch") != source_batch.name:
        raise ValueError("渲染来源与映射记录不符")
    static_library = local_batch(mapping["static_batch"]) / "美军模型库"
    all_audio, output_files = {}, []
    event_users = Counter(ref.get("event_path") for model in mapping["models"] for ref in model["references"] if ref["mapping_status"] == "confirmed")
    model_users = Counter(path for model in mapping["models"] for path in {ref["event_path"] for ref in model["references"] if ref["mapping_status"] == "confirmed"})
    (output / "音频").mkdir()
    for path, result in renders.items():
        if result["check"]["decode_status"] != "passed":
            continue
        source = source_batch / result["render"]["output"]
        check = inspect(source)
        if check["sha256"] != result["check"]["sha256"] or check["decode_status"] != "passed":
            raise ValueError("渲染载荷变化或检查未通过")
        relative = "音频/" + check["sha256"] + ".wav"
        target = output / relative
        if not target.exists():
            shutil.copyfile(source, target)
        if digest(target) != check["sha256"]:
            raise ValueError("音频复制摘要不符")
        all_audio[path] = {"path": relative, "kind": "event_render", "check": check,
                           "render": result["render"], "model_users": model_users[path],
                           "reference_users": event_users[path], "source_batch": source_batch.name}
    csv_rows = []
    csv_fields = ["model_id", "variant", "category", "model_status", "unit_id", "domain", "role_field", "preset", "origin_table", "origin_id", "event_path", "event_guid", "bank", "sample_kind", "sample_path", "sample_sha256", "mapping_status", "decode_status", "loop_status", "listening_status", "playback_review", "normal_impact_covered", "ammunition_impact_events"]
    for model in mapping["models"]:
        thumbnail = model["model_files"]["预览/整体.png"]
        source = static_library / thumbnail["path"]
        target = output / "预览" / (model["model_id"] + ".png")
        target.parent.mkdir(exist_ok=True)
        if digest(source) != thumbnail["sha256"]:
            raise ValueError("原模型预览摘要不符")
        shutil.copyfile(source, target)
        if digest(target) != thumbnail["sha256"]:
            raise ValueError("预览复制摘要不符")
        model["thumbnail"] = target.relative_to(output).as_posix()
        model["model_links"] = {name: quote(Path(os.path.relpath(static_library / file["path"], output)).as_posix(), safe="/.")
                                for name, file in model["model_files"].items() if name in ("model.glb", "model.blend")}
        for ref in model["references"]:
            path = ref.get("event_path", "")
            audio = all_audio.get(path)
            if audio:
                ref.update(audio_path=audio["path"], sample_sha256=audio["check"]["sha256"],
                           sample_kind="event_render", decode_status="passed", model_users=audio["model_users"],
                           loop_status="unknown_finite_render", listening_status="pending")
            csv_rows.append({"model_id": model["model_id"], "variant": model["model_variant"],
                "category": model["category"], "model_status": model["model_status"], "unit_id": model["unit_id"],
                "domain": ref["domain"], "role_field": ref.get("role_field", ""), "preset": ref.get("preset", ""),
                "origin_table": ref["origin"]["table"], "origin_id": ref["origin"]["id"],
                "event_path": path, "event_guid": (ref.get("event") or {}).get("guid", ""),
                "bank": (ref.get("event") or {}).get("bank", ""), "sample_kind": ref.get("sample_kind", ""),
                "sample_path": ref.get("audio_path", ""), "sample_sha256": ref.get("sample_sha256", ""),
                "mapping_status": ref["mapping_status"], "decode_status": ref.get("decode_status", "not_available"),
                "loop_status": ref.get("loop_status", "unknown"), "listening_status": ref.get("listening_status", "pending"),
                "playback_review": ref.get("playback_review", {}).get("status", ""),
                "normal_impact_covered": ref.get("playback_review", {}).get("normal_impact_covered", ""),
                "ammunition_impact_events": " | ".join(r["event_path"] for r in ref.get("playback_review", {}).get("ammunition_routes", []))})
        if not model["references"]:
            csv_rows.append({**dict.fromkeys(csv_fields, ""), "model_id": model["model_id"], "variant": model["model_variant"],
                "category": model["category"], "model_status": model["model_status"], "mapping_status": "unresolved",
                "decode_status": "not_available", "loop_status": "unknown", "listening_status": "not_available"})
        model["audio_event_count"] = len({ref["event_path"] for ref in model["references"] if ref.get("audio_path")})
        model["missing_event_count"] = len({ref["event_path"] for ref in model["references"] if ref["mapping_status"] == "event_not_indexed"})
        save(output / "型号" / (model["model_id"] + ".json"), model)
    missing = sorted({ref["event_path"] for model in mapping["models"] for ref in model["references"] if ref["mapping_status"] == "event_not_indexed"})
    summary = {"models": len(mapping["models"]), "categories": dict(Counter(row["category"] for row in mapping["models"])),
               "mapping": dict(Counter(row["mapping_status"] for row in mapping["models"])),
               "models_with_audio": sum(row["audio_event_count"] > 0 for row in mapping["models"]),
               "events": len(all_audio), "unique_wav": len({row["check"]["sha256"] for row in all_audio.values()}),
               "missing_event_paths": missing, "reference_statuses": dict(Counter(ref["mapping_status"] for model in mapping["models"] for ref in model["references"])),
               "listening_status": "pending", "raw_sample_count": 0, "event_render_count": len(all_audio),
               "model_partial_count": sum(row["model_status"] == "partial" for row in mapping["models"])}
    if "playback_review_summary" in mapping:
        summary["playback_review"] = mapping["playback_review_summary"]
    delivery = {**mapping, "summary": summary, "audio": all_audio}
    save(output / "型号音效映射.json", delivery)
    with (output / "型号音效映射.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(csv_rows)
    template = (Path(__file__).parent / "音效目录模板.html").read_text(encoding="utf-8")
    payload = json.dumps(delivery, ensure_ascii=False).replace("<", "\\u003c")
    (output / "配套音效目录.html").write_text(template.replace("__DELIVERY_DATA__", payload), encoding="utf-8")
    (output / "README.md").write_text(f"""# 载具音效配套库

直接打开 `配套音效目录.html`，搜索型号后主动点击原生音频控件试听。页面无在线依赖、无自动播放；每次只播放一条，循环需主动勾选。

## 范围与状态

- 静态批次 `{mapping['static_batch']}` 的129个资源入口，52空中、77地面；包含配置变体。
- {summary['models_with_audio']}项有确证声音；{len(all_audio)}个事件、{summary['unique_wav']}份共享WAV；{summary['mapping'].get('confirmed',0)}项引用完整、{summary['mapping'].get('partial',0)}项部分映射、{summary['mapping'].get('unresolved',0)}项无当前单位引用。
- 所有音频为 `event_render`，不是原始样本；48kHz、双声道PCM16。参数、位置与有限停止规则见清单的 `audio.*.render`。
- 持续事件请求8秒后ALLOWFADEOUT，保留2秒尾音；一次性事件按需延长至源事件长度加0.5秒（最多30秒），实际文件长度见技术检查。默认参数渲染没有复现游戏的动态转速、距离变化、混音和操作时序。
- 实际听审均为待试听；循环点未知，不保证首尾无缝。空字段、{len(missing)}条未索引事件及3个未关联模型在清单保留，不用相似声音填补。
- 原4项模型待修状态保留；音频状态独立。语音、音乐、环境不在本轮范围。

## 文件与迁移

`音频/`按SHA-256共享；`型号/`保存每入口配置和字段链；JSON/CSV保存型号、角色、事件、bank、样本与状态；`预览/`为已校验的原模型预览副本。

音频、预览及页面可作为一个目录携带。模型链接依赖相邻静态批次，当前相对关系为 `../../{mapping['static_batch']}/美军模型库/`；迁移时须同时保留这一路径或更新链接。GLB、Blender文件未复制到本库。

## 证据与核验

本库输入与结束核验保存在本库上级批次目录。设置字段核验、SFX预设与集合、完整事件索引、A10首测和每次失败/成功的FMOD原件保留在音频来源批次 `{source_batch.name}`。运行库原始WAV存在RIFF长度少4字节的问题；交付文件仅重封装，PCM字节保持不变。来源与原件摘要在渲染记录。

`输出核验.json`固定本库每个输出摘要。实际听感、循环接缝和与原游戏的一致性需要另行验证。不得将页面播放状态写为听审通过。
""", encoding="utf-8")
    if source_batch != batch:
        review = summary["playback_review"]
        with (output / "README.md").open("a", encoding="utf-8") as stream:
            stream.write(f"\n## 第二轮命中分支复核\n\n本库属于 `{batch.name}`，{len(all_audio)}个事件渲染来自 `{source_batch.name}`，复制后逐文件校验。原渲染参数中的输出路径以来源批次为基准。\n\n{review['reviewed_missing_paths']}条未索引路径、{review['reviewed_references']}条武器回退引用保持原值与原状态。当前{review['affected_models']}个受影响型号所列弹药的正常命中分支可使用已导出的弹药事件；本轮新增事件{review['recovered_new_event_paths']}，全局废弃证明{review['proven_globally_unused_paths']}。弹药对象缺失时仍会走未解析武器回退，运行时是否触发未观测。详见上级目录的 `命中音效复核.json` 与代码证据。实际听审仍待完成。\n")
    for file in sorted(output.rglob("*")):
        if file.is_file():
            output_files.append({"path": file.relative_to(output).as_posix(), "bytes": file.stat().st_size, "sha256": digest(file)})
    save(output / "输出核验.json", {"summary": summary, "files": output_files, "verification": "音频和预览复制后重新核对摘要；型号映射来自真实字段链"})
    save(batch / "覆盖与异常报告.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
