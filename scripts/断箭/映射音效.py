"""沿已交付模型的选用配置、数据库关系与SFX字段建立可追溯音效映射。"""

import argparse
from collections import Counter
import json
from pathlib import Path
from 探测音效 import ROOT, local_batch, save, digest


def build(batch):
    static = local_batch("BA-26273833-001")
    library = json.loads((static / "美军模型库/模型清单.json").read_text(encoding="utf-8"))
    plans = json.loads((static / "装配映射.json").read_text(encoding="utf-8"))["roots"]
    tables = json.loads((static / "证据/装配/源配置核验.json").read_text(encoding="utf-8"))["database"]["tables"]
    by_id = {name: {row["Id"]: row for row in rows} for name, rows in tables.items()}
    sfx = json.loads((batch / "证据/SFX预设与集合.json").read_text(encoding="utf-8"))
    catalog = json.loads((batch / "证据/音效目录位置.json").read_text(encoding="utf-8"))
    entries = {row["path"]: row for row in sfx["entries"]}
    objects = sfx["objects"]
    events = {row["path"]: row for row in json.loads((batch / "证据/兼容性与事件索引.json").read_text(encoding="utf-8"))["events"]}
    resolutions_file = batch / "证据/事件路径原生复核.json"
    resolutions = {row["requested_path"]: row for row in json.loads(resolutions_file.read_text(encoding="utf-8"))["resolutions"]} if resolutions_file.exists() else {}
    models = []
    for model in sorted(library["entries"], key=lambda row: (row["category"] != "air", row["name"])):
        plan = next(row for row in plans if Path(row["root_path"]).stem == model["name"])
        unit = by_id["Units"].get(plan["selected_unit_id"])
        result = {"model_id": model["name"], "model_variant": model["variant"], "category": model["category"],
                  "model_status": model["status"], "model_files": model["files"], "unit_id": plan["selected_unit_id"],
                  "selected_option_ids": plan["selected_option_ids"], "references": [], "issues": [],
                  "configuration_note": "音效沿静态批次选用配置；基础机体或待修参考的可见外挂范围见模型限制",
                  "model_limitations": model["limitations"]}
        models.append(result)
        if unit is None:
            result["issues"].append("没有当前Units精确引用，未猜配其他单位的声音")
            result["mapping_status"] = "unresolved"
            continue
        result["unit_name"] = unit.get("HUDName") or unit["Name"]

        def resolve_preset(preset, origin, domain):
            if not preset:
                result["references"].append({"origin": origin, "domain": domain, "preset": preset,
                                             "mapping_status": "empty_preset", "decode_status": "not_applicable"})
                return
            matches = [row for row in catalog if row["primary_key"] == preset and row["internal_id"] in entries]
            matches = {row["internal_id"]: row for row in matches}
            if len(matches) != 1:
                result["issues"].append(f"预设{preset}正式位置匹配数为{len(matches)}")
                result["references"].append({"origin": origin, "domain": domain, "preset": preset, "mapping_status": "unresolved"})
                return
            location = next(iter(matches.values()))
            entry = entries[location["internal_id"]]

            def walk(data, chain, visited):
                for field, value in data.items():
                    if field.startswith("m_") or field == "AudioCommands":
                        continue
                    evidence = chain + [{"field": field}]
                    if isinstance(value, str) and (value.startswith("event:/") or value == ""):
                        event = events.get(value)
                        native = resolutions.get(value)
                        if event is None and native and len(native["canonical_events"]) == 1:
                            event = native["canonical_events"][0]
                        result["references"].append({"origin": origin, "domain": domain, "preset": preset,
                            "preset_path": entry["path"], "catalog_location_offset": location["offset"],
                            "role_field": field, "field_chain": evidence,
                            "source_event_path": value, "event_path": event["path"] if event else value, "event": event,
                            "resolution": "native_guid" if native and event else "exact_path",
                            "mapping_status": "confirmed" if event else "empty_field" if value == "" else "event_not_indexed",
                            "decode_status": "pending" if event else "not_available",
                            "loop_status": "unknown", "listening_status": "pending"})
                    elif field in ("WeaponCollection", "AmmunitionCollection") and isinstance(value, dict):
                        pointer = value.get("m_PathID")
                        if value.get("m_FileID") == 0 and str(pointer) in objects and pointer not in visited:
                            walk(objects[str(pointer)], evidence + [{"object_id": pointer}], visited | {pointer})
                        elif pointer:
                            result["issues"].append(f"集合引用未解析：{preset}.{field}")
            walk(entry["data"], [{"object_id": entry["id"]}], {entry["id"]})

        selected_options = [by_id["Options"][id] for id in plan["selected_option_ids"]]
        overrides = [option for option in selected_options if option.get("AudioPresetOverride")]
        unique = {option["AudioPresetOverride"] for option in overrides}
        if len(unique) > 1:
            result["issues"].append("多个不同AudioPresetOverride，优先级未确认，单位声音未确证")
        else:
            preset = next(iter(unique)) if unique else unit.get("AudioPreset")
            resolve_preset(preset, {"table": "Units", "id": unit["Id"], "field": "AudioPreset",
                                   "overrides": [{"option_id": o["Id"], "value": o["AudioPresetOverride"]} for o in overrides]}, "unit")
        active = {row["turret_id"] for row in plan["slot_turrets"]}
        child_relations = []
        changed = True
        while changed:
            changed = False
            for relation in tables["TurretUnits"]:
                turret = by_id["Turrets"][relation["TurretId"]]
                if relation["UnitId"] == unit["Id"] and turret.get("ParentTurretId") in active and turret["Id"] not in active:
                    active.add(turret["Id"])
                    child_relations.append({"TurretUnits_id": relation["Id"], "turret_id": turret["Id"], "parent_id": turret["ParentTurretId"]})
                    changed = True
        result["active_turret_ids"] = sorted(active)
        result["child_turret_relations"] = child_relations
        weapons = {}
        for relation in tables["TurretWeapons"]:
            if relation["TurretId"] in active:
                weapons.setdefault(relation["WeaponId"], []).append({"table": "TurretWeapons", **relation})
        for relation in tables["SquadWeapons"]:
            if relation["UnitId"] == unit["Id"]:
                weapons.setdefault(relation["WeaponId"], []).append({"table": "SquadWeapons", **relation})
        result["selected_weapon_ids"] = sorted(weapons)
        for weapon_id, relations in weapons.items():
            weapon = by_id["Weapons"][weapon_id]
            resolve_preset(weapon.get("AudioPreset"), {"table": "Weapons", "id": weapon_id,
                           "name": weapon.get("HUDName") or weapon["Name"], "relations": relations}, "weapon")
            for relation in tables["WeaponAmmunitions"]:
                if relation["UnitId"] != unit["Id"] or relation["WeaponId"] != weapon_id or relation.get("Quantity", 0) <= 0:
                    continue
                ammo = by_id["Ammunitions"][relation["AmmunitionId"]]
                resolve_preset(ammo.get("AudioPreset"), {"table": "Ammunitions", "id": ammo["Id"],
                               "name": ammo.get("HUDName") or ammo["Name"], "weapon_id": weapon_id,
                               "relation": {"table": "WeaponAmmunitions", **relation}}, "ammunition")
        counts = Counter(row["mapping_status"] for row in result["references"])
        result["mapping_counts"] = dict(counts)
        result["mapping_status"] = "partial" if result["issues"] or counts["unresolved"] or counts["event_not_indexed"] else "confirmed"
    result = {"batch": batch.name, "static_batch": static.name, "models": models,
              "scope": "载具运动、死亡与选用武器弹药事件；AudioCommands语音不导出",
              "source_sha256": {"model_manifest": digest(static / "美军模型库/模型清单.json"),
                                 "assembly": digest(static / "装配映射.json"), "sfx": sfx["sha256"]}}
    save(batch / "型号音效映射.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    result = build(local_batch(args.batch))
    print("型号", len(result["models"]), "映射状态", dict(Counter(row["mapping_status"] for row in result["models"])))
    refs = [ref for model in result["models"] for ref in model["references"]]
    print("引用状态", dict(Counter(row["mapping_status"] for row in refs)), "唯一事件", len({row["event_path"] for row in refs if row["mapping_status"] == "confirmed"}))


if __name__ == "__main__":
    main()
