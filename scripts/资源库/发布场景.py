"""校验不可变场景包及独立原型，复用维护锁和原子指针切换；保留回滚凭证。"""

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
from urllib.parse import unquote, urlsplit
import uuid

from 统一资源 import ROOT, HOME, read, save, sha

sys.path.insert(0, str(ROOT / "scripts/维护清理"))
from 清理工具 import lock

GATES=("complete", "modular", "web", "library", "ai_visual")


def acceptance_subject(manifest, receipt_key):
    """验收绑定除自身凭证以外的完整文件、资产、场景与包根，避免循环摘要。"""
    return sha_manifest({"schema":manifest["schema"],"package_root":manifest["package_root"],
        "files":{key:row for key,row in manifest["files"].items() if key!=receipt_key},
        "assets":manifest["assets"],"scene":manifest["scene"]})


def validate_acceptance(manifest, package, root):
    acceptance=manifest['acceptance'];binding=acceptance.get('binding',{})
    key=binding.get('receipt')
    if binding.get('schema')!=1 or key not in acceptance['evidence'] or key not in manifest['files']:
        raise ValueError('技术验收没有绑定具体交付集合')
    subject=acceptance_subject(manifest,key)
    if binding.get('subject_sha256')!=subject:
        raise ValueError('技术验收绑定的文件或资产集合已经变化')
    receipt=read(regular_file(package/key,root/'local-data'))
    if receipt.get('schema')!='scene-technical-acceptance-v1' or receipt.get('subject_sha256')!=subject or receipt.get('passed') is not True:
        raise ValueError('技术验收凭证身份或通过状态无效')
    if receipt.get('human_acceptance')!='待人工验收' or any(receipt.get('gates',{}).get(gate) is not True for gate in GATES):
        raise ValueError('技术验收凭证关口或人工状态不符')
    expected={name:{'sha256':manifest['files'][name]['sha256'],'bytes':manifest['files'][name]['bytes']} for name in acceptance['evidence'] if name!=key}
    if not expected:
        raise ValueError('技术验收缺少独立冻结凭证')
    rows=receipt.get('evidence',[])
    actual={row['file']:{'sha256':row['sha256'],'bytes':row['bytes']} for row in rows}
    if len(actual)!=len(rows) or actual!=expected:
        raise ValueError('技术验收引用的凭证集合不一致')
    if any(asset.get('technical_status')!='passed' or asset.get('ai_visual_status')!='passed' for asset in manifest['assets']):
        raise ValueError('独立原型技术或AI视觉状态未通过')


def copy_scene_file(source,target,row,home):
    """先验证临时副本再原子落位；中断副本不会占用最终文件名，允许同清单续跑。"""
    target=Path(target).absolute();home=Path(home).resolve()
    if not target.resolve().is_relative_to(home):
        raise ValueError('场景目标路径越界')
    for parent in target.parents:
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):
            raise ValueError('场景目标目录含链接或目录联接')
        if parent==home:break
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if sha(regular_file(target,home))!=row['sha256'] or target.stat().st_size!=row['bytes']:
            raise ValueError('场景内容地址冲突')
        return
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.writing')
    with source.open('rb') as src,temporary.open('xb') as dst:
        shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
    if temporary.stat().st_size!=row['bytes'] or sha(temporary)!=row['sha256']:
        raise ValueError('场景复制摘要不符')
    os.replace(temporary,target)


def regular_file(path, boundary):
    """拒绝越界、符号链接及任意祖先目录联接。"""
    path, boundary = Path(path).absolute(), Path(boundary).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(boundary):
        raise ValueError("场景文件越界")
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("场景文件路径含链接或目录联接")
        if parent == boundary:
            break
    if not path.is_file():
        raise ValueError("场景文件不存在：" + str(path))
    return resolved


def relative_key(key):
    if not isinstance(key, str) or "\\" in key or any(c in key for c in "?#%:"):
        raise ValueError("包内路径无效")
    value = PurePosixPath(key)
    if value.is_absolute() or not value.parts or any(p in ("..", ".") for p in key.split("/")) or str(value) != key:
        raise ValueError("包内路径必须是规范相对路径")
    return key


def glb_dependencies(path, key):
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("GLB头不完整")
        magic, version, length, size, kind = struct.unpack("<4sIIII", header)
        if magic != b"glTF" or version != 2 or length != path.stat().st_size or kind != 0x4E4F534A or size > length - 20:
            raise ValueError("GLB身份或JSON块无效")
        document = json.loads(stream.read(size))
    result = set()
    for row in [*document.get("images", []), *document.get("buffers", [])]:
        uri = row.get("uri")
        if not uri or uri.startswith("data:"):
            continue
        parsed = urlsplit(uri)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in uri or uri.startswith("/"):
            raise ValueError("GLB不能引用包外资源")
        parts = list(PurePosixPath(key).parent.parts)
        for part in unquote(parsed.path).split("/"):
            if part == "..":
                if not parts:
                    raise ValueError("GLB依赖逃逸场景包")
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        result.add(relative_key("/".join(parts)))
    return result


def validate(manifest, root=ROOT, accepted=False):
    """只读验证；候选可保持待验，正式提交必须具备四项及AI视觉通过凭证。"""
    root = Path(root).resolve()
    if manifest.get("schema") != 1:
        raise ValueError("不支持的场景包契约")
    package = (root / manifest["package_root"]).absolute()
    if not package.resolve().is_relative_to(root / "local-data"):
        raise ValueError("场景包必须位于项目本地数据")
    files = manifest["files"]
    if not files:
        raise ValueError("场景包为空")
    folded = set()
    for key, row in files.items():
        relative_key(key)
        if key.casefold() in folded:
            raise ValueError("包内路径大小写冲突")
        folded.add(key.casefold())
        path = regular_file(package / key, root / "local-data")
        if row["bytes"] != path.stat().st_size or row["sha256"] != sha(path):
            raise ValueError("冻结文件摘要变化：" + key)
        dependencies = row.get("dependencies", [])
        if len(dependencies) != len(set(dependencies)) or key in dependencies or any(dep not in files for dep in dependencies):
            raise ValueError("场景文件依赖未闭合：" + key)
        if path.suffix.lower() == ".glb" and glb_dependencies(path, key) != set(dependencies):
            raise ValueError("GLB真实外部依赖与清单不一致：" + key)
    # 依赖必须有向无环；相对链接工程也使用同一完整包内路径。
    visited, stack = set(), set()
    def visit(key):
        if key in stack:
            raise ValueError("场景依赖形成循环")
        if key in visited:
            return
        stack.add(key)
        for dep in files[key].get("dependencies", []):
            visit(dep)
        stack.remove(key)
        visited.add(key)
    for key in files:
        visit(key)
    assets, scene = manifest["assets"], manifest["scene"]
    entries = [*assets, scene]
    ids = [r["id"] for r in entries]
    if len(ids) != len(set(ids)) or not re.fullmatch(r"scene:[A-Za-z0-9_.-]+", scene["id"]):
        raise ValueError("场景或原型身份重复或无效")
    if len(scene["asset_ids"]) != len(set(scene["asset_ids"])) or set(scene["asset_ids"]) != {r["id"] for r in assets}:
        raise ValueError("场景原型引用未闭合")
    for entry in entries:
        if not entry.get("source_key") or not entry.get("version") or entry.get("human_acceptance") != "待人工验收":
            raise ValueError("不可变来源、版本或人工状态不符合契约")
        required = ["source", "bounds", "conversion", "coverage"] if entry is scene else ["source", "dimensions_m", "anchor", "materials", "collision", "detail_levels"]
        if any(name not in entry for name in required):
            raise ValueError("场景或资产契约字段缺失")
        if not entry["source"].get("usage_scope") or not entry["source"].get("license_status"):
            raise ValueError("来源使用范围或许可核验状态缺失")
        if entry is not scene and (not re.fullmatch(r"model:[A-Za-z0-9_.-]+", entry["id"]) or entry["category"] not in ("building", "decor", "tree", "grass", "assembly")):
            raise ValueError("独立原型身份或分类无效")
        refs = entry["files"]
        required_roles = {"runtime", "blend", "preview"} if entry is not scene else {"manifest", "blend", "preview"}
        if not required_roles.issubset(refs) or any(value not in files for value in refs.values()):
            raise ValueError("资产文件角色缺失或失联")
    acceptance = manifest["acceptance"]
    if acceptance.get("human") != "待人工验收":
        raise ValueError("发布不能升级人工验收状态")
    evidence = acceptance.get("evidence", [])
    if not evidence or any(key not in files for key in evidence):
        raise ValueError("场景缺少冻结验收凭证")
    if accepted and not all(acceptance.get(gate) is True for gate in GATES):
        raise ValueError("完整范围、模块化、网页、入库或AI视觉尚未通过")
    if accepted:
        validate_acceptance(manifest,package,root)
    return {"package": package, "assets": len(assets), "files": len(files), "bytes": sum(r["bytes"] for r in files.values()), "accepted": accepted}


def current_data(home, expected_revision, expected_sha):
    pointer_file = home / "当前版本.json"
    pointer = read(pointer_file)
    if pointer["revision"] != expected_revision or sha(pointer_file) != expected_sha:
        raise ValueError("当前指针已变化，拒绝提交")
    base = (home / pointer["directory"]).resolve()
    if not base.is_relative_to(home):
        raise ValueError("索引目录越界")
    data = {}
    for name, digest in pointer["metadata"].items():
        if Path(name).name != name or sha(regular_file(base / name, home)) != digest:
            raise ValueError("当前元数据摘要变化")
        data[name] = read(base / name)
    return pointer, base, data


def candidate_index(manifest, catalog=None, routes=None, root=ROOT):
    """将真实候选投影为既有查询契约；不写库，也不将未过关状态升级。"""
    checked = validate(manifest, root)
    catalog = copy.deepcopy(catalog) if catalog is not None else {'models': [], 'audio': [], 'scenes': [], 'summary': {}}
    routes = copy.deepcopy(routes) if routes is not None else {}
    identity = sha_manifest(manifest)
    retained = {key: {'path': str(checked['package'] / key), 'bytes': row['bytes'], 'sha256': row['sha256'],
                      'url': '/candidate-media/' + identity + '/' + key} for key, row in manifest['files'].items()}
    existing = {r['id'] for r in catalog['models'] + catalog['audio'] + catalog.get('scenes', [])}
    incoming = {r['id'] for r in [manifest['scene'], *manifest['assets']]}
    if existing & incoming:
        raise ValueError('候选不能遮蔽当前登记ID')
    for row in retained.values():
        routes[row['url']] = {key: value for key, value in row.items() if key != 'url'}
    scene = manifest['scene']
    for asset in manifest['assets']:
        refs = {role: retained[key] for role, key in asset['files'].items()}
        variant = {**refs['runtime'], 'resolution': asset.get('texture_max', 0), 'level': 1,
                   'triangles': asset['triangles'], 'animated': False}
        catalog['models'].append({'id': asset['id'], 'type': 'model', 'asset_type': asset['category'],
            'name': asset['name'], 'source_id': asset['source_key'], 'category': 'other', 'variant': '场景原型候选',
            'aliases': asset.get('aliases', []), 'tags': ['Airbase', asset['category']], 'audio_ids': [], 'quality_status': 'pending',
            'technical_status': asset.get('technical_status', 'pending'), 'ai_visual_status': asset.get('ai_visual_status', 'pending'),
            'human_acceptance': '待人工验收', 'source': asset['source'], 'scene_id': scene['id'],
            'model_url': refs['runtime']['url'], 'blend_url': refs['blend']['url'], 'preview_url': refs['preview']['url'],
            'bytes': refs['runtime']['bytes'], 'sha256': refs['runtime']['sha256'], 'runtime_variants': [variant],
            'geometry': {'triangles': asset['triangles'], 'texture_max': asset.get('texture_max', 0)},
            'extensions': {'scene_asset': asset}})
    refs = {role: retained[key] for role, key in scene['files'].items()}
    catalog.setdefault('scenes', []).append({'id': scene['id'], 'type': 'scene', 'asset_type': 'scene',
        'name': scene['name'], 'source_id': scene['source_key'], 'category': 'scene', 'variant': '场景候选',
        'aliases': [], 'tags': ['Airbase', '场景', '候选'], 'audio_ids': [], 'quality_status': 'pending',
        'technical_status': 'pending', 'ai_visual_status': 'pending', 'human_acceptance': '待人工验收',
        'source': scene['source'], 'asset_ids': scene['asset_ids'], 'scene_url': refs['manifest']['url'],
        'blend_url': refs['blend']['url'], 'preview_url': refs['preview']['url'], 'bytes': checked['bytes'],
        'extensions': {'scene': scene}})
    catalog.update(status='candidate', candidate_sha256=identity, revision=catalog.get('revision', 0))
    catalog['summary'].update(models=len(catalog['models']), audio=len(catalog['audio']), scenes=len(catalog['scenes']),
        model_statuses=dict(Counter(row['quality_status'] for row in catalog['models'])))
    return catalog, routes


def atomic_pointer(path, value, expected_sha):
    temporary = path.parent / ("当前版本." + uuid.uuid4().hex + ".writing.json")
    save(temporary, value)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    if sha(path) != expected_sha:
        raise ValueError("原子切换前指针竞争")
    os.replace(temporary, path)


def publish(manifest, expected_revision, expected_pointer_sha, root=ROOT, home=HOME, guard=lock):
    root, home = Path(root).resolve(), Path(home).resolve()
    with guard():
        pointer, base, data = current_data(home, expected_revision, expected_pointer_sha)
        checked = validate(manifest, root, accepted=True)
        catalog = data["资源索引.json"]
        incoming = {r["id"] for r in [*manifest["assets"], manifest["scene"]]}
        existing = {r["id"] for r in catalog["models"] + catalog["audio"] + catalog.get("scenes", [])}
        if incoming & existing:
            raise ValueError("不可覆盖已经登记的场景或原型ID")
        identity = sha_manifest(manifest)
        target_root = home / "当前资源/场景" / manifest["scene"]["id"].split(":")[1] / identity
        routes = data["资源路由.json"]
        prefix = "/scene-media/" + identity + "/"
        retained = {}
        # 保持包内相对路径，使外置GLB贴图和Blender链接工程均可独立收集。
        for index,(key, row) in enumerate(manifest["files"].items(),1):
            source = regular_file(checked["package"] / key, root / "local-data")
            target = target_root / key
            copy_scene_file(source,target,row,home)
            if index%512==0:print(json.dumps({'phase':'复制场景','done':index,'total':len(manifest['files'])},ensure_ascii=False),flush=True)
            url = prefix + key
            retained[key] = {"path": target.relative_to(root).as_posix(), "bytes": row["bytes"], "sha256": row["sha256"], "url": url}
            if url in routes and routes[url] != {k: v for k, v in retained[key].items() if k != "url"}:
                raise ValueError("场景路由身份冲突")
            routes[url] = {k: v for k, v in retained[key].items() if k != "url"}
        scenes = data.setdefault("场景登记.json", {})
        scene = manifest["scene"]
        for asset in manifest["assets"]:
            refs = asset["files"]
            runtime, blend, preview = (retained[refs[key]] for key in ("runtime", "blend", "preview"))
            variant = {**runtime, "resolution": asset.get("texture_max", 256), "level": 1, "animated": False,
                       "triangles": asset["triangles"], "before_triangles": asset.get("source_triangles", asset["triangles"])}
            card = {"id": asset["id"], "type": "model", "asset_type": asset["category"], "name": asset["name"],
                    "source_id": asset["source_key"], "category": "other", "aliases": asset.get("aliases", []),
                    "tags": ["Airbase", asset["category"]], "audio_ids": [], "quality_status": "pending",
                    "technical_status": "passed", "ai_visual_status": "passed", "human_acceptance": "待人工验收",
                    "model_url": runtime["url"], "preview_url": preview["url"], "blend_url": blend["url"],
                    "bytes": runtime["bytes"], "sha256": runtime["sha256"], "source": asset["source"],
                    "geometry": {"triangles": asset["triangles"], "texture_max": variant["resolution"]},
                    "runtime_variants": [variant], "scene_id": scene["id"], "extensions": {"scene_asset": asset}}
            catalog["models"].append(card)
            # 用不可变ID作登记键；同一GLB可服务不同来源身份。
            data["模型登记.json"][asset["id"]] = {"id": asset["id"], "name": asset["name"], "kind": "model",
                "source_sha256": runtime["sha256"], "variants": [variant], "default": runtime["sha256"], "provenance": asset["source"]}
            data["主工程.json"][asset["id"]] = blend
        refs = scene["files"]
        record = {**scene, "retained_files": retained, "package_sha256": identity, "acceptance": manifest["acceptance"],
                  "package_dependencies": {key: row.get("dependencies", []) for key, row in manifest["files"].items()}}
        scenes[scene["id"]] = record
        catalog.setdefault("scenes", []).append({"id": scene["id"], "type": "scene", "asset_type": "scene", "name": scene["name"],
            "source_id": scene["source_key"], "category": "other", "aliases": [], "tags": ["Airbase", "全图", "场景"],
            "quality_status": "pending", "technical_status": "passed", "ai_visual_status": "passed", "human_acceptance": "待人工验收",
            "audio_ids": [], "preview_url": retained[refs["preview"]]["url"], "blend_url": retained[refs["blend"]]["url"],
            "scene_url": retained[refs["manifest"]]["url"], "bytes": checked["bytes"], "source": scene["source"], "asset_ids": scene["asset_ids"]})
        revision = expected_revision + 1
        catalog["revision"] = revision
        catalog["summary"].update(models=len(catalog["models"]), scenes=len(catalog["scenes"]),
            model_statuses=dict(Counter(r["quality_status"] for r in catalog["models"])))
        data["主工程.json"][scene["id"]] = {**retained[refs["blend"]], "package_sha256": identity,
                                                 "dependencies": manifest["files"][refs["blend"]].get("dependencies", [])}
        if "保留策略.json" in data:
            data["保留策略.json"]["masters"] = len(data["主工程.json"])
        changed = {"资源索引.json", "资源路由.json", "模型登记.json", "主工程.json", "场景登记.json", "保留策略.json"}
        directory = "索引版本/" + f"{revision:03}-场景-{uuid.uuid4().hex[:12]}"
        version = home / directory
        version.mkdir(parents=True, exist_ok=False)
        for name in data:
            if name in changed:
                save(version / name, data[name])
            else:
                shutil.copyfile(base / name, version / name)
        new_pointer = {**pointer, "revision": revision, "directory": directory,
                       "metadata": {name: sha(version / name) for name in data}}
        receipt_dir = home / "验收" / ("场景发布-" + identity[:12] + "-" + uuid.uuid4().hex[:8])
        receipt_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(home / "当前版本.json", receipt_dir / "原指针.json")
        shutil.copyfile(home / "当前版本.json", receipt_dir / "切换失败回退指针.json")
        save(receipt_dir/'计划新指针.json',new_pointer)
        result = {"status": "准备切换", "previous_pointer_sha256": expected_pointer_sha, "pointer": new_pointer,
                  "manifest_sha256": identity, "scene_id": scene["id"], "assets_added": len(manifest["assets"]),
                  "receipt_directory": str(receipt_dir), "deleted": 0, "human_acceptance": "待人工验收"}
        result['pointer_sha256']=sha(receipt_dir/'计划新指针.json')
        save(receipt_dir / "发布回执.json", result)
        atomic_pointer(home / "当前版本.json", new_pointer, expected_pointer_sha)
        result.update(status="已提交", pointer_sha256=sha(home / "当前版本.json"))
        try:
            save(receipt_dir / "发布回执.json", result)
        except OSError:
            if sha(home/'当前版本.json')==result['pointer_sha256']:
                os.replace(receipt_dir/'切换失败回退指针.json',home/'当前版本.json')
            raise
        return result


def sha_manifest(manifest):
    import hashlib
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def rollback(receipt_directory, expected_pointer_sha, home=HOME, guard=lock):
    home, receipt_directory = Path(home).resolve(), Path(receipt_directory).resolve()
    with guard():
        receipt = read(regular_file(receipt_directory / "发布回执.json", home))
        if receipt.get("status") not in ("已提交","准备切换") or receipt["pointer_sha256"] != expected_pointer_sha:
            raise ValueError("回滚凭证不是当前提交")
        if sha(home/'当前版本.json')!=expected_pointer_sha:
            raise ValueError('当前指针已有后续变化，拒绝过期回滚')
        planned=receipt_directory/'计划新指针.json'
        if planned.exists():
            if sha(regular_file(planned,home))!=expected_pointer_sha:
                raise ValueError('计划新指针摘要变化')
        elif receipt['status']!='已提交':
            raise ValueError('准备状态缺少确切计划新指针')
        previous = regular_file(receipt_directory / "原指针.json", home)
        if sha(previous) != receipt["previous_pointer_sha256"]:
            raise ValueError("回滚原指针摘要变化")
        pointer = read(previous)
        # 旧快照必须仍然完整；发生新发布时不能覆盖新指针。
        for name, digest in pointer["metadata"].items():
            if Path(name).name != name or sha(regular_file(home / pointer["directory"] / name, home)) != digest:
                raise ValueError("旧快照已变化，不能回滚")
        atomic_pointer(home / "当前版本.json", pointer, expected_pointer_sha)
        result = {"status": "已回滚", "pointer_sha256": sha(home / "当前版本.json"), "revision": pointer["revision"], "deleted": 0}
        save(receipt_directory / "回滚回执.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["validate", "publish", "rollback"])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--expected-pointer-sha")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.operation == "rollback":
        result = rollback(args.receipt, args.expected_pointer_sha)
    elif args.operation == "publish":
        result = publish(read(args.manifest), args.expected_revision, args.expected_pointer_sha)
    else:
        result = validate(read(args.manifest))
        result["package"] = str(result["package"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
