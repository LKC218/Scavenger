"""V0 阶段：基线冻结、只读恢复 129 份高模原件缓存、全库节点语义分析与贴图去重盘点。"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import shutil
import struct
import subprocess
import time
from pathlib import Path

from 公共配置 import (
    BATCH_ID,
    ORIGINALS_CACHE_DIR,
    PARTIAL_IDS,
    ROOT,
    ROTORCRAFT_IDS,
    SEVEN_ZIP_EXE,
    TMP_DIR,
    UAV_IDS,
    V0_DIR,
    V1_SAMPLE_IDS,
    fingerprint,
    load_formal_vehicles,
    now_iso,
    safe_slug,
    save_json,
    sha256_bytes,
    sha256_file,
    vehicle_short_id,
)


def parse_glb_json_and_bin(path: Path) -> tuple[dict, memoryview]:
    raw = path.read_bytes()
    magic, version, length, jlen, ctype = struct.unpack_from("<4sIIII", raw, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError(f"非法 GLB 文件头: {path}")
    gltf = json.loads(raw[20 : 20 + jlen].decode("utf-8"))
    bin_off = 20 + jlen
    bin_view = memoryview(b"")
    if bin_off + 8 <= len(raw):
        blen, btype = struct.unpack_from("<II", raw, bin_off)
        if btype == 0x004E4942:
            bin_view = memoryview(raw)[bin_off + 8 : bin_off + 8 + blen]
    return gltf, bin_view


def classify_vehicle_role(vid: str, category: str) -> str:
    if category == "ground":
        return "ground"
    if vid in ROTORCRAFT_IDS:
        return "rotorcraft"
    if vid in UAV_IDS:
        return "uav"
    return "fixed_wing"


def infer_slot_from_ancestors(role: str, ancestor_names: list[str]) -> str:
    """基于自顶向下祖先链语义判断部件逻辑槽位。"""
    joined = " / ".join(ancestor_names).lower()
    if role == "ground":
        # 优先判炮管/发射管，其次炮塔，其次雷达，最后车体
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("barrel", "cannon", "gun_tube", "gunbarrel", "main_gun", "gun_0", "gun1")):
                return "barrel"
            if any(k in low for k in ("gun", "launcher", "mortar", "tube")) and "turret" not in low and "gmv" not in low:
                # 排除整车名含 gun，细判在炮塔下或显式武器节点
                if any("turret" in a.lower() for a in ancestor_names) or "launcher" in low:
                    return "barrel"
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("radar", "dish", "antenna_rot")):
                return "radar"
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("turret", "tower", "cupola", "pod_rot", "crows", "rws")):
                return "turret"
        return "hull"

    if role == "rotorcraft":
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("tail_rotor", "tailrotor", "rotor_tail", "rear_rotor", "tail_blade", "back_rotor")):
                return "tail_rotor"
            if ("tail" in low or "rear" in low or "back" in low) and any(k in low for k in ("rotor", "blade", "prop", "vint")):
                return "tail_rotor"
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("main_rotor", "mainrotor", "rotor", "blade", "propeller", "vint", "proprotor")):
                return "main_rotor"
        for nm in reversed(ancestor_names):
            low = nm.lower()
            if any(k in low for k in ("turret", "gun", "cannon", "chin")):
                return "gun"
        return "hull"

    # 固定翼与无人机：默认整机合批为 hull（保留螺旋桨可选识别）
    return "hull"


def analyze_glb_structure(path: Path, vid: str, role: str) -> dict:
    gltf, bin_view = parse_glb_json_and_bin(path)
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    materials = gltf.get("materials", [])
    textures = gltf.get("textures", [])
    images = gltf.get("images", [])
    bvs = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])

    # 构建父映射与场景根节点
    parent_of = {}
    for idx, n in enumerate(nodes):
        for ch in n.get("children", []):
            parent_of[ch] = idx

    def get_ancestors(nidx: int) -> list[int]:
        chain = []
        cur = nidx
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parent_of.get(cur)
        chain.reverse()
        return chain

    # 计算三角形与节点分布
    total_tris = 0
    total_prims = 0
    slot_mesh_nodes = collections.defaultdict(list)
    slot_tris = collections.Counter()
    empty_nodes = 0
    mesh_nodes = 0

    for nidx, n in enumerate(nodes):
        if "mesh" not in n:
            empty_nodes += 1
            continue
        mesh_nodes += 1
        m = meshes[n["mesh"]]
        tris_n = 0
        for pr in m.get("primitives", []):
            total_prims += 1
            if "indices" in pr:
                tris_n += accessors[pr["indices"]]["count"] // 3
            else:
                pos_acc = pr.get("attributes", {}).get("POSITION")
                if pos_acc is not None:
                    tris_n += accessors[pos_acc]["count"] // 3
        total_tris += tris_n
        chain = get_ancestors(nidx)
        chain_names = [nodes[i].get("name", f"node_{i}") for i in chain]
        slot = infer_slot_from_ancestors(role, chain_names)
        slot_mesh_nodes[slot].append({"node_index": nidx, "name": n.get("name", ""), "ancestors": chain_names, "tris": tris_n})
        slot_tris[slot] += tris_n

    # 识别图像用途（baseColor / normal / metallicRoughness / occlusion / emissive）
    img_usages = collections.defaultdict(set)
    for m in materials:
        pbr = m.get("pbrMetallicRoughness", {})
        for slot_name, tex_ref in [
            ("baseColor", pbr.get("baseColorTexture")),
            ("metallicRoughness", pbr.get("metallicRoughnessTexture")),
            ("normal", m.get("normalTexture")),
            ("occlusion", m.get("occlusionTexture")),
            ("emissive", m.get("emissiveTexture")),
        ]:
            if tex_ref and "index" in tex_ref:
                t = textures[tex_ref["index"]]
                src = t.get("source")
                if src is None and "extensions" in t:
                    src = t["extensions"].get("KHR_texture_basisu", {}).get("source")
                if src is not None:
                    img_usages[src].add(slot_name)

    img_records = []
    for iidx, im in enumerate(images):
        if "bufferView" not in im:
            continue
        bv = bvs[im["bufferView"]]
        bo = bv.get("byteOffset", 0)
        bl = bv["byteLength"]
        data = bytes(bin_view[bo : bo + bl])
        img_records.append({
            "index": iidx,
            "mime": im.get("mimeType", ""),
            "bytes": bl,
            "sha256": sha256_bytes(data),
            "usages": sorted(img_usages.get(iidx, ["unknown"])),
        })

    # 统计材质特性
    has_normal = any("normalTexture" in m for m in materials)
    has_mr = any("metallicRoughnessTexture" in m.get("pbrMetallicRoughness", {}) for m in materials)
    transparent_mats = sum(1 for m in materials if m.get("alphaMode") == "BLEND")
    mask_mats = sum(1 for m in materials if m.get("alphaMode") == "MASK")

    return {
        "bytes": path.stat().st_size,
        "nodes": len(nodes),
        "mesh_nodes": mesh_nodes,
        "empty_nodes": empty_nodes,
        "meshes": len(meshes),
        "primitives": total_prims,
        "materials": len(materials),
        "triangles": total_tris,
        "has_normal": has_normal,
        "has_mr": has_mr,
        "transparent_mats": transparent_mats,
        "mask_mats": mask_mats,
        "slot_tris": dict(slot_tris),
        "slot_counts": {k: len(v) for k, v in slot_mesh_nodes.items()},
        "images": img_records,
    }


def batch_extract_originals(vehicles: list[dict], originals: dict) -> dict[str, Path]:
    """一次性批量解包 129 款高模原件到临时缓存目录，逐文件核验 SHA256（避免固实 7z 重复解包）。"""
    ORIGINALS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result_paths: dict[str, Path] = {}
    need_by_archive: dict[Path, list[tuple[str, str, str, Path]]] = collections.defaultdict(list)

    for v in vehicles:
        vid = v["id"]
        ssha = v["source_sha256"]
        if ssha not in originals:
            raise KeyError(f"原件映射缺少 {vid} 的 source_sha256: {ssha}")
        ent = originals[ssha]
        arc_path = (ROOT / ent["archive"]["path"]).resolve()
        target = ORIGINALS_CACHE_DIR / f"{safe_slug(vid)}__{ssha[:12]}.glb"
        if target.is_file() and sha256_file(target) == ssha:
            result_paths[vid] = target
        else:
            need_by_archive[arc_path].append((vid, ssha, ent["entry"], target))

    if need_by_archive:
        staging = TMP_DIR / "extract_staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        for arc_path, items in need_by_archive.items():
            # 使用文件列表一次性解包所需成员，充分利用固实 7z 顺序流
            unique_entries = sorted({it[2] for it in items})
            list_file = staging / "entries.txt"
            list_file.write_text("\n".join(unique_entries), encoding="utf-8")
            print(f"[V0] 一次性从 {arc_path.name} 恢复 {len(unique_entries)} 份高模原件...")
            t0 = time.perf_counter()
            subprocess.run(
                [str(SEVEN_ZIP_EXE), "x", "-y", f"-o{staging}", str(arc_path), f"@{list_file}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            print(f"[V0] 解包完成，耗时 {time.perf_counter() - t0:.2f}s，正在逐文件核验 SHA256...")
            for vid, ssha, entry_rel, target in items:
                extracted = staging / entry_rel
                if not extracted.is_file():
                    raise FileNotFoundError(f"解包结果缺失: {extracted}")
                actual_sha = sha256_file(extracted)
                if actual_sha != ssha:
                    raise ValueError(f"原件 SHA256 不匹配 {vid}: 期望 {ssha}，实得 {actual_sha}")
                shutil.move(str(extracted), str(target))
                result_paths[vid] = target
        shutil.rmtree(staging, ignore_errors=True)
    else:
        print(f"[V0] 全部 {len(result_paths)} 份高模原件缓存已命中且 SHA256 校验通过。")

    return result_paths


def main() -> None:
    t_start = time.perf_counter()
    V0_DIR.mkdir(parents=True, exist_ok=True)
    pointer, vehicles, originals, base = load_formal_vehicles()
    assert len(vehicles) == 129, f"期望 129 款载具，实得 {len(vehicles)}"

    print(f"[V0] 正式资料库 revision={pointer['revision']}，共定位 {len(vehicles)} 款载具。")
    orig_paths = batch_extract_originals(vehicles, originals)

    # 黄金基准（来自现行 src/战术演训/场景.mjs 已验证坐标）
    golden_overrides = {
        "model:US_M1A2": {
            "golden": True,
            "pivots_override": {
                "turret": [0.0, 1.53, -0.23],
                "barrel": [0.0, 1.83, -1.5],
            },
            "note": "M1A2 战术演训黄金基准轴心",
        },
        "model:US_AH64D": {
            "golden": True,
            "pivots_override": {
                "main_rotor": [0.0, 3.772, 0.0],
                "tail_rotor": [-0.64, 3.02, 8.98],
                "gun": [0.0, 1.097, -2.804],
            },
            "note": "AH-64D 战术演训黄金基准轴心",
        },
        "model:US_A10": {
            "golden": True,
            "pivots_override": {},
            "note": "A-10C 固定翼黄金基准（整机合批）",
        },
    }

    vehicle_entries = []
    rig_rules_vehicles = {}
    global_img_refs = collections.defaultdict(list)
    global_img_meta = {}

    normal_veh_count = 0
    mr_veh_count = 0
    total_orig_bytes = 0
    total_orig_tris = 0
    total_curr_256_bytes = 0
    role_counter = collections.Counter()
    slot_summary = collections.Counter()

    for idx, v in enumerate(vehicles):
        vid = v["id"]
        role = classify_vehicle_role(vid, v["category"])
        role_counter[role] += 1
        orig_p = orig_paths[vid]
        curr_256_p = Path(v["local_paths"]["model_url"])
        total_curr_256_bytes += curr_256_p.stat().st_size

        st = analyze_glb_structure(orig_p, vid, role)
        total_orig_bytes += st["bytes"]
        total_orig_tris += st["triangles"]
        if st["has_normal"]:
            normal_veh_count += 1
        if st["has_mr"]:
            mr_veh_count += 1

        for slot_name in st["slot_tris"]:
            slot_summary[f"{role}:{slot_name}"] += 1

        for im in st["images"]:
            h = im["sha256"]
            global_img_refs[h].append({"vehicle_id": vid, "usages": im["usages"]})
            if h not in global_img_meta:
                global_img_meta[h] = {"bytes": im["bytes"], "mime": im["mime"], "usages": set(im["usages"])}
            else:
                global_img_meta[h]["usages"].update(im["usages"])

        # 按原始面数与类别设定 LOD0 目标预算档位（小型/主力/重型）
        orig_t = st["triangles"]
        if role == "uav" or orig_t <= 18000:
            size_tier = "small"
            lod0_target = min(orig_t, 9500)
            lod1_target = min(lod0_target, 2400)
            lod2_target = 260
            atlas_max = 512
        elif orig_t >= 85000 or any(k in vid for k in ("C130", "AC130", "C17", "B52", "B1B", "B2", "CH53", "Ch47", "MH47", "M270")):
            size_tier = "heavy"
            lod0_target = min(orig_t, 22000)
            lod1_target = min(lod0_target, 3600)
            lod2_target = 360
            atlas_max = 1024
        else:
            size_tier = "standard"
            lod0_target = min(orig_t, 16500)
            lod1_target = min(lod0_target, 3000)
            lod2_target = 300
            atlas_max = 1024

        entry = {
            "index": idx,
            "id": vid,
            "short_id": vehicle_short_id(vid),
            "slug": safe_slug(vid),
            "name": v["name"],
            "category": v["category"],
            "role": role,
            "size_tier": size_tier,
            "variant": v.get("variant", ""),
            "quality_status": "partial" if vid in PARTIAL_IDS else v.get("quality_status", "passed"),
            "is_partial": vid in PARTIAL_IDS,
            "is_v1_sample": vid in V1_SAMPLE_IDS,
            "source_sha256": v["source_sha256"],
            "original_cache_path": orig_p.relative_to(ROOT).as_posix(),
            "current_256_path": curr_256_p.relative_to(ROOT).as_posix(),
            "current_256_bytes": curr_256_p.stat().st_size,
            "current_256_tris": v.get("geometry", {}).get("triangles", 0),
            "original_stats": {k: val for k, val in st.items() if k != "images"},
            "original_image_count": len(st["images"]),
        }
        vehicle_entries.append(entry)

        ov = golden_overrides.get(vid, {})
        rig_rules_vehicles[vid] = {
            "role": role,
            "size_tier": size_tier,
            "lod0_target_tris": lod0_target,
            "lod1_target_tris": lod1_target,
            "lod2_target_tris": lod2_target,
            "atlas_max_size": atlas_max,
            "normal_max_size": 512,
            "detected_slots": sorted(st["slot_tris"].keys()),
            "slot_tris": st["slot_tris"],
            "golden": ov.get("golden", False),
            "pivots_override": ov.get("pivots_override", {}),
            "note": ov.get("note", ""),
        }

    # 统计跨车共享原图（被 >=2 辆车引用的贴图进入共享贴图池）
    shared_images = {}
    unique_total_img_bytes = 0
    total_img_entries = 0
    shared_ref_entries = 0
    for h, refs in global_img_refs.items():
        meta = global_img_meta[h]
        unique_total_img_bytes += meta["bytes"]
        total_img_entries += len(refs)
        veh_set = sorted({r["vehicle_id"] for r in refs})
        if len(veh_set) >= 2:
            shared_ref_entries += len(refs)
            shared_images[h] = {
                "sha256": h,
                "bytes": meta["bytes"],
                "mime": meta["mime"],
                "usages": sorted(meta["usages"]),
                "vehicle_count": len(veh_set),
                "vehicles": veh_set,
            }

    pointer_file = ROOT / "local-data/资源库/当前版本.json"
    baseline_doc = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "created_at": now_iso(),
        "elapsed_sec": round(time.perf_counter() - t_start, 2),
        "formal_library_revision": pointer["revision"],
        "formal_pointer_fingerprint": fingerprint(pointer_file),
        "archive_model_7z_fingerprint": fingerprint(ROOT / "local-data/资源库/原始归档/模型.7z"),
        "summary": {
            "vehicle_count": len(vehicles),
            "roles": dict(role_counter),
            "partial_count": sum(1 for e in vehicle_entries if e["is_partial"]),
            "v1_sample_count": sum(1 for e in vehicle_entries if e["is_v1_sample"]),
            "original_total_bytes": total_orig_bytes,
            "original_total_mib": round(total_orig_bytes / 1048576, 3),
            "original_total_triangles": total_orig_tris,
            "current_256_total_bytes": total_curr_256_bytes,
            "current_256_total_mib": round(total_curr_256_bytes / 1048576, 3),
            "vehicles_with_normal_map": normal_veh_count,
            "vehicles_with_mr_map": mr_veh_count,
            "original_total_image_entries": total_img_entries,
            "original_unique_images": len(global_img_refs),
            "original_unique_image_mib": round(unique_total_img_bytes / 1048576, 3),
            "shared_image_hashes": len(shared_images),
            "shared_image_ref_entries": shared_ref_entries,
            "slot_coverage": dict(slot_summary),
        },
        "vehicles": vehicle_entries,
    }

    rules_doc = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "created_at": now_iso(),
        "slot_semantics": {
            "ground": ["hull", "turret", "barrel", "radar"],
            "rotorcraft": ["hull", "main_rotor", "tail_rotor", "gun"],
            "fixed_wing": ["hull"],
            "uav": ["hull"],
        },
        "visual_thresholds": {
            "lod0_iou_min": 0.965,
            "lod1_iou_min": 0.915,
            "lod2_iou_min": 0.800,
            "lod0_fg_ssim_min": 0.920,
            "cross_lod_color_drift_max": 0.050,
            "black_anomaly_max": 0.005,
        },
        "vehicles": rig_rules_vehicles,
    }

    save_json(V0_DIR / "基线凭证.json", baseline_doc)
    save_json(V0_DIR / "vehicle-rig-rules.json", rules_doc)
    save_json(
        V0_DIR / "全库贴图哈希表.json",
        {
            "schema_version": 1,
            "created_at": now_iso(),
            "shared_count": len(shared_images),
            "shared_images": shared_images,
        },
    )

    s = baseline_doc["summary"]
    print(f"[V0] 完成！耗时 {baseline_doc['elapsed_sec']}s")
    print(f"     角色分布: {s['roles']} | partial={s['partial_count']} | 带法线={s['vehicles_with_normal_map']}/129 | 带MR={s['vehicles_with_mr_map']}/129")
    print(f"     原始高模总量: {s['original_total_mib']} MiB ({s['original_total_triangles']:,} 面) | 现行256默认: {s['current_256_total_mib']} MiB")
    print(f"     原始贴图: 总条目={s['original_total_image_entries']} | 唯一={s['original_unique_images']} ({s['original_unique_image_mib']} MiB) | 跨车共享哈希={s['shared_image_hashes']} (覆盖 {s['shared_image_ref_entries']} 条引用)")
    print(f"     槽位覆盖: {s['slot_coverage']}")


if __name__ == "__main__":
    main()
