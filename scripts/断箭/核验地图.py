"""在独立Blender进程保存重开、GLB回读与高度射线核验。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def statistics():
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return {"mesh_objects": len(objects), "triangles": sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in objects),
            "materials": len(bpy.data.materials), "images": [{"name": i.name, "size": list(i.size), "packed": bool(i.packed_file), "has_data": i.has_data} for i in bpy.data.images if i.type != "RENDER_RESULT"]}


def verify(directory):
    runtime = json.loads((directory / "地图运行.json").read_text("utf-8"))
    size = runtime["size"]
    blend = directory / f"Airbase-{size}米.blend"
    glb = directory / f"Airbase-{size}米.glb"
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    original = statistics()
    build = json.loads((directory / "构建记录.json").read_text("utf-8"))
    if original["triangles"] != build["triangles"] or original["mesh_objects"] != build.get("mesh_objects", original["mesh_objects"]):
        raise ValueError("工程网格或三角形数量与清单不符")
    if any(not i["packed"] or not i["has_data"] for i in original["images"]):
        raise ValueError("工程贴图未完整打包")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    bpy.context.view_layer.update()
    imported = statistics()
    if imported["mesh_objects"] != original["mesh_objects"] or imported["triangles"] != original["triangles"]:
        raise ValueError("GLB回读对象或三角形数量变化")
    vertices, faces = [], []
    ground_bounds = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or obj.get("category") not in ["terrain", "road"]:
            continue
        base = len(vertices)
        points = [obj.matrix_world @ v.co for v in obj.data.vertices]
        vertices.extend(points)
        faces.extend([[i + base for i in p.vertices] for p in obj.data.polygons])
        ground_bounds.extend(points)
    if not vertices:
        raise ValueError("GLB未保留地表类型身份")
    span = [max(p[i] for p in ground_bounds) - min(p[i] for p in ground_bounds) for i in [0, 1]]
    if any(abs(s - size) > .05 for s in span):
        raise ValueError("地表范围偏离冻结尺寸")
    bvh = BVHTree.FromPolygons(vertices, faces, all_triangles=True)
    ceiling = max(p.z for p in ground_bounds) + 10
    ray_distance = ceiling - min(p.z for p in ground_bounds) + 10
    n = runtime["height"]["width"]
    grid = np.fromfile(directory / runtime["height"]["file"], dtype="<f4").reshape(n, n)
    if not np.isfinite(grid).all():
        raise ValueError("高度存在未知支撑面")
    rng = np.random.default_rng(26273833)
    samples = []
    for x, z in rng.uniform(-size / 2 + 1, size / 2 - 1, (100, 2)):
        px, pz = x + size / 2, z + size / 2
        ix, iz = int(px), int(pz)
        ax, az = px - ix, pz - iz
        height = (grid[iz, ix] * (1 - ax) + grid[iz, ix + 1] * ax) * (1 - az) + (grid[iz + 1, ix] * (1 - ax) + grid[iz + 1, ix + 1] * ax) * az
        location = bvh.ray_cast(Vector((x, -z, ceiling)), Vector((0, 0, -1)), ray_distance)[0]
        if location is None:
            raise ValueError("GLB回读地表存在无命中点")
        samples.append({"x": x, "z": z, "height_query": float(height), "glb_raycast": location.z, "error": abs(float(height) - location.z)})
    error = max(s["error"] for s in samples)
    save(directory / "高度核验诊断.json", {"range": [float(grid.min()), float(grid.max())], "max_error": error,
         "ground_bounds": [[min(p[i] for p in ground_bounds), max(p[i] for p in ground_bounds)] for i in range(3)], "samples": samples})
    if error > .1:
        raise ValueError(f"高度误差超限：{error}")
    # 独立回读图，不引用源工程相机或光照状态。
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 960, 100
    scene.world = bpy.data.worlds.new("回读环境")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.45, .55, .7, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = .7
    sun = bpy.data.objects.new("回读日光", bpy.data.lights.new("回读日光", "SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 2.5
    sun.rotation_euler = (.6, -.4, -.5)
    camera = bpy.data.objects.new("回读相机", bpy.data.cameras.new("回读相机"))
    scene.collection.objects.link(camera)
    camera.location = (size * .6, -size, size * .7)
    camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type, camera.data.ortho_scale = "ORTHO", size * 1.3
    camera.data.clip_end = size * 10
    scene.camera = camera
    scene.render.filepath = str(directory / "GLB独立回读.png")
    bpy.ops.render.render(write_still=True)
    result = {"status": "通过结构与高度核验，图片待实际审阅", "blend": original, "glb": imported, "ground_span_m": span,
              "height_max_error_m": error, "height_samples": samples, "unit_scale": 1,
              "glb_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(), "visual_status": "待实际审图"}
    save(directory / "独立回读核验.json", result)
    print("独立回读核验完成", json.dumps({"span": span, "samples": 100, "error": error}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    directory = args.directory.resolve()
    directory.relative_to((Path(__file__).resolve().parents[2] / "local-data/断箭/批次").resolve())
    verify(directory)
