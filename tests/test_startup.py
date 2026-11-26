"""The start-up warning about old media and its "Don't show again" tickbox."""
import os
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk

from _common import d
from test_busy import BusyApp


class StartupWarning(BusyApp):
    def setUp(self):
        super().setUp()
        self._prefs = d.PREFS_FILE
        d.PREFS_FILE = os.path.join(tempfile.mkdtemp(), "prefs.ini")
        self.app.STARTUP_WARNING = d.DiskMasher.STARTUP_WARNING
        self.app._show_startup_warning = d.DiskMasher._show_startup_warning.__get__(self.app)

    def tearDown(self):
        d.PREFS_FILE = self._prefs
        super().tearDown()

    def dialog(self):
        self.app._show_startup_warning()
        dlg = [w for w in self.app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        widgets = dlg.winfo_children()[-1].winfo_children()
        tick = next(w for w in widgets if isinstance(w, ttk.Checkbutton))
        ok = next(w for w in widgets if isinstance(w, ttk.Button))
        return dlg, tick, ok

    def hidden(self):
        return d._load_prefs().getboolean("startup", "hide_warning", fallback=False)

    def test_ok_without_the_tick_shows_it_again(self):
        dlg, _, ok = self.dialog()
        self.assertIn("own risk", self.app.STARTUP_WARNING)
        ok.invoke()
        self.assertFalse(dlg.winfo_exists())
        self.assertFalse(self.hidden())

    def test_dont_show_again(self):
        dlg, tick, ok = self.dialog()
        tick.invoke()
        ok.invoke()
        self.assertFalse(dlg.winfo_exists())
        self.assertTrue(self.hidden())


if __name__ == "__main__":
    unittest.main()
