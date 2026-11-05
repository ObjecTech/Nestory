"""Smoke the isolated integration launcher without cameras, GPU or saved credentials."""
import http.client
import importlib.util
import io
import json
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class DevLauncherTest(unittest.TestCase):
    def test_isolated_mock_launcher_and_same_origin_api(self):
        spec=importlib.util.spec_from_file_location('assistant_dev_launcher',Path('scripts/run-assistant-dev.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        password='TEST_ONLY_DEVELOPMENT_PASSWORD';tested=[]
        class SmokeServer(module.MonitorServer):
            def serve_forever(self,poll_interval=.1):
                thread=threading.Thread(target=super().serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
                try:
                    token,csrf=self.auth.login('assistant-dev',password)
                    headers={'Cookie':'bmon_session='+token,'X-CSRF-Token':csrf,'Origin':self.auth.config.origin,'Content-Type':'application/json'}
                    connection=http.client.HTTPConnection('127.0.0.1',self.server_port,timeout=2)
                    connection.request('POST','/api/assistant/conversations',body=json.dumps({'scope':'demo'}),headers=headers)
                    response=connection.getresponse();conversation=json.loads(response.read())['conversation'];tested.append(response.status)
                    path='/api/assistant/conversations/'+conversation['id']+'/messages?scope=demo'
                    connection.request('POST',path,body=json.dumps({'content':'你好','client_message_id':'dev-smoke'}),headers=headers)
                    response=connection.getresponse();accepted=json.loads(response.read());tested.append(response.status)
                    self.app.assistant.queue.join()
                    connection.request('GET',accepted['poll_url'],headers=headers);response=connection.getresponse();result=json.loads(response.read());tested.append(result['status']);tested.append(result['assistant_message']['content']);connection.close()
                finally:self.shutdown();thread.join(1)
        with tempfile.TemporaryDirectory() as d:
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            output=io.StringIO()
            with patch.object(module,'ROOT',Path(d)),patch.object(module,'MonitorServer',SmokeServer),patch.object(module.getpass,'getpass',return_value=password),patch('sys.argv',['dev','--port',str(port),'--static-dir',str(Path('static').resolve())]),patch.dict('os.environ',{},clear=False),redirect_stdout(output):module.main()
            self.assertEqual(tested[:3],[201,202,'completed']);self.assertIn('未调用 Qwen',tested[3]);self.assertIn('SYNTHETIC DEVELOPMENT ONLY',output.getvalue())
            self.assertTrue((Path(d)/'data/assistant-dev/assistant.db').is_file());self.assertFalse((Path(d)/'.env').exists())
