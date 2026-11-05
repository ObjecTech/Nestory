"""Night sleep uses confirmed interval unions inside local 20:00–next-day 10:00."""
import unittest
from unittest.mock import patch

from baby_monitor.assistant_common import AssistantError, sleep_window, timestamp
import test_assistant as store_base
import test_assistant_http as http_base
import test_assistant_rules as rule_base


class NightFactsTest(unittest.TestCase):
    setUp = store_base.FactsTest.setUp
    tearDown = store_base.FactsTest.tearDown
    sleep = store_base.FactsTest.sleep

    def query(self):
        return dict(scope='demo', timezone='Asia/Shanghai', period='night',
                    **{'from': '2026-10-12', 'to': '2026-10-12'})

    def test_clip_both_boundaries_union_overlap_and_exclude_awake_gaps(self):
        # Local times: 19–21, 20:30–22, 23–06 and 09–11 => 2 + 7 + 1 hours.
        for client, start, end in (
            ('a', '2026-10-12T11:00:00Z', '2026-10-12T13:00:00Z'),
            ('b', '2026-10-12T12:30:00Z', '2026-10-12T14:00:00Z'),
            ('c', '2026-10-12T15:00:00Z', '2026-10-12T22:00:00Z'),
            ('d', '2026-10-13T01:00:00Z', '2026-10-13T03:00:00Z'),
            ('before', '2026-10-12T02:00:00Z', '2026-10-12T12:00:00Z'),
            ('after', '2026-10-13T02:00:00Z', '2026-10-13T04:00:00Z'),
        ):
            self.sleep(start, end, client)
        before = [tuple(r) for r in self.db.connection.execute('SELECT * FROM care_events ORDER BY id')]
        result = self.facts.sleep_summary(self.query())
        self.assertEqual(result['parent_sleep']['seconds'], 10 * 3600)
        self.assertEqual(result['parent_sleep']['event_count'], 4)
        self.assertEqual(result['window']['start_at'], '2026-10-12T12:00:00.000Z')
        self.assertEqual(result['window']['end_at'], '2026-10-13T02:00:00.000Z')
        self.assertIsNone(result['camera_sleep']['seconds'])
        self.assertIsNone(result['combined_sleep_seconds'])
        self.assertEqual(before, [tuple(r) for r in self.db.connection.execute('SELECT * FROM care_events ORDER BY id')])

    def test_scope_and_deleted_records_stay_excluded(self):
        self.sleep('2026-10-12T12:00:00Z', '2026-10-13T02:00:00Z', 'real', 'real')
        self.sleep('2026-10-12T13:00:00Z', '2026-10-12T14:00:00Z', 'deleted')
        event = self.db.events('demo', 0, 2**62, {})['events'][0]
        self.db.delete_event(event['id'], 'demo', event['version'])
        self.assertIsNone(self.facts.sleep_summary(self.query())['parent_sleep']['seconds'])
        real = self.query(); real['scope'] = 'real'
        self.assertEqual(self.facts.sleep_summary(real)['parent_sleep']['seconds'], 14 * 3600)

    def test_unfinished_window_and_no_records_remain_unknown(self):
        with patch('baby_monitor.assistant_facts.now_ms', return_value=timestamp('2026-10-12T23:00:00Z')):
            result = self.facts.sleep_summary(self.query())
        self.assertFalse(result['window']['has_ended'])
        self.assertIsNone(result['parent_sleep']['seconds'])
        self.assertEqual(result['parent_sleep']['reliability'], 'insufficient_data')
        self.assertIn('尚未结束', ''.join(result['warnings']))
        self.assertIn('没有已确认', self.facts.explanation(result))

    def test_query_passes_exact_night_window_to_visual_evidence(self):
        intent = dict(kind='sleep_summary', period='night', **{'from': '2026-10-12', 'to': '2026-10-12'})
        with patch.object(self.facts, 'visual_evidence', return_value={}) as visual:
            result = self.facts.query(intent, 'real', 'Asia/Shanghai')
        visual.assert_called_once_with(timestamp(result['window']['start_at']), timestamp(result['window']['end_at']))
        self.assertEqual(result['period'], 'night')

    def test_invalid_period_or_multiple_nights_rejected(self):
        for changes in ({'period': 'unknown'}, {'to': '2026-10-13'}):
            query = self.query(); query.update(changes)
            with self.assertRaises(AssistantError): self.facts.sleep_summary(query)

    def test_night_uses_local_clock_across_dst(self):
        for day, hours in (('2026-10-31', 15), ('2026-03-07', 13)):
            _, _, _, start, end = sleep_window(dict(period='night', timezone='America/New_York', **{'from': day, 'to': day}))
            self.assertEqual(end - start, hours * 3600000)


class NightChatTest(unittest.TestCase):
    setUp = rule_base.RuleFirstTest.setUp
    tearDown = rule_base.RuleFirstTest.tearDown
    send = rule_base.RuleFirstTest.send
    events = rule_base.RuleFirstTest.events

    def test_record_then_cross_conversation_night_query_and_calendar_day(self):
        saved = self.send('昨晚9点到今天早上6点宝宝睡了一觉')
        self.assertEqual(saved['record_actions'][0]['event']['duration_seconds'], 9 * 3600)
        self.cid = self.db.create_conversation({'scope': 'demo'})['conversation']['id']
        for text in ('宝宝昨晚睡了多久？', '宝宝昨天晚上睡了几个小时？'):
            result = self.send(text)
            self.assertEqual(result['facts']['parent_sleep']['seconds'], 9 * 3600)
            self.assertIn('9 小时', result['assistant_message']['content'])
            self.assertIn('10月12日 20:00 至 10月13日 10:00', result['assistant_message']['content'])
            self.assertEqual(result['record_actions'], [])
        result = self.send('宝宝昨天睡了多久？')
        self.assertEqual(result['facts']['parent_sleep']['seconds'], 3 * 3600)
        self.assertNotIn('period', result['facts'])
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.model.calls, [])

    def test_previous_night_phrase_uses_previous_start_date(self):
        result = self.send('宝宝前晚睡了多久？')
        self.assertEqual(result['facts']['from'], '2026-10-11')
        self.assertEqual(result['facts']['window']['end_at'], '2026-10-12T02:00:00.000Z')
        self.assertIsNone(result['facts']['parent_sleep']['seconds'])


class NightHTTPTest(unittest.TestCase):
    setUpClass = classmethod(http_base.AssistantHTTPTest.setUpClass.__func__)
    setUp = http_base.AssistantHTTPTest.setUp
    tearDown = http_base.AssistantHTTPTest.tearDown
    request = http_base.AssistantHTTPTest.request

    def test_night_summary_is_available_through_authenticated_route(self):
        self.service.database.create_event(dict(scope='demo', event_type='sleep', timezone='Asia/Shanghai',
            occurred_at='2026-10-12T13:00:00Z', end_at='2026-10-12T22:00:00Z', client_event_id='night'))
        path = '/sleep-summary?scope=demo&from=2026-10-12&to=2026-10-12&period=night'
        status, _, result = self.request('GET', path)
        self.assertEqual(status, 200)
        self.assertEqual(result['parent_sleep']['seconds'], 9 * 3600)
        self.assertEqual(self.request('GET', path, auth=False)[0], 401)
        self.assertEqual(self.request('GET', path.replace('period=night', 'period=invalid'))[0], 422)


if __name__ == '__main__':
    unittest.main()
