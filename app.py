"""
PDF Bookmark & Navigation Tool
Engineering report processor — detects headings, generates bookmarks, creates TOC
Render.com compatible version — uses /tmp for file storage
"""

import os
import re
import uuid
import io
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

# ── App setup ─────────────────────────────────────────────────────────────────
# Use /tmp on Render (writable), fallback to local folders when running locally
if os.environ.get("RENDER"):
    BASE_TMP   = Path("/tmp/pdf_tool")
else:
    BASE_TMP   = Path(__file__).parent / "tmp"

UPLOAD_DIR = BASE_TMP / "uploads"
OUTPUT_DIR = BASE_TMP / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pdf-bookmark-tool-2024")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB


# ── Heading detection ─────────────────────────────────────────────────────────
HEADING_PATTERNS = [
    (r"^(\d+)\.\s+([A-Z][^\n]{2,80})$", 1),
    (r"^(\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 2),
    (r"^(\d+\.\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 3),
    (r"^(\d+\.\d+\.\d+\.\d+)\s+([A-Z][^\n]{2,80})$", 4),
    (r"^([A-Z][A-Z\s\-\/]{4,60})$", 1),
    (r"^(SECTION\s+\d+[\.\:]?\s*[-–—]?\s*.{2,60})$", 1),
    (r"^(CHAPTER\s+\d+[\.\:]?\s*[-–—]?\s*.{2,60})$", 1),
    (r"^(APPENDIX\s+[A-Z0-9][\.\:]?\s*.{0,60})$", 2),
]

FONT_SIZE_THRESHOLDS = {"h1": 16, "h2": 13, "h3": 11}


def detect_headings_by_text(text_lines):
    headings = []
    for line in text_lines:
        text = line["text"].strip()
        if not text or len(text) < 3:
            continue
        for pattern, level in HEADING_PATTERNS:
            if re.match(pattern, text):
                headings.append({
                    "text": text, "level": level,
                    "page": line["page"], "method": "pattern",
                })
                break
    return headings


def detect_headings_by_font(pdf_path):
    headings = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(extra_attrs=["size", "fontname"])
                lines = {}
                for w in words:
                    y = round(float(w.get("top", 0)), 1)
                    lines.setdefault(y, []).append(w)
                for y_pos in sorted(lines.keys()):
                    line_words = lines[y_pos]
                    text = " ".join(w["text"] for w in line_words).strip()
                    if not text or len(text) < 3:
                        continue
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
                            "text": text, "level": level,
                            "page": page_num, "method": "font",
                            "font_size": max_size,
                        })
    except Exception as e:
        print(f"Font detection error: {e}")
    return headings


def extract_text_lines(pdf_path):
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


def merge_and_deduplicate(font_headings, text_headings):
    seen = set()
    merged = []
    for h in font_headings + text_headings:
        key = (h["page"], h["text"][:40].lower())
        if key not in seen:
            seen.add(key)
            merged.append(h)
    merged.sort(key=lambda x: (x["page"], x.get("y_pos", 0)))
    return merged


def analyze_pdf(pdf_path):
    result = {
        "page_count": 0, "headings": [],
        "metadata": {}, "detection_method": "", "errors": [],
    }
    try:
        reader = PdfReader(pdf_path)
        result["page_count"] = len(reader.pages)
        meta = reader.metadata or {}
        result["metadata"] = {
            "title":   meta.get("/Title", ""),
            "author":  meta.get("/Author", ""),
            "subject": meta.get("/Subject", ""),
            "creator": meta.get("/Creator", ""),
        }
    except Exception as e:
        result["errors"].append(f"Metadata error: {e}")

    font_headings = detect_headings_by_font(pdf_path)
    text_lines    = extract_text_lines(pdf_path)
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
            "No headings detected. The PDF may be a scanned image or use unusual formatting."
        )
    result["heading_count"] = len(result["headings"])
    return result


# ── TOC PDF generation ────────────────────────────────────────────────────────
def generate_toc_pdf(headings, doc_title, total_pages):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2.5*cm, bottomMargin=2.5*cm,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TOCTitle", parent=styles["Title"],
        fontSize=22, textColor=colors.HexColor("#1a365d"),
        spaceAfter=6, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "TOCSubtitle", parent=styles["Normal"],
        fontSize=10, textColor=colors.HexColor("#4a5568"),
        spaceAfter=4, alignment=TA_CENTER,
    )
    level_styles = {
        1: ParagraphStyle("H1", parent=styles["Normal"], fontSize=11,
                          textColor=colors.HexColor("#1a365d"),
                          fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=2),
        2: ParagraphStyle("H2", parent=styles["Normal"], fontSize=10,
                          textColor=colors.HexColor("#2d3748"),
                          fontName="Helvetica", spaceBefore=3, spaceAfter=1, leftIndent=18),
        3: ParagraphStyle("H3", parent=styles["Normal"], fontSize=9,
                          textColor=colors.HexColor("#4a5568"),
                          fontName="Helvetica-Oblique", spaceBefore=2, leftIndent=36),
        4: ParagraphStyle("H4", parent=styles["Normal"], fontSize=9,
                          textColor=colors.HexColor("#718096"),
                          fontName="Helvetica-Oblique", spaceBefore=1, leftIndent=50),
    }
    bullets = {1: "■", 2: "▸", 3: "–", 4: "·"}

    story = []
    story.append(Paragraph(doc_title or "Document", title_style))
    story.append(Paragraph("Table of Contents", subtitle_style))
    story.append(Paragraph(f"Total Pages: {total_pages}", subtitle_style))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2b6cb0")))
    story.append(Spacer(1, 0.4*cm))

    if not headings:
        story.append(Paragraph("No headings were detected in this document.", styles["Normal"]))
    else:
        for h in headings:
            level = min(h["level"], 4)
            style = level_styles[level]
            bullet = bullets[level]
            text = h["text"][:87] + "..." if len(h["text"]) > 90 else h["text"]
            indent = (level - 1) * 18
            row_data = [[
                Paragraph(f"{bullet}  {text}", style),
                Paragraph(f"<b>{h['page']}</b>", ParagraphStyle(
                    "PageNum", parent=styles["Normal"],
                    fontSize=style.fontSize, alignment=TA_RIGHT,
                    textColor=style.textColor,
                )),
            ]]
            t = Table(row_data, colWidths=[14*cm, 2*cm])
            t.setStyle(TableStyle([
                ("LEFTPADDING",   (0, 0), (-1, -1), indent),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
                ("TOPPADDING",    (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(t)

    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generated by PDF Bookmark &amp; Navigation Tool  •  {len(headings)} headings detected",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8,
                       textColor=colors.HexColor("#a0aec0"), alignment=TA_CENTER)
    ))
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def add_bookmarks_to_pdf(pdf_path, headings):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if reader.metadata:
        writer.add_metadata(reader.metadata)

    parent_map = {}
    for h in headings:
        level    = h["level"]
        page_idx = max(0, h["page"] - 1)
        parent   = None
        for lvl in range(level - 1, 0, -1):
            if lvl in parent_map:
                parent = parent_map[lvl]
                break
        bm = writer.add_outline_item(
            title=h["text"], page_number=page_idx, parent=parent,
        )
        parent_map[level] = bm
        for lvl in list(parent_map.keys()):
            if lvl > level:
                del parent_map[lvl]

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer.read()


# ── Routes ────────────────────────────────────────────────────────────────────
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

    file_id  = str(uuid.uuid4())[:8]
    filename = f"upload_{file_id}.pdf"
    save_path = UPLOAD_DIR / filename
    f.save(str(save_path))

    result = analyze_pdf(str(save_path))
    result["file_id"]   = file_id
    result["filename"]  = f.filename
    result["saved_as"]  = filename
    return jsonify(result)


@app.route("/api/generate", methods=["POST"])
def generate():
    data         = request.get_json()
    file_id      = data.get("file_id")
    headings     = data.get("headings", [])
    doc_title    = data.get("doc_title", "Document")
    total_pages  = data.get("total_pages", 0)
    generate_type = data.get("type", "both")

    matches = list(UPLOAD_DIR.glob(f"upload_{file_id}.pdf"))
    if not matches:
        return jsonify({"error": "Upload not found. Please re-upload."}), 404

    pdf_path = str(matches[0])
    outputs  = {}
    out_id   = str(uuid.uuid4())[:8]

    if generate_type in ("toc", "both"):
        toc_bytes = generate_toc_pdf(headings, doc_title, total_pages)
        toc_name  = f"TOC_{out_id}.pdf"
        (OUTPUT_DIR / toc_name).write_bytes(toc_bytes)
        outputs["toc"] = toc_name

    if generate_type in ("bookmarks", "both"):
        bm_bytes = add_bookmarks_to_pdf(pdf_path, headings)
        bm_name  = f"Bookmarked_{out_id}.pdf"
        (OUTPUT_DIR / bm_name).write_bytes(bm_bytes)
        outputs["bookmarked"] = bm_name

    return jsonify({"status": "ok", "outputs": outputs})


@app.route("/api/download/<filename>")
def download(filename):
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "", filename)
    path = OUTPUT_DIR / safe
    if not path.exists():
        return "File not found — files are cleared on server restart. Please regenerate.", 404
    return send_file(str(path), as_attachment=True, download_name=safe)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("=" * 55)
    print("  PDF Bookmark & Navigation Tool")
    print("  Open: http://localhost:5050")
    print("=" * 55)
    app.run(debug=False, host="0.0.0.0", port=5050)
