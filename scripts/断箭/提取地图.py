"""提取冻结区域实际引用的网格、原型、材质及作者实例，不写回游戏。"""

import argparse
import json
from pathlib import Path
import re

import numpy as np
import UnityPy
from PIL import Image
from UnityPy.helpers.MeshHelper import MeshHandler

from 解析地图 import ROOT, save, digest
from 解析装配 import BundleIndex, NodeStream
from 导出场景 import Extractor, C


class MapExtractor(Extractor):
    """复用已核验的几何和LOD读取，地图材质独立转换。"""

    def __init__(self, batch, game):
        self.batch, self.game = batch, game
        self.env = UnityPy.Environment()
        self.bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
        self.files = {}
        self.texture_dir = batch / "原始贴图"
        self.texture_dir.mkdir(exist_ok=True)
        self.texture_cache, self.material_cache, self.mesh_cache = {}, {}, {}
        self.external_bundles = {}

    def load_external(self, name):
        if name in self.env.cabs:
            return
        index = json.loads((self.batch / "来源/依赖包节点定位.json").read_text("utf-8"))
        source = next(row for row in index if any(n.lower() == name.lower() for n in row["nodes"]))
        file = self.game / "BrokenArrow_Data/StreamingAssets/aa/PC" / source["bundle"]
        manifest_path = self.batch / "输入清单.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        relative = file.relative_to(self.game).as_posix()
        if not any(row["path"] == relative for row in manifest["files"]):
            manifest["files"].append({"path": relative, **digest(file)})
            save(manifest_path, manifest)
        if file.name not in self.external_bundles:
            bundle = self.external_bundles[file.name] = BundleIndex(file)
            for node in bundle.nodes:
                self.env.load_file(NodeStream(bundle, node), name=node["name"])

    def load(self, name):
        if name not in self.files:
            node = next(n for n in self.bundle.nodes if n["name"] == name)
            self.files[name] = self.env.load_file(NodeStream(self.bundle, node), name=name)
        return self.files[name]

    def deref(self, ptr, af):
        if ptr["m_FileID"]:
            name = Path(af.externals[ptr["m_FileID"] - 1].path).name
            if name in {n["name"] for n in self.bundle.nodes}:
                self.load(name)
            elif name.lower().startswith("cab-"):
                self.load_external(name)
        return UnityPy.classes.PPtr(**ptr, assetsfile=af).deref()

    def texture(self, ptr, af):
        if not ptr["m_PathID"]:
            return None
        obj = self.deref(ptr, af)
        key = re.sub(r"[^a-zA-Z0-9_-]", "_", obj.assets_file.name) + "-" + str(obj.path_id)
        if key in self.texture_cache:
            return self.texture_cache[key]
        texture = obj.read()
        stream = texture.m_StreamData
        if stream and stream.path:
            stream_name = Path(stream.path).name
            if stream_name in {n["name"] for n in self.bundle.nodes}:
                self.load(stream_name)
        path = self.texture_dir / (key + ".png")
        if not path.exists():
            texture.image.save(path)
        self.texture_cache[key] = str(path)
        return str(path)

    def material(self, ptr, af, warnings):
        obj = self.deref(ptr, af)
        key = re.sub(r"[^a-zA-Z0-9_-]", "_", obj.assets_file.name) + "-" + str(obj.path_id)
        if key in self.material_cache:
            return key, self.material_cache[key]
        data = obj.read_typetree()
        tex = dict(data["m_SavedProperties"]["m_TexEnvs"])
        colors = dict(data["m_SavedProperties"]["m_Colors"])
        slot = next((p for p in ["_BaseColorMap", "_Albedo", "_MainTex", "Layer_A97CDC25"] if p in tex and tex[p]["m_Texture"]["m_PathID"]), None)
        entry = {"name": data["m_Name"], "raw": data, "source_node": obj.assets_file.name, "source_id": obj.path_id,
                 "color": list(colors.get("_BaseColor", dict(zip("rgba", [1, 1, 1, 1]))).values()),
                 "conversion": "底色贴图与作者UV；固定粗糙度。未复刻地表混合、破损、天气和原游戏光照。", "textures": {}, "derived": {}}
        if slot:
            entry["textures"][slot] = {"file": self.texture(tex[slot]["m_Texture"], obj.assets_file), "scale": tex[slot]["m_Scale"], "offset": tex[slot]["m_Offset"]}
            entry["basecolor"] = entry["textures"][slot]["file"]
            if slot == "_Albedo":
                entry["color"] = [1, 1, 1, 1]
        self.material_cache[key] = entry
        return key, entry

    def geometry(self, ptr, af):
        obj = self.deref(ptr, af)
        mesh = obj.read()
        if mesh.m_StreamData and mesh.m_StreamData.path:
            self.load(Path(mesh.m_StreamData.path).name)
        handler = MeshHandler(mesh)
        handler.process()
        vertices = np.asarray(handler.m_Vertices, dtype=np.float32)[:, :3]
        uv = np.asarray(handler.m_UV0, dtype=np.float32)[:, :2] if handler.m_UV0 else np.zeros((len(vertices), 2), dtype=np.float32)
        triangles, materials = [], []
        for i, faces in enumerate(handler.get_triangles()):
            faces = np.asarray(faces, dtype=np.int32)
            faces += getattr(mesh.m_SubMeshes[i], "baseVertex", 0) or 0
            triangles.extend(faces)
            materials.extend([i] * len(faces))
        return obj, vertices, uv, np.asarray(triangles, dtype=np.int32), np.asarray(materials, dtype=np.int32)


def clipped(vertices, uv, triangles, material_indices, bounds):
    """按XZ方区精确裁剪三角形，同步线性插值UV；不裁剪独立建筑。"""
    out_v, out_uv, out_t, out_m = [], [], [], []
    tri_v = vertices[triangles]
    keep = ((tri_v[:, :, 0].max(1) >= bounds[0]) & (tri_v[:, :, 0].min(1) <= bounds[1]) &
            (tri_v[:, :, 2].max(1) >= bounds[2]) & (tri_v[:, :, 2].min(1) <= bounds[3]))
    for face, mi in zip(triangles[keep], material_indices[keep]):
        polygon = [np.concatenate([vertices[i], uv[i]]) for i in face]
        for axis, value, sign in [(0, bounds[0], 1), (0, bounds[1], -1), (2, bounds[2], 1), (2, bounds[3], -1)]:
            output = []
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                ia, ib = sign * (a[axis] - value) >= -1e-9, sign * (b[axis] - value) >= -1e-9
                if ia:
                    output.append(a)
                if ia != ib:
                    output.append(a + (b - a) * ((value - a[axis]) / (b[axis] - a[axis])))
            polygon = output
            if len(polygon) < 3:
                break
        if len(polygon) < 3:
            continue
        base = len(out_v)
        for v in polygon:
            out_v.append(v[:3])
            out_uv.append(v[3:])
        for i in range(1, len(polygon) - 1):
            out_t.append([base, base + i, base + i + 1])
            out_m.append(mi)
    return np.asarray(out_v, dtype=np.float32), np.asarray(out_uv, dtype=np.float32), np.asarray(out_t, dtype=np.int32), np.asarray(out_m, dtype=np.int32)


def bake_terrain(worker, row, vertices, uv, triangles, bounds, target, size, dimension=None, controller_path=None, layers_directory=None, fallbacks=None, preserve_source_holes=False):
    """以真实权重和纹理层派生区域底色，保留作者UV频率；不复刻视距噪声和高度混合。"""
    controller = json.loads((controller_path or worker.batch / "来源/地表控制器字段.json").read_text("utf-8"))["data"]
    renderer_id = next(c["id"] for c in row["components"] if c["type"] == "MeshRenderer")
    container = next(c for c in controller["SplatController"]["Containers"] if c["Renderer"]["m_PathID"] == renderer_id)
    # 作者UV有半精度量化误差。记录仿射拟合偏差，烘焙使用真实三角形插值。
    design = np.column_stack([vertices[:, 0], vertices[:, 2], np.ones(len(vertices))])
    mapping, _, _, _ = np.linalg.lstsq(design, uv, rcond=None)
    error = float(np.max(np.abs(design @ mapping - uv)))
    dimension = dimension or (2048 if size == 500 else 4096)
    x = np.linspace(bounds[0], bounds[1], dimension, endpoint=False) + size / dimension / 2
    z = np.linspace(bounds[3], bounds[2], dimension, endpoint=False) - size / dimension / 2
    raster = np.full((dimension, dimension, 2), np.nan, dtype=np.float32)
    pixels = np.column_stack([(vertices[:, 0] - bounds[0]) / size * dimension - .5,
                              (bounds[3] - vertices[:, 2]) / size * dimension - .5])
    for face in triangles:
        a, b, c = pixels[face]
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) < 1e-8:
            continue
        low = np.maximum(0, np.ceil(pixels[face].min(0)).astype(int))
        high = np.minimum(dimension - 1, np.floor(pixels[face].max(0)).astype(int))
        xx, yy = np.meshgrid(np.arange(low[0], high[0] + 1), np.arange(low[1], high[1] + 1))
        wb = ((xx - a[0]) * (c[1] - a[1]) - (yy - a[1]) * (c[0] - a[0])) / area
        wc = ((b[0] - a[0]) * (yy - a[1]) - (b[1] - a[1]) * (xx - a[0])) / area
        wa = 1 - wb - wc
        keep = (wa >= -1e-6) & (wb >= -1e-6) & (wc >= -1e-6)
        raster[yy[keep], xx[keep]] = wa[keep, None] * uv[face[0]] + wb[keep, None] * uv[face[1]] + wc[keep, None] * uv[face[2]]
    fallback_points = []
    source_holes = []
    if not np.isfinite(raster).all():
        # 保存真实未覆盖像素，供独立几何射线区分源空洞和烘焙器问题。
        holes = ~np.isfinite(raster).all(axis=2)
        np.savez_compressed(target / f"地表覆盖异常-{row['id']}.npz", holes=holes,
                            bounds=np.asarray(bounds), resolution=np.asarray(dimension))
        # 实际分区边缘并非理想直线。只接受其他真实地形三角形的独立支撑，分别用其原UV和图层。
        for iy, ix in np.argwhere(holes):
            px, pz = x[ix], z[iy]
            best = None
            for other in fallbacks or []:
                face = other['vertices'][other['triangles']]
                a, b, c = face[:, 0], face[:, 1], face[:, 2]
                area = (b[:, 0] - a[:, 0]) * (c[:, 2] - a[:, 2]) - (b[:, 2] - a[:, 2]) * (c[:, 0] - a[:, 0])
                valid = np.abs(area) > 1e-12
                denominator = np.where(valid, area, 1)
                wb = ((px - a[:, 0]) * (c[:, 2] - a[:, 2]) - (pz - a[:, 2]) * (c[:, 0] - a[:, 0])) / denominator
                wc = ((b[:, 0] - a[:, 0]) * (pz - a[:, 2]) - (b[:, 2] - a[:, 2]) * (px - a[:, 0])) / denominator
                wa = 1 - wb - wc
                candidates = np.flatnonzero(valid & (wa >= -1e-10) & (wb >= -1e-10) & (wc >= -1e-10))
                for ti in candidates:
                    weights = np.array([wa[ti], wb[ti], wc[ti]])
                    height = float(weights @ face[ti, :, 1])
                    if best is None or height > best['height']:
                        best = {'pixel': [int(iy), int(ix)], 'xz': [float(px), float(pz)], 'height': height,
                                'renderer_id': other['renderer_id'], 'source_key': other['source_key'],
                                'source_triangle': int(ti), 'barycentric': weights.tolist(),
                                'uv': (weights @ other['uv'][other['triangles'][ti]]).tolist()}
            if best is None:
                if preserve_source_holes:
                    source_holes.append([int(iy), int(ix)])
                    continue
                raise ValueError(f"地表像素没有任何真实高细节三角形支撑：{px},{pz}；禁止仿射填补")
            fallback_points.append(best)
        # 此临时值只用于完成主材质数组运算，输出前逐点写入上方真实来源的颜色。
        raster[holes] = 0
    weights = []
    for key in ["Splat0", "Splat1", "Splat2"]:
        path = worker.texture(container[key], worker.files["level7"])
        weights.append(np.array(Image.open(path).convert("RGBA"), dtype=np.float32) / 255)

    def sample(array, u, v):
        h, w = array.shape[:2]
        px, py = np.mod(u, 1) * w - .5, (1 - np.mod(v, 1)) * h - .5
        ix, iy = np.floor(px).astype(int), np.floor(py).astype(int)
        ax, ay = (px - ix)[..., None], (py - iy)[..., None]
        return ((array[iy % h, ix % w] * (1 - ax) + array[iy % h, (ix + 1) % w] * ax) * (1 - ay) +
                (array[(iy + 1) % h, ix % w] * (1 - ax) + array[(iy + 1) % h, (ix + 1) % w] * ax) * ay)

    layers_directory = layers_directory or worker.batch / '原始贴图/地表数组'
    layers = [np.array(Image.open(layers_directory / f"底色-{i}.png").convert("RGB"), dtype=np.float32) / 255 for i in range(12)]
    layers = [np.where(a <= .04045, a / 12.92, ((a + .055) / 1.055) ** 2.4) for a in layers]
    result = np.empty((dimension, dimension, 3), dtype=np.uint8)
    ranges = np.zeros((12, 2), dtype=float)
    ranges[:, 0] = 1
    for start in range(0, dimension, 32):
        xx, zz = np.meshgrid(x, z[start:start + 32])
        coords = raster[start:start + 32]
        u, v = coords[..., 0], coords[..., 1]
        weight = np.concatenate([sample(a, u, v) for a in weights], axis=-1)
        weight /= np.maximum(weight.sum(axis=-1, keepdims=True), 1e-6)
        color = np.zeros((*xx.shape, 3), dtype=float)
        for layer, image in enumerate(layers):
            ranges[layer, 0] = min(ranges[layer, 0], float(weight[..., layer].min()))
            ranges[layer, 1] = max(ranges[layer, 1], float(weight[..., layer].max()))
            props = controller[f"_layer{layer // 4}{'rgba'[layer % 4]}"]
            angle = np.deg2rad(props["Angle"])
            su = u * props["Tile"][0] * container["BigFactor"]
            sv = v * props["Tile"][1] * container["BigFactor"]
            ru, rv = np.cos(angle) * su - np.sin(angle) * sv, np.sin(angle) * su + np.cos(angle) * sv
            color += sample(image, ru, rv) * props["Light"][:3] * weight[..., layer, None]
        color *= controller["LightColor"][:3]
        rgb = np.where(color <= .0031308, color * 12.92, 1.055 * np.maximum(color, 0) ** (1 / 2.4) - .055)
        result[start:start + len(zz)] = np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    for point in fallback_points:
        container_other = next(c for c in controller['SplatController']['Containers'] if c['Renderer']['m_PathID'] == point['renderer_id'])
        other_weights = [np.asarray(Image.open(worker.texture(container_other[key], worker.files['level7'])).convert('RGBA'), dtype=np.float32) / 255
                         for key in ['Splat0', 'Splat1', 'Splat2']]
        u, v = np.array([point['uv'][0]]), np.array([point['uv'][1]])
        weight = np.concatenate([sample(a, u, v) for a in other_weights], axis=-1)
        weight /= np.maximum(weight.sum(axis=-1, keepdims=True), 1e-6)
        color = np.zeros((1, 3))
        for layer, image in enumerate(layers):
            props = controller[f"_layer{layer // 4}{'rgba'[layer % 4]}"]
            angle = np.deg2rad(props['Angle'])
            su, sv = u * props['Tile'][0] * container_other['BigFactor'], v * props['Tile'][1] * container_other['BigFactor']
            ru, rv = np.cos(angle) * su - np.sin(angle) * sv, np.sin(angle) * su + np.cos(angle) * sv
            color += sample(image, ru, rv) * props['Light'][:3] * weight[..., layer, None]
        color *= controller['LightColor'][:3]
        rgb = np.where(color <= .0031308, color * 12.92, 1.055 * np.maximum(color, 0) ** (1 / 2.4) - .055)
        result[tuple(point['pixel'])] = np.round(np.clip(rgb[0], 0, 1) * 255).astype(np.uint8)
    if source_holes:
        coverage = np.ones((dimension, dimension), dtype=bool)
        yy, xx = np.asarray(source_holes).T
        coverage[yy, xx] = False
        np.savez_compressed(target / f"高细节覆盖-{row['id']}.npz", covered=coverage, bounds=np.asarray(bounds))
        # 只延展边缘RGB以免实际三角形边界采到黑色；无效掩码不改变，不生成几何或高度。
        valid = coverage.copy()
        for _ in range(2):
            pending = np.argwhere(~valid)
            updates = []
            for py, px in pending:
                for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    sy, sx = py + dy, px + dx
                    if 0 <= sy < dimension and 0 <= sx < dimension and valid[sy, sx]:
                        updates.append((py, px, result[sy, sx].copy()))
                        break
            for py, px, rgb in updates:
                result[py, px] = rgb
                valid[py, px] = True
        alpha = coverage.astype(np.uint8) * 255
        result = np.dstack([result, alpha])
    path = target / f"地表底色-{row['id']}.png"
    Image.fromarray(result).save(path)
    save(target / f"地表混合-{row['id']}.json", {"source_controller": "level7:961", "source_renderer": renderer_id, "container": container,
         "uv_affine": mapping.tolist(), "uv_fit_max_error": error, "layer_weight_ranges": ranges.tolist(), "resolution": dimension,
         "source_coverage_fallbacks": fallback_points,
         "source_uncovered_pixels": source_holes, "uncovered_policy": "源高细节几何没有覆盖时保留无效掩码；RGB仅作两像素边缘延展，不填高度或生成地面",
         "policy": "真实12层颜色数组、真实3张RGBA权重、作者频率角度和颜色；线性底色混合。未复刻视距微纹理、Perlin噪声、破损、天气与高度混合。"})
    return str(path)


def extract(batch, game, size):
    region_path = batch / f"区域冻结-{size}米.json"
    region = json.loads(region_path.read_text("utf-8"))
    if region["size_m"] != size or region["status"] != "frozen":
        raise ValueError("必须先冻结实际区域")
    target = batch / "地图数据" / f"{size}米"
    target.mkdir(parents=True, exist_ok=True)
    worker = MapExtractor(batch, game)
    try:
        level, assets = worker.load("level7"), worker.load("sharedassets7.assets")
        rows = json.loads((batch / "来源/Airbase-场景结构.json").read_text("utf-8"))
        center = np.array(region["source_origin"], dtype=float)
        bounds = [center[0] - size / 2, center[0] + size / 2, center[2] - size / 2, center[2] + size / 2]
        parts, materials, warnings = [], {}, []
        # 使用实际高细节地形几何与其对应的源烘焙底色，保留二者身份。
        for row in rows:
            name = row["name"]
            category = "terrain" if name.startswith("tile_mesh_high_") else "road" if name.startswith(("road_geo_", "road_ellement_", "airport_")) else None
            if not category:
                continue
            mf = next((c for c in row["components"] if c["type"] == "MeshFilter"), None)
            mr = next((c for c in row["components"] if c["type"] == "MeshRenderer"), None)
            if not mf or not mr or not row["active"] or not mr["data"]["m_Enabled"]:
                continue
            obj, v, uv, tri, mi = worker.geometry(mf["data"]["m_Mesh"], level)
            world = np.array(row["world_matrix"])
            v = v @ world[:3, :3].T + world[:3, 3]
            v, uv, tri, mi = clipped(v, uv, tri, mi, bounds)
            if not len(tri):
                continue
            matptrs = mr["data"]["m_Materials"]
            if category == "terrain":
                tile = int(re.search(r"high_(\d+)", name).group(1))
                matptrs = [{"m_FileID": 2, "m_PathID": 84 + tile}]
            matkeys = []
            for ptr in matptrs:
                key, material = worker.material(ptr, level, warnings)
                materials[key] = material
                matkeys.append(key)
            if category == "terrain":
                color = bake_terrain(worker, row, v, uv, tri, bounds, target, size)
                materials[matkeys[0]] = {**materials[matkeys[0]], "basecolor": color, "color": [1, 1, 1, 1], "conversion": "真实地表权重与12层底色区域混合；详见地表混合记录"}
                uv = np.column_stack([(v[:, 0] - bounds[0]) / size, (v[:, 2] - bounds[2]) / size]).astype(np.float32)
            file = f"{category}-{row['id']}.npz"
            # Blender坐标为x,z,y；其到glTF的标准转换得到x,y,-z。
            np.savez_compressed(target / file, vertices=((v - center) @ C[:3, :3]).astype(np.float32), uv0=uv,
                                triangles=tri[:, [0, 2, 1]], material_indices=mi)
            parts.append({"name": name, "category": category, "file": file, "gameobject": row["id"], "mesh_node": obj.assets_file.name,
                          "mesh_id": obj.path_id, "world_matrix": row["world_matrix"], "materials": matkeys, "triangles": len(tri),
                          "ground_color_source": "原始权重与12层底色区域混合" if category == "terrain" else None})
        save(target / "地表场景.json", {"parts": parts, "materials": materials, "warnings": warnings})
        # 本步骤先保存选区真实实例，原型按正式catalog路径精确匹配。
        bank_data = json.loads((batch / "来源/Airbase-原始放置.json").read_text("utf-8"))["banks"]
        instances = []
        for bank in bank_data:
            if not bank["enabled"] or "/buildings/" not in bank["resource_path"]:
                continue
            for index, (trs, matrix) in enumerate(zip(bank["trs"], bank["matrices"])):
                if bounds[0] - 100 <= trs[0] <= bounds[1] + 100 and bounds[2] - 100 <= trs[2] <= bounds[3] + 100:
                    instances.append({"id": f"{bank['source_id']}:{index}", "prefab_key": bank["prefab_key"], "catalog": bank["catalog"], "source_matrix": matrix,
                                      "source_bank": bank["resource_path"], "core_origin": bool(bounds[0] <= trs[0] <= bounds[1] and bounds[2] <= trs[2] <= bounds[3])})
        save(target / "建筑候选实例.json", instances)
        bundle = next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("mapresources_assets_all_*.bundle"))
        # 主包ResourceManager会按需追加外部文件，不能与Prefab容器索引混用。
        worker.env = UnityPy.load(str(bundle))
        # 现有几何导出器不使用主包文件流，先显式加载原型依赖，不启动游戏。
        prototypes = []
        for path in sorted({i["catalog"]["internal_id"] for i in instances}):
            prototypes.append(worker.export(path, "map", retry_incomplete=True))
        save(target / "原型索引.json", prototypes)
        return {"size": size, "parts": len(parts), "triangles": sum(p["triangles"] for p in parts), "candidate_instances": len(instances), "prototypes": len(prototypes), "warnings": warnings}
    finally:
        worker.bundle.file.close()
        for bundle in worker.external_bundles.values():
            bundle.file.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", required=True)
    p.add_argument("--size", type=int, choices=[500, 2000], default=500)
    args = p.parse_args()
    batch = (ROOT / "local-data/断箭/批次" / args.batch).resolve()
    batch.relative_to((ROOT / "local-data/断箭/批次").resolve())
    game = Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text("utf-8"))["game_directory"])
    print(json.dumps(extract(batch, game, args.size), ensure_ascii=False))


if __name__ == "__main__":
    main()
