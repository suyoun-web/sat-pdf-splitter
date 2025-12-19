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

# 고정 허용비율(요청사항): 문제 번호는 페이지 왼쪽 35% 이내
X_MAX_RATIO = 0.35

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
    rects = sorted(rects, key=lambda r: (r.x0, r.y0))
    return rects[0] if rects else None

def find_num_rect(page, n):
    rects = page.search_for(f"{n}.")
    if not rects:
        return None
    w = page.rect.width
    rects = [r for r in rects if r.x0 <= w * X_MAX_RATIO]
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
    # 공백 제거용: 머리말 블록 제외하고 실제 텍스트가 있는 가장 아래 y
    blocks = page.get_text("blocks")
    bottoms = []
    for b in blocks:
        if len(b) < 5:
            continue
        y0, y1 = b[1], b[3]
        text = b[4]
        if y1 < y_from or y0 > y_to:
            continue
        if text and HEADER_FOOTER_HINT_RE.search(str(text)):
            continue
        if text and str(text).strip():
            bottoms.append(y1)
    return max(bottoms) if bottoms else None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_x=14, pad_top=10, pad_bottom=12,
              tighten_blank=True):
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

        nums = []
        for n in range(1, 23):
            r = find_num_rect(page, n)
            if r is not None:
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

            y_end = clamp(y_end, y_start + 80, h)

            # 문제 사이에 머리말이 끼는 케이스 컷 (page 8/13/14 참고)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 220:
                y_end = clamp(cut_y - 6, y_start + 80, y_end)

            # 다음 번호를 못 잡아 길어지는 경우 공백 축소
            if tighten_blank:
                bottom = content_bottom_y(page, y_start, y_end)
                if bottom is not None and bottom > y_start + 140:
                    y_end = min(y_end, bottom + 16)

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

st.title("SAT 수학 PDF → 문제별 PNG (M1/M2, 1.png..22.png)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 80, 14, 1)
pad_top = col3.slider("번호 포함 여백(위)", 0, 120, 10, 1)
pad_bottom = col4.slider("끝 여백(아래)", 0, 160, 12, 1)

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
            tighten_blank=tighten,
        )

    c1 = len(module_map.get(1, {}))
    c2 = len(module_map.get(2, {}))
    st.success(f"완료: M1 {c1}개, M2 {c2}개 (목표: 각 22개)")

    miss1 = [n for n in range(1, 23) if n not in module_map.get(1, {})]
    miss2 = [n for n in range(1, 23) if n not in module_map.get(2, {})]
    with st.expander("누락 번호(있으면 알려주세요)"):
        st.write("M1 누락:", miss1)
        st.write("M2 누락:", miss2)

    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
