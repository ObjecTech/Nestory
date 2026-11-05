#!/usr/bin/env python3
"""Isolated local frontend integration server; synthetic model, never Jetson Qwen."""
from __future__ import annotations

import argparse
from dataclasses import replace
import getpass
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from access_control import AccessStateStore
from baby_monitor.app import BabyMonitorApp
from baby_monitor.assistant_common import instant,timestamp
from baby_monitor.assistant_extraction import local_times
from baby_monitor.config import Config
from security import AuthConfig,make_password_hash
from server import Handler,MonitorServer


class SyntheticAssistantModel:
    """Development fixtures, clearly distinct from actual Qwen acceptance."""
    def chat(self,payload):
        if payload['facts'] is not None:
            return dict(answer='开发模拟：后端计算的事实已收到。',actions=[],query=None)
        content=payload['messages'][-1]['content'];times=local_times(content,timestamp(payload['reference_at']),payload['timezone'])
        kind=('feeding' if re.search(r'奶|毫升|ml',content,re.I) else 'diaper' if re.search(r'尿布|纸尿裤',content) else 'sleep' if re.search(r'睡|补觉',content) else 'activity' if re.search(r'散步|玩耍|洗澡',content) else None)
        actions=[];query=None
        if re.search(r'睡.*多久|睡眠.*时长|(?:查询|查看|多少|几次|记录).*喂奶|喂奶.*(?:记录|多少|几次)|(?:查询|查看).*生活记录',content):
            from datetime import datetime,timedelta
            from zoneinfo import ZoneInfo
            day=datetime.fromtimestamp(timestamp(payload['reference_at'])/1000,ZoneInfo(payload['timezone'])).date()
            if '昨天' in content:day-=timedelta(days=1)
            query_kind='sleep_summary' if re.search(r'睡.*多久|睡眠.*时长',content) else 'feeding_summary' if '喂奶' in content else 'care_list'
            query=dict(kind=query_kind,**{'from':day.isoformat(),'to':day.isoformat()})
        elif kind:
            amount=re.search(r'(\d{1,4})\s*(?:ml|毫升)',content,re.I)
            operation='delete' if re.search(r'取消|删除|撤销',content) else 'update' if re.search(r'更正|不是|上一条',content) else 'create'
            actions=[dict(operation=operation,event_type=kind,occurred_at=instant(times[0]) if times else None,end_at=instant(times[1]) if kind=='sleep' and len(times)>1 else None,
                amount_ml=int(amount[1]) if kind=='feeding' and amount else None,diaper_type='unknown' if kind=='diaper' else None,
                notes='模拟活动' if kind=='activity' else '',event_state='future' if re.search(r'明天|后天|计划',content) else 'past' if times else 'uncertain',evidence=content,missing_fields=[],target_event_id=None)]
        return dict(answer='开发模拟回复（未调用 Qwen）；记录结果以结构化记录卡片为准。',actions=actions,query=query)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18881)
    parser.add_argument('--static-dir',type=Path,help='Gemini worktree static directory, served on the same origin')
    parser.add_argument('--username',default='assistant-dev')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Use an isolated port between 1024 and 65535')
    password=getpass.getpass('Choose a development login password (at least 14 characters): ')
    if len(password)<14:parser.error('Password too short')
    data=ROOT/'data'/'assistant-dev'
    # No .env loading, no model server launch, no audio hardware, no production DB.
    os.environ.update(BMON_APP_DIR=str(ROOT),BMON_DATA_DIR=str(data),BMON_MEDIA_MOUNT=str(data/'media'),BMON_MEDIA_DIR=str(data/'media'),
        BMON_DEMO='true',BMON_ASSISTANT_ENABLED='true',BMON_VLM_ENABLED='false',BMON_MODEL_ENABLED='false',BMON_VOICE_ENABLED='false',
        BMON_PLAYBACK_BACKEND='silent',BMON_PERSON_TEST_MODE='false',BMON_HOST='127.0.0.1',BMON_PORT=str(args.port),BMON_VLM_SERVICE_URL='http://127.0.0.1:8010')
    config=Config.from_env()
    if args.static_dir:config=replace(config,static_dir=args.static_dir.resolve())
    app=BabyMonitorApp(config)
    if app.assistant is None:raise SystemExit('Assistant development database unavailable')
    app.assistant.model=SyntheticAssistantModel()
    origin=f'http://127.0.0.1:{args.port}'
    auth=AuthConfig(args.username,make_password_hash(password),origin)
    server=MonitorServer(('127.0.0.1',args.port),Handler,app,AccessStateStore(data/'access.json'),auth)
    app.start()
    print(f'SYNTHETIC DEVELOPMENT ONLY: {origin}; isolated data: {data}')
    try:server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:pass
    finally:app.stop();server.server_close()


if __name__=='__main__':main()
