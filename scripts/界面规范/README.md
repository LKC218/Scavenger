# 全局 UI/UX 活体规范路由工具

配套[全局 UI/UX 共享规范源](../../src/界面规范/README.md)与[全局 UI/UX 规范](../../docs/全局UI-UX规范.md)。本目录仅使用 Python 标准库与 Playwright 提供 `127.0.0.1:8766` 独立轻量活体规范路由与双浏览器同源自动验证。

## 启动规范路由

```powershell
# 检查、登记长驻服务保护并在后台启动或复用 127.0.0.1:8766
python -X utf8 -B scripts/界面规范/启动规范路由.py --start

# 前台调试运行（Ctrl+C 结束）
python -X utf8 -B scripts/界面规范/启动规范路由.py --serve

# 只读查询运行状态
python -X utf8 -B scripts/界面规范/启动规范路由.py --status
```

- 仅绑定 `127.0.0.1:8766`，以白名单只读映射 `/`、`/ui-spec` 及 `/ui/*` 到 `src/界面规范/`。
- 通过 `/api/status` 暴露实时源码 SHA-256 摘要，不加载地图或模型大包，秒开即用；不在项目根目录新增 `.cmd` 快捷文件。
- 长驻服务由维护清理工具任务 `全局UI规范路由服务-8766-V001-d6febf34` 独立保护，不与 `8784`、`8811`～`8814`、`8794`、`8767` 等既有端口冲突。

## 双浏览器同源自动验证

```powershell
python -X utf8 -B scripts/界面规范/验证界面规范.py --channel both --run 本轮独立目录名
```

- 串行在无窗口 Chrome 与 Edge（`device_scale_factor=2`）核验 `/ui-spec` 与 `/` 可达性、`/api/status` SHA-256 同源性（含 `交互控件.mjs`）、五维语义色令牌实时读取、4 类 `evenodd` 军事剪影与情景图标、演训调试抽屉开合、640px 3D 模拟下原生 DPR 高清叠加层隔离、密集编队车底锚定三级防叠字与 `Tab` 循环、定制战术下拉自动 `.is-dropup`＋LIFO `ESC` 优先收起，以及三档原生物理尺码无损放大验证（共 10 项校验）。
- 本轮 V2.1 验证输出保存在已忽略目录 `local-data/界面规范/V003-c7c1e6f7/验证/<run>/`，拒绝覆盖已存在目录。
