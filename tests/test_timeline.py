"""统一时间线：视觉区间/事件＋家长记录＋互动记录的合并、范围隔离与汇总。全部为合成数据。"""
import http.client
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from baby_monitor.assistant_common import date_window
from baby_monitor.assistant_database import AssistantDatabase
from baby_monitor.assistant_facts import AssistantFacts
from baby_monitor.database import Database
import test_assistant_http as base  # 只复用夹具


def ts(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


DAY = '2026-10-12'  # 上海时间当天 = 2026-10-11T16:00Z 至 2026-10-12T16:00Z


class TimelineFacadeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        Database(root / 'baby_monitor.db', 'infant')  # 用真实视觉库建表
        c = sqlite3.connect(root / 'baby_monitor.db')
        rows = [('activity', 'sleeping', '2026-10-12T02:00:00Z', '2026-10-12T03:00:00Z', 'infant'),
                ('activity', 'awake', '2026-10-12T03:00:00Z', '2026-10-12T03:30:00Z', 'infant'),
                ('activity', 'crying', '2026-10-11T15:00:00Z', '2026-10-11T17:00:00Z', 'infant'),  # 跨日界，裁剪
                ('activity', 'awake', '2026-10-12T04:00:00Z', '2026-10-12T05:00:00Z', 'adult_test'),
                ('posture', 'supine', '2026-10-12T02:00:00Z', '2026-10-12T02:20:00Z', 'legacy_unknown')]
        for cat, value, s, e, mode in rows:
            c.execute('INSERT INTO intervals(category,value,start_ts,end_ts,source,confidence,note,observation_mode) VALUES(?,?,?,?,?,?,?,?)',
                      (cat, value, ts(s), ts(e), 'vlm', .8, None, mode))
        c.execute("INSERT INTO events(event_type,ts,activity,observation_mode) VALUES('smile',?,'awake_happy','infant')", (ts('2026-10-12T03:10:00Z'),))
        c.execute("INSERT INTO events(event_type,ts,activity,observation_mode) VALUES('smile',?,'awake_happy','adult_test')", (ts('2026-10-12T04:10:00Z'),))
        c.commit(); c.close()
        self.db = AssistantDatabase(root / 'assistant.db')
        self.facts = AssistantFacts(self.db, root / 'baby_monitor.db')
        for i, (scope, kind, at, end, ml) in enumerate([
                ('real', 'feeding', '2026-10-12T01:00:00Z', None, 120), ('real', 'feeding', '2026-10-12T05:00:00Z', None, 90),
                ('real', 'sleep', '2026-10-12T06:00:00Z', '2026-10-12T07:00:00Z', None),
                ('real', 'sleep', '2026-10-12T06:30:00Z', '2026-10-12T07:30:00Z', None),  # 重叠，取并集 90 分钟
                ('demo', 'feeding', '2026-10-12T01:30:00Z', None, 150)]):
            self.db.create_event(dict(scope=scope, client_event_id=f'e{i}', event_type=kind, timezone='Asia/Shanghai',
                                      occurred_at=at, end_at=end, amount_ml=ml))
        self.db.create_interaction(dict(client_id='p1', scope='real', activity_id='fp-01', kind='finger_play', title='小手拍拍',
                                        started_at='2026-10-12T03:15:00Z', ended_at='2026-10-12T03:20:00Z', reaction='happy'))

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def timeline(self, scope):
        _, _, tz, start, end = date_window({'from': DAY, 'to': DAY, 'timezone': 'Asia/Shanghai'})
        return self.facts.unified_timeline(scope, start, end, tz)

    def test_real_merges_sources_sorted_and_infant_only(self):
        t = self.timeline('real')
        self.assertTrue(t['visual']['available']); self.assertEqual(t['visual']['observation_mode'], 'infant')
        starts = [i['start_at'] for i in t['items']]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual({i['source'] for i in t['items']}, {'vision', 'parent_manual', 'interaction'})
        self.assertFalse(any(i.get('observation_mode') in ('adult_test', 'legacy_unknown') for i in t['items']))
        crying = next(i for i in t['items'] if i['type'] == 'crying')
        self.assertEqual((crying['start_at'][:19], crying['duration_seconds']), ('2026-10-11T16:00:00', 3600))
        self.assertIn('笑了', [i['label'] for i in t['items']])
        self.assertIn('喂奶 120 ml', [i['label'] for i in t['items']])

    def test_real_follows_current_observation_mode(self):
        mode = {'value': 'adult_test'}
        self.facts = AssistantFacts(self.db, Path(self.tmp.name) / 'baby_monitor.db', lambda: mode['value'])
        t = self.timeline('real')
        self.assertEqual(t['visual']['observation_mode'], 'adult_test')
        self.assertEqual({i['observation_mode'] for i in t['items'] if i['source'] == 'vision'}, {'adult_test'})
        self.assertIn('喂奶 120 ml', [i['label'] for i in t['items']])  # 家长记录仍是同一套 real
        mode['value'] = 'infant'
        self.assertEqual({i['observation_mode'] for i in self.timeline('real')['items'] if i['source'] == 'vision'}, {'infant'})

    def test_summary_union_and_unverified_visual(self):
        s = self.timeline('real')['summary']
        self.assertEqual((s['feeding_count'], s['feeding_ml'], s['parent_sleep_seconds'], s['interaction_count']), (2, 210, 5400, 1))
        self.assertEqual(s['visual_activity_seconds'], {'crying': 3600, 'sleeping': 3600, 'awake': 1800})
        self.assertEqual(s['visual_reliability'], 'unverified')

    def test_demo_isolated_and_uses_adult_test_visual(self):
        t = self.timeline('demo')
        self.assertEqual(t['visual']['observation_mode'], 'adult_test')
        care = [i for i in t['items'] if i['category'] == 'care']
        self.assertEqual([i['amount_ml'] for i in care], [150])
        self.assertEqual([i['category'] for i in t['items'] if i['source'] == 'interaction'], [])
        self.assertEqual({i['observation_mode'] for i in t['items'] if i['source'] == 'vision'}, {'adult_test'})

    def test_deleted_care_excluded_and_missing_visual_db(self):
        event = next(e for e in self.db.events('real', 0, 2**62, {'limit': '100'})['events'] if e['event_type'] == 'feeding')
        self.db.delete_event(event['id'], 'real', event['version'])
        self.assertEqual(self.timeline('real')['summary']['feeding_count'], 1)
        facts = AssistantFacts(self.db, Path(self.tmp.name) / 'missing.db')
        _, _, tz, start, end = date_window({'from': DAY, 'to': DAY})
        t = facts.unified_timeline('real', start, end, tz)
        self.assertEqual((t['visual']['available'], t['visual']['reason_code']), (False, 'visual_data_unavailable'))


class TimelineHTTPTest(unittest.TestCase):
    setUpClass = classmethod(base.AssistantHTTPTest.setUpClass.__func__)
    setUp = base.AssistantHTTPTest.setUp
    tearDown = base.AssistantHTTPTest.tearDown

    def call(self, method, path, auth=True):
        h = {'Origin': self.origin, 'X-CSRF-Token': self.csrf}
        if auth: h['Cookie'] = 'bmon_session=' + self.token
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3); c.request(method, path, headers=h)
        r = c.getresponse(); out = (r.status, json.loads(r.read())); c.close(); return out

    def test_route_auth_and_shape(self):
        status, body = self.call('GET', f'/api/timeline/unified?scope=demo&date={DAY}&timezone=Asia%2FShanghai')
        self.assertEqual(status, 200)
        self.assertEqual((body['from'], body['to'], body['items'], body['visual']['available']), (DAY, DAY, [], False))
        self.assertEqual(self.call('GET', '/api/timeline/unified?scope=demo', auth=False)[0], 401)
        self.assertEqual(self.call('POST', '/api/timeline/unified?scope=demo')[0], 405)
        self.assertEqual(self.call('GET', f'/api/timeline/unified?scope=other&date={DAY}')[0], 422)
        self.assertEqual(self.call('GET', f'/api/timeline/unified?scope=demo&date={DAY}&from={DAY}')[0], 422)


if __name__ == '__main__':
    unittest.main()
