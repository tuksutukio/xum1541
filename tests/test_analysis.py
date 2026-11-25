"""analyze_image / describe_image on synthetic .d64 images: the checks that decide whether
Sort and FB-ify may rewrite a disk."""
import unittest

from _common import d, make_d64


def prg(name, *sectors, **kw):
    return dict(name=name, sectors=list(sectors), **kw)


class Sizes(unittest.TestCase):
    def test_all_d64_sizes(self):
        for tracks, err, size in ((35, False, 174848), (35, True, 175531),
                                  (40, False, 196608), (40, True, 197376)):
            img = make_d64(tracks=tracks, errors={} if err else None)
            self.assertEqual(len(img), size)
            self.assertEqual(d.analyze_image(img)["tracks"], tracks)

    def test_image_formats_table_matches_geometry(self):
        b35, b40 = sum(d.SECTORS_PER_TRACK[:35]), sum(d.SECTORS_PER_TRACK[:40])
        self.assertEqual((b35, b40), (683, 768))
        for size in (b35 * 256, b35 * 257, b40 * 256, b40 * 257):
            self.assertIn(size, d.IMAGE_FORMATS)

    def test_other_sizes_raise(self):
        for size in (349696, 819200, 1000):
            with self.assertRaises(ValueError):
                d.analyze_image(bytes(size))


class CleanDisks(unittest.TestCase):
    def test_empty_disk(self):
        info = d.analyze_image(make_d64(name="BLANK"))
        self.assertEqual(info["disk_name"], "BLANK")
        self.assertEqual(info["files"], [])
        self.assertEqual(info["problems"], [])
        self.assertFalse(info["double_sided"])
        self.assertIsNone(info["ext_bam"])

    def test_ordinary_files(self):
        img = make_d64(files=[prg("GAME", (1, 0), (1, 1), (2, 0)),
                              prg("NOTES", (3, 0), type=0x81)])
        info = d.analyze_image(img)
        self.assertEqual([(f["name"], f["type"], f["blocks"], f["max_track"], f["problem"])
                          for f in info["files"]],
                         [("GAME", "PRG", 3, 2, None), ("NOTES", "SEQ", 1, 3, None)])
        self.assertEqual(info["problems"], [])

    def test_formatted_40_track_disk_has_no_data_beyond_35(self):
        # cbmforng fills fresh sectors with $4B $01 $01 ...: blank, not data.
        fill = bytes([0x4B] + [1] * 255)
        raw = {(36, s): fill for s in range(17)}
        info = d.analyze_image(make_d64(tracks=40, raw=raw))
        self.assertEqual(info["data_beyond_35"], [])
        self.assertEqual(info["problems"], [])


class Problems(unittest.TestCase):
    def problem(self, **kw):
        info = d.analyze_image(make_d64(files=[prg("F", (1, 0), (1, 1), **kw)]))
        return info["files"][0]["problem"], info["problems"]

    def test_splat_file(self):
        p, probs = self.problem(type=0x02)
        self.assertIn("splat", p)
        self.assertTrue(probs)

    def test_rel_and_geos_are_unsupported(self):
        self.assertIn("REL", self.problem(type=0x84)[0])
        self.assertIn("GEOS", self.problem(geos=True)[0])

    def test_broken_chain(self):
        self.assertIn("track 99", self.problem(link=(99, 0))[0])

    def test_chain_loop(self):
        self.assertEqual(self.problem(link=(1, 0))[0], "chain loops")

    def test_del_separator_is_listed_but_not_followed(self):
        info = d.analyze_image(make_d64(files=[prg("----", type=0x80)]))
        self.assertEqual(info["files"][0]["type"], "DEL")
        self.assertIn("unsupported", info["files"][0]["problem"])


class Beyond35(unittest.TestCase):
    def test_file_reaching_track_36_is_flagged(self):
        img = make_d64(tracks=40, files=[prg("BIG", (35, 0), (36, 0))])
        info = d.analyze_image(img)
        self.assertEqual(info["files"][0]["max_track"], 36)
        self.assertIn("beyond 35", info["files"][0]["problem"])
        self.assertEqual(info["data_beyond_35"], [])    # used by the file: not "outside"

    def test_data_outside_files_beyond_35(self):
        raw = {(37, 3): b"\x99" * 256}
        info = d.analyze_image(make_d64(tracks=40, raw=raw))
        self.assertEqual(info["data_beyond_35"], [37])
        self.assertEqual(info["problems"], [])          # kept in the image, not a stop

    def test_35_track_image_chain_into_36_is_invalid(self):
        info = d.analyze_image(make_d64(files=[prg("X", (35, 0))], raw={}))
        self.assertIsNone(info["files"][0]["problem"])
        img = make_d64(files=[prg("X", (35, 0), link=(36, 0))])
        self.assertIn("track 36", d.analyze_image(img)["files"][0]["problem"])


class ErrorTable(unittest.TestCase):
    def test_bad_sectors_counted_per_track(self):
        img = make_d64(errors={(5, 0): 5, (5, 3): 5, (20, 1): 2})
        self.assertEqual(d.analyze_image(img)["bad_sectors"], {5: 2, 20: 1})

    def test_read_error_inside_a_file(self):
        img = make_d64(files=[prg("F", (1, 0), (1, 1))], errors={(1, 1): 5})
        info = d.analyze_image(img)
        self.assertIn("read error in track 1 sector 1", info["files"][0]["problem"])

    def test_code_0_counts_as_ok(self):
        self.assertEqual(d.analyze_image(make_d64(errors={(3, 0): 0}))["bad_sectors"], {})


class BamFlags(unittest.TestCase):
    def test_double_sided_is_a_problem(self):
        info = d.analyze_image(make_d64(double_sided=True))
        self.assertTrue(info["double_sided"])
        self.assertTrue(any("double-sided" in p for p in info["problems"]))

    def test_extended_bam_hints(self):
        self.assertIn("SpeedDOS", d.analyze_image(make_d64(ext_bam="speed"))["ext_bam"])
        self.assertIn("DolphinDOS", d.analyze_image(make_d64(ext_bam="dolphin"))["ext_bam"])


class Describe(unittest.TestCase):
    def test_lines(self):
        img = make_d64(tracks=40, ext_bam="speed", files=[prg("A", (1, 0), type=0x02)],
                       errors={(2, 0): 5}, raw={(38, 0): b"\x01" * 256})
        lines = d.describe_image(d.analyze_image(img))
        self.assertEqual(lines[0], "40-track image, 1 file(s).")
        text = "\n".join(lines)
        for part in ("Extended BAM", "Read errors: track 2: 1 sector",
                     "track(s) 38", '"A": not closed properly'):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
