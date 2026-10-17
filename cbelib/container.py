
import struct
import re
from dataclasses import dataclass, field
from typing import List, Optional

MAGIC_TAIL = b"CoolBars"
FOOTER_LEN = 44

class CbeError(Exception):
    pass

@dataclass
class ResEntry:
    name: str
    off: int
    size: int
    data: bytes = field(repr=False, default=b"")

@dataclass
class ResArchive:
    base: int
    size: int
    count: int
    data_off: int
    data_size: int
    entries: List[ResEntry]

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.entries[key]
        for e in self.entries:
            if e.name == key:
                return e
        raise KeyError(key)

    def names(self):
        return [e.name for e in self.entries]

def _parse_res(buf: bytes, base: int, size: int, variant: bool = False) -> Optional[ResArchive]:

    if size < 0x1C:
        return None
    if variant:
        index_size, data_size, count, zero = struct.unpack_from("<4I", buf, base)
        hdr, isz_at = 0x10, 0x00
    else:
        magic = struct.unpack_from("<I", buf, base)[0]
        if magic == 8:
            magic, n_alt, flag, index_size, data_size, count, zero =                struct.unpack_from("<7I", buf, base)
            hdr, isz_at = 0x1C, 0x0C
        elif magic == 4:

            magic, n_alt, index_size, data_size, count, zero =                struct.unpack_from("<6I", buf, base)
            hdr, isz_at = 0x18, 0x08
        else:
            return None
    if count == 0 or count > 0x10000:
        return None
    data_off = isz_at + 4 + index_size
    if data_off + data_size != size:
        raise CbeError(f"res archive size mismatch: {data_off:#x}+{data_size:#x} != {size:#x}")

    offs = [0] + list(struct.unpack_from("<%dI" % (count - 1), buf, base + hdr))
    o = base + hdr + 4 * (count - 1)
    names = []
    for _ in range(count):
        ln = buf[o]
        names.append(buf[o + 1:o + 1 + ln].decode("latin1"))
        o += 1 + ln
    if o - base != data_off:
        raise CbeError(f"res name table ends at {o - base:#x}, expected {data_off:#x}")

    entries = []
    for i, (nm, off) in enumerate(zip(names, offs)):
        end = offs[i + 1] if i + 1 < count else data_size
        abs_off = base + data_off + off
        entries.append(ResEntry(nm, off, end - off, buf[abs_off:abs_off + (end - off)]))
    return ResArchive(base, size, count, data_off, data_size, entries)

def _multi_dir_end(buf: bytes, o: int, count: int, limit: int) -> int:
    for _ in range(count):
        if o >= limit:
            break
        ln = buf[o]
        if ln == 0 or ln > 48 or o + 1 + ln + 4 > limit:
            break
        if not all(32 <= c < 127 for c in buf[o + 1:o + 1 + ln]):
            break
        o += 1 + ln + 4
    return o

def _multi_header(buf: bytes, base: int):

    head = struct.unpack_from("<I", buf, base)[0]
    limit = len(buf)
    for cnt_at in (8, 4):
        count = struct.unpack_from("<I", buf, base + cnt_at)[0]
        if 0 < count < 512:
            o = base + cnt_at + 4
            if _multi_dir_end(buf, o, count, limit) - base - 4 == head:
                return count, o
    return struct.unpack_from("<I", buf, base + 8)[0], base + 12

def _parse_multi(buf: bytes, base: int, size: int):

    if size < 16:
        return None
    count, o = _multi_header(buf, base)
    if not (0 < count < 512):
        return None
    ents = []
    for _ in range(count):
        if o >= base + size:
            break
        ln = buf[o]
        if ln == 0 or ln > 48 or o + 1 + ln + 4 > base + size:
            break
        nm = buf[o + 1:o + 1 + ln]
        if not all(32 <= c < 127 for c in nm):
            break
        o += 1 + ln
        off = struct.unpack_from("<I", buf, o)[0]
        o += 4
        if off >= size:
            break
        ents.append((nm.decode("latin1"), off))
    if not ents:
        return None
    packs = {}
    bounds = [off for _, off in ents] + [size]
    for i, (nm, off) in enumerate(ents):
        try:
            a = _parse_res(buf, base + off, bounds[i + 1] - off, variant=True)
        except CbeError:
            a = None
        if a:
            packs[nm] = a

    root_off = o - base
    if len(ents) < count and root_off < ents[0][1]:
        try:
            a = _parse_res(buf, o, ents[0][1] - root_off, variant=True)
        except CbeError:
            a = None
        if a:
            packs[""] = a
    return packs or None

@dataclass
class CbeModule:
    path: str
    raw: bytes = field(repr=False)
    name: str
    load_base: int
    image_size: int
    image_end: int
    rw_size: int
    ro: bytes = field(repr=False)
    rw: bytes = field(repr=False)
    ro_off: int
    rw_off: int
    ro_chk: int
    rw_chk: int
    endian: str
    icons: Optional[ResArchive]
    res: Optional[ResArchive]
    packages: dict = field(default_factory=dict)

    res_off: int = 0

    @property
    def bss_size(self):
        return self.rw_size - len(self.rw)

    @property
    def thumb(self):
        return True

def _skip_fe(d, o):
    while o < len(d) and d[o] == 0xFE:
        o += 1
    return o

def _detect_endian(d: bytes) -> str:
    le = d.count(b"\x1e\xff\x2f\xe1") + len(re.findall(rb"[\x00-\xff][\x40-\x4f]\x2d\xe9", d))
    be = d.count(b"\xe1\x2f\xff\x1e") + len(re.findall(rb"\xe9\x2d[\x40-\x4f][\x00-\xff]", d))
    return "LE" if le >= be else "BE"

def load(path: str) -> CbeModule:
    with open(path, "rb") as f:
        d = f.read()
    if not d.endswith(MAGIC_TAIL):
        raise CbeError("missing 'CoolBars' trailer — not a CBE module?")
    foot = struct.unpack_from(">I", d, len(d) - 12)[0]
    if foot != len(d) - FOOTER_LEN:
        raise CbeError(f"footer offset {foot:#x} != {len(d) - FOOTER_LEN:#x}")

    o = 0
    vals = []
    for i in range(6):
        o = _skip_fe(d, o)
        if i == 5:
            vals.append(d[o:o + vals[4]].decode("latin1"))
            o += vals[4]
        else:
            vals.append(struct.unpack_from(">I", d, o)[0])
            o += 4
    sizes = []
    for _ in range(6):
        o = _skip_fe(d, o)
        sizes.append(struct.unpack_from(">I", d, o)[0])
        o += 4

    load_base, image_size, image_end, rw_size, _nl, name = vals
    ro_sz, ro_chk, rw_sz, rw_chk, ico_sz, _ico_chk = sizes

    ro_off = _skip_fe(d, o)
    rw_off = _skip_fe(d, ro_off + ro_sz)
    ico_off = _skip_fe(d, rw_off + rw_sz)
    e = _skip_fe(d, ico_off + ico_sz)
    res_size = struct.unpack_from(">I", d, e)[0]
    res_off = _skip_fe(d, e + 4)
    if res_off + res_size != len(d) - FOOTER_LEN - 12:

        pass

    try:
        res = _parse_res(d, res_off, res_size)
    except CbeError:
        res = None
    packs = {}
    if res is None:
        packs = _parse_multi(d, res_off, res_size) or {}
        if packs:
            res = max(packs.values(), key=lambda a: a.count)

    return CbeModule(
        path=path, raw=d, name=name,
        load_base=load_base, image_size=image_size, image_end=image_end,
        rw_size=rw_size,
        ro=d[ro_off:ro_off + ro_sz], rw=d[rw_off:rw_off + rw_sz],
        ro_off=ro_off, rw_off=rw_off, ro_chk=ro_chk, rw_chk=rw_chk,
        endian=_detect_endian(d),
        icons=_parse_res(d, ico_off, ico_sz),
        res=res, packages=packs, res_off=res_off,
    )
