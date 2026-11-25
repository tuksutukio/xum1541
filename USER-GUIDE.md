# Diskmasher user guide

Diskmasher moves software between your Mac and real Commodore floppy disks, through an xum1541 USB adapter and a
1541, 1570 or 1571 drive. This guide covers every feature: what it does, how to use it, and what its checks and
messages mean. For installation requirements, see [README.md](README.md).

## Contents

- [Before you start](#before-you-start)
- [The window](#the-window)
- [Choosing a drive](#choosing-a-drive)
- [Files: write, read, scratch, rename, copy, make first](#files-write-read-scratch-rename-copy-make-first)
- [Whole disks: images, format, erase](#whole-disks-images-format-erase)
- [Rearranging a disk: Sort and FB-ify](#rearranging-a-disk-sort-and-fb-ify)
- [Local files and disk images](#local-files-and-disk-images)
- [Cancel, and when something goes wrong](#cancel-and-when-something-goes-wrong)
- [The disk check and 40-track disks](#the-disk-check-and-40-track-disks)
- [Where Diskmasher keeps things](#where-diskmasher-keeps-things)
- [Limits](#limits)

## Before you start

- Connect the xum1541 to the Mac and the drive to the xum1541, and switch the drive on.
- Start Diskmasher: `./diskmasher.py` in the project folder. Only one copy runs at a time.
- At start-up it looks for drives on the bus and lists the disk in the selected drive.
- The left pane opens at your home folder.
- **Only one program can use the xum1541 at a time.** While Diskmasher is working on the drive, command-line
  tools (`cbmctrl`, `d64copy`) fail with `LIBUSB_ERROR_ACCESS`, and the other way round.
- **Write protection:** the 1571 senses the write-protect notch with light. Clear tape lets the light through, so
  the drive treats the disk as writable. When you want to protect a disk, cover the notch with something opaque
  (black tape, foil, or a proper write-protect tab).

## The window

The window has two panes side by side.

- **Left: your Mac.** Folders, disk images (`.d64`, `.d71`, `.d81`) and Commodore files (`.prg`, `.seq`, `.usr`,
  `.t64`, `.tap`, `.crt`). Other files are not shown.
  - Above the list: the current folder, the **File** menu, **Refresh**, and **Write →**.
  - Below the list: how many folders and files there are, and what's selected. For a selected image that is the
    format and, with VICE installed, its free blocks; for files, their total size in blocks and kB.
- **Right: the disk in the drive.** The directory as the drive reports it.
  - Above the list: the drive number, the disk name and ID, and the menus **← Read**, **Drive**, **Disk** and
    **File**.
  - Below the list: file count and blocks free, and the size of the selection.
- **Status bar** (bottom): what the last operation did. The dot at its right end is green while the drive is
  idle, flashes red while it's working, and is grey when no drive answered the last scan (Detect, or the bus check
  after a Cancel). While it's red, the drive menus and **Write →** are greyed out: only
  one drive operation runs at a time. They come back when it's finished, including the directory refresh that
  follows. The left pane stays usable for browsing, except during operations that read from or write into its
  folder (writing files or an image, reading files or an image, Read to image, Sort, FB-ify): then it's greyed out
  until they finish.

Selecting: click selects one item, ⌘-click adds or removes items. Click an empty area below the list to clear the
selection. The divider between the panes can be dragged; window size and divider position are remembered.

## Choosing a drive

- **Drive → Detect** looks for drives on the bus (a second or two).
- The Drive menu then lists them, for example "Use drive 9: 1571". Choose one: every operation uses that drive
  until you choose another.
- The choice is remembered. If the remembered drive doesn't answer at start-up, the first drive found is used and
  the status bar says so.
- You can't switch drives while an operation window is open.
- **Drive → Reset** resets the bus and every drive on it, then looks for drives again and refreshes. Use it when
  a drive misbehaves: keeps spinning, answers `74,DRIVE NOT READY`, or seems stuck. If no drive answers
  afterwards, check the drives' power and the cable; if the xum1541 itself doesn't answer, replug its USB cable.
- **Drive → Refresh** reads the directory again. Commodore drives can't tell the computer that a disk was
  changed, so the right pane keeps showing the old disk until you refresh. Refresh after every disk change.

## Files: write, read, scratch, rename, copy, make first

### Write files to the disk

1. Select one or more files in the left pane.
2. Click **Write →**.

Each file is written under its name without the extension, in lower case, up to 16 characters (`Game.prg` becomes
`game`). The right pane updates after every file. If a file can't be written, for example because a file of the
same name already exists (`63, FILE EXISTS`), the others still go, and the errors are listed at the end.

Selecting a disk image instead of files writes the whole image (see [Write an image](#write-an-image-to-a-disk)).
Images and files can't be written in one go.

### Read files from the disk

1. In the left pane, open the folder the files should go to.
2. Select one or more files in the right pane.
3. Open the **← Read** menu and choose **Read file(s)**.

Files arrive with an extension for their type (`.prg`, `.seq`, …). A read counts as successful only when the file
really arrived: the opencbm tools sometimes report success after a failed transfer, and Diskmasher checks for that.

### Scratch (delete) files

Select files in the right pane and choose **File → Scratch…**. You confirm the list first; Diskmasher waits for
the drive to finish each one before the next.

### Rename and copy files

Select one file in the right pane and choose **File → Rename…** or **File → Copy…**, then type the new name. The
drive does the work itself (its `R` and `C` commands), so nothing travels to the Mac.

- **Rename**, for example, to make room for a local file of the same name before writing it.
- **Copy** makes a second, identical file on the same disk: a spare in case the original gets damaged. Only
  closed PRG, SEQ and USR files can be copied, and there must be room for it. The drive copies on its own, so
  there's no Cancel; a long file takes a while.
- The new name: up to 16 characters, not already on the disk, and none of `, " : = * ?`. Letters are stored as
  capitals, as typed on a C64.
- Names with graphics characters (shown in lowercase in the listing) can't be renamed, copied or scratched:
  they can't be typed into a drive command reliably.

### Make a file load with `LOAD"*"`

`LOAD"*",8` (or `,8,1`) loads the first file in the directory, after the drive has been reset or switched on.
**File → Make first (LOAD"*")…** moves the selected program to the top, so it loads that way. The other files keep
their order.

1. Select one PRG file in the right pane.
2. Choose **File → Make first (LOAD"*")…** and confirm.

How it works, and how it keeps the disk safe:

- Only the directory (on track 18) is rewritten. File contents and the block allocation map (BAM) are not touched.
- Before writing, Diskmasher reads the directory from the disk and checks that it matches the list on screen. If
  the disk was swapped, it stops without changing anything.
- The original directory is saved to a backup file first (see
  [Where Diskmasher keeps things](#where-diskmasher-keeps-things)), and what was written is read back and compared.
- Cancel works until the writing starts; then it is switched off, so the directory is never left half rewritten.
- Only a properly closed PRG can be chosen (a locked one, shown as `prg<`, is fine). SEQ, USR, REL and unclosed
  (`*prg`) files are refused, because `LOAD` loads programs.

To have the other files sorted as well, run [Sort](#sort) first, then Make first.

## Whole disks: images, format, erase

### Write an image to a disk

1. Select one `.d64`, `.d71` or `.d81` image in the left pane.
2. Click **Write →**.
3. In the dialog, decide on **Pre-format disk** ("Format disk before writing"). Writing an image needs a formatted
   disk; tick this for a new or erased disk. The disk is formatted with the image's own name and ID, as the
   original was (the write replaces the name anyway, but the ID is also in every sector's header, which only a
   format writes). An image whose name or ID has characters the formatter can't reproduce gets `DISK` and `01`.
4. Click **Write**. A window shows the progress.

Details:

- A `.d71` needs a 1571. A 40-track `.d64` needs a disk formatted for 40 tracks: pre-format is ticked for it by
  default. If you untick it, Diskmasher first checks that the disk in the drive has a sound track 36, and refuses
  if it doesn't (see [The disk check](#the-disk-check-and-40-track-disks)).
- The write counts as successful only if every block was written. If the disk turns out to be unformatted, the
  message suggests writing again with Pre-format ticked.

### Read a disk to an image

1. In the left pane, open the folder the image should go to.
2. Open the **← Read** menu and choose **Read image**.
3. Diskmasher first checks the disk (a few seconds; a spinner and a seconds counter show it's working). The
   result decides between 35 and 40 tracks and is explained in the dialog.
4. Check the file name, the 40-track box and **Retries** (how often a bad sector is tried again), then click
   **Read image**.

A full read takes about a minute. Afterwards the window lists what's in the image: its files, sectors that couldn't
be read (they are marked inside the image, in its error table), and any data on tracks 36–40 that belongs to no
file. If fewer blocks were read than the disk holds, it says how many are missing.

If the read is cancelled or fails, what was read is kept under a name ending in `-incomplete.d64`, never under the
name you chose, so a partial image is never mistaken for a good one. A failing disk may not read a second time, so
even a partial copy can be worth keeping.

A double-sided disk (1571) is refused: reading `.d71` images isn't supported yet.

### Format a disk

**Disk → Format…** formats the disk in the drive. Everything on it is lost.

- **Tool:** `cbmforng` (default) or `cbmformat`.
- **Name and ID:** the disk name (up to 16 characters) and the disk ID: any two characters, letters or digits, not
  a comma. The ID is shown next to the name in the directory and is useful for telling disks apart; giving each
  disk its own ID is a good habit. Format checks both before it starts: no comma or `"`, and only characters
  the drive can store as typed.
- **Options:**
  - **40-track extended (-x):** format tracks 36–40 too.
  - **No head bump (-n):** skip the knock that moves the head to track 1 first.
  - **Demagnetize first (-c):** clear each track before formatting. Recommended for disks used elsewhere before.
    This works on the tracks being formatted only, so it doesn't remove an old 40-track format; use Erase for that.
  - **Verify after write (-v):** read back each track.
  - **Original fill pattern (-o):** fill sectors the way Commodore's own format does, instead of with zeroes.
  - **Show drive status (-s)**, and **Progress indicator (-p)** for `cbmformat`.
  - **Retries (-r):** for `cbmforng`.

A cancelled format leaves the disk unusable until it is formatted again.

### Erase a disk

**Disk → Erase…** overwrites tracks so that nothing on them can be read back. Two choices:

- **Whole disk (tracks 1–40)** (default): the disk reads as never formatted. **It must be formatted before
  anything can be written to it:** use **Disk → Format…**, or write an image with **Pre-format disk** ticked, which
  formats and writes in one go. Writing files or an image without formatting fails with an error.
- **Tracks 36–40 only:** turns a 40-track disk into a 35-track one. Tracks 1–35 are left untouched: files on
  them stay; anything reaching into tracks 36–40 is lost.

Erasing a disk that was once formatted for 40 tracks is the way to get a true 35-track disk: a 35-track format
alone leaves the old tracks 36–40 in place, and the drive still finds them.

- A write-protected disk is refused before anything is written.
- Afterwards the disk is checked: for "Tracks 36–40 only" the result should say the disk now checks as 35-track.
- Erase works with 1541, 1570 and 1571 drives. A small routine of Diskmasher's own runs inside the drive for it.
- Cancel stops after the current track; that track may be partly erased. Erase again to finish.

## Rearranging a disk: Sort and FB-ify

Both rewrite every file on the disk: they read everything off it, scratch all the files and write them back in
the new order. That is a lot of reading and writing, and every step is a chance for something to go wrong, so both
work the same careful way (below). **Even so, don't use them on rare originals or on disks you can't replace:**
copy such a disk to an image first (Read image) and rearrange a copy instead. Commercial originals may also rely on
their exact layout (copy protection, loaders that read sectors directly), which a rewrite doesn't keep.

1. **Check the disk** (a few seconds).
2. **Read the whole disk to an image** (about a minute) and analyse it.
3. **Read every file** to the Mac.
4. Only then **scratch the files and write them back** in the new order.

Anything obviously risky stops the operation in steps 1–3, with the disk unchanged:

- a double-sided disk;
- files reaching beyond track 35;
- read errors inside files;
- REL, GEOS, DEL or unclosed files;
- a disk swapped since the listing.

The dialog shows each step, real progress and the time left.

**Safety copy:** the whole-disk image from step 2 can be kept ("Keep the image afterwards", with its path). If
writing fails after the files were scratched, the files read in step 3 are saved to a folder ending in `_rescue`
next to the image, and the image is kept whatever you chose. Write them back from there.

### Sort

**Disk → Sort…** orders the files: PRG files alphabetically, then SEQ and USR files in their current order.
Optionally the File Browser goes first ("Add FB as first file"); choose which machines it's for, as in FB-ify below.

### FB-ify

**Disk → FB-ify…** adds CBM FileBrowser as the first file, so `LOAD"*",8` followed by `RUN` starts a menu of the
disk's files. The browser is a machine-language program, and each Commodore machine needs its own version, so you
choose which machine (or machines) the disk is for:

- **C64**, **C128** or **C16/Plus4:** one file, `FB`, the version for that machine.
- **VIC-20:** a small BASIC starter `FB` plus the four VIC-20 versions (unexpanded, +3K, +8K or more, Mega-Cart).
  The starter detects the machine and loads the matching version.
- **All:** the BASIC starter plus the versions for every machine above, so the same disk starts the browser on a
  C64, C128, C16/Plus4 or any VIC-20.

Room on the disk: 9–10 blocks for one machine, 41 for VIC-20, 69 for All. The extra files show up in the directory
right after `FB`. The C64DTV version isn't offered.

## Local files and disk images

- **Navigating:** double-click a folder (or press Return) to open it. `..` at the top of the list goes up one
  folder, and so does ⌘↑; the folder you came from is then selected.
- **File → New directory…:** a new folder in the current one.
- **File → New image…:** a blank 35-track `.d64` in the current folder (needs VICE). The disk name and file name
  follow each other while you type.
- **File → Move to Trash… (⌘⌫):** moves the selected files and folders to the macOS Trash, after a confirmation
  that lists them (with how many items each folder holds). **OK** is the default. Use the Finder's Put Back to undo.
  Copying, moving and renaming are left to the Finder.
- **Look inside an image:** double-click a `.d64`, `.d71` or `.d81` to see its directory (needs VICE).
- **Read to image…** (in the **← Read** menu): reads the selected files from the drive straight into the image
  selected in the left pane (needs VICE). It checks first that the image is writable and has room.

## Cancel, and when something goes wrong

### Cancel

Every operation that uses the drive has a Cancel button. Cancel stops the running tool gently (it gets three
seconds to let go of the bus before it is stopped hard), resets the bus, waits two seconds for the drive, and checks
that the drive answers. The status bar then shows one of three results:

- **"Cancelled — bus OK (9: 1571)":** all good; the directory is read again.
- **"no drive answers":** check the drive's power and the cable between drive and xum1541.
- **"xum1541 unresponsive — replug USB cable":** unplug the xum1541's USB cable and plug it in again. If that isn't
  enough, also switch the drive off and on.

### `74, DRIVE NOT READY`

The drive says this when it can't find a readable track under its head, or after a few other mishaps.

- **The directory shows 74:** Diskmasher moves the head to track 1 and then to track 18, reinitialises the drive,
  and reads again. This fixes the case where the head was left on a blank track, for example after a cancelled
  erase.
- **The disk is blank or erased:** 74 is the correct answer. Format it.

### The drive log

Every command sent to a drive goes into `~/Library/Logs/diskmasher.log`, with how long it took, how it ended and
its last lines of output. When a drive misbehaves, the log usually shows where.

## The disk check and 40-track disks

Some disks were formatted for 40 tracks instead of the standard 35. You can't tell by looking, and a 35-track copy
of such a disk silently loses tracks 36–40. So before reading a disk to an image, and before Sort and FB-ify,
Diskmasher checks it. It reads track 18 (the directory) and track 36, which takes a few seconds.

What the check can find:

- **Standard 35-track disk:** track 36 isn't there.
- **40-track disk:** all 17 sectors of track 36 read. Reads then cover 40 tracks.
- **Damaged 40-track disk:** only some sectors of track 36 read. This is a 40-track disk that has decayed; reads
  cover 40 tracks so nothing is lost, and Write image without pre-format refuses it.
- **Couldn't check:** the track-36 read took too long, so it's unknown whether the disk has 40 tracks. Read
  image suggests ticking 40-track to be safe (on a 35-track disk the extra tracks just read as errors). Sort and
  FB-ify read 35 tracks; a file reaching beyond them then shows up in the analysis and stops the operation.
- **Double-sided (1571) disk:** reading and Sort/FB-ify refuse it, since only one side would be read.
- **Extended BAM:** a hint that the disk was formatted by a 40-track DOS (SpeedDOS or DolphinDOS style).

## Where Diskmasher keeps things

- **Settings** (window size, divider position, drive, checkbox defaults):
  `~/.config/diskmasher/prefs.ini`
- **Drive command log:** `~/Library/Logs/diskmasher.log`; the previous one is `diskmasher.log.1`.
- **Directory backups from Make first:**
  `~/Library/Application Support/Diskmasher/directory-backups/`
- **Safety copies from Sort and FB-ify:** where you chose in the dialog. By default that's the current left-pane
  folder, as `<disk name>_backup.d64`.
- **Rescued files after a failed Sort or FB-ify:** a `…_rescue` folder next to the safety copy.
- **"Only one copy running" marker:** `~/.diskmasher.pid`

A directory backup can be written back to the disk if a Make first ever goes wrong; there is no menu item for
that yet.

## Limits

- `.d71` (double-sided) disks can be written but not read; `.d81` images can be written with a 1581 (`imgcopy`).
- New images are 35-track `.d64` only. A blank 40-track image would be easy to make, but nothing here could use
  the extra tracks: VICE's `c1541` stops at track 35 when adding files, and so does a stock
  1541/1571 DOS. 40-track images come from reading 40-track disks.
- Erase and Make first work with 1541, 1570 and 1571 drives.
- Write files only offers what the left pane shows (see [The window](#the-window)).
- REL (relative) files can't be read or written one by one; reading or writing the whole disk as an image
  carries them along.
- If the xum1541 itself locks up, only replugging its USB cable helps.
