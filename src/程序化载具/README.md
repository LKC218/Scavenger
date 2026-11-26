# 程序化载具流式渲染与展示模块

本目录承载全部 129 款美军空地载具的**单源同源三档 LOD 流式加载器、组件级 `InstancedMesh` 渲染器、清透微缩沙盘风格化材质与环境反射引擎及多车型混编演训场**。全仓载具统一启用唯一正式标准 `VEHICLE_STYLE_PRESETS.miniature_sand`（清透微缩沙盘）管线；单车 360° 检视由 [主入口](../主入口/README.md) 武器库（`/armory`）统一承载，早期对比预览与测试夹具页面已收口删除。

## 核心文件职责

| 文件 | 职责说明 |
| --- | --- |
| [载具风格化材质.mjs](载具风格化材质.mjs) | 唯一正式标准 `miniature_sand`（亮度自适应暗部提拉 / 高度渐变 / 干扫棱边 / `coated_clear` 物理镀膜玻璃）、WeakMap 状态隔离（保障 `clone(true)` 安全）、1mm 空间焊接平滑法线 1.15px 像素描边、AlphaTest 纹理裁剪、`options.envMap`/`setEnvMap`/`dispose` 及轻量化 PMREM 天光/机库环境模块（`sandboxEnv` / `armoryEnv`） |
| [载具流式加载器.mjs](载具流式加载器.mjs) | 解析定长二进制索引 `vehicles-index.scvi`、HTTP `206 Partial Content` Range 读取 `.scvveh` 归档包、去重共享 KTX2 纹理池（`anisotropy` 对齐 8）、统一应用 `miniature_sand`（`LOD2` 自动禁用描边、`setResolution`/`setEnvMap` 与安全 LRU 释放） |
| [载具实例化渲染器.mjs](载具实例化渲染器.mjs) | 按 `(车型 × LOD × 可动槽位 × 材质)` 维护共享 `InstancedMesh` 池、±15% 距离滞回平滑 LOD 切换与 `LOD1` 近景实时阴影解耦投射、`meta.sl`/`meta.slots` 机械转轴与反量化世界矩阵精确对齐，兼容平滑法线反转壳描边实例（LOD0/1 按需描边且 `castShadow=false`/`receiveShadow=false`/`renderOrder=-1`，严格过滤 LOD2 与阴影代理）、支持 `setResolution`/`setOutlineEnabled` |
| [混编演训场.html](混编演训场.html) | 统一主服务 `/dev/battlefield`（兼容 `/battlefield`）多车型同屏混编场：接入 `sandboxEnv` 沙盘环境（地面显式 `envMap` + `envMapIntensity` 隔离防漂白）、风格化材质与实例化描边、切幅同步 `uResolution`（10 车近景 / 30 车型×100 混编 / 300 压力探索 / 129 全车大阅兵及 `70m ↔ 9000m` 缩放） |

## 架构与规范边界

- 所有运行页面强制直接引用唯一渲染基线配置源 [渲染基线.mjs](../程序化地图/渲染基线.mjs)（`stylized-680p-v1`：`1208×680` / `1588×680`、`antialias: true`、`pixelRatio: 1`）与全局 UI 共享令牌源 [界面规范](../界面规范/README.md)（`/ui/令牌.css`、`/ui/组件.css`、`/ui/图标与标绘.mjs`）。
- 已接入全仓唯一正式运行与开发端口 **`127.0.0.1:8800`**（[启动主服务.py](../../scripts/启动主服务.py)）；`127.0.0.1:8822`（[预览服务.py](../../scripts/程序化载具/预览服务.py)）仅保留作历史 V0–V5 离线批处理工具兼容回环入口，严禁新开独立业务 HTTP 端口，亦不修改 `revision 7` 正式资料库指针及历史冻结端口。
- 实施计划见 [程序化载具优化与流式渲染-实施计划](../../docs/地图与载具/程序化载具优化与流式渲染-实施计划.md)，工具链见 [程序化载具工具](../../scripts/程序化载具/README.md)。
