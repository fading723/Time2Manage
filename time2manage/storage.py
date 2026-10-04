import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_APPS = [('Edge 浏览器', 'msedge.exe'), ('VS Code', 'code.exe'),
                ('Codex', 'codex.exe,chatgpt.exe'), ('微信', 'weixin.exe,wechat.exe'), ('QQ', 'qq.exe')]


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        check = self.db.execute('PRAGMA quick_check').fetchall()
        if check != [('ok',)]:
            self.db.close()
            raise RuntimeError(f'数据库检查失败，原文件已保留，请从备份恢复：{self.path}')
        # A single GUI thread owns this database. Keep committed data in the main
        # file, so copying it or reopening after a crash does not omit a WAL file.
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS apps(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, processes TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(day TEXT, app_id INTEGER, seconds REAL NOT NULL, PRIMARY KEY(day,app_id));
            CREATE TABLE IF NOT EXISTS focus(day TEXT PRIMARY KEY, seconds REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intervals(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, app_id INTEGER, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS interval_time ON intervals(start,end);
            CREATE TABLE IF NOT EXISTS journals(id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, content TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS journal_time ON journals(created);
            CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, created TEXT NOT NULL, model TEXT NOT NULL, endpoint TEXT NOT NULL, prompt TEXT NOT NULL, payload TEXT NOT NULL, content TEXT NOT NULL);
        ''')
        self._tails = {}
        if self.setting('initialized') is None:
            self.db.executemany('INSERT INTO apps(name,processes) VALUES (?,?)', DEFAULT_APPS)
            self.set_setting('initialized', 'yes')
        self.db.commit()
        if self.setting('detail_since') is None:
            self.set_setting('detail_since', datetime.now().isoformat(timespec='seconds'))
        # Some packaged Codex desktop distributions use ChatGPT.exe as the executable.
        # Only upgrade the untouched default; respect custom mappings and deleted apps.
        if self.setting('codex_alias_migration') is None:
            rows = self.apps()
            if not any('chatgpt.exe' in p.split(',') for _,_,p in rows):
                self.db.execute("UPDATE apps SET processes='codex.exe,chatgpt.exe' WHERE name='Codex' AND processes='codex.exe'")
            self.set_setting('codex_alias_migration', 'yes')

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))
        self.db.commit()

    def apps(self):
        return self.db.execute('SELECT id,name,processes FROM apps ORDER BY id').fetchall()

    def save_app(self, name, processes, app_id=None):
        names = [p.strip().lower() for p in processes.split(',') if p.strip()]
        if not name.strip() or not names:
            raise ValueError('请填写软件名称和进程名。')
        if any(not p.endswith('.exe') or '/' in p or '\\' in p for p in names):
            raise ValueError('进程名应为 example.exe，多个进程用英文逗号分隔。')
        for existing_id, _, existing in self.apps():
            if existing_id != app_id and set(existing.split(',')) & set(names):
                raise ValueError('该进程已被其他软件使用，请编辑原有软件。')
        if app_id is None:
            self.db.execute('INSERT INTO apps(name,processes) VALUES (?,?)', (name.strip(), ','.join(dict.fromkeys(names))))
        else:
            self.db.execute('UPDATE apps SET name=?, processes=? WHERE id=?', (name.strip(), ','.join(dict.fromkeys(names)), app_id))
        self.db.commit()

    def delete_app(self, app_id):
        # Retain raw history for recovery, but exclude it from current reports.
        self.db.execute('DELETE FROM apps WHERE id=?', (app_id,))
        self.db.commit()

    def record(self, start, end, app_id=None, focus=False):
        if end <= start:
            return
        with self.db:
            while start < end:
                boundary = datetime.combine(start.date() + timedelta(days=1), datetime.min.time())
                stop = min(end, boundary)
                day, seconds = start.date().isoformat(), (stop-start).total_seconds()
                if app_id is not None:
                    self.db.execute('INSERT INTO usage VALUES (?,?,?) ON CONFLICT(day,app_id) DO UPDATE SET seconds=seconds+excluded.seconds', (day, app_id, seconds))
                    row = self.db.execute('SELECT name FROM apps WHERE id=?', (app_id,)).fetchone()
                    self._interval('app', app_id, row[0] if row else f'已删除软件 #{app_id}', start, stop)
                if focus:
                    self.db.execute('INSERT INTO focus VALUES (?,?) ON CONFLICT(day) DO UPDATE SET seconds=seconds+excluded.seconds', (day, seconds))
                    self._interval('focus', None, '手动专注', start, stop)
                start = stop

    def _interval(self, kind, app_id, name, start, end):
        key = kind, app_id
        tail = self._tails.get(key)
        # Merge only truly adjoining samples, never idle/sleep gaps or separate sessions.
        if tail and tail[1] == start and tail[2] == name and start.date() == end.date():
            self.db.execute('UPDATE intervals SET end=? WHERE id=?', (end.isoformat(timespec='microseconds'), tail[0]))
            self._tails[key] = tail[0], end, name
        else:
            cur = self.db.execute('INSERT INTO intervals(kind,app_id,name,start,end) VALUES (?,?,?,?,?)', (kind, app_id, name, start.isoformat(timespec='microseconds'), end.isoformat(timespec='microseconds')))
            self._tails[key] = cur.lastrowid, end, name

    def break_focus(self):
        self._tails.pop(('focus', None), None)

    def intervals(self, day, include_removed=False):
        start = datetime.fromisoformat(day).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        visibility = '' if include_removed else " AND (i.kind='focus' OR a.id IS NOT NULL)"
        rows = self.db.execute('SELECT i.id,i.kind,i.app_id,COALESCE(a.name,i.name),i.start,i.end FROM intervals i LEFT JOIN apps a ON a.id=i.app_id WHERE i.start<? AND i.end>?' + visibility + ' ORDER BY i.start,i.id', (end.isoformat(), start.isoformat())).fetchall()
        result = []
        for ident, kind, app_id, name, a, b in rows:
            a, b = max(datetime.fromisoformat(a), start), min(datetime.fromisoformat(b), end)
            if b > a:
                result.append(dict(id=ident, kind=kind, app_id=app_id, name=name, start=a.isoformat(timespec='milliseconds'), end=b.isoformat(timespec='milliseconds'), start_second=(a-start).total_seconds(), end_second=(b-start).total_seconds()))
        return result

    @staticmethod
    def union_seconds(intervals):
        spans = sorted((i['start_second'], i['end_second']) for i in intervals)
        total, end = 0.0, -1.0
        for a, b in spans:
            total += max(0.0, b - max(a, end))
            end = max(end, b)
        return total

    def add_journal(self, content, created=None):
        content = content.strip()
        if not content:
            raise ValueError('请先写一点内容。')
        if len(content) > 20000:
            raise ValueError('单条日记最多 20000 字。')
        created = (created or datetime.now()).isoformat(timespec='seconds')
        with self.db:
            cur = self.db.execute('INSERT INTO journals(created,content) VALUES (?,?)', (created, content))
        return cur.lastrowid

    def journals(self, day):
        return [dict(id=row[0], created=row[1], content=row[2]) for row in self.db.execute('SELECT id,created,content FROM journals WHERE created>=? AND created<? ORDER BY created,id', (day+'T00:00:00', (datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+'T00:00:00'))]

    def day_data(self, day):
        apps, usage, focus = self.report(day, day)
        intervals = self.intervals(day)
        return dict(day=day, intervals=intervals, journals=self.journals(day), apps=[dict(name=n, seconds=s) for n,s in apps], app_seconds=sum(usage.values()), focus_seconds=sum(focus.values()), active_seconds=self.union_seconds(intervals), detail_since=self.setting('detail_since'))

    def save_review(self, day, model, endpoint, prompt, payload, content):
        with self.db:
            self.db.execute('INSERT INTO reviews(day,created,model,endpoint,prompt,payload,content) VALUES (?,?,?,?,?,?,?)', (day, datetime.now().isoformat(timespec='seconds'), model, endpoint, prompt, json.dumps(payload, ensure_ascii=False), content))

    def reviews(self, day):
        return [dict(id=r[0], created=r[1], model=r[2], content=r[3]) for r in self.db.execute('SELECT id,created,model,content FROM reviews WHERE day=? ORDER BY id DESC', (day,))]

    def report(self, start, end, include_removed=False):
        join = 'LEFT JOIN' if include_removed else 'JOIN'
        apps = self.db.execute(f'SELECT COALESCE(a.name,\'已删除软件 #\'||u.app_id), SUM(u.seconds) FROM usage u {join} apps a ON a.id=u.app_id WHERE day BETWEEN ? AND ? GROUP BY u.app_id ORDER BY SUM(u.seconds) DESC', (start, end)).fetchall()
        days = self.db.execute(f'SELECT day,SUM(seconds) FROM usage u {join} apps a ON a.id=u.app_id WHERE day BETWEEN ? AND ? GROUP BY day', (start, end)).fetchall()
        focuses = self.db.execute('SELECT day,seconds FROM focus WHERE day BETWEEN ? AND ?', (start, end)).fetchall()
        return apps, dict(days), dict(focuses)

    def backup(self, path):
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
        finally:
            target.close()

    def close(self):
        self.db.commit()
        self.db.close()
