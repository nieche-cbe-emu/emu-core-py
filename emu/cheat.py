
WIDTHS = (1, 2, 4)

MAX_LIST = 200

class Cheat:
    def __init__(self, mach):
        self.m = mach
        self.width = 0
        self.cands = None
        self.locks = {}

    def _regions(self):
        m = self.m
        yield m.rw_base, m.rw_size
        if m.heap.cur > m.heap.base:
            yield m.heap.base, m.heap.cur - m.heap.base

    def _pack(self, width, value):
        v = value & ((1 << (8 * width)) - 1)
        return v.to_bytes(width, "little" if self.m.le else "big")

    def _read(self, addr, width):
        b = bytes(self.m.uc.mem_read(addr, width))
        return int.from_bytes(b, "little" if self.m.le else "big")

    def reset(self):
        self.width = 0
        self.cands = None

    def search(self, width, value):

        if width not in WIDTHS:
            return 0
        pat = self._pack(width, value)
        if self.cands is None or width != self.width:
            self.width = width
            out = []
            for base, size in self._regions():
                buf = bytes(self.m.uc.mem_read(base, size))
                i = buf.find(pat)
                while i >= 0:
                    if i % width == 0:
                        out.append(base + i)
                    i = buf.find(pat, i + 1)
            self.cands = out
        else:
            self.cands = [a for a in self.cands
                          if bytes(self.m.uc.mem_read(a, width)) == pat]
        return len(self.cands)

    def results(self, limit=MAX_LIST):

        if not self.cands:
            return []
        return [(a, self._read(a, self.width)) for a in self.cands[:limit]]

    def write(self, addr, width, value):
        if width not in WIDTHS:
            return False
        try:
            self.m.uc.mem_write(addr, self._pack(width, value))
        except Exception:
            return False
        return True

    def lock(self, addr, width, value, on=True):
        if not on:
            self.locks.pop(addr, None)
        elif width in WIDTHS:
            self.locks[addr] = (width, value)

    def apply_locks(self):
        for addr, (width, value) in sorted(self.locks.items()):
            self.write(addr, width, value)
