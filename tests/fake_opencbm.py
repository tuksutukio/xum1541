"""Stand-in for the opencbm tools (cbmctrl, cbmread, cbmwrite, d64copy) in offline tests.

Run as: python3 fake_opencbm.py <tool> <args...>. The test harness puts small wrapper
scripts named after the tools on PATH. State lives in $FAKE_CBM_DIR:

  scenario.json  what the tools answer (see _common.FakeTools.scenario)
  mem.bin        64 KB of simulated drive memory (cbmctrl upload/download)
  calls.log      one line per invocation: the tool and its arguments
  jobs.log       one line per job put in job slot 2: "<code hex> <track> <sector>"

Jobs complete at once: $E0 (execute) and $90 (write) answer $08 when the scenario says
the disk is write protected, everything else $01, unless scenario["job_results"]
overrides a code. With scenario["disk"] (a .d64 path), $80 reads that image's sector
into buffer 2 and $90 writes buffer 2 back into it.

With scenario["dos"] as well, a little of the DOS works on that image: `cbmctrl dir`
lists it (in cbmctrl's format, names in lower case), `cbmwrite` adds a directory entry
in the first free slot (data on free sectors from track 19 on; the BAM isn't kept),
and `cbmctrl command <unit> S0:A,B` scratches those names; the next `status` then
answers 01, FILES SCRATCHED.
"""
import json
import os
import sys
import time

STATE = os.environ["FAKE_CBM_DIR"]


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _text(path):
    with open(path) as f:
        return f.read()


def _scenario():
    with open(os.path.join(STATE, "scenario.json")) as f:
        return json.load(f)


def _save_scenario(sc):
    with open(os.path.join(STATE, "scenario.json"), "w") as f:
        json.dump(sc, f)


def _next(sc, key, default=""):
    """A scenario value; a list is consumed one item per call (the last item repeats)."""
    v = sc.get(key, default)
    if isinstance(v, list):
        item = v[0] if v else default
        if len(v) > 1:
            sc[key] = v[1:]
            _save_scenario(sc)
        return item
    return v


def _mem():
    p = os.path.join(STATE, "mem.bin")
    if not os.path.exists(p):
        return bytearray(65536)
    return bytearray(_bytes(p))


def _save_mem(m):
    with open(os.path.join(STATE, "mem.bin"), "wb") as f:
        f.write(m)


def _log(name, line):
    with open(os.path.join(STATE, name), "a") as f:
        f.write(line + "\n")


_SPT = [21] * 17 + [19] * 7 + [18] * 6 + [17] * 10


def _sector_job(code, track, sector, m, disk):
    """$80 read: disk sector -> buffer 2 ($0500); $90 write: buffer 2 -> disk sector.
    `disk` is a .d64 file, changed in place by writes."""
    off = (sum(_SPT[:track - 1]) + sector) * 256
    with open(disk, "r+b") as f:
        f.seek(off)
        if code == 0x80:
            m[0x0500:0x0600] = f.read(256)
        else:
            f.write(bytes(m[0x0500:0x0600]))


def _dos_dir_sectors(img):
    """[(offset of the sector in the image)] along the directory chain from 18/1."""
    out, t, sec = [], 18, 1
    while t:
        off = (sum(_SPT[:t - 1]) + sec) * 256
        out.append(off)
        t, sec = img[off], img[off + 1]
    return out


def _dos_slots(img):
    return [off + 32 * k for off in _dos_dir_sectors(img) for k in range(8)]


def _dos_listing(img):
    bam = (sum(_SPT[:17])) * 256
    name = img[bam + 0x90:bam + 0xA0].decode("latin-1").replace("\xa0", " ").lower()
    lines = [f'0 ."{name}" {img[bam + 0xA2:bam + 0xA4].decode("latin-1").lower()} 2a']
    kinds = {0: "del", 1: "seq", 2: "prg", 3: "usr", 4: "rel"}
    for o in _dos_slots(img):
        t = img[o + 2]
        if t == 0:
            continue
        n = img[o + 5:o + 21].rstrip(b"\xa0").decode("latin-1").lower()
        blocks = img[o + 30] | (img[o + 31] << 8)
        kind = ("" if t & 0x80 else "*") + kinds.get(t & 7, "???") + ("<" if t & 0x40 else "")
        lines.append(f'{blocks:<4} "{n}"'.ljust(24) + f" {kind}")
    lines.append("400 blocks free.")
    return "\n".join(lines)


def _dos_write(img, name, data, ftype):
    slots = [o for o in _dos_slots(img) if img[o + 2] == 0]
    if not slots:
        return "72,disk full,00,00"
    used = {(img[o + 3], img[o + 4]) for o in _dos_slots(img) if img[o + 2]}
    free = [(t, x) for t in range(19, 36) for x in range(_SPT[t - 1]) if (t, x) not in used]
    chunks = [data[i:i + 254] for i in range(0, max(len(data), 1), 254)]
    chain = free[:len(chunks)]
    for j, ((t, x), chunk) in enumerate(zip(chain, chunks)):
        off = (sum(_SPT[:t - 1]) + x) * 256
        blk = bytearray(256)
        blk[0:2] = bytes(chain[j + 1]) if j + 1 < len(chain) else bytes([0, len(chunk) + 1])
        blk[2:2 + len(chunk)] = chunk
        img[off:off + 256] = blk
    o = slots[0]
    img[o + 2] = ftype
    img[o + 3], img[o + 4] = chain[0]
    img[o + 5:o + 21] = name.upper().encode("latin-1")[:16].ljust(16, b"\xa0")
    img[o + 30], img[o + 31] = len(chain) & 0xFF, len(chain) >> 8
    return None


def _dos_scratch(img, names):
    n = 0
    for o in _dos_slots(img):
        if img[o + 2] and img[o + 5:o + 21].rstrip(b"\xa0").decode("latin-1") in names:
            img[o + 2] = 0
            n += 1
    return n


def _image(sc):
    return bytearray(_bytes(sc["disk"]))


def _save_image(sc, img):
    with open(sc["disk"], "wb") as f:
        f.write(img)


def cbmctrl(args, sc):
    cmd = args[0]
    if cmd == "detect":
        print(sc.get("detect", " 8: 1541"))
    elif cmd == "reset":
        pass
    elif cmd == "status":
        pending = os.path.join(STATE, "status.pending")
        if os.path.exists(pending):
            print(_text(pending))
            os.remove(pending)
        else:
            print(_next(sc, "status", "00, ok,00,00"))
    elif cmd == "command" and sc.get("dos") and _head74():
        if args[2] == "I":
            _head_ok()
        else:
            with open(os.path.join(STATE, "status.pending"), "w") as f:
                f.write("74,drive not ready,00,00")
    elif cmd == "command":
        if sc.get("dos") and args[2].startswith("S0:"):
            img = _image(sc)
            n = _dos_scratch(img, args[2][3:].split(","))
            _save_image(sc, img)
            with open(os.path.join(STATE, "status.pending"), "w") as f:
                f.write(f"01, files scratched,{n:02d},00")
    elif cmd == "dir" and sc.get("dos") and "dir" not in sc:
        print("74,drive not ready,00,00" if _head74() else _dos_listing(_image(sc)))
    elif cmd == "dir":
        v = _next(sc, "dir", '0 ."empty" 01 2a\n664 blocks free.')
        if isinstance(v, dict):                 # {"out": ..., "rc": ...}
            if v.get("out"):
                print(v["out"])
            return v.get("rc", 0)
        print(v)
    elif cmd == "upload":
        addr, path = int(args[2], 0), args[3]
        data = _bytes(path)
        m = _mem()
        m[addr:addr + len(data)] = data
        if addr == 0x0002 and data and data[0] >= 0x80:
            code, track, sector = data[0], m[0x000A], m[0x000B]
            _log("jobs.log", f"{code:02x} {track} {sector}")
            res = sc.get("job_results", {}).get(f"{code:02x}")
            if res is None:
                res = 0x08 if code in (0xE0, 0x90) and sc.get("write_protected") else 0x01
            if res == 0x01 and code in (0x80, 0x90) and sc.get("disk"):
                _sector_job(code, track, sector, m, sc["disk"])
            m[0x0002] = res
        _save_mem(m)
    elif cmd == "download":
        addr, count, path = int(args[2], 0), int(args[3], 0), args[4]
        with open(path, "wb") as f:
            f.write(bytes(_mem()[addr:addr + count]))
    else:
        print(f"fake cbmctrl: unknown command {cmd}", file=sys.stderr)
        return 1
    return 0


def d64copy(args, sc):
    """Answers per start track (-s N), or "full" without -s. The entry names an image file
    to copy to the destination (the last argument), the output text and the exit code.
    With scenario["dos"] and no entry, it works on scenario["disk"] (a 35-track disk):
    writing an image (`d64copy <image> <unit>`) copies it onto the disk, a full read copies
    the disk out, track 18 reads from it, and track 36 isn't there."""
    start = args[args.index("-s") + 1] if "-s" in args else "full"
    entry = sc.get("d64copy", {}).get(start)
    if entry is None and sc.get("dos"):
        if args[-1].isdigit():                       # write: d64copy <image> <unit>
            with open(args[-2], "rb") as f:
                data = f.read()[:174848]
            if sc.get("write_protected"):
                print("[Fatal] 26,WRITE PROTECT ON,18,00")
                return 0
            with open(sc["disk"], "r+b") as g:
                g.write(data)
            print("683 blocks copied.")
            return 0
        if start == "36":
            # The head stays on track 36; on this 35-track disk the DOS then answers 74
            # until an `I` (CLAUDE.md "74,DRIVE NOT READY can be the head").
            open(os.path.join(STATE, "head74"), "w").close()
            print("[Fatal] 21,READ ERROR,36,00\n0 blocks copied.")
            return 0
        _head_ok()
        with open(sc["disk"], "rb") as f, open(args[-1], "wb") as g:
            g.write(f.read())
        print("19 blocks copied." if start == "18" else "683 blocks copied.")
        return 0
    entry = entry or {}
    if entry.get("image"):
        with open(entry["image"], "rb") as f, open(args[-1], "wb") as g:
            g.write(f.read())
    print(entry.get("out", ""))
    return entry.get("rc", 0)


def cbmread(args, sc):
    entry = sc.get("cbmread", {})
    if entry.get("creates"):
        with open(entry["creates"], "wb") as f:      # relative to cwd, like the real tool
            f.write(b"\x01\x08")
    print(entry.get("out", ""))
    return entry.get("rc", 0)


def cbmwrite(args, sc):
    if sc.get("dos"):
        name = args[args.index("-o") + 1] if "-o" in args else os.path.basename(args[-1])
        ftype = {".seq": 0x81, ".usr": 0x83}.get(os.path.splitext(args[-1])[1].lower(), 0x82)
        img = _image(sc)
        err = _dos_write(img, name, _bytes(args[-1]), ftype)
        if err:
            print(f"[Fatal] could not open file for writing: {err}")
            return 0
        _save_image(sc, img)
        print("[Info] 00, OK,00,00")
        return 0
    entry = sc.get("cbmwrite", {})
    print(entry.get("out", ""))
    return entry.get("rc", 0)


def _head74():
    return os.path.exists(os.path.join(STATE, "head74"))


def _head_ok():
    if _head74():
        os.remove(os.path.join(STATE, "head74"))


def cbmforng(args, sc):
    """Formats nothing (the image write that follows rewrites every sector anyway); its
    answer can be set with scenario["cbmforng"] = {"out", "rc"}."""
    entry = sc.get("cbmforng", {})
    print(entry.get("out", ""))
    return entry.get("rc", 0)


def main():
    tool, args = sys.argv[1], sys.argv[2:]
    _log("calls.log", " ".join([tool, *args]))
    sc = _scenario()
    hang = sc.get("hang", {}).get(tool)
    if hang:
        time.sleep(hang)
    return {"cbmctrl": cbmctrl, "d64copy": d64copy, "cbmread": cbmread,
            "cbmwrite": cbmwrite, "cbmforng": cbmforng}[tool](args, sc)


if __name__ == "__main__":
    sys.exit(main())
