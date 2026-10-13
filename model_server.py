from __future__ import annotations

import io
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import torch
from PIL import Image, ImageFile
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

ImageFile.LOAD_TRUNCATED_IMAGES = True

from baby_monitor.vlm import normalize_observation
from baby_monitor.model_prompt import INFANT_PROMPT, observation_prompt
from baby_monitor.observation_mode import mode_name


MODEL_PATH = Path(
    os.getenv("BMON_VLM_MODEL_PATH", str(Path(__file__).resolve().parent / "models/Qwen3-VL-2B-Instruct"))
)
HOST = os.getenv("BMON_VLM_HOST", "127.0.0.1")
PORT = int(os.getenv("BMON_VLM_PORT", "8010"))
MAX_NEW_TOKENS = int(os.getenv("BMON_VLM_MAX_NEW_TOKENS", "220"))
MAX_IMAGE_BYTES = int(os.getenv("BMON_VLM_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
MAX_IMAGE_EDGE = int(os.getenv("BMON_VLM_MAX_IMAGE_EDGE", "672"))

PROMPT = INFANT_PROMPT


def _extract_json(text: str) -> dict:
    content = text.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].lstrip()
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    result = json.loads(content[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("模型 JSON 顶层必须是对象")
    return result


class Engine:
    def __init__(self):
        self.processor = None
        self.model = None
        self.ready = False
        self.error: str | None = None
        self.load_seconds: float | None = None
        self.lock = threading.Lock()

    def load(self) -> None:
        started = time.perf_counter()
        try:
            self.processor = AutoProcessor.from_pretrained(
                MODEL_PATH, local_files_only=True
            )
            self.model = Qwen3VLForConditionalGeneration.from_pretrained(
                MODEL_PATH,
                dtype=torch.bfloat16,
                device_map="auto",
                attn_implementation="eager",
                local_files_only=True,
            )
            self.load_seconds = round(time.perf_counter() - started, 3)
            self.ready = True
            self.error = None
        except Exception as exc:
            self.ready = False
            self.error = "Model initialization failed"
            raise

    def analyze(self, image_bytes: bytes, person_test_mode: bool = False) -> dict:
        if not self.ready or self.processor is None or self.model is None:
            raise RuntimeError(self.error or "模型尚未就绪")
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": observation_prompt(PROMPT, person_test_mode)},
                ],
            }
        ]
        prompt = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.processor(
            text=[prompt], images=[image], return_tensors="pt"
        ).to(self.model.device)
        started = time.perf_counter()
        with self.lock, torch.inference_mode():
            generated = self.model.generate(
                **inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False
            )
            torch.cuda.synchronize()
        infer_seconds = round(time.perf_counter() - started, 3)
        trimmed = generated[:, inputs.input_ids.shape[1] :]
        text = self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]
        return {
            "model": "Qwen3-VL-2B-Instruct",
            "person_test_mode": person_test_mode,
            "observation_mode": mode_name(person_test_mode),
            "load_seconds": self.load_seconds,
            "infer_seconds": infer_seconds,
            "observation": normalize_observation(_extract_json(text), person_test_mode=person_test_mode),
        }


ENGINE = Engine()


class Handler(BaseHTTPRequestHandler):
    server_version = "BabyMonitorQwen/1.0"

    def log_message(self, format: str, *args) -> None:
        pass

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._json(
            {
                "ok": ENGINE.ready,
                "model": "Qwen3-VL-2B-Instruct",
                "error": ENGINE.error,
                "load_seconds": ENGINE.load_seconds,
            },
            HTTPStatus.OK if ENGINE.ready else HTTPStatus.SERVICE_UNAVAILABLE,
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/analyze":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get_content_type() != "image/jpeg":
            self._json({"error": "仅接受 image/jpeg"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_IMAGE_BYTES:
            self._json({"error": "图片大小不合法"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self._json(ENGINE.analyze(
                self.rfile.read(length),
                person_test_mode=self.headers.get("X-Bmon-Person-Test-Mode") == "true",
            ))
        except Exception as exc:
            self._json({"error": "Model inference failed"}, HTTPStatus.BAD_GATEWAY)


def main() -> None:
    if HOST not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The unauthenticated model worker must bind to loopback.")
    if not MODEL_PATH.joinpath("model.safetensors").is_file():
        raise SystemExit("Model weights are not installed.")
    ENGINE.load()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Qwen 服务已启动：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
