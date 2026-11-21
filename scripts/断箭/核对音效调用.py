"""只读核对 IL2CPP 音效类型布局、目标方法与直接调用；不执行游戏代码。"""

import argparse
from bisect import bisect_right
import ctypes as C
import json
from pathlib import Path
import struct
import sys
import time

from 探测音效 import ROOT, digest, local_batch, save, resolve_input

TOOL = ROOT / "local-data/tools/Cpp2IL/2022.1.0-pre-release.21/net472"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_inputs(batch):
    manifest = read_json(batch / "输入清单.json")
    for row in manifest["files"]:
        if digest(resolve_input(batch, row["path"])) != row["sha256"]:
            raise ValueError("输入摘要变化：" + row["path"])
    game = Path(read_json(ROOT / "local-data/断箭/本地配置.json")["game_directory"])
    return game / "GameAssembly.dll", game / "BrokenArrow_Data/il2cpp_data/Metadata/global-metadata.dat"


def load_native(binary, metadata):
    sys.path.insert(0, str(TOOL))
    import clr
    clr.AddReference(str(TOOL / "LibCpp2IL.dll"))
    clr.AddReference(str(TOOL / "AssetRipper.Primitives.dll"))
    from LibCpp2IL import LibCpp2IlMain
    from AssetRipper.Primitives import UnityVersion

    LibCpp2IlMain.Settings.AllowManualMetadataAndCodeRegInput = False
    LibCpp2IlMain.LoadFromFile(str(binary), str(metadata), UnityVersion.Parse("6000.3.24f1"))
    return LibCpp2IlMain


def index_types(batch, binary, metadata):
    LibCpp2IlMain = load_native(binary, metadata)
    layouts, methods = [], []
    for t in LibCpp2IlMain.TheMetadata.typeDefs:
        # 所有方法保留地址索引；只展开与本次音效有关的字段布局。
        for method in t.Methods:
            pointer = int(method.MethodPointer)
            if pointer:
                methods.append({"pointer": pointer, "type": str(t.FullName), "name": str(method.Name),
                                "signature": str(method.HumanReadableSignature)})
        name = str(t.FullName)
        if not name.startswith(("BrokenArrow.", "Plugins.FMOD.")):
            continue
        fields = list(t.Fields)
        field_types = [str(f.FieldType) for f in fields]
        if not (any(w in name for w in ("Audio", "Sound", "Projectile", "Ammunition", "Weapon"))
                or any("SoundsPreset" in s for s in field_types)):
            continue
        layout = {"type": name, "type_index": int(t.TypeIndex.Value), "is_value_type": bool(t.IsValueType),
                  "fields": [], "methods": []}
        for i, field in enumerate(fields):
            is_static = bool(int(field.RawFieldType.Attrs) & 0x10)
            offset = LibCpp2IlMain.Binary.GetFieldOffsetFromIndex(t.TypeIndex, i, field.FieldIndex, t.IsValueType, is_static)
            layout["fields"].append({"name": str(field.Name), "type": field_types[i], "offset": int(offset), "static": is_static})
        layout["methods"] = [{"name": str(m.Name), "pointer": int(m.MethodPointer),
                              "signature": str(m.HumanReadableSignature)} for m in t.Methods]
        layouts.append(layout)
    save(batch / "证据/音效类型布局.json", {"unity_version": "6000.3.24f1", "metadata_version": float(LibCpp2IlMain.MetadataVersion),
                                        "type_count": len(LibCpp2IlMain.TheMetadata.typeDefs), "layouts": layouts})
    save(batch / "证据/原生方法地址索引.json", methods)
    print("类型布局", len(layouts), "有地址方法", len(methods), flush=True)


def resolve_globals(batch, binary, metadata, addresses, output):
    native = load_native(binary, metadata)
    rows = []
    for address in addresses:
        value = native.GetAnyGlobalByAddress(address)
        rows.append({"address": address, "hex_address": hex(address), "value": str(value) if value else None,
                     "kind": str(value.Type) if value else None})
    save(batch / "证据" / output, rows)
    print("元数据全局引用", len(rows), flush=True)


class Instruction(C.Structure):
    # 随 Cpp2IL 发布的 Capstone 4.0 cs_insn；实际运行先核对 ABI 版本。
    _fields_ = [("id", C.c_uint), ("address", C.c_uint64), ("size", C.c_uint16),
                ("bytes", C.c_ubyte * 16), ("mnemonic", C.c_char * 32),
                ("op_str", C.c_char * 160), ("detail", C.c_void_p)]


class NativeImage:
    def __init__(self, binary):
        self.data = binary.read_bytes()
        data = self.data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", data, pe + 4)[0] != 0x8664:
            raise ValueError("本脚本仅核对 AMD64 PE")
        count, optional_bytes = struct.unpack_from("<H12xH", data, pe + 6)
        optional = pe + 24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise ValueError("不是 PE32+")
        self.base = struct.unpack_from("<Q", data, optional + 24)[0]
        self.sections = []
        for i in range(count):
            at = optional + optional_bytes + i * 40
            name = data[at:at + 8].rstrip(b"\0").decode("ascii")
            vsize, va, size, raw = struct.unpack_from("<IIII", data, at + 8)
            flags = struct.unpack_from("<I", data, at + 36)[0]
            self.sections.append({"name": name, "rva": va, "virtual_bytes": vsize, "bytes": size, "raw": raw, "execute": bool(flags & 0x20000000)})
        exception_rva, exception_bytes = struct.unpack_from("<II", data, optional + 112 + 3 * 8)
        at = self.raw(self.base + exception_rva)
        self.unwind = sorted(struct.unpack_from("<III", data, at + i) for i in range(0, exception_bytes, 12))
        self.unwind_starts = [x[0] for x in self.unwind]
        self.unwind_groups = {}
        self.lib = C.CDLL(str(TOOL / "capstone.dll"))
        major, minor = C.c_int(), C.c_int()
        self.lib.cs_version(C.byref(major), C.byref(minor))
        if (major.value, minor.value) != (4, 0):
            raise ValueError("Capstone ABI 不符合已核对的 4.0")
        self.handle = C.c_size_t()
        self.lib.cs_open.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_size_t)]
        self.lib.cs_disasm.argtypes = [C.c_size_t, C.c_void_p, C.c_size_t, C.c_uint64, C.c_size_t, C.POINTER(C.POINTER(Instruction))]
        self.lib.cs_disasm.restype = C.c_size_t
        self.lib.cs_free.argtypes = [C.POINTER(Instruction), C.c_size_t]
        self.lib.cs_close.argtypes = [C.POINTER(C.c_size_t)]
        if self.lib.cs_open(3, 8, C.byref(self.handle)):
            raise RuntimeError("无法初始化 x86-64 反汇编器")

    def raw(self, address):
        rva = address - self.base
        for section in self.sections:
            if section["rva"] <= rva < section["rva"] + section["bytes"]:
                return section["raw"] + rva - section["rva"]
        raise ValueError("地址不在文件段内")

    def bounds(self, address, pointers):
        rva = address - self.base
        i = bisect_right(self.unwind_starts, rva) - 1
        if i >= 0 and rva < self.unwind[i][1]:
            root = self.unwind_root(self.unwind[i])
            if root[0] not in self.unwind_groups:
                start, end = root[:2]
                j = bisect_right(self.unwind_starts, start) - 1
                while j < len(self.unwind) and self.unwind[j][0] <= end + 16:
                    item = self.unwind[j]
                    if self.unwind_root(item)[0] == start:
                        end = max(end, item[1])
                    elif item[0] >= end:
                        break
                    j += 1
                self.unwind_groups[start] = (start, end)
            start, end = self.unwind_groups[root[0]]
            return self.base + start, self.base + end, "PE_EXCEPTION_DIRECTORY_CHAININFO"
        i = bisect_right(pointers, address)
        end = min(pointers[i] if i < len(pointers) else address + 256, address + 65536)
        return address, end, "NEXT_METADATA_METHOD_LIMIT_64K"

    def unwind_root(self, entry):
        visited = set()
        while entry[2] not in visited:
            visited.add(entry[2])
            at = self.raw(self.base + entry[2])
            version_flags, _, codes, _ = struct.unpack_from("<BBBB", self.data, at)
            if not ((version_flags >> 3) & 4):
                return entry
            extra = at + 4 + ((codes * 2 + 3) // 4) * 4
            entry = struct.unpack_from("<III", self.data, extra)
        raise ValueError("UNWIND_INFO 链出现循环")

    def decode(self, start, end):
        at = self.raw(start)
        code = self.data[at:at + end - start]
        pointer = C.POINTER(Instruction)()
        buffer = C.create_string_buffer(code)
        count = self.lib.cs_disasm(self.handle.value, buffer, len(code), start, 0, C.byref(pointer))
        try:
            return [{"address": int(p.address), "hex": bytes(p.bytes[:p.size]).hex(),
                     "mnemonic": p.mnemonic.decode(), "operands": p.op_str.decode()}
                    for p in pointer[:count]]
        finally:
            self.lib.cs_free(pointer, count)

    def close(self):
        self.lib.cs_close(C.byref(self.handle))


def disassemble(batch, binary, type_names, addresses, scan_names, output):
    methods = read_json(batch / "证据/原生方法地址索引.json")
    by_address = {}
    for method in methods:
        by_address.setdefault(method["pointer"], []).append(method)
    pointers = sorted(by_address)
    selected = set(addresses)
    selected.update(m["pointer"] for m in methods if m["type"] in type_names and m["name"] != ".ctor")
    targets = {m["pointer"]: m for m in methods if m["type"] + "." + m["name"] in scan_names}
    image = NativeImage(binary)
    xrefs, dumps = [], {}
    try:
        # 先找相对分支候选，再从函数入口反汇编验证指令边界；字节命中本身不算调用证据。
        for section in image.sections:
            if not section["execute"] or not targets:
                continue
            code = image.data[section["raw"]:section["raw"] + section["bytes"]]
            for opcode in (b"\xe8", b"\xe9"):
                at = code.find(opcode)
                while at >= 0 and at + 5 <= len(code):
                    address = image.base + section["rva"] + at
                    target = address + 5 + struct.unpack_from("<i", code, at + 1)[0]
                    if target in targets:
                        start, end, basis = image.bounds(address, pointers)
                        insns = image.decode(start, end)
                        instruction = next((i for i in insns if i["address"] == address and i["hex"] == code[at:at + 5].hex() and i["mnemonic"] in ("call", "jmp")), None)
                        if instruction:
                            xrefs.append({"address": address, "target": targets[target], "owner_start": start,
                                          "owners": by_address.get(start, []), "basis": basis})
                            selected.add(start)
                    at = code.find(opcode, at + 1)
        for address in sorted(selected):
            start, end, basis = image.bounds(address, pointers)
            insns = image.decode(start, end)
            for instruction in insns:
                if instruction["mnemonic"] in ("call", "jmp") and instruction["operands"].startswith("0x"):
                    instruction["target_methods"] = by_address.get(int(instruction["operands"], 16), [])
            dumps[hex(address)] = {"requested_address": address, "start": start, "end": end, "basis": basis,
                                   "methods": by_address.get(address, []), "instructions": insns}
        save(batch / "证据" / output, {"binary_sha256": digest(binary), "decoder": "Cpp2IL随附Capstone4.0",
                                      "targets": list(targets.values()), "direct_xrefs": xrefs, "methods": dumps,
                                      "limitations": ["仅核对直接相对call/jmp；间接调用、反射、冷代码块与动态行为未穷尽", "字段偏移必须结合对象来源与类型核对，不能按相同数字猜字段"]})
    finally:
        image.close()
    print("反汇编方法", len(dumps), "已核对直接引用", len(xrefs), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--type", action="append", default=[])
    parser.add_argument("--address", action="append", type=lambda s: int(s, 0), default=[])
    parser.add_argument("--scan-target", action="append", default=[])
    parser.add_argument("--global", dest="globals", action="append", type=lambda s: int(s, 0), default=[])
    parser.add_argument("--output", default="音效调用反汇编.json")
    args = parser.parse_args()
    if Path(args.output).name != args.output:
        raise ValueError("证据输出只允许单层文件名")
    batch = local_batch(args.batch)
    binary, metadata = verify_inputs(batch)
    start = time.time()
    if args.index:
        if (batch / "证据/音效类型布局.json").exists():
            raise FileExistsError("类型索引已存在，重试使用新批次或保留历史输出")
        index_types(batch, binary, metadata)
    elif args.globals:
        if (batch / "证据" / args.output).exists():
            raise FileExistsError("全局引用证据已存在，请指定新的输出名")
        resolve_globals(batch, binary, metadata, args.globals, args.output)
    else:
        if (batch / "证据" / args.output).exists():
            raise FileExistsError("反汇编证据已存在，请指定新的输出名")
        disassemble(batch, binary, args.type, args.address, args.scan_target, args.output)
    print("耗时秒", round(time.time() - start, 2), flush=True)


if __name__ == "__main__":
    main()
