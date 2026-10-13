from __future__ import annotations

import io
import json
import os
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from functools import wraps

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

from .camera import Camera, Frame
from .config import Config
from .database import POSTURE_VALUES, Database
from .inference import InferenceWorker
from .play_with_me import PlayWithMePlayer
from .voice_control import VoiceControl
from .vlm import VLMObserver
from .observation_mode import mode_name, validate_mode


CLASSIFIED_INTERVAL_SECONDS = {
    "sleeping": 20 * 60,
    "awake": 5 * 60,
    "awake_happy": 90,
    "awake_unhappy": 90,
    "crying": 60,
}
CLASSIFIED_DAILY_LIMITS = {
    "sleeping": 48,
    "awake": 96,
    "awake_happy": 100,
    "awake_unhappy": 100,
    "crying": 60,
}
CLASSIFIED_MIN_CONFIDENCE = {
    "sleeping": 0.60,
    "awake": 0.60,
    "awake_happy": 0.30,
    "awake_unhappy": 0.45,
    "crying": 0.80,
}
PHOTO_EVENT_TYPES = (
    "smile",
    "expression_photo",
    "classified_photo",
    "classified_posture_photo",
)
EXPRESSION_LABELS = {
    "calm": "平静",
    "happy": "开心",
    "unhappy": "不开心",
    "crying": "哭泣",
}
EXPRESSION_DAILY_LIMIT = 80


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=30)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        message = {"type": event_type, "ts": time.time(), **payload}
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(message)
            except queue.Full:
                try:
                    channel.get_nowait()
                    channel.put_nowait(message)
                except queue.Empty:
                    pass


def observation_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        lock = getattr(self, "_observation_lock", None)
        if lock is not None:
            with lock:
                return method(self, *args, **kwargs)
        return method(self, *args, **kwargs)
    return locked


class BabyMonitorApp:
    def __init__(self, config: Config):
        self.config = config
        self._observation_lock = threading.RLock()
        self._subject_started_ts = 0.0
        config.ensure_directories()
        self._subject_settings = config.data_dir / "observation_subject.json"
        person_test_mode = config.person_test_mode
        if self._subject_settings.is_file():
            saved = json.loads(self._subject_settings.read_text())
            if type(saved.get("person_test_mode")) is bool:
                person_test_mode = saved["person_test_mode"]
        self.database = Database(config.data_dir / "baby_monitor.db", observation_mode=mode_name(person_test_mode))
        self.camera = Camera(
            config.camera_device,
            config.camera_width,
            config.camera_height,
            config.camera_fps,
        )
        if os.getenv("BMON_DEMO", "false").lower() == "true":
            from .synthetic_camera import SyntheticCamera
            self.camera = SyntheticCamera()
        self.events = EventBus()
        self.player = PlayWithMePlayer(
            media_root=config.play_media_dir,
            player_python=config.airplay_python,
            speaker_host=config.speaker_host,
            speaker_name=config.speaker_name,
            ffmpeg_path=config.ffmpeg_path,
            default_volume=config.play_default_volume,
            playback_backend=config.playback_backend,
            bluetooth_speaker_mac=config.bluetooth_speaker_mac,
        )
        self.voice = VoiceControl(
            player=self.player,
            settings_path=config.data_dir / "voice_control.json",
            worker_python=config.voice_worker_python,
            model_dir=config.voice_model_dir,
            keywords_file=config.voice_keywords_file,
            asr_model_dir=config.voice_asr_model_dir,
            vad_model=config.voice_vad_model,
            audio_device=config.voice_audio_device,
            wake_word=config.voice_wake_word,
            default_enabled=config.voice_default_enabled,
            schedule_start=config.voice_schedule_start,
            schedule_end=config.voice_schedule_end,
            timezone_name=config.voice_timezone,
            on_event=lambda payload: self.events.publish("voice", payload),
        )
        self._classified_lock = threading.Lock()
        self._classified_last_saved: dict[str, float] = {}
        self._classified_last_hash: dict[str, int] = {}
        self.inference = InferenceWorker(
            person_test_mode=person_test_mode, mode_lock=self._observation_lock,
            camera=self.camera,
            database=self.database,
            enabled=config.model_enabled,
            model_name=config.model_name,
            emotion_model_name=config.emotion_model_name,
            face_model_path=config.face_model_path,
            posture_model_path=config.posture_model_path,
            analysis_interval_seconds=config.analysis_interval_seconds,
            sleep_stable_seconds=config.sleep_stable_seconds,
            awake_stable_seconds=config.awake_stable_seconds,
            posture_stable_seconds=config.posture_stable_seconds,
            sleep_motion_threshold=config.sleep_motion_threshold,
            motion_threshold=config.motion_threshold,
            smile_confidence=config.smile_confidence,
            smile_cooldown_seconds=config.smile_cooldown_seconds,
            record_transitions=not (config.vlm_enabled and config.vlm_primary),
            on_transition=self.transition,
            on_smile=self.save_smile,
            on_classification=self.consider_classified_photo,
        )
        self.vlm = VLMObserver(
            person_test_mode=person_test_mode, mode_lock=self._observation_lock,
            camera=self.camera,
            database=self.database,
            enabled=config.vlm_enabled,
            service_url=config.vlm_service_url,
            interval_seconds=config.vlm_interval_seconds,
            sleep_interval_seconds=config.vlm_sleep_interval_seconds,
            motion_interval_seconds=config.vlm_motion_interval_seconds,
            timeout_seconds=config.vlm_timeout_seconds,
            confidence_threshold=config.vlm_confidence_threshold,
            stable_seconds=config.vlm_stable_seconds,
            motion_threshold=config.motion_threshold,
            on_transition=self.transition,
            on_observation=self.publish_vlm_observation,
            on_expression=self.save_expression_photo,
        )

    @observation_locked
    def set_observation_subject(self, enabled: bool) -> dict[str, Any]:
        def persist(value):
            temporary = self._subject_settings.with_suffix(".tmp")
            temporary.write_text(json.dumps({"person_test_mode": value}))
            temporary.replace(self._subject_settings)
        previous = self.vlm.person_test_mode
        self.vlm.set_person_test_mode(enabled, persist=persist)
        self.inference.set_person_test_mode(enabled)
        if previous != enabled:
            self._subject_started_ts = time.time()
            self._classified_last_saved.clear()
            self._classified_last_hash.clear()
        result = self.status()
        self.events.publish("status", result)
        return result

    def start(self) -> None:
        self.camera.start()
        self.inference.start()
        self.vlm.start()
        self.voice.start()

    def stop(self) -> None:
        self.voice.close()
        self.player.close()
        self.vlm.stop()
        self.inference.stop()
        self.camera.stop()
        self.database.close()

    def storage_status(self) -> dict[str, Any]:
        mounted = self.config.media_mount.is_dir()
        result: dict[str, Any] = {
            "mounted": mounted,
            "mount": str(self.config.media_mount),
            "path": str(self.config.media_dir),
        }
        if mounted:
            usage = shutil.disk_usage(self.config.media_mount)
            free_gb = usage.free / (1024**3)
            result.update(
                {
                    "total_gb": round(usage.total / (1024**3), 1),
                    "free_gb": round(free_gb, 1),
                    "warning": free_gb < self.config.disk_warning_gb,
                }
            )
        else:
            result.update({"total_gb": 0, "free_gb": 0, "warning": True})
        return result

    @observation_locked
    def status(self) -> dict[str, Any]:
        now = time.time()
        current = self.database.current()
        activity = current.get("activity", {})
        posture = current.get("posture", {})
        presence = current.get("presence", {})
        latest = self.camera.latest()
        camera_fresh = bool(self.camera.online and latest and now - latest.ts <= max(5.0, 3.0 / max(1, self.config.camera_fps)))
        last_success = self.vlm.last_success_ts if self.vlm.enabled else self.inference.last_success_ts
        freshness_window = (max(self.config.vlm_interval_seconds, self.config.vlm_sleep_interval_seconds,
                                self.config.vlm_motion_interval_seconds) + self.config.vlm_timeout_seconds
                            if self.vlm.enabled else max(self.config.analysis_interval_seconds, 5.0) * 2)
        observation_valid = bool(camera_fresh and last_success is not None and now - last_success <= freshness_window)
        return {
            "title": "Edge AI Monitor",
            "person_test_mode": self.vlm.person_test_mode,
            "observation_mode": self.database.observation_mode,
            "observation_valid": observation_valid,
            "now": now,
            "activity": {
                "value": activity.get("value", "unknown"),
                "since": activity.get("start_ts"),
                "duration_seconds": max(0, now - activity.get("start_ts", now)),
                "source": activity.get("source"),
                "confidence": activity.get("confidence"),
                "note": activity.get("note"),
            },
            "posture": {
                "value": posture.get("value", "unknown"),
                "since": posture.get("start_ts"),
                "duration_seconds": max(0, now - posture.get("start_ts", now)),
                "source": posture.get("source"),
                "confidence": posture.get("confidence"),
                "note": posture.get("note"),
            },
            "presence": {
                "value": presence.get("value", "unknown"),
                "since": presence.get("start_ts"),
                "duration_seconds": max(0, now - presence.get("start_ts", now)),
                "source": presence.get("source"),
                "confidence": presence.get("confidence"),
                "note": presence.get("note"),
            },
            "camera": {
                "online": camera_fresh,
                "error": self.camera.error,
                "last_frame_ts": latest.ts if latest else None,
                "motion_score": round(self.camera.motion_score, 2),
                "device": self.config.camera_device,
                "resolution": f"{self.config.camera_width}×{self.config.camera_height}",
            },
            "model": {
                "enabled": self.vlm.enabled or self.inference.enabled,
                "online": self.vlm.online if self.vlm.enabled else self.inference.online,
                "name": "Qwen3-VL-2B-Instruct" if self.vlm.enabled else self.inference.model_name,
                "posture_classifier_available": self.inference.posture.online,
                "architecture": "qwen-vlm-observer-with-lightweight-reference",
                "last_error": self.vlm.last_error if self.vlm.enabled else self.inference.last_error,
                "last_analysis_ts": self.vlm.last_analysis_ts if self.vlm.enabled else self.inference.last_analysis_ts,
                "last_success_ts": last_success,
                "observation_valid": observation_valid,
                "last_result": self.vlm.last_result if self.vlm.enabled else self.inference.last_result,
                "components": {
                    **self.inference.components,
                    "qwen_vlm": self.vlm.component,
                },
            },
            "storage": self.storage_status(),
        }

    @observation_locked
    def transition(
        self,
        category: str,
        value: str,
        source: str,
        confidence: float | None = None,
        note: str | None = None,
        capture_frame: Frame | None = None,
    ) -> dict[str, Any]:
        changed, previous = self.database.transition(
            category, value, source, confidence, note
        )
        payload = {
            "category": category,
            "observation_mode": self.database.observation_mode,
            "value": value,
            "previous": previous,
            "source": source,
            "confidence": confidence,
            "note": note,
        }
        classified_photo_saved = False
        if changed:
            self.events.publish("state", payload)
            if (
                category == "activity"
                and value != "unknown"
                and not (source == "qwen3-vl-2b" and capture_frame is None)
            ):
                try:
                    classified_photo_saved = self.save_classified_photo(
                        value,
                        confidence or 0.0,
                        source,
                        note or "状态切换",
                        force=True,
                        frame=capture_frame,
                    )
                except RuntimeError:
                    pass
            if (
                category == "activity"
                and previous == "sleeping"
                and value in {"awake", "awake_happy", "awake_unhappy", "crying"}
            ):
                self.database.add_event(
                    "wake",
                    time.time(),
                    value,
                    self.database.current().get("posture", {}).get("value"),
                    confidence,
                    metadata_json=json.dumps({"source": source, "note": note}, ensure_ascii=False),
                )
                self.events.publish(
                    "wake",
                    {"message": "Edge AI Monitor睡醒啦", **payload},
                )
        return {
            "changed": changed,
            "classified_photo_saved": classified_photo_saved,
            **payload,
        }

    def publish_vlm_observation(self, observation: dict[str, Any]) -> None:
        self.events.publish("vlm_observation", observation)

    def observations(
        self,
        limit: int = 100,
        start_ts: float | None = None,
        end_ts: float | None = None,
        observation_mode: str = "infant",
    ) -> list[dict[str, Any]]:
        items = self.database.observations(limit, start_ts, end_ts, validate_mode(observation_mode))
        interval_end = min(time.time(), end_ts) if end_ts is not None else time.time()
        for item in items:
            item["end_ts"] = interval_end
            item["duration_seconds"] = max(0.0, interval_end - item["ts"])
            interval_end = item["ts"]
        return items

    def _frame_in_current_mode(self, frame: Frame) -> bool:
        return frame.ts >= getattr(self, "_subject_started_ts", 0.0)

    @staticmethod
    def _image_hash(jpeg: bytes) -> int:
        with Image.open(io.BytesIO(jpeg)) as image:
            pixels = image.convert("L").resize((17, 16)).tobytes()
        result = 0
        for row in range(16):
            offset = row * 17
            for column in range(16):
                result = (result << 1) | int(
                    pixels[offset + column] > pixels[offset + column + 1]
                )
        return result

    @observation_locked
    def consider_classified_photo(self, result: dict[str, Any]) -> bool:
        # Qwen is the primary observer. Its exact analyzed frame is saved only
        # after a stable state change; lightweight periodic supplements produced
        # too many false face detections on blankets.
        if result.get("observation_mode", self.database.observation_mode) != self.database.observation_mode:
            return False
        if self.config.vlm_enabled and self.config.vlm_primary:
            return False
        if not self.config.classified_photos_enabled or not result.get("face_detected"):
            return False
        activity = str(result.get("activity", "unknown"))
        if activity == "unknown":
            return False
        current = self.database.current().get("activity", {}).get("value", "unknown")
        if current != activity:
            return False
        confidence = float(result.get("confidence", 0.0))
        if confidence < CLASSIFIED_MIN_CONFIDENCE.get(activity, 1.0):
            return False
        metadata = {
            "reason": result.get("reason"),
            "emotion": result.get("emotion"),
            "emotion_confidence": result.get("emotion_confidence"),
            "happiness_score": result.get("happiness_score"),
            "eyes_detected": result.get("eyes_detected"),
            "motion_score": result.get("motion_score"),
        }
        return self.save_classified_photo(
            activity,
            confidence,
            "emotiefflib-temporal",
            str(result.get("reason", "自动分类")),
            metadata=metadata,
        )

    @observation_locked
    def save_classified_photo(
        self,
        activity: str,
        confidence: float,
        source: str,
        reason: str,
        *,
        force: bool = False,
        metadata: dict[str, Any] | None = None,
        frame: Frame | None = None,
    ) -> bool:
        if not self.config.classified_photos_enabled or activity not in CLASSIFIED_INTERVAL_SECONDS:
            return False
        if not self.config.media_mount.is_dir():
            raise RuntimeError("媒体存储未挂载，未保存分类照片")
        frame = frame or self.camera.sharpest_recent(5)
        if frame is None or not self._frame_in_current_mode(frame):
            return False

        now = time.time()
        moment = datetime.fromtimestamp(frame.ts)
        day_start = datetime.combine(moment.date(), datetime.min.time()).timestamp()
        day_end = day_start + 24 * 60 * 60
        with self._classified_lock:
            counts = self.database.event_counts("classified_photo", day_start, day_end, observation_mode=self.database.observation_mode)
            posture_counts = self.database.event_counts(
                "classified_posture_photo", day_start, day_end, group_by="posture", observation_mode=self.database.observation_mode
            )
            if (
                sum(counts.values()) + sum(posture_counts.values())
                >= self.config.classified_daily_limit
            ):
                return False
            if counts.get(activity, 0) >= CLASSIFIED_DAILY_LIMITS[activity]:
                return False
            if not force and now - self._classified_last_saved.get(activity, 0.0) < CLASSIFIED_INTERVAL_SECONDS[activity]:
                return False

            standard = self.camera.standard_jpeg(frame.jpeg, quality=90)
            image_hash = self._image_hash(standard)
            previous_hash = self._classified_last_hash.get(activity)
            if not force and previous_hash is not None and (image_hash ^ previous_hash).bit_count() < 10:
                return False

            directory = (
                self.config.media_dir
                / "classified"
                / self.database.observation_mode
                / "activity"
                / activity
                / moment.strftime("%Y/%m/%d")
            )
            directory.mkdir(parents=True, exist_ok=True)
            filename = moment.strftime(f"{activity}_%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
            original = directory / filename
            original.write_bytes(standard)
            relative = original.relative_to(self.config.media_dir).as_posix()
            current = self.database.current()
            event_metadata = {
                "source": source,
                "reason": reason,
                "sharpness": round(frame.sharpness, 2),
                "strategy": "transition-or-interval-deduplicated",
                **(metadata or {}),
            }
            self.database.add_event(
                "classified_photo",
                frame.ts,
                activity,
                current.get("posture", {}).get("value"),
                confidence,
                original_path=relative,
                metadata_json=json.dumps(event_metadata, ensure_ascii=False),
            )
            self._classified_last_saved[activity] = now
            self._classified_last_hash[activity] = image_hash
        return True

    @observation_locked
    def save_expression_photo(
        self,
        expression: str,
        confidence: float,
        reason: str,
        frame: Frame,
    ) -> bool:
        if not self._frame_in_current_mode(frame) or expression not in EXPRESSION_LABELS or confidence < 0.72:
            return False
        if not self.config.media_mount.is_dir():
            raise RuntimeError("媒体存储未挂载，未保存表情照片")

        moment = datetime.fromtimestamp(frame.ts)
        day_start = datetime.combine(moment.date(), datetime.min.time()).timestamp()
        day_end = day_start + 24 * 60 * 60
        with self._classified_lock:
            if (
                self.database.event_count(
                    "expression_photo", day_start, day_end, observation_mode=self.database.observation_mode
                )
                >= EXPRESSION_DAILY_LIMIT
            ):
                return False

            standard = self.camera.standard_jpeg(frame.jpeg, quality=92)
            day_dir = (
                self.config.media_dir
                / "classified"
                / self.database.observation_mode
                / "expression"
                / expression
                / moment.strftime("%Y/%m/%d")
            )
            originals = day_dir / "original"
            thumbnails = day_dir / "thumbnail"
            originals.mkdir(parents=True, exist_ok=True)
            thumbnails.mkdir(parents=True, exist_ok=True)
            stem = moment.strftime(f"{expression}_%Y%m%d_%H%M%S_%f")[:-3]
            original = originals / f"{stem}.jpg"
            thumbnail = thumbnails / f"{stem}.jpg"
            original.write_bytes(standard)
            with Image.open(io.BytesIO(standard)) as image:
                image.thumbnail((480, 480))
                image.convert("RGB").save(
                    thumbnail, "JPEG", quality=86, optimize=True
                )

            current = self.database.current()
            original_rel = original.relative_to(self.config.media_dir).as_posix()
            thumbnail_rel = thumbnail.relative_to(self.config.media_dir).as_posix()
            self.database.add_event(
                "expression_photo",
                frame.ts,
                current.get("activity", {}).get("value"),
                current.get("posture", {}).get("value"),
                confidence,
                original_rel,
                thumbnail_rel,
                json.dumps(
                    {
                        "expression": expression,
                        "label": EXPRESSION_LABELS[expression],
                        "reason": reason,
                        "source": "qwen3-vl-2b-analyzed-frame",
                        "sharpness": round(frame.sharpness, 2),
                    },
                    ensure_ascii=False,
                ),
            )
        return True

    @observation_locked
    def set_manual_state(self, category: str, value: str) -> dict[str, Any]:
        result = self.transition(category, value, "manual", 1.0, "手机手动设置")
        if category == "activity" and not result["classified_photo_saved"]:
            try:
                result["classified_photo_saved"] = self.save_classified_photo(
                    value, 1.0, "manual", "手机手动确认", force=True
                )
            except RuntimeError:
                result["classified_photo_saved"] = False
        result["training_sample_saved"] = self.save_training_sample(category, value)
        return result

    @observation_locked
    def save_training_sample(self, category: str, value: str) -> bool:
        """Save human-confirmed examples for the later baby-specific fine-tuning."""
        if (
            category not in {"activity", "posture"}
            or value == "unknown"
            or not self.config.media_mount.is_dir()
        ):
            return False
        frame = self.camera.sharpest_recent(3)
        if frame is None or not self._frame_in_current_mode(frame):
            return False
        moment = datetime.fromtimestamp(frame.ts)
        directory = self.config.media_dir / "training" / self.database.observation_mode / category / value
        directory.mkdir(parents=True, exist_ok=True)
        filename = moment.strftime("%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
        (directory / filename).write_bytes(self.camera.standard_jpeg(frame.jpeg, quality=90))
        (directory / Path(filename).with_suffix(".json")).write_text(json.dumps({
            "observation_mode": self.database.observation_mode,
            "person_test_mode": self.vlm.person_test_mode,
            "source": "manual", "category": category, "value": value, "ts": frame.ts,
        }, ensure_ascii=False))
        return True

    @observation_locked
    def save_current_classified_photo(self) -> dict[str, Any]:
        current = self.database.current()
        activity = current.get("activity", {}).get("value", "unknown")
        posture = current.get("posture", {}).get("value", "unknown")
        if activity == "unknown" and posture == "unknown":
            raise ValueError("请先选择当前状态或睡姿")

        saved: list[dict[str, str]] = []
        if activity != "unknown" and self.save_classified_photo(
            activity, 1.0, "manual-button", "手机点击保存照片", force=True
        ):
            saved.append({"category": "activity", "value": activity})
        if posture != "unknown" and self.save_posture_photo(posture):
            saved.append({"category": "posture", "value": posture})
        if not saved:
            raise RuntimeError("今日分类照片已达到保存上限")
        return {"saved": saved, "activity": activity, "posture": posture}

    @observation_locked
    def save_posture_photo(self, posture: str) -> bool:
        if posture not in POSTURE_VALUES or posture == "unknown":
            return False
        if not self.config.media_mount.is_dir():
            raise RuntimeError("媒体存储未挂载，未保存照片")
        frame = self.camera.sharpest_recent(5)
        if frame is None or not self._frame_in_current_mode(frame):
            raise RuntimeError("摄像头尚无可保存画面")

        moment = datetime.fromtimestamp(frame.ts)
        day_start = datetime.combine(moment.date(), datetime.min.time()).timestamp()
        day_end = day_start + 24 * 60 * 60
        with self._classified_lock:
            activity_counts = self.database.event_counts(
                "classified_photo", day_start, day_end, observation_mode=self.database.observation_mode
            )
            posture_counts = self.database.event_counts(
                "classified_posture_photo", day_start, day_end, group_by="posture", observation_mode=self.database.observation_mode
            )
            if sum(activity_counts.values()) + sum(posture_counts.values()) >= self.config.classified_daily_limit:
                return False
            if posture_counts.get(posture, 0) >= 100:
                return False

            standard = self.camera.standard_jpeg(frame.jpeg, quality=90)
            directory = (
                self.config.media_dir
                / "classified"
                / self.database.observation_mode
                / "posture"
                / posture
                / moment.strftime("%Y/%m/%d")
            )
            directory.mkdir(parents=True, exist_ok=True)
            filename = moment.strftime(f"{posture}_%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
            original = directory / filename
            original.write_bytes(standard)
            relative = original.relative_to(self.config.media_dir).as_posix()
            current = self.database.current()
            self.database.add_event(
                "classified_posture_photo",
                frame.ts,
                current.get("activity", {}).get("value"),
                posture,
                1.0,
                original_path=relative,
                metadata_json=json.dumps(
                    {
                        "source": "manual-button",
                        "reason": "手机点击保存照片",
                        "sharpness": round(frame.sharpness, 2),
                    },
                    ensure_ascii=False,
                ),
            )
        return True

    @observation_locked
    def save_smile(self, confidence: float = 1.0, reason: str = "manual") -> dict[str, Any]:
        storage = self.storage_status()
        if not storage["mounted"]:
            raise RuntimeError("媒体存储未挂载，未保存照片")
        frame = self.camera.sharpest_recent(5)
        if frame is None or not self._frame_in_current_mode(frame):
            raise RuntimeError("摄像头尚无可保存画面")
        moment = datetime.fromtimestamp(frame.ts)
        day_dir = self.config.media_dir / "smiles" / self.database.observation_mode / moment.strftime("%Y/%m/%d")
        originals = day_dir / "original"
        thumbnails = day_dir / "thumbnail"
        originals.mkdir(parents=True, exist_ok=True)
        thumbnails.mkdir(parents=True, exist_ok=True)
        stem = moment.strftime("smile_%Y%m%d_%H%M%S_%f")[:-3]
        original = originals / f"{stem}.jpg"
        thumbnail = thumbnails / f"{stem}.jpg"
        standard_original = self.camera.standard_jpeg(frame.jpeg, quality=92)
        original.write_bytes(standard_original)
        with Image.open(io.BytesIO(standard_original)) as image:
            image.thumbnail((480, 480))
            image.convert("RGB").save(thumbnail, "JPEG", quality=86, optimize=True)
        current = self.database.current()
        activity = current.get("activity", {}).get("value")
        posture = current.get("posture", {}).get("value")
        original_rel = original.relative_to(self.config.media_dir).as_posix()
        thumbnail_rel = thumbnail.relative_to(self.config.media_dir).as_posix()
        event_id = self.database.add_event(
            "smile",
            frame.ts,
            activity,
            posture,
            confidence,
            original_rel,
            thumbnail_rel,
            json.dumps(
                {"reason": reason, "sharpness": round(frame.sharpness, 2)},
                ensure_ascii=False,
            ),
        )
        payload = {
            "id": event_id,
            "observation_mode": self.database.observation_mode,
            "ts": frame.ts,
            "activity": activity,
            "posture": posture,
            "confidence": confidence,
            "original_url": f"/api/photos/{event_id}/original",
            "thumbnail_url": f"/api/photos/{event_id}/thumbnail",
        }
        self.events.publish("smile", payload)
        return payload

    def smile_events(self, limit: int = 100) -> list[dict[str, Any]]:
        result = []
        for item in self.database.events("smile", limit):
            if item.get("original_path"):
                item["original_url"] = f"/api/photos/{item['id']}/original"
            if item.get("thumbnail_path"):
                item["thumbnail_url"] = f"/api/photos/{item['id']}/thumbnail"
            result.append(item)
        return result

    def photo_events(self, limit: int = 300) -> list[dict[str, Any]]:
        result = []
        for item in self.database.events_by_types(PHOTO_EVENT_TYPES, limit):
            event_type = item["event_type"]
            if event_type == "smile":
                kind, value, label = "smile", "smile", "笑脸"
            elif event_type == "expression_photo":
                try:
                    metadata = json.loads(item.get("metadata_json") or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                value = str(metadata.get("expression") or "unknown")
                kind = "expression"
                label = EXPRESSION_LABELS.get(value, "表情未知")
            elif event_type == "classified_photo":
                kind, value = "activity", item.get("activity") or "unknown"
                label = {
                    "sleeping": "睡觉",
                    "awake": "清醒",
                    "awake_happy": "开心",
                    "awake_unhappy": "不开心",
                    "crying": "大哭",
                }.get(value, "状态未知")
            else:
                kind, value = "posture", item.get("posture") or "unknown"
                label = {
                    "supine": "仰睡",
                    "side": "侧睡",
                    "prone": "趴睡",
                    "left_side": "左侧睡",
                    "right_side": "右侧睡",
                    "left_prone": "左趴睡",
                    "right_prone": "右趴睡",
                }.get(value, "睡姿未知")

            original_path = item.get("original_path")
            thumbnail_path = item.get("thumbnail_path") or original_path
            if not original_path or self.resolve_media(original_path) is None:
                continue
            result.append(
                {
                    "id": item["id"],
                    "event_type": event_type,
                    "observation_mode": item["observation_mode"],
                    "ts": item["ts"],
                    "kind": kind,
                    "value": value,
                    "label": label,
                    "activity": item.get("activity"),
                    "posture": item.get("posture"),
                    "confidence": item.get("confidence"),
                    "original_url": f"/api/photos/{item['id']}/original",
                    "thumbnail_url": f"/api/photos/{item['id']}/thumbnail",
                }
            )
        return result

    def trash_photo(self, event_id: int, event_type: str) -> dict[str, Any]:
        if event_type not in PHOTO_EVENT_TYPES:
            raise ValueError("不支持的照片类型")
        if event_id <= 0:
            raise ValueError("照片编号无效")

        with self._classified_lock:
            item = self.database.event(event_id)
            if item is None or item.get("event_type") != event_type:
                raise ValueError("照片不存在或已经移入回收站")

            moment = datetime.now()
            trash_directory = (
                self.config.media_dir
                / "_trash"
                / "photos"
                / moment.strftime("%Y/%m/%d")
            )
            trash_directory.mkdir(parents=True, exist_ok=True)
            moved: list[tuple[Path, Path]] = []
            trash_paths: dict[str, str | None] = {
                "original_path": None,
                "thumbnail_path": None,
            }
            try:
                for field in ("original_path", "thumbnail_path"):
                    relative = item.get(field)
                    if not relative:
                        continue
                    source = self.resolve_media(relative)
                    if source is None:
                        continue
                    destination = trash_directory / (
                        f"{event_id}_{field}_{source.name}"
                    )
                    shutil.move(str(source), str(destination))
                    moved.append((destination, source))
                    trash_paths[field] = destination.relative_to(
                        self.config.media_dir
                    ).as_posix()
                if not moved:
                    raise ValueError("照片文件不存在")

                try:
                    original_metadata = json.loads(item.get("metadata_json") or "{}")
                except json.JSONDecodeError:
                    original_metadata = {"raw": item.get("metadata_json")}
                metadata = json.dumps(
                    {
                        "trashed_at": moment.timestamp(),
                        "original_event_type": event_type,
                        "original_original_path": item.get("original_path"),
                        "original_thumbnail_path": item.get("thumbnail_path"),
                        "original_metadata": original_metadata,
                    },
                    ensure_ascii=False,
                )
                if not self.database.mark_event_trashed(
                    event_id,
                    event_type,
                    trash_paths["original_path"],
                    trash_paths["thumbnail_path"],
                    metadata,
                ):
                    raise RuntimeError("照片记录更新失败")
            except Exception:
                for destination, source in reversed(moved):
                    if destination.is_file() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(destination), str(source))
                raise
        return {"trashed": True, "id": event_id}

    def timeline(self, start_ts: float, end_ts: float, observation_mode: str = "infant") -> dict[str, Any]:
        intervals = self.database.intervals(start_ts, end_ts, validate_mode(observation_mode))
        totals: dict[str, dict[str, float]] = {}
        for interval in intervals:
            category = interval["category"]
            value = interval["value"]
            totals.setdefault(category, {})
            totals[category][value] = totals[category].get(value, 0) + interval[
                "duration_seconds"
            ]
        return {"observation_mode": observation_mode, "intervals": intervals, "totals": totals}

    def resolve_media(self, relative: str) -> Path | None:
        candidate = (self.config.media_dir / relative).resolve()
        root = self.config.media_dir.resolve()
        if candidate == root or root not in candidate.parents or not candidate.is_file():
            return None
        return candidate
