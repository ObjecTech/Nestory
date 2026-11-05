"""Cry detection: score smoothing, storage, assistant answers and summary. Synthetic data only."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from baby_monitor.assistant_common import timestamp
from baby_monitor.assistant_service import AssistantService
from baby_monitor.cry_detection import CryTracker
from baby_monitor.database import Database
from baby_monitor.voice_control import VoiceControl
from test_assistant import FIXED, FakeModel

# FIXED = 2026-10-13T08:00Z (16:00 Shanghai); "昨晚" is 10-12 20:00 to 10-13 10:00 local.


def ts(iso):
    return timestamp(iso) / 1000


class CryTrackerTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.tracker = CryTracker(lambda s, p: self.events.append(('start', s, p)),
                                  lambda e, p: self.events.append(('end', e, p)))

    def feed(self, start, scores):
        for offset, score in enumerate(scores):
            self.tracker.observe(score, start + offset)

    def test_single_spike_is_ignored(self):
        self.feed(100, [0.9] + [0.0] * 30)
        self.assertEqual(self.events, [])

    def test_confirmed_period_starts_at_first_hit_and_ends_after_quiet_gap(self):
        self.feed(100, [0.1, 0.8, 0.6, 0.9, 0.2, 0.7] + [0.0] * 20)
        self.assertEqual(self.events[0], ('start', 101, 0.6))
        self.assertEqual(self.events[1], ('end', 106, 0.9))  # last hit at 105 plus the 1 s window
        self.assertIsNone(self.tracker.active_since)

    def test_short_pause_does_not_split_a_period_and_flush_closes(self):
        self.feed(0, [0.8, 0.8] + [0.0] * 10 + [0.8])
        self.assertEqual([e[0] for e in self.events], ['start'])
        self.tracker.flush()
        self.assertEqual(self.events[-1], ('end', 13, 0.8))


class CryStorageTest(unittest.TestCase):
    def test_start_end_and_dangling_period_closed_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'visual.db'
            db = Database(path, 'adult_test')
            first = db.start_cry(1000.0, 0.7)
            db.end_cry(first, 1060.0, 0.95)
            db.start_cry(2000.0, 0.6)  # power loss before it ended
            db.close()
            db = Database(path, 'adult_test')
            rows = db._connection.execute(
                "SELECT start_ts,end_ts,confidence,observation_mode FROM intervals WHERE category='cry' ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(1000.0, 1060.0, 0.95, 'adult_test'), (2000.0, 2001.0, 0.6, 'adult_test')])
            self.assertNotIn('cry', db.current())
            db.close()


class CryAssistantTest(unittest.TestCase):
    def setUp(self):
        for target in ('baby_monitor.assistant_database.now_ms', 'baby_monitor.assistant_common.now_ms',
                       'baby_monitor.assistant_facts.now_ms', 'baby_monitor.assistant_summary.now_ms'):
            clock = patch(target, return_value=FIXED)
            clock.start(); self.addCleanup(clock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.visual = Database(root / 'baby_monitor.db', 'infant')
        # 03:10–03:14 and 05:00–05:02 local last night; 13:00 today; an adult-test cry is never mixed in.
        for start, end, mode in (('2026-10-12T19:10:00Z', '2026-10-12T19:14:00Z', 'infant'),
                                 ('2026-10-12T21:00:00Z', '2026-10-12T21:02:00Z', 'infant'),
                                 ('2026-10-13T05:00:00Z', '2026-10-13T05:01:00Z', 'infant'),
                                 ('2026-10-12T20:00:00Z', '2026-10-12T20:30:00Z', 'adult_test')):
            self.visual._connection.execute(
                "INSERT INTO intervals(category,value,start_ts,end_ts,source,observation_mode) VALUES('cry','crying',?,?,'audio',?)",
                (ts(start), ts(end), mode))
        self.visual._connection.commit()
        self.service = AssistantService(root / 'assistant.db', model=FakeModel(), visual_path=root / 'baby_monitor.db')
        self.cid = self.service.database.create_conversation({'scope': 'real'})['conversation']['id']
        self.n = 0

    def tearDown(self):
        self.service.close(); self.visual.close(); self.tmp.cleanup()

    def ask(self, content):
        self.n += 1
        result, _ = self.service.send(self.cid, 'real', dict(content=content, client_message_id=f'c{self.n}', timezone='Asia/Shanghai'))
        self.service.queue.join()
        reply = self.service.database.poll(self.cid, result['user_message']['id'], 'real')
        return reply

    def answer(self, reply):
        return next(m for m in reply['messages'] if m['role'] == 'assistant')['content'] if 'messages' in reply else reply['assistant_message']['content']

    def test_last_night_cry_query_lists_periods_without_model(self):
        reply = self.ask('昨晚宝宝哭了几次？')
        text = self.answer(reply)
        self.assertIn('2 段哭声', text)
        self.assertIn('03:10–03:14', text)
        self.assertIn('05:00–05:02', text)

    def test_today_cry_and_no_cry_answers(self):
        self.assertIn('3 段哭声', self.answer(self.ask('今天哭了几次')))  # calendar day incl. early morning
        self.assertIn('没有记录到哭声', self.answer(self.ask('前天哭了吗')))

    def test_night_sleep_answer_mentions_cries_for_cross_check(self):
        self.service.database.create_event(dict(scope='real', timezone='Asia/Shanghai', event_type='sleep',
            occurred_at='2026-10-12T12:30:00Z', end_at='2026-10-12T22:00:00Z', notes='', client_event_id='night'))
        text = self.answer(self.ask('昨晚睡了多久'))
        self.assertIn('2 段哭声', text)
        self.assertIn('补记', text)

    def test_daily_summary_and_timeline_include_cry(self):
        day = self.service.summary.build(dict(scope='real', timezone='Asia/Shanghai', **{'from': '2026-10-13', 'to': '2026-10-13'}))
        self.assertEqual(day['stats']['cry']['count'], 3)  # local 10-13 covers 03:10, 05:00 and 13:00
        self.assertTrue(any('段哭声' in line for line in day['highlights']))
        _, _, tz, start, end = __import__('baby_monitor.assistant_common', fromlist=['date_window']).date_window(
            dict(timezone='Asia/Shanghai', **{'from': '2026-10-13', 'to': '2026-10-13'}))
        items = self.service.facts.unified_timeline('real', start, end, tz)['items']
        cries = [i for i in items if i['category'] == 'cry']
        self.assertEqual({(i['source'], i['label']) for i in cries}, {('audio', '哭声')})


class CryVoiceControlTest(unittest.TestCase):
    def make(self, root, enabled):
        model = root / 'tagger'
        model.mkdir()
        (model / 'model.int8.onnx').write_bytes(b'x')
        (model / 'class_labels_indices.csv').write_text('index,mid,display_name\n')
        self.scores = []
        return VoiceControl(player=None, settings_path=root / 'voice.json', worker_python=root / 'python',
                            model_dir=root / 'kws', keywords_file=root / 'kw.txt', audio_device='plughw:0,0',
                            default_enabled=enabled, cry_model_dir=model, cry_enabled=True,
                            on_cry_score=self.scores.append,
                            now_factory=lambda: datetime(2026, 10, 13, 3, 0))  # outside the 07–23 schedule

    def test_cry_scores_flow_while_voice_is_off_or_out_of_hours(self):
        with tempfile.TemporaryDirectory() as folder:
            for enabled in (False, True):
                root = Path(folder) / str(enabled); root.mkdir()
                voice = self.make(root, enabled)
                voice._handle_event({'type': 'cry', 'score': 0.83})
                voice._handle_event({'type': 'wake', 'text': '佩奇'})
                self.assertEqual(self.scores, [0.83])
                self.assertEqual(voice.status()['state'], 'outside_schedule' if enabled else 'disabled')
                self.assertTrue(voice._cry_active())
                self.assertTrue(voice.status()['cry_detection']['ready'])


if __name__ == '__main__':
    unittest.main()
