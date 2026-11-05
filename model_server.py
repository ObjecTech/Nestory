from __future__ import annotations

import io
from datetime import datetime
import json
import os
import signal
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
from baby_monitor.inference_scheduler import InferenceScheduler
from baby_monitor.assistant_common import AssistantError, fields, integer, text as validate_text, scope, zone, timestamp, strict_json


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
        # Single shared model; no unprofiled-chat environment bypass.
        self.scheduler = InferenceScheduler()
        self.chat_enabled = os.getenv("BMON_ASSISTANT_ENABLED", "false").lower() == "true"

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
        # Admission precedes processor use and every GPU allocation, not just generate().
        with self.scheduler.slot("vision"), self.lock, torch.inference_mode():
            prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.processor(text=[prompt], images=[image], return_tensors="pt").to(self.model.device)
            started = time.perf_counter()
            try:
                generated = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
                torch.cuda.synchronize()
                infer_seconds = round(time.perf_counter() - started, 3)
                trimmed = generated[:, inputs.input_ids.shape[1]:]
                text = self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]
                # A generation is successful only after JSON/schema processing.
                observation = normalize_observation(_extract_json(text), person_test_mode=person_test_mode)
            finally:
                del inputs
                if "generated" in locals(): del generated
                if "trimmed" in locals(): del trimmed
        return {
            "model": "Qwen3-VL-2B-Instruct",
            "person_test_mode": person_test_mode,
            "observation_mode": mode_name(person_test_mode),
            "load_seconds": self.load_seconds,
            "infer_seconds": infer_seconds,
            "observation": observation,
        }

    def chat(self, payload: dict) -> dict:
        if not self.chat_enabled:
            raise AssistantError("assistant_disabled", 503, retryable=True)
        if not self.ready or self.model is None or self.processor is None:
            raise AssistantError("model_unavailable", 503, retryable=True)
        fields(payload, ("request_id", "messages", "scope", "timezone", "reference_at", "facts", "max_new_tokens"),
               ("request_id", "messages", "scope", "timezone", "reference_at", "facts", "max_new_tokens"))
        validate_text(payload["request_id"], 128, "request_id")
        scope(payload["scope"]); zone(payload["timezone"]); timestamp(payload["reference_at"])
        maximum = integer(payload["max_new_tokens"], 1, 256, "max_new_tokens")
        messages = payload["messages"]
        if not isinstance(messages, list) or not 1 <= len(messages) <= 13:
            raise AssistantError("validation_error")
        for i, message in enumerate(messages):
            fields(message, ("role", "content"), ("role", "content"))
            if message["role"] != ("user" if i % 2 == 0 else "assistant"):
                raise AssistantError("validation_error")
            validate_text(message["content"], 4000, "content")
        if messages[-1]["role"] != "user": raise AssistantError("validation_error")
        if payload["facts"] is not None and not isinstance(payload["facts"], dict): raise AssistantError("validation_error")
        # Instructions are owned by this worker; user/fact contents are data, never system directives.
        # 方案 A：记录与统计由后端规则处理，模型只负责简短聊天
        # 2B 输出 JSON 不稳定，聊天改为纯文本作答，由本服务包装成原结构
        system = """你是本地育儿助手。直接用简短中文回答家长，不超过50字，不要使用JSON、Markdown或列表。
生活记录和统计由系统另行处理，不要声称已保存或已记录。
关于宝宝实际发生的事（几点、几次、多少、多久），只能用上下文数据里给出的事实；上下文里没有就回答“我这里没有查到相关记录”，绝不编造时间、次数或数量。
不要诊断疾病或建议用药，健康问题请家长咨询医护人员。
"""
        metadata = json.dumps({**{k:payload[k] for k in ("scope","timezone","reference_at","facts")},
            "local_date": datetime.fromisoformat(payload["reference_at"].replace("Z", "+00:00")).astimezone(zone(payload["timezone"])).date().isoformat()}, ensure_ascii=False)
        with self.scheduler.slot("chat") as wait_ms, self.lock, torch.inference_mode():
            history = list(messages)
            while True:
                template = [{"role":"system", "content":system + "\n上下文数据：" + metadata}] + history
                prompt = self.processor.apply_chat_template(template, tokenize=False, add_generation_prompt=True)
                inputs = self.processor(text=[prompt], return_tensors="pt")
                tokens = inputs.input_ids.shape[1]
                if tokens <= 2048: break
                del inputs
                if len(history) <= 1: raise AssistantError("validation_error")
                history = history[2:]
            started = time.perf_counter()
            try:
                inputs = inputs.to(self.model.device)
                generated = self.model.generate(**inputs, max_new_tokens=maximum, do_sample=False, max_time=8.0)
                torch.cuda.synchronize()
                infer_ms = round((time.perf_counter()-started)*1000)
                trimmed = generated[:, tokens:]
                output_tokens = trimmed.shape[1]
                decoded = self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]
                if infer_ms >= 8000: raise AssistantError("model_timeout", 504, retryable=True)
                result = dict(answer=chat_answer(decoded, output_tokens >= maximum), actions=[], query=None)
            except AssistantError as exc:
                if exc.code == "invalid_json": raise AssistantError("invalid_model_output", 502, retryable=True) from None
                raise
            finally:
                del inputs
                if "generated" in locals(): del generated
                if "trimmed" in locals(): del trimmed
            return dict(model="Qwen3-VL-2B-Instruct", request_id=payload["request_id"], queue_wait_ms=wait_ms,
                        infer_ms=infer_ms, input_tokens=tokens, output_tokens=output_tokens, result=result)


def chat_answer(decoded: str, truncated: bool) -> str:
    """把纯文本回答整理成 answer；兼容模型仍按旧习惯输出 JSON 的情况。"""
    content = decoded.strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    if content.startswith("{"):
        try:
            answer = strict_json(content).get("answer")
            content = answer.strip() if isinstance(answer, str) else ""
        except AssistantError:
            content = ""
    if truncated:
        # 达到 token 上限时截到最后一个完整句子
        cut = max(content.rfind(p) for p in "。！？!?；")
        content = content[:cut + 1] if cut > 0 else content
    if not content:
        raise AssistantError("invalid_model_output", 502, retryable=True)
    return content[:4000]


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

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

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
                "scheduler": ENGINE.scheduler.metrics(),
            },
            HTTPStatus.OK if ENGINE.ready else HTTPStatus.SERVICE_UNAVAILABLE,
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/chat":
            self._chat()
            return
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
        except AssistantError as exc:
            self._json({"error": "Model inference unavailable"}, exc.status)
        except Exception:
            self._json({"error": "Model inference failed"}, HTTPStatus.BAD_GATEWAY)

    def _chat(self):
        request_id = "internal-chat"
        try:
            if self.headers.get_content_type() != "application/json": raise AssistantError("unsupported_media_type",415)
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths)!=1 or self.headers.get("Transfer-Encoding"): raise AssistantError("invalid_json",400)
            try: length=int(lengths[0])
            except ValueError: raise AssistantError("invalid_json",400) from None
            if length>32768: raise AssistantError("payload_too_large",413)
            if length<=0: raise AssistantError("invalid_json",400)
            payload = strict_json(self.rfile.read(length))
            self._json(ENGINE.chat(payload))
        except AssistantError as exc:
            self._json(exc.envelope(request_id), exc.status)
        except Exception:
            self._json(AssistantError("model_failed",502,retryable=True).envelope(request_id),502)


class ModelHTTPServer(ThreadingHTTPServer):
    # Graceful close waits for the active generation; never abandon GPU work.
    daemon_threads = False

    def __init__(self, *args, **kwargs):
        self.handler_slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.handler_slots.acquire(blocking=False):
            try:
                body=b'{"error":{"code":"queue_full","message":"Model request capacity exceeded","retryable":true}}'
                request.sendall(b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except BaseException:
            self.handler_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.handler_slots.release()


def main() -> None:
    if HOST not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The unauthenticated model worker must bind to loopback.")
    if not MODEL_PATH.joinpath("model.safetensors").is_file():
        raise SystemExit("Model weights are not installed.")
    ENGINE.load()
    server = ModelHTTPServer((HOST, PORT), Handler)
    def stop_server(*_):
        ENGINE.scheduler.stop_accepting()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(f"Qwen 服务已启动：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
