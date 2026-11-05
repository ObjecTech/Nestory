"""Assistant contract primitives. Never includes private values in failures."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AssistantError(Exception):
    def __init__(self, code, status=422, message=None, *, retryable=False, details=None, retry_after=None):
        super().__init__(code)
        self.code, self.status = code, status
        self.message = message or {
            'validation_error': '请求字段无效，请检查后重试。',
            'model_unavailable': '本地模型暂不可用，请稍后重试。',
            'model_failed': '本地模型推理失败，请稍后重试。',
            'model_timeout': '本地模型处理超时，请稍后重试。',
            'vision_capacity_reserved': '当前算力优先用于视觉监测，请稍后重试。',
            'storage_unavailable': '本地存储暂不可用。',
            'service_restarted': '服务已重启，请手动重试此消息。',
        }.get(code, '操作未完成，请刷新数据或检查请求。')
        self.retryable, self.details, self.retry_after = retryable, details or [], retry_after

    def object(self):
        return dict(code=self.code, message=self.message, retryable=self.retryable,
                    details=self.details, retry_after_seconds=self.retry_after)

    def envelope(self, request_id):
        return dict(api_version='1', error=self.object(), request_id=request_id)


def uid():
    return str(uuid.uuid4())


def now_ms():
    return time.time_ns() // 1_000_000


def instant(value):
    return None if value is None else datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def timestamp(value, field='occurred_at'):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        return int(dt.timestamp() * 1000)
    except (ValueError, OverflowError, TypeError):
        raise AssistantError('validation_error', details=[dict(field=field, reason='invalid_timestamp')]) from None


def zone(value):
    try:
        if not isinstance(value, str) or len(value) > 80:
            raise ValueError()
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise AssistantError('invalid_timezone') from None


def scope(value):
    if value not in ('demo', 'real'):
        raise AssistantError('invalid_scope')
    return value


def integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise AssistantError('validation_error', details=[dict(field=field, reason='out_of_range')])
    return value


def text(value, maximum, field, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise AssistantError('validation_error', details=[dict(field=field, reason='invalid_text')])
    return value


def key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
        raise AssistantError('validation_error', details=[dict(field='client_id', reason='invalid_id')])
    return value


def fields(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or set(required) - set(payload):
        raise AssistantError('validation_error')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate')
            result[k] = v
        return result
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, TypeError):
        raise AssistantError('invalid_json', 400) from None


def page_limit(query):
    try:
        value = int(query.get('limit', '50'))
    except (ValueError, TypeError):
        raise AssistantError('validation_error') from None
    return integer(value, 1, 100, 'limit')


def cursor_encode(binding, position):
    return base64.urlsafe_b64encode(canonical(dict(binding=binding, position=position)).encode()).decode().rstrip('=')


def cursor_decode(value, binding):
    if not value:
        return None
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError()
        data = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        pos = data['position']
        if data['binding'] != binding or not isinstance(pos, list) or len(pos) != 2 or type(pos[0]) is not int or not isinstance(pos[1], str):
            raise ValueError()
        return pos
    except (ValueError, KeyError, TypeError):
        raise AssistantError('invalid_cursor', 400) from None


def date_window(query):
    tz = query.get('timezone', 'Asia/Shanghai')
    z = zone(tz)
    a, b = query.get('from'), query.get('to')
    if bool(a) != bool(b):
        raise AssistantError('validation_error')
    if not a:
        a = b = datetime.now(z).date().isoformat()
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', a) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', b):
            raise ValueError()
        first, last = datetime.fromisoformat(a).date(), datetime.fromisoformat(b).date()
        if not 0 <= (last - first).days < 31:
            raise ValueError()
        start = int(datetime.combine(first, datetime.min.time(), z).timestamp() * 1000)
        end = int(datetime.combine(last + timedelta(days=1), datetime.min.time(), z).timestamp() * 1000)
    except (ValueError, OverflowError, TypeError):
        raise AssistantError('validation_error') from None
    return a, b, tz, start, end


def sleep_window(query):
    """A night belongs to its starting date: local 20:00 through next-day 10:00."""
    a, b, tz, start, end = date_window(query)
    period = query.get('period', 'day')
    if period not in ('day', 'night') or (period == 'night' and a != b):
        raise AssistantError('validation_error')
    if period == 'night':
        first = datetime.fromisoformat(a).date()
        z = zone(tz)
        start = int(datetime.combine(first, datetime.min.time(), z).replace(hour=20).timestamp() * 1000)
        end = int(datetime.combine(first + timedelta(days=1), datetime.min.time(), z).replace(hour=10).timestamp() * 1000)
    return a, b, tz, start, end


EVENT_FIELDS = {'event_type', 'timezone', 'occurred_at', 'end_at', 'amount_ml', 'diaper_type', 'notes'}
EDIT_FIELDS = EVENT_FIELDS - {'event_type'}


def event_values(payload, *, now=None):
    """Validate complete canonical care fields; nulls are explicit, never guessed."""
    fields(payload, EVENT_FIELDS, ('event_type', 'occurred_at', 'timezone'))
    kind = payload['event_type']
    if kind not in ('feeding', 'diaper', 'sleep', 'activity'):
        raise AssistantError('validation_error')
    zone(payload['timezone'])
    start = timestamp(payload['occurred_at'])
    end = timestamp(payload['end_at'], 'end_at') if payload.get('end_at') is not None else None
    current = now_ms() if now is None else now
    if start > current + 60000 or (end is not None and (end <= start or end > current + 60000)):
        raise AssistantError('validation_error', details=[dict(field='occurred_at', reason='future_or_invalid_range')])
    amount, diaper = payload.get('amount_ml'), payload.get('diaper_type')
    notes = text(payload.get('notes', ''), 2000, 'notes', empty=True)
    if kind == 'feeding':
        integer(amount, 1, 2000, 'amount_ml')
        if end is not None or diaper is not None:
            raise AssistantError('validation_error')
    elif kind == 'diaper':
        if diaper not in ('wet', 'dirty', 'mixed', 'unknown') or amount is not None or end is not None:
            raise AssistantError('validation_error')
    else:
        if amount is not None or diaper is not None:
            raise AssistantError('validation_error')
        if kind == 'sleep' and (end is None or end - start > 86400000):
            raise AssistantError('validation_error')
        if kind == 'activity' and not notes.strip():
            raise AssistantError('validation_error')
    return dict(event_type=kind, timezone=payload['timezone'], occurred_at=start, end_at=end,
                amount_ml=amount, diaper_type=diaper, notes=notes)
