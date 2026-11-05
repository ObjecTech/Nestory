#!/usr/bin/env python3
"""Small, private, detached switch/restore helper. Prepare only after start approval.

One-command recovery: python3 maintenance/assistant-maintenance.py restore --state PATH
The command uses nohup and logs to PATH's private directory. No SIGKILL is used.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

CHILDREN = {}

def identity(pid):
    """PID reuse guard, without printing process arguments or environment."""
    try:
        proc=Path('/proc')/str(pid)/'stat'
        if proc.exists():
            parts=proc.read_text().rsplit(')',1)[1].split()
            return None if parts[0]=='Z' else parts[19]
        result=subprocess.run(['ps','-p',str(pid),'-o','stat=','-o','lstart='],capture_output=True,text=True)
        fields=result.stdout.strip().split(maxsplit=1)
        return fields[1] if len(fields)==2 and not fields[0].startswith('Z') else None
    except (OSError,IndexError): return None


def save(path, state):
    temp=path.with_suffix('.tmp')
    with temp.open('w') as out:
        os.chmod(temp,0o600); json.dump(state,out,indent=2)
        out.flush(); os.fsync(out.fileno())
    temp.replace(path)


def read(path):
    path=Path(path).resolve(strict=True)
    if path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077:
        raise RuntimeError('State and directory must be private (600/700)')
    return json.loads(path.read_text())


def alive(record):
    return bool(record and identity(record['pid'])==record['identity'])


def health(spec):
    url=spec['url']; parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('Health probes must use loopback')
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        request=urllib.request.Request(url,headers=spec.get('headers',{}))
        with opener.open(request,timeout=2) as response:
            raw=response.read(65536)
            if spec.get('model'):
                result=json.loads(raw)
                return result.get('ok') is True and (not spec.get('policy') or
                    result.get('scheduler',{}).get('policy')==spec['policy'])
            return response.status==200
    except urllib.error.HTTPError as exc:
        # The existing trusted Tailscale owner login sets secure cookies then
        # redirects to /. A loopback HTTP readiness probe must not follow that
        # redirect without the secure cookie and misdiagnose a login loop.
        return (not spec.get('model') and exc.code in (302,303)
                and exc.headers.get('Location') in ('/','/login'))
    except (OSError,ValueError,urllib.error.URLError): return False


def free_port(spec):
    parsed=urlsplit(spec['url'])
    with socket.socket() as sock:
        sock.settimeout(.3)
        if sock.connect_ex((parsed.hostname,parsed.port or 80))==0:
            raise RuntimeError('Port occupied; refusing to launch another service')


class Maintenance:
    def __init__(self,path):
        self.path=Path(path).resolve(); self.state=read(self.path)

    def persist(self,phase):
        self.state['phase']=phase; self.state['updated_at']=time.time()
        save(self.path,self.state)
        print(phase,flush=True)

    def stop(self,role):
        record=self.state['running'].get(role)
        if not alive(record):
            if record and record['pid'] in CHILDREN:
                CHILDREN.pop(record['pid']).wait()
            self.state['running'][role]=None; self.persist(role+'_already_stopped'); return
        # Existing app owns voice/arecord cleanup (its established fallback is
        # approved). Model gets SIGINT only, and retains its active GPU slot.
        os.kill(record['pid'],signal.SIGINT if role=='model' else signal.SIGTERM)
        deadline=time.monotonic()+self.state.get('stop_timeout',120)
        while alive(record) and time.monotonic()<deadline: time.sleep(.1)
        if alive(record):
            self.persist('blocked_'+role+'_did_not_exit')
            raise RuntimeError(role+' did not exit normally; no force-kill or replacement')
        if record['pid'] in CHILDREN:
            CHILDREN.pop(record['pid']).wait()
        self.state['running'][role]=None; self.persist(role+'_stopped')

    def launch(self,profile,role):
        spec=self.state['profiles'][profile][role]
        free_port(spec['health'])
        log=self.path.parent/(profile+'-'+role+'.log')
        with log.open('ab') as out:
            os.chmod(log,0o600)
            process=subprocess.Popen(spec['argv'],cwd=spec['cwd'],env=spec['env'],
                stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        CHILDREN[process.pid]=process
        token=identity(process.pid)
        if token is None:
            CHILDREN.pop(process.pid).wait()
            raise RuntimeError(role+' exited during launch')
        self.state['running'][role]={'pid':process.pid,'identity':token,'profile':profile}
        self.persist(profile+'_'+role+'_starting')
        deadline=time.monotonic()+self.state.get('ready_timeout',90)
        while alive(self.state['running'][role]) and time.monotonic()<deadline:
            if health(spec['health']): return
            time.sleep(.2)
        raise RuntimeError(role+' readiness failed; inspect the private log')

    def transition(self,profile):
        running=self.state['running']; specs=self.state['profiles'][profile]
        if all(alive(running.get(role)) and running[role]['profile']==profile and health(specs[role]['health'])
               for role in ('app','model')):
            self.persist(profile+'_running'); return
        self.stop('app')
        self.stop('model')
        # Even if a recorded PID vanished, an unknown listener blocks startup.
        for role in ('model','app'): free_port(specs[role]['health'])
        self.launch(profile,'model'); self.launch(profile,'app')
        self.persist(profile+'_running')

    def execute(self,action):
        try:
            self.transition('new' if action=='switch' else 'old')
        except Exception:
            if self.state['phase'].startswith('blocked_'):
                # An owner that did not exit must be inspected, not signalled
                # repeatedly or replaced by another model during auto-recovery.
                raise
            if action=='switch':
                print('Switch failed; attempting old profile recovery without force-kill',flush=True)
                try: self.transition('old')
                except Exception:
                    self.persist('blocked_recovery'); raise
            raise


def capture(pid,base,name):
    if sys.platform!='linux': raise RuntimeError('Real prepare requires Jetson Linux')
    proc=Path('/proc')/str(pid)
    if proc.joinpath('cwd').resolve()!=base: raise RuntimeError('Unexpected production cwd')
    argv=proc.joinpath('cmdline').read_bytes().decode().strip('\0').split('\0')
    if not argv or Path(argv[-1]).name!=name: raise RuntimeError('Unexpected service command')
    env=dict(item.split('=',1) for item in proc.joinpath('environ').read_bytes().decode().split('\0') if '=' in item)
    return {'argv':argv,'cwd':str(base),'env':env}


def prepare(args):
    if not args.maintenance_started: raise RuntimeError('Explicit human maintenance-start instruction required')
    base=args.base.resolve(strict=True); release=args.release.resolve(strict=True)
    if base==release: raise RuntimeError('Use an independent release directory')
    # No dependency installs, database access or checkout changes. The private
    # manifest captures the ORIGINAL process environment without echoing secrets.
    old={role:capture(pid,base,name) for role,pid,name in
         [('model',args.model_pid,'model_server.py'),('app',args.app_pid,'server.py')]}
    env=old['app']['env']; origin=env.get('BMON_PUBLIC_ORIGIN','')
    if not origin: raise RuntimeError('Missing original public origin')
    headers={'Host':urlsplit(origin).netloc}
    if env.get('BMON_TAILSCALE_USER'): headers['Tailscale-User-Login']=env['BMON_TAILSCALE_USER']
    old['model']['health']={'url':'http://127.0.0.1:8010/health','model':True}
    old['app']['health']={'url':'http://127.0.0.1:8080/login','headers':headers}
    new=json.loads(json.dumps(old))
    for role,name in [('model','model_server.py'),('app','server.py')]:
        if not (release/name).is_file(): raise RuntimeError('Candidate source missing')
        new[role]['argv'][-1]=str(release/name)
        new[role]['env'].update(BMON_ASSISTANT_ENABLED='true',BMON_APP_DIR=str(base),
            BMON_STATIC_DIR=str(release/'static'),BMON_VLM_MODEL_PATH=str(base/'models/Qwen3-VL-2B-Instruct'),
            BMON_VLM_HOST='127.0.0.1',BMON_VLM_PORT='8010',BMON_VLM_SERVICE_URL='http://127.0.0.1:8010')
        new[role]['env'].pop('BMON_ASSISTANT_ALLOW_UNPROFILED_CHAT',None)
    new['model']['health']['policy']='chat_first_latest_vision_120s'
    state={'profiles':{'old':old,'new':new},'running':{},'phase':'prepared',
           'stop_timeout':120,'ready_timeout':90}
    for role,pid in [('model',args.model_pid),('app',args.app_pid)]:
        token=identity(pid)
        if token is None: raise RuntimeError('Original process disappeared')
        state['running'][role]={'pid':pid,'identity':token,'profile':'old'}
    directory=args.state.absolute().parent
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.stat().st_mode & 0o077: raise RuntimeError('State directory must be private')
    if args.state.exists(): raise RuntimeError('Do not overwrite a recovery manifest')
    save(args.state,state)
    print('Prepared private recovery manifest; no services stopped')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','switch','restore','status','_run'])
    parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--base',type=Path); parser.add_argument('--release',type=Path)
    parser.add_argument('--model-pid',type=int); parser.add_argument('--app-pid',type=int)
    parser.add_argument('--maintenance-started',action='store_true')
    parser.add_argument('--action',choices=['switch','restore'])
    args=parser.parse_args()
    if args.command=='prepare':
        if not all([args.base,args.release,args.model_pid,args.app_pid]): parser.error('prepare requires paths and original PIDs')
        prepare(args); return
    state=read(args.state)
    if args.command=='status':
        print(json.dumps({'phase':state['phase'],'running':{k:alive(v) for k,v in state['running'].items()}})); return
    if args.command=='switch' and not args.maintenance_started:
        parser.error('Explicit human maintenance-start instruction required')
    if args.command=='_run':
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        with args.state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            Maintenance(args.state).execute(args.action)
        return
    log=args.state.parent/'maintenance.log'
    with log.open('ab') as out:
        os.chmod(log,0o600)
        process=subprocess.Popen([shutil.which('nohup') or 'nohup',sys.executable,'-B',str(Path(__file__).resolve()),
            '_run','--state',str(args.state.resolve()),'--action',args.command],
            stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    print('Detached operation PID:',process.pid)
    print('Status: python3',Path(__file__).name,'status --state',args.state)
    print('Private log:',log)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        # Never dump the private manifest/environment in an error traceback.
        print(type(exc).__name__+': '+str(exc),file=sys.stderr); raise SystemExit(1)
