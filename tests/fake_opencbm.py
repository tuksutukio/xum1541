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


def cbmctrl(args, sc):
    cmd = args[0]
    if cmd == "detect":
        print(sc.get("detect", " 8: 1541"))
    elif cmd == "reset":
        pass
    elif cmd == "status":
        print(_next(sc, "status", "00, ok,00,00"))
    elif cmd == "command":
        pass
    elif cmd == "dir":
        print(_next(sc, "dir", '0 ."empty" 01 2a\n664 blocks free.'))
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
    to copy to the destination (the last argument), the output text and the exit code."""
    start = args[args.index("-s") + 1] if "-s" in args else "full"
    entry = sc.get("d64copy", {}).get(start, {})
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
    entry = sc.get("cbmwrite", {})
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
            "cbmwrite": cbmwrite}[tool](args, sc)


if __name__ == "__main__":
    sys.exit(main())
