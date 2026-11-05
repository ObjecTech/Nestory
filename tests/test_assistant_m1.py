"""Run the delivered JS controller against real localhost HTTP and temporary SQLite."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from access_control import AccessStateStore
from baby_monitor.app import BabyMonitorApp
from baby_monitor.assistant_common import AssistantError
from baby_monitor.config import Config
from security import AuthConfig, make_password_hash
from server import Handler, MonitorServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('m1_dev', ROOT/'scripts/run-assistant-dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


class FaultModel(dev.SyntheticAssistantModel):
    def chat(self, payload):
        content = payload['messages'][-1]['content']
        if content == 'M1_MODEL_FAILURE':
            raise AssistantError('model_failed', 502, retryable=True)
        if content == 'M1_MODEL_TIMEOUT':
            raise AssistantError('model_timeout', 504, retryable=True)
        if content.startswith('M1_DELAY'):
            time.sleep(.35)
        return super().chat(payload)


class M1IntegrationTest(unittest.TestCase):
    def test_actual_frontend_http_sqlite_and_restart(self):
        from baby_monitor import assistant_common, assistant_database
        reference = datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=16, minute=0, second=0, microsecond=0)-timedelta(days=1)
        start = time.monotonic()
        clock = lambda: int(reference.timestamp()*1000 + (time.monotonic()-start)*1000)
        with tempfile.TemporaryDirectory(prefix='nestory-m1-') as temp, patch.object(assistant_common, 'now_ms', clock), patch.object(assistant_database, 'now_ms', clock):
            data = Path(temp)
            overrides = dict(BMON_APP_DIR=str(ROOT), BMON_DATA_DIR=str(data),
                BMON_MEDIA_MOUNT=str(data/'media'), BMON_MEDIA_DIR=str(data/'media'),
                BMON_DEMO='true', BMON_ASSISTANT_ENABLED='true', BMON_VLM_ENABLED='false',
                BMON_MODEL_ENABLED='false', BMON_VOICE_ENABLED='false', BMON_PLAYBACK_BACKEND='silent',
                BMON_PERSON_TEST_MODE='false', BMON_LOCAL_PREVIEW_NO_LOGIN='false', BMON_HOST='127.0.0.1')
            with patch.dict(os.environ, overrides):
                config = Config.from_env()
            app = BabyMonitorApp(config)
            app.assistant.model = FaultModel()
            password = 'M1_TEST_ONLY_HTTP_PASSWORD'
            auth = AuthConfig('assistant-dev', make_password_hash(password), 'http://127.0.0.1')
            server = MonitorServer(('127.0.0.1', 0), Handler, app, AccessStateStore(data/'access.json'), auth, auto_login=False)
            origin = f'http://127.0.0.1:{server.server_port}'
            server.auth.config = replace(auth, origin=origin)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
            thread.start(); app.start()
            def run(stage):
                env = dict(os.environ, M1_ORIGIN=origin, M1_STAGE=stage, M1_PASSWORD=password, M1_DAY=reference.date().isoformat())
                result = subprocess.run(['node', str(ROOT/'tests/test_assistant_m1.cjs')],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)
                print(result.stdout.strip())
            try:
                run('business')
                db = app.assistant.database.connection
                with app.assistant.database.lock:
                    event = db.execute("SELECT * FROM care_events WHERE notes='M1_CROSS_SESSION'").fetchone()
                    self.assertEqual((event['status'], event['version'], event['amount_ml']), ('deleted', 3, 155))
                    self.assertIsNone(event['source_message_id'])
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM care_event_revisions WHERE event_id=?', (event['id'],)).fetchone()[0], 3)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_conversations WHERE title='M1_A'").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_messages WHERE content='今天上午 11 点宝宝喝了 150ml 奶'").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM care_events WHERE notes='M1_PENDING_CONFIRMED' AND status='confirmed'").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM care_events WHERE notes='M1_MANUAL' AND data_scope='real'").fetchone()[0], 1)
                # Reinitialize the actual app/service/DB, revoke sessions, keep the same loopback listener.
                app.stop()
                app = BabyMonitorApp(config); app.assistant.model = FaultModel(); app.start()
                server.app = app
                server.auth.sessions.clear()
                run('restart')
            finally:
                server.shutdown(); server.server_close(); thread.join(2); app.stop()
