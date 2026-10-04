"""Run explicitly: python tests/ui_smoke.py. Uses only an isolated demo database."""
import json
from pathlib import Path
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from time2manage.desktop import App

root = Path(__file__).resolve().parents[1]
captured = {}
failures = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        captured['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        data = json.dumps({'choices':[{'message':{'content':'## 今日概览\n本地模拟复盘完成。\n\n## 明天的一个小计划\n- 先留一个完整专注块。'}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self,*args):
        pass


server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
application = QApplication(sys.argv)
host = App(root/'build'/'ui-integration-data'/uuid.uuid4().hex,demo=True)
host.show()
completed = False


def finish():
    global completed
    if completed:
        return
    completed = True
    host.shutdown()
    host.page.deleteLater()
    host.view.deleteLater()
    QTimer.singleShot(200,application.quit)


def guard(fn):
    try:
        fn()
    except Exception as e:
        failures.append(str(e))
        print('FAIL',repr(e),flush=True)
        finish()


def check_ready(ready):
    def work():
        assert ready, 'Frontend bridge not ready'
        print('PASS frontend bridge',flush=True)
        host.grab().save(str(root/'build'/'design-overview.png'))
        host.open_journal()
        host.journal_dialog.editor.setPlainText('UI 集成测试日记，保存时精确到秒。')
        host.journal_dialog.save()
        journals = host.store.journals(date.today().isoformat())
        saved = [j for j in journals if j['content'].startswith('UI 集成测试')]
        assert saved, 'Journal was not saved'
        assert len(saved[-1]['created']) == 19, 'Journal timestamp not second precision'
        print('PASS native journal save',flush=True)
        host.bridge.dispatch('save_api',dict(endpoint=f'http://127.0.0.1:{server.server_port}/v1',model='local-test-model',key='test-only-key',prompt='测试提示词：请基于并集活动覆盖复盘。'))
        day=date.today().isoformat()
        preview=host.bridge.dispatch('preview_review',{'day':day})
        assert any('UI 集成测试' in j['content'] for j in preview['payload']['journals'])
        host.bridge.dispatch('generate_review',{'day':day})
        host.store.add_journal('预览之后的新日记，不应混入冻结请求。')
        host.page.runJavaScript("zoom=1; windowStart=9; selectedMinute=547; navigate('timeline');",lambda _:QTimer.singleShot(300,inspect_timeline))
    guard(work)


def inspect_timeline():
    host.grab().save(str(root/'build'/'design-timeline.png'))
    host.page.runJavaScript("JSON.stringify({diaryLanes:document.querySelectorAll('.journal-dot,.minute-journal').length, names:document.querySelector('#timeline-full').textContent})",lambda text:guard(lambda: check_timeline(text)))


def check_timeline(text):
    result=json.loads(text)
    assert result['diaryLanes']==0, 'Diary still visualized in timeline'
    assert '此刻日记' not in result['names'], 'Diary lane still present'
    print('PASS timeline excludes diary visualization',flush=True)
    wait_review()


attempts=0
def wait_review():
    global attempts
    attempts+=1
    if host.api_busy and attempts < 40:
        QTimer.singleShot(250,wait_review)
        return
    def work():
        assert not host.api_busy,'API request did not finish'
        reviews=host.store.reviews(date.today().isoformat())
        assert reviews and '本地模拟复盘完成' in reviews[0]['content']
        body=captured['body']
        assert 'UI 集成测试' in body['messages'][1]['content']
        assert '预览之后的新日记' not in body['messages'][1]['content'], 'Request changed after preview'
        print('PASS background API request, frozen preview, local review persistence',flush=True)
        host.page.runJavaScript("navigate('review');",lambda _:QTimer.singleShot(500,review_shot))
    guard(work)


def review_shot():
    host.grab().save(str(root/'build'/'design-review.png'))
    host.page.runJavaScript("navigate('settings');",lambda _:QTimer.singleShot(500,settings_shot))


def settings_shot():
    host.grab().save(str(root/'build'/'design-settings.png'))
    host.bridge.dispatch('save_app',dict(name='刷新验证软件',processes='refresh-test.exe'))
    host.page.runJavaScript("refresh();",lambda _:QTimer.singleShot(300,check_added))


def check_added():
    host.page.runJavaScript("document.querySelector('#timeline-full').textContent.includes('刷新验证软件') && document.querySelector('#timeline-home').textContent.includes('刷新验证软件')", lambda visible:guard(lambda: delete_added(visible)))


def delete_added(visible):
    assert visible, 'Added app not immediately visible in both timelines'
    app_id=next(i for i,n,p in host.store.apps() if n=='刷新验证软件')
    host.bridge.dispatch('delete_app',{'id':app_id})
    host.page.runJavaScript("refresh();",lambda _:QTimer.singleShot(300,check_deleted))


def check_deleted():
    host.page.runJavaScript("!document.querySelector('#timeline-full').textContent.includes('刷新验证软件') && !document.querySelector('#timeline-home').textContent.includes('刷新验证软件')", lambda hidden:guard(lambda: finish_checks(hidden)))


def finish_checks(hidden):
    assert hidden, 'Deleted app remains visible in timeline'
    print('PASS immediate add/delete refresh in both timelines',flush=True)
    print('PASS all integration checks',flush=True)
    finish()


def loaded(ok):
    if not ok:
        failures.append('HTML failed to load')
        finish()
    else:
        QTimer.singleShot(1500,lambda:host.page.runJavaScript('Boolean(window.__uiReady)',check_ready))


host.page.loadFinished.connect(loaded)
def timeout():
    if not completed:
        failures.append('Timed out')
        finish()
QTimer.singleShot(30000,timeout)
application.exec()
server.shutdown()
server.server_close()
print('RESULT', 'FAIL' if failures else 'PASS', failures,flush=True)
sys.exit(bool(failures))
