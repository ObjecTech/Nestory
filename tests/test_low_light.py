"""Low-light gating for a camera without night vision. Synthetic frames only."""
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from baby_monitor.camera import Camera, Frame
from baby_monitor.database import Database
from baby_monitor.vlm import LOW_LIGHT_NOTE
from test_visual_modes import make_app, make_observer


def jpeg(level):
    output = io.BytesIO()
    Image.new('L', (64, 48), level).save(output, 'JPEG')
    return output.getvalue()


def frame(brightness):
    return Frame(time.time(), b'test', 1.0, brightness)


class CameraBrightnessTest(unittest.TestCase):
    def test_sampled_frames_carry_mean_brightness(self):
        camera = Camera('/dev/null', 64, 48, 15)
        camera._accept_jpeg(jpeg(10))
        self.assertLess(camera.latest().brightness, 15)
        self.assertLess(camera.brightness, 15)
        camera._ring[-1].ts -= 1  # allow the next frame to be sampled
        camera._accept_jpeg(jpeg(200))
        self.assertGreater(camera.brightness, 190)


class LowLightObserverTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.folder.name) / 'test.db')
        self.observer = make_observer(self.db, min_brightness=40)

    def tearDown(self):
        self.folder.cleanup()

    def at(self, ts, brightness):
        with patch('baby_monitor.vlm.time.time', return_value=ts):
            return self.observer._update_light(frame(brightness))

    def test_brief_shadow_skips_analysis_but_keeps_state(self):
        self.db.transition('activity', 'sleeping', 'qwen3-vl-2b', .9, 'test')
        self.assertFalse(self.at(1000, 12))
        self.assertFalse(self.at(1025, 12))
        self.assertFalse(self.observer.low_light)
        self.assertEqual(self.db.current()['activity']['value'], 'sleeping')
        self.assertTrue(self.at(1026, 120))

    def test_sustained_darkness_clears_state_until_light_returns(self):
        self.db.transition('activity', 'sleeping', 'qwen3-vl-2b', .9, 'test')
        self.db.transition('posture', 'supine', 'qwen3-vl-2b', .9, 'test')
        self.assertFalse(self.at(1000, 12))
        self.assertFalse(self.at(1031, 12))
        self.assertTrue(self.observer.low_light)
        self.assertEqual(self.observer.low_light_since, 1000)
        current = self.db.current()
        self.assertEqual(current['activity']['value'], 'unknown')
        self.assertEqual(current['activity']['note'], LOW_LIGHT_NOTE)
        self.assertEqual(current['posture']['value'], 'unknown')
        # Just above the threshold is not enough to resume (hysteresis).
        self.assertFalse(self.at(1035, 45))
        self.assertTrue(self.observer.low_light)
        self.assertTrue(self.at(1040, 60))
        self.assertFalse(self.observer.low_light)
        self.assertIsNone(self.observer.low_light_since)

    def test_dark_frames_never_reach_the_model(self):
        self.observer.camera.sharpest_recent = lambda _: frame(5)
        with patch.object(self.observer, '_request') as request:
            self.observer.start()
            time.sleep(1.5)
            self.observer.stop()
        request.assert_not_called()

    def test_threshold_zero_or_unsampled_frame_disables_gate(self):
        self.observer.min_brightness = 0
        self.assertTrue(self.at(1000, 1))
        self.observer.min_brightness = 40
        self.assertTrue(self.at(1000, None))


class LowLightStatusTest(unittest.TestCase):
    def test_status_reports_low_light_and_brightness(self):
        with tempfile.TemporaryDirectory() as folder:
            app = make_app(folder, BMON_VLM_MIN_BRIGHTNESS='55')
            self.assertEqual(app.vlm.min_brightness, 55)
            status = app.status()
            self.assertFalse(status['low_light'])
            self.assertIsNotNone(status['camera']['brightness'])
            app.vlm.low_light = True
            self.assertTrue(app.status()['low_light'])
            self.assertTrue(app.status()['model']['components']['qwen_vlm']['low_light'])


if __name__ == '__main__':
    unittest.main()
