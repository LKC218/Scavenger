"""依据已核验的数据库与骨架锚点，将独立部件合并为静态载具场景。"""

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scene_export", Path(__file__).with_name("导出场景.py"))
scene_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scene_export)


def geometry_plan(root):
    """目录别名证据补充不改变几何，装配选择或矩阵变化才需要重建。"""
    return {"sources": [{k: s.get(k) for k in ("path", "kind", "slot", "turret_id", "binding", "matrix_blender")} for s in root["sources"]], "selected_unit_id": root.get("selected_unit_id"), "selected_option_ids": root.get("selected_option_ids"), "status": root["status"], "warnings": root["warnings"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-001")
    parser.add_argument("--category", choices=["air", "ground", "all"], default="all")
    parser.add_argument("--name", action="append")
    args = parser.parse_args()
    batch = (ROOT / "local-data/断箭/批次" / args.batch).resolve()
    worker = scene_export.Extractor(batch)
    mapping = json.loads((batch / "装配映射.json").read_text(encoding="utf-8"))
    roots = sorted(mapping["roots"], key=lambda r: (r["category"] != "air", r["root_path"]))
    records = []
    for root in roots:
        category = root["category"]
        if category not in ("air", "ground"):
            category = "ground"
        if args.category not in ("all", category) or args.name and Path(root["root_path"]).stem not in args.name:
            continue
        name = Path(root["root_path"]).stem
        target = batch / "装配场景" / category / name
        marker = target / "scene.json"
        if marker.exists():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("assembly") == root or geometry_plan(previous["assembly"]) == geometry_plan(root):
                records.append({"name": name, "category": category, "reused": True, "scene": str(marker)})
                continue
            if (target / "source.blend").exists():
                raise ValueError(f"装配计划已变化且已有构建工程，请先保留旧输出再重建：{name}")
            marker.rename(target / f"scene-装配刷新前-{time.time_ns()}.json")
        target.mkdir(parents=True, exist_ok=True)
        scene = {"name": name, "source_path": root["root_path"], "category": category, "nodes": [], "materials": {}, "warnings": list(root["warnings"]), "pose": "按数据库基础预设与序列化锚点合并；各部件使用初始静态蒙皮；无运行时动画", "assembly": root, "skipped": []}
        start = time.time()
        for index, source in enumerate(root["sources"]):
            try:
                if source.get("binding") == "unresolved" or "matrix_blender" not in source:
                    raise ValueError("原始锚点尚未核验")
                data = worker.export(source["path"], category if source["kind"] == "root" else "attachment", retry_incomplete=True)
                path = Path(data["scene"])
                component = json.loads(path.read_text(encoding="utf-8"))
                wrapper = f"source-{index}"
                matrix = np.asarray(source["matrix_blender"], dtype=float)
                if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
                    raise ValueError("无效装配矩阵")
                scene["nodes"].append({"id": wrapper, "name": Path(source["path"]).stem, "parent": None, "matrix": matrix.tolist(), "meshes": []})
                for original in component["nodes"]:
                    node = copy.deepcopy(original)
                    node["id"] = f"{wrapper}:{node['id']}"
                    node["parent"] = f"{wrapper}:{node['parent']}" if node["parent"] else wrapper
                    for mesh in node["meshes"]:
                        mesh["file"] = str((path.parent / mesh["file"]).resolve())
                    scene["nodes"].append(node)
                scene["materials"].update(component["materials"])
                scene["warnings"].extend(f"{component['name']}：{w}" for w in component["warnings"])
                scene["skipped"].extend(component.get("skipped", []))
            except Exception as error:
                scene["warnings"].append(f"装配来源未合并：{source['path']}：{error}")
        scene["mesh_count"] = sum(len(n["meshes"]) for n in scene["nodes"])
        if scene["mesh_count"]:
            scene_export.save_json(marker, scene)
        record = {"name": name, "category": category, "scene": str(marker), "meshes": scene["mesh_count"], "warnings": scene["warnings"], "sources": len(root["sources"]), "seconds": round(time.time()-start, 2)}
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        scene_export.save_json(batch / f"装配记录-{args.category}.json", records)


if __name__ == "__main__":
    main()
