import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

MODULE_RE = re.compile(r"<\s*MODULE\s*(\d+)\s*>", re.IGNORECASE)

# 꼬리말/머리말 힌트(문서에 반복 등장)
HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|700\+\s*MOCK\s*TEST|Kakaotalk|Instagram|010-\d{3,4}-\d{4})",
    re.IGNORECASE,
)

NUMDOT_RE = re.compile(r"^(\d{1,2})\.$$")
NUM_RE = re.compile(r"^\d{1,2}$$")

CHOICE_LABELS = ["D)", "C)", "B)", "A)"]

SIDE_PAD_PX = 10
INK_PAD_PX = 10
SCAN_ZOOM = 0.6
WHITE_THRESH = 250

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def find_module_on_page(page):
    txt = page.get_text("text") or ""
    m = MODULE_RE.search(txt)
    if not m:
        return None
    mid = int(m.group(1))
    return mid if mid in (1, 2) else None

def group_words_into_lines(words):
    lines = {}
    for w in words:
        x0,y0,x1,y1,txt,block_no,line_no,word_no = w
        key = (block_no, line_no)
        lines.setdefault(key, []).append((x0,y0,x1,y1,txt))
    for k in lines:
        lines[k].sort(key=lambda t: t[0])
    return list(lines.values())

def detect_question_anchors(page, left_ratio=0.25, max_line_chars=4):
    w_page = page.rect.width
    words = page.get_text("words")
    if not words:
        return []

    lines = group_words_into_lines(words)
    anchors = []

    for tokens in lines:
        line_text = " ".join(t[4] for t in tokens).strip()
        compact = re.sub(r"\s+", "", line_text)

        # 머리말/꼬리말 줄 제외
        if HEADER_FOOTER_HINT_RE.search(line_text):
            continue
        if len(compact) > max_line_chars:
            continue

        x_left = min(t[0] for t in tokens)
        if x_left > w_page * left_ratio:
            continue

        qnum = None
        y_top = None

        for (x0,y0,x1,y1,txt) in tokens:
            m = NUMDOT_RE.match(txt)
            if m:
                qnum = int(m.group(1))
                y_top = y0
                break

        if qnum is None:
            for i in range(len(tokens)-1):
                t1 = tokens[i][4]
                t2 = tokens[i+1][4]
                if NUM_RE.match(t1) and t2 == ".":
                    qnum = int(t1)
                    y_top = tokens[i][1]
                    break

        if qnum is None:
            continue
        if not (1 <= qnum <= 22):
            continue

        anchors.append((qnum, y_top))

    anchors.sort(key=lambda t: t[1])
    return anchors

def band_text(page, clip):
    return (page.get_text("text", clip=clip) or "")

def last_choice_bottom_y_in_band(page, y_from, y_to):
    clip = fitz.Rect(0, y_from, page.rect.width, y_to)
    t = band_text(page, clip)
    if "A)" not in t:
        return None
    for lab in CHOICE_LABELS:
        rects = page.search_for(lab)
        bottoms = [r.y1 for r in rects if (r.y1 >= y_from and r.y0 <= y_to)]
        if bottoms:
            return max(bottoms)
    return None

def find_footer_start_y(page, y_from, y_to):
    """
    현재 문제 구간(y_from~y_to) 안에서
    꼬리말 힌트가 있는 텍스트 블록의 '시작 y'를 반환.
    (있으면 그 위에서 잘라내기)
    """
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
        y0,y1,text = b[1], b[3], b[4]
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
        x0,y0,x1,y1,text = b[0],b[1],b[2],b[3],b[4]
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

def ink_bbox_by_raster(page, clip, scan_zoom=SCAN_ZOOM, white_thresh=WHITE_THRESH):
    mat = fitz.Matrix(scan_zoom, scan_zoom)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    w, h = img.size
    px = img.load()

    minx, miny = w, h
    maxx, maxy = -1, -1

    step = 2
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if r < white_thresh or g < white_thresh or b < white_thresh:
                if x < minx: minx = x
                if y < miny: miny = y
                if x > maxx: maxx = x
                if y > maxy: maxy = y

    if maxx < 0:
        return None
    return (minx, miny, maxx, maxy, w, h)

def px_bbox_to_page_rect(clip, px_bbox, pad_px=INK_PAD_PX):
    minx, miny, maxx, maxy, w, h = px_bbox

    minx = max(0, minx - pad_px)
    miny = max(0, miny - pad_px)
    maxx = min(w - 1, maxx + pad_px)
    maxy = min(h - 1, maxy + pad_px)

    x0 = clip.x0 + (minx / (w - 1)) * (clip.x1 - clip.x0)
    x1 = clip.x0 + (maxx / (w - 1)) * (clip.x1 - clip.x0)
    y0 = clip.y0 + (miny / (h - 1)) * (clip.y1 - clip.y0)
    y1 = clip.y0 + (maxy / (h - 1)) * (clip.y1 - clip.y0)
    return fitz.Rect(x0, y0, x1, y1)

def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf(pdf_bytes, zoom=3.0, pad_top=10, pad_bottom=12, frq_extra_space_px=250):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = {1: {}, 2: {}}
    current_module = None
    side_pad_pt = SIDE_PAD_PX / zoom
    frq_extra_pt = frq_extra_space_px / zoom

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height

        mid = find_module_on_page(page)
        if mid is not None:
            current_module = mid
        if current_module not in (1, 2):
            continue

        anchors = detect_question_anchors(page, left_ratio=0.25, max_line_chars=4)
        if not anchors:
            continue

        for i, (qnum, y0) in enumerate(anchors):
            if qnum in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            if i + 1 < len(anchors):
                next_y = anchors[i + 1][1]
                y_cap = clamp(next_y - 1, 0, h)
                y_end = clamp(next_y - pad_bottom, y_start + 80, y_cap)
            else:
                y_cap = h
                y_end = clamp(h - 8, y_start + 80, h)

            # (1) 우선 꼬리말 캡을 적용: 이 아래로는 절대 포함하지 않음
            footer_y = find_footer_start_y(page, y_start, y_cap)
            if footer_y is not None and footer_y > y_start + 120:
                y_cap = min(y_cap, footer_y - 4)
                y_end = min(y_end, y_cap)

            mcq_last = last_choice_bottom_y_in_band(page, y_start, y_cap)
            is_frq = (mcq_last is None)

            if mcq_last is not None:
                y_end = clamp(max(y_end, mcq_last + 18), y_start + 80, y_cap)

            # (2) 텍스트 기반 공백 축소(꼬리말 제외)
            bottom = content_bottom_y(page, y_start, y_end)
            if bottom is not None and bottom > y_start + 140:
                if mcq_last is not None:
                    bottom = max(bottom, mcq_last + 10)
                y_end = min(y_end, bottom + 14)

            # (3) 좌우 1차: 텍스트 기준
            xb = text_x_bounds_in_band(page, y_start, y_end, min_len=2)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 80, w)

            # (4) 잉크 bbox(그림 포함)로 타이트: 단, y는 y_cap(꼬리말 위)까지만
            scan_clip = fitz.Rect(0, y_start, w, y_end)
            px_bbox = ink_bbox_by_raster(page, scan_clip)
            if px_bbox is not None:
                tight = px_bbox_to_page_rect(scan_clip, px_bbox, pad_px=INK_PAD_PX)
                x0 = clamp(tight.x0, 0, w)
                x1 = clamp(tight.x1, x0 + 80, w)

                new_y_end = clamp(tight.y1, y_start + 80, y_end)
                if mcq_last is not None:
                    new_y_end = max(new_y_end, mcq_last + 12)
                y_end = clamp(new_y_end, y_start + 80, y_end)

            # (5) FRQ 여백 추가(단, 꼬리말/다음 문제 시작 캡을 절대 넘지 않음)
            if is_frq:
                y_end = min(y_cap, y_end + frq_extra_pt)

            clip = fitz.Rect(x0, y_start, x1, y_end)
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

st.title("SAT 수학 PDF → 문제별 PNG (꼬리말 제거 + FRQ 여백)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_top = col2.slider("위 여백(번호 포함)", 0, 140, 10, 1)
pad_bottom = col3.slider("아래 여백(다음 문제 전)", 0, 200, 12, 1)
frq_space = col4.slider("FRQ 아래 여백(px)", 0, 600, 250, 25)

if pdf is None:
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드"):
    with st.spinner("문제별로 자르는 중..."):
        module_map = split_pdf(
            pdf.read(),
            zoom=zoom,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
            frq_extra_space_px=frq_space,
        )

    st.success(f"완료: M1 {len(module_map.get(1, {}))}개, M2 {len(module_map.get(2, {}))}개")
    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button("ZIP 다운로드", data=zbuf, file_name=zip_filename, mime="application/zip")
