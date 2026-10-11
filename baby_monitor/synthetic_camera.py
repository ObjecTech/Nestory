"""Hardware-free camera adapter. Uses only the geometric example shipped here."""
import threading
from pathlib import Path
from .camera import Camera

class SyntheticCamera(Camera):
    def __init__(self):
        super().__init__('synthetic', 640, 360, 2)
        self.jpeg = (Path(__file__).resolve().parents[1] / 'examples/sample_image.jpg').read_bytes()

    def start(self):
        self.online = True
        def generate():
            while not self._stop.is_set():
                self._accept_jpeg(self.jpeg)
                self._stop.wait(0.5)
        self._thread = threading.Thread(target=generate, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.online = False
