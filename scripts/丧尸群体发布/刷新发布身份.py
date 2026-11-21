"""源码或角色资源修改后，刷新分享包内的代码、资源和文件摘要。"""
from argparse import ArgumentParser
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


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


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def code_digest(root):
    value = hashlib.sha256()
    for relative in RUNTIME_SOURCES:
        path = root / relative
        value.update(relative.encode("utf-8"))
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


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
        rows.append(
            f"{digest(path)}  {path.relative_to(root).as_posix()}"
        )
    (root / "SHA256SUMS.txt").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return len(rows)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "data" / "发布资源清单.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    assets_changed = False
    for model in manifest["models"]:
        for variant in model["variants"]:
            path = (root / variant["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError(variant["path"])
            size = path.stat().st_size
            sha256 = digest(path)
            if size != variant["bytes"] or sha256 != variant["sha256"]:
                variant["bytes"] = size
                variant["sha256"] = sha256
                assets_changed = True

    current_code_sha256 = code_digest(root)
    code_changed = current_code_sha256 != manifest.get("code_sha256")
    manifest["code_sha256"] = current_code_sha256
    if code_changed or assets_changed:
        manifest["package"]["locally_modified"] = True
    if assets_changed:
        delivery = manifest["delivery"]
        upstream = delivery.get("upstream_sha256", delivery["sha256"])
        source_batch = delivery.get("source_batch_id", delivery["batch_id"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        delivery.update(
            upstream_sha256=upstream,
            source_batch_id=source_batch,
            batch_id=f"{source_batch}-局部修改-{stamp}",
            local_modified=True,
        )
        identity = {
            "upstream_sha256": upstream,
            "source_sha256": delivery["source_sha256"],
            "models": [
                {
                    "id": model["id"],
                    "source_mesh_index": model["source_mesh_index"],
                    "variants": [
                        {
                            "resolution": variant["resolution"],
                            "bytes": variant["bytes"],
                            "sha256": variant["sha256"],
                        }
                        for variant in model["variants"]
                    ],
                }
                for model in manifest["models"]
            ],
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        delivery["sha256"] = digest_bytes(encoded)

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    count = write_checksums(root)
    print(
        json.dumps(
            {
                "updated": True,
                "code_changed": code_changed,
                "assets_changed": assets_changed,
                "locally_modified": manifest["package"]["locally_modified"],
                "code_sha256": manifest["code_sha256"],
                "checksum_files": count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
