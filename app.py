import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 문제별 PNG", layout="wide")

QNUM_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")
MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)

# 참고자료에 실제 등장하는 머리말/연락처 문자열들
HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk\s*:|Instagram\s*:|010-\d{3,4}-\d{4}|700\+\s*MOCK\s*TEST)",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def get_blocks(page):
    # blocks: (x0, y0, x1, y1, text, ...)
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

def find_question_starts(page, top_exclude_ratio=0.10):
    h = page.rect.height
    words = page.get_text("words")
    # words: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
    starts = []
    for w in words:
        y0 = w[1]
        txt = w[4]
        if y0 < top_exclude_ratio * h:
            continue
        m = QNUM_RE.match(txt)
        if m:
            qnum = int(m.group(1))
            # SAT 모듈당 1~22 가정
            if 1 <= qnum <= 22:
                starts.append((y0, qnum))
    starts.sort(key=lambda t: t[0])
    return starts

def find_header_footer_cut_y(page, y_from, y_to):
    # y_from~y_to 사이에 머리말/연락처 블록이 끼면 그 시작 y에서 자르기
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

def render_clip(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")

def split_pdf_to_images(pdf_bytes, zoom=3.0, pad_x=12, pad_top=6, pad_bottom=10):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # 결과: {1: {q: png}, 2: {q: png}}
    out = {1: {}, 2: {}}

    current_module = None

    for pno in range(len(doc)):
        page = doc[pno]
        w = page.rect.width
        h = page.rect.height

        mid = find_module_on_page(page)
        if mid is not None:
            current_module = mid

        starts = find_question_starts(page)

        if not starts:
            continue

        for i, (y0, qnum) in enumerate(starts):
            if current_module not in (1, 2):
                # 모듈을 못 찾은 페이지면 스킵
                continue

            # 이미 해당 문제를 뽑았으면(다른 페이지에서 중복 감지) 스킵
            if qnum in out[current_module]:
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            # 기본 끝: 다음 문제 시작 직전, 없으면 페이지 끝
            if i + 1 < len(starts):
                y_end = starts[i + 1][0] - pad_bottom
            else:
                y_end = h - 8

            y_end = clamp(y_end, y_start + 10, h)

            # 머리말/연락처가 문제 영역 사이에 끼면 그 위에서 컷
            cut_y = find_header_footer_cut_y(page, y_start, y_end)
            if cut_y is not None and cut_y > y_start + 40:
                y_end = clamp(cut_y - 6, y_start + 10, y_end)

            clip = fitz.Rect(0 + pad_x, y_start, w - pad_x, y_end)
            out[current_module][qnum] = render_clip(page, clip, zoom)

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

st.title("SAT 수학 PDF → 문제별 PNG (M1/M2 폴더, 파일명 1.png..22.png)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 3.0, 0.1)
pad_x = col2.slider("좌우 여백", 0, 40, 12, 1)
pad_top = col3.slider("위 여백", 0, 40, 6, 1)
pad_bottom = col4.slider("아래 여백", 0, 60, 10, 1)

if pdf is None:
    st.info("PDF를 업로드하세요.")
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드"):
    with st.spinner("자르는 중..."):
        module_map = split_pdf_to_images(
            pdf.read(),
            zoom=zoom,
            pad_x=pad_x,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
        )

    c1 = len(module_map.get(1, {}))
    c2 = len(module_map.get(2, {}))
    st.success(f"완료: M1 {c1}개, M2 {c2}개")

    # 미리보기: 각 모듈 2개씩
    st.subheader("미리보기(각 모듈 일부)")
    for mod in (1, 2):
        st.write(f"### M{mod}")
        shown = 0
        for q in range(1, 23):
            data = module_map.get(mod, {}).get(q)
            if data is None:
                continue
            st.write(f"{q}.png")
            st.image(data)
            shown += 1
            if shown >= 2:
                break
        if shown == 0:
            st.warning(f"M{mod}에서 추출된 이미지가 없습니다.")

    miss1 = [n for n in range(1, 23) if n not in module_map.get(1, {})]
    miss2 = [n for n in range(1, 23) if n not in module_map.get(2, {})]
    with st.expander("누락된 번호(진단용)"):
        st.write("M1 누락:", miss1)
        st.write("M2 누락:", miss2)

    zbuf, zip_filename = make_zip(module_map, zip_base)
    st.download_button(
        "ZIP 다운로드",
        data=zbuf,
        file_name=zip_filename,
        mime="application/zip",
    )
