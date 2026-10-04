import ctypes
from ctypes import wintypes
import queue
import unittest
from time2manage.windows import Hotkey


class HotkeyTests(unittest.TestCase):
    def test_two_registered_keys_and_message_routes(self):
        events = queue.Queue()
        hotkey = Hotkey(events)
        try:
            hotkey.configure('Ctrl+Alt+Shift+F10', 'focus')
            hotkey.configure('Ctrl+Alt+Shift+F11', 'journal')
            first, second = events.get(timeout=3), events.get(timeout=3)
            if first[0] != 'hotkey_ok' or second[0] != 'hotkey_ok':
                self.skipTest('测试组合被其他应用占用')
            user = ctypes.WinDLL('user32',use_last_error=True)
            user.PostThreadMessageW.argtypes = [wintypes.DWORD,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            self.assertTrue(user.PostThreadMessageW(hotkey.thread.native_id,0x312,1,0))
            self.assertEqual(events.get(timeout=3),('toggle',None))
            self.assertTrue(user.PostThreadMessageW(hotkey.thread.native_id,0x312,2,0))
            self.assertEqual(events.get(timeout=3),('journal',None))
            hotkey.configure('Ctrl+Alt+Shift+F10','journal')
            self.assertEqual(events.get(timeout=3),('hotkey_error',('journal','Ctrl+Alt+Shift+F10')))
        finally:
            hotkey.commands.put(None)
            hotkey.thread.join(timeout=3)
            self.assertFalse(hotkey.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
