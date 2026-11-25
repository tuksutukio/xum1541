# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Diskmasher (repo folder `xum1541`): a Tkinter GUI for writing and reading real CBM floppies through an xum1541 USB
adapter. User docs, features and backlog: README.md. opencbm command-line behaviour, including tested quirks
(40-track rules, `d64copy` options): opencbm-tools.md; check it before changing how a tool is invoked.
The "UI layout" and "lockup" sections moved here on 2026-10-15 from Claude's per-project memory, so they're versioned.

## Running

- `./diskmasher.py` (shebang `/opt/homebrew/bin/python3.13`; stdlib only, no venv or requirements file).
- **Restarting it for Antti after a code change** (he asked, 2026-11-25): only when no drive operation runs (the
  log's last line is a finished command); `kill -TERM $(cat ~/.diskmasher.pid)` (that exact PID, never by name), then
  `nohup ./diskmasher.py >/dev/null 2>&1 & disown` from the repo; check the new PID in `~/.diskmasher.pid`.
- Offline tests (stdlib `unittest`, no hardware, ~40 s): `/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests`
  (one file: add `-p test_tools.py`). Run them after every change. `tests/_common.py` builds `.d64` images
  (`make_d64`) and puts fake opencbm tools (`tests/fake_opencbm.py`, with a simulated drive memory and job queue) on
  `PATH`; it also points `LOG_FILE` at a temp file. New opencbm behaviour learnt on hardware goes into the fakes as a
  scenario, with the real output text.
- `tests/test_vice.py` (~30 s of the run) checks Make first on an emulated C64: VICE with true drive emulation,
  `LOAD"*",8`/`,9` must load the moved file. It uses `../c64home/tools/vicetest.py` by relative path and VICE's
  `x64sc`, and is skipped when either is missing.
- The tests can't prove IEC behaviour: a change to a drive operation still needs a run against real hardware
  (xum1541 + a drive), so say what's untested and ask Antti to try it. No linter or build step.
- External tools: opencbm via Homebrew (`cbmctrl`, `cbmwrite`, `cbmread`, `d64copy`, `cbmforng`, `cbmformat`); VICE's
  `c1541` found at `/Applications/VICE*/bin/c1541` (image features are disabled when it's missing).
- State outside the repo: prefs (geometry, sash, checkbox defaults) in `~/.config/diskmasher/prefs.ini`; single-instance
  PID file `~/.diskmasher.pid`. The left pane starts at `~`.

## Architecture

Everything is in `diskmasher.py`; `fb/` holds the File Browser boot binaries that "FB-ify disk" and "Sort disk" write
to disk (`FB_PATHS` maps platform → files).

- **Classes:** `FilesystemPane` (left: local navigation, image create/browse via `c1541`), `CbmDirPane` (right: parses
  `cbmctrl dir` output), `DiskMasher` (the `tk.Tk` window; menus and every drive operation as a `_verb_noun` method).
- **Threading rule:** all opencbm/`c1541` calls run in `threading.Thread(daemon=True)`; UI updates go back to the Tk
  thread only via `self.after(0, …)`. Keep that pattern; Tk isn't thread-safe.
- **Two progress UIs, one Cancel sequence.** Both return a `proc_store` (`[None]`, the running Popen in slot 0) and
  a `cancel_event`, and both cancel through `_cancel_and_recover`:
  - `_make_task_dialog`: small modal, used for per-file operations (read, write, scratch, sort, FB-ify,
    read-to-image).
  - `_make_progress_window`: resizable log window for streaming tools (format, write image, read disk to image), fed by
    `_pty_run`, which runs the tool under a pty so opencbm prints its progress live.
- **One drive operation at a time:** every worker that touches the bus starts with `self._start_op(label, fn)`
  (from the Tk thread), never a bare `threading.Thread`. It counts running operations; while any runs, the drive
  menus and Write → are disabled, the dot at the right of the status bar flashes red (green when idle, grey when the last detect found no drive), the menu events (wrapped in
  `_when_free`) are refused, and `cbm_pane.refresh()` only sets `_refresh_pending`, run when the count is back to 0.
  Operations started during one (Cancel's recovery, a follow-up reset) count as part of it. `c1541` threads stay
  plain threads. Tested in `tests/test_busy.py`.
- **Drive → Reset and Cancel share `_reset_and_check`:** `cbmctrl reset`, 2 s settle, detect; drives found →
  drive menu updated + refresh (which recovers a 74 head via `cbm_dir`); no answer → replug USB; no drive → power/cable.
- **After a cancel the bus belongs to `_cancel_and_recover`** until it finishes; it refreshes the right pane itself.
  Workers must check `cancel_event` and skip any further `cbmctrl`/`cbm_dir` call, including the final refresh.
- **Never `subprocess.run(timeout=…)` for opencbm tools:** on timeout it SIGKILLs. Use `_run_bounded` (short
  commands) or `_stop_proc` (SIGTERM, 3 s grace, SIGKILL only if still alive).
- **`reset_and_settle()` before every `d64copy`, and `after_d64copy()` after it** (a `d64copy` right after
  `cbmread`/`cbmwrite` can stall; and `d64copy` can leave a 1571 at 2 MHz under its 1541-mode DOS, after which every
  file write fails with `74,DRIVE NOT READY,18,00` until a reset; see opencbm-tools.md). After a Cancel the cancel
  sequence resets instead. Judge `cbmread`/`cbmwrite` success with `_opencbm_error`, never by exit code alone.
- **Disk checking, no drive access:** `analyze_image()` walks the directory and every file's sector chain in an
  image (files beyond track 35, read errors inside files, unsupported types, data on 36–40 outside files,
  double-sided flag); `describe_image()` turns that into text.
- **Disk checking, on the drive:** `probe_disk()` reads tracks 18 and 36 (about 6 s) to learn the shape of the disk
  before a full read; `read_disk_to_file()` is the full read with progress, judged complete only by `d64copy`'s
  "N blocks copied." (it creates the file at full size up front).
- **Sort and FB-ify share `_rewrite_disk`:** check, full read + analysis, read every file, and only then scratch
  and rewrite. Anything risky stops it before the disk is touched; a failure after the scratch moves the read
  files to a `*_rescue` folder next to the image and keeps the image.
- **Drive command log:** every opencbm command (via `_run_bounded`, `stream_cmd`, `cbm_dir`, `cbm_detect`,
  `cbm_read_file`, `cbm_write_file`) is logged to `~/Library/Logs/diskmasher.log` with duration, outcome and last
  output lines; also Cancel, process stops and disk-check results. Read it first when a drive misbehaves.
- **Track 36 in `probe_disk`:** all 17 sectors OK = present; none = 35-track disk; some = `t36_partial`, a decayed
  40-track disk: reads use 40 tracks, Write image without pre-format refuses.
- **Erase (`erase_tracks`, drive code `drive/erase.s`):** the only code of ours that runs in the drive. Loaded to
  buffer 2 (`$0500`) with `cbmctrl upload` and run as an execute job (`$E0`) per track, after a bump job (`$C0`) so the
  controller knows the head position; the job slot is polled with `cbmctrl download`. A 1571 is put in 1541 mode first
  (`U0>M0`), because the routine exits through the 1541-mode ROM's `$F969`. `ERASE_CODE` holds the assembled bytes:
  change `erase.s` and the bytes together.
  Write protect is honoured (tested 2026-11-25, drive 9), but the 1571's sensor is optical: clear tape over the notch
  lets the light through, so the drive (DOS writes too) sees the disk as unprotected. Use opaque tape. The sense bit
  is `$1C00` bit 4 (0 = protected), readable with `cbmctrl download <unit> 0x1C00 1`.
- **Directory editing (`move_to_first_slot`, Make first):** sector read/write jobs (`$80`/`$90`) in slot 2 via
  `_read_sector`/`_write_sector`, after bump + seek 18 (the seek teaches the drive the disk ID; without it reads give
  29). Checks the directory against the pane listing, saves a JSON backup (`DIR_BACKUP_DIR`;
  `restore_directory_backup()` writes it back), writes only changed sectors, reads them back. The writes run inside
  the task dialog's `enter_critical()`, which turns Cancel off without a race: never leave half a directory.
  Tested 2026-11-25 on drive 9 (1571): a file from the middle and one from the second directory sector moved to
  the top, the rest kept their order; after `cbmctrl reset`, `cbmread <unit> '*'` (what `LOAD"*"` asks for)
  delivered the moved file; a SEQ was refused before any drive access.
- **74,DRIVE NOT READY can be the head, not the disk:** after a reset or disk change the DOS checks the drive by reading
  one header on whatever track the head is on (1541 ROM `LC63D` → `LD00E`); on a blank or unreadable track it sets a
  not-ready flag and answers 74 until `I`, even once the head is back on a good track (found 2026-11-25 after a
  cancelled erase). `recover_head()` (bump, seek 18, `I`) fixes it; `cbm_dir()` tries it once on a 74.
- **The selected drive is the module global `DRIVE`**, read at call time everywhere (never bind it as a default
  argument). Only `DiskMasher._select_drive` changes it (Drive menu, after Detect; saved in prefs), and it refuses
  while an operation window is open.

## UI layout

- Right pane has fixed width (weight=0); the left pane absorbs window resize (weight=1). Antti prefers this: the
  right pane's content is fixed-width by nature, and resizing felt unintuitive when both panes moved together.
- The left pane's filename column is dynamic, fitted to the longest name in the current directory (a fixed 42-char
  padding left too much empty space). Keep that approach if columns are added.
- Open: overall window/pane proportions (defaults 1100×660, sash ~halfway); revisit once there's more mileage.

## xum1541 lockup: evidence so far

- **Symptom:** when `d64copy` is killed mid-transfer (e.g. via Cancel), the xum1541's firmware state machine can get
  stuck. The 1541 itself is fine. `cbmctrl reset` doesn't help, because the stuck xum1541 can't process USB
  commands. Recovery needed a physical USB replug.
- **Correlation:** lockups came with cancelling during a *failing* operation (`d64copy` stuck on a bad sector,
  xum1541 mid-IEC-transaction, SIGKILL cutting it dead).
- **Cancel sequence (`_cancel_and_recover`, unified 2026-11-25):** SIGTERM, wait up to 3 s, SIGKILL only if still
  alive; `cbmctrl reset`; 2 s settle (drives re-initialise after a reset); one `cbmctrl detect` with a 5 s limit.
  Three outcomes: drives listed → "bus OK (8: 1571)" and refresh; detect hangs → "replug USB" error; detect
  returns but lists no drive → "check drive power and IEC cable". `cbmctrl status` was tried as the probe first and
  proved insufficient: it succeeded while the bus still wasn't usable.
- **`cbmctrl reset` *before* killing the process is impossible** (tested 2026-11-25, 1571): while `d64copy` holds the
  xum1541, a second opencbm process fails at once with `LIBUSB_ERROR_ACCESS`, and the transfer carries on unaffected.
  `d64copy` exits on SIGTERM (143) within a second, and reset + `detect` worked straight after.
- **Resilience observed 2026-06-30:** survived a drive power cycle and an IEC cable unplug/replug during an active
  read, without a relaunch. No true firmware lockup was reproduced; early incidents may have been timing-specific.

| Scenario | Expected | Tested |
|---|---|---|
| Cancel during normal operation | Bus OK | ✓ |
| Cancel during read on bad-sector disk | Bus OK or stuck | ✓ (Bus OK) |
| Cancel with IEC cable unplugged | Bus OK after IEC replug + settle | ✓ |
| Cancel with drive powered off | Bus OK | ✓ |
| SIGKILL `d64copy` from terminal (no graceful shutdown) | Most likely to trigger stuck state | ✗ |

- **Stuck with no cancel, 2026-11-24 (1571 on device 8):** on first use after weeks idle, `cbmctrl detect` and
  `cbmctrl status 8`–`11` hung (normally `detect` answers in about a second), while the xum1541 enumerated on USB
  and `cbmctrl reset` returned at once and reached the drive (motor spin). A USB replug (through a USB-A to USB-C
  adapter) together with a drive power cycle fixed it; we don't know which one did. Next time, replug USB alone
  first. Probe commands always with a timeout: a hung `cbmctrl` never returns by itself.
- **Stall after a file transfer, 2026-11-25 (1571):** a 40-track `d64copy` read started right after `cbmread`
  stalled at track 31; stopping it (SIGTERM after 120 s) left the drive unresponsive: the xum1541 answered at once,
  `detect` found no drive. A drive power cycle alone (no USB replug) fixed it, so the drive was the stuck part.
  Prevented since by `reset_and_settle()` before every `d64copy`.
- **Not the xum1541, 2026-11-25 ~02:50:** drive 8 (1571) stopped reading any disk: motor spins, activity LED flashes,
  then `74,DRIVE NOT READY` (per `drive-1571-users-guide-1985` p.110: no formatted disk in place). The bus and the
  drive's command channel were fine (`detect`, `status` OK), and the same disks read in a spare 1571 at device 9.
  Suspected overheating (disks covering the vents). Don't read this as an adapter lockup.

- **Writes fail after `d64copy`, 2026-11-25 (drive 8, 1571):** twice, a file write (`cbmwrite`) right after a `d64copy`
  (a read at 15:15, a Write image at 16:13) failed with `74,DRIVE NOT READY,18,00` while directory reads and `V` worked.
  The controller had rejected the BAM jobs with `$0F`; VIA 1 `$1801` bit 5 was still set from `d64copy` (believed to be the 1/2 MHz select; the service
  manual p.12 confirms such a line, not which pin drives it).
  `cbmctrl reset` fixed it at once. Not a drive fault and not about which drive formatted the disk (first suspected).
- **Next if it recurs:** try the SIGKILL-from-terminal case first. Real fix: a programmatic USB device reset via
  macOS IOKit, or check whether `xum1541cfg` has a reset command. Not yet investigated.
- Why it matters: old CBM hardware never needed power cycling; the xum1541 USB bridge is the weak link.

## Open items

@OPEN.md
