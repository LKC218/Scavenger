"""读取独立原型的源碰撞、导航障碍及正常静态组件，不改变游戏或原型来源。"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from 导出全图原型 import FullMapExtractor
from 导出场景 import transform_matrix
from 地图执行记录 import ROOT, Journal, atomic_json, fingerprint


def run(directory, revision):
    output = directory / "来源" / f"原型物理-{revision}.json"
    if output.exists():
        raise FileExistsError("原型物理快照已存在")
    journal = Journal(directory)
    worker = FullMapExtractor(directory, Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))["game_directory"]))
    try:
        worker.load("globalgamemanagers.assets")
        worker.load("resources.assets")
        worker.load("sharedassets7.assets")
        worker.open_bundle(next((worker.game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("mapresources_assets_all_*.bundle")))
        rows = json.loads((directory / "来源/全量原型来源.json").read_text("utf-8"))
        results = {}
        for index, row in enumerate(rows):
            journal.update(phase="A1双层资产样本", operation="源原型碰撞及锚点审计", current=index + 1, total=len(rows))
            af = worker.env.cabs.get(row["source_node"].lower()) or worker.env.cabs.get(row["source_node"])
            if af is None:
                worker.load_external(row["source_node"])
                af = worker.env.cabs.get(row["source_node"].lower()) or worker.env.cabs[row["source_node"]]
            root = af.objects[int(row["source_id"])]
            colliders, obstacles, static_components = [], [], []
            def walk(go_obj, parent_matrix, parent_active):
                go = go_obj.read_typetree()
                components = [worker.deref(p["component"], go_obj.assets_file) for p in go["m_Component"]]
                transform = next(c for c in components if c.type.name == "Transform").read_typetree()
                matrix = parent_matrix @ transform_matrix(transform)
                active = bool(parent_active and go["m_IsActive"])
                for component in components:
                    kind = component.type.name
                    if kind.endswith("Collider") or kind == "NavMeshObstacle":
                        fields = component.read_typetree()
                        record = {"source_node": component.assets_file.name, "source_id": component.path_id,
                                  "name": go["m_Name"], "type": kind, "active": active,
                                  "enabled": bool(fields.get("m_Enabled", True)), "trigger": bool(fields.get("m_IsTrigger", False)),
                                  "matrix_unity": matrix.tolist(), "fields": fields,
                                  "raw_sha256": hashlib.sha256(component.get_raw_data()).hexdigest()}
                        if kind == "MeshCollider" and fields.get("m_Mesh", {}).get("m_PathID"):
                            mesh = worker.deref(fields["m_Mesh"], component.assets_file)
                            record["mesh_source"] = {"node": mesh.assets_file.name, "id": mesh.path_id,
                                "raw_sha256": hashlib.sha256(mesh.get_raw_data()).hexdigest()}
                        (obstacles if kind == "NavMeshObstacle" else colliders).append(record)
                    elif kind == "MonoBehaviour":
                        fields = component.read_typetree()
                        script = worker.deref(fields["m_Script"], component.assets_file).read_typetree()
                        name = script["m_Namespace"] + "." + script["m_ClassName"]
                        if any(word in name for word in ("BuildingInitializer", "BakerMarker", "BuildingMarker", "RemainInitializer")):
                            static_components.append({"class": name, "source_id": component.path_id, "active": active, "fields": fields})
                for child in transform["m_Children"]:
                    tr = worker.deref(child, go_obj.assets_file).read_typetree()
                    walk(worker.deref(tr["m_GameObject"], go_obj.assets_file), matrix, active)
            walk(root, np.eye(4), True)
            physical = [c for c in colliders if c["active"] and c["enabled"] and not c["trigger"]]
            if physical:
                policy = {"mode": "source", "movement": "按启用非触发源Collider生成区域碰撞", "ballistics": "同源静态碰撞表面"}
            elif row["category"] == "tree":
                policy = {"mode": "derived_trunk", "movement": "来源无启用Collider；按源树干几何派生小柱并留证，不能使用整棵树冠方盒", "ballistics": "派生树干；树冠不作实心阻挡"}
            else:
                policy = {"mode": "visual_only", "movement": "来源无启用非触发Collider，不额外制造实心方盒", "ballistics": "无碰撞，保留视觉原型"}
            bounds = np.asarray(row["source_local_bounds"])
            results[row["asset_id"]] = {"source_key": row["source_key"], "source_node": row["source_node"], "source_id": row["source_id"],
                "category": row["category"], "colliders": colliders, "nav_obstacles": obstacles, "static_components": static_components,
                "policy": policy, "anchor": {"author_origin_unity_m": [0, 0, 0],
                    "suggested_bottom_center_unity_m": [(bounds[0, 0] + bounds[1, 0]) / 2, bounds[0, 1], (bounds[0, 2] + bounds[1, 2]) / 2],
                    "source_origin_retained": True}, "source_manifest": fingerprint(Path(row["exported"]["scene"]))}
        atomic_json(output, {"schema": 1, "assets": results, "source_manifest": fingerprint(directory / "来源/全量原型来源.json"),
                             "scope": "只读来源及类别策略，不等于运行碰撞或通行验证通过"})
        journal.close(status="原型物理来源冻结完成，A1继续", completed=len(results))
        print(json.dumps({"assets": len(results), "colliders": sum(len(r["colliders"]) for r in results.values()),
                          "obstacles": sum(len(r["nav_obstacles"]) for r in results.values())}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status="原型物理审计失败", error=repr(error))
        raise
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / "local-data/断箭/批次")
    if not args.revision or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.revision):
        raise ValueError("无效修订名")
    run(directory, args.revision)
