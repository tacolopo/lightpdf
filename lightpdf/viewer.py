"""PDF rendering widget with edit-mode text selection and annotation drawing."""

import math
import fitz
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf

_BASE_SCALE = 96 / 72  # PDF pts → screen px at zoom 1.0

# Annotation tool constants
TOOL_NONE = ""
TOOL_HIGHLIGHT = "highlight"
TOOL_NOTE = "note"
TOOL_RECT = "rect"
TOOL_CIRCLE = "circle"
TOOL_LINE = "line"
TOOL_DRAW = "draw"
TOOL_IMAGE = "image"


class PDFViewer(Gtk.ScrolledWindow):
    """Scrollable PDF viewer with text-edit and annotation modes."""

    def __init__(self):
        super().__init__()
        self.doc = None
        self.page_num = 0
        self.zoom = 1.0
        self.pixbuf = None

        # Edit mode
        self.edit_mode = False
        self.text_blocks = []
        self.selected_block = None
        self.hover_block = None

        # Annotation mode
        self.annot_mode = False
        self.annot_tool = TOOL_NONE
        self.annot_color = (1.0, 0.0, 0.0)
        self.annot_width = 2.0
        self._dragging = False
        self._drag_start = (0, 0)
        self._drag_end = (0, 0)
        self._ink_points = []

        # Callbacks (set by MainWindow)
        self.on_text_selected = None
        self.on_page_changed = None
        self.on_zoom_changed = None
        self.on_annotation_added = None
        self.on_note_requested = None
        self.on_image_requested = None

        self._area = Gtk.DrawingArea()
        self._area.connect("draw", self._on_draw)
        self._area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self._area.connect("button-press-event", self._on_click)
        self._area.connect("button-release-event", self._on_release)
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
        self.selected_block = self.hover_block = None
        self._dragging = False
        self._render_page()

    @property
    def page_count(self):
        return len(self.doc) if self.doc else 0

    def set_page(self, num):
        if self.doc and 0 <= num < len(self.doc):
            self.page_num = num
            self.selected_block = self.hover_block = None
            self._dragging = False
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
        if active:
            self.annot_mode = False
        self.selected_block = self.hover_block = None
        if active:
            self._extract_text_blocks()
        else:
            self.text_blocks = []
        self._area.queue_draw()

    def set_annot_mode(self, active):
        self.annot_mode = active
        if active:
            self.edit_mode = False
            self.text_blocks = []
        self._dragging = False
        self._area.queue_draw()

    # ── rendering ───────────────────────────────────────────────

    def _render_page(self):
        if not self.doc:
            return
        page = self.doc[self.page_num]
        scale = self.zoom * _BASE_SCALE
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
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
            if b["type"] != 0:
                continue
            r = fitz.Rect(b["bbox"])
            self.text_blocks.append({
                "rect": r,
                "scaled_rect": fitz.Rect(r.x0 * scale, r.y0 * scale,
                                         r.x1 * scale, r.y1 * scale),
                "block": b,
            })

    # ── drawing ─────────────────────────────────────────────────

    def _on_draw(self, widget, cr):
        if self.pixbuf:
            Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
            cr.paint()

        # Edit-mode overlays
        if self.edit_mode:
            for idx, blk_item in ((self.hover_block, "hover"),
                                  (self.selected_block, "sel")):
                if idx is not None and idx < len(self.text_blocks):
                    r = self.text_blocks[idx]["scaled_rect"]
                    if blk_item == "hover":
                        cr.set_source_rgba(0.2, 0.5, 1.0, 0.12)
                    else:
                        cr.set_source_rgba(1.0, 0.5, 0.0, 0.18)
                    cr.rectangle(r.x0, r.y0, r.width, r.height)
                    cr.fill()
                    if blk_item == "hover":
                        cr.set_source_rgba(0.2, 0.5, 1.0, 0.5)
                    else:
                        cr.set_source_rgba(1.0, 0.5, 0.0, 0.8)
                    cr.set_line_width(1 if blk_item == "hover" else 2)
                    cr.rectangle(r.x0, r.y0, r.width, r.height)
                    cr.stroke()

        # Annotation drag preview
        if self.annot_mode and self._dragging:
            self._draw_annot_preview(cr)

    def _draw_annot_preview(self, cr):
        x0, y0 = self._drag_start
        x1, y1 = self._drag_end
        c = self.annot_color
        w = self.annot_width

        if self.annot_tool == TOOL_HIGHLIGHT:
            cr.set_source_rgba(1, 1, 0, 0.35)
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.fill()
        elif self.annot_tool == TOOL_RECT:
            cr.set_source_rgba(*c, 0.6)
            cr.set_line_width(w)
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.stroke()
        elif self.annot_tool == TOOL_CIRCLE:
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            rx = abs(x1 - x0) / 2
            ry = abs(y1 - y0) / 2
            if rx > 0 and ry > 0:
                cr.save()
                cr.translate(cx, cy)
                cr.scale(rx, ry)
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.restore()
                cr.set_source_rgba(*c, 0.6)
                cr.set_line_width(w)
                cr.stroke()
        elif self.annot_tool == TOOL_LINE:
            cr.set_source_rgba(*c, 0.6)
            cr.set_line_width(w)
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()
        elif self.annot_tool == TOOL_DRAW:
            if len(self._ink_points) > 1:
                cr.set_source_rgba(*c, 0.6)
                cr.set_line_width(w)
                cr.move_to(*self._ink_points[0])
                for p in self._ink_points[1:]:
                    cr.line_to(*p)
                cr.stroke()

    # ── input handling ──────────────────────────────────────────

    def _hit_test(self, x, y):
        for i, blk in enumerate(self.text_blocks):
            r = blk["scaled_rect"]
            if r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1:
                return i
        return None

    def _screen_to_pdf(self, sx, sy):
        s = self.zoom * _BASE_SCALE
        return sx / s, sy / s

    def _on_click(self, widget, event):
        if event.button != 1:
            return

        # ── annotation mode ──
        if self.annot_mode and self.annot_tool:
            if self.annot_tool == TOOL_NOTE:
                px, py = self._screen_to_pdf(event.x, event.y)
                if self.on_note_requested:
                    self.on_note_requested(fitz.Point(px, py))
                return
            if self.annot_tool == TOOL_IMAGE:
                px, py = self._screen_to_pdf(event.x, event.y)
                if self.on_image_requested:
                    self.on_image_requested(fitz.Point(px, py))
                return
            self._dragging = True
            self._drag_start = (event.x, event.y)
            self._drag_end = (event.x, event.y)
            if self.annot_tool == TOOL_DRAW:
                self._ink_points = [(event.x, event.y)]
            return

        # ── edit mode ──
        if self.edit_mode:
            idx = self._hit_test(event.x, event.y)
            self.selected_block = idx
            self._area.queue_draw()
            if idx is not None and self.on_text_selected:
                self.on_text_selected(self.text_blocks[idx])

    def _on_release(self, widget, event):
        if self.annot_mode and self._dragging:
            self._dragging = False
            self._drag_end = (event.x, event.y)
            self._commit_annotation()

    def _on_motion(self, widget, event):
        # annotation drag
        if self.annot_mode and self._dragging:
            self._drag_end = (event.x, event.y)
            if self.annot_tool == TOOL_DRAW:
                self._ink_points.append((event.x, event.y))
            self._area.queue_draw()
            return

        # edit hover
        if self.edit_mode:
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

    # ── annotation creation ─────────────────────────────────────

    def _commit_annotation(self):
        if not self.doc:
            return
        page = self.doc[self.page_num]
        s = self.zoom * _BASE_SCALE
        px0, py0 = self._drag_start[0] / s, self._drag_start[1] / s
        px1, py1 = self._drag_end[0] / s, self._drag_end[1] / s
        rect = fitz.Rect(min(px0, px1), min(py0, py1),
                         max(px0, px1), max(py0, py1))
        c = self.annot_color
        w = self.annot_width

        if self.annot_tool == TOOL_HIGHLIGHT:
            words = page.get_text("words")
            quads = [fitz.Rect(wd[:4]).quad for wd in words
                     if rect.intersects(fitz.Rect(wd[:4]))]
            if quads:
                a = page.add_highlight_annot(quads=quads)
                a.set_colors(stroke=c)
                a.update()
        elif self.annot_tool == TOOL_RECT:
            a = page.add_rect_annot(rect)
            a.set_colors(stroke=c)
            a.set_border(width=w)
            a.update()
        elif self.annot_tool == TOOL_CIRCLE:
            a = page.add_circle_annot(rect)
            a.set_colors(stroke=c)
            a.set_border(width=w)
            a.update()
        elif self.annot_tool == TOOL_LINE:
            a = page.add_line_annot(fitz.Point(px0, py0), fitz.Point(px1, py1))
            a.set_colors(stroke=c)
            a.set_border(width=w)
            a.update()
        elif self.annot_tool == TOOL_DRAW:
            pts = [(x / s, y / s) for x, y in self._ink_points]
            if len(pts) > 1:
                a = page.add_ink_annot([pts])
                a.set_colors(stroke=c)
                a.set_border(width=w)
                a.update()
        else:
            return

        self._render_page()
        if self.on_annotation_added:
            self.on_annotation_added()
