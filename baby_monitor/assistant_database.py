"""Transactional Assistant store, independent of the visual SQLite database."""
from __future__ import annotations

import json
import fcntl
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .assistant_common import (AssistantError, EDIT_FIELDS, canonical, cursor_decode,
    cursor_encode, digest, event_values, fields, instant, integer, key, now_ms,
    page_limit, scope, text, timestamp, uid, zone)


class AssistantDatabase:
    def __init__(self, path):
        destination=Path(path)
        destination.parent.mkdir(parents=True,exist_ok=True)
        self._lock_fd=os.open(str(destination)+'.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(self._lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd);self._lock_fd=None
            raise AssistantError('storage_unavailable',503,retryable=True) from None
        try:
            self._initialize(path)
        except BaseException:
            if hasattr(self,'connection'):self.connection.close()
            os.close(self._lock_fd);self._lock_fd=None
            raise

    def _initialize(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=3)
        self.connection.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('PRAGMA busy_timeout=3000')
        # executescript is used only for the initial transactional migration.
        exists = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assistant_schema_migrations'").fetchone()
        if not exists:
            sql = Path(__file__).with_name('assistant_schema.sql').read_text()
            try:
                self.connection.executescript('BEGIN IMMEDIATE;\n' + sql + f'\nINSERT INTO assistant_schema_migrations VALUES(1,{now_ms()});\nCOMMIT;')
            except BaseException:
                self.connection.rollback()
                self.connection.close()
                raise
        else:
            version = self.connection.execute('SELECT MAX(version) FROM assistant_schema_migrations').fetchone()[0]
            if version not in (1,2,3,4,5):
                self.connection.close()
                raise AssistantError('storage_unavailable', 503)
        with self.transaction():
            version=self.connection.execute('SELECT MAX(version) FROM assistant_schema_migrations').fetchone()[0]
            if version==1:
                self.connection.execute('ALTER TABLE chat_turns ADD COLUMN record_action_id TEXT')
                self.connection.execute('INSERT INTO assistant_schema_migrations VALUES(2,?)',(now_ms(),))
            if version<3:
                self.connection.execute('ALTER TABLE record_proposals ADD COLUMN origin_proposal_id TEXT REFERENCES record_proposals(id) ON DELETE CASCADE')
                self.connection.execute('INSERT INTO assistant_schema_migrations VALUES(3,?)',(now_ms(),))
            if version<4:
                for operation in ('INSERT','UPDATE'):
                    self.connection.execute(f"""CREATE TRIGGER assistant_proposal_origin_scope_{operation.lower()}
                        BEFORE {operation} ON record_proposals
                        WHEN NEW.origin_proposal_id IS NOT NULL AND NOT EXISTS (
                          SELECT 1 FROM record_proposals original JOIN chat_turns oldturn ON oldturn.id=original.turn_id
                          JOIN chat_turns newturn ON newturn.id=NEW.turn_id
                          WHERE original.id=NEW.origin_proposal_id AND original.data_scope=NEW.data_scope
                            AND oldturn.conversation_id=newturn.conversation_id
                        ) BEGIN SELECT RAISE(ABORT,'proposal origin scope mismatch'); END""")
                self.connection.execute('INSERT INTO assistant_schema_migrations VALUES(4,?)',(now_ms(),))
            if version<5:
                # 「陪我玩」互动记录：与聊天无关，只按范围和 client_id 幂等
                self.connection.execute("""CREATE TABLE interaction_events(
                    id TEXT PRIMARY KEY, data_scope TEXT NOT NULL CHECK(data_scope IN ('demo','real')),
                    client_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    activity_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('music','finger_play','game','sensory')),
                    title TEXT NOT NULL, timezone TEXT NOT NULL, started_at INTEGER NOT NULL,
                    ended_at INTEGER CHECK(ended_at IS NULL OR ended_at>=started_at),
                    reaction TEXT CHECK(reaction IS NULL OR reaction IN ('happy','calm','uninterested','fussy')),
                    notes TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
                    UNIQUE(data_scope,client_id))""")
                self.connection.execute('CREATE INDEX interaction_events_scope_time ON interaction_events(data_scope,started_at)')
                self.connection.execute('INSERT INTO assistant_schema_migrations VALUES(5,?)',(now_ms(),))
        with self.transaction():
            error = canonical(AssistantError('service_restarted', 503, retryable=True).object())
            self.connection.execute("UPDATE chat_messages SET status='failed' WHERE turn_id IN (SELECT id FROM chat_turns WHERE status IN ('queued','running'))", ())
            self.connection.execute("UPDATE chat_turns SET status='failed',error_json=?,updated_at=? WHERE status IN ('queued','running')", (error, now_ms()))

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.connection.execute('BEGIN IMMEDIATE')
                yield
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _updated_now(self):
        previous=self.connection.execute('SELECT MAX(updated_at) FROM chat_conversations').fetchone()[0] or 0
        return max(now_ms(),previous+1)

    def close(self):
        with self.lock:
            try:self.connection.close()
            finally:
                if self._lock_fd is not None:
                    os.close(self._lock_fd);self._lock_fd=None

    def _conversation(self, cid, data_scope):
        row = self.connection.execute('SELECT * FROM chat_conversations WHERE id=? AND data_scope=?', (cid, scope(data_scope))).fetchone()
        if not row:
            raise AssistantError('conversation_not_found', 404)
        return row

    def _conversation_json(self, row):
        latest = self.connection.execute('SELECT content FROM chat_messages WHERE conversation_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (row['id'],)).fetchone()
        return dict(id=row['id'], title=row['title'], title_source=row['title_source'], scope=row['data_scope'],
                    timezone=row['timezone'], created_at=instant(row['created_at']), updated_at=instant(row['updated_at']),
                    last_message_preview=latest['content'][:80] if latest else None, version=row['version'])

    def conversation(self, cid, data_scope):
        with self.lock:
            return self._conversation_json(self._conversation(cid, data_scope))

    def create_conversation(self, payload):
        fields(payload, ('scope', 'timezone'), ('scope',))
        data_scope = scope(payload['scope']); tz = payload.get('timezone', 'Asia/Shanghai'); zone(tz)
        cid, now = uid(), now_ms()
        with self.transaction():
            self.connection.execute('INSERT INTO chat_conversations VALUES(?,?,?,?,?,?,?,1)', (cid,'新对话','default',data_scope,tz,now,now))
            result = self._conversation_json(self._conversation(cid, data_scope))
        return dict(api_version='1', conversation=result)

    def conversations(self, query):
        data_scope = scope(query.get('scope', 'demo')); limit = page_limit(query)
        binding = ['conversations', data_scope]
        position = cursor_decode(query.get('cursor'), binding)
        with self.lock:
            args = [data_scope]; condition = ''
            if position:
                condition = ' AND (updated_at,id) < (?,?)'; args += position
            rows = self.connection.execute('SELECT * FROM chat_conversations WHERE data_scope=?'+condition+' ORDER BY updated_at DESC,id DESC LIMIT ?', args+[limit+1]).fetchall()
            more = len(rows)>limit; rows=rows[:limit]
            return dict(api_version='1', scope=data_scope, conversations=[self._conversation_json(r) for r in rows], has_more=more,
                        next_cursor=cursor_encode(binding,[rows[-1]['updated_at'],rows[-1]['id']]) if more else None)

    def rename(self, cid, data_scope, payload):
        fields(payload, ('title','expected_version'), ('title',))
        title = text(payload['title'],80,'title').strip()
        if any(ord(c)<32 for c in title): raise AssistantError('validation_error')
        with self.transaction():
            row = self._conversation(cid,data_scope)
            if 'expected_version' in payload:
                integer(payload['expected_version'],1,2**63-1,'expected_version')
                if row['version'] != payload['expected_version']: raise AssistantError('version_conflict',409)
            self.connection.execute("UPDATE chat_conversations SET title=?,title_source='manual',updated_at=?,version=version+1 WHERE id=?",(title,self._updated_now(),cid))
            result=self._conversation_json(self._conversation(cid,data_scope))
        return dict(api_version='1',conversation=result)

    def delete_conversation(self,cid,data_scope):
        with self.transaction():
            self._conversation(cid,data_scope)
            self.connection.execute('DELETE FROM chat_conversations WHERE id=?',(cid,))
        return dict(api_version='1',deleted=True,conversation_id=cid,care_events_preserved=True)

    def _message_json(self,row):
        client=None
        if row['role']=='user':
            client=self.connection.execute('SELECT client_message_id FROM chat_turns WHERE id=?',(row['turn_id'],)).fetchone()[0]
        return dict(id=row['id'],conversation_id=row['conversation_id'],role=row['role'],content=row['content'],
                    created_at=instant(row['created_at']),status=row['status'],client_message_id=client,turn_id=row['turn_id'])

    def messages(self,cid,data_scope,query):
        limit=page_limit(query);binding=['messages',cid,data_scope]
        position=cursor_decode(query.get('cursor'),binding)
        with self.lock:
            conv=self._conversation(cid,data_scope);args=[cid];condition=''
            if position:condition=' AND (created_at,id) < (?,?)';args+=position
            rows=self.connection.execute('SELECT * FROM chat_messages WHERE conversation_id=?'+condition+' ORDER BY created_at DESC,id DESC LIMIT ?',args+[limit+1]).fetchall()
            more=len(rows)>limit;rows=rows[:limit]
            pending=self.connection.execute("SELECT t.status,m.id FROM chat_turns t JOIN chat_messages m ON m.turn_id=t.id AND m.role='user' WHERE t.conversation_id=? AND t.status IN ('queued','running')",(cid,)).fetchone()
            p=dict(user_message_id=pending['id'],status=pending['status'],poll_url=self.poll_url(cid,pending['id'],data_scope),poll_after_ms=1500) if pending else None
            return dict(api_version='1',conversation=self._conversation_json(conv),messages=[self._message_json(r) for r in reversed(rows)],pending_turn=p,has_more=more,
                        next_cursor=cursor_encode(binding,[rows[-1]['created_at'],rows[-1]['id']]) if more else None)

    @staticmethod
    def poll_url(cid,mid,data_scope):
        return f'/api/assistant/conversations/{cid}/messages/{mid}?scope={data_scope}'

    def _turn(self,cid,mid,data_scope):
        self._conversation(cid,data_scope)
        row=self.connection.execute("SELECT t.* FROM chat_turns t JOIN chat_messages m ON m.turn_id=t.id WHERE m.id=? AND m.role='user' AND t.conversation_id=?",(mid,cid)).fetchone()
        if not row:raise AssistantError('message_not_found',404)
        return row

    def _event(self,eid,data_scope,deleted=False):
        row=self.connection.execute('SELECT * FROM care_events WHERE id=? AND data_scope=?',(eid,scope(data_scope))).fetchone()
        if not row or (row['status']=='deleted' and not deleted):raise AssistantError('event_not_found',404)
        return row

    def _event_json(self,row):
        result={k:row[k] for k in ('id','event_type','timezone','amount_ml','diaper_type','notes','source','status','source_message_id','source_reference_id','version')}
        result['scope']=row['data_scope']
        for k in ('occurred_at','end_at','created_at','updated_at','deleted_at'):result[k]=instant(row[k])
        result['duration_seconds']=(row['end_at']-row['occurred_at'])/1000 if row['end_at'] is not None else None
        return result

    def event(self,eid,data_scope,deleted=False):
        with self.lock:return self._event_json(self._event(eid,data_scope,deleted))

    def _actions(self,turn):
        actions=json.loads(turn['result_actions_json'] or '[]')
        for action in actions:
            proposal=self.connection.execute('SELECT * FROM record_proposals WHERE id=?',(action['action_id'],)).fetchone()
            if proposal:
                if proposal['origin_proposal_id']:
                    original=self.connection.execute('SELECT * FROM record_proposals WHERE id=?',(proposal['origin_proposal_id'],)).fetchone()
                    if original and original['status'] in ('confirmed','dismissed'):proposal=original
                if proposal['status']=='confirmed':
                    action.update(status='saved',reason_code='parent_confirmed',event=None,proposed_event=None,missing_fields=[],expires_at=None)
                    action['_event_id']=proposal['result_event_id']
                elif proposal['status'] in ('dismissed','expired') or proposal['expires_at']<now_ms():
                    action.update(status='not_saved',reason_code='parent_dismissed' if proposal['status']=='dismissed' else 'action_expired',event=None,proposed_event=None,missing_fields=[],expires_at=None)
            eid=action.pop('_event_id',None)
            if eid:
                row=self.connection.execute('SELECT * FROM care_events WHERE id=?',(eid,)).fetchone()
                action['event']=self._event_json(row) if row else None
        return actions

    def poll(self,cid,mid,data_scope,*,replayed=False):
        with self.lock:
            turn=self._turn(cid,mid,data_scope)
            rows=self.connection.execute('SELECT * FROM chat_messages WHERE turn_id=? ORDER BY role DESC',(turn['id'],)).fetchall()
            user=next(r for r in rows if r['role']=='user');assistant=next((r for r in rows if r['role']=='assistant'),None)
            return dict(api_version='1',status=turn['status'],user_message=self._message_json(user),
                assistant_message=self._message_json(assistant) if assistant else None,
                record_actions=self._actions(turn),conversation=self._conversation_json(self._conversation(cid,data_scope)),
                facts=json.loads(turn['facts_json']) if turn['facts_json'] else None,
                error=json.loads(turn['error_json']) if turn['error_json'] else None,replayed=replayed,attempt=turn['attempt'],
                poll_url=self.poll_url(cid,mid,data_scope),poll_after_ms=1500 if turn['status'] in ('queued','running') else None)

    def accept(self,cid,data_scope,payload):
        fields(payload,('content','client_message_id','timezone','retry','record_action_id'),('content','client_message_id'))
        content=text(payload['content'],4000,'content');client=key(payload['client_message_id'])
        retry=payload.get('retry',False)
        if type(retry) is not bool:raise AssistantError('validation_error')
        if 'record_action_id' in payload and not isinstance(payload['record_action_id'],str):raise AssistantError('validation_error')
        with self.transaction():
            conv=self._conversation(cid,data_scope)
            old=self.connection.execute('SELECT * FROM chat_turns WHERE conversation_id=? AND client_message_id=?',(cid,client)).fetchone()
            tz=payload.get('timezone',old['timezone'] if old else conv['timezone']);zone(tz)
            hash_value=digest(dict(content=content,timezone=tz,record_action_id=payload.get('record_action_id')))
            if old:
                mid=self.connection.execute("SELECT id FROM chat_messages WHERE turn_id=? AND role='user'",(old['id'],)).fetchone()[0]
                if old['request_hash']!=hash_value:raise AssistantError('idempotency_conflict',409)
                if old['status']!='failed' or not retry:
                    return mid,old['id'],False,old['status'] in ('queued','running')
                if not json.loads(old['error_json'] or '{}').get('retryable'):raise AssistantError('request_not_retryable',409)
            if self.connection.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",(cid,)).fetchone():raise AssistantError('conversation_busy',409)
            if self.connection.execute("SELECT COUNT(*) FROM chat_turns WHERE status IN ('queued','running')").fetchone()[0]>=4:raise AssistantError('queue_full',429,retryable=True,retry_after=2)
            if payload.get('record_action_id'):
                proposal=self.connection.execute('SELECT p.* FROM record_proposals p JOIN chat_turns t ON t.id=p.turn_id WHERE p.id=? AND t.conversation_id=? AND p.data_scope=?',(payload['record_action_id'],cid,data_scope)).fetchone()
                if not proposal:raise AssistantError('action_not_found',404)
            now=self._updated_now()
            if old:
                tid=old['id']
                self.connection.execute("UPDATE chat_turns SET status='queued',attempt=attempt+1,error_json=NULL,updated_at=? WHERE id=?",(now,tid))
                self.connection.execute("UPDATE chat_messages SET status='queued' WHERE id=?",(mid,))
            else:
                tid,mid=uid(),uid()
                self.connection.execute("INSERT INTO chat_turns(id,conversation_id,client_message_id,request_hash,timezone,reference_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)",(tid,cid,client,hash_value,tz,now,now,now))
                self.connection.execute('UPDATE chat_turns SET record_action_id=? WHERE id=?',(payload.get('record_action_id'),tid))
                self.connection.execute("INSERT INTO chat_messages VALUES(?,?,?,'user',?,'queued',?)",(mid,cid,tid,content,now))
                if conv['title_source']=='default':
                    title=''.join(c for c in content if ord(c)>=32).strip()[:24]
                    self.connection.execute("UPDATE chat_conversations SET title=?,title_source='auto' WHERE id=?",(title,cid))
            self.connection.execute('UPDATE chat_conversations SET updated_at=?,version=version+1 WHERE id=?',(now,cid))
            return mid,tid,True,True

    def claim(self,tid):
        with self.transaction():
            row=self.connection.execute("SELECT t.*,c.data_scope FROM chat_turns t JOIN chat_conversations c ON c.id=t.conversation_id WHERE t.id=? AND t.status='queued'",(tid,)).fetchone()
            if not row:return None
            self.connection.execute("UPDATE chat_turns SET status='running',updated_at=? WHERE id=?",(now_ms(),tid))
            self.connection.execute("UPDATE chat_messages SET status='running' WHERE turn_id=?",(tid,))
            result=dict(row)
            messages=self.connection.execute("SELECT m.* FROM chat_messages m JOIN chat_turns t ON t.id=m.turn_id WHERE m.conversation_id=? AND t.status='completed' ORDER BY m.created_at DESC,m.id DESC LIMIT 12",(row['conversation_id'],)).fetchall()
            result['messages']=[dict(role=m['role'],content=m['content']) for m in reversed(messages)]
            current=self.connection.execute("SELECT * FROM chat_messages WHERE turn_id=? AND role='user'",(tid,)).fetchone()
            result['messages'].append(dict(role='user',content=current['content']))
            result['user_message_id']=current['id'];result['content']=current['content']
            return result

    def fail(self,tid,error):
        with self.transaction():
            self.connection.execute("UPDATE chat_turns SET status='failed',error_json=?,updated_at=? WHERE id=? AND status IN ('queued','running')",(canonical(error.object()),now_ms(),tid))
            self.connection.execute("UPDATE chat_messages SET status='failed' WHERE turn_id=? AND EXISTS(SELECT 1 FROM chat_turns t WHERE t.id=? AND t.status='failed')",(tid,tid))

    def _audit(self,row,operation,before,source):
        after=self._event_json(row);after.pop('source_message_id',None)
        if before:before.pop('source_message_id',None)
        self.connection.execute('INSERT INTO care_event_revisions VALUES(?,?,?,?,?,?,?,?)',(uid(),row['id'],row['version'],operation,canonical(before) if before else None,canonical(after),now_ms(),source))

    def _create_event(self,values,data_scope,source,source_message_id,extraction_key,change_source):
        eid,now=uid(),now_ms()
        self.connection.execute('INSERT INTO care_events(id,event_type,data_scope,timezone,occurred_at,end_at,amount_ml,diaper_type,notes,source,source_message_id,source_reference_id,extraction_key,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,\'confirmed\',1,?,?)',
            (eid,values['event_type'],data_scope,values['timezone'],values['occurred_at'],values['end_at'],values['amount_ml'],values['diaper_type'],values['notes'],source,source_message_id,uid(),extraction_key,now,now))
        row=self._event(eid,data_scope);self._audit(row,'create',None,change_source);return row

    def create_event(self,payload):
        fields(payload,{'scope','client_event_id'}|set(('event_type','timezone','occurred_at','end_at','amount_ml','diaper_type','notes')),('scope','client_event_id'))
        data_scope=scope(payload['scope']);client=key(payload['client_event_id']);request_hash=digest(payload)
        with self.transaction():
            old=self.connection.execute('SELECT * FROM assistant_write_requests WHERE data_scope=? AND client_event_id=?',(data_scope,client)).fetchone()
            if old:
                if old['request_hash']!=request_hash:raise AssistantError('idempotency_conflict',409)
                return dict(api_version='1',event=self._event_json(self._event(old['event_id'],data_scope,True)),replayed=True),200
            values=event_values({k:v for k,v in payload.items() if k not in ('scope','client_event_id')})
            row=self._create_event(values,data_scope,'parent_manual',None,f'manual:{data_scope}:{client}','parent_form')
            self.connection.execute('INSERT INTO assistant_write_requests VALUES(?,?,?,?)',(data_scope,client,request_hash,row['id']))
            result=self._event_json(row)
        return dict(api_version='1',event=result,replayed=False),201

    @staticmethod
    def _interaction_json(row):
        ended=row['ended_at']
        return dict(id=row['id'],scope=row['data_scope'],client_id=row['client_id'],activity_id=row['activity_id'],
                    kind=row['kind'],title=row['title'],timezone=row['timezone'],started_at=instant(row['started_at']),
                    ended_at=instant(ended),duration_seconds=None if ended is None else (ended-row['started_at'])//1000,
                    reaction=row['reaction'],notes=row['notes'],created_at=instant(row['created_at']))

    def create_interaction(self,payload):
        fields(payload,('client_id','scope','activity_id','kind','title','timezone','started_at','ended_at','reaction','notes'),
               ('client_id','scope','activity_id','kind','title','started_at'))
        data_scope=scope(payload['scope']);client=key(payload['client_id']);request_hash=digest(payload)
        text(payload['activity_id'],64,'activity_id');text(payload['title'],100,'title')
        notes=text(payload.get('notes',''),200,'notes',empty=True)
        tz=payload.get('timezone','Asia/Shanghai');zone(tz)
        if payload['kind'] not in ('music','finger_play','game','sensory'):raise AssistantError('validation_error',details=[dict(field='kind',reason='invalid_enum')])
        if payload.get('reaction') not in (None,'happy','calm','uninterested','fussy'):raise AssistantError('validation_error',details=[dict(field='reaction',reason='invalid_enum')])
        started=timestamp(payload['started_at'],'started_at')
        ended=None if payload.get('ended_at') is None else timestamp(payload['ended_at'],'ended_at')
        # 允许 1 分钟时钟误差；单次互动不超过 6 小时
        if started>now_ms()+60000 or (ended is not None and not started<=ended<=min(started+6*3600000,now_ms()+60000)):
            raise AssistantError('validation_error',details=[dict(field='ended_at' if ended else 'started_at',reason='invalid_time_range')])
        with self.transaction():
            old=self.connection.execute('SELECT * FROM interaction_events WHERE data_scope=? AND client_id=?',(data_scope,client)).fetchone()
            if old:
                if old['request_hash']!=request_hash:raise AssistantError('idempotency_conflict',409)
                return dict(api_version='1',interaction=self._interaction_json(old),replayed=True),200
            iid=uid()
            self.connection.execute('INSERT INTO interaction_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (iid,data_scope,client,request_hash,payload['activity_id'],payload['kind'],payload['title'],tz,started,ended,payload.get('reaction'),notes,now_ms()))
            row=self.connection.execute('SELECT * FROM interaction_events WHERE id=?',(iid,)).fetchone()
        return dict(api_version='1',interaction=self._interaction_json(row),replayed=False),201

    def interactions(self,data_scope,start,end):
        with self.lock:
            rows=self.connection.execute('SELECT * FROM interaction_events WHERE data_scope=? AND started_at>=? AND started_at<? ORDER BY started_at DESC,id LIMIT 200',
                                         (scope(data_scope),start,end)).fetchall()
        return [self._interaction_json(r) for r in rows]

    def delete_interaction(self,iid,data_scope):
        with self.transaction():
            if not self.connection.execute('DELETE FROM interaction_events WHERE id=? AND data_scope=?',(iid,scope(data_scope))).rowcount:
                raise AssistantError('interaction_not_found',404)
        return dict(api_version='1',deleted=True)

    def _update_event(self,eid,data_scope,payload,change_source):
        fields(payload,EDIT_FIELDS|{'expected_version'},('expected_version',))
        version=integer(payload['expected_version'],1,2**63-1,'expected_version');row=self._event(eid,data_scope)
        if row['version']!=version:raise AssistantError('version_conflict',409)
        before=self._event_json(row);merged={k:before[k] for k in ('event_type','timezone','occurred_at','end_at','amount_ml','diaper_type','notes')}
        merged.update({k:v for k,v in payload.items() if k!='expected_version'});values=event_values(merged)
        self.connection.execute('UPDATE care_events SET timezone=?,occurred_at=?,end_at=?,amount_ml=?,diaper_type=?,notes=?,updated_at=?,version=version+1 WHERE id=?',(values['timezone'],values['occurred_at'],values['end_at'],values['amount_ml'],values['diaper_type'],values['notes'],now_ms(),eid))
        row=self._event(eid,data_scope);self._audit(row,'update',before,change_source);return row

    def update_event(self,eid,data_scope,payload):
        with self.transaction():result=self._event_json(self._update_event(eid,data_scope,payload,'parent_form'))
        return dict(api_version='1',event=result)

    def _delete_event(self,eid,data_scope,version,source):
        integer(version,1,2**63-1,'expected_version');row=self._event(eid,data_scope,True)
        if row['status']=='deleted':
            if version not in (row['version'],row['version']-1):raise AssistantError('version_conflict',409)
            return row
        if row['version']!=version:raise AssistantError('version_conflict',409)
        before=self._event_json(row);now=now_ms()
        self.connection.execute("UPDATE care_events SET status='deleted',deleted_at=?,updated_at=?,version=version+1 WHERE id=?",(now,now,eid))
        row=self._event(eid,data_scope,True);self._audit(row,'delete',before,source);return row

    def delete_event(self,eid,data_scope,version):
        with self.transaction():row=self._delete_event(eid,data_scope,version,'parent_form');result=dict(api_version='1',deleted=True,event_id=eid,version=row['version'],deleted_at=instant(row['deleted_at']))
        return result

    def events(self,data_scope,start,end,query):
        limit=page_limit(query);kind=query.get('event_type')
        if kind and kind not in ('feeding','diaper','sleep','activity'):raise AssistantError('validation_error')
        binding=['events',data_scope,start,end,kind,query.get('timezone','Asia/Shanghai')];position=cursor_decode(query.get('cursor'),binding)
        with self.lock:
            sql="SELECT * FROM care_events WHERE data_scope=? AND status='confirmed' AND occurred_at<? AND ((end_at IS NULL AND occurred_at>=?) OR end_at>?)";args=[scope(data_scope),end,start,start]
            if kind:sql+=' AND event_type=?';args.append(kind)
            if position:sql+=' AND (occurred_at,id)<(?,?)';args+=position
            rows=self.connection.execute(sql+' ORDER BY occurred_at DESC,id DESC LIMIT ?',args+[limit+1]).fetchall()
            more=len(rows)>limit;rows=rows[:limit]
            return dict(events=[self._event_json(r) for r in rows],next_cursor=cursor_encode(binding,[rows[-1]['occurred_at'],rows[-1]['id']]) if more else None,has_more=more)

    def latest_event_for_message(self,cid,data_scope,exclude_tid):
        with self.lock:
            # Only the immediately preceding completed turn, never a global nearest-event guess.
            row=self.connection.execute("SELECT id FROM chat_turns WHERE conversation_id=? AND status='completed' AND id!=? ORDER BY created_at DESC,id DESC LIMIT 1",(cid,exclude_tid)).fetchone()
            if not row:return None
            events=self.connection.execute("SELECT e.* FROM care_events e JOIN chat_messages m ON m.id=e.source_message_id WHERE m.turn_id=? AND e.data_scope=? AND e.status='confirmed'",(row['id'],data_scope)).fetchall()
            return self._event_json(events[0]) if len(events)==1 else None

    def linked_proposal(self,turn):
        aid=turn.get('record_action_id')
        if not aid:return None
        with self.lock:
            row=self.connection.execute('SELECT p.* FROM record_proposals p JOIN chat_turns t ON t.id=p.turn_id WHERE p.id=? AND t.conversation_id=? AND p.data_scope=?',(aid,turn['conversation_id'],turn['data_scope'])).fetchone()
            if not row:raise AssistantError('action_not_found',404)
            if row['status']!='pending' or row['expires_at']<now_ms():raise AssistantError('action_expired',409)
            return dict(row)

    def finish(self,turn,answer,actions,facts=None,authorized=lambda:True):
        with self.transaction():
            row=self.connection.execute("SELECT * FROM chat_turns WHERE id=? AND status='running'",(turn['id'],)).fetchone()
            if not row:return False
            if not authorized():raise AssistantError('access_disabled',403)
            now=max(self._updated_now(),row['created_at']+1)
            saved=[]
            for ordinal,action in enumerate(actions):
                card=dict(action);card['action_id']=uid()
                origin_id=card.pop('_origin_proposal_id',None)
                original=self.connection.execute('SELECT * FROM record_proposals WHERE id=?',(origin_id,)).fetchone() if origin_id else None
                if origin_id and (not original or original['status']!='pending' or original['expires_at']<now_ms()):raise AssistantError('action_expired',409)
                if card['status']=='saved':
                    values=event_values(card.pop('_values'))
                    if original:
                        source_mid=self.connection.execute("SELECT id FROM chat_messages WHERE turn_id=? AND role='user'",(original['turn_id'],)).fetchone()[0]
                        if original['operation']=='create':
                            event=self._create_event(values,turn['data_scope'],'parent_chat',source_mid,f"turn:{original['turn_id']}:{original['ordinal']}",'parent_confirmation')
                        elif original['operation']=='update':
                            update={k:v for k,v in self._event_json(self._event(original['target_event_id'],turn['data_scope'])).items() if k in EDIT_FIELDS}
                            update.update({k:instant(v) if k in ('occurred_at','end_at') else v for k,v in values.items() if k in EDIT_FIELDS})
                            event=self._update_event(original['target_event_id'],turn['data_scope'],dict(update,expected_version=original['expected_version']),'parent_confirmation')
                        else:raise AssistantError('validation_error')
                        self.connection.execute("UPDATE record_proposals SET status='confirmed',resolution_hash=?,result_event_id=? WHERE id=?",(digest({'clarification_turn':turn['id']}),event['id'],origin_id))
                    else:
                        event=self._create_event(values,turn['data_scope'],'parent_chat',turn['user_message_id'],f"turn:{turn['id']}:{ordinal}",'auto_chat')
                    card['_event_id']=event['id']
                    saved.append('已记录'+{'feeding':f"喂奶 {event['amount_ml']} ml",'diaper':'换尿布','sleep':'睡眠时段','activity':'日常活动'}[event['event_type']])
                elif card['status']=='needs_confirmation':
                    values=card['proposed_event'];values={k:v for k,v in values.items() if k!='scope'}
                    self.connection.execute("INSERT INTO record_proposals(id,turn_id,ordinal,data_scope,operation,target_event_id,expected_version,proposed_fields_json,missing_fields_json,reason_code,status,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)",(card['action_id'],turn['id'],ordinal,turn['data_scope'],card['operation'],card['target_event_id'],card['expected_version'],canonical(values),canonical(card['missing_fields']),card['reason_code'],now+86400000))
                    if original:
                        self.connection.execute('UPDATE record_proposals SET origin_proposal_id=?,expires_at=? WHERE id=?',(origin_id,original['expires_at'],card['action_id']))
                    card['expires_at']=instant(original['expires_at'] if original else now+86400000)
                card.pop('_values',None)
                actions[ordinal]=card
            final=answer
            if saved:final='；'.join(saved)+'。'+(' '+answer if answer else '')
            self.connection.execute("INSERT INTO chat_messages VALUES(?,?,?,'assistant',?,'completed',?)",(uid(),turn['conversation_id'],turn['id'],final,now))
            self.connection.execute("UPDATE chat_messages SET status='completed' WHERE turn_id=?",(turn['id'],))
            self.connection.execute("UPDATE chat_turns SET status='completed',result_actions_json=?,facts_json=?,error_json=NULL,updated_at=? WHERE id=?",(canonical(actions),canonical(facts) if facts is not None else None,now,turn['id']))
            self.connection.execute('UPDATE chat_conversations SET updated_at=?,version=version+1 WHERE id=?',(now,turn['conversation_id']))
        return True

    def confirm(self,cid,mid,aid,data_scope,payload):
        fields(payload,('decision','fields'),('decision',))
        decision=payload['decision']
        if decision not in ('confirm','dismiss'):raise AssistantError('validation_error')
        edits=payload.get('fields',{});fields(edits,EDIT_FIELDS)
        request_hash=digest(payload)
        with self.transaction():
            turn=self._turn(cid,mid,data_scope)
            p=self.connection.execute('SELECT * FROM record_proposals WHERE id=? AND turn_id=? AND data_scope=?',(aid,turn['id'],data_scope)).fetchone()
            if not p:raise AssistantError('action_not_found',404)
            alias=p
            if p['origin_proposal_id']:
                p=self.connection.execute('SELECT * FROM record_proposals WHERE id=?',(p['origin_proposal_id'],)).fetchone()
                if not p:raise AssistantError('action_not_found',404)
            if p['status'] in ('confirmed','dismissed'):
                if p['resolution_hash']!=request_hash:raise AssistantError('idempotency_conflict',409)
            else:
                if p['expires_at']<now_ms():raise AssistantError('action_expired',409)
                eid=None
                if decision=='confirm':
                    proposed=json.loads(alias['proposed_fields_json']);proposed.update(edits)
                    if p['operation']=='create':
                        source_mid=self.connection.execute("SELECT id FROM chat_messages WHERE turn_id=? AND role='user'",(p['turn_id'],)).fetchone()[0]
                        row=self._create_event(event_values(proposed),data_scope,'parent_chat',source_mid,f"turn:{p['turn_id']}:{p['ordinal']}",'parent_confirmation')
                    elif p['operation']=='update':
                        if not p['target_event_id']:raise AssistantError('validation_error')
                        update={k:v for k,v in proposed.items() if k in EDIT_FIELDS};update['expected_version']=p['expected_version']
                        row=self._update_event(p['target_event_id'],data_scope,update,'parent_confirmation')
                    else:
                        if not p['target_event_id'] or edits:raise AssistantError('validation_error')
                        row=self._delete_event(p['target_event_id'],data_scope,p['expected_version'],'parent_confirmation')
                    eid=row['id']
                self.connection.execute('UPDATE record_proposals SET status=?,resolution_hash=?,result_event_id=? WHERE id=?',('confirmed' if decision=='confirm' else 'dismissed',request_hash,eid,p['id']))
            card=next(a for a in self._actions(turn) if a['action_id']==aid)
        return dict(api_version='1',resolution='confirmed' if decision=='confirm' else 'dismissed',record_action=card)
