# 全局 UI/UX 共享规范源（Code SSOT V2.2）

本目录是项目所有运行期界面与交互的**单一样式、交互与标绘数据源**，已按 [单端口整合与正式开发规范-实施计划](../../docs/单端口整合与正式开发规范-实施计划.md) 收口升级至 **V2.2（A3 烟炭双金 × B2 战术微描边 × C4 外柔内锐 × 全套共享 UI 组件 × 680p 复古微缩基线）** 正式体系，对应规范说明见 [全局 UI/UX 规范](../../docs/全局UI-UX规范.md)。

## 文件职责

| 文件 | 职责 | 消费端引用路由 |
| --- | --- | --- |
| `令牌.css` | 三层 A3 烟炭双金色阶、7 维零撞色语义通道、C4 倒角、4px 栅格、V2.1 `[data-ui-size]` 原生三档物理尺码令牌组（`--ui-sz-*`）、5 级 Z-Index 与动效时长令牌 | `/ui/令牌.css` |
| `组件.css` | 战斗 HUD 高透实底舱、菜单毛玻璃、L1/L2/L3 按钮、`.ui-select`（自动 `.is-dropup`）、输入框 `.ui-input`/`.is-error`、开关 `.ui-toggle`、方案 A 8 槽连续扇区军工仪表盘轮盘 `.ui-wheel`/`.ui-wheel-svg`/`.ui-wheel-slot`/`.ui-wheel-center`、跨页共享载具参数详情卡与兵牌悬停精简浮卡（`.ui-vd-card`/`.ui-vd-hover`）、模态窗/暂停 `.ui-modal`/`.ui-pause-grid`、分级 Toast、空态/骨架屏、占领条、4C 三行安全区 HUD 骨架、V2.2 实战扩展基元（`.ui-banner-card`、`.ui-rating-pips`、`.ui-cmd-shell-paged` / `.ui-cmd-viewport` / `.ui-cmd-exit-layer` / `.ui-cmd-pager` / `.ui-cmd-pager-indicator` 与全页统一方案 B 分区命令盘 `.ui-cmd-deck-b`/`.ui-cmd-wep-grid`/`.ui-cmd-wep-card`/`.ui-cmd-util-bar`/`.ui-cmd-util-btn`）、全站统一左上角返回大厅胶囊（`.ui-back-home` / `.ui-back-home--hud`）及品牌组合标样式（`.ui-brand-lockup` / `.ui-brand-svg.is-brand-animated`） | `/ui/组件.css` |
| `场内视觉.css` | 五层空间秩序、12 兵种通道着色、贴地双环、车底去框透视名牌、`680p` 暖滤镜对照及三级碰撞原地降级态 | `/ui/场内视觉.css` |
| `交互控件.mjs` | 共享 `createEscStack`（严格 LIFO `ESC` 栈）、`bindUiSelect`/`bindAllUiSelects`、`createUiSizeController`、`loadUiPrefs`/`saveUiPrefs`（含独立 `vehicleAudioEnabled`/`vehicleAudioVolume` 与 680p 复古微缩移轴景深 `dofMode`）、`createToastManager`、`syncCmdPagerIndicator`（就地更新翻页滑块指示条）、`runCmdPageTransition`（可中断 `translateY` 方向性切页过渡）、方案 A 8 槽战术轮盘引擎（`TACTICAL_WHEEL_CONFIG`/`resolveWheelSectorFromVector`/`clampWheelVisualCenter`/`canCommitWheelSlot`/`renderTacticalWheel`/`renderTacticalWheelRay`）、跨页共享载具详情组件（`renderVehicleDetailCard`/`renderVehicleHoverCard`/`attachDeckHoverCardController`）与 Web Audio `playUiSfx` | `/ui/交互控件.mjs` |
| `图标与标绘.mjs` | **5 大程序化矢量资产族**唯一源码：正式品牌标识（`BRAND_IDENTITY` / `BRAND_LOGO_LAYERS` 生化肃清三楔盾，`createBrandLogoSvg` / `createBrandLockup` / `buildBrandFaviconSvgString` / `drawBrandLogoCanvas`）、12 类武器库兵种剪影（含 `tank/heli/plane` 兼容别名）、固定 4 大通用武器剪影（`WEAPON_ICONS`：`cannon`/`rocket`/`missile`/`bomb`）、4 枚 ISO 情景警示图标、26 枚 HUD 战术功能图标、线框幽灵标绘、车底防叠字引擎、4 级视距 `span` 战术图标分层与簇聚合及固定翼贴地范围圈/35°转向虚弧 | `/ui/图标与标绘.mjs` |
| `favicon.svg` | 全站唯一浏览器标签图标单源（生化肃清三楔盾 24×24 光学精简版，由 `启动主服务.py` 等服务端动态读取分发） | `/favicon.svg`、`/ui/favicon.svg` |
| `index.html`、`规范页.css`、`规范页.mjs` | 统一主端口 `http://127.0.0.1:8800/ui-spec` 活体规范展示台（含 01~08 全展区：品牌标识 4 档光学尺码与 Canvas 贴花台、V2.2 全组件状态机、680p 双层沙盒、4px 栅格/Z 轴/双画幅安全区及 08 实战扩展基元/4 级视距/贴地圈交互沙盒） | `/ui-spec` |

## 约束边界

- 任何已接入模块不得复制本目录下的颜色令牌或载具剪影路径，必须通过唯一主端口白名单静态路由 `/ui/*` 同源加载，由 [检查项目结构.py](../../scripts/检查项目结构.py) 自动校验。
- **三向同源防滞后铁律**：新增或修改语义令牌、组件基元、剪影/情景图标、标绘原语或偏好键后，必须在同一轮内同步更新核心源码、`/ui-spec` 活体规范台、[全局 UI/UX 规范](../../docs/全局UI-UX规范.md) 及 [检查项目结构.py](../../scripts/检查项目结构.py) 的同步门禁清单；禁止只改核心源码或业务页而遗漏 `/ui-spec`，禁止在正式模块使用原生 `<select>` 或对 HUD 容器施加 `transform: scale()`。
- 自动化验证证据保存在已忽略目录 `local-data/界面规范/`（或统一主服务 `local-data/主服务/V001-c7c1e6f7/`）。
