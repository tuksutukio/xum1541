#!/opt/homebrew/bin/python3.13
"""
diskmasher — CBM disk writing tool
Commander-style split view: filesystem navigator | CBM device directory
"""

import configparser
import glob
import json
import math
import os
import pty
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

PREFS_FILE = os.path.expanduser("~/.config/diskmasher/prefs.ini")

def _load_prefs():
    cfg = configparser.ConfigParser()
    cfg.read(PREFS_FILE)
    return cfg

def _save_prefs(cfg):
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        cfg.write(f)

DRIVE = "8"   # the drive all operations use; changed only by DiskMasher._select_drive
START_DIR = os.path.expanduser("~")
RELEVANT_EXTENSIONS = {".d64", ".d71", ".d81", ".prg", ".seq", ".usr", ".t64", ".tap", ".crt"}
TYPE_EXT = {"PRG": ".prg", "SEQ": ".seq", "USR": ".usr", "REL": ".rel"}

_HERE = os.path.dirname(os.path.abspath(__file__))
def _find_c1541():
    """Find c1541 from an installed VICE app bundle. Returns (path, env) or (None, {})."""
    for vice_app in sorted(glob.glob("/Applications/VICE*"), reverse=True):
        script = os.path.join(vice_app, "bin", "c1541")
        if os.path.exists(script):
            return script, {}
    return None, {}
_C1541, _C1541_ENV = _find_c1541()
FB_PATHS = {
    "C16/Plus4": [("FB", os.path.join(_HERE, "fb", "fb16"))],
    "C64":       [("FB", os.path.join(_HERE, "fb", "fb64"))],
    "C128":      [("FB", os.path.join(_HERE, "fb", "fb128"))],
    "VIC-20": [
        ("FB",      os.path.join(_HERE, "fb", "fb")),
        ("FB20",    os.path.join(_HERE, "fb", "fb20")),
        ("FB20-3K", os.path.join(_HERE, "fb", "fb20-3k")),
        ("FB20-8K", os.path.join(_HERE, "fb", "fb20-8k")),
        ("FB20-MC", os.path.join(_HERE, "fb", "fb20-mc")),
    ],
    "All": [
        ("FB",      os.path.join(_HERE, "fb", "fb")),
        ("FB16",    os.path.join(_HERE, "fb", "fb16")),
        ("FB20",    os.path.join(_HERE, "fb", "fb20")),
        ("FB20-3K", os.path.join(_HERE, "fb", "fb20-3k")),
        ("FB20-8K", os.path.join(_HERE, "fb", "fb20-8k")),
        ("FB20-MC", os.path.join(_HERE, "fb", "fb20-mc")),
        ("FB64",    os.path.join(_HERE, "fb", "fb64")),
        ("FB128",   os.path.join(_HERE, "fb", "fb128")),
    ],
}

IMAGE_FORMATS = {
    174848: ("35-track d64",            ".d64", False, False),  # label, ext, is_40track, is_d71
    175531: ("35-track d64 +errorbytes",".d64", False, False),  # 174848 + 683 error bytes
    196608: ("40-track d64",            ".d64", True,  False),
    197376: ("40-track d64 +errorbytes",".d64", True,  False),  # 196608 + 768 error bytes
    349696: ("d71 two-sided",           ".d71", False, True),
    351062: ("d71 +errorbytes",         ".d71", False, True),   # 349696 + 1366 error bytes
    819200: ("d81 (1581)",              ".d81", False, False),
}

# --- Drive command log ---
# Every opencbm command goes into ~/Library/Logs/diskmasher.log with its duration, how it
# ended and its last output lines, to catch intermittent faults (2026-11-25: a track-36
# read that sometimes hung for 30 s and couldn't be reproduced from the command line).

LOG_FILE  = os.path.expanduser("~/Library/Logs/diskmasher.log")
_log_lock = threading.Lock()


def log_event(text):
    """Append one timestamped line; never raises. Rotates to .1 past 1 MB."""
    try:
        with _log_lock:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
                os.replace(LOG_FILE, LOG_FILE + ".1")
            with open(LOG_FILE, "a") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except Exception:
        pass


def log_cmd(cmd, started, outcome, output=""):
    """Log a finished command: duration, outcome (exit code, 'HUNG', 'stopped'…), last lines."""
    lines = [l.strip() for l in (output or "").replace("\r", "\n").splitlines() if l.strip()]
    tail  = " | ".join(lines[-3:])
    log_event(f"{' '.join(map(str, cmd))}  [{time.monotonic() - started:.1f}s, {outcome}]"
              + (f"  {tail}" if tail else ""))


# --- Disk image analysis (pure functions on image bytes; no drive access) ---

SECTORS_PER_TRACK = [21] * 17 + [19] * 7 + [18] * 6 + [17] * 10   # tracks 1–40 (1541 zones)
FILE_TYPES = {0: "DEL", 1: "SEQ", 2: "PRG", 3: "USR", 4: "REL"}


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _blk(track, sector):
    """Block index of track/sector in a d64 image."""
    return sum(SECTORS_PER_TRACK[:track - 1]) + sector


def _format_safe(raw):
    """PETSCII bytes as text cbmforng can take: it uppercases its argument, so only
    $20–$5A pass through unchanged (no comma). None if anything else is in there."""
    if not raw or any(b < 0x20 or b > 0x5A or b == 0x2C for b in raw):
        return None
    return raw.decode("ascii")


def format_header_problem(name, disk_id):
    """Why `name`,`disk_id` can't go to cbmforng/cbmformat as typed, or None if fine.
    The ID is any two characters (drive-1571-users-guide-1985 p.18), the name up to 16.
    The tools uppercase their argument, so letters are fine in either case; other
    characters must be plain ASCII $20–$5A. No comma (it separates name and ID) and no
    quote (it would break the directory header)."""
    for label, text in (("name", name), ("ID", disk_id)):
        bad = sorted({c for c in text if not (" " <= c.upper() <= "Z") or c in ',"'})
        if bad:
            return f"The {label} can't contain " + " ".join(repr(c) for c in bad) + "."
    if len(name) > 16:
        return f"The name is {len(name)} characters; at most 16 fit."
    if len(disk_id) != 2:
        return f"The ID must be exactly two characters (now {len(disk_id)})."
    return None


def image_format_header(data):
    """The name and ID to pre-format with before writing a .d64/.d71 image: the image's
    own, from 18/0. The write replaces the name anyway, but the ID also goes into every
    sector header at format time and the write can't change that, so formatting with the
    image's ID keeps headers and BAM matching, as on the original disk. Falls back to
    "DISK"/"01" for bytes cbmforng can't reproduce."""
    off = _blk(18, 0) * 256
    if len(data) < off + 256:
        return "DISK", "01"
    bam = data[off:off + 256]
    return (_format_safe(bam[0x90:0xA0].rstrip(b"\xa0")) or "DISK",
            _format_safe(bam[0xA2:0xA4]) or "01")


def analyze_image(data):
    """Inspect a single-sided .d64 (35 or 40 tracks, with or without error bytes).

    Returns a dict: tracks, disk_name, double_sided, ext_bam, files (name, type, blocks,
    max_track, problem), bad_sectors {track: count}, data_beyond_35 (tracks 36–40 hold
    non-blank sectors that no file uses), and problems (reasons Sort/FB-ify must not
    rewrite this disk). Raises ValueError for sizes it doesn't handle (.d71, .d81)."""
    sizes = {174848: (35, False), 175531: (35, True), 196608: (40, False), 197376: (40, True)}
    if len(data) not in sizes:
        raise ValueError(f"unsupported image size {len(data)}")
    tracks, has_err = sizes[len(data)]
    nblocks = sum(SECTORS_PER_TRACK[:tracks])
    errors  = data[nblocks * 256:] if has_err else b""

    def sector(t, s):
        o = _blk(t, s) * 256
        return data[o:o + 256]

    def valid(t, s):
        return 1 <= t <= tracks and 0 <= s < SECTORS_PER_TRACK[t - 1]

    bam = sector(18, 0)
    res = {
        "tracks": tracks,
        "disk_name": bam[0x90:0xA0].rstrip(b"\xa0").decode("latin-1"),
        "double_sided": bam[3] == 0x80,   # drive-1571-users-guide-1985 p.116
        "ext_bam": None, "files": [], "bad_sectors": {}, "data_beyond_35": [],
        "problems": [],
    }
    # Extended-BAM hint only (layouts not in c64doc; seen: cbmforng -x writes $C0–$D3).
    if any(bam[0xC0:0xD4]):
        res["ext_bam"] = "SpeedDOS-style ($C0)"
    elif any(bam[0xAC:0xC0]):
        res["ext_bam"] = "DolphinDOS-style ($AC)"

    if has_err:
        for t in range(1, tracks + 1):
            bad = sum(1 for s in range(SECTORS_PER_TRACK[t - 1])
                      if errors[_blk(t, s)] not in (0, 1))
            if bad:
                res["bad_sectors"][t] = bad

    def is_bad(t, s):
        return has_err and errors[_blk(t, s)] not in (0, 1)

    used = set()
    # Directory chain from 18/1.
    t, s, seen_dir = 18, 1, set()
    dir_sectors = []
    while t != 0:
        if not valid(t, s) or (t, s) in seen_dir:
            res["problems"].append("directory chain is broken")
            break
        seen_dir.add((t, s))
        dir_sectors.append((t, s))
        blk = sector(t, s)
        t, s = blk[0], blk[1]
    used |= seen_dir | {(18, 0)}

    for dt, ds in dir_sectors:
        blk = sector(dt, ds)
        for i in range(8):
            e = blk[32 * i: 32 * i + 32]
            ftype = e[2]
            if ftype == 0:
                continue                      # empty or scratched slot
            name  = e[5:21].rstrip(b"\xa0").decode("latin-1")
            kind  = FILE_TYPES.get(ftype & 0x07, "?")
            f = {"name": name, "type": kind, "blocks": e[30] | (e[31] << 8),
                 "max_track": 0, "problem": None}
            if not ftype & 0x80:
                f["problem"] = "not closed properly (splat file)"
            elif kind in ("REL", "DEL", "?") or e[0x18]:
                f["problem"] = ("unsupported file type " +
                                ("(GEOS)" if e[0x18] else f"({kind})"))
            if kind == "DEL":
                res["files"].append(f)        # separators: no chain worth following
                continue
            # Follow the data chain.
            ct, cs, chain = e[3], e[4], []
            while ct != 0:
                if not valid(ct, cs):
                    f["problem"] = f["problem"] or f"chain points to track {ct} sector {cs}"
                    break
                if (ct, cs) in chain or len(chain) > nblocks:
                    f["problem"] = f["problem"] or "chain loops"
                    break
                chain.append((ct, cs))
                if is_bad(ct, cs):
                    f["problem"] = f["problem"] or f"read error in track {ct} sector {cs}"
                blk2 = sector(ct, cs)
                ct, cs = blk2[0], blk2[1]
            used |= set(chain)
            f["max_track"] = max((c[0] for c in chain), default=0)
            if f["max_track"] > 35:
                f["problem"] = f["problem"] or f"uses track {f['max_track']} (beyond 35)"
            res["files"].append(f)

    def blank(t, s):
        b = sector(t, s)
        return not any(b) or (b[0] == 0x4B and not any(x != 1 for x in b[1:]))

    for t in range(36, tracks + 1):
        if any(not blank(t, s) and (t, s) not in used and not is_bad(t, s)
               for s in range(SECTORS_PER_TRACK[t - 1])):
            res["data_beyond_35"].append(t)

    if res["double_sided"]:
        res["problems"].append("the disk is double-sided (1571); only one side was read")
    for f in res["files"]:
        if f["problem"]:
            res["problems"].append(f'"{f["name"]}": {f["problem"]}')
    return res


def describe_image(info):
    """Human-readable lines for an analyze_image() result."""
    lines = [f'{info["tracks"]}-track image, {len(info["files"])} file(s).']
    if info["ext_bam"]:
        lines.append(f'Extended BAM found: {info["ext_bam"]}.')
    if info["bad_sectors"]:
        lines.append("Read errors: " + ", ".join(
            f"track {t}: {n} sector{'s' if n > 1 else ''}" for t, n in info["bad_sectors"].items()))
    if info["data_beyond_35"]:
        lines.append("Data outside any file on track(s) " +
                     ", ".join(map(str, info["data_beyond_35"])) +
                     " (loader data or leftovers; kept in the image).")
    lines += info["problems"]
    return lines


def reset_and_settle():
    """`cbmctrl reset` + 2 s for the drive to re-initialise. Run before every d64copy:
    right after a cbmread/cbmwrite, d64copy can stall (tested 2026-11-25 on a 1571: a
    track-36 read hung every time, and a full 40-track read stalled at track 31 and left
    the drive unresponsive until a power cycle). A reset first fixed it."""
    _run_bounded(["cbmctrl", "reset"], 5)
    time.sleep(2)


def after_d64copy(cancel_event=None):
    """reset_and_settle() after every d64copy, unless the run was cancelled (then the cancel
    sequence owns the bus and resets it itself). d64copy's fast-transfer code can leave a
    1571 at 2 MHz under its 1541-mode DOS (VIA 1 $1801 bit 5 stays set; believed to be the
    1/2 MHz select, drive-1571-service-manual-1986 p.12 has the line but not the pin); the DOS then
    reads fine but every file write fails with 74,DRIVE NOT READY,18,00 (controller job
    result $0F) until a reset. Seen twice on drive 8, 2026-11-25; a reset fixed it both times."""
    if cancel_event is None or not cancel_event.is_set():
        reset_and_settle()


def stream_cmd(cmd, on_text, proc_store=None):
    """Run cmd under a pty (opencbm tools buffer their progress output on a pipe) and call
    on_text(chunk) from this thread as output arrives. Returns the exit code.
    Call from a worker thread; proc_store[0] holds the process while it runs."""
    import select as _select
    started, tail = time.monotonic(), []
    def _on(t):
        tail.append(t)
        del tail[:-20]
        on_text(t)
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(cmd, stdout=slave_fd, stderr=slave_fd,
                                stdin=subprocess.DEVNULL, close_fds=True)
    finally:
        os.close(slave_fd)
    if proc_store is not None:
        proc_store[0] = proc
    try:
        while True:
            try:
                r, _, _ = _select.select([master_fd], [], [], 1.0)
                if r:
                    chunk = os.read(master_fd, 4096)
                    if not chunk:
                        break
                    _on(chunk.decode("utf-8", errors="replace")
                        .replace("\r\n", "\n").replace("\r", "\n"))
                elif proc.poll() is not None:
                    break
            except OSError:
                break
        proc.wait()
        log_cmd(cmd, started, f"exit {proc.returncode}", "".join(tail))
        return proc.returncode
    finally:
        if proc_store is not None:
            proc_store[0] = None
        try:
            os.close(master_fd)
        except OSError:
            pass


_D64COPY_PROGRESS = re.compile(r'(\d+):\s*\S*\s+(\d+)%\s+(\d+)/(\d+)')


def blocks_copied(text, tracks):
    """(copied, expected) from d64copy's 'N blocks copied.'; copied None if absent.
    Fewer than expected means sectors d64copy gave up on ('giving up...', still exit 0);
    they are marked in the image's error table."""
    m = None
    for m in re.finditer(r'(\d+) blocks copied', text or ""):
        pass
    return (int(m.group(1)) if m else None), sum(SECTORS_PER_TRACK[:tracks])


def read_disk_to_file(path, tracks, on_progress=None, proc_store=None, cancel_event=None,
                      stats=None):
    """Read the whole disk with d64copy (35 or 40 tracks) into `path`.
    on_progress(track, fraction) is called from this thread. Returns None when the read is
    complete, else an error string. d64copy creates the file at full size before reading,
    so completeness is judged from its output ('N blocks copied.') and exit code.
    If `stats` is a dict, it gets 'copied' and 'expected' block counts."""
    cmd = ["d64copy"] + (["--end-track", "40"] if tracks == 40 else []) + [DRIVE, path]
    out = []
    def _text(t):
        out.append(t)
        if on_progress:
            m = None
            for m in _D64COPY_PROGRESS.finditer(t):
                pass
            if m:
                on_progress(int(m.group(1)), int(m.group(3)) / max(1, int(m.group(4))))
    reset_and_settle()
    if cancel_event is not None and cancel_event.is_set():
        return "cancelled"      # nothing registered in proc_store yet: don't start d64copy
    try:
        rc = stream_cmd(cmd, _text, proc_store)
    except OSError as e:
        return str(e)
    after_d64copy(cancel_event)
    text = "".join(out)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    copied, expected = blocks_copied(text, tracks)
    if stats is not None:
        stats.update(copied=copied, expected=expected)
    if rc == 0 and size in IMAGE_FORMATS and copied is not None:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else f"d64copy exit code {rc}"


def probe_disk(proc_store=None, cancel_event=None, track18=True):
    """The disk check (_probe_disk), then a reset: it runs d64copy (see after_d64copy)."""
    res = _probe_disk(proc_store, cancel_event, track18)
    after_d64copy(cancel_event)
    return res


def _probe_disk(proc_store=None, cancel_event=None, track18=True):
    """Quick read-only look at the disk in the drive (about 3 s per track on a 1571).

    Reads track 18 (BAM: name, double-sided flag, extended BAM) and track 36 (is the disk
    formatted beyond 35 tracks, and does that area hold data?). Returns a dict with error,
    double_sided, ext_bam, disk_name, t36_checked (False if the track-36 read timed out),
    t36_ok (sectors of 17 that read OK), t36_readable (all 17 OK: formatted and sound),
    t36_partial (some but not all: formatted once, now damaged; seen 2026-11-25 on a
    decayed 40-track disk, 8 of 17 OK, the rest checksum errors), t36_data. On a never-formatted track 36
    d64copy gives up quickly (whole check about 7 s on a 1571, 2026-11-25); the 30 s
    watchdog is only a safety net."""
    res = {"error": None, "double_sided": False, "ext_bam": None, "disk_name": "",
           "t36_readable": False, "t36_partial": False, "t36_ok": 0,
           "t36_data": False, "t36_checked": False}
    log_event(f"--- disk check on drive {DRIVE}")
    reset_and_settle()
    if cancel_event is not None and cancel_event.is_set():
        res["error"] = "cancelled"
        return res
    with tempfile.TemporaryDirectory() as tmp:
        if track18:
            p18 = os.path.join(tmp, "t18.d64")
            r = _run_bounded(["d64copy", "-s", "18", "-e", "18", DRIVE, p18], 30, proc_store)
            if cancel_event is not None and cancel_event.is_set():
                res["error"] = "cancelled"
                return res
            data = _read_bytes(p18) if os.path.exists(p18) else b""
            if r is None or r[0] != 0 or len(data) not in IMAGE_FORMATS or \
                    not re.search(r'\d+ blocks copied', r[1]):
                res["error"] = "could not read the directory track (18)"
                return res
            bam = data[_blk(18, 0) * 256:_blk(18, 0) * 256 + 256]
            res["double_sided"] = bam[3] == 0x80
            res["disk_name"] = bam[0x90:0xA0].rstrip(b"\xa0").decode("latin-1")
            res["ext_bam"] = ("SpeedDOS-style" if any(bam[0xC0:0xD4]) else
                              "DolphinDOS-style" if any(bam[0xAC:0xC0]) else None)
        p36 = os.path.join(tmp, "t36.d64")
        r = _run_bounded(["d64copy", "-s", "36", "-e", "36", "-r", "0", DRIVE, p36], 30, proc_store)
        if cancel_event is not None and cancel_event.is_set():
            res["error"] = "cancelled"
            return res
        if r is None:
            # The watchdog stopped d64copy. Don't guess "35 tracks" from that: callers
            # must tell the user the check failed (t36_checked stays False).
            reset_and_settle()
            return res
        res["t36_checked"] = True
        data = _read_bytes(p36) if os.path.exists(p36) else b""
        if len(data) == 196608 and r[0] == 0 and re.search(r'\d+ blocks copied', r[1]):
            # No error table: d64copy left it out because nothing failed (seen on full
            # reads; single-track reads have so far always included one).
            res["t36_readable"], res["t36_ok"] = True, 17
            b = data[_blk(36, 0) * 256:_blk(37, 0) * 256]
            res["t36_data"] = any(any(b[i:i + 256]) and not (b[i] == 0x4B and not any(
                x != 1 for x in b[i + 1:i + 256])) for i in range(0, len(b), 256))
        elif len(data) == 197376:                    # 40 tracks + error bytes
            codes = [data[196608 + _blk(36, s)] for s in range(17)]
            res["t36_ok"] = sum(1 for c in codes if c == 1)
            res["t36_readable"] = res["t36_ok"] == 17
            res["t36_partial"] = 0 < res["t36_ok"] < 17
            for s in range(17):
                if codes[s] != 1:
                    continue
                b = data[_blk(36, s) * 256:_blk(36, s) * 256 + 256]
                if any(b) and not (b[0] == 0x4B and not any(x != 1 for x in b[1:])):
                    res["t36_data"] = True
    log_event(f"--- disk check result: {res}")
    return res


# Drive code from drive/erase.s (built with cl65 -t none --start-addr 0x500); keep the two in
# step. Run as an execute job ($E0) from buffer 2, it overwrites one whole track with
# sync-free bytes and reports $01 OK or $08 write protected in job slot $02.
ERASE_CODE = bytes.fromhex(
    "ad001c2910d005a9084c69f9a9558d011ca9ff8d031cad0c1c291f09c08d0c1c"
    "a220a000a95550feb88d011cc8d0f7cad0f450feb8ad0c1c09e08d0c1ca9008d"
    "031ca9014c69f9")
_JOB2, _HDR2, _BUF2 = 0x0002, 0x000A, 0x0500     # job slot, header table, buffer #2


def _drive_mem_write(addr, data, tmp, proc_store=None, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        return False      # the cancel sequence owns the bus now
    path = os.path.join(tmp, "upload.bin")
    with open(path, "wb") as f:
        f.write(data)
    r = _run_bounded(["cbmctrl", "upload", DRIVE, hex(addr), path], 10, proc_store)
    return r is not None and r[0] == 0


def _drive_mem_read(addr, count, tmp, proc_store=None, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        return None
    path = os.path.join(tmp, "download.bin")
    if os.path.exists(path):
        os.remove(path)
    r = _run_bounded(["cbmctrl", "download", DRIVE, hex(addr), str(count), path], 10,
                     proc_store)
    if r is None or r[0] != 0 or not os.path.exists(path):
        return None
    data = _read_bytes(path)
    return data if len(data) == count else None


def _drive_job(code, tmp, proc_store=None, cancel_event=None, timeout=20):
    """Put a job in slot 2 and wait for the controller to finish it. Returns the result
    byte ($01 = OK), or None if the drive didn't answer or the job didn't finish."""
    if not _drive_mem_write(_JOB2, bytes([code]), tmp, proc_store, cancel_event):
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        b = _drive_mem_read(_JOB2, 1, tmp, proc_store, cancel_event)
        if b is None:
            return None
        if b[0] < 0x80:
            return b[0]
        if cancel_event is not None and cancel_event.is_set():
            return None
        time.sleep(0.1)
    return None


def recover_head(bump=True):
    """Get the drive out of 74,DRIVE NOT READY caused by where the head is, not by the disk.

    After a reset or disk change the DOS checks the drive by reading one sector header on
    whatever track the head is on (1541 ROM LC63D → LD00E, a seek job). If that track is
    unreadable (erased, unformatted, beyond track 35 on a 35-track disk, or the head lost
    between tracks) it sets its not-ready flag and answers 74 to everything until an `I`
    or a disk change, even once the head is back on a good track (found 2026-11-25 after a
    cancelled erase left the head on a blank track 12; a seek to 18 alone didn't help, `I`
    did). So: bump (head to track 1, position known), seek track 18, `I`. Returns the
    drive status text. Uses job slot 2 only; writes nothing to the disk."""
    with tempfile.TemporaryDirectory() as tmp:
        if bump and _drive_mem_write(_HDR2, bytes([1, 0]), tmp):
            _drive_job(0xC0, tmp)
        if _drive_mem_write(_HDR2, bytes([18, 0]), tmp):
            _drive_job(0xB0, tmp)
    _run_bounded(["cbmctrl", "command", DRIVE, "I"], 10)
    return drive_status()[1]


def erase_tracks(tracks, on_progress=None, proc_store=None, cancel_event=None):
    """Erase the given tracks (1–40) by writing sync-free bytes over each one, so the drive
    reads them as never formatted. Bumps the head first, so the controller knows where it
    is whatever is (or isn't) on the disk. Returns (ok, message).

    A 1571 is put in 1541 mode first (U0>M0, drive-1571-users-guide-1985 p.13): the drive
    code relies on the 1541-mode ROM's job exit at $F969."""
    log_event(f"--- erase tracks {tracks[0]}–{tracks[-1]} on drive {DRIVE}")
    reset_and_settle()
    if cancel_event is not None and cancel_event.is_set():
        return False, "cancelled"
    det = _run_bounded(["cbmctrl", "detect"], 10)
    model = dict(parse_detect(det[1])).get(DRIVE, "") if det and det[0] == 0 else ""
    if not model:
        return False, f"drive {DRIVE} does not answer"
    if not re.match(r'15(41|70|71)', model):
        return False, f"erasing is only implemented for 1541/1570/1571 drives, not {model}"
    if model.startswith(("1570", "1571")):
        _run_bounded(["cbmctrl", "command", DRIVE, "U0>M0"], 10)
        drive_status()
    with tempfile.TemporaryDirectory() as tmp:
        if cancel_event is not None and cancel_event.is_set():
            return False, "cancelled"
        if not _drive_mem_write(_BUF2, ERASE_CODE, tmp, proc_store, cancel_event) or \
                _drive_mem_read(_BUF2, len(ERASE_CODE), tmp, proc_store, cancel_event) != ERASE_CODE:
            return False, "could not load the erase routine into the drive"
        # Bump: head to track 1, a known position (the ROM sets its current track to 1;
        # LF37C in the 1541 ROM). The controller reads the slot's header track before
        # any job, so give it a valid one.
        r = (_drive_job(0xC0, tmp, proc_store, cancel_event)
             if _drive_mem_write(_HDR2, bytes([1, 0]), tmp, proc_store, cancel_event) else None)
        if r != 0x01:
            return False, ("cancelled" if cancel_event is not None and cancel_event.is_set()
                           else f"head bump failed (job result {r})")
        for i, t in enumerate(tracks):
            if cancel_event is not None and cancel_event.is_set():
                return False, "cancelled"
            if on_progress:
                on_progress(t, i / len(tracks))
            if not _drive_mem_write(_HDR2, bytes([t, 0]), tmp, proc_store, cancel_event):
                return False, f"drive stopped answering at track {t}"
            r = _drive_job(0xE0, tmp, proc_store, cancel_event)
            if r == 0x08:
                return False, "the disk is write protected (nothing was erased)" if i == 0 else \
                              f"write protect at track {t}"
            if r != 0x01:
                if cancel_event is not None and cancel_event.is_set():
                    return False, "cancelled"
                return False, f"erasing track {t} failed (job result {r})"
        if on_progress:
            on_progress(tracks[-1], 1.0)
        if cancel_event is not None and cancel_event.is_set():
            return False, "cancelled"
    # Park the head on track 18 and re-read the BAM (the drive's copy is stale). A plain
    # reset would leave the head on the last, now blank, track: 74 until a disk change.
    recover_head(bump=False)
    log_event("--- erase done")
    return True, "done"


# --- Directory editing (job queue, buffer 2) ---

DIR_BACKUP_DIR = os.path.expanduser("~/Library/Application Support/Diskmasher/directory-backups")


def _job_error(code):
    """Controller job result → DOS error text (codes: c64-inside-commodore-dos p.103)."""
    names = {0x02: "20, READ ERROR (header not found)", 0x03: "21, READ ERROR (no sync)",
             0x04: "22, READ ERROR (data block not found)", 0x05: "23, READ ERROR (checksum)",
             0x07: "25, WRITE ERROR (verify)", 0x08: "26, WRITE PROTECT ON",
             0x09: "27, READ ERROR (header checksum)", 0x0B: "29, DISK ID MISMATCH",
             0x0F: "74, DRIVE NOT READY"}
    return names.get(code, "no answer from the drive" if code is None else f"job result ${code:02X}")


def _read_sector(t, s, tmp, proc_store=None, cancel_event=None):
    """(bytes, None) or (None, error text). Job $80 into buffer 2, then download it."""
    if not _drive_mem_write(_HDR2, bytes([t, s]), tmp, proc_store, cancel_event):
        return None, "no answer from the drive"
    r = _drive_job(0x80, tmp, proc_store, cancel_event)
    if r != 0x01:
        return None, _job_error(r)
    data = _drive_mem_read(_BUF2, 256, tmp, proc_store, cancel_event)
    return (data, None) if data else (None, "no answer from the drive")


def _write_sector(t, s, data, tmp):
    """None or error text. Upload to buffer 2, then job $90. Not cancellable on purpose."""
    if not _drive_mem_write(_BUF2, data, tmp) or not _drive_mem_write(_HDR2, bytes([t, s]), tmp):
        return "no answer from the drive"
    r = _drive_job(0x90, tmp)
    return None if r == 0x01 else _job_error(r)


def _petscii_key(raw):
    """A loose comparison key for a file name: letters and digits only, lower case. Raw
    directory bytes ($41–$5A and $C1–$DA are letters) and cbmctrl's listing (which shows
    them lower and upper case) give the same key."""
    out = []
    for b in raw:
        if 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A or 0xC1 <= b <= 0xDA:
            out.append(chr((b & 0x1F) + 0x60))
        elif 0x30 <= b <= 0x39:
            out.append(chr(b))
    return "".join(out)


def move_to_first_slot(index, listing, on_step=None, proc_store=None, cancel_event=None,
                       enter_critical=None):
    """Move the `index`th listed file to the first directory slot, so LOAD"*" loads it
    (after a drive reset or power-on, "*" means the first file in the directory). The
    other files keep their order; file data and the BAM are not touched.

    listing: the right pane's entries [(blocks, name, type)] in directory order; the
    directory read from the disk must match it (else the disk was changed since).
    Steps: bump + seek 18 (the drive learns the disk ID), read the chain from 18/1,
    check, save the original sectors to DIR_BACKUP_DIR, write only the changed sectors
    (enter_critical() is called first and turns Cancel off), read them back, `I`.
    Returns (ok, message, backup)."""
    def step(text):
        if on_step:
            on_step(text)

    def cancelled():
        return cancel_event is not None and cancel_event.is_set()

    log_event(f"--- move listed file {index} to the first directory slot on drive {DRIVE}")
    step("Preparing the drive…")
    reset_and_settle()
    if cancelled():
        return False, "cancelled", None
    det = _run_bounded(["cbmctrl", "detect"], 10)
    model = dict(parse_detect(det[1])).get(DRIVE, "") if det and det[0] == 0 else ""
    if not re.match(r'15(41|70|71)', model):
        return False, (f"drive {DRIVE} does not answer" if not model else
                       f"only implemented for 1541/1570/1571 drives, not {model}"), None
    if model.startswith(("1570", "1571")):
        _run_bounded(["cbmctrl", "command", DRIVE, "U0>M0"], 10)
        drive_status()

    with tempfile.TemporaryDirectory() as tmp:
        step("Reading the directory…")
        ok = (_drive_mem_write(_HDR2, bytes([1, 0]), tmp, proc_store, cancel_event)
              and _drive_job(0xC0, tmp, proc_store, cancel_event) == 0x01
              and _drive_mem_write(_HDR2, bytes([18, 0]), tmp, proc_store, cancel_event))
        r = _drive_job(0xB0, tmp, proc_store, cancel_event) if ok else None
        if cancelled():
            return False, "cancelled", None
        if r != 0x01:
            return False, f"can't find track 18: {_job_error(r)}", None
        bam, err = _read_sector(18, 0, tmp, proc_store, cancel_event)
        if err:
            return False, ("cancelled" if cancelled() else f"reading 18/0: {err}"), None

        sectors, t, s = [], 18, 1
        while t:
            if t != 18 or s >= 19 or any(x[1] == s for x in sectors) or len(sectors) >= 18:
                return False, "the directory chain is broken; nothing was changed", None
            data, err = _read_sector(t, s, tmp, proc_store, cancel_event)
            if err:
                return False, ("cancelled" if cancelled() else f"reading 18/{s}: {err}"), None
            sectors.append((t, s, data))
            t, s = data[0], data[1]

        slots = [(i, k) for i in range(len(sectors)) for k in range(8)]
        def body(i, k):
            return sectors[i][2][32 * k + 2:32 * k + 32]
        listed = [(i, k) for i, k in slots if body(i, k)[0] != 0]

        same = len(listed) == len(listing) and all(
            (b[28] | (b[29] << 8)) == blocks and
            _petscii_key(b[3:19].rstrip(b"\xa0")) == _petscii_key(name.encode("latin-1", "replace"))
            for (i, k), (blocks, name, _) in zip(listed, listing) for b in [body(i, k)])
        if not same:
            return False, ("the directory on the disk doesn't match the listing (was the disk "
                           "changed?); refresh and try again"), None
        if not 0 <= index < len(listed):
            return False, "no such file in the listing", None
        ci, ck = listed[index]
        chosen = body(ci, ck)
        name = bytes(chosen[3:19]).rstrip(b"\xa0").decode("latin-1")
        if (ci, ck) == slots[0]:
            return True, f'"{name}" is already the first file', None
        if chosen[0] & 0x87 != 0x82:
            return False, (f'"{name}" is not a properly closed PRG file; LOAD"*" loads '
                           "programs"), None

        order = [chosen] + [body(i, k) for i, k in slots if (i, k) != (ci, ck)]
        new = [bytearray(data) for _, _, data in sectors]
        for (i, k), b in zip(slots, order):
            new[i][32 * k + 2:32 * k + 32] = b
        changed = [i for i in range(len(sectors)) if bytes(new[i]) != sectors[i][2]]

        # From here on Cancel is off (enter_critical, from the task dialog): a directory
        # must never be left half rewritten.
        if cancelled() or (enter_critical is not None and not enter_critical()):
            return False, "cancelled", None
        os.makedirs(DIR_BACKUP_DIR, exist_ok=True)
        disk = re.sub(r'[^a-z0-9_-]+', "_", bam[0x90:0xA0].rstrip(b"\xa0")
                      .decode("latin-1").lower()).strip("_") or "disk"
        backup = os.path.join(DIR_BACKUP_DIR, f"{disk}-{time.strftime('%Y%m%d-%H%M%S')}.json")
        with open(backup, "w") as f:
            json.dump({"drive": DRIVE, "disk_name": bam[0x90:0xA0].hex(),
                       "disk_id": bam[0xA2:0xA4].hex(), "made": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "note": "original directory sectors before moving a file to the first "
                               "slot; restore with restore_directory_backup()",
                       "sectors": {f"{t}/{s}": data.hex() for t, s, data in sectors}}, f, indent=1)
        log_event(f"--- directory backup: {backup}")

        step("Writing the directory…")
        for i in changed:                     # not cancellable: never leave half a directory
            err = _write_sector(sectors[i][0], sectors[i][1], bytes(new[i]), tmp)
            if err:
                return False, (f"writing 18/{sectors[i][1]} failed: {err}. The original "
                               f"directory is saved in {backup}"), backup
        step("Checking what was written…")
        for i in changed:
            data, err = _read_sector(sectors[i][0], sectors[i][1], tmp)
            if err or data != bytes(new[i]):
                return False, (f"18/{sectors[i][1]} doesn't read back as written "
                               f"({err or 'different bytes'}). The original directory is "
                               f"saved in {backup}"), backup
    _run_bounded(["cbmctrl", "command", DRIVE, "I"], 10)
    drive_status()
    log_event(f"--- {name} is now the first file")
    return True, f'"{name}" is now the first file: LOAD"*" loads it', backup


def restore_directory_backup(path):
    """Write a directory backup from move_to_first_slot back to the disk in the drive
    (DRIVE). Checks the disk name and ID first. Returns None or error text."""
    with open(path) as f:
        b = json.load(f)
    reset_and_settle()
    with tempfile.TemporaryDirectory() as tmp:
        if not (_drive_mem_write(_HDR2, bytes([1, 0]), tmp) and _drive_job(0xC0, tmp) == 0x01
                and _drive_mem_write(_HDR2, bytes([18, 0]), tmp) and _drive_job(0xB0, tmp) == 0x01):
            return "can't find track 18"
        bam, err = _read_sector(18, 0, tmp)
        if err:
            return f"reading 18/0: {err}"
        if bam[0x90:0xA0].hex() != b["disk_name"] or bam[0xA2:0xA4].hex() != b["disk_id"]:
            return "this backup is from a different disk (name or ID differs)"
        for ts, hexdata in b["sectors"].items():
            t, s = map(int, ts.split("/"))
            err = _write_sector(t, s, bytes.fromhex(hexdata), tmp)
            if err:
                return f"writing {ts}: {err}"
    _run_bounded(["cbmctrl", "command", DRIVE, "I"], 10)
    drive_status()
    return None


def _bind_mac_multiselect(lb):
    """Make Command-click toggle items in extended-select Listbox (macOS convention)."""
    def _cmd_click(event):
        idx = lb.nearest(event.y)
        if idx in lb.curselection():
            lb.selection_clear(idx)
        else:
            lb.selection_set(idx)
        lb.event_generate("<<ListboxSelect>>")
        return "break"
    lb.bind("<Command-Button-1>", _cmd_click)


# --- CBM helpers ---

def _stop_proc(proc, grace=3.0):
    """Stop an opencbm process: SIGTERM, wait up to `grace` s, SIGKILL only if it is still alive.
    A hard kill mid-IEC-transaction is what has wedged the xum1541 before, so the
    gentle signal always gets its chance first."""
    if proc is None or proc.poll() is not None:
        return
    log_event(f"stopping pid {proc.pid} ({' '.join(map(str, proc.args)) if isinstance(proc.args, list) else proc.args}) with SIGTERM")
    try:
        proc.terminate()
    except Exception:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    log_event(f"pid {proc.pid} ignored SIGTERM for {grace:.0f}s: SIGKILL")
    try:
        proc.kill()
    except Exception:
        pass


def _run_bounded(cmd, timeout, proc_store=None):
    """Run a short command. Returns (returncode, output), or None if it hung past `timeout`.
    Unlike subprocess.run(timeout=…), a hung process is stopped with _stop_proc, not SIGKILL.
    With proc_store, the process sits in proc_store[0] while it runs, so Cancel can stop it."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    except OSError as e:
        return -1, str(e)
    if proc_store is not None:
        proc_store[0] = proc
    try:
        out, _ = proc.communicate(timeout=timeout)
        log_cmd(cmd, started, f"exit {proc.returncode}", out)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        _stop_proc(proc)
        out = ""
        try:
            out = proc.communicate(timeout=2)[0] or ""
        except Exception:
            pass
        log_cmd(cmd, started, f"HUNG past {timeout}s, stopped", out)
        return None
    finally:
        if proc_store is not None:
            proc_store[0] = None


def parse_detect(text):
    """[(unit, model)] from `cbmctrl detect` output, e.g. ' 8: 1571' → [("8", "1571")]."""
    return re.findall(r'^\s*(\d+):\s*(\S+)', text or "", re.M)


def cbm_detect():
    """`cbmctrl detect` output; "" when no drive answers (exit 1, no output), "Error: …"
    when it hangs or reports an error (e.g. no xum1541 on USB)."""
    r = _run_bounded(["cbmctrl", "detect"], 5)
    if r is None:
        return "Error: cbmctrl detect didn't answer in 5 s (replug the xum1541's USB cable)"
    rc, out = r[0], (r[1] or "").strip()
    if rc != 0 and out:
        return f"Error: {out.splitlines()[-1]}"
    return out


def drive_status(drive=None, timeout=120):
    """Read the drive's error channel; returns (code, text), code None if unreadable.
    `cbmctrl command` returns as soon as the command is sent and the drive works on
    afterwards (V0 returned in 0.1 s, the status read then waited 2.7 s), so reading the
    status is how to wait until a command has finished, before the next bus request."""
    r = _run_bounded(["cbmctrl", "status", drive or DRIVE], timeout)
    if r is None or r[0] != 0:
        return None, (r[1].strip() if r else "status read timed out")
    text = r[1].strip()
    m = re.match(r'\s*(\d+)\s*,', text)
    return (int(m.group(1)) if m else None), text


def _cbm_dir_once(drive):
    """`cbmctrl dir` output (a drive error like 74 comes back as text, with exit 0), or
    "Error: …" when it hangs or fails."""
    r = _run_bounded(["cbmctrl", "dir", drive], 30)
    if r is None:
        return "Error: cbmctrl dir didn't answer in 30 s"
    rc, out = r[0], (r[1] or "").strip()
    if rc != 0:
        return f"Error: {out.splitlines()[-1] if out else f'cbmctrl dir exited {rc}'}"
    return out


def cbm_dir(drive=None):
    """Directory listing. On 74,DRIVE NOT READY, tries recover_head() once and reads again:
    the DOS says 74 when the head sits on an unreadable track (see recover_head)."""
    drive = drive or DRIVE
    out = _cbm_dir_once(drive)
    if out.startswith("74,") and drive == DRIVE:
        log_event(f"--- 74 on drive {drive}: bump, seek track 18, initialise, read again")
        recover_head()
        out = _cbm_dir_once(drive)
    return out


def cbm_display(text):
    """A name, ID or type from `cbmctrl dir` as a Commodore shows it at power-up.

    cbmctrl converts PETSCII to ASCII as the lowercase character set shows it: unshifted
    letters ($41–$5A, what you type) come out lowercase and shifted ones ($C1–$DA)
    uppercase. On the C64's default (uppercase/graphics) set the unshifted letters are
    capitals, so swapping the case gives the familiar listing ("MATRIX"  PRG). Shifted
    letters, graphics characters there, show as lowercase for now. Display only: the
    names passed to cbmread/cbmwrite stay as cbmctrl gave them, since those tools
    convert again."""
    return text.swapcase()


def parse_cbm_dir(raw):
    lines = raw.splitlines()
    entries, disk_name, disk_id, blocks_free = [], "", "", None
    for line in lines:
        # Header: name, then ID and DOS type; some disks show a single field there
        # (the Lykia disk: `0 ."- lykia - c64   " 2022`), so the second one is optional.
        m = re.match(r'\s*\d+\s+\."(.+?)"\s+(\S+)(?:\s+(\S+))?', line)
        if m:
            disk_name = m.group(1).strip()
            disk_id   = " ".join(g for g in m.group(2, 3) if g)
            continue
        m = re.match(r'\s*(\d+)\s+"(.+?)"\s+(\S+)', line)
        if m:
            entries.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
            continue
        m = re.match(r'\s*(\d+)\s+blocks free', line)
        if m:
            blocks_free = int(m.group(1))
    return disk_name, disk_id, entries, blocks_free


_OPENCBM_FAILURE = re.compile(
    r'\[Fatal\]|error status|error (reading|writing)|FILE NOT FOUND', re.I)

def _opencbm_error(output):
    """The failure lines in cbmread/cbmwrite output, or None. Both tools can exit 0 on a
    failed transfer and only print a [Warning] (tested 2026-11-25: cbmread on a file whose
    chain reaches track 36 gave '67,ILLEGAL TRACK OR SECTOR,36,00', exit 0, no file).
    Not every [Warning] is a failure: 'could not identify drive, using no turbo' (seen
    right after a drive power cycle) still transfers the file."""
    bad = [l.strip() for l in (output or "").splitlines() if _OPENCBM_FAILURE.search(l)]
    return " / ".join(bad) or None


def type_char(ftype):
    """The DOS type letter for a listing type ("prg", "SEQ", "*seq", "usr<"), or None."""
    return {"PRG": "p", "SEQ": "s", "USR": "u", "REL": "r"}.get(ftype.strip("*<").upper())


def cbm_read_file(drive, cbm_name, dest_dir, proc_store=None, ftype=None):
    """Read a single file from CBM drive into dest_dir. Returns error string or None.
    Success means: no failure line in cbmread's output, and the file it names exists.
    Pass the listing's ftype: cbmread opens a plain name as PRG, so a SEQ or USR file
    fails with 64 FILE TYPE MISMATCH unless the name carries its type ("name,s"; tested
    2026-11-25, drive 9). The local file then gets .seq/.usr, which cbmwrite reads back
    as the type."""
    started = time.monotonic()
    t = type_char(ftype) if ftype else None
    arg = f"{cbm_name},{t}" if t in ("p", "s", "u") else cbm_name
    try:
        proc = subprocess.Popen(
            ["cbmread", drive, arg],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", cwd=dest_dir)
        if proc_store is not None:
            proc_store[0] = proc
        try:
            out, _ = proc.communicate(timeout=300)
        finally:
            if proc_store is not None:
                proc_store[0] = None
        log_cmd(["cbmread", drive, arg], started, f"exit {proc.returncode}", out)
        err = _opencbm_error(out)
        if err:
            return err
        if proc.returncode != 0:
            return out.strip() or f"cbmread returned {proc.returncode}"
        m = re.search(r'-> (.+?)\s*$', out, re.M)
        if not m or not os.path.isfile(os.path.join(dest_dir, m.group(1))):
            return "cbmread reported success but wrote no file"
        return None
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Timeout"
    except Exception as e:
        return str(e)


def cbm_write_file(drive, local_path, cbm_name=None, proc_store=None):
    """Write a local file to CBM drive, optionally with a specific CBM filename."""
    cmd = ["cbmwrite", "-o", cbm_name, drive, local_path] if cbm_name else ["cbmwrite", drive, local_path]
    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        if proc_store is not None:
            proc_store[0] = proc
        try:
            out, _ = proc.communicate(timeout=300)
        finally:
            if proc_store is not None:
                proc_store[0] = None
        log_cmd(cmd, started, f"exit {proc.returncode}", out)
        err = _opencbm_error(out)
        if err:
            return err
        if proc.returncode != 0:
            return out.strip() or f"cbmwrite returned {proc.returncode}"
        return None
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Timeout"
    except Exception as e:
        return str(e)


def _fmt_size(n):
    if n < 1024:    return f"{n} B"
    if n < 1024**2: return f"{n//1024} KB"
    return f"{n/1024**2:.1f} MB"

def _deselect_if_empty(lb, click_y, update_fn):
    idx = lb.nearest(click_y)
    if idx < 0:
        lb.selection_clear(0, tk.END)
        update_fn()
        return
    bbox = lb.bbox(idx)
    if bbox and click_y > bbox[1] + bbox[3]:
        lb.selection_clear(0, tk.END)
        update_fn()


def _safe_filename(name):
    """Strip characters unsafe for local filenames."""
    return re.sub(r'[/\\:*?"<>|]', '_', name)


# --- Left pane: filesystem navigator ---

class FilesystemPane(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._path = START_DIR
        self._entries = []
        self._build()
        self._load()

    def _build(self):
        self._hdr = tk.StringVar()
        ttk.Label(self, textvariable=self._hdr, anchor="w",
                  padding=(6, 3)).pack(fill="x")

        nav = ttk.Frame(self)
        nav.pack(fill="x")
        file_btn  = ttk.Menubutton(nav, text="File")
        file_menu = tk.Menu(file_btn, tearoff=False)
        file_menu.add_command(label="New directory…", command=self._mkdir)
        file_menu.add_command(label="New image…",     command=self._new_image,
                              state="normal" if _C1541 else "disabled")
        file_menu.add_separator()
        file_menu.add_command(label="Move to Trash…", command=self._trash_selected)
        file_btn["menu"] = file_menu
        file_btn.pack(side="left", padx=4, pady=2)
        ttk.Button(nav, text="Refresh", command=self._load).pack(side="left", padx=2, pady=2)
        self._write_btn = ttk.Button(nav, text="Write →", command=lambda: self.event_generate("<<WriteRequest>>"))
        self._write_btn.pack(side="right", padx=4, pady=2)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.lb = tk.Listbox(frame, font=("Menlo", 11),
                             selectmode="extended", exportselection=False,
                             activestyle="none", relief="flat", bd=0,
                             highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=vsb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.lb.bind("<Double-1>",        self._on_double)
        self.lb.bind("<Return>",          self._on_double)
        self.lb.bind("<Command-Up>",      lambda e: self._go_up())
        self.lb.bind("<Command-BackSpace>", lambda e: self._trash_selected())
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_sel())
        self.lb.bind("<Button-1>",        lambda e: self.after(0, lambda: _deselect_if_empty(self.lb, e.y, self._update_sel)))
        _bind_mac_multiselect(self.lb)

        self._stats_var = tk.StringVar()
        self._sel_var   = tk.StringVar()
        ttk.Label(self, textvariable=self._stats_var, anchor="w", padding=(6, 2)).pack(fill="x")
        ttk.Label(self, textvariable=self._sel_var,   anchor="w", padding=(6, 2)).pack(fill="x")

    def _update_sel(self):
        files = self.selected_files()
        if not files:
            self._sel_var.set("")
            return
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() in image_exts]
        prgs   = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() not in image_exts]
        if images and prgs:
            self._sel_var.set("Mixed selection — images and files can't be written together.")
        elif len(images) > 1:
            self._sel_var.set(f"{len(images)} images selected — write supports one at a time.")
        elif len(images) == 1:
            name, path = images[0]
            size = os.path.getsize(path)
            fmt  = IMAGE_FORMATS.get(size)
            if fmt:
                label, _, _, _ = fmt
                kb = size // 1024
                base_msg = f"1 image: {label}, {kb} kB"
                self._sel_var.set(base_msg)
                if _C1541:
                    self._query_image_blocks_free(path, base_msg)
            else:
                self._sel_var.set(f"1 image: unknown format ({size:,} bytes)")
        else:
            total_bytes  = sum(os.path.getsize(p) for _, p in prgs)
            total_blocks = math.ceil(total_bytes / 254)
            kb = total_bytes // 1024
            n  = len(prgs)
            self._sel_var.set(f"{n} file{'s' if n > 1 else ''} selected,  {total_blocks} blocks ({kb} kB)")
        self.event_generate("<<SelectionChanged>>")

    def _query_image_blocks_free(self, path, base_msg):
        self._img_query_path = path
        def _run():
            try:
                r = subprocess.run([_C1541, "-attach", path, "-dir"],
                                   capture_output=True, text=True, timeout=5)
                m = re.search(r'(\d+)\s+blocks?\s+free', r.stdout, re.IGNORECASE)
                if m:
                    blocks = int(m.group(1))
                    def _update():
                        if getattr(self, '_img_query_path', None) == path:
                            self._sel_var.set(f"{base_msg}  ·  {blocks} blocks free")
                    self.after(0, _update)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _load(self, select_name=None):
        """List the current folder. A `..` entry comes first (except at /); select_name,
        if given, is selected and scrolled into view (the folder we just came up from)."""
        self._hdr.set(self._path)
        self.lb.delete(0, tk.END)
        self._entries = []
        parent = os.path.dirname(self._path)
        if parent != self._path:
            self._entries.append(("..", True, parent))
            self.lb.insert(tk.END, "  ../")
        try:
            raw = sorted(os.scandir(self._path),
                         key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        visible = [(e, e.is_dir()) for e in raw
                   if not e.name.startswith(".")
                   and (e.is_dir() or os.path.splitext(e.name)[1].lower() in RELEVANT_EXTENSIONS)]
        col_w = max((len(e.name) + (1 if d else 0) for e, d in visible), default=20) + 2
        total_kb = 0
        for e, is_dir in visible:
            if is_dir:
                self._entries.append((e.name, True, e.path))
                self.lb.insert(tk.END, f"  {e.name + '/':<{col_w}}")
            else:
                sz_bytes = e.stat().st_size
                total_kb += sz_bytes // 1024
                sz = _fmt_size(sz_bytes)
                self._entries.append((e.name, False, e.path))
                self.lb.insert(tk.END, f"  {e.name:<{col_w}} {sz:>8}")
        files_only = [e for e, d in visible if not d]
        dirs_only  = [e for e, d in visible if d]
        parts = []
        if dirs_only:  parts.append(f"{len(dirs_only)} folder{'s' if len(dirs_only) > 1 else ''}")
        if files_only: parts.append(f"{len(files_only)} file{'s' if len(files_only) > 1 else ''}  ·  {total_kb} kB")
        self._stats_var.set("  ·  ".join(parts) if parts else "")
        self._sel_var.set("")
        if select_name is not None:
            for i, (n, _, _) in enumerate(self._entries):
                if n == select_name and n != "..":
                    self.lb.selection_set(i)
                    self.lb.see(i)
                    break

    def _on_double(self, _event):
        sel = self.lb.curselection()
        if not sel:
            return
        name, is_dir, path = self._entries[sel[0]]
        if name == "..":
            self._go_up()
        elif is_dir:
            self._path = path
            self._load()
        elif _C1541 and os.path.splitext(name)[1].lower() in {".d64", ".d71", ".d81"}:
            self._open_image_view(path, name)

    def _open_image_view(self, path, name):
        dlg = tk.Toplevel(self)
        dlg.title(name)
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        x = self.winfo_rootx() + 40
        y = self.winfo_rooty() + 40
        dlg.geometry(f"480x340+{x}+{y}")

        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(side="bottom", pady=6)

        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        txt = tk.Text(frame, font=("Menlo", 11), state="disabled",
                      relief="flat", bd=0, highlightthickness=0, wrap="none")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        _noise = re.compile(
            r'^(OPENCBM:|D\d{2} disk image |Unit \d+ drive \d+:|Loading directory image|/\S+$)',
            re.IGNORECASE)

        def _run():
            try:
                r = subprocess.run([_C1541, "-attach", path, "-dir"],
                                   capture_output=True, text=True, timeout=10)
                lines = [l for l in r.stdout.splitlines() if not _noise.match(l)]
                output = "\n".join(lines).strip() or r.stderr.strip() or "(no output)"
            except Exception as e:
                output = str(e)
            def _fill():
                txt.configure(state="normal")
                txt.insert("1.0", output)
                txt.configure(state="disabled")
            try:
                dlg.after(0, _fill)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _go_up(self):
        parent = os.path.dirname(self._path)
        if parent != self._path:
            came_from = os.path.basename(self._path)
            self._path = parent
            self._load(select_name=came_from)

    def _trash_selected(self):
        """Move the selected files and folders to the macOS Trash (Finder's Put Back works).
        For clearing out test and intermediate files without leaving the app; copying,
        moving and renaming stay with the Finder (README, "Local pane")."""
        items = [(n, d, p) for i in self.lb.curselection()
                 for n, d, p in [self._entries[i]] if n != ".."]
        if not items:
            messagebox.showinfo("Move to Trash", "Select the files or folders to move first.")
            return
        lines = []
        for n, d, p in items[:12]:
            if d:
                try:
                    k = sum(len(fs) + len(ds) for _, ds, fs in os.walk(p))
                except OSError:
                    k = 0
                lines.append(f"  {n}/" + (f"  ({k} item{'s' if k != 1 else ''} inside)" if k else "  (empty)"))
            else:
                lines.append(f"  {n}")
        if len(items) > 12:
            lines.append(f"  … and {len(items) - 12} more")
        what = (f'"{items[0][0]}"' if len(items) == 1 else f"these {len(items)} items")
        # OK is the default (Antti, 2026-11-25): the Trash is recoverable (Finder's Put Back).
        if not messagebox.askokcancel("Move to Trash",
                f"Move {what} to the Trash?\n\n" + "\n".join(lines),
                icon="warning", default="ok"):
            return
        paths = [p for _, _, p in items]
        try:
            if os.path.exists("/usr/bin/trash"):
                r = subprocess.run(["/usr/bin/trash", *paths],
                                   capture_output=True, text=True, timeout=60)
            else:   # before macOS 15: ask the Finder
                script = ("on run argv\n tell application \"Finder\"\n"
                          "  repeat with p in argv\n delete (POSIX file p as alias)\n end repeat\n"
                          " end tell\nend run")
                r = subprocess.run(["osascript", "-e", script, *paths],
                                   capture_output=True, text=True, timeout=60)
            failed = [p for p in paths if os.path.lexists(p)]
        except Exception as e:
            r, failed = None, paths
            err = str(e)
        else:
            err = (r.stderr or r.stdout or "").strip()
        self._load()
        if failed:
            messagebox.showerror("Move to Trash",
                "Couldn't move to the Trash:\n" +
                "\n".join("  " + os.path.basename(p) for p in failed[:12]) +
                (f"\n\n{err}" if err else "") +
                "\n\nNothing was deleted for good.")

    def _mkdir(self):
        dlg = tk.Toplevel(self)
        dlg.title("New folder")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width()  // 2 - 160
        y = self.winfo_rooty() + self.winfo_height() // 2 - 40
        dlg.geometry(f"320x80+{x}+{y}")
        ttk.Label(dlg, text="Folder name:", padding=(12, 10)).pack(side="left")
        name_var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=name_var, width=20)
        entry.pack(side="left", padx=(0, 8))
        entry.focus_set()
        def _do():
            name = name_var.get().strip()
            if not name:
                dlg.destroy()
                return
            path = os.path.join(self._path, name)
            try:
                os.mkdir(path)
                self._load()
            except OSError as e:
                messagebox.showerror("Mkdir failed", str(e))
            dlg.destroy()
        entry.bind("<Return>", lambda e: _do())
        ttk.Button(dlg, text="OK", command=_do).pack(side="left")

    def _new_image(self):
        dlg = tk.Toplevel(self)
        dlg.title("New disk image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # --- image type ---
        type_frame = ttk.LabelFrame(dlg, text="Image type", padding=(8, 4))
        type_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        type_var = tk.StringVar(value="d64")
        for fmt in ("d64", "d71", "d81"):
            rb = ttk.Radiobutton(type_frame, text=fmt, variable=type_var, value=fmt,
                                 command=lambda: _on_type())
            if fmt != "d64":
                rb.configure(state="disabled")
            rb.pack(side="left", padx=8)

        # --- disk identity ---
        id_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 4))
        id_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        ttk.Label(id_frame, text="Disk name:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        diskname_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=diskname_var, width=18).grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(id_frame, text="Disk ID:").grid(row=1, column=0, sticky="e", padx=4, pady=3)
        diskid_var = tk.StringVar(value="00")
        ttk.Entry(id_frame, textvariable=diskid_var, width=4).grid(row=1, column=1, sticky="w", padx=4)


        # --- filename ---
        out_frame = ttk.LabelFrame(dlg, text="Output", padding=(8, 4))
        out_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(out_frame, text="Filename:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        fname_var = tk.StringVar()
        fname_entry = ttk.Entry(out_frame, textvariable=fname_var, width=24)
        fname_entry.grid(row=0, column=1, sticky="w", padx=4)

        # bidirectional sync: diskname ↔ filename
        _syncing = [False]
        def _sync_name_to_file(*_):
            if _syncing[0]: return
            _syncing[0] = True
            raw = diskname_var.get().strip()
            if raw:
                safe = re.sub(r'[^a-zA-Z0-9_-]', '_', raw).lower()
                fname_var.set(f"{safe}.{type_var.get()}")
            _syncing[0] = False
        def _sync_file_to_name(*_):
            if _syncing[0]: return
            _syncing[0] = True
            raw = fname_var.get().strip()
            base = re.sub(r'\.[^.]+$', '', raw)
            if base and not diskname_var.get().strip():
                diskname_var.set(base[:16])
            _syncing[0] = False
        diskname_var.trace_add("write", _sync_name_to_file)
        fname_var.trace_add("write",    _sync_file_to_name)

        def _on_type():
            fmt = type_var.get()
            state = "normal" if fmt == "d64" else "disabled"
            track_35.configure(state=state)
            track_40.configure(state=state)
            # update filename extension
            cur = fname_var.get().strip()
            if cur:
                base = re.sub(r'\.[^.]+$', '', cur)
                _syncing[0] = True
                fname_var.set(f"{base}.{fmt}")
                _syncing[0] = False

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(4, 10))

        def _do():
            if not _C1541:
                messagebox.showerror("c1541 not found",
                    "VICE is not installed. Install VICE to create disk images.")
                return
            diskname = diskname_var.get().strip()[:16] or "unnamed"
            disk_id  = (diskid_var.get().strip()[:2] or "00").lower()
            fmt      = type_var.get()
            fname    = fname_var.get().strip()
            if not fname:
                fname = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', diskname).lower()}.{fmt}"
            if not fname.lower().endswith(f".{fmt}"):
                fname += f".{fmt}"
            out_path = os.path.join(self._path, fname)
            dlg.destroy()

            r = subprocess.run(
                [_C1541, "-format", f"{diskname.lower()},{disk_id}", fmt, out_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                messagebox.showerror("Create failed", r.stderr.strip() or r.stdout.strip())
                return
            self._load()

        ttk.Button(btn_frame, text="Create", command=_do).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

    def current_dir(self):
        return self._path

    def selected_files(self):
        """Return list of (name, path) for selected non-directory entries."""
        result = []
        for i in self.lb.curselection():
            name, is_dir, path = self._entries[i]
            if not is_dir:
                result.append((name, path))
        return result


# --- Right pane: CBM directory ---

class CbmDirPane(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._entries = []
        self._build()

    def _build(self):
        self._hdr = tk.StringVar(value=f"{DRIVE}:  (not refreshed)")
        self.disk_name = ""
        ttk.Label(self, textvariable=self._hdr, anchor="w",
                  padding=(6, 3)).pack(fill="x")

        nav = ttk.Frame(self)
        nav.pack(fill="x")

        read_btn  = ttk.Menubutton(nav, text="← Read")
        read_menu = tk.Menu(read_btn, tearoff=False)
        read_menu.add_command(label="Read file(s)", command=lambda: self.event_generate("<<ReadRequest>>"))
        read_menu.add_command(label="Read image",   command=lambda: self.event_generate("<<ReadImageRequest>>"))
        read_menu.add_command(label="Read to image…",
                              command=lambda: self.event_generate("<<ReadToImageRequest>>"),
                              state="disabled")
        read_btn["menu"] = read_menu
        self._read_menu = read_menu
        read_btn.pack(side="left", padx=4, pady=2)

        drive_btn  = ttk.Menubutton(nav, text="Drive")
        drive_menu = tk.Menu(drive_btn, tearoff=False)
        drive_menu.add_command(label="Detect",  command=lambda: self.event_generate("<<DetectRequest>>"))
        drive_menu.add_command(label="Refresh", command=lambda: self.event_generate("<<RefreshRequest>>"))
        drive_menu.add_command(label="Reset",   command=lambda: self.event_generate("<<ResetRequest>>"))
        drive_btn["menu"] = drive_menu
        self._drive_menu = drive_menu
        self.drive_var   = tk.StringVar(value=DRIVE)
        self._drive_btn  = drive_btn
        self.set_drives([])
        drive_btn.pack(side="right", padx=4, pady=2)

        disk_btn  = ttk.Menubutton(nav, text="Disk")
        disk_menu = tk.Menu(disk_btn, tearoff=False)
        disk_menu.add_command(label="Format…",  command=lambda: self.event_generate("<<FormatRequest>>"))
        disk_menu.add_command(label="FB-ify…",  command=lambda: self.event_generate("<<FBifyRequest>>"))
        disk_menu.add_command(label="Sort…",    command=lambda: self.event_generate("<<SortRequest>>"))
        disk_menu.add_separator()
        disk_menu.add_command(label="Erase…",   command=lambda: self.event_generate("<<EraseRequest>>"))
        disk_btn["menu"] = disk_menu
        disk_btn.pack(side="right", padx=2, pady=2)

        file_btn  = ttk.Menubutton(nav, text="File")
        file_menu = tk.Menu(file_btn, tearoff=False)
        file_menu.add_command(label="Scratch…", command=lambda: self.event_generate("<<ScratchRequest>>"))
        file_menu.add_command(label="Make first (LOAD\"*\")…",
                              command=lambda: self.event_generate("<<MakeFirstRequest>>"))
        file_btn["menu"] = file_menu
        file_btn.pack(side="right", padx=2, pady=2)
        self._menubuttons = [read_btn, drive_btn, disk_btn, file_btn]

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.lb = tk.Listbox(frame, font=("Menlo", 11),
                             selectmode="extended", exportselection=False,
                             activestyle="none", relief="flat", bd=0,
                             highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=vsb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_sel_status())
        self.lb.bind("<Button-1>",        lambda e: self.after(0, lambda: _deselect_if_empty(self.lb, e.y, self._update_sel_status)))
        _bind_mac_multiselect(self.lb)

        self._status     = tk.StringVar(value="")
        self._sel_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._status,    anchor="w", padding=(6, 2)).pack(fill="x")
        ttk.Label(self, textvariable=self._sel_status, anchor="w", padding=(6, 2)).pack(fill="x")

    def set_drives(self, drives):
        """Rebuild the drive choices under Detect/Refresh from [(unit, model)]."""
        m = self._drive_menu
        if m.index("end") is not None and m.index("end") > 2:
            m.delete(3, "end")
        m.add_separator()
        if not drives:
            m.add_command(label="(no drives detected)", state="disabled")
        for unit, model in drives:
            m.add_radiobutton(label=f"Use drive {unit}: {model}", value=unit,
                              variable=self.drive_var,
                              command=lambda: self.event_generate("<<DriveSelected>>"))
        self.drive_var.set(DRIVE)
        self._drive_btn.configure(text=f"Drive {DRIVE}")

    def _apply_dir(self, raw):
        """Apply a raw cbm_dir result to the UI. Must be called on the main thread."""
        if raw.startswith("Error"):
            self._hdr.set(f"{DRIVE}:  error")
            self._status.set(raw)
            self._sel_status.set("")
            return
        disk_name, disk_id, entries, blocks_free = parse_cbm_dir(raw)
        self.disk_name = disk_name     # last disk name read; the header text can say "refreshing…"
        self._hdr.set(f'{DRIVE}:  "{cbm_display(disk_name)}"  {cbm_display(disk_id)}')
        if blocks_free is not None:
            kb_free = blocks_free * 254 // 1024
            self._status.set(f"{len(entries)} files  ·  {blocks_free} blocks ({kb_free} kB) free")
        else:
            self._status.set("")
        self._entries = entries
        self.lb.delete(0, tk.END)
        for blk, name, ftype in entries:
            quoted = f'"{cbm_display(name)}"'
            self.lb.insert(tk.END, f"  {blk:>4}  {quoted:<18}  {cbm_display(ftype)}")
        self._sel_status.set("")

    def refresh(self):
        app = self.winfo_toplevel()
        if app._ops:                    # the bus is busy: refresh when it's free
            app._refresh_pending = True
            return
        self._hdr.set(f"{DRIVE}:  refreshing…")
        self.lb.delete(0, tk.END)
        self._status.set("")
        self._entries = []

        def _run():
            raw = cbm_dir(DRIVE)
            self.after(0, lambda: self._apply_dir(raw))

        app._start_op("Refreshing", _run)

    def _update_sel_status(self):
        sel = self.lb.curselection()
        if not sel:
            self._sel_status.set("")
            return
        entries = [self._entries[i] for i in sel]
        total_blocks = sum(blk for blk, _, _ in entries)
        kb = total_blocks * 254 // 1024
        n = len(entries)
        self._sel_status.set(f"{n} file{'s' if n > 1 else ''} selected,  {total_blocks} blocks ({kb} kB)")

    def selected_entries(self):
        return [self._entries[i] for i in self.lb.curselection()]

    def blocks_free(self):
        m = re.search(r'·\s*(\d+)\s*blocks', self._status.get())
        return int(m.group(1)) if m else None


# --- Main window ---

class DiskMasher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diskmasher")
        # One drive operation at a time: _start_op counts running operations; while any
        # runs, the drive menus and Write are disabled and a refresh waits for the end.
        self._ops = 0
        self._op_label = ""
        self._have_drives = False       # set by detect and Cancel's bus check
        self._refresh_pending = False
        self.geometry("1100x660+120+80")
        self.minsize(750, 420)
        self._build_toolbar()
        self._build_panes()
        self._build_statusbar()
        if _C1541 is None:
            def _on_detect_done():
                self.cbm_pane.refresh()
                self._set_status("VICE/c1541 not found — disk image features unavailable.")
            self._detect_drives(on_done=_on_detect_done)
        else:
            self._detect_drives(on_done=self.cbm_pane.refresh)
        self._restore_prefs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _restore_prefs(self):
        global DRIVE
        cfg = _load_prefs()
        unit = cfg.get("drive", "unit", fallback="").strip()
        if unit.isdigit() and 4 <= int(unit) <= 30:
            DRIVE = unit
            self.cbm_pane.drive_var.set(DRIVE)
            self.cbm_pane._drive_btn.configure(text=f"Drive {DRIVE}")
            self.cbm_pane._hdr.set(f"{DRIVE}:  (not refreshed)")
        if "window" in cfg:
            geo = cfg["window"].get("geometry")
            if geo:
                self.geometry(geo)
        if "panes" in cfg:
            sash = cfg["panes"].get("sash")
            if sash:
                try:
                    self.after(50, lambda: self._paned.sashpos(0, int(sash)))
                except Exception:
                    pass

    def _on_close(self):
        cfg = _load_prefs()
        if "window" not in cfg:
            cfg["window"] = {}
        if "panes" not in cfg:
            cfg["panes"] = {}
        cfg["window"]["geometry"] = self.wm_geometry()
        try:
            cfg["panes"]["sash"] = str(self._paned.sashpos(0))
        except Exception:
            pass
        _save_prefs(cfg)
        self.destroy()

    def _build_toolbar(self):
        pass

    def _build_panes(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        self._paned = paned
        paned.pack(fill="both", expand=True, padx=4, pady=4)
        self.fs_pane  = FilesystemPane(paned)
        self.cbm_pane = CbmDirPane(paned)
        paned.add(self.fs_pane,  weight=1)
        paned.add(self.cbm_pane, weight=0)
        self.fs_pane.bind("<<WriteRequest>>",        self._when_free(self._write_to_cbm))
        self.fs_pane.bind("<<SelectionChanged>>",    lambda e: self._update_read_to_image_state())
        self.cbm_pane.bind("<<DetectRequest>>",  self._when_free(self._detect_drives))
        self.cbm_pane.bind("<<DriveSelected>>",
                           lambda e: self._select_drive(self.cbm_pane.drive_var.get()))
        self.cbm_pane.bind("<<RefreshRequest>>", self._when_free(self._refresh_cbm))
        self.cbm_pane.bind("<<ResetRequest>>",   self._when_free(self._reset_bus))
        self.cbm_pane.bind("<<ReadRequest>>",   self._when_free(self._read_from_cbm))
        self.cbm_pane.bind("<<FormatRequest>>", self._when_free(self._format_disk))
        self.cbm_pane.bind("<<FBifyRequest>>",    self._when_free(self._fbify_disk))
        self.cbm_pane.bind("<<SortRequest>>",     self._when_free(self._sort_disk))
        self.cbm_pane.bind("<<EraseRequest>>",    self._when_free(self._erase_disk))
        self.cbm_pane.bind("<<ReadImageRequest>>",   self._when_free(self._read_image_from_cbm))
        self.cbm_pane.bind("<<ReadToImageRequest>>", self._when_free(self._read_to_image))
        self.cbm_pane.bind("<<ScratchRequest>>",     self._when_free(self._scratch_files))
        self.cbm_pane.bind("<<MakeFirstRequest>>",   self._when_free(self._make_first))

    def _update_read_to_image_state(self):
        if not _C1541:
            return
        left_files = self.fs_pane.selected_files()
        image_exts = {".d64", ".d71", ".d81"}
        enabled = (len(left_files) == 1
                   and os.path.splitext(left_files[0][0])[1].lower() in image_exts
                   and os.path.getsize(left_files[0][1]) in IMAGE_FORMATS)
        self.cbm_pane._read_menu.entryconfigure(2, state="normal" if enabled else "disabled")

    def _build_statusbar(self):
        # Flat, with a separator line above (sunken borders glared in dark mode), and the
        # right end padded so the dot clears the window's rounded corner.
        bar = ttk.Frame(self, padding=(6, 2, 14, 5))
        bar.pack(fill="x", side="bottom")
        ttk.Separator(self, orient="horizontal").pack(fill="x", side="bottom")
        self._build_busy_indicator(bar).pack(side="right")
        self._sv = tk.StringVar(value="Ready.  |  Multi-select: shift-click or cmd-click")
        ttk.Label(bar, textvariable=self._sv, anchor="w").pack(fill="x", side="left", expand=True)

    IDLE_GREEN, BUSY_RED, NO_DRIVE_GREY = "#27ae60", "#d62c1a", "#9a9a9a"

    def _build_busy_indicator(self, parent):
        """A dot at the right of the status bar: flashing red while a drive operation runs
        (_show_busy), green when idle, grey when the last detect (or Cancel's bus check)
        found no drive. The status text says what's running."""
        box = ttk.Frame(parent)
        bg = ttk.Style().lookup("TFrame", "background") or "systemWindowBackgroundColor"
        self._busy_dot = tk.Canvas(box, width=12, height=12, highlightthickness=0, bg=bg)
        self._busy_oval = self._busy_dot.create_oval(1, 1, 11, 11, fill=self.NO_DRIVE_GREY,
                                                     outline="")
        self._busy_dot.pack(side="left")
        self._busy_blink = None
        return box

    def _blink(self):
        on = self._busy_dot.itemcget(self._busy_oval, "state") == "hidden"
        self._busy_dot.itemconfigure(self._busy_oval, state="normal" if on else "hidden")
        self._busy_blink = self.after(500, self._blink)

    def _start_op(self, label, target):
        """Run `target` in a worker thread as a drive operation. Call from the Tk thread.
        Operations started while one runs (a refresh at its end, Cancel's recovery) count
        as part of it; the bus is free again when the count is back to 0."""
        self._ops += 1
        if self._ops == 1:
            self._op_label = label
            self._show_busy(True)

        def wrapper():
            try:
                target()
            finally:
                self.after(0, self._end_op)
        threading.Thread(target=wrapper, daemon=True).start()

    def _end_op(self):
        self._ops -= 1
        if self._ops == 0:
            self._show_busy(False)
            if self._refresh_pending:
                self._refresh_pending = False
                self.cbm_pane.refresh()

    def _show_busy(self, busy):
        state = "disabled" if busy else "normal"
        for b in self.cbm_pane._menubuttons + [self.fs_pane._write_btn]:
            b.configure(state=state)
        if self._busy_blink:
            self.after_cancel(self._busy_blink)
            self._busy_blink = None
        self._busy_dot.itemconfigure(self._busy_oval, state="normal",
                                     fill=self.BUSY_RED if busy else
                                     self.IDLE_GREEN if self._have_drives else self.NO_DRIVE_GREY)
        if busy:
            self._blink()

    def _when_free(self, fn):
        """For the menu events: run fn only while no drive operation runs, after closing
        the finished progress windows of earlier operations."""
        def handler(_e=None):
            if self._ops:
                self.bell()
                self._set_status(f"Drive busy ({self._op_label}): try again when it has finished.")
                return
            self._close_stale()
            fn()
        return handler

    def _close_stale(self):
        """Close progress windows whose operation has finished (marked by their _finish)."""
        for w in self.winfo_children():
            if isinstance(w, tk.Toplevel) and getattr(w, "_stale", False):
                w.destroy()

    def _set_status(self, msg):
        self._sv.set(msg)

    def _make_spinner(self, message):
        """Small modal progress overlay with no cancel. Returns close_fn."""
        dlg = tk.Toplevel(self)
        dlg.title("")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  // 2 - 160
        y = self.winfo_y() + self.winfo_height() // 2 - 45
        dlg.geometry(f"320x90+{x}+{y}")
        ttk.Label(dlg, text=message, padding=(16, 12)).pack()
        pb = ttk.Progressbar(dlg, mode="indeterminate", length=280)
        pb.pack(padx=16, pady=(0, 12))
        pb.start(10)
        return dlg.destroy

    def _make_task_dialog(self, title, message):
        """Modal progress dialog with Cancel button.
        Returns (set_msg, close_fn, cancel_event, proc_store).

        set_msg(msg, fraction=None, detail=None) may be called from any thread. With a
        fraction (0–1) the bar shows real progress plus an estimate of the time left;
        without one it goes back to the busy animation. `detail` is a second, smaller line
        (e.g. "Step 2 of 4"); pass "" to clear it. An elapsed-time counter runs throughout,
        so a long step never looks like a hang."""
        cancel_event = threading.Event()
        proc_store   = [None]

        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  // 2 - 220
        y = self.winfo_y() + self.winfo_height() // 2 - 80
        dlg.geometry(f"440x160+{x}+{y}")

        detail_var = tk.StringVar(value="")
        msg_var    = tk.StringVar(value=message)
        time_var   = tk.StringVar(value="0:00")
        ttk.Label(dlg, textvariable=detail_var, foreground="gray",
                  padding=(16, 8, 16, 0)).pack(anchor="w")
        ttk.Label(dlg, textvariable=msg_var, padding=(16, 2, 16, 6)).pack(anchor="w")
        pb = ttk.Progressbar(dlg, mode="indeterminate", length=400, maximum=1000)
        pb.pack(padx=16)
        pb.start(10)
        ttk.Label(dlg, textvariable=time_var, foreground="gray",
                  padding=(16, 2, 16, 0)).pack(anchor="e")

        started = time.monotonic()
        state   = {"determinate": False, "frac_since": None}

        def _tick():
            if not dlg.winfo_exists():
                return
            el = int(time.monotonic() - started)
            text = f"{el // 60}:{el % 60:02d} elapsed"
            fs = state["frac_since"]
            if state["determinate"] and fs:
                t0, f0, f1 = fs
                rate = (f1 - f0) / max(0.001, time.monotonic() - t0)
                if f1 > f0 + 0.05 and rate > 0:
                    left = int((1 - f1) / rate)
                    text += f"  ·  about {left // 60}:{left % 60:02d} left"
            time_var.set(text)
            dlg.after(1000, _tick)
        dlg.after(1000, _tick)

        def _cancel():
            cancel_btn.configure(state="disabled")
            self._cancel_and_recover(proc_store, cancel_event)

        cancel_btn = ttk.Button(dlg, text="Cancel", command=_cancel)
        cancel_btn.pack(pady=(4, 8))

        # A step that must not be interrupted (writing a directory) calls enter_critical()
        # from the worker: under the lock, either Cancel already won (returns False, do
        # nothing) or Cancel is off from now on (returns True). No race either way.
        crit_lock, crit = threading.Lock(), {"on": False}
        _cancel_plain = _cancel

        def _cancel():
            with crit_lock:
                if crit["on"]:
                    return
                _cancel_plain()
        cancel_btn.configure(command=_cancel)

        def enter_critical():
            with crit_lock:
                if cancel_event.is_set():
                    return False
                crit["on"] = True
            self.after(0, lambda: dlg.winfo_exists() and cancel_btn.configure(state="disabled"))
            return True

        def set_msg(msg, fraction=None, detail=None):
            def _apply():
                if not dlg.winfo_exists():
                    return
                msg_var.set(msg)
                if detail is not None:
                    detail_var.set(detail)
                if fraction is None:
                    if state["determinate"]:
                        pb.configure(mode="indeterminate", value=0)
                        pb.start(10)
                        state.update(determinate=False, frac_since=None)
                else:
                    if not state["determinate"]:
                        pb.stop()
                        pb.configure(mode="determinate")
                        state.update(determinate=True,
                                     frac_since=(time.monotonic(), fraction, fraction))
                    else:
                        t0, f0, _ = state["frac_since"]
                        state["frac_since"] = (t0, f0, fraction)
                    pb.configure(value=int(fraction * 1000))
            self.after(0, _apply)

        set_msg.enter_critical = enter_critical
        return set_msg, dlg.destroy, cancel_event, proc_store

    def _cancel_and_recover(self, proc_store, cancel_event):
        """The one cancel sequence, shared by _make_task_dialog and _make_progress_window.

        Sets cancel_event first: workers check it, stop after the current step and must not
        touch the bus again (no directory refresh), because this sequence owns the bus until
        it finishes. Then: SIGTERM → up to 3 s → SIGKILL, `cbmctrl reset`, a settle pause,
        and a `cbmctrl detect` probe that tells three outcomes apart."""
        cancel_event.set()
        log_event("--- Cancel pressed")
        self._set_status("Cancelling — stopping transfer…")

        def _run():
            _stop_proc(proc_store[0])
            self.after(0, lambda: self._set_status("Cancelled — resetting bus…"))
            self._reset_and_check("Cancelled")

        self._start_op('Recovering the bus', _run)

    def _reset_and_check(self, what):
        """Worker side of Cancel's recovery and Drive → Reset: `cbmctrl reset` (every drive on
        the bus), a settle pause, and a `cbmctrl detect` probe that tells three outcomes
        apart: drives answer (refresh, which recovers a 74 head), the xum1541 doesn't answer
        (replug USB), or no drive answers (power, cable). `what` starts the status text."""
        _run_bounded(["cbmctrl", "reset"], 5)
        time.sleep(2)   # drives run their power-on init after a reset
        r = _run_bounded(["cbmctrl", "detect"], 5)
        drives = parse_detect(r[1]) if r is not None and r[0] == 0 else []
        self._have_drives = bool(drives)        # the dot turns grey without drives
        if drives:
            found = ", ".join(f"{dev}: {model}" for dev, model in drives)
            def _ok():
                self.cbm_pane.set_drives(drives)
                self._set_status(f"{what} — bus OK ({found}).")
                self.cbm_pane.refresh()
            self.after(0, _ok)
        elif r is None:
            self.after(0, lambda: (
                self._set_status("xum1541 unresponsive — replug USB cable."),
                messagebox.showerror("Bus unresponsive",
                    "The xum1541 did not answer a drive scan.\n\n"
                    "Replug its USB cable to recover. If that isn't enough, "
                    "power-cycle the drive too.")))
        else:
            self.after(0, lambda: (
                self.cbm_pane.set_drives([]),
                self._set_status(f"{what} — no drive answers."),
                messagebox.showwarning("No drive",
                    "The xum1541 responds, but no drive answered the scan.\n\n"
                    "Check the drive's power and the IEC cable.")))

    def _reset_bus(self):
        """Drive → Reset: put the bus and every drive back to idle (stops a drive that keeps
        spinning, clears a stuck state), then detect and refresh."""
        log_event("--- Drive → Reset")
        self._set_status("Resetting the bus…")
        self._start_op("Resetting", lambda: self._reset_and_check("Reset"))

    def _detect_drives(self, on_done=None):
        self._set_status("Detecting drives…")
        close = self._make_spinner("Detecting drives…")
        def _run():
            result = cbm_detect()
            def _done():
                close()
                drives = parse_detect(result) if not result.startswith("Error:") else []
                self.cbm_pane.set_drives(drives)
                self._have_drives = bool(drives)
                if not result:
                    self._set_status("No drives detected.")
                elif result.startswith("Error:"):
                    self._set_status(result)
                else:
                    found = ", ".join(f"{u}: {m}" for u, m in drives)
                    units = [u for u, _ in drives]
                    if units and DRIVE not in units and self._select_drive(units[0], refresh=False):
                        self._set_status(f"Detected {found}. Drive {units[0]} selected "
                                         f"(no drive answers at the previous one).")
                    else:
                        self._set_status(f"Detected {found}. Using drive {DRIVE}.")
                if on_done:
                    on_done()
            self.after(0, _done)
        self._start_op('Detecting drives', _run)

    def _select_drive(self, unit, refresh=True):
        """Make `unit` the drive every operation uses. Refused while an operation window is
        open, so a running Sort or copy can't switch drives halfway. Returns True if switched."""
        global DRIVE
        unit = str(unit)
        if unit == DRIVE:
            return True
        busy = self._ops or [w for w in self.winfo_children()
                             if isinstance(w, tk.Toplevel) and w.winfo_exists()]
        if busy:
            self.cbm_pane.drive_var.set(DRIVE)
            messagebox.showinfo("Drive in use",
                "Close the open operation window first, then switch drives.")
            return False
        DRIVE = unit
        self.cbm_pane.drive_var.set(DRIVE)
        self.cbm_pane._drive_btn.configure(text=f"Drive {DRIVE}")
        self.cbm_pane.disk_name = ""
        self.cbm_pane._hdr.set(f"{DRIVE}:  (not refreshed)")
        cfg = _load_prefs()
        cfg["drive"] = {"unit": DRIVE}
        _save_prefs(cfg)
        if refresh:
            self._set_status(f"Using drive {DRIVE}.")
            self.cbm_pane.refresh()
        return True

    def _refresh_cbm(self):
        self._set_status("Refreshing…")
        close = self._make_spinner("Refreshing…")
        self.cbm_pane._hdr.set(f"{DRIVE}:  refreshing…")
        self.cbm_pane.lb.delete(0, tk.END)
        self.cbm_pane._status.set("")
        self.cbm_pane._entries = []
        def _run():
            raw = cbm_dir(DRIVE)
            def _done():
                close()
                self.cbm_pane._apply_dir(raw)
                self._set_status("Refreshed.")
            self.after(0, _done)
        self._start_op('Refreshing', _run)

    def _make_first(self):
        """File → Make first (LOAD"*"): move the selected file to the first directory slot.
        Only the directory is rewritten (move_to_first_slot); the others keep their order."""
        sel = self.cbm_pane.lb.curselection()
        if len(sel) != 1:
            messagebox.showinfo("Make first", "Select one file in the right pane first.")
            return
        index = sel[0]
        listing = self.cbm_pane._entries[:]
        blocks, name, ftype = listing[index]
        if index == 0:
            messagebox.showinfo("Make first", f'"{cbm_display(name)}" is already the first file.')
            return
        if ftype.lower().rstrip("<") != "prg":       # "prg<" is locked: fine; "*prg" isn't
            messagebox.showinfo("Make first",
                f'"{cbm_display(name)}" is a {cbm_display(ftype)} file. LOAD"*" loads programs: pick a PRG.')
            return
        if not messagebox.askyesno("Make first",
                f'Move "{cbm_display(name)}" to the first directory slot on drive {DRIVE}?\n\n'
                'After a drive reset or power-on, LOAD"*",8 (or ,8,1) then loads it. The other '
                "files keep their order. Only the directory is rewritten; the original is saved "
                "to a backup first.", parent=self):
            return

        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Make first", f'Moving "{cbm_display(name)}" to the first slot…')
        self._set_status(f'Moving "{cbm_display(name)}" to the first directory slot…')

        def run():
            ok, msg, backup = move_to_first_slot(
                index, listing, on_step=lambda t: set_msg(t), proc_store=proc_store,
                cancel_event=cancel_event, enter_critical=set_msg.enter_critical)

            def done():
                close()
                if cancel_event.is_set() and msg == "cancelled":
                    return              # _cancel_and_recover reports and refreshes
                self._set_status(msg[0].upper() + msg[1:] + ".")
                if ok:
                    if backup:
                        messagebox.showinfo("Make first", msg[0].upper() + msg[1:] + ".")
                else:
                    messagebox.showerror("Make first",
                        f"Not done: {msg}." + ("" if backup else "\n\nThe disk was not changed."))
                self.cbm_pane.refresh()
            self.after(0, done)

        self._start_op('Make first', run)

    def _scratch_files(self):
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Scratch", "Select one or more files in the right pane first.")
            return
        names = [name for _, name, _ in entries]
        confirm = messagebox.askyesno(
            "Scratch files",
            f"Scratch {len(names)} file{'s' if len(names) > 1 else ''} from drive {DRIVE}?\n\n"
            + "\n".join(cbm_display(n) for n in names))
        if not confirm:
            return

        total = len(names)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Scratching files…", f"Scratching {total} file{'s' if total > 1 else ''}…")
        self._set_status(f"Scratching {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, name in enumerate(names, 1):
                if cancel_event.is_set():
                    break
                set_msg(f"[{i}/{total}]  {cbm_display(name)}")
                proc = subprocess.Popen(
                    ["cbmctrl", "command", DRIVE, f"S:{name.upper()}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc_store[0] = proc
                proc.communicate(timeout=30)
                proc_store[0] = None
                if cancel_event.is_set():
                    break
                code, st = drive_status()      # waits until the drive has finished
                if proc.returncode != 0 or code != 1:
                    errors.append(f"{cbm_display(name)}: {st}")
                else:
                    ok += 1
                    raw = cbm_dir(DRIVE)
                    self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))
            # After a cancel the bus belongs to the cancel sequence, which refreshes itself.
            raw = None if cancel_event.is_set() else cbm_dir(DRIVE)
            def done():
                close()
                if raw is not None:
                    self.cbm_pane._apply_dir(raw)
                if cancel_event.is_set():
                    self._set_status(f"Scratch cancelled after {ok} file{'s' if ok != 1 else ''}.")
                elif errors:
                    messagebox.showerror("Scratch errors", "Failed to scratch:\n" + "\n".join(errors))
                    self._set_status("Scratch completed with errors.")
                else:
                    self._set_status(f"Scratched {total} file{'s' if total > 1 else ''}.")
            self.after(0, done)

        self._start_op('Scratching', run)

    def _read_from_cbm(self):
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Read", "Select one or more files in the right pane first.")
            return
        dest_dir = self.fs_pane.current_dir()
        total    = len(entries)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Reading files…", f"Reading from drive {DRIVE}…")
        self._set_status(f"Reading {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, (blk, name, ftype) in enumerate(entries, 1):
                if cancel_event.is_set():
                    break
                set_msg(f"[{i}/{total}]  {cbm_display(name)}")
                err = cbm_read_file(DRIVE, name, dest_dir, proc_store, ftype)
                if cancel_event.is_set():
                    break
                if err:
                    errors.append(f"{cbm_display(name)}: {err}")
                else:
                    ok += 1
                    self.after(0, self.fs_pane._load)
            def done():
                close()
                self.fs_pane._load()
                if errors:
                    messagebox.showerror("Read errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(f"Cancelled after reading {ok} file{'s' if ok != 1 else ''}.")
                else:
                    self._set_status(f"Read {ok} file(s) to {dest_dir}" +
                                     (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        self._start_op('Reading files', run)

    def _read_to_image(self):
        left_files = self.fs_pane.selected_files()
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in left_files
                  if os.path.splitext(n)[1].lower() in image_exts
                  and os.path.getsize(p) in IMAGE_FORMATS]
        if len(images) != 1:
            messagebox.showinfo("Read to image",
                "Select exactly one recognised disk image in the left pane.")
            return
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Read to image",
                "Select one or more files in the right pane first.")
            return

        img_name, img_path = images[0]
        total       = len(entries)
        need_blocks = sum(b for b, _, _ in entries)

        # Pre-flight: writable?
        if not os.access(img_path, os.W_OK):
            messagebox.showerror("Read to image",
                f"{img_name} is read-only.")
            return

        # Pre-flight: enough blocks free? (c1541 on a local file is fast)
        try:
            _r = subprocess.run([_C1541, "-attach", img_path, "-dir"],
                                capture_output=True, text=True, timeout=5)
            _m = re.search(r'(\d+)\s+blocks?\s+free', _r.stdout, re.IGNORECASE)
            img_free = int(_m.group(1)) if _m else None
        except Exception:
            img_free = None

        if img_free is not None and need_blocks > img_free:
            messagebox.showerror("Read to image",
                f"Not enough space: need {need_blocks} blocks, "
                f"image has {img_free} free.")
            return

        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Reading to image",
            f"Reading {total} file{'s' if total > 1 else ''} into {img_name}…")
        self._set_status(f"Reading to {img_name}…")

        def run():
            errors = []
            ok     = 0
            with tempfile.TemporaryDirectory() as tmp:
                for i, (blk, name, ftype) in enumerate(entries, 1):
                    if cancel_event.is_set():
                        break
                    set_msg(f"[{i}/{total}]  {cbm_display(name)}")

                    per_dir = tempfile.mkdtemp(dir=tmp)
                    err = cbm_read_file(DRIVE, name, per_dir, proc_store, ftype)
                    if cancel_event.is_set():
                        break
                    if err:
                        errors.append(f"{cbm_display(name)}: {err}")
                        continue

                    tmp_files = [f for f in os.listdir(per_dir)
                                 if os.path.isfile(os.path.join(per_dir, f))]
                    if not tmp_files:
                        errors.append(f"{cbm_display(name)}: cbmread produced no file")
                        continue
                    local_file = os.path.join(per_dir, tmp_files[0])

                    tc = type_char(ftype) or "p"
                    r2 = subprocess.run(
                        [_C1541, "-attach", img_path,
                         "-write", local_file, f"{name},{tc}"],
                        capture_output=True, text=True, timeout=30)
                    if r2.returncode != 0:
                        errors.append(
                            f"{cbm_display(name)}: {(r2.stderr.strip() or r2.stdout.strip() or 'c1541 error')}")
                    else:
                        ok += 1

            def done():
                close()
                self.after(0, self.fs_pane._update_sel)
                if errors:
                    messagebox.showerror("Read to image errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(
                        f"Cancelled after reading {ok} file{'s' if ok != 1 else ''} to image.")
                else:
                    self._set_status(
                        f"Read {ok} file{'s' if ok != 1 else ''} into {img_name}" +
                        (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        self._start_op('Reading to image', run)

    def _write_to_cbm(self):
        files = self.fs_pane.selected_files()
        if not files:
            messagebox.showinfo("Write", "Select one or more files in the left pane first.")
            return
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() in image_exts]
        prgs   = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() not in image_exts]
        if images and prgs:
            messagebox.showerror("Mixed selection",
                                 "Select either disk images or individual files, not both.")
            return
        if len(images) > 1:
            messagebox.showerror("One at a time", "Select only one disk image at a time.")
            return
        if images:
            self._write_image_to_cbm(images[0][1])
            return
        total = len(files)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Writing files…", f"Writing to drive {DRIVE}…")
        self._set_status(f"Writing {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, (name, path) in enumerate(files, 1):
                if cancel_event.is_set():
                    break
                cbm_name = os.path.splitext(name)[0].lower()[:16]
                set_msg(f"[{i}/{total}]  {name}")
                err = cbm_write_file(DRIVE, path, cbm_name, proc_store)
                if cancel_event.is_set():
                    break
                if err:
                    errors.append(f"{name}: {err}")
                else:
                    ok += 1
                    raw = cbm_dir(DRIVE)
                    self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))
            def done():
                close()
                if not cancel_event.is_set():
                    self.cbm_pane.refresh()
                if errors:
                    messagebox.showerror("Write errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(f"Cancelled after writing {ok} file{'s' if ok != 1 else ''}.")
                else:
                    self._set_status(f"Wrote {ok} file(s) to drive {DRIVE}" +
                                     (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        self._start_op('Writing files', run)

    def _write_image_to_cbm(self, path):
        size     = os.path.getsize(path)
        fmt_info = IMAGE_FORMATS.get(size)
        if not fmt_info:
            messagebox.showerror("Unknown format",
                f"File size {size:,} bytes doesn't match any known disk image format.")
            return
        fmt_label, ext, is_40track, is_d71 = fmt_info
        basename = os.path.basename(path)

        dlg = tk.Toplevel(self)
        dlg.title("Write disk image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        info_frame = ttk.LabelFrame(dlg, text="Image", padding=(8, 4))
        info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Label(info_frame, text=f"File:    {basename}",  font=("Menlo", 10), anchor="w").pack(fill="x")
        ttk.Label(info_frame, text=f"Format:  {fmt_label}", font=("Menlo", 10), anchor="w").pack(fill="x")
        if is_d71:
            ttk.Label(info_frame, text="Requires 1571 drive.", font=("Menlo", 10),
                      foreground="orange", anchor="w").pack(fill="x")

        fmt_frame = ttk.LabelFrame(dlg, text="Pre-format disk", padding=(8, 4))
        fmt_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        prefmt_var = tk.BooleanVar(value=is_40track)
        ttk.Checkbutton(fmt_frame, text="Format disk before writing",
                        variable=prefmt_var, command=lambda: _toggle()).grid(
            row=0, column=0, columnspan=4, sticky="w")
        dname, did = ("DISK", "01") if ext == ".d81" else image_format_header(_read_bytes(path))
        hdr_lbl = ttk.Label(fmt_frame, text=f'With the image\'s name and ID: "{dname}" {did}',
                            font=("Menlo", 10), anchor="w")
        hdr_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=4, pady=4)

        def _toggle():
            hdr_lbl.configure(state="normal" if prefmt_var.get() else "disabled")
        _toggle()

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(4, 10))

        def _do_write():
            do_fmt = prefmt_var.get()
            dlg.destroy()
            prog, pbar, close_btn, _append, proc_store, cancel_event = \
                self._make_progress_window(f"Writing {basename}…")
            self._set_status(f"Writing {basename} to drive {DRIVE}…")

            def run():
                ok = True
                if do_fmt:
                    fmt_flag = ["-x"] if is_40track else []
                    fmt_cmd  = ["cbmforng"] + fmt_flag + ["-r", "2", DRIVE, f"{dname},{did}"]
                    self.after(0, lambda: _append(f"Pre-formatting drive {DRIVE}…\n"))
                    _run_bounded(["cbmctrl", "reset"], 5)
                    rc = self._pty_run(fmt_cmd, _append, proc_store)
                    if rc != 0:
                        self.after(0, lambda: _append("\nFormat failed — aborting write.\n"))
                        ok = False

                if cancel_event.is_set():
                    ok = False
                if ok and is_40track and not do_fmt:
                    # Writing tracks 36–40 onto a disk not formatted that far gives seek
                    # errors (opencbm-tools.md), so check the target first.
                    self.after(0, lambda: _append("Checking that the disk has track 36…\n"))
                    pr = probe_disk(proc_store, cancel_event, track18=False)
                    if cancel_event.is_set():
                        ok = False
                    elif not pr["t36_checked"]:
                        self.after(0, lambda: _append(
                            "\nCouldn't check track 36 (the read timed out). Write again with "
                            "'Pre-format' ticked (it uses cbmforng -x).\n"))
                        ok = False
                    elif pr["t36_partial"]:
                        n36 = pr["t36_ok"]
                        self.after(0, lambda: _append(
                            f"\nTrack 36 is damaged ({n36} of 17 sectors readable). Write again "
                            "with 'Pre-format' ticked (it uses cbmforng -x).\n"))
                        ok = False
                    elif not pr["t36_readable"]:
                        self.after(0, lambda: _append(
                            "\nThis disk isn't formatted beyond track 35. Write again with "
                            "'Pre-format' ticked (it uses cbmforng -x).\n"))
                        ok = False
                    else:
                        self.after(0, lambda: _append("Track 36 is there.\n"))
                if ok:
                    if ext == ".d81":
                        copy_cmd = ["imgcopy", path, DRIVE]
                    elif is_d71:
                        copy_cmd = ["d64copy", "--two-sided", "-d", "1571", path, DRIVE]
                    elif is_40track:
                        copy_cmd = ["d64copy", "--end-track", "40", path, DRIVE]
                    else:
                        copy_cmd = ["d64copy", path, DRIVE]
                    self.after(0, lambda: _append(f"\nCopying image…\n"))
                    reset_and_settle()
                    out = []
                    def _collect(t):
                        out.append(t)
                        _append(t)
                    rc = 1 if cancel_event.is_set() else self._pty_run(copy_cmd, _collect, proc_store)
                    after_d64copy(cancel_event)
                    text = "".join(out)
                    # d64copy exits 0 even after a [Fatal] (2026-11-25: writing to an erased
                    # disk stopped at once with 21,READ ERROR, exit 0), so judge by its
                    # "N blocks copied." line too.
                    ok = rc == 0 and not cancel_event.is_set() and "[Fatal]" not in text
                    if ok and copy_cmd[0] == "d64copy" and not is_d71:
                        copied, expected = blocks_copied(text, 40 if is_40track else 35)
                        if copied != expected:
                            ok = False
                            self.after(0, lambda: _append(
                                f"\nOnly {copied or 0} of {expected} blocks were written.\n"))
                    elif ok and not re.search(r'\d+ blocks copied', text):
                        ok = False
                    if not ok and not cancel_event.is_set() and not do_fmt and \
                            re.search(r'\b2[0-3],READ ERROR|\b74,', text):
                        self.after(0, lambda: _append(
                            "\nThe disk doesn't look formatted. Write again with "
                            "'Pre-format' ticked.\n"))

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if cancel_event.is_set():
                        _append("\nCancelled. The disk is incomplete.\n")
                        prog.title("Write cancelled")
                    elif ok:
                        _append("\nDone.\n")
                        prog.title("Write complete")
                        self._set_status(f"Wrote {basename} to drive {DRIVE}.")
                        self.after(1500, self.cbm_pane.refresh)
                    else:
                        _append("\nWrite failed.\n")
                        prog.title("Write failed")
                        self._set_status("Write failed.")
                    prog._finish()
                self.after(0, done)

            self._start_op('Writing image', run)

        ttk.Button(btn_frame, text="Write", command=_do_write).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _read_image_from_cbm(self):
        hdr      = self.cbm_pane._hdr.get()
        m        = re.search(r'"(.+?)"', hdr)
        diskname = self.cbm_pane.disk_name.strip() or (m.group(1).strip() if m else "disk")
        dest_dir = self.fs_pane.current_dir()

        dlg = tk.Toplevel(self)
        dlg.title("Read disk to image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        info_frame = ttk.LabelFrame(dlg, text="Source", padding=(8, 4))
        info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        disk_lbl = hdr.split("  ", 1)[-1] if "  " in hdr else hdr
        ttk.Label(info_frame, font=("Menlo", 10), anchor="w",
                  text=f"Drive:    {DRIVE}\nDisk:     {disk_lbl}\nSave to:  {dest_dir}").pack(fill="x")

        out_frame = ttk.LabelFrame(dlg, text="Output", padding=(8, 4))
        out_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(out_frame, text="Filename:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        fname_var = tk.StringVar(value=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', diskname).lower()}.d64")
        fname_entry = ttk.Entry(out_frame, textvariable=fname_var, width=26)
        fname_entry.grid(row=0, column=1, sticky="w", padx=4)
        ext40_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(out_frame, text="40-track  (--end-track 40)", variable=ext40_var,
                        command=lambda: _explain()).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        ttk.Label(out_frame, text="Retries:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        retry_var = tk.IntVar(value=5)
        ttk.Spinbox(out_frame, from_=0, to=100, textvariable=retry_var,
                    width=6).grid(row=2, column=1, sticky="w", padx=4)

        check_frame = ttk.LabelFrame(dlg, text="Disk check", padding=(8, 4))
        check_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        check_var = tk.StringVar(value="Checking the disk (tracks 18 and 36)…")
        check_lbl = ttk.Label(check_frame, textvariable=check_var, justify="left", wraplength=380)
        check_lbl.pack(anchor="w")
        # Busy bar + seconds counter while the check runs (it takes 5–10 s), so the
        # dialog doesn't look stuck; both go away when the result arrives.
        check_bar = ttk.Progressbar(check_frame, mode="indeterminate", length=380)
        check_bar.pack(anchor="w", pady=(4, 0))
        check_bar.start(10)
        check_started = time.monotonic()

        def _tick_check():
            if probe or not dlg.winfo_exists():
                return
            check_var.set(f"Checking the disk (tracks 18 and 36)…  "
                          f"{int(time.monotonic() - check_started)} s")
            dlg.after(1000, _tick_check)
        dlg.after(1000, _tick_check)

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(4, 10))
        read_btn = ttk.Button(btn_frame, text="Read image", state="disabled",
                              command=lambda: _do_read())
        read_btn.pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        probe = {}

        def _explain():
            """Say what the check found and whether the chosen track count fits it."""
            if not probe:
                return
            if probe["error"]:
                msg, warn = (f"Couldn't check the disk ({probe['error']}). "
                             "Choose the track count yourself."), False
            elif probe["double_sided"]:
                msg, warn = ("Double-sided (1571) disk. Reading both sides to .d71 isn't "
                             "supported yet, and a .d64 would hold only one side."), True
            elif not probe["t36_checked"]:
                msg = ("Couldn't check track 36 (the read timed out), so it's unknown whether "
                       "this is a 40-track disk. Tick 40-track to be safe: on a 35-track disk "
                       "the extra tracks just read as errors.")
                warn = not ext40_var.get()
            elif probe["t36_partial"]:
                msg = (f"Track 36 is formatted but partly unreadable ({probe['t36_ok']} of 17 "
                       "sectors read): a 40-track disk that has decayed. Reading 40 tracks keeps "
                       "what survives; the errors are recorded in the image.")
                warn = not ext40_var.get()
            elif probe["t36_readable"]:
                what = "holds data" if probe["t36_data"] else "is formatted but empty"
                msg = f"Track 36 {what}: this is a 40-track disk."
                warn = not ext40_var.get()
                if warn:
                    msg += " A 35-track image would leave tracks 36–40 out."
            else:
                msg, warn = "Standard 35-track disk.", False
            if probe.get("ext_bam"):
                msg += f" Extended BAM: {probe['ext_bam']}."
            msg += "  Reading takes about a minute."
            check_var.set(msg)
            check_lbl.configure(foreground="red" if warn else "")

        def _probed(res):
            if not dlg.winfo_exists():
                return
            probe.update(res)
            check_bar.stop()
            check_bar.pack_forget()
            ext40_var.set(bool(res["t36_readable"] or res["t36_partial"]))
            read_btn.configure(state="disabled" if res["double_sided"] and not res["error"]
                               else "normal")
            _explain()

        def _probe():
            res = probe_disk()
            self.after(0, lambda: _probed(res))
        self._start_op('Reading image', _probe)

        def _do_read():
            if str(read_btn.cget("state")) == "disabled":
                return
            fname = fname_var.get().strip() or f"{diskname}.d64"
            if not fname.lower().endswith((".d64", ".d71", ".d81")):
                fname += ".d64"
            out_path  = os.path.join(dest_dir, fname)
            if os.path.exists(out_path) and not messagebox.askyesno(
                    "Replace image?", f"{fname} already exists. Replace it?", parent=dlg):
                return
            is_40     = ext40_var.get()
            retries   = retry_var.get()
            dlg.destroy()
            prog, pbar, close_btn, _append, proc_store, cancel_event = \
                self._make_progress_window(f"Reading disk to {fname}…")
            self._set_status(f"Reading drive {DRIVE} to {fname}…")
            part = out_path + ".part"

            def run():
                cmd = ["d64copy", "-r", str(retries)]
                if is_40:
                    cmd += ["--end-track", "40"]
                cmd += [DRIVE, part]
                self.after(0, lambda: _append(f"Running: {' '.join(cmd)}\n\n"))
                out = []
                def _text(t):
                    out.append(t)
                    self.after(0, lambda t=t: _append(t))
                reset_and_settle()
                try:
                    rc = 1 if cancel_event.is_set() else stream_cmd(cmd, _text, proc_store)
                except OSError as e:
                    rc = 1
                    out.append(str(e))
                after_d64copy(cancel_event)
                text = "".join(out)
                size = os.path.getsize(part) if os.path.exists(part) else 0
                ok = (rc == 0 and size in IMAGE_FORMATS and not cancel_event.is_set()
                      and re.search(r'\d+ blocks copied', text))
                report, kept = [], None
                if ok:
                    os.replace(part, out_path)
                    copied, expected = blocks_copied(text, 40 if is_40 else 35)
                    if copied is not None and copied < expected:
                        report.append(f"{expected - copied} of {expected} blocks couldn't be "
                                      "read (marked in the image's error table).")
                    try:
                        report += describe_image(analyze_image(_read_bytes(out_path)))
                    except (ValueError, OSError) as e:
                        report.append(f"(no analysis: {e})")
                elif os.path.exists(part):
                    # d64copy fills the file to full size up front, so a partial read
                    # looks complete. Keep it (a failing disk may not read again) but
                    # under a name that says what it is.
                    base, ext = os.path.splitext(out_path)
                    kept, n = f"{base}-incomplete{ext}", 2
                    while os.path.exists(kept):
                        kept, n = f"{base}-incomplete-{n}{ext}", n + 1
                    os.replace(part, kept)

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if ok:
                        _append(f"\nSaved: {out_path}\n" + "".join(f"  {l}\n" for l in report))
                        prog.title("Read complete")
                        self._set_status(f"Saved {fname}.")
                    else:
                        _append("\nCancelled." if cancel_event.is_set() else "\nRead failed.")
                        _append(f" Partial image kept as {os.path.basename(kept)}.\n" if kept else "\n")
                        prog.title("Read cancelled" if cancel_event.is_set() else "Read failed")
                        if not cancel_event.is_set():
                            self._set_status("Read failed.")
                    self.fs_pane._load()
                    prog._finish()
                self.after(0, done)

            self._start_op('Reading image', run)

        fname_entry.bind("<Return>", lambda e: _do_read())
        dlg.wait_window()

    def _make_progress_window(self, title):
        """Return (prog, pbar, close_btn, _append, proc_store, cancel_event): a progress Toplevel.
        proc_store[0] holds the running Popen (set by _pty_run) so Cancel stops the right one;
        after a cancel, workers must check cancel_event and leave the bus alone."""
        self._close_stale()
        prog = tk.Toplevel(self)
        prog.title(title)
        prog.geometry("680x380")
        prog.minsize(500, 280)
        prog.transient(self)
        prog.resizable(True, True)

        pbar = ttk.Progressbar(prog, mode="indeterminate", length=400)
        pbar.pack(padx=6, pady=(6, 2), fill="x")
        pbar.start(50)

        proc_store   = [None]
        cancel_event = threading.Event()
        finished     = [False]

        btn_row = ttk.Frame(prog)
        btn_row.pack(side="bottom", pady=(2, 8))

        txt_frame = ttk.Frame(prog)
        txt_frame.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        txt = tk.Text(txt_frame, font=("Menlo", 10), state="disabled",
                      wrap="none", relief="flat", bd=0)
        vsb = ttk.Scrollbar(txt_frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _cancel():
            if finished[0] or cancel_event.is_set():
                return
            cancel_btn.configure(state="disabled")
            _append("\nCancelling…\n")
            self._cancel_and_recover(proc_store, cancel_event)

        def _on_close_button():
            # The window's close button: cancel while running, plain close once finished.
            if finished[0]:
                prog.destroy()
            else:
                _cancel()

        cancel_btn = ttk.Button(btn_row, text="Cancel", command=_cancel)
        cancel_btn.pack(side="left", padx=6)
        close_btn = ttk.Button(btn_row, text="Close", state="disabled", command=prog.destroy)
        close_btn.pack(side="left", padx=6)

        prog.protocol("WM_DELETE_WINDOW", _on_close_button)

        def _append(text):
            txt.configure(state="normal")
            txt.insert(tk.END, text)
            txt.see(tk.END)
            txt.configure(state="disabled")

        def _finish():
            finished[0] = True
            prog._stale = True          # closed by the next drive action (_close_stale)
            cancel_btn.configure(state="disabled")
            close_btn.configure(state="normal")

        prog._finish = _finish

        return prog, pbar, close_btn, _append, proc_store, cancel_event

    def _pty_run(self, cmd, _append, proc_store=None):
        """Run cmd via pty, streaming output to _append on the Tk thread. Returns exit code.
        Call from a worker thread."""
        try:
            return stream_cmd(cmd, lambda t: self.after(0, lambda t=t: _append(t)), proc_store)
        except Exception as e:
            self.after(0, lambda: _append(f"\nError: {e}\n"))
            return 1

    def _format_disk(self):
        dlg = tk.Toplevel(self)
        dlg.title("Format disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # --- Tool selector ---
        tool_var = tk.StringVar(value="cbmforng")
        tool_frame = ttk.LabelFrame(dlg, text="Tool", padding=(8, 4))
        tool_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Radiobutton(tool_frame, text="cbmforng", variable=tool_var, value="cbmforng",
                        command=lambda: _on_tool_change()).pack(side="left", padx=8)
        ttk.Radiobutton(tool_frame, text="cbmformat", variable=tool_var, value="cbmformat",
                        command=lambda: _on_tool_change()).pack(side="left", padx=8)

        # --- Disk name / ID ---
        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 4))
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(info_frame, text="Name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value="DISKNAME")
        name_entry = ttk.Entry(info_frame, textvariable=name_var, width=18)
        name_entry.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(info_frame, text="ID:").grid(row=0, column=2, sticky="e", padx=(12, 4))
        id_var = tk.StringVar(value="01")
        id_entry = ttk.Entry(info_frame, textvariable=id_var, width=5)
        id_entry.grid(row=0, column=3, sticky="w", padx=4)

        # --- Options ---
        opt_frame = ttk.LabelFrame(dlg, text="Options", padding=(8, 4))
        opt_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        ext_var      = tk.BooleanVar(value=False)
        nobump_var   = tk.BooleanVar(value=False)
        clear_var    = tk.BooleanVar(value=False)
        verify_var   = tk.BooleanVar(value=False)
        original_var = tk.BooleanVar(value=False)
        status_var   = tk.BooleanVar(value=True)
        progress_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(opt_frame, text="40-track extended  (-x)",      variable=ext_var     ).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="No head bump       (-n)",       variable=nobump_var  ).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Demagnetize first  (-c)",       variable=clear_var   ).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Verify after write (-v)",       variable=verify_var  ).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Original fill pattern (-o)",    variable=original_var).grid(row=4, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Show drive status (-s)",        variable=status_var  ).grid(row=5, column=0, sticky="w", pady=2)
        progress_cb = ttk.Checkbutton(opt_frame, text="Progress indicator (-p, cbmformat only)",
                                      variable=progress_var)
        progress_cb.grid(row=6, column=0, sticky="w", pady=2)

        # --- Retries (cbmforng only) ---
        retry_frame = ttk.Frame(dlg)
        retry_frame.grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(2, 6))
        retry_label = ttk.Label(retry_frame, text="Retries (-r):")
        retry_label.pack(side="left")
        retry_var = tk.IntVar(value=2)
        retry_spin = ttk.Spinbox(retry_frame, from_=0, to=10, textvariable=retry_var, width=4)
        retry_spin.pack(side="left", padx=6)

        def _on_tool_change():
            is_forng = tool_var.get() == "cbmforng"
            state = "normal" if is_forng else "disabled"
            retry_label.configure(state=state)
            retry_spin.configure(state=state)
            progress_cb.configure(state="disabled" if is_forng else "normal")

        # --- Buttons ---
        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 10))

        def _do_format():
            dname = name_var.get().strip() or "DISKNAME"
            did   = id_var.get().strip() or "01"
            problem = format_header_problem(dname, did)
            if problem:
                messagebox.showerror("Format", problem, parent=dlg)
                return
            tool  = tool_var.get()
            cmd   = [tool]
            if ext_var.get():      cmd.append("-x")
            if nobump_var.get():   cmd.append("-n")
            if clear_var.get():    cmd.append("-c")
            if verify_var.get():   cmd.append("-v")
            if original_var.get(): cmd.append("-o")
            if status_var.get():   cmd.append("-s")
            if tool == "cbmformat" and progress_var.get():
                cmd.append("-p")
            if tool == "cbmforng":
                cmd += ["-r", str(retry_var.get())]
            cmd += [DRIVE, f"{dname},{did}"]
            dlg.destroy()

            prog, pbar, close_btn, _append, proc_store, cancel_event = self._make_progress_window(
                f"Formatting drive {DRIVE}…")
            self._set_status(f"Formatting drive {DRIVE} with {tool}…")

            def run():
                _run_bounded(["cbmctrl", "reset"], 5)
                rc = 1 if cancel_event.is_set() else self._pty_run(cmd, _append, proc_store)
                ok = rc == 0 and not cancel_event.is_set()

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if cancel_event.is_set():
                        # The cancel sequence resets the bus and refreshes on its own.
                        _append("\nCancelled. The disk is not usable until formatted again.\n")
                        prog.title("Format cancelled")
                        prog._finish()
                        return
                    if ok:
                        _append("\nDone.\n")
                        prog.title("Format complete")
                        self._set_status("Format complete.")
                    else:
                        _append("\nFormat failed.\n")
                        prog.title("Format failed")
                        self._set_status("Format failed.")
                    def _reset_and_refresh():
                        # In a thread: a hung reset must not freeze the UI.
                        _run_bounded(["cbmctrl", "reset"], 5)
                        self.after(0, self.cbm_pane.refresh)
                    self._start_op("Resetting the drive",
                                   lambda: (time.sleep(1.5), _reset_and_refresh()))
                    prog._finish()

                self.after(0, done)

            self._start_op('Formatting', run)

        ttk.Button(btn_frame, text="Format", command=_do_format).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

        name_entry.bind("<Return>", lambda e: _do_format())
        id_entry.bind("<Return>",   lambda e: _do_format())

        dlg.wait_window()

    def _erase_disk(self):
        """Disk → Erase…: wipe tracks 36–40 (a 40-track disk becomes a clean 35-track one) or
        the whole disk (1–40, as if never formatted). Drive code: erase_tracks()."""
        dlg = tk.Toplevel(self)
        dlg.title("Erase disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        name = self.cbm_pane.disk_name.strip()
        ttk.Label(dlg, padding=(14, 12, 14, 4), justify="left",
                  text=f"Drive {DRIVE}" + (f', disk "{name}"' if name else "") + ".\n"
                       "Erasing overwrites whole tracks: nothing on them can be read back.").grid(
            row=0, column=0, sticky="w")
        what = tk.StringVar(value="all")
        box = ttk.LabelFrame(dlg, text="Erase", padding=(8, 4))
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        ttk.Radiobutton(box, variable=what, value="all",
                        text="Whole disk (tracks 1–40)").grid(row=0, column=0, sticky="w")
        ttk.Label(box, foreground="gray", justify="left",
                  text="Everything is gone and the disk reads as never formatted.\n"
                       "Format it before use.").grid(
            row=1, column=0, sticky="w", padx=(22, 0), pady=(0, 6))
        ttk.Radiobutton(box, variable=what, value="extra",
                        text="Tracks 36–40 only").grid(row=2, column=0, sticky="w")
        ttk.Label(box, foreground="gray", justify="left",
                  text="Makes a 40-track disk a clean 35-track one. Files on tracks 1–35\n"
                       "stay; anything reaching into tracks 36–40 is lost.").grid(
            row=3, column=0, sticky="w", padx=(22, 0), pady=(0, 4))

        def _go():
            tracks = list(range(36, 41)) if what.get() == "extra" else list(range(1, 41))
            label = "tracks 36–40" if tracks[0] == 36 else "the whole disk"
            if not messagebox.askyesno("Erase disk",
                    f"Erase {label} on drive {DRIVE}" + (f' ("{name}")' if name else "") +
                    "?\n\nThis cannot be undone.", icon="warning", parent=dlg):
                return
            dlg.destroy()
            self._run_erase(tracks, label)

        btns = ttk.Frame(dlg)
        btns.grid(row=2, column=0, pady=(6, 10))
        ttk.Button(btns, text="Erase…", command=_go).pack(side="left", padx=8)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _run_erase(self, tracks, label):
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Erasing", f"Erasing {label}…")
        self._set_status(f"Erasing {label} on drive {DRIVE}…")

        def run():
            ok, msg = erase_tracks(
                tracks, lambda t, f: set_msg(f"Erasing track {t}…", f,
                                             f"Tracks {tracks[0]}–{tracks[-1]}"),
                proc_store, cancel_event)
            check = None
            if ok and not cancel_event.is_set():
                set_msg("Checking the result…", None, "")
                check = probe_disk(proc_store, cancel_event, track18=tracks[0] == 36)

            def done():
                close()
                if cancel_event.is_set():
                    # _cancel_and_recover resets the bus and refreshes the pane.
                    messagebox.showinfo("Erase cancelled",
                        "Erasing was cancelled. The track being written when you cancelled "
                        "may be partly erased; erase again to finish the job.")
                    return
                if not ok:
                    self._set_status(f"Erase failed: {msg}.")
                    messagebox.showerror("Erase failed", f"Erase failed: {msg}.")
                    self.cbm_pane.refresh()
                    return
                if tracks[0] == 36:
                    if check and not check["error"] and check["t36_checked"] and check["t36_ok"] == 0:
                        text = "Tracks 36–40 erased. The disk now checks as a 35-track disk."
                    else:
                        text = ("Tracks 36–40 were written, but the check afterwards didn't "
                                "confirm it: " + (check["error"] if check and check["error"] else
                                "track 36 check timed out" if check and not check["t36_checked"]
                                else f"{check['t36_ok'] if check else '?'} of 17 sectors on track "
                                     f"36 still read") + ".")
                else:
                    text = ("Disk erased: tracks 1–40 hold nothing readable now. "
                            "Format it before use.")
                self._set_status(text)
                messagebox.showinfo("Erase complete", text)
                self.cbm_pane.refresh()
            self.after(0, done)

        self._start_op('Erasing', run)

    def _add_image_frame(self, dlg, row, enabled=True):
        """The 'Safety copy' section of the Sort and FB-ify dialogs.
        The whole disk is always read first; this only chooses whether the image is kept.
        Returns a function giving (keep, path); call it before destroying dlg."""
        hdr      = self.cbm_pane._hdr.get()
        m        = re.search(r'"(.+?)"', hdr)
        diskname = re.sub(r'[^a-zA-Z0-9_-]', '_', self.cbm_pane.disk_name.strip()
                          or (m.group(1).strip() if m else "disk")).lower()
        dest_dir = self.fs_pane.current_dir()
        default  = os.path.join(dest_dir, f"{diskname}_backup.d64")
        n = 2
        while os.path.exists(default):   # never offer to overwrite an earlier backup
            default = os.path.join(dest_dir, f"{diskname}_backup-{n}.d64")
            n += 1

        frame = ttk.LabelFrame(dlg, text="Safety copy", padding=(8, 4))
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(frame, foreground="gray", justify="left",
                  text=("Reads the whole disk first and checks it (about 1 minute).\n"
                        "Nothing is changed if the check finds a problem.") if enabled else
                       "The disk is empty: nothing to copy.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        var = tk.BooleanVar(value=enabled)
        cb = ttk.Checkbutton(frame, text="Keep the image afterwards", variable=var,
                             command=lambda: _toggle())
        cb.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="Path:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        path_var = tk.StringVar(value=default)
        entry = ttk.Entry(frame, textvariable=path_var, width=34)
        entry.grid(row=2, column=1, sticky="ew", padx=4)

        def _toggle():
            entry.configure(state="normal" if var.get() else "disabled")
        if not enabled:
            cb.configure(state="disabled")
        _toggle()
        return lambda: (var.get(), path_var.get().strip() or default)

    def _rewrite_disk(self, verb, snap, write_order, fb_files, keep_image, image_path):
        """Shared engine of Sort and FB-ify: scratch the disk and write it back.

        Steps: 1 check the disk (tracks 18 and 36), 2 read the whole disk to an image
        (40 tracks when track 36 is formatted) and analyse it, 3 read every file,
        4 scratch and write FB files + write_order. Nothing is changed unless 1–3 all pass,
        so an unsuitable disk (double-sided, files beyond track 35, read errors in files,
        unsupported file types, or a disk swapped since the listing) is refused untouched.
        If step 4 fails or is cancelled, the read files are moved to a rescue folder next
        to the image, and the image is kept even if keep_image is off."""
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            f"{verb} disk…", "Starting…")
        self._set_status(f"{verb} disk…")
        steps = 4 if snap else 1

        def _push_dir():
            raw = cbm_dir(DRIVE)
            self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))

        def run():
            outcome = {"abort": None, "errors": [], "rescue": None, "image": None,
                       "touched": False}
            tmpdir = tempfile.mkdtemp(prefix="diskmasher-")
            part   = None
            local_copy = {}     # index in snap → local file read in step 3
            rd_stats   = {}

            def done():
                close()
                o = outcome
                saved = f"\n\nDisk image: {o['image']}" if o["image"] else ""
                if o["rescue"]:
                    messagebox.showerror(f"{verb} incomplete",
                        f"{verb} stopped after the disk was scratched.\n\n"
                        + ("\n".join(o["errors"]) + "\n\n" if o["errors"] else "")
                        + f"The files read from the disk are in:\n{o['rescue']}" + saved)
                    self._set_status(f"{verb} incomplete — files saved in rescue folder.")
                elif o["abort"]:
                    note = f"\n\n{o['note']}" if o.get("note") else ""
                    messagebox.showerror(f"{verb} not done", o["abort"] + note + saved)
                    self._set_status(f"{verb} not done — disk unchanged.")
                elif cancel_event.is_set():
                    self._set_status(f"{verb} cancelled — disk unchanged.")
                elif o["errors"]:
                    messagebox.showerror(f"{verb} errors", "\n".join(o["errors"]) + saved)
                    self._set_status(f"{verb} completed with errors.")
                else:
                    self._set_status(f"{verb} complete." +
                                     (f" Image saved: {o['image']}" if o["image"] else ""))
                if o["image"] or o["rescue"]:
                    self.fs_pane._load()
                if not cancel_event.is_set():
                    self.cbm_pane.refresh()

            try:
                if snap:
                    # --- Step 1: quick check ---
                    set_msg("Reading tracks 18 and 36…", detail=f"Step 1 of {steps} · Checking the disk")
                    pr = probe_disk(proc_store, cancel_event)
                    if cancel_event.is_set():
                        return
                    if pr["error"]:
                        outcome["abort"] = f"Could not check the disk: {pr['error']}.\n\nDisk unchanged."
                        return
                    if pr["double_sided"]:
                        outcome["abort"] = ("This is a double-sided (1571) disk. "
                                            f"{verb} only handles single-sided disks so far.\n\nDisk unchanged.")
                        return
                    # If track 36 couldn't be checked, 35 tracks is still safe here: Sort/FB-ify
                    # never write beyond 35, and a file reaching track 36 shows up in the
                    # analysis as a chain pointing outside the image.
                    tracks = 40 if (pr["t36_readable"] or pr["t36_partial"]) else 35

                    # --- Step 2: whole-disk read + analysis ---
                    part = (image_path + ".part") if keep_image else os.path.join(tmpdir, "disk.d64")
                    det2 = f"Step 2 of {steps} · Reading the whole disk ({tracks} tracks)"
                    set_msg("Starting…", detail=det2)
                    err = read_disk_to_file(part, tracks,
                        lambda t, f: set_msg(f"Track {t} of {tracks}", fraction=f, detail=det2),
                        proc_store, cancel_event, rd_stats)
                    if cancel_event.is_set():
                        return
                    if err:
                        outcome["abort"] = f"Reading the disk failed ({err}).\n\nDisk unchanged."
                        return
                    info = analyze_image(_read_bytes(part))
                    if rd_stats.get("copied") is not None and rd_stats["copied"] < rd_stats["expected"]:
                        outcome["note"] = (f"{rd_stats['expected'] - rd_stats['copied']} of "
                                           f"{rd_stats['expected']} blocks couldn't be read.")
                    if keep_image:
                        if os.path.exists(image_path):
                            outcome["abort"] = f"{os.path.basename(image_path)} already exists.\n\nDisk unchanged."
                            return
                        os.rename(part, image_path)
                        part = None
                        outcome["image"] = image_path
                    names_img  = [f["name"].upper() for f in info["files"]]
                    names_pane = [n.upper() for _, n, _ in snap]
                    if len(names_img) != len(names_pane) or any(
                            a != b for a, b in zip(names_img, names_pane) if a.isascii() and b.isascii()):
                        outcome["abort"] = ("The disk doesn't match the directory shown "
                                            "(was it swapped?). Refresh and try again.\n\nDisk unchanged.")
                        return
                    if info["problems"]:
                        outcome["abort"] = (f"{verb} would lose data on this disk:\n\n• "
                                            + "\n• ".join(info["problems"]) + "\n\nDisk unchanged.")
                        return

                    # --- Step 3: read every file ---
                    # Each file goes to its own subfolder first, then becomes
                    # "NNN-<name>" (NNN = directory position), so two entries with the same
                    # name can't overwrite each other and the rescue folder keeps the order.
                    det3 = f"Step 3 of {steps} · Reading files"
                    for i, (blk, name, ftype) in enumerate(snap, 1):
                        if cancel_event.is_set():
                            return
                        set_msg(f"[{i}/{len(snap)}]  {cbm_display(name)}", fraction=(i - 1) / len(snap), detail=det3)
                        sub = tempfile.mkdtemp(dir=tmpdir)
                        err = cbm_read_file(DRIVE, name, sub, proc_store, ftype)
                        got = os.listdir(sub)
                        if not err and len(got) != 1:
                            err = f"expected one file, got {len(got)}"
                        if err:
                            outcome["abort"] = f"Reading {cbm_display(name)} failed:\n{err}\n\nDisk unchanged."
                            return
                        local_copy[i - 1] = os.path.join(tmpdir, f"{i:03d}-{got[0]}")
                        os.rename(os.path.join(sub, got[0]), local_copy[i - 1])
                        os.rmdir(sub)
                    if cancel_event.is_set():
                        return

                    # --- Step 4: scratch ---
                    det4 = f"Step 4 of {steps} · Rewriting the disk"
                    set_msg("Scratching all files…", detail=det4)
                    outcome["touched"] = True
                    _run_bounded(["cbmctrl", "command", DRIVE, "S:*"], 30)
                    # Wait for the drive to finish before any other bus request, then
                    # check it scratched every file ("01, FILES SCRATCHED,nn") and that a
                    # real directory listing (not an error) shows none left.
                    code, st = drive_status()
                    m = re.match(r'\s*01\s*,[^,]*,\s*(\d+)', st)
                    if code != 1 or not m or int(m.group(1)) != len(snap):
                        outcome["errors"].append(
                            f"Scratch didn't remove all {len(snap)} files (drive status: {st}). "
                            "Nothing was written.")
                        return
                    raw = cbm_dir(DRIVE)
                    _, _, left, free = parse_cbm_dir(raw)
                    if raw.startswith("Error") or free is None or left:
                        outcome["errors"].append(
                            f"After the scratch the directory shows {len(left)} file(s) "
                            f"or couldn't be read ({raw.splitlines()[-1] if raw else 'empty'}). "
                            "Nothing was written.")
                        return
                else:
                    det4 = f"Step 1 of 1 · Writing"

                # --- Step 4 (cont.): write FB files and the files back ---
                jobs = [(n.lower(), p) for n, p in fb_files]
                for entry in write_order:
                    # write_order holds the same tuples as snap; `is` keeps duplicates apart.
                    idx = next(i for i, e in enumerate(snap) if e is entry)
                    jobs.append((entry[1], local_copy.get(idx, "")))
                for i, (name, local) in enumerate(jobs, 1):
                    if cancel_event.is_set():
                        return
                    set_msg(f"[{i}/{len(jobs)}]  Writing {cbm_display(name)}", fraction=(i - 1) / len(jobs), detail=det4)
                    if not os.path.exists(local):
                        outcome["errors"].append(f"{cbm_display(name)}: local copy missing")
                        continue
                    err = cbm_write_file(DRIVE, local, name, proc_store)
                    if err:
                        outcome["errors"].append(f"Write {cbm_display(name)}: {err}")
                    else:
                        _push_dir()
            except Exception as e:
                outcome["errors"].append(f"Unexpected error: {e}")
            finally:
                failed_after_scratch = outcome["touched"] and (
                    outcome["errors"] or cancel_event.is_set())
                if failed_after_scratch:
                    base = os.path.splitext(image_path)[0]
                    rescue, n = base + "_rescue", 2
                    while os.path.exists(rescue):
                        rescue, n = f"{base}_rescue-{n}", n + 1
                    try:
                        os.makedirs(rescue)
                        # Drop the "NNN-" order prefix, so writing a rescued file back with
                        # Write gives it its original name; only a name clash gets "-2".
                        for f in sorted(os.listdir(tmpdir)):
                            src = os.path.join(tmpdir, f)
                            if f == "disk.d64" or not os.path.isfile(src):
                                continue
                            stem, ext = os.path.splitext(re.sub(r'^\d{3}-', '', f))
                            dest, n = os.path.join(rescue, stem + ext), 2
                            while os.path.exists(dest):
                                dest, n = os.path.join(rescue, f"{stem}-{n}{ext}"), n + 1
                            shutil.move(src, dest)
                        outcome["rescue"] = rescue
                        if part and os.path.exists(part):   # image not kept: keep it now
                            dest = image_path
                            n = 2
                            while os.path.exists(dest):
                                dest = f"{base}-{n}.d64"; n += 1
                            shutil.move(part, dest)
                            outcome["image"] = dest
                    except OSError as e:
                        outcome["errors"].append(f"Could not save rescue copy: {e} (temp: {tmpdir})")
                        tmpdir = None
                if part and os.path.exists(part):
                    try: os.unlink(part)
                    except OSError: pass
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                self.after(0, done)

        self._start_op(verb, run)

    def _sort_disk(self):
        if not self.cbm_pane._entries:
            self._set_status("Refreshing…")
            def _refresh():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda: self.cbm_pane._apply_dir(raw))
                self.after(0, self._sort_disk)
            self._start_op('Refreshing', _refresh)
            return
        entries = self.cbm_pane._entries

        dlg = tk.Toplevel(self)
        dlg.title("Sort disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        fb_var   = tk.BooleanVar(value=False)
        plat_var = tk.StringVar(value="C16/Plus4")

        fb_frame = ttk.LabelFrame(dlg, text="FB file browser", padding=(8, 4))
        fb_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Checkbutton(fb_frame, text="Add FB as first file",
                        variable=fb_var, command=lambda: _toggle()).grid(
            row=0, column=0, columnspan=2, sticky="w")

        plat_frame = ttk.LabelFrame(dlg, text="Platform", padding=(8, 4))
        plat_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        plat_radios = []
        for plat in FB_PATHS:
            rb = ttk.Radiobutton(plat_frame, text=plat, variable=plat_var, value=plat)
            rb.pack(side="left", padx=8)
            plat_radios.append(rb)

        def _toggle():
            s = "normal" if fb_var.get() else "disabled"
            for rb in plat_radios:
                rb.configure(state=s)
        _toggle()

        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 6))
        info_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        prgs  = [(b, n, t) for b, n, t in entries if t.upper().lstrip("*") == "PRG"]
        other = [(b, n, t) for b, n, t in entries if t.upper().lstrip("*") != "PRG"]
        ttk.Label(info_frame, font=("Menlo", 10), anchor="w",
                  text=f"Files on disk:  {len(entries)}\n"
                       f"PRG files:      {len(prgs)}\n"
                       f"Other (SEQ/USR/REL): {len(other)}").pack(fill="x")

        get_image = self._add_image_frame(dlg, row=3)

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 10))

        def _do_sort():
            fb_files = FB_PATHS[plat_var.get()] if fb_var.get() else []
            snap     = entries[:]
            keep, path = get_image()
            dlg.destroy()
            prgs  = sorted([e for e in snap if e[2].upper().lstrip("*").startswith("PRG")],
                           key=lambda x: x[1].lower())
            other = [e for e in snap if not e[2].upper().lstrip("*").startswith("PRG")]
            self._rewrite_disk("Sort", snap, prgs + other, fb_files, keep, path)

        ttk.Button(btn_frame, text="Sort", command=_do_sort).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _fbify_disk(self):
        if not self.cbm_pane._entries and self.cbm_pane.blocks_free() is None:
            self._set_status("Refreshing…")
            def _refresh():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda: self.cbm_pane._apply_dir(raw))
                self.after(0, self._fbify_disk)
            self._start_op('Refreshing', _refresh)
            return
        dlg = tk.Toplevel(self)
        dlg.title("FB-ify disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        plat_var = tk.StringVar(value="C16/Plus4")

        plat_frame = ttk.LabelFrame(dlg, text="Platform", padding=(8, 4))
        plat_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        for plat in FB_PATHS:
            ttk.Radiobutton(plat_frame, text=plat, variable=plat_var, value=plat,
                            command=lambda: _update_info()).pack(side="left", padx=8)

        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 6))
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        info_var    = tk.StringVar()
        verdict_var = tk.StringVar()
        ttk.Label(info_frame, textvariable=info_var,    font=("Menlo", 10),
                  justify="left").pack(anchor="w")
        ttk.Label(info_frame, textvariable=verdict_var, font=("Menlo", 10, "bold"),
                  justify="left").pack(anchor="w")

        # FB-ify scratches and rewrites every file, so it reads and checks the disk first;
        # an empty disk has nothing to lose.
        get_image = self._add_image_frame(dlg, row=2, enabled=bool(self.cbm_pane._entries))

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(4, 10))
        proceed_btn = ttk.Button(btn_frame, text="FB-ify", command=lambda: _do_fbify())
        proceed_btn.pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

        def _update_info():
            platform  = plat_var.get()
            fb_files  = FB_PATHS[platform]
            fb_blocks = sum(math.ceil(os.path.getsize(p) / 254) for _, p in fb_files)
            bf        = self.cbm_pane.blocks_free()
            entries   = self.cbm_pane._entries
            if bf is None:
                info_var.set("Disk info unavailable — refresh first.")
                verdict_var.set("")
                proceed_btn.configure(state="disabled")
                return
            n_files = len(fb_files)
            info_var.set(
                f"Files on disk:  {len(entries)}\n"
                f"Blocks free:    {bf}\n"
                f"FB cost:        {fb_blocks} blocks  ({n_files} file{'s' if n_files > 1 else ''})"
            )
            if fb_blocks <= bf:
                verdict_var.set("Fits.")
                proceed_btn.configure(state="normal")
            else:
                verdict_var.set(f"Won't fit — need {fb_blocks}, have {bf}.")
                proceed_btn.configure(state="disabled")

        def _do_fbify():
            fb_files = FB_PATHS[plat_var.get()]
            entries  = self.cbm_pane._entries[:]
            keep, path = get_image()
            dlg.destroy()
            self._rewrite_disk("FB-ify", entries, entries, fb_files, keep and bool(entries), path)

        _update_info()
        dlg.wait_window()


def _check_single_instance():
    import atexit, signal
    pid_file = os.path.expanduser("~/.diskmasher.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # raises if not running
            # Another instance is live — ask what to do
            probe = tk.Tk()
            probe.withdraw()
            kill_it = messagebox.askyesnocancel(
                "Diskmasher already running",
                f"Diskmasher (PID {old_pid}) is already running.\n\n"
                "Kill it and start fresh?",
                parent=probe)
            probe.destroy()
            if kill_it is None:
                return False      # Cancel — don't start
            if kill_it:
                try:
                    os.kill(old_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                return False      # No — don't start
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file — proceed

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.unlink(pid_file) if os.path.exists(pid_file) else None)
    return True

if __name__ == "__main__":
    if _check_single_instance():
        app = DiskMasher()
        app.mainloop()
