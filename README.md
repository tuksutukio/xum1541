# Diskmasher

A macOS GUI for mastering Commodore floppy disks via an xum1541 USB adapter. **How to use it: [USER-GUIDE.md](USER-GUIDE.md).** The goal is to reduce the mechanical effort of getting software from a digital collection onto physical media — minimum clicks between "I want this on a disk" and "disk is ready."

## What it is (and isn't)

Diskmasher is a split-pane tool: the left pane navigates your local filesystem, the right pane shows the directory of a connected CBM drive. You pick files from one side and push them to the other.

It is **not** a general-purpose file manager. The left pane does only what a disk project needs without leaving the app: create a working folder or a disk image, and move test and intermediate files or folders to the Trash. Copying, moving, renaming and the rest of local file management stay with the Finder or your shell. The focus is on the CBM side of the workflow: writing floppies, reading them back, building images, and managing what's on a disk.

## Requirements

- macOS (tested on Apple Silicon)
- Python 3.13 (Homebrew: `/opt/homebrew/bin/python3.13`)
- [opencbm](https://opencbm.trikaliotis.net/) 0.4.99.104 via Homebrew — provides `cbmctrl`, `cbmwrite`, `cbmread`, `d64copy`, `cbmforng`, `cbmformat`
- xum1541 USB adapter with a connected 1541 (or compatible) drive
- [VICE](https://vice-emu.sourceforge.io/) *(optional)* — the `c1541` tool from a VICE app bundle enables disk image creation and image content browsing. Diskmasher looks for a VICE installation at `/Applications/VICE*/bin/c1541`. Image-related features are disabled if VICE is not found.

## Implemented features

### CBM drive operations
- **Format disk** — full format via `cbmforng`/`cbmformat` with streaming progress output
- **Write files** — write one or more local files (`.prg`, `.seq`, `.usr`) to the drive via `cbmwrite`; the file type comes from the extension (REL files can't be written); right pane refreshes after each file
- **Read files** — read one or more files from the drive to the current local directory via `cbmread`; left pane refreshes after each file
- **Scratch files** — delete one or more files from the drive via `cbmctrl`; right pane refreshes after each scratch
- **Write disk image** — write a `.d64`, `.d71`, or `.d81` image to the drive via `d64copy`/`imgcopy` with streaming progress
- **Read disk to image** — read the entire disk from the drive into a local image file via `d64copy` with streaming progress and configurable retry count for bad sectors. A quick disk check (tracks 18 and 36) first picks 35 or 40 tracks and explains why, so a 40-track disk isn't silently cut short; double-sided disks are refused until `.d71` reading exists. Afterwards the log lists the image's files, read errors and any data on tracks 36–40. An interrupted read is kept as `…-incomplete.d64`, never under the real name
- **FB-ify disk** — write a File Browser boot file to the disk; platform variants: C16/Plus4, C64, C128, VIC-20 (all five memory-configuration variants), All; same safety steps as Sort
- **Sort disk** — reorganise files on disk: optional FB first, PRGs alphabetically, then SEQ/USR in their current order (a disk with REL files is refused). Before anything is scratched, it checks the disk, reads the whole disk to an image (35 or 40 tracks, about a minute) and analyses it, then reads every file; a double-sided disk, files reaching beyond track 35, read errors inside files, unsupported file types, or a disk swapped since the listing stop it with the disk unchanged. The dialog shows each step, real progress and the time left. Keeping the image is optional; if rewriting fails after the scratch, the read files go to a `…_rescue` folder and the image is kept
- **Erase disk** — wipe tracks 36–40 (turns a 40-track disk into a clean 35-track one; tracks 1–35 untouched) or the whole disk (reads as never formatted afterwards). A small routine of our own runs in the drive and overwrites each track with sync-free bytes; it stops at once on a write-protected disk, and afterwards the disk check confirms the result. 1541/1570/1571 only
- **Make first (LOAD"*")** — right pane File menu: moves the selected PRG to the first directory slot, so `LOAD"*",8` (or `,8,1`) loads it after a drive reset or power-on; the other files keep their order. Only the directory sectors on track 18 are rewritten (file data and the BAM are untouched), and only after the directory read from the disk matches the listing on screen; the original sectors are saved first to `~/Library/Application Support/Diskmasher/directory-backups/`, and what was written is read back and compared. Cancel works until the writing starts. To sort the other files, run Sort first

### Disk image operations *(requires VICE/c1541)*
- **Create new image** — create a blank `.d64` image (35-track, 174848 bytes) in the current local directory; disk name and filename are kept in sync; d71/d81 greyed out pending implementation
- **Browse image contents** — double-click a `.d64`/`.d71`/`.d81` in the left pane to view its directory listing via `c1541`
- **Read IEC files to image** — read selected files from the connected drive into a selected local image file; pre-flight checks block count and image writability before any IEC activity starts *(see Work in progress below)*

### UI and workflow
- Commander-style split pane with adjustable sash; geometry and sash position persisted across sessions
- Left pane: `..` at the top of the list goes up one folder (so does ⌘↑) and selects the folder you came from; the File menu has New directory…, New image… and Move to Trash… (⌘⌫), which moves the selected files and folders to the macOS Trash after a confirmation that lists them, so Finder's Put Back works
- Multi-select with Cmd-click (macOS convention) on both panes
- Right pane: disk name, ID, file count, blocks free; selection line shows blocks and kB for selected files
- Left pane: folder and file counts, total kB; selection line shows image format info and blocks free (queried live from the image via c1541) or file count and size for non-image selections
- Cancel button on all long-running operations — sends SIGTERM and gives the tool up to three seconds to let go of the bus (SIGKILL only if it doesn't), then `cbmctrl reset`, then a `cbmctrl detect` health check that reports one of: bus OK (with the drives found), no drive answering (check power and cable), or the xum1541 not responding (replug USB)
- Status bar with operation feedback throughout; every drive command is also logged to `~/Library/Logs/diskmasher.log` (duration, outcome, last output lines) for tracking down drive problems
- Single-instance enforcement via PID file
- Auto-detect drives and refresh disk directory on startup; the Drive menu lists the detected drives to choose from (remembered across sessions; if the remembered drive doesn't answer, the first detected one is used)
- "Read to image…" menu item is greyed out unless exactly one recognised image is selected in the left pane

## Testing status

An offline test suite (about 40 seconds, no drive needed) covers disk image analysis, parsing of opencbm output, the erase routine's bytes, and the drive protocols against simulated opencbm tools: success and failure detection, the erase job sequence, recovery from `74,DRIVE NOT READY`, the track-36 disk check, and Make first's directory edit. Make first is also checked on an emulated C64: VICE (with its real 1541 DOS) must load the moved file with `LOAD"*",8` and `,9`. That test needs VICE and the sibling c64home project, and is skipped without them. Run everything with `python3.13 -m unittest discover -s tests`. Tests against a real drive are still to come.

Most IEC operations have been exercised extensively in normal use. Cancel handling has been tested under adverse conditions: drive power cycle mid-read, IEC cable unplug mid-read, and cancel during reads on disks with bad sectors. The bus has proven more resilient than initially expected — no true xum1541 firmware lockups were reproduced during testing.

The **Read IEC files to image** feature is newly implemented and has seen limited testing. The image creation and directory browse features have been verified with VICE.

## Work in progress

### IEC-to-image workflow (stop-gap)

The current "Read to image…" implementation is deliberately minimal: it reads each selected CBM file to a temporary directory via `cbmread`, then writes it into the local image with `c1541 -write`. There is no integration between the image view and the transfer — the image directory popup is a one-shot modal, not a live view. This is enough to support the core workflow (pull files off an old floppy, drop them into an image, write the image back to a fresh disk) but is not a polished end-state.

## Planned and to-do

### Disk files (right pane)
- **Rename** in the File menu, with the DOS command `R0:new=old`: e.g. to make room for a local file of the same name before writing it
- **Copy** in the File menu, with the DOS command `C0:copy=original`: an exact duplicate on the same disk, a spare in case the original gets damaged
- Both check the new name (16 characters, not already on the disk) and refresh afterwards

### Image handling (near term)
- `.d71` and `.d81` image creation (currently greyed out)
- Delete (scratch) files from a local image via `c1541`, completing image editing with Read to image. Works on 40-track images too: `c1541` leaves tracks 36–40 and the extended BAM untouched (tested 2026-11-25); refuse files whose chain reaches beyond track 35, which neither `c1541` nor a stock DOS can handle
- Rename files in a local image via `c1541`
- Show blocks-free count in the image directory popup

### Image handling (longer term)
- Left pane first-class image browsing — double-clicking an image navigates into it rather than opening a popup, with the image directory displayed inline and operations (scratch, rename, read-to-image) available directly. This is a significant abstraction and is treated as a future chapter.
- `.d71`/`.d81` full support throughout (browse, read-to-image, create)

### Infrastructure
- Startup dependency check for opencbm tools (`cbmctrl`, `d64copy`, `cbmwrite`, `cbmread`) with clear install instructions if missing
- xum1541 USB reset via macOS IOKit — programmatic recovery from firmware lockup without physically replugging the USB cable
- Write verify — read back after each `cbmwrite` to catch flaky sectors
- Preferences UI for default checkbox states (platform, FB options) and other settings
- Window proportion preferences

### Testing and documentation
- Hardware tests, run on request against a real drive and a sacrificial disk (read, write, scratch, erase, format, cancel paths); the offline suite is in `tests/`

### Release
- GitHub release with full dependency and setup documentation

## Known caveats

- 40-track `.d64` images are recognised, can be read from a disk and written back, but new images are 35-track only: a blank 40-track image is easy to make, but `c1541` (and a stock 1541/1571 DOS) only ever writes files on tracks 1–35, so the extra tracks would stay empty.
- The VICE `c1541` binary has a heavy GTK3/audio dependency tree and must be run via the VICE app bundle wrapper script. A standalone headless build would be preferable long-term.
- Hard IEC bus lockups (xum1541 firmware stuck mid-transaction) currently require USB replug. The cancel sequence (SIGTERM → SIGKILL → `cbmctrl reset`) handles the common case; the IOKit reset path is a future improvement.
