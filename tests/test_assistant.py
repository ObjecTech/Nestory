"""Isolated business, race and semantic tests; all records are synthetic Demo data."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from baby_monitor.assistant_common import AssistantError,date_window,instant,now_ms,timestamp
from baby_monitor.assistant_database import AssistantDatabase
from baby_monitor.assistant_extraction import local_times,plan_actions
from baby_monitor.assistant_facts import AssistantFacts
from baby_monitor.assistant_service import AssistantService
from baby_monitor.database import Database

FIXED=timestamp('2026-10-13T08:00:00Z')

def action(kind='feeding',**changes):
    result=dict(operation='create',event_type=kind,occurred_at='2026-10-13T03:00:00Z',end_at=None,amount_ml=150 if kind=='feeding' else None,diaper_type=None,notes='',event_state='past',evidence='宝宝',missing_fields=[],target_event_id=None)
    result.update(changes);return result

class FakeModel:
    def __init__(self,result=None):
        self.result=result or dict(answer='您好。',actions=[],query=None)
        self.calls=[];self.entered=threading.Event();self.release=None;self.error=None
    def chat(self,payload):
        self.calls.append(copy.deepcopy(payload));self.entered.set()
        if self.release:self.release.wait(3)
        if self.error:raise self.error
        return copy.deepcopy(self.result)

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'assistant.db';self.db=AssistantDatabase(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def conv(self,s='demo'):return self.db.create_conversation({'scope':s})['conversation']['id']
    def manual(self,**changes):
        p=dict(scope='demo',timezone='Asia/Shanghai',event_type='feeding',occurred_at='2026-10-12T03:00:00Z',amount_ml=150,client_event_id='manual-1');p.update(changes)
        return self.db.create_event(p)[0]['event']
    def complete(self,cid,client='c1',content='hello',actions=None):
        mid,tid,_,_=self.db.accept(cid,'demo',dict(content=content,client_message_id=client));turn=self.db.claim(tid);self.db.finish(turn,'reply',actions or []);return mid,tid
    def test_new_empty_rename_order_and_persistence(self):
        a,b=self.conv(),self.conv();self.assertEqual(self.db.messages(a,'demo',{})['messages'],[])
        self.db.rename(a,'demo',{'title':'宝宝日常记录','expected_version':1})
        self.assertEqual(self.db.conversations({})['conversations'][0]['id'],a)
        self.complete(a,content='first message')
        self.assertEqual(self.db.conversation(a,'demo')['title'],'宝宝日常记录')
        self.db.close();self.db=AssistantDatabase(self.path)
        self.assertEqual(len(self.db.messages(a,'demo',{})['messages']),2)
        self.assertEqual(self.db.conversation(b,'demo')['title'],'新对话')
    def test_two_conversations_independent_context_and_switch(self):
        a,b=self.conv(),self.conv();self.complete(a,content='A-only');self.complete(b,content='B-only')
        mid,tid,_,_=self.db.accept(a,'demo',dict(content='A2',client_message_id='a2'));turn=self.db.claim(tid)
        self.assertEqual([m['content'] for m in turn['messages']],['A-only','reply','A2'])
        self.assertEqual([m['content'] for m in self.db.messages(b,'demo',{})['messages']],['B-only','reply'])
    def test_message_and_conversation_paging_and_scope_bound_cursors(self):
        a=self.conv()
        for i in range(5):self.complete(a,str(i),str(i))
        page=self.db.messages(a,'demo',{'limit':'3'});seen=[m['id'] for m in page['messages']]
        while page['has_more']:
            page=self.db.messages(a,'demo',{'limit':'3','cursor':page['next_cursor']});seen+= [m['id'] for m in page['messages']]
        self.assertEqual(len(set(seen)),10)
        self.conv();self.conv();p=self.db.conversations({'limit':'1'})
        with self.assertRaises(AssistantError) as e:self.db.conversations({'scope':'real','cursor':p['next_cursor']})
        self.assertEqual(e.exception.code,'invalid_cursor')
    def test_deleted_chat_retains_event_and_revision_without_message_body(self):
        a,b=self.conv(),self.conv();mid,tid,_,_=self.db.accept(a,'demo',dict(content='PRIVATE_CHAT_BODY',client_message_id='a'));turn=self.db.claim(tid)
        card=dict(operation='create',status='saved',reason_code='explicit_past_event',message='已记录',event=None,proposed_event=None,missing_fields=[],target_event_id=None,expected_version=None,expires_at=None,_values=dict(event_type='feeding',timezone='Asia/Shanghai',occurred_at='2026-10-12T03:00:00Z',amount_ml=120))
        self.db.finish(turn,'reply',[card]);eid=self.db.poll(a,mid,'demo')['record_actions'][0]['event']['id']
        self.complete(b,content='B');self.db.delete_conversation(a,'demo')
        self.assertIsNone(self.db.event(eid,'demo')['source_message_id']);self.assertEqual(len(self.db.messages(b,'demo',{})['messages']),2)
        revisions=self.db.connection.execute('SELECT after_json FROM care_event_revisions').fetchone()[0];self.assertNotIn('PRIVATE_CHAT_BODY',revisions)
        self.assertFalse(self.db.connection.execute('PRAGMA foreign_key_check').fetchall())
    def test_delete_racing_completion_never_resurrects(self):
        a=self.conv();mid,tid,_,_=self.db.accept(a,'demo',dict(content='hello',client_message_id='a'));turn=self.db.claim(tid)
        self.db.delete_conversation(a,'demo');self.assertFalse(self.db.finish(turn,'late',[]))
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],0)
    def test_idempotency_and_failed_retry_one_message(self):
        a=self.conv();payload=dict(content='hello',client_message_id='a')
        mid,tid,_,_=self.db.accept(a,'demo',payload)
        self.assertEqual(self.db.accept(a,'demo',payload),(mid,tid,False,True))
        with self.assertRaises(AssistantError) as e:self.db.accept(a,'demo',dict(payload,content='different'))
        self.assertEqual(e.exception.code,'idempotency_conflict')
        self.db.fail(tid,AssistantError('model_unavailable',503,retryable=True))
        self.db.accept(a,'demo',dict(payload,retry=True));turn=self.db.claim(tid);self.db.finish(turn,'reply',[])
        self.assertEqual(len(self.db.messages(a,'demo',{})['messages']),2);self.assertEqual(self.db.poll(a,mid,'demo')['attempt'],2)
        self.assertFalse(self.db.accept(a,'demo',payload)[2])
    def test_restart_marks_inflight_failed_and_retains_reference_time(self):
        a=self.conv();mid,tid,_,_=self.db.accept(a,'demo',dict(content='昨天',client_message_id='a'));ref=self.db.connection.execute('SELECT reference_at FROM chat_turns').fetchone()[0]
        self.db.close();self.db=AssistantDatabase(self.path)
        self.assertEqual(self.db.poll(a,mid,'demo')['error']['code'],'service_restarted')
        self.db.accept(a,'demo',dict(content='昨天',client_message_id='a',retry=True))
        self.assertEqual(self.db.connection.execute('SELECT reference_at FROM chat_turns').fetchone()[0],ref)
    def test_scope_immutable_trigger_and_cross_scope_reads(self):
        a=self.conv();r=self.conv('real');ev=self.manual()
        for f in (lambda:self.db.conversation(a,'real'),lambda:self.db.event(ev['id'],'real'),lambda:self.db.delete_conversation(r,'demo')):
            with self.assertRaises(AssistantError) as e:f()
            self.assertEqual(e.exception.status,404)
        with self.assertRaises(sqlite3.IntegrityError):self.db.connection.execute("UPDATE chat_conversations SET data_scope='real' WHERE id=?",(a,))
        self.db.connection.rollback()
    def test_manual_idempotency_edit_delete_version_and_revisions(self):
        ev=self.manual();self.assertEqual(self.manual()['id'],ev['id'])
        with self.assertRaises(AssistantError):self.manual(amount_ml=120)
        updated=self.db.update_event(ev['id'],'demo',{'expected_version':1,'amount_ml':120})['event'];self.assertEqual(updated['version'],2)
        with self.assertRaises(AssistantError) as e:self.db.update_event(ev['id'],'demo',{'expected_version':1,'amount_ml':100})
        self.assertEqual(e.exception.code,'version_conflict')
        self.db.delete_event(ev['id'],'demo',2);self.assertEqual(self.db.delete_event(ev['id'],'demo',2)['version'],3)
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_event_revisions').fetchone()[0],3)
        self.assertEqual(self.db.events('demo',0,now_ms(),{})['events'],[])
    def test_active_turn_bound_and_global_queue_limit(self):
        a=self.conv();self.db.accept(a,'demo',dict(content='hello',client_message_id='a'))
        with self.assertRaises(AssistantError) as e:self.db.accept(a,'demo',dict(content='next',client_message_id='b'))
        self.assertEqual(e.exception.code,'conversation_busy')
        for _ in range(3):self.db.accept(self.conv(),'demo',dict(content='hello',client_message_id='a'))
        with self.assertRaises(AssistantError) as e:self.db.accept(self.conv(),'demo',dict(content='hello',client_message_id='a'))
        self.assertEqual(e.exception.code,'queue_full')
    def test_atomic_event_and_reply_rollback(self):
        a=self.conv();mid,tid,_,_=self.db.accept(a,'demo',dict(content='hello',client_message_id='a'));turn=self.db.claim(tid)
        self.db.connection.execute("CREATE TRIGGER test_fail BEFORE INSERT ON care_event_revisions BEGIN SELECT RAISE(ABORT,'simulated failure');END;")
        card=dict(operation='create',status='saved',reason_code='explicit_past_event',message='record',event=None,proposed_event=None,missing_fields=[],target_event_id=None,expected_version=None,expires_at=None,_values=dict(event_type='feeding',timezone='Asia/Shanghai',occurred_at='2026-10-12T03:00:00Z',amount_ml=120))
        with self.assertRaises(sqlite3.IntegrityError):self.db.finish(turn,'reply',[card])
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0)
        self.assertIsNone(self.db.poll(a,mid,'demo')['assistant_message'])
    def test_migration_does_not_touch_visual_file(self):
        visual=Path(self.tmp.name)/'visual.db';visual.write_bytes(b'visual sentinel');before=visual.read_bytes()
        self.db.close();self.db=AssistantDatabase(self.path);self.assertEqual(visual.read_bytes(),before)
        self.assertEqual(self.db.connection.execute('SELECT MAX(version) FROM assistant_schema_migrations').fetchone()[0],5)

class ServiceTest(unittest.TestCase):
    def setUp(self):
        # Freeze BEFORE conversation creation too: _updated_now deliberately
        # refuses to move behind existing rows. Patching send alone starts using
        # the real day once the calendar passes this fixed synthetic fixture.
        for target in ('baby_monitor.assistant_database.now_ms','baby_monitor.assistant_common.now_ms'):
            clock=patch(target,return_value=FIXED)
            clock.start();self.addCleanup(clock.stop)
        self.tmp=tempfile.TemporaryDirectory();self.model=FakeModel();self.service=AssistantService(Path(self.tmp.name)/'assistant.db',model=self.model)
        self.db=self.service.database;self.cid=self.db.create_conversation({'scope':'demo'})['conversation']['id']
    def tearDown(self):
        if self.model.release:self.model.release.set()
        self.service.close();self.tmp.cleanup()
    def send(self,content,client='a'):
        with patch('baby_monitor.assistant_database.now_ms',return_value=FIXED):
            result,status=self.service.send(self.cid,'demo',dict(content=content,client_message_id=client,timezone='Asia/Shanghai'))
        self.assertEqual(status,202);self.service.queue.join();return self.db.poll(self.cid,result['user_message']['id'],'demo')
    def test_automatic_feeding_saved_only_after_commit_and_retry_no_duplicate(self):
        self.model.result=dict(answer='已记录成功',actions=[action()],query=None)
        r=self.send('今天上午11点宝宝喝了150ml奶');self.assertEqual(r['status'],'completed');self.assertEqual(r['record_actions'][0]['event']['amount_ml'],150)
        self.assertIn('已记录喂奶 150 ml',r['assistant_message']['content'])
        replay,status=self.service.send(self.cid,'demo',dict(content='今天上午11点宝宝喝了150ml奶',client_message_id='a',timezone='Asia/Shanghai'))
        # 明确记录走规则，不调用模型
        self.assertEqual(status,200);self.assertTrue(replay['replayed']);self.assertEqual(len(self.model.calls),0)
    def test_model_failure_no_success_or_events(self):
        self.model.error=AssistantError('model_unavailable',503,retryable=True)
        r=self.send('你好');self.assertEqual(r['status'],'failed');self.assertIsNone(r['assistant_message']);self.assertEqual(r['record_actions'],[])
    def test_invalid_model_output_is_failed(self):
        # 规则识别不了的句子才走模型；模型输出结构不完整时仍应失败
        self.model.result=dict(answer='记录',actions=[])
        r=self.send('宝宝今天状态不错');self.assertEqual(r['error']['code'],'invalid_model_output')
    def test_delete_while_fake_inference_running_and_queued(self):
        self.model.release=threading.Event();r,_=self.service.send(self.cid,'demo',dict(content='hello',client_message_id='a'))
        self.assertTrue(self.model.entered.wait(1));self.db.delete_conversation(self.cid,'demo');self.model.release.set();self.service.queue.join()
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0],0)
    def test_authorization_revoked_during_generation_writes_nothing(self):
        self.model.release=threading.Event();allowed=[True]
        r,_=self.service.send(self.cid,'demo',dict(content='hello',client_message_id='a'),lambda:allowed[0])
        self.assertTrue(self.model.entered.wait(1));allowed[0]=False;self.model.release.set();self.service.queue.join()
        self.assertEqual(self.db.poll(self.cid,r['user_message']['id'],'demo')['error']['code'],'access_disabled')
    def test_confirmation_idempotency_and_saved_event_survives_delete(self):
        self.model.result=dict(answer='实际喝多少？',actions=[action(occurred_at=None,amount_ml=None,event_state='uncertain')],query=None)
        r=self.send('宝宝刚才喝了奶');card=r['record_actions'][0];self.assertEqual(card['status'],'needs_confirmation')
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0)
        payload=dict(decision='confirm',fields={'occurred_at':'2026-10-13T03:00:00Z','amount_ml':120})
        mid=r['user_message']['id'];aid=card['action_id'];out=self.db.confirm(self.cid,mid,aid,'demo',payload)
        self.assertEqual(out['record_action']['status'],'saved');self.db.confirm(self.cid,mid,aid,'demo',payload)
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],1)
        with self.assertRaises(AssistantError):self.db.confirm(self.cid,mid,aid,'demo',dict(decision='dismiss'))
        self.db.delete_conversation(self.cid,'demo');self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],1)
    def test_correction_requires_explicit_confirmation(self):
        self.model.result=dict(answer='记录',actions=[action()],query=None);first=self.send('今天上午11点宝宝喝了150ml奶')
        eid=first['record_actions'][0]['event']['id']
        self.model.result=dict(answer='确认',actions=[action(operation='update',amount_ml=120,evidence='上一条')],query=None)
        r=self.send('上一条实际喝了120ml，不是150ml','b');c=r['record_actions'][0];self.assertEqual(c['target_event_id'],eid);self.assertEqual(c['status'],'needs_confirmation')
        self.assertEqual(self.db.event(eid,'demo')['amount_ml'],150)
        self.db.confirm(self.cid,r['user_message']['id'],c['action_id'],'demo',{'decision':'confirm'})
        self.assertEqual(self.db.event(eid,'demo')['amount_ml'],120)
    def test_dismiss_and_expire_confirmation(self):
        self.model.result=dict(answer='确认',actions=[action(occurred_at=None,amount_ml=None,event_state='uncertain')],query=None)
        r=self.send('宝宝刚才喝了奶');aid=r['record_actions'][0]['action_id'];mid=r['user_message']['id']
        out=self.db.confirm(self.cid,mid,aid,'demo',{'decision':'dismiss'});self.assertEqual(out['resolution'],'dismissed')
        r=self.send('宝宝刚才喝了奶','b');aid=r['record_actions'][0]['action_id'];mid=r['user_message']['id']
        with self.db.transaction():self.db.connection.execute('UPDATE record_proposals SET expires_at=0 WHERE id=?',(aid,))
        with self.assertRaises(AssistantError) as e:self.db.confirm(self.cid,mid,aid,'demo',{'decision':'confirm'})
        self.assertEqual(e.exception.code,'action_expired')

class ExtractionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=AssistantDatabase(Path(self.tmp.name)/'assistant.db');self.cid=self.db.create_conversation({'scope':'demo'})['conversation']['id']
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def plan(self,content,item):
        turn=dict(id='test-turn',conversation_id=self.cid,data_scope='demo',reference_at=FIXED,timezone='Asia/Shanghai',content=content)
        return plan_actions(dict(answer='reply',actions=[item],query=None),turn,self.db)[0]
    def test_diaper_unspecified_kind_unknown(self):
        r=self.plan('宝宝今天下午两点换了尿布',action('diaper',occurred_at='2026-10-13T06:00:00Z',evidence='宝宝'))
        self.assertEqual(r['status'],'saved');self.assertEqual(r['_values']['diaper_type'],'unknown')
    def test_manual_nap_and_cross_midnight(self):
        r=self.plan('宝宝今天下午1点半到2点20分补觉',action('sleep',occurred_at='2026-10-13T05:30:00Z',end_at='2026-10-13T06:20:00Z'))
        self.assertEqual(r['status'],'saved')
        t=local_times('昨晚23:40到今天00:20',FIXED,'Asia/Shanghai');self.assertEqual(t[1]-t[0],2400000)
        self.assertEqual(local_times('昨天23:40到00:20',FIXED,'Asia/Shanghai'),[])
    def test_fuzzy_time_prepared_milk_future_and_negation(self):
        cases=['宝宝刚才喝了150ml奶','宝宝今天上午11点冲泡了150ml奶','宝宝明天下午三点准备喝150ml奶','宝宝今天上午11点没有喝150ml奶']
        for c in cases:
            with self.subTest(c=c):self.assertNotEqual(self.plan(c,action())['status'],'saved')
    def test_clear_completed_activity(self):
        r=self.plan('宝宝今天下午两点散步了',action('activity',occurred_at='2026-10-13T06:00:00Z',notes='散步'))
        self.assertEqual(r['status'],'saved')
    def test_hallucinated_evidence_and_wrong_date(self):
        with self.assertRaises(AssistantError):self.plan('你好',action(evidence='喝奶'))
        r=self.plan('宝宝今天上午11点喝了150ml奶',action(occurred_at='2026-10-12T03:00:00Z'))
        self.assertEqual(r['status'],'needs_confirmation')

class FactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=AssistantDatabase(Path(self.tmp.name)/'assistant.db');self.visual=Database(Path(self.tmp.name)/'visual.db');self.facts=AssistantFacts(self.db,self.visual.path)
    def tearDown(self):self.db.close();self.visual.close();self.tmp.cleanup()
    def sleep(self,a,b,client,data_scope='demo'):
        self.db.create_event(dict(scope=data_scope,timezone='Asia/Shanghai',event_type='sleep',occurred_at=a,end_at=b,notes='',client_event_id=client))
    def test_overlapping_manual_sleep_union_and_no_camera_sum(self):
        self.sleep('2026-10-12T05:00:00Z','2026-10-12T06:00:00Z','a');self.sleep('2026-10-12T05:30:00Z','2026-10-12T06:30:00Z','b')
        r=self.facts.sleep_summary({'scope':'demo','from':'2026-10-12','to':'2026-10-12'})
        self.assertEqual(r['parent_sleep']['seconds'],5400);self.assertIsNone(r['camera_sleep']['seconds']);self.assertIsNone(r['combined_sleep_seconds'])
        real=self.facts.sleep_summary({'scope':'real','from':'2026-10-12','to':'2026-10-12'});self.assertIsNone(real['parent_sleep']['seconds'])
    def test_clipping_and_dst_calendar_day(self):
        self.sleep('2026-10-11T15:30:00Z','2026-10-11T16:30:00Z','a')
        r=self.facts.sleep_summary({'scope':'demo','from':'2026-10-12','to':'2026-10-12'});self.assertEqual(r['parent_sleep']['seconds'],1800)
        _,_,_,a,b=date_window({'from':'2026-11-01','to':'2026-11-01','timezone':'America/New_York'});self.assertEqual(b-a,25*3600000)
    def test_real_visual_evidence_filters_adult_and_legacy_open_not_total(self):
        with self.visual._lock,self.visual._connection:
            self.visual._connection.execute('UPDATE intervals SET end_ts=? WHERE end_ts IS NULL',(time.time(),))
            for mode in ('infant','adult_test','legacy_unknown'):
                self.visual._connection.execute("INSERT INTO intervals(category,value,start_ts,end_ts,source,observation_mode) VALUES('activity','sleeping',?,NULL,'synthetic',?)",(timestamp('2026-10-12T03:00:00Z')/1000,mode)) if mode=='infant' else self.visual._connection.execute("INSERT INTO intervals(category,value,start_ts,end_ts,source,observation_mode) VALUES('activity','sleeping',?,?, 'synthetic',?)",(timestamp('2026-10-12T03:00:00Z')/1000,timestamp('2026-10-12T04:00:00Z')/1000,mode))
        facts=self.facts.query({'kind':'sleep_summary','from':'2026-10-12','to':'2026-10-12'},'real','Asia/Shanghai')
        self.assertIsNone(facts['camera_sleep']['seconds']);self.assertEqual(len(facts['visual_evidence']['intervals']),1)
        demo=self.facts.query({'kind':'sleep_summary','from':'2026-10-12','to':'2026-10-12'},'demo','Asia/Shanghai');self.assertNotIn('visual_evidence',demo)

class ExtendedBusinessTest(unittest.TestCase):
    setUp=ServiceTest.setUp
    tearDown=ServiceTest.tearDown
    send=ServiceTest.send
    def test_linked_chat_clarification_resolves_original_without_duplicate(self):
        self.model.result=dict(answer='实际多少？',actions=[action(occurred_at=None,amount_ml=None,event_state='uncertain')],query=None)
        first=self.send('宝宝刚才喝了奶');aid=first['record_actions'][0]['action_id']
        self.model.result=dict(answer='好的',actions=[action(amount_ml=120)],query=None)
        with patch('baby_monitor.assistant_database.now_ms',return_value=FIXED):
            second,status=self.service.send(self.cid,'demo',dict(content='宝宝今天上午11点喝了120ml奶',client_message_id='b',record_action_id=aid))
        self.service.queue.join();r=self.db.poll(self.cid,second['user_message']['id'],'demo')
        self.assertEqual(r['record_actions'][0]['event']['amount_ml'],120)
        original=self.db.poll(self.cid,first['user_message']['id'],'demo');self.assertEqual(original['record_actions'][0]['status'],'saved')
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],1)
    def test_partial_linked_clarification_alias_confirm_no_duplicate(self):
        self.model.result=dict(answer='实际多少？',actions=[action(occurred_at=None,amount_ml=None,event_state='uncertain')],query=None)
        first=self.send('宝宝刚才喝了奶');aid=first['record_actions'][0]['action_id']
        self.model.result=dict(answer='时间呢？',actions=[action(occurred_at=None,amount_ml=120)],query=None)
        second,status=self.service.send(self.cid,'demo',dict(content='宝宝喝了120ml奶',client_message_id='b',record_action_id=aid));self.service.queue.join()
        r=self.db.poll(self.cid,second['user_message']['id'],'demo');alias=r['record_actions'][0]
        self.assertEqual(alias['status'],'needs_confirmation')
        self.db.confirm(self.cid,second['user_message']['id'],alias['action_id'],'demo',dict(decision='confirm',fields={'occurred_at':'2026-10-12T03:00:00Z'}))
        self.assertEqual(self.db.poll(self.cid,first['user_message']['id'],'demo')['record_actions'][0]['status'],'saved')
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],1)
    def test_confirmation_rejects_stale_revision_after_form_edit(self):
        self.model.result=dict(answer='记录',actions=[action()],query=None);first=self.send('宝宝今天上午11点喝了150ml奶');eid=first['record_actions'][0]['event']['id']
        self.model.result=dict(answer='确认',actions=[action(operation='update',amount_ml=120,evidence='上一条')],query=None)
        second=self.send('上一条实际喝了120ml，不是150ml','b');c=second['record_actions'][0]
        self.db.update_event(eid,'demo',{'expected_version':1,'amount_ml':130})
        with self.assertRaises(AssistantError) as e:self.db.confirm(self.cid,second['user_message']['id'],c['action_id'],'demo',{'decision':'confirm'})
        self.assertEqual(e.exception.code,'version_conflict');self.assertEqual(self.db.event(eid,'demo')['amount_ml'],130)
    def test_query_facts_two_calls_python_totals_and_no_generated_numbers(self):
        class QueryModel:
            def chat(self,p):
                if p['facts'] is None:return dict(answer='记录查询',actions=[],query={'kind':'sleep_summary','from':'2026-10-12','to':'2026-10-12'})
                return dict(answer='睡了99999小时',actions=[],query=None)
        self.service.model=QueryModel()
        self.db.create_event(dict(scope='demo',timezone='Asia/Shanghai',event_type='sleep',occurred_at='2026-10-12T05:00:00Z',end_at='2026-10-12T06:00:00Z',client_event_id='sleep1'))
        r=self.send('昨天睡了多久');self.assertEqual(r['facts']['parent_sleep']['seconds'],3600)
        self.assertIn('60 分钟',r['assistant_message']['content']);self.assertNotIn('99999',r['assistant_message']['content'])
    def test_concurrent_same_key_accepts_one_turn(self):
        self.model.release=threading.Event();barrier=threading.Barrier(5);results=[]
        def send():
            barrier.wait();results.append(self.service.send(self.cid,'demo',dict(content='你好',client_message_id='concurrent')))
        threads=[threading.Thread(target=send) for _ in range(4)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join(2)
        self.assertEqual(len({r[0]['user_message']['id'] for r in results}),1)
        self.model.release.set();self.service.queue.join();self.assertEqual(len(self.model.calls),1)
    def test_delete_queued_conversation_skips_model_call(self):
        self.model.release=threading.Event();self.service.send(self.cid,'demo',dict(content='first',client_message_id='first'));self.assertTrue(self.model.entered.wait(1))
        second=self.db.create_conversation({'scope':'demo'})['conversation']['id'];self.service.send(second,'demo',dict(content='queued',client_message_id='q'))
        self.db.delete_conversation(second,'demo');self.model.release.set();self.service.queue.join();self.assertEqual(len(self.model.calls),1)
    def test_same_message_multiple_grounded_events(self):
        first='宝宝今天上午11点喝了150ml奶';second='宝宝今天下午两点换了尿布'
        self.model.result=dict(answer='整理记录',actions=[action(evidence=first),action('diaper',occurred_at='2026-10-13T06:00:00Z',evidence=second)],query=None)
        # Test transaction at a fixed synthetic afternoon; finish validation uses actual clock, so
        # only extraction/DB planning is tested for this future-to-host synthetic sample.
        mid,tid,_,_=self.db.accept(self.cid,'demo',dict(content=first+'；'+second,client_message_id='multi'))
        turn=self.db.claim(tid);turn['reference_at']=FIXED
        cards=plan_actions(self.model.result,turn,self.db)
        self.assertEqual([c['status'] for c in cards],['saved','saved'])
        self.assertNotEqual(cards[0]['_values']['occurred_at'],cards[1]['_values']['occurred_at'])
    def test_model_cannot_duplicate_same_event_in_one_turn(self):
        result=dict(answer='记录',actions=[action(evidence='宝宝今天上午11点喝了150ml奶')]*2,query=None)
        mid,tid,_,_=self.db.accept(self.cid,'demo',dict(content='宝宝今天上午11点喝了150ml奶',client_message_id='dup'))
        turn=self.db.claim(tid);turn['reference_at']=FIXED
        with self.assertRaises(AssistantError) as e:plan_actions(result,turn,self.db)
        self.assertEqual(e.exception.code,'invalid_model_output')
        self.assertEqual(self.db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0)

class FailureAndMigrationTest(unittest.TestCase):
    def test_scope_source_trigger_rejects_cross_domain_link(self):
        with tempfile.TemporaryDirectory() as d:
            db=AssistantDatabase(Path(d)/'db');cid=db.create_conversation({'scope':'real'})['conversation']['id'];mid,tid,_,_=db.accept(cid,'real',{'content':'你好','client_message_id':'a'})
            with self.assertRaises(sqlite3.IntegrityError):
                with db.transaction():db._create_event({'event_type':'feeding','timezone':'Asia/Shanghai','occurred_at':1,'end_at':None,'amount_ml':120,'diaper_type':None,'notes':''},'demo','parent_chat',mid,'bad','auto_chat')
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0);db.close()
    def test_version_one_database_incrementally_upgrades_without_row_loss(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            c=sqlite3.connect(path);c.executescript(Path('baby_monitor/assistant_schema.sql').read_text());c.execute('INSERT INTO assistant_schema_migrations VALUES(1,1)')
            c.execute("INSERT INTO chat_conversations VALUES('old','历史对话','manual','demo','Asia/Shanghai',1,1,1)");c.commit();c.close()
            db=AssistantDatabase(path);self.assertEqual(db.conversation('old','demo')['title'],'历史对话');self.assertEqual(db.connection.execute('SELECT MAX(version) FROM assistant_schema_migrations').fetchone()[0],5);db.close()
    def test_commit_failure_is_failed_turn_not_false_acknowledgement(self):
        with tempfile.TemporaryDirectory() as d, patch('baby_monitor.assistant_database.now_ms',return_value=FIXED), patch('baby_monitor.assistant_common.now_ms',return_value=FIXED):
            model=FakeModel(dict(answer='已记录成功',actions=[action()],query=None));service=AssistantService(Path(d)/'db',model=model);db=service.database;cid=db.create_conversation({'scope':'demo'})['conversation']['id']
            db.connection.execute("CREATE TRIGGER simulated_failure BEFORE INSERT ON care_event_revisions BEGIN SELECT RAISE(ABORT,'simulated');END;")
            with patch('baby_monitor.assistant_database.now_ms',return_value=FIXED):r,_=service.send(cid,'demo',dict(content='宝宝今天上午11点喝了150ml奶',client_message_id='a'))
            service.queue.join();failed=db.poll(cid,r['user_message']['id'],'demo');self.assertEqual(failed['status'],'failed');self.assertEqual(failed['error']['code'],'storage_unavailable');self.assertIsNone(failed['assistant_message']);self.assertEqual(failed['record_actions'],[])
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0);service.close()
    def test_explicit_scope_filter_message_polls_and_proposals(self):
        with tempfile.TemporaryDirectory() as d:
            db=AssistantDatabase(Path(d)/'db');cid=db.create_conversation({'scope':'demo'})['conversation']['id'];mid,tid,_,_=db.accept(cid,'demo',dict(content='你好',client_message_id='a'))
            with self.assertRaises(AssistantError) as e:db.poll(cid,mid,'real')
            self.assertEqual(e.exception.status,404)
            other=db.create_conversation({'scope':'demo'})['conversation']['id']
            with self.assertRaises(AssistantError) as e:db.poll(other,mid,'demo')
            self.assertEqual(e.exception.code,'message_not_found');db.close()
    def test_input_limits_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as d:
            db=AssistantDatabase(Path(d)/'db');cid=db.create_conversation({'scope':'demo'})['conversation']['id']
            for payload in [dict(content='x'*4001,client_message_id='a'),dict(content='x',client_message_id='../a'),dict(content='x',client_message_id='a',retry=1),dict(content='x',client_message_id='a',scope='real')]:
                with self.assertRaises(AssistantError):db.accept(cid,'demo',payload)
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0],0);db.close()

class AppIsolationTest(unittest.TestCase):
    def test_failed_assistant_initialization_does_not_disable_visual_components(self):
        from baby_monitor.app import BabyMonitorApp
        from baby_monitor.config import Config
        with tempfile.TemporaryDirectory() as d:
            env={'BMON_APP_DIR':str(Path.cwd()),'BMON_DATA_DIR':d,'BMON_MEDIA_DIR':str(Path(d)/'media'),'BMON_MEDIA_MOUNT':str(Path(d)/'media'),
                'BMON_DEMO':'true','BMON_MODEL_ENABLED':'false','BMON_VLM_ENABLED':'false','BMON_VOICE_ENABLED':'false','BMON_PLAYBACK_BACKEND':'silent','BMON_ASSISTANT_ENABLED':'true'}
            with patch.dict('os.environ',env),patch('baby_monitor.assistant_service.AssistantService',side_effect=RuntimeError('synthetic migration failure')):
                app=BabyMonitorApp(Config.from_env())
            self.assertIsNone(app.assistant);self.assertEqual(app.assistant_error,'storage_unavailable')
            app.start();time.sleep(.05);self.assertIsNotNone(app.camera.latest());app.stop()
    def test_disabled_assistant_does_not_create_database(self):
        from baby_monitor.app import BabyMonitorApp
        from baby_monitor.config import Config
        with tempfile.TemporaryDirectory() as d:
            env={'BMON_APP_DIR':str(Path.cwd()),'BMON_DATA_DIR':d,'BMON_MEDIA_DIR':str(Path(d)/'media'),'BMON_MEDIA_MOUNT':str(Path(d)/'media'),
                'BMON_DEMO':'true','BMON_MODEL_ENABLED':'false','BMON_VLM_ENABLED':'false','BMON_VOICE_ENABLED':'false','BMON_PLAYBACK_BACKEND':'silent','BMON_ASSISTANT_ENABLED':'false'}
            with patch.dict('os.environ',env):app=BabyMonitorApp(Config.from_env())
            self.assertIsNone(app.assistant);self.assertFalse((Path(d)/'assistant.db').exists());app.stop()


class SingleControllerTest(unittest.TestCase):
    def test_second_controller_cannot_fail_an_active_turn(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'assistant.db';db=AssistantDatabase(path)
            cid=db.create_conversation({'scope':'demo'})['conversation']['id']
            mid,tid,_,_=db.accept(cid,'demo',dict(content='你好',client_message_id='a'))
            with self.assertRaises(AssistantError) as e:AssistantDatabase(path)
            self.assertEqual(e.exception.code,'storage_unavailable');self.assertEqual(db.poll(cid,mid,'demo')['status'],'queued')
            db.close();reopened=AssistantDatabase(path);self.assertEqual(reopened.poll(cid,mid,'demo')['error']['code'],'service_restarted');reopened.close()
