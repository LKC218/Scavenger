"""V3 自动闸门：通过真实 HTTP Range (206 Partial Content) 逐条回读校验全部归档字节与 SHA256。"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import threading
import time
import urllib.request
import zlib
from pathlib import Path

from 公共配置 import V3_DIR, now_iso, read_json, save_json
from 预览服务 import start_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=V3_DIR)
    parser.add_argument("--port", type=int, default=8825)
    args = parser.parse_args()

    t0 = time.perf_counter()
    arc_dir = args.dir.resolve()
    manifest = read_json(arc_dir / "归档清单.json")

    httpd = start_server(args.port, verbose=False)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()

    errors = []
    verified = 0
    total_range_bytes = 0
    arc_by_id = {a["id"]: a["file"] for a in manifest["archives"]}

    try:
        # 1. 回读二进制索引魔数、条目数与解压 JSON 元数据
        idx_url = f"http://127.0.0.1:{args.port}/v3/vehicles-index.scvi"
        with urllib.request.urlopen(idx_url, timeout=10) as resp:
            scvi_raw = resp.read()
        magic, ver, vcnt, ecnt, jlen, flags = struct.unpack_from("<4sIIIII", scvi_raw, 0)
        if magic != b"SCVI" or ver != 1 or vcnt != manifest["vehicle_count"] or ecnt != manifest["entry_count"]:
            errors.append(f"SCVI 头部校验失败: {magic} v{ver} v={vcnt} e={ecnt}")
        else:
            joff = 24 + ecnt * 24
            payload_raw = scvi_raw[joff : joff + jlen]
            if flags & 1:
                payload_raw = zlib.decompress(payload_raw)
            parsed_meta = json.loads(payload_raw.decode("utf-8"))
            if len(parsed_meta.get("vehicles", [])) != vcnt or len(parsed_meta.get("entries", [])) != ecnt:
                errors.append("SCVI 解压后 JSON 数量校验失败")

        # 2. 逐条发起标准 HTTP 206 Partial Content Range 请求验证字节与 SHA256
        for e in manifest["entries"]:
            file_name = arc_by_id[e["archive_id"]]
            url = f"http://127.0.0.1:{args.port}/v3/{file_name}"
            start = e["offset"]
            end = start + e["length"] - 1
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 206:
                    errors.append(f"{e['key']} 未返回 206 (实际 {resp.status})")
                    continue
                data = resp.read()
            if len(data) != e["length"]:
                errors.append(f"{e['key']} 长度不符: {len(data)} != {e['length']}")
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest != e["sha256"]:
                errors.append(f"{e['key']} SHA256 不符")
                continue
            if e["kind_code"] & 0x80:
                try:
                    raw = zlib.decompress(data)
                except Exception as exc:
                    errors.append(f"{e['key']} deflate 解压失败: {exc}")
                    continue
                if hashlib.sha256(raw).hexdigest() != e.get("raw_sha256"):
                    errors.append(f"{e['key']} 解压还原 SHA256 不符")
                    continue
            verified += 1
            total_range_bytes += len(data)
    finally:
        httpd.shutdown()
        httpd.server_close()

    # 3. 核验硬门限
    gates = {
        "index_le_64kib": manifest["index_kib"] <= 64.0,
        "far_base_le_3mib": manifest["archives"][0]["mib"] <= 3.0,
        "total_le_85mib": manifest["total_mib"] <= 85.0,
        "all_entries_range_verified": verified == manifest["entry_count"] and len(errors) == 0,
    }
    receipt = {
        "schema_version": 1,
        "checked_at": now_iso(),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "verified_entries": verified,
        "expected_entries": manifest["entry_count"],
        "total_range_bytes_read": total_range_bytes,
        "gates": gates,
        "passed": all(gates.values()),
        "errors": errors[:30],
    }
    save_json(arc_dir / "归档回读核验回执.json", receipt)
    print(
        f"[V3 归档回读] 验证={verified}/{manifest['entry_count']} ({total_range_bytes/1048576:.2f} MiB) | "
        f"门限={gates} | 耗时 {receipt['elapsed_sec']}s"
    )
    if not receipt["passed"]:
        for er in errors[:10]:
            print("  ERR:", er)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
