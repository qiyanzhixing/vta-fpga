# 编译、联调与部署

## 双架构产物

```sh
make build
file bin/vta-service-linux-amd64 bin/vta-service-linux-arm64
```

- `bin/vta-service-linux-amd64`：Linux x64（x86-64）。
- `bin/vta-service-linux-arm64`：Linux AArch64（ARM64）。
- `make build-native`：当前开发主机的本地程序。

交叉构建设置 `CGO_ENABLED=0`，无需交叉 C 编译器。构建成功只证明可生成对应 ELF；仍需在 x64 测试机及实际 AArch64 板上运行验证。当前未取得板卡型号、RAM 和延迟要求，尚未进行设备部署或板端性能验收。

## 本地配置

默认监听 `:8080`，固定路径 `/vta`。服务用于约定的内网 ws 联调，不配置 TLS。设备稳定标识建议通过 `--device-id` 配置，设备型号可用 `--device-model` 覆盖平台采集值。

所有时间选项使用整数毫秒，必须为正数。选项名采用连字符：

| 参数 | 默认值 |
| --- | ---: |
| `--max-request-bytes` | 16777216 |
| `--max-response-bytes` | 33554432 |
| `--max-time-points` | 100000 |
| `--max-channels` | 1024 |
| `--max-cells` | 500000 |
| `--max-window-size` | 4095 |
| `--max-inflight-per-connection` | 1（v1 固定） |
| `--max-active-requests` | 1 |
| `--max-connections` | 4 |
| `--processing-timeout-ms` | 120000 |
| `--upload-timeout-ms` | 30000 |
| `--write-timeout-ms` | 10000 |
| `--ping-interval-ms` | 20000 |
| `--pong-timeout-ms` | 60000 |
| `--report-interval-ms` | 5000 |
| `--report-stale-timeout-ms` | 15000（供 PC 配套使用） |
| `--close-timeout-ms` | 3000 |

资源预算和心跳配置不经 hello 下发，变更时须同步 PC。`max_cells` 和行列限额同时生效；较大的算法窗口仍有 CPU 成本。SG 单列临时拟合工作区上限为 32 MiB，超过时保留原列并报告 `ALGORITHM_ERROR`。调高全局并发会提高内存使用，需以板端测量为依据。

SIGINT/SIGTERM 停止接入、向已有连接发送 1001 并取消任务，在关闭预算内退出。算法取消后的槽位只有在实际计算和编码退出后才释放；心跳 `recovering` 表示仍在清理。

Linux 设备信息来自 `/proc/stat`、`/proc/meminfo`、`/proc/uptime`、`/proc/cpuinfo`、`/etc/os-release`、内核版本及 device-tree/DMI。无权限、字段缺失或非 Linux 时返回 null 和对应 `collection_errors`；不将 MemFree 当作 MemAvailable。CPU 使用率为全部在线 CPU 的窗口平均值，首个窗口为 null。单次系统文件读取最多等待 100ms；底层读取未退出时不叠加新读取任务，后续快照以 null/READ_FAILED 报告。

## PC 接入责任

PC 发起连接，收到有效 hello 后进入 READY。连接数超过预算时握手返回 503 和 Retry-After。heartbeat 与 result/error 共享连接；心跳不可重置处理请求的等待预算。

等待结果预算建议至少覆盖 120 秒处理、10 秒写入和网络余量。结果未知时保留原始数据；连接恢复后由上层显式决定使用新 request_id 重试或放弃，不自动重放，也不直接提交下一组。`internal/testclient.Client.ResolveUnknown()` 表示测试上层已作出该决定。

可恢复断线采用 full jitter 退避：第 k 次上限 `min(30000,1000*2^k)` 毫秒，健康 hello 后存活 30 秒才重置。关闭码分类见协议；主动 close 清除保持连接标记。服务不会主动连接 PC、持久化请求或跨连接恢复结果。

PC 显示状态使用本地收到 heartbeat 后的单调经过时间加 sample_age_ms；15 秒标记过期，null 显示未知。状态过期本身不自动断开健康连接，也不重放数据。

## 板端发布前验收

1. 记录型号、CPU 架构、操作系统/内核、物理及可用 RAM、目标延迟，选定产物。
2. 用完整 `cleandata_ms.csv` 测量各算法耗时和峰值 RSS；检查输出维度、负值、partial 状态及参考通道。
3. 检查真实 CPU/内存指标，验证网络拔断、服务重启、主动关闭、超时取消后的 BUSY 恢复。
4. 同时连接多个 PC，检查长结果、慢消费者下其他连接心跳和控制帧仍能工作。
5. 根据测量调整运行预算并同步 PC；保留版本、配置、输入和验收记录。
