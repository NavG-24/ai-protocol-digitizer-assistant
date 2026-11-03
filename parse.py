"""Page-level PDF parsing with PyMuPDF: text per page, page images, and highlights.
Page numbers are 1-based PDF page numbers (the first page of the file is 1),
which is what every citation in the app refers to."""

import pymupdf


def page_texts(pdf_bytes):
    """List of page texts; index 0 is PDF page 1."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def render(pdf_bytes, page, highlight=None, zoom=1.6):
    """PNG of one page. highlight is text to mark in yellow if it is found on the page
    (long text is matched on its first words, since PDF line breaks split it)."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        p = doc[page - 1]
        if highlight:
            words = " ".join(str(highlight).split())
            hits = p.search_for(words)
            if not hits and len(words) > 60:
                hits = p.search_for(" ".join(words.split()[:8]))
            for rect in hits:
                p.add_highlight_annot(rect)
        return p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
