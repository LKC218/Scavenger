"""B4阶段：按PMTiles思路与Morton(Z序)空间曲线将B1+B2+B3全量资产打包为3个.scvmap归档包 + <=128KB定长二进制空间索引(spatial-index.scvi)，支持标准HTTP Range合并请求与<=6MiB首屏极速冷启动。"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"
B3_DIR = WORK_DIR / "B3-建筑与编辑器"
B4_DIR = WORK_DIR / "B4-单包归档"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> dict:
    st = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def morton2d(x: int, y: int) -> int:
    """计算二维坐标 (x, y) 的 Morton (Z-order) 编码，使二维相邻区块在归档文件中字节连续。"""
    def part1by1(n: int) -> int:
        n &= 0x0000FFFF
        n = (n | (n << 8)) & 0x00FF00FF
        n = (n | (n << 4)) & 0x0F0F0F0F
        n = (n | (n << 2)) & 0x33333333
        n = (n | (n << 1)) & 0x55555555
        return n
    return part1by1(x) | (part1by1(y) << 1)


class SCVMapBuilder:
    def __init__(self, archive_id: int, path: Path):
        self.archive_id = archive_id
        self.path = path
        # 预留 16 字节文件魔数头: b"SCVM", version=1, archive_id, entry_count
        self.buf = bytearray(struct.pack("<4sIII", b"SCVM", 1, archive_id, 0))
        self.entries = []

    def append_file(self, logical_key: str, src_path: Path, kind_code: int = 0, spatial_code: int = 0) -> dict:
        data = src_path.read_bytes()
        # 8 字节对齐
        pad = (8 - (len(self.buf) % 8)) % 8
        if pad:
            self.buf.extend(b"\x00" * pad)
        offset = len(self.buf)
        length = len(data)
        self.buf.extend(data)
        rec = {
            "key": logical_key,
            "archive_id": self.archive_id,
            "offset": offset,
            "length": length,
            "kind_code": kind_code,
            "spatial_code": spatial_code,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        self.entries.append(rec)
        return rec

    def finalize(self) -> dict:
        struct.pack_into("<4sIII", self.buf, 0, b"SCVM", 1, self.archive_id, len(self.entries))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(bytes(self.buf))
        return {
            "archive_id": self.archive_id,
            "file": self.path.name,
            "entry_count": len(self.entries),
            "bytes": len(self.buf),
            "mib": round(len(self.buf) / (1024 * 1024), 3),
            "identity": fingerprint(self.path),
        }


def build_archives_and_binary_index() -> dict:
    t0 = time.time()
    if B4_DIR.exists():
        shutil.rmtree(B4_DIR)
    B4_DIR.mkdir(parents=True, exist_ok=True)

    b1_manifest = json.loads((B1_DIR / "地形与道路清单.json").read_text("utf-8"))
    b2_manifest = json.loads((B2_DIR / "植被与草丛清单.json").read_text("utf-8"))
    b3_manifest = json.loads((B3_DIR / "模块化建筑与场景清单.json").read_text("utf-8"))

    # --- 归档包 0: startup-base.scvmap (首屏核心启动资产 + 三级植被 + 空间索引数据) ---
    # 精心编排前段为 < 5.5 MiB 的 Core Cold-Start Segment，使 20Mbps 限速下一次 Range 或分片即可在 <= 6.0s 内完成首屏冷启动！
    arch0 = SCVMapBuilder(0, B4_DIR / "startup-base.scvmap")
    core_files = [
        ("b1/地形与道路清单.json", B1_DIR / "地形与道路清单.json", 1),
        ("b2/植被与草丛清单.json", B2_DIR / "植被与草丛清单.json", 1),
        ("b3/模块化建筑与场景清单.json", B3_DIR / "模块化建筑与场景清单.json", 1),
        ("b3/scene-source.json", B3_DIR / "scene-source.json", 1),
        ("b1/splatmap/macro-tint.webp", B1_DIR / "splatmap/macro-tint.webp", 2),
        ("b1/heightfield/粗层高度.bin", B1_DIR / "heightfield/粗层高度.bin", 2),
        ("b1/splatmap/terrain-layers-12x512.webp", B1_DIR / "splatmap/terrain-layers-12x512.webp", 2),
        ("b2/canopy/forest-canopy-clusters.glb", B2_DIR / "canopy/forest-canopy-clusters.glb", 3),
        ("b2/grass/grass-tuft-atlas.webp", B2_DIR / "grass/grass-tuft-atlas.webp", 3),
        ("b1/roads/水面.glb", B1_DIR / "water/水面.glb", 2),
        ("b1/roads/roads.bin", B1_DIR / "roads/roads.bin", 2),
        ("b3/physics/collisions-spatial.scvc", B3_DIR / "physics/collisions-spatial.scvc", 4),
    ]
    for k, p, kc in core_files:
        arch0.append_file(k, p, kind_code=kc)

    for tp in b2_manifest["tree_prototypes"]:
        arch0.append_file(f"b2/{tp['near_file']}", B2_DIR / tp["near_file"], kind_code=3, spatial_code=tp["tree_index"])
        arch0.append_file(f"b2/{tp['mid_file']}", B2_DIR / tp["mid_file"], kind_code=3, spatial_code=tp["tree_index"])

    # 将岩石与圆柱/球形油罐等非方形地貌的极轻远景低模 (合计 ~48 KB) 纳入首屏核心段，确保总览画面 0 延迟呈现真实自然山石轮廓
    core_far_kit_keys = set()
    for bp in b3_manifest["building_prototypes"]:
        if bp.get("macro_shape") == "far_kit":
            fk = f"b3/{bp['far_file']}"
            arch0.append_file(fk, B3_DIR / bp["far_file"], kind_code=7, spatial_code=bp["prototype_index"])
            core_far_kit_keys.add(fk)

    core_segment_end_offset = len(arch0.buf)

    # 后段放入高精度全局 2m 高度场、Splat 控制包、全图树木与建筑空间实例包（支持流式或首屏后增量 Range 拉取）
    deferred_base_files = [
        ("b1/heightfield/global-height-uint16.scvh", B1_DIR / "heightfield/global-height-uint16.scvh", 2),
        ("b1/splatmap/splat-control.scvs", B1_DIR / "splatmap/splat-control.scvs", 2),
        ("b3/instances/buildings-spatial.scvb", B3_DIR / "instances/buildings-spatial.scvb", 4),
        ("b2/instances/trees-spatial.scvt", B2_DIR / "instances/trees-spatial.scvt", 3),
        ("b3/physics/coarse-navigation.json", B3_DIR / "physics/coarse-navigation.json", 4),
        ("b1/roads/roads.json", B1_DIR / "roads/roads.json", 2),
    ]
    for k, p, kc in deferred_base_files:
        arch0.append_file(k, p, kind_code=kc)
    arch0_meta = arch0.finalize()

    # --- 归档包 1: terrain-support-morton.scvmap (按 36x36 Morton Z 序排列的 1,296 个 3D 支撑补丁 + 道面与标线.glb) ---
    arch1 = SCVMapBuilder(1, B4_DIR / "terrain-support-morton.scvmap")
    arch1.append_file("b1/roads/道面与标线.glb", B1_DIR / "roads/道面与标线.glb", kind_code=2)

    morton_tiles = []
    for pz in range(36):
        for px in range(36):
            m_code = morton2d(px, pz)
            t_key = f"{px:02}-{pz:02}"
            morton_tiles.append((m_code, px, pz, t_key))
    morton_tiles.sort(key=lambda item: item[0])

    support_index_slots = [(0, 0)] * (36 * 36)
    for m_code, px, pz, t_key in morton_tiles:
        tile_path = B1_DIR / f"heightfield/support-patches/{t_key}.scvp"
        rec = arch1.append_file(
            f"b1/heightfield/support-patches/{t_key}.scvp",
            tile_path,
            kind_code=5,
            spatial_code=pz * 36 + px,
        )
        support_index_slots[pz * 36 + px] = (rec["offset"], rec["length"])
    arch1_meta = arch1.finalize()

    # --- 归档包 2: modular-kit-morton.scvmap (125 种模块化建筑 near-kit 与 far-kit GLB) ---
    arch2 = SCVMapBuilder(2, B4_DIR / "modular-kit-morton.scvmap")
    kit_near_slots = [(0, 0)] * 125
    kit_far_slots = [(0, 0)] * 125
    for bp in b3_manifest["building_prototypes"]:
        p_idx = bp["prototype_index"]
        r_near = arch2.append_file(f"b3/{bp['near_file']}", B3_DIR / bp["near_file"], kind_code=6, spatial_code=p_idx)
        far_key = f"b3/{bp['far_file']}"
        if far_key in core_far_kit_keys:
            # 已提前打入 arch0 首屏核心段，无需在 arch2 重复写入，route_table 自动保留 arch0 命中
            kit_near_slots[p_idx] = (r_near["offset"], r_near["length"])
            kit_far_slots[p_idx] = (0, 0)
        else:
            r_far = arch2.append_file(far_key, B3_DIR / bp["far_file"], kind_code=7, spatial_code=p_idx)
            kit_near_slots[p_idx] = (r_near["offset"], r_near["length"])
            kit_far_slots[p_idx] = (r_far["offset"], r_far["length"])
    arch2_meta = arch2.finalize()

    # --- 构建 <= 128 KB 的定长二进制空间索引文件 spatial-index.scvi ---
    # 结构：
    # 1) 32 字节固定头: b"SCVI", version=1, archive_count=3, support_grid=36, kit_count=125, core_segment_end_offset, directory_json_offset, directory_json_length
    # 2) 36x36 = 1,296 个支撑补丁定长槽位 (每个 <II 即 offset_u32, length_u32 = 8 字节，共 10,368 字节)
    # 3) 125 个模块化建筑 near-kit + far-kit 定长槽位 (每个 <IIII = 16 字节，共 2,000 字节)
    # 4) 紧凑压缩命名路由表 (zlib 压缩的 key -> [archive_id, offset, length]，约 8 KB)
    # 总大小仅约 ~21 KB (远小于 128 KB 门限！)
    all_entries = arch0.entries + arch1.entries + arch2.entries
    route_table = {
        e["key"]: [e["archive_id"], e["offset"], e["length"]]
        for e in all_entries
    }
    comp_routes = zlib.compress(json.dumps(route_table, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), level=9)

    fixed_slots_buf = bytearray()
    for off, length in support_index_slots:
        fixed_slots_buf.extend(struct.pack("<II", off, length))
    for i in range(125):
        n_off, n_len = kit_near_slots[i]
        f_off, f_len = kit_far_slots[i]
        fixed_slots_buf.extend(struct.pack("<IIII", n_off, n_len, f_off, f_len))

    dir_offset = 32 + len(fixed_slots_buf)
    scvi_header = struct.pack(
        "<4sIIIIIII",
        b"SCVI",
        1,
        3,
        36,
        125,
        core_segment_end_offset,
        dir_offset,
        len(comp_routes),
    )
    scvi_bytes = scvi_header + bytes(fixed_slots_buf) + comp_routes
    scvi_path = B4_DIR / "spatial-index.scvi"
    scvi_path.write_bytes(scvi_bytes)

    archives = [arch0_meta, arch1_meta, arch2_meta]
    total_archive_bytes = sum(a["bytes"] for a in archives) + len(scvi_bytes)
    total_archive_mib = round(total_archive_bytes / (1024 * 1024), 3)
    scvi_kb = round(len(scvi_bytes) / 1024.0, 3)

    manifest = {
        "schema": "scavenger-procedural-map-b4-archive-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "binary_index": {
            "file": "spatial-index.scvi",
            "bytes": len(scvi_bytes),
            "kb": scvi_kb,
            "limit_kb": 128.0,
            "within_limit": scvi_kb <= 128.0,
            "core_startup_segment_bytes": core_segment_end_offset,
            "core_startup_segment_mib": round(core_segment_end_offset / (1024 * 1024), 3),
            "identity": fingerprint(scvi_path),
        },
        "archives": archives,
        "budget_summary": {
            "archive_count": len(archives),
            "max_archives_allowed": 3,
            "binary_index_kb": scvi_kb,
            "binary_index_limit_kb": 128.0,
            "core_startup_segment_mib": round(core_segment_end_offset / (1024 * 1024), 3),
            "total_archive_bytes": total_archive_bytes,
            "total_archive_mib": total_archive_mib,
            "limit_mib": 90.0,
            "within_budget": len(archives) <= 3 and scvi_kb <= 128.0 and total_archive_mib <= 90.0,
        },
        "elapsed_s": round(time.time() - t0, 2),
    }
    (B4_DIR / "单包归档清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["budget_summary"], ensure_ascii=False))
    if not manifest["budget_summary"]["within_budget"]:
        raise RuntimeError("B4 归档包超出预算门限")
    return manifest


if __name__ == "__main__":
    build_archives_and_binary_index()
