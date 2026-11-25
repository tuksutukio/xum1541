"""Parsing opencbm output. Samples are real output, from opencbm-tools.md and the drive log."""
import unittest

from _common import d, make_d64


class CbmDir(unittest.TestCase):
    def test_normal_listing(self):
        raw = ('0 ."diskname        " 01 2a\n'
               '196  "krakout"          prg  \n'
               '1    "notes"            seq< \n'
               '468 blocks free.             \n'
               '00, ok,00,00')
        name, disk_id, entries, free = d.parse_cbm_dir(raw)
        self.assertEqual((name, disk_id, free), ("diskname", "01 2a", 468))
        self.assertEqual(entries, [(196, "krakout", "prg"), (1, "notes", "seq<")])

    def test_header_with_a_single_id_field(self):
        # The Lykia disk (2026-11-25): the name was lost before the parser accepted this.
        raw = '0 ."- lykia - c64   " 2022 \n9    "entrance"         prg  \n0 blocks free.'
        name, disk_id, entries, free = d.parse_cbm_dir(raw)
        self.assertEqual((name, disk_id, free), ("- lykia - c64", "2022", 0))
        self.assertEqual(entries, [(9, "entrance", "prg")])

    def test_error_instead_of_listing(self):
        name, disk_id, entries, free = d.parse_cbm_dir("74,drive not ready,00,00")
        self.assertEqual((name, entries, free), ("", [], None))


class Detect(unittest.TestCase):
    def test_two_drives(self):
        self.assertEqual(d.parse_detect(" 8: 1571 \n 9: 1571 \n"), [("8", "1571"), ("9", "1571")])

    def test_nothing(self):
        self.assertEqual(d.parse_detect(""), [])
        self.assertEqual(d.parse_detect(None), [])


class D64copyOutput(unittest.TestCase):
    def test_full_read(self):
        out = "35: *****************       100%   683/683\n683 blocks copied."
        self.assertEqual(d.blocks_copied(out, 35), (683, 683))
        self.assertEqual(d.blocks_copied(out, 40), (683, 768))

    def test_partial_read_gives_up_with_exit_0(self):
        out = ("[Warning] read error: 24/00: 5\n36: ?----------------  5%  1/17"
               "[Warning] giving up...\n8 blocks copied.")
        self.assertEqual(d.blocks_copied(out, 35)[0], 8)

    def test_fatal_has_no_count(self):
        out = "[Fatal] drive 09 (1571): 21,READ ERROR,82,00"
        self.assertEqual(d.blocks_copied(out, 35), (None, 683))

    def test_progress_line(self):
        m = d._D64COPY_PROGRESS.search("18: *********-*********      94%    18/19")
        self.assertEqual(m.groups(), ("18", "94", "18", "19"))


class OpencbmErrors(unittest.TestCase):
    def test_failures(self):
        for out in (
            "[Info] writing x.prg -> x,p,w\n[Info] identified a 1571 drive\n"
            "[Fatal] could not open file for writing: 74,DRIVE NOT READY,18,00",
            "[Fatal] could not open file for writing: 63, FILE EXISTS",
            "[Warning] file copy ended with error status: 67,ILLEGAL TRACK OR SECTOR,36,00\n"
            "[Warning] error reading crosses36",
        ):
            self.assertIsNotNone(d._opencbm_error(out), out)

    def test_successes(self):
        for out in (
            "[Info] reading matrix -> matrix.prg\n[Info] identified a 1571 drive\n"
            ".\\|/-.\\|/-[Info] 00, OK,00,00",
            "[Warning] could not identify drive, using no turbo\n[Info] 00, OK,00,00",
            "",
        ):
            self.assertIsNone(d._opencbm_error(out), out)


class SmallHelpers(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(d._safe_filename('a/b:c*?"<>|\\d'), "a_b_c_______d")

    def test_fmt_size(self):
        self.assertEqual([d._fmt_size(n) for n in (5, 2048, 3 * 1024 ** 2)],
                         ["5 B", "2 KB", "3.0 MB"])

    def test_blk(self):
        self.assertEqual(d._blk(1, 0), 0)
        self.assertEqual(d._blk(18, 0), 357)
        self.assertEqual(d._blk(36, 0), 683)


if __name__ == "__main__":
    unittest.main()


class FormatHeader(unittest.TestCase):
    """Pre-format before Write image uses the image's own name and ID (the ID goes into
    every sector header, which the write can't change)."""

    def header(self, name=b"GAMES", disk_id=b"2A"):
        img = bytearray(make_d64())
        off = d._blk(18, 0) * 256
        img[off + 0x90:off + 0xA0] = name.ljust(16, b"\xa0")
        img[off + 0xA2:off + 0xA4] = disk_id
        return d.image_format_header(bytes(img))

    def test_from_the_image(self):
        self.assertEqual(self.header(), ("GAMES", "2A"))

    def test_bytes_cbmforng_would_change_fall_back(self):
        self.assertEqual(self.header(name=b"A,B", disk_id=b"\xc1\xc2"), ("DISK", "01"))
        self.assertEqual(self.header(name=b"\x00"), ("DISK", "2A"))

    def test_short_data(self):
        self.assertEqual(d.image_format_header(b""), ("DISK", "01"))
