"""rebuild_image: a fresh image with the same files, order, names, types and header, each
file in one piece; and what it refuses. Needs VICE's c1541 (skipped without it)."""
import os
import tempfile
import unittest

from _common import _bytes, d, make_d64


def f(name, chain, data, **kw):
    return dict(name=name, sectors=chain, data=data, **kw)


@unittest.skipIf(d._C1541 is None, "VICE's c1541 not found")
class Rebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "new.d64")

    def files(self, img):
        return [(e[2], e[5:21], d.image_file_data(img, e[3], e[4]))
                for _, e in d._listed_entries(img)]

    def test_same_files_order_names_and_header(self):
        big = bytes(range(256)) * 3                       # 768 bytes: 4 sectors
        gfx = b"\xc1\xc2 ART"                             # shifted letters: graphics on a C64
        src = make_d64(name="MY DISK", files=[
            f("ZED", [(30, 5), (3, 1), (17, 9), (3, 11)], big),   # scattered
            f("GONE", [(5, 0)], b"x", type=0),                     # scratched
            f(gfx.decode("latin-1"), [(1, 0)], b"\x01\x08hi"),
            f("NOTES", [(2, 0)], b"TEXT\r", type=0x81),
            f("LOCKED", [(4, 0)], b"\x01\x08", type=0xC2),
            f("DATA", [(6, 0)], b"U", type=0x83)])
        self.assertIsNone(d.rebuild_image(src, self.out))
        new = _bytes(self.out)
        self.assertEqual(len(new), 174848)
        self.assertEqual(self.files(new), self.files(src))      # GONE stays gone
        bam = d._blk(18, 0) * 256
        self.assertEqual(new[bam + 0x90:bam + 0xAB], src[bam + 0x90:bam + 0xAB])
        # ZED is written in one go now: its 4 sectors sit on one track.
        e = d._listed_entries(new)[0][1]
        t, s, tracks = e[3], e[4], set()
        while t:
            tracks.add(t)
            o = d._blk(t, s) * 256
            t, s = new[o], new[o + 1]
        self.assertEqual(len(tracks), 1)

    def test_refuses_what_it_cant_copy_and_writes_nothing(self):
        cases = {
            "read error": make_d64(files=[f("A", [(1, 0)], b"x")], errors={(1, 0): 5}),
            "DEL": make_d64(files=[f("A", [(1, 0)], b"x"), f("----", [(2, 0)], b"", type=0x80)]),
            "REL": make_d64(files=[f("A", [(1, 0)], b"x", type=0x84)]),
            "40-track": make_d64(tracks=40, files=[f("A", [(1, 0)], b"x")]),
            "no files": make_d64(),
        }
        for what, src in cases.items():
            with self.subTest(what):
                self.assertIsNotNone(d.rebuild_image(src, self.out))
                self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
