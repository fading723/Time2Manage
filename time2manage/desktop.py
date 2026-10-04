"""Windows host for the local interface. No local web server is used."""
import argparse
import calendar
import csv
import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QUrl, Qt, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen, QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout, QMessageBox, QFileDialog, QSystemTrayIcon, QMenu
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
from PySide6.QtWebChannel import QWebChannel

from time2manage.storage import Store
from time2manage.tracker import Tracker
from time2manage.windows import Hotkey, single_instance, foreground, protect_secret
from time2manage.review import DEFAULT_PROMPT, make_payload, complete, endpoint_url


def default_data_dir():
    """Resolve the Windows account's stable local folder, independent of cwd/EXE."""
    folder = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, folder) == 0:
        return Path(folder.value) / 'Time2Manage'
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Time2Manage'


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in ('file', 'qrc', 'data', 'about', 'blob'):
            info.block(True)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        return url.scheme() in ('file', 'qrc', 'about', 'data')

    def javaScriptConsoleMessage(self, level, message, line, source):
        if not getattr(sys, 'frozen', False):
            print(f'UI: {message} ({source}:{line})', flush=True)


class JournalDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle('记下此刻 · 时间有迹')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(570, 400)
        self.setStyleSheet('''QDialog {background:#f5f5f7;} QLabel {color:#1d1d1f; font-family:"Microsoft YaHei UI";}
        QTextEdit {background:white; border:1px solid #e5e5ea; border-radius:12px; padding:14px; font:14px "Microsoft YaHei UI"; color:#1d1d1f;}
        QPushButton {background:#007aff; color:white; border:0; border-radius:10px; padding:10px 24px; font:13px "Microsoft YaHei UI";}
        QPushButton#cancel {background:#e8e8ed; color:#515154;}''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        title = QLabel('记下此刻')
        title.setStyleSheet('font-size:24px; font-weight:600;')
        layout.addWidget(title)
        stamp = QLabel('保存时记录本地时间，精确到秒 · Ctrl + Enter 保存')
        stamp.setStyleSheet('color:#86868b; font-size:12px;')
        layout.addWidget(stamp)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText('刚完成了什么？现在在想什么？\n为今天留下一点线索。')
        layout.addWidget(self.editor)
        row = QHBoxLayout()
        cancel = QPushButton('取消')
        cancel.setObjectName('cancel')
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        save = QPushButton('保存日记')
        save.clicked.connect(self.save)
        row.addWidget(save)
        layout.addLayout(row)
        self.shortcut = QShortcut(QKeySequence('Ctrl+Return'), self)
        self.shortcut.activated.connect(self.save)

    def save(self):
        try:
            self.host.store.add_journal(self.editor.toPlainText())
            self.host.bridge.changed.emit('journal')
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, '无法保存', str(e))

    def reject(self):
        if self.editor.toPlainText().strip():
            answer = QMessageBox.question(self, '保留这段文字？', '是否放弃未保存的日记？', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()


class Bridge(QObject):
    live = Signal(str)
    changed = Signal(str)
    notice = Signal(str)
    reviewFinished = Signal(str)

    def __init__(self, host):
        super().__init__(host)
        self.host = host

    @Slot(str, result=str)
    def call(self, raw):
        try:
            request = json.loads(raw)
            result = self.dispatch(request['action'], request.get('data', {}))
            return encode(dict(ok=True, data=result))
        except Exception as e:
            return encode(dict(ok=False, error=str(e)))

    def dispatch(self, action, data):
        h, store = self.host, self.host.store
        if action == 'snapshot':
            day = date.fromisoformat(data.get('day', date.today().isoformat()))
            period = data.get('period', 'day')
            start = day if period == 'day' else day-timedelta(days=6) if period == 'week' else day.replace(day=1)
            end = day if period != 'month' else day.replace(day=calendar.monthrange(day.year, day.month)[1])
            apps, usage, focuses = store.report(start.isoformat(), end.isoformat())
            trend = []
            for i in range((end-start).days+1):
                d = (start+timedelta(days=i)).isoformat()
                trend.append(dict(day=d, app=usage.get(d, 0), focus=focuses.get(d, 0), active=store.union_seconds(store.intervals(d))))
            return dict(day=store.day_data(day.isoformat()), range=dict(start=start.isoformat(), end=end.isoformat(), app=sum(usage.values()), focus=sum(focuses.values()), active=sum(t['active'] for t in trend), apps=[dict(name=n, seconds=s) for n,s in apps], trend=trend), tracked_apps=[dict(id=i, name=n, processes=p) for i,n,p in store.apps()], reviews=store.reviews(day.isoformat()), demo=h.demo, **h.live_data())
        if action == 'toggle_focus':
            h.tracker.toggle_focus()
            self.live.emit(encode(h.live_data()))
            return h.live_data()
        if action == 'toggle_pause':
            h.tracker.toggle_pause()
            self.live.emit(encode(h.live_data()))
            return h.live_data()
        if action == 'journal':
            h.open_journal()
            return None
        if action == 'save_app':
            store.save_app(data['name'], data['processes'], int(data['id']) if data.get('id') else None)
            h.tracker.refresh_map()
            self.changed.emit('apps')
            return None
        if action == 'delete_app':
            store.delete_app(int(data['id']))
            h.tracker.refresh_map()
            self.changed.emit('apps')
            return None
        if action == 'capture_app':
            h.showMinimized()
            def capture():
                process, _ = foreground()
                h.showNormal()
                h.activateWindow()
                self.changed.emit(encode(dict(captured_process=process)))
            QTimer.singleShot(3000, capture)
            return None
        if action == 'settings':
            return dict(focus_key=store.setting('hotkey', 'Ctrl+Alt+F'), journal_key=store.setting('journal_hotkey', 'Ctrl+Alt+J'), idle_seconds=h.tracker.idle_limit, endpoint=store.setting('api_endpoint', ''), model=store.setting('api_model', ''), has_key=bool(store.setting('api_key')), prompt=store.setting('review_prompt', DEFAULT_PROMPT), data_dir=str(h.data_dir))
        if action == 'save_tracking':
            idle = int(data['idle_seconds'])
            if not 5 <= idle <= 3600:
                raise ValueError('空闲阈值需为 5–3600 秒。')
            from time2manage.windows import parse_hotkey
            if parse_hotkey(data['focus_key']) == parse_hotkey(data['journal_key']):
                raise ValueError('两个快捷键不能相同。')
            h.hotkey.configure(data['focus_key'], 'focus')
            h.hotkey.configure(data['journal_key'], 'journal')
            store.set_setting('idle_seconds', idle)
            h.tracker.idle_limit = idle
            return None
        if action == 'save_api':
            endpoint = data['endpoint'].strip()
            endpoint_url(endpoint)
            if not data['model'].strip():
                raise ValueError('请填写模型名称。')
            encrypted = protect_secret(data['key']) if data.get('key') else None
            store.set_setting('api_endpoint', endpoint)
            store.set_setting('api_model', data['model'].strip())
            if encrypted:
                store.set_setting('api_key', encrypted)
            elif data.get('clear_key'):
                store.set_setting('api_key', '')
            store.set_setting('review_prompt', data.get('prompt', DEFAULT_PROMPT).strip() or DEFAULT_PROMPT)
            return None
        if action == 'default_prompt':
            return DEFAULT_PROMPT
        if action == 'preview_review':
            day = date.fromisoformat(data['day']).isoformat()
            payload = make_payload(store, day)
            h.preview = dict(day=day, payload=payload, prompt=store.setting('review_prompt', DEFAULT_PROMPT), endpoint=store.setting('api_endpoint', ''), model=store.setting('api_model', ''))
            return h.preview
        if action in ('generate_review', 'test_api'):
            if h.api_busy:
                raise ValueError('已有请求正在进行，请稍候。')
            endpoint = store.setting('api_endpoint', '')
            model = store.setting('api_model', '')
            endpoint_url(endpoint)
            secret = store.setting('api_key', '')
            key = protect_secret(secret, decrypt=True) if secret else ''
            if action == 'generate_review':
                if not h.preview or h.preview['day'] != data['day'] or h.preview['endpoint'] != endpoint or h.preview['model'] != model or h.preview['prompt'] != store.setting('review_prompt', DEFAULT_PROMPT):
                    raise ValueError('预览已失效，请重新预览请求内容。')
                snapshot = h.preview.copy()
                h.preview = None
            else:
                snapshot = dict(day=None, payload={'task': '只回复：连接成功'}, prompt='请只回复：连接成功。', endpoint=endpoint, model=model)
            h.api_busy = True
            def request():
                try:
                    content = complete(endpoint, model, key, snapshot['prompt'], snapshot['payload'])
                    h.events.put(('api_done', dict(**snapshot, content=content)))
                except Exception as e:
                    h.events.put(('api_error', str(e)))
            threading.Thread(target=request, daemon=True).start()
            return None
        if action == 'open_data':
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(h.data_dir)))
            return None
        if action == 'backup':
            path, _ = QFileDialog.getSaveFileName(h, '备份本地数据库', f'Time2Manage_{date.today()}.sqlite3', 'SQLite 数据库 (*.sqlite3)')
            if path:
                if Path(path).resolve() == (h.data_dir / 'usage.sqlite3').resolve():
                    raise ValueError('不能覆盖当前数据库。')
                store.backup(path)
                self.notice.emit('数据库备份已保存')
            return None
        if action == 'export':
            day = date.fromisoformat(data['day']).isoformat()
            path, _ = QFileDialog.getSaveFileName(h, '导出当天明细', f'时间明细_{day}.csv', 'CSV (*.csv)')
            if path:
                with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(['类型', '名称', '开始时间', '结束时间', '时长秒', '日记内容'])
                    for i in store.intervals(day):
                        writer.writerow([i['kind'], i['name'], i['start'], i['end'], round(i['end_second']-i['start_second'], 3), ''])
                    for j in store.journals(day):
                        writer.writerow(['journal', '', j['created'], '', '', j['content']])
                self.notice.emit('时间轴与日记明细已导出')
            return None
        raise ValueError('未知操作。')


class App(QMainWindow):
    def __init__(self, data_dir=None, demo=False):
        super().__init__()
        self.demo = demo
        self.data_dir = Path(data_dir).resolve() if data_dir else default_data_dir()
        self.store = Store(self.data_dir / 'usage.sqlite3')
        if demo:
            seed_demo(self.store)
        self.tracker = Tracker(self.store)
        self.events = queue.Queue()
        self.hotkey = Hotkey(self.events)
        self.preview = None
        self.api_busy = False
        self.journal_dialog = None
        self.setWindowTitle('时间有迹 · Time2Manage')
        self.resize(1280, 880)
        self.setMinimumSize(980, 700)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'app.ico')))
        QApplication.instance().setWindowIcon(self.windowIcon())
        self._shutdown = False
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('时间有迹 · 后台自动记录')
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction('打开时间有迹', self.restore_window)
        self.tray_menu.addAction('写一条日记', self.open_journal)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction('退出…', self.close)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            QApplication.instance().setQuitOnLastWindowClosed(False)
            self.tray.show()
        self.view = QWebEngineView(self)
        self.page = Page(self.view)
        self.view.setPage(self.page)
        self.interceptor = LocalOnly(self)
        self.page.profile().setUrlRequestInterceptor(self.interceptor)
        self.setCentralWidget(self.view)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('backend', self.bridge)
        self.page.setWebChannel(self.channel)
        ui_dir = Path(__file__).parent / 'ui'
        html = (ui_dir / 'index.html').read_text(encoding='utf-8')
        logo = base64.b64encode((Path(__file__).parent / 'assets' / 'app.png').read_bytes()).decode('ascii')
        html = html.replace('APP_ICON_DATA', 'data:image/png;base64,' + logo)
        html = html.replace('<link rel="stylesheet" href="style.css">', '<style>' + (ui_dir / 'style.css').read_text(encoding='utf-8') + '</style>')
        html = html.replace('<script src="app.js"></script>', '<script>' + (ui_dir / 'app.js').read_text(encoding='utf-8') + '</script>')
        self.view.setHtml(html, QUrl('qrc:///time2manage/'))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(self.auto_backup)
        self.backup_timer.start(300000)
        self.auto_backup()
        if not demo:
            self.hotkey.configure(self.store.setting('hotkey', 'Ctrl+Alt+F'), 'focus')
            self.hotkey.configure(self.store.setting('journal_hotkey', 'Ctrl+Alt+J'), 'journal')

    def live_data(self):
        return dict(status='演示数据 · 不记录真实使用' if self.demo else self.tracker.status, focus_running=self.tracker.focus, session_seconds=self.tracker.focus_seconds, paused=self.tracker.paused, api_busy=self.api_busy)

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_window()

    def hide_to_tray(self):
        if self._shutdown or not self.isMinimized():
            return
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.hide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide_to_tray)

    def auto_backup(self):
        if self.demo:
            return
        try:
            directory = self.data_dir / 'backups'
            directory.mkdir(exist_ok=True)
            destination = directory / f'usage_{date.today()}.sqlite3'
            temporary = destination.with_suffix('.tmp')
            self.store.backup(temporary)
            temporary.replace(destination)
        except Exception as e:
            self.bridge.notice.emit(f'自动备份失败：{e}')

    def tick(self):
        try:
            if not self.demo:
                self.tracker.account()
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'toggle':
                    self.tracker.toggle_focus()
                    self.bridge.changed.emit('focus')
                elif kind == 'journal':
                    self.open_journal()
                elif kind == 'hotkey_ok':
                    action, text = value
                    self.store.set_setting('hotkey' if action == 'focus' else 'journal_hotkey', text)
                    self.bridge.notice.emit(f'{"专注" if action == "focus" else "日记"}快捷键已启用：{text}')
                elif kind == 'hotkey_error':
                    self.bridge.notice.emit(f'快捷键 {value[1]} 被占用，未替换原设置')
                elif kind == 'api_done':
                    self.api_busy = False
                    if value['day']:
                        self.store.save_review(value['day'], value['model'], value['endpoint'], value['prompt'], value['payload'], value['content'])
                        self.bridge.changed.emit('review')
                    self.bridge.reviewFinished.emit(encode(dict(ok=True, day=value['day'], content=value['content'])))
                elif kind == 'api_error':
                    self.api_busy = False
                    self.bridge.reviewFinished.emit(encode(dict(ok=False, error=value)))
            self.bridge.live.emit(encode(self.live_data()))
        except Exception as e:
            self.timer.stop()
            QMessageBox.critical(self, '记录已中断', f'无法继续保存记录，请检查磁盘空间并重新启动。\n{e}')

    def open_journal(self):
        if self.journal_dialog and self.journal_dialog.isVisible():
            self.journal_dialog.raise_()
            self.journal_dialog.activateWindow()
            self.journal_dialog.editor.setFocus()
            return
        self.journal_dialog = JournalDialog(self)
        self.journal_dialog.show()
        self.journal_dialog.raise_()
        self.journal_dialog.activateWindow()
        self.journal_dialog.editor.setFocus()

    def closeEvent(self, event):
        if self.journal_dialog and self.journal_dialog.isVisible():
            self.journal_dialog.reject()
            if self.journal_dialog.isVisible():
                event.ignore()
                return
        box = QMessageBox(self)
        box.setWindowTitle('关闭时间有迹')
        box.setText('保持记录，还是结束今天的记录？')
        if self.api_busy:
            box.setInformativeText('模型请求仍在进行；退出会放弃尚未保存的结果。')
        background = box.addButton('后台继续记录', QMessageBox.ButtonRole.ActionRole)
        exit_button = box.addButton('保存并退出', QMessageBox.ButtonRole.AcceptRole)
        box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == background:
            self.showMinimized()
            event.ignore()
        elif box.clickedButton() == exit_button:
            self.shutdown()
            event.accept()
            QApplication.instance().quit()
        else:
            event.ignore()

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self.tray.hide()
        self.timer.stop()
        self.backup_timer.stop()
        if not self.demo:
            self.tracker.account()
        self.hotkey.commands.put(None)
        self.hotkey.thread.join(timeout=1)
        self.auto_backup()
        self.store.close()


def seed_demo(store):
    if store.setting('demo_seeded'):
        return
    day = date.today()
    apps = store.apps()
    def at(h, m, s=0):
        return datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m, second=s)
    blocks = [(1,9,5,9,22), (2,9,22,10,18), (3,10,18,10,44), (4,10,44,10,52), (2,10,52,11,40), (1,11,40,12,5), (5,13,10,13,24), (3,13,24,14,8), (2,14,8,15,12), (4,15,12,15,20), (1,15,20,15,47), (2,15,47,16,35), (3,16,35,17,10)]
    for ident,h,m,eh,em in blocks:
        store.record(at(h,m), at(eh,em), apps[ident-1][0])
    for h,m,eh,em in [(9,22,10,30),(10,52,11,40),(13,24,14,45),(15,47,16,35)]:
        store.record(at(h,m), at(eh,em), focus=True)
        store.break_focus()
    store.add_journal('今天先把时间轴的数据结构理清楚。希望午饭前完成本地存储与跨日处理。', at(9,7,16))
    store.add_journal('完成了第一轮实现。和同学聊了十分钟，发现并集统计比直接累加更合理。', at(10,49,32))
    store.add_journal('下午的连续专注状态很好。明天优先处理分钟详情和日记联动。', at(16,38,9))
    store.set_setting('demo_seeded', 'yes')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-shot')
    args = parser.parse_args()
    if args.demo and not args.data_dir:
        parser.error('演示模式必须指定独立的 --data-dir。')
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Time2Manage.Desktop')
    app = QApplication(sys.argv)
    app.setApplicationName('Time2Manage')
    app.setStyle('Fusion')
    mutex, is_first = single_instance() if not args.demo and not args.smoke_shot else (None, True)
    if not is_first:
        QMessageBox.information(None, '时间有迹正在运行', '请点击右下角系统托盘中的时间有迹图标打开窗口（可能在 ↑ 隐藏图标中）。')
        return
    try:
        host = App(args.data_dir, args.demo)
    except Exception as e:
        QMessageBox.critical(None, '无法打开本地记录', str(e))
        return
    host.show()
    if args.smoke_shot:
        def ready(ok):
            print(f'UI load finished: {ok}', flush=True)
            def shot():
                host.page.runJavaScript('Boolean(window.__uiReady)', lambda result: print(f'UI bridge ready: {result}', flush=True))
                host.grab().save(args.smoke_shot)
                QTimer.singleShot(300, finish)
            QTimer.singleShot(2000, shot)
        def finish():
            host.shutdown()
            host.page.deleteLater()
            host.view.deleteLater()
            QTimer.singleShot(100, app.quit)
        host.page.loadFinished.connect(ready)
        QTimer.singleShot(30000, finish)
    app.exec()


if __name__ == '__main__':
    main()
