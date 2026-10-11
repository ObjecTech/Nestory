"""Security regression tests. TEST_ONLY values: Do not use in production."""
import io
import json
import http.client
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from PIL import Image
from access_control import AccessStateStore
from baby_monitor.app import EventBus
from baby_monitor.camera import Camera
from baby_monitor.database import Database
from security import AuthConfig, Sessions, make_password_hash, public_payload
from server import MonitorServer, Handler

TEST_ONLY_PASSWORD = 'TEST_ONLY_PASSWORD_DO_NOT_USE_IN_PRODUCTION'

class HttpSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = make_password_hash(TEST_ONLY_PASSWORD)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        media = root / 'media'
        media.mkdir()
        self.db = Database(root / 'db')
        self.camera = Camera('synthetic', 16, 16, 1)
        output = io.BytesIO()
        Image.new('RGB', (16, 16), 'green').save(output, 'JPEG')
        self.camera._accept_jpeg(output.getvalue())
        self.app = SimpleNamespace(
            database=self.db, camera=self.camera, events=EventBus(),
            config=SimpleNamespace(media_dir=media, static_dir=Path('static').resolve()),
            status=lambda: {'camera': {'error': '/internal/example'}, 'storage': {'path': '/internal/example', 'mounted': True}},
        )
        config = AuthConfig('TEST_ONLY_OWNER', self.password_hash, 'http://127.0.0.1')
        self.server = MonitorServer(('127.0.0.1', 0), Handler, self.app, AccessStateStore(root / 'access.json'), config)
        self.port = self.server.server_port
        self.origin = f'http://127.0.0.1:{self.port}'
        self.server.auth.config = replace(config, origin=self.origin)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.token, self.csrf = self.server.auth.login('TEST_ONLY_OWNER', TEST_ONLY_PASSWORD)

    def tearDown(self):
        self.server.close_streams()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.db.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, authenticated=True, csrf=True, origin=None):
        headers = {'Origin': origin or self.origin}
        if authenticated:
            headers['Cookie'] = 'bmon_session=' + self.token
        if csrf:
            headers['X-CSRF-Token'] = self.csrf
        if isinstance(body, dict):
            body = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def test_tailscale_owner_auto_session_and_reject_other_identity(self):
        self.server.tailscale_user = 'owner@example.invalid'
        self.server.lan_subnet = ''
        self.server.auth.config = replace(self.server.auth.config, origin='https://monitor.example.invalid')
        for identity, expected in [(None, 403), ('other@example.invalid', 403),
                                   ('owner@example.invalid', 303)]:
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
            headers = {'Host': 'monitor.example.invalid'}
            if identity:
                headers['Tailscale-User-Login'] = identity
            connection.request('GET', '/', headers=headers)
            response = connection.getresponse()
            self.assertEqual(response.status, expected)
            if expected == 303:
                cookies = [v for k, v in response.getheaders() if k.lower() == 'set-cookie']
                self.assertEqual(len(cookies), 2)
                self.assertTrue(all('Secure' in c for c in cookies))
            response.read()
            connection.close()

    def test_lan_listener_rejects_outside_subnet_even_with_identity_header(self):
        self.server.lan_subnet = '192.168.31.0/24'
        self.server.auto_login = True
        self.assertEqual(self.request('GET', '/', authenticated=False)[0], 403)

    def test_local_preview_explicit_opt_in(self):
        with patch.dict('os.environ', {'BMON_LOCAL_PREVIEW_NO_LOGIN': 'true'}):
            status, headers, _ = self.request('GET', '/', authenticated=False)
            self.assertEqual(status, 303)
            self.assertEqual(headers['Location'], '/')
            self.assertIn('bmon_csrf=', headers['Set-Cookie'])
            self.assertEqual(self.request('GET', '/api/status', authenticated=False)[0], 401)

    def test_local_preview_rejects_nonloopback_origin(self):
        with patch.dict('os.environ', {'BMON_LOCAL_PREVIEW_NO_LOGIN': 'true'}):
            self.server.auth.config = replace(self.server.auth.config, origin='https://example.invalid')
            self.assertEqual(self.request('GET', '/', authenticated=False)[0], 421)

    def test_loopback_cannot_bypass_authentication(self):
        for path in ['/api/status', '/snapshot.jpg', '/stream.mjpg', '/api/events', '/media/a.jpg', '/api/photos/1/original', '/api/access']:
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path, authenticated=False)[0], 401)
        self.assertEqual(self.request('GET', '/', authenticated=False)[0], 303)

    def test_login_sets_http_only_session_and_csrf_cookie(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        connection.request('POST', '/login', urlencode({'username':'TEST_ONLY_OWNER', 'password':TEST_ONLY_PASSWORD}), {'Origin':self.origin, 'Content-Type':'application/x-www-form-urlencoded'})
        response = connection.getresponse()
        self.assertEqual(response.status, 303)
        cookies = [value for key,value in response.getheaders() if key == 'Set-Cookie']
        self.assertTrue(any('bmon_session=' in x and 'HttpOnly' in x and 'SameSite=Strict' in x for x in cookies))
        response.read();connection.close()

    def test_csrf_and_origin_are_required_for_every_mutation(self):
        body = {'key':'lan_enabled','enabled':False}
        self.assertEqual(self.request('POST','/api/access',body,csrf=False)[0],403)
        self.assertEqual(self.request('POST','/api/access',body,origin='https://example.invalid')[0],403)
        self.assertTrue(self.server.access_store.read()['lan_enabled'])

    def test_disable_blocks_data_but_authenticated_owner_can_restore(self):
        self.assertEqual(self.request('POST','/api/access',{'key':'lan_enabled','enabled':False})[0],200)
        self.assertEqual(self.request('GET','/snapshot.jpg')[0],403)
        self.assertEqual(self.request('POST','/api/access',{'key':'lan_enabled','enabled':True},authenticated=False)[0],401)
        self.assertEqual(self.request('POST','/api/access',{'key':'lan_enabled','enabled':True})[0],200)
        self.assertEqual(self.request('GET','/snapshot.jpg')[0],200)

    def test_logout_revokes_copied_session(self):
        self.assertEqual(self.request('POST','/logout')[0],204)
        self.assertEqual(self.request('GET','/api/status')[0],401)

    def test_disable_terminates_existing_video_and_event_sockets(self):
        for path in ['/stream.mjpg','/api/events']:
            self.request('POST','/api/access',{'key':'lan_enabled','enabled':True})
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
            connection.request('GET',path,headers={'Cookie':'bmon_session='+self.token})
            response = connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertTrue(response.readline())
            start = time.monotonic()
            self.assertEqual(self.request('POST','/api/access',{'key':'lan_enabled','enabled':False})[0],200)
            response.read()  # Must reach EOF, not merely stop producing frames.
            self.assertLess(time.monotonic()-start, 1)
            connection.close()
        self.assertFalse(self.server.active_streams)

    def add_photo(self, relative, kind='smile'):
        return self.db.add_event(kind, time.time(), 'awake', 'supine', 1, original_path=relative)

    def test_photo_id_isolation_file_type_and_trash(self):
        directory=self.app.config.media_dir/'smiles';directory.mkdir()
        image=directory/'synthetic.jpg';Image.new('RGB',(8,8)).save(image)
        photo_id=self.add_photo('smiles/synthetic.jpg')
        status,headers,data=self.request('GET',f'/api/photos/{photo_id}/original')
        self.assertEqual(status,200)
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertFalse(Image.open(io.BytesIO(data)).getexif())
        self.assertEqual(self.request('GET','/media/smiles/synthetic.jpg')[0],404)
        for relative,kind in [('smiles/synthetic.jpg','photo_trashed'),('../outside.jpg','smile'),('training/x.jpg','smile'),('smiles/script.py','smile')]:
            denied=self.add_photo(relative,kind)
            self.assertEqual(self.request('GET',f'/api/photos/{denied}/original')[0],404)
        (directory/'link.jpg').symlink_to(self.app.config.media_dir.parent/'outside.jpg')
        denied=self.add_photo('smiles/link.jpg')
        self.assertEqual(self.request('GET',f'/api/photos/{denied}/original')[0],404)

    def test_api_does_not_return_filesystem_or_raw_errors(self):
        status,_,data=self.request('GET','/api/status')
        self.assertEqual(status,200)
        self.assertNotIn(b'/internal/',data)
        self.assertNotIn(b'"path"',data)

    def test_unexpected_host_is_rejected(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
        connection.request('GET','/login',headers={'Host':'example.invalid'})
        response=connection.getresponse();self.assertEqual(response.status,421);response.read();connection.close()

class SessionTest(unittest.TestCase):
    def test_expiration_and_limiter(self):
        auth=Sessions(AuthConfig('TEST_ONLY_OWNER',make_password_hash(TEST_ONLY_PASSWORD),'http://localhost',session_seconds=0))
        token,_=auth.login('TEST_ONLY_OWNER',TEST_ONLY_PASSWORD)
        self.assertFalse(auth.valid(token))
        for _ in range(5):self.assertIsNone(auth.login('TEST_ONLY_OWNER','TEST_ONLY_WRONG_PASSWORD'))
        self.assertIsNone(auth.login('TEST_ONLY_OWNER',TEST_ONLY_PASSWORD))

    def test_redaction_is_recursive(self):
        data=public_payload({'items':[{'original_path':'private','metadata_json':'private','error':'private','speaker':{'address':'private'}}]})
        self.assertEqual(data,{'items':[{'error':'Operation unavailable'}]})
