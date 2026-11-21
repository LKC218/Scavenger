"""按来源身份导出Airbase建筑、装饰、树木和草丛原型，不改正式库。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import UnityPy

from 解析装配 import BundleIndex, NodeStream
from 提取地图 import MapExtractor
from 地图来源类型树 import corrected_generator
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


class FullMapExtractor(MapExtractor):
    def __init__(self, batch, game):
        super().__init__(batch, game)
        self.env.typetree_generator = corrected_generator(ROOT / "tmp/Airbase全图-V001-01a1a602/组件程序集")

    def load_external(self, name):
        if name in self.env.cabs:
            return
        if name == "unity default resources":
            self.env.load_file(str(self.game / "BrokenArrow_Data/Resources" / name))
            return
        super().load_external(name)

    def deref(self, ptr, af):
        if ptr["m_FileID"]:
            name = Path(af.externals[ptr["m_FileID"] - 1].path).name
            if name == "unity default resources":
                self.load_external(name)
        return super().deref(ptr, af)

    def material(self, ptr, af, warnings):
        key, entry = super().material(ptr, af, warnings)
        props = entry["raw"]["m_SavedProperties"]
        floats = dict(props["m_Floats"])
        keywords = entry["raw"].get("m_ValidKeywords", [])
        entry["alpha_mode"] = "MASK" if floats.get("_AlphaCutoffEnable") or "_ALPHATEST_ON" in keywords else "BLEND" if floats.get("_SurfaceType") == 1 else "OPAQUE"
        entry["alpha_cutoff"] = floats.get("_AlphaCutoff", .5)
        entry["double_sided"] = bool(floats.get("_DoubleSidedEnable") or floats.get("_CullMode", 2) == 0)
        entry["conversion"] = "保留真实底色、作者UV、透明裁切及双面标记；原始材质全部字段保留。原游戏Shader、风动及光照未重建。"
        return key, entry

    def open_bundle(self, path):
        bundle = self.external_bundles[path.name] = BundleIndex(path)
        container = {}
        for node in bundle.nodes:
            file = self.env.load_file(NodeStream(bundle, node), name=node["name"])
            if node["name"].lower().startswith("cab-") and not node["name"].endswith(".resS"):
                container.update(file.container.items())
        return container


def scene_bounds(path):
    scene = json.loads(path.read_text("utf-8"))
    worlds, low, high = {}, np.full(3, np.inf), np.full(3, -np.inf)
    files = {"scene.json": fingerprint(path)}
    for node in scene["nodes"]:
        matrix = worlds[node["id"]] = worlds.get(node["parent"], np.eye(4)) @ np.asarray(node["matrix"])
        for mesh in node["meshes"]:
            file = path.parent / mesh["file"]
            with np.load(file) as data:
                points = data["vertices"] @ matrix[:3, :3].T + matrix[:3, 3]
            # Blender(x,z,y)回到来源Unity局部(x,y,z)。
            points = points[:, [0, 2, 1]]
            low, high = np.minimum(low, points.min(0)), np.maximum(high, points.max(0))
            files[mesh["file"]] = fingerprint(file)
    if not np.isfinite([low, high]).all():
        raise ValueError("原型没有有限几何包围盒")
    return [low.tolist(), high.tolist()], files


def run(directory, sample):
    game = Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))["game_directory"])
    journal = Journal(directory)
    journal.update(operation="导出双层原型来源", phase="A0原型来源核对", status="进行中")
    source = directory / "来源"
    prior = ROOT / "local-data/断箭/批次/BA-26273833-010/来源/依赖包节点定位.json"
    dependency_path = source / "依赖包节点定位.json"
    if not dependency_path.exists():
        dependency_path.write_bytes(prior.read_bytes())
        atomic_json(source / "依赖定位复用凭证.json", {"source": prior.relative_to(ROOT).as_posix(), **fingerprint(prior), "condition": "真实输入摘要已全量核对一致"})
    worker = FullMapExtractor(directory, game)
    results = []
    try:
        assets = worker.load("sharedassets7.assets")
        worker.load("globalgamemanagers.assets")
        worker.load("resources.assets")
        bundle = next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("mapresources_assets_all_*.bundle"))
        map_container = worker.open_bundle(bundle)
        banks = json.loads((source / "Airbase-原始放置.json").read_text("utf-8"))["banks"]
        candidates = []
        for bank in banks:
            if not bank["enabled"]:
                continue
            if not bank["catalog"]:
                raise ValueError("启用Bank缺少正式目录关联：" + bank["resource_path"])
            path = bank["catalog"]["internal_id"]
            pointer = map_container[path]
            candidates.append({"source_key": f"catalog:{bank['prefab_key']}", "path": path, "name": bank["prefab_key"],
                               "category": "building" if "/buildings/" in bank["resource_path"] else "decor", "pointer": pointer,
                               "bank": bank["resource_path"]})
        vegetation = json.loads((source / "植被来源索引.json").read_text("utf-8"))
        for terrain in vegetation:
            fields = terrain["detail_database"]
            for field, pointer_field, category in [("m_TreePrototypes", "prefab", "tree"), ("m_DetailPrototypes", "prototype", "grass")]:
                for index, item in enumerate(fields[field]):
                    ptr = item[pointer_field]
                    obj = worker.deref(ptr, assets)
                    name = obj.peek_name()
                    candidates.append({"source_key": f"{obj.assets_file.name}:{obj.path_id}",
                                       "path": f"source/{obj.assets_file.name}/{obj.path_id}/{name}-{obj.path_id}.prefab", "name": name,
                                       "category": category, "pointer": UnityPy.classes.PPtr(m_FileID=0, m_PathID=obj.path_id, assetsfile=obj.assets_file),
                                       "terrain_data": terrain["data_id"], "prototype_index": index})
        if sample:
            chosen = {"decor_fance_1", "decor_railroad", "decor_Rock_01_Low", "building_Air_Big_Hangar_1"}
            candidates = [row for row in candidates if row["name"] in chosen or row["category"] in ("tree", "grass")]
        seen = set()
        for index, candidate in enumerate(candidates):
            key = candidate["source_key"]
            if key in seen:
                continue
            seen.add(key)
            journal.update(current=index + 1, total=len(candidates), source_key=key)
            pointer = candidate.pop("pointer")
            row = {**candidate, "asset_id": "model:BA_SCENE_" + hashlib.sha256(key.encode()).hexdigest()[:20] + "_v001"}
            try:
                exported = worker.export(row["path"], row["category"], retry_incomplete=True, source_ptr=pointer)
                bounds, files = scene_bounds(Path(exported["scene"]))
                row.update(status="来源结构通过，待工程及画面验证", exported=exported, source_local_bounds=bounds, files=files,
                           source_node=pointer.deref().assets_file.name, source_id=pointer.path_id)
            except Exception as error:
                row.update(status="失败", error=repr(error))
            results.append(row)
            atomic_json(source / ("全类别样本原型.json" if sample else "全量原型来源.json"), results)
        failed = [r for r in results if r["status"] == "失败"]
        journal.close(status="原型来源核对失败" if failed else "原型来源核对完成", summary={"prototypes": len(results), "failed": len(failed)})
        print(json.dumps({"prototypes": len(results), "failed": failed}, ensure_ascii=False))
        if failed:
            raise ValueError("存在原型来源失败，阻止来源闭合")
    except BaseException as error:
        if journal.thread.is_alive():
            journal.close(status="原型来源失败，保留恢复点", error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    run(directory, args.sample)
