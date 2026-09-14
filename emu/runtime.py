
import collections
import os
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR
from .machine import Machine
from .hostabi import vm_printf
from . import vmspec
from . import api as apireg
from .lcd import Framebuffer, write_svg
from .vfs import Vfs
from . import paths
from .images import ImageStore
from .font import load as load_font
from .audio import Audio

class Runtime:
    def __init__(self, mod, trace=True, quiet_log=False, screen=(240, 400),
                 fsroot=None, fsbase="assets/fatfs", trace_fs=True, audio=False,
                 pc_trace=False):
        self.mod = mod
        self.mach = m = Machine(mod, trace=False)
        self.mach.rt = self

        if pc_trace or trace:
            self.mach.enable_pc_trace()
        self.trace = trace
        self.quiet_log = quiet_log
        self.logs = []
        self.unimpl = collections.Counter()
        self.host_errors = collections.Counter()
        self.old_syscalls = collections.Counter()
        self.tick_spin = 0
        self.style = None
        self.managers = {}
        self.screen_w, self.screen_h = screen
        self.state = {}
        self.net_responses = {}

        init_map = {}
        for off, (nm, _t) in vmspec.MGR["VmManagerTag"].items():
            key = nm.lower().replace("vminit", "").replace("init", "", 1)
            if "init" not in nm.lower():
                continue
            for goff, (gnm, _tag) in vmspec.SYS.items():
                if gnm.lower().replace("vmget", "").replace("get", "", 1) == key:
                    init_map[off] = goff
                    break

        self.sys_tbl = m.data.alloc(vmspec.SIZE["VmManagerTag"], "VmManager")
        for off in range(0, vmspec.SIZE["VmManagerTag"], 4):
            ent = vmspec.MGR["VmManagerTag"].get(off)
            name = ent[0] if ent else f"VmManager+{off:#x}"
            if off in vmspec.SYS:
                h = self._make_getter(off)
            elif off in init_map:
                h = self._make_initer(init_map[off])
            else:
                h = lambda mc: mc.ret(0)
            m.w32(self.sys_tbl + off, m.new_trap(name, h))

        self.host = m.data.alloc(0x10, "VmSysCallRegParam")
        m.w32(self.host + 0x08, self.sys_tbl)

        m.w32(self.host + 0x0c, m.new_trap("gm_TRACE", self._vm_log))
        self.fb = Framebuffer(m, self.screen_w, self.screen_h)
        self.images = ImageStore(m)
        self.frame_hook = None
        self.vfs = Vfs(fsroot or paths.fs_dir(mod.name), fsbase)
        self.trace_fs = trace_fs
        self.finds = {}
        self.next_find = 1
        self.screens = []
        self.pending = []
        self._installed = {}
        self._methods = {}
        self._co_frames = []
        self._co_trap = None
        self.font = load_font()
        self.audio = Audio(self, outdir=f"out/{mod.name}/audio", play=audio)
        self.tick = 0
        self.frame_ms = 40
        self.timers = {}
        self._next_timer = 1
        self.text_layer = []
        self.pointer = (0, 0)
        self.keys_down = 0
        self.keys_up = 0
        self.keys_hold = 0
        self.touch_down = self.touch_up = self.touch_hold = self.touch_drag = 0
        self.mod_cb0 = self.mod_cb1 = 0

    def _make_getter(self, off):
        def getter(mc):
            mc.ret(self.get_manager(off))
        return getter

    def _make_initer(self, getoff):
        def initer(mc):
            dst = mc.arg(0)
            src = self.get_manager(getoff)
            tag = vmspec.SYS[getoff][1]
            n = vmspec.SIZE.get(tag, 0x400) or 0x400
            if dst:
                mc.uc.mem_write(dst, bytes(mc.uc.mem_read(src, n)))
            mc.ret(0)
        return initer

    OLD_GAMEMGR_SYSOFF = 0x090
    OLD_GAMEMGR_SHIFT = 0x1c

    def _old_game_manager(self):

        so = self.OLD_GAMEMGR_SYSOFF
        if so in self.managers:
            return self.managers[so]
        base = self.get_manager(0x084)
        tag = vmspec.SYS[0x084][1]
        size = (vmspec.SIZE.get(tag, 0x400) or 0x400) + 0x100
        sh = self.OLD_GAMEMGR_SHIFT
        addr = self.mach.data.alloc(size + sh, "GameManagerOld@old")
        for off in range(0, size, 4):
            self.mach.w32(addr + sh + off, self.mach.r32(base + off))
        self.managers[so] = addr
        return addr

    def get_manager(self, sysoff):
        if sysoff in self.managers:
            return self.managers[sysoff]
        if (sysoff == self.OLD_GAMEMGR_SYSOFF
                and getattr(self, "style", None) == self.ENTRY_OLD):
            return self._old_game_manager()
        getter, tag = vmspec.SYS[sysoff]
        return self._build_manager(sysoff, tag, tag or f"mgr{sysoff:02x}")

    def manager_by_tag(self, tag):

        tm = self.__dict__.setdefault("tag_managers", {})
        if tag not in tm:
            tm[tag] = self._build_manager(None, tag, tag)
        return tm[tag]

    def _build_manager(self, key, tag, name):
        m = self.mach

        size = (vmspec.SIZE.get(tag, 0x400) or 0x400) + 0x100
        addr = m.data.alloc(size, name)
        if key is not None:
            self.managers[key] = addr
        table = vmspec.MGR.get(tag, {})
        for off in range(0, size, 4):
            ent = table.get(off)
            nm = ent[0] if ent else f"{(tag or 'mgr')[:-3]}+{off:#x}"
            const = self.NATIVE_CONSTS.get(nm)

            if os.environ.get("NO_NATIVE"):
                const = None
            if const is not None:

                m.w32(addr + off, m.native_const(nm, const(self)))
                continue
            m.w32(addr + off, m.new_trap(f"{nm}", self._make_method(tag, off, nm)))
        return addr

    NATIVE_CONSTS = {
        "VMGetCurrMainScreenImage": lambda rt: rt.fb.img,
        "VMGetLCDBuffer":           lambda rt: rt.fb.buf,
        "VmGetScreenWidth":         lambda rt: rt.screen_w,
        "GetScreenWidth":           lambda rt: rt.screen_w,
        "VmGetScreenHeight":        lambda rt: rt.screen_h,
        "GetScreenHeight":          lambda rt: rt.screen_h,
    }

    def _make_method(self, tag, off, nm):
        fn = apireg.lookup(tag, nm)

        def method(mc):
            if self.trace and self.mach.log_calls:
                a = [mc.arg(i) for i in range(4)]
                print(f"  {nm:34s}({a[0]:#x}, {a[1]:#x}, {a[2]:#x}, {a[3]:#x})"
                      f"   <- {mc.where(mc.lr() & ~1)}")
            if fn:
                try:
                    fn(mc, self)
                except Exception as e:

                    self.host_errors[(nm, type(e).__name__, str(e)[:60])] += 1
                    mc.ret(0)
            else:
                self.unimpl[(tag, off, nm)] += 1
                mc.ret(0)
        return method

    S_INIT, S_DESTROY, S_LOGIC, S_RENDER, S_PAUSE, S_RESUME, S_LOADRES = range(7)
    SPIN_PER_MS = 64

    def push_screen(self, scr, param, flag):
        m = self.mach
        self.screens.append((scr, param, flag))

        line = (f"  >> vmAddScreen VmScreen@{scr:#x}: " + " ".join(
            f"{f}={m.r32(scr + 4 * i):#x}" for i, f in enumerate(
                ("init", "destroy", "logic", "render", "pause", "resume", "loadRes"))))
        self.logs.append(line)
        if not self.quiet_log:
            print(line)

        self.defer(m.r32(scr + 4 * self.S_INIT), (param,), "screenInit")
        self.defer(m.r32(scr + 4 * self.S_LOADRES), (param,), "screenLoadResource")

    def defer(self, fn, args=(), tag=""):

        if fn:
            self.pending.append((fn, tuple(args), tag))

    def pump(self, limit=64):
        n = 0
        while self.pending and n < limit:
            fn, args, tag = self.pending.pop(0)
            if self.trace and tag:
                print(f"  >> 回调 {tag} @{fn:#x}{args}")
            self.mach.call(fn, args)
            n += 1
        return n

    def live_screens(self):

        out = []
        for ent in self.screens:
            scr = ent[0]
            if scr and any(self.mach.r32(scr + 4 * k) for k in range(7)):
                out.append(ent)
        return out

    def call_screen(self, scr, slot, *args):
        fn = self.mach.r32(scr + 4 * slot)
        if not fn:
            return None
        if os.environ.get("REGDBG"):
            import sys as _s
            r = ",".join(hex(self.mach.reg(i)) for i in range(13))
            print(f"REG slot={slot} fn={fn:#x} args={list(args)} r0-12={r}", file=_s.stderr)
        return self.mach.call(fn, args)

    def start_timer(self, ms, cb, param):
        if not cb:
            return 0
        tid = self._next_timer
        self._next_timer = self._next_timer % 0x7FFF + 1
        self.timers[tid] = [self.tick + max(ms, 1), cb, param]
        return tid

    def stop_timer(self, tid):
        self.timers.pop(tid, None)

    def _fire_timers(self):
        due = [(t, v) for t, v in self.timers.items() if v[0] <= self.tick]
        for tid, (_, cb, param) in sorted(due, key=lambda kv: kv[1][0]):
            self.timers.pop(tid, None)
            self.defer(cb, (param,), f"timer#{tid}")

    NO_EVENT = 0xFF

    def frame(self, event=None, data=0):
        if event is None:
            event = self.NO_EVENT
        if self.style == self.ENTRY_OLD and not self.live_screens():

            try:
                self.mach.call(self.mod_cb0)
            except Exception:
                pass
            self.pump()
            self.present()
            return

        self.tick += self.frame_ms
        self.audio.tick()
        self._fire_timers()
        self.pump()
        for scr, param, _ in reversed(self.screens):
            if self.call_screen(scr, self.S_LOGIC, param, event, data):
                break
        for scr, param, _ in self.screens:
            self.call_screen(scr, self.S_RENDER, param)
        self.present()

    def net_response(self, url):

        return self.net_responses.get(url) if self.net_responses else None

    def input_query(self, mc, kind="down"):
        mask = mc.arg(0)
        bits = {"down": self.keys_down, "up": self.keys_up,
                "hold": self.keys_hold}.get(kind, 0)
        return 1 if (bits & mask) else 0

    def press(self, mask):
        self.keys_down |= mask
        self.keys_hold |= mask
        self.keys_up &= ~mask

    def release(self, mask=None):
        if mask is None:
            self.keys_up = self.keys_down
            self.keys_down = self.keys_hold = 0
        else:
            self.keys_down &= ~mask
            self.keys_hold &= ~mask
            self.keys_up |= mask

    exit_requested = False

    def clear_input(self):
        self.keys_down = self.keys_up = self.keys_hold = 0

    def note_text(self, s, x, y, color, size=12):

        try:
            txt = s.decode("gb18030", "replace") if isinstance(s, bytes) else str(s)
        except Exception:
            return
        if txt.strip():
            self.text_layer.append((x, y, color, txt, size))

    def write_svg(self, path, scale=1):
        return write_svg(path, self.fb, self.text_layer, scale)

    def trace_io(self, msg):
        if self.trace_fs:
            print(f"  [fs] {msg}")

    def lcd_buffer(self):
        return self.fb.buf

    def lcd_image(self):
        return self.fb.img

    ADOPT_BEFORE_FRAME = 4
    ADOPT_RANGE = (80, 320, 80, 480)

    def maybe_adopt_screen(self, w, h):

        lo_w, hi_w, lo_h, hi_h = self.ADOPT_RANGE
        if (self.fb.frames >= self.ADOPT_BEFORE_FRAME
                or (w, h) == (self.screen_w, self.screen_h)
                or not (lo_w <= w <= hi_w and lo_h <= h <= hi_h)
                or w * h * 2 > self.fb.bytes):
            return
        self.screen_w, self.screen_h = w, h
        self.fb.resize(w, h)
        self.logs.append(f"按模块的清屏认领屏幕尺寸：{w}x{h}")

    def fill_rect(self, x, y, w, h, color):
        if x <= 0 and y <= 0:
            self.maybe_adopt_screen(w, h)

        if x <= 0 and y <= 0 and w >= self.screen_w and h >= self.screen_h:
            self.text_layer.clear()
        self.fb.fill_rect(x, y, w, h, color)

    def present(self):
        self.fb.frames += 1
        if self.frame_hook:
            self.frame_hook(self.fb)

    def install(self, addr, name, fn):

        key = (addr, name)
        if key not in self._installed:
            self._installed[key] = self.mach.new_trap(name, lambda mc, _f=fn: _f(mc, self))
        self.mach.w32(addr, self._installed[key])
        return self._installed[key]

    def method(self, name, fn):

        t = self._methods.get(name)
        if t is None:
            t = self._methods[name] = self.mach.new_trap(name, lambda mc, _f=fn: _f(mc, self))
        return t

    def cocall(self, mc, gen):
        self._co_step(mc, gen, None, mc.lr(), mc.uc.reg_read(UC_ARM_REG_SP), first=True)

    def _co_step(self, mc, gen, val, lr, sp, first=False):
        try:
            fn, args = gen.send(val)
        except StopIteration as e:
            mc.ret((e.value or 0) & 0xFFFFFFFF)
            if not first:
                mc.uc.reg_write(UC_ARM_REG_SP, sp)
                mc.uc.reg_write(UC_ARM_REG_LR, lr)
                mc._resume_pc = lr
                mc.uc.emu_stop()
            return
        if self._co_trap is None:
            self._co_trap = mc.new_trap("<hostco-return>", self._co_return)
        extra = [a & 0xFFFFFFFF for a in args[4:]]
        nsp = (sp - 4 * len(extra)) & ~7 if extra else sp
        for i, a in enumerate(extra):
            mc.w32(nsp + 4 * i, a)
        for i, a in enumerate(args[:4]):
            mc.setreg(i, a & 0xFFFFFFFF)
        mc.uc.reg_write(UC_ARM_REG_SP, nsp)
        self._co_frames.append((gen, lr, sp))
        mc.uc.reg_write(UC_ARM_REG_LR, self._co_trap)
        mc._resume_pc = fn
        mc.uc.emu_stop()

    def _co_return(self, mc):

        if mc._resume_pc is not None or not self._co_frames:
            return
        gen, lr, sp = self._co_frames.pop()
        self._co_step(mc, gen, mc.reg(0), lr, sp)

    def _vm_log(self, mc):
        msg = vm_printf(mc, mc.arg(0)).rstrip()
        self.logs.append(msg)
        if not self.quiet_log:
            print(f"  [trace] {msg}")
        mc.ret(len(msg))

    ENTRY_NEW, ENTRY_OLD = "new", "old"

    def entry_style(self):

        import capstone
        from capstone import arm
        mode = capstone.CS_MODE_THUMB | (0 if self.mach.le
                                         else capstone.CS_MODE_BIG_ENDIAN)
        md = capstone.Cs(capstone.CS_ARCH_ARM, mode)
        md.detail = True

        argregs = {arm.ARM_REG_R0}
        for i in md.disasm(self.mod.ro[:0x40], 0):
            if i.mnemonic == "bl":
                break
            ops = i.operands
            if i.mnemonic.startswith("str"):
                if len(ops) == 2 and ops[1].type == arm.ARM_OP_MEM:
                    if ops[1].mem.base in argregs:
                        return self.ENTRY_NEW
                    if ops[0].reg in argregs:
                        return self.ENTRY_OLD
                continue

            if (i.mnemonic in ("mov", "movs", "add", "adds")
                    and len(ops) >= 2 and ops[0].type == arm.ARM_OP_REG
                    and ops[1].type == arm.ARM_OP_REG
                    and (len(ops) == 2
                         or (ops[2].type == arm.ARM_OP_IMM and ops[2].imm == 0))
                    and ops[1].reg in argregs):
                argregs.add(ops[0].reg)
                continue
            for r in i.regs_access()[1]:
                argregs.discard(r)
        return self.ENTRY_NEW

    OLD_REGISTER_APP = 1950

    def _old_syscall(self, mc):

        sid = mc.arg(0)
        self.old_syscalls[sid] += 1
        if not hasattr(self, "_old_objs"):
            self._old_objs = {}
        if sid == self.OLD_REGISTER_APP:
            blk = mc.arg(1)
            if blk:
                self.mod_cb0 = mc.r32(blk + 0)
                self.mod_cb1 = mc.r32(blk + 4)

                mc.w32(blk + 8, self._old_helper())

            mc.ret(self._old_obj(sid))
            return

        if sid == self.OLD_FETCH:
            self._old_fetch(mc)
            return
        if sid == self.OLD_GAMELIB:
            mc.ret(self._old_gamelib())
            return
        if sid == self.OLD_GAMELIB_COPY:
            buf = mc.arg(1)
            if buf:
                tbl = self._old_gamelib()
                mc.uc.mem_write(buf, bytes(mc.uc.mem_read(tbl, self.OLD_GAMELIB_COPY_SIZE)))
            mc.ret(0)
            return
        if sid == 185:

            frame = mc.arg(1)
            if frame:
                pp, n = mc.r32(frame), mc.r32(frame + 4)
                apireg._gblock_alloc(mc, n)
                p = mc.reg(0)
                if pp:
                    mc.w32(pp, p)
                mc.uc.mem_write(frame + 8, bytes([1 if p else 0]))
            mc.ret(0)
            return
        if sid == 190:

            frame = mc.arg(1)
            pp = mc.r32(frame) if frame else 0
            if pp:
                mc.w32(pp, 0)
            mc.ret(0)
            return
        if sid in self.OLD_NET:
            self._old_net(mc, sid)
            return
        fwd = self.OLD_FORWARD.get(sid)
        if fwd:
            tag, name, kind, n = fwd
            frame = mc.arg(1)
            if isinstance(n, str):
                args = self._old_unpack(mc, frame, n)
            else:
                args = [mc.r32(frame + 4 * i) for i in range(n)] if frame else [0] * n

            for i, v in enumerate(args[:4]):
                mc.setreg(i, v)
            sp = mc.uc.reg_read(UC_ARM_REG_SP)
            extra = args[4:]
            saved = bytes(mc.uc.mem_read(sp, 4 * len(extra))) if extra else b""
            for i, v in enumerate(extra):
                mc.w32(sp + 4 * i, v)
            fn = apireg.lookup(tag, name)
            if fn:
                fn(mc, self)
            else:
                mc.ret(0)
            if extra:
                mc.uc.mem_write(sp, saved)
            return

        obj = self._old_obj(sid)
        frame = mc.arg(1)
        if frame:
            mc.w32(frame + 8, obj)
        mc.ret(obj)

    OLD_FETCH = 2001

    OLD_FORWARD = {

        183: ("VmMemoryManagerTag", "dF_InitMemory", "frame", 1),
        142: ("VmMemoryManagerTag", "mF_GetGMemoryBlockPtr", "frame", 0),
        103: ("VmMemoryManagerTag", "mF_InitMemoryBlock", "frame", 2),
        184: ("VmMemoryManagerTag", "dF_ReleaseMemory", "frame", 0),

        4: ("VmSysManagerTag", "VmEnterWinClose", "frame", 0),

        61: ("GameManagerOldTag", "Storage_Date", "frame", "IIIBB"),

        156: ("VmDFEnginelManagerTag", "DF_SendMessage", "frame", "IHI"),
        1050: ("VmIoManagerTag", "Vm_file_open", "frame", 3),
        1051: ("VmIoManagerTag", "Vm_file_write", "frame", 3),
        1052: ("VmIoManagerTag", "Vm_file_close", "frame", 1),
        1063: ("VmIoManagerTag", "Vm_file_read", "frame", 3),
        1066: ("VmIoManagerTag", "Vm_file_getfilesize", "frame", 1),
    }

    def _old_unpack(self, mc, frame, spec):

        out, off = [], 0
        for c in spec:
            w = {"I": 4, "H": 2, "B": 1}[c]
            off = (off + w - 1) // w * w
            if not frame:
                out.append(0)
            elif c == "I":
                out.append(mc.r32(frame + off))
            elif c == "H":
                v = mc.r16(frame + off)
                out.append((v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF)
            else:
                out.append(mc.r8(frame + off))
            off += w
        return out

    OLD_NET = (1004, 1005, 1006, 1030, 1031, 1057)

    def _old_net(self, mc, sid):
        frame = mc.arg(1)
        if sid == 1004 and frame:
            apireg._http(mc, self, mc.r32(frame), mc.r32(frame + 12), 0)
            mc.ret(1)
        elif sid == 1030:
            mc.ret(1)
        elif sid in (1006, 1031):
            mc.ret(1)
        else:
            mc.ret(0)

    OLD_GAMELIB = 143
    OLD_GAMELIB_COPY = 82
    OLD_GAMELIB_COPY_SIZE = 0x26c
    OLD_GAMELIB_GAP = (0x114, 8)

    gamelib_v3 = False

    def _old_gamelib(self):
        addr = self._old_objs.get("gamelib")
        if addr:
            return addr
        self.gamelib_v3 = True
        self.images.wide = self.fb.wide = True
        self.fb.write_header()
        base = self.get_manager(0x084)
        tag = vmspec.SYS[0x084][1]
        size = (vmspec.SIZE.get(tag, 0x400) or 0x400) + 0x100
        addr = self.mach.data.alloc(size, "GameManagerOld@v3")
        at, gap = self.OLD_GAMELIB_GAP
        for off in range(0, size - gap, 4):
            src = off if off < at else off + gap
            self.mach.w32(addr + off, self.mach.r32(base + src))
        self._old_gamelib_sms(addr)
        self._old_objs["gamelib"] = addr
        return addr

    OLD_SMS_DIFF, OLD_SMS_ADD = 3764, 1376

    def _old_gamelib_sms(self, addr):
        m = self.mach

        def api(tag, name):
            fn = apireg.lookup(tag, name)
            return lambda mc: fn(mc, self)

        def send_sms(mc):

            sp = mc.uc.reg_read(UC_ARM_REG_SP)
            self.defer(mc.r32(sp + 4), (1,), "smsResult")
            mc.ret(1)

        pay = m.new_trap("BILLING_GetPayNumByAppId@v3", api("VmBillingManagerTag", "BILLING_GetPayNumByAppId"))
        m.next_trap += self.OLD_SMS_DIFF // 4 - 1
        remain = m.new_trap("BILLING_GetRemainDay@v3", api("VmBillingManagerTag", "BILLING_GetRemainDay"))
        m.next_trap += self.OLD_SMS_ADD // 4 - 1
        m.new_trap("vMSendSms@v3", send_sms)
        m.w32(addr + 0x240, pay)
        m.w32(addr + 0x244, remain)

    def _old_fetch(self, mc):

        desc = mc.arg(1)
        if not desc:
            mc.ret(0)
            return
        ptr, handle, ln = mc.r32(desc), mc.r32(desc + 4), mc.r32(desc + 8)

        if ptr and ln >= 4:
            mc.w32(ptr, handle)
        elif ptr and ln == 2:
            mc.uc.mem_write(ptr, (handle & 0xFFFF).to_bytes(2, 'little' if mc.le else 'big'))
        elif ptr and ln == 1:
            mc.uc.mem_write(ptr, bytes([handle & 0xFF]))
        mc.ret(1)

    def _old_helper(self):

        if getattr(self, "_old_help", None) is None:
            self._old_help = self.mach.new_trap(
                "oldsys_helper",
                lambda mc: mc.ret(self._old_obj("helper")))
        return self._old_help

    OLD_MEM_SLOTS = {0x9c: "alloc", 0xa0: "free", 0x214: "memset"}

    def _old_mem_table(self, name):

        m = self.mach
        addr = m.new_table(name, 256)

        def alloc(mc):
            n = mc.arg(0)
            mc.ret(m.heap.alloc(max(4, n), "oldsdk") if n else 0)

        def free(mc):
            mc.ret(0)

        def memset(mc):
            p, v, n = mc.arg(0), mc.arg(1) & 0xFF, mc.arg(2)
            if p and 0 < n <= 0x400000:
                m.uc.mem_write(p, bytes([v]) * n)
            mc.ret(p)

        for off, kind in self.OLD_MEM_SLOTS.items():
            m.w32(addr + off, m.new_trap(f"{name}.{kind}",
                                         {"alloc": alloc, "free": free,
                                          "memset": memset}[kind]))
        return addr

    def _old_obj(self, sid):

        obj = self._old_objs.get(sid)
        if obj is None:
            if sid == 143:
                obj = self._old_mem_table("oldsys#143(mem)")
            else:
                obj = self.mach.new_table(f"oldsys#{sid}", 256)
            self._old_objs[sid] = obj
        return obj

    def boot(self):
        m = self.mach

        self.style = self.entry_style()

        if self.style == self.ENTRY_OLD:
            self.game_tbl = m.new_table("GameManagerOld", 0x100, getters=True)
            m.w32(self.host + 0x0c, self.game_tbl)

        tramp = m.new_trap("OldSysCall", self._old_syscall)
        m.w32(self.host + 0x00, 0xE51FF004)
        m.w32(self.host + 0x04, tramp)
        r = m.call(m.ro_base | 1, [self.host])
        cb0 = m.r32(self.host + 0x00)
        if cb0 != 0xE51FF004:
            self.mod_cb0 = cb0
            self.mod_cb1 = m.r32(self.host + 0x04)

        return r

    def app_start(self):
        r = self.mach.call(self.mod_cb0)
        self.pump()
        return r

    def app_stop(self):
        return self.mach.call(self.mod_cb1)

    def report_null_calls(self, top=8):
        nc = getattr(self.mach, "null_calls", None)
        if not nc:
            return
        print("\n调用了空函数指针（已兜底返回 0）:")
        for lr, n in nc.most_common(top):
            print(f"   调用点 {self.mach.where(lr)}  x{n}")

    def report_errors(self, top=15):
        if not self.host_errors:
            return
        print("\n宿主实现内部报错（不影响继续执行，但说明参数解读可能不对）:")
        for (nm, kind, msg), n in self.host_errors.most_common(top):
            print(f"   {nm:30s} {kind}: {msg}   x{n}")

    def report_unimpl(self, top=40):
        if not self.unimpl:
            print("\n所有被调用的 API 均已实现。")
            return
        print(f"\n未实现的宿主 API（按调用次数）:")
        for (tag, off, nm), n in self.unimpl.most_common(top):
            print(f"   {str(tag or '?'):24s} +{off:#05x}  {nm:34s} x{n}")
