import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

MODULE_RE = re.compile(r"<\s*MODULE\s*(\d+)\s*>", re.IGNORECASE)
HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4}|700\+\s*MOCK\s*TEST)",
    re.IGNORECASE,
)

X_MAX_RATIO = 0.35  # 번호는 페이지 왼쪽 35% 이내

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

def find_choice_D_bottom_y(page, y_from, y_to):
    # 구간 안에서 "D)"의 가장 아래 y를 찾아, 보기까지 포함되도록 끝점을 늘리는 용도
    # D) 텍스트가 여러 번 잡히면 가장 아래 것을 사용
    rects = page.search_for("D)")
    if not rects:
        return None
    bottoms = []
    for r in rects:
        if r.y1 < y_from or r.y0 > y_to:
            continue
        bottoms.append(r.y1)
    return max(bottoms) if bottoms else None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_x=14, pad_top=10, pad_bottom=12,
              tighten_blank=True, ensure_choices=True):
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

            # 1) 기본 끝: 다음 번호 직전
            if i + 1 < len(nums):
                y_end = nums[i + 1][1] - pad_bottom
            else:
                y_end = h - 8

            y_end = clamp(y_end, y_start + 120, h)

            # 2) 문제 사이에 머리말이 끼면 컷(최소 높이 보장)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 260:
                y_end = clamp(cut_y - 6, y_start + 120, y_end)

            # 3) (핵심) 객관식이면 D)까지 포함되도록 y_end를 늘림
            if ensure_choices:
                # D) 탐색 범위는 너무 위로 잡지 않게 (문제 시작 아래부터)
                d_bottom = find_choice_D_bottom_y(page, y_start, y_end + 900)
                if d_bottom is not None and d_bottom > y_start + 80:
                    y_end = clamp(max(y_end, d_bottom + 24), y_start + 120, h)

                    # D) 포함 후에도 머리말이 아래에 있으면 다시 컷(하지만 D) 위로 자르면 안 됨)
                    cut2 = find_header_footer_cut_y(page, y_start, y_end)
                    if cut2 is not None and cut2 > d_bottom + 30:
                        y_end = min(y_end, cut2 - 6)

            # 4) 공백 축소(단, D)까지 포함한 경우엔 D) 아래는 남겨둠)
            if tighten_blank:
                bottom = content_bottom_y(page, y_start, y_end)
                if bottom is not None:
                    # D) 포함 케이스면 bottom이 D)보다 위로 가면 안 됨
                    if ensure_choices:
                        d_bottom_now = find_choice_D_bottom_y(page, y_start, y_end)
                        if d_bottom_now is not None:
                            bottom = max(bottom, d_bottom_now + 10)
                    if bottom > y_start + 160:
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

st.title("SAT 수학 PDF → 문제별 PNG (보기 D)까지 포함)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 80, 14, 1)
pad_top = col3.slider("번호 포함 여백(위)", 0, 120, 10, 1)
pad_bottom = col4.slider("끝 여백(아래)", 0, 160, 12, 1)

tighten = st.checkbox("공백 자동 줄이기", value=True)
ensure_choices = st.checkbox("객관식 보기(D)까지 포함", value=True)

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
            ensure_choices=ensure_choices,
        )

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
