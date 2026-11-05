"""Recovery rehearsal with disposable local HTTP processes; no Jetson or GPU."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'maintenance/assistant-maintenance.py'
spec=importlib.util.spec_from_file_location('maintenance',SCRIPT)
maintenance=importlib.util.module_from_spec(spec); spec.loader.exec_module(maintenance)

WORKER='''import json,signal,sys,time
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self):
  if self.path=='/login':
   self.send_response(303); self.send_header('Location','/'); self.send_header('Set-Cookie','session=test; Secure'); self.end_headers(); return
  body=json.dumps({'ok':True,'scheduler':{'policy':'chat_first_latest_vision_120s'}}).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
server=HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
try: server.serve_forever(.05)
except KeyboardInterrupt: pass
finally: server.server_close()
'''


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.worker=self.root/'worker.py'; self.worker.write_text(WORKER)
        self.state_path=self.root/'state.json'
        profiles={}
        ports={'model':port(),'app':port()}
        while ports['model']==ports['app']: ports['app']=port()
        for profile in ['old','new']:
            profiles[profile]={role:{'argv':[sys.executable,'-B',str(self.worker),str(value)],
                'cwd':str(self.root),'env':dict(os.environ),
                'health':{'url':f'http://127.0.0.1:{value}/'+('health' if role=='model' else 'login'),'model':role=='model'}}
                for role,value in ports.items()}
        maintenance.save(self.state_path,dict(profiles=profiles,running={'app':None,'model':None},
            phase='prepared',stop_timeout=2,ready_timeout=2))
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        state=maintenance.read(self.state_path)
        for role in ['app','model']:
            record=state['running'].get(role)
            if maintenance.alive(record):
                os.kill(record['pid'],__import__('signal').SIGINT)
                end=time.monotonic()+3
                while maintenance.alive(record) and time.monotonic()<end: time.sleep(.05)
        for pid,process in list(maintenance.CHILDREN.items()):
            process.wait(timeout=3)
            maintenance.CHILDREN.pop(pid)

    def test_detached_switch_and_one_command_restore_are_really_rehearsed(self):
        maintenance.Maintenance(self.state_path).transition('old')
        for action,profile in [('switch','new'),('restore','old')]:
            previous=maintenance.read(self.state_path)['running']['model']['pid']
            command=[sys.executable,'-B',str(SCRIPT),action,'--state',str(self.state_path)]
            if action=='switch': command.append('--maintenance-started')
            result=subprocess.run(command,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Detached operation PID',result.stdout)
            end=time.monotonic()+8
            while time.monotonic()<end:
                state=maintenance.read(self.state_path)
                if state['phase']==profile+'_running' and state['running']['model']['pid']!=previous: break
                time.sleep(.1)
            self.assertEqual(state['phase'],profile+'_running')
            self.assertNotEqual(state['running']['model']['pid'],previous)
            for role in ['model','app']:
                self.assertTrue(maintenance.alive(state['running'][role]))
                self.assertTrue(maintenance.health(state['profiles'][profile][role]['health']))
        # Recovery is idempotent once the original services are healthy.
        before=maintenance.read(self.state_path)['running']
        maintenance.Maintenance(self.state_path).transition('old')
        self.assertEqual(maintenance.read(self.state_path)['running'],before)

    def test_failed_candidate_recovers_old_profile(self):
        runner=maintenance.Maintenance(self.state_path); runner.transition('old')
        runner.state['profiles']['new']['app']['argv']=[sys.executable,'-c','raise SystemExit(7)']
        runner.persist('prepared')
        with self.assertRaises(RuntimeError): runner.execute('switch')
        self.assertEqual(runner.state['phase'],'old_running')
        self.assertTrue(all(maintenance.alive(runner.state['running'][role]) for role in ['app','model']))

    def test_model_that_will_not_exit_is_never_killed_or_replaced(self):
        runner=maintenance.Maintenance(self.state_path)
        runner.state['stop_timeout']=0
        runner.state['running']['model']={'pid':54321,'identity':'fake','profile':'new'}
        with patch.object(maintenance,'alive',side_effect=lambda r:bool(r)), patch.object(maintenance.os,'kill') as kill, \
                patch.object(runner,'launch') as launch:
            with self.assertRaisesRegex(RuntimeError,'no force-kill'): runner.execute('switch')
        kill.assert_called_once_with(54321,__import__('signal').SIGINT)
        launch.assert_not_called()
        self.assertEqual(runner.state['phase'],'blocked_model_did_not_exit')
        runner.state['running']['model']=None; runner.persist('test_cleanup')

    def test_no_start_permission_and_occupied_port_stop_before_launch(self):
        result=subprocess.run([sys.executable,'-B',str(SCRIPT),'switch','--state',str(self.state_path)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        runner=maintenance.Maintenance(self.state_path)
        with patch.object(maintenance,'free_port',side_effect=RuntimeError('occupied')), patch.object(subprocess,'Popen') as launch:
            with self.assertRaises(RuntimeError): runner.transition('new')
        launch.assert_not_called()
