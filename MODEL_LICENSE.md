# Model sources and licensing

No weights, private fine-tunes, model caches or training datasets are included. Entries below
identify optional integrations, not a blanket right to redistribute downloaded artifacts.
Source review date: 2026-09-10. Record the exact revision and SHA-256 of any artifact you use.
GPU package and model downloads are intentionally not executed during public preparation.

| Model | Referenced version | Source / download | License status |
|---|---|---|---|
| Qwen3-VL | Qwen3-VL-2B-Instruct | [Official model and files](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) | Official model card: Apache-2.0. Pin the chosen revision before provisioning. |
| YuNet | face_detection_yunet_2023mar.onnx | [Official model directory](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | Directory [MIT license](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE); preserve its notice. The main branch is mutable. |
| EmotiEffLib expression | enet_b0_8_best_afew, library 1.1.1 | [Upstream models](https://github.com/sb-ai-lab/EmotiEffLib/tree/main/models) | Library code: Apache-2.0. Exact weight/training-data redistribution rights require artifact-level review; no weights redistributed here. |
| Sherpa keyword spotting | sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20 | [Release artifact](https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2) | Sherpa code is Apache-2.0. Exact model-specific terms and this artifact's availability remain to be verified before use. |
| Paraformer ASR | sherpa-onnx-paraformer-zh-small-2024-03-09 | [Model repository](https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09), [release archive](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-paraformer-zh-small-2024-03-09.tar.bz2) | Verify the exact converted model's license and upstream notices before use or redistribution. |
| Silero VAD | silero_vad.onnx; exact revision not recorded | [Original upstream](https://github.com/snakers4/silero-vad), [Sherpa artifact](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx) | Original upstream states MIT. The mirrored artifact is not pinned; verify provenance and matching terms. |
| Optional posture classifier | posture.onnx interface only | No model supplied | A new model needs its own model card, consent/provenance assessment and license. Do not distribute personal fine-tunes. |

When redistributing an artifact in a future release, retain its applicable LICENSE/NOTICE and
record its source revision, hash and permissions separately. Dataset terms and model terms
must not be inferred from a library's code license. This file is a provenance register with
explicit unresolved items, not legal clearance or a claim that all optional artifacts are verified.
