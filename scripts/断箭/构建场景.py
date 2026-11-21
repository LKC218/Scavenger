"""由只读场景 JSON 与 NPZ 构建保留层级、原始几何和近似材质的 Blender 工程。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="导出场景生成的 scene.json")
    parser.add_argument("--output", type=Path, required=True, help="全新 blend 文件，禁止覆盖")
    parser.add_argument("--mask-layout", choices=["hdrp", "none"], default="hdrp", help="遮罩通道推断；none 不接遮罩")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def output_path(path):
    path = path.resolve()
    allowed = [ROOT / "local-data/断箭/批次", ROOT / "tmp/断箭"]
    if path.suffix.lower() != ".blend":
        raise ValueError("输出扩展名必须为 .blend")
    if not any(path.is_relative_to(base.resolve()) for base in allowed):
        raise ValueError("输出必须位于本地断箭批次目录或断箭临时目录")
    if path.exists() or path.with_suffix(".构建记录.json").exists():
        raise FileExistsError("工程或构建记录已存在，拒绝覆盖")
    return path


def pairs(value):
    if isinstance(value, dict):
        return value
    result = {}
    for item in value or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            result[item[0]] = item[1]
        elif isinstance(item, dict) and "first" in item and "second" in item:
            result[item["first"]] = item["second"]
    return result


def first(mapping, keys, default):
    return next((mapping[key] for key in keys if key in mapping), default)


class Builder:
    def __init__(self, source, data, mask_layout):
        import bpy

        self.source = source
        self.data = data
        self.mask_layout = mask_layout
        self.warnings = list(data.get("warnings", []))
        self.images = {}
        self.materials = {}
        self.camo_tasks = []
        self.report = {
            "source": str(source), "source_sha256": sha256(source),
            "source_path": data.get("source_path"), "name": data.get("name"),
            "blender": bpy.app.version_string, "mask_layout": mask_layout,
            "coordinate_policy": "输入已经转换为 Blender 坐标；直接使用本地矩阵、顶点、法线与三角形绕序",
            "material_policy": "Principled 材质近似；原始 Shader、运行时效果、动画未等价还原",
            "warnings": self.warnings, "missing_files": [], "materials": {}, "meshes": [],
        }

    def warn(self, text):
        self.warnings.append(text)

    def resolve(self, path):
        path = Path(path)
        return (self.source.parent / path).resolve() if not path.is_absolute() else path.resolve()

    def image(self, path, color_space):
        import bpy

        path = self.resolve(path)
        if not path.is_file():
            if str(path) not in self.report["missing_files"]:
                self.report["missing_files"].append(str(path))
                self.warn(f"贴图文件缺失：{path.name}")
            return None
        key = (str(path), color_space)
        if key not in self.images:
            image = bpy.data.images.load(str(path), check_existing=False)
            image.colorspace_settings.name = color_space
            image.pack()
            self.images[key] = image
        return self.images[key]

    def texture(self, tree, path, info, color_space, label, uv_name=None):
        image = self.image(path, color_space)
        if image is None:
            return None
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image, node.label, node.name = image, label, label
        node.interpolation = "Linear"
        scale, offset = info.get("scale", {}), info.get("offset", {})
        sx, sy = float(scale.get("x", 1)), float(scale.get("y", 1))
        ox, oy = float(offset.get("x", 0)), float(offset.get("y", 0))
        if uv_name or (sx, sy, ox, oy) != (1, 1, 0, 0):
            uv = tree.nodes.new("ShaderNodeUVMap")
            uv.uv_map = uv_name or "UVMap"
            multiply = tree.nodes.new("ShaderNodeVectorMath")
            multiply.operation = "MULTIPLY"
            multiply.inputs[1].default_value = (sx, sy, 1)
            add = tree.nodes.new("ShaderNodeVectorMath")
            add.operation = "ADD"
            add.inputs[1].default_value = (ox, oy, 0)
            tree.links.new(uv.outputs["UV"], multiply.inputs[0])
            tree.links.new(multiply.outputs["Vector"], add.inputs[0])
            tree.links.new(add.outputs["Vector"], node.inputs["Vector"])
        return node

    @staticmethod
    def math(tree, operation, socket, value, clamp=False):
        node = tree.nodes.new("ShaderNodeMath")
        node.operation, node.use_clamp = operation, clamp
        tree.links.new(socket, node.inputs[0])
        node.inputs[1].default_value = value
        return node.outputs[0]

    def orm_image(self, image, floats, unit_v3, encoding, name, camo=False):
        import bpy
        import numpy as np

        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)
        if encoding == "srgb":
            rgb = pixels[:, :, :3]
            pixels[:, :, :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        orm = np.ones_like(pixels)
        smoothness = float(first(floats, ["_Smoothness", "_Glossiness"], 0.5))
        if unit_v3:
            minimum_ao = 1 - float(floats.get("_AOClamp", 1)) if camo else 0
            orm[:, :, 0] = np.clip(pixels[:, :, 1] * float(floats.get("_AOStrange", 1)), minimum_ao, 1)
            orm[:, :, 1] = 1 - np.clip(pixels[:, :, 3] * smoothness, 0, 1)
            orm[:, :, 2] = np.clip(pixels[:, :, 0] * float(first(floats, ["_Metal", "_Metallic"], 0)), 0, 1)
        else:
            def remap(values, minimum, maximum):
                return values * (maximum - minimum) + minimum

            strength = float(floats.get("_OcclusionStrength", 1))
            orm[:, :, 0] = np.clip(remap(pixels[:, :, 1], float(floats.get("_AORemapMin", 0)), float(floats.get("_AORemapMax", 1))) * strength + 1 - strength, 0, 1)
            orm[:, :, 1] = 1 - np.clip(remap(pixels[:, :, 3], float(floats.get("_SmoothnessRemapMin", 0)), float(floats.get("_SmoothnessRemapMax", 1))) * smoothness, 0, 1)
            orm[:, :, 2] = np.clip(remap(pixels[:, :, 0], float(floats.get("_MetallicRemapMin", 0)), float(floats.get("_MetallicRemapMax", 1))), 0, 1)
        result = bpy.data.images.new(f"派生ORM-{name}", width, height, alpha=True, is_data=True)
        result.colorspace_settings.name = "Non-Color"
        result.pixels.foreach_set(orm.ravel())
        result.pack()
        self.images[(f"ORM-{name}", "Non-Color")] = result
        return result

    def camo_graph(self, tree, record, material, principled, color_socket, color_file, textures, derived, floats, colors, mask_file, mask_info):
        camo_info = textures.get("Layer_A97CDC25_1", {})
        camo_file = camo_info.get("file")
        if not (color_socket and color_file and camo_file and mask_file):
            self.warn(f"迷彩材质缺少底色、迷彩或遮罩，未完成叠加：{material.name}")
            record["camo_bake"] = {"status": "输入缺失"}
            return color_socket
        tiling = colors.get("_CamoTiling", {})
        camo_transform = {"scale": {"x": tiling.get("r", tiling.get("x", 1)), "y": tiling.get("g", tiling.get("y", 1))}, "offset": {"x": floats.get("_OffsetX", 0), "y": floats.get("_OffsetY", 0)}}
        camo = self.texture(tree, camo_file, camo_transform, "sRGB", "UV1 迷彩颜色", "UV1")
        mask = self.texture(tree, mask_file, mask_info, "sRGB" if derived.get("mask_encoding") == "srgb" else "Non-Color", "原始迷彩权重", "UVMap")
        if camo is None or mask is None:
            self.warn(f"迷彩材质贴图缺失，未完成叠加：{material.name}")
            record["camo_bake"] = {"status": "贴图缺失"}
            return color_socket
        separate = tree.nodes.new("ShaderNodeSeparateColor")
        separate.mode = "RGB"
        tree.links.new(mask.outputs["Color"], separate.inputs["Color"])
        multiply = tree.nodes.new("ShaderNodeMixRGB")
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1
        tree.links.new(camo.outputs["Color"], multiply.inputs[1])
        tree.links.new(separate.outputs["Blue"], multiply.inputs[2])
        add = tree.nodes.new("ShaderNodeMixRGB")
        add.blend_type = "ADD"
        add.inputs[0].default_value = 1
        tree.links.new(color_socket, add.inputs[1])
        tree.links.new(multiply.outputs[0], add.inputs[2])
        self.camo_tasks.append({"material": material, "principled": principled, "color": add.outputs[0], "base_file": color_file, "record": record})
        record["camo_bake"] = {"status": "待烘焙", "formula": "线性底色(UV0)+线性迷彩(UV1×CamoTiling+Offset)×线性Mask.B", "transform": camo_transform}
        return add.outputs[0]

    def bake_camo(self):
        import bmesh
        import bpy

        if not self.camo_tasks:
            return
        scene = bpy.context.scene
        previous_engine = scene.render.engine
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.render.threads_mode, scene.render.threads = "FIXED", 2
        scene.render.bake.use_selected_to_active = False
        scene.render.bake.use_clear = False
        scene.render.bake.margin = 16
        scene.render.bake.target = "IMAGE_TEXTURES"
        try:
            for task in self.camo_tasks:
                material, principled = task["material"], task["principled"]
                tree = material.node_tree
                base = self.image(task["base_file"], "sRGB")
                width, height = base.size
                baked = bpy.data.images.new("迷彩烘焙-" + material.name, width, height, alpha=True)
                baked.colorspace_settings.name = "sRGB"
                baked.generated_color = (0, 0, 0, 0)
                target_node = tree.nodes.new("ShaderNodeTexImage")
                target_node.image, target_node.name = baked, "UV0 迷彩烘焙底色"
                tree.nodes.active = target_node
                emission = tree.nodes.new("ShaderNodeEmission")
                emission.inputs["Strength"].default_value = 1
                tree.links.new(task["color"], emission.inputs["Color"])
                output = next(node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output)
                original_output = output.inputs["Surface"].links[0].from_socket
                tree.links.new(emission.outputs[0], output.inputs["Surface"])
                temporary = []
                try:
                    # 仅复制该材质使用的面，避免同一物体的其它材质污染目标图。
                    for obj in list(self.collection.objects):
                        if obj.type != "MESH" or material not in obj.data.materials.values():
                            continue
                        if obj.data.uv_layers.get("UV1") is None:
                            raise ValueError(f"迷彩材质需要 UV1：{obj.name}")
                        slots = {index for index, candidate in enumerate(obj.data.materials) if candidate == material}
                        bm = bmesh.new()
                        try:
                            bm.from_mesh(obj.data)
                            bmesh.ops.delete(bm, geom=[face for face in bm.faces if face.material_index not in slots], context="FACES")
                            if not bm.faces:
                                continue
                            mesh = bpy.data.meshes.new("临时迷彩烘焙网格")
                            bm.to_mesh(mesh)
                        finally:
                            bm.free()
                        mesh.materials.clear()
                        mesh.materials.append(material)
                        for face in mesh.polygons:
                            face.material_index = 0
                        mesh.uv_layers.active_index = mesh.uv_layers.find("UVMap")
                        mesh.uv_layers["UVMap"].active_render = True
                        copy = bpy.data.objects.new("临时迷彩烘焙对象", mesh)
                        scene.collection.objects.link(copy)
                        copy.matrix_world = obj.matrix_world
                        temporary.append(copy)
                    if not temporary:
                        raise ValueError(f"迷彩材质没有对应网格：{material.name}")
                    bpy.ops.object.select_all(action="DESELECT")
                    for obj in temporary:
                        obj.select_set(True)
                    bpy.context.view_layer.objects.active = temporary[0]
                    bpy.ops.object.bake(type="EMIT")
                    baked.pack()
                    self.images[("Camo-" + material.name, "sRGB")] = baked
                    tree.links.new(target_node.outputs["Color"], principled.inputs["Base Color"])
                    task["record"]["camo_bake"].update({"status": "已烘焙", "resolution": [width, height], "source_meshes": len(temporary), "method": "独立临时网格 Cycles CPU EMIT 烘焙，保留原网格、UV0 和 UV1"})
                    task["record"]["notes"].append("迷彩已折算到 UV0 底色；重叠 UV0 可能无法唯一表示不同 UV1 颜色，需视觉复核；烘焙图按原底色分辨率保存")
                finally:
                    tree.links.new(original_output, output.inputs["Surface"])
                    tree.nodes.remove(emission)
                    for obj in temporary:
                        mesh = obj.data
                        bpy.data.objects.remove(obj, do_unlink=True)
                        bpy.data.meshes.remove(mesh)
        finally:
            scene.render.engine = previous_engine

    def material(self, mat_id):
        import bpy

        if mat_id in self.materials:
            return self.materials[mat_id]
        entry = self.data.get("materials", {}).get(mat_id)
        if entry is None:
            self.warn(f"材质定义缺失：{mat_id}；保留中性占位材质")
            entry = {"name": f"缺失材质-{mat_id}"}
        raw = entry.get("raw", {})
        props = raw.get("m_SavedProperties", {})
        floats, colors = pairs(props.get("m_Floats")), pairs(props.get("m_Colors"))
        textures, derived = entry.get("textures", {}), entry.get("derived", {})
        camo = derived.get("shader_model") == "UnitV4_Camo"
        unit_v3 = derived.get("shader_model") in {"UnitV3", "UnitV4_Camo"} or ("_Metal" in floats and "_AOStrange" in floats)
        color = first(colors, ["_BaseColor", "_Color", "_MainColor"], {"r": 0.8, "g": 0.8, "b": 0.8, "a": 1})
        rgba = tuple(float(color.get(key, 1)) for key in "rgba")
        metallic = float(first(floats, ["_Metal", "_Metallic"] if unit_v3 else ["_Metallic"], 0))
        smoothness = float(first(floats, ["_Smoothness", "_Glossiness"], 0.5))
        material = bpy.data.materials.new(entry.get("name", mat_id))
        material.use_nodes = True
        material.diffuse_color = rgba
        material["原材质标识"] = mat_id
        material["材质说明"] = "由原始参数构建的 Principled 近似，原 Shader 效果未完全还原"
        tree = material.node_tree
        principled = tree.nodes.get("Principled BSDF")
        principled.inputs["Base Color"].default_value = rgba
        principled.inputs["Metallic"].default_value = max(0, min(1, metallic))
        principled.inputs["Roughness"].default_value = max(0, min(1, 1 - smoothness))
        principled.inputs["Alpha"].default_value = max(0, min(1, rgba[3]))
        principled.inputs["IOR"].default_value = float(first(floats, ["_Ior", "_IOR"], 1.45))
        double_sided = bool(floats.get("_DoubleSidedEnable", 0)) or floats.get("_CullMode") == 0
        material.use_backface_culling = not double_sided
        transparent = rgba[3] < 0.999 or floats.get("_SurfaceType", 0) > 0 or floats.get("_Mode", 0) >= 2
        alpha_clip = bool(first(floats, ["_AlphaCutoffEnable", "_ENABLEALPHACLIPPING", "_AlphaClip"], 0))
        if transparent or alpha_clip:
            material.surface_render_method = "DITHERED"
        record = {
            "name": material.name, "shader_model": "UnitV4_Camo" if camo else "UnitV3推断" if unit_v3 else "HDRP及通用参数近似",
            "source_shader_reference": raw.get("m_Shader"),
            "base_rgba": rgba, "metallic": metallic, "smoothness": smoothness,
            "transparent": transparent, "alpha_clip": alpha_clip,
            "textures": {}, "unused_texture_properties": [], "notes": [],
        }

        def choose(keys):
            return next(((key, textures[key]) for key in keys if textures.get(key, {}).get("file")), (None, {}))

        color_key, color_info = choose(["_BaseColorMap", "BaseColorMap", "_MainTex", "MainTex", "_Albedo", "Layer_A97CDC25"])
        color_file = derived.get("base_color") or color_info.get("file")
        record["base_color_property"] = "derived.base_color" if derived.get("base_color") else color_key
        normal_file = derived.get("normal")
        mask_key, mask_info = choose(["_MaskMap", "MaskMap"])
        mask_file = derived.get("mask") or mask_info.get("file")
        color_socket = None
        if color_file:
            color_node = self.texture(tree, color_file, color_info, "sRGB", "原始底色")
            if color_node:
                record["textures"]["base_color"] = str(self.resolve(color_file))
                color_socket = color_node.outputs["Color"]
                if rgba[:3] != (1, 1, 1):
                    tint = tree.nodes.new("ShaderNodeMixRGB")
                    tint.blend_type = "MULTIPLY"
                    tint.inputs[0].default_value = 1
                    tint.inputs[2].default_value = (*rgba[:3], 1)
                    tree.links.new(color_socket, tint.inputs[1])
                    color_socket = tint.outputs[0]
                if transparent or alpha_clip:
                    alpha = color_node.outputs["Alpha"]
                    if rgba[3] != 1:
                        alpha = self.math(tree, "MULTIPLY", alpha, rgba[3])
                    if alpha_clip:
                        cutoff = float(first(floats, ["_AlphaClipThreshold", "_AlphaCutoff", "_Cutoff"], 0.5))
                        alpha = self.math(tree, "GREATER_THAN", alpha, cutoff)
                        record["notes"].append("AlphaClip 使用原阈值的二值透明近似")
                    tree.links.new(alpha, principled.inputs["Alpha"])
        elif not any(key in colors for key in ["_BaseColor", "_Color", "_MainColor"]):
            record["notes"].append("缺少底色及颜色参数，保留 Blender 中性默认色")

        if normal_file:
            normal_node = self.texture(tree, normal_file, {}, "Non-Color", "已转换切线法线")
            if normal_node:
                normal_map = tree.nodes.new("ShaderNodeNormalMap")
                normal_map.inputs["Strength"].default_value = float(first(floats, ["_NormalStrange", "_NormalScale", "_BumpScale"], 1))
                tree.links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
                tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
                record["textures"]["normal"] = str(self.resolve(normal_file))
        elif any(key in textures for key in ["_NormalMap", "_BumpMap"]):
            record["notes"].append("原始法线图的通道编码未确认，未直接接入；需要 derived.normal")

        if mask_file and self.mask_layout == "hdrp":
            encoding = derived.get("mask_encoding", "linear")
            if encoding not in ("linear", "srgb"):
                raise ValueError(f"未知遮罩编码：{encoding}")
            mask_node = self.texture(tree, mask_file, mask_info, "Non-Color", "派生金属粗糙度")
            if mask_node:
                mask_node.image = self.orm_image(mask_node.image, floats, unit_v3, encoding, material.name, camo=camo)
                separate = tree.nodes.new("ShaderNodeSeparateColor")
                separate.mode = "RGB"
                tree.links.new(mask_node.outputs["Color"], separate.inputs["Color"])
                tree.links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
                tree.links.new(separate.outputs["Green"], principled.inputs["Roughness"])
                # glTF 导出器识别此约定节点组；Eevee 没有等价的独立 AO 材质输入。
                group = bpy.data.node_groups.get("glTF Material Output")
                if group is None:
                    group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
                    group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
                    group.nodes.new("NodeGroupInput")
                output = tree.nodes.new("ShaderNodeGroup")
                output.node_tree = group
                tree.links.new(separate.outputs["Red"], output.inputs["Occlusion"])
                record["textures"]["mask"] = str(self.resolve(mask_file))
                record["mask_encoding"] = encoding
                record["notes"].append("原遮罩 R金属度、G环境遮蔽、A光滑度按原标量转换为嵌入 ORM（R遮蔽、G粗糙度、B金属度）；Eevee 未单独施加 AO，glTF 使用约定 AO 输入")
        elif mask_file:
            record["notes"].append("按 --mask-layout none 保留遮罩文件但未接入")
        if camo:
            color_socket = self.camo_graph(tree, record, material, principled, color_socket, color_file, textures, derived, floats, colors, mask_file, mask_info)
        if color_socket:
            tree.links.new(color_socket, principled.inputs["Base Color"])
        used = {key for key in (color_key, mask_key) if key}
        record["unused_texture_properties"] = [key for key in textures if key not in used]
        if transparent:
            record["notes"].append("透明度直接采用原颜色 Alpha；折射、玻璃高光等原 Shader 效果未完整还原")
        for index, node in enumerate(tree.nodes):
            node.location = ((index % 5) * 230 - 900, -(index // 5) * 260)
        self.report["materials"][mat_id] = record
        self.materials[mat_id] = material
        return material

    def mesh(self, entry, parent):
        import bpy
        import numpy as np
        from mathutils import Matrix

        source = self.resolve(entry["file"])
        with np.load(source, allow_pickle=False) as archive:
            vertices = np.asarray(archive["vertices"], dtype=np.float32)
            triangles = np.asarray(archive["triangles"])
            normals = np.asarray(archive["normals"], dtype=np.float32)
            uv0 = np.asarray(archive["uv0"], dtype=np.float32)
            uv1 = np.asarray(archive["uv1"], dtype=np.float32) if "uv1" in archive else np.empty(0)
            indices = np.asarray(archive["material_indices"])
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all():
            raise ValueError(f"顶点数组无效：{source.name}")
        if triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles) or not np.issubdtype(triangles.dtype, np.integer):
            raise ValueError(f"三角形数组无效：{source.name}")
        if triangles.min() < 0 or triangles.max() >= len(vertices):
            raise ValueError(f"三角形索引越界：{source.name}")
        slots = entry.get("materials", []) or ["missing"]
        if indices.shape != (len(triangles),) or not np.issubdtype(indices.dtype, np.integer):
            raise ValueError(f"面材质索引数组无效：{source.name}")
        if indices.min() < 0 or indices.max() >= len(slots):
            raise ValueError(f"面材质槽索引越界：{source.name}")
        mesh = bpy.data.meshes.new(parent.name + "-网格")
        mesh.from_pydata(vertices.tolist(), [], triangles.tolist())
        mesh.update()
        if len(mesh.polygons) != len(triangles):
            raise ValueError(f"构建后三角形数量不符：{source.name}")
        for mat_id in slots:
            mesh.materials.append(self.material(str(mat_id)))
        mesh.polygons.foreach_set("material_index", indices.astype(np.int32))
        if uv0.size:
            if uv0.shape != (len(vertices), 2) or not np.isfinite(uv0).all():
                raise ValueError(f"UV 数组无效：{source.name}")
            loop_indices = np.empty(len(mesh.loops), dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", loop_indices)
            layer = mesh.uv_layers.new(name="UVMap")
            layer.data.foreach_set("uv", uv0[loop_indices].ravel())
        else:
            self.warn(f"网格缺少 UV0：{source.name}")
        if uv1.size:
            if not uv0.size:
                raise ValueError(f"UV1 存在但缺少 UV0：{source.name}")
            if uv1.shape != (len(vertices), 2) or not np.isfinite(uv1).all():
                raise ValueError(f"UV1 数组无效：{source.name}")
            layer = mesh.uv_layers.new(name="UV1")
            layer.data.foreach_set("uv", uv1[loop_indices].ravel())
            mesh.uv_layers.active_index = mesh.uv_layers.find("UVMap")
            mesh.uv_layers["UVMap"].active_render = True
        if normals.size:
            if normals.shape != vertices.shape or not np.isfinite(normals).all():
                raise ValueError(f"法线数组无效：{source.name}")
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
            mesh.normals_split_custom_set_from_vertices(normals.tolist())
        else:
            self.warn(f"网格缺少源法线，采用 Blender 面法线：{source.name}")
        obj = bpy.data.objects.new(parent.name + "-渲染网格", mesh)
        self.collection.objects.link(obj)
        obj.parent = parent
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_local = Matrix.Identity(4)
        obj["原渲染器标识"] = str(entry.get("renderer_id", ""))
        obj["原蒙皮网格"] = bool(entry.get("skinned", False))
        if entry.get("skinned"):
            obj["姿态说明"] = "使用上游导出的静态顶点姿态，不含骨骼绑定或动画"
        self.report["meshes"].append({
            "name": obj.name, "source": str(source), "source_sha256": sha256(source),
            "vertices": len(vertices), "triangles": len(triangles), "uv0": bool(uv0.size),
            "uv1": bool(uv1.size), "source_normals": bool(normals.size), "materials": slots,
            "renderer_id": entry.get("renderer_id"), "skinned_static": bool(entry.get("skinned")),
        })

    def build(self):
        import bpy
        import numpy as np
        from mathutils import Matrix

        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.collection = bpy.data.collections.new("模型")
        bpy.context.scene.collection.children.link(self.collection)
        nodes, objects = {}, {}
        for node in self.data["nodes"]:
            identity = str(node["id"])
            if identity in nodes:
                raise ValueError(f"节点标识重复：{identity}")
            nodes[identity] = node
            obj = bpy.data.objects.new(node.get("name", identity), None)
            obj["原节点标识"] = identity
            obj.empty_display_size = 0.2
            self.collection.objects.link(obj)
            objects[identity] = obj
        for identity, node in nodes.items():
            seen = {identity}
            parent = node.get("parent")
            while parent is not None:
                parent = str(parent)
                if parent not in nodes:
                    raise ValueError(f"节点父级不存在：{identity} → {parent}")
                if parent in seen:
                    raise ValueError(f"节点层级形成循环：{identity}")
                seen.add(parent)
                parent = nodes[parent].get("parent")
            matrix = np.asarray(node["matrix"], dtype=float)
            if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1]):
                raise ValueError(f"节点本地矩阵无效：{identity}")
            obj = objects[identity]
            parent = node.get("parent")
            if parent is not None:
                obj.parent = objects[str(parent)]
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_local = Matrix(matrix.tolist())
            for entry in node.get("meshes", []):
                self.mesh(entry, obj)
        if not self.report["meshes"]:
            raise ValueError("场景没有可构建的网格")
        scene = bpy.context.scene
        scene.name = self.data.get("name", "断箭场景")
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1
        scene["来源场景"] = self.data.get("source_path", "")
        scene["坐标说明"] = self.report["coordinate_policy"]
        scene["材质说明"] = self.report["material_policy"]
        scene["姿态说明"] = self.data.get("pose", "静态导出；不代表运行时动画或实车尺度验收")
        bpy.context.view_layer.update()
        self.bake_camo()
        self.report.update({
            "node_count": len(nodes), "mesh_count": len(self.report["meshes"]),
            "vertices": sum(item["vertices"] for item in self.report["meshes"]),
            "triangles": sum(item["triangles"] for item in self.report["meshes"]),
            "packed_images": len(self.images),
            "has_camera_or_light": any(obj.type in {"CAMERA", "LIGHT"} for obj in scene.objects),
        })


def main():
    import bpy

    args = arguments()
    source, target = args.input.resolve(), output_path(args.output)
    started = time.time()
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    builder = Builder(source, data, args.mask_layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = target.with_suffix(".构建记录.json")
    try:
        builder.build()
        if target.exists():
            raise FileExistsError("输出在构建期间出现，拒绝覆盖")
        bpy.ops.wm.save_as_mainfile(filepath=str(target), check_existing=False)
        builder.report.update({"status": "构建完成", "output": str(target), "output_sha256": sha256(target)})
    except Exception:
        builder.report.update({"status": "构建失败", "error": traceback.format_exc()})
        raise
    finally:
        builder.report["elapsed_seconds"] = round(time.time() - started, 3)
        with record.open("x", encoding="utf-8") as stream:
            json.dump(builder.report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: builder.report.get(key) for key in ["status", "output", "node_count", "mesh_count", "vertices", "triangles", "packed_images", "elapsed_seconds"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
