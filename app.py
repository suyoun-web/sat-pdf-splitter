import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 모듈별 문제 이미지", layout="wide")

QNUM_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")
MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)

FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4})",
    re.IGNORECASE
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def extract_module_id(page):
    blocks = page.get_text("blocks")
    for *_xy, text, *_ in blocks:
        if not text:
            continue
        for line in text.splitlines():
            m = MODULE_RE.match(line.strip())
            if m:
                return int(m.group(1))
    return None

def find_footer_cut_y(page, y_from, y_to):
    blocks = page.get_text("blocks")
    ys = []
    for x0, y0, x1, y1, text, *_ in blocks:
        if y0 < y_from or y0 > y_to:
            continue
        if text and FOOTER_HINT_RE.search(text):
            ys.append(y0)
    return min(ys) if ys else None

def split_pdf(pdf_bytes, zoom=2.8, pad_x=14, pad_top=6, pad_bottom=10, top_exclude_ratio=0.12):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    current_module = None
    out = {1: {}, 2: {}}

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height

        mid = extract_module_id(page)
        if mid in (1, 2):
            current_module = mid

        words = page.get_text("words")
        words.sort(key=lambda t: (t[1], t[0]))  # y, x

        starts = []
        for (x0, y0, x1, y1, txt, *_rest) in words:
            if y0 < top_exclude_ratio * h:
                continue
            if QNUM_RE.match(txt):
                starts.append((y0, txt))

        if not starts:
            continue

        starts.sort(key=lambda t: t[0])

        for i, (y0, txt) in enumerate(starts):
            if current_module not in (1, 2):
                continue

            qnum = int(re.sub(r"\D", "", txt))
            if not (1 <= qnum <= 22):
                continue

            y_start = clamp(y0 - pad_top, 0, h)
            y_end_default = (starts[i + 1][0] - pad_bottom) if i + 1 < len(starts) else (h - 10)
            y_end_default = clamp(y_end_default, y_start + 5, h)

            footer_cut_y = find_footer_cut_y(page, y_start, y_end_default)
            y_end = y_end_default
            if footer_cut_y is not None and footer_cut_y > y_start + 20:
                y_end = clamp(footer_cut_y - 6, y_start + 5, y_end_default)

            clip = fitz.Rect(0 + pad_x, y_start, w - pad_x, y_end)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            png_bytes = pix.tobytes("png")

            out[current_module].setdefault(qnum, png_bytes)

    return out

def build_zip(module_map, zip_base_name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for mod in (1, 2):
            for qnum in range(1, 23):
                data = module_map.get(mod, {}).get(qnum)
                if data is None:
                    continue
                z.writestr(f"M{mod}/{qnum}.png", data)
    buf.seek(0)
    return buf, f"{zip_base_name}.zip"

st.title("SAT 수학 PDF → 모듈별(M1/M2) 문제 이미지 (파일명: 1.png, 2.png, …)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 2.8, 0.1)
pad_x = col2.slider("좌우 여백", 0, 40, 14, 1)
pad_top = col3.slider("위 여백", 0, 30, 6, 1)
pad_bottom = col4.slider("아래 여백", 0, 30, 10, 1)

if pdf is None:
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드 준비"):
    with st.spinner("문제별로 자르는 중..."):
        module_map = split_pdf(
            pdf.read(),
            zoom=zoom,
            pad_x=pad_x,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
        )

    c1 = len(module_map.get(1, {}))
    c2 = len(module_map.get(2, {}))
    st.success(f"완료: M1 {c1}개, M2 {c2}개 (최대 각 22개)")

    zbuf, zip_filename = build_zip(module_map, zip_base)

    st.download_button(
        label=f"다운로드: {zip_filename}",
        data=zbuf,
        file_name=zip_filename,
        mime="application/zip",
    )
