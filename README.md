# VTA 质谱降噪服务

Go WebSocket 服务。PC 向板端 `ws://<host>:8080/vta` 建联，每次发送完整时间轴、通道列表和强度矩阵，指定一种算法及其参数；收到完整 result/error 后再发送下一组。所有业务消息为 JSON 文本。

## 编译与运行

需要 Go 1.26。生产程序为纯 Go，不依赖 Python、动态数学库或 CGO。

```sh
make build
# Linux x64 / x86-64
./bin/vta-service-linux-amd64 --listen :8080 --device-id board-001
# Linux AArch64 / ARM64
./bin/vta-service-linux-arm64 --listen :8080 --device-id board-001 --device-model '实际板卡型号'
```

`amd64` 对应 x64，`arm64` 对应 AArch64。两个二进制都以 Linux 为目标；在 macOS 或其他开发主机本地测试使用 `make build-native` 或 `go run ./cmd/vta-service`。非 Linux 开发态无法采集的系统指标会显示为 null。

## 通信

建联后板端首先发送 `hello`：设备概要与 `functions.denoise` 算子列表。随后立即发送首次 heartbeat，再每 5 秒上报设备及 CPU/内存动态指标。默认 Ping/Pong 为 20 秒/60 秒，最多 4 条连接、全局 1 项计算、每连接 1 个请求。

```json
{
  "version": 1,
  "type": "denoise",
  "request_id": "ms-0001",
  "data": {
    "time_unit": "min",
    "time": [0, 0.1, 0.2],
    "channels": ["mz28", "mz44"],
    "intensity": [[1e-12, 4e-12], [2e-12, 5e-12], [3e-12, 6e-12]]
  },
  "config": {"method": "mean", "params": {"window_size": 3}}
}
```

响应 `result` 保留 `data` 的结构、时间轴和通道顺序，附有 `stats`、`column_results`、`timing`。`status=partial` 表示存在降级、参数层数调整、标定跳过或列错误；失败列保留原数据。`error` 表示整次请求未完成。PC 按 `type` 独立消费 heartbeat，按 `request_id` 匹配终态。设备状态不是请求完成确认。

支持 `none`、`mean`、`std_threshold`、`ewma`、`median`、`quantile`、`savitzky_golay`、`wavelet`（db4）、`kalman`、`mz5_11`。算法由 `docs/deNoise_ms.py` 移植，数值使用 float64。`mz5_11` 必须显式指定参考通道；参考通道保持原值，其余通道使用同一配置。未实现的功能类别不会出现在 hello 中。

完整字段、默认参数、错误码和生命周期见 [网络通信协议](docs/network-protocol.md)。运行预算、PC 重连与板端验收见 [部署说明](docs/deployment.md)。

## 验证

Python 联调客户端位于 [example/](example/README.md)，由 uv 管理：

```sh
cd example
uv run --locked python client.py --url ws://127.0.0.1:8080/vta --method mean --params '{"window_size":5}' --save-json
```

默认读取完整示例 CSV，显示设备 hello 和心跳，保存同结构 CSV；支持 `--repeat` 串行测试。
以下 Go 验证命令在仓库根目录执行。

```sh
make test        # 包含真实 WebSocket、本机临时端口和竞态检测
make vet
make benchmark   # 全部算子在 1911 × 63 示例矩阵上的主机基准
```

测试覆盖严格 JSON、数值对照、完整 CSV 原值往返、连接生命周期、超时后实际占槽和设备采集。`internal/testclient` 是用于集成测试的无界面 PC 客户端；它实现 hello 等待、结果分发、状态新鲜度、退避重连及结果未知保护，生产 PC 软件仍需接入这些约定。

参考数据生成仅在开发时需要 Python：

```sh
python3 -m venv .venv
.venv/bin/pip install -r tools/reference-requirements.txt
make reference
```

参考集记录脚本 SHA-256、Python 与依赖版本、随机种子对应的输入和 73 组期望结果。所有算法对照误差上限为 `1e-24 + 1e-7 * max(abs(expected), input_channel_scale)`；none、置零掩码和状态码要求一致。保留脚本的数值语义：例如全零信号的 soft 小波可能产生非有限值，此时返回原列并报告 `NON_FINITE_RESULT`。
