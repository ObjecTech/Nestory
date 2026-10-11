import unittest

from baby_monitor.model_prompt import observation_prompt


class PersonPromptTest(unittest.TestCase):
    def test_default_and_restoration_preserve_baby_prompt(self):
        original = "没有宝宝时 baby_visible=false；状态 unknown。"
        self.assertEqual(observation_prompt(original), original)
        person = observation_prompt(original, True)
        self.assertIn("没有人时 baby_visible=false", person)
        self.assertIn("unknown", person)
        self.assertIn("包括成人", person)
        self.assertNotIn("宝宝", person)
        self.assertEqual(observation_prompt(original, False), original)
