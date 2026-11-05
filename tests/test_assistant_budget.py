"""Approved prototype policy; fake clocks and CPU-only model handlers."""
import http.client
import io
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from baby_monitor.assistant_common import AssistantError
from baby_monitor.inference_scheduler import InferenceScheduler
from baby_monitor.vlm import VLMObserver
import test_assistant_scheduler as worker_tests


class Clock:
    value = 0.0
    def __call__(self): return self.value


class PrototypeScheduleTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.scheduler = InferenceScheduler(clock=self.clock)
        self.order, self.errors, self.threads = [], [], []

    def wait_pending(self, kind, count):
        end = time.monotonic()+1
        while self.scheduler.metrics()['pending'][kind] != count and time.monotonic()<end:
            time.sleep(.001)
        self.assertEqual(self.scheduler.metrics()['pending'][kind], count)

    def submit(self, kind, name, fail=False, timeout=10):
        def run():
            try:
                with self.scheduler.slot(kind, timeout=timeout):
                    self.order.append(name)
                    if fail: raise RuntimeError('synthetic')
            except AssistantError as exc: self.errors.append((name, exc.code))
            except RuntimeError: self.errors.append((name, 'synthetic'))
        thread=threading.Thread(target=run); thread.start(); self.threads.append(thread)

    def finish(self):
        for thread in self.threads:
            thread.join(2); self.assertFalse(thread.is_alive())

    def test_chat_first_and_latest_waiting_vision_replaces_old_without_preemption(self):
        with self.scheduler.slot('vision'): pass
        with self.scheduler.slot('chat'):
            owner=self.scheduler.running
            self.submit('vision','old'); self.wait_pending('vision',1)
            self.submit('chat','chat'); self.wait_pending('chat',1)
            self.submit('vision','new')
            end=time.monotonic()+1
            while self.scheduler.metrics()['vision_replaced']!=1 and time.monotonic()<end: time.sleep(.001)
            self.assertEqual(self.scheduler.metrics()['vision_replaced'],1)
            self.assertIs(self.scheduler.running,owner)
        self.finish()
        self.assertEqual(self.order,['chat','new'])
        self.assertEqual(self.errors,[('old','vision_superseded')])

    def test_120_seconds_since_success_promotes_vision_once_then_chat(self):
        with self.scheduler.slot('vision'): pass
        self.clock.value=119
        with self.scheduler.slot('chat'):
            self.submit('vision','due'); self.wait_pending('vision',1)
            self.submit('chat','chat'); self.wait_pending('chat',1)
            self.clock.value=120
        self.finish()
        self.assertEqual(self.order,['due','chat'])
        self.assertEqual(self.scheduler.last_vision_success,120)
        self.assertEqual(self.scheduler.metrics()['vision_success_age_seconds'],0)

    def test_failure_does_not_reset_success_age_and_five_second_retry_is_unchanged(self):
        with self.scheduler.slot('vision'): pass
        self.clock.value=121
        with self.assertRaises(RuntimeError):
            with self.scheduler.slot('vision'): raise RuntimeError('synthetic')
        self.assertEqual(self.scheduler.last_vision_success,0)
        with self.scheduler.slot('chat'):
            self.submit('chat','chat'); self.wait_pending('chat',1)
            self.submit('vision','retry'); self.wait_pending('vision',1)
        self.finish(); self.assertEqual(self.order,['retry','chat'])
        observer=object.__new__(VLMObserver)
        observer.interval_seconds=30; observer.sleep_interval_seconds=60; observer.motion_interval_seconds=15
        observer.motion_threshold=8; observer.camera=SimpleNamespace(motion_score=0)
        state={'activity':{'value':'awake'},'presence':{'value':'visible'}}
        observer.database=SimpleNamespace(current=lambda:state); observer.online=True
        self.assertEqual(observer._next_interval(),30)
        state['activity']['value']='sleeping'; self.assertEqual(observer._next_interval(),60)
        observer.camera.motion_score=9; self.assertEqual(observer._next_interval(),15)
        observer.online=False; self.assertEqual(observer._next_interval(),5)

    def test_first_vision_has_priority_and_expired_jobs_cannot_start(self):
        with self.scheduler.slot('chat'):
            self.submit('chat','expired',timeout=1); self.wait_pending('chat',1)
            self.submit('vision','first'); self.wait_pending('vision',1)
            self.clock.value=2
        self.finish(); self.assertEqual(self.order,['first'])
        self.assertEqual(self.errors,[('expired','model_timeout')])

    def test_shutdown_rejects_waiters_and_overrun_never_releases_owner_early(self):
        with self.scheduler.slot('chat'):
            owner=self.scheduler.running
            self.submit('chat','queued'); self.wait_pending('chat',1)
            self.clock.value=9
            self.scheduler.stop_accepting()
            self.assertIs(self.scheduler.running,owner)
        self.finish()
        self.assertEqual(self.errors,[('queued','model_unavailable')])
        self.assertEqual(self.scheduler.metrics()['chat_overruns'],1)
        with self.assertRaises(AssistantError):
            with self.scheduler.slot('vision'): pass


class PrototypeHTTPTest(unittest.TestCase):
    setUp = worker_tests.EngineTest.setUp

    def test_bad_visual_json_does_not_count_as_success_and_old_hints_cannot_control_admission(self):
        output=io.BytesIO(); Image.new('RGB',(16,16)).save(output,'JPEG')
        server=self.module.ModelHTTPServer(('127.0.0.1',0),self.module.Handler)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True); thread.start()
        try:
            with patch.object(self.module,'ENGINE',self.engine):
                for fail,status in [(True,502),(False,200)]:
                    self.engine.processor.batch_decode=lambda *a,**k:['invalid' if fail else '{}']
                    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                    conn.request('POST','/analyze',body=output.getvalue(),headers={'Content-Type':'image/jpeg','X-Bmon-Next-Vision-Seconds':'5'})
                    response=conn.getresponse(); response.read(); conn.close()
                    self.assertEqual(response.status,status)
                    self.assertEqual(self.engine.scheduler.last_vision_success is None,fail)
                with self.engine.scheduler.slot('chat'): pass
        finally:
            server.shutdown(); server.server_close(); thread.join(1)

    def test_no_unprofiled_flag_is_needed_or_read(self):
        for value in ['true','false']:
            with patch.dict('os.environ',{'BMON_ASSISTANT_ALLOW_UNPROFILED_CHAT':value}):
                scheduler=self.module.Engine().scheduler
            self.assertFalse(hasattr(scheduler,'allow_unprofiled_chat'))
            with scheduler.slot('chat'): pass
