---
name: daily-dev-log
description: >-
  Scavenger 项目每日开发日志采集、目标提炼与 V4 可视化看板生成工作流。
  当工程师请求“记录每日开发日志”、“生成今天/昨天的工作日志”、“制作开发看板”、
  “总结昨天做了什么”、“更新每日开发看板”或明确调用 daily-dev-log 时触发。
---

# Scavenger 每日开发日志（daily-dev-log）

本技能指导 Agent 按**北京时间（`Asia/Shanghai / UTC+8`）**采集项目当日/昨日跨会话开发事实，提炼为“目标导向”的结构化 JSON，并编译更新唯一离线可视化看板 [每日开发看板.html](../../../reports/开发日志/每日开发看板.html)。

## 核心规范边界

- **唯一时区口径**：所有统计窗口与文件名严格按 **北京时间（`UTC+8`）** `00:00:00 ~ 23:59:59` 计算（切勿直接沿用本机美西 `-08:00` 日期）。
- **唯一目录与命名**（由 [检查项目结构.py](../../../scripts/检查项目结构.py) 强制校验）：
  - 总看板入口：`reports/开发日志/每日开发看板.html`（禁止新建 `index.html` 或每日单独 `.html`）。
  - 数据文件：`reports/开发日志/数据/YYYY-MM/YYYY-MM-DD-开发日志.json`（禁止空格或自定义后缀）。
  - 卡片编号：页面自动生成跨天唯一编号 `MMDD-01`（如 `1122-01`）。
- **脱敏要求**：正式 JSON 与看板中严禁写入本机用户目录绝对路径、Steam 安装绝对路径或凭据；仅使用项目相对路径。

## 标准执行步骤

### 1. 确定目标日期与登记保护
- 若工程师未指定日期，默认取**北京时间昨日**（`yesterday`）；若说“今天”，取**北京时间今日**（`today`）。
- 按 `AGENTS.md` 启动前先运行清理状态核对并登记本轮独立任务保护：
  ```powershell
  python -X utf8 -B scripts/维护清理/清理工具.py status
  python -X utf8 -B scripts/维护清理/清理工具.py protect --task 每日开发日志-YYYYMMDD-<会话短ID> --path reports/开发日志 --path tmp/每日工作快照
  ```

### 2. 采集客观事实快照（只读）
运行 [生成每日工作快照.py](../../../scripts/生成每日工作快照.py) 获取 Antigravity + Codex 主会话轨迹、正式目录改动、`local-data/` 交付聚合、清理登记与 Git 快照：
```powershell
python -X utf8 -B scripts/生成每日工作快照.py --date YYYY-MM-DD --format json
```
- 如需核对某条会话的完整结论或量化数据，可按快照中的会话 ID 与修改文件补读对应文档或报告，确保数据 100% 有据可查、不脑补。

### 3. 提炼目标并写入每日 JSON
- 按 [数据契约与编写规范](./references/数据契约与编写规范.md) 将零散对话合并提炼为 **目标卡片（`goals`）**、**待拍板/验收行动项（`actions`）** 与 **当日总括（`headline`）**。
- 写入 `reports/开发日志/数据/YYYY-MM/YYYY-MM-DD-开发日志.json`，并将 `"mode"` 设为 `"curated"`，确保 `"date"` 字段与文件名日期严格一致。
- **减负铁律**：每张卡片 `summary` 控制在 1~2 句人话（≤ 90 字），`metrics` 放 1~2 个最硬核量化对比，`highlights` 放 2~3 条简短要点（供点击右侧抽屉查看，不在卡面堆字）。

### 4. 编译更新离线可视化看板
运行 [生成每日工作日志页面.py](../../../scripts/生成每日工作日志页面.py) 将 `数据/` 下全部历史 JSON 合并编译进唯一总看板：
```powershell
python -X utf8 -B scripts/生成每日工作日志页面.py
```

### 5. 校验与收口交付
1. 运行全仓门禁校验目录命名、JSON 字段与本地链接：
   ```powershell
   python -X utf8 -B scripts/检查项目结构.py
   ```
2. 用无头 Chrome（Playwright）快速打开 `reports/开发日志/每日开发看板.html`，确认当日日期标签、三列泳道卡片数量与编号（`MMDD-01`）正常且 `pageerror` / `console.error` 为空。
3. 若本轮未产生需留存的临时文件，完成校验后立即 `close` 本轮保护任务并用 `status --task` 核对；若向 `tmp/每日工作快照/` 写入了快照草稿，先 `enroll --kind cache` 冻结登记再 `close`。
4. 回复工程师时直接给出 [每日开发看板.html](../../../reports/开发日志/每日开发看板.html) 可点击链接、当日目标达成与待拍板精简速览，以及校验与任务状态。
