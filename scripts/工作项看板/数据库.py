"""本地工作项看板 SQLite WAL 数据层与领域规则引擎。

核心职责：
- 管理多项目、人类/AI 双轨成员、模块上下文包、标签、工作项、评论、附件与活动事件流；
- 统一 UTC ISO-8601 存储、整型 rev 乐观锁防并发覆盖、严格模式流转校验；
- 实时计算活动工作项之间的项目相对路径（scope_paths）重叠冲突；
- 首次初始化自动写入一套贴近 Scavenger 实战的示范项目与模块数据，开箱即测。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATES: list[dict[str, Any]] = [
    {"key": "backlog", "label": "待规划", "group": "backlog", "color": "#838896", "gate": False},
    {"key": "todo", "label": "待开始", "group": "unstarted", "color": "#68c8ff", "gate": False},
    {"key": "plan_review", "label": "方案待审", "group": "started", "color": "#ffb800", "gate": True},
    {"key": "in_progress", "label": "进行中", "group": "started", "color": "#38e5c0", "gate": False},
    {"key": "qa_wait", "label": "待测试", "group": "started", "color": "#b197fc", "gate": True},
    {"key": "testing", "label": "测试中", "group": "started", "color": "#68c8ff", "gate": False},
    {"key": "rejected", "label": "不通过", "group": "started", "color": "#ff5266", "gate": False},
    {"key": "done", "label": "已完成", "group": "completed", "color": "#3cd69f", "gate": False},
]
VALID_STATE_KEYS = {s["key"] for s in STATES}
ACTIVE_CONFLICT_STATES = {"plan_review", "in_progress"}

PRIORITIES: list[dict[str, str]] = [
    {"key": "urgent", "label": "紧急", "color": "#ff5266"},
    {"key": "high", "label": "高", "color": "#ffb800"},
    {"key": "medium", "label": "中", "color": "#f0d372"},
    {"key": "low", "label": "低", "color": "#68c8ff"},
    {"key": "none", "label": "无", "color": "#838896"},
]
VALID_PRIORITY_KEYS = {p["key"] for p in PRIORITIES}

DEFAULT_MODULE_CATEGORIES: list[dict[str, str]] = [
    {"name": "核心玩法与战斗", "color": "#f97316", "desc": "G02–G08 主线玩法、载具数值基底、尸群行为与三区占领经济"},
    {"name": "交互与界面", "color": "#3b82f6", "desc": "G01 RTS 操控、战术标绘层、Code SSOT V2.2 规范与主入口大厅"},
    {"name": "地图、载具与视听表现", "color": "#a855f7", "desc": "9km 大地图与编辑器、129 车流式动效、写实爆炸 V3 与空间声学"},
    {"name": "资产管线与主服务", "color": "#14b8a6", "desc": "断箭资源提取、前线资产库、8800 单端口主服与工程清理门禁"},
    {"name": "团队协作与研发工具", "color": "#22c55e", "desc": "专属 18800 本地工作项看板、多 Agent 协作与每日开发日志"},
]

DEFAULT_LABELS: list[tuple[str, str]] = [
    ("功能需求", "#3b82f6"),
    ("缺陷修复", "#ef4444"),
    ("性能优化", "#14b8a6"),
    ("UI/UX", "#a855f7"),
    ("AI协作", "#f59e0b"),
    ("资产管线", "#06b6d4"),
    ("数值/规则", "#f97316"),
    ("需评审", "#eab308"),
]

SCAVENGER_PRESET_MODULES: list[dict[str, Any]] = [
    {
        "category": "核心玩法与战斗",
        "name": "载具战斗与数值基底",
        "summary": "武器弹药装填、固定翼攻击机制与 ba-source-ssot-v1 唯一数值基底",
        "scope_desc": "维护载具武器选弹、有限库存、弹道拦截、固定翼底座及全项目 GLOBAL_BALANCE_SCALES 派生公式（对应 G04/G05/G06）。",
        "capabilities": "已确立 brokenarrowstats.com 唯一原值基底与固定翼通用底座，支持 A-10C 扫射/轰炸与盘旋。",
        "redlines": "严禁各模块私自写死局部换算系数，所有实战数值必须经由 src/程序化地图/数值基底与倍率规范.mjs 派生。",
        "entry_paths": ["src/程序化地图/数值基底与倍率规范.mjs", "src/程序化地图/固定翼底座.mjs", "src/地面载具实装/"],
        "verify_cmd": "python -X utf8 -B scripts/检查项目结构.py",
        "color": "#f97316",
    },
    {
        "category": "核心玩法与战斗",
        "name": "尸群系统与动画分级",
        "summary": "13 款断箭丧尸 GPU 96Hz 合批、近低模远替身与声源诱导行为",
        "scope_desc": "维护断箭原作 13 款骨骼丧尸的 GPU 姿态纹理合批、近个体/远替身分级及游荡/诱导/追击/绕障/死亡/群声规则（对应 G02/G03）。",
        "capabilities": "已完成 13 款拆分入库（256 档 2.58MB）及 500 只混合群体基准测试底座。",
        "redlines": "新玩法仅使用断箭丧尸，旧卡通方案不再进入主线；近景简化表现不得改变实际人数、伤害与占区判定。",
        "entry_paths": ["src/原作群尸/", "src/丧尸群体测试/", "scripts/原作群尸/"],
        "verify_cmd": "python -X utf8 -B scripts/原作群尸/验证运行.py",
        "color": "#ef4444",
    },
    {
        "category": "核心玩法与战斗",
        "name": "大地图任务与后勤经济",
        "summary": "三区肃清占领、视野迷雾、直升机诱饵与前线/回站补给增援",
        "scope_desc": "维护大地图三区自由肃清、占领中断/失守、迷雾最后发现位置、诱饵投放、点数收入与补给车运输机制（对应 G07/G08）。",
        "capabilities": "正式策划书 V002 与 G00–G08 开发目标人工验收总表已冻结规则口径。",
        "redlines": "严禁沿用旧防线无限弹药或无损规则；每个目标须按设计确认→实装→技术检查→人工验收串行推进。",
        "entry_paths": ["src/大地图肃清/", "docs/大地图肃清-正式策划书.md", "docs/大地图肃清-开发目标与人工验收.md"],
        "verify_cmd": "node scripts/大地图肃清/验证规则.mjs",
        "color": "#f59e0b",
    },
    {
        "category": "交互与界面",
        "name": "操作交互与战术标绘",
        "summary": "RTS 方案 A 键鼠、右键队形预览、Shift 队列、4 级视距图标与 ESC 仲裁",
        "scope_desc": "维护 src/交互系统/ 下的镜头控制、点框选与 1~6/Shift+1~9 编组、右键预览、高清 2D 标绘层、固定翼贴地主范围圈与 8 槽轮盘（对应 G01）。",
        "capabilities": "已实现 CQS 命令-快照分离、多车横队展开、贴身名牌防叠字与 5 级 ESC 撤退仲裁。",
        "redlines": "严禁绕过 契约与仲裁.mjs 监听全局快捷键；点选/框选/下令永久零 UI 提示音；2D 标绘层保持原生 DPR 无滤镜。",
        "entry_paths": ["src/交互系统/", "scripts/交互系统/"],
        "verify_cmd": "node scripts/交互系统/验证交互规则.mjs",
        "color": "#3b82f6",
    },
    {
        "category": "交互与界面",
        "name": "全局UI规范与主入口",
        "summary": "Code SSOT V2.2 令牌/剪影/组件、/ui-spec 活体台与 4A/4B/设置页",
        "scope_desc": "维护 src/界面规范/ 共享单源与 src/主入口/ 的 4A 主菜单大厅、4B 129 车 680p 武器库整备台及系统设置。",
        "capabilities": "已收口唯一设计令牌、品牌三楔盾、35+ 军事剪影、6 槽编队持久化与防滞后门禁。",
        "redlines": "增改设计源必须同轮三向同步（核心源 ↔ /ui-spec ↔ docs/全局UI-UX规范.md）；严禁原生 <select> 或容器 transform: scale()。",
        "entry_paths": ["src/界面规范/", "src/主入口/", "scripts/界面规范/"],
        "verify_cmd": "python -X utf8 -B scripts/界面规范/验证界面规范.py --base-url http://127.0.0.1:8800",
        "color": "#eab308",
    },
    {
        "category": "地图、载具与视听表现",
        "name": "9km程序化大地图与编辑器",
        "summary": "stylized-680p-v1 渲染基线、9km 地形建筑植被、LOD 过渡与 /editor",
        "scope_desc": "维护 9km 程序化地形、道路、模块化建筑、立体植被、远中近 LOD 过渡管线、4m A* 导路及地图编辑器实时物理派生。",
        "capabilities": "已锁定 680p 复古微缩双画幅（16:9 1208×680 / 21:9 1588×680 Hor+）与 .scvmap Range 206 流式归档。",
        "redlines": "严禁改动 stylized-680p-v1 渲染基线参数；非授权不得篡改并行会话维护的 过渡动画管线.mjs 与 地图编辑器.mjs。",
        "entry_paths": ["src/程序化地图/", "scripts/程序化地图/"],
        "verify_cmd": "python -X utf8 -B scripts/程序化地图/验证实战HUD.py --port 8800 --path /play",
        "color": "#8b5cf6",
    },
    {
        "category": "地图、载具与视听表现",
        "name": "129车程序化载具与动效",
        "summary": "129 款美军空地载具 .scvveh 流式加载、KTX2 图集与部件旋转驱动",
        "scope_desc": "维护全库 129 车三档 LOD 离线编译、流式归档加载器、实例池、刚体姿态与 37+ 车旋翼/炮塔/雷达部件动效。",
        "capabilities": "已完成 129 车全库视觉六视角验真与 /dev/battlefield 100~300 车混编压测场。",
        "redlines": "离线重编 .scvveh 须走 IoU/SSIM 核验与 stage/accept 7 天退役流程，不覆盖当前受保护基线。",
        "entry_paths": ["src/程序化载具/", "scripts/程序化载具/"],
        "verify_cmd": "node scripts/程序化载具/验证静态结构.mjs",
        "color": "#ec4899",
    },
    {
        "category": "地图、载具与视听表现",
        "name": "写实爆炸与战场特效",
        "summary": "写实爆炸 V3.0（8×8 体积云图集、黑体辐射、Soft Particles 与焦坑）",
        "scope_desc": "维护 RealisticVfxEngine 爆炸火球、非发光贴地扬尘、破片烟丝、炽热火花、深度柔边粒子与斜风黑烟柱。",
        "capabilities": "已完成离线 8×8 体积云图集烘焙与 P1/P0/P2 升级实施蓝图。",
        "redlines": "热路径严禁每帧堆分配或闭包创建；不得修改 UnrealBloomPass 1.35 阈值与全局暖调滤镜。",
        "entry_paths": ["src/程序化地图/写实爆炸特效.mjs", "public/assets/vfx-explosion/", "scripts/程序化地图/验证写实爆炸特效.py"],
        "verify_cmd": "python -X utf8 -B scripts/程序化地图/验证写实爆炸特效.py",
        "color": "#fb7185",
    },
    {
        "category": "地图、载具与视听表现",
        "name": "战场声学与空间音效",
        "summary": "36 条 MoveEvent 无缝循环、8 声部 3D 空间引擎混音与物理声学传播",
        "scope_desc": "维护载具音效分类索引台（/dev/audio/）、机械引擎循环派生、距离衰减/多普勒/声学传播与炮声爆炸混音。",
        "capabilities": "已完成 253 条音效入库、/dev/audio/ 索引验听台及战场现实声学 V3 底座。",
        "redlines": "严禁给点选/框选/下令配哔哔声；空间引擎音必须受 /settings 独立开关 vehicleAudioEnabled 控制。",
        "entry_paths": ["src/音效索引/", "docs/音效与渲染/"],
        "verify_cmd": "python -X utf8 -B scripts/检查项目结构.py",
        "color": "#a855f7",
    },
    {
        "category": "资产管线与主服务",
        "name": "断箭资产提取与前线库",
        "summary": "UnityFS 只读提取、Blender 回读、revision 7 正式库与 /dev/library/",
        "scope_desc": "维护《断箭》只读解包、材质与骨骼还原、正式资料库增量发布（276 模型 / 253 声音 / Airbase 场景）与整库压缩归档。",
        "capabilities": "支持 断箭/查询资料库.py 按 ID/型号秒查，/dev/library/ 三档响应式资产检视台。",
        "redlines": "游戏安装目录仅只读打开；原件与冷归档保存在 local-data/ 永不入库 Git，查询优先走查询脚本不遍历提取目录。",
        "entry_paths": ["src/资料库/", "scripts/断箭/", "scripts/资源库/", "scripts/资源优化/"],
        "verify_cmd": "python -X utf8 -B scripts/断箭/查询资料库.py --query M1A2 --json",
        "color": "#14b8a6",
    },
    {
        "category": "资产管线与主服务",
        "name": "8800主服务与工程门禁",
        "summary": "唯一 127.0.0.1:8800 统一服务、全仓结构检查、双浏览器回归与清理维护",
        "scope_desc": "维护 启动Scavenger.cmd、scripts/启动主服务.py 路由与资产 Range 分发、检查项目结构.py 全仓门禁与 7 天版本退役/定时清理工具。",
        "capabilities": "已实现前台留窗关窗停服、外部占口只诊断保护、全仓防漂移门禁与定时维护计划任务。",
        "redlines": "游戏业务严禁新开独立端口；8800 永远只绑 127.0.0.1 回环；禁止绕过 清理工具.py 直接脚本删历史载荷。",
        "entry_paths": ["scripts/启动主服务.py", "scripts/检查项目结构.py", "scripts/验证主服务全链路.py", "scripts/维护清理/"],
        "verify_cmd": "python -X utf8 -B scripts/检查项目结构.py",
        "color": "#06b6d4",
    },
    {
        "category": "团队协作与研发工具",
        "name": "工作项看板与多Agent协作",
        "summary": "专属 18800 独立本地/局域网看板、8 列人机闸门、路径防撞与 AI CLI",
        "scope_desc": "维护独立于 8800 的本地工作项看板 SQLite WAL 引擎、SSE 实时同步、单人/局域网双鉴权、模块上下文包与多 Agent CLI。",
        "capabilities": "已完成 V001 完整闭环（专属 18800、8 列拖拽、Ctrl+V 贴图、路径冲突雷达与自动化验证）。",
        "redlines": "专属 18800 端口独立运行，不得与 8800 游戏主服合并进程或暴露游戏 local-data/ 资产；所有路径保持项目相对路径。",
        "entry_paths": ["scripts/工作项看板/", "src/工作项看板/", "docs/研发协作/"],
        "verify_cmd": "python -X utf8 -B scripts/工作项看板/验证看板闭环.py",
        "color": "#22c55e",
    },
    {
        "category": "团队协作与研发工具",
        "name": "每日开发日志与简报",
        "summary": "按 UTC+8 采集多会话轨迹、按月 JSON 归档与 V4 离线可视化看板",
        "scope_desc": "维护 daily-dev-log 技能与快照采集/页面编译脚本，输出每日三列 Kanban 与高管简报双视图离线看板。",
        "capabilities": "已落地 reports/开发日志/每日开发看板.html 唯一总入口与按月 JSON 命名门禁。",
        "redlines": "reports/开发日志/ 根层仅允许 每日开发看板.html 与 数据/YYYY-MM/YYYY-MM-DD-开发日志.json。",
        "entry_paths": ["reports/开发日志/", "scripts/生成每日工作快照.py", "scripts/生成每日工作日志页面.py", ".agents/skills/daily-dev-log/"],
        "verify_cmd": "python -X utf8 -B scripts/检查项目结构.py",
        "color": "#10b981",
    },
]


class BoardError(Exception):
    status_code = 400
    error_code = "bad_request"

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.payload = payload or {}


class ValidationError(BoardError):
    status_code = 400
    error_code = "validation_error"


class NotFoundError(BoardError):
    status_code = 404
    error_code = "not_found"


class ConflictError(BoardError):
    status_code = 409
    error_code = "rev_conflict"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(12)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 60000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_pin(pin: str, encoded: str) -> bool:
    if not encoded or "$" not in encoded:
        return False
    parts = encoded.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected = parts
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 60000).hex()
    return hmac.compare_digest(digest, expected)


def generate_api_token() -> str:
    return "lb_" + secrets.token_hex(18)


def normalize_rel_path(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text:
        return ""
    if ":" in text.split("/")[0] or ".." in text.split("/"):
        raise ValidationError(f"作用域路径必须使用合法的项目相对路径：{raw}")
    return text


def normalize_scope_paths(raw_list: Any) -> list[str]:
    if raw_list is None:
        return []
    if isinstance(raw_list, str):
        raw_items = [line.strip() for line in raw_list.replace(",", "\n").splitlines()]
    elif isinstance(raw_list, list):
        raw_items = [str(x).strip() for x in raw_list]
    else:
        raise ValidationError("scope_paths 必须为字符串列表")
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw_items:
        norm = normalize_rel_path(entry)
        if norm and norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return cleaned


def paths_overlap(path_a: str, path_b: str) -> bool:
    if not path_a or not path_b:
        return False
    a = path_a.replace("\\", "/").strip("/").lower()
    b = path_b.replace("\\", "/").strip("/").lower()
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def compute_scope_conflicts(items: list[dict[str, Any]]) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """检测活动工作项（方案待审 / 进行中）之间的作用域路径重叠冲突。"""
    active = [it for it in items if it.get("state") in ACTIVE_CONFLICT_STATES and it.get("scope_paths")]
    by_item: dict[int, list[dict[str, Any]]] = {}
    pairs: list[dict[str, Any]] = []
    for i in range(len(active)):
        a = active[i]
        for j in range(i + 1, len(active)):
            b = active[j]
            # 直接父子工作项之间不互相触发路径重叠冲突误报
            if a.get("parent_id") == b["id"] or b.get("parent_id") == a["id"]:
                continue
            overlaps: list[dict[str, str]] = []
            for pa in a["scope_paths"]:
                for pb in b["scope_paths"]:
                    if paths_overlap(pa, pb):
                        overlaps.append({"a": pa, "b": pb})
            if overlaps:
                pair_record = {
                    "item_a": {"id": a["id"], "item_key": a["item_key"], "title": a["title"], "state": a["state"], "agent_id": a.get("agent_id")},
                    "item_b": {"id": b["id"], "item_key": b["item_key"], "title": b["title"], "state": b["state"], "agent_id": b.get("agent_id")},
                    "overlaps": overlaps,
                }
                pairs.append(pair_record)
                by_item.setdefault(a["id"], []).append({
                    "other_id": b["id"],
                    "other_key": b["item_key"],
                    "other_title": b["title"],
                    "other_state": b["state"],
                    "overlaps": overlaps,
                })
                by_item.setdefault(b["id"], []).append({
                    "other_id": a["id"],
                    "other_key": a["item_key"],
                    "other_title": a["title"],
                    "other_state": a["state"],
                    "overlaps": [{"a": o["b"], "b": o["a"]} for o in overlaps],
                })
    return by_item, pairs


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    strict_mode INTEGER NOT NULL DEFAULT 0,
    next_seq INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('human', 'agent')),
    role TEXT NOT NULL DEFAULT '',
    avatar_color TEXT NOT NULL DEFAULT '#3b82f6',
    pin_hash TEXT NOT NULL DEFAULT '',
    api_token TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category TEXT NOT NULL DEFAULT '未分类',
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    scope_desc TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '',
    redlines TEXT NOT NULL DEFAULT '',
    entry_paths_json TEXT NOT NULL DEFAULT '[]',
    verify_cmd TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#6366f1',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#64748b',
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    seq_num INTEGER NOT NULL,
    item_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description_md TEXT NOT NULL DEFAULT '',
    plan_md TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    parent_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
    module_id INTEGER REFERENCES modules(id) ON DELETE SET NULL,
    owner_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    agent_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    agent_host TEXT NOT NULL DEFAULT '',
    agent_branch TEXT NOT NULL DEFAULT '',
    agent_heartbeat_at TEXT NOT NULL DEFAULT '',
    scope_paths_json TEXT NOT NULL DEFAULT '[]',
    blocked_by_json TEXT NOT NULL DEFAULT '[]',
    sort_order REAL NOT NULL DEFAULT 1000.0,
    rev INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES members(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_item_labels (
    work_item_id INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, label_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    body_md TEXT NOT NULL,
    comment_type TEXT NOT NULL DEFAULT 'comment',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    ext TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    uploaded_by INTEGER REFERENCES members(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    work_item_id INTEGER,
    actor_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_project_state ON work_items(project_id, state, sort_order);
CREATE INDEX IF NOT EXISTS idx_comments_item ON comments(work_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attachments_item ON attachments(work_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_project ON activity_events(project_id, seq);
"""


class BoardStore:
    """线程安全的 SQLite WAL 工作项看板存储引擎。"""

    def __init__(self, db_path: Path, *, seed_workspace: bool = True):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db(seed_workspace=seed_workspace)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    @contextmanager
    def _tx(self):
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def _ro(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self, *, seed_workspace: bool) -> None:
        with self._tx() as conn:
            conn.executescript(SCHEMA_SQL)
            # 幂等轻量迁移：为存量库补齐 modules.category 与 work_items.parent_id 字段
            mod_cols = {r["name"] for r in conn.execute("PRAGMA table_info(modules)").fetchall()}
            if "category" not in mod_cols:
                conn.execute("ALTER TABLE modules ADD COLUMN category TEXT NOT NULL DEFAULT '未分类'")
            item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
            if "parent_id" not in item_cols:
                conn.execute("ALTER TABLE work_items ADD COLUMN parent_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_parent ON work_items(project_id, parent_id)")
            if seed_workspace:
                row = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()
                if row["c"] == 0:
                    self._seed_initial_workspace(conn)
                else:
                    # 幂等补齐默认 SCAV 项目的标准标签（不覆盖已有自定义标签与颜色）
                    scav_row = conn.execute("SELECT id FROM projects WHERE UPPER(key) = 'SCAV' LIMIT 1").fetchone()
                    if scav_row:
                        for lb_name, lb_color in DEFAULT_LABELS:
                            conn.execute(
                                "INSERT OR IGNORE INTO labels(project_id, name, color) VALUES (?, ?, ?)",
                                (int(scav_row["id"]), lb_name, lb_color),
                            )

    def _seed_initial_workspace(self, conn: sqlite3.Connection) -> None:
        """初始化空白正式工作区：仅写入成员、SCAV 项目、5 大分类 × 13 个实战模块与标准标签，不注入任何假工作项。"""
        now = utc_now_iso()
        # 1. 预置人类主理人与 3 个 AI Agent 槽位
        members_seed = [
            ("engineer", "工程师", "human", "项目主理人 / 验收人", "#f59e0b", hash_pin("1234")),
            ("claude_lead", "Claude-主程", "agent", "架构设计与核心编码", "#8b5cf6", ""),
            ("gemini_ui", "Gemini-前端", "agent", "看板界面与交互设计", "#06b6d4", ""),
            ("codex_qa", "Codex-验收", "agent", "自动化回归与门禁巡检", "#10b981", ""),
        ]
        member_ids: dict[str, int] = {}
        for name, display, kind, role, color, pin_h in members_seed:
            cur = conn.execute(
                """INSERT INTO members(name, display_name, kind, role, avatar_color, pin_hash, api_token, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (name, display, kind, role, color, pin_h, generate_api_token(), now),
            )
            member_ids[name] = int(cur.lastrowid)

        # 2. 预置默认项目：SCAV（next_seq 从 1 起步，零假工作项）
        cur = conn.execute(
            """INSERT INTO projects(key, name, description, strict_mode, next_seq, created_at, updated_at)
               VALUES (?, ?, ?, 0, 1, ?, ?)""",
            ("SCAV", "Scavenger 主线研发", "Scavenger 网页端 3D 战术游戏主线任务、模块上下文与多 AI Agent 并行派单看板", now, now),
        )
        project_id = int(cur.lastrowid)

        # 3. 预置 5 大分类 × 13 个 Scavenger 实战模块上下文包（AI 拉单自动注入）
        for m in SCAVENGER_PRESET_MODULES:
            conn.execute(
                """INSERT INTO modules(project_id, category, name, summary, scope_desc, capabilities, redlines, entry_paths_json, verify_cmd, color, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    m["category"],
                    m["name"],
                    m["summary"],
                    m["scope_desc"],
                    m["capabilities"],
                    m["redlines"],
                    json.dumps(normalize_scope_paths(m["entry_paths"]), ensure_ascii=False),
                    m["verify_cmd"],
                    m["color"],
                    now,
                    now,
                ),
            )

        # 4. 预置标准标签（8 个高频标签）
        for name, color in DEFAULT_LABELS:
            conn.execute("INSERT INTO labels(project_id, name, color) VALUES (?, ?, ?)", (project_id, name, color))

        self._append_event(conn, project_id, None, member_ids["engineer"], "workspace_initialized", {"project_key": "SCAV"})

    def _append_event(
        self,
        conn: sqlite3.Connection,
        project_id: int | None,
        work_item_id: int | None,
        actor_id: int | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        cur = conn.execute(
            """INSERT INTO activity_events(project_id, work_item_id, actor_id, event_type, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, work_item_id, actor_id, event_type, json.dumps(payload, ensure_ascii=False), utc_now_iso()),
        )
        return int(cur.lastrowid)

    # ---------- 成员与鉴权 ----------

    def list_members(self, *, include_tokens: bool = False) -> list[dict[str, Any]]:
        with self._ro() as conn:
            rows = conn.execute("SELECT * FROM members WHERE is_active = 1 ORDER BY CASE kind WHEN 'human' THEN 0 ELSE 1 END, id ASC").fetchall()
            return [self._member_row_to_dict(r, include_token=include_tokens) for r in rows]

    def _member_row_to_dict(self, row: sqlite3.Row, *, include_token: bool = False) -> dict[str, Any]:
        d = {
            "id": row["id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "kind": row["kind"],
            "role": row["role"],
            "avatar_color": row["avatar_color"],
            "has_pin": bool(row["pin_hash"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }
        if include_token:
            d["api_token"] = row["api_token"]
        else:
            tok = row["api_token"] or ""
            d["token_preview"] = f"{tok[:7]}...{tok[-4:]}" if len(tok) > 12 else "***"
        return d

    def get_default_human(self) -> dict[str, Any] | None:
        with self._ro() as conn:
            row = conn.execute("SELECT * FROM members WHERE kind = 'human' AND is_active = 1 ORDER BY id ASC LIMIT 1").fetchone()
            return self._member_row_to_dict(row) if row else None

    def get_member_by_id(self, member_id: int, *, include_token: bool = False) -> dict[str, Any] | None:
        with self._ro() as conn:
            row = conn.execute("SELECT * FROM members WHERE id = ? AND is_active = 1", (member_id,)).fetchone()
            return self._member_row_to_dict(row, include_token=include_token) if row else None

    def get_member_by_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._ro() as conn:
            row = conn.execute("SELECT * FROM members WHERE api_token = ? AND is_active = 1", (token.strip(),)).fetchone()
            return self._member_row_to_dict(row) if row else None

    def authenticate_pin(self, member_id: int, pin: str) -> dict[str, Any] | None:
        with self._ro() as conn:
            row = conn.execute("SELECT * FROM members WHERE id = ? AND kind = 'human' AND is_active = 1", (member_id,)).fetchone()
            if not row or not row["pin_hash"]:
                return None
            if verify_pin(pin or "", row["pin_hash"]):
                return self._member_row_to_dict(row)
            return None

    def create_member(self, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        display_name = (data.get("display_name") or "").strip()
        if not display_name:
            raise ValidationError("成员显示名不能为空")
        kind = (data.get("kind") or "human").strip()
        if kind not in ("human", "agent"):
            raise ValidationError("kind 必须为 human 或 agent")
        raw_name = (data.get("name") or "").strip() or f"{kind}_{secrets.token_hex(3)}"
        role = (data.get("role") or "").strip()
        raw_color = (data.get("avatar_color") or "").strip()
        avatar_color = raw_color if re.fullmatch(r"#[0-9a-fA-F]{3,8}", raw_color) else ("#3b82f6" if kind == "human" else "#8b5cf6")
        pin = (data.get("pin") or "").strip()
        pin_hash = hash_pin(pin) if (kind == "human" and pin) else (hash_pin("1234") if kind == "human" else "")
        token = generate_api_token()
        now = utc_now_iso()
        with self._tx() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO members(name, display_name, kind, role, avatar_color, pin_hash, api_token, is_active, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (raw_name, display_name, kind, role, avatar_color, pin_hash, token, now),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"成员标识已存在：{raw_name}")
            mid = int(cur.lastrowid)
            seq = self._append_event(conn, None, None, actor_id, "member_created", {"member_id": mid, "display_name": display_name, "kind": kind})
            row = conn.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
            return self._member_row_to_dict(row, include_token=True), seq

    def update_member(self, member_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if not row:
                raise NotFoundError(f"成员不存在：{member_id}")
            display_name = (data.get("display_name") if "display_name" in data else row["display_name"]).strip()
            if not display_name:
                raise ValidationError("成员显示名不能为空")
            role = (data.get("role") if "role" in data else row["role"]).strip()
            raw_color = (data.get("avatar_color") if "avatar_color" in data else row["avatar_color"]).strip()
            avatar_color = raw_color if re.fullmatch(r"#[0-9a-fA-F]{3,8}", raw_color) else (row["avatar_color"] or "#64748b")
            pin_hash = row["pin_hash"]
            if data.get("pin"):
                pin_hash = hash_pin(str(data["pin"]).strip())
            api_token = generate_api_token() if data.get("rotate_token") else row["api_token"]
            is_active = int(bool(data.get("is_active", row["is_active"])))
            conn.execute(
                """UPDATE members SET display_name = ?, role = ?, avatar_color = ?, pin_hash = ?, api_token = ?, is_active = ?
                   WHERE id = ?""",
                (display_name, role, avatar_color, pin_hash, api_token, is_active, member_id),
            )
            seq = self._append_event(conn, None, None, actor_id, "member_updated", {"member_id": member_id})
            updated = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            return self._member_row_to_dict(updated, include_token=bool(data.get("rotate_token"))), seq

    # ---------- 项目、模块与标签 ----------

    def list_projects(self) -> list[dict[str, Any]]:
        with self._ro() as conn:
            rows = conn.execute(
                """SELECT p.*,
                          (SELECT COUNT(*) FROM work_items w WHERE w.project_id = p.id) AS item_count,
                          (SELECT COUNT(*) FROM work_items w WHERE w.project_id = p.id AND w.state = 'done') AS done_count
                   FROM projects p ORDER BY p.id ASC"""
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "key": r["key"],
                    "name": r["name"],
                    "description": r["description"],
                    "strict_mode": bool(r["strict_mode"]),
                    "next_seq": r["next_seq"],
                    "item_count": r["item_count"],
                    "done_count": r["done_count"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]

    def resolve_project(self, project_ref: int | str) -> dict[str, Any]:
        with self._ro() as conn:
            if isinstance(project_ref, int) or (isinstance(project_ref, str) and project_ref.isdigit()):
                row = conn.execute("SELECT * FROM projects WHERE id = ?", (int(project_ref),)).fetchone()
            else:
                row = conn.execute("SELECT * FROM projects WHERE UPPER(key) = UPPER(?)", (str(project_ref).strip(),)).fetchone()
            if not row:
                raise NotFoundError(f"项目不存在：{project_ref}")
            return {
                "id": row["id"],
                "key": row["key"],
                "name": row["name"],
                "description": row["description"],
                "strict_mode": bool(row["strict_mode"]),
                "next_seq": row["next_seq"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def create_project(self, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        key = (data.get("key") or "").strip().upper()
        name = (data.get("name") or "").strip()
        if not key or not key.isalnum() or len(key) > 12:
            raise ValidationError("项目代号 key 须为 1~12 位字母或数字（如 SCAV / RX003）")
        if not name:
            raise ValidationError("项目名称不能为空")
        desc = (data.get("description") or "").strip()
        strict = 1 if data.get("strict_mode") else 0
        now = utc_now_iso()
        with self._tx() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO projects(key, name, description, strict_mode, next_seq, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (key, name, desc, strict, now, now),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"项目代号已存在：{key}")
            pid = int(cur.lastrowid)
            for lname, lcolor in (("功能需求", "#3b82f6"), ("缺陷修复", "#ef4444"), ("AI协作", "#f59e0b")):
                conn.execute("INSERT INTO labels(project_id, name, color) VALUES (?, ?, ?)", (pid, lname, lcolor))
            seq = self._append_event(conn, pid, None, actor_id, "project_created", {"project_id": pid, "key": key})
            return self.resolve_project(pid), seq

    def update_project(self, project_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                raise NotFoundError(f"项目不存在：{project_id}")
            name = (data.get("name") if "name" in data else row["name"]).strip()
            if not name:
                raise ValidationError("项目名称不能为空")
            desc = (data.get("description") if "description" in data else row["description"]).strip()
            strict = int(bool(data["strict_mode"])) if "strict_mode" in data else row["strict_mode"]
            now = utc_now_iso()
            conn.execute(
                "UPDATE projects SET name = ?, description = ?, strict_mode = ?, updated_at = ? WHERE id = ?",
                (name, desc, strict, now, project_id),
            )
            seq = self._append_event(conn, project_id, None, actor_id, "project_updated", {"project_id": project_id, "strict_mode": bool(strict)})
            return self.resolve_project(project_id), seq

    def list_modules(self, project_id: int) -> list[dict[str, Any]]:
        with self._ro() as conn:
            rows = conn.execute(
                """SELECT m.*,
                          (SELECT COUNT(*) FROM work_items w WHERE w.module_id = m.id) AS item_count,
                          (SELECT COUNT(*) FROM work_items w WHERE w.module_id = m.id AND w.state = 'done') AS done_count
                   FROM modules m WHERE m.project_id = ? ORDER BY m.id ASC""",
                (project_id,),
            ).fetchall()
            return [self._module_row_to_dict(r) for r in rows]

    def _module_row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        keys = set(r.keys())
        d = {
            "id": r["id"],
            "project_id": r["project_id"],
            "category": (r["category"] if "category" in keys else "未分类") or "未分类",
            "name": r["name"],
            "summary": r["summary"],
            "scope_desc": r["scope_desc"],
            "capabilities": r["capabilities"],
            "redlines": r["redlines"],
            "entry_paths": json.loads(r["entry_paths_json"] or "[]"),
            "verify_cmd": r["verify_cmd"],
            "color": r["color"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        if "item_count" in keys:
            d["item_count"] = r["item_count"]
            d["done_count"] = r["done_count"]
        return d

    def create_module(self, project_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("模块名称不能为空")
        category = (data.get("category") or "未分类").strip() or "未分类"
        entry_paths = normalize_scope_paths(data.get("entry_paths"))
        now = utc_now_iso()
        with self._tx() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO modules(project_id, category, name, summary, scope_desc, capabilities, redlines, entry_paths_json, verify_cmd, color, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        category,
                        name,
                        (data.get("summary") or "").strip(),
                        (data.get("scope_desc") or "").strip(),
                        (data.get("capabilities") or "").strip(),
                        (data.get("redlines") or "").strip(),
                        json.dumps(entry_paths, ensure_ascii=False),
                        (data.get("verify_cmd") or "").strip(),
                        (data.get("color") or "#6366f1").strip(),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"模块名称已存在：{name}")
            mid = int(cur.lastrowid)
            seq = self._append_event(conn, project_id, None, actor_id, "module_created", {"module_id": mid, "category": category, "name": name})
            row = conn.execute("SELECT * FROM modules WHERE id = ?", (mid,)).fetchone()
            return self._module_row_to_dict(row), seq

    def update_module(self, module_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
            if not row:
                raise NotFoundError(f"模块不存在：{module_id}")
            name = (data.get("name") if "name" in data else row["name"]).strip()
            if not name:
                raise ValidationError("模块名称不能为空")
            category = ((data.get("category") if "category" in data else row["category"]) or "未分类").strip() or "未分类"
            entry_paths = normalize_scope_paths(data["entry_paths"]) if "entry_paths" in data else json.loads(row["entry_paths_json"] or "[]")
            now = utc_now_iso()
            try:
                conn.execute(
                    """UPDATE modules
                       SET category = ?, name = ?, summary = ?, scope_desc = ?, capabilities = ?, redlines = ?, entry_paths_json = ?, verify_cmd = ?, color = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        category,
                        name,
                        (data.get("summary") if "summary" in data else row["summary"]).strip(),
                        (data.get("scope_desc") if "scope_desc" in data else row["scope_desc"]).strip(),
                        (data.get("capabilities") if "capabilities" in data else row["capabilities"]).strip(),
                        (data.get("redlines") if "redlines" in data else row["redlines"]).strip(),
                        json.dumps(entry_paths, ensure_ascii=False),
                        (data.get("verify_cmd") if "verify_cmd" in data else row["verify_cmd"]).strip(),
                        (data.get("color") if "color" in data else row["color"]).strip(),
                        now,
                        module_id,
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"模块名称重复：{name}")
            seq = self._append_event(conn, row["project_id"], None, actor_id, "module_updated", {"module_id": module_id, "category": category})
            updated = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
            return self._module_row_to_dict(updated), seq

    def sync_preset_modules(self, project_id: int, actor_id: int | None = None) -> tuple[dict[str, int], int]:
        """将 SCAVENGER_PRESET_MODULES 幂等补齐到指定项目，并自动迁移旧 4 个演示模块引用。"""
        legacy_rename = {
            "操作交互": "操作交互与战术标绘",
            "程序化地图与战斗": "载具战斗与数值基底",
            "界面规范与大厅": "全局UI规范与主入口",
            "工作项看板": "工作项看板与多Agent协作",
        }
        now = utc_now_iso()
        added = 0
        updated = 0
        with self._tx() as conn:
            for old_name, new_name in legacy_rename.items():
                old_row = conn.execute("SELECT id FROM modules WHERE project_id = ? AND name = ?", (project_id, old_name)).fetchone()
                new_row = conn.execute("SELECT id FROM modules WHERE project_id = ? AND name = ?", (project_id, new_name)).fetchone()
                if old_row and not new_row:
                    conn.execute("UPDATE modules SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, old_row["id"]))
            for m in SCAVENGER_PRESET_MODULES:
                existing = conn.execute("SELECT id FROM modules WHERE project_id = ? AND name = ?", (project_id, m["name"])).fetchone()
                ep_json = json.dumps(normalize_scope_paths(m["entry_paths"]), ensure_ascii=False)
                if existing:
                    conn.execute(
                        """UPDATE modules
                           SET category = ?, summary = ?, scope_desc = ?, capabilities = ?, redlines = ?, entry_paths_json = ?, verify_cmd = ?, color = ?, updated_at = ?
                           WHERE id = ?""",
                        (m["category"], m["summary"], m["scope_desc"], m["capabilities"], m["redlines"], ep_json, m["verify_cmd"], m["color"], now, existing["id"]),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """INSERT INTO modules(project_id, category, name, summary, scope_desc, capabilities, redlines, entry_paths_json, verify_cmd, color, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (project_id, m["category"], m["name"], m["summary"], m["scope_desc"], m["capabilities"], m["redlines"], ep_json, m["verify_cmd"], m["color"], now, now),
                    )
                    added += 1
            seq = self._append_event(conn, project_id, None, actor_id, "modules_preset_synced", {"added": added, "updated": updated})
        return {"added": added, "updated": updated}, seq

    def delete_module(self, module_id: int, actor_id: int | None) -> int:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
            if not row:
                raise NotFoundError(f"模块不存在：{module_id}")
            conn.execute("DELETE FROM modules WHERE id = ?", (module_id,))
            return self._append_event(conn, row["project_id"], None, actor_id, "module_deleted", {"module_id": module_id})

    def list_labels(self, project_id: int) -> list[dict[str, Any]]:
        with self._ro() as conn:
            rows = conn.execute("SELECT * FROM labels WHERE project_id = ? ORDER BY id ASC", (project_id,)).fetchall()
            return [{"id": r["id"], "project_id": r["project_id"], "name": r["name"], "color": r["color"]} for r in rows]

    def create_label(self, project_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("标签名不能为空")
        color = (data.get("color") or "#64748b").strip()
        with self._tx() as conn:
            try:
                cur = conn.execute("INSERT INTO labels(project_id, name, color) VALUES (?, ?, ?)", (project_id, name, color))
            except sqlite3.IntegrityError:
                raise ValidationError(f"标签已存在：{name}")
            lid = int(cur.lastrowid)
            seq = self._append_event(conn, project_id, None, actor_id, "label_created", {"label_id": lid, "name": name})
            return {"id": lid, "project_id": project_id, "name": name, "color": color}, seq

    # ---------- 工作项 CRUD、状态流转与冲突检测 ----------

    def _resolve_parent_id(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        raw_parent_ref: Any,
        *,
        self_item_id: int | None = None,
    ) -> int | None:
        if raw_parent_ref in (None, "", 0, "0"):
            return None
        if isinstance(raw_parent_ref, int) or (isinstance(raw_parent_ref, str) and raw_parent_ref.strip().isdigit()):
            prow = conn.execute("SELECT id, project_id, item_key, parent_id FROM work_items WHERE id = ?", (int(str(raw_parent_ref).strip()),)).fetchone()
        else:
            prow = conn.execute(
                "SELECT id, project_id, item_key, parent_id FROM work_items WHERE UPPER(item_key) = UPPER(?)",
                (str(raw_parent_ref).strip(),),
            ).fetchone()
        if not prow:
            raise ValidationError(f"父工作项不存在：{raw_parent_ref}")
        if int(prow["project_id"]) != int(project_id):
            raise ValidationError(f"父工作项 {prow['item_key']} 与当前工作项不属于同一项目")
        if self_item_id is not None and int(prow["id"]) == int(self_item_id):
            raise ValidationError("工作项不能将自己设为父工作项")
        if prow["parent_id"] is not None:
            raise ValidationError(f"{prow['item_key']} 本身已是子工作项，看板限定 2 层结构（父工作项 → 子工作项），请关联至顶层父工作项")
        if self_item_id is not None:
            child_cnt = conn.execute("SELECT COUNT(*) AS c FROM work_items WHERE parent_id = ?", (self_item_id,)).fetchone()["c"]
            if child_cnt > 0:
                raise ValidationError(f"当前工作项已包含 {child_cnt} 个子工作项，不可再挂载为其他工作项的子项（限定 2 层结构）")
        return int(prow["id"])

    def _hydrate_items(self, conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        ph = ",".join("?" for _ in ids)
        label_map: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
        for lr in conn.execute(
            f"""SELECT wil.work_item_id, l.id, l.name, l.color
                FROM work_item_labels wil JOIN labels l ON l.id = wil.label_id
                WHERE wil.work_item_id IN ({ph}) ORDER BY l.id ASC""",
            ids,
        ).fetchall():
            label_map[lr["work_item_id"]].append({"id": lr["id"], "name": lr["name"], "color": lr["color"]})

        att_counts: dict[int, int] = {}
        for ar in conn.execute(f"SELECT work_item_id, COUNT(*) AS c FROM attachments WHERE work_item_id IN ({ph}) GROUP BY work_item_id", ids).fetchall():
            att_counts[ar["work_item_id"]] = ar["c"]

        cmt_counts: dict[int, int] = {}
        for cr in conn.execute(f"SELECT work_item_id, COUNT(*) AS c FROM comments WHERE work_item_id IN ({ph}) GROUP BY work_item_id", ids).fetchall():
            cmt_counts[cr["work_item_id"]] = cr["c"]

        # 批量加载父工作项摘要与子工作项完成度统计
        parent_ids = sorted({int(r["parent_id"]) for r in rows if r["parent_id"]})
        parent_map: dict[int, dict[str, Any]] = {}
        if parent_ids:
            pph = ",".join("?" for _ in parent_ids)
            for pr in conn.execute(
                f"SELECT id, item_key, title, state FROM work_items WHERE id IN ({pph})",
                parent_ids,
            ).fetchall():
                parent_map[int(pr["id"])] = {
                    "id": pr["id"],
                    "item_key": pr["item_key"],
                    "title": pr["title"],
                    "state": pr["state"],
                }

        sub_stats: dict[int, tuple[int, int]] = {}
        for sr in conn.execute(
            f"""SELECT parent_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done_c
                FROM work_items
                WHERE parent_id IN ({ph})
                GROUP BY parent_id""",
            ids,
        ).fetchall():
            sub_stats[int(sr["parent_id"])] = (int(sr["total"]), int(sr["done_c"] or 0))

        # 批量统计未完成前置阻塞依赖数（用于在看板卡片上直接标出阻塞微胶囊）
        parsed_blockers: dict[int, list[str]] = {}
        all_blocker_keys: set[str] = set()
        for r in rows:
            blks = json.loads(r["blocked_by_json"] or "[]")
            parsed_blockers[r["id"]] = blks
            for k in blks:
                if str(k).strip():
                    all_blocker_keys.add(str(k).strip().upper())

        blocker_state_map: dict[str, str] = {}
        if all_blocker_keys:
            bkeys = sorted(all_blocker_keys)
            bph = ",".join("?" for _ in bkeys)
            for br in conn.execute(
                f"SELECT UPPER(item_key) AS k, state FROM work_items WHERE UPPER(item_key) IN ({bph})",
                bkeys,
            ).fetchall():
                blocker_state_map[br["k"]] = br["state"]

        result: list[dict[str, Any]] = []
        for r in rows:
            pid_val = int(r["parent_id"]) if r["parent_id"] else None
            pinfo = parent_map.get(pid_val) if pid_val else None
            st_total, st_done = sub_stats.get(r["id"], (0, 0))
            blks = parsed_blockers.get(r["id"], [])
            blocked_open = sum(1 for k in blks if blocker_state_map.get(str(k).upper(), "open") != "done")
            result.append({
                "id": r["id"],
                "project_id": r["project_id"],
                "seq_num": r["seq_num"],
                "item_key": r["item_key"],
                "title": r["title"],
                "description_md": r["description_md"],
                "plan_md": r["plan_md"],
                "state": r["state"],
                "priority": r["priority"],
                "parent_id": pid_val,
                "parent_key": pinfo["item_key"] if pinfo else "",
                "parent_title": pinfo["title"] if pinfo else "",
                "parent_state": pinfo["state"] if pinfo else "",
                "sub_total": st_total,
                "sub_done": st_done,
                "module_id": r["module_id"],
                "owner_id": r["owner_id"],
                "agent_id": r["agent_id"],
                "agent_host": r["agent_host"],
                "agent_branch": r["agent_branch"],
                "agent_heartbeat_at": r["agent_heartbeat_at"],
                "scope_paths": json.loads(r["scope_paths_json"] or "[]"),
                "blocked_by": blks,
                "blocked_open_count": blocked_open,
                "labels": label_map.get(r["id"], []),
                "label_ids": [lb["id"] for lb in label_map.get(r["id"], [])],
                "attachment_count": att_counts.get(r["id"], 0),
                "comment_count": cmt_counts.get(r["id"], 0),
                "sort_order": r["sort_order"],
                "rev": r["rev"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return result

    def list_items(self, project_id: int) -> dict[str, Any]:
        with self._ro() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE project_id = ? ORDER BY sort_order ASC, id ASC",
                (project_id,),
            ).fetchall()
            items = self._hydrate_items(conn, rows)
            by_item, pairs = compute_scope_conflicts(items)
            for it in items:
                it["conflicts"] = by_item.get(it["id"], [])
            return {"items": items, "conflict_pairs": pairs}

    def _find_item_row(self, conn: sqlite3.Connection, item_ref: int | str) -> sqlite3.Row:
        if isinstance(item_ref, int) or (isinstance(item_ref, str) and item_ref.isdigit()):
            row = conn.execute("SELECT * FROM work_items WHERE id = ?", (int(item_ref),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM work_items WHERE UPPER(item_key) = UPPER(?)", (str(item_ref).strip(),)).fetchone()
        if not row:
            raise NotFoundError(f"工作项不存在：{item_ref}")
        return row

    def get_item_detail(self, item_ref: int | str) -> dict[str, Any]:
        with self._ro() as conn:
            row = self._find_item_row(conn, item_ref)
            item = self._hydrate_items(conn, [row])[0]
            # 计算同项目冲突与模块映射
            all_rows = conn.execute(
                "SELECT * FROM work_items WHERE project_id = ? ORDER BY sort_order ASC, id ASC",
                (item["project_id"],),
            ).fetchall()
            all_items = self._hydrate_items(conn, all_rows)
            by_item, _ = compute_scope_conflicts(all_items)
            item["conflicts"] = by_item.get(item["id"], [])

            mod_rows = conn.execute("SELECT id, name, color, category FROM modules WHERE project_id = ?", (item["project_id"],)).fetchall()
            mod_meta = {int(mr["id"]): {"name": mr["name"], "color": mr["color"], "category": mr["category"]} for mr in mod_rows}

            # 模块上下文包
            item["module_context"] = None
            if item["module_id"]:
                mrow = conn.execute("SELECT * FROM modules WHERE id = ?", (item["module_id"],)).fetchone()
                if mrow:
                    item["module_context"] = self._module_row_to_dict(mrow)

            # 父工作项详情（若为子单，向抽屉与 Agent pull 透出父单总目标）
            item["parent_detail"] = None
            if item["parent_id"]:
                p_match = next((x for x in all_items if x["id"] == item["parent_id"]), None)
                if p_match:
                    item["parent_detail"] = {
                        "id": p_match["id"],
                        "item_key": p_match["item_key"],
                        "title": p_match["title"],
                        "state": p_match["state"],
                        "priority": p_match["priority"],
                        "sub_total": p_match["sub_total"],
                        "sub_done": p_match["sub_done"],
                        "description_md": p_match["description_md"],
                    }

            # 子工作项列表（若为父单）与同父兄弟子项（若为子单，供并行 Agent 对齐接口与路径边界）
            def _brief_rel(x: dict[str, Any]) -> dict[str, Any]:
                mm = mod_meta.get(x["module_id"]) if x.get("module_id") else None
                return {
                    "id": x["id"],
                    "item_key": x["item_key"],
                    "title": x["title"],
                    "state": x["state"],
                    "priority": x["priority"],
                    "module_id": x["module_id"],
                    "module_name": mm["name"] if mm else "",
                    "module_color": mm["color"] if mm else "#64748b",
                    "agent_id": x["agent_id"],
                    "scope_paths": x["scope_paths"],
                    "blocked_by": x["blocked_by"],
                    "blocked_open_count": x["blocked_open_count"],
                    "has_conflict": bool(by_item.get(x["id"])),
                }

            item["children"] = [_brief_rel(x) for x in all_items if x.get("parent_id") == item["id"]]
            item["siblings"] = (
                [_brief_rel(x) for x in all_items if x.get("parent_id") == item["parent_id"] and x["id"] != item["id"]]
                if item["parent_id"]
                else []
            )

            # 前置依赖摘要与下游被阻塞摘要
            blocked_details = []
            for key in item["blocked_by"]:
                brow = conn.execute("SELECT id, item_key, title, state, owner_id FROM work_items WHERE UPPER(item_key) = UPPER(?)", (str(key),)).fetchone()
                if brow:
                    blocked_details.append({"id": brow["id"], "item_key": brow["item_key"], "title": brow["title"], "state": brow["state"]})
            item["blocked_by_details"] = blocked_details

            my_key_up = item["item_key"].upper()
            item["blocks_details"] = [
                {"id": x["id"], "item_key": x["item_key"], "title": x["title"], "state": x["state"]}
                for x in all_items
                if x["id"] != item["id"] and any(str(k).upper() == my_key_up for k in (x.get("blocked_by") or []))
            ]

            # 评论
            cmts = conn.execute(
                """SELECT c.*, m.display_name AS author_name, m.kind AS author_kind, m.avatar_color AS author_color
                   FROM comments c LEFT JOIN members m ON m.id = c.author_id
                   WHERE c.work_item_id = ? ORDER BY c.created_at ASC, c.id ASC""",
                (item["id"],),
            ).fetchall()
            item["comments"] = [
                {
                    "id": c["id"],
                    "author_id": c["author_id"],
                    "author_name": c["author_name"] or "系统",
                    "author_kind": c["author_kind"] or "human",
                    "author_color": c["author_color"] or "#64748b",
                    "body_md": c["body_md"],
                    "comment_type": c["comment_type"],
                    "created_at": c["created_at"],
                }
                for c in cmts
            ]

            # 附件
            atts = conn.execute(
                "SELECT * FROM attachments WHERE work_item_id = ? ORDER BY created_at DESC, id DESC",
                (item["id"],),
            ).fetchall()
            item["attachments"] = [
                {
                    "id": a["id"],
                    "sha256": a["sha256"],
                    "ext": a["ext"],
                    "filename": a["filename"],
                    "mime_type": a["mime_type"],
                    "size_bytes": a["size_bytes"],
                    "url": f"/api/v1/attachments/{a['sha256']}{a['ext']}",
                    "uploaded_by": a["uploaded_by"],
                    "created_at": a["created_at"],
                }
                for a in atts
            ]
            return item

    def create_item(self, project_id: int, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValidationError("工作项标题不能为空")
        state = (data.get("state") or "todo").strip()
        if state not in VALID_STATE_KEYS:
            raise ValidationError(f"非法状态：{state}")
        priority = (data.get("priority") or "medium").strip()
        if priority not in VALID_PRIORITY_KEYS:
            raise ValidationError(f"非法优先级：{priority}")
        scope_paths = normalize_scope_paths(data.get("scope_paths"))
        blocked_by = [str(k).strip().upper() for k in (data.get("blocked_by") or []) if str(k).strip()]
        now = utc_now_iso()

        with self._tx() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not proj:
                raise NotFoundError(f"项目不存在：{project_id}")
            seq_num = int(proj["next_seq"])
            item_key = f"{proj['key']}-{seq_num}"
            conn.execute("UPDATE projects SET next_seq = ?, updated_at = ? WHERE id = ?", (seq_num + 1, now, project_id))

            max_row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS mx FROM work_items WHERE project_id = ? AND state = ?", (project_id, state)).fetchone()
            sort_order = float(max_row["mx"] or 0.0) + 1000.0

            owner_id = data.get("owner_id")
            if owner_id is None:
                owner_id = actor_id
            agent_id = data.get("agent_id")
            module_id = data.get("module_id")
            raw_parent = data.get("parent_id") if "parent_id" in data else data.get("parent_key")
            parent_id = self._resolve_parent_id(conn, project_id, raw_parent)

            cur = conn.execute(
                """INSERT INTO work_items(
                       project_id, seq_num, item_key, title, description_md, plan_md, state, priority,
                       parent_id, module_id, owner_id, agent_id, agent_host, agent_branch, agent_heartbeat_at,
                       scope_paths_json, blocked_by_json, sort_order, rev, created_by, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    project_id,
                    seq_num,
                    item_key,
                    title,
                    (data.get("description_md") or "").strip(),
                    (data.get("plan_md") or "").strip(),
                    state,
                    priority,
                    parent_id,
                    int(module_id) if module_id else None,
                    int(owner_id) if owner_id else None,
                    int(agent_id) if agent_id else None,
                    (data.get("agent_host") or "").strip(),
                    (data.get("agent_branch") or "").strip(),
                    (data.get("agent_heartbeat_at") or "").strip(),
                    json.dumps(scope_paths, ensure_ascii=False),
                    json.dumps(blocked_by, ensure_ascii=False),
                    sort_order,
                    actor_id,
                    now,
                    now,
                ),
            )
            wid = int(cur.lastrowid)
            for lid in data.get("label_ids") or []:
                conn.execute("INSERT OR IGNORE INTO work_item_labels(work_item_id, label_id) VALUES (?, ?)", (wid, int(lid)))
            seq = self._append_event(conn, project_id, wid, actor_id, "item_created", {"item_key": item_key, "state": state, "parent_id": parent_id})
        return self.get_item_detail(wid), seq

    def update_item(self, item_ref: int | str, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        with self._tx() as conn:
            row = self._find_item_row(conn, item_ref)
            current = self._hydrate_items(conn, [row])[0]
            expected_rev = data.get("expected_rev")
            if expected_rev is not None and int(expected_rev) != int(current["rev"]):
                raise ConflictError(
                    f"工作项 {current['item_key']} 已被其他会话更新（当前 rev={current['rev']}，请求 expected_rev={expected_rev}），请刷新后重试",
                    payload={"latest": current},
                )

            proj = conn.execute("SELECT * FROM projects WHERE id = ?", (current["project_id"],)).fetchone()
            title = (data.get("title") if "title" in data else current["title"]).strip()
            if not title:
                raise ValidationError("工作项标题不能为空")
            new_state = (data.get("state") if "state" in data else current["state"]).strip()
            if new_state not in VALID_STATE_KEYS:
                raise ValidationError(f"非法状态：{new_state}")
            priority = (data.get("priority") if "priority" in data else current["priority"]).strip()
            if priority not in VALID_PRIORITY_KEYS:
                raise ValidationError(f"非法优先级：{priority}")

            reason = (data.get("reason") or "").strip()
            if proj["strict_mode"] and new_state == "rejected" and current["state"] != "rejected" and not reason:
                raise ValidationError("项目已开启严格模式：拖入「不通过」必须填写打回原因")

            # 父工作项防误结单保护：存在未完成子项时默认拦截，显式传 force=True 放行
            if new_state == "done" and current["state"] != "done" and not bool(data.get("force")):
                open_children = conn.execute(
                    "SELECT item_key, title, state FROM work_items WHERE parent_id = ? AND state != 'done' ORDER BY sort_order ASC, id ASC",
                    (current["id"],),
                ).fetchall()
                if open_children:
                    preview_keys = ", ".join(r["item_key"] for r in open_children[:5])
                    more = f" 等 {len(open_children)} 项" if len(open_children) > 5 else ""
                    raise ValidationError(
                        f"父工作项 {current['item_key']} 尚有 {len(open_children)} 个未完成子项（{preview_keys}{more}），请先完成子项或确认后强制结单",
                        payload={
                            "reason_code": "incomplete_children",
                            "incomplete_children": [
                                {"item_key": r["item_key"], "title": r["title"], "state": r["state"]}
                                for r in open_children
                            ],
                        },
                    )

            scope_paths = normalize_scope_paths(data["scope_paths"]) if "scope_paths" in data else current["scope_paths"]
            if "blocked_by" in data:
                blocked_by = [str(k).strip().upper() for k in (data.get("blocked_by") or []) if str(k).strip() and str(k).strip().upper() != current["item_key"]]
            else:
                blocked_by = current["blocked_by"]

            if "parent_id" in data or "parent_key" in data:
                raw_parent = data.get("parent_id") if "parent_id" in data else data.get("parent_key")
                parent_id = self._resolve_parent_id(conn, current["project_id"], raw_parent, self_item_id=current["id"])
            else:
                parent_id = current["parent_id"]

            def _opt_int(key: str, fallback: int | None) -> int | None:
                if key not in data:
                    return fallback
                val = data.get(key)
                return int(val) if val not in (None, "", 0, "0") else None

            now = utc_now_iso()
            new_rev = int(current["rev"]) + 1
            conn.execute(
                """UPDATE work_items
                   SET title = ?, description_md = ?, plan_md = ?, state = ?, priority = ?,
                       parent_id = ?, module_id = ?, owner_id = ?, agent_id = ?, agent_host = ?, agent_branch = ?,
                       agent_heartbeat_at = ?, scope_paths_json = ?, blocked_by_json = ?,
                       sort_order = ?, rev = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    title,
                    (data.get("description_md") if "description_md" in data else current["description_md"]).strip(),
                    (data.get("plan_md") if "plan_md" in data else current["plan_md"]).strip(),
                    new_state,
                    priority,
                    parent_id,
                    _opt_int("module_id", current["module_id"]),
                    _opt_int("owner_id", current["owner_id"]),
                    _opt_int("agent_id", current["agent_id"]),
                    (data.get("agent_host") if "agent_host" in data else current["agent_host"]).strip(),
                    (data.get("agent_branch") if "agent_branch" in data else current["agent_branch"]).strip(),
                    (data.get("agent_heartbeat_at") if "agent_heartbeat_at" in data else current["agent_heartbeat_at"]).strip(),
                    json.dumps(scope_paths, ensure_ascii=False),
                    json.dumps(blocked_by, ensure_ascii=False),
                    float(data["sort_order"]) if "sort_order" in data and data["sort_order"] is not None else current["sort_order"],
                    new_rev,
                    now,
                    current["id"],
                ),
            )
            if "label_ids" in data:
                conn.execute("DELETE FROM work_item_labels WHERE work_item_id = ?", (current["id"],))
                for lid in data.get("label_ids") or []:
                    conn.execute("INSERT OR IGNORE INTO work_item_labels(work_item_id, label_id) VALUES (?, ?)", (current["id"], int(lid)))

            if new_state != current["state"]:
                state_labels = {s["key"]: s["label"] for s in STATES}
                msg = f"状态流转：{state_labels.get(current['state'], current['state'])} → {state_labels.get(new_state, new_state)}"
                if reason:
                    msg += f"\n\n原因：{reason}"
                ctype = "reject" if new_state == "rejected" else "system"
                conn.execute(
                    "INSERT INTO comments(work_item_id, author_id, body_md, comment_type, created_at) VALUES (?, ?, ?, ?, ?)",
                    (current["id"], actor_id, msg, ctype, now),
                )

            seq = self._append_event(
                conn,
                current["project_id"],
                current["id"],
                actor_id,
                "item_updated",
                {"item_key": current["item_key"], "state": new_state, "rev": new_rev},
            )
        return self.get_item_detail(current["id"]), seq

    def transition_item(self, item_ref: int | str, action: str, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        """人机闸门语义流转：claim / submit_plan / approve_plan / start / submit_qa / start_testing / reject / done。"""
        act = (action or "").strip().lower()
        now = utc_now_iso()
        patch: dict[str, Any] = {}
        if "expected_rev" in data and data["expected_rev"] is not None:
            patch["expected_rev"] = data["expected_rev"]
        note = (data.get("note") or data.get("reason") or "").strip()

        if act == "claim":
            patch["agent_id"] = data.get("agent_id") or actor_id
            if data.get("agent_branch"):
                patch["agent_branch"] = data["agent_branch"]
            if data.get("agent_host"):
                patch["agent_host"] = data["agent_host"]
            if "scope_paths" in data:
                patch["scope_paths"] = data["scope_paths"]
            patch["agent_heartbeat_at"] = now
            if data.get("state"):
                patch["state"] = data["state"]
        elif act == "submit_plan":
            patch["state"] = "plan_review"
            if data.get("plan_md"):
                patch["plan_md"] = data["plan_md"]
            if data.get("agent_branch"):
                patch["agent_branch"] = data["agent_branch"]
            if "scope_paths" in data:
                patch["scope_paths"] = data["scope_paths"]
            patch["agent_heartbeat_at"] = now
        elif act == "approve_plan":
            patch["state"] = "in_progress"
        elif act == "start":
            patch["state"] = "in_progress"
            patch["agent_heartbeat_at"] = now
            if data.get("agent_id"):
                patch["agent_id"] = data["agent_id"]
            if data.get("agent_branch"):
                patch["agent_branch"] = data["agent_branch"]
            if data.get("agent_host"):
                patch["agent_host"] = data["agent_host"]
        elif act == "submit_qa":
            patch["state"] = "qa_wait"
            patch["agent_heartbeat_at"] = now
        elif act == "start_testing":
            patch["state"] = "testing"
        elif act == "reject":
            patch["state"] = "rejected"
            patch["reason"] = note
        elif act == "done":
            patch["state"] = "done"
            if data.get("force"):
                patch["force"] = True
        else:
            raise ValidationError(f"不支持的流转动作：{action}")

        item, seq = self.update_item(item_ref, patch, actor_id)
        if note and act != "reject":
            ctype = "plan" if act == "submit_plan" else ("qa" if act == "submit_qa" else "comment")
            _, seq = self.add_comment(item["id"], note, actor_id, comment_type=ctype)
            item = self.get_item_detail(item["id"])
        return item, seq

    def heartbeat_item(self, item_ref: int | str, data: dict[str, Any], actor_id: int | None) -> tuple[dict[str, Any], int]:
        patch: dict[str, Any] = {"agent_heartbeat_at": utc_now_iso()}
        if data.get("agent_id"):
            patch["agent_id"] = data["agent_id"]
        elif actor_id:
            m = self.get_member_by_id(actor_id)
            if m and m["kind"] == "agent":
                patch["agent_id"] = actor_id
        if data.get("agent_branch"):
            patch["agent_branch"] = data["agent_branch"]
        if data.get("agent_host"):
            patch["agent_host"] = data["agent_host"]
        item, seq = self.update_item(item_ref, patch, actor_id)
        note = (data.get("note") or "").strip()
        if note:
            _, seq = self.add_comment(item["id"], note, actor_id, comment_type="comment")
            item = self.get_item_detail(item["id"])
        return item, seq

    def delete_item(self, item_ref: int | str, actor_id: int | None) -> int:
        with self._tx() as conn:
            row = self._find_item_row(conn, item_ref)
            conn.execute("DELETE FROM work_items WHERE id = ?", (row["id"],))
            return self._append_event(conn, row["project_id"], row["id"], actor_id, "item_deleted", {"item_key": row["item_key"]})

    # ---------- 评论与附件 ----------

    def add_comment(self, item_ref: int | str, body_md: str, actor_id: int | None, *, comment_type: str = "comment") -> tuple[dict[str, Any], int]:
        text = (body_md or "").strip()
        if not text:
            raise ValidationError("评论内容不能为空")
        now = utc_now_iso()
        with self._tx() as conn:
            row = self._find_item_row(conn, item_ref)
            cur = conn.execute(
                "INSERT INTO comments(work_item_id, author_id, body_md, comment_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (row["id"], actor_id, text, comment_type, now),
            )
            cid = int(cur.lastrowid)
            conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (now, row["id"]))
            seq = self._append_event(conn, row["project_id"], row["id"], actor_id, "comment_added", {"item_key": row["item_key"], "comment_id": cid})
            crow = conn.execute(
                """SELECT c.*, m.display_name AS author_name, m.kind AS author_kind, m.avatar_color AS author_color
                   FROM comments c LEFT JOIN members m ON m.id = c.author_id WHERE c.id = ?""",
                (cid,),
            ).fetchone()
            return (
                {
                    "id": crow["id"],
                    "author_id": crow["author_id"],
                    "author_name": crow["author_name"] or "系统",
                    "author_kind": crow["author_kind"] or "human",
                    "author_color": crow["author_color"] or "#64748b",
                    "body_md": crow["body_md"],
                    "comment_type": crow["comment_type"],
                    "created_at": crow["created_at"],
                },
                seq,
            )

    def add_attachment(
        self,
        item_ref: int | str,
        *,
        sha256: str,
        ext: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        actor_id: int | None,
    ) -> tuple[dict[str, Any], int]:
        now = utc_now_iso()
        with self._tx() as conn:
            row = self._find_item_row(conn, item_ref)
            cur = conn.execute(
                """INSERT INTO attachments(work_item_id, sha256, ext, filename, mime_type, size_bytes, uploaded_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], sha256, ext, filename, mime_type, size_bytes, actor_id, now),
            )
            aid = int(cur.lastrowid)
            conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (now, row["id"]))
            seq = self._append_event(conn, row["project_id"], row["id"], actor_id, "attachment_added", {"item_key": row["item_key"], "attachment_id": aid})
            return (
                {
                    "id": aid,
                    "sha256": sha256,
                    "ext": ext,
                    "filename": filename,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    "url": f"/api/v1/attachments/{sha256}{ext}",
                    "uploaded_by": actor_id,
                    "created_at": now,
                },
                seq,
            )

    def latest_event_seq(self) -> int:
        with self._ro() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM activity_events").fetchone()
            return int(row["m"] or 0)
