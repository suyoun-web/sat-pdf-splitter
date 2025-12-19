import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

MODULE_RE = re.compile(r"<\s*MODULE\s*(\d+)\s*>", re.IGNORECASE)

HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4}|700\+\s*MOCK\s*TEST)",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def find_module_on_page(page):
    txt = page.get_text("text") or ""
    m = MODULE_RE.search(txt)
    if not m:
        return None
    mid = int(m.group(1))
    return mid if mid in (1, 2) else None

def pick_leftmost_rect(rects):
    # 문제 번호는 왼쪽에 있으니 x0가 가장 작은 것 우선
    rects = sorted(rects, key=lambda r: (r.x0, r.y0))
    return rects[0] if rects else None

def find_num_rect(page, n, x_max_ratio=0.28):
    # "n."의 위치를 좌표로 탐색하고, 왼쪽 여백에 있는 것만 인정
    rects = page.search_for(f"{n}.")
    if not rects:
        return None
    w = page.rect.width
    # 왼쪽 영역 필터
    rects = [r for r in rects if r.x0 <= w * x_max_ratio]
    if not rects:
        return None
    return pick_leftmost_rect(rects)

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

def content_bottom_y(page, y_from, y_to):
    # y_from~y_to 구간에서 "실제 내용"이 존재하는 가장 아래 y를 찾음(공백 제거용)
    blocks = page.get_text("blocks")
    bottoms = []
    for b in blocks:
        if len(b) < 5:
            continue
        y0, y1 = b[1], b[3]
        text = b[4]
        if y1 < y_from or y0 > y_to:
            continue
        # 머리말/연락처 블록은 내용 바닥 계산에서 제외
        if text and HEADER_FOOTER_HINT_RE.search(str(text)):
            continue
        # 텍스트가 조금이라도 있으면 바닥 후보
        if text and str(text).strip():
            bottoms.append(y1)
    return max(bottoms) if bottoms else None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_x=14, pad_top=10, pad_bottom=12,
              x_max_ratio=0.28, tighten_blank=True):
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

        # 이 페이지에서 검출되는 번호들(y0 기준으로 정렬)
        nums = []
        rect_by_n = {}
        for n in range(1, 23):
            r = find_num_rect(page, n, x_max_ratio=x_max_ratio)
            if r is not None:
                rect_by_n[n] = r
                nums.append((n, r.y0))

        if not nums:
            continue

        nums.sort(key=lambda t: t[1])

        for i, (n, y0) in enumerate(nums):
            if n in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            if i + 1 < len(nums):
                y_end = nums[i + 1][1] - pad_bottom
            else:
                y_end = h - 8

            y_end = clamp(y_end, y_start + 60, h)

            # 문제 사이에 머리말이 끼면 컷(단, 너무 일찍 컷하지 않게 최소 높이 보장)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 200:
                y_end = clamp(cut_y - 6, y_start + 60, y_end)

            # 공백 제거: 다음 번호를 못 잡아서 y_end가 너무 아래면, 실제 내용 바닥까지만
            if tighten_blank:
                bottom = content_bottom_y(page, y_start, y_end)
                if bottom is not None and bottom > y_start + 120:
                    y_end = min(y_end, bottom + 12)

            clip = fitz.Rect(0 + pad_x, y_start, w - pad_x, y_end)
            out[current_module][n] = render_png(page, clip, zoom)

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

st.title("SAT 수학 PDF → 문제별 PNG (번호 포함, 공백 자동 축소)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 80, 14, 1)
pad_top = col3.slider("번호 포함 여백(위)", 0, 80, 10, 1)
pad_bottom = col4.slider("끝 여백(아래)", 0, 120, 12, 1)

x_ratio = st.slider("번호 위치(왼쪽 영역) 허용 비율", 0.10, 0.60, 0.28, 0.01)
tighten = st.checkbox("공백 자동 줄이기", value=True)

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
            x_max_ratio=x_ratio,
            tighten_blank=tighten,
        )

    c1 = len(module_map.get(1, {}))
    c2 = len(module_map.get(2, {}))
    st.success(f"완료: M1 {c1}개, M2 {c2}개 (목표: 각 22개)")

    miss1 = [n for n in range(1, 23) if n not in module_map.get(1, {})]
    miss2 = [n for n in range(1, 23) if n not in module_map.get(2, {})]
    with st.expander("누락 번호"):
        st.write("M1 누락:", miss1)
        st.write("M2 누락:", miss2)

    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
``*
