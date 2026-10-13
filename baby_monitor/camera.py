from __future__ import annotations

import io
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


@dataclass
class Frame:
    ts: float
    jpeg: bytes
    sharpness: float


class Camera:
    def __init__(self, device: str, width: int, height: int, fps: int):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self._condition = threading.Condition()
        self._latest: Frame | None = None
        self._ring: deque[Frame] = deque(maxlen=90)
        self._last_analysis_gray: np.ndarray | None = None
        self._last_analysis_ts = 0.0
        self._compatible_cache_ts = 0.0
        self._compatible_cache_jpeg: bytes | None = None
        self.motion_score = 0.0
        self._motion_scores: deque[float] = deque(maxlen=3)
        self.online = False
        self.error: str | None = None
        self._pipeline = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @staticmethod
    def _gray_small(jpeg: bytes) -> np.ndarray | None:
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                return np.asarray(image.convert("L").resize((64, 36)), dtype=np.float32)
        except (OSError, ValueError, SyntaxError):
            return None

    @staticmethod
    def _sharpness(gray: np.ndarray | None) -> float:
        if gray is None:
            return 0.0
        dx = np.diff(gray, axis=1)
        dy = np.diff(gray, axis=0)
        return float(dx.var() + dy.var())

    @staticmethod
    def standard_jpeg(jpeg: bytes, quality: int = 88) -> bytes:
        """Convert UVC MJPEG frames into baseline JPEGs accepted by Safari."""
        output = io.BytesIO()
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                image.convert("RGB").save(
                    output,
                    "JPEG",
                    quality=quality,
                    optimize=False,
                    progressive=False,
                    subsampling=2,
                )
            return output.getvalue()
        except (OSError, ValueError, SyntaxError):
            return jpeg

    def _accept_jpeg(self, jpeg: bytes) -> None:
        if not jpeg or len(jpeg) < 128:
            return
        now = time.time()
        with self._condition:
            sample_frame = not self._ring or now - self._ring[-1].ts >= 0.45
            sample_motion = now - self._last_analysis_ts >= 0.8
        gray = self._gray_small(jpeg) if sample_frame or sample_motion else None
        sharpness = self._sharpness(gray) if gray is not None and sample_frame else 0.0
        frame = Frame(now, jpeg, sharpness)
        with self._condition:
            self._latest = frame
            if sample_frame:
                self._ring.append(frame)
            if sample_motion and gray is not None:
                if self._last_analysis_gray is not None:
                    delta = gray - self._last_analysis_gray
                    # Suppress common exposure/IR brightness shifts; require sustained
                    # spatial changes rather than one noisy frame to shorten scheduling.
                    spatial_score = float(np.abs(delta - np.median(delta)).mean())
                    self._motion_scores.append(spatial_score)
                    self.motion_score = float(np.median([0.0] * (3 - len(self._motion_scores)) + list(self._motion_scores)))
                self._last_analysis_gray = gray
                self._last_analysis_ts = now
            self.online = True
            self.error = None
            self._condition.notify_all()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            pipeline = None
            try:
                import gi

                gi.require_version("Gst", "1.0")
                from gi.repository import Gst

                Gst.init(None)
                pipeline_text = (
                    f"v4l2src device={self.device} do-timestamp=true ! "
                    f"image/jpeg,width={self.width},height={self.height},framerate={self.fps}/1 ! "
                    "jpegparse ! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
                )
                pipeline = Gst.parse_launch(pipeline_text)
                self._pipeline = pipeline
                sink = pipeline.get_by_name("sink")

                def on_sample(appsink):
                    sample = appsink.emit("pull-sample")
                    if sample is None:
                        return Gst.FlowReturn.ERROR
                    buffer = sample.get_buffer()
                    success, mapped = buffer.map(Gst.MapFlags.READ)
                    if success:
                        try:
                            try:
                                self._accept_jpeg(bytes(mapped.data))
                            except Exception:
                                # Tolerate malformed, truncated or hardware glitch frames
                                # from any USB camera without terminating or polluting stderr.
                                pass
                        finally:
                            buffer.unmap(mapped)
                    return Gst.FlowReturn.OK

                sink.connect("new-sample", on_sample)
                state_change = pipeline.set_state(Gst.State.PLAYING)
                if state_change == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError("camera pipeline failed to start")
                bus = pipeline.get_bus()
                while not self._stop.is_set():
                    message = bus.timed_pop_filtered(
                        500 * Gst.MSECOND,
                        Gst.MessageType.ERROR | Gst.MessageType.EOS,
                    )
                    if message:
                        if message.type == Gst.MessageType.ERROR:
                            error, debug = message.parse_error()
                            raise RuntimeError(f"{error}: {debug or ''}")
                        raise RuntimeError("camera stream ended")
            except Exception as exc:
                self.online = False
                self.error = str(exc)
            finally:
                if pipeline is not None:
                    try:
                        pipeline.set_state(Gst.State.NULL)
                    except Exception:
                        pass
                self._pipeline = None

            if not self._stop.is_set():
                self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def latest(self) -> Frame | None:
        with self._condition:
            return self._latest

    def compatible_snapshot(self) -> bytes | None:
        with self._condition:
            frame = self._latest
            if frame is None:
                return None
            if self._compatible_cache_jpeg is not None and self._compatible_cache_ts == frame.ts:
                return self._compatible_cache_jpeg
        compatible = self.standard_jpeg(frame.jpeg)
        with self._condition:
            if self._latest and self._latest.ts == frame.ts:
                self._compatible_cache_ts = frame.ts
                self._compatible_cache_jpeg = compatible
        return compatible

    def wait_for_frame(self, after_ts: float, timeout: float = 10) -> Frame | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while time.monotonic() < deadline:
                if self._latest and self._latest.ts > after_ts:
                    return self._latest
                self._condition.wait(timeout=min(1, deadline - time.monotonic()))
        return None

    def recent_frames(self, count: int = 3, window_seconds: float = 8) -> list[Frame]:
        cutoff = time.time() - window_seconds
        with self._condition:
            candidates = [frame for frame in self._ring if frame.ts >= cutoff]
        if len(candidates) <= count:
            return candidates
        indexes = np.linspace(0, len(candidates) - 1, count, dtype=int)
        return [candidates[index] for index in indexes]

    def sharpest_recent(self, window_seconds: float = 5) -> Frame | None:
        cutoff = time.time() - window_seconds
        with self._condition:
            frames = [frame for frame in self._ring if frame.ts >= cutoff]
            if not frames and self._latest:
                frames = [self._latest]
        return max(frames, key=lambda item: item.sharpness) if frames else None
