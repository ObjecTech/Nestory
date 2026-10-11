import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from baby_monitor.database import Database
from baby_monitor.vlm import VLMObserver


class SubjectSwitchTest(unittest.TestCase):
    def test_switch_resets_state_and_persists_before_accepting_new_results(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / 'test.db')
            db.transition('activity', 'sleeping', 'test')
            observer = VLMObserver(
                camera=SimpleNamespace(motion_score=0), database=db, enabled=True,
                service_url='http://127.0.0.1:1', interval_seconds=30,
                sleep_interval_seconds=60, motion_interval_seconds=15,
                timeout_seconds=1, confidence_threshold=.6, stable_seconds=20,
                motion_threshold=8, on_transition=lambda *args: None,
                on_observation=lambda *args: None)
            saved = []
            observer.last_result = {'sleep_state': 'sleeping'}
            observer.set_person_test_mode(True, persist=saved.append)
            self.assertEqual(saved, [True])
            self.assertTrue(observer.person_test_mode)
            self.assertEqual(observer._mode_generation, 1)
            self.assertIsNone(observer.last_result)
            self.assertIsNone(observer._last_recorded_signature)
            self.assertEqual(db.current()['activity']['value'], 'unknown')
            observer.set_person_test_mode(False)
            self.assertEqual(observer._mode_generation, 2)
            self.assertFalse(observer.person_test_mode)
            with self.assertRaises(ValueError):
                observer.set_person_test_mode('true')
            db._connection.close()
