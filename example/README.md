# Python 测试客户端（uv）

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
`pyproject.toml` 声明依赖，`uv.lock` 锁定版本；`uv run --locked` 自动创建独立的 `.venv` 并安装依赖。
客户端只依赖 `websockets`，CSV 解析使用 Python 标准库，不需要 NumPy 或 Pandas。

## 快速开始

先在仓库根目录启动 Go 服务（本机测试）：

```sh
go run ./cmd/vta-service --listen :8080 --device-id test-board
```

另开终端，进入 `example` 目录：

```sh
cd example
uv run --locked python client.py
```

默认连接 `ws://127.0.0.1:8080/vta`，读取仓库的 `docs/cleandata_ms.csv`，
使用 `mean` 算法和服务端默认参数，将结果写到 `example/output/cleandata_ms.mean.csv`。
默认输入、输出路径根据脚本位置确定，不受当前工作目录影响；显式传入的相对路径按当前工作目录解释。

连接开发板，指定一个算法及其参数：

```sh
uv run --locked python client.py \
  --url ws://192.168.1.100:8080/vta \
  --input ../docs/cleandata_ms.csv \
  --method mean \
  --params '{"window_size":5}' \
  --output output/mean.csv \
  --save-json
```

上例同时生成 `output/mean.csv` 和 `output/mean.json`，JSON 包含完整矩阵、逐通道状态、实际参数及处理耗时。
参数示例使用 POSIX shell 引号；其他终端请按对应 shell 的规则传入 JSON 字符串。

## 常用测试

```sh
# 原值往返：检查通信和 CSV 数据结构
uv run --locked python client.py --method none --save-json

# 同一个连接连续请求三次，每次收到结果并保存后才发送下一张
uv run --locked python client.py --method median --params '{"window_size":5}' --repeat 3

# Savitzky–Golay
uv run --locked python client.py --method savitzky_golay --params '{"window_size":11,"polyorder":3}'

# 小波
uv run --locked python client.py --method wavelet --params '{"wavelet":"db4","level":3,"threshold_type":"soft"}'

# 参考通道校正
uv run --locked python client.py --method mz5_11 \
  --params '{"no_gas_mz":["mz5","mz6","mz7","mz8","mz9","mz10","mz11"],"T_frac":0.1,"k":5,"max_lag":30}'

uv run --locked python client.py --help
```

`--repeat N` 每次使用新的 `request_id`，发送同一份原始 CSV；输出加编号，
例如 `cleandata_ms.median.001.csv`～`003.csv`。算法是否可用由板端 `hello.functions.denoise` 判断，
参数是否有效由服务端校验。完整参数见[网络通信协议](../docs/network-protocol.md)。

## 连接与结果处理

- 建联后等待板端主动下发 `hello`，终端显示设备、CPU、内存、操作系统和功能清单。
- 等待结果期间独立打印 `heartbeat` 的设备指标、采集错误和状态；`null` 保留为未知值。
  该脚本是按次运行的测试工具，完成最后一次请求后关闭连接；不作为长期在线监控面板。
- 按 `request_id` 匹配响应，并检查返回矩阵、时间轴和通道顺序。输入支持 UTF-8 BOM，
  首列必须为 `Time/min`。输出保留数值和结构，浮点数的文本格式可能变化。
- CSV 预检查使用服务端默认预算：最多 100000 行、1024 通道、500000 个强度值，
  JSON 请求最多 16 MiB，响应最多 32 MiB；服务端预算调整时需同步修改客户端常量/检查。
- 使用 [websockets 的异步连接 API](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html)，
  自动处理控制帧 Ping/Pong。建联超时 5 秒，hello 超时 15 秒，每次请求总超时默认 150 秒（`--timeout`），
  心跳不会延长请求期限。关闭压缩和系统代理，直接连接板端。
- 输出通过临时文件替换，避免写出半个 CSV；允许替换同名旧输出，禁止覆盖输入 CSV。
  `partial` 结果仍保存，同时打印逐通道诊断；`error`、超时、断线或无效响应不保存本次结果。
  多次请求时已保存的结果保留，某次失败后停止后续请求。
- 超时或断线后不自动重连、重发；已发送请求的结果可能未知。确认板端恢复后手动重新运行，使用新请求 ID。
  Ctrl+C 取消等待并关闭连接，不代表板端算法已停止。

退出码：`0` 全部成功；`2` 至少一次返回 `partial`；`1` 输入、连接或请求失败；`130` 用户中断。
命令行参数格式错误由 argparse 返回 `2`。诊断消息写到 stderr，保存路径写到 stdout。

## 客户端测试

```sh
uv run --locked python -m unittest -v
```

测试包含 CSV BOM/负值往返、非法输入、心跳穿插、响应匹配、串行请求、partial、服务端错误和断线/超时。
WebSocket 测试使用本机临时端口。
