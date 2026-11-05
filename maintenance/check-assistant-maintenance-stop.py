#!/usr/bin/env python3
"""Read-only stop preflight. Sends NO signal; never reads SQLite or media rows.

The owner now accepts existing voice/arecord cleanup fallbacks. Audio children
are inventoried, not treated as a prerequisite to wait until night time.
Even PASS is a precondition snapshot, not authorization or proof of GPU drain.
"""
import argparse
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from baby_monitor.voice_control import is_within_listen_schedule


def inspect(project):
    project=project.resolve(); processes={}; apps=[]; models=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            value=(proc/'stat').read_text(); fields=value[value.rfind(')')+2:].split()
            pid=int(proc.name)
            processes[pid]={'pid':pid,'ppid':int(fields[1]),'start_ticks':fields[19],
                            'state':fields[0]}
            if (proc/'cwd').resolve()!=project: continue
            args=(proc/'cmdline').read_bytes().split(b'\0')
            names=[Path(arg.decode(errors='replace')).name for arg in args if arg]
            if 'server.py' in names: apps.append(pid)
            if 'model_server.py' in names: models.append(pid)
        except (OSError,ValueError): continue
    if len(apps)!=1 or len(models)!=1:
        return dict(can_request_app_stop=False,reason='Expected exactly one original app and one original model')
    app=apps[0]; descendants=set(); frontier=[app]
    while frontier:
        children=[pid for pid,info in processes.items() if info['ppid'] in frontier and pid not in descendants]
        descendants.update(children); frontier=children
    # Read only selected configuration. Full environment values never leave this process.
    env=dict(item.split(b'=',1) for item in Path(f'/proc/{app}/environ').read_bytes().split(b'\0') if b'=' in item)
    def setting(name,default): return env.get(name.encode(),default.encode()).decode()
    data=Path(setting('BMON_DATA_DIR',str(project/'data')))
    enabled=setting('BMON_VOICE_ENABLED','true').lower() in ('1','true','yes','on')
    saved=data/'voice_control.json'
    if saved.is_file():
        value=json.loads(saved.read_text()).get('enabled')
        if type(value) is bool: enabled=value
    start=setting('BMON_VOICE_SCHEDULE_START','07:00'); end=setting('BMON_VOICE_SCHEDULE_END','23:00')
    now=datetime.now(ZoneInfo(setting('BMON_VOICE_TIMEZONE','Asia/Shanghai')))
    may_start=enabled and any(is_within_listen_schedule(now+timedelta(minutes=n),start,end) for n in (0,5))
    safe=processes[app]['state'] not in ('D','T','Z')
    return dict(utc=datetime.now(ZoneInfo('UTC')).isoformat(),can_request_app_stop=safe,
        app=processes[app],model=processes[models[0]],children=[processes[p] for p in sorted(descendants)],
        voice_enabled=enabled,voice_schedule={'start':start,'end':end,'timezone':str(now.tzinfo)},
        voice_may_start=may_start,reason='App can receive normal shutdown; approved legacy audio cleanup applies' if safe else
        'BLOCKED: inspect non-running app state before shutdown',
        signals_sent=False,gpu_drain_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production-dir',type=Path,default=Path.home()/'baby-monitor-public')
    args=parser.parse_args()
    result=inspect(args.production_dir)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['can_request_app_stop'] else 2)
