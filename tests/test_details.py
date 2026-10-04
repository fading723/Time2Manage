import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time2manage.storage import Store
from time2manage.review import make_payload, complete, endpoint_url, DEFAULT_PROMPT
from time2manage.windows import protect_secret


class DetailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.start = datetime(2026, 10, 4, 9, 0, 30)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_adjacent_samples_merge_but_gaps_do_not(self):
        t = self.start
        self.store.record(t, t+timedelta(seconds=.5), 1)
        self.store.record(t+timedelta(seconds=.5), t+timedelta(seconds=1), 1)
        self.store.record(t+timedelta(seconds=4), t+timedelta(seconds=5), 1)
        spans = self.store.intervals('2026-10-04')
        self.assertEqual(len(spans), 2)
        self.assertEqual(self.store.union_seconds(spans), 2)

    def test_union_avoids_double_counting_focus_and_apps(self):
        self.store.record(self.start, self.start+timedelta(minutes=2), 1)
        self.store.record(self.start+timedelta(minutes=1), self.start+timedelta(minutes=3), 2)
        self.store.record(self.start, self.start+timedelta(minutes=3), focus=True)
        day = self.store.day_data('2026-10-04')
        self.assertEqual(day['active_seconds'], 180)
        self.assertEqual(day['app_seconds'], 240)
        self.assertEqual(day['focus_seconds'], 180)

    def test_minute_accuracy_and_journal_seconds(self):
        self.store.record(self.start, self.start+timedelta(minutes=1), 1, True)
        created = datetime(2026, 10, 4, 9, 0, 47, 923123)
        self.store.add_journal('完成了时间轴', created)
        payload = make_payload(self.store, '2026-10-04')
        self.assertEqual([m['time'] for m in payload['minute_usage']], ['09:00', '09:01'])
        self.assertEqual(payload['minute_usage'][0]['seconds_by_activity']['Edge 浏览器'], 30)
        self.assertEqual(payload['minute_usage'][1]['seconds_by_activity']['Edge 浏览器'], 30)
        self.assertEqual(payload['journals'][0]['time'], '2026-10-04T09:00:47')
        self.assertEqual(payload['journals'][0]['content'], '完成了时间轴')

    def test_midnight_intervals_split_correctly(self):
        a = datetime(2026, 10, 4, 23, 59, 59)
        self.store.record(a, a+timedelta(seconds=3), 1, True)
        first, second = self.store.intervals('2026-10-04'), self.store.intervals('2026-10-05')
        self.assertEqual(self.store.union_seconds(first), 1)
        self.assertEqual(self.store.union_seconds(second), 2)

    def test_deleted_name_is_preserved_in_detail(self):
        self.store.record(self.start, self.start+timedelta(seconds=10), 1)
        self.store.delete_app(1)
        self.assertEqual(self.store.intervals('2026-10-04'), [])
        self.assertEqual(self.store.intervals('2026-10-04', include_removed=True)[0]['name'], 'Edge 浏览器')

    def test_migration_preserves_old_totals_without_fabricated_details(self):
        path = Path(self.tmp.name) / 'old.sqlite3'
        old = sqlite3.connect(path)
        old.executescript('CREATE TABLE usage(day TEXT,app_id INTEGER,seconds REAL,PRIMARY KEY(day,app_id)); INSERT INTO usage VALUES ("2026-10-01",1,3600);')
        old.close()
        migrated = Store(path)
        self.assertEqual(migrated.day_data('2026-10-01')['app_seconds'], 3600)
        self.assertEqual(migrated.intervals('2026-10-01'), [])
        migrated.db.close()

    def test_review_and_journal_persist_in_backup(self):
        self.store.add_journal('本地日记', self.start)
        payload = make_payload(self.store, '2026-10-04')
        self.store.save_review('2026-10-04','test-model','http://localhost/v1',DEFAULT_PROMPT,payload,'## 今日概览\n很清晰')
        backup = Path(self.tmp.name)/'backup.sqlite3'
        self.store.backup(backup)
        copy = Store(backup)
        self.assertEqual(copy.journals('2026-10-04')[0]['content'], '本地日记')
        self.assertEqual(copy.reviews('2026-10-04')[0]['model'], 'test-model')
        copy.db.close()

    def test_secret_encryption_round_trip(self):
        key = 'test-only-not-real-secret'
        encrypted = protect_secret(key)
        self.assertNotIn(key, encrypted)
        self.assertEqual(protect_secret(encrypted, decrypt=True), key)

    def test_codex_desktop_alias_is_tracked(self):
        from time2manage.tracker import Tracker
        tracker = Tracker(self.store)
        self.assertEqual(tracker.app_map['chatgpt.exe'][1], 'Codex')


class ApiTests(unittest.TestCase):
    def test_url_rules(self):
        self.assertEqual(endpoint_url('https://example.com/v1/'), 'https://example.com/v1/chat/completions')
        self.assertEqual(endpoint_url('http://127.0.0.1:9876/v1/chat/completions'), 'http://127.0.0.1:9876/v1/chat/completions')
        for url in ['http://example.com/v1', 'https://user:secret@example.com/v1', 'https://example.com/v1?key=secret']:
            with self.assertRaises(ValueError):
                endpoint_url(url)

    def test_actual_local_api_request_and_response(self):
        captured = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured['path'] = self.path
                captured['auth'] = self.headers.get('Authorization')
                captured['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                data = json.dumps({'choices':[{'message':{'content':'## 今日概览\n模拟接口复盘成功'}}]},ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self,*args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            result = complete(f'http://127.0.0.1:{server.server_port}/v1','test-model','test-key',DEFAULT_PROMPT,{'date':'2026-10-04','journals':[{'time':'09:00:47','content':'测试日记'}]})
            self.assertIn('模拟接口复盘成功',result)
            self.assertEqual(captured['path'],'/v1/chat/completions')
            self.assertEqual(captured['auth'],'Bearer test-key')
            self.assertEqual(captured['body']['model'],'test-model')
            self.assertIn('测试日记',captured['body']['messages'][1]['content'])
            self.assertIn('并集',captured['body']['messages'][0]['content'])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
