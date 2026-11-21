"""核对原型四向工程与回读图，生成联系图及像素差异；不自动宣布AI视觉通过。"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from 地图执行记录 import ROOT, atomic_json, fingerprint


def run(directory, revision, image_role="readback", base=None, source_file=None):
    base = base or directory / "独立原型" / revision
    output = base / ("工程联系图" if image_role == "project" else "审图联系图")
    output.mkdir(exist_ok=False)
    source = json.loads((source_file or directory / "来源/全量原型来源.json").read_text("utf-8"))
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    records, pages = [], []
    for start in range(0, len(source), 8):
        entries = source[start:start + 8]
        page = Image.new("RGB", (1280, 240 * len(entries)), "#f5f5f5")
        draw = ImageDraw.Draw(page)
        page_ids = []
        for index, row in enumerate(entries):
            folder = base / row["asset_id"].split(":")[1]
            record = {"asset_id": row["asset_id"], "name": row["name"], "views": {}}
            page_ids.append(row["asset_id"])
            label = row["name"]
            for line in range(0, len(label), 27):
                draw.text((8, 240 * index + 18 + line // 27 * 24), label[line:line + 27], fill="#111111", font=font)
            build = json.loads((folder / "构建凭证.json").read_text("utf-8"))
            readback = json.loads((folder / "回读凭证.json").read_text("utf-8"))
            draw.text((8, 240 * index + 110), f"{build['source_triangles']} -> {readback['triangles']}", fill="#555555", font=font)
            for vi, view in enumerate(["正面", "背面", "左侧", "右侧"]):
                a, b = folder / f"工程-{view}.png", folder / f"回读-{view}.png"
                first, second = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
                if first.size != second.size:
                    raise ValueError("对照图尺寸不一致")
                ar, br = np.asarray(first).astype(float), np.asarray(second).astype(float)
                delta = np.abs(ar - br)
                foreground = np.max(np.abs(ar - ar[0, 0]), axis=2) > 15
                record["views"][view] = {"project": fingerprint(a), "readback": fingerprint(b),
                    "foreground_mae": float(delta[foreground].mean()) if foreground.any() else 0,
                    "frame_mae": float(delta.mean())}
                thumbnail = first if image_role == "project" else second
                thumbnail.thumbnail((232, 216))
                page.paste(thumbnail, (320 + vi * 240, index * 240 + 20))
                draw.text((320 + vi * 240, index * 240), view, fill="#111111", font=font)
            record["build_receipt"] = fingerprint(folder / "构建凭证.json")
            record["readback_receipt"] = fingerprint(folder / "回读凭证.json")
            records.append(record)
        name = f"原型-{len(pages) + 1:02}.png"
        page.save(output / name)
        pages.append({"file": name, "asset_ids": page_ids, **fingerprint(output / name)})
    result = {"status": "已生成联系图及差异，待实际AI审图", "revision": revision, "image_role": image_role, "assets": records, "pages": pages,
              "max_foreground_mae": max(v["foreground_mae"] for r in records for v in r["views"].values()),
              "human_acceptance": "待人工验收"}
    atomic_json(output / "审图输入.json", result)
    print(json.dumps({"assets": len(records), "pages": len(pages), "max_foreground_mae": result["max_foreground_mae"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--image-role", choices=["project", "readback"], default="readback")
    parser.add_argument("--base", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to(ROOT / "local-data/断箭/批次")
    if not args.revision or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.revision):
        raise ValueError("无效修订名")
    base = (directory / args.base).resolve() if args.base else None
    source_file = (directory / args.source).resolve() if args.source else None
    for path in [base, source_file]:
        if path:
            path.relative_to(directory)
    run(directory, args.revision, args.image_role, base, source_file)
