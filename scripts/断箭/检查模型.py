"""在独立 Blender 进程中检查提取模型、生成 Eevee 预览并回读交付文件。"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]
VIEW_NAMES = ["整体", "反侧", "正面", "顶部"]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def allowed_output(path):
    path = Path(path).resolve()
    allowed = [ROOT / "local-data/断箭/批次", ROOT / "tmp/断箭"]
    if not any(path.is_relative_to(parent.resolve()) for parent in allowed):
        raise ValueError("输出必须位于本地断箭批次目录或断箭临时目录")
    return path


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="输入 GLB、glTF、FBX、OBJ 或 blend")
    parser.add_argument("--output", type=Path, help="新建输出目录，拒绝覆盖")
    parser.add_argument("--blender", type=Path, help="Blender 程序，默认读取本地配置")
    parser.add_argument("--resolution", type=int, default=768)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--views", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--collection", help="blend 输入中需要检查的集合；默认采用全部模型对象")
    parser.add_argument("--timeout", type=float, default=1800, help="每个独立进程的最长秒数")
    parser.add_argument("--contact-sheet", type=Path, nargs="+", help="已有预览图列表")
    parser.add_argument("--sheet-output", type=Path, help="新建联系表 PNG")
    parser.add_argument("--worker", choices=["prepare", "reopen"], help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    if args.resolution < 64 or args.samples < 1 or args.timeout <= 0:
        parser.error("分辨率至少 64，采样与超时必须为正数")
    return args


def make_sheet(files, destination):
    from PIL import Image, ImageDraw, ImageFont

    destination = allowed_output(destination)
    if destination.exists():
        raise FileExistsError("联系表已存在，拒绝覆盖")
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height, label = 384, 384, 40
    columns = min(4, len(files))
    rows = math.ceil(len(files) / columns)
    canvas = Image.new("RGB", (columns * width, rows * (height + label)), (27, 30, 34))
    draw = ImageDraw.Draw(canvas)
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font_path = next((path for path in [fonts / "msyh.ttc", fonts / "simhei.ttf"] if path.is_file()), None)
    font = ImageFont.truetype(str(font_path), 16) if font_path else ImageFont.load_default(size=16)
    for index, path in enumerate(files):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((width, height))
        x, y = (index % columns) * width, (index // columns) * (height + label)
        canvas.paste(image, (x + (width - image.width) // 2, y + (height - image.height) // 2))
        text = f"{index + 1:03} {path.parent.parent.name[:30]} / {path.stem}"
        draw.text((x + 8, y + height + 8), text, fill=(225, 229, 232), font=font)
    canvas.save(destination)
    print(str(destination))


def import_model(path):
    import bpy

    suffix = path.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if suffix in {".glb", ".gltf"}:
            bpy.ops.import_scene.gltf(filepath=str(path))
        elif suffix == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(path))
        elif suffix == ".obj":
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            raise ValueError(f"不支持的输入格式：{suffix}")


def model_objects(collection_name=None):
    import bpy

    if collection_name:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise ValueError("指定的模型集合不存在")
        candidates = list(collection.all_objects)
    else:
        candidates = list(bpy.context.scene.objects)
    return [obj for obj in candidates if obj.type not in {"CAMERA", "LIGHT"}]


def inspect(objects):
    import bpy
    from mathutils import Vector

    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes or not any(len(obj.data.polygons) for obj in meshes):
        raise ValueError("模型没有可检查的网格面")
    bpy.context.view_layer.update()
    corners = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    lower = [min(point[axis] for point in corners) for axis in range(3)]
    upper = [max(point[axis] for point in corners) for axis in range(3)]
    triangles, invalid = 0, 0
    details = []
    for obj in meshes:
        mesh = obj.data
        mesh.calc_loop_triangles()
        triangles += len(mesh.loop_triangles)
        invalid += sum(not all(math.isfinite(value) for value in vertex.co) for vertex in mesh.vertices)
        details.append({"名称": obj.name, "顶点数": len(mesh.vertices), "三角形数": len(mesh.loop_triangles),
                        "UV层数": len(mesh.uv_layers), "材质槽数": len(obj.material_slots),
                        "隐藏渲染": bool(obj.hide_render), "修改器": [modifier.type for modifier in obj.modifiers]})
    materials = {material for obj in meshes for material in obj.data.materials if material is not None}
    images = set()
    untextured = []
    for material in materials:
        textures = []
        if material.use_nodes and material.node_tree:
            textures = [node.image for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image]
        images.update(textures)
        if not textures:
            untextured.append(material.name)
    missing = []
    for image in images:
        packed = bool(image.packed_file or image.packed_files)
        if image.source in {"FILE", "TILED"} and not packed:
            path = Path(bpy.path.abspath(image.filepath))
            if not path.is_file():
                missing.append({"图像": image.name, "引用": image.filepath})
    return {"Blender版本": bpy.app.version_string, "对象数": len(objects), "网格数": len(meshes),
            "顶点数": sum(len(obj.data.vertices) for obj in meshes), "三角形数": triangles,
            "非有限坐标顶点数": invalid, "包围盒最小": lower, "包围盒最大": upper,
            "尺寸": [upper[axis] - lower[axis] for axis in range(3)], "尺度依据": "导入尺度，未经实车尺寸标定",
            "材质数": len(materials), "贴图数": len(images), "缺失贴图": missing,
            "无图像节点材质": sorted(untextured), "网格明细": details,
            "其他对象类型": sorted({obj.type for obj in objects if obj.type not in {"MESH", "EMPTY", "ARMATURE"}})}


def studio(objects, stats, resolution, samples):
    import bpy
    from mathutils import Vector

    scene = bpy.context.scene
    model = bpy.data.collections.new("检查模型")
    scene.collection.children.link(model)
    for obj in objects:
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        model.objects.link(obj)
    # 仅保留本次检查对象；源工程由独立进程读取，不写回。
    keep = set(objects)
    for obj in list(scene.objects):
        if obj not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)
    stage = bpy.data.collections.new("检查摄影棚")
    scene.collection.children.link(stage)

    def put(obj):
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        stage.objects.link(obj)

    size = max(stats["尺寸"])
    if size <= 0 or not math.isfinite(size):
        raise ValueError("包围盒尺寸无效")
    center = Vector([(stats["包围盒最小"][axis] + stats["包围盒最大"][axis]) / 2 for axis in range(3)])
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.world = bpy.data.worlds.new("检查环境")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.12, 0.14, 0.17, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.35
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_samples"):
        scene.eevee.taa_samples = samples
    scene.view_settings.view_transform = "AgX"
    bpy.ops.mesh.primitive_plane_add(size=size * 5, location=(center.x, center.y, stats["包围盒最小"][2] - size * .006))
    floor = bpy.context.object
    floor.name = "检查地面"
    put(floor)
    material = bpy.data.materials.new("检查地面材质")
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (.09, .11, .14, 1)
    material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = .9
    floor.data.materials.append(material)
    for name, direction, energy in [("主光", (1, -1, 2), 85), ("辅光", (-1, -.2, 1), 35), ("轮廓光", (0, 1, 1.5), 100)]:
        lamp = bpy.data.lights.new(name, "AREA")
        lamp.energy = energy * size * size
        lamp.shape = "DISK"
        lamp.size = size * 1.8
        obj = bpy.data.objects.new(name, lamp)
        stage.objects.link(obj)
        obj.location = center + Vector(direction) * size
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("检查相机")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = size * 1.6
    camera_data.clip_start = max(size * .0001, .001)
    camera_data.clip_end = size * 30
    camera = bpy.data.objects.new("检查相机", camera_data)
    stage.objects.link(camera)
    scene.camera = camera
    return camera, center, size


def render_views(objects, stats, output, args, count=None):
    import bpy
    from mathutils import Vector

    output.mkdir(parents=True, exist_ok=True)
    camera, center, size = studio(objects, stats, args.resolution, args.samples)
    directions = [(1, -1, .8), (-1, 1, .7), (0, -1, .25), (0, 0, 1)]
    paths = []
    for name, direction in zip(VIEW_NAMES[:count or args.views], directions):
        camera.location = center + Vector(direction).normalized() * size * 3
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        path = output / f"{name}.png"
        bpy.context.scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(path.name)
    camera.location = center + Vector(directions[0]).normalized() * size * 3
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    return paths


def worker(args):
    import bpy

    started = time.time()
    output = args.output.resolve()
    if args.worker == "prepare":
        import_model(args.input.resolve())
        objects = model_objects(args.collection)
        stats = inspect(objects)
        if stats["非有限坐标顶点数"]:
            raise ValueError("网格包含非有限坐标")
        stats["预览"] = render_views(objects, stats, output / "预览", args)
        # 打包到新的工程文件，外部源贴图保持原样。
        for image in bpy.data.images:
            if image.source == "FILE" and image.has_data and not image.packed_file:
                try:
                    image.pack()
                except RuntimeError:
                    pass
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = next(obj for obj in objects if obj.type == "MESH")
        bpy.ops.export_scene.gltf(filepath=str(output / "model.glb"), export_format="GLB", use_selection=True,
                                  export_animations=False, export_apply=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output / "model.blend"), check_existing=False)
        stats["阶段耗时秒"] = time.time() - started
        stats["视觉复核"] = "待智能体查看预览；结构检查不代替视觉判断"
        write_json(output / "导入检查.json", stats)
    else:
        bpy.ops.wm.open_mainfile(filepath=str(output / "model.blend"))
        collection = bpy.data.collections.get("检查模型")
        if collection is None:
            raise ValueError("重开工程缺少模型集合")
        reopened = inspect(list(collection.all_objects))
        import_model(output / "model.glb")
        objects = model_objects()
        exported = inspect(objects)
        exported["预览"] = render_views(objects, exported, output / "回读预览", args, count=1)
        scale = max(reopened["尺寸"])
        bounds_match = all(abs(reopened[key][axis] - exported[key][axis]) <= max(scale * 1e-4, 1e-5)
                           for key in ["包围盒最小", "包围盒最大"] for axis in range(3))
        result = {"工程重开": reopened, "GLB回读": exported, "包围盒匹配": bounds_match,
                  "三角形数匹配": reopened["三角形数"] == exported["三角形数"], "阶段耗时秒": time.time() - started}
        write_json(output / "独立回读.json", result)


def main(args):
    if args.contact_sheet:
        if not args.sheet_output:
            raise ValueError("联系表需要 --sheet-output")
        make_sheet(args.contact_sheet, args.sheet_output)
        return 0
    if not args.input or not args.output:
        raise ValueError("模型检查需要 --input 和 --output")
    source = args.input.resolve()
    if not source.is_file():
        raise FileNotFoundError("输入模型不存在")
    output = allowed_output(args.output)
    if output.exists():
        raise FileExistsError("输出目录已存在，请使用新的目录")
    if args.blender:
        blender = args.blender.resolve()
    else:
        configuration = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
        blender = Path(configuration["tools"]["Blender"])
    if not blender.is_file():
        raise FileNotFoundError("Blender 程序不存在")
    output.mkdir(parents=True)
    started = time.time()
    source_hash = sha256(source)
    manifest = {"输入文件": str(source), "输入SHA256": source_hash, "开始时间戳": started,
                "说明": "仅静态模型检查；不修改输入，不减面，不规定模型体积上限", "阶段": []}
    try:
        for phase in ["prepare", "reopen"]:
            command = [str(blender), "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
                       "--", "--worker", phase, "--input", str(source), "--output", str(output),
                       "--resolution", str(args.resolution), "--samples", str(args.samples), "--views", str(args.views)]
            if args.collection:
                command.extend(["--collection", args.collection])
            tick = time.time()
            with (output / f"{phase}.log").open("w", encoding="utf-8") as log:
                process = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=args.timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            manifest["阶段"].append({"阶段": phase, "退出码": process.returncode, "耗时秒": time.time() - tick})
            if process.returncode:
                raise RuntimeError(f"Blender {phase} 阶段失败，退出码 {process.returncode}，详见日志")
        imported = json.loads((output / "导入检查.json").read_text(encoding="utf-8"))
        reopened = json.loads((output / "独立回读.json").read_text(encoding="utf-8"))
        unchanged = sha256(source) == source_hash
        missing = len(imported["缺失贴图"]) + len(reopened["工程重开"]["缺失贴图"]) + len(reopened["GLB回读"]["缺失贴图"])
        passed = unchanged and reopened["包围盒匹配"] and reopened["三角形数匹配"] and missing == 0
        manifest.update({"结构检查通过": passed, "源文件指纹一致": unchanged, "缺失贴图条目": missing,
                         "几何统计": {key: imported[key] for key in ["对象数", "网格数", "顶点数", "三角形数", "尺寸", "材质数", "贴图数"]},
                         "视觉复核": "待智能体查看整体、细节与回读预览", "耗时秒": time.time() - started,
                         "交付文件": {name: {"字节": (output / name).stat().st_size, "SHA256": sha256(output / name)} for name in ["model.blend", "model.glb"]}})
        write_json(output / "模型检查.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0 if passed else 2
    except Exception as error:
        manifest.update({"结构检查通过": False, "异常": str(error), "耗时秒": time.time() - started})
        write_json(output / "模型检查.json", manifest)
        raise


if __name__ == "__main__":
    arguments_value = arguments()
    try:
        if arguments_value.worker:
            worker(arguments_value)
        else:
            raise SystemExit(main(arguments_value))
    except Exception:
        traceback.print_exc()
        raise
