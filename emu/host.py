
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cbelib
from emu.cheat import Cheat
from emu.runtime import Runtime

class Session:

    REP_DELAY, REP_RATE = 0.40, 0.12

    def __init__(self, path, audio=True, budget=40_000_000):
        self.mod = cbelib.load(path)
        self.rt = Runtime(self.mod, trace=False, quiet_log=True,
                          trace_fs=False, audio=audio)
        self.rt.mach.BUDGET = budget
        self.events = []
        if audio:
            self.rt.audio.on_event = lambda e: self.events.append(dict(e, kind="audio"))
        self.keys = 0
        self.latched = 0
        self.prev_keys = 0
        self.rep_next = {}
        self.touch = None

        self.touch_queue = []
        self._soft = None

        self._kev = self.rt.mach.data.alloc(4, "KeyEvent")
        self.cheat = Cheat(self.rt.mach)
        self.frame_no = 0
        self.nlog = 0
        self.alive = True

    def boot(self):

        with _quiet():
            self.rt.boot()
            self.rt.app_start()
        return self

    def stop(self):

        if not self.alive:
            return
        self.alive = False
        try:
            with _quiet():
                self.rt.app_stop()
                self.rt.pump()
        except Exception:
            pass

    def set_keys(self, mask):
        mask = int(mask)

        self.latched |= mask & ~self.keys
        self.keys = mask

    SOFT_POS = {"left": (0.08, 0.96), "right": (0.93, 0.96)}
    SOFT_BITS = {"left": 1 << 12, "right": 1 << 13}
    SOFT_WAIT = 8
    SOFT_MIN_CHANGE = 0.10

    def soft_key(self, side, pressed=True):

        if not pressed or side not in self.SOFT_POS:
            return
        fx, fy = self.SOFT_POS[side]
        w, h = self.size
        x, y = int(w * fx), int(h * fy)
        self.set_touch(x, y, "down")
        self.set_touch(x, y, "up")
        self._soft = [side, self.SOFT_WAIT, bytes(self.rt.fb.raw565())]

    def _soft_followup(self):

        if not self._soft:
            return
        self._soft[1] -= 1
        if self._soft[1] > 0:
            return
        side, _, before = self._soft
        self._soft = None
        cur = self.rt.fb.raw565()
        if before == cur:
            changed = 0
        else:

            changed = sum(1 for i in range(0, len(cur), 16) if cur[i] != before[i])
        if changed < (len(cur) // 16) * self.SOFT_MIN_CHANGE:
            self.latched |= self.SOFT_BITS[side]

    def set_touch(self, x, y, state):
        e = (int(x), int(y), state)

        if state == "move" and self.touch_queue and self.touch_queue[-1][2] == "move":
            self.touch_queue[-1] = e
        elif len(self.touch_queue) < 64:
            self.touch_queue.append(e)

    def _apply_input(self, now):
        rt = self.rt
        bits = self.keys | self.latched
        self.latched = 0

        rt.keys_down = bits & ~self.prev_keys
        rt.keys_hold = bits
        rt.keys_up = self.prev_keys & ~bits
        rt.keys_down |= self._autorepeat(bits, now)
        self.prev_keys = bits

        if self.touch_queue:

            self.touch = self.touch_queue.pop(0)
        if self.touch:
            x, y, st = self.touch
            rt.pointer = (x, y)
            rt.touch_down = 1 if st == "down" else 0
            rt.touch_hold = 1 if st in ("down", "move") else 0
            rt.touch_up = 1 if st == "up" else 0
            rt.touch_drag = 1 if st == "move" else 0
            if st == "up":
                self.touch = None
            elif st == "down":

                self.touch = (x, y, "move")

    def _key_event(self):

        rt = self.rt
        if rt.keys_down:
            rt.mach.w32(self._kev, rt.keys_down)
            return 0, self._kev
        if rt.keys_up:
            rt.mach.w32(self._kev, rt.keys_up)
            return 1, self._kev
        return rt.NO_EVENT, 0

    def _autorepeat(self, bits, now):
        out = 0
        for b in range(32):
            m = 1 << b
            if not (bits & m):
                self.rep_next.pop(b, None)
            elif b not in self.rep_next:
                self.rep_next[b] = now + self.REP_DELAY
            elif now >= self.rep_next[b]:
                self.rep_next[b] = now + self.REP_RATE
                out |= m
        return out

    def step(self, now=None):

        self._apply_input(time.time() if now is None else now)
        event, data = self._key_event()
        self.cheat.apply_locks()
        try:
            self.rt.frame(event, data)
        except Exception as e:
            self.events.append({"kind": "log",
                                "text": f"[宿主错误] {type(e).__name__}: {e}"})
        self.frame_no += 1
        self._soft_followup()
        if self.rt.exit_requested:
            self.events.append({"kind": "exit", "why": "module"})
            self.alive = False
        logs = self.rt.logs
        if len(logs) > self.nlog:
            for ln in logs[self.nlog:]:
                self.events.append({"kind": "log", "text": ln})
            self.nlog = len(logs)
        return self.rt.fb.raw565()

    def take_events(self):

        e, self.events = self.events, []
        return e

    def take_events_json(self):
        return json.dumps(self.take_events(), ensure_ascii=False)

    @property
    def name(self):

        return self.mod.name

    @property
    def screens(self):

        return len(self.rt.screens)

    @property
    def nonblank(self):

        return self.rt.fb.nonblank()

    @property
    def size(self):
        return (self.rt.fb.w, self.rt.fb.h)

class _quiet:

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = io.StringIO()

    def __exit__(self, *a):
        sys.stdout = self._old
        return False

def open_module(path, audio=True):

    return Session(path, audio=audio).boot()
