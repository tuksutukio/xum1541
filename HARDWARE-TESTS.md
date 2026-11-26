# Hardware test coverage

What has been run against real drives, on which drive, when and with what result. The offline suite (`tests/`)
covers logic; only these runs prove IEC behaviour. Update a cell whenever a hardware test runs, and mark cells
**stale** when the code behind an operation changes substantially (the result then says nothing about the current
code).

Cells: date (day-first is for chat; ISO here), ✓ worked, ✗ failed, ◐ worked with a caveat, — not run. Details and
timings below the table; tool quirks live in `opencbm-tools.md`, drive incidents in `CLAUDE.md`.

## Drives

| Short | Drive | Unit | Notes |
|---|---|---|---|
| 1541 | plain 1541, light beige | 8 | opencbm detects "1540 or 1541"; head knocks (no track-0 sensor) |
| 1571a | 1571 | 8 | 74 episode and writes failing after `d64copy` (2026-11-25); runs hot |
| 1571b | 1571 | 9 | power LED went dark during a read (2026-11-26) |
| 1570 | 1570 (×2) | 8 (hard-wired) | not yet tested |
| 1541-II | 1541-II (×2) | 8 | not yet tested |
| VIC-1541 | VIC-1541 | 8 | not yet tested |
| SD2IEC | several forms | — | not yet tested |

## Matrix

| Operation | 1541 | 1571a | 1571b |
|---|---|---|---|
| Detect | ✓ 2026-11-26 | ✓ | ✓ |
| Directory read | ✓ 2026-11-26 | ✓ 2026-11-25 | ✓ 2026-11-25 |
| 74 head recovery (bump, seek 18, `I`) | ◐ 2026-11-26 (ran with no disk in: knocked, 21) | — | ✓ 2026-11-25 |
| Disk check (tracks 18 + 36) | ✓ 2026-11-26 | — | ◐ 2026-11-25 (intermittent 30 s hang) |
| Read disk to image (35 tracks) | ◐ 2026-11-26 (one misread, 13/5) | ✓ 2026-11-25 | ◐ 2026-11-26 (2 of 5 reads stalled early, at tracks 5 and 2) |
| Write image, no format | ✓ 2026-11-26 | ✓ 2026-11-25 | ✓ 2026-11-26 (189 s) |
| Write image with pre-format | — | — | ✓ 2026-11-26 (format 17 s) |
| Read files (`cbmread`, PRG/SEQ/USR) | ✓ 2026-11-26 | — | ✓ 2026-11-25 |
| Write files (`cbmwrite`) | ✗ 2026-11-26 after a scratch without reset (74) | ✗ 2026-11-25 after `d64copy` without reset (74) | ✓ 2026-11-25 |
| Scratch, full disk (`S:*`) | ✓ 2026-11-26 (count right, 189 s) | — | ◐ 2026-11-26 (count unreliable) |
| Rename / Copy (drive's R and C) | — | — | ◐ 2026-11-26 (next writes 71 until a reset) |
| Make first | — | — | ✓ 2026-11-26 (shared engine, 12 s) |
| Sort (directory only) | ✓ 2026-11-26 | — | ✓ 2026-11-26 (12 s) |
| Restore a directory backup | ✓ 2026-11-26 | — | ✓ 2026-11-26 (8.1 s) |
| FB-ify (incl. replacing an FB) | ✓ 2026-11-26 | — | ✓ 2026-11-26 after a fix (replace got 74: head left on track 36) |
| Sort + FB + full image | ✓ 2026-11-26 | — | — |
| Rebuild (with format) | ✓ 2026-11-26 | — | ◐ 2026-11-26 (disk rebuilt right; the read-back stalled at track 2) |
| Erase | — | — | ✓ 2026-11-25 (write protect honoured) |
| Format (Disk → Format) | — | — | ✓ 2026-11-25 |
| Cancel during a disk check | ✓ 2026-11-26 | — | — |
| Cancel during an image read | — | ✓ 2026-11-25 (a 1571; unit not recorded) | |
| UI walk-through, Sort and FB-ify dialogs | ✓ 2026-11-26 (Antti) | — | — |

## Details

- **2026-11-26, 1541:** directory-only Sort 13.5 s; a full re-read afterwards was identical to the image from before
  except 18/1 and 18/4 (one checksum misread of 13/5, clean on two re-reads; MANNERHEIM read back
  byte-identical). Directory backup restore 9.9 s. FB-ify (C64) 27 s, `LOAD"*"` (`cbmread 8 '*'`) gave `fb64`
  byte-identical. Sort + C16/Plus4 FB replacing the old FB + full image 136 s (image 77 s), `LOAD"*"` gave
  `fb16`. Rebuild with format 301 s, every sector read back identical; it separated a cross-linked pair (NOTES
  and USERDATA shared 2/0). Write image without format 95 s.
- **2026-11-26, 1571b:** pre-format (`cbmforng`) 17 s, then Write image 189 s, about twice the 1541's time.
- **2026-11-25, 1571b:** Make first moved a file from the middle and one from the second directory sector to the
  top; `LOAD"*"` delivered it. Erase honoured write protect (opaque tape; clear tape doesn't block the optical
  sensor).
- **2026-11-26, 1571b (evening):** the disk check leaves the head on track 36; on a 35-track disk the DOS then
  answers 74 to plain DOS commands after a reset, so FB-ify's scratch of an old FB failed until it recovered the
  head first (fixed; the 1541 run had passed only because a full image read came between). Two `d64copy` reads
  stalled early (track 5 at 19:56, with the power LED dark for a while; track 2 at 20:13, the motor running and no
  stepping); a full read in between and one after both ran clean in 86–92 s. Rebuild's write was verified by a
  separate full read afterwards: all 13 files identical in type, name, data and order. Make first 12 s, Sort 12 s,
  FB-ify (C16/Plus4) 44 s, FB-ify (C64, replacing) 48 s, `LOAD"*"` gave `fb64`, restore 8.1 s.
