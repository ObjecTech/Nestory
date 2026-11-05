"""The thirteen frozen Assistant routes, plus 「陪我玩」 interaction records, behind one authorization boundary."""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import parse_qs,urlparse

from .assistant_common import (AssistantError,date_window,scope,strict_json,uid)

ROUTES=[
 (re.compile(r'/conversations'),{'GET','POST'}),
 (re.compile(r'/conversations/([^/]+)'),{'PATCH','DELETE'}),
 (re.compile(r'/conversations/([^/]+)/messages'),{'GET','POST'}),
 (re.compile(r'/conversations/([^/]+)/messages/([^/]+)'),{'GET'}),
 (re.compile(r'/conversations/([^/]+)/messages/([^/]+)/record-actions/([^/]+)/confirm'),{'POST'}),
 (re.compile(r'/care-events'),{'GET','POST'}),
 (re.compile(r'/care-events/([^/]+)'),{'PATCH','DELETE'}),
 (re.compile(r'/sleep-summary'),{'GET'}),
]


INTERACTION=re.compile(r'/api/interactions(?:/([^/]+))?')
TIMELINE='/api/timeline/unified'
SUMMARY='/api/summary/daily'


def is_assistant(path):
    path=urlparse(path).path
    return path=='/api/assistant' or path.startswith('/api/assistant/') or path in (TIMELINE,SUMMARY) or bool(INTERACTION.fullmatch(path))


def _dated(query):
    """支持 date=YYYY-MM-DD 简写，等价于 from=to=date。"""
    if 'date' not in query:return query
    if 'from' in query or 'to' in query:raise AssistantError('validation_error')
    return dict(query,**{'from':query['date'],'to':query['date']})


def timeline_response(facts,query):
    """GET /api/timeline/unified?scope=demo&date=YYYY-MM-DD&timezone=Asia%2FShanghai"""
    a,b,tz,start,end=date_window(_dated(query));data_scope=scope(query.get('scope','demo'))
    return dict(api_version='1',scope=data_scope,timezone=tz,**{'from':a,'to':b},**facts.unified_timeline(data_scope,start,end,tz))


def handle_interactions(db,method,match,query,body):
    """POST/GET /api/interactions，DELETE /api/interactions/{id}?scope=。"""
    # 方法已在 handle() 中校验
    if method=='POST':return db.create_interaction(body)
    if method=='DELETE':
        if 'scope' not in query:raise AssistantError('scope_required',400)
        return db.delete_interaction(match.group(1),scope(query['scope'])),200
    a,b,tz,start,end=date_window(_dated(query));data_scope=scope(query.get('scope','demo'))
    return dict(api_version='1',scope=data_scope,timezone=tz,**{'from':a,'to':b},items=db.interactions(data_scope,start,end)),200


def handle(handler):
    request_id=uid();extra={};slot_acquired=False
    def reply(payload,status=200):
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        handler.send_response(status)
        handler.send_header('Content-Type','application/json; charset=utf-8')
        handler.send_header('Content-Length',str(len(raw)));handler.send_header('Cache-Control','no-store')
        for k,v in extra.items():handler.send_header(k,v)
        handler.end_headers()
        if handler.command!='HEAD':handler.wfile.write(raw)
    try:
        server=handler.server
        if server.tailscale_user and not handler._tailscale_owner():raise AssistantError('client_forbidden',403)
        subnet=server.lan_subnet if server.lan_subnet is not None else __import__('os').environ.get('BMON_PREVIEW_LAN_SUBNET')
        if subnet and not handler._lan_allows(handler.client_address[0]):raise AssistantError('client_forbidden',403)
        if handler.headers.get('Host')!=urlparse(server.auth.config.origin).netloc:raise AssistantError('host_mismatch',421)
        token=handler._session_token()
        if not server.auth.valid(token):raise AssistantError('authentication_required',401)
        if not server.access_store.read()['lan_enabled']:raise AssistantError('access_disabled',403)
        if not server.assistant_request_slots.acquire(blocking=False):raise AssistantError('rate_limited',429,retryable=True,retry_after=2)
        slot_acquired=True
        method=handler.command
        if method in ('POST','PATCH','DELETE'):
            if handler.headers.get('Origin')!=server.auth.config.origin:raise AssistantError('origin_forbidden',403)
            if not server.auth.csrf_valid(token,handler.headers.get('X-CSRF-Token','')):raise AssistantError('csrf_failed',403)
        service=getattr(handler.app,'assistant',None)
        if service is None:
            raise AssistantError(getattr(handler.app,'assistant_error',None) or 'assistant_disabled',503,retryable=True)
        parsed=urlparse(handler.path);path=parsed.path[len('/api/assistant'):]
        interaction=INTERACTION.fullmatch(parsed.path);timeline=parsed.path in (TIMELINE,SUMMARY)
        matched=None
        for pattern,methods in ([] if interaction or timeline else ROUTES):
            m=pattern.fullmatch(path)
            if m:
                if method not in methods:
                    extra['Allow']=', '.join(sorted(methods));raise AssistantError('method_not_allowed',405)
                matched=m;break
        if timeline:
            if method!='GET':
                extra['Allow']='GET';raise AssistantError('method_not_allowed',405)
        elif interaction:
            allowed={'GET','POST'} if interaction.group(1) is None else {'DELETE'}
            if method not in allowed:
                extra['Allow']=', '.join(sorted(allowed));raise AssistantError('method_not_allowed',405)
        elif not matched:raise AssistantError('route_not_found',404)
        params=parse_qs(parsed.query,keep_blank_values=True)
        if any(len(v)!=1 for v in params.values()):raise AssistantError('validation_error')
        query={k:v[0] for k,v in params.items()}
        groups=matched.groups() if matched else ();data_scope=None
        if groups:
            if 'scope' not in query:raise AssistantError('scope_required',400)
            data_scope=scope(query['scope'])
        body=None
        if method in ('POST','PATCH'):
            if handler.headers.get_content_type()!='application/json':raise AssistantError('unsupported_media_type',415)
            lengths=handler.headers.get_all('Content-Length') or []
            if handler.headers.get('Transfer-Encoding') or len(lengths)!=1:raise AssistantError('invalid_json',400)
            try:length=int(lengths[0])
            except ValueError:raise AssistantError('invalid_json',400) from None
            if length>32768:raise AssistantError('payload_too_large',413)
            if length<=0:raise AssistantError('invalid_json',400)
            body=strict_json(handler.rfile.read(length))
        elif method=='DELETE' and (handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Length','0')!='0'):
            raise AssistantError('validation_error')
        db=service.database;status=200
        if interaction:
            result,status=handle_interactions(db,method,interaction,query,body);reply(result,status);return
        if parsed.path==SUMMARY:
            # narrative=1 时同步请求模型润色（受调度器排队与超时约束），失败只影响润色字段
            narrative=query.pop('narrative','0')
            if narrative not in ('0','1'):raise AssistantError('validation_error')
            reply(service.summary.build(_dated(query),narrative=='1'));return
        if timeline:
            reply(timeline_response(service.facts,query));return
        if path=='/conversations':
            if method=='GET':result=db.conversations(query)
            else:
                result=db.create_conversation(body);status=201
                extra['Location']=f"/api/assistant/conversations/{result['conversation']['id']}?scope={result['conversation']['scope']}"
        elif path=='/care-events':
            if method=='POST':result,status=db.create_event(body)
            else:
                a,b,tz,start,end=date_window(query);data_scope=scope(query.get('scope','demo'))
                result=dict(api_version='1',scope=data_scope,timezone=tz,**{'from':a,'to':b},**db.events(data_scope,start,end,query))
        elif path=='/sleep-summary':result=service.facts.sleep_summary(query)
        elif path.startswith('/care-events/'):
            if method=='PATCH':result=db.update_event(groups[0],data_scope,body)
            else:
                try:version=int(query.get('expected_version',''))
                except ValueError:raise AssistantError('validation_error') from None
                result=db.delete_event(groups[0],data_scope,version)
        elif len(groups)==1 and path.endswith('/messages'):
            if method=='GET':result=db.messages(groups[0],data_scope,query)
            else:
                guard=lambda:server.auth.valid(token) and server.access_store.read()['lan_enabled']
                result,status=service.send(groups[0],data_scope,body,guard)
        elif len(groups)==3:result=db.confirm(*groups,data_scope,body)
        elif len(groups)==2:result=db.poll(*groups,data_scope)
        elif method=='PATCH':result=db.rename(groups[0],data_scope,body)
        else:result=db.delete_conversation(groups[0],data_scope)
        reply(result,status)
    except AssistantError as exc:
        if exc.retry_after is not None:extra['Retry-After']=str(exc.retry_after)
        reply(exc.envelope(request_id),exc.status)
    except sqlite3.Error:
        reply(AssistantError('storage_unavailable',503,retryable=True).envelope(request_id),503)
    except (ValueError,TypeError,UnicodeError):
        reply(AssistantError('validation_error').envelope(request_id),422)
    except Exception:
        reply(AssistantError('internal_error',500).envelope(request_id),500)

    finally:
        if slot_acquired:handler.server.assistant_request_slots.release()
