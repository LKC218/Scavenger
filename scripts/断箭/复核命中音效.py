"""依据当前构建机器码，逐项核对未索引武器 Impact 的弹药优先分支。"""

import argparse
from collections import Counter
import copy
from pathlib import Path

from 核对音效调用 import read_json, verify_inputs
from 探测音效 import digest, local_batch, save, resolve_input


def assert_field(layouts, typename, field, offset, fieldtype):
    item = next(t for t in layouts if t["type"] == typename)
    assert any(f["name"] == field and f["offset"] == offset and f["type"] == fieldtype for f in item["fields"]), (typename, field)


def check_field(ref, sfx):
    entry = next(e for e in sfx["entries"] if e["path"] == ref["preset_path"])
    assert ref["field_chain"][0]["object_id"] == entry["id"]
    current = entry["data"]
    for part in ref["field_chain"][1:]:
        if "field" in part:
            current = current[part["field"]]
        else:
            assert current["m_FileID"] == 0 and current["m_PathID"] == part["object_id"]
            current = sfx["objects"][str(part["object_id"])]
    assert current == ref["source_event_path"]


def build(batch, source):
    binary, _ = verify_inputs(batch)
    assert read_json(batch / "输入清单.json")["previous_audio_batch"] == source.name
    native = read_json(batch / "证据/音效调用反汇编-含展开链.json")
    assert native["binary_sha256"] == digest(binary)
    layouts = read_json(batch / "证据/音效类型布局.json")["layouts"]
    hit = "BrokenArrow.Client.Ecs.BattleSystem.Components.HitComponent"
    assert_field(layouts, hit, "AmmoInfo", 0x30, "BrokenArrow.DataBase.Models.Ammunitions")
    assert_field(layouts, hit, "WeaponAudioPreset", 0x28, "BrokenArrow.AudioSystem.Presets.WeaponSoundsPreset")
    assert_field(layouts, "BrokenArrow.DataBase.Models.Ammunitions", "<AudioPreset>k__BackingField", 0x40, "System.String")
    assert_field(layouts, "BrokenArrow.AudioSystem.Presets.WeaponSoundsPreset", "WeaponCollection", 0x18, "BrokenArrow.AudioSystem.Weapons.WeaponAudioCollection")
    assert_field(layouts, "BrokenArrow.AudioSystem.Weapons.WeaponAudioCollection", "Impact", 0x20, "System.String")
    assert_field(layouts, "BrokenArrow.AudioSystem.Presets.AmmunitionSoundsPreset", "Impact", 0x18, "System.String")
    assert_field(layouts, "BrokenArrow.AudioSystem.Presets.AmmunitionSoundsPreset", "MissileInterception", 0x20, "System.String")
    method = native["methods"]["0x1806bb8d0"]
    assert method["methods"][0]["type"].endswith("ExplosionEventSystem") and method["methods"][0]["name"] == "Update"
    instructions = {i["address"]: i for i in method["instructions"]}
    # 固定到本批构建的条件分支；地址或指令变化时停止，不将旧结论套到新版。
    expected = {
        0x1806BBB7E: ("mov", "rax, qword ptr [rbx + 0x30]"),
        0x1806BBB9E: ("mov", "r15, qword ptr [rax + 0x40]"),
        0x1806BBC1A: ("mov", "rdi, qword ptr [rax + 8]"),
        0x1806BBC3B: ("je", "0x1806bbc6b"),
        0x1806BBC8F: ("jne", "0x1806bbd1f"),
        0x1806BBCCD: ("mov", "rdi, qword ptr [rax + 0x18]"),
        0x1806BBCDA: ("mov", "rdi, qword ptr [rdi + 0x20]"),
        0x1806BBD28: ("cmp", "byte ptr [rbx + 0x1c], 0x64"),
        0x1806BBD2E: ("mov", "rdi, qword ptr [rdi + 0x20]"),
        0x1806BBD34: ("mov", "rdi, qword ptr [rdi + 0x18]"),
        0x1806BBD3D: ("call", "0x18408e8e0"),
        0x1806BBD44: ("jne", "0x1806bbda4"),
        0x1806BBD8D: ("mov", "rdx, rdi"),
        0x1806BBD9D: ("call", "0x18087e350"),
    }
    for address, pair in expected.items():
        if pair is not None:
            assert (instructions[address]["mnemonic"], instructions[address]["operands"]) == pair, hex(address)
    globals_ = read_json(batch / "证据/命中泛型类型核验.json")
    assert any("Entity.Get<" + hit + ">" in r["value"] for r in globals_)
    assert any("GetOrLoadAsset<BrokenArrow.AudioSystem.Presets.AmmunitionSoundsPreset>" in r["value"] for r in globals_)
    constructors = read_json(batch / "证据/命中构造反汇编.json")
    assert constructors["binary_sha256"] == native["binary_sha256"]
    ctor = {i["address"]: i for i in constructors["methods"]["0x180821b90"]["instructions"]}
    assert ctor[0x180821BBE]["operands"] == "rdx, qword ptr [r9 + 0x40]"
    assert ctor[0x180821BC2]["operands"] == "qword ptr [rcx + 0x28], rdx"
    assert ctor[0x180821BCF]["operands"] == "rdx, qword ptr [rbx + 0x30]"
    assert ctor[0x180821BD7]["operands"] == "qword ptr [rdi + 0x30], rdx"
    old = read_json(source / "型号音效映射.json")
    mapping = copy.deepcopy(old)
    sfx = read_json(source / "证据/SFX预设与集合.json")
    catalog = read_json(source / "证据/音效目录位置.json")
    audio = read_json(resolve_input(batch, source / "音效配套库/型号音效映射.json"))["audio"]
    tables = read_json(local_batch(old["static_batch"]) / "证据/装配/源配置核验.json")["database"]["tables"]
    ammunition_by_id = {a["Id"]: a for a in tables["Ammunitions"]}
    weapon_ammunition = {a["Id"]: a for a in tables["WeaponAmmunitions"]}
    reviews = []
    for model in mapping["models"]:
        for index, ref in enumerate(model["references"]):
            if ref["mapping_status"] != "event_not_indexed":
                continue
            assert ref["domain"] == "weapon" and ref["role_field"] == "Impact"
            check_field(ref, sfx)
            ammo_refs = [(i, r) for i, r in enumerate(model["references"]) if r["domain"] == "ammunition" and r["origin"]["weapon_id"] == ref["origin"]["id"] and r.get("role_field") == "Impact"]
            routes = []
            for ammo_index, ammo in ammo_refs:
                check_field(ammo, sfx)
                origin = ammo["origin"]
                row = weapon_ammunition[origin["relation"]["Id"]]
                assert row["UnitId"] == model["unit_id"] and row["WeaponId"] == ref["origin"]["id"] and row["AmmunitionId"] == origin["id"] and row["Quantity"] > 0
                assert ammunition_by_id[origin["id"]]["AudioPreset"] == ammo["preset"]
                assert any(c["primary_key"] == ammo["preset"] and c["internal_id"] == ammo["preset_path"] for c in catalog)
                available = ammo["mapping_status"] == "confirmed" and ammo["event_path"] in audio
                routes.append({"reference_index": ammo_index, "ammunition_id": origin["id"], "ammunition_name": origin["name"],
                               "weapon_ammunition_id": row["Id"], "quantity": row["Quantity"], "preset": ammo["preset"],
                               "event_path": ammo["event_path"], "normal_impact_status": "confirmed_existing_render" if available else "unresolved",
                               "sample_sha256": audio[ammo["event_path"]]["check"]["sha256"] if available else None})
            normal_covered = bool(routes) and all(r["normal_impact_status"] == "confirmed_existing_render" for r in routes)
            review = {"status": "ammunition_priority_confirmed" if normal_covered else "still_unresolved",
                      "normal_impact_covered": normal_covered, "fallback_status": "event_not_indexed",
                      "fallback_condition": "弹药预设对象缺失时读取武器Impact；弹药字段为空不触发此回退",
                      "runtime_observed": False, "ammunition_routes": routes,
                      "evidence": "证据/音效调用反汇编-含展开链.json#0x1806bb8d0",
                      "note": "所列配置正常命中优先使用已导出的弹药声音；武器回退事件仍缺失。不是事件别名，也未恢复该旧路径。"}
            ref["playback_review"] = review
            reviews.append({"model_id": model["model_id"], "unit_id": model["unit_id"], "reference_index": index,
                            "weapon_id": ref["origin"]["id"], "source_event_path": ref["source_event_path"], **review})
        own = [r for r in reviews if r["model_id"] == model["model_id"]]
        if own:
            model["impact_review"] = {"reviewed_fallback_references": len(own),
                                      "normal_impact_covered": all(r["normal_impact_covered"] for r in own),
                                      "note": "正常命中分支已定位到弹药声音；未索引武器回退路径保留，原始映射状态不变。"}
    summary = {"reviewed_missing_paths": len({r["source_event_path"] for r in reviews}), "reviewed_references": len(reviews),
               "affected_models": len({r["model_id"] for r in reviews}),
               "normal_covered_references": sum(r["normal_impact_covered"] for r in reviews),
               "ammunition_route_records": sum(len(r["ammunition_routes"]) for r in reviews),
               "normal_impact_events": sorted({a["event_path"] for r in reviews for a in r["ammunition_routes"]}),
               "recovered_new_event_paths": 0, "proven_globally_unused_paths": 0,
               "unresolved_fallback_paths": len({r["source_event_path"] for r in reviews}),
               "runtime_status": "not_observed", "original_mapping_statuses": dict(Counter(m["mapping_status"] for m in mapping["models"]))}
    mapping.update(batch=batch.name, source_audio_batch=source.name, playback_review_summary=summary)
    save(batch / "型号音效映射.json", mapping)
    result = {"batch": batch.name, "source_audio_batch": source.name, "binary_sha256": native["binary_sha256"],
              "summary": summary, "reviews": reviews,
              "branch_evidence": [instructions[a] for a, pair in expected.items() if pair],
              "conclusion": "7条为仍存在代码回退用途的未索引路径；当前所列弹药预设可定位，正常命中不需用这些武器回退路径。不能断言全游戏永不使用。"}
    save(batch / "命中音效复核.json", result)
    print(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--source-batch", required=True)
    args = parser.parse_args()
    batch, source = local_batch(args.batch), local_batch(args.source_batch)
    if batch == source or (batch / "型号音效映射.json").exists():
        raise FileExistsError("复核输出必须独立且未占用")
    build(batch, source)


if __name__ == "__main__":
    main()
