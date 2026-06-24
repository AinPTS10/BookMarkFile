"""
PDF Bookmark & Navigation Tool
Engineering report processor — detects headings, generates bookmarks, creates TOC
"""

import os
import re
import json
import uuid
import io
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, session

import pdfplumber
from pypdf import PdfReader, PdfWriter, generic
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ── App setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "pdf-bookmark-tool-2024"
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB


# ── Heading detection ─────────────────────────────────────────────────────────
HEADING_PATTERNS = [
    # Numbered headings: 1. Title, 1.1 Title, 1.1.1 Title
    (r"^(\d+)\.\s+([A-Z][^\n]{2,80})$", 1),
    (r"^(\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 2),
    (r"^(\d+\.\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 3),
    (r"^(\d+\.\d+\.\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 4),
    # ALL CAPS headings (common in engineering docs)
    (r"^([A-Z][A-Z\s\-\/]{4,60})$", 1),
    # "SECTION X — Title" patterns
    (r"^(SECTION\s+\d+[\.\:]?\s*[-–—]?\s*.{2,60})$", 1),
    (r"^(CHAPTER\s+\d+[\.\:]?\s*[-–—]?\s*.{2,60})$", 1),
    # Appendix patterns
    (r"^(APPENDIX\s+[A-Z0-9][\.\:]?\s*.{0,60})$", 2),
]

FONT_SIZE_THRESHOLDS = {
    "h1": 16,
    "h2": 13,
    "h3": 11,
    "body": 9,
}


def detect_headings_by_text(text_lines: list[dict]) -> list[dict]:
    """Detect headings using regex patterns on text content."""
    headings = []
    for line in text_lines:
        text = line["text"].strip()
        if not text or len(text) < 3:
            continue
        for pattern, level in HEADING_PATTERNS:
            m = re.match(pattern, text)
            if m:
                headings.append({
                    "text": text,
                    "level": level,
                    "page": line["page"],
                    "method": "pattern",
                })
                break
    return headings


def detect_headings_by_font(pdf_path: str) -> list[dict]:
    """Use pdfplumber to detect headings by font size."""
    headings = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(extra_attrs=["size", "fontname"])
                # Group words into lines by y-position
                lines: dict[float, list] = {}
                for w in words:
                    y = round(float(w.get("top", 0)), 1)
                    lines.setdefault(y, []).append(w)

                for y_pos in sorted(lines.keys()):
                    line_words = lines[y_pos]
                    if not line_words:
                        continue
                    text = " ".join(w["text"] for w in line_words).strip()
                    if not text or len(text) < 3:
                        continue
                    # Use max font size in the line
                    sizes = [float(w.get("size", 10)) for w in line_words]
                    max_size = max(sizes) if sizes else 10

                    level = None
                    if max_size >= FONT_SIZE_THRESHOLDS["h1"]:
                        level = 1
                    elif max_size >= FONT_SIZE_THRESHOLDS["h2"]:
                        level = 2
                    elif max_size >= FONT_SIZE_THRESHOLDS["h3"]:
                        level = 3

                    if level:
                        headings.append({
                            "text": text,
                            "level": level,
                            "page": page_num,
                            "method": "font",
                            "font_size": max_size,
                        })
    except Exception as e:
        print(f"Font detection error: {e}")
    return headings


def extract_text_lines(pdf_path: str) -> list[dict]:
    """Extract all text lines with page numbers."""
    lines = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text:
                    for line in text.split("\n"):
                        stripped = line.strip()
                        if stripped:
                            lines.append({"text": stripped, "page": page_num})
    except Exception as e:
        print(f"Text extraction error: {e}")
    return lines


def merge_and_deduplicate(font_headings: list, text_headings: list) -> list[dict]:
    """Merge font-based and pattern-based detections, remove duplicates."""
    seen = set()
    merged = []

    # Prefer font-based (more reliable for engineering PDFs)
    for h in font_headings + text_headings:
        key = (h["page"], h["text"][:40].lower())
        if key not in seen:
            seen.add(key)
            merged.append(h)

    # Sort by page then by position
    merged.sort(key=lambda x: (x["page"], x.get("y_pos", 0)))
    return merged


def analyze_pdf(pdf_path: str) -> dict:
    """Full PDF analysis — returns headings, metadata, page count."""
    result = {
        "page_count": 0,
        "headings": [],
        "metadata": {},
        "detection_method": "",
        "errors": [],
    }

    try:
        reader = PdfReader(pdf_path)
        result["page_count"] = len(reader.pages)
        meta = reader.metadata or {}
        result["metadata"] = {
            "title": meta.get("/Title", ""),
            "author": meta.get("/Author", ""),
            "subject": meta.get("/Subject", ""),
            "creator": meta.get("/Creator", ""),
        }
    except Exception as e:
        result["errors"].append(f"Metadata error: {e}")

    # Try font-based detection first
    font_headings = detect_headings_by_font(pdf_path)

    # Also try pattern-based
    text_lines = extract_text_lines(pdf_path)
    text_headings = detect_headings_by_text(text_lines)

    if font_headings:
        result["detection_method"] = "font-size + pattern"
        result["headings"] = merge_and_deduplicate(font_headings, text_headings)
    elif text_headings:
        result["detection_method"] = "pattern matching"
        result["headings"] = text_headings
    else:
        result["detection_method"] = "none detected"
        result["errors"].append(
            "No headings detected automatically. The PDF may be scanned or use unusual formatting."
        )

    # Cap to reasonable count for display
    result["heading_count"] = len(result["headings"])
    return result


# ── TOC PDF generation ────────────────────────────────────────────────────────

def generate_toc_pdf(headings: list[dict], doc_title: str, total_pages: int) -> bytes:
    """Generate a standalone Table of Contents PDF."""
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "TOCTitle",
        parent=styles["Title"],
        fontSize=22,
        textColor=colors.HexColor("#1a365d"),
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "TOCSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#4a5568"),
        spaceAfter=4,
        alignment=TA_CENTER,
    )
    h1_style = ParagraphStyle(
        "TOCH1",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#1a365d"),
        fontName="Helvetica-Bold",
        spaceBefore=10,
        spaceAfter=2,
        leftIndent=0,
    )
    h2_style = ParagraphStyle(
        "TOCH2",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#2d3748"),
        fontName="Helvetica",
        spaceBefore=3,
        spaceAfter=1,
        leftIndent=18,
    )
    h3_style = ParagraphStyle(
        "TOCH3",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#4a5568"),
        fontName="Helvetica-Oblique",
        spaceBefore=2,
        spaceAfter=0,
        leftIndent=36,
    )
    h4_style = ParagraphStyle(
        "TOCH4",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#718096"),
        fontName="Helvetica-Oblique",
        spaceBefore=1,
        spaceAfter=0,
        leftIndent=50,
    )

    story = []

    # Header
    story.append(Paragraph(doc_title or "Document", title_style))
    story.append(Paragraph("Table of Contents", subtitle_style))
    story.append(Paragraph(f"Total Pages: {total_pages}", subtitle_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2b6cb0")))
    story.append(Spacer(1, 0.4 * cm))

    if not headings:
        story.append(Paragraph("No headings were detected in this document.", styles["Normal"]))
    else:
        level_styles = {1: h1_style, 2: h2_style, 3: h3_style, 4: h4_style}
        bullets = {1: "■", 2: "▸", 3: "–", 4: "·"}

        for h in headings:
            level = min(h["level"], 4)
            style = level_styles.get(level, h3_style)
            bullet = bullets.get(level, "·")

            # Truncate long headings for display
            text = h["text"]
            if len(text) > 90:
                text = text[:87] + "..."

            # Build the TOC row as a table (text ... dots ... page)
            dots_col = "." * max(3, 60 - len(text) - level * 4)
            page_str = str(h["page"])

            indent = (level - 1) * 18

            row_data = [[
                Paragraph(f"{bullet}  {text}", style),
                Paragraph(f"<b>{page_str}</b>", ParagraphStyle(
                    "PageNum",
                    parent=styles["Normal"],
                    fontSize=style.fontSize,
                    alignment=TA_RIGHT,
                    textColor=style.textColor,
                )),
            ]]

            t = Table(row_data, colWidths=[14 * cm, 2 * cm])
            t.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), indent),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(t)

    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        f"Generated by PDF Bookmark &amp; Navigation Tool  •  {len(headings)} headings detected",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8,
                       textColor=colors.HexColor("#a0aec0"), alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def add_bookmarks_to_pdf(pdf_path: str, headings: list[dict]) -> bytes:
    """Add PDF bookmarks (outline) to the original PDF."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    # Copy all pages
    for page in reader.pages:
        writer.add_page(page)

    # Copy existing metadata
    if reader.metadata:
        writer.add_metadata(reader.metadata)

    # Build bookmark tree
    # We keep track of parent bookmarks per level
    parent_map: dict[int, object] = {}

    for h in headings:
        level = h["level"]
        page_idx = max(0, h["page"] - 1)
        text = h["text"]

        # Find the appropriate parent
        parent = None
        for lvl in range(level - 1, 0, -1):
            if lvl in parent_map:
                parent = parent_map[lvl]
                break

        bm = writer.add_outline_item(
            title=text,
            page_number=page_idx,
            parent=parent,
        )
        parent_map[level] = bm
        # Clear all child levels when a higher-level heading appears
        for lvl in list(parent_map.keys()):
            if lvl > level:
                del parent_map[lvl]

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer.read()


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    # Save with unique name
    file_id = str(uuid.uuid4())[:8]
    filename = f"upload_{file_id}.pdf"
    save_path = UPLOAD_DIR / filename

    f.save(str(save_path))

    # Analyze
    result = analyze_pdf(str(save_path))
    result["file_id"] = file_id
    result["filename"] = f.filename
    result["saved_as"] = filename

    return jsonify(result)


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json()
    file_id = data.get("file_id")
    headings = data.get("headings", [])
    doc_title = data.get("doc_title", "Document")
    total_pages = data.get("total_pages", 0)
    generate_type = data.get("type", "both")  # "toc", "bookmarks", "both"

    # Find uploaded file
    matches = list(UPLOAD_DIR.glob(f"upload_{file_id}.pdf"))
    if not matches:
        return jsonify({"error": "Upload not found. Please re-upload."}), 404

    pdf_path = str(matches[0])
    outputs = {}

    out_id = str(uuid.uuid4())[:8]

    if generate_type in ("toc", "both"):
        toc_bytes = generate_toc_pdf(headings, doc_title, total_pages)
        toc_name = f"TOC_{out_id}.pdf"
        (OUTPUT_DIR / toc_name).write_bytes(toc_bytes)
        outputs["toc"] = toc_name

    if generate_type in ("bookmarks", "both"):
        bm_bytes = add_bookmarks_to_pdf(pdf_path, headings)
        bm_name = f"Bookmarked_{out_id}.pdf"
        (OUTPUT_DIR / bm_name).write_bytes(bm_bytes)
        outputs["bookmarked"] = bm_name

    return jsonify({"status": "ok", "outputs": outputs})


@app.route("/api/download/<filename>")
def download(filename):
    # Security: only allow files in output dir
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "", filename)
    path = OUTPUT_DIR / safe
    if not path.exists():
        return "File not found", 404
    return send_file(str(path), as_attachment=True, download_name=safe)


if __name__ == "__main__":
    print("=" * 55)
    print("  PDF Bookmark & Navigation Tool")
    print("  Open in browser: http://localhost:5050")
    print("  Share on network: http://<your-ip>:5050")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5050)
