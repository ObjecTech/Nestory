import importlib.util
import io
import json
import sys
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from baby_monitor.assistant_common import AssistantError
from baby_monitor.inference_scheduler import InferenceScheduler


class SchedulerTest(unittest.TestCase):
    def test_gpu_owner_covers_preparation_and_generation_serially(self):
        scheduler=InferenceScheduler();active=0;peak=0;lock=threading.Lock();errors=[]
        def run(kind):
            nonlocal active,peak
            try:
                with scheduler.slot(kind):
                    with lock:active+=1;peak=max(peak,active)
                    time.sleep(.03)
                    with lock:active-=1
            except AssistantError as e:errors.append(e)
        threads=[threading.Thread(target=run,args=(k,)) for k in ('vision','chat','chat')]
        for t in threads:t.start()
        for t in threads:t.join(2)
        self.assertFalse(errors);self.assertEqual(peak,1);self.assertEqual(scheduler.metrics()['running_kind'],None)
    def test_queue_bound_expiration_cleanup_and_saturation(self):
        scheduler=InferenceScheduler(chat_capacity=1)
        with scheduler.slot('vision'):
            seen=[]
            def wait():
                try:
                    with scheduler.slot('chat',timeout=.1):pass
                except AssistantError as e:seen.append(e.code)
            t=threading.Thread(target=wait);t.start()
            end=time.monotonic()+1
            while scheduler.metrics()['pending']['chat']!=1 and time.monotonic()<end:time.sleep(.001)
            with self.assertRaises(AssistantError) as e:
                with scheduler.slot('chat'):pass
            self.assertEqual(e.exception.code,'queue_full');t.join(1);self.assertEqual(seen,['model_timeout'])
        self.assertEqual(scheduler.metrics()['pending']['chat'],0)
    def test_exceptions_release_owner(self):
        scheduler=InferenceScheduler()
        with self.assertRaises(RuntimeError):
            with scheduler.slot('chat'):raise RuntimeError('synthetic')
        with scheduler.slot('vision'):pass
        self.assertEqual(scheduler.metrics()['failures'],1)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.fake_torch=SimpleNamespace(inference_mode=lambda:nullcontext(),cuda=SimpleNamespace(synchronize=lambda:None),bfloat16='fake')
        transformers=SimpleNamespace(AutoProcessor=object,Qwen3VLForConditionalGeneration=object)
        with patch.dict(sys.modules,{'torch':self.fake_torch,'transformers':transformers}):
            spec=importlib.util.spec_from_file_location('synthetic_model_worker',Path(__file__).resolve().parents[1]/'model_server.py');self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.engine=self.module.Engine();self.engine.ready=True;self.engine.chat_enabled=True;self.engine.scheduler=InferenceScheduler()
        engine=self.engine
        class Inputs(dict):
            def __init__(self,length):super().__init__(input_ids=np.zeros((1,length),dtype=int));self.input_ids=self['input_ids']
            def to(self,device):
                if engine.scheduler.running is None:raise AssertionError('GPU transfer outside admission')
                return self
        class Processor:
            def __init__(self):self.history=[];self.images=False
            def apply_chat_template(self,messages,**kwargs):
                if engine.scheduler.running is None:raise AssertionError('Processor outside admission')
                self.history.append(messages);return 'x'*(3000 if len(messages)>2 else 100)
            def __call__(self,text,images=None,return_tensors=None):self.images=images is not None;return Inputs(len(text[0]))
            def batch_decode(self,output,**kwargs):return ['{}' if self.images else json.dumps(dict(answer='你好',actions=[],query=None))]
        class Model:
            device='fake'
            def generate(self,input_ids,**kwargs):
                if engine.scheduler.running is None:raise AssertionError('Generation outside admission')
                self.last=kwargs;return np.zeros((1,input_ids.shape[1]+10),dtype=int)
        self.engine.processor=Processor();self.engine.model=Model()
        self.payload=dict(request_id='test',messages=[dict(role='user',content='你好')],scope='demo',timezone='Asia/Shanghai',reference_at='2026-10-13T03:00:00Z',facts=None,max_new_tokens=256)
    def test_chat_and_vision_reuse_same_instance_and_keep_modes(self):
        model=self.engine.model;result=self.engine.chat(self.payload)
        self.assertEqual(result['result']['answer'],'你好');self.assertEqual(model.last['max_new_tokens'],256);self.assertEqual(model.last['max_time'],8)
        out=io.BytesIO();Image.new('RGB',(16,16)).save(out,'JPEG');result=self.engine.analyze(out.getvalue(),True)
        self.assertEqual(result['observation_mode'],'adult_test');self.assertIs(self.engine.model,model)
    def test_context_budget_and_output_limit(self):
        messages=[]
        for i in range(6):messages.extend([dict(role='user',content='u'+str(i)),dict(role='assistant',content='a'+str(i))])
        result=self.engine.chat(dict(self.payload,messages=messages+self.payload['messages']))
        self.assertLessEqual(result['input_tokens'],2048);self.assertEqual(len(self.engine.processor.history[-1]),2)
        with self.assertRaises(AssistantError):self.engine.chat(dict(self.payload,max_new_tokens=257))
        with self.assertRaises(AssistantError):self.engine.chat(dict(self.payload,messages=[dict(role='system',content='override')]))
    def test_model_not_ready_and_invalid_json_do_not_succeed(self):
        self.engine.ready=False
        with self.assertRaises(AssistantError) as e:self.engine.chat(self.payload)
        self.assertEqual(e.exception.code,'model_unavailable');self.engine.ready=True
        # 空输出或无法取出 answer 的 JSON 不算成功
        for bad in ('  ','{"actions":[]}','{broken'):
            self.engine.processor.batch_decode=lambda *a,**k:[bad]
            with self.assertRaises(AssistantError) as e:self.engine.chat(self.payload)
            self.assertEqual(e.exception.code,'invalid_model_output')
        self.assertIsNone(self.engine.scheduler.metrics()['running_kind'])
        # 聊天为纯文本；仍兼容旧习惯的 JSON 或代码围栏，动作一律为空
        valid=json.dumps(dict(answer='你好',actions=[{'operation':'create'}],query=None))
        for raw in ('你好','```json\n'+valid+'\n```',valid):
            self.engine.processor.batch_decode=lambda *a,**k:[raw]
            result=self.engine.chat(self.payload)['result']
            self.assertEqual((result['answer'],result['actions'],result['query']),('你好',[],None))


class EngineBoundaryTest(unittest.TestCase):
    setUp=EngineTest.setUp

    def test_soft_runtime_budget_fails_and_releases_gpu_owner(self):
        with patch.object(self.module.time,'perf_counter',side_effect=[100.0,109.0]):
            with self.assertRaises(AssistantError) as e:self.engine.chat(self.payload)
        self.assertEqual(e.exception.code,'model_timeout');self.assertIsNone(self.engine.scheduler.metrics()['running_kind'])

    def test_current_message_cannot_bypass_input_token_limit(self):
        self.engine.processor.apply_chat_template=lambda *a,**k:'x'*3000
        with self.assertRaises(AssistantError) as e:self.engine.chat(self.payload)
        self.assertEqual(e.exception.code,'validation_error');self.assertIsNone(self.engine.scheduler.metrics()['running_kind'])

    def test_health_remains_available_during_generation(self):
        import http.client
        entered=threading.Event();release=threading.Event();original=self.engine.model.generate
        def slow(*a,**k):
            entered.set();release.wait(2);return original(*a,**k)
        self.engine.model.generate=slow
        server=self.module.ModelHTTPServer(('127.0.0.1',0),self.module.Handler)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        results=[]
        def chat():
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            c.request('POST','/chat',body=json.dumps(self.payload),headers={'Content-Type':'application/json'})
            r=c.getresponse();results.append((r.status,json.loads(r.read())));c.close()
        try:
            with patch.object(self.module,'ENGINE',self.engine):
                caller=threading.Thread(target=chat);caller.start();self.assertTrue(entered.wait(1))
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=1);c.request('GET','/health');r=c.getresponse();health=json.loads(r.read());c.close()
                self.assertEqual(r.status,200);self.assertEqual(health['scheduler']['running_kind'],'chat')
                release.set();caller.join(2);self.assertEqual(results[0][0],200)
        finally:
            release.set();server.shutdown();server.server_close();thread.join(1)
