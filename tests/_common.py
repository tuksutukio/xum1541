"""Shared test setup: import diskmasher from the repo, keep its log out of ~/Library/Logs,
build .d64 images, and run the fake opencbm tools."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import diskmasher as d  # noqa: E402


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _text(path):
    with open(path) as f:
        return f.read()

_LOG_DIR = tempfile.mkdtemp(prefix="diskmasher-test-log-")
d.LOG_FILE = os.path.join(_LOG_DIR, "diskmasher.log")
d.DIR_BACKUP_DIR = os.path.join(_LOG_DIR, "directory-backups")


# --- .d64 builder ---

def make_d64(tracks=35, name="TEST", files=(), errors=None, double_sided=False,
             ext_bam=None, raw=None):
    """A .d64 image as bytes.

    files: dicts with name, sectors [(t, s), ...] (the chain, in order), and optionally
      type (directory type byte, default $82 = closed PRG), geos (True sets byte $18),
      blocks (directory block count, default len(sectors)), link (override the last
      sector's link, e.g. (99, 0) for a broken chain or a sector to loop back to).
    errors: {(t, s): code} for the error table (others 1 = OK); None = no table.
    ext_bam: "speed" (bytes at $C0) or "dolphin" (bytes at $AC).
    raw: {(t, s): 256 bytes} written last, as is.
    """
    spt = d.SECTORS_PER_TRACK
    nblocks = sum(spt[:tracks])
    img = bytearray(nblocks * 256)

    def put(t, s, data):
        o = d._blk(t, s) * 256
        img[o:o + len(data)] = data

    bam = bytearray(256)
    bam[0:4] = bytes([18, 1, 0x41, 0x80 if double_sided else 0])
    bam[0x90:0xA0] = name.encode("latin-1")[:16].ljust(16, b"\xa0")
    bam[0xA2:0xA4] = b"01"
    if ext_bam == "speed":
        bam[0xC0] = 0x11
    elif ext_bam == "dolphin":
        bam[0xAC] = 0x11
    put(18, 0, bam)

    # Directory: 8 entries per sector, chained 18/1, 18/4, 18/7, … like the DOS does.
    files = list(files)
    # The 1541 DOS's own order (interleave 3, wrapping on track 18's 19 sectors).
    dos_order = [1, 4, 7, 10, 13, 16, 2, 5, 8, 11, 14, 17, 3, 6, 9, 12, 15, 18]
    dir_secs = dos_order[:max(1, (len(files) + 7) // 8)]
    dir_data = {s: bytearray(256) for s in dir_secs}
    for n, s in enumerate(dir_secs):
        nxt = dir_secs[n + 1] if n + 1 < len(dir_secs) else None
        dir_data[s][0:2] = bytes([18, nxt]) if nxt else bytes([0, 0xFF])
    for i, f in enumerate(files):
        dir_sector, i = dir_data[dir_secs[i // 8]], i % 8
        e = bytearray(32)
        e[2] = f.get("type", 0x82)
        chain = f["sectors"]
        e[3], e[4] = chain[0] if chain else (0, 0)
        e[5:21] = f["name"].encode("latin-1")[:16].ljust(16, b"\xa0")
        if f.get("geos"):
            e[0x18] = 1
        blocks = f.get("blocks", len(chain))
        e[30], e[31] = blocks & 0xFF, blocks >> 8
        dir_sector[32 * i + 2:32 * i + 32] = e[2:]
        content = f.get("data")              # real file bytes, 254 per sector
        for j, (t, s) in enumerate(chain):
            blk = bytearray(b"\x55" * 256)
            if content is not None:
                chunk = content[254 * j:254 * j + 254]
                blk[2:2 + len(chunk)] = chunk
            if j + 1 < len(chain):
                blk[0], blk[1] = chain[j + 1]
            elif content is not None:
                blk[0], blk[1] = 0, len(content) - 254 * j + 1   # last used byte
            else:
                blk[0], blk[1] = f.get("link", (0, 0xFF))
            put(t, s, blk)
    for s, data in dir_data.items():
        put(18, s, data)

    for (t, s), data in (raw or {}).items():
        put(t, s, data)

    if errors is not None:
        table = bytearray([1] * nblocks)
        for (t, s), code in errors.items():
            table[d._blk(t, s)] = code
        img += table
    return bytes(img)


def basic_rem(text, line=10, addr=0x0801):
    """A one-line BASIC program `<line> REM <text>` as PRG bytes (load address first):
    LIST then shows e.g. "10 rem game", so the screen tells which file was loaded."""
    body = bytes([line & 0xFF, line >> 8, 0x8F, 0x20]) + text.upper().encode() + b"\x00"
    nxt = addr + 2 + len(body)
    return bytes([addr & 0xFF, addr >> 8, nxt & 0xFF, nxt >> 8]) + body + b"\x00\x00"


# --- fake opencbm tools ---

class FakeTools(unittest.TestCase):
    """Base class: puts fake cbmctrl/cbmread/cbmwrite/d64copy first on PATH, makes
    time.sleep instant, and selects drive 8. Set self.scenario(...) before acting."""

    drive = "8"

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="fakecbm-")
        self.bin = os.path.join(self.state, "bin")
        os.mkdir(self.bin)
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_opencbm.py")
        for tool in ("cbmctrl", "cbmread", "cbmwrite", "d64copy"):
            p = os.path.join(self.bin, tool)
            with open(p, "w") as f:
                f.write(f'#!/bin/sh\nexec "{sys.executable}" -B "{fake}" {tool} "$@"\n')
            os.chmod(p, 0o755)
        self._env = mock.patch.dict(os.environ, {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_CBM_DIR": self.state})
        self._env.start()
        self._sleep = mock.patch.object(d.time, "sleep", lambda s: None)
        self._sleep.start()
        self._old_drive, d.DRIVE = d.DRIVE, self.drive
        self.scenario()

    def tearDown(self):
        d.DRIVE = self._old_drive
        self._sleep.stop()
        self._env.stop()
        shutil.rmtree(self.state, ignore_errors=True)

    def scenario(self, **kw):
        kw.setdefault("detect", f" {self.drive}: 1571")
        with open(os.path.join(self.state, "scenario.json"), "w") as f:
            json.dump(kw, f)

    def calls(self):
        p = os.path.join(self.state, "calls.log")
        return _text(p).splitlines() if os.path.exists(p) else []

    def jobs(self):
        """[(code, track)] for every job put in slot 2."""
        p = os.path.join(self.state, "jobs.log")
        if not os.path.exists(p):
            return []
        return [(int(c, 16), int(t)) for c, t, *_ in (l.split() for l in _text(p).splitlines())]

    def jobs_ts(self):
        """[(code, track, sector)] for every job put in slot 2."""
        p = os.path.join(self.state, "jobs.log")
        if not os.path.exists(p):
            return []
        return [tuple(int(x, 16) if i == 0 else int(x) for i, x in enumerate(l.split()))
                for l in _text(p).splitlines()]

    def commands(self):
        """The DOS commands sent with `cbmctrl command`."""
        return [c.split(" ", 3)[3] for c in self.calls() if c.startswith("cbmctrl command ")]

    def mem(self, addr, count):
        p = os.path.join(self.state, "mem.bin")
        return _bytes(p)[addr:addr + count] if os.path.exists(p) else b""

    def write_file(self, name, data):
        p = os.path.join(self.state, name)
        with open(p, "wb") as f:
            f.write(data)
        return p
