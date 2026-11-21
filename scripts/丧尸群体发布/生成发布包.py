"""生成丧尸群体运行研究包和完整研究包，拒绝覆盖已有版本。"""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = Path(__file__).resolve().parent
PACKAGE_ID_PATTERN = re.compile(r"^ZOM-SHARE-\d{6}-\d{3}$")
SOURCE_TASK = "丧尸群体分享包V001-01a1a529"
DELIVERY = PROJECT_ROOT / "local-data/丧尸模型/十三款优化/交接/当前交付.json"
FULL_DATA = PROJECT_ROOT / "local-data/丧尸模型"
THREE_ROOT = PROJECT_ROOT / "local-data/tools/three/0.187.0"
LIBRARY_ROOT = PROJECT_ROOT / "local-data/资源库"
SOURCE_ORIGINAL = (
    LIBRARY_ROOT
    / "恢复缓存/19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea"
    / "super_low-poly_stylized_zombies_animated.glb"
)

STATIC_FILES = [
    "index.html",
    "界面.css",
    "入口.mjs",
    "群体.mjs",
    "角色.mjs",
    "角色配置.mjs",
    "场景.mjs",
    "统计.mjs",
]
RUNTIME_SOURCES = [
    "src/丧尸群体测试/index.html",
    "src/丧尸群体测试/界面.css",
    "src/丧尸群体测试/入口.mjs",
    "src/丧尸群体测试/群体.mjs",
    "src/丧尸群体测试/角色.mjs",
    "src/丧尸群体测试/角色配置.mjs",
    "src/丧尸群体测试/场景.mjs",
    "src/丧尸群体测试/统计.mjs",
    "src/资源加载/压缩模型加载.mjs",
    "scripts/发布服务.py",
]
VENDOR_FILES = [
    "build/three.module.js",
    "build/three.core.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/utils/BufferGeometryUtils.js",
    "examples/jsm/utils/SkeletonUtils.js",
    "examples/jsm/loaders/KTX2Loader.js",
    "examples/jsm/libs/meshopt_decoder.module.js",
    "examples/jsm/utils/WorkerPool.js",
    "examples/jsm/libs/ktx-parse.module.js",
    "examples/jsm/libs/zstddec.module.js",
    "examples/jsm/math/ColorSpaces.js",
    "examples/jsm/libs/basis/basis_transcoder.js",
    "examples/jsm/libs/basis/basis_transcoder.wasm",
]
DOCS = [
    "docs/丧尸群体设计与性能研究.md",
    "docs/丧尸十三款拆分入库与混合群体-实施计划.md",
    "docs/项目开发经验/项目开发经验.md",
    "docs/项目开发经验/模型与音频优化复用指南.md",
    "reports/报告维护说明.md",
    "scripts/脚本维护说明.md",
]
RESEARCH_SCRIPTS = [
    "scripts/测试丧尸模型.py",
    "scripts/核验丧尸测试工程.py",
    "scripts/预览丧尸群体测试.py",
    "scripts/验证丧尸群体测试.py",
    "scripts/验证丧尸群体规则.mjs",
    "scripts/群体资源交接.py",
    "scripts/群体采样归档.py",
    "scripts/生成混合丧尸性能报告.py",
    "scripts/资源优化/运行资源接入.py",
    "scripts/记录内置浏览器前台.py",
    "scripts/归档内置浏览器采样.py",
]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def checked_file(root, path, label):
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"{label}不存在或越界：{path}")
    return path


def copy_file(source, target):
    source = Path(source)
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"待复制文件不存在或为链接：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f"目标文件已存在：{target}")
    shutil.copy2(source, target)


def copy_tree(source, target, exclude_roots=()):
    source = Path(source).resolve()
    excluded = [Path(path).resolve() for path in exclude_roots]
    if not source.is_dir():
        raise FileNotFoundError(source)
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"拒绝复制符号链接或目录联接：{item}")
        resolved = item.resolve()
        if any(resolved == root or resolved.is_relative_to(root) for root in excluded):
            continue
        relative = item.relative_to(source)
        if "__pycache__" in relative.parts or item.suffix in (".pyc", ".pyo"):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            copy_file(item, destination)


def code_digest(root):
    value = hashlib.sha256()
    for relative in RUNTIME_SOURCES:
        path = root / relative
        value.update(relative.encode("utf-8"))
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


def load_delivery():
    raw = DELIVERY.read_bytes()
    delivery = json.loads(raw.decode("utf-8-sig"))
    if delivery.get("schema") != 1 or delivery.get("technical_passed") is not True:
        raise ValueError("当前资源交付不是冻结技术通过版本")
    models = delivery.get("models") or []
    if len(models) != 13:
        raise ValueError("当前交付不是十三款")
    expected_ids = {"model:zombie-runtime"} | {
        f"model:zombie-{index:02d}" for index in range(1, 13)
    }
    if {model.get("id") for model in models} != expected_ids:
        raise ValueError("当前交付稳定ID不完整")
    if {model.get("source_mesh_index") for model in models} != set(range(13)):
        raise ValueError("当前交付源网格不完整")
    return raw, delivery


def verify_receipts(model):
    hashes = []
    for receipt in model.get("technical_receipts") or []:
        path = checked_file(PROJECT_ROOT, receipt["path"], "技术回执")
        if digest(path) != receipt["sha256"] or path.stat().st_size != receipt["bytes"]:
            raise ValueError(f"技术回执摘要变化：{model['id']}")
        hashes.append(receipt["sha256"])
    return hashes


def package_models(delivery, runtime_dir):
    models = []
    total_by_resolution = {256: 0, 512: 0}
    for source_model in sorted(delivery["models"], key=lambda row: row["source_mesh_index"]):
        receipts = verify_receipts(source_model)
        variants = []
        for source_variant in sorted(
            source_model["variants"],
            key=lambda row: row["resolution"],
        ):
            source = checked_file(
                PROJECT_ROOT,
                source_variant["path"],
                f"角色资源{source_model['id']}",
            )
            if (
                digest(source) != source_variant["sha256"]
                or source.stat().st_size != source_variant["bytes"]
            ):
                raise ValueError(f"角色资产摘要变化：{source_model['id']}")
            relative = (
                f"runtime/assets/{source_model['source_mesh_index']:02d}-"
                f"{source_variant['resolution']}.glb"
            )
            copy_file(source, runtime_dir.parent.parent / relative)
            variants.append(
                {
                    "resolution": source_variant["resolution"],
                    "path": relative,
                    "bytes": source_variant["bytes"],
                    "sha256": source_variant["sha256"],
                }
            )
            total_by_resolution[source_variant["resolution"]] += source_variant["bytes"]
        models.append(
            {
                "id": source_model["id"],
                "source_mesh_index": source_model["source_mesh_index"],
                "source_mesh_name": source_model["source_mesh_name"],
                "bones": source_model["bones"],
                "materials": source_model["materials"],
                "triangles": source_model["triangles"],
                "clips": source_model["clips"],
                "coordinate_system": source_model["coordinate_system"],
                "technical_receipt_sha256": receipts,
                "variants": variants,
            }
        )
    return models, total_by_resolution


def write_checksums(root):
    rows = []
    for path in sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and item.name != "SHA256SUMS.txt"
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        rows.append(f"{digest(path)}  {path.relative_to(root).as_posix()}")
    write_text(root / "SHA256SUMS.txt", "\n".join(rows))
    return len(rows)


def create_archive(package_dir, archive_path):
    fixed_time = (2026, 11, 4, 0, 0, 0)
    compressed = {
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".css",
        ".csv",
        ".txt",
        ".svg",
    }
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in sorted(
            (item for item in package_dir.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(package_dir).as_posix(),
        ):
            archive_name = (
                package_dir.name + "/" + path.relative_to(package_dir).as_posix()
            )
            info = zipfile.ZipInfo(archive_name, date_time=fixed_time)
            info.compress_type = (
                zipfile.ZIP_DEFLATED
                if path.suffix.lower() in compressed
                else zipfile.ZIP_STORED
            )
            info.external_attr = (0o100644 & 0xFFFF) << 16
            with path.open("rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
    write_text(sidecar, f"{digest(archive_path)}  {archive_path.name}")


def copy_docs(package_dir):
    for relative in DOCS:
        source = checked_file(PROJECT_ROOT, relative, "文档")
        copy_file(source, package_dir / relative)
    copy_tree(PROJECT_ROOT / "reports/丧尸模型", package_dir / "reports/丧尸模型")
    copy_tree(PROJECT_ROOT / "research/丧尸模型", package_dir / "research/丧尸模型")
    copy_file(
        MODULE_ROOT / "运行包README.md",
        package_dir / "README.md",
    )
    copy_file(
        PROJECT_ROOT / "docs/丧尸群模拟开发者阅读说明书.md",
        package_dir / "开发者阅读说明书.md",
    )
    copy_file(
        MODULE_ROOT / "使用指南.html",
        package_dir / "使用指南.html",
    )
    copy_file(
        MODULE_ROOT / "assets/WorkBuddy操作截图.png",
        package_dir / "assets/WorkBuddy操作截图.png",
    )
    copy_file(MODULE_ROOT / "已知限制.md", package_dir / "已知限制.md")
    copy_file(MODULE_ROOT / "许可与来源.md", package_dir / "许可与来源.md")
    copy_file(
        MODULE_ROOT / "模块说明.md",
        package_dir / "src/丧尸群体测试/README.md",
    )


def copy_vendor(package_dir):
    destination = package_dir / "runtime/vendor"
    for relative in VENDOR_FILES:
        copy_file(THREE_ROOT / relative, destination / relative)
    copy_file(THREE_ROOT / "LICENSE", destination / "LICENSE")
    copy_file(THREE_ROOT / "来源与校验.json", destination / "来源与校验.json")


def write_launchers(package_dir):
    write_text(
        package_dir / "启动丧尸群体测试.cmd",
        """@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 -B "scripts\\发布服务.py" --port 8769
if errorlevel 1 pause
""",
    )
    shell = package_dir / "start.sh"
    write_text(
        shell,
        """#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec python3 -X utf8 -B scripts/发布服务.py --port 8769
""",
    )
    try:
        os.chmod(shell, 0o755)
    except OSError:
        pass


def write_navigation(package_dir):
    write_text(
        package_dir / "docs/文件导航.md",
        """# 文件导航

| 入口 | 职责 |
| --- | --- |
| `README.md` | 启动、操作和包内导航 |
| `使用指南.html` | 解压项目并在 WorkBuddy 中添加本地文件夹 |
| `assets/WorkBuddy操作截图.png` | 使用指南中的 WorkBuddy 操作截图 |
| `开发者阅读说明书.md` | 运行流程、模块职责、修改与验证 |
| `已知限制.md` | 未验收项和结论边界 |
| `data/发布资源清单.json` | 十三款角色相对路径与摘要 |
| `scripts/发布服务.py` | 本机回环服务和采样归档 |
| `scripts/核验发布包.py` | 文件、服务与可选浏览器检查 |
| `scripts/刷新发布身份.py` | 修改后刷新源码与资源摘要 |
| `src/丧尸群体测试/` | 页面、群体、角色、场景和统计源码 |
| `reports/丧尸模型/` | 离线性能与技术报告 |
| `research/丧尸模型/` | 脱敏研究摘要与样本摘要 |
""",
    )


def build_runtime(output_root, package_id):
    runtime_dir = output_root / "运行研究版"
    runtime_dir.mkdir(parents=True, exist_ok=False)
    source_app = PROJECT_ROOT / "src/丧尸群体测试"
    for name in STATIC_FILES:
        copy_file(source_app / name, runtime_dir / "src/丧尸群体测试" / name)
    copy_file(
        PROJECT_ROOT / "src/资源加载/压缩模型加载.mjs",
        runtime_dir / "src/资源加载/压缩模型加载.mjs",
    )
    for name in (
        "发布服务.py",
        "核验发布包.py",
        "刷新发布身份.py",
    ):
        copy_file(MODULE_ROOT / name, runtime_dir / "scripts" / name)
    copy_file(
        PROJECT_ROOT / "scripts/验证丧尸群体规则.mjs",
        runtime_dir / "tests/验证群体规则.mjs",
    )
    copy_docs(runtime_dir)
    copy_vendor(runtime_dir)
    write_launchers(runtime_dir)
    write_navigation(runtime_dir)

    raw_delivery, delivery = load_delivery()
    models, asset_bytes = package_models(delivery, runtime_dir / "src/丧尸群体测试")
    delivery_identity = {
        "sha256": hashlib.sha256(raw_delivery).hexdigest(),
        "batch_id": delivery["batch_id"],
        "frozen_at": delivery["frozen_at"],
        "source_sha256": delivery["source_sha256"],
        "library_revision": delivery["library_revision"],
        "library_pointer_sha256": delivery["library_pointer_sha256"],
        "catalog_sha256": delivery["catalog_sha256"],
    }
    runtime_manifest = {
        "schema": 1,
        "package": {
            "id": package_id,
            "version": "1.0.0",
            "kind": "runtime-research",
            "source_task": SOURCE_TASK,
            "locally_modified": False,
        },
        "delivery": delivery_identity,
        "models": models,
        "three": "0.187.0",
    }
    runtime_manifest["code_sha256"] = code_digest(runtime_dir)
    write_json(runtime_dir / "data/发布资源清单.json", runtime_manifest)
    write_json(
        runtime_dir / "版本与状态.json",
        {
            "package_id": package_id,
            "version": "1.0.0",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_task": SOURCE_TASK,
            "source_delivery": delivery_identity,
            "model_count": 13,
            "asset_bytes": asset_bytes,
            "status": {
                "technical_resource_identity": "passed",
                "built_in_browser_matrix": "passed_report_available",
                "native_chrome_edge_foreground": "pending",
                "manual_visual_and_motion_acceptance": "pending",
            },
            "code_sha256": runtime_manifest["code_sha256"],
        },
    )
    return runtime_dir


def copy_library_metadata(package_dir):
    target = package_dir / "研究资料/统一库元数据"
    pointer_path = LIBRARY_ROOT / "当前版本.json"
    pointer = read_json(pointer_path)
    index = (LIBRARY_ROOT / pointer["directory"]).resolve()
    if not index.is_relative_to(LIBRARY_ROOT) or not index.is_dir():
        raise ValueError("当前统一库指针无效")
    copy_file(pointer_path, target / "当前版本.json")
    copy_tree(index, target / pointer["directory"])


def build_full(output_root, runtime_dir, package_id):
    full_dir = output_root / "完整研究版"
    shutil.copytree(runtime_dir, full_dir)
    copy_file(
        MODULE_ROOT / "完整研究包说明.md",
        full_dir / "完整研究包说明.md",
    )
    copy_tree(
        FULL_DATA,
        full_dir / "研究资料/本地丧尸模型",
        exclude_roots=(FULL_DATA / "发布",),
    )
    copy_file(SOURCE_ORIGINAL, full_dir / "研究资料/来源/super_low-poly_stylized_zombies_animated.glb")
    copy_library_metadata(full_dir)
    copy_tree(
        PROJECT_ROOT / "local-data/tools/three/0.187.0",
        full_dir / "研究资料/工具/three-0.187.0",
    )
    for relative in RESEARCH_SCRIPTS:
        source = PROJECT_ROOT / relative
        if source.is_file():
            copy_file(source, full_dir / "研究资料/脚本" / source.name)
    copy_tree(
        PROJECT_ROOT / "scripts/丧尸十三款",
        full_dir / "研究资料/脚本/丧尸十三款",
    )
    for relative in DOCS:
        source = PROJECT_ROOT / relative
        if source.is_file():
            copy_file(source, full_dir / "研究资料/文档" / source.name)
    copy_tree(
        MODULE_ROOT,
        full_dir / "研究资料/研究工具/发布工具",
    )
    write_checksums(full_dir)
    return full_dir


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-id",
        required=True,
        help="发布包编号，格式为 ZOM-SHARE-YYMMDD-NNN",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="发布输出目录；省略时按包号写入默认发布目录",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    package_id = args.package_id.strip()
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        parser.error("--package-id 必须形如 ZOM-SHARE-261104-006")
    output_root = (
        args.output
        if args.output
        else PROJECT_ROOT / "local-data/丧尸模型/发布" / package_id
    ).resolve()
    if args.dry_run:
        raw, delivery = load_delivery()
        total = sum(
            variant["bytes"]
            for model in delivery["models"]
            for variant in model["variants"]
        )
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "package_id": package_id,
                    "delivery_sha256": hashlib.sha256(raw).hexdigest(),
                    "models": len(delivery["models"]),
                    "asset_bytes": total,
                    "original_exists": SOURCE_ORIGINAL.is_file(),
                    "output": str(output_root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if output_root.exists():
        raise FileExistsError(f"发布输出已存在，拒绝覆盖：{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)

    runtime_dir = build_runtime(output_root, package_id)
    runtime_hash_count = write_checksums(runtime_dir)
    runtime_archive = output_root / (
        f"Scavenger-丧尸群体模拟-{package_id}-运行研究包.zip"
    )
    create_archive(runtime_dir, runtime_archive)

    full_dir = build_full(output_root, runtime_dir, package_id)
    full_hash_count = write_checksums(full_dir)
    full_archive = output_root / (
        f"Scavenger-丧尸群体模拟-{package_id}-完整研究包.zip"
    )
    create_archive(full_dir, full_archive)

    receipt = {
        "package_id": package_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_task": SOURCE_TASK,
        "runtime": {
            "folder": runtime_dir.name,
            "files": runtime_hash_count,
            "bytes": sum(
                path.stat().st_size
                for path in runtime_dir.rglob("*")
                if path.is_file()
            ),
            "archive": runtime_archive.name,
            "archive_bytes": runtime_archive.stat().st_size,
            "archive_sha256": digest(runtime_archive),
        },
        "full": {
            "folder": full_dir.name,
            "files": full_hash_count,
            "bytes": sum(
                path.stat().st_size
                for path in full_dir.rglob("*")
                if path.is_file()
            ),
            "archive": full_archive.name,
            "archive_bytes": full_archive.stat().st_size,
            "archive_sha256": digest(full_archive),
        },
        "public_claims": {
            "browser_verified": False,
            "manual_acceptance": False,
            "note": "文件身份与技术资料已冻结；独立Chrome／Edge前台和人工观感另行验收",
        },
    }
    write_json(output_root / "发布回执.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
