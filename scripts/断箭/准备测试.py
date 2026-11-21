"""将单位包按块解压到本地批次，避免一次驻留整个 UnityFS 数据区。"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import time

import lz4.block

ROOT = Path(__file__).resolve().parents[2]


def zstring(stream):
    result = bytearray()
    while True:
        value = stream.read(1)
        if not value:
            raise ValueError("未终止的字符串")
        if value == b"\0":
            return result.decode("utf-8")
        result.extend(value)


def unpack(source, target):
    marker = target / "解析缓存清单.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    if target.exists():
        raise FileExistsError(f"未完成的缓存目录，请使用新批次：{target}")
    target.mkdir(parents=True)
    start = time.time()
    with source.open("rb") as stream:
        signature = zstring(stream)
        version = struct.unpack(">I", stream.read(4))[0]
        player, engine = zstring(stream), zstring(stream)
        size, compressed, raw, flags = struct.unpack(">QIII", stream.read(20))
        if signature != "UnityFS" or version < 7 or flags & 0x80:
            raise ValueError("当前只处理已核验的前置索引 UnityFS 格式")
        stream.seek((stream.tell() + 15) // 16 * 16)
        info_data = stream.read(compressed)
        if flags & 63 in (2, 3):
            info_data = lz4.block.decompress(info_data, uncompressed_size=raw)
        elif flags & 63:
            raise ValueError("未适配的索引压缩类型")
        table = io.BytesIO(info_data)
        table.read(16)
        count = struct.unpack(">I", table.read(4))[0]
        blocks = [struct.unpack(">IIH", table.read(10)) for _ in range(count)]
        nodes = []
        for _ in range(struct.unpack(">I", table.read(4))[0]):
            offset, length, node_flags = struct.unpack(">QQI", table.read(20))
            name = zstring(table)
            if Path(name).name != name:
                raise ValueError("资源节点名称超出缓存目录")
            nodes.append({"offset": offset, "size": length, "flags": node_flags, "name": name})
        if flags & 0x200:
            stream.seek((stream.tell() + 15) // 16 * 16)
        outputs = [(target / node["name"]).open("xb") for node in nodes]
        digests = [hashlib.sha256() for _ in nodes]
        position, last = 0, start
        try:
            for index, (raw_size, compressed_size, block_flags) in enumerate(blocks):
                chunk = stream.read(compressed_size)
                kind = block_flags & 63
                if kind in (2, 3):
                    chunk = lz4.block.decompress(chunk, uncompressed_size=raw_size)
                elif kind:
                    raise ValueError(f"未适配的块压缩类型：{kind}")
                if len(chunk) != raw_size:
                    raise ValueError("解压长度不符")
                for node, output, digest in zip(nodes, outputs, digests):
                    lo = max(position, node["offset"])
                    hi = min(position + len(chunk), node["offset"] + node["size"])
                    if lo < hi:
                        part = chunk[lo - position:hi - position]
                        output.write(part)
                        digest.update(part)
                position += len(chunk)
                if time.time() - last > 5:
                    print(f"按块解压：{index + 1}/{count}，{position / 1e9:.2f} GB", flush=True)
                    last = time.time()
        finally:
            for output in outputs:
                output.close()
    for node, digest in zip(nodes, digests):
        if (target / node["name"]).stat().st_size != node["size"]:
            raise ValueError("缓存文件长度不符")
        node["sha256"] = digest.hexdigest()
    result = {"source_relative": str(source.name), "source_size": size, "engine": engine,
              "nodes": nodes, "elapsed_seconds": time.time() - start, "method": "逐块LZ4解压，游戏文件只读"}
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-001")
    args = parser.parse_args()
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
    source = next((Path(config["game_directory"]) / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("units_assets*.bundle"))
    batch_root = (ROOT / config["batch_root"]).resolve()
    batch = (batch_root / args.batch).resolve()
    if not batch.is_relative_to(batch_root) or batch == batch_root:
        raise ValueError("批次超出本地批次根目录")
    result = unpack(source, batch / "解析缓存")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
