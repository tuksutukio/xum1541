# Hardware test coverage

What has been run against real drives, on which drive, when and with what result. The offline suite (`tests/`)
covers logic; only these runs prove IEC behaviour. Update a cell whenever a hardware test runs, and mark cells
**stale** when the code behind an operation changes substantially (the result then says nothing about the current
code).

Cells: date (day-first is for chat; ISO here), ✓ worked, ✗ failed, ◐ worked with a caveat, — not run. Details and
timings below the table; tool quirks live in `opencbm-tools.md`, drive incidents in `CLAUDE.md`.

## Strategy (draft, 2026-11-26)

- **One drive on the bus, always** (Antti, 2026-11-26: "we should not test on a throttled bus"). A second device
  makes `d64copy` ~75 % slower (opencbm-tools.md) and may cause stalls; Diskmasher advises users the same way.
  Pinning down the bottleneck (xum1541 firmware, opencbm's macOS port, `auto` transfer choice) isn't ours to fix;
  report it upstream some day.
- **Give Antti feedback while tests run:** say what a run will do and in what order before starting it (he only hears
  the drive), and suggest `tail -f ~/Library/Logs/diskmasher.log` in another tab for a live view of every command.
- **Before any test that writes, list the disk on its own** and confirm it's the agreed test disk.
- **Worn, reused disks first** (what users have); a mint disk as the reference when a result might be the disk.
- **Default test disk image:** `/Users/tuksu/tmp/diskname_backup.d64` (Antti, 2026-11-26): DISKNAME, ID 01, 13 files
  (PRG, SEQ, USR, 14 blocks free), read from the test disk on 2026-11-26. Write it to a test disk to start from a
  known state. It also holds a cross-link (NOTES and USERDATA share 2/0), useful for testing cross-link detection.
  Local only: the files aren't ours to publish.
- **Unattended long runs** while Antti is away (his offer): one drive, one test disk, a script that loops read-only
  full reads (and, on a sacrificial disk, write/read-back cycles), each with a no-progress watchdog, logging
  drive, uptime, duration, stalls and retries; a summary table at the end. Read-only loops can run on any disk.
- **Minimise cable changes** (Antti, 2026-11-26: new-stock 6-pin DIN cables have flimsy connectors): batch all the
  tests planned for a drive into one session on the bus, swap disks rather than drives where that answers the
  question, and plan the order of drives ahead.
- **Swap one thing at a time** when hunting a fault: the disk between drives, the drive on a unit (DIP switches),
  the devices on the bus. Two of each kind make the comparison.
- **Update the matrix** after every hardware test, and mark cells stale when the code behind them changes.
- **Next candidates:** drive 9 alone over a long run (does it still stall?); Erase and Make first on the
  1541; the VIC-1541 (ROM via a dump compared with VICE's ROM files); the 1570s and 1541-IIs; SD2IEC.

## Drives

| Short | Drive | Unit | Notes |
|---|---|---|---|
| 1541 | plain 1541, light beige | 8 | opencbm detects "1540 or 1541"; head knocks (no track-0 sensor) |
| 1571a | 1571 | 8 | 74 episode and writes failing after `d64copy` (2026-11-25); runs hot |
| 1571b | 1571 | 9 | power LED went dark during a read (2026-11-26) |
| 1570 | 1570 (×2) | 8 (hard-wired) | not yet tested |
| 1541-II | 1541-II (×2) | 8 | not yet tested |
| VIC-1541 | VIC-1541 (black front, no lever) | 8 | detects as "1540 or 1541", `73,CBM DOS V2.6 1541`; ROM: lower chip = `325302-01`, upper = a `901229` revision 2 bytes off VICE's `-05` (`$FEE6` `$00`/`$3E`, `$FFE0` `$E8`/`$AA`; two identical downloads; dump kept locally only) |
| SD2IEC | several forms | — | not yet tested |

## Matrix

| Operation | 1541 | 1571a | 1571b | VIC-1541 |
|---|---|---|---|---|
| Detect | ✓ 2026-11-26 | ✓ | ✓ | ✓ 2026-11-26 |
| Directory read | ✓ 2026-11-26 | ✓ 2026-11-25 | ✓ 2026-11-25 | — |
| 74 head recovery (bump, seek 18, `I`) | ◐ 2026-11-26 (ran with no disk in: knocked, 21) | — | ✓ 2026-11-25 | — |
| Disk check (tracks 18 + 36) | ✓ 2026-11-26 | — | ◐ 2026-11-25 (intermittent 30 s hang) | ◐ 2026-11-26 (track 36 hung 30 s once, alone on the bus) |
| Read disk to image (35 tracks) | ◐ 2026-11-26 (one misread, 13/5) | ✓ 2026-11-25 | ◐ 2026-11-26 (2 of 5 reads stalled early, at tracks 5 and 2) | ✓ 2026-11-26 (83 s, identical) |
| Write image, no format | ✓ 2026-11-26 | ✓ 2026-11-25 | ✓ 2026-11-26 (189 s) | ◐ 2026-11-26 (102 s clean; later one slowed to ~6× and was stopped) |
| Write image with pre-format | — | — | ✓ 2026-11-26 (format 17 s) | — |
| Read files (`cbmread`, PRG/SEQ/USR) | ✓ 2026-11-26 | — | ✓ 2026-11-25 | — |
| Write files (`cbmwrite`) | ✗ 2026-11-26 after a scratch without reset (74) | ✗ 2026-11-25 after `d64copy` without reset (74) | ✓ 2026-11-25 | — |
| Scratch, full disk (`S:*`) | ✓ 2026-11-26 (count right, 189 s) | — | ◐ 2026-11-26 (count unreliable) | — |
| Rename / Copy (drive's R and C) | ◐ 2026-11-26 (next writes 71 until a reset, as on the 1571) | — | ◐ 2026-11-26 (next writes 71 until a reset) | — |
| Make first | ✓ 2026-11-26 (11.4 s, `LOAD"*"` checked) | — | ✓ 2026-11-26 (shared engine, 12 s) | ✓ 2026-11-26 (13 s) |
| Sort (directory only) | ✓ 2026-11-26 | — | ✓ 2026-11-26 (12 s) | ✓ 2026-11-26 (13.4 s) |
| Restore a directory backup | ✓ 2026-11-26 | — | ✓ 2026-11-26 (8.1 s) | — |
| FB-ify (incl. replacing an FB) | ✓ 2026-11-26 | — | ✓ 2026-11-26 after a fix (replace got 74: head left on track 36) | ✓ 2026-11-26 (`LOAD"*"` checked) |
| Sort + FB + full image | ✓ 2026-11-26 | — | — | — |
| Rebuild (with format) | ✓ 2026-11-26 | — | ◐ 2026-11-26 (disk rebuilt right; the read-back stalled at track 2) | ✗ 2026-11-26 (write after the format: header not found on track 1, head banging) |
| Erase | ✓ 2026-11-26 (whole disk, 29 s; native 1541 ROM) | — | ✓ 2026-11-25 (write protect honoured) | — |
| Format (Disk → Format) | ✓ 2026-11-26 (`cbmforng`, 18 s, after Erase) | — | ✓ 2026-11-25 | — |
| Cancel during a disk check | ✓ 2026-11-26 | — | — | — |
| Cancel during an image read | — | ✓ 2026-11-25 (a 1571; unit not recorded) | | — |
| UI walk-through, Sort and FB-ify dialogs | ✓ 2026-11-26 (Antti) | — | — | — |

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
- **2026-11-26, round 1 (bus):** same two disks, swapped between the 1541 and drive 9: full reads 158 s (1541) and
  92 s (1571) with both drives on the bus, 87 s and 52 s with the drive alone; no stalls in 9 reads. Timings
  above from before ~19:54 were with the 1541 alone; from then until 20:50 with both drives on the bus.
- **2026-11-26, 1541 alone on the bus (late evening):** Make first 11.4 s, `LOAD"*"` gave RELOADED byte-identical.
  Copy and Rename each worked, but the next `cbmwrite` ended with `71,DIR ERROR` and left an entry the BAM didn't
  count; after a reset writes worked (opencbm-tools.md). Erase of tracks 1–40: 29 s; afterwards track 18 was
  unreadable and the directory answered 74, as for a never-formatted disk; `cbmforng` then formatted it in 18 s
  (DISKNAME, 01, 664 blocks free).
- **2026-11-26, VIC-1541 alone on the bus (21:40–22:08):** ROM: see Drives. Write image 102 s and read 83 s,
  byte-identical; Make first 13 s, Sort 13.4 s, FB-ify 73 s (30 s of it a track-36 hang in the disk check, so
  that hang isn't a two-device effect), `LOAD"*"` gave `fb64`. Rebuild: read and `cbmforng` (14.9 s) fine, then
  the image write found no header on track 1 (write errors code 2 on every sector, constant head banging) and was
  stopped after 7 min; minutes later the disk read cleanly (tracks 1, 10, 18, 30 in full, 664 blocks free). A
  plain write after that crawled (622/683 blocks in 10 min, ~6× slower than at 21:42); Claude stopped it, taking
  it for a hang (it was progressing). The drive got worse the longer it worked: suspect heat or a dirty head,
  unproven. Next: cool down, maybe clean the head, then a read-only full read.
- **2026-11-26, VIC-1541 after reseating a loose IEC cable (22:20–22:43):** a full read ran at normal speed with
  12 checksum errors on track 1 (where the failing writes had worked); a write onto its own `cbmforng` format
  failed again on track 1 (header not found, banging); after a DOS `N:` format (79 s) a write ran without errors
  but at ~4× slower until stopped at track 20. `cbmforng` had measured 297.3 RPM. Parked (OPEN.md).
- **2026-11-26/27, 1541 alone on the bus, long run (23:39–00:21):** 30 read-only full reads of a healthy disk in a
  row, each with a 45 s no-progress watchdog: 30 × OK, 683/683, 82–83 s each, no stall, all 30 images
  byte-identical (one SHA-1). The first unattended-style run; supports "two drives on the bus" as the cause of the
  stalls seen earlier (drive 9 alone remains to be run).
