# Nestory Assistant M2-A：Jetson 只读核查与 M2-B 准备

日期：2026-10-13。主要状态采样：19:50–20:13 CST；资源短采样：19:59:08–19:59:12 CST；服务终检：20:41:37 CST。本报告是内部部署资料。

**结论：已有 2B 权重、已运行的视觉服务、兼容的软件环境和足够的当前内存/磁盘余量；尚不具备直接启用正式 Assistant 并发的验收条件。推荐用户批准后使用方案 B，在维护窗口中运行唯一的真实模型测试实例。M2-B 尚未开始。**

本阶段只读取 Jetson 状态、文件元数据、白名单配置、源码和模型健康；没有提交 `/analyze` 或 `/chat`，没有加载第二个模型，没有打开摄像头/音频采集，也没有连接生产 SQLite、备份、迁移、校时、安装依赖、改变功耗或重启服务。生产服务原有推理仍自行运行，不是本阶段测试发起的推理。Mac 上的报告与既有隔离回归不属于 Jetson 推理验收。

证据分级：

- **F：实测事实**——本次进程、端口、元数据、健康读取和短采样。
- **C：代码结论**——冻结基线/生产源码及部署配置的静态检查；不是实机性能证明。
- **E：估算/建议**——供用户批准的资源预算、测试规模和停止阈值。
- **U：待验证**——真实模型功能、性能、离线保时、恢复后的业务闭环等。

## 1. 可追溯代码基线与 Git 状态

| 项目 | SHA / 状态 | 证据 |
|---|---|---|
| M1 最终提交 | `eb1ab5af7447a83fcc808651c26e728de09e98a6` | F：Mac 与 Jetson 内部裸仓库一致 |
| M1 分支 | `codex/assistant-m1-integration` | F：已安全同步，未部署 |
| 后端来源 | `7370e1c4bea179f35213607f0a852a85f3f3da9f` | F/C |
| 前端交付来源 | `88d7d747240cf06a9c9280ee2d0b8febc9cc71bb` | C：M1 已合并此提交 |
| 冻结契约 | `7cf02ac81a7189f49148943369e9b681aa3b42f9` | C：v1 不变 |
| 当前生产 main | `82c5015bee5a5ddc7f92d9041d045b9960018173` | F：Mac main、Jetson 工作副本与共享 main 一致；工作区干净 |
| 内部前端分支最新 | `93b8f9fc614b54a8fc73a9ddeb0f8fb78e0b3b70` | F：交付后合并 main 文档；不替代上述 M1 基线 |
| 本报告分支 | `codex/assistant-m2a-readiness`，从 M1 创建的独立 Mac worktree | 只提交本报告 |

Jetson 生产工作副本的对象库**没有** M1 对象，直接在它上面 `git show eb1ab5a` 返回 bad object；内部共享裸仓库中对象与分支完整存在。差异比较改为只读共享裸仓库对象，没有 fetch/pull 或切换生产目录。这个区别表示“共享分支已同步，生产工作副本未更新”，不能误报成 M1 同步失败。

共享裸仓库比较 main→M1：36 个文件、8702 行增加、24 行删除；主要是 Assistant 后端、前端、模型文字接口、调度器和测试。生产 `model_server.py`、`vlm.py`、`config.py`、`app.py` 与 `33b0785` 的相应文件逐字节相同，当前 main 后续文档提交没有部署 M1 代码。

已阅读 AGENTS、GIT_WORKFLOW、冻结契约、Phase 1/M1 报告、JETSON_MODELS 和 ADR v2.1。ADR 新条目均为 Proposed；不据其实施 Chat First、4B、时间线或 RTC 新功能。JETSON_MODELS 的旧摄像头、旧测试数量及单张灰图时长是历史信息，本次以 live 核查为准。

## 2. 硬件、软件、时钟与当前资源

| 项目 | 本次结果 | 分级 / 限制 |
|---|---|---|
| 板卡 | NVIDIA Jetson AGX Orin Developer Kit；设备树 `p3737-0000+p3701-0005 / tegra234` | F：64GB 型号与可用系统内存相符 |
| 内存 | MemTotal 65,893,371,904 bytes＝61.368 GiB；available 52.154 GiB，used 8.572 GiB | F：时间点快照，不是独立 VRAM |
| Swap | 30.684 GiB，8 个 zram 分区；使用 0 | F：不是额外 GPU 容量保证 |
| 系统 | Ubuntu 22.04.4 LTS，`5.15.148-tegra` | F |
| L4T | `36.4.0-20240912212859` | F |
| JetPack | L4T/CUDA 与 JetPack 6.1 基线相符；`nvidia-jetpack` 元包未安装 | F＋C：不能说已安装完整 SDK 元包 |
| CUDA | toolkit `12.6.11-1`；nvcc release 12.6 / `V12.6.68` | F：仅版本读取 |
| Python | `.venv-models`：3.10.12；实际二进制链接至系统 Python 3.10 | F |
| PyTorch | `2.5.0a0+872d972e41.nv24.8` | F：distribution metadata，未在核查进程导入 Torch/CUDA |
| 其他依赖 | Transformers 4.57.6；Accelerate 1.12.0；Torchvision 0.20.0；NumPy 1.26.4；OpenCV 4.11.0.86；ONNX Runtime 1.18.1；SQLite 3.37.2 | F：metadata/标准库版本 |
| 环境隔离 | `.venv-models/pyvenv.cfg`：include-system-site-packages=true | F：未来不得以通用 requirements 覆盖它 |
| CPU / 功耗 | 物理 12 核、在线 0–7；MODE_30W，模式 ID 2 | F：没有更改模式 |
| CPU 短采样 | 每核约 1%–95%，频率 729–1728 MHz；其余四核 off | F：5 个一秒样本，非持续负载统计 |
| GPU | GR3D 利用率 70%–94%；独立 devfreq 快照当前 306 MHz、范围 306–612 MHz | F：频率与利用率不是同一瞬间配对测量 |
| 温度 | CPU/Tj 47.5–48.4℃，SoC 约 44–45.4℃ | F：GPU 专用温度读取未返回有效值，不用 CPU 值冒充 |
| 内存短采样 | tegrastats RAM 8869/62841 MB；Swap 0；largest free block 4×4 MB | F：工具与 free 的口径不同；不是 CUDA 单进程峰值 |
| 供电轨短采样 | VDD_GPU_SOC 6.816–8.022 W；VDD_CPU_CV 0.802–1.604 W；VIN_SYS_5V0 4.915–6.028 W | F：不相加冒充整机墙上功耗 |
| 根磁盘 | eMMC，53.836 GiB 总容量，26.841 GiB available；使用率 48% | F：项目和 /tmp 位于同一分区 |

L4T 36.4、CUDA 12.6 与 JetPack 6.1 的对应关系依据 [NVIDIA 6.1 release notes](https://docs.nvidia.com/jetson/archives/jetpack-archived/jetpack-61/release-notes/index.html)；版本数值本身来自设备查询。

**时钟 F：** Asia/Shanghai，LocalRTC=no，NTP active，NTPSynchronized=yes；timesyncd 对 1.pool.ntp.org 的已记录 offset −20.522 ms、delay 303.178 ms、jitter 31.744 ms。SSH 对照 Mac UTC 的往返 2.298 秒，差值 +1.111 秒、测量不确定度约 ±1.149 秒，不能把它解释成确定的 1.1 秒漂移；NTP 状态提供更强的当前同步证据。未发校时命令。

主 `rtc0`：nvvrs-pseq-rtc，hctosys=1，日期/UTC 时间与系统一致。`rtc1`：tegra_rtc，hctosys=0，显示 1970 年及本次运行计时；它不是当前系统选用的主 RTC，不能仅据此认定系统时钟失效。**U：** RTC 电池/备用供电和断电后保时没有验证，不做断电实验。

**资源评估 E：** 当前内存和磁盘能够支持准备一套小型隔离数据/日志及只读复用既有环境。额外模型稳态暂按 6–10 GiB 统一内存规划，启动峰值另留余量；这是粗预算，绝非实测承诺。当前 GPU 样本已忙，内存充足不等于可无影响地再加载模型。RSS 仅是进程 CPU 地址空间指标，不能拿模型 RSS 约 2.25 GiB 当作全部 GPU/统一内存占用。

## 3. 当前生产进程、端口与健康

启动时间为 ps 报告的 CST；PID 表是后续对照，不保证永久不变。

| 职责 | PID / PPID | 启动时间 | 证据 |
|---|---|---|---|
| Qwen worker | 22156 / 22155 | 2026-10-13 14:35:32 | F：`.venv-models/bin/python -B model_server.py`，cwd 为生产项目 |
| 主应用、摄像头及视觉观察线程 | 22254 / 22251 | 2026-10-13 14:35:47 | F：`.venv-models/bin/python -B server.py`；45 个线程、持有 /dev/video0 与活跃 DB FD |
| 语音 worker | 22299 / 22254 | 2026-10-13 14:35:48 | F：`-m baby_monitor.voice_worker` |
| 音频采集子进程 | 22352 / 22299 | 2026-10-13 14:35:52 | F：进程名 arecord；没有采集或读取其音频 |
| Tailscale | 795 / 1 | ps：12:05:13 | F：tailscaled.service active/running |
| NTP | 538 / 1 | ps：12:05:11 | F：systemd-timesyncd.service active/running |

主应用/模型位于 user session scope，不是已登记的 Nestory systemd 服务；没有匹配到 baby/nestory/qwen 服务单元。不能假定有 `systemctl restart nestory` 可用。systemd 的 ActiveEnterTimestamp 是独立的启动墙钟记录，与 ps 由当前 boot 时钟推算的值有差别；后续用 PID＋/proc start ticks/单调时钟对照，不能将其冒充新的重启。

| 监听 | 当前状态 |
|---|---|
| 127.0.0.1:8010 | Qwen PID 22156 |
| 127.0.0.1:8080 | 主应用 PID 22254 |
| 已配置 LAN IPv4:8080 | 同一主应用 PID；既有可信 LAN 预览，未更改 |
| Tailnet 地址:443 | Tailscale HTTPS Serve → 127.0.0.1:8080 |
| 18010、18881 | 本次 ss 快照未监听；只能作为候选，M2-B 启动前须复查 |
| 其他 | SSH 22、RPC 111、DNS stub 53、Tailscale 内部监听；均未调整 |

模型 `/health` 是源码确认过的无生成路径：HTTP 200、ok=true、Qwen3-VL-2B-Instruct、load_seconds=3.665、error=null；**没有 scheduler 字段**。这个数值是已有进程启动加载耗时，不是本次推理耗时。

Tailscale BackendState=Running、Self.Online=true，Serve 443 TLS 配置存在。Mac 默认 curl 路径 SSL_ERROR_SYSCALL；保持证书校验、显式直连已知 Tailnet IP 并使用原域名/SNI 时得到 303；系统 DNS 解析也指向已知设备。Mac 存在 proxy 环境变量，代理具体原因尚未确定，未读取/输出代理凭据或修改设置。Jetson loopback 裸请求缺 Tailscale 所有者身份头得到 403，与现有 `_valid_host` 检查相符；没有伪造身份头。

**已发现、未修复：** tailscaled 有 connmark 匹配/规则加载警告。SSH 和上述直连 HTTPS 当前可达，不代表这个内核警告可忽略或长期远程链路已验收。维护恢复必须包含用户正常访问路径检查，不能只凭直连 303 宣称所有客户端正常。

本阶段没有查看私人画面、生活记录行或原始业务日志。摄像头 FD、GPU 活动和健康读取证明组件在运行，不能单独证明每次视觉结果已正确入库；实际观察间隔/时间线增量留待批准后的测试和恢复验收。

## 4. 实际运行版本、配置与权重

**C＋F：** 生产模型磁盘代码仅有 GET `/health` 和 POST `/analyze`；无 `/chat`、无 inference_scheduler 导入。源文件 mtime 为 13:33 CST，早于模型进程启动；其 SHA256 为：

```text
83e6361d1557587d50e4a50afccefebe04bf6e790f342a918f34c1bac2a71348
```

M1 同文件 SHA256 为：

```text
1957be95480acfa328fad171b33b52f58cd901d1c21a21af3c371cee994d3d24
```

进程 cwd/命令、文件 hash/mtime 和健康字段共同支持“运行的是旧视觉版”的结论；旧健康接口不暴露启动 commit，不能把当前 checkout HEAD 严格当作进程加载时的 Git SHA。没有用注入/调试器去读取运行对象，也没有 POST 探测 `/chat`。

- 生产主应用和模型进程中无 BMON_ASSISTANT_* 环境项；生产 .env 白名单检查也无 Assistant 开关。生产代码本身尚不包含这项功能，**M1 Vision First 调度器没有正式运行**。
- 实际配置：常规视觉 30s、睡眠/无人 60s、运动 15s；这些 interval 没有 env 覆盖，来自启动代码默认值。失败/未在线重试为 min(5s,15s)=5s；另有约 1s 轮询及推理时间。不能把配置值直接当实际成功观察间隔。
- VLM HTTP timeout 是 env 明确设置的 **60s**，不是源码默认 35s；轻量分析 interval 默认 3s。读取这些值没有修改采样频率。
- 摄像头配置为 Sonix USB 2.0 的 by-id 路径、1280×720 / 15fps；进程已持有 /dev/video0。这是配置/占用事实，不是实测帧率或画面正确性。
- `observation_subject.json` 的 person_test_mode=true；当前保存的主体配置为 **adult_test**，它和 Assistant demo/real 是独立维度。未来 Real 事实查询不能把这些成人观察当婴幼儿记录。
- 应用有语音 worker，播放 backend=silent；不能从这个状态宣称真实音箱播放已验收。本次未动音频配置。
- 两个正式 Python 进程均使用 HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1、HF_HUB_DISABLE_TELEMETRY=1。

权重 F：`~/baby-monitor-public/models/Qwen3-VL-2B-Instruct/` 中有 tokenizer、processor、chat template、config 和单个 model.safetensors；模型类型 qwen3_vl、架构 Qwen3VLForConditionalGeneration。文件 4,255,140,312 bytes；仅阅读 76,240-byte tensor header 得到 625 个 tensor entries、2,127,532,032 elements、BF16、payload 3.963 GiB。没有读取/加载全部张量，没有重做整文件校验；旧 manifest 的全文件 hash 不冒充本次新验证。源码 loader 为 BF16/eager/local_files_only；运行健康说明既有加载成功，真实文字输出尚未验证。

## 5. 新发现的并发准入前置问题

**C：M1 预算提示未覆盖失败后的快重试。**

- `baby_monitor/vlm.py:340`：未 online 的 `_next_interval()` 可为 5s。
- 同文件 `:361`：发给 worker 的 X-Bmon-Next-Vision-Seconds 只取 min(120,30,60,15)=15s。
- `inference_scheduler.py:35`：chat 只检查当前时间＋8s budget＋1s < 已声明 vision deadline；不预知观察器失败后的状态转换。

只在 Mac 用同一源码和假单调时钟做逻辑检查，未调用模型：t=0 reserve 15s；t=1 时 chat admission=true。如果视觉格式错误等导致观察器回到 5s 重试，8s 聊天可能到 t=9 才结束，已经超过实际 t=5 的下一次视觉目标。Vision First 不能抢占正在执行的聊天。**这是可构造的代码边界反例，不是已经在 Jetson 观察到的故障。**

此外，8s 是软生成保护；prefill/CUDA kernel 不立即可抢占，max_time 截断也可能产生不完整 JSON。头部预算只表示可信客户端声明的下一次视觉时间，不是已完成性能 profiling 的证据。无头、过期预算或没有安全空档时，允许返回 vision_capacity_reserved；不能为了让测试成功设置 unprofiled_chat=true 或伪造长预算。

**准入结论：** 现阶段不足以直接将 M1 Assistant 接入正式摄像头并发。可先在独立 fixture 排程中验证唯一 worker 的真实中文功能；原观察器混合负载验收前，须用户批准一项独立的内部预算安全修正/验证，覆盖 5s 失败重试、状态转换及实际排队/推理耗时，继续保持 Vision First 和 13 个 v1 API。简单延长正式视觉 interval 或换成 Chat First 不属于修复。本阶段不修改该代码，也不宣称已经解决。

## 6. 两种部署方案比较

| 项目 | 方案 A：保留生产 8010，另启模型端口 | 方案 B：维护窗口，唯一真实测试 worker |
|---|---|---|
| 模型实例 | 新进程会再创建 Engine/model；需重复分配权重及 GPU context | 停旧 worker 后才加载测试 worker，物理上一份 Qwen |
| 权重/环境 | 可只读复用文件与环境；不能由共享 page cache 推断 GPU 权重也共享 | 复用旧 .venv/权重，绝不 pip install/覆盖环境 |
| 资源 | 权重新增约 3.963 GiB＋未知激活/KV/workspace；稳态先预算 6–10 GiB，启动峰值未测 | 不与旧模型争 GPU；仍要测测试负载峰值 |
| GPU 干扰 | 两个进程共用同一 GPU、内存带宽和功耗；各自 scheduler 不协调 | 单一调度器能观察两类请求，但预算问题仍须解决 |
| 正式监控 | 表面不停服务，但可能拖慢/超时；不能承诺零影响 | 明确中断，用户安排成人看护；测试结果不冒充监控 |
| 端口/数据 | 18010/18881 候选回环端口，新 DB；端口隔离不是 GPU 隔离 | 同候选端口，旧 8010/8080 暂停；结束后恢复原服务/入口 |
| 当前授权/契约 | 本阶段禁止；冻结契约第 9 节也禁止在生产 GPU 额外加载真实实例 | 本阶段仅设计，须批准停止/测试/恢复后才执行 |

**推荐 B。** A 技术上存在内存余量，但 GPU 当前已忙，且与冻结契约的部署 gate 冲突。若以后仍选择 A，须用户单独接受该例外和监控影响；仅“换端口”不足以授权。另写一个测试 Web 服务指向旧 8010 也不能获得不存在的 /chat，不能靠 Mock 替代真实文字测试。

推荐 B 的具体形态：**保留生产 main/文件原样，维护时停止正式主应用及旧 worker，在新 Jetson 测试目录、18010 模型端口和 18881 Web 端口运行 M1 的唯一测试 worker/测试应用；恢复旧 8010/8080。** 无须为了测试在生产工作副本 checkout M1。

## 7. 方案 B 的拟执行流程（现在均未执行）

1. 用户批准维护时间、预计中断、测试数据/素材和预算问题处理边界。建议为完整性能样本预留 60–90 分钟（E）；20–30 分钟只能做小样本功能 smoke，不能声称稳定 P95。超过窗口停止新增请求并恢复监控。
2. 在独立 Jetson 目录建立固定 M1 SHA 的工作副本；开发/验证薄测试 harness。先在 Mac 复核它不会 source 正式 .env、打开真实设备、读生产 DB、打开 LAN listener 或替换模型配置。harness 尚未编写，需后续授权；`run-assistant-dev.py` 固定使用合成模型，不能直接拿它当真实 Qwen 服务。
3. 重查 PID/start ticks、端口、时间、权限、空闲资源和 Git hash；准备一致性备份与可用恢复命令；暂停前记录正式参数、子进程树和日志路径，私有内容仅留受限备份目录。
4. 优先用主应用既有 SIGTERM/SIGINT handler 正常关闭 22254。它会停视觉/采集/语音子进程；VLM join 可能等待 timeout 60s＋余量。核对 22299、22352 等后代退出及设备释放，不能只看父 PID。现有 voice_control 关闭逻辑有 TERM 进程组、3s 超时 KILL fallback；批准维护需知道这一既有连带行为，本阶段未调用它。
5. 主应用停止后等待旧视觉请求结束，核对模型/GPU 的空闲迹象，再用模型已有 SIGINT/Python finally 退出旧 worker 22156。旧模型没有显式 SIGTERM 清理 handler；健康 ok 不能证明它没有在途生成。若不能正常退出/清理，停止后续加载并报告，不能自动 SIGKILL 模型或同时启动替代实例。
6. 使用最终停写后的数据库快照作为恢复点；再确认只有一份真实模型即将运行。Tailscale/NTP/功耗、系统和生产 main 都保持原样。
7. 新测试 worker 必须用旧环境的**绝对解释器与库路径**，cwd/脚本指向新测试代码；显式设模型路径为既有只读权重。worktree 没有忽略的 .venv/models，不能盲用其 run-model.sh；不得安装第二套环境。启用 Assistant，unprofiled_chat 保持 false，保留 offline flags、BF16/eager、token/time budgets 和 Vision First。
8. 测试 Web 使用真实 AssistantService→18010 `/chat`，独立新 SQLite。注入合成/获授权 fixture camera adapter，禁用物理采集、语音和真实媒体播放。现有 VLMObserver/状态机只写测试视觉库；先验证 fixture adapter 再启动。仅 127.0.0.1:18881，保留登录、Session、Host、Origin、CSRF；通过获批准的 SSH 隧道给 Mac 前端访问，不修改 Tailscale Serve，不转发模型端口给浏览器。
9. 先无聊天 vision baseline，然后在可信 fixture driver **确实知道下一次 vision 到达时刻**的空档测试聊天。启动后无预算的 503 是正确保护；不得直接修改 scheduler 的 deadline 来造空档。此低负载功能测试不等于生产 cadence 验收。
10. 预算安全前置未解决时，不执行“原观察器同时聊天”准入试验；保留为阻塞项。解决并批准后才用相同原 interval 测混合负载，所有性能统计按请求/配置分层。
11. 停止新测试入口并排空/正常结束测试进程，确认测试模型退出/资源释放。按原 launch scripts、原 .env、原数据目录恢复旧模型与旧主应用，记录新 PID；执行第 10 节恢复验收。会话失效后重新登录，保留安全检查。

## 8. M2-B 真实模型功能用例

所有输入均为虚构内容。精确“今天”样例建议在 Asia/Shanghai 当天 14:20 之后运行，避免把尚未发生的时间误当提取失败；另测真正未来时间的保守行为。不得为了让测试过而校时。每组清空/新建的是测试会话/测试 DB；重复使用相同幂等键验证的请求不得清空记录。

记录四层证据：①原始 Qwen JSON/结构是否合法；②后端语义校验/record_actions；③实际测试 SQLite 事务/审计/版本；④真实 UI/HTTP 状态。口头“已记录”不作为证据；502/504 零写入只证明失败安全，不证明理解正确。

| 用例 | 输入 / 前置 | 期望与验收 |
|---|---|---|
| C0 中文多轮 | 你好、一般日常问题；独立 A/B 会话 | 合法 answer/actions/query，中文可读；上下文不串线；无编造事件 |
| C1 完整喂奶 | 今天上午11点宝宝喝了150ml奶。 | past feeding，11:00 CST 对应 UTC，实际量150；严格校验后 saved；SQLite 唯一行/创建修订 |
| C2 冲泡≠喝下 | 冲了150ml，但宝宝只喝了80ml。 | 不能记150；缺时刻需确认，实际量80或保守缺失；确认具体时刻后才入80 |
| C3 模糊量/时刻 | 宝宝刚才喝了一些奶。 | needs_confirmation；不猜量或精确时刻；确认前零写入，补填后唯一行 |
| C4 未来计划 | 明天下午准备给宝宝换尿布。 | 未发生，不存 confirmed；actions为空或not_saved皆可；不能以失败零写入冒充语义通过 |
| C5 完整睡眠 | 下午1点30分到2点20分宝宝睡了一觉。 | 单独组，已过去的13:30–14:20；saved sleep，duration3000s；统计按区间并集，不加摄像头时长 |
| C6 查询总量 | 今天宝宝喝了多少奶？ | 独立已确认C1=150＋C2=80的测试组；facts合计230ml/2次；数字来自SQL，模型事实解释 actions=[]、query=null；不得写事件 |
| C7 更正 | 新会话中刚保存一条喂奶，再输入：上一条喂奶记录写错了。 | 保守待确认、更正目标是同 scope 的喂奶及其版本；没有更正值不能自动改；补充明确新值并确认后版本＋1/审计；旧版本409 |
| C8 歧义目标 | 喂奶后又有睡眠，再说“上一条喂奶记录写错了”；新会话无上条；已删目标 | 不误指另一类型/另一会话/另一范围；信息不足要求指定记录，不自动修改 |
| C9 跨会话/删除 | A创建150，B查询；删除A，再查；独立删除事件，再查 | 消息删除、事件保留/source_message_id=NULL；事件删除后统计排除；不保留删除正文作审计 |
| C10 幂等/故障 | 同client ID在queued/running/completed重发、冲突内容；推理中删会话、登出/撤权；超时、截断JSON | 无重复、无复活/部分写/虚假保存；严格错误、可重试语义；未经确认不自动生成新ID重发 |
| C11 范围/睡眠 | 测试DB中分别放虚构demo/real；重叠/跨日sleep；空视觉、adult_test记录 | wrong-scope404、无交叉统计；manual union/clipping；camera seconds=null、不当可靠全天时长 |

每个关键语义至少重复10次、同时报告失败样本，不只挑成功输出。功能准入建议（E，待批准）：上述关键用例100%满足业务不变量、所有成功生成JSON合法、0次虚假保存/重复/跨范围写入；失败率与原因单独列出。任何错记150、错目标、非法SQL/代码执行、错误范围或删除后复活即阻断。若256 tokens/8s软预算无法可靠输出完整JSON，报告限制并另行提修订，不擅自放宽预算或改模型。

## 9. 视觉与共享模型负载计划

### 9.1 视觉 fixture

素材清单先固定来源授权、SHA256、尺寸、场景、expected解析，不携带真实儿童身份。使用空/灰/遮挡的合成JPEG；姿态正向用虚构渲染/玩偶或经明确同意的成人测试素材。成人素材标为 adult_test；测试 infant 时间线可用合成空场景，不能把成人结果冒充 infant 准确率。

| 场景 | 检查 |
|---|---|
| 灰图、空房间 | `/analyze` 包含原model、person_test_mode、observation_mode、load_seconds、infer_seconds、observation；无可确认对象时允许unknown/false |
| 遮挡、不清晰、矛盾姿态 | 可见性/睡醒/姿态枚举与置信度解析；不强迫未知猜确定姿势；partial/矛盾经normalize保守处理 |
| 授权成人/虚构姿态 | supine/side/prone和awake/sleeping等字段能按已有Schema解析；记录原输出和归一化结果，人工标注分歧 |
| 模式切换、旧帧 | person flag/observation mode一致；成人不进入infant事实；同一fixture重复及时间先后可追溯 |
| 时间线 | 真实HTTP返回经现有VLMObserver/状态机写**测试**observations/intervals；核对条目/来源/时间/unknown，不能仅调/analyze后手工插SQL冒充完整集成 |

unknown 是有效保守结果，但如果所有明确正向样例都unknown，不能据Schema通过宣称识别能力合格。本次不做2B/4B跑分或婴幼儿准确率承诺。

### 9.2 负载阶段与指标

1. cold load 与5次 warm-up单列；至少50次固定视觉fixture的无聊天baseline、50次低负载聊天，再按已批准的安全预算进行混合负载。小样本先报告数量，不把不稳定P95当结论。
2. 混合场景：同一worker同时收到1个vision与跨会话chat；连续vision期间排队chat；达到vision waiting1/chat waiting2的边界与超额；无预算/过期/高负载保护；失败后恢复；access撤权和删除在途turn。Web层最多4个pending/一业务worker，应区分Web业务排队与模型队列；直接内部模型负载不替代13 API流程。
3. 保持原30/60/15以及失败5s路径的相同排程/输入条件；已知低频fixture用来验证文字能力时单列，不能用延长正式间隔“优化”并发结果。8s soft cap不能被称作硬实时截止。
4. client记录：接收202、每次轮询、terminal完成/失败的单调时间；每个子RPC提交/返回、类型、字节数、HTTP code/error code。成功模型chat有queue_wait_ms/infer_ms/tokens；原vision.infer_seconds只覆盖生成/同步，不等于完整RPC或有效观察间隔。
5. 记录聊天端到端P50/P95/max（包括Web等待和facts的第二次模型调用）；成功、超时、拒绝分别列样本/比例，不删失败来美化延迟。45s是一次模型transport上限，不能当排队4个turn或两次RPC的整个消息保证。
6. 记录vision推理与RPC P50/P95/max、有效成功观察结果间隔、帧采集/提交/结果时间、失败和覆盖空档。相邻模型成功不自动等于可靠全天睡眠覆盖。
7. health的running_kind/pending/completed/failures定时读取；统计429/503/504/502与queue_full/vision_capacity_reserved。completed含finally结束的失败job，不直接当成功数。失败vision没有独立queue_wait字段；RPC减infer是混合开销估计，不能冒充精确排队时长。
8. 1s tegrastats取RAM/Swap/GPU/温度采样峰值；若需精确CUDA allocated/reserved峰值，需在唯一**测试**worker的测试观测wrapper中采集，不注入正式进程、不改队列优先级；采样峰值与allocator峰值分开报告。记录测试前后空闲资源和至少15分钟空闲后的趋势，不凭一次快照判断无泄漏。

**建议准入指标 E：** 本机POST接受P95≤1s；成功聊天单次infer<8s且tokens≤256/input≤2048；队列容量/到期/拒绝与基线一致，GPU临界区最大并发1；已批准安全空档内有chat成功、无空档则明确拒绝；vision混合P95较同样fixture baseline额外≤max(2s,10%)，有效观察间隔最大额外延迟≤2s；0 OOM/进程崩溃/错误写入，失败后可恢复。比例/时间目标是拟议验收线，不是既有实测保证；如达不到就报告失败、保持正式Assistant关闭，不能改采样频率隐藏它。

停止/回退建议 E：出现数据边界错误、GPU owner重叠、进程失去响应、OOM、明显持续Swap增长，或可用内存低于10GiB时暂停新增请求并恢复；这些是建议测试阈值，未把Proposed ADR当作已批准运行政策。未解决第5节的反例，不得宣称“Vision First已保护所有实际分支”。

## 10. 数据保护、备份与回退

### 10.1 实际位置与备份条件

- **F：活跃视觉库** `~/baby-monitor-public/data/camera-check-20260914/baby_monitor.db`，442,368 bytes、0600；目录0700；其wal/shm也0600。从PID22254的实际FD和BMON_DATA_DIR确认，不从默认值猜。
- **F：Assistant生产库不存在**：活跃目录和当前项目data目录均未发现assistant.db。若以后在原数据目录直接启用M1，会创建那里的一套库；本计划明确不这样做。
- **F：默认 `data/baby_monitor.db` 是0-byte非活跃文件**，0644但父目录0700；来源未核查、未删除。不可误拿它当正常生产备份。活跃库stat/wal元数据的mtime不是可靠的最近业务时间证明；本阶段未查询其记录。
- **C：视觉库WAL/NORMAL，Assistant库WAL/FULL＋独占进程文件锁。** 单独一份live .db复制可能漏掉已提交WAL内容。`immutable=1`不适用于正在变化的生产库，不能用它规避锁。
- **E：未来测试目录** `~/nestory-m2b/<UTC-run-id>/`：code、data、media-fixtures、logs分开；目录0700，SQLite/config/logs0600，umask077。权限/空间本阶段仅提出，未创建Jetson目录。test assistant.db与visual baby_monitor.db必须resolve到test data内，拒绝生产目录和符号链接到生产数据；仅权重/现有解释器允许只读复用。

### 10.2 将来一致性备份的方法（未执行）

批准后，在线备份优先使用现有Python sqlite3.Connection.backup / SQLite Online Backup API，写到新私有备份文件，检查返回/完成状态；不先强制生产checkpoint，不边写边分别cp db/wal/shm。源可用mode=ro，但WAL读者仍可能涉及现有共享内存锁，因此本次没有打开它。方法依据 [SQLite Online Backup](https://www.sqlite.org/backup.html) 与 [WAL说明](https://www.sqlite.org/wal.html)。

在停服务前保留一份成功的在线snapshot；正式应用正常停写/关闭后再取最终快照，记录cut时间。两套数据库的独立backup不是跨库原子事务；当前只有视觉库，未来若有双库，应在所有写者静止时取得同一恢复边界。仅在备份**副本**上做integrity_check、schema/version/必要数量核对与恢复演练，不把私人行内容输出到报告。

备份生产.env、主体/访问/语音设置、已追踪代码SHA和原启动wrapper、权限/所有者、模型manifest与旧依赖版本；备份不进入Git、测试fixture或公共目录。原model/venv保持原样，只读复用；不需要复制4GiB权重才能避免覆盖。备份空间估算以核对后的目录元数据与日志保留策略为准，不先复制私人媒体或完整声音记录。

### 10.3 回退与恢复验收

1. 停止测试Web、fixture driver与测试worker，确认无测试进程占端口/设备、单一模型资源已释放；保留隔离日志/DB，不把测试库覆盖生产库。
2. 生产working tree一直保持main/82c5015，优先直接以原.env、wrapper、权重、venv恢复旧8010，再启动原8080主应用及既有LAN listener。无需reset、checkout main或安装依赖。
3. 正常回退不恢复/覆盖原数据库，因为测试从未连接它。只有确认损坏并获得恢复授权才使用已验证snapshot；停止全部读写者，保留现状，正确处理旧wal/shm后恢复一致备份。绝不拿一份旧备份自动回滚合法生产记录。
4. **旧PID不能保证复原**；OS重启进程会分配新PID。目标是相同职责、代码、环境、数据、端口和可用性，记录旧→新PID/start ticks，并检查子进程无孤儿/重复模型。
5. 恢复模型 `/health`；摄像头原by-id节点与尺寸配置未改变，在用户授权的无隐私测试场景检查实际新帧、正常视觉结果、当前模式、时间线的正常新记录；这属于M2-B恢复验收，不在M2-A提前推理。
6. 通过原Tailscale/用户日常访问入口和LAN授权路径重新登录，验证会话/Host/Origin/CSRF、画面/状态更新；关注既有connmark与默认代理路径问题。仅SSH通或health ok不算端到端恢复。
7. 记录维护开始/恢复UTC及未知覆盖区间，不捏造连续观察、睡眠时长或缺口期间记录；正常恢复也不等于已批准启用Assistant。

测试日志只允许虚构输入、授权fixture的raw JSON、event ID/事务/版本与聚合资源信息；不记录Cookie、Token、密码、生产.env正文、原始私人画面/音频/实际记录。真实模型若意外接收到生产数据，立刻停止测试、隔离输出并报告，不能继续作为样本。

## 11. M2-B 前需要用户授权的清单

当前没有执行这些动作，以下批准与“main合并/公开发布/永久启用Assistant”相互独立：

1. **选择方案B及窗口**：允许监控/语音暂时中断，明确成人看护、时长和到点恢复责任。
2. **备份与恢复边界**：允许私有配置及活跃视觉库的一致性备份、副本检查；必要时数据库恢复另行确认，默认不覆盖生产记录。
3. **受控停止/恢复**：批准关闭既有主应用、语音/arecord连带进程和旧8010 worker，再按原配置恢复。正式main不切换。模型异常退出/卡住不包含自动强杀或重启整机的授权。
4. **测试资源**：允许新建独立测试代码/数据库/日志目录与候选回环18010/18881、只读复用原环境/权重、加载一份2B测试模型；禁止同时加载第二份。
5. **测试harness和素材**：批准只测虚构数据及明确授权素材、真实13 API与测试视觉时间线；接受记录raw模型输出及资源指标，保证不读生产数据。
6. **预算安全问题处理**：若要完成原观察器混合负载准入，需另行批准第5节所述仅内部预算保护修正/回归；保留v1与Vision First。若暂不批准，M2-B只能交付隔离排程的功能结果，正式并发保持阻塞。
7. **指标/停止条件**：确认建议样本规模、相对延迟线、内存停止阈值与报告方式；不凭一次成功或低负载数据宣称生产安全。

不包含：4B升级、Chat First、SSE、新事件类型、RTC迁移/校时、依赖升级、摄像头配置修改、生产main合并、正式Assistant上线或GitHub发布。

## 12. 本报告验证与最终状态

文档分支仅新增本报告；按AGENTS完成本地验证：

| 检查 | 真实结果 | 边界 |
|---|---|---|
| `python3 -B -m unittest discover -s tests -v` | 161项，160通过、1项既有可选拼音测试跳过；25.477s，exit 0 | 模拟模型/临时SQLite；含既有真实HTTP与重启持久化回归；不代表真实Qwen验收 |
| `python3 -B scripts/scan-release.py` | 103文件、13图片；1项finding，exit 1 | 仅既有 `tests/test_security.py` 的private-ip测试常量；报告没有新增命中；没有削弱扫描或获得公开发布许可 |
| Git文件范围 | 只新增本报告，生产代码及冻结契约不变 | 不运行生产同步脚本、不更新main |

服务终检时间为 **2026-10-13T12:41:37Z / 20:41:37 CST**：生产仍为干净main/82c5015；模型源hash仍为第4节旧版本；8010/8080/HTTPS监听不变，18010/18881仍未启动；模型health仍200/ok=true、load_seconds=3.665、无scheduler；NTP仍同步。共享main与M1 ref仍为第1节SHA。

原模型22156、主应用22254、语音22299、arecord22352的PID、PPID、ps启动时间与本次初查一致。保存这次读取的 `/proc/PID/stat` start ticks 为后续对照：模型903280、主应用904774、语音904807、arecord905210；Tailscale795为1386、NTP538为1133。这些值是本次boot内的标识，不作跨重启恢复目标。

只将本报告分支同步到内部裸仓库；本次所有发现保持未修复。没有新增浏览器/GPU实测、模型请求、生产数据库操作或维护切换；真实Qwen功能、延迟/GPU峰值与恢复流程仍待第11节授权后的实测。
