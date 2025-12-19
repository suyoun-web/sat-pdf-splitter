import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 모듈별 문제 이미지", layout="wide")

# 문제 번호 토큰: "1." "2." ...
QNUM_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")
# 모듈 표기: "<MODULE1>", "<MODULE2>"
MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)

# PDF 내 반복 머리말/연락처(발췌에도 실제 존재) 잘라내기 힌트
FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4})",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def extract_module_id(page):
    """
    page.get_text("blocks")는 보통 (x0,y0,x1,y1,text,block_no,block_type,...) 튜플 리스트.
    starred unpacking을 쓰지 않고 안전하게 인덱싱한다.
    """
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
                return mid
    return None

def find_footer_cut_y(page, y_from, y_to):
    blocks = page.get_text("blocks")
    ys = []
    for b in blocks:
        if len(b) < 5:
            continue
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        text = b[4]
        if y0 < y_from or y0 > y_to:
            continue
        if text and FOOTER_HINT_RE.search(str(text)):
            ys.append(y0)
    return min(ys) if ys else None

def split_pdf(pdf_bytes, zoom=2.8, pad_x=14, pad_top=6, pad_bottom=10, top_exclude_ratio=0.12):
    """
    Returns:
      {1: {qnum: png_bytes}, 2: {qnum: png_bytes}}
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    current_module = None
    out = {1: {}, 2: {}}

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height

        mid = extract_module_id(page)
        if mid in (1, 2):
            current_module = mid

        # 단어 단위 추출: (x0,y0,x1,y1,"word", block_no, line_no, word_no)
        words = page.get_text("words")
        words.sort(key=lambda t: (t[1], t[0]))  # y, x

        starts = []
        for (x0, y0, x1, y1, txt, *_) in words:
            # 페이지 맨 위 머리말 영역에서 잡히는 숫자를 줄이기 위해 상단 일부 제외
            if y0 < top_exclude_ratio * h:
                continue
            if QNUM_RE.match(txt):
                starts.append((y0, txt))

        if not starts:
            continue

        starts.sort(key=lambda t: t[0])

        for i, (y0, txt) in enumerate(starts):
            if current_module not in (1, 2):
                # 모듈 판정 전이면 스킵 (당신 PDF는 <MODULE1>, <MODULE2>가 있으니 보통 안 걸림)
                continue

            qnum = int(re.sub(r"\D", "", txt))
            if not (1 <= qnum <= 22):
                continue

            y_start = clamp(y0 - pad_top, 0, h)

            # 기본 끝: 다음 문제 번호 직전, 마지막이면 페이지 끝 근처
            y_end_default = (starts[i + 1][0] - pad_bottom) if i + 1 < len(starts) else (h - 10)
            y_end_default = clamp(y_end_default, y_start + 5, h)

            # 문제 끝 구간에 반복 머리말이 섞이면 그 위에서 컷
            footer_cut_y = find_footer_cut_y(page, y_start, y_end_default)
            y_end = y_end_default
            if footer_cut_y is not None and footer_cut_y > y_start + 20:
                y_end = clamp(footer_cut_y - 6, y_start + 5, y_end_default)

            clip = fitz.Rect(0 + pad_x, y_start, w - pad_x, y_end)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            png_bytes = pix.tobytes("png")

            # 같은 번호가 중복 감지되면 첫 번째 유지(이상 케이스 방어)
            out[current_module].setdefault(qnum, png_bytes)

    return out

def build_zip(module_map, zip_base_name):
    """
    ZIP 내부:
      M1/1.png ... M1/22.png
      M2/1.png ... M2/22.png
    ZIP 파일명:
      업로드 PDF 이름과 동일한 베이스명 + .zip
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for mod in (1, 2):
            for qnum in range(1, 23):
                data = module_map.get(mod, {}).get(qnum)
                if data is None:
                    continue
                z.writestr(f"M{mod}/{qnum}.png", data)
    buf.seek(0)
    return buf, f"{zip_base_name}.zip"

st.title("SAT 수학 PDF → 문제별 PNG (M1/M2 폴더, 파일명 1.png..22.png)")

pdf = st.file_uploader("PDF 업로드", type=["pdf"])

col1, col2, col3, col4 = st.columns(4)
zoom = col1.slider("해상도(zoom)", 2.0, 4.0, 2.8, 0.1)
pad_x = col2.slider("좌우 여백", 0, 40, 14, 1)
pad_top = col3.slider("위 여백", 0, 30, 6, 1)
pad_bottom = col4.slider("아래 여백", 0, 30, 10, 1)

if pdf is None:
    st.info("PDF를 업로드하세요.")
    st.stop()

pdf_name = pdf.name
zip_base = pdf_name[:-4] if pdf_name.lower().endswith(".pdf") else pdf_name

if st.button("생성 & ZIP 다운로드 준비"):
    with st.spinner("문제별로 자르는 중..."):
        module_map = split_pdf(
            pdf.read(),
            zoom=zoom,
            pad_x=pad_x,
            pad_top=pad_top,
            pad_bottom=pad_bottom,
        )

    c1 = len(module_map.get(1, {}))
    c2 = len(module_map.get(2, {}))
    st.success(f"완료: M1 {c1}개, M2 {c2}개 (각 최대 22개)")

    # 누락 번호 표시(원인 파악에 도움)
    miss1 = [n for n in range(1, 23) if n not in module_map.get(1, {})]
    miss2 = [n for n in range(1, 23) if n not in module_map.get(2, {})]
    with st.expander("누락된 번호 보기(문제가 안 잘렸을 때 확인용)"):
        st.write(f"M1 누락: {miss1}")
        st.write(f"M2 누락: {miss2}")

    zbuf, zip_filename = build_zip(module_map, zip_base)
    st.download_button(
        label=f"다운로드: {zip_filename}",
        data=zbuf,
        file_name=zip_filename,
        mime="application/zip",
    )
``_
