from __future__ import annotations

import os
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    app_dir: Path
    data_dir: Path
    media_mount: Path
    media_dir: Path
    static_dir: Path
    host: str
    port: int
    camera_device: str
    camera_width: int
    camera_height: int
    camera_fps: int
    model_enabled: bool
    model_name: str
    vlm_enabled: bool
    vlm_primary: bool
    vlm_service_url: str
    vlm_interval_seconds: float
    vlm_sleep_interval_seconds: float
    vlm_motion_interval_seconds: float
    vlm_timeout_seconds: float
    vlm_confidence_threshold: float
    vlm_stable_seconds: float
    emotion_model_name: str
    face_model_path: Path
    posture_model_path: Path
    analysis_interval_seconds: float
    sleep_stable_seconds: float
    awake_stable_seconds: float
    posture_stable_seconds: float
    sleep_motion_threshold: float
    motion_threshold: float
    smile_confidence: float
    smile_cooldown_seconds: float
    classified_photos_enabled: bool
    classified_daily_limit: int
    disk_warning_gb: float
    play_media_dir: Path
    airplay_python: Path
    speaker_host: str
    speaker_name: str
    playback_backend: str
    bluetooth_speaker_mac: str
    ffmpeg_path: Path
    play_default_volume: int
    voice_worker_python: Path
    voice_model_dir: Path
    voice_keywords_file: Path
    voice_asr_model_dir: Path
    voice_vad_model: Path
    voice_audio_device: str
    voice_wake_word: str
    voice_default_enabled: bool
    voice_schedule_start: str
    voice_schedule_end: str
    voice_timezone: str
    person_test_mode: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        app_dir = Path(os.getenv("BMON_APP_DIR", Path(__file__).resolve().parents[1]))
        data_dir = Path(os.getenv("BMON_DATA_DIR", app_dir / "data"))
        media_mount = Path(os.getenv("BMON_MEDIA_MOUNT", str(data_dir / "media")))
        media_dir = Path(
            os.getenv(
                "BMON_MEDIA_DIR",
                str(media_mount),
            )
        )
        endpoint = urlsplit(os.getenv("BMON_VLM_SERVICE_URL", "http://127.0.0.1:8010"))
        if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"} or endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment:
            raise ValueError("Model service must be an exact loopback HTTP origin")
        return cls(
            app_dir=app_dir,
            person_test_mode=_bool_env("BMON_PERSON_TEST_MODE", False),
            data_dir=data_dir,
            media_mount=media_mount,
            media_dir=media_dir,
            static_dir=app_dir / "static",
            host=os.getenv("BMON_HOST", "127.0.0.1"),
            port=int(os.getenv("BMON_PORT", "8080")),
            camera_device=os.getenv("BMON_CAMERA_DEVICE", "/dev/video0"),
            camera_width=int(os.getenv("BMON_CAMERA_WIDTH", "1280")),
            camera_height=int(os.getenv("BMON_CAMERA_HEIGHT", "720")),
            camera_fps=int(os.getenv("BMON_CAMERA_FPS", "15")),
            model_enabled=_bool_env("BMON_MODEL_ENABLED", False),
            model_name=os.getenv("BMON_MODEL_NAME", "EmotiEffLib + 时间状态机"),
            vlm_enabled=_bool_env("BMON_VLM_ENABLED", False),
            vlm_primary=_bool_env("BMON_VLM_PRIMARY", True),
            vlm_service_url=os.getenv(
                "BMON_VLM_SERVICE_URL", "http://127.0.0.1:8010"
            ),
            vlm_interval_seconds=float(os.getenv("BMON_VLM_INTERVAL_SECONDS", "30")),
            vlm_sleep_interval_seconds=float(
                os.getenv("BMON_VLM_SLEEP_INTERVAL_SECONDS", "60")
            ),
            vlm_motion_interval_seconds=float(
                os.getenv("BMON_VLM_MOTION_INTERVAL_SECONDS", "15")
            ),
            vlm_timeout_seconds=float(os.getenv("BMON_VLM_TIMEOUT_SECONDS", "35")),
            vlm_confidence_threshold=float(
                os.getenv("BMON_VLM_CONFIDENCE_THRESHOLD", "0.60")
            ),
            vlm_stable_seconds=float(os.getenv("BMON_VLM_STABLE_SECONDS", "20")),
            emotion_model_name=os.getenv(
                "BMON_EMOTION_MODEL_NAME", "enet_b0_8_best_afew"
            ),
            face_model_path=Path(
                os.getenv(
                    "BMON_FACE_MODEL_PATH",
                    str(app_dir / "models/face_detection_yunet_2023mar.onnx"),
                )
            ),
            posture_model_path=Path(
                os.getenv("BMON_POSTURE_MODEL_PATH", str(app_dir / "models/posture.onnx"))
            ),
            analysis_interval_seconds=float(
                os.getenv("BMON_ANALYSIS_INTERVAL_SECONDS", "3")
            ),
            sleep_stable_seconds=float(os.getenv("BMON_SLEEP_STABLE_SECONDS", "30")),
            awake_stable_seconds=float(os.getenv("BMON_AWAKE_STABLE_SECONDS", "10")),
            posture_stable_seconds=float(
                os.getenv("BMON_POSTURE_STABLE_SECONDS", "20")
            ),
            sleep_motion_threshold=float(os.getenv("BMON_SLEEP_MOTION_THRESHOLD", "2.5")),
            motion_threshold=float(os.getenv("BMON_MOTION_THRESHOLD", "8.0")),
            smile_confidence=float(os.getenv("BMON_SMILE_CONFIDENCE", "0.30")),
            smile_cooldown_seconds=float(
                os.getenv("BMON_SMILE_COOLDOWN_SECONDS", "60")
            ),
            classified_photos_enabled=_bool_env("BMON_CLASSIFIED_PHOTOS_ENABLED", True),
            classified_daily_limit=int(os.getenv("BMON_CLASSIFIED_DAILY_LIMIT", "250")),
            disk_warning_gb=float(os.getenv("BMON_DISK_WARNING_GB", "20")),
            play_media_dir=Path(
                os.getenv("BMON_PLAY_MEDIA_DIR", str(media_dir / "陪我玩"))
            ),
            airplay_python=Path(
                os.getenv(
                    "BMON_AIRPLAY_PYTHON",
                    str(app_dir / ".venv-airplay/bin/python"),
                )
            ),
            speaker_host=os.getenv("BMON_SPEAKER_HOST", ""),
            speaker_name=os.getenv("BMON_SPEAKER_NAME", "Audio output"),
            playback_backend=os.getenv("BMON_PLAYBACK_BACKEND", "airplay"),
            bluetooth_speaker_mac=os.getenv("BMON_BLUETOOTH_SPEAKER_MAC", ""),
            ffmpeg_path=Path(os.getenv("BMON_FFMPEG_PATH", "/usr/bin/ffmpeg")),
            play_default_volume=int(os.getenv("BMON_PLAY_DEFAULT_VOLUME", "25")),
            voice_worker_python=Path(
                os.getenv(
                    "BMON_VOICE_PYTHON",
                    str(app_dir / ".venv-voice/bin/python"),
                )
            ),
            voice_model_dir=Path(
                os.getenv(
                    "BMON_VOICE_MODEL_DIR",
                    str(app_dir / "models/sherpa-onnx-kws-zh-en-3M"),
                )
            ),
            voice_keywords_file=Path(
                os.getenv(
                    "BMON_VOICE_KEYWORDS_FILE",
                    str(app_dir / "models/sherpa-onnx-kws-zh-en-3M/keywords-bmon.txt"),
                )
            ),
            voice_asr_model_dir=Path(
                os.getenv(
                    "BMON_VOICE_ASR_MODEL_DIR",
                    str(app_dir / "models/sherpa-onnx-paraformer-zh-small-2024-03-09"),
                )
            ),
            voice_vad_model=Path(
                os.getenv(
                    "BMON_VOICE_VAD_MODEL",
                    str(app_dir / "models/silero_vad.onnx"),
                )
            ),
            voice_audio_device=os.getenv("BMON_VOICE_AUDIO_DEVICE", "plughw:0,0"),
            voice_wake_word=os.getenv("BMON_VOICE_WAKE_WORD", "八戒"),
            voice_default_enabled=_bool_env("BMON_VOICE_ENABLED", False),
            voice_schedule_start=os.getenv("BMON_VOICE_SCHEDULE_START", "07:00"),
            voice_schedule_end=os.getenv("BMON_VOICE_SCHEDULE_END", "23:00"),
            voice_timezone=os.getenv("BMON_VOICE_TIMEZONE", "Asia/Shanghai"),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.app_dir / "models").mkdir(parents=True, exist_ok=True)
        self.media_mount.mkdir(parents=True, exist_ok=True)
        if self.media_mount.is_dir():
            self.media_dir.mkdir(parents=True, exist_ok=True)
            (self.media_dir / "smiles").mkdir(parents=True, exist_ok=True)
            (self.media_dir / "classified" / "activity").mkdir(
                parents=True, exist_ok=True
            )
            (self.media_dir / "classified" / "posture").mkdir(
                parents=True, exist_ok=True
            )
            (self.media_dir / "classified" / "expression").mkdir(
                parents=True, exist_ok=True
            )
            (self.media_dir / "training" / "activity").mkdir(parents=True, exist_ok=True)
            (self.media_dir / "training" / "posture").mkdir(parents=True, exist_ok=True)
            self.play_media_dir.mkdir(parents=True, exist_ok=True)
