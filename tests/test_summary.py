"""每日总结：Python 统计/要点/趋势，模型润色的数字校验与失败降级。全部为合成数据。"""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from baby_monitor.assistant_common import AssistantError
from baby_monitor.assistant_database import AssistantDatabase
from baby_monitor.assistant_facts import AssistantFacts
from baby_monitor.assistant_summary import DailySummary
import test_assistant_http as base  # 只复用夹具

DAY = '2026-10-12'


class Model:
    def __init__(self):
        self.answer, self.error, self.calls = '宝宝今天吃得不错，也睡得很安稳。', None, 0

    def chat(self, payload):
        self.calls += 1
        if self.error: raise self.error
        return dict(answer=self.answer, actions=[], query=None)


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDatabase(Path(self.tmp.name) / 'assistant.db')
        self.model = Model()
        self.summary = DailySummary(AssistantFacts(self.db), lambda: self.model)
        for i, (kind, at, end, ml, diaper) in enumerate([
                ('feeding', '2026-10-12T01:00:00Z', None, 120, None), ('feeding', '2026-10-12T05:00:00Z', None, 90, None),
                ('sleep', '2026-10-12T06:00:00Z', '2026-10-12T07:00:00Z', None, None),
                ('sleep', '2026-10-12T06:30:00Z', '2026-10-12T07:00:00Z', None, None),
                ('diaper', '2026-10-12T02:00:00Z', None, None, 'dirty')]):
            self.db.create_event(dict(scope='demo', client_event_id=f'e{i}', event_type=kind, timezone='Asia/Shanghai',
                                      occurred_at=at, end_at=end, amount_ml=ml, diaper_type=diaper))
        self.db.create_interaction(dict(client_id='p1', scope='demo', activity_id='fp-01', kind='finger_play', title='小手拍拍',
                                        started_at='2026-10-12T03:00:00Z', ended_at='2026-10-12T03:05:00Z', reaction='happy'))

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def build(self, narrative=False, day=DAY, scope='demo'):
        return self.summary.build({'scope': scope, 'from': day, 'to': day, 'timezone': 'Asia/Shanghai'}, narrative)

    def test_stats_highlights_and_trend(self):
        r = self.build()
        s = r['stats']
        self.assertEqual((s['feeding']['count'], s['feeding']['total_ml'], s['feeding']['last_at'], s['feeding']['average_gap_minutes']), (2, 210, '13:00', 240))
        self.assertEqual((s['sleep']['parent_seconds'], s['sleep']['sessions'], s['sleep']['longest_seconds']), (3600, 2, 3600))
        self.assertEqual((s['diaper']['count'], s['diaper']['dirty'], s['interaction']['favorite']), (1, 1, '小手拍拍'))
        text = ''.join(r['highlights'])
        for expected in ('2 次喂奶，共 210 ml，最近一次在 13:00，平均间隔约 4 小时', '睡眠共 1 小时（2 段', '其中 1 次有便便', '「小手拍拍」时最开心'):
            self.assertIn(expected, text)
        self.assertEqual([d['date'] for d in r['trend']][-1], DAY)
        self.assertEqual([d['feeding_count'] for d in r['trend']], [0, 0, 0, 0, 0, 0, 2])
        self.assertEqual((r['narrative'], r['narrative_status'], self.model.calls), (None, 'not_requested', 0))
        self.assertEqual(r['suggestions'], [])

    def test_narrative_ok_and_cached(self):
        r = self.build(True)
        self.assertEqual((r['narrative_status'], r['narrative']), ('ok', self.model.answer))
        self.build(True)
        self.assertEqual(self.model.calls, 1)

    def test_narrative_with_invented_number_rejected(self):
        self.model.answer = '宝宝今天喝了 999 ml 奶。'
        r = self.build(True)
        self.assertEqual((r['narrative'], r['narrative_status']), (None, 'rejected'))
        self.assertTrue(r['highlights'])

    def test_model_failure_keeps_python_summary(self):
        self.model.error = AssistantError('model_timeout', 504, retryable=True)
        r = self.build(True)
        self.assertEqual((r['narrative'], r['narrative_status']), (None, 'model_timeout'))
        self.assertIn('2 次喂奶', r['highlights'][0])

    def test_empty_day_and_scope_isolation(self):
        r = self.build(scope='real')
        self.assertTrue(r['highlights'][0].startswith('今天还没有记录'))
        self.assertEqual(len(r['suggestions']), 3)
        with self.assertRaises(AssistantError):
            self.summary.build({'scope': 'demo', 'from': '2026-10-11', 'to': DAY}, False)


class SummaryHTTPTest(unittest.TestCase):
    setUpClass = classmethod(base.AssistantHTTPTest.setUpClass.__func__)
    setUp = base.AssistantHTTPTest.setUp
    tearDown = base.AssistantHTTPTest.tearDown

    def get(self, path):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        c.request('GET', path, headers={'Cookie': 'bmon_session=' + self.token})
        r = c.getresponse(); out = (r.status, json.loads(r.read())); c.close(); return out

    def test_route(self):
        status, body = self.get(f'/api/summary/daily?scope=demo&date={DAY}&timezone=Asia%2FShanghai')
        self.assertEqual((status, body['date'], len(body['trend'])), (200, DAY, 7))
        status, body = self.get(f'/api/summary/daily?scope=demo&date={DAY}&narrative=1')
        self.assertEqual((status, body['narrative_status']), (200, 'ok'))
        self.assertEqual(self.get(f'/api/summary/daily?scope=demo&date={DAY}&narrative=2')[0], 422)
        self.assertEqual(self.get(f'/api/summary/daily?scope=demo&from=2026-10-11&to={DAY}')[0], 422)


if __name__ == '__main__':
    unittest.main()
