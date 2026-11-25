"""move_to_first_slot / restore_directory_backup against the fake drive with a disk image:
only the directory changes, only changed sectors are written, and nothing is written when
a check fails."""
import glob
import os

from _common import FakeTools, _bytes, d, make_d64


def prg(name, t, s, **kw):
    return dict(name=name, sectors=[(t, s)], **kw)


class FirstFile(FakeTools):
    def disk(self, files):
        self.image = self.write_file("disk.d64", make_d64(name="WORK", files=files))
        self.original = _bytes(self.image)
        self.scenario(disk=self.image)
        info = d.analyze_image(self.original)
        return [(f["blocks"], f["name"].lower(), f["type"].lower()) for f in info["files"]]

    def names(self):
        return [f["name"] for f in d.analyze_image(_bytes(self.image))["files"]]

    def writes(self):
        return [(t, s) for c, t, s in self.jobs_ts() if c == 0x90]

    def backups(self):
        return glob.glob(os.path.join(d.DIR_BACKUP_DIR, "work-*.json"))

    def setUp(self):
        super().setUp()
        for p in glob.glob(os.path.join(d.DIR_BACKUP_DIR, "*.json")):
            os.remove(p)

    def test_last_file_becomes_first_others_keep_order(self):
        listing = self.disk([prg("ALPHA", 1, 0), prg("BETA", 2, 0), prg("GAME", 3, 0)])
        ok, msg, backup = d.move_to_first_slot(2, listing)
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(), ["GAME", "ALPHA", "BETA"])
        self.assertEqual(self.writes(), [(18, 1)])
        new = _bytes(self.image)
        dir_blk = d._blk(18, 1) * 256
        # Only the directory sector changed: data, BAM and everything else are identical.
        self.assertEqual(new[:dir_blk], self.original[:dir_blk])
        self.assertEqual(new[dir_blk + 256:], self.original[dir_blk + 256:])
        # The chains still point where they did.
        info = d.analyze_image(new)
        self.assertEqual([f["max_track"] for f in info["files"]], [3, 1, 2])
        self.assertTrue(os.path.isfile(backup))
        self.assertIn("LOAD", msg)
        self.assertEqual(self.commands()[-1], "I")

    def test_across_directory_sectors(self):
        files = [prg(f"F{n}", 1 + n, 0) for n in range(10)]
        listing = self.disk(files)
        ok, msg, _ = d.move_to_first_slot(9, listing)
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(), ["F9"] + [f"F{n}" for n in range(9)])
        self.assertEqual(sorted(self.writes()), [(18, 1), (18, 4)])

    def test_already_first_writes_nothing(self):
        listing = self.disk([prg("GAME", 1, 0), prg("B", 2, 0)])
        ok, msg, backup = d.move_to_first_slot(0, listing)
        self.assertTrue(ok)
        self.assertIn("already", msg)
        self.assertEqual((self.writes(), backup), ([], None))

    def test_only_closed_prg(self):
        for kw in ({"type": 0x81}, {"type": 0x02}):          # SEQ, splat PRG
            listing = self.disk([prg("A", 1, 0), prg("X", 2, 0, **kw)])
            ok, msg, _ = d.move_to_first_slot(1, listing)
            self.assertFalse(ok)
            self.assertIn("not a properly closed PRG", msg)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.backups(), [])

    def test_listing_mismatch_changes_nothing(self):
        self.disk([prg("ALPHA", 1, 0), prg("BETA", 2, 0)])
        ok, msg, _ = d.move_to_first_slot(1, [(1, "alpha", "prg"), (1, "other", "prg")])
        self.assertFalse(ok)
        self.assertIn("doesn't match", msg)
        self.assertEqual(self.writes(), [])

    def test_write_protected(self):
        listing = self.disk([prg("A", 1, 0), prg("B", 2, 0)])
        self.scenario(disk=self.image, write_protected=True)
        ok, msg, backup = d.move_to_first_slot(1, listing)
        self.assertFalse(ok)
        self.assertIn("26, WRITE PROTECT ON", msg)
        self.assertEqual(_bytes(self.image), self.original)
        self.assertTrue(os.path.isfile(backup))

    def test_cancel_before_writing(self):
        import threading
        listing = self.disk([prg("A", 1, 0), prg("B", 2, 0)])
        ev = threading.Event()
        ev.set()
        ok, msg, _ = d.move_to_first_slot(1, listing, cancel_event=ev)
        self.assertEqual((ok, msg), (False, "cancelled"))
        self.assertEqual(self.writes(), [])

    def test_cancel_wins_the_race_with_the_guard(self):
        listing = self.disk([prg("A", 1, 0), prg("B", 2, 0)])
        ok, msg, _ = d.move_to_first_slot(1, listing, enter_critical=lambda: False)
        self.assertEqual((ok, msg), (False, "cancelled"))
        self.assertEqual((self.writes(), self.backups()), ([], []))

    def test_locked_prg_is_fine(self):
        listing = self.disk([prg("A", 1, 0), prg("LOCKED", 2, 0, type=0xC2)])
        self.assertTrue(d.move_to_first_slot(1, listing)[0])
        self.assertEqual(self.names()[0], "LOCKED")

    def test_restore_backup(self):
        listing = self.disk([prg("A", 1, 0), prg("B", 2, 0), prg("C", 3, 0)])
        ok, _, backup = d.move_to_first_slot(2, listing)
        self.assertTrue(ok)
        self.assertNotEqual(_bytes(self.image), self.original)
        self.assertIsNone(d.restore_directory_backup(backup))
        self.assertEqual(_bytes(self.image), self.original)

    def test_restore_refuses_another_disk(self):
        listing = self.disk([prg("A", 1, 0), prg("B", 2, 0)])
        _, _, backup = d.move_to_first_slot(1, listing)
        other = self.write_file("other.d64", make_d64(name="OTHER"))
        self.scenario(disk=other)
        self.assertIn("different disk", d.restore_directory_backup(backup))
