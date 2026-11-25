"""The opencbm wrappers and drive protocols, run against fake tools (fake_opencbm.py):
success and failure detection, the erase job sequence, recovery from 74, and the disk
check's track-36 rule. No hardware involved."""
import os
import tempfile
import time

from _common import FakeTools, d, make_d64

FATAL_74 = ("[Info] writing x.prg -> x,p,w\n[Info] identified a 1571 drive\n"
            "[Fatal] could not open file for writing: 74,DRIVE NOT READY,18,00")


class Wrappers(FakeTools):
    def test_write_fatal_with_exit_0_is_a_failure(self):
        self.scenario(cbmwrite={"out": FATAL_74, "rc": 0})
        err = d.cbm_write_file("8", self.write_file("x.prg", b"\x01\x08"), "x")
        self.assertIn("74,DRIVE NOT READY", err)

    def test_write_ok(self):
        self.scenario(cbmwrite={"out": "[Info] writing x.prg -> x,p,w\n[Info] 00, OK,00,00"})
        self.assertIsNone(d.cbm_write_file("8", self.write_file("x.prg", b"\x01\x08"), "x"))

    def test_read_ok_needs_the_file(self):
        with tempfile.TemporaryDirectory() as dest:
            self.scenario(cbmread={"out": "[Info] reading matrix -> matrix.prg\n"
                                          "[Info] 00, OK,00,00", "creates": "matrix.prg"})
            self.assertIsNone(d.cbm_read_file("8", "matrix", dest))
            self.assertTrue(os.path.isfile(os.path.join(dest, "matrix.prg")))

    def test_read_opens_with_the_type(self):
        # A plain name opens as PRG: a SEQ/USR file then fails with 64 FILE TYPE MISMATCH
        # (real drive, 2026-11-25), so the type goes into the name.
        with tempfile.TemporaryDirectory() as dest:
            self.scenario(cbmread={"out": "[Info] 00, OK,00,00\n[Info] reading x -> x.seq",
                                   "creates": "x.seq"})
            for ftype, arg in (("seq", "x,s"), ("usr<", "x,u"), ("*prg", "x,p"),
                               ("rel", "x"), (None, "x")):
                self.assertIsNone(d.cbm_read_file("8", "x", dest, ftype=ftype))
                self.assertEqual(self.calls()[-1], f"cbmread 8 {arg}")

    def test_type_char(self):
        self.assertEqual([d.type_char(t) for t in ("prg", "SEQ", "usr<", "*seq", "rel", "del")],
                         ["p", "s", "u", "s", "r", None])

    def test_read_says_ok_but_writes_nothing(self):
        with tempfile.TemporaryDirectory() as dest:
            self.scenario(cbmread={"out": "[Info] reading matrix -> matrix.prg"})
            self.assertIn("wrote no file", d.cbm_read_file("8", "matrix", dest))

    def test_read_error_status_with_exit_0(self):
        with tempfile.TemporaryDirectory() as dest:
            self.scenario(cbmread={"out": "[Warning] file copy ended with error status: "
                                          "67,ILLEGAL TRACK OR SECTOR,36,00"})
            self.assertIn("67,ILLEGAL", d.cbm_read_file("8", "crosses36", dest))

    def test_drive_status(self):
        self.scenario(status=["00, ok,00,00", "74,drive not ready,00,00"])
        self.assertEqual(d.drive_status()[0], 0)
        self.assertEqual(d.drive_status(), (74, "74,drive not ready,00,00"))

    def test_detect_outcomes(self):
        self.scenario(detect=" 8: 1571\n 9: 1571")
        self.assertEqual(d.parse_detect(d.cbm_detect()), [("8", "1571"), ("9", "1571")])
        self.scenario(detect="")                     # no drive: empty, not an error
        self.assertEqual(d.cbm_detect(), "")

    def test_dir_74_is_text_not_an_error(self):
        self.scenario(dir="74,drive not ready,00,00")
        self.assertEqual(d._cbm_dir_once("9"), "74,drive not ready,00,00")

    def test_run_bounded_stops_a_hung_tool(self):
        self.scenario(hang={"cbmctrl": 30})
        t0 = time.monotonic()
        self.assertIsNone(d._run_bounded(["cbmctrl", "detect"], 1))
        self.assertLess(time.monotonic() - t0, 10)


class Recovery74(FakeTools):
    def test_dir_74_bumps_seeks_initialises_and_reads_again(self):
        self.scenario(dir=["74,drive not ready,00,00", '0 ."back" 01 2a\n664 blocks free.'])
        self.assertIn('"back"', d.cbm_dir())
        self.assertEqual(self.jobs(), [(0xC0, 1), (0xB0, 18)])
        self.assertEqual(self.commands(), ["I"])

    def test_dir_ok_does_nothing_extra(self):
        d.cbm_dir()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.commands(), [])

    def test_other_drive_is_not_touched(self):
        self.scenario(dir="74,drive not ready,00,00")
        d.cbm_dir("9")
        self.assertEqual(self.jobs(), [])


class Erase(FakeTools):
    def test_tracks_36_to_40(self):
        ok, msg = d.erase_tracks(list(range(36, 41)))
        self.assertTrue(ok, msg)
        self.assertEqual(self.mem(0x500, len(d.ERASE_CODE)), d.ERASE_CODE)
        jobs = self.jobs()
        self.assertEqual(jobs[0], (0xC0, 1))                       # bump first
        self.assertEqual(jobs[1:6], [(0xE0, t) for t in range(36, 41)])
        self.assertEqual(jobs[6:], [(0xB0, 18)])                   # park on 18 at the end
        self.assertEqual(self.commands(), ["U0>M0", "I"])          # 1571: 1541 mode first

    def test_whole_disk_order(self):
        ok, _ = d.erase_tracks(list(range(1, 41)))
        self.assertTrue(ok)
        self.assertEqual([t for c, t in self.jobs() if c == 0xE0], list(range(1, 41)))

    def test_write_protected_stops_at_once(self):
        self.scenario(write_protected=True)
        ok, msg = d.erase_tracks(list(range(1, 41)))
        self.assertFalse(ok)
        self.assertIn("write protected (nothing was erased)", msg)
        self.assertEqual([j for j in self.jobs() if j[0] == 0xE0], [(0xE0, 1)])

    def test_1541_skips_the_mode_switch(self):
        self.scenario(detect=" 8: 1541")
        self.assertTrue(d.erase_tracks([36])[0])
        self.assertNotIn("U0>M0", self.commands())

    def test_other_drives_are_refused_before_any_upload(self):
        self.scenario(detect=" 8: 1581")
        ok, msg = d.erase_tracks([36])
        self.assertFalse(ok)
        self.assertIn("1581", msg)
        self.assertFalse(any(c.startswith("cbmctrl upload") for c in self.calls()))

    def test_failed_job(self):
        self.scenario(job_results={"e0": 0x05})
        ok, msg = d.erase_tracks([36, 37])
        self.assertFalse(ok)
        self.assertIn("track 36", msg)

    def test_cancel_before_start(self):
        import threading
        ev = threading.Event()
        ev.set()
        self.assertEqual(d.erase_tracks([36], cancel_event=ev), (False, "cancelled"))
        self.assertEqual(self.jobs(), [])


class FullRead(FakeTools):
    def test_reset_after_d64copy(self):
        img = self.write_file("full.d64", make_d64())
        self.scenario(d64copy={"full": {"image": img, "out": "683 blocks copied."}})
        dest = os.path.join(self.state, "out.d64")
        self.assertIsNone(d.read_disk_to_file(dest, 35))
        self.assertEqual(self.calls()[-1], "cbmctrl reset")

    def test_no_reset_after_cancel(self):
        import threading
        img = self.write_file("full.d64", make_d64())
        self.scenario(d64copy={"full": {"image": img, "out": "683 blocks copied."}})
        ev = threading.Event()
        orig = d.stream_cmd

        def cancelled_during(cmd, on_text, proc_store=None):
            rc = orig(cmd, on_text, proc_store)
            ev.set()                       # Cancel pressed while d64copy ran
            return rc
        d.stream_cmd = cancelled_during
        try:
            d.read_disk_to_file(os.path.join(self.state, "out.d64"), 35, cancel_event=ev)
        finally:
            d.stream_cmd = orig
        self.assertTrue(self.calls()[-1].startswith("d64copy"))


class DiskCheck(FakeTools):
    """probe_disk's track-36 rule (CLAUDE.md "Track 36 in probe_disk")."""

    def setUp(self):
        super().setUp()
        self.t18 = self.write_file("t18.d64", make_d64(tracks=40, name="PROBE",
                                                       ext_bam="dolphin"))

    def check(self, t36_image, t36_out):
        self.scenario(d64copy={
            "18": {"image": self.t18, "out": "19 blocks copied."},
            "36": {"image": t36_image, "out": t36_out}})
        return d.probe_disk()

    def test_35_track_disk(self):
        r = self.check(self.write_file("t36.d64", bytes(196608)),
                       "[Fatal] drive 09 (1571): 21,READ ERROR,82,00")
        self.assertEqual((r["error"], r["t36_checked"], r["t36_ok"], r["t36_readable"],
                          r["t36_partial"]), (None, True, 0, False, False))
        self.assertEqual(r["disk_name"], "PROBE")
        self.assertEqual(r["ext_bam"], "DolphinDOS-style")

    def test_40_track_disk_all_sectors_read(self):
        data = bytearray(196608)
        data[d._blk(36, 0) * 256] = 0x42                           # some data on 36
        r = self.check(self.write_file("t36.d64", bytes(data)), "17 blocks copied.")
        self.assertEqual((r["t36_ok"], r["t36_readable"], r["t36_data"]), (17, True, True))

    def test_decayed_40_track_disk_is_partial(self):
        img = bytearray(make_d64(tracks=40, errors={(36, s): 5 for s in range(9)}))
        r = self.check(self.write_file("t36.d64", bytes(img)),
                       "[Warning] giving up...\n8 blocks copied.")
        self.assertEqual((r["t36_ok"], r["t36_readable"], r["t36_partial"]), (8, False, True))

    def test_reset_after_the_last_d64copy(self):
        # d64copy can leave a 1571 at 2 MHz under its 1541-mode DOS: file writes then fail
        # with 74 until a reset (drive 8, 2026-11-25).
        self.check(self.write_file("t36.d64", bytes(196608)), "")
        calls = self.calls()
        last_copy = max(i for i, c in enumerate(calls) if c.startswith("d64copy"))
        self.assertIn("cbmctrl reset", calls[last_copy + 1:])

    def test_track_18_unreadable(self):
        self.scenario(d64copy={"18": {"out": "[Fatal] drive 08 (1571): 74,DRIVE NOT READY"}})
        self.assertIn("track (18)", d.probe_disk()["error"])

    def test_track_36_timeout_is_not_a_35_track_verdict(self):
        self.scenario(d64copy={"18": {"image": self.t18, "out": "19 blocks copied."}})
        # The track-36 read hangs: _run_bounded's watchdog returns None for it (the real
        # 30 s timeout is too slow for a test, so stand in for the watchdog directly).
        orig = d._run_bounded

        def short(cmd, timeout, proc_store=None):
            if cmd[0] == "d64copy" and "36" in cmd:
                return None                                         # watchdog fired
            return orig(cmd, timeout, proc_store)
        d._run_bounded = short
        try:
            r = d.probe_disk()
        finally:
            d._run_bounded = orig
        self.assertFalse(r["t36_checked"])
        self.assertFalse(r["t36_readable"])
