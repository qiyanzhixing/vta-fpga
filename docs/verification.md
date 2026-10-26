# 实现验证记录

日期：2026-10-26。主机：macOS arm64 / Apple M4 Max；Go 1.26.6。

- `go test -race ./...`：全部包通过。
- `go vet ./...`：通过。
- Python 72 组离线参考与 Go 逐点对照：通过，未放宽误差。
- 1911×63 CSV 经真实 WebSocket none 请求原值往返：通过。
- 72 组参考输入经真实 WebSocket 返回并核对结果/状态：通过。
- `make build build-native`：Linux amd64、Linux arm64 和 macOS arm64 通过。两个 Linux 产物经 file 确认为静态 ELF。

## 主机数值执行基准

以下仅测算法执行器，包含结果矩阵复制，不含 JSON/网络，不是板端性能承诺；mz5_11 按样例通道及默认标定规则，可能包含标定跳过列。B/op 是累计分配量，不是峰值 RSS。

```text
goos: darwin
goarch: arm64
pkg: vta-fpga/internal/integration
cpu: Apple M4 Max
BenchmarkSample/ewma-16         	    1044	   1097077 ns/op	 4205935 B/op	    4270 allocs/op
BenchmarkSample/kalman-16       	     752	   1609169 ns/op	 4205927 B/op	    4270 allocs/op
BenchmarkSample/mean-16         	    1036	   1123401 ns/op	 4205935 B/op	    4270 allocs/op
BenchmarkSample/median-16       	     405	   2924950 ns/op	 4208962 B/op	    4333 allocs/op
BenchmarkSample/mz5_11-16       	     642	   1780212 ns/op	 5328538 B/op	    4317 allocs/op
BenchmarkSample/none-16         	    1791	    690210 ns/op	 4190802 B/op	    4270 allocs/op
BenchmarkSample/quantile-16     	     194	   6086246 ns/op	 4211969 B/op	    4333 allocs/op
BenchmarkSample/savitzky_golay-16         	     510	   2374922 ns/op	 4576762 B/op	    9021 allocs/op
BenchmarkSample/std_threshold-16          	     780	   1641617 ns/op	 6270324 B/op	    4396 allocs/op
BenchmarkSample/wavelet-16                	     201	   5972227 ns/op	 7307555 B/op	    4900 allocs/op
PASS
ok  	vta-fpga/internal/integration	14.982s
```

## 实现时的决定

- 原生 worktree 创建成功但沙箱禁止写入，已归档空 worktree，按技能回退到当前目录的 codex/websocket-denoise 分支。代价是当前目录包含实现改动。
- Go 构建和模块缓存放在临时目录；依赖一次下载并锁定计划版本。仅影响开发环境。
- 基础和高级算法共享注册/参数文件，任务 3/4 合并为一次提交；任务 6/7 也合并提交。代价是提交粒度较粗。
- WebSocket 测试需监听本机临时端口，经沙箱权限提升运行。
- 既有参考脚本和 CSV 原样纳入版本控制，以便重现验证。

## 尚需实际设备验证

没有部署到板端；需验证 Linux 真实指标、实际峰值 RSS、每算法延迟及网络异常恢复。暂未在 Linux x64/AArch64 主机上执行交叉构建产物。
