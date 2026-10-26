# 质谱降噪 WebSocket JSON 协议 v1

日期：2026-10-26。状态：协议设计已确认；服务实现尚未开始。

## 1. 目标与范围

PC 每次提交完整的“时间 × m/z 通道”数据集。开发板上的 Go 服务沿时间方向执行降噪，返回时间轴、通道名称、通道顺序和矩阵尺寸相同的结果。PC 收到本次终态响应后，再发送下一组数据。

用户已确认：WebSocket、全 JSON、完整数据集请求响应，每次请求只指定一种降噪算法及其对应参数；主要实现 `docs/deNoise_ms.py` 中的算法部分，不实现可视化等其他功能。

另需支持开发板定期主动发送设备状态心跳，供 PC 监控 CPU、内存、操作系统、设备型号和服务运行状态。

hello 用于报告设备概要、发现功能类别和每类已支持的算子。当前实现范围为降噪，后续可增加去基线等功能类别；算子参数由对应处理请求携带，不在 hello 中展开。

同一次请求的所有待处理通道使用同一种算法和同一组参数。普通算法处理全部强度通道；`mz5_11` 使用参数中指定的参考通道构造底谱，参考通道保留原值，其余通道作为目标。时间轴不参与降噪。

本设计覆盖 9 种降噪方法及 `none`，以及执行这些方法必需的参数、参考通道和处理状态。CSV 的读取和结果 CSV 的生成由 PC 完成。开发板不承担绘图、报告生成、文件管理、批量文件工作流、交互式菜单、SNR/强度自动选算法、同位素和生物标志物分析。

算法的有效输入、默认参数、边界处理及短序列行为以参考脚本的实际执行代码为依据，而非仅依据其注释。Go 移植的库选择和数值对照测试属于后续实现设计；本草案不宣称已验证算法效果或板端性能。

本次参考脚本的 SHA-256 为 `0c9934c3ebe9f2f628b834320973705a02994ea08875472c9379a41c195fb139`，后续对照测试应保留该参考版本及使用的 Python 依赖版本。

## 2. 依据与方案选择

样例 `docs/cleandata_ms.csv`：1911 个时间点，63 个通道（`mz2` 至 `mz64`），时间单位分钟，强度包含负值，无空值，原文件 1,321,569 字节。拆分时间轴、通道轴和强度矩阵后，紧凑 JSON 数值内容约 1,325,428 字节；同样数值用 float64 二进制约 978,936 字节。数字不包含协议封装，实际大小取决于序列化方式。

考虑了两个方案：

| 方案 | 优点 | 代价与选择 |
| --- | --- | --- |
| 全 JSON | 联调直观，PC 与 Go 易于实现 | 解析及内存成本较高；用户已选择 |
| JSON 元信息 + 二进制数值 | 数据更紧凑 | 额外约定字节序、数据布局；本版不采用 |

下文为本版已确认的协议设计。资源预算和部署配置中的建议初始值仍需经开发板实测后调整。

## 3. 连接、消息边界与版本

- 地址：`ws://<board-ip>:<port>/vta`，建议初始端口 `8080`，允许部署时配置。采用内网部署，上位机通过 `ws://` 直接连接开发板，不支持 TLS。
- PC 是客户端，负责首次连接和断线后的重连；开发板是服务端，负责监听和接受连接。完整连接生命周期见第 11 节。
- 每个应用消息是一个完整的 UTF-8 JSON 对象，放在一条 WebSocket 文本消息中。一条消息可以由 WebSocket 库拆成多个帧，接收端按完整消息解析。
- 不使用换行、分隔符、Base64、CSV 字符串或应用层分块。v1 不启用 WebSocket 消息压缩。
- 顶层 `version` 必须为整数 `1`；不兼容的结构或语义变更升级版本。v1 内仅增加可选响应字段；PC 应忽略未知响应字段。服务端拒绝未知请求字段、未知参数及重复 JSON 对象键，避免配置拼写错误被忽略。
- 质谱及算法数值在 Go 中使用 float64；接受科学计数法，输入和输出都必须是有限数值。不接受数值字符串、`null`、`NaN`、无穷值或超出 float64 表示范围的数值。不得将很小的强度值统一四舍五入为固定小数位。hello 中的可空设备信息及设备心跳中的可空指标分别按第 5、12 节处理；这种可空约定不适用于质谱或算法参数。
- JSON 对象键的顺序不具有语义，数组顺序具有语义。

消息及控制帧语义依据 [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html)；JSON 编码依据 [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259.html)。本协议对字段、重复键和数值范围施加更严格的约束。

## 4. 消息类型及交互顺序

应用层有五种 JSON 消息；connect/open、reconnect、close、error 等连接事件及 Ping/Pong 控制帧的处理另见第 11 节：

| type | 方向 | 用途 |
| --- | --- | --- |
| `hello` | 开发板 → PC | 建连后报告设备概要，列出功能类别及每类支持的算子 |
| `heartbeat` | 开发板 → PC | 定期主动发送设备信息及运行状态，无需请求或 ACK |
| `denoise` | PC → 开发板 | 提交完整数据和本次降噪配置 |
| `result` | 开发板 → PC | 完整结果及逐通道状态，包括部分处理失败 |
| `error` | 开发板 → PC | 请求整体拒绝或处理整体失败，无结果矩阵 |

顺序：PC 发起 connect → WebSocket 建连成功 → 开发板发送 `hello` → PC 验证 hello 后进入 READY → `denoise` → `result` 或 `error` → 下一次 `denoise`。断线后由 PC 决定 reconnect，重连成功后重新执行 hello 流程。没有应用层 ACK、进度推送、配置会话或结果拉取。

hello 之后，heartbeat 可以出现在任意两条应用消息之间，包括等待请求和降噪处理期间。PC 先按 type 分发：heartbeat 更新监控信息，不结束请求、不释放客户端等待状态、不重置降噪请求超时。每条连接一个未完成请求的约束仅针对 denoise，不限制板端主动上报。

每条连接最多一个未完成请求，服务端全局默认最多一个实际执行任务，不排队。其他连接在执行槽占用时提交请求，收到 `BUSY`。同一连接尚未完成便再次发送应用请求，属于协议违例：用 Close `1008` 关闭连接，当前请求结果放弃；不发送可能混淆两个请求的终态。服务端必须保证合规 PC 在收到终态后立即发下一次请求时，不会由于状态更新竞态被误判为繁忙。

正常可用的连接上，一次已接收请求只产生一个终态消息。断线、超长消息或其他连接级错误可能只有关闭事件，没有 JSON 终态。

## 5. hello：设备概要、功能与算子发现

### 5.1 消息结构

hello 每条连接仅发送一次，包含设备概要与功能算子列表。下面的型号和配置数值仅为格式示例：

```json
{
  "version": 1,
  "type": "hello",
  "device": {
    "id": "board-001",
    "model": "example-board",
    "os": {
      "name": "Linux",
      "distribution": "Example Linux",
      "version": "1.0",
      "kernel": "6.x-example",
      "arch": "arm64"
    }
  },
  "cpu": {"model": "Example ARM CPU", "logical_cores": 4},
  "memory": {"total_bytes": 1073741824},
  "functions": {
    "denoise": [
      "mean",
      "std_threshold",
      "ewma",
      "median",
      "quantile",
      "savitzky_golay",
      "wavelet",
      "kalman",
      "mz5_11",
      "none"
    ]
  }
}
```

`device`、`cpu`、`memory` 必填，表示建联时的设备与系统信息快照，字段路径、类型和单位与 heartbeat 中对应字段一致：

| 字段 | 含义 |
| --- | --- |
| device.id | 稳定设备标识，重连后仍用于识别同一设备 |
| device.model | 开发板型号 |
| device.os | 系统名称、发行版名称及版本、内核版本、架构 |
| cpu.model | CPU 型号 |
| cpu.logical_cores | 建联时在线逻辑 CPU 数 |
| memory.total_bytes | 操作系统可用物理 RAM 总量，单位 byte |

上述嵌套字段均发送，无法获取的字符串或数值使用 null，不使用空字符串或 0 冒充，也不因此阻止功能发现。设备信息优先使用缓存或快速平台读取，不能为采集 CPU 使用率等动态指标延迟 hello；动态使用率及采集失败原因由周期 heartbeat 上报。CPU 在线数及内存总量发生变化时，以后续有效 heartbeat 为准。

`functions` 必填，是“功能类别 → 算子名称数组”的对象。当前 `denoise` 表示降噪；将来实现去基线时可增加 `baseline` 类别，并列出实际可执行的去基线算子。功能数量取 functions 的键数量，无需再维护计数字段。

类别名称和算子名称均为非空字符串；每个算子数组必须非空且无重复。只列出已经实现并启用的能力，尚未实现的去基线类别不提前上报，也不使用空数组占位。服务端尚未启用任何处理能力时，functions 可为 `{}`。PC 按类别发现算子，可忽略暂不支持的新类别；数组排列仅用于展示，不表示算法执行链。

hello 包含设备概要和功能清单；算法参数、参数候选值、资源预算、超时时间、服务软件版本及连接标识不在 hello 中展开。每次处理请求仍只选择一种算子及其参数；当前降噪请求使用 `config.method` 指定 functions.denoise 中的算子，使用 `config.params` 指定本次参数。其他类别的请求结构随对应功能单独定义，本轮不增加去基线算法或请求类型。

connection_id 由开发板在接受连接时生成，在本连接的 heartbeat 中提供；请求响应通过所属连接及 request_id 关联。algorithm_version 保留在 result 中，用于追溯算法实现、默认参数及数值行为，影响结果的改动须更新该版本。

### 5.2 本地运行预算

下列预算供服务端实现及 PC 联调配置使用，不通过 hello 下发或协商。建议值需在开发板实测后调整；部署时同步配置 PC 的数据大小预检查、等待预算和监控周期。

| 配置名 | 建议初始值 |
| --- | --- |
| max_request_bytes | 16777216 |
| max_response_bytes | 33554432 |
| max_time_points | 100000 |
| max_channels | 1024 |
| max_cells | 500000 |
| max_window_size | 4095 |
| max_inflight_per_connection | 1 |
| max_active_requests | 1 |
| max_connections | 4 |
| processing_timeout_ms | 120000 |
| upload_timeout_ms | 30000 |
| write_timeout_ms | 10000 |
| ping_interval_ms | 20000 |
| pong_timeout_ms | 60000 |
| report_interval_ms | 5000 |
| report_stale_timeout_ms | 15000 |

字节限额计算完整 UTF-8 JSON 消息，包含封装及空白；维度限额同时满足，max_cells 指 N × C。响应预算单独设置，因为处理后数字的文本表示可能更长。服务端仍在处理前执行限额校验，超限按第 10、11 节返回错误或关闭连接。超过最大连接数时，WebSocket Upgrade 前返回 HTTP 503。

## 6. denoise 请求

以下是可执行的短序列协议示例，数值用于说明字段；实际质谱使用完整矩阵。

```json
{
  "version": 1,
  "type": "denoise",
  "request_id": "ms-0001",
  "data": {
    "time_unit": "min",
    "time": [0.0, 0.1, 0.2],
    "channels": ["mz28", "mz44"],
    "intensity": [[1e-12, 4e-12], [2e-12, 5e-12], [3e-12, 6e-12]]
  },
  "config": {
    "method": "mean",
    "params": {"window_size": 3}
  }
}
```

字段约定：

- `version`、`type`、`request_id`、`data`、`config` 必填。
- `request_id` 是 1～64 字节 ASCII 字符串，字符集 `[A-Za-z0-9_-]`；建议 PC 每次生成新 UUID。它用于关联请求响应，不是服务端去重或幂等键。
- `data.time_unit` 必填，v1 固定为 `min`，对应 CSV 的 `Time/min`。PC 负责其他时间单位的转换。
- `data.time` 是 N 个非负、严格递增的数值，N ≥ 1。服务端不重采样、不排序；所有窗口、滞后及标定索引按样本点计算，不按分钟计算。
- `data.channels` 是 C 个唯一非空字符串，C ≥ 1；每个名称最多 64 个 UTF-8 字节，不得为 `Time/min`。名称按原 CSV 保留，作为不透明标识使用，不要求服务端从名称推断 m/z。
- `data.intensity` 是 N 行、每行 C 个数值的矩阵。`intensity[i][j]` 对应 `time[i]`、`channels[j]`。允许零和负数；传输层不截断负值、不归一化、不补值。
- `config` 仅包含必填字符串 `method` 和可选对象 `params`（省略视为 `{}`）。一次只能指定一个方法，不能传方法数组、方法链、默认/覆盖配置或逐通道算法配置。
- `config.method` 对整组数据生效；普通算法逐通道使用同一种方法，`mz5_11` 的参考通道按第 8 节保留原值，`none` 保留整组数据。
- `config.params` 只接受该方法的参数；省略的参数以该方法的默认值补齐，得到全请求共用的参数配置。算法内部根据通道数据计算的中间量可以不同，例如 mz5_11 的 lag、a、c。
- 所有参数、引用及形状先统一校验，再开始算法处理。每次请求配置独立，不继承上次请求。

输出 `data` 使用完全相同结构。PC 重新导出 CSV 时写 `Time/min` 加 `channels` 作为表头，将每个时间值与对应强度行合并。相同结构不要求 JSON 数字的词法表示、CSV BOM 或小数位格式相同。

## 7. 算法名称、默认值与参数校验

每次请求从下表中选择一个方法；该方法沿各待处理通道的时间序列计算，`mz5_11` 另需跨通道预处理。表中参数之外的字段均拒绝；整数参数不接受带小数点或指数的 JSON 写法。

| method | params 默认值 | 约束及参考行为 |
| --- | --- | --- |
| `none` | `{}` | 原值保留 |
| `mean` | `window_size: 5` | 窗口为正整数，≤ `max_window_size`；reflect 边界 |
| `std_threshold` | `window_size: 11, sigma_multiple: 3` | 窗口为正奇数，≤ 上限；`sigma_multiple ≥ 0`；两端窗口截断，低于局部均值加倍数标准差者置零 |
| `ewma` | `alpha: 0.3` | `0 < alpha ≤ 1`；首个输出取首个输入 |
| `median` | `window_size: 5` | 窗口为正整数，≤ 上限；reflect 边界 |
| `quantile` | `window_size: 11, quantile: 0.75` | 正整数窗口，≤ 上限；`0 ≤ quantile ≤ 1`；低于局部分位数的原值置零 |
| `savitzky_golay` | `window_size: 11, polyorder: 3` | 窗口为正奇数，≤ 上限；整数 `0 ≤ polyorder < window_size`；常规边界按脚本 `interp`，N 小于窗口时退化为 `min(3,N)` 点 reflect 均值 |
| `wavelet` | `wavelet: "db4", level: 3, threshold_type: "soft"` | 建议首版小波名仅支持 db4，作为参数在请求时校验；整数 `1 ≤ level ≤ 16`；阈值类型 `soft` 或 `hard`；其余规则见下文 |
| `kalman` | `process_variance: 1e-5, measurement_variance: 0.1` | `process_variance ≥ 0`，`measurement_variance > 0`；初值 `x=input[0], P=1` |
| `mz5_11` | 见下一节 | 显式参考通道，标定、平滑及参考信号扣除 |

窗口大于 N 对 mean、median、quantile、std_threshold 允许，由相应边界规则决定；不自动把窗口缩小为 N。正奇数等严格校验是协议约束，避免脚本中隐式参数修正带来的歧义。

小波处理遵循脚本：N < 8 时退化为 `min(3,N)` 点 reflect 均值；否则根据小波滤波器长度确定最大分解层数，并按脚本 `max_level >= 1 ? max(1,min(level,max_level)) : 1` 计算实际层数。使用脚本中 PyWavelets 默认的 symmetric 延拓、最高频细节系数的 MAD 噪声估计及统一阈值；重构后按原长度截断或补零。实际层数变化必须在通道结果中报告。默认延拓及最大层数定义参见 [PyWavelets 官方文档](https://pywavelets.readthedocs.io/en/latest/ref/dwt-discrete-wavelet-transform.html)。

不得在本次协议设计中顺便修改参考算法的阈值公式、边界或时间滞后方向。移植发现疑似算法问题时，独立记录，并通过算法版本变更处理。

## 8. mz5_11 参数与执行语义

配置例子（作为整次请求的 `config`）：

```json
{
  "method": "mz5_11",
  "params": {
    "no_gas_mz": ["mz5", "mz6", "mz7", "mz8", "mz9", "mz10", "mz11"],
    "T_frac": 0.1,
    "k": 5,
    "max_lag": 30,
    "aggregation": "mean"
  }
}
```

- `no_gas_mz` 必填，是非空、无重复的现存通道名称数组。协议只接受名称，不接受脚本所兼容的整数列索引或浮点 m/z 匹配方式。
- 服务端按输入通道顺序求这些参考通道的均值，构成全时域 `x_ref`。参考通道由这一组请求参数统一指定，不设置逐通道配置。
- 参考通道自动保留原值，其他全部通道作为目标使用 `mz5_11`。至少需要一个目标通道；引用不存在或全部通道都被选为参考时，在处理前报 `INVALID_CONFIG`。参考通道保留是该算法自身的规则，不是请求中另行指定 `none` 方法。
- `T_idx` 可选，必须是整数二元数组 `[start, stop]`，含起点、不含终点，满足 `0 ≤ start < stop ≤ N`，并至少包含 10 点。
- `T_frac` 可选，默认 `0.1`，要求 `0 < T_frac ≤ 1`；与 `T_idx` 不能同时出现，也不能传 `null`。未指定 `T_idx` 时取 `[0, min(N, max(10, floor(N*T_frac))))`；N < 10 时配置无效。
- `k` 默认 5，必须为 ≥ 3 的奇整数，≤ `max_window_size`。`max_lag` 默认 30，必须为非负整数，≤ `max_window_size`；搜索时对标定段过短的收缩规则遵循脚本。
- `aggregation` 默认且仅允许 `mean`。
- 请求参数只描述方法配置；`x_ref`、`lag`、`a`、`c` 等中间结果由服务端计算，不由 PC 注入。

执行流程：构造参考底谱 → 在标定段逐目标通道求 lag、a、c → 中心均值平滑实际信号与模拟参考信号 → 两者相减。引用原始输入构造底谱，不能使用其他通道已降噪的结果。

标定段应由 PC/实验流程保证确实满足参考脚本的无气体要求，服务端不能仅从数据自动证明这一实验前提。示例 6 的 `k=31, max_lag=10` 是一个示例配置，不是方法默认值，需要时由 PC 显式传入。

参考或目标信号相对波动小于 0.01、幅度系数 a 不在 `[0.5,2]`、标定计算失败时，按脚本保留目标原值，并通过通道状态报告原因；不能默默宣称该通道已完成降噪。正常路径不使用缺少参考通道时的隐式均值降级，缺失引用在请求校验阶段拒绝。

## 9. result：数据与逐通道结果

对应第 6 节请求的响应示例：

```json
{
  "version": 1,
  "type": "result",
  "request_id": "ms-0001",
  "status": "ok",
  "algorithm_version": "1.0.0",
  "data": {
    "time_unit": "min",
    "time": [0.0, 0.1, 0.2],
    "channels": ["mz28", "mz44"],
    "intensity": [[1.3333333333333334e-12, 4.333333333333333e-12], [2e-12, 5e-12], [2.6666666666666667e-12, 5.666666666666667e-12]]
  },
  "stats": {"total": 2, "processed": 2, "skipped": 0, "errors": 0},
  "column_results": [
    {"channel": "mz28", "status": "processed", "requested_method": "mean", "applied_method": "mean", "effective_params": {"window_size": 3}, "code": "PROCESSED"},
    {"channel": "mz44", "status": "processed", "requested_method": "mean", "applied_method": "mean", "effective_params": {"window_size": 3}, "code": "PROCESSED"}
  ],
  "timing": {"processing_ms": 1.2}
}
```

- `data.time_unit`、`data.time`、`data.channels` 原样保留数值/字符串及数组顺序；只有强度矩阵中的目标通道被修改。协议层不额外裁剪负值。
- `column_results` 按输入通道顺序恰好包含 C 项；`stats.total = C = processed + skipped + errors`。
- 单个通道算法失败、产生非有限值或返回长度不符时，整个通道原样保留，状态为 `error`，禁止返回半列结果或将非法数值替换成 `null`。
- `status=ok`：所有通道按配置完成；请求选择 `none` 或 mz5_11 预期参考通道保留不影响 ok。
- `status=partial`：出现任何通道错误、标定跳过、短序列算法退化或小波层数调整。即使所有目标通道都失败，也返回 `partial` 及完整逐通道说明，不能被 PC 当作成功降噪。
- 每个通道的 `requested_method` 均等于本次 `config.method`；`applied_method` 是实际方法。参考通道、标定跳过或失败保留原值时，`applied_method` 为 `none`；短序列退化时为 `mean`。这些是执行状态，不表示允许请求指定多个方法。`effective_params` 记录实际应用的完整参数，原样保留时为 `{}`；这里的参数不包含全长中间数组。
- mz5_11 成功项另带 `calibration` 对象：`T_idx`、`lag`、`a`、`c`；实际 k 及参考通道在 `effective_params` 中报告。
- `code` 是稳定机器码；可选 `message` 供人阅读，PC 不解析文案。
- `timing.processing_ms` 从配置校验成功且获得执行槽开始，到算法及结果校验完成，包含必要的矩阵预处理，不含网络收发和 JSON 编解码。示例数值不代表性能承诺。

通道 code：`PROCESSED`、`NONE`、`REFERENCE_CHANNEL`、`FALLBACK_SHORT_SERIES`、`WAVELET_LEVEL_ADJUSTED`、`CALIBRATION_LOW_VARIATION`、`CALIBRATION_SCALE_OUT_OF_RANGE`、`CALIBRATION_FAILED`、`ALGORITHM_ERROR`、`NON_FINITE_RESULT`、`INVALID_RESULT_LENGTH`。降级/调整为 processed，标定跳过为 skipped，后三个计算失败码为 error。

## 10. error：整体失败

```json
{
  "version": 1,
  "type": "error",
  "request_id": "ms-0002",
  "error": {
    "code": "INVALID_SHAPE",
    "message": "intensity 第 2 行的通道数与 channels 不一致",
    "field": "data.intensity[1]"
  }
}
```

`error` 必含 `code`、`message`，`field` 可选，采用示例中的点号/数组下标路径。`request_id` 能安全解析且符合格式时回传，否则为 `null`。整体 error 不附带结果 data，PC 不应自动以原始输入冒充降噪结果。

| code | 含义 | 连接处理 |
| --- | --- | --- |
| `INVALID_JSON` | 语法错误、尾随多个 JSON 值、重复键 | 返回 error，保持连接 |
| `UNSUPPORTED_VERSION` | 版本不支持 | 返回 error，保持连接 |
| `INVALID_REQUEST` | 消息类型、必填项、字段类型或未知字段错误 | 返回 error，保持连接 |
| `INVALID_SHAPE` | 轴/矩阵尺寸不一致 | 返回 error，保持连接 |
| `INVALID_DATA` | 非有限值、空值、非法通道或时间轴 | 返回 error，保持连接 |
| `INVALID_CONFIG` | 无效参数、引用或未知参数 | 返回 error，保持连接 |
| `UNSUPPORTED_METHOD` | 算子未在 functions.denoise 中声明，或所选小波名尚未支持 | 返回 error，保持连接 |
| `LIMIT_EXCEEDED` | 点数、通道数、单元数超限，消息字节数尚未超限 | 返回 error，保持连接 |
| `BUSY` | 其他请求占用实际执行槽，本次未执行 | 返回 error，保持连接 |
| `PROCESSING_TIMEOUT` | 本次整体处理超过预算 | 返回 error，放弃本次结果；执行槽规则见下文 |
| `RESPONSE_TOO_LARGE` | 编码后的完整结果超过响应预算 | 返回 error，释放执行资源 |
| `INTERNAL_ERROR` | 无法归属于单个通道的服务端故障 | 能安全回复则返回 error；无法恢复则 Close 1011 |

输入是合法 JSON 数字但转换到 float64 溢出，归为 `INVALID_DATA`；字面 `NaN`/`Infinity` 是 `INVALID_JSON`。未知算法优先报 `UNSUPPORTED_METHOD`，已知算法的无效参数报 `INVALID_CONFIG`。多处错误时按顶层信封 → 数据与形状 → 配置 → 执行槽的顺序返回首个错误；PC 不应依赖同一层内的检查顺序。

## 11. WebSocket 连接生命周期与消息处理

### 11.1 连接事件与业务消息的边界

以下事件由 PC 的连接管理器或双方 WebSocket 协议栈处理。事件名用于描述本地处理入口，不是新增加的 JSON `type`；一次 JSON 发送之前必须已存在有效连接。

| 事件/机制 | 触发场景 | PC 处理 | 开发板处理 |
| --- | --- | --- | --- |
| `connect` | 用户启动连接或显式再次连接 | 向开发板发起 WebSocket Upgrade，进入 CONNECTING | 校验路径、握手及连接数，接受或通过 HTTP 拒绝 |
| `open` / connected | 握手成功 | 进入 WAIT_HELLO，启动 hello 等待计时，暂不发送业务数据 | 为连接生成 connection_id，首先发送 hello |
| `hello` | 收到本连接第一条业务消息 | 校验消息结构，显示设备概要并读取功能算子列表，进入 READY | hello 写入失败则清理连接；不接受 hello 发送前的业务请求 |
| `message` | 收到文本应用消息 | 先按 type 分发 heartbeat，再匹配 request_id 处理 result/error | 校验并执行 denoise，独立采集上报状态，持续处理控制帧 |
| `error`（连接事件） | 建连失败、网络读写失败等 | 记录阶段、错误及连接代次；进入统一清理与重连判定 | 取消该连接任务、停止写入并清理资源 |
| `reconnect` | 可重试断线且 PC 仍希望保持连接 | 按退避策略重新向同一地址 connect，重新等待 hello | 作为一条全新连接接收，不恢复旧任务 |
| Ping / Pong | 连接保活 | 响应板端 Ping，也可主动发 Ping 检测板端存活 | 响应 Ping，检测 Pong 超时 |
| `close` | 主动断开、服务退出或收到关闭帧 | 记录关闭码和原因，结束当前连接，决定是否重连 | 回应关闭握手，取消连接任务并释放资源 |

连接事件 `error` 表示传输或连接失败；JSON `type=error` 表示一次业务请求整体失败。收到业务 error 后通常回到 READY，不触发重连。双方可以直接使用 Close 控制帧关闭连接，无需先成功发送某条 JSON close 消息。WebSocket 没有单独的 reconnect 控制帧，重连是重新执行连接握手。这些标准机制参见 [RFC 6455 第 4、5.5、7 节](https://www.rfc-editor.org/rfc/rfc6455.html)。

### 11.2 首次连接与就绪

PC 的建议初始本地预算为 `connect_timeout_ms=5000`、`hello_timeout_ms=15000`、`close_timeout_ms=3000`；这些值是连接管理器配置，不依赖尚未收到的 hello。

1. PC 连接 `ws://<board-ip>:<port>/vta`，本次拨号及 HTTP Upgrade 共用 connect 超时。
2. 开发板成功 Upgrade 后生成 connection_id，使用写超时发送一次 hello。PC 握手成功只代表传输已连通，收到并验证 hello 才代表业务就绪。
3. PC 验证 `version=1`、设备概要字段类型及 functions 结构，显示设备信息并读取各功能支持的算子；设备属性值为 null 表示未知，不影响功能就绪。所需功能或算子不可用时保留连接也可，但该任务不能发送；应向上层报告不支持的能力。资源和超时预算使用本地联调配置，不依赖 hello 提供。
4. hello 正常则进入 READY。超时未收到 hello，PC 用 Close `4000`（HELLO_TIMEOUT）关闭并进入重连判定；hello 结构错误或版本不兼容则正常关闭（1000），报告协议不兼容，停止自动重连直到配置或程序修正。
5. PC 在 hello 前收到 heartbeat/result/error，或同一连接重复收到 hello，均视为应用协议不兼容：关闭并停止自动重连。开发板在 hello 发送前收到业务请求，用 Close 1008 拒绝，不执行请求。

握手失败尚未建立 WebSocket 时，通过 HTTP 状态处理，不发送 JSON hello/error：错误路径返回 404，无效握手返回 400（WebSocket 版本不支持时可返回 426），容量已满或服务正在退出返回 503。PC 对网络失败、拨号超时和 HTTP 5xx 可重试；对 HTTP 4xx 停止自动重试并报告地址/协议配置错误。

### 11.3 PC 状态与回调处理

| 本地状态 | 含义 | 正常后继 |
| --- | --- | --- |
| DISCONNECTED | 无连接，尚未启用或已停止自动连接 | 用户连接 → CONNECTING |
| CONNECTING | 进行 TCP/HTTP Upgrade | 成功 → WAIT_HELLO；失败 → RECONNECT_WAIT 或 DISCONNECTED |
| WAIT_HELLO | 已建连，等待并验证能力消息 | hello 有效 → READY；失败 → 关闭及重连判定 |
| READY | 可以提交下一组完整数据 | 提交 denoise → PROCESSING；关闭/断线 → 清理 |
| PROCESSING | 已开始提交当前数据，等待终态 | 匹配的 result/error → READY；断线 → 清理，当前请求结果未知 |
| RECONNECT_WAIT | 已清理旧连接，等待退避计时 | 到期且仍启用连接 → CONNECTING；用户断开 → DISCONNECTED |
| CLOSING | 已开始关闭握手，禁止新业务写入 | 握手完成或 close 超时 → 清理及重连判定 |

PC 必须在开始发送 denoise 时标记 PROCESSING，包括正在上传的阶段；发送失败同样可能属于结果未知。状态是客户端本地概念，不增加业务 JSON 字段。

同一次故障可能同时触发 error 和 close 回调，两者必须走幂等的统一清理入口：只结束一次当前请求、只停止一次心跳、只安排一个重连定时器。每次本地拨号都生成新连接代次；回调和计时器关联到该代次，旧连接迟到的 close、message 或超时回调不得修改新连接状态。板端 connection_id 通过首次 heartbeat 绑定到本地连接代次，用于日志；hello 不再承担连接标识的传输。

error 回调先记录错误并开始本连接的关闭/清理；重连分类在取得该连接的 close 事件及关闭码后统一决定。如果协议栈未产生 close 事件，则在有界关闭超时后按异常断线归类。不得在 error 回调中立即开始独立重连，然后又在 close 回调中重复重连或忽略其不可重试的关闭码。

### 11.4 reconnect 策略与旧请求

- 只有 PC 发起重连，开发板持续监听，不主动向 PC 建连。重连成功后分配新 connection_id、重新发送功能算子列表 hello；PC 重新发现可用功能及算子，通过首条 heartbeat 绑定新 connection_id。
- PC 保存独立的“希望保持连接”标记。用户主动断开或 PC 程序退出时，先清除该标记并取消重连定时器，再关闭当前连接，避免主动 close 又触发自动重连。
- 对可恢复的断线，建议第 k 次重连前等待 `uniform(0, min(30000, 1000 * 2^k))` 毫秒，k 从 0 开始；连续失败时增加 k，一次最多一个拨号任务。连接在收到有效 hello 后持续存活 30 秒，才将 k 归零，避免反复建连即掉线时不断快速重试。
- 网络异常、心跳超时、hello 超时、板端退出（1001）、板端可恢复内部错误（1011）和握手 5xx 按上条退避重试；其余关闭码策略见下一节。HTTP 503 若带有效 Retry-After，至少等待其建议时间。
- 旧连接断开时，未取得完整匹配终态的请求记为“结果未知”，保留 PC 原始数据及本次提交记录；不自动发送下一组数据，也不自动重放旧请求。PC 上层决定重试当前数据或明确放弃，再继续流程。
- 重试必须等新连接 READY，使用新 request_id；同一数据可能重新计算。板端不保留旧结果、不跨连接去重、不将旧结果发到新连接。旧计算尚未退出时，新请求可能收到 BUSY，此时在现有健康连接上退避，不必再次建连。

退避和重新连接机制参考 [RFC 6455 第 7.2.3 节](https://www.rfc-editor.org/rfc/rfc6455.html#section-7.2.3)，本协议中的具体时间值及旧任务策略是设计建议。

### 11.5 close、服务退出与关闭码

主动断开顺序：停止新业务写入 → 设置 CLOSING → 发出 Close 控制帧 → 等待对端 Close，最多 close_timeout_ms → 关闭底层连接并统一清理。收到对端 Close 时及时回应；进入关闭流程后不再发送业务 JSON。连接已损坏时直接清理，不要求一定能送达关闭帧。

PC 主动断开默认立即结束当前连接；开发板收到关闭后取消本连接的降噪任务。需要保留当前结果时，PC 应先等 result/error，再主动关闭。开发板进程退出时停止接受新连接及新任务，对现有连接发送 1001（SERVER_SHUTDOWN），取消各连接任务，完成有界关闭后退出；正在处理的 PC 请求按“结果未知”收尾。

| 关闭码 | 本协议中的场景 | PC 默认自动重连策略（仍启用连接时） |
| --- | --- | --- |
| 1000 | 正常关闭；用户断开或协议不兼容导致的本地主动关闭 | 不自动重连；可由用户再次连接 |
| 1001 | 开发板服务退出/重启 | 退避重连；PC 自身退出则关闭连接保持标记，不重连 |
| 1002 | WebSocket 帧协议错误 | 停止，修正实现后再连接 |
| 1003 | 发送了不支持的二进制消息 | 停止，修正发送方式 |
| 1007 | 非法 UTF-8 | 停止，修正编码 |
| 1008 | 应用策略违例，如同连接并发请求、hello 前发业务请求、上传超时 | 停止，报告原因并修正/排查 |
| 1009 | 请求消息字节数过大 | 停止，减小数据或调整板端预算 |
| 1011 | 无法继续使用连接的服务端内部错误 | 退避重连 |
| 4000 | 本协议自定义：HELLO_TIMEOUT，PC 等待 hello 超时 | 退避重连 |
| 4001 | 本协议自定义：HEARTBEAT_TIMEOUT，任一端 Ping/Pong 保活超时 | 退避重连 |
| 1006（仅本地事件值） | 未收到有效 Close 帧便丢失连接 | 退避重连；不得把 1006 放进 Close 帧发送 |

未列出的远端关闭码默认停止自动重连并上报原始码，交由上层决定。关闭原因用于日志，不解析原因字符串驱动流程。1000～1011 中上述标准码的意义和 1006 的限制参见 [RFC 6455 第 7.4 节](https://www.rfc-editor.org/rfc/rfc6455.html#section-7.4)；4000、4001 是本协议选用的私有用途代码。

### 11.6 连接保活、业务超时与取消

- 建议双方均每 20 秒发送一次 WebSocket Ping，并对对端 Ping 回复 Pong；每端独立从连接建立/最近一次收到 Pong 起计时，60 秒没有 Pong 时用 4001 关闭并统一清理。PC 可使用协议栈自动 Pong，并需由其连接管理器主动 Ping 来检测板端存活；不新增 JSON ping/pong。具体保活和状态上报周期采用第 5.2 节的本地联调配置，不通过 hello 下发。
- 在上传、计算和等待结果时都继续处理控制帧，普通数据消息不替代 Pong。读循环不能因同步执行算法而长期停住，所有出站数据和控制帧应遵守所选 WebSocket 库的并发写约束。
- 从一条上传消息首个数据帧到完整消息收到，超过 upload_timeout_ms 时用 1008 关闭；该预算不限制空闲等待。请求字节数超限用 1009 关闭，不保证 JSON error，不能继续无界读取正文。
- 收到完整消息起，到 JSON 解码、校验、算法执行、结果校验及 JSON 编码结束，整个阶段受 processing_timeout_ms 限制。到期且连接可用时，只发送一次 PROCESSING_TIMEOUT，丢弃迟到结果，并发出计算取消信号。
- 在计算任务及工作内存实际退出/释放前，不能释放全局执行槽。因此业务超时或重连后的新请求可能收到 BUSY，不能通过反复建连无限启动后台任务。
- 断线取消本连接任务，不保存待领取结果；取消和任务正常完成竞态时，同一请求仍只能存在一个终态。没有可用连接时无需再制造 JSON error；不得把任务结果路由到新连接。
- 写入受 write_timeout_ms 限制；写入失败时关闭连接，不追加第二个终态。结果中途断开，PC 丢弃不完整消息。
- PC 等待结果的预算应覆盖服务端处理预算、写预算及网络余量；如果本地请求等待超时，需要关闭当前连接、将结果标记未知，再按连接策略恢复，不能直接在同一连接发送下一组数据。
- JSON error（包括 PROCESSING_TIMEOUT 和 BUSY）通常保持连接；PC 收到终态后按上层任务策略处理。v1 不支持断点续传、持久化任务、取消业务消息或断线结果恢复。

## 12. 设备状态心跳 heartbeat

### 12.1 发送方式与示例

开发板在每条连接 hello 之后立即安排首条 heartbeat，后续按 report_interval_ms（建议 5000 毫秒）主动上报。PC 无需发送查询请求，也无需回复业务 ACK。空闲、正常降噪及取消清理期间均持续上报；连接进入 CLOSING 后停止上报。所有连接共享一次板端指标采集，分别附加本连接的 connection_id 和 seq。

每条 heartbeat 都携带设备静态信息及最新动态指标，便于 PC 独立消费任一条完整消息。静态信息可在板端缓存；动态指标定期重新采集。以下硬件和系统名称仅为格式示例，不表示已识别当前开发板：

```json
{
  "version": 1,
  "type": "heartbeat",
  "connection_id": "074218cf-9982-4bf3-88da-235318d9ed13",
  "seq": 1,
  "timestamp": "2026-10-26T03:20:00Z",
  "sample_age_ms": 100,
  "uptime_sec": 3600,
  "device": {
    "id": "board-001",
    "model": "example-board",
    "os": {
      "name": "Linux",
      "distribution": "Example Linux",
      "version": "1.0",
      "kernel": "6.x-example",
      "arch": "arm64"
    }
  },
  "cpu": {
    "model": "Example ARM CPU",
    "logical_cores": 4,
    "usage_percent": 23.5,
    "sample_window_ms": 5000
  },
  "memory": {
    "total_bytes": 1073741824,
    "available_bytes": 805306368,
    "used_bytes": 268435456,
    "usage_percent": 25.0
  },
  "service": {"state": "processing", "active_requests": 1},
  "collection_errors": []
}
```

### 12.2 字段及采集口径

示例中的顶层字段及嵌套字段均发送。只有下文明确可空的设备属性和采集值允许 null；缺失指标不能伪装成 0、空字符串或沿用未标明年龄的旧值。

| 字段 | 类型与含义 |
| --- | --- |
| `connection_id` | 开发板为当前连接生成的 UUID；PC 从本连接首条有效 heartbeat 绑定，后续必须保持一致 |
| `seq` | 正整数，本连接从 1 开始，每实际发送一条 heartbeat 加一；新连接重新计数 |
| `timestamp` | 本次动态指标快照采集完成时间，UTC RFC 3339 字符串；用于日志和显示 |
| `sample_age_ms` | 发送时距离该快照采集完成经过的非负整数毫秒，使用板端单调时钟计算；供 PC 识别延迟/缓存快照 |
| `uptime_sec` | 操作系统自启动至采集时的运行秒数，非负数，可空；不等于服务运行时长 |
| `device.id` | 稳定设备标识，建议由部署配置提供，重连/重启保持；未知时 null，不使用 connection_id 冒充 |
| `device.model` | 开发板型号，可由部署配置或平台硬件信息读取；未知时 null |
| `device.os` | 系统名称、发行版名称、发行版版本、内核版本、Go 风格架构名（如 arm64）；各字符串独立可空 |
| `cpu.model` | CPU 型号字符串，可空；与设备型号分别报告 |
| `cpu.logical_cores` | 当前在线逻辑 CPU 数，正整数，可空；不是物理核心数 |
| `cpu.usage_percent` | 板端操作系统视图下全部 CPU 的窗口平均使用率，0～100，可空；不是降噪进程单独占用率，也不按核心数累加到 400% 等 |
| `cpu.sample_window_ms` | 本次 CPU 使用率实际采用的采样间隔，正整数；CPU 使用率不可用时为 null |
| `memory.total_bytes` | 操作系统可用物理 RAM 总量（不含 swap），正整数，可空 |
| `memory.available_bytes` | 操作系统估计的可用 RAM，非负整数，可空 |
| `memory.used_bytes` | total_bytes − available_bytes，非负整数，可空；所需源值缺失时为 null |
| `memory.usage_percent` | 100 × used_bytes / total_bytes，0～100，可空 |
| `service.state` | 板端全局降噪执行状态：idle、processing、recovering |
| `service.active_requests` | 当前实际占用的全局执行槽数，非负整数，包含正在取消但尚未退出的任务 |
| `collection_errors` | 本次不可用字段说明数组；正常为空，每项含 field、code 及可选 message |

service.state=idle 表示无任务占槽；processing 表示存在执行中的任务；recovering 表示任务已发出取消但仍占用执行槽。这个全局状态只供监控，不能作为某个 request_id 已成功、已失败或可立即提交的确认；请求接收仍可能因竞态返回 BUSY。

CPU 使用率需要两个有效采样点；服务刚启动且没有基线时发送 null，sample_window_ms 同时为 null，并报告 WARMING_UP，不能为等待 CPU 采样而延迟首次 hello。采用 Linux 时建议读取 /proc/stat 两次：总时间计 user、nice、system、idle、iowait、irq、softirq、steal，不再重复累加 guest/guest_nice；非忙时间取 idle+iowait，按时间增量计算使用率。计数回退、CPU 在线集合改变、增量为零或计算结果超出有效范围时，本次 CPU 使用率为 null 并报告 SAMPLE_RESET，重新建立基线。

Linux 内存建议读取 /proc/meminfo，按 MemTotal 与 MemAvailable 计算上述字段；内核文件中的 kB 转为 byte 时乘 1024。缺少 MemAvailable 时，不用 MemFree 冒充，available/used/usage 字段为 null 并报告 NOT_AVAILABLE。字段含义及计数器限制参见 [Linux /proc 官方文档](https://www.kernel.org/doc/html/latest/filesystems/proc.html)；在线逻辑 CPU 视图参见 [Linux CPU 拓扑文档](https://docs.kernel.org/admin-guide/cputopology.html)。

Linux 发行版信息按 [os-release 官方定义](https://www.freedesktop.org/software/systemd/man/latest/os-release.html) 读取，系统与内核信息由平台接口读取。设备型号可通过平台硬件信息或部署配置取得。字段不可采集、权限不足或功能不支持时如实返回 null。板端 OS 尚未确定，Linux 采集来源是实现建议；其他系统须以等价数据源满足相同的对外口径。

collection_errors.field 使用类似 `cpu.usage_percent`、`device.model` 的路径；稳定 code 为 WARMING_UP、NOT_AVAILABLE、PERMISSION_DENIED、READ_FAILED、SAMPLE_RESET。它们只说明监控采集状态，不生成请求级 JSON error，不中断降噪。缺失的可空字段都应有对应路径的说明；对依赖同一源值而不可用的派生字段也分别说明。非空指标必须是有限数值或合法字符串。

### 12.3 PC 更新、过期与断线

- PC 按 type=heartbeat 独立消费，不要求 request_id。先确认消息来自当前本地连接代次；首条有效 heartbeat 绑定板端 connection_id，后续要求 UUID 一致且 seq 比上一条大，再更新监控。旧连接消息不得绑定到新连接；同一连接 connection_id 改变视为协议不兼容，关闭并停止自动重连。重复/倒退序号不得覆盖当前显示。
- 无业务请求时也持续接收 heartbeat；等待降噪结果时持续更新监控，但不把 heartbeat 视为结果，也不延长该降噪请求的等待预算。
- 收到 hello 后即可显示设备概要，动态指标在首条有效 heartbeat 前显示“等待状态”；hello 不作为动态指标的新鲜度样本。之后用 PC 本地单调时钟统计距离最近一次有效 heartbeat 到达的时间，加上该消息的 sample_age_ms，作为接收侧估计年龄；达到 report_stale_timeout_ms（建议 15000）时标记“状态过期”。sample_age_ms 已达阈值的快照、seq 倒退或其他连接的消息不能恢复新鲜状态。该估计不包含本条消息的网络传输及正在进行的 WebSocket 写入耗时，不承诺精确的端到端采集年龄。
- 采集字段为 null 时，该字段显示“未知/不可用”；message 中其他有效指标继续使用。不得继续把上一次正常值显示为当前值。
- 设备状态过期时可提示监控异常；只要 Ping/Pong 仍健康，不仅因状态上报过期就自动断开或重放任务。Ping/Pong 超时、网络断线仍按第 11 节关闭并重连。
- 重连时清空上一连接的 seq、新鲜度和 CPU 显示窗口，重新等待 hello 及首条 heartbeat；旧状态可作为带“历史”标记的日志保留。timestamp 可能受板端时钟未校准或校时影响，不使用 PC 与板端墙上时间相减判断超时。

### 12.4 采集调度与发送约束

指标采集与降噪执行独立调度，使用有界采集时间，不在算法线程中等待完整的 CPU 采样间隔。静态信息缓存，CPU 计数使用相邻采样点差分；动态采集失败仍发送含 null 和 collection_errors 的心跳。多条连接共享采集快照，避免每条连接重复扫描系统资源。

heartbeat 与 result/error 共用一条 WebSocket，由同一发送调度器串行发送完整消息。尚未开始写出的 heartbeat 最多保留最新一份，过期快照直接替换；不能无限排队，也不能阻塞算法等候某个 PC 消费心跳。单个慢连接的写超时或心跳队列不影响其他连接的监控发送。

WebSocket 文本数据消息之间不能随意交叉写入：一条大的 result 写入期间，下一条 JSON heartbeat 可以延后，写完后优先发送最新快照并更新 sample_age_ms；不把旧缓存伪装成刚采集值。Ping/Pong 控制帧仍由协议栈按标准处理。建议单条 heartbeat 限制在 8192 UTF-8 字节内，静态字符串和错误文案有界截断（保留有效 UTF-8）；发生错误时也遵守同一预算。连接关闭时停止本连接的定时发送并清理待发消息。

## 13. 实现分层与验收范围

分成 WebSocket 传输、JSON 与请求校验、单算法及参数解析、算法执行与 mz5_11 预处理、结果组装、设备指标采集与定期上报六部分。算法接口接收 float64 输入及结构化参数，不依赖 WebSocket、绘图或 CSV 文件路径。Go 包布局、WebSocket 库、数学库及部署方式留给实现者选择。

后续实现至少验证：

1. 样例 CSV 经 PC 转换、发送、返回后，时间轴、名称、维度及顺序完全一致；普通算法对所有通道使用相同方法及参数，none 请求整组数据不变，mz5_11 参考通道数值不变。
2. 九种算法与脚本对照，覆盖边界、短序列、极小/负数、小波层数及 mz5_11 成功/标定跳过；采用 float64 对照，逐算法记录误差预算，不能仅以“结果不同”判断算法有效。
3. 单算法及参数默认值解析，拒绝方法数组、逐通道算法配置、未知参数，验证缺失引用、形状、非有限值和限额；处理前整体校验失败不产生半成品。
4. 通道失败保留完整原列，partial 明确，统计与逐列状态一致，输出不存在 NaN/Infinity。
5. 连续串行请求、全局 BUSY、同连接并发违例、双向 Ping/Pong、超时取消和断线取消，确认实际执行槽不提前释放。
6. JSON 跨帧读取、输入/输出字节限制以及长响应写入失败；开发板实测峰值内存和处理延迟后调整本地运行预算，并同步 PC 联调配置。
7. 首次建连、hello 等待超时/无效、正常关闭、板端重启、网络异常和握手失败的状态转移；主动关闭不重连，可恢复故障按退避重连。
8. 同一次断线同时触发 error/close 时只安排一个重连；旧连接回调不干扰新连接，重连后重新验证功能列表并从首条 heartbeat 绑定新 connection_id；旧请求不自动重放、旧结果不串到新连接。
9. hello 后首次及周期性 heartbeat，空闲/降噪/取消期间均能监控；消息插入不打断串行请求，也不重置请求超时。验证 CPU 窗口、内存字节与百分比计算、设备/系统字段以及采集失败 null 的语义。
10. 状态过期、板端时钟跳变、重连序号重置、旧快照/旧连接消息、慢连接丢弃待发旧心跳，以及多连接共享采样；状态过期与连接失效分别处理，发送队列和采集任务无无界堆积。
11. hello 包含设备概要及功能算子列表，设备字段与 heartbeat 使用相同路径、类型和单位，未知设备属性为 null 不影响功能发现；类别和算子去重、未知类别忽略、未实现功能不声明、重连后设备与功能列表刷新。参数仍仅在处理请求中传递，限额及心跳设置使用本地联调配置。

实现前仍需从用户或板端环境取得 CPU 架构、可用 RAM、操作系统和目标处理延迟，以确定交叉编译与部署配置；这些信息不改变本草案的数据与消息结构。本轮仅完成协议设计，不搭建服务或移植算法。
