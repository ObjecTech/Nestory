#!/usr/bin/env python3
"""Prepare only, or explicitly run a private, single-worker maintenance harness."""
from __future__ import annotations
import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path
import select
import signal
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from maintenance.assistant_test_harness import (Harness,prepare_run,validate_run,
    isolated_environment,load_worker,real_preflight,port_free,ResourceSampler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare',help='Generate ONLY local synthetic fixtures/cases; no GPU or listener')
    prepare.add_argument('--parent',type=Path,default=Path.home()/'nestory-m2b')
    serve=commands.add_parser('serve',help='Default-deny real execution until a separately authorized maintenance start')
    serve.add_argument('--run-dir',type=Path,required=True)
    mode=serve.add_mutually_exclusive_group(required=True)
    mode.add_argument('--mock',action='store_true',help='Explicit CPU-only model double; NOT real Qwen acceptance')
    mode.add_argument('--real',action='store_true')
    serve.add_argument('--maintenance-started',action='store_true',help='Only after the human gives a new start instruction')
    serve.add_argument('--weights',type=Path)
    serve.add_argument('--model-port',type=int,default=18010)
    serve.add_argument('--web-port',type=int,default=18881)
    serve.add_argument('--minutes',type=float,default=60,help='Stop admission at this limit, then drain; no force-kill')
    args=parser.parse_args()
    if args.command=='prepare':
        print(prepare_run(args.parent)); return
    if not 0<args.minutes<=90: parser.error('Maintenance limit must be >0 and <=90 minutes')
    if not (1024<=args.model_port<=65535 and 1024<=args.web_port<=65535) or args.model_port==args.web_port:
        parser.error('Use two distinct isolated ports')
    if args.model_port in (8010,8080) or args.web_port in (8010,8080): parser.error('Production ports forbidden')
    if args.real and not args.maintenance_started:
        parser.error('A new explicit human maintenance-start instruction is required; B0 is preparation only')
    run=validate_run(args.run_dir)
    if args.real:
        if args.weights is None: parser.error('Existing weights path required')
        real_preflight(run,args.weights,args.maintenance_started,args.model_port,args.web_port)
    else:
        for port in (args.model_port,args.web_port): port_free(port)
    password=getpass.getpass('Choose an isolated test login password (at least 14 characters): ')
    if len(password)<14: parser.error('Password too short')
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    harness=None
    timer=None
    sampler=None
    with contextlib.ExitStack() as stack:
        if args.real:
            import fcntl
            # Dedicated cross-run lock; never locks or changes a production file.
            lock_path=run.parent/'real-model.lock'
            fd=os.open(lock_path,os.O_RDWR|os.O_CREAT,0o600)
            lock=stack.enter_context(os.fdopen(fd,'w'))
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise SystemExit('Another maintenance model owns the real-mode lock')
        stack.enter_context(isolated_environment(run,args.model_port,args.web_port,args.weights))
        try:
            if args.real: real_preflight(run,args.weights,True,args.model_port,args.web_port)
            module=load_worker(mock=args.mock)
            harness=Harness(run,module,password,args.model_port,args.web_port,'real_qwen2b' if args.real else 'mock')
            if args.real:
                # Bind test listeners first, then recheck the OLD ports/processes
                # immediately before the only real model load. No pip/install.
                for port in (8010,8080): port_free(port)
                module.ENGINE.load()
                harness.capture_test_decoder()
                harness.journal.emit('real_model_loaded',model='Qwen3-VL-2B-Instruct',load_seconds=module.ENGINE.load_seconds)
            harness.start()
            def admission_deadline():
                harness.gate.stopping()
                module.ENGINE.scheduler.stop_accepting()
                stop.set()
                harness.journal.emit('maintenance_deadline',new_admission=False,force_kill=False)
            timer=threading.Timer(args.minutes*60,admission_deadline)
            timer.daemon=True; timer.start()
            sampler=ResourceSampler(harness,admission_deadline,real=args.real)
            sampler.start()
            print(('MOCK ONLY' if args.mock else 'MAINTENANCE REAL QWEN2B')+f': http://127.0.0.1:{args.web_port}/ ; user assistant-test')
            print('Commands: vision empty|gray|occluded [adult]; functional empty|gray|occluded; status; cases; quit')
            print('Periodic mixed-load is BLOCKED. Functional quiet lease is 120s; expired/failed admission stays closed.')
            deadline=time.monotonic()+args.minutes*60
            while not stop.is_set() and not harness.journal.failed and time.monotonic()<deadline:
                if not select.select([sys.stdin],[],[],.2)[0]: continue
                line=sys.stdin.readline()
                if not line: break
                words=line.strip().split()
                if not words: continue
                if words[0]=='quit': break
                try:
                    if words[0] in ('vision','functional') and 2<=len(words)<=3:
                        harness.fixture(words[1],functional=words[0]=='functional',adult=len(words)==3 and words[2]=='adult')
                    elif words==['status']:
                        print(json.dumps(dict(phase=harness.gate.phase,scheduler=module.ENGINE.scheduler.metrics()),ensure_ascii=False))
                    elif words==['cases']:
                        from maintenance.assistant_test_cases import run_cases
                        run_cases(harness,password)
                    else: print('Unknown command; mixed-load commands are intentionally unavailable')
                except Exception as exc:
                    # No arbitrary exception text: could include private config/path.
                    harness.journal.emit('command_failed',command=words[0],error_type=type(exc).__name__)
                    print('Command failed; see private synthetic-test journal; no admission fallback')
        finally:
            if timer: timer.cancel(); timer.join()
            if sampler:
                while not sampler.close():
                    print('Read-only sampler still running; waiting without force-kill',file=sys.stderr)
                    time.sleep(1)
            if harness:
                while not harness.stop(timeout=5):
                    print('DRAIN BLOCKED: do not restore/load another model; waiting without force-kill',file=sys.stderr)
                    stop.wait(1)
    print('Harness stopped. Production restore is a separate, explicitly checked manual step.')


if __name__=='__main__':
    main()
