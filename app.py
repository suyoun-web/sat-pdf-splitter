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

X_MAX_RATIO = 0.35  # 번호는 왼쪽에 있다고 가정

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

def find_num_y(page, n):
    rects = page.search_for(f"{n}.")
    if not rects:
        return None
    w = page.rect.width
    rects = [r for r in rects if r.x0 <= w * X_MAX_RATIO]
    if not rects:
        return None
    return pick_leftmost_rect(rects).y0

def find_header_footer_cut_y(page, y_from, y_to):
    ys = []
    for b in page.get_text("blocks"):
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
    bottoms = []
    for b in page.get_text("blocks"):
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

def x_bounds_in_band(page, y_from, y_to):
    xs0, xs1 = [], []
    for b in page.get_text("blocks"):
        if len(b) < 5:
            continue
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        text = b[4]
        if y1 < y_from or y0 > y_to:
            continue
        if text and HEADER_FOOTER_HINT_RE.search(str(text)):
            continue
        if text and str(text).strip():
            xs0.append(x0)
            xs1.append(x1)
    if not xs0:
        return None
    return min(xs0), max(xs1)

def find_choice_D_bottom_y_in_band(page, y_from, y_to):
    # 중요: 현재 문제 y구간 안에서만 D) 탐색
    rects = page.search_for("D)")
    if not rects:
        return None
    bottoms = [r.y1 for r in rects if (r.y1 >= y_from and r.y0 <= y_to)]
    return max(bottoms) if bottoms else None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_top=10, pad_bottom=12, side_pad_px=5,
              ensure_choices=True, tighten_blank=True):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {1: {}, 2: {}}
    current_module = None

    side_pad_pt = side_pad_px / zoom

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height

        mid = find_module_on_page(page)
        if mid is not None:
            current_module = mid
        if current_module not in (1, 2):
            continue

        found = []
        for n in range(1, 23):
            y = find_num_y(page, n)
            if y is not None:
                found.append((n, y))
        if not found:
            continue

        found.sort(key=lambda t: t[1])

        for i, (n, y0) in enumerate(found):
            if n in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            # 기본 끝(=다음 문제 시작 전)
            if i + 1 < len(found):
                next_y = found[i + 1][1]
                y_end = next_y - pad_bottom
                y_cap = next_y - 1  # 절대 이걸 넘기면 안 됨
            else:
                y_end = h - 8
                y_cap = h

            y_end = clamp(y_end, y_start + 80, y_cap)

            # 머리말 끼면 컷(다음 문제 전까지만)
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 220:
                y_end = clamp(cut_y - 6, y_start + 80, y_end)

            # 보기 D)까지 포함하되, 절대 다음 문제로 넘어가지 않게 cap 적용
            if ensure_choices and i + 1 < len(found):
                d_bottom = find_choice_D_bottom_y_in_band(page, y_start, y_cap)
                if d_bottom is not None and d_bottom > y_start + 60:
                    y_end = clamp(max(y_end, d_bottom + 18), y_start + 80, y_cap)

            # 공백 축소(역시 cap 안에서만)
            if tighten_blank:
                bottom = content_bottom_y(page, y_start, y_end)
                if bottom is not None and bottom > y_start + 140:
                    y_end = min(y_end, bottom + 14)

            # 좌우 타이트 크롭
            xb = x_bounds_in_band(page, y_start, y_end)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 50, w)

            clip = fitz.Rect(x0, y_start, x1, y_end)
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

st.title("SAT 수학 PDF → 문제별 PNG (타이트 크롭 + 보기 D) 보정)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])
col1, col2, col3 = st.columns(3)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_top = col2.slider("위 여백(번호 포함)", 0, 120, 10, 1)
pad_bottom = col3.slider("아래 여백", 0, 160, 12, 1)

ensure_choices = st.checkbox("객관식 보기(D)까지 포함(다음 문제로는 절대 안 넘어감)", value=True)
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
            pad_top=pad_top,
            pad_bottom=pad_bottom,
            side_pad_px=5,
            ensure_choices=ensure_choices,
            tighten_blank=tighten,
        )

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
