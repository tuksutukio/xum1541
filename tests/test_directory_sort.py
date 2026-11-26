"""Sort and FB-ify by rewriting the directory only: the pure core (directory_sort_order,
reorder_directory) and rewrite_directory against the fake drive with a disk image."""
import glob
import os
import unittest

from _common import FakeTools, _bytes, d, make_d64


def f(name, t, s=0, **kw):
    return dict(name=name, sectors=[(t, s)], **kw)


def body(name, ftype=0x82):
    b = bytearray(30)
    b[0] = ftype
    b[3:19] = name.ljust(16, b"\xa0")
    return bytes(b)


def dir_sectors(img):
    """The directory chain's sectors of an image, as bytes."""
    out, t, s = [], 18, 1
    while t:
        o = d._blk(t, s) * 256
        out.append(img[o:o + 256])
        t, s = img[o], img[o + 1]
    return out


class Order(unittest.TestCase):
    def names(self, bodies, order):
        return [d._entry_name(bodies[i]).decode() for i in order]

    def test_prgs_a_z_then_the_rest_a_z(self):
        bodies = [body(b"ZORRO"), body(b"NOTES", 0x81), body(b"ALPHA"), body(b"DATA", 0x83),
                  body(b"BETA", 0x02), body(b"APPEND", 0x81)]      # BETA: an open PRG
        self.assertEqual(self.names(bodies, d.directory_sort_order(bodies)),
                         ["ALPHA", "BETA", "ZORRO", "APPEND", "DATA", "NOTES"])

    def test_petscii_order_digits_and_spaces_first(self):
        bodies = [body(b"GAME"), body(b"1ST"), body(b" INTRO"), body(b"GAME 2")]
        self.assertEqual(self.names(bodies, d.directory_sort_order(bodies)),
                         [" INTRO", "1ST", "GAME", "GAME 2"])

    def test_equal_names_keep_their_order(self):
        bodies = [body(b"SAME", 0x82), body(b"A"), body(b"SAME", 0xC2)]
        self.assertEqual(d.directory_sort_order(bodies), [1, 0, 2])

    def test_del_entries_sort_with_the_rest(self):
        bodies = [body(b"----", 0x80), body(b"GAME"), body(b"AAAA", 0x80)]
        self.assertEqual(self.names(bodies, d.directory_sort_order(bodies)),
                         ["GAME", "----", "AAAA"])

    def test_first_names_lead_in_the_given_order(self):
        bodies = [body(b"ZED"), body(b"FB20"), body(b"ALPHA"), body(b"FB")]
        order = d.directory_sort_order(bodies, first=[b"FB", b"FB20"])
        self.assertEqual(self.names(bodies, order), ["FB", "FB20", "ALPHA", "ZED"])
        order = d.directory_sort_order(bodies, first=[b"FB", b"FB20"], sort=False)
        self.assertEqual(self.names(bodies, order), ["FB", "FB20", "ZED", "ALPHA"])


class Reorder(unittest.TestCase):
    def test_moves_entries_not_links_and_packs_scratched_slots_last(self):
        img = make_d64(files=[f("C", 1), f("GONE", 2, type=0), f("A", 3), f("B", 4)])
        secs = dir_sectors(img)
        new = d.reorder_directory(secs, [1, 2, 0])            # A, B, C
        names = [d._entry_name(new[0][32 * k + 2:32 * k + 32]) for k in range(4)]
        self.assertEqual(names, [b"A", b"B", b"C", b"GONE"])
        self.assertEqual(new[0][:2], secs[0][:2])              # chain link kept
        self.assertEqual(new[0][32 * 3 + 2], 0)                # scratched entry stays scratched

    def test_across_sectors(self):
        img = make_d64(files=[f(f"F{n:02d}", 1 + n) for n in range(12)][::-1])
        secs = dir_sectors(img)
        new = d.reorder_directory(secs, list(range(12))[::-1])
        names = [d._entry_name(sec[32 * k + 2:32 * k + 32]) for sec in new for k in range(8)]
        self.assertEqual(names[:12], [f"F{n:02d}".encode() for n in range(12)])
        self.assertEqual([sec[:2] for sec in new], [sec[:2] for sec in secs])

    def test_order_must_be_a_permutation(self):
        secs = dir_sectors(make_d64(files=[f("A", 1), f("B", 2)]))
        for bad in ([0], [0, 0], [0, 1, 2]):
            with self.assertRaises(ValueError):
                d.reorder_directory(secs, bad)


class OnTheDrive(FakeTools):
    def disk(self, files):
        self.image = self.write_file("disk.d64", make_d64(name="WORK", files=files))
        self.original = _bytes(self.image)
        self.scenario(disk=self.image)
        info = d.analyze_image(self.original)
        return [(x["blocks"], x["name"].lower(), x["type"].lower()) for x in info["files"]]

    def names(self):
        return [x["name"] for x in d.analyze_image(_bytes(self.image))["files"]]

    def writes(self):
        return [(t, s) for c, t, s in self.jobs_ts() if c == 0x90]

    def setUp(self):
        super().setUp()
        for p in glob.glob(os.path.join(d.DIR_BACKUP_DIR, "*.json")):
            os.remove(p)

    def sort(self, listing, **kw):
        return d.rewrite_directory(listing, lambda b: (d.directory_sort_order(b, **kw), None),
                                   "sort the directory")

    def test_sort_changes_only_the_directory(self):
        listing = self.disk([f("ZED", 1), f("NOTES", 2, type=0x81), f("ALPHA", 3), f("BETA", 4)])
        ok, msg, backup = self.sort(listing)
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(), ["ALPHA", "BETA", "ZED", "NOTES"])
        new, blk = _bytes(self.image), d._blk(18, 1) * 256
        self.assertEqual(new[:blk], self.original[:blk])
        self.assertEqual(new[blk + 256:], self.original[blk + 256:])
        info = d.analyze_image(new)                       # each entry still finds its file
        self.assertEqual([x["max_track"] for x in info["files"]], [3, 4, 1, 2])
        self.assertTrue(os.path.isfile(backup))
        self.assertEqual(self.commands()[-1], "I")

    def test_already_sorted_writes_nothing(self):
        listing = self.disk([f("A", 1), f("B", 2), f("C", 3, type=0x81)])
        ok, msg, backup = self.sort(listing)
        self.assertEqual((ok, msg, backup, self.writes()), (True, "unchanged", None, []))

    def test_full_directory_of_144_files(self):
        files = [f(f"F{n:03d}", 1 + n % 17, n // 17) for n in range(144)][::-1]  # tracks 1–17
        listing = self.disk(files)
        ok, msg, _ = self.sort(listing)
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(), [f"F{n:03d}" for n in range(144)])
        self.assertEqual(len(self.writes()), 18)

    def test_fb_first_then_sorted(self):
        listing = self.disk([f("ZED", 1), f("FB", 2), f("ALPHA", 3)])
        ok, msg, _ = self.sort(listing, first=[b"FB"])
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(), ["FB", "ALPHA", "ZED"])

    def test_refusal_writes_nothing(self):
        listing = self.disk([f("B", 1), f("A", 2)])
        ok, msg, backup = d.rewrite_directory(listing, lambda b: (None, "no thanks"), "test")
        self.assertEqual((ok, msg, backup, self.writes()), (False, "no thanks", None, []))

    def test_disk_changed_since_the_listing(self):
        self.disk([f("B", 1), f("A", 2)])
        ok, msg, _ = self.sort([(1, "b", "prg"), (1, "c", "prg")])
        self.assertFalse(ok)
        self.assertIn("doesn't match", msg)
        self.assertEqual(self.writes(), [])

    def test_write_protected_leaves_the_disk_alone(self):
        listing = self.disk([f("B", 1), f("A", 2)])
        self.scenario(disk=self.image, write_protected=True)
        ok, msg, backup = self.sort(listing)
        self.assertFalse(ok)
        self.assertIn("26, WRITE PROTECT ON", msg)
        self.assertEqual(_bytes(self.image), self.original)
        self.assertTrue(os.path.isfile(backup))

    def test_restore_undoes_a_sort(self):
        listing = self.disk([f("C", 1), f("B", 2), f("A", 3)])
        ok, _, backup = self.sort(listing)
        self.assertTrue(ok)
        self.assertIsNone(d.restore_directory_backup(backup))
        self.assertEqual(_bytes(self.image), self.original)

    def test_two_rewrites_in_one_second_keep_both_backups(self):
        listing = self.disk([f("C", 1), f("B", 2), f("A", 3)])
        _, _, first = self.sort(listing, sort=False, first=[b"B"])
        listing = [listing[1], listing[0], listing[2]]
        _, _, second = self.sort(listing)
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(first) and os.path.isfile(second))


if __name__ == "__main__":
    unittest.main()
