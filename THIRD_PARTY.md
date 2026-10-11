# Third-party attribution

This release bundles project source and newly drawn geometric artwork only. It does not bundle
third-party library source, fonts, music, photographs or model weights. The Apache-2.0 project
license does not replace dependency or model licenses.

| Integration | Role | Source / license reference |
|---|---|---|
| Qwen3-VL | Local visual-language observations | [Qwen model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct), Apache-2.0 |
| EmotiEffLib | Lightweight expression inference | [Official project](https://github.com/sb-ai-lab/EmotiEffLib), code Apache-2.0 |
| OpenCV / YuNet | Image processing / face detection | [OpenCV Zoo model license](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE), model directory MIT |
| Sherpa-ONNX | Offline speech and keyword spotting | [Official license](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE), code Apache-2.0 |
| Silero VAD | Voice activity detection | [Official project](https://github.com/snakers4/silero-vad), upstream MIT; verify converted artifact |
| Python, Pillow, NumPy | Runtime, imaging and arrays | [Python](https://docs.python.org/3/license.html), [Pillow](https://github.com/python-pillow/Pillow), [NumPy](https://github.com/numpy/numpy) |
| ONNX / ONNX Runtime | Optional model execution | [ONNX](https://github.com/onnx/onnx), [ONNX Runtime](https://github.com/microsoft/onnxruntime) |
| PyTorch / Transformers / Accelerate | Optional Qwen runtime | [PyTorch](https://github.com/pytorch/pytorch), [Transformers](https://github.com/huggingface/transformers), [Accelerate](https://github.com/huggingface/accelerate) |
| pyatv / FFmpeg / GStreamer / PipeWire | Optional media transport and device integration | [pyatv](https://github.com/postlund/pyatv), [FFmpeg](https://ffmpeg.org/legal.html), [GStreamer](https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/licensing.html), [PipeWire](https://pipewire.org/) |
| pypinyin | Optional Mandarin command matching | [Official project](https://github.com/mozillazg/python-pinyin) |

For dependencies without a license identifier in this table, inspect the exact installed
package's license before distributing a binary environment. In particular, FFmpeg licensing
depends on build options. See MODEL_LICENSE.md for model-specific versions and unresolved terms.

## Artwork

All images under static/assets, static/apple-touch-icon.png and examples/sample_image.jpg were
created from geometric primitives for this release. No source photographs were used. They are
licensed with this project's original content under Apache-2.0. Original audio files were excluded.
