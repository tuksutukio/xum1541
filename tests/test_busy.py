"""One drive operation at a time: DiskMasher's operation counter (_start_op/_end_op), the
busy indicator, the menu guard and the deferred refresh, on a real Tk root without drives."""
import gc
import threading
import time
import tkinter as tk
import unittest
from tkinter import ttk

from _common import FakeTools, d


class BusyApp(unittest.TestCase):
    """A Tk root with DiskMasher's busy machinery bound to it (no tests of its own)."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        app = self.app = self.root
        for name in ("_start_op", "_end_op", "_show_busy", "_when_free", "_close_stale",
                     "_build_busy_indicator", "_blink"):
            setattr(app, name, getattr(d.DiskMasher, name).__get__(app))
        app._ops, app._op_label, app._refresh_pending, app._local_ops = 0, "", False, 0
        app.IDLE_GREEN, app.BUSY_RED = d.DiskMasher.IDLE_GREEN, d.DiskMasher.BUSY_RED
        app.NO_DRIVE_GREY, app._have_drives = d.DiskMasher.NO_DRIVE_GREY, True
        app._build_busy_indicator(app)
        app._sv = tk.StringVar(master=app)
        app._set_status = app._sv.set
        app.bell = lambda: None
        self.refreshes = 0

        class Pane:
            pass
        app.cbm_pane, app.fs_pane = Pane(), Pane()
        app.cbm_pane._menubuttons = [ttk.Menubutton(app, text=t) for t in ("Read", "Drive")]
        app.fs_pane._write_btn = ttk.Button(app, text="Write")
        self.frozen = []
        app.fs_pane.set_frozen = self.frozen.append

        def refresh():
            if app._ops:
                app._refresh_pending = True
                return
            self.refreshes += 1
        app.cbm_pane.refresh = refresh

    def tearDown(self):
        # Let the test's worker threads finish first: a thread that drops the last
        # reference to a Tk object after the root is gone makes Tcl abort the whole
        # process at exit ("Tcl_AsyncDelete: async handler deleted by the wrong thread").
        for t in threading.enumerate():
            if t is not threading.main_thread():
                t.join(5)
        self.root.destroy()
        # The bound methods set on the root make a reference cycle, so only the cyclic GC
        # frees it, and that can run in any thread (a later test's worker): Tcl then aborts.
        # Break the cycle and collect here, in the thread that created the root.
        self.root.__dict__.clear()
        self.app = self.root = None
        gc.collect()

    def pump(self, until, timeout=5):
        """Run the Tk main loop until `until()` holds. A worker's after() call only reaches
        the Tk thread while it sits in mainloop (update() isn't enough), as in the app."""
        end = time.monotonic() + timeout

        def check():
            if until() or time.monotonic() > end:
                self.root.quit()
            else:
                self.root.after(10, check)
        self.root.after(10, check)
        self.root.mainloop()
        self.assertTrue(until())

    def busy(self):
        """The dot's colour, "red" or "green" (it flashes while red)."""
        fill = self.app._busy_dot.itemcget(self.app._busy_oval, "fill")
        return {d.DiskMasher.BUSY_RED: "red", d.DiskMasher.IDLE_GREEN: "green",
                d.DiskMasher.NO_DRIVE_GREY: "grey"}[fill]

    def states(self):
        bs = self.app.cbm_pane._menubuttons + [self.app.fs_pane._write_btn]
        return {str(b.cget("state")) for b in bs}


class Busy(BusyApp):
    def test_busy_while_running_free_after(self):
        go = threading.Event()
        self.app._start_op("Writing image", go.wait)
        self.assertEqual(self.states(), {"disabled"})
        self.assertEqual(self.busy(), "red")
        go.set()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.states(), {"normal"})
        self.assertEqual(self.busy(), "green")
        self.assertEqual(self.app._busy_dot.itemcget(self.app._busy_oval, "state"), "normal")

    def test_menu_refused_while_busy(self):
        go, ran = threading.Event(), []
        self.app._start_op("Formatting", go.wait)
        self.app._when_free(lambda: ran.append(1))()
        self.assertEqual(ran, [])
        self.assertIn("Drive busy (Formatting)", self.app._sv.get())
        go.set()
        self.pump(lambda: self.app._ops == 0)
        self.app._when_free(lambda: ran.append(1))()
        self.assertEqual(ran, [1])

    def test_refresh_waits_for_the_end(self):
        go = threading.Event()
        self.app._start_op("Sort", go.wait)
        self.app.cbm_pane.refresh()                 # e.g. from a done callback
        self.assertEqual(self.refreshes, 0)
        go.set()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.refreshes, 1)

    def test_nested_operation_keeps_the_bus_busy(self):
        # Cancel's recovery starts while the cancelled worker still runs.
        a, b = threading.Event(), threading.Event()
        self.app._start_op("Reading image", a.wait)
        self.app._start_op("Recovering the bus", b.wait)
        a.set()
        self.pump(lambda: self.app._ops == 1)
        self.assertEqual(self.states(), {"disabled"})
        self.assertEqual(self.busy(), "red")
        b.set()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.states(), {"normal"})

    def test_grey_when_no_drive_answered(self):
        def detect_finds_nothing():
            self.app._have_drives = False
        self.app._start_op("Detecting drives", detect_finds_nothing)
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.busy(), "grey")

    def test_local_operations_freeze_the_left_pane(self):
        a, b = threading.Event(), threading.Event()
        self.app._start_op("Formatting", a.wait)              # not local: pane stays free
        self.assertEqual(self.frozen, [])
        a.set()
        self.pump(lambda: self.app._ops == 0)
        self.app._start_op("Reading files", b.wait, local=True)
        self.app._start_op("Recovering the bus", lambda: None)  # nested, not local
        self.assertEqual(self.frozen, [True])
        b.set()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.frozen, [True, False])

    def test_new_action_closes_finished_windows(self):
        done, running = tk.Toplevel(self.app), tk.Toplevel(self.app)
        done._stale = True                           # set by a progress window's _finish
        self.app._when_free(lambda: None)()
        self.assertFalse(done.winfo_exists())
        self.assertTrue(running.winfo_exists())

    def test_failing_worker_still_frees_the_bus(self):
        def boom():
            raise RuntimeError("worker crashed")
        import sys, io
        err, sys.stderr = sys.stderr, io.StringIO()   # the thread's traceback
        try:
            self.app._start_op("Scratching", boom)
            self.pump(lambda: self.app._ops == 0)
        finally:
            sys.stderr = err
        self.assertEqual(self.states(), {"normal"})


class ResetBus(FakeTools, BusyApp):
    """Drive → Reset (and Cancel's recovery): reset, settle, detect, then three outcomes."""

    def setUp(self):
        FakeTools.setUp(self)
        BusyApp.setUp(self)
        for name in ("_reset_and_check", "_reset_bus"):
            setattr(self.app, name, getattr(d.DiskMasher, name).__get__(self.app))
        self.drives_set = []
        self.app.cbm_pane.set_drives = self.drives_set.append
        self.warned = []
        self._warn = d.messagebox.showwarning
        d.messagebox.showwarning = lambda *a, **k: self.warned.append(a)

    def tearDown(self):
        d.messagebox.showwarning = self._warn
        BusyApp.tearDown(self)
        FakeTools.tearDown(self)

    def test_reset_detects_and_refreshes(self):
        self.app._reset_bus()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual([c for c in self.calls() if c.startswith("cbmctrl")],
                         ["cbmctrl reset", "cbmctrl detect"])
        self.assertEqual(self.drives_set, [[("8", "1571")]])
        self.assertEqual(self.refreshes, 1)                  # after the operation
        self.assertIn("Reset — bus OK (8: 1571)", self.app._sv.get())
        self.assertEqual(self.busy(), "green")

    def test_fresh_operation_resets_first(self):
        self.app._start_op("Writing files", lambda: d.drive_status(), fresh=True)
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual([c for c in self.calls() if c.startswith("cbmctrl")],
                         ["cbmctrl reset", "cbmctrl status 8"])

    def test_no_drive_answers(self):
        self.scenario(detect="")
        self.app._reset_bus()
        self.pump(lambda: self.app._ops == 0)
        self.assertEqual(self.drives_set, [[]])
        self.assertEqual(len(self.warned), 1)
        self.assertEqual(self.refreshes, 0)
        self.assertEqual(self.busy(), "grey")


if __name__ == "__main__":
    unittest.main()
