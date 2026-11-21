"""本机正式资料库：版本、来源、查询及增量入库。仅使用标准库。"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import uuid
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "local-data/断箭"
LIB = DATA / "正式资料库"
BASE = DATA / "批次"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def token(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def file_record(path):
    path = Path(path).resolve()
    if not path.is_relative_to(DATA.resolve()) or path.is_symlink():
        raise ValueError("正式资料路由必须位于本地资料边界内")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}


def check_file(row):
    path = Path(row["path"])
    if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
        raise ValueError("资源指纹不一致：" + str(path))
    return path


def constrained(base, relative):
    path = (Path(base) / relative).resolve()
    if not path.is_relative_to(Path(base).resolve()):
        raise ValueError("相对路径越界")
    return path


def resource_target(kind, identity, category, extension, digest, lib=LIB):
    """正式媒体按用途和稳定ID归档；URL仍由内容摘要标识。"""
    def component(value):
        value = str(value)
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
        if not cleaned or cleaned in (".", ".."):
            cleaned = "未分类"
        if cleaned != value or cleaned.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            cleaned += "-" + token(value)[:8]
        return cleaned
    if kind == "audio":
        relative = Path("资源/音效") / component(category) / (digest + extension)
    else:
        group = {"air": "空中", "ground": "地面"}.get(category, "其他")
        folder = component(identity.removeprefix("model:"))
        label = {"glb": "模型", "blend": "工程", "preview": "预览"}[kind]
        relative = Path("资源/模型") / group / folder / (label + extension)
    return constrained(lib, relative)


def glb_check(path):
    with Path(path).open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("GLB头截断")
        magic, version, length, json_length, chunk_type = struct.unpack("<4sIIII", header)
        if magic != b"glTF" or version != 2 or length != Path(path).stat().st_size or chunk_type != 0x4e4f534a or json_length > length - 20:
            raise ValueError("GLB头或长度不符")
        data = json.loads(stream.read(json_length))
        if any(row.get("uri") and not row["uri"].startswith("data:") for row in data.get("buffers", []) + data.get("images", [])):
            raise ValueError("当前入库要求自包含GLB，存在外部依赖")
    return {"status": "passed", "version": version}


def wav_check(path):
    peak, nonzero, clipped, actual_samples = 0, 0, 0, 0
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() not in (1, 2) or stream.getnframes() <= 0:
            raise ValueError("当前入库接受非空PCM16单声道或双声道WAV")
        details = {"sample_rate": stream.getframerate(), "channels": stream.getnchannels(), "frames": stream.getnframes(),
                   "duration_seconds": stream.getnframes() / stream.getframerate(), "format": "PCM16"}
        while block := stream.readframes(65536):
            actual_samples += len(block) // 2
            for value, in struct.iter_unpack("<h", block):
                peak = max(peak, abs(value)); nonzero += value != 0; clipped += value in (-32768, 32767)
        if actual_samples != details["frames"] * details["channels"]:
            raise ValueError("WAV数据截断，实际样本数与头部不符")
    return {**details, "peak": peak / 32768, "nonzero_samples": nonzero, "clip_samples": clipped,
            "technical_status": "ready" if nonzero and not clipped else "review", "listening_status": "pending", "loop_status": "unknown"}


def validate_catalog(catalog, routes):
    ids = [row["id"] for row in catalog["models"] + catalog["audio"] + catalog.get("scenes", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("资源ID重复")
    models = {r["id"]: r for r in catalog["models"]}
    audio = {r["id"]: r for r in catalog["audio"]}
    for scene in catalog.get("scenes", []):
        if scene.get("type") != "scene" or any(scene.get(key) not in routes for key in ("scene_url", "preview_url", "blend_url")):
            raise ValueError("场景类型或媒体路由无效")
        if any(identity not in models for identity in scene.get("asset_ids", [])):
            raise ValueError("场景独立原型引用失联")
    for row in [*models.values(), *audio.values()]:
        if row["type"] == "model":
            urls = [row["model_url"], row["preview_url"]] + ([row["blend_url"]] if row.get("blend_url") else [])
            for audio_id in row["audio_ids"]:
                if audio_id not in audio or row["id"] not in audio[audio_id]["model_ids"]:
                    raise ValueError("模型声音反向关系不一致")
        else:
            urls = [row["url"]]
            for model_id in row["model_ids"]:
                if model_id not in models or row["id"] not in models[model_id]["audio_ids"]:
                    raise ValueError("声音模型反向关系不一致")
        if any(url not in routes for url in urls):
            raise ValueError("缺少媒体路由")
    catalog["summary"] = {"models": len(models), "scenes": len(catalog.get("scenes", [])), "audio": len(audio), "models_with_audio": sum(bool(r["audio_ids"]) for r in models.values()),
                          "associated_audio": sum(bool(r["model_ids"]) for r in audio.values()),
                          "unassigned_audio": sum(not r["model_ids"] for r in audio.values()),
                          "model_statuses": dict(Counter(r["quality_status"] for r in models.values())),
                          "audio_statuses": dict(Counter(r["quality_status"] for r in audio.values()))}
    return catalog


@contextmanager
def write_lock(lib):
    lib.mkdir(parents=True, exist_ok=True)
    path = lib / "写入锁.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "started_at": stamp()}, stream)
    try:
        yield
    finally:
        path.unlink()


def current(lib=LIB):
    if Path(lib).resolve()==LIB.resolve():
        import sys
        sys.path.insert(0,str(ROOT/'scripts/资源库'))
        from 统一资源 import available,current as unified_current,routes_absolute
        if available():
            p,c,r,b=unified_current()
            return {**p,'revision':c['revision']},c,routes_absolute(r),b
    pointer = read(lib / "当前版本.json")
    version = constrained(lib, pointer["directory"])
    manifest = read(version / "版本核验.json")
    for row in manifest["files"]:
        check_file({**row, "path": str(constrained(version, row["path"]))})
    return pointer, read(version / "资源索引.json"), read(version / "资源路由.json"), version


def guard_legacy_write(lib):
    """新统一库建立后，旧批次发布器不能回写退役媒体目录。"""
    if Path(lib).resolve()==LIB.resolve() and (ROOT/'local-data/资源库/当前版本.json').exists():
        raise ValueError('旧格式入库已退役；当前资源需先在统一库待入库目录生成并核验256／512规格、原件与主工程，再设计发布清单。查询和既有资源维护不受影响。')


def publish(catalog, routes, sources, lib=LIB, expected_revision=0):
    guard_legacy_write(lib)
    old_revision = read(lib / "当前版本.json")["revision"] if (lib / "当前版本.json").exists() else 0
    if old_revision != expected_revision:
        raise ValueError("资料库版本已变化，请重新校验入库清单")
    validate_catalog(catalog, routes)
    revision = old_revision + 1
    catalog.update(schema_version=1, revision=revision, status="formal", updated_at=stamp())
    path = lib / "版本" / f"v{revision:04d}"
    path.mkdir(parents=True)
    save(path / "资源索引.json", catalog)
    save(path / "资源路由.json", routes)
    save(path / "来源清单.json", sources)
    save(path / "版本核验.json", {"files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(path.iterdir())]})
    pointer = {"schema_version": 1, "status": "formal", "revision": revision, "directory": path.relative_to(lib).as_posix(), "updated_at": stamp()}
    temp = lib / ("当前版本-" + uuid.uuid4().hex + ".tmp")
    save(temp, pointer)
    os.replace(temp, lib / "当前版本.json")
    return pointer


def bootstrap():
    guard_legacy_write(LIB)
    if (LIB / "当前版本.json").exists():
        raise FileExistsError("正式资料库已存在，初始化拒绝覆盖")
    model_file = BASE / "BA-26273833-001/美军模型库/模型清单.json"
    source_dir = BASE / "BA-26273833-009/模型音效浏览库"
    source_file = source_dir / "模型音效目录.json"
    audit_file = source_dir / "引用复核.json"
    # 仅校验所需数据的冻结摘要；正式库不依赖任何旧HTML。
    expected = {r["path"]: r for r in read(source_dir / "输出核验.json")["files"]}
    for name in ("模型音效目录.json", "引用复核.json"):
        check_file({**expected[name], "path": str(source_dir / name)})
    old_input = read(BASE / "BA-26273833-005/输入清单.json")["files"]
    check_file(next(r for r in old_input if Path(r["path"]).resolve() == model_file.resolve()))
    source = read(source_file)
    sources = {"created_at": stamp(), "files": [file_record(p) for p in (model_file, source_file, audit_file)],
               "reference_rows": read(audit_file), "build": "26273833", "unity": "6000.3.24f1", "fmod": "2.01.11"}
    catalog = {"name": "载具正式资料库", "scope": "现有美军模型入口及全部阵营相关声音素材；关联仅采用既有确证关系", "models": [], "audio": [],
               "acceptance": {"registration": "formal", "browser": "pending", "listening": "pending"}}
    routes = {}
    def route(path, frozen):
        record = file_record(path)
        if record["sha256"] != frozen["sha256"] or record["bytes"] != frozen["bytes"]:
            raise ValueError("媒体与来源记录不一致")
        url = "/media/" + record["sha256"] + Path(path).suffix.lower()
        routes[url] = record
        return url
    audio_ids = {}
    for row in source["events"]:
        audio_id = "audio:" + row["guid"] + ":" + token(row["render_profile"])
        audio_ids[row["guid"]] = audio_id
        path = (source_dir / row["audio_path"]).resolve()
        catalog["audio"].append({"id": audio_id, "type": "audio", "name": row["name"], "aliases": [], "category": row["category"], "tags": [row["category"]],
          "quality_status": row["status"], "issue": row["issue"], "event_guid": row["guid"], "event_path": row["path"], "banks": row["banks"],
          "url": route(path, row["check"]), "sha256": row["check"]["sha256"], "bytes": row["check"]["bytes"], "check": row["check"],
          "render_profile": row["render_profile"], "kind": "event_render", "model_ids": ["model:" + id for id in row["model_ids"]],
          "bindings": [{**b, "model_id": "model:" + b["model_id"]} for b in row["bindings"]], "source": row["source"], "extensions": {}})
    source_models = {m["id"]: m for m in source["models"]}
    for model in read(model_file)["entries"]:
        old = source_models[model["name"]]
        files = model["files"]
        urls = {key: route(constrained(model_file.parent, files[key]["path"]), files[key]) for key in ("model.glb", "model.blend", "预览/整体.png")}
        glb_check(constrained(model_file.parent, files["model.glb"]["path"]))
        catalog["models"].append({"id": "model:" + model["name"], "source_id": model["name"], "type": "model", "name": old["name"], "aliases": [],
          "category": old["category"], "tags": ["美军", "空中" if old["category"] == "air" else "地面"], "variant": old["variant"],
          "quality_status": old["model_status"], "model_url": urls["model.glb"], "blend_url": urls["model.blend"], "preview_url": urls["预览/整体.png"],
          "sha256": files["model.glb"]["sha256"], "bytes": files["model.glb"]["bytes"], "audio_ids": [audio_ids[g] for g in old["event_guids"]],
          "reference_counts": old["reference_counts"], "geometry": model.get("geometry", {}), "limitations": model.get("limitations", []),
          "source": {"batch": "BA-26273833-001", "build": "26273833"}, "extensions": {}})
    with write_lock(LIB):
        pointer = publish(catalog, routes, sources)
    return {**pointer, "summary": catalog["summary"], "physical_media_copied": 0}


def normalize(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(value)), flags=re.UNICODE).casefold()


def query(catalog, routes, query="", type="all", id=None, related_to=None, status=None, limit=20, offset=0, paths=True):
    terms = [normalize(t) for t in query.split() if normalize(t)]
    rows = catalog["models"] + catalog["audio"] + catalog.get("scenes", [])
    if related_to:
        parent = next((r for r in rows if r["id"] == related_to), None)
        if not parent:
            raise ValueError("关联查询的资源ID不存在")
        related_ids = parent.get("asset_ids", parent.get("audio_ids", parent.get("model_ids", [])))
        rows = [r for r in rows if r["id"] in related_ids]
    result = []
    for row in rows:
        if type != "all" and row["type"] != type or id and row["id"] != id or status and row["quality_status"] != status:
            continue
        haystack = normalize(" ".join([row["id"], row["name"], *row.get("aliases", []), *row.get("tags", []), row.get("event_path", ""), row.get("event_guid", ""), *row.get("banks", [])]))
        if not all(t in haystack for t in terms):
            continue
        item = deepcopy(row)
        if paths:
            urls = {key: value for key, value in row.items() if key in ("url", "model_url", "preview_url", "blend_url", "scene_url") and value}
            item["local_paths"] = {key: routes[value]["path"] for key, value in urls.items()}
        result.append(item)
    result.sort(key=lambda r: (0 if terms and normalize(r["name"]) == "".join(terms) else 1, r["name"]))
    return {"schema_version": 1, "revision": catalog["revision"], "total": len(result), "offset": offset, "limit": limit, "results": result[offset:offset + limit]}


def import_manifest(manifest_path, publish_changes=False, expected_revision=None, lib=LIB):
    guard_legacy_write(lib)
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    if manifest.get("schema_version") != 1 or not manifest.get("source"):
        raise ValueError("入库清单缺少版本或来源说明")
    pointer, catalog, routes, _ = current(lib)
    if expected_revision is None or expected_revision != pointer["revision"]:
        raise ValueError("须明确当前expected-revision，防止覆盖并发更新")
    catalog, routes = deepcopy(catalog), deepcopy(routes)
    incoming, staged = [], {}
    def add_file(relative, kind, identity, category):
        source = (manifest_path.parent / relative).resolve()
        if not source.is_file() or source.is_symlink():
            raise ValueError("新增资源不存在或为符号链接")
        digest = sha(source)
        ext = source.suffix.lower()
        if kind == "glb": glb_check(source)
        with source.open("rb") as stream: header = stream.read(16)
        if kind == "preview":
            valid = (ext == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")) or (ext in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff")) or (ext == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
            if not valid: raise ValueError("预览图格式或文件头不支持")
        if kind == "blend" and header[:7] != b"BLENDER": raise ValueError("Blender文件头不符")
        if kind == "glb" and ext != ".glb": raise ValueError("模型扩展名应为GLB")
        if kind == "audio" and ext != ".wav": raise ValueError("声音扩展名应为WAV")
        url = "/media/" + digest + ext
        if url not in routes:
            target = resource_target(kind, identity, category, ext, digest, lib)
            if any(Path(record["path"]).resolve() == target for record in routes.values()):
                raise ValueError("分类目录目标已登记其他内容")
            routes[url] = {"path": str(target.resolve()), "bytes": source.stat().st_size, "sha256": digest}
            staged[url] = (source, target)
        return url, digest, source
    for row in manifest.get("models", []):
        model_url, digest, path = add_file(row["model_path"], "glb", row["id"], row["category"])
        preview_url, _, _ = add_file(row["preview_path"], "preview", row["id"], row["category"])
        blend_url = add_file(row["blend_path"], "blend", row["id"], row["category"])[0] if row.get("blend_path") else None
        if row["category"] not in ("air", "ground", "other"): raise ValueError("模型分类不支持")
        incoming.append({"id": row["id"], "type": "model", "source_id": row.get("source_id", row["id"]), "name": row["name"], "category": row["category"],
          "variant": row.get("variant", "待说明配置"), "aliases": row.get("aliases", []), "tags": row.get("tags", []), "quality_status": "pending",
          "model_url": model_url, "preview_url": preview_url, "blend_url": blend_url, "sha256": digest, "bytes": path.stat().st_size,
          "audio_ids": [], "reference_counts": {}, "limitations": ["新增资源待人工外观验收"], "source": manifest["source"], "extensions": row.get("extensions", {})})
    for row in manifest.get("audio", []):
        url, digest, path = add_file(row["audio_path"], "audio", row["id"], row["category"])
        details = wav_check(path)
        incoming.append({"id": row["id"], "type": "audio", "name": row["name"], "category": row["category"], "aliases": row.get("aliases", []),
          "tags": row.get("tags", []), "quality_status": details["technical_status"], "check": details, "sha256": digest, "bytes": path.stat().st_size,
          "url": url, "model_ids": [], "bindings": [], "kind": row.get("kind", "provided_audio"), "render_profile": row.get("render_profile", {}),
          "event_guid": row.get("event_guid", ""), "event_path": row.get("event_path", ""), "banks": [], "issue": "逐条听审待完成",
          "source": manifest["source"], "extensions": row.get("extensions", {})})
    ids = {r["id"] for r in catalog["models"] + catalog["audio"]}
    for row in incoming:
        if not re.fullmatch(r"(model|audio):[A-Za-z0-9_.:-]+", row["id"]) or not row["id"].startswith(row["type"] + ":") or row["id"] in ids:
            raise ValueError("资源ID无效或已存在；补充使用新ID，旧版本不覆盖")
        ids.add(row["id"])
        catalog["models" if row["type"] == "model" else "audio"].append(row)
    models = {r["id"]: r for r in catalog["models"]}; audio = {r["id"]: r for r in catalog["audio"]}
    for binding in manifest.get("bindings", []):
        if not binding.get("evidence"):
            raise ValueError("新增关联必须说明确证来源，不按名称猜配")
        model = models[binding["model_id"]]; sound = audio[binding["audio_id"]]
        if sound["id"] not in model["audio_ids"]: model["audio_ids"].append(sound["id"])
        if model["id"] not in sound["model_ids"]: sound["model_ids"].append(model["id"])
        sound["bindings"].append({"model_id": model["id"], "field": binding["field"], "evidence": binding["evidence"]})
    if not incoming and not manifest.get("bindings"):
        raise ValueError("入库清单没有资源或关联")
    validate_catalog(catalog, routes)
    result = {"status": "validated", "base_revision": pointer["revision"], "new_resources": len(incoming), "new_files": len(staged), "summary": catalog["summary"]}
    if publish_changes:
        with write_lock(lib):
            # 锁内重查版本，再写文件；失败不会切换正式指针。
            if read(lib / "当前版本.json")["revision"] != expected_revision: raise ValueError("版本已变化")
            for url, (source, target) in staged.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    check_file(routes[url])
                else:
                    temporary = target.with_suffix(target.suffix + ".tmp")
                    shutil.copyfile(source, temporary)
                    if sha(temporary) != routes[url]["sha256"]: raise ValueError("复制后摘要不一致")
                    os.replace(temporary, target)
            pointer = publish(catalog, routes, {"manifest": str(manifest_path), "manifest_sha256": sha(manifest_path), "source": manifest["source"], "base_revision": expected_revision}, lib, expected_revision)
        result.update(status="published", revision=pointer["revision"])
    return result
