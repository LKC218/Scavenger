"""只读审计Airbase全图来源；独立批次、原子进度、摘要核对和中断恢复。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

import numpy as np

from 解析地图 import ROOT, sources
from 解析装配 import BundleIndex


from 地图执行记录 import TASK, Journal, atomic_json, fingerprint


def initialize(directory, prior, journal):
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))
    game = Path(config["game_directory"])
    manifest = Path(config["steam_manifest"])
    build = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text("utf-8")).group(1)
    if build != "26273833":
        raise ValueError(f"构建已改变：{build}；不得复用旧来源映射")
    old = json.loads((prior / "输入清单.json").read_text("utf-8"))
    files = [row["path"] for row in old["files"]]
    files.extend(["GameAssembly.dll", "BrokenArrow_Data/il2cpp_data/Metadata/global-metadata.dat"])
    result = {"schema": 1, "task": TASK, "purpose": "Airbase全图V001来源与范围审计",
              "created_utc": datetime.now(timezone.utc).isoformat(), "build": build,
              "steam_manifest": fingerprint(manifest), "source_open_mode": "rb", "files": [],
              "tools": {"python": sys.version, "UnityPy": importlib.metadata.version("UnityPy"), "numpy": np.__version__},
              "source_programs": {}, "disk": {"free": shutil.disk_usage(directory).free}}
    old_by_path = {row["path"]: row for row in old["files"]}
    for relative in dict.fromkeys(files):
        journal.update(operation="核对真实输入摘要", current_input=relative)
        identity = fingerprint(game / relative, journal)
        baseline = old_by_path.get(relative)
        row = {"path": relative, **identity,
               "prior_sha256": baseline["sha256"] if baseline else None,
               "matches_prior": identity["sha256"] == baseline["sha256"] if baseline else None}
        result["files"].append(row)
        atomic_json(directory / "输入清单.json", result)
        if baseline and not row["matches_prior"]:
            raise ValueError(f"原游戏输入与010冻结摘要不同：{relative}")
    bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
    try:
        result["engine"] = bundle.engine
    finally:
        bundle.file.close()
    for relative in ["scripts/断箭/审计全图.py", "scripts/断箭/解析地图.py", "scripts/断箭/解析装配.py",
                     "docs/Airbase全图与场景资产双层入库-实施计划.md"]:
        result["source_programs"][relative] = fingerprint(ROOT / relative)
    atomic_json(directory / "输入清单.json", result)
    journal.update(operation="重新解析实际场景与放置", build=build, engine=result["engine"])
    summary = sources(game, directory)
    atomic_json(directory / "来源/来源解析摘要.json", summary)
    return game


def summarize(directory):
    source = directory / "来源"
    scene = json.loads((source / "Airbase-场景结构.json").read_text("utf-8"))
    banks = json.loads((source / "Airbase-原始放置.json").read_text("utf-8"))["banks"]
    transforms = {c["id"]: c["data"] for row in scene for c in row["components"] if c["type"] == "Transform"}
    active_by_transform = {c["id"]: row["active"] for row in scene for c in row["components"] if c["type"] == "Transform"}

    def enabled(tid):
        parent = transforms[tid]["m_Father"]["m_PathID"]
        return bool(active_by_transform[tid] and (not parent or enabled(parent)))

    objects = []
    for row in scene:
        transform = next(c["id"] for c in row["components"] if c["type"] == "Transform")
        objects.append({"source_key": f"level7:{row['id']}", "name": row["name"],
                        "active_self": bool(row["active"]), "active_hierarchy": enabled(transform),
                        "components": row["components"], "world_matrix": row["world_matrix"]})
    audit_banks = []
    for bank in banks:
        trs = np.asarray(bank["trs"])
        audit_banks.append({k: bank[k] for k in ["resource_path", "source_node", "source_id", "raw_sha256", "prefab_key", "enabled", "count", "catalog"]})
        audit_banks[-1]["origin_bounds"] = [trs[:, :3].min(0).tolist(), trs[:, :3].max(0).tolist()] if len(trs) else None
    audit = {"schema": 1, "task": TASK, "objects": objects, "banks": audit_banks,
             "summary": {"scene_objects": len(objects), "banks": len(banks), "bank_placements": sum(b["count"] for b in banks),
                         "scripts": dict(Counter(c["data"].get("script") or "未知脚本" for r in scene for c in r["components"] if c["type"] == "MonoBehaviour"))},
             "coverage_status": "未冻结：尚需植被、直接对象、水面与有效边界核对", "gate": "A0进行中"}
    atomic_json(source / "全图来源审计.json", audit)
    return audit["summary"]


def details(directory, journal):
    import UnityPy
    from 解析装配 import NodeStream
    from 地图来源类型树 import corrected_generator, read_component, restore_assemblies

    game = Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))["game_directory"])
    assemblies = ROOT / "tmp/Airbase全图-V001-01a1a602/组件程序集"
    restore_assemblies(game, assemblies, directory / "组件程序集恢复.log", journal)
    bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
    env, files = UnityPy.Environment(), {}
    env.typetree_generator = corrected_generator(assemblies)
    try:
        for name in ["globalgamemanagers", "globalgamemanagers.assets", "resources.assets", "level7", "sharedassets7.assets"]:
            files[name] = env.load_file(NodeStream(bundle, next(n for n in bundle.nodes if n["name"] == name)), name=name)
        level = files["level7"]
        component_results = []
        for obj in level.objects.values():
            if obj.type.name != "MonoBehaviour":
                continue
            journal.update(operation="逐组件完整字节与原生对象头核对", current_object=obj.path_id)
            header = obj.parse_monobehaviour_head()
            script = header.m_Script.deref().read_typetree() if header.m_Script.path_id else {}
            row = {"node": "level7", "id": obj.path_id, "script": script.get("m_ClassName"),
                   "assembly": script.get("m_AssemblyName"), "bytes": obj.byte_size,
                   "raw_sha256": hashlib.sha256(obj.get_raw_data()).hexdigest()}
            try:
                tree = read_component(obj)
                atomic_json(directory / "来源/组件完整字段" / f"level7-{obj.path_id}.json", tree)
                row["status"] = "通过"
            except Exception as error:
                row.update(status="失败", error=str(error))
            component_results.append(row)
        atomic_json(directory / "来源/组件解析覆盖.json", {"header_policy": "UnityPy原生头替换生成器头；原生PPtr逐一比对；严格检查完整字节",
                                                  "objects": component_results})
        terrain_rows = json.loads((directory / "来源/Airbase-地形索引.json").read_text("utf-8"))
        terrain_results = []
        for terrain in terrain_rows:
            journal.update(operation="读取Terrain植被和细节原始数据", current_object=terrain["data_id"])
            obj = files[terrain["data_node"]].objects[terrain["data_id"]]
            tree = obj.read_typetree()
            database = tree["m_DetailDatabase"]
            instances = database.pop("m_TreeInstances", [])
            patches = database.pop("m_Patches", [])
            target = directory / "来源/植被原始数据"
            target.mkdir(exist_ok=True)
            tree_file = target / f"树木-{obj.path_id}.json"
            atomic_json(tree_file, instances)
            coverage, indices, offsets, index_offsets = [], [], [0], [0]
            for patch in patches:
                coverage.extend(patch["coverage"])
                indices.extend(patch["layerIndices"])
                offsets.append(len(coverage))
                index_offsets.append(len(indices))
            file = target / f"细节密度-{obj.path_id}.npz"
            np.savez_compressed(file, coverage=np.asarray(coverage, dtype=np.uint8), layer_indices=np.asarray(indices, dtype=np.int32),
                                coverage_offsets=np.asarray(offsets, dtype=np.int64), index_offsets=np.asarray(index_offsets, dtype=np.int64))
            result = {**terrain, "source_sha256": hashlib.sha256(obj.get_raw_data()).hexdigest(), "detail_database": database,
                      "tree_instances": len(instances), "tree_file": tree_file.relative_to(directory).as_posix(),
                      "tree_identity": fingerprint(tree_file), "patches": len(patches), "coverage_bytes": len(coverage),
                      "coverage_nonzero": int(np.count_nonzero(coverage)), "coverage_sum": int(np.sum(coverage, dtype=np.uint64)),
                      "density_file": file.relative_to(directory).as_posix(), "density_identity": fingerprint(file)}
            terrain_results.append(result)
            atomic_json(directory / "来源/植被来源索引.json", terrain_results)
        scene = json.loads((directory / "来源/Airbase-场景结构.json").read_text("utf-8"))
        mesh_results = []
        for row in scene:
            meshfilter = next((c for c in row["components"] if c["type"] == "MeshFilter"), None)
            if not meshfilter or not meshfilter["data"]["m_Mesh"]["m_PathID"]:
                continue
            ptr = meshfilter["data"]["m_Mesh"]
            if ptr["m_FileID"]:
                external = Path(level.externals[ptr["m_FileID"] - 1].path).name
                if external == "unity default resources" and external not in env.files:
                    builtin = game / "BrokenArrow_Data/Resources" / external
                    identity = fingerprint(builtin)
                    manifest_path = directory / "输入清单.json"
                    manifest = json.loads(manifest_path.read_text("utf-8"))
                    relative = builtin.relative_to(game).as_posix()
                    existing = next((r for r in manifest["files"] if r["path"] == relative), None)
                    if existing and existing["sha256"] != identity["sha256"]:
                        raise ValueError("内置资源摘要发生变化")
                    if not existing:
                        manifest["files"].append({"path": relative, **identity})
                        atomic_json(manifest_path, manifest)
                    env.load_file(str(builtin))
            mesh = UnityPy.classes.PPtr(**ptr, assetsfile=level).deref()
            journal.update(operation="读取直接网格包围盒与规模", current_object=row["id"])
            data = mesh.read()
            aabb = data.m_LocalAABB
            center, extent = np.array([getattr(aabb.m_Center, axis) for axis in "xyz"]), np.array([getattr(aabb.m_Extent, axis) for axis in "xyz"])
            corners = np.array([center + extent * [x, y, z] for x in [-1, 1] for y in [-1, 1] for z in [-1, 1]])
            matrix = np.asarray(row["world_matrix"])
            corners = corners @ matrix[:3, :3].T + matrix[:3, 3]
            mesh_results.append({"gameobject": row["id"], "name": row["name"], "mesh_node": mesh.assets_file.name, "mesh_id": mesh.path_id,
                                 "world_bounds": [corners.min(0).tolist(), corners.max(0).tolist()],
                                 "vertices": data.m_VertexData.m_VertexCount, "indices": sum(s.indexCount for s in data.m_SubMeshes),
                                 "index_buffer_bytes": len(data.m_IndexBuffer), "vertex_buffer_bytes": len(data.m_VertexData.m_DataSize)})
        atomic_json(directory / "来源/直接网格范围.json", mesh_results)
        rm = next(o for o in files["globalgamemanagers"].objects.values() if o.type.name == "ResourceManager").read_typetree()
        atomic_json(directory / "来源/地图关联资源路径.json", [(key, ptr) for key, ptr in rm["m_Container"] if "airbase" in key.lower() or "forest" in key.lower()])
        return {"components": len(component_results), "component_failures": [r for r in component_results if r["status"] == "失败"],
                "terrain_data": len(terrain_results), "tree_instances": sum(r["tree_instances"] for r in terrain_results),
                "detail_coverage_bytes": sum(r["coverage_bytes"] for r in terrain_results), "direct_meshes": len(mesh_results)}
    finally:
        bundle.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--prior", default="local-data/断箭/批次/BA-26273833-010")
    parser.add_argument("--summarize", action="store_true")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    if not args.summarize and not args.details:
        directory.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    try:
        if args.details:
            summary = details(directory, journal)
            journal.close(status="详细来源解析完成，A0继续", operation="下一步冻结范围与覆盖", summary=summary)
            print(json.dumps(summary, ensure_ascii=False))
            return
        if not args.summarize:
            initialize(directory, (ROOT / args.prior).resolve(), journal)
        summary = summarize(directory)
        journal.close(status="来源基线完成，A0继续", operation="下一步解析植被及直接对象", summary=summary)
        print(json.dumps(summary, ensure_ascii=False))
    except BaseException as error:
        journal.close(status="失败，保留恢复点", error=str(error), traceback=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
