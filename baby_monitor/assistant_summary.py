"""每日总结：数字与要点由 Python 计算；模型只做可选的措辞润色，且不得引入新数字。"""
from __future__ import annotations

import re
import threading
from collections import Counter
from datetime import datetime, timedelta

from .assistant_common import AssistantError, date_window, digest, instant, now_ms, scope, timestamp, zone
from .assistant_extraction import safe_answer

REACTIONS = {'happy': '开心', 'calm': '平静', 'uninterested': '没兴趣', 'fussy': '哭闹'}
DISCLAIMER = '以上为记录汇总，仅供参考，不构成医疗建议。'


def stats_local(iso, tz):
    return datetime.fromtimestamp(timestamp(iso) / 1000, zone(tz)).strftime('%H:%M')


def duration_text(seconds):
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours} 小时 {minutes} 分钟' if hours and minutes else f'{hours} 小时' if hours else f'{minutes} 分钟'


class DailySummary:
    def __init__(self, facts, get_model):
        # get_model 每次取当前模型客户端（测试或服务可替换）
        self.facts, self.get_model = facts, get_model
        self._cache, self._lock = {}, threading.Lock()

    def build(self, query, narrative=False):
        a, b, tz, start, end = date_window(query)
        if a != b:
            raise AssistantError('validation_error', details=[dict(field='date', reason='single_day_only')])
        data_scope = scope(query.get('scope', 'demo'))
        day = self.facts.unified_timeline(data_scope, start, end, tz)
        stats = self._stats(day['items'], tz)
        stats['sleep']['parent_seconds'] = day['summary']['parent_sleep_seconds']  # 重叠时段取并集
        cries = [i for i in day['items'] if i['category'] == 'cry']
        stats['cry'] = dict(count=len(cries), seconds=day['summary']['cry_seconds'],
                            last_at=stats_local(cries[-1]['start_at'], tz) if cries else None, reliability='unverified')
        stats['visual'] = dict(day['summary']['visual_activity_seconds'], available=day['visual']['available'],
                               smiles=sum(1 for i in day['items'] if i['type'] == 'smile'), reliability='unverified')
        highlights = self._highlights(stats)
        result = dict(api_version='1', scope=data_scope, timezone=tz, date=a, stats=stats, highlights=highlights,
                      suggestions=self._suggestions(stats, a, tz), trend=self._trend(data_scope, a, tz),
                      narrative=None, narrative_status='not_requested', disclaimer=DISCLAIMER, truncated=day['truncated'])
        if narrative:
            result['narrative'], result['narrative_status'] = self._narrative(data_scope, tz, highlights)
        return result

    @staticmethod
    def _stats(items, tz):
        z = zone(tz)
        local = lambda iso: datetime.fromtimestamp(timestamp(iso) / 1000, z).strftime('%H:%M')
        care = [i for i in items if i['category'] == 'care']
        feeds = [i for i in care if i['type'] == 'feeding']
        times = sorted(timestamp(i['start_at']) for i in feeds)
        gaps = [b - a for a, b in zip(times, times[1:])]
        sleeps = [i for i in care if i['type'] == 'sleep' and i['duration_seconds']]
        diapers = [i for i in care if i['type'] == 'diaper']
        plays = [i for i in items if i['category'] == 'interaction']
        happy = Counter(i['label'] for i in plays if i['reaction'] == 'happy')
        return dict(
            feeding=dict(count=len(feeds), total_ml=sum(i['amount_ml'] or 0 for i in feeds) if feeds else None,
                         last_at=local(feeds[-1]['start_at']) if feeds else None,
                         average_gap_minutes=round(sum(gaps) / len(gaps) / 60000) if gaps else None),
            sleep=dict(parent_seconds=None, sessions=len(sleeps), longest_seconds=max((i['duration_seconds'] for i in sleeps), default=None)),
            diaper=dict(count=len(diapers), dirty=sum(1 for i in diapers if i['diaper_type'] in ('dirty', 'mixed'))),
            interaction=dict(count=len(plays), minutes=round(sum(i['duration_seconds'] or 0 for i in plays) / 60),
                             reactions=dict(Counter(REACTIONS[i['reaction']] for i in plays if i['reaction'])),
                             favorite=happy.most_common(1)[0][0] if happy else None))

    @staticmethod
    def _highlights(s):
        lines = []
        f, sl, d, p, v = s['feeding'], s['sleep'], s['diaper'], s['interaction'], s['visual']
        if f['count']:
            line = f"记录了 {f['count']} 次喂奶" + (f"，共 {f['total_ml']} ml" if f['total_ml'] else '') + f"，最近一次在 {f['last_at']}"
            if f['average_gap_minutes']:
                line += f"，平均间隔约 {duration_text(f['average_gap_minutes'] * 60)}"
            lines.append(line + '。')
        if sl['parent_seconds']:
            lines.append(f"家长记录的睡眠共 {duration_text(sl['parent_seconds'])}（{sl['sessions']} 段，最长 {duration_text(sl['longest_seconds'])}）。")
        if d['count']:
            lines.append(f"换了 {d['count']} 次尿布" + (f"，其中 {d['dirty']} 次有便便" if d['dirty'] else '') + '。')
        if p['count']:
            line = f"进行了 {p['count']} 次亲子互动，共约 {p['minutes']} 分钟"
            if p['favorite']:
                line += f"，宝宝在「{p['favorite']}」时最开心"
            lines.append(line + '。')
        c = s.get('cry') or {}
        if c.get('count'):
            lines.append(f"麦克风记录到 {c['count']} 段哭声，共约 {duration_text(max(60, c['seconds']))}，最近一次在 {c['last_at']}（仅供参考）。")
        if v['available'] and v['smiles']:
            lines.append(f"摄像头捕捉到 {v['smiles']} 次笑容（仅供参考）。")
        return lines or ['今天还没有记录。可以在 AI 助手里说“上午9点宝宝喝了120ml奶”来记录。']

    @staticmethod
    def _suggestions(s, day, tz):
        """温和的记录提醒，不做医疗判断，最多 3 条。"""
        now = datetime.fromtimestamp(now_ms() / 1000, zone(tz))
        today = now.date().isoformat() == day
        tips = []
        if not s['feeding']['count'] and (not today or now.hour >= 10):
            tips.append('还没有喂奶记录，可以在 AI 助手里补记。')
        if not s['sleep']['sessions']:
            tips.append('记录小睡的开始和结束时间，可以看出作息规律。')
        if not s['interaction']['count']:
            tips.append('可以去「陪我玩」挑一个亲子互动，并记录宝宝的反应。')
        return tips[:3]

    def _trend(self, data_scope, day, tz):
        last = datetime.fromisoformat(day).date()
        rows = []
        for offset in range(6, -1, -1):
            d = (last - timedelta(days=offset)).isoformat()
            _, _, _, start, end = date_window({'from': d, 'to': d, 'timezone': tz})
            summary = self.facts.unified_timeline(data_scope, start, end, tz, include_visual=False)['summary']
            rows.append(dict(date=d, feeding_count=summary['feeding_count'], feeding_ml=summary['feeding_ml'],
                             parent_sleep_seconds=summary['parent_sleep_seconds'], diaper_count=summary['diaper_count'],
                             interaction_count=summary['interaction_count']))
        return rows

    def _narrative(self, data_scope, tz, highlights):
        facts_text = ' '.join(highlights)
        key = digest([data_scope, facts_text])
        with self._lock:
            if key in self._cache:
                return self._cache[key], 'ok'
        prompt = '请把下面的记录汇总改写成一两句温暖、简洁的中文，只能使用其中的信息，不要添加任何新的数字或建议：' + facts_text
        payload = dict(request_id='summary-' + key[:16], messages=[dict(role='user', content=prompt)], scope=data_scope,
                       timezone=tz, reference_at=instant(now_ms()), facts=None, max_new_tokens=64)
        try:
            text = safe_answer(self.get_model().chat(payload).get('answer') or '', '')
        except AssistantError as exc:
            return None, exc.code
        # 模型不得引入事实中没有的数字；否则丢弃润色，只显示 Python 要点
        if not text or any(n not in facts_text for n in re.findall(r'\d+', text)):
            return None, 'rejected'
        with self._lock:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = text
        return text, 'ok'
