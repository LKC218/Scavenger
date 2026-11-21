"""只执行工程师已批准的956份冻结媒体清单，不删除目录、不扩展范围。"""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
from urllib.request import Request, urlopen

from 资料库核心 import ROOT, BASE, LIB, read, save, sha, stamp, check_file, current
from 历史资源解析 import ArchiveResolver, path_key
from 退役旧版资料 import run_path, refresh_active

APPROVED_SHA = "816e7e61e40249dec0048995ffca11be53b055538e8b6169338963d1e26ddfe8"
APPROVED_COUNT = 956
APPROVED_BYTES = 7374624439
ALLOWED = {("BA-26273833-001", "美军模型库"), ("BA-26273833-003", "音效配套库"), ("BA-26273833-005", "分类音频"), ("BA-26273833-007", "音效浏览库")}


def checked_path(row):
    path = Path(row["path"])
    absolute = path.absolute()
    resolved = path.resolve()
    if not path.is_absolute() or absolute != resolved or not resolved.is_relative_to(BASE.resolve()): raise ValueError("路径解析超出批准批次边界")
    parts = resolved.relative_to(BASE.resolve()).parts
    if len(parts) < 3 or tuple(parts[:2]) not in ALLOWED or resolved.suffix.lower() not in {".blend", ".glb", ".png", ".wav"}:
        raise ValueError("不在批准的四类媒体目录")
    for component in [path, *path.parents]:
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()): raise ValueError("拒绝经过链接的删除路径")
        if component == ROOT: break
    if not stat.S_ISREG(path.stat().st_mode): raise ValueError("候选不是普通文件")
    return resolved


def approved(run, expected):
    manifest = run / "精确删除清单.json"
    if expected != APPROVED_SHA or sha(manifest) != APPROVED_SHA: raise ValueError("未匹配工程师批准的冻结摘要")
    data = read(manifest)
    rows = data["files"]
    if data["count"] != APPROVED_COUNT or len(rows) != APPROVED_COUNT or data["bytes"] != APPROVED_BYTES or sum(r["bytes"] for r in rows) != APPROVED_BYTES:
        raise ValueError("冻结数量或字节发生变化")
    if len({path_key(r["path"]) for r in rows}) != APPROVED_COUNT: raise ValueError("冻结清单存在重复路径")
    for name, digest in read(run / "清单指纹.json").items():
        if sha(run / name) != digest: raise ValueError("冻结文件指纹变化：" + name)
    return data


def scope_checks(data, resolver):
    keys = {path_key(r["path"]) for r in data["files"]}
    protected = {path_key(r["path"]) for r in resolver.manifest["protected"]}
    targets = {path_key(r["retained"]["path"]) for r in data["files"]}
    if keys & (protected | targets) or keys != set(resolver.rows): raise ValueError("范围与保护或映射冲突")
    for row in data["files"]:
        mapped = resolver.rows[path_key(row["path"])]
        if any(row[k] != mapped["old"][k] for k in ("bytes", "sha256")) or row["retained"] != mapped["replacement"]:
            raise ValueError("冻结文件与归档映射身份不一致")


def active_guard(audit, keys):
    sources, matched = refresh_active(audit["files"])
    if matched & keys: raise ValueError("发现其他对话的新使用引用，停止删除")
    return sources


def execute(run, audit_dir, expected):
    data = approved(run, expected)
    out = run / "删除执行001"
    if out.exists(): raise ValueError("执行目录已存在，禁止重复执行或覆盖回执")
    audit_dir = Path(audit_dir).resolve()
    if not audit_dir.is_relative_to((ROOT / "local-data/断箭/维护/重复文件依赖排查").resolve()): raise ValueError("引用审计不在专属目录")
    audit = read(audit_dir / "依赖审计.json")
    sources = read(audit_dir / "扫描来源与异常.json")
    if sources["errors"] or sources["changed"]: raise ValueError("最新引用审计不稳定，尚不执行删除")
    keys = {path_key(r["path"]) for r in data["files"]}
    fresh = {path_key(r["path"]): r for r in audit["files"]}
    if not keys <= fresh.keys() or any(fresh[k]["status"] != "archive_ready" for k in keys): raise ValueError("批准文件出现新增依赖或保护变化")
    resolver = ArchiveResolver(run / "迁移对照清单.json", require_verified=True)
    scope_checks(data, resolver)
    baseline = read(run / "证据保护基线.json")
    check_file(baseline["formal_pointer"])
    pointer, catalog, routes, version = current()
    if pointer["revision"] != resolver.manifest["formal_revision"]: raise ValueError("正式版本变化")
    # 先完成整批校验；任何失败均发生在第一份删除之前。
    identities = {}
    for row in data["files"]:
        path = checked_path(row)
        check_file(row)
        resolver.verify_coverage(row["path"], fresh[path_key(path)]["references"])
        st = path.stat(); identities[path_key(path)] = (st.st_size, st.st_mtime_ns, st.st_ino)
    for i, row in enumerate(baseline["files"], 1):
        check_file(row)
        if i % 2000 == 0: print(f"删除前证据复核 {i}/{len(baseline['files'])}", flush=True)
    for row in routes.values(): check_file(row)
    for row in read(version / "来源清单.json")["files"]: check_file(row)
    for row in resolver.manifest["protected"]: check_file(row)
    # 扫描后所有非活动来源再校验一次，避免新修改绕过完整引用审计。
    for row in sources["files"]:
        if row["kind"] != "active_task": check_file(row)
    active_before = active_guard(audit, keys)
    out.mkdir()
    save(out / "批准范围.json", {"at": stamp(), "authorization": "工程师已明确批准按冻结清单删除这956份重复文件，保留提取证据和其他对话使用文件", "manifest_sha256": APPROVED_SHA, "count": APPROVED_COUNT, "bytes": APPROVED_BYTES, "no_scope_expansion": True})
    save(out / "执行前复核.json", {"status": "passed", "at": stamp(), "manifest_sha256": APPROVED_SHA, "audit_path": str(audit_dir), "audit_sha256": sha(audit_dir / "依赖审计.json"), "active_sources": active_before, "protected": resolver.manifest["protected"], "historical_baseline_files": len(baseline["files"]), "formal_media": len(routes), "formal_pointer_sha256": sha(LIB / "当前版本.json")})
    deleted, failures = [], []
    with (out / "逐文件执行记录.jsonl").open("x", encoding="utf-8") as journal:
        for i, row in enumerate(data["files"]):
            try:
                if i % 50 == 0:
                    active_guard(audit, keys)
                    check_file(baseline["formal_pointer"])
                    for protected in resolver.manifest["protected"]: check_file(protected)
                path = checked_path(row)
                before = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != identities[path_key(path)]: raise ValueError("候选在复核后发生变化")
                check_file(row)
                resolver.resolve(row["path"])
                # 唯一删除操作：已解析且逐文件验证的冻结路径，不删除目录。
                journal.write(json.dumps({"at": stamp(), "action": "intent", **row}, ensure_ascii=False) + "\n"); journal.flush(); os.fsync(journal.fileno())
                path.unlink()
                result = {"at": stamp(), "path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"], "status": "deleted"}
                deleted.append(result)
                journal.write(json.dumps(result, ensure_ascii=False) + "\n"); journal.flush(); os.fsync(journal.fileno())
                if (i + 1) % 100 == 0: print(f"已按冻结清单删除 {i + 1}/{len(data['files'])}", flush=True)
            except (OSError, ValueError, KeyError) as error:
                failures.append({"path": row["path"], "error": str(error)}); break
    result = {"status": "deleted_pending_verification" if len(deleted) == len(data["files"]) else "partial_stopped", "at": stamp(), "manifest_sha256": APPROVED_SHA, "approved_count": len(data["files"]), "deleted_count": len(deleted), "deleted_bytes": sum(r["bytes"] for r in deleted), "not_processed_count": len(data["files"]) - len(deleted), "failures": failures, "files": deleted, "directories_deleted": 0, "other_task_files_written": 0}
    save(out / "删除执行回执.json", result)
    if failures: raise ValueError("执行已停止，见逐文件记录和部分回执")
    verify_after(run, out, data, audit)


def verify_after(run, out, data, audit):
    keys = {path_key(r["path"]) for r in data["files"]}
    if any(Path(r["path"]).exists() for r in data["files"]): raise ValueError("批准删除文件仍有残留")
    baseline = read(run / "证据保护基线.json")
    preserved = [r for r in baseline["files"] if path_key(r["path"]) not in keys]
    for i, row in enumerate(preserved, 1):
        check_file(row)
        if i % 2000 == 0: print(f"删除后证据复核 {i}/{len(preserved)}", flush=True)
    check_file(baseline["formal_pointer"])
    resolver = ArchiveResolver(run / "迁移对照清单.json", require_verified=True)
    for row in resolver.manifest["protected"]: check_file(row)
    active_after = active_guard(audit, keys)
    _, catalog, routes, version = current()
    for row in routes.values(): check_file(row)
    for row in read(version / "来源清单.json")["files"]: check_file(row)
    subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/断箭/退役旧版资料.py"), "replay", "--run", str(run), "--report", str(out / "删除后归档回读轨迹.json")], check=True)
    subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/断箭/核验音效交付.py"), "--batch", "BA-26273833-003", "--archive-manifest", str(run / "迁移对照清单.json")], check=True)
    from 启动正式资料库 import create_server
    server = create_server(0); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    checks = []
    try:
        for ext, magic in [(".glb", b"glTF"), (".wav", b"RIFF"), (".png", b"\x89PNG")]:
            url = next(url for url in routes if url.endswith(ext))
            with urlopen(Request(f"http://127.0.0.1:{server.server_port}" + url, headers={"Range": "bytes=0-15"}), timeout=15) as response:
                if response.status != 206 or not response.read().startswith(magic): raise ValueError("正式媒体Range请求失败")
                checks.append({"url": url, "status": 206})
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=15) as response:
            if response.status != 200: raise ValueError("正式页面访问失败")
    finally: server.shutdown(); server.server_close(); thread.join()
    cli = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/断箭/查询资料库.py"), "--query", "A-10", "--type", "model", "--json"], capture_output=True, text=True, encoding="utf-8", check=True)
    if not json.loads(cli.stdout)["results"]: raise ValueError("Agent命令行检索失败")
    for name, digest in read(run / "清单指纹.json").items():
        if sha(run / name) != digest: raise ValueError("原冻结清单或证据被修改")
    save(out / "删除后完整性核验.json", {"status": "passed", "at": stamp(), "deleted_files_absent": len(data["files"]), "preserved_baseline_files_unchanged": len(preserved), "protected_files_unchanged": len(resolver.manifest["protected"]), "formal_media_verified": len(routes), "archive_replayed": len(data["files"]), "formal_pointer_unchanged": True, "agent_cli": "passed", "http_checks": checks, "active_sources": active_after})
    receipt = read(out / "删除执行回执.json")
    receipt.update({"status": "complete", "verified_at": stamp(), "preserved_baseline_files": len(preserved), "protected_files": len(resolver.manifest["protected"]), "formal_media": len(routes), "archive_replayed": len(data["files"]), "approved_files_remaining": 0, "post_verification": "passed", "limits": "删除字节为逻辑文件大小；磁盘实际可用空间受文件系统及同时运行任务影响"})
    save(out / "删除执行回执.json", receipt)
    with (out / "逐文件删除回执.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["绝对路径", "字节", "SHA256", "结果", "时间"])
        for row in receipt["files"]: writer.writerow([row["path"], row["bytes"], row["sha256"], row["status"], row["at"]])
    lines = ["# 删除执行回执", "", f"状态：完成。批准清单SHA256：`{APPROVED_SHA}`。", "", f"已删除{receipt['deleted_count']}份批准副本，逻辑大小{receipt['deleted_bytes']:,}字节（约{receipt['deleted_bytes']/1024**3:.2f} GiB）；未处理0份，失败0份，未删除目录。", "", f"保留的{len(preserved)}份基线文件摘要一致，其中639份原始导出及提取证据媒体、10份其他对话保护资源保持原位。635份正式媒体、当前索引、来源记录和旧快照验证通过。", "", "956份历史媒体归档回读、004样例媒体路由、003归档载荷核验、正式页面、GLB/WAV/PNG范围请求及Agent命令行检索通过。原始提取记录及原冻结清单未改写。", "", "- [逐文件删除回执](逐文件删除回执.csv)", "- [机器可读执行回执](删除执行回执.json)", "- [删除前引用与保护复核](执行前复核.json)", "- [删除后完整性核验](删除后完整性核验.json)", "- [删除后归档回读轨迹](删除后归档回读轨迹.json)", "", "本次只执行批准清单，无新增删除目标。后续使用正式资料库；旧快照通过显式归档解析回读，旧命令不保证按原路径运行。磁盘实际可用空间变化以系统状态为准。"]
    (out / "删除回执.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    save(out / "回执指纹.json", {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "回执指纹.json"})
    print(json.dumps({k: v for k, v in receipt.items() if k != "files"}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--manifest-sha", required=True)
    args = parser.parse_args()
    execute(run_path(args.run), args.audit, args.manifest_sha)


if __name__ == "__main__": main()
