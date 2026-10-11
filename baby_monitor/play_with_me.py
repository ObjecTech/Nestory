from __future__ import annotations

import hashlib
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DIRECT_AUDIO_EXTENSIONS = {".flac", ".mp3", ".oga", ".ogg", ".wav"}
CONVERTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".webm",
}
MEDIA_EXTENSIONS = DIRECT_AUDIO_EXTENSIONS | CONVERTED_AUDIO_EXTENSIONS


def _display_title(path: Path) -> str:
    title = path.stem.strip()
    parts = title.split(" - ")
    if len(parts) >= 3 and parts[1].strip().isdigit():
        title = " - ".join(parts[2:]).strip()
    title = re.sub(r"\s*\(Av\d+[^)]*\)\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\d{1,4}[.、]\s*", "", title).strip()
    return title or path.stem


@dataclass(frozen=True)
class MediaTrack:
    category: str
    source: Path
    playback: Path
    title: str
    cache_key: str
    ready: bool
    unplayable: bool


class PlayWithMePlayer:
    """Random category player backed by AirPlay or the local PipeWire sink."""

    def __init__(
        self,
        media_root: Path,
        player_python: Path,
        speaker_host: str,
        speaker_name: str = "Audio output",
        ffmpeg_path: Path = Path("/usr/bin/ffmpeg"),
        default_volume: int = 25,
        playback_backend: str = "airplay",
        bluetooth_speaker_mac: str = "",
        start_converter: bool = True,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.media_root = media_root
        self.cache_dir = media_root / ".audio-cache"
        self.player_python = player_python
        self.speaker_host = speaker_host
        self.speaker_name = speaker_name
        self.ffmpeg_path = ffmpeg_path
        self.playback_backend = playback_backend.strip().lower()
        self.bluetooth_speaker_mac = bluetooth_speaker_mac.strip()
        self.ffprobe_path = ffmpeg_path.with_name("ffprobe")
        self._popen_factory = popen_factory
        self._lock = threading.RLock()
        self._command_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._converter_process: subprocess.Popen | None = None
        self._current: MediaTrack | None = None
        self._category: str | None = None
        self._queue: list[MediaTrack] = []
        self._history: list[MediaTrack] = []
        self._state = "stopped"
        self._error: str | None = None
        self._prepare_error: str | None = None
        self._volume = max(0, min(60, int(default_volume)))
        self._started_ts: float | None = None
        self._started_monotonic: float | None = None
        self._position_offset = 0.0
        self._duration_seconds = 0.0
        self._duration_cache: dict[tuple[str, int, int], float] = {}
        self._closing = threading.Event()
        self._conversion_wakeup = threading.Event()
        self._failed_until: dict[str, float] = {}
        self._converter_thread: threading.Thread | None = None
        if start_converter:
            self._converter_thread = threading.Thread(
                target=self._conversion_loop,
                name="play-media-converter",
                daemon=True,
            )
            self._converter_thread.start()

    def _cache_key(self, category: str, path: Path) -> str:
        stat = path.stat()
        relative = path.relative_to(self.media_root).as_posix()
        value = f"{category}\0{relative}\0{stat.st_size}\0{stat.st_mtime_ns}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def _scan(self) -> dict[str, list[MediaTrack]]:
        library: dict[str, list[MediaTrack]] = {}
        if not self.media_root.is_dir():
            return library
        for category_dir in sorted(
            (
                item
                for item in self.media_root.iterdir()
                if item.is_dir() and not item.name.startswith(".")
            ),
            key=lambda item: item.name,
        ):
            tracks: list[MediaTrack] = []
            for source in sorted(category_dir.rglob("*"), key=lambda item: item.name):
                if not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                relative = source.relative_to(category_dir)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                cache_key = self._cache_key(category_dir.name, source)
                unplayable_marker = self.cache_dir / f"{cache_key}.unplayable"
                unplayable = unplayable_marker.is_file()
                if source.suffix.lower() in DIRECT_AUDIO_EXTENSIONS:
                    playback = source
                    ready = source.stat().st_size > 0
                    unplayable = False
                else:
                    playback = self.cache_dir / f"{cache_key}.mp3"
                    ready = (
                        not unplayable
                        and playback.is_file()
                        and playback.stat().st_size > 0
                    )
                tracks.append(
                    MediaTrack(
                        category=category_dir.name,
                        source=source,
                        playback=playback,
                        title=_display_title(source),
                        cache_key=cache_key,
                        ready=ready,
                        unplayable=unplayable,
                    )
                )
            # Keep an intentionally-created category visible before its first
            # audio upload; the web UI disables playback until a track is ready.
            library[category_dir.name] = tracks
        return library

    def _category_status(self, library: dict[str, list[MediaTrack]]) -> list[dict[str, Any]]:
        result = []
        for name, tracks in library.items():
            ready = sum(1 for track in tracks if track.ready)
            unplayable = sum(1 for track in tracks if track.unplayable)
            result.append(
                {
                    "name": name,
                    "count": len(tracks),
                    "ready_count": ready,
                    "preparing_count": len(tracks) - ready - unplayable,
                    "unplayable_count": unplayable,
                }
            )
        return result

    def status(self) -> dict[str, Any]:
        library = self._scan()
        with self._lock:
            state = self._state
            if state == "connecting" and self._started_ts and time.time() - self._started_ts > 3:
                state = "playing"
                self._state = state
            position = self._position_locked()
            return {
                "state": state,
                "category": self._category,
                "current_title": self._current.title if self._current else None,
                "position_seconds": round(position, 1),
                "duration_seconds": round(self._duration_seconds, 1),
                "seekable": bool(self._current and self._duration_seconds > 1),
                "volume": self._volume,
                "speaker": {
                    "name": self.speaker_name,
                    "address": self.speaker_host,
                    "backend": self.playback_backend,
                },
                "queue_remaining": len(self._queue),
                "can_previous": bool(self._history),
                "error": self._error,
                "prepare_error": self._prepare_error,
                "categories": self._category_status(library),
            }

    def _ready_tracks(self, category: str) -> list[MediaTrack]:
        library = self._scan()
        if category not in library:
            raise ValueError("该类目不存在或没有可识别的音频")
        tracks = [track for track in library[category] if track.ready]
        if not tracks:
            if library[category]:
                raise RuntimeError("该类目正在准备播放缓存，请稍后再试")
            raise ValueError("该类目暂时没有音频")
        return tracks

    def _refill_queue_locked(self, exclude: Path | None = None) -> None:
        if not self._category:
            self._queue = []
            return
        tracks = self._ready_tracks(self._category)
        random.shuffle(tracks)
        if exclude and len(tracks) > 1 and tracks[0].source == exclude:
            tracks.append(tracks.pop(0))
        self._queue = tracks

    def _pop_next_locked(self, exclude: Path | None = None) -> MediaTrack:
        if not self._queue:
            self._refill_queue_locked(exclude)
        if not self._queue:
            raise RuntimeError("该类目暂时没有可播放的音频")
        return self._queue.pop(0)

    def _probe_duration(self, path: Path) -> float:
        duration = 0.0
        key: tuple[str, int, int] | None = None
        try:
            stat = path.stat()
            key = (str(path), stat.st_size, stat.st_mtime_ns)
            cached = self._duration_cache.get(key)
            if cached is not None:
                return cached
            result = subprocess.run(
                [
                    str(self.ffprobe_path),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            duration = float(result.stdout.strip()) if result.returncode == 0 else 0.0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if duration <= 0 and path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as source:
                    duration = source.getnframes() / source.getframerate()
            except (OSError, EOFError, wave.Error, ZeroDivisionError):
                duration = 0.0
        duration = max(0.0, duration)
        if key is not None:
            self._duration_cache[key] = duration
        return duration

    def _position_locked(self) -> float:
        position = self._position_offset
        if (
            self._current
            and self._started_monotonic is not None
            and self._state in {"connecting", "playing"}
        ):
            position += max(0.0, time.monotonic() - self._started_monotonic)
        if self._duration_seconds > 0:
            position = min(position, self._duration_seconds)
        return max(0.0, position)

    def _launch_locked(self, track: MediaTrack, start_seconds: float = 0.0) -> None:
        args = self._worker_args(track, start_seconds)
        try:
            process = self._popen_factory(
                args,
                cwd=str(self.player_python.parents[2]),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(f"无法启动音频播放：{exc}") from exc
        self._process = process
        self._current = track
        self._state = "connecting"
        self._error = None
        self._started_ts = time.time()
        self._started_monotonic = time.monotonic()
        self._position_offset = max(0.0, start_seconds)
        self._duration_seconds = self._probe_duration(track.playback)
        threading.Thread(
            target=self._watch_process,
            args=(process, track),
            name="audio-track-watcher",
            daemon=True,
        ).start()

    def _worker_args(self, track: MediaTrack, start_seconds: float = 0.0) -> list[str]:
        if self.playback_backend == "silent":
            args = [
                sys.executable,
                "-m",
                "baby_monitor.silent_audio_worker",
                "--file",
                str(track.playback),
                "--start-seconds",
                str(max(0.0, start_seconds)),
            ]
        elif self.playback_backend == "pipewire":
            local_python = self.player_python.parents[2] / ".venv/bin/python"
            if not local_python.is_file():
                raise RuntimeError("本地音频播放环境尚未安装")
            args = [
                str(local_python),
                "-m",
                "baby_monitor.system_audio_worker",
                "--file",
                str(track.playback),
                "--volume",
                str(self._volume),
                "--start-seconds",
                str(max(0.0, start_seconds)),
                "--ffmpeg",
                str(self.ffmpeg_path),
                "--bluetooth-mac",
                self.bluetooth_speaker_mac,
            ]
        else:
            if not self.player_python.is_file():
                raise RuntimeError("AirPlay 播放环境尚未安装")
            args = [
                str(self.player_python),
                "-m",
                "baby_monitor.airplay_worker",
                "--host",
                self.speaker_host,
                "--speaker-name",
                self.speaker_name,
                "--file",
                str(track.playback),
                "--title",
                track.title,
                "--category",
                track.category,
                "--volume",
                str(self._volume),
                "--start-seconds",
                str(max(0.0, start_seconds)),
                "--ffmpeg",
                str(self.ffmpeg_path),
            ]
            if track.category == "语音反馈":
                args.extend(("--scan-timeout", "1.2"))
        return args

    def _watch_process(self, process: subprocess.Popen, track: MediaTrack) -> None:
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read()
        return_code = process.wait()
        with self._lock:
            if self._process is not process:
                return
            self._process = None
            if self._closing.is_set():
                return
            if return_code != 0:
                self._position_offset = self._position_locked()
                self._started_monotonic = None
                detail = stderr.decode("utf-8", errors="replace").strip()
                self._state = "error"
                self._error = detail[-300:] or f"音频播放失败（{return_code}）"
                return
            self._history.append(track)
            try:
                next_track = self._pop_next_locked(exclude=track.source)
                self._launch_locked(next_track)
            except (RuntimeError, ValueError) as exc:
                self._current = None
                self._state = "error"
                self._error = str(exc)

    def _take_process_locked(self) -> tuple[subprocess.Popen | None, bool]:
        process = self._process
        was_paused = self._state == "paused"
        self._process = None
        return process, was_paused

    @staticmethod
    def _terminate_process(process: subprocess.Popen | None, was_paused: bool) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            if was_paused:
                os.killpg(process.pid, signal.SIGCONT)
            if process.stdin is not None:
                process.stdin.write(b"STOP\n")
                process.stdin.flush()
            process.wait(timeout=4)
            return
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass

    def play_category(self, category: str) -> dict[str, Any]:
        category = category.strip()
        with self._command_lock:
            tracks = self._ready_tracks(category)
            with self._lock:
                process, was_paused = self._take_process_locked()
            self._terminate_process(process, was_paused)
            random.shuffle(tracks)
            with self._lock:
                self._category = category
                self._queue = tracks
                self._history = []
                self._current = None
                self._state = "stopped"
                self._error = None
                self._launch_locked(self._pop_next_locked())
        return self.status()

    def next(self) -> dict[str, Any]:
        with self._command_lock:
            with self._lock:
                if not self._category:
                    raise RuntimeError("请先选择一个类目")
                process, was_paused = self._take_process_locked()
                previous = self._current
            self._terminate_process(process, was_paused)
            with self._lock:
                if previous:
                    self._history.append(previous)
                next_track = self._pop_next_locked(
                    exclude=previous.source if previous else None
                )
                self._launch_locked(next_track)
        return self.status()

    def previous(self) -> dict[str, Any]:
        with self._command_lock:
            with self._lock:
                if not self._history:
                    raise RuntimeError("当前还没有上一首")
                process, was_paused = self._take_process_locked()
                current = self._current
                target = self._history.pop()
            self._terminate_process(process, was_paused)
            with self._lock:
                if current:
                    self._queue.insert(0, current)
                self._launch_locked(target)
        return self.status()

    def pause(self) -> dict[str, Any]:
        with self._command_lock, self._lock:
            if not self._process or self._process.poll() is not None:
                raise RuntimeError("当前没有正在播放的内容")
            if self._state != "paused":
                self._position_offset = self._position_locked()
                os.killpg(self._process.pid, signal.SIGSTOP)
                self._state = "paused"
                self._started_monotonic = None
        return self.status()

    def resume(self) -> dict[str, Any]:
        with self._command_lock, self._lock:
            if self._state != "paused" or not self._current:
                raise RuntimeError("当前没有可继续播放的内容")
            if self._process and self._process.poll() is None:
                os.killpg(self._process.pid, signal.SIGCONT)
                self._state = "playing"
                self._started_monotonic = time.monotonic()
                self._send_worker_command_locked(f"VOLUME {self._volume}")
            else:
                self._launch_locked(self._current, start_seconds=self._position_offset)
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._command_lock:
            with self._lock:
                process, was_paused = self._take_process_locked()
            self._terminate_process(process, was_paused)
            with self._lock:
                self._current = None
                self._queue = []
                self._history = []
                self._state = "stopped"
                self._error = None
                self._started_ts = None
                self._started_monotonic = None
                self._position_offset = 0.0
                self._duration_seconds = 0.0
        return self.status()

    def seek(self, position_seconds: float) -> dict[str, Any]:
        try:
            requested = float(position_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("播放位置格式不正确") from exc
        with self._command_lock:
            with self._lock:
                if not self._current or not self._process:
                    raise RuntimeError("当前没有可跳转的音频")
                if self._duration_seconds <= 1:
                    raise RuntimeError("当前音频无法获取时长")
                target = max(0.0, min(requested, self._duration_seconds - 0.5))
                track = self._current
                process, was_paused = self._take_process_locked()
            self._terminate_process(process, was_paused)
            with self._lock:
                self._launch_locked(track, start_seconds=target)
        return self.status()

    def _send_worker_command_locked(self, command: str) -> None:
        if not self._process or not self._process.stdin:
            return
        try:
            self._process.stdin.write(f"{command}\n".encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def set_volume(self, volume: int) -> dict[str, Any]:
        volume = int(volume)
        if not 0 <= volume <= 60:
            raise ValueError("音量应在 0 到 60 之间")
        with self._command_lock, self._lock:
            self._volume = volume
            if self._state != "paused":
                self._send_worker_command_locked(f"VOLUME {volume}")
        return self.status()

    def play_feedback(self, feedback_file: Path, title: str) -> None:
        """Play a short spoken reply and then restore the current track."""
        feedback_file = Path(feedback_file)
        if not feedback_file.is_file() or feedback_file.stat().st_size == 0:
            raise RuntimeError("语音反馈文件不存在")
        feedback_track = MediaTrack(
            category="语音反馈",
            source=feedback_file,
            playback=feedback_file,
            title=title,
            cache_key=f"feedback-{feedback_file.stem}",
            ready=True,
            unplayable=False,
        )
        feedback_error: RuntimeError | None = None
        with self._command_lock:
            with self._lock:
                previous_state = self._state
                previous_track = self._current
                previous_position = self._position_locked()
                previous_duration = self._duration_seconds
                process, was_paused = self._take_process_locked()
                self._state = "feedback"
                self._started_monotonic = None
            self._terminate_process(process, was_paused)

            feedback_process: subprocess.Popen | None = None
            try:
                feedback_process = self._popen_factory(
                    self._worker_args(feedback_track),
                    cwd=str(self.player_python.parents[2]),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    return_code = feedback_process.wait(timeout=25)
                except subprocess.TimeoutExpired as exc:
                    self._terminate_process(feedback_process, False)
                    raise RuntimeError("语音反馈播放超时") from exc
                detail = (
                    feedback_process.stderr.read().decode("utf-8", errors="replace").strip()
                    if feedback_process.stderr is not None
                    else ""
                )
                if return_code != 0:
                    raise RuntimeError(detail[-240:] or "语音反馈播放失败")
            except OSError as exc:
                feedback_error = RuntimeError(f"无法播放语音反馈：{exc}")
            except RuntimeError as exc:
                feedback_error = exc
            finally:
                with self._lock:
                    if previous_track and previous_state in {"connecting", "playing"}:
                        self._launch_locked(previous_track, start_seconds=previous_position)
                    elif previous_track and previous_state == "paused":
                        self._process = None
                        self._current = previous_track
                        self._state = "paused"
                        self._position_offset = previous_position
                        self._duration_seconds = previous_duration
                        self._started_ts = None
                        self._started_monotonic = None
                    else:
                        self._process = None
                        self._current = previous_track
                        self._state = previous_state if previous_state != "feedback" else "stopped"
                        self._started_ts = None
                        self._started_monotonic = None
            if feedback_error is not None:
                raise feedback_error

    def control(self, action: str) -> dict[str, Any]:
        actions = {
            "next": self.next,
            "pause": self.pause,
            "previous": self.previous,
            "resume": self.resume,
            "stop": self.stop,
        }
        if action not in actions:
            raise ValueError("不支持的播放操作")
        return actions[action]()

    def _pending_tracks(self) -> list[MediaTrack]:
        now = time.time()
        return [
            track
            for tracks in self._scan().values()
            for track in tracks
            if (
                not track.ready
                and not track.unplayable
                and self._failed_until.get(track.cache_key, 0) <= now
            )
        ]

    def _convert_track(self, track: MediaTrack) -> bool:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_dir / f".{track.cache_key}.part.mp3"
        unplayable_marker = self.cache_dir / f"{track.cache_key}.unplayable"
        args = [
            "/usr/bin/nice",
            "-n",
            "15",
            str(self.ffmpeg_path),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(track.source),
            "-map",
            "0:a:0",
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(temporary),
        ]
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            with self._lock:
                self._converter_process = process
            stderr = process.communicate()[1]
            with self._lock:
                self._converter_process = None
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                self._prepare_error = f"{track.title}：{detail[-240:]}"
                unplayable_marker.write_text(
                    self._prepare_error,
                    encoding="utf-8",
                )
                self._failed_until.pop(track.cache_key, None)
                return False
            temporary.replace(track.playback)
            if unplayable_marker.exists():
                unplayable_marker.unlink()
            self._prepare_error = None
            self._failed_until.pop(track.cache_key, None)
            return True
        except OSError as exc:
            self._prepare_error = f"无法准备 {track.title}：{exc}"
            self._failed_until[track.cache_key] = time.time() + 300
            return False
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def prepare_all(self) -> dict[str, int]:
        prepared = 0
        failed = 0
        for track in self._pending_tracks():
            if self._convert_track(track):
                prepared += 1
            else:
                failed += 1
        remaining = sum(
            1
            for tracks in self._scan().values()
            for track in tracks
            if not track.ready and not track.unplayable
        )
        return {"prepared": prepared, "failed": failed, "remaining": remaining}

    def _conversion_loop(self) -> None:
        while not self._closing.is_set():
            pending = self._pending_tracks()
            if pending:
                for track in pending:
                    if self._closing.is_set():
                        return
                    self._convert_track(track)
                continue
            self._prepare_error = None
            self._conversion_wakeup.wait(10)
            self._conversion_wakeup.clear()

    def close(self) -> None:
        self._closing.set()
        self._conversion_wakeup.set()
        with self._lock:
            converter = self._converter_process
        if converter and converter.poll() is None:
            converter.terminate()
        self.stop()
        if self._converter_thread:
            self._converter_thread.join(timeout=3)
