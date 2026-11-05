"""规则优先记录/查询回归：样本来自 2026-10-15 真实 2B 维护测试与验收清单，全部为合成 Demo 数据。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from baby_monitor.assistant_service import AssistantService
from test_assistant import FIXED, FakeModel

# FIXED = 2026-10-13T08:00Z，即上海时间 16:00


class RuleFirstTest(unittest.TestCase):
    def setUp(self):
        for target in ('baby_monitor.assistant_database.now_ms', 'baby_monitor.assistant_common.now_ms'):
            clock = patch(target, return_value=FIXED)
            clock.start(); self.addCleanup(clock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.model = FakeModel()
        self.service = AssistantService(Path(self.tmp.name) / 'assistant.db', model=self.model)
        self.db = self.service.database
        self.cid = self.db.create_conversation({'scope': 'demo'})['conversation']['id']
        self.n = 0

    def tearDown(self):
        self.service.close(); self.tmp.cleanup()

    def send(self, content, **extra):
        self.n += 1
        payload = dict(content=content, client_message_id=f'c{self.n}', timezone='Asia/Shanghai', **extra)
        result, _ = self.service.send(self.cid, 'demo', payload)
        self.service.queue.join()
        return self.db.poll(self.cid, result['user_message']['id'], 'demo')

    def events(self):
        return self.db.connection.execute(
            "SELECT event_type,occurred_at,end_at,amount_ml,diaper_type FROM care_events WHERE status='confirmed' ORDER BY occurred_at").fetchall()

    def test_real_2b_failure_sample_saved_without_model(self):
        r = self.send('今天上午11点宝宝喝了150ml奶。')
        card = r['record_actions'][0]
        self.assertEqual((r['status'], card['status']), ('completed', 'saved'))
        self.assertEqual(card['event']['amount_ml'], 150)
        self.assertTrue(card['event']['occurred_at'].startswith('2026-10-13T03:00:00'))
        self.assertEqual(self.model.calls, [])

    def test_ambiguous_feeding_asks_then_supplement_saves(self):
        r = self.send('宝宝刚才喝了点奶')
        card = r['record_actions'][0]
        self.assertEqual(card['status'], 'needs_confirmation')
        self.assertEqual(set(card['missing_fields']), {'occurred_at', 'amount_ml'})
        self.assertEqual(self.events(), [])
        r = self.send('下午3点喝了90ml', record_action_id=card['action_id'])
        self.assertEqual(r['record_actions'][0]['status'], 'saved')
        self.assertEqual(self.events()[0][3], 90)
        self.assertEqual(self.model.calls, [])

    def test_sleep_diaper_and_multi_event(self):
        self.assertEqual(self.send('下午1点半到2点20分补觉')['record_actions'][0]['status'], 'saved')
        self.assertEqual(self.send('下午两点给宝宝换了纸尿裤，有便便')['record_actions'][0]['status'], 'saved')
        cards = self.send('上午9点喝了120ml奶，上午10点换了尿布')['record_actions']
        self.assertEqual([c['status'] for c in cards], ['saved', 'saved'])
        kinds = sorted(e[0] for e in self.events())
        self.assertEqual(kinds, ['diaper', 'diaper', 'feeding', 'sleep'])
        sleep = [e for e in self.events() if e[0] == 'sleep'][0]
        self.assertEqual(sleep[2] - sleep[1], 50 * 60 * 1000)

    def test_future_negated_and_adult_not_saved(self):
        for text in ('明天上午9点给宝宝喂奶', '宝宝今天还没喝奶', '我下午2点喝了一杯奶茶'):
            self.assertEqual(self.send(text)['record_actions'][0]['status'], 'not_saved', text)
        self.assertEqual(self.events(), [])

    def test_queries_use_python_facts_and_cross_conversation(self):
        self.send('今天上午11点宝宝喝了150ml奶')
        self.cid = self.db.create_conversation({'scope': 'demo'})['conversation']['id']
        r = self.send('今天宝宝喝了多少奶？')
        self.assertEqual((r['facts']['feeding_count'], r['facts']['parent_reported_ml']), (1, 150))
        self.assertIn('150 ml', r['assistant_message']['content'])
        self.assertEqual(r['record_actions'], [])
        r = self.send('昨天睡了多久')
        self.assertEqual(r['facts']['from'], '2026-10-12')
        self.assertEqual(self.model.calls, [])

    def test_now_means_the_message_time(self):
        # 2026-10-21 实机：“宝宝现在喝了150毫升奶”被要求确认时间
        r = self.send('宝宝现在喝了150毫升奶')
        self.assertEqual(r['record_actions'][0]['status'], 'saved')
        self.assertEqual(self.events()[0][2:4], (None, 150))
        self.assertEqual(self.model.calls, [])

    def test_when_questions_about_past_events_use_records(self):
        # 2026-10-21 实机：“孩子今天什么时候哭了”进了模型聊天，模型编出“早上7点哭了”
        r = self.send('孩子今天什么时候哭了')
        self.assertIn('cry', r['facts'])
        r = self.send('宝宝几点喝的奶')
        self.assertIn('feeding_count', r['facts'])
        self.assertEqual(self.model.calls, [])
        self.send('宝宝什么时候睡觉比较好？')
        self.assertEqual(len(self.model.calls), 1)

    def test_advice_and_chat_go_to_model(self):
        # “午睡”等词出现在求建议的句子里时不能当成记录（2026-10-15 实机样本）
        for text in ('宝宝睡不好怎么办？', '你好', '给我一个哄宝宝午睡的小建议'):
            self.send(text)
        self.assertEqual(len(self.model.calls), 3)
        self.assertTrue(all(c['max_new_tokens'] == 64 for c in self.model.calls))
        self.assertEqual(self.events(), [])

    def test_delete_previous_record_requires_confirmation(self):
        self.send('今天上午11点宝宝喝了150ml奶')
        card = self.send('删除上一条')['record_actions'][0]
        self.assertEqual((card['operation'], card['status']), ('delete', 'needs_confirmation'))
        self.assertIsNotNone(card['target_event_id'])
        self.assertEqual(len(self.events()), 1)


if __name__ == '__main__':
    unittest.main()
