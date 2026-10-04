"""Isolated tray integration check; no user database is touched."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from time2manage.desktop import App

application=QApplication(sys.argv)
directory=tempfile.TemporaryDirectory()
host=App(Path(directory.name),demo=True)
host.show()
errors=[]

def check_hidden():
    try:
        assert QSystemTrayIcon.isSystemTrayAvailable(), 'System tray unavailable'
        assert not host.isVisible(), 'Minimized window remains visible'
        assert host.tray.isVisible(), 'Tray icon not visible'
        assert host.timer.isActive(), 'Recording timer stopped'
        host.open_journal()
        assert host.journal_dialog.isVisible(), 'Journal cannot open while hidden'
        host.journal_dialog.reject()
        host.tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        QTimer.singleShot(300,check_restored)
    except Exception as e:
        errors.append(str(e))
        finish()

def check_restored():
    try:
        assert host.isVisible() and not host.isMinimized(), 'Tray click did not restore'
        print('PASS minimize to tray, recording timer, hidden journal, restore',flush=True)
    except Exception as e:
        errors.append(str(e))
    finish()

def finish():
    host.shutdown()
    application.quit()

QTimer.singleShot(1200,host.showMinimized)
QTimer.singleShot(1800,check_hidden)
QTimer.singleShot(10000,finish)
application.exec()
print('RESULT',errors,flush=True)
sys.exit(bool(errors))
