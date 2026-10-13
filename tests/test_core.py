from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from access_control import AccessStateStore
from baby_monitor.app import BabyMonitorApp
from baby_monitor.camera import Frame
from baby_monitor.database import Database
from baby_monitor.inference import TemporalStateFilter, classify_activity
from baby_monitor.play_with_me import MediaTrack, PlayWithMePlayer, _display_title
from baby_monitor.voice_control import (
    VoiceControl,
    is_within_listen_schedule,
    parse_voice_command,
)
from baby_monitor.vlm import VLMObserver, activity_from_observation, normalize_observation


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "test.db")

    def tearDown(self):
        self.database.close()
        self.tempdir.cleanup()

    def test_initial_intervals_exist(self):
        current = self.database.current()
        self.assertEqual(current["activity"]["value"], "unknown")
        self.assertEqual(current["posture"]["value"], "unknown")
        self.assertEqual(current["presence"]["value"], "unknown")

    def test_transition_closes_previous_interval(self):
        changed, previous = self.database.transition("activity", "sleeping", "test")
        self.assertTrue(changed)
        self.assertEqual(previous, "unknown")
        self.assertEqual(self.database.current()["activity"]["value"], "sleeping")
        rows = self.database.intervals(time.time() - 60, time.time() + 60)
        self.assertTrue(any(row["value"] == "sleeping" for row in rows))

    def test_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            self.database.transition("posture", "upside_down", "test")

    def test_event_counts_group_by_activity(self):
        now = time.time()
        self.database.add_event("classified_photo", now, "sleeping", None, 0.8)
        self.database.add_event("classified_photo", now + 1, "sleeping", None, 0.8)
        self.database.add_event("classified_photo", now + 2, "awake", None, 0.7)
        self.database.add_event("smile", now + 3, "awake_happy", None, 0.9)
        counts = self.database.event_counts("classified_photo", now - 1, now + 10)
        self.assertEqual(counts, {"awake": 1, "sleeping": 2})

    def test_events_by_types_and_trash_marker(self):
        now = time.time()
        photo_id = self.database.add_event(
            "classified_photo", now, "sleeping", "left_side", 0.9,
            original_path="classified/example.jpg",
        )
        self.database.add_event("unrelated", now + 1, None, None, None)
        items = self.database.events_by_types(("smile", "classified_photo"))
        self.assertEqual([item["id"] for item in items], [photo_id])
        self.assertTrue(
            self.database.mark_event_trashed(
                photo_id,
                "classified_photo",
                "_trash/example.jpg",
                None,
                "{}",
            )
        )
        self.assertEqual(self.database.event(photo_id)["event_type"], "photo_trashed")

    def test_observations_round_trip_flexible_state(self):
        observation = normalize_observation(
            {
                "baby_visible": True,
                "visibility": "visible",
                "sleep_state": "awake",
                "posture": "supine",
                "expression": "neutral",
                "activity_level": "low",
                "confidence": 0.81,
                "posture_confidence": 0.72,
                "visibility_confidence": 0.94,
                "suggested_state": "安静观察四周",
                "observed_actions": ["睁眼", "轻微转头"],
                "description": "宝宝睁眼并轻微转头。",
            }
        )
        observation_id = self.database.add_observation(
            time.time(), "test-vlm", observation
        )
        item = self.database.observations(1)[0]
        self.assertEqual(item["id"], observation_id)
        self.assertEqual(item["suggested_state"], "安静观察四周")
        self.assertEqual(item["observed_actions"], ["睁眼", "轻微转头"])
        self.assertEqual(item["posture_confidence"], 0.72)
        self.assertEqual(item["visibility_confidence"], 0.94)


class VLMObservationTest(unittest.TestCase):
    def test_sitting_and_standing_descriptions_cannot_become_lying_postures(self):
        for description, posture in [
            ("一名男性坐在椅子上，面部清晰可见。", "supine"),
            ("一名男性侧身站立，手扶着头。", "side"),
        ]:
            with self.subTest(description=description):
                item = normalize_observation({
                    "baby_visible": True, "visibility": "visible",
                    "sleep_state": "awake", "posture": posture,
                    "posture_confidence": 0.95, "confidence": 0.95,
                    "suggested_state": "清醒侧卧", "description": description,
                })
                self.assertEqual(item["sleep_state"], "awake")
                self.assertEqual(item["posture"], "unknown")
                self.assertEqual(item["posture_confidence"], 0.0)
                self.assertEqual(item["suggested_state"], "")

    def test_stable_sitting_clears_previous_lying_posture(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.transition("posture", "side", "test")
            observer = VLMObserver(
                camera=SimpleNamespace(motion_score=0.0), database=database,
                enabled=True, service_url="http://127.0.0.1:1",
                interval_seconds=30, sleep_interval_seconds=60,
                motion_interval_seconds=15, timeout_seconds=1,
                confidence_threshold=0.6, stable_seconds=0, motion_threshold=8,
                on_transition=lambda category, value, source, confidence, note, _frame=None:
                    database.transition(category, value, source, confidence, note),
                on_observation=lambda _observation: None,
            )
            seated = normalize_observation({
                "baby_visible": True, "visibility": "visible",
                "sleep_state": "awake", "posture": "side",
                "confidence": 0.95, "posture_confidence": 0.95,
                "description": "一名男性坐在椅子上。",
            })
            observer._apply(seated, {"model": "test"})
            self.assertEqual(database.current()["posture"]["value"], "side")
            observer._apply(seated, {"model": "test"})
            self.assertEqual(database.current()["posture"]["value"], "unknown")
            database.close()

    def test_absent_baby_forces_all_state_fields_unknown(self):
        item = normalize_observation(
            {
                "baby_visible": False,
                "visibility": "not_visible",
                "sleep_state": "sleeping",
                "posture": "supine",
                "expression": "happy",
                "activity_level": "still",
                "confidence": 0.99,
                "posture_confidence": 0.99,
            }
        )
        self.assertFalse(item["baby_visible"])
        self.assertEqual(item["sleep_state"], "unknown")
        self.assertEqual(item["posture"], "unknown")
        self.assertEqual(item["expression"], "unknown")
        self.assertEqual(activity_from_observation(item), "unknown")
        self.assertEqual(item["visibility_confidence"], 0.75)

    def test_conflicting_visibility_is_not_trusted(self):
        item = normalize_observation(
            {
                "baby_visible": False,
                "visibility": "visible",
                "sleep_state": "sleeping",
                "posture": "left_side",
            }
        )
        self.assertIsNone(item["baby_visible"])
        self.assertEqual(item["visibility"], "unknown")
        self.assertEqual(item["sleep_state"], "unknown")

    def test_prior_categories_are_normalized_but_free_label_is_preserved(self):
        item = normalize_observation(
            {
                "baby_visible": True,
                "visibility": "visible",
                "sleep_state": "awake",
                "posture": "supine",
                "expression": "happy",
                "suggested_state": "醒着并好奇地观察摄像头",
            }
        )
        self.assertEqual(activity_from_observation(item), "awake")
        self.assertEqual(item["suggested_state"], "醒着并好奇地观察摄像头")
        self.assertEqual(
            normalize_observation(
                {
                    "baby_visible": True,
                    "visibility": "visible",
                    "posture": "left_side",
                }
            )["posture"],
            "side",
        )

    def test_stable_absence_clears_previous_activity_and_posture(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.transition("activity", "sleeping", "test")
            database.transition("posture", "left_side", "test")
            observer = VLMObserver(
                camera=SimpleNamespace(motion_score=0.0),
                database=database,
                enabled=True,
                service_url="http://127.0.0.1:1",
                interval_seconds=30,
                sleep_interval_seconds=60,
                motion_interval_seconds=15,
                timeout_seconds=1,
                confidence_threshold=0.6,
                stable_seconds=0,
                motion_threshold=8,
                on_transition=lambda category, value, source, confidence, note, _frame=None: database.transition(
                    category, value, source, confidence, note
                ),
                on_observation=lambda _observation: None,
            )
            absent = normalize_observation(
                {
                    "baby_visible": False,
                    "visibility": "not_visible",
                    "visibility_confidence": 0.95,
                    "suggested_state": "未见宝宝",
                    "description": "床上没有宝宝。",
                }
            )
            envelope = {"model": "test"}
            observer._apply(absent, envelope)
            self.assertEqual(database.current()["activity"]["value"], "sleeping")
            observer._apply(absent, envelope)
            current = database.current()
            self.assertEqual(current["presence"]["value"], "not_visible")
            self.assertEqual(current["activity"]["value"], "unknown")
            self.assertEqual(current["posture"]["value"], "unknown")
            observer._apply(absent, envelope)
            self.assertEqual(len(database.observations()), 1)
            database.close()

    def test_expression_photo_uses_the_analyzed_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            captured = []
            frame = Frame(time.time(), b"analyzed-jpeg", 88.0)
            observer = VLMObserver(
                camera=SimpleNamespace(motion_score=0.0),
                database=database,
                enabled=True,
                service_url="http://127.0.0.1:1",
                interval_seconds=30,
                sleep_interval_seconds=60,
                motion_interval_seconds=15,
                timeout_seconds=1,
                confidence_threshold=0.6,
                stable_seconds=0,
                motion_threshold=8,
                on_transition=lambda *_args: None,
                on_observation=lambda _observation: None,
                on_expression=lambda expression, confidence, reason, captured_frame: captured.append(
                    (expression, confidence, reason, captured_frame)
                ),
            )
            observation = normalize_observation(
                {
                    "baby_visible": True,
                    "visibility": "visible",
                    "sleep_state": "awake",
                    "posture": "side",
                    "expression": "happy",
                    "confidence": 0.9,
                    "expression_confidence": 0.91,
                    "posture_confidence": 0.85,
                    "visibility_confidence": 0.95,
                    "description": "宝宝清楚地微笑。",
                }
            )
            observer._apply(observation, {"model": "test"}, frame)
            self.assertEqual(captured[0][0], "happy")
            self.assertIs(captured[0][3], frame)
            database.close()


class TemporalStateFilterTest(unittest.TestCase):
    def test_requires_time_and_multiple_observations(self):
        state_filter = TemporalStateFilter(minimum_observations=3)
        self.assertFalse(state_filter.observe("activity", "sleeping", 10, 10))
        self.assertFalse(state_filter.observe("activity", "sleeping", 15, 10))
        self.assertTrue(state_filter.observe("activity", "sleeping", 20, 10))

    def test_unknown_resets_candidate(self):
        state_filter = TemporalStateFilter(minimum_observations=2)
        self.assertFalse(state_filter.observe("activity", "awake", 10, 5))
        self.assertFalse(state_filter.observe("activity", "unknown", 20, 5))
        self.assertFalse(state_filter.observe("activity", "awake", 30, 5))


class ActivityMappingTest(unittest.TestCase):
    def classify(self, **overrides):
        values = {
            "emotion": "Neutral",
            "emotion_confidence": 0.7,
            "eyes_detected": 1,
            "face_detected": True,
            "motion_score": 1.0,
            "sleep_motion_threshold": 2.5,
            "wake_motion_threshold": 8.0,
        }
        values.update(overrides)
        return classify_activity(**values)[0]

    def test_happiness_maps_to_awake_happy(self):
        self.assertEqual(
            self.classify(emotion="Happiness", emotion_confidence=0.8),
            "awake_happy",
        )

    def test_weak_happiness_stays_awake(self):
        self.assertEqual(
            self.classify(emotion="Happiness", emotion_confidence=0.29), "awake"
        )

    def test_closed_eyes_and_low_motion_map_to_sleeping(self):
        self.assertEqual(self.classify(eyes_detected=0), "sleeping")

    def test_negative_expression_maps_to_unhappy_only_when_awake(self):
        self.assertEqual(self.classify(emotion="Sadness", emotion_confidence=0.7), "awake_unhappy")

    def test_missing_face_stays_unknown(self):
        self.assertEqual(self.classify(face_detected=False), "unknown")


class ManualClassifiedPhotoTest(unittest.TestCase):
    def test_saves_activity_and_posture_into_separate_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "test.db")
            database.transition("activity", "sleeping", "test")
            database.transition("posture", "left_side", "test")
            image_bytes = io.BytesIO()
            Image.new("RGB", (64, 48), "white").save(image_bytes, "JPEG")
            frame = Frame(time.time(), image_bytes.getvalue(), 100.0)

            class FakeCamera:
                def sharpest_recent(self, _window):
                    return frame

                def standard_jpeg(self, jpeg, quality=90):
                    return jpeg

            app = BabyMonitorApp.__new__(BabyMonitorApp)
            app._observation_lock = threading.RLock()
            app.config = SimpleNamespace(
                classified_photos_enabled=True,
                classified_daily_limit=250,
                media_mount=Path("/"),
                media_dir=root / "media",
            )
            app.camera = FakeCamera()
            app.database = database
            app._classified_lock = threading.Lock()
            app._classified_last_saved = {}
            app._classified_last_hash = {}

            result = app.save_current_classified_photo()
            self.assertEqual(len(result["saved"]), 2)
            self.assertEqual(
                len(list((root / "media/classified/infant/activity/sleeping").rglob("*.jpg"))),
                1,
            )
            self.assertEqual(
                len(list((root / "media/classified/infant/posture/left_side").rglob("*.jpg"))),
                1,
            )

            photos = app.photo_events()
            self.assertEqual({item["value"] for item in photos}, {"sleeping", "left_side"})
            activity_photo = next(item for item in photos if item["kind"] == "activity")
            result = app.trash_photo(
                activity_photo["id"], activity_photo["event_type"]
            )
            self.assertTrue(result["trashed"])
            self.assertFalse(
                list((root / "media/classified/infant/activity/sleeping").rglob("*.jpg"))
            )
            self.assertEqual(
                len(list((root / "media/_trash/photos").rglob("*.jpg"))), 1
            )
            self.assertEqual(
                database.event(activity_photo["id"])["event_type"], "photo_trashed"
            )
            database.close()

    def test_qwen_state_photo_uses_the_exact_analyzed_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "test.db")

            def jpeg(color):
                output = io.BytesIO()
                Image.new("RGB", (64, 48), color).save(output, "JPEG")
                return output.getvalue()

            analyzed = Frame(time.time(), jpeg("red"), 50.0)
            recent = Frame(time.time(), jpeg("blue"), 100.0)

            class FakeCamera:
                def sharpest_recent(self, _window):
                    return recent

                def standard_jpeg(self, raw, quality=90):
                    return raw

            app = BabyMonitorApp.__new__(BabyMonitorApp)
            app._observation_lock = threading.RLock()
            app.config = SimpleNamespace(
                classified_photos_enabled=True,
                classified_daily_limit=250,
                media_mount=Path("/"),
                media_dir=root / "media",
            )
            app.camera = FakeCamera()
            app.database = database
            app.events = SimpleNamespace(publish=lambda *_args: None)
            app._classified_lock = threading.Lock()
            app._classified_last_saved = {}
            app._classified_last_hash = {}

            result = app.transition(
                "activity",
                "sleeping",
                "qwen3-vl-2b",
                0.95,
                "宝宝清楚可见并正在睡觉",
                analyzed,
            )
            self.assertTrue(result["classified_photo_saved"])
            event = database.events("classified_photo", 1)[0]
            with Image.open(root / "media" / event["original_path"]) as image:
                red, _green, blue = image.convert("RGB").getpixel((10, 10))
            self.assertGreater(red, blue)
            database.close()

    def test_expression_photo_is_classified_and_manageable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "test.db")
            output = io.BytesIO()
            Image.new("RGB", (96, 72), "white").save(output, "JPEG")
            frame = Frame(time.time(), output.getvalue(), 90.0)

            class FakeCamera:
                def standard_jpeg(self, raw, quality=90):
                    return raw

            app = BabyMonitorApp.__new__(BabyMonitorApp)
            app._observation_lock = threading.RLock()
            app.config = SimpleNamespace(
                media_mount=Path("/"),
                media_dir=root / "media",
            )
            app.camera = FakeCamera()
            app.database = database
            app._classified_lock = threading.Lock()
            self.assertTrue(
                app.save_expression_photo("happy", 0.91, "宝宝清楚微笑", frame)
            )
            photos = app.photo_events()
            self.assertEqual(photos[0]["kind"], "expression")
            self.assertEqual(photos[0]["value"], "happy")
            self.assertEqual(photos[0]["label"], "开心")
            self.assertTrue((root / "media" / database.events("expression_photo", 1)[0]["original_path"]).is_file())
            database.close()


class AccessStateStoreTest(unittest.TestCase):
    def test_defaults_enable_monitoring_behind_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccessStateStore(Path(directory) / "access.json")
            self.assertEqual(
                store.read(),
                {"lan_enabled": True},
            )

    def test_persists_authenticated_monitoring_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccessStateStore(Path(directory) / "access.json")
            self.assertEqual(store.update("lan_enabled", False), {"lan_enabled": False})
            self.assertEqual(store.read(), {"lan_enabled": False})

    def test_rejects_unknown_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccessStateStore(Path(directory) / "access.json")
            with self.assertRaises(ValueError):
                store.update("camera_enabled", True)


class PlayWithMePlayerTest(unittest.TestCase):
    def player(self, root: Path) -> PlayWithMePlayer:
        return PlayWithMePlayer(
            media_root=root,
            player_python=root / "missing-python",
            speaker_host="192.0.2.10",
            start_converter=False,
        )

    def test_scans_categories_and_marks_video_as_preparing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = root / "英文歌曲"
            category.mkdir()
            (category / "song.mp3").write_bytes(b"audio")
            (category / "video.mp4").write_bytes(b"video")
            (category / ".DS_Store").write_bytes(b"hidden")
            player = self.player(root)
            status = player.status()
            self.assertEqual(
                status["categories"],
                [
                    {
                        "name": "英文歌曲",
                        "count": 2,
                        "ready_count": 1,
                        "preparing_count": 1,
                        "unplayable_count": 0,
                    }
                ],
            )
            player.close()

    def test_empty_category_is_visible_until_audio_is_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "法语儿歌").mkdir()
            player = self.player(root)
            self.assertEqual(
                player.status()["categories"],
                [
                    {
                        "name": "法语儿歌",
                        "count": 0,
                        "ready_count": 0,
                        "preparing_count": 0,
                        "unplayable_count": 0,
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "暂时没有音频"):
                player.play_category("法语儿歌")
            player.close()

    def test_refilled_round_does_not_immediately_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category = root / "经典儿歌"
            category.mkdir()
            first = category / "first.mp3"
            first.write_bytes(b"first")
            (category / "second.mp3").write_bytes(b"second")
            player = self.player(root)
            player._category = "经典儿歌"
            player._refill_queue_locked(exclude=first)
            self.assertNotEqual(player._pop_next_locked().source, first)
            player.close()

    def test_uploaded_filename_gets_a_short_display_title(self):
        source = Path(
            "整理了100首经典儿歌，适合给孩子听！ - 030 - "
            "030.小白兔合唱团 - 卖汤圆.mp3"
        )
        self.assertEqual(_display_title(source), "小白兔合唱团 - 卖汤圆")

    def test_seek_restarts_the_same_track_at_requested_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "song.mp3"
            source.write_bytes(b"audio")
            track = MediaTrack(
                category="经典儿歌",
                source=source,
                playback=source,
                title="测试儿歌",
                cache_key="test",
                ready=True,
                unplayable=False,
            )
            player = self.player(root)
            player._current = track
            player._process = object()
            player._duration_seconds = 120
            player._terminate_process = lambda _process, _paused: None
            launched = []
            player._launch_locked = lambda selected, start_seconds=0: launched.append(
                (selected, start_seconds)
            )
            player.seek(55)
            self.assertEqual(launched, [(track, 55.0)])
            player.close()

    def test_wav_duration_falls_back_without_ffprobe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\0\0" * 12000)
            player = self.player(root)
            self.assertEqual(player._probe_duration(source), 1.5)
            player.close()

    def test_silent_backend_uses_no_output_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test.wav"
            source.write_bytes(b"audio")
            track = MediaTrack("测试", source, source, "测试音频", "test", True, False)
            player = PlayWithMePlayer(
                media_root=root,
                player_python=root / "missing-python",
                speaker_host="",
                playback_backend="silent",
                start_converter=False,
            )
            args = player._worker_args(track)
            self.assertIn("baby_monitor.silent_audio_worker", args)
            self.assertEqual(args[args.index("--file") + 1], str(source))
            player.close()


class VoiceControlTest(unittest.TestCase):
    CATEGORIES = ["古诗词", "经典儿歌", "英文歌曲", "法语儿歌", "钢琴曲"]

    def test_daily_listen_schedule_boundaries(self):
        self.assertFalse(
            is_within_listen_schedule(datetime(2026, 8, 9, 6, 59), "07:00", "23:00")
        )
        self.assertTrue(
            is_within_listen_schedule(datetime(2026, 8, 9, 7, 0), "07:00", "23:00")
        )
        self.assertTrue(
            is_within_listen_schedule(datetime(2026, 8, 9, 22, 59), "07:00", "23:00")
        )
        self.assertFalse(
            is_within_listen_schedule(datetime(2026, 8, 9, 23, 0), "07:00", "23:00")
        )

    def test_wake_word_and_category_are_parsed(self):
        self.assertEqual(
            parse_voice_command(
                "八戒，播放古诗词",
                self.CATEGORIES,
                require_wake=True,
            ),
            {"type": "category", "value": "古诗词"},
        )
        self.assertEqual(
            parse_voice_command("八戒八戒", self.CATEGORIES, require_wake=True),
            {"type": "wake", "value": "八戒"},
        )
        self.assertEqual(
            parse_voice_command(
                "八戒八戒提高音量",
                self.CATEGORIES,
                require_wake=True,
            ),
            {"type": "volume_delta", "value": 10},
        )

    def test_natural_category_phrases_are_mapped(self):
        examples = {
            "播放儿歌": "经典儿歌",
            "来点儿歌": "经典儿歌",
            "音乐": "经典儿歌",
            "唱歌": "经典儿歌",
            "唱一首歌": "经典儿歌",
            "放首英文歌": "英文歌曲",
            "播放英文儿歌": "英文歌曲",
            "戒播八戒八戒播放英文儿歌嗯嗯": "英文歌曲",
            "播放法语儿歌": "法语儿歌",
            "来首法文歌": "法语儿歌",
            "听法国儿歌": "法语儿歌",
            "背一首唐诗": "古诗词",
            "听点轻音乐": "钢琴曲",
        }
        for phrase, category in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "category", "value": category},
                )

    def test_asr_homophones_are_phonetically_mapped(self):
        try:
            import pypinyin  # noqa: F401
        except ImportError:
            self.skipTest("pypinyin is optional in the local test environment")
        self.assertEqual(
            parse_voice_command("莱迪尔哥", self.CATEGORIES),
            {"type": "category", "value": "经典儿歌"},
        )

    def test_natural_player_actions_are_mapped(self):
        examples = {
            "先停一下": "pause",
            "接着放": "resume",
            "换一首": "next",
            "别放了": "stop",
        }
        for phrase, action in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "action", "value": action},
                )

    def test_general_quiet_commands_stop_music(self):
        phrases = (
            "关闭音乐",
            "关闭声音",
            "把声音关掉",
            "安静",
            "请安静",
            "别播了",
            "不要放了",
            "闭嘴",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "action", "value": "stop"},
                )

    def test_general_pause_commands_pause_music(self):
        for phrase in ("暂停音乐", "等一下", "先别放", "先别播"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "action", "value": "pause"},
                )

    def test_any_phrase_containing_quiet_stops_music(self):
        for phrase in ("安静", "安静一点", "谢安静", "请安静下来", "播放安静一点的音乐"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "action", "value": "stop"},
                )

    def test_command_rejects_missing_wake_when_required(self):
        with self.assertRaises(ValueError):
            parse_voice_command("播放钢琴曲", self.CATEGORIES, require_wake=True)

    def test_player_actions_and_volume_are_whitelisted(self):
        self.assertEqual(
            parse_voice_command("下一首", self.CATEGORIES),
            {"type": "action", "value": "next"},
        )
        self.assertEqual(
            parse_voice_command("声音小一点", self.CATEGORIES),
            {"type": "volume_delta", "value": -10},
        )
        self.assertEqual(
            parse_voice_command("音量调到二十", self.CATEGORIES),
            {"type": "volume", "value": 20},
        )

    def test_exact_volume_accepts_common_phrasings(self):
        examples = {
            "音量调到30": 30,
            "把声音设置为百分之二十": 20,
            "声音开到四十": 40,
            "调节音量到15%": 15,
            "声音调小到二十五": 25,
            "音量设为零": 0,
        }
        for phrase, volume in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "volume", "value": volume},
                )

    def test_exact_volume_is_capped_at_safe_maximum(self):
        self.assertEqual(
            parse_voice_command("音量调到99", self.CATEGORIES),
            {"type": "volume", "value": 60},
        )

    def test_general_relative_volume_phrasings(self):
        examples = {
            "小点声音": -10,
            "声音小点": -10,
            "小声点": -10,
            "再小点": -10,
            "降低音量": -10,
            "把音量调低一点": -10,
            "减小音量": -10,
            "声音轻一点": -10,
            "再轻点": -10,
            "大点声音": 10,
            "声音大点": 10,
            "大声点": 10,
            "再大点": 10,
            "提高音量": 10,
            "把音量调高一点": 10,
            "增加音量": 10,
            "声音响一点": 10,
            "再响点": 10,
        }
        for phrase, delta in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_voice_command(phrase, self.CATEGORIES),
                    {"type": "volume_delta", "value": delta},
                )

    def test_voice_manager_calls_only_player_api(self):
        class FakePlayer:
            def __init__(self):
                self.calls = []

            def status(self):
                return {
                    "volume": 25,
                    "categories": [{"name": item} for item in VoiceControlTest.CATEGORIES],
                }

            def play_category(self, category):
                self.calls.append(("category", category))
                return {"state": "playing"}

            def control(self, action):
                self.calls.append(("action", action))
                return {"state": action}

            def set_volume(self, volume):
                self.calls.append(("volume", volume))
                return {"volume": volume}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            player = FakePlayer()
            voice = VoiceControl(
                player=player,
                settings_path=root / "voice.json",
                worker_python=root / "missing-python",
                model_dir=root / "missing-model",
                keywords_file=root / "missing-keywords",
                audio_device="plughw:0,0",
            )
            result = voice.execute_phrase("八戒播放英文歌曲", require_wake=True)
            self.assertEqual(player.calls, [("category", "英文歌曲")])
            self.assertEqual(result["message"], "开始随机播放英文歌曲")
            voice.close()

    def test_voice_feedback_is_silent_on_success_and_replies_on_failure(self):
        class FakePlayer:
            def __init__(self):
                self.calls = []

            def status(self):
                return {
                    "volume": 25,
                    "categories": [{"name": item} for item in VoiceControlTest.CATEGORIES],
                }

            def set_volume(self, volume):
                self.calls.append(("volume", volume))
                return {"volume": volume}

            def play_feedback(self, path, title):
                self.calls.append(("feedback", Path(path).name, title))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feedback_dir = root / "feedback"
            feedback_dir.mkdir()
            (feedback_dir / "ok.mp3").write_bytes(b"ok")
            (feedback_dir / "repeat.mp3").write_bytes(b"repeat")
            player = FakePlayer()
            voice = VoiceControl(
                player=player,
                settings_path=root / "voice.json",
                worker_python=root / "missing-python",
                model_dir=root / "missing-model",
                keywords_file=root / "missing-keywords",
                audio_device="plughw:0,0",
                feedback_dir=feedback_dir,
                now_factory=lambda: datetime(2026, 8, 24, 12, 0),
            )

            voice._handle_event({"type": "command", "text": "提高音量"})
            self.assertEqual(
                player.calls,
                [("volume", 35)],
            )
            voice._handle_event({"type": "command", "text": "没有这个指令"})
            self.assertEqual(
                player.calls[-1],
                ("feedback", "repeat.mp3", "请再说一遍"),
            )
            voice._handle_event({"type": "timeout"})
            self.assertEqual(
                player.calls[-1],
                ("feedback", "repeat.mp3", "请再说一遍"),
            )
            self.assertEqual(voice.status()["last_feedback"], "请再说一遍")
            self.assertIsNone(voice.status()["feedback_error"])
            voice.close()


if __name__ == "__main__":
    unittest.main()
