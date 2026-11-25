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
- Reading to an image creates the file **at full size before reading**: a read stopped at 10% left 174848 bytes, so
  the file size never proves a complete read. A complete read exits 0 and ends with `683 blocks copied.` (1571 drive,
  35-track disk, 48 s; 2026-11-25)
- Exits on SIGTERM (code 143) within a second, mid-read; the bus was fine afterwards (reset + `detect` OK)
- While it runs it holds the xum1541: any other opencbm command fails at once with `LIBUSB_ERROR_ACCESS`
- `-s T -e T` reads a single track in about 3 s (1571), writing a full-size image with only that track filled; with
  `-e 40` the image is 40-track size even if only track 36 was read
- A track beyond 35 can hold **leftover data** from an earlier 40-track use: a disk formatted to 35 tracks later
  doesn't touch tracks 36–40, so they still read cleanly (seen on a spare disk, 2026-11-25). A readable track 36
  doesn't mean any file uses it, but a 35-track image silently drops that data.

- **Reset before every `d64copy`.** Right after a `cbmread` or `cbmwrite`, the next `d64copy` can stall: a track-36
  read hung every time (only the first attempt; a second one worked), and a full 40-track read stalled at track 31
  and, when stopped, left the 1571 unresponsive until a power cycle. `cbmctrl reset` + 2 s first fixed both, every
  time (2026-11-25, 1571). `cbmctrl dir` and a scratch don't cause it. diskmasher does this in `reset_and_settle()`.

**Disk facts seen with these tools (2026-11-25, 1571 in 1541 mode):**
- `cbmforng -x` formats 40 tracks and writes a SpeedDOS-style extended BAM at `$C0–$D3` of 18/0 (tracks 36–40,
  4 bytes each: free count + bitmap); the drive's DOS still reports **664 blocks free**, so a directory listing
  never reveals a 40-track disk.
- Standard DOS refuses a file whose chain reaches track 36: `67,ILLEGAL TRACK OR SECTOR,36,00`.
- On a disk never formatted past track 35, `d64copy -s 36 -e 36 -r 0` gives up quickly instead of hanging: diskmasher's
  whole disk check (reset + tracks 18 and 36) took about 7 s, as on a 40-track disk, and reported a standard
  35-track disk. Write image then refused a 40-track image without pre-format, as intended.
- **`cbmread` exits 0 on a failed read** and only prints `[Warning] file copy ended with error status: …` and
  `[Warning] error reading NAME`, without creating the file. Treat any `[Warning]`/`[Fatal]` line as failure.
  `cbmwrite` prints `[Fatal] could not open file for writing: 63, FILE EXISTS` for a duplicate name.
  Not every `[Warning]` is a failure: `could not identify drive, using no turbo` (right after a drive power cycle)
  still transfers the file.
- Right after a drive power cycle, the first `cbmctrl detect` returned no drive at all; a later one found it.
- **`cbmctrl command` returns as soon as the command is sent**; the drive works on afterwards (`V0`: command
  returned in 0.1 s, the following `cbmctrl status` waited 2.7 s). Read the status before the next bus request,
  and check it: `S:*` answers `01, FILES SCRATCHED,nn`. A Sort that listed the directory straight after `S:*`
  later wrote into files that were never scratched (2026-11-25; most likely this race, not proven).
- `V0` (validate) removed a splat file left by an interrupted `cbmwrite` and returned its 20 allocated blocks;
  the directory had listed it as 0 blocks. Don't scratch splat files; validate instead.

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
