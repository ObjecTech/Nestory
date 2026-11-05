"""Conservative grounding: model JSON is a proposal, never a database command."""
from __future__ import annotations

import re
import json
from datetime import datetime, timedelta

from .assistant_common import AssistantError, EVENT_FIELDS, event_values, fields, instant, text, timestamp, zone

NUM=r'(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})'
CLOCK=re.compile(r'(?P<day>今天|昨天|前天)?\s*(?P<part>凌晨|早上|上午|中午|下午|晚上|昨晚)?\s*(?P<h>'+NUM+r')\s*(?:[:：]\s*(?P<m>\d{2})|点\s*(?:(?P<half>半)|(?P<minutes>'+NUM+r')\s*分?)?)')

def number(s):
    if s.isdigit():return int(s)
    digits=dict(zip('零〇一二两三四五六七八九',(0,0,1,2,2,3,4,5,6,7,8,9)))
    if '十' in s:
        a,b=s.split('十');return (digits.get(a,1)*10)+digits.get(b,0)
    return digits.get(s,-1)


def local_times(content,reference,tz):
    """Resolve explicitly supported dates/clocks; missing/ambiguous means confirmation."""
    ref=datetime.fromtimestamp(reference/1000,zone(tz));result=[];last_part=None
    iso=re.search(r'(\d{4}-\d{2}-\d{2})',content)
    inherited_day=0
    for match in CLOCK.finditer(content):
        # Do not mistake numeric components of an ISO date for a clock.
        h=number(match['h']);m=30 if match['half'] else number(match['m'] or match['minutes'] or '0')
        day=match['day'];part=match['part'] or (None if day else last_part)
        if day:inherited_day={'今天':0,'昨天':-1,'前天':-2}[day]
        if part=='昨晚':inherited_day=-1;part='晚上'
        if part in ('下午','晚上') and h<12:h+=12
        if part=='中午' and h<11:h+=12
        if not 0<=h<=23 or not 0<=m<=59:return []
        # A 1–12 hour without an explicit daypart is ambiguous unless inherited.
        if 1<=h<=12 and part is None and ':' not in match.group(0) and '：' not in match.group(0):return []
        base=ref.date()+timedelta(days=inherited_day)
        if iso:
            try:base=datetime.fromisoformat(iso[1]).date()
            except ValueError:return []
        dt=datetime.combine(base,datetime.min.time(),zone(tz)).replace(hour=h,minute=m)
        # Reject non-existent local times and ambiguous DST folds.
        if dt.fold==0 and dt.utcoffset()!=dt.replace(fold=1).utcoffset():return []
        if datetime.fromtimestamp(dt.timestamp(),zone(tz)).replace(tzinfo=None)!=dt.replace(tzinfo=None):return []
        result.append(int(dt.timestamp()*1000));last_part=part
    if len(result)>=2 and result[1]<=result[0]:
        if any(x in content for x in ('跨午夜','到今天','至今天','第二天','次日')):result[1]+=86400000
        else:return []
    # “现在”指说这句话的时刻（语音记录也是即时转发的）；“刚才”时间不确定，仍要确认
    if not result and '现在' in content:return [int(reference)]
    return result


def card(operation,status,reason,message,proposed=None,target=None,version=None,missing=None):
    return dict(action_id=None,operation=operation,status=status,reason_code=reason,message=message,event=None,
                proposed_event=proposed,missing_fields=missing or [],target_event_id=target,expected_version=version,expires_at=None)


def plan_actions(result,turn,db):
    fields(result,('answer','actions','query'),('answer','actions','query'))
    text(result['answer'],4000,'answer',empty=True)
    actions=result['actions']
    if not isinstance(actions,list) or len(actions)>4:raise AssistantError('invalid_model_output',502,retryable=True)
    output=[];content=turn['content'];linked=db.linked_proposal(turn)
    serialized=[json.dumps(a,sort_keys=True,ensure_ascii=False) for a in actions]
    if len(set(serialized))!=len(serialized):raise AssistantError('invalid_model_output',502,retryable=True)
    if linked and len(actions)>1:raise AssistantError('invalid_model_output',502,retryable=True)
    for action in actions:
        try:
            fields(action,('operation','event_type','occurred_at','end_at','amount_ml','diaper_type','notes','event_state','evidence','missing_fields','target_event_id'),('operation','event_type','event_state','evidence'))
            operation=action['operation'];kind=action['event_type'];evidence=action['evidence']
            if operation not in ('create','update','delete') or kind not in ('feeding','diaper','sleep','activity'):
                raise ValueError()
            if not isinstance(evidence,str) or not evidence.strip() or evidence not in content:raise ValueError()
            if action.get('event_state') not in ('past','future','uncertain'):raise ValueError()
            if 'missing_fields' in action and (not isinstance(action['missing_fields'],list) or any(f not in EVENT_FIELDS for f in action['missing_fields'])):raise ValueError()
            if not isinstance(action.get('notes',''),str) or len(action.get('notes',''))>2000:raise ValueError()
            for k in ('occurred_at','end_at'):
                if action.get(k) is not None:timestamp(action[k],k)
            if action.get('amount_ml') is not None and (type(action['amount_ml']) is not int or not 1<=action['amount_ml']<=2000):raise ValueError()
            if action.get('diaper_type') not in (None,'wet','dirty','mixed','unknown'):raise ValueError()
        except (AssistantError,ValueError,TypeError):raise AssistantError('invalid_model_output',502,retryable=True) from None
        if linked:
            proposed=json.loads(linked['proposed_fields_json']);proposed['scope']=turn['data_scope']
            if linked['operation']=='delete' or re.search(r'准备(?!了)|计划|明天|后天|没有|没喝|取消|吗|\?',content):
                item=card(linked['operation'],'needs_confirmation','correction_requires_confirmation','请在记录卡片中明确确认或取消。',proposed,linked['target_event_id'],linked['expected_version'],json.loads(linked['missing_fields_json']))
            else:
                times=local_times(content,turn['reference_at'],turn['timezone'])
                if times:proposed['occurred_at']=instant(times[0])
                if len(times)>1:proposed['end_at']=instant(times[1])
                if proposed['event_type']=='feeding':
                    consumed=re.search(r'(?:喝了|喝完了|饮用了|吃了|摄入了|喂了)\s*(\d{1,4})\s*(?:ml|毫升)',content,re.I)
                    if consumed:proposed['amount_ml']=int(consumed[1])
                if proposed['event_type']=='diaper' and proposed.get('diaper_type') is None:proposed['diaper_type']='unknown'
                try:
                    values={k:v for k,v in proposed.items() if k!='scope'}
                    event_values(values,now=turn['reference_at'])
                    item=card(linked['operation'],'saved','parent_confirmed','已记录确认的生活事件')
                    item['_values']=values
                except AssistantError:
                    missing=[k for k in json.loads(linked['missing_fields_json']) if proposed.get(k) is None]
                    item=card(linked['operation'],'needs_confirmation','time_or_state_ambiguous','请确认缺少的具体信息。',proposed,linked['target_event_id'],linked['expected_version'],missing)
            item['_origin_proposal_id']=linked['origin_proposal_id'] or linked['id'];output.append(item);continue
        correction=operation!='create' or bool(re.search(r'更正|纠正|不是|取消|撤销|上一条|上条|实际',content) and re.search(r'更正|纠正|不是|取消|撤销|上一条|上条',content))
        if correction and operation=='create':operation='delete' if re.search(r'取消|撤销|没喝|没有喝|删',content) else 'update'
        if not correction and re.search(r'(?:我|爸爸|妈妈|大人|成人)(?:自己|今天|昨天|刚才|上午|下午|\d|点|[:：]|\s)*(?:喝了|睡了)',content):
            output.append(card('none','not_saved','not_child_event','该描述不是明确的孩子生活事件，未保存。'));continue
        if not correction and (action['event_state']=='future' or re.search(r'明天|后天|计划|准备(?!了)|打算|将要|如果|假如|没喝|没有|并未|还没|未发生|不曾|没有换|没有睡|吗|\?',content)):
            output.append(card('none','not_saved','future_plan' if re.search(r'明天|后天|计划|准备(?!了)|打算|将要',content) else 'not_explicit_past_event','不是明确完成的生活事件，未保存。'));continue
        proposed=dict(event_type=kind,scope=turn['data_scope'],timezone=turn['timezone'],occurred_at=None,end_at=None,amount_ml=action.get('amount_ml'),diaper_type='unknown' if kind=='diaper' else None,notes=action.get('notes',''))
        target=None
        if correction:
            eid=action.get('target_event_id')
            if eid:
                # Caller must explicitly name the opaque event ID; otherwise require unique previous turn.
                if eid in content:
                    try:target=db.event(eid,turn['data_scope'])
                    except AssistantError:target=None
            if target is None and re.search(r'上一条|上条|前一条',content):target=db.latest_event_for_message(turn['conversation_id'],turn['data_scope'],turn['id'])
            if target:
                proposed={k:target[k] for k in ('event_type','scope','timezone','occurred_at','end_at','amount_ml','diaper_type','notes')}
                if kind=='feeding' and action.get('amount_ml') is not None:
                    explicit=re.findall(r'(\d{1,4})\s*(?:ml|毫升)',content,re.I)
                    if str(action['amount_ml']) in explicit:proposed['amount_ml']=action['amount_ml']
                times=local_times(content,turn['reference_at'],turn['timezone'])
                if times:proposed['occurred_at']=instant(times[0])
                if len(times)>1:proposed['end_at']=instant(times[1])
            output.append(card(operation,'needs_confirmation','correction_requires_confirmation','请确认要更正或删除的生活记录。',proposed,target['id'] if target else None,target['version'] if target else None,[] if target else ['target_event_id']));continue
        grounding=content if len(actions)==1 else evidence
        times=local_times(grounding,turn['reference_at'],turn['timezone'])
        # Require model timestamp to agree with grounded local phrase; otherwise ask.
        if times and action.get('occurred_at') is not None and timestamp(action['occurred_at'])!=times[0]:times=[]
        if times:proposed['occurred_at']=instant(times[0])
        if kind=='sleep' and len(times)==2:
            if action.get('end_at') is not None and timestamp(action['end_at'])!=times[1]:times=[];proposed['occurred_at']=None
            else:proposed['end_at']=instant(times[1])
        missing=[]
        if proposed['occurred_at'] is None:missing.append('occurred_at')
        if kind=='feeding':
            consumed=re.search(r'(?:喝了|喝完了|饮用了|吃了|摄入了|喂了)\s*(\d{1,4})\s*(?:ml|毫升)',grounding,re.I)
            if not consumed or proposed['amount_ml']!=int(consumed[1]):proposed['amount_ml']=None;missing.append('amount_ml')
        elif kind=='diaper':
            if not re.search(r'换.*尿布|换.*纸尿裤|尿布.*换',content):
                output.append(card('none','not_saved','not_explicit_past_event','没有明确描述尿布更换，未保存。'));continue
            if re.search(r'尿湿|湿尿布',content):proposed['diaper_type']='wet'
            if re.search(r'排便|大便|便便',content):proposed['diaper_type']='mixed' if proposed['diaper_type']=='wet' else 'dirty'
        elif kind=='sleep':
            if not re.search(r'睡|补觉',content):raise AssistantError('invalid_model_output',502,retryable=True)
            if proposed['end_at'] is None:missing.append('end_at')
        elif not re.search(r'散步|玩耍|玩了|活动|洗澡',content):
            output.append(card('none','not_saved','unsupported_activity','请说明已完成的具体活动。'));continue
        if not missing:
            try:
                values=event_values({k:v for k,v in proposed.items() if k!='scope'},now=turn['reference_at'])
            except AssistantError:
                missing=['occurred_at'] if proposed['end_at'] is None else ['occurred_at','end_at']
        if missing or action['event_state']=='uncertain':
            output.append(card('create','needs_confirmation','actual_intake_unknown' if 'amount_ml' in missing else 'time_or_state_ambiguous','请确认具体时间、实际数量或事件状态。',proposed,missing=missing));continue
        item=card('create','saved','explicit_past_event','已记录生活事件');item['_values']={k:v for k,v in proposed.items() if k!='scope'};output.append(item)
    seen=set()
    for item in output:
        if item.get('_values'):
            signature=json.dumps(item['_values'],sort_keys=True,ensure_ascii=False)
            if signature in seen:raise AssistantError('invalid_model_output',502,retryable=True)
            seen.add(signature)
    return output


def safe_answer(answer,content):
    # The backend owns all persistence acknowledgements; discard model-generated ones.
    if ('数据库' in answer or (re.search(r'记录|保存|修改|删除|录入|记下|添加|登记|更新|更正|撤销|归档|记住',answer) and re.search(r'成功|已经|已|完成|好了|记住了|记下了|录入了|登记了|添加了|更新了',answer))):answer=''
    if re.search(r'确诊|诊断为|服用|用药|剂量|治疗方案',answer):answer='涉及健康或用药的问题请咨询专业医护人员，我只能提供一般信息。'
    if re.search(r'发烧|发热|呼吸|用药|药物|疾病|病了',content):answer+=' 健康问题需要专业医护人员评估，不能依据聊天或摄像头观察进行诊断。'
    return answer.strip()
