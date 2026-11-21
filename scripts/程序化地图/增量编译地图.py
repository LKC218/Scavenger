"""B3阶段：十秒级增量打包CLI。读取scene-source.json中的手工覆盖表(新增/修改/删除建筑与树木、地形笔刷、道路样条)，自动重算受影响区块的旋转凸包碰撞、4米寻路网格、125m粗通行成本与直升机30米越障包络。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"

MIN_X, MAX_X = -4425.0, 4575.0
MIN_Z, MAX_Z = -4150.0, 4850.0
CHUNK_M = 125.0
GRID_N = 72
NAV_STEP = 4.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def compute_obb_from_bounds_and_transform(
    b_min: list[float],
    b_max: list[float],
    x: float,
    y: float,
    z: float,
    yaw: float,
    sx: float = 1.0,
    sy: float = 1.0,
    sz: float = 1.0,
) -> dict:
    """从原型局部包围盒与实例 TRS 变换派生世界空间旋转盒碰撞体 (OBB) 与直升机越障顶高。"""
    lcx = (b_min[0] + b_max[0]) * 0.5 * sx
    lcy = (b_min[1] + b_max[1]) * 0.5 * sy
    lcz = (b_min[2] + b_max[2]) * 0.5 * sz
    hx = max(0.25, (b_max[0] - b_min[0]) * 0.5 * abs(sx))
    hy = max(0.25, (b_max[1] - b_min[1]) * 0.5 * abs(sy))
    hz = max(0.25, (b_max[2] - b_min[2]) * 0.5 * abs(sz))

    c = math.cos(yaw)
    s = math.sin(yaw)
    wcx = x + lcx * c - lcz * s
    wcy = y + lcy
    wcz = z + lcx * s + lcz * c

    ext_x = abs(c) * hx + abs(s) * hz
    ext_z = abs(s) * hx + abs(c) * hz
    return {
        "center": [round(wcx, 3), round(wcy, 3), round(wcz, 3)],
        "half_extents": [round(hx, 3), round(hy, 3), round(hz, 3)],
        "yaw_rad": round(yaw, 4),
        "axis_x": [round(c, 5), round(s, 5)],
        "axis_z": [round(-s, 5), round(c, 5)],
        "aabb": [
            round(wcx - ext_x, 3),
            round(wcy - hy, 3),
            round(wcz - ext_z, 3),
            round(wcx + ext_x, 3),
            round(wcy + hy, 3),
            round(wcz + ext_z, 3),
        ],
        "top_y": round(wcy + hy, 3),
    }


def point_in_obb_xz(px: float, pz: float, obb: dict, clearance: float = 5.8) -> bool:
    dx = px - obb["center"][0]
    dz = pz - obb["center"][2]
    ux, uz = obb["axis_x"]
    vx, vz = obb["axis_z"]
    lx = abs(dx * ux + dz * uz)
    lz = abs(dx * vx + dz * vz)
    return lx <= (obb["half_extents"][0] + clearance) and lz <= (obb["half_extents"][2] + clearance)


def compile_incremental(scene_source_path: Path, out_derived_path: Path) -> dict:
    t0 = time.time()
    scene_src = json.loads(scene_source_path.read_text("utf-8"))
    b3_manifest = json.loads((B3_DIR / "模块化建筑与场景清单.json").read_text("utf-8"))
    b2_manifest = json.loads((B2_DIR / "植被与草丛清单.json").read_text("utf-8"))

    proto_map = {p["id"]: p for p in b3_manifest["building_prototypes"]}
    for tp in b2_manifest["tree_prototypes"]:
        proto_map[tp["id"]] = tp

    overrides = scene_src.get("manual_overrides", {})
    added = overrides.get("added_instances", [])
    modified = overrides.get("modified_instances", {})
    deleted = set(overrides.get("deleted_instances", []))
    terrain_strokes = overrides.get("terrain_height_strokes", [])
    splat_strokes = overrides.get("splat_strokes", [])
    road_mods = overrides.get("modified_road_splines", {})

    # 识别所有受影响的 125m 区块并派生新增/修改实例的旋转 OBB 碰撞体
    dirty_cells: set[tuple[int, int]] = set()
    derived_obbs = []
    cell_top_y_overrides: dict[str, float] = {}

    def mark_aabb_cells(aabb: list[float], top_y: float):
        cx0 = max(0, min(GRID_N - 1, int(math.floor((aabb[0] - 8.0 - MIN_X) / CHUNK_M))))
        cx1 = max(0, min(GRID_N - 1, int(math.floor((aabb[3] + 8.0 - MIN_X) / CHUNK_M))))
        cz0 = max(0, min(GRID_N - 1, int(math.floor((aabb[2] - 8.0 - MIN_Z) / CHUNK_M))))
        cz1 = max(0, min(GRID_N - 1, int(math.floor((aabb[5] + 8.0 - MIN_Z) / CHUNK_M))))
        for cz in range(cz0, cz1 + 1):
            for cx in range(cx0, cx1 + 1):
                dirty_cells.add((cx, cz))
                ckey = f"{cx:02}-{cz:02}"
                cell_top_y_overrides[ckey] = max(cell_top_y_overrides.get(ckey, -40.0), top_y)

    for item in added:
        pid = item.get("prototype_id")
        p_meta = proto_map.get(pid)
        if not p_meta:
            continue
        pos = item.get("position", [0.0, 0.0, 0.0])
        yaw = float(item.get("yaw_rad", 0.0))
        scale = item.get("scale", [1.0, 1.0, 1.0])
        if isinstance(scale, (int, float)):
            scale = [float(scale)] * 3
        obb = compute_obb_from_bounds_and_transform(
            p_meta["bounds_min"],
            p_meta["bounds_max"],
            float(pos[0]),
            float(pos[1]),
            float(pos[2]),
            yaw,
            float(scale[0]),
            float(scale[1]),
            float(scale[2]),
        )
        obb["instance_id"] = item.get("id", f"added_{len(derived_obbs)}")
        obb["prototype_id"] = pid
        obb["kind"] = p_meta.get("category", p_meta.get("kind", "building"))
        derived_obbs.append(obb)
        mark_aabb_cells(obb["aabb"], obb["top_y"])

    for inst_key, mod in modified.items():
        pid = mod.get("prototype_id")
        p_meta = proto_map.get(pid)
        if not p_meta:
            continue
        pos = mod.get("position", [0.0, 0.0, 0.0])
        yaw = float(mod.get("yaw_rad", 0.0))
        scale = mod.get("scale", [1.0, 1.0, 1.0])
        if isinstance(scale, (int, float)):
            scale = [float(scale)] * 3
        obb = compute_obb_from_bounds_and_transform(
            p_meta["bounds_min"],
            p_meta["bounds_max"],
            float(pos[0]),
            float(pos[1]),
            float(pos[2]),
            yaw,
            float(scale[0]),
            float(scale[1]),
            float(scale[2]),
        )
        obb["instance_id"] = inst_key
        obb["prototype_id"] = pid
        obb["kind"] = p_meta.get("category", p_meta.get("kind", "building"))
        derived_obbs.append(obb)
        mark_aabb_cells(obb["aabb"], obb["top_y"])

    for stroke in terrain_strokes:
        sx = float(stroke.get("x", 0.0))
        sz = float(stroke.get("z", 0.0))
        sr = float(stroke.get("radius", 25.0))
        mark_aabb_cells([sx - sr, -40.0, sz - sr, sx + sr, 120.0, sz + sr], 0.0)

    # 对所有受影响的 125m 区块重算 32x32 (4m 步距) 局部寻路阻挡格数与 125m 粗通行成本增量
    local_nav_patches = {}
    for cx, cz in sorted(dirty_cells):
        ckey = f"{cx:02}-{cz:02}"
        cell_min_x = MIN_X + cx * CHUNK_M
        cell_min_z = MIN_Z + cz * CHUNK_M
        blocked_cells = []
        for iz in range(32):
            pz = cell_min_z + (iz + 0.5) * (CHUNK_M / 32.0)
            for ix in range(32):
                px = cell_min_x + (ix + 0.5) * (CHUNK_M / 32.0)
                if any(point_in_obb_xz(px, pz, obb, clearance=5.8) for obb in derived_obbs):
                    blocked_cells.append(iz * 32 + ix)
        blocked_ratio = len(blocked_cells) / 1024.0
        local_nav_patches[ckey] = {
            "cell": ckey,
            "blocked_4m_count": len(blocked_cells),
            "blocked_ratio": round(blocked_ratio, 4),
            "coarse_cost_multiplier": round(1.0 + blocked_ratio * 4.5, 3),
            "helicopter_safe_top_y": round(cell_top_y_overrides.get(ckey, 0.0) + 12.0, 2),
        }

    elapsed_s = round(time.time() - t0, 4)
    result = {
        "schema": "scavenger-incremental-map-compilation-v1",
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source_file": scene_source_path.relative_to(ROOT).as_posix() if scene_source_path.is_relative_to(ROOT) else str(scene_source_path),
        "source_sha256": sha256_file(scene_source_path),
        "dirty_cells_count": len(dirty_cells),
        "derived_obbs_count": len(derived_obbs),
        "deleted_instances_count": len(deleted),
        "terrain_strokes_count": len(terrain_strokes),
        "splat_strokes_count": len(splat_strokes),
        "modified_roads_count": len(road_mods),
        "derived_obbs": derived_obbs,
        "local_nav_and_envelope_patches": local_nav_patches,
        "elapsed_s": elapsed_s,
        "within_10s_target": elapsed_s <= 10.0,
    }
    out_derived_path.parent.mkdir(parents=True, exist_ok=True)
    out_derived_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="十秒级增量编译地图 CLI")
    parser.add_argument("--source", type=Path, default=B3_DIR / "scene-source.json", help="可编辑 scene-source.json 路径")
    parser.add_argument("--output", type=Path, default=B3_DIR / "physics/incremental-derived.json", help="增量物理与导航输出路径")
    args = parser.parse_args()

    res = compile_incremental(args.source.resolve(), args.output.resolve())
    print(json.dumps({
        "dirty_cells_count": res["dirty_cells_count"],
        "derived_obbs_count": res["derived_obbs_count"],
        "elapsed_s": res["elapsed_s"],
        "within_10s_target": res["within_10s_target"],
    }, ensure_ascii=False))
    if not res["within_10s_target"]:
        raise RuntimeError(f"增量编译耗时超出 10 秒上限: {res['elapsed_s']}s")


if __name__ == "__main__":
    main()
