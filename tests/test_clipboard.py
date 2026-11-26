"""⌘C / ⇧⌘C in the panes: names, full paths, several items one per line."""
import os
import tempfile
import unittest

from _common import d
from test_busy import BusyApp


class Clipboard(BusyApp):
    def test_left_pane_names_and_paths(self):
        folder = tempfile.mkdtemp()
        for n in ("game.d64", "notes.prg"):
            open(os.path.join(folder, n), "w").close()
        pane = d.FilesystemPane(self.root)
        pane._path = folder
        pane._load()
        names = [e[0] for e in pane._entries]
        for n in ("game.d64", "notes.prg"):
            pane.lb.selection_set(names.index(n))
        pane.copy_selected(full=False)
        self.assertEqual(self.root.clipboard_get(), "game.d64\nnotes.prg")
        self.assertIn("Copied 2 names", self.app._sv.get())
        pane.copy_selected(full=True)
        self.assertEqual(self.root.clipboard_get(), os.path.join(folder, "game.d64") + "\n"
                         + os.path.join(folder, "notes.prg"))
        pane.lb.selection_clear(0, "end")
        pane.lb.selection_set(names.index(".."))
        pane.copy_selected(full=False)
        self.assertIn("Nothing selected", self.app._sv.get())

    def test_right_pane_names_as_shown(self):
        pane = d.CbmDirPane(self.root)
        pane._entries = [(34, "matrix", "prg"), (1, "notes", "seq")]
        for e in pane._entries:
            pane.lb.insert("end", e[1])
        pane.lb.selection_set(0)
        pane.copy_selected()
        self.assertEqual(self.root.clipboard_get(), "MATRIX")
        self.assertIn("Copied MATRIX", self.app._sv.get())


if __name__ == "__main__":
    unittest.main()
