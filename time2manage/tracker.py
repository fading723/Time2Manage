import time
from datetime import datetime, timedelta
from time2manage.windows import foreground


class Tracker:
    def __init__(self, store):
        self.store = store
        self.focus = False
        self.focus_seconds = 0.0
        self.paused = False
        self.previous_app = None
        self.last_wall = datetime.now()
        self.last_tick = time.perf_counter()
        self.idle_limit = int(store.setting('idle_seconds', '60'))
        self.refresh_map()
        self.status = '正在识别前台软件'

    def refresh_map(self):
        self.app_map = {p: (app_id, name) for app_id, name, processes in self.store.apps() for p in processes.split(',')}

    def account(self):
        now, monotonic = datetime.now(), time.perf_counter()
        elapsed = monotonic - self.last_tick
        process, idle = foreground()
        matched = self.app_map.get(process)
        app_id = matched[0] if matched and idle is not None and idle < self.idle_limit and not self.paused else None
        if 0 < elapsed < 3 and 0 <= (now-self.last_wall).total_seconds() < 3:
            # Keep contiguous endpoints to merge samples without filling any gaps.
            start = self.last_wall
            credited = app_id if app_id == self.previous_app else None
            self.store.record(start, now, credited, self.focus)
            if self.focus:
                self.focus_seconds += (now-start).total_seconds()
        self.previous_app = app_id
        self.last_wall, self.last_tick = now, monotonic
        if self.paused:
            self.status = '自动记录已暂停'
        elif idle is None:
            self.status = '锁屏或前台不可访问'
        elif idle >= self.idle_limit:
            self.status = f'空闲 {int(idle)} 秒 · 记录暂停'
        elif matched:
            self.status = f'正在使用 {matched[1]}'
        else:
            self.status = f'{process or "未知前台"} · 未加入记录'

    def toggle_focus(self):
        self.account()
        self.store.break_focus()
        self.focus = not self.focus
        if self.focus:
            self.focus_seconds = 0

    def toggle_pause(self):
        self.account()
        self.paused = not self.paused
        self.previous_app = None
