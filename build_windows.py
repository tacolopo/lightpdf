#!/usr/bin/env python3
"""Build a standalone Windows executable of LightPDF.

Run inside an MSYS2 MINGW64 shell (or the GitHub Actions CI) where GTK3
and PyGObject are available via pacman.

Usage:
    python build_windows.py
"""

import os
import shutil
import subprocess
import sys


def get_mingw_prefix():
    """Return the MINGW64 prefix (e.g. /mingw64 or C:/msys64/mingw64)."""
    p = os.environ.get("MINGW_PREFIX", "")
    if p:
        return p
    for candidate in ["C:/msys64/mingw64", "D:/msys64/mingw64"]:
        if os.path.isdir(candidate):
            return candidate
    return None


def main():
    dist_name = "LightPDF"
    dist_dir = os.path.join("dist", dist_name)

    # ── 1. Run PyInstaller ──────────────────────────────────────
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", dist_name,
        "--windowed",
        "--onedir",
        "--noconfirm",
        # GI / GTK hidden imports
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
    print(">>> Running PyInstaller …")
    subprocess.run(cmd, check=True)

    # ── 2. Bundle GTK runtime files PyInstaller may miss ────────
    prefix = get_mingw_prefix()
    if prefix:
        _copy_tree(prefix, dist_dir, "share/glib-2.0/schemas")
        _copy_tree(prefix, dist_dir, "lib/gdk-pixbuf-2.0")

        # Icons – only Adwaita scalable + hicolor index (keeps size down)
        for theme in ("hicolor", "Adwaita"):
            src = os.path.join(prefix, "share", "icons", theme)
            if os.path.isdir(src):
                _copy_tree(prefix, dist_dir, f"share/icons/{theme}")

        # GTK settings for native Windows look
        etc_dir = os.path.join(dist_dir, "etc", "gtk-3.0")
        os.makedirs(etc_dir, exist_ok=True)
        with open(os.path.join(etc_dir, "settings.ini"), "w") as f:
            f.write("[Settings]\ngtk-theme-name = win32\ngtk-icon-theme-name = Adwaita\n")

    # ── 3. Create zip ───────────────────────────────────────────
    zip_path = shutil.make_archive(
        os.path.join("dist", f"{dist_name}-Windows"), "zip", "dist", dist_name
    )
    print(f"\n=== Built: {zip_path} ===")


def _copy_tree(prefix, dist_dir, rel_path):
    """Copy a directory tree from *prefix* into *dist_dir*."""
    src = os.path.join(prefix, rel_path)
    dst = os.path.join(dist_dir, rel_path)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print(f"  copied {rel_path}")


if __name__ == "__main__":
    main()
