"""核验登记媒体、Agent查询、本机HTTP边界及隔离增量发布。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
import wave
import struct

from 资料库核心 import ROOT, LIB, current, check_file, read, save, sha, query, import_manifest, wav_check
from 启动正式资料库 import create_server, VENDOR


def main():
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 核验统一库 import run
        return run()
    pointer, catalog, routes, version = current()
    results = []
    def passed(name): results.append({"name": name, "status": "passed"})
    frozen_pointer = sha(LIB / "当前版本.json")
    for row in routes.values(): check_file(row)
    source = read(version / "来源清单.json")
    for row in source.get("files", []): check_file(row)
    passed("全部登记媒体及直接来源摘要")
    if pointer["revision"] == 1:
        assert len(catalog["models"]) == 129 and len(catalog["audio"]) == 250
        assert sum(len(a["bindings"]) for a in catalog["audio"]) == 884
        assert sum(len(a["model_ids"]) for a in catalog["audio"]) == 741
        assert catalog["summary"]["model_statuses"] == {"passed": 125, "partial": 4}
        assert catalog["summary"]["audio_statuses"] == {"ready": 247, "review": 3}
    for model in catalog["models"]:
        sounds = query(catalog, routes, related_to=model["id"], limit=200)["results"]
        assert {r["id"] for r in sounds} == set(model["audio_ids"])
        assert all(model["id"] in row["model_ids"] for row in sounds)
    passed("模型声音双向集合及原状态")
    command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/断箭/查询资料库.py"), "--query", "A-10", "--type", "model", "--json"]
    actual = json.loads(subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert any(row["id"] == "model:US_A10" and Path(row["local_paths"]["model_url"]).is_file() for row in actual["results"])
    assert query(catalog, routes, "Ａ１０", "model")["total"] == actual["total"]
    passed("实际命令行JSON及宽松型号搜索")
    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def request(path, headers=None, method="GET"):
        try:
            with urlopen(Request(base + path, headers=headers or {}, method=method), timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error: return error.code, error.headers, error.read()
    try:
        status, headers, body = request("/")
        assert status == 200 and b"app.mjs" in body and headers["Cache-Control"] == "no-store"
        for name in VENDOR: assert request("/vendor/" + name, method="HEAD")[0] == 200
        for name in ("style.css", "app.mjs", "viewer.mjs", "search.mjs"): assert request("/" + name)[0] == 200
        params = urlencode({"q": "A10", "type": "model", "limit": 20})
        remote = json.loads(request("/api/search?" + params)[2])
        assert [r["id"] for r in remote["results"]] == [r["id"] for r in actual["results"]]
        assert all("local_paths" not in row for row in remote["results"])
        passed("网页入口依赖闭合及API与CLI一致")
        glb = catalog["models"][0]["model_url"]; audio = catalog["audio"][0]["url"]
        for url, signature in ((glb, b"glTF"), (audio, b"RIFF")):
            status, headers, body = request(url, {"Range": "bytes=0-3"})
            assert status == 206 and body == signature and headers["Content-Range"].startswith("bytes 0-3/")
            assert request(url, {"Range": "bytes=-0"})[0] == 416
            assert request(url, {"Range": "bytes=20-5"})[0] == 416
        assert request(glb, {"Range": "bytes=-4"})[0] == 206
        for url in ("/../README.md", "/%2e%2e/local-data/", "/local-data/", "/api/search?limit=0", "/api/search?type=unknown"):
            assert request(url)[0] in (400, 404)
        passed("GLB及WAV范围读取与越界拒绝")
    finally:
        server.shutdown(); server.server_close()
    # 仅在隔离目录测试发布，复用已登记媒体，不写正式指针。
    temp = ROOT / "tmp/断箭" / ("正式资料库验收-" + uuid.uuid4().hex[:8])
    target = temp / "测试资料库"; target.mkdir(parents=True)
    shutil.copytree(version, target / pointer["directory"])
    save(target / "当前版本.json", pointer)
    model, audio = catalog["models"][0], catalog["audio"][0]
    manifest = {"schema_version": 1, "source": {"label": "隔离验收复用样本，非新增真实资产"},
      "models": [{"id": "model:fixture_new", "name": "增量模型测试", "category": "air", "model_path": routes[model["model_url"]]["path"], "preview_path": routes[model["preview_url"]]["path"]}],
      "audio": [{"id": "audio:fixture_new", "name": "增量音效测试", "category": "验收", "audio_path": routes[audio["url"]]["path"]}],
      "bindings": [{"model_id": "model:fixture_new", "audio_id": "audio:fixture_new", "field": "MoveEvent", "evidence": "仅测试清单，不用于真实型号关联"}]}
    path = temp / "入库清单.json"; save(path, manifest)
    before = sha(target / "当前版本.json")
    dry = import_manifest(path, False, pointer["revision"], target)
    assert dry["new_files"] == 0 and sha(target / "当前版本.json") == before
    after = import_manifest(path, True, pointer["revision"], target)
    assert after["revision"] == pointer["revision"] + 1 and current(target)[1]["summary"]["models"] == len(catalog["models"]) + 1
    assert (target / pointer["directory"] / "资源索引.json").is_file()
    assert not (target / "资源").exists()
    try:
        import_manifest(path, True, pointer["revision"], target)
        raise AssertionError("应拒绝过期版本")
    except ValueError: pass
    passed("增量校验无副作用、按摘要复用及版本原子切换")
    bad = deepcopy(manifest); bad["models"][0]["id"] = model["id"]
    bad["audio"] = []; bad["bindings"] = []; save(path, bad)
    try:
        import_manifest(path, False, after["revision"], target)
        raise AssertionError("应拒绝既有ID")
    except ValueError: pass
    bad = {"schema_version": 1, "source": "隔离验收", "bindings": [{"model_id": model["id"], "audio_id": audio["id"], "field": "Shot"}]}; save(path, bad)
    try:
        import_manifest(path, False, after["revision"], target)
        raise AssertionError("应拒绝缺少证据的关系")
    except ValueError: pass
    sample = temp / "新增样本.wav"
    with wave.open(str(sample), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(struct.pack("<800h", *([100, -100] * 400)))
    new_manifest = {"schema_version": 1, "source": "隔离生成样本，非真实资产", "audio": [
        {"id": "audio:fixture_copy", "name": "新增文件分支", "category": "验收", "audio_path": str(sample)}]}
    save(path, new_manifest)
    new_dry = import_manifest(path, False, after["revision"], target)
    assert new_dry["new_files"] == 1 and not (target / "资源").exists()
    copied = import_manifest(path, True, after["revision"], target)
    assert len(list((target / "资源").rglob("*.wav"))) == 1
    assert (target / "资源/音效/验收").is_dir()
    new_manifest["audio"][0]["id"] = "audio:fixture_copy_again"; save(path, new_manifest)
    assert import_manifest(path, False, copied["revision"], target)["new_files"] == 0
    truncated = temp / "截断样本.wav"; truncated.write_bytes(sample.read_bytes()[:-20])
    try:
        wav_check(truncated)
        raise AssertionError("应拒绝截断WAV")
    except ValueError: pass
    passed("新媒体实际复制、重复内容复用及截断WAV拒绝")
    assert sha(LIB / "当前版本.json") == frozen_pointer
    passed("重复ID、缺少关系证据及正式指针保护")
    result = {"status": "passed", "revision": pointer["revision"], "checks": results, "media_routes": len(routes), "summary": catalog["summary"],
              "fixture_directory": str(temp), "formal_pointer_unchanged": True, "browser_validation": "separate_record"}
    save(LIB / "技术核验.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "fixture_directory"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
