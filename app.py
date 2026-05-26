# sat_math_problem_bank_app.py
# =========================================================
# YOU, GENIUS SAT MATH Problem Bank App
# 1) PDF -> unit-code PNG ZIP
# 2) ZIP -> Google Drive upload + Google Sheet DB build
# 3) Student remedial packet PDF generation
# =========================================================

import io
import re
import csv
import random
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

import pandas as pd
import streamlit as st

# PDF/image crop
import fitz  # PyMuPDF
from PIL import Image

# Google
from google.oauth2 import service_account
import gspread
from gspread.exceptions import WorksheetNotFound
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# PDF packet
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# =========================================================
# SETTINGS
# =========================================================
st.set_page_config(page_title="YOU, GENIUS SAT MATH 문제은행", layout="wide")

APP_TITLE = "YOU, GENIUS SAT MATH 문제은행 & 보충 PACKET"
DEFAULT_SHEET_ID = "1qJSe8HX6mAQ8nKnnhfzz2PT-ycjM7W6kBsK6n9xZ3pY"
DB_SHEET_NAME = "Sheet1"
ROSTER_SHEET_NAME = "Sheet2"

FONT_NAME = "NotoSansKR"
FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansKR-VariableFont_wght.ttf"

FOLDER_MIME = "application/vnd.google-apps.folder"
PNG_MIME = "image/png"

DB_HEADERS = [
    "Code",
    "UnitName",
    "QNo",
    "FileName",
    "FolderName",
    "FolderLink",
    "DriveFileId",
    "ExpectedCount",
    "Answer",
    "Notes",
]

# =========================================================
# UNIT TABLE: user-provided 1255-question bank order
# =========================================================
UNIT_ROWS: List[Tuple[str, str, int]] = [
    ("1.1", "Linear function", 33),
    ("1.2", "Linear equation", 6),
    ("1.3", "Linear interpretation", 58),
    ("1.4", "Linear word problems", 54),
    ("1.5", "Linear inequality", 33),
    ("1.6", "Identity equation", 53),
    ("1.7", "Absolute function and equation", 15),
    ("1.8", "System of equations", 9),
    ("2.1", "Ratios and Percent", 52),
    ("2.2", "Unit conversion", 28),
    ("3.1", "Quadratic function", 48),
    ("3.2", "Quadratic equation and inequality", 34),
    ("3.3", "Sum and product", 13),
    ("3.4", "Discriminant", 40),
    ("3.5", "Quadratic Word problems", 55),
    ("3.6", "Factoring", 29),
    ("4.1", "Exponential equation", 10),
    ("4.2", "Exponential function", 56),
    ("4.3", "Exponential model", 69),
    ("5.1", "Polynomial equation and graph", 36),
    ("5.2", "Polynomial long division / factor / remainder theorem", 8),
    ("5.3", "Radical equation and function", 22),
    ("5.4", "Rational expression and rational exponent", 36),
    ("5.5", "Rational equation and function", 22),
    ("5.6", "Isolation", 16),
    ("6.1", "Similar and congruent triangles", 55),
    ("6.2", "Similar figure", 29),
    ("6.3", "Right triangle and trigonometry", 70),
    ("6.4", "Volume and surface area", 24),
    ("6.5", "Parallel lines", 13),
    ("6.6", "Circle", 67),
    ("6.7", "Polygon and ETC", 8),
    ("7.1", "Probability & Conditional probability", 32),
    ("7.2", "Scatter plot", 22),
    ("7.3", "Sampling method", 1),
    ("7.4", "Generalize", 7),
    ("7.5", "Mean, median, mode", 38),
    ("7.6", "Standard deviation", 21),
    ("7.7", "Margin of error", 14),
    ("7.8", "Experiment", 11),
    ("7.9", "Box plot", 8),
]

UNIT_DF = pd.DataFrame(UNIT_ROWS, columns=["Code", "UnitName", "ExpectedCount"])
UNIT_META: Dict[str, Dict[str, Any]] = {
    code: {"UnitName": unit, "ExpectedCount": int(cnt)} for code, unit, cnt in UNIT_ROWS
}
TOTAL_EXPECTED = int(UNIT_DF["ExpectedCount"].sum())


# =========================================================
# PDF crop constants and helpers
# =========================================================
HEADER_FOOTER_HINT_RE = re.compile(
    r"(YOU,\s*GENIUS|700\+\s*MOCK\s*TEST|Kakaotalk|Instagram|010-\d{3,4}-\d{4}|SECTION|Module)",
    re.IGNORECASE,
)
NUMDOT_RE = re.compile(r"^(\d{1,4})\.$")
NUM_RE = re.compile(r"^\d{1,4}$")
# This problem-bank PDF uses real problem anchors like: 12. [Example ...]
# Requiring the bracket prevents false anchors from section headings (1. Linear)
# and numeric answer choices (1., 2., 3., 4.).
QUESTION_START_RE = re.compile(r"^\s*(\d{1,4})\s*\.\s*\[")
CHOICE_LABELS = [
    "H)", "G)", "F)", "E)", "D)", "C)", "B)", "A)",
    "(H)", "(G)", "(F)", "(E)", "(D)", "(C)", "(B)", "(A)",
    # Some source questions use numbered choices instead of A-D.
    "4.", "3.", "2.", "1.",
]
SECTION_BREAK_RE = re.compile(
    r"^\s*(※|겨울\s*개념반|개념반/중급반|추가문항)",
    re.IGNORECASE,
)

SIDE_PAD_PX = 10
INK_PAD_PX = 10
SCAN_ZOOM = 0.6
WHITE_THRESH = 250


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\n\r\t]+', "_", str(name)).strip()


def safe_sheet_id() -> str:
    try:
        return str(st.secrets.get("sheet_id", DEFAULT_SHEET_ID)).strip()
    except Exception:
        return DEFAULT_SHEET_ID


def folder_display_name(code: str, unit_name: str) -> str:
    return sanitize_filename(f"{code} {unit_name}")


def parse_code_token(token: str) -> Optional[str]:
    s = str(token).strip().replace("_", ".").replace("-", ".")
    m = re.match(r"^(\d{1,2})\s*\.\s*(\d{1,2})$", s)
    if not m:
        return None
    return f"{int(m.group(1))}.{int(m.group(2))}"


def parse_needs(needs: str) -> List[str]:
    if needs is None:
        return []
    parts = re.split(r"[,;\n/|]+", str(needs))
    out: List[str] = []
    seen = set()
    for p in parts:
        code = parse_code_token(p)
        if code and code in UNIT_META and code not in seen:
            out.append(code)
            seen.add(code)
    return out


def parse_problem_filename(name: str) -> Optional[Tuple[str, int]]:
    """
    Accepts:
    - 1.1-5.png
    - folder/1.1-5.png
    - 1.1_5.png
    """
    base = Path(str(name)).name
    stem = Path(base).stem.strip()
    m = re.match(r"^(\d{1,2}\.\d{1,2})\s*[-_]\s*(\d{1,4})$", stem)
    if not m:
        return None
    code = parse_code_token(m.group(1))
    if not code:
        return None
    return code, int(m.group(2))


def get_unit_options() -> List[str]:
    return [f"{r.Code}  |  {r.UnitName}  ({int(r.ExpectedCount)}문항)" for r in UNIT_DF.itertuples()]


def label_to_code(label: str) -> Optional[str]:
    if not label:
        return None
    return parse_code_token(str(label).split("|")[0].strip())


# =========================================================
# Font helpers
# =========================================================
def ensure_korean_font_registered() -> bool:
    try:
        if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            if not FONT_PATH.exists():
                return False
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        return True
    except Exception:
        return False


def draw_fake_bold_string(c: canvas.Canvas, x: float, y: float, text: str, font: str, size: int):
    c.setFont(font, size)
    c.drawString(x, y, text)
    c.drawString(x + 0.35, y, text)
    c.drawString(x, y + 0.15, text)


# =========================================================
# Google helpers
# =========================================================
@st.cache_resource(show_spinner=False)
def get_creds():
    try:
        sa_info = st.secrets["gcp_service_account"]
    except Exception as e:
        raise RuntimeError(
            "Streamlit secrets에 [gcp_service_account]가 필요합니다. "
            "Google Drive/Sheets 탭을 쓰려면 서비스 계정 JSON을 secrets에 넣어주세요."
        ) from e

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)


@st.cache_resource(show_spinner=False)
def get_gspread_client():
    return gspread.authorize(get_creds())


@st.cache_resource(show_spinner=False)
def get_drive_service():
    return build("drive", "v3", credentials=get_creds())


def get_or_create_worksheet(sheet_id: str, worksheet_name: str, rows: int = 1000, cols: int = 20):
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet(worksheet_name)
    except WorksheetNotFound:
        return sh.add_worksheet(title=worksheet_name, rows=rows, cols=cols)


def extract_folder_id(folder_link_or_id: str) -> str:
    s = str(folder_link_or_id).strip()
    if not s:
        raise ValueError("Drive 폴더 링크 또는 ID가 비어 있습니다.")
    patterns = [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"^([a-zA-Z0-9_-]{15,})$",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    raise ValueError(f"Folder ID를 찾을 수 없는 형식입니다: {s}")


def drive_link(file_or_folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{file_or_folder_id}"


def q_escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def drive_list_all(query: str, fields: str = "nextPageToken, files(id,name,webViewLink,mimeType)") -> List[Dict[str, Any]]:
    drive = get_drive_service()
    files: List[Dict[str, Any]] = []
    token = None
    while True:
        resp = (
            drive.files()
            .list(
                q=query,
                fields=fields,
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files


@st.cache_data(show_spinner=False, ttl=60 * 20)
def list_png_files_in_folder(folder_id: str) -> Dict[str, str]:
    q = f"'{folder_id}' in parents and trashed=false and mimeType='{PNG_MIME}'"
    files = drive_list_all(q, fields="nextPageToken, files(id,name)")
    return {f["name"]: f["id"] for f in files}


def find_child_folder(parent_id: str, folder_name: str) -> Optional[Dict[str, str]]:
    q = (
        f"'{parent_id}' in parents and trashed=false "
        f"and mimeType='{FOLDER_MIME}' and name='{q_escape(folder_name)}'"
    )
    files = drive_list_all(q, fields="nextPageToken, files(id,name,webViewLink)")
    return files[0] if files else None


def get_or_create_child_folder(parent_id: str, folder_name: str) -> Dict[str, str]:
    found = find_child_folder(parent_id, folder_name)
    if found:
        return found

    drive = get_drive_service()
    body = {"name": folder_name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    created = (
        drive.files()
        .create(body=body, fields="id,name,webViewLink", supportsAllDrives=True)
        .execute()
    )
    return created


def find_file_in_folder(folder_id: str, filename: str) -> Optional[str]:
    q = (
        f"'{folder_id}' in parents and trashed=false "
        f"and mimeType='{PNG_MIME}' and name='{q_escape(filename)}'"
    )
    files = drive_list_all(q, fields="nextPageToken, files(id,name)")
    return files[0]["id"] if files else None


def upload_or_update_png(folder_id: str, filename: str, data: bytes, overwrite: bool) -> str:
    drive = get_drive_service()
    existing_id = find_file_in_folder(folder_id, filename)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=PNG_MIME, resumable=False)

    if existing_id and overwrite:
        updated = (
            drive.files()
            .update(
                fileId=existing_id,
                media_body=media,
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute()
        )
        return updated["id"]

    if existing_id and not overwrite:
        return existing_id

    body = {"name": filename, "parents": [folder_id], "mimeType": PNG_MIME}
    created = (
        drive.files()
        .create(
            body=body,
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]


def download_drive_file_bytes(file_id: str) -> bytes:
    drive = get_drive_service()
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


# =========================================================
# Sheet DB helpers
# =========================================================
def normalize_db(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # New schema
    if "Code" in df.columns:
        df["Code"] = df["Code"].apply(lambda x: parse_code_token(x) or str(x).strip())
    # Old schema support: Major/Minor -> Code
    elif {"Major", "Minor"}.issubset(df.columns):
        df["Code"] = df.apply(
            lambda r: f"{int(float(r['Major']))}.{int(float(r['Minor']))}"
            if str(r.get("Major", "")).strip() and str(r.get("Minor", "")).strip()
            else "",
            axis=1,
        )

    if "UnitName" not in df.columns:
        if "MinorFolder" in df.columns:
            df["UnitName"] = df["MinorFolder"].astype(str)
        else:
            df["UnitName"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("UnitName", ""))

    if "ExpectedCount" not in df.columns:
        df["ExpectedCount"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("ExpectedCount", ""))

    if "FileName" not in df.columns:
        df["FileName"] = df.apply(
            lambda r: f"{r.get('Code','')}-{int(float(r.get('QNo', 0)))}.png"
            if str(r.get("Code", "")).strip() and str(r.get("QNo", "")).strip()
            else "",
            axis=1,
        )

    if "QNo" not in df.columns:
        df["QNo"] = df["FileName"].apply(lambda x: parse_problem_filename(x)[1] if parse_problem_filename(x) else None)

    if "FolderName" not in df.columns:
        df["FolderName"] = df.apply(lambda r: folder_display_name(r.get("Code", ""), r.get("UnitName", "")), axis=1)

    for c in DB_HEADERS:
        if c not in df.columns:
            df[c] = ""

    df = df[DB_HEADERS].copy()
    df["QNo"] = pd.to_numeric(df["QNo"], errors="coerce").astype("Int64")
    df["ExpectedCount"] = pd.to_numeric(df["ExpectedCount"], errors="coerce").astype("Int64")
    df = df[df["Code"].isin(UNIT_META.keys())]
    df = df[df["FileName"].astype(str).str.strip() != ""]
    df = df.sort_values(["Code", "QNo"], key=lambda col: col.map(sort_code_key) if col.name == "Code" else col)
    return df.reset_index(drop=True)


def sort_code_key(code: str) -> Tuple[int, int]:
    parsed = parse_code_token(code)
    if not parsed:
        return (999, 999)
    a, b = parsed.split(".")
    return int(a), int(b)


@st.cache_data(show_spinner=False, ttl=60 * 5)
def load_db_from_sheet(sheet_id: str) -> pd.DataFrame:
    ws = get_or_create_worksheet(sheet_id, DB_SHEET_NAME)
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=DB_HEADERS)
    return normalize_db(pd.DataFrame(records))


@st.cache_data(show_spinner=False, ttl=60 * 5)
def load_roster_from_sheet(sheet_id: str) -> pd.DataFrame:
    ws = get_or_create_worksheet(sheet_id, ROSTER_SHEET_NAME, rows=1000, cols=10)
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=["Class", "StudentName", "Needs"])
    df = pd.DataFrame(records)
    for c in ["Class", "StudentName", "Needs"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].astype(str)
    df = df[(df["Class"].str.strip() != "") & (df["StudentName"].str.strip() != "")]
    return df[["Class", "StudentName", "Needs"]].reset_index(drop=True)


def write_db_rows(sheet_id: str, rows: List[Dict[str, Any]], mode: str = "replace"):
    ws = get_or_create_worksheet(sheet_id, DB_SHEET_NAME, rows=max(1000, len(rows) + 20), cols=len(DB_HEADERS) + 2)

    values = [DB_HEADERS]
    for r in rows:
        values.append([r.get(h, "") for h in DB_HEADERS])

    if mode == "replace":
        ws.clear()
        ws.update(values=values, range_name="A1")
    else:
        existing = ws.get_all_values()
        if not existing:
            ws.update(values=values, range_name="A1")
        else:
            # append rows only; assumes header exists
            append_values = values[1:]
            if append_values:
                ws.append_rows(append_values, value_input_option="USER_ENTERED")

    try:
        ws.format("A1:J1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
        ws.freeze(rows=1)
    except Exception:
        pass

    load_db_from_sheet.clear()


def ensure_roster_headers(sheet_id: str):
    ws = get_or_create_worksheet(sheet_id, ROSTER_SHEET_NAME, rows=1000, cols=10)
    values = ws.get_all_values()
    if not values:
        ws.update(values=[["Class", "StudentName", "Needs"]], range_name="A1")
    else:
        header = [str(x).strip() for x in values[0]]
        if not {"Class", "StudentName", "Needs"}.issubset(set(header)):
            ws.update(values=[["Class", "StudentName", "Needs"]], range_name="A1")
    try:
        ws.format("A1:C1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
        ws.freeze(rows=1)
    except Exception:
        pass


# =========================================================
# PDF crop engine
# =========================================================
def group_words_into_lines(words):
    lines = {}
    for w in words:
        x0, y0, x1, y1, txt, block_no, line_no, word_no = w
        key = (block_no, line_no)
        lines.setdefault(key, []).append((x0, y0, x1, y1, txt))
    for k in lines:
        lines[k].sort(key=lambda t: t[0])
    return list(lines.values())


def detect_question_anchors(page, left_ratio=0.28, max_line_chars=8, require_bracket_after_number=True):
    """
    Detect only real problem starts.

    For this problem-bank PDF, real starts look like:
        13. [개념반 Example 1.1-8)]
        35. [Example 1.3-35)]

    Requiring '[' after the printed number prevents false crops from:
    - section titles like "1. Linear", "2. Percent & Unit conversion", "3. Quadratic"
    - numbered answer choices like "1.", "2.", "3.", "4."
    - wrapped sentences that begin a new line with something like "8. What is ..."
    """
    w_page = page.rect.width
    h_page = page.rect.height
    words = page.get_text("words")
    if not words:
        return []

    lines = group_words_into_lines(words)
    anchors = []

    for tokens in lines:
        if not tokens:
            continue
        line_text = " ".join(t[4] for t in tokens).strip()
        compact = re.sub(r"\s+", "", line_text)

        if HEADER_FOOTER_HINT_RE.search(line_text):
            continue

        x_left = min(t[0] for t in tokens)
        y_top = min(t[1] for t in tokens)
        if x_left > w_page * left_ratio:
            continue
        if y_top > h_page * 0.96:
            continue

        qnum = None
        after_index = None

        # Case 1: first token is "125."
        m = NUMDOT_RE.match(tokens[0][4])
        if m:
            qnum = int(m.group(1))
            after_index = 1

        # Case 2: first two tokens are "125" "."
        if qnum is None and len(tokens) >= 2 and NUM_RE.match(tokens[0][4]) and tokens[1][4] == ".":
            qnum = int(tokens[0][4])
            after_index = 2

        # Fallback: old strict mode where the line is almost only "n."
        if qnum is None and not require_bracket_after_number and len(compact) <= max_line_chars:
            for idx, (x0, y0, x1, y1, txt) in enumerate(tokens):
                m = NUMDOT_RE.match(txt)
                if m:
                    qnum = int(m.group(1))
                    y_top = y0
                    after_index = idx + 1
                    break

        if qnum is None:
            continue

        if require_bracket_after_number:
            after_text = " ".join(t[4] for t in tokens[after_index:after_index + 6])
            if "[" not in after_text:
                continue
        elif len(compact) > max_line_chars:
            continue

        anchors.append((qnum, y_top))

    # Deduplicate anchors very close to each other
    anchors.sort(key=lambda t: t[1])
    deduped = []
    for q, y in anchors:
        if deduped and abs(y - deduped[-1][1]) < 3:
            continue
        deduped.append((q, y))
    return deduped

def band_text(page, clip):
    return page.get_text("text", clip=clip) or ""


def last_choice_bottom_y_in_band(page, y_from, y_to):
    clip = fitz.Rect(0, y_from, page.rect.width, y_to)
    t = band_text(page, clip)
    if not any(a in t for a in ["A)", "(A)"]):
        return None
    bottoms = []
    for lab in CHOICE_LABELS:
        rects = page.search_for(lab)
        bottoms.extend([r.y1 for r in rects if (r.y1 >= y_from and r.y0 <= y_to)])
    return max(bottoms) if bottoms else None


def find_footer_start_y(page, y_from, y_to):
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



def find_section_break_y(page, y_from, y_to):
    """Return y of divider text such as '※ 겨울 ...' between two problems."""
    ys = []
    for tokens in group_words_into_lines(page.get_text("words")):
        if not tokens:
            continue
        line_text = " ".join(t[4] for t in tokens).strip()
        y0 = min(t[1] for t in tokens)
        if y0 < y_from or y0 > y_to:
            continue
        if y0 <= y_from + 35:
            continue
        if SECTION_BREAK_RE.search(line_text):
            ys.append(y0)
    return min(ys) if ys else None

def content_bottom_y(page, y_from, y_to):
    bottoms = []
    for b in page.get_text("blocks"):
        if len(b) < 5:
            continue
        y0, y1, text = b[1], b[3], b[4]
        if y1 < y_from or y0 > y_to:
            continue
        if text and HEADER_FOOTER_HINT_RE.search(str(text)):
            continue
        if text and str(text).strip():
            bottoms.append(y1)
    return max(bottoms) if bottoms else None


def text_x_bounds_in_band(page, y_from, y_to, min_len=1):
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
                minx = min(minx, x)
                miny = min(miny, y)
                maxx = max(maxx, x)
                maxy = max(maxy, y)

    if maxx < 0:
        return None
    return minx, miny, maxx, maxy, w, h


def px_bbox_to_page_rect(clip, px_bbox, pad_px=INK_PAD_PX):
    minx, miny, maxx, maxy, w, h = px_bbox
    minx = max(0, minx - pad_px)
    miny = max(0, miny - pad_px)
    maxx = min(w - 1, maxx + pad_px)
    maxy = min(h - 1, maxy + pad_px)

    x0 = clip.x0 + (minx / max(1, w - 1)) * (clip.x1 - clip.x0)
    x1 = clip.x0 + (maxx / max(1, w - 1)) * (clip.x1 - clip.x0)
    y0 = clip.y0 + (miny / max(1, h - 1)) * (clip.y1 - clip.y0)
    y1 = clip.y0 + (maxy / max(1, h - 1)) * (clip.y1 - clip.y0)
    return fitz.Rect(x0, y0, x1, y1)


def render_png(page, clip, zoom):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")


def expand_rect_to_width_right_only(rect, target_width, page_width):
    if rect.width >= target_width:
        return rect
    new_x0 = rect.x0
    new_x1 = clamp(rect.x0 + target_width, new_x0 + 80, page_width)
    return fitz.Rect(new_x0, rect.y0, new_x1, rect.y1)


def compute_rects_for_pdf(
    pdf_bytes: bytes,
    zoom: float = 3.0,
    pad_top: int = 10,
    pad_bottom: int = 12,
    last_question_extra_px: int = 30,
    require_bracket_after_number: bool = True,
):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    rects = []
    side_pad_pt = SIDE_PAD_PX / zoom
    last_extra_pt = last_question_extra_px / zoom

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height
        anchors = detect_question_anchors(page, require_bracket_after_number=require_bracket_after_number)
        if not anchors:
            continue

        for i, (printed_qnum, y0) in enumerate(anchors):
            y_start = clamp(y0 - pad_top, 0, h)

            if i + 1 < len(anchors):
                next_y = anchors[i + 1][1]
                y_cap = clamp(next_y - 1, 0, h)
                y_end = clamp(next_y - pad_bottom, y_start + 60, y_cap)
            else:
                y_cap = h - 8
                y_end = clamp(h - 8, y_start + 60, h)

            footer_y = find_footer_start_y(page, y_start, y_cap)
            if footer_y is not None and footer_y > y_start + 120:
                y_cap = min(y_cap, footer_y - 4)
                y_end = min(y_end, y_cap)

            section_break_y = find_section_break_y(page, y_start, y_cap)
            if section_break_y is not None and section_break_y > y_start + 60:
                y_cap = min(y_cap, section_break_y - 4)
                y_end = min(y_end, y_cap)

            mcq_last = last_choice_bottom_y_in_band(page, y_start, y_cap)
            if mcq_last is not None:
                y_end = clamp(max(y_end, mcq_last + 18), y_start + 60, y_cap)

            bottom = content_bottom_y(page, y_start, y_end)
            if bottom is not None and bottom > y_start + 80:
                if mcq_last is not None:
                    bottom = max(bottom, mcq_last + 10)
                y_end = min(y_end, bottom + 14)

            # Last question on a page often needs a little more blank space below.
            if i + 1 == len(anchors):
                y_end = min(y_cap, y_end + last_extra_pt)

            xb = text_x_bounds_in_band(page, y_start, y_end, min_len=1)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 80, w)

            scan_clip = fitz.Rect(0, y_start, w, y_end)
            px_bbox = ink_bbox_by_raster(page, scan_clip)
            if px_bbox is not None:
                tight = px_bbox_to_page_rect(scan_clip, px_bbox, pad_px=INK_PAD_PX)
                x0 = clamp(tight.x0, 0, w)
                x1 = clamp(tight.x1, x0 + 80, w)
                new_y_end = clamp(tight.y1, y_start + 60, y_end)
                if mcq_last is not None:
                    new_y_end = max(new_y_end, mcq_last + 12)
                y_end = clamp(new_y_end, y_start + 60, y_end)

            rects.append(
                {
                    "order": len(rects) + 1,
                    "printed_qnum": printed_qnum,
                    "page": pno,
                    "rect": fitz.Rect(x0, y_start, x1, y_end),
                    "page_width": w,
                }
            )

    return doc, rects


def build_full_filename_plan() -> List[Dict[str, Any]]:
    plan = []
    order = 1
    for code, unit_name, cnt in UNIT_ROWS:
        for qno in range(1, cnt + 1):
            plan.append(
                {
                    "order": order,
                    "Code": code,
                    "UnitName": unit_name,
                    "QNo": qno,
                    "FileName": f"{code}-{qno}.png",
                    "FolderName": folder_display_name(code, unit_name),
                    "ExpectedCount": cnt,
                }
            )
            order += 1
    return plan


def build_single_unit_filename_plan(code: str, count: int) -> List[Dict[str, Any]]:
    meta = UNIT_META[code]
    return [
        {
            "order": i,
            "Code": code,
            "UnitName": meta["UnitName"],
            "QNo": i,
            "FileName": f"{code}-{i}.png",
            "FolderName": folder_display_name(code, meta["UnitName"]),
            "ExpectedCount": meta["ExpectedCount"],
        }
        for i in range(1, count + 1)
    ]


def read_mapping_csv(uploaded_file) -> List[Dict[str, Any]]:
    data = uploaded_file.read()
    text = data.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    plan = []
    for idx, row in enumerate(rows, start=1):
        raw_name = str(row.get("FileName") or row.get("filename") or row.get("Name") or row.get("name") or "").strip()
        parsed = parse_problem_filename(raw_name)
        if not parsed:
            continue
        code, qno = parsed
        meta = UNIT_META.get(code, {"UnitName": "", "ExpectedCount": ""})
        plan.append(
            {
                "order": int(row.get("order") or row.get("Order") or idx),
                "Code": code,
                "UnitName": meta["UnitName"],
                "QNo": qno,
                "FileName": f"{code}-{qno}.png",
                "FolderName": folder_display_name(code, meta["UnitName"]),
                "ExpectedCount": meta["ExpectedCount"],
            }
        )
    plan.sort(key=lambda x: x["order"])
    return plan


def make_zip_from_rects(
    doc,
    rects: List[Dict[str, Any]],
    plan: List[Dict[str, Any]],
    zoom: float,
    zip_base_name: str,
    put_unit_folders: bool = True,
    unify_width: bool = True,
) -> Tuple[io.BytesIO, str, pd.DataFrame]:
    maxw = 0.0
    if unify_width:
        for r in rects:
            maxw = max(maxw, r["rect"].width)

    index_rows = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for i, r in enumerate(rects):
            if i < len(plan):
                meta = plan[i]
            else:
                meta = {
                    "order": i + 1,
                    "Code": "UNMAPPED",
                    "UnitName": "Unmapped",
                    "QNo": i + 1,
                    "FileName": f"UNMAPPED-{i + 1}.png",
                    "FolderName": "UNMAPPED",
                    "ExpectedCount": "",
                }

            page = doc[r["page"]]
            rect = r["rect"]
            if unify_width and maxw > 0:
                rect = expand_rect_to_width_right_only(rect, maxw, r["page_width"])

            png = render_png(page, rect, zoom)
            arcname = meta["FileName"]
            if put_unit_folders and meta["Code"] != "UNMAPPED":
                arcname = f"{meta['FolderName']}/{meta['FileName']}"
            elif put_unit_folders:
                arcname = f"UNMAPPED/{meta['FileName']}"

            z.writestr(arcname, png)
            index_rows.append(
                {
                    "Order": i + 1,
                    "PrintedQNo": r.get("printed_qnum", ""),
                    "Page": int(r["page"]) + 1,
                    "Code": meta["Code"],
                    "UnitName": meta["UnitName"],
                    "QNo": meta["QNo"],
                    "FileName": meta["FileName"],
                    "ZipPath": arcname,
                }
            )

        # Add index.csv for checking/upload reference.
        index_df = pd.DataFrame(index_rows)
        csv_bytes = index_df.to_csv(index=False).encode("utf-8-sig")
        z.writestr("index.csv", csv_bytes)

    buf.seek(0)
    return buf, sanitize_filename(zip_base_name) + ".zip", pd.DataFrame(index_rows)


# =========================================================
# ZIP upload engine
# =========================================================
def iter_pngs_from_zip(zip_bytes: bytes) -> List[Tuple[str, bytes, str, int, str]]:
    """
    Returns: (filename, bytes, code, qno, zip_path)
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if not info.filename.lower().endswith(".png"):
                continue
            base = Path(info.filename).name
            parsed = parse_problem_filename(base)
            if not parsed:
                continue
            code, qno = parsed
            out.append((base, z.read(info), code, qno, info.filename))
    out.sort(key=lambda x: (sort_code_key(x[2]), x[3]))
    return out


def upload_zip_to_drive_and_build_rows(
    zip_bytes: bytes,
    root_folder_id: str,
    overwrite: bool,
    progress_bar=None,
) -> List[Dict[str, Any]]:
    pngs = iter_pngs_from_zip(zip_bytes)
    rows: List[Dict[str, Any]] = []
    total = len(pngs)

    folder_cache: Dict[str, Dict[str, str]] = {}
    for idx, (filename, data, code, qno, _zip_path) in enumerate(pngs, start=1):
        meta = UNIT_META.get(code)
        if not meta:
            continue
        unit_name = meta["UnitName"]
        expected_count = meta["ExpectedCount"]
        fname = folder_display_name(code, unit_name)
        if code not in folder_cache:
            folder_cache[code] = get_or_create_child_folder(root_folder_id, fname)
        folder = folder_cache[code]
        folder_id = folder["id"]
        file_id = upload_or_update_png(folder_id, filename, data, overwrite=overwrite)
        rows.append(
            {
                "Code": code,
                "UnitName": unit_name,
                "QNo": qno,
                "FileName": filename,
                "FolderName": fname,
                "FolderLink": drive_link(folder_id),
                "DriveFileId": file_id,
                "ExpectedCount": expected_count,
                "Answer": "",
                "Notes": "",
            }
        )
        if progress_bar is not None:
            progress_bar.progress(idx / max(total, 1))

    rows.sort(key=lambda r: (sort_code_key(r["Code"]), int(r["QNo"])))
    list_png_files_in_folder.clear()
    return rows


# =========================================================
# Packet PDF engine
# =========================================================
def desired_pick_count(available_count: int, expected_count: Optional[int], small_max: int = 20, mid_max: int = 49) -> int:
    base = int(expected_count or available_count or 0)
    if base <= 0 or available_count <= 0:
        return 0
    if base <= small_max:
        k = 3
    elif base <= mid_max:
        k = 4
    else:
        k = 5
    return min(k, int(available_count))


def get_file_bytes_from_db_row(row: pd.Series) -> Optional[bytes]:
    file_id = str(row.get("DriveFileId", "")).strip()
    if file_id:
        return download_drive_file_bytes(file_id)

    folder_link = str(row.get("FolderLink", "")).strip()
    filename = str(row.get("FileName", "")).strip()
    if folder_link and filename:
        folder_id = extract_folder_id(folder_link)
        file_map = list_png_files_in_folder(folder_id)
        fid = file_map.get(filename)
        if fid:
            return download_drive_file_bytes(fid)
    return None


PacketItem = Tuple[str, str, str, str, bytes, str, bool]
# code, unit_name, q_label, filename, png_bytes, answer, show_category


def build_packet_items(
    df_db: pd.DataFrame,
    codes: List[str],
    small_max: int,
    mid_max: int,
    rng: random.Random,
) -> Tuple[List[PacketItem], List[str]]:
    items: List[PacketItem] = []
    warnings: List[str] = []

    for code in codes:
        subset = df_db[df_db["Code"] == code].copy()
        subset = subset[subset["FileName"].astype(str).str.strip() != ""]
        subset = subset.drop_duplicates(subset=["FileName"], keep="first")
        subset = subset.sort_values("QNo")

        unit_name = UNIT_META.get(code, {}).get("UnitName", code)
        expected = UNIT_META.get(code, {}).get("ExpectedCount", len(subset))
        available = len(subset)
        k = desired_pick_count(available, expected, small_max=small_max, mid_max=mid_max)
        if k <= 0:
            warnings.append(f"{code} {unit_name}: 가져올 수 있는 PNG가 없습니다.")
            continue

        chosen_indices = rng.sample(list(subset.index), k=k) if available > k else list(subset.index)
        chosen = subset.loc[chosen_indices].sort_values("QNo")

        first = True
        for _, row in chosen.iterrows():
            try:
                png_bytes = get_file_bytes_from_db_row(row)
            except Exception as e:
                warnings.append(f"{code} {unit_name} / {row.get('FileName','')}: 다운로드 실패 ({e})")
                continue
            if not png_bytes:
                warnings.append(f"{code} {unit_name} / {row.get('FileName','')}: 파일을 찾지 못했습니다.")
                continue
            qno = row.get("QNo", "")
            q_label = f"{code}-{qno}" if str(qno).strip() else str(row.get("FileName", ""))
            answer = "" if pd.isna(row.get("Answer", "")) else str(row.get("Answer", "")).strip()
            items.append((code, unit_name, q_label, str(row.get("FileName", "")), png_bytes, answer, first))
            first = False

    return items, warnings


def make_packet_pdf(title: str, items: List[PacketItem], page_size_name: str = "Letter") -> bytes:
    korean_ok = ensure_korean_font_registered()
    title_font = FONT_NAME if korean_ok else "Helvetica"
    body_font = FONT_NAME if korean_ok else "Helvetica"

    pagesize = A4 if page_size_name == "A4" else letter
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=pagesize)
    W, H = pagesize

    margin_x = 42
    margin_top = 52
    margin_bottom = 48
    title_font_size = 15
    body_font_size = 9

    title_gap = 30
    content_top = (H - margin_top) - title_gap
    category_h = 13
    category_gap = 5
    block_gap = 9
    min_scale_for_three = 0.43
    max_w = W - 2 * margin_x

    def draw_footer():
        c.setFont(body_font, 7)
        c.drawRightString(W - margin_x, 24, "YOU, GENIUS 유지니어스 MATH with 유진쌤")

    def compute_scale(iw: float, ih: float, slot_h: float, has_category_line: bool) -> float:
        extra_cat = category_h + category_gap if has_category_line else 0
        usable_h = slot_h - extra_cat - block_gap
        if usable_h <= 25:
            return 0.0
        return min(max_w / iw, usable_h / ih)

    # Problem pages
    idx = 0
    page_index = 0
    n_total = len(items)

    while idx < n_total:
        if page_index == 0:
            draw_fake_bold_string(c, margin_x, H - margin_top, title, title_font, title_font_size)

        avail_h = content_top - margin_bottom
        remaining = n_total - idx

        def can_fit(k: int) -> bool:
            if remaining < k:
                return False
            slot_h = avail_h / k
            scales = []
            for j in range(k):
                _code, _unit, _ql, _fn, png_bytes, _ans, show_cat = items[idx + j]
                img = ImageReader(io.BytesIO(png_bytes))
                iw, ih = img.getSize()
                scales.append(compute_scale(iw, ih, slot_h, show_cat))
            return bool(scales) and min(scales) >= min_scale_for_three

        if remaining >= 3 and can_fit(3):
            per_page = 3
            slot_h = avail_h / 3
        elif remaining >= 2:
            per_page = 2
            slot_h = avail_h / 2
        else:
            per_page = 1
            slot_h = avail_h

        c.setFont(body_font, body_font_size)

        for block_i in range(per_page):
            if idx >= n_total:
                break
            code, unit_name, q_label, _filename, png_bytes, _answer, show_cat = items[idx]
            slot_top = content_top - slot_h * block_i
            slot_bottom = content_top - slot_h * (block_i + 1)
            y = slot_top

            if show_cat:
                c.setFont(body_font, body_font_size)
                c.drawString(margin_x, y, f"Category: {code} {unit_name}")
                y -= category_h + category_gap

            img = ImageReader(io.BytesIO(png_bytes))
            iw, ih = img.getSize()
            usable_h = (y - slot_bottom) - block_gap
            if usable_h <= 25:
                break
            scale = min(max_w / iw, usable_h / ih)
            draw_w = iw * scale
            draw_h = ih * scale
            c.drawImage(img, margin_x, y - draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
            idx += 1

        draw_footer()
        c.showPage()
        page_index += 1

    # Answer Key
    draw_fake_bold_string(c, margin_x, H - margin_top, "Answer Key", title_font, 14)
    c.setFont(body_font, body_font_size)
    y = content_top
    line_h = 14
    current_cat = None

    for i, (code, unit_name, q_label, _filename, _png, answer, _show_cat) in enumerate(items, start=1):
        cat = f"{code} {unit_name}"
        if cat != current_cat:
            if y < margin_bottom + line_h * 3:
                draw_footer()
                c.showPage()
                c.setFont(body_font, body_font_size)
                y = content_top
            c.drawString(margin_x, y, f"[{cat}]")
            y -= line_h
            current_cat = cat

        if y < margin_bottom + line_h:
            draw_footer()
            c.showPage()
            c.setFont(body_font, body_font_size)
            y = content_top

        ans_text = answer if answer else ""
        c.drawString(margin_x, y, f"{i}) {q_label}    {ans_text}")
        y -= line_h

    draw_footer()
    c.save()
    return out.getvalue()


# =========================================================
# UI
# =========================================================
st.title(APP_TITLE)
st.caption(f"단원표 기준 총 {TOTAL_EXPECTED}문항 / {len(UNIT_ROWS)}개 단원")

with st.expander("단원 코드표 확인", expanded=False):
    st.dataframe(UNIT_DF, use_container_width=True, hide_index=True)

sheet_id = safe_sheet_id()

with st.sidebar:
    st.header("기본 설정")
    sheet_id = st.text_input("Google Sheet ID", value=sheet_id)
    st.caption("Sheet1 = DB, Sheet2 = 학생표(Class, StudentName, Needs)")
    if st.button("캐시 새로고침"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("캐시를 비웠어요.")


tab1, tab2, tab3, tab4 = st.tabs([
    "1️⃣ PDF → PNG ZIP",
    "2️⃣ ZIP → Drive/Sheet",
    "3️⃣ 보충 Packet 생성",
    "4️⃣ DB 점검",
])


# -------------------------
# TAB 1
# -------------------------
with tab1:
    st.subheader("1️⃣ PDF를 문제별 PNG ZIP으로 만들기")
    st.write("PDF에 있는 문제를 위에서 아래 순서대로 잘라서 `1.1-1.png`, `1.1-2.png`처럼 저장합니다.")

    pdf = st.file_uploader("문제은행 PDF 업로드", type=["pdf"], key="tab1_pdf")

    c1, c2, c3, c4 = st.columns(4)
    zoom = c1.slider("해상도 zoom", 2.0, 4.5, 3.0, 0.1)
    pad_top = c2.slider("위 여백", 0, 120, 10, 1)
    pad_bottom = c3.slider("아래 여백", 0, 180, 12, 1)
    last_extra = c4.slider("페이지 마지막 문제 추가 여백(px)", 0, 400, 30, 10)

    naming_mode = st.radio(
        "파일명 부여 방식",
        [
            "전체 1255문항: 표 순서대로 자동 이름 붙이기",
            "선택한 단원 하나로만 이름 붙이기",
            "CSV 매핑으로 이름 붙이기",
        ],
        horizontal=False,
    )

    selected_code = None
    mapping_file = None
    if naming_mode == "선택한 단원 하나로만 이름 붙이기":
        selected_label = st.selectbox("단원 선택", options=get_unit_options())
        selected_code = label_to_code(selected_label)
    elif naming_mode == "CSV 매핑으로 이름 붙이기":
        st.caption("CSV에는 최소 `FileName` 컬럼이 있어야 합니다. 예: 1.1-1.png, 1.1-2.png")
        mapping_file = st.file_uploader("매핑 CSV 업로드", type=["csv"], key="mapping_csv")

    cc1, cc2 = st.columns(2)
    put_unit_folders = cc1.checkbox("ZIP 안에 단원별 폴더 만들기", value=True)
    unify_width = cc2.checkbox("가로폭을 가장 넓은 문제에 맞춤", value=True)
    require_bracket = st.checkbox("문제 시작을 `번호. [Example...]` 패턴으로만 인식 (현재 문제은행 권장)", value=True)

    if pdf is not None and st.button("문제별 PNG ZIP 생성", type="primary"):
        try:
            pdf_bytes = pdf.read()
            with st.spinner("문제 위치를 찾고 이미지를 자르는 중..."):
                doc, rects = compute_rects_for_pdf(
                    pdf_bytes,
                    zoom=zoom,
                    pad_top=pad_top,
                    pad_bottom=pad_bottom,
                    last_question_extra_px=last_extra,
                    require_bracket_after_number=require_bracket,
                )

                if naming_mode == "전체 1255문항: 표 순서대로 자동 이름 붙이기":
                    plan = build_full_filename_plan()
                elif naming_mode == "선택한 단원 하나로만 이름 붙이기":
                    if not selected_code:
                        st.error("단원을 선택해줘.")
                        st.stop()
                    plan = build_single_unit_filename_plan(selected_code, len(rects))
                else:
                    if mapping_file is None:
                        st.error("CSV 매핑 파일을 올려줘.")
                        st.stop()
                    plan = read_mapping_csv(mapping_file)

                zip_base = Path(pdf.name).stem + "_unit_png"
                zbuf, zname, index_df = make_zip_from_rects(
                    doc,
                    rects,
                    plan,
                    zoom=zoom,
                    zip_base_name=zip_base,
                    put_unit_folders=put_unit_folders,
                    unify_width=unify_width,
                )

            detected = len(rects)
            expected = len(plan)
            if naming_mode == "전체 1255문항: 표 순서대로 자동 이름 붙이기":
                prefix_total = 0
                prefix_label = None
                for code, unit_name, cnt in UNIT_ROWS:
                    prefix_total += int(cnt)
                    if detected == prefix_total:
                        prefix_label = f"{code} {unit_name}까지"
                        break

                if detected == TOTAL_EXPECTED:
                    st.success(f"완료! 전체 문제 {detected}개를 생성했습니다.")
                elif prefix_label:
                    st.success(
                        f"완료! 감지된 {detected}개를 표 순서 기준으로 `{prefix_label}` 자동 이름 붙였습니다."
                    )
                else:
                    st.warning(
                        f"감지된 문제 수는 {detected}개입니다. 이 수가 단원표의 누적 문항수와 딱 맞지 않습니다. "
                        "문제 누락/추가 감지 가능성이 있으니 index.csv를 꼭 확인해줘."
                    )
            elif detected != expected:
                st.warning(
                    f"감지된 문제 수는 {detected}개, 파일명 계획은 {expected}개입니다. "
                    "순서가 맞는지 index.csv를 꼭 확인해줘."
                )
            else:
                st.success(f"완료! 문제 {detected}개를 생성했습니다.")

            st.download_button("ZIP 다운로드", data=zbuf.getvalue(), file_name=zname, mime="application/zip")
            st.dataframe(index_df.head(100), use_container_width=True, hide_index=True)
            st.caption("전체 목록은 ZIP 안의 index.csv에 들어 있습니다.")
        except Exception as e:
            st.error("생성 중 오류가 발생했어요.")
            st.code(str(e))


# -------------------------
# TAB 2
# -------------------------
with tab2:
    st.subheader("2️⃣ PNG ZIP을 Google Drive에 업로드하고 Sheet1 DB 만들기")
    st.write("Tab 1에서 만든 ZIP을 올리면, 단원별 Drive 폴더를 만들고 Sheet1에 DB를 기록합니다.")

    zip_upload = st.file_uploader("Tab 1에서 만든 PNG ZIP 업로드", type=["zip"], key="tab2_zip")
    root_folder_input = st.text_input("문제은행 Root Google Drive 폴더 링크 또는 ID", value="", key="root_folder")

    u1, u2 = st.columns(2)
    overwrite = u1.checkbox("Drive에 같은 이름 PNG가 있으면 덮어쓰기", value=True)
    replace_db = u2.checkbox("Sheet1 DB를 새로 덮어쓰기", value=True)

    if zip_upload is not None:
        try:
            preview_pngs = iter_pngs_from_zip(zip_upload.getvalue())
            st.info(f"ZIP 안에서 인식된 PNG: {len(preview_pngs)}개")
            if preview_pngs:
                preview_df = pd.DataFrame(
                    [{"FileName": x[0], "Code": x[2], "QNo": x[3], "ZipPath": x[4]} for x in preview_pngs[:50]]
                )
                st.dataframe(preview_df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error("ZIP 미리보기 중 오류가 발생했어요.")
            st.code(str(e))

    if st.button("Drive 업로드 + Sheet1 DB 작성", type="primary"):
        if zip_upload is None:
            st.error("ZIP 파일을 먼저 올려줘.")
            st.stop()
        if not root_folder_input.strip():
            st.error("Google Drive Root 폴더 링크 또는 ID를 입력해줘.")
            st.stop()
        try:
            root_folder_id = extract_folder_id(root_folder_input)
            progress = st.progress(0)
            with st.spinner("Drive에 업로드하고 Sheet1 DB를 만드는 중..."):
                rows = upload_zip_to_drive_and_build_rows(
                    zip_upload.getvalue(), root_folder_id=root_folder_id, overwrite=overwrite, progress_bar=progress
                )
                mode = "replace" if replace_db else "append"
                write_db_rows(sheet_id, rows, mode=mode)
                ensure_roster_headers(sheet_id)

            st.success(f"완료! {len(rows)}개 PNG를 DB에 기록했습니다.")
            st.dataframe(pd.DataFrame(rows).head(100), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error("업로드/DB 작성 중 오류가 발생했어요.")
            st.code(str(e))


# -------------------------
# TAB 3
# -------------------------
with tab3:
    st.subheader("3️⃣ 학생별 보충 Packet 생성")

    p1, p2, p3 = st.columns(3)
    small_max = p1.number_input("문항수 ≤ 이 값이면 3개", min_value=1, max_value=100, value=20, step=1)
    mid_max = p2.number_input("문항수 ≤ 이 값이면 4개", min_value=int(small_max), max_value=150, value=max(49, int(small_max)), step=1)
    page_size_name = p3.selectbox("PDF 용지", ["Letter", "A4"], index=0)

    seed_text = st.text_input("랜덤 seed 선택사항: 같은 seed를 쓰면 같은 문제 조합", value="")
    rng = random.Random(seed_text if seed_text.strip() else None)

    try:
        df_db = load_db_from_sheet(sheet_id)
    except Exception as e:
        st.error("Sheet1 DB를 읽는 중 오류가 발생했어요.")
        st.code(str(e))
        st.stop()

    if df_db.empty:
        st.warning("Sheet1 DB가 비어 있어요. 먼저 Tab 2에서 DB를 만들어줘.")
        st.stop()

    mode = st.radio("생성 모드", ["학생 1명 직접 생성", "Sheet2 여러 학생 ZIP 생성"], horizontal=True)

    if mode == "학생 1명 직접 생성":
        col1, col2 = st.columns(2)
        student_name = col1.text_input("학생 이름", value="")
        class_name = col2.text_input("반/Class", value="")

        selected_labels = st.multiselect("부족 단원 선택", options=get_unit_options())
        selected_codes = [label_to_code(x) for x in selected_labels]
        selected_codes = [c for c in selected_codes if c]

        if st.button("PDF 생성", type="primary"):
            if not student_name.strip() or not class_name.strip():
                st.error("학생 이름과 반을 입력해줘.")
                st.stop()
            if not selected_codes:
                st.error("부족 단원을 최소 1개 선택해줘.")
                st.stop()

            with st.spinner("Drive에서 문제 이미지를 가져와 PDF를 만드는 중..."):
                items, warns = build_packet_items(df_db, selected_codes, int(small_max), int(mid_max), rng)
                if not items:
                    st.error("PDF에 넣을 문제가 없습니다. DB/Drive 권한/파일명을 확인해줘.")
                    if warns:
                        st.code("\n".join(warns))
                    st.stop()
                title = sanitize_filename(f"{student_name}_{class_name}_보충 PACKET")
                pdf_bytes = make_packet_pdf(title, items, page_size_name=page_size_name)

            st.success(f"완료! 총 {len(items)}문제")
            if warns:
                with st.expander("경고/누락 확인"):
                    st.write("\n".join(warns))
            st.download_button("PDF 다운로드", data=pdf_bytes, file_name=f"{title}.pdf", mime="application/pdf")

    else:
        try:
            df_roster = load_roster_from_sheet(sheet_id)
        except Exception as e:
            st.error("Sheet2 학생표를 읽는 중 오류가 발생했어요.")
            st.code(str(e))
            st.stop()

        if df_roster.empty:
            st.warning("Sheet2에 학생표가 비어 있어요. 헤더는 Class, StudentName, Needs 입니다.")
            st.stop()

        classes = sorted([c for c in df_roster["Class"].dropna().unique().tolist() if str(c).strip()])
        selected_class = st.selectbox("Class 필터", options=["(전체)"] + classes)
        df_view = df_roster.copy()
        if selected_class != "(전체)":
            df_view = df_view[df_view["Class"] == selected_class]

        max_students = st.number_input(
            "한 번에 생성할 최대 학생 수", min_value=1, max_value=500, value=min(30, max(1, len(df_view))), step=1
        )
        df_view = df_view.head(int(max_students))
        st.write(f"대상 학생 수: {len(df_view)}")
        st.dataframe(df_view, use_container_width=True, hide_index=True)

        if st.button("여러 학생 PDF ZIP 생성", type="primary"):
            if df_view.empty:
                st.error("대상 학생이 없습니다.")
                st.stop()

            zip_buf = io.BytesIO()
            all_warnings: List[str] = []
            progress = st.progress(0)

            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for idx, (_, r) in enumerate(df_view.iterrows(), start=1):
                    cls = str(r.get("Class", "")).strip()
                    name = str(r.get("StudentName", "")).strip()
                    needs_text = str(r.get("Needs", "")).strip()
                    codes = parse_needs(needs_text)
                    if not cls or not name or not codes:
                        all_warnings.append(f"{name or '(이름없음)'}: Class/StudentName/Needs 누락")
                        progress.progress(idx / len(df_view))
                        continue

                    student_seed = f"{seed_text}-{cls}-{name}" if seed_text.strip() else None
                    student_rng = random.Random(student_seed)
                    items, warns = build_packet_items(df_db, codes, int(small_max), int(mid_max), student_rng)
                    all_warnings.extend([f"{name}: {w}" for w in warns])
                    if not items:
                        progress.progress(idx / len(df_view))
                        continue

                    title = sanitize_filename(f"{name}_{cls}_보충 PACKET")
                    pdf_bytes = make_packet_pdf(title, items, page_size_name=page_size_name)
                    zf.writestr(f"{title}.pdf", pdf_bytes)
                    progress.progress(idx / len(df_view))

                if all_warnings:
                    zf.writestr("warnings.txt", "\n".join(all_warnings).encode("utf-8-sig"))

            zip_buf.seek(0)
            zip_name = sanitize_filename(f"{selected_class if selected_class != '(전체)' else 'ALL'}_PACKETS.zip")
            st.success("배치 생성 완료!")
            if all_warnings:
                with st.expander("경고/누락 확인"):
                    st.write("\n".join(all_warnings[:200]))
                    if len(all_warnings) > 200:
                        st.caption(f"나머지 {len(all_warnings)-200}개 경고는 ZIP 안의 warnings.txt에서 확인하세요.")
            st.download_button("ZIP 다운로드", data=zip_buf.getvalue(), file_name=zip_name, mime="application/zip")


# -------------------------
# TAB 4
# -------------------------
with tab4:
    st.subheader("4️⃣ DB 점검")
    st.write("Sheet1에 기록된 파일 수가 단원표의 문항수와 맞는지 확인합니다.")

    if st.button("DB 다시 불러오기"):
        load_db_from_sheet.clear()

    try:
        df_db_check = load_db_from_sheet(sheet_id)
    except Exception as e:
        st.error("Sheet1 DB를 읽는 중 오류가 발생했어요.")
        st.code(str(e))
        st.stop()

    if df_db_check.empty:
        st.warning("DB가 비어 있습니다.")
    else:
        actual = df_db_check.groupby("Code").size().reset_index(name="UploadedCount")
        check = UNIT_DF.merge(actual, on="Code", how="left")
        check["UploadedCount"] = check["UploadedCount"].fillna(0).astype(int)
        check["Missing"] = check["ExpectedCount"] - check["UploadedCount"]
        check["Status"] = check["Missing"].apply(lambda x: "OK" if x == 0 else ("부족" if x > 0 else "초과"))
        st.metric("DB 총 PNG 수", int(check["UploadedCount"].sum()))
        st.metric("표 기준 총 문항수", TOTAL_EXPECTED)
        st.dataframe(check, use_container_width=True, hide_index=True)

        bad = check[check["Status"] != "OK"]
        if not bad.empty:
            st.warning("문항수가 맞지 않는 단원이 있습니다.")
            st.dataframe(bad, use_container_width=True, hide_index=True)

st.caption("파일명 규칙: 단원코드-문제번호.png  예) 1.1-5.png")
