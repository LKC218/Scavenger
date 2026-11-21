"""按冻结清单复制分类媒体并发布路径版本；原批次和其他任务只读。"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from 资料库核心 import ROOT, LIB, BASE, current, read, save, sha, stamp, check_file, resource_target, write_lock, publish

WORK = ROOT / "local-data/断箭/维护/模型音效资料整理"
PLAN = WORK / "迁移清单.json"
RECEIPT = WORK / "迁移回执.json"
PROTOTYPE = ROOT / "local-data/原型/载具靶场"


def record(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha(path)}


def safe_target(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_relative_to(LIB.resolve()):
        raise ValueError("目标必须在正式资料库内，且不能经过链接")
    return path


def prepare():
    if PLAN.exists(): raise ValueError("迁移清单已冻结，拒绝自动覆盖")
    pointer, catalog, routes, version = current()
    WORK.mkdir(parents=True, exist_ok=True)
    usage = defaultdict(list)
    for model in catalog["models"]:
        for key, kind in (("model_url", "glb"), ("blend_url", "blend"), ("preview_url", "preview")):
            if model.get(key): usage[model[key]].append((kind, model["id"], model["category"]))
    for audio in catalog["audio"]: usage[audio["url"]].append(("audio", audio["id"], audio["category"]))
    planned, targets = [], set()
    for url, old in routes.items():
        rows = usage[url]; kind, identity, category = rows[0]
        if kind == "audio" and len({r[2] for r in rows}) > 1: category = "跨用途共享"
        target = safe_target(resource_target(kind, identity, category, Path(old["path"]).suffix, old["sha256"]))
        if target in targets: raise ValueError("目标路径重叠")
        if target.exists(): raise ValueError("目标已存在，请先核对，不覆盖")
        targets.add(target)
        planned.append({**old, "url": url, "target": str(target), "kind": kind, "resource_ids": [r[1] for r in rows], "category": category})
    sources = read(version / "来源清单.json")
    evidence = []
    for old in sources.get("files", []):
        relative = Path(old["path"]).relative_to(BASE)
        target = safe_target(LIB / "入库记录/初始提取来源" / relative)
        evidence.append({**old, "target": str(target)})
    protection = []
    for path in sorted(PROTOTYPE.glob("*.json")):
        protection.append(record(path))
    plan = {"schema_version": 1, "at": stamp(), "base_revision": pointer["revision"], "pointer_sha256": sha(LIB / "当前版本.json"),
            "previous_version": version.relative_to(LIB).as_posix(), "previous_version_files": [record(p) for p in sorted(version.iterdir()) if p.is_file()],
            "media": planned, "source_files": evidence, "prototype_metadata": protection,
            "media_bytes": sum(r["bytes"] for r in planned), "summary": catalog["summary"],
            "protected_scopes": ["src/载具靶场", "local-data/原型", "local-data/丧尸模型", "reports/丧尸模型", "research/丧尸模型", "全部原提取批次及37份其他清理候选"],
            "write_scopes": ["local-data/断箭/正式资料库/资源", "local-data/断箭/正式资料库/入库记录", "local-data/断箭/正式资料库/版本", "local-data/断箭/正式资料库/当前版本.json", "local-data/断箭/维护/模型音效资料整理"]}
    save(PLAN, plan)
    save(WORK / "迁移清单指纹.json", record(PLAN))
    return {"status": "prepared", "sha256": sha(PLAN), "files": len(planned), "media_bytes": plan["media_bytes"], "source_files": len(evidence)}


def copy_checked(row):
    source = check_file(row)
    target = safe_target(row["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        check_file({**row, "path": str(target)})
        return
    temporary = target.with_name(target.name + ".迁入.tmp")
    with source.open("rb") as src, temporary.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    check_file({**row, "path": str(temporary)})
    os.replace(temporary, target)


def migrate(expected_sha):
    if sha(PLAN) != expected_sha: raise ValueError("清单摘要不符")
    if RECEIPT.exists(): raise ValueError("已有迁移回执，请核对状态，不自动重复发布")
    plan = read(PLAN)
    if sha(LIB / "当前版本.json") != plan["pointer_sha256"]: raise ValueError("正式版本已变化")
    pointer, old_catalog, old_routes, version = current()
    if shutil.disk_usage(LIB).free < plan["media_bytes"] + 1024 ** 3: raise ValueError("迁移可用空间不足")
    for row in plan["previous_version_files"]: check_file(row)
    progress = {"status": "copying", "started_at": stamp(), "manifest_sha256": expected_sha, "copied_files": 0}
    save(WORK / "迁移进度.json", progress)
    for i, row in enumerate(plan["media"] + plan["source_files"], 1):
        copy_checked(row)
        if i % 50 == 0:
            progress["copied_files"] = i; save(WORK / "迁移进度.json", progress)
    routes = {r["url"]: {"path": r["target"], "bytes": r["bytes"], "sha256": r["sha256"]} for r in plan["media"]}
    if set(routes) != set(old_routes): raise ValueError("迁移前后媒体URL集合变化")
    for url, row in routes.items():
        if (row["bytes"], row["sha256"]) != (old_routes[url]["bytes"], old_routes[url]["sha256"]): raise ValueError("媒体身份变化")
    sources = deepcopy(read(version / "来源清单.json"))
    sources["original_files"] = sources.get("files", [])
    sources["files"] = [{"path": r["target"], "bytes": r["bytes"], "sha256": r["sha256"]} for r in plan["source_files"]]
    sources["migration"] = {"manifest": record(PLAN), "previous_revision": pointer["revision"], "previous_version_files": plan["previous_version_files"], "operation": "同内容分类复制，原件保留"}
    with write_lock(LIB):
        if sha(LIB / "当前版本.json") != plan["pointer_sha256"]: raise ValueError("复制期间版本已变化，保留候选不切换")
        new_pointer = publish(deepcopy(old_catalog), routes, sources, expected_revision=plan["base_revision"])
    _, new_catalog, _, _ = current()
    strip = lambda data: {k: v for k, v in data.items() if k not in ("revision", "updated_at")}
    if strip(new_catalog) != strip(old_catalog): raise ValueError("资源、关系或质量状态变化")
    for row in plan["media"] + plan["source_files"]: check_file(row)
    for row in plan["previous_version_files"]: check_file(row)
    # 其他对话可以继续写入自身元数据，记录变化而不恢复或覆盖它。
    concurrent = [r["path"] for r in plan["prototype_metadata"] if not Path(r["path"]).is_file() or sha(r["path"]) != r["sha256"]]
    result = {"status": "passed", "finished_at": stamp(), "manifest_sha256": expected_sha, "from_revision": pointer["revision"],
              "to_revision": new_pointer["revision"], "copied_media": len(routes), "copied_media_bytes": plan["media_bytes"],
              "source_files": len(plan["source_files"]), "catalog_unchanged_except_revision": True, "urls_unchanged": True,
              "original_files_verified": True, "previous_version_verified": True, "prototype_metadata_changed_concurrently": concurrent,
              "other_task_writes": 0, "deleted_files": 0}
    save(RECEIPT, result)
    progress.update(status="passed", copied_files=len(plan["media"]) + len(plan["source_files"])); save(WORK / "迁移进度.json", progress)
    return result


def inventory():
    """历史批次只分类登记，不移动、不删除、不改写冻结文件。"""
    _, catalog, routes, _ = current()
    size_groups = defaultdict(list)
    for url, row in routes.items(): size_groups[row["bytes"]].append((url, row["sha256"]))
    old_current = {r["path"] for r in read(PLAN)["media"]}
    items, duplicate = [], []
    for batch in sorted(BASE.iterdir()):
        if not batch.is_dir(): continue
        for path in sorted(batch.rglob("*")):
            if not path.is_file() or path.is_symlink(): continue
            stat = path.stat(); relative = path.relative_to(batch)
            row = {"path": str(path.resolve()), "batch": batch.name, "relative": relative.as_posix(), "bytes": stat.st_size}
            parts = set(relative.parts)
            if str(path.resolve()) in old_current:
                kind = "正式资产历史来源"
            elif path.suffix.lower() in (".glb", ".blend", ".wav"):
                kind = "提取尝试" if {"尝试", "分工"} & parts else "历史模型音效"
            elif {"日志", "解析缓存", "证据", "原始贴图", "场景数据"} & parts:
                kind = "历史证据与工作数据"
            else: kind = "历史说明与核验"
            row["classification"] = kind
            if path.suffix.lower() in (".glb", ".blend", ".wav", ".png", ".jpg") and stat.st_size in size_groups:
                digest = sha(path)
                matches = [url for url, expected in size_groups[stat.st_size] if digest == expected]
                if matches:
                    row.update(sha256=digest, formal_urls=matches, cleanup_status="保留：历史来源、冻结证据或其他任务可能引用")
                    duplicate.append(row)
            items.append(row)
    summary = {"at": stamp(), "files": len(items), "bytes": sum(r["bytes"] for r in items), "classifications": dict(Counter(r["classification"] for r in items)),
               "same_content_as_formal": len(duplicate), "same_content_bytes": sum(r["bytes"] for r in duplicate),
               "deletion_candidates": 0, "reason": "原批次处于来源保留范围，旧版本及原型仍引用；本次只归类，不把重复等同可删除"}
    save(WORK / "历史文件分类.json", {"summary": summary, "files": items})
    save(WORK / "重复文件保留清单.json", {"summary": summary, "files": duplicate})
    return summary


def verify():
    plan, receipt = read(PLAN), read(RECEIPT)
    if sha(PLAN) != receipt["manifest_sha256"]: raise ValueError("清单发生变化")
    pointer, catalog, routes, version = current()
    if pointer["revision"] != receipt["to_revision"]: raise ValueError("正式版本已另行更新，请独立核验新版本")
    for row in plan["media"] + plan["source_files"]:
        check_file(row); check_file({**row, "path": row["target"]})
    for row in plan["previous_version_files"]: check_file(row)
    assert set(routes) == {r["url"] for r in plan["media"]}
    assert catalog["summary"] == plan["summary"]
    assert all(Path(r["path"]).is_relative_to(LIB / "资源") for r in routes.values())
    original = read(LIB / plan["previous_version"] / "资源索引.json")
    for key in ("models", "audio", "summary"):
        assert catalog[key] == original[key]
    prototype = read(PROTOTYPE / "私有路由.json") if (PROTOTYPE / "私有路由.json").exists() else {}
    for row in prototype.values(): check_file(row)
    result = {"status": "passed", "revision": pointer["revision"], "media": len(routes), "original_and_destination_hashes": "passed",
              "catalog_and_relationships": "unchanged", "previous_version": "unchanged", "prototype_current_routes": len(prototype), "prototype_route_hashes": "passed", "deleted_files": 0}
    save(WORK / "迁移核验.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "migrate", "inventory", "verify"))
    parser.add_argument("--manifest-sha")
    args = parser.parse_args()
    if args.action == "migrate" and not args.manifest_sha: parser.error("迁移须指定冻结清单摘要")
    result = {"prepare": prepare, "migrate": lambda: migrate(args.manifest_sha), "inventory": inventory, "verify": verify}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))
