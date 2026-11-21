"""独立构建及回读全图原型工程、交换GLB和四向材质预览。由后台Blender执行。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 构建场景 import Builder
from 地图执行记录 import Journal, atomic_json, fingerprint
from 地图表面派生 import unique_surfaces


class PrototypeBuilder(Builder):
    def __init__(self, source, data, mask_layout, material_metadata=None):
        super().__init__(source, data, mask_layout)
        self.material_metadata = material_metadata or {}

    def mesh(self, entry, parent):
        super().mesh(entry, parent)
        file = self.resolve(entry["file"])
        with np.load(file) as arrays:
            if "export_vertex_identity" not in arrays:
                return
            identity = arrays["export_vertex_identity"]
        obj = self.collection.objects[self.report["meshes"][-1]["name"]]
        attribute = obj.data.attributes.new(name="_source_vertex", type="FLOAT", domain="POINT")
        attribute.data.foreach_set("value", identity)

    def material(self, mat_id):
        if mat_id in self.materials:
            return self.materials[mat_id]
        entry = self.data["materials"][mat_id]
        mat = bpy.data.materials.new(entry["name"])
        mat.use_nodes = True
        mat.use_backface_culling = not entry.get("double_sided", False)
        semantic = self.material_metadata.get(mat_id, {})
        alpha_mode = semantic.get("static_day_alpha", entry.get("alpha_mode", "OPAQUE"))
        mat["source_material"] = mat_id
        mat["alpha_mode"] = alpha_mode
        mat["alpha_cutoff"] = float(semantic.get("static_alpha_cutoff", entry.get("alpha_cutoff", .5)))
        if alpha_mode != "OPAQUE":
            mat.surface_render_method = "DITHERED"
        tree = mat.node_tree
        bsdf = tree.nodes.get("Principled BSDF")
        color = tuple(entry.get("color", [1, 1, 1, 1]))
        mat["base_color_factor"] = (*color[:3], color[3] if alpha_mode == "BLEND" else 1.0)
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = color[3] if alpha_mode == "BLEND" else 1.0
        bsdf.inputs["Roughness"].default_value = .9
        file = entry.get("basecolor")
        if file:
            info = next((v for v in entry.get("textures", {}).values() if v.get("file") == file), {})
            texture = self.texture(tree, file, info, "sRGB", "来源底色及Alpha")
            if texture is None:
                raise ValueError("必要底色未能加载：" + file)
            # Unity底色RGB与裁切Alpha是独立通道；与glTF回读保持相同读取语义。
            # STRAIGHT会在插值时用Alpha改变RGB，令围栏等裁切边缘出现亮暗偏差。
            texture.image.alpha_mode = "CHANNEL_PACKED"
            mat["uv_scale"] = (float(info.get("scale", {}).get("x", 1)), float(info.get("scale", {}).get("y", 1)))
            mat["uv_offset"] = (float(info.get("offset", {}).get("x", 0)), float(info.get("offset", {}).get("y", 0)))
            socket = texture.outputs["Color"]
            if any(abs(v - 1) > 1e-5 for v in color[:3]):
                tint = tree.nodes.new("ShaderNodeMixRGB")
                tint.blend_type = "MULTIPLY"
                tint.inputs[0].default_value = 1
                tint.inputs[2].default_value = color
                tree.links.new(socket, tint.inputs[1])
                socket = tint.outputs[0]
            tree.links.new(socket, bsdf.inputs["Base Color"])
            if alpha_mode != "OPAQUE":
                alpha = texture.outputs["Alpha"]
                if alpha_mode == "MASK":
                    alpha = self.math(tree, "GREATER_THAN", alpha, mat["alpha_cutoff"])
                elif color[3] != 1:
                    alpha = self.math(tree, "MULTIPLY", alpha, color[3])
                tree.links.new(alpha, bsdf.inputs["Alpha"])
        elif not entry.get("raw", {}).get("m_SavedProperties", {}).get("m_Colors"):
            raise ValueError("必要材质既无底色图也无颜色参数：" + mat_id)
        self.materials[mat_id] = mat
        self.report["materials"][mat_id] = {"name": mat.name, "basecolor": file, "alpha_mode": alpha_mode,
                                            "alpha_cutoff": mat["alpha_cutoff"], "double_sided": not mat.use_backface_culling,
                                            "semantic_policy": semantic.get("policy"), "source_shader": semantic.get("shader")}
        return mat


def bounds(objects):
    points = [obj.matrix_world @ Vector(corner) for obj in objects if obj.type == "MESH" for corner in obj.bound_box]
    if not points:
        raise ValueError("没有可见原型网格")
    return np.min(points, axis=0), np.max(points, axis=0)


def geometry_signatures(objects):
    signatures, duplicates = {}, 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        if any(len(p.vertices) != 3 for p in mesh.polygons):
            raise ValueError("原型回读出现非三角形面")
        vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", vertices)
        vertices = vertices.reshape(-1, 3)
        indices = np.asarray([list(p.vertices) for p in mesh.polygons], dtype=np.int32)
        slots = np.asarray([p.material_index for p in mesh.polygons])
        for slot in np.unique(slots):
            triangles = indices[slots == slot]
            # 只认可同一材料、同顶点索引且绕序相同的重复源面；原工程仍保留原面。
            rotations = np.stack([np.roll(triangles, shift, axis=1) for shift in range(3)], axis=1)
            keys = rotations.copy().view("V12").reshape(-1, 3)
            canonical = np.sort(keys, axis=1)[:, 0]
            duplicates += len(canonical) - len(np.unique(canonical))
            coords = np.round(vertices[triangles], 5).astype("<f4")
            coords[coords == 0] = 0
            rotations = np.stack([np.roll(coords, shift, axis=1).reshape(-1, 9) for shift in range(3)], axis=1)
            keys = rotations.copy().view("V36").reshape(-1, 3)
            canonical = np.unique(np.sort(keys, axis=1)[:, 0])
            material = mesh.materials[int(slot)]
            key = str(obj.get("原渲染器标识", obj.name)) + "|" + str(material.get("source_material", material.name))
            signatures[key] = hashlib.sha256(canonical.tobytes()).hexdigest()
    return signatures, duplicates


def patch_alpha(path):
    data = path.read_bytes()
    size, kind = struct.unpack_from("<II", data, 12)
    if data[:4] != b"glTF" or kind != 0x4E4F534A:
        raise ValueError("不是标准GLB")
    document = json.loads(data[20:20 + size])
    for mat in document.get("materials", []):
        extra = mat.get("extras", {})
        if "alpha_mode" in extra:
            mat["alphaMode"] = extra["alpha_mode"]
            if mat["alphaMode"] == "MASK":
                mat["alphaCutoff"] = extra["alpha_cutoff"]
            pbr = mat.setdefault("pbrMetallicRoughness", {})
            pbr["baseColorFactor"] = extra["base_color_factor"]
            scale, offset = extra.get("uv_scale", [1, 1]), extra.get("uv_offset", [0, 0])
            texture = pbr.get("baseColorTexture")
            if texture and (scale != [1, 1] or offset != [0, 0]):
                texture.setdefault("extensions", {})["KHR_texture_transform"] = {"scale": scale, "offset": [offset[0], 1 - scale[1] - offset[1]]}
                document["extensionsUsed"] = sorted(set(document.get("extensionsUsed", []) + ["KHR_texture_transform"]))
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    content += b" " * (-len(content) % 4)
    tail = data[20 + size:]
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(content) + len(tail)) + struct.pack("<II", len(content), kind) + content + tail)


def render_views(directory, objects, prefix, engine="BLENDER_EEVEE"):
    scene = bpy.context.scene
    scene.render.engine = engine
    # 网页使用栅格化。Cycles不按材质剔除重合背面，可能产生与网页无关的自遮挡。
    scene.cycles.device = "CPU"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = bpy.data.worlds.new("四向审阅环境")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.44, .5, .6, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .8
    sun = bpy.data.objects.new("四向审阅日光", bpy.data.lights.new("四向审阅日光", "SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 2.5
    sun.rotation_euler = (.5, -.5, -.6)
    camera = bpy.data.objects.new("四向审阅相机", bpy.data.cameras.new("四向审阅相机"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    low, high = bounds(objects)
    center = Vector((low + high) / 2)
    span = max(float(np.max(high - low)), .5)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span * 1.4
    camera.data.clip_end = max(1000, span * 10)
    for name, direction in [("正面", (0, -2, 1)), ("背面", (0, 2, 1)), ("左侧", (-2, 0, 1)), ("右侧", (2, 0, 1))]:
        camera.location = center + Vector(direction).normalized() * span * 2
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(directory / f"{prefix}-{name}.png")
        bpy.ops.render.render(write_still=True)


def prepare_mesh_repairs(source, output, surface_policy="preserve"):
    data = json.loads(source.read_text("utf-8"))
    repairs = []
    for node in data["nodes"]:
        for mesh in node["meshes"]:
            file = source.parent / mesh["file"]
            with np.load(file) as archive:
                arrays = {key: archive[key] for key in archive.files}
            local_repairs = []
            vertices, normals = arrays["vertices"], arrays["normals"]
            for channel in ["uv0", "uv1"]:
                uv = arrays.get(channel)
                if uv is None or not uv.size:
                    continue
                valid = np.isfinite(uv).all(1)
                for index in np.flatnonzero(~valid):
                    distance = np.linalg.norm(vertices - vertices[index], axis=1)
                    if normals.shape == vertices.shape and np.linalg.norm(normals[index]) > .99:
                        same_normal = (normals @ normals[index]) > .99
                        method = "同法线真实邻点"
                    else:
                        # 某些源网格法线全零；使用原索引拓扑中的同面邻点，避免错误跨UV接缝。
                        faces = arrays["triangles"][np.any(arrays["triangles"] == index, axis=1)]
                        neighbors = np.unique(faces)
                        same_normal = np.zeros(len(vertices), dtype=bool)
                        same_normal[neighbors] = True
                        method = "零法线源网格的同三角形真实邻点"
                    distance[~valid | ~same_normal] = np.inf
                    nearest = int(distance.argmin())
                    if not np.isfinite(distance[nearest]) or distance[nearest] > .05:
                        faces = arrays["triangles"][np.any(arrays["triangles"] == index, axis=1)]
                        neighbors = np.unique(faces)
                        candidate = np.zeros(len(vertices), dtype=bool)
                        candidate[neighbors] = True
                        distance = np.linalg.norm(vertices - vertices[index], axis=1)
                        distance[~valid | ~candidate] = np.inf
                        nearest = int(distance.argmin())
                        method = "源UV为NaN，保留几何并复制同源三角形的有限邻点UV"
                    # 全量盘点六个源缺陷网格，同面最近邻最远为8.9701厘米；不跨面远距离补图。
                    if not np.isfinite(distance[nearest]) or distance[nearest] > .100001:
                        raise ValueError(f"异常UV没有10厘米内同源三角形的有限邻点：{file.name}/{index}")
                    uv[index] = uv[nearest]
                    local_repairs.append({"channel": channel, "vertex": int(index), "source_vertex": nearest, "method": method,
                                          "distance_m": float(distance[nearest]), "uv": uv[index].tolist()})
            original_vertices = len(vertices)
            source_triangles = len(arrays["triangles"])
            surface_receipt = None
            if surface_policy == "unique":
                keep, mapping, surface_receipt = unique_surfaces(arrays)
                arrays["triangles"] = arrays["triangles"][keep]
                arrays["material_indices"] = arrays["material_indices"][keep]
                check_keep, _, derived_receipt = unique_surfaces(arrays)
                if len(check_keep) != len(keep) or derived_receipt["oriented_surface_sha256"] != surface_receipt["oriented_surface_sha256"]:
                    raise ValueError("归并前后有绕序属性表面不一致")
                if len(keep) != source_triangles:
                    target = output / "修复数据" / (file.stem + "-源面映射.npz")
                    target.parent.mkdir(exist_ok=True)
                    np.savez_compressed(target, source_face_to_render=mapping, retained_source_faces=keep)
                    surface_receipt["mapping"] = {"path": str(target), **fingerprint(target)}
            original_triangles = arrays["triangles"].copy()
            seen, duplicated = set(), []
            for face_index, face in enumerate(original_triangles):
                key = tuple(sorted(map(int, face)))
                if key in seen:
                    duplicated.append(face_index)
                else:
                    seen.add(key)
            if duplicated:
                copied = original_triangles[duplicated].ravel()
                for key in ["vertices", "normals", "uv0", "uv1"]:
                    values = arrays.get(key)
                    if values is not None and values.size:
                        arrays[key] = np.concatenate([values, values[copied]], axis=0)
                arrays["triangles"][duplicated] = np.arange(original_vertices, original_vertices + len(copied), dtype=np.int32).reshape(-1, 3)
                arrays["source_vertex_indices"] = np.concatenate([np.arange(original_vertices, dtype=np.int32), copied])
                arrays["export_vertex_identity"] = np.arange(original_vertices + len(copied), dtype=np.float32)
            if local_repairs or duplicated or (surface_receipt and surface_receipt["coincident_faces_merged"]):
                target = output / "修复数据" / file.name
                target.parent.mkdir(exist_ok=True)
                np.savez_compressed(target, **arrays)
                mesh["file"] = str(target)
                repairs.append({"source_file": str(file), "source_identity": fingerprint(file), "derived_file": str(target),
                                "derived_identity": fingerprint(target), "method": "源UV缺陷局部派生；按显式策略处理等价重合面；需要保留的反向面或属性差异面分配独立顶点，原始数据不改写",
                                "uv_repairs": local_repairs, "duplicate_faces_preserved": len(duplicated),
                                "surface_policy": surface_policy, "surface_equivalence": surface_receipt,
                                "source_vertices": original_vertices, "derived_vertices": len(arrays["vertices"]),
                                "source_triangles": source_triangles, "derived_triangles": len(arrays["triangles"])})
            else:
                mesh["file"] = str(file)
    if not repairs:
        return source, repairs
    target = output / "修复数据/scene.json"
    atomic_json(target, data)
    atomic_json(output / "网格修复凭证.json", repairs)
    return target, repairs


def build_one(row, output, previews, material_metadata=None, surface_policy="preserve", preview_engine="BLENDER_EEVEE"):
    source = Path(row["exported"]["scene"])
    output.mkdir(parents=True, exist_ok=False)
    prepared, repairs = prepare_mesh_repairs(source, output, surface_policy)
    builder = PrototypeBuilder(prepared, json.loads(prepared.read_text("utf-8")), "none", material_metadata)
    builder.build()
    bpy.context.scene.render.engine = preview_engine
    objects = list(builder.collection.all_objects)
    low, high = bounds(objects)
    file = output / "原型.blend"
    bpy.context.scene["asset_id"] = row["asset_id"]
    bpy.context.scene["source_key"] = row["source_key"]
    bpy.ops.wm.save_as_mainfile(filepath=str(file), check_existing=False)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = next(obj for obj in objects if obj.type == "MESH")
    glb = output / "原型.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True, export_extras=True,
                             export_yup=True, export_animations=False, export_cameras=False, export_lights=False, export_attributes=True)
    patch_alpha(glb)
    result = {"asset_id": row["asset_id"], "source_key": row["source_key"], "status": "构建完成，待独立回读及审图",
              "blender": bpy.app.version_string, "source": fingerprint(source), "mesh_repairs": repairs, "bounds_blender": [low.tolist(), high.tolist()],
              "source_bounds_unity": row["source_local_bounds"], "triangles": builder.report["triangles"],
              "source_triangles": sum(m["triangles"] for n in json.loads(source.read_text("utf-8"))["nodes"] for m in n["meshes"]),
              "surface_policy": surface_policy, "source_program": fingerprint(Path(__file__)), "preview_engine": preview_engine,
              "material_policy_sha256": hashlib.sha256(json.dumps(material_metadata or {}, sort_keys=True).encode()).hexdigest(),
              "materials": builder.report["materials"], "warnings": builder.warnings,
              "files": {file.name: fingerprint(file), glb.name: fingerprint(glb)}, "human_acceptance": "待人工验收"}
    atomic_json(output / "构建凭证.json", result)
    if previews:
        render_views(output, objects, "工程", engine=preview_engine)
    return result


def readback_one(row, output, previews, preview_engine="BLENDER_EEVEE"):
    record = json.loads((output / "构建凭证.json").read_text("utf-8"))
    for name, identity in record["files"].items():
        if fingerprint(output / name)["sha256"] != identity["sha256"]:
            raise ValueError("回读前产物摘要变化：" + name)
    bpy.ops.wm.open_mainfile(filepath=str(output / "原型.blend"), load_ui=False)
    if bpy.context.scene.unit_settings.scale_length != 1 or any(image.source == "FILE" and not image.packed_file for image in bpy.data.images):
        raise ValueError("工程米制或打包贴图不闭合")
    low, high = bounds(bpy.context.scene.objects)
    project_error = float(np.abs(np.array([low, high]) - record["bounds_blender"]).max())
    project_signatures, duplicate_faces = geometry_signatures(bpy.context.scene.objects)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(output / "原型.glb"), merge_vertices=False)
    objects = list(bpy.context.scene.objects)
    low, high = bounds(objects)
    glb_error = float(np.abs(np.array([low, high]) - record["bounds_blender"]).max())
    triangles = sum(sum(len(p.vertices) - 2 for p in obj.data.polygons) for obj in objects if obj.type == "MESH")
    glb_signatures, _ = geometry_signatures(objects)
    if max(project_error, glb_error) > .001 or triangles != record["triangles"] or project_signatures != glb_signatures:
        raise ValueError(f"独立回读几何不符：包围盒误差{glb_error}，三角形{triangles}/{record['triangles']}")
    result = {"asset_id": row["asset_id"], "status": "工程及GLB独立回读通过", "project_bounds_error_m": project_error,
              "glb_bounds_error_m": glb_error, "triangles": triangles, "source_triangles": record.get("source_triangles", record["triangles"]),
              "surface_policy": record.get("surface_policy", "preserve"),
              "intentional_equivalent_faces_merged": record.get("source_triangles", record["triangles"]) - record["triangles"],
              "coincident_source_duplicate_faces": duplicate_faces, "normalized_duplicate_faces": record["triangles"] - triangles,
              "oriented_unique_geometry_sha256": glb_signatures, "human_acceptance": "待人工验收",
              "build_receipt": fingerprint(output / "构建凭证.json"), "source_program": fingerprint(Path(__file__)),
              "preview_engine": preview_engine}
    if previews:
        render_views(output, objects, "回读", engine=preview_engine)
        result["previews"] = {f"回读-{view}.png": fingerprint(output / f"回读-{view}.png") for view in ["正面", "背面", "左侧", "右侧"]}
    atomic_json(output / "回读凭证.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--mode", choices=["build", "readback"], default="build")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--previews", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--id", action="append")
    parser.add_argument("--surface-policy", choices=["preserve", "unique"], default="preserve")
    parser.add_argument("--material-metadata", type=Path)
    parser.add_argument("--preview-engine", choices=["BLENDER_EEVEE", "CYCLES"], default="BLENDER_EEVEE")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    directory = (ROOT / args.directory).resolve()
    directory.relative_to((ROOT / "local-data/断箭/批次").resolve())
    if not args.revision or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.revision):
        raise ValueError("无效修订名")
    rows = json.loads((directory / "来源" / ("全类别样本原型.json" if args.sample else "全量原型来源.json")).read_text("utf-8"))
    if args.id:
        rows = [row for row in rows if row["asset_id"] in set(args.id)]
        if len(rows) != len(set(args.id)):
            raise ValueError("指定原型ID不存在或重复")
    if args.limit:
        rows = rows[:args.limit]
    journal = Journal(directory)
    material_path = args.material_metadata.resolve() if args.material_metadata else directory / "来源/材质语义.json"
    material_path.resolve().relative_to(directory)
    material_metadata = json.loads(material_path.read_text("utf-8"))["materials"] if material_path.exists() else {}
    results = []
    try:
        for index, row in enumerate(rows):
            journal.update(phase="A1双层资产样本", operation="原型构建" if args.mode == "build" else "独立进程回读",
                           current=index + 1, total=len(rows), source_key=row["source_key"])
            journal.state["gates"]["A1"] = "进行中"
            output = directory / "独立原型" / args.revision / row["asset_id"].split(":")[1]
            if args.mode == "readback" and args.resume and (output / "回读凭证.json").exists():
                previous = json.loads((output / "回读凭证.json").read_text("utf-8"))
                identities = {"构建凭证.json": previous.get("build_receipt", {}), **previous.get("previews", {})}
                valid = previous.get("source_program", {}).get("sha256") == fingerprint(Path(__file__))["sha256"]
                valid = valid and previous.get("preview_engine") == args.preview_engine
                valid = valid and all(identity.get("sha256") == fingerprint(output / name)["sha256"] for name, identity in identities.items())
                record = json.loads((output / "构建凭证.json").read_text("utf-8"))
                valid = valid and all(identity["sha256"] == fingerprint(output / name)["sha256"] for name, identity in record["files"].items())
                if valid and (not args.previews or len(previous.get("previews", {})) == 4):
                    results.append(previous)
                    continue
            if args.mode == "build" and args.resume and output.exists():
                receipt = output / "构建凭证.json"
                if receipt.exists():
                    previous = json.loads(receipt.read_text("utf-8"))
                    source = Path(row["exported"]["scene"])
                    if fingerprint(source)["sha256"] != previous["source"]["sha256"]:
                        raise ValueError("续跑源场景身份变化")
                    if previous.get("surface_policy", "preserve") != args.surface_policy or previous.get("material_policy_sha256") != hashlib.sha256(json.dumps(material_metadata, sort_keys=True).encode()).hexdigest() or previous.get("preview_engine") != args.preview_engine:
                        raise ValueError("续跑表面或材质策略变化，须使用新修订")
                    for name, identity in previous["files"].items():
                        if fingerprint(output / name)["sha256"] != identity["sha256"]:
                            raise ValueError("续跑原型产物身份变化")
                    if not args.previews or all((output / f"工程-{view}.png").is_file() for view in ["正面", "背面", "左侧", "右侧"]):
                        results.append(previous)
                        continue
                backup = output.parent / "未完成保留" / (output.name + "-" + str(time.time_ns()))
                backup.resolve().relative_to(directory)
                output.resolve().relative_to(directory)
                backup.parent.mkdir(exist_ok=True)
                output.rename(backup)
            result = build_one(row, output, args.previews, material_metadata, args.surface_policy, args.preview_engine) if args.mode == "build" else readback_one(row, output, args.previews, args.preview_engine)
            results.append(result)
            atomic_json(directory / "独立原型" / args.revision / f"{args.mode}-汇总.json", results)
        journal.close(status="当前原型作业完成，A1继续", completed=len(results))
    except BaseException as error:
        journal.close(status="原型作业失败，保留恢复点", error=repr(error))
        raise


if __name__ == "__main__":
    main()
