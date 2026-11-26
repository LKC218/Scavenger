"""把已核验的单位包解包批次组装为可编辑的 Blender 总场景。"""

import argparse
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector
import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from 构建丧尸工程 import C, add_native_actions, sha, source_matrix


BATCH_ROOT = (Path(__file__).resolve().parents[2] / "local-data/断箭/批次").resolve()
ARMOUR = "ZOM_ArmouredZombie1"
SWARM = "ZOM_Swarm1"
DISPLAY_FRAME = 8


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def collection(parent, name):
    result = bpy.data.collections.new(name)
    parent.children.link(result)
    return result


class Batch:
    def __init__(self, directory):
        self.directory = directory
        self.delivery = directory / "交付"
        self.manifest = read(self.delivery / "对象清单.json")
        self.roles = self.manifest["roles"]
        self.objects = self.manifest["objects"]
        self.checksums = read(self.delivery / "文件校验.json")
        receipt = read(directory / "核验结果.json")
        if sha(self.delivery / "文件校验.json") != receipt["manifest_sha256"]:
            raise ValueError("解包校验清单与回执摘要不一致")
        self.check_file("对象清单.json")

    @lru_cache(maxsize=None)
    def check_file(self, relative):
        path = (self.delivery / relative).resolve()
        if not path.is_relative_to(self.delivery) or sha(path) != self.checksums[relative]["sha256"]:
            raise ValueError(f"源解包文件摘要不一致：{relative}")
        return path

    @lru_cache(maxsize=None)
    def fields(self, key):
        return read(self.check_file(self.objects[key]["fields"]))

    def role(self, name):
        return read(self.check_file(f"角色清单/{name}.json"))

    def resolve(self, role, origin, ptr):
        path = str(ptr["m_PathID"])
        if not ptr["m_PathID"]:
            return None
        edges = role["edges"][origin]
        if ptr["m_FileID"] == 0:
            key = f"{self.objects[origin]['serialized_file']}:{path}"
            if key not in edges:
                raise KeyError(f"{origin} 缺少源引用 {key}")
            return key
        key, = [key for key in edges if key.rsplit(":", 1)[-1] == path]
        return key

    def skeleton(self, role):
        nodes = []
        renderers = {}
        def walk(transform_key, parent, trail):
            t = self.fields(transform_key)
            game_key = self.resolve(role, transform_key, t["m_GameObject"])
            go = self.fields(game_key)
            path = "/".join(s for s in (trail, go["m_Name"]) if s) if parent else ""
            ident = transform_key.rsplit(":", 1)[-1]
            n = {"name": go["m_Name"], "id": ident, "parent": parent,
                 "path": path, "transform": t, "source_object": game_key}
            nodes.append(n)
            for item in go["m_Component"]:
                key = self.resolve(role, game_key, item["component"])
                if self.objects[key]["type"] == "SkinnedMeshRenderer":
                    renderers[key] = n
            for ptr in t["m_Children"]:
                walk(self.resolve(role, transform_key, ptr), ident, path)

        root = role["root"]
        components = self.fields(root)["m_Component"]
        start, = [key for item in components
                  if (key := self.resolve(role, root, item["component"]))
                  and self.objects[key]["type"] == "Transform"]
        walk(start, None, "")
        lod_group, = [key for key in role["objects"] if self.objects[key]["type"] == "LODGroup"]
        lods = []
        for level, decision in enumerate(self.fields(lod_group)["m_LODs"]):
            for item in decision["renderers"]:
                key = self.resolve(role, lod_group, item["renderer"])
                fields = self.fields(key)
                mesh_key = self.resolve(role, key, fields["m_Mesh"])
                materials = [self.resolve(role, key, p) for p in fields["m_Materials"]]
                lods.append({"level": level, "renderer": key, "mesh": mesh_key,
                             "node": renderers[key]["id"], "materials": materials,
                             "bones": [str(p["m_PathID"]) for p in fields["m_Bones"]]})
        if len(lods) != 3 or {item["level"] for item in lods} != {0, 1, 2}:
            raise ValueError(f"{role['role']} 原始 LOD 结构不符")
        return nodes, lods


class Materials:
    def __init__(self, batch):
        self.batch = batch
        self.images = {}
        self.materials = {}
        self.normals = {}

    def image(self, key, srgb):
        pair = key, srgb
        if pair not in self.images:
            row = self.batch.objects[key]
            source = row["image"]["file"]
            path = self.batch.check_file(source)
            image = bpy.data.images.load(str(path), check_existing=False)
            image.name = row["name"] + ("｜色彩" if srgb else "｜数据")
            image.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
            image.pack()
            image.use_fake_user = True
            image["源文件"] = source
            image["源SHA256"] = self.batch.checksums[source]["sha256"]
            self.images[pair] = image
        return self.images[pair]

    def normal(self, key):
        if key not in self.normals:
            original = self.image(key, False)
            width, height = original.size
            pixels = np.empty(width * height * 4, dtype=np.float32)
            original.pixels.foreach_get(pixels)
            pixels = pixels.reshape(-1, 4)
            x = (pixels[:, 0] * pixels[:, 3]) * 2 - 1
            y = pixels[:, 1] * 2 - 1
            z = np.sqrt(np.maximum(0, 1 - x * x - y * y))
            result = np.column_stack(((x + 1) / 2, (y + 1) / 2, (z + 1) / 2, np.ones(len(x))))
            image = bpy.data.images.new(original.name + "｜XYZ法线", width, height, alpha=True)
            image.colorspace_settings.name = "Non-Color"
            image.pixels.foreach_set(result.astype(np.float32).ravel())
            image.pack()
            image["法线转换来源"] = key
            self.normals[key] = image
        return self.normals[key]

    def material(self, role, key):
        if key in self.materials:
            return self.materials[key]
        raw = self.batch.fields(key)
        props = raw["m_SavedProperties"]
        nodes_by_attribute = {}
        mat = bpy.data.materials.new(raw["m_Name"])
        mat.use_nodes = True
        mat["源材质ID"] = key
        mat["适配"] = "Unity HDRP Lit → Principled BSDF"
        tree = mat.node_tree
        bsdf = tree.nodes.get("Principled BSDF")
        floats = dict(props["m_Floats"])
        colors = dict(props["m_Colors"])
        for name, info in props["m_TexEnvs"]:
            pointer = info["m_Texture"]
            if not pointer["m_PathID"] or name == "_MainTex":
                continue
            image_key = self.batch.resolve(role, key, pointer)
            if self.batch.objects[image_key]["type"] != "Texture2D":
                continue
            node = tree.nodes.new("ShaderNodeTexImage")
            node.label = name
            node.location = (-560, -230 * len(nodes_by_attribute))
            node.image = self.normal(image_key) if name == "_NormalMap" else self.image(
                image_key, name in ("_BaseColorMap", "_EmissiveColorMap"))
            scale = info["m_Scale"]
            offset = info["m_Offset"]
            if tuple(scale.values()) != (1.0, 1.0) or tuple(offset.values()) != (0.0, 0.0):
                uv = tree.nodes.new("ShaderNodeTexCoord")
                mapping = tree.nodes.new("ShaderNodeVectorMath")
                mapping.operation = "MULTIPLY_ADD"
                mapping.inputs[1].default_value = (scale["x"], scale["y"], 1)
                mapping.inputs[2].default_value = (offset["x"], offset["y"], 0)
                tree.links.new(uv.outputs["UV"], mapping.inputs[0])
                tree.links.new(mapping.outputs[0], node.inputs["Vector"])
            nodes_by_attribute[name] = node

        base = nodes_by_attribute["_BaseColorMap"]
        color = colors.get("_BaseColor", {"r": 1, "g": 1, "b": 1, "a": 1})
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = 1.0
        mix.inputs[2].default_value = tuple(color[channel] for channel in "rgba")
        tree.links.new(base.outputs["Color"], mix.inputs[1])
        tree.links.new(mix.outputs[0], bsdf.inputs["Base Color"])
        mask = nodes_by_attribute["_MaskMap"]
        separate = tree.nodes.new("ShaderNodeSeparateColor")
        tree.links.new(mask.outputs["Color"], separate.inputs[0])
        tree.links.new(separate.outputs["Red"], bsdf.inputs["Metallic"])
        rough = tree.nodes.new("ShaderNodeMapRange")
        rough.inputs["To Min"].default_value = 1 - floats.get("_SmoothnessRemapMin", 0)
        rough.inputs["To Max"].default_value = 1 - floats.get("_SmoothnessRemapMax", 1)
        tree.links.new(mask.outputs["Alpha"], rough.inputs["Value"])
        tree.links.new(rough.outputs[0], bsdf.inputs["Roughness"])
        normal = tree.nodes.new("ShaderNodeNormalMap")
        normal.inputs["Strength"].default_value = floats.get("_NormalScale", 1)
        tree.links.new(nodes_by_attribute["_NormalMap"].outputs["Color"], normal.inputs["Color"])
        tree.links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
        if floats.get("_AlphaCutoffEnable"):
            cutoff = tree.nodes.new("ShaderNodeMath")
            cutoff.operation = "GREATER_THAN"
            cutoff.inputs[1].default_value = floats.get("_AlphaCutoff", .5)
            tree.links.new(base.outputs["Alpha"], cutoff.inputs[0])
            tree.links.new(cutoff.outputs[0], bsdf.inputs["Alpha"])
        if emission := nodes_by_attribute.get("_EmissiveColorMap"):
            hdr = colors.get("_EmissiveColor", {"r": 1, "g": 1, "b": 1})
            peak = max(hdr[channel] for channel in "rgb")
            tint = tree.nodes.new("ShaderNodeMixRGB")
            tint.blend_type = "MULTIPLY"
            tint.label = "源发光色"
            tint.inputs[0].default_value = 1
            tint.inputs[2].default_value = (*(hdr[channel] / max(peak, 1e-6) for channel in "rgb"), 1)
            tree.links.new(emission.outputs["Color"], tint.inputs[1])
            tree.links.new(tint.outputs[0], bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = peak
        self.materials[key] = mat
        return mat

    def preserve_textures(self, keys):
        loaded = {key for key, _ in self.images}
        for key in sorted(keys - loaded):
            self.image(key, False)


def create_mesh(name, arrays):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(arrays["vertices"][:, [0, 2, 1]], [], arrays["triangles"][:, [0, 2, 1]])
    mesh.update()
    loops = np.array([loop.vertex_index for loop in mesh.loops])
    for index in range(8):
        key = f"uv{index}"
        if key in arrays:
            uv = mesh.uv_layers.new(name=f"UV{index}")
            uv.uv.foreach_set("vector", arrays[key][loops, :2].ravel())
    for face in mesh.polygons:
        face.use_smooth = True
    if "normals" in arrays:
        normals = arrays["normals"][:, :3]
        mesh.normals_split_custom_set_from_vertices(normals[:, [0, 2, 1]])
        mesh.attributes.new("源Unity法线", "FLOAT_VECTOR", "POINT").data.foreach_set("vector", normals.ravel())
    if "tangents" in arrays:
        mesh.attributes.new("源Unity切线", "FLOAT_COLOR", "POINT").data.foreach_set(
            "color", arrays["tangents"].ravel())
    if "colors" in arrays:
        mesh.color_attributes.new(name="源Unity顶点色", type="FLOAT_COLOR", domain="POINT").data.foreach_set(
            "color", arrays["colors"].ravel())
    return mesh


def assembly_root(group, name, location):
    root = bpy.data.objects.new(name + "｜展示根", None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = .2
    group.objects.link(root)
    root.location = location
    root["角色"] = name
    return root


def rest_worlds(nodes):
    result = {}
    for n in nodes:
        local = source_matrix(n)
        parent = n["parent"]
        result[n["id"]] = result[parent] @ local if parent else local
    return result


def factor_prefab_scale(nodes, native):
    # Blender 骨骼的静止矩阵不储存缩放，预制体统一倍率应由外围对象承担。
    scaled = [n for n in nodes if any(abs(v - 1) > 5e-4
              for v in n["transform"]["m_LocalScale"].values())]
    if not scaled:
        return nodes, Matrix.Identity(4)
    if len(scaled) != 1:
        raise ValueError(f"{native['name']} 存在多个非单位缩放节点")
    node = scaled[0]
    path = node["path"]
    scale = tuple(node["transform"]["m_LocalScale"][axis] for axis in "xyz")
    if min(scale) <= 0 or max(scale) - min(scale) > 1e-6 or "/" in path:
        raise ValueError(f"{native['name']} 预制体倍率非正均匀外层缩放")
    if path and any(n["path"] and n["path"] != path and not n["path"].startswith(path + "/")
                    for n in nodes):
        raise ValueError(f"{native['name']} 有层级位于缩放节点外")
    pivot = rest_worlds(nodes)[node["id"]].translation
    placement = C @ Matrix.Translation(pivot) @ Matrix.Scale(scale[0], 4) @ Matrix.Translation(-pivot) @ C
    start = native["paths"].index(path) * 10 + 7
    for clip in native["clips"]:
        for frame in clip["frames"]:
            values = frame["trs"]
            if any(abs(a - b) > 1e-5 for a, b in zip(values[start:start + 3], scale)):
                raise ValueError(f"{native['name']} 的外层倍率随动作变化")
            values[start:start + 3] = [1, 1, 1]
    transform = dict(node["transform"], m_LocalScale=dict.fromkeys("xyz", 1))
    normalized = [dict(n, transform=transform) if n is node else n for n in nodes]
    return normalized, placement


def rigged_character(batch, materials, scene, group, role, native, root):
    name = role["role"]
    nodes, lods = batch.skeleton(role)
    nodes, placement = factor_prefab_scale(nodes, native)
    root["展示位置"] = tuple(root.location)
    root.matrix_basis = Matrix.Translation(root.location) @ placement
    paths = {n["id"]: n["path"] for n in nodes}
    worlds = rest_worlds(nodes)
    excluded = {item["node"] for item in lods}
    bones = [n for n in nodes if n["id"] not in excluded]
    expected = {batch.objects[key]["name"] for key in role["objects"]
                if batch.objects[key]["type"] == "AnimationClip"}
    observed = {clip["name"] for clip in native["clips"]}
    if not native["validAvatar"] or set(native["paths"]) != set(paths.values()) or observed != expected:
        raise ValueError(f"{name} 原生动作、Avatar 或层级路径与解包清单不一致")
    arm = bpy.data.objects.new(name + "｜骨架", bpy.data.armatures.new(name + "｜骨架数据"))
    arm.data.display_type = "STICK"
    group.objects.link(arm)
    arm.parent = root
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    names = {}
    for n in bones:
        bone = arm.data.edit_bones.new(n["name"])
        names[n["id"]] = bone.name
        bone.length = .07
        bone.matrix = C @ worlds[n["id"]] @ C
        if n["parent"]:
            bone.parent = arm.data.edit_bones[names[n["parent"]]]
    bpy.ops.object.mode_set(mode="OBJECT")
    for ident, bone_name in names.items():
        arm.data.bones[bone_name]["源节点ID"] = ident
        arm.data.bones[bone_name]["源层级路径"] = paths[ident]
        arm.pose.bones[bone_name].rotation_mode = "QUATERNION"

    for lod in lods:
        level = lod["level"]
        target = collection(group, f"{name}｜LOD{level}")
        mesh_row = batch.objects[lod["mesh"]]["mesh"]
        with np.load(batch.check_file(mesh_row["file"]), allow_pickle=False) as arrays:
            mesh = create_mesh(f"{name}｜LOD{level}", arrays)
            for key in lod["materials"]:
                mesh.materials.append(materials.material(role, key))
            mesh.polygons.foreach_set("material_index", arrays["material_indices"])
            obj = bpy.data.objects.new(mesh.name, mesh)
            target.objects.link(obj)
            obj.parent = root
            obj.matrix_basis = C @ worlds[lod["node"]] @ C
            obj["源网格键"] = lod["mesh"]
            obj["源渲染器键"] = lod["renderer"]
            obj["细节层"] = level
            groups = [obj.vertex_groups.new(name=names[ident]) for ident in lod["bones"]]
            for index, (indices, weights) in enumerate(zip(arrays["indices"], arrays["weights"])):
                for bone, weight in zip(indices, weights):
                    if weight > 0:
                        groups[int(bone)].add([index], float(weight), "ADD")
            modifier = obj.modifiers.new("源骨架蒙皮", "ARMATURE")
            modifier.object = arm
            modifier.use_deform_preserve_volume = False
        target.hide_render = target.hide_viewport = level != 0

    actions, stats = add_native_actions(scene, arm, name, native, bones, names, paths, prefix=f"{name}｜")
    selected = actions["ZombieIdle"]
    arm.animation_data.action = selected
    arm.animation_data.action_slot = selected.slots[0]
    arm["角色"] = name
    arm["源预制体"] = role["source"]
    arm["动作列表"] = json.dumps(list(actions), ensure_ascii=False)
    arm.select_set(False)
    arm.hide_set(True)
    print(json.dumps({"角色": name, "骨架节点": len(bones), "动作": len(stats)}, ensure_ascii=False), flush=True)


def simple_material(name, color, roughness):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    surface = mat.node_tree.nodes.get("Principled BSDF")
    surface.inputs["Base Color"].default_value = (*color, 1)
    surface.inputs["Roughness"].default_value = roughness
    return mat


def build_swarm(batch, materials, group, role, root):
    mat = simple_material("Swarm 源网格检查材质", (.39, .46, .49), .8)
    nodes = []
    for key in role["objects"]:
        info = batch.objects[key]
        if info["type"] == "Mesh":
            with np.load(batch.check_file(info["mesh"]["file"]), allow_pickle=False) as arrays:
                mesh = create_mesh(f"{SWARM}｜{info['name']}", arrays)
            mesh.materials.append(mat)
            obj = bpy.data.objects.new(mesh.name, mesh)
            group.objects.link(obj)
            obj.parent = root
            obj.location = (0, 0 if info["name"] == "swarm5" else 1.0, .1 if info["name"] == "swarm5" else .65)
            obj["源网格键"] = key
        elif info["type"] == "VisualEffectAsset":
            nodes.append({"key": key, "name": info["name"]})
    root["源预制体"] = role["source"]
    root["源VFX引用"] = json.dumps(nodes, ensure_ascii=False)
    root["场景说明"] = "两件源网格和七张源贴图用于检查。Unity VFX Graph 粒子行为保留在原始解包批次。"


def model_bounds(groups):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bounds = {}
    for name, group in groups.items():
        points = []
        for obj in group.all_objects:
            if obj.type != "MESH" or "源网格键" not in obj or obj.get("细节层", 0) != 0:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            points.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
        values = np.asarray(points)
        bounds[name] = (values.min(axis=0), values.max(axis=0))
    return bounds


def layout(groups, roots):
    bounds = model_bounds(groups)
    names = list(groups)
    for row, start in enumerate(range(0, len(names), 5)):
        row_names = names[start:start + 5]
        spans = {name: max(2.6, bounds[name][1][0] - bounds[name][0][0] + .75) for name in row_names}
        cursor = -sum(spans.values()) / 2
        for name in row_names:
            low, high = bounds[name]
            width = spans[name]
            position = Vector((cursor + width / 2 - (low[0] + high[0]) / 2, (1 - row) * 7.0, 0))
            roots[name]["展示位置"] = tuple(position)
            roots[name].location += position
            cursor += width
    bpy.context.view_layer.update()


def studio(scene, groups):
    stage = collection(scene.collection, "预览摄影棚")
    camera_data = bpy.data.cameras.new("总览相机")
    camera = bpy.data.objects.new("总览相机", camera_data)
    stage.objects.link(camera)
    scene.camera = camera
    single = len(groups) == 1
    bounds = model_bounds(groups)
    corners = []
    for low, high in bounds.values():
        corners.extend(Vector((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1])
                       for z in (low[2], high[2]))
        corners.extend(Vector(((low[0] + high[0]) / 2, y, 0)) for y in (low[1] - .58, high[1] + .58))
    coordinates = np.asarray(corners)
    aim = Vector((coordinates.min(axis=0) + coordinates.max(axis=0)) / 2)
    camera.location = aim + Vector((2, 5, 2.0) if single else (1.3, 21.5, 18.3))
    camera.rotation_euler = (aim - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    scene.render.resolution_x = 1120 if single else 2320
    scene.render.resolution_y = 1000 if single else 1460
    aspect = scene.render.resolution_x / scene.render.resolution_y
    local = camera.rotation_euler.to_matrix().inverted()
    view_points = np.asarray([local @ (point - aim) for point in corners])
    width, height = 2 * np.max(np.abs(view_points[:, :2]), axis=0)
    camera_data.ortho_scale = 1.20 * max(width, height * aspect)
    scene["预览中心"] = tuple(aim)
    floor = bpy.data.meshes.new("摄影棚地面")
    floor.from_pydata([(-30, -30, -.10), (30, -30, -.10), (30, 30, -.10), (-30, 30, -.10)], [], [(0, 1, 2, 3)])
    floor_obj = bpy.data.objects.new(floor.name, floor)
    stage.objects.link(floor_obj)
    floor.materials.append(simple_material("摄影棚地面材质", (.61, .64, .69), .88))
    label_mat = simple_material("预览标签材质", (.018, .03, .05), .8)

    for role, (low, high) in bounds.items():
        data = bpy.data.curves.new(role + "｜标签数据", type="FONT")
        data.body = "Swarm1 / VFX meshes" if role == SWARM else role.removeprefix("ZOM_")
        data.size = .31 if not single else .22
        data.align_x = "CENTER"
        label = bpy.data.objects.new(role + "｜预览标签", data)
        stage.objects.link(label)
        center_x = float((low[0] + high[0]) / 2)
        label.location = (center_x, high[1] + .58, .08)
        label["背面标签位置"] = (center_x, float(low[1] - .58), .08)
        label.rotation_euler = camera.rotation_euler
        data.materials.append(label_mat)

    def area(name, position, energy, size, color):
        data = bpy.data.lights.new(name, type="AREA")
        data.energy, data.shape, data.size, data.color = energy, "DISK", size, color
        light = bpy.data.objects.new(name, data)
        stage.objects.link(light)
        light.location = position
        light.rotation_euler = (aim - light.location).to_track_quat("-Z", "Y").to_euler()

    if single:
        area("主光", (2, 4, 5), 480, 4, (1.0, .94, .86))
        area("补光", (-3, 1, 3.5), 260, 4, (.81, .91, 1.0))
    else:
        area("主光", (1, 8, 13), 3200, 16, (1.0, .94, .86))
        area("补光", (-9, 1, 8), 1800, 12, (.81, .91, 1.0))
        area("轮廓光", (6, -7, 7.5), 1700, 10, (1.0, .91, .8))
    scene.world = bpy.data.worlds.new("摄影棚环境")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    bg.inputs[0].default_value = (.52, .57, .66, 1)
    bg.inputs[1].default_value = .35
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_percentage = 100
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
            space.region_3d.view_location = aim
            space.region_3d.view_distance = camera_data.ortho_scale * 1.20
            space.region_3d.view_perspective = "ORTHO"
            space.shading.type = "MATERIAL"
            space.overlay.show_extras = False


def load_native(paths):
    roles = {}
    for path in paths:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                capture = json.load(stream)
        else:
            capture = read(path)
        for role in capture["roles"]:
            if role["name"] in roles:
                raise ValueError(f"原生采样角色重复：{role['name']}")
            roles[role["name"]] = role
    return roles


def build(batch, output, native, sample):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    scene = bpy.context.scene
    scene.name = "断箭原作丧尸总览" if not sample else "装甲丧尸｜转换样本"
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    scene.render.fps = 24
    role_group = collection(scene.collection, "原作丧尸")
    materials = Materials(batch)
    names = [ARMOUR] if sample else [name for name in batch.roles if name != SWARM] + [SWARM]
    if set(native) != set(names) - {SWARM}:
        raise ValueError("原生采样角色集与场景所需角色不一致")
    groups, roots, textures = {}, {}, set()
    for name in names:
        group = collection(role_group, name)
        groups[name] = group
        role = batch.role(name)
        group["源预制体"] = role["source"]
        root = assembly_root(group, name, (0, 0, 0))
        roots[name] = root
        textures.update(key for key in role["objects"] if batch.objects[key]["type"] == "Texture2D")
        if name == SWARM:
            build_swarm(batch, materials, group, role, root)
        else:
            rigged_character(batch, materials, scene, group, role, native[name], root)
    scene.frame_set(DISPLAY_FRAME)
    materials.preserve_textures(textures)
    if not sample:
        layout(groups, roots)
    studio(scene, groups)
    scene["源批次"] = batch.directory.name
    scene["源对象清单SHA256"] = sha(batch.delivery / "对象清单.json")
    scene.frame_end = math.ceil(max(action.frame_range[1] for action in bpy.data.actions))
    bpy.context.view_layer.objects.active = None
    text = bpy.data.texts.new("使用说明")
    text.write(
        "在原作丧尸集合中展开角色。LOD0 默认可见，切换前请关闭其他细节层。\n"
        "取消骨架的视图隐藏后，即可在动作编辑器选择该角色名称开头的 Actions。\n"
        "每个展示根控制原作角色在排布中的位置。摄影棚可在大纲视图中单独隐藏。\n"
        "Swarm 使用独立集合，保留两件源网格、贴图和 VFX 引用。\n"
        "材质由源 HDRP 参数与贴图适配为 Blender 节点，Unity VFX Graph 行为仍保存在解包数据中。"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False, compress=True)


def args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--native", required=True, action="append", type=Path)
    parser.add_argument("--sample", action="store_true")
    val = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    val.batch = val.batch.resolve()
    val.output = val.output.resolve()
    val.native = [p.resolve() for p in val.native]
    if val.batch.parent != BATCH_ROOT or not val.output.is_relative_to(val.batch):
        parser.error("工程输出必须在对应断箭批次内")
    if val.output.is_relative_to(val.batch / "交付") or val.output.suffix != ".blend" or val.output.exists():
        parser.error("输出必须是解包交付目录之外的新 .blend")
    return val


if __name__ == "__main__":
    params = args()
    build(Batch(params.batch), params.output, load_native(params.native), params.sample)
