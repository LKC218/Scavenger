"""按需只读主数据包，保存地图对象及来源证据。使用项目隔离 Python 运行。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import time

import numpy as np
import UnityPy

from 解析装配 import BundleIndex, NodeStream, transform_matrix, read_catalog_locations


ROOT = Path(__file__).resolve().parents[2]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            sha.update(chunk)
    stat = path.stat()
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha.hexdigest()}


def bootstrap(game, batch):
    batch.mkdir(parents=True, exist_ok=False)
    manifest = game.parents[1] / "appmanifest_1604270.acf"
    text = manifest.read_text(encoding="utf-8")
    build = re.search(r'"buildid"\s+"(\d+)"', text).group(1)
    files = [
        "BrokenArrow_Data/data.unity3d",
        "BrokenArrow_Data/StreamingAssets/aa/catalog.bin",
        "BrokenArrow_Data/StreamingAssets/aa/PC/mapsettings_assets_mapsettingsairbase_f28604c0f1829a35d50f99e2b6dcdef2.bundle",
        "BrokenArrow_Data/StreamingAssets/aa/PC/mapresources_assets_all_19b860e9a522465bce083978b7044755.bundle",
        "BrokenArrow_Data/StreamingAssets/Navigation/Airbase.ng",
        "BrokenArrow_Data/StreamingAssets/Navigation/Airbase_roads.ng",
        "BrokenArrow_Data/StreamingAssets/SharedMissions/Airbase_network/meta.json",
        "BrokenArrow_Data/StreamingAssets/SharedMissions/Airbase_network/Airbase_network.bascr",
        "BrokenArrow_Data/StreamingAssets/SharedMissions/Airbase_Skirmish/meta.json",
        "BrokenArrow_Data/StreamingAssets/SharedMissions/Airbase_Skirmish/Airbase_Skirmish.bascr",
    ]
    result = {"created_utc": datetime.now(timezone.utc).isoformat(), "purpose": "Airbase地图500米首测", "build": build,
              "game_directory": str(game), "steam_manifest": {"path": str(manifest), **digest(manifest)},
              "source_open_mode": "rb", "files": []}
    for relative in files:
        source = game / relative
        result["files"].append({"path": relative, **digest(source)})
        save(batch / "输入清单.json", result)
    save(batch / "执行状态.json", {"phase": "G0来源定位", "gates": {"G0": "进行中", "G1": "未开始", "G2": "未开始", "G3": "未开始"}})
    return {"batch": str(batch), "build": build, "frozen_files": len(result["files"])}


def inspect(game, batch, node_name, ids, types):
    bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
    try:
        if not node_name:
            result = {"engine": bundle.engine, "nodes": bundle.nodes, "blocks": len(bundle.blocks)}
            save(batch / "来源" / "主包节点.json", result)
            return result
        node = next(item for item in bundle.nodes if item["name"] == node_name)
        env = UnityPy.Environment()
        af = env.load_file(NodeStream(bundle, node), name=node_name)
        objects, trees = [], {}
        for obj in af.objects.values():
            name = obj.type.name
            row = {"id": obj.path_id, "type": name, "bytes": obj.byte_size}
            if name in {"GameObject", "MonoScript", "Material", "Mesh", "Texture2D", "TerrainData", "MonoBehaviour"}:
                try:
                    row["name"] = obj.peek_name()
                except Exception as error:
                    row["name_error"] = str(error)
            objects.append(row)
            if obj.path_id in ids or name in types:
                try:
                    trees[str(obj.path_id)] = {"type": name, "tree": obj.read_typetree()}
                except Exception as error:
                    trees[str(obj.path_id)] = {"type": name, "error": str(error)}
        external = [{"path": entry.path, "guid": str(entry.guid), "type": entry.type} for entry in af.externals]
        result = {"node": node, "engine": bundle.engine, "counts": dict(Counter(row["type"] for row in objects)), "externals": external, "objects": objects}
        save(batch / "来源" / f"{node_name}-索引.json", result)
        if trees:
            save(batch / "来源" / f"{node_name}-字段.json", trees)
        return {"node": node_name, "counts": result["counts"], "externals": external, "selected_ids": list(trees), "blocks_read": bundle.blocks_read}
    finally:
        bundle.file.close()


def sources(game, batch):
    """恢复正式路径关联、作者变换和高度场，保留原始对象身份。"""
    bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
    env, files = UnityPy.Environment(), {}
    try:
        for name in ["globalgamemanagers", "globalgamemanagers.assets", "resources.assets", "level7", "sharedassets7.assets"]:
            node = next(row for row in bundle.nodes if row["name"] == name)
            files[name] = env.load_file(NodeStream(bundle, node), name=name)
        settings = next(o for o in files["globalgamemanagers"].objects.values() if o.type.name == "BuildSettings").read_typetree()
        settings = {k: v for k, v in settings.items() if not any(t in k.lower() for t in ("token", "password", "secret"))}
        if settings["scenes"][7] != "Assets/Scenes/Airbase.unity":
            raise ValueError("Airbase关卡编号发生变化，停止使用旧编号")
        rm = next(o for o in files["globalgamemanagers"].objects.values() if o.type.name == "ResourceManager").read_typetree()
        catalog = read_catalog_locations(game / "BrokenArrow_Data/StreamingAssets/aa/catalog.bin")
        catalog_keys = {row["primary_key"]: row for row in catalog["locations"]}
        banks = []
        for key, ptr in rm["m_Container"]:
            if not key.startswith("scenecompressor/airbase/"):
                continue
            obj = UnityPy.classes.PPtr(**ptr, assetsfile=files["globalgamemanagers"]).deref()
            obj.reset()
            header = obj.read(check_read=False)
            if header.m_Script.deref().read_typetree()["m_ClassName"] != "SceneCompressorBank":
                raise ValueError("放置表脚本身份不符")
            raw = obj.get_raw_data()
            pos = (32 + struct.unpack_from("<I", raw, 28)[0] + 3) // 4 * 4
            length = struct.unpack_from("<I", raw, pos)[0]
            prefab = raw[pos + 4:pos + 4 + length].decode("utf-8")
            pos = (pos + 4 + length + 3) // 4 * 4
            count = struct.unpack_from("<I", raw, pos)[0]
            pos += 4
            if pos + count * 40 + 4 != len(raw):
                raise ValueError(f"放置表范围与已核对的10浮点字段布局不一致：{key}")
            values = np.frombuffer(raw, dtype="<f4", count=count * 10, offset=pos).reshape(-1, 10)
            if not np.isfinite(values).all() or not np.allclose(np.linalg.norm(values[:, 3:7], axis=1), 1, atol=.001):
                raise ValueError("放置表存在无效坐标或非单位四元数")
            enabled = struct.unpack_from("<I", raw, len(raw) - 4)[0]
            if enabled not in (0, 1):
                raise ValueError("放置表结尾不是对齐布尔值")
            matrices = []
            for value in values:
                matrices.append(transform_matrix({"m_LocalPosition": dict(zip("xyz", map(float, value[:3]))), "m_LocalRotation": dict(zip("xyzw", map(float, value[3:7]))), "m_LocalScale": dict(zip("xyz", map(float, value[7:])))}).tolist())
            banks.append({"resource_path": key, "source_node": obj.assets_file.name, "source_id": obj.path_id,
                          "raw_sha256": hashlib.sha256(raw).hexdigest(), "prefab_key": prefab,
                          "catalog": catalog_keys.get(prefab), "enabled": bool(enabled), "count": count,
                          "trs": values.tolist(), "matrices": matrices})
        save(batch / "来源/Airbase-原始放置.json", {"format_evidence": "地图压缩器类型定义.json", "banks": banks})
        level = files["level7"]
        transforms = {o.path_id: o.read_typetree() for o in level.objects.values() if o.type.name == "Transform"}
        worlds = {}

        def world(tid):
            if tid not in worlds:
                tr = transforms[tid]
                parent = tr["m_Father"]["m_PathID"]
                worlds[tid] = (world(parent) if parent else np.eye(4)) @ transform_matrix(tr)
            return worlds[tid]

        rows, terrain = [], []
        for obj in level.objects.values():
            if obj.type.name != "GameObject":
                continue
            data = obj.read_typetree()
            components = []
            for component in data["m_Component"]:
                co = level.objects[component["component"]["m_PathID"]]
                if co.type.name == "MonoBehaviour":
                    co.reset()
                    mh = co.read(check_read=False)
                    body = {"script": mh.m_Script.deref().read_typetree()["m_ClassName"] if mh.m_Script.path_id else None, "bytes": co.byte_size}
                else:
                    body = co.read_typetree()
                components.append({"id": co.path_id, "type": co.type.name, "data": body})
            tid = next(c["id"] for c in components if c["type"] == "Transform")
            row = {"id": obj.path_id, "name": data["m_Name"], "active": data["m_IsActive"], "world_matrix": world(tid).tolist(), "components": components}
            rows.append(row)
            for c in components:
                if c["type"] != "Terrain":
                    continue
                td = UnityPy.classes.PPtr(**c["data"]["m_TerrainData"], assetsfile=level).deref()
                ht = td.read_typetree()["m_Heightmap"]
                height = np.array(ht["m_Heights"], dtype="<i2").reshape(ht["m_Resolution"], -1)
                target = batch / "来源" / f"TerrainData-{td.path_id}.npy"
                np.save(target, height, allow_pickle=False)
                terrain.append({"gameobject": obj.path_id, "name": row["name"], "terrain_id": c["id"], "data_node": td.assets_file.name, "data_id": td.path_id,
                                "enabled": c["data"]["m_Enabled"], "draw_heightmap": c["data"]["m_DrawHeightmap"], "world_matrix": row["world_matrix"], "resolution": ht["m_Resolution"], "scale": ht["m_Scale"], "raw_range": [int(height.min()), int(height.max())], "holes": len(ht["m_Holes"]), "file": target.name})
        save(batch / "来源/Airbase-场景结构.json", rows)
        save(batch / "来源/Airbase-地形索引.json", terrain)
        settings_bundle = next((game / "BrokenArrow_Data/StreamingAssets/aa/PC").glob("mapsettings_assets_mapsettingsairbase_*.bundle"))
        se = UnityPy.load(str(settings_bundle))
        st = next(o for o in se.objects if o.type.name == "MonoBehaviour").read_typetree()
        save(batch / "来源/Airbase-地图设置.json", st)
        for field in ["MinimapImageColored", "MinimapImageGrey", "BakeTexture"]:
            value = st[field]
            co = next(o for o in se.objects if o.path_id == value["m_PathID"])
            texture = co.read()
            try:
                texture.image.save(batch / "来源" / f"{field}.png")
            except ValueError as error:
                # 部分高度或编码纹理不是Pillow可直接显示的颜色，保留原字节与格式。
                (batch / "来源" / f"{field}.bin").write_bytes(texture.image_data)
                save(batch / "来源" / f"{field}-格式.json", {"id": co.path_id, "width": texture.m_Width, "height": texture.m_Height, "format": int(texture.m_TextureFormat), "preview_error": str(error)})
        return {"scene": settings["scenes"][7], "banks": len(banks), "building_instances": sum(b["count"] for b in banks if "/buildings/" in b["resource_path"]), "terrain": terrain}
    finally:
        bundle.file.close()


def type_layouts(game, batch):
    """只读取IL2CPP元数据，不运行游戏。递归记录地图控制器引用的数据结构。"""
    from 核对音效调用 import load_native

    native = load_native(game / "GameAssembly.dll", game / "BrokenArrow_Data/il2cpp_data/Metadata/global-metadata.dat")
    lookup = {str(t.FullName): t for t in native.TheMetadata.typeDefs}
    pending = [n for n in lookup if n.endswith("BloodTerrainController")]
    pending.extend(["SceneCompressor.SceneCompressorBank", "SceneCompressor.CompressedItemTransform"])
    rows, visited = [], set()
    while pending:
        name = pending.pop()
        if name in visited or name not in lookup:
            continue
        visited.add(name)
        t = lookup[name]
        fields = [{"name": str(f.Name), "type": str(f.FieldType), "attrs": int(f.RawFieldType.Attrs)} for f in t.Fields]
        rows.append({"type": name, "base": str(t.BaseType), "fields": fields,
                     "methods": [{"signature": str(m.HumanReadableSignature), "pointer": int(m.MethodPointer)} for m in t.Methods]})
        for f in fields:
            target = f["type"].removesuffix("[]")
            target = target.removeprefix("System.Collections.Generic.List`1<").removesuffix(">")
            if not target.startswith(("System.", "UnityEngine.", "BrokenArrow.")) and target in lookup:
                pending.append(target)
    save(batch / "来源/地图控制器类型定义.json", rows)
    return {"types": [r["type"] for r in rows]}


def terrain_material(game, batch):
    """按当前构建字段定义读取地表控制器；未知字段或结尾余量直接失败。"""
    from UnityPy.export.Texture2DConverter import parse_image_data
    from UnityPy.enums import TextureFormat, BuildTarget

    layouts = {row["type"]: row for row in json.loads((batch / "来源/地图控制器类型定义.json").read_text("utf-8"))}
    bundle = BundleIndex(game / "BrokenArrow_Data/data.unity3d")
    env, files = UnityPy.Environment(), {}
    try:
        for name in ["level7", "sharedassets7.assets", "sharedassets7.assets.resS"]:
            files[name] = env.load_file(NodeStream(bundle, next(n for n in bundle.nodes if n["name"] == name)), name=name)
        obj = files["level7"].objects[961]
        raw = obj.get_raw_data()
        pos = (32 + struct.unpack_from("<I", raw, 28)[0] + 3) // 4 * 4

        def unpack(fmt):
            nonlocal pos
            values = struct.unpack_from("<" + fmt, raw, pos)
            pos += struct.calcsize("<" + fmt)
            return values[0] if len(values) == 1 else list(values)

        def read(kind):
            nonlocal pos
            if kind == "System.Boolean":
                result = bool(unpack("B"))
                pos = (pos + 3) // 4 * 4
                return result
            if kind in ["System.Single", "System.Int32"]:
                return unpack("f" if kind == "System.Single" else "i")
            if kind in ["UnityEngine.Vector2", "UnityEngine.Vector3", "UnityEngine.Vector4", "UnityEngine.Color"]:
                return unpack("f" * {"UnityEngine.Vector2": 2, "UnityEngine.Vector3": 3, "UnityEngine.Vector4": 4, "UnityEngine.Color": 4}[kind])
            if kind.startswith("System.Collections.Generic.List`1<") or kind.endswith("[]"):
                element = kind.removeprefix("System.Collections.Generic.List`1<").removesuffix(">").removesuffix("[]")
                count = unpack("i")
                if count < 0 or count > 10000:
                    raise ValueError("地表控制器数组长度不合法")
                return [read(element) for _ in range(count)]
            if kind in layouts:
                # 本控制器及值结构只有public序列化字段；不把静态、运行时私有或NonSerialized项混入。
                return {f["name"]: read(f["type"]) for f in layouts[kind]["fields"] if f["attrs"] & 7 == 6 and not f["attrs"] & (16 | 128)}
            if kind.startswith("UnityEngine."):
                file_id, path_id = unpack("iq")
                return {"m_FileID": file_id, "m_PathID": path_id}
            raise ValueError("尚未核对的序列化类型：" + kind)

        controller = read("Shaders.BloodTerrain.BloodTerrainController")
        if pos != len(raw):
            raise ValueError(f"地表控制器字段未完整匹配：{pos}/{len(raw)}")
        save(batch / "来源/地表控制器字段.json", {"node": "level7", "id": 961, "bytes": len(raw), "consumed": pos, "data": controller})
        target = batch / "原始贴图/地表数组"
        target.mkdir(parents=True, exist_ok=True)
        array = files["sharedassets7.assets"].objects[831].read_typetree()
        raw_data = bytes(array["image data"])
        stride = len(raw_data) // array["m_Depth"]
        # 当前GraphicsFormat 96为RGBA_DXT1_SRGB，按每层完整Mip链分割，并检查理论长度。
        if array["m_Format"] != 96:
            raise ValueError("地表颜色数组GraphicsFormat发生变化")
        mip_size = sum(max(1, (array["m_Width"] >> mip) // 4) * max(1, (array["m_Height"] >> mip) // 4) * 8 for mip in range(array["m_MipCount"]))
        if stride != mip_size or stride * array["m_Depth"] != len(raw_data):
            raise ValueError("地表数组层与Mip长度不符")
        for layer in range(array["m_Depth"]):
            image = parse_image_data(raw_data[layer * stride:(layer + 1) * stride], array["m_Width"], array["m_Height"], TextureFormat.DXT1, (6000, 3, 24, 1), BuildTarget.StandaloneWindows64)
            image.save(target / f"底色-{layer}.png")
        return {"controller_bytes": pos, "array_layers": array["m_Depth"], "containers": controller["SplatController"]}
    finally:
        bundle.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["初始化", "索引", "来源", "类型", "地表"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--node")
    parser.add_argument("--id", type=int, action="append", default=[])
    parser.add_argument("--type", action="append", default=[])
    args = parser.parse_args()
    game = Path(json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))["game_directory"])
    batch = (ROOT / args.batch).resolve()
    batch.relative_to((ROOT / "local-data/断箭/批次").resolve())
    start = time.time()
    if args.command == "初始化":
        result = bootstrap(game, batch)
    elif args.command == "来源":
        result = sources(game, batch)
    elif args.command == "类型":
        result = type_layouts(game, batch)
    elif args.command == "地表":
        result = terrain_material(game, batch)
    else:
        result = inspect(game, batch, args.node, args.id, args.type)
    print(json.dumps({"elapsed_s": round(time.time() - start, 3), "result": result}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
