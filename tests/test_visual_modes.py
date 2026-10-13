"""Synthetic engineering regressions. No infant accuracy or real-night validation."""
import io
import http.client
from datetime import datetime
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

import numpy as np
from PIL import Image

from baby_monitor.app import BabyMonitorApp
from baby_monitor.camera import Camera, Frame
from baby_monitor.config import Config
from baby_monitor.database import Database
from baby_monitor.inference import InferenceWorker, PostureClassifier
from baby_monitor.model_prompt import observation_prompt, COMMON_PROMPT
from baby_monitor.observation_mode import mode_from_metadata
from baby_monitor.vlm import VLMObserver, normalize_observation
from access_control import AccessStateStore
from security import AuthConfig
from server import Handler, MonitorServer


def visible(**fields):
    return dict(baby_visible=True, visibility='visible', sleep_state='awake',
                posture='supine', confidence=.95, posture_confidence=.95,
                visibility_confidence=.95, **fields)


def make_observer(db, **overrides):
    options = dict(camera=SimpleNamespace(online=True, motion_score=0), database=db,
                   enabled=True, service_url='http://127.0.0.1:1', interval_seconds=30,
                   sleep_interval_seconds=60, motion_interval_seconds=15, timeout_seconds=35,
                   confidence_threshold=.6, stable_seconds=20, motion_threshold=8,
                   on_transition=lambda c,v,s,k,n,f=None: db.transition(c,v,s,k,n),
                   on_observation=lambda o: None)
    options.update(overrides)
    return VLMObserver(**options)


def make_app(directory, **values):
    env = {'BMON_APP_DIR': str(Path.cwd()), 'BMON_DATA_DIR': str(Path(directory)/'data'),
           'BMON_MEDIA_MOUNT': str(Path(directory)/'media'),
           'BMON_MEDIA_DIR': str(Path(directory)/'media'),
           'BMON_DEMO': 'true', 'BMON_MODEL_ENABLED': 'false',
           'BMON_VLM_ENABLED': 'true', 'BMON_VOICE_ENABLED': 'false', **values}
    with patch.dict(os.environ, env, clear=True):
        app = BabyMonitorApp(Config.from_env())
    output = io.BytesIO()
    Image.new('L', (64, 48), 120).save(output, 'JPEG')
    app.camera._accept_jpeg(output.getvalue())
    return app


class PromptAndEvidenceTest(unittest.TestCase):
    def test_two_subject_rules_share_schema_but_not_subject_assumptions(self):
        infant, adult = observation_prompt(), observation_prompt(person_test_mode=True)
        self.assertTrue(infant.startswith(COMMON_PROMPT))
        self.assertTrue(adult.startswith(COMMON_PROMPT))
        self.assertIn('INFANT MODE', infant)
        self.assertNotIn('ADULT TEST MODE', infant)
        self.assertIn('ADULT TEST MODE', adult)
        self.assertNotIn('INFANT MODE', adult)
        self.assertIn('不能证明真实睡眠', adult)
        self.assertIn('无法确认是宝宝', infant)

    def test_adult_sitting_standing_walking_and_lying(self):
        for body in ('sitting','standing','walking'):
            item = normalize_observation(visible(body_state=body), True)
            self.assertEqual(item['posture'], 'unknown')
            self.assertEqual(item['sleep_state'], 'awake')
        for posture in ('supine','side','prone'):
            payload = visible(body_state='lying')
            payload['posture'] = posture
            self.assertEqual(normalize_observation(payload, True)['posture'], posture)

    def test_adult_eye_closing_simulation_does_not_force_sleep(self):
        payload = visible(description='成人闭眼保持静止，模拟睡觉。')
        payload['sleep_state'] = 'sleeping'
        adult = normalize_observation(payload, True)
        self.assertEqual(adult['sleep_state'], 'unknown')
        self.assertEqual(adult['posture'], 'supine')
        # Adult-specific correction must not relax or replace the infant rules.
        infant = normalize_observation(payload, False)
        self.assertEqual(infant['sleep_state'], 'sleeping')

    def test_absent_unconfirmed_and_occluded_subject(self):
        for mode in (False, True):
            for overrides in ({'baby_visible':None, 'visibility':'unknown'},
                              {'baby_visible':False,'visibility':'not_visible'},
                              {'visibility':'partial'}):
                payload = visible(); payload.update(overrides)
                result = normalize_observation(payload, mode)
                self.assertEqual(result['sleep_state'], 'unknown')
                self.assertEqual(result['posture'], 'unknown')
                self.assertEqual(result['expression'], 'unknown')
            result = normalize_observation(visible(body_visible=False), mode)
            self.assertEqual(result['posture'], 'unknown')
            payload = visible(face_visible=False); payload['sleep_state']='sleeping'
            result = normalize_observation(payload, mode)
            self.assertEqual(result['sleep_state'], 'unknown')
            self.assertEqual(result['expression'], 'unknown')
        payload = visible(); del payload['baby_visible']
        self.assertIsNone(normalize_observation(payload)['baby_visible'])


class ModeSwitchAndFailureTest(unittest.TestCase):
    def test_pending_request_cannot_apply_after_switch_or_round_trip(self):
        for round_trip in (False,True):
            for outcome in ('success','timeout','bad_json'):
                with self.subTest(round_trip=round_trip,outcome=outcome), tempfile.TemporaryDirectory() as folder:
                    db=Database(Path(folder)/'test.db'); observer=make_observer(db)
                    entered,release=threading.Event(),threading.Event()
                    def pending(_jpeg):
                        generation=observer._mode_generation
                        entered.set(); self.assertTrue(release.wait(3))
                        if outcome=='timeout': raise URLError('timeout')
                        if outcome=='bad_json': raise ValueError('bad json')
                        return normalize_observation(visible()), {'_mode_generation':generation}
                    with patch.object(observer,'_request',side_effect=pending):
                        thread=threading.Thread(target=observer._observe_frame,args=(Frame(time.time(),b'test',1),))
                        thread.start(); self.assertTrue(entered.wait(2))
                        observer.set_person_test_mode(True)
                        if round_trip: observer.set_person_test_mode(False)
                        release.set(); thread.join(3); self.assertFalse(thread.is_alive())
                    self.assertIsNone(observer.last_result)
                    self.assertIsNone(observer.last_success_ts)
                    self.assertIsNone(observer.last_error)
                    self.assertEqual(db.observations(),[])
                    self.assertEqual(db.current()['activity']['value'],'unknown')
                    db.close()

    def test_pre_switch_frame_cannot_be_saved_or_analyzed_in_new_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder)
            try:
                old=app.camera.latest()
                app.set_observation_subject(True)
                with patch.object(app.vlm,'_request') as request:
                    app.vlm._observe_frame(old)
                    request.assert_not_called()
                with patch.object(app.inference,'_analyze') as analyze:
                    app.inference._process_frame(old)
                    analyze.assert_not_called()
                self.assertFalse(app.save_training_sample('posture','supine'))
                self.assertEqual(list((app.config.media_dir/'training').rglob('*.jpg')),[])
            finally:app.stop()

    def test_camera_disconnection_during_request_does_not_write_state(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db');o=make_observer(db)
            def response(_):
                o.camera.online=False
                return normalize_observation(visible()),{'_mode_generation':0}
            with patch.object(o,'_request',side_effect=response):
                o._observe_frame(Frame(time.time(),b'test',1))
            self.assertEqual(db.observations(),[])
            self.assertIsNone(o.last_success_ts)
            db.close()

    def test_failure_preserves_last_success_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db'); o=make_observer(db)
            result=normalize_observation(visible())
            envelope={'_mode_generation':0,'observation_mode':'infant'}
            with patch.object(o,'_request',return_value=(result,envelope)):
                o._observe_frame(Frame(time.time(),b'test',1))
            success=o.last_success_ts
            with patch.object(o,'_request',side_effect=URLError('timeout')):
                o._observe_frame(Frame(time.time(),b'test',1))
            self.assertFalse(o.online);self.assertEqual(o.last_success_ts,success)
            self.assertIn('timeout',o.last_error)
            with patch.object(o,'_request',return_value=(result,envelope)):
                o._observe_frame(Frame(time.time(),b'test',1))
            self.assertTrue(o.online);self.assertIsNone(o.last_error)
            db.close()

    def test_single_worker_and_camera_not_blocked_by_qwen(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db'); o=make_observer(db)
            entered,release=threading.Event(),threading.Event();calls=[]
            o.camera.sharpest_recent=lambda _: Frame(time.time(),b'test',1)
            def request(_):
                calls.append(1);entered.set();release.wait(3)
                return normalize_observation(visible()),{'_mode_generation':0}
            with patch.object(o,'_request',side_effect=request):
                o.start(); first=o._thread;o.start();self.assertIs(o._thread,first)
                self.assertTrue(entered.wait(2))
                # Mode change acquires the lock without waiting for model completion.
                o.set_person_test_mode(True)
                release.set();o.stop()
            self.assertEqual(len(calls),1);db.close()

    def test_sustained_occlusion_clears_confirmed_sleep_and_posture(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db');o=make_observer(db,stable_seconds=0)
            db.transition('activity','sleeping','test')
            db.transition('posture','supine','test')
            payload=visible();payload['visibility']='partial'
            result=normalize_observation(payload)
            o._apply(result,{})
            self.assertEqual(db.current()['activity']['value'],'sleeping')
            o._apply(result,{})
            self.assertEqual(db.current()['activity']['value'],'unknown')
            self.assertEqual(db.current()['posture']['value'],'unknown')
            db.close()

    def test_current_result_expiry_and_offline_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder)
            try:
                app.vlm.last_success_ts=time.time()
                self.assertTrue(app.status()['observation_valid'])
                app.vlm.last_success_ts=time.time()-1000
                self.assertFalse(app.status()['observation_valid'])
                app.camera.online=False
                self.assertFalse(app.status()['observation_valid'])
                self.assertFalse(app.status()['camera']['online'])
            finally:app.stop()


class IsolationTest(unittest.TestCase):
    def test_switching_boundaries_and_infant_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db')
            db.transition('activity','sleeping','test',now=100)
            db.activate_mode('adult_test',now=200)
            db.transition('activity','sleeping','manual',now=250)
            db.activate_mode('infant',now=400)
            app=BabyMonitorApp.__new__(BabyMonitorApp);app.database=db
            self.assertEqual(app.timeline(100,500)['totals']['activity']['sleeping'],100)
            self.assertEqual(app.timeline(100,500,'adult_test')['totals']['activity']['sleeping'],150)
            self.assertEqual({x['observation_mode'] for x in app.timeline(100,500)['intervals']},{'infant'})
            self.assertEqual(db.current()['activity']['observation_mode'],'infant')
            db.close()

    def test_http_statistics_default_infant_and_explicit_adult_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder)
            now=time.time()
            app.database.transition('activity','sleeping','test',now=now-200)
            app.database.activate_mode('adult_test',now=now-100)
            app.database.transition('activity','sleeping','test',now=now-90)
            server=MonitorServer(('127.0.0.1',0),Handler,app,
                AccessStateStore(Path(folder)/'access.json'),
                AuthConfig('TEST_ONLY_OWNER','TEST_ONLY_UNUSED_HASH','http://127.0.0.1'))
            port=server.server_port
            from dataclasses import replace
            server.auth.config=replace(server.auth.config,origin=f'http://127.0.0.1:{port}')
            token='TEST_ONLY_SESSION_NOT_FOR_DEPLOYMENT'
            server.auth.sessions[token]=('TEST_ONLY_CSRF',time.monotonic()+60)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                day=datetime.now().strftime('%Y-%m-%d')
                for suffix,mode in [('', 'infant'),('&mode=adult_test','adult_test')]:
                    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
                    connection.request('GET',f'/api/timeline?date={day}'+suffix,
                                       headers={'Cookie':'bmon_session='+token})
                    response=connection.getresponse();self.assertEqual(response.status,200)
                    body=json.loads(response.read());connection.close()
                    self.assertEqual(body['observation_mode'],mode)
                    self.assertTrue(all(i['observation_mode']==mode for i in body['intervals']))
                    if mode=='infant':self.assertAlmostEqual(body['totals']['activity']['sleeping'],100,places=2)
            finally:
                server.shutdown();server.server_close();thread.join(2);app.stop()

    def test_manual_training_and_photos_keep_subject_after_switch_back(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder)
            try:
                app.set_observation_subject(True)
                jpeg=app.camera.latest().jpeg
                app.camera._ring.clear()
                app.camera._accept_jpeg(jpeg)
                self.assertEqual(app.inference.person_test_mode,True)
                result=app.set_manual_state('posture','supine')
                self.assertTrue(result['training_sample_saved'])
                app.save_posture_photo('supine')
                root=app.config.media_dir
                self.assertEqual(len(list((root/'training/adult_test').rglob('*.jpg'))),1)
                self.assertFalse((root/'training/infant').exists())
                metadata=json.loads(next((root/'training/adult_test').rglob('*.json')).read_text())
                self.assertEqual(metadata['observation_mode'],'adult_test')
                app.set_observation_subject(False)
                self.assertEqual(app.inference.person_test_mode,False)
                adult_photo=app.photo_events()[0]
                self.assertEqual(adult_photo['observation_mode'],'adult_test')
                jpeg=app.camera.latest().jpeg
                app.camera._ring.clear()
                app.camera._accept_jpeg(jpeg)
                app.set_manual_state('posture','side')
                self.assertEqual(len(list((root/'training/infant').rglob('*.jpg'))),1)
                # Reload uses saved infant choice, irrespective of default env.
                settings=json.loads(app._subject_settings.read_text())
                self.assertFalse(settings['person_test_mode'])
            finally:app.stop()

    def test_legacy_migration_preserves_uncertainty_and_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db'
            db=Database(path)
            db.add_observation(100,'test',normalize_observation(visible()))
            db.add_event('smile',100,None,None,1,original_path='smiles/old.jpg')
            db.close()
            # Emulate the pre-mode schema, preserving all existing row fields.
            c=sqlite3.connect(path)
            for table in ('intervals','events','observations'):
                c.execute(f'DROP INDEX idx_{table}_mode')
                c.execute(f'ALTER TABLE {table} DROP COLUMN observation_mode')
            c.execute('UPDATE observations SET metadata_json=?',(json.dumps({'person_test_mode':True}),))
            c.execute('UPDATE events SET metadata_json=?',('{bad_json',))
            c.execute("UPDATE intervals SET note='[成人测试] 曾经确认' WHERE category='posture'")
            c.commit();c.close()
            migrated=Database(path)
            self.assertEqual(migrated.observations()[0]['observation_mode'],'adult_test')
            self.assertEqual(migrated.event(1)['observation_mode'],'legacy_unknown')
            self.assertEqual(migrated.event(1)['original_path'],'smiles/old.jpg')
            rows=migrated._connection.execute('SELECT * FROM intervals WHERE id<=3').fetchall()
            self.assertEqual(len(rows),3)
            self.assertEqual({r['observation_mode'] for r in rows},{'legacy_unknown','adult_test'})
            modes=[r['observation_mode'] for r in rows]
            migrated.close();migrated=Database(path)
            self.assertEqual([r['observation_mode'] for r in migrated._connection.execute('SELECT * FROM intervals WHERE id<=3')],modes)
            migrated.close()
            self.assertEqual(mode_from_metadata('{"observation_mode":"infant","person_test_mode":true}'),'legacy_unknown')
            self.assertEqual(mode_from_metadata('{"observation_mode":[]}'),'legacy_unknown')


class ScheduleAndLightweightTest(unittest.TestCase):
    def test_existing_timing_defaults_and_env_override(self):
        with patch.dict(os.environ,{},clear=True):c=Config.from_env()
        self.assertEqual((c.camera_fps,c.analysis_interval_seconds,c.sleep_stable_seconds,
                          c.awake_stable_seconds,c.posture_stable_seconds,
                          c.sleep_motion_threshold,c.motion_threshold), (15,3,30,10,20,2.5,8))
        self.assertEqual((c.vlm_interval_seconds,c.vlm_sleep_interval_seconds,c.vlm_motion_interval_seconds,
                          c.vlm_timeout_seconds,c.vlm_confidence_threshold,c.vlm_stable_seconds),(30,60,15,35,.6,20))
        with patch.dict(os.environ,{'BMON_VLM_TIMEOUT_SECONDS':'60','BMON_SLEEP_STABLE_SECONDS':'42'}):c=Config.from_env()
        self.assertEqual(c.vlm_timeout_seconds,60);self.assertEqual(c.sleep_stable_seconds,42)

    def test_qwen_existing_adaptive_intervals_same_in_both_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db');o=make_observer(db)
            for mode in (False,True):
                o.set_person_test_mode(mode);o.online=True;o.camera.motion_score=0
                self.assertEqual(o._next_interval(),30)
                db.transition('activity','sleeping','test')
                self.assertEqual(o._next_interval(),60)
                o.camera.motion_score=9;self.assertEqual(o._next_interval(),15)
                o.online=False;self.assertEqual(o._next_interval(),5)
            db.close()

    def test_primary_lightweight_cannot_write_intervals_or_photos(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder);worker=app.inference
            callbacks=[];worker.on_smile=lambda *a: callbacks.append(a)
            worker.on_classification=lambda *a:callbacks.append(a)
            result={'activity':'awake_happy','posture':'supine','confidence':.99,
                    'posture_confidence':.99,'reason':'synthetic','smile':True,'happiness_score':.99}
            try:
                for mode in (False,True):
                    app.set_observation_subject(mode)
                    with patch.object(worker,'_analyze',return_value=result.copy()):
                        for _ in range(4):worker._process_frame(Frame(time.time(),b'test',1))
                    self.assertEqual(app.database.current()['activity']['value'],'unknown')
                self.assertEqual(callbacks,[])
            finally:app.stop()

    def test_pending_lightweight_result_is_discarded_on_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            app=make_app(folder);worker=app.inference
            entered,release=threading.Event(),threading.Event()
            def pending(_):
                entered.set();release.wait(3)
                return {'activity':'sleeping'}
            try:
                with patch.object(worker,'_analyze',side_effect=pending):
                    thread=threading.Thread(target=worker._process_frame,args=(Frame(time.time(),b'test',1),))
                    thread.start();self.assertTrue(entered.wait(2));app.set_observation_subject(True)
                    release.set();thread.join(3)
                self.assertIsNone(worker.last_result)
                self.assertEqual(app.database.observations(),[])
            finally:app.stop()

    def test_missing_or_incompatible_optional_posture_does_not_disable_qwen(self):
        with tempfile.TemporaryDirectory() as folder:
            missing=Path(folder)/'posture.onnx';classifier=PostureClassifier(missing)
            classifier.load();self.assertFalse(classifier.online)
            self.assertEqual(classifier.predict(np.zeros((8,8,3),dtype=np.uint8)),('unknown',0))
            missing.write_bytes(b'not an ONNX model')
            classifier.load();self.assertFalse(classifier.online);self.assertIsNotNone(classifier.error)
            db=Database(Path(folder)/'test.db');o=make_observer(db,stable_seconds=0)
            for mode in (False,True):
                o.set_person_test_mode(mode)
                for posture in ('supine','side','prone'):
                    payload=visible();payload['posture']=posture
                    item=normalize_observation(payload,mode)
                    o._apply(item,{});o._apply(item,{})
                    self.assertEqual(db.current()['posture']['value'],posture)
            db.close()

    def test_grayscale_brightness_change_and_single_frame_motion(self):
        camera=Camera('unused',64,36,15)
        def feed(array,ts):
            out=io.BytesIO();Image.fromarray(array.astype(np.uint8)).save(out,'JPEG',quality=100)
            with patch('baby_monitor.camera.time.time',return_value=ts):camera._accept_jpeg(out.getvalue())
        base=np.ones((36,64))*40
        feed(base,10);feed(base+50,11);feed(base+30,12)
        self.assertLess(camera.motion_score,1)
        moved=base+30;moved[:18,:32]+=70
        feed(moved,13);self.assertLess(camera.motion_score,1)
        feed(base+30,14);self.assertGreater(camera.motion_score,8)
        self.assertTrue(camera.online)


if __name__=='__main__':unittest.main()
