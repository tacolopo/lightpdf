"""LightPDF - main GTK3 application."""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib

from . import __app_name__, __version__
from .viewer import PDFViewer
from .editor import TextEditor
from .signer import (
    PYHANKO_AVAILABLE,
    PKCS11_AVAILABLE,
    _IS_WINDOWS,
    find_pkcs11_library,
    create_pkcs11_signer,
    create_pkcs12_signer,
    sign_pdf,
)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, filepath=None):
        super().__init__(application=app, title=__app_name__)
        self.set_default_size(960, 720)

        self.file_path = None
        self.modified = False
        self.editor = TextEditor()

        self._build_ui()
        self._connect_keys()

        if filepath:
            self._open_file(filepath)

    # ── UI construction ─────────────────────────────────────────

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # ── header bar ──
        hb = Gtk.HeaderBar(show_close_button=True, title=__app_name__)
        self.set_titlebar(hb)

        for icon, tip, cb in [
            ("document-open-symbolic", "Open  (Ctrl+O)", self._on_open),
            ("document-save-symbolic", "Save  (Ctrl+S)", self._on_save),
            ("document-save-as-symbolic", "Save As  (Ctrl+Shift+S)", self._on_save_as),
        ]:
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _, f=cb: f())
            hb.pack_start(b)

        hb.pack_start(Gtk.Separator())

        self.edit_btn = Gtk.ToggleButton()
        self.edit_btn.set_image(
            Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
        )
        self.edit_btn.set_tooltip_text("Edit mode  (Ctrl+E)")
        self.edit_btn.connect("toggled", self._on_edit_toggled)
        hb.pack_start(self.edit_btn)

        sign_btn = Gtk.Button.new_from_icon_name(
            "changes-allow-symbolic", Gtk.IconSize.BUTTON
        )
        sign_btn.set_tooltip_text("Sign with smart card / certificate")
        sign_btn.connect("clicked", lambda _: self._on_sign())
        hb.pack_start(sign_btn)

        # right side – zoom
        zbox = Gtk.Box(spacing=2)
        zbox.get_style_context().add_class("linked")
        for icon, delta in [("zoom-out-symbolic", -0.15), ("zoom-in-symbolic", 0.15)]:
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.connect("clicked", lambda _, d=delta: self.viewer.set_zoom(self.viewer.zoom + d))
            zbox.add(b)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_width_chars(5)
        zbox.pack_start(self.zoom_label, False, False, 4)
        hb.pack_end(zbox)

        # right side – page nav
        nav = Gtk.Box(spacing=4)
        pb = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        pb.connect("clicked", lambda _: self.viewer.set_page(self.viewer.page_num - 1))
        nav.add(pb)
        self.page_label = Gtk.Label(label="0 / 0")
        nav.add(self.page_label)
        nb = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        nb.connect("clicked", lambda _: self.viewer.set_page(self.viewer.page_num + 1))
        nav.add(nb)
        hb.pack_end(nav)

        # ── viewer ──
        self.viewer = PDFViewer()
        self.viewer.on_text_selected = self._on_text_selected
        self.viewer.on_page_changed = lambda _: self._sync_labels()
        self.viewer.on_zoom_changed = lambda _: self._sync_labels()
        vbox.pack_start(self.viewer, True, True, 0)

        # ── status bar ──
        self.status = Gtk.Statusbar()
        self.status.push(0, "Ready")
        vbox.pack_end(self.status, False, False, 0)

        # close confirmation
        self.connect("delete-event", self._on_close)

        # drag-and-drop
        self.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY,
        )
        self.connect("drag-data-received", self._on_drag)

        self.show_all()

    def _connect_keys(self):
        ag = Gtk.AccelGroup()
        self.add_accel_group(ag)
        pairs = [
            ("<Control>o", self._on_open),
            ("<Control>s", self._on_save),
            ("<Control><Shift>s", self._on_save_as),
            ("<Control>e", lambda: self.edit_btn.set_active(not self.edit_btn.get_active())),
            ("<Control>z", self._on_undo),
            ("<Control><Shift>z", self._on_redo),
            ("<Control>plus", lambda: self.viewer.set_zoom(self.viewer.zoom + 0.15)),
            ("<Control>minus", lambda: self.viewer.set_zoom(self.viewer.zoom - 0.15)),
            ("<Control>0", lambda: self.viewer.set_zoom(1.0)),
        ]
        for accel, cb in pairs:
            key, mods = Gtk.accelerator_parse(accel)
            if key:
                ag.connect(key, mods, 0, lambda *_, f=cb: f())

        self.connect("key-press-event", self._on_keypress)

    def _on_keypress(self, _w, ev):
        if ev.keyval == Gdk.KEY_Page_Down:
            self.viewer.set_page(self.viewer.page_num + 1)
            return True
        if ev.keyval == Gdk.KEY_Page_Up:
            self.viewer.set_page(self.viewer.page_num - 1)
            return True
        if ev.keyval == Gdk.KEY_Escape and self.edit_btn.get_active():
            self.edit_btn.set_active(False)
            return True
        return False

    # ── file operations ─────────────────────────────────────────

    def _on_open(self):
        dlg = Gtk.FileChooserDialog(
            title="Open PDF", parent=self, action=Gtk.FileChooserAction.OPEN
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        f = Gtk.FileFilter()
        f.set_name("PDF files")
        f.add_mime_type("application/pdf")
        f.add_pattern("*.pdf")
        dlg.add_filter(f)
        af = Gtk.FileFilter()
        af.set_name("All files")
        af.add_pattern("*")
        dlg.add_filter(af)
        if dlg.run() == Gtk.ResponseType.OK:
            self._open_file(dlg.get_filename())
        dlg.destroy()

    def _open_file(self, path):
        try:
            self.viewer.load_document(path)
            self.file_path = path
            self.modified = False
            self.set_title(f"{os.path.basename(path)} \u2014 {__app_name__}")
            self._sync_labels()
            self.status.push(0, f"Opened {path}")
        except Exception as exc:
            self._err(f"Cannot open file:\n{exc}")

    def _on_save(self):
        if not self.viewer.doc:
            return
        if self.file_path:
            self._save_to(self.file_path)
        else:
            self._on_save_as()

    def _on_save_as(self):
        if not self.viewer.doc:
            return
        dlg = Gtk.FileChooserDialog(
            title="Save PDF As", parent=self, action=Gtk.FileChooserAction.SAVE
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        if self.file_path:
            dlg.set_current_name(os.path.basename(self.file_path))
            dlg.set_current_folder(os.path.dirname(self.file_path))
        pf = Gtk.FileFilter()
        pf.set_name("PDF files")
        pf.add_pattern("*.pdf")
        dlg.add_filter(pf)
        if dlg.run() == Gtk.ResponseType.OK:
            p = dlg.get_filename()
            if not p.lower().endswith(".pdf"):
                p += ".pdf"
            self._save_to(p)
        dlg.destroy()

    def _save_to(self, path):
        try:
            if path == self.file_path:
                try:
                    self.viewer.doc.save(
                        path, incremental=True, encryption=0
                    )
                except Exception:
                    tmp = path + ".lightpdf_tmp"
                    self.viewer.doc.save(tmp)
                    self.viewer.doc.close()
                    os.replace(tmp, path)
                    self.viewer.doc = __import__("fitz").open(path)
                    self.viewer._render_page()
            else:
                self.viewer.doc.save(path)
            self.file_path = path
            self.modified = False
            self.set_title(f"{os.path.basename(path)} \u2014 {__app_name__}")
            self.status.push(0, f"Saved {path}")
        except Exception as exc:
            self._err(f"Save failed:\n{exc}")

    # ── edit mode ───────────────────────────────────────────────

    def _on_edit_toggled(self, btn):
        on = btn.get_active()
        self.viewer.set_edit_mode(on)
        self.status.push(0, "Edit mode \u2014 click a text block to edit" if on else "Edit mode off")

    def _on_text_selected(self, block_data):
        blk = block_data["block"]
        old_text = self.editor.get_block_text(blk)

        dlg = Gtk.Dialog(
            title="Edit Text", parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_APPLY, Gtk.ResponseType.OK)
        dlg.set_default_size(480, 300)

        ca = dlg.get_content_area()
        ca.set_spacing(8)
        ca.set_margin_start(12)
        ca.set_margin_end(12)
        ca.set_margin_top(8)

        lbl = Gtk.Label(label="Edit the selected text:")
        lbl.set_halign(Gtk.Align.START)
        ca.add(lbl)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.get_buffer().set_text(old_text)
        sw.add(tv)
        ca.add(sw)
        ca.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            buf = tv.get_buffer()
            new = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if new != old_text:
                self.editor.replace_text(
                    self.viewer.doc, self.viewer.page_num, blk, new
                )
                self.modified = True
                self.viewer._render_page()
                self.status.push(0, "Text updated")
        dlg.destroy()

    def _on_undo(self):
        res = self.editor.undo(self.viewer.doc)
        if res:
            self.viewer.doc, pn = res
            self.viewer.set_page(pn)
            self.status.push(0, "Undo")

    def _on_redo(self):
        res = self.editor.redo(self.viewer.doc)
        if res:
            self.viewer.doc, pn = res
            self.viewer.set_page(pn)
            self.status.push(0, "Redo")

    # ── signing ─────────────────────────────────────────────────

    def _on_sign(self):
        if not self.viewer.doc:
            return self._err("No document open.")
        if not PYHANKO_AVAILABLE:
            return self._err(
                "pyHanko is required for signing.\n"
                "Install with:  pip install 'pyHanko[pkcs11]'"
            )
        if not self.file_path:
            return self._err("Save the document first.")
        if self.modified:
            self._on_save()
        self._show_sign_dialog()

    def _show_sign_dialog(self):
        dlg = Gtk.Dialog(
            title="Sign PDF", parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Sign", Gtk.ResponseType.OK)
        dlg.set_default_size(540, 0)

        ca = dlg.get_content_area()
        ca.set_spacing(10)
        ca.set_margin_start(16)
        ca.set_margin_end(16)
        ca.set_margin_top(12)
        ca.set_margin_bottom(8)

        # ── method selector ──
        method_box = Gtk.Box(spacing=8)
        rb_sc = Gtk.RadioButton.new_with_label(None, "Smart Card (PKCS#11)")
        rb_p12 = Gtk.RadioButton.new_with_label_from_widget(rb_sc, "Certificate file (.p12/.pfx)")
        method_box.add(rb_sc)
        method_box.add(rb_p12)
        ca.add(method_box)

        # ── PKCS#11 fields ──
        sc_frame = Gtk.Frame(label="Smart Card")
        sc_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        sc_grid.set_margin_start(8)
        sc_grid.set_margin_end(8)
        sc_grid.set_margin_top(4)
        sc_grid.set_margin_bottom(8)
        sc_frame.add(sc_grid)

        row = 0
        sc_grid.attach(Gtk.Label(label="Library:", halign=Gtk.Align.END), 0, row, 1, 1)
        lib_entry = Gtk.Entry(hexpand=True)
        auto = find_pkcs11_library()
        if auto:
            lib_entry.set_text(auto)
        else:
            lib_entry.set_placeholder_text(
                r"C:\Program Files\OpenSC Project\...\opensc-pkcs11.dll"
                if _IS_WINDOWS else "/usr/lib/opensc-pkcs11.so"
            )
        sc_grid.attach(lib_entry, 1, row, 1, 1)
        _lib_pat = "*.dll" if _IS_WINDOWS else "*.so"
        browse = Gtk.Button(label="...")
        browse.connect("clicked", lambda _: self._browse_file(dlg, lib_entry, _lib_pat))
        sc_grid.attach(browse, 2, row, 1, 1)

        row += 1
        sc_grid.attach(Gtk.Label(label="Slot:", halign=Gtk.Align.END), 0, row, 1, 1)
        slot_spin = Gtk.SpinButton.new_with_range(0, 15, 1)
        sc_grid.attach(slot_spin, 1, row, 2, 1)

        row += 1
        sc_grid.attach(Gtk.Label(label="PIN:", halign=Gtk.Align.END), 0, row, 1, 1)
        pin_entry = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PIN, hexpand=True)
        sc_grid.attach(pin_entry, 1, row, 2, 1)

        row += 1
        sc_grid.attach(Gtk.Label(label="Key label:", halign=Gtk.Align.END), 0, row, 1, 1)
        keylbl_entry = Gtk.Entry(hexpand=True)
        keylbl_entry.set_placeholder_text("(auto-detect)")
        sc_grid.attach(keylbl_entry, 1, row, 2, 1)

        ca.add(sc_frame)

        # ── PKCS#12 fields ──
        p12_frame = Gtk.Frame(label="Certificate File")
        p12_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        p12_grid.set_margin_start(8)
        p12_grid.set_margin_end(8)
        p12_grid.set_margin_top(4)
        p12_grid.set_margin_bottom(8)
        p12_frame.add(p12_grid)

        p12_grid.attach(Gtk.Label(label="File:", halign=Gtk.Align.END), 0, 0, 1, 1)
        p12_entry = Gtk.Entry(hexpand=True)
        p12_entry.set_placeholder_text("/path/to/cert.p12")
        p12_grid.attach(p12_entry, 1, 0, 1, 1)
        p12_browse = Gtk.Button(label="...")
        p12_browse.connect("clicked", lambda _: self._browse_file(dlg, p12_entry, "*.p12 *.pfx"))
        p12_grid.attach(p12_browse, 2, 0, 1, 1)

        p12_grid.attach(Gtk.Label(label="Password:", halign=Gtk.Align.END), 0, 1, 1, 1)
        p12_pw = Gtk.Entry(visibility=False, hexpand=True)
        p12_grid.attach(p12_pw, 1, 1, 2, 1)

        ca.add(p12_frame)

        # show/hide frames based on radio
        def toggle_method(*_a):
            sc_frame.set_visible(rb_sc.get_active())
            p12_frame.set_visible(rb_p12.get_active())

        rb_sc.connect("toggled", toggle_method)
        rb_p12.connect("toggled", toggle_method)
        p12_frame.set_visible(False)

        # ── common fields ──
        ca.add(Gtk.Separator())
        cg = Gtk.Grid(column_spacing=10, row_spacing=6)

        cg.attach(Gtk.Label(label="Reason:", halign=Gtk.Align.END), 0, 0, 1, 1)
        reason_e = Gtk.Entry(hexpand=True)
        reason_e.set_placeholder_text("Document approval")
        cg.attach(reason_e, 1, 0, 1, 1)

        cg.attach(Gtk.Label(label="Location:", halign=Gtk.Align.END), 0, 1, 1, 1)
        loc_e = Gtk.Entry(hexpand=True)
        cg.attach(loc_e, 1, 1, 1, 1)

        vis_chk = Gtk.CheckButton(label="Visible signature on current page")
        cg.attach(vis_chk, 0, 2, 2, 1)

        ca.add(cg)

        ca.add(Gtk.Separator())
        obox = Gtk.Box(spacing=10)
        obox.add(Gtk.Label(label="Output:"))
        out_entry = Gtk.Entry(hexpand=True)
        base, ext = os.path.splitext(self.file_path)
        out_entry.set_text(f"{base}_signed{ext}")
        obox.add(out_entry)
        ca.add(obox)

        ca.show_all()
        toggle_method()  # ensure correct initial visibility

        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy()
            return

        # ── gather values & sign ──
        use_sc = rb_sc.get_active()
        reason = reason_e.get_text().strip() or None
        location = loc_e.get_text().strip() or None
        visible = vis_chk.get_active()
        output = out_entry.get_text().strip()

        rect = None
        if visible:
            pg = self.viewer.doc[self.viewer.page_num]
            pw, ph = pg.rect.width, pg.rect.height
            rect = (pw - 260, ph - 80, pw - 20, ph - 20)

        try:
            if use_sc:
                lib = lib_entry.get_text().strip()
                pin = pin_entry.get_text()
                klbl = keylbl_entry.get_text().strip() or None
                if not lib:
                    raise ValueError("PKCS#11 library path is required.")
                if not pin:
                    raise ValueError("PIN is required.")
                signer = create_pkcs11_signer(lib, int(slot_spin.get_value()), pin, klbl)
            else:
                pf = p12_entry.get_text().strip()
                pw = p12_pw.get_text()
                if not pf:
                    raise ValueError("Certificate file path is required.")
                signer = create_pkcs12_signer(pf, pw)

            sign_pdf(
                self.file_path, output, signer,
                reason=reason, location=location,
                visible=visible, page=self.viewer.page_num, rect=rect,
            )
            self.status.push(0, f"Signed \u2192 {output}")
            dlg.destroy()

            msg = Gtk.MessageDialog(
                parent=self, flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.YES_NO,
                text="Document signed successfully.\nOpen the signed file?",
            )
            if msg.run() == Gtk.ResponseType.YES:
                self._open_file(output)
            msg.destroy()

        except Exception as exc:
            dlg.destroy()
            self._err(f"Signing failed:\n{exc}")

    # ── helpers ─────────────────────────────────────────────────

    def _browse_file(self, parent, entry, patterns):
        fc = Gtk.FileChooserDialog(
            title="Select File", parent=parent, action=Gtk.FileChooserAction.OPEN
        )
        fc.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        ff = Gtk.FileFilter()
        for p in patterns.split():
            ff.add_pattern(p)
        fc.add_filter(ff)
        if fc.run() == Gtk.ResponseType.OK:
            entry.set_text(fc.get_filename())
        fc.destroy()

    def _sync_labels(self):
        if self.viewer.doc:
            self.page_label.set_text(
                f"{self.viewer.page_num + 1} / {self.viewer.page_count}"
            )
        self.zoom_label.set_text(f"{int(self.viewer.zoom * 100)}%")

    def _on_close(self, _w, _ev):
        if not self.modified:
            return False
        md = Gtk.MessageDialog(
            parent=self, flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Save changes before closing?",
        )
        md.add_buttons(
            "Discard", Gtk.ResponseType.REJECT,
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        r = md.run()
        md.destroy()
        if r == Gtk.ResponseType.OK:
            self._on_save()
            return False
        return r != Gtk.ResponseType.REJECT  # True = cancel close

    def _on_drag(self, _w, _ctx, _x, _y, data, _info, _time):
        uris = data.get_uris()
        if uris:
            path = GLib.filename_from_uri(uris[0])[0]
            if path.lower().endswith(".pdf"):
                self._open_file(path)

    def _err(self, msg):
        md = Gtk.MessageDialog(
            parent=self, flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        md.run()
        md.destroy()


class LightPDFApp(Gtk.Application):
    def __init__(self, filepath=None):
        super().__init__(application_id="com.github.lightpdf")
        self.filepath = filepath

    def do_activate(self):
        win = MainWindow(self, filepath=self.filepath)

    def do_startup(self):
        Gtk.Application.do_startup(self)
