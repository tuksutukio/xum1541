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

    def paste(self, pane, text, follow=True):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        pane.paste_locate(follow=follow)
        return [pane._entries[i][0] for i in pane.lb.curselection()]

    def test_paste_finds_and_selects(self):
        top = tempfile.mkdtemp()
        sub = os.path.join(top, "disks")
        os.mkdir(sub)
        for n in ("Game.d64", "other.prg", "readme.txt"):
            open(os.path.join(sub, n), "w").close()
        pane = d.FilesystemPane(self.root)
        pane._path = top
        pane._load()
        # A full path opens its folder and selects the file.
        self.assertEqual(self.paste(pane, os.path.join(sub, "Game.d64")), ["Game.d64"])
        self.assertEqual(pane._path, sub)
        # ⌘V: bare names, several lines, case ignored when there's no exact match.
        self.assertEqual(self.paste(pane, "game.d64\nother.prg\n", follow=False),
                         ["Game.d64", "other.prg"])
        # ⌘V never changes folder: a full path counts by its last part, here.
        pane._path = top
        pane._load()
        self.assertEqual(self.paste(pane, os.path.join(sub, "Game.d64"), follow=False), [])
        self.assertEqual(pane._path, top)
        self.assertEqual(self.paste(pane, os.path.join("/elsewhere", "disks"), follow=False), ["disks"])
        # ⇧⌘V with no path: says so, looks here.
        self.paste(pane, "disks", follow=True)
        self.assertIn("No path on the clipboard", self.app._sv.get())
        pane._path = sub
        pane._load()
        # A folder by full path is selected in its parent.
        self.assertEqual(self.paste(pane, sub + "/"), ["disks"])
        self.assertEqual(pane._path, top)
        # Missing, and present but not listed.
        pane._path = sub
        pane._load()
        self.assertEqual(self.paste(pane, "nothere.prg\nreadme.txt", follow=False), [])
        self.assertIn("Not found: nothere.prg.", self.app._sv.get())
        self.assertIn("not a Commodore file): readme.txt.", self.app._sv.get())
        # No such folder: the pane stays where it is.
        self.paste(pane, "/no/such/folder/x.d64")
        self.assertEqual(pane._path, sub)
        self.assertIn("No such folder", self.app._sv.get())

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
