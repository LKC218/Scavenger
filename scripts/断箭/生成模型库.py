"""按交付选择生成可离线浏览的模型库；逐项核验回执、审核图像和复制指纹。"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
import time
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = "BA-26273833-001"
CATEGORIES = {"air": "空中", "ground": "地面"}
STATUSES = {"passed": "通过", "partial": "待修参考"}
PAYLOADS = ("model.blend", "model.glb")
IMAGES = ("预览/整体.png", "预览/反侧.png", "预览/正面.png", "预览/顶部.png", "回读预览/整体.png")
CHECKS = ("模型检查.json", "导入检查.json", "独立回读.json")
FILES = PAYLOADS + IMAGES + CHECKS
GENERAL_LIMITATIONS = [
    "静态模型，无完整骨骼动画、游戏逻辑或全部改装组合。",
    "Unity 自定义 Shader 在 Blender 与 GLB 中为近似还原，透明、光照及材质表现可能不同。",
    "尺寸沿用资源导入尺度，未经真实载具尺寸标定；未做引擎集成与设备性能验收。",
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def component(value, label):
    """拒绝路径分隔符、Windows 保留名和会被系统截断的文件名。"""
    if not isinstance(value, str) or not value or value != value.strip() or value in (".", ".."):
        raise ValueError(f"{label}必须是非空的单层目录名")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " ")):
        raise ValueError(f"{label}含有不安全的目录字符：{value!r}")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", value.split(".")[0], re.I):
        raise ValueError(f"{label}不能使用系统保留名")
    return value


def inside(parent, relative, label):
    """只接受批次内相对路径，并解析符号链接与目录联接后再校验。"""
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label}必须是相对路径")
    normal = relative.replace("\\", "/")
    parts = PurePosixPath(normal)
    if parts.is_absolute() or PureWindowsPath(relative).drive or ".." in parts.parts or not parts.parts:
        raise ValueError(f"{label}必须位于指定目录内：{relative!r}")
    result = (parent / Path(*parts.parts)).resolve()
    if result == parent or not result.is_relative_to(parent):
        raise ValueError(f"{label}越过指定目录：{relative!r}")
    return result


def valid_hash(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"{label}缺少有效 SHA256")
    return value.lower()


def texts(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label}必须是文字列表")
    return value


def preflight(batch, output, selection):
    if not isinstance(selection, dict) or selection.get("batch") != batch.name:
        raise ValueError("交付选择的 batch 与当前批次不一致")
    entries = selection.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("交付选择 entries 必须是非空列表")
    plans, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("交付条目必须是对象")
        name = component(entry.get("name"), "型号")
        variant = component(entry.get("variant"), f"{name} 的 variant")
        category, status = entry.get("category"), entry.get("status")
        if category not in CATEGORIES or status not in STATUSES:
            raise ValueError(f"{name} 的 category 或 status 不受支持")
        observations = texts(entry.get("observations"), f"{name} 的 observations")
        limitations = texts(entry.get("limitations"), f"{name} 的 limitations")
        if status == "partial" and not limitations:
            raise ValueError(f"{name} 为待修参考，必须注明限制")
        source = inside(batch, entry.get("source_output"), f"{name} 的源输出")
        if not source.is_dir() or source == output or source.is_relative_to(output):
            raise ValueError(f"{name} 的源输出不存在或与模型库输出重叠")
        destination = Path(CATEGORIES[category]) / name / variant
        if status == "partial":
            destination = Path("待修参考") / destination
        key = destination.as_posix().casefold()
        if key in seen:
            raise ValueError(f"型号、分类与 variant 重复：{destination}")
        seen.add(key)

        evidence = entry.get("review_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(f"{name} 的 review_evidence 必须是路径到 SHA256 的映射")
        verified_evidence = {}
        for relative, expected in evidence.items():
            image_path = inside(batch, relative, f"{name} 的审核证据")
            expected = valid_hash(expected, f"{name} 的审核图")
            if image_path.suffix.lower() != ".png" or not image_path.is_file():
                raise ValueError(f"{name} 的审核图不存在或不是 PNG：{relative}")
            if sha256(image_path) != expected:
                raise ValueError(f"{name} 的审核图已改变：{relative}")
            verified_evidence[image_path] = expected

        sources = {relative: inside(source, relative, f"{name} 的交付文件") for relative in FILES}
        for relative, path in sources.items():
            if not path.is_file():
                raise FileNotFoundError(f"{name} 缺少交付文件：{relative}")
        receipt = read_json(sources["模型检查.json"])
        if receipt.get("结构检查通过") is not True or receipt.get("源文件指纹一致") is not True:
            raise ValueError(f"{name} 的结构检查回执未通过")
        if receipt.get("缺失贴图条目") != 0:
            raise ValueError(f"{name} 的回执存在缺失贴图")
        phases = {item.get("阶段"): item.get("退出码") for item in receipt.get("阶段", [])}
        if any(phases.get(phase) != 0 for phase in ("prepare", "reopen")):
            raise ValueError(f"{name} 的检查进程未全部成功")
        imported = read_json(sources["导入检查.json"])
        reopened = read_json(sources["独立回读.json"])
        if reopened.get("包围盒匹配") is not True or reopened.get("三角形数匹配") is not True:
            raise ValueError(f"{name} 的独立回读几何检查未通过")
        for stats in (imported, reopened.get("工程重开", {}), reopened.get("GLB回读", {})):
            if stats.get("缺失贴图") != [] or stats.get("非有限坐标顶点数") != 0:
                raise ValueError(f"{name} 的导入或回读统计存在异常")

        file_records = {}
        for relative, path in sources.items():
            actual = sha256(path)
            size = path.stat().st_size
            if relative in PAYLOADS:
                record = receipt.get("交付文件", {}).get(relative, {})
                expected = valid_hash(record.get("SHA256"), f"{name} 的 {relative} 回执")
                if actual != expected or size != record.get("字节"):
                    raise ValueError(f"{name} 的载荷与回执不一致：{relative}")
            if relative in IMAGES:
                expected = verified_evidence.get(path)
                if status == "passed" and expected is None:
                    raise ValueError(f"{name} 的通过状态缺少当前审核图：{relative}")
                if expected is not None and actual != expected:
                    raise ValueError(f"{name} 的审核图在核验期间改变：{relative}")
            file_records[relative] = {"source": path, "bytes": size, "sha256": actual}
        plans.append({"name": name, "variant": variant, "category": category, "status": status,
                      "source_output": source.relative_to(batch).as_posix(), "destination": destination,
                      "observations": observations, "limitations": limitations,
                      "reviewed_image_count": sum(sources[p] in verified_evidence for p in IMAGES),
                      "reviewed_image_files": [p for p in IMAGES if sources[p] in verified_evidence],
                      "geometry": receipt.get("几何统计", {}), "files": file_records})
    return plans


def copy_verified(source, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if destination.stat().st_size != expected["bytes"] or sha256(destination) != expected["sha256"]:
        raise ValueError(f"复制后指纹不一致：{destination}")


def counts(entries):
    return {"total": len(entries),
            "category": {key: sum(e["category"] == key for e in entries) for key in CATEGORIES},
            "status": {key: sum(e["status"] == key for e in entries) for key in STATUSES}}


def href(relative):
    return html.escape(quote(str(relative).replace("\\", "/"), safe="/"), quote=True)


def esc(value):
    return html.escape(str(value), quote=True)


def write_html(path, manifest):
    summary = manifest["counts"]
    cards = []
    for entry in manifest["entries"]:
        files = entry["files"]
        links = "".join(f'<a href="{href(files[p]["path"])}">{esc(label)}</a>' for p, label in
                        [("预览/整体.png", "整体"), ("预览/反侧.png", "反侧"), ("预览/正面.png", "正面"),
                         ("预览/顶部.png", "顶部"), ("回读预览/整体.png", "GLB回读"), ("模型检查.json", "检查回执")])
        notes = "".join(f"<li>{esc(note)}</li>" for note in entry["observations"])
        limits = "".join(f"<li>{esc(note)}</li>" for note in entry["limitations"])
        search = " ".join([entry["name"], entry["variant"], *entry["observations"], *entry["limitations"]]).casefold()
        cards.append(f'''<article class="card" data-category="{entry['category']}" data-status="{entry['status']}" data-search="{esc(search)}">
<a class="preview" href="{href(files['预览/整体.png']['path'])}"><img loading="lazy" src="{href(files['预览/整体.png']['path'])}" alt="{esc(entry['name'])}整体预览"></a>
<div class="body"><div class="meta"><span>{CATEGORIES[entry['category']]} · {esc(entry['variant'])}</span><span class="badge {entry['status']}">{STATUSES[entry['status']]}</span></div>
<h2>{esc(entry['name'])}</h2><p class="geometry">{esc(entry['geometry'].get('三角形数', '未记录'))} 三角形 · {entry['reviewed_image_count']}/5 图像指纹已复核</p>
<div class="downloads"><a href="{href(files['model.glb']['path'])}">GLB 文件 ↗</a><a href="{href(files['model.blend']['path'])}">Blender 工程 ↗</a></div>
<details><summary>预览、观察与限制</summary><nav class="links">{links}</nav><ul>{notes}</ul>{'<h3>限制</h3><ul>'+limits+'</ul>' if limits else ''}</details></div></article>''')
    document = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>美军载具模型库</title><style>
:root{color-scheme:dark;--bg:#10171c;--panel:#19242c;--line:#30434e;--muted:#aab9c3;--text:#edf3f6;--accent:#8ed1c2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1480px;margin:auto;padding:40px 28px}header{border-bottom:1px solid var(--line);padding-bottom:26px}.eyebrow{color:var(--accent);letter-spacing:.15em;font-size:12px}h1{font-size:36px;line-height:1.25;margin:10px 0 16px}p{margin:8px 0}.muted,.geometry{color:var(--muted)}.stats{display:flex;flex-wrap:wrap;gap:12px;margin:22px 0}.stat{min-width:145px;padding:12px 18px;background:var(--panel);border:1px solid var(--line);border-radius:8px}.stat strong{font-size:26px;margin-right:8px}.notice{max-width:1100px;color:var(--muted);font-size:13px}.toolbar{display:flex;flex-wrap:wrap;align-items:end;gap:14px;padding:26px 0 14px}label{display:grid;gap:6px;color:var(--muted);font-size:13px}input,select{background:var(--panel);border:1px solid var(--line);color:var(--text);border-radius:6px;padding:10px;font:inherit}input{width:min(420px,78vw)}input:focus,select:focus,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}.result{color:var(--muted);font-size:13px;margin:0 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(295px,1fr));gap:20px}.card{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--panel)}.card[hidden]{display:none}.preview{display:block;background:#87939c}.preview img{width:100%;aspect-ratio:1.25;object-fit:contain;display:block}.body{padding:18px}.meta{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:var(--muted)}.badge{white-space:nowrap;border:1px solid #477b6d;color:#a7dccb;border-radius:4px;padding:0 7px}.badge.partial{border-color:#967e4e;color:#ead194}h2{font-size:20px;margin:10px 0 5px;overflow-wrap:anywhere}.geometry{font-size:12px}.downloads{display:flex;gap:18px;padding:13px 0 10px}a{color:#9fd9cd;text-underline-offset:3px}.downloads a{font-weight:600}details{border-top:1px solid var(--line);padding-top:12px;font-size:12px;color:var(--muted)}summary{cursor:pointer;color:var(--text)}.links{display:flex;flex-wrap:wrap;gap:7px 13px;margin:12px 0}ul{padding-left:19px}h3{font-size:13px;color:#ead194}footer{margin-top:36px;border-top:1px solid var(--line);padding-top:18px;font-size:12px;color:var(--muted)}.empty{border:1px dashed var(--line);padding:30px;text-align:center} [hidden]{display:none!important}@media(max-width:600px){main{padding:24px 16px}h1{font-size:29px}.stat{min-width:125px;flex:1}}
</style></head><body><main><header><div class="eyebrow">本地研究资产 · 离线目录</div><h1>美军载具模型库</h1>
<p class="muted">批次 __BATCH__ · 生成于 __TIME__</p><div class="stats"><div class="stat"><strong>__TOTAL__</strong>条交付</div><div class="stat"><strong>__AIR__</strong>空中</div><div class="stat"><strong>__GROUND__</strong>地面</div><div class="stat"><strong>__PASSED__</strong>通过</div><div class="stat"><strong>__PARTIAL__</strong>待修参考</div></div>
<div class="notice"><p>机体版保留基础预制体，装配版合入已选炮塔、外挂或部件；每张卡片标明具体 variant。待修参考与通过项分目录保存，只用于问题定位和后续修复。</p><p>全部为静态模型，无完整动画。Unity 自定义 Shader 为近似还原；尚未验收引擎集成、设备性能或真实载具尺寸。此页提供预览与文件链接，GLB 需在兼容的软件中打开。</p></div></header>
<section aria-label="筛选模型"><div class="toolbar"><label>搜索型号或观察<input id="search" type="search" placeholder="例如 US_A10、装配、贴图" autocomplete="off"></label><label>分类<select id="category"><option value="all">全部分类</option><option value="air">空中</option><option value="ground">地面</option></select></label><label>状态<select id="status"><option value="all">全部状态</option><option value="passed">通过</option><option value="partial">待修参考</option></select></label></div><p class="result" id="result" aria-live="polite"></p></section>
<section class="grid" aria-label="型号列表">__CARDS__</section><p id="empty" class="empty" hidden>没有符合条件的型号，请调整搜索或筛选。</p><footer><a href="模型清单.json">模型清单 JSON</a> · <a href="模型清单.csv">CSV 清单</a> · <a href="模型库核验.json">复制与指纹核验</a> · <a href="README.md">使用说明</a><p>本页、预览及文件均为本地相对链接，无在线依赖。通过仅涵盖交付选择所对应的结构回执与已审图像，不代表游戏运行逻辑或版权授权结论。</p></footer></main>
<script>const cards=[...document.querySelectorAll('.card')],search=document.querySelector('#search'),category=document.querySelector('#category'),status=document.querySelector('#status');function filter(){const q=search.value.trim().toLocaleLowerCase();let n=0;for(const card of cards){const show=(!q||card.dataset.search.includes(q))&&(category.value==='all'||card.dataset.category===category.value)&&(status.value==='all'||card.dataset.status===status.value);card.hidden=!show;if(show)n++}document.querySelector('#result').textContent=`显示 ${n} / ${cards.length} 条交付`;document.querySelector('#empty').hidden=n>0}search.addEventListener('input',filter);category.addEventListener('change',filter);status.addEventListener('change',filter);filter();</script></body></html>'''
    values = {"BATCH": esc(manifest["batch"]), "TIME": esc(manifest["generated_at"]), "TOTAL": summary["total"],
              "AIR": summary["category"]["air"], "GROUND": summary["category"]["ground"],
              "PASSED": summary["status"]["passed"], "PARTIAL": summary["status"]["partial"], "CARDS": "\n".join(cards)}
    # 单次替换模板标记，避免型号或观察中的文字被当成模板再次处理。
    document = re.sub(r"__(BATCH|TIME|TOTAL|AIR|GROUND|PASSED|PARTIAL|CARDS)__", lambda match: str(values[match[1]]), document)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(document)


def write_catalogs(output, manifest):
    write_json(output / "模型清单.json", manifest)
    with (output / "模型清单.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["型号", "分类", "状态", "版本", "目录", "Blend", "GLB", "观察", "限制"])
        for e in manifest["entries"]:
            values = [e["name"], CATEGORIES[e["category"]], STATUSES[e["status"]], e["variant"], e["directory"],
                      e["files"]["model.blend"]["path"], e["files"]["model.glb"]["path"], "；".join(e["observations"]), "；".join(e["limitations"])]
            writer.writerow(["'" + value if value.startswith(("=", "+", "-", "@")) else value for value in values])
    summary = manifest["counts"]
    readme = f'''# 美军载具模型库

批次：{manifest['batch']}。生成时间：{manifest['generated_at']}。

共 {summary['total']} 条交付：空中 {summary['category']['air']}，地面 {summary['category']['ground']}；通过 {summary['status']['passed']}，待修参考 {summary['status']['partial']}。

## 浏览与打开

直接打开 [批量导出目录.html](批量导出目录.html)，可离线搜索型号、按分类和状态筛选。页面显示静态预览，点击 GLB 或 Blender 工程链接后使用相应软件打开。

通过项位于 `空中/型号/版本/` 或 `地面/型号/版本/`；部分通过项单独放在 `待修参考/空中/型号/版本/` 或对应地面目录。每件包含 `model.blend`、`model.glb`、四视角、GLB 回读整体图与三份检查 JSON。路径、字节数与 SHA256 记录在 [模型清单.json](模型清单.json)，表格入口为 [模型清单.csv](模型清单.csv)。

## 版本和限制

- 机体版是基础预制体；装配版包含当前交付选择指定的炮塔、外挂或部件，均以条目的 `variant` 和观察为准。
- 待修参考保留已知问题，仅用于查阅、定位和修复，不能作为通过模型使用。具体限制见型号卡片与清单。
''' + "\n".join("- " + note for note in GENERAL_LIMITATIONS) + '''

## 核验范围

生成前核对本批次 `交付选择.json`、结构检查回执和两份载荷的回执 SHA256。通过项必须具备五张当前图像的审核 SHA256；待修项若提供审核指纹，同样逐一核对。复制后重新计算全部已复制文件的 SHA256，结果见 [模型库核验.json](模型库核验.json)。原始检查 JSON 中可能保留生成时的绝对输入路径，模型库文件路径以本清单中的相对路径为准。

本模型库未包含原始游戏包、第三方工具或完整中间缓存。交付选择与回执是可追溯记录，结构通过、静态视觉通过与运行验证分别判断。移动模型库时应保留整个目录结构。
'''
    with (output / "README.md").open("x", encoding="utf-8") as stream:
        stream.write(readme)
    write_html(output / "批量导出目录.html", manifest)


def generate_library(batch, output_name="美军模型库"):
    started = time.time()
    batch = Path(batch).resolve()
    output_name = component(output_name, "输出目录名")
    output = inside(batch, output_name, "模型库输出")
    if output.exists():
        raise FileExistsError(f"模型库输出已存在，拒绝覆盖：{output}")
    selection_path = inside(batch, "交付选择.json", "交付选择")
    selection_hash = sha256(selection_path)
    selection = read_json(selection_path)
    plans = preflight(batch, output, selection)
    if sha256(selection_path) != selection_hash:
        raise ValueError("核验期间交付选择发生改变")
    output.mkdir(exist_ok=False)
    verification = {"batch": batch.name, "passed": False, "selection_sha256": selection_hash,
                    "scope": "按交付选择核验结构回执、载荷与当前审核图像；复制后重新计算文件SHA256", "entries": []}
    try:
        copy_verified(selection_path, output / "交付选择.json", {"sha256": selection_hash, "bytes": selection_path.stat().st_size})
        entries = []
        for plan in plans:
            records = {}
            for relative, record in plan["files"].items():
                destination = output / plan["destination"] / relative
                copy_verified(record["source"], destination, record)
                records[relative] = {"path": destination.relative_to(output).as_posix(), "bytes": record["bytes"], "sha256": record["sha256"]}
            e = {key: plan[key] for key in ("name", "variant", "category", "status", "source_output", "observations", "limitations", "reviewed_image_count", "geometry")}
            e.update(directory=plan["destination"].as_posix(), files=records)
            e["review_evidence"] = {records[p]["path"]: records[p]["sha256"] for p in plan["reviewed_image_files"]}
            entries.append(e)
            verification["entries"].append({"name": e["name"], "variant": e["variant"], "status": e["status"],
                                            "files_verified": len(records), "reviewed_images_verified": e["reviewed_image_count"], "passed": True})
            print(json.dumps({"型号": e["name"], "版本": e["variant"], "状态": e["status"], "复制核验": True}, ensure_ascii=False), flush=True)
        manifest = {"schema_version": 1, "batch": batch.name, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "counts": counts(entries), "general_limitations": GENERAL_LIMITATIONS, "entries": entries}
        write_catalogs(output, manifest)
        verification.update(passed=True, counts=manifest["counts"], elapsed_seconds=round(time.time() - started, 3))
        verification["catalogs"] = {name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)} for name in
                                    ("交付选择.json", "模型清单.json", "模型清单.csv", "README.md", "批量导出目录.html")}
        write_json(output / "模型库核验.json", verification)
        return output
    except Exception as error:
        verification.update(error=str(error), elapsed_seconds=round(time.time() - started, 3))
        failure = output / "模型库核验.json"
        if not failure.exists():
            write_json(failure, verification)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default=DEFAULT_BATCH, help=f"本地批次编号，default 表示 {DEFAULT_BATCH}")
    parser.add_argument("--output-name", default="美军模型库", help="本批次中新建的单层目录名，拒绝覆盖")
    args = parser.parse_args()
    batch_name = DEFAULT_BATCH if args.batch == "default" else component(args.batch, "批次编号")
    batches = (ROOT / "local-data/断箭/批次").resolve()
    batch = inside(batches, batch_name, "批次")
    output = generate_library(batch, args.output_name)
    print(json.dumps({"模型库": str(output), "核验通过": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"生成模型库失败：{error}", file=sys.stderr)
        raise SystemExit(2)
