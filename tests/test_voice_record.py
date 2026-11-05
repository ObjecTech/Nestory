"""Spoken records: "佩奇，记一下…" reaches the AI assistant; music commands keep working."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from baby_monitor.assistant_service import AssistantService
from baby_monitor.voice_control import VoiceControl
from test_assistant import FIXED, FakeModel


class FakePlayer:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"volume": 25, "categories": [{"name": "儿歌"}]}

    def play_category(self, name):
        self.calls.append(("play", name))
        return {}

    def play_feedback(self, path, title):
        self.calls.append(("feedback", title))


class VoiceRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.records, self.player = [], FakePlayer()
        self.voice = VoiceControl(player=self.player, settings_path=root / 'voice.json', worker_python=root / 'python',
                                  model_dir=root / 'kws', keywords_file=root / 'kw.txt', audio_device='plughw:0,0',
                                  default_enabled=True, now_factory=lambda: datetime(2026, 10, 13, 12, 0),
                                  on_record=lambda text: self.records.append(text) or {"message": "已记录"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefixed_record_converts_spoken_amount(self):
        result = self.voice.execute_phrase('佩奇记一下宝宝上午九点喝了一百五十毫升奶')
        self.assertEqual(self.records, ['宝宝上午九点喝了150毫升奶'])
        self.assertEqual((result['intent']['type'], result['message']), ('record', '已记录'))

    def test_care_sentence_without_prefix_falls_back_to_record(self):
        self.voice.execute_phrase('宝宝刚换了尿布')
        self.assertEqual(self.records, ['宝宝刚换了尿布'])

    def test_please_record_prefix_is_a_record(self):
        self.voice.execute_phrase('请记录宝宝今天上午十一点哭了')
        self.assertEqual(self.records, ['宝宝今天上午十一点哭了'])

    def test_clipped_record_prefix_is_a_record(self):
        # 2026-10-21 实机：ASR 把“记一下宝宝现在喝了…”听成“一下宝宝现在喝了…”
        self.voice.execute_phrase('一下宝宝现在喝了一百五十毫升奶')
        self.assertEqual(self.records, ['宝宝现在喝了150毫升奶'])

    def test_generic_play_words_start_a_category(self):
        for phrase in ('播放', '听音乐', '音乐'):
            result = self.voice.execute_phrase(phrase)
            self.assertEqual(result['intent']['type'], 'play')
        self.assertEqual(self.player.calls, [('play', '儿歌')] * 3)

    def test_generic_play_resumes_when_paused(self):
        self.player.status = lambda: {"volume": 25, "state": "paused", "category": "儿歌",
                                      "categories": [{"name": "儿歌"}]}
        self.player.control = lambda action: self.player.calls.append(("control", action)) or {}
        self.assertEqual(self.voice.execute_phrase('播放')['message'], '继续播放')
        self.assertEqual(self.player.calls, [('control', 'resume')])

    def test_music_commands_and_unknown_phrases_unchanged(self):
        self.voice.execute_phrase('佩奇播放儿歌')
        self.assertEqual(self.player.calls, [('play', '儿歌')])
        with self.assertRaises(ValueError):
            self.voice.execute_phrase('今天天气怎么样')
        self.assertEqual(self.records, [])


class AssistantVoiceRecordTest(unittest.TestCase):
    def setUp(self):
        for target in ('baby_monitor.assistant_database.now_ms', 'baby_monitor.assistant_common.now_ms'):
            clock = patch(target, return_value=FIXED)
            clock.start(); self.addCleanup(clock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.service = AssistantService(Path(self.tmp.name) / 'assistant.db', model=FakeModel())

    def tearDown(self):
        self.service.close(); self.tmp.cleanup()

    def test_spoken_feeding_saved_to_real_voice_conversation(self):
        first = self.service.voice_record('宝宝上午9点喝了150毫升奶')
        second = self.service.voice_record('刚换了尿布，是尿湿的')
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(first['record_actions'][0]['status'], 'saved')
        self.assertEqual(first['record_actions'][0]['event']['amount_ml'], 150)
        self.assertEqual(first['conversation']['id'], second['conversation']['id'])
        conversations = self.service.database.conversations({'scope': 'real'})['conversations']
        self.assertEqual([c['title'] for c in conversations], ['语音记录'])


if __name__ == '__main__':
    unittest.main()
