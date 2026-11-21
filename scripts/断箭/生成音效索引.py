"""从已验收音效库生成独立的三层索引；不修改原库、不复制音频和模型。"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import time
from urllib.parse import unquote

from 探测音效 import ROOT, digest, local_batch, save

SAMPLES = ["US_A10", "US_AH64D", "US_M1A2"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]


def exact_numbers(value):
    """Unity对象ID超出JS安全整数范围时保留十进制字符串。"""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, list):
        return [exact_numbers(v) for v in value]
    if isinstance(value, dict):
        return {k: exact_numbers(v) for k, v in value.items()}
    return value


def build(batch, source):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available,current,HOME
    if available():
        catalog=current()[1]
        output=HOME/'维护/声音关联索引.json';output.parent.mkdir(parents=True,exist_ok=True)
        save(output,{'models':[{k:r[k] for k in ['id','name','audio_ids']} for r in catalog['models']],'audio':[{k:r[k] for k in ['id','name','url','model_ids']} for r in catalog['audio']]})
        print('已导出当前统一声音关联索引：'+str(output));return
    if batch.exists():
        raise ValueError("新索引批次拒绝覆盖")
    library = source / "音效配套库"
    pointer = read(ROOT / "local-data/断箭/正式音效库.json")
    manifest_path = library / "输出核验.json"
    if pointer["batch"] != source.name or digest(manifest_path) != pointer["output_manifest_sha256"]:
        raise ValueError("当前正式音效库指针或指纹不符")
    manifest = read(manifest_path)
    for row in manifest["files"]:
        path = (library / row["path"]).resolve()
        if not path.is_relative_to(library.resolve()) or digest(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("正式库载荷与清单不符")
    delivery = read(library / "型号音效映射.json")
    if delivery["release"]["status"] != "formal":
        raise ValueError("来源未提升为正式版")
    output = batch / "音效索引库"
    output.mkdir(parents=True)
    data_dir = output / "数据"
    source_files = [manifest_path, library / "型号音效映射.json", library / "正式版本.json", ROOT / "local-data/断箭/正式音效库.json"]
    routes, assets, event_ids = {}, {}, {}
    for event_path, audio in delivery["audio"].items():
        render = audio["render"]
        profile = {k: render.get(k) for k in ("parameters", "position", "duration_request_seconds", "stop", "tail_seconds", "sample_rate", "channels")}
        audio_id = render["event"]["guid"] + ":" + token(profile)
        if audio_id in assets:
            raise ValueError("同一事件渲染身份重复")
        event_ids[event_path] = audio_id
        check = audio["check"]
        url = "资源/音频/" + check["sha256"] + ".wav"
        routes[url] = {"path": str((library / audio["path"]).resolve()), "bytes": check["bytes"], "sha256": check["sha256"], "type": "audio/wav"}
        assets[audio_id] = {
            "audio_id": audio_id, "event_guid": render["event"]["guid"], "event_path": event_path,
            "label": event_path.rsplit("/", 1)[-1], "bank": render["event"]["bank"], "url": url,
            "sha256": check["sha256"], "bytes": check["bytes"], "duration_seconds": check["duration_seconds"],
            "sample_rate": check["sample_rate"], "channels": check["channels"], "format": check["format"],
            "kind": audio["kind"], "render_profile": profile, "source_batch": audio["source_batch"],
            "loop_status": check["loop_status"], "listening_status": check["listening_status"],
            "source_oneshot": render["event"]["oneshot"], "users": [],
        }
    vehicles, counts = [], Counter()
    for model in delivery["models"]:
        identity = {"model_id": model["model_id"], "variant": model["model_variant"], "unit_id": model["unit_id"], "option_ids": sorted(model["selected_option_ids"])}
        config_id = model["model_id"] + ":" + token(identity)
        bindings = []
        for i, ref in enumerate(model["references"]):
            audio_id = event_ids.get(ref["event_path"]) if ref["mapping_status"] == "confirmed" else None
            if ref["mapping_status"] == "confirmed" and not audio_id:
                raise ValueError("确证引用没有渲染：" + model["model_id"])
            binding = {
                "binding_id": config_id + ":r" + str(i), "reference_index": i,
                "domain": ref["domain"], "role_field": ref["role_field"], "status": ref["mapping_status"],
                "audio_id": audio_id, "event_path": ref["event_path"], "source_event_path": ref["source_event_path"],
                "origin": ref["origin"], "preset": ref["preset"], "preset_path": ref.get("preset_path"),
                "field_chain": exact_numbers(ref.get("field_chain", [])),
                "playback_review": ref.get("playback_review"),
            }
            bindings.append(binding)
            counts[binding["status"]] += 1
            if audio_id:
                assets[audio_id]["users"].append({"model_id": model["model_id"], "config_id": config_id, "binding_id": binding["binding_id"], "domain": binding["domain"], "role_field": binding["role_field"], "origin_id": binding["origin"]["id"], "weapon_id": binding["origin"].get("weapon_id")})
        preview_url = "资源/预览/" + model["model_id"] + ".png"
        preview = library / model["thumbnail"]
        routes[preview_url] = {"path": str(preview.resolve()), "bytes": preview.stat().st_size, "sha256": digest(preview), "type": "image/png"}
        item = {**identity, "config_id": config_id, "name": model.get("unit_name", model["model_id"]), "category": model["category"],
                "mapping_status": model["mapping_status"], "model_status": model["model_status"], "issues": model["issues"],
                "thumbnail_url": preview_url, "bindings": bindings, "sample_model_url": None,
                "configuration_note": model["configuration_note"]}
        if model["model_id"] in SAMPLES:
            url = "资源/模型/" + model["model_id"] + ".glb"
            path = (library / unquote(model["model_links"]["model.glb"])).resolve()
            expected = model["model_files"]["model.glb"]
            if digest(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
                raise ValueError("样例模型摘要不符")
            routes[url] = {"path": str(path), "bytes": expected["bytes"], "sha256": expected["sha256"], "type": "model/gltf-binary"}
            source_files.append(path)
            item["sample_model_url"] = url
        vehicles.append(item)
    summary = {"models": len(vehicles), "audio_events": len(assets), "models_with_audio": sum(any(b["audio_id"] for b in v["bindings"]) for v in vehicles),
               "reference_statuses": dict(counts), "mapping_statuses": dict(Counter(v["mapping_status"] for v in vehicles)),
               "missing_event_paths": sorted({b["event_path"] for v in vehicles for b in v["bindings"] if b["status"] == "event_not_indexed"})}
    if summary["models"] != delivery["summary"]["models"] or summary["reference_statuses"] != delivery["summary"]["reference_statuses"]:
        raise ValueError("索引引用数量不守恒")
    header = {"schema_version": 1, "index_batch": batch.name, "source_library_batch": source.name, "source_version": delivery["release"]["version"]}
    save(data_dir / "音频资源.json", {**header, "assets": assets})
    save(data_dir / "载具绑定.json", {**header, "summary": summary, "vehicles": vehicles})
    actions = {
        "move": {"label": "发动机 / 运动片段", "anchor": "body", "max_voices": 1, "cooldown_ms": 250, "volume": 0.45},
        "shot": {"label": "射击 / 发射片段", "anchor": "muzzle", "max_voices": 2, "cooldown_ms": 150, "volume": 0.65},
        "impact": {"label": "命中 / 爆炸", "anchor": "impact", "max_voices": 4, "cooldown_ms": 80, "volume": 0.7},
        "interception": {"label": "拦截爆炸", "anchor": "impact", "max_voices": 4, "cooldown_ms": 80, "volume": 0.65},
        "death": {"label": "载具毁伤", "anchor": "body", "max_voices": 1, "cooldown_ms": 500, "volume": 0.65},
    }
    for rule in actions.values():
        rule.update(loop=False, fade_in_seconds=0.025, fade_out_seconds=0.12, distance_model="inverse", ref_distance=8, rolloff_factor=1)
    rules = {**header, "actions": actions, "sample_models": SAMPLES, "global_max_voices": 12, "cache_max_bytes": 64 * 1024 * 1024,
             "impact_selection": {"present": "按武器与弹药选择Impact；拦截选择MissileInterception；字段为空不回退", "missing": "仅显式声明弹药预设对象缺失时读取武器Impact", "unknown": "保持未知，不猜配"},
             "anchor_status": "body_center_and_adjustable_muzzle_are_demo_anchors", "runtime_parameters": "demo_only_not_original_mix", "loop_policy": "disabled_until_loop_points_verified"}
    save(data_dir / "播放规则.json", rules)
    source_dir = ROOT / "src/音效索引"
    for path in source_dir.iterdir():
        if path.suffix in (".html", ".css", ".mjs"):
            shutil.copyfile(path, output / path.name)
            source_files.append(path)
    tool_root = ROOT / "local-data/tools/three"
    pinned = read(tool_root / "当前版本.json")
    tool = tool_root / pinned["version"]
    metadata = tool / "来源与校验.json"
    if digest(metadata) != pinned["metadata_sha256"]:
        raise ValueError("Three.js版本校验记录变化")
    files = ["build/three.module.js", "build/three.core.js", "examples/jsm/loaders/GLTFLoader.js", "examples/jsm/controls/OrbitControls.js", "examples/jsm/utils/BufferGeometryUtils.js", "examples/jsm/utils/SkeletonUtils.js", "LICENSE"]
    original = {f["path"]: f for f in read(metadata)["files"]}
    for name in files:
        path = tool / name
        if digest(path) != original[name]["sha256"]:
            raise ValueError("Three.js运行文件指纹变化")
        target = output / "vendor/three" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        source_files.append(path)
    source_files += [metadata, Path(__file__).resolve()]
    source_files += [Path(__file__).parent / n for n in ("预览音效索引.py", "核验音效索引.py", "验证音效索引.mjs") if (Path(__file__).parent / n).is_file()]
    save(batch / "输入清单.json", {**header, "created_at_epoch": time.time(), "three_version": pinned["version"], "files": [{"path": str(p.resolve()), "bytes": p.stat().st_size, "sha256": digest(p)} for p in source_files]})
    save(batch / "资源路由.json", {"routes": routes})
    save(batch / "生成摘要.json", {**summary, "three_version": pinned["version"], "audio_payload_copied": False, "sample_model_payload_copied": False})
    (output / "README.md").write_text("# 载具音效索引验证库\n\n从项目根目录使用参数列表运行 `python scripts/断箭/预览音效索引.py --batch " + batch.name + "`，按启动地址打开独立验证页。直接file打开不能执行ES模块与资源请求。\n\n三份JSON位于数据目录。资源路由保存在批次上级，包含本机绝对路径，禁止公开发布该路由。源声音及模型按需读取，未复制到此目录。来源正式库与原模型保持只读。\n\n样例炮口为可调示意点，场景按包围盒归一化，不代表真实米制尺度。全部片段关闭循环。按一次动作播放一次渲染片段，未复现原FMOD参数、连续射击时序或程序动画。\n", encoding="utf-8")
    save(batch / "输出核验.json", {"files": [{"path": str(p.relative_to(batch).as_posix()), "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(batch.rglob("*")) if p.is_file()]})
    print(json.dumps({"batch": batch.name, **summary, "three_version": pinned["version"]}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--source-batch", default="BA-26273833-003")
    args = parser.parse_args()
    build(local_batch(args.batch), local_batch(args.source_batch))


if __name__ == "__main__":
    main()
