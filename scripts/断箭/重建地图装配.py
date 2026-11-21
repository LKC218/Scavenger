"""用稳定原型引用重建旧2公里布局，生成可编辑链接工程和逐实例回归证据。"""

import argparse
import json
from pathlib import Path
import shutil
import sys
from mathutils import Vector

import bpy
from mathutils import Matrix, Quaternion
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import Journal, atomic_json, fingerprint


def prototype_matrix(obj):
    def local(item):
        if item.rotation_mode == "QUATERNION":
            rotation = item.rotation_quaternion
        elif item.rotation_mode == "AXIS_ANGLE":
            angle, x, y, z = item.rotation_axis_angle
            rotation = Quaternion(Vector((x, y, z)), angle)
        else:
            rotation = item.rotation_euler.to_quaternion()
        # 未求值链接对象的matrix_local和matrix_world均可能为单位矩阵，保存的TRS通道仍有效。
        return item.matrix_parent_inverse @ Matrix.LocRotScale(item.location, rotation, item.scale)
    matrix = local(obj)
    parent = obj.parent
    while parent is not None:
        matrix = local(parent) @ matrix
        parent = parent.parent
    return matrix


def rebuild(directory, prototype_revision, revision):
    output = directory / "引用装配/2000米" / revision
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    journal.update(phase="A1双层资产样本", operation="从独立原型引用重建2公里")
    try:
        prior = ROOT / "local-data/断箭/批次/BA-26273833-011/交付/2000米/r02"
        baseline = json.loads((prior / "地图运行.json").read_text("utf-8"))
        sources = json.loads((directory / "来源/全量原型来源.json").read_text("utf-8"))
        by_key = {r["source_key"].removeprefix("catalog:"): r for r in sources if r["source_key"].startswith("catalog:")}
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 1
        bpy.ops.import_scene.gltf(filepath=str(prior / baseline["ground"]))
        references = {}
        for index, (key, prototype) in enumerate(baseline["prototypes"].items()):
            journal.update(current=index + 1, total=len(baseline["prototypes"]), prototype=key)
            row = by_key[key]
            source = directory / "独立原型" / prototype_revision / row["asset_id"].split(":")[1]
            record = json.loads((source / "构建凭证.json").read_text("utf-8"))
            if fingerprint(source / "原型.blend")["sha256"] != record["files"]["原型.blend"]["sha256"]:
                raise ValueError("原型工程摘要变化：" + key)
            with bpy.data.libraries.load(str(source / "原型.blend"), link=True) as (available, requested):
                if "模型" not in available.collections:
                    raise ValueError("独立原型缺少模型集合：" + key)
                requested.collections = ["模型"]
            collection = requested.collections[0]
            if collection is None:
                raise ValueError("原型集合链接失败：" + key)
            references[key] = {"asset_id": row["asset_id"], "source_key": row["source_key"], "collection": collection,
                               "blend": source / "原型.blend", "glb": source / "原型.glb", "record": record}
        rotation = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
        checks = []
        for instance in baseline["instances"]:
            reference = references[instance["prototype"]]
            runtime_matrix = Matrix(np.asarray(instance["matrix"]).reshape(4, 4).T.tolist())
            matrix = rotation.inverted() @ runtime_matrix @ rotation
            obj = bpy.data.objects.new(instance["id"], None)
            obj.instance_type = "COLLECTION"
            obj.instance_collection = reference["collection"]
            obj.matrix_world = matrix
            obj["instance_id"] = instance["id"]
            obj["asset_id"] = reference["asset_id"]
            bpy.context.scene.collection.objects.link(obj)
            low, high = np.full(3, np.inf), np.full(3, -np.inf)
            for part in reference["collection"].all_objects:
                if part.type != "MESH":
                    continue
                points = np.empty(len(part.data.vertices) * 3, dtype=np.float32)
                part.data.vertices.foreach_get("co", points)
                # 链接集合未直接加入场景，matrix_world可能尚未求值；按保存的本地父链恢复作者矩阵。
                transform = np.asarray(matrix @ prototype_matrix(part))
                points = points.reshape(-1, 3) @ transform[:3, :3].T + transform[:3, 3]
                low, high = np.minimum(low, points.min(0)), np.maximum(high, points.max(0))
            footprint = np.asarray(instance["footprint"])
            expected = np.array([[footprint[:, 0].min(), -footprint[:, 1].max(), instance["height_bounds"][0]],
                                 [footprint[:, 0].max(), -footprint[:, 1].min(), instance["height_bounds"][1]]])
            error = float(np.max(np.abs(np.array([low, high]) - expected)))
            if error > .002:
                atomic_json(output / "差异实例.json", {"id": instance["id"], "asset_id": reference["asset_id"],
                                                   "actual": [low.tolist(), high.tolist()], "expected": expected.tolist(),
                                                   "instance_matrix": np.asarray(matrix).tolist(),
                                                   "parts": [{"name": part.name, "matrix": np.asarray(prototype_matrix(part)).tolist(),
                                                              "vertices": len(part.data.vertices)} for part in reference["collection"].all_objects if part.type == "MESH"]})
                raise ValueError(f"引用重建的实例包围盒不符：{instance['id']}，误差{error}米")
            checks.append({"instance_id": instance["id"], "asset_id": reference["asset_id"], "bounds_error_m": error})
        bpy.context.view_layer.update()
        for library in bpy.data.libraries:
            library.filepath = bpy.path.relpath(library.filepath, start=str(output))
        blend = output / "Airbase两公里引用装配.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False, relative_remap=False)
        links = [{"prototype_key": key, "asset_id": ref["asset_id"], "source_key": ref["source_key"],
                  "blend_path": ref["blend"].relative_to(directory).as_posix(), "blend_identity": fingerprint(ref["blend"]),
                  "glb_path": ref["glb"].relative_to(directory).as_posix(), "glb_identity": fingerprint(ref["glb"])} for key, ref in references.items()]
        result = {"status": "逐实例引用重建通过，待工程独立重开与场景审图", "baseline": fingerprint(prior / "地图运行.json"),
                  "prototypes": len(links), "instances": len(checks), "links": links, "instance_checks": checks,
                  "max_bounds_error_m": max(r["bounds_error_m"] for r in checks), "blend_identity": fingerprint(blend),
                  "human_acceptance": "待人工验收"}
        atomic_json(output / "引用装配凭证.json", result)
        journal.close(status="2公里引用重建完成，A1继续", summary={"prototypes": len(links), "instances": len(checks)})
    except BaseException as error:
        journal.close(status="2公里引用重建失败，保留恢复点", error=repr(error))
        raise


def verify(directory, revision, run):
    from 构建全图原型 import render_views
    output = directory / "引用装配/2000米" / revision
    evidence = output / "独立核验" / run
    evidence.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    journal.update(phase="A1双层资产样本", operation="引用装配独立重开与实际场景预览")
    try:
        record = json.loads((output / "引用装配凭证.json").read_text("utf-8"))
        blend = output / "Airbase两公里引用装配.blend"
        if fingerprint(blend)["sha256"] != record["blend_identity"]["sha256"]:
            raise ValueError("引用工程摘要变化")
        for row in record["links"]:
            for role in ["blend", "glb"]:
                if fingerprint(directory / row[f"{role}_path"])["sha256"] != row[f"{role}_identity"]["sha256"]:
                    raise ValueError("引用原型依赖变化：" + row["asset_id"])
        bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False)
        if bpy.context.scene.unit_settings.scale_length != 1:
            raise ValueError("引用工程米制设置不符")
        missing = [lib.filepath for lib in bpy.data.libraries if not Path(bpy.path.abspath(lib.filepath)).is_file()]
        if missing:
            raise ValueError("工程存在失效相对链接：" + str(missing))
        instances = [obj for obj in bpy.context.scene.objects if obj.get("asset_id")]
        expected = {r["instance_id"]: r["asset_id"] for r in record["instance_checks"]}
        actual = {obj.get("instance_id"): obj.get("asset_id") for obj in instances}
        if expected != actual or len(instances) != record["instances"]:
            raise ValueError("独立重开后实例引用不完整")
        graph = bpy.context.evaluated_depsgraph_get()
        actual_mesh_instances = sum(1 for item in graph.object_instances if item.is_instance and item.object.type == "MESH")
        if actual_mesh_instances < record["instances"]:
            raise ValueError("链接实例没有实际可渲染网格")
        render_views(evidence, list(bpy.context.scene.objects), "总装配")
        scene = bpy.context.scene
        scene.render.resolution_x, scene.render.resolution_y = 1280, 960
        camera = scene.camera
        camera.data.clip_end = 10000
        for name, location, target, span in [("机库道路", (30, -100, 110), (0, 60, 2), 240),
                                             ("塔台接地", (-230, -220, 130), (-133.5, -100.5, 8), 180)]:
            camera.location = location
            camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()
            camera.data.ortho_scale = span
            scene.render.filepath = str(evidence / f"{name}.png")
            bpy.ops.render.render(write_still=True)
        if fingerprint(blend)["sha256"] != record["blend_identity"]["sha256"]:
            raise ValueError("独立核验改动了源工程")
        result = {"status": "引用工程独立重开通过，待AI场景审图", "human_acceptance": "待人工验收",
                  "instances": len(instances), "actual_mesh_instances": actual_mesh_instances, "libraries": len(bpy.data.libraries),
                  "blend_identity": fingerprint(blend), "assembly_receipt": fingerprint(output / "引用装配凭证.json"),
                  "previews": {p.name: fingerprint(p) for p in evidence.glob("*.png")}}
        atomic_json(evidence / "核验凭证.json", result)
        journal.close(status="2公里引用工程独立核验完成，A1继续", evidence=evidence.relative_to(directory).as_posix())
    except BaseException as error:
        journal.close(status="引用装配独立核验失败，保留恢复点", error=repr(error))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--prototype-revision")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--run", default="r01")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    for name in [args.revision, args.prototype_revision or "unused", args.run]:
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in name):
            raise ValueError("无效修订名")
    if args.verify:
        verify(directory, args.revision, args.run)
    elif args.prototype_revision:
        rebuild(directory, args.prototype_revision, args.revision)
    else:
        parser.error("构建模式需要--prototype-revision")
