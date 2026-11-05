"""规则优先意图识别：常见记录/查询句式不经过模型，输出与模型相同结构的提案。

返回值仍交给 plan_actions 做时间、数量、否定和未来计划校验；规则只决定
“这是哪类事件/查询”，不直接写库。识别不了时返回 None，由模型负责聊天。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .assistant_common import zone

# 事件类型关键词；activity 与 plan_actions 中允许的活动词保持一致
EVENT_WORDS = {
    'feeding': r'喝了|喝完|吃了.{0,4}奶|喝.{0,6}奶|喂奶|喂了|吃奶|母乳|奶粉|亲喂|瓶喂',
    'sleep': r'睡|补觉',
    'diaper': r'尿布|纸尿裤|尿不湿',
    'activity': r'散步|玩耍|玩了|洗澡',
}
# 查询类问法：需要同时出现统计/确认类词语，避免把“睡不好怎么办”当成查询
QUERY_WORDS = (r'查询|查看|查一下|看看|看一下|多少|几次|几回|多久|多长|几个?小时|总共|一共|合计|统计|汇总|有哪些|哪些记录|了吗|了没|有没有|记录了?什么'
               # 问过去发生的时间（“今天什么时候哭了”“几点喝的奶”）；“什么时候睡比较好”仍交给模型
               r'|(?:什么时候|啥时候|几点|何时)[^，,。？?]{0,8}(?:了|的|过)')
QUESTION = r'[?？]|吗|怎么|怎样|为什么|如何|是否|要不要|该不该|能不能|可以吗|建议|技巧|方法|办法|讲讲|推荐|告诉我'
CORRECTION = r'更正|纠正|不是|取消|撤销|删除|删掉|上一条|上条|前一条'
CLAUSE = re.compile(r'[^，,。；;！!\n]+')


def _local_date(turn, days=0):
    ref = datetime.fromtimestamp(turn['reference_at'] / 1000, zone(turn['timezone']))
    return (ref.date() + timedelta(days=days)).isoformat()


def _query(turn, content):
    if not re.search(QUERY_WORDS, content):
        return None
    if re.search(r'哭', content):
        kind, extra = 'cry_summary', {}
    elif re.search(r'奶|喂|喝', content):
        kind, extra = 'feeding_summary', {}
    elif re.search(EVENT_WORDS['sleep'], content):
        kind, extra = 'sleep_summary', {}
    elif re.search(EVENT_WORDS['diaper'], content):
        kind, extra = 'care_list', {'event_type': 'diaper'}
    elif re.search(r'记录|事件|情况', content):
        kind, extra = 'care_list', {}
    else:
        return None
    night = re.search(r'昨晚|昨天(?:晚上|夜里|夜间)|前晚|前天(?:晚上|夜里|夜间)', content)
    if kind in ('sleep_summary', 'cry_summary') and night:
        first = last = _local_date(turn, -2 if night[0].startswith('前') else -1)
        extra['period'] = 'night'
    elif re.search(r'最近|这周|本周|一周|七天|7天', content):
        first, last = _local_date(turn, -6), _local_date(turn)
    else:
        offset = -2 if '前天' in content else -1 if re.search(r'昨天|昨晚', content) else 0
        first = last = _local_date(turn, offset)
    return dict(kind=kind, **{'from': first, 'to': last}, **extra)


def _evidence(content, kind):
    """取第一个到最后一个命中子句之间的连续原文，保证 evidence 是原文片段。"""
    spans = [m.span() for m in CLAUSE.finditer(content) if re.search(EVENT_WORDS[kind], m.group())]
    if not spans:
        return None
    return content[spans[0][0]:spans[-1][1]].strip()


def _amount(text):
    found = re.search(r'(?:喝了|喝完了|饮用了|吃了|摄入了|喂了)\s*(\d{1,4})\s*(?:ml|毫升)', text, re.I)
    return int(found[1]) if found else None


def rule_result(turn, db):
    """返回 {'answer','actions','query'} 或 None（交给模型）。"""
    content = turn['content'].strip()
    linked = db.linked_proposal(turn)
    if linked:
        # 家长在补充追问卡片：类型沿用原提案，时间/数量由 plan_actions 从原文解析
        proposed = json.loads(linked['proposed_fields_json'])
        action = dict(operation=linked['operation'], event_type=proposed['event_type'],
                      event_state='past', evidence=content)
        return dict(answer='', actions=[action], query=None)
    if re.search(QUESTION, content) or re.search(QUERY_WORDS, content):
        query = _query(turn, content)
        # 其他提问（育儿建议等）交给模型聊天
        return dict(answer='', actions=[], query=query) if query else None
    kinds = [k for k, pattern in EVENT_WORDS.items() if re.search(pattern, content)]
    if not kinds:
        if re.search(CORRECTION, content) and re.search(r'上一条|上条|前一条', content):
            # 只说“删除/更正上一条”时，用上一轮唯一事件的类型；找不到时 plan_actions 会要求确认
            target = db.latest_event_for_message(turn['conversation_id'], turn['data_scope'], turn['id'])
            kind = target['event_type'] if target else 'activity'
            operation = 'delete' if re.search(r'取消|撤销|删除|删掉', content) else 'update'
            return dict(answer='', actions=[dict(operation=operation, event_type=kind,
                                                 event_state='past', evidence=content)], query=None)
        return None
    actions = []
    for kind in kinds:
        evidence = content if len(kinds) == 1 else _evidence(content, kind)
        if not evidence:
            continue
        action = dict(operation='create', event_type=kind, event_state='past', evidence=evidence)
        if kind == 'feeding' and _amount(evidence) is not None:
            action['amount_ml'] = _amount(evidence)
        actions.append(action)
    return dict(answer='', actions=actions, query=None) if actions else None
