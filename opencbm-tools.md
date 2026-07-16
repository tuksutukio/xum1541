# opencbm Tools Reference

Version 0.4.99.104 — installed via Homebrew at `/opt/homebrew/bin/`

Also available: `cc1541` 4.2 mac binary at `/Volumes/ULTI/tools/pc/cc1541_bin-4.2/cc1541_mac`

---

## Disk Image Copy Tools

### `d64copy` — primary tool for d64/d71 ↔ physical drive

```
d64copy [OPTIONS] SOURCE TARGET
```

SOURCE/TARGET is either a `.d64`/`.d71` file or a drive number (e.g. `8`).

Key options:
- `-s, --start-track=N` — start track (default 1)
- `-e, --end-track=N` — end track (max 42 for 1541, 70 for 1571); use `-e 40` for 40-track images
- `-2, --two-sided` — d71 two-sided transfer; requires 1571
- `-d, --drive-type=TYPE` — `1541` or `1571` (also accepts `0` or `1`)
- `-b, --bam-only` — copy only allocated blocks; assumes SpeedDOS BAM for tracks 36–40
- `-B, --bam-save` — like `-b` but always copies entire directory track
- `-w, --warp` — warp mode (default unless transfer=original)
- `-t, --transfer=MODE` — `auto` (default), `original`, `serial1`, `serial2`, `parallel`
- `-i, --interleave=N` — interleave value (affects write speed)
- `-r, --retry-count=N` — retry count on error
- `-E, --error-map=WHEN` — `always`, `on_errors` (default), `never`

**Confirmed behaviour (tested):**
- Reads 40-track images correctly from physical disk (detects 768-block size automatically)
- Writing 40-track image back to the **same 40-track formatted disk** (without re-formatting) works perfectly
- Writing 40-track images to disks **pre-formatted shorter than 40 tracks** causes seeking errors on extended tracks
- Writing 40-track images to disks **pre-formatted to 40 tracks** with `cbmforng -x` works perfectly
- **Unconfirmed:** whether writing a 40-track image to a genuinely virgin/unformatted disk works
- **Rule: for 40-track images, always `cbmforng -x` before `d64copy` unless the disk is known to already be 40-track formatted**

**Image size detection:**
- Standard 35-track d64: 174,848 bytes (683 blocks × 256)
- 40-track d64: 196,608 bytes (768 blocks × 256)
- d71 (two-sided): 349,696 bytes

---

### `imgcopy` — for d81 (1581) and CBM 8050/8250 drives

```
imgcopy [OPTIONS] SOURCE TARGET
```

Selects image type automatically by file extension: `.d64`, `.d71`, `.d80`, `.d81`, `.d82`

Key options:
- `-e, --end-track=N` — max 77 (single side) or 154 (double side)
- `-1, --one-sided` — d80 for 8050
- `-2, --two-sided` — d82 for 8250/SFD-1001
- `-d, --drive-type=TYPE` — `1541`, `1571`, `1581`, `8050`, `8250`, `1001`, etc.
- Transfer options same as d64copy but only `auto` and `original` modes supported

Use `imgcopy` for **d81 images with the 1581** (once repaired).

---

### `d82copy` — for CBM 8250/8050 drives only

Subset of imgcopy for the 8250 family. Not relevant to current hardware.

---

## Formatting Tools

### `cbmforng` — preferred formatter ("next generation")

```
cbmforng [OPTIONS] DRIVE NAME,ID
```

- `-x, --extended` — format 40 tracks
- `-n, --no-bump` — skip head bump on start
- `-r, --retries N` — retry count on error
- `-c, --clear` — demagnetize first (recommended for new/blank disks)
- `-v, --verify` — verify each track after writing
- `-o, --original` — fill with original CBM pattern (0x4b, 0x01...) instead of zeroes
- `-s, --status` — show drive status after format

Example: `cbmforng -x 8 "diskname,01"` — format drive 8 to 40 tracks

**Note:** `cbmforng` also accepts `-e N` to format to a specific end track (tested: `-e 36` works).

---

### `cbmformat` — older formatter

Same options as `cbmforng` minus `--retries`. Use `cbmforng` instead.

---

## File Transfer Tools

### `cbmwrite` / `cbmread` / `cbmcopy`

All three are the same binary. Transfer individual files to/from a CBM drive.

```
cbmwrite [OPTIONS] DRIVE FILE...
cbmread  [OPTIONS] DRIVE FILE...
```

- `-w, --write` — PC → drive (default for cbmwrite)
- `-r, --read` — drive → PC (default for cbmread)
- `-o, --output=NAME` — target filename on CBM side
- `-f, --file-type` — CBM file type: D, P, S, U
- `-d, --drive-type=TYPE` — `1541`, `1570`, `1571`, `1581`
- `-a, --address=ADDRESS` — override load address
- `-R, --raw` — skip .p00/.t64 detection
- Transfer modes same as d64copy

**Not for whole-image transfers** — use `d64copy`/`imgcopy` for that.

---

## Drive Control

### `cbmctrl` — IEC bus control

```
cbmctrl [OPTIONS] ACTION [ACTION_ARGS]
```

Useful actions:
- `detect` — detect all drives on IEC bus
- `dir N` — list directory of drive N
- `status N` — get drive status
- `reset` — reset all drives on IEC bus
- `command N "CMD"` — send raw DOS command to drive N
- `download N addr len` — read drive memory
- `upload N addr file` — write to drive memory
- `change N` — wait for disk change in drive N

Flags:
- `-p, --petscii` — convert PETSCII↔ASCII
- `-r, --raw` — no conversion (default for `open`, `command`)

---

## Drive Measurement

### `cbmrpm41` — high-precision 1541 RPM measurement

```
cbmrpm41 [OPTIONS] DRIVE
```

- `-j, --job=N` — job type: 1=detailed (default), 2=track sync, 3=linear regression, 4=adjustment with moving average
- `-x, --extended` — measure 40-track disk
- `-b, --begin-track=N` / `-e, --end-track=N` — track range (max 42)
- `-s, --status` — show drive status after measurement
- `-r, --retries=N` — retries per track

Useful for drive calibration and health checks. 300 RPM is nominal.

---

## Firmware Tool

### `xum1541cfg` — xum1541 firmware management

```
xum1541cfg [-v] COMMAND [ARGS]
```

Commands:
- `update firmware.hex` — update xum1541 firmware
- `info firmware.hex` — print firmware file info
- `devinfo` — print info from connected device

Supported device types: `ZOOMFLOPPY` (default), `USBKEY`, `BUMBLEB`, `OLIMEX`, `TEENSY2`, `PROMICRO`, `PROMICRO_7406`

---

## cc1541 4.2 — d64/d71/d81 image manipulation (Mac binary)

Path: `/Volumes/ULTI/tools/pc/cc1541_bin-4.2/cc1541_mac`

**Image-only tool — does not talk to physical drives.**

Key capabilities:
- Create new d64/d71/d81 images
- Write local files into images (`-w localfile`)
- Set filename (`-f name`), filetype (`-T PRG/SEQ/USR/REL`)
- Extract files from images (`-X filename`, wildcards supported)
- Set disk name (`-n name`) and ID (`-i id`)
- 40-track BAM: `-4` (SpeedDOS) or `-5` (Dolphin DOS)
- Control sector interleave (`-S default`, `-s per-file`)
- Directory art, loop files, hidden BAM messages
- Transwarp fastloader file encoding (`-W`)
- Restore deleted/formatted files (`-R level 0–5`)
- Krill's loader hash computation (`-M`, `-m`)

Full docs: https://acoustic-velocity.com/cc1541

---

## Workflow Summary

### Write standard 35-track d64 to physical disk
```sh
d64copy image.d64 8
```

### Write 40-track d64 to physical disk
```sh
cbmforng -x 8 "diskname,id"
d64copy image.d64 8
```

### Write d71 (two-sided) to 1571
```sh
d64copy -2 -d 1571 image.d71 8
```

### Write d81 to 1581
```sh
imgcopy image.d81 8
```

### Read physical disk to image
```sh
d64copy 8 image.d64          # standard
d64copy -e 40 8 image.d64   # 40-track
d64copy -2 8 image.d71       # two-sided (1571)
```

### Detect image track count from file size
| Size (bytes) | Type |
|---|---|
| 174,848 | 35-track d64 (standard) |
| 196,608 | 40-track d64 |
| 349,696 | d71 (two-sided 1571) |
| 819,200 | d81 (1581) |
