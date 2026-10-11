"""Hardware-free integration smoke test; all output is synthetic and temporary."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from baby_monitor.app import BabyMonitorApp
from baby_monitor.config import Config
from security import public_payload

class DemoTest(unittest.TestCase):
    def test_synthetic_camera_and_public_api_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            values={'BMON_DEMO':'true','BMON_MODEL_ENABLED':'false','BMON_VLM_ENABLED':'false',
                    'BMON_VOICE_ENABLED':'false','BMON_DATA_DIR':directory,
                    'BMON_APP_DIR':str(Path(__file__).resolve().parents[1])}
            with patch.dict(os.environ,values,clear=True):
                app=BabyMonitorApp(Config.from_env())
                try:
                    app.start()
                    frame=app.camera.wait_for_frame(0,2)
                    self.assertIsNotNone(frame)
                    status=public_payload(app.status())
                    self.assertTrue(status['camera']['online'])
                    self.assertNotIn('device',status['camera'])
                    self.assertNotIn('path',status['storage'])
                    self.assertNotIn('speaker',public_payload(app.player.status()))
                    self.assertEqual(app.photo_events(),[])
                    self.assertFalse(app.voice.status()['enabled'])
                finally:
                    app.stop()
