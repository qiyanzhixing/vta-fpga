# 质谱降噪服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 实现运行在开发板上的 Go WebSocket 服务，按已确认的 JSON 协议完成单算法整组质谱降噪、设备状态上报及连接资源管理。

**Architecture:** 独立协议模型和校验层，纯数值降噪模块，拥有全局执行槽的执行器，以及共享指标采集器。每个连接保持独立读循环、任务取消状态和有界发送调度，设备心跳与请求终态共享一个数据写入入口。PC 连接/重连策略通过联调测试客户端验证，生产 PC 软件由上位机工程接入。

**Tech Stack:** 当前已安装 Go 1.26.6；新模块采用 Go 1.26、标准库、github.com/gorilla/websocket v1.5.3、gonum.org/v1/gonum v0.17.0。生产程序以纯 Go、CGO_ENABLED=0 为目标；Python 仅用于生成离线算法对照数据。

**Spec:** `docs/superpowers/specs/2026-10-26-websocket-denoise-protocol-design.md`。执行者必须同时阅读协议及其引用的 `docs/deNoise_ms.py`；本计划不替代协议中的完整字段和算法定义。

## Global Constraints

- 协议版本为整数 1；地址 `ws://<board-ip>:<port>/vta`，默认端口 8080，内网 ws，不实现 TLS。
- 每次一组完整数据；每次仅一种算法和一组参数；返回相同时间轴、通道名称、顺序、矩阵尺寸。
- 质谱使用 float64，保留负数，禁止 null、非有限值和数值字符串；设备信息不可用时按协议使用 null。
- hello 仅包含 version、type、device、cpu、memory、functions；参数只在处理请求中出现。当前仅实现 denoise，去基线留给后续功能。
- 五种消息为 hello、heartbeat、denoise、result、error；心跳不能结束请求或重置请求等待预算。
- 最大请求 16777216 字节，最大响应 33554432 字节，最大时间点 100000，最大通道 1024，最大单元 500000，最大窗口 4095。
- 每连接最多 1 个未完成请求；全局最多 1 个实际执行任务；最多 4 条连接；不排队执行。
- processing_timeout_ms=120000，upload_timeout_ms=30000，write_timeout_ms=10000；本地配置，不通过 hello 下发。
- Ping 每 20000 毫秒，Pong 超时 60000 毫秒；设备心跳每 5000 毫秒，过期阈值 15000 毫秒。
- hello 等待建议 15000 毫秒，connect 5000 毫秒，close 3000 毫秒；PC 重连采用协议第 11 节的退避规则。
- 取消或超时后，实际计算尚未退出不能释放执行槽；断线结果不缓存、不跨连接重放或恢复。
- 不实现可视化、报告、板端 CSV 文件管理、SNR 自动选算法、同位素/生物标志物工作流或生产 PC 界面。
- 每项任务按测试先行推进；提交只包含本任务文件，保留现有参考 CSV、脚本、LICENSE 及用户修改，不提交 .DS_Store。

## Review Focus

1. JSON 对象重复键、null 数值和极大指数：拒绝错误输入，不能被标准库的宽松默认行为悄悄接受。测试归任务 1。
2. 1e-12 量级信号、负值、偶数长度和边界延拓：用参考数据对照，不能以全零结果“通过”。测试归任务 2～5。
3. 超时后计算尚未退出、断线与完成同时发生：全局槽仍占用，只有一个终态，旧结果不进入新连接。测试归任务 6、8。
4. 大结果及慢消费者与周期状态上报并存：发送队列有界，控制帧持续处理，其他连接仍有监控。测试归任务 8。
5. 系统接口缺失、CPU 计数回退及板端墙钟跳变：使用 null/错误码，基于单调时钟估计新鲜度，不能伪造 0。测试归任务 7、9。

---

## 文件与依赖关系

模块名采用 `vta-fpga`，作为本仓库内部程序使用。新增文件按职责分组：

| 路径 | 职责 |
| --- | --- |
| go.mod、go.sum | 固定 Go 及直接依赖版本 |
| internal/protocol/models.go、decode.go、validate.go、defaults.go | JSON 模型、严格解码、数据校验、统一默认预算 |
| tools/generate_reference_fixtures.py、tools/test_reference_fixtures.py | 参考脚本离线输出及其自检 |
| tools/reference-requirements.txt、internal/denoise/testdata/reference.json | Python 参考环境版本记录及提交的数值对照集 |
| internal/denoise/registry.go、params.go、filters.go、recursive.go | 算子登记、参数解析、窗口滤波和递归滤波 |
| internal/denoise/savgol.go、wavelet.go、baseline.go | SG、db4、mz5_11 算法 |
| internal/engine/runner.go、gate.go | 逐列状态、结果校验、全局执行槽和取消状态 |
| internal/telemetry/sampler.go、linux.go、other.go | 共享指标快照和平台采集 |
| internal/server/handler.go、connection.go、writer.go | Upgrade、连接读写、心跳和关闭 |
| internal/testclient/client.go、policy.go | 联调测试使用的 PC 客户端及连接策略，无生产 UI |
| cmd/vta-service/main.go、internal/app/config.go、run.go | 启动参数、服务组装、信号退出 |
| internal/integration/service_test.go、benchmark_test.go | 完整 CSV 联调、生命周期及资源基准 |
| README.md、docs/deployment.md | 运行、PC 接入及开发板验收说明 |

每个实现文件的对应测试文件位于同一目录，后缀 `_test.go`。依赖顺序：1 → 2 → 3 → 4 → 5 → 6；7 依赖 1；8 依赖 1、6、7；9 依赖全部前置任务。已按当前会话逐项实现，并验证 Linux amd64/x64 和 arm64/AArch64 构建。

### Task 1: 协议模型、严格解析与预算

**Files:** 创建 go.mod、internal/protocol/{models,decode,validate,defaults}.go 及 decode_test.go、validate_test.go、models_test.go。

**Interfaces:**
- Produces: `protocol.DefaultLimits() protocol.Limits`。
- Produces: `protocol.DecodeRequest(ctx context.Context, raw []byte, limits Limits) (Request, *Error)`。
- Produces: Request{Version, Type, RequestID, Data, Config}；Data{TimeUnit string, Time []float64, Channels []string, Intensity [][]float64}；Config{Method string, Params json.RawMessage}。
- Produces: Hello、Heartbeat、DeviceSummary、DeviceInfo、OSInfo、CPUSummary、MemorySummary、CPUInfo、MemoryInfo、ServiceStatus、Result、Stats、ColumnResult、Calibration、Error；JSON 名称逐项与协议示例一致。DeviceSummary{Device DeviceInfo, CPU CPUSummary, Memory MemorySummary}；CPUSummary 仅含 model/logical_cores，MemorySummary 仅含 total_bytes。心跳 CPUInfo/MemoryInfo 在相同路径上增加动态字段；不得因复用结构让 hello 多出动态字段。可空字段用指针且不使用 omitempty。
- Error 同时承载响应 request_id（可空）和嵌套 code/message/field；禁止将 Go error 字符串直接当作机器码。

- [x] **Step 1: 编写失败测试。** 测试名称和关键断言如下，其余字段直接使用协议示例：

```go
TestHelloSummary: keys(hello) == {version,type,device,cpu,memory,functions}
TestDecodeDuplicateKey: {"version":1,"version":1} => INVALID_JSON
TestDecodeNullIntensity: intensity=[[null]] => INVALID_DATA
TestDecodeExponentOverflow: intensity=[[1e999]] => INVALID_DATA
TestDecodeShape: time=[0,0.1], channels=["mz28"], intensity=[[1]] => INVALID_SHAPE
TestDecodeLimits: valid_cells=500001 => LIMIT_EXCEEDED
TestDecodeConfig: config={method:"mean",params:{window_size:3}} => accepted
TestDecodeLegacyConfig: config={default:{method:"mean"}} => INVALID_REQUEST
```

- [x] **Step 2:** 初始化 `go mod init vta-fpga`，运行 `go test ./internal/protocol -v`，确认新行为测试失败；不能把依赖缺失当作通过测试先行。
- [x] **Step 3:** 实现上述接口。对所有层级扫描重复键，拒绝尾随 JSON 值和未知请求字段；形状、非有限值、严格递增时间和字节/UTF-8/名称限制按协议校验，整数参数的词法检查由任务 3 负责。配置缺少 params 时保存为 `{}`，显式 null 拒绝。
- [x] **Step 4:** 运行 `go test ./internal/protocol -v`，所有有效/无效输入及模型序列化测试通过。
- [x] **Step 5:** 仅暂存本任务文件，提交 `feat: define strict websocket protocol models`。

### Task 2: 建立可追溯的 Python 数值对照集

**Files:** 创建 tools/generate_reference_fixtures.py、tools/test_reference_fixtures.py、tools/reference-requirements.txt、internal/denoise/testdata/reference.json。

**Interfaces:**
- Produces: `python3 tools/generate_reference_fixtures.py --source docs/deNoise_ms.py --output internal/denoise/testdata/reference.json`。
- JSON 根字段为 reference_sha256、python_version、dependencies、cases；case 包含 name、data、config、expected_intensity、expected_column_codes、rtol、atol。
- 不导入或启动脚本 main；以非交互方式调用脚本算法/SelectiveDenoiser，设置无界面 Matplotlib 后端。生产服务不依赖 Python。

- [x] **Step 1:** 编写 `tools/test_reference_fixtures.py`：断言参考 SHA-256=`0c9934c3ebe9f2f628b834320973705a02994ea08875472c9379a41c195fb139`；10 个方法均有 case；包含 N=1、N=7、N=8、奇偶长度、1e-12 信号、负值、常量、mz5_11 成功和标定跳过；同种输入重复生成得到相同数值内容。
- [x] **Step 2:** 运行 `python3 -m unittest discover -s tools -p 'test_reference_fixtures.py' -v`，确认缺少生成器/数据时失败。
- [x] **Step 3:** 创建仅开发使用的隔离 Python 环境，安装参考脚本所需 NumPy、Pandas、SciPy、PyWavelets、Matplotlib，记录实际精确版本到 reference-requirements.txt 和对照集元数据；随机样本使用 seed=20261026。通过脚本真实实现生成期望值，不能从待实现的 Go 代码生成期望结果。
- [x] **Step 4:** 生成对照集并重跑上述 unittest；默认浮点判据为 `abs(got-want) <= atol + rtol*max(abs(want), input_scale)`，input_scale 为该通道最大输入绝对值，rtol=1e-7、atol=1e-24；none、置零掩码、状态码及尺寸要求精确一致。遇到超差先分析原因，不通过放宽误差隐藏错误。
- [x] **Step 5:** 提交 `test: add reproducible denoising reference fixtures`；不提交虚拟环境或绘图输出。

### Task 3: 单算法参数解析及基础滤波

**Files:** 创建 internal/denoise/{registry,params,filters,recursive}.go 及 params_test.go、filters_test.go、recursive_test.go、reference_test.go。

**Interfaces:**
- Produces: `denoise.NewRegistry() *Registry`，`(*Registry).Methods() []string` 返回实际可执行的降噪算子。
- Produces: `(*Registry).Parse(req protocol.Request, maxWindow int) (Config, *protocol.Error)`；Config{Method string, Params map[string]any}，默认值补齐，整数规范化为 int。
- Produces: `(*Registry).Apply(ctx context.Context, data protocol.Data, cfg Config) (protocol.Data, []protocol.ColumnResult, error)`；输出不得修改输入矩阵。context 取消作为整体 error 返回，单列计算错误进入 ColumnResult 并保留原列。
- Consumes: Task 1 的模型/限制，Task 2 的对照集；后续任务通过注册方法扩展 Registry，不改变已定义签名。

- [x] **Step 1:** 创建失败测试，断言 `mean([1,2,3],window=3)=[4/3,2,8/3]`、`ewma([1,3],alpha=.5)=[1,2]`、none 原值相等且输入不被修改；median/quantile/std_threshold/kalman 对照 fixture。参数 `window_size:3.0`、未知字段、alpha=0、R=0 拒绝；默认值与协议表相等。
- [x] **Step 2:** 运行 `go test ./internal/denoise -run 'TestParams|TestBasic|TestRecursive' -v`，确认尚未实现行为失败。
- [x] **Step 3:** 实现 none、mean、std_threshold、ewma、median、quantile、kalman；reflect 边界与分位数秩按 SciPy 参考执行结果匹配，std_threshold 使用脚本截断窗口和方差非负保护；长循环检查 context，不重新定义阈值。
- [x] **Step 4:** 重跑上述测试及这七种算子的 reference 子测试；严格比较零掩码和首尾点，不能只比较平均误差。
- [x] **Step 5:** 提交 `feat: implement basic and recursive denoising operators`。

### Task 4: Savitzky-Golay 与 db4 小波

**Files:** 创建 internal/denoise/savgol.go、wavelet.go、savgol_test.go、wavelet_test.go；修改 registry.go、params.go、go.mod、go.sum。

**Interfaces:** Consumes/extends Task 3 的 Registry.Parse/Apply；新增算子名称严格为 savitzky_golay、wavelet。请求模型不变。

- [x] **Step 1:** 创建失败测试：SG 对三次多项式内部及 interp 两端均保持曲线；N<window 时返回 mean(min(3,N)) 且代码为 FALLBACK_SHORT_SERIES；wavelet N=7 降级，N=8、N=65、soft/hard 与 fixture 对照，level 调整报告 WAVELET_LEVEL_ADJUSTED；wavelet 名称 db5 拒绝。
- [x] **Step 2:** 运行 `go test ./internal/denoise -run 'TestSavgol|TestWavelet' -v`，确认失败。
- [x] **Step 3:** 固定 Gonum v0.17.0，使用其 mat 纯 Go 最小二乘实现 SG 权重/边界拟合；实现 db4 分解与重构、symmetric 延拓、MAD 阈值和长度修正。db4 系数从参考 PyWavelets 取得并保留精度，记录来源，禁止用名称近似的小波替代。临时矩阵分配前检查整数溢出及工作区预算（包内建议初值32MiB），不足时返回该列 ALGORITHM_ERROR，说明资源不足并保留原列，不能偷偷降低阶数；对应测试断言极大阶数不会无界分配。
- [x] **Step 4:** 重跑该任务测试和对应 reference 子集，包含小量级/负数、偶数长度和长度阈值；病态拟合/非有限结果进入单列失败，不静默改参数或输出零。
- [x] **Step 5:** 提交 `feat: add savitzky golay and db4 wavelet denoising`。

### Task 5: mz5_11 参考底谱算法

**Files:** 创建 internal/denoise/baseline.go、baseline_test.go；修改 registry.go、params.go。

**Interfaces:** 扩展同一 Registry.Parse/Apply。参数中的 no_gas_mz 仅接受名称；所有非参考通道使用同一配置，参考通道输出代码 REFERENCE_CHANNEL。Calibration 使用协议中的 T_idx、lag、a、c。

- [x] **Step 1:** 创建失败测试：缺失参考、全部为参考、T_idx 越界及 T_idx/T_frac 同时提供均 INVALID_CONFIG；参考原值不变；固定输入的 lag/a/c/目标结果与 fixture 对齐；低波动/幅度越界保留原列并返回对应 CALIBRATION_*。
- [x] **Step 2:** 运行 `go test ./internal/denoise -run TestBaseline -v`，确认失败。
- [x] **Step 3:** 按参考脚本实现参考均值、互相关遍历及并列时选择顺序、标定区间、reflect 平滑、a 与 c 的解析求解、信号移位的边界填充；严格保留脚本实际滞后符号，不能按注释重新推导后改变。中间数组仅使用原始输入，长循环检查取消。
- [x] **Step 4:** 重跑 TestBaseline 和全部 reference；检查 k 默认 5、max_lag 默认 30，与示例配置 k=31/max_lag=10 不混淆；Registry.Methods 此时应恰好包含协议中的十个名称。
- [x] **Step 5:** 提交 `feat: implement reference channel baseline noise denoising`。

### Task 6: 执行器、逐通道结果与真实槽位生命周期

**Files:** 创建 internal/engine/runner.go、gate.go、runner_test.go、gate_test.go。

**Interfaces:**
- Consumes: protocol.Request、denoise.Config 及 Registry.Apply。
- Produces: `engine.New(reg *denoise.Registry) *Runner`；`(*Runner).Run(ctx context.Context, req protocol.Request, cfg denoise.Config) (protocol.Result, *protocol.Error)`。
- Produces: `engine.NewGate(maxActive int) *Gate`；`(*Gate).TryAcquire() (*Lease, bool)`；`(*Lease).MarkRecovering()`；`(*Lease).Release()`（幂等）；`(*Gate).Snapshot() protocol.ServiceStatus`。
- Run 不拥有全局槽，调用方取得 Lease 后以实际任务 goroutine 的退出释放它；超时监督器无权提前释放。

- [x] **Step 1:** 创建失败测试：一列返回 NaN/错误长度时保留整列并计 errors；参考/none 跳过仍 ok，标定跳过/降级为 partial；total=processed+skipped+errors。用 channel gate 模拟忽略取消的计算：MarkRecovering 后 TryAcquire=false，直到实际 worker 退出才 true。
- [x] **Step 2:** 运行 `go test ./internal/engine -v`，确认失败。
- [x] **Step 3:** 实现结果组装、输入保护、逐列完整性/有限值校验、状态和算法版本（初始 1.0.0），processing_ms 使用单调计时；gate 用互斥保护真实占用状态，Release 与完成/取消竞态只执行一次。
- [x] **Step 4:** 运行 `go test -race ./internal/engine -v`，确认上述行为和并发清理通过。
- [x] **Step 5:** 提交 `feat: add denoising execution and bounded admission`。

### Task 7: 共享设备概要与周期指标采集

**Files:** 创建 internal/telemetry/{sampler,linux,other}.go、sampler_test.go、linux_test.go、testdata/ 下的系统文件样例。linux.go 使用 linux 构建约束，other.go 使用 !linux；非 Linux 允许开发联调，无法采集的指标按协议为 null。

**Interfaces:**
- Produces: `telemetry.New(cfg Config, readFile func(string) ([]byte,error)) *Sampler`；Config{DeviceID string, DeviceModel string, Interval time.Duration, Clock Clock}，Clock 接口为 `Now() time.Time` 及 `Monotonic() time.Duration`。生产默认实现用 time.Now 与相对固定起点的 time.Since，测试时可独立推进墙钟及单调时间。
- Produces: `(*Sampler).Run(ctx context.Context)`；`(*Sampler).Summary() protocol.DeviceSummary`；`(*Sampler).Snapshot(service protocol.ServiceStatus) protocol.Heartbeat`，发送时读取 Clock.Monotonic 计算 sample_age_ms。
- Snapshot 不设置 connection_id、seq，由连接发送器填入；返回独立可安全序列化的快照。Summary 供 hello 使用，同路径同单位。

- [x] **Step 1:** 创建失败测试：total=1073741824、available=805306368 推出 used=268435456、usage=25；CPU 首次无基线为 null/WARMING_UP；有效两次样本得到可预知使用率；计数回退/在线集合改变为 null/SAMPLE_RESET；缺 MemAvailable、权限拒绝分别报告；墙钟前后跳变不改变 sample_age_ms 的单调时间结果。
- [x] **Step 2:** 运行 `go test ./internal/telemetry -v`，确认失败。
- [x] **Step 3:** 实现可注入文件读取和纯解析函数（解析测试在 macOS 也能运行），Linux 平台读取按协议来源；id/model 部署配置优先。一个采样器服务所有连接，不每个请求/连接启动额外采集；缓存硬件信息、动态采样有界，未知属性不阻止 hello。
- [x] **Step 4:** 运行 `go test -race ./internal/telemetry -v`；并发读取快照不竞态，context 取消停止采样，动态/派生字段缺失均能得到对应错误路径。
- [x] **Step 5:** 提交 `feat: report shared device metadata and runtime metrics`。

### Task 8: WebSocket 连接、串行发送、保活与取消

**Files:** 创建 internal/server/{handler,connection,writer}.go、handler_test.go、connection_test.go、writer_test.go；修改 go.mod、go.sum，固定 Gorilla v1.5.3。

**Interfaces:**
- Consumes: Task 1 DecodeRequest、Task 3 Registry.Parse、Task 6 Runner/Gate、Task 7 Sampler。
- Produces: `server.New(cfg Config, reg *denoise.Registry, run Runner, gate *engine.Gate, metrics *telemetry.Sampler) *Server`；Runner 是拥有 Task 6 同名 Run 签名的接口，供测试注入阻塞实现。
- Produces: `(*Server).Handler() http.Handler`；`(*Server).Close(ctx context.Context) error`。
- Config{Limits protocol.Limits, HeartbeatInterval time.Duration, PingInterval time.Duration, PongTimeout time.Duration, CloseTimeout time.Duration}；每连接 UUID 用 crypto/rand 生成，初次 heartbeat 提供，hello 不携带。

- [x] **Step 1:** 使用 httptest.Server 与真实 WebSocket 客户端创建失败集成测试：首条 hello keys 与协议相等、functions.denoise 与 Registry.Methods 一致；下一条 heartbeat seq=1、UUID 有效；空闲和假算法阻塞期间均持续心跳和 Pong；两个请求串行成功，第二连接竞争返回 BUSY，同连接并发 Close1008。
- [x] **Step 2:** 运行 `go test ./internal/server -v`，确认未实现行为失败。
- [x] **Step 3:** 实现单读循环、异步请求阶段、单数据 writer。收到完整请求即创建整体截止时间，依次解码/校验配置/尝试获取 Lease/执行/校验/编码；读循环始终可处理控制帧和并发违例。终态与 timeout 使用一次性仲裁；真实 worker defer Release，超时/断线只取消及 MarkRecovering。尚未发出的 heartbeat 仅保留最新一份，writer 获取时取最新快照并更新年龄，终态不可被丢弃。
- [x] **Step 4:** 扩充并运行 `go test -race ./internal/server -v`：上传中止、超字节1009、二进制1003、非法UTF-8 1007、失去Pong 4001、响应预算超限、取消与完成竞争、慢客户端/其他连接并存；Close/SIGTERM 时1001及取消，3秒关闭预算。控制帧遵循 Gorilla 允许的并发调用范围；read deadline 取 Pong 与上传截止的较早者，不被 Pong 意外延长上传预算。
- [x] **Step 5:** 提交 `feat: serve websocket requests heartbeat and connection lifecycle`。

### Task 9: 可运行入口、PC 联调策略及板端验收

**Files:** 创建 cmd/vta-service/main.go、internal/app/{config,run}.go、app_test.go、internal/testclient/{client,policy}.go 及 policy_test.go、internal/integration/{service,benchmark}_test.go、README.md、docs/deployment.md；按需把 bin/、.venv/ 加入 .gitignore。

**Interfaces:**
- Produces: `app.Parse(args []string) (Config,error)`；`app.Run(ctx context.Context,cfg Config) error`；main 只设置 signal context 和退出码。
- Config 包含 listen（默认 :8080）、device-id、device-model 及协议第5.2节同名限额/时间预算；使用标准 flag，PC 配套设置写入部署文档。
- Test client: `testclient.New(cfg Config) *Client`；`(*Client).Connect(ctx context.Context,url string) error`；`(*Client).Denoise(ctx context.Context,req protocol.Request) (protocol.Result,*protocol.Error)`；`(*Client).Close(ctx context.Context) error`。它是测试工具，不是生产 PC 产品承诺。

- [x] **Step 1:** 创建失败测试：默认端口/路径和每项预算与协议一致；客户端验证 hello 后才能提交；heartbeat 与 result 分发独立，心跳不重置请求超时；主动 close 不重连，error/close 合并为一次清理，重连 k=0..5 的随机上界为1000/2000/4000/8000/16000/30000毫秒，旧代次消息无效。
- [x] **Step 2:** 运行 `go test ./internal/app ./internal/testclient ./internal/integration -v`，确认失败。
- [x] **Step 3:** 组装程序和测试客户端，提供可注入时钟/随机源测试重连策略。输入 docs/cleandata_ms.csv 转成请求，对 none 验证1911×63及所有原值，对其他算子对照已提交 fixture；实现信号关闭及非 Linux 开发态 fallback，README 写清启动、设备配置、请求示例、算法范围、开发板参数收集及 PC 接入责任。
- [x] **Step 4:** 运行 `go test -race ./...`、`go vet ./...`、`go build ./cmd/vta-service`，均通过后运行 `go test ./internal/integration -run '^$' -bench BenchmarkSample -benchmem`。分别执行 `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o bin/vta-service-linux-arm64 ./cmd/vta-service` 及 amd64 交叉构建；在未明确型号前不猜测 ARM32 GOARM 或部署到设备。
- [x] **Step 5:** 记录主机基准及构建结果，提交 `feat: add service entrypoint integration checks and deployment guide`。目标板的型号/CPU架构、OS、RAM 和目标延迟取得后，才选定实际发布目标并执行下述板端验收。

## 开发板发布前验收

记录真实型号、GOARCH/必要时 GOARM、操作系统/内核、可用 RAM、期望单组处理延迟。上传选定产物并运行完整样例，记录各算法耗时、峰值 RSS、心跳间隔和慢客户端表现；按测量修改本地预算并同步 PC。ARM64 构建成功不代表板端运行或性能达标。

在目标板分别验证 CPU/内存/OS/型号采集、网络拔断、服务重启、主动断开、长任务取消及 BUSY 恢复；结果/状态须符合协议，没有结果时 PC 标记未知。若板端预算要求改变协议语义或参数范围，先记录设计变更，不临时隐藏不兼容行为。

## 自查与执行选择

协议1～3节由任务1/9覆盖，4～6节由1/8覆盖，7节由2～4覆盖，8节由2/5覆盖，9～10节由1/6/8覆盖，11节由8/9覆盖，12节由7～9覆盖，13节由所有任务及板端验收覆盖。五项 Review Focus 均有对应失败测试。所有任务复用上述模型及方法签名，九项实现任务已执行；全套竞态测试、go vet、完整 CSV 联调和双架构构建已通过，最终独立审阅另行记录。

建议 Native 执行：由当前会话逐项实现，最后集中审阅；模型、算法注册、执行器及连接状态共享接口较多。也可选择 Subagent-driven，每个任务由新子代理实现及独立审阅，但需先由用户选择执行方式。

技术选择参考：[Gorilla v1.5.3 发布](https://github.com/gorilla/websocket/releases/tag/v1.5.3)、[连接并发与控制消息接口](https://pkg.go.dev/github.com/gorilla/websocket)、[Gonum v0.17.0 发布](https://github.com/gonum/gonum/releases/tag/v0.17.0)、[mat QR 接口](https://pkg.go.dev/gonum.org/v1/gonum/mat#QR.SolveTo)。

## 执行记录

2026-10-26：用户选择当前会话逐项实现，要求 x64 与 AArch64 编译。代码位于 codex/websocket-denoise 分支，make build 生成两个 Linux 静态 ELF。9 项任务已完成；实际设备部署和板端性能验收仍需型号、OS/RAM 与设备访问。详细验证见 docs/verification.md。
