# 操作交互系统（`src/交互系统/`）

本模块承载 Scavenger 正式主入口 `http://127.0.0.1:8800`（`/play`、`/editor` 及 `/armory` 编队联动）的 RTS/RTT 战术操作交互闭环，彻底解耦 [预览入口.html](../程序化地图/预览入口.html) 的内联键鼠与镜头状态机，并严格遵循 **Code SSOT V2.2**（`/ui/*` 单源）与 **680p 复古微缩 Stylized** 渲染基线。

## 模块结构与职责

| 文件 | 核心职责 |
| :--- | :--- |
| [契约与仲裁.mjs](契约与仲裁.mjs) | CQS 命令/结果契约、纯规则计划/调度器（支持 `plannedRows` 预览结果直传复用与地面多点 `attackGroundQueue` 追加/覆写/清空）、5 大分类独立部署配额（`DEPLOY_CATEGORY_QUOTAS` 合计 `MAX_HERO_UNITS = 50`）、模式仲裁（`play`/`editor`/`targeting`/`deploy`/`wheel`/`paused`）、5 级 LIFO `ESC` 优先级栈与 5 类战术光标 |
| [镜头控制.mjs](镜头控制.mjs) | 正交战术镜头平滑阻尼、`ResizeObserver` 宿主尺寸缓存与 `forcedAspect` 短路零回流、`projectAirAnchorToGroundFocus` 三维空中视线地面焦点投影、鼠标地面锚点缩放（`65m ~ 9000m`）、中键/`WASD`/小地图拖拽平移（持续跟随期间改用 `followOffset` 偏移叠加而绝不退出跟随）、`Q/E` 及 `Alt+拖` 旋转（实时保持跟随居中）、右键雷达跨区下令、可打断 320ms `flyTo`、`resetFollowOffset`（`F` 键归中）与 `setView` |
| [选区与编组.mjs](选区与编组.mjs) | 多选集合与主焦点、真实 3D 机身/名牌/L1~L3 图标与密集簇点选、`#scv-drag-box` 矩形框选、双击同类、`Shift` 增减、50ms 悬停同步、`Tab` 重叠循环、`1~6` 固定编组快捷槽、`F` 空中三维投影居中聚焦并归零跟随偏移、`G` 持久跟随开关（移动/旋转保持跟随、切选自动接力，仅再按 `G`/`R`/清选区退出）与 `Shift/Alt+1~9` 编组 |
| [指令与标绘层.mjs](指令与标绘层.mjs) | 原生 DPR 高清 2D 标绘层 `<canvas>`（无 680p 滤镜；内部缓存语义调色盘 + `ResizeObserver` 脏标同步零热路径回流）、方案 2A《Ebbtide》极细高清空间投影线（`1.0px` 淡白微点高度垂线、`1.25px` 渐隐珊瑚红 3D 攻击斜线、`1.25px` 淡紫蓝盘旋切线赶路路径线、`1.25px` 细红矩形杀伤盒 `drawEbbtideKillBox` 与微十字细红准星）、固定翼与地面攻地全队列编号靶环与 `1→2→3→4...` 串联虚线（`queuedStrikeMarkerCount`）及 `Shift` 悬停串联虚线+`已排/余弹` 微标、右键预览与朝向线框幽灵、`Shift` 多段移动队列、固定翼贴地主范围圈、`isFixedWingOrbiting` 绕圈态 35° 机头转向弧、4A 全武器射程双环、地对地三态投影圈、V2.2 方案 A 8 槽连续扇区 `.ui-wheel` 轮盘（按住 `Space` 无限外延甩选、轻按单击兼容、边角自动内推与按键原点防误触、冷却/禁用实时拦截） |
| [战场适配器.mjs](战场适配器.mjs) | 对接 [场景运行.mjs](../程序化地图/场景运行.mjs) 窄接口，统一提供零回流 `ResizeObserver` 缓存 3D 投影、`getUnitFocus`/`getUnitAnchor`、`pickUnits`、`queryGroundAt`、CQS 命令规划/调度与 `cancelUnits` |
| [入口接入.mjs](入口接入.mjs) | 统一导出 `createTacticalInteraction(...)`、`enterAirStrikeTargeting(weaponSpec)`、`getAirStrikePreview()`、`commitPendingMultiDrop()` 与 `dispose()`，接管画布热区、固定翼全武器（`point_target`/`drag_line`/`multi_point`）与地面强制攻地（`K`）按住 `Shift` 连续加选排队/松开末点退出/右键 `Esc` 保留排队退出及实时 `shiftHeld` 同步、纯固定翼右键拖拽自定义盘旋半径、Q12-A `Space` 按住甩选/轻按单击/失焦安全取消与同源命令栏桥接、`I` 键详情卡、`B` 键部署面板与 `deploy`、`U` 键三态画幅循环，并挂载 `window.__SCV_INTERACTION__` |

## 核心键鼠映射与零冲突约定

- **选择**：左键单击（点选机身/名牌）、左键拖动 `> 5px`（屏幕框选）、左键双击 `< 350ms`（视口内同类）、`Shift` 增减、`Tab` / `Shift+Tab`（重叠单位正反向循环）。
- **指挥**：右键按住预览（拖动设朝向并展开横队，松开提交；`Shift+右键` 追加多段队列；`Esc` 取消未提交预览）、`X`（清空移动队列：地停/旋悬/固盘）、`Space`（按住呼出方案 A 8 槽轮盘并朝任意方向无限外延甩选、松开触发；轻按不越死区保留单击模式；回中心/`Esc`/失焦取消）、`K` 或 `Alt+A`（进入/退出强制攻地 `targeting` 态，彻底避开 `WASD` 左移冲突）。
- **镜头**：中键拖动 / `WASD` / 方向键平移（自动解除跟随）、`Q/E` / `Alt+左/中键` 旋转（保留跟随）、滚轮鼠标地面锚点缩放、小地图左键点击/按住拖动平移、`F` 单次聚焦、`G` 开关单车持续跟随、`R` 复位主跑道视角。
- **编队**：`1~6` 快选出击队列槽位（350ms 内连按居中聚焦）；`Shift+1~9` 保存自定义编组、`Alt+1~9` 召回编组并同步频道过滤。
- **5 级 LIFO `ESC` 撤退**：`未提交预览/框选` → `攻地/编辑笔刷` → `战术轮盘/下拉菜单` → `右侧编辑/左侧名册抽屉` → `4A 二分暂停菜单`。

## 验证命令

```powershell
# 1. 纯规则单元测试（Node ESM，无 DOM / Three.js 依赖）
node scripts/交互系统/验证交互规则.mjs

# 2. 双浏览器端到端专项验证（Chrome + Edge，连接 http://127.0.0.1:8800）
python -X utf8 -B scripts/交互系统/验证操作交互.py --base-url http://127.0.0.1:8800 --channel both --run run-v001

# 3. 全仓规范门禁
python -X utf8 -B scripts/检查项目结构.py
```

- 载具交互行为全程保持静默，不触发 `AudioContext` / UI 提示音效。
