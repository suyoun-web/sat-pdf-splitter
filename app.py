import re
import io
import zipfile
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="SAT PDF → 모듈별 문제 이미지", layout="wide")

# "<MODULE1>" "<MODULE2>"
MODULE_RE = re.compile(r"^<\s*MODULE\s*(\d+)\s*>$$", re.IGNORECASE)

# "2." 같은 문제 번호 (라인 텍스트에서 찾음)
QNUM_LINE_RE = re.compile(r"^\s*(\d{1,3})\.\s*$$")

# 머리말/연락처 힌트(발췌에 실제 등장)
FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|Kakaotalk|Instagram|010-\d{3,4}-\d{4})",
    re.IGNORECASE,
)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def extract_module_id_from_blocks(page):
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

def find_footer_cut_y_blocks(page, y_from, y_to):
    """blocks 기반: 지정 y구간에 머리말 힌트가 있는 블록이 있으면 그 최상단 y 반환"""
    blocks = page.get_text("blocks")
    ys = []
    for b in blocks:
        if len(b) < 5:
            continue
        y0 = b[1]
        text = b[4]
        if y0 < y_from or y0 > y_to:
            continue
        if text and FOOTER_HINT_RE.search(str(text)):
            ys.append(y0)
    return min(ys) if ys else None

def iter_question_anchors_by_lines(page, top_exclude_ratio=0.10):
    """
    page.get_text("dict")의 lines를 순회하며
    라인 텍스트가 정확히 '2.' 같은 경우를 문제 시작점(앵커)로 잡는다.
    Returns
