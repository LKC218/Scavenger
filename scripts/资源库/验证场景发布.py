"""只在隔离沙盒检查场景发布拒绝、依赖保持、并发保护及回滚，不宣称模型验收。"""

import argparse
from contextlib import contextmanager
import copy
from pathlib import Path
import struct
import uuid

import 发布场景 as publisher
from 统一资源 import ROOT, read, save, sha


def bind_fixture(manifest,root):
    """仅生成事务夹具凭证，不借用真实模型验收状态。"""
    asset=manifest['assets'][0];asset.update(technical_status='passed',ai_visual_status='passed')
    manifest['acceptance']['evidence']=['预览.png','验收.json']
    manifest['files']['验收.json']['dependencies']=['预览.png']
    subject=publisher.acceptance_subject(manifest,'验收.json')
    path=root/manifest['package_root']/'验收.json'
    save(path,{'schema':'scene-technical-acceptance-v1','passed':True,'subject_sha256':subject,
        'gates':{key:True for key in publisher.GATES},'human_acceptance':'待人工验收',
        'evidence':[{'file':'预览.png',**{key:manifest['files']['预览.png'][key] for key in ['sha256','bytes']}}],
        'scope':'纯事务夹具，不代表模型或网页验收'})
    manifest['files']['验收.json'].update(bytes=path.stat().st_size,sha256=sha(path))
    manifest['acceptance']['binding']={'schema':1,'receipt':'验收.json','subject_sha256':subject}


def fixture(root):
    home = root / "local-data/资源库"
    base = home / "索引版本/003"
    base.mkdir(parents=True)
    values = {"资源索引.json": {"models": [{"id": "model:old", "quality_status": "pending"}], "audio": [{"id": "audio:keep"}], "summary": {}},
              "资源路由.json": {}, "模型登记.json": {}, "主工程.json": {}, "保留策略.json": {"masters": 0},
              "运行替换.json": {"keep": "原运行映射"}, "靶场运行清单.json": {"keep": "原载具及音效"}, "原件映射.json": {"keep": "原件归档"}}
    for name, value in values.items():
        save(base / name, value)
    save(home / "当前版本.json", {"revision": 3, "directory": "索引版本/003", "metadata": {name: sha(base / name) for name in values}})
    package = root / "local-data/候选"
    package.mkdir()
    for name in ("images/底色.png", "原型/工程.blend", "总装配.blend", "预览.png", "场景.json", "验收.json"):
        path = package / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(("仅事务夹具：" + name).encode())
    document = b'{"asset":{"version":"2.0"},"images":[{"uri":"../images/' + "底色.png".encode() + b'"}]}'
    document += b" " * (-len(document) % 4)
    path = package / "原型/模型.glb"
    path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 20 + len(document), len(document), 0x4E4F534A) + document)
    files = {path.relative_to(package).as_posix(): {"bytes": path.stat().st_size, "sha256": sha(path), "dependencies": []} for path in package.rglob("*") if path.is_file()}
    files["原型/模型.glb"]["dependencies"] = ["images/底色.png"]
    files["总装配.blend"]["dependencies"] = ["原型/工程.blend"]
    source = {"build": "fixture", "usage_scope": "隔离事务验证", "license_status": "测试夹具"}
    asset = {"id": "model:sample_v001", "source_key": "fixture:sample", "version": "V001", "name": "事务夹具",
             "category": "building", "human_acceptance": "待人工验收", "source": source, "dimensions_m": [1, 1, 1],
             "anchor": {}, "materials": [], "collision": {}, "detail_levels": [], "triangles": 1,
             "files": {"runtime": "原型/模型.glb", "blend": "原型/工程.blend", "preview": "预览.png"}}
    scene = {"id": "scene:sample_v001", "source_key": "fixture:scene", "version": "V001", "name": "事务场景夹具",
             "human_acceptance": "待人工验收", "source": source, "bounds": {}, "conversion": {}, "coverage": {}, "asset_ids": [asset["id"]],
             "files": {"manifest": "场景.json", "blend": "总装配.blend", "preview": "预览.png"}}
    manifest = {"schema": 1, "package_root": "local-data/候选", "files": files, "assets": [asset], "scene": scene,
                "acceptance": {"complete": True, "modular": True, "web": True, "library": True, "ai_visual": True,
                               "human": "待人工验收", "evidence": ["验收.json"]}}
    bind_fixture(manifest,root)
    return home, base, manifest


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for case in ("候选只读", "候选摘要计数", "锁占用", "版本竞争", "摘要变化", "缺失真实依赖", "包内逃逸", "人工状态升级", "未通过网页", "重复ID", "验收集合误绑", "缺少验收绑定", "伪升网页状态", "复制中断续跑", "切换后回执失败", "切换后中断恢复", "原子切换失败", "成功及回滚"):
        root = output / case
        home, base, manifest = fixture(root)
        identity = sha(home / "当前版本.json")
        lockfile = root / "测试锁"
        @contextmanager
        def guard():
            with lockfile.open("x") as stream:
                stream.write("场景事务验证")
            try:
                yield
            finally:
                lockfile.unlink()
        if case == "锁占用":
            lockfile.write_text("其他任务持有")
        elif case == "摘要变化":
            manifest["files"]["原型/模型.glb"]["sha256"] = "0" * 64
        elif case == "缺失真实依赖":
            manifest["files"]["原型/模型.glb"]["dependencies"] = []
        elif case == "包内逃逸":
            manifest["files"]["../escape"] = manifest["files"]["预览.png"]
        elif case == "人工状态升级":
            manifest["scene"]["human_acceptance"] = "已通过"
        elif case == "未通过网页":
            manifest["acceptance"]["web"] = False
        elif case == "重复ID":
            manifest["assets"][0]["id"] = "model:old"
            manifest["scene"]["asset_ids"] = ["model:old"]
            bind_fixture(manifest,root)
        elif case=='验收集合误绑':
            manifest['acceptance']['binding']['subject_sha256']='0'*64
        elif case=='缺少验收绑定':
            manifest['acceptance'].pop('binding')
        elif case=='伪升网页状态':
            path=root/manifest['package_root']/'验收.json';receipt=read(path);receipt['gates']['web']=False;save(path,receipt)
            manifest['files']['验收.json'].update(bytes=path.stat().st_size,sha256=sha(path))
        original_replace = publisher.os.replace
        original_copy=publisher.shutil.copyfileobj
        original_save=publisher.save
        if case == "原子切换失败":
            def fail_replace(*args):
                if Path(args[1]).name=='当前版本.json':raise OSError("注入提交前故障")
                return original_replace(*args)
            publisher.os.replace = fail_replace
        elif case=='复制中断续跑':
            def fail_copy(src,dst,*args):
                dst.write(src.read(3));raise OSError('注入复制中断')
            publisher.shutil.copyfileobj=fail_copy
        elif case in ('切换后回执失败','切换后中断恢复'):
            def fail_receipt(path,value):
                if Path(path).name=='发布回执.json' and value.get('status')=='已提交':
                    if case=='切换后中断恢复':raise KeyboardInterrupt('注入切换后中断')
                    raise OSError('注入提交后回执写入失败')
                return original_save(path,value)
            publisher.save=fail_receipt
        try:
            if case == "候选只读":
                publisher.validate(manifest, root)
                assert sha(home / "当前版本.json") == identity
                results.append({"case": case, "passed": True, "pointer_unchanged": True})
                continue
            if case=='候选摘要计数':
                catalog,_=publisher.candidate_index(manifest,read(base/'资源索引.json'),root=root)
                assert catalog['summary']['model_statuses']=={'pending':2}
                assert sum(catalog['summary']['model_statuses'].values())==catalog['summary']['models']
                assert sha(home/'当前版本.json')==identity
                results.append({'case':case,'passed':True,'pointer_unchanged':True})
                continue
            result = publisher.publish(manifest, 2 if case == "版本竞争" else 3, identity, root, home, guard)
            assert case == "成功及回滚", case
            pointer = read(home / "当前版本.json")
            current = home / pointer["directory"]
            assert pointer["revision"] == 4
            assert len(read(current / "资源索引.json")["models"]) == 2
            assert len(read(current / "资源索引.json")["scenes"]) == 1
            for name in ("运行替换.json", "靶场运行清单.json", "原件映射.json"):
                assert sha(current / name) == sha(base / name)
            retained = read(current / "场景登记.json")["scene:sample_v001"]["retained_files"]
            blend = root / retained["总装配.blend"]["path"]
            assert (blend.parent / "原型/工程.blend").exists()
            glb = root / retained["原型/模型.glb"]["path"]
            assert (glb.parent / "../images/底色.png").exists()
            publisher.rollback(result["receipt_directory"], result["pointer_sha256"], home, guard)
            assert read(home / "当前版本.json")["revision"] == 3
            # 新发布会阻止上一份回滚凭证改写指针。
            save(home / "当前版本.json", {"revision": 99})
            before = sha(home / "当前版本.json")
            try:
                publisher.rollback(result["receipt_directory"], result["pointer_sha256"], home, guard)
                raise AssertionError("过期回滚未被拒绝")
            except ValueError:
                assert sha(home / "当前版本.json") == before
            results.append({"case": case, "passed": True, "relative_dependencies": True, "rollback": True, "stale_rollback_refused": True})
        except KeyboardInterrupt as error:
            assert case=='切换后中断恢复'
            assert read(home/'当前版本.json')['revision']==4
            receipt=next((home/'验收').glob('*/发布回执.json'))
            prepared=read(receipt)
            assert prepared['status']=='准备切换'
            publisher.save=original_save
            publisher.rollback(receipt.parent,prepared['pointer_sha256'],home,guard)
            assert sha(home/'当前版本.json')==identity
            results.append({'case':case,'passed':True,'prepared_receipt_recovered':True})
        except (ValueError, OSError) as error:
            assert case != "成功及回滚", repr(error)
            assert sha(home / "当前版本.json") == identity
            if case=='复制中断续跑':
                assert list((home/'当前资源').rglob('*.writing'))
                publisher.shutil.copyfileobj=original_copy
                resumed=publisher.publish(manifest,3,identity,root,home,guard)
                assert resumed['status']=='已提交'
                publisher.rollback(resumed['receipt_directory'],resumed['pointer_sha256'],home,guard)
                assert sha(home/'当前版本.json')==identity
            results.append({"case": case, "passed": True, "pointer_unchanged": True, "rejection": str(error),'resumed':case=='复制中断续跑'})
        finally:
            publisher.os.replace = original_replace
            publisher.shutil.copyfileobj=original_copy
            publisher.save=original_save
    save(output / "验证结果.json", {"passed": True, "cases": results, "scope": "隔离事务夹具；不代替实际模型、场景、浏览器或人工验收"})
    print({"passed": True, "cases": len(results), "directory": str(output)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "local-data")
    run(output)
