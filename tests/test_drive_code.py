"""The erase routine that runs inside the drive: ERASE_CODE must be what drive/erase.s
assembles to (they are kept in step by hand)."""
import os
import shutil
import subprocess
import tempfile
import unittest

from _common import REPO, _bytes, d


class EraseCode(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ca65") and shutil.which("ld65"), "cc65 not installed")
    def test_matches_source(self):
        # ca65 + ld65 separately: cl65 leaves erase.o next to the source, in the repo.
        with tempfile.TemporaryDirectory() as tmp:
            obj, out = os.path.join(tmp, "erase.o"), os.path.join(tmp, "erase.bin")
            subprocess.run(["ca65", "-o", obj, os.path.join(REPO, "drive", "erase.s")],
                           check=True, capture_output=True)
            subprocess.run(["ld65", "-t", "none", "-S", "0x500", "-o", out, obj],
                           check=True, capture_output=True)
            self.assertEqual(_bytes(out), d.ERASE_CODE)

    def test_shape(self):
        code = d.ERASE_CODE
        self.assertLessEqual(len(code), 256)                       # fits buffer 2
        self.assertEqual(code[:5], bytes.fromhex("ad001c2910"))    # write protect check first
        self.assertEqual(code[-3:], bytes.fromhex("4c69f9"))       # ends JMP $F969
        self.assertEqual(code.count(bytes.fromhex("4c69f9")), 2)   # both exits go there


if __name__ == "__main__":
    unittest.main()
