"""只读解包当前 Steam 构建的十五个原作丧尸预制体及引用资源。"""

import argparse
from collections import Counter, deque
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct

import numpy as np
from PIL import Image
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler
from UnityPy.streams import EndianBinaryReader

from 提取丧尸角色 import fingerprint, write
from 资源包分块读取 import UnityFSBundle


ROOT = Path(__file__).resolve().parents[2]
BATCHES = (ROOT / "local-data/断箭/批次").resolve()
PREFAB = re.compile(r"^Assets/Resources_moved/ModelPrefabs/Zombie/[^/]+/(ZOM_[A-Za-z0-9_]+)\.prefab$")
SAMPLE = "ZOM_ArmouredZombie1"
SWARM = "ZOM_Swarm1"
METADATA_TYPES = {
    "Animator", "AnimatorController", "AnimatorOverrideController", "AnimationClip",
    "Avatar", "GameObject", "LODGroup", "Material", "SkinnedMeshRenderer",
    "Transform", "RectTransform", "VisualEffect",
}


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def leaf(path):
    return PurePosixPath(path.replace("\\", "/")).name


class SourceFiles:
    def __init__(self, game, stack):
        self.stack = stack
        self.data = game / "BrokenArrow_Data"
        self.pc = self.data / "StreamingAssets/aa/PC"
        self.env = UnityPy.Environment()
        self.locations = {}
        self.bundles = {}
        self.sources = {}
        candidates = sorted(self.pc.glob("units_assets*.bundle"))
        if len(candidates) != 1:
            raise ValueError(f"预期有一个单位资源包，实际 {len(candidates)} 个")
        self.primary = candidates[0]
        self.indexed = False
        self.mount(self.primary)

    def remember(self, path):
        stat = path.stat()
        self.sources[path] = (stat.st_size, stat.st_mtime_ns)

    def mount(self, path):
        if path in self.bundles:
            return
        self.remember(path)
        source = self.stack.enter_context(path.open("rb"))
        bundle = UnityFSBundle(source, self.sources[path][0])
        self.bundles[path] = bundle
        for name in bundle.nodes:
            self.locations[name.lower()] = path
            reader = EndianBinaryReader(bundle.open_node(name))
            if not name.lower().endswith((".ress", ".resource")):
                reader = UnityPy.files.SerializedFile(reader, parent=self.env, name=name)
            self.env.files[name] = reader
            self.env.register_cab(name, reader)

    def index_dependencies(self):
        for path in sorted(self.pc.glob("*.bundle")):
            if path in self.bundles:
                continue
            with path.open("rb") as stream:
                try:
                    bundle = UnityFSBundle(stream, path.stat().st_size)
                except (ValueError, struct.error):
                    continue
                for node in bundle.nodes:
                    self.locations.setdefault(node.lower(), path)
        self.indexed = True

    def entry(self, name):
        found = self.env.get_cab(name)
        if found is not None:
            return found
        if name.lower() == "unity default resources":
            path = self.data / "Resources/unity default resources"
            self.remember(path)
            reader = EndianBinaryReader(self.stack.enter_context(path.open("rb")))
            af = UnityPy.files.SerializedFile(reader, parent=self.env, name=name)
            self.env.files[name] = af
            self.env.register_cab(name, af)
            self.locations[name.lower()] = path
            return af
        if name.lower() not in self.locations and not self.indexed:
            self.index_dependencies()
        if name.lower() not in self.locations:
            raise FileNotFoundError(f"未找到序列化文件 {name}")
        self.mount(self.locations[name.lower()])
        found = self.env.get_cab(name)
        if found is None:
            raise FileNotFoundError(f"无法访问 {name}")
        return found

    def pointer(self, af, file_id, path_id):
        if not file_id:
            return af.objects[path_id]
        if not 0 < file_id <= len(af.externals):
            raise KeyError(f"未知外部文件 ID {file_id}")
        return self.entry(leaf(af.externals[file_id - 1].path)).objects[path_id]

    def stream(self, name, offset, size):
        reader = self.entry(leaf(name))
        if offset < 0 or size <= 0 or offset + size > reader.Length:
            raise ValueError(f"外部流区间越界：{name} @ {offset} + {size}")
        reader.Position = offset
        return reader.read_bytes(size)

    def identity(self):
        result = []
        for path, original in sorted(self.sources.items()):
            record = fingerprint(path)
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != original:
                raise ValueError(f"核验过程中源文件变化：{path.name}")
            record["game_relative"] = path.relative_to(self.data.parent).as_posix()
            record["mtime_ns"] = original[1]
            result.append(record)
        return result


def object_key(obj):
    return f"{obj.assets_file.name}:{obj.path_id}"


def asset_pointers(value):
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            if value["m_PathID"]:
                yield value["m_FileID"], value["m_PathID"]
        else:
            for item in value.values():
                yield from asset_pointers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from asset_pointers(item)


def prefab_pointers(af, info):
    preload = af.assetbundle.m_PreloadTable
    end = info.preloadIndex + info.preloadSize
    if not 0 <= info.preloadIndex < end <= len(preload):
        raise ValueError(f"预制体 {info.asset.path_id} 的预载区间不正确")
    return [(ptr.file_id, ptr.path_id) for ptr in
            [info.asset, *preload[info.preloadIndex:end]] if ptr.path_id]


def texture_or_mesh(obj):
    decoded = obj.parse_as_object()
    info = decoded.m_StreamData
    streams = [(info.path, info.offset, info.size)] if info and info.path and info.size else []
    return decoded, streams


def source_streams(tree):
    if not tree:
        return []
    info = tree.get("m_Resource") or tree.get("m_ExternalResources") or tree.get("m_StreamData")
    if not isinstance(info, dict):
        return []
    name = info.get("path") or info.get("m_Source")
    size = info.get("size", info.get("m_Size", 0))
    offset = info.get("offset", info.get("m_Offset", 0))
    return [(name, offset, size)] if name and size else []


def mesh_arrays(mesh):
    handler = MeshHandler(mesh)
    handler.process()
    triangles, slots = [], []
    for slot, group in enumerate(handler.get_triangles()):
        base = mesh.m_SubMeshes[slot].baseVertex or 0
        triangles.extend(tuple(int(i) + base for i in tri) for tri in group)
        slots.extend([slot] * len(group))
    result = {
        "vertices": np.asarray(handler.m_Vertices)[:, :3],
        "triangles": np.asarray(triangles, dtype=np.int32).reshape(-1, 3),
        "material_indices": np.asarray(slots, dtype=np.int32),
        "bind": np.asarray([[[getattr(matrix, f"e{row}{col}") for col in range(4)]
                            for row in range(4)] for matrix in mesh.m_BindPose or []]),
    }
    for attribute, key in (
        ("m_Normals", "normals"), ("m_Tangents", "tangents"), ("m_Colors", "colors"),
        ("m_BoneWeights", "weights"), ("m_BoneIndices", "indices"),
        *((f"m_UV{i}", f"uv{i}") for i in range(8)),
    ):
        value = getattr(handler, attribute)
        if value is not None:
            result[key] = np.asarray(value, dtype=np.int32 if key == "indices" else None)
    check_mesh(result)
    return result


def check_mesh(arrays):
    vertices, faces = arrays["vertices"], arrays["triangles"]
    if len(vertices) == 0 or not np.isfinite(vertices).all():
        raise ValueError("网格顶点为空或含无效坐标")
    if len(faces) == 0 or faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("网格三角形为空或索引越界")
    if len(faces) != len(arrays["material_indices"]):
        raise ValueError("材质槽数量与三角形不一致")
    if "weights" not in arrays:
        return
    weights, indices = arrays["weights"], arrays["indices"]
    if len(weights) != len(vertices) or weights.shape != indices.shape or not np.isfinite(weights).all():
        raise ValueError("蒙皮权重维度或数值异常")
    used = indices[weights > 0]
    if used.size == 0 or used.min() < 0 or used.max() >= len(arrays["bind"]):
        raise ValueError("蒙皮索引越界或缺少绑定矩阵")
    if not np.allclose(weights.sum(axis=1), 1, atol=2e-4):
        raise ValueError("源蒙皮权重总和超过校验容差")


def save_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def json_value(value):
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    raise TypeError(f"未适配的字段类型 {type(value).__name__}")


def save_fields(path, tree):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(tree, stream, ensure_ascii=False, indent=2, default=json_value)
        stream.write("\n")


class Exporter:
    def __init__(self, sources, output):
        self.sources = sources
        self.output = output
        self.objects = {}
        self.streams = {}

    def save_stream(self, name, offset, length):
        source_key = (leaf(name).lower(), offset, length)
        if source_key not in self.streams:
            raw = self.sources.stream(name, offset, length)
            digest = hashlib.sha256(raw).hexdigest()
            relative = f"原始流数据/{digest}.bin"
            if not (self.output / relative).exists():
                save_bytes(self.output / relative, raw)
            self.streams[source_key] = {
                "node": leaf(name), "offset": offset, "bytes": length,
                "file": relative, "sha256": digest,
            }
        return self.streams[source_key]

    def save_object(self, obj):
        key = object_key(obj)
        if key in self.objects:
            return self.objects[key]
        kind = obj.type.name
        af = obj.assets_file
        stem = f"{af.name}/{obj.path_id}"
        relative = f"原始对象/{kind}/{stem}.bin"
        raw = obj.get_raw_data()
        if len(raw) != obj.byte_size:
            raise ValueError(f"对象字节长度不符：{key}")
        save_bytes(self.output / relative, raw)
        row = {
            "type": kind, "serialized_file": af.name, "path_id": str(obj.path_id),
            "offset": obj.byte_start, "bytes": len(raw), "file": relative,
            "sha256": hashlib.sha256(raw).hexdigest(), "references": [],
        }
        self.objects[key] = row

        if kind in ("Mesh", "Texture2D"):
            decoded, streams = texture_or_mesh(obj)
            row["name"] = decoded.m_Name
            row["streams"] = [self.save_stream(*info) for info in streams]
            if kind == "Mesh":
                arrays = mesh_arrays(decoded)
                file = f"可读网格/{stem}.npz"
                path = self.output / file
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as out:
                    np.savez_compressed(out, **arrays)
                row["mesh"] = {
                    "file": file, "vertices": len(arrays["vertices"]),
                    "triangles": len(arrays["triangles"]), "bind_poses": len(arrays["bind"]),
                }
            else:
                file = f"可读贴图/{stem}.png"
                path = self.output / file
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as out:
                    decoded.image.save(out, format="PNG")
                row["image"] = {"file": file, "width": decoded.m_Width, "height": decoded.m_Height,
                                "source_format": int(decoded.m_TextureFormat)}
            return row

        try:
            tree = obj.parse_as_dict()
        except (ValueError, EOFError, struct.error, TypeError) as error:
            if kind in METADATA_TYPES:
                raise
            row["tree_error"] = str(error)
            return row
        row["name"] = tree.get("m_Name") or kind
        row["references"] = sorted(set(asset_pointers(tree)))
        row["streams"] = [self.save_stream(*info) for info in source_streams(tree)]
        if kind in METADATA_TYPES:
            row["fields"] = f"源字段/{kind}/{stem}.json"
            save_fields(self.output / row["fields"], tree)
        return row

    def collect(self, name, source, af, info):
        pending = deque((af, file_id, path_id, "预载") for file_id, path_id in prefab_pointers(af, info))
        seen = set()
        edges = {}
        missing = {}
        while pending:
            origin, file_id, path_id, caller = pending.popleft()
            try:
                obj = self.sources.pointer(origin, file_id, path_id)
            except (KeyError, FileNotFoundError) as error:
                missing[(origin.name, file_id, path_id)] = {
                    "from": caller, "file": origin.name, "file_id": file_id,
                    "path_id": str(path_id), "error": str(error),
                }
                continue
            key = object_key(obj)
            edges.setdefault(caller, set()).add(key)
            if key in seen:
                continue
            seen.add(key)
            row = self.save_object(obj)
            pending.extend((obj.assets_file, f, p, key) for f, p in row["references"])
        counts = Counter(self.objects[key]["type"] for key in seen)
        result = {
            "role": name, "source": source, "root": object_key(info.asset.deref()), "counts": dict(counts),
            "objects": sorted(seen), "edges": {k: sorted(v) for k, v in sorted(edges.items())},
            "missing": list(missing.values()),
            "serialized_files": sorted({self.objects[k]["serialized_file"] for k in seen}),
        }
        check_role(result, self.objects, self.output)
        return result


def check_role(role, objects, output):
    name = role["role"]
    rows = [objects[key] for key in role["objects"]]
    counts = Counter(row["type"] for row in rows)
    if role["missing"]:
        print(json.dumps({"role": name, "missing": role["missing"]}, ensure_ascii=False), flush=True)
    if role["root"] not in role["objects"] or counts["GameObject"] == 0 or counts["Transform"] == 0:
        raise ValueError(f"{name} 源层级缺失")
    if any(target not in role["objects"] for targets in role["edges"].values() for target in targets):
        raise ValueError(f"{name} 的依赖清单引用了未登记对象")
    if name == SWARM:
        for kind in ("VisualEffect", "VisualEffectAsset", "ComputeShader", "Material"):
            if not counts[kind]:
                raise ValueError(f"{name} 缺少 {kind}")
        return
    expected = ("Animator", "AnimatorController", "AnimationClip", "Avatar", "LODGroup", "Material")
    if any(not counts[kind] for kind in expected) or counts["SkinnedMeshRenderer"] != 3:
        raise ValueError(f"{name} 骨骼、网格、材质、细节层或动画对象不完整")
    if counts["Mesh"] < 3 or not counts["Texture2D"]:
        raise ValueError(f"{name} 的网格或贴图缺失")
    for key in role["objects"]:
        row = objects[key]
        if row["type"] != "SkinnedMeshRenderer":
            continue
        fields = read_json(output / row["fields"])
        refs = [objects[k] for k in role["edges"].get(key, [])]
        mesh_id = str(fields["m_Mesh"]["m_PathID"])
        mesh, = (ref for ref in refs if ref["type"] == "Mesh" and ref["path_id"] == mesh_id)
        bones = fields["m_Bones"]
        resolved = {ref["path_id"] for ref in refs if ref["type"] in ("Transform", "RectTransform")}
        if len(bones) != mesh["mesh"]["bind_poses"] or any(
            str(bone["m_PathID"]) not in resolved for bone in bones
        ):
            raise ValueError(f"{name} 源骨骼引用与绑定矩阵不一致")


def check_payload(root, row, sources=None):
    original = root / row["file"]
    if original.stat().st_size != row["bytes"] or sha_file(original) != row["sha256"]:
        raise ValueError(f"对象回读摘要不符：{row['file']}")
    if sources is not None:
        af = sources.entry(row["serialized_file"])
        obj = af.objects[int(row["path_id"])]
        if (obj.type.name != row["type"] or obj.byte_start != row["offset"]
                or hashlib.sha256(obj.get_raw_data()).hexdigest() != row["sha256"]):
            raise ValueError(f"回读对象与安装源不一致：{row['file']}")
        if obj.type.name not in ("Mesh", "Texture2D") and "tree_error" not in row:
            refs = [list(ref) for ref in sorted(set(asset_pointers(obj.parse_as_dict())))]
            if refs != row["references"]:
                raise ValueError(f"源对象引用与清单不符：{row['file']}")
    for stream in row.get("streams", []):
        path = root / stream["file"]
        if path.stat().st_size != stream["bytes"] or sha_file(path) != stream["sha256"]:
            raise ValueError(f"外部流回读摘要不符：{stream['file']}")
        if sources is not None:
            raw = sources.stream(stream["node"], stream["offset"], stream["bytes"])
            if hashlib.sha256(raw).hexdigest() != stream["sha256"]:
                raise ValueError(f"外部流与安装源不一致：{stream['file']}")
    if "mesh" in row:
        mesh = row["mesh"]
        with np.load(root / mesh["file"], allow_pickle=False) as arrays:
            check_mesh(arrays)
            if (len(arrays["vertices"]), len(arrays["triangles"])) != (mesh["vertices"], mesh["triangles"]):
                raise ValueError(f"网格回读数量不符：{mesh['file']}")
    if "image" in row:
        image = row["image"]
        with Image.open(root / image["file"]) as img:
            if img.size != (image["width"], image["height"]):
                raise ValueError(f"贴图尺寸不符：{image['file']}")
            img.verify()


def source_roles(sources):
    primary = sources.bundles[sources.primary]
    roles = {}
    for node in primary.nodes:
        af = sources.entry(node)
        if not isinstance(af, UnityPy.files.SerializedFile) or not af.assetbundle:
            continue
        for path, info in af.assetbundle.m_Container:
            match = PREFAB.fullmatch(path)
            if not match:
                continue
            role = match[1]
            if role in roles:
                raise ValueError(f"角色条目重复：{role}")
            roles[role] = (path, af, info)
    if len(roles) != 15 or SAMPLE not in roles or SWARM not in roles:
        raise ValueError(f"源目录与授权的 15 款不符：{sorted(roles)}")
    return roles


def batch_summary(roles, objects):
    counts = Counter(row["type"] for row in objects.values())
    return {
        "roles": len(roles),
        "skinned_roles": sum(name != SWARM for name in roles),
        "vfx_roles": [SWARM],
        "unique_objects": len(objects),
        "unique_types": dict(sorted(counts.items())),
        "unique_streams": len({s["file"] for row in objects.values() for s in row.get("streams", [])}),
        "missing_references": sum(len(role["missing"]) for role in roles.values()),
        "objects_with_raw_only_fields": sum("tree_error" in row for row in objects.values()),
    }


def verify_role_edges(sources, role, objects, af, info):
    def resolve(origin, refs):
        return sorted({object_key(sources.pointer(origin, file_id, path_id)) for file_id, path_id in refs})

    candidates = {"预载": resolve(af, prefab_pointers(af, info))}
    pending = deque(candidates["预载"])
    reached = set()
    while pending:
        key = pending.popleft()
        if key in reached:
            continue
        reached.add(key)
        row = objects[key]
        if row["references"]:
            candidates[key] = resolve(sources.entry(row["serialized_file"]), row["references"])
            pending.extend(candidates[key])
    if role["missing"] or candidates != role["edges"] or reached != set(role["objects"]):
        raise ValueError(f"{role['role']} 的源引用与批次依赖清单不一致")


def extract(batch, sources, build, task):
    roles = source_roles(sources)
    stage = batch / "待核验"
    if (batch / "交付").exists() or (batch / "核验结果.json").exists():
        raise FileExistsError("批次已交付，拒绝覆盖")
    batch.mkdir(exist_ok=True)
    stage.mkdir()
    exporter = Exporter(sources, stage)
    results = {}
    for name in [SAMPLE, *sorted(roles.keys() - {SAMPLE})]:
        role = exporter.collect(name, *roles[name])
        write(stage / "角色清单" / f"{name}.json", role)
        results[name] = role
        if name == SAMPLE:
            for key in role["objects"]:
                check_payload(stage, exporter.objects[key], sources)
            if role["missing"]:
                raise ValueError("装甲丧尸存在未解析依赖，暂停整批解包")
            write(stage / "装甲丧尸样本核验.json", {
                "role": name, "status": "通过", "objects": len(role["objects"]),
                "counts": role["counts"], "serialized_files": role["serialized_files"],
            })
        print(json.dumps({"role": name, "objects": len(role["objects"]), "missing": len(role["missing"]),
                          "sample_verified": name == SAMPLE}, ensure_ascii=False), flush=True)

    manifest = {
        "build": build, "task": task, "unitypy": UnityPy.__version__,
        "unityfs": sources.bundles[sources.primary].version,
        "unity_engine": sources.bundles[sources.primary].engine,
        "sources": sources.identity(), "roles": sorted(roles),
        "objects": exporter.objects, "summary": batch_summary(results, exporter.objects),
    }
    write(stage / "对象清单.json", manifest)
    files = [p for p in sorted(stage.rglob("*")) if p.is_file()]
    write(stage / "文件校验.json", {
        p.relative_to(stage).as_posix(): {"bytes": p.stat().st_size, "sha256": sha_file(p)} for p in files
    })
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)


def verify(batch, sources, build):
    pending = batch / "待核验"
    published = batch / "交付"
    root = pending if pending.is_dir() else published
    if pending.exists() and published.exists():
        raise ValueError("同一批次同时存在候选和交付目录")
    manifest = read_json(root / "对象清单.json")
    expected = source_roles(sources)
    if manifest["build"] != build or set(manifest["roles"]) != set(expected):
        raise ValueError("源包构建或角色清单与当前安装不一致")
    checksums = read_json(root / "文件校验.json")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != checksums.keys() | {"文件校验.json"}:
        raise ValueError("文件校验清单和批次实际文件集合不一致")
    for file, record in checksums.items():
        target = root / file
        if target.stat().st_size != record["bytes"] or sha_file(target) != record["sha256"]:
            raise ValueError(f"批次文件摘要错误：{file}")

    objects = manifest["objects"]
    roles = {}
    for name, (path, source_file, info) in expected.items():
        role = read_json(root / "角色清单" / f"{name}.json")
        if role["role"] != name or role["source"] != path or role["root"] != object_key(info.asset.deref()):
            raise ValueError(f"角色根标识不符：{name}")
        check_role(role, objects, root)
        verify_role_edges(sources, role, objects, source_file, info)
        roles[name] = role
    if set(objects) != set().union(*(set(role["objects"]) for role in roles.values())):
        raise ValueError("共享对象与各角色引用集合不一致")
    for row in objects.values():
        check_payload(root, row, sources)
    if sources.identity() != manifest["sources"]:
        raise ValueError("游戏资源包摘要与解包时不一致")
    summary = batch_summary(roles, objects)
    if summary != manifest["summary"]:
        raise ValueError("核验汇总与提取清单不一致")
    receipt = {"build": build, "files_checked": len(checksums), **summary,
               "manifest_sha256": sha_file(root / "文件校验.json")}
    if root == pending:
        pending.rename(published)
    receipt_path = batch / "核验结果.json"
    if receipt_path.exists():
        if read_json(receipt_path) != receipt:
            raise ValueError("现有核验回执与二次回读不一致")
    else:
        write(receipt_path, receipt)
    print(json.dumps({"status": "核验通过", "files": len(checksums), **summary}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, help="BA-当前构建号-三位序号")
    parser.add_argument("--stage", choices=("extract", "verify"), required=True)
    parser.add_argument("--task", help="已登记的任务名称，解包阶段必填")
    args = parser.parse_args()
    if not re.fullmatch(r"BA-\d+-\d{3}", args.batch):
        parser.error("批次编号格式错误")
    config = read_json(ROOT / "local-data/断箭/本地配置.json")
    steam_file = Path(config["steam_manifest"]).read_text(encoding="utf-8-sig")
    match = re.search(r'"buildid"\s+"(\d+)"', steam_file)
    if not match or args.batch.split("-")[1] != match[1]:
        parser.error("批次构建号与本机 Steam 安装不一致")
    if args.stage == "extract" and not args.task:
        parser.error("解包阶段必须提供已登记的任务名称")
    batch = (BATCHES / args.batch).resolve()
    if batch.parent != BATCHES:
        parser.error("批次路径越界")
    with ExitStack() as stack:
        sources = SourceFiles(Path(config["game_directory"]), stack)
        if args.stage == "extract":
            extract(batch, sources, match[1], args.task)
        else:
            verify(batch, sources, match[1])


if __name__ == "__main__":
    main()
