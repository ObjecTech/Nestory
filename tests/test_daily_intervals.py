import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from baby_monitor.database import Database


class DailyIntervalsTest(unittest.TestCase):
    def test_cross_day_open_and_closed_intervals(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('baby_monitor.database.time.time', return_value=100):
                db = Database(Path(folder) / 'test.db')
            with patch('baby_monitor.database.time.time', return_value=300):
                yesterday = db.intervals(150, 250)[0]
                self.assertEqual((yesterday['start_ts'], yesterday['end_ts'],
                                  yesterday['duration_seconds']), (150, 250, 100))
                self.assertTrue(yesterday['ends_at_day_boundary'])
                today = db.intervals(250, 350)[0]
                self.assertEqual(today['start_ts'], 250)
                self.assertIsNone(today['end_ts'])
                self.assertEqual(today['duration_seconds'], 50)
                self.assertFalse(today['ends_at_day_boundary'])
                self.assertEqual(db.current()['activity']['start_ts'], 100)
                self.assertEqual(db.intervals(350, 450), [])
                db._connection.execute('UPDATE intervals SET end_ts = 280')
                closed = db.intervals(250, 350)[0]
                self.assertEqual(closed['end_ts'], 280)
                self.assertEqual(closed['duration_seconds'], 30)
            db._connection.close()
