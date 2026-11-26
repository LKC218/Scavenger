"""本地工作项看板多 AI Agent 协作 CLI 工具（同源调用 /api/v1/*，零第三方依赖）。

用法示例：
  python -X utf8 -B scripts/工作项看板/看板工具.py meta
  python -X utf8 -B scripts/工作项看板/看板工具.py list --project SCAV
  python -X utf8 -B scripts/工作项看板/看板工具.py pull SCAV-3
  python -X utf8 -B scripts/工作项看板/看板工具.py create --title "[缺陷] 21:9画幅下投弹圈偏移" --module "操作交互与战术标绘" --priority high --label "缺陷修复,UI/UX" --scope "src/交互系统/" --desc-file tmp/item.md --dry-run
  python -X utf8 -B scripts/工作项看板/看板工具.py update SCAV-3 --add-label "需评审" --scope "src/交互系统/指令与标绘层.mjs"
  python -X utf8 -B scripts/工作项看板/看板工具.py lint --all
  python -X utf8 -B scripts/工作项看板/看板工具.py claim SCAV-3 --agent "Claude-主程" --branch "feat/scav-3"
  python -X utf8 -B scripts/工作项看板/看板工具.py submit-plan SCAV-3 --plan-file tmp/plan.md
  python -X utf8 -B scripts/工作项看板/看板工具.py approve-plan SCAV-3
  python -X utf8 -B scripts/工作项看板/看板工具.py heartbeat SCAV-3 --note "编码完成，开始测试"
  python -X utf8 -B scripts/工作项看板/看板工具.py submit-qa SCAV-3 --note "单测通过"
  python -X utf8 -B scripts/工作项看板/看板工具.py reject SCAV-3 --reason "缺少边缘用例"
  python -X utf8 -B scripts/工作项看板/看板工具.py done SCAV-3 --note "人工验收通过"
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_URL = "http://127.0.0.1:18800"
_OPENER = build_opener(ProxyHandler({}))

# ---------- 工作项编写规范（SSOT 机械门禁） ----------

VALID_TYPE_PREFIXES: tuple[str, ...] = ("[需求]", "[缺陷]", "[优化]", "[任务]", "[资产]")

REQUIRED_SECTIONS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "[缺陷]": ("触发场景", "复现步骤", "实际表现", "预期表现"),
    "[需求]": ("背景与目标", "核心变更", "验收标准"),
    "[优化]": ("背景与目标", "核心变更", "验收标准"),
    "[任务]": ("任务目标", "执行范围", "完成标准"),
    "[资产]": ("资产对象与来源", "交付规格", "验收与退役要求"),
}

OVERLY_BROAD_SCOPES: set[str] = {
    "src",
    "scripts",
    "docs",
    "reports",
    "public",
    "local-data",
    "tmp",
    ".",
}

ACTIVE_CONFLICT_STATES: set[str] = {"plan_review", "in_progress"}

_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/|\\\\[A-Za-z0-9_.-]+\\)")
_TITLE_PREFIX_RE = re.compile(r"^(\[[^\]]+\])\s*(.*)$")
_HEADING_RE = re.compile(r"^\s*#{2,4}\s+(.+?)\s*$", re.MULTILINE)


def split_csv_args(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        for part in str(raw).replace("，", ",").split(","):
            item = part.strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item)
    return result


def read_text_arg(inline_text: str, file_path: str, *, label: str) -> str:
    if inline_text and file_path:
        raise SystemExit(f"[错误] {label} 不能同时传入文本与文件路径")
    if file_path:
        if file_path == "-":
            return sys.stdin.read()
        fp = Path(file_path)
        if not fp.is_file():
            raise SystemExit(f"[错误] {label}文件不存在：{fp}")
        return fp.read_text(encoding="utf-8")
    return inline_text


def normalize_rel_path(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def paths_overlap(path_a: str, path_b: str) -> bool:
    a = normalize_rel_path(path_a).lower()
    b = normalize_rel_path(path_b).lower()
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def count_effective_chars(markdown_text: str) -> int:
    """统计正文有效字数（剔除图片链接、纯符号与空白）。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown_text or "")
    text = re.sub(r"`{3}[\s\S]*?`{3}", "", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s*(\[[ xX]\]\s*)?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", "", text)
    return len(text)


def lint_work_item_spec(
    *,
    title: str,
    description_md: str,
    state: str = "todo",
    scope_paths: list[str] | None = None,
    module_name: str = "",
    labels: list[str] | None = None,
    active_items: list[dict[str, Any]] | None = None,
    exclude_key: str = "",
) -> dict[str, Any]:
    """按 Scavenger 工作项编写规范执行机械校验，返回 errors / warnings / conflicts / metrics。"""
    errors: list[str] = []
    warnings: list[str] = []
    conflicts: list[dict[str, Any]] = []

    clean_title = (title or "").strip()
    clean_desc = (description_md or "").strip()
    norm_scopes = [normalize_rel_path(p) for p in (scope_paths or []) if (p or "").strip()]

    # 1. 标题规范
    m = _TITLE_PREFIX_RE.match(clean_title)
    type_prefix = ""
    if not m or m.group(1) not in VALID_TYPE_PREFIXES:
        errors.append(f"标题必须以规范类型前缀开头：{' / '.join(VALID_TYPE_PREFIXES)}（示例：[缺陷] 21:9画幅下投弹圈偏移）")
    else:
        type_prefix = m.group(1)
        body_title = m.group(2).strip()
        if len(body_title) < 4:
            errors.append("标题类型前缀后的核心描述过短（至少 4 个字，需写明对象/场景与现象或目标）")
    if len(clean_title) > 42:
        errors.append(f"标题长度 {len(clean_title)} 字超出硬上限（建议 ≤ 32 字，硬上限 42 字）")
    elif len(clean_title) > 32:
        warnings.append(f"标题长度 {len(clean_title)} 字略长（建议精简至 ≤ 32 字，便于看板窄卡完整展示）")

    # 2. 正文边界（只写 What，控字数，含必填小节）
    eff_chars = count_effective_chars(clean_desc)
    if not clean_desc or eff_chars < 25:
        errors.append(f"正文有效字数仅 {eff_chars} 字，缺少清晰需求/现象与验收口径（建议 150 ~ 350 字）")
    elif eff_chars < 90:
        warnings.append(f"正文有效字数偏少（{eff_chars} 字，建议 150 ~ 350 字，确保背景与验收标准完整）")
    elif eff_chars > 650:
        errors.append(f"正文有效字数 {eff_chars} 字严重超预算（建议 150 ~ 350 字，上限 650 字；实施方案请写进 plan_md，源码线索请放首条评论）")
    elif eff_chars > 420:
        warnings.append(f"正文有效字数 {eff_chars} 字偏长（建议精简至 150 ~ 350 字，只写 What 不写 How）")

    if type_prefix and type_prefix in REQUIRED_SECTIONS_BY_TYPE:
        headings = [h.strip() for h in _HEADING_RE.findall(clean_desc)]
        missing = [
            sec
            for sec in REQUIRED_SECTIONS_BY_TYPE[type_prefix]
            if not any(sec in h for h in headings)
        ]
        if missing:
            errors.append(f"{type_prefix} 正文缺少规范小节：{', '.join('### ' + s for s in missing)}")

    if _ABS_PATH_RE.search(clean_desc):
        errors.append("正文包含本机绝对路径（如盘符或用户目录），请改用项目相对路径或目录别名")

    code_fences = len(re.findall(r"```", clean_desc)) // 2
    if code_fences >= 2:
        warnings.append(f"正文包含 {code_fences} 处代码块；若为源码排查或改造方案，请移至首条评论或 plan_md")

    # 3. 所属模块与标签建议
    if not module_name:
        warnings.append("未指定所属模块（--module）；绑定模块后 Agent 拉单可自动注入架构红线与验证命令")
    if not labels:
        warnings.append("未指定标签（--label）；建议从看板已有标签中选配 1~2 个")

    # 4. 作用域路径（激活防撞雷达）
    for raw_p in scope_paths or []:
        txt = (raw_p or "").strip().replace("\\", "/")
        if not txt:
            continue
        if ":" in txt.split("/")[0] or ".." in txt.split("/"):
            errors.append(f"非法作用域路径：{raw_p}（必须为合法项目相对路径）")
    if state != "backlog" and not norm_scopes:
        errors.append("非待规划（backlog）工作项必须填写 1~3 条作用域相对路径（--scope），用于多 Agent 路径防撞预警")
    for p in norm_scopes:
        if p.lower() in OVERLY_BROAD_SCOPES:
            warnings.append(f"作用域路径「{p}」过于宽泛，易造成多 Agent 误报冲突，建议精确到子目录或核心文件")

    # 5. 对照当前活动工作项预演路径冲突
    if norm_scopes and active_items:
        for other in active_items:
            if exclude_key and str(other.get("item_key", "")).upper() == exclude_key.upper():
                continue
            if other.get("state") not in ACTIVE_CONFLICT_STATES:
                continue
            overlaps: list[dict[str, str]] = []
            for pa in norm_scopes:
                for pb in other.get("scope_paths") or []:
                    if paths_overlap(pa, pb):
                        overlaps.append({"a": pa, "b": pb})
            if overlaps:
                conflicts.append({
                    "other_key": other["item_key"],
                    "other_state": other["state"],
                    "other_title": other["title"],
                    "overlaps": overlaps,
                })

    return {
        "ok": len(errors) == 0,
        "type_prefix": type_prefix,
        "title_length": len(clean_title),
        "effective_chars": eff_chars,
        "errors": errors,
        "warnings": warnings,
        "conflicts": conflicts,
    }


def print_lint_report(label: str, report: dict[str, Any]) -> None:
    badge = "通过" if report["ok"] else "不通过"
    print(f"[规范校验:{badge}] {label} | 标题 {report['title_length']} 字 | 正文 {report['effective_chars']} 字")
    for err in report["errors"]:
        print(f"  [错误] {err}")
    for warn in report["warnings"]:
        print(f"  [提示] {warn}")
    for c in report["conflicts"]:
        ov = ", ".join(f"{o['a']} ∩ {o['b']}" for o in c["overlaps"])
        print(f"  [⚠路径冲突预警] 与 {c['other_key']} ({c['other_state']}) {c['other_title']} 重叠: {ov}")


# ---------- HTTP 客户端 ----------

class BoardClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json", "Connection": "close"}
        if self.token:
            headers["X-Board-Token"] = self.token
        data_bytes = None
        if body is not None:
            data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(f"{self.base_url}{path}", data=data_bytes, headers=headers, method=method)
        try:
            with _OPENER.open(req, timeout=10.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                err_obj = json.loads(raw)
                msg = err_obj.get("message") or raw
            except Exception:
                msg = raw
            raise SystemExit(f"[HTTP {exc.code}] {msg}")
        except URLError as exc:
            raise SystemExit(f"[连接失败] 无法访问 {self.base_url}（请确认 18800 看板服务已启动）：{exc.reason}")

    def bootstrap(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/bootstrap")

    def resolve_project_id(self, project_ref: str) -> tuple[int, dict[str, Any]]:
        boot = self.bootstrap()
        projects = boot["projects"]
        if not projects:
            raise SystemExit("[错误] 看板中尚无项目")
        if not project_ref:
            return int(projects[0]["id"]), boot
        for p in projects:
            if str(p["id"]) == str(project_ref) or p["key"].upper() == project_ref.upper():
                return int(p["id"]), boot
        raise SystemExit(f"[错误] 找不到项目：{project_ref}")

    def resolve_member_id(self, members: list[dict[str, Any]], name_or_id: str) -> int | None:
        if not name_or_id:
            return None
        q = name_or_id.strip().lower()
        for m in members:
            if str(m["id"]) == q or m["name"].lower() == q or m["display_name"].lower() == q:
                return int(m["id"])
        raise SystemExit(f"[错误] 找不到成员或 Agent：{name_or_id}")

    def resolve_module(self, modules: list[dict[str, Any]], name_or_id: str) -> tuple[int | None, str]:
        if not name_or_id:
            return None, ""
        for m in modules:
            if str(m["id"]) == str(name_or_id) or m["name"] == name_or_id:
                return int(m["id"]), str(m["name"])
        available = ", ".join(m["name"] for m in modules)
        raise SystemExit(f"[错误] 找不到模块「{name_or_id}」，当前项目可用模块：{available}")

    def resolve_label_ids(self, labels: list[dict[str, Any]], names_or_ids: list[str]) -> tuple[list[int], list[str]]:
        ids: list[int] = []
        names: list[str] = []
        seen: set[int] = set()
        for raw in split_csv_args(names_or_ids):
            found = None
            for lb in labels:
                if str(lb["id"]) == raw or lb["name"].lower() == raw.lower():
                    found = lb
                    break
            if not found:
                available = ", ".join(lb["name"] for lb in labels)
                raise SystemExit(f"[错误] 找不到标签「{raw}」（严禁自造标签），当前项目可用标签：{available}")
            lid = int(found["id"])
            if lid not in seen:
                seen.add(lid)
                ids.append(lid)
                names.append(str(found["name"]))
        return ids, names


def upload_image(client: BoardClient, item_key: str, file_path: Path) -> dict[str, Any]:
    if not file_path.is_file():
        raise SystemExit(f"[错误] 附件文件不存在：{file_path}")
    raw = file_path.read_bytes()
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return client.request(
        "POST",
        f"/api/v1/items/{quote(item_key)}/attachments",
        {
            "filename": file_path.name,
            "mime_type": mime,
            "data_base64": base64.b64encode(raw).decode("ascii"),
        },
    )


def print_pull_readable(item: dict[str, Any]) -> None:
    print(f"=== {item['item_key']} | {item['title']} ===")
    print(f"状态: {item['state']} | 优先级: {item['priority']} | rev: {item['rev']} | 分支: {item.get('agent_branch') or '-'}")
    if item.get("labels"):
        print(f"标签: {', '.join(lb['name'] for lb in item['labels'])}")
    if item.get("scope_paths"):
        print(f"作用域路径: {', '.join(item['scope_paths'])}")
    if item.get("blocked_by_details"):
        blk = ", ".join(f"{b['item_key']}({b['state']}) {b['title']}" for b in item["blocked_by_details"])
        print(f"前置依赖: {blk}")
    elif item.get("blocked_by"):
        print(f"前置依赖: {', '.join(item['blocked_by'])}")
    if item.get("conflicts"):
        print("\n[⚠ 作用域路径冲突预警]")
        for c in item["conflicts"]:
            ov = ", ".join(f"{o['a']} ∩ {o['b']}" for o in c["overlaps"])
            print(f"  - 与 {c['other_key']} ({c['other_state']}) {c['other_title']} 重叠: {ov}")
    if item.get("module_context"):
        mc = item["module_context"]
        cat = mc.get("category") or "未分类"
        print(f"\n[模块上下文包: {cat} / {mc['name']}]")
        if mc.get("scope_desc"):
            print(f"  职责范围: {mc['scope_desc']}")
        if mc.get("capabilities"):
            print(f"  现有能力: {mc['capabilities']}")
        if mc.get("redlines"):
            print(f"  架构红线: {mc['redlines']}")
        if mc.get("entry_paths"):
            print(f"  入口路径: {', '.join(mc['entry_paths'])}")
        if mc.get("verify_cmd"):
            print(f"  验证命令: {mc['verify_cmd']}")
    print("\n[精简需求描述 (What)]")
    print(item.get("description_md") or "(空)")
    if item.get("plan_md"):
        print("\n[实施方案 (Plan)]")
        print(item["plan_md"])
    if item.get("attachments"):
        print("\n[附件列表]")
        for a in item["attachments"]:
            local_hint = f"local-data/工作项看板/附件/{a['sha256']}{a['ext']}" if a.get("sha256") and a.get("ext") else ""
            hint_str = f" | 本机文件: {local_hint}" if local_hint else ""
            print(f"  - {a['filename']} ({a['size_bytes']} B) -> {a['url']}{hint_str}")
    if item.get("comments"):
        print("\n[最近活动与评论]")
        for c in item["comments"][-8:]:
            print(f"  [{c['created_at']}] {c['author_name']} ({c['comment_type']}): {c['body_md']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("LOCAL_BOARD_URL", DEFAULT_URL), help=f"看板服务地址（默认 {DEFAULT_URL}）")
    parser.add_argument("--token", default=os.environ.get("LOCAL_BOARD_TOKEN", ""), help="Agent API Token（X-Board-Token）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_meta = sub.add_parser("meta", help="查询项目、状态、模块、标签与成员")
    p_meta.add_argument("--project", default="", help="项目代号或 ID")

    p_list = sub.add_parser("list", help="列出项目工作项")
    p_list.add_argument("--project", default="", help="项目代号或 ID")
    p_list.add_argument("--state", default="", help="按状态筛选")

    p_conflicts = sub.add_parser("conflicts", help="查看当前活动工作项的作用域路径冲突")
    p_conflicts.add_argument("--project", default="", help="项目代号或 ID")

    p_pull = sub.add_parser("pull", help="拉取工作项完整需求、附件与模块上下文包")
    p_pull.add_argument("key", help="工作项编号（如 SCAV-3）或 ID")

    p_create = sub.add_parser("create", help="新建工作项（支持规范校验与 --dry-run 预演）")
    p_create.add_argument("--project", default="", help="项目代号或 ID")
    p_create.add_argument("--title", required=True, help="工作项标题（推荐：[类型] 对象/场景 + 目标或现象）")
    p_create.add_argument("--state", default="todo", help="初始状态（默认 todo；暂不排期用 backlog）")
    p_create.add_argument("--priority", default="medium", help="优先级（urgent/high/medium/low/none）")
    p_create.add_argument("--module", default="", help="所属模块名或 ID")
    p_create.add_argument("--owner", default="", help="人类负责人名称或 ID")
    p_create.add_argument("--agent", default="", help="AI Agent 名称或 ID")
    p_create.add_argument("--label", action="append", default=[], help="标签名或 ID（支持逗号分隔或重复传入，严禁自造）")
    p_create.add_argument("--scope", action="append", default=[], help="项目相对路径（可重复传入）")
    p_create.add_argument("--blocked-by", action="append", default=[], help="前置阻塞工作项编号（如 SCAV-1，支持逗号分隔）")
    p_create.add_argument("--desc", default="", help="精简需求描述（What）")
    p_create.add_argument("--desc-file", default="", help="从 UTF-8 Markdown 文件读取正文（传 - 表示 stdin）")
    p_create.add_argument("--comment", default="", help="可选：作为第 1 条评论附加的外置源码排查线索")
    p_create.add_argument("--comment-file", default="", help="可选：从 UTF-8 文件读取第 1 条评论")
    p_create.add_argument("--image", action="append", default=[], help="附带上传的截图文件路径（可重复传入）")
    p_create.add_argument("--dry-run", action="store_true", help="仅做字段解析、规范校验与路径冲突预演，不写入看板")
    p_create.add_argument("--check-spec", action="store_true", help="写入前强制执行编写规范门禁，存在规范错误时拒绝创建")
    p_create.add_argument("--allow-spec-errors", action="store_true", help="配合 --dry-run 时即使存在规范错误仍返回退出码 0")

    p_upd = sub.add_parser("update", help="更新已有工作项的字段、正文、标签或作用域路径（支持乐观锁）")
    p_upd.add_argument("key", help="工作项编号（如 SCAV-3）或 ID")
    p_upd.add_argument("--title", default=None, help="新标题")
    p_upd.add_argument("--state", default=None, help="新状态")
    p_upd.add_argument("--priority", default=None, help="新优先级")
    p_upd.add_argument("--module", default=None, help="新所属模块（传空字符串可清除）")
    p_upd.add_argument("--owner", default=None, help="新人类负责人")
    p_upd.add_argument("--agent", default=None, help="新 AI Agent")
    p_upd.add_argument("--label", action="append", default=None, help="覆盖设置标签（支持逗号分隔）")
    p_upd.add_argument("--add-label", action="append", default=[], help="追加标签（支持逗号分隔）")
    p_upd.add_argument("--remove-label", action="append", default=[], help="移除标签（支持逗号分隔）")
    p_upd.add_argument("--scope", action="append", default=None, help="覆盖设置作用域路径")
    p_upd.add_argument("--add-scope", action="append", default=[], help="追加作用域路径")
    p_upd.add_argument("--blocked-by", action="append", default=None, help="覆盖设置前置阻塞编号")
    p_upd.add_argument("--desc", default="", help="新正文内容")
    p_upd.add_argument("--desc-file", default="", help="从 UTF-8 文件读取新正文")
    p_upd.add_argument("--dry-run", action="store_true", help="仅预演变更与规范校验，不写库")
    p_upd.add_argument("--check-spec", action="store_true", help="保存前强制校验编写规范")

    p_lint = sub.add_parser("lint", help="只读校验单张或全项目工作项是否符合编写规范")
    p_lint.add_argument("key", nargs="?", default="", help="工作项编号（如 SCAV-3）；省略时需传 --all")
    p_lint.add_argument("--project", default="", help="项目代号或 ID（配合 --all 使用）")
    p_lint.add_argument("--all", action="store_true", help="校验项目内全部未完成工作项")
    p_lint.add_argument("--include-done", action="store_true", help="配合 --all 时一并校验已完成工作项")

    p_claim = sub.add_parser("claim", help="AI Agent 认领工作项并登记分支")
    p_claim.add_argument("key")
    p_claim.add_argument("--agent", default="", help="AI Agent 名称或 ID")
    p_claim.add_argument("--branch", default="", help="分支或 worktree 名称")
    p_claim.add_argument("--host", default=socket.gethostname(), help="主机名")
    p_claim.add_argument("--scope", action="append", default=[], help="补充作用域路径")
    p_claim.add_argument("--note", default="", help="认领备注")

    p_plan = sub.add_parser("submit-plan", help="提交实施方案并进入「方案待审」")
    p_plan.add_argument("key")
    p_plan.add_argument("--plan", default="", help="实施方案摘要")
    p_plan.add_argument("--plan-file", default="", help="从 UTF-8 文件读取实施方案（传 - 表示 stdin）")
    p_plan.add_argument("--branch", default="", help="分支名")
    p_plan.add_argument("--note", default="", help="附加说明")

    for cname, help_txt in (
        ("approve-plan", "批准方案并推进至「进行中」"),
        ("start", "直接推进至「进行中」"),
        ("submit-qa", "提交至「待测试」并附验证结果"),
        ("start-testing", "进入「测试中」"),
        ("done", "确认验收通过并流转至「已完成」"),
    ):
        pc = sub.add_parser(cname, help=help_txt)
        pc.add_argument("key")
        pc.add_argument("--note", default="", help="流转说明")
        pc.add_argument("--branch", default="", help="可选分支名")

    p_rej = sub.add_parser("reject", help="打回至「不通过」")
    p_rej.add_argument("key")
    p_rej.add_argument("--reason", required=True, help="打回原因")

    p_hb = sub.add_parser("heartbeat", help="更新 AI Agent 执行心跳")
    p_hb.add_argument("key")
    p_hb.add_argument("--branch", default="", help="当前分支")
    p_hb.add_argument("--host", default=socket.gethostname(), help="主机名")
    p_hb.add_argument("--note", default="", help="进展心跳评论")

    p_cmt = sub.add_parser("comment", help="追加评论")
    p_cmt.add_argument("key")
    p_cmt.add_argument("body", nargs="?", default="", help="评论正文")
    p_cmt.add_argument("--file", default="", help="从 UTF-8 文件读取评论正文")

    p_att = sub.add_parser("attach", help="上传截图或文件附件")
    p_att.add_argument("key")
    p_att.add_argument("file", help="本地文件路径")

    args = parser.parse_args()
    client = BoardClient(args.url, args.token)

    if args.cmd == "meta":
        pid, boot = client.resolve_project_id(args.project)
        proj_data = client.request("GET", f"/api/v1/projects/{pid}/items")
        out = {
            "service": boot["service"],
            "projects": boot["projects"],
            "active_project": proj_data["project"],
            "modules": proj_data["modules"],
            "labels": proj_data["labels"],
            "members": boot["members"],
            "states": boot["states"],
            "priorities": boot.get("priorities") or [],
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"服务: {boot['service']['local_url']} (mode={boot['service']['auth_mode']})")
            print("项目:", ", ".join(f"{p['key']}({p['name']})" for p in boot["projects"]))
            print("模块:", ", ".join(f"[{m.get('category') or '未分类'}]{m['name']}" for m in proj_data["modules"]))
            print("标签:", ", ".join(lb["name"] for lb in proj_data["labels"]))
            print("成员:", ", ".join(f"{m['display_name']}[{m['kind']}]" for m in boot["members"]))
        return 0

    if args.cmd == "list":
        pid, _ = client.resolve_project_id(args.project)
        data = client.request("GET", f"/api/v1/projects/{pid}/items")
        items = data["items"]
        if args.state:
            items = [i for i in items if i["state"] == args.state]
        if args.json:
            print(json.dumps({"project": data["project"], "items": items, "conflict_pairs": data["conflict_pairs"]}, ensure_ascii=False, indent=2))
        else:
            for it in items:
                warn = " [⚠路径冲突]" if it.get("conflicts") else ""
                print(f"{it['item_key']:<8} [{it['state']:<11}] ({it['priority']:<6}) {it['title']}{warn}")
        return 0

    if args.cmd == "conflicts":
        pid, _ = client.resolve_project_id(args.project)
        data = client.request("GET", f"/api/v1/projects/{pid}/items")
        pairs = data.get("conflict_pairs") or []
        if args.json:
            print(json.dumps({"conflict_pairs": pairs}, ensure_ascii=False, indent=2))
        elif not pairs:
            print("当前活动工作项之间无作用域路径冲突。")
        else:
            for pair in pairs:
                ov = ", ".join(f"{o['a']} ∩ {o['b']}" for o in pair["overlaps"])
                print(f"⚠ {pair['item_a']['item_key']} ↔ {pair['item_b']['item_key']} | 重叠路径: {ov}")
        return 0

    if args.cmd == "pull":
        res = client.request("GET", f"/api/v1/items/{quote(args.key)}")
        if args.json:
            print(json.dumps(res["item"], ensure_ascii=False, indent=2))
        else:
            print_pull_readable(res["item"])
        return 0

    if args.cmd == "create":
        pid, boot = client.resolve_project_id(args.project)
        proj_data = client.request("GET", f"/api/v1/projects/{pid}/items")
        mod_id, mod_name = client.resolve_module(proj_data["modules"], args.module)
        label_ids, label_names = client.resolve_label_ids(proj_data["labels"], args.label)
        desc_text = read_text_arg(args.desc, args.desc_file, label="正文")
        comment_text = read_text_arg(args.comment, args.comment_file, label="首条评论")
        blocked_by = [k.upper() for k in split_csv_args(args.blocked_by)]
        for img in args.image:
            if not Path(img).is_file():
                raise SystemExit(f"[错误] 附件文件不存在：{img}")

        owner_id = client.resolve_member_id(boot["members"], args.owner)
        agent_id = client.resolve_member_id(boot["members"], args.agent)
        payload = {
            "title": args.title.strip(),
            "state": args.state,
            "priority": args.priority,
            "module_id": mod_id,
            "owner_id": owner_id,
            "agent_id": agent_id,
            "label_ids": label_ids,
            "scope_paths": args.scope,
            "blocked_by": blocked_by,
            "description_md": desc_text,
        }
        lint_res = lint_work_item_spec(
            title=payload["title"],
            description_md=desc_text,
            state=args.state,
            scope_paths=args.scope,
            module_name=mod_name,
            labels=label_names,
            active_items=proj_data["items"],
        )

        if args.dry_run:
            preview = {
                "dry_run": True,
                "project": proj_data["project"]["key"],
                "payload": payload,
                "resolved": {
                    "module": mod_name,
                    "labels": label_names,
                    "images": args.image,
                    "has_first_comment": bool(comment_text.strip()),
                },
                "spec_lint": lint_res,
            }
            if args.json:
                print(json.dumps(preview, ensure_ascii=False, indent=2))
            else:
                print(f"=== [DRY-RUN 预览] {proj_data['project']['key']} | {payload['title']} ===")
                print(f"状态: {args.state} | 优先级: {args.priority} | 模块: {mod_name or '(未指定)'} | 标签: {', '.join(label_names) or '(无)'}")
                print(f"作用域路径: {', '.join(args.scope) or '(无)'} | 前置依赖: {', '.join(blocked_by) or '(无)'}")
                print_lint_report(payload["title"], lint_res)
            return 0 if (lint_res["ok"] or args.allow_spec_errors) else 2

        if args.check_spec and not lint_res["ok"]:
            if args.json:
                print(json.dumps({"ok": False, "error": "spec_lint_failed", "spec_lint": lint_res}, ensure_ascii=False, indent=2))
            else:
                print_lint_report(payload["title"], lint_res)
            return 2

        res = client.request("POST", f"/api/v1/projects/{pid}/items", payload)
        item = res["item"]
        for img in args.image:
            upload_image(client, item["item_key"], Path(img))
        if comment_text.strip():
            client.request("POST", f"/api/v1/items/{quote(item['item_key'])}/comments", {"body_md": comment_text.strip()})
            item = client.request("GET", f"/api/v1/items/{quote(item['item_key'])}")["item"]
        if args.json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            print(f"[已创建] {item['item_key']} | {item['title']} (state={item['state']})")
            if lint_res["warnings"] or lint_res["conflicts"]:
                print_lint_report(item["item_key"], lint_res)
        return 0

    if args.cmd == "update":
        detail = client.request("GET", f"/api/v1/items/{quote(args.key)}")["item"]
        boot = client.bootstrap()
        proj_data = client.request("GET", f"/api/v1/projects/{detail['project_id']}/items")
        patch: dict[str, Any] = {"expected_rev": detail["rev"]}

        new_title = args.title.strip() if args.title is not None else detail["title"]
        if args.title is not None:
            patch["title"] = new_title

        new_state = args.state if args.state is not None else detail["state"]
        if args.state is not None:
            patch["state"] = new_state

        if args.priority is not None:
            patch["priority"] = args.priority

        mod_name = (detail.get("module_context") or {}).get("name") or ""
        if args.module is not None:
            if args.module == "":
                patch["module_id"] = None
                mod_name = ""
            else:
                mid, mod_name = client.resolve_module(proj_data["modules"], args.module)
                patch["module_id"] = mid

        if args.owner is not None:
            patch["owner_id"] = client.resolve_member_id(boot["members"], args.owner) if args.owner else None
        if args.agent is not None:
            patch["agent_id"] = client.resolve_member_id(boot["members"], args.agent) if args.agent else None

        # 标签计算（支持覆盖或增量增删）
        cur_label_ids = list(detail.get("label_ids") or [])
        cur_label_map = {int(lb["id"]): str(lb["name"]) for lb in proj_data["labels"]}
        if args.label is not None or args.add_label or args.remove_label:
            if args.label is not None:
                next_ids, _ = client.resolve_label_ids(proj_data["labels"], args.label)
            else:
                next_ids = list(cur_label_ids)
            if args.add_label:
                add_ids, _ = client.resolve_label_ids(proj_data["labels"], args.add_label)
                for lid in add_ids:
                    if lid not in next_ids:
                        next_ids.append(lid)
            if args.remove_label:
                rem_ids, _ = client.resolve_label_ids(proj_data["labels"], args.remove_label)
                next_ids = [lid for lid in next_ids if lid not in set(rem_ids)]
            patch["label_ids"] = next_ids
            next_label_names = [cur_label_map[lid] for lid in next_ids if lid in cur_label_map]
        else:
            next_label_names = [lb["name"] for lb in detail.get("labels") or []]

        # 作用域路径计算
        next_scopes = list(detail.get("scope_paths") or [])
        if args.scope is not None or args.add_scope:
            if args.scope is not None:
                next_scopes = list(args.scope)
            if args.add_scope:
                for sp in args.add_scope:
                    if sp not in next_scopes:
                        next_scopes.append(sp)
            patch["scope_paths"] = next_scopes

        if args.blocked_by is not None:
            patch["blocked_by"] = [k.upper() for k in split_csv_args(args.blocked_by)]

        new_desc = detail.get("description_md") or ""
        if args.desc or args.desc_file:
            new_desc = read_text_arg(args.desc, args.desc_file, label="正文")
            patch["description_md"] = new_desc

        lint_res = lint_work_item_spec(
            title=new_title,
            description_md=new_desc,
            state=new_state,
            scope_paths=next_scopes,
            module_name=mod_name,
            labels=next_label_names,
            active_items=proj_data["items"],
            exclude_key=detail["item_key"],
        )

        if args.dry_run:
            out = {"dry_run": True, "item_key": detail["item_key"], "patch": patch, "spec_lint": lint_res}
            if args.json:
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print(f"=== [DRY-RUN 更新预览] {detail['item_key']} | {new_title} ===")
                print_lint_report(detail["item_key"], lint_res)
            return 0 if lint_res["ok"] else 2

        if args.check_spec and not lint_res["ok"]:
            if args.json:
                print(json.dumps({"ok": False, "error": "spec_lint_failed", "spec_lint": lint_res}, ensure_ascii=False, indent=2))
            else:
                print_lint_report(detail["item_key"], lint_res)
            return 2

        res = client.request("PATCH", f"/api/v1/items/{quote(detail['item_key'])}", patch)
        item = res["item"]
        if args.json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            print(f"[已更新] {item['item_key']} | {item['title']} (rev={item['rev']})")
        return 0

    if args.cmd == "lint":
        if not args.all and not args.key:
            raise SystemExit("[错误] 请指定工作项编号（如 SCAV-1）或传入 --all")
        if args.key:
            detail = client.request("GET", f"/api/v1/items/{quote(args.key)}")["item"]
            proj_data = client.request("GET", f"/api/v1/projects/{detail['project_id']}/items")
            rep = lint_work_item_spec(
                title=detail["title"],
                description_md=detail.get("description_md") or "",
                state=detail["state"],
                scope_paths=detail.get("scope_paths") or [],
                module_name=(detail.get("module_context") or {}).get("name") or "",
                labels=[lb["name"] for lb in detail.get("labels") or []],
                active_items=proj_data["items"],
                exclude_key=detail["item_key"],
            )
            if args.json:
                print(json.dumps({"item_key": detail["item_key"], **rep}, ensure_ascii=False, indent=2))
            else:
                print_lint_report(f"{detail['item_key']} {detail['title']}", rep)
            return 0 if rep["ok"] else 2

        pid, _ = client.resolve_project_id(args.project)
        proj_data = client.request("GET", f"/api/v1/projects/{pid}/items")
        mod_map = {int(m["id"]): m["name"] for m in proj_data["modules"]}
        targets = [
            it for it in proj_data["items"]
            if args.include_done or it["state"] != "done"
        ]
        reports: list[dict[str, Any]] = []
        failed = 0
        for it in targets:
            rep = lint_work_item_spec(
                title=it["title"],
                description_md=it.get("description_md") or "",
                state=it["state"],
                scope_paths=it.get("scope_paths") or [],
                module_name=mod_map.get(it["module_id"], "") if it.get("module_id") else "",
                labels=[lb["name"] for lb in it.get("labels") or []],
                active_items=proj_data["items"],
                exclude_key=it["item_key"],
            )
            if not rep["ok"]:
                failed += 1
            reports.append({"item_key": it["item_key"], "title": it["title"], "state": it["state"], **rep})
        summary = {"project": proj_data["project"]["key"], "total": len(reports), "passed": len(reports) - failed, "failed": failed, "reports": reports}
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"规范巡检完成：{summary['project']} 共 {summary['total']} 条（通过 {summary['passed']} / 需整改 {summary['failed']}）")
            for r in reports:
                print_lint_report(f"{r['item_key']} {r['title']}", r)
        return 0 if failed == 0 else 2

    if args.cmd == "claim":
        boot = client.bootstrap()
        payload_claim: dict[str, Any] = {
            "action": "claim",
            "agent_id": client.resolve_member_id(boot["members"], args.agent),
            "agent_branch": args.branch,
            "agent_host": args.host,
            "note": args.note,
        }
        if args.scope:
            payload_claim["scope_paths"] = args.scope
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/transition", payload_claim)
        print(json.dumps(res["item"], ensure_ascii=False, indent=2) if args.json else f"[已认领] {res['item']['item_key']} (branch={res['item']['agent_branch']})")
        return 0

    if args.cmd == "submit-plan":
        plan_text = read_text_arg(args.plan, args.plan_file, label="实施方案").strip()
        if not plan_text:
            raise SystemExit("[错误] 请通过 --plan 或 --plan-file 提供实施方案内容")
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/transition", {
            "action": "submit_plan",
            "plan_md": plan_text,
            "agent_branch": args.branch,
            "note": args.note or "已提交实施方案，等待人工审阅批准。",
        })
        print(json.dumps(res["item"], ensure_ascii=False, indent=2) if args.json else f"[已提交方案] {res['item']['item_key']} -> plan_review")
        return 0

    if args.cmd in ("approve-plan", "start", "submit-qa", "start-testing", "done"):
        act_map = {
            "approve-plan": "approve_plan",
            "start": "start",
            "submit-qa": "submit_qa",
            "start-testing": "start_testing",
            "done": "done",
        }
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/transition", {
            "action": act_map[args.cmd],
            "note": args.note,
            "agent_branch": args.branch,
        })
        print(json.dumps(res["item"], ensure_ascii=False, indent=2) if args.json else f"[已流转] {res['item']['item_key']} -> {res['item']['state']} (rev={res['item']['rev']})")
        return 0

    if args.cmd == "reject":
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/transition", {
            "action": "reject",
            "reason": args.reason,
        })
        print(json.dumps(res["item"], ensure_ascii=False, indent=2) if args.json else f"[已打回] {res['item']['item_key']} -> rejected")
        return 0

    if args.cmd == "heartbeat":
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/heartbeat", {
            "agent_branch": args.branch,
            "agent_host": args.host,
            "note": args.note,
        })
        print(json.dumps(res["item"], ensure_ascii=False, indent=2) if args.json else f"[心跳已更新] {res['item']['item_key']} @ {res['item']['agent_heartbeat_at']}")
        return 0

    if args.cmd == "comment":
        body_text = read_text_arg(args.body, args.file, label="评论正文").strip()
        if not body_text:
            raise SystemExit("[错误] 评论正文不能为空")
        res = client.request("POST", f"/api/v1/items/{quote(args.key)}/comments", {"body_md": body_text})
        print(json.dumps(res["comment"], ensure_ascii=False, indent=2) if args.json else f"[评论已发送] id={res['comment']['id']}")
        return 0

    if args.cmd == "attach":
        res = upload_image(client, args.key, Path(args.file))
        print(json.dumps(res["attachment"], ensure_ascii=False, indent=2) if args.json else f"[附件已上传] {res['attachment']['url']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
