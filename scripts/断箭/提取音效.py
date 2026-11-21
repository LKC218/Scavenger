"""用独立 FMOD 实例离线渲染已确证事件，不连接声卡。"""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import time
import struct
import hashlib
import wave
from 探测音效 import ROOT, FMOD, local_batch, save, digest


class Vector(C.Structure):
    _fields_ = [("x", C.c_float), ("y", C.c_float), ("z", C.c_float)]


class Attributes(C.Structure):
    _fields_ = [("position", Vector), ("velocity", Vector), ("forward", Vector), ("up", Vector)]


class StudioAdvanced(C.Structure):
    _fields_ = [("cbsize", C.c_int), ("commandqueuesize", C.c_int),
                ("handleinitialsize", C.c_int), ("studioupdateperiod", C.c_int),
                ("idlesampledatapoolsize", C.c_int), ("streamingscheduledelay", C.c_int),
                ("encryptionkey", C.c_void_p)]


def serialized_settings(batch):
    """按 FMOD Settings 序列化字段顺序读取，不尝试猜测密钥。"""
    records = json.loads((batch / "证据/音频设置原始对象.json").read_text(encoding="utf-8"))
    record = next(row for row in records if row["name"] == "FMODStudioSettings")
    raw = bytes.fromhex(record["hex"])
    pos = 28
    fields = {}
    offsets = {}
    def string(name):
        nonlocal pos
        size = struct.unpack_from("<I", raw, pos)[0]
        offsets[name] = {"offset": pos, "bytes": size}
        pos += 4
        fields[name] = raw[pos:pos + size].decode("utf-8")
        pos = (pos + size + 3) // 4 * 4
    def integer(name):
        nonlocal pos
        fields[name] = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
    string("m_Name")
    for name in ("HasSourceProject", "HasPlatforms"):
        integer(name)
    for name in ("sourceProjectPath", "sourceBankPath", "sourceBankPathUnformatted"):
        string(name)
    for name in ("BankRefreshCooldown", "ShowBankRefreshWindow", "AutomaticEventLoading", "BankLoadType", "AutomaticSampleLoading"):
        integer(name)
    string("EncryptionKey")
    integer("ImportType")
    string("TargetAssetPath")
    if fields["m_Name"] != "FMODStudioSettings" or fields["TargetAssetPath"] != "FMODBanks":
        raise ValueError("FMOD Settings序列化字段布局不符")
    key = fields.pop("EncryptionKey").encode("utf-8")
    save(batch / "证据/FMOD设置字段核验.json", {"source_node": record["node"], "path_id": record["id"],
         "fields": fields, "string_offsets": offsets, "key_sha256": hashlib.sha256(key).hexdigest(),
         "schema_source": "https://github.com/fmod/fmod-for-unity/blob/2.02/Assets/Plugins/FMOD/src/Settings.cs",
         "validation": "逐字段对齐解析并核对对象名与目标目录；密钥仅由实际序列化字段读取，不写入正式文档"})
    return key


def normalize_wave(path):
    """保留运行库原件，重封装PCM；修正此版本RIFF长度少4字节的问题。"""
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("渲染结果不是WAVE")
    pos, pcm, fmt = 12, None, None
    while pos + 8 <= len(raw):
        tag, size = struct.unpack_from("<4sI", raw, pos)
        if pos + 8 + size > len(raw):
            raise ValueError("WAV块不完整")
        if tag == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", raw, pos + 8)
        if tag == b"data":
            pcm = raw[pos + 8:pos + 8 + size]
        pos += 8 + size + size % 2
    if fmt is None or fmt[0] != 1 or fmt[5] != 16 or pcm is None or len(pcm) % fmt[4]:
        raise ValueError("输出不是完整PCM16")
    original = path.with_name("event-fmod.wav")
    if original.exists():
        raise FileExistsError(original)
    path.rename(original)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(fmt[1])
        stream.setsampwidth(2)
        stream.setframerate(fmt[2])
        stream.writeframes(pcm)
    return {"original": original.name, "original_sha256": digest(original),
            "original_riff_bytes": struct.unpack_from("<I", raw, 4)[0], "original_file_bytes": len(raw),
            "pcm_bytes": len(pcm), "processing": "PCM字节不变，仅重写标准WAV容器"}


def render(batch, event, attempt, seconds=8):
    output = batch / "尝试" / attempt
    output.mkdir(parents=True, exist_ok=False)
    path = output / "event.wav"
    requested_seconds = seconds
    if event["oneshot"]:
        seconds = min(30, max(seconds, event["length_ms"] / 1000 + 0.5))
    info = {"event": event, "kind": "event_render", "attempt": attempt,
            "sample_rate": 48000, "channels": 2, "dsp_frames": 1024,
            "duration_request_seconds": requested_seconds, "duration_effective_seconds": seconds, "stop": "ALLOWFADEOUT",
            "tail_seconds": 2, "position": [0, 0, 10], "parameters": {},
            "listening_status": "pending", "loop_status": "unknown",
            "output_type": "WAVWRITER_NRT", "output": str(path.relative_to(batch))}
    fmod = None
    previous_cwd = Path.cwd()
    start = time.time()
    try:
        fmod = FMOD(ROOT / "local-data/tools/FMOD/2.01.11/fmodstudio.dll")
        fmod.call("FMOD_System_SetOutput", fmod.core, 5)
        fmod.call("FMOD_System_SetSoftwareFormat", fmod.core, 48000, 3, 0)
        fmod.call("FMOD_System_SetDSPBufferSize", fmod.core, 1024, 4)
        key = C.create_string_buffer(serialized_settings(batch))
        advanced = StudioAdvanced()
        advanced.cbsize = C.sizeof(advanced)
        advanced.encryptionkey = C.cast(key, C.c_void_p)
        fmod.call("FMOD_Studio_System_SetAdvancedSettings", fmod.system, C.byref(advanced))
        info["encryption_settings"] = "读取FMODStudioSettings.EncryptionKey实际序列化字段"
        # 此运行库的 WAV writer 使用窄字符文件接口；工作目录仍严格位于批次内。
        os.chdir(output)
        filename = C.create_string_buffer(b"event.wav")
        fmod.call("FMOD_Studio_System_Initialize", fmod.system, 256, 4, 3, filename)
        config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8-sig"))
        game = Path(config["game_directory"])
        for name in dict.fromkeys(["Master.strings.bank", "Master.bank", event["bank"]]):
            fmod.load(game / "BrokenArrow_Data/StreamingAssets" / name)
        desc = C.c_void_p()
        fmod.call("FMOD_Studio_System_GetEvent", fmod.system, event["path"].encode(), C.byref(desc))
        fmod.call("FMOD_Studio_EventDescription_LoadSampleData", desc)
        fmod.call("FMOD_Studio_System_FlushSampleLoading", fmod.system)
        state = C.c_int()
        fmod.call("FMOD_Studio_EventDescription_GetSampleLoadingState", desc, C.byref(state))
        info["sample_loading_state"] = state.value
        if state.value != 3:
            raise RuntimeError(f"样本未就绪，LOADING_STATE={state.value}")
        instance = C.c_void_p()
        fmod.call("FMOD_Studio_EventDescription_CreateInstance", desc, C.byref(instance))
        attributes = Attributes(Vector(0, 0, 10), Vector(), Vector(0, 0, -1), Vector(0, 1, 0))
        fmod.call("FMOD_Studio_EventInstance_Set3DAttributes", instance, C.byref(attributes))
        for param in event["parameters"]:
            if param["type"] == 0 and not param["flags"] & 3:
                value = param["default"]
                fmod.call("FMOD_Studio_EventInstance_SetParameterByName", instance,
                          param["name"].encode(), C.c_float(value), 1)
                info["parameters"][param["name"]] = value
        fmod.call("FMOD_Studio_EventInstance_Start", instance)
        ticks = int(seconds * 48000 / 1024)
        for tick in range(ticks):
            fmod.call("FMOD_Studio_System_Update", fmod.system)
        position = C.c_int()
        fmod.call("FMOD_Studio_EventInstance_GetTimelinePosition", instance, C.byref(position))
        info["timeline_position_before_stop_ms"] = position.value
        fmod.call("FMOD_Studio_EventInstance_Stop", instance, 0)
        for tick in range(int(2 * 48000 / 1024)):
            fmod.call("FMOD_Studio_System_Update", fmod.system)
        fmod.call("FMOD_Studio_EventInstance_Release", instance)
        info["render_status"] = "passed"
    except Exception as error:
        info["render_status"] = "failed"
        info["error"] = str(error)
    finally:
        if fmod:
            try:
                fmod.close()
            except Exception as error:
                info["release_error"] = str(error)
        os.chdir(previous_cwd)
        info["elapsed_seconds"] = time.time() - start
        if path.exists() and info.get("render_status") == "passed":
            try:
                info["wav_normalization"] = normalize_wave(path)
            except Exception as error:
                info["render_status"] = "failed"
                info["error"] = str(error)
        if path.exists():
            info["bytes"] = path.stat().st_size
            info["sha256"] = digest(path)
        save(output / "渲染记录.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--event")
    parser.add_argument("--attempt")
    parser.add_argument("--category", choices=("air", "ground", "all"))
    parser.add_argument("--model")
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    if not 0 < args.seconds <= 30 or (args.attempt and Path(args.attempt).name != args.attempt):
        raise ValueError("时长限于30秒内，尝试目录必须为单层名称")
    batch = local_batch(args.batch)
    index = json.loads((batch / "证据/兼容性与事件索引.json").read_text(encoding="utf-8"))
    if args.event:
        if not args.attempt:
            parser.error("单事件渲染必须指定新尝试目录")
        event = next(item for item in index["events"] if item["path"] == args.event)
        print(json.dumps(render(batch, event, args.attempt, args.seconds), ensure_ascii=False, indent=2))
        return
    if not (args.category or args.model):
        parser.error("指定--event、--model或--category")
    from 检查音效 import inspect
    mapping = json.loads((batch / "型号音效映射.json").read_text(encoding="utf-8"))
    cached = {}
    for record in (batch / "尝试").glob("*/渲染记录.json"):
        info = json.loads(record.read_text(encoding="utf-8"))
        if info["render_status"] != "passed" or "wav_normalization" not in info:
            continue
        details = inspect(batch / info["output"])
        if details["decode_status"] == "passed":
            cached[info["event"]["path"]] = {"render": info, "check": details}
    first = next(model for model in mapping["models"] if model["model_id"] == "US_A10")
    gate_events = [ref["event_path"] for ref in first["references"] if ref["mapping_status"] == "confirmed"
                   and ((ref["domain"] == "unit" and ref["role_field"] == "MoveEvent")
                        or (ref["origin"].get("id") == 383 and ref.get("role_field") == "Shot"))]
    gate = {"model": "US_A10", "events": gate_events, "technical_passed": len(gate_events) == 2 and all(path in cached for path in gate_events),
            "listening_status": "pending", "mapping_source": "型号音效映射.json"}
    save(batch / "证据/A10首测关口.json", gate)
    if not gate["technical_passed"]:
        raise ValueError("A10映射与可播放技术关口未通过，不进入批量")
    selected = [model for model in mapping["models"] if (not args.model or model["model_id"] == args.model)
                and (not args.category or args.category == "all" or model["category"] == args.category)]
    paths = list(dict.fromkeys(ref["event_path"] for model in selected for ref in model["references"] if ref["mapping_status"] == "confirmed"))
    event_index = {item["path"]: item for item in index["events"]}
    event_index.update({ref["event_path"]: ref["event"] for model in mapping["models"] for ref in model["references"] if ref["mapping_status"] == "confirmed"})
    for ordinal, path in enumerate(paths):
        if path in cached:
            continue
        event = event_index[path]
        attempt = event["guid"] + "-" + str(time.time_ns())
        info = render(batch, event, attempt, args.seconds)
        details = inspect(batch / info["output"]) if info["render_status"] == "passed" else {"decode_status": "failed", "error": info.get("error")}
        cached[path] = {"render": info, "check": details}
        save(batch / "事件渲染结果.json", cached)
        print(ordinal + 1, "/", len(paths), details["decode_status"], path.rsplit("/", 1)[-1], flush=True)
    save(batch / "事件渲染结果.json", cached)


if __name__ == "__main__":
    main()
