"""根据9公里导航边界、真实原型包围盒和植被来源冻结全图范围及条目。"""

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from 地图执行记录 import ROOT, TASK, Journal, atomic_json, fingerprint


def corners(bounds):
    low, high = np.asarray(bounds)
    return np.array([[x, y, z] for x in [low[0], high[0]] for y in [low[1], high[1]] for z in [low[2], high[2]]])


def freeze(directory, revision):
    journal = Journal(directory)
    journal.update(operation="冻结有效范围及来源条目", phase="A0范围冻结")
    if not revision or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in revision):
        raise ValueError("冻结修订名只允许字母数字及连字符")
    output = directory / "范围冻结" / revision
    output.mkdir(parents=True, exist_ok=False)
    try:
        source = directory / "来源"
        objects = json.loads((source / "全图来源审计.json").read_text("utf-8"))["objects"]
        by_object = {int(row["source_key"].split(":")[1]): row for row in objects}
        meshes = json.loads((source / "直接网格范围.json").read_text("utf-8"))
        prototypes = json.loads((source / "全量原型来源.json").read_text("utf-8"))
        if len(prototypes) != 133 or any(r["status"] == "失败" for r in prototypes):
            raise ValueError("当前冻结需要119组Bank和14种植被原型完整来源")
        banks = json.loads((source / "Airbase-原始放置.json").read_text("utf-8"))["banks"]
        by_prefab = {r["source_key"].removeprefix("catalog:"): r for r in prototypes if r["source_key"].startswith("catalog:")}
        bank_records = []
        for bank in banks:
            journal.update(operation="逐Bank包围盒与跨界对账", current=bank["resource_path"])
            prototype = by_prefab[bank["prefab_key"]]
            pts = corners(prototype["source_local_bounds"])
            matrices = np.asarray(bank["matrices"])
            transformed = np.einsum("nij,kj->nki", matrices[:, :3, :3], pts) + matrices[:, None, :3, 3]
            low, high = transformed.min(1), transformed.max(1)
            intersects = (high[:, 0] >= 0) & (low[:, 0] <= 9000) & (high[:, 2] >= 0) & (low[:, 2] <= 9000)
            origins = np.asarray(bank["trs"])[:, [0, 2]]
            core = ((origins >= 0) & (origins <= 9000)).all(1)
            ids = np.arange(bank["count"], dtype=np.int32)
            file = output / f"Bank-{bank['source_id']}.npz"
            np.savez_compressed(file, source_indices=ids, trs=np.asarray(bank["trs"], dtype=np.float32),
                                intersects=intersects, core_origin=core, world_bounds=np.stack([low, high], axis=1).astype(np.float32))
            bank_records.append({"source_key": f"{bank['source_node']}:{bank['source_id']}", "resource_path": bank["resource_path"],
                                 "asset_id": prototype["asset_id"], "category": prototype["category"], "source_count": len(ids),
                                 "enabled": bank["enabled"], "required_intersections": int(intersects.sum()),
                                 "core_origins": int(core.sum()), "cross_boundary": int((intersects & ~core).sum()),
                                 "excluded_outside": int((~intersects).sum()), "file": file.name, "identity": fingerprint(file)})
        vegetation = json.loads((source / "植被来源索引.json").read_text("utf-8"))
        tree_records = []
        for terrain in vegetation:
            if not terrain["tree_instances"]:
                continue
            trees = json.loads((directory / terrain["tree_file"]).read_text("utf-8"))
            positions = np.array([[row["position"][axis] for axis in "xyz"] for row in trees], dtype=np.float32)
            size = np.array([terrain["scale"]["x"] * (terrain["resolution"] - 1), terrain["scale"]["y"], terrain["scale"]["z"] * (terrain["resolution"] - 1)])
            world = np.asarray(terrain["world_matrix"])
            positions = (positions * size) @ world[:3, :3].T + world[:3, 3]
            indices = np.array([r["index"] for r in trees], dtype=np.uint8)
            params = np.array([[r["widthScale"], r["heightScale"], r["rotation"]] for r in trees], dtype=np.float32)
            intersects = np.zeros(len(trees), dtype=bool)
            for index, proto in enumerate(terrain["detail_database"]["m_TreePrototypes"]):
                selected = np.flatnonzero(indices == index)
                p = next(r for r in prototypes if r["source_key"] == f"{terrain['data_node']}:{proto['prefab']['m_PathID']}")
                pts = corners(p["source_local_bounds"])
                scaled = pts[None, :, :] * params[selected][:, [0, 1, 0]][:, None, :]
                angle = params[selected, 2]
                c, s = np.cos(angle)[:, None], np.sin(angle)[:, None]
                x = scaled[:, :, 0] * c + scaled[:, :, 2] * s + positions[selected, None, 0]
                z = -scaled[:, :, 0] * s + scaled[:, :, 2] * c + positions[selected, None, 2]
                intersects[selected] = (x.max(1) >= 0) & (x.min(1) <= 9000) & (z.max(1) >= 0) & (z.min(1) <= 9000)
            file = output / f"树木-{terrain['data_id']}.npz"
            np.savez_compressed(file, source_indices=np.arange(len(trees), dtype=np.int32), positions=positions.astype(np.float32),
                                prototype_indices=indices, parameters=params, intersects=intersects)
            tree_records.append({"terrain_data": terrain["data_id"], "source_count": len(trees), "required_intersections": int(intersects.sum()),
                                 "excluded_outside": int((~intersects).sum()), "file": file.name, "identity": fingerprint(file),
                                 "activation_evidence": "来源/森林启用逻辑.json；Load.MoveNext读取TreeManager并SetActive(true)，不能只按编辑器初始禁用排除"})
        excluded_lod = set()
        for row in objects:
            for component in row["components"]:
                if component["type"] != "LODGroup":
                    continue
                lods = component["data"]["m_LODs"]
                keep = {r["renderer"]["m_PathID"] for r in lods[0]["renderers"]}
                excluded_lod.update(r["renderer"]["m_PathID"] for lod in lods[1:] for r in lod["renderers"] if r["renderer"]["m_PathID"] not in keep)
        direct = []
        for mesh in meshes:
            obj = by_object[mesh["gameobject"]]
            renderer = next((c for c in obj["components"] if c["type"] == "MeshRenderer"), None)
            low, high = mesh["world_bounds"]
            outside = high[0] < 0 or low[0] > 9000 or high[2] < 0 or low[2] > 9000
            reason = "没有显示渲染器" if not renderer else "层级禁用" if not obj["active_hierarchy"] else "渲染器禁用" if not renderer["data"]["m_Enabled"] else "同组较低LOD，保留为源细节层" if renderer["id"] in excluded_lod else "完全在冻结范围外" if outside else None
            direct.append({**mesh, "required": reason is None, "exclusion_reason": reason})
        result = {"schema": 1, "task": TASK, "version": "V001", "scope_revision": revision, "status": "frozen-source-scope",
                  "source_bounds_xz": [0, 9000, 0, 9000], "area_m2": 81000000, "source_origin": [4425, 136, 4850],
                  "runtime_bounds": {"minX": -4425, "maxX": 4575, "minZ": -4150, "maxZ": 4850},
                  "conversion": "x-4425,y-136,4850-z；保留旧区域原点，不居中全图", "chunk_m": 125,
                  "range_evidence": {"map_settings": fingerprint(source / "Airbase-地图设置.json"), "boundary_objects": [204, 77, 216, 154],
                                     "boundary_note": "四个9公里导航障碍的内边界为0和9000；九块高细节地形覆盖同一标称范围。实际支撑及接缝仍须A2逐块核验。"},
                  "banks": bank_records, "trees": tree_records, "direct_meshes": direct,
                  "detail_sources": [{"terrain_data": r["data_id"], "nonzero_cells": r["coverage_nonzero"], "density_sum": r["coverage_sum"],
                                      "source_file": r["density_file"], "identity": r["density_identity"],
                                      "policy": "保留原始密度和种类；运行摆放算法及画面仍待A1验证，非源树木位置表"} for r in vegetation if r["coverage_bytes"]],
                  "excluded_components": [{"id": 984, "object": "PassOutline", "reason": "轮廓后处理OutlineCustomPass，保留原字节；不属于静态来源布局"},
                                          {"id": 1020, "object": "Reflection Probe", "reason": "原HDRP反射数据，保留原字节；完整Shader及光照不在静态承诺内"}],
                  "counts": {"bank_source": sum(r["source_count"] for r in bank_records), "bank_required": sum(r["required_intersections"] for r in bank_records),
                             "trees_source": sum(r["source_count"] for r in tree_records), "trees_required": sum(r["required_intersections"] for r in tree_records),
                             "direct_meshes_source": len(direct), "direct_meshes_required": sum(r["required"] for r in direct), "prototypes": len(prototypes)},
                  "gate_scope": "只确认A0输入、来源身份与有效范围；不代表产物完整、画面、性能或入库通过"}
        atomic_json(output / "范围与条目.json", result)
        receipt = fingerprint(output / "范围与条目.json")
        journal.state["gates"]["A0"] = "来源与范围通过"
        journal.close(status="A0通过，继续A1", phase="A1双层资产样本", operation="独立原型工程及回读验证", frozen_scope=receipt)
        print(json.dumps({"counts": result["counts"], "scope_sha256": receipt["sha256"]}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status="范围冻结失败，保留输出", error=repr(error))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--revision", default="V001")
    args = parser.parse_args()
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    freeze(directory, args.revision)
