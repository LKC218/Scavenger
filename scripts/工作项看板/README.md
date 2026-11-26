# 本地工作项看板独立服务与 CLI 工具

独立于 Scavenger 游戏主服（`127.0.0.1:8800`）的轻量本地/局域网工作项看板服务，**专属端口 `18800`**，仅使用 Python 3.11+ 标准库与 SQLite WAL，零第三方依赖。

完整设计见 [本地工作项看板-实施计划](../../docs/研发协作/本地工作项看板-实施计划.md)，前端界面源码见 [src/工作项看板/](../../src/工作项看板/README.md)。

## 文件清单

- [数据库.py](数据库.py)：SQLite WAL 数据库初始化、Schema、预置示例数据、乐观锁更新与路径重叠冲突检测。
- [启动看板服务.py](启动看板服务.py)：专属 `18800` 独立 HTTP/SSE 服务、附件托管、单人/局域网双鉴权模式与防占口启停控制。
- [看板工具.py](看板工具.py)：面向多 AI Agent 与终端的 CLI 客户端，直接调用同源 `/api/v1/*` 接口。
- [验证看板闭环.py](验证看板闭环.py)：隔离临时端口与临时数据库的端到端自动化验证脚本。
- [启动工作任务看板.cmd](../../启动工作任务看板.cmd)：项目根目录 Windows 一键快捷启动脚本（双击前台留窗常驻、自动打开浏览器、支持 `[O]/[L]/[R]/[Q]` 单键控制、关窗即优雅停服，并自动接管后台实例与保护已有前台窗口；带参数时完整透传子命令）。

## 常用启动命令

```powershell
# 0. Windows 推荐方式：直接双击项目根目录「启动工作任务看板.cmd」（或命令行透传参数）
.\启动工作任务看板.cmd
.\启动工作任务看板.cmd --lan
.\启动工作任务看板.cmd status
.\启动工作任务看板.cmd stop

# 1. 单人本机前台启动并打开浏览器（默认 127.0.0.1:18800，免登录）
python -X utf8 -B scripts/工作项看板/启动看板服务.py serve --open --panel

# 2. 后台常驻启动 / 查看状态 / 优雅停止 / 重启
python -X utf8 -B scripts/工作项看板/启动看板服务.py start
python -X utf8 -B scripts/工作项看板/启动看板服务.py status
python -X utf8 -B scripts/工作项看板/启动看板服务.py stop
python -X utf8 -B scripts/工作项看板/启动看板服务.py restart

# 3. 切换局域网团队模式（监听 0.0.0.0:18800 + 启用成员 PIN 与 Agent Token 鉴权）
python -X utf8 -B scripts/工作项看板/启动看板服务.py --lan --open

# 4. 运行隔离自动化闭环验证
python -X utf8 -B scripts/工作项看板/验证看板闭环.py
```

## AI Agent CLI 常用命令

```powershell
# 查看项目与成员元数据
python -X utf8 -B scripts/工作项看板/看板工具.py meta

# 拉取单据完整需求、附件、模块上下文包与冲突提示
python -X utf8 -B scripts/工作项看板/看板工具.py pull SCAV-1

# 新建工作项（支持规范模板文件、标签、依赖、外置排查线索与 --dry-run 门禁预演）
python -X utf8 -B scripts/工作项看板/看板工具.py create --title "[缺陷] 21:9画幅下固定翼投弹圈偏移" --module "操作交互与战术标绘" --priority high --label "缺陷修复,UI/UX" --scope "src/交互系统/" --desc-file tmp/item.md --dry-run
python -X utf8 -B scripts/工作项看板/看板工具.py create --title "[缺陷] 21:9画幅下固定翼投弹圈偏移" --module "操作交互与战术标绘" --priority high --label "缺陷修复,UI/UX" --scope "src/交互系统/" --desc-file tmp/item.md --comment "源码线索..." --check-spec

# 存量工作项只读规范体检与增量更新
python -X utf8 -B scripts/工作项看板/看板工具.py lint --all
python -X utf8 -B scripts/工作项看板/看板工具.py update SCAV-1 --add-label "需评审" --add-scope "src/交互系统/契约与仲裁.mjs"

# 认领 -> 提交方案待审 -> 批准开工 -> 汇报心跳 -> 提交测试 -> 结单
python -X utf8 -B scripts/工作项看板/看板工具.py claim SCAV-1 --agent "Claude-主程" --branch "feat/scav-1"
python -X utf8 -B scripts/工作项看板/看板工具.py submit-plan SCAV-1 --plan-file tmp/plan.md
python -X utf8 -B scripts/工作项看板/看板工具.py approve-plan SCAV-1
python -X utf8 -B scripts/工作项看板/看板工具.py heartbeat SCAV-1 --note "已完成核心改动，准备跑验证"
python -X utf8 -B scripts/工作项看板/看板工具.py submit-qa SCAV-1 --note "单测与结构检查均通过"
python -X utf8 -B scripts/工作项看板/看板工具.py done SCAV-1 --note "工程师人工验收通过"
```

> 项目级 AI 技能已内置于 [workboard-item-create](../../.agents/skills/workboard-item-create/SKILL.md)（统一编写规范与预览建单）与 [workboard-item-exec](../../.agents/skills/workboard-item-exec/SKILL.md)（拉单执行与人机双闸门闭环），局域网模式或需指定 Agent 身份时可配置 `LOCAL_BOARD_URL` 与 `LOCAL_BOARD_TOKEN`。

## 运行数据边界

全部运行态数据统一写入已由 `.gitignore` 忽略的 `local-data/工作项看板/`：
- `local-data/工作项看板/数据/workboard.db`（SQLite WAL 主库）
- `local-data/工作项看板/附件/`（SHA-256 内容寻址附件）
- `local-data/工作项看板/运行状态/`（`server-18800.json` 与滚动日志）
