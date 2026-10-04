import ctypes as C
from ctypes import wintypes as W
import os
import queue
import threading
import time
import base64

user32 = C.WinDLL('user32', use_last_error=True)
kernel32 = C.WinDLL('kernel32', use_last_error=True)
user32.GetForegroundWindow.restype = W.HWND
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
kernel32.CloseHandle.argtypes = [W.HANDLE]
kernel32.GetTickCount64.restype = C.c_ulonglong
user32.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
user32.OpenInputDesktop.restype = W.HANDLE
user32.CloseDesktop.argtypes = [W.HANDLE]
user32.SwitchDesktop.argtypes = [W.HANDLE]


class LASTINPUTINFO(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]


def foreground():
    desktop = user32.OpenInputDesktop(0, False, 0x100)
    if not desktop:
        return None, None
    try:
        if not user32.SwitchDesktop(desktop):
            return None, None
    finally:
        user32.CloseDesktop(desktop)
    info = LASTINPUTINFO(C.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(C.byref(info)):
        return None, None
    idle = ((kernel32.GetTickCount64() & 0xffffffff) - info.dwTime) & 0xffffffff
    pid = W.DWORD()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, idle / 1000
    user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return None, idle / 1000
    try:
        buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
            return os.path.basename(buf.value).lower(), idle / 1000
    finally:
        kernel32.CloseHandle(handle)
    return None, idle / 1000


def parse_hotkey(text):
    parts = [p.strip().upper() for p in text.split('+')]
    mods = 0
    for p in parts[:-1]:
        if p not in {'CTRL': 2, 'ALT': 1, 'SHIFT': 4, 'WIN': 8}:
            raise ValueError('快捷键格式示例：Ctrl+Alt+F 或 Ctrl+Shift+F8')
        mods |= {'CTRL': 2, 'ALT': 1, 'SHIFT': 4, 'WIN': 8}[p]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError('主键支持 A–Z、0–9、F1–F12。')
    if not mods:
        raise ValueError('请至少使用 Ctrl、Alt、Shift 或 Win 中的一个修饰键。')
    return mods | 0x4000, vk


class Hotkey:
    def __init__(self, events):
        self.events, self.commands = events, queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def configure(self, text, action='focus'):
        parse_hotkey(text)
        self.commands.put((action, text))

    def run(self):
        current = {}
        ids = {'focus': 1, 'journal': 2}
        msg = W.MSG()
        while True:
            try:
                command = self.commands.get_nowait()
                if command is None:
                    for ident in ids.values():
                        user32.UnregisterHotKey(None, ident)
                    return
                action, text = command
                ident = ids[action]
                mods, vk = parse_hotkey(text)
                if current.get(action) == text:
                    self.events.put(('hotkey_ok', (action, text)))
                elif user32.RegisterHotKey(None, 99, mods, vk):
                    user32.UnregisterHotKey(None, ident)
                    user32.UnregisterHotKey(None, 99)
                    if user32.RegisterHotKey(None, ident, mods, vk):
                        current[action] = text
                        self.events.put(('hotkey_ok', (action, text)))
                    else:
                        if action in current:
                            user32.RegisterHotKey(None, ident, *parse_hotkey(current[action]))
                        self.events.put(('hotkey_error', (action, text)))
                else:
                    self.events.put(('hotkey_error', (action, text)))
            except queue.Empty:
                pass
            while user32.PeekMessageW(C.byref(msg), None, 0, 0, 1):
                if msg.message == 0x312:
                    self.events.put(('toggle' if msg.wParam == 1 else 'journal', None))
            time.sleep(0.03)


def single_instance():
    kernel32.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
    kernel32.CreateMutexW.restype = W.HANDLE
    handle = kernel32.CreateMutexW(None, False, 'Local\\Time2Manage.Desktop.v1')
    return handle, C.get_last_error() != 183


class DATA_BLOB(C.Structure):
    _fields_ = [('cbData', W.DWORD), ('pbData', C.POINTER(C.c_ubyte))]


def protect_secret(value, decrypt=False):
    """Windows DPAPI, tied to the current Windows account."""
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buf = (C.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = DATA_BLOB(len(raw), buf), DATA_BLOB()
    crypt = C.WinDLL('crypt32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [C.POINTER(DATA_BLOB), C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(DATA_BLOB)]
    if not function(C.byref(source), None, None, None, None, 1, C.byref(output)):
        raise OSError('无法读取 API 密钥，请重新输入并保存。')
    kernel32.LocalFree.argtypes = [C.c_void_p]
    try:
        result = C.string_at(output.pbData, output.cbData)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        kernel32.LocalFree(output.pbData)
