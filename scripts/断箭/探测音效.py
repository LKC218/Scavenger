"""只读固定音频输入，并用随附 FMOD 的隔离副本探测事件元数据。"""

import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def local_batch(name):
    parent = (ROOT / "local-data/断箭/批次").resolve()
    path = (parent / name).resolve()
    if path == parent or not path.is_relative_to(parent):
        raise ValueError("输出必须位于本项目批次目录")
    return path


def resolve_input(batch, path):
    """只在原输入已退役时，使用显式迁移表中的同摘要元数据副本。"""
    path = Path(path)
    if path.is_file():
        return path
    migration = batch / "输入迁移.json"
    if not migration.is_file():
        raise FileNotFoundError(path)
    rows = json.loads(migration.read_text(encoding="utf-8"))["files"]
    matches = [row for row in rows if Path(row["original_path"]).resolve() == path.resolve()]
    if len(matches) != 1:
        raise ValueError("缺失输入没有唯一的显式迁移记录：" + str(path))
    row = matches[0]
    retained = Path(row["retained_path"]).resolve()
    parent = (ROOT / "local-data/断箭/批次").resolve()
    if not retained.is_relative_to(parent) or not path.resolve().is_relative_to(parent):
        raise ValueError("迁移记录超出本地批次边界")
    if retained.stat().st_size != row["bytes"] or digest(retained) != row["sha256"]:
        raise ValueError("迁移元数据摘要不符：" + str(retained))
    return retained


def riff(path):
    chunks = []
    with path.open("rb") as stream:
        head = stream.read(12)
        if head[:4] != b"RIFF" or head[8:] != b"FEV ":
            raise ValueError("不是 RIFF/FEV 容器")
        while stream.tell() + 8 <= path.stat().st_size:
            offset = stream.tell()
            tag, size = struct.unpack("<4sI", stream.read(8))
            if offset + 8 + size > path.stat().st_size:
                raise ValueError("块长度超过文件")
            item = {"tag": tag.decode("ascii"), "offset": offset, "bytes": size}
            if tag == b"FMT ":
                item["hex"] = stream.read(size).hex()
            chunks.append(item)
            stream.seek(offset + 8 + size + size % 2)
    return {"header": head.hex(), "chunks": chunks}


class GUID(C.Structure):
    _fields_ = [("data", C.c_ubyte * 16)]


class Parameter(C.Structure):
    # 对应已核对的 FMOD 2.01 C 接口，尾部预留空间兼容探测。
    _fields_ = [("name", C.c_char_p), ("id", C.c_uint * 2),
                ("minimum", C.c_float), ("maximum", C.c_float),
                ("default", C.c_float), ("type", C.c_int),
                ("flags", C.c_uint), ("reserved", C.c_byte * 32)]


class FMOD:
    def __init__(self, dll):
        self.dll = C.WinDLL(str(dll))
        self.system = C.c_void_p()
        self.core = C.c_void_p()
        self.log = []
        self.call("FMOD_Studio_System_Create", C.byref(self.system), 0x00020100)
        self.call("FMOD_Studio_System_GetCoreSystem", self.system, C.byref(self.core))
        version = C.c_uint()
        self.call("FMOD_System_GetVersion", self.core, C.byref(version))
        self.version = hex(version.value)

    def call(self, name, *args):
        code = getattr(self.dll, name)(*args)
        if code:
            raise RuntimeError(f"{name}: FMOD_RESULT={code}")
        return code

    def initialize(self):
        self.call("FMOD_System_SetOutput", self.core, 4)  # NOSOUND_NRT，无声卡输出
        self.call("FMOD_Studio_System_Initialize", self.system, 256, 4, 0, None)

    def load(self, path):
        bank = C.c_void_p()
        self.call("FMOD_Studio_System_LoadBankFile", self.system,
                  str(path).encode("utf-8"), 0, C.byref(bank))
        return bank

    def text(self, name, pointer):
        data, length = C.create_string_buffer(4096), C.c_int()
        self.call(name, pointer, data, len(data), C.byref(length))
        return data.value.decode("utf-8")

    def events(self, bank, bank_name):
        count = C.c_int()
        self.call("FMOD_Studio_Bank_GetEventCount", bank, C.byref(count))
        pointers = (C.c_void_p * count.value)()
        self.call("FMOD_Studio_Bank_GetEventList", bank, pointers, count.value, C.byref(count))
        result = []
        for pointer in pointers:
            event = C.c_void_p(pointer)
            guid, length, one, params = GUID(), C.c_int(), C.c_int(), C.c_int()
            self.call("FMOD_Studio_EventDescription_GetID", event, C.byref(guid))
            self.call("FMOD_Studio_EventDescription_GetLength", event, C.byref(length))
            self.call("FMOD_Studio_EventDescription_IsOneshot", event, C.byref(one))
            self.call("FMOD_Studio_EventDescription_GetParameterDescriptionCount", event, C.byref(params))
            descriptions = []
            for index in range(params.value):
                value = Parameter()
                self.call("FMOD_Studio_EventDescription_GetParameterDescriptionByIndex", event, index, C.byref(value))
                descriptions.append({"name": value.name.decode("utf-8"), "id": list(value.id),
                                     "minimum": value.minimum, "maximum": value.maximum,
                                     "default": value.default, "type": value.type, "flags": value.flags})
            result.append({"bank": bank_name, "path": self.text("FMOD_Studio_EventDescription_GetPath", event),
                           "guid": str(uuid.UUID(bytes_le=bytes(guid.data))), "length_ms": length.value,
                           "oneshot": bool(one.value), "parameters": descriptions})
        return result

    def close(self):
        if self.system:
            self.call("FMOD_Studio_System_Release", self.system)
            self.system = C.c_void_p()


def resolve_missing(batch):
    """对缺项调用原生路径查找，并仅扫描所有bank的事件元数据。"""
    mapping = json.loads((batch / "型号音效映射.json").read_text(encoding="utf-8"))
    paths = sorted({ref["source_event_path"] if "source_event_path" in ref else ref["event_path"]
                    for model in mapping["models"] for ref in model["references"]
                    if ref["mapping_status"] == "event_not_indexed" or ref.get("resolution") == "native_guid"})
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8-sig"))
    directory = Path(config["game_directory"]) / "BrokenArrow_Data/StreamingAssets"
    fmod = FMOD(ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll")
    fmod.initialize()
    events, receipts, banks = [], [], []
    try:
        strings_bank = fmod.load(directory / "Master.strings.bank")
        count = C.c_int()
        fmod.call("FMOD_Studio_Bank_GetStringCount", strings_bank, C.byref(count))
        strings = []
        for ordinal in range(count.value):
            guid, buffer, length = GUID(), C.create_string_buffer(4096), C.c_int()
            fmod.call("FMOD_Studio_Bank_GetStringInfo", strings_bank, ordinal, C.byref(guid), buffer, 4096, C.byref(length))
            strings.append({"path": buffer.value.decode("utf-8"), "guid": str(uuid.UUID(bytes_le=bytes(guid.data)))})
        save(batch / "证据/strings全路径索引.json", strings)
        for path in sorted(directory.glob("*.bank")):
            if path.name == "Master.strings.bank":
                continue
            receipts.append({"path": str(path), "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns, "sha256": digest(path)})
            try:
                bank = fmod.load(path)
                items = fmod.events(bank, path.name)
                events.extend(items)
                banks.append({"bank": path.name, "event_count": len(items), "status": "passed"})
            except Exception as error:
                banks.append({"bank": path.name, "status": "failed", "error": str(error)})
        checks = []
        for path in paths:
            guid = GUID()
            code = fmod.dll.FMOD_Studio_System_LookupID(fmod.system, path.encode(), C.byref(guid))
            value = str(uuid.UUID(bytes_le=bytes(guid.data))) if code == 0 else None
            checks.append({"requested_path": path, "lookup_result": code, "guid": value,
                           "canonical_events": [event for event in events if event["guid"] == value]})
        save(batch / "证据/事件路径原生复核.json", {"banks": banks, "files": receipts, "resolutions": checks})
    finally:
        fmod.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--resolve-missing", action="store_true")
    args = parser.parse_args()
    start = time.time()
    batch = local_batch(args.batch)
    if args.resolve_missing:
        resolve_missing(batch)
        return
    batch.mkdir(exist_ok=False)
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8-sig"))
    game = Path(config["game_directory"]).resolve()
    manifest = Path(config["steam_manifest"])
    build = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text()).group(1)
    if not args.batch.startswith("BA-" + build + "-"):
        raise ValueError("批次名与实际构建不符")
    static = local_batch("BA-26273833-001")
    source_files = [manifest]
    source_files += [game / "BrokenArrow_Data/StreamingAssets" / n for n in
                     ("Master.bank", "Master.strings.bank", "SFX_Unit.bank", "SFX_Weapon.bank",
                      "SFX_Unit_BB_DLC.bank", "SFX_Unit_DLC3.bank", "SFX_Weapon_BB_DLC.bank")]
    source_files += [game / "BrokenArrow_Data/Plugins/x86_64" / n for n in ("fmodstudio.dll", "resonanceaudio.dll")]
    source_files += [static / n for n in ("美军模型库/模型清单.json", "交付选择.json", "装配映射.json", "证据/装配/源配置核验.json")]
    receipts = []
    for path in source_files:
        receipts.append({"path": str(path), "bytes": path.stat().st_size,
                         "mtime_ns": path.stat().st_mtime_ns, "sha256": digest(path),
                         **({"riff": riff(path)} if path.suffix == ".bank" else {})})
    save(batch / "输入清单.json", {"build": build, "batch": args.batch, "static_batch": static.name,
         "created_at_epoch": start, "disk_free_bytes": shutil.disk_usage(ROOT).free, "files": receipts})
    runtime = ROOT / "local-data/tools/FMOD/2.01.11"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("fmodstudio.dll", "resonanceaudio.dll"):
        target = runtime / name
        source = game / "BrokenArrow_Data/Plugins/x86_64" / name
        if not target.exists():
            shutil.copyfile(source, target)
        if digest(source) != digest(target):
            raise ValueError("运行库副本摘要不符")
    fmod = None
    result = {"banks": [], "events": [], "errors": [], "output": "NOSOUND_NRT"}
    try:
        fmod = FMOD(runtime / "fmodstudio.dll")
        result["runtime_version"] = fmod.version
        fmod.initialize()
        for name in ("Master.strings.bank", "Master.bank", "SFX_Unit.bank", "SFX_Weapon.bank",
                     "SFX_Unit_BB_DLC.bank", "SFX_Unit_DLC3.bank", "SFX_Weapon_BB_DLC.bank"):
            try:
                bank = fmod.load(game / "BrokenArrow_Data/StreamingAssets" / name)
                events = fmod.events(bank, name)
                result["events"].extend(events)
                result["banks"].append({"name": name, "status": "passed", "events": len(events)})
            except Exception as error:
                result["banks"].append({"name": name, "status": "failed", "error": str(error)})
    except Exception as error:
        result["errors"].append(str(error))
    finally:
        if fmod:
            fmod.close()
        result["elapsed_seconds"] = time.time() - start
        save(batch / "证据/兼容性与事件索引.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "events"}, ensure_ascii=False, indent=2))
    print("事件数", len(result["events"]))


if __name__ == "__main__":
    main()
