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

# "21." 같은 토큰 / 또는 "21" + "." 결합을 처리
NUMDOT_RE = re.compile(r"^(\d{1,2})\.$$")
NUM_RE = re.compile(r"^\d{1,2}$")

# 보기 감지
CHOICE_LABELS = ["D)", "C)", "B)", "A)"]

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

def group_words_into_lines(words, y_tol=2.5):
    # words: (x0,y0,x1,y1,txt,block,line,word)
    # line key = (block, line)
    lines = {}
    for w in words:
        x0,y0,x1,y1,txt,block_no,line_no,word_no = w
        key = (block_no, line_no)
        lines.setdefault(key, []).append((x0,y0,x1,y1,txt))
    for k in lines:
        lines[k].sort(key=lambda t: t[0])
    return list(lines.values())

def detect_question_anchors(page, left_ratio=0.25, max_line_chars=4):
    """
    진짜 문제번호 라인만 잡기:
    - 왼쪽에 있음
    - 라인 텍스트가 거의 'n.' 단독(공백 제외 길이 max_line_chars 이하)
    - 머리말/연락처 라인은 제외
    - "n."이 하나의 토큰이거나, n과 '.'이 인접 토큰으로 분리된 경우도 처리
    Returns: list of (qnum, y0)
    """
    w_page = page.rect.width
    words = page.get_text("words")
    if not words:
        return []

    lines = group_words_into_lines(words)
    anchors = []

    for tokens in lines:
        # tokens: [(x0,y0,x1,y1,txt), ...] sorted by x0
        line_text = " ".join(t[4] for t in tokens).strip()
        compact = re.sub(r"\s+", "", line_text)

        # 머리말/연락처 줄 제외
        if HEADER_FOOTER_HINT_RE.search(line_text):
            continue

        # 라인이 너무 길면 문제번호 단독 라인이 아닐 확률이 큼
        if len(compact) > max_line_chars:
            continue

        # 라인 왼쪽 위치
        x_left = min(t[0] for t in tokens)
        if x_left > w_page * left_ratio:
            continue

        # 케이스1: '21.' 토큰이 그대로 있음
        qnum = None
        for (x0,y0,x1,y1,txt) in tokens:
            m = NUMDOT_RE.match(txt)
            if m:
                qnum = int(m.group(1))
                y_top = y0
                break

        # 케이스2: '21' '.' 분리
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

    anchors.sort(key=lambda t: t[1])  # y 순
    return anchors

def last_choice_bottom_y_in_band(page, y_from, y_to):
    # 현재 문제 band 내부에서만 마지막 보기 위치 찾기(없으면 None)
    clip = fitz.Rect(0, y_from, page.rect.width, y_to)
    t = (page.get_text("text", clip=clip) or "")
    if "A)" not in t:
        return None
    for lab in CHOICE_LABELS:
        rects = page.search_for(lab)  # 페이지 전체지만, y band로 필터
        bottoms = [r.y1 for r in rects if (r.y1 >= y_from and r.y0 <= y_to)]
        if bottoms:
            return max(bottoms)
    return None

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
        xs0.append(x0); xs1.append(x1)
    if not xs0:
        return None
    return min(xs0), max(xs1)

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

            # MCQ면 보기 마지막(D/C/B/A)까지는 반드시 포함(단, 다음 문제로는 절대 안 감)
            mcq_last = last_choice_bottom_y_in_band(page, y_start, y_cap)
            if mcq_last is not None:
                y_end = clamp(max(y_end, mcq_last + 18), y_start + 80, y_cap)

            # 머리말 컷: 보기보다 위로는 절대 자르지 않음
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 220:
                if mcq_last is None or cut_y > mcq_last + 30:
                    y_end = clamp(cut_y - 6, y_start + 80, y_end)

            # 공백 줄이기(보기 보호)
            bottom = content_bottom_y(page, y_start, y_end)
            if bottom is not None and bottom > y_start + 140:
                if mcq_last is not None:
                    bottom = max(bottom, mcq_last + 10)
                y_end = min(y_end, bottom + 14)

            xb = text_x_bounds_in_band(page, y_start, y_end, min_len=2)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 80, w)

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

st.title("SAT 수학 PDF → 문제별 PNG (문제 속 숫자 오탐 제거 버전)")

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
