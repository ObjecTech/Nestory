from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .camera import Camera, Frame
from .database import Database
from .observation_mode import mode_name


VISIBILITY_VALUES = {"visible", "partial", "not_visible", "unknown"}
SLEEP_STATE_VALUES = {"sleeping", "awake", "drowsy", "unknown"}
POSTURE_VALUES = {
    "supine",
    "side",
    "prone",
    "unknown",
}
EXPRESSION_VALUES = {"calm", "happy", "unhappy", "crying", "neutral", "unknown"}
ACTIVITY_LEVEL_VALUES = {"still", "low", "active", "unknown"}


def _enum(value: Any, allowed: set[str]) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in allowed else "unknown"


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _short_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _optional_label(value: Any, limit: int) -> str:
    text = _short_text(value, limit)
    return "" if text.lower() in {"unknown", "none", "null", "未知"} else text


def _posture(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = {
        "left_side": "side",
        "right_side": "side",
        "left_prone": "prone",
        "right_prone": "prone",
    }.get(text, text)
    return text if text in POSTURE_VALUES else "unknown"


def normalize_observation(payload: dict[str, Any], person_test_mode: bool = False) -> dict[str, Any]:
    """Validate model output and enforce cross-field consistency."""
    visibility = _enum(payload.get("visibility"), VISIBILITY_VALUES)
    baby_visible_raw = payload.get("baby_visible")
    baby_visible = baby_visible_raw if isinstance(baby_visible_raw, bool) else None
    visibility_boolean = (
        False
        if visibility == "not_visible"
        else True
        if visibility in {"visible", "partial"}
        else None
    )
    if (
        baby_visible is not None
        and visibility_boolean is not None
        and baby_visible != visibility_boolean
    ):
        # Conflicting visibility fields are not safe enough to drive the timeline.
        baby_visible = None
        visibility = "unknown"
    elif visibility_boolean is False:
        baby_visible = False
    elif baby_visible is True and visibility == "unknown":
        visibility = "visible"
    elif baby_visible is False:
        visibility = "not_visible"

    observed_actions = payload.get("observed_actions", [])
    if not isinstance(observed_actions, list):
        observed_actions = []
    actions = [
        _short_text(item, 40)
        for item in observed_actions[:8]
        if _short_text(item, 40)
    ]

    result = {
        "baby_visible": baby_visible,
        "visibility": visibility,
        "sleep_state": _enum(payload.get("sleep_state"), SLEEP_STATE_VALUES),
        "posture": _posture(payload.get("posture")),
        "expression": _enum(payload.get("expression"), EXPRESSION_VALUES),
        "activity_level": _enum(
            payload.get("activity_level"), ACTIVITY_LEVEL_VALUES
        ),
        "confidence": _confidence(payload.get("confidence")),
        "expression_confidence": _confidence(
            payload.get("expression_confidence")
        ),
        "posture_confidence": _confidence(payload.get("posture_confidence")),
        "visibility_confidence": _confidence(payload.get("visibility_confidence")),
        "suggested_state": _optional_label(payload.get("suggested_state"), 80),
        "description": _short_text(payload.get("description"), 240),
        "observed_actions": actions,
    }

    # Never infer a state for an absent or unconfirmed baby.
    if baby_visible is not True or visibility == "partial":
        result.update(
            {
                "sleep_state": "unknown",
                "posture": "unknown",
                "expression": "unknown",
                "activity_level": "unknown",
                "confidence": 0.0,
                "expression_confidence": 0.0,
                "posture_confidence": 0.0,
            }
        )
        if baby_visible is False:
            result["confidence"] = min(result["confidence"], 0.5)
            # Some small VLM outputs use zero to mean "no baby state to score"
            # even when baby_visible=false and visibility=not_visible are fully
            # consistent. Treat that explicit absence as sufficiently reliable
            # to clear stale activity/posture after temporal confirmation.
            result["visibility_confidence"] = max(
                result["visibility_confidence"], 0.75
            )

    # A model may describe someone sitting or standing while choosing one of
    # the lying-only posture labels. Keep the observable description, but do
    # not turn that contradiction into a posture or sleep interval.
    non_lying = bool(re.search(
        r"坐在|坐着|坐于|坐姿|端坐|坐起|站立|站着|站在|行走|走动",
        result["description"],
    ))
    non_lying = non_lying or payload.get("body_state") in {"sitting", "standing", "walking"}
    result["posture_not_lying"] = non_lying
    body_hidden = payload.get("body_visible") is False or bool(re.search(
        r"只[见有].{0,5}(脸|面部|头部)|仅.{0,5}(脸|面部|头部)|未见.{0,6}身体|身体.{0,4}(遮挡|不可见)", result["description"]))
    face_hidden = payload.get("face_visible") is False
    if body_hidden:
        result["posture"] = "unknown"
        result["posture_confidence"] = 0.0
    if face_hidden:
        result["expression"] = "unknown"
        result["expression_confidence"] = 0.0
        if result["sleep_state"] in {"sleeping", "drowsy"}:
            result["sleep_state"] = "unknown"
            result["confidence"] = 0.0
    simulation = person_test_mode and (payload.get("sleep_evidence") in {"eyes_closed_only", "simulation"}
        or bool(re.search(r"模拟|假装|(?:闭眼|闭目).{0,6}(?:静止|静卧|不动)", result["description"])))
    if simulation and result["sleep_state"] == "sleeping":
        result["sleep_state"] = "unknown"
        result["confidence"] = 0.0
        if re.search(r"睡|入眠", result["suggested_state"]):
            result["suggested_state"] = ""

    if non_lying:
        result["posture"] = "unknown"
        result["posture_confidence"] = 0.0
        if result["sleep_state"] == "sleeping":
            result["sleep_state"] = "unknown"
            result["confidence"] = 0.0
        if re.search(r"仰卧|侧卧|俯卧|趴卧|躺卧", result["suggested_state"]):
            result["suggested_state"] = ""

    return result


def activity_from_observation(observation: dict[str, Any]) -> str:
    if observation.get("baby_visible") is not True:
        return "unknown"
    sleep_state = observation.get("sleep_state")
    if sleep_state == "sleeping":
        return "sleeping"
    if sleep_state in {"awake", "drowsy"}:
        return "awake"
    return "unknown"


@dataclass
class _Candidate:
    value: str
    since: float
    observations: int = 1


@dataclass
class _ObservationCandidate:
    signature: tuple[Any, ...]
    since: float
    observations: int = 1


class ObservationFilter:
    def __init__(self, minimum_observations: int = 2):
        self.minimum_observations = minimum_observations
        self._candidates: dict[str, _Candidate] = {}

    def reset(self, category: str) -> None:
        self._candidates.pop(category, None)

    def observe(self, category: str, value: str, now: float, seconds: float) -> bool:
        if value == "unknown":
            self.reset(category)
            return False
        candidate = self._candidates.get(category)
        if candidate is None or candidate.value != value:
            self._candidates[category] = _Candidate(value, now)
            return False
        candidate.observations += 1
        if candidate.observations >= self.minimum_observations and now - candidate.since >= seconds:
            self.reset(category)
            return True
        return False


class VLMObserver:
    def __init__(
        self,
        camera: Camera,
        database: Database,
        enabled: bool,
        service_url: str,
        interval_seconds: float,
        sleep_interval_seconds: float,
        motion_interval_seconds: float,
        timeout_seconds: float,
        confidence_threshold: float,
        stable_seconds: float,
        motion_threshold: float,
        on_transition: Callable[..., Any],
        on_observation: Callable[[dict[str, Any]], None],
        on_expression: Callable[[str, float, str, Frame], Any] | None = None,
        person_test_mode: bool = False,
        mode_lock=None,
    ):
        self.camera = camera
        self._mode_lock = mode_lock or threading.RLock()
        self._mode_generation = 0
        self._mode_since_ts = 0.0
        self.person_test_mode = person_test_mode
        self.database = database
        self.database.activate_mode(mode_name(person_test_mode), reset=False)
        self.enabled = enabled
        self.service_url = service_url.rstrip("/")
        self.interval_seconds = interval_seconds
        self.sleep_interval_seconds = sleep_interval_seconds
        self.motion_interval_seconds = motion_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.confidence_threshold = confidence_threshold
        self.stable_seconds = stable_seconds
        self.motion_threshold = motion_threshold
        self.on_transition = on_transition
        self.on_observation = on_observation
        self.on_expression = on_expression
        self.online = False
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_analysis_ts: float | None = None
        self.last_success_ts: float | None = None
        self._filter = ObservationFilter()
        previous_observations = self.database.observations(1, observation_mode=mode_name(person_test_mode))
        self._last_recorded_signature = (
            self._observation_signature(previous_observations[0])
            if previous_observations
            else None
        )
        self._observation_candidate: _ObservationCandidate | None = None
        self._last_stable_expression = self._canonical_expression(
            previous_observations[0].get("expression", "unknown")
            if previous_observations
            and previous_observations[0].get("baby_visible") is True
            else "unknown"
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_person_test_mode(self, enabled: bool, persist=None) -> None:
        if type(enabled) is not bool:
            raise ValueError("成人测试开关必须为布尔值")
        with self._mode_lock:
            if persist:
                persist(enabled)
            if enabled == self.person_test_mode:
                return
            self.person_test_mode = enabled
            self._mode_generation += 1
            self._mode_since_ts = time.time()
            self._filter = ObservationFilter()
            self._last_recorded_signature = None
            self._observation_candidate = None
            self._last_stable_expression = "unknown"
            self.last_result = None
            self.last_analysis_ts = None
            self.last_success_ts = None
            self.online = False
            self.last_error = None
            self.database.activate_mode(mode_name(enabled))

    @property
    def component(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "name": "Qwen3-VL-2B-Instruct",
            "error": self.last_error,
            "last_analysis_ts": self.last_analysis_ts,
            "last_success_ts": self.last_success_ts,
            "observation_mode": mode_name(self.person_test_mode),
        }

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="qwen-vlm-observer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(3.0, self.timeout_seconds + 1.0))

    def _current_activity(self) -> str:
        return self.database.current().get("activity", {}).get("value", "unknown")

    def _next_interval(self) -> float:
        if not self.online:
            return min(5.0, self.motion_interval_seconds)
        if self.camera.motion_score >= self.motion_threshold:
            return self.motion_interval_seconds
        if self.database.current().get("presence", {}).get("value") == "not_visible":
            return self.sleep_interval_seconds
        if self._current_activity() == "sleeping":
            return self.sleep_interval_seconds
        return self.interval_seconds

    def _request(self, jpeg: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._mode_lock:
            request_mode = self.person_test_mode
            generation = self._mode_generation
        request = urllib.request.Request(
            f"{self.service_url}/analyze",
            data=jpeg,
            method="POST",
            headers={"Content-Type": "image/jpeg",
                     "X-Bmon-Person-Test-Mode": "true" if request_mode else "false"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        if not isinstance(envelope, dict) or not isinstance(envelope.get("observation"), dict):
            raise RuntimeError("Qwen 服务返回格式错误")
        if envelope.get("person_test_mode", False) is not request_mode:
            raise RuntimeError("Qwen 服务测试模式与应用不一致，请重启模型服务")
        expected_mode = mode_name(request_mode)
        if envelope.get("observation_mode", expected_mode) != expected_mode:
            raise RuntimeError("Qwen observation mode mismatch")
        envelope["observation_mode"] = expected_mode
        envelope["_mode_generation"] = generation
        return normalize_observation(envelope["observation"], person_test_mode=request_mode), envelope

    def _transition(
        self,
        category: str,
        value: str,
        confidence: float,
        note: str,
        frame: Frame | None = None,
    ) -> bool:
        current = self.database.current().get(category, {}).get("value", "unknown")
        if value == current:
            self._filter.reset(category)
            return False
        now = time.time()
        if self._filter.observe(category, value, now, self.stable_seconds):
            self.on_transition(
                category,
                value,
                "qwen3-vl-2b",
                confidence,
                ("[成人测试] " + note) if self.person_test_mode else note,
                frame,
            )
            return True
        return False

    def _persist_observation(
        self, observation: dict[str, Any], envelope: dict[str, Any], now: float
    ) -> bool:
        signature = self._observation_signature(observation)
        if signature == self._last_recorded_signature:
            self._observation_candidate = None
            return False
        if self._last_recorded_signature is not None:
            candidate = self._observation_candidate
            if candidate is None or candidate.signature != signature:
                self._observation_candidate = _ObservationCandidate(signature, now)
                return False
            candidate.observations += 1
            if candidate.observations < 2 or now - candidate.since < self.stable_seconds:
                return False
        self._observation_candidate = None
        observation_id = self.database.add_observation(
            ts=now,
            source="qwen3-vl-2b",
            observation=observation,
            metadata_json=json.dumps(
                {
                    "model": envelope.get("model"),
                    "person_test_mode": self.person_test_mode,
                    "observation_mode": mode_name(self.person_test_mode),
                    "load_seconds": envelope.get("load_seconds"),
                    "infer_seconds": envelope.get("infer_seconds"),
                },
                ensure_ascii=False,
            ),
        )
        published = {"id": observation_id, "ts": now, "observation_mode": mode_name(self.person_test_mode), **observation}
        self.on_observation(published)
        self._last_recorded_signature = signature
        return True

    @staticmethod
    def _observation_signature(observation: dict[str, Any]) -> tuple[Any, ...]:
        expression = observation.get("expression", "unknown")
        if expression in {"calm", "neutral"}:
            expression = "calm"
        return (
            observation.get("baby_visible"),
            observation.get("sleep_state", "unknown"),
            observation.get("posture", "unknown"),
            expression,
        )

    @staticmethod
    def _canonical_expression(expression: Any) -> str:
        value = str(expression or "unknown")
        return "calm" if value in {"calm", "neutral"} else value

    def _apply(
        self,
        observation: dict[str, Any],
        envelope: dict[str, Any],
        frame: Frame | None = None,
    ) -> None:
        now = time.time()

        presence = (
            "visible"
            if observation["baby_visible"] is True
            else "not_visible"
            if observation["baby_visible"] is False
            else "unknown"
        )
        presence_confidence = observation["visibility_confidence"] or observation["confidence"]
        if presence != "unknown" and presence_confidence >= self.confidence_threshold:
            presence_changed = self._transition(
                "presence",
                presence,
                presence_confidence,
                observation["description"],
                frame,
            )
        else:
            presence_changed = False

        if observation["baby_visible"] is not True:
            # Only clear the old baby state after "not visible" itself has become
            # stable. Unknown values otherwise reset candidates and cannot erase
            # a valid interval after one uncertain frame.
            if presence == "not_visible" and presence_changed:
                self._filter.reset("activity")
                self._filter.reset("posture")
                self.on_transition(
                    "activity", "unknown", "qwen3-vl-2b", presence_confidence,
                    "宝宝持续不在画面中", frame,
                )
                self.on_transition(
                    "posture", "unknown", "qwen3-vl-2b", presence_confidence,
                    "宝宝持续不在画面中", frame,
                )
            self._persist_observation(observation, envelope, now)
            return

        observation_persisted = self._persist_observation(observation, envelope, now)
        expression = self._canonical_expression(observation["expression"])
        expression_confidence = observation["expression_confidence"]
        capture_frame = (
            frame
            if observation["visibility"] == "visible"
            and observation["visibility_confidence"] >= 0.80
            else None
        )
        if observation_persisted:
            if (
                self.on_expression is not None
                and frame is not None
                and expression in {"calm", "happy", "unhappy", "crying"}
                and expression != self._last_stable_expression
                and expression_confidence >= max(0.78, self.confidence_threshold)
            ):
                try:
                    self.on_expression(
                        expression,
                        expression_confidence,
                        observation["description"],
                        frame,
                    )
                except RuntimeError:
                    pass
            self._last_stable_expression = expression
        # Keep a single uncertain frame from erasing a confirmed state, but do
        # not display a previous sleep/lying result indefinitely under occlusion.
        for category, value in (("activity", activity_from_observation(observation)),
                                ("posture", observation["posture"])):
            unknown_key = category + "_unknown"
            if value == "unknown":
                self._filter.reset(category)
                if self._filter.observe(unknown_key, "unconfirmed", now, self.stable_seconds):
                    if self.database.current().get(category, {}).get("value") != "unknown":
                        self.on_transition(category, "unknown", "qwen3-vl-2b", 0.0,
                                           "连续观察无法确认当前状态", frame)
            else:
                self._filter.reset(unknown_key)

        confidence = observation["confidence"]
        activity = activity_from_observation(observation)
        if activity == "unknown":
            self._filter.reset("activity")
        if activity != "unknown" and confidence >= max(0.75, self.confidence_threshold):
            self._transition(
                "activity",
                activity,
                confidence,
                observation["description"],
                capture_frame,
            )

        posture = observation["posture"]
        if posture == "unknown":
            self._filter.reset("posture")
        posture_confidence = observation["posture_confidence"] or confidence
        if observation.get("posture_not_lying"):
            if self._filter.observe("posture_not_lying", "confirmed", now, self.stable_seconds):
                if self.database.current().get("posture", {}).get("value") != "unknown":
                    self.on_transition(
                        "posture", "unknown", "qwen3-vl-2b", 0.0,
                        "坐姿或站姿不属于卧姿分类", frame,
                    )
        else:
            self._filter.reset("posture_not_lying")
        if posture != "unknown" and posture_confidence >= max(
            0.82, self.confidence_threshold
        ):
            self._transition(
                "posture",
                posture,
                posture_confidence,
                observation["description"],
                frame,
            )

    def _observe_frame(self, frame: Frame) -> None:
        # Network/model work stays outside the shared mode lock. The worker is
        # single-threaded, so slow requests never accumulate in a queue.
        with self._mode_lock:
            if frame.ts < self._mode_since_ts:
                return
            generation = self._mode_generation
            self.last_analysis_ts = time.time()
        try:
            observation, envelope = self._request(frame.jpeg)
            with self._mode_lock:
                if generation != self._mode_generation or envelope.get("_mode_generation") != generation:
                    return
                if not self.camera.online:
                    return
                self.last_result = observation
                self.online = True
                self.last_error = None
                self.last_success_ts = time.time()
                self._apply(observation, envelope, frame)
        except Exception as exc:
            with self._mode_lock:
                if generation != self._mode_generation:
                    return
                self.online = False
                self.last_error = f"Qwen 观察失败: {exc}"[:300]
                # last_success_ts and last_result remain inspectable as history.

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            if not self.camera.online:
                continue
            with self._mode_lock:
                if self.last_analysis_ts is not None:
                    if time.time() - self.last_analysis_ts < self._next_interval():
                        continue
            frame = self.camera.sharpest_recent(3)
            if frame is not None and time.time() - frame.ts <= 3.0:
                self._observe_frame(frame)
