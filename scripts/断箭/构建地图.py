"""在独立Blender后台进程构建地图工程、GLB和运行数据。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
import numpy as np


ROOT = Path(__file__).resolve().parents[2]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def hull(points):
    points = sorted(set(map(tuple, points)))
    if len(points) < 3:
        return points

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in points[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class Builder:
    def __init__(self, batch, size, revision):
        self.batch, self.size = batch, size
        self.source = batch / "地图数据" / f"{size}米"
        self.region = json.loads((batch / f"区域冻结-{size}米.json").read_text("utf-8"))
        self.output = batch / "交付" / f"{size}米" / revision
        self.output.mkdir(parents=True, exist_ok=False)
        self.materials, self.images, self.meshes = {}, {}, {}
        self.report = {"size_m": size, "revision": revision, "blender": bpy.app.version_string, "warnings": [], "files": {}, "visual_status": "待实际审图"}
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 1
        bpy.context.scene.render.engine = "CYCLES"
        self.ground_objects = []

    def material(self, key, data):
        if key in self.materials:
            return self.materials[key]
        mat = bpy.data.materials.new(data["name"])
        mat.use_nodes = True
        node = mat.node_tree.nodes.get("Principled BSDF")
        node.inputs["Roughness"].default_value = .9
        color = data.get("color", [1, 1, 1, 1])
        node.inputs["Base Color"].default_value = tuple(color)
        path = data.get("basecolor")
        if path:
            if path not in self.images:
                image = bpy.data.images.load(path, check_existing=False)
                original = list(image.size)
                limit = 4096 if self.size == 2000 and "地表底色-" in Path(path).name else 2048
                if max(original) > limit:
                    factor = limit / max(original)
                    image.scale(round(original[0] * factor), round(original[1] * factor))
                image.pack()
                self.images[path] = image
            image = self.images[path]
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            mat.node_tree.links.new(tex.outputs["Color"], node.inputs["Base Color"])
            # 底色乘数使用独立节点，避免原HDRP灯光被烘入PBR。
            if any(abs(c - 1) > 1e-5 for c in color[:3]):
                mix = mat.node_tree.nodes.new("ShaderNodeMixRGB")
                mix.blend_type = "MULTIPLY"
                mix.inputs[0].default_value = 1
                mix.inputs[2].default_value = color
                mat.node_tree.links.new(tex.outputs["Color"], mix.inputs[1])
                mat.node_tree.links.new(mix.outputs[0], node.inputs["Base Color"])
        mat["source_id"] = str(data.get("source_id", key))
        mat["conversion"] = data.get("conversion", "底色与粗糙度派生")
        self.materials[key] = mat
        return mat

    def mesh(self, name, file, mats, library):
        cache_key = str(file)
        if cache_key not in self.meshes:
            d = np.load(file)
            vertices = d["vertices"]
            faces = d["triangles"]
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            for key in mats:
                mesh.materials.append(self.material(key, library[key]))
            if len(d["uv0"]):
                uv = mesh.uv_layers.new(name="UVMap")
                uv.data.foreach_set("uv", d["uv0"][faces.ravel()].ravel())
            if len(d["material_indices"]):
                mesh.polygons.foreach_set("material_index", d["material_indices"])
            self.meshes[cache_key] = mesh
        obj = bpy.data.objects.new(name, self.meshes[cache_key])
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def export(self, path, objects):
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_yup=True,
                                  export_extras=True, export_lights=False, export_cameras=False, export_animations=False)
        self.report["files"][path.name] = {"bytes": path.stat().st_size, "sha256": sha(path)}

    def ground(self):
        scene = json.loads((self.source / "地表场景.json").read_text("utf-8"))
        for part in scene["parts"]:
            obj = self.mesh(part["name"], self.source / part["file"], part["materials"], scene["materials"])
            obj["source_mesh_id"] = str(part["mesh_id"])
            obj["category"] = part["category"]
            self.ground_objects.append(obj)
        self.export(self.output / "地表.glb", self.ground_objects)

    def buildings(self):
        candidates = json.loads((self.source / "建筑候选实例.json").read_text("utf-8"))
        prototype_index = json.loads((self.source / "原型索引.json").read_text("utf-8"))
        prototypes, runtime_instances, placed = {}, [], []
        conversion = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        origin = self.region["source_origin"]
        translation = Matrix.Translation((-origin[0], -origin[2], -origin[1]))
        for prototype in prototype_index:
            file = Path(prototype["scene"])
            scene = json.loads(file.read_text("utf-8"))
            path = scene["source_path"]
            sample = next(c for c in candidates if c["catalog"]["internal_id"] == path)
            key = sample["prefab_key"]
            worlds, objects = {}, []
            for node in scene["nodes"]:
                worlds[node["id"]] = worlds.get(node["parent"], Matrix.Identity(4)) @ Matrix(node["matrix"])
                for mesh in node["meshes"]:
                    obj = self.mesh(node["name"], file.parent / mesh["file"], mesh["materials"], scene["materials"])
                    obj.matrix_world = worlds[node["id"]]
                    objects.append(obj)
            all_points = [obj.matrix_world @ v.co for obj in objects for v in obj.data.vertices]
            # 先用完整形体判断交界，再决定是否进入首测；候选外的飞机不以白模进入交付。
            selected = []
            for instance in [c for c in candidates if c["prefab_key"] == key]:
                matrix = translation @ conversion @ Matrix(instance["source_matrix"]) @ conversion
                points = [matrix @ p for p in all_points]
                minx, maxx = min(p.x for p in points), max(p.x for p in points)
                miny, maxy = min(p.y for p in points), max(p.y for p in points)
                h = self.size / 2
                if maxx < -h or minx > h or maxy < -h or miny > h:
                    continue
                selected.append((instance, matrix, points))
            if not selected:
                for obj in objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
                continue
            outfile = key + ".glb"
            self.export(self.output / outfile, objects)
            prototypes[key] = {"file": outfile, "source_path": path, "vertices": sum(len(o.data.vertices) for o in objects),
                               "triangles": sum(len(o.data.polygons) for o in objects)}
            for instance, matrix, points in selected:
                footprint = hull([(round(p.x, 5), round(-p.y, 5)) for p in points])
                bounds = [min(p.z for p in points), max(p.z for p in points)]
                # glTF原型已转换轴向，实例的米制矩阵单独记录。
                g = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
                runtime_instances.append({"id": instance["id"], "prototype": key, "matrix": [v for row in (g @ matrix @ g.inverted()).transposed() for v in row],
                                          "footprint": footprint, "height_bounds": bounds, "cross_boundary": any(abs(v) > self.size / 2 for p in footprint for v in p),
                                          "source_bank": instance["source_bank"], "source_matrix": instance["source_matrix"]})
                for source_obj in objects:
                    obj = bpy.data.objects.new(key + "-" + instance["id"], source_obj.data)
                    bpy.context.scene.collection.objects.link(obj)
                    obj.matrix_world = matrix @ source_obj.matrix_world
                    obj["instance_id"] = instance["id"]
                    obj["category"] = "building"
                    placed.append(obj)
            for obj in objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        return prototypes, runtime_instances, placed

    def height(self):
        vertices, triangles = [], []
        for obj in self.ground_objects:
            # 以实际渲染地表和道路的最高支撑面查询，保留坡面和路肩。
            base = len(vertices)
            vertices.extend([v.co.copy() for v in obj.data.vertices])
            triangles.extend([[i + base for i in p.vertices] for p in obj.data.polygons])
        bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
        ceiling = max(v.z for v in vertices) + 10
        distance = ceiling - min(v.z for v in vertices) + 10
        n, half = self.size + 1, self.size / 2
        grid = np.full((n, n), np.nan, dtype="<f4")
        for iz in range(n):
            for ix in range(n):
                x = min(half - .0001, max(-half + .0001, ix - half))
                y = min(half - .0001, max(-half + .0001, half - iz))
                location = bvh.ray_cast(Vector((x, y, ceiling)), Vector((0, 0, -1)), distance)[0]
                if location is not None:
                    grid[iz, ix] = location.z
        grid.tofile(self.output / "高度.f32")
        self.report["height"] = {"samples": n * n, "holes": int(np.isnan(grid).sum()), "range": [float(np.nanmin(grid)), float(np.nanmax(grid))], "spacing_m": 1,
                                 "source": "来源可追溯的高细节地形和道路网格最高支撑面；无命中保持NaN"}
        if np.isnan(grid).any():
            self.report["warnings"].append("高度网格存在无支撑样点，不可验收通行")
        if np.nanmin(grid) < min(v.z for v in vertices) - .001 or np.nanmax(grid) > max(v.z for v in vertices) + .001:
            raise ValueError("射线高度超出真实源地表包围范围")
        return {"file": "高度.f32", "width": n, "height": n, "spacing": 1, "origin": [-half, -half], "encoding": "float32-little-endian", "unknown": "NaN"}

    def render(self):
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 16
        scene.cycles.use_denoising = True
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1280, 960, 100
        scene.world = bpy.data.worlds.new("地图审阅环境")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs[0].default_value = (.45, .55, .7, 1)
        scene.world.node_tree.nodes["Background"].inputs[1].default_value = .7
        light = bpy.data.lights.new("日光", "SUN")
        light.energy = 2.5
        sun = bpy.data.objects.new("日光", light)
        scene.collection.objects.link(sun)
        sun.rotation_euler = (.6, -.4, -.5)
        cam = bpy.data.objects.new("地图审阅相机", bpy.data.cameras.new("地图审阅相机"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        cam.data.type = "ORTHO"
        cam.data.clip_end = self.size * 10
        h = self.size / 2
        views = [("俯视", (0, 0, self.size * 1.6), (0, 0, 0), self.size * 1.15),
                 ("南侧", (0, -self.size, self.size * .7), (0, 0, 0), self.size * 1.2),
                 ("北侧", (0, self.size, self.size * .7), (0, 0, 0), self.size * 1.2),
                 ("东侧", (self.size, 0, self.size * .7), (0, 0, 0), self.size * 1.2),
                 ("西侧", (-self.size, 0, self.size * .7), (0, 0, 0), self.size * 1.2),
                 ("塔台接地", (-230, -220, 130), (-133.5, -100.5, 8), 180),
                 ("机库道路", (30, -100, 110), (0, 60, 2), 240)]
        for name, position, target, scale in views:
            cam.location = position
            cam.rotation_euler = (Vector(target) - cam.location).to_track_quat("-Z", "Y").to_euler()
            cam.data.ortho_scale = scale
            scene.render.filepath = str(self.output / (name + ".png"))
            bpy.ops.render.render(write_still=True)
        return cam

    def run(self):
        start = time.time()
        self.ground()
        prototypes, instances, buildings = self.buildings()
        height = self.height()
        runtime = {"id": f"airbase-{self.size}", "name": f"Airbase机场{self.size}米", "size": self.size,
                   "bounds": self.region["map_bounds"], "region": self.region, "ground": "地表.glb", "height": height,
                   "prototypes": prototypes, "instances": instances, "source_build": "26273833", "status": "candidate"}
        save(self.output / "地图运行.json", runtime)
        self.export(self.output / f"Airbase-{self.size}米.glb", self.ground_objects + buildings)
        self.report["mesh_objects"] = len(self.ground_objects + buildings)
        self.report.update(building_instances=len(instances), prototypes=len(prototypes), ground_meshes=len(self.ground_objects),
                           triangles=sum(len(o.data.polygons) for o in self.ground_objects + buildings), source_region_sha256=sha(self.batch / f"区域冻结-{self.size}米.json"))
        self.render()
        blend = self.output / f"Airbase-{self.size}米.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        self.report["files"][blend.name] = {"bytes": blend.stat().st_size, "sha256": sha(blend)}
        self.report["elapsed_s"] = time.time() - start
        save(self.output / "构建记录.json", self.report)
        print("地图构建完成", json.dumps({"output": str(self.output), "instances": len(instances), "warnings": self.report["warnings"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--size", type=int, choices=[500, 2000], default=500)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    batch = (ROOT / "local-data/断箭/批次" / args.batch).resolve()
    batch.relative_to((ROOT / "local-data/断箭/批次").resolve())
    Builder(batch, args.size, args.revision).run()
