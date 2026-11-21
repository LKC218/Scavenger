"""只读解析单位缓存，导出最高细节、游戏初始状态的静态载具场景。"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import time

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

ROOT = Path(__file__).resolve().parents[2]
C = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
VERIFIED_SHADER_IDS = {2703859142610330766: "UnitV3", 1050977530402573550: "UnitV4_Camo"}


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def transform_matrix(t):
    q = t["m_LocalRotation"]
    x, y, z, w = (q[k] for k in "xyzw")
    m = np.eye(4)
    m[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                  [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                  [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    m[:3, :3] *= [t["m_LocalScale"][k] for k in "xyz"]
    m[:3, 3] = [t["m_LocalPosition"][k] for k in "xyz"]
    return m


class Extractor:
    def __init__(self, batch):
        self.batch = batch.resolve()
        if not self.batch.is_relative_to((ROOT / "local-data/断箭/批次").resolve()):
            raise ValueError("输出必须位于约定批次目录")
        self.env = UnityPy.Environment()
        for path in (self.batch / "解析缓存").glob("CAB-*"):
            self.env.load_file(str(path))
        if not self.env.container:
            raise ValueError("没有可用的单位缓存")
        self.texture_dir = self.batch / "原始贴图"
        self.texture_dir.mkdir(exist_ok=True)
        self.config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
        self.material_cache = {}
        self.mesh_cache = {}
        self.texture_cache = {}
        spec = importlib.util.spec_from_file_location("texture_tools", Path(__file__).with_name("纹理工具.py"))
        self.texture_tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.texture_tools)

    def texture(self, ptr, af):
        key = f"{ptr['m_FileID']}_{ptr['m_PathID']}"
        if key in self.texture_cache:
            return self.texture_cache[key]
        if not ptr["m_PathID"]:
            return None
        obj = UnityPy.classes.PPtr(**ptr, assetsfile=af).deref()
        path = self.texture_dir / f"{obj.path_id}.png"
        if not path.exists():
            obj.read().image.save(path)
        self.texture_cache[key] = str(path)
        return str(path)

    def material(self, ptr, af, warnings):
        key = str(ptr["m_PathID"])
        if key in self.material_cache:
            warnings.extend(self.material_cache[key].get("warnings", []))
            return key, self.material_cache[key]
        warning_start = len(warnings)
        obj = UnityPy.classes.PPtr(**ptr, assetsfile=af).deref()
        data = obj.read_typetree()
        entry = {"name": data["m_Name"], "raw": data, "textures": {}, "derived": {}}
        for name, tex in data["m_SavedProperties"]["m_TexEnvs"]:
            if tex["m_Texture"]["m_PathID"]:
                try:
                    entry["textures"][name] = {"file": self.texture(tex["m_Texture"], obj.assets_file), "scale": tex["m_Scale"], "offset": tex["m_Offset"]}
                except Exception as error:
                    warnings.append(f"贴图解析失败：{data['m_Name']}/{name}：{error}")
        # 虚拟纹理的通道转换由独立工具提供，保留原材质作为核对依据。
        mod = self.texture_tools
        normal_property = next((key for key in ("_NormalMap", "_Normal") if key in entry["textures"]), None)
        if normal_property:
            entry["derived"]["normal"] = mod.convert_unity_normal(entry["textures"][normal_property]["file"])
        if "_MaskMap" in entry["textures"]:
            entry["derived"].update(mask=entry["textures"]["_MaskMap"]["file"], mask_encoding="linear", shader_model="HDRP")
        for stack in data.get("m_BuildTextureStacks", []):
            tileset = Path(self.config["game_directory"]) / "BrokenArrow_Data/StreamingAssets" / (stack["groupName"] + ".gts")
            try:
                if data["m_Shader"]["m_FileID"] or data["m_Shader"]["m_PathID"] not in VERIFIED_SHADER_IDS:
                    raise ValueError(f"着色器尚未核验通道：{data['m_Shader']}")
                if hasattr(mod, "prepare_material_maps"):
                    entry["derived"].update(mod.prepare_material_maps(tileset, stack["itemName"], shader_model=VERIFIED_SHADER_IDS[data["m_Shader"]["m_PathID"]]))
                else:
                    receipt = mod.extract_texture(tileset, stack["itemName"])
                    entry["virtual_texture"] = receipt
                    warnings.append(f"虚拟纹理已提取，尚未接入转换：{data['m_Name']}")
            except Exception as error:
                warnings.append(f"虚拟纹理解析失败：{data['m_Name']}：{error}")
        entry["warnings"] = warnings[warning_start:]
        self.material_cache[key] = entry
        return key, entry

    def refresh_materials(self, scene_path):
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        af = self.env.container[scene["source_path"]].deref().assets_file
        warnings, materials = [], {}
        for key in scene["materials"]:
            _, materials[key] = self.material({"m_FileID": 0, "m_PathID": int(key)}, af, warnings)
        backup = scene_path.with_name("scene-材质刷新前.json")
        if not backup.exists():
            save_json(backup, scene)
        scene.update(materials=materials, warnings=warnings)
        save_json(scene_path, scene)
        return {"scene": str(scene_path), "materials": len(materials), "warnings": warnings}

    def export(self, source_path, category, retry_incomplete=False, source_ptr=None, lod_level=0):
        if not isinstance(lod_level, int) or lod_level < 0:
            raise ValueError('LOD级别必须为非负整数')
        start = time.time()
        ptr = source_ptr if source_ptr is not None else self.env.container[source_path]
        af = ptr.deref().assets_file
        name = Path(source_path).stem
        slug = re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")
        if category == "attachment":
            slug += "-" + str(ptr.path_id)[-8:]
        target = self.batch / "场景数据" / category / slug
        marker = target / "scene.json"
        if marker.exists():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous["source_path"] != source_path:
                raise ValueError("场景名称碰撞，拒绝复用其他预制体")
            if previous.get('lod_level', 0) != lod_level:
                raise ValueError('已有来源LOD不同，须使用独立批次路径')
            return {"name": name, "category": category, "scene": str(marker), "reused": True}
        if target.exists():
            if not retry_incomplete:
                raise FileExistsError(f"场景目录尚未完成：{target}")
            backup = self.batch / "未完成场景" / category / (slug + "-" + str(time.time_ns()))
            if not target.resolve().is_relative_to(self.batch) or not backup.resolve().is_relative_to(self.batch):
                raise ValueError("重试目录超出本批次")
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        target.mkdir(parents=True)
        go_root = ptr.read_typetree()
        root = next(c["component"]["m_PathID"] for c in go_root["m_Component"] if af.objects[c["component"]["m_PathID"]].type.name == "Transform")
        nodes, components, worlds = {}, {}, {}
        warnings, skipped = [], []

        def walk(tid, parent=None, parent_active=True):
            tr = af.objects[tid].read_typetree()
            go = af.objects[tr["m_GameObject"]["m_PathID"]].read_typetree()
            active = parent_active and go["m_IsActive"]
            comps = [(af.objects[c["component"]["m_PathID"]], af.objects[c["component"]["m_PathID"]].read_typetree()) for c in go["m_Component"]]
            nodes[tid] = {"id": str(tid), "name": go["m_Name"], "parent": str(parent) if parent else None, "matrix": (C @ transform_matrix(tr) @ C).tolist(), "meshes": [], "active": active}
            components[tid] = comps
            worlds[tid] = (worlds[parent] if parent else np.eye(4)) @ transform_matrix(tr)
            for child in tr["m_Children"]:
                walk(child["m_PathID"], tid, active)

        walk(root)
        excluded_renderers, hidden_roots = set(), set()
        for comps in components.values():
            for obj, data in comps:
                if obj.type.name == "LODGroup":
                    lods = data["m_LODs"]
                    if lods and lod_level >= len(lods):
                        raise ValueError(f'源LODGroup没有请求的级别：{lod_level}')
                    keep = {p["renderer"]["m_PathID"] for p in lods[lod_level]["renderers"]} if lods else set()
                    excluded_renderers.update(p["renderer"]["m_PathID"] for lod in lods for p in lod["renderers"] if p["renderer"]["m_PathID"] not in keep)
                if obj.type.name == "MonoBehaviour" and "_gameState" in data:
                    ids = {p["rid"] for p in data["_gameState"]}
                    for ref in data.get("references", {}).get("RefIds", []):
                        if ref["rid"] in ids and ref["type"]["class"] == "HideMesh":
                            hidden_roots.add(ref["data"]["_root"]["m_PathID"])

        hidden = set()
        for tid, node in nodes.items():
            parent = tid
            while parent:
                if parent in hidden_roots:
                    hidden.add(tid)
                    break
                par = nodes[parent]["parent"]
                parent = int(par) if par else None
        materials = {}
        for tid, node in nodes.items():
            for obj, data in components[tid]:
                if obj.type.name not in ("MeshRenderer", "SkinnedMeshRenderer"):
                    continue
                reason = "LOD低细节" if obj.path_id in excluded_renderers else "游戏状态隐藏" if tid in hidden else "未启用" if not node["active"] or not data.get("m_Enabled", True) else None
                if reason:
                    skipped.append({"renderer": str(obj.path_id), "name": node["name"], "reason": reason})
                    continue
                mesh_ptr = data.get("m_Mesh")
                if mesh_ptr is None:
                    mesh_ptr = next((d["m_Mesh"] for o, d in components[tid] if o.type.name == "MeshFilter"), None)
                if not mesh_ptr or not mesh_ptr["m_PathID"]:
                    warnings.append(f"渲染器无网格：{node['name']}")
                    continue
                mesh_obj = UnityPy.classes.PPtr(**mesh_ptr, assetsfile=af).deref()
                mesh_key = (mesh_obj.assets_file.name, mesh_obj.path_id)
                if mesh_key not in self.mesh_cache:
                    mesh = mesh_obj.read()
                    handler = MeshHandler(mesh)
                    handler.process()
                    self.mesh_cache[mesh_key] = (mesh, handler)
                mesh, handler = self.mesh_cache[mesh_key]
                verts = np.asarray(handler.m_Vertices, dtype=float)[:, :3]
                normals = np.asarray(handler.m_Normals if handler.m_Normals else [], dtype=float)
                if normals.size:
                    normals = normals[:, :3]
                skinned = obj.type.name == "SkinnedMeshRenderer"
                if skinned and data.get("m_Bones") and handler.m_BoneIndices:
                    ids = np.asarray(handler.m_BoneIndices, dtype=int)
                    if ids.ndim == 1:
                        ids = ids[:, None]
                    weights = np.asarray(handler.m_BoneWeights, dtype=float) if handler.m_BoneWeights else np.ones((len(verts), 1))
                    if weights.shape != ids.shape:
                        raise ValueError("骨骼索引与权重维度不符")
                    matrices = []
                    inv = np.linalg.inv(worlds[tid])
                    for bi, (bone, pose) in enumerate(zip(data["m_Bones"], mesh.m_BindPose)):
                        bind = np.array([[getattr(pose, f"e{i}{j}") for j in range(4)] for i in range(4)])
                        bone_id = bone["m_PathID"]
                        if not bone_id:
                            used = int(((ids == bi) & (weights > 0)).sum())
                            matrices.append(np.eye(4))
                            if used:
                                warnings.append(f"空骨引用保留原始绑定姿态：{node['name']}/骨索引{bi}/影响权重{used}项；运行姿态未核验")
                            continue
                        if bone_id not in worlds:
                            raise ValueError(f"骨骼不在预制体层级：{bone_id}")
                        matrices.append(inv @ worlds[bone_id] @ bind)
                    mats = np.asarray(matrices)
                    blend = (mats[ids] * weights[:, :, None, None]).sum(axis=1)
                    verts = np.einsum("nij,nj->ni", blend, np.column_stack([verts, np.ones(len(verts))]))[:, :3]
                    if normals.size:
                        normals = np.einsum("nij,nj->ni", np.linalg.inv(blend[:, :3, :3]).transpose(0, 2, 1), normals)
                        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-10)
                triangles, material_ids = [], []
                for mi, tris in enumerate(handler.get_triangles()):
                    base_vertex = getattr(mesh.m_SubMeshes[mi], "baseVertex", 0) or 0
                    if base_vertex:
                        tris = [tuple(i + base_vertex for i in tri) for tri in tris]
                    triangles.extend(tris)
                    material_ids.extend([mi] * len(tris))
                vertices = verts @ C[:3, :3]
                tris = np.asarray(triangles, dtype=np.int32)[:, [0, 2, 1]]
                if not len(tris) or not np.isfinite(vertices).all():
                    raise ValueError("网格为空或坐标非有限")
                file = f"{obj.path_id}.npz"
                uv1 = getattr(handler, "m_UV1", None)
                np.savez_compressed(target / file, vertices=vertices.astype(np.float32), normals=(normals @ C[:3, :3]).astype(np.float32) if normals.size else [], uv0=np.asarray(handler.m_UV0 or [], dtype=np.float32)[:, :2] if handler.m_UV0 else [], uv1=np.asarray(uv1, dtype=np.float32)[:, :2] if uv1 else [], triangles=tris, material_indices=np.asarray(material_ids, dtype=np.int32))
                mat_keys = []
                for p in data["m_Materials"]:
                    if p["m_PathID"]:
                        key, mat = self.material(p, af, warnings)
                        mat_keys.append(key)
                        materials[key] = mat
                    else:
                        mat_keys.append("missing")
                        warnings.append(f"空材质：{node['name']}")
                node["meshes"].append({"file": file, "materials": mat_keys, "renderer_id": str(obj.path_id), "skinned": skinned, "source_mesh_id": str(mesh_obj.path_id), "vertices": len(vertices), "triangles": len(tris)})
        count = sum(len(node["meshes"]) for node in nodes.values())
        if not count:
            raise ValueError("预制体没有可导出的可见网格")
        scene = {"name": name, "source_path": source_path, "category": category, "coordinate_conversion": "Unity(x,y,z)→Blender(x,z,y)，反转三角形绕序", "pose": f"预制体游戏初始静态姿态；应用HideMesh与LOD{lod_level}；不含运行时动画与动态外挂", "lod_level": lod_level, "nodes": list(nodes.values()), "materials": materials, "warnings": warnings, "skipped": skipped, "mesh_count": count, "elapsed_seconds": round(time.time()-start, 3)}
        save_json(marker, scene)
        return {"name": name, "category": category, "scene": str(marker), "meshes": count, "warnings": warnings, "elapsed_seconds": scene["elapsed_seconds"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-001")
    parser.add_argument("--name", default="US_A10")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--category", default="air")
    parser.add_argument("--refresh-materials", type=Path)
    parser.add_argument("--retry-incomplete", action="store_true")
    args = parser.parse_args()
    worker = Extractor(ROOT / "local-data/断箭/批次" / args.batch)
    if args.refresh_materials:
        path = args.refresh_materials.resolve()
        if not path.is_relative_to(worker.batch / "场景数据"):
            raise ValueError("只刷新当前批次的场景材质")
        print(json.dumps(worker.refresh_materials(path), ensure_ascii=False))
        return
    if args.manifest:
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries["candidates"]
        entries = [e for e in entries if e["category"] in ("air", "ground")]
    else:
        entries = [{"path": p, "category": args.category} for p in worker.env.container if Path(p).stem == args.name and p.endswith(".prefab")]
    results = []
    for entry in sorted(entries, key=lambda e: (e["category"] != "air", e["path"])):
        try:
            result = worker.export(entry["path"], entry["category"], args.retry_incomplete)
        except Exception as error:
            result = {"name": Path(entry["path"]).stem, "category": entry["category"], "error": repr(error)}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        save_json(worker.batch / "场景导出记录.json", results)


if __name__ == "__main__":
    main()
