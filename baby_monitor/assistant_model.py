"""Loopback-only Qwen chat transport. No private prompts are logged or uploaded."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .assistant_common import AssistantError, canonical, fields, strict_json


class AssistantModel:
    def __init__(self,endpoint,timeout=45):
        u=urlsplit(endpoint)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError('Assistant model requires exact loopback HTTP origin')
        self.endpoint,self.timeout=endpoint,timeout
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def chat(self,payload):
        payload=dict(payload,messages=list(payload['messages']))
        body=canonical(payload).encode()
        while len(body)>32768 and len(payload['messages'])>1:
            payload['messages']=payload['messages'][2:]
            body=canonical(payload).encode()
        if len(body)>32768:raise AssistantError('validation_error')
        request=urllib.request.Request(self.endpoint+'/chat',data=body,method='POST',headers={'Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(65537)
                if len(raw)>65536:raise AssistantError('invalid_model_output',502)
                value=strict_json(raw)
            if not isinstance(value.get('result'),dict):raise AssistantError('invalid_model_output',502)
            return value['result']
        except urllib.error.HTTPError as exc:
            status=exc.code
            try:
                raw=exc.read(32769);data=json.loads(raw) if len(raw)<=32768 else {}
                code=data.get('error',{}).get('code') if isinstance(data.get('error'),dict) else None
            except (ValueError,AttributeError):code=None
            allowed={'queue_full','vision_capacity_reserved','model_unavailable','model_timeout','invalid_model_output','validation_error','model_failed'}
            code=code if code in allowed else ('model_unavailable' if status==503 else 'model_failed')
            raise AssistantError(code,status if status in (429,503,504,502,422) else 502,retryable=code!='validation_error',retry_after=5) from None
        except (socket.timeout,TimeoutError):raise AssistantError('model_timeout',504,retryable=True,retry_after=5) from None
        except urllib.error.URLError:raise AssistantError('model_unavailable',503,retryable=True,retry_after=5) from None
        except AssistantError as exc:
            if exc.code=='invalid_json':raise AssistantError('invalid_model_output',502) from None
            raise
