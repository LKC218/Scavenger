---
name: workboard-item-exec
description: >-
  Scavenger 本地工作项看板（专属端口 18800）的指定工作项拉取、模块红线与路径冲突核对、方案待审、实施心跳与提测/验收闭环技能。
  当工程师指定“处理 SCAV-1”、“认领本地工作项”、“拉取看板任务并出方案”、“提测/结单 SCAV-1”
  或明确调用 workboard-item-exec 时触发。
---

# Scavenger 工作项拉取与人机双闸门执行流（workboard-item-exec）

本技能指导 AI Agent 严格围绕本地 18800 看板的**8 列人机双闸门状态流**、**13 模块上下文包**、**作用域路径防撞雷达**与 `AGENTS.md` **版本退役清理保护**完成单据执行闭环。

状态机细则、方案模板与标签驱动策略见 [状态流转与协作规程](./references/状态流转与协作规程.md)。

---

## 核心规则（必须严格遵守）

1. **只处理工程师明确指定的工作项**：
   - 必须由工程师给出明确单号（如 `SCAV-1`）或明确授权认领范围；**严禁**擅自扫描并动手修改未点名的 `todo` / `backlog` 单据。
2. **拉单必看四要素（需求 + 截图 + 模块上下文包 + 父兄上下文）**：
   - 执行 `看板工具.py pull <KEY>` 后，若输出包含附件本机路径（`local-data/工作项看板/附件/...`），**必须调用 `read_file` 查看所有截图**，避免漏掉视觉或交互细节。
   - 必须通读随单自动注入的 `module_context`（职责范围、现有能力、**架构红线**、入口路径、验证命令），严禁违背模块红线。
   - 若为**子工作项**，必须同步阅读 `pull` 自动带出的 **`[所属父工作项]` 总目标**与 **`[同父兄弟子项]` 状态及作用域**，确保接口契约、命名与数据结构与兄弟模块对齐，不越界改动兄弟单负责的文件。
   - 若为**父工作项**，存在未完成子项时看板会默认拦截 `done` 结单；须先确认所有子项已验收通过（仅在工程师明确要求收口时才允许带 `--force` 强制结单）。
3. **路径防撞与清理保护双前置**：
   - 若 `pull` 报出 `[⚠ 作用域路径冲突预警]` 或存在未完成前置依赖（`blocked_by`），必须先评估冲突面并向工程师说明，切勿盲目并发改同一文件。
   - 动手前按 `AGENTS.md` 登记本轮独立清理保护任务（`清理工具.py protect --task <主题-版本-会话短ID> ...`），并用 `status --task` 核对。
4. **强制「方案待审」人工闸门（严禁未批先改）**：
   - 定位源码并核对红线后，执行 `claim` 认领并执行 `submit-plan --plan-file` 将实施方案写入 `plan_md`，工作项停在 **`plan_review`（方案待审）**。
   - **暂停并等待工程师明确批准方案后**，才允许推进至 `in_progress` 并修改业务代码。
5. **标签驱动策略 + 实时心跳汇报**：
   - 获批后执行 `approve-plan` 进入 `in_progress`（若原挂有 `需评审` 标签可顺带移除）。
   - 较长任务在里程碑节点执行 `heartbeat` 刷新分支与进展，避免看板超时灰显；遇外部阻塞或需求歧义时暂停改码，用 `comment` 说明卡点并挂 `需评审` 标签。
6. **验证达标再提测，人工验收后再结单**：
   - 改码完成后，必须跑通所属模块的 `verify_cmd` 与 `python -X utf8 -B scripts/检查项目结构.py`，再通过 `submit-qa` 流转至 `qa_wait`（待测试），附改动摘要、校验结果与必要对比截图（`attach`）。
   - **严禁 AI 自行将任务流转至 `done`**：仅在工程师明确确认人工验收通过后，才执行 `done`，并同步完成 `清理工具.py` 对应的 `stage/accept/close` 交接说明。

---

## 标准执行步骤

### 第 1 步：拉取指定工作项并核对依赖、冲突与截图

```powershell
python -X utf8 -B scripts/工作项看板/看板工具.py pull SCAV-1
```

- 检查 `blocked_by_details`（前置单是否已 `done`）与 `conflicts`（是否与进行中单据路径重叠）。
- 对 `[附件列表]` 中的每一张截图，使用 `read_file` 读取本机路径核验界面细节。
- 按 `AGENTS.md` 检查清理状态并登记本轮独立任务保护：
  ```powershell
  python -X utf8 -B scripts/维护清理/清理工具.py status
  python -X utf8 -B scripts/维护清理/清理工具.py protect --task SCAV-1-实施-V001-<会话短ID> --path <涉及目录>
  ```

### 第 2 步：结合模块上下文定位源码并提交「方案待审」

- 按模块 `entry_paths` 定位源码，核对 `redlines`（如 `680p` 基线、`ba-source-ssot-v1`、`Code SSOT V2.2`、`8800` 单端口、`18800` 独立隔离等）。
- 按 [状态流转与协作规程](./references/状态流转与协作规程.md) 起草精简 `plan_md`（含根因/现状、拟改文件、红线核对、验证方式；命中 `需评审` 标签时必须给 ≥ 2 套方案对比）。
- 认领并提交方案至 `plan_review`：
  ```powershell
  python -X utf8 -B scripts/工作项看板/看板工具.py claim SCAV-1 --branch "feat/scav-1" --scope "<补充的精确相对路径>"
  python -X utf8 -B scripts/工作项看板/看板工具.py submit-plan SCAV-1 --plan-file "<临时方案文件>"
  ```
- 在对话中向工程师清晰汇报实施方案并**暂停等待确认**。

### 第 3 步：获批开工与执行心跳

工程师明确批准后：
```powershell
python -X utf8 -B scripts/工作项看板/看板工具.py approve-plan SCAV-1 --note "工程师已批准方案，开始实施"
# 若原工作项带有「需评审」标签，顺带移除
python -X utf8 -B scripts/工作项看板/看板工具.py update SCAV-1 --remove-label "需评审"
```

编码与阶段推进期间适时刷新心跳：
```powershell
python -X utf8 -B scripts/工作项看板/看板工具.py heartbeat SCAV-1 --branch "feat/scav-1" --note "已完成核心改造，正在运行模块校验"
```

### 第 4 步：执行验证并提交「待测试」

- 运行模块 `verify_cmd` 与全仓结构门禁 `python -X utf8 -B scripts/检查项目结构.py`。
- 涉及 UI/视觉/交互改动或原单附带问题截图时，上传修复后验证截图：
  ```powershell
  python -X utf8 -B scripts/工作项看板/看板工具.py attach SCAV-1 "<修复后截图路径>"
  ```
- 流转至 `qa_wait`（待测试）并汇报给工程师验收：
  ```powershell
  python -X utf8 -B scripts/工作项看板/看板工具.py submit-qa SCAV-1 --note "改动文件：...；验证通过：模块 verify_cmd + 检查项目结构.py"
  ```

### 第 5 步：工程师验收通过后结单收口

- 若工程师打回（`rejected`），读取打回原因并在 `in_progress` 补齐修复后重新 `submit-qa`。
- 工程师明确回复验收通过后，执行结单并按 `AGENTS.md` 处理清理任务（有旧版退役先 `accept`，无持续依赖再 `close`）：
  ```powershell
  python -X utf8 -B scripts/工作项看板/看板工具.py done SCAV-1 --note "工程师人工验收通过"
  ```
- 若本轮改动改变了所属模块的对外能力、架构红线或验证命令，主动提醒工程师是否同步更新看板模块上下文包与 [apps-code-map.md](../../../docs/apps-code-map.md)。
