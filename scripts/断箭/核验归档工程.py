"""由独立后台Blender只读打开归档工程，检查打包图像及外部依赖。"""
import argparse
import json
from pathlib import Path
import sys


def main():
    import bpy
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    boundary = Path(__file__).resolve().parents[2] / "local-data/断箭/维护/旧版退役与引用迁移"
    if not args.output.resolve().is_relative_to(boundary.resolve()) or args.output.exists(): raise ValueError("必须写入新的维护回执")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    paths = sorted({r["replacement"]["path"] for r in manifest["files"] if Path(r["old"]["path"]).suffix == ".blend"})
    results = []
    for number, name in enumerate(paths, 1):
        bpy.ops.wm.open_mainfile(filepath=name, load_ui=False, use_scripts=False)
        external = []
        for image in bpy.data.images:
            if image.source in {"FILE", "TILED", "MOVIE", "SEQUENCE"} and not (image.packed_file or image.packed_files):
                external.append({"kind": "image", "name": image.name, "path": image.filepath})
        for collection in (bpy.data.libraries, bpy.data.movieclips, bpy.data.cache_files, bpy.data.volumes):
            for item in collection: external.append({"kind": item.bl_rna.identifier, "name": item.name, "path": getattr(item, "filepath", "")})
        for item in [*bpy.data.sounds, *bpy.data.fonts]:
            if getattr(item, "filepath", "") not in {"", "<builtin>"} and not getattr(item, "packed_file", None):
                external.append({"kind": item.bl_rna.identifier, "name": item.name, "path": item.filepath})
        for obj in bpy.data.objects:
            for modifier in obj.modifiers:
                if modifier.type in {"MESH_CACHE", "MESH_SEQUENCE_CACHE", "FLUID", "CLOTH", "SOFT_BODY", "PARTICLE_SYSTEM"}:
                    external.append({"kind": "simulation_modifier", "name": obj.name + "/" + modifier.name})
        results.append({"path": name, "status": "passed" if not external else "defer", "images": len(bpy.data.images), "external": external})
        print(f"归档工程只读检查 {number}/{len(paths)}", flush=True)
    args.output.write_text(json.dumps({"blender_version": bpy.app.version_string, "saved_any_scene": False, "files": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__": main()
