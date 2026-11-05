"""「陪我玩」互动记录接口：真实 HTTP、登录/CSRF 边界、幂等、范围隔离。全部为合成 Demo 数据。"""
import http.client
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from baby_monitor.assistant_database import AssistantDatabase
import test_assistant_http as base  # 只复用夹具，避免重复收集其用例


def iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds').replace('+00:00', 'Z')


class InteractionHTTPTest(unittest.TestCase):
    setUpClass = classmethod(base.AssistantHTTPTest.setUpClass.__func__)
    setUp = base.AssistantHTTPTest.setUp
    tearDown = base.AssistantHTTPTest.tearDown

    def call(self, method, path, body=None, *, csrf=True, auth=True):
        h = {'Origin': self.origin}
        if auth: h['Cookie'] = 'bmon_session=' + self.token
        if csrf: h['X-CSRF-Token'] = self.csrf
        if body is not None: body = json.dumps(body); h['Content-Type'] = 'application/json'
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3); c.request(method, path, body=body, headers=h)
        r = c.getresponse(); out = (r.status, json.loads(r.read())); c.close(); return out

    def payload(self, **changes):
        p = dict(client_id='play-1', scope='demo', activity_id='fp-01', kind='finger_play', title='小手拍拍',
                 timezone='Asia/Shanghai', started_at=iso(10), ended_at=iso(7), reaction='happy', notes='')
        p.update(changes); return p

    def today(self):
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    def test_create_replay_list_delete(self):
        status, body = self.call('POST', '/api/interactions', self.payload())
        self.assertEqual(status, 201)
        item = body['interaction']
        self.assertEqual((item['reaction'], item['duration_seconds'], body['replayed']), ('happy', 180, False))
        status, body = self.call('POST', '/api/interactions', self.payload())
        self.assertEqual((status, body['replayed'], body['interaction']['id']), (200, True, item['id']))
        self.assertEqual(self.call('POST', '/api/interactions', self.payload(reaction='calm'))[0], 409)
        status, body = self.call('GET', f'/api/interactions?scope=demo&date={self.today()}&timezone=Asia%2FShanghai')
        self.assertEqual((status, [i['id'] for i in body['items']]), (200, [item['id']]))
        self.assertEqual(self.call('GET', f'/api/interactions?scope=real&date={self.today()}')[1]['items'], [])
        self.assertEqual(self.call('DELETE', f"/api/interactions/{item['id']}?scope=real")[0], 404)
        self.assertEqual(self.call('DELETE', f"/api/interactions/{item['id']}?scope=demo")[0], 200)
        self.assertEqual(self.call('GET', f'/api/interactions?scope=demo&date={self.today()}')[1]['items'], [])

    def test_open_interaction_and_null_reaction(self):
        status, body = self.call('POST', '/api/interactions', self.payload(client_id='open', ended_at=None, reaction=None))
        self.assertEqual(status, 201)
        self.assertIsNone(body['interaction']['duration_seconds'])

    def test_validation(self):
        bad = [dict(kind='dance'), dict(reaction='angry'), dict(started_at=iso(-30), ended_at=None),
               dict(ended_at=iso(20)), dict(started_at=iso(500), ended_at=iso(0)), dict(notes='x' * 201),
               dict(client_id='含空格 id'), dict(title=''), dict(extra=1)]
        for i, change in enumerate(bad):
            change.setdefault('client_id', f'bad-{i}')
            self.assertEqual(self.call('POST', '/api/interactions', self.payload(**change))[0], 422, change)
        self.assertEqual(self.call('POST', '/api/interactions', self.payload(scope='other'))[0], 422)

    def test_shared_auth_boundary_and_methods(self):
        self.assertEqual(self.call('POST', '/api/interactions', self.payload(), csrf=False)[0], 403)
        self.assertEqual(self.call('GET', '/api/interactions?scope=demo', auth=False)[0], 401)
        self.assertEqual(self.call('PUT', '/api/interactions', self.payload())[0], 405)
        self.assertEqual(self.call('DELETE', '/api/interactions/x')[0], 400)


class InteractionMigrationTest(unittest.TestCase):
    def test_version_four_database_upgrades_to_five(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'assistant.db'
            AssistantDatabase(path).close()
            c = sqlite3.connect(path)
            c.execute('DROP TABLE interaction_events'); c.execute('DELETE FROM assistant_schema_migrations WHERE version=5')
            c.commit(); c.close()
            db = AssistantDatabase(path)
            self.assertEqual(db.connection.execute('SELECT MAX(version) FROM assistant_schema_migrations').fetchone()[0], 5)
            self.assertEqual(db.connection.execute('SELECT COUNT(*) FROM interaction_events').fetchone()[0], 0)
            db.close()


if __name__ == '__main__':
    unittest.main()
