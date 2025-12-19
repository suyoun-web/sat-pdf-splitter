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

# 본문 힌트(번호 오른쪽에 따라오는지 검증)
BODY_HINT_RE = re.compile(r"\b(The|In the|If|Which|What|A circle|The graph)\b", re.IGNORECASE)

X_MAX_RATIO = 0.35
SIDE_PAD_PX = 10

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def find_module_on_page(page):
    txt = page.get_text("text") or ""
    m = MODULE_RE.search(txt)
    if not m:
        return None
    mid = int(m.group(1))
    return mid if mid in (1, 2) else None

def band_text(page, clip):
    # clip 영역 텍스트만 가져오기
    return (page.get_text("text", clip=clip) or "").strip()

def pick_best_num_rect(page, n):
    rects = page.search_for(f"{n}.")
    if not rects:
        return None

    w = page.rect.width
    # 1) 왼쪽 영역 필터
    rects = [r for r in rects if r.x0 <= w * X_MAX_RATIO]
    if not rects:
        return None

    # 2) “진짜 번호” 검증: 번호 근처에 본문/보기 존재?
    scored = []
    for r in rects:
        y0 = r.y0
        # 번호 오른쪽/아래 작은 영역을 검사
        probe = fitz.Rect(r.x0, y0, w, min(page.rect.height, y0 + 110))
        t = band_text(page, probe)
        # 보기 A) 또는 본문 힌트가 있으면 강하게 점수
        score = 0
        if "A)" in t:
            score += 3
        if BODY_HINT_RE.search(t):
            score += 2
        # 너무 짧으면 감점(문제 속 4. 같은 것)
        if len(re.sub(r"\s+", " ", t)) < 20:
            score -= 2
        scored.append((score, r.x0, r.y0, r))

    scored.sort(key=lambda x: (-x[0], x[1], x[2]))  # score desc, x asc, y asc
    best = scored[0]
    # 점수가 너무 낮으면(오탐 가능) 아예 None 처리
    if best[0] < 1:
        return None
    return best[3]

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

def text_x_bounds_in_band(page, y_from, y_to, min_len=2):
    xs0, xs1 = [], []
    for b in page.get_text("blocks"):
        if len(b) < 5:
            continue
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        if y1 < y_from or y0 > y_to:
            continue
        if not text:
            continue
        t = str(text).strip()
        if len(t) < min_len:
            continue
        if HEADER_FOOTER_HINT_RE.search(t):
            continue
        xs0.append(x0)
        xs1.append(x1)
    if not xs0:
        return None
    return min(xs0), max(xs1)

def last_choice_bottom_y(page, y_from, y_to):
    # y구간 안에서만 마지막 보기(A/B/C/D) 위치 찾기
    # A)가 없으면 MCQ로 보지 않음
    clip = fitz.Rect(0, y_from, page.rect.width, y_to)
    t = band_text(page, clip)
    if "A)" not in t:
        return None

    for label in ["D)", "C)", "B)", "A)"]:
        rects = page.search_for(label)
        bottoms = [r.y1 for r in rects if (r.y1 >= y_from and r.y0 <= y_to)]
        if bottoms:
            return max(bottoms)
    return None

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_top=10, pad_bottom=12):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {1: {}, 2: {}}
    current_module = None
    side_pad_pt = SIDE_PAD_PX / zoom

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
            r = pick_best_num_rect(page, n)
            if r is not None:
                found.append((n, r.y0))
        if not found:
            continue

        found.sort(key=lambda t: t[1])

        for i, (n, y0) in enumerate(found):
            if n in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            if i + 1 < len(found):
                next_y = found[i + 1][1]
                y_cap = clamp(next_y - 1, 0, h)   # 절대 다음 문제로는 안 감
                y_end = clamp(next_y - pad_bottom, y_start + 80, y_cap)
            else:
                y_cap = h
                y_end = clamp(h - 8, y_start + 80, h)

            # MCQ면 보기 마지막 위치를 먼저 확보 (D) 잘림 방지)
            mcq_last = last_choice_bottom_y(page, y_start, y_cap)
            if mcq_last is not None:
                y_end = clamp(max(y_end, mcq_last + 18), y_start + 80, y_cap)

            # 머리말 컷: 단, MCQ 보기가 있으면 보기 위로는 절대 자르지 않음
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 220:
                if mcq_last is None or cut_y > mcq_last + 30:
                    y_end = clamp(cut_y - 6, y_start + 80, y_end)

            # 공백 축소(보기는 보호)
            bottom = content_bottom_y(page, y_start, y_end)
            if bottom is not None and bottom > y_start + 140:
                if mcq_last is not None:
                    bottom = max(bottom, mcq_last + 10)
                y_end = min(y_end, bottom + 14)

            # 좌우(텍스트 기준) + 10px
            xb = text_x_bounds_in_band(page, y_start, y_end, min_len=2)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 80, w)

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

st.title("SAT 수학 PDF → 문제별 PNG (번호 오탐 방지 + MCQ 보기 보호 + 좌우 10px)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])
col1, col2, col3 = st.columns(3)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_top = col2.slider("위 여백(번호 포함)", 0, 120, 10, 1)
pad_bottom = col3.slider("아래 여백", 0, 160, 12, 1)

if pdf is None:
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드"):
    with st.spinner("자르는 중..."):
        module_map = split_pdf(pdf.read(), zoom=zoom, pad_top=pad_top, pad_bottom=pad_bottom)

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
