# Open items — xum1541 (diskmasher)

Format and rules: "Open items" in ~/src/claude-home/CLAUDE.md. Imported by CLAUDE.md.
The wider backlog is README.md "Planned and to-do"; only these are tracked as items for now (Antti, 2026-10-15).

- parked · 2026-07-16 · "Create new image": add .d71 and .d81 (currently greyed out, only .d64 implemented) · when: a d71/d81 is actually needed · → README.md "Planned and to-do"
- parked · 2026-06-30 · xum1541 lockup after cancel: may already be fixed; run the untested SIGKILL-from-terminal case; if it locks up, investigate a programmatic USB reset via IOKit (or `xum1541cfg`) instead of a physical replug · when: a lockup happens again · → CLAUDE.md "xum1541 lockup: evidence so far"
- parked · 2026-06-30 · Revisit window/pane proportions (defaults 1100×660, sash ~halfway) once the app has more mileage · when: next diskmasher UI work · → CLAUDE.md "UI layout"
- todo · 2026-06-30 · One IEC operation at a time: a shared lock or busy flag around every drive operation, plus a busy indicator where there is no progress window; never implemented (was kept only in Claude memory until 2026-11-25) · → README.md "Planned and to-do", Infrastructure
- todo · 2026-11-25 · Disk check too lenient: probe_disk counts track 36 as present if any one of 17 sectors reads OK; require all 17, treat "some" as uncertain (Read disk to image → 40 tracks with a note, Write image → refuse without pre-format). Also find out why checks on drive 9 took 30+ s on some disks (one "couldn't check track 36", one "present but empty"): time the track-18 and track-36 reads from the command line with that disk, list the per-sector codes; consider -r 0/1 for the track-18 read · → opencbm-tools.md
- todo · 2026-11-25 · Drive 8 (first 1571) stopped reading all disks (~02:50: spins, LED flashes, 74 DRIVE NOT READY); disks fine in the spare (device 9). Suspected overheating: disks lay on its ventilation grille, similar issue years ago. Retest after cooling with vents clear; if still failing, clean the head, then look up 1571 service docs in c64doc · → CLAUDE.md "xum1541 lockup: evidence so far"
