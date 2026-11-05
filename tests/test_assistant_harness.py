"""Real Web/model HTTP handlers and real private SQLite; CPU synthetic engine only."""
import contextlib
from datetime import datetime,timedelta
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from baby_monitor.assistant_common import AssistantError
from maintenance.assistant_test_harness import (FixtureGate,Harness,prepare_run,
    validate_run,isolated_environment,load_worker,real_preflight)
from maintenance.assistant_test_cases import TestClient,snapshot


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


class HarnessSafetyTest(unittest.TestCase):
    def test_prepare_is_private_synthetic_only_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); run=prepare_run(root)
            self.assertEqual(validate_run(run),run)
            for path in run.rglob('*'):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700 if path.is_dir() else 0o600)
            self.assertFalse(list(run.rglob('*.db')))
            self.assertEqual(len(json.loads((run/'cases.json').read_text())),7)
            alias=root/'alias'; alias.symlink_to(run,target_is_directory=True)
            with self.assertRaises(ValueError): validate_run(alias)
            target=run/'data'/'bad.db'; target.symlink_to(root/'outside.db')
            with self.assertRaises(ValueError): validate_run(run)

    def test_environment_cannot_inherit_production_or_proxy_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve())
            with patch.dict(os.environ,{'BMON_DATA_DIR':'forbidden-production','BMON_PREVIEW_LAN_SUBNET':'invalid',
                    'BMON_TAILSCALE_USER':'private','BMON_LOCAL_PREVIEW_NO_LOGIN':'true','http_proxy':'invalid'}):
                with isolated_environment(run,18010,18881):
                    self.assertEqual(os.environ['BMON_DATA_DIR'],str(run/'data'))
                    self.assertNotIn('BMON_TAILSCALE_USER',os.environ)
                    self.assertNotIn('http_proxy',os.environ)
                    self.assertNotIn('BMON_PREVIEW_LAN_SUBNET',os.environ)
                    self.assertEqual(os.environ['BMON_LOCAL_PREVIEW_NO_LOGIN'],'false')
                    self.assertEqual(os.environ['BMON_ASSISTANT_ALLOW_UNPROFILED_CHAT'],'false')
                self.assertEqual(os.environ['BMON_DATA_DIR'],'forbidden-production')

    def test_real_mode_is_denied_before_import_or_load(self):
        with self.assertRaisesRegex(ValueError,'maintenance-start'):
            real_preflight(Path('not-used'),Path('not-used'),False,18010,18881)
        result=subprocess.run([sys.executable,'-B','maintenance/run-assistant-qwen-test.py','serve',
            '--run-dir','not-used','--real','--weights','not-used'],capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('torch',result.stderr.lower())

    def test_fixture_gate_never_shortens_quiet_promise_or_preempts_active_chat(self):
        now=[0.0]; gate=FixtureGate(clock=lambda:now[0]); gate.begin_vision(); gate.begin_functional()
        with gate.job('chat'):
            with self.assertRaises(RuntimeError): gate.begin_vision()
            now[0]=130
            with self.assertRaises(RuntimeError): gate.begin_vision()
            gate.stopping()
            self.assertEqual(gate.active_chat,1)
        self.assertEqual(gate.active_chat,0)
        with self.assertRaises(AssistantError):
            with gate.job('chat'): pass


class HarnessHTTPTest(unittest.TestCase):
    def test_all_thirteen_apis_through_model_http_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve()); mp,wp=free_port(),free_port()
            password='CPU_ONLY_HARNESS_TEST_PASSWORD'
            with isolated_environment(run,mp,wp), patch('baby_monitor.camera.Camera.start',side_effect=AssertionError('hardware start')), patch('baby_monitor.voice_control.VoiceControl.start',side_effect=AssertionError('audio start')):
                module=load_worker(mock=True); harness=Harness(run,module,password,mp,wp)
                harness.start()
                try:
                    client=TestClient(wp,password); base='/api/assistant'
                    self.assertEqual(client.request('GET',base+'/conversations?scope=demo',authenticated=False)[0],401)
                    self.assertEqual(client.request('POST',base+'/conversations',{'scope':'demo'},csrf=False)[0],403)
                    status,body=client.request('POST',base+'/conversations',{'scope':'demo'})
                    self.assertEqual(status,201); cid=body['conversation']['id']
                    cp=f'{base}/conversations/{cid}?scope=demo'
                    self.assertEqual(client.request('PATCH',cp,{'title':'test A'})[0],200)
                    self.assertEqual(client.request('GET',base+'/conversations?scope=demo')[0],200)
                    self.assertEqual(client.request('GET',f'{base}/conversations/{cid}/messages?scope=demo')[0],200)
                    blocked=client.turn(cid,'你好')['response']
                    self.assertEqual(blocked['status'],'failed')
                    self.assertEqual(blocked['error']['code'],'vision_capacity_reserved')
                    harness.fixture('empty',functional=True)
                    self.assertTrue(harness.app.database.observations())
                    before=len(snapshot(harness))
                    turn=client.turn(cid,'昨天上午11点宝宝喝了150ml奶','saved-milk')['response']
                    self.assertEqual(turn['status'],'completed',turn)
                    self.assertEqual(turn['record_actions'][0]['status'],'saved',turn)
                    event=turn['record_actions'][0]['event']; eid=event['id']
                    self.assertEqual(len(snapshot(harness)),before+1)
                    replay=client.turn(cid,'昨天上午11点宝宝喝了150ml奶','saved-milk')['response']
                    self.assertTrue(replay['replayed']); self.assertEqual(len(snapshot(harness)),before+1)
                    pending=client.turn(cid,'昨天上午11点宝宝喝了一些奶')['response']
                    self.assertEqual(pending['record_actions'][0]['status'],'needs_confirmation')
                    aid=pending['record_actions'][0]['action_id']; mid=pending['user_message']['id']
                    confirm=f'{base}/conversations/{cid}/messages/{mid}/record-actions/{aid}/confirm?scope=demo'
                    self.assertEqual(client.request('POST',confirm,{'decision':'confirm','fields':{'amount_ml':80}})[0],200)
                    self.assertEqual(len(snapshot(harness)),before+2)
                    day=(datetime.now(ZoneInfo('Asia/Shanghai'))-timedelta(days=1)).date().isoformat()
                    query=f'scope=demo&from={day}&to={day}&timezone=Asia%2FShanghai'
                    self.assertEqual(len(client.request('GET',base+'/care-events?'+query)[1]['events']),2)
                    self.assertEqual(client.request('GET',base+'/sleep-summary?'+query)[0],200)
                    status,body=client.request('POST',base+'/care-events',{'scope':'real','timezone':'Asia/Shanghai','event_type':'feeding',
                        'occurred_at':day+'T03:00:00Z','amount_ml':60,'client_event_id':'manual-1'})
                    self.assertEqual(status,201); real=body['event']
                    self.assertEqual(client.request('PATCH',f'{base}/care-events/{real["id"]}?scope=demo',{'expected_version':1,'amount_ml':1})[0],404)
                    ep=f'{base}/care-events/{eid}?scope=demo'
                    self.assertEqual(client.request('PATCH',ep,{'expected_version':1,'amount_ml':155})[0],200)
                    self.assertEqual(client.request('PATCH',ep,{'expected_version':1,'amount_ml':160})[0],409)
                    self.assertEqual(client.request('DELETE',cp)[0],200)
                    self.assertEqual(client.request('GET',f'{base}/conversations/{cid}/messages?scope=demo')[0],404)
                    record=next(r for r in snapshot(harness) if r['id']==eid)
                    self.assertIsNone(record['source_message_id'])
                    self.assertEqual(record['status'],'confirmed')
                    self.assertEqual(client.request('DELETE',ep+'&expected_version=2')[0],200)
                    self.assertEqual(next(r for r in snapshot(harness) if r['id']==eid)['status'],'deleted')
                    with self.assertRaises(RuntimeError): harness.fixture('gray')
                finally:
                    self.assertTrue(harness.stop(timeout=3))
            journal=[json.loads(line) for line in harness.journal.path.read_text().splitlines()]
            self.assertTrue(any(row['kind']=='model_response' and row['route']=='/chat' for row in journal))
            self.assertTrue(any(row['kind']=='model_response' and row['route']=='/analyze' for row in journal))
            self.assertTrue(all(row['backend']=='mock' for row in journal))
            self.assertNotIn(password,harness.journal.path.read_text())
            self.assertNotIn('bmon_session',harness.journal.path.read_text())
            self.assertTrue((run/'data'/'assistant.db').exists())
            for path in (run/'data').rglob('*'):
                if path.is_file(): self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_adult_fixture_stays_out_of_infant_and_case_recorder_never_auto_passes(self):
        from maintenance.assistant_test_cases import run_cases
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve()); mp,wp=free_port(),free_port()
            with isolated_environment(run,mp,wp):
                harness=Harness(run,load_worker(mock=True),'CPU_ONLY_TEST_PASSWORD',mp,wp); harness.start()
                try:
                    harness.fixture('gray',adult=True)
                    self.assertTrue(harness.app.database.observations(observation_mode='adult_test'))
                    self.assertFalse(harness.app.database.observations(observation_mode='infant'))
                    harness.fixture('empty',functional=True)
                    run_cases(harness,'CPU_ONLY_TEST_PASSWORD')
                finally: self.assertTrue(harness.stop(timeout=3))
            rows=[json.loads(line) for line in harness.journal.path.read_text().splitlines()]
            cases=[row for row in rows if row['kind']=='case_observation']
            self.assertEqual(len(cases),7)
            self.assertTrue(all(row['semantic_verdict'].startswith('NOT_AUTO_PASSED') for row in cases))

    def test_stopping_waits_for_model_owner_without_force_kill(self):
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve()); mp,wp=free_port(),free_port()
            with isolated_environment(run,mp,wp):
                module=load_worker(mock=True); entered=threading.Event(); release=threading.Event()
                def slow(payload):
                    with module.ENGINE.scheduler.slot('chat'):
                        entered.set(); release.wait(3)
                        return {'result':{'answer':'CPU synthetic only','actions':[],'query':None}}
                module.ENGINE.chat=slow
                harness=Harness(run,module,'CPU_ONLY_TEST_PASSWORD',mp,wp); harness.start()
                caller=None
                try:
                    harness.fixture('empty',functional=True)
                    client=TestClient(wp,'CPU_ONLY_TEST_PASSWORD')
                    _,created=client.request('POST','/api/assistant/conversations',{'scope':'demo'})
                    cid=created['conversation']['id']
                    def request_until_stopped():
                        try: client.turn(cid,'你好')
                        except OSError: pass  # stopping the listener may end a poll
                    caller=threading.Thread(target=request_until_stopped); caller.start()
                    self.assertTrue(entered.wait(1))
                    owner=module.ENGINE.scheduler.running
                    self.assertFalse(harness.stop(timeout=.02))
                    self.assertIs(module.ENGINE.scheduler.running,owner)
                    self.assertEqual(harness.gate.phase,'stopping')
                finally:
                    release.set()
                    self.assertTrue(harness.stop(timeout=3))
                    if caller: caller.join(2)
            self.assertIn('drain_blocked',harness.journal.path.read_text())

    def test_cli_mock_timer_closes_admission_and_exits_without_gpu(self):
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve()); mp,wp=free_port(),free_port()
            process=subprocess.Popen([sys.executable,'-B','maintenance/run-assistant-qwen-test.py','serve','--run-dir',str(run),
                '--mock','--model-port',str(mp),'--web-port',str(wp),'--minutes','0.005'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:
                process.stdin.write('CPU_ONLY_TEST_PASSWORD\n'); process.stdin.flush()
                self.assertEqual(process.wait(timeout=6),0)
                self.assertIn('MOCK ONLY',process.stdout.read())
            finally:
                process.stdin.close(); process.stdout.close(); process.stderr.close()
                if process.poll() is None: process.terminate(); process.wait(3)
            logs=''.join(path.read_text() for path in (run/'logs').glob('events-*.jsonl'))
            self.assertIn('maintenance_deadline',logs)
            self.assertIn('stopped',logs)
            rows=[json.loads(line) for line in logs.splitlines()]
            self.assertFalse(any(row['kind']=='real_model_loaded' for row in rows))

    def test_journal_failure_closes_admission_and_raw_decoder_is_test_only(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            run=prepare_run(Path(temp).resolve()); mp,wp=free_port(),free_port()
            with isolated_environment(run,mp,wp):
                module=load_worker(mock=True)
                module.ENGINE.processor=SimpleNamespace(batch_decode=lambda *_a,**_k:['{bad JSON'])
                harness=Harness(run,module,'CPU_ONLY_TEST_PASSWORD',mp,wp); harness.start()
                try:
                    harness.capture_test_decoder()
                    self.assertEqual(module.ENGINE.processor.batch_decode(None),['{bad JSON'])
                    with patch.object(harness.journal.stream,'write',side_effect=OSError('synthetic disk full')):
                        harness.journal.emit('synthetic_failure')
                    self.assertTrue(harness.journal.failed)
                    self.assertEqual(harness.gate.phase,'stopping')
                    with self.assertRaises(AssistantError): module.ENGINE.chat({})
                finally: self.assertTrue(harness.stop(timeout=3))
            logs=[json.loads(line) for line in harness.journal.path.read_text().splitlines()]
            self.assertEqual(next(row for row in logs if row['kind']=='raw_generation')['decoded'],['{bad JSON'])
