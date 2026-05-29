# sat_math_problem_bank_app.py
# =========================================================
# YOU, GENIUS SAT MATH Problem Bank App
# 1) PDF -> unit-code PNG ZIP
# 2) Student remedial packet PDF generation
#    - No automatic ZIP upload tab. Upload images to Drive manually, then Sheet1 DB is read-only.
# =========================================================

import io
import re
import csv
import random
import zipfile
import hmac
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

APP_TITLE = "YOU, GENIUS SAT MATH 보충 PACKET"
APP_ACCESS_CODE_SECRET_KEY = "access_code"
DEFAULT_ACCESS_CODE = "110729"
DEFAULT_SHEET_ID = "1qJSe8HX6mAQ8nKnnhfzz2PT-ycjM7W6kBsK6n9xZ3pY"
DB_SHEET_NAME = "Sheet1"
ROSTER_SHEET_NAME = "Sheet2"

FONT_NAME = "NotoSansKR"
FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansKR-VariableFont_wght.ttf"

FOLDER_MIME = "application/vnd.google-apps.folder"
PNG_MIME = "image/png"

DB_HEADERS = [
    "MajorCode",      # 대단원 코드: 1, 2, 3, ...
    "MajorName",      # 대단원명: Linear, Geometry, ...
    "Code",           # 단원코드: 1.1, 1.2, ...
    "UnitName",       # 단원명
    "QNo",            # 단원 안 문제번호
    "FileName",       # 예: 1.1-5.png
    "FolderName",     # 예: 1.1 Linear function
    "FolderLink",     # 해당 단원 Drive 폴더 링크
    "DriveFileId",    # 선택: 개별 PNG 파일 ID
    "ExpectedCount",  # 표 기준 단원별 전체 문항수
    "Answer",         # 선택: 정답
    "Notes",          # 선택: 메모
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

MAJOR_ROWS: List[Tuple[str, str]] = [
    ("1", "Linear"),
    ("2", "Percent & Unit conversion"),
    ("3", "Quadratic"),
    ("4", "Exponential"),
    ("5", "Polynomials, radical and rational functions"),
    ("6", "Geometry"),
    ("7", "Statistics"),
]
MAJOR_META: Dict[str, str] = {code: name for code, name in MAJOR_ROWS}


def major_code_from_unit_code(code: str) -> str:
    return str(code).strip().split(".")[0] if str(code).strip() else ""


def major_name_from_unit_code(code: str) -> str:
    return MAJOR_META.get(major_code_from_unit_code(code), "")


UNIT_DF = pd.DataFrame(UNIT_ROWS, columns=["Code", "UnitName", "ExpectedCount"])
UNIT_DF["MajorCode"] = UNIT_DF["Code"].map(major_code_from_unit_code)
UNIT_DF["MajorName"] = UNIT_DF["Code"].map(major_name_from_unit_code)
UNIT_DF = UNIT_DF[["MajorCode", "MajorName", "Code", "UnitName", "ExpectedCount"]]

UNIT_META: Dict[str, Dict[str, Any]] = {
    row.Code: {
        "MajorCode": row.MajorCode,
        "MajorName": row.MajorName,
        "UnitName": row.UnitName,
        "ExpectedCount": int(row.ExpectedCount),
    }
    for row in UNIT_DF.itertuples()
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
# In this problem-bank PDF, real problem anchors look like:
#   1. [Example ...]
#   9. [개념반 Example ...]
#   45. [중급반 Example ...]
# This prevents false cuts at unit headings like "1. Linear",
# numbered choices like "1.", "2.", and wrapped text like "8. What ...".
QUESTION_ANCHOR_HINT_RE = re.compile(r"\[.*?Example", re.IGNORECASE)
CHOICE_LABELS = ["D)", "C)", "B)", "A)", "(D)", "(C)", "(B)", "(A)"]

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


def get_access_code() -> str:
    """Read the entry code from Streamlit Secrets if provided.

    Optional Streamlit Secrets override:
        access_code = "110729"

    If no access_code is set in Secrets, the app uses DEFAULT_ACCESS_CODE.
    """
    try:
        code = str(st.secrets.get(APP_ACCESS_CODE_SECRET_KEY, DEFAULT_ACCESS_CODE)).strip()
    except Exception:
        code = DEFAULT_ACCESS_CODE
    return code


def require_access_code() -> None:
    """Stop the app until the correct 6-digit entry code is entered."""
    if st.session_state.get("access_code_ok", False):
        return

    st.title(APP_TITLE)
    st.subheader("🔐 입장 코드 입력")
    st.write("패킷을 만들려면 먼저 6자리 입장 코드를 입력해줘.")

    entered_code = st.text_input(
        "입장 코드",
        type="password",
        max_chars=6,
        placeholder="6자리 숫자",
        key="packet_access_code_input",
    )
    submit = st.button("입장", type="primary")

    if submit:
        expected_code = get_access_code()
        if hmac.compare_digest(str(entered_code).strip(), expected_code):
            st.session_state["access_code_ok"] = True
            st.rerun()
        else:
            st.error("입장 코드가 맞지 않아요.")

    st.stop()


def logout_button() -> None:
    if st.sidebar.button("나가기 / 코드 초기화"):
        st.session_state["access_code_ok"] = False
        st.rerun()


def folder_display_name(code: str, unit_name: str) -> str:
    return sanitize_filename(f"{code} {unit_name}")


def unit_record_for_code(code: str) -> Dict[str, Any]:
    """Standard row metadata based on the fixed problem classification table."""
    meta = UNIT_META.get(code, {})
    unit_name = meta.get("UnitName", "")
    return {
        "MajorCode": meta.get("MajorCode", major_code_from_unit_code(code)),
        "MajorName": meta.get("MajorName", major_name_from_unit_code(code)),
        "Code": code,
        "UnitName": unit_name,
        "FolderName": folder_display_name(code, unit_name),
        "ExpectedCount": meta.get("ExpectedCount", ""),
    }


def parse_code_token(token: str) -> Optional[str]:
    s = str(token).strip().replace("_", ".").replace("-", ".")
    # Accept both "1.1" and labels like "1.1 Linear function".
    m = re.search(r"(\d{1,2})\s*\.\s*(\d{1,2})", s)
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
    return [
        f"{r.Code}  |  {r.MajorName} > {r.UnitName}  ({int(r.ExpectedCount)}문항)"
        for r in UNIT_DF.itertuples()
    ]


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
    """
    Normalize the Google Sheet database so it matches the fixed problem classification.

    Recommended Sheet1 headers:
      MajorCode, MajorName, Code, UnitName, QNo, FileName, FolderName,
      FolderLink, DriveFileId, ExpectedCount, Answer, Notes

    Also accepts Korean aliases such as:
      대단원코드, 대단원명, 단원코드, 단원명, 문항번호, 파일명,
      폴더명, 폴더링크, 예상문항수, 정답, 메모

    Legacy schema is still supported:
      Major, Minor, MajorFolder, MinorFolder, FolderLink, FileName, Answer
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    aliases = {
        "대단원코드": "MajorCode",
        "대단원명": "MajorName",
        "대분류코드": "MajorCode",
        "대분류명": "MajorName",
        "CategoryCode": "MajorCode",
        "CategoryName": "MajorName",
        "Major Name": "MajorName",
        "단원코드": "Code",
        "UnitCode": "Code",
        "Unit Code": "Code",
        "코드": "Code",
        "단원명": "UnitName",
        "Unit Name": "UnitName",
        "문항번호": "QNo",
        "문제번호": "QNo",
        "QuestionNo": "QNo",
        "Question No": "QNo",
        "No": "QNo",
        "파일명": "FileName",
        "File": "FileName",
        "ImageName": "FileName",
        "Image Name": "FileName",
        "폴더명": "FolderName",
        "Folder Name": "FolderName",
        "폴더링크": "FolderLink",
        "Folder Link": "FolderLink",
        "DriveFolderLink": "FolderLink",
        "Drive Folder Link": "FolderLink",
        "Drive File ID": "DriveFileId",
        "DriveFileID": "DriveFileId",
        "파일ID": "DriveFileId",
        "예상문항수": "ExpectedCount",
        "문항수": "ExpectedCount",
        "Expected Count": "ExpectedCount",
        "정답": "Answer",
        "답": "Answer",
        "메모": "Notes",
        "비고": "Notes",
    }

    # Rename aliases safely. If two columns map to the same canonical name, keep the first.
    new_cols = []
    seen = set()
    for col in df.columns:
        canonical = aliases.get(col, col)
        if canonical in seen:
            canonical = col
        new_cols.append(canonical)
        seen.add(canonical)
    df.columns = new_cols

    def clean_text(v: Any) -> str:
        s = str(v or "").strip()
        return "" if s.lower() in {"nan", "none", "<na>"} else s

    def normalize_major_code(raw: Any, unit_code: str = "") -> str:
        s = clean_text(raw)
        roman_map = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7"}
        m = re.match(r"^(\d{1,2})", s)
        if m:
            return str(int(m.group(1)))
        m = re.match(r"^([IVX]+)\b", s.upper())
        if m and m.group(1) in roman_map:
            return roman_map[m.group(1)]
        return major_code_from_unit_code(unit_code)

    # Build Code from Code / legacy Major+Minor / FileName.
    if "Code" in df.columns:
        df["Code"] = df["Code"].apply(lambda x: parse_code_token(x) or "")
    elif {"Major", "Minor"}.issubset(df.columns):
        def mm_to_code(r):
            try:
                if clean_text(r.get("Major", "")) and clean_text(r.get("Minor", "")):
                    return f"{int(float(r['Major']))}.{int(float(r['Minor']))}"
            except Exception:
                return ""
            return ""
        df["Code"] = df.apply(mm_to_code, axis=1)
    elif "FileName" in df.columns:
        df["Code"] = df["FileName"].apply(lambda x: parse_problem_filename(x)[0] if parse_problem_filename(x) else "")
    else:
        df["Code"] = ""

    # FileName can be omitted if Code + QNo exist; otherwise it can drive Code/QNo.
    if "FileName" not in df.columns:
        if "QNo" in df.columns:
            df["FileName"] = df.apply(
                lambda r: f"{r.get('Code','')}-{int(float(r.get('QNo', 0)))}.png"
                if clean_text(r.get("Code", "")) and clean_text(r.get("QNo", ""))
                else "",
                axis=1,
            )
        else:
            df["FileName"] = ""
    df["FileName"] = df["FileName"].apply(ensure_png_filename)

    if "QNo" not in df.columns:
        df["QNo"] = df["FileName"].apply(lambda x: parse_problem_filename(x)[1] if parse_problem_filename(x) else None)
    else:
        def fill_qno(r):
            q = clean_text(r.get("QNo", ""))
            if q:
                return q
            parsed = parse_problem_filename(r.get("FileName", ""))
            return parsed[1] if parsed else None
        df["QNo"] = df.apply(fill_qno, axis=1)

    # Fill unit/major metadata from the fixed 1.1–7.9 classification table.
    if "UnitName" not in df.columns:
        if "MinorFolder" in df.columns:
            df["UnitName"] = df["MinorFolder"].astype(str)
        else:
            df["UnitName"] = ""
    df["UnitName"] = df.apply(
        lambda r: clean_text(r.get("UnitName", "")) or UNIT_META.get(r.get("Code", ""), {}).get("UnitName", ""),
        axis=1,
    )

    if "MajorCode" not in df.columns:
        df["MajorCode"] = ""
    df["MajorCode"] = df.apply(lambda r: normalize_major_code(r.get("MajorCode", ""), r.get("Code", "")), axis=1)

    if "MajorName" not in df.columns:
        if "MajorFolder" in df.columns:
            df["MajorName"] = df["MajorFolder"].astype(str)
        else:
            df["MajorName"] = ""
    df["MajorName"] = df.apply(
        lambda r: clean_text(r.get("MajorName", "")) or MAJOR_META.get(clean_text(r.get("MajorCode", "")), "") or major_name_from_unit_code(r.get("Code", "")),
        axis=1,
    )

    if "ExpectedCount" not in df.columns:
        df["ExpectedCount"] = ""
    df["ExpectedCount"] = df.apply(
        lambda r: clean_text(r.get("ExpectedCount", "")) or UNIT_META.get(r.get("Code", ""), {}).get("ExpectedCount", ""),
        axis=1,
    )

    if "FolderName" not in df.columns:
        if "MinorFolder" in df.columns:
            df["FolderName"] = df["MinorFolder"].astype(str)
        else:
            df["FolderName"] = ""
    df["FolderName"] = df.apply(
        lambda r: clean_text(r.get("FolderName", "")) or folder_display_name(r.get("Code", ""), r.get("UnitName", "")),
        axis=1,
    )

    for c in DB_HEADERS:
        if c not in df.columns:
            df[c] = ""

    df = df[DB_HEADERS].copy()
    df["Code"] = df["Code"].astype(str).str.strip()
    df["MajorCode"] = df["MajorCode"].astype(str).str.strip()
    df["QNo"] = pd.to_numeric(df["QNo"], errors="coerce").astype("Int64")
    df["ExpectedCount"] = pd.to_numeric(df["ExpectedCount"], errors="coerce").astype("Int64")
    df["FileName"] = df["FileName"].astype(str).apply(ensure_png_filename)

    for c in ["MajorName", "UnitName", "FolderName", "FolderLink", "DriveFileId", "Answer", "Notes"]:
        df[c] = df[c].astype(str).replace({"nan": "", "None": "", "<NA>": ""})

    df = df[df["Code"].isin(UNIT_META.keys())]
    df = df.sort_values(
        ["Code", "QNo"],
        key=lambda col: col.map(sort_code_key) if col.name == "Code" else col,
        na_position="last",
    )
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


def detect_question_anchors(
    page,
    left_ratio=0.28,
    max_line_chars=8,
    allow_inline_question_text=True,
    require_example_anchor=True,
):
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

        # For the unit-code problem-bank PDFs, require the actual problem-start pattern.
        # Without this, the detector mistakes unit titles ("1. Linear"),
        # numbered answer choices ("1.", "2."), and wrapped text ("8. What ...")
        # for separate questions.
        if require_example_anchor and not QUESTION_ANCHOR_HINT_RE.search(line_text):
            continue

        x_left = min(t[0] for t in tokens)
        y_top = min(t[1] for t in tokens)
        y_bottom = max(t[3] for t in tokens)
        if x_left > w_page * left_ratio:
            continue
        if y_top > h_page * 0.96:
            continue

        qnum = None

        # Case 1: first token is "125."
        m = NUMDOT_RE.match(tokens[0][4])
        if m:
            qnum = int(m.group(1))

        # Case 2: first two tokens are "125" "."
        if qnum is None and len(tokens) >= 2 and NUM_RE.match(tokens[0][4]) and tokens[1][4] == ".":
            qnum = int(tokens[0][4])

        # Case 3: old strict mode where line is almost only "n."
        if qnum is None and len(compact) <= max_line_chars:
            for (x0, y0, x1, y1, txt) in tokens:
                m = NUMDOT_RE.match(txt)
                if m:
                    qnum = int(m.group(1))
                    y_top = y0
                    y_bottom = y1
                    break

        if qnum is None:
            continue

        if not allow_inline_question_text and len(compact) > max_line_chars:
            continue

        # Save both the top and bottom of the number/anchor line.
        # The crop engine can start from the NEXT line so the printed number
        # itself is not included in the final PNG.
        anchors.append((qnum, y_top, y_bottom))

    # Deduplicate anchors very close to each other
    anchors.sort(key=lambda t: t[1])
    deduped = []
    for q, y_top, y_bottom in anchors:
        if deduped and abs(y_top - deduped[-1][1]) < 3:
            continue
        deduped.append((q, y_top, y_bottom))
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
    allow_inline_question_text: bool = True,
    require_example_anchor: bool = True,
    exclude_question_number_line: bool = True,
    number_line_offset: int = 2,
    protect_graph_table_bottom: bool = True,
    ink_bottom_extra: int = 8,
):
    """Find crop rectangles for each problem.

    Important for this problem-bank PDF:
    - Real problem starts are detected by the number + [Example] line.
    - If exclude_question_number_line=True, the saved PNG starts below that line.
    - Some choices are not A/B/C/D text; they are Roman-numeral table/graph choices
      such as I, II, III, IV. Those lower tables can be vector drawings, so text-only
      bottom detection may cut them off. protect_graph_table_bottom scans the whole
      candidate band by raster ink before any text-based shrinking, then keeps the
      bottom at least as low as the lowest ink.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    rects = []
    side_pad_pt = SIDE_PAD_PX / zoom
    last_extra_pt = last_question_extra_px / zoom
    ink_extra_pt = ink_bottom_extra / zoom

    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.rect.width, page.rect.height
        anchors = detect_question_anchors(
            page,
            allow_inline_question_text=allow_inline_question_text,
            require_example_anchor=require_example_anchor,
        )
        if not anchors:
            continue

        for i, (printed_qnum, y0, y0_bottom) in enumerate(anchors):
            if exclude_question_number_line:
                # Start the crop BELOW the number/anchor line so the printed
                # question number is excluded from the saved PNG.
                y_start = clamp(y0_bottom + number_line_offset, 0, h)
            else:
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

            # Rescue bottom for graphics/tables before text-based shrinking.
            # This is the key fix for table-choice problems: I/II/III/IV table grids
            # may not appear as text blocks, but they are visible ink.
            ink_bottom_y = None
            ink_bounds_rect = None
            if protect_graph_table_bottom and y_cap > y_start + 60:
                full_scan_clip = fitz.Rect(0, y_start, w, y_cap)
                full_px_bbox = ink_bbox_by_raster(page, full_scan_clip)
                if full_px_bbox is not None:
                    ink_bounds_rect = px_bbox_to_page_rect(full_scan_clip, full_px_bbox, pad_px=INK_PAD_PX)
                    ink_bottom_y = clamp(ink_bounds_rect.y1 + ink_extra_pt, y_start + 60, y_cap)

            mcq_last = last_choice_bottom_y_in_band(page, y_start, y_cap)
            if mcq_last is not None:
                y_end = clamp(max(y_end, mcq_last + 18), y_start + 60, y_cap)

            bottom = content_bottom_y(page, y_start, y_end)
            if bottom is not None and bottom > y_start + 80:
                if mcq_last is not None:
                    bottom = max(bottom, mcq_last + 10)
                text_based_end = bottom + 14
                if ink_bottom_y is not None:
                    # Never shrink above the lowest visible graph/table ink.
                    y_end = min(y_end, max(text_based_end, ink_bottom_y))
                else:
                    y_end = min(y_end, text_based_end)

            if ink_bottom_y is not None:
                y_end = clamp(max(y_end, ink_bottom_y), y_start + 60, y_cap)

            # Last question on a page often needs a little more blank space below.
            if i + 1 == len(anchors):
                y_end = min(y_cap, y_end + last_extra_pt)

            xb = text_x_bounds_in_band(page, y_start, y_end, min_len=1)
            if xb is None:
                x0, x1 = 0, w
            else:
                x0 = clamp(xb[0] - side_pad_pt, 0, w)
                x1 = clamp(xb[1] + side_pad_pt, x0 + 80, w)

            # Final tight crop over the final vertical band. This removes excess
            # blank space but now the band already includes lower graph/table ink.
            scan_clip = fitz.Rect(0, y_start, w, y_end)
            px_bbox = ink_bbox_by_raster(page, scan_clip)
            if px_bbox is not None:
                tight = px_bbox_to_page_rect(scan_clip, px_bbox, pad_px=INK_PAD_PX)
                x0 = clamp(tight.x0, 0, w)
                x1 = clamp(tight.x1, x0 + 80, w)
                new_y_end = clamp(tight.y1, y_start + 60, y_end)
                if mcq_last is not None:
                    new_y_end = max(new_y_end, mcq_last + 12)
                if ink_bottom_y is not None:
                    new_y_end = max(new_y_end, ink_bottom_y)
                y_end = clamp(new_y_end, y_start + 60, y_cap)

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
            meta = unit_record_for_code(code)
            plan.append(
                {
                    "order": order,
                    **meta,
                    "QNo": qno,
                    "FileName": f"{code}-{qno}.png",
                }
            )
            order += 1
    return plan


def build_single_unit_filename_plan(code: str, count: int) -> List[Dict[str, Any]]:
    meta = unit_record_for_code(code)
    return [
        {
            "order": i,
            **meta,
            "QNo": i,
            "FileName": f"{code}-{i}.png",
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
        meta = unit_record_for_code(code)
        plan.append(
            {
                "order": int(row.get("order") or row.get("Order") or idx),
                **meta,
                "QNo": qno,
                "FileName": f"{code}-{qno}.png",
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
                    "MajorCode": "",
                    "MajorName": "",
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
                    "MajorCode": meta.get("MajorCode", ""),
                    "MajorName": meta.get("MajorName", ""),
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
        major_code = meta.get("MajorCode", major_code_from_unit_code(code))
        major_name = meta.get("MajorName", major_name_from_unit_code(code))
        fname = folder_display_name(code, unit_name)
        if code not in folder_cache:
            folder_cache[code] = get_or_create_child_folder(root_folder_id, fname)
        folder = folder_cache[code]
        folder_id = folder["id"]
        file_id = upload_or_update_png(folder_id, filename, data, overwrite=overwrite)
        rows.append(
            {
                "MajorCode": major_code,
                "MajorName": major_name,
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
# Overrides for manual Drive upload + direct remedial packet generation
# =========================================================
def ensure_png_filename(value: str) -> str:
    """Normalize FileName values such as '1.1-5' -> '1.1-5.png'."""
    s = str(value or "").strip()
    if not s or s.lower() in {"nan", "none"}:
        return ""
    base = Path(s).name
    if base.lower().endswith(".png"):
        return base
    parsed = parse_problem_filename(base)
    if parsed:
        code, qno = parsed
        return f"{code}-{qno}.png"
    # If the user typed only a base name, still append .png.
    if re.match(r"^\d{1,2}\.\d{1,2}\s*[-_]\s*\d{1,4}$", base):
        return base.replace("_", "-") + ".png"
    return base


def normalize_db(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize the Google Sheet database so it matches the fixed problem classification.

    Recommended Sheet1 headers:
      MajorCode, MajorName, Code, UnitName, QNo, FileName, FolderName,
      FolderLink, DriveFileId, ExpectedCount, Answer, Notes

    Also accepts Korean aliases such as:
      대단원코드, 대단원명, 단원코드, 단원명, 문항번호, 파일명,
      폴더명, 폴더링크, 예상문항수, 정답, 메모

    Legacy schema is still supported:
      Major, Minor, MajorFolder, MinorFolder, FolderLink, FileName, Answer
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    aliases = {
        "대단원코드": "MajorCode",
        "대단원명": "MajorName",
        "대분류코드": "MajorCode",
        "대분류명": "MajorName",
        "CategoryCode": "MajorCode",
        "CategoryName": "MajorName",
        "Major Name": "MajorName",
        "단원코드": "Code",
        "UnitCode": "Code",
        "Unit Code": "Code",
        "코드": "Code",
        "단원명": "UnitName",
        "Unit Name": "UnitName",
        "문항번호": "QNo",
        "문제번호": "QNo",
        "QuestionNo": "QNo",
        "Question No": "QNo",
        "No": "QNo",
        "파일명": "FileName",
        "File": "FileName",
        "ImageName": "FileName",
        "Image Name": "FileName",
        "폴더명": "FolderName",
        "Folder Name": "FolderName",
        "폴더링크": "FolderLink",
        "Folder Link": "FolderLink",
        "DriveFolderLink": "FolderLink",
        "Drive Folder Link": "FolderLink",
        "Drive File ID": "DriveFileId",
        "DriveFileID": "DriveFileId",
        "파일ID": "DriveFileId",
        "예상문항수": "ExpectedCount",
        "문항수": "ExpectedCount",
        "Expected Count": "ExpectedCount",
        "정답": "Answer",
        "답": "Answer",
        "메모": "Notes",
        "비고": "Notes",
    }

    # Rename aliases safely. If two columns map to the same canonical name, keep the first.
    new_cols = []
    seen = set()
    for col in df.columns:
        canonical = aliases.get(col, col)
        if canonical in seen:
            canonical = col
        new_cols.append(canonical)
        seen.add(canonical)
    df.columns = new_cols

    def clean_text(v: Any) -> str:
        s = str(v or "").strip()
        return "" if s.lower() in {"nan", "none", "<na>"} else s

    def normalize_major_code(raw: Any, unit_code: str = "") -> str:
        s = clean_text(raw)
        roman_map = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7"}
        m = re.match(r"^(\d{1,2})", s)
        if m:
            return str(int(m.group(1)))
        m = re.match(r"^([IVX]+)\b", s.upper())
        if m and m.group(1) in roman_map:
            return roman_map[m.group(1)]
        return major_code_from_unit_code(unit_code)

    # Build Code from Code / legacy Major+Minor / FileName.
    if "Code" in df.columns:
        df["Code"] = df["Code"].apply(lambda x: parse_code_token(x) or "")
    elif {"Major", "Minor"}.issubset(df.columns):
        def mm_to_code(r):
            try:
                if clean_text(r.get("Major", "")) and clean_text(r.get("Minor", "")):
                    return f"{int(float(r['Major']))}.{int(float(r['Minor']))}"
            except Exception:
                return ""
            return ""
        df["Code"] = df.apply(mm_to_code, axis=1)
    elif "FileName" in df.columns:
        df["Code"] = df["FileName"].apply(lambda x: parse_problem_filename(x)[0] if parse_problem_filename(x) else "")
    else:
        df["Code"] = ""

    # FileName can be omitted if Code + QNo exist; otherwise it can drive Code/QNo.
    if "FileName" not in df.columns:
        if "QNo" in df.columns:
            df["FileName"] = df.apply(
                lambda r: f"{r.get('Code','')}-{int(float(r.get('QNo', 0)))}.png"
                if clean_text(r.get("Code", "")) and clean_text(r.get("QNo", ""))
                else "",
                axis=1,
            )
        else:
            df["FileName"] = ""
    df["FileName"] = df["FileName"].apply(ensure_png_filename)

    if "QNo" not in df.columns:
        df["QNo"] = df["FileName"].apply(lambda x: parse_problem_filename(x)[1] if parse_problem_filename(x) else None)
    else:
        def fill_qno(r):
            q = clean_text(r.get("QNo", ""))
            if q:
                return q
            parsed = parse_problem_filename(r.get("FileName", ""))
            return parsed[1] if parsed else None
        df["QNo"] = df.apply(fill_qno, axis=1)

    # Fill unit/major metadata from the fixed 1.1–7.9 classification table.
    if "UnitName" not in df.columns:
        if "MinorFolder" in df.columns:
            df["UnitName"] = df["MinorFolder"].astype(str)
        else:
            df["UnitName"] = ""
    df["UnitName"] = df.apply(
        lambda r: clean_text(r.get("UnitName", "")) or UNIT_META.get(r.get("Code", ""), {}).get("UnitName", ""),
        axis=1,
    )

    if "MajorCode" not in df.columns:
        df["MajorCode"] = ""
    df["MajorCode"] = df.apply(lambda r: normalize_major_code(r.get("MajorCode", ""), r.get("Code", "")), axis=1)

    if "MajorName" not in df.columns:
        if "MajorFolder" in df.columns:
            df["MajorName"] = df["MajorFolder"].astype(str)
        else:
            df["MajorName"] = ""
    df["MajorName"] = df.apply(
        lambda r: clean_text(r.get("MajorName", "")) or MAJOR_META.get(clean_text(r.get("MajorCode", "")), "") or major_name_from_unit_code(r.get("Code", "")),
        axis=1,
    )

    if "ExpectedCount" not in df.columns:
        df["ExpectedCount"] = ""
    df["ExpectedCount"] = df.apply(
        lambda r: clean_text(r.get("ExpectedCount", "")) or UNIT_META.get(r.get("Code", ""), {}).get("ExpectedCount", ""),
        axis=1,
    )

    if "FolderName" not in df.columns:
        if "MinorFolder" in df.columns:
            df["FolderName"] = df["MinorFolder"].astype(str)
        else:
            df["FolderName"] = ""
    df["FolderName"] = df.apply(
        lambda r: clean_text(r.get("FolderName", "")) or folder_display_name(r.get("Code", ""), r.get("UnitName", "")),
        axis=1,
    )

    for c in DB_HEADERS:
        if c not in df.columns:
            df[c] = ""

    df = df[DB_HEADERS].copy()
    df["Code"] = df["Code"].astype(str).str.strip()
    df["MajorCode"] = df["MajorCode"].astype(str).str.strip()
    df["QNo"] = pd.to_numeric(df["QNo"], errors="coerce").astype("Int64")
    df["ExpectedCount"] = pd.to_numeric(df["ExpectedCount"], errors="coerce").astype("Int64")
    df["FileName"] = df["FileName"].astype(str).apply(ensure_png_filename)

    for c in ["MajorName", "UnitName", "FolderName", "FolderLink", "DriveFileId", "Answer", "Notes"]:
        df[c] = df[c].astype(str).replace({"nan": "", "None": "", "<NA>": ""})

    df = df[df["Code"].isin(UNIT_META.keys())]
    df = df.sort_values(
        ["Code", "QNo"],
        key=lambda col: col.map(sort_code_key) if col.name == "Code" else col,
        na_position="last",
    )
    return df.reset_index(drop=True)

def first_nonempty(series: pd.Series) -> str:
    for v in series.tolist():
        s = str(v or "").strip()
        if s and s.lower() not in {"nan", "none"}:
            return s
    return ""


def build_packet_items(
    df_db: pd.DataFrame,
    codes: List[str],
    small_max: int,
    mid_max: int,
    rng: random.Random,
) -> Tuple[List[PacketItem], List[str]]:
    """
    Build packet items from manually uploaded Drive folders.
    It uses DriveFileId if present. Otherwise it uses FolderLink + FileName.
    If FileName rows are missing but FolderLink exists, it can list PNGs in that Drive folder and choose from them.
    """
    items: List[PacketItem] = []
    warnings: List[str] = []

    for code in codes:
        unit_meta = UNIT_META.get(code, {})
        unit_name = unit_meta.get("UnitName", code)
        expected = int(unit_meta.get("ExpectedCount", 0) or 0)

        subset_all = df_db[df_db["Code"] == code].copy()
        if subset_all.empty:
            warnings.append(f"{code} {unit_name}: Sheet1 DB에 해당 단원 행이 없습니다.")
            continue

        folder_link = first_nonempty(subset_all.get("FolderLink", pd.Series(dtype=str)))
        folder_id = ""
        file_map: Dict[str, str] = {}
        if folder_link:
            try:
                folder_id = extract_folder_id(folder_link)
                file_map = list_png_files_in_folder(folder_id)
            except Exception as e:
                warnings.append(f"{code} {unit_name}: FolderLink 확인 실패 ({e})")

        # Candidate rows from DB rows with FileName.
        candidates = []
        rows = subset_all[subset_all["FileName"].astype(str).str.strip() != ""].copy()
        rows = rows.drop_duplicates(subset=["FileName"], keep="first")
        rows = rows.sort_values("QNo")

        for _, row in rows.iterrows():
            filename = ensure_png_filename(row.get("FileName", ""))
            if not filename:
                continue
            file_id = str(row.get("DriveFileId", "")).strip()
            if not file_id and file_map:
                file_id = file_map.get(filename, "")
            # If neither DriveFileId nor FolderLink lookup works, keep candidate but it may fail later with warning.
            candidates.append({
                "filename": filename,
                "qno": row.get("QNo", ""),
                "answer": "" if pd.isna(row.get("Answer", "")) else str(row.get("Answer", "")).strip(),
                "file_id": file_id,
            })

        # Fallback: one row per folder only. Use actual PNG list in Drive folder.
        if not candidates and file_map:
            for filename, fid in sorted(file_map.items(), key=lambda kv: (parse_problem_filename(kv[0])[1] if parse_problem_filename(kv[0]) else 99999, kv[0])):
                parsed = parse_problem_filename(filename)
                candidates.append({
                    "filename": filename,
                    "qno": parsed[1] if parsed else "",
                    "answer": "",
                    "file_id": fid,
                })

        if not candidates:
            warnings.append(f"{code} {unit_name}: 가져올 수 있는 PNG가 없습니다. FileName 또는 FolderLink를 확인해줘.")
            continue

        available = len(candidates)
        k = desired_pick_count(available, expected, small_max=small_max, mid_max=mid_max)
        if k <= 0:
            warnings.append(f"{code} {unit_name}: 선택 가능한 문제가 없습니다.")
            continue

        chosen = rng.sample(candidates, k=k) if available > k else candidates
        chosen = sorted(chosen, key=lambda x: (int(x["qno"]) if str(x["qno"]).isdigit() else 99999, x["filename"]))

        first = True
        for cand in chosen:
            file_id = cand.get("file_id", "")
            if not file_id:
                warnings.append(f"{code} {unit_name} / {cand['filename']}: Drive에서 파일을 찾지 못했습니다.")
                continue
            try:
                png_bytes = download_drive_file_bytes(file_id)
            except Exception as e:
                warnings.append(f"{code} {unit_name} / {cand['filename']}: 다운로드 실패 ({e})")
                continue

            qno = cand.get("qno", "")
            q_label = f"{code}-{qno}" if str(qno).strip() and str(qno).strip() != "<NA>" else Path(cand["filename"]).stem
            items.append((code, unit_name, q_label, cand["filename"], png_bytes, cand.get("answer", ""), first))
            first = False

    return items, warnings


def pdf_text(text: str, korean_ok: bool) -> str:
    """Avoid ReportLab Helvetica Unicode errors if Korean font is missing."""
    s = str(text or "")
    if korean_ok:
        return s
    try:
        s.encode("latin-1")
        return s
    except Exception:
        return re.sub(r"[^\x00-\x7F]+", "", s)


def make_packet_pdf(title: str, items: List[PacketItem], page_size_name: str = "Letter") -> bytes:
    """
    Layout based on the user's uploaded packet code:
    - Title only on the first page
    - Same top title gap on every page
    - Category printed once per selected unit
    - 2 to 3 question images per page when possible
    - Answer printed under each image, right aligned
    """
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
    answer_h = 13
    block_gap = 9
    min_scale_for_three = 0.43
    max_w = W - 2 * margin_x

    def draw_footer():
        c.setFont(body_font, 7)
        c.drawRightString(W - margin_x, 24, pdf_text("YOU, GENIUS 유지니어스 MATH with 유진쌤", korean_ok))

    def compute_scale(iw: float, ih: float, slot_h: float, has_category_line: bool) -> float:
        extra_cat = category_h + category_gap if has_category_line else 0
        usable_h = slot_h - extra_cat - answer_h - block_gap
        if usable_h <= 25:
            return 0.0
        return min(max_w / iw, usable_h / ih)

    idx = 0
    page_index = 0
    n_total = len(items)

    while idx < n_total:
        if page_index == 0:
            draw_fake_bold_string(c, margin_x, H - margin_top, pdf_text(title, korean_ok), title_font, title_font_size)

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
            code, unit_name, q_label, _filename, png_bytes, answer, show_cat = items[idx]
            slot_top = content_top - slot_h * block_i
            slot_bottom = content_top - slot_h * (block_i + 1)
            y = slot_top

            if show_cat:
                cat_text = f"Category: {code} {unit_name}"
                c.drawString(margin_x, y, pdf_text(cat_text, korean_ok))
                y -= (category_h + category_gap)

            img = ImageReader(io.BytesIO(png_bytes))
            iw, ih = img.getSize()
            usable_h = (y - slot_bottom) - answer_h - block_gap
            if usable_h <= 25:
                break

            scale = min(max_w / iw, usable_h / ih)
            if scale <= 0:
                break

            draw_w = iw * scale
            draw_h = ih * scale
            img_x = margin_x
            img_y = y - draw_h
            c.drawImage(img, img_x, img_y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")

            ans = str(answer or "").strip()
            answer_text = f"Answer:{ans}" if ans else "Answer:"
            answer_text = pdf_text(answer_text, korean_ok)
            answer_y = img_y - answer_h
            try:
                text_w = pdfmetrics.stringWidth(answer_text, body_font, body_font_size)
            except Exception:
                text_w = 0
            c.drawString(max(margin_x, W - margin_x - text_w), answer_y, answer_text)
            idx += 1

        draw_footer()
        c.showPage()
        page_index += 1

    c.save()
    return out.getvalue()




# =========================================================
# Minimal Sheet1 DB support override
# =========================================================
# ✅ 가장 간단한 Sheet1 헤더
# 1) 단원별 폴더만 관리할 때: Code | FolderLink
# 2) 문제별 정답까지 관리할 때: FileName | FolderLink | Answer
#
# 앱 내부에서는 단원표(1.1–7.9)를 이미 알고 있으므로,
# MajorName / UnitName / ExpectedCount / QNo는 자동으로 채웁니다.
SIMPLE_FOLDER_DB_HEADERS = ["Code", "FolderLink"]
SIMPLE_FILE_DB_HEADERS = ["FileName", "FolderLink", "Answer"]


def _clean_db_text(value: Any) -> str:
    s = str(value or "").strip()
    return "" if s.lower() in {"nan", "none", "<na>"} else s


def _first_clean(values) -> str:
    for v in list(values):
        s = _clean_db_text(v)
        if s:
            return s
    return ""


def normalize_db(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sheet1 can now be very simple.

    Minimal option A, one row per unit folder:
      Code | FolderLink
      1.1  | https://drive.google.com/drive/folders/...

    Minimal option B, one row per problem if you want answer keys:
      FileName | FolderLink | Answer
      1.1-5.png | https://drive.google.com/drive/folders/... | C

    Accepted optional columns:
      Code, FileName, FolderLink, DriveFileId, Answer, Notes

    The app automatically infers:
      MajorCode, MajorName, UnitName, QNo, FolderName, ExpectedCount
    """
    df = df.copy()
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame(columns=DB_HEADERS)

    df.columns = [str(c).strip() for c in df.columns]

    aliases = {
        "단원코드": "Code", "코드": "Code", "UnitCode": "Code", "Unit Code": "Code", "unit_code": "Code",
        "파일명": "FileName", "문제파일": "FileName", "이미지파일": "FileName", "File": "FileName",
        "ImageName": "FileName", "Image Name": "FileName", "file_name": "FileName",
        "폴더링크": "FolderLink", "폴더 링크": "FolderLink", "Drive폴더링크": "FolderLink",
        "Drive Folder Link": "FolderLink", "DriveFolderLink": "FolderLink", "Folder Link": "FolderLink",
        "FolderURL": "FolderLink", "Folder URL": "FolderLink", "Link": "FolderLink", "링크": "FolderLink",
        "Drive File ID": "DriveFileId", "DriveFileID": "DriveFileId", "FileId": "DriveFileId",
        "File ID": "DriveFileId", "파일ID": "DriveFileId",
        "정답": "Answer", "답": "Answer", "메모": "Notes", "비고": "Notes",
        "대단원코드": "MajorCode", "대단원명": "MajorName", "단원명": "UnitName",
        "문항번호": "QNo", "문제번호": "QNo", "No": "QNo", "문항수": "ExpectedCount",
        "예상문항수": "ExpectedCount", "폴더명": "FolderName",
    }

    renamed = []
    seen = set()
    for col in df.columns:
        canonical = aliases.get(col, col)
        if canonical in seen:
            canonical = col
        renamed.append(canonical)
        seen.add(canonical)
    df.columns = renamed

    for c in ["Code", "FileName", "QNo", "FolderLink", "DriveFileId", "Answer", "Notes"]:
        if c not in df.columns:
            df[c] = ""

    df["FileName"] = df["FileName"].astype(str).apply(ensure_png_filename)

    def code_from_row(r) -> str:
        code = parse_code_token(r.get("Code", ""))
        if code:
            return code
        parsed = parse_problem_filename(r.get("FileName", ""))
        return parsed[0] if parsed else ""

    def qno_from_row(r):
        q = _clean_db_text(r.get("QNo", ""))
        if q:
            try:
                return int(float(q))
            except Exception:
                return q
        parsed = parse_problem_filename(r.get("FileName", ""))
        return parsed[1] if parsed else None

    df["Code"] = df.apply(code_from_row, axis=1)
    df["QNo"] = df.apply(qno_from_row, axis=1)
    df = df[df["Code"].isin(UNIT_META.keys())].copy()

    df["MajorCode"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("MajorCode", major_code_from_unit_code(c)))
    df["MajorName"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("MajorName", major_name_from_unit_code(c)))
    df["UnitName"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("UnitName", ""))
    df["ExpectedCount"] = df["Code"].map(lambda c: UNIT_META.get(c, {}).get("ExpectedCount", ""))
    df["FolderName"] = df.apply(lambda r: folder_display_name(r.get("Code", ""), r.get("UnitName", "")), axis=1)

    link_by_code = df.groupby("Code")["FolderLink"].apply(_first_clean).to_dict() if not df.empty else {}
    df["FolderLink"] = df.apply(
        lambda r: _clean_db_text(r.get("FolderLink", "")) or link_by_code.get(r.get("Code", ""), ""),
        axis=1,
    )

    for c in DB_HEADERS:
        if c not in df.columns:
            df[c] = ""

    df = df[DB_HEADERS].copy()
    df["Code"] = df["Code"].astype(str).str.strip()
    df["MajorCode"] = df["MajorCode"].astype(str).str.strip()
    df["QNo"] = pd.to_numeric(df["QNo"], errors="coerce").astype("Int64")
    df["ExpectedCount"] = pd.to_numeric(df["ExpectedCount"], errors="coerce").astype("Int64")
    df["FileName"] = df["FileName"].astype(str).apply(ensure_png_filename)
    for c in ["MajorName", "UnitName", "FolderName", "FolderLink", "DriveFileId", "Answer", "Notes"]:
        df[c] = df[c].astype(str).replace({"nan": "", "None": "", "<NA>": ""})

    df = df.sort_values(
        ["Code", "QNo", "FileName"],
        key=lambda col: col.map(sort_code_key) if col.name == "Code" else col,
        na_position="last",
    )
    return df.reset_index(drop=True)


def count_available_pngs_by_code(df_db: pd.DataFrame, use_drive_folders: bool = True) -> Tuple[pd.DataFrame, List[str]]:
    """Count recognized problems from FileName rows, or from Drive folders for Code|FolderLink minimal DB."""
    warnings: List[str] = []
    file_rows = df_db[df_db["FileName"].astype(str).str.strip() != ""].copy()
    file_counts = file_rows.groupby("Code").size().to_dict() if not file_rows.empty else {}

    rows = []
    for unit in UNIT_DF.itertuples():
        code = unit.Code
        db_count = int(file_counts.get(code, 0))
        drive_count = None

        if use_drive_folders and db_count == 0:
            subset = df_db[df_db["Code"] == code].copy()
            folder_link = first_nonempty(subset.get("FolderLink", pd.Series(dtype=str))) if not subset.empty else ""
            if folder_link:
                try:
                    folder_id = extract_folder_id(folder_link)
                    file_map = list_png_files_in_folder(folder_id)
                    matching = [name for name in file_map if parse_problem_filename(name) and parse_problem_filename(name)[0] == code]
                    drive_count = len(matching) if matching else len(file_map)
                except Exception as e:
                    warnings.append(f"{code} {unit.UnitName}: Drive 폴더 파일 수 확인 실패 ({e})")

        available = db_count if db_count > 0 else int(drive_count or 0)
        missing = int(unit.ExpectedCount) - available
        rows.append({
            "MajorCode": unit.MajorCode,
            "MajorName": unit.MajorName,
            "Code": code,
            "UnitName": unit.UnitName,
            "ExpectedCount": int(unit.ExpectedCount),
            "SheetFileNameRows": db_count,
            "DriveFolderPNGCount": "" if drive_count is None else int(drive_count),
            "RecognizedCount": available,
            "Missing": missing,
            "Status": "OK" if missing == 0 else ("부족" if missing > 0 else "초과"),
        })

    return pd.DataFrame(rows), warnings


# =========================================================
# UI
# =========================================================
require_access_code()
st.title(APP_TITLE)
st.caption(f"단원표 기준 총 {TOTAL_EXPECTED}문항 / {len(UNIT_ROWS)}개 단원")

with st.expander("단원 코드표 확인", expanded=False):
    st.dataframe(UNIT_DF, use_container_width=True, hide_index=True)

sheet_id = safe_sheet_id()

with st.sidebar:
    logout_button()
    st.divider()
    st.header("기본 설정")
    sheet_id = st.text_input("Google Sheet ID", value=sheet_id)
    st.caption("Sheet1 DB는 최소형 헤더도 인식합니다: Code|FolderLink 또는 FileName|FolderLink|Answer")
    st.markdown("**필요한 폰트 경로**  ")
    st.code("assets/fonts/NotoSansKR-VariableFont_wght.ttf")
    if st.button("캐시 새로고침"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("캐시를 비웠어요.")


tab1, tab2, tab3 = st.tabs([
    "1️⃣ PDF → PNG ZIP",
    "2️⃣ 보충 Packet 생성",
    "3️⃣ DB 점검",
])

# -------------------------
# TAB 1
# -------------------------
with tab1:
    st.subheader("1️⃣ PDF를 문제별 PNG ZIP으로 만들기")
    st.write("PDF에 있는 문제를 위에서 아래 순서대로 잘라서 `1.1-1.png`, `1.1-2.png`처럼 저장합니다. ZIP은 여기서 다운로드한 뒤 직접 Drive에 올리면 됩니다.")

    pdf = st.file_uploader("문제은행 PDF 업로드", type=["pdf"], key="tab1_pdf")

    c1, c2, c3, c4 = st.columns(4)
    zoom = c1.slider("해상도 zoom", 2.0, 4.5, 3.0, 0.1)
    pad_top = c2.slider("위 여백", 0, 120, 10, 1)
    pad_bottom = c3.slider("아래 여백", 0, 180, 12, 1)
    last_extra = c4.slider("페이지 마지막 문제 아래 여백(px)", 0, 300, 30, 10)

    naming_mode = st.radio(
        "파일명 붙이는 방식",
        ["전체 1255문항: 표 순서대로 자동 이름 붙이기", "선택한 단원 하나로만 이름 붙이기", "CSV 매핑으로 이름 붙이기"],
        horizontal=True,
    )

    selected_code = None
    mapping_file = None
    if naming_mode == "선택한 단원 하나로만 이름 붙이기":
        selected_label = st.selectbox("단원 선택", options=get_unit_options())
        selected_code = label_to_code(selected_label)
    elif naming_mode == "CSV 매핑으로 이름 붙이기":
        st.info("CSV에는 FileName 컬럼이 필요합니다. 예: 1.1-1.png")
        mapping_file = st.file_uploader("CSV 매핑 업로드", type=["csv"], key="mapping_csv")

    cc1, cc2 = st.columns(2)
    put_unit_folders = cc1.checkbox("ZIP 안에 단원별 폴더 만들기", value=True)
    unify_width = cc2.checkbox("가로폭을 가장 넓은 문제에 맞춤", value=True)

    allow_inline = st.checkbox("문제번호가 문제 문장과 같은 줄에 있어도 anchor로 인식", value=True)
    require_example_anchor = st.checkbox(
        "문제 시작 줄에 [Example]이 있는 이 문제은행 PDF 전용으로 인식",
        value=True,
        help="켜두면 1. Linear 같은 단원 제목, 1./2./3./4. 번호 선택지, 줄바꿈으로 생긴 '8. What ...' 같은 가짜 anchor를 제외합니다.",
    )

    st.markdown("##### 캡쳐 보정 옵션")
    opt1, opt2, opt3, opt4 = st.columns(4)
    exclude_number_line = opt1.checkbox("문제번호 줄 제외", value=True)
    number_line_offset = opt2.slider("번호 줄 아래 시작 보정", 0, 20, 2, 1)
    protect_graph_table_bottom = opt3.checkbox("표/그림 아래쪽 잘림 방지", value=True)
    ink_bottom_extra = opt4.slider("표/그림 아래 여백", 0, 40, 8, 1)

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
                    allow_inline_question_text=allow_inline,
                    require_example_anchor=require_example_anchor,
                    exclude_question_number_line=exclude_number_line,
                    number_line_offset=number_line_offset,
                    protect_graph_table_bottom=protect_graph_table_bottom,
                    ink_bottom_extra=ink_bottom_extra,
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
            if detected != expected:
                st.warning(
                    f"감지된 문제 수는 {detected}개, 파일명 계획은 {expected}개입니다. "
                    "ZIP에는 감지된 문제 수만큼만 저장됩니다. 부분 PDF라면 정상일 수 있고, "
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
    st.subheader("2️⃣ 학생별 보충 Packet 생성")
    st.write("학생 이름과 반을 입력하고, 보충이 필요한 단원을 여러 개 직접 선택하면 PDF가 생성됩니다.")

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
        st.warning("Sheet1 DB가 비어 있어요. 최소형은 Code / FolderLink 두 컬럼만 넣어도 됩니다.")
        st.stop()

    with st.expander("현재 Sheet1에서 읽은 DB 미리보기", expanded=False):
        st.info("최소형 Sheet1 헤더: Code | FolderLink  /  정답까지 넣을 때: FileName | FolderLink | Answer")
        st.dataframe(df_db.head(80), use_container_width=True, hide_index=True)

    col1, col2 = st.columns(2)
    student_name = col1.text_input("학생 이름", value="")
    class_name = col2.text_input("반/Class", value="")

    selected_labels = st.multiselect("보충이 필요한 단원 선택", options=get_unit_options())
    selected_codes = [label_to_code(x) for x in selected_labels]
    selected_codes = [c for c in selected_codes if c]

    display_title = f"{class_name.strip()} {student_name.strip()} 보충 PACKET".strip()
    file_title = sanitize_filename(f"{student_name.strip()}_{class_name.strip()}_보충 PACKET")
    st.write("PDF 제목 미리보기:", display_title if display_title else "")

    if st.button("PDF 생성", type="primary"):
        if not student_name.strip() or not class_name.strip():
            st.error("학생 이름과 반을 입력해줘.")
            st.stop()
        if not selected_codes:
            st.error("보충 단원을 최소 1개 선택해줘.")
            st.stop()

        with st.spinner("Drive에서 문제 이미지를 가져와 PDF를 만드는 중..."):
            items, warns = build_packet_items(df_db, selected_codes, int(small_max), int(mid_max), rng)
            if not items:
                st.error("PDF에 넣을 문제가 없습니다. DB/Drive 권한/파일명/FolderLink를 확인해줘.")
                if warns:
                    st.code("\n".join(warns))
                st.stop()
            pdf_bytes = make_packet_pdf(display_title, items, page_size_name=page_size_name)

        st.success(f"완료! 총 {len(items)}문제")
        if warns:
            with st.expander("경고/누락 확인"):
                st.write("\n".join(warns))
        st.download_button("PDF 다운로드", data=pdf_bytes, file_name=f"{file_title}.pdf", mime="application/pdf")

# -------------------------
# TAB 3
# -------------------------
with tab3:
    st.subheader("3️⃣ DB 점검")
    st.write("Sheet1에 기록된 파일 수가 단원표의 문항수와 맞는지 확인합니다. 수동 업로드 후 DB 확인용입니다.")

    if st.button("DB 다시 불러오기"):
        load_db_from_sheet.clear()
        list_png_files_in_folder.clear()

    try:
        df_db_check = load_db_from_sheet(sheet_id)
    except Exception as e:
        st.error("Sheet1 DB를 읽는 중 오류가 발생했어요.")
        st.code(str(e))
        st.stop()

    if df_db_check.empty:
        st.warning("DB가 비어 있습니다.")
    else:
        use_drive_folder_counts = st.checkbox(
            "Code | FolderLink 최소형 DB일 때 Drive 폴더 안 PNG 개수까지 세기",
            value=True,
            help="Sheet1에 FileName 행이 없고 Code와 FolderLink만 있을 때, Drive 폴더 안의 PNG 파일 수로 점검합니다.",
        )
        check, count_warnings = count_available_pngs_by_code(df_db_check, use_drive_folders=use_drive_folder_counts)
        st.metric("인식 가능한 문제 수", int(check["RecognizedCount"].sum()))
        st.metric("표 기준 총 문항수", TOTAL_EXPECTED)
        st.dataframe(check, use_container_width=True, hide_index=True)

        if count_warnings:
            with st.expander("Drive 폴더 점검 경고"):
                st.write("\n".join(count_warnings))

        bad = check[check["Status"] != "OK"]
        if not bad.empty:
            st.warning("문항수가 맞지 않는 단원이 있습니다.")
            st.dataframe(bad, use_container_width=True, hide_index=True)

st.caption("파일명 규칙: 단원코드-문제번호.png  예) 1.1-5.png")
