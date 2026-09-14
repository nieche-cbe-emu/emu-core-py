
import struct

def stride_of(w):

    return w + ((4 - w) & 3)

class ImageStore:

    wide = False

    def header_size(self):
        return 16 if self.wide else 12

    def wh(self, addr):
        m = self.mach
        if self.wide:
            return m.r32(addr + 4), m.r32(addr + 8)
        return m.r16(addr + 4), m.r16(addr + 6)

    def set_wh(self, addr, w, h, kind=0):
        if self.wide:
            self.mach.uc.mem_write(addr + 4, struct.pack(self.E + "IIB", w, h, kind) + b"\x00" * 3)
        else:
            self.mach.uc.mem_write(addr + 4, struct.pack(self.E + "HHB", w & 0xFFFF, h & 0xFFFF, kind) + b"\x00" * 3)

    def __init__(self, mach):
        self.mach = mach
        self.E = "<" if mach.le else ">"
        self.masks = {}
        self.by_stream = {}

    def upload(self, img, out_addr=None):
        m = self.mach
        w, h = img["width"], img["height"]
        st = stride_of(w)
        pix = img["rgb565"]
        data = m.heap.alloc(max(st * h * 2, 2), "img_data")
        rows = []
        for y in range(h):
            row = pix[y * w:(y + 1) * w]
            row = list(row) + [0] * (st - w)
            rows.extend(row)
        m.uc.mem_write(data, struct.pack(f"{self.E}{st * h}H", *rows))
        vt = out_addr or m.heap.alloc(self.header_size(), "VmImageType")
        m.w32(vt, data)
        self.set_wh(vt, w, h)

        src_mask = None
        tidx = img.get("transparent")
        if tidx is not None and img.get("index") is not None:
            idx = img["index"]
            src_mask = [1 if v == tidx else 0 for v in idx]
        elif img.get("alpha"):
            src_mask = img["alpha"]
        if src_mask:
            mask = bytearray(st * h)
            for y in range(h):
                row = y * w
                for x in range(w):
                    if src_mask[row + x]:
                        mask[y * st + x] = 1
                for x in range(w, st):
                    mask[y * st + x] = 1
            self.masks[data] = bytes(mask)
        return vt

    def info(self, addr):
        m = self.mach
        if not addr:
            return None
        data = m.r32(addr)
        w, h = self.wh(addr)
        return data, w, h, stride_of(w)

    def blit(self, src, dst, dx, dy, w=None, h=None, sx=0, sy=0, alpha=False):

        m = self.mach
        s = self.info(src)
        d = self.info(dst)
        if not s or not d or not s[0] or not d[0]:
            return
        sdata, sw, sh, sst = s
        ddata, dw, dh, dst_st = d
        w = sw if w is None else w
        h = sh if h is None else h

        w = min(w, dw)
        if dy + h > dh:
            h = dh - dy
        if sy + h > sh:
            h = sh - sy
        if dx + w > dst_st:
            w = dst_st - dx
        if sx + w > sst:
            w = sst - sx
        if dx < 0:
            sx -= dx; w += dx; dx = 0
        if dy < 0:
            sy -= dy; h += dy; dy = 0
        if w <= 0 or h <= 0:
            return

        mask = self.masks.get(sdata) if alpha else None

        srow, drow = sst * 2, dst_st * 2
        sbuf = bytes(m.uc.mem_read(sdata + sy * srow, h * srow))
        dbuf = bytearray(m.uc.mem_read(ddata + dy * drow, h * drow))
        sx2, dx2, w2 = sx * 2, dx * 2, w * 2
        for row in range(h):
            so = row * srow + sx2
            do = row * drow + dx2
            line = sbuf[so:so + w2]
            if mask is None:
                dbuf[do:do + w2] = line
                continue
            mrow = mask[(sy + row) * sst + sx:(sy + row) * sst + sx + w]
            if not any(mrow):
                dbuf[do:do + w2] = line
                continue
            for i, t in enumerate(mrow):
                if not t:
                    dbuf[do + i * 2:do + i * 2 + 2] = line[i * 2:i * 2 + 2]
        m.uc.mem_write(ddata + dy * drow, bytes(dbuf))
