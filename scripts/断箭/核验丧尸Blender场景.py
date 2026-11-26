"""独立重开丧尸场景，对照解包数据和 Unity 原生采样核验。"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector
import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from 构建丧尸工程 import C, matrix, sha
from 组装丧尸Blender场景 import ARMOUR, SWARM, Batch, factor_prefab_scale, load_native, rest_worlds


def max_error(a, b):
    return float(np.max(np.abs(np.asarray(a) - np.asarray(b))))


def attribute_values(attribute, prop, shape):
    value = np.empty(shape, dtype=np.float32)
    attribute.data.foreach_get(prop, value.ravel())
    return value


def check_mesh(obj, arrays):
    mesh = obj.data
    positions = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", positions.ravel())
    error = max_error(positions, arrays["vertices"][:, [0, 2, 1]])
    triangles = np.array([list(p.vertices) for p in mesh.polygons])
    if error > 1e-6 or not np.array_equal(triangles, arrays["triangles"][:, [0, 2, 1]]):
        raise ValueError(f"{obj.name} 顶点或拓扑回读失败")
    loops = np.array([loop.vertex_index for loop in mesh.loops])
    for index in range(8):
        key = f"uv{index}"
        if key not in arrays:
            continue
        uv = np.empty((len(loops), 2), dtype=np.float32)
        mesh.uv_layers[f"UV{index}"].uv.foreach_get("vector", uv.ravel())
        if max_error(uv, arrays[key][loops, :2]) > 1e-6:
            raise ValueError(f"{obj.name} UV 回读失败")
    for key, name, prop in (("normals", "源Unity法线", "vector"), ("tangents", "源Unity切线", "color"),
                            ("colors", "源Unity顶点色", "color")):
        if key not in arrays:
            continue
        expected = arrays[key][:, :3] if key == "normals" else arrays[key]
        observed = attribute_values(mesh.attributes[name], prop, expected.shape)
        if max_error(observed, expected) > 1e-6:
            raise ValueError(f"{obj.name} 的 {name} 回读失败")
    return {"name": obj.name, "vertices": len(mesh.vertices), "triangles": len(mesh.polygons),
            "max_position_error_m": error}


def check_skins(batch, role, group, arm, bone_names, native):
    nodes, lods = batch.skeleton(role)
    nodes, placement = factor_prefab_scale(nodes, native)
    if max_error(arm.parent.matrix_basis, Matrix.Translation(arm.parent["展示位置"]) @ placement) > 1e-6:
        raise ValueError(f"{role['role']} 预制体倍率或展示根变换与来源不符")
    rest = rest_worlds(nodes)
    rest_max = max(max_error(arm.data.bones[name].matrix_local, C @ rest[ident] @ C)
                   for ident, name in bone_names.items())
    if rest_max > 2e-5:
        raise ValueError(f"{role['role']} 骨架静止矩阵偏差：{rest_max:.6g}")
    result = []
    for lod in lods:
        level = lod["level"]
        layer = group.children[f"{role['role']}｜LOD{level}"]
        obj, = layer.objects
        hidden = level != 0
        if layer.hide_viewport != hidden or layer.hide_render != hidden or obj["源渲染器键"] != lod["renderer"]:
            raise ValueError(f"{obj.name} 的 LOD 状态或渲染器来源不一致")
        if len(obj.modifiers) != 1 or obj.modifiers[0].object != arm:
            raise ValueError(f"{obj.name} 缺少对应的骨架蒙皮修改器")
        with np.load(batch.check_file(batch.objects[lod["mesh"]]["mesh"]["file"]), allow_pickle=False) as arrays:
            row = check_mesh(obj, arrays)
            actual_slots = np.array([face.material_index for face in obj.data.polygons])
            if not np.array_equal(actual_slots, arrays["material_indices"]):
                raise ValueError(f"{obj.name} 源面材质槽回读失败")
            names = [bone_names[ident] for ident in lod["bones"]]
            bound = [arm.data.bones[name].matrix_local.inverted() @ obj.matrix_basis for name in names]
            bind_max = max_error(bound, [C @ np_matrix(values) @ C for values in arrays["bind"]])
            if bind_max > 2e-4:
                raise ValueError(f"{obj.name} 源绑定矩阵偏差：{bind_max:.6g}")
            maximum = 0
            for vertex, (indices, weights) in enumerate(zip(arrays["indices"], arrays["weights"])):
                expected = {}
                for idx, weight in zip(indices, weights):
                    if weight > 0:
                        name = names[int(idx)]
                        expected[name] = expected.get(name, 0) + float(weight)
                observed = {obj.vertex_groups[g.group].name: g.weight for g in obj.data.vertices[vertex].groups}
                if expected.keys() != observed.keys():
                    raise ValueError(f"{obj.name} 蒙皮骨骼集合变化")
                maximum = max(maximum, max((abs(w - observed[name]) for name, w in expected.items()), default=0))
            if maximum > 1e-6:
                raise ValueError(f"{obj.name} 蒙皮权重回读偏差：{maximum:.6g}")
            row.update(lod=level, bones=len(names), bind_error=bind_max, max_weight_error=maximum)
            result.append(row)
    return nodes, result, rest_max


def np_matrix(values):
    return Matrix(values.tolist())


def set_time(scene, time):
    frame = 1 + time * scene.render.fps
    whole = math.floor(frame)
    scene.frame_set(whole, subframe=frame - whole)


def check_actions(scene, group, arm, bone_names, nodes, native):
    lookup = {path: i * 10 for i, path in enumerate(native["paths"])}
    by_bone = [(n, arm.pose.bones[bone_names[n["id"]]]) for n in nodes if n["id"] in bone_names]
    mesh, = group.children[f"{native['name']}｜LOD0"].objects
    reports = []
    # Unity 采样的顶点包含上层预制体倍率，Blender 网格局部顶点由展示根在外层缩放。
    source_scale = arm.parent.matrix_basis.to_scale().x
    for clip in native["clips"]:
        action = bpy.data.actions[f"{native['name']}｜{clip['name']}"]
        if not action.use_fake_user or not math.isclose(action["源时长秒"], clip["duration"], abs_tol=1e-6):
            raise ValueError(f"{action.name} 动作保存属性不符")
        arm.animation_data.action = action
        arm.animation_data.action_slot = action.slots[0]
        checks = {row["frame"]: row["vertices"] for row in clip["checks"]}
        max_pose, max_skin = 0, 0
        for index, f in enumerate(clip["frames"]):
            set_time(scene, f["time"])
            worlds = {}
            for n, bone in by_bone:
                start = lookup[n["path"]]
                local = matrix(f["trs"][start:start + 10])
                worlds[n["id"]] = worlds[n["parent"]] @ local if n["parent"] else local
                max_pose = max(max_pose, max_error(bone.matrix, C @ worlds[n["id"]] @ C))
            if index in checks:
                evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
                deformed = evaluated.to_mesh()
                try:
                    actual = np.empty((len(deformed.vertices), 3), dtype=np.float32)
                    deformed.vertices.foreach_get("co", actual.ravel())
                    expected = np.asarray(checks[index]).reshape(-1, 3)[:, [0, 2, 1]]
                    max_skin = max(max_skin, float(np.linalg.norm(actual * source_scale - expected, axis=1).max()))
                finally:
                    evaluated.to_mesh_clear()
        if max_pose > 2e-4 or max_skin > 2e-4:
            raise ValueError(f"{action.name} 回读偏差，骨矩阵 {max_pose:.6g}，蒙皮 {max_skin:.6g} 米")
        reports.append({"action": action.name, "frames": len(clip["frames"]), "unity_mesh_checks": len(checks),
                        "max_bone_matrix_error": max_pose, "max_skinned_vertex_error_m": max_skin})
        print(f"{action.name} 骨矩阵 {max_pose:.6g} 蒙皮 {max_skin:.6g} 米", flush=True)
    return reports


def check_blend(batch, filepath, native, sample):
    digest = sha(filepath)
    bpy.ops.wm.open_mainfile(filepath=str(filepath), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    if scene["源批次"] != batch.directory.name or scene["源对象清单SHA256"] != sha(batch.delivery / "对象清单.json"):
        raise ValueError("工程所记载的源批次或摘要不符")
    top = bpy.data.collections["原作丧尸"]
    names = {child.name for child in top.children}
    if names != ({ARMOUR} if sample else set(batch.roles)) or set(native) != names - {SWARM}:
        raise ValueError("角色集合与授权范围不一致")
    initial_frame = scene.frame_current
    idle_actions = {}
    for group in top.children:
        if group.name == SWARM:
            continue
        arm, = [obj for obj in group.objects if obj.type == "ARMATURE"]
        idle_actions[group.name] = arm.animation_data.action
        arm.animation_data.action = None

    roles = []
    native_texture_paths = set()
    for name in sorted(names):
        group = top.children[name]
        role = batch.role(name)
        textures = {batch.objects[key]["image"]["file"] for key in role["objects"]
                    if batch.objects[key]["type"] == "Texture2D"}
        native_texture_paths.update(textures)
        if name == SWARM:
            expected = {key for key in role["objects"] if batch.objects[key]["type"] == "Mesh"}
            meshes = [obj for obj in group.objects if obj.type == "MESH"]
            observed = {obj["源网格键"] for obj in meshes}
            if expected != observed:
                raise ValueError("Swarm 源网格集合不一致")
            for obj in meshes:
                info = batch.objects[obj["源网格键"]]["mesh"]
                with np.load(batch.check_file(info["file"]), allow_pickle=False) as arrays:
                    check_mesh(obj, arrays)
            packed_paths = {image.get("源文件") for image in bpy.data.images if image.packed_file}
            if not textures <= packed_paths:
                raise ValueError("Swarm 缺少源贴图")
            roles.append({"name": name, "meshes": len(meshes), "source_textures": len(textures), "mode": "VFX 源资源"})
            continue
        arm, = [obj for obj in group.objects if obj.type == "ARMATURE"]
        bone_names = {bone["源节点ID"]: bone.name for bone in arm.data.bones}
        nodes, skins, rest = check_skins(batch, role, group, arm, bone_names, native[name])
        hidden = arm.hide_get()
        arm.hide_set(False)
        actions = check_actions(scene, group, arm, bone_names, nodes, native[name])
        arm.animation_data.action = None
        arm.hide_set(hidden)
        roles.append({"name": name, "bones": len(bone_names), "meshes": skins,
                      "rest_matrix_max_error": rest, "actions": actions})

    for name, action in idle_actions.items():
        arm = bpy.data.objects[name + "｜骨架"]
        arm.animation_data.action = action
        arm.animation_data.action_slot = action.slots[0]
    scene.frame_set(initial_frame)

    source_images, decoded_images = set(), 0
    for image in bpy.data.images:
        if image.get("源文件"):
            source = image["源文件"]
            batch.check_file(source)
            expected = batch.checksums[source]["sha256"]
            if not image.packed_file or hashlib.sha256(image.packed_file.data).hexdigest() != expected:
                raise ValueError(f"源贴图打包字节不符：{image.name}")
            source_images.add(source)
        elif image.get("法线转换来源"):
            if not image.packed_file:
                raise ValueError(f"适配后的法线贴图未打包：{image.name}")
            decoded_images += 1
    if source_images != native_texture_paths:
        raise ValueError("源贴图打包集合与解包依赖不一致")
    action_count = sum(len(row.get("actions", [])) for row in roles)
    if action_count != len(bpy.data.actions):
        raise ValueError("场景动作数据块与角色动作清单不一致")
    if sha(filepath) != digest:
        raise ValueError("独立回读期间工程文件变化")
    return {"status": "通过", "file": filepath.name, "sha256": digest, "bytes": filepath.stat().st_size,
            "blender": bpy.app.version_string, "armatures": len(idle_actions), "source_images": len(source_images),
            "converted_normal_maps": decoded_images, "roles": roles, "actions": action_count}


def render_preview(path, back_path=None):
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if back_path:
        camera = scene.camera
        camera.location.y = -camera.location.y
        aim = Vector(scene["预览中心"])
        camera.rotation_euler = (aim - camera.location).to_track_quat("-Z", "Y").to_euler()
        for obj in bpy.data.collections["预览摄影棚"].objects:
            if obj.type == "FONT":
                obj.location = obj["背面标签位置"]
                obj.rotation_euler = camera.rotation_euler
        scene.render.filepath = str(back_path)
        bpy.ops.render.render(write_still=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--blend", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--native", required=True, action="append", type=Path)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--back-preview", type=Path)
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    batch_dir, blend = args.batch.resolve(), args.blend.resolve()
    targets = [p.resolve() for p in (args.report, args.preview, args.back_preview) if p]
    if not blend.is_relative_to(batch_dir) or any(
        p.exists() or p.parent != blend.parent for p in targets
    ):
        parser.error("回读和预览须输出为工程目录中的新文件")
    result = check_blend(Batch(batch_dir), blend, load_native(args.native), args.sample)
    with args.report.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "roles"}, ensure_ascii=False), flush=True)
    if args.preview:
        render_preview(args.preview, args.back_preview)


if __name__ == "__main__":
    main()
