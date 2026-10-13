# Jetson 本地视觉模型

这三个集成使用上游预训练权重，没有项目内微调步骤。`posture.onnx` 是可选的
个性化分类器接口，目前不安装；不能从接口推断已有老师训练好的模型。

## 文件布局

以下路径相对于 Jetson 的 `~/baby-monitor-public`：

| 用途 | 本地位置 | 项目读取方式 |
| --- | --- | --- |
| Qwen3-VL-2B-Instruct | `models/Qwen3-VL-2B-Instruct/` | `model_server.py` 默认目录；含完整 tokenizer、processor、配置和 safetensors |
| YuNet | `models/face_detection_yunet_2023mar.onnx` | `Config.face_model_path` 默认文件 |
| EmotiEffLib | `models/emotiefflib/enet_b0_8_best_afew.onnx` | `~/.emotiefflib/enet_b0_8_best_afew.onnx` 链接到该文件；这是上游 1.1.1 的固定缓存路径 |
| 来源记录 | `models/manifest.json`、`models/licenses/` | 精确 revision、文件大小、SHA-256 和上游许可证 |
| Python 环境 | `.venv-models/` | `scripts/model-python.sh` |
| 离线安装材料 | `.cache/provision/` | Python wheels、图像处理库源码、cuSPARSELt 压缩包 |

权重、环境和安装缓存均被 Git 忽略，不通过源代码仓库分发。

## 启动和验证

在 Jetson 项目目录中启动 Qwen worker（前台运行，Ctrl+C 停止）：

```bash
bash scripts/run-model.sh
```

服务仅监听 `127.0.0.1:8010`。另一个终端检查：

```bash
curl --fail http://127.0.0.1:8010/health
```

不启动摄像头或主应用，也可重复执行完整模型验证：

```bash
bash scripts/model-python.sh -B scripts/verify-models.py --vlm
```

不加 `--vlm` 只验证 YuNet 和 EmotiEffLib。验证脚本阻止 Python 网络连接，
调用生产模型加载器，并用纯灰合成图片执行推理。它验证离线运行和输出结构，
不代表婴儿识别、表情或睡姿的准确率评估。

主应用在完成账户配置及摄像头配置后，在私有 `.env` 中设置：

```bash
BMON_PYTHON=./scripts/model-python.sh
BMON_MODEL_ENABLED=true
BMON_VLM_ENABLED=true
BMON_VLM_SERVICE_URL=http://127.0.0.1:8010
```

然后用 `bash scripts/run.sh` 启动主应用，并保持 Qwen worker 运行。
`BMON_DEMO=true` 的演示模式不会启动模型推理。硬件接入后再设置为 `false`。
本次模型部署不创建账户、不启用摄像头采集、不安装开机服务。

## 部署环境与硬件规格

目标设备为 Jetson AGX Orin 64GB 开发套件，系统环境为 L4T R36.4.0、CUDA 12.6、cuDNN 9.3、Python 3.10。
板载 64GB 统一内存（实际可用 61GB，常驻空闲内存约 50+ GB），显存与算力资源充裕。

网络与系统时间状态：
- 设备已接入 Tailscale 组网及局域网环境，并已启用 NTP 网络时间同步服务。
- 操作系统时区为 `Asia/Shanghai`（中国标准时间，CST，UTC+8），时间同步正常（已校准为真实当前时间，不再是早期离线阶段的 1970 年状态）。
- 主应用监测时间线、历史记录与日志均已使用校准后的真实时间戳。

实测：三模型文件校验通过；禁用 Python 网络连接的推理验证通过。
YuNet 对灰色合成图片未检出人脸，EmotiEffLib 返回有限且归一化的 8 类概率；
Qwen 在 Orin GPU 上加载约 4.98 秒、生成约 14.50 秒，返回合法 JSON，
判断无宝宝并将睡醒、姿势、表情字段置为 `unknown`。
这些时间只代表这张 320×320 合成图片，不是实际摄像头画面的性能保证。
项目单元测试运行 58 项，57 项通过、1 项跳过。

关键依赖：NVIDIA PyTorch `2.5.0a0+872d972e41.nv24.8`、Transformers `4.57.6`、
Accelerate `1.12.0`、EmotiEffLib `1.1.1`、OpenCV `4.11.0.86`、NumPy `1.26.4`、
ONNX `1.19.1`、ONNX Runtime `1.18.1`。此 Python 3.10 环境使用兼容的 ONNX 版本，
不要直接用通用 `requirements-lightweight.txt` 覆盖已验证环境。
cuSPARSELt `0.6.3.2` 安装于 `.venv-models/lib`，由 wrapper 设置动态库搜索路径。
虚拟环境允许访问系统 site-packages，以便主应用使用系统 GI/GStreamer 绑定；
模型依赖优先使用虚拟环境内的版本。

Torchvision `0.20.0` 从官方 `pytorch/vision` 的同名标签在 Jetson 编译。
仅构建 CPU 扩展及图像解码，Qwen 的 GPU 推理由 PyTorch 提供；没有安装额外的
GPU 视频解码扩展。离线重建命令（已解压源码和安装依赖时）：

```bash
export PATH="$PWD/.venv-models/bin:$PATH"
export LD_LIBRARY_PATH="$PWD/.venv-models/lib:/usr/local/cuda/lib64"
cd .cache/provision/vision-0.20.0
CUDA_VISIBLE_DEVICES="" TORCHVISION_USE_NVJPEG=0 TORCHVISION_USE_FFMPEG=0 \
  TORCHVISION_USE_VIDEO_CODEC=0 BUILD_VERSION=0.20.0 MAX_JOBS=6 \
  python -m pip wheel --no-deps --no-build-isolation --no-index . -w ../wheels
python -m pip install --no-index --no-deps ../wheels/torchvision-0.20.0-*.whl
```

设备当前启用 CPU 0–7，物理核心为 0–11。ONNX Runtime `1.23.2` 在导入时发生
CPU 拓扑断言崩溃，因此使用已通过推理的 `1.18.1`。后者可能打印针对离线 CPU 核的
线程亲和性警告，但本次测试正常完成；没有为此更改设备功耗模式。

Qwen 使用 NVIDIA GPU 和 BF16；YuNet 使用 OpenCV CPU，EmotiEffLib 使用 ONNX Runtime CPU，
与项目现有调用一致。可选的 GPU 优化和真实摄像头准确率评估属于后续工作。

## 固定的上游来源

- [Qwen 官方模型](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct/tree/89644892e4d85e24eaac8bacfd4f463576704203)：revision `89644892e4d85e24eaac8bacfd4f463576704203`。
- [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)：revision `47534e27c9851bb1128ccc0102f1145e27f23f98`。
- [EmotiEffLib ONNX 模型](https://github.com/sb-ai-lab/EmotiEffLib/tree/520a051c64cd191521e5934655314e769a319684/models/affectnet_emotions/onnx)：revision `520a051c64cd191521e5934655314e769a319684`。
- [NVIDIA JetPack 6.1 PyTorch wheel](https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/) 和 [cuSPARSELt](https://developer.download.nvidia.com/compute/cusparselt/redist/libcusparse_lt/linux-aarch64/)。

授权来源说明见 `MODEL_LICENSE.md`；库的许可证与训练数据、权重的再分发条款应分别对待。

## 2026-09-14 摄像头实机联调

Logitech C270 HD WEBCAM 已识别，视频节点为 `/dev/video0`；
1280×720、15 fps 的 MJPEG GStreamer 管线成功取帧，网页显示真实实时画面。
主应用及 Qwen worker 已手动启动。主应用已成功调用 Qwen 分析真实摄像头图片，
结果写入 observations，网页显示本地 AI 运行中。
随后对完整人脸画面进行生产加载器及检测函数验证：YuNet 检出 1 张人脸，
眼部检测检出双眼，EmotiEffLib 返回完整的 8 类表情概率。
这些检查证明实机推理链路可用，不构成婴儿睡醒、表情或睡姿准确率验收。

本次使用项目已有的本机预览模式，服务只监听 Jetson 回环地址，Mac 通过 SSH
访问，未开放局域网免登录。Mac 预览地址为 `http://localhost:18080/`。
需要恢复通道时，在 Mac 执行（已有通道时不要重复创建）：

```bash
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 jetson
```

Jetson 已通电并可通过 SSH 连接时，也可以直接在 Mac 项目目录执行一键启动：

```bash
bash scripts/start-jetson-preview.sh
```

该脚本仅启动尚未运行的 Qwen worker 和主应用，并等待两者通过健康检查。
回环预览配置下，它会在 Mac 后台建立 SSH 通道；局域网预览配置下，它会直接
打开 `BMON_PUBLIC_ORIGIN`。重复执行不会重复启动服务或通道。
运行日志保存在 Jetson 的 `logs/model-preview.log` 和 `logs/app-preview.log`。
默认使用 SSH 别名 `jetson` 和 Mac 端口 `18080`；必要时可以通过
`BMON_JETSON_HOST` 和 `BMON_PREVIEW_PORT` 覆盖。

Jetson 的私有 `.env` 已配置摄像头和两个视觉 worker，`BMON_DEMO=false`，
Qwen 请求超时设为 60 秒。此次自动分类照片开关关闭；测试数据库位于
忽略目录 `data/camera-check-20260914/`，日志位于 `logs/*-camera-check.log`。
正式使用前需配置用户自己的登录凭据并关闭临时免登录预览。

重启后时间发生回退：此次系统日期为 2024-11-28，RTC 为 1970 年，
因此本次数据库时间戳不代表实际日期；之前手动校时未通过重启保持测试。
应用与模型 worker 也没有开机自启。以上两项仍属于待处理的部署问题。
