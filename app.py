import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)
QNUM_LINE_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")

# 보기/본문 검증용
CHOICE_RE = re.compile(r"\b(A$$|B$$|C$$|D$$)")
TEXT_HINT_RE = re.compile(r"\b(The|What|Which|In the|If|A circle|Line|The graph)\b")

# 문서에 실제로 들어있는 머리말/연락처(문제 중간에도 삽입됨)
HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4}|700\+\s*MOCK\s*TEST)",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def get_blocks(page):
    return page.get_text("blocks")

def find_module_on_page(page):
    for b in get_blocks(page):
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
    lines_out = []  # (y0, y1, text)
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

def text_in_band(page, y_from, y_to):
    parts = []
    for b in get_blocks(page):
        if len(b) < 5:
            continue
        y0, y1 = b[1], b[3]
        if y1 < y_from or y0 > y_to:
            continue
        text = b[4]
        if text:
            parts.append(str(text))
    s = "\n".join(parts)
    return re.sub(r"\s+", " ", s).strip()

def is_real_anchor(page, y_numline, lookahead_height=420, min_chars=40):
    # 번호 아래에 본문/보기 존재하면 진짜 문제 번호로 인정
    h = page.rect.height
    y_from = clamp(y_numline, 0, h)
    y_to = clamp(y_numline + lookahead_height, y_from + 5, h)
    s = text_in_band(page, y_from, y_to)

    if len(s) < min_chars:
        return False
    if CHOICE_RE.search(s):
        return True
    if TEXT_HINT_RE.search(s):
        return True
    # 텍스트가 충분히 길면(그림 문제/표 문제 대비) 통과
    return len(s) >= (min_chars * 3)

def find_header_footer_cut_y(page, y_from, y_to):
    ys = []
    for b in get_blocks(page):
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

def split_pdf(pdf_bytes, zoom=3.0, pad_x=12, pad_top=6, pad_bottom=8,
              top_exclude_ratio=0.10, lookahead_height=420):
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

        lines = iter_lines(page)

        # 번호 라인 수집
        q_lines = []  # (qnum, y0)
        for (y0, y1, text) in lines:
            if y0 < top_exclude_ratio * h:
                continue
            m = QNUM_LINE_RE.match(text)
            if m:
                qnum = int(m.group(1))
                if 1 <= qnum <= 22:
                    q_lines.append((qnum, y0))

        if not q_lines:
            continue

        q_lines.sort(key=lambda t: t[1])

        # 진짜 앵커만 남김(번호만 떠있는 오탐 제거)
        starts = []
        for (qnum, y0) in q_lines:
            if is_real_anchor(page, y0, lookahead_height=lookahead_height):
                starts.append((qnum, y0))

        if not starts:
            continue

        starts.sort(key=lambda t: t[1])

        # (n.) 포함해서 (n+1.) 전까지 자르기
        for i, (qnum, y0) in enumerate(starts):
            if qnum in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)
            if i + 1 < len(starts):
                y_end = starts[i + 1][1] - pad_bottom
            else:
                y_end = h - 8

            y_end = clamp(y_end, y_start + 40, h)

            # 문제 사이에 머리말이 끼는 케이스 컷 (page 6, 13 등)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            # 너무 위에서 잘라 문제를 날리는 것 방지: 최소 160pt는 보장
            if cut_y is not None and cut_y > y_start + 160:
                y_end = clamp(cut_y - 6, y_start + 40, y_end)

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

st.title("SAT 수학 PDF → 문제별 PNG (번호 포함, 오탐 필터)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 60, 12, 1)
pad_top = col3.slider("번호 포함 여백(위)", 0, 60, 6, 1)
pad_bottom = col4.slider("끝 여백(아래)", 0, 80, 8, 1)

lookahead = st.slider("오탐 필터(번호 아래 검사 높이)", 200, 900, 420, 20)

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
            lookahead_height=lookahead,
        )

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
