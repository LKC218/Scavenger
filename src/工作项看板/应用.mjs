/**
 * 工作任务看板前端主控 V003（全面对齐 /ui-spec Code SSOT V2.2：A3 烟炭双金 × B2 微描边 × C4 外柔内锐）
 */

import { bindAllUiSelects, createEscStack } from "/ui/交互控件.mjs";
import { createBrandLogoSvg } from "/ui/图标与标绘.mjs";

const escStack = createEscStack();
const selectBinders = { login: null, drawer: null, modal: null };

const DEFAULT_DISPLAY = {
  layout: "board", // board | list
  groupBy: "state", // state | module | priority | parent
  autoCollapseEmpty: true,
  compact: false,
  topLevelOnly: false,
  fields: {
    priority: true,
    module: true,
    labels: true,
    owner: true,
    agent: true,
    subProgress: true,
    scopeCount: false,
    statePill: false,
  },
};

function loadDisplayPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem("lb_display_v2") || "null");
    if (!raw || typeof raw !== "object") return structuredClone(DEFAULT_DISPLAY);
    return {
      layout: raw.layout === "list" ? "list" : "board",
      groupBy: ["state", "module", "priority", "parent"].includes(raw.groupBy) ? raw.groupBy : "state",
      autoCollapseEmpty: raw.autoCollapseEmpty !== false,
      compact: Boolean(raw.compact),
      topLevelOnly: Boolean(raw.topLevelOnly),
      fields: { ...DEFAULT_DISPLAY.fields, ...(raw.fields || {}) },
    };
  } catch {
    return structuredClone(DEFAULT_DISPLAY);
  }
}

function saveDisplayPrefs() {
  localStorage.setItem("lb_display_v2", JSON.stringify(state.display));
}

const state = {
  service: null,
  authMode: "local_single",
  currentMember: null,
  actorOverrideId: Number(localStorage.getItem("lb_actor_id") || "0") || null,
  states: [],
  priorities: [],
  moduleCategories: [],
  projects: [],
  members: [],
  activeProjectId: Number(localStorage.getItem("lb_project_id") || "0") || null,
  activeProject: null,
  modules: [],
  labels: [],
  items: [],
  conflictPairs: [],
  activeView: "items", // items | modules | agents | settings
  activeModuleTab: "",
  sidebarCollapsed: localStorage.getItem("lb_sidebar_collapsed") === "1",
  display: loadDisplayPrefs(),
  manualCollapsedCols: new Set(),
  manualExpandedCols: new Set(),
  openPopover: null, // "actor" | "filter" | "display" | null
  filters: {
    q: "",
    stateKey: "",
    priority: "",
    memberId: "",
    category: "",
    moduleId: null,
    parentId: null,
    conflictOnly: false,
  },
  openItemKey: null,
  openItemDetail: null,
  drawerBlockers: [],
  sse: null,
  dragItemKey: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safeColor(color, fallback = "#64748b") {
  const s = String(color ?? "").trim();
  return /^#[0-9a-fA-F]{3,8}$/.test(s) ? s : fallback;
}

function safeAttachmentUrl(url) {
  const s = String(url ?? "").trim();
  return /^\/api\/v1\/attachments\/[a-zA-Z0-9._-]+$/.test(s) ? s : "";
}

// ---------- 1.5px 线性 SVG 图标库 ----------

const SVG_ICONS = {
  items: `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><rect x="2.2" y="2.5" width="11.6" height="11" rx="2"/><line x1="5.2" y1="6" x2="10.8" y2="6"/><line x1="5.2" y1="9.5" x2="9" y2="9.5"/></svg>`,
  modules: `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/></svg>`,
  settings: `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="2.3"/><path d="M8 2.2v1.3M8 12.5v1.3M2.2 8h1.3M12.5 8h1.3M3.9 3.9l.9.9M11.2 11.2l.9.9M12.1 3.9l-.9.9M4.8 11.2l-.9.9"/></svg>`,
  collapseCol: `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 4 2.5 4 2.5 7.5"/><line x1="2.5" y1="4" x2="6.5" y2="8"/><polyline points="10 12 13.5 12 13.5 8.5"/><line x1="13.5" y1="12" x2="9.5" y2="8"/></svg>`,
  plus: `<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><line x1="8" y1="3" x2="8" y2="13"/><line x1="3" y1="8" x2="13" y2="8"/></svg>`,
  copy: `<svg viewBox="0 0 16 16" width="12.5" height="12.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><rect x="5.5" y="5.5" width="8" height="8" rx="1.4"/><path d="M10.5 5.5V3.9A1.4 1.4 0 0 0 9.1 2.5H3.9A1.4 1.4 0 0 0 2.5 3.9v5.2a1.4 1.4 0 0 0 1.4 1.4h1.6"/></svg>`,
  moduleSm: `<svg viewBox="0 0 16 16" width="11.5" height="11.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><rect x="2.5" y="2.5" width="4.3" height="4.3" rx="0.9"/><rect x="9.2" y="2.5" width="4.3" height="4.3" rx="0.9"/><rect x="2.5" y="9.2" width="4.3" height="4.3" rx="0.9"/><rect x="9.2" y="9.2" width="4.3" height="4.3" rx="0.9"/></svg>`,
  warn: `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.4 14 13H2L8 2.4z"/><line x1="8" y1="6.4" x2="8" y2="9.4"/><circle cx="8" cy="11.3" r="0.6" fill="currentColor" stroke="none"/></svg>`,
  attach: `<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M11.6 7.4 6.9 12a2.6 2.6 0 0 1-3.7-3.7l5.4-5.4a1.7 1.7 0 0 1 2.4 2.4l-5.3 5.3a.85.85 0 0 1-1.2-1.2L9.3 4.6"/></svg>`,
  comment: `<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M2.8 3.5h10.4a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H6.5l-3 2.2v-2.2h-.7a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z"/></svg>`,
  folder: `<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M2.2 4.2A1.2 1.2 0 0 1 3.4 3h2.8l1.3 1.5h5.1a1.2 1.2 0 0 1 1.2 1.2v6.1a1.2 1.2 0 0 1-1.2 1.2H3.4a1.2 1.2 0 0 1-1.2-1.2V4.2z"/></svg>`,
};

function renderStateIcon(stateKey, color = "#989ca6", size = 14) {
  const c = esc(color);
  switch (stateKey) {
    case "backlog":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6" stroke-dasharray="2.2 2.2"><circle cx="8" cy="8" r="5.5"/></svg>`;
    case "todo":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6"><circle cx="8" cy="8" r="5.5"/></svg>`;
    case "plan_review":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.2 13 4.3v3.6c0 2.9-2 5-5 5.9-3-.9-5-3-5-5.9V4.3L8 2.2z"/><circle cx="8" cy="7.8" r="1.4" fill="${c}" stroke="none"/></svg>`;
    case "in_progress":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none"><circle cx="8" cy="8" r="5.5" stroke="${c}" stroke-width="1.6"/><path d="M8 4.2A3.8 3.8 0 0 1 11.8 8 3.8 3.8 0 0 1 8 11.8V4.2z" fill="${c}"/></svg>`;
    case "qa_wait":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2.1 1.4"/></svg>`;
    case "testing":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6"><circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="2.2" fill="${c}" stroke="none"/></svg>`;
    case "rejected":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round"><circle cx="8" cy="8" r="5.5"/><line x1="5.8" y1="5.8" x2="10.2" y2="10.2"/><line x1="10.2" y1="5.8" x2="5.8" y2="10.2"/></svg>`;
    case "done":
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none"><circle cx="8" cy="8" r="6" fill="${c}"/><polyline points="5.3 8.2 7.2 10.1 10.9 6.2" stroke="#0d0e10" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    default:
      return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none"><circle cx="8" cy="8" r="4.5" fill="${c}"/></svg>`;
  }
}

function renderPriorityIcon(prioKey, color = "#989ca6", size = 12) {
  const c = esc(color);
  const dim = "rgba(255,255,255,0.18)";
  if (prioKey === "urgent") {
    return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none"><rect x="2" y="2" width="12" height="12" rx="3" fill="${c}"/><line x1="8" y1="5" x2="8" y2="8.8" stroke="#0d0e10" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11.2" r="0.95" fill="#0d0e10"/></svg>`;
  }
  if (prioKey === "none") {
    return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="${c}" stroke-width="1.7" stroke-linecap="round"><line x1="3.5" y1="8" x2="12.5" y2="8"/></svg>`;
  }
  const activeBars = prioKey === "high" ? 3 : prioKey === "medium" ? 2 : 1;
  return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none">
    <rect x="2.2" y="9.5" width="2.7" height="4.2" rx="0.8" fill="${activeBars >= 1 ? c : dim}"/>
    <rect x="6.6" y="6.2" width="2.7" height="7.5" rx="0.8" fill="${activeBars >= 2 ? c : dim}"/>
    <rect x="11" y="2.8" width="2.7" height="10.9" rx="0.8" fill="${activeBars >= 3 ? c : dim}"/>
  </svg>`;
}

// ---------- 时间与基础工具 ----------

function fmtBeijingTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const map = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return `${map.month}-${map.day} ${map.hour}:${map.minute}`;
}

function fmtRelativeAge(iso) {
  if (!iso) return { text: "无心跳", fresh: false };
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return { text: "刚刚", fresh: true };
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return { text: "刚刚", fresh: true };
  const min = Math.floor(sec / 60);
  if (min < 20) return { text: `${min}m前`, fresh: true };
  if (min < 60) return { text: `${min}m前`, fresh: false };
  const hr = Math.floor(min / 60);
  if (hr < 24) return { text: `${hr}h前`, fresh: false };
  return { text: `${Math.floor(hr / 24)}d前`, fresh: false };
}

function toast(msg, kind = "info") {
  const stack = $("#toast-stack");
  const el = document.createElement("div");
  el.className = `toast is-${kind}`;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 3400);
}

async function copyText(text, okMsg = "已复制到剪贴板") {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg, "success");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast(okMsg, "success");
  }
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.authMode === "local_single" && state.actorOverrideId) {
    headers["X-Actor-Id"] = String(state.actorOverrideId);
  }
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, {
    ...opts,
    headers,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  const data = await resp.json().catch(() => ({ ok: false, message: `HTTP ${resp.status}` }));
  if (resp.status === 401 && state.authMode === "lan_pin") {
    showLoginOverlay();
    throw new Error(data.message || "需要登录");
  }
  if (!resp.ok) {
    const err = new Error(data.message || `请求失败 (${resp.status})`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

function memberById(id) {
  return state.members.find((m) => m.id === id) || null;
}
function moduleById(id) {
  return state.modules.find((m) => m.id === id) || null;
}
function priorityMeta(key) {
  return state.priorities.find((p) => p.key === key) || { key: "none", label: "无", color: "#64748b" };
}
function stateMeta(key) {
  return state.states.find((s) => s.key === key) || { key, label: key, color: "#64748b", gate: false };
}

const ITEM_SPEC_TEMPLATES = [
  {
    key: "feature",
    prefix: "[需求]",
    name: "需求",
    color: "#3b82f6",
    defaultLabel: "功能需求",
    titleHint: "[需求] 对象/场景 + 新增核心能力（≤ 32 字）",
    body: [
      "### 背景与目标",
      "<1~2 句话说明痛点、玩法价值或关联的 G00–G08 目标>",
      "",
      "### 核心变更",
      "- <外部可见的界面、交互或规则变化 1>",
      "- <外部可见的界面、交互或规则变化 2>",
      "",
      "### 架构约束",
      "- <按需填写：如严格遵循 680p 基线 / ba-source-ssot-v1 / Code SSOT V2.2 / 8800 单端口>",
      "",
      "### 验收标准",
      "- [ ] <可客观验证的检查项 1>",
      "- [ ] <可客观验证的检查项 2>",
    ].join("\n"),
  },
  {
    key: "bug",
    prefix: "[缺陷]",
    name: "缺陷",
    color: "#ef4444",
    defaultLabel: "缺陷修复",
    titleHint: "[缺陷] 触发条件 + 异常现象（≤ 32 字）",
    body: [
      "### 触发场景",
      "- 路由 / 模式：<如 /play 9km 实战 · 21:9 680p>",
      "- 前置条件：<如 编组 3 辆 M1A2 + 1 架 A-10C>",
      "",
      "### 复现步骤",
      "1. <操作步骤 1>",
      "2. <操作步骤 2>",
      "",
      "### 实际表现",
      "<1~2 句话描述异常现象，截图建卡后在抽屉 Ctrl+V 粘贴上传>",
      "",
      "### 预期表现",
      "<1~2 句话描述正确行为，直接作为验收标准>",
    ].join("\n"),
  },
  {
    key: "opt",
    prefix: "[优化]",
    name: "优化",
    color: "#14b8a6",
    defaultLabel: "性能优化",
    titleHint: "[优化] 目标模块 + 体验或性能提升点（≤ 32 字）",
    body: [
      "### 背景与目标",
      "<1~2 句话说明当前瓶颈、体验痛点与预期量化提升>",
      "",
      "### 核心变更",
      "- <优化项 1>",
      "- <优化项 2>",
      "",
      "### 验收标准",
      "- [ ] <帧率 / 耗时 / 视觉 / 交互量化或实测验证标准>",
      "- [ ] <现有功能零回归>",
    ].join("\n"),
  },
  {
    key: "task",
    prefix: "[任务]",
    name: "任务",
    color: "#8b5cf6",
    defaultLabel: "AI协作",
    titleHint: "[任务] 工程/工具/文档目标（≤ 32 字）",
    body: [
      "### 任务目标",
      "<1~2 句话说明要达成的工程、脚本或文档治理目标>",
      "",
      "### 执行范围",
      "- <明确纳入范围与产出落点>",
      "- <明确不改动范围，防越界>",
      "",
      "### 完成标准",
      "- [ ] <可运行的校验命令或门禁通过，如 scripts/检查项目结构.py>",
    ].join("\n"),
  },
  {
    key: "asset",
    prefix: "[资产]",
    name: "资产",
    color: "#06b6d4",
    defaultLabel: "资产管线",
    titleHint: "[资产] 型号/资源名 + 提取或编译目标（≤ 32 字）",
    body: [
      "### 资产对象与来源",
      "- 目标资产：<型号 / 稳定 ID / 批次>",
      "- 输入来源：<只读来源或资料库 ID，严禁写本机绝对路径>",
      "",
      "### 交付规格",
      "- <LOD / 贴图档位 / 声道规格 / IoU 或帧率基准指标>",
      "",
      "### 验收与退役要求",
      "- [ ] <视觉六视角 / 听感 / 引擎实机验收项>",
      "- [ ] <涉及旧版替换时：登记清理工具 stage/accept 7 天退役>",
    ].join("\n"),
  },
];

function countSpecEffectiveChars(text) {
  return String(text || "")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/^\s*[-*+]\s*(\[[ xX]\]\s*)?/gm, "")
    .replace(/\s+/g, "").length;
}

function buildAgentPrompt(item) {
  const mod = moduleById(item.module_id);
  const modHint = mod ? `（所属模块：${mod.name}）` : "";
  return [
    `请按项目级技能 workboard-item-exec 处理本地工作项 ${item.item_key}：${item.title}${modHint}。`,
    `1. 先运行 \`python -X utf8 -B scripts/工作项看板/看板工具.py pull ${item.item_key}\` 读取完整需求、附件、模块红线与路径冲突预警；`,
    `2. 登记本轮清理保护并认领提交方案（\`claim\` + \`submit-plan --plan-file\`）停在「方案待审」，等待我确认后再动手改码；`,
    `3. 获批实施完成后跑通模块验证命令并调用 \`submit-qa ${item.item_key}\` 回写测试说明。`,
  ].join("\n");
}

// ---------- Code SSOT V2.2 定制战术下拉工厂（全面取代浏览器原生下拉控件） ----------

const UI_SELECT_CHEV_SVG = `<svg class="ui-select-chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><polyline points="4 6 8 10 12 6"/></svg>`;
const UI_SELECT_CHECK_SVG = `<svg class="ui-select-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3.5 8.5 6.8 11.6 12.6 4.8"/></svg>`;

function buildUiSelectHtml({ id, value = "", options = [], groups = null, disabled = false, placeholder = "请选择" }) {
  const flatOpts = [];
  if (Array.isArray(groups)) {
    for (const g of groups) {
      for (const o of g.options || []) flatOpts.push(o);
    }
  } else {
    for (const o of options) flatOpts.push(o);
  }
  const strVal = value == null ? "" : String(value);
  const selected = flatOpts.find((o) => String(o.value) === strVal) || flatOpts[0] || { value: "", label: placeholder, dot: "" };
  const renderOpt = (o) => {
    const isSel = String(o.value) === String(selected.value);
    const dotAttr = o.dot ? ` data-dot="${esc(o.dot)}"` : "";
    const dotHtml = o.dot ? `<span class="ui-select-dot" style="color:${esc(o.dot)};background:currentColor"></span>` : "";
    return `<button type="button" class="ui-select-option" role="option" data-val="${esc(o.value)}"${dotAttr} aria-selected="${isSel ? "true" : "false"}">
      <span class="ui-select-option-main">
        <span class="ui-select-option-title">${dotHtml}<span>${esc(o.label)}</span></span>
        ${o.sub ? `<span class="ui-select-option-sub">${esc(o.sub)}</span>` : ""}
      </span>
      ${UI_SELECT_CHECK_SVG}
    </button>`;
  };
  let menuHtml = "";
  if (Array.isArray(groups)) {
    menuHtml = groups
      .map((g) => {
        const head = g.label ? `<div class="ui-select-group-label">${esc(g.label)}</div>` : "";
        return head + (g.options || []).map(renderOpt).join("");
      })
      .join("");
  } else {
    menuHtml = flatOpts.map(renderOpt).join("");
  }
  const trigDot = selected.dot
    ? `<span class="ui-select-dot" style="color:${esc(selected.dot)};background:currentColor"></span>`
    : `<span class="ui-select-dot" style="display:none"></span>`;
  return `<div class="ui-select ${disabled ? "is-disabled" : ""}" id="${esc(id)}" data-value="${esc(selected.value)}" data-disabled="${disabled ? "1" : "0"}">
    <button type="button" class="ui-select-trigger" aria-haspopup="listbox" aria-expanded="false" ${disabled ? "disabled" : ""}>
      <span class="ui-select-val">${trigDot}<span class="ui-select-txt">${esc(selected.label || placeholder)}</span></span>
      ${UI_SELECT_CHEV_SVG}
    </button>
    <div class="ui-select-menu" role="listbox" hidden>${menuHtml}</div>
  </div>`;
}

function buildModuleSelectGroups(emptyLabel = "无模块") {
  const groups = getGroupedModules();
  return [
    { label: "", options: [{ value: "", label: emptyLabel, dot: "#838896" }] },
    ...groups.map((g) => ({
      label: g.name,
      options: g.modules.map((m) => ({ value: String(m.id), label: m.name, dot: safeColor(m.color) })),
    })),
  ];
}

function bindSelectScope(scopeKey, rootEl, boundaryEl = null) {
  if (selectBinders[scopeKey]) {
    selectBinders[scopeKey].destroy();
    selectBinders[scopeKey] = null;
  }
  if (!rootEl) return;
  selectBinders[scopeKey] = bindAllUiSelects(rootEl, {
    escStack,
    boundaryEl: boundaryEl || rootEl,
    dropupThreshold: 210,
  });
}

// ---------- 初始化与数据刷新 ----------

async function boot() {
  const sigilHost = $("#brand-sigil-host");
  if (sigilHost) {
    sigilHost.replaceChildren(createBrandLogoSvg({ variant: "sigil", size: 17 }));
  }
  applySidebarState();
  bindStaticEvents();
  const auth = await fetch("/api/v1/auth/state").then((r) => r.json());
  state.authMode = auth.auth_mode || "local_single";
  if (state.authMode === "lan_pin" && !auth.authenticated) {
    renderLoginMembers(auth.human_members || []);
    showLoginOverlay();
    return;
  }
  await loadBootstrapAndProject();
  connectSSE();
}

function applySidebarState() {
  $("#workspace-shell").classList.toggle("is-sidebar-collapsed", state.sidebarCollapsed);
}

function renderLoginMembers(humans) {
  const host = $("#login-member-select-host");
  if (!host) return;
  const opts = humans.map((m) => ({
    value: String(m.id),
    label: `${m.display_name} (${m.role || "成员"})`,
    dot: safeColor(m.avatar_color),
  }));
  host.innerHTML = buildUiSelectHtml({
    id: "login-member-select",
    value: opts[0]?.value || "",
    options: opts,
    placeholder: "选择成员",
  });
  bindSelectScope("login", host, $("#login-overlay .login-box"));
}

function showLoginOverlay() {
  $("#login-overlay").hidden = false;
  $("#login-pin-input").focus();
}

async function loadBootstrapAndProject() {
  const data = await api("/api/v1/bootstrap");
  state.service = data.service;
  state.authMode = data.service.auth_mode;
  state.currentMember = data.current_member;
  state.states = data.states;
  state.priorities = data.priorities;
  state.moduleCategories = data.module_categories || [];
  state.projects = data.projects;
  state.members = data.members;

  $("#port-badge").textContent = `:${state.service.port}`;
  const modeBadge = $("#mode-badge");
  modeBadge.textContent = state.authMode === "lan_pin" ? "局域网团队" : "本机单人";
  modeBadge.classList.toggle("is-lan", state.authMode === "lan_pin");
  $("#btn-logout").hidden = state.authMode !== "lan_pin";

  if (!state.activeProjectId || !state.projects.some((p) => p.id === state.activeProjectId)) {
    state.activeProjectId = state.projects[0]?.id || null;
  }
  renderActorTrigger();
  if (state.activeProjectId) {
    await loadActiveProject();
  }
}

function isDrawerEditing() {
  const drawer = $("#item-drawer");
  if (!drawer || drawer.hidden || !state.openItemDetail) return false;
  const active = document.activeElement;
  if (active && drawer.contains(active) && active.matches("input, textarea, select")) {
    return true;
  }
  const commentDraft = ($("#d-new-comment")?.value || "").trim();
  if (commentDraft) return true;
  const item = state.openItemDetail;
  const titleEl = $("#d-title");
  const descEl = $("#d-desc");
  const planEl = $("#d-plan");
  if (titleEl && titleEl.value !== (item.title || "")) return true;
  if (descEl && descEl.value !== (item.description_md || "")) return true;
  if (planEl && planEl.value !== (item.plan_md || "")) return true;
  return false;
}

function showDrawerRemoteNotice() {
  const main = $("#drawer-content .drawer-main");
  if (!main) return;
  let banner = $("#drawer-remote-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "drawer-remote-banner";
    banner.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 10px;border-radius:6px;background:rgba(245,158,11,0.14);border:1px solid rgba(245,158,11,0.45);font-size:12px;color:#fcd34d;margin-bottom:8px";
    banner.innerHTML = `<span>远端工作项已有新更新（为防丢失未保存编辑已暂缓刷新）</span><button type="button" id="btn-drawer-sync-remote" class="btn btn-xs">同步最新</button>`;
    main.prepend(banner);
  }
}

async function loadActiveProject({ fromSSE = false } = {}) {
  if (!state.activeProjectId) return;
  localStorage.setItem("lb_project_id", String(state.activeProjectId));
  const data = await api(`/api/v1/projects/${state.activeProjectId}/items`);
  state.activeProject = data.project;
  state.modules = data.modules;
  state.labels = data.labels;
  state.items = data.items;
  state.conflictPairs = data.conflict_pairs || [];

  renderAll();
  if (state.openItemKey) {
    if (fromSSE && isDrawerEditing()) {
      showDrawerRemoteNotice();
    } else {
      await openDrawer(state.openItemKey, { silent: true });
    }
  }
}

function connectSSE() {
  if (state.sse) state.sse.close();
  const dot = $("#sse-dot");
  dot.className = "status-dot is-connecting";
  const es = new EventSource("/api/v1/events");
  state.sse = es;
  let refreshTimer = null;

  es.addEventListener("connected", () => {
    dot.className = "status-dot is-live";
    dot.title = "SSE 实时同步已连接";
  });
  es.addEventListener("board_update", () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      try {
        const bootData = await api("/api/v1/bootstrap");
        state.projects = bootData.projects;
        state.members = bootData.members;
        renderActorTrigger();
        await loadActiveProject({ fromSSE: true });
      } catch {
        // 忽略静默重刷失败
      }
    }, 180);
  });
  es.onerror = () => {
    dot.className = "status-dot is-offline";
    dot.title = "SSE 连接中断，正在自动重连...";
  };
}

// ---------- 分组、筛选与视图协调 ----------

function getGroupedModules() {
  const order = state.moduleCategories.map((c) => c.name);
  const metaMap = Object.fromEntries(state.moduleCategories.map((c) => [c.name, c]));
  const map = new Map();
  for (const m of state.modules) {
    const cat = m.category || "未分类";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat).push(m);
  }
  const names = Array.from(map.keys()).sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b, "zh-CN");
  });
  return names.map((name) => ({
    name,
    color: metaMap[name]?.color || "#64748b",
    desc: metaMap[name]?.desc || "",
    modules: map.get(name),
  }));
}

function buildModuleOptionsHtml(selectedId = null, emptyLabel = "无模块") {
  const groups = getGroupedModules();
  return (
    `<option value="">${esc(emptyLabel)}</option>` +
    groups
      .map(
        (g) =>
          `<optgroup label="${esc(g.name)}">` +
          g.modules.map((m) => `<option value="${m.id}" ${m.id === selectedId ? "selected" : ""}>${esc(m.name)}</option>`).join("") +
          `</optgroup>`
      )
      .join("")
  );
}

function getActiveFilterCount() {
  let n = 0;
  if (state.filters.stateKey) n++;
  if (state.filters.priority) n++;
  if (state.filters.memberId) n++;
  if (state.filters.moduleId) n++;
  if (state.filters.category) n++;
  if (state.filters.parentId) n++;
  if (state.filters.conflictOnly) n++;
  return n;
}

function getTopLevelParents() {
  return state.items.filter((it) => !it.parent_id);
}

function itemById(id) {
  return state.items.find((it) => it.id === id) || null;
}

function getFilteredItems() {
  const q = state.filters.q.trim().toLowerCase();
  return state.items.filter((it) => {
    if (state.display.topLevelOnly && it.parent_id && !state.filters.parentId) return false;
    if (state.filters.stateKey && it.state !== state.filters.stateKey) return false;
    if (state.filters.parentId) {
      const pid = Number(state.filters.parentId);
      if (it.id !== pid && it.parent_id !== pid) return false;
    }
    if (state.filters.priority && it.priority !== state.filters.priority) return false;
    if (state.filters.moduleId && it.module_id !== state.filters.moduleId) return false;
    if (state.filters.category) {
      const mod = moduleById(it.module_id);
      if (!mod || (mod.category || "未分类") !== state.filters.category) return false;
    }
    if (state.filters.conflictOnly && !(it.conflicts && it.conflicts.length)) return false;
    if (state.filters.memberId) {
      const mid = Number(state.filters.memberId);
      if (it.owner_id !== mid && it.agent_id !== mid) return false;
    }
    if (q) {
      const mod = moduleById(it.module_id);
      const hay = `${it.item_key} ${it.title} ${it.parent_key || ""} ${it.parent_title || ""} ${it.description_md} ${it.agent_branch} ${mod?.name || ""} ${mod?.category || ""} ${(it.scope_paths || []).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function getColumnBuckets(filteredItems) {
  const groupBy = state.display.groupBy;
  if (groupBy === "priority") {
    return state.priorities.map((p) => ({
      colId: `prio:${p.key}`,
      groupType: "priority",
      groupValue: p.key,
      label: p.label,
      color: p.color,
      iconHtml: renderPriorityIcon(p.key, p.color, 13),
      gate: false,
      gateKind: "",
      items: filteredItems.filter((i) => i.priority === p.key),
    }));
  }
  if (groupBy === "module") {
    const cols = state.modules.map((m) => ({
      colId: `mod:${m.id}`,
      groupType: "module",
      groupValue: String(m.id),
      label: m.name,
      color: m.color,
      iconHtml: `<span class="mod-dot" style="background:${esc(m.color)}"></span>`,
      gate: false,
      gateKind: "",
      items: filteredItems.filter((i) => i.module_id === m.id),
    }));
    const unassigned = filteredItems.filter((i) => !i.module_id);
    if (unassigned.length || !cols.length) {
      cols.push({
        colId: "mod:none",
        groupType: "module",
        groupValue: "",
        label: "未指定模块",
        color: "#64748b",
        iconHtml: `<span class="mod-dot" style="background:#64748b"></span>`,
        gate: false,
        gateKind: "",
        items: unassigned,
      });
    }
    return cols;
  }
  if (groupBy === "parent") {
    const parentOwners = state.items.filter((it) => !it.parent_id && (it.sub_total > 0 || filteredItems.some((x) => x.parent_id === it.id)));
    const cols = parentOwners.map((p) => {
      const st = stateMeta(p.state);
      const groupItems = filteredItems.filter((i) => i.id === p.id || i.parent_id === p.id);
      return {
        colId: `parent:${p.id}`,
        groupType: "parent",
        groupValue: String(p.id),
        label: `${p.item_key} · ${p.title}`,
        color: st.color,
        iconHtml: renderStateIcon(st.key, st.color, 13),
        gate: false,
        gateKind: "",
        items: groupItems,
      };
    });
    const usedIds = new Set(cols.flatMap((c) => c.items.map((x) => x.id)));
    const standalone = filteredItems.filter((i) => !usedIds.has(i.id));
    if (standalone.length || !cols.length) {
      cols.push({
        colId: "parent:none",
        groupType: "parent",
        groupValue: "",
        label: "独立工作项（无父子分组）",
        color: "#64748b",
        iconHtml: `<span class="mod-dot" style="background:#64748b"></span>`,
        gate: false,
        gateKind: "",
        items: standalone,
      });
    }
    return cols;
  }
  return state.states.map((st) => ({
    colId: `state:${st.key}`,
    groupType: "state",
    groupValue: st.key,
    label: st.label,
    color: st.color,
    iconHtml: renderStateIcon(st.key, st.color, 14),
    gate: Boolean(st.gate),
    gateKind: st.key === "qa_wait" ? "qa" : st.gate ? "plan" : "",
    items: filteredItems.filter((i) => i.state === st.key),
  }));
}

function isColCollapsed(col) {
  if (state.manualCollapsedCols.has(col.colId)) return true;
  if (state.manualExpandedCols.has(col.colId)) return false;
  return Boolean(state.display.autoCollapseEmpty && col.items.length === 0);
}

// ---------- Popover 管理 ----------

function setPopover(name) {
  state.openPopover = state.openPopover === name ? null : name;
  $("#actor-popover").hidden = state.openPopover !== "actor";
  $("#filter-popover").hidden = state.openPopover !== "filter";
  $("#display-popover").hidden = state.openPopover !== "display";
  if (state.openPopover === "actor") renderActorPopover();
  if (state.openPopover === "filter") renderFilterPopover();
  if (state.openPopover === "display") renderDisplayPopover();
}

function closePopovers() {
  if (!state.openPopover) return;
  state.openPopover = null;
  $("#actor-popover").hidden = true;
  $("#filter-popover").hidden = true;
  $("#display-popover").hidden = true;
}

function renderActorTrigger() {
  const curId = state.actorOverrideId || state.currentMember?.id;
  const active = memberById(curId) || state.currentMember;
  if (!active) return;
  const av = $("#actor-avatar");
  av.textContent = active.display_name.slice(0, 1);
  av.style.background = active.avatar_color || "#3b82f6";
  $("#actor-name-label").textContent = active.display_name;
}

function renderActorPopover() {
  const curId = state.actorOverrideId || state.currentMember?.id;
  const el = $("#actor-popover");
  if (state.authMode === "lan_pin") {
    const me = state.currentMember;
    el.innerHTML = `
      <div class="pop-section">
        <div class="pop-head"><span>局域网已登录身份</span></div>
        <div class="pop-menu-item is-active" style="cursor:default">
          <span class="avatar-xs" style="background:${safeColor(me?.avatar_color, "#3b82f6")}">${esc(me?.display_name?.slice(0, 1) || "?")}</span>
          <span>${esc(me?.display_name || "未知")}</span>
        </div>
      </div>`;
    return;
  }
  el.innerHTML = `
    <div class="pop-section">
      <div class="pop-head"><span>切换当前操作视角（本机单人）</span></div>
      ${state.members
        .map(
          (m) => `
        <button class="pop-menu-item ${m.id === curId ? "is-active" : ""}" data-select-actor="${m.id}">
          <span class="avatar-xs" style="background:${safeColor(m.avatar_color)}">${esc(m.display_name.slice(0, 1))}</span>
          <span style="flex:1">${esc(m.display_name)}</span>
          <span style="font-size:10.5px;color:var(--text-muted)">${m.kind === "agent" ? "AI" : "成员"}</span>
        </button>`
        )
        .join("")}
    </div>`;
}

function renderFilterPopover() {
  const el = $("#filter-popover");
  const groups = getGroupedModules();
  el.innerHTML = `
    <div class="pop-section">
      <div class="pop-head">
        <span>优先级</span>
        ${state.filters.priority ? `<button class="text-btn-xs" data-clear-filter="priority">清除</button>` : ""}
      </div>
      <div class="pop-pills">
        ${state.priorities
          .map(
            (p) => `
          <button class="pop-pill ${state.filters.priority === p.key ? "is-active" : ""}" data-set-priority="${p.key}">
            ${renderPriorityIcon(p.key, p.color, 11)}
            <span>${esc(p.label)}</span>
          </button>`
          )
          .join("")}
      </div>
    </div>

    <div class="pop-section">
      <div class="pop-head">
        <span>成员 / AI Agent</span>
        ${state.filters.memberId ? `<button class="text-btn-xs" data-clear-filter="memberId">清除</button>` : ""}
      </div>
      <div class="pop-pills">
        ${state.members
          .map(
            (m) => `
          <button class="pop-pill ${String(state.filters.memberId) === String(m.id) ? "is-active" : ""}" data-set-member="${m.id}">
            <span class="avatar-chip" style="background:${safeColor(m.avatar_color)}">${esc(m.display_name.slice(0, 1))}</span>
            <span>${esc(m.display_name)}</span>
          </button>`
          )
          .join("")}
      </div>
    </div>

    <div class="pop-section">
      <div class="pop-head">
        <span>按业务分类 / 模块筛选</span>
        ${state.filters.moduleId || state.filters.category ? `<button class="text-btn-xs" data-clear-filter="module">清除</button>` : ""}
      </div>
      ${groups
        .map(
          (g) => `
        <div class="pop-mod-group">
          <div class="pop-mod-cat ${state.filters.category === g.name && !state.filters.moduleId ? "is-active" : ""}" data-set-category="${esc(g.name)}">
            <span class="mod-dot" style="background:${esc(g.color)}"></span>
            <span>${esc(g.name)}</span>
          </div>
          <div class="pop-pills" style="padding-left:10px;margin-top:3px">
            ${g.modules
              .map(
                (m) => `
              <button class="pop-pill ${state.filters.moduleId === m.id ? "is-active" : ""}" data-set-module="${m.id}">
                <span class="chip-dot" style="background:${esc(m.color)}"></span>
                <span>${esc(m.name)}</span>
                <span style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted)">${m.item_count ?? 0}</span>
              </button>`
              )
              .join("")}
          </div>
        </div>`
        )
        .join("")}
    </div>

    <div class="pop-section">
      <div class="pop-head">
        <span>按父工作项专题筛选</span>
        ${state.filters.parentId ? `<button class="text-btn-xs" data-clear-filter="parentId">清除</button>` : ""}
      </div>
      <div class="pop-pills">
        ${(() => {
          const parents = state.items.filter((it) => !it.parent_id && it.sub_total > 0);
          if (!parents.length) {
            return `<span style="font-size:11.5px;color:var(--text-muted)">暂无已拆分子项的父工作项</span>`;
          }
          return parents
            .map(
              (p) => `
            <button class="pop-pill ${Number(state.filters.parentId) === p.id ? "is-active" : ""}" data-set-parent="${p.id}" title="${esc(p.title)}">
              <span style="font-family:var(--font-mono);font-size:10.5px">${esc(p.item_key)}</span>
              <span style="max-width:130px;overflow:hidden;text-overflow:ellipsis">${esc(p.title)}</span>
              <span style="font-family:var(--font-mono);font-size:10px;color:#7dd3fc">${p.sub_done}/${p.sub_total}</span>
            </button>`
            )
            .join("");
        })()}
      </div>
    </div>

    <div class="pop-section">
      <div class="pop-row-toggle" data-toggle-conflict-filter="1">
        <span style="display:inline-flex;align-items:center;gap:6px">${SVG_ICONS.warn} 仅显示存在路径冲突的工作项</span>
        <span class="switch-track ${state.filters.conflictOnly ? "is-on" : ""}"></span>
      </div>
    </div>
  `;
}

function renderDisplayPopover() {
  const d = state.display;
  const el = $("#display-popover");
  const propItems = [
    ["priority", "优先级"],
    ["module", "所属模块"],
    ["labels", "标签"],
    ["owner", "负责人"],
    ["agent", "AI Agent"],
    ["subProgress", "父子进度"],
    ["scopeCount", "路径数"],
    ["statePill", "状态徽标"],
  ];
  el.innerHTML = `
    <div class="pop-section">
      <div class="pop-head"><span>分组方式</span></div>
      <div class="pop-pills">
        <button class="pop-pill ${d.groupBy === "state" ? "is-active" : ""}" data-set-groupby="state">按状态分列</button>
        <button class="pop-pill ${d.groupBy === "module" ? "is-active" : ""}" data-set-groupby="module">按模块分列</button>
        <button class="pop-pill ${d.groupBy === "priority" ? "is-active" : ""}" data-set-groupby="priority">按优先级分列</button>
        <button class="pop-pill ${d.groupBy === "parent" ? "is-active" : ""}" data-set-groupby="parent">按父工作项分组</button>
      </div>
    </div>

    <div class="pop-section">
      <div class="pop-head">
        <span>看板列与层级策略</span>
        <div style="display:flex;gap:6px">
          <button class="text-btn-xs" data-cols-action="expand-all">全部展开</button>
          <button class="text-btn-xs" data-cols-action="reset">按默认</button>
        </div>
      </div>
      <div class="pop-row-toggle" data-toggle-display="topLevelOnly">
        <span>仅看顶层工作项（折叠隐藏子项）</span>
        <span class="switch-track ${d.topLevelOnly ? "is-on" : ""}"></span>
      </div>
      <div class="pop-row-toggle" data-toggle-display="autoCollapseEmpty">
        <span>自动折叠空列为窄竖条</span>
        <span class="switch-track ${d.autoCollapseEmpty ? "is-on" : ""}"></span>
      </div>
      <div class="pop-row-toggle" data-toggle-display="compact">
        <span>紧凑卡片密度</span>
        <span class="switch-track ${d.compact ? "is-on" : ""}"></span>
      </div>
    </div>

    <div class="pop-section">
      <div class="pop-head"><span>卡片显示属性</span></div>
      <div class="pop-pills">
        ${propItems
          .map(
            ([key, label]) => `
          <button class="pop-pill ${d.fields[key] ? "is-active" : ""}" data-toggle-field="${key}">${esc(label)}</button>`
          )
          .join("")}
      </div>
    </div>
  `;
}

// ---------- 核心渲染 ----------

function renderAll() {
  renderSidebarPulse();
  renderProjectTree();
  renderSubHeader();
  renderActiveFilterBar();
  syncActiveViewPanes();
  renderBoard();
  renderListView();
  renderModulesView();
  renderAgentsRadarView();
  renderSettingsView();
  if (state.openPopover === "filter") renderFilterPopover();
  if (state.openPopover === "display") renderDisplayPopover();
}

function renderSidebarPulse() {
  const host = $("#sidebar-pulse-card");
  if (!host) return;
  const total = state.items.length;
  const done = state.items.filter((i) => i.state === "done").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const planWait = state.items.filter((i) => i.state === "plan_review").length;
  const qaWait = state.items.filter((i) => i.state === "qa_wait").length;
  const conflicts = state.conflictPairs.length;

  host.innerHTML = `
    <div class="pulse-head">
      <span class="pulse-label">推进态势 · ${esc(state.activeProject?.key || "OPS")}</span>
      <span class="pulse-ratio">${done}/${total} (${pct}%)</span>
    </div>
    <div class="pulse-bar" title="已完成 ${done} / 总计 ${total}"><i style="width:${pct}%"></i></div>
    <div class="pulse-quick-grid">
      <button type="button" class="pulse-kpi-btn ${planWait ? "is-warn" : ""}" data-pulse-state="plan_review" title="点击筛选「方案待审」闸门工作项">
        <strong>${planWait}</strong>
        <span>方案待审</span>
      </button>
      <button type="button" class="pulse-kpi-btn ${qaWait ? "is-qa" : ""}" data-pulse-state="qa_wait" title="点击筛选「待测试」闸门工作项">
        <strong>${qaWait}</strong>
        <span>待测试</span>
      </button>
      <button type="button" class="pulse-kpi-btn ${conflicts ? "is-danger" : ""}" data-jump-agents="1" title="查看作用域路径防撞雷达">
        <strong>${conflicts}</strong>
        <span>路径冲突</span>
      </button>
    </div>
  `;
}

function renderProjectTree() {
  const el = $("#project-tree");
  el.innerHTML = state.projects
    .map((p) => {
      const isActiveProj = p.id === state.activeProjectId;
      const subNav = isActiveProj
        ? `
        <div class="project-sub-list">
          <button class="project-sub-item ${state.activeView === "items" ? "is-active" : ""}" data-view="items">
            <span class="nav-ico">${SVG_ICONS.items}</span>
            <span>工作项</span>
            <span class="nav-count">${state.items.length}</span>
          </button>
          <button class="project-sub-item ${state.activeView === "modules" ? "is-active" : ""}" data-view="modules">
            <span class="nav-ico">${SVG_ICONS.modules}</span>
            <span>模块上下文包</span>
            <span class="nav-count">${state.modules.length}</span>
          </button>
          <button class="project-sub-item ${state.activeView === "settings" ? "is-active" : ""}" data-view="settings">
            <span class="nav-ico">${SVG_ICONS.settings}</span>
            <span>项目设置</span>
          </button>
        </div>`
        : "";
      return `
        <div class="project-node">
          <button class="project-node-btn ${isActiveProj ? "is-expanded" : ""}" data-proj-id="${p.id}">
            <span class="proj-key-tag">${esc(p.key)}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(p.name)}</span>
            ${!isActiveProj ? `<span class="nav-count">${p.item_count ?? 0}</span>` : ""}
          </button>
          ${subNav}
        </div>`;
    })
    .join("");

  $$(".sidebar .nav-item[data-view]").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.view === state.activeView);
  });
}

function renderSubHeader() {
  const viewNames = {
    items: "工作项",
    modules: "模块上下文包",
    agents: "Agent 与冲突雷达",
    settings: "项目与成员设置",
  };
  $("#view-title").textContent = viewNames[state.activeView] || "工作项";

  if (state.activeProject) {
    $("#project-key-pill").textContent = state.activeProject.key;
    $("#project-name-crumb").textContent = state.activeProject.name;
    const sp = $("#strict-pill");
    sp.textContent = state.activeProject.strict_mode ? "严格闸门" : "自由流转";
    sp.classList.toggle("is-strict", Boolean(state.activeProject.strict_mode));
  }

  const filtered = getFilteredItems();
  const isFiltered = filtered.length !== state.items.length;
  const countBadge = $("#view-count-badge");
  if (state.activeView === "items") {
    countBadge.hidden = false;
    countBadge.textContent = isFiltered ? `${filtered.length} / ${state.items.length}` : String(state.items.length);
  } else if (state.activeView === "modules") {
    countBadge.hidden = false;
    countBadge.textContent = String(state.modules.length);
  } else {
    countBadge.hidden = true;
  }

  const planWait = state.items.filter((i) => i.state === "plan_review").length;
  const qaWait = state.items.filter((i) => i.state === "qa_wait").length;
  const conflictCount = state.conflictPairs.length;

  const cBadge = $("#nav-count-conflicts");
  cBadge.textContent = String(conflictCount);
  cBadge.hidden = conflictCount === 0;

  const alerts = [];
  if (planWait > 0) alerts.push(`<button type="button" class="stat-alert-pill is-gate" data-pulse-state="plan_review">方案待审 ${planWait}</button>`);
  if (qaWait > 0) alerts.push(`<button type="button" class="stat-alert-pill is-qa" data-pulse-state="qa_wait">待测试 ${qaWait}</button>`);
  if (conflictCount > 0) {
    alerts.push(`<button type="button" class="stat-alert-pill is-conflict" data-jump-agents="1">${SVG_ICONS.warn} 路径冲突 ${conflictCount}</button>`);
  }
  $("#topbar-stats").innerHTML = alerts.join("");

  $("#work-items-controls").hidden = state.activeView !== "items";
  $$(".seg-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.layout === state.display.layout));

  const fc = getActiveFilterCount();
  const fBtn = $("#btn-filter-popover");
  const fBadge = $("#filter-active-badge");
  fBtn.classList.toggle("is-active", fc > 0);
  fBadge.hidden = fc === 0;
  fBadge.textContent = String(fc);
}

function renderActiveFilterBar() {
  const bar = $("#active-filter-bar");
  if (state.activeView !== "items") {
    bar.hidden = true;
    return;
  }
  const chips = [];
  if (state.filters.q.trim()) {
    chips.push(`<span class="af-chip">搜索：${esc(state.filters.q.trim())}<button data-clear-filter="q">×</button></span>`);
  }
  if (state.filters.stateKey) {
    const st = stateMeta(state.filters.stateKey);
    chips.push(`<span class="af-chip">${renderStateIcon(st.key, st.color, 11)} 状态：${esc(st.label)}<button data-clear-filter="stateKey">×</button></span>`);
  }
  if (state.filters.priority) {
    const p = priorityMeta(state.filters.priority);
    chips.push(`<span class="af-chip">${renderPriorityIcon(p.key, p.color, 11)} ${esc(p.label)}<button data-clear-filter="priority">×</button></span>`);
  }
  if (state.filters.memberId) {
    const m = memberById(Number(state.filters.memberId));
    if (m) chips.push(`<span class="af-chip">成员：${esc(m.display_name)}<button data-clear-filter="memberId">×</button></span>`);
  }
  if (state.filters.category) {
    chips.push(`<span class="af-chip">分类：${esc(state.filters.category)}<button data-clear-filter="module">×</button></span>`);
  }
  if (state.filters.moduleId) {
    const mod = moduleById(state.filters.moduleId);
    if (mod) chips.push(`<span class="af-chip"><span class="chip-dot" style="background:${esc(mod.color)}"></span>${esc(mod.name)}<button data-clear-filter="module">×</button></span>`);
  }
  if (state.filters.parentId) {
    const pItem = itemById(Number(state.filters.parentId));
    if (pItem) chips.push(`<span class="af-chip" style="border-color:rgba(177,151,252,0.48);color:var(--ui-ch-plane-inner)">专题：${esc(pItem.item_key)} ${esc(pItem.title.slice(0, 14))}<button data-clear-filter="parentId">×</button></span>`);
  }
  if (state.filters.conflictOnly) {
    chips.push(`<span class="af-chip" style="border-color:rgba(255,184,0,0.45);color:var(--warn)">仅路径冲突<button data-clear-filter="conflictOnly">×</button></span>`);
  }

  if (!chips.length) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  bar.hidden = false;
  bar.innerHTML = `<span class="af-label">已筛选</span>${chips.join("")}<button class="af-clear-all" data-clear-filter="all">清除全部</button>`;
}

function syncActiveViewPanes() {
  const targetPaneId =
    state.activeView === "items"
      ? state.display.layout === "list"
        ? "view-list"
        : "view-board"
      : `view-${state.activeView}`;
  $$(".view-pane").forEach((p) => p.classList.toggle("is-active", p.id === targetPaneId));
}

function renderBoard() {
  const container = $("#kanban-columns");
  container.classList.toggle("is-compact", state.display.compact);
  const filtered = getFilteredItems();
  const buckets = getColumnBuckets(filtered);

  container.innerHTML = buckets
    .map((col) => {
      const collapsed = isColCollapsed(col);
      const gateCls = col.gateKind === "qa" ? "is-gate-qa" : col.gateKind === "plan" ? "is-gate" : "";
      const colAccent = safeColor(col.color, "#838896");
      const dataAttrs = `style="--col-accent:${esc(colAccent)}" data-col-id="${esc(col.colId)}" data-group-type="${esc(col.groupType)}" data-group-value="${esc(col.groupValue)}"`;

      if (collapsed) {
        return `
          <div class="kanban-col is-collapsed ${gateCls}" ${dataAttrs} data-expand-col="${esc(col.colId)}" title="点击展开「${esc(col.label)}」或将卡片拖拽至此">
            <div class="col-collapsed-head">
              ${col.iconHtml}
              <span class="col-collapsed-count">${col.items.length}</span>
              ${col.gate ? `<span class="col-collapsed-gate" title="人工闸门"></span>` : ""}
              <span class="col-collapsed-title">${esc(col.label)}</span>
            </div>
          </div>`;
      }

      const cardsHtml = col.items.length
        ? col.items.map((it) => renderCardHtml(it)).join("")
        : `<div class="col-empty">拖拽卡片到此列</div>`;

      return `
        <div class="kanban-col ${gateCls}" ${dataAttrs}>
          <div class="col-header">
            ${col.iconHtml}
            <span class="col-name" title="${esc(col.label)}">${esc(col.label)}</span>
            <span class="col-count">${col.items.length}</span>
            ${col.gate ? `<span class="gate-tag ${col.gateKind === "qa" ? "is-qa" : ""}">闸门</span>` : ""}
            <div class="col-actions">
              <button class="icon-btn-xs" data-collapse-col="${esc(col.colId)}" title="折叠此列">${SVG_ICONS.collapseCol}</button>
              <button class="icon-btn-xs" data-quick-add-type="${esc(col.groupType)}" data-quick-add-val="${esc(col.groupValue)}" title="在「${esc(col.label)}」新建">${SVG_ICONS.plus}</button>
            </div>
          </div>
          <div class="col-body">
            ${cardsHtml}
          </div>
          <button class="col-footer-add" data-quick-add-type="${esc(col.groupType)}" data-quick-add-val="${esc(col.groupValue)}">
            ${SVG_ICONS.plus}
            <span>新建工作项</span>
          </button>
        </div>`;
    })
    .join("");
}

function renderCardHtml(it) {
  const f = state.display.fields;
  const prio = priorityMeta(it.priority);
  const st = stateMeta(it.state);
  const mod = moduleById(it.module_id);
  const owner = memberById(it.owner_id);
  const rawAgent = memberById(it.agent_id);
  // 人物智能去重：仅真正 AI 槽位以 Agent 胶囊渲染，避免把同一人类成员重复渲染两枚胶囊
  const aiAgent = rawAgent && rawAgent.kind === "agent" ? rawAgent : null;
  const displayOwner = owner || (rawAgent && rawAgent.kind === "human" ? rawAgent : null);
  const hasConflict = Boolean(it.conflicts && it.conflicts.length);
  const labels = it.labels || [];

  // 卡片左侧语义光条：阻塞/紧急用危急红，高优先级用琥珀黄，其余用状态色
  const cardAccent =
    it.blocked_open_count > 0 || it.priority === "urgent"
      ? "#ff5266"
      : it.priority === "high"
      ? "#ffb800"
      : safeColor(st.color, "#838896");

  const pills = [];

  if (f.statePill || state.display.groupBy !== "state") {
    pills.push(`<span class="prop-pill">${renderStateIcon(st.key, st.color, 11)}<span>${esc(st.label)}</span></span>`);
  }

  if (f.module && mod && state.display.groupBy !== "module") {
    pills.push(
      `<span class="prop-pill" title="模块：${esc(mod.category || "")} / ${esc(mod.name)}"><span style="color:${esc(mod.color)};display:inline-flex">${SVG_ICONS.moduleSm}</span><span class="prop-pill-text">${esc(mod.name)}</span></span>`
    );
  }

  if (f.labels && labels.length) {
    const firstLb = labels[0];
    const extraTxt = labels.length > 1 ? ` +${labels.length - 1}` : "";
    const tip = labels.map((l) => l.name).join("、");
    pills.push(
      `<span class="prop-pill" title="标签：${esc(tip)}"><span class="chip-dot" style="background:${esc(firstLb.color)}"></span><span>${esc(firstLb.name)}${extraTxt}</span></span>`
    );
  }

  if (f.subProgress && it.sub_total > 0) {
    const allDone = it.sub_done >= it.sub_total;
    pills.push(
      `<span class="prop-pill is-sub ${allDone ? "is-all-done" : ""}" title="子工作项完成度：${it.sub_done}/${it.sub_total}">↳ ${it.sub_done}/${it.sub_total}</span>`
    );
  }

  if (it.blocked_open_count > 0) {
    pills.push(
      `<span class="prop-pill is-blocked" title="存在 ${it.blocked_open_count} 项未完成前置阻塞：${esc((it.blocked_by || []).join(", "))}">⊘ 阻塞 ${it.blocked_open_count}</span>`
    );
  }

  if (hasConflict) {
    const conflictText = it.conflicts.map((c) => c.other_key).join(", ");
    pills.push(`<span class="prop-pill is-conflict" title="作用域路径重叠：${esc(conflictText)}">${SVG_ICONS.warn} 冲突 ${esc(conflictText)}</span>`);
  }

  // 底部足迹：左侧精简人物（已去重） + 右侧无框等宽微标
  const footLeft = [];
  if (f.agent && aiAgent) {
    const hb = fmtRelativeAge(it.agent_heartbeat_at);
    const br = it.agent_branch ? ` · ${it.agent_branch}` : "";
    footLeft.push(
      `<span class="prop-pill is-agent" title="AI 执行器：${esc(aiAgent.display_name + br)} (${esc(hb.text)})"><span class="hb-dot ${hb.fresh ? "is-fresh" : "is-stale"}"></span><span class="prop-pill-text">${esc(aiAgent.display_name)}</span></span>`
    );
  }
  if (f.owner && displayOwner) {
    footLeft.push(
      `<span class="card-metric" style="color:var(--text-secondary)" title="人类负责人：${esc(displayOwner.display_name)}"><span class="avatar-chip" style="background:${safeColor(displayOwner.avatar_color)}">${esc(displayOwner.display_name.slice(0, 1))}</span><span>${esc(displayOwner.display_name)}</span></span>`
    );
  }

  const footRight = [];
  if (f.scopeCount && it.scope_paths?.length) {
    footRight.push(`<span class="card-metric" title="${esc(it.scope_paths.join("\n"))}">${SVG_ICONS.folder}${it.scope_paths.length}</span>`);
  }
  if (it.attachment_count) {
    footRight.push(`<span class="card-metric" title="附件数">${SVG_ICONS.attach}${it.attachment_count}</span>`);
  }
  if (it.comment_count) {
    footRight.push(`<span class="card-metric" title="评论数">${SVG_ICONS.comment}${it.comment_count}</span>`);
  }

  const footHtml =
    footLeft.length || footRight.length
      ? `<div class="card-foot"><div class="card-foot-left">${footLeft.join("")}</div><div class="card-foot-right">${footRight.join("")}</div></div>`
      : "";

  let gateBtns = "";
  if (it.state === "plan_review") {
    gateBtns = `
      <div class="gate-actions">
        <button class="btn btn-success" data-gate-action="approve_plan" data-item-key="${esc(it.item_key)}">✓ 批准开工</button>
        <button class="btn btn-danger" data-gate-action="reject" data-item-key="${esc(it.item_key)}">↩ 打回</button>
      </div>`;
  } else if (it.state === "qa_wait") {
    gateBtns = `
      <div class="gate-actions">
        <button class="btn" data-gate-action="start_testing" data-item-key="${esc(it.item_key)}">开始测试</button>
        <button class="btn btn-success" data-gate-action="done" data-item-key="${esc(it.item_key)}">✓ 通过</button>
        <button class="btn btn-danger" data-gate-action="reject" data-item-key="${esc(it.item_key)}">↩ 打回</button>
      </div>`;
  }

  const prioChip =
    f.priority && prio.key !== "none"
      ? `<span class="card-prio-chip" style="color:${esc(prio.color)}" title="优先级：${esc(prio.label)}">${renderPriorityIcon(prio.key, prio.color, 11)}<span>${esc(prio.label)}</span></span>`
      : "";

  const parentBadge =
    f.subProgress && it.parent_key
      ? `<button type="button" class="card-parent-tag" data-open-parent-key="${esc(it.parent_key)}" title="所属父工作项：${esc(it.parent_key)} · ${esc(it.parent_title || "")}">↑ ${esc(it.parent_key)}</button>`
      : "";
  const subBarHtml =
    f.subProgress && it.sub_total > 0
      ? `<div class="card-sub-bar" title="子项进度 ${it.sub_done}/${it.sub_total}"><i style="width:${Math.round((it.sub_done / it.sub_total) * 100)}%"></i></div>`
      : "";

  return `
    <article class="card ${hasConflict ? "has-conflict" : ""}" style="--card-accent:${esc(cardAccent)}" draggable="true" data-card-key="${esc(it.item_key)}">
      <div class="card-top">
        <span class="card-key">${esc(it.item_key)}</span>
        ${prioChip}
        ${parentBadge}
        <div class="card-hover-tools">
          <button class="icon-btn-xs" data-copy-prompt="${esc(it.item_key)}" title="复制 Agent 开工指令">${SVG_ICONS.copy}</button>
        </div>
      </div>
      <div class="card-title" title="${esc(it.title)}">${esc(it.title)}</div>
      ${pills.length ? `<div class="card-props">${pills.join("")}</div>` : ""}
      ${footHtml}
      ${gateBtns}
      ${subBarHtml}
    </article>`;
}

function renderListView() {
  const wrap = $("#list-container");
  const filtered = getFilteredItems();
  const buckets = getColumnBuckets(filtered);
  const html = buckets
    .filter((b) => b.items.length > 0)
    .map((b) => {
      const rows = b.items
        .map((it) => {
          const mod = moduleById(it.module_id);
          const owner = memberById(it.owner_id);
          const agent = memberById(it.agent_id);
          const prio = priorityMeta(it.priority);
          const st = stateMeta(it.state);
          const pTag = it.parent_key
            ? `<button type="button" class="card-parent-tag" data-open-parent-key="${esc(it.parent_key)}" title="父工作项：${esc(it.parent_title || "")}">↑ ${esc(it.parent_key)}</button>`
            : "";
          const subTag =
            it.sub_total > 0
              ? `<span class="prop-pill is-sub ${it.sub_done >= it.sub_total ? "is-all-done" : ""}" style="height:18px;padding:0 5px">↳ ${it.sub_done}/${it.sub_total}</span>`
              : "";
          const blkTag =
            it.blocked_open_count > 0
              ? `<span class="prop-pill is-blocked" style="height:18px;padding:0 5px" title="前置阻塞：${esc((it.blocked_by || []).join(", "))}">⊘${it.blocked_open_count}</span>`
              : "";
          return `
            <div class="list-row" data-open-key="${esc(it.item_key)}">
              <span class="card-key">${esc(it.item_key)}</span>
              <span style="display:flex;align-items:center;gap:6px;min-width:0;font-weight:500">
                ${state.display.groupBy !== "state" ? renderStateIcon(st.key, st.color, 13) : ""}
                ${pTag}
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${it.conflicts?.length ? "⚠ " : ""}${esc(it.title)}</span>
                ${subTag}
                ${blkTag}
              </span>
              <span>${mod ? `<span class="prop-pill"><span class="chip-dot" style="background:${esc(mod.color)}"></span><span class="prop-pill-text">${esc(mod.name)}</span></span>` : `<span style="color:var(--text-muted)">-</span>`}</span>
              <span style="display:inline-flex;align-items:center;gap:5px;color:${esc(prio.color)};font-size:12px">${renderPriorityIcon(prio.key, prio.color, 11)}${esc(prio.label)}</span>
              <span style="color:var(--text-secondary);font-size:12px">${owner ? esc(owner.display_name) : "-"}</span>
              <span style="color:#c4b5fd;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${agent ? esc(agent.display_name) : `<span style="color:var(--text-muted)">未分配</span>`}</span>
              <span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted);text-align:right">${fmtBeijingTime(it.updated_at)}</span>
            </div>`;
        })
        .join("");
      return `
        <div class="list-group">
          <div class="list-group-head">
            ${b.iconHtml}
            <span>${esc(b.label)}</span>
            <span class="col-count">${b.items.length}</span>
          </div>
          ${rows}
        </div>`;
    })
    .join("");
  wrap.innerHTML = html || `<div class="col-empty">当前筛选条件下暂无工作项</div>`;
}

function renderModulesView() {
  const tabsEl = $("#modules-cat-tabs");
  const grid = $("#modules-grid");
  const groups = getGroupedModules();

  if (state.activeModuleTab && !groups.some((g) => g.name === state.activeModuleTab)) {
    state.activeModuleTab = "";
  }

  tabsEl.innerHTML =
    `<button class="cat-tab ${!state.activeModuleTab ? "is-active" : ""}" data-mod-tab="">全部模块 (${state.modules.length})</button>` +
    groups
      .map(
        (g) =>
          `<button class="cat-tab ${state.activeModuleTab === g.name ? "is-active" : ""}" data-mod-tab="${esc(g.name)}">
            <span class="mod-dot" style="background:${esc(g.color)}"></span>${esc(g.name)} (${g.modules.length})
          </button>`
      )
      .join("");

  if (!state.modules.length) {
    grid.innerHTML = `<div class="col-empty">暂无模块，点击右上角「新增模块」创建 AI 上下文包</div>`;
    return;
  }

  const visibleGroups = state.activeModuleTab ? groups.filter((g) => g.name === state.activeModuleTab) : groups;
  grid.innerHTML = visibleGroups
    .map(
      (g) => `
      <div class="mod-cat-section-title">
        <span class="mod-dot" style="background:${esc(g.color)};width:9px;height:9px"></span>
        <span>${esc(g.name)}</span>
        <span class="nav-count">${g.modules.length} 个模块</span>
        ${g.desc ? `<span class="mod-cat-section-desc">· ${esc(g.desc)}</span>` : ""}
      </div>
      ${g.modules
        .map(
          (m) => `
        <div class="mod-card">
          <div class="mod-card-head">
            <span class="mod-dot" style="background:${esc(m.color)};width:9px;height:9px"></span>
            <span class="mod-card-title">${esc(m.name)}</span>
            <span class="cat-pill">${esc(m.category || "未分类")}</span>
            <span class="nav-count">${m.done_count ?? 0}/${m.item_count ?? 0}</span>
            <button class="btn btn-xs" style="margin-left:auto" data-edit-module="${m.id}">编辑</button>
          </div>
          ${m.summary ? `<div style="color:var(--text-secondary);font-size:12.5px">${esc(m.summary)}</div>` : ""}
          <div class="mod-kv"><label>职责范围</label><div>${esc(m.scope_desc || "未填写")}</div></div>
          <div class="mod-kv"><label>现有能力</label><div>${esc(m.capabilities || "未填写")}</div></div>
          <div class="mod-kv"><label>架构红线（AI 必读）</label><div class="redline-text">${esc(m.redlines || "无特别限制")}</div></div>
          <div class="mod-kv"><label>入口路径</label><div class="code-inline">${esc((m.entry_paths || []).join(" , ") || "-")}</div></div>
          <div class="mod-kv"><label>验证命令</label><div class="code-inline">${esc(m.verify_cmd || "-")}</div></div>
        </div>`
        )
        .join("")}`
    )
    .join("");
}

function renderAgentsRadarView() {
  const conflictEl = $("#conflict-radar-list");
  if (!state.conflictPairs.length) {
    conflictEl.innerHTML = `<div style="color:#7ee2a8;font-size:12.5px">✓ 当前处于「方案待审 / 进行中」的工作项之间无作用域路径重叠冲突。</div>`;
  } else {
    conflictEl.innerHTML = state.conflictPairs
      .map(
        (pair) => `
        <div class="conflict-pair-card">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <strong>⚠ 路径重叠警告</strong>
            <button class="btn btn-xs" data-open-key="${esc(pair.item_a.item_key)}">${esc(pair.item_a.item_key)} · ${esc(pair.item_a.title)}</button>
            <span>↔</span>
            <button class="btn btn-xs" data-open-key="${esc(pair.item_b.item_key)}">${esc(pair.item_b.item_key)} · ${esc(pair.item_b.title)}</button>
          </div>
          <div style="font-family:var(--font-mono);font-size:11.5px;color:#f3c873">
            重叠路径：${pair.overlaps.map((o) => `${esc(o.a)} ∩ ${esc(o.b)}`).join(" ； ")}
          </div>
        </div>`
      )
      .join("");
  }

  const rosterEl = $("#agent-roster-grid");
  rosterEl.innerHTML = state.members
    .map((m) => {
      const activeTasks = state.items.filter(
        (it) => (it.agent_id === m.id || (m.kind === "human" && it.owner_id === m.id)) && !["done", "backlog"].includes(it.state)
      );
      return `
        <div class="agent-card">
          <div style="display:flex;align-items:center;gap:8px">
            <span class="avatar-dot" style="background:${safeColor(m.avatar_color)}">${esc(m.display_name.slice(0, 1))}</span>
            <div>
              <div style="font-weight:600">${esc(m.display_name)} <span class="cat-pill">${m.kind === "agent" ? "AI" : "成员"}</span></div>
              <div style="font-size:11px;color:var(--text-muted)">${esc(m.role || m.kind)}</div>
            </div>
          </div>
          <div style="font-size:12px;color:var(--text-secondary)">
            当前活跃工作项：<b>${activeTasks.length}</b>
          </div>
          <div style="display:flex;flex-direction:column;gap:4px">
            ${activeTasks
              .slice(0, 3)
              .map((it) => {
                const hb = fmtRelativeAge(it.agent_heartbeat_at);
                return `<button class="btn btn-xs" style="justify-content:space-between" data-open-key="${esc(it.item_key)}">
                  <span>${esc(it.item_key)} · ${esc(it.title.slice(0, 14))}</span>
                  <span style="color:var(--text-muted)">${esc(stateMeta(it.state).label)}${it.agent_id === m.id ? " · " + hb.text : ""}</span>
                </button>`;
              })
              .join("") || `<span style="font-size:11px;color:var(--text-muted)">空闲待命</span>`}
          </div>
        </div>`;
    })
    .join("");

  const sampleAgent = state.members.find((m) => m.kind === "agent");
  const tokenLine = sampleAgent?.api_token ? `$env:LOCAL_BOARD_TOKEN="${sampleAgent.api_token}"` : `$env:LOCAL_BOARD_TOKEN="<在设置页复制 Agent Token>"`;
  const url = state.service?.local_url || "http://127.0.0.1:18800";
  $("#cli-guide-code").textContent = [
    `# 项目级技能（已内置于 .agents/skills/，对话直接触发）：`,
    `#   • workboard-item-create：口述/截图 -> 规范预览 -> 建单/整改`,
    `#   • workboard-item-exec  ：拉单 -> 方案待审 -> 实施 -> 提测/结单`,
    ``,
    `# 配置当前终端 / AI Agent 会话环境变量`,
    `$env:LOCAL_BOARD_URL="${url}"`,
    tokenLine,
    ``,
    `# 1. 规范化新建工作项（先 --dry-run 校验标题/字数/小节/路径冲突，确认后再 --check-spec 建单）`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py create --title "[缺陷] 触发条件 + 异常现象" --module "操作交互与战术标绘" --label "缺陷修复" --scope "src/交互系统/" --desc-file tmp/item.md --dry-run`,
    ``,
    `# 2. 拉取单据（自动附带模块红线、附件本机路径与路径冲突警告）`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py pull SCAV-1`,
    ``,
    `# 3. 认领并提交方案至「方案待审」等待工程师批准`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py claim SCAV-1 --branch feat/scav-1`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py submit-plan SCAV-1 --plan-file tmp/plan.md`,
    ``,
    `# 4. 推进执行、刷新心跳、提测与全项目规范巡检`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py heartbeat SCAV-1 --note "核心代码已写完，正在跑验证"`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py submit-qa SCAV-1 --note "所有测试已通过"`,
    `python -X utf8 -B scripts/工作项看板/看板工具.py lint --all`,
  ].join("\n");
}

function renderSettingsView() {
  const wrap = $("#settings-container");
  const proj = state.activeProject;
  const svc = state.service || {};
  wrap.innerHTML = `
    <div class="settings-card">
      <div class="panel-head">
        <h2>服务与局域网模式信息</h2>
        <span class="panel-sub">独立专属端口 18800，与 8800 游戏主服进程与数据彻底隔离</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-bottom:12px">
        <div class="mod-kv"><label>本机地址</label><div class="code-inline">${esc(svc.local_url || "")}</div></div>
        <div class="mod-kv"><label>局域网地址（--lan 模式生效）</label><div class="code-inline">${esc(svc.lan_url || "")}</div></div>
        <div class="mod-kv"><label>当前鉴权模式</label><div class="code-inline">${esc(svc.auth_mode || "local_single")}</div></div>
      </div>
      <div class="mod-kv">
        <label>切换局域网团队模式启动命令</label>
        <div class="code-inline">python -X utf8 -B scripts/工作项看板/启动看板服务.py restart --lan</div>
      </div>
    </div>

    ${
      proj
        ? `<div class="settings-card">
        <div class="panel-head">
          <h2>当前项目规则：${esc(proj.key)} · ${esc(proj.name)}</h2>
          <span class="panel-sub">单人可保持自由流转；局域网团队协作时建议打开严格模式</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
          <div>
            <div style="font-weight:600">严格闸门流转（strict_mode）</div>
            <div class="muted-text">开启后，将工作项打回「不通过」必须填写打回原因。</div>
          </div>
          <button id="btn-toggle-strict-settings" class="btn ${proj.strict_mode ? "btn-primary" : ""}">
            ${proj.strict_mode ? "已开启严格模式" : "当前自由流转（点击开启）"}
          </button>
        </div>
      </div>`
        : ""
    }

    <div class="settings-card">
      <div class="panel-head" style="display:flex;align-items:center;justify-content:space-between">
        <div>
          <h2>成员与 AI Agent 槽位管理</h2>
          <span class="panel-sub">支持人类成员（局域网 PIN 登录）与 AI Agent（X-Board-Token 鉴权）双轨管理</span>
        </div>
        <button id="btn-add-member" class="btn btn-primary">＋ 新增成员 / Agent</button>
      </div>
      <table class="member-table">
        <thead>
          <tr><th>名称</th><th>类型</th><th>职责</th><th>Agent Token / 接口凭据</th><th>操作</th></tr>
        </thead>
        <tbody>
          ${state.members
            .map(
              (m) => `
            <tr>
              <td><span class="avatar-dot avatar-sm" style="background:${safeColor(m.avatar_color)};margin-right:7px">${esc(m.display_name.slice(0, 1))}</span><b>${esc(m.display_name)}</b></td>
              <td>${m.kind === "agent" ? "AI Agent" : "人类成员"}</td>
              <td style="color:var(--text-secondary)">${esc(m.role || "-")}</td>
              <td><span class="code-inline">${esc(m.api_token || m.token_preview || "***")}</span></td>
              <td>
                ${m.api_token ? `<button class="btn btn-xs" data-copy-token="${esc(m.api_token)}">复制 Token</button>` : ""}
                <button class="btn btn-xs" data-rotate-token="${m.id}">重置 Token</button>
              </td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

// ---------- 右侧双栏详情抽屉（Plane Issue Detail 风格） ----------

async function openDrawer(itemKey, { silent = false } = {}) {
  if (!silent) {
    closePopovers();
    $("#drawer-backdrop").hidden = false;
    $("#item-drawer").hidden = false;
  }
  try {
    const res = await api(`/api/v1/items/${encodeURIComponent(itemKey)}`);
    state.openItemKey = res.item.item_key;
    state.openItemDetail = res.item;
    renderDrawer(res.item);
    $("#drawer-backdrop").hidden = false;
    $("#item-drawer").hidden = false;
  } catch (err) {
    if (!silent) {
      closeDrawer();
      toast(err.message, "error");
    }
  }
}

function closeDrawer() {
  state.openItemKey = null;
  state.openItemDetail = null;
  $("#drawer-backdrop").hidden = true;
  $("#item-drawer").hidden = true;
}

function renderDrawer(item) {
  const humans = state.members.filter((m) => m.kind === "human");
  const agents = state.members.filter((m) => m.kind === "agent");
  const modCtx = item.module_context;
  const st = stateMeta(item.state);

  let gateStrip = "";
  if (item.state === "plan_review") {
    gateStrip = `
      <div style="display:flex;gap:8px;padding:9px 12px;background:var(--warn-soft);border:1px solid rgba(229,169,60,0.35);border-radius:7px;align-items:center">
        <span style="color:#f3c873;font-weight:600;flex:1;font-size:12.5px">当前处于「方案待审」闸门，请审阅下方实施计划：</span>
        <button class="btn btn-success btn-xs" data-drawer-gate="approve_plan">✓ 批准开工</button>
        <button class="btn btn-danger btn-xs" data-drawer-gate="reject">↩ 打回</button>
      </div>`;
  } else if (item.state === "qa_wait") {
    gateStrip = `
      <div style="display:flex;gap:8px;padding:9px 12px;background:rgba(168,85,247,0.14);border:1px solid rgba(168,85,247,0.36);border-radius:7px;align-items:center">
        <span style="color:#e9d5ff;font-weight:600;flex:1;font-size:12.5px">当前处于「待测试」闸门：</span>
        <button class="btn btn-xs" data-drawer-gate="start_testing">开始测试</button>
        <button class="btn btn-success btn-xs" data-drawer-gate="done">✓ 验收通过</button>
        <button class="btn btn-danger btn-xs" data-drawer-gate="reject">↩ 打回</button>
      </div>`;
  }

  const conflictBox = item.conflicts?.length
    ? `<div class="conflict-pair-card">
        <strong style="font-size:12.5px">⚠ 作用域路径冲突预警</strong>
        ${item.conflicts
          .map(
            (c) => `
          <div style="font-size:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span>与 <button class="btn btn-xs" data-open-key="${esc(c.other_key)}">${esc(c.other_key)} · ${esc(c.other_title)}</button> 重叠：</span>
            <code>${c.overlaps.map((o) => `${esc(o.a)} ∩ ${esc(o.b)}`).join(", ")}</code>
          </div>`
          )
          .join("")}
      </div>`
    : "";

  const modBox = modCtx
    ? `<details style="background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:7px;padding:8px 11px">
        <summary style="cursor:pointer;font-weight:600;font-size:12px;color:#93c5fd">模块上下文包：${esc(modCtx.category || "未分类")} / ${esc(modCtx.name)}（Agent 拉单自动注入）</summary>
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px;font-size:12px">
          <div><b>红线：</b><span style="color:#fca5a5">${esc(modCtx.redlines || "无")}</span></div>
          <div><b>入口路径：</b><code class="code-inline">${esc((modCtx.entry_paths || []).join(", "))}</code></div>
          <div><b>验证命令：</b><code class="code-inline">${esc(modCtx.verify_cmd || "-")}</code></div>
        </div>
      </details>`
    : "";

  // 父级面包屑（子单顶部一键跳回父单）
  const pDetail = item.parent_detail;
  const parentCrumb = pDetail
    ? `<div class="drawer-parent-crumb">
        <span style="color:var(--text-muted);flex-shrink:0">上级父单</span>
        <button type="button" class="parent-link-btn" data-open-key="${esc(pDetail.item_key)}">
          ${renderStateIcon(pDetail.state, stateMeta(pDetail.state).color, 13)}
          <span class="crumb-key">${esc(pDetail.item_key)}</span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(pDetail.title)}</span>
        </button>
        <span class="prop-pill is-sub" style="height:20px;margin-left:auto">↳ ${pDetail.sub_done ?? 0}/${pDetail.sub_total ?? 0}</span>
        <button type="button" class="btn btn-xs" data-focus-parent="${pDetail.id}" title="在看板中仅筛选此专题">聚焦专题</button>
      </div>`
    : "";

  // 子工作项管理区（顶层工作项可拆分子项并追踪总进度）
  const children = item.children || [];
  const subTotal = children.length;
  const subDone = children.filter((c) => c.state === "done").length;
  const subPct = subTotal ? Math.round((subDone / subTotal) * 100) : 0;
  const subItemsBox = !item.parent_id
    ? `<div class="sub-items-box">
        <div class="sub-items-head">
          <label class="field-label" style="margin:0;color:var(--text-primary)">子工作项 (Sub-items)</label>
          <span class="prop-pill is-sub ${subTotal > 0 && subDone >= subTotal ? "is-all-done" : ""}" style="height:20px">${subDone} / ${subTotal}</span>
          ${subTotal > 0 ? `<button type="button" class="btn btn-xs" data-focus-parent="${item.id}">在看板聚焦</button>` : ""}
          <button type="button" class="btn btn-primary btn-xs" style="margin-left:auto" data-create-subitem="${item.id}">＋ 拆分子项</button>
        </div>
        ${subTotal > 0 ? `<div class="sub-progress-track"><div class="sub-progress-fill" style="width:${subPct}%"></div></div>` : ""}
        ${
          subTotal > 0
            ? `<div class="sub-items-list">
                ${children
                  .map((ch) => {
                    const cst = stateMeta(ch.state);
                    const cMod = ch.module_name
                      ? `<span class="prop-pill" style="height:19px;padding:0 6px"><span class="chip-dot" style="background:${safeColor(ch.module_color)}"></span><span style="max-width:96px;overflow:hidden;text-overflow:ellipsis">${esc(ch.module_name)}</span></span>`
                      : "";
                    const cBlk = ch.blocked_open_count > 0 ? `<span class="prop-pill is-blocked" style="height:19px;padding:0 5px">⊘${ch.blocked_open_count}</span>` : "";
                    const cWarn = ch.has_conflict ? `<span class="prop-pill is-conflict" style="height:19px;padding:0 5px">⚠</span>` : "";
                    return `<div class="sub-item-row" data-open-key="${esc(ch.item_key)}">
                      ${renderStateIcon(cst.key, cst.color, 13)}
                      <span class="card-key">${esc(ch.item_key)}</span>
                      <span class="sub-item-title ${ch.state === "done" ? "is-done" : ""}" title="${esc(ch.title)}">${esc(ch.title)}</span>
                      ${cMod}
                      ${cBlk}
                      ${cWarn}
                      <button type="button" class="icon-btn-xs" data-detach-child="${esc(ch.item_key)}" title="移出父单（转为顶层工作项）">✕</button>
                    </div>`;
                  })
                  .join("")}
              </div>`
            : `<div class="muted-text">尚未拆分子项；跨模块特性建议按模块拆分多个并行子项并用前置阻塞串联波次。</div>`
        }
      </div>`
    : "";

  // 同父兄弟子项速览（子单参考，防跨模块接口打架）
  const siblings = item.siblings || [];
  const siblingsBox =
    item.parent_id && siblings.length
      ? `<details style="background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:7px;padding:8px 11px">
          <summary style="cursor:pointer;font-weight:600;font-size:12px;color:#c7d2fe">同父兄弟子项（共 ${siblings.length} 项 · 便于对齐模块接口与路径）</summary>
          <div class="sub-items-list" style="margin-top:8px">
            ${siblings
              .map((sb) => {
                const sst = stateMeta(sb.state);
                return `<div class="sub-item-row" data-open-key="${esc(sb.item_key)}">
                  ${renderStateIcon(sst.key, sst.color, 12)}
                  <span class="card-key">${esc(sb.item_key)}</span>
                  <span class="sub-item-title ${sb.state === "done" ? "is-done" : ""}">${esc(sb.title)}</span>
                  ${sb.module_name ? `<span class="prop-pill" style="height:19px;padding:0 6px">${esc(sb.module_name)}</span>` : ""}
                </div>`;
              })
              .join("")}
          </div>
        </details>`
      : "";

  // 可选父工作项列表（仅同项目无父级的顶层单，排除自己）
  const parentCandidates = state.items.filter((x) => !x.parent_id && x.id !== item.id);
  const canSetParent = (item.sub_total || 0) === 0;
  state.drawerBlockers = (item.blocked_by || []).map((k) => String(k).toUpperCase());

  const stateOpts = state.states.map((s) => ({ value: s.key, label: s.label, dot: safeColor(s.color) }));
  const prioOpts = state.priorities.map((p) => ({ value: p.key, label: p.label, dot: safeColor(p.color) }));
  const parentOpts = canSetParent
    ? [
        { value: "", label: "无（顶层工作项）", dot: "#838896" },
        ...parentCandidates.map((p) => ({ value: String(p.id), label: `${p.item_key} · ${p.title.slice(0, 22)}`, dot: "#b197fc" })),
      ]
    : [{ value: "", label: `已包含 ${item.sub_total} 个子项（不可嵌套）`, dot: "#838896" }];
  const ownerOpts = [
    { value: "", label: "未指派", dot: "#838896" },
    ...humans.map((m) => ({ value: String(m.id), label: m.display_name, dot: safeColor(m.avatar_color) })),
  ];
  const agentOpts = [
    { value: "", label: "未分配 AI", dot: "#838896" },
    ...agents.map((m) => ({ value: String(m.id), label: m.display_name, dot: safeColor(m.avatar_color) })),
  ];

  $("#drawer-content").innerHTML = `
    <div class="drawer-head">
      ${renderStateIcon(st.key, st.color, 14)}
      <span class="crumb-key">${esc(item.item_key)}</span>
      <span class="mode-pill">rev #${item.rev}</span>
      <span style="font-size:11px;color:var(--text-muted);font-family:var(--font-mono)">更新 ${fmtBeijingTime(item.updated_at)}</span>
      <button id="btn-drawer-copy-prompt" class="btn btn-xs" style="margin-left:auto">${SVG_ICONS.copy} 复制 Agent 指令</button>
      <button id="btn-drawer-save" class="btn btn-primary btn-xs">保存修改</button>
      <button id="btn-drawer-delete" class="btn btn-danger btn-xs">删除</button>
      <button id="btn-drawer-close" class="icon-btn" title="关闭 (Esc)">✕</button>
    </div>

    <div class="drawer-split">
      <!-- 左侧正文、方案、子工作项、附件与活动流 -->
      <div class="drawer-main">
        ${gateStrip}
        ${conflictBox}
        ${parentCrumb}

        <input id="d-title" class="drawer-title-input" value="${esc(item.title)}" />

        <div>
          <label class="field-label">精简需求描述（What）</label>
          <textarea id="d-desc" class="textarea-ctrl" style="min-height:150px" placeholder="简明描述需求目标、现象或验收标准...">${esc(item.description_md)}</textarea>
        </div>

        <div>
          <label class="field-label">实施方案摘要（Plan — 供方案待审闸门核对）</label>
          <textarea id="d-plan" class="textarea-ctrl" style="min-height:92px" placeholder="由 AI Agent 提交或工程师补充的修改计划...">${esc(item.plan_md)}</textarea>
        </div>

        ${subItemsBox}
        ${siblingsBox}
        ${modBox}

        <div>
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
            <label class="field-label" style="margin:0">附件与截图（抽屉内可直接 <kbd class="kbd-subtle">Ctrl+V</kbd> 粘贴）</label>
            <label class="btn btn-xs" style="cursor:pointer">
              ＋ 上传文件
              <input id="d-file-input" type="file" hidden accept="image/*,.md,.txt,.json,.log,.pdf" />
            </label>
          </div>
          <div class="attachments-grid">
            ${
              (item.attachments || []).length
                ? item.attachments
                    .map((a) => {
                      const isImg = a.mime_type.startsWith("image/") && a.mime_type !== "image/svg+xml";
                      const safeUrl = safeAttachmentUrl(a.url);
                      return `<div class="att-thumb" data-preview-url="${esc(safeUrl)}" data-preview-name="${esc(a.filename)}" data-is-img="${isImg ? "1" : "0"}">
                        ${isImg && safeUrl ? `<img src="${esc(safeUrl)}" alt="${esc(a.filename)}" />` : `<div style="height:80px;display:flex;align-items:center;justify-content:center;font-size:22px">📄</div>`}
                        <div class="att-name" title="${esc(a.filename)}">${esc(a.filename)}</div>
                      </div>`;
                    })
                    .join("")
                : `<div class="muted-text">暂无附件，可直接按 Ctrl+V 粘贴截图</div>`
            }
          </div>
        </div>

        <div>
          <label class="field-label">活动与评论（北京时间 UTC+8）</label>
          <div class="comment-list" style="margin-bottom:10px">
            ${
              (item.comments || []).length
                ? item.comments
                    .map(
                      (c) => `
                  <div class="comment-item is-${esc(c.comment_type)}">
                    <div class="comment-head">
                      <span class="avatar-dot avatar-sm" style="background:${safeColor(c.author_color)}">${esc(c.author_name.slice(0, 1))}</span>
                      <b>${esc(c.author_name)}</b>
                      <span class="mode-pill">${esc(c.comment_type)}</span>
                      <span class="comment-time">${fmtBeijingTime(c.created_at)}</span>
                    </div>
                    <div class="comment-body">${esc(c.body_md)}</div>
                  </div>`
                    )
                    .join("")
                : `<div class="muted-text">暂无评论记录</div>`
            }
          </div>
          <div style="display:flex;gap:8px">
            <textarea id="d-new-comment" class="textarea-ctrl" style="min-height:58px" placeholder="写下评论、评审意见或交付摘要... (Ctrl+Enter 发送)"></textarea>
            <button id="btn-send-comment" class="btn btn-primary" style="align-self:flex-end">发送</button>
          </div>
        </div>
      </div>

      <!-- 右侧固定 296px 属性栏（全量 V2.2 .ui-select，零原生 select） -->
      <aside class="drawer-aside">
        <div class="drawer-aside-title">属性与协作元数据</div>

        <div class="prop-field">
          <label class="field-label">当前状态</label>
          ${buildUiSelectHtml({ id: "d-state", value: item.state, options: stateOpts })}
        </div>

        <div class="prop-field">
          <label class="field-label">优先级</label>
          ${buildUiSelectHtml({ id: "d-priority", value: item.priority, options: prioOpts })}
        </div>

        <div class="prop-field">
          <label class="field-label">所属父工作项（限定 2 层）</label>
          ${buildUiSelectHtml({ id: "d-parent", value: item.parent_id ? String(item.parent_id) : "", options: parentOpts, disabled: !canSetParent })}
        </div>

        <div class="prop-field">
          <label class="field-label">所属模块</label>
          ${buildUiSelectHtml({ id: "d-module", value: item.module_id ? String(item.module_id) : "", groups: buildModuleSelectGroups("无模块") })}
        </div>

        <div class="prop-field">
          <label class="field-label">人类负责人 (Owner)</label>
          ${buildUiSelectHtml({ id: "d-owner", value: item.owner_id ? String(item.owner_id) : "", options: ownerOpts })}
        </div>

        <div class="prop-field">
          <label class="field-label">AI 执行槽位 (Agent)</label>
          ${buildUiSelectHtml({ id: "d-agent", value: item.agent_id ? String(item.agent_id) : "", options: agentOpts })}
        </div>

        <div class="prop-field">
          <label class="field-label">分支 / Worktree</label>
          <input id="d-branch" class="input-ctrl mono-input" value="${esc(item.agent_branch)}" placeholder="如 feat/scav-1" />
        </div>

        <div class="prop-field" id="d-blocker-section"></div>

        <div class="prop-field">
          <label class="field-label">标签</label>
          <div class="label-toggle-row" id="d-labels">
            ${state.labels
              .map(
                (lb) => `<button type="button" class="label-toggle ${(item.label_ids || []).includes(lb.id) ? "is-selected" : ""}" data-lid="${lb.id}">
                  <span class="chip-dot" style="background:${esc(lb.color)}"></span>${esc(lb.name)}
                </button>`
              )
              .join("")}
          </div>
        </div>

        <div class="prop-field">
          <label class="field-label">作用域相对路径（每行一条）</label>
          <textarea id="d-scope" class="textarea-ctrl mono-input" style="min-height:84px" placeholder="例如：src/交互系统/">${esc((item.scope_paths || []).join("\n"))}</textarea>
        </div>
      </aside>
    </div>
  `;

  renderDrawerBlockerSection();
  bindSelectScope("drawer", $("#item-drawer"), $("#item-drawer"));
}

function renderDrawerBlockerSection() {
  const host = $("#d-blocker-section");
  const item = state.openItemDetail;
  if (!host || !item) return;

  const currentBlockerKeys = state.drawerBlockers || [];
  const blockerDetailMap = Object.fromEntries((item.blocked_by_details || []).map((b) => [b.item_key.toUpperCase(), b]));
  const blockerCandidates = state.items.filter((x) => x.id !== item.id && !currentBlockerKeys.includes(x.item_key.toUpperCase()));
  const blocksDownstream = item.blocks_details || [];

  const blockerOpts = [
    { value: "", label: "选择前置单号...", dot: "#838896" },
    ...blockerCandidates.map((x) => ({
      value: x.item_key,
      label: `${x.item_key} · ${x.title.slice(0, 18)}`,
      dot: safeColor(stateMeta(x.state).color),
    })),
  ];

  host.innerHTML = `
    <label class="field-label">前置阻塞依赖 (blocked_by)</label>
    <div id="d-blocker-list" class="dep-chip-list">
      ${
        currentBlockerKeys.length
          ? currentBlockerKeys
              .map((bk) => {
                const bd = blockerDetailMap[bk] || state.items.find((x) => x.item_key.toUpperCase() === bk);
                const bState = bd ? bd.state : "todo";
                const isDone = bState === "done";
                const stInfo = stateMeta(bState);
                return `<div class="dep-chip-row ${isDone ? "is-done" : "is-open"}" data-blocker-key="${esc(bk)}">
                  <button type="button" class="dep-chip-btn" data-open-key="${esc(bk)}" title="${esc(bd ? `${bd.item_key} (${stInfo.label}) ${bd.title}` : bk)}">
                    ${renderStateIcon(bState, stInfo.color, 11)}
                    <span class="card-key" style="color:inherit">${esc(bk)}</span>
                    <span style="overflow:hidden;text-overflow:ellipsis">${esc(bd ? bd.title : "(外部/已删除)")}</span>
                  </button>
                  <button type="button" class="icon-btn-xs" data-remove-blocker="${esc(bk)}" title="移除前置依赖">✕</button>
                </div>`;
              })
              .join("")
          : `<div class="muted-text" style="font-size:11px">无前置阻塞</div>`
      }
    </div>
    <div class="dep-add-row">
      ${buildUiSelectHtml({ id: "d-blocker-select", value: "", options: blockerOpts, placeholder: "选择前置单号..." })}
      <button type="button" id="d-blocker-add" class="btn btn-xs" style="height:32px;padding:0 10px">＋</button>
    </div>
    ${
      blocksDownstream.length
        ? `<div style="margin-top:6px">
            <div class="field-label" style="margin-bottom:4px">阻塞下游（${blocksDownstream.length}）</div>
            <div class="dep-chip-list" style="margin-bottom:0">
              ${blocksDownstream
                .map((dn) => {
                  const dSt = stateMeta(dn.state);
                  return `<div class="dep-chip-row">
                    <button type="button" class="dep-chip-btn" data-open-key="${esc(dn.item_key)}">
                      ${renderStateIcon(dn.state, dSt.color, 11)}
                      <span class="card-key">${esc(dn.item_key)}</span>
                      <span style="overflow:hidden;text-overflow:ellipsis">${esc(dn.title)}</span>
                    </button>
                  </div>`;
                })
                .join("")}
            </div>
          </div>`
        : ""
    }
  `;
}

async function saveDrawerItem({ force = false } = {}) {
  const item = state.openItemDetail;
  if (!item) return;
  const newState = $("#d-state").value;
  if (state.activeProject?.strict_mode && newState === "rejected" && item.state !== "rejected") {
    openRejectModal(item.item_key, item.rev);
    return;
  }
  const labelIds = $$("#d-labels .label-toggle.is-selected").map((b) => Number(b.dataset.lid));
  const blockedBy = [...(state.drawerBlockers || [])];
  const parentEl = $("#d-parent");
  const payload = {
    expected_rev: item.rev,
    title: $("#d-title").value.trim(),
    state: newState,
    priority: $("#d-priority").value,
    owner_id: $("#d-owner").value ? Number($("#d-owner").value) : null,
    agent_id: $("#d-agent").value ? Number($("#d-agent").value) : null,
    module_id: $("#d-module").value ? Number($("#d-module").value) : null,
    agent_branch: $("#d-branch").value.trim(),
    scope_paths: $("#d-scope").value.split("\n").map((s) => s.trim()).filter(Boolean),
    blocked_by: blockedBy,
    description_md: $("#d-desc").value,
    plan_md: $("#d-plan").value,
    label_ids: labelIds,
  };
  if (parentEl && parentEl.dataset.disabled !== "1") {
    payload.parent_id = parentEl.value ? Number(parentEl.value) : null;
  }
  if (force) {
    payload.force = true;
  }
  try {
    const res = await api(`/api/v1/items/${encodeURIComponent(item.item_key)}`, { method: "PATCH", body: payload });
    state.openItemDetail = res.item;
    renderDrawer(res.item);
    await loadActiveProject();
    toast(`已保存 ${item.item_key} (rev #${res.item.rev})`, "success");
  } catch (err) {
    if (err.data?.reason_code === "incomplete_children" && !force) {
      if (confirm(`${err.message}\n\n是否忽略未完成子项并强制结单？`)) {
        return saveDrawerItem({ force: true });
      }
      return;
    }
    if (err.status === 409) {
      toast("检测到并发修改冲突，已自动刷新最新内容", "warn");
      await openDrawer(item.item_key);
      await loadActiveProject();
    } else {
      toast(err.message, "error");
    }
  }
}

async function uploadAttachmentFile(itemKey, file) {
  const reader = new FileReader();
  const dataUrl = await new Promise((resolve, reject) => {
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  await api(`/api/v1/items/${encodeURIComponent(itemKey)}/attachments`, {
    method: "POST",
    body: {
      filename: file.name || `screenshot-${Date.now()}.png`,
      mime_type: file.type || "image/png",
      data_base64: dataUrl,
    },
  });
  toast("附件上传成功（SHA-256 已去重托管）", "success");
  await openDrawer(itemKey);
  await loadActiveProject();
}

// ---------- 弹窗表单 ----------

function showModal(html) {
  closePopovers();
  $("#modal-box").innerHTML = html;
  $("#modal-root").hidden = false;
  bindSelectScope("modal", $("#modal-box"), $("#modal-box"));
}
function closeModal() {
  if (selectBinders.modal) {
    selectBinders.modal.destroy();
    selectBinders.modal = null;
  }
  $("#modal-root").hidden = true;
  $("#modal-box").innerHTML = "";
}

function openCreateItemModal(defaults = {}) {
  const defaultState = defaults.state || "todo";
  const defaultPrio = defaults.priority || "medium";
  const defaultModId = defaults.moduleId ?? state.filters.moduleId ?? null;
  const defaultParentId = defaults.parentId ?? (state.filters.parentId ? Number(state.filters.parentId) : null);
  const humans = state.members.filter((m) => m.kind === "human");
  const agents = state.members.filter((m) => m.kind === "agent");
  const topParents = getTopLevelParents();
  const selectedLabelIds = new Set();
  const presetBodies = new Set(ITEM_SPEC_TEMPLATES.map((t) => t.body.trim()));
  let activeTplKey = "";

  const stateOpts = state.states.map((s) => ({ value: s.key, label: s.label, dot: safeColor(s.color) }));
  const prioOpts = state.priorities.map((p) => ({ value: p.key, label: p.label, dot: safeColor(p.color) }));
  const agentOpts = [
    { value: "", label: "暂不分配", dot: "#838896" },
    ...agents.map((m) => ({ value: String(m.id), label: m.display_name, dot: safeColor(m.avatar_color) })),
  ];
  const parentOpts = [
    { value: "", label: "无（顶层工作项）", dot: "#838896" },
    ...topParents.map((p) => ({ value: String(p.id), label: `${p.item_key} · ${p.title.slice(0, 22)}`, dot: "#b197fc" })),
  ];

  showModal(`
    <div class="modal-header">
      <h3>${defaultParentId ? "拆分子工作项" : "新建工作项"} (${esc(state.activeProject?.key || "")})</h3>
      <button class="icon-btn-xs" data-close-modal>✕</button>
    </div>
    <div class="modal-body">
      <div>
        <div class="spec-label-row">
          <label class="field-label" style="margin:0">一键套用统一编写模板（人机同标准）</label>
          <span class="spec-hint">只写 What，方案交由 Agent 在「方案待审」拟定</span>
        </div>
        <div id="m-tpl-bar" class="template-bar">
          ${ITEM_SPEC_TEMPLATES.map(
            (t) => `<button type="button" class="template-pill" data-tpl="${t.key}">
              <span class="dot" style="background:${t.color}"></span>
              <span>${esc(t.prefix)} ${esc(t.name)}</span>
            </button>`
          ).join("")}
        </div>
      </div>
      <div>
        <div class="spec-label-row">
          <label class="field-label" style="margin:0">标题 *</label>
          <span id="m-title-count" class="spec-counter">0 / 32 字</span>
        </div>
        <input id="m-title" class="input-ctrl" placeholder="[类型] 对象/场景 + 核心目标或异常现象（≤ 32 字）" />
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label class="field-label">初始列</label>
          ${buildUiSelectHtml({ id: "m-state", value: defaultState, options: stateOpts })}
        </div>
        <div>
          <label class="field-label">优先级</label>
          ${buildUiSelectHtml({ id: "m-prio", value: defaultPrio, options: prioOpts })}
        </div>
        <div>
          <label class="field-label">所属模块</label>
          ${buildUiSelectHtml({ id: "m-mod", value: defaultModId ? String(defaultModId) : "", groups: buildModuleSelectGroups("暂不指定") })}
        </div>
        <div>
          <label class="field-label">指派 AI Agent（可选）</label>
          ${buildUiSelectHtml({ id: "m-agent", value: "", options: agentOpts })}
        </div>
        <div>
          <label class="field-label">所属父工作项（可选，2 层结构）</label>
          ${buildUiSelectHtml({ id: "m-parent", value: defaultParentId ? String(defaultParentId) : "", options: parentOpts })}
        </div>
        <div>
          <label class="field-label">前置阻塞单号（可选，逗号分隔）</label>
          <input id="m-blocked-by" class="input-ctrl mono-input" placeholder="如 SCAV-1, SCAV-2" />
        </div>
      </div>
      ${
        state.labels.length
          ? `<div>
              <label class="field-label">标签（从项目已有标签选配 1~2 个）</label>
              <div id="m-labels" class="label-toggle-row">
                ${state.labels
                  .map(
                    (lb) => `<button type="button" class="label-toggle" data-create-label="${lb.id}">
                      <span class="chip-dot" style="background:${esc(lb.color)}"></span>${esc(lb.name)}
                    </button>`
                  )
                  .join("")}
              </div>
            </div>`
          : ""
      }
      <div>
        <label class="field-label">预计涉及相对路径（非待规划建议填 1~3 条，激活多 Agent 路径防撞）</label>
        <textarea id="m-scope" class="textarea-ctrl mono-input" style="min-height:52px" placeholder="例如：src/交互系统/指令与标绘层.mjs"></textarea>
      </div>
      <div>
        <div class="spec-label-row">
          <label class="field-label" style="margin:0">精简需求描述（What）</label>
          <span id="m-desc-count" class="spec-counter">0 字 · 建议 150~350 字</span>
        </div>
        <textarea id="m-desc" class="textarea-ctrl" style="min-height:132px" placeholder="点击上方类型按钮可一键填入标准骨架（只写 What，禁贴源码方案）"></textarea>
      </div>
      <input type="hidden" id="m-owner" value="${humans[0]?.id || ""}" />
    </div>
    <div class="modal-footer">
      <button class="btn" data-close-modal>取消</button>
      <button id="btn-submit-item" class="btn btn-primary">创建工作项</button>
    </div>
  `);

  const titleEl = $("#m-title");
  const descEl = $("#m-desc");
  const titleCountEl = $("#m-title-count");
  const descCountEl = $("#m-desc-count");

  const refreshCounters = () => {
    const tLen = titleEl.value.trim().length;
    titleCountEl.textContent = `${tLen} / 32 字`;
    titleCountEl.classList.toggle("is-warn", tLen > 32 && tLen <= 42);
    titleCountEl.classList.toggle("is-danger", tLen > 42);

    const dLen = countSpecEffectiveChars(descEl.value);
    descCountEl.textContent = `${dLen} 字 · 建议 150~350 字`;
    descCountEl.classList.toggle("is-warn", dLen > 420 && dLen <= 650);
    descCountEl.classList.toggle("is-danger", dLen > 650);
  };

  const syncLabelButtons = () => {
    $$("#m-labels [data-create-label]").forEach((btn) => {
      const lid = Number(btn.dataset.createLabel);
      btn.classList.toggle("is-selected", selectedLabelIds.has(lid));
    });
  };

  $$("#m-labels [data-create-label]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const lid = Number(btn.dataset.createLabel);
      if (selectedLabelIds.has(lid)) selectedLabelIds.delete(lid);
      else selectedLabelIds.add(lid);
      syncLabelButtons();
    });
  });

  $$("#m-tpl-bar [data-tpl]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tpl = ITEM_SPEC_TEMPLATES.find((x) => x.key === btn.dataset.tpl);
      if (!tpl) return;
      const curDesc = descEl.value.trim();
      if (curDesc && !presetBodies.has(curDesc) && activeTplKey !== tpl.key) {
        if (!confirm(`切换为「${tpl.prefix}」模板将替换当前正文内容，是否继续？`)) return;
      }
      activeTplKey = tpl.key;
      $$("#m-tpl-bar [data-tpl]").forEach((b) => b.classList.toggle("is-active", b.dataset.tpl === tpl.key));

      // 自动替换或补齐标题前缀
      const rawTitle = titleEl.value.trim().replace(/^\[[^\]]+\]\s*/, "");
      titleEl.value = `${tpl.prefix} ${rawTitle}`;
      titleEl.placeholder = tpl.titleHint;

      // 填充规范正文骨架
      descEl.value = tpl.body;

      // 自动选中对应默认标签（仅在项目已存在该标签时勾选）
      const defaultNames = new Set(ITEM_SPEC_TEMPLATES.map((x) => x.defaultLabel));
      state.labels.forEach((lb) => {
        if (defaultNames.has(lb.name)) selectedLabelIds.delete(lb.id);
        if (lb.name === tpl.defaultLabel) selectedLabelIds.add(lb.id);
      });
      syncLabelButtons();
      refreshCounters();
      titleEl.focus();
      titleEl.setSelectionRange(titleEl.value.length, titleEl.value.length);
    });
  });

  titleEl.addEventListener("input", refreshCounters);
  descEl.addEventListener("input", refreshCounters);
  refreshCounters();
  titleEl.focus();

  $("#btn-submit-item").onclick = async () => {
    const title = titleEl.value.trim();
    if (!title) return toast("请输入工作项标题", "warn");
    const blockedBy = $("#m-blocked-by")
      .value.replace(/，/g, ",")
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const parentVal = $("#m-parent").value;
    try {
      const res = await api(`/api/v1/projects/${state.activeProjectId}/items`, {
        method: "POST",
        body: {
          title,
          state: $("#m-state").value,
          priority: $("#m-prio").value,
          parent_id: parentVal ? Number(parentVal) : null,
          module_id: $("#m-mod").value ? Number($("#m-mod").value) : null,
          owner_id: $("#m-owner").value ? Number($("#m-owner").value) : null,
          agent_id: $("#m-agent").value ? Number($("#m-agent").value) : null,
          label_ids: Array.from(selectedLabelIds),
          scope_paths: $("#m-scope").value.split("\n").map((s) => s.trim()).filter(Boolean),
          blocked_by: blockedBy,
          description_md: descEl.value,
        },
      });
      closeModal();
      await loadActiveProject();
      toast(`已创建 ${res.item.item_key}`, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

function openRejectModal(itemKey, expectedRev) {
  showModal(`
    <div class="modal-header">
      <h3>打回 ${esc(itemKey)} 至「不通过」</h3>
      <button class="icon-btn-xs" data-close-modal>✕</button>
    </div>
    <div class="modal-body">
      <label class="field-label">请填写打回原因（将记录到活动评论并提示对应 Agent）</label>
      <textarea id="m-reject-reason" class="textarea-ctrl" placeholder="例如：回归测试未覆盖 21:9 画幅，请补充验证后重新提测"></textarea>
    </div>
    <div class="modal-footer">
      <button class="btn" data-close-modal>取消</button>
      <button id="btn-confirm-reject" class="btn btn-danger">确认打回</button>
    </div>
  `);
  $("#m-reject-reason").focus();
  $("#btn-confirm-reject").onclick = async () => {
    const reason = $("#m-reject-reason").value.trim();
    if (state.activeProject?.strict_mode && !reason) {
      return toast("严格模式下必须填写打回原因", "warn");
    }
    try {
      await api(`/api/v1/items/${encodeURIComponent(itemKey)}/transition`, {
        method: "POST",
        body: { action: "reject", reason, expected_rev: expectedRev },
      });
      closeModal();
      await loadActiveProject();
      toast(`已打回 ${itemKey}`, "warn");
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

function openCreateProjectModal() {
  showModal(`
    <div class="modal-header"><h3>新建协作项目</h3><button class="icon-btn-xs" data-close-modal>✕</button></div>
    <div class="modal-body">
      <div><label class="field-label">项目代号（1~12 位字母数字，如 RX003）*</label><input id="p-key" class="input-ctrl mono-input" placeholder="RX003" /></div>
      <div><label class="field-label">项目名称 *</label><input id="p-name" class="input-ctrl" placeholder="例如：新项目研发看板" /></div>
      <div><label class="field-label">项目简介</label><textarea id="p-desc" class="textarea-ctrl" style="min-height:60px"></textarea></div>
    </div>
    <div class="modal-footer">
      <button class="btn" data-close-modal>取消</button>
      <button id="btn-submit-project" class="btn btn-primary">创建项目</button>
    </div>
  `);
  $("#btn-submit-project").onclick = async () => {
    try {
      const res = await api("/api/v1/projects", {
        method: "POST",
        body: { key: $("#p-key").value.trim(), name: $("#p-name").value.trim(), description: $("#p-desc").value.trim() },
      });
      closeModal();
      state.activeProjectId = res.project.id;
      await loadBootstrapAndProject();
      toast(`已创建项目 ${res.project.key}`, "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

function openModuleModal(existing = null) {
  const catOptions = Array.from(
    new Set([
      ...state.moduleCategories.map((c) => c.name),
      ...state.modules.map((m) => m.category || "未分类"),
    ])
  );
  showModal(`
    <div class="modal-header"><h3>${existing ? "编辑模块上下文包" : "新增模块上下文包"}</h3><button class="icon-btn-xs" data-close-modal>✕</button></div>
    <div class="modal-body">
      <div style="display:grid;grid-template-columns:1fr 1fr 78px;gap:10px">
        <div>
          <label class="field-label">所属分类（可选或自定义）*</label>
          <input id="mod-category" list="mod-cat-datalist" class="input-ctrl" value="${esc(existing?.category || state.activeModuleTab || "核心玩法与战斗")}" placeholder="选择或输入分类名" />
          <datalist id="mod-cat-datalist">${catOptions.map((c) => `<option value="${esc(c)}"></option>`).join("")}</datalist>
        </div>
        <div><label class="field-label">模块名称 *</label><input id="mod-name" class="input-ctrl" value="${esc(existing?.name || "")}" /></div>
        <div><label class="field-label">标识色</label><input id="mod-color" type="color" class="input-ctrl" style="height:32px;padding:2px" value="${esc(existing?.color || "#6366f1")}" /></div>
      </div>
      <div><label class="field-label">一句话简介</label><input id="mod-summary" class="input-ctrl" value="${esc(existing?.summary || "")}" /></div>
      <div><label class="field-label">职责范围</label><textarea id="mod-scope" class="textarea-ctrl" style="min-height:56px">${esc(existing?.scope_desc || "")}</textarea></div>
      <div><label class="field-label">现有能力</label><textarea id="mod-caps" class="textarea-ctrl" style="min-height:56px">${esc(existing?.capabilities || "")}</textarea></div>
      <div><label class="field-label">架构红线（AI 拉单必读约束）</label><textarea id="mod-redlines" class="textarea-ctrl" style="min-height:56px">${esc(existing?.redlines || "")}</textarea></div>
      <div><label class="field-label">入口相对路径（每行一条）</label><textarea id="mod-paths" class="textarea-ctrl mono-input" style="min-height:52px">${esc((existing?.entry_paths || []).join("\n"))}</textarea></div>
      <div><label class="field-label">标准验证命令</label><input id="mod-vcmd" class="input-ctrl mono-input" value="${esc(existing?.verify_cmd || "")}" /></div>
    </div>
    <div class="modal-footer">
      <button class="btn" data-close-modal>取消</button>
      <button id="btn-submit-module" class="btn btn-primary">保存模块</button>
    </div>
  `);
  $("#btn-submit-module").onclick = async () => {
    const body = {
      category: $("#mod-category").value.trim() || "未分类",
      name: $("#mod-name").value.trim(),
      color: $("#mod-color").value,
      summary: $("#mod-summary").value.trim(),
      scope_desc: $("#mod-scope").value.trim(),
      capabilities: $("#mod-caps").value.trim(),
      redlines: $("#mod-redlines").value.trim(),
      entry_paths: $("#mod-paths").value.split("\n").map((s) => s.trim()).filter(Boolean),
      verify_cmd: $("#mod-vcmd").value.trim(),
    };
    try {
      if (existing) {
        await api(`/api/v1/modules/${existing.id}`, { method: "PATCH", body });
      } else {
        await api(`/api/v1/projects/${state.activeProjectId}/modules`, { method: "POST", body });
      }
      closeModal();
      await loadActiveProject();
      toast("模块上下文包已保存", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

function openAddMemberModal() {
  showModal(`
    <div class="modal-header"><h3>新增成员 / AI Agent 槽位</h3><button class="icon-btn-xs" data-close-modal>✕</button></div>
    <div class="modal-body">
      <div><label class="field-label">显示名称 *</label><input id="mem-name" class="input-ctrl" placeholder="例如：同事A 或 DeepSeek-架构" /></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <label class="field-label">身份类型</label>
          ${buildUiSelectHtml({
            id: "mem-kind",
            value: "agent",
            options: [
              { value: "agent", label: "AI Agent 槽位", dot: "#b197fc" },
              { value: "human", label: "人类成员", dot: "#f0d372" },
            ],
          })}
        </div>
        <div><label class="field-label">头像颜色</label><input id="mem-color" type="color" class="input-ctrl" style="height:32px;padding:2px" value="#f0d372" /></div>
      </div>
      <div><label class="field-label">职责说明</label><input id="mem-role" class="input-ctrl" placeholder="例如：后端开发 / 前端联调" /></div>
      <div><label class="field-label">局域网登录口令 PIN（仅人类成员需要，默认 1234）</label><input id="mem-pin" class="input-ctrl" placeholder="4~8 位数字或字符" /></div>
    </div>
    <div class="modal-footer">
      <button class="btn" data-close-modal>取消</button>
      <button id="btn-submit-member" class="btn btn-primary">确认新增</button>
    </div>
  `);
  $("#btn-submit-member").onclick = async () => {
    try {
      await api("/api/v1/members", {
        method: "POST",
        body: {
          display_name: $("#mem-name").value.trim(),
          kind: $("#mem-kind").value,
          role: $("#mem-role").value.trim(),
          avatar_color: $("#mem-color").value,
          pin: $("#mem-pin").value.trim(),
        },
      });
      closeModal();
      await loadBootstrapAndProject();
      toast("成员已添加并自动生成 API Token", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };
}

// ---------- 事件绑定 ----------

function switchView(viewName) {
  state.activeView = viewName;
  closePopovers();
  renderProjectTree();
  renderSubHeader();
  renderActiveFilterBar();
  syncActiveViewPanes();
}

function toggleSidebar() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  localStorage.setItem("lb_sidebar_collapsed", state.sidebarCollapsed ? "1" : "0");
  applySidebarState();
}

function clearFilterKey(key) {
  if (key === "all") {
    state.filters = { q: "", stateKey: "", priority: "", memberId: "", category: "", moduleId: null, parentId: null, conflictOnly: false };
    $("#search-input").value = "";
    $("#btn-clear-search").hidden = true;
  } else if (key === "q") {
    state.filters.q = "";
    $("#search-input").value = "";
    $("#btn-clear-search").hidden = true;
  } else if (key === "stateKey") {
    state.filters.stateKey = "";
  } else if (key === "module") {
    state.filters.category = "";
    state.filters.moduleId = null;
  } else if (key === "parentId") {
    state.filters.parentId = null;
  } else if (key === "conflictOnly") {
    state.filters.conflictOnly = false;
  } else {
    state.filters[key] = "";
  }
  renderAll();
}

function bindStaticEvents() {
  // 侧边栏折叠
  $("#btn-sidebar-toggle-top").addEventListener("click", toggleSidebar);

  // 侧边栏项目树 & 视图导航
  $("#sidebar").addEventListener("click", async (e) => {
    const viewBtn = e.target.closest("[data-view]");
    if (viewBtn) {
      switchView(viewBtn.dataset.view);
      return;
    }
    const projBtn = e.target.closest("[data-proj-id]");
    if (projBtn) {
      const pid = Number(projBtn.dataset.projId);
      if (pid !== state.activeProjectId) {
        state.activeProjectId = pid;
        state.filters.stateKey = "";
        state.filters.moduleId = null;
        state.filters.category = "";
        state.filters.parentId = null;
        await loadActiveProject();
      } else {
        switchView("items");
      }
    }
  });

  $("#btn-new-project").addEventListener("click", openCreateProjectModal);
  $("#btn-new-item").addEventListener("click", () => openCreateItemModal({ state: "todo" }));
  $("#btn-new-module").addEventListener("click", () => openModuleModal(null));

  // 分段视图切换（看板 / 列表）
  $$(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.display.layout = btn.dataset.layout;
      saveDisplayPrefs();
      renderSubHeader();
      syncActiveViewPanes();
    });
  });

  // 三个 Popover 开关
  $("#btn-actor-trigger").addEventListener("click", (e) => {
    e.stopPropagation();
    setPopover("actor");
  });
  $("#btn-filter-popover").addEventListener("click", (e) => {
    e.stopPropagation();
    setPopover("filter");
  });
  $("#btn-display-popover").addEventListener("click", (e) => {
    e.stopPropagation();
    setPopover("display");
  });

  // 操作视角 Popover 交互
  $("#actor-popover").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-select-actor]");
    if (!btn) return;
    state.actorOverrideId = Number(btn.dataset.selectActor) || null;
    localStorage.setItem("lb_actor_id", String(state.actorOverrideId || ""));
    renderActorTrigger();
    closePopovers();
    toast(`当前操作视角已切换为 ${memberById(state.actorOverrideId)?.display_name || ""}`, "info");
  });

  // 筛选 Popover 交互
  $("#filter-popover").addEventListener("click", (e) => {
    const clearBtn = e.target.closest("[data-clear-filter]");
    if (clearBtn) return clearFilterKey(clearBtn.dataset.clearFilter);
    const pBtn = e.target.closest("[data-set-priority]");
    if (pBtn) {
      state.filters.priority = state.filters.priority === pBtn.dataset.setPriority ? "" : pBtn.dataset.setPriority;
      return renderAll();
    }
    const mBtn = e.target.closest("[data-set-member]");
    if (mBtn) {
      state.filters.memberId = String(state.filters.memberId) === mBtn.dataset.setMember ? "" : mBtn.dataset.setMember;
      return renderAll();
    }
    const catBtn = e.target.closest("[data-set-category]");
    if (catBtn) {
      const cat = catBtn.dataset.setCategory;
      if (state.filters.category === cat && !state.filters.moduleId) {
        state.filters.category = "";
      } else {
        state.filters.category = cat;
        state.filters.moduleId = null;
      }
      return renderAll();
    }
    const modBtn = e.target.closest("[data-set-module]");
    if (modBtn) {
      const mid = Number(modBtn.dataset.setModule);
      state.filters.category = "";
      state.filters.moduleId = state.filters.moduleId === mid ? null : mid;
      return renderAll();
    }
    const parBtn = e.target.closest("[data-set-parent]");
    if (parBtn) {
      const pid = Number(parBtn.dataset.setParent);
      state.filters.parentId = state.filters.parentId === pid ? null : pid;
      return renderAll();
    }
    if (e.target.closest("[data-toggle-conflict-filter]")) {
      state.filters.conflictOnly = !state.filters.conflictOnly;
      return renderAll();
    }
  });

  // 显示 Popover 交互
  $("#display-popover").addEventListener("click", (e) => {
    const gBtn = e.target.closest("[data-set-groupby]");
    if (gBtn) {
      state.display.groupBy = gBtn.dataset.setGroupby;
      state.manualCollapsedCols.clear();
      state.manualExpandedCols.clear();
      saveDisplayPrefs();
      return renderAll();
    }
    const tBtn = e.target.closest("[data-toggle-display]");
    if (tBtn) {
      const k = tBtn.dataset.toggleDisplay;
      state.display[k] = !state.display[k];
      if (k === "autoCollapseEmpty") {
        state.manualCollapsedCols.clear();
        state.manualExpandedCols.clear();
      }
      saveDisplayPrefs();
      return renderAll();
    }
    const fBtn = e.target.closest("[data-toggle-field]");
    if (fBtn) {
      const fk = fBtn.dataset.toggleField;
      state.display.fields[fk] = !state.display.fields[fk];
      saveDisplayPrefs();
      return renderAll();
    }
    const actBtn = e.target.closest("[data-cols-action]");
    if (actBtn) {
      const act = actBtn.dataset.colsAction;
      state.manualCollapsedCols.clear();
      state.manualExpandedCols.clear();
      if (act === "expand-all") {
        state.display.autoCollapseEmpty = false;
        saveDisplayPrefs();
      } else {
        state.display.autoCollapseEmpty = true;
        saveDisplayPrefs();
      }
      return renderAll();
    }
  });

  // 活跃筛选条点击清除
  $("#active-filter-bar").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-clear-filter]");
    if (btn) clearFilterKey(btn.dataset.clearFilter);
  });

  // 顶部全局搜索
  $("#search-input").addEventListener("input", (e) => {
    state.filters.q = e.target.value;
    $("#btn-clear-search").hidden = !state.filters.q;
    renderSubHeader();
    renderActiveFilterBar();
    renderBoard();
    renderListView();
  });
  $("#btn-clear-search").addEventListener("click", () => clearFilterKey("q"));

  // 模块分类页签
  $("#modules-cat-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-mod-tab]");
    if (!tab) return;
    state.activeModuleTab = tab.dataset.modTab;
    renderModulesView();
  });

  // 快速切换项目严格模式
  $("#strict-pill").addEventListener("click", async () => {
    if (!state.activeProject) return;
    try {
      await api(`/api/v1/projects/${state.activeProject.id}`, {
        method: "PATCH",
        body: { strict_mode: !state.activeProject.strict_mode },
      });
      await loadActiveProject();
      toast(state.activeProject.strict_mode ? "已开启严格模式：打回必须填原因" : "已切换为自由流转", "info");
    } catch (err) {
      toast(err.message || "切换严格模式失败", "error");
    }
  });

  // 退出局域网登录
  $("#btn-logout").addEventListener("click", async () => {
    await fetch("/api/v1/auth/logout", { method: "POST" });
    location.reload();
  });

  // 局域网登录提交
  $("#btn-login-submit").addEventListener("click", async () => {
    const mid = Number($("#login-member-select").value);
    const pin = $("#login-pin-input").value;
    const errEl = $("#login-error");
    errEl.hidden = true;
    const resp = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: mid, pin }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      errEl.textContent = data.message || "登录失败";
      errEl.hidden = false;
      return;
    }
    $("#login-overlay").hidden = true;
    await loadBootstrapAndProject();
    connectSSE();
  });

  // 看板交互、列折叠与拖拽流转（支持向折叠竖条 Drop！）
  const kanban = $("#kanban-columns");
  kanban.addEventListener("click", async (e) => {
    const parentJump = e.target.closest("[data-open-parent-key]");
    if (parentJump) {
      e.stopPropagation();
      openDrawer(parentJump.dataset.openParentKey);
      return;
    }
    const collapseBtn = e.target.closest("[data-collapse-col]");
    if (collapseBtn) {
      e.stopPropagation();
      const cid = collapseBtn.dataset.collapseCol;
      state.manualExpandedCols.delete(cid);
      state.manualCollapsedCols.add(cid);
      renderBoard();
      return;
    }
    const expandCol = e.target.closest("[data-expand-col]");
    if (expandCol) {
      const cid = expandCol.dataset.expandCol;
      state.manualCollapsedCols.delete(cid);
      state.manualExpandedCols.add(cid);
      renderBoard();
      return;
    }
    const copyBtn = e.target.closest("[data-copy-prompt]");
    if (copyBtn) {
      e.stopPropagation();
      const it = state.items.find((x) => x.item_key === copyBtn.dataset.copyPrompt);
      if (it) copyText(buildAgentPrompt(it), `已复制 ${it.item_key} Agent 开工指令`);
      return;
    }
    const gateBtn = e.target.closest("[data-gate-action]");
    if (gateBtn) {
      e.stopPropagation();
      const key = gateBtn.dataset.itemKey;
      const act = gateBtn.dataset.gateAction;
      const it = state.items.find((x) => x.item_key === key);
      if (act === "reject") return openRejectModal(key, it?.rev);
      const execGate = async (force = false) => {
        try {
          await api(`/api/v1/items/${encodeURIComponent(key)}/transition`, {
            method: "POST",
            body: { action: act, expected_rev: it?.rev, ...(force ? { force: true } : {}) },
          });
          await loadActiveProject();
          toast(`${key} 状态已更新`, "success");
        } catch (err) {
          if (err.data?.reason_code === "incomplete_children" && !force) {
            if (confirm(`${err.message}\n\n是否忽略未完成子项并强制结单？`)) {
              return execGate(true);
            }
            return;
          }
          toast(err.message, "error");
        }
      };
      await execGate(false);
      return;
    }
    const qAdd = e.target.closest("[data-quick-add-type]");
    if (qAdd) {
      const gt = qAdd.dataset.quickAddType;
      const gv = qAdd.dataset.quickAddVal;
      const defaults = {};
      if (gt === "state") defaults.state = gv;
      else if (gt === "priority") defaults.priority = gv;
      else if (gt === "module") defaults.moduleId = gv ? Number(gv) : null;
      else if (gt === "parent") defaults.parentId = gv ? Number(gv) : null;
      openCreateItemModal(defaults);
      return;
    }
    const card = e.target.closest("[data-card-key]");
    if (card) {
      openDrawer(card.dataset.cardKey);
    }
  });

  kanban.addEventListener("dragstart", (e) => {
    const card = e.target.closest("[data-card-key]");
    if (!card) return;
    state.dragItemKey = card.dataset.cardKey;
    card.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", state.dragItemKey);
  });
  kanban.addEventListener("dragend", (e) => {
    const card = e.target.closest("[data-card-key]");
    if (card) card.classList.remove("is-dragging");
    $$(".kanban-col.is-dragover").forEach((c) => c.classList.remove("is-dragover"));
    state.dragItemKey = null;
  });
  kanban.addEventListener("dragover", (e) => {
    const col = e.target.closest(".kanban-col");
    if (!col || !state.dragItemKey) return;
    e.preventDefault();
    $$(".kanban-col.is-dragover").forEach((c) => {
      if (c !== col) c.classList.remove("is-dragover");
    });
    col.classList.add("is-dragover");
  });
  kanban.addEventListener("drop", async (e) => {
    const col = e.target.closest(".kanban-col");
    if (!col || !state.dragItemKey) return;
    e.preventDefault();
    col.classList.remove("is-dragover");

    const item = state.items.find((x) => x.item_key === state.dragItemKey);
    if (!item) return;

    const gt = col.dataset.groupType;
    const gv = col.dataset.groupValue;
    const cid = col.dataset.colId;
    const body = { expected_rev: item.rev };
    let msgLabel = "";

    if (gt === "state") {
      if (item.state === gv) return;
      if (state.activeProject?.strict_mode && gv === "rejected") {
        openRejectModal(item.item_key, item.rev);
        return;
      }
      body.state = gv;
      msgLabel = stateMeta(gv).label;
    } else if (gt === "priority") {
      if (item.priority === gv) return;
      body.priority = gv;
      msgLabel = `优先级：${priorityMeta(gv).label}`;
    } else if (gt === "module") {
      const targetModId = gv ? Number(gv) : null;
      if ((item.module_id || null) === targetModId) return;
      body.module_id = targetModId;
      msgLabel = targetModId ? `模块：${moduleById(targetModId)?.name || ""}` : "未指定模块";
    } else if (gt === "parent") {
      const targetParentId = gv ? Number(gv) : null;
      if (targetParentId === item.id || (item.parent_id || null) === targetParentId) return;
      body.parent_id = targetParentId;
      msgLabel = targetParentId ? `归入父单 ${itemById(targetParentId)?.item_key || ""}` : "移出父单（独立顶层）";
    }

    const execDrop = async (force = false) => {
      try {
        await api(`/api/v1/items/${encodeURIComponent(item.item_key)}`, {
          method: "PATCH",
          body: force ? { ...body, force: true } : body,
        });
        state.manualCollapsedCols.delete(cid);
        await loadActiveProject();
        toast(`${item.item_key} → ${msgLabel}`, "success");
      } catch (err) {
        if (err.data?.reason_code === "incomplete_children" && !force) {
          if (confirm(`${err.message}\n\n是否忽略未完成子项并强制结单？`)) {
            return execDrop(true);
          }
          await loadActiveProject();
          return;
        }
        toast(err.message, err.status === 409 ? "warn" : "error");
        await loadActiveProject();
      }
    };
    await execDrop(false);
  });

  // 全局点击委托
  document.addEventListener("click", async (e) => {
    if (state.openPopover && !e.target.closest(".popover-anchor")) {
      closePopovers();
    }
    if (e.target.closest("[data-jump-agents]")) {
      switchView("agents");
      return;
    }
    const pulseStateBtn = e.target.closest("[data-pulse-state]");
    if (pulseStateBtn) {
      const sk = pulseStateBtn.dataset.pulseState;
      state.filters.stateKey = state.filters.stateKey === sk ? "" : sk;
      state.manualCollapsedCols.delete(`state:${sk}`);
      switchView("items");
      renderAll();
      return;
    }
    if (e.target.closest("[data-close-modal]") || e.target === $("#modal-root")) {
      closeModal();
      return;
    }
    const focusParentBtn = e.target.closest("[data-focus-parent]");
    if (focusParentBtn) {
      e.stopPropagation();
      state.filters.parentId = Number(focusParentBtn.dataset.focusParent) || null;
      closeDrawer();
      switchView("items");
      renderAll();
      return;
    }
    const openParentBtn = e.target.closest("[data-open-parent-key]");
    if (openParentBtn) {
      e.stopPropagation();
      openDrawer(openParentBtn.dataset.openParentKey);
      return;
    }
    const openKeyBtn = e.target.closest("[data-open-key]");
    if (openKeyBtn) {
      openDrawer(openKeyBtn.dataset.openKey);
      return;
    }
    const editModBtn = e.target.closest("[data-edit-module]");
    if (editModBtn) {
      const m = moduleById(Number(editModBtn.dataset.editModule));
      if (m) openModuleModal(m);
      return;
    }
    if (e.target.closest("#btn-add-member")) {
      openAddMemberModal();
      return;
    }
    if (e.target.closest("#btn-toggle-strict-settings") && state.activeProject) {
      try {
        await api(`/api/v1/projects/${state.activeProject.id}`, {
          method: "PATCH",
          body: { strict_mode: !state.activeProject.strict_mode },
        });
        await loadActiveProject();
      } catch (err) {
        toast(err.message || "更新项目模式失败", "error");
      }
      return;
    }
    const copyTok = e.target.closest("[data-copy-token]");
    if (copyTok) {
      copyText(copyTok.dataset.copyToken, "已复制 Agent API Token");
      return;
    }
    const rotTok = e.target.closest("[data-rotate-token]");
    if (rotTok) {
      try {
        await api(`/api/v1/members/${rotTok.dataset.rotateToken}`, { method: "PATCH", body: { rotate_token: true } });
        await loadBootstrapAndProject();
        toast("API Token 已轮换更新", "success");
      } catch (err) {
        toast(err.message || "轮换 Token 失败", "error");
      }
    }
  });

  // 抽屉事件委托
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  $("#item-drawer").addEventListener("click", async (e) => {
    const item = state.openItemDetail;
    if (!item) return;
    if (e.target.closest("#btn-drawer-sync-remote")) return openDrawer(item.item_key);
    if (e.target.closest("#btn-drawer-close")) return closeDrawer();
    if (e.target.closest("#btn-drawer-save")) return saveDrawerItem();
    if (e.target.closest("#btn-drawer-copy-prompt")) return copyText(buildAgentPrompt(item), `已复制 ${item.item_key} Agent 开工指令`);
    const createSubBtn = e.target.closest("[data-create-subitem]");
    if (createSubBtn) {
      e.stopPropagation();
      openCreateItemModal({ state: "todo", parentId: Number(createSubBtn.dataset.createSubitem) });
      return;
    }
    const detachBtn = e.target.closest("[data-detach-child]");
    if (detachBtn) {
      e.stopPropagation();
      const childKey = detachBtn.dataset.detachChild;
      if (!confirm(`确定将子项 ${childKey} 从 ${item.item_key} 中移出并转为独立顶层工作项吗？`)) return;
      try {
        await api(`/api/v1/items/${encodeURIComponent(childKey)}`, { method: "PATCH", body: { parent_id: null } });
        await openDrawer(item.item_key);
        await loadActiveProject();
        toast(`已将 ${childKey} 移出父工作项`, "info");
      } catch (err) {
        toast(err.message || "解除父子关系失败", "error");
      }
      return;
    }
    if (e.target.closest("#d-blocker-add")) {
      e.stopPropagation();
      const sel = $("#d-blocker-select");
      const val = (sel?.value || "").trim().toUpperCase();
      if (!val) return toast("请先选择前置工作项单号", "warn");
      if (!state.drawerBlockers.includes(val)) {
        state.drawerBlockers.push(val);
        renderDrawerBlockerSection();
        bindSelectScope("drawer", $("#item-drawer"), $("#item-drawer"));
        toast(`已加入前置依赖 ${val}，请点击「保存修改」生效`, "info");
      }
      return;
    }
    const remBlkBtn = e.target.closest("[data-remove-blocker]");
    if (remBlkBtn) {
      e.stopPropagation();
      const bk = (remBlkBtn.dataset.removeBlocker || "").toUpperCase();
      state.drawerBlockers = (state.drawerBlockers || []).filter((x) => x !== bk);
      renderDrawerBlockerSection();
      bindSelectScope("drawer", $("#item-drawer"), $("#item-drawer"));
      toast(`已移除前置依赖 ${bk}，请点击「保存修改」生效`, "info");
      return;
    }
    if (e.target.closest("#btn-drawer-delete")) {
      const subWarn = item.sub_total > 0 ? `\n（注意：其下 ${item.sub_total} 个子工作项将被保留并自动转为顶层工作项）` : "";
      if (!confirm(`确认永久删除工作项 ${item.item_key} 吗？${subWarn}`)) return;
      try {
        await api(`/api/v1/items/${encodeURIComponent(item.item_key)}`, { method: "DELETE" });
        closeDrawer();
        await loadActiveProject();
        toast(`已删除 ${item.item_key}`, "warn");
      } catch (err) {
        toast(err.message || "删除工作项失败", "error");
      }
      return;
    }
    const lbBtn = e.target.closest(".label-toggle");
    if (lbBtn) {
      lbBtn.classList.toggle("is-selected");
      return;
    }
    const gateBtn = e.target.closest("[data-drawer-gate]");
    if (gateBtn) {
      const act = gateBtn.dataset.drawerGate;
      if (act === "reject") return openRejectModal(item.item_key, item.rev);
      const execDrawerGate = async (force = false) => {
        try {
          await api(`/api/v1/items/${encodeURIComponent(item.item_key)}/transition`, {
            method: "POST",
            body: { action: act, expected_rev: item.rev, ...(force ? { force: true } : {}) },
          });
          await loadActiveProject();
          toast(`${item.item_key} 已流转`, "success");
        } catch (err) {
          if (err.data?.reason_code === "incomplete_children" && !force) {
            if (confirm(`${err.message}\n\n是否忽略未完成子项并强制结单？`)) {
              return execDrawerGate(true);
            }
            return;
          }
          toast(err.message || "状态流转失败", err.status === 409 ? "warn" : "error");
          if (err.status === 409) await loadActiveProject();
        }
      };
      await execDrawerGate(false);
      return;
    }
    if (e.target.closest("#btn-send-comment")) {
      const body = $("#d-new-comment").value.trim();
      if (!body) return;
      try {
        await api(`/api/v1/items/${encodeURIComponent(item.item_key)}/comments`, { method: "POST", body: { body_md: body } });
        $("#d-new-comment").value = "";
        await openDrawer(item.item_key);
        await loadActiveProject();
      } catch (err) {
        toast(err.message || "发送评论失败", "error");
      }
      return;
    }
    const att = e.target.closest(".att-thumb");
    if (att) {
      const url = safeAttachmentUrl(att.dataset.previewUrl);
      if (!url) return;
      if (att.dataset.isImg === "1") {
        showModal(`
          <div class="modal-header"><h3>${esc(att.dataset.previewName)}</h3><button class="icon-btn-xs" data-close-modal>✕</button></div>
          <div class="modal-body" style="align-items:center">
            <img src="${esc(url)}" style="max-width:100%;max-height:70vh;border-radius:6px" />
          </div>
          <div class="modal-footer"><a class="btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">新标签打开原图</a><button class="btn btn-primary" data-close-modal>关闭</button></div>
        `);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    }
  });

  $("#item-drawer").addEventListener("change", async (e) => {
    if (e.target.id === "d-file-input" && e.target.files?.[0] && state.openItemKey) {
      try {
        await uploadAttachmentFile(state.openItemKey, e.target.files[0]);
      } catch (err) {
        toast(err.message, "error");
      }
      e.target.value = "";
    }
  });

  // 抽屉内直接 Ctrl+V 粘贴截图
  document.addEventListener("paste", async (e) => {
    if (!state.openItemKey || $("#item-drawer").hidden) return;
    const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
    if (!files.length) return;
    e.preventDefault();
    try {
      await uploadAttachmentFile(state.openItemKey, files[0]);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  // 快捷键：/ 或 Ctrl+K 搜索，C 新建，B 折叠侧栏，Esc 按 Code SSOT V2.2 LIFO 栈逐层退出，Ctrl+Enter 发评论
  document.addEventListener("keydown", (e) => {
    const inField = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (e.key === "Escape") {
      if (escStack.handleKeydown(e)) return;
      if (!$("#modal-root").hidden) return closeModal();
      if (state.openPopover) return closePopovers();
      if (!$("#item-drawer").hidden) return closeDrawer();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      $("#search-input").focus();
      $("#search-input").select();
      return;
    }
    if (!inField && e.key === "/") {
      e.preventDefault();
      $("#search-input").focus();
      return;
    }
    if (!inField && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === "c") {
        e.preventDefault();
        openCreateItemModal({ state: "todo" });
        return;
      }
      if (k === "b") {
        e.preventDefault();
        toggleSidebar();
        return;
      }
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && document.activeElement?.id === "d-new-comment") {
      $("#btn-send-comment")?.click();
    }
  });
}

boot().catch((err) => {
  console.error(err);
  toast(`初始化失败：${err.message}`, "error");
});
