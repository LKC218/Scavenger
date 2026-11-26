#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按北京时间（UTC+8）采集 Scavenger 项目每日工作客观事实快照（只读）。

聚合四类本地事实：
1. Antigravity 与 Codex 主会话在目标北京时间窗口内的用户需求与关键回复摘要；
2. 正式仓库目录（src/scripts/docs/reports 等）当日文件修改与 Git 状态；
3. local-data/ 交付目录当日产物聚合（过滤 Unity Library 与零碎中间缓存）；
4. local-data/维护清理/登记.json 当日新建/关闭任务及待验收清单。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parent.parent

REPO_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "tmp", "local-data"}
LOCAL_DATA_NOISE_DIRS = {"Library", "Logs", "Temp", "obj", "__pycache__", "维护清理"}
KEY_DELIVERY_EXTS = {".blend", ".png", ".jpg", ".webp", ".md", ".json", ".html", ".ogg", ".wav"}

_RE_USER_REQ = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
_RE_TAG_TAIL = re.compile(r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*$", re.DOTALL)
_RE_ENV_CTX = re.compile(r"<environment_context>.*?</environment_context>", re.DOTALL)


def parse_bj_date(date_arg: str) -> datetime.date:
    """将日期参数解析为北京时间 date 对象。"""
    now_bj = datetime.datetime.now(TZ_BJ).date()
    text = date_arg.strip().lower()
    if text == "yesterday":
        return now_bj - datetime.timedelta(days=1)
    if text == "today":
        return now_bj
    return datetime.date.fromisoformat(text)


def bj_window(target_date: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """返回目标北京时间全天 [00:00:00, 23:59:59.999999] 起止时间。"""
    start = datetime.datetime.combine(target_date, datetime.time.min, tzinfo=TZ_BJ)
    end = datetime.datetime.combine(target_date, datetime.time.max, tzinfo=TZ_BJ)
    return start, end


def sanitize_text(text: str, project_root: Path) -> str:
    """脱敏本机用户名与外部绝对路径，项目内路径转为相对路径。"""
    if not text:
        return ""
    out = text
    root_fwd = project_root.as_posix()
    root_back = str(project_root)
    from urllib.parse import quote

    encoded_fwd = quote(root_fwd, safe="/:")
    for variant in (
        root_fwd + "/",
        root_back + "\\",
        root_fwd.lower() + "/",
        root_back.lower() + "\\",
        root_fwd.replace("E:/", "e:/") + "/",
        root_back.replace("E:\\", "e:\\") + "\\",
        "file:///" + root_fwd + "/",
        "file:///" + root_fwd.replace("E:/", "e:/") + "/",
        "file:///" + encoded_fwd + "/",
        "file:///" + encoded_fwd.replace("E:/", "e:/") + "/",
    ):
        out = out.replace(variant, "")
    out = out.replace(root_fwd, ".").replace(root_back, ".")

    home = Path.home()
    out = out.replace(str(home), "~").replace(home.as_posix(), "~")
    out = re.sub(r"[A-Za-z]:[/\\]Users[/\\][^/\\\s]+", "~", out)
    out = re.sub(r"[A-Za-z]:[/\\]SteamLibrary[/\\][^\s\"')`>]+", "<Steam游戏目录>", out)
    return " ".join(out.split())


def clean_user_prompt(raw: str) -> str:
    """剥离系统附加元数据标签，提取纯用户指令文本。"""
    m = _RE_USER_REQ.search(raw)
    if m:
        text = m.group(1)
    else:
        text = _RE_TAG_TAIL.sub("", raw)
    text = _RE_ENV_CTX.sub("", text)
    return text.strip()


def _parse_iso_to_bj(iso_str: str | None) -> datetime.datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(TZ_BJ)
    except ValueError:
        return None


def _is_scavenger_related(texts: list[str], title: str) -> bool:
    """粗筛会话是否属于 Scavenger 项目工作。"""
    joined = (title + "\n" + "\n".join(texts)).lower()
    keywords = ("scavenger", "127.0.0.1:8800", "/play", "/armory", "断箭", "丧尸", "武器库", "固定翼", "scripts/", "src/")
    return any(k in joined for k in keywords)


def collect_antigravity_sessions(
    start_bj: datetime.datetime,
    end_bj: datetime.datetime,
    project_root: Path,
) -> list[dict[str, Any]]:
    """采集北京时间窗口内有用户交互的 Antigravity 主会话摘要。"""
    base = Path.home() / ".gemini" / "antigravity"
    brain_dir = base / "brain"
    ann_dir = base / "annotations"
    if not brain_dir.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for entry in brain_dir.iterdir():
        if not entry.is_dir():
            continue
        tr = entry / ".system_generated" / "logs" / "transcript.jsonl"
        if not tr.exists():
            continue

        title = ""
        ann_file = ann_dir / f"{entry.name}.pbtxt"
        if ann_file.exists():
            ann_raw = ann_file.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'title:"([^"]*)"', ann_raw)
            if m:
                title = m.group(1)

        first_dt: datetime.datetime | None = None
        last_dt: datetime.datetime | None = None
        user_prompts: list[tuple[str, str]] = []
        final_replies: list[tuple[str, str]] = []
        raw_texts_for_match: list[str] = []

        for line in tr.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = _parse_iso_to_bj(obj.get("created_at"))
            if not dt or not (start_bj <= dt <= end_bj):
                continue

            if first_dt is None or dt < first_dt:
                first_dt = dt
            if last_dt is None or dt > last_dt:
                last_dt = dt

            stype = obj.get("type")
            content = (obj.get("content") or "").strip()
            if content:
                raw_texts_for_match.append(content[:400])

            if stype == "USER_INPUT":
                cleaned = clean_user_prompt(content)
                if cleaned:
                    user_prompts.append((dt.strftime("%H:%M:%S"), sanitize_text(cleaned, project_root)[:220]))
            elif stype == "PLANNER_RESPONSE" and content and not obj.get("tool_calls"):
                final_replies.append((dt.strftime("%H:%M:%S"), sanitize_text(content, project_root)[:360]))

        # 仅保留有真实 USER_INPUT 的主会话，自动过滤子代理/swarm 节点
        if not user_prompts or first_dt is None or last_dt is None:
            continue
        if not _is_scavenger_related(raw_texts_for_match, title):
            continue

        sessions.append(
            {
                "tool": "Antigravity",
                "id": entry.name[:8],
                "title": title or "(未命名会话)",
                "start": first_dt.strftime("%H:%M:%S"),
                "end": last_dt.strftime("%H:%M:%S"),
                "user_prompts": user_prompts,
                "key_replies": final_replies[-3:],
            }
        )

    sessions.sort(key=lambda item: item["start"])
    return sessions


def collect_codex_sessions(
    start_bj: datetime.datetime,
    end_bj: datetime.datetime,
    project_root: Path,
) -> list[dict[str, Any]]:
    """采集北京时间窗口内与项目相关的 Codex 会话摘要。"""
    codex_dir = Path.home() / ".codex" / "sessions"
    if not codex_dir.exists():
        return []

    sessions: list[dict[str, Any]] = []
    # 只检查修改时间不早于统计窗口起点的日志，避免全盘历史扫描耗时
    min_mtime = start_bj.timestamp()
    for p in sorted(codex_dir.rglob("*.jsonl")):
        try:
            if p.stat().st_mtime < min_mtime:
                continue
        except OSError:
            continue

        first_dt: datetime.datetime | None = None
        last_dt: datetime.datetime | None = None
        user_prompts: list[tuple[str, str]] = []
        final_replies: list[tuple[str, str]] = []
        raw_texts: list[str] = []

        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = _parse_iso_to_bj(obj.get("timestamp"))
            if not dt or not (start_bj <= dt <= end_bj):
                continue

            if obj.get("type") != "response_item":
                continue
            pl = obj.get("payload") or {}
            role = pl.get("role")
            if role not in ("user", "assistant"):
                continue

            parts = pl.get("content") or []
            txt = " ".join(
                x.get("text", "")
                for x in parts
                if isinstance(x, dict) and x.get("type") in ("input_text", "output_text")
            ).strip()
            if not txt or txt.startswith("# AGENTS.md"):
                continue

            raw_texts.append(txt[:400])
            if first_dt is None or dt < first_dt:
                first_dt = dt
            if last_dt is None or dt > last_dt:
                last_dt = dt

            if role == "user":
                cleaned = clean_user_prompt(txt)
                if cleaned:
                    user_prompts.append((dt.strftime("%H:%M:%S"), sanitize_text(cleaned, project_root)[:220]))
            elif role == "assistant":
                final_replies.append((dt.strftime("%H:%M:%S"), sanitize_text(txt, project_root)[:360]))

        if not user_prompts or first_dt is None or last_dt is None:
            continue
        if all(p.lstrip().startswith("<heartbeat>") for _, p in user_prompts):
            continue
        if not _is_scavenger_related(raw_texts, p.name):
            continue

        # 从文件名抽取短会话 ID（如 rollout-...-01a2023f-...）
        m = re.search(r"-([0-9a-f]{8})-[0-9a-f]{4}-", p.name)
        short_id = m.group(1) if m else p.stem[:8]
        sessions.append(
            {
                "tool": "Codex",
                "id": short_id,
                "title": f"Codex 会话 ({short_id})",
                "start": first_dt.strftime("%H:%M:%S"),
                "end": last_dt.strftime("%H:%M:%S"),
                "user_prompts": user_prompts,
                "key_replies": final_replies[-3:],
            }
        )

    sessions.sort(key=lambda item: item["start"])
    return sessions


def collect_repo_changes(
    start_bj: datetime.datetime,
    end_bj: datetime.datetime,
    project_root: Path,
    include_local_data: bool = True,
) -> dict[str, Any]:
    """统计正式仓库目录与关键本地交付目录在窗口内的文件变动。"""
    start_ts = start_bj.timestamp()
    end_ts = end_bj.timestamp()

    grouped_repo: dict[str, list[dict[str, str]]] = defaultdict(list)
    total_repo = 0

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in REPO_IGNORE_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            try:
                st = fpath.stat()
            except OSError:
                continue
            if start_ts <= st.st_mtime <= end_ts:
                rel = fpath.relative_to(project_root).as_posix()
                top = rel.split("/")[0] if "/" in rel else "(根目录)"
                mt = datetime.datetime.fromtimestamp(st.st_mtime, tz=TZ_BJ).strftime("%H:%M:%S")
                grouped_repo[top].append({"time": mt, "path": rel})
                total_repo += 1

    for items in grouped_repo.values():
        items.sort(key=lambda x: x["time"])

    local_summary: list[dict[str, Any]] = []
    local_root = project_root / "local-data"
    if include_local_data and local_root.exists():
        bucket_counts: dict[str, int] = defaultdict(int)
        bucket_samples: dict[str, list[str]] = defaultdict(list)
        for dirpath, dirnames, filenames in os.walk(local_root):
            dirnames[:] = [d for d in dirnames if d not in LOCAL_DATA_NOISE_DIRS]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                if start_ts <= st.st_mtime <= end_ts:
                    rel = fpath.relative_to(local_root).as_posix()
                    parts = rel.split("/")
                    bucket = "/".join(parts[:3]) if len(parts) >= 3 else "/".join(parts[:2])
                    bucket_counts[bucket] += 1
                    if fpath.suffix.lower() in KEY_DELIVERY_EXTS and len(bucket_samples[bucket]) < 8:
                        bucket_samples[bucket].append(f"local-data/{rel}")

        for bucket, count in sorted(bucket_counts.items(), key=lambda kv: -kv[1]):
            local_summary.append(
                {
                    "bucket": f"local-data/{bucket}",
                    "count": count,
                    "samples": bucket_samples[bucket],
                }
            )

    git_commits: list[str] = []
    git_dirty_count = 0
    try:
        status_out = subprocess.check_output(
            ["git", "status", "--short"], cwd=project_root, text=True, encoding="utf-8", errors="ignore"
        )
        git_dirty_count = len([ln for ln in status_out.splitlines() if ln.strip()])
        log_out = subprocess.check_output(
            [
                "git",
                "log",
                f"--since={start_bj.isoformat()}",
                f"--until={end_bj.isoformat()}",
                "--pretty=format:%h | %ai | %s",
            ],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        git_commits = [ln.strip() for ln in log_out.splitlines() if ln.strip()]
    except (subprocess.SubprocessError, OSError):
        pass

    return {
        "repo_touched_count": total_repo,
        "repo_groups": dict(sorted(grouped_repo.items())),
        "local_data_summary": local_summary,
        "git_commits": git_commits,
        "git_dirty_count": git_dirty_count,
    }


def collect_cleanup_registry(
    start_bj: datetime.datetime,
    end_bj: datetime.datetime,
    project_root: Path,
) -> dict[str, Any]:
    """读取清理登记与待确认验收清单。"""
    start_ts = start_bj.timestamp()
    end_ts = end_bj.timestamp()

    reg_path = project_root / "local-data" / "维护清理" / "登记.json"
    touched_tasks: list[dict[str, str]] = []
    if reg_path.exists():
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            for name, info in (reg.get("tasks") or {}).items():
                ca = info.get("created_at") or info.get("created") or 0
                cl = info.get("closed_at") or info.get("closed") or 0
                if (start_ts <= ca <= end_ts) or (start_ts <= cl <= end_ts):
                    ca_s = datetime.datetime.fromtimestamp(ca, tz=TZ_BJ).strftime("%H:%M:%S") if ca else "-"
                    cl_s = datetime.datetime.fromtimestamp(cl, tz=TZ_BJ).strftime("%H:%M:%S") if cl else "-"
                    touched_tasks.append(
                        {
                            "task": name,
                            "status": str(info.get("status") or "-"),
                            "created": ca_s,
                            "closed": cl_s,
                        }
                    )
        except (OSError, json.JSONDecodeError):
            pass

    waiting_dir = project_root / "local-data" / "维护清理" / "验收待确认"
    waiting_items: list[dict[str, str]] = []
    if waiting_dir.exists():
        for p in sorted(waiting_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                waiting_items.append(
                    {
                        "manifest": p.relative_to(project_root).as_posix(),
                        "asset": str(data.get("asset") or "-"),
                        "version": str(data.get("version") or "-"),
                        "task": str(data.get("task") or "-"),
                        "status": str(data.get("status") or "-"),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue

    return {
        "touched_tasks": touched_tasks,
        "waiting_acceptance": waiting_items,
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    """将采集结果格式化为结构化 Markdown 草稿。"""
    lines: list[str] = [
        f"# 每日工作客观事实快照（北京时间 {snapshot['date']}）",
        "",
        f"- 统计窗口：`{snapshot['window_start']}` ~ `{snapshot['window_end']}`",
        f"- 活跃主会话：{len(snapshot['sessions'])} 个 | 正式仓库变动文件：{snapshot['changes']['repo_touched_count']} 个 | 当前未提交 Git 变更：{snapshot['changes']['git_dirty_count']} 项",
        "",
        "## 一、活跃主会话轨迹",
        "",
    ]

    if not snapshot["sessions"]:
        lines.append("（该北京时间窗口内无关联项目的主会话记录）")
    else:
        for s in snapshot["sessions"]:
            lines.append(f"### [{s['tool']}] {s['title']}（`{s['id']}` · {s['start']} ~ {s['end']}）")
            lines.append("- **用户需求节点**：")
            for ts, prompt in s["user_prompts"]:
                lines.append(f"  - `{ts}` {prompt}")
            if s["key_replies"]:
                lines.append("- **尾部回复摘要**：")
                for ts, reply in s["key_replies"]:
                    lines.append(f"  - `{ts}` {reply}")
            lines.append("")

    lines.extend(["## 二、正式仓库文件改动（按模块目录）", ""])
    groups = snapshot["changes"]["repo_groups"]
    if not groups:
        lines.append("（无正式目录文件改动）")
    else:
        for top, items in groups.items():
            lines.append(f"### `{top}`（{len(items)} 个）")
            for item in items:
                lines.append(f"- `{item['time']}` [{item['path']}]({item['path']})")
            lines.append("")

    local_summary = snapshot["changes"]["local_data_summary"]
    if local_summary:
        lines.extend(["## 三、`local-data/` 关键交付目录聚合", ""])
        for entry in local_summary:
            lines.append(f"- `{entry['bucket']}`：共 {entry['count']} 个文件")
            for sample in entry["samples"]:
                lines.append(f"  - 样本：`{sample}`")
        lines.append("")

    lines.extend(["## 四、维护清理登记与待验收清单", ""])
    tasks = snapshot["cleanup"]["touched_tasks"]
    if tasks:
        lines.append("### 当日新建/关闭任务")
        for t in tasks:
            lines.append(f"- `{t['task']}` | 状态：`{t['status']}` | 创建 `{t['created']}` / 关闭 `{t['closed']}`")
        lines.append("")

    waiting = snapshot["cleanup"]["waiting_acceptance"]
    if waiting:
        lines.append("### 当前待工程师确认清单")
        for w in waiting:
            lines.append(f"- `{w['asset']}`（{w['version']}）| 任务：`{w['task']}` | 清单：`{w['manifest']}`")
        lines.append("")

    commits = snapshot["changes"]["git_commits"]
    lines.extend(["## 五、Git 提交记录", ""])
    if commits:
        for c in commits:
            lines.append(f"- `{c}`")
    else:
        lines.append(f"- 该时间窗口内无新提交；当前工作区共有 `{snapshot['changes']['git_dirty_count']}` 项未提交变更。")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按北京时间采集 Scavenger 每日工作事实快照")
    parser.add_argument("--date", default="yesterday", help="目标北京时间日期 YYYY-MM-DD，或 today / yesterday（默认 yesterday）")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="输出格式")
    parser.add_argument("--out", default="", help="可选：写入项目相对路径（如 tmp/每日工作快照/2026-11-22.md）")
    parser.add_argument("--no-local-data", action="store_true", help="跳过扫描 local-data/ 交付统计")
    args = parser.parse_args(argv)

    target_date = parse_bj_date(args.date)
    start_bj, end_bj = bj_window(target_date)

    sessions = collect_antigravity_sessions(start_bj, end_bj, PROJECT_ROOT)
    sessions.extend(collect_codex_sessions(start_bj, end_bj, PROJECT_ROOT))
    sessions.sort(key=lambda item: item["start"])

    snapshot: dict[str, Any] = {
        "date": target_date.isoformat(),
        "timezone": "Asia/Shanghai (UTC+8)",
        "window_start": start_bj.strftime("%Y-%m-%d %H:%M:%S %z"),
        "window_end": end_bj.strftime("%Y-%m-%d %H:%M:%S %z"),
        "sessions": sessions,
        "changes": collect_repo_changes(start_bj, end_bj, PROJECT_ROOT, include_local_data=not args.no_local_data),
        "cleanup": collect_cleanup_registry(start_bj, end_bj, PROJECT_ROOT),
    }

    output = (
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        if args.format == "json"
        else render_markdown(snapshot)
    )

    if args.out:
        out_path = (PROJECT_ROOT / args.out).resolve()
        try:
            out_path.relative_to(PROJECT_ROOT)
        except ValueError:
            print("错误：--out 路径必须位于项目目录内", file=sys.stderr)
            return 2
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"已写入工作快照：{out_path.relative_to(PROJECT_ROOT).as_posix()}")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
