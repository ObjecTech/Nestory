from __future__ import annotations

import io
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from .camera import Camera, Frame
from .database import Database


NEGATIVE_EMOTIONS = {"Anger", "Contempt", "Disgust", "Fear", "Sadness"}
POSTURE_LABELS = (
    "supine",
    "left_side",
    "right_side",
    "left_prone",
    "right_prone",
)


@dataclass
class _Candidate:
    value: str
    since: float
    observations: int = 1


class TemporalStateFilter:
    """Only accept a state after it remains stable across time and frames."""

    def __init__(self, minimum_observations: int = 3):
        self.minimum_observations = minimum_observations
        self._candidates: dict[str, _Candidate] = {}

    def reset(self, category: str) -> None:
        self._candidates.pop(category, None)

    def observe(
        self,
        category: str,
        value: str,
        now: float,
        required_seconds: float,
    ) -> bool:
        if value == "unknown":
            self.reset(category)
            return False
        candidate = self._candidates.get(category)
        if candidate is None or candidate.value != value:
            self._candidates[category] = _Candidate(value, now)
            return False
        candidate.observations += 1
        if (
            candidate.observations >= self.minimum_observations
            and now - candidate.since >= required_seconds
        ):
            self.reset(category)
            return True
        return False


def classify_activity(
    emotion: str,
    emotion_confidence: float,
    eyes_detected: int,
    face_detected: bool,
    motion_score: float,
    sleep_motion_threshold: float,
    wake_motion_threshold: float,
) -> tuple[str, float, str]:
    """Conservative visual-only activity mapping; crying requires future audio."""
    if not face_detected:
        return "unknown", 0.0, "未检测到清晰面部"

    clearly_awake = eyes_detected > 0 or motion_score >= wake_motion_threshold
    if emotion == "Happiness" and emotion_confidence >= 0.30 and clearly_awake:
        return "awake_happy", emotion_confidence, "检测到持续开心表情"
    if emotion in NEGATIVE_EMOTIONS and emotion_confidence >= 0.45 and clearly_awake:
        return "awake_unhappy", emotion_confidence, "检测到疑似不开心表情"
    if clearly_awake:
        confidence = max(0.62, min(emotion_confidence, 0.82))
        return "awake", confidence, "检测到睁眼或明显活动"
    if eyes_detected == 0 and motion_score <= sleep_motion_threshold:
        confidence = max(0.62, min(emotion_confidence, 0.78))
        return "sleeping", confidence, "闭眼且画面持续平稳"
    return "unknown", 0.0, "当前线索不足"


class PostureClassifier:
    """Optional personalized ONNX posture classifier.

    The production interface is ready now. It intentionally stays unavailable until
    a model fine-tuned with this crib/camera view is placed at ``model_path``.
    """

    def __init__(self, model_path: Path, confidence_threshold: float = 0.68):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.online = False
        self.error: str | None = None
        self._session = None
        self._input_name = ""

    def load(self) -> None:
        if not self.model_path.is_file():
            self.error = "等待个体化睡姿模型和训练样本"
            return
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self.online = True
        self.error = None

    @staticmethod
    def _softmax(values: np.ndarray) -> np.ndarray:
        values = values - np.max(values)
        exp = np.exp(values)
        return exp / exp.sum()

    def predict(self, rgb: np.ndarray) -> tuple[str, float]:
        if not self.online or self._session is None:
            return "unknown", 0.0
        image = Image.fromarray(rgb).resize((224, 224))
        tensor = np.asarray(image, dtype=np.float32) / 255.0
        tensor = (tensor - np.asarray([0.485, 0.456, 0.406])) / np.asarray(
            [0.229, 0.224, 0.225]
        )
        tensor = tensor.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        scores = np.asarray(self._session.run(None, {self._input_name: tensor})[0]).reshape(-1)
        if scores.size != len(POSTURE_LABELS):
            raise RuntimeError(f"睡姿模型输出应为 {len(POSTURE_LABELS)} 类")
        if np.any(scores < 0) or not np.isclose(float(scores.sum()), 1.0, atol=0.05):
            scores = self._softmax(scores)
        index = int(np.argmax(scores))
        confidence = float(scores[index])
        if confidence < self.confidence_threshold:
            return "unknown", confidence
        return POSTURE_LABELS[index], confidence


class InferenceWorker:
    def __init__(
        self,
        camera: Camera,
        database: Database,
        enabled: bool,
        model_name: str,
        emotion_model_name: str,
        face_model_path: Path,
        posture_model_path: Path,
        analysis_interval_seconds: float,
        sleep_stable_seconds: float,
        awake_stable_seconds: float,
        posture_stable_seconds: float,
        sleep_motion_threshold: float,
        motion_threshold: float,
        smile_confidence: float,
        smile_cooldown_seconds: float,
        record_transitions: bool,
        on_transition: Callable[[str, str, str, float | None, str | None], None],
        on_smile: Callable[[float, str], None],
        on_classification: Callable[[dict[str, Any]], None],
    ):
        self.camera = camera
        self.database = database
        self.enabled = enabled
        self.model_name = model_name
        self.emotion_model_name = emotion_model_name
        self.face_model_path = face_model_path
        self.analysis_interval_seconds = analysis_interval_seconds
        self.sleep_stable_seconds = sleep_stable_seconds
        self.awake_stable_seconds = awake_stable_seconds
        self.posture_stable_seconds = posture_stable_seconds
        self.sleep_motion_threshold = sleep_motion_threshold
        self.motion_threshold = motion_threshold
        self.smile_confidence = smile_confidence
        self.smile_cooldown_seconds = smile_cooldown_seconds
        self.record_transitions = record_transitions
        self.on_transition = on_transition
        self.on_smile = on_smile
        self.on_classification = on_classification
        self.posture = PostureClassifier(posture_model_path)
        self.online = False
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_analysis_ts: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._emotion = None
        self._cv2 = None
        self._face_cascade = None
        self._face_detector = None
        self._eye_cascade = None
        self._filter = TemporalStateFilter()
        self._smile_votes: deque[tuple[float, bool, float]] = deque(maxlen=5)
        self._last_smile_ts = 0.0

    @property
    def components(self) -> dict[str, dict[str, Any]]:
        return {
            "emotion": {
                "online": self._emotion is not None,
                "name": self.emotion_model_name,
                "error": None if self._emotion is not None else self.last_error,
            },
            "posture": {
                "online": self.posture.online,
                "name": "personalized-mobilenet-onnx",
                "error": self.posture.error,
            },
            "temporal_filter": {"online": True, "name": "time-state-machine"},
            "cry_audio": {
                "online": False,
                "name": "not-configured",
                "error": "尚未接入麦克风，哭泣保留手动校正",
            },
        }

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lightweight-inference", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _load_models(self) -> None:
        import cv2
        from emotiefflib.facial_analysis import EmotiEffLibRecognizer

        self._cv2 = cv2
        self._emotion = EmotiEffLibRecognizer(
            engine="onnx", model_name=self.emotion_model_name
        )
        face_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        eye_path = cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml"
        self._face_cascade = cv2.CascadeClassifier(face_path)
        self._eye_cascade = cv2.CascadeClassifier(eye_path)
        if self._face_cascade.empty() or self._eye_cascade.empty():
            raise RuntimeError("OpenCV 面部检测器加载失败")
        if self.face_model_path.is_file() and hasattr(cv2, "FaceDetectorYN_create"):
            self._face_detector = cv2.FaceDetectorYN_create(
                str(self.face_model_path), "", (320, 320), 0.72, 0.3, 5000
            )
        self.posture.load()
        self.online = True
        self.last_error = None

    def _current_activity(self) -> str:
        return self.database.current().get("activity", {}).get("value", "unknown")

    def _should_analyze(self) -> bool:
        if not self.camera.online:
            return False
        if self.last_analysis_ts is None:
            return True
        interval = self.analysis_interval_seconds
        if self._current_activity() == "sleeping":
            interval = max(interval, 5.0)
        if self.camera.motion_score >= self.motion_threshold:
            interval = min(interval, 1.5)
        return time.time() - self.last_analysis_ts >= interval

    def _run(self) -> None:
        next_load_attempt = 0.0
        while not self._stop.wait(0.5):
            if self._emotion is None:
                if time.time() < next_load_attempt:
                    continue
                try:
                    self._load_models()
                except Exception as exc:
                    self.online = False
                    self.last_error = f"轻量模型加载失败: {exc}"[:300]
                    next_load_attempt = time.time() + 30
                    continue
            if not self._should_analyze():
                continue
            frame = self.camera.sharpest_recent(2)
            if frame is None:
                continue
            try:
                result = self._analyze(frame)
                self.online = True
                self.last_error = None
                self.last_result = result
                self.last_analysis_ts = time.time()
                self._apply(result)
                self.on_classification(result)
            except Exception as exc:
                self.last_error = f"轻量识别失败: {exc}"[:300]
                self.last_analysis_ts = time.time()

    def _detect_face(self, bgr: np.ndarray) -> tuple[np.ndarray | None, int]:
        cv2 = self._cv2
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = []
        if self._face_detector is not None:
            self._face_detector.setInputSize((bgr.shape[1], bgr.shape[0]))
            _, detections = self._face_detector.detect(bgr)
            if detections is not None:
                faces = [tuple(map(int, row[:4])) for row in detections]
        if not faces:
            detections = self._face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4, minSize=(55, 55)
            )
            faces = [tuple(map(int, row)) for row in detections]
        if not faces:
            return None, 0
        x, y, width, height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
        x = max(0, x)
        y = max(0, y)
        width = min(width, bgr.shape[1] - x)
        height = min(height, bgr.shape[0] - y)
        if width < 20 or height < 20:
            return None, 0
        margin_x = int(width * 0.12)
        margin_y = int(height * 0.12)
        x0 = max(0, x - margin_x)
        y0 = max(0, y - margin_y)
        x1 = min(bgr.shape[1], x + width + margin_x)
        y1 = min(bgr.shape[0], y + height + margin_y)
        face_bgr = bgr[y0:y1, x0:x1]

        face_gray = gray[y : y + height, x : x + width]
        upper_face = face_gray[: max(1, int(height * 0.65)), :]
        eyes = self._eye_cascade.detectMultiScale(
            upper_face,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(max(12, width // 10), max(8, height // 12)),
        )
        return cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB), min(len(eyes), 2)

    def _emotion_result(self, face_rgb: np.ndarray) -> tuple[str, float, dict[str, float]]:
        labels, scores = self._emotion.predict_emotions(face_rgb, logits=False)
        probabilities = np.asarray(scores)[0]
        class_names = [
            self._emotion.idx_to_emotion_class[index] for index in range(len(probabilities))
        ]
        values = {
            label: round(float(probability), 4)
            for label, probability in zip(class_names, probabilities)
        }
        label = labels[0]
        return label, float(values[label]), values

    def _analyze(self, frame: Frame) -> dict[str, Any]:
        cv2 = self._cv2
        bgr = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError("摄像头 JPEG 解码失败")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        face_rgb, eyes = self._detect_face(bgr)
        emotion = "Unknown"
        emotion_confidence = 0.0
        emotion_scores: dict[str, float] = {}
        if face_rgb is not None:
            emotion, emotion_confidence, emotion_scores = self._emotion_result(face_rgb)

        happiness_score = emotion_scores.get("Happiness", 0.0)
        activity_emotion = emotion
        activity_emotion_confidence = emotion_confidence
        if eyes > 0 and happiness_score >= self.smile_confidence:
            activity_emotion = "Happiness"
            activity_emotion_confidence = happiness_score

        activity, activity_confidence, reason = classify_activity(
            emotion=activity_emotion,
            emotion_confidence=activity_emotion_confidence,
            eyes_detected=eyes,
            face_detected=face_rgb is not None,
            motion_score=self.camera.motion_score,
            sleep_motion_threshold=self.sleep_motion_threshold,
            wake_motion_threshold=self.motion_threshold,
        )
        posture, posture_confidence = self.posture.predict(rgb)
        smile = eyes > 0 and happiness_score >= self.smile_confidence
        return {
            "activity": activity,
            "posture": posture,
            "smile": smile,
            "confidence": round(activity_confidence, 4),
            "posture_confidence": round(posture_confidence, 4),
            "reason": reason,
            "emotion": emotion,
            "emotion_confidence": round(emotion_confidence, 4),
            "happiness_score": round(happiness_score, 4),
            "emotion_scores": emotion_scores,
            "face_detected": face_rgb is not None,
            "eyes_detected": eyes,
            "motion_score": round(self.camera.motion_score, 3),
        }

    def _apply(self, result: dict[str, Any]) -> None:
        now = time.time()
        if self.record_transitions:
            activity = result["activity"]
            current_activity = self._current_activity()
            if activity == current_activity:
                self._filter.reset("activity")
            else:
                stable_seconds = (
                    self.sleep_stable_seconds
                    if activity == "sleeping"
                    else self.awake_stable_seconds
                )
                if self._filter.observe("activity", activity, now, stable_seconds):
                    self.on_transition(
                        "activity",
                        activity,
                        "emotiefflib-temporal",
                        result["confidence"],
                        result["reason"],
                    )

            posture = result["posture"]
            current_posture = self.database.current().get("posture", {}).get("value", "unknown")
            if posture == current_posture:
                self._filter.reset("posture")
            elif self._filter.observe("posture", posture, now, self.posture_stable_seconds):
                self.on_transition(
                    "posture",
                    posture,
                    "personalized-posture-onnx",
                    result["posture_confidence"],
                    "个体化睡姿小模型",
                )

        self._smile_votes.append((now, bool(result["smile"]), result["happiness_score"]))
        recent_positive = [
            confidence
            for ts, positive, confidence in self._smile_votes
            if positive and now - ts <= 12
        ]
        if (
            len(recent_positive) >= 3
            and now - self._last_smile_ts >= self.smile_cooldown_seconds
        ):
            self._last_smile_ts = now
            self._smile_votes.clear()
            self.on_smile(max(recent_positive), "EmotiEffLib 连续多帧笑容")
