"""按当前不可变登记收集独立原型或整图工程的完整相对依赖，不改写资源。"""

import argparse
from pathlib import Path
import shutil

from 统一资源 import ROOT, current, read, save, sha
from 发布场景 import regular_file, relative_key, validate, sha_manifest


def copy_closure(identity, role, selected, retained, dependencies, output, revision, package_sha256):
    """在复制前核对完整闭包，保留包内层级及工程相对链接。"""
    keep = set()
    def visit(key):
        if key in keep:
            return
        relative_key(key)
        if key not in retained or key not in dependencies:
            raise ValueError("当前登记的依赖不完整")
        keep.add(key)
        for child in dependencies[key]:
            visit(child)
    visit(selected)
    output = Path(output).absolute()
    if not output.resolve().is_relative_to(ROOT / "local-data"):
        raise ValueError("依赖收集输出须位于项目本地数据")
    for parent in (output, *output.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('依赖收集输出含链接或目录联接')
        if parent == ROOT / 'local-data':
            break
    sources = {}
    for key in sorted(keep):
        record = retained[key]
        source = regular_file(ROOT / record["path"], ROOT / "local-data")
        if source.stat().st_size != record["bytes"] or sha(source) != record["sha256"]:
            raise ValueError("当前资源摘要变化：" + key)
        sources[key] = source
    output.mkdir(parents=True, exist_ok=False)
    for key, source in sources.items():
        target = output / key
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        if sha(target) != retained[key]["sha256"]:
            raise ValueError("依赖复制摘要不符")
    result = {"id": identity, "role": role, "entry": selected, "revision": revision,
              "package_sha256": package_sha256, "files": {key: retained[key] for key in sorted(keep)},
              "status": "依赖完整收集，待目标工具独立回读", "human_acceptance": "待人工验收"}
    save(output / "依赖收集凭证.json", result)
    return result


def collect(identity, role, output):
    pointer, catalog, _, index = current()
    scene_card = next((row for row in catalog.get("scenes", []) if row["id"] == identity), None)
    asset = None
    if scene_card is None:
        asset = next((row for row in catalog["models"] if row["id"] == identity and row.get("scene_id")), None)
        if asset is None:
            raise ValueError("当前ID不是已登记场景或场景原型")
        scene_card = next(row for row in catalog["scenes"] if row["id"] == asset["scene_id"])
    scene = read(index / "场景登记.json")[scene_card["id"]]
    entry = asset["extensions"]["scene_asset"] if asset else scene
    selected = entry["files"].get(role)
    if selected is None:
        raise ValueError("该资产没有请求的文件角色")
    return copy_closure(identity, role, selected, scene['retained_files'], scene['package_dependencies'],
                        output, pointer['revision'], scene['package_sha256'])


def collect_candidate(manifest, identity, role, output):
    checked = validate(manifest)
    entry = next((row for row in [manifest['scene'], *manifest['assets']] if row['id'] == identity), None)
    if entry is None or role not in entry['files']:
        raise ValueError('候选资产或文件角色不存在')
    retained = {key: {**row, 'path': (checked['package'] / key).relative_to(ROOT).as_posix()}
                for key, row in manifest['files'].items()}
    dependencies = {key: row.get('dependencies', []) for key, row in manifest['files'].items()}
    return copy_closure(identity, role, entry['files'][role], retained, dependencies, output,
                        'candidate', sha_manifest(manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--role", choices=["runtime", "blend", "manifest"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="只读候选清单；省略时读取当前正式登记")
    args = parser.parse_args()
    result = collect_candidate(read(args.manifest), args.id, args.role, args.output) if args.manifest else collect(args.id, args.role, args.output)
    print({"id": result["id"], "files": len(result["files"]), "output": str(args.output)})
