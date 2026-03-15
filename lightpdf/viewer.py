"""PDF rendering widget using PyMuPDF and GTK3."""

import fitz
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

# PDF points are 72/inch; standard screen ~96 DPI
_BASE_SCALE = 96 / 72


class PDFViewer(Gtk.ScrolledWindow):
    """Scrollable PDF page viewer with edit-mode text selection."""

    def __init__(self):
        super().__init__()
        self.doc = None
        self.page_num = 0
        self.zoom = 1.0
        self.pixbuf = None
        self.edit_mode = False
        self.text_blocks = []
        self.selected_block = None
        self.hover_block = None

        # Callbacks set by the main window
        self.on_text_selected = None
        self.on_page_changed = None
        self.on_zoom_changed = None

        self._area = Gtk.DrawingArea()
        self._area.connect("draw", self._on_draw)
        self._area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self._area.connect("button-press-event", self._on_click)
        self._area.connect("motion-notify-event", self._on_motion)
        self._area.connect("scroll-event", self._on_scroll)

        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.add(self._area)

    # ── public API ──────────────────────────────────────────────

    def load_document(self, path):
        if self.doc:
            self.doc.close()
        self.doc = fitz.open(path)
        self.page_num = 0
        self.zoom = 1.0
        self.selected_block = None
        self.hover_block = None
        self._render_page()

    @property
    def page_count(self):
        return len(self.doc) if self.doc else 0

    def set_page(self, num):
        if self.doc and 0 <= num < len(self.doc):
            self.page_num = num
            self.selected_block = None
            self.hover_block = None
            self._render_page()
            if self.on_page_changed:
                self.on_page_changed(num)

    def set_zoom(self, zoom):
        self.zoom = max(0.25, min(5.0, zoom))
        self._render_page()
        if self.on_zoom_changed:
            self.on_zoom_changed(self.zoom)

    def set_edit_mode(self, active):
        self.edit_mode = active
        self.selected_block = None
        self.hover_block = None
        if active:
            self._extract_text_blocks()
        else:
            self.text_blocks = []
        self._area.queue_draw()

    # ── rendering ───────────────────────────────────────────────

    def _render_page(self):
        if not self.doc:
            return
        page = self.doc[self.page_num]
        scale = self.zoom * _BASE_SCALE
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)

        # PNG round-trip is safest with PyGObject memory management
        png_data = pix.tobytes("png")
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png_data)
        loader.close()
        self.pixbuf = loader.get_pixbuf()

        self._area.set_size_request(pix.width, pix.height)

        if self.edit_mode:
            self._extract_text_blocks()

        self._area.queue_draw()

    def _extract_text_blocks(self):
        if not self.doc:
            return
        page = self.doc[self.page_num]
        raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        scale = self.zoom * _BASE_SCALE
        self.text_blocks = []
        for b in raw["blocks"]:
            if b["type"] != 0:  # skip images
                continue
            r = fitz.Rect(b["bbox"])
            self.text_blocks.append(
                {
                    "rect": r,
                    "scaled_rect": fitz.Rect(
                        r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale
                    ),
                    "block": b,
                }
            )

    # ── drawing ─────────────────────────────────────────────────

    def _on_draw(self, widget, cr):
        if self.pixbuf:
            Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
            cr.paint()

        if not self.edit_mode:
            return

        # Hover highlight
        if self.hover_block is not None and self.hover_block < len(self.text_blocks):
            r = self.text_blocks[self.hover_block]["scaled_rect"]
            cr.set_source_rgba(0.2, 0.5, 1.0, 0.12)
            cr.rectangle(r.x0, r.y0, r.width, r.height)
            cr.fill()
            cr.set_source_rgba(0.2, 0.5, 1.0, 0.5)
            cr.set_line_width(1)
            cr.rectangle(r.x0, r.y0, r.width, r.height)
            cr.stroke()

        # Selection highlight
        if self.selected_block is not None and self.selected_block < len(
            self.text_blocks
        ):
            r = self.text_blocks[self.selected_block]["scaled_rect"]
            cr.set_source_rgba(1.0, 0.5, 0.0, 0.18)
            cr.rectangle(r.x0, r.y0, r.width, r.height)
            cr.fill()
            cr.set_source_rgba(1.0, 0.5, 0.0, 0.8)
            cr.set_line_width(2)
            cr.rectangle(r.x0, r.y0, r.width, r.height)
            cr.stroke()

    # ── input handling ──────────────────────────────────────────

    def _hit_test(self, x, y):
        for i, blk in enumerate(self.text_blocks):
            r = blk["scaled_rect"]
            if r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1:
                return i
        return None

    def _on_click(self, widget, event):
        if not self.edit_mode or event.button != 1:
            return
        idx = self._hit_test(event.x, event.y)
        self.selected_block = idx
        self._area.queue_draw()
        if idx is not None and self.on_text_selected:
            self.on_text_selected(self.text_blocks[idx])

    def _on_motion(self, widget, event):
        if not self.edit_mode:
            return
        old = self.hover_block
        self.hover_block = self._hit_test(event.x, event.y)
        if old != self.hover_block:
            self._area.queue_draw()
            name = "text" if self.hover_block is not None else "default"
            cur = Gdk.Cursor.new_from_name(widget.get_display(), name)
            win = widget.get_window()
            if win:
                win.set_cursor(cur)

    def _on_scroll(self, widget, event):
        if not (event.state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            delta = 0.1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = -0.1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            ok, dx, dy = event.get_scroll_deltas()
            delta = -dy * 0.1 if ok else 0
        else:
            return False
        self.set_zoom(self.zoom + delta)
        return True
