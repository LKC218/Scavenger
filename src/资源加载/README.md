# 压缩模型加载

`压缩模型加载.mjs`为已有Three.js页面提供同一个GLTFLoader工厂，注册Meshopt和KTX2，按WebGLRenderer缓存转码器。浏览器只从本机固定Three.js目录加载WASM和Worker，不依赖CDN。

工厂同时兼容原始GLB，资源选型仍由各服务的清单决定。`disposeModelLoader(renderer)`在预览器销毁时释放Worker。靶场与群体实验的渲染器保持页面生命周期。

共享KTX2转码器的Worker上限为2。全图模块另外使用有界下载、资源引用计数和区域租约；此Worker配置本身不表示下载缓存、纹理驻留或帧时间已经达标。

Python服务使用`提供压缩解码器`登记明确白名单，并对新增依赖检查原固定版本摘要；CSP需要允许本地WASM、blob Worker和固定Basis转码器的动态JavaScript生成；当前服务仅绑定回环地址且脚本仅来自固定白名单，不直接沿用为公开部署策略。当前统一指针存在时只读`local-data/资源库/`的登记媒体，不读取旧运行开关；缺失时报错，不自动回到待退役原件。旧源摘要、稳定ID和质量状态继续保留。结构与恢复见[统一库说明](../../docs/资源与资料库/统一模型音频库使用说明.md)。
