# 载具音效索引模块

当前网页入口已统一到8767模型音频库；`预览音效索引.py`复用统一服务，不再提供旧批次媒体。`生成音效索引.py`改为导出当前关联小索引到统一库维护目录。下文独立三维样例属于历史验证，公共`音频运行时.mjs`仍由靶场使用；当前模型和音频存储见[统一库说明](../../docs/资源与资料库/统一模型音频库使用说明.md)。

## 入口与职责

独立本地验证页，已实现全量三层索引查询和三款载具的Three.js声音样例。它不承担正式游戏规则或部署入口，不直接读取研究目录。

| 文件 | 职责 |
| --- | --- |
| [索引验证.html](索引验证.html) | 页面结构、首次开启声音、双向查询和样例控件 |
| [空战音效对比试听.html](空战音效对比试听.html) | A-10C 机炮与 Hydra 70 火箭齐射 WebAudio 实时 A/B 对比试听台（挂载于主服务 `/dev/audio/空战音效对比试听.html`） |
| [机炮物理声学引擎.mjs](机炮物理声学引擎.mjs) | 参数化共享机炮物理混合声学引擎 V2（导出 `DEFAULT_GAU8_ACOUSTIC_PRESET`、参数化 `buildShockPulseLoopBuffer({ rpm, caliberMm, barrelCount })`、共享 `buildAirspaceThunderIR`、`createContinuousGunAcousticVoice` 无内建 Convolver 持续动态声源，同时向后兼容试听台 `computeGau8DistanceProfile` / `createGau8AcousticResources` / `triggerGau8AcousticBurst`） |
| [界面.css](界面.css) | 深绿档案界面、窄屏布局、键盘焦点和减少动效支持 |
| [页面.mjs](页面.mjs) | 读取三份JSON，将选择和动作交给查询及播放接口 |
| [音效查询.mjs](音效查询.mjs) | 纯数据查询、精确配置匹配、弹药优先和显式回退条件 |
| [音频运行时.mjs](音频运行时.mjs) | Three.js音频适配、首次点击解锁、共享缓存、声音实例与生命周期 |
| [三维样例.mjs](三维样例.mjs) | 按需GLB加载、视角、示意挂点、世界命中点和模型释放 |

生成入口是 [生成音效索引.py](../../scripts/断箭/生成音效索引.py)，本地服务入口是 [预览音效索引.py](../../scripts/断箭/预览音效索引.py)。依赖固定版本Three.js，版本、官方npm来源、包SHA-512完整性和逐文件SHA-256记录保存在本地工具目录。生成库复制必要的运行文件与MIT许可证，运行页面不访问CDN。

## 生成与启动

从项目根目录运行，生成批次拒绝覆盖；原音效库及模型只读。

```powershell
python scripts/断箭/生成音效索引.py --batch BA-26273833-004 --source-batch BA-26273833-003
python scripts/断箭/核验音效索引.py --batch BA-26273833-004
python scripts/断箭/预览音效索引.py --batch BA-26273833-004 --port 8766
```

已生成的批次只需执行预览命令，打开终端打印的地址。服务只绑定127.0.0.1，不显示项目目录或本地配置。源码中的HTML是生成模板，不能直接file打开；需要生成的JSON与本地ES模块服务。

旧交付副本完成归档映射后，预览命令显式增加`--archive-manifest 迁移对照清单`，从同摘要正式副本读取已迁移媒体，原路由及冻结数据不改。归档完整性验证及旧命令的退役范围见[归档工具说明](../../scripts/断箭/旧版资料归档说明.md)。此选项不改变共享查询和音频运行时模块。

## 三层数据契约

| 文件 | 主键与内容 |
| --- | --- |
| 音频资源JSON | `assets[audio_id]`；事件GUID加渲染配置摘要，文件摘要、相对URL、格式、时长、循环状态及反向引用 |
| 载具绑定JSON | `model_id + config_id`；配置包含变体、单位、选项；每个binding保留原引用序号、武器/弹药关系、原事件与字段链 |
| 播放规则JSON | `actions[action]`；示例音量、位置类型、淡入淡出、冷却、单组及全局并发、64MiB缓存预算 |

三份数据都包含 `schema_version`、索引批次及来源正式版本。运行资源URL由预览服务显式路由，本机绝对路径只存在批次中的私有路由文件，不随网页JSON输出。Unity对象ID超出JavaScript安全整数范围时转换为十进制字符串。

`AudioCatalog.vehicle(modelId, configId)` 要求精确配置，不能只按型号猜配。`users(audioId)` 返回使用型号及绑定；`searchVehicles` 和 `searchAudio` 支持两个查询方向。884条确证、672条空字段、56条未索引引用全部保留；74项引用完整、52项部分映射、3项无当前单位引用及4项模型待修状态不变。

## 动作接口

`resolve` 或 `trigger` 的请求格式：

```js
{
  modelId: vehicle.model_id,
  configId: vehicle.config_id,
  action: 'impact',
  weaponId: weaponId,
  ammoId: ammunitionId,
  ammunitionPresetState: 'present'
}
```

动作支持 `move`、`shot`、`impact`、`interception`、`death`。射击要求weaponId；正常命中及拦截同时要求当前武器与ammoId。弹药预设对象为 `present` 时读取弹药字段，即使字段为空也不回退；只有明确为 `missing` 才读取武器Impact；`unknown` 返回未知状态。该条件来自当前构建静态证据，不代表样例已观测原游戏动态加载。

返回值中 `status: playable` 才可播放。缺项保留 `empty_field`、`event_not_indexed`、`no_binding`、`configuration_not_found` 等状态及原因，不猜配声音。播放器另返回 `locked`、`cooldown`、`voice_limit`、`cancelled`、`load_failed` 等状态。

## Three.js适配与生命周期

宿主传入THREE、AudioListener、AudioCatalog及资源基础URL。由用户点击调用 `unlock()`，之后 `trigger(request, {instanceId, anchor})` 播放空间动作。不同载具实例提供不同instanceId；缓存按音频身份共享，播放实例独立。查询或构造模块不会下载音频。原生试听使用非空间Audio，不要求先加载模型。

缓存计入所有并行解码的预算，驱逐近期未用且未播放的条目。正在读取的声音也占并发名额，避免连续点击产生无界加载。停止或切换递增代次标识，旧请求完成后不播放；清空缓存后旧解码不重新填回缓存。解码失败移除在途请求，允许重试。

`stopAll()` 以AudioContext绝对时间调度淡出，`stopAll({immediate: true, clearCache: true})` 立即停止并清空缓存，`dispose()` 用于退出。模型切换、后台隐藏和页面离开分别停止声音；GLB及其几何、材质、纹理和ImageBitmap在替换或退出时释放。

## 样例与边界

样例为A-10、AH-64D及M1A2的真实静态GLB，主动点击加载，未复制模型载荷。车体挂点跟随示意运动；射击点可调，未声明为源游戏真实炮口。点击地面设置命中点，每次命中保留触发时的世界位置，不跟随射手。场景按包围盒归一化，单位不代表米。

所有声音为既有有限事件渲染，循环关闭，动作每次触发一次片段。没有实现RPM实时混音、程序动画、连续射击调度、完整游戏玩法或压缩音频派生。本次不修改正式音效库的听审、循环和缺项状态。

验证入口是 [核验音效索引.py](../../scripts/断箭/核验音效索引.py) 与 [验证音效索引.mjs](../../scripts/断箭/验证音效索引.mjs)。后者运行真实Three.js音频类，Web Audio为替身；页面脚本使用最小DOM和场景替身，不能证明真实浏览器布局、WebGL、媒体解码、声卡或听感已通过。HTTP检查只验证资源服务与边界，不代替浏览器验收。

## 依赖边界

音效查询是纯数据模块；音频运行时通过显式参数接收Three.js和场景挂点。三维样例及页面依赖运行时模块，研究脚本仅生成数据，未形成运行时对研究目录的依赖。生成器复用同一断箭工具模块的摘要和批次边界函数。未来接入正式游戏时，可复用查询与音频运行时，替换示例挂点及动作源。
