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

**File types with `cbmread`/`cbmwrite`/`cbmctrl command` (tested 2026-11-25, drive 9, 1571):**
- `cbmread <unit> name` opens the file as PRG: a SEQ or USR file fails with `64, FILE TYPE MISMATCH`. Put the type
  in the name (`name,s`, `name,u`, `name,p`); the local file then gets `.seq`/`.usr`/`.prg`. A wrong type
  (`seqfile,p`) gives the same 64.
- `cbmwrite` takes the type from the local file's extension when `-f` isn't given (`x.seq` → `x,s,w`, also with
  `-o newname`); `-f S`/`-f U` set it explicitly. An existing name fails with `63, FILE EXISTS` (as `[Fatal]`).
- `cbmctrl command` sends its text raw (no PETSCII conversion): file names in a DOS command must be uppercase
  ASCII (`S0:SEQTEST`). Lowercase names match nothing, and the drive still answers `01, FILES SCRATCHED,00`.

**`cbmread` leaves a 1571 at 2 MHz too** (2026-11-26, drive 9): VIA 1 `$1801` read `$99` after a reset and `$38`
after one `cbmread` (bit 5, believed to be the 1/2 MHz select, set). In that state 15 status reads and 5 directory
reads all came back clean, and DOS commands run about twice as fast (a 13-file scratch: 66 s, against 170 s after a
reset).

**The 1571's `FILES SCRATCHED` count is unreliable for long scratches** (2026-11-26, drive 9, at 1 and 2 MHz alike):
1, 2, 3 and 4 files reported `01`–`04` correctly (up to 31 s), but 6 large files reported `22`, 10 files `42` and
13 files `5;` (not even a decimal digit; the drive converts the count with SED/ADC and prints the nibbles,
anatomy-of-the-1541 p.216). The scratch itself was complete and the BAM consistent every time. Suspected: the
counter (`$86`, anatomy p.144) is reused during long scratches; not checked in the 1571 ROM. Check the directory, not
the count. **A 1541 counts right:** the same 13-file `S:*` on a plain 1541 (2026-11-26, device 8) answered
`01, FILES SCRATCHED,13,00`, in 189 s.

**Writes after a scratch fail until a reset, on a 1541 too** (2026-11-26, device 8, during Sort): `d64copy`,
reset, 13 `cbmread`s, `S:*` (status 01, directory empty, 664 blocks free), then every `cbmwrite` failed within a
second with `74,DRIVE NOT READY,18,00`. A 1541 has no 2 MHz mode, so the 1571's 2 MHz state (below) can't be the
whole story. Hypothesis, unchecked: the fast-transfer code `cbmread` uploads into drive RAM leaves the DOS
unable to write until a reset. Sort now resets after the scratch.

**After the drive's own Copy or Rename (`cbmctrl command <unit> C0:NEW=OLD` / `R0:NEW=OLD`, 2026-11-26, drive 9, 1571):**
the commands themselves worked (types kept), but the next `cbmwrite`s failed with `71,DIR ERROR,01,04`, even after
`I`. The BAM on the disk was consistent (counts matched bitmaps). A `cbmctrl reset` fixed it at once. One failed
write left a 1-block entry whose sector wasn't counted in the BAM (free count unchanged); a `V` (validate) cleans
that up. Not yet known which of the two commands causes it; Diskmasher resets after both.
**Both cause it, on a 1541 too** (2026-11-26, plain 1541 at 8, freshly rebuilt disk with a consistent BAM): after
`C0:NOTES3=NOTES` and after `R0:NOTES4=NOTES3` (each `00, OK`), the next `cbmwrite` ended with `71,DIR ERROR`; each
failed write left a 1-block entry whose block the BAM didn't count, so the next save could reuse it: a cross-link
in the making (likely how NOTES and USERDATA came to share 2/0). After a `cbmctrl reset` the write worked and was
counted. Guess, unchecked: the DOS keeps its updated BAM in drive RAM after C/R, while cbmwrite's drive code
works from the disk's copy.

**Disk facts seen with these tools (2026-11-25, 1571 in 1541 mode):**
- The disk ID is in two places: the BAM (18/0 `$A2–$A3`, what a directory listing shows) and every sector header,
  written only by a format. `d64copy` writes the BAM, not the headers. To see the header ID, send `I` and read the
  drive's zero page: `cbmctrl download <unit> 0x12 2` (ID learnt from the headers; `$16–$17` is the last header
  read; anatomy-of-the-1541 p.111). Write image's pre-format uses the image's own ID, so both match: checked
  2026-11-25 on drive 9, an image with ID `7Q` gave `7Q` in both, where the old dialog would have formatted `01`.
- `cbmforng -x` formats 40 tracks and writes a SpeedDOS-style extended BAM at `$C0–$D3` of 18/0 (tracks 36–40,
  4 bytes each: free count + bitmap); the drive's DOS still reports **664 blocks free**, so a directory listing
  never reveals a 40-track disk.
- Standard DOS refuses a file whose chain reaches track 36: `67,ILLEGAL TRACK OR SECTOR,36,00`.
- A decayed 40-track disk (2026-11-25, spare 1571 at device 9): `-s 36 -e 36 -r 0` found every sector header but
  only 8 of 17 sectors read; the rest gave `read error: 24/xx: 5` (data checksum), then `[Warning] giving up...`
  and `8 blocks copied.`, **exit 0**. So "N blocks copied" with N below the expected count is a partial read, not
  a failure of the tool; the bad sectors are in the error table.
- The disk check on that disk sometimes took 30+ s in the app (one time track 36 timed out), but never from the
  command line: not after a directory listing, not after a fresh insert (8–10 s each, 2026-11-25). Unexplained;
  diskmasher's drive command log is there to catch it.
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
- `detect` — detect all drives on IEC bus. The model can be several words: a light beige 1541
  (2026-11-26) shows as ` 8: 1540 or 1541`, since opencbm can't tell the two apart by ROM
  Two drives set to the same unit answer together and garble the ROM reads: a 1541 and a 1571 both on 8
  gave ` 8: *unknown*, footprint=<@B@@>` (2026-11-26). An unknown model with a footprint hints at a clash.

**A second device on the bus halves `d64copy`'s speed** (2026-11-26, 1541 at 8, same disk): 158 s for a full
35-track read with a 1571 also on the bus (twice), 87 s with the 1571 unplugged (twice); the 1571 at 9 took
92 s with the 1541 on the bus (twice) and 52 s alone (three times). The disks swapped between drives made no
difference. `d64copy --help`:
`serial2` "won't work if more than one device is connected to the IEC bus"; `auto` (the default) "tries to
determine the best option". So timings are only comparable with the same set of devices on the bus. Whether
`auto` counts devices, and whether a wrong pick explains the early stalls seen with two drives, is unverified.
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

## d64copy leaves a 1571 at 2 MHz (2026-11-25)

After `d64copy` (read or write) on a 1571, the drive can stay at 2 MHz while its DOS is in 1541 mode: VIA 1 `$1801` bit 5
stays set (believed to be the 1/2 MHz select; `drive-1571-service-manual-1986` p.12 documents a 1 MHz/2 MHz
select line but not which VIA pin drives it). Directory reads, `V` and further `d64copy` runs still work, but opening a file for writing fails:
`cbmwrite` prints `[Fatal] could not open file for writing: 74,DRIVE NOT READY,18,00` and exits 0. The job queue
shows the controller returned `$0F` for the BAM jobs. `cbmctrl reset` clears it (bit 5 back to 0), and the same
write then succeeds. Diskmasher resets after every `d64copy` (`after_d64copy`).
