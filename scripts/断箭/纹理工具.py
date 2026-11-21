"""只读索引 Granite GTS，并调用官方工具按名称提取指定纹理。

格式依据 Nenkai/GraniteTextureReader 1.1.5 的 TileSetFile 与 GDEXItem。
仅读取 GTS 元数据生成索引，不加载大型 GTP；提取时显式指定本地输出目录。
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "local-data/断箭/本地配置.json"
CACHE = ROOT / "local-data/断箭/纹理缓存"
DATA_TYPES = ["R8G8B8_SRGB", "R8G8B8A8_SRGB", "X8Y8Z0_TANGENT", "R8G8B8_LINEAR", "R8G8B8A8_LINEAR", "X8", "X8Y8", "X8Y8Z8", "X8Y8Z8W8"]
UNIT_V3_TILESET = "f51bbaf7a40a0ab4909c16ff519cc9cf"


def local_output(path):
    """只允许在项目约定的本地资料或纹理研究临时目录写入。"""
    path = Path(path).resolve()
    if not any(path.is_relative_to(base.resolve()) for base in [ROOT / "local-data/断箭", ROOT / "tmp/断箭/纹理研究"]):
        raise ValueError("纹理输出必须位于本地断箭资料或纹理研究临时目录")
    path.mkdir(parents=True, exist_ok=True)
    return path


def gdex_item(data, position=0):
    """解析官方 GDEX 项结构，保留不认识的数据类型的字节表示。"""
    if position + 8 > len(data):
        raise ValueError("GDEX 项头越界")
    tag = data[position:position + 4].decode("ascii", errors="replace").rstrip()
    kind, flags = data[position + 4:position + 6]
    length_bytes = 6 if flags & 1 else 2
    start = position + 6 + length_bytes
    length = int.from_bytes(data[position + 6:start], "little")
    end = start + length
    if end > len(data):
        raise ValueError(f"GDEX 数据越界：{tag}")
    value = None
    if kind == 1:
        value = []
        cursor = start
        while cursor < end:
            child, next_cursor = gdex_item(data, cursor)
            if next_cursor <= cursor:
                raise ValueError("GDEX 解析未前进")
            value.append(child)
            cursor = next_cursor
    elif kind == 2:
        value = data[start:end].decode("utf-16-le").rstrip("\0")
    elif kind == 3:
        value = struct.unpack_from("<i", data, start)[0]
    elif kind == 8:
        value = list(struct.unpack_from("<" + "i" * (length // 4), data, start))
    else:
        value = {"字节数": length, "原始十六进制": data[start:end].hex()}
    return {"tag": tag, "type": kind, "value": value}, (end + 3) & ~3


def child(item, tag):
    return next((part for part in item["value"] if part["tag"] == tag), None)


def fields(item):
    return {part["tag"]: part["value"] for part in item["value"]}


def index_tileset(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        header = stream.read(192)
        if header[:4] != b"GRPG" or struct.unpack_from("<I", header, 4)[0] != 6:
            raise ValueError("仅支持已核验的第 6 版 GTS")
        layer_count, layer_offset = struct.unpack_from("<IQ", header, 28)
        metadata_size, metadata_offset = struct.unpack_from("<IQ", header, 144)
        stream.seek(metadata_offset)
        metadata = stream.read(metadata_size)
        if len(metadata) != metadata_size:
            raise ValueError("GTS 元数据读取不完整")
        tree, _ = gdex_item(metadata)
        stream.seek(layer_offset)
        layers = [dict(zip(["data_type", "default_color"], struct.unpack("<II", stream.read(8)))) for _ in range(layer_count)]
    atlas = child(tree, "ATLS")
    textures = child(atlas, "TXTS")
    metadata_layers = child(tree, "LINF")
    for index, layer in enumerate(layers):
        layer["index"] = index
        layer["data_type_name"] = DATA_TYPES[layer["data_type"]] if layer["data_type"] < len(DATA_TYPES) else "未收录类型"
        layer["metadata"] = fields(metadata_layers["value"][index])
    entries = []
    for index, texture in enumerate(textures["value"]):
        item = fields(texture)
        entries.append({"index": index, "name": item.get("NAME"), "width": item.get("WDTH"), "height": item.get("HGHT"), "x": item.get("XXXX"), "y": item.get("YYYY"), "fields": item})
    project = child(tree, "PROJ")
    return {"tileset": path.name, "version": 6, "layer_count": layer_count, "layers": layers, "metadata_bytes": metadata_size, "has_embedded_project": bool(project and project["value"]), "texture_count": len(entries), "textures": entries}


def make_index(game_directory, output):
    directory = Path(game_directory) / "BrokenArrow_Data/StreamingAssets"
    output = local_output(output)
    items = [index_tileset(path) for path in sorted(directory.glob("*.gts"))]
    result = {"source": "GraniteTextureReader 1.1.5 格式代码与本地 GTS 元数据", "layer_semantics": "图层用途需按实际材质和图像确认，不沿用 CLI 通用示例", "tilesets": items}
    (output / "纹理索引.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@contextmanager
def texture_process_lock(timeout=900):
    """官方读取 GTP 时采用独占共享模式，因此跨进程串行调用提取程序。"""
    import msvcrt

    lock = local_output(CACHE) / "纹理提取.lock"
    with lock.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.time() + timeout
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("等待其他纹理提取进程释放 GTP 超时")
                # 绝对截止时间控制等待，短暂轮询不用于累计计时。
                time.sleep(min(0.2, remaining))
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def extract_texture(tileset, name, layer=-1, output=None, timeout=900):
    """复用成功缓存；失败目录保留，新尝试写入独立重试目录。"""
    tileset = Path(tileset).resolve()
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    default = CACHE / tileset.stem / slug / str(layer)
    with texture_process_lock(timeout):
        if output is None:
            candidates = [default] + sorted(default.parent.glob(f"{layer}-重试-*"))
            for candidate in candidates:
                receipt = candidate / "提取记录.json"
                if not receipt.is_file():
                    continue
                previous = json.loads(receipt.read_text(encoding="utf-8"))
                if previous.get("success") and previous.get("name") == name and previous.get("tileset") == tileset.name and previous.get("layer") == layer and all((candidate / item["file"]).is_file() for item in previous.get("files", [])):
                    return {**previous, "output_dir": str(candidate.resolve())}
            output = default
            if default.exists() and any(default.iterdir()):
                index = 1
                while True:
                    candidate = default.with_name(f"{layer}-重试-{index:03d}")
                    if not candidate.exists():
                        output = candidate
                        break
                    index += 1
        result = _extract_texture_once(tileset, name, layer, output, timeout)
        return {**result, "output_dir": str(Path(output).resolve())}


def _extract_texture_once(tileset, name, layer=-1, output=None, timeout=900):
    """按官方内部纹理名提取；退出码和实际图像文件共同判断成功。"""
    tileset = Path(tileset).resolve()
    tile_index = index_tileset(tileset)
    entry = next((item for item in tile_index["textures"] if item["name"] == name), None)
    if entry is None:
        raise ValueError("指定内部名称不属于该 GTS")
    if layer < -1 or layer >= tile_index["layer_count"]:
        raise ValueError("图层编号越界")
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    output = local_output(output or (CACHE / tileset.stem / slug / str(layer)))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    exe = (ROOT / config["tools"]["GraniteTextureReader"]).resolve()
    runtime = (ROOT / config["tools"]["dotnet_runtime"]).resolve()
    env = os.environ.copy()
    env["DOTNET_ROOT"] = env["DOTNET_ROOT_X64"] = str(runtime)
    receipt = output / "提取记录.json"
    if receipt.is_file():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if previous.get("name") == name and previous.get("tileset") == tileset.name and previous.get("layer") == layer and previous.get("success") and all((output / item["file"]).is_file() for item in previous["files"]):
            return previous
        raise ValueError("输出目录已有不同或未完成的提取记录，请使用新的输出目录")
    if any(path.suffix.lower() == ".tga" for path in output.iterdir()):
        raise ValueError("输出目录已有未登记图像，请使用新目录")
    started = time.time()
    command = [str(exe), "extract", "-t", str(tileset), "-f", name, "-l", str(layer), "-o", str(output)]
    run = subprocess.run(command, env=env, cwd=output, capture_output=True, timeout=timeout)
    stdout = run.stdout.decode("utf-8", errors="replace")
    stderr = run.stderr.decode("utf-8", errors="replace")
    (output / "工具输出.log").write_text(stdout + "\n" + stderr, encoding="utf-8")
    files = []
    for image in sorted(output.glob("*.tga")):
        with image.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append({"file": image.name, "bytes": image.stat().st_size, "sha256": digest})
    expected_count = tile_index["layer_count"] if layer == -1 else 1
    success = run.returncode == 0 and "ERROR:" not in stdout and "ERROR:" not in stderr and len(files) == expected_count
    result = {"tileset": tileset.name, "gts_version": tile_index["version"], "name": name, "layer": layer, "width": entry["width"], "height": entry["height"], "elapsed_seconds": round(time.time() - started, 3), "exit_code": run.returncode, "success": success, "files": files, "tool_version": "1.1.5", "output_dir": str(output)}
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not success:
        raise RuntimeError(f"纹理提取未成功，请查看 {output} 下的日志")
    return result


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_shader_layout(shader_typetree):
    """从 Shader 的实际序列化属性确认虚拟堆栈层，未知语义不通过。"""
    form = shader_typetree.get("m_ParsedForm", shader_typetree)
    layers = []
    for prop in form.get("m_PropInfo", {}).get("m_Props", []):
        for attribute in prop.get("m_Attributes", []):
            match = re.fullmatch(r"TextureStack\.(.+)\((\d+)\)", attribute)
            if match:
                layers.append({"property": prop["m_Name"], "description": prop.get("m_Description", ""), "stack": match[1], "index": int(match[2])})
    normal = [item for item in layers if item["description"].lower() == "normal"]
    mask = [item for item in layers if item["description"].lower() == "mask"]
    compatible = len(normal) == len(mask) == 1 and normal[0]["index"] == 0 and mask[0]["index"] == 1 and normal[0]["stack"] == mask[0]["stack"]
    # 当前通道公式已由 UnitV3 的实际编译程序核验，其他着色器仅同名层还不够。
    models = {"Shader Graphs/UnitV3": "UnitV3", "Shader Graphs/UnitV4_Camo": "UnitV4_Camo"}
    model = models.get(form.get("m_Name"))
    return {"shader_name": form.get("m_Name", ""), "shader_model": model, "layers": layers, "normal_index": normal[0]["index"] if len(normal) == 1 else None, "mask_index": mask[0]["index"] if len(mask) == 1 else None, "matching_layer_layout": compatible, "verified_unitv3_channels": compatible and model == "UnitV3", "verified_normal_mask_channels": compatible and model is not None, "material_difference": "迷彩使用Mask.B和UV1；AO另受_AOClamp下限约束" if model == "UnitV4_Camo" else None}


def convert_unity_normal(path):
    """将 Unity 的 RG 或 AG 打包法线转为切线空间 XYZ，不覆盖输入。"""
    import numpy as np
    from PIL import Image

    path = Path(path).resolve()
    digest = file_sha256(path)
    output = local_output(CACHE / "普通法线" / digest)
    result_path = output / "法线.png"
    receipt = output / "法线转换记录.json"
    if result_path.is_file() and receipt.is_file():
        return str(result_path)
    raw = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0
    # Unity UnpackNormalMapRGorAG：RGBA 的 R*A 同时兼容 RG 与 AG 储存。
    x = raw[:, :, 0] * raw[:, :, 3] * 2.0 - 1.0
    y = raw[:, :, 1] * 2.0 - 1.0
    z = np.sqrt(np.maximum(1.0 - np.minimum(x*x + y*y, 1.0), 0.0))
    encoded = np.clip(np.stack([x, y, z], axis=2) * 0.5 + 0.5, 0.0, 1.0)
    Image.fromarray(np.rint(encoded * 255).astype(np.uint8), "RGB").save(result_path)
    record = {"algorithm": "Unity_RGorAG_XYZ_v1", "source_sha256": digest, "output_sha256": file_sha256(result_path), "encoding": "XYZ切线空间，正Y，非颜色数据", "formula": "X=2*R*A-1；Y=2*G-1；Z=sqrt(1-min(X²+Y²,1))", "evidence": "Unity UnpackNormalMapRGorAG；实际 UnitV3 GBuffer 程序5420 的法线采样与重建"}
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(result_path)


def prepare_material_maps(tileset, item_name, shader_model="UnitV3"):
    """为已核验的主 GTS 生成法线与线性遮罩，保留着色器模型区别。"""
    import numpy as np
    from PIL import Image

    tileset = Path(tileset).resolve()
    shader_model = {"Shader Graphs/UnitV3": "UnitV3", "Shader Graphs/UnitV4_Camo": "UnitV4_Camo"}.get(shader_model, shader_model)
    if shader_model not in {"UnitV3", "UnitV4_Camo"}:
        raise ValueError("该着色器的通道公式尚未核验")
    if tileset.stem != UNIT_V3_TILESET:
        raise ValueError("该 GTS 尚未核验图层用途，不能套用 UnitV3 转换")
    index = index_tileset(tileset)
    if index["layer_count"] != 2 or [layer["metadata"]["TECT"] for layer in index["layers"]] != ["UNORM", "SRGB"]:
        raise ValueError("主 GTS 图层布局已变化，需要重新核验")
    extracted = extract_texture(tileset, item_name)
    raw_dir = Path(extracted["output_dir"])
    normal = convert_unity_normal(raw_dir / f"{item_name}_0.tga")
    output = local_output(CACHE / tileset.stem / item_name / "派生贴图")
    source_mask = raw_dir / f"{item_name}_1.tga"
    mask = output / "遮罩线性.png"
    original = output / "遮罩原始.png"
    receipt = output / "通道转换记录.json"
    if not (mask.is_file() and original.is_file() and receipt.is_file()):
        image = Image.open(source_mask).convert("RGBA")
        image.save(original)
        raw = np.asarray(image, dtype=np.float32) / 255.0
        rgb = raw[:, :, :3]
        raw[:, :, :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        Image.fromarray(np.rint(np.clip(raw, 0.0, 1.0) * 255).astype(np.uint8), "RGBA").save(mask)
        record = {"algorithm": "Unit主GTS遮罩_v1", "source_sha256": file_sha256(source_mask), "output_sha256": file_sha256(mask), "normal": normal, "mask_encoding": "linear", "mask_channels": {"R": "Metallic，乘材质_Metal", "G": "AO，乘材质_AOStrange；迷彩着色器另有_AOClamp下限", "B": "UnitV3未使用；UnitV4_Camo为迷彩叠加强度", "A": "Smoothness，乘材质_Smoothness；Roughness=1-Smoothness"}, "rgb_conversion": "GTS图层SRGB，按标准分段函数转线性；Alpha保持原数值", "precision": "8位PNG；原始TGA与原始PNG均保留", "evidence": "UnitV3程序5420与UnitV4_Camo程序788的属性和实际GBuffer DXBC指令"}
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"normal": normal, "mask": str(mask.resolve()), "mask_original": str(original.resolve()), "mask_encoding": "linear", "shader_model": shader_model, "mask_channels": {"metallic": "R", "occlusion": "G", "smoothness": "A", "camo": "B" if shader_model == "UnitV4_Camo" else None}, "normal_encoding": "XYZ正Y"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="仅读取 GTS 元数据建立索引")
    index.add_argument("--output", type=Path, default=ROOT / "tmp/断箭/纹理研究")
    extract = commands.add_parser("extract", help="按内部名称提取一张纹理的指定图层")
    extract.add_argument("--tileset", required=True, type=Path)
    extract.add_argument("--name", required=True)
    extract.add_argument("--layer", type=int, default=-1)
    extract.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "index":
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        result = make_index(config["game_directory"], args.output)
        print(json.dumps([{key: value for key, value in item.items() if key != "textures"} for item in result["tilesets"]], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(extract_texture(args.tileset, args.name, args.layer, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
