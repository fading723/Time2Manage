import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from time2manage.storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_midnight_and_overlap(self):
        app_id = self.store.apps()[0][0]
        self.store.record(datetime(2026, 10, 3, 23, 59, 58), datetime(2026, 10, 4, 0, 0, 3), app_id, True)
        _, usage, focus = self.store.report('2026-10-03', '2026-10-04')
        self.assertEqual(usage, {'2026-10-03': 2.0, '2026-10-04': 3.0})
        self.assertEqual(usage, focus)

    def test_deleted_app_history_not_reassigned(self):
        app_id = self.store.apps()[-1][0]
        self.store.record(datetime(2026, 10, 4, 12), datetime(2026, 10, 4, 12, 1), app_id)
        self.store.delete_app(app_id)
        self.store.save_app('测试软件', 'test.exe')
        self.assertGreater(self.store.apps()[-1][0], app_id)
        apps, _, _ = self.store.report('2026-10-04', '2026-10-04')
        self.assertEqual(apps, [])
        apps, _, _ = self.store.report('2026-10-04', '2026-10-04', include_removed=True)
        self.assertEqual(apps, [(f'已删除软件 #{app_id}', 60.0)])

    def test_duplicate_process_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_app('重复 Edge', 'MSEDGE.EXE')

    def test_persistence_and_backup(self):
        self.store.record(datetime(2026, 10, 4), datetime(2026, 10, 4, 0, 1), focus=True)
        backup = Path(self.tmp.name) / 'backup.sqlite3'
        self.store.backup(backup)
        copy = Store(backup)
        self.assertEqual(copy.report('2026-10-04', '2026-10-04')[2], {'2026-10-04': 60.0})
        copy.db.close()


if __name__ == '__main__':
    unittest.main()
