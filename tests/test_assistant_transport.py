"""Real loopback HTTP, with synthetic model replies and no GPU or external API."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

from baby_monitor.assistant_common import AssistantError
from baby_monitor.assistant_model import AssistantModel
from baby_monitor.assistant_service import AssistantService


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.response={'result':{'answer':'您好','actions':[],'query':None}};self.code=200;self.raw=None;self.calls=[];self.delay=0;test=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                test.calls.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                time.sleep(test.delay)
                body=test.raw if test.raw is not None else json.dumps(test.response).encode()
                try:
                    self.send_response(test.code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.model=AssistantModel(f'http://127.0.0.1:{self.server.server_port}',timeout=.2)
        self.payload=dict(request_id='a',messages=[dict(role='user',content='你好')],scope='demo',timezone='Asia/Shanghai',reference_at='2026-10-13T03:00:00Z',facts=None,max_new_tokens=256)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(1)
    def test_end_to_end_async_turn_over_loopback_transport(self):
        with tempfile.TemporaryDirectory() as d:
            service=AssistantService(Path(d)/'db',model=self.model);cid=service.database.create_conversation({'scope':'demo'})['conversation']['id'];r,status=service.send(cid,'demo',dict(content='你好',client_message_id='a'));self.assertEqual(status,202);service.queue.join()
            self.assertEqual(service.database.poll(cid,r['user_message']['id'],'demo')['status'],'completed');self.assertEqual(self.calls[0][0],'/chat');service.close()
    def test_local_only_endpoint_and_body_budget(self):
        for endpoint in ['http://example.invalid','https://127.0.0.1','http://127.0.0.1/chat','http://u:p@127.0.0.1']:
            with self.assertRaises(ValueError):AssistantModel(endpoint)
        messages=[]
        for _ in range(6):messages.extend([dict(role='user',content='你'*4000),dict(role='assistant',content='好'*4000)])
        self.model.chat(dict(self.payload,messages=messages+self.payload['messages']))
        self.assertLess(len(self.calls[0][1]['messages']),13);self.assertLessEqual(len(json.dumps(self.calls[0][1],ensure_ascii=False).encode()),32768)
    def test_busy_and_model_unavailable_errors_are_recoverable(self):
        for status,code in [(429,'queue_full'),(503,'vision_capacity_reserved'),(504,'model_timeout'),(502,'model_failed')]:
            self.code=status;self.response={'error':{'code':code}}
            with self.assertRaises(AssistantError) as e:self.model.chat(self.payload)
            self.assertEqual(e.exception.code,code);self.assertTrue(e.exception.retryable)
    def test_malformed_and_oversized_model_responses_fail(self):
        for raw in [b'not json',b'[]',b'x'*65537,b'{"result":null}']:
            self.raw=raw
            with self.assertRaises(AssistantError) as e:self.model.chat(self.payload)
            self.assertEqual(e.exception.code,'invalid_model_output')

    def test_real_socket_timeout_fails_turn_without_record_acknowledgement(self):
        self.delay=.3;self.model.timeout=.05
        with tempfile.TemporaryDirectory() as d:
            service=AssistantService(Path(d)/'assistant.db',model=self.model)
            try:
                cid=service.database.create_conversation({'scope':'demo'})['conversation']['id']
                accepted,status=service.send(cid,'demo',dict(content='你好',client_message_id='timeout'))
                self.assertEqual(status,202);service.queue.join()
                failed=service.database.poll(cid,accepted['user_message']['id'],'demo')
                self.assertEqual(failed['status'],'failed');self.assertEqual(failed['error']['code'],'model_timeout')
                self.assertIsNone(failed['assistant_message']);self.assertEqual(failed['record_actions'],[])
                self.assertEqual(service.database.connection.execute('SELECT COUNT(*) FROM care_events').fetchone()[0],0)
            finally:service.close()
