import unittest

from baby_monitor.model_prompt import observation_prompt, INFANT_PROMPT, ADULT_PROMPT, COMMON_PROMPT


class PersonPromptTest(unittest.TestCase):
    def test_default_and_restoration_preserve_baby_prompt(self):
        original = "没有宝宝时 baby_visible=false；状态 unknown。"
        self.assertEqual(observation_prompt(original), original)
        person = observation_prompt(original, True)
        self.assertEqual(person, ADULT_PROMPT)
        self.assertTrue(person.startswith(COMMON_PROMPT))
        self.assertTrue(INFANT_PROMPT.startswith(COMMON_PROMPT))
        self.assertNotIn(original, person)
        self.assertIn("unknown", person)
        self.assertIn("测试成人", person)
        self.assertNotIn("INFANT MODE", person)
        self.assertEqual(observation_prompt(original, False), original)
