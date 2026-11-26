"""The stall watchdog: a tool that prints nothing for STALL_AFTER seconds is stopped
(SIGTERM first) and, where that's safe, run once more after a reset; every stall is
visible (output stream, STALL_HOOK, log, the operation's final status)."""
import os
import threading

from _common import FakeTools, _bytes, _text, d, make_d64
from test_busy import BusyApp


class Stall(FakeTools):
    def setUp(self):
        super().setUp()
        self._after, d.STALL_AFTER = d.STALL_AFTER, 1          # seconds, for the tests
        self.hooked = []
        self._hook, d.STALL_HOOK = d.STALL_HOOK, self.hooked.append
        self.count = d.STALL_COUNT

    def tearDown(self):
        d.STALL_AFTER, d.STALL_HOOK = self._after, self._hook
        super().tearDown()

    def image(self):
        return self.write_file("full.d64", make_d64(name="WORK"))

    def test_full_read_stalls_once_and_the_retry_works(self):
        img = self.image()
        self.scenario(hang_once={"d64copy": 4},
                      d64copy={"full": {"image": img, "out": "683 blocks copied."}})
        dest = os.path.join(self.state, "out.d64")
        self.assertIsNone(d.read_disk_to_file(dest, 35, on_progress=None))
        self.assertEqual(_bytes(dest), _bytes(img))
        self.assertEqual(d.STALL_COUNT - self.count, 1)
        self.assertEqual(len(self.hooked), 1)
        self.assertIn("attempt 2 of 2", self.hooked[0])
        copies = [c for c in self.calls() if c.startswith("d64copy")]
        self.assertEqual(len(copies), 2)
        calls = self.calls()
        self.assertIn("cbmctrl reset", calls[calls.index(copies[0]) + 1:calls.index(copies[1], calls.index(copies[0]) + 1)])
        log = _text(d.LOG_FILE)
        self.assertIn("stalled: no output for 1 s", log)
        self.assertIn("retry after a stall", log)

    def test_stalls_twice_gives_up(self):
        self.scenario(hang={"d64copy": 4})
        err = d.read_disk_to_file(os.path.join(self.state, "out.d64"), 35)
        self.assertIsNotNone(err)
        self.assertEqual(d.STALL_COUNT - self.count, 2)
        self.assertEqual(len([c for c in self.calls() if c.startswith("d64copy")]), 2)

    def test_no_retry_after_cancel(self):
        self.scenario(hang={"d64copy": 4})
        ev = threading.Event()
        threading.Timer(0.3, ev.set).start()
        d.read_disk_to_file(os.path.join(self.state, "out.d64"), 35, cancel_event=ev)
        self.assertEqual(len([c for c in self.calls() if c.startswith("d64copy")]), 1)
        self.assertEqual(self.hooked, [])

    def test_image_write_retries(self):
        img = self.image()
        disk = self.write_file("disk.d64", make_d64(name="OLD"))
        self.scenario(disk=disk, dos=True, hang_once={"d64copy": 4})
        self.assertIsNone(d.write_image_to_disk(img))
        self.assertEqual(_bytes(disk)[:174848], _bytes(img)[:174848])
        self.assertEqual(len(self.hooked), 1)

    def test_file_read_retry_deletes_the_half_read_file_first(self):
        dest = os.path.join(self.state, "dest")
        os.mkdir(dest)
        self.scenario(hang_mid_once={"cbmread": True},
                      cbmread={"creates": "game.prg", "out": "[Info] reading game -> game.prg"})
        self.assertIsNone(d.cbm_read_file("8", "game", dest))
        self.assertEqual(_bytes(os.path.join(dest, "game.prg")), b"\x01\x08")   # the full one
        self.assertEqual(len([c for c in self.calls() if c.startswith("cbmread")]), 2)

    def test_file_write_stall_stops_without_retry(self):
        self.scenario(hang={"cbmwrite": 4})
        path = self.write_file("game.prg", b"\x01\x08x")
        err = d.cbm_write_file("8", path, "game")
        self.assertIn("half-written", err)
        self.assertEqual(len([c for c in self.calls() if c.startswith("cbmwrite")]), 1)
        self.assertEqual(self.hooked, [])


class StallStatus(BusyApp):
    def test_operation_end_mentions_a_stall(self):
        def worker():
            d.STALL_COUNT += 1                    # as stream_cmd does on a stall
            self.app.after(0, lambda: self.app._set_status("Rebuild done."))
        self.app._start_op("Rebuild", worker)
        self.pump(lambda: self.app._ops == 0)
        self.assertIn("Rebuild done.", self.app._sv.get())
        self.assertIn("1 stall during this", self.app._sv.get())

    def test_no_stall_no_note(self):
        self.app._start_op("Rebuild", lambda: self.app.after(0, lambda: self.app._set_status("Done.")))
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.app._sv.get(), "Done.")
