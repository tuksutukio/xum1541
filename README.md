# Diskmasher

A macOS GUI for mastering Commodore floppy disks via an xum1541 USB adapter. The goal is to reduce the mechanical effort of getting software from a digital collection onto physical media — minimum clicks between "I want this on a disk" and "disk is ready."

## What it is (and isn't)

Diskmasher is a split-pane tool: the left pane navigates your local filesystem, the right pane shows the directory of a connected CBM drive. You pick files from one side and push them to the other.

It is **not** a general-purpose file manager. The left pane is intentionally read-light — you can create folders and disk images, but renaming, deleting, and managing local files is left to the Finder or your shell. The focus is on the CBM side of the workflow: writing floppies, reading them back, building images, and managing what's on a disk.

## Requirements

- macOS (tested on Apple Silicon)
- Python 3.13 (Homebrew: `/opt/homebrew/bin/python3.13`)
- [opencbm](https://opencbm.trikaliotis.net/) 0.4.99.104 via Homebrew — provides `cbmctrl`, `cbmwrite`, `cbmread`, `d64copy`, `cbmforng`, `cbmformat`
- xum1541 USB adapter with a connected 1541 (or compatible) drive
- [VICE](https://vice-emu.sourceforge.io/) *(optional)* — the `c1541` tool from a VICE app bundle enables disk image creation and image content browsing. Diskmasher looks for a VICE installation at `/Applications/VICE*/bin/c1541`. Image-related features are disabled if VICE is not found.

## Implemented features

### CBM drive operations
- **Format disk** — full format via `cbmforng`/`cbmformat` with streaming progress output
- **Write files** — write one or more local files (PRG, SEQ, USR, REL) to the drive via `cbmwrite`; file type is inferred from the local file extension; right pane refreshes after each file
- **Read files** — read one or more files from the drive to the current local directory via `cbmread`; left pane refreshes after each file
- **Scratch files** — delete one or more files from the drive via `cbmctrl`; right pane refreshes after each scratch
- **Write disk image** — write a `.d64`, `.d71`, or `.d81` image to the drive via `d64copy`/`imgcopy` with streaming progress
- **Read disk to image** — read the entire disk from the drive into a local image file via `d64copy` with streaming progress and configurable retry count for bad sectors
- **FB-ify disk** — write a File Browser boot file to the disk; platform variants: C16/Plus4, C64, C128, VIC-20 (all five memory-configuration variants), All
- **Sort disk** — reorganise files on disk: optional FB first, PRGs alphabetically, then SEQ/USR/REL; optional image backup before sorting

### Disk image operations *(requires VICE/c1541)*
- **Create new image** — create a blank `.d64` image (35-track, 174848 bytes) in the current local directory; disk name and filename are kept in sync; d71/d81 greyed out pending implementation
- **Browse image contents** — double-click a `.d64`/`.d71`/`.d81` in the left pane to view its directory listing via `c1541`
- **Read IEC files to image** — read selected files from the connected drive into a selected local image file; pre-flight checks block count and image writability before any IEC activity starts *(see Work in progress below)*

### UI and workflow
- Commander-style split pane with adjustable sash; geometry and sash position persisted across sessions
- Multi-select with Cmd-click (macOS convention) on both panes
- Right pane: disk name, ID, file count, blocks free; selection line shows blocks and kB for selected files
- Left pane: folder and file counts, total kB; selection line shows image format info and blocks free (queried live from the image via c1541) or file count and size for non-image selections
- Cancel button on all long-running operations — sends SIGTERM, then SIGKILL after one second, then `cbmctrl reset`, followed by a health check that probes `cbmctrl detect` for up to ten seconds and reports whether the bus is responsive or the USB cable needs replugging
- Status bar with operation feedback throughout
- Single-instance enforcement via PID file
- Auto-detect drives and refresh disk directory on startup
- "Read to image…" menu item is greyed out unless exactly one recognised image is selected in the left pane

## Testing status

Most IEC operations have been exercised extensively in normal use. Cancel handling has been tested under adverse conditions: drive power cycle mid-read, IEC cable unplug mid-read, and cancel during reads on disks with bad sectors. The bus has proven more resilient than initially expected — no true xum1541 firmware lockups were reproduced during testing.

The **Read IEC files to image** feature is newly implemented and has seen limited testing. The image creation and directory browse features have been verified with VICE.

## Work in progress

### IEC-to-image workflow (stop-gap)

The current "Read to image…" implementation is deliberately minimal: it reads each selected CBM file to a temporary directory via `cbmread`, then writes it into the local image with `c1541 -write`. There is no integration between the image view and the transfer — the image directory popup is a one-shot modal, not a live view. This is enough to support the core workflow (pull files off an old floppy, drop them into an image, write the image back to a fresh disk) but is not a polished end-state.

## Planned and to-do

### Image handling (near term)
- `.d71` and `.d81` image creation (currently greyed out)
- Scratch files from a local image via `c1541`
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

### Release
- GitHub release with full dependency and setup documentation

## Known caveats

- 40-track `.d64` images are recognised and can be written to a drive, but cannot be created locally — extended tracks require proper sector headers that only hardware formatting provides. The option has been intentionally removed from the image creation dialog.
- The VICE `c1541` binary has a heavy GTK3/audio dependency tree and must be run via the VICE app bundle wrapper script. A standalone headless build would be preferable long-term.
- Hard IEC bus lockups (xum1541 firmware stuck mid-transaction) currently require USB replug. The cancel sequence (SIGTERM → SIGKILL → `cbmctrl reset`) handles the common case; the IOKit reset path is a future improvement.
