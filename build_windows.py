#!/usr/bin/env python3
"""Build a standalone Windows executable of LightPDF.

Supports two GTK3 runtime sources:
  - conda-forge  (recommended for CI – pre-built, fast)
  - MSYS2 MINGW64 (for local builds)

Usage:
    python build_windows.py
"""

import os
import shutil
import subprocess
import sys


def _find_gtk_prefix():
    """Return the directory that contains bin/, lib/, share/ for GTK3."""
    # conda-forge puts everything under $CONDA_PREFIX/Library
    conda = os.environ.get("CONDA_PREFIX", "")
    lib = os.path.join(conda, "Library")
    if conda and os.path.isdir(os.path.join(lib, "bin")):
        return lib

    # MSYS2 MINGW64
    mingw = os.environ.get("MINGW_PREFIX", "")
    if mingw and os.path.isdir(mingw):
        return mingw
    for p in ("C:/msys64/mingw64", "D:/msys64/mingw64"):
        if os.path.isdir(p):
            return p

    return None


def _copy_tree(prefix, dest_root, rel):
    src = os.path.join(prefix, rel)
    dst = os.path.join(dest_root, rel)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f"  + {rel}")


def main():
    name = "LightPDF"
    dist = os.path.join("dist", name)

    # ── 1. PyInstaller ──────────────────────────────────────────
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", name,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--hidden-import=gi",
        "--hidden-import=gi.repository.Gtk",
        "--hidden-import=gi.repository.Gdk",
        "--hidden-import=gi.repository.GdkPixbuf",
        "--hidden-import=gi.repository.GLib",
        "--hidden-import=gi.repository.Gio",
        "--hidden-import=gi.repository.Pango",
        "--hidden-import=gi.repository.cairo",
        "--hidden-import=gi.repository.Atk",
        "--collect-all", "gi",
        "run.py",
    ]
    print(">>> PyInstaller")
    subprocess.run(cmd, check=True)

    # ── 2. Bundle GTK runtime files PyInstaller may skip ────────
    prefix = _find_gtk_prefix()
    if prefix:
        print(f">>> Bundling GTK runtime from {prefix}")
        _copy_tree(prefix, dist, os.path.join("share", "glib-2.0", "schemas"))
        _copy_tree(prefix, dist, os.path.join("lib", "gdk-pixbuf-2.0"))
        for theme in ("hicolor", "Adwaita"):
            _copy_tree(prefix, dist, os.path.join("share", "icons", theme))

        # GTK settings for native Windows appearance
        etc = os.path.join(dist, "etc", "gtk-3.0")
        os.makedirs(etc, exist_ok=True)
        with open(os.path.join(etc, "settings.ini"), "w") as f:
            f.write("[Settings]\ngtk-theme-name = win32\n"
                    "gtk-icon-theme-name = Adwaita\n")
    else:
        print(">>> WARNING: GTK prefix not found – skipping runtime bundle")

    # ── 3. ZIP ──────────────────────────────────────────────────
    zip_path = shutil.make_archive(
        os.path.join("dist", f"{name}-Windows"), "zip", "dist", name
    )
    print(f"\n=== {zip_path} ===")


if __name__ == "__main__":
    main()
