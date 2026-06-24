# 📑 PDF Bookmark & Navigation Tool

Auto-detect headings in engineering reports, generate clickable PDF bookmarks, and produce a Table of Contents — all from a browser interface.

---

## ✅ Features

- **Upload any PDF** — up to 200 MB, 1000+ pages
- **Auto-detect headings** using font-size analysis + pattern matching
- **Edit headings** — rename, change level (H1–H4), change page, add manually, delete
- **Generate bookmarked PDF** — original PDF with a full clickable bookmark outline panel
- **Generate Table of Contents PDF** — clean standalone TOC with page numbers
- **Team-shareable** — runs as a web server, accessible from any browser on your network

---

## 🚀 Quick Start

### 1. Install Python (if not already installed)
Download from https://www.python.org/downloads/ (Python 3.10+)

### 2. Install dependencies
```bash
pip install flask pypdf pdfplumber reportlab
```

### 3. Run the tool
```bash
python app.py
```

### 4. Open in browser
```
http://localhost:5050
```

### 5. Share with your team (same network)
Find your IP address:
- Windows: run `ipconfig` → look for IPv4 Address
- Mac/Linux: run `ifconfig` or `ip addr`

Then share: `http://YOUR-IP:5050`

---

## 📁 Project Structure

```
pdf_bookmark_tool/
├── app.py              ← Main application (Flask)
├── requirements.txt    ← Python dependencies
├── templates/
│   └── index.html      ← Web interface
├── uploads/            ← Temporary uploaded PDFs (auto-cleaned)
└── outputs/            ← Generated output files
```

---

## 🛠 How It Works

1. **Upload** your engineering PDF
2. Tool analyses the PDF using two detection methods:
   - **Font-size analysis** — identifies text that is larger/bolder than body text
   - **Pattern matching** — detects numbered headings (`1.`, `1.1`, `1.1.1`), ALL CAPS sections, CHAPTER/SECTION/APPENDIX labels
3. **Review & edit** the detected headings in the table — rename, reorder, add missing ones
4. Click **Generate Files** to produce:
   - A **bookmarked PDF** — open in Acrobat/Chrome and see the sidebar navigation
   - A **Table of Contents PDF** — print or attach to the front of your report

---

## 💡 Tips for Best Results

- PDFs with **selectable text** (not scanned images) work best
- If headings are missed, use the **Add Heading** row to add them manually
- Use **Sort by Page** after adding manual headings
- Adjust heading levels (H1/H2/H3) to match the document hierarchy
- The bookmarked PDF opens in Adobe Acrobat, Chrome, Edge, Firefox, Foxit, etc.

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| No headings detected | PDF may be a scanned image — try OCR first (Adobe Acrobat, Google Drive) |
| Wrong heading levels | Edit them in the table before generating |
| Port 5050 in use | Edit `app.py` last line, change `port=5050` to another number |
| Large file slow | Normal — analysis takes ~1–3 sec per 100 pages |

---

## 📋 Requirements

- Python 3.10+
- Flask, pypdf, pdfplumber, reportlab
- Any modern browser (Chrome, Edge, Firefox, Safari)

---

*Built for engineering teams — PDF Bookmark & Navigation Tool*
