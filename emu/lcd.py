
import array
import struct, zlib

class Framebuffer:
    def __init__(self, mach, w, h):
        self.mach, self.w, self.h = mach, w, h
        self.E = "<" if mach.le else ">"
        self.bytes = w * h * 2
        self.buf = mach.heap.alloc(self.bytes, "LCD")
        mach.uc.mem_write(self.buf, b"\x00" * self.bytes)

        self.img = mach.heap.alloc(16, "VmImageType(screen)")
        mach.w32(self.img, self.buf)
        self.wide = False
        self.write_header()
        self.frames = 0

    def write_header(self):
        if self.wide:
            self.mach.uc.mem_write(self.img + 4, struct.pack(self.E + "II", self.w, self.h) + b"\x00" * 4)
        else:
            self.mach.uc.mem_write(self.img + 4, struct.pack(self.E + "HH", self.w, self.h) + b"\x00" * 8)

    def resize(self, w, h):

        self.w, self.h = w, h
        self.bytes = w * h * 2
        self.mach.uc.mem_write(self.buf, b"\x00" * self.bytes)
        self.write_header()

    def fill_rect(self, x, y, w, h, color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = struct.pack(self.E + "H", color & 0xFFFF) * (x1 - x0)
        for yy in range(y0, y1):
            self.mach.uc.mem_write(self.buf + (yy * self.w + x0) * 2, row)

    def raw565(self):

        raw = bytes(self.mach.uc.mem_read(self.buf, self.bytes))
        if self.mach.le:
            return raw
        a = array.array("H")
        a.frombytes(raw)
        a.byteswap()
        return a.tobytes()

    def rgb888(self):

        raw = self.raw565()
        lut = self._lut()
        fmt = "<" + str(self.w * self.h) + "H"
        return b"".join([lut[v] for v in struct.unpack(fmt, raw)])

    _LUT = None

    @classmethod
    def _lut(cls):

        if cls._LUT is None:
            t = []
            for v in range(1 << 16):
                r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                t.append(bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                                (b << 3) | (b >> 2))))
            cls._LUT = t
        return cls._LUT

    def _rotated(self, rotate=0):

        rgb, w, h = self.rgb888(), self.w, self.h
        if rotate not in (90, 180, 270):
            return rgb, w, h
        src = rgb
        if rotate == 180:
            out = bytearray(len(src))
            for i in range(w * h):
                j = w * h - 1 - i
                out[i * 3:i * 3 + 3] = src[j * 3:j * 3 + 3]
            return bytes(out), w, h
        nw, nh = h, w
        out = bytearray(len(src))
        for y in range(nh):
            row = y * nw
            for x in range(nw):
                sx, sy = (y, h - 1 - x) if rotate == 90 else (w - 1 - y, x)
                j = sy * w + sx
                i = row + x
                out[i * 3:i * 3 + 3] = src[j * 3:j * 3 + 3]
        return bytes(out), nw, nh

    def write_png(self, path, rotate=0):
        with open(path, "wb") as f:
            self._png(f, *self._rotated(rotate))
        return path

    def write_png_bytes(self, rotate=0):

        import io as _io
        buf = _io.BytesIO()
        self._png(buf, *self._rotated(rotate))
        return buf.getvalue()

    def _png(self, fobj, rgb, w, h):
        raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

        fobj.write(b"\x89PNG\r\n\x1a\n"
                   + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(raw, 6))
                   + chunk(b"IEND", b""))

    def nonblank(self):
        raw = bytes(self.mach.uc.mem_read(self.buf, self.bytes))
        return sum(1 for i in range(0, len(raw), 2) if raw[i] or raw[i + 1])

def rgb565_to_png(path, w, h, pixels, transparent=None):

    if transparent is None:
        raw = bytearray()
        for y in range(h):
            raw.append(0)
            for x in range(w):
                v = pixels[y * w + x]
                r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                raw += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        color, depth = 2, 8
    else:
        raw = bytearray()
        for y in range(h):
            raw.append(0)
            for x in range(w):
                i = y * w + x
                v = pixels[i]
                r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                a = 0 if (transparent is not None and i < len(pixels) and
                          pixels[i] == pixels[i] and False) else 255
                raw += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), a))
        color = 6

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    open(path, "wb").write(png)
    return path

def write_svg(path, fb, texts, scale=1):

    import base64, io, os
    tmp = path + ".bg.png"
    fb.write_png(tmp)
    b64 = base64.b64encode(open(tmp, "rb").read()).decode()
    os.remove(tmp)
    w, h = fb.w, fb.h
    esc = (lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w * scale}" '
             f'height="{h * scale}" viewBox="0 0 {w} {h}">',
             f'<image href="data:image/png;base64,{b64}" width="{w}" height="{h}" '
             f'style="image-rendering:pixelated"/>']
    for x, y, color, s, fs in texts:
        r = ((color >> 11) & 0x1F) << 3
        g = ((color >> 5) & 0x3F) << 2
        b = (color & 0x1F) << 3
        parts.append(f'<text x="{x}" y="{y + fs - 2}" font-size="{fs}" '
                     f'font-family="PingFang SC,Hiragino Sans GB,Microsoft YaHei,sans-serif" '
                     f'fill="rgb({r},{g},{b})" stroke="black" stroke-width="0.4" '
                     f'paint-order="stroke">{esc(s)}</text>')
    parts.append("</svg>")
    open(path, "w", encoding="utf-8").write("\n".join(parts))
    return path
