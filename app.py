import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)
QNUM_LINE_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")

HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk\s*:|Instagram\s*:|010-\d{3,4}-\d{4}|700\+\s*MOCK\s*TEST)",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def find_module_on_page(page):
    blocks = page.get_text("blocks")
    for b in blocks:
        if len(b) < 5:
            continue
        text = b[4]
        if not text:
            continue
        for line in str(text).splitlines():
            m = MODULE_RE.match(line.strip())
            if m:
                mid = int(m.group(1))
                if mid in (1, 2):
                    return mid
    return None

def iter_lines(page):
    d = page.get_text("dict")
    lines_out = []
    # 각 line: (y0, y1, text)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox")
            if not bbox:
                continue
            y0, y1 = bbox[1], bbox[3]
            parts = []
            for span in line.get("spans", []):
                t = span.get("text", "")
                if t:
                    parts.append(t)
            text = "".join(parts).strip()
            if text:
                lines_out.append((y0, y1, text))
    lines_out.sort(key=lambda t: t[0])
    return lines_out

def find_question_ranges_by_next_line(page, top_exclude_ratio=0.10):
    h = page.rect.height
    lines = iter_lines(page)

    # 번호 라인 위치 찾기
    q_lines = []  # (qnum, idx, y0_line)
    for idx, (y0, y1, text) in enumerate(lines):
        if y0 < top_exclude_ratio * h:
            continue
        m = QNUM_LINE_RE.match(text)
        if m:
            qnum = int(m.group(1))
            if 1 <= qnum <= 22:
                q_lines.append((qnum, idx, y0))

    if not q_lines:
        return []

    q_lines.sort(key=lambda t: t[2])  # y로 정렬

    ranges = []  # (qnum, y_start, y_end)
    for i, (qnum, idx, y0_numline) in enumerate(q_lines):
        # 시작점: "번호 다음 줄"의 y0 (없으면 번호줄 y0)
        if idx + 1 < len(lines):
            y_start = lines[idx + 1][0]
        else:
            y_start = y0_numline

        # 끝점: 다음 번호줄 y0
        if i + 1 < len(q_lines):
            y_end = q_lines[i + 1][2]
        else:
            y_end = h - 8

        # sanity: 너무 얇으면 번호줄부터 시작
        if y_end - y_start < 25:
            y_start = y0_numline

        ranges.append((qnum, y_start, y_end))

    return ranges

def find_header_footer_cut_y(page, y_from, y_to):
    blocks = page.get_text("blocks")
    ys = []
    for b in blocks:
        if len(b) < 5:
            continue
        y0 = b[1]
        text = b[4]
        if y0 < y_from or y0 > y_to:
            continue
        if text and HEADER_FOOTER_HINT_RE.search(str(text)):
            ys.append(y0)
    return min(ys) if ys else None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_x=12, pad_top=8, pad_bottom=8):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {1: {}, 2: {}}
    current_module = None

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height

        mid = find_module_on_page(page)
        if mid is not None:
            current_module = mid

        if current_module not in (1, 2):
            continue

        ranges = find_question_ranges_by_next_line(page)
        if not ranges:
            continue

        for (qnum, y_start0, y_end0) in ranges:
            if qnum in out[current_module]:
                continue

            y_start = clamp(y_start0 - pad_top, 0, h)
            y_end = clamp(y_end0 - pad_bottom, y_start + 30, h)

            # 머리말/연락처가 중간에 끼면 그 위에서 컷 (page 13 같은 케이스)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            # 단, 너무 위에서 컷(=본문까지 잘라먹는) 방지: 시작 후 최소 120pt는 보장
            if cut_y is not None and cut_y > y_start + 120:
                y_end = clamp(cut_y - 6, y_start + 30, y_end)

            clip = fitz.Rect(0 + pad_x, y_start, w - pad_x, y_end)
            out[current_module][qnum] = render_png(page, clip, zoom)

    return out

def make_zip(module_map, zip_base_name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for mod in (1, 2):
            for qnum in range(1, 23):
                data = module_map.get(mod, {}).get(qnum)
                if data is None:
                    continue
                z.writestr(f"M{mod}/{qnum}.png", data)
    buf.seek(0)
    return buf, zip_base_name + ".zip"

st.title("SAT 수학 PDF → 문제별 PNG (번호 다음 줄부터 캡처)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 60, 12, 1)
pad_top = col3.slider("위 여백", 0, 60, 8, 1)
pad_bottom = col4.slider("아래 여백", 0, 80, 8, 1)

if pdf is None:
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드"):
    with st.spinner("자르는 중..."):
        module_map = split_pdf(
            pdf.read(),
            zoom=zoom,
            pad_x=pad_x,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
        )

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
