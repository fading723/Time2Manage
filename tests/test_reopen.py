import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from time2manage.storage import Store


class ReopenTests(unittest.TestCase):
    def test_committed_records_survive_abrupt_process_exit_and_main_file_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.sqlite3'
            code = '''import os,sys
from datetime import datetime,timedelta
from time2manage.storage import Store
s=Store(sys.argv[1])
t=datetime(2026,10,4,12,0,1)
s.record(t,t+timedelta(seconds=75),1,True)
s.add_journal('重启记录',t)
s.save_app('测试软件','persist.exe')
s.set_setting('test_setting','retained')
os._exit(0)
'''
            subprocess.run([sys.executable, '-c', code, str(path)], check=True)
            copy_path = Path(directory) / 'copied.sqlite3'
            shutil.copy2(path, copy_path)
            for candidate in (path, copy_path):
                store = Store(candidate)
                data = store.day_data('2026-10-04')
                self.assertEqual(data['app_seconds'], 75)
                self.assertEqual(data['focus_seconds'], 75)
                self.assertEqual(data['active_seconds'], 75)
                self.assertEqual(data['journals'][0]['content'], '重启记录')
                self.assertIn('测试软件', [r[1] for r in store.apps()])
                self.assertEqual(store.setting('test_setting'), 'retained')
                self.assertEqual(store.db.execute('PRAGMA quick_check').fetchall(), [('ok',)])
                store.close()

    def test_deletion_and_rename_apply_to_all_current_statistics_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.sqlite3'
            store = Store(path)
            t = datetime(2026,10,4,12)
            store.record(t,t+timedelta(seconds=60),1)
            store.save_app('新名称','msedge.exe',1)
            self.assertEqual(store.intervals('2026-10-04')[0]['name'], '新名称')
            store.delete_app(1)
            store.close()
            store = Store(path)
            self.assertEqual(store.day_data('2026-10-04')['app_seconds'], 0)
            self.assertEqual(store.intervals('2026-10-04'), [])
            self.assertEqual(store.report('2026-10-04','2026-10-04')[0], [])
            self.assertEqual(len(store.intervals('2026-10-04',include_removed=True)), 1)
            store.close()

    def test_existing_wal_database_migrates_without_losing_committed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.sqlite3'
            store = Store(path)
            store.db.execute('PRAGMA journal_mode=WAL')
            store.record(datetime(2026,10,4,12),datetime(2026,10,4,12,1),1)
            store.close()
            store = Store(path)
            self.assertEqual(store.day_data('2026-10-04')['app_seconds'],60)
            self.assertEqual(store.db.execute('PRAGMA journal_mode').fetchone()[0],'delete')
            store.close()
