"""PDF manipulation tools — merge, split, convert, compress, metadata."""

import os
import fitz


def merge_pdfs(input_paths, output_path):
    """Merge multiple PDFs into one."""
    result = fitz.open()
    for path in input_paths:
        src = fitz.open(path)
        result.insert_pdf(src)
        src.close()
    result.save(output_path)
    result.close()


def parse_ranges(text, max_page):
    """Parse '1-3, 5, 7-10' → [(0,2), (4,4), (6,9)] (0-indexed)."""
    ranges = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            s = max(0, min(int(a.strip()) - 1, max_page - 1))
            e = max(s, min(int(b.strip()) - 1, max_page - 1))
            ranges.append((s, e))
        else:
            n = max(0, min(int(part.strip()) - 1, max_page - 1))
            ranges.append((n, n))
    return ranges


def split_pdf(input_path, output_dir, ranges=None):
    """Split PDF by page ranges. Returns list of output paths."""
    doc = fitz.open(input_path)
    base = os.path.splitext(os.path.basename(input_path))[0]
    if ranges is None:
        ranges = [(i, i) for i in range(len(doc))]
    outputs = []
    for i, (s, e) in enumerate(ranges):
        out = fitz.open()
        out.insert_pdf(doc, from_page=s, to_page=e)
        name = f"{base}_p{s+1}-{e+1}.pdf" if s != e else f"{base}_p{s+1}.pdf"
        path = os.path.join(output_dir, name)
        out.save(path)
        out.close()
        outputs.append(path)
    doc.close()
    return outputs


def reorder_pages(doc, new_order):
    """Return a new doc with pages reordered per *new_order* index list."""
    new = fitz.open()
    for idx in new_order:
        new.insert_pdf(doc, from_page=idx, to_page=idx)
    return new


def images_to_pdf(image_paths, output_path):
    """Convert images to PDF — one image per page."""
    doc = fitz.open()
    for path in image_paths:
        img = fitz.open(path)
        pdf_bytes = img.convert_to_pdf()
        img_pdf = fitz.open("pdf", pdf_bytes)
        doc.insert_pdf(img_pdf)
        img_pdf.close()
        img.close()
    doc.save(output_path)
    doc.close()


def pdf_to_images(input_path, output_dir, dpi=150, fmt="png"):
    """Export pages as images. Returns output paths."""
    doc = fitz.open(input_path)
    base = os.path.splitext(os.path.basename(input_path))[0]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    outputs = []
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(matrix=mat)
        path = os.path.join(output_dir, f"{base}_page{i+1}.{fmt}")
        pix.save(path)
        outputs.append(path)
    doc.close()
    return outputs


def compress_pdf(input_path, output_path):
    """Compress PDF via garbage collection and stream deflation."""
    doc = fitz.open(input_path)
    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()


def remove_metadata(doc):
    """Strip all metadata and XMP from the document in place."""
    doc.set_metadata({
        "author": "", "title": "", "subject": "",
        "keywords": "", "creator": "", "producer": "",
        "creationDate": "", "modDate": "",
    })
    doc.del_xml_metadata()
