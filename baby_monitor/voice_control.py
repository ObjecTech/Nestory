from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    from pypinyin import lazy_pinyin
except ImportError:  # Keeps the monitor usable while the optional helper is installed.
    lazy_pinyin = None


COMMAND_EXAMPLES = (
    "播放儿歌",
    "来点儿歌",
    "播放古诗词",
    "播放经典儿歌",
    "播放英文歌曲",
    "播放法语儿歌",
    "播放钢琴曲",
    "暂停音乐",
    "继续播放",
    "上一首",
    "下一首",
    "停止播放",
    "关闭音乐",
    "安静",
    "声音大一点",
    "声音小一点",
    "提高音量",
    "降低音量",
    "音量调到30",
)

VOLUME_UP_PHRASES = (
    "声音大一点",
    "音量大一点",
    "调大音量",
    "调高音量",
    "提高音量",
    "提升音量",
    "增加音量",
    "增大音量",
    "加大音量",
    "开大音量",
    "音量调大",
    "音量调高",
    "音量提高",
    "音量提升",
    "音量增加",
    "音量增大",
    "音量高一点",
    "音量高点",
    "调大声音",
    "调高声音",
    "提高声音",
    "加大声音",
    "开大声音",
    "声音调大",
    "声音调高",
    "声音提高",
    "声音增大",
    "声音响一点",
    "声音响点",
    "大点声音",
    "声音大点",
    "大声点",
    "响一点",
    "响点",
    "再大点",
    "再响点",
)

VOLUME_DOWN_PHRASES = (
    "声音小一点",
    "音量小一点",
    "调小音量",
    "调低音量",
    "降低音量",
    "减低音量",
    "减小音量",
    "减少音量",
    "音量调小",
    "音量调低",
    "音量降低",
    "音量减低",
    "音量减小",
    "音量减少",
    "音量低一点",
    "音量低点",
    "调小声音",
    "调低声音",
    "降低声音",
    "减小声音",
    "声音调小",
    "声音调低",
    "声音降低",
    "声音减小",
    "声音轻一点",
    "声音轻点",
    "小点声音",
    "声音小点",
    "小声点",
    "轻一点",
    "轻点",
    "再小点",
    "再低点",
    "再轻点",
)

RELATIVE_VOLUME_STEP = 10

CATEGORY_ALIASES = {
    "古诗词": ("古诗词", "古诗", "诗词", "唐诗"),
    "经典儿歌": (
        "经典儿歌",
        "中文儿歌",
        "儿歌",
        "童谣",
        "宝宝音乐",
        "音乐",
        "宝宝听的",
        "唱歌",
        "唱首歌",
        "唱一首歌",
        "唱个歌",
        "唱支歌",
    ),
    "英文歌曲": ("英文歌曲", "英文歌", "英语歌曲", "英语歌", "英文儿歌"),
    "法语儿歌": (
        "法语儿歌",
        "法文儿歌",
        "法国儿歌",
        "法语歌曲",
        "法文歌曲",
        "法语歌",
        "法文歌",
    ),
    "钢琴曲": ("钢琴曲", "钢琴音乐", "钢琴", "轻音乐", "安静一点的音乐"),
}

CATEGORY_FUZZY_PHRASES = {
    "古诗词": ("播放古诗词", "来点古诗", "背一首唐诗", "听古诗"),
    "经典儿歌": ("播放儿歌", "来点儿歌", "放首儿歌", "听儿歌", "播放童谣"),
    "英文歌曲": ("播放英文歌", "来首英文歌", "放英文儿歌", "听英语歌"),
    "法语儿歌": ("播放法语儿歌", "来首法语歌", "放法文儿歌", "听法语歌"),
    "钢琴曲": ("播放钢琴曲", "来点钢琴曲", "听轻音乐", "放安静一点的音乐"),
}


def _clock_minutes(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not match:
        raise ValueError(f"语音监听时间格式不正确：{value}")
    hour, minute = (int(item) for item in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError(f"语音监听时间超出范围：{value}")
    return hour * 60 + minute


def is_within_listen_schedule(now: datetime, start: str, end: str) -> bool:
    """Return whether a local wall-clock time is inside the daily schedule."""
    current = now.hour * 60 + now.minute
    start_minutes = _clock_minutes(start)
    end_minutes = _clock_minutes(end)
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes


def _normalize_phrase(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;\-—_]+", "", str(value)).strip().lower()


def _phonetic_match(value: str, phrases: tuple[str, ...], threshold: float = 0.86) -> bool:
    if lazy_pinyin is None:
        return False
    value_tokens = lazy_pinyin(value)
    for phrase in phrases:
        phrase_tokens = lazy_pinyin(phrase)
        minimum = max(1, len(phrase_tokens) - 1)
        maximum = min(len(value_tokens), len(phrase_tokens) + 2)
        for length in range(minimum, maximum + 1):
            target = "".join(phrase_tokens)
            for start in range(0, len(value_tokens) - length + 1):
                candidate = "".join(value_tokens[start : start + length])
                if SequenceMatcher(None, candidate, target).ratio() >= threshold:
                    return True
    return False


def _chinese_number(value: str) -> int | None:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def parse_voice_command(
    phrase: str,
    categories: list[str],
    wake_word: str = "八戒",
    require_wake: bool = False,
) -> dict[str, Any]:
    text = _normalize_phrase(phrase)
    wake = _normalize_phrase(wake_word)
    has_wake = bool(wake and text.startswith(wake))
    if has_wake:
        while text.startswith(wake):
            text = text[len(wake):]
    elif require_wake:
        raise ValueError(f"请先说唤醒词“{wake_word}”")
    if not text:
        return {"type": "wake", "value": wake_word}

    volume_prefixes = (
        r"(?:把)?(?:音量|声音)(?:给我)?(?:调大|调小|调节|调整|调|设置|设定|设|开|改)?(?:到|成|为)?(?:百分之)?",
        r"(?:调大|调小|调节|调整|调|设置|设定|设|开|改)(?:一下)?(?:音量|声音)(?:到|成|为)?(?:百分之)?",
    )
    for prefix in volume_prefixes:
        number_match = re.search(prefix + r"([0-9]{1,2})(?:%|％)?", text)
        if number_match:
            return {"type": "volume", "value": min(60, int(number_match.group(1)))}
        chinese_match = re.search(
            prefix + r"([零一二两三四五六七八九十]{1,3})", text
        )
        if chinese_match:
            number = _chinese_number(chinese_match.group(1))
            if number is not None:
                return {"type": "volume", "value": min(60, number)}

    # "安静" is an unconditional stop keyword.  Accept surrounding ASR noise
    # such as "谢安静" instead of requiring an exact phrase match.
    if "安静" in text:
        return {"type": "action", "value": "stop"}

    if any(item in text for item in VOLUME_UP_PHRASES):
        return {"type": "volume_delta", "value": RELATIVE_VOLUME_STEP}
    if any(item in text for item in VOLUME_DOWN_PHRASES):
        return {"type": "volume_delta", "value": -RELATIVE_VOLUME_STEP}
    if text in {"小声一点"}:
        return {"type": "volume_delta", "value": -RELATIVE_VOLUME_STEP}

    if text in {"闭嘴", "够了"}:
        return {"type": "action", "value": "stop"}

    actions = (
        ((
            "停止",
            "关闭音乐",
            "关掉音乐",
            "把音乐关了",
            "关闭声音",
            "关掉声音",
            "把声音关掉",
            "别放了",
            "别播了",
            "不要放了",
            "不要播了",
            "不要播放了",
            "别唱了",
            "不要唱了",
            "不听了",
        ), "stop"),
        (("暂停", "停一下", "先停一下", "等一下", "先等一下", "先别放", "先别播"), "pause"),
        (("继续", "恢复播放", "接着放", "接着播放"), "resume"),
        (("下一首", "下一个", "换一首", "换首歌"), "next"),
        (("上一首", "上一个", "前一首"), "previous"),
    )
    for phrases, action in actions:
        if any(item in text for item in phrases):
            return {"type": "action", "value": action}

    play_words = ("播放", "放", "来", "听", "唱", "讲", "背")
    available = set(categories)
    # Prefer the most specific alias globally.  For example, "英文儿歌"
    # must win over the generic substring "儿歌" regardless of dict order.
    category_aliases = sorted(
        (
            (alias, canonical)
            for canonical, aliases in CATEGORY_ALIASES.items()
            if canonical in available
            for alias in aliases
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, canonical in category_aliases:
        if alias in text:
            return {"type": "category", "value": canonical}
    if any(word in text for word in play_words):
        for category in sorted(available, key=len, reverse=True):
            if category in text:
                return {"type": "category", "value": category}
    for canonical, phrases in CATEGORY_FUZZY_PHRASES.items():
        if canonical in available and _phonetic_match(text, phrases):
            return {"type": "category", "value": canonical}
    raise ValueError("未识别出可执行的音乐指令")


class VoiceControl:
    """Supervises the offline KWS worker and only exposes player-safe intents."""

    def __init__(
        self,
        player,
        settings_path: Path,
        worker_python: Path,
        model_dir: Path,
        keywords_file: Path,
        audio_device: str,
        wake_word: str = "八戒",
        wake_word_aliases: tuple[str, ...] | None = None,
        default_enabled: bool = False,
        asr_model_dir: Path | None = None,
        vad_model: Path | None = None,
        schedule_start: str = "07:00",
        schedule_end: str = "23:00",
        timezone_name: str = "Asia/Shanghai",
        feedback_dir: Path | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.player = player
        self.settings_path = settings_path
        self.worker_python = worker_python
        self.model_dir = model_dir
        self.keywords_file = keywords_file
        self.audio_device = audio_device
        self.wake_word = wake_word
        aliases = wake_word_aliases or (f"{wake_word}{wake_word}",)
        self.wake_words = tuple(
            dict.fromkeys(item.strip() for item in (wake_word, *aliases) if item.strip())
        )
        self.asr_model_dir = asr_model_dir or (
            model_dir.parent / "sherpa-onnx-paraformer-zh-small-2024-03-09"
        )
        self.vad_model = vad_model or (model_dir.parent / "silero_vad.onnx")
        _clock_minutes(schedule_start)
        _clock_minutes(schedule_end)
        self.schedule_start = schedule_start
        self.schedule_end = schedule_end
        self.timezone_name = timezone_name
        self.feedback_dir = feedback_dir or (
            Path(__file__).resolve().parents[1] / "assets" / "voice_feedback"
        )
        self._timezone = ZoneInfo(timezone_name)
        self._now_factory = now_factory or (lambda: datetime.now(self._timezone))
        self._on_event = on_event
        self._popen_factory = popen_factory
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._schedule_thread: threading.Thread | None = None
        self._worker: subprocess.Popen | None = None
        self._enabled = self._read_enabled(default_enabled)
        self._state = (
            "disabled"
            if not self._enabled
            else "starting" if self._schedule_active() else "outside_schedule"
        )
        self._microphone_ready = False
        self._error: str | None = None
        self._last_command: str | None = None
        self._last_result: str | None = None
        self._last_heard_at: float | None = None
        self._last_feedback: str | None = None
        self._feedback_error: str | None = None

    def _read_enabled(self, default: bool) -> bool:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return bool(payload.get("enabled", default))
        except (OSError, ValueError, TypeError):
            return default

    def _save_enabled(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"enabled": self._enabled}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.settings_path)

    def _kws_ready(self) -> bool:
        required = ("encoder", "decoder", "joiner")
        return (
            self.model_dir.is_dir()
            and all(any(self.model_dir.glob(f"*{name}*.onnx")) for name in required)
            and (self.model_dir / "tokens.txt").is_file()
            and self.keywords_file.is_file()
        )

    def _asr_ready(self) -> bool:
        return (
            self.asr_model_dir.is_dir()
            and (self.asr_model_dir / "model.int8.onnx").is_file()
            and (self.asr_model_dir / "tokens.txt").is_file()
            and self.vad_model.is_file()
        )

    def _model_ready(self) -> bool:
        return self._kws_ready() and self._asr_ready()

    def _schedule_active(self) -> bool:
        return is_within_listen_schedule(
            self._now_factory(),
            self.schedule_start,
            self.schedule_end,
        )

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._supervise,
                name="voice-control-supervisor",
                daemon=True,
            )
            self._thread.start()
            self._schedule_thread = threading.Thread(
                target=self._supervise_schedule,
                name="voice-control-schedule",
                daemon=True,
            )
            self._schedule_thread.start()
        self._wake.set()

    def close(self) -> None:
        self._closing.set()
        self._wake.set()
        self._terminate_worker()
        if self._thread:
            self._thread.join(timeout=5)
        if self._schedule_thread:
            self._schedule_thread.join(timeout=5)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("语音控制开关格式不正确")
        with self._lock:
            self._enabled = enabled
            self._state = (
                "starting"
                if enabled and self._schedule_active()
                else "outside_schedule" if enabled else "disabled"
            )
            self._microphone_ready = False
            self._error = None
            self._save_enabled()
        if not enabled:
            self._terminate_worker()
        self._wake.set()
        self._publish()
        return self.status()

    def status(self) -> dict[str, Any]:
        model_ready = self._model_ready()
        runtime_ready = self.worker_python.is_file() and model_ready
        with self._lock:
            return {
                "enabled": self._enabled,
                "state": self._state,
                "runtime_ready": runtime_ready,
                "model_ready": model_ready,
                "asr_ready": self._asr_ready(),
                "recognition_mode": "wake_then_free_speech",
                "microphone_ready": self._microphone_ready,
                "wake_word": self.wake_word,
                "wake_words": list(self.wake_words),
                "audio_device": self.audio_device,
                "schedule_start": self.schedule_start,
                "schedule_end": self.schedule_end,
                "schedule_timezone": self.timezone_name,
                "within_schedule": self._schedule_active(),
                "last_command": self._last_command,
                "last_result": self._last_result,
                "last_heard_at": self._last_heard_at,
                "last_feedback": self._last_feedback,
                "feedback_ready": (self.feedback_dir / "repeat.mp3").is_file(),
                "feedback_error": self._feedback_error,
                "error": self._error,
                "command_examples": list(COMMAND_EXAMPLES),
            }

    def execute_phrase(self, phrase: str, require_wake: bool = False) -> dict[str, Any]:
        player_status = self.player.status()
        categories = [item["name"] for item in player_status.get("categories", [])]
        intent = parse_voice_command(
            phrase,
            categories,
            wake_word=self.wake_word,
            require_wake=require_wake,
        )
        kind = intent["type"]
        value = intent["value"]
        if kind == "wake":
            return {"intent": intent, "message": "已唤醒，等待音乐指令"}
        if kind == "category":
            result = self.player.play_category(value)
            message = f"开始随机播放{value}"
        elif kind == "action":
            result = self.player.control(value)
            labels = {"stop": "已停止播放", "pause": "已暂停", "resume": "继续播放",
                      "next": "播放下一首", "previous": "播放上一首"}
            message = labels[value]
        elif kind == "volume_delta":
            target = max(0, min(60, int(player_status.get("volume", 25)) + int(value)))
            result = self.player.set_volume(target)
            message = f"音量已调到{target}%"
        elif kind == "volume":
            result = self.player.set_volume(int(value))
            message = f"音量已调到{int(value)}%"
        else:
            raise ValueError("不支持的语音指令")
        return {"intent": intent, "message": message, "player": result}

    def _publish(self) -> None:
        if self._on_event:
            try:
                self._on_event(self.status())
            except Exception:
                pass

    def _set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._error = error
        self._publish()

    def _play_feedback(self, key: str) -> None:
        replies = {
            "repeat": ("repeat.mp3", "请再说一遍"),
        }
        filename, reply = replies[key]
        try:
            self.player.play_feedback(self.feedback_dir / filename, reply)
            with self._lock:
                self._last_feedback = reply
                self._feedback_error = None
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            with self._lock:
                self._feedback_error = str(exc)
        self._publish()

    def _handle_event(self, event: dict[str, Any]) -> None:
        if not self._schedule_active():
            with self._lock:
                self._microphone_ready = False
            self._set_state("outside_schedule")
            return
        event_type = str(event.get("type", ""))
        if event_type == "ready":
            with self._lock:
                self._microphone_ready = True
            self._set_state("listening")
        elif event_type == "wake":
            with self._lock:
                self._last_heard_at = time.time()
            self._set_state("waiting_command")
        elif event_type == "transcribing":
            self._set_state("transcribing")
        elif event_type == "timeout":
            self._set_state("listening")
            self._play_feedback("repeat")
        elif event_type == "microphone_error":
            with self._lock:
                self._microphone_ready = False
            self._set_state("waiting_microphone", str(event.get("error", "麦克风不可用")))
        elif event_type == "command":
            phrase = str(event.get("text", ""))
            try:
                result = self.execute_phrase(phrase)
                with self._lock:
                    self._last_command = phrase
                    self._last_result = result["message"]
                    self._last_heard_at = time.time()
                self._set_state("executed")
            except (RuntimeError, ValueError) as exc:
                with self._lock:
                    self._last_command = phrase
                    self._last_result = None
                    self._last_heard_at = time.time()
                self._set_state("command_error", str(exc))
                self._play_feedback("repeat")

    def _supervise(self) -> None:
        while not self._closing.is_set():
            with self._lock:
                enabled = self._enabled
            if not enabled:
                self._wake.wait(2)
                self._wake.clear()
                continue
            if not self._schedule_active():
                with self._lock:
                    self._microphone_ready = False
                self._set_state("outside_schedule")
                self._wake.wait(30)
                self._wake.clear()
                continue
            if not self.worker_python.is_file() or not self._model_ready():
                self._set_state("runtime_missing", "离线语音运行环境或模型尚未准备完成")
                self._wake.wait(10)
                self._wake.clear()
                continue
            self._run_worker_once()
            if not self._closing.is_set():
                self._wake.wait(5)
                self._wake.clear()

    def _supervise_schedule(self) -> None:
        previous: bool | None = None
        while not self._closing.is_set():
            active = self._schedule_active()
            with self._lock:
                enabled = self._enabled
            if previous is None or active != previous:
                if enabled and not active:
                    with self._lock:
                        self._microphone_ready = False
                    self._set_state("outside_schedule")
                    self._terminate_worker()
                    self._wake.set()
                elif enabled and active:
                    self._set_state("starting")
                    self._wake.set()
            previous = active
            self._closing.wait(5)

    def _run_worker_once(self) -> None:
        args = [
            str(self.worker_python),
            "-m",
            "baby_monitor.voice_worker",
            "--model-dir",
            str(self.model_dir),
            "--keywords-file",
            str(self.keywords_file),
            "--asr-model-dir",
            str(self.asr_model_dir),
            "--vad-model",
            str(self.vad_model),
            "--device",
            self.audio_device,
            "--wake-word",
            self.wake_word,
        ]
        for alias in self.wake_words[1:]:
            args.extend(("--wake-word-alias", alias))
        try:
            process = self._popen_factory(
                args,
                cwd=str(self.worker_python.parents[2]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._set_state("error", f"无法启动语音服务：{exc}")
            return
        with self._lock:
            self._worker = process
            self._state = "starting"
            self._error = None
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    if self._closing.is_set():
                        break
                    try:
                        self._handle_event(json.loads(line))
                    except (json.JSONDecodeError, TypeError):
                        continue
            return_code = process.wait()
            detail = process.stderr.read().strip() if process.stderr else ""
            with self._lock:
                still_enabled = self._enabled
                current_state = self._state
            if (
                still_enabled
                and not self._closing.is_set()
                and return_code != 0
                and current_state not in {"waiting_microphone", "outside_schedule"}
            ):
                self._set_state("error", detail[-300:] or f"语音服务异常退出（{return_code}）")
        finally:
            with self._lock:
                if self._worker is process:
                    self._worker = None

    def _terminate_worker(self) -> None:
        with self._lock:
            process = self._worker
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
