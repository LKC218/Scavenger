"""将已验收的内部候选批次固化为命名稳定的正式版本。"""

from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_ROOT = PROJECT_ROOT / "local-data/丧尸模型/发布"
DEFAULT_SOURCE_ID = "ZOM-SHARE-261104-006"
DEFAULT_FORMAL_NAME = "丧尸群体模拟-正式版-V1.0"
DEFAULT_FORMAL_VERSION = "V1.0"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def checked_file(path, label):
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"{label}不存在或不是普通文件：{path}")
    return path


def archive_facts(path):
    path = checked_file(path, "ZIP")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        crc_failure = archive.testzip()
    return {
        "member_count": len(names),
        "crc_ok": crc_failure is None,
        "crc_failure": crc_failure,
        "usage_guide_entries": sum(
            name.endswith("使用指南.html") for name in names
        ),
        "screenshot_entries": sum(
            name.endswith("assets/WorkBuddy操作截图.png") for name in names
        ),
    }


def copy_file(source, target):
    checked_file(source, "待复制文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest(source) != digest(target):
        raise ValueError(f"复制后摘要不一致：{target}")


def validate_source(source_root, source_id):
    receipt_path = checked_file(source_root / "发布回执.json", "发布回执")
    summary_path = checked_file(source_root / "发布核验汇总.json", "核验汇总")
    receipt = load_json(receipt_path)
    summary = load_json(summary_path)
    if receipt.get("package_id") != source_id:
        raise ValueError("发布回执的来源批次不一致")
    if summary.get("package_id") != source_id:
        raise ValueError("核验汇总的来源批次不一致")
    if summary.get("technical_status") != "passed":
        raise ValueError("来源批次尚未完成技术核验")

    source_runtime = checked_file(
        source_root / receipt["runtime"]["archive"],
        "运行版 ZIP",
    )
    source_full = checked_file(
        source_root / receipt["full"]["archive"],
        "完整研究版 ZIP",
    )
    expected = {
        "runtime": (
            source_runtime,
            summary["runtime"]["archive_sha256"],
            summary["runtime"]["archive_bytes"],
            summary["archive_checks"]["runtime_members"],
            summary["archive_checks"]["runtime_usage_guide_entries"],
            summary["archive_checks"]["runtime_screenshot_entries"],
        ),
        "full": (
            source_full,
            summary["full"]["archive_sha256"],
            summary["full"]["archive_bytes"],
            summary["archive_checks"]["full_members"],
            summary["archive_checks"]["full_usage_guide_entries"],
            summary["archive_checks"]["full_screenshot_entries"],
        ),
    }
    facts = {}
    for label, (path, sha, size, members, guides, screenshots) in expected.items():
        actual_sha = digest(path)
        actual_size = path.stat().st_size
        if actual_sha != sha or actual_size != size:
            raise ValueError(f"{label} ZIP 与已核验摘要或大小不一致")
        archive = archive_facts(path)
        if not archive["crc_ok"]:
            raise ValueError(f"{label} ZIP CRC 检查失败")
        if (
            archive["member_count"] != members
            or archive["usage_guide_entries"] != guides
            or archive["screenshot_entries"] != screenshots
        ):
            raise ValueError(f"{label} ZIP 条目与核验汇总不一致")
        archive.update({"bytes": actual_size, "sha256": actual_sha})
        facts[label] = archive

    guide_path = checked_file(
        PROJECT_ROOT / "scripts/丧尸群体发布/使用指南.html",
        "正式使用指南",
    )
    screenshot_path = checked_file(
        PROJECT_ROOT / "scripts/丧尸群体发布/assets/WorkBuddy操作截图.png",
        "WorkBuddy 操作截图",
    )
    guide_text = guide_path.read_text(encoding="utf-8")
    if "这是开发项目" not in guide_text:
        raise ValueError("使用指南缺少开发项目说明")
    if "assets/WorkBuddy操作截图.png" not in guide_text:
        raise ValueError("使用指南未引用包内 WorkBuddy 截图")

    return {
        "source_root": source_root,
        "receipt_path": receipt_path,
        "summary_path": summary_path,
        "receipt": receipt,
        "summary": summary,
        "runtime": source_runtime,
        "full": source_full,
        "guide": guide_path,
        "screenshot": screenshot_path,
        "facts": facts,
    }


def evidence_files(source_root, summary):
    names = {
        "发布回执.json",
        "发布核验汇总.json",
        "使用指南浏览器核验.json",
        "完整版ZIP解压静态与规则核验.json",
        "运行版Chrome-256冒烟核验.json",
        "运行版Chrome-512冒烟核验.json",
        "运行版ZIP解压Chrome-512核验.json",
        "运行版ZIP解压Edge-512核验.json",
    }
    for check in summary.get("checks", {}).values():
        if check.get("evidence"):
            names.add(check["evidence"])
    return [checked_file(source_root / name, f"验收证据 {name}") for name in sorted(names)]


def build_release(
    validated,
    formal_root,
    formal_name,
    formal_version,
    source_id,
    promotion_basis,
):
    runtime_target = formal_root / f"丧尸群体模拟-{formal_version}-正式运行版.zip"
    full_target = formal_root / f"丧尸群体模拟-{formal_version}-完整研究版.zip"
    copy_file(validated["runtime"], runtime_target)
    copy_file(validated["full"], full_target)
    copy_file(validated["guide"], formal_root / "使用指南.html")
    copy_file(
        validated["screenshot"],
        formal_root / "assets/WorkBuddy操作截图.png",
    )

    source_runtime_preview = checked_file(
        validated["source_root"] / "使用指南-桌面预览.png",
        "桌面预览",
    )
    source_mobile_preview = checked_file(
        validated["source_root"] / "使用指南-移动预览.png",
        "移动预览",
    )
    copy_file(source_runtime_preview, formal_root / "使用指南-桌面预览.png")
    copy_file(source_mobile_preview, formal_root / "使用指南-移动预览.png")

    evidence_root = formal_root / "验收证据"
    upstream_root = evidence_root / "上游批次"
    copied_evidence = []
    for path in evidence_files(validated["source_root"], validated["summary"]):
        target = upstream_root / path.name
        copy_file(path, target)
        copied_evidence.append(target.relative_to(formal_root).as_posix())

    sha_rows = []
    for path in (runtime_target, full_target):
        sha = digest(path)
        (formal_root / (path.name + ".sha256")).write_text(
            f"{sha}  {path.name}\n",
            encoding="utf-8",
        )
        sha_rows.append((sha, path.name))

    formal_report = {
        "formal_name": formal_name,
        "formal_version": formal_version,
        "status": "formal_release",
        "promoted_by": "工程师",
        "promotion_basis": promotion_basis,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "package_id": source_id,
            "receipt": "验收证据/上游批次/发布回执.json",
            "summary": "验收证据/上游批次/发布核验汇总.json",
            "receipt_sha256": digest(validated["receipt_path"]),
            "summary_sha256": digest(validated["summary_path"]),
        },
        "artifacts": {
            "runtime": {
                "path": runtime_target.name,
                **validated["facts"]["runtime"],
            },
            "full": {
                "path": full_target.name,
                **validated["facts"]["full"],
            },
        },
        "inherited_checks": validated["summary"]["checks"],
        "claim_boundaries": {
            "engineer_release_decision": True,
            "automated_browser_smoke": True,
            "independent_visible_chrome_matrix": False,
            "independent_visible_edge_matrix": False,
            "manual_visual_motion_acceptance": False,
            "other_device_performance_acceptance": False,
        },
        "migrated_evidence": copied_evidence,
    }
    report_path = formal_root / "正式版核验报告.json"
    write_json(report_path, formal_report)

    release_note = f"""# {formal_name}

状态：正式发布。

本正式版由已冻结的内部候选 `{source_id}` 升级而来，工程师确认依据为：“{promotion_basis}”。正式文件名已统一为稳定名称，ZIP 内容保持来源批次原有摘要，未改写运行代码、模型或验收证据。

## 交付文件

- `{runtime_target.name}`：运行与研究入口，解压后可直接启动。
- `{full_target.name}`：完整研究资料，包含原始 GLB、Blender 工程、性能样本和发布工具。
- `使用指南.html`：解压后在 WorkBuddy 中添加项目文件夹的离线说明。
- `正式版核验报告.json`：正式版身份、来源、摘要和验收边界。
- `验收证据/上游批次/`：来源批次的原始发布回执与浏览器核验记录。

## 使用方式

1. 选择需要分享或继续研究的 ZIP 并完整解压。
2. 在 WorkBuddy 新建任务。
3. 选择“工作空间”，再选择“打开本地文件夹”。
4. 添加解压后的“运行研究版”或“完整研究版”目录，不要直接选择 ZIP。

详细图文步骤见 `使用指南.html`，其中使用实际 WorkBuddy 截图和红色操作箭头。

## 已验证范围

- 运行包 Chrome 156 的 256／512 档无窗口冒烟通过。
- ZIP 解压后的 Chrome 512 与 Edge 512 无窗口冒烟通过。
- 500 只、13 款模型、500 套独立骨架通过自动检查。
- 完整包 1,950 个文件、26 个资源变体和 12 项群体规则通过静态与规则检查。
- 使用指南桌面与移动布局、复制按钮、离线资源和截图加载通过自动检查。

## 结论边界

正式化是工程师对具体 `{source_id}` 构建的发布决定，不把尚未执行的项目改写成已通过。独立可见 Chrome／Edge 原生前台性能矩阵、人工外观与动作观感、其他目标设备性能和目录可写性仍需按具体设备继续验证。
"""
    (formal_root / "正式发布说明.md").write_text(
        release_note,
        encoding="utf-8",
    )

    sum_rows = []
    for path in sorted(formal_root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            sum_rows.append(
                f"{digest(path)}  {path.relative_to(formal_root).as_posix()}"
            )
    (formal_root / "SHA256SUMS.txt").write_text(
        "\n".join(sum_rows) + "\n",
        encoding="utf-8",
    )

    return {
        "formal_root": formal_root,
        "runtime": runtime_target,
        "full": full_target,
        "report": report_path,
        "release_note": formal_root / "正式发布说明.md",
        "sha_rows": sha_rows,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--formal-name", default=DEFAULT_FORMAL_NAME)
    parser.add_argument("--formal-version", default=DEFAULT_FORMAL_VERSION)
    parser.add_argument("--promotion-basis", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_root = PUBLISH_ROOT / args.source_id
    validated = validate_source(source_root, args.source_id)
    output_root = (
        args.output
        if args.output
        else PUBLISH_ROOT / args.formal_name
    ).resolve()
    if output_root.parent != PUBLISH_ROOT.resolve():
        parser.error("正式版输出必须直接位于发布根目录")
    if output_root.exists():
        raise FileExistsError(f"正式版目录已存在，拒绝覆盖：{output_root}")

    preview = {
        "dry_run": args.dry_run,
        "source_id": args.source_id,
        "formal_name": args.formal_name,
        "formal_version": args.formal_version,
        "output": str(output_root),
        "runtime_sha256": validated["facts"]["runtime"]["sha256"],
        "full_sha256": validated["facts"]["full"]["sha256"],
        "runtime_members": validated["facts"]["runtime"]["member_count"],
        "full_members": validated["facts"]["full"]["member_count"],
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{args.formal_name}-", dir=output_root.parent)
    )
    try:
        built = build_release(
            validated,
            temporary,
            args.formal_name,
            args.formal_version,
            args.source_id,
            args.promotion_basis,
        )
        temporary.rename(output_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    built["formal_root"] = output_root
    built["runtime"] = output_root / built["runtime"].name
    built["full"] = output_root / built["full"].name
    built["report"] = output_root / built["report"].name
    built["release_note"] = output_root / built["release_note"].name
    preview.update(
        {
            "status": "created",
            "formal_root": str(output_root),
            "formal_report": str(built["report"]),
            "runtime": str(built["runtime"]),
            "full": str(built["full"]),
        }
    )
    print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
