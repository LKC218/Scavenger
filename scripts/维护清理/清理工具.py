"""项目内清理：默认预览、显式任务占用、冻结审批、七天回收。仅使用标准库。"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import uuid

MODULE_DIR = str(Path(__file__).resolve().parent)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

ROOT = Path(__file__).resolve().parents[2]
STATE = "local-data/维护清理"
DAY = 86400


def checked(relative):
    """逐级拒绝链接、目录联接、绝对路径和路径穿越。"""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("路径必须是项目相对路径，使用正斜杠")
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or any(p in ("..", ".") or ":" in p for p in relative.split("/")):
        raise ValueError("禁止绝对路径或路径穿越")
    if not parts:
        raise ValueError("禁止项目根目录")
    cursor = ROOT
    for part in parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            info = cursor.lstat()
            if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"拒绝链接或目录联接：{relative}")
    if not cursor.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("路径超出项目")
    return cursor


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(relative):
    return json.loads(checked(relative).read_text(encoding="utf-8"))


def save(relative, value, exclusive=False):
    path = checked(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    else:
        temporary = checked(relative + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)


@contextmanager
def lock():
    path = checked(STATE + "/执行锁")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def overlap(a, b):
    a, b = a.casefold().rstrip("/"), b.casefold().rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def is_release_bundle_path(relative):
    """只识别丧尸群体发布目录中的完整候选批次文件。"""
    parts = PurePosixPath(relative).parts
    return (
        len(parts) >= 5
        and parts[:3] == ("local-data", "丧尸模型", "发布")
        and re.fullmatch(r"ZOM-SHARE-\d{6}-\d{3}", parts[3]) is not None
    )


def allowed(relative, kind, release_bundle=False):
    checked(relative)
    parts = PurePosixPath(relative).parts
    if overlap(relative, STATE):
        raise ValueError("禁止清理维护清理自身")
    if kind == "history" and release_bundle and is_release_bundle_path(relative):
        return
    if any(any(word in p.casefold() for word in ('原件', '原始', '凭证', '证据', '输入快照', 'original', 'evidence')) for p in parts):
        raise ValueError('原件及必要凭证禁止进入通用清理')
    if kind == "cache":
        temporary = parts[0] in ("tmp", "temp") and len(parts) > 1
        python = parts[0] in ("scripts", "src") and "__pycache__" in parts
        if not (temporary or python):
            raise ValueError("缓存白名单仅限tmp、temp子项及自有源码的Python缓存")
    elif kind == "history":
        if len(parts) < 4 or parts[0] != "local-data":
            raise ValueError("历史载荷必须是local-data内的具体文件")
        forbidden = ("tools", "资源归档", "正式资料库", "原件", "输入快照")
        if any(p.casefold() in forbidden for p in parts):
            raise ValueError("原件、工具、正式库和冷归档须用专项工具维护")
        if any(any(word in p for word in ("原件", "原始", "输入快照", "凭证", "证据")) for p in parts):
            raise ValueError("原始材料及必要凭证不得作为历史载荷")
        if Path(relative).suffix.lower() not in {".blend", ".blend1", ".blend2", ".glb", ".gltf", ".bin", ".wav", ".opus", ".flac", ".png", ".jpg", ".jpeg", ".ktx2", ".zip", ".7z"}:
            raise ValueError("历史自动退役只接收明确的制作载荷，源码和元数据保持保护")


def automatic(registry):
    from 自动维护 import Automatic
    from types import SimpleNamespace
    return Automatic(sys.modules.get(__name__) or SimpleNamespace(**globals()), registry)


def item_blockers(registry, item, relative):
    if item.get("automatic_policy"):
        engine = automatic(registry)
        if engine.protected(relative):
            return ["当前通过版或必要凭证"]
        return engine.blockers(item, relative)
    return blockers(registry, relative)


def files_under(relative):
    path = checked(relative)
    if path.is_file():
        yield relative
    elif path.is_dir():
        for child in sorted(path.iterdir()):
            yield from files_under(child.relative_to(ROOT).as_posix())


def fingerprint(relative):
    path = checked(relative)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"不是普通文件：{relative}")
    sha = digest(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"扫描期间发生写入：{relative}")
    return {"path": relative, "bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": sha}


def same(row, relative=None):
    actual = fingerprint(relative or row["path"])
    if any(actual[k] != row[k] for k in ("bytes", "mtime_ns", "sha256")):
        raise ValueError(f"文件已变化：{row['path']}")


def blockers(registry, relative):
    return [key for key, task in registry["tasks"].items()
            if task["status"] != "closed" and any(overlap(relative, p) for p in task["paths"])]


def proof_check(item, registry=None):
    if item.get("automatic_policy"):
        if registry is None:
            raise ValueError("自动条目需要当前登记核验")
        automatic(registry).validate(item)
        return
    if item["kind"] != "history":
        return
    proof = item["proof"]
    if not proof.get("accepted_version") or proof.get("accepted_by") != "工程师" or not proof.get("dependency_review"):
        raise ValueError("历史版需要工程师验收版本和依赖审查记录")
    for name in ("replacement", "evidence"):
        row = proof[name]
        if overlap(row["path"], item["path"]):
            raise ValueError("替代件或证据不可位于待清理目录")
        same(row)


def pending_recycle(relative):
    for previous in checked(STATE + "/回收").glob("*/回执.json"):
        receipt = read(previous.relative_to(ROOT).as_posix())
        if receipt["status"] == "中断待核对":
            return True
        if any(row["state"] == "stored" and overlap(relative, row["item"]["path"]) for row in receipt["files"]):
            return True
    return False


def candidates(registry, automatic_only=False):
    rows, skipped = [], []
    seen = set()
    for item in registry["items"]:
        if item.get('suspended'):
            continue
        if automatic_only and item.get("automatic_policy") != registry.get("automation", {}).get("id"):
            continue
        reason = ""
        task = registry["tasks"].get(item["task"])
        if not task or task["status"] != "closed":
            reason = "所属任务尚未明确结束"
        elif not item.get("files") and blockers(registry, item["path"]):
            reason = "存在活动或状态不明的任务占用"
        elif pending_recycle(item["path"]):
            reason = "已有一代回收或中断回执，暂缓下一代"
        if reason:
            skipped.append({"path": item["path"], "reason": reason})
            continue
        try:
            allowed(item["path"], item["kind"], bool(item.get("release_bundle")))
            proof_check(item, registry)
        except (ValueError, OSError, KeyError) as error:
            if not item.get("automatic_policy"):
                raise
            skipped.append({"path":item["path"], "reason":str(error)})
            continue
        if item.get("files"):
            members = [r["path"] for r in item["files"]]
            if any(item_blockers(registry, item, p) for p in members):
                skipped.append({"path":item["path"], "reason":"具体文件存在活动或状态不明依赖"})
                continue
            members = [p for p in members if checked(p).exists()]
        else:
            members = list(files_under(item["path"]))
        if any(checked(p).name.casefold().endswith(".lock") or checked(p).name in ("LOCK", "SingletonLock") for p in members):
            skipped.append({"path": item["path"], "reason": "范围内存在锁文件，需人工核对"})
            continue
        for relative in members:
            path = checked(relative)
            if item.get("files"):
                frozen = next(r for r in item["files"] if r["path"] == relative)
                try:
                    same(frozen)
                except (ValueError, OSError) as error:
                    skipped.append({"path":relative,"reason":str(error)})
                    continue
            if PurePosixPath(relative).parts[0] in ("scripts", "src") and path.suffix not in (".pyc", ".pyo"):
                skipped.append({"path": relative, "reason": "自有源码只允许Python字节码缓存"})
                continue
            last_use = max(path.stat().st_mtime, item.get("registered_at", 0), task.get("closed_at", 0)) if item.get("automatic_policy") else path.stat().st_mtime
            if item["kind"] == "cache" and time.time() - last_use < 7 * DAY:
                skipped.append({"path": relative, "reason": "缓存未满七天"})
                continue
            if path.name.endswith(".lock") or path.name in ("LOCK", "SingletonLock"):
                skipped.append({"path": relative, "reason": "锁文件需人工核对"})
                continue
            if relative.casefold() not in seen:
                reference = automatic(registry).reference(item) if item.get('automatic_policy') else item
                rows.append({**fingerprint(relative), "item": reference})
                seen.add(relative.casefold())
    return rows, skipped


def initialize():
    groups = {
        "既有断箭任务待核对": ["local-data/断箭"],
        "既有资源优化任务待核对": ["local-data/网页资源库", "local-data/资源归档"],
        "既有统一资源库任务待核对": ["local-data/资源库"],
        "既有原型任务待核对": ["local-data/原型"],
        "既有丧尸任务待核对": ["local-data/丧尸模型"],
        "既有临时任务待核对": ["tmp", "temp"],
    }
    registry = {"schema": 1, "automatic_cache": False, "first_review": "pending",
                "tasks": {name: {"status": "unknown", "paths": paths} for name, paths in groups.items()},
                "items": []}
    save(STATE + "/登记.json", registry, exclusive=True)
    print("登记已建立；现有任务状态不明，全部保护；自动清理关闭。")


def preview(registry, scheduled=False):
    enabled = automatic(registry).enabled()
    rows, skipped = candidates(registry, automatic_only=scheduled and enabled)
    if scheduled and not enabled:
        rows = [row for row in rows if row["item"]["kind"] == "cache"]
    report = {"created": time.time(), "mode": "只读预览", "registry_sha256": digest(checked(STATE + "/登记.json")),
              "files": rows, "skipped": skipped, "bytes": sum(r["bytes"] for r in rows)}
    run = STATE + "/定时预览.json" if scheduled else STATE + "/预览/" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8] + ".json"
    unchanged = False
    if scheduled and checked(run).exists():
        old = read(run)
        unchanged = all(old.get(key) == value for key, value in report.items() if key != "created")
    if unchanged:
        report = old
    else:
        save(run, report, exclusive=not scheduled)
    print(json.dumps({"report": run, "files": len(rows), "bytes": report["bytes"], "sha256": digest(checked(run)),
                      "automatic_cache": registry["automatic_cache"], "first_review": registry["first_review"], "unchanged": unchanged}, ensure_ascii=False))
    return run, report


def quarantine(registry, manifest, approved_sha, cache_only=False, automatic_only=False):
    if not manifest.startswith(STATE + "/预览/") and manifest != STATE + "/定时预览.json":
        raise ValueError("只接受本工具生成的执行预览")
    if digest(checked(manifest)) != approved_sha:
        raise ValueError("审批摘要不匹配")
    report = read(manifest)
    if not report["files"]:
        raise ValueError("空清单无需回收")
    if report["registry_sha256"] != digest(checked(STATE + "/登记.json")):
        raise ValueError("任务登记已变化，请重新预览审批")
    eligible, _ = candidates(registry, automatic_only=automatic_only)
    current = {r["path"]: r for r in eligible}
    for row in report["files"]:
        if row["path"] not in current or row != current[row["path"]]:
            raise ValueError("候选身份或任务状态变化")
        if cache_only and row["item"]["kind"] != "cache":
            raise ValueError("定时任务不得处理历史版本")
        same(row)
    for previous in checked(STATE + "/回收").glob("*/回执.json"):
        old = read(previous.relative_to(ROOT).as_posix())
        if old["status"] == "中断待核对":
            raise ValueError("存在中断回收，请先核对回执")
        if any(entry["state"] == "stored" and overlap(entry["item"]["path"], row["item"]["path"])
               for entry in old["files"] for row in report["files"]):
            raise ValueError("同范围已有一代回收，须先处理到期回收或恢复")
    policy = registry.get('automation', {}).get('id')
    policies = {row['item'].get('automatic_policy') for row in report['files']}
    automatic_receipt = bool(policy and automatic(registry).enabled() and policies == {policy})
    if any(policies) and not automatic_receipt:
        raise ValueError('不同策略或旧登记须分开回收，不能生成混合策略回执')
    run = STATE + "/回收/" + uuid.uuid4().hex
    receipt = {"created": time.time(), "approved_sha256": approved_sha, "files": [], "status": "进行中"}
    if automatic_receipt:
        receipt['automatic_policy'] = registry['automation']['id']
    from 占用检查 import require_unoccupied
    if automatic_receipt:
        require_unoccupied([checked(r['path']) for r in report['files']])
    save(run + "/回执.json", receipt, exclusive=True)
    try:
        for row in report["files"]:
            fresh = read(STATE + "/登记.json")
            if fresh != registry or item_blockers(fresh, row['item'], row["path"]):
                raise ValueError("任务占用变化，停止回收")
            same(row)
            destination = run + "/文件/" + row["path"]
            target = checked(destination)
            if target.exists():
                raise ValueError("回收目标已有文件，拒绝覆盖")
            target.parent.mkdir(parents=True, exist_ok=True)
            # 先持久化意图；中断后仍能按原路径定位载荷。
            entry = {**row, "stored": destination, "moved_at": time.time(), "state": "pending"}
            receipt["files"].append(entry)
            save(run + "/回执.json", receipt)
            os.rename(checked(row["path"]), target)
            same(row, destination)
            entry["state"] = "stored"
            save(run + "/回执.json", receipt)
        receipt["status"] = "完成"
    except Exception:
        receipt["status"] = "中断待核对"
        raise
    finally:
        save(run + "/回执.json", receipt)
    print(run + "/回执.json")


def repair_receipt_policy(registry, receipt_path, approved_sha, note):
    """显式修复当前策略已授权回执的漏标，不接管旧登记或重置七天期限。"""
    if not receipt_path or not receipt_path.startswith(STATE + '/回收/') or not receipt_path.endswith('/回执.json'):
        raise ValueError('只接受本工具回收回执')
    if not note or not approved_sha or digest(checked(receipt_path)) != approved_sha:
        raise ValueError('缺少修复依据或回执摘要不符')
    engine = automatic(registry)
    receipt = read(receipt_path)
    if not engine.enabled() or receipt.get('automatic_policy') or receipt.get('status') != '完成' or not receipt.get('files'):
        raise ValueError('只修复已完成且漏标的当前自动策略回执')
    if receipt.get('created', 0) < engine.policy['enabled_at']:
        raise ValueError('不接管启用前回执')
    validated = set()
    for row in receipt['files']:
        item = engine.registered(row['item'])
        if item.get('automatic_policy') != engine.policy['id'] or row.get('state') != 'stored':
            raise ValueError('旧登记、其他策略或已恢复载荷禁止接管')
        if registry['tasks'][item['task']]['status'] != 'closed':
            raise ValueError('所属任务尚未结束')
        frozen = next((r for r in item['files'] if r['path'] == row['path']), None)
        if not frozen or any(row[k] != frozen[k] for k in ('bytes', 'mtime_ns', 'sha256')):
            raise ValueError('回执超出已冻结授权')
        expected = receipt_path.removesuffix('/回执.json') + '/文件/' + row['path']
        if row.get('stored') != expected or checked(row['path']).exists() or row.get('moved_at', 0) < receipt['created']:
            raise ValueError('载荷位置、原路径或回收时间异常')
        same(row, row['stored'])
        if item['id'] not in validated:
            engine.validate(item)
            validated.add(item['id'])
    receipt.update(automatic_policy=engine.policy['id'], policy_repair={
        'previous_sha256': approved_sha, 'note': note, 'repaired_at': time.time()})
    save(receipt_path, receipt)
    print(json.dumps({'receipt': receipt_path, 'sha256': digest(checked(receipt_path)),
                      'automatic_policy': engine.policy['id'], 'files': len(receipt['files'])}, ensure_ascii=False))


def recover_or_purge(registry, receipt_path, purge=False):
    if not receipt_path.startswith(STATE + "/回收/") or not receipt_path.endswith("/回执.json"):
        raise ValueError("只接受本工具回收回执")
    receipt = read(receipt_path)
    if receipt["status"] not in ("完成", "已恢复"):
        raise ValueError("中断回执需人工核对")
    validated = set()
    for row in receipt["files"]:
        if row["state"] != "stored":
            continue
        expected = receipt_path.removesuffix("/回执.json") + "/文件/" + row["path"]
        if row["stored"] != expected:
            raise ValueError("回收载荷路径不匹配")
        if row['item'].get('automatic_policy'):
            if receipt.get('automatic_policy') != row['item']['automatic_policy']:
                raise ValueError('自动回执策略身份不匹配')
            registered = automatic(registry).registered(row['item'])
            frozen = next((r for r in registered['files'] if r['path']==row['path']), None)
            if not frozen or any(row[k]!=frozen[k] for k in ('bytes','mtime_ns','sha256')):
                raise ValueError('回执文件超出冻结授权集合')
        allowed(
            row["path"],
            row["item"]["kind"],
            bool(row["item"].get("release_bundle")),
        )
        same(row, row["stored"])
        if purge:
            if item_blockers(registry, row['item'], row["path"]) or blockers(registry, row['stored']) or time.time() - row["moved_at"] < 7 * DAY:
                raise ValueError("仍有任务占用或回收未满七天")
            if checked(row['path']).exists():
                raise ValueError("原路径重新出现文件，停止清除并核对")
            identity = row['item'].get('id')
            if not identity or identity not in validated:
                proof_check(row["item"], registry)
                validated.add(identity)
        elif checked(row["path"]).exists():
            raise ValueError("原路径已有文件，拒绝覆盖")
    if purge and receipt.get('automatic_policy'):
        from 占用检查 import require_unoccupied
        require_unoccupied([checked(r['stored']) for r in receipt['files'] if r['state']=='stored'])
    try:
        for row in receipt["files"]:
            if row["state"] != "stored":
                continue
            if read(STATE + "/登记.json") != registry:
                raise ValueError("任务登记发生变化")
            same(row, row["stored"])
            if purge:
                if item_blockers(registry, row['item'], row["path"]) or blockers(registry, row['stored']) or checked(row['path']).exists():
                    raise ValueError("发现新任务占用或原路径重建")
                if not row['item'].get('automatic_policy'):
                    proof_check(row['item'], registry)
                checked(row["stored"]).unlink()
                row["state"] = "purged"
                row['purged_at'] = time.time()
            else:
                target = checked(row["path"])
                if target.exists():
                    raise ValueError('恢复目标已出现文件，拒绝覆盖')
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(checked(row["stored"]), target)
                row["state"] = "restored"
            save(receipt_path, receipt)
    except Exception:
        receipt['status'] = '中断待核对'
        save(receipt_path, receipt)
        raise
    receipt["status"] = "已清除" if purge else "已恢复"
    save(receipt_path, receipt)
    if not purge and receipt.get('automatic_policy'):
        ids = {r['item'].get('id') for r in receipt['files']}
        for item in registry['items']:
            if item.get('id') in ids:
                item['suspended'] = True
        save(STATE+'/登记.json', registry)


def inventory():
    """存量审核清单不等于可执行清单，不按目录名推定可删除。"""
    roots = ["local-data/断箭/批次", "tmp", "temp", "local-data/资源归档"]
    groups, rows, errors = [], [], []
    for base in roots:
        path = checked(base)
        if not path.exists():
            continue
        for child in sorted(path.iterdir()):
            relative = child.relative_to(ROOT).as_posix()
            count, size = 0, 0
            try:
                for filename in files_under(relative):
                    info = checked(filename).stat()
                    rows.append({"path": filename, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
                                 "status": "保护，等待所属任务及依赖审核"})
                    count += 1
                    size += info.st_size
            except (OSError, ValueError) as error:
                errors.append({"path": relative, "error": str(error)})
            groups.append({"path": relative, "files": count, "bytes": size})
    run = STATE + "/存量审核/" + uuid.uuid4().hex
    save(run + "/逐文件清单.json", {"created": time.time(), "executable": False, "groups": groups,
                                   "files": rows, "errors": errors}, exclusive=True)
    lines = ["# 首次存量清理审核清单", "", "状态：等待工程师审核；本清单不可直接执行。", "",
             "所有现存任务默认保护。表中大小表示盘点体积，不代表可释放空间。", "",
             "| 路径 | 文件数 | GiB | 当前处理 |", "| --- | ---: | ---: | --- |"]
    for group in groups:
        lines.append(f"| `{group['path']}` | {group['files']} | {group['bytes'] / 2**30:.3f} | 保护待审 |")
    lines += ["", "审核顺序：先核对临时恢复副本，再核对已结束的测试缓存，最后逐项审查历史载荷。",
              "当前正式库、网页运行派生、原型和丧尸工程不在本轮删除范围。",
              "冷归档仅作体积对照；当前唯一有效归档不得删除。",
              "获批后先明确结束所属任务、迁移依赖和登记具体候选，再生成含SHA256的执行预览供复核。",
              f"扫描异常：{len(errors)}；有异常的范围须先排查。"]
    path = checked(run + "/首次存量清理审核单.md")
    with path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    print(run + "/首次存量清理审核单.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "inventory", "preview", "tick", "protect", "close", "enroll", "quarantine", "restore", "purge", "enable-cache", "enable-auto", "stage", "accept", "status", "repair-receipt"])
    parser.add_argument("--task")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--note")
    parser.add_argument("--kind", choices=["cache", "history"], default="cache")
    parser.add_argument("--proof", help="项目相对JSON；历史版验收、替代件及依赖审查记录")
    parser.add_argument("--manifest")
    parser.add_argument("--approved-sha")
    parser.add_argument("--asset", help="同一制作资源的稳定ID")
    parser.add_argument("--version")
    parser.add_argument("--replacement", action="append", default=[])
    parser.add_argument("--evidence")
    parser.add_argument(
        "--release-bundle",
        action="store_true",
        help="仅限丧尸群体发布根目录中的候选批次，允许整包登记全部文件类型",
    )
    args = parser.parse_args()
    with lock():
        if args.command == "init":
            initialize()
            return
        if args.command == "inventory":
            inventory()
            return
        registry = read(STATE + "/登记.json")
        engine = automatic(registry)
        if args.command == 'status':
            engine.status(args.task)
            return
        if args.command == 'repair-receipt':
            repair_receipt_policy(registry, args.manifest, args.approved_sha, args.note)
            return
        if args.command == 'enable-auto':
            engine.enable(args.approved_sha, args.note)
            return
        if args.command in ('stage', 'accept'):
            if not engine.enabled():
                raise ValueError('自动维护尚未启用')
            if args.command == 'stage':
                engine.stage(args)
            else:
                engine.accept(args.manifest, args.approved_sha, args.note)
            return
        if args.command == 'enroll' and engine.enabled():
            if args.kind != 'cache':
                raise ValueError('自动历史退役须先stage，再由工程师确认后accept')
            engine.enroll_cache(args.task, args.path, args.note)
            return
        if args.command == 'tick' and engine.enabled():
            engine.tick()
            return
        if args.command in ("preview", "tick"):
            if args.command == "tick" and registry["automatic_cache"] and registry["first_review"] == "approved":
                # 先清除到期回收，避免同范围下一代候选阻断回收。
                for receipt_file in checked(STATE + "/回收").glob("*/回执.json"):
                    receipt_path = receipt_file.relative_to(ROOT).as_posix()
                    receipt = read(receipt_path)
                    if receipt["status"] == "完成" and receipt["files"] and all(
                        r["item"]["kind"] == "cache" and r["state"] == "stored"
                        and time.time() - r["moved_at"] >= 7 * DAY and not blockers(registry, r["path"])
                        for r in receipt["files"]
                    ):
                        recover_or_purge(registry, receipt_path, purge=True)
                        print("到期缓存已清除：" + receipt_path)
            manifest, report = preview(registry, scheduled=args.command == "tick")
            if args.command == "tick" and registry["automatic_cache"] and registry["first_review"] == "approved":
                if report["files"]:
                    quarantine(registry, manifest, digest(checked(manifest)), cache_only=True)
            return
        if args.command in ("protect", "close", "enroll") and not args.task:
            raise ValueError("必须指定任务")
        if args.command == "enable-cache":
            if not args.note or args.approved_sha != digest(checked(STATE + "/登记.json")):
                raise ValueError("启用执行需工程师批准依据及当前登记摘要")
            registry.update(automatic_cache=True, first_review="approved", approval_note=args.note)
        elif args.command == "protect":
            if not args.path:
                raise ValueError("必须登记任务使用的目录或文件")
            for relative in args.path:
                checked(relative)
            previous = registry["tasks"].get(args.task, {}).get("paths", [])
            old = registry['tasks'].get(args.task, {})
            registry["tasks"][args.task] = {**old, "status": "active", "paths": sorted(set(previous + args.path)), "created_at":old.get('created_at', 0 if old else time.time())}
        elif args.command == "close":
            if not args.note:
                raise ValueError("明确结束任务须记录完成依据")
            registry["tasks"][args.task].update(status="closed", note=args.note, closed_at=time.time())
        elif args.command == "enroll":
            if len(args.path) != 1 or args.task not in registry["tasks"]:
                raise ValueError("每次登记一个范围和已存在的任务")
            allowed(args.path[0], args.kind)
            item = {"path": args.path[0], "task": args.task, "kind": args.kind}
            if args.kind == "history":
                if not args.proof:
                    raise ValueError("历史版本需要验收与依赖证据")
                item["proof"] = read(args.proof)
                proof_check(item)
            if any(overlap(item["path"], p["path"]) for p in registry["items"]):
                raise ValueError("重复或重叠登记")
            registry["items"].append(item)
        elif args.command == "quarantine":
            if not args.manifest or not args.approved_sha:
                raise ValueError("需指定已获工程师批准的预览清单及摘要")
            quarantine(registry, args.manifest, args.approved_sha)
            return
        elif args.command in ("restore", "purge"):
            if not args.manifest:
                raise ValueError("需指定回收回执")
            if args.command == "purge" and (not args.approved_sha or digest(checked(args.manifest)) != args.approved_sha):
                raise ValueError("永久清除需提供批准的回执摘要")
            recover_or_purge(registry, args.manifest, args.command == "purge")
            return
        save(STATE + "/登记.json", registry)
        print("登记已更新")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"停止：{error}", file=sys.stderr)
        sys.exit(1)
