"""核对三层索引守恒、来源指纹和资源边界，并检查本地HTTP及播放条件分支。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from 探测音效 import digest, local_batch, save
from 生成音效索引 import exact_numbers
from 预览音效索引 import create_server, resource_map


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(batch):
    started = time.time()
    input_manifest = read(batch / "输入清单.json")
    for row in input_manifest["files"]:
        path = Path(row["path"])
        assert path.stat().st_size == row["bytes"] and digest(path) == row["sha256"], "输入发生变化：" + row["path"]
    source = local_batch(input_manifest["source_library_batch"]) / "音效配套库"
    original = read(source / "型号音效映射.json")
    for row in read(source / "输出核验.json")["files"]:
        path = (source / row["path"]).resolve()
        assert path.is_relative_to(source.resolve()) and digest(path) == row["sha256"] and path.stat().st_size == row["bytes"], "正式库发生变化"
    data = batch / "音效索引库/数据"
    assets = read(data / "音频资源.json")["assets"]
    mapping = read(data / "载具绑定.json")
    rules = read(data / "播放规则.json")
    assert len(mapping["vehicles"]) == len(original["models"]) == 129
    assert len(assets) == len(original["audio"]) == 101
    original_models = {model["model_id"]: model for model in original["models"]}
    counts = Counter()
    expected_users = {key: [] for key in assets}
    evidence_ids = 0
    for vehicle in mapping["vehicles"]:
        old = original_models[vehicle["model_id"]]
        assert vehicle["unit_id"] == old["unit_id"] and vehicle["option_ids"] == sorted(old["selected_option_ids"])
        assert vehicle["model_status"] == old["model_status"] and vehicle["mapping_status"] == old["mapping_status"]
        assert len(vehicle["bindings"]) == len(old["references"])
        for binding, ref in zip(vehicle["bindings"], old["references"]):
            assert binding["status"] == ref["mapping_status"] and binding["origin"] == ref["origin"]
            assert binding["role_field"] == ref["role_field"] and binding["source_event_path"] == ref["source_event_path"]
            assert binding["event_path"] == ref["event_path"] and binding["field_chain"] == exact_numbers(ref.get("field_chain", []))
            evidence_ids += sum(isinstance(part.get("object_id"), str) for part in binding["field_chain"])
            counts[binding["status"]] += 1
            if binding["audio_id"]:
                asset = assets[binding["audio_id"]]
                assert asset["sha256"] == ref["sample_sha256"] and asset["event_guid"] == ref["event"]["guid"]
                expected_users[binding["audio_id"]].append(binding["binding_id"])
            else:
                assert binding["status"] != "confirmed"
    for key, asset in assets.items():
        assert expected_users[key] == [user["binding_id"] for user in asset["users"]]
        old = original["audio"][asset["event_path"]]
        assert asset["bytes"] == old["check"]["bytes"] and asset["duration_seconds"] == old["check"]["duration_seconds"]
    assert dict(counts) == original["summary"]["reference_statuses"]
    assert all(rule["loop"] is False for rule in rules["actions"].values())
    allowed, _ = resource_map(batch)
    for vehicle in mapping["vehicles"]:
        if not vehicle["sample_model_url"]:
            continue
        path = allowed[vehicle["sample_model_url"]][0]
        with path.open("rb") as stream:
            magic, version, total, chunk_size, chunk_type = struct.unpack("<4s4I", stream.read(20))
            gltf = json.loads(stream.read(chunk_size))
        assert magic == b"glTF" and version == 2 and total == path.stat().st_size and chunk_type == 0x4E4F534A
        assert gltf.get("nodes") and not gltf.get("extensionsRequired"), "样例需要额外解码依赖"
        for item in gltf.get("buffers", []) + gltf.get("images", []):
            assert "uri" not in item or item["uri"].startswith("data:"), "样例包含未登记外部依赖"
    server = create_server(batch, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/"
    http_checks = []
    try:
        for key in ["", "数据/音频资源.json", "数据/载具绑定.json", "数据/播放规则.json", "页面.mjs", "vendor/three/build/three.module.js", "vendor/three/build/three.core.js", "vendor/three/examples/jsm/loaders/GLTFLoader.js", "vendor/three/examples/jsm/utils/SkeletonUtils.js"]:
            with urlopen(Request(base + quote(key), method="HEAD")) as response:
                assert response.status == 200
                http_checks.append({"path": key, "status": 200})
        for key in [next(key for key in allowed if key.startswith("资源/音频/"))] + [f"资源/模型/{name}.glb" for name in rules["sample_models"]]:
            with urlopen(Request(base + quote(key), headers={"Range": "bytes=0-15"})) as response:
                payload = response.read()
                assert response.status == 206 and len(payload) == 16 and payload[:4] in (b"RIFF", b"glTF")
                http_checks.append({"path": key, "status": 206})
        for key in ["../README.md", "%2e%2e/README.md", "资源路由.json", "输入清单.json", "local-data/断箭/本地配置.json"]:
            try:
                urlopen(base + quote(key, safe="/%"))
            except HTTPError as error:
                assert error.code == 404
                http_checks.append({"path": key, "status": 404})
            else:
                raise AssertionError("未登记资源可访问：" + key)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    save(batch / "证据/本地HTTP路由核验.json", {"status": "passed", "checks": http_checks, "browser_status": "not_run"})
    node = subprocess.run(["node", str(Path(__file__).with_name("验证音效索引.mjs")), batch.name], capture_output=True, encoding="utf-8", errors="replace")
    if node.returncode:
        raise RuntimeError(node.stdout + node.stderr)
    playback = read(batch / "证据/索引与播放逻辑核验.json")
    result = {"status": "passed", "inputs": len(input_manifest["files"]), "models": 129, "events": 101,
              "references": sum(counts.values()), "reference_statuses": dict(counts), "exact_object_id_strings": evidence_ids,
              "sample_glbs": 3, "http_checks": len(http_checks), "playback_checks": len(playback["checks"]), "formal_source_unchanged": True,
              "elapsed_seconds": time.time() - started, "browser_status": "not_run", "listening_status": "not_individually_recorded"}
    save(batch / "交付完成核验.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-004")
    args = parser.parse_args()
    verify(local_batch(args.batch))


if __name__ == "__main__":
    main()
