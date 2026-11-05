"""Durable asynchronous turns with bounded inference and atomic care commits."""
from __future__ import annotations

import queue
import sqlite3
import threading
import time

from .assistant_common import AssistantError, fields, instant, uid
from .assistant_database import AssistantDatabase
from .assistant_extraction import plan_actions, safe_answer
from .assistant_facts import AssistantFacts
from .assistant_model import AssistantModel
from .assistant_rules import rule_result
from .assistant_summary import DailySummary


class AssistantService:
    def __init__(self,path,model=None,visual_path=None,endpoint='http://127.0.0.1:8010',visual_mode=None):
        self.database=AssistantDatabase(path)
        self.model=model or AssistantModel(endpoint)
        self.facts=AssistantFacts(self.database,visual_path,visual_mode)
        self.summary=DailySummary(self.facts,lambda:self.model)
        self.queue=queue.Queue(maxsize=4)
        self.stop=threading.Event()
        self.worker=threading.Thread(target=self._run,name='assistant-turns',daemon=True)
        self.worker.start()

    VOICE_TITLE='语音记录'

    def voice_record(self,content,timezone='Asia/Shanghai',wait=12.0):
        """Send a spoken sentence through the normal turn pipeline into the real records."""
        cid=self._voice_conversation(timezone)
        reply,_=self.send(cid,'real',dict(content=content,client_message_id='voice-'+uid(),timezone=timezone))
        mid=reply['user_message']['id'];deadline=time.monotonic()+wait
        while reply['status'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(0.2);reply=self.database.poll(cid,mid,'real')
        return reply

    def _voice_conversation(self,timezone):
        for conv in self.database.conversations({'scope':'real','limit':'50'})['conversations']:
            if conv['title']==self.VOICE_TITLE:return conv['id']
        cid=self.database.create_conversation({'scope':'real','timezone':timezone})['conversation']['id']
        self.database.rename(cid,'real',{'title':self.VOICE_TITLE})
        return cid

    def send(self,cid,data_scope,payload,authorized=lambda:True):
        if self.stop.is_set():raise AssistantError('assistant_disabled',503,retryable=True)
        mid,tid,start,pending=self.database.accept(cid,data_scope,payload)
        response=self.database.poll(cid,mid,data_scope,replayed=not start)
        if start:
            try:self.queue.put_nowait((tid,authorized))
            except queue.Full:
                self.database.fail(tid,AssistantError('queue_full',429,retryable=True,retry_after=2))
        return response,202 if pending else 200

    def _run(self):
        try:
            while not self.stop.is_set():
                try:tid,authorized=self.queue.get(timeout=.1)
                except queue.Empty:continue
                try:
                    if not authorized():raise AssistantError('access_disabled',403)
                    turn=self.database.claim(tid)
                    if not turn:continue
                    # 规则优先：常见记录/查询不占用 GPU；识别不了才交给模型聊天
                    result=rule_result(turn,self.database)
                    if result is None:
                        payload=dict(request_id=tid+'-attempt-'+str(turn['attempt']),messages=turn['messages'],scope=turn['data_scope'],timezone=turn['timezone'],reference_at=instant(turn['reference_at']),facts=None,max_new_tokens=64)
                        result=self.model.chat(payload)
                        # 模型只负责聊天：校验顶层结构后丢弃其动作与查询
                        try:fields(result,('answer','actions','query'),('answer','actions','query'))
                        except AssistantError:raise AssistantError('invalid_model_output',502,retryable=True) from None
                        result=dict(result,actions=[],query=None)
                    if self.stop.is_set() or not authorized():raise AssistantError('access_disabled',403)
                    actions=plan_actions(result,turn,self.database)
                    facts=None
                    if result['query'] is not None:
                        facts=self.facts.query(result['query'],turn['data_scope'],turn['timezone'])
                        # 数字由 Python 渲染；模型解释原本就被丢弃，因此不再额外调用模型
                        answer=self.facts.explanation(facts)
                    else:
                        answer=('；'.join(a['message'] for a in actions if a['status']!='saved') if actions else safe_answer(result['answer'],turn['content']))
                    if not answer:
                        answer='；'.join(a['message'] for a in actions if a['status']!='saved') or ('请补充具体情况，我会依据明确的信息帮助你。' if not actions else '')
                    guard=lambda:not self.stop.is_set() and authorized()
                    self.database.finish(turn,answer,actions,facts,guard)
                except AssistantError as exc:
                    self.database.fail(tid,exc)
                except sqlite3.Error:
                    try:self.database.fail(tid,AssistantError('storage_unavailable',503,retryable=True))
                    except sqlite3.Error:pass  # no partial success; startup recovers the interrupted turn
                except Exception:
                    self.database.fail(tid,AssistantError('model_failed',502,retryable=True))
                finally:self.queue.task_done()
        finally:
            # Queued turns are persisted failed, not replayed by accident on restart.
            while True:
                try:tid,_=self.queue.get_nowait()
                except queue.Empty:break
                self.database.fail(tid,AssistantError('service_restarted',503,retryable=True));self.queue.task_done()

    def close(self):
        self.stop.set();self.worker.join(timeout=46)
        if self.worker.is_alive():return False
        self.database.close();return True
