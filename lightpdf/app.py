"""LightPDF — lightweight PDF editor & toolbox (GTK3)."""

import os
import sys

import fitz
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from . import __app_name__, __version__
from .viewer import (
    PDFViewer, TOOL_HIGHLIGHT, TOOL_NOTE, TOOL_RECT,
    TOOL_CIRCLE, TOOL_LINE, TOOL_DRAW, TOOL_IMAGE,
)
from .editor import TextEditor
from .signer import (
    PYHANKO_AVAILABLE, PKCS11_AVAILABLE, _IS_WINDOWS,
    find_pkcs11_library, create_pkcs11_signer, create_pkcs12_signer, sign_pdf,
)
from . import tools as T


# ── Welcome screen ──────────────────────────────────────────────


class WelcomeView(Gtk.Box):
    """Card-based landing page shown when no document is open."""

    def __init__(self, cbs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label()
        title.set_markup(
            f'<span size="xx-large" weight="bold">{__app_name__}</span>\n'
            '<span size="large">Lightweight PDF Editor &amp; Toolbox</span>'
        )
        title.set_justify(Gtk.Justification.CENTER)
        title.set_line_wrap(True)
        self.pack_start(title, False, False, 0)

        grid = Gtk.Grid(column_spacing=16, row_spacing=16)
        grid.set_halign(Gtk.Align.CENTER)

        categories = [
            ("Organize", [
                ("document-open-symbolic", "Merge PDFs", cbs["merge"]),
                ("edit-cut-symbolic", "Split PDF", cbs["split"]),
                ("view-grid-symbolic", "Manage Pages", cbs["pages"]),
            ]),
            ("Convert", [
                ("image-x-generic-symbolic", "Images \u2192 PDF", cbs["img2pdf"]),
                ("document-send-symbolic", "PDF \u2192 Images", cbs["pdf2img"]),
            ]),
            ("Edit", [
                ("document-edit-symbolic", "Open & Edit PDF", cbs["open"]),
                ("changes-allow-symbolic", "Sign PDF", cbs["sign"]),
            ]),
            ("Optimize", [
                ("drive-harddisk-symbolic", "Compress PDF", cbs["compress"]),
                ("edit-clear-symbolic", "Remove Metadata", cbs["metadata"]),
            ]),
        ]

        for idx, (cat, items) in enumerate(categories):
            frame = Gtk.Frame(label=f"  {cat}  ")
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vb.set_margin_start(10)
            vb.set_margin_end(10)
            vb.set_margin_top(6)
            vb.set_margin_bottom(10)
            for icon, label, cb in items:
                btn = Gtk.Button()
                bx = Gtk.Box(spacing=8)
                bx.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
                bx.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
                btn.add(bx)
                btn.connect("clicked", lambda _, f=cb: f())
                vb.pack_start(btn, False, False, 0)
            frame.add(vb)
            grid.attach(frame, idx % 2, idx // 2, 1, 1)

        self.pack_start(grid, False, False, 0)

        open_btn = Gtk.Button(label="  Open PDF File  ")
        open_btn.get_style_context().add_class("suggested-action")
        open_btn.set_halign(Gtk.Align.CENTER)
        open_btn.connect("clicked", lambda _: cbs["open"]())
        self.pack_start(open_btn, False, False, 8)


# ── Main window ─────────────────────────────────────────────────


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, filepath=None):
        super().__init__(application=app, title=__app_name__)
        self.set_default_size(980, 740)
        self.file_path = None
        self.modified = False
        self.editor = TextEditor()
        self._build_ui()
        self._connect_keys()
        if filepath:
            self._open_file(filepath)

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # ── header bar ──
        hb = Gtk.HeaderBar(show_close_button=True, title=__app_name__)
        self.set_titlebar(hb)

        for icon, tip, cb in [
            ("document-open-symbolic", "Open (Ctrl+O)", self._on_open),
            ("document-save-symbolic", "Save (Ctrl+S)", self._on_save),
            ("document-save-as-symbolic", "Save As", self._on_save_as),
        ]:
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _, f=cb: f())
            hb.pack_start(b)

        hb.pack_start(Gtk.Separator())

        # Edit toggle
        self.edit_btn = Gtk.ToggleButton()
        self.edit_btn.set_image(Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON))
        self.edit_btn.set_tooltip_text("Edit text (Ctrl+E)")
        self.edit_btn.connect("toggled", self._on_edit_toggled)
        hb.pack_start(self.edit_btn)

        # Annotate toggle
        self.annot_btn = Gtk.ToggleButton()
        self.annot_btn.set_image(Gtk.Image.new_from_icon_name("document-properties-symbolic", Gtk.IconSize.BUTTON))
        self.annot_btn.set_tooltip_text("Annotate (Ctrl+A)")
        self.annot_btn.connect("toggled", self._on_annot_toggled)
        hb.pack_start(self.annot_btn)

        # Sign
        sign_btn = Gtk.Button.new_from_icon_name("changes-allow-symbolic", Gtk.IconSize.BUTTON)
        sign_btn.set_tooltip_text("Sign PDF")
        sign_btn.connect("clicked", lambda _: self._on_sign())
        hb.pack_start(sign_btn)

        # Tools menu
        tools_mbtn = Gtk.MenuButton()
        tools_mbtn.set_image(Gtk.Image.new_from_icon_name("applications-utilities-symbolic", Gtk.IconSize.BUTTON))
        tools_mbtn.set_tooltip_text("Tools")
        tmenu = Gtk.Menu()
        for label, cb in [
            ("Merge PDFs\u2026", self._on_merge),
            ("Split PDF\u2026", self._on_split),
            ("Manage Pages\u2026", self._on_pages),
            (None, None),
            ("Images \u2192 PDF\u2026", self._on_img2pdf),
            ("PDF \u2192 Images\u2026", self._on_pdf2img),
            (None, None),
            ("Compress PDF\u2026", self._on_compress),
            ("Remove Metadata", self._on_metadata),
        ]:
            if label is None:
                tmenu.append(Gtk.SeparatorMenuItem())
            else:
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", lambda _, f=cb: f())
                tmenu.append(mi)
        tmenu.show_all()
        tools_mbtn.set_popup(tmenu)
        hb.pack_start(tools_mbtn)

        # right – zoom
        zbox = Gtk.Box(spacing=2)
        zbox.get_style_context().add_class("linked")
        for icon, d in [("zoom-out-symbolic", -0.15), ("zoom-in-symbolic", 0.15)]:
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.connect("clicked", lambda _, dd=d: self.viewer.set_zoom(self.viewer.zoom + dd))
            zbox.add(b)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_width_chars(5)
        zbox.pack_start(self.zoom_label, False, False, 4)
        hb.pack_end(zbox)

        # right – page nav
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

        # ── annotation toolbar (revealed when annotate is on) ──
        self._build_annot_bar()
        vbox.pack_start(self.annot_reveal, False, False, 0)

        # ── stack: welcome / viewer ──
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.welcome = WelcomeView({
            "open": self._on_open, "merge": self._on_merge, "split": self._on_split,
            "pages": self._on_pages, "img2pdf": self._on_img2pdf,
            "pdf2img": self._on_pdf2img, "compress": self._on_compress,
            "metadata": self._on_metadata, "sign": self._on_sign,
        })
        self.stack.add_named(self.welcome, "welcome")

        self.viewer = PDFViewer()
        self.viewer.on_text_selected = self._on_text_selected
        self.viewer.on_page_changed = lambda _: self._sync_labels()
        self.viewer.on_zoom_changed = lambda _: self._sync_labels()
        self.viewer.on_annotation_added = self._on_annot_added
        self.viewer.on_note_requested = self._on_note_req
        self.viewer.on_image_requested = self._on_image_req
        self.stack.add_named(self.viewer, "viewer")

        self.stack.set_visible_child_name("welcome")
        vbox.pack_start(self.stack, True, True, 0)

        # ── status bar ──
        self.status = Gtk.Statusbar()
        self.status.push(0, "Ready")
        vbox.pack_end(self.status, False, False, 0)

        self.connect("delete-event", self._on_close)
        self.drag_dest_set(Gtk.DestDefaults.ALL,
                           [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
                           Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_drag)

        self.show_all()

    def _build_annot_bar(self):
        bar = Gtk.Box(spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        self._annot_radios = {}
        group = None
        for tool_id, label in [
            (TOOL_HIGHLIGHT, "Highlight"), (TOOL_NOTE, "Note"),
            (TOOL_RECT, "Rect"), (TOOL_CIRCLE, "Circle"),
            (TOOL_LINE, "Line"), (TOOL_DRAW, "Draw"),
            (TOOL_IMAGE, "Image"),
        ]:
            rb = Gtk.RadioButton.new_with_label_from_widget(group, label)
            if group is None:
                group = rb
            rb.connect("toggled", self._on_annot_tool_changed, tool_id)
            bar.pack_start(rb, False, False, 0)
            self._annot_radios[tool_id] = rb

        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        bar.pack_start(Gtk.Label(label="Color:"), False, False, 0)
        self.color_btn = Gtk.ColorButton()
        self.color_btn.set_rgba(Gdk.RGBA(1, 0, 0, 1))
        self.color_btn.connect("color-set", self._on_color_set)
        bar.pack_start(self.color_btn, False, False, 0)

        bar.pack_start(Gtk.Label(label="Width:"), False, False, 0)
        self.width_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.width_spin.set_value(2)
        self.width_spin.connect("value-changed",
                                lambda s: setattr(self.viewer, "annot_width", s.get_value()))
        bar.pack_start(self.width_spin, False, False, 0)

        self.annot_reveal = Gtk.Revealer()
        self.annot_reveal.add(bar)
        self.annot_reveal.set_reveal_child(False)

    # ── keyboard shortcuts ──────────────────────────────────────

    def _connect_keys(self):
        ag = Gtk.AccelGroup()
        self.add_accel_group(ag)
        for accel, cb in [
            ("<Control>o", self._on_open),
            ("<Control>s", self._on_save),
            ("<Control><Shift>s", self._on_save_as),
            ("<Control>e", lambda: self.edit_btn.set_active(not self.edit_btn.get_active())),
            ("<Control>a", lambda: self.annot_btn.set_active(not self.annot_btn.get_active())),
            ("<Control>z", self._on_undo),
            ("<Control><Shift>z", self._on_redo),
            ("<Control>plus", lambda: self.viewer.set_zoom(self.viewer.zoom + 0.15)),
            ("<Control>minus", lambda: self.viewer.set_zoom(self.viewer.zoom - 0.15)),
            ("<Control>0", lambda: self.viewer.set_zoom(1.0)),
        ]:
            key, mods = Gtk.accelerator_parse(accel)
            if key:
                ag.connect(key, mods, 0, lambda *_, f=cb: f())
        self.connect("key-press-event", self._on_keypress)

    def _on_keypress(self, _w, ev):
        if ev.keyval == Gdk.KEY_Page_Down:
            self.viewer.set_page(self.viewer.page_num + 1); return True
        if ev.keyval == Gdk.KEY_Page_Up:
            self.viewer.set_page(self.viewer.page_num - 1); return True
        if ev.keyval == Gdk.KEY_Escape:
            if self.edit_btn.get_active():
                self.edit_btn.set_active(False); return True
            if self.annot_btn.get_active():
                self.annot_btn.set_active(False); return True
        return False

    # ── file operations ─────────────────────────────────────────

    def _on_open(self):
        dlg = Gtk.FileChooserDialog(title="Open PDF", parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        self._add_pdf_filter(dlg)
        af = Gtk.FileFilter(); af.set_name("All files"); af.add_pattern("*")
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
            self.stack.set_visible_child_name("viewer")
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
        path = self._pick_save("Save PDF As",
                               os.path.basename(self.file_path) if self.file_path else "document.pdf")
        if path:
            self._save_to(path)

    def _save_to(self, path):
        try:
            if path == self.file_path:
                try:
                    self.viewer.doc.save(path, incremental=True, encryption=0)
                except Exception:
                    tmp = path + ".lightpdf_tmp"
                    self.viewer.doc.save(tmp)
                    self.viewer.doc.close()
                    os.replace(tmp, path)
                    self.viewer.doc = fitz.open(path)
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
        if on and self.annot_btn.get_active():
            self.annot_btn.set_active(False)
        self.viewer.set_edit_mode(on)
        self.status.push(0, "Edit mode \u2014 click a text block" if on else "Edit mode off")

    def _on_text_selected(self, block_data):
        blk = block_data["block"]
        old_text = self.editor.get_block_text(blk)
        dlg = Gtk.Dialog(title="Edit Text", parent=self,
                         flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_APPLY, Gtk.ResponseType.OK)
        dlg.set_default_size(480, 300)
        ca = dlg.get_content_area()
        ca.set_spacing(8); ca.set_margin_start(12); ca.set_margin_end(12); ca.set_margin_top(8)
        ca.add(Gtk.Label(label="Edit the selected text:", halign=Gtk.Align.START))
        sw = Gtk.ScrolledWindow(); sw.set_vexpand(True)
        tv = Gtk.TextView(); tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.get_buffer().set_text(old_text)
        sw.add(tv); ca.add(sw); ca.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            buf = tv.get_buffer()
            new = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if new != old_text:
                self.editor.replace_text(self.viewer.doc, self.viewer.page_num, blk, new)
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

    # ── annotation mode ────────────────────────────────────────

    def _on_annot_toggled(self, btn):
        on = btn.get_active()
        if on and self.edit_btn.get_active():
            self.edit_btn.set_active(False)
        self.viewer.set_annot_mode(on)
        self.annot_reveal.set_reveal_child(on)
        if on:
            self._on_annot_tool_changed(self._annot_radios[TOOL_HIGHLIGHT], TOOL_HIGHLIGHT)
        self.status.push(0, "Annotate mode \u2014 choose a tool" if on else "Annotate mode off")

    def _on_annot_tool_changed(self, btn, tool_id):
        if btn.get_active():
            self.viewer.annot_tool = tool_id

    def _on_color_set(self, btn):
        c = btn.get_rgba()
        self.viewer.annot_color = (c.red, c.green, c.blue)

    def _on_annot_added(self):
        self.modified = True

    def _on_note_req(self, point):
        dlg = Gtk.Dialog(title="Add Note", parent=self, flags=Gtk.DialogFlags.MODAL)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OK, Gtk.ResponseType.OK)
        ca = dlg.get_content_area()
        ca.set_margin_start(12); ca.set_margin_end(12); ca.set_margin_top(8)
        ca.add(Gtk.Label(label="Note text:", halign=Gtk.Align.START))
        entry = Gtk.Entry(); entry.set_text("Note")
        ca.add(entry); ca.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            page = self.viewer.doc[self.viewer.page_num]
            a = page.add_text_annot(point, entry.get_text())
            c = self.viewer.annot_color
            a.set_colors(stroke=c)
            a.update()
            self.modified = True
            self.viewer._render_page()
        dlg.destroy()

    def _on_image_req(self, point):
        dlg = Gtk.FileChooserDialog(title="Select Image", parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        ff = Gtk.FileFilter(); ff.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff", "*.gif"):
            ff.add_pattern(p)
        dlg.add_filter(ff)
        if dlg.run() == Gtk.ResponseType.OK:
            img_path = dlg.get_filename()
            dlg.destroy()
            try:
                img = fitz.open(img_path)
                r = img[0].rect
                scale = min(200 / max(r.width, 1), 200 / max(r.height, 1), 1.0)
                w, h = r.width * scale, r.height * scale
                rect = fitz.Rect(point.x, point.y, point.x + w, point.y + h)
                page = self.viewer.doc[self.viewer.page_num]
                page.insert_image(rect, filename=img_path)
                img.close()
                self.modified = True
                self.viewer._render_page()
            except Exception as exc:
                self._err(f"Image insert failed:\n{exc}")
        else:
            dlg.destroy()

    # ── signing ─────────────────────────────────────────────────

    def _on_sign(self):
        if not self.viewer.doc:
            self._on_open()
        if not self.viewer.doc:
            return
        if not PYHANKO_AVAILABLE:
            return self._err("pyHanko is required for signing.\nInstall: pip install 'pyHanko[pkcs11]'")
        if not self.file_path:
            return self._err("Save the document first.")
        if self.modified:
            self._on_save()
        self._show_sign_dialog()

    def _show_sign_dialog(self):
        dlg = Gtk.Dialog(title="Sign PDF", parent=self,
                         flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Sign", Gtk.ResponseType.OK)
        dlg.set_default_size(540, 0)
        ca = dlg.get_content_area()
        ca.set_spacing(10); ca.set_margin_start(16); ca.set_margin_end(16)
        ca.set_margin_top(12); ca.set_margin_bottom(8)

        rb_sc = Gtk.RadioButton.new_with_label(None, "Smart Card (PKCS#11)")
        rb_p12 = Gtk.RadioButton.new_with_label_from_widget(rb_sc, "Certificate (.p12/.pfx)")
        mb = Gtk.Box(spacing=8); mb.add(rb_sc); mb.add(rb_p12); ca.add(mb)

        # PKCS#11
        sc_frame = Gtk.Frame(label="Smart Card")
        sg = Gtk.Grid(column_spacing=10, row_spacing=6)
        sg.set_margin_start(8); sg.set_margin_end(8); sg.set_margin_top(4); sg.set_margin_bottom(8)
        sc_frame.add(sg)
        sg.attach(Gtk.Label(label="Library:", halign=Gtk.Align.END), 0, 0, 1, 1)
        lib_e = Gtk.Entry(hexpand=True)
        auto = find_pkcs11_library()
        if auto:
            lib_e.set_text(auto)
        else:
            lib_e.set_placeholder_text(r"C:\...\opensc-pkcs11.dll" if _IS_WINDOWS else "/usr/lib/opensc-pkcs11.so")
        sg.attach(lib_e, 1, 0, 1, 1)
        _lp = "*.dll" if _IS_WINDOWS else "*.so"
        brw = Gtk.Button(label="...")
        brw.connect("clicked", lambda _: self._browse_file(dlg, lib_e, _lp))
        sg.attach(brw, 2, 0, 1, 1)
        sg.attach(Gtk.Label(label="Slot:", halign=Gtk.Align.END), 0, 1, 1, 1)
        slot_sp = Gtk.SpinButton.new_with_range(0, 15, 1)
        sg.attach(slot_sp, 1, 1, 2, 1)
        sg.attach(Gtk.Label(label="PIN:", halign=Gtk.Align.END), 0, 2, 1, 1)
        pin_e = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PIN, hexpand=True)
        sg.attach(pin_e, 1, 2, 2, 1)
        sg.attach(Gtk.Label(label="Key label:", halign=Gtk.Align.END), 0, 3, 1, 1)
        kl_e = Gtk.Entry(hexpand=True); kl_e.set_placeholder_text("(auto)")
        sg.attach(kl_e, 1, 3, 2, 1)
        ca.add(sc_frame)

        # PKCS#12
        p12_frame = Gtk.Frame(label="Certificate File")
        pg = Gtk.Grid(column_spacing=10, row_spacing=6)
        pg.set_margin_start(8); pg.set_margin_end(8); pg.set_margin_top(4); pg.set_margin_bottom(8)
        p12_frame.add(pg)
        pg.attach(Gtk.Label(label="File:", halign=Gtk.Align.END), 0, 0, 1, 1)
        p12_e = Gtk.Entry(hexpand=True)
        pg.attach(p12_e, 1, 0, 1, 1)
        p12b = Gtk.Button(label="...")
        p12b.connect("clicked", lambda _: self._browse_file(dlg, p12_e, "*.p12 *.pfx"))
        pg.attach(p12b, 2, 0, 1, 1)
        pg.attach(Gtk.Label(label="Password:", halign=Gtk.Align.END), 0, 1, 1, 1)
        p12pw = Gtk.Entry(visibility=False, hexpand=True)
        pg.attach(p12pw, 1, 1, 2, 1)
        ca.add(p12_frame)

        def toggle(*_):
            sc_frame.set_visible(rb_sc.get_active())
            p12_frame.set_visible(rb_p12.get_active())
        rb_sc.connect("toggled", toggle); rb_p12.connect("toggled", toggle)
        p12_frame.set_visible(False)

        ca.add(Gtk.Separator())
        cg = Gtk.Grid(column_spacing=10, row_spacing=6)
        cg.attach(Gtk.Label(label="Reason:", halign=Gtk.Align.END), 0, 0, 1, 1)
        rea_e = Gtk.Entry(hexpand=True); rea_e.set_placeholder_text("Document approval")
        cg.attach(rea_e, 1, 0, 1, 1)
        cg.attach(Gtk.Label(label="Location:", halign=Gtk.Align.END), 0, 1, 1, 1)
        loc_e = Gtk.Entry(hexpand=True); cg.attach(loc_e, 1, 1, 1, 1)
        vis_chk = Gtk.CheckButton(label="Visible signature on current page")
        cg.attach(vis_chk, 0, 2, 2, 1)
        ca.add(cg)

        ca.add(Gtk.Separator())
        ob = Gtk.Box(spacing=10); ob.add(Gtk.Label(label="Output:"))
        out_e = Gtk.Entry(hexpand=True)
        base, ext = os.path.splitext(self.file_path)
        out_e.set_text(f"{base}_signed{ext}")
        ob.add(out_e); ca.add(ob)

        ca.show_all(); toggle()

        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy(); return

        reason = rea_e.get_text().strip() or None
        location = loc_e.get_text().strip() or None
        visible = vis_chk.get_active()
        output = out_e.get_text().strip()
        rect = None
        if visible:
            pg2 = self.viewer.doc[self.viewer.page_num]
            pw, ph = pg2.rect.width, pg2.rect.height
            rect = (pw - 260, ph - 80, pw - 20, ph - 20)
        try:
            if rb_sc.get_active():
                lib = lib_e.get_text().strip()
                pin = pin_e.get_text()
                if not lib: raise ValueError("Library path required.")
                if not pin: raise ValueError("PIN required.")
                signer = create_pkcs11_signer(lib, int(slot_sp.get_value()), pin,
                                              kl_e.get_text().strip() or None)
            else:
                pf = p12_e.get_text().strip()
                if not pf: raise ValueError("Certificate file required.")
                signer = create_pkcs12_signer(pf, p12pw.get_text())
            sign_pdf(self.file_path, output, signer, reason=reason, location=location,
                     visible=visible, page=self.viewer.page_num, rect=rect)
            self.status.push(0, f"Signed \u2192 {output}")
            dlg.destroy()
            self._ask_open(output)
        except Exception as exc:
            dlg.destroy()
            self._err(f"Signing failed:\n{exc}")

    # ── tool dialogs ────────────────────────────────────────────

    def _on_merge(self):
        dlg = Gtk.FileChooserDialog(title="Select PDFs to Merge", parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.set_select_multiple(True)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        self._add_pdf_filter(dlg)
        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy(); return
        paths = dlg.get_filenames(); dlg.destroy()
        if len(paths) < 2:
            return self._err("Select at least 2 PDFs.")
        output = self._pick_save("Save Merged PDF", "merged.pdf")
        if not output:
            return
        try:
            T.merge_pdfs(paths, output)
            self.status.push(0, f"Merged {len(paths)} PDFs")
            self._ask_open(output)
        except Exception as e:
            self._err(f"Merge failed:\n{e}")

    def _on_split(self):
        path = self._pick_pdf("Select PDF to Split")
        if not path:
            return
        doc = fitz.open(path); n = len(doc); doc.close()

        dlg = Gtk.Dialog(title="Split PDF", parent=self, flags=Gtk.DialogFlags.MODAL)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Split", Gtk.ResponseType.OK)
        ca = dlg.get_content_area()
        ca.set_spacing(8); ca.set_margin_start(12); ca.set_margin_end(12); ca.set_margin_top(8)
        ca.add(Gtk.Label(label=f"PDF has {n} pages.", halign=Gtk.Align.START))
        ca.add(Gtk.Label(label="Page ranges (e.g. 1-3, 5, 7-10):", halign=Gtk.Align.START))
        entry = Gtk.Entry(); entry.set_placeholder_text("Leave blank for one file per page")
        ca.add(entry); ca.show_all()
        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy(); return
        rtxt = entry.get_text().strip(); dlg.destroy()

        out_dir = self._pick_dir("Output Directory")
        if not out_dir:
            return
        try:
            ranges = T.parse_ranges(rtxt, n) if rtxt else None
            outs = T.split_pdf(path, out_dir, ranges)
            self.status.push(0, f"Split into {len(outs)} files")
        except Exception as e:
            self._err(f"Split failed:\n{e}")

    def _on_pages(self):
        if not self.viewer.doc:
            p = self._pick_pdf("Select PDF")
            if p:
                self._open_file(p)
            if not self.viewer.doc:
                return

        doc = self.viewer.doc
        n = len(doc)
        pages = [{"idx": i, "rot": 0} for i in range(n)]

        # Render small thumbnails
        thumbs = {}
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
            png = pix.tobytes("png")
            ld = GdkPixbuf.PixbufLoader.new_with_type("png")
            ld.write(png); ld.close()
            thumbs[i] = ld.get_pixbuf()

        dlg = Gtk.Dialog(title=f"Manage Pages ({n})", parent=self,
                         flags=Gtk.DialogFlags.MODAL)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        dlg.set_default_size(520, 480)

        ca = dlg.get_content_area()
        sw = Gtk.ScrolledWindow(); sw.set_vexpand(True)
        listbox = Gtk.ListBox(); listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        sw.add(listbox); ca.add(sw)

        def rebuild():
            for ch in listbox.get_children():
                listbox.remove(ch)
            for i, p in enumerate(pages):
                row = Gtk.ListBoxRow()
                hb = Gtk.Box(spacing=8)
                hb.set_margin_start(6); hb.set_margin_end(6)
                hb.set_margin_top(3); hb.set_margin_bottom(3)
                hb.pack_start(Gtk.Image.new_from_pixbuf(thumbs[p["idx"]]), False, False, 0)
                rot = (doc[p["idx"]].rotation + p["rot"]) % 360
                lbl = f"Page {p['idx']+1}"
                if rot:
                    lbl += f"  ({rot}\u00b0)"
                hb.pack_start(Gtk.Label(label=lbl), True, True, 0)
                for icon, tip, cb in [
                    ("go-up-symbolic", "Up", lambda _, ii=i: move(ii, -1)),
                    ("go-down-symbolic", "Down", lambda _, ii=i: move(ii, 1)),
                    ("object-rotate-right-symbolic", "CW", lambda _, ii=i: rotate(ii, 90)),
                    ("object-rotate-left-symbolic", "CCW", lambda _, ii=i: rotate(ii, -90)),
                    ("edit-delete-symbolic", "Del", lambda _, ii=i: delete(ii)),
                ]:
                    b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
                    b.set_tooltip_text(tip)
                    b.connect("clicked", cb)
                    hb.pack_end(b, False, False, 0)
                row.add(hb); listbox.add(row)
            listbox.show_all()

        def move(i, d):
            j = i + d
            if 0 <= j < len(pages):
                pages[i], pages[j] = pages[j], pages[i]; rebuild()

        def rotate(i, a):
            pages[i]["rot"] = (pages[i]["rot"] + a) % 360; rebuild()

        def delete(i):
            if len(pages) > 1:
                pages.pop(i); rebuild()

        rebuild(); ca.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            new_order = [p["idx"] for p in pages]
            new_doc = T.reorder_pages(doc, new_order)
            for i, p in enumerate(pages):
                if p["rot"]:
                    new_doc[i].set_rotation((new_doc[i].rotation + p["rot"]) % 360)
            self.viewer.doc = new_doc
            self.modified = True
            self.viewer.set_page(0)
            self.status.push(0, "Pages updated")
        dlg.destroy()

    def _on_img2pdf(self):
        dlg = Gtk.FileChooserDialog(title="Select Images", parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.set_select_multiple(True)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        ff = Gtk.FileFilter(); ff.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff", "*.gif"):
            ff.add_pattern(p)
        dlg.add_filter(ff)
        if dlg.run() != Gtk.ResponseType.OK:
            dlg.destroy(); return
        paths = dlg.get_filenames(); dlg.destroy()
        output = self._pick_save("Save PDF", "images.pdf")
        if not output:
            return
        try:
            T.images_to_pdf(paths, output)
            self.status.push(0, f"PDF created from {len(paths)} images")
            self._ask_open(output)
        except Exception as e:
            self._err(f"Conversion failed:\n{e}")

    def _on_pdf2img(self):
        path = (self.file_path if self.viewer.doc else None) or self._pick_pdf("Select PDF")
        if not path:
            return
        out_dir = self._pick_dir("Output Directory")
        if not out_dir:
            return
        try:
            outs = T.pdf_to_images(path, out_dir)
            self.status.push(0, f"Exported {len(outs)} images to {out_dir}")
        except Exception as e:
            self._err(f"Export failed:\n{e}")

    def _on_compress(self):
        path = self._pick_pdf("Select PDF to Compress")
        if not path:
            return
        base = os.path.splitext(os.path.basename(path))[0]
        output = self._pick_save("Save Compressed PDF", f"{base}_compressed.pdf")
        if not output:
            return
        try:
            old = os.path.getsize(path)
            T.compress_pdf(path, output)
            new = os.path.getsize(output)
            pct = (1 - new / old) * 100 if old else 0
            self.status.push(0, f"Compressed: {old//1024}KB \u2192 {new//1024}KB ({pct:.0f}% smaller)")
            self._ask_open(output)
        except Exception as e:
            self._err(f"Compression failed:\n{e}")

    def _on_metadata(self):
        if not self.viewer.doc:
            p = self._pick_pdf("Select PDF")
            if p:
                self._open_file(p)
            if not self.viewer.doc:
                return
        T.remove_metadata(self.viewer.doc)
        self.modified = True
        self.status.push(0, "Metadata removed")

    # ── helpers ─────────────────────────────────────────────────

    def _pick_pdf(self, title):
        dlg = Gtk.FileChooserDialog(title=title, parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        self._add_pdf_filter(dlg)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return path

    def _pick_save(self, title, default="output.pdf"):
        dlg = Gtk.FileChooserDialog(title=title, parent=self,
                                    action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name(default)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if path and not path.lower().endswith(".pdf"):
            path += ".pdf"
        return path

    def _pick_dir(self, title):
        dlg = Gtk.FileChooserDialog(title=title, parent=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        "Select", Gtk.ResponseType.OK)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return path

    def _add_pdf_filter(self, dlg):
        f = Gtk.FileFilter(); f.set_name("PDF files")
        f.add_pattern("*.pdf"); f.add_mime_type("application/pdf")
        dlg.add_filter(f)

    def _browse_file(self, parent, entry, patterns):
        fc = Gtk.FileChooserDialog(title="Select File", parent=parent,
                                   action=Gtk.FileChooserAction.OPEN)
        fc.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        ff = Gtk.FileFilter()
        for p in patterns.split():
            ff.add_pattern(p)
        fc.add_filter(ff)
        if fc.run() == Gtk.ResponseType.OK:
            entry.set_text(fc.get_filename())
        fc.destroy()

    def _ask_open(self, path):
        md = Gtk.MessageDialog(parent=self, flags=Gtk.DialogFlags.MODAL,
                               message_type=Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.YES_NO,
                               text="Done! Open the result?")
        if md.run() == Gtk.ResponseType.YES:
            self._open_file(path)
        md.destroy()

    def _sync_labels(self):
        if self.viewer.doc:
            self.page_label.set_text(f"{self.viewer.page_num + 1} / {self.viewer.page_count}")
        self.zoom_label.set_text(f"{int(self.viewer.zoom * 100)}%")

    def _on_close(self, _w, _ev):
        if not self.modified:
            return False
        md = Gtk.MessageDialog(parent=self, flags=Gtk.DialogFlags.MODAL,
                               message_type=Gtk.MessageType.WARNING,
                               buttons=Gtk.ButtonsType.NONE,
                               text="Save changes before closing?")
        md.add_buttons("Discard", Gtk.ResponseType.REJECT,
                       Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        r = md.run(); md.destroy()
        if r == Gtk.ResponseType.OK:
            self._on_save(); return False
        return r != Gtk.ResponseType.REJECT

    def _on_drag(self, _w, _ctx, _x, _y, data, _info, _time):
        uris = data.get_uris()
        if uris:
            path = GLib.filename_from_uri(uris[0])[0]
            if path.lower().endswith(".pdf"):
                self._open_file(path)

    def _err(self, msg):
        md = Gtk.MessageDialog(parent=self, flags=Gtk.DialogFlags.MODAL,
                               message_type=Gtk.MessageType.ERROR,
                               buttons=Gtk.ButtonsType.OK, text=msg)
        md.run(); md.destroy()


# ── Application ─────────────────────────────────────────────────


class LightPDFApp(Gtk.Application):
    def __init__(self, filepath=None):
        super().__init__(application_id="com.github.lightpdf")
        self.filepath = filepath

    def do_activate(self):
        MainWindow(self, filepath=self.filepath)

    def do_startup(self):
        Gtk.Application.do_startup(self)
