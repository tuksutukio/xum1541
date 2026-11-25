# Open items — xum1541 (diskmasher)

Format and rules: "Open items" in ~/src/claude-home/CLAUDE.md. Imported by CLAUDE.md.
The wider backlog is README.md "Planned and to-do"; only these are tracked as items for now (Antti, 2026-10-15).

- parked · 2026-07-16 · "Create new image": add .d71 and .d81 (currently greyed out, only .d64 implemented) · when: a d71/d81 is actually needed · → README.md "Planned and to-do"
- parked · 2026-06-30 · xum1541 lockup after cancel: may already be fixed; run the untested SIGKILL-from-terminal case; if it locks up, investigate a programmatic USB reset via IOKit (or `xum1541cfg`) instead of a physical replug · when: a lockup happens again · → CLAUDE.md "xum1541 lockup: evidence so far"
- parked · 2026-06-30 · Revisit window/pane proportions (defaults 1100×660, sash ~halfway) once the app has more mileage · when: next diskmasher UI work · → CLAUDE.md "UI layout"
- todo · 2026-11-25 · Hardware-test the 40-track write refusal: Write image with a 40-track .d64 and Pre-format unticked onto a disk never formatted past track 35 (the spare has 36–40 from cbmforng -x, so it can’t be used) → refused; also shows how d64copy behaves on an unformatted track 36 (probe_disk’s untested path) · → opencbm-tools.md
- todo · 2026-06-30 · One IEC operation at a time: a shared lock or busy flag around every drive operation, plus a busy indicator where there is no progress window; never implemented (was kept only in Claude memory until 2026-11-25) · → README.md "Planned and to-do", Infrastructure
