# CLAUDE.md — diskmasher (xum1541)

User docs, features and backlog: README.md. Tool notes: opencbm-tools.md.
The two sections below moved here on 2026-10-15 from Claude's per-project memory, so they're versioned.

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
- **Cancel-sequence ideas (before IOKit):** `cbmctrl reset` *before* killing the process; SIGTERM and wait 2–3 s
  (1 s may be too short); SIGKILL only as a last resort.
- **Health check, implemented:** after cancel, probe `cbmctrl detect` (a real bus operation) every 1 s for up to
  10 s; "Cancelled — bus OK." in the status bar if it responds, otherwise a modal error. `cbmctrl status` was tried
  first and proved insufficient: it succeeded while the bus still wasn't usable.
- **Resilience observed 2026-06-30:** survived a drive power cycle and an IEC cable unplug/replug during an active
  read, without a relaunch. No true firmware lockup was reproduced; early incidents may have been timing-specific.

| Scenario | Expected | Tested |
|---|---|---|
| Cancel during normal operation | Bus OK | ✓ |
| Cancel during read on bad-sector disk | Bus OK or stuck | ✓ (Bus OK) |
| Cancel with IEC cable unplugged | Bus OK after IEC replug + settle | ✓ |
| Cancel with drive powered off | Bus OK | ✓ |
| SIGKILL `d64copy` from terminal (no graceful shutdown) | Most likely to trigger stuck state | ✗ |

- **Next if it recurs:** try the SIGKILL-from-terminal case first. Real fix: a programmatic USB device reset via
  macOS IOKit, or check whether `xum1541cfg` has a reset command. Not yet investigated.
- Why it matters: old CBM hardware never needed power cycling; the xum1541 USB bridge is the weak link.

## Open items

@OPEN.md
