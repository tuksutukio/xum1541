#!/opt/homebrew/bin/python3.13
"""
diskmasher — CBM disk writing tool
Commander-style split view: filesystem navigator | CBM device directory
"""

import configparser
import glob
import math
import os
import pty
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox

PREFS_FILE = os.path.expanduser("~/.config/diskmasher/prefs.ini")

def _load_prefs():
    cfg = configparser.ConfigParser()
    cfg.read(PREFS_FILE)
    return cfg

def _save_prefs(cfg):
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        cfg.write(f)

DRIVE = "8"
COLLECTION_ROOT = "/Volumes/ULTI" if os.path.exists("/Volumes/ULTI") else os.path.expanduser("~")
RELEVANT_EXTENSIONS = {".d64", ".d71", ".d81", ".prg", ".t64", ".tap", ".crt"}
TYPE_EXT = {"PRG": ".prg", "SEQ": ".seq", "USR": ".usr", "REL": ".rel"}

_HERE = os.path.dirname(os.path.abspath(__file__))
def _find_c1541():
    """Find c1541 from an installed VICE app bundle. Returns (path, env) or (None, {})."""
    for vice_app in sorted(glob.glob("/Applications/VICE*"), reverse=True):
        script = os.path.join(vice_app, "bin", "c1541")
        if os.path.exists(script):
            return script, {}
    return None, {}
_C1541, _C1541_ENV = _find_c1541()
FB_PATHS = {
    "C16/Plus4": [("FB", os.path.join(_HERE, "fb", "fb16"))],
    "C64":       [("FB", os.path.join(_HERE, "fb", "fb64"))],
    "C128":      [("FB", os.path.join(_HERE, "fb", "fb128"))],
    "VIC-20": [
        ("FB",      os.path.join(_HERE, "fb", "fb")),
        ("FB20",    os.path.join(_HERE, "fb", "fb20")),
        ("FB20-3K", os.path.join(_HERE, "fb", "fb20-3k")),
        ("FB20-8K", os.path.join(_HERE, "fb", "fb20-8k")),
        ("FB20-MC", os.path.join(_HERE, "fb", "fb20-mc")),
    ],
    "All": [
        ("FB",      os.path.join(_HERE, "fb", "fb")),
        ("FB16",    os.path.join(_HERE, "fb", "fb16")),
        ("FB20",    os.path.join(_HERE, "fb", "fb20")),
        ("FB20-3K", os.path.join(_HERE, "fb", "fb20-3k")),
        ("FB20-8K", os.path.join(_HERE, "fb", "fb20-8k")),
        ("FB20-MC", os.path.join(_HERE, "fb", "fb20-mc")),
        ("FB64",    os.path.join(_HERE, "fb", "fb64")),
        ("FB128",   os.path.join(_HERE, "fb", "fb128")),
    ],
}

IMAGE_FORMATS = {
    174848: ("35-track d64",            ".d64", False, False),  # label, ext, is_40track, is_d71
    175531: ("35-track d64 +errorbytes",".d64", False, False),  # 174848 + 683 error bytes
    196608: ("40-track d64",            ".d64", True,  False),
    197376: ("40-track d64 +errorbytes",".d64", True,  False),  # 196608 + 768 error bytes
    349696: ("d71 two-sided",           ".d71", False, True),
    351062: ("d71 +errorbytes",         ".d71", False, True),   # 349696 + 1366 error bytes
    819200: ("d81 (1581)",              ".d81", False, False),
}

def _bind_mac_multiselect(lb):
    """Make Command-click toggle items in extended-select Listbox (macOS convention)."""
    def _cmd_click(event):
        idx = lb.nearest(event.y)
        if idx in lb.curselection():
            lb.selection_clear(idx)
        else:
            lb.selection_set(idx)
        lb.event_generate("<<ListboxSelect>>")
        return "break"
    lb.bind("<Command-Button-1>", _cmd_click)


# --- CBM helpers ---

def cbm_detect():
    try:
        r = subprocess.run(["cbmctrl", "detect"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def cbm_dir(drive=DRIVE):
    try:
        r = subprocess.run(["cbmctrl", "dir", drive], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def parse_cbm_dir(raw):
    lines = raw.splitlines()
    entries, disk_name, disk_id, blocks_free = [], "", "", None
    for line in lines:
        m = re.match(r'\s*\d+\s+\."(.+?)"\s+(\S+)\s+(\S+)', line)
        if m:
            disk_name = m.group(1).strip()
            disk_id   = f"{m.group(2)} {m.group(3)}"
            continue
        m = re.match(r'\s*(\d+)\s+"(.+?)"\s+(\S+)', line)
        if m:
            entries.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
            continue
        m = re.match(r'\s*(\d+)\s+blocks free', line)
        if m:
            blocks_free = int(m.group(1))
    return disk_name, disk_id, entries, blocks_free


def cbm_read_file(drive, cbm_name, dest_dir, proc_store=None):
    """Read a single file from CBM drive into dest_dir. Returns error string or None."""
    try:
        proc = subprocess.Popen(
            ["cbmread", drive, cbm_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=dest_dir)
        if proc_store is not None:
            proc_store[0] = proc
        try:
            _, stderr = proc.communicate(timeout=300)
        finally:
            if proc_store is not None:
                proc_store[0] = None
        if "[Fatal]" in stderr or "FILE NOT FOUND" in stderr:
            return stderr.strip()
        return None
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Timeout"
    except Exception as e:
        return str(e)


def cbm_write_file(drive, local_path, cbm_name=None, proc_store=None):
    """Write a local file to CBM drive, optionally with a specific CBM filename."""
    cmd = ["cbmwrite", "-o", cbm_name, drive, local_path] if cbm_name else ["cbmwrite", drive, local_path]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc_store is not None:
            proc_store[0] = proc
        try:
            _, stderr = proc.communicate(timeout=300)
        finally:
            if proc_store is not None:
                proc_store[0] = None
        if proc.returncode != 0:
            return stderr.strip() or f"cbmwrite returned {proc.returncode}"
        return None
    except subprocess.TimeoutExpired:
        proc.kill()
        return "Timeout"
    except Exception as e:
        return str(e)


def _fmt_size(n):
    if n < 1024:    return f"{n} B"
    if n < 1024**2: return f"{n//1024} KB"
    return f"{n/1024**2:.1f} MB"

def _deselect_if_empty(lb, click_y, update_fn):
    idx = lb.nearest(click_y)
    if idx < 0:
        lb.selection_clear(0, tk.END)
        update_fn()
        return
    bbox = lb.bbox(idx)
    if bbox and click_y > bbox[1] + bbox[3]:
        lb.selection_clear(0, tk.END)
        update_fn()


def _safe_filename(name):
    """Strip characters unsafe for local filenames."""
    return re.sub(r'[/\\:*?"<>|]', '_', name)


# --- Left pane: filesystem navigator ---

class FilesystemPane(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._path = COLLECTION_ROOT
        self._entries = []
        self._build()
        self._load()

    def _build(self):
        self._hdr = tk.StringVar()
        ttk.Label(self, textvariable=self._hdr, anchor="w",
                  padding=(6, 3)).pack(fill="x")

        nav = ttk.Frame(self)
        nav.pack(fill="x")
        ttk.Button(nav, text="↑ Up",    command=self._go_up).pack(side="left", padx=4, pady=2)
        ttk.Button(nav, text="Refresh", command=self._load).pack(side="left", padx=2, pady=2)
        new_btn  = ttk.Menubutton(nav, text="New")
        new_menu = tk.Menu(new_btn, tearoff=False)
        new_menu.add_command(label="Directory", command=self._mkdir)
        new_menu.add_command(label="Image…",    command=self._new_image,
                             state="normal" if _C1541 else "disabled")
        new_btn["menu"] = new_menu
        new_btn.pack(side="left", padx=2, pady=2)
        ttk.Button(nav, text="Write →", command=lambda: self.event_generate("<<WriteRequest>>")).pack(side="right", padx=4, pady=2)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.lb = tk.Listbox(frame, font=("Menlo", 11),
                             selectmode="extended", exportselection=False,
                             activestyle="none", relief="flat", bd=0,
                             highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=vsb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.lb.bind("<Double-1>",        self._on_double)
        self.lb.bind("<Return>",          self._on_double)
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_sel())
        self.lb.bind("<Button-1>",        lambda e: self.after(0, lambda: _deselect_if_empty(self.lb, e.y, self._update_sel)))
        _bind_mac_multiselect(self.lb)

        self._stats_var = tk.StringVar()
        self._sel_var   = tk.StringVar()
        ttk.Label(self, textvariable=self._stats_var, anchor="w", padding=(6, 2)).pack(fill="x")
        ttk.Label(self, textvariable=self._sel_var,   anchor="w", padding=(6, 2)).pack(fill="x")

    def _update_sel(self):
        files = self.selected_files()
        if not files:
            self._sel_var.set("")
            return
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() in image_exts]
        prgs   = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() not in image_exts]
        if images and prgs:
            self._sel_var.set("Mixed selection — images and files can't be written together.")
        elif len(images) > 1:
            self._sel_var.set(f"{len(images)} images selected — write supports one at a time.")
        elif len(images) == 1:
            name, path = images[0]
            size = os.path.getsize(path)
            fmt  = IMAGE_FORMATS.get(size)
            if fmt:
                label, _, _, _ = fmt
                kb = size // 1024
                base_msg = f"1 image: {label}, {kb} kB"
                self._sel_var.set(base_msg)
                if _C1541:
                    self._query_image_blocks_free(path, base_msg)
            else:
                self._sel_var.set(f"1 image: unknown format ({size:,} bytes)")
        else:
            total_bytes  = sum(os.path.getsize(p) for _, p in prgs)
            total_blocks = math.ceil(total_bytes / 254)
            kb = total_bytes // 1024
            n  = len(prgs)
            self._sel_var.set(f"{n} file{'s' if n > 1 else ''} selected,  {total_blocks} blocks ({kb} kB)")
        self.event_generate("<<SelectionChanged>>")

    def _query_image_blocks_free(self, path, base_msg):
        self._img_query_path = path
        def _run():
            try:
                r = subprocess.run([_C1541, "-attach", path, "-dir"],
                                   capture_output=True, text=True, timeout=5)
                m = re.search(r'(\d+)\s+blocks?\s+free', r.stdout, re.IGNORECASE)
                if m:
                    blocks = int(m.group(1))
                    def _update():
                        if getattr(self, '_img_query_path', None) == path:
                            self._sel_var.set(f"{base_msg}  ·  {blocks} blocks free")
                    self.after(0, _update)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _load(self):
        self._hdr.set(self._path)
        self.lb.delete(0, tk.END)
        self._entries = []
        try:
            raw = sorted(os.scandir(self._path),
                         key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        visible = [(e, e.is_dir()) for e in raw
                   if not e.name.startswith(".")
                   and (e.is_dir() or os.path.splitext(e.name)[1].lower() in RELEVANT_EXTENSIONS)]
        col_w = max((len(e.name) + (1 if d else 0) for e, d in visible), default=20) + 2
        total_kb = 0
        for e, is_dir in visible:
            if is_dir:
                self._entries.append((e.name, True, e.path))
                self.lb.insert(tk.END, f"  {e.name + '/':<{col_w}}")
            else:
                sz_bytes = e.stat().st_size
                total_kb += sz_bytes // 1024
                sz = _fmt_size(sz_bytes)
                self._entries.append((e.name, False, e.path))
                self.lb.insert(tk.END, f"  {e.name:<{col_w}} {sz:>8}")
        files_only = [e for e, d in visible if not d]
        dirs_only  = [e for e, d in visible if d]
        parts = []
        if dirs_only:  parts.append(f"{len(dirs_only)} folder{'s' if len(dirs_only) > 1 else ''}")
        if files_only: parts.append(f"{len(files_only)} file{'s' if len(files_only) > 1 else ''}  ·  {total_kb} kB")
        self._stats_var.set("  ·  ".join(parts) if parts else "")
        self._sel_var.set("")

    def _on_double(self, _event):
        sel = self.lb.curselection()
        if not sel:
            return
        name, is_dir, path = self._entries[sel[0]]
        if is_dir:
            self._path = path
            self._load()
        elif _C1541 and os.path.splitext(name)[1].lower() in {".d64", ".d71", ".d81"}:
            self._open_image_view(path, name)

    def _open_image_view(self, path, name):
        dlg = tk.Toplevel(self)
        dlg.title(name)
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        x = self.winfo_rootx() + 40
        y = self.winfo_rooty() + 40
        dlg.geometry(f"480x340+{x}+{y}")

        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(side="bottom", pady=6)

        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        txt = tk.Text(frame, font=("Menlo", 11), state="disabled",
                      relief="flat", bd=0, highlightthickness=0, wrap="none")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        _noise = re.compile(
            r'^(OPENCBM:|D\d{2} disk image |Unit \d+ drive \d+:|Loading directory image|/\S+$)',
            re.IGNORECASE)

        def _run():
            try:
                r = subprocess.run([_C1541, "-attach", path, "-dir"],
                                   capture_output=True, text=True, timeout=10)
                lines = [l for l in r.stdout.splitlines() if not _noise.match(l)]
                output = "\n".join(lines).strip() or r.stderr.strip() or "(no output)"
            except Exception as e:
                output = str(e)
            def _fill():
                txt.configure(state="normal")
                txt.insert("1.0", output)
                txt.configure(state="disabled")
            try:
                dlg.after(0, _fill)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _go_up(self):
        parent = os.path.dirname(self._path)
        if parent != self._path:
            self._path = parent
            self._load()

    def _mkdir(self):
        dlg = tk.Toplevel(self)
        dlg.title("New folder")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width()  // 2 - 160
        y = self.winfo_rooty() + self.winfo_height() // 2 - 40
        dlg.geometry(f"320x80+{x}+{y}")
        ttk.Label(dlg, text="Folder name:", padding=(12, 10)).pack(side="left")
        name_var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=name_var, width=20)
        entry.pack(side="left", padx=(0, 8))
        entry.focus_set()
        def _do():
            name = name_var.get().strip()
            if not name:
                dlg.destroy()
                return
            path = os.path.join(self._path, name)
            try:
                os.mkdir(path)
                self._load()
            except OSError as e:
                messagebox.showerror("Mkdir failed", str(e))
            dlg.destroy()
        entry.bind("<Return>", lambda e: _do())
        ttk.Button(dlg, text="OK", command=_do).pack(side="left")

    def _new_image(self):
        dlg = tk.Toplevel(self)
        dlg.title("New disk image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # --- image type ---
        type_frame = ttk.LabelFrame(dlg, text="Image type", padding=(8, 4))
        type_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        type_var = tk.StringVar(value="d64")
        for fmt in ("d64", "d71", "d81"):
            rb = ttk.Radiobutton(type_frame, text=fmt, variable=type_var, value=fmt,
                                 command=lambda: _on_type())
            if fmt != "d64":
                rb.configure(state="disabled")
            rb.pack(side="left", padx=8)

        # --- disk identity ---
        id_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 4))
        id_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        ttk.Label(id_frame, text="Disk name:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        diskname_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=diskname_var, width=18).grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(id_frame, text="Disk ID:").grid(row=1, column=0, sticky="e", padx=4, pady=3)
        diskid_var = tk.StringVar(value="00")
        ttk.Entry(id_frame, textvariable=diskid_var, width=4).grid(row=1, column=1, sticky="w", padx=4)


        # --- filename ---
        out_frame = ttk.LabelFrame(dlg, text="Output", padding=(8, 4))
        out_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(out_frame, text="Filename:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        fname_var = tk.StringVar()
        fname_entry = ttk.Entry(out_frame, textvariable=fname_var, width=24)
        fname_entry.grid(row=0, column=1, sticky="w", padx=4)

        # bidirectional sync: diskname ↔ filename
        _syncing = [False]
        def _sync_name_to_file(*_):
            if _syncing[0]: return
            _syncing[0] = True
            raw = diskname_var.get().strip()
            if raw:
                safe = re.sub(r'[^a-zA-Z0-9_-]', '_', raw).lower()
                fname_var.set(f"{safe}.{type_var.get()}")
            _syncing[0] = False
        def _sync_file_to_name(*_):
            if _syncing[0]: return
            _syncing[0] = True
            raw = fname_var.get().strip()
            base = re.sub(r'\.[^.]+$', '', raw)
            if base and not diskname_var.get().strip():
                diskname_var.set(base[:16])
            _syncing[0] = False
        diskname_var.trace_add("write", _sync_name_to_file)
        fname_var.trace_add("write",    _sync_file_to_name)

        def _on_type():
            fmt = type_var.get()
            state = "normal" if fmt == "d64" else "disabled"
            track_35.configure(state=state)
            track_40.configure(state=state)
            # update filename extension
            cur = fname_var.get().strip()
            if cur:
                base = re.sub(r'\.[^.]+$', '', cur)
                _syncing[0] = True
                fname_var.set(f"{base}.{fmt}")
                _syncing[0] = False

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(4, 10))

        def _do():
            if not _C1541:
                messagebox.showerror("c1541 not found",
                    "VICE is not installed. Install VICE to create disk images.")
                return
            diskname = diskname_var.get().strip()[:16] or "unnamed"
            disk_id  = (diskid_var.get().strip()[:2] or "00").lower()
            fmt      = type_var.get()
            fname    = fname_var.get().strip()
            if not fname:
                fname = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', diskname).lower()}.{fmt}"
            if not fname.lower().endswith(f".{fmt}"):
                fname += f".{fmt}"
            out_path = os.path.join(self._path, fname)
            dlg.destroy()

            r = subprocess.run(
                [_C1541, "-format", f"{diskname.lower()},{disk_id}", fmt, out_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                messagebox.showerror("Create failed", r.stderr.strip() or r.stdout.strip())
                return
            self._load()

        ttk.Button(btn_frame, text="Create", command=_do).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

    def current_dir(self):
        return self._path

    def selected_files(self):
        """Return list of (name, path) for selected non-directory entries."""
        result = []
        for i in self.lb.curselection():
            name, is_dir, path = self._entries[i]
            if not is_dir:
                result.append((name, path))
        return result


# --- Right pane: CBM directory ---

class CbmDirPane(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self._entries = []
        self._build()

    def _build(self):
        self._hdr = tk.StringVar(value=f"{DRIVE}:  (not refreshed)")
        ttk.Label(self, textvariable=self._hdr, anchor="w",
                  padding=(6, 3)).pack(fill="x")

        nav = ttk.Frame(self)
        nav.pack(fill="x")

        read_btn  = ttk.Menubutton(nav, text="← Read")
        read_menu = tk.Menu(read_btn, tearoff=False)
        read_menu.add_command(label="Read file(s)", command=lambda: self.event_generate("<<ReadRequest>>"))
        read_menu.add_command(label="Read image",   command=lambda: self.event_generate("<<ReadImageRequest>>"))
        read_menu.add_command(label="Read to image…",
                              command=lambda: self.event_generate("<<ReadToImageRequest>>"),
                              state="disabled")
        read_btn["menu"] = read_menu
        self._read_menu = read_menu
        read_btn.pack(side="left", padx=4, pady=2)

        drive_btn  = ttk.Menubutton(nav, text="Drive")
        drive_menu = tk.Menu(drive_btn, tearoff=False)
        drive_menu.add_command(label="Detect",  command=lambda: self.event_generate("<<DetectRequest>>"))
        drive_menu.add_command(label="Refresh", command=lambda: self.event_generate("<<RefreshRequest>>"))
        drive_btn["menu"] = drive_menu
        drive_btn.pack(side="right", padx=4, pady=2)

        disk_btn  = ttk.Menubutton(nav, text="Disk")
        disk_menu = tk.Menu(disk_btn, tearoff=False)
        disk_menu.add_command(label="Format…",  command=lambda: self.event_generate("<<FormatRequest>>"))
        disk_menu.add_command(label="FB-ify…",  command=lambda: self.event_generate("<<FBifyRequest>>"))
        disk_menu.add_command(label="Sort…",    command=lambda: self.event_generate("<<SortRequest>>"))
        disk_btn["menu"] = disk_menu
        disk_btn.pack(side="right", padx=2, pady=2)

        file_btn  = ttk.Menubutton(nav, text="File")
        file_menu = tk.Menu(file_btn, tearoff=False)
        file_menu.add_command(label="Scratch…", command=lambda: self.event_generate("<<ScratchRequest>>"))
        file_btn["menu"] = file_menu
        file_btn.pack(side="right", padx=2, pady=2)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.lb = tk.Listbox(frame, font=("Menlo", 11),
                             selectmode="extended", exportselection=False,
                             activestyle="none", relief="flat", bd=0,
                             highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=vsb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_sel_status())
        self.lb.bind("<Button-1>",        lambda e: self.after(0, lambda: _deselect_if_empty(self.lb, e.y, self._update_sel_status)))
        _bind_mac_multiselect(self.lb)

        self._status     = tk.StringVar(value="")
        self._sel_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._status,    anchor="w", padding=(6, 2)).pack(fill="x")
        ttk.Label(self, textvariable=self._sel_status, anchor="w", padding=(6, 2)).pack(fill="x")

    def _apply_dir(self, raw):
        """Apply a raw cbm_dir result to the UI. Must be called on the main thread."""
        if raw.startswith("Error"):
            self._hdr.set(f"{DRIVE}:  error")
            self._status.set(raw)
            self._sel_status.set("")
            return
        disk_name, disk_id, entries, blocks_free = parse_cbm_dir(raw)
        self._hdr.set(f'{DRIVE}:  "{disk_name}"  {disk_id}')
        if blocks_free is not None:
            kb_free = blocks_free * 254 // 1024
            self._status.set(f"{len(entries)} files  ·  {blocks_free} blocks ({kb_free} kB) free")
        else:
            self._status.set("")
        self._entries = entries
        self.lb.delete(0, tk.END)
        for blk, name, ftype in entries:
            self.lb.insert(tk.END, f"  {blk:>4}  {name:<24}  {ftype}")
        self._sel_status.set("")

    def refresh(self):
        self._hdr.set(f"{DRIVE}:  refreshing…")
        self.lb.delete(0, tk.END)
        self._status.set("")
        self._entries = []

        def _run():
            raw = cbm_dir(DRIVE)
            self.after(0, lambda: self._apply_dir(raw))

        threading.Thread(target=_run, daemon=True).start()

    def _update_sel_status(self):
        sel = self.lb.curselection()
        if not sel:
            self._sel_status.set("")
            return
        entries = [self._entries[i] for i in sel]
        total_blocks = sum(blk for blk, _, _ in entries)
        kb = total_blocks * 254 // 1024
        n = len(entries)
        self._sel_status.set(f"{n} file{'s' if n > 1 else ''} selected,  {total_blocks} blocks ({kb} kB)")

    def selected_entries(self):
        return [self._entries[i] for i in self.lb.curselection()]

    def blocks_free(self):
        m = re.search(r'·\s*(\d+)\s*blocks', self._status.get())
        return int(m.group(1)) if m else None


# --- Main window ---

class DiskMasher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Diskmasher")
        self.geometry("1100x660+120+80")
        self.minsize(750, 420)
        self._build_toolbar()
        self._build_panes()
        self._build_statusbar()
        if _C1541 is None:
            def _on_detect_done():
                self.cbm_pane.refresh()
                self._set_status("VICE/c1541 not found — disk image features unavailable.")
            self._detect_drives(on_done=_on_detect_done)
        else:
            self._detect_drives(on_done=self.cbm_pane.refresh)
        self._restore_prefs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _restore_prefs(self):
        cfg = _load_prefs()
        if "window" in cfg:
            geo = cfg["window"].get("geometry")
            if geo:
                self.geometry(geo)
        if "panes" in cfg:
            sash = cfg["panes"].get("sash")
            if sash:
                try:
                    self.after(50, lambda: self._paned.sashpos(0, int(sash)))
                except Exception:
                    pass

    def _on_close(self):
        cfg = _load_prefs()
        if "window" not in cfg:
            cfg["window"] = {}
        if "panes" not in cfg:
            cfg["panes"] = {}
        cfg["window"]["geometry"] = self.wm_geometry()
        try:
            cfg["panes"]["sash"] = str(self._paned.sashpos(0))
        except Exception:
            pass
        _save_prefs(cfg)
        self.destroy()

    def _build_toolbar(self):
        pass

    def _build_panes(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        self._paned = paned
        paned.pack(fill="both", expand=True, padx=4, pady=4)
        self.fs_pane  = FilesystemPane(paned)
        self.cbm_pane = CbmDirPane(paned)
        paned.add(self.fs_pane,  weight=1)
        paned.add(self.cbm_pane, weight=0)
        self.fs_pane.bind("<<WriteRequest>>",        lambda e: self._write_to_cbm())
        self.fs_pane.bind("<<SelectionChanged>>",    lambda e: self._update_read_to_image_state())
        self.cbm_pane.bind("<<DetectRequest>>",  lambda e: self._detect_drives())
        self.cbm_pane.bind("<<RefreshRequest>>", lambda e: self._refresh_cbm())
        self.cbm_pane.bind("<<ReadRequest>>",   lambda e: self._read_from_cbm())
        self.cbm_pane.bind("<<FormatRequest>>", lambda e: self._format_disk())
        self.cbm_pane.bind("<<FBifyRequest>>",    lambda e: self._fbify_disk())
        self.cbm_pane.bind("<<SortRequest>>",     lambda e: self._sort_disk())
        self.cbm_pane.bind("<<ReadImageRequest>>",   lambda e: self._read_image_from_cbm())
        self.cbm_pane.bind("<<ReadToImageRequest>>", lambda e: self._read_to_image())
        self.cbm_pane.bind("<<ScratchRequest>>",     lambda e: self._scratch_files())

    def _update_read_to_image_state(self):
        if not _C1541:
            return
        left_files = self.fs_pane.selected_files()
        image_exts = {".d64", ".d71", ".d81"}
        enabled = (len(left_files) == 1
                   and os.path.splitext(left_files[0][0])[1].lower() in image_exts
                   and os.path.getsize(left_files[0][1]) in IMAGE_FORMATS)
        self.cbm_pane._read_menu.entryconfigure(2, state="normal" if enabled else "disabled")

    def _build_statusbar(self):
        self._sv = tk.StringVar(value="Ready.  |  Multi-select: shift-click or cmd-click")
        ttk.Label(self, textvariable=self._sv, anchor="w",
                  padding=(6, 3), relief="sunken").pack(fill="x", side="bottom")

    def _set_status(self, msg):
        self._sv.set(msg)

    def _make_spinner(self, message):
        """Small modal progress overlay with no cancel. Returns close_fn."""
        dlg = tk.Toplevel(self)
        dlg.title("")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  // 2 - 160
        y = self.winfo_y() + self.winfo_height() // 2 - 45
        dlg.geometry(f"320x90+{x}+{y}")
        ttk.Label(dlg, text=message, padding=(16, 12)).pack()
        pb = ttk.Progressbar(dlg, mode="indeterminate", length=280)
        pb.pack(padx=16, pady=(0, 12))
        pb.start(10)
        return dlg.destroy

    def _make_task_dialog(self, title, message):
        """Modal progress dialog with status label and Cancel button.
        Returns (set_msg_fn, close_fn, cancel_event, proc_store)."""
        cancel_event = threading.Event()
        proc_store   = [None]

        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        self.update_idletasks()
        x = self.winfo_x() + self.winfo_width()  // 2 - 200
        y = self.winfo_y() + self.winfo_height() // 2 - 55
        dlg.geometry(f"400x110+{x}+{y}")

        msg_var = tk.StringVar(value=message)
        ttk.Label(dlg, textvariable=msg_var, padding=(16, 10)).pack()
        pb = ttk.Progressbar(dlg, mode="indeterminate", length=360)
        pb.pack(padx=16)
        pb.start(10)

        def _cancel():
            cancel_event.set()
            if proc_store[0]:
                try: proc_store[0].terminate()
                except Exception: pass
            cancel_btn.configure(state="disabled")
            self._set_status("Cancelled — checking bus…")
            def _reset_and_probe():
                import time as _time
                _time.sleep(1)
                if proc_store[0]:
                    try: proc_store[0].kill()
                    except Exception: pass
                subprocess.run(["cbmctrl", "reset"], capture_output=True, timeout=5)
                # probe until responsive or 10s elapsed
                deadline = _time.monotonic() + 10
                responsive = False
                while _time.monotonic() < deadline:
                    try:
                        r = subprocess.run(["cbmctrl", "detect"],
                                           capture_output=True, timeout=3)
                        responsive = True
                        break
                    except Exception:
                        pass
                    _time.sleep(1)
                if responsive:
                    self.after(0, lambda: self._set_status("Cancelled — bus OK."))
                else:
                    self.after(0, lambda: (
                        self._set_status("xum1541 unresponsive — replug USB cable."),
                        messagebox.showerror("Bus unresponsive",
                            "xum1541 is not responding.\n\nReplug the USB cable to recover.")))
            threading.Thread(target=_reset_and_probe, daemon=True).start()

        cancel_btn = ttk.Button(dlg, text="Cancel", command=_cancel)
        cancel_btn.pack(pady=(6, 8))

        def set_msg(msg):
            self.after(0, lambda: msg_var.set(msg))

        return set_msg, dlg.destroy, cancel_event, proc_store

    def _detect_drives(self, on_done=None):
        self._set_status("Detecting drives…")
        close = self._make_spinner("Detecting drives…")
        def _run():
            result = cbm_detect()
            def _done():
                close()
                if not result:
                    self._set_status("No drives detected.")
                elif result.startswith("Error:"):
                    self._set_status(result)
                else:
                    self._set_status(f"Detected: {result}")
                if on_done:
                    on_done()
            self.after(0, _done)
        threading.Thread(target=_run, daemon=True).start()

    def _refresh_cbm(self):
        self._set_status("Refreshing…")
        close = self._make_spinner("Refreshing…")
        self.cbm_pane._hdr.set(f"{DRIVE}:  refreshing…")
        self.cbm_pane.lb.delete(0, tk.END)
        self.cbm_pane._status.set("")
        self.cbm_pane._entries = []
        def _run():
            raw = cbm_dir(DRIVE)
            def _done():
                close()
                self.cbm_pane._apply_dir(raw)
                self._set_status("Refreshed.")
            self.after(0, _done)
        threading.Thread(target=_run, daemon=True).start()

    def _scratch_files(self):
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Scratch", "Select one or more files in the right pane first.")
            return
        names = [name for _, name, _ in entries]
        confirm = messagebox.askyesno(
            "Scratch files",
            f"Scratch {len(names)} file{'s' if len(names) > 1 else ''} from drive {DRIVE}?\n\n"
            + "\n".join(names))
        if not confirm:
            return

        total = len(names)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Scratching files…", f"Scratching {total} file{'s' if total > 1 else ''}…")
        self._set_status(f"Scratching {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, name in enumerate(names, 1):
                if cancel_event.is_set():
                    break
                set_msg(f"[{i}/{total}]  {name}")
                proc = subprocess.Popen(
                    ["cbmctrl", "command", DRIVE, f"S:{name.upper()}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                proc_store[0] = proc
                proc.communicate(timeout=30)
                proc_store[0] = None
                if cancel_event.is_set():
                    break
                if proc.returncode != 0:
                    errors.append(name)
                else:
                    ok += 1
                    raw = cbm_dir(DRIVE)
                    self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))
            raw = cbm_dir(DRIVE)
            def done():
                close()
                self.cbm_pane._apply_dir(raw)
                if cancel_event.is_set():
                    self._set_status(f"Scratch cancelled after {ok} file{'s' if ok != 1 else ''}.")
                elif errors:
                    messagebox.showerror("Scratch errors", "Failed to scratch:\n" + "\n".join(errors))
                    self._set_status("Scratch completed with errors.")
                else:
                    self._set_status(f"Scratched {total} file{'s' if total > 1 else ''}.")
            self.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _read_from_cbm(self):
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Read", "Select one or more files in the right pane first.")
            return
        dest_dir = self.fs_pane.current_dir()
        total    = len(entries)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Reading files…", f"Reading from drive {DRIVE}…")
        self._set_status(f"Reading {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, (blk, name, ftype) in enumerate(entries, 1):
                if cancel_event.is_set():
                    break
                set_msg(f"[{i}/{total}]  {name}")
                err = cbm_read_file(DRIVE, name, dest_dir, proc_store)
                if cancel_event.is_set():
                    break
                if err:
                    errors.append(f"{name}: {err}")
                else:
                    ok += 1
                    self.after(0, self.fs_pane._load)
            def done():
                close()
                self.fs_pane._load()
                if errors:
                    messagebox.showerror("Read errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(f"Cancelled after reading {ok} file{'s' if ok != 1 else ''}.")
                else:
                    self._set_status(f"Read {ok} file(s) to {dest_dir}" +
                                     (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _read_to_image(self):
        left_files = self.fs_pane.selected_files()
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in left_files
                  if os.path.splitext(n)[1].lower() in image_exts
                  and os.path.getsize(p) in IMAGE_FORMATS]
        if len(images) != 1:
            messagebox.showinfo("Read to image",
                "Select exactly one recognised disk image in the left pane.")
            return
        entries = self.cbm_pane.selected_entries()
        if not entries:
            messagebox.showinfo("Read to image",
                "Select one or more files in the right pane first.")
            return

        img_name, img_path = images[0]
        total       = len(entries)
        need_blocks = sum(b for b, _, _ in entries)
        TYPE_CHAR   = {"PRG": "p", "SEQ": "s", "USR": "u", "REL": "r"}

        # Pre-flight: writable?
        if not os.access(img_path, os.W_OK):
            messagebox.showerror("Read to image",
                f"{img_name} is read-only.")
            return

        # Pre-flight: enough blocks free? (c1541 on a local file is fast)
        try:
            _r = subprocess.run([_C1541, "-attach", img_path, "-dir"],
                                capture_output=True, text=True, timeout=5)
            _m = re.search(r'(\d+)\s+blocks?\s+free', _r.stdout, re.IGNORECASE)
            img_free = int(_m.group(1)) if _m else None
        except Exception:
            img_free = None

        if img_free is not None and need_blocks > img_free:
            messagebox.showerror("Read to image",
                f"Not enough space: need {need_blocks} blocks, "
                f"image has {img_free} free.")
            return

        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Reading to image",
            f"Reading {total} file{'s' if total > 1 else ''} into {img_name}…")
        self._set_status(f"Reading to {img_name}…")

        def run():
            errors = []
            ok     = 0
            with tempfile.TemporaryDirectory() as tmp:
                for i, (blk, name, ftype) in enumerate(entries, 1):
                    if cancel_event.is_set():
                        break
                    set_msg(f"[{i}/{total}]  {name}")

                    per_dir = tempfile.mkdtemp(dir=tmp)
                    err = cbm_read_file(DRIVE, name, per_dir, proc_store)
                    if cancel_event.is_set():
                        break
                    if err:
                        errors.append(f"{name}: {err}")
                        continue

                    tmp_files = [f for f in os.listdir(per_dir)
                                 if os.path.isfile(os.path.join(per_dir, f))]
                    if not tmp_files:
                        errors.append(f"{name}: cbmread produced no file")
                        continue
                    local_file = os.path.join(per_dir, tmp_files[0])

                    type_char = TYPE_CHAR.get(ftype.lstrip("*").upper(), "p")
                    r2 = subprocess.run(
                        [_C1541, "-attach", img_path,
                         "-write", local_file, f"{name},{type_char}"],
                        capture_output=True, text=True, timeout=30)
                    if r2.returncode != 0:
                        errors.append(
                            f"{name}: {(r2.stderr.strip() or r2.stdout.strip() or 'c1541 error')}")
                    else:
                        ok += 1

            def done():
                close()
                self.after(0, self.fs_pane._update_sel)
                if errors:
                    messagebox.showerror("Read to image errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(
                        f"Cancelled after reading {ok} file{'s' if ok != 1 else ''} to image.")
                else:
                    self._set_status(
                        f"Read {ok} file{'s' if ok != 1 else ''} into {img_name}" +
                        (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _write_to_cbm(self):
        files = self.fs_pane.selected_files()
        if not files:
            messagebox.showinfo("Write", "Select one or more files in the left pane first.")
            return
        image_exts = {".d64", ".d71", ".d81"}
        images = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() in image_exts]
        prgs   = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() not in image_exts]
        if images and prgs:
            messagebox.showerror("Mixed selection",
                                 "Select either disk images or individual files, not both.")
            return
        if len(images) > 1:
            messagebox.showerror("One at a time", "Select only one disk image at a time.")
            return
        if images:
            self._write_image_to_cbm(images[0][1])
            return
        total = len(files)
        set_msg, close, cancel_event, proc_store = self._make_task_dialog(
            "Writing files…", f"Writing to drive {DRIVE}…")
        self._set_status(f"Writing {total} file{'s' if total > 1 else ''}…")

        def run():
            errors = []
            ok = 0
            for i, (name, path) in enumerate(files, 1):
                if cancel_event.is_set():
                    break
                cbm_name = os.path.splitext(name)[0].lower()[:16]
                set_msg(f"[{i}/{total}]  {name}")
                err = cbm_write_file(DRIVE, path, cbm_name, proc_store)
                if cancel_event.is_set():
                    break
                if err:
                    errors.append(f"{name}: {err}")
                else:
                    ok += 1
                    raw = cbm_dir(DRIVE)
                    self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))
            def done():
                close()
                self.cbm_pane.refresh()
                if errors:
                    messagebox.showerror("Write errors", "\n".join(errors))
                if cancel_event.is_set():
                    self._set_status(f"Cancelled after writing {ok} file{'s' if ok != 1 else ''}.")
                else:
                    self._set_status(f"Wrote {ok} file(s) to drive {DRIVE}" +
                                     (f" ({len(errors)} error(s))" if errors else "."))
            self.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _write_image_to_cbm(self, path):
        size     = os.path.getsize(path)
        fmt_info = IMAGE_FORMATS.get(size)
        if not fmt_info:
            messagebox.showerror("Unknown format",
                f"File size {size:,} bytes doesn't match any known disk image format.")
            return
        fmt_label, ext, is_40track, is_d71 = fmt_info
        basename = os.path.basename(path)

        dlg = tk.Toplevel(self)
        dlg.title("Write disk image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        info_frame = ttk.LabelFrame(dlg, text="Image", padding=(8, 4))
        info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Label(info_frame, text=f"File:    {basename}",  font=("Menlo", 10), anchor="w").pack(fill="x")
        ttk.Label(info_frame, text=f"Format:  {fmt_label}", font=("Menlo", 10), anchor="w").pack(fill="x")
        if is_d71:
            ttk.Label(info_frame, text="Requires 1571 drive.", font=("Menlo", 10),
                      foreground="orange", anchor="w").pack(fill="x")

        fmt_frame = ttk.LabelFrame(dlg, text="Pre-format disk", padding=(8, 4))
        fmt_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        prefmt_var = tk.BooleanVar(value=is_40track)
        ttk.Checkbutton(fmt_frame, text="Format disk before writing",
                        variable=prefmt_var, command=lambda: _toggle()).grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(fmt_frame, text="Name:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        dname_var = tk.StringVar(value=os.path.splitext(basename)[0].upper()[:16])
        dname_entry = ttk.Entry(fmt_frame, textvariable=dname_var, width=18)
        dname_entry.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(fmt_frame, text="ID:").grid(row=1, column=2, sticky="e", padx=(12, 4))
        did_var = tk.StringVar(value="01")
        did_entry = ttk.Entry(fmt_frame, textvariable=did_var, width=5)
        did_entry.grid(row=1, column=3, sticky="w", padx=4)

        def _toggle():
            s = "normal" if prefmt_var.get() else "disabled"
            dname_entry.configure(state=s)
            did_entry.configure(state=s)
        _toggle()

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(4, 10))

        def _do_write():
            do_fmt = prefmt_var.get()
            dname  = dname_var.get().strip() or "DISK"
            did    = did_var.get().strip() or "01"
            dlg.destroy()
            prog, pbar, close_btn, _append, proc_store = self._make_progress_window(f"Writing {basename}…")
            self._set_status(f"Writing {basename} to drive {DRIVE}…")

            def run():
                ok = True
                if do_fmt:
                    fmt_flag = ["-x"] if is_40track else []
                    fmt_cmd  = ["cbmforng"] + fmt_flag + ["-r", "2", DRIVE, f"{dname},{did}"]
                    self.after(0, lambda: _append(f"Pre-formatting drive {DRIVE}…\n"))
                    subprocess.run(["cbmctrl", "reset"], capture_output=True, timeout=5)
                    rc = self._pty_run(fmt_cmd, _append, proc_store)
                    if rc != 0:
                        self.after(0, lambda: _append("\nFormat failed — aborting write.\n"))
                        ok = False

                if ok:
                    if ext == ".d81":
                        copy_cmd = ["imgcopy", path, DRIVE]
                    elif is_d71:
                        copy_cmd = ["d64copy", "--two-sided", "-d", "1571", path, DRIVE]
                    elif is_40track:
                        copy_cmd = ["d64copy", "--end-track", "40", path, DRIVE]
                    else:
                        copy_cmd = ["d64copy", path, DRIVE]
                    self.after(0, lambda: _append(f"\nCopying image…\n"))
                    rc = self._pty_run(copy_cmd, _append, proc_store)
                    ok = rc == 0

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if ok:
                        _append("\nDone.\n")
                        prog.title("Write complete")
                        self._set_status(f"Wrote {basename} to drive {DRIVE}.")
                        self.after(1500, self.cbm_pane.refresh)
                    else:
                        _append("\nWrite failed.\n")
                        prog.title("Write failed")
                        self._set_status("Write failed.")
                    prog._finish()
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(btn_frame, text="Write", command=_do_write).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _read_image_from_cbm(self):
        hdr      = self.cbm_pane._hdr.get()
        m        = re.search(r'"(.+?)"', hdr)
        diskname = m.group(1).strip() if m else "disk"
        dest_dir = self.fs_pane.current_dir()

        dlg = tk.Toplevel(self)
        dlg.title("Read disk to image")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        info_frame = ttk.LabelFrame(dlg, text="Source", padding=(8, 4))
        info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        disk_lbl = hdr.split("  ", 1)[-1] if "  " in hdr else hdr
        ttk.Label(info_frame, font=("Menlo", 10), anchor="w",
                  text=f"Drive:    {DRIVE}\nDisk:     {disk_lbl}\nSave to:  {dest_dir}").pack(fill="x")

        out_frame = ttk.LabelFrame(dlg, text="Output", padding=(8, 4))
        out_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(out_frame, text="Filename:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        fname_var = tk.StringVar(value=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', diskname).lower()}.d64")
        fname_entry = ttk.Entry(out_frame, textvariable=fname_var, width=26)
        fname_entry.grid(row=0, column=1, sticky="w", padx=4)
        ext40_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(out_frame, text="40-track  (--end-track 40)",
                        variable=ext40_var).grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        ttk.Label(out_frame, text="Retries:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        retry_var = tk.IntVar(value=5)
        ttk.Spinbox(out_frame, from_=0, to=100, textvariable=retry_var,
                    width=6).grid(row=2, column=1, sticky="w", padx=4)

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(4, 10))

        def _do_read():
            fname = fname_var.get().strip() or f"{diskname}.d64"
            if not fname.lower().endswith((".d64", ".d71", ".d81")):
                fname += ".d64"
            out_path  = os.path.join(dest_dir, fname)
            is_40     = ext40_var.get()
            retries   = retry_var.get()
            dlg.destroy()
            prog, pbar, close_btn, _append, proc_store = self._make_progress_window(f"Reading disk to {fname}…")
            self._set_status(f"Reading drive {DRIVE} to {fname}…")

            def run():
                cmd = ["d64copy", "-r", str(retries)]
                if is_40:
                    cmd += ["--end-track", "40"]
                cmd += [DRIVE, out_path]
                self.after(0, lambda: _append(f"Running: {' '.join(cmd)}\n\n"))
                rc = self._pty_run(cmd, _append, proc_store)
                ok = rc == 0

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if ok:
                        _append(f"\nSaved: {out_path}\n")
                        prog.title("Read complete")
                        self._set_status(f"Saved {fname}.")
                        self.fs_pane._load()
                    else:
                        _append("\nRead failed.\n")
                        prog.title("Read failed")
                        self._set_status("Read failed.")
                    prog._finish()
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(btn_frame, text="Read image", command=_do_read).pack(side="left", padx=8)
        fname_entry.bind("<Return>", lambda e: _do_read())
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _make_progress_window(self, title):
        """Return (prog, pbar, close_btn, _append, proc_store) — progress Toplevel ready to use.
        proc_store is a list; _pty_run appends the Popen object so Cancel can kill it."""
        prog = tk.Toplevel(self)
        prog.title(title)
        prog.geometry("680x380")
        prog.minsize(500, 280)
        prog.transient(self)
        prog.resizable(True, True)

        pbar = ttk.Progressbar(prog, mode="indeterminate", length=400)
        pbar.pack(padx=6, pady=(6, 2), fill="x")
        pbar.start(50)

        proc_store = []

        btn_row = ttk.Frame(prog)
        btn_row.pack(side="bottom", pady=(2, 8))

        txt_frame = ttk.Frame(prog)
        txt_frame.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        txt = tk.Text(txt_frame, font=("Menlo", 10), state="disabled",
                      wrap="none", relief="flat", bd=0)
        vsb = ttk.Scrollbar(txt_frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _cancel():
            if proc_store:
                try:
                    proc_store[0].terminate()
                except Exception:
                    pass
            cancel_btn.configure(state="disabled")
            self._set_status("Cancelled — checking bus…")
            def _reset_and_probe():
                import time as _time
                _time.sleep(1)
                try:
                    if proc_store:
                        proc_store[0].kill()
                except Exception:
                    pass
                subprocess.run(["cbmctrl", "reset"], capture_output=True, timeout=5)
                deadline = _time.monotonic() + 10
                responsive = False
                while _time.monotonic() < deadline:
                    try:
                        subprocess.run(["cbmctrl", "status"],
                                       capture_output=True, timeout=2)
                        responsive = True
                        break
                    except Exception:
                        pass
                    _time.sleep(1)
                if responsive:
                    self.after(0, lambda: self._set_status("Cancelled — bus OK."))
                else:
                    self.after(0, lambda: (
                        self._set_status("xum1541 unresponsive — replug USB cable."),
                        messagebox.showerror("Bus unresponsive",
                            "xum1541 is not responding.\n\nReplug the USB cable to recover.")))
            threading.Thread(target=_reset_and_probe, daemon=True).start()

        cancel_btn = ttk.Button(btn_row, text="Cancel", command=_cancel)
        cancel_btn.pack(side="left", padx=6)
        close_btn = ttk.Button(btn_row, text="Close", state="disabled", command=prog.destroy)
        close_btn.pack(side="left", padx=6)

        prog.protocol("WM_DELETE_WINDOW", _cancel)

        def _append(text):
            txt.configure(state="normal")
            txt.insert(tk.END, text)
            txt.see(tk.END)
            txt.configure(state="disabled")

        def _finish():
            cancel_btn.configure(state="disabled")
            close_btn.configure(state="normal")

        prog._finish = _finish

        return prog, pbar, close_btn, _append, proc_store

    def _pty_run(self, cmd, _append, proc_store=None):
        """Run cmd via pty, streaming output through _append. Returns exit code. Call from thread."""
        import select as _select
        try:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(cmd, stdout=slave_fd, stderr=slave_fd,
                                    stdin=subprocess.DEVNULL, close_fds=True)
            os.close(slave_fd)
            if proc_store is not None:
                proc_store.append(proc)
            while True:
                try:
                    r, _, _ = _select.select([master_fd], [], [], 1.0)
                    if r:
                        chunk = os.read(master_fd, 4096)
                        if not chunk:
                            break
                        text = chunk.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
                        self.after(0, lambda t=text: _append(t))
                    elif proc.poll() is not None:
                        break
                except OSError:
                    break
            proc.wait()
            try:
                os.close(master_fd)
            except OSError:
                pass
            return proc.returncode
        except Exception as e:
            self.after(0, lambda: _append(f"\nError: {e}\n"))
            return 1

    def _format_disk(self):
        dlg = tk.Toplevel(self)
        dlg.title("Format disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # --- Tool selector ---
        tool_var = tk.StringVar(value="cbmforng")
        tool_frame = ttk.LabelFrame(dlg, text="Tool", padding=(8, 4))
        tool_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Radiobutton(tool_frame, text="cbmforng", variable=tool_var, value="cbmforng",
                        command=lambda: _on_tool_change()).pack(side="left", padx=8)
        ttk.Radiobutton(tool_frame, text="cbmformat", variable=tool_var, value="cbmformat",
                        command=lambda: _on_tool_change()).pack(side="left", padx=8)

        # --- Disk name / ID ---
        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 4))
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        ttk.Label(info_frame, text="Name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value="DISKNAME")
        name_entry = ttk.Entry(info_frame, textvariable=name_var, width=18)
        name_entry.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(info_frame, text="ID:").grid(row=0, column=2, sticky="e", padx=(12, 4))
        id_var = tk.StringVar(value="01")
        id_entry = ttk.Entry(info_frame, textvariable=id_var, width=5)
        id_entry.grid(row=0, column=3, sticky="w", padx=4)

        # --- Options ---
        opt_frame = ttk.LabelFrame(dlg, text="Options", padding=(8, 4))
        opt_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        ext_var      = tk.BooleanVar(value=False)
        nobump_var   = tk.BooleanVar(value=False)
        clear_var    = tk.BooleanVar(value=False)
        verify_var   = tk.BooleanVar(value=False)
        original_var = tk.BooleanVar(value=False)
        status_var   = tk.BooleanVar(value=True)
        progress_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(opt_frame, text="40-track extended  (-x)",      variable=ext_var     ).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="No head bump       (-n)",       variable=nobump_var  ).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Demagnetize first  (-c)",       variable=clear_var   ).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Verify after write (-v)",       variable=verify_var  ).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Original fill pattern (-o)",    variable=original_var).grid(row=4, column=0, sticky="w", pady=2)
        ttk.Checkbutton(opt_frame, text="Show drive status (-s)",        variable=status_var  ).grid(row=5, column=0, sticky="w", pady=2)
        progress_cb = ttk.Checkbutton(opt_frame, text="Progress indicator (-p, cbmformat only)",
                                      variable=progress_var)
        progress_cb.grid(row=6, column=0, sticky="w", pady=2)

        # --- Retries (cbmforng only) ---
        retry_frame = ttk.Frame(dlg)
        retry_frame.grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(2, 6))
        retry_label = ttk.Label(retry_frame, text="Retries (-r):")
        retry_label.pack(side="left")
        retry_var = tk.IntVar(value=2)
        retry_spin = ttk.Spinbox(retry_frame, from_=0, to=10, textvariable=retry_var, width=4)
        retry_spin.pack(side="left", padx=6)

        def _on_tool_change():
            is_forng = tool_var.get() == "cbmforng"
            state = "normal" if is_forng else "disabled"
            retry_label.configure(state=state)
            retry_spin.configure(state=state)
            progress_cb.configure(state="disabled" if is_forng else "normal")

        # --- Buttons ---
        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 10))

        def _do_format():
            dname = name_var.get().strip() or "DISKNAME"
            did   = id_var.get().strip() or "01"
            tool  = tool_var.get()
            cmd   = [tool]
            if ext_var.get():      cmd.append("-x")
            if nobump_var.get():   cmd.append("-n")
            if clear_var.get():    cmd.append("-c")
            if verify_var.get():   cmd.append("-v")
            if original_var.get(): cmd.append("-o")
            if status_var.get():   cmd.append("-s")
            if tool == "cbmformat" and progress_var.get():
                cmd.append("-p")
            if tool == "cbmforng":
                cmd += ["-r", str(retry_var.get())]
            cmd += [DRIVE, f"{dname},{did}"]
            dlg.destroy()

            prog, pbar, close_btn, _append, proc_store = self._make_progress_window(
                f"Formatting drive {DRIVE}…")
            self._set_status(f"Formatting drive {DRIVE} with {tool}…")

            def run():
                subprocess.run(["cbmctrl", "reset"], capture_output=True, timeout=5)
                rc = self._pty_run(cmd, _append, proc_store)
                ok = rc == 0

                def done():
                    pbar.stop()
                    pbar.pack_forget()
                    if ok:
                        _append("\nDone.\n")
                        prog.title("Format complete")
                        self._set_status("Format complete.")
                    else:
                        _append("\nFormat failed.\n")
                        prog.title("Format failed")
                        self._set_status("Format failed.")
                    def _reset_and_refresh():
                        subprocess.run(["cbmctrl", "reset"], capture_output=True, timeout=5)
                        self.cbm_pane.refresh()
                    self.after(1500, _reset_and_refresh)
                    prog._finish()

                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(btn_frame, text="Format", command=_do_format).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

        name_entry.bind("<Return>", lambda e: _do_format())
        id_entry.bind("<Return>",   lambda e: _do_format())

        dlg.wait_window()

    def _sort_disk(self):
        if not self.cbm_pane._entries:
            self._set_status("Refreshing…")
            def _refresh():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda: self.cbm_pane._apply_dir(raw))
                self.after(0, self._sort_disk)
            threading.Thread(target=_refresh, daemon=True).start()
            return
        entries = self.cbm_pane._entries

        dlg = tk.Toplevel(self)
        dlg.title("Sort disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        fb_var   = tk.BooleanVar(value=False)
        plat_var = tk.StringVar(value="C16/Plus4")

        fb_frame = ttk.LabelFrame(dlg, text="FB file browser", padding=(8, 4))
        fb_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        ttk.Checkbutton(fb_frame, text="Add FB as first file",
                        variable=fb_var, command=lambda: _toggle()).grid(
            row=0, column=0, columnspan=2, sticky="w")

        plat_frame = ttk.LabelFrame(dlg, text="Platform", padding=(8, 4))
        plat_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        plat_radios = []
        for plat in FB_PATHS:
            rb = ttk.Radiobutton(plat_frame, text=plat, variable=plat_var, value=plat)
            rb.pack(side="left", padx=8)
            plat_radios.append(rb)

        def _toggle():
            s = "normal" if fb_var.get() else "disabled"
            for rb in plat_radios:
                rb.configure(state=s)
        _toggle()

        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 6))
        info_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        prgs  = [(b, n, t) for b, n, t in entries if t.upper().lstrip("*") == "PRG"]
        other = [(b, n, t) for b, n, t in entries if t.upper().lstrip("*") != "PRG"]
        ttk.Label(info_frame, font=("Menlo", 10), anchor="w",
                  text=f"Files on disk:  {len(entries)}\n"
                       f"PRG files:      {len(prgs)}\n"
                       f"Other (SEQ/USR/REL): {len(other)}").pack(fill="x")

        hdr      = self.cbm_pane._hdr.get()
        m        = re.search(r'"(.+?)"', hdr)
        diskname = re.sub(r'[^a-zA-Z0-9_-]', '_', m.group(1).strip() if m else "disk").lower()
        dest_dir = self.fs_pane.current_dir()
        default_backup = os.path.join(dest_dir, f"{diskname}_backup.d64")

        backup_frame = ttk.LabelFrame(dlg, text="Backup", padding=(8, 4))
        backup_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(backup_frame, text="Backup disk to image first",
                        variable=backup_var, command=lambda: _toggle_backup()).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(backup_frame, text="Path:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        backup_path_var = tk.StringVar(value=default_backup)
        backup_entry = ttk.Entry(backup_frame, textvariable=backup_path_var, width=34)
        backup_entry.grid(row=1, column=1, sticky="ew", padx=4)

        def _toggle_backup():
            s = "normal" if backup_var.get() else "disabled"
            backup_entry.configure(state=s)
        _toggle_backup()

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 10))

        def _do_sort():
            add_fb      = fb_var.get()
            platform    = plat_var.get()
            fb_files    = FB_PATHS[platform] if add_fb else []
            snap        = entries[:]
            do_backup   = backup_var.get()
            backup_path = backup_path_var.get().strip()
            dlg.destroy()

            prg_entries   = sorted(
                [(b, n, t) for b, n, t in snap if t.upper().lstrip("*") == "PRG"],
                key=lambda x: x[1].lower())
            other_entries = [(b, n, t) for b, n, t in snap if t.upper().lstrip("*") != "PRG"]
            write_order   = prg_entries + other_entries
            total = len(snap) * 2 + len(fb_files)
            step  = [0]

            set_msg, close, cancel_event, proc_store = self._make_task_dialog(
                "Sorting disk…", "Sorting…")
            self._set_status("Sorting disk…")

            def _progress(msg):
                step[0] += 1
                set_msg(f"[{step[0]}/{total}]  {msg}")

            def _push_dir():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))

            def run():
                tmpdir = None
                errors = []
                try:
                    if cancel_event.is_set():
                        return
                    if do_backup and backup_path:
                        set_msg(f"Backing up to {os.path.basename(backup_path)}…")
                        proc = subprocess.Popen(["d64copy", DRIVE, backup_path],
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        proc_store[0] = proc
                        proc.communicate(timeout=300)
                        proc_store[0] = None
                        if not os.path.exists(backup_path) or os.path.getsize(backup_path) == 0:
                            self.after(0, lambda: messagebox.showerror(
                                "Sort aborted", "Backup failed — disk unchanged."))
                            return
                        self.after(0, lambda: self.fs_pane._load())

                    if cancel_event.is_set():
                        return
                    tmpdir = tempfile.mkdtemp()
                    for blk, name, ftype in snap:
                        if cancel_event.is_set():
                            break
                        _progress(f"Reading {name}…")
                        err = cbm_read_file(DRIVE, name, tmpdir, proc_store)
                        if err:
                            errors.append(f"Read {name}: {err}")
                    if errors and not cancel_event.is_set():
                        self.after(0, lambda: messagebox.showerror(
                            "Sort aborted",
                            "Read failed — disk unchanged:\n" + "\n".join(errors)))
                        return

                    if not cancel_event.is_set():
                        set_msg("Scratching files…")
                        subprocess.run(["cbmctrl", "command", DRIVE, "S:*"],
                                       capture_output=True, timeout=10)

                    for cbm_name, local_path in fb_files:
                        if cancel_event.is_set():
                            break
                        _progress(f"Writing {cbm_name}…")
                        err = cbm_write_file(DRIVE, local_path, cbm_name.lower(), proc_store)
                        if err:
                            errors.append(f"Write {cbm_name}: {err}")
                        else:
                            _push_dir()

                    for blk, name, ftype in write_order:
                        if cancel_event.is_set():
                            break
                        ext = TYPE_EXT.get(ftype.upper().lstrip("*"), ".prg")
                        local_file = os.path.join(tmpdir, name + ext)
                        if not os.path.exists(local_file):
                            for f in os.listdir(tmpdir):
                                if f.upper().startswith(name.upper() + "."):
                                    local_file = os.path.join(tmpdir, f)
                                    break
                        if os.path.exists(local_file):
                            _progress(f"Writing {name}…")
                            err = cbm_write_file(DRIVE, local_file, name, proc_store)
                            if err:
                                errors.append(f"Write {name}: {err}")
                            else:
                                _push_dir()
                        else:
                            errors.append(f"Not found in scratch dir: {name}")
                finally:
                    if tmpdir:
                        try: shutil.rmtree(tmpdir)
                        except Exception: pass

                def done():
                    close()
                    self.cbm_pane.refresh()
                    if cancel_event.is_set():
                        self._set_status("Sort cancelled.")
                    elif errors:
                        messagebox.showerror("Sort errors", "\n".join(errors))
                        self._set_status("Sort completed with errors.")
                    else:
                        self._set_status("Sort complete.")
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(btn_frame, text="Sort", command=_do_sort).pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)
        dlg.wait_window()

    def _fbify_disk(self):
        if not self.cbm_pane._entries and self.cbm_pane.blocks_free() is None:
            self._set_status("Refreshing…")
            def _refresh():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda: self.cbm_pane._apply_dir(raw))
                self.after(0, self._fbify_disk)
            threading.Thread(target=_refresh, daemon=True).start()
            return
        dlg = tk.Toplevel(self)
        dlg.title("FB-ify disk")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        plat_var = tk.StringVar(value="C16/Plus4")

        plat_frame = ttk.LabelFrame(dlg, text="Platform", padding=(8, 4))
        plat_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        for plat in FB_PATHS:
            ttk.Radiobutton(plat_frame, text=plat, variable=plat_var, value=plat,
                            command=lambda: _update_info()).pack(side="left", padx=8)

        info_frame = ttk.LabelFrame(dlg, text="Disk", padding=(8, 6))
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        info_var    = tk.StringVar()
        verdict_var = tk.StringVar()
        ttk.Label(info_frame, textvariable=info_var,    font=("Menlo", 10),
                  justify="left").pack(anchor="w")
        ttk.Label(info_frame, textvariable=verdict_var, font=("Menlo", 10, "bold"),
                  justify="left").pack(anchor="w")

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(4, 10))
        proceed_btn = ttk.Button(btn_frame, text="FB-ify", command=lambda: _do_fbify())
        proceed_btn.pack(side="left", padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

        def _update_info():
            platform  = plat_var.get()
            fb_files  = FB_PATHS[platform]
            fb_blocks = sum(math.ceil(os.path.getsize(p) / 254) for _, p in fb_files)
            bf        = self.cbm_pane.blocks_free()
            entries   = self.cbm_pane._entries
            if bf is None:
                info_var.set("Disk info unavailable — refresh first.")
                verdict_var.set("")
                proceed_btn.configure(state="disabled")
                return
            n_files = len(fb_files)
            info_var.set(
                f"Files on disk:  {len(entries)}\n"
                f"Blocks free:    {bf}\n"
                f"FB cost:        {fb_blocks} blocks  ({n_files} file{'s' if n_files > 1 else ''})"
            )
            if fb_blocks <= bf:
                verdict_var.set("Fits.")
                proceed_btn.configure(state="normal")
            else:
                verdict_var.set(f"Won't fit — need {fb_blocks}, have {bf}.")
                proceed_btn.configure(state="disabled")

        def _do_fbify():
            platform = plat_var.get()
            fb_files = FB_PATHS[platform]
            entries  = self.cbm_pane._entries[:]
            dlg.destroy()

            total = (len(entries) * 2 + len(fb_files)) if entries else len(fb_files)
            step  = [0]
            set_msg, close, cancel_event, proc_store = self._make_task_dialog(
                "FB-ifying disk…", f"FB-ifying for {platform}…")
            self._set_status(f"FB-ifying for {platform}…")

            def _progress(msg):
                step[0] += 1
                set_msg(f"[{step[0]}/{total}]  {msg}")

            def _push_dir():
                raw = cbm_dir(DRIVE)
                self.after(0, lambda r=raw: self.cbm_pane._apply_dir(r))

            def run():
                tmpdir = None
                errors = []
                try:
                    if entries and not cancel_event.is_set():
                        tmpdir = tempfile.mkdtemp()
                        for blk, name, ftype in entries:
                            if cancel_event.is_set():
                                break
                            _progress(f"Reading {name}…")
                            err = cbm_read_file(DRIVE, name, tmpdir, proc_store)
                            if err:
                                errors.append(f"Read {name}: {err}")
                        if errors and not cancel_event.is_set():
                            self.after(0, lambda: messagebox.showerror(
                                "FB-ify aborted",
                                "Read failed — disk unchanged:\n" + "\n".join(errors)))
                            return
                        if not cancel_event.is_set():
                            set_msg("Scratching files…")
                            subprocess.run(["cbmctrl", "command", DRIVE, "S:*"],
                                           capture_output=True, timeout=10)

                    for cbm_name, local_path in fb_files:
                        if cancel_event.is_set():
                            break
                        _progress(f"Writing {cbm_name}…")
                        err = cbm_write_file(DRIVE, local_path, cbm_name.lower(), proc_store)
                        if err:
                            errors.append(f"Write {cbm_name}: {err}")
                        else:
                            _push_dir()

                    if tmpdir:
                        for blk, name, ftype in entries:
                            if cancel_event.is_set():
                                break
                            ext = TYPE_EXT.get(ftype.upper().lstrip("*"), ".prg")
                            local_file = os.path.join(tmpdir, name + ext)
                            if not os.path.exists(local_file):
                                for f in os.listdir(tmpdir):
                                    if f.upper().startswith(name.upper() + "."):
                                        local_file = os.path.join(tmpdir, f)
                                        break
                            if os.path.exists(local_file):
                                _progress(f"Restoring {name}…")
                                err = cbm_write_file(DRIVE, local_file, name, proc_store)
                                if err:
                                    errors.append(f"Restore {name}: {err}")
                                else:
                                    _push_dir()
                            else:
                                errors.append(f"Not found in scratch dir: {name}")
                finally:
                    if tmpdir:
                        try: shutil.rmtree(tmpdir)
                        except Exception: pass

                def done():
                    close()
                    self.cbm_pane.refresh()
                    if cancel_event.is_set():
                        self._set_status("FB-ify cancelled.")
                    elif errors:
                        messagebox.showerror("FB-ify errors", "\n".join(errors))
                        self._set_status("FB-ify completed with errors.")
                    else:
                        self._set_status(f"FB-ify complete ({platform}).")
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        _update_info()
        dlg.wait_window()


def _check_single_instance():
    import atexit, signal
    pid_file = os.path.expanduser("~/.diskmasher.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # raises if not running
            # Another instance is live — ask what to do
            probe = tk.Tk()
            probe.withdraw()
            kill_it = messagebox.askyesnocancel(
                "Diskmasher already running",
                f"Diskmasher (PID {old_pid}) is already running.\n\n"
                "Kill it and start fresh?",
                parent=probe)
            probe.destroy()
            if kill_it is None:
                return False      # Cancel — don't start
            if kill_it:
                try:
                    os.kill(old_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                return False      # No — don't start
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file — proceed

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.unlink(pid_file) if os.path.exists(pid_file) else None)
    return True

if __name__ == "__main__":
    if _check_single_instance():
        app = DiskMasher()
        app.mainloop()
