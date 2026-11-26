"""获取指定构建中 18 个已确认的原作丧尸战斗事件并核验试听交付。"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import wave

from 检查音效 import inspect
from 探测音效 import ROOT, digest, local_batch, riff, save
from 提取音效 import render
from 资源包分块读取 import UnityFSBundle


TASK = "断箭原作丧尸战斗音效-V001-01a2023f"
BANKS = ("Master.strings.bank", "Master.bank", "SFX_Unit.bank", "SFX_Weapon.bank")
ATTACKS = {
    "Zomb_Walker-Runner_Shooting", "Zomb_Spitter_Shooting",
    "Zomb_Smoker_Shooting", "Zomb_Thrower_Shooting", "Zomb_The Swarm_Shooting",
}
LOOPS = {
    "Zomb_Crusher-Tank", "Zomb_Boomer", "Zomb_BoomerMain", "Zomb_Smoker", "Zomb_The Swarm",
}
LEGACY_WAV = {"Zomb_Walker", "Zomb_Runner", "Zomb_Waalker-Runner_Death"}
LEGACY_ARCHIVE = {
    "SFX_Zom_Car_Expl", "SFX_Zom_Crusher_Hit", "SFX_Zom_MELEE_HIT",
    "SFX_Zom_Smoker_Expl", "SFX_Zom_Spitter_Expl",
}
MOVEMENT = (
    ("移动5-地表1", {"InfantryMove": 5, "Surfase": 1}),
    ("移动12-地表1", {"InfantryMove": 12, "Surfase": 1}),
    ("移动12-地表2", {"InfantryMove": 12, "Surfase": 2}),
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def identity(path):
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def checked(path, expected):
    row = identity(path)
    if (row["bytes"], row["sha256"]) != (expected["bytes"], expected["sha256"]):
        raise ValueError(f"源文件身份与已核验记录不符：{path.name}")
    return row


def source_index():
    snapshot = local_batch("BA-26273833-006")
    event_index = snapshot / "证据/全bank完整事件索引.json"
    rows = read(event_index)["events"]
    names = ATTACKS | LOOPS | LEGACY_WAV | LEGACY_ARCHIVE
    events = {}
    for row in rows:
        bank, name = PurePosixPath(row["bank"]).name, row["path"].rsplit("/", 1)[-1]
        if bank in BANKS[2:] and name in names:
            if name in events:
                raise ValueError(f"事件名或源包有多个匹配：{name}")
            events[name] = dict(row, bank=bank)
    if events.keys() != names or len({row["guid"] for row in events.values()}) != len(names):
        raise ValueError("18 项源事件未逐一对应")
    return events, event_index, snapshot / "输入清单.json"


def prepare(batch):
    config = read(ROOT / "local-data/断箭/本地配置.json")
    game = Path(config["game_directory"])
    manifest = Path(config["steam_manifest"])
    build, = re.findall(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8-sig"))
    if build != "26627577" or not re.fullmatch(rf"BA-{build}-\d{{3}}", batch.name):
        raise ValueError("批次须匹配已确认的 Steam 构建")
    events, index, old_inputs = source_index()
    reference = {Path(row["path"]).name: row for row in read(old_inputs)["files"]}
    data = game / "BrokenArrow_Data"
    banks = []
    for name in BANKS:
        path = data / "StreamingAssets" / name
        container = riff(path)
        banks.append(dict(checked(path, reference[name]), bank=name, header=container["header"],
                          fmt=[row["hex"] for row in container["chunks"] if row["tag"] == "FMT "]))

    # 只核对游戏设置对象身份，正式交付只记录摘要。
    from UnityPy.files import SerializedFile
    from UnityPy.streams import EndianBinaryReader

    settings = local_batch("BA-26273833-002") / "证据/音频设置原始对象.json"
    checked(settings, reference[settings.name])
    setting, = (row for row in read(settings) if row["name"] == "FMODStudioSettings")
    pack = data / "data.unity3d"
    with pack.open("rb") as stream:
        bundle = UnityFSBundle(stream, pack.stat().st_size)
        af = SerializedFile(EndianBinaryReader(bundle.open_node(setting["node"])), name=setting["node"])
        raw = af.objects[setting["id"]].get_raw_data()
        if raw != bytes.fromhex(setting["hex"]):
            raise ValueError("当前构建的 FMOD 设置对象与已验证对象不同")
        engine, version = bundle.engine, bundle.version
    read_files = [identity(p) for p in (index, old_inputs, manifest, settings)]
    tools_dir = ROOT / "local-data/tools/FMOD/2.01.11"
    read_files.extend(identity(p) for p in (tools_dir / "fmodstudio.dll", tools_dir / "resonanceaudio.dll",
        Path(__file__), Path(__file__).with_name("提取音效.py"), Path(__file__).with_name("检查音效.py")))
    plan = {
        "task": TASK, "build": build, "unity": engine, "unityfs": version, "fmod": "2.01.11",
        "banks": banks, "inputs": read_files, "main_package": identity(pack),
        "settings_object": {"node": setting["node"], "id": str(setting["id"]),
                            "sha256": hashlib.sha256(raw).hexdigest()},
    }
    batch.mkdir()
    save(batch / "证据/音频设置原始对象.json", [setting])
    save(batch / "输入清单.json", plan)
    return events, plan


def event_type(name):
    if name in ATTACKS:
        return "攻击"
    if "Death" in name:
        return "死亡"
    if name in LEGACY_ARCHIVE:
        return "命中与爆炸"
    return "角色持续声"


def render_take(batch, event, attempt, overrides=None):
    overrides = overrides or {}
    modified = deepcopy(event)
    writable = {p["name"]: p for p in modified["parameters"] if p["type"] == 0 and not p["flags"] & 3}
    for key, value in overrides.items():
        param = writable[key]
        if not param["minimum"] <= value <= param["maximum"]:
            raise ValueError(f"{key} 试样超出事件参数范围")
        param["default"] = value
    result = render(batch, modified, attempt)
    result.update(event=event, requested_parameter_overrides=overrides)
    save(batch / "尝试" / attempt / "渲染记录.json", result)
    if result["render_status"] != "passed":
        raise RuntimeError(f"{event['path']} 渲染失败：{result.get('error')}")
    source = batch / result["output"]
    metrics = inspect(source)
    record = {"mode": "本批离线渲染", "attempt": attempt, "parameters": result["parameters"],
              "overrides": overrides, "position": result["position"], "check": metrics}
    return source, record


def usable(check):
    return (check["decode_status"] == "passed" and check["sample_rate"] == 48000
            and check["channels"] == 2 and check["clip_samples"] == 0)


def old_wavs(batch, events):
    prior = local_batch("肃清视听-V001-01a1ac6a")
    wav_index = prior / "丧尸音频渲染.json"
    yield from reused_attempts(prior, wav_index, events)
    old = local_batch("BA-26273833-007") / "提取回执.json"
    library = ROOT / "local-data/资源库"
    pointer = read(library / "当前版本.json")
    original_map = library / pointer["directory"] / "原件映射.json"
    mapping = read(original_map)
    work = batch / "归档恢复"
    work.mkdir()
    archive_records = {}
    recovered = set()
    for row in read(old):
        name = row["name"]
        if name not in LEGACY_ARCHIVE:
            continue
        if row["guid"] != events[name]["guid"] or row["path"] != events[name]["path"]:
            raise ValueError("历史音效与当前事件身份不相符")
        expected = row["check"]
        entry = mapping[expected["sha256"]]
        archive = ROOT / entry["archive"]["path"]
        if archive not in archive_records:
            archive_records[archive] = checked(archive, entry["archive"])
        payload = subprocess.run(["tar", "-xOf", str(archive), entry["entry"]],
                                 check=True, capture_output=True).stdout
        if hashlib.sha256(payload).hexdigest() != expected["sha256"] or len(payload) != expected["bytes"]:
            raise ValueError(f"归档 WAV 摘要不符：{name}")
        path = work / f"{name}.wav"
        with path.open("xb") as stream:
            stream.write(payload)
        recovered.add(name)
        profile = row.get("render_profile", {})
        yield name, path, {
            "mode": "从归档复用旧 WAV", "source_batch": "BA-26273833-007",
            "archive": archive_records[archive], "archive_entry": entry["entry"],
            "receipt": identity(old), "archive_map": identity(original_map),
            "parameters": profile.get("parameters", {}), "position": profile.get("position"),
            "check": inspect(path),
        }
    if recovered != LEGACY_ARCHIVE:
        raise ValueError("历史归档事件范围不完整")


def reused_attempts(prior, wav_index, events):
    names = set()
    for row in read(wav_index):
        event = row["event"]
        name = event["path"].rsplit("/", 1)[-1]
        if name not in LEGACY_WAV or event["guid"] != events[name]["guid"]:
            raise ValueError("历史丧尸 WAV 的事件身份不符")
        path = prior / row["output"]
        checked(path, row)
        names.add(name)
        yield name, path, {
            "mode": "复用旧 WAV", "source_batch": prior.name,
            "source": identity(path), "receipt": identity(wav_index),
            "parameters": row["parameters"], "position": row["position"], "check": inspect(path),
        }
    if names != LEGACY_WAV:
        raise ValueError("历史丧尸 WAV 事件范围不完整")


def pcm_sha(path):
    with wave.open(str(path), "rb") as stream:
        return hashlib.sha256(stream.readframes(stream.getnframes())).hexdigest()


def add_take(batch, media, event, name, variant, source, record):
    check = record["check"]
    if not usable(check):
        raise ValueError(f"试听 WAV 未通过非静音、规格和削波检查：{name} {variant}")
    pcm = pcm_sha(source)
    if pcm not in media:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name)
        target = batch / "试听WAV" / f"{safe}-{variant}-{pcm[:8]}.wav"
        if source.is_relative_to(batch):
            source.rename(target)
        else:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        media[pcm] = {"file": target.relative_to(batch).as_posix(), "sha256": digest(target),
                      "bytes": target.stat().st_size}
    row = {"name": name, "category": event_type(name), "variant": variant, "event": event,
           "pcm_sha256": pcm, "media": media[pcm], "listening_status": "待试听",
           "trigger_status": "待核实原作触发时机", "source": record}
    print(json.dumps({"name": name, "variant": variant, "peak": check["peak"],
                      "rms": check["rms"], "file": media[pcm]["file"]}, ensure_ascii=False), flush=True)
    return row


def render_event(batch, media, name, event):
    path, receipt = render_take(batch, event, event["guid"] + "-默认")
    if usable(receipt["check"]):
        return add_take(batch, media, event, name, "默认", path, receipt)
    options = {p["name"]: p for p in event["parameters"] if p["type"] == 0 and not p["flags"] & 3}
    parameter = next((key for key in ("RPM", "InfantryMove", "Loop_Count_MG") if key in options), None)
    if not parameter or receipt["check"]["clip_samples"]:
        raise ValueError(f"{name} 默认渲染未通过，请查看尝试记录")
    limits = options[parameter]
    value = (limits["minimum"] + limits["maximum"]) / 2
    if limits["flags"] & 8:
        value = round(value)
    path, receipt = render_take(batch, event, event["guid"] + "-参数复核", {parameter: value})
    receipt["default_attempt"] = event["guid"] + "-默认"
    return add_take(batch, media, event, name, "参数复核", path, receipt)


def build(batch):
    events, plan = prepare(batch)
    (batch / "试听WAV").mkdir()
    media, rows = {}, []
    order = ["Zomb_Walker-Runner_Shooting", *sorted(ATTACKS - {"Zomb_Walker-Runner_Shooting"}), *sorted(LOOPS)]
    # 首条攻击声技术检查通过后才继续整批。
    for name in order:
        rows.append(render_event(batch, media, name, events[name]))
        save(batch / "进度.json", {"accepted": len(rows), "last": name})
    for name, path, record in old_wavs(batch, events):
        rows.append(add_take(batch, media, events[name], name, "历史基线", path, record))
    for name in ("Zomb_Walker", "Zomb_Runner"):
        for variant, overrides in MOVEMENT:
            path, record = render_take(batch, events[name], events[name]["guid"] + "-" + variant, overrides)
            rows.append(add_take(batch, media, events[name], name, variant, path, record))

    save(batch / "事件与试听清单.json", {"build": plan["build"], "rows": rows})
    verify(batch)


def verify(batch):
    events, _, _ = source_index()
    manifest_path = batch / "事件与试听清单.json"
    doc = read(manifest_path)
    rows = doc["rows"]
    coverage = Counter(row["name"] for row in rows)
    if coverage != Counter({name: (4 if name in ("Zomb_Walker", "Zomb_Runner") else 1) for name in events}):
        raise ValueError("事件或移动参数覆盖不符")
    expected = {}
    for row in rows:
        if row["event"] != events[row["name"]]:
            raise ValueError("清单中的 FMOD 身份被修改")
        record = row["media"]
        path = batch / record["file"]
        if path.parent != batch / "试听WAV":
            raise ValueError("试听路径不在批次内")
        if path not in expected:
            detail = inspect(path)
            expected[path] = detail, pcm_sha(path)
        detail, pcm = expected[path]
        if (detail["sha256"], detail["bytes"], pcm) != (record["sha256"], record["bytes"], row["pcm_sha256"]):
            raise ValueError(f"试听 WAV 文件摘要不符：{path.name}")
        if not usable(detail) or pcm in {v[1] for p, v in expected.items() if p != path}:
            raise ValueError(f"试听 WAV 无效或未去重：{path.name}")
    if set((batch / "试听WAV").glob("*.wav")) != expected.keys():
        raise ValueError("试听目录包含清单之外的 WAV")
    plan = read(batch / "输入清单.json")
    for row in [*plan["banks"], *plan["inputs"], plan["main_package"]]:
        checked(Path(row["path"]), row)
    report = {"build": doc["build"], "status": "通过", "events": len(coverage), "rows": len(rows),
              "movement_variants": len(rows) - len(coverage), "wav_files": len(expected),
              "reused_events": sum(row["variant"] == "历史基线" for row in rows),
              "audio_bytes": sum(d[0]["bytes"] for d in expected.values()),
              "manifest_sha256": digest(manifest_path), "listening": "待人工试听"}
    out = batch / "交付核验.json"
    if out.exists() and read(out) != report:
        raise ValueError("重查结果与现有核验回执不一致")
    if not out.exists():
        save(out, report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("build", "verify"))
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    batch = local_batch(args.batch)
    if args.stage == "build":
        build(batch)
    else:
        verify(batch)
