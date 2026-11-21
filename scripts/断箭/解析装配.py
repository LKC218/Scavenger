"""只读核对载具层级、安装点与游戏资源中的装配配置。"""

import argparse
import base64
from bisect import bisect_right
from collections import Counter, OrderedDict
import io
import hashlib
import json
from pathlib import Path
import struct
import time

import lz4.block
import numpy as np
import UnityPy


ROOT = Path(__file__).resolve().parents[2]
NAMES = ("US_M1A2", "US_STRYKER_ICV", "US_M2A3", "US_LAV25")
TABLE_NAMES = ("Units", "Abilities", "UnitAbilities", "Ammunitions", "Armors", "Mobility", "PlaneFlyPresets", "UnitPropulsions", "Countries", "Turrets", "TurretUnits", "Weapons", "TurretWeapons", "WeaponAmmunitions", "SensorUnits", "Sensors", "SquadMembers", "SquadWeapons", "Modifications", "Options", "UnitArmors", "SpecializationAvailabilities", "Specializations", "TransportAvailabilities")


def decode_database(raw):
    """按公开工具给出的格式解码，并以填充、JSON结构和行ID逐层验证。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    pos = (32 + struct.unpack_from("<I", raw, 28)[0] + 3) // 4 * 4
    mode = struct.unpack_from("<I", raw, pos)[0]
    pos += 4
    tables, receipts = {}, []
    for name in TABLE_NAMES:
        length = struct.unpack_from("<I", raw, pos)[0]
        offset = pos + 4
        blob = base64.b64decode(raw[offset:offset + length], validate=True)
        pos = (offset + length + 3) // 4 * 4
        if blob[:8] != b"fhk3s0g3":
            raise ValueError("数据库标记不匹配")
        decryptor = Cipher(algorithms.AES(b"09234237536700238099172758697347"), modes.CBC(blob[8:24])).decryptor()
        padded = decryptor.update(blob[24:]) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        rows = json.loads(plain)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("Id"), int) for row in rows):
            raise ValueError("数据表不是带整数ID的对象数组")
        if len({row["Id"] for row in rows}) != len(rows):
            raise ValueError("数据表存在重复ID")
        tables[name] = rows
        receipts.append({"table": name, "offset": offset, "base64_length": length, "rows": len(rows), "json_sha256": hashlib.sha256(plain).hexdigest()})
    return {"source": "globalgamemanagers.assets", "path_id": 52739, "mode": mode, "raw_sha256": hashlib.sha256(raw).hexdigest(), "format_source": "https://github.com/JohnJinHM/BA-Units/blob/master/docs/EXTRACTION.md", "schema_source": "https://github.com/JohnJinHM/BA-Units/blob/master/tools/table_schema.py", "validation": "24段均通过Base64、标记、AES-CBC、PKCS7、UTF8、JSON及唯一整数Id验证；表序与公开schema一致，并逐表核对字段含义", "trailing_hex": raw[pos:].hex(), "receipts": receipts, "tables": tables}


def configuration_mappings(tables, container_paths):
    """只输出数据库明确关联，不依靠模型名称相似度合并。"""
    turrets = {row["Id"]: row for row in tables["Turrets"]}
    all_paths = set(container_paths)
    result = []
    for unit in tables["Units"]:
        if unit.get("CountryId") != 2 or not unit.get("ModelFileName"):
            continue
        model = unit["ModelFileName"].replace("\\", "/")
        exact = [path for path in all_paths if path.endswith("/" + model + ".prefab")]
        if not exact:
            continue
        turret_units = [row for row in tables["TurretUnits"] if row["UnitId"] == unit["Id"]]
        mods = [row for row in tables["Modifications"] if row["UnitId"] == unit["Id"]]
        mod_ids = {row["Id"] for row in mods}
        options = [row for row in tables["Options"] if row["ModificationId"] in mod_ids]
        turret_ids = {row["TurretId"] for row in turret_units}
        turret_ids.update(value for option in options for key, value in option.items() if key.startswith("Turret") and key.endswith("Id") and value)
        turret_rows = []
        for turret_id in sorted(turret_ids):
            turret = turrets[turret_id]
            file = (turret.get("ModelFileName") or "").replace("\\", "/")
            matches = [path for path in all_paths if file and path.endswith("/Turrets/" + file + ".prefab")]
            turret_rows.append({"turret": turret, "container_paths": matches, "match_method": "ModelFileName在Turrets目录中的完整相对路径后缀精确相等" if matches else "未声明外部模型或尚未找到精确匹配"})
        result.append({"unit": unit, "container_paths": sorted(exact), "turret_units": turret_units, "modifications": mods, "options": options, "turrets": turret_rows})
    return result


def transform_matrix(data):
    x, y, z, w = (data["m_LocalRotation"][key] for key in "xyzw")
    matrix = np.eye(4)
    matrix[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    matrix[:3, :3] *= [data["m_LocalScale"][key] for key in "xyz"]
    matrix[:3, 3] = [data["m_LocalPosition"][key] for key in "xyz"]
    return matrix


def rigid_frame(matrix):
    """去除锚点基向量尺度，保留作者位置和正交旋转，供静态候选核验。"""
    result = np.array(matrix, dtype=float, copy=True)
    left, _, right = np.linalg.svd(result[:3, :3])
    rotation = left @ right
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right
    result[:3, :3] = rotation
    return result


def read_catalog_locations(path):
    """读取二进制目录的正式键与位置表，不以字符串邻近关系推断别名。"""
    raw = path.read_bytes()
    magic, version, keys_offset = struct.unpack_from("<III", raw)
    if magic != 0x0DE38942 or version != 2:
        raise ValueError("目前只验证了 Addressables 二进制目录版本2")

    def offsets(offset):
        if offset == 0xFFFFFFFF:
            return ()
        size = struct.unpack_from("<I", raw, offset - 4)[0]
        if size % 4 or offset + size > len(raw):
            raise ValueError("目录数组范围不合法")
        return struct.unpack_from("<" + "I" * (size // 4), raw, offset)

    def string(encoded, separator=None):
        if encoded == 0xFFFFFFFF:
            return None
        offset = encoded & 0x3FFFFFFF
        if encoded & 0x40000000 and separator:
            parts, visited = [], set()
            while True:
                if offset in visited:
                    raise ValueError("目录动态字符串形成循环")
                visited.add(offset)
                part, next_offset = struct.unpack_from("<II", raw, offset)
                parts.append(string(part))
                if next_offset == 0xFFFFFFFF:
                    break
                offset = next_offset
            return separator.join(parts[::-1])
        size = struct.unpack_from("<I", raw, offset - 4)[0]
        if offset + size > len(raw):
            raise ValueError("目录字符串范围不合法")
        return raw[offset:offset + size].decode("utf-16-le" if encoded & 0x80000000 else "ascii")

    key_locations = offsets(keys_offset)
    location_offsets = sorted({offset for location_list in key_locations[1::2] for offset in offsets(location_list)})
    locations = []
    for offset in location_offsets:
        key, internal, provider = struct.unpack_from("<III", raw, offset)
        locations.append({"primary_key": string(key, "/"), "internal_id": string(internal, "/"), "provider_id": string(provider, "."), "offset": offset})
    return {"source": "BrokenArrow_Data/StreamingAssets/aa/catalog.bin", "sha256": hashlib.sha256(raw).hexdigest(), "version": version, "key_count": len(key_locations) // 2, "location_count": len(locations), "format_source": "https://github.com/nesrak1/AddressablesTools/blob/3dbc8210a10c9ef0592580bb6710b5623fcc8d33/AddressablesTools/Catalog/ResourceLocation.cs", "format_source_commit": "3dbc8210a10c9ef0592580bb6710b5623fcc8d33", "locations": locations}


def create_assembly_plan(env, tables, candidates, catalog=None):
    """将数据库选择与序列化锚点分开记录，给静态场景合并提供矩阵。"""
    cache = {}
    by_id = {name: {row["Id"]: row for row in rows} for name, rows in tables.items()}
    all_paths = list(env.container.keys())
    paths_casefold = {path.casefold(): path for path in all_paths}
    catalog_models = {}
    for location in (catalog or {}).get("locations", []):
        internal = location["internal_id"]
        if internal and internal.casefold() in paths_casefold and internal.lower().endswith(".prefab"):
            catalog_models.setdefault(location["primary_key"].casefold(), []).append(location)
    roots = [row for row in candidates if row["role"] in ("vehicle", "base", "body")]
    conversion = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)

    def metadata(path):
        if path in cache:
            return cache[path]
        info = inspect_prefab(env, path)
        transforms, worlds, relative = {}, {}, {}
        for node in info["nodes"]:
            if not node["transform"]:
                continue
            tid = next(component["id"] for component in node["components"] if component["type"] == "Transform")
            transforms[tid] = node
            parent = node["node"].rsplit("/", 1)[0]
            local = transform_matrix(node["transform"])
            worlds[node["node"]] = worlds.get(parent, np.eye(4)) @ local
            relative[node["node"].split("/", 2)[-1] if node["node"].count("/") > 1 else ""] = node
        root_component = next((mono for mono in info["monobehaviours"] if mono["script_name"] == "UnitPrefabRoot"), None)
        info.update(transforms=transforms, worlds=worlds, relative=relative, root_component=root_component)
        cache[path] = info
        return info

    def describe(node):
        tid = next(component["id"] for component in node["components"] if component["type"] == "Transform")
        return {"transform_id": tid, "path": node["node"], "name": node["node"].rsplit("/", 1)[-1]}

    def resolve_model(model):
        model = model.replace("\\", "/").strip("/")
        locations = catalog_models.get(model.casefold(), [])
        if locations:
            return sorted({paths_casefold[location["internal_id"].casefold()] for location in locations})
        suffixes = ("/US/Turrets/" + model + ".prefab", "/ModelPrefabs/" + model + ".prefab")
        return sorted(path for path in all_paths if any(path.casefold().endswith(suffix.casefold()) for suffix in suffixes))

    def unit_matches(unit, path, stem):
        model = (unit.get("ModelFileName") or "").replace("\\", "/").strip("/")
        if catalog_models.get(model.casefold()):
            return path in resolve_model(model)
        return model.rsplit("/", 1)[-1].casefold() == stem

    plans = []
    for candidate in roots:
        path = candidate["path"]
        root = metadata(path)
        stem = Path(path).stem.casefold()
        units = [unit for unit in tables["Units"] if unit.get("CountryId") == 2 and unit_matches(unit, path, stem)]
        units.sort(key=lambda unit: (bool(unit.get("IsUnitModification")), bool(unit.get("OriginalName")), not unit.get("DisplayInArmory", False), unit["Id"]))
        plan = {"root_path": path, "root_id": candidate["id"], "category": candidate["category"], "role": candidate["role"], "all_unit_ids": [unit["Id"] for unit in units], "selected_unit_id": units[0]["Id"] if units else None, "selection_policy": "优先非IsUnitModification且OriginalName为空、军械库可见，最后按最小Id；不按名称推断配置", "selected_option_ids": [], "selected_options": [], "slot_turrets": [], "sources": [], "warnings": []}
        root_node = root["nodes"][0]
        plan["sources"].append({"path": path, "kind": "root", "source_root_id": candidate["id"], "binding": "identity", "matrix_unity": np.eye(4).tolist(), "matrix_blender": np.eye(4).tolist()})
        if not units:
            plan["status"] = "root_only_no_current_unit"
            plan["warnings"].append("当前数据库没有精确ModelFileName引用；保留根模型，不给它套用名称相似的其他单位配置")
            plans.append(plan)
            continue
        unit = units[0]
        plan["selected_unit"] = {key: unit.get(key) for key in ("Id", "Name", "ModelFileName", "IsUnitModification", "OriginalName", "DisplayInArmory")}
        plan["unit_catalog_locations"] = catalog_models.get((unit.get("ModelFileName") or "").replace("\\", "/").strip("/").casefold(), [])
        slots = {}
        default_conflicts = set()
        relations = [row for row in tables["TurretUnits"] if row["UnitId"] == unit["Id"]]
        for relation in relations:
            turret = by_id["Turrets"][relation["TurretId"]]
            if turret["IsDefault"] and turret.get("ParentTurretId") is None:
                slot = relation["Order"]
                if slot in slots and slots[slot]["turret_id"] != turret["Id"]:
                    default_conflicts.add(slot)
                slots[slot] = {"turret_id": turret["Id"], "source": "TurretUnits关联且Turrets.IsDefault=true", "relation_id": relation["Id"]}
        modifications = sorted((row for row in tables["Modifications"] if row["UnitId"] == unit["Id"]), key=lambda row: (row["Order"], row["Id"]))
        for modification in modifications:
            options = [row for row in tables["Options"] if row["ModificationId"] == modification["Id"]]
            options.sort(key=lambda option: (bool(option.get("ReplaceUnitId")), not option["IsDefault"], option["Order"], option["Id"]))
            if not options:
                continue
            option = options[0]
            chosen = {key: option.get(key) for key in ("Id", "Name", "ModificationId", "IsDefault", "Order", "ArmorId", "MobilityId", "ReplaceUnitId")}
            chosen["selection"] = "数据库IsDefault" if option["IsDefault"] else "基础预设策略：无显式默认时选择最小Order再Id，未宣称为玩家已保存配置"
            plan["selected_options"].append(chosen)
            plan["selected_option_ids"].append(option["Id"])
            if option.get("ReplaceUnitId"):
                plan["warnings"].append(f"选项{option['Id']}替换整个单位为{option['ReplaceUnitId']}，未自动切换本次根模型")
                continue
            for key, value in option.items():
                if key.startswith("Turret") and key.endswith("Id") and key[6:-2].isdigit() and value:
                    slots[int(key[6:-2])] = {"turret_id": value, "source": "Options." + key, "option_id": option["Id"]}
        explicit_turrets = {selection["turret_id"] for selection in slots.values() if "option_id" in selection}
        redundant = [slot for slot, selection in slots.items() if "option_id" not in selection and selection["turret_id"] in explicit_turrets]
        if redundant:
            plan["suppressed_default_relations"] = [{"slot": slot, **slots.pop(slot), "reason": "同一TurretId已经被Options.TurretNId显式指定；TurretUnits.Order回退不重复创建模型"} for slot in redundant]
        for slot in default_conflicts:
            if slot in slots and "option_id" not in slots[slot]:
                plan["warnings"].append(f"槽{slot}存在多个IsDefault炮塔，尚无选项消歧")
        body_pointer = root["root_component"]["data"].get("PrefabBody", {}) if root["root_component"] else {}
        target_body = root["transforms"].get(body_pointer.get("m_PathID"))
        for slot, selection in sorted(slots.items()):
            turret = by_id["Turrets"][selection["turret_id"]]
            row = {"slot": slot, **selection, "name": turret["Name"], "model_file_name": turret["ModelFileName"], "parent_turret_id": turret["ParentTurretId"]}
            plan["slot_turrets"].append(row)
            if not turret["ModelFileName"]:
                row["status"] = "internal_or_empty_no_external_model"
                continue
            matches = resolve_model(turret["ModelFileName"])
            row["container_matches"] = matches
            row["catalog_locations"] = catalog_models.get(turret["ModelFileName"].replace("\\", "/").strip("/").casefold(), [])
            if len(matches) != 1:
                row["status"] = "unresolved_container"
                plan["warnings"].append(f"炮塔{turret['Id']}外部模型精确匹配{len(matches)}项")
                continue
            source_path = matches[0]
            source = metadata(source_path)
            entry = {"path": source_path, "kind": "turret", "slot": slot, "turret_id": turret["Id"], "source_root_id": source["root_id"], "binding": "unresolved", "evidence": {"database_model_file_name": turret["ModelFileName"], "catalog_locations": row["catalog_locations"], "selection": selection}}
            source_body = None
            if target_body:
                relative = target_body["node"].split("/", 2)[-1]
                source_body = source["relative"].get(relative)
            if source_body:
                target_world = root["worlds"][target_body["node"]]
                source_world = source["worlds"][source_body["node"]]
                delta = target_world @ np.linalg.inv(source_world)
                entry.update(binding="align_shared_prefabbody", target_transform_id=describe(target_body)["transform_id"], target_transform_path=target_body["node"], source_anchor=describe(source_body), matrix_unity=delta.tolist(), matrix_blender=(conversion @ delta @ conversion).tolist())
                entry["evidence"].update(target_prefabbody_field=body_pointer, shared_relative_path=relative, target_world=target_world.tolist(), source_world=source_world.tolist(), authored_frames_equal=bool(np.allclose(target_world, source_world, atol=1e-5)))
                target_scale = np.linalg.norm(target_world[:3, :3], axis=0)
                source_scale = np.linalg.norm(source_world[:3, :3], axis=0)
                if not np.allclose(target_scale, source_scale, atol=1e-5) and np.allclose(target_world[:3, 3], source_world[:3, 3], atol=1e-5):
                    rigid_delta = rigid_frame(target_world) @ np.linalg.inv(rigid_frame(source_world))
                    entry["evidence"].update(full_scale_matrix_unity=delta.tolist(), full_scale_matrix_blender=(conversion @ delta @ conversion).tolist(), target_anchor_scale=target_scale.tolist(), source_anchor_scale=source_scale.tolist(), rule="静态网格已经应用各自bindpose且尺寸独立正确；相同PrefabBody位置而骨架尺度不同，使用去尺度刚体帧候选，必须重渲染核验，不声称运行时严格一致")
                    entry.update(binding="align_shared_prefabbody_rigid_candidate", matrix_unity=rigid_delta.tolist(), matrix_blender=(conversion @ rigid_delta @ conversion).tolist(), requires_visual_review=True)
                    plan["warnings"].append("共同PrefabBody存在骨架尺度差异；刚体帧候选保留源静态网格尺寸，需独立视觉复核")
            else:
                anchors = []
                for mono in root["monobehaviours"]:
                    if mono["script_name"] == "UnitPrefabTurretInfo" and mono["data"]["TurretIndex"] == slot:
                        if not any(other["script_name"] == "UnitPrefabTurretInfo" and mono["node"].startswith(other["node"] + "/") for other in root["monobehaviours"]):
                            anchors.append(mono)
                if len(anchors) == 1:
                    anchor = next(node for node in root["nodes"] if node["node"] == anchors[0]["node"])
                    delta = root["worlds"][anchor["node"]] @ np.linalg.inv(source["worlds"][source["nodes"][0]["node"]])
                    entry.update(binding="attach_to_serialized_turret_index", target_transform_id=describe(anchor)["transform_id"], target_transform_path=anchor["node"], matrix_unity=delta.tolist(), matrix_blender=(conversion @ delta @ conversion).tolist())
                    entry["evidence"]["unit_prefab_turret_info"] = anchors[0]["data"]
                else:
                    source_turrets = [mono for mono in source["monobehaviours"] if mono["script_name"] == "UnitPrefabTurretInfo" and mono["data"]["TurretIndex"] == slot]
                    shared_parents = []
                    for source_turret in source_turrets:
                        parent_path = source_turret["node"].rsplit("/", 1)[0]
                        parent_relative = parent_path.split("/", 2)[-1]
                        target_parent = root["relative"].get(parent_relative)
                        source_parent = source["relative"].get(parent_relative)
                        if not source_parent or not target_parent or "/" not in parent_relative:
                            continue
                        target_world = root["worlds"][target_parent["node"]]
                        source_world = source["worlds"][source_parent["node"]]
                        if np.allclose(target_world, source_world, atol=1e-5) and np.allclose(transform_matrix(target_parent["transform"]), transform_matrix(source_parent["transform"]), atol=1e-5):
                            shared_parents.append((source_turret, source_parent, target_parent, parent_relative, source_world, target_world))
                    if len(shared_parents) == 1:
                        source_turret, source_parent, target_parent, parent_relative, source_world, target_world = shared_parents[0]
                        delta = target_world @ np.linalg.inv(source_world)
                        entry.update(binding="align_shared_turret_parent", target_transform_id=describe(target_parent)["transform_id"], target_transform_path=target_parent["node"], source_anchor=describe(source_parent), matrix_unity=delta.tolist(), matrix_blender=(conversion @ delta @ conversion).tolist())
                        entry["evidence"].update(source_unit_prefab_turret_info=source_turret["data"], shared_relative_path=parent_relative, target_world=target_world.tolist(), source_world=source_world.tolist(), authored_frames_equal=True, local_frames_equal=True, rule="源序列化炮塔节点的直接父骨架，在目标中具有相同非空相对路径，且局部及世界作者坐标均相等；仅用于当前静态姿态")
                    else:
                        plan["warnings"].append(f"炮塔{turret['Id']}没有唯一序列化槽位或共同骨架锚点，未猜安装位置")
            row["status"] = entry["binding"]
            if "/Weapon/Bomb_Bays/" in source_path:
                entry.update(static_merge_eligible=False, requires_visual_review=True)
                entry["evidence"]["static_display_verification"] = {"source_unit_prefab_turret_info_count": sum(mono["script_name"] == "UnitPrefabTurretInfo" for mono in source["monobehaviours"]), "weapon_prefab_info_count": sum(mono["script_name"] == "WeaponPrefabInfo" for mono in source["monobehaviours"]), "source_root_transform": source["nodes"][0]["transform"], "conclusion": "数据库及目录只证明弹舱武器资源关联，未发现证明该模板应整体静态外显的字段；按槽位根对齐已视觉失败，保留独立参考，静态可用库采用已验原机体"}
                plan["warnings"].append("弹舱武器模板缺少已验证的静态挂载显示规则；不能仅因ModelFileName有值就整体外显")
            plan["sources"].append(entry)
        plan["status"] = "ready_static_merge" if all(source["binding"] != "unresolved" and not source.get("requires_visual_review") for source in plan["sources"]) and not any(row.get("status") == "unresolved_container" for row in plan["slot_turrets"]) else "partial_requires_review"
        coincident = []
        for index, first in enumerate(plan["sources"][1:], 1):
            for second in plan["sources"][index + 1:]:
                if first["path"] == second["path"] and first.get("target_transform_id") != second.get("target_transform_id") and first.get("matrix_unity") and second.get("matrix_unity") and np.allclose(first["matrix_unity"], second["matrix_unity"], atol=1e-5):
                    coincident.append({"path": first["path"], "slots": [first["slot"], second["slot"]], "target_transform_ids": [first.get("target_transform_id"), second.get("target_transform_id")]})
        if coincident:
            plan["coincident_sources"] = coincident
            plan["status"] = "partial_requires_review"
            plan["warnings"].append("不同序列化槽的同一外部模型产生重合坐标；保留原数据证据，不按发射点猜测静态挂载位置，需核查内置弹舱与运行时显示语义")
        plans.append(plan)
    return {"schema_version": 1, "coordinate_note": "matrix_unity和matrix_blender均将该source完整预制体原始世界坐标变换到目标root坐标；静态合并时对source整体左乘一次，不能对源内各骨架重复加偏移。blender轴序与导出场景.py一致为XZY。", "policy_note": "外部模型选择只来自数据库ID关系与ModelFileName；位置来自UnitPrefabRoot.PrefabBody共同骨架或UnitPrefabTurretInfo序列化槽位。基础选项没有IsDefault时明确按Order选择，不冒充已保存的游戏配装。", "summary": {"roots": len(plans), "status": dict(Counter(plan["status"] for plan in plans)), "external_sources": sum(len(plan["sources"]) - 1 for plan in plans), "binding": dict(Counter(source["binding"] for plan in plans for source in plan["sources"][1:]))}, "roots": plans}


def zstring(stream):
    data = bytearray()
    while True:
        char = stream.read(1)
        if not char:
            raise EOFError("字符串未终止")
        if char == b"\0":
            return data.decode("utf-8")
        data.extend(char)


class BundleIndex:
    """按需解压少量块，避免将主游戏 data.unity3d 整体驻留或复制。"""
    def __init__(self, path):
        self.file = path.open("rb")
        stream = self.file
        signature = zstring(stream)
        version = struct.unpack(">I", stream.read(4))[0]
        player, self.engine = zstring(stream), zstring(stream)
        self.size, compressed, raw, flags = struct.unpack(">QIII", stream.read(20))
        if signature != "UnityFS" or version < 7 or flags & 0x80:
            raise ValueError("仅适配前置索引 UnityFS 7 及以上版本")
        stream.seek((stream.tell() + 15) // 16 * 16)
        info = stream.read(compressed)
        if flags & 63 in (2, 3):
            info = lz4.block.decompress(info, uncompressed_size=raw)
        elif flags & 63:
            raise ValueError("索引压缩格式未适配")
        table = io.BytesIO(info)
        table.read(16)
        self.blocks = [struct.unpack(">IIH", table.read(10)) for _ in range(struct.unpack(">I", table.read(4))[0])]
        self.nodes = []
        for _ in range(struct.unpack(">I", table.read(4))[0]):
            offset, size, node_flags = struct.unpack(">QQI", table.read(20))
            self.nodes.append({"offset": offset, "size": size, "flags": node_flags, "name": zstring(table)})
        if flags & 0x200:
            stream.seek((stream.tell() + 15) // 16 * 16)
        packed, raw_offset = stream.tell(), 0
        self.packed_offsets, self.raw_offsets = [], []
        for raw_size, packed_size, _ in self.blocks:
            self.packed_offsets.append(packed)
            self.raw_offsets.append(raw_offset)
            packed += packed_size
            raw_offset += raw_size
        self.cache = OrderedDict()
        self.blocks_read = 0

    def block(self, index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        raw, packed, flags = self.blocks[index]
        self.file.seek(self.packed_offsets[index])
        data = self.file.read(packed)
        if flags & 63 in (2, 3):
            data = lz4.block.decompress(data, uncompressed_size=raw)
        elif flags & 63:
            raise ValueError("数据块压缩格式未适配")
        if len(data) != raw:
            raise ValueError("数据块长度不符")
        self.cache[index] = data
        if len(self.cache) > 16:
            self.cache.popitem(last=False)
        self.blocks_read += 1
        return data


class NodeStream(io.RawIOBase):
    def __init__(self, bundle, node):
        self.bundle, self.node, self.position = bundle, node, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.node["size"] + offset
        if self.position < 0:
            raise ValueError("负数文件位置")
        return self.position

    def read(self, size=-1):
        size = max(0, self.node["size"] - self.position) if size < 0 else min(size, max(0, self.node["size"] - self.position))
        end = self.node["offset"] + self.position + size
        position = self.node["offset"] + self.position
        parts = []
        while position < end:
            index = bisect_right(self.bundle.raw_offsets, position) - 1
            data = self.bundle.block(index)
            start = position - self.bundle.raw_offsets[index]
            part = data[start:start + min(end - position, len(data) - start)]
            if not part:
                raise EOFError("资源块提前结束")
            parts.append(part)
            position += len(part)
        self.position += size
        return b"".join(parts)


def pointer_refs(value, prefix=""):
    if isinstance(value, dict):
        if "m_PathID" in value and "m_FileID" in value:
            yield prefix, value
        else:
            for key, item in value.items():
                yield from pointer_refs(item, prefix + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from pointer_refs(item, prefix + f"[{index}]")


def inspect_prefab(env, path):
    obj = env.container[path].deref()
    af = obj.assets_file
    reverse = {ptr.path_id: key for key, ptr in env.container.items()}
    nodes, monos, meshes, links = [], [], [], []

    def walk(go_id, parent):
        go = af.objects[go_id].read_typetree()
        node_path = parent + "/" + go["m_Name"]
        components = []
        transform = None
        for component in go["m_Component"]:
            reader = af.objects[component["component"]["m_PathID"]]
            data = reader.read_typetree()
            components.append({"id": reader.path_id, "type": reader.type.name})
            if reader.type.name == "Transform":
                transform = data
            if reader.type.name in ("MeshFilter", "SkinnedMeshRenderer") and data.get("m_Mesh", {}).get("m_PathID"):
                ptr = data["m_Mesh"]
                try:
                    mesh = UnityPy.classes.PPtr(**ptr, assetsfile=af).deref()
                    meshes.append({"node": node_path, "mesh_id": mesh.path_id, "mesh_name": mesh.peek_name(), "component": reader.type.name})
                except FileNotFoundError as error:
                    meshes.append({"node": node_path, "pointer": ptr, "component": reader.type.name, "unresolved_external": str(error)})
            if reader.type.name == "MonoBehaviour":
                script = data.get("m_Script", {})
                try:
                    script_name = UnityPy.classes.PPtr(**script, assetsfile=af).deref().read_typetree().get("m_ClassName")
                except Exception:
                    script_name = None
                entry = {"node": node_path, "id": reader.path_id, "script": script, "script_name": script_name, "data": {key: val for key, val in data.items() if key != "Skins"}}
                monos.append(entry)
                for field, ptr in pointer_refs(entry["data"]):
                    if ptr["m_FileID"] == 0 and ptr["m_PathID"] in reverse and ptr["m_PathID"] != go_id:
                        links.append({"node": node_path, "field": field, "target": reverse[ptr["m_PathID"]], "pointer": ptr})
        nodes.append({"node": node_path, "gameobject_id": go_id, "active": go["m_IsActive"], "components": components, "transform": transform})
        if transform:
            for child in transform["m_Children"]:
                tr = af.objects[child["m_PathID"]].read_typetree()
                walk(tr["m_GameObject"]["m_PathID"], node_path)

    walk(obj.path_id, "")
    return {"path": path, "root_id": obj.path_id, "node_count": len(nodes), "mesh_instances": meshes, "monobehaviours": monos, "container_references": links, "nodes": nodes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--数据索引", action="store_true")
    parser.add_argument("--装配计划", action="store_true")
    parser.add_argument("--batch", default="BA-26273833-001")
    args = parser.parse_args()
    start = time.time()
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
    game = Path(config["game_directory"])
    batch_root = (ROOT / "local-data/断箭/批次").resolve()
    batch = (batch_root / args.batch).resolve()
    if not batch.is_relative_to(batch_root) or batch == batch_root:
        raise ValueError("批次超出项目本地目录")
    cache = batch / "解析缓存"
    env = UnityPy.Environment()
    env.load_file(str(next(path for path in cache.glob("CAB-*") if path.suffix != ".resS")))
    script_bundle = next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("*_monoscripts_*.bundle"))
    env.load_file(str(script_bundle))
    names = list(NAMES)
    paths = ["Assets/Resources_moved/ModelPrefabs/US/" + name + ".prefab" for name in names]
    paths.extend(path for path in env.container if "/US/Turrets/" in path and any(group in path for group in ("/US_Stryker_ICV/", "/US_M2A3_New/", "/US_LAV_AT/")))
    result = {"method": "UnityPy文件流读取缓存；只读层级和元数据，不解码网格顶点，不写回游戏", "prefabs": [inspect_prefab(env, path) for path in paths]}
    if args.数据索引:
        bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
        result["data_bundle"] = {"engine": bundle.engine, "nodes": bundle.nodes, "assets": []}
        for node in bundle.nodes:
            if node["name"] not in ("globalgamemanagers.assets", "resources.assets"):
                continue
            af = env.load_file(NodeStream(bundle, node), name=node["name"])
            assets = []
            for obj in af.objects.values():
                if obj.type.name in ("TextAsset", "MonoBehaviour", "ResourceManager"):
                    name = obj.peek_name()
                    entry = {"id": obj.path_id, "type": obj.type.name, "name": name, "byte_size": obj.byte_size}
                    if obj.type.name == "ResourceManager":
                        entry["data"] = obj.read_typetree()
                    assets.append(entry)
            result["data_bundle"]["assets"].append({"node": node["name"], "object_count": len(af.objects), "types": dict(Counter(obj.type.name for obj in af.objects.values())), "entries": assets})
            if node["name"] == "globalgamemanagers.assets":
                result["database"] = decode_database(af.objects[52739].get_raw_data())
                profile = af.objects[52735].get_raw_data()
                result["database"]["default_profile_evidence"] = {"path_id": 52735, "name": "DefaultConfigProfile", "raw_hex": profile.hex(), "contains_database_pointer": struct.pack("<iq", 0, 52739) in profile}
                result["configuration_mappings"] = configuration_mappings(result["database"]["tables"], env.container.keys())
        result["data_bundle"]["blocks_read"] = bundle.blocks_read
        bundle.file.close()
    output = ROOT / "tmp/断箭/地面装配核验.json"
    if args.装配计划:
        database = result.get("database")
        evidence = batch / "证据/装配/源配置核验.json"
        if database is None:
            if not evidence.exists():
                raise ValueError("本批次缺少源配置核验证据，请先同时使用--数据索引 --装配计划从游戏资源重建")
            database = json.loads(evidence.read_text(encoding="utf-8"))["database"]
        candidate_path = batch / "美军载具候选.json"
        if not candidate_path.exists():
            candidate_path = ROOT / "tmp/断箭/美军载具候选.json"
        candidates = json.loads(candidate_path.read_text(encoding="utf-8"))["candidates"]
        catalog = read_catalog_locations(game / "BrokenArrow_Data/StreamingAssets/aa/catalog.bin")
        plan = create_assembly_plan(env, database["tables"], candidates, catalog)
        plan["catalog_provenance"] = {key: value for key, value in catalog.items() if key != "locations"}
        plan["database_provenance"] = {key: database[key] for key in ("source", "path_id", "raw_sha256", "format_source", "schema_source", "validation")}
        batch.mkdir(parents=True, exist_ok=True)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({"database": database, "catalog_provenance": plan["catalog_provenance"], "candidate_source": str(candidate_path.relative_to(ROOT)), "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "rebuild": "使用--batch指定批次；--数据索引 --装配计划可从只读游戏资源重新解码数据库并建立计划"}, ensure_ascii=False, indent=2), encoding="utf-8")
        conclusion = {"summary": plan["summary"], "coordinate_note": plan["coordinate_note"], "policy_note": plan["policy_note"], "sources": {"database": plan["database_provenance"], "catalog": plan["catalog_provenance"]}, "selection": "root通过catalog primary_key与internal_id正式位置关系匹配Units.ModelFileName；缺目录映射时才使用原容器精确后缀。保留all_unit_ids；优先基础非clone再最小Id。选项优先IsDefault，未声明时按最小Order/Id作基础预设。", "bindings": {"align_shared_prefabbody": "根PrefabBody指针指定锚点，源相同相对骨架路径，保留两个作者帧及变换", "attach_to_serialized_turret_index": "数据库Options.TurretNId槽N对应唯一根UnitPrefabTurretInfo.TurretIndex", "align_shared_turret_parent": "序列化源炮塔的直接父骨架在目标有相同非空相对路径，局部及世界作者矩阵同时相等", "align_shared_prefabbody_rigid_candidate": "源静态网格已应用bindpose；同位骨架尺度不同的刚体帧候选，须视觉复核"}, "limits": "资源关联和作者锚点不能独立证明运行时装配与显隐规则；候选及异常根必须以实际视觉复核结果为准，未证明的弹舱模板保留独立参考。", "exceptions": [{"root_path": row["root_path"], "status": row["status"], "warnings": row["warnings"], "coincident_sources": row.get("coincident_sources", [])} for row in plan["roots"] if row["status"] != "ready_static_merge"], "rebuild": "local-data/tools/python-unity/Scripts/python.exe scripts/断箭/解析装配.py --batch 批次名 --数据索引 --装配计划"}
        (evidence.parent / "装配核验结论.json").write_text(json.dumps(conclusion, ensure_ascii=False, indent=2), encoding="utf-8")
        (batch / "装配映射.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        result["assembly_plan"] = plan
        print(json.dumps(plan["summary"], ensure_ascii=False))
    result["elapsed_seconds"] = time.time() - start
    if args.数据索引 or not args.装配计划:
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif args.装配计划:
        previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else result
        previous["assembly_plan"] = result["assembly_plan"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "prefab_count": len(result["prefabs"]), "seconds": result["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
