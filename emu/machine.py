
import struct
import collections
from unicorn import *
from unicorn.arm_const import *

PAGE = 0x1000

def align_up(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)

class Region:
    def __init__(self, name, base, size):
        self.name, self.base, self.size = name, base, size

    def __contains__(self, a):
        return self.base <= a < self.base + self.size

    def __repr__(self):
        return f"<{self.name} {self.base:#x}+{self.size:#x}>"

class Bump:

    def __init__(self, base, size):
        self.base, self.size, self.cur = base, size, base
        self.blocks = {}
        self.freelist = []

    MAX_ALLOC = 0x0400_0000

    def alloc(self, n, tag="", strict=True):
        if n > self.MAX_ALLOC:
            if strict:
                raise MemoryError(f"单次分配 {n:#x} 过大（tag={tag}）")
            return 0
        n = align_up(max(n, 1), 16)
        for i, (p, sz) in enumerate(self.freelist):
            if sz >= n:
                if sz > n:
                    self.freelist[i] = (p + n, sz - n)
                else:
                    self.freelist.pop(i)
                self.blocks[p] = (n, tag)
                return p
        if self.cur + n > self.base + self.size:
            if strict:
                raise MemoryError(f"模拟堆耗尽（已用 {self.cur - self.base:#x} / {self.size:#x}）")
            return 0
        p, self.cur = self.cur, self.cur + n
        self.blocks[p] = (n, tag)
        return p

    def free(self, p):
        ent = self.blocks.pop(p, None)
        if not ent:
            return
        n = ent[0]
        if p + n == self.cur:
            self.cur = p
            return
        import bisect as _b
        i = _b.bisect_left([a for a, _ in self.freelist], p)
        self.freelist.insert(i, (p, n))

        j = i
        while j + 1 < len(self.freelist) and                self.freelist[j][0] + self.freelist[j][1] == self.freelist[j + 1][0]:
            a, sa = self.freelist[j]
            b, sb = self.freelist.pop(j + 1)
            self.freelist[j] = (a, sa + sb)
        while j > 0 and self.freelist[j - 1][0] + self.freelist[j - 1][1] == self.freelist[j][0]:
            a, sa = self.freelist[j - 1]
            b, sb = self.freelist.pop(j)
            self.freelist[j - 1] = (a, sa + sb)
            j -= 1

class Machine:
    NULL_GUARD = 0x00020000
    RO_DEFAULT = 0x01000000

    RW_BASE = 0x20000000
    STACK_BASE = 0x30000000
    STACK_SIZE = 0x00100000
    HEAP_BASE = 0x40000000
    HEAP_SIZE = 0x10000000
    NATIVE_BASE = 0x51000000
    NATIVE_SIZE = 0x00010000
    TRAP_BASE = 0x50000000
    TRAP_SIZE = 0x00040000
    DATA_BASE = 0x60000000
    DATA_SIZE = 0x00400000
    RETURN_MAGIC = 0x7FFF0000

    def __init__(self, mod, trace=False, autotable=False):
        self.mod = mod
        self.trace = trace
        self.autotable = autotable
        self.log_calls = True
        self.auto_objs = {}
        self.auto_slots = set()
        self.le = mod.endian == "LE"
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM |
                     (UC_MODE_LITTLE_ENDIAN if self.le else UC_MODE_BIG_ENDIAN))
        try:
            self.uc.ctl_set_cpu_model(UC_CPU_ARM_926)
        except Exception:
            pass

        self.ro_base = mod.load_base or self.RO_DEFAULT
        self.ro_size = align_up(max(mod.image_size, len(mod.ro)))
        self.rw_size = align_up(max(mod.rw_size, len(mod.rw)) + 0x10000)

        self.rw_base = (self.rw_place(mod) if mod.load_base else self.RW_BASE)

        self.r9_live = self.rw_base

        self._map()
        self.heap = Bump(self.HEAP_BASE, self.HEAP_SIZE)
        self.data = Bump(self.DATA_BASE, self.DATA_SIZE)

        self.trap_names = {}
        self.trap_handlers = {}
        self.trap_hits = {}
        self.next_trap = 0

        self.call_log = collections.deque(maxlen=4096)
        self.stopped = False
        self.in_emu = False
        self._resume_pc = None
        self.last_trap = -1
        self.same_trap = 0
        self.exit_reason = None

        self.hist = collections.deque(maxlen=64)
        self.uc.hook_add(UC_HOOK_CODE, self._on_trap,
                         begin=self.TRAP_BASE, end=self.TRAP_BASE + self.TRAP_SIZE - 1)
        self.next_native = 0
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_bad_mem)
        self.uc.hook_add(UC_HOOK_INTR, self._on_intr)
        self.semihost_out = bytearray()
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self._on_bad_insn)

    @staticmethod
    def rw_place(mod):

        end = getattr(mod, "image_end", 0) or 0
        lo = mod.load_base
        hi = mod.load_base + max(mod.image_size, len(mod.ro)) + 0x10000
        if lo < end < hi:
            return end
        return mod.load_base + align_up(len(mod.ro), 16)

    def _map(self):
        u = self.uc

        u.mem_map(self.ro_base, self.ro_size, UC_PROT_ALL)
        u.mem_write(self.ro_base, self.mod.ro)
        if self.mod.load_base:

            extra = align_up(self.rw_base - self.ro_base + self.rw_size) - self.ro_size
            if extra > 0:
                u.mem_map(self.ro_base + self.ro_size, extra, UC_PROT_ALL)
                self.ro_size += extra
        else:
            u.mem_map(self.rw_base, self.rw_size)
        u.mem_write(self.rw_base, self.mod.rw)
        u.mem_map(self.STACK_BASE, self.STACK_SIZE)
        u.mem_map(self.HEAP_BASE, self.HEAP_SIZE)

        u.mem_map(self.DATA_BASE, self.DATA_SIZE, UC_PROT_READ | UC_PROT_WRITE)

        u.mem_protect(self.DATA_BASE, 0x1000, UC_PROT_ALL)
        u.mem_map(self.TRAP_BASE, self.TRAP_SIZE, UC_PROT_READ | UC_PROT_EXEC)
        u.mem_map(self.NATIVE_BASE, self.NATIVE_SIZE, UC_PROT_READ | UC_PROT_EXEC)

        thumb_bxlr = b"\x70\x47" if self.le else b"\x47\x70"
        nop = b"\x00\xbf" if self.le else b"\xbf\x00"
        u.mem_write(self.TRAP_BASE, (thumb_bxlr + nop) * (self.TRAP_SIZE // 4))
        u.mem_map(self.RETURN_MAGIC & ~(PAGE - 1), PAGE, UC_PROT_READ | UC_PROT_EXEC)

        u.mem_map(0, self.NULL_GUARD, UC_PROT_ALL)

        self.null_calls = collections.Counter()
        u.hook_add(UC_HOOK_CODE, self._on_null_call, begin=0, end=self.NULL_GUARD - 1)

        self.regions = [
            Region("RO", self.ro_base, self.ro_size),
            Region("RW", self.rw_base, self.rw_size),
            Region("STACK", self.STACK_BASE, self.STACK_SIZE),
            Region("HEAP", self.HEAP_BASE, self.HEAP_SIZE),
            Region("DATA", self.DATA_BASE, self.DATA_SIZE),
            Region("TRAP", self.TRAP_BASE, self.TRAP_SIZE),
        ]

    def where(self, a):
        for r in self.regions:
            if a in r:
                return f"{r.name}+{a - r.base:#x}"
        return f"{a:#x}"

    _F = property(lambda self: "<" if self.le else ">")

    def r32(self, a):
        return struct.unpack(self._F + "I", self.uc.mem_read(a, 4))[0]

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack(self._F + "I", v & 0xFFFFFFFF))

    def r16(self, a):
        return struct.unpack(self._F + "H", self.uc.mem_read(a, 2))[0]

    def r8(self, a):
        return self.uc.mem_read(a, 1)[0]

    def cstr(self, a, maxlen=512):
        if not a:
            return None
        out = bytearray()
        while len(out) < maxlen:
            b = self.uc.mem_read(a + len(out), 1)[0]
            if b == 0:
                break
            out.append(b)
        return bytes(out)

    def reg(self, i):
        return self.uc.reg_read(UC_ARM_REG_R0 + i)

    def setreg(self, i, v):
        self.uc.reg_write(UC_ARM_REG_R0 + i, v & 0xFFFFFFFF)

    def arg(self, i):

        if i < 4:
            return self.reg(i)
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        return self.r32(sp + (i - 4) * 4)

    def lr(self):
        return self.uc.reg_read(UC_ARM_REG_LR)

    def ret(self, v=0):
        self.setreg(0, v)

    def new_trap(self, name, handler=None):
        i = self.next_trap
        self.next_trap += 1
        if i * 4 >= self.TRAP_SIZE:
            raise RuntimeError("trap slots exhausted")
        self.trap_names[i] = name
        if handler:
            self.trap_handlers[i] = handler
        return self.TRAP_BASE + i * 4 + 1

    def native_const(self, name, value):

        a = self.NATIVE_BASE + self.next_native
        self.next_native += 8
        if self.next_native > self.NATIVE_SIZE:
            raise RuntimeError("原生桩区用尽")
        op = b"\x00\x48\x70\x47" if self.le else b"\x48\x00\x47\x70"
        self.uc.mem_write(a, op + (value & 0xFFFFFFFF).to_bytes(4, "little" if self.le else "big"))
        self.trap_names[("native", a)] = name
        return a | 1

    def read_upto(self, addr, n):

        if not addr:
            return b""
        for lo, hi, _ in self.uc.mem_regions():
            if lo <= addr <= hi:
                n = min(n, hi - addr + 1)
                break
        while n > 0:
            try:
                return bytes(self.uc.mem_read(addr, n))
            except Exception:
                n //= 2
        return b""

    def new_table(self, name, nslots, getters=False):

        addr = self.data.alloc(nslots * 4, name)
        for k in range(nslots):
            p = self.new_trap(f"{name}+{k * 4:#x}")
            if getters:
                self.auto_slots.add((p & ~1 - self.TRAP_BASE) // 4)
            self.w32(addr + 4 * k, p)
        return addr

    def _on_trap(self, uc, address, size, user):

        idx = (address - self.TRAP_BASE) // 4
        self.trap_hits[idx] = self.trap_hits.get(idx, 0) + 1

        if idx == self.last_trap:
            self.same_trap += 1
        else:
            self.last_trap, self.same_trap = idx, 0
        h = self.trap_handlers.get(idx)
        if self.log_calls:
            name = self.trap_names.get(idx, f"trap#{idx}")
            lr = uc.reg_read(UC_ARM_REG_LR)
            args = [self.reg(i) for i in range(4)]
            self.call_log.append((name, args, lr))
            if self.trace:
                print(f"  API {name:28s} ({args[0]:#x}, {args[1]:#x}, "
                      f"{args[2]:#x}, {args[3]:#x})  <- {self.where(lr & ~1)}")
        if h:
            h(self)
        elif idx in self.auto_slots:
            self.ret(self.auto_result(idx, name))
        else:
            self.ret(0)

    def auto_result(self, idx, name):

        if idx not in self.auto_objs:
            self.auto_objs[idx] = self.new_table(f"obj<{name}>", 96)
        return self.auto_objs[idx]

    def enable_pc_trace(self):
        self.uc.hook_add(UC_HOOK_BLOCK, lambda uc, a, sz, u: self.hist.append((a, sz)))

    def dump_hist(self, n=20):
        print("  最近执行的基本块:")
        for a, sz in list(self.hist)[-n:]:
            print(f"    {self.where(a)}  (block size {sz})")

    def _on_null_call(self, uc, address, size, user):

        lr = uc.reg_read(UC_ARM_REG_LR)
        self.null_calls[lr & ~1] += 1
        self.setreg(0, 0)
        self._resume_pc = lr
        uc.emu_stop()

    SH_WRITEC, SH_WRITE0, SH_WRITE, SH_READC, SH_EXIT = 0x03, 0x04, 0x05, 0x07, 0x18

    def _on_intr(self, uc, intno, user):
        if intno != 2:
            return
        pc = uc.reg_read(UC_ARM_REG_PC)
        thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
        try:
            imm = (uc.mem_read(pc - 2, 1)[0] if thumb
                   else struct.unpack("<I", uc.mem_read(pc - 4, 4))[0] & 0xFFFFFF)
        except Exception:
            return
        if imm != 0xAB:
            return
        op, arg = self.reg(0), self.reg(1)
        if op == self.SH_WRITEC:
            self.semihost_out += uc.mem_read(arg, 1)
        elif op == self.SH_WRITE0:
            self.semihost_out += self.cstr(arg) or b""
        elif op == self.SH_WRITE:
            p = self.r32(arg + 4); n = self.r32(arg + 8)
            self.semihost_out += uc.mem_read(p, min(n, 0x10000))
            self.setreg(0, 0)
        elif op == self.SH_EXIT:
            self.exit_reason = "模块调用了 semihosting SYS_EXIT"
            uc.emu_stop()
        else:
            self.setreg(0, 0)
        if len(self.semihost_out) > 1 << 20:
            del self.semihost_out[:1 << 19]

    _ACCESS = {UC_MEM_READ_UNMAPPED: "READ", UC_MEM_WRITE_UNMAPPED: "WRITE",
               UC_MEM_FETCH_UNMAPPED: "FETCH", UC_MEM_READ_PROT: "READ(prot)",
               UC_MEM_WRITE_PROT: "WRITE(prot)", UC_MEM_FETCH_PROT: "FETCH(prot)"}

    def _on_bad_mem(self, uc, access, address, size, value, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        kind = self._ACCESS.get(access, str(access))

        self.exit_reason = f"BAD MEM {kind} @{address:#x} size={size} pc={self.where(pc)}"
        self.stopped = True
        uc.emu_stop()
        return False

    def _on_bad_insn(self, uc, user):
        pc = uc.reg_read(UC_ARM_REG_PC)
        self.exit_reason = f"BAD INSN @ {self.where(pc)}"
        self.stopped = True
        uc.emu_stop()
        return False

    def stub(self, va, retval=0, name=None):

        addr = va & ~1

        def h(uc, address, size, user):
            self.setreg(0, retval)
            lr = uc.reg_read(UC_ARM_REG_LR)
            uc.reg_write(UC_ARM_REG_PC, lr | 1)
        self.uc.hook_add(UC_HOOK_CODE, h, begin=addr, end=addr)
        self.stubs = getattr(self, 'stubs', {})
        self.stubs[addr] = (name or f"stub@{addr:#x}", retval)

    BUDGET = 100_000_000

    def call(self, addr, args=(), timeout=0, count=0):

        if self.in_emu:
            raise RuntimeError("不能在 Unicorn 回调内嵌套 call()，请使用延迟队列")
        u = self.uc
        for i, a in enumerate(args[:4]):
            self.setreg(i, a)
        u.reg_write(UC_ARM_REG_SP, self.STACK_BASE + self.STACK_SIZE - 0x1000)
        u.reg_write(UC_ARM_REG_R9, self.r9_live)
        u.reg_write(UC_ARM_REG_LR, self.RETURN_MAGIC | 1)
        thumb = addr & 1
        start = addr | 1 if thumb else addr
        cpsr = u.reg_read(UC_ARM_REG_CPSR)
        u.reg_write(UC_ARM_REG_CPSR, cpsr | 0x20 if thumb else cpsr & ~0x20)
        self.exit_reason = None
        self.in_emu = True
        self._resume_pc = None
        resumes = 0
        try:
            while True:
                try:
                    u.emu_start(start, self.RETURN_MAGIC, timeout=timeout,
                                count=count or self.BUDGET)
                except UcError as e:
                    if self.exit_reason is None:
                        self.exit_reason = f"{e} pc={self.where(u.reg_read(UC_ARM_REG_PC))}"
                    break
                if self._resume_pc is None:
                    break
                start, self._resume_pc = self._resume_pc, None
                cpsr = u.reg_read(UC_ARM_REG_CPSR)
                u.reg_write(UC_ARM_REG_CPSR,
                            cpsr | 0x20 if start & 1 else cpsr & ~0x20)
                start |= 1 if start & 1 else 0
                resumes += 1
                if resumes > 20000:
                    self.exit_reason = "空指针调用过多，放弃"
                    break
        finally:
            self.in_emu = False
        if self.exit_reason is None:

            self.r9_live = u.reg_read(UC_ARM_REG_R9)
        return self.reg(0)
