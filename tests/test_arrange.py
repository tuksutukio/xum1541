"""Sort and FB-ify end to end (DiskMasher._arrange_disk) on a real Tk root, against the
fake drive with a little DOS (fake_opencbm.py: dir, cbmwrite and scratch on the image)."""
import glob
import os

from _common import FakeTools, _bytes, d, make_d64
from test_busy import BusyApp

FB16 = os.path.join(d._HERE, "fb", "fb16")


def f(name, t, s=0, **kw):
    return dict(name=name, sectors=[(t, s)], **kw)


def file_data(img, name):
    """The bytes of a file in an image, by following its chain."""
    info = {x["name"]: x for x in d.analyze_image(img)["files"]}
    out, slot = b"", None
    t, s = 18, 1
    while t and slot is None:
        off = d._blk(t, s) * 256
        for k in range(8):
            e = img[off + 32 * k:off + 32 * k + 32]
            if e[2] and e[5:21].rstrip(b"\xa0").decode("latin-1") == name:
                slot = (e[3], e[4])
        t, s = img[off], img[off + 1]
    assert name in info and slot
    t, s = slot
    while True:
        off = d._blk(t, s) * 256
        blk = img[off:off + 256]
        if blk[0] == 0:
            return out + blk[2:blk[1] + 1]
        out += blk[2:]
        t, s = blk[0], blk[1]


class Arrange(FakeTools, BusyApp):
    def setUp(self):
        FakeTools.setUp(self)
        BusyApp.setUp(self)
        for name in ("_arrange_disk", "_cancel_and_recover", "_reset_and_check"):
            setattr(self.app, name, getattr(d.DiskMasher, name).__get__(self.app))
        # Keep the task dialog's closures (and their Tk variables) alive until tearDown:
        # dropped last in the worker thread, they'd be freed there, which Tcl doesn't allow
        # outside the main loop (the app always runs it; a test doesn't).
        self.dialogs = []
        make = d.DiskMasher._make_task_dialog.__get__(self.app)
        self.details = []

        def make_recording(*a):
            set_msg, *rest = make(*a)

            def recording(msg, fraction=None, detail=None):
                if detail:
                    self.details.append(detail)
                set_msg(msg, fraction=fraction, detail=detail)
            recording.enter_critical = set_msg.enter_critical
            self.dialogs.append((recording, *rest))
            return self.dialogs[-1]
        self.app._make_task_dialog = make_recording
        self.app.fs_pane._load = lambda: None
        self.errors = []
        self._err = d.messagebox.showerror
        d.messagebox.showerror = lambda *a, **k: self.errors.append(a)
        for p in glob.glob(os.path.join(d.DIR_BACKUP_DIR, "*.json")):
            os.remove(p)

    def tearDown(self):
        d.messagebox.showerror = self._err
        for t in d.threading.enumerate():
            if t is not d.threading.main_thread():
                t.join(5)
        for after_id in self.root.tk.call("after", "info"):
            self.root.after_cancel(after_id)
        self.dialogs.clear()
        BusyApp.tearDown(self)
        FakeTools.tearDown(self)

    def disk(self, files, **sc):
        self.image = self.write_file("disk.d64", make_d64(name="WORK", files=files))
        self.original = _bytes(self.image)
        self.scenario(disk=self.image, dos=True, **sc)
        info = d.analyze_image(self.original)
        return [(x["blocks"], x["name"].lower(), x["type"].lower()) for x in info["files"]]

    def names(self):
        return [x["name"] for x in d.analyze_image(_bytes(self.image))["files"]]

    def run_it(self, *args):
        self.app._arrange_disk(*args)
        self.pump(lambda: self.app._ops == 0, timeout=20)

    def test_sort(self):
        listing = self.disk([f("ZED", 1), f("NOTES", 2, type=0x81), f("ALPHA", 3)])
        self.run_it("Sort", listing, True, [], None)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.names(), ["ALPHA", "ZED", "NOTES"])
        self.assertIn("Sort done", self.app._sv.get())
        self.assertFalse(any(c.startswith(("cbmwrite", "d64copy")) for c in self.calls()))
        self.assertFalse(any(c.startswith("S") for c in self.commands()))

    def test_fbify_replaces_an_old_fb_and_keeps_the_order(self):
        listing = self.disk([f("ZED", 1), f("FB", 2), f("ALPHA", 3)])
        self.scenario(disk=self.image, dos=True, d64copy=self.probe())
        self.run_it("FB-ify", listing, False, [("FB", FB16)], None)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.names(), ["FB", "ZED", "ALPHA"])
        self.assertEqual(file_data(_bytes(self.image), "FB"), _bytes(FB16))
        calls = self.calls()
        scratch = calls.index("cbmctrl command 8 S0:FB")
        write = next(i for i, c in enumerate(calls) if c.startswith("cbmwrite"))
        self.assertIn("cbmctrl reset", calls[scratch:write])       # 74 without it (1541)

    def test_sort_with_fb_and_a_full_image_first(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)])
        copy = self.write_file("copy.d64", self.original)
        t36 = self.write_file("t36.d64", bytes(196608))
        self.scenario(disk=self.image, dos=True, d64copy={
            "18": {"image": copy, "out": "19 blocks copied."},
            "36": {"image": t36, "out": "[Fatal] 21,READ ERROR"},
            "full": {"image": copy, "out": "683 blocks copied."}})
        dest = os.path.join(self.state, "work_backup.d64")
        self.run_it("Sort", listing, True, [("FB", FB16)], dest)
        self.assertEqual(self.errors, [])
        self.assertEqual(_bytes(dest), self.original)
        self.assertEqual(self.names(), ["FB", "ALPHA", "ZED"])
        # One step per text: the step number goes up whenever the text changes.
        steps = list(dict.fromkeys(self.details))
        self.assertEqual([x.split(" · ")[0] for x in steps],
                         [f"Step {n} of 4" for n in range(1, 5)])
        self.assertEqual([x.split(" · ")[1] for x in steps],
                         ["Checking the disk", "Saving a full disk image", "Writing FB",
                          "Rewriting the directory"])
        calls = self.calls()
        image = max(i for i, c in enumerate(calls) if c.startswith("d64copy"))
        write = next(i for i, c in enumerate(calls) if c.startswith("cbmwrite"))
        self.assertLess(image, write)                    # the image comes before any change

    def test_existing_image_file_stops_before_any_change(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)])
        dest = self.write_file("taken.d64", b"keep me")
        self.run_it("Sort", listing, True, [], dest)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("already exists", self.errors[0][1])
        self.assertIn("not changed", self.errors[0][1])
        self.assertEqual(_bytes(self.image), self.original)
        self.assertEqual(_bytes(dest), b"keep me")

    def test_incomplete_image_stops_before_any_change(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)])
        copy = self.write_file("copy.d64", self.original)
        self.scenario(disk=self.image, dos=True, d64copy={
            "18": {"image": copy, "out": "19 blocks copied."},
            "36": {"out": "[Fatal] 21,READ ERROR"},
            "full": {"image": copy, "out": "680 blocks copied."}})
        self.run_it("Sort", listing, True, [], os.path.join(self.state, "x.d64"))
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(_bytes(self.image), self.original)

    def probe(self, **kw):
        copy = self.write_file("copy.d64", make_d64(name="WORK", **kw))
        return {"18": {"image": copy, "out": "19 blocks copied."},
                "36": {"out": "[Fatal] 21,READ ERROR"}}

    def test_fb_refuses_a_double_sided_disk(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)])
        self.scenario(disk=self.image, dos=True, d64copy=self.probe(double_sided=True))
        self.run_it("Sort", listing, True, [("FB", FB16)], None)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("double-sided", self.errors[0][1])
        self.assertEqual(_bytes(self.image), self.original)

    def test_sort_alone_needs_no_disk_check(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)])
        self.run_it("Sort", listing, True, [], None)
        self.assertFalse(any(c.startswith("d64copy") for c in self.calls()))

    def test_already_sorted(self):
        listing = self.disk([f("ALPHA", 1), f("ZED", 2)])
        self.run_it("Sort", listing, True, [], None)
        self.assertEqual(self.errors, [])
        self.assertIn("already in order", self.app._sv.get())
        self.assertEqual(_bytes(self.image), self.original)

    def test_write_protected_disk_reports_unchanged(self):
        listing = self.disk([f("ZED", 1), f("ALPHA", 2)], write_protected=True)
        self.run_it("Sort", listing, True, [], None)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("26, WRITE PROTECT ON", self.errors[0][1])
        self.assertEqual(_bytes(self.image), self.original)


if __name__ == "__main__":
    import unittest
    unittest.main()
