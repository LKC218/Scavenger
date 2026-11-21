"""按需读取主 UnityFS 中的音效预设，保留字段与对象身份。"""

import argparse
import hashlib
import importlib
import json
import time
from pathlib import Path

from 探测音效 import ROOT, local_batch, save, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    import UnityPy
    assembly = importlib.import_module("解析装配")
    start = time.time()
    batch = local_batch(args.batch)
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8-sig"))
    game = Path(config["game_directory"])
    source = game / "BrokenArrow_Data/data.unity3d"
    bundle = assembly.BundleIndex(source)
    env = UnityPy.Environment()
    results = []
    try:
        sfx_source = next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("sfx_assets_all_*.bundle"))
        sfx = UnityPy.load(str(sfx_source))
        entries = [{"path": path, "id": ptr.path_id, "data": ptr.deref().read_typetree()}
                   for path, ptr in sfx.container.items() if ptr.deref().type.name == "MonoBehaviour"]
        objects = {str(obj.path_id): obj.read_typetree() for obj in sfx.objects if obj.type.name == "MonoBehaviour"}
        save(batch / "证据/SFX预设与集合.json", {"source": str(sfx_source), "sha256": digest(sfx_source),
             "entries": entries, "objects": objects})
        catalog = assembly.read_catalog_locations(game / "BrokenArrow_Data/StreamingAssets/aa/catalog.bin")
        save(batch / "证据/音效目录位置.json", [row for row in catalog["locations"]
             if "/SFX/" in (row["internal_id"] or "")])
        raw_settings = []
        for node in bundle.nodes:
            if node["name"] not in ("globalgamemanagers.assets", "resources.assets"):
                continue
            af = env.load_file(assembly.NodeStream(bundle, node), name=node["name"])
            hits = []
            for obj in af.objects.values():
                if obj.type.name not in ("MonoBehaviour", "TextAsset", "ResourceManager"):
                    continue
                name = obj.peek_name() or ""
                if name in ("AudioConfig", "FMODStudioSettings", "FmodPoolConfig"):
                    raw_settings.append({"node": node["name"], "id": obj.path_id,
                                         "name": name, "hex": obj.get_raw_data().hex()})
                if any(word in name.lower() for word in ("sound", "audio", "fmod")) or obj.type.name == "ResourceManager":
                    try:
                        data = obj.read_typetree()
                        hits.append({"id": obj.path_id, "type": obj.type.name, "name": name,
                                     "raw_sha256": hashlib.sha256(obj.get_raw_data()).hexdigest(), "data": data})
                    except Exception as error:
                        hits.append({"id": obj.path_id, "name": name, "error": str(error)})
            results.append({"node": node["name"], "node_size": node["size"], "objects": hits})
            print(node["name"], "匹配对象", len(hits), flush=True)
        save(batch / "证据/音频设置原始对象.json", raw_settings)
        save(batch / "证据/音效预设原字段.json", {"source": "BrokenArrow_Data/data.unity3d",
             "source_bytes": source.stat().st_size, "source_mtime_ns": source.stat().st_mtime_ns,
             "source_sha256": digest(source), "engine": bundle.engine, "nodes": results,
             "blocks_read": bundle.blocks_read, "elapsed_seconds": time.time() - start})
    finally:
        bundle.file.close()


if __name__ == "__main__":
    main()
