"""Black-box tests of the frozen 13-route contract and write authorization."""
import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from access_control import AccessStateStore
from baby_monitor.assistant_service import AssistantService
from server import Handler,MonitorServer
from security import AuthConfig,make_password_hash


class Model:
    def __init__(self):self.result=dict(answer='您好',actions=[],query=None)
    def chat(self,payload):return self.result

class AssistantHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.password='TEST_ONLY_ASSISTANT_PASSWORD';cls.hash=make_password_hash(cls.password)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.model=Model();self.service=AssistantService(root/'assistant.db',model=self.model)
        self.app=SimpleNamespace(assistant=self.service,assistant_error=None)
        config=AuthConfig('TEST_ONLY_OWNER',self.hash,'http://127.0.0.1')
        self.server=MonitorServer(('127.0.0.1',0),Handler,self.app,AccessStateStore(root/'access.json'),config)
        self.port=self.server.server_port;self.origin=f'http://127.0.0.1:{self.port}';self.server.auth.config=replace(config,origin=self.origin)
        self.token,self.csrf=self.server.auth.login('TEST_ONLY_OWNER',self.password)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.service.close();self.tmp.cleanup()
    def request(self,method,path,body=None,*,auth=True,csrf=True,origin=None,headers=None):
        h={'Origin':origin or self.origin}
        if auth:h['Cookie']='bmon_session='+self.token
        if csrf:h['X-CSRF-Token']=self.csrf
        if isinstance(body,dict):body=json.dumps(body);h['Content-Type']='application/json'
        if headers:h.update(headers)
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3);c.request(method,'/api/assistant'+path,body=body,headers=h)
        r=c.getresponse();raw=r.read();out=(r.status,dict(r.getheaders()),json.loads(raw));c.close();return out
    def conv(self,data_scope='demo'):
        status,_,body=self.request('POST','/conversations',{'scope':data_scope});self.assertEqual(status,201);return body['conversation']['id']
    def manual(self):
        status,_,body=self.request('POST','/care-events',{'scope':'demo','event_type':'feeding','timezone':'Asia/Shanghai','occurred_at':'2026-10-12T03:00:00Z','amount_ml':150,'client_event_id':'form1'});self.assertEqual(status,201);return body['event']
    def test_all_thirteen_routes_end_to_end(self):
        cid=self.conv();self.assertEqual(self.request('GET','/conversations?scope=demo')[0],200)
        self.assertEqual(self.request('PATCH',f'/conversations/{cid}?scope=demo',{'title':'更正记录'})[0],200)
        path=f'/conversations/{cid}/messages?scope=demo'
        self.assertEqual(self.request('GET',path)[2]['messages'],[])
        self.model.result={'answer':'请确认时间和数量','query':None,'actions':[dict(operation='create',event_type='feeding',occurred_at=None,end_at=None,amount_ml=None,diaper_type=None,notes='',event_state='uncertain',evidence='宝宝',missing_fields=['occurred_at','amount_ml'],target_event_id=None)]}
        status,_,accepted=self.request('POST',path,{'content':'宝宝刚才喝了奶','client_message_id':'msg1','timezone':'Asia/Shanghai'})
        self.assertEqual(status,202);self.assertEqual(accepted['status'],'queued');self.assertIsNone(accepted['assistant_message'])
        self.service.queue.join();mid=accepted['user_message']['id'];poll=f'/conversations/{cid}/messages/{mid}?scope=demo'
        status,_,done=self.request('GET',poll);self.assertEqual(status,200);self.assertEqual(done['status'],'completed')
        aid=done['record_actions'][0]['action_id']
        status,_,confirm=self.request('POST',f'/conversations/{cid}/messages/{mid}/record-actions/{aid}/confirm?scope=demo',{'decision':'confirm','fields':{'occurred_at':'2026-10-12T03:00:00Z','amount_ml':120}})
        self.assertEqual(status,200);self.assertEqual(confirm['record_action']['status'],'saved')
        event=self.manual();eid=event['id']
        self.assertEqual(self.request('GET','/care-events?scope=demo&from=2026-10-12&to=2026-10-12')[0],200)
        status,_,edit=self.request('PATCH',f'/care-events/{eid}?scope=demo',{'expected_version':1,'amount_ml':125});self.assertEqual(status,200);self.assertEqual(edit['event']['version'],2)
        self.assertEqual(self.request('DELETE',f'/care-events/{eid}?scope=demo&expected_version=2')[0],200)
        status,_,summary=self.request('GET','/sleep-summary?scope=real&from=2026-10-12&to=2026-10-12');self.assertEqual(status,200);self.assertIsNone(summary['camera_sleep']['seconds'])
        self.assertEqual(self.request('DELETE',f'/conversations/{cid}?scope=demo')[0],200)
        self.assertEqual(self.request('GET',poll)[0],404)
        status,_,events=self.request('GET','/care-events?scope=demo&from=2026-10-12&to=2026-10-12');self.assertEqual(len(events['events']),1)
    def test_all_mutating_methods_require_session_origin_and_csrf(self):
        cid=self.conv();eid=self.manual()['id']
        for method,path,payload in [('POST','/conversations',{'scope':'demo'}),('PATCH',f'/conversations/{cid}?scope=demo',{'title':'title'}),('DELETE',f'/conversations/{cid}?scope=demo',None),('PATCH',f'/care-events/{eid}?scope=demo',{'expected_version':1,'amount_ml':120}),('DELETE',f'/care-events/{eid}?scope=demo&expected_version=1',None)]:
            for options,expected,code in [({'auth':False},401,'authentication_required'),({'csrf':False},403,'csrf_failed'),({'origin':'https://evil.invalid'},403,'origin_forbidden')]:
                with self.subTest(method=method,path=path,options=options):
                    status,headers,body=self.request(method,path,payload,**options);self.assertEqual(status,expected);self.assertEqual(body['error']['code'],code)
                    self.assertIsInstance(body['error']['details'],list);self.assertIn('request_id',body);self.assertEqual(headers['Cache-Control'],'no-store')
    def test_scope_wrong_id_host_access_and_disabled(self):
        cid=self.conv('demo');self.assertEqual(self.request('GET',f'/conversations/{cid}/messages?scope=real')[0],404)
        self.assertEqual(self.request('DELETE',f'/conversations/{cid}')[2]['error']['code'],'scope_required')
        self.assertEqual(self.request('GET','/conversations',headers={'Host':'wrong.invalid'})[0],421)
        self.server.access_store.update('lan_enabled',False);self.assertEqual(self.request('GET','/conversations')[0],403)
        self.server.access_store.update('lan_enabled',True);self.app.assistant=None
        self.assertEqual(self.request('GET','/conversations')[2]['error']['code'],'assistant_disabled');self.app.assistant=self.service
    def test_invalid_body_duplicate_keys_large_input_timezone_and_unknown_fields(self):
        for body in ['{}','{"scope":"demo","scope":"real"}', '{bad', '[]']:
            self.assertIn(self.request('POST','/conversations',body,headers={'Content-Type':'application/json'})[0],(400,422))
        self.assertEqual(self.request('POST','/conversations',' '*32769,headers={'Content-Type':'application/json'})[0],413)
        self.assertEqual(self.request('POST','/conversations',{'scope':'demo','timezone':'Bad/Zone'})[2]['error']['code'],'invalid_timezone')
        self.assertEqual(self.request('POST','/conversations',{'scope':'demo','unexpected':1})[0],422)
        self.assertEqual(self.request('POST','/conversations','{}',headers={'Content-Type':'text/plain'})[0],415)
    def test_method_errors_validation_bounds_and_cursor(self):
        status,headers,body=self.request('PUT','/conversations',{'scope':'demo'});self.assertEqual(status,405);self.assertEqual(headers['Allow'],'GET, POST')
        self.assertEqual(self.request('GET','/missing')[0],404)
        for q in ['limit=0','limit=101','cursor=bad','scope=oops','scope=demo&scope=real']:
            self.assertIn(self.request('GET','/conversations?'+q)[0],(400,422))
        self.assertEqual(self.request('GET','/care-events?from=2026-01-01&to=2026-04-01')[0],422)
    def test_logout_and_restart_history_recovery(self):
        cid=self.conv();self.server.auth.revoke(self.token);self.assertEqual(self.request('GET','/conversations')[0],401)
        self.token,self.csrf=self.server.auth.login('TEST_ONLY_OWNER',self.password)
        self.assertEqual(self.request('GET','/conversations')[2]['conversations'][0]['id'],cid)
    def test_failed_turn_200_is_not_model_success(self):
        from baby_monitor.assistant_common import AssistantError
        class Broken:
            def chat(self,p):raise AssistantError('model_timeout',504,retryable=True)
        self.service.model=Broken();cid=self.conv();path=f'/conversations/{cid}/messages?scope=demo'
        accepted=self.request('POST',path,{'content':'你好','client_message_id':'msg1'})[2];self.service.queue.join()
        r=self.request('GET',f"/conversations/{cid}/messages/{accepted['user_message']['id']}?scope=demo")[2]
        self.assertEqual(r['status'],'failed');self.assertEqual(r['error']['code'],'model_timeout');self.assertIsNone(r['assistant_message']);self.assertEqual(r['record_actions'],[])
