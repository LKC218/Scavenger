"""冻结旧入口和Python缓存清单；仅在清单批准后显式执行删除。"""
import argparse
from pathlib import Path
import json

from 资料库核心 import ROOT, DATA, LIB, BASE, current, read, save, sha, stamp, check_file

MAINT = DATA / "维护/正式资料库清理"
MANIFEST = MAINT / "删除清单.json"
OLD = [
    BASE / "BA-26273833-001/美军模型库/批量导出目录.html",
    BASE / "BA-26273833-003/音效配套库/配套音效目录.html",
    BASE / "BA-26273833-007/音效浏览库/音效浏览库.html",
    BASE / "BA-26273833-008/模型音效浏览库/模型音效浏览库.html",
    BASE / "BA-26273833-009/模型音效浏览库/模型音效浏览库.html",
]
CACHE = ROOT / "scripts/断箭/__pycache__"


def allowed(path):
    absolute = Path(path).absolute()
    resolved = absolute.resolve()
    if resolved != absolute or not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("删除目标存在链接或超出工作区")
    if resolved not in OLD and not (resolved.parent == CACHE and resolved.suffix == ".pyc"):
        raise ValueError("删除目标不在固定页面及缓存边界内")
    return resolved


def prepare():
    MAINT.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists():
        raise ValueError("冻结清单已存在，不自动覆盖；先核对当前清单状态")
    pointer, _, routes, version = current()
    candidates = []
    for path in OLD + sorted(CACHE.glob("*.pyc")):
        path = allowed(path)
        if path.is_file():
            candidates.append({"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path),
                               "kind": "replaced_page" if path in OLD else "python_cache"})
    protected = list(routes.values()) + read(version / "来源清单.json").get("files", [])
    protected_paths = {Path(row["path"]).resolve() for row in protected}
    if any(Path(row["path"]) in protected_paths for row in candidates):
        raise ValueError("候选与正式来源或媒体重叠")
    manifest = {"schema_version": 1, "created_at": stamp(), "status": "awaiting_approval", "revision": pointer["revision"],
                "replacement": "http://127.0.0.1:8767/", "files": candidates, "count": len(candidates),
                "expected_bytes": sum(row["bytes"] for row in candidates), "protected_files": protected,
                "retained": ["模型、WAV、Blender、预览图、来源JSON、提取和渲染证据", "004独立联动样例", "所有批次的其他文件与其他主题研究", "37份既有并行清理候选", "tmp与游戏安装目录"],
                "historical_manifest_note": "历史快照不改写；删除后旧HTML缺失由独立退役回执解释，旧页面载荷检查不再代表当前全部原文件存在"}
    save(MANIFEST, manifest)
    digest = sha(MANIFEST)
    lines = ["# 正式资料库旧入口清理确认单", "", "状态：待工程师批准，尚未删除。", "",
             f"冻结清单SHA256：`{digest}`", "", f"共{len(candidates)}个文件，预计释放{manifest['expected_bytes']:,}字节。只删除下表文件，不删除目录。", "",
             "| 绝对路径 | 字节 | SHA256 |", "| --- | ---: | --- |"]
    lines += [f"| `{r['path']}` | {r['bytes']} | `{r['sha256']}` |" for r in candidates]
    lines += ["", "## 保留与依赖", "", *["- " + value for value in manifest["retained"]], "",
              "新正式库读取原GLB、WAV、图片、Blender与结构化JSON，不读取以上5份旧页面。原003配套正式库的WAV和JSON保留。浏览器全局缓存不删除；新页面/API使用no-store，媒体使用内容摘要URL，避免旧页面缓存干扰。", "",
              "执行前逐项复核大小与SHA256，同时核对全部正式媒体和直接来源；任何变化先停止。批准仅适用于这份清单，不自动扩大范围。缓存执行后如被程序重新生成，单独记作regenerated_cache。", "",
              "历史批次冻结清单保留原样，退役说明单独保存；不能再以旧HTML存在性宣称旧批次完整未变。"]
    (MAINT / "清理确认单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"manifest": str(MANIFEST), "sha256": digest, "count": manifest["count"], "expected_bytes": manifest["expected_bytes"]}


def execute(expected_sha):
    if sha(MANIFEST) != expected_sha:
        raise ValueError("清单摘要与批准版本不同")
    manifest = read(MANIFEST)
    if (MAINT / "退役回执.json").exists():
        raise ValueError("已有回执，不重复删除或扩大清单")
    _, _, routes, _ = current()
    protected = {Path(row["path"]).resolve() for row in routes.values()}
    for row in manifest["files"]:
        path = allowed(row["path"])
        if path in protected: raise ValueError("目标已成为正式媒体，停止删除")
        check_file(row)
    for row in manifest["protected_files"]: check_file(row)
    removed = []
    receipt = {"manifest_sha256": expected_sha, "started_at": stamp(), "status": "running", "removed": removed}
    save(MAINT / "退役回执.json", receipt)
    for row in manifest["files"]:
        allowed(row["path"]).unlink()
        removed.append(row)
        save(MAINT / "退役回执.json", receipt)
    for row in manifest["protected_files"]: check_file(row)
    regenerated = [r["path"] for r in removed if Path(r["path"]).exists() and r["kind"] == "python_cache"]
    unexpected = [r["path"] for r in removed if Path(r["path"]).exists() and r["kind"] != "python_cache"]
    receipt.update(status="passed" if not unexpected else "review", finished_at=stamp(), released_bytes=sum(r["bytes"] for r in removed),
                   regenerated_cache=regenerated, unexpected_remaining=unexpected, protected_files_verified=True,
                   historical_note=manifest["historical_manifest_note"])
    save(MAINT / "退役回执.json", receipt)
    return {key: value for key, value in receipt.items() if key != "removed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--manifest-sha", help="工程师已批准的冻结清单SHA256；只有execute使用")
    args = parser.parse_args()
    if args.action == "execute" and not args.manifest_sha: parser.error("execute须提供已批准清单摘要")
    print(json.dumps(prepare() if args.action == "prepare" else execute(args.manifest_sha), ensure_ascii=False, indent=2))
