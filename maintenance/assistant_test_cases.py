"""Private real-HTTP case recorder. Never claims model semantics passed by text alone."""
import datetime as dt
import http.client
import json
import sqlite3
import time
import urllib.parse
import uuid

from maintenance.assistant_test_harness import CASES


class TestClient:
    def __init__(self,port,password):
        self.port=port; self.origin=f'http://127.0.0.1:{port}'; self.cookie=''; self.csrf=''
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        body=urllib.parse.urlencode({'username':'assistant-test','password':password})
        connection.request('POST','/login',body,{'Origin':self.origin,'Content-Type':'application/x-www-form-urlencoded'})
        response=connection.getresponse(); response.read()
        if response.status!=303: raise RuntimeError('Test login failed')
        cookies=[value.split(';')[0] for key,value in response.getheaders() if key.lower()=='set-cookie']
        self.cookie='; '.join(cookies)
        self.csrf=next(value.partition('=')[2] for value in cookies if value.startswith('bmon_csrf='))
        connection.close()

    def request(self,method,path,body=None,csrf=True,authenticated=True):
        if not path.startswith('/api/assistant/') or '://' in path:
            raise ValueError('Only same-origin Assistant routes')
        headers={'Content-Type':'application/json','Origin':self.origin}
        if authenticated: headers['Cookie']=self.cookie
        if csrf: headers['X-CSRF-Token']=self.csrf
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request(method,path,None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers)
        response=connection.getresponse(); raw=response.read(); status=response.status; connection.close()
        return status,json.loads(raw)

    def turn(self,cid,text,client_id=None):
        path=f'/api/assistant/conversations/{cid}/messages?scope=demo'
        start=time.monotonic()
        status,value=self.request('POST',path,{'content':text,'client_message_id':client_id or uuid.uuid4().hex,'timezone':'Asia/Shanghai'})
        accepted=time.monotonic()-start
        if status not in (200,202): return dict(http=status,response=value,accept_seconds=accepted)
        deadline=time.monotonic()+110
        polls=0
        while value.get('status') not in ('completed','failed') and time.monotonic()<deadline:
            time.sleep(.1); status,value=self.request('GET',value['poll_url']); polls+=1
            if status!=200: break
        return dict(http=status,response=value,accept_seconds=accepted,total_seconds=time.monotonic()-start,polls=polls)


def snapshot(harness):
    # Query ONLY the harness-owned SQLite, not production or an arbitrary filename.
    db=harness.app.assistant.database
    with db.lock:
        return [dict(row) for row in db.connection.execute(
            'SELECT id,data_scope,event_type,status,occurred_at,end_at,amount_ml,version,source_message_id FROM care_events ORDER BY id')]


def run_cases(harness,password):
    """Record every success/failure plus actual SQL; reviewer applies case oracle.

    Run in one fresh demo archive; feeding query uses cross-conversation facts.
    Confirmation/edit/delete/13-route acceptance use the UI and regression suite;
    this recorder does not manufacture a successful extraction via manual writes.
    """
    if harness.gate.phase!='functional': raise RuntimeError('First open a successful functional fixture phase')
    client=TestClient(harness.web.server_port,password)
    feeding_cid=None
    for case_id,text,oracle in CASES:
        if harness.gate.phase!='functional':
            harness.journal.emit('case_group_interrupted',reason='admission_closed')
            break
        if case_id=='correction' and feeding_cid:
            cid=feeding_cid
        else:
            status,created=client.request('POST','/api/assistant/conversations',{'scope':'demo','timezone':'Asia/Shanghai'})
            if status!=201: raise RuntimeError('Test conversation creation failed')
            cid=created['conversation']['id']
            if case_id=='feeding_complete': feeding_cid=cid
        before=snapshot(harness)
        result=client.turn(cid,text)
        after=snapshot(harness)
        harness.journal.emit('case_observation',case=case_id,input=text,oracle=oracle,
            http=result,before=before,after=after,
            semantic_verdict='NOT_AUTO_PASSED; review raw JSON, actions, transaction and case oracle')
        if result['response'].get('status') in ('queued','running'):
            harness.gate.stopping()
            harness.module.ENGINE.scheduler.stop_accepting()
            harness.journal.emit('case_group_interrupted',reason='unsettled_turn; drain before restoration')
            break
    harness.journal.emit('case_group_complete',actual_db=snapshot(harness),
        warning='No claim of real model success, concurrency, or infant accuracy without reviewing all cases')
