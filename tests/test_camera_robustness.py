import io
import unittest
from PIL import Image

from baby_monitor.camera import Camera, Frame


class CameraRobustnessTest(unittest.TestCase):
    def setUp(self):
        # Create a valid JPEG
        img = Image.new("RGB", (64, 48), color="blue")
        img.paste("red", (0, 0, 32, 24))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        self.valid_jpeg = buf.getvalue()

    def test_accept_valid_jpeg(self):
        camera = Camera("unused", 64, 48, 15)
        camera._accept_jpeg(self.valid_jpeg)
        self.assertIsNotNone(camera._latest)
        self.assertEqual(camera._latest.jpeg, self.valid_jpeg)
        self.assertTrue(camera.online)
        self.assertIsNone(camera.error)

    def test_accept_truncated_jpeg_does_not_crash(self):
        # Simulate hardware UVC packet truncation (drop trailing 30 bytes)
        truncated_jpeg = self.valid_jpeg[:-30]
        camera = Camera("unused", 64, 48, 15)
        # Should not raise OSError: broken data stream
        camera._accept_jpeg(truncated_jpeg)
        self.assertIsNotNone(camera._latest)
        self.assertEqual(camera._latest.jpeg, truncated_jpeg)
        self.assertTrue(camera.online)

    def test_accept_malformed_and_short_bytes(self):
        camera = Camera("unused", 64, 48, 15)
        # Very short byte sequences (< 128 bytes)
        camera._accept_jpeg(b"too short")
        self.assertIsNone(camera._latest)

        # Corrupted non-JPEG payload
        garbage = b"X" * 500
        camera._accept_jpeg(garbage)
        # Should safely store or drop without raising unhandled exception
        self.assertIsNotNone(camera._latest)
        self.assertEqual(camera._latest.sharpness, 0.0)

    def test_standard_jpeg_robustness(self):
        # Valid JPEG produces standard baseline JPEG
        standard = Camera.standard_jpeg(self.valid_jpeg)
        self.assertIsInstance(standard, bytes)
        self.assertGreater(len(standard), 0)

        # Truncated JPEG is safely converted or returned
        truncated_jpeg = self.valid_jpeg[:-30]
        standard_truncated = Camera.standard_jpeg(truncated_jpeg)
        self.assertIsInstance(standard_truncated, bytes)

        # Corrupted data falls back safely to input bytes rather than raising
        garbage = b"corrupted bytes"
        standard_garbage = Camera.standard_jpeg(garbage)
        self.assertEqual(standard_garbage, garbage)

    def test_sharpness_handles_none(self):
        self.assertEqual(Camera._sharpness(None), 0.0)
