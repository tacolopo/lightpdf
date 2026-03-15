"""Text editing operations on PDF documents."""

import fitz


class TextEditor:
    """Edit text in PDF pages using redact-and-reinsert."""

    def __init__(self):
        self.history = []  # (page_num, doc_bytes)
        self.redo_stack = []
        self._max_history = 8

    # ── text extraction helpers ─────────────────────────────────

    @staticmethod
    def get_block_text(block):
        """Extract plain text from a text-block dict."""
        lines = []
        for line in block["lines"]:
            parts = [span["text"] for span in line["spans"]]
            lines.append("".join(parts))
        return "\n".join(lines)

    @staticmethod
    def get_block_font(block):
        """Return (fontname, size, color_int) of the dominant span."""
        best, best_len = ("helv", 11.0, 0), 0
        for line in block["lines"]:
            for span in line["spans"]:
                n = len(span["text"])
                if n > best_len:
                    best = (span["font"], span["size"], span["color"])
                    best_len = n
        return best

    # ── editing ─────────────────────────────────────────────────

    def replace_text(self, doc, page_num, block, new_text):
        """Replace the text inside *block* with *new_text*."""
        self._push_undo(doc, page_num)

        page = doc[page_num]
        rect = fitz.Rect(block["bbox"])
        font_name, font_size, color_int = self.get_block_font(block)

        # Redact old text (fills with white)
        page.add_redact_annot(rect)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        # Convert colour
        r = ((color_int >> 16) & 0xFF) / 255.0
        g = ((color_int >> 8) & 0xFF) / 255.0
        b = (color_int & 0xFF) / 255.0

        fontname = self._map_font(font_name)

        # Use insert_text (no bounding-box clipping) so replacement text
        # is never silently truncated even if it's longer than the original.
        page.insert_text(
            fitz.Point(rect.x0, rect.y0 + font_size),
            new_text,
            fontsize=font_size,
            fontname=fontname,
            color=(r, g, b),
        )
        self.redo_stack.clear()

    def add_text(self, doc, page_num, rect, text, fontsize=11, color=(0, 0, 0)):
        """Insert new text at *rect*."""
        self._push_undo(doc, page_num)
        page = doc[page_num]
        page.insert_textbox(
            fitz.Rect(rect), text, fontsize=fontsize, fontname="helv", color=color
        )
        self.redo_stack.clear()

    # ── undo / redo ─────────────────────────────────────────────

    def _push_undo(self, doc, page_num):
        self.history.append((page_num, doc.tobytes()))
        if len(self.history) > self._max_history:
            self.history.pop(0)

    def undo(self, doc):
        """Return (new_doc, page_num) or None."""
        if not self.history:
            return None
        page_num, saved = self.history.pop()
        self.redo_stack.append((page_num, doc.tobytes()))
        return fitz.open(stream=saved, filetype="pdf"), page_num

    def redo(self, doc):
        """Return (new_doc, page_num) or None."""
        if not self.redo_stack:
            return None
        page_num, saved = self.redo_stack.pop()
        self.history.append((page_num, doc.tobytes()))
        return fitz.open(stream=saved, filetype="pdf"), page_num

    def can_undo(self):
        return bool(self.history)

    def can_redo(self):
        return bool(self.redo_stack)

    # ── font mapping ───────────────────────────────────────────

    @staticmethod
    def _map_font(pdf_name):
        """Map a PDF font name to a PyMuPDF built-in font."""
        n = pdf_name.lower()
        bold = "bold" in n
        italic = "italic" in n or "oblique" in n
        if "courier" in n or "mono" in n:
            if bold and italic:
                return "cobi"
            if bold:
                return "cobo"
            if italic:
                return "coit"
            return "cour"
        if "times" in n or "serif" in n:
            if bold and italic:
                return "tibi"
            if bold:
                return "tibo"
            if italic:
                return "tiit"
            return "tiro"
        # default: Helvetica family
        if bold and italic:
            return "hebi"
        if bold:
            return "hebo"
        if italic:
            return "heit"
        return "helv"
