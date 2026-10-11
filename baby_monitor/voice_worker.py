from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx


def _emit(event_type: str, **payload) -> None:
    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def _normalized_phrase(value: str) -> str:
    return "".join(str(value).strip().lower().split())


def _model_file(model_dir: Path, component: str) -> Path:
    int8 = sorted(model_dir.glob(f"*{component}*int8.onnx"))
    regular = sorted(model_dir.glob(f"*{component}*.onnx"))
    choices = int8 or regular
    if not choices:
        raise RuntimeError(f"语音模型缺少 {component} 文件")
    return choices[0]


def _create_asr(model_dir: Path):
    model = model_dir / "model.int8.onnx"
    if not model.is_file():
        model = model_dir / "model.onnx"
    return sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(model),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=2,
        sample_rate=16000,
        provider="cpu",
    )


def _create_vad(model: Path, command_window: float):
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(model)
    config.silero_vad.threshold = 0.45
    config.silero_vad.min_speech_duration = 0.18
    config.silero_vad.min_silence_duration = 0.55
    config.silero_vad.max_speech_duration = command_window
    config.sample_rate = 16000
    config.num_threads = 1
    return sherpa_onnx.VoiceActivityDetector(
        config,
        buffer_size_in_seconds=max(10, int(command_window) + 2),
    )


def _decode_segment(recognizer, samples) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, np.asarray(samples, dtype=np.float32))
    recognizer.decode_stream(stream)
    return str(stream.result.text).strip()


def _run(args: argparse.Namespace) -> int:
    accepted_wake_words = tuple(dict.fromkeys((args.wake_word, *args.wake_word_alias)))
    normalized_wake_words = {_normalized_phrase(item) for item in accepted_wake_words}
    spotter = sherpa_onnx.KeywordSpotter(
        tokens=str(args.model_dir / "tokens.txt"),
        encoder=str(_model_file(args.model_dir, "encoder")),
        decoder=str(_model_file(args.model_dir, "decoder")),
        joiner=str(_model_file(args.model_dir, "joiner")),
        num_threads=2,
        keywords_file=str(args.keywords_file),
        provider="cpu",
        keywords_score=1.5,
        keywords_threshold=0.28,
    )
    recognizer = _create_asr(args.asr_model_dir)
    vad = _create_vad(args.vad_model, args.command_window)
    vad_window = int(vad.config.silero_vad.window_size)
    recorder = subprocess.Popen(
        [
            "/usr/bin/arecord",
            "-q",
            "-D",
            args.device,
            "-f",
            "S16_LE",
            "-r",
            "16000",
            "-c",
            "1",
            "-t",
            "raw",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if recorder.stdout is None:
        raise RuntimeError("无法读取麦克风音频")
    time.sleep(0.25)
    if recorder.poll() is not None:
        detail = recorder.stderr.read().decode("utf-8", errors="replace").strip()
        _emit("microphone_error", error=detail or f"无法打开麦克风 {args.device}")
        return 2

    stream = spotter.create_stream()
    listening_until = 0.0
    vad_pending = np.empty(0, dtype=np.float32)
    _emit(
        "ready",
        device=args.device,
        wake_word=args.wake_word,
        wake_words=list(accepted_wake_words),
        asr_model=args.asr_model_dir.name,
    )

    def finish_available_segment() -> bool:
        nonlocal listening_until, vad_pending
        if vad.empty():
            return False
        segment = np.asarray(vad.front.samples, dtype=np.float32)
        vad.pop()
        _emit("transcribing")
        text = _decode_segment(recognizer, segment)
        vad.reset()
        vad_pending = np.empty(0, dtype=np.float32)
        if text:
            if _normalized_phrase(text) in normalized_wake_words:
                listening_until = time.monotonic() + args.command_window
                _emit("wake", text=text)
                return True
            listening_until = 0.0
            _emit("command", text=text, source="offline_asr")
        else:
            listening_until = 0.0
            _emit("timeout")
        return True

    try:
        while True:
            chunk = recorder.stdout.read(3200)
            if not chunk:
                detail = recorder.stderr.read().decode("utf-8", errors="replace").strip()
                _emit("microphone_error", error=detail or "麦克风音频流已中断")
                return 2
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            now = time.monotonic()
            if listening_until:
                vad_pending = np.concatenate((vad_pending, samples))
                while vad_pending.size >= vad_window:
                    vad.accept_waveform(vad_pending[:vad_window])
                    vad_pending = vad_pending[vad_window:]
                    if finish_available_segment():
                        break
                if listening_until and now > listening_until:
                    vad.flush()
                    if not finish_available_segment():
                        vad.reset()
                        vad_pending = np.empty(0, dtype=np.float32)
                        listening_until = 0.0
                        _emit("timeout")
                continue

            stream.accept_waveform(16000, samples)
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
                result = str(spotter.get_result(stream)).strip()
                if not result:
                    continue
                spotter.reset_stream(stream)
                if result not in accepted_wake_words:
                    continue
                vad.reset()
                # The wake word and the first command syllable can share the same
                # 100 ms recorder chunk (for example, "八戒小点声音").  Reuse that
                # chunk as command audio so switching from KWS to ASR does not
                # discard the beginning of the command.  A lone wake-word tail is
                # shorter than VAD's minimum speech duration and is ignored when
                # the speaker pauses before giving a command.
                vad_pending = samples.copy()
                listening_until = time.monotonic() + args.command_window
                _emit("wake", text=result)
    finally:
        if recorder.poll() is None:
            recorder.terminate()
            try:
                recorder.wait(timeout=2)
            except subprocess.TimeoutExpired:
                recorder.kill()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline music voice command worker")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--keywords-file", type=Path, required=True)
    parser.add_argument("--asr-model-dir", type=Path, required=True)
    parser.add_argument("--vad-model", type=Path, required=True)
    parser.add_argument("--device", default="plughw:0,0")
    parser.add_argument("--wake-word", default="八戒")
    parser.add_argument("--wake-word-alias", action="append", default=[])
    parser.add_argument("--command-window", type=float, default=6.0)
    args = parser.parse_args()
    if not args.model_dir.is_dir():
        parser.error("voice model directory does not exist")
    if not args.keywords_file.is_file():
        parser.error("voice keywords file does not exist")
    if not args.asr_model_dir.is_dir():
        parser.error("ASR model directory does not exist")
    if not (args.asr_model_dir / "tokens.txt").is_file():
        parser.error("ASR tokens file does not exist")
    if not (
        (args.asr_model_dir / "model.int8.onnx").is_file()
        or (args.asr_model_dir / "model.onnx").is_file()
    ):
        parser.error("ASR model file does not exist")
    if not args.vad_model.is_file():
        parser.error("VAD model does not exist")
    args.command_window = max(3.0, min(10.0, args.command_window))
    return args


def main() -> int:
    try:
        return _run(_arguments())
    except Exception as exc:
        print("Voice processing failed", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
