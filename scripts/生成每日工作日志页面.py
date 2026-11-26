#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Scavenger 每日工作成果离线看板与简报（reports/开发日志/每日开发看板.html）。

V4 视觉与交互体系 + 统一命名规范：
- 唯一页面入口：reports/开发日志/每日开发看板.html；
- 唯一数据目录：reports/开发日志/数据/YYYY-MM/YYYY-MM-DD-开发日志.json（按北京时间按月归档）；
- 卡片采用跨天唯一编号 MMDD-01（如 1122-01），默认三列 Kanban 泳道，支持拖拽流转、右侧滑出详情抽屉与双视图/双主题切换。
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "reports" / "开发日志"
DATA_DIR = REPORT_DIR / "数据"
OUTPUT_HTML = REPORT_DIR / "每日开发看板.html"
SNAPSHOT_SCRIPT = PROJECT_ROOT / "scripts" / "生成每日工作快照.py"
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-开发日志\.json$")


def _load_snapshot_module():
    spec = importlib.util.spec_from_file_location("daily_snapshot_collector", SNAPSHOT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载快照脚本：{SNAPSHOT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_auto_draft_payload(target_date: datetime.date) -> dict[str, Any]:
    """将客观事实快照转换为兼容看板结构的 auto-draft JSON。"""
    mod = _load_snapshot_module()
    start_bj, end_bj = mod.bj_window(target_date)
    sessions = mod.collect_antigravity_sessions(start_bj, end_bj, PROJECT_ROOT)
    sessions.extend(mod.collect_codex_sessions(start_bj, end_bj, PROJECT_ROOT))
    sessions.sort(key=lambda item: item["start"])
    changes = mod.collect_repo_changes(start_bj, end_bj, PROJECT_ROOT, include_local_data=True)
    cleanup = mod.collect_cleanup_registry(start_bj, end_bj, PROJECT_ROOT)

    goals: list[dict[str, Any]] = []
    for idx, s in enumerate(sessions, start=1):
        prompts = [p for _, p in s.get("user_prompts", [])]
        replies = [r for _, r in s.get("key_replies", [])]
        summary_text = replies[-1] if replies else (prompts[0] if prompts else "详见会话记录")
        if len(summary_text) > 160:
            summary_text = summary_text[:157] + "..."
        highlights = [p[:64] + ("..." if len(p) > 64 else "") for p in prompts[:3]]
        goals.append(
            {
                "id": f"auto-session-{idx}-{s['id']}",
                "title": s["title"],
                "category": f"{s['tool']} 会话",
                "status": "in_progress",
                "status_label": "自动采集草稿",
                "summary": summary_text,
                "highlights": highlights,
                "metrics": [
                    {"label": "活跃时段", "before": s["start"][:5], "after": s["end"][:5]},
                    {"label": "交互轮次", "before": "-", "after": f"{len(prompts)} 次输入"},
                ],
                "sessions": [
                    {
                        "tool": s["tool"],
                        "id": s["id"],
                        "start": s["start"][:5],
                        "end": s["end"][:5],
                        "label": s["title"],
                    }
                ],
                "files": [],
                "verification": "由快照脚本自动汇总，可在对话中润色为目标卡片。",
            }
        )

    if not goals and changes["repo_touched_count"] > 0:
        all_files = [it["path"] for grp in changes["repo_groups"].values() for it in grp]
        goals.append(
            {
                "id": "auto-files-only",
                "title": "项目文件离线变更（无主会话交互记录）",
                "category": "仓库变更",
                "status": "done",
                "status_label": "文件变更记录",
                "summary": f"北京时间 {target_date.isoformat()} 共检测到 {changes['repo_touched_count']} 个正式文件修改，未检测到关联对话记录。",
                "highlights": [f"涉及目录：{', '.join(changes['repo_groups'].keys())}"],
                "metrics": [{"label": "正式改动文件", "before": "-", "after": f"{changes['repo_touched_count']} 个"}],
                "sessions": [],
                "files": all_files[:30],
                "verification": "仅统计文件修改时间戳。",
            }
        )

    actions: list[dict[str, Any]] = []
    for i, w in enumerate(cleanup.get("waiting_acceptance", []), start=1):
        actions.append(
            {
                "id": f"auto-wait-{i}",
                "type": "accept",
                "title": f"待确认验收：{w['asset']} ({w['version']})",
                "detail": f"关联任务：{w['task']}",
                "ref": w["manifest"],
            }
        )

    repo_files_flat = [it["path"] for grp in changes["repo_groups"].values() for it in grp]
    if goals and repo_files_flat:
        goals[0]["files"] = repo_files_flat[:40]

    closed_cnt = sum(1 for t in cleanup.get("touched_tasks", []) if t["status"] == "closed")
    active_cnt = sum(1 for t in cleanup.get("touched_tasks", []) if t["status"] == "active")

    now_bj = datetime.datetime.now(mod.TZ_BJ).strftime("%Y-%m-%d %H:%M:%S %z")
    return {
        "date": target_date.isoformat(),
        "timezone": "Asia/Shanghai (UTC+8)",
        "mode": "auto-draft",
        "generated_at": now_bj,
        "headline": (
            f"自动采集草稿：共记录 {len(sessions)} 个活跃主会话、{changes['repo_touched_count']} 个正式文件改动，"
            f"当前待确认验收 {len(actions)} 项。"
        ),
        "kpis": {
            "goals_achieved": sum(1 for g in goals if g["status"] == "done"),
            "goals_total": len(goals),
            "pending_actions": len(actions),
            "repo_files": changes["repo_touched_count"],
            "git_dirty": changes["git_dirty_count"],
        },
        "goals": goals,
        "actions": actions,
        "cleanup_summary": {
            "closed_count": closed_cnt,
            "active_count": active_cnt,
            "waiting_acceptance": cleanup.get("waiting_acceptance", []),
        },
        "repo_snapshot": {
            "commits": len(changes.get("git_commits", [])),
            "dirty_count": changes.get("git_dirty_count", 0),
        },
        "auto_repo_groups": changes.get("repo_groups", {}),
        "auto_local_data": changes.get("local_data_summary", []),
    }


def ensure_auto_draft(date_arg: str) -> Path:
    """为指定日期生成兜底草稿 JSON（按 数据/YYYY-MM/YYYY-MM-DD-开发日志.json）；若已存在 curated 精炼版则跳过不覆盖。"""
    mod = _load_snapshot_module()
    target_date = mod.parse_bj_date(date_arg)
    month_dir = DATA_DIR / target_date.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    target_file = month_dir / f"{target_date.isoformat()}-开发日志.json"

    if target_file.exists():
        try:
            existing = json.loads(target_file.read_text(encoding="utf-8"))
            if existing.get("mode") == "curated":
                return target_file
        except (OSError, json.JSONDecodeError):
            pass

    payload = build_auto_draft_payload(target_date)
    target_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target_file


def load_all_days() -> list[dict[str, Any]]:
    """递归读取 reports/开发日志/数据/YYYY-MM/YYYY-MM-DD-开发日志.json 并按日期倒序排列。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    days: list[dict[str, Any]] = []
    for month_dir in sorted(DATA_DIR.iterdir(), reverse=True):
        if not month_dir.is_dir() or not MONTH_DIR_RE.fullmatch(month_dir.name):
            continue
        for p in sorted(month_dir.glob("*-开发日志.json"), reverse=True):
            m = DAY_FILE_RE.fullmatch(p.name)
            if not m:
                continue
            expected_date = m.group(1)
            if not expected_date.startswith(month_dir.name + "-"):
                raise ValueError(f"日期文件归属月份不匹配：{p.relative_to(PROJECT_ROOT).as_posix()}")
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("date") != expected_date:
                raise ValueError(f"日志 JSON 内 date 字段与文件名不一致：{p.relative_to(PROJECT_ROOT).as_posix()}")
            days.append(data)
    days.sort(key=lambda d: d.get("date", ""), reverse=True)
    return days


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>SCAVENGER // 每日开发看板</title>
<style>
  :root {
    color-scheme: dark;
    /* Scavenger 深炭双金看板主题（默认） */
    --bg-canvas: #0b0c11;
    --bg-mast: rgba(11, 12, 17, 0.92);
    --bg-lane: #101219;
    --bg-lane-drag: rgba(240, 211, 114, 0.06);
    --bg-card: #161822;
    --bg-card-hover: #1b1e2a;
    --bg-drawer: #11131b;
    --bg-soft: rgba(255, 255, 255, 0.036);

    --line-hair: rgba(255, 255, 255, 0.075);
    --line-base: rgba(255, 255, 255, 0.14);
    --line-gold: rgba(240, 211, 114, 0.48);
    --rim-top: inset 0 1px 0 rgba(255, 255, 255, 0.06);
    --card-shadow: 0 2px 10px rgba(0, 0, 0, 0.26);
    --card-shadow-hover: 0 8px 24px rgba(0, 0, 0, 0.42);

    --text-primary: #f3f5f8;
    --text-secondary: #b5bac6;
    --text-muted: #7e8496;
    --text-dim: #54596b;

    --gold: #f0d372;
    --gold-deep: #d9aa2b;
    --gold-bg: rgba(240, 211, 114, 0.13);

    --urgent: #ff7a88;
    --urgent-bg: rgba(255, 122, 136, 0.14);

    --blue: #6fb5ff;
    --blue-bg: rgba(111, 181, 255, 0.14);

    --green: #3cd69f;
    --green-bg: rgba(60, 214, 159, 0.14);

    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    --font-display: "Bahnschrift Condensed", "DIN Condensed", "HarmonyOS Sans SC", "Microsoft YaHei UI", sans-serif;
    --font-sans: "Bahnschrift", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    --font-mono: "Cascadia Mono", "Consolas", "JetBrains Mono", monospace;
  }

  [data-theme="light"] {
    color-scheme: light;
    /* 参考图同款清爽浅灰白看板模式 */
    --bg-canvas: #f3f5f8;
    --bg-mast: rgba(255, 255, 255, 0.92);
    --bg-lane: #ebeff5;
    --bg-lane-drag: rgba(37, 99, 235, 0.07);
    --bg-card: #ffffff;
    --bg-card-hover: #ffffff;
    --bg-drawer: #ffffff;
    --bg-soft: #f4f6fa;

    --line-hair: rgba(15, 23, 42, 0.08);
    --line-base: rgba(15, 23, 42, 0.14);
    --line-gold: rgba(194, 120, 3, 0.45);
    --rim-top: inset 0 1px 0 rgba(255, 255, 255, 0.8);
    --card-shadow: 0 1px 3px rgba(15, 23, 42, 0.06), 0 1px 2px rgba(15, 23, 42, 0.04);
    --card-shadow-hover: 0 8px 20px rgba(15, 23, 42, 0.09);

    --text-primary: #141b26;
    --text-secondary: #465368;
    --text-muted: #6c7a90;
    --text-dim: #95a2b6;

    --gold: #b86f09;
    --gold-deep: #d97706;
    --gold-bg: rgba(245, 158, 11, 0.14);

    --urgent: #dc2626;
    --urgent-bg: rgba(239, 68, 68, 0.12);

    --blue: #2563eb;
    --blue-bg: rgba(59, 130, 246, 0.12);

    --green: #0f8a58;
    --green-bg: rgba(16, 185, 129, 0.13);
  }

  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    background-color: var(--bg-canvas);
    color: var(--text-primary);
    font-family: var(--font-sans);
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }

  /* 顶部指挥眉栏 */
  .mast {
    position: sticky;
    top: 0;
    z-index: 30;
    background: var(--bg-mast);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line-hair);
  }
  .mast-inner {
    max-width: 1440px;
    margin: 0 auto;
    padding: 0 24px;
    height: 54px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .brand-mark {
    width: 8px;
    height: 20px;
    background: var(--gold);
    border-radius: 1px;
    flex-shrink: 0;
  }
  .brand-code {
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 1.8px;
    color: var(--text-primary);
  }
  .brand-sub {
    font-size: 12.5px;
    color: var(--text-muted);
    white-space: nowrap;
  }

  .date-nav {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .icon-btn {
    border: 1px solid var(--line-hair);
    background: transparent;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 12px;
    height: 28px;
    padding: 0 9px;
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    transition: all 0.14s var(--ease-out);
    white-space: nowrap;
  }
  .icon-btn:hover:not(:disabled) {
    color: var(--text-primary);
    border-color: var(--line-base);
    background: var(--bg-soft);
  }
  .icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }
  .icon-btn.accent {
    color: var(--gold);
    border-color: var(--line-gold);
    background: var(--gold-bg);
    font-weight: 600;
  }

  .date-tabs {
    display: flex;
    align-items: center;
    gap: 4px;
    overflow-x: auto;
  }
  .date-tab {
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 12px;
    height: 28px;
    padding: 0 10px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.14s ease;
    white-space: nowrap;
  }
  .date-tab:hover { color: var(--text-primary); background: var(--bg-soft); }
  .date-tab.active {
    color: var(--gold);
    background: var(--gold-bg);
    border-color: var(--line-gold);
    font-weight: 600;
  }

  .mast-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .seg-switch {
    display: inline-flex;
    background: var(--bg-lane);
    border: 1px solid var(--line-hair);
    border-radius: 7px;
    padding: 2px;
    gap: 2px;
  }
  .seg-btn {
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 5px;
    cursor: pointer;
    transition: all 0.14s ease;
  }
  .seg-btn.active {
    background: var(--bg-card);
    color: var(--text-primary);
    box-shadow: var(--card-shadow);
  }
  .search-box {
    background: var(--bg-lane);
    border: 1px solid var(--line-hair);
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 12px;
    height: 28px;
    padding: 0 10px;
    border-radius: 6px;
    width: 172px;
    outline: none;
    transition: border-color 0.14s ease, width 0.2s var(--ease-out);
  }
  .search-box:focus { border-color: var(--line-gold); width: 210px; }
  .search-box::placeholder { color: var(--text-dim); }

  /* 页面主容器（100% 宽度留给看板） */
  .page {
    max-width: 1440px;
    margin: 0 auto;
    padding: 18px 24px 56px;
  }

  /* 顶部极薄态势条（高度克制，一屏尽览全天大局） */
  .status-strip {
    background: var(--bg-lane);
    border: 1px solid var(--line-hair);
    border-radius: 10px;
    padding: 13px 18px;
    margin-bottom: 18px;
    display: grid;
    grid-template-columns: minmax(0, 1.55fr) minmax(260px, 0.9fr) auto;
    gap: 18px;
    align-items: center;
  }
  .strip-left { min-width: 0; }
  .strip-kicker {
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 4px;
    flex-wrap: wrap;
  }
  .strip-headline {
    margin: 0;
    font-size: 14px;
    font-weight: 500;
    color: var(--text-primary);
    line-height: 1.55;
  }
  .strip-mix {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .mix-top {
    display: flex;
    justify-content: space-between;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
  }
  .mix-bar {
    display: flex;
    height: 8px;
    border-radius: 99px;
    overflow: hidden;
    gap: 2px;
    background: var(--bg-canvas);
  }
  .mix-seg { height: 100%; min-width: 5px; }
  .mix-mini-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 11px;
    color: var(--text-muted);
  }
  .mix-mini-legend span {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .mix-mini-legend i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    display: inline-block;
  }

  .strip-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .kpi-pill {
    background: var(--bg-card);
    border: 1px solid var(--line-hair);
    border-radius: 8px;
    padding: 6px 12px;
    display: flex;
    flex-direction: column;
    min-width: 78px;
  }
  .kpi-pill span { font-size: 10.5px; color: var(--text-muted); }
  .kpi-pill strong {
    font-family: var(--font-display);
    font-size: 20px;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }
  .kpi-pill.warn strong { color: var(--gold); }
  .kpi-pill.ok strong { color: var(--green); }

  /* 核心：三列 Kanban 看板泳道 */
  .board {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    align-items: start;
  }
  .lane {
    background: var(--bg-lane);
    border: 1px solid var(--line-hair);
    border-radius: 12px;
    padding: 14px;
    min-height: 460px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: background-color 0.16s ease, border-color 0.16s ease;
  }
  .lane.drag-over {
    background: var(--bg-lane-drag);
    border-color: var(--line-gold);
    border-style: dashed;
  }
  .lane-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 4px 6px;
  }
  .lane-title {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .lane-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .lane-pending .lane-dot { background: var(--gold); box-shadow: 0 0 8px rgba(240, 211, 114, 0.45); }
  .lane-progress .lane-dot { background: var(--blue); box-shadow: 0 0 8px rgba(111, 181, 255, 0.35); }
  .lane-done .lane-dot { background: var(--green); box-shadow: 0 0 8px rgba(60, 214, 159, 0.35); }
  .lane-count {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--text-muted);
    background: var(--bg-card);
    border: 1px solid var(--line-hair);
    padding: 1px 8px;
    border-radius: 99px;
    font-variant-numeric: tabular-nums;
  }
  .lane-cards {
    display: flex;
    flex-direction: column;
    gap: 11px;
    flex: 1;
  }
  .lane-empty {
    border: 1px dashed var(--line-base);
    border-radius: 10px;
    padding: 28px 14px;
    text-align: center;
    font-size: 12.5px;
    color: var(--text-dim);
  }

  /* 参考图同款极简 3 层 Kanban 卡片（零长段落轰炸） */
  .k-card {
    background: var(--bg-card);
    border: 1px solid var(--line-hair);
    box-shadow: var(--rim-top), var(--card-shadow);
    border-radius: 10px;
    padding: 14px 15px 13px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 11px;
    transition: transform 0.16s var(--ease-out), border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease;
    position: relative;
  }
  .k-card:hover {
    transform: translateY(-2px);
    background: var(--bg-card-hover);
    border-color: var(--line-gold);
    box-shadow: var(--card-shadow-hover);
  }
  .k-card.dragging {
    opacity: 0.45;
    border-style: dashed;
    border-color: var(--gold);
  }
  .k-card.selected {
    border-color: var(--gold);
    box-shadow: 0 0 0 1px var(--gold), var(--card-shadow-hover);
  }

  /* 卡片第 1 行：编号 + 类别 + 右侧轻量语义微胶囊 */
  .k-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .k-id-wrap {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
  }
  .k-code {
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.3px;
  }
  .k-cat {
    font-size: 11.5px;
    color: var(--text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .k-badge {
    font-size: 11px;
    font-weight: 700;
    padding: 2px 9px;
    border-radius: 99px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .b-accept { background: var(--gold-bg); color: var(--gold); }
  .b-decision { background: var(--urgent-bg); color: var(--urgent); }
  .b-progress { background: var(--blue-bg); color: var(--blue); }
  .b-done { background: var(--green-bg); color: var(--green); }

  /* 卡片第 2 行：清爽标题 + 单行核心量化结果微条 */
  .k-title {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.42;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .k-metric-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 10px;
    border-radius: 6px;
    background: var(--bg-soft);
    border: 1px solid var(--line-hair);
    font-size: 12px;
  }
  .k-metric-lbl {
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .k-metric-val {
    font-family: var(--font-mono);
    font-weight: 700;
    color: var(--gold);
    white-space: nowrap;
  }
  .lane-done .k-metric-val { color: var(--green); }

  /* 卡片第 3 行：极简底栏（左来源与时段 · 右改动规模） */
  .k-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--line-hair);
    font-size: 11.5px;
    color: var(--text-muted);
  }
  .k-owner {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .k-avatar {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--bg-soft);
    border: 1px solid var(--line-base);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    color: var(--gold);
    flex-shrink: 0;
  }
  .k-meta-right {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-dim);
    white-space: nowrap;
  }

  /* 简报列表视图（保留 V3 细线精炼阅读体验） */
  .list-view {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .list-group {
    background: var(--bg-lane);
    border: 1px solid var(--line-hair);
    border-radius: 10px;
    overflow: hidden;
  }
  .list-group-head {
    padding: 10px 16px;
    border-bottom: 1px solid var(--line-hair);
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12.5px;
    font-weight: 700;
  }
  .list-row {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr) 230px;
    gap: 16px;
    padding: 14px 16px;
    background: var(--bg-card);
    border-bottom: 1px solid var(--line-hair);
    cursor: pointer;
    align-items: start;
    transition: background-color 0.14s ease;
  }
  .list-row:last-child { border-bottom: none; }
  .list-row:hover { background: var(--bg-card-hover); }
  .list-row-summary { margin: 4px 0 0; font-size: 13px; color: var(--text-secondary); line-height: 1.6; }

  /* 右侧滑出详情抽屉（Slide-over Drawer） */
  .drawer-backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(5, 6, 10, 0.56);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.22s var(--ease-out);
  }
  .drawer-backdrop.open {
    opacity: 1;
    pointer-events: auto;
  }
  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(500px, 94vw);
    z-index: 60;
    background: var(--bg-drawer);
    border-left: 1px solid var(--line-base);
    box-shadow: -12px 0 40px rgba(0, 0, 0, 0.45);
    transform: translateX(100%);
    transition: transform 0.26s var(--ease-out);
    display: flex;
    flex-direction: column;
  }
  .drawer.open { transform: translateX(0); }
  .drawer-head {
    padding: 16px 20px;
    border-bottom: 1px solid var(--line-hair);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .drawer-body {
    padding: 20px;
    overflow-y: auto;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .drawer-title {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    line-height: 1.4;
  }
  .drawer-summary {
    margin: 0;
    font-size: 13.5px;
    color: var(--text-secondary);
    line-height: 1.72;
  }
  .drawer-sec-h {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 0 0 8px;
  }
  .stage-switch {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 6px;
  }
  .stage-btn {
    border: 1px solid var(--line-hair);
    background: var(--bg-soft);
    color: var(--text-muted);
    font-size: 12px;
    font-weight: 600;
    padding: 7px 6px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.14s ease;
  }
  .stage-btn:hover { border-color: var(--line-base); color: var(--text-primary); }
  .stage-btn.active {
    border-color: var(--line-gold);
    background: var(--gold-bg);
    color: var(--gold);
  }
  .d-metrics {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .d-metric-card {
    background: var(--bg-soft);
    border: 1px solid var(--line-hair);
    border-radius: 8px;
    padding: 10px 12px;
  }
  .d-metric-card .lbl { font-size: 11px; color: var(--text-muted); margin-bottom: 3px; }
  .d-metric-card .val { font-family: var(--font-display); font-size: 19px; font-weight: 700; color: var(--gold); }
  .d-metric-card .old { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); text-decoration: line-through; margin-top: 2px; }
  .bullet-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 7px;
    font-size: 13px;
    color: var(--text-secondary);
  }
  .bullet-list li { position: relative; padding-left: 14px; }
  .bullet-list li::before { content: "›"; position: absolute; left: 0; color: var(--gold); font-weight: 700; }
  .verify-box {
    font-size: 12.5px;
    color: var(--text-muted);
    background: var(--bg-soft);
    border-left: 3px solid var(--gold);
    padding: 9px 12px;
    border-radius: 0 6px 6px 0;
    margin: 0;
  }
  .file-btn-list {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .file-copy-btn {
    width: 100%;
    text-align: left;
    border: 1px solid var(--line-hair);
    background: var(--bg-soft);
    color: var(--text-secondary);
    font-family: var(--font-mono);
    font-size: 11.5px;
    padding: 6px 10px;
    border-radius: 6px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    transition: border-color 0.14s ease, color 0.14s ease;
  }
  .file-copy-btn:hover { border-color: var(--line-gold); color: var(--text-primary); }
  .file-copy-btn span.f-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .file-copy-btn span.f-op { color: var(--text-dim); font-size: 10.5px; flex-shrink: 0; }

  /* 待办抽屉列表 */
  .action-card {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 11px 12px;
    background: var(--bg-soft);
    border: 1px solid var(--line-hair);
    border-radius: 8px;
    cursor: pointer;
  }
  .action-card.done { opacity: 0.48; }
  .action-card.done .ac-title { text-decoration: line-through; }
  .action-card input { margin-top: 3px; width: 15px; height: 15px; accent-color: var(--gold-deep); cursor: pointer; }
  .ac-title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 3px; }
  .ac-desc { font-size: 12px; color: var(--text-muted); line-height: 1.5; }
  .ac-ref { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-dim); margin-top: 4px; word-break: break-all; }

  /* 轻提示 Toast */
  .toast {
    position: fixed;
    left: 50%;
    bottom: 24px;
    transform: translate(-50%, 16px);
    background: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--line-gold);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    padding: 8px 16px;
    border-radius: 99px;
    font-size: 12.5px;
    font-weight: 600;
    opacity: 0;
    pointer-events: none;
    z-index: 80;
    transition: opacity 0.18s ease, transform 0.18s var(--ease-out);
  }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; animation: none !important; }
  }
  @media (max-width: 1080px) {
    .status-strip { grid-template-columns: 1fr; gap: 12px; }
    .strip-right { justify-content: flex-start; }
    .board { grid-template-columns: 1fr; }
    .lane { min-height: auto; }
    .list-row { grid-template-columns: 1fr; gap: 8px; }
  }
  @media (max-width: 680px) {
    .mast-inner { height: auto; padding: 10px 14px; flex-wrap: wrap; }
    .brand-sub { display: none; }
    .mast-actions { width: 100%; flex-wrap: wrap; }
    .search-box { flex: 1; width: auto; }
    .page { padding: 14px 14px 48px; }
  }
</style>
</head>
<body>

<header class="mast">
  <div class="mast-inner">
    <div class="brand">
      <span class="brand-mark"></span>
      <span class="brand-code">SCAVENGER</span>
      <span class="brand-sub">每日开发看板</span>
    </div>

    <div class="date-nav" aria-label="日期导航">
      <button type="button" id="prev-day" class="icon-btn" title="前一天 (←)">‹</button>
      <nav id="date-tabs" class="date-tabs"></nav>
      <button type="button" id="next-day" class="icon-btn" title="后一天 (→)">›</button>
    </div>

    <div class="mast-actions">
      <div class="seg-switch" role="tablist" aria-label="视图切换">
        <button type="button" class="seg-btn active" data-view="board">看板</button>
        <button type="button" class="seg-btn" data-view="list">简报</button>
      </div>
      <button type="button" id="actions-open-btn" class="icon-btn accent">待办 (<span id="actions-badge-num">0</span>)</button>
      <button type="button" id="copy-md-btn" class="icon-btn">复制摘要</button>
      <button type="button" id="theme-btn" class="icon-btn" title="切换深炭双金 / 浅灰看板">浅灰</button>
      <input id="search-input" class="search-box" type="search" placeholder="筛选卡片或文件…" />
    </div>
  </div>
</header>

<main id="app" class="page"></main>

<div id="drawer-backdrop" class="drawer-backdrop"></div>
<aside id="drawer" class="drawer" aria-hidden="true"></aside>
<div id="toast" class="toast" role="status" aria-live="polite"></div>

<script id="worklog-db" type="application/json">__WORKLOG_JSON__</script>
<script>
(function () {
  const rawEl = document.getElementById("worklog-db");
  const DAYS = JSON.parse(rawEl ? rawEl.textContent : "[]");
  const appEl = document.getElementById("app");
  const tabsEl = document.getElementById("date-tabs");
  const prevBtn = document.getElementById("prev-day");
  const nextBtn = document.getElementById("next-day");
  const actionsOpenBtn = document.getElementById("actions-open-btn");
  const actionsBadgeNum = document.getElementById("actions-badge-num");
  const copyBtn = document.getElementById("copy-md-btn");
  const themeBtn = document.getElementById("theme-btn");
  const searchInput = document.getElementById("search-input");
  const backdropEl = document.getElementById("drawer-backdrop");
  const drawerEl = document.getElementById("drawer");
  const toastEl = document.getElementById("toast");

  const LANES = [
    { key: "pending", title: "待验收 / 待拍板", cls: "lane-pending" },
    { key: "progress", title: "进行中 / 跨天跟进", cls: "lane-progress" },
    { key: "done", title: "已完成 / 已验证", cls: "lane-done" }
  ];
  const MIX_PALETTE = ["#f0d372", "#3cd69f", "#6fb5ff", "#d99b38", "#8f9bb3", "#5d8a78"];

  function esc(str) {
    return String(str ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
    }[ch]));
  }

  let toastTimer = null;
  function showToast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 1900);
  }

  function applyTheme(t) {
    if (t === "light") {
      document.documentElement.setAttribute("data-theme", "light");
      themeBtn.textContent = "深炭";
    } else {
      document.documentElement.removeAttribute("data-theme");
      themeBtn.textContent = "浅灰";
    }
    try { localStorage.setItem("scv-kanban-theme", t); } catch (_) {}
  }
  applyTheme((() => { try { return localStorage.getItem("scv-kanban-theme") || "dark"; } catch (_) { return "dark"; } })());
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    applyTheme(next);
  });

  if (!DAYS.length) {
    appEl.innerHTML = `<div class="lane-empty">暂无开发日志数据，请运行 <code>python -X utf8 -B scripts/生成每日工作日志页面.py --auto-date yesterday</code> 生成。</div>`;
    return;
  }

  const urlDate = new URLSearchParams(location.search).get("date");
  let currentDate = DAYS.some((d) => d.date === urlDate) ? urlDate : DAYS[0].date;
  let viewMode = "board";
  let searchQuery = "";
  let selectedGoalId = "";
  let drawerMode = ""; /* "goal" | "actions" | "" */
  let draggingGoalId = "";

  function defaultLaneOf(goal) {
    if (goal.status === "done") return "done";
    if (goal.status === "in_progress") return "progress";
    return "pending";
  }

  function loadLaneOverrides(date) {
    try { return JSON.parse(localStorage.getItem("scv-kanban-lanes-" + date) || "{}"); }
    catch (_) { return {}; }
  }
  function saveLaneOverride(date, goalId, laneKey) {
    const map = loadLaneOverrides(date);
    map[goalId] = laneKey;
    try { localStorage.setItem("scv-kanban-lanes-" + date, JSON.stringify(map)); } catch (_) {}
  }
  function clearLaneOverrides(date) {
    try { localStorage.removeItem("scv-kanban-lanes-" + date); } catch (_) {}
  }

  function loadChecks(date) {
    try { return JSON.parse(localStorage.getItem("scv-sitrep-checks-" + date) || "{}"); }
    catch (_) { return {}; }
  }
  function saveCheck(date, id, checked) {
    const map = loadChecks(date);
    map[id] = checked;
    try { localStorage.setItem("scv-sitrep-checks-" + date, JSON.stringify(map)); } catch (_) {}
  }

  function currentDay() {
    return DAYS.find((d) => d.date === currentDate) || DAYS[0];
  }

  function effectiveLane(day, goal) {
    const overrides = loadLaneOverrides(day.date);
    return overrides[goal.id] || defaultLaneOf(goal);
  }

  function badgeInfo(goal, laneKey) {
    if (laneKey === "done") return { cls: "b-done", text: "已通过" };
    if (laneKey === "progress") return { cls: "b-progress", text: "进行中" };
    if (goal.status === "waiting_decision") return { cls: "b-decision", text: "待拍板" };
    return { cls: "b-accept", text: "待验收" };
  }

  function toMinutes(hhmm) {
    const p = String(hhmm || "00:00").split(":");
    return Math.max(0, Math.min(1440, (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0)));
  }

  function computeCategoryMix(goals) {
    const map = new Map();
    let total = 0;
    goals.forEach((g) => {
      const cat = g.category || "其他";
      let dur = 0;
      (g.sessions || []).forEach((s) => {
        const st = toMinutes(s.start);
        const en = Math.max(st + 15, toMinutes(s.end));
        dur += (en - st);
      });
      if (!dur) dur = 30;
      map.set(cat, (map.get(cat) || 0) + dur);
      total += dur;
    });
    return Array.from(map.entries())
      .map(([name, mins]) => ({ name, mins, pct: total ? Math.round((mins / total) * 100) : 0 }))
      .sort((a, b) => b.mins - a.mins);
  }

  function copyText(text, successMsg) {
    const done = (ok) => showToast(ok ? successMsg : "复制失败，请手动复制");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => done(true)).catch(() => {
        fallbackCopy(text, done);
      });
    } else {
      fallbackCopy(text, done);
    }
  }
  function fallbackCopy(text, cb) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    document.body.removeChild(ta);
    cb(ok);
  }

  function switchDateByOffset(delta) {
    const idx = DAYS.findIndex((d) => d.date === currentDate);
    const nextIdx = idx + delta;
    if (nextIdx >= 0 && nextIdx < DAYS.length) {
      currentDate = DAYS[nextIdx].date;
      closeDrawer();
      renderTabs();
      render();
    }
  }

  function goalCode(day, idx) {
    const mmdd = String(day.date || "").slice(5).replace("-", "") || "0000";
    return mmdd + "-" + String(idx + 1).padStart(2, "0");
  }

  function renderTabs() {
    const idx = DAYS.findIndex((d) => d.date === currentDate);
    prevBtn.disabled = idx >= DAYS.length - 1;
    nextBtn.disabled = idx <= 0;

    const currentMonth = currentDate.slice(0, 7);
    const months = Array.from(new Set(DAYS.map((d) => d.date.slice(0, 7))));
    const monthDays = DAYS.filter((d) => d.date.slice(0, 7) === currentMonth);

    const monthTabsHtml = months.length > 1
      ? months.map((m) => `
          <button type="button" class="date-tab ${m === currentMonth ? "active" : ""}" data-month="${esc(m)}" style="opacity:${m === currentMonth ? 1 : 0.72};">${esc(m)}</button>
        `).join("") + `<span style="color:var(--text-dim);padding:0 2px;">/</span>`
      : "";

    const dayTabsHtml = monthDays.slice(0, 12).map((d) => `
      <button type="button" class="date-tab ${d.date === currentDate ? "active" : ""}" data-date="${esc(d.date)}">
        ${esc(months.length > 1 ? d.date.slice(5) : d.date)}${d.mode === "auto-draft" ? " *" : ""}
      </button>
    `).join("");

    tabsEl.innerHTML = monthTabsHtml + dayTabsHtml;
    tabsEl.querySelectorAll("button[data-month]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const m = btn.getAttribute("data-month");
        const firstInMonth = DAYS.find((d) => d.date.slice(0, 7) === m);
        if (firstInMonth) {
          currentDate = firstInMonth.date;
          closeDrawer();
          renderTabs();
          render();
        }
      });
    });
    tabsEl.querySelectorAll("button[data-date]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentDate = btn.getAttribute("data-date");
        closeDrawer();
        renderTabs();
        render();
      });
    });
  }

  function openGoalDrawer(goalId) {
    selectedGoalId = goalId;
    drawerMode = "goal";
    renderDrawer();
    backdropEl.classList.add("open");
    drawerEl.classList.add("open");
    drawerEl.setAttribute("aria-hidden", "false");
    render();
  }

  function openActionsDrawer() {
    selectedGoalId = "";
    drawerMode = "actions";
    renderDrawer();
    backdropEl.classList.add("open");
    drawerEl.classList.add("open");
    drawerEl.setAttribute("aria-hidden", "false");
  }

  function closeDrawer() {
    drawerMode = "";
    selectedGoalId = "";
    backdropEl.classList.remove("open");
    drawerEl.classList.remove("open");
    drawerEl.setAttribute("aria-hidden", "true");
    render();
  }

  function renderDrawer() {
    const day = currentDay();
    if (drawerMode === "actions") {
      const actions = day.actions || [];
      const checks = loadChecks(day.date);
      const remain = actions.filter((a) => !checks[a.id]).length;
      drawerEl.innerHTML = `
        <div class="drawer-head">
          <strong style="font-size:15px;">待你拍板与验收（剩余 ${remain} / ${actions.length}）</strong>
          <button type="button" id="drawer-close" class="icon-btn">关闭 Esc</button>
        </div>
        <div class="drawer-body">
          ${actions.length ? actions.map((a) => {
            const isDone = !!checks[a.id];
            const isAccept = a.type === "accept";
            return `
              <label class="action-card ${isDone ? "done" : ""}" data-act-id="${esc(a.id)}">
                <input type="checkbox" ${isDone ? "checked" : ""} />
                <div style="min-width:0;flex:1;">
                  <div class="ac-title"><span class="k-badge ${isAccept ? "b-accept" : "b-decision"}" style="margin-right:6px;">${isAccept ? "验收" : "拍板"}</span>${esc(a.title)}</div>
                  <div class="ac-desc">${esc(a.detail)}</div>
                  <div class="ac-ref">${esc(a.ref)}</div>
                </div>
              </label>
            `;
          }).join("") : `<div class="lane-empty">当日无待处理项。</div>`}
        </div>
      `;
      drawerEl.querySelector("#drawer-close").addEventListener("click", closeDrawer);
      drawerEl.querySelectorAll("[data-act-id]").forEach((el) => {
        const id = el.getAttribute("data-act-id");
        el.querySelector("input").addEventListener("change", (ev) => {
          saveCheck(day.date, id, ev.target.checked);
          renderDrawer();
          render();
        });
      });
      return;
    }

    if (drawerMode === "goal") {
      const goals = day.goals || [];
      const idx = goals.findIndex((g) => g.id === selectedGoalId);
      if (idx < 0) return;
      const g = goals[idx];
      const code = goalCode(day, idx);
      const laneKey = effectiveLane(day, g);
      const metrics = g.metrics || [];
      const files = g.files || [];
      const sess = (g.sessions || []).map((s) => `${s.tool} · ${s.start}–${s.end}（${s.label}）`);

      drawerEl.innerHTML = `
        <div class="drawer-head">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="k-code">${code}</span>
            <span style="font-size:12px;color:var(--text-muted);">${esc(g.category)}</span>
          </div>
          <button type="button" id="drawer-close" class="icon-btn">关闭 Esc</button>
        </div>
        <div class="drawer-body">
          <div>
            <h2 class="drawer-title">${esc(g.title)}</h2>
          </div>

          <div>
            <div class="drawer-sec-h">看板泳道流转（或直接拖拽卡片）</div>
            <div class="stage-switch">
              ${LANES.map((l) => `
                <button type="button" class="stage-btn ${laneKey === l.key ? "active" : ""}" data-move-lane="${l.key}">${l.title.split(" / ")[0]}</button>
              `).join("")}
            </div>
          </div>

          <div>
            <div class="drawer-sec-h">成果总结</div>
            <p class="drawer-summary">${esc(g.summary)}</p>
          </div>

          ${metrics.length ? `
            <div>
              <div class="drawer-sec-h">核心量化对比</div>
              <div class="d-metrics">
                ${metrics.map((m) => `
                  <div class="d-metric-card">
                    <div class="lbl">${esc(m.label)}</div>
                    <div class="val">${esc(m.after)}</div>
                    ${m.before && m.before !== "-" ? `<div class="old">${esc(m.before)}</div>` : ""}
                  </div>
                `).join("")}
              </div>
            </div>
          ` : ""}

          ${(g.highlights || []).length ? `
            <div>
              <div class="drawer-sec-h">关键进展要点</div>
              <ul class="bullet-list">${g.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>
            </div>
          ` : ""}

          ${g.verification ? `
            <div>
              <div class="drawer-sec-h">验证与状态结论</div>
              <p class="verify-box">${esc(g.verification)}</p>
            </div>
          ` : ""}

          ${sess.length ? `
            <div>
              <div class="drawer-sec-h">执行时段</div>
              <ul class="bullet-list">${sess.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
            </div>
          ` : ""}

          <div>
            <div class="drawer-sec-h">涉及核心文件（${files.length} · 点击复制路径）</div>
            ${files.length ? `
              <div class="file-btn-list">
                ${files.map((f) => `
                  <button type="button" class="file-copy-btn" data-copy-file="${esc(f)}">
                    <span class="f-path">${esc(f)}</span>
                    <span class="f-op">复制</span>
                  </button>
                `).join("")}
              </div>
            ` : `<div style="font-size:12.5px;color:var(--text-dim);">本项为只读排查，未修改仓库文件。</div>`}
          </div>
        </div>
      `;

      drawerEl.querySelector("#drawer-close").addEventListener("click", closeDrawer);
      drawerEl.querySelectorAll("[data-move-lane]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const targetLane = btn.getAttribute("data-move-lane");
          saveLaneOverride(day.date, g.id, targetLane);
          const laneMeta = LANES.find((l) => l.key === targetLane);
          showToast(`已移入「${laneMeta ? laneMeta.title : targetLane}」`);
          renderDrawer();
          render();
        });
      });
      drawerEl.querySelectorAll("[data-copy-file]").forEach((btn) => {
        btn.addEventListener("click", () => {
          copyText(btn.getAttribute("data-copy-file"), "已复制文件路径");
        });
      });
    }
  }

  function render() {
    const day = currentDay();
    const goals = day.goals || [];
    const actions = day.actions || [];
    const checks = loadChecks(day.date);
    const overrides = loadLaneOverrides(day.date);
    const hasOverrides = Object.keys(overrides).length > 0;
    const remainActions = actions.filter((a) => !checks[a.id]).length;
    actionsBadgeNum.textContent = String(remainActions);

    const filtered = goals.filter((g) => {
      if (!searchQuery) return true;
      const hay = (g.title + " " + g.summary + " " + g.category + " " + (g.files || []).join(" ") + " " + (g.highlights || []).join(" ")).toLowerCase();
      return hay.includes(searchQuery.toLowerCase());
    });

    const buckets = { pending: [], progress: [], done: [] };
    filtered.forEach((g) => {
      const lk = effectiveLane(day, g);
      (buckets[lk] || buckets.pending).push(g);
    });

    const mix = computeCategoryMix(goals);
    const mixBarHtml = mix.map((m, i) => `
      <div class="mix-seg" style="width:${Math.max(5, m.pct)}%;background:${MIX_PALETTE[i % MIX_PALETTE.length]}" title="${esc(m.name)} ${m.pct}%"></div>
    `).join("");
    const mixLegendHtml = mix.slice(0, 4).map((m, i) => `
      <span><i style="background:${MIX_PALETTE[i % MIX_PALETTE.length]}"></i>${esc(m.name)} ${m.pct}%</span>
    `).join("");

    const kpis = day.kpis || {};
    const modeLabel = day.mode === "curated" ? "AI 精炼目标版" : "自动草稿";

    const stripHtml = `
      <section class="status-strip">
        <div class="strip-left">
          <div class="strip-kicker">
            <strong style="color:var(--gold);">${esc(day.date)}（北京时间 UTC+8）</strong>
            <span>·</span>
            <span>${modeLabel}</span>
            <span>·</span>
            <span>拖拽卡片可跨列更新验收进度，点击卡片看详情</span>
            ${hasOverrides ? `<button type="button" id="reset-lanes-btn" class="icon-btn" style="height:22px;font-size:10.5px;">重置流转</button>` : ""}
          </div>
          <p class="strip-headline">${esc(day.headline)}</p>
        </div>

        <div class="strip-mix">
          <div class="mix-top"><span>当日精力分布</span><span>${goals.length} 条目标线</span></div>
          <div class="mix-bar">${mixBarHtml}</div>
          <div class="mix-mini-legend">${mixLegendHtml}</div>
        </div>

        <div class="strip-right">
          <div class="kpi-pill warn"><span>待处理</span><strong>${buckets.pending.length}</strong></div>
          <div class="kpi-pill ok"><span>已完成</span><strong>${buckets.done.length}</strong></div>
          <div class="kpi-pill"><span>改动文件</span><strong>${esc(kpis.repo_files ?? 0)}</strong></div>
        </div>
      </section>
    `;

    let bodyHtml = "";
    if (viewMode === "board") {
      bodyHtml = `
        <section class="board">
          ${LANES.map((lane) => {
            const items = buckets[lane.key];
            const cardsHtml = items.length ? items.map((g) => {
              const origIdx = goals.indexOf(g);
              const code = goalCode(day, origIdx);
              const b = badgeInfo(g, lane.key);
              const firstMetric = (g.metrics || [])[0];
              const sess = (g.sessions || [])[0];
              const toolName = sess ? sess.tool : "项目";
              const avatarChar = toolName === "Codex" ? "C" : "A";
              const timeRange = (g.sessions || []).length
                ? `${g.sessions[0].start}–${g.sessions[g.sessions.length - 1].end}`
                : day.date.slice(5);
              const fileCount = (g.files || []).length;

              return `
                <article class="k-card ${selectedGoalId === g.id ? "selected" : ""}"
                         draggable="true"
                         data-goal-id="${esc(g.id)}">
                  <div class="k-top">
                    <span class="k-id-wrap">
                      <span class="k-code">${code}</span>
                      <span class="k-cat">${esc(g.category)}</span>
                    </span>
                    <span class="k-badge ${b.cls}">${b.text}</span>
                  </div>

                  <h3 class="k-title">${esc(g.title)}</h3>

                  ${firstMetric ? `
                    <div class="k-metric-bar">
                      <span class="k-metric-lbl">${esc(firstMetric.label)}</span>
                      <span class="k-metric-val">${esc(firstMetric.after)}</span>
                    </div>
                  ` : ""}

                  <div class="k-foot">
                    <span class="k-owner">
                      <span class="k-avatar">${avatarChar}</span>
                      <span>${esc(toolName)} · ${esc(timeRange)}</span>
                    </span>
                    <span class="k-meta-right">${fileCount ? `${fileCount} 文件` : "只读排查"}</span>
                  </div>
                </article>
              `;
            }).join("") : `<div class="lane-empty">暂无卡片，可将其他列卡片拖入此处</div>`;

            return `
              <div class="lane ${lane.cls}" data-lane="${lane.key}">
                <div class="lane-head">
                  <span class="lane-title"><i class="lane-dot"></i>${lane.title}</span>
                  <span class="lane-count">${items.length}</span>
                </div>
                <div class="lane-cards">${cardsHtml}</div>
              </div>
            `;
          }).join("")}
        </section>
      `;
    } else {
      bodyHtml = `
        <section class="list-view">
          ${LANES.map((lane) => {
            const items = buckets[lane.key];
            if (!items.length) return "";
            return `
              <div class="list-group ${lane.cls}">
                <div class="list-group-head">
                  <span class="lane-title"><i class="lane-dot"></i>${lane.title}</span>
                  <span class="lane-count">${items.length}</span>
                </div>
                ${items.map((g) => {
                  const origIdx = goals.indexOf(g);
                  const code = goalCode(day, origIdx);
                  const b = badgeInfo(g, lane.key);
                  const firstMetric = (g.metrics || [])[0];
                  return `
                    <div class="list-row" data-goal-id="${esc(g.id)}">
                      <div>
                        <div class="k-code">${code}</div>
                        <span class="k-badge ${b.cls}" style="margin-top:6px;display:inline-block;">${b.text}</span>
                      </div>
                      <div>
                        <div class="k-title" style="-webkit-line-clamp:unset;">${esc(g.title)}</div>
                        <p class="list-row-summary">${esc(g.summary)}</p>
                      </div>
                      <div style="text-align:right;">
                        ${firstMetric ? `<div style="font-size:11px;color:var(--text-muted);">${esc(firstMetric.label)}</div><div class="k-metric-val" style="font-size:16px;margin-top:2px;">${esc(firstMetric.after)}</div>` : ""}
                        <div class="k-meta-right" style="margin-top:8px;">点击展开详情 ▸</div>
                      </div>
                    </div>
                  `;
                }).join("")}
              </div>
            `;
          }).join("")}
        </section>
      `;
    }

    appEl.innerHTML = stripHtml + bodyHtml;

    const resetBtn = document.getElementById("reset-lanes-btn");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        clearLaneOverrides(day.date);
        showToast("已恢复原始看板状态");
        render();
        if (drawerMode === "goal") renderDrawer();
      });
    }

    /* 卡片点击打开抽屉 & 拖拽跨列 */
    appEl.querySelectorAll("[data-goal-id]").forEach((el) => {
      const gid = el.getAttribute("data-goal-id");
      el.addEventListener("click", () => openGoalDrawer(gid));

      if (el.getAttribute("draggable") === "true") {
        el.addEventListener("dragstart", (ev) => {
          draggingGoalId = gid;
          el.classList.add("dragging");
          ev.dataTransfer.effectAllowed = "move";
          ev.dataTransfer.setData("text/plain", gid);
        });
        el.addEventListener("dragend", () => {
          draggingGoalId = "";
          el.classList.remove("dragging");
          appEl.querySelectorAll(".lane.drag-over").forEach((l) => l.classList.remove("drag-over"));
        });
      }
    });

    appEl.querySelectorAll(".lane[data-lane]").forEach((laneEl) => {
      const laneKey = laneEl.getAttribute("data-lane");
      laneEl.addEventListener("dragover", (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        laneEl.classList.add("drag-over");
      });
      laneEl.addEventListener("dragleave", (ev) => {
        if (!laneEl.contains(ev.relatedTarget)) {
          laneEl.classList.remove("drag-over");
        }
      });
      laneEl.addEventListener("drop", (ev) => {
        ev.preventDefault();
        laneEl.classList.remove("drag-over");
        const gid = ev.dataTransfer.getData("text/plain") || draggingGoalId;
        if (!gid) return;
        saveLaneOverride(day.date, gid, laneKey);
        const laneMeta = LANES.find((l) => l.key === laneKey);
        showToast(`已移入「${laneMeta ? laneMeta.title : laneKey}」`);
        render();
        if (drawerMode === "goal" && selectedGoalId === gid) renderDrawer();
      });
    });
  }

  function buildCopySummary(day) {
    const goals = day.goals || [];
    const lines = [
      `# Scavenger 每日开发看板（北京时间 ${day.date}）`,
      ``,
      `> ${day.headline}`,
      ``
    ];
    LANES.forEach((lane) => {
      const list = goals.filter((g) => effectiveLane(day, g) === lane.key);
      if (!list.length) return;
      lines.push(`## ${lane.title}（${list.length}）`);
      list.forEach((g) => {
        const m = (g.metrics || [])[0];
        const mTxt = m ? ` [${m.label}: ${m.after}]` : "";
        lines.push(`- **${g.title}**${mTxt}：${g.summary}`);
      });
      lines.push("");
    });
    return lines.join("\n").trim();
  }

  /* 全局控件事件 */
  prevBtn.addEventListener("click", () => switchDateByOffset(1));
  nextBtn.addEventListener("click", () => switchDateByOffset(-1));
  actionsOpenBtn.addEventListener("click", openActionsDrawer);
  backdropEl.addEventListener("click", closeDrawer);

  copyBtn.addEventListener("click", () => {
    copyText(buildCopySummary(currentDay()), "已复制今日 Markdown 摘要");
  });

  document.querySelectorAll(".seg-btn[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      viewMode = btn.getAttribute("data-view");
      document.querySelectorAll(".seg-btn[data-view]").forEach((b) => b.classList.toggle("active", b === btn));
      render();
    });
  });

  searchInput.addEventListener("input", () => {
    searchQuery = searchInput.value.trim();
    render();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawerMode) {
      closeDrawer();
      return;
    }
    const tag = (document.activeElement && document.activeElement.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.key === "ArrowLeft") switchDateByOffset(1);
    else if (e.key === "ArrowRight") switchDateByOffset(-1);
    else if (e.key === "/") { e.preventDefault(); searchInput.focus(); }
  });

  renderTabs();
  render();
})();
</script>
</body>
</html>
"""


def compile_html(days: list[dict[str, Any]]) -> Path:
    """将多日 JSON 数据嵌入生成单文件离线看板 HTML。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload_json = json.dumps(days, ensure_ascii=False).replace("</", "<\\/")
    html_text = HTML_TEMPLATE.replace("__WORKLOG_JSON__", payload_json)
    OUTPUT_HTML.write_text(html_text, encoding="utf-8")
    return OUTPUT_HTML


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Scavenger 每日工作成果 V4 离线可视化看板")
    parser.add_argument("--auto-date", default="", help="可选：为指定北京时间日期（如 yesterday / 2026-11-22）自动补全 auto-draft JSON")
    args = parser.parse_args(argv)

    if args.auto_date:
        json_path = ensure_auto_draft(args.auto_date)
        print(f"已确认日期数据：{json_path.relative_to(PROJECT_ROOT).as_posix()}")

    days = load_all_days()
    out = compile_html(days)
    print(f"已生成 V4 每日开发看板：{out.relative_to(PROJECT_ROOT).as_posix()}（共汇总 {len(days)} 天）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
