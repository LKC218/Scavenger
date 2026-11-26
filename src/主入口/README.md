# Scavenger 正式游戏统一主入口

本模块是全项目唯一正式入口 `http://127.0.0.1:8800/`（与 `/armory`、`/settings`）的前端应用壳，消费唯一全局 UI/UX 代码单源 [界面规范](../界面规范/README.md)（Code SSOT V2.2，路由 `/ui/*`）与唯一渲染基线 [渲染基线.mjs](../程序化地图/渲染基线.mjs)（`stylized-680p-v1`）。

## 文件职责

| 文件 | 职责说明 |
| --- | --- |
| [index.html](index.html) | 正式主壳页面骨架（`/` 4A 作战大厅含去重精简正式入口、3×2 编队卡 + 固定翼一键试飞与默认展开的研发/调试工作台；`/armory` 4B 129 车全景沉浸式 680p 武器库支持 `?unit=<slug>` 深链及固定翼直跳 `/air-lab`；`/settings` 系统与渲染设置） |
| [主入口.css](主入口.css) | 正式主壳页面布局样式表，严格仅引用 `/ui/令牌.css` 与 `/ui/组件.css` 语义变量和组件类；含 3×2 编队卡（含固定翼 `试飞` 徽标）、研发工作台网格、与武器库全视口无黑边毛玻璃悬浮布局 |
| [武器库沙盘环境.mjs](武器库沙盘环境.mjs) | 武器库 Airbase 微缩沙盘日光 3D 展台环境模块（天蓝暖米梯度天空球、远距柔雾、程序化混凝土停机坪与沙土场坪、远景拱形机库/油桶栈板/松树丛及 `sandbox`/`armory` 双光与动态阴影视锥收紧） |
| [主入口.mjs](主入口.mjs) | 路由同步（`pushState` / `popstate` 含 `?unit=<slug>`）、研发工作台折叠记忆、大厅 `Enter` 快捷部署、680p `full` 满幅复古微缩 Three.js + `ProceduralVehicleLoader` 流式 3D 武器库展台（默认 `sandbox` 沙盘日光 + `miniature_sand` 材质与 1.15px 描边、`buildVehicleRigHierarchy` 机械部件实时演示、`OrbitControls` 阻尼缩放）、6 槽编队持久化与 `loadUiPrefs` 联动 |

## 约束边界

- 严禁在本模块内部使用原生 `<select>`、重定义 `--ui-ch-*`/`--ui-state-*`、硬编码载具剪影路径或使用容器 `transform: scale()`；
- 全部分发资产通过唯一主服务 [启动主服务.py](../../scripts/启动主服务.py) 同源路由加载。
