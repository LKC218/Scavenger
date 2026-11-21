"""只读排查旧媒体引用，输出精确候选及保留清单；不提供删除功能。"""
import argparse
from collections import Counter, defaultdict
import csv
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote

from 资料库核心 import ROOT, LIB, BASE, current, read, save, sha, stamp, check_file

INPUT = ROOT / "local-data/断箭/维护/模型音效资料整理/重复文件保留清单.json"
TEXT = {".json", ".jsonl", ".csv", ".md", ".txt", ".log", ".py", ".mjs", ".cjs", ".js", ".html", ".css", ".cmd", ".yaml", ".yml"}
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}
DIR_FIELDS = {"source_output", "directory", "output_dir", "output_root", "source_dir", "source_root", "output_path", "output_directory", "scene_root"}
AUDIT_DIR = ROOT / "local-data/断箭/维护/重复文件依赖排查"
# 自己的分析清单只说明文件存在，不算消费者，否则会让全部文件循环自证依赖。
INVENTORY_DIR = ROOT / "local-data/断箭/维护/模型音效资料整理"
RETIRE_DIR = ROOT / "local-data/断箭/维护/旧版退役与引用迁移"


@lru_cache(maxsize=200000)
def canonical(path):
    return str(Path(path).resolve()).replace("\\", "/").casefold()


@lru_cache(maxsize=200000)
def resolve_target(base, value):
    target = (base / value).resolve()
    return target, str(target).replace("\\", "/").casefold()


def audit(output, archive_manifest=None, refresh_from=None):
    output = Path(output).absolute()
    if output.resolve() != output or not output.is_relative_to(AUDIT_DIR.resolve()): raise ValueError("审计输出须在专属维护目录内且不能经过链接")
    output.mkdir(parents=True, exist_ok=False)
    pointer, catalog, routes, _ = current()
    frozen_pointer = sha(LIB / "当前版本.json")
    items = read(INPUT)["files"]
    index = {canonical(r["path"]): i for i, r in enumerate(items)}
    refs = [dict() for _ in items]
    dir_index = defaultdict(set)
    for i, row in enumerate(items):
        path = Path(row["path"])
        for parent in path.parents:
            if parent == BASE: break
            dir_index[canonical(parent)].add(i)
    current_paths = {canonical(r["path"]) for r in routes.values()}
    source_records, changed, errors = [], [], []
    excluded = []
    def add(i, owner, field, method, value="", locator=None):
        key = (str(owner), field, method, json.dumps(locator, ensure_ascii=False))
        refs[i][key] = {"source": str(owner), "field": field, "method": method, "value": str(value), "locator": locator}

    # 摘要复核包括替代正式媒体，不能仅凭上轮盘点声称本轮仍相同。
    checked_targets = set()
    valid = []
    for i, row in enumerate(items):
        try:
            check_file(row)
            targets = []
            for url in row["formal_urls"]:
                target = routes[url]
                if target["sha256"] != row["sha256"] or target["bytes"] != row["bytes"]: raise ValueError("替代媒体身份变化")
                if target["path"] not in checked_targets:
                    check_file(target); checked_targets.add(target["path"])
                targets.append(target)
            valid.append(True)
        except (OSError, ValueError, KeyError) as error:
            valid.append(False); errors.append({"candidate": row["path"], "error": str(error)})
        if (i + 1) % 200 == 0: print(f"已复核相同内容文件 {i + 1}/{len(items)}", flush=True)

    def owner_kind(path):
        if path.is_relative_to(ROOT / "local-data/原型") or path.is_relative_to(ROOT / "src/载具靶场"):
            return "active_task"
        if path.is_relative_to(LIB / "版本") and path.parent.name != f"v{pointer['revision']:04d}": return "old_version"
        if path.is_relative_to(BASE) or path.is_relative_to(LIB / "入库记录"):
            return "historical_evidence"
        if path.is_relative_to(LIB) or path.is_relative_to(ROOT / "src") or path.is_relative_to(ROOT / "scripts"):
            return "maintained_code_or_library"
        if path.is_relative_to(ROOT / "local-data/断箭/维护"):
            return "maintenance_receipt"
        if path.is_relative_to(ROOT / "tmp"): return "temporary_reference"
        return "documentation"

    roots = [ROOT / n for n in ("src", "scripts", "docs", "reports", "research", "local-data/断箭", "local-data/原型", "tmp")]
    documents = [ROOT / n for n in ("README.md", "AGENTS.md", "打开前线资产库.cmd", "打开载具靶场.cmd") if (ROOT / n).is_file()]
    for start in roots:
        for folder, dirs, files in os.walk(start):
            parent = Path(folder)
            dirs[:] = [d for d in dirs if d not in SKIP and not (parent / d).is_symlink()
                       and not (parent / d).is_relative_to(AUDIT_DIR) and not (parent / d).is_relative_to(INVENTORY_DIR)
                       and not (parent / d).is_relative_to(RETIRE_DIR)
                       and not (parent.is_relative_to(ROOT / "tmp") and d == "无头浏览器")]
            for name in files:
                path = parent / name
                if path.suffix.lower() in TEXT and not path.is_symlink(): documents.append(path)
    documents = sorted(set(documents))
    directory_hints = []
    def resolve_value(value, owner, bases, field, directory=False, locator=None):
        if not isinstance(value, str) or not value or len(value) > 2000 or "\n" in value: return []
        value = unquote(value).strip().removeprefix("file:///")
        if "\x00" in value or re.match(r"^(https?|event|app|codex|data):", value): return []
        looks_path = "/" in value or "\\" in value or Path(value).suffix.lower() in {".glb", ".blend", ".wav", ".png", ".jpg"}
        if not looks_path and not directory: return []
        discovered = []
        for base in dict.fromkeys([*bases, ROOT]):
            try:
                target, key = resolve_target(base, value.replace("\\", "/"))
                if not target.is_relative_to(ROOT): continue
            except (OSError, ValueError): continue
            if key in index: add(index[key], owner, field, "resolved_path", value, locator)
            if directory and key in dir_index and value not in (".", "..", ""):
                for i in dir_index[key]: add(i, owner, field, "resolved_directory", value, locator)
                discovered.append(target)
        return discovered

    def walk(node, owner, bases, field="$", depth=0, tokens=()):
        if depth > 80: raise ValueError("JSON嵌套过深，未确认扫描完整")
        if isinstance(node, dict):
            extra = []
            for key, value in node.items():
                if key in DIR_FIELDS and isinstance(value, str): extra += resolve_value(value, owner, bases, field + "." + key, True, {"type": "json_value", "tokens": [*tokens, key]})
            nested = list(dict.fromkeys([*extra, *bases]))
            for key, value in node.items():
                resolve_value(key, owner, nested, field + ".<key>", locator={"type": "json_key", "tokens": list(tokens), "key": key})
                walk(value, owner, nested, field + "." + key, depth + 1, (*tokens, key))
        elif isinstance(node, list):
            for i, value in enumerate(node): walk(value, owner, bases, field + f"[{i}]", depth + 1, (*tokens, i))
        elif isinstance(node, str): resolve_value(node, owner, bases, field, locator={"type": "json_value", "tokens": list(tokens)})

    # 对未能静态解析的文本路径，保守记录为需复核，不据此放行删除。
    aliases = defaultdict(set)
    for i, row in enumerate(items):
        path = Path(row["path"])
        aliases[canonical(path)].add(i)
        aliases[path.relative_to(ROOT).as_posix().casefold()].add(i)
        aliases[path.relative_to(BASE).as_posix().casefold()].add(i)
        aliases[row["relative"].casefold()].add(i)
    def scan_document(owner):
        try:
            before = owner.stat()
            raw = owner.read_bytes()
            if before.st_size != owner.stat().st_size or before.st_mtime_ns != owner.stat().st_mtime_ns:
                changed.append(str(owner)); return
            encoding = "utf-8-sig"
            try: text = raw.decode(encoding)
            except UnicodeError:
                if owner.suffix.lower() not in {".log", ".txt", ".cmd"}: raise
                encoding = "gb18030"; text = raw.decode(encoding)
            source_records.append({"path": str(owner), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "kind": owner_kind(owner), "encoding": encoding})
            bases = [owner.parent, *[p for p in owner.parents if p.is_relative_to(ROOT)]]
            if owner.suffix.lower() == ".json":
                walk(json.loads(text), owner, bases)
            elif owner.suffix.lower() == ".csv":
                import io
                for n, row in enumerate(csv.reader(io.StringIO(text))):
                    for c, value in enumerate(row): resolve_value(value, owner, bases, f"CSV[{n},{c}]", locator={"type": "csv", "row": n, "column": c})
            else:
                normalized = text.casefold()
                for _ in range(3): normalized = normalized.replace("\\\\", "\\")
                normalized = normalized.replace("\\", "/")
                for alias, ids in aliases.items():
                    if alias in normalized:
                        for i in ids: add(i, owner, "文本路径", "text_path_hint", alias, {"type": "text_alias"})
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            # 二进制日志或无法解析资料影响保守判定，不能默默忽略。
            errors.append({"source": str(owner), "error": str(error)[:500]})
    previous_sources, previous_refs = {}, defaultdict(list)
    reused = 0
    if refresh_from:
        previous_dir = Path(refresh_from).resolve()
        if not previous_dir.is_relative_to(AUDIT_DIR.resolve()): raise ValueError("增量基线不在审计目录")
        for name, digest in read(previous_dir / "清单指纹.json").items():
            if sha(previous_dir / name) != digest: raise ValueError("增量审计基线指纹不一致")
        previous = read(previous_dir / "依赖审计.json")
        previous_scan = read(previous_dir / "扫描来源与异常.json")
        if previous["summary"]["formal_revision"] != pointer["revision"] or {canonical(r["path"]) for r in previous["files"]} != set(index):
            raise ValueError("增量基线版本或资源范围变化")
        unstable = set(previous_scan["changed"]) | {r.get("source") for r in previous_scan["errors"]}
        previous_sources = {r["path"]: r for r in previous_scan["files"] if r["path"] not in unstable}
        for row in previous["files"]:
            i = index[canonical(row["path"])]
            for ref in row["references"]: previous_refs[ref["source"]].append((i, ref))
    for document_number, owner in enumerate(documents, 1):
        if document_number % 250 == 0: print(f"已扫描依赖文本 {document_number}/{len(documents)}", flush=True)
        prior = previous_sources.get(str(owner))
        if prior:
            before = owner.stat()
            digest = sha(owner)
            after = owner.stat()
            if digest == prior["sha256"] and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                source_records.append({**prior, "kind": owner_kind(owner)})
                for i, ref in previous_refs[str(owner)]:
                    add(i, owner, ref["field"], ref["method"], ref["value"], ref["locator"])
                reused += 1
                continue
        scan_document(owner)
    # 活动验证报告可能持续生成，补扫发生变化及新增的活动记录；不改写其他任务。
    concurrent_rescans = []
    active_owners = {p for p in documents if owner_kind(p) == "active_task"}
    for start in (ROOT / "src/载具靶场", ROOT / "local-data/原型"):
        for folder, dirs, files in os.walk(start):
            dirs[:] = [d for d in dirs if d not in SKIP and not (Path(folder) / d).is_symlink()]
            active_owners.update(Path(folder) / name for name in files if Path(name).suffix.lower() in TEXT)
    for attempt in range(3):
        snapshots = {r["path"]: r for r in source_records}
        pending = [p for p in active_owners if not p.is_file() or str(p) not in snapshots or sha(p) != snapshots[str(p)]["sha256"]]
        if not pending: break
        for owner in pending:
            if not owner.is_file():
                changed.append(str(owner)); continue
            previous = snapshots.get(str(owner), {}).get("sha256")
            for incoming in refs:
                for key in [k for k in incoming if k[0] == str(owner)]: del incoming[key]
            source_records[:] = [r for r in source_records if r["path"] != str(owner)]
            changed[:] = [p for p in changed if p != str(owner)]
            errors[:] = [r for r in errors if r.get("source") != str(owner)]
            scan_document(owner)
            concurrent_rescans.append({"path": str(owner), "previous_sha256": previous, "attempt": attempt + 1})
    snapshots = {r["path"]: r for r in source_records}
    for owner in active_owners:
        if not owner.is_file() or str(owner) not in snapshots or sha(owner) != snapshots[str(owner)]["sha256"]: changed.append(str(owner))
    results = []
    resolver = None
    if archive_manifest:
        from 历史资源解析 import ArchiveResolver
        resolver = ArchiveResolver(archive_manifest, require_verified=True)
    for i, original in enumerate(items):
        incoming = list(refs[i].values())
        for ref in incoming: ref["kind"] = owner_kind(Path(ref["source"]))
        reasons = []
        if canonical(original["path"]) in current_paths: reasons.append("当前正式版本仍直接引用")
        if any(r["kind"] == "active_task" for r in incoming): reasons.append("其他对话原型或输入记录仍引用")
        if any(r["kind"] == "old_version" for r in incoming): reasons.append("旧版本快照回读依赖")
        if any(r["kind"] == "historical_evidence" for r in incoming): reasons.append("历史来源或冻结核验仍引用")
        if any(r["kind"] == "maintained_code_or_library" for r in incoming): reasons.append("维护脚本或资料库来源仍引用")
        if incoming and not reasons: reasons.append("文档、临时记录或维护回执含路径线索")
        if not valid[i]: reasons.append("本轮文件或替代副本摘要未通过")
        # 未解析二进制依赖不冒称安全；只有无引用、非证据区的WAV才可能静态放行。
        if not incoming and Path(original["path"]).suffix.lower() != ".wav": reasons.append("模型或图像的二进制内部依赖仍需复核")
        if not incoming and ({"证据", "尝试", "分工"} & set(Path(original["relative"]).parts)):
            reasons.append("提取或复核证据保留范围")
        if not incoming and (errors or changed): reasons.append("存在扫描未解析或并发变化，不能确认无引用")
        status = "active_protected" if any(r["kind"] == "active_task" for r in incoming) else "retain_referenced" if incoming else "manual_review" if reasons else "safe_candidate"
        if resolver and resolver.has(original["path"]) and status != "active_protected":
            try:
                if errors or changed or not valid[i]: raise ValueError("扫描或摘要未通过")
                resolver.verify_coverage(original["path"], incoming)
                status = "archive_ready"
                reasons = ["原始引用逐项登记，已由受控归档解析回读；等待删除批准"]
            except (OSError, ValueError, KeyError) as error:
                reasons.append("归档候选暂缓：" + str(error))
        results.append({**original, "status": status, "reasons": reasons, "references": incoming,
                        "replacement": [routes[u] for u in original["formal_urls"] if u in routes], "verified_this_run": valid[i]})
    safe = [r for r in results if r["status"] in {"safe_candidate", "archive_ready"}]
    summary = {"at": stamp(), "formal_revision": pointer["revision"], "duplicate_files": len(results), "duplicate_bytes": sum(r["bytes"] for r in results),
               "statuses": dict(Counter(r["status"] for r in results)), "safe_candidate_count": len(safe), "safe_candidate_bytes": sum(r["bytes"] for r in safe),
               "scanned_text_files": len(source_records), "reused_after_fingerprint_check": reused, "scan_errors": len(errors), "changed_during_scan": len(set(changed)), "deletion_performed": False,
               "limits": ["只读静态依赖审计，不修改旧快照、来源或其他任务", "Blender二进制及运行时动态路径未穷尽解析，无显式引用的模型图像仍不放行", "仅针对既有1605份相同内容文件，不扩展到未知新资产", "清单是扫描时点结果，任何后续删除必须重新验证活动依赖和指纹"]}
    if sha(LIB / "当前版本.json") != frozen_pointer: raise ValueError("扫描期间正式索引变化，请重新审计")
    save(output / "依赖审计.json", {"summary": summary, "files": results})
    save(output / "可安全删除候选.json", {"summary": summary, "files": safe})
    save(output / "扫描来源与异常.json", {"files": source_records, "errors": errors, "changed": sorted(set(changed)), "concurrent_rescans": concurrent_rescans, "excluded": [str(AUDIT_DIR), str(INVENTORY_DIR), str(RETIRE_DIR), "tmp内无头浏览器配置与LevelDB缓存", "第三方工具、缓存、原游戏与二进制资产正文"], "other_task_files_written": 0})
    with (output / "文件依赖清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["绝对路径", "字节", "SHA256", "判定", "原因", "引用数量", "引用示例", "正式保留副本"])
        for row in results:
            writer.writerow([row["path"], row["bytes"], row["sha256"], row["status"], "；".join(row["reasons"]), len(row["references"]),
                             "；".join(r["source"] + " " + r["field"] for r in row["references"][:3]), row["replacement"][0]["path"] if row["replacement"] else ""])
    save(output / "清单指纹.json", {name: sha(output / name) for name in ["依赖审计.json", "可安全删除候选.json", "文件依赖清单.csv", "扫描来源与异常.json"]})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="专属审计目录下未使用的新目录")
    parser.add_argument("--archive-manifest", type=Path, help="显式使用已通过隔离核验的归档清单，逐项核对覆盖")
    parser.add_argument("--refresh-from", type=Path, help="校验全部来源摘要，仅复用内容未变文件的路径解析结果；新改文件补扫")
    args = parser.parse_args()
    audit(args.output, args.archive_manifest, args.refresh_from)
