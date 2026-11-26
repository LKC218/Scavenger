"""从 local-data/ 精选导出完整可玩运行资产至 public/assets/。

用途：
- 将 9km 地图 B4 单包归档、129 车 V3 流式归档与兵牌/元数据、3 台英雄载具 GLB、
  Three.js 精确闭合运行子集，以及 64 个现役实战与载具循环原样音频同步至 public/assets/；
- 生成纯相对路径的 public/assets/audio/runtime-audio-manifest.json 与
  public/assets/runtime-assets-manifest.json，确保无 local-data/ 的全新克隆环境也能直接运行；
- 音效严格按原文件字节拷贝，不做任何二次压缩或转码；
- 不改动 public/assets/vfx-explosion/ 等既有独立资产目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ASSETS = ROOT / "public" / "assets"

THREE_ROOT = ROOT / "local-data" / "tools" / "three"
MAP_B4_SRC = ROOT / "local-data" / "程序化地图" / "V001-fcf46de3" / "B4-单包归档"
VEH_DATA_SRC = ROOT / "local-data" / "程序化载具" / "V001-b4570e71"
FORMAL_MODEL_SRC = ROOT / "local-data" / "资源库" / "当前资源" / "模型"

HERO_GLB_SOURCES = {
    "US_M1A2.glb": FORMAL_MODEL_SRC / "地面载具" / "US_M1A2-M1A2-Abrams" / "M1A2-Abrams-256默认.glb",
    "US_AH64D.glb": FORMAL_MODEL_SRC / "空中载具" / "US_AH64D-AH-64D-Apache" / "AH-64D-Apache-256默认.glb",
    "US_A10.glb": FORMAL_MODEL_SRC / "空中载具" / "US_A10-A-10C-Thunderbolt-II" / "A-10C-Thunderbolt-II-256默认.glb",
}

MAP_B4_CORE_FILES = [
    "spatial-index.scvi",
    "startup-base.scvmap",
    "terrain-support-morton.scvmap",
    "modular-kit-morton.scvmap",
    "单包归档清单.json",
]

FRONTEND_SCAN_DIRS = [
    ROOT / "src" / "主入口",
    ROOT / "src" / "程序化地图",
    ROOT / "src" / "程序化载具",
    ROOT / "src" / "交互系统",
    ROOT / "src" / "界面规范",
]

ES_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"](\.[^'"]+)['"]|import\(\s*['"](\.[^'"]+)['"]\s*\)"""
)
ABS_PATH_LEAK_RE = re.compile(
    r"""(?:(?<![A-Za-z0-9_])[A-Za-z]:[/\\]|/Users/|/home/|Administrator|SteamLibrary|steamapps)""",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_file_clean(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst.stat().st_size


def reset_dir(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def assert_no_abs_path(payload_text: str, label: str) -> None:
    for lineno, line in enumerate(payload_text.splitlines(), 1):
        cleaned = re.sub(r"https?://\S+", "", line)
        hit = ABS_PATH_LEAK_RE.search(cleaned)
        if hit:
            raise ValueError(f"清单 {label} 第 {lineno} 行包含疑似本机绝对路径或隐私标识：{hit.group(0)}")


def audit_exported_jsons() -> int:
    """全量复核 public/assets 下所有导出 JSON 文件，确保零本机路径或隐私信息泄露。"""
    checked = 0
    for fp in sorted(PUBLIC_ASSETS.rglob("*.json")):
        if not fp.is_file():
            continue
        assert_no_abs_path(fp.read_text(encoding="utf-8", errors="ignore"), fp.relative_to(ROOT).as_posix())
        checked += 1
    return checked


def sync_three_subset() -> dict:
    pin_file = THREE_ROOT / "当前版本.json"
    if not pin_file.is_file():
        raise FileNotFoundError(f"缺少 Three.js 版本指针：{pin_file}")
    pin = json.loads(pin_file.read_text(encoding="utf-8"))
    version = str(pin["version"])
    src_dir = (THREE_ROOT / version).resolve()
    if not src_dir.is_dir():
        raise FileNotFoundError(f"缺少 Three.js 目录：{src_dir}")

    entry_rel = {"build/three.module.js", "build/three.core.js"}
    for folder in FRONTEND_SCAN_DIRS:
        if not folder.is_dir():
            continue
        for fp in list(folder.rglob("*.mjs")) + list(folder.rglob("*.js")) + list(folder.rglob("*.html")):
            txt = fp.read_text(encoding="utf-8", errors="ignore")
            for m in re.findall(r"three/addons/([^\s\"'`]+)", txt):
                entry_rel.add("examples/jsm/" + m)
            for m in re.findall(r"/vendor/([^\s\"'`\)>,;]+)", txt):
                if not m.endswith("/"):
                    entry_rel.add(m)

    visited: set[Path] = set()
    queue: list[Path] = []
    for rel in sorted(entry_rel):
        fp = (src_dir / rel).resolve()
        if fp.is_file() and fp.is_relative_to(src_dir):
            queue.append(fp)

    while queue:
        cur = queue.pop()
        if cur in visited:
            continue
        visited.add(cur)
        txt = cur.read_text(encoding="utf-8", errors="ignore")
        for m in ES_IMPORT_RE.finditer(txt):
            target = m.group(1) or m.group(2)
            nxt = (cur.parent / target).resolve()
            if nxt.is_file() and nxt.is_relative_to(src_dir):
                queue.append(nxt)

    basis_dir = src_dir / "examples" / "jsm" / "libs" / "basis"
    for fp in basis_dir.rglob("*"):
        if fp.is_file():
            visited.add(fp.resolve())
    for extra in ("LICENSE", "package.json"):
        ep = (src_dir / extra).resolve()
        if ep.is_file():
            visited.add(ep)

    out_root = PUBLIC_ASSETS / "vendor" / "three"
    reset_dir(out_root)
    out_ver = out_root / version
    total_bytes = 0
    for src_fp in sorted(visited):
        rel = src_fp.relative_to(src_dir)
        total_bytes += copy_file_clean(src_fp, out_ver / rel)

    out_pin = {
        "version": version,
        "subset": True,
        "file_count": len(visited),
    }
    pin_text = json.dumps(out_pin, ensure_ascii=False, indent=2) + "\n"
    assert_no_abs_path(pin_text, "vendor/three/当前版本.json")
    (out_root / "当前版本.json").write_text(pin_text, encoding="utf-8")
    total_bytes += (out_root / "当前版本.json").stat().st_size

    return {
        "version": version,
        "files": len(visited) + 1,
        "bytes": total_bytes,
    }


def sync_map_b4() -> dict:
    if not MAP_B4_SRC.is_dir():
        raise FileNotFoundError(f"缺少地图 B4 单包归档目录：{MAP_B4_SRC}")
    out_dir = PUBLIC_ASSETS / "map-b4"
    reset_dir(out_dir)

    count = 0
    total_bytes = 0
    max_file = ("", 0)
    for name in MAP_B4_CORE_FILES:
        src = MAP_B4_SRC / name
        if not src.is_file():
            if name == "单包归档清单.json":
                continue
            raise FileNotFoundError(f"缺少地图 B4 核心文件：{src}")
        sz = copy_file_clean(src, out_dir / name)
        count += 1
        total_bytes += sz
        if sz > max_file[1]:
            max_file = (name, sz)

    return {
        "files": count,
        "bytes": total_bytes,
        "max_file": max_file[0],
        "max_bytes": max_file[1],
    }


def sync_vehicles_v3() -> dict:
    out_root = PUBLIC_ASSETS / "vehicles-v3"
    reset_dir(out_root)

    count = 0
    total_bytes = 0
    max_file = ("", 0)

    def copy_tree(src_dir: Path, dst_dir: Path, allow_exts: set[str] | None = None) -> None:
        nonlocal count, total_bytes, max_file
        if not src_dir.is_dir():
            raise FileNotFoundError(f"缺少载具目录：{src_dir}")
        for fp in sorted(src_dir.rglob("*")):
            if not fp.is_file():
                continue
            if allow_exts is not None and fp.suffix.lower() not in allow_exts:
                continue
            rel = fp.relative_to(src_dir)
            sz = copy_file_clean(fp, dst_dir / rel)
            count += 1
            total_bytes += sz
            if sz > max_file[1]:
                max_file = (rel.as_posix(), sz)

    copy_tree(VEH_DATA_SRC / "V3-归档打包", out_root / "v3")
    copy_tree(VEH_DATA_SRC / "V6-兵牌插画", out_root / "portraits")
    copy_tree(VEH_DATA_SRC / "V2-全库编译" / "meta", out_root / "v2" / "meta", {".json"})

    v2_summary = VEH_DATA_SRC / "V2-全库编译" / "编译汇总.json"
    if v2_summary.is_file():
        sz = copy_file_clean(v2_summary, out_root / "v2" / "编译汇总.json")
        count += 1
        total_bytes += sz

    copy_tree(VEH_DATA_SRC / "V0-基线与规则", out_root / "v0", {".json"})

    for name, src in HERO_GLB_SOURCES.items():
        if not src.is_file():
            raise FileNotFoundError(f"缺少英雄载具 GLB：{src}")
        sz = copy_file_clean(src, out_root / "heroes" / name)
        count += 1
        total_bytes += sz

    return {
        "files": count,
        "bytes": total_bytes,
        "max_file": max_file[0],
        "max_bytes": max_file[1],
    }


def sync_runtime_audio() -> dict:
    for p in (str(ROOT / "scripts" / "断箭"), str(ROOT / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from 资料库核心 import current  # noqa: PLC0415
    from 资源优化.运行资源接入 import library  # noqa: PLC0415

    pointer, catalog, routes, _ = current()
    holder = type("LibHolder", (), {"catalog": catalog, "routes": routes})()
    library(holder)
    lib_catalog = holder.catalog
    lib_routes = holder.routes

    # 1. 收集前端硬编码的 /media/* 音频引用
    hardcoded_urls: set[str] = set()
    for folder in FRONTEND_SCAN_DIRS:
        if not folder.is_dir():
            continue
        for fp in list(folder.rglob("*.mjs")) + list(folder.rglob("*.js")) + list(folder.rglob("*.html")):
            txt = fp.read_text(encoding="utf-8", errors="ignore")
            for m in re.findall(r"['\"`](/media/[0-9a-f]+\.[a-z0-9]+)['\"`]", txt):
                hardcoded_urls.add(m)

    # 2. 构建与 启动主服务.py 完全一致的 129 车循环音效映射
    loop_by_model: dict[str, tuple[dict, str]] = {}
    for a in lib_catalog.get("audio", []):
        if a.get("kind") != "derived_loop":
            continue
        is_v2 = (a.get("extensions") or {}).get("loop_version") == "v2"
        if a.get("url") not in lib_routes:
            continue
        for b in a.get("bindings", []):
            if b.get("field") != "MoveEvent":
                continue
            mid = b.get("model_id")
            if not mid:
                continue
            prev = loop_by_model.get(mid)
            if prev is None or (is_v2 and (prev[0].get("extensions") or {}).get("loop_version") != "v2"):
                loop_by_model[mid] = (a, b.get("evidence", ""))

    meta_dir = VEH_DATA_SRC / "V2-全库编译" / "meta"
    vehicles: dict[str, dict] = {}
    unique_loop_urls: set[str] = set()
    unique_audio_ids: set[str] = set()
    for mp in sorted(meta_dir.glob("*.json")):
        mj = json.loads(mp.read_text(encoding="utf-8"))
        slug = mj.get("slug") or mp.stem
        mid = mj.get("id") or ("model:" + slug)
        hit = loop_by_model.get(mid) or loop_by_model.get("model:" + slug)
        if not hit:
            continue
        a, evidence = hit
        chk = a.get("check") or {}
        unique_audio_ids.add(a["id"])
        unique_loop_urls.add(a["url"])
        vehicles[slug] = {
            "slug": slug,
            "model_id": mid,
            "role": mj.get("role", "ground"),
            "audio_id": a["id"],
            "url": a["url"],
            "sha256": a["sha256"],
            "bytes": a["bytes"],
            "duration_seconds": chk.get("duration_seconds", 6.38),
            "sample_rate": chk.get("sample_rate", 48000),
            "channels": chk.get("channels", 1),
            "source_audio_id": a.get("source_audio_id", ""),
            "source_name": (a.get("aliases") or [a.get("name", "")])[0],
            "evidence": evidence,
        }

    # 3. 逐字节原样拷贝全部命中音频到 public/assets/audio/media/<filename>
    out_root = PUBLIC_ASSETS / "audio"
    reset_dir(out_root)
    media_out = out_root / "media"
    media_out.mkdir(parents=True, exist_ok=True)

    all_urls = sorted(hardcoded_urls | unique_loop_urls)
    media_routes: dict[str, str] = {}
    total_bytes = 0
    missing_urls: list[str] = []
    for url in all_urls:
        rec = lib_routes.get(url)
        if not rec:
            missing_urls.append(url)
            continue
        src_fp = Path(rec["path"]).resolve()
        if not src_fp.is_file():
            missing_urls.append(url)
            continue
        fname = Path(url).name
        total_bytes += copy_file_clean(src_fp, media_out / fname)
        media_routes[url] = f"media/{fname}"

    if missing_urls:
        raise FileNotFoundError(f"以下运行时音频在正式资料库中缺失：{missing_urls}")

    vehicle_audio_map = {
        "schema_version": 1,
        "revision": pointer.get("revision"),
        "asset_library_revision": lib_catalog.get("asset_library_revision", 1),
        "total_vehicles": len(vehicles),
        "unique_loops": len(unique_audio_ids),
        "vehicles": vehicles,
    }
    manifest = {
        "schema_version": 1,
        "source_revision": pointer.get("revision"),
        "audio_compression": "none-original-bytes",
        "hardcoded_scene_audio_count": len(hardcoded_urls),
        "vehicle_loop_audio_count": len(unique_loop_urls),
        "total_audio_files": len(media_routes),
        "media_routes": media_routes,
        "vehicle_audio_map": vehicle_audio_map,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    assert_no_abs_path(manifest_text, "audio/runtime-audio-manifest.json")
    manifest_path = out_root / "runtime-audio-manifest.json"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    total_bytes += manifest_path.stat().st_size

    return {
        "files": len(media_routes) + 1,
        "audio_files": len(media_routes),
        "hardcoded_scene_audio": len(hardcoded_urls),
        "vehicle_loop_audio": len(unique_loop_urls),
        "mapped_vehicles": len(vehicles),
        "bytes": total_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="仅检查源目录存在性，不写入 public/assets/")
    args = parser.parse_args()

    if args.dry_run:
        for label, p in [
            ("Three.js", THREE_ROOT / "当前版本.json"),
            ("地图 B4", MAP_B4_SRC),
            ("载具 V3", VEH_DATA_SRC / "V3-归档打包"),
            ("载具 V6 兵牌", VEH_DATA_SRC / "V6-兵牌插画"),
        ]:
            print(f"[check] {label}: {'OK' if p.exists() else 'MISSING'} -> {p.relative_to(ROOT)}")
        return 0

    PUBLIC_ASSETS.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modules": {
            "three": sync_three_subset(),
            "map_b4": sync_map_b4(),
            "vehicles_v3": sync_vehicles_v3(),
            "audio": sync_runtime_audio(),
        },
    }
    total_files = sum(m["files"] for m in summary["modules"].values())
    total_bytes = sum(m["bytes"] for m in summary["modules"].values())
    summary["total_files"] = total_files
    summary["total_bytes"] = total_bytes
    summary["total_mib"] = round(total_bytes / (1024 * 1024), 2)

    out_manifest = PUBLIC_ASSETS / "runtime-assets-manifest.json"
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    assert_no_abs_path(text, "runtime-assets-manifest.json")
    out_manifest.write_text(text, encoding="utf-8")
    checked_jsons = audit_exported_jsons()

    print(f"[同步可玩运行资产] 完成！总计 {total_files} 个文件，{summary['total_mib']:.2f} MiB（复核 {checked_jsons} 份 JSON 零绝对路径泄露）")
    for k, m in summary["modules"].items():
        print(f"  - {k:12s}: {m['files']:4d} files | {m['bytes'] / (1024 * 1024):6.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
