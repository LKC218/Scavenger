"""B0阶段：核对只读基线身份、冻结第零节签批参数、锁定源对照摘要与独立端口（8820/8821）。"""
from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B0_DIR = WORK_DIR / "B0-基线冻结"
TASK_NAME = "程序化大地图重建-路线B-V001-fcf46de3"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def check_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, False)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> None:
    B0_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 核对正式资源库 revision 7
    lib_ver_path = ROOT / "local-data/资源库/当前版本.json"
    lib_ver = json.loads(lib_ver_path.read_text("utf-8"))
    if lib_ver.get("revision") != 7:
        raise RuntimeError(f"正式资源库版本不是 revision 7: {lib_ver}")

    # 2. 核对只读源输入与运行接入 r14 清单
    r14_manifest_path = BATCH_DIR / "运行接入/r14/地图运行.json"
    r14_fp = fingerprint(r14_manifest_path)
    if r14_fp["sha256"] != "f34220a03f2c104e43675c426acb572f89708454b4c62fba90804786a599b3f9":
        raise RuntimeError("运行接入 r14 地图运行.json 摘要不匹配")

    scope_path = BATCH_DIR / "范围冻结/V001-r02/范围与条目.json"
    scope_fp = fingerprint(scope_path)
    if scope_fp["sha256"] != "9e9431aa5d9f8d24b64687bee80f57c79d1bc3ede545bd45c9edbb7a505614cf":
        raise RuntimeError("范围冻结 V001-r02 摘要不匹配")

    direct_surface_path = BATCH_DIR / "表面来源/full-r01/直接表面.json"
    surface_chunks_path = BATCH_DIR / "全图分块/r02/地表索引.json"
    collision_index_path = BATCH_DIR / "碰撞分页/r01/碰撞索引.json"
    terrain_atlas_path = BATCH_DIR / "地表派生/r03/地表图集.json"
    terrain_params_path = BATCH_DIR / "地表派生/r03/输入地表参数.json"
    coarse_height_path = BATCH_DIR / "粗层/r01/粗层索引.json"
    nav_graph_path = BATCH_DIR / "通行粗层/r01/分区通行.json"
    water_receipt_path = BATCH_DIR / "水面运行/r02/水面核验.json"
    bg_receipt_path = BATCH_DIR / "背景运行/r01/背景核验.json"
    load_freeze_path = BATCH_DIR / "负载冻结/r01/负载与位置.json"
    prototypes_source_path = BATCH_DIR / "来源/全量原型来源.json"
    three_pin_path = ROOT / "local-data/tools/three/当前版本.json"

    direct_surface = json.loads(direct_surface_path.read_text("utf-8"))
    r14_manifest = json.loads(r14_manifest_path.read_text("utf-8"))
    scope_data = json.loads(scope_path.read_text("utf-8"))

    # 统计源地形、道路、装饰、水面、林冠与原型实例基线
    part_categories = {}
    for part in direct_surface["parts"]:
        cat = part["category"]
        part_categories[cat] = part_categories.get(cat, 0) + 1

    layers_dir = ROOT / "local-data/断箭/批次/BA-26273833-010/原始贴图/地表数组"
    layer_textures = {
        f"底色-{i}.png": fingerprint(layers_dir / f"底色-{i}.png")
        for i in range(12)
    }

    # 3. 检查 8820 / 8821 独立端口可用性（绝不触碰 8767/8768/8769/8780/8781/8784/8811-8814）
    port_8820_free = check_port_available(8820)
    port_8821_free = check_port_available(8821)
    if not port_8820_free or not port_8821_free:
        raise RuntimeError(f"独立候选端口被占用: 8820={port_8820_free}, 8821={port_8821_free}")

    # 4. 冻结第零节签批参数与源基准摘要
    baseline_receipt = {
        "schema": "scavenger-procedural-map-b0-baseline-v1",
        "task": TASK_NAME,
        "phase": "B0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_switches": {
            "g01_relationship": "independent_parallel_pre_research",
            "asset_strategy": "hybrid_procedural_terrain_grass_canopy_and_modular_glb_kit_with_replacement_slots",
            "editing_capability": "blender_incremental_cli_plus_browser_editor_mode_with_auto_collision_nav_altitude",
            "visual_style_and_resolution": {
                "style": "tactical_miniature_realistic_ebbtide_wargame",
                "primary_resolution": {"css": [1280, 720], "internal": [640, 364], "dpr": 1},
                "high_res_regression": {"css": [1920, 1080], "internal": [1920, 1080], "dpr": 1},
            },
            "distribution_and_network": {
                "dev_mode": "scatter_files_hot_reload",
                "release_mode": "pmtiles_style_scvmap_range_archives_max_3_files_index_le_128kb",
                "cold_start_20mbps_80ms_max_s": 6.0,
                "warm_start_20mbps_80ms_max_s": 2.0,
                "cold_start_map_max_mib": 6.0,
                "cold_start_total_max_mib": 12.0,
                "full_map_archive_max_mib": 90.0,
            },
            "structure_and_destruction": "static_indestructible_with_3d_multilayer_bridge_support_patches",
        },
        "isolated_workspace": {
            "source_module": "src/程序化地图",
            "scripts_module": "scripts/程序化地图",
            "local_batch_dir": WORK_DIR.relative_to(ROOT).as_posix(),
            "primary_port": 8820,
            "secondary_port": 8821,
            "protected_existing_ports": [8767, 8768, 8769, 8780, 8781, 8784, 8811, 8812, 8813, 8814],
        },
        "source_baseline_fingerprints": {
            "asset_library_pointer": fingerprint(lib_ver_path),
            "r14_runtime_manifest": r14_fp,
            "scope_freeze": scope_fp,
            "direct_surfaces": fingerprint(direct_surface_path),
            "surface_chunks_index": fingerprint(surface_chunks_path),
            "collision_index": fingerprint(collision_index_path),
            "terrain_atlas": fingerprint(terrain_atlas_path),
            "terrain_parameters": fingerprint(terrain_params_path),
            "coarse_height_index": fingerprint(coarse_height_path),
            "navigation_graph": fingerprint(nav_graph_path),
            "water_receipt": fingerprint(water_receipt_path),
            "background_receipt": fingerprint(bg_receipt_path),
            "load_freeze": fingerprint(load_freeze_path),
            "prototypes_source": fingerprint(prototypes_source_path),
            "three_pin": fingerprint(three_pin_path),
            "terrain_layer_textures": layer_textures,
        },
        "source_counts": {
            "bounds": r14_manifest["bounds"],
            "grid": r14_manifest["grid"],
            "chunk_m": r14_manifest["chunk_m"],
            "center": r14_manifest["center"],
            "safe_altitude_m": r14_manifest["safeAltitude"],
            "direct_surface_parts": len(direct_surface["parts"]),
            "direct_surface_categories": part_categories,
            "prototypes_total": len(r14_manifest["prototypes"]),
            "building_prototypes": sum(1 for p in r14_manifest["prototypes"] if p["category"] == "building"),
            "tree_prototypes": sum(1 for p in r14_manifest["prototypes"] if p["category"] == "tree"),
            "grass_prototypes": sum(1 for p in r14_manifest["prototypes"] if p["category"] == "grass"),
            "chunks_total": len(r14_manifest["chunks"]),
            "instance_pages_total": len(r14_manifest["pages"]),
            "source_trees_count": r14_manifest["vegetation"]["source_count"],
            "banks_in_scope": len(scope_data["banks"]),
        },
        "gate_passed": True,
    }

    out_path = B0_DIR / "基线凭证.json"
    out_path.write_text(json.dumps(baseline_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "phase": "B0",
        "gate_passed": True,
        "receipt": out_path.relative_to(ROOT).as_posix(),
        "receipt_sha256": sha256_file(out_path),
        "primary_port": 8820,
        "secondary_port": 8821,
        "prototypes_total": baseline_receipt["source_counts"]["prototypes_total"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
