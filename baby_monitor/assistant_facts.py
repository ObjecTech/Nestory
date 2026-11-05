"""Bounded facts queries. Visual continuity is deliberately not certified."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .assistant_common import AssistantError, date_window, fields, instant, now_ms, scope, sleep_window, timestamp, zone

CARE_LABELS={'feeding':'喂奶','sleep':'睡眠','diaper':'换尿布','activity':'活动'}
DIAPER_LABELS={'wet':'尿湿','dirty':'便便','mixed':'尿湿+便便'}
VISUAL_LABELS={'sleeping':'睡着','awake':'清醒','awake_happy':'清醒·开心','awake_unhappy':'清醒·不开心','crying':'哭闹',
    'supine':'仰卧','side':'侧卧','prone':'趴卧','left_side':'左侧卧','right_side':'右侧卧','left_prone':'左侧趴','right_prone':'右侧趴',
    'visible':'在画面中','not_visible':'不在画面','unknown':'未知'}


class AssistantFacts:
    def __init__(self, database, visual_path=None, visual_mode=None):
        self.database, self.visual_path = database, Path(visual_path) if visual_path else None
        # Real records read the vision data of the mode currently selected in
        # settings (infant or adult test), so adult testing still fills the timeline.
        self._visual_mode = visual_mode or (lambda: 'infant')

    def visual_mode(self):
        mode = self._visual_mode()
        return mode if mode in ('infant', 'adult_test') else 'infant'

    def sleep_summary(self, query):
        data_scope=scope(query.get('scope','demo'));a,b,tz,start,end=sleep_window(query)
        total=0;count=0;union_start=union_end=None
        with self.database.lock:
            rows=self.database.connection.execute("SELECT occurred_at,end_at FROM care_events WHERE data_scope=? AND event_type='sleep' AND status='confirmed' AND occurred_at<? AND end_at>? ORDER BY occurred_at,end_at",(data_scope,end,start))
            for row in rows:
                count+=1;s,e=max(start,row[0]),min(end,row[1])
                if union_start is None:union_start,union_end=s,e
                elif s<=union_end:union_end=max(union_end,e)
                else:total+=union_end-union_start;union_start,union_end=s,e
            if union_start is not None:total+=union_end-union_start
        camera=dict(seconds=None,reliability='unverified',source='camera_observation',
                    observation_mode=self.visual_mode() if data_scope=='real' else None,
                    reason_code='monitoring_continuity_unverified' if data_scope=='real' else 'demo_visual_data_unavailable')
        result=dict(api_version='1',scope=data_scope,timezone=tz,**{'from':a,'to':b},
            parent_sleep=dict(seconds=total/1000 if count else None,event_count=count,
                reliability='parent_reported' if count else 'insufficient_data',source='parent_reported',method='clipped_interval_union'),
            camera_sleep=camera,combined_sleep_seconds=None,coverage=dict(complete_day=False,unknown_seconds=None),
            warnings=['摄像头监测连续性尚未验证，家长记录不代表完整全天监测。' if count else '缺少家长确认的睡眠记录，摄像头监测连续性尚未验证，无法可靠计算全天睡眠时长。'])
        if query.get('period')=='night':
            result['period']='night'
            result['window']=dict(start_at=instant(start),end_at=instant(end),has_ended=now_ms()>=end)
            if data_scope=='real':result['cry']=self.cry_periods(start,end)
            if not result['window']['has_ended']:
                result['warnings'].append('夜间统计窗口尚未结束，当前仅汇总已确认的睡眠记录。')
        return result

    def query(self, intent, data_scope, timezone):
        fields(intent,('kind','from','to','event_type','period'),('kind','from','to'))
        kind=intent['kind']
        if kind not in ('sleep_summary','care_list','feeding_summary','cry_summary'):raise AssistantError('invalid_model_output',502,retryable=True)
        if 'period' in intent and kind not in ('sleep_summary','cry_summary'):raise AssistantError('invalid_model_output',502,retryable=True)
        args=dict(scope=data_scope,timezone=timezone,**{'from':intent['from'],'to':intent['to']})
        if 'period' in intent:args['period']=intent['period']
        _,_,_,start,end=(sleep_window if kind in ('sleep_summary','cry_summary') else date_window)(args)
        if kind=='sleep_summary':
            result=self.sleep_summary(args);result.pop('api_version')
        elif kind=='cry_summary':
            result=dict(scope=data_scope,timezone=timezone,**{'from':intent['from'],'to':intent['to']},
                cry=self.cry_periods(start,end) if data_scope=='real' else dict(count=0,seconds=0,periods=[],reliability='unverified'))
            if intent.get('period')=='night':
                result['period']='night';result['window']=dict(start_at=instant(start),end_at=instant(end),has_ended=now_ms()>=end)
        elif kind=='feeding_summary':
            with self.database.lock:
                row=self.database.connection.execute("SELECT COUNT(*),COALESCE(SUM(amount_ml),0) FROM care_events WHERE data_scope=? AND event_type='feeding' AND status='confirmed' AND occurred_at>=? AND occurred_at<?",(scope(data_scope),start,end)).fetchone()
            result=dict(scope=data_scope,timezone=timezone,**{'from':intent['from'],'to':intent['to']},feeding_count=row[0],parent_reported_ml=row[1] if row[0] else None,reliability='parent_reported' if row[0] else 'insufficient_data')
        else:
            if intent.get('event_type'):args['event_type']=intent['event_type']
            args['limit']='100';result=dict(scope=data_scope,**self.database.events(data_scope,start,end,args))
            result['truncated']=result['has_more'];result['totals_complete']=False
        result['query']=intent
        if data_scope=='real':result['visual_evidence']=self.visual_evidence(start,end)
        return result

    def cry_periods(self,start,end):
        """Crying periods heard by the microphone (no audio is stored). Continuity is unverified."""
        mode=self.visual_mode()
        result=dict(count=0,seconds=0,periods=[],observation_mode=mode,source='microphone',reliability='unverified',truncated=False)
        if not self.visual_path or not self.visual_path.is_file():
            result['reason_code']='audio_data_unavailable';return result
        try:
            with sqlite3.connect('file:'+quote(str(self.visual_path.resolve()))+'?mode=ro',uri=True,timeout=1) as conn:
                rows=conn.execute("SELECT start_ts,end_ts FROM intervals WHERE category='cry' AND observation_mode=? AND start_ts<? AND COALESCE(end_ts,?)>? ORDER BY start_ts LIMIT 51",
                    (mode,end/1000,now_ms()/1000,start/1000)).fetchall()
        except sqlite3.Error:
            result['reason_code']='audio_data_unavailable';return result
        now=now_ms()
        for s,e in rows[:50]:
            a=max(int(s*1000),start);b=min(int(e*1000) if e is not None else now,end)
            result['periods'].append(dict(start_at=instant(a),end_at=instant(b),duration_seconds=max(0,b-a)//1000,ongoing=e is None))
        result['count']=len(result['periods']);result['seconds']=sum(p['duration_seconds'] for p in result['periods'])
        result['truncated']=len(rows)>50
        return result

    def visual_evidence(self,start,end):
        mode=self.visual_mode()
        result=dict(observation_mode=mode,continuity_verified=False,observations=[],intervals=[],truncated=False)
        if not self.visual_path or not self.visual_path.is_file():return result
        try:
            with sqlite3.connect('file:'+quote(str(self.visual_path.resolve()))+'?mode=ro',uri=True,timeout=1) as conn:
                conn.row_factory=sqlite3.Row
                rows=conn.execute("SELECT ts,sleep_state,visibility FROM observations WHERE observation_mode=? AND ts>=? AND ts<? ORDER BY ts DESC LIMIT 51",(mode,start/1000,end/1000)).fetchall()
                result['observations']=[dict(ts=instant(int(r['ts']*1000)),sleep_state=r['sleep_state'],visibility=r['visibility']) for r in rows[:50]]
                spans=conn.execute("SELECT start_ts,end_ts,value FROM intervals WHERE observation_mode=? AND category='activity' AND start_ts<? AND (end_ts IS NULL OR end_ts>?) ORDER BY start_ts DESC LIMIT 51",(mode,end/1000,start/1000)).fetchall()
                result['intervals']=[dict(start_at=instant(int(r['start_ts']*1000)),end_at=instant(int(r['end_ts']*1000)) if r['end_ts'] is not None else None,value=r['value'],reliable_for_duration=False) for r in spans[:50]]
                result['truncated']=len(rows)>50 or len(spans)>50
        except sqlite3.Error:
            result['reason_code']='visual_data_unavailable'
        return result

    def unified_timeline(self, data_scope, start, end, tz, include_visual=True):
        """统一只读时间线：视觉区间/事件＋家长记录＋互动记录，按 UTC 时间升序合并。

        Real 读设置里当前选择的观察模式（婴儿或成人测试）；Demo 只读 adult_test。都不认证连续性。
        """
        data_scope=scope(data_scope);mode=self.visual_mode() if data_scope=='real' else 'adult_test'
        items=[];now=now_ms()
        with self.database.lock:
            care=self.database.connection.execute("SELECT * FROM care_events WHERE data_scope=? AND status='confirmed' AND occurred_at<? AND COALESCE(end_at,occurred_at)>=? ORDER BY occurred_at LIMIT 501",(data_scope,end,start)).fetchall()
            plays=self.database.connection.execute('SELECT * FROM interaction_events WHERE data_scope=? AND started_at<? AND COALESCE(ended_at,started_at)>=? ORDER BY started_at LIMIT 501',(data_scope,end,start)).fetchall()
        for r in care[:500]:
            label=CARE_LABELS[r['event_type']]
            if r['amount_ml']:label+=f" {r['amount_ml']} ml"
            if r['diaper_type'] in DIAPER_LABELS:label+='·'+DIAPER_LABELS[r['diaper_type']]
            items.append(dict(id='care:'+r['id'],source=r['source'],category='care',type=r['event_type'],label=label,
                start_at=instant(r['occurred_at']),end_at=instant(r['end_at']),duration_seconds=None if r['end_at'] is None else (r['end_at']-r['occurred_at'])//1000,
                reliability='parent_reported',amount_ml=r['amount_ml'],diaper_type=r['diaper_type'],notes=r['notes']))
        for r in plays[:500]:
            items.append(dict(id='interaction:'+r['id'],source='interaction',category='interaction',type=r['kind'],label=r['title'],
                start_at=instant(r['started_at']),end_at=instant(r['ended_at']),duration_seconds=None if r['ended_at'] is None else (r['ended_at']-r['started_at'])//1000,
                reliability='parent_reported',reaction=r['reaction'],notes=r['notes']))
        visual=dict(observation_mode=mode,continuity_verified=False,available=False)
        if not include_visual:
            visual['reason_code']='not_requested'
        elif self.visual_path and self.visual_path.is_file():
            try:
                with sqlite3.connect('file:'+quote(str(self.visual_path.resolve()))+'?mode=ro',uri=True,timeout=1) as conn:
                    conn.row_factory=sqlite3.Row
                    spans=conn.execute("SELECT id,category,value,start_ts,end_ts,source,confidence FROM intervals WHERE observation_mode=? AND category IN ('activity','posture','presence','cry') AND start_ts<? AND COALESCE(end_ts,?)>? ORDER BY start_ts LIMIT 1001",(mode,end/1000,now/1000,start/1000)).fetchall()
                    points=conn.execute("SELECT id,event_type,ts,activity,confidence FROM events WHERE observation_mode=? AND event_type IN ('smile','wake') AND ts>=? AND ts<? ORDER BY ts LIMIT 501",(mode,start/1000,end/1000)).fetchall()
                visual['available']=True
                for r in spans[:1000]:
                    s=max(int(r['start_ts']*1000),start);e=min(int(r['end_ts']*1000) if r['end_ts'] is not None else now,end)
                    cry=r['category']=='cry'  # crying periods come from the microphone, not the camera
                    items.append(dict(id=f"{'audio' if cry else 'vision'}:interval:{r['id']}",source='audio' if cry else 'vision',category=r['category'],type=r['value'],
                        label='哭声' if cry else VISUAL_LABELS.get(r['value'],r['value']),start_at=instant(s),end_at=instant(e),duration_seconds=max(0,e-s)//1000,
                        open=r['end_ts'] is None,reliability='unverified',confidence=r['confidence'],observation_mode=mode))
                for r in points[:500]:
                    items.append(dict(id=f"vision:event:{r['id']}",source='vision',category='moment',type=r['event_type'],
                        label='笑了' if r['event_type']=='smile' else '醒来',start_at=instant(int(r['ts']*1000)),end_at=None,duration_seconds=None,
                        reliability='unverified',confidence=r['confidence'],observation_mode=mode))
            except sqlite3.Error:
                visual['reason_code']='visual_data_unavailable'
        else:
            visual['reason_code']='visual_data_unavailable'
        truncated=len(care)>500 or len(plays)>500 or (visual['available'] and (len(spans)>1000 or len(points)>500))
        items.sort(key=lambda i:(i['start_at'],i['id']))
        feeds=[i for i in items if i['category']=='care' and i['type']=='feeding']
        summary=dict(feeding_count=len(feeds),feeding_ml=sum(i['amount_ml'] or 0 for i in feeds) if feeds else None,
            diaper_count=sum(1 for i in items if i['category']=='care' and i['type']=='diaper'),
            parent_sleep_seconds=self._union_seconds([(timestamp(i['start_at']),timestamp(i['end_at'])) for i in items if i['category']=='care' and i['type']=='sleep' and i['end_at']],start,end),
            interaction_count=sum(1 for i in items if i['category']=='interaction'),
            cry_count=sum(1 for i in items if i['category']=='cry'),
            cry_seconds=sum(i['duration_seconds'] for i in items if i['category']=='cry'),
            visual_activity_seconds={},visual_reliability='unverified')
        for i in items:
            if i['source']=='vision' and i['category']=='activity':
                summary['visual_activity_seconds'][i['type']]=summary['visual_activity_seconds'].get(i['type'],0)+i['duration_seconds']
        return dict(visual=visual,items=items,summary=summary,truncated=truncated,
            warnings=['摄像头监测连续性未验证，视觉时长仅供参考，不能与家长记录相加。'])

    @staticmethod
    def _union_seconds(spans,start,end):
        total=0;cur=None
        for s,e in sorted((max(s,start),min(e,end)) for s,e in spans):
            if e<=s:continue
            if cur and s<=cur[1]:cur[1]=max(cur[1],e)
            else:
                if cur:total+=cur[1]-cur[0]
                cur=[s,e]
        if cur:total+=cur[1]-cur[0]
        return total//1000 if spans else None

    @staticmethod
    def _cry_text(cry,tz):
        z=zone(tz);hm=lambda v:datetime.fromtimestamp(timestamp(v)/1000,z).strftime('%H:%M')
        spans='、'.join(f"{hm(p['start_at'])}–{hm(p['end_at'])}" for p in cry['periods'][:6])
        more='等' if cry['count']>6 else ''
        return f"麦克风记录到 {cry['count']} 段哭声（{spans}{more}），共约 {max(1,round(cry['seconds']/60))} 分钟"

    @staticmethod
    def explanation(facts):
        if 'cry' in facts and 'parent_sleep' not in facts:
            cry=facts['cry'];tz=facts['timezone']
            scope_text='夜间（20:00 至次日 10:00）' if facts.get('period')=='night' else '所选时间范围内'
            if not cry['count']:
                return f'{scope_text}麦克风没有记录到哭声。麦克风是否全程在线未经验证，没有记录不代表一定没哭。'
            return f"{scope_text}{AssistantFacts._cry_text(cry,tz)}。仅供参考，不作为医学判断。"
        if 'parent_sleep' in facts:
            seconds=facts['parent_sleep']['seconds']
            if facts.get('period')=='night':
                z=zone(facts['timezone'])
                local=lambda value:datetime.fromtimestamp(timestamp(value)/1000,z).strftime('%m月%d日 %H:%M')
                window=f"夜间（{local(facts['window']['start_at'])} 至 {local(facts['window']['end_at'])}）"
                if seconds is None:return window+'没有已确认的家长睡眠记录，无法可靠计算睡眠时长；未记录的时间不视为睡眠。'
                hours,remainder=divmod(seconds,3600)
                duration=(f'{hours:g} 小时' if hours else '')+(f' {remainder/60:g} 分钟' if remainder else '')
                pending='窗口尚未结束，当前仅统计已确认记录。' if not facts['window']['has_ended'] else ''
                cry=facts.get('cry') or {}
                heard=f"另外，{AssistantFacts._cry_text(cry,facts['timezone'])}，如果宝宝那时醒了，可以告诉我补记。" if cry.get('count') else ''
                return f'{window}已确认的家长睡眠累计 {duration.strip()}（重叠区间已去重）。{pending}未记录的时间不视为睡眠，摄像头时长未加入。{heard}'
            if seconds is None:return '缺少已确认的家长睡眠记录，摄像头监测连续性尚未确认，无法可靠计算睡眠时长。'
            return f'已确认的家长睡眠记录在所选时间范围内共 {seconds/60:g} 分钟（重叠区间已去重）。这不是完整全天监测结果，不能与摄像头观察时长直接相加。'
        if 'feeding_count' in facts:
            if not facts['feeding_count']:return '所选时间范围内没有已确认的喂养记录，数据不足。'
            return f"所选时间范围内家长报告了 {facts['feeding_count']} 次喂养，实际饮用量合计 {facts['parent_reported_ml']} ml。不能据此判断因果关系或作临床诊断。"
        return '已查询所选范围的家长生活记录。记录可能不完整，不能由此推断未记录期间的情况或因果关系。'

    @staticmethod
    def model_context(facts):
        """Compact evidence for the model; browser still receives the bounded full facts."""
        result={k:v for k,v in facts.items() if k not in ('events','visual_evidence','next_cursor')}
        if 'events' in facts:
            result['events']=[{k:e[k] for k in ('event_type','occurred_at','end_at','amount_ml','diaper_type')} for e in facts['events'][:4]]
            result['evidence_truncated']=len(facts['events'])>4 or facts.get('has_more',False)
        if 'visual_evidence' in facts:
            result['visual_evidence']={k:v for k,v in facts['visual_evidence'].items() if k not in ('observations','intervals')}
            result['visual_evidence']['observations']=facts['visual_evidence']['observations'][:2]
            result['visual_evidence']['intervals']=facts['visual_evidence']['intervals'][:2]
            result['visual_evidence']['truncated']=bool(facts['visual_evidence']['truncated'] or len(facts['visual_evidence']['observations'])>2 or len(facts['visual_evidence']['intervals'])>2)
        return result
