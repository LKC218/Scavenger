"""旧交付副本的冻结、归档映射、隔离回读及候选生成；没有删除接口。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.request import Request, urlopen

from 资料库核心 import ROOT, BASE, LIB, current, read, save, sha, stamp, check_file, file_record, glb_check, query
from 历史资源解析 import ArchiveResolver, path_key, reference_key, normalized_text

BOUNDARY = ROOT / "local-data/断箭/维护/旧版退役与引用迁移"
DELIVERY = {"美军模型库", "音效配套库", "分类音频", "音效浏览库"}


def run_path(value):
    path = Path(value).resolve()
    if path == BOUNDARY.resolve() or not path.is_relative_to(BOUNDARY.resolve()): raise ValueError("必须使用专属维护子目录")
    return path


def frozen_record(path):
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}


def refresh_active(rows):
    """独立刷新其他任务；只读，允许它们继续修改与资源路径无关的界面。"""
    roots = [ROOT / n for n in ("src/载具靶场", "local-data/原型")]
    snapshots, matched = [], set()
    for start in roots:
        for path in start.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".mjs", ".py", ".js", ".md", ".html", ".csv", ".css"}: continue
            before = path.stat(); raw = path.read_bytes(); after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size): raise ValueError("活动文件读取中变化，稍后重新运行")
            import hashlib
            text = normalized_text(raw.decode("utf-8-sig"))
            snapshots.append({"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "kind": "active_task"})
            for row in rows:
                p = Path(row["path"])
                aliases = [path_key(p), p.relative_to(ROOT).as_posix().casefold(), p.relative_to(BASE).as_posix().casefold()]
                if any(alias in text for alias in aliases): matched.add(path_key(p))
    return snapshots, matched


def prepare(run, audit_dir):
    if run.exists(): raise ValueError("阶段目录已存在，不能覆盖")
    audit_dir = Path(audit_dir).resolve()
    if not audit_dir.is_relative_to((ROOT / "local-data/断箭/维护/重复文件依赖排查").resolve()): raise ValueError("审计目录超出范围")
    data = read(audit_dir / "依赖审计.json")
    source = read(audit_dir / "扫描来源与异常.json")
    if source["errors"] or source["changed"]: raise ValueError("先处理审计异常及并发变化")
    pointer, catalog, routes, version = current()
    if pointer["revision"] != data["summary"]["formal_revision"]: raise ValueError("正式版本变化")
    run.mkdir(parents=True)
    sources = {path_key(r["path"]): r for r in source["files"]}
    active_sources, refreshed_protected = refresh_active(data["files"])
    protected = [r for r in data["files"] if r["status"] == "active_protected" or path_key(r["path"]) in refreshed_protected]
    for row in [*protected, *active_sources]: check_file(row)
    mapped, retained = [], []
    for row in data["files"]:
        if row in protected or row["relative"].split("/")[0] not in DELIVERY:
            retained.append({"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"], "reason": "其他对话保护" if row in protected else "原始导出或提取证据保留"}); continue
        if not row["verified_this_run"]: raise ValueError("待迁移文件摘要未通过")
        refs = []
        for ref in row["references"]:
            src = sources[path_key(ref["source"])]
            if not ref.get("locator") or ref["kind"] == "active_task": raise ValueError("引用没有精确定位或属于其他对话")
            refs.append({**ref, "source_sha256": src["sha256"], "source_encoding": src.get("encoding", "utf-8-sig")})
        target = routes[row["formal_urls"][0]]
        mapped.append({"old": {k: row[k] for k in ("path", "bytes", "sha256")}, "replacement": {**target, "url": row["formal_urls"][0]}, "references": refs})
    manifest = {"schema_version": 1, "status": "prepared_not_deleted", "at": stamp(), "formal_revision": pointer["revision"], "audit_path": str(audit_dir), "audit_sha256": sha(audit_dir / "依赖审计.json"), "protected": [{k: r[k] for k in ("path", "bytes", "sha256")} for r in protected], "files": mapped}
    save(run / "迁移对照清单.json", manifest)
    save(run / "保留清单.json", {"files": retained, "active_sources": active_sources})
    # 全批次及旧快照建立独立基线，后续仅核对，不更新它们的内容。
    evidence = []
    paths = sorted([p for p in BASE.rglob("*") if p.is_file()] + [p for p in (LIB / "版本").rglob("*") if p.is_file()])
    for i, path in enumerate(paths, 1):
        evidence.append(frozen_record(path))
        if i % 1000 == 0: print(f"证据及历史载荷基线 {i}/{len(paths)}", flush=True)
    save(run / "证据保护基线.json", {"files": evidence, "formal_pointer": frozen_record(LIB / "当前版本.json"), "active_sources": active_sources})
    legacy_names = {"生成模型库.py", "生成音效库.py", "生成音效索引.py", "核验音效索引.py", "分类载具音频.py", "复核剩余音效.py", "构建音效浏览库.py", "补提参数音效.py", "构建模型音效视图.py", "核验音效交付.py", "清理资料库旧入口.py", "整理正式资料库.py"}
    ledger = []
    for path in sorted((ROOT / "scripts/断箭").glob("*")):
        if path.suffix not in {".py", ".cjs", ".mjs"}: continue
        status = "archived_command" if path.name in legacy_names or path.name.startswith("验证") else "retained_tool"
        if path.name == "核验音效交付.py": status = "explicit_archive_check"
        if path.name == "预览音效索引.py": status = "explicit_archive_preview"
        ledger.append({"script": str(path), "sha256": sha(path), "status": status, "replacement": "退役旧版资料.py replay" if status in {"archived_command", "explicit_archive_check"} else None})
    save(run / "引用消费者台账.json", {"at": stamp(), "scripts": ledger, "reference_sources": list(sources.values()), "limits": "归档核验接替旧副本完整性检查；不重跑FMOD、重新提取或继承人工听审"})
    save(run / "归档版本登记.json", {"status": "archive_mapping_prepared", "current_revision": pointer["revision"], "archived_revision": 1, "legacy_snapshot_unchanged": True, "manifest_sha256": sha(run / "迁移对照清单.json"), "deletion_performed": False})
    print(json.dumps({"mapped": len(mapped), "bytes": sum(r['old']['bytes'] for r in mapped), "protected": len(protected), "retained": len(retained)}, ensure_ascii=False))


def replay(run, report=None):
    report = Path(report).resolve() if report else run / "隔离回读轨迹.json"
    if not report.is_relative_to(run): raise ValueError("回读报告超出阶段目录")
    if report != run / "隔离回读轨迹.json" and report.exists(): raise ValueError("不能覆盖已有回读报告")
    manifest_path = run / "迁移对照清单.json"
    resolver = ArchiveResolver(manifest_path)
    denied = set(resolver.rows)
    blocked = []
    # 独立进程审计钩子：任何代码读取待删旧文件都会失败，真实文件不改名、不移动。
    def hook(event, args):
        if event == "open" and isinstance(args[0], (str, bytes)):
            if path_key(args[0]) in denied:
                blocked.append(str(args[0])); raise PermissionError("隔离演练禁止读取待删旧资源")
    sys.addaudithook(hook)
    first = next(iter(resolver.rows.values()))["old"]["path"]
    try:
        with Path(first).open("rb"): pass
    except PermissionError: pass
    else: raise ValueError("隔离禁止读取检查失效")
    blocked.clear()
    types = Counter()
    for old, row in resolver.rows.items():
        target = resolver.resolve(row["old"]["path"])
        ext = target.suffix
        if ext == ".glb": glb_check(target)
        elif ext == ".png":
            with target.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n": raise ValueError("PNG头无效")
        elif ext == ".wav":
            import wave
            with wave.open(str(target), "rb") as stream:
                frame_size = stream.getnchannels() * stream.getsampwidth()
                expected = stream.getnframes() * frame_size
                if len(stream.readframes(stream.getnframes())) != expected: raise ValueError("音频截断")
        elif ext != ".blend": raise ValueError("未支持的媒体类型")
        types[ext] += 1
    # 旧样例由受控解析继续服务，同样在禁止读取旧资源的进程内检查媒体路由。
    from 预览音效索引 import create_server
    server = create_server(BASE / "BA-26273833-004", 0, archive_manifest=manifest_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        from urllib.parse import quote
        for url in ["资源/模型/US_A10.glb", "资源/模型/US_AH64D.glb", "资源/模型/US_M1A2.glb"]:
            with urlopen(Request(f"http://127.0.0.1:{server.server_port}/" + quote(url), headers={"Range": "bytes=0-15"}), timeout=15) as response:
                if response.status != 206 or response.read(4) != b"glTF": raise ValueError("归档样例媒体回读失败")
    finally: server.shutdown(); server.server_close(); thread.join()
    if blocked: raise ValueError("回读仍尝试访问旧资源")
    save(report, {"status": "passed", "blocked_probe_passed": True, "unexpected_old_reads": blocked, "types": dict(types), "files": resolver.trace})
    print(json.dumps({"status": "passed", "mapped_reads": len(resolver.rows), "types": dict(types)}, ensure_ascii=False))


def negative_checks(run, manifest):
    from copy import deepcopy
    checks = []
    sample = {**manifest, "files": [deepcopy(manifest["files"][0])]}
    cases = ["missing_mapping", "wrong_target", "missing_target", "changed_source", "protected_overlap"]
    for name in cases:
        value = deepcopy(sample)
        old = value["files"][0]["old"]["path"]
        if name == "wrong_target":
            replacement = next(r["replacement"]["path"] for r in manifest["files"] if r["old"]["sha256"] != value["files"][0]["old"]["sha256"])
            value["files"][0]["replacement"]["path"] = replacement
        elif name == "missing_target": value["files"][0]["replacement"]["path"] = str(LIB / "资源/不存在的测试目标.wav")
        elif name == "changed_source": value["files"][0]["references"][0]["source_sha256"] = "0" * 64
        elif name == "protected_overlap": value["protected"].append(value["files"][0]["old"])
        file = run / ("拒绝检查-" + name + ".json")
        save(file, value)
        try:
            resolver = ArchiveResolver(file)
            resolver.resolve(str(BASE / "未登记路径.wav") if name == "missing_mapping" else old)
        except (OSError, ValueError, KeyError): checks.append({"case": name, "status": "correctly_rejected"})
        else: raise ValueError("未正确拒绝错误归档：" + name)
    save(run / "错误映射拒绝核验.json", {"status": "passed", "checks": checks})


def verify(run):
    if (run / "隔离核验.json").exists(): raise ValueError("已有核验回执，不能覆盖")
    manifest_path = run / "迁移对照清单.json"
    manifest = read(manifest_path)
    negative_checks(run, manifest)
    blender_output = run / "工程外部依赖核验.json"
    blender = read(ROOT / "local-data/断箭/本地配置.json")["tools"]["Blender"]
    with (run / "工程只读核验.log").open("x", encoding="utf-8") as stream:
        subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec", "--python", str(ROOT / "scripts/断箭/核验归档工程.py"), "--", "--manifest", str(manifest_path), "--output", str(blender_output)], stdout=stream, stderr=subprocess.STDOUT, check=True)
    failed = [r for r in read(blender_output)["files"] if r["status"] != "passed"]
    if failed: raise ValueError("有Blender外部依赖，先建立暂缓清单：" + str(len(failed)))
    subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "replay", "--run", str(run)], check=True)
    baseline = read(run / "证据保护基线.json")
    for i, row in enumerate(baseline["files"], 1):
        check_file(row)
        if i % 1500 == 0: print(f"证据结束指纹复核 {i}/{len(baseline['files'])}", flush=True)
    for row in [baseline["formal_pointer"], *manifest["protected"]]: check_file(row)
    active_now, protected_now = refresh_active(read(Path(manifest["audit_path"]) / "依赖审计.json")["files"])
    if protected_now & {path_key(r["old"]["path"]) for r in manifest["files"]}: raise ValueError("发现新的活动任务依赖，须重新准备范围")
    previous = {r["path"]: r["sha256"] for r in baseline["active_sources"]}
    concurrent = [r["path"] for r in active_now if previous.get(r["path"]) != r["sha256"]]
    save(run / "并行任务只读复核.json", {"active_sources": active_now, "concurrent_changes_not_written_by_this_task": concurrent, "new_candidate_dependencies": [], "other_task_files_written": 0})
    _, catalog, routes, version = current()
    for row in routes.values(): check_file(row)
    for row in read(version / "来源清单.json")["files"]: check_file(row)
    if not query(catalog, routes, "A-10", "model")["results"]: raise ValueError("正式检索失败")
    from 启动正式资料库 import create_server
    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for url in ["/", "/app.mjs", *list(routes)[:3]]:
            with urlopen(Request(f"http://127.0.0.1:{server.server_port}" + url, method="HEAD"), timeout=15) as response:
                if response.status != 200: raise ValueError("正式HTTP检查失败")
    finally: server.shutdown(); server.server_close(); thread.join()
    proof = {"status": "passed", "at": stamp(), "manifest_sha256": sha(manifest_path), "verified_paths": sorted(path_key(r["old"]["path"]) for r in manifest["files"]), "evidence_files_unchanged": len(baseline["files"]), "protected_files_unchanged": len(manifest["protected"]), "blender_files": len(read(blender_output)["files"]), "old_reads_blocked_in_child": True, "negative_checks": 5, "formal_pointer_unchanged": True, "deletion_performed": False, "limits": "只读归档完整性及服务检查，不替代模型视觉、逐条听审或原游戏验收"}
    save(run / "隔离核验.json", proof)
    print(json.dumps({k: v for k, v in proof.items() if k != "verified_paths"}, ensure_ascii=False, indent=2))


def candidates(run, audit_dir):
    if (run / "精确删除清单.json").exists(): raise ValueError("候选清单已冻结，不能覆盖")
    resolver = ArchiveResolver(run / "迁移对照清单.json", require_verified=True)
    audit = read(Path(audit_dir) / "依赖审计.json")
    if audit["summary"]["scan_errors"] or audit["summary"]["changed_during_scan"]: raise ValueError("最终审计有未处理异常")
    allowed, deferred = [], []
    for row in audit["files"]:
        if row["status"] != "archive_ready": deferred.append({k: row[k] for k in ("path", "bytes", "sha256", "status", "reasons")}); continue
        resolver.verify_coverage(row["path"], row["references"])
        check_file(row)
        mapped = resolver.rows[path_key(row["path"])]
        allowed.append({"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"], "retained": mapped["replacement"], "reference_count": len(mapped["references"]), "evidence": str(run / "迁移对照清单.json"), "basis": "逐项归档解析、禁止旧路径读取及证据完整性检查通过"})
    baseline = read(run / "证据保护基线.json")
    check_file(baseline["formal_pointer"])
    for i, row in enumerate(baseline["files"], 1):
        check_file(row)
        if i % 2000 == 0: print(f"清单冻结前证据复核 {i}/{len(baseline['files'])}", flush=True)
    for row in resolver.manifest["protected"]: check_file(row)
    _, latest_protected = refresh_active(audit["files"])
    if latest_protected & set(resolver.rows): raise ValueError("最终发现新的其他对话引用，不能冻结候选")
    result = {"schema_version": 1, "status": "awaiting_explicit_delete_approval", "at": stamp(), "count": len(allowed), "bytes": sum(r["bytes"] for r in allowed), "migration_manifest_sha256": sha(run / "迁移对照清单.json"), "audit_path": str(Path(audit_dir).resolve()), "audit_sha256": sha(Path(audit_dir) / "依赖审计.json"), "deletion_performed": False, "files": allowed}
    save(run / "精确删除清单.json", result)
    save(run / "暂缓与保留清单.json", {"count": len(deferred), "files": deferred})
    with (run / "精确删除清单.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["绝对路径", "字节", "SHA256", "正式保留副本", "保留副本SHA256", "引用数量", "处理依据"])
        for row in allowed: writer.writerow([row["path"], row["bytes"], row["sha256"], row["retained"]["path"], row["retained"]["sha256"], row["reference_count"], row["basis"]])
    counts = Counter(); sizes = Counter()
    for row in allowed:
        path = Path(row["path"]); parts = path.relative_to(BASE).parts; group = "/".join(parts[:2])
        counts[group] += 1; sizes[group] += row["bytes"]
    protected = [r for r in deferred if r["status"] == "active_protected"]
    lines = ["# 精确删除清单确认单", "", "## 当前状态", "", "**阶段一至四完成，本轮没有删除文件。以下清单等待工程师另行批准。**", "", f"候选：{len(allowed)}份，逻辑大小{result['bytes']:,}字节，约{result['bytes']/1024**3:.2f} GiB。实际释放空间以未来删除回执为准。", "", f"继续保留{len(deferred)}份，其中其他对话保护{len(protected)}份。原始提取证据、639份原始导出或尝试媒体、历史快照及元数据保持原位。", "", "## 候选分组", "", "| 批次与目录 | 文件数 | 字节 |", "| --- | ---: | ---: |"]
    for group in sorted(counts): lines.append(f"| {group} | {counts[group]} | {sizes[group]:,} |")
    lines += ["", "## 精确清单与摘要", "", "- [逐文件CSV](精确删除清单.csv)：绝对路径、字节、SHA256、正式保留副本及依赖处理依据。", "- [冻结JSON](精确删除清单.json)：供后续按清单执行，包含每份文件的保留目标。", f"- 冻结JSON的SHA256：`{sha(run / '精确删除清单.json')}`。", f"- 迁移对照清单的SHA256：`{result['migration_manifest_sha256']}`。", "- [保留与暂缓清单](暂缓与保留清单.json)、[归档核验回执](隔离核验.json)、[真实回读轨迹](隔离回读轨迹.json)。", "", "## 验证与边界", "", "956份范围按逐记录摘要、字段和正式副本核对；后台Blender只读检查129份工程。独立进程禁止读取候选旧文件，归档回读及004样例媒体检查通过；未创建链接或占位文件。原始批次与版本文件的8526项基线复核通过。", "", "10份已知保护项为靶场运行使用的8份声音，以及冻结输入使用的M1A2、AH64D两份GLB。其资源、路由、派生文件与其他对话源码均不修改。并行任务的合法新改动单独记录，不回滚。", "", "冻结基线同时记录候选旧副本以供删除前核验，不能把基线盘点自身当作运行依赖。未来删除后仅允许清单中的旧副本缺失，其他原始证据仍须摘要一致；归档回读使用保留目标。", "", "原JSON、CSV和V1快照不改字节；新增映射保留原路径事实，显式归档入口读取同摘要正式副本。旧版原路径生成或验证命令按消费者台账归档，不宣称删除后仍能原样运行。归档完整性不替代人工视觉、听审或重提取验收。", "", "本轮未实现删除命令。后续批准应指定此JSON摘要；执行前还必须重新检查其他对话、新增引用、正式版本和所有文件指纹，发现变化即停止，不扩展清单。"]
    (run / "精确删除清单确认单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    save(run / "归档就绪登记.json", {"status": "archived_read_mode_copies_retained", "at": stamp(), "revision": 1, "current_revision": resolver.manifest["formal_revision"], "manifest_sha256": sha(run / "迁移对照清单.json"), "candidate_sha256": sha(run / "精确删除清单.json"), "deletion_performed": False, "legacy_snapshot_bytes_unchanged": True})
    save(run / "清单指纹.json", {n: sha(run / n) for n in ("精确删除清单.json", "精确删除清单.csv", "精确删除清单确认单.md", "迁移对照清单.json", "隔离核验.json", "暂缓与保留清单.json", "归档就绪登记.json")})
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "replay", "candidates"])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--report", type=Path, help="replay写入新的回读报告，不覆盖此前证据")
    args = parser.parse_args(); run = run_path(args.run)
    if args.command in {"prepare", "candidates"} and not args.audit: parser.error("此命令需要--audit")
    if args.command == "prepare": prepare(run, args.audit)
    elif args.command == "verify": verify(run)
    elif args.command == "replay": replay(run, args.report)
    else: candidates(run, args.audit)


if __name__ == "__main__": main()
