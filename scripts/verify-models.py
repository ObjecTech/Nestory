"""Exercise the project's three pretrained models offline using synthetic pixels.

Run with: bash scripts/model-python.sh -B scripts/verify-models.py [--vlm]
This does not access a camera, save media, or evaluate recognition accuracy.
"""
from __future__ import annotations

import argparse
import io
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def deny_network(*args, **kwargs):
    raise RuntimeError("Network access attempted during offline model verification")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlm", action="store_true", help="Also load Qwen on CUDA and generate JSON")
    args = parser.parse_args()
    socket.socket.connect = deny_network
    socket.create_connection = deny_network

    import numpy as np
    from PIL import Image
    from baby_monitor.config import Config
    from baby_monitor.inference import InferenceWorker

    config = Config.from_env()
    # Exercise the production loader without starting its camera/database thread.
    worker = InferenceWorker.__new__(InferenceWorker)
    worker.emotion_model_name = config.emotion_model_name
    worker.face_model_path = config.face_model_path
    from baby_monitor.inference import PostureClassifier
    worker.posture = PostureClassifier(config.posture_model_path)
    worker._load_models()
    assert worker._face_detector is not None, "YuNet was not loaded"
    pixels = np.full((320, 320, 3), 127, dtype=np.uint8)
    _, faces = worker._face_detector.detect(pixels)
    assert faces is None, "Unexpected face in blank synthetic image"
    _, scores = worker._emotion.predict_emotions(pixels, logits=False)
    assert scores.shape == (1, 8) and np.isfinite(scores).all()
    assert np.allclose(scores.sum(axis=1), 1, atol=1e-5)
    result = {"yunet": "passed", "emotiefflib": "passed", "emotion_output_shape": list(scores.shape),
              "posture_installed": worker.posture.online, "network": "blocked"}
    print(json.dumps(result), flush=True)

    if args.vlm:
        import torch
        import model_server

        assert torch.cuda.is_available(), "CUDA is required for this deployment check"
        engine = model_server.Engine()
        engine.load()
        assert engine.model.device.type == "cuda", "Qwen did not load onto CUDA"
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="JPEG")
        response = engine.analyze(buffer.getvalue())
        assert response["observation"]["baby_visible"] is not True, response
        print(json.dumps({"qwen": "passed", "gpu": torch.cuda.get_device_name(),
                          "torch": torch.__version__, "result": response}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
