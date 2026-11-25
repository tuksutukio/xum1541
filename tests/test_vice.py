"""Make first, checked on an emulated C64: after move_to_first_slot, LOAD"*",N in VICE
(true drive emulation, a real 1541 DOS) loads the chosen file.

Needs VICE (x64sc) and c64home's tools/vicetest.py (../c64home, used by relative path
as c64home's README asks). Skipped when either is missing. Takes about 15–30 s.
VT_WINDOW=1 shows VICE's window; VT_KEEP=1 keeps the scratch files."""
import glob
import os
import shutil
import sys
import unittest

from _common import REPO, FakeTools, _bytes, basic_rem, d, make_d64

VICETEST = os.path.join(os.path.dirname(REPO), "c64home", "tools")
X64SC = (os.environ.get("X64SC") or shutil.which("x64sc") or
         next(iter(sorted(glob.glob("/Applications/VICE*/bin/x64sc"))), None))


@unittest.skipUnless(X64SC and os.path.isfile(os.path.join(VICETEST, "vicetest.py")),
                     "VICE (x64sc) or ../c64home/tools/vicetest.py not found")
class LoadStarInVice(FakeTools):
    def test_load_star_loads_the_moved_file(self):
        os.environ["X64SC"] = X64SC
        sys.path.insert(0, VICETEST)
        try:
            import vicetest
        finally:
            sys.path.remove(VICETEST)
        vicetest.X64 = X64SC

        files = [dict(name=n.upper(), sectors=[(1 + i, 0)], data=basic_rem(n))
                 for i, n in enumerate(("alpha", "beta", "game"))]
        original = self.write_file("original.d64", make_d64(name="VICE TEST", files=files))
        moved = self.write_file("moved.d64", _bytes(original))
        self.scenario(disk=moved)
        listing = [(1, n, "prg") for n in ("alpha", "beta", "game")]
        ok, msg, _ = d.move_to_first_slot(2, listing)
        self.assertTrue(ok, msg)
        moved9 = self.write_file("moved9.d64", _bytes(moved))

        # vicetest always autostarts an image: give it a harmless program, injected
        # straight into memory (not through a drive), and NEW it away.
        boot = self.write_file("boot.prg", basic_rem("boot"))
        extra = ["-autostartprgmode", "1",
                 "-drive8type", "1541", "-8", moved,
                 "-drive9type", "1541", "-9", moved9,
                 "-drive10type", "1541", "-10", original]
        with vicetest.Vice(boot, extra=extra) as v:
            v.wait_for("ready.", timeout=60)
            v.run("new")
            for unit, want in ((8, "10 rem game"), (9, "10 rem game"), (10, "10 rem alpha")):
                with self.subTest(unit=unit):
                    v.run(f'load"*",{unit}', timeout=120)
                    self.assertIn(want, v.run("list"))
                    v.run("new")


if __name__ == "__main__":
    unittest.main()
