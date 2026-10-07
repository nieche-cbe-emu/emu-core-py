
import os
import struct
import subprocess

STOPPED, PLAYING, PAUSED = 0, 1, 2

MAGIC = [(b"MThd", "mid"), (b"RIFF", "wav"), (b"#!AMR", "amr"),
         (b"ID3", "mp3"), (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"),
         (b"\xff\xe3", "mp3"), (b"\xff\xf2", "mp3")]

def sniff(data):
    for m, ext in MAGIC:
        if data.startswith(m):
            return ext
    return "bin"

def midi_duration_ms(d):

    try:
        if not d.startswith(b"MThd"):
            return 1000
        div = struct.unpack_from(">h", d, 12)[0]
        ticks_total = 0
        o = 14
        tempo = 500000
        while o + 8 <= len(d):
            tag = d[o:o + 4]
            ln = struct.unpack_from(">I", d, o + 4)[0]
            body = d[o + 8:o + 8 + ln]
            if tag == b"MTrk":
                t, p, run = 0, 0, 0
                while p < len(body):
                    dt = 0
                    while p < len(body):
                        b = body[p]; p += 1
                        dt = (dt << 7) | (b & 0x7F)
                        if not b & 0x80:
                            break
                    t += dt
                    if p >= len(body):
                        break
                    st = body[p]
                    if st < 0x80:
                        st = run
                    else:
                        p += 1; run = st
                    if st == 0xFF:
                        mt = body[p]; p += 1
                        ln2 = 0
                        while p < len(body):
                            b = body[p]; p += 1
                            ln2 = (ln2 << 7) | (b & 0x7F)
                            if not b & 0x80:
                                break
                        if mt == 0x51 and ln2 == 3:
                            tempo = int.from_bytes(body[p:p + 3], "big")
                        p += ln2
                    elif st in (0xC0 | (st & 0x0F), 0xD0 | (st & 0x0F)) and (st & 0xF0) in (0xC0, 0xD0):
                        p += 1
                    elif (st & 0xF0) == 0xF0:
                        pass
                    else:
                        p += 2
                ticks_total = max(ticks_total, t)
            o += 8 + ln
        if div > 0 and ticks_total:
            return int(ticks_total * tempo / div / 1000)
    except Exception:
        pass
    return 1000

_MP3_BR = {
    1: {1: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
        2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
        3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)},
    2: {1: (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
        2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        3: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)},
}

def mp3_duration_ms(d):

    n = len(d)
    o = 0
    if d[:3] == b"ID3" and n >= 10:

        o = 10 + ((d[6] & 0x7F) << 21 | (d[7] & 0x7F) << 14
                  | (d[8] & 0x7F) << 7 | (d[9] & 0x7F))
    i = o
    while i + 4 <= n:
        if d[i] == 0xFF and (d[i + 1] & 0xE0) == 0xE0:
            ver = 1 if (d[i + 1] >> 3) & 1 else 2
            layer = 4 - ((d[i + 1] >> 1) & 3)
            bri = (d[i + 2] >> 4) & 0x0F
            sri = (d[i + 2] >> 2) & 0x03
            if layer in (1, 2, 3) and bri not in (0, 0x0F) and sri != 3:
                kbps = _MP3_BR[ver][layer][bri]
                if kbps:
                    return max(100, (n - i) * 8 // kbps)
        i += 1
    return max(500, n * 8 // 32)

def wav_duration_ms(d):

    n = len(d)
    if n < 44 or d[:4] != b"RIFF" or d[8:12] != b"WAVE":
        return max(500, n // 8)
    o, rate, data = 12, 0, 0
    while o + 8 <= n:
        tag = d[o:o + 4]
        ln = struct.unpack_from("<I", d, o + 4)[0]
        if tag == b"fmt " and ln >= 16:
            rate = struct.unpack_from("<I", d, o + 16)[0]
        elif tag == b"data":
            data = min(ln, n - o - 8)
            break
        o += 8 + ln + (ln & 1)
    if rate and data:
        return max(100, data * 1000 // rate)
    return max(500, n // 8)

class Audio:
    def __init__(self, rt, outdir=None, play=False):
        self.rt = rt
        self.outdir = outdir
        self.play = play
        self.state = STOPPED
        self.volume = 5
        self.ends_at = 0
        self.loop = 0
        self.current = None
        self.events = []
        self.proc = None
        self._dumped = {}

        self.on_event = None

    def _dump(self, data, name):
        if not self.outdir:
            return None
        key = (name, len(data))
        if key in self._dumped:
            return self._dumped[key]
        os.makedirs(self.outdir, exist_ok=True)
        ext = sniff(data)
        base = name[:-len(ext) - 1] if name.lower().endswith("." + ext) else name
        path = os.path.join(self.outdir, f"{base}.{ext}")
        with open(path, "wb") as f:
            f.write(data)
        self._dumped[key] = path
        return path

    def _spawn(self, path):
        if not self.play or not path or path.endswith((".mid", ".bin")):
            return
        try:
            self.stop_proc()
            self.proc = subprocess.Popen(["/usr/bin/afplay", path],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except Exception:
            self.proc = None

    def stop_proc(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    def play_data(self, data, loop, name="audio"):
        if not data:
            self.state = STOPPED
            return 0
        ext = sniff(data)
        if ext == "mid":
            dur = midi_duration_ms(data)
        elif ext == "mp3":
            dur = mp3_duration_ms(data)
        elif ext == "wav":
            dur = wav_duration_ms(data)
        else:
            dur = max(500, len(data) // 8)
        self.loop = loop
        self.state = PLAYING
        self.ends_at = self.rt.tick + dur
        self.current = name
        path = self._dump(data, name)
        if self.on_event:

            self.on_event({"op": "play", "path": path, "loop": bool(loop),
                           "ext": ext, "name": name})
        else:
            self._spawn(path)
        self.events.append((self.rt.tick, f"播放 {name} ({sniff(data)}, ~{dur}ms, loop={loop})"))
        return 1

    def tick(self):

        if self.state == PLAYING and self.rt.tick >= self.ends_at:
            if self.loop:
                self.ends_at = self.rt.tick + max(1, self.ends_at - self.rt.tick or 1000)
            else:
                self.state = STOPPED
                self.stop_proc()
                if self.on_event:
                    self.on_event({"op": "stop"})
