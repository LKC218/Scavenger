"""V3 阶段：将全库 129 款载具打包为 3 个 HTTP Range 分层归档包(.scvveh) + <=64KB 定长二进制索引(vehicles-index.scvi)。"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
import zlib
from pathlib import Path

from 公共配置 import (
    BATCH_ID,
    ROOT,
    V2_DIR,
    V3_DIR,
    fingerprint,
    now_iso,
    read_json,
    save_json,
    sha256_bytes,
)


class SCVVehArchiveBuilder:
    def __init__(self, archive_id: int, path: Path):
        self.archive_id = archive_id
        self.path = path
        # 16 字节头: b"SCVV", u32 version=1, u32 archive_id, u32 entry_count
        self.buf = bytearray(struct.pack("<4sIII", b"SCVV", 1, archive_id, 0))
        self.entries: list[dict] = []

    def append_file(self, key: str, src_path: Path, kind_code: int, veh_index: int, compress: bool = False) -> dict:
        raw_data = src_path.read_bytes()
        raw_sha = sha256_bytes(raw_data)
        data = raw_data
        stored_kind = kind_code
        if compress:
            comp = zlib.compress(raw_data, level=6)
            if len(comp) + 64 < len(raw_data):
                data = comp
                stored_kind = kind_code | 0x80
        # 8 字节对齐
        pad = (8 - (len(self.buf) % 8)) % 8
        if pad:
            self.buf.extend(b"\x00" * pad)
        offset = len(self.buf)
        length = len(data)
        self.buf.extend(data)
        rec = {
            "key": key,
            "archive_id": self.archive_id,
            "offset": offset,
            "length": length,
            "raw_length": len(raw_data),
            "kind_code": stored_kind,
            "veh_index": veh_index,
            "sha256": sha256_bytes(data),
            "raw_sha256": raw_sha,
        }
        self.entries.append(rec)
        return rec

    def finalize(self) -> dict:
        struct.pack_into("<4sIII", self.buf, 0, b"SCVV", 1, self.archive_id, len(self.entries))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(bytes(self.buf))
        return {
            "id": self.archive_id,
            "file": self.path.name,
            "entry_count": len(self.entries),
            "bytes": len(self.buf),
            "mib": round(len(self.buf) / 1048576, 3),
            "identity": fingerprint(self.path),
        }


def compact_vehicle_record(idx: int, meta: dict) -> dict:
    """提炼运行时必需的紧凑元数据，保证全库 129 车二进制索引 <= 64 KB。"""
    b = meta["bounds"]
    lb = meta["labelBounds"]
    return {
        "i": idx,
        "id": meta["id"],
        "slug": meta["slug"],
        "name": meta["name"],
        "cat": meta["category"][0],  # 'g' | 'a'
        "role": meta["role"],
        "st": "P" if meta["quality_status"] == "passed" else "R",
        "b": [
            [round(x, 3) for x in b["min"]],
            [round(x, 3) for x in b["max"]],
        ],
        "lb": [
            [round(x, 3) for x in lb["min"]],
            [round(x, 3) for x in lb["max"]],
        ],
        "col": [round(x, 3) for x in meta["avgLinearColor"]],
        "sl": [
            [s["slot"], [round(x, 3) for x in s["pivot"]], s["axis"]]
            for s in meta["slots"]
            if s["slot"] != "hull"
        ],
        "t": [meta["lods"]["lod0"]["triangles"], meta["lods"]["lod1"]["triangles"], meta["lods"]["lod2"]["triangles"]],
        "dc": meta["lods"]["lod0"]["drawCalls"],
        "b256": meta["baseline_256_bytes"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=V2_DIR)
    parser.add_argument("--out", type=Path, default=V3_DIR)
    args = parser.parse_args()

    t0 = time.perf_counter()
    src_dir = args.src.resolve()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_files = sorted((src_dir / "meta").glob("*.json"))
    metas = [read_json(p) for p in meta_files]
    metas.sort(key=lambda m: (0 if m["category"] == "ground" else 1, m["id"].lower()))

    arc_far = SCVVehArchiveBuilder(0, out_dir / "vehicles-far.scvveh")
    arc_shared = SCVVehArchiveBuilder(1, out_dir / "vehicles-shared-tex.scvveh")
    arc_detail = SCVVehArchiveBuilder(2, out_dir / "vehicles-detail.scvveh")

    all_entries: list[dict] = []
    added_tex: set[str] = set()
    compact_vehicles: list[dict] = []

    # 1. 按真实引用次数统计全部贴图：被 >=2 辆车引用的（或 shared_ 前缀）统一归入共享归档包 archive 1，杜绝任何孤立废贴图打入归档
    tex_ref_count: dict[str, int] = {}
    for meta in metas:
        seen_in_veh: set[str] = set()
        for t_info in meta.get("textures", []):
            tk = t_info["uri"]
            if tk not in seen_in_veh:
                seen_in_veh.add(tk)
                tex_ref_count[tk] = tex_ref_count.get(tk, 0) + 1

    for t_key in sorted(tex_ref_count.keys()):
        if tex_ref_count[t_key] >= 2 or Path(t_key).name.startswith("shared_"):
            tp = src_dir / t_key
            if not tp.is_file():
                raise FileNotFoundError(f"共享贴图缺失: {tp}")
            rec = arc_shared.append_file(t_key, tp, kind_code=10, veh_index=0xFFFFFFFF)
            all_entries.append(rec)
            added_tex.add(t_key)

    # 2. 逐车打包 LOD2 到 archive 0，LOD1/LOD0 及单车独有贴图到 archive 2
    for idx, meta in enumerate(metas):
        slug = meta["slug"]
        compact_vehicles.append(compact_vehicle_record(idx, meta))

        p_lod2 = ROOT / meta["lods"]["lod2"]["path"]
        all_entries.append(arc_far.append_file(f"models/{slug}/lod2.glb", p_lod2, kind_code=2, veh_index=idx, compress=True))

        p_lod1 = ROOT / meta["lods"]["lod1"]["path"]
        all_entries.append(arc_detail.append_file(f"models/{slug}/lod1.glb", p_lod1, kind_code=1, veh_index=idx, compress=True))

        p_lod0 = ROOT / meta["lods"]["lod0"]["path"]
        all_entries.append(arc_detail.append_file(f"models/{slug}/lod0.glb", p_lod0, kind_code=0, veh_index=idx, compress=True))

        for t_info in meta.get("textures", []):
            t_key = t_info["uri"]
            if t_key not in added_tex:
                tp = src_dir / t_key
                if not tp.is_file():
                    raise FileNotFoundError(f"贴图缺失: {tp}")
                all_entries.append(arc_detail.append_file(t_key, tp, kind_code=11, veh_index=idx))
                added_tex.add(t_key)
            # 同步打入对应的 256px 轻量中景底色 Mip（供 LOD1 战场极速流式拉取，单张仅 ~10-20KB）
            if not t_info.get("linear"):
                m256_key = t_key[:-5] + "_m256.ktx2"
                m256_path = src_dir / m256_key
                if m256_key not in added_tex and m256_path.is_file():
                    all_entries.append(arc_shared.append_file(m256_key, m256_path, kind_code=12, veh_index=0xFFFFFFFF))
                    added_tex.add(m256_key)

    arc_infos = [arc_far.finalize(), arc_shared.finalize(), arc_detail.finalize()]

    # 3. 写出定长二进制索引 vehicles-index.scvi
    # 头 24B (flags=1 表示尾段 JSON 经 RFC1950 zlib/deflate 压缩，浏览器原生 DecompressionStream('deflate') 秒解) + 定长条目表(24B * N) + 压缩 JSON
    meta_payload = {
        "archives": [{"id": a["id"], "file": a["file"], "bytes": a["bytes"]} for a in arc_infos],
        "entries": [e["key"] for e in all_entries],
        "vehicles": compact_vehicles,
    }
    raw_json_bytes = json.dumps(meta_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp_json_bytes = zlib.compress(raw_json_bytes, level=9)

    table_buf = bytearray()
    for e in all_entries:
        khash = zlib.crc32(e["key"].encode("utf-8")) & 0xFFFFFFFF
        table_buf.extend(
            struct.pack(
                "<IIIIII",
                khash,
                e["archive_id"],
                e["offset"],
                e["length"],
                e["kind_code"],
                e["veh_index"],
            )
        )

    scvi_header = struct.pack(
        "<4sIIIII",
        b"SCVI",
        1,
        len(compact_vehicles),
        len(all_entries),
        len(comp_json_bytes),
        1,  # flags: bit0 = zlib/deflate compressed payload
    )
    scvi_path = out_dir / "vehicles-index.scvi"
    scvi_bytes = scvi_header + bytes(table_buf) + comp_json_bytes
    scvi_path.write_bytes(scvi_bytes)

    total_archive_bytes = sum(a["bytes"] for a in arc_infos) + len(scvi_bytes)
    manifest = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "created_at": now_iso(),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "vehicle_count": len(compact_vehicles),
        "entry_count": len(all_entries),
        "index_file": fingerprint(scvi_path),
        "index_kib": round(len(scvi_bytes) / 1024, 3),
        "archives": arc_infos,
        "total_bytes": total_archive_bytes,
        "total_mib": round(total_archive_bytes / 1048576, 3),
        "entries": all_entries,
    }
    save_json(out_dir / "归档清单.json", manifest)

    print(f"[V3 归档打包] 完成！耗时 {manifest['elapsed_sec']}s")
    print(f"  vehicles-index.scvi      : {manifest['index_kib']:.2f} KiB ({len(all_entries)} 条目)")
    for a in arc_infos:
        print(f"  {a['file']:26s}: {a['mib']:.3f} MiB ({a['entry_count']} 条目)")
    print(f"  全库归档总计             : {manifest['total_mib']:.3f} MiB (原256默认+远景为 270.63 MiB)")


if __name__ == "__main__":
    main()
