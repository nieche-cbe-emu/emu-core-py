
import struct
import zlib

class ImgError(Exception):
    pass

def _lzw(data, min_code_size):

    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    dic = [bytes([i]) for i in range(clear)] + [b"", b""]
    out = bytearray()
    prev = None
    bitpos = 0
    nbits = len(data) * 8
    while bitpos + code_size <= nbits:
        byte = bitpos >> 3
        chunk = int.from_bytes(data[byte:byte + 3].ljust(3, b"\0"), "little")
        code = (chunk >> (bitpos & 7)) & ((1 << code_size) - 1)
        bitpos += code_size
        if code == clear:
            dic = [bytes([i]) for i in range(clear)] + [b"", b""]
            code_size = min_code_size + 1
            prev = None
            continue
        if code == end:
            break
        if code < len(dic):
            entry = dic[code]
        elif prev is not None:
            entry = prev + prev[:1]
        else:
            break
        out += entry
        if prev is not None:
            dic.append(prev + entry[:1])
            if len(dic) == (1 << code_size) and code_size < 12:
                code_size += 1
        prev = entry
    return bytes(out)

def _blocks(buf, o):

    out = bytearray()
    while o < len(buf):
        n = buf[o]; o += 1
        if n == 0:
            break
        out += buf[o:o + n]; o += n
    return bytes(out), o

def _bg_if_keyed(idx, w, h, bg):

    return None

def decode_gif_variant(p):

    if len(p) < 8:
        raise ImgError("载荷太短")
    dec_size = struct.unpack_from(">I", p, 0)[0]
    flags = p[4]
    o = 7
    palette = []
    if flags & 0x80:
        if p[6] != 0:
            raise ImgError(f"p[6]={p[6]:#x}，期望 0")
        n = 1 << ((flags & 7) + 1)
        for i in range(n):
            palette.append(struct.unpack_from(">H", p, o + i * 2)[0])
        o += n * 2

    bg = p[5]
    transparent = None
    while o < len(p):
        b = p[o]
        if b == 0x21:
            label = p[o + 1]; o += 2
            if label == 0xF9:
                size = p[o]
                gflags = p[o + 1]
                if gflags & 1:
                    transparent = p[o + 4]
                o += size + 1
                o += 1
            else:
                _, o = _blocks(p, o + 1) if False else (None, o)
                size = p[o]; o += 1 + size
                _, o = _blocks(p, o)
        elif b == 0x2C:
            left, top, w, h = struct.unpack_from("<HHHH", p, o + 1)
            lflags = p[o + 9]
            o += 10
            if lflags & 0x80:
                n = 1 << ((lflags & 7) + 1)
                palette = [struct.unpack_from(">H", p, o + i * 2)[0] for i in range(n)]
                o += n * 2
            mcs = p[o]; o += 1
            data, o = _blocks(p, o)
            idx = _lzw(data, mcs)
            if lflags & 0x40:
                idx = _deinterlace(idx, w, h)
            pix = [palette[i] if i < len(palette) else 0 for i in idx[:w * h]]
            pix += [0] * (w * h - len(pix))
            idx_full = list(idx[:w * h]) + [0] * max(0, w * h - len(idx))
            if transparent is None:
                transparent = _bg_if_keyed(idx_full, w, h, bg)
            return dict(width=w, height=h, rgb565=pix,
                        transparent=transparent,
                        index=idx_full,
                        dec_size=dec_size, left=left, top=top)
        elif b == 0x3B:
            break
        else:
            o += 1
    raise ImgError("没找到图像描述符")

def _deinterlace(idx, w, h):
    out = bytearray(w * h)
    rows = list(range(0, h, 8)) + list(range(4, h, 8)) +        list(range(2, h, 4)) + list(range(1, h, 2))
    for src, dst in enumerate(rows):
        out[dst * w:(dst + 1) * w] = idx[src * w:(src + 1) * w]
    return bytes(out)

def decode(entry_data):

    t = entry_data[0]
    if t == 0:
        w = struct.unpack_from(">H", entry_data, 1)[0]
        h = struct.unpack_from(">H", entry_data, 3)[0]
        pix = list(struct.unpack_from(f"<{w * h}H", entry_data, 8))
        return dict(width=w, height=h, rgb565=pix, transparent=None)
    if t == 1:
        return decode_gif_variant(entry_data[1:])
    if t == 3:
        return decode_png(entry_data[9:])
    return None

def _unfilter(raw, w, h, bpp):

    stride = (w * bpp)
    out = bytearray(stride * h)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        f = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride
        if f == 0:
            pass
        elif f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise ImgError(f"未知 PNG 滤波类型 {f}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)

def decode_png(data):

    game = data[:8] == b"\x89PNGGAME"
    if data[:8] != b"\x89PNG\r\n\x1a\n" and not game:
        raise ImgError("不是 PNG")
    o = 8
    idat = bytearray()
    plte = b""
    trns = b""
    w = h = depth = ctype = None
    while o + 8 <= len(data):
        ln = struct.unpack_from(">I", data, o)[0]
        tag = data[o + 4:o + 8]
        if game and tag == b"PLTE":
            n = ln // 3
            pal = bytearray()
            for i in range(n):
                v = struct.unpack_from("<H", data, o + 8 + 2 * i)[0]
                pal += bytes(((v >> 11) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3))
            plte = bytes(pal)
            o += 12 + 2 * n
            continue
        body = data[o + 8:o + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body[:13])
            if interlace:
                raise ImgError("暂不支持隔行 PNG")
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        o += 12 + ln
    if w is None:
        raise ImgError("PNG 缺少 IHDR")
    raw = zlib.decompress(bytes(idat))
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    if depth == 8:
        bpp = chans
        rowbytes = w * chans
    elif depth in (1, 2, 4) and ctype == 3:
        bpp = 1
        rowbytes = (w * depth + 7) // 8
    else:
        raise ImgError(f"暂不支持 PNG 位深 {depth} 类型 {ctype}")

    out = bytearray(rowbytes * h)
    prev = bytearray(rowbytes)
    pos = 0
    for y in range(h):
        f = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + rowbytes]); pos += rowbytes
        if f == 1:
            for i in range(bpp, rowbytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(rowbytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(rowbytes):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(rowbytes):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            raise ImgError(f"未知 PNG 滤波类型 {f}")
        out[y * rowbytes:(y + 1) * rowbytes] = line
        prev = line

    pix = []
    alpha = []
    if ctype == 3:
        pal = [(plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2]) for i in range(len(plte) // 3)]
        pal_a = list(trns) + [255] * (len(pal) - len(trns))
        for y in range(h):
            base = y * rowbytes
            for x in range(w):
                if depth == 8:
                    idx = out[base + x]
                else:
                    per = 8 // depth
                    b = out[base + x // per]
                    sh = 8 - depth * (x % per + 1)
                    idx = (b >> sh) & ((1 << depth) - 1)
                r, g, bl = pal[idx] if idx < len(pal) else (0, 0, 0)
                pix.append(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (bl >> 3))
                alpha.append(1 if pal_a[idx] < 128 else 0)
    else:
        for y in range(h):
            base = y * rowbytes
            for x in range(w):
                p = base + x * chans
                if ctype == 0:
                    r = g = bl = out[p]; a = 255
                elif ctype == 4:
                    r = g = bl = out[p]; a = out[p + 1]
                elif ctype == 2:
                    r, g, bl = out[p], out[p + 1], out[p + 2]; a = 255
                else:
                    r, g, bl, a = out[p], out[p + 1], out[p + 2], out[p + 3]
                pix.append(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (bl >> 3))
                alpha.append(1 if a < 128 else 0)
    return dict(width=w, height=h, rgb565=pix,
                alpha=alpha if any(alpha) else None, transparent=None)
