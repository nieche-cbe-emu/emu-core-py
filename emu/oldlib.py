
import struct

from .api import impl, _s16

GAME = "GameManagerOldTag"
SCREEN_W, SCREEN_H = 240, 400

def _u16(v):
    return v & 0xFFFF

def _w16(mc, a, v):
    mc.uc.mem_write(a, struct.pack("<H" if mc.le else ">H", v & 0xFFFF))

def _rs16(mc, a):
    return _s16(mc.r16(a))

def _clip(rt):
    c = rt.state.get("oldlib_clip")
    if c is None:
        c = rt.state["oldlib_clip"] = [0, 0, 0, 0]
    return c

def _alloc(mc, size, tag):
    p = mc.heap.alloc(size, tag, strict=False) if size else 0
    if p:
        mc.uc.mem_write(p, b"\x00" * size)
    return p

@impl(GAME, "SetClip")
def set_clip(mc, rt):

    x, y, w, h = (_s16(mc.arg(i)) for i in range(4))
    if x <= 0 and y <= 0 and w > 0 and h > 0:
        rt.maybe_adopt_screen(w, h)
    c = _clip(rt)
    c[0], c[1], c[2], c[3] = x, y, _s16(x + w), _s16(y + h)
    mc.ret(_u16(y + h))

@impl(GAME, "GetClip")
def get_clip(mc, rt):

    out = mc.arg(0)
    x0, y0, x1, y1 = _clip(rt)
    w = x1 - x0 if x1 > x0 else 0
    h = y1 - y0 if y1 > y0 else 0
    if out:
        for i, v in enumerate((x0, y0, w, h)):
            _w16(mc, out + 2 * i, v)
    mc.ret(out)

@impl(GAME, "CheckClip")
def check_clip(mc, rt):

    mw, mh = _s16(mc.arg(0)), _s16(mc.arg(1))
    c = _clip(rt)
    if c[2] >= 0 and c[0] <= mw and c[3] >= 0 and c[1] <= mh:
        if c[0] < 0:
            c[0] = 0
        if c[2] > mw:
            c[2] = mw
        if c[3] > mh:
            c[3] = mh
    else:
        c[0] = c[1] = c[2] = c[3] = 0
    mc.ret(mc.arg(0))

@impl(GAME, "IMG_GetHeight")
def img_height(mc, rt):
    p = mc.arg(0)
    mc.ret(rt.images.wh(p)[1] & 0xFFFF if p else 0)

@impl(GAME, "IMG_GetWidth")
def img_width(mc, rt):
    p = mc.arg(0)
    mc.ret(rt.images.wh(p)[0] & 0xFFFF if p else 0)

@impl(GAME, "ToRGB")
def to_rgb(mc, rt):
    r, g, b = mc.arg(0) & 0xFF, mc.arg(1) & 0xFF, mc.arg(2)
    mc.ret(((r & 0xF8) << 8) + 8 * (g & 0xFC) + (b >> 3) & 0xFFFF)

def _clipped_blit(rt, dst, src, sx, sy, w, h, dx, dy, alpha):

    if not src:
        return
    x0, y0, x1, y1 = _clip(rt)
    if not (dx + w > x0 and dy + h > y0):
        return
    if x0 > dx:
        w = _s16(dx - x0 + w)
        sx = _s16(sx - (dx - x0))
        dx = x0
    if y0 > dy:
        h = _s16(dy - y0 + h)
        sy = _s16(sy - (dy - y0))
        dy = y0
    if dx + w > x1:
        w = _s16(x1 - dx)
    if w <= 0:
        return
    if dy + h > y1:
        h = _s16(y1 - dy)
    if h <= 0:
        return
    rt.images.blit(src, dst or rt.fb.img, dx, dy, w, h, sx, sy, alpha=alpha)

def _args8(mc):
    return (mc.arg(0), mc.arg(1), _s16(mc.arg(2)), _s16(mc.arg(3)), _s16(mc.arg(4)),
            _s16(mc.arg(5)), _s16(mc.arg(6)), _s16(mc.arg(7)))

@impl(GAME, "DrawImageWithClipEx")
def draw_img_clip_ex(mc, rt):
    _clipped_blit(rt, *_args8(mc), alpha=False)
    mc.ret(0)

@impl(GAME, "DrawImageClipAndAlphaEx")
def draw_img_clip_alpha_ex(mc, rt):
    _clipped_blit(rt, *_args8(mc), alpha=True)
    mc.ret(0)

def _args7(mc):
    return (mc.arg(0), _s16(mc.arg(1)), _s16(mc.arg(2)), _s16(mc.arg(3)), _s16(mc.arg(4)),
            _s16(mc.arg(5)), _s16(mc.arg(6)))

@impl(GAME, "DrawImageWithClip")
def draw_img_clip(mc, rt):

    _clipped_blit(rt, rt.fb.img, *_args7(mc), alpha=False)
    mc.ret(0)

@impl(GAME, "DrawImageClipAndAlpha")
def draw_img_clip_alpha(mc, rt):
    _clipped_blit(rt, rt.fb.img, *_args7(mc), alpha=True)
    mc.ret(0)

@impl(GAME, "DrawFullScreen")
def draw_full_screen(mc, rt):

    img = mc.arg(0)
    c = _clip(rt)
    w, h = (_s16(v) for v in rt.images.wh(img))
    c[0], c[1] = 0, 0
    c[2], c[3] = min(w, SCREEN_W), min(h, SCREEN_H)
    c[2], c[3] = max(c[2], 0), max(c[3], 0)
    n = 2 * c[2] * c[3]
    if n:
        mc.uc.mem_write(rt.lcd_buffer(), bytes(mc.uc.mem_read(mc.r32(img), n)))
    mc.ret(0)

@impl(GAME, "GetLCDBuffer")
def get_lcd_buffer(mc, rt):
    mc.ret(rt.lcd_buffer())

@impl(GAME, "FillRect")
def fill_rect(mc, rt):

    x, y, w, h = (_s16(mc.arg(i)) for i in range(1, 5))
    color = mc.arg(5) & 0xFFFF
    if x <= SCREEN_W and y <= SCREEN_H and x + w >= 0 and y + h >= 0:
        r, b = _s16(x + w - 1), _s16(y + h - 1)
        if r > x and b > y:
            rt.fill_rect(x & 0xFFFF, y & 0xFFFF, r - x + 1, b - y + 1, color)
    mc.ret(0)

def _line_ex(mc, rt, img, x1, y1, x2, y2, color):

    if img:
        base, stride = mc.r32(img), _s16(rt.images.wh(img)[0])
    else:
        base, stride = rt.lcd_buffer(), SCREEN_W
    x0c, y0c, x1c, y1c = _clip(rt)
    if x1 > x2:
        x1, x2, y1, y2 = x2, x1, y2, y1
    dx, dy, step = _s16(x2 - x1), _s16(y2 - y1), 1
    if dy < 0:
        dy, step = -dy, -1
    two_dx, two_dy = 2 * dx, 2 * dy
    pk = struct.Struct("<H" if mc.le else ">H").pack(color & 0xFFFF)

    def plot(x, y):
        if x0c <= x < x1c and y0c <= y < y1c:
            mc.uc.mem_write(base + 2 * y * stride + 2 * x, pk)

    x, y = x1, y1
    if dx < dy:
        e = _s16(two_dx - dy)
        n = dy
        while n >= 0:
            plot(x, y)
            if e <= 0:
                e = _s16(e + two_dx)
            else:
                x = _s16(x + 1)
                e = _s16(e + two_dx - two_dy)
            n -= 1
            y = _s16(y + step)
    else:
        e = _s16(two_dy - dx)
        n = dx
        while n >= 0:
            plot(x, y)
            if e <= 0:
                e = _s16(e + two_dy)
            else:
                y = _s16(y + step)
                e = _s16(e + two_dy - two_dx)
            n -= 1
            x = _s16(x + 1)

@impl(GAME, "DrawLineEx")
def draw_line_ex(mc, rt):
    _line_ex(mc, rt, mc.arg(0), *(_s16(mc.arg(i)) for i in range(1, 5)), mc.arg(5))
    mc.ret(0)

PIC_METHODS = {}

def _pic_method(off):
    def deco(fn):
        PIC_METHODS[off] = (f"DF_PictureLibrary+{off:#x}", fn)
        return fn
    return deco

@impl(GAME, "initDFPictureLibrary")
def init_picture_library(mc, rt):
    lib, n = mc.arg(0), _s16(mc.arg(1))
    mc.w32(lib + 12, _alloc(mc, 2 * n, "piclib_ids"))
    mc.w32(lib + 16, _alloc(mc, 4 * n, "piclib_imgs"))
    _w16(mc, lib + 20, 0)
    _w16(mc, lib + 8, n)
    for off, (name, fn) in PIC_METHODS.items():
        mc.w32(lib + off, rt.method(name, fn))
    mc.w32(lib, _alloc(mc, 480, "piclib_line"))
    mc.uc.mem_write(lib + 22, b"\x01")
    mc.ret(1)

def _pic_img(mc, lib, idx):
    return mc.r32(mc.r32(lib + 16) + 4 * idx)

@_pic_method(0x18)
def pic_create_blank(mc, rt):

    lib, w, h = mc.arg(0), mc.arg(1), mc.arg(2)
    r = 4 - (w - _cdiv(w, 4) * 4)
    pad = r - _cdiv(r, 4) * 4
    n, cap = _rs16(mc, lib + 20), _rs16(mc, lib + 8)
    if n >= cap:
        mc.ret(0xFFFFFFFF)
        return
    img = _alloc(mc, rt.images.header_size(), "piclib_img")
    mc.w32(mc.r32(lib + 16) + 4 * n, img)
    size = 2 * (w + pad) * h
    data = mc.heap.alloc(size, "bigmem", strict=False) if size else 0
    if data:
        mc.uc.mem_write(data, b"\x00" * size)
    mc.w32(img, data)
    rt.images.set_wh(img, w, h, 1)
    _w16(mc, mc.r32(lib + 12) + 2 * n, -1)
    _w16(mc, lib + 20, n + 1)
    mc.ret(n)

@_pic_method(0x1c)
def pic_load(mc, rt):

    lib = mc.arg(0)
    api = _api()
    name = (mc.cstr(mc.arg(1)) or b"").decode("latin1")
    rid = api._res_index(rt, name)
    n, cap = _rs16(mc, lib + 20), _rs16(mc, lib + 8)
    if rid < 0 or n >= cap:
        mc.ret(0xFFFFFFFF)
        return
    ids = mc.r32(lib + 12)
    for i in range(n):
        if _rs16(mc, ids + 2 * i) == _s16(rid):
            mc.ret(i)
            return
    img = _alloc(mc, rt.images.header_size(), "piclib_img")
    mc.w32(mc.r32(lib + 16) + 4 * n, img)
    mc.setreg(0, api._res_ptr(mc, rt, rid))
    mc.setreg(1, img)
    api.img_from_stream(mc, rt)
    _w16(mc, ids + 2 * n, rid)
    _w16(mc, lib + 20, n + 1)
    mc.ret(n)

@_pic_method(0x50)
def pic_release(mc, rt):
    lib = mc.arg(0)
    if mc.r8(lib + 22) != 1:
        mc.ret(mc.r8(lib + 22))
        return
    imgs = mc.r32(lib + 16)
    for i in range(_rs16(mc, lib + 20)):
        mc.setreg(0, mc.r32(imgs + 4 * i))
        _api().img_release(mc, rt)
        _free_field(mc, imgs + 4 * i)
    _free_field(mc, lib + 12)
    _free_field(mc, lib + 16)
    _free_field(mc, lib)
    mc.uc.mem_write(lib + 22, b"\x00")
    mc.ret(0)

@_pic_method(0x20)
def pic_width(mc, rt):
    lib, idx = mc.arg(0), _s16(mc.arg(1))
    mc.ret(_u16(rt.images.wh(_pic_img(mc, lib, idx))[0]))

@_pic_method(0x24)
def pic_height(mc, rt):
    lib, idx = mc.arg(0), _s16(mc.arg(1))
    mc.ret(_u16(rt.images.wh(_pic_img(mc, lib, idx))[1]))

@_pic_method(0x28)
def pic_fill_rect(mc, rt):

    lib = mc.arg(0)
    x, y, w, h = (_s16(mc.arg(i)) for i in range(1, 5))
    color = mc.arg(5) & 0xFFFF
    line = mc.r32(lib)
    if w > 0 and line:
        mc.uc.mem_write(line, struct.pack(("<" if mc.le else ">") + "H", color) * w)
    tmp = rt.state.get("piclib_tmpimg")
    if tmp is None:
        tmp = rt.state["piclib_tmpimg"] = _alloc(mc, rt.images.header_size(), "piclib_tmpimg")
    mc.w32(tmp, line)
    rt.images.set_wh(tmp, w & 0xFFFF, 1)
    target = mc.r32(lib + 4)
    for i in range(max(h, 0)):
        _clipped_blit(rt, target or rt.fb.img, tmp, 0, 0, w, 1, x, _s16(y + i), alpha=False)
    mc.ret(0)

@_pic_method(0x2c)
def pic_line(mc, rt):
    lib = mc.arg(0)
    _line_ex(mc, rt, mc.r32(lib + 4), *(_s16(mc.arg(i)) for i in range(1, 5)), mc.arg(5))
    mc.ret(0)

@_pic_method(0x30)
def pic_full_screen(mc, rt):
    lib, idx = mc.arg(0), _s16(mc.arg(1))
    if not mc.r32(lib + 4):
        mc.setreg(0, _pic_img(mc, lib, idx))
        draw_full_screen(mc, rt)
    mc.ret(0)

def _pic_draw(mc, rt, alpha):
    lib, idx, x, y = mc.arg(0), _s16(mc.arg(1)), _s16(mc.arg(2)), _s16(mc.arg(3))
    img = _pic_img(mc, lib, idx)
    w, h = rt.images.wh(img)
    _clipped_blit(rt, mc.r32(lib + 4) or rt.fb.img, img, 0, 0, w, h, x, y, alpha)
    mc.ret(0)

@_pic_method(0x34)
def pic_draw(mc, rt):
    _pic_draw(mc, rt, False)

@_pic_method(0x38)
def pic_draw_alpha(mc, rt):
    _pic_draw(mc, rt, True)

def _pic_region(mc, rt, alpha):

    lib, idx = mc.arg(0), _s16(mc.arg(1))
    dx, dy, sx, sy, w, h = (_s16(mc.arg(i)) for i in range(2, 8))
    _clipped_blit(rt, mc.r32(lib + 4) or rt.fb.img, _pic_img(mc, lib, idx),
                  sx, sy, w, h, dx, dy, alpha)
    mc.ret(0)

@_pic_method(0x3c)
def pic_region(mc, rt):
    _pic_region(mc, rt, False)

@_pic_method(0x40)
def pic_region_alpha(mc, rt):
    _pic_region(mc, rt, True)

@_pic_method(0x4c)
def pic_set_target(mc, rt):

    lib, img = mc.arg(0), mc.arg(1)
    mc.w32(lib + 4, img)
    w, h = (_s16(v) for v in rt.images.wh(img)) if img else (SCREEN_W, SCREEN_H)
    mc.setreg(0, w & 0xFFFF)
    mc.setreg(1, h & 0xFFFF)
    check_clip(mc, rt)

def _noop(mc, rt):
    pass

@impl(GAME, "initDFWindows")
def init_repaint_panel(mc, rt):
    p = mc.arg(0)
    xy, wh, ud_logic, ud_paint, n = mc.arg(1), mc.arg(2), mc.arg(3), mc.arg(4), mc.arg(5)
    table = _alloc(mc, 4 * n, "panel_dirty") if n else 0
    mc.w32(p + 12, table)
    for i in range(n):
        mc.w32(table + 4 * i, _alloc(mc, 8, "panel_rect"))
    mc.w32(p + 4, 0)
    mc.w32(p + 8, n)
    mc.w32(p + 24, xy)
    mc.w32(p + 28, wh)
    mc.w32(p + 16, ud_logic)
    mc.w32(p + 20, ud_paint)
    mc.w32(p + 40, rt.method("DF_Windows+0x28", panel_add_child))
    mc.w32(p + 44, rt.method("DF_Windows.nullLogic", _noop))
    mc.w32(p + 64, rt.method("DF_Windows.nullEvent", _noop))
    mc.w32(p + 48, rt.method("DF_Windows.nullPaint", _noop))
    mc.w32(p + 56, rt.method("DF_Windows+0x38", panel_repaint))
    mc.w32(p + 52, rt.method("DF_Windows+0x34", panel_update))
    mc.w32(p + 68, rt.method("DF_Windows+0x44", panel_event))
    mc.w32(p + 32, 0)
    mc.w32(p + 60, rt.method("DF_Windows+0x3c", panel_invalidate))
    mc.w32(p + 36, 0)
    _invalidate(mc, p, 4, p + 24)
    mc.ret(0)

def _last_sibling(mc, p):
    while mc.r32(p + 32):
        p = mc.r32(p + 32)
    return p

def panel_add_child(mc, rt):

    p, child, where = mc.arg(0), mc.arg(1), mc.arg(2)
    if where == 0:
        mc.w32(_last_sibling(mc, p) + 32, child)
    elif where == 1:
        if not mc.r32(p + 36):
            mc.w32(p + 36, child)
        else:
            mc.w32(_last_sibling(mc, mc.r32(p + 36)) + 32, child)

def _add_dirty(mc, p, rect):
    n, cap = mc.r32(p + 4), mc.r32(p + 8)
    if n < cap:
        e = mc.r32(mc.r32(p + 12) + 4 * n)
        mc.uc.mem_write(e, bytes(mc.uc.mem_read(rect, 8)))
        mc.w32(p + 4, n + 1)

def _invalidate(mc, p, kind, rect):

    while True:
        x, y, w, h = (_rs16(mc, rect + 2 * i) for i in range(4))
        if x + w < 0 or x > SCREEN_W or y + h < 0 or y > SCREEN_H:
            x = y = w = h = 0
        if x < 0:
            w, x = _s16(w + x), 0
        if x + w > SCREEN_W:
            w = SCREEN_W - x
        if y + h > SCREEN_H:
            h = SCREEN_H - y
        for i, v in enumerate((x, y, w, h)):
            _w16(mc, rect + 2 * i, v)
        if kind != 2:
            break
        _add_dirty(mc, p, rect)
        if mc.r32(p + 32):
            _invalidate(mc, mc.r32(p + 32), 2, rect)
        p = mc.r32(p + 36)
        if not p:
            return
    if kind == 4:
        _add_dirty(mc, p, rect)

@impl("VmDFEnginelManagerTag", "DF_SendMessage")
def panel_invalidate(mc, rt):

    _invalidate(mc, mc.arg(0), _s16(mc.arg(1)), mc.arg(2))

def panel_update(mc, rt):
    def run(p):
        yield mc.r32(p + 44), (mc.r32(p + 16),)
        if mc.r32(p + 36):
            yield mc.r32(p + 52), (mc.r32(p + 36),)
        if mc.r32(p + 32):
            yield mc.r32(p + 52), (mc.r32(p + 32),)
        return 0
    rt.cocall(mc, run(mc.arg(0)))

def panel_repaint(mc, rt):
    def run(p):
        i = 0
        while mc.r32(p + 4) > i:
            e = mc.r32(mc.r32(p + 12) + 4 * i)
            x, y, w, h = (_rs16(mc, e + 2 * k) for k in range(4))
            c = _clip(rt)
            c[0], c[1], c[2], c[3] = x, y, _s16(x + w), _s16(y + h)
            yield mc.r32(p + 48), (mc.r32(p + 20),)
            i += 1
        mc.w32(p + 4, 0)
        if mc.r32(p + 36):
            yield mc.r32(p + 56), (mc.r32(p + 36),)
        if mc.r32(p + 32):
            yield mc.r32(p + 56), (mc.r32(p + 32),)
        return 0
    rt.cocall(mc, run(mc.arg(0)))

def panel_event(mc, rt):
    def run(p, a, b):
        yield mc.r32(p + 64), (mc.r32(p + 16),)
        if mc.r32(p + 36):
            yield mc.r32(p + 68), (mc.r32(p + 36), a, b)
        if mc.r32(p + 32):
            yield mc.r32(p + 68), (mc.r32(p + 32), a, b)
        return 0
    rt.cocall(mc, run(mc.arg(0), mc.arg(1), mc.arg(2)))

def _api():
    from . import api
    return api

def _font_w(rt, full):
    f = rt.font
    if not f:
        return 16 if full else 8
    return f.hw if full else f.aw

def _font_h(rt):
    return rt.font.hh if rt.font else 12

@impl(GAME, "GetFontWidth")
def get_font_width(mc, rt):
    mc.ret(_font_w(rt, True))

@impl(GAME, "GetFontWidth_char")
def get_font_width_char(mc, rt):
    mc.ret(_font_w(rt, False))

@impl(GAME, "GetFontHeight", "GetFontHeight_char")
def get_font_height(mc, rt):
    mc.ret(_font_h(rt))

def _str_width(rt, s):
    return _api()._text_width(rt, s) & 0xFFFF

def _bounded(mc, p, n):

    n = max(0, min(n, 199))
    raw = bytes(mc.uc.mem_read(p, n)) if n and p else b""
    return raw.split(b"\x00", 1)[0]

def _rgb888_to_565(c):
    return ((((c >> 16) & 0xF8) << 8) + 8 * ((c >> 8) & 0xFC) + ((c & 0xFF) >> 3)) & 0xFFFF

def _draw_string_ex(mc, rt, img, s, n, x, y, rgb):
    _api()._draw_text(rt, mc, _bounded(mc, s, n), _s16(x & 0xFFFF), _s16(y & 0xFFFF),
                      _rgb888_to_565(rgb), img=img)

@impl(GAME, "DrawStringEx")
def draw_string_ex(mc, rt):

    _draw_string_ex(mc, rt, mc.arg(0), mc.arg(1), _s16(mc.arg(2)), mc.arg(3), mc.arg(4), mc.arg(5))
    mc.ret(0)

@impl(GAME, "DrawString")
def draw_string(mc, rt):

    _draw_string_ex(mc, rt, rt.fb.img, mc.arg(0), _s16(mc.arg(1)), mc.arg(2), mc.arg(3), mc.arg(4))
    mc.ret(0)

def _s8(v):
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v

def _rs8(mc, a):
    return _s8(mc.r8(a))

def _cdiv(a, b):
    if b == 0:
        return 0
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q

@impl(GAME, "InitTextBox")
def init_text_box(mc, rt):
    tb = mc.arg(0)
    for i, off in enumerate((20, 22, 24, 26)):
        _w16(mc, tb + off, mc.arg(2 + i))
    mc.w32(tb + 32, rt.method("TextBox+0x20", tb_set_line_height))
    mc.w32(tb, 0)
    mc.w32(tb + 36, rt.method("TextBox+0x24", tb_set_text))
    mc.w32(tb + 40, rt.method("TextBox+0x28", tb_draw))
    mc.w32(tb + 48, rt.method("TextBox+0x30", tb_release))
    mc.w32(tb + 28, rt.method("TextBox+0x1c", tb_set_rect))
    mc.w32(tb + 44, rt.method("TextBox+0x2c", tb_draw_ex))
    mc.w32(tb + 52, rt.method("TextBox+0x34", tb_set_style))
    v = _font_h(rt) + 2
    _w16(mc, tb + 6, v)
    _w16(mc, tb + 4, 0)
    mc.w32(tb + 12, 0)
    mc.w32(tb + 8, 0)
    mc.ret(v)

def tb_set_rect(mc, rt):
    tb = mc.arg(0)
    for i, off in enumerate((20, 22, 24, 26)):
        _w16(mc, tb + off, mc.arg(1 + i))

def tb_set_line_height(mc, rt):
    _w16(mc, mc.arg(0) + 6, mc.arg(1))

def tb_set_style(mc, rt):
    _w16(mc, mc.arg(0) + 4, mc.arg(1))

def _free_field(mc, a):
    if mc.r32(a):
        mc.heap.free(mc.r32(a))
        mc.w32(a, 0)

def tb_release(mc, rt):
    tb = mc.arg(0)
    _free_field(mc, tb + 12)
    _free_field(mc, tb + 8)

def tb_set_text(mc, rt):

    tb, text = mc.arg(0), mc.arg(1)
    w, h = _rs16(mc, tb + 24), _rs16(mc, tb + 26)
    if not (_font_w(rt, True) <= w and _font_h(rt) <= h + 2 and text):
        mc.ret(0)
        return
    raw = mc.read_upto(text, 0x10000)
    end = raw.find(b"\x00")
    if end >= 0:
        raw = raw[:end + 1]

    def ch(i):
        return raw[i] if i < len(raw) else 0

    def seg_width(start, stop):
        return _s16(_str_width(rt, _bounded(mc, text + start, stop - start)))

    mc.w32(tb, text)
    lines = last = i = start = 0
    while ch(i):
        if ch(i) == 10:
            last = 0
            while ch(i) == 10:
                i += 1
            lines += 1
            if ch(i) == 0:
                lines += 1
            start = i
        else:
            n = 2 if ch(i) & 0x80 else 1
            last = seg_width(start, i + n)
            if last > w:
                lines += 1
                start = i
            i += n
    if last > 0:
        lines += 1
    lines = _s16(lines)
    mc.uc.mem_write(tb + 16, bytes([lines & 0xFF]))
    per = _s8(_cdiv(h, _rs16(mc, tb + 6)))
    mc.uc.mem_write(tb + 17, bytes([per & 0xFF]))
    pages = _cdiv(per + lines - 1, per)
    mc.uc.mem_write(tb + 18, bytes([pages & 0xFF, 0]))
    _free_field(mc, tb + 12)
    _free_field(mc, tb + 8)
    mc.w32(tb + 12, _alloc(mc, lines, "textbox_lens"))
    mc.w32(tb + 8, _alloc(mc, 2 * lines, "textbox_starts"))
    starts, lens = mc.r32(tb + 8), mc.r32(tb + 12)

    def set_start(k, v):
        _w16(mc, starts + 2 * k, v)

    def set_len(k, v):
        mc.uc.mem_write(lens + k, bytes([v & 0xFF]))

    set_start(0, 0)
    line = last = i = start = 0
    while ch(i):
        if ch(i) == 10:
            last = 0
            set_len(line, i - mc.r16(starts + 2 * line))
            while ch(i) == 10:
                i += 1
            line += 1
            set_start(line, i)
            start = i
        else:
            n = 2 if ch(i) & 0x80 else 1
            last = seg_width(start, i + n)
            if last > w:
                start = i
                set_len(line, i - mc.r16(starts + 2 * line))
                line += 1
                set_start(line, i)
            i += n
    if last > 0:
        set_len(line, i - mc.r16(starts + 2 * line))
    mc.ret(0)

def _tb_draw(mc, rt, tb, img, rgb):
    x, y = _rs16(mc, tb + 20), _rs16(mc, tb + 22)
    style = mc.r16(tb + 4)
    per, page, lines = _rs8(mc, tb + 17), _rs8(mc, tb + 19), _rs8(mc, tb + 16)
    first = page * per
    xoff = yoff = 0
    if style & 4:
        cnt = per
        if per + first > lines:
            cnt = _s16(lines - first)
        yoff = _s16(_rs16(mc, tb + 26) - (_font_h(rt) + 2) * cnt) >> 1
    i = first
    text, starts, lens = mc.r32(tb), mc.r32(tb + 8), mc.r32(tb + 12)
    while _rs8(mc, tb + 17) + first > i:
        if _rs8(mc, tb + 16) > i:
            s = text + _rs16(mc, starts + 2 * i)
            n = _rs8(mc, lens + i)
            if style & 2:
                sw = _str_width(rt, _bounded(mc, s, n))
                bw = _rs16(mc, tb + 24)
                xoff = 0 if sw >= bw else _s16(_cdiv(bw - sw, 2))
            _draw_string_ex(mc, rt, img or rt.fb.img, s, n, x + xoff, y + yoff, rgb)
            y = _s16(mc.r16(tb + 6) + y)
        i = _s16(i + 1)

def tb_draw(mc, rt):
    _tb_draw(mc, rt, mc.arg(0), 0, mc.arg(1))

def tb_draw_ex(mc, rt):
    _tb_draw(mc, rt, mc.arg(0), mc.arg(1), mc.arg(2))

def _cmod(a, b):
    return a - _cdiv(a, b) * b if b else 0

def _and_clip(rt, x, y, w, h):

    c = _clip(rt)
    x0, y0, x1, y1 = c
    if x + w >= x0 and y + h >= y0 and x1 >= x and y1 >= y:
        if x0 > x:
            w, x = _s16(w - (x0 - x)), x0
        if y0 > y:
            h, y = _s16(h - (y0 - y)), y0
        if x + w > x1:
            w = _s16(x1 - x)
        if y + h > y1:
            h = _s16(y1 - y)
        w, h = max(w, 0), max(h, 0)
        c[0], c[1], c[2], c[3] = x, y, _s16(x + w), _s16(y + h)
    else:
        c[0] = c[1] = c[2] = c[3] = 0

def _saved_clip(rt):
    x0, y0, x1, y1 = _clip(rt)
    return x0, y0, (x1 - x0 if x1 > x0 else 0), (y1 - y0 if y1 > y0 else 0)

def _restore_clip(rt, saved):
    x, y, w, h = saved
    c = _clip(rt)
    c[0], c[1], c[2], c[3] = x, y, _s16(x + w), _s16(y + h)

def _cell(rt, img, sx, sy, w, h, dx, dy, alpha=False):
    _clipped_blit(rt, rt.fb.img, img, _s16(sx), _s16(sy), _s16(w), _s16(h), _s16(dx), _s16(dy), alpha)

@impl(GAME, "DrawUI")
def draw_ui(mc, rt):

    img = mc.arg(0)
    x, y, w, h, n = (_s16(mc.arg(i)) for i in range(1, 6))
    iw, ih = rt.images.wh(img)
    cw, ch = _s16(_cdiv(iw & 0xFFFF, n)), _s16(_cdiv(ih & 0xFFFF, n))
    cols, rows = _s16(_cdiv(w + cw - 1, cw)), _s16(_cdiv(h + ch - 1, ch))
    saved = _saved_clip(rt)
    _and_clip(rt, x, y, w, h)
    k = _s16(n - 2)
    right, last = _s16(x + w - cw), _s16(cw * (k + 1))

    def row(sy, dy):
        _cell(rt, img, 0, sy, cw, ch, x, dy)
        for i in range(1, cols):
            _cell(rt, img, (_cmod(i - 1, k) + 1) * cw, sy, cw, ch, i * cw + x, dy)
        _cell(rt, img, last, sy, cw, ch, right, dy)

    row(0, y)
    for j in range(1, rows):
        row((_cmod(j - 1, k) + 1) * ch, j * ch + y)
    row(ch * (k + 1), y + h - ch)
    _restore_clip(rt, saved)
    mc.ret(0)

@impl(GAME, "DrawUIHorizontal")
def draw_ui_horizontal(mc, rt):

    img = mc.arg(0)
    x, y, w = (_s16(mc.arg(i)) for i in range(1, 4))
    iw, ih = rt.images.wh(img)
    cw, ch = _s16((iw & 0xFFFF) // 3), _s16(ih)
    cols = _s16(_cdiv(w + cw - 1, cw))
    saved = _saved_clip(rt)
    _and_clip(rt, x, y, w, ch)
    _cell(rt, img, 0, 0, cw, ch, x, y)
    for i in range(1, cols):
        _cell(rt, img, cw, 0, cw, ch, i * cw + x, y)
    _cell(rt, img, 2 * cw, 0, cw, ch, x + w - cw, y)
    _restore_clip(rt, saved)
    mc.ret(0)

@impl(GAME, "DrawUISingleRepeat")
def draw_ui_single_repeat(mc, rt):

    img = mc.arg(0)
    x, y, w, h = (_s16(mc.arg(i)) for i in range(1, 5))
    iw, ih = (_s16(v) for v in rt.images.wh(img))
    saved = _saved_clip(rt)
    _and_clip(rt, x, y, w, h)
    right, bottom = x + w, y + h
    yy = y
    while ih > 0 and iw > 0 and bottom >= yy:
        xx = x
        while right >= xx:
            _cell(rt, img, 0, 0, iw, ih, xx, yy)
            xx = _s16(xx + iw)
        yy = _s16(yy + ih)
    _restore_clip(rt, saved)
    mc.ret(0)

@impl(GAME, "DrawUIFourXRepeat", "OldLib_08c")
def draw_ui_four_x_repeat(mc, rt):

    img = mc.arg(0)
    x, y, w, h = (_s16(mc.arg(i)) for i in range(1, 5))
    iw, ih = rt.images.wh(img)
    cw, ch = (iw & 0xFFFF) >> 1, _s16(ih)
    saved = _saved_clip(rt)
    _and_clip(rt, x, y, w, h)
    r, yy = 0, y
    while ch > 0 and cw > 0 and yy < h:
        c, xx = 0, x
        while xx < w:
            _cell(rt, img, cw if (c + r) & 1 else 0, 0, cw, ch, xx, yy)
            xx, c = _s16(xx + cw), _s16(c + 1)
        yy, r = _s16(yy + ch), _s16(r + 1)
    _restore_clip(rt, saved)
    mc.ret(0)

def _draw_number(rt, dst, img, num, cw, ch, gap, x, y, align):

    num = num - (1 << 32) if num & 0x80000000 else num
    v = abs(num)
    digits, q = 1, v
    while q // 10:
        q //= 10
        digits += 1
    if num < 0:
        digits += 1
    total = _s16(digits * cw + (digits - 1) * gap)
    pos = 0
    if align == 0:
        pos = _s16(x + total - cw)
    elif align == 1:
        pos = _s16((total >> 1) + x - cw)
    elif align == 2:
        pos = _s16(x - cw)
    dst = dst or rt.fb.img
    if v == 0:
        _clipped_blit(rt, dst, img, 0, 0, cw, ch, pos, y, True)
        return
    while True:
        q, rem = divmod(v, 10)
        _clipped_blit(rt, dst, img, _s16(rem * cw), 0, cw, ch, pos, y, True)
        pos = _s16(pos - (cw + gap))
        v = q
        if not v:
            break
    if num < 0:
        _clipped_blit(rt, dst, img, _s16(10 * cw), 0, cw, ch, pos, y, True)

@impl(GAME, "DrawImageNumberEx")
def draw_image_number_ex(mc, rt):

    _draw_number(rt, mc.arg(0), mc.arg(1), mc.arg(2), _s16(mc.arg(3)), _s16(mc.arg(4)),
                 _s16(mc.arg(5)), _s16(mc.arg(6)), _s16(mc.arg(7)), mc.arg(8))
    mc.ret(0)

@impl(GAME, "DrawNumber")
def draw_number(mc, rt):

    _draw_number(rt, rt.fb.img, mc.arg(0), mc.arg(1), _s16(mc.arg(2)), _s16(mc.arg(3)),
                 _s16(mc.arg(4)), _s16(mc.arg(5)), _s16(mc.arg(6)), mc.arg(7))
    mc.ret(0)

@impl(GAME, "RefresScreen")
def refres_screen(mc, rt):

    x0, y0, x1, y1 = _clip(rt)
    if x1 - 1 > x0 and y1 - 1 > y0:
        rt.present()
    mc.ret(0)

def _alias(name, target):
    impl(GAME, name)(getattr(_api(), target))

_alias("OldLib_094", "malloc_big")
_alias("OldLib_098", "free_big")
_alias("OldLib_09c", "malloc_big")
_alias("OldLib_0a0", "free_big")
_alias("OldLib_0b0", "get_tick")
_alias("OldLib_0b4", "sys_sleep")

def _time_word(mc, rt, off):

    buf = rt.state.get("oldlib_timebuf")
    if buf is None:
        buf = rt.state["oldlib_timebuf"] = _alloc(mc, 24, "oldlib_time")
    mc.setreg(0, buf)
    _api().get_current_time(mc, rt)
    mc.ret(mc.r32(buf + off))

@impl(GAME, "OldLib_0a4")
def time_word8(mc, rt):
    _time_word(mc, rt, 8)

@impl(GAME, "OldLib_0a8")
def time_word4(mc, rt):
    _time_word(mc, rt, 4)

@impl(GAME, "OldLib_0ac")
def time_word0(mc, rt):
    _time_word(mc, rt, 0)

@impl(GAME, "Abs")
def lib_abs(mc, rt):
    v = mc.arg(0) - (1 << 32) if mc.arg(0) & 0x80000000 else mc.arg(0)
    mc.ret(_s16(-v if v < 0 else v) & 0xFFFFFFFF)

def _i32(v):
    return v - (1 << 32) if v & 0x80000000 else v

@impl(GAME, "Max")
def lib_max(mc, rt):
    a, b = _i32(mc.arg(0)), _i32(mc.arg(1))
    mc.ret(_s16(b if a <= b else a) & 0xFFFFFFFF)

@impl(GAME, "Min")
def lib_min(mc, rt):
    a, b = _i32(mc.arg(0)), _i32(mc.arg(1))
    mc.ret(_s16(b if a >= b else a) & 0xFFFFFFFF)

@impl(GAME, "Random")
def lib_random(mc, rt):

    lo, hi = _i32(mc.arg(0)), _i32(mc.arg(1))
    st = (1103515245 * rt.state.get("rand", 0x12345678) + 12345) & 0x7FFFFFFF
    rt.state["rand"] = st
    rem = _cmod(st, hi - lo + 1)
    mc.ret(_s16(abs(_s16(rem)) + lo) & 0xFFFFFFFF)

@impl(GAME, "initDFActor")
def init_df_actor(mc, rt):
    a = mc.arg(0)
    _w16(mc, a, mc.arg(1))
    _w16(mc, a + 2, mc.arg(2))
    for off, name, fn in ((16, "DF_Actor+0x10", actor_load), (20, "DF_Actor+0x14", actor_draw),
                          (24, "DF_Actor+0x18", actor_draw_at), (28, "DF_Actor+0x1c", actor_next),
                          (32, "DF_Actor+0x20", actor_set_action), (36, "DF_Actor+0x24", actor_last),
                          (40, "DF_Actor+0x28", actor_collide)):
        mc.w32(a + off, rt.method(name, fn))
    for off in (6, 8, 10, 4):
        _w16(mc, a + off, 0)
    mc.ret(a)

def actor_load(mc, rt):
    a, lib, name = mc.arg(0), mc.arg(1), mc.arg(2)
    api = _api()

    def call_host(fn, *args):
        for i, v in enumerate(args):
            mc.setreg(i, v)
        fn(mc, rt)
        return mc.reg(0)

    def run():
        res = call_host(api.df_res_by_name, name)
        buf = call_host(api.get_stream_data, res)
        pos = [0]

        def rint():
            v = int.from_bytes(bytes(mc.uc.mem_read(buf + pos[0], 4)), "little")
            pos[0] += 4
            return _s16(v)

        def rstr():
            p = mc.heap.alloc(4, "df_pos")
            mc.w32(p, pos[0])
            r = call_host(api.df_read_string, buf, p)
            pos[0] = mc.r32(p)
            mc.heap.free(p)
            return r

        head = _alloc(mc, 20, "actor_head")
        mc.w32(a + 12, head)
        n = rint()
        if n > 0:
            imgs = _alloc(mc, 2 * n, "actor_imgs")
            mc.w32(head, imgs)
            for i in range(n):
                s = rstr()
                idx = yield mc.r32(lib + 28), (lib, s)
                _w16(mc, imgs + 2 * i, idx)
        n = rint()
        if n > 0:
            frames = _alloc(mc, 10 * n, "actor_frames")
            mc.w32(head + 4, frames)
            for i in range(n):
                f = frames + 10 * i
                x0 = rint(); _w16(mc, f, x0)
                y0 = rint(); _w16(mc, f + 2, y0)
                _w16(mc, f + 4, rint() - x0)
                _w16(mc, f + 6, rint() - y0)
                _w16(mc, f + 8, rint())
        n = rint()
        _w16(mc, head + 8, n)
        if n > 0:
            acts = _alloc(mc, 8 * n, "actor_acts")
            mc.w32(head + 12, acts)
            for i in range(n):
                act = acts + 8 * i
                nf = rint()
                _w16(mc, act, nf)
                if nf <= 0:
                    continue
                fl = _alloc(mc, 8 * nf, "actor_act_frames")
                mc.w32(act + 4, fl)
                for j in range(nf):
                    fr = fl + 8 * j
                    _w16(mc, fr, rint())
                    np_ = rint()
                    _w16(mc, fr + 2, np_)
                    if np_ <= 0:
                        continue
                    parts = _alloc(mc, 10 * np_, "actor_parts")
                    mc.w32(fr + 4, parts)
                    for k in range(np_):
                        for m in range(5):
                            _w16(mc, parts + 10 * k + 2 * m, rint())
        mc.w32(head + 16, lib)
        call_host(api.free_big, buf)
        return 0

    rt.cocall(mc, run())

def _actor_frame(mc, a):
    head = mc.r32(a + 12)
    act = mc.r32(head + 12) + 8 * _rs16(mc, a + 6)
    return mc.r32(act + 4) + 8 * _rs16(mc, a + 8)

def _actor_draw(mc, rt, a, ox, oy):
    def run():
        fr = _actor_frame(mc, a)
        i = 0
        while _rs16(mc, fr + 2) > i:
            part = mc.r32(fr + 4) + 10 * i
            head = mc.r32(a + 12)
            f = mc.r32(head + 4) + 10 * _rs16(mc, part)
            lib = mc.r32(head + 16)
            yield mc.r32(lib + 64), (
                lib, _rs16(mc, mc.r32(head) + 2 * _rs16(mc, f + 8)) & 0xFFFFFFFF,
                _s16(_rs16(mc, part + 2) + mc.r16(a) - ox) & 0xFFFFFFFF,
                _s16(_rs16(mc, part + 4) + mc.r16(a + 2) - oy) & 0xFFFFFFFF,
                _rs16(mc, f) & 0xFFFFFFFF, _rs16(mc, f + 2) & 0xFFFFFFFF,
                _rs16(mc, f + 4) & 0xFFFFFFFF, _rs16(mc, f + 6) & 0xFFFFFFFF,
                mc.r16(part + 6) & 0xFF, 0)
            i = _s16(i + 1)
        return 0
    rt.cocall(mc, run())

def actor_draw(mc, rt):
    _actor_draw(mc, rt, mc.arg(0), 0, 0)

def actor_draw_at(mc, rt):
    _actor_draw(mc, rt, mc.arg(0), _s16(mc.arg(1)), _s16(mc.arg(2)))

def _actor_duration(mc, a):
    head = mc.r32(a + 12)
    act = mc.r32(head + 12) + 8 * _rs16(mc, a + 6)
    frame, last = _rs16(mc, a + 8), _rs16(mc, act) - 1
    fl = mc.r32(act + 4)
    return _rs16(mc, fl + 8 * frame) if last <= frame else _rs16(mc, fl + 8 * frame + 8)

def actor_next(mc, rt):

    a = mc.arg(0)
    _w16(mc, a + 10, mc.r16(a + 10) + 1)
    r = _actor_duration(mc, a)
    if r <= _rs16(mc, a + 10):
        r = _s16(mc.r16(a + 8) + 1)
        _w16(mc, a + 8, r)
        head = mc.r32(a + 12)
        act = mc.r32(head + 12) + 8 * _rs16(mc, a + 6)
        if _rs16(mc, act) <= r:
            _w16(mc, a + 8, 0)
            _w16(mc, a + 10, 0)
            r = 0
    mc.ret(r & 0xFFFFFFFF)

def actor_set_action(mc, rt):
    a, act = mc.arg(0), _i32(mc.arg(1))
    if _rs16(mc, a + 6) != act and _rs16(mc, mc.r32(a + 12) + 8) > act:
        _w16(mc, a + 6, act)
        _w16(mc, a + 8, 0)
        _w16(mc, a + 10, 0)
    mc.ret(a)

def actor_last(mc, rt):
    a = mc.arg(0)
    act = mc.r32(mc.r32(a + 12) + 12) + 8 * _rs16(mc, a + 6)
    mc.ret(_rs16(mc, mc.r32(act + 4) + 8 * _rs16(mc, act) - 8) & 0xFFFFFFFF)

def actor_collide(mc, rt):

    a, b, ta, tb = mc.arg(0), mc.arg(1), _i32(mc.arg(2)), _i32(mc.arg(3))
    fa, fb = _actor_frame(mc, a), _actor_frame(mc, b)
    ha, hb = mc.r32(a + 12), mc.r32(b + 12)
    for i in range(_rs16(mc, fa + 2)):
        pa = mc.r32(fa + 4) + 10 * i
        for j in range(_rs16(mc, fb + 2)):
            if _rs16(mc, pa + 8) != ta:
                continue
            pb = mc.r32(fb + 4) + 10 * j
            if _rs16(mc, pb + 8) != tb:
                continue
            ra = mc.r32(ha + 4) + 10 * _rs16(mc, pa)
            rb = mc.r32(hb + 4) + 10 * _rs16(mc, pb)
            ax, ay = _rs16(mc, pa + 2) + _rs16(mc, a), _rs16(mc, pa + 4) + _rs16(mc, a + 2)
            bx, by = _rs16(mc, pb + 2) + _rs16(mc, b), _rs16(mc, pb + 4) + _rs16(mc, b + 2)
            aw, ah = _rs16(mc, ra + 4), _rs16(mc, ra + 6)
            bw, bh = _rs16(mc, rb + 4), _rs16(mc, rb + 6)
            if (ax + aw - 1 >= bx and bx + bw - 1 >= ax and ay + ah - 1 >= by
                    and by + bh - 1 >= ay):
                mc.ret(1)
                return
    mc.ret(0)

def _lcd_forward(mc, rt, fn, args):

    from unicorn.arm_const import UC_ARM_REG_SP
    for i, v in enumerate(args[:4]):
        mc.setreg(i, v & 0xFFFFFFFF)
    sp = mc.uc.reg_read(UC_ARM_REG_SP)
    extra = args[4:]
    saved = bytes(mc.uc.mem_read(sp, 4 * len(extra))) if extra else b""
    for i, v in enumerate(extra):
        mc.w32(sp + 4 * i, v & 0xFFFFFFFF)
    fn(mc, rt)
    if extra:
        mc.uc.mem_write(sp, saved)

def _u16a(mc, *idx):
    return [mc.arg(i) & 0xFFFF for i in idx]

@impl(GAME, "DrawVLine")
def draw_vline(mc, rt):
    x, y, y2, c = _u16a(mc, 1, 2, 4, 5)
    _lcd_forward(mc, rt, _api().draw_line_ex, [x, y, x, y2, c])

@impl(GAME, "DrawHLine")
def draw_hline(mc, rt):
    x, y, x2, c = _u16a(mc, 1, 2, 3, 5)
    _lcd_forward(mc, rt, _api().draw_line_ex, [x, y, x2, y, c])

@impl(GAME, "DrawLine")
def draw_line(mc, rt):
    _lcd_forward(mc, rt, _api().draw_line_ex, _u16a(mc, 1, 2, 3, 4, 5))

@impl(GAME, "DrawRect")
def draw_rect(mc, rt):
    _lcd_forward(mc, rt, _api().draw_rect_ex, _u16a(mc, 1, 2, 3, 4, 5))

@impl(GAME, "OldLib_064")
def draw_string_rect_old(mc, rt):
    s, x, y, w, h, c = mc.arg(1), mc.arg(2) & 0xFFFF, mc.arg(3) & 0xFFFF, mc.arg(4), mc.arg(5), mc.arg(6)
    x2 = (_font_w(rt, True) + w - 1) & 0xFFFF
    _lcd_forward(mc, rt, _api().draw_string_rect, [s, x, y, x2, h, c])

ZHIFEI = {i: bytes([0xA3, 0xB0 + i]) for i in range(1, 10)}
ZHIFEI[10] = b"\xa3\xb1\xa3\xb0"

@impl(GAME, "NumToZhiFei")
def num_to_zhifei(mc, rt):
    n = mc.arg(0)
    key = n if n in ZHIFEI else 0
    tab = rt.state.setdefault("zhifei", {})
    if key not in tab:
        text = ZHIFEI.get(key, b"\xa3\xb0") + b"\x00"
        p = _alloc(mc, len(text), "NumToZhiFei")
        mc.uc.mem_write(p, text)
        tab[key] = p
    mc.ret(tab[key])
