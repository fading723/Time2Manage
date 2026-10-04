import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from time2manage.tracker import Tracker
from time2manage.storage import Store
from time2manage.windows import parse_hotkey


class Value:
    def set(self, value):
        self.value = value


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Tracker.__new__(Tracker)
        self.app.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        self.app.refresh_map()
        self.app.focus = False
        self.app.focus_seconds = 0
        self.app.paused = False
        self.app.idle_limit = 60
        self.app.previous_app = 1
        self.app.last_tick = 10
        self.app.last_wall = datetime(2026, 10, 4, 12)
        self.app.status = Value()
        self.app.session_label = Value()

    def tearDown(self):
        self.app.store.db.close()
        self.tmp.cleanup()

    def account(self, process, idle, tick=10.5):
        wall = self.app.last_wall + __import__('datetime').timedelta(seconds=tick-self.app.last_tick)
        with patch('time2manage.tracker.foreground', return_value=(process, idle)), patch('time2manage.tracker.time.perf_counter', return_value=tick), patch('time2manage.tracker.datetime') as date_mock:
            date_mock.now.return_value = wall
            self.app.account()
        return self.app.store.db.execute('SELECT SUM(seconds) FROM usage').fetchone()[0] or 0

    def test_foreground_counts(self):
        self.assertEqual(self.account('msedge.exe', 0), .5)

    def test_untracked_foreground_does_not_count_background_edge(self):
        self.assertEqual(self.account('other.exe', 0), 0)

    def test_idle_and_locked_do_not_count(self):
        self.assertEqual(self.account('msedge.exe', 61), 0)
        self.assertEqual(self.account(None, None, 11), 0)

    def test_suspend_gap_not_counted_even_for_focus(self):
        self.app.focus = True
        self.assertEqual(self.account('msedge.exe', 0, 600), 0)
        self.assertEqual(self.app.focus_seconds, 0)

    def test_manual_focus_counts_without_tracked_app(self):
        self.app.focus = True
        self.assertEqual(self.account('other.exe', 100), 0)
        self.assertEqual(self.app.focus_seconds, .5)

    def test_paused_tracking(self):
        self.app.paused = True
        self.assertEqual(self.account('msedge.exe', 0), 0)

    def test_hotkey_validation(self):
        self.assertEqual(parse_hotkey('Ctrl+Alt+F'), (0x4003, ord('F')))
        self.assertEqual(parse_hotkey('Ctrl+Shift+F8'), (0x4006, 0x77))
        with self.assertRaises(ValueError):
            parse_hotkey('F')


if __name__ == '__main__':
    unittest.main()
