"""显式归档解析：保持原记录不变，按记录指纹与字段读取同摘要正式副本。"""
import csv
import io
import json
from pathlib import Path
from urllib.parse import unquote

from 资料库核心 import ROOT, LIB, BASE, read, sha, check_file


def path_key(path):
    return str(Path(path).resolve()).replace("\\", "/").casefold()


def reference_key(ref):
    return json.dumps({k: ref.get(k) for k in ("source", "field", "method", "value", "locator")}, ensure_ascii=False, sort_keys=True)


def normalized_text(text):
    text = text.casefold()
    for _ in range(3): text = text.replace("\\\\", "\\")
    return text.replace("\\", "/")


class ArchiveResolver:
    def __init__(self, manifest, require_verified=False):
        self.manifest_path = Path(manifest).resolve()
        boundary = ROOT / "local-data/断箭/维护/旧版退役与引用迁移"
        if not self.manifest_path.is_relative_to(boundary.resolve()): raise ValueError("归档清单不在维护边界内")
        self.manifest = read(self.manifest_path)
        if self.manifest.get("schema_version") != 1: raise ValueError("未知归档清单格式")
        self.rows = {}
        self.documents = {}
        self.checked = {}
        self.trace = []
        for row in self.manifest["files"]:
            old = Path(row["old"]["path"]).resolve()
            target = Path(row["replacement"]["path"]).resolve()
            if not old.is_relative_to(BASE.resolve()) or not target.is_relative_to((LIB / "资源").resolve()):
                raise ValueError("归档路径超出明确边界")
            if any(row["old"][k] != row["replacement"][k] for k in ("sha256", "bytes")):
                raise ValueError("归档替代内容身份不一致")
            key = path_key(old)
            if key in self.rows: raise ValueError("归档源路径重复")
            self.rows[key] = row
        protected = {path_key(r["path"]) for r in self.manifest["protected"]}
        if protected & self.rows.keys(): raise ValueError("映射包含保护文件")
        if require_verified:
            proof = read(self.manifest_path.parent / "隔离核验.json")
            if proof.get("status") != "passed" or proof.get("manifest_sha256") != sha(self.manifest_path):
                raise ValueError("清单没有相符的隔离核验回执")
            if set(proof["verified_paths"]) != set(self.rows): raise ValueError("隔离核验未覆盖全部映射")

    def has(self, old):
        return path_key(old) in self.rows

    def _document(self, ref):
        path = Path(ref["source"]).resolve()
        if not path.is_relative_to(ROOT): raise ValueError("引用来源超出工作区")
        expected = ref["source_sha256"]
        stat = path.stat()
        cache_key = (str(path), expected)
        signature = (stat.st_size, stat.st_mtime_ns)
        if cache_key in self.documents:
            cached_signature, parsed, text = self.documents[cache_key]
            if cached_signature != signature: raise ValueError("来源记录在回读中变化")
            return parsed, text
        if sha(path) != expected: raise ValueError("来源记录摘要变化：" + str(path))
        text = path.read_text(encoding=ref.get("source_encoding", "utf-8-sig"))
        kind = ref["locator"]["type"]
        parsed = json.loads(text) if kind.startswith("json") else list(csv.reader(io.StringIO(text))) if kind == "csv" else None
        self.documents[cache_key] = (signature, parsed, text)
        return parsed, text

    def verify_reference(self, ref):
        locator = ref.get("locator")
        if not locator: raise ValueError("引用缺少精确字段定位")
        parsed, text = self._document(ref)
        kind = locator["type"]
        if kind == "text_alias":
            if ref["value"] not in normalized_text(text): raise ValueError("文本引用线索不再存在")
            return
        if kind == "csv": value = parsed[locator["row"]][locator["column"]]
        else:
            value = parsed
            for token in locator["tokens"]: value = value[token]
            if kind == "json_key":
                if locator["key"] not in value: raise ValueError("JSON引用键不存在")
                value = locator["key"]
        value = unquote(str(value)).strip().removeprefix("file:///")
        if value != ref["value"]: raise ValueError("原引用字段值变化")

    def verify_coverage(self, old, incoming):
        row = self.rows[path_key(old)]
        known = {reference_key(r) for r in row["references"]}
        actual = {reference_key(r) for r in incoming}
        if actual != known: raise ValueError("引用集合变化或没有完整迁移")
        for ref in row["references"]:
            if ref["kind"] == "active_task": raise ValueError("其他对话引用不能归档放行")
            self.verify_reference(ref)
        return self.resolve(old)

    def resolve(self, old):
        key = path_key(old)
        if key not in self.rows: raise ValueError("旧资源没有唯一的显式归档映射")
        row = self.rows[key]
        for ref in row["references"]: self.verify_reference(ref)
        target = Path(row["replacement"]["path"])
        stat = target.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if key not in self.checked or self.checked[key] != signature:
            check_file(row["replacement"]); self.checked[key] = signature
        self.trace.append({"old_path": row["old"]["path"], "read_path": str(target), "sha256": row["old"]["sha256"], "references": len(row["references"])})
        return target
