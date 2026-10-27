# VTA 网络通信协议

协议版本：`1`；当前算法版本：`1.0.0`；更新日期：2026-10-27。

本文用于 PC 上位机与开发板 Go 服务的接口开发和联调，按当前实现整理。
PC 每次发送一份完整质谱数据，指定一种降噪算法及其参数；开发板返回同结构结果，PC 再发送下一份。
当前实现降噪功能，后续功能通过能力发现扩展。本文中的设备型号、系统信息和处理耗时均为格式示例。

## 1. 接入方式

| 项目 | 约定 |
| --- | --- |
| 客户端 | PC 上位机，主动连接和重连 |
| 服务端 | 开发板，监听并接受连接 |
| 默认地址 | `ws://<board-ip>:8080/vta` |
| 路径 | 固定为 `/vta`，端口可通过服务启动参数配置 |
| 传输 | WebSocket；当前服务使用内网明文 `ws://`，未提供 TLS 或应用层鉴权 |
| 消息编码 | UTF-8 JSON 对象，每条 WebSocket 文本消息恰好一个对象 |
| 数值 | IEEE 754 float64，支持科学计数法 |
| 压缩 | v1 不启用 WebSocket 消息压缩 |
| 单连接并发 | 最多一个未完成的降噪请求 |
| 默认全局并发 | 最多一个实际执行任务，不排队 |

一次发送的内容是 CSV 解析后的数值数据，不是 CSV 文件、CSV 文本或 Base64。
WebSocket 库可以将一条消息拆成多个帧，但业务层必须收到完整消息后再解析，不按换行分包，也不拼接多个 JSON 对象。

JSON 通用要求：

- 所有业务消息都带整数 `version: 1` 和字符串 `type`；字段名、算法名和代码区分大小写。
- 拒绝请求中的未知字段、未知算法参数及任意层级的重复对象键。
- PC 应忽略响应中新增的未知字段，以兼容 v1 的可选字段扩展。
- 数值不得使用字符串、布尔值、`null`、`NaN` 或无穷值；float64 溢出，以及非零数下溢为零，均拒绝。
- `version` 和整数算法参数使用整数字面量，例如 `3`；不接受 `3.0`、`3e0`。
- JSON 键顺序无意义，数组顺序有意义。不得为固定小数位显示而截断原始强度值。
- 只有设备信息、监控指标和错误响应的 `request_id` 按下文允许 `null`，质谱数据和算法参数不允许。

## 2. 消息与交互顺序

| `type` | 方向 | 说明 |
| --- | --- | --- |
| `hello` | 开发板 → PC | 每次连接成功后主动发送一次设备概要与功能清单 |
| `heartbeat` | 开发板 → PC | hello 之后首次发送，后续默认每 5 秒上报设备状态 |
| `denoise` | PC → 开发板 | 提交完整数据及一个算法配置 |
| `result` | 开发板 → PC | 本次完整结果，状态为 `ok` 或 `partial` |
| `error` | 开发板 → PC | 本次请求整体拒绝或失败，不包含结果矩阵 |

正常交互：

1. PC 发起 TCP / HTTP WebSocket Upgrade。
2. 握手成功后，PC 等待并验证开发板主动发送的 `hello`，确认需要的功能和算法可用。
3. 开发板发送首次 `heartbeat`，之后周期发送；PC 无需回复业务 ACK。
4. PC 发送一条 `denoise`，进入等待结果状态。
5. 等待期间继续处理 `heartbeat` 和 WebSocket Ping/Pong。
6. 收到属于当前请求的完整 `result` 或 `error` 后，结束该请求，再决定是否发送下一份数据。
7. 不再使用连接时进行 WebSocket Close 握手。

PC 收到有效 hello 后即可发送业务请求，无需等待首条 heartbeat。心跳可穿插在任意两条业务消息之间；
它不代表降噪完成，不释放当前请求状态，也不延长请求等待超时。

`connect`、`open`、`reconnect`、`close` 是连接操作或本地事件，**不是 JSON 消息类型**。
协议没有 JSON `connect`、`close`、`ping`、`pong`、ACK、进度消息、取消请求或结果拉取接口。
WebSocket 控制帧 Ping/Pong 与 JSON `heartbeat` 分工不同：前者检测连接存活，后者报告设备状态。

## 3. hello：设备概要与能力发现

开发板在每条新连接上主动发送一次，PC 不发送 hello 请求，也不需要确认响应。

```json
{
  "version": 1,
  "type": "hello",
  "device": {
    "id": "board-001",
    "model": "example-board",
    "os": {
      "name": "linux",
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
      "ewma", "kalman", "mean", "median", "mz5_11", "none",
      "quantile", "savitzky_golay", "std_threshold", "wavelet"
    ]
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `device.id` | string / null | 稳定设备标识，建议部署时通过 `--device-id` 配置；不同于连接标识 |
| `device.model` | string / null | 设备型号，可通过 `--device-model` 配置 |
| `device.os.name` | string / null | 操作系统名称，如 `linux` |
| `device.os.distribution` | string / null | 发行版名称 |
| `device.os.version` | string / null | 发行版版本 |
| `device.os.kernel` | string / null | 内核版本 |
| `device.os.arch` | string / null | Go 风格架构名，如 `amd64`、`arm64` |
| `cpu.model` | string / null | CPU 型号 |
| `cpu.logical_cores` | integer / null | 正整数，当前在线逻辑 CPU 数 |
| `memory.total_bytes` | integer / null | 正整数，操作系统可用物理 RAM 总量，单位 byte，不含 swap |
| `functions` | object | 功能类别名称到算子名称数组的映射 |

上表中的设备字段均发送，无法取得时为 `null`，PC 显示“未知”；这不影响功能发现。
hello 不携带动态使用率、算法参数、参数默认值、运行限额或连接标识。

当前只声明 `denoise` 类别；将来可增加去基线等类别，PC 忽略暂不支持的新类别。
每类算子数组必须非空、名称非空且无重复；只声明已实现并启用的算子，数组顺序不代表执行链。
没有启用的功能时 `functions` 可以是 `{}`。PC 不得发送设备未声明支持的算法。

## 4. denoise：完整数据请求

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

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `version` | integer | 是 | 固定 `1` |
| `type` | string | 是 | 固定 `denoise` |
| `request_id` | string | 是 | 1～64 个 ASCII 字符，仅 `[A-Za-z0-9_-]`，建议每次使用新 UUID |
| `data` | object | 是 | 完整数据集，结构如下 |
| `data.time_unit` | string | 是 | 固定 `min`，对应 CSV 的 `Time/min` |
| `data.time` | number[] | 是 | N ≥ 1，有限、非负、严格递增，不自动排序或重采样 |
| `data.channels` | string[] | 是 | C ≥ 1，名称非空且唯一，每项最多 64 个 UTF-8 字节，不得为 `Time/min` |
| `data.intensity` | number[][] | 是 | N 行、每行 C 列，允许负值和零 |
| `config` | object | 是 | 一种算法及其参数 |
| `config.method` | string | 是 | 必须为 `hello.functions.denoise` 中的一个算子 |
| `config.params` | object | 否 | 省略视为 `{}`，不可为 `null`，仅包含该算法接受的参数 |

`intensity[i][j]` 对应 `time[i]` 和 `channels[j]`。通道名作为标识保留，不要求从名称推断 m/z。
PC 将 CSV 首列拆为 `time`，其余表头拆为 `channels`，其余数据拆为 `intensity`。
返回后按 `Time/min` 加通道名重建 CSV；保持结构和数值，不保证原 CSV 的 BOM、小数位或数字文本写法。

每次请求配置独立，不继承上一次配置。普通算法沿每个通道的时间序列计算，同一次请求的全部通道使用同一种算法和参数。
不支持算法数组、算法链、逐通道配置。时间轴不参与降噪，窗口和滞后均按样本点计算，而非分钟。
`request_id` 仅用于响应匹配，不是去重或幂等键；服务端不缓存已完成请求。

## 5. 算法及参数

以下为当前默认值。未提供的参数由服务端补齐，未知参数会拒绝；所有数值参数必须有限。
`max_window_size` 默认 4095。窗口大于序列长度时，`mean`、`median`、`quantile`、`std_threshold` 仍允许执行。

| `method` | `params` 默认值 | 约束与行为 |
| --- | --- | --- |
| `none` | `{}` | 所有数据原值保留，适合通信往返测试 |
| `mean` | `{"window_size":5}` | 正整数窗口，不超过窗口上限；reflect 边界均值 |
| `std_threshold` | `{"window_size":11,"sigma_multiple":3}` | 正奇数窗口，不超过上限；`sigma_multiple ≥ 0`；局部均值加倍数标准差阈值置零 |
| `ewma` | `{"alpha":0.3}` | `0 < alpha ≤ 1`；指数移动平均 |
| `median` | `{"window_size":5}` | 正整数窗口，不超过上限；reflect 边界中值 |
| `quantile` | `{"window_size":11,"quantile":0.75}` | 正整数窗口，不超过上限；`0 ≤ quantile ≤ 1`；低于局部分位数的原值置零 |
| `savitzky_golay` | `{"window_size":11,"polyorder":3}` | 正奇数窗口，不超过上限；整数 `0 ≤ polyorder < window_size` |
| `wavelet` | `{"wavelet":"db4","level":3,"threshold_type":"soft"}` | 仅支持 db4；整数 `1 ≤ level ≤ 16`；阈值类型 `soft` 或 `hard` |
| `kalman` | `{"process_variance":1e-5,"measurement_variance":0.1}` | `process_variance ≥ 0`，`measurement_variance > 0` |
| `mz5_11` | 见下表 | 基于指定参考通道进行标定、平滑及参考信号扣除 |

Savitzky–Golay 在 N 小于窗口时退化为 `min(3,N)` 点 reflect 均值；wavelet 在 N < 8 时同样退化。
wavelet 根据序列长度调整实际分解层数；退化和层数调整均返回 `partial`，在逐通道结果中说明实际使用的方法和参数。

### 5.1 mz5_11 参数

下面是 `denoise.config` 的示例片段，需配合包含这些参考通道及至少一个目标通道的完整数据发送：

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

| 参数 | 类型 | 默认值 | 约束 |
| --- | --- | --- | --- |
| `no_gas_mz` | string[] | 无，必填 | 非空、无重复，全部是输入通道名；至少留下一个非参考通道 |
| `T_idx` | integer[2] | 可省略 | 零起点 `[start, stop)`，`0 ≤ start < stop ≤ N`，至少 10 点；与 `T_frac` 互斥 |
| `T_frac` | number | `0.1` | `0 < T_frac ≤ 1`；未指定 `T_idx` 时，标定段为 `[0, min(N, max(10, floor(N*T_frac))))` |
| `k` | integer | `5` | ≥ 3 的奇数，不超过窗口上限 |
| `max_lag` | integer | `30` | 非负整数，不超过窗口上限 |
| `aggregation` | string | `mean` | 当前仅支持 `mean` |

该方法要求 N ≥ 10。参考通道按输入顺序取均值构成底谱，参考通道本身原值保留，其余通道使用同一配置处理。
标定区间的实验有效性由 PC/实验流程保证；低波动、幅度系数超出范围或标定失败时，保留对应目标通道原值并返回诊断。

## 6. result：结果与逐通道状态

下面对应第 4 节的均值请求；数值舍入和耗时仅用于示例：

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
    "intensity": [
      [1.3333333333333334e-12, 4.333333333333333e-12],
      [2e-12, 5e-12],
      [2.6666666666666667e-12, 5.666666666666667e-12]
    ]
  },
  "stats": {"total": 2, "processed": 2, "skipped": 0, "errors": 0},
  "column_results": [
    {
      "channel": "mz28", "status": "processed",
      "requested_method": "mean", "applied_method": "mean",
      "effective_params": {"window_size": 3}, "code": "PROCESSED"
    },
    {
      "channel": "mz44", "status": "processed",
      "requested_method": "mean", "applied_method": "mean",
      "effective_params": {"window_size": 3}, "code": "PROCESSED"
    }
  ],
  "timing": {"processing_ms": 1.2}
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | string | 与本次请求完全一致 |
| `status` | string | `ok` 或 `partial` |
| `algorithm_version` | string | 算法实现版本，用于追溯数值行为，不同于协议版本 |
| `data` | object | 与请求相同结构；时间轴、通道名、顺序及 N × C 形状不变 |
| `stats` | object | 整数计数：`total = processed + skipped + errors = C` |
| `column_results` | object[] | 按输入通道顺序恰好 C 项 |
| `timing.processing_ms` | number | 取得执行槽后算法、预处理和结果校验的毫秒耗时，不含网络传输和 JSON 编解码 |

逐通道字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `channel` | string | 输入通道名称 |
| `status` | string | `processed`、`skipped` 或 `error` |
| `requested_method` | string | 本次请求的算法名 |
| `applied_method` | string | 实际算法名；保留原值时为 `none`，短序列退化时为 `mean` |
| `effective_params` | object | 实际应用的完整参数；原值保留时为 `{}` |
| `code` | string | 稳定机器码，见下表 |
| `message` | string，可选 | 供人阅读，不应解析文案控制程序流程 |
| `calibration` | object，可选 | mz5_11 成功标定信息：`T_idx` 为整数二元数组，`lag` 为整数，`a`、`c` 为数值 |

| `code` | 通道 `status` | 含义 | 导致整体 `partial` |
| --- | --- | --- | --- |
| `PROCESSED` | `processed` | 按配置完成处理 | 否 |
| `NONE` | `skipped` | 请求选择 none，原值保留 | 否 |
| `REFERENCE_CHANNEL` | `skipped` | mz5_11 参考通道按约定保留 | 否 |
| `FALLBACK_SHORT_SERIES` | `processed` | 短序列退化为均值处理 | 是 |
| `WAVELET_LEVEL_ADJUSTED` | `processed` | 小波实际层数已调整 | 是 |
| `CALIBRATION_LOW_VARIATION` | `skipped` | 标定信号波动不足，保留原值 | 是 |
| `CALIBRATION_SCALE_OUT_OF_RANGE` | `skipped` | 标定幅度系数超出范围，保留原值 | 是 |
| `CALIBRATION_FAILED` | `skipped` | 标定失败，保留原值 | 是 |
| `ALGORITHM_ERROR` | `error` | 单通道算法失败，保留原值 | 是 |
| `NON_FINITE_RESULT` | `error` | 计算结果含非有限值，保留原值 | 是 |
| `INVALID_RESULT_LENGTH` | `error` | 返回长度异常，保留原值 | 是 |

`partial` 仍包含完整矩阵，PC 可保存但必须标明异常或降级。即使所有目标通道失败，仍可能是 `partial`，
不能仅凭收到 `result` 就判定全部降噪成功。失败通道整列保留原值，不返回半列、不用 `null` 替换非法结果。

## 7. error：请求整体失败

```json
{
  "version": 1,
  "type": "error",
  "request_id": "ms-0002",
  "error": {
    "code": "INVALID_SHAPE",
    "message": "channel count mismatch",
    "field": "data.intensity[1]"
  }
}
```

`error.code`、`error.message` 必填，`error.field` 可选，数组下标从 0 开始。
请求 ID 可安全解析且格式合法时回传，否则 `request_id` 为 `null`，包括 JSON 无法解析等情况。
不得依赖错误文案语言或文字内容；按 code 处理，field 用于定位。

| `error.code` | 含义与处理建议 |
| --- | --- |
| `INVALID_JSON` | JSON 语法错误、重复键、尾随多个值或嵌套过深；修正序列化 |
| `UNSUPPORTED_VERSION` | 不支持的协议版本；核对双方版本 |
| `INVALID_REQUEST` | 消息类型、必填项、字段类型或未知字段错误 |
| `INVALID_SHAPE` | 时间轴、通道轴为空，或矩阵尺寸不一致 |
| `INVALID_DATA` | 非法通道、时间轴、数值类型或 float64 范围错误 |
| `INVALID_CONFIG` | 算法参数、引用或未知参数错误 |
| `UNSUPPORTED_METHOD` | 不支持的算法，或不支持的小波名称 |
| `LIMIT_EXCEEDED` | 点数、通道数或单元数超过预算 |
| `BUSY` | 全局实际执行槽已占用，本次未执行；可在健康连接上延迟后重新提交 |
| `PROCESSING_TIMEOUT` | 本次处理超过预算，结果放弃；实际任务尚未退出时仍可能占槽 |
| `RESPONSE_TOO_LARGE` | 编码后的完整响应超过预算；减少数据或同步调整预算 |
| `INTERNAL_ERROR` | 请求整体处理故障；记录上下文并排查服务端 |

这些业务错误通常不关闭连接。收到已匹配的 error 后本次请求结束；它没有结果矩阵，PC 不应把原始输入标为降噪结果。
错误 ID 为 null 或无法与当前请求匹配时，记录原始错误并进行异常收尾，不将其当成其他请求的成功确认。

字面量 `NaN`、`Infinity` 属于 `INVALID_JSON`；合法 JSON 数字在质谱数据中溢出或非零下溢，属于 `INVALID_DATA`，
在算法参数中则属于 `INVALID_CONFIG`。请求整体按信封、数据、配置、执行槽顺序校验；不要依赖同层多个错误的返回顺序。
消息字节数超限、连接违例或网络损坏可能直接关闭连接，不保证还有 JSON error。

## 8. heartbeat：设备状态上报

开发板在 hello 之后立即发送首次心跳，后续默认每 5 秒发送，空闲和算法处理期间均持续上报。
心跳不含 `request_id`；同一设备的多个连接共享采集快照，但各自使用独立的 `connection_id`、`seq`。

```json
{
  "version": 1,
  "type": "heartbeat",
  "connection_id": "074218cf-9982-4bf3-88da-235318d9ed13",
  "seq": 1,
  "timestamp": "2026-10-27T03:20:00Z",
  "sample_age_ms": 100,
  "uptime_sec": 3600,
  "device": {
    "id": "board-001",
    "model": "example-board",
    "os": {
      "name": "linux",
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
    "usage_percent": 25
  },
  "service": {"state": "processing", "active_requests": 1},
  "collection_errors": []
}
```

设备静态字段含义与 hello 相同，此外：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `connection_id` | string | 服务端为本次连接生成的 UUID；重连后更换，不是稳定设备 ID |
| `seq` | integer | 本连接从 1 开始，每发送一条加一；重连后重置 |
| `timestamp` | string | 动态快照采集完成时间，UTC RFC 3339；用于显示、日志 |
| `sample_age_ms` | integer | 非负；发送时距快照采集完成的单调经过时间，单位毫秒 |
| `uptime_sec` | number / null | 系统自启动至采集时的秒数，非负，不是服务运行时间 |
| `cpu.usage_percent` | number / null | 全部 CPU 的窗口平均使用率，0～100，不是本进程使用率 |
| `cpu.sample_window_ms` | integer / null | CPU 使用率的实际采样间隔，正整数；使用率不可用时为 null |
| `memory.available_bytes` | integer / null | 非负，可用 RAM，不以空闲内存 MemFree 冒充 |
| `memory.used_bytes` | integer / null | `total_bytes - available_bytes` |
| `memory.usage_percent` | number / null | `100 × used_bytes / total_bytes`，0～100 |
| `service.state` | string | `idle`、`processing` 或 `recovering` |
| `service.active_requests` | integer | 非负，全局实际占用的执行槽数，包括尚未退出的取消任务 |
| `collection_errors` | object[] | 正常为空；每项含 `field`、`code` 及可选 `message` |

`idle` 表示无任务占槽；`processing` 表示存在执行中的任务；`recovering` 表示已发出取消，但任务尚未完全退出。
这只是设备全局快照，不是当前请求的终态，也不保证下一条请求一定不会收到 `BUSY`。

CPU 首次采样没有基线时，使用率及窗口为 null，并报告 `WARMING_UP`。其他采集错误码为
`NOT_AVAILABLE`、`PERMISSION_DENIED`、`READ_FAILED`、`SAMPLE_RESET`。
例如 `{"field":"cpu.usage_percent","code":"WARMING_UP"}` 是 collection_errors 中的一项。
采集失败不产生业务 error，不中断降噪；缺失指标显示“未知”，不能显示为 0 或继续把旧值当成当前值。

PC 更新监控时：

1. 确认消息来自当前连接；首次有效心跳绑定 `connection_id`，以后必须一致，seq 必须递增。
2. hello 只提供静态概要，首条有效心跳前动态指标显示“等待状态”。
3. 使用“本地单调时钟距最近有效心跳到达的时间 + 该消息 sample_age_ms”估计快照年龄。
4. 年龄达到默认 15000 毫秒时显示“状态过期”。这个估计不含网络传输耗时；不要用 PC 与板端墙上时间之差判断过期。
5. 重复/倒退 seq、旧连接消息或已经过期的快照不能恢复新鲜状态；重连后清空旧序号和新鲜度。

状态过期与连接失效分别处理：只要 Ping/Pong 正常，不仅因状态过期自动断线或重放请求。
较大的 result 正在写出时，JSON 心跳可能延后；写完后发送最新快照，控制帧仍由协议栈处理。

## 9. 连接、重连与关闭

### 9.1 建联与状态转换

建议 PC 状态：

| 状态 | 进入条件 | 后续处理 |
| --- | --- | --- |
| `DISCONNECTED` | 尚未连接或用户停止连接 | 用户启用后进入 CONNECTING |
| `CONNECTING` | TCP / HTTP Upgrade 进行中 | 成功进入 WAIT_HELLO；失败按错误分类处理 |
| `WAIT_HELLO` | 握手成功 | 有效 hello 后进入 READY；此前不能发送业务请求 |
| `READY` | 已验证 hello，且无未完成请求 | 发送 denoise 后进入 PROCESSING |
| `PROCESSING` | 已开始发送当前请求，等待终态 | 匹配的 result/error 后回到 READY；断线则结果未知 |
| `CLOSING` | 正在关闭 | 禁止新业务写入，完成关闭并统一清理 |
| `RECONNECT_WAIT` | 可恢复故障后的退避等待 | 等待到期且仍启用连接时再次 CONNECTING |

握手失败时尚未建立 WebSocket，不发送业务 JSON。错误路径返回 HTTP 404，
连接数满或服务正在退出返回 HTTP 503，并提供 `Retry-After: 1`。
其他无效握手可能被 HTTP 4xx 拒绝；PC 对 4xx 排查地址、Origin 或协议配置，对网络失败及 5xx 可退避重试。

PC 等待 hello 默认建议 15 秒，超时可用 Close 4000 关闭；无效 hello、版本不兼容、
hello 前收到其他业务消息或重复 hello，按协议不兼容关闭并停止自动重连。
同一连接在上一请求尚未结束时发送第二条应用请求，或在 hello 前发请求，服务端用 Close 1008 关闭，不执行排队。

### 9.2 重连与结果未知

仅由 PC 发起重连。建议对可恢复故障采用随机指数退避：第 k 次延时从
`0～min(30000, 1000 × 2^k)` 毫秒均匀采样，k 从 0 开始；有效 hello 后持续存活 30 秒才重置计数。
同一时刻只能有一次拨号或一个重连定时器；HTTP 503 的有效 Retry-After 是额外的最短等待约束。

网络 error 和 close 事件可能同时出现，需统一、幂等地清理；每次拨号使用本地连接代次，旧连接的迟到回调不得改变新连接状态。
用户主动断开时先清除“保持连接”标记、取消重连，再关闭，避免立即自动重连。

断线时，如果本次请求已开始发送但未收到完整、匹配的终态，应记为“结果未知”：

- 保留原始输入及提交记录，不将心跳、部分字节或旧连接响应当成结果。
- 新连接重新等待 hello；旧任务不会恢复，旧结果不会转发到新连接。
- 不自动重放旧请求，也不自动跳到下一份数据；由上层决定重试或放弃。
- 决定重试时使用新 request_id。同一数据可能被重复计算，服务端没有跨连接去重。
- 旧计算尚未实际退出时，新连接可能收到 BUSY；可在健康连接上等待，不需要反复重连。

### 9.3 关闭码

| 关闭码 | 场景 | PC 建议处理 |
| --- | --- | --- |
| `1000` | 正常关闭、用户断开或本地主动停止 | 不自动重连，用户可再次连接 |
| `1001` | 板端服务退出或重启 | 若仍希望连接则退避重连 |
| `1002` | WebSocket 帧协议错误 | 停止并修正实现 |
| `1003` | 使用了二进制数据消息 | 改为 JSON 文本消息后再连接 |
| `1007` | 非法 UTF-8 | 修正编码 |
| `1008` | 同连接并发请求、hello 前发请求、上传超时 | 停止，排查原因 |
| `1009` | 请求消息字节数过大 | 减少数据或调整双方预算 |
| `1011` | 无法继续使用连接的服务端错误 | 记录故障，退避重连 |
| `4000` | PC 定义的 HELLO_TIMEOUT | 退避重连 |
| `4001` | 本协议的 Ping/Pong 保活超时 | 退避重连 |
| `1006` | 本地观察到无正常 Close 帧的断线 | 退避重连；禁止在 Close 帧中发送此码 |

4000、4001 是本协议约定的私有码；其他库的超时关闭表现可能不同，PC 应保留原始码和原因。
未知关闭码默认停止自动重连，由上层处理；不要解析原因字符串来驱动流程。

关闭顺序为停止新业务写入、发送 Close 控制帧、等待对端回应、关闭底层连接并清理。
关闭等待默认 3 秒。PC 希望保留当前结果时，应先等待终态再关闭。
板端收到 SIGINT/SIGTERM 后停止接入，向已有连接发送 1001，取消任务并有界退出。

## 10. 默认限额、保活与超时

下表为当前服务默认值及 PC 配套建议，**不在 hello 中下发或协商**。修改部署配置时必须同步 PC。

| 项目 | 默认值 | 说明 |
| --- | --- | --- |
| 最大请求消息 | 16777216 bytes（16 MiB） | 完整 UTF-8 JSON，包含封装和空白；超限关闭 1009 |
| 最大结果消息 | 33554432 bytes（32 MiB） | 编码后的完整 result；超限返回 RESPONSE_TOO_LARGE |
| 最大时间点数 N | 100000 | 与其他维度限制同时满足 |
| 最大通道数 C | 1024 | 与其他维度限制同时满足 |
| 最大强度单元数 N × C | 500000 | 不含时间轴 |
| 最大算法窗口 | 4095 | 具体算法还有奇偶等约束 |
| 单连接未完成请求 | 1 | v1 固定，不可配置成并发模式 |
| 全局实际执行任务 | 1 | 默认不排队，超出返回 BUSY |
| 最大连接数 | 4 | 超出时 Upgrade 前返回 HTTP 503 |
| 上传超时 | 30000 ms | 一条请求上传阶段的预算，不限制空闲等待 |
| 服务端处理超时 | 120000 ms | 完整收到请求后，包含解码、校验、算法和结果编码 |
| 单次写超时 | 10000 ms | 写失败关闭连接，不追加第二个终态 |
| Ping 周期 | 20000 ms | 使用 WebSocket 控制帧 |
| Pong 超时 | 60000 ms | 从建联或最近一次收到 Pong 起计时 |
| JSON 心跳周期 | 5000 ms | hello 后首次立即发送 |
| PC 状态过期阈值 | 15000 ms | 仅用于监控新鲜度，不等同断线 |
| PC 建联 / hello 等待 | 5000 / 15000 ms | PC 本地配置建议 |
| Close 等待 | 3000 ms | 超时后关闭底层连接 |

双方及时回复 Ping；在上传、计算和等待结果期间仍须读取控制帧。普通数据或 JSON heartbeat 不能替代 Pong。
全局执行槽在计算和工作资源实际退出前不释放，因此 PROCESSING_TIMEOUT 之后仍可能暂时 BUSY。

PC 请求等待预算需覆盖上传、服务端处理、写入和网络余量；示例客户端默认总等待 150 秒，可用 `--timeout` 调整，
慢速网络下应加大到足以覆盖上传耗时。心跳不会重置此预算。
PC 本地请求超时后应关闭连接并将结果记为未知，不能直接在原连接发送下一份。

## 11. 联调示例与相关文档

仓库根目录启动本机 Go 服务：

```sh
go run ./cmd/vta-service --listen :8080 --device-id test-board
```

另开终端，从仓库根目录进入 example：

```sh
cd example
uv run --locked python client.py \
  --url ws://127.0.0.1:8080/vta \
  --method mean \
  --params '{"window_size":5}' \
  --save-json
```

连接板卡时把地址改为板卡 IP。默认读取 `docs/cleandata_ms.csv`（1911 × 63），输出位于 `example/output/`。
使用 `--method none` 验证原值往返，使用 `--repeat 3` 验证同一连接串行请求。
示例客户端完成后关闭连接，断线或超时直接报错，不实现上述生产 PC 的自动重连管理。

相关文件：

- [Python 客户端使用说明](../example/README.md)
- [编译、联调与部署配置](deployment.md)
- [测试与验证记录](verification.md)
- [原协议设计记录](superpowers/specs/2026-10-26-websocket-denoise-protocol-design.md)
- [Go 消息结构](../internal/protocol/models.go)、[请求校验](../internal/protocol/decode.go)、[默认预算](../internal/protocol/defaults.go)

维护约定：请求字段、参数或响应语义变化时同步更新本文、Go 模型/校验与客户端。
不兼容的结构或语义变更升级协议版本；影响计算结果的实现或默认参数变更升级算法版本。
