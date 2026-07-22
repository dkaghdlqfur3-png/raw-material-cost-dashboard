import html
import io
import re
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st


st.set_page_config(
    page_title="원가 영향 인자 대시보드",
    page_icon="📊",
    layout="wide",
)

# =========================================================
# 화면 스타일
# =========================================================
st.markdown(
    """
    <style>
    .block-container {
        max-width: 120rem;
        padding-top: 1rem;
        padding-bottom: 2rem;
    }

    h1 { font-size: 2.15rem !important; }
    h2 { font-size: 1.65rem !important; }
    h3 { font-size: 1.30rem !important; }

    div[data-testid="stMetric"] {
        background-color: #f8f9fb;
        border: 1px solid #e7eaf0;
        padding: 15px;
        border-radius: 14px;
    }

    div[data-testid="stMetricLabel"] {
        font-size: 1.02rem !important;
        font-weight: 700 !important;
    }

    div[data-testid="stMetricValue"] {
        font-size: 1.65rem !important;
        font-weight: 700 !important;
    }

    div[data-testid="stMetricDelta"] {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
    }

    [data-testid="stDataFrame"] td { font-size: 1rem !important; }
    [data-testid="stDataFrame"] th { font-size: 1.03rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 기본값
# =========================================================
KST = pytz.timezone("Asia/Seoul")
NOW = datetime.now(KST)

SHEET_ALIASES = {
    "원자재": ["원자재", "raw", "raw material", "raw_material", "commodity", "commodities"],
    "환율": ["환율", "fx", "exchange", "exchange rate", "currency"],
}

COLUMN_ALIASES = {
    "item": ["품목", "항목", "원자재", "통화", "item", "name", "indicator", "지표"],
    "date": ["날짜", "일자", "기준일", "년월", "월", "date", "month", "ym"],
    "value": ["값", "가격", "시세", "환율", "지수", "단가", "value", "price", "rate"],
    "unit": ["단위", "unit"],
    "currency": ["통화", "currency"],
    "related": ["사용 품목", "관련 품목", "적용 품목", "연관 품목", "related", "related item"],
    "source": ["출처", "source"],
    "category": ["구분", "분류", "category", "type"],
}


# =========================================================
# 공통 유틸리티
# =========================================================
def normalize_text(value):
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def find_column(df, aliases):
    normalized = {normalize_text(c): c for c in df.columns}
    for alias in aliases:
        key = normalize_text(alias)
        if key in normalized:
            return normalized[key]
    return None


def clean_number(value):
    if pd.isna(value):
        return pd.NA
    if isinstance(value, (int, float, np.number)):
        return float(value)

    text = str(value).strip()
    if not text:
        return pd.NA

    text = text.replace(",", "")
    text = re.sub(r"[₩$€¥%]", "", text)
    text = re.sub(r"\(([^)]+)\)", r"-\1", text)
    text = re.sub(r"[^0-9.\-]", "", text)

    if text in {"", "-", ".", "-."}:
        return pd.NA

    try:
        return float(text)
    except ValueError:
        return pd.NA


def parse_month(value):
    if pd.isna(value):
        return pd.NaT

    if isinstance(value, pd.Timestamp):
        return value.to_period("M").to_timestamp()

    if isinstance(value, datetime):
        return pd.Timestamp(value).to_period("M").to_timestamp()

    # Excel 날짜 일련번호 대응
    if isinstance(value, (int, float, np.number)) and 20000 < float(value) < 80000:
        try:
            dt = pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
            return dt.to_period("M").to_timestamp()
        except Exception:
            pass

    text = str(value).strip()
    if not text:
        return pd.NaT

    text = text.replace("년", "-").replace("월", "")
    text = text.replace("/", "-").replace(".", "-")
    text = re.sub(r"\s+", "", text)

    patterns = [
        (r"^\d{4}-\d{1,2}$", lambda x: x + "-01"),
        (r"^\d{2}-\d{1,2}$", lambda x: "20" + x + "-01"),
        (r"^\d{6}$", lambda x: x[:4] + "-" + x[4:] + "-01"),
        (r"^\d{4}$", lambda x: x + "-01-01"),
    ]

    for pattern, converter in patterns:
        if re.fullmatch(pattern, text):
            text = converter(text)
            break

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    return parsed.to_period("M").to_timestamp()


def looks_like_month_column(column_name):
    return not pd.isna(parse_month(column_name))


def select_sheet(sheet_names, target):
    aliases = [normalize_text(x) for x in SHEET_ALIASES[target]]
    normalized_names = {normalize_text(name): name for name in sheet_names}

    for alias in aliases:
        if alias in normalized_names:
            return normalized_names[alias]

    for name in sheet_names:
        normalized_name = normalize_text(name)
        if any(alias in normalized_name for alias in aliases):
            return name

    return None


# =========================================================
# 엑셀 데이터 변환
# =========================================================
def convert_long_format(df, default_category=None):
    item_col = find_column(df, COLUMN_ALIASES["item"])
    date_col = find_column(df, COLUMN_ALIASES["date"])
    value_col = find_column(df, COLUMN_ALIASES["value"])

    if not all([item_col, date_col, value_col]):
        return None

    unit_col = find_column(df, COLUMN_ALIASES["unit"])
    currency_col = find_column(df, COLUMN_ALIASES["currency"])
    related_col = find_column(df, COLUMN_ALIASES["related"])
    source_col = find_column(df, COLUMN_ALIASES["source"])
    category_col = find_column(df, COLUMN_ALIASES["category"])

    result = pd.DataFrame({
        "item": df[item_col].astype(str).str.strip(),
        "date": df[date_col].apply(parse_month),
        "value": df[value_col].apply(clean_number),
        "unit": df[unit_col].astype(str).str.strip() if unit_col else "",
        "currency": df[currency_col].astype(str).str.strip() if currency_col else "",
        "related": df[related_col].astype(str).str.strip() if related_col else "",
        "source": df[source_col].astype(str).str.strip() if source_col else "",
        "category": (
            df[category_col].astype(str).str.strip()
            if category_col
            else default_category or ""
        ),
    })
    return result


def convert_wide_format(df, default_category=None):
    month_cols = [c for c in df.columns if looks_like_month_column(c)]
    if not month_cols:
        return None

    item_col = find_column(df, COLUMN_ALIASES["item"])
    if item_col is None:
        # 첫 번째 비월 컬럼을 품목으로 사용
        non_month_cols = [c for c in df.columns if c not in month_cols]
        if not non_month_cols:
            return None
        item_col = non_month_cols[0]

    unit_col = find_column(df, COLUMN_ALIASES["unit"])
    currency_col = find_column(df, COLUMN_ALIASES["currency"])
    related_col = find_column(df, COLUMN_ALIASES["related"])
    source_col = find_column(df, COLUMN_ALIASES["source"])
    category_col = find_column(df, COLUMN_ALIASES["category"])

    id_vars = [item_col]
    for col in [unit_col, currency_col, related_col, source_col, category_col]:
        if col and col not in id_vars:
            id_vars.append(col)

    melted = df.melt(
        id_vars=id_vars,
        value_vars=month_cols,
        var_name="date_raw",
        value_name="value_raw",
    )

    result = pd.DataFrame({
        "item": melted[item_col].astype(str).str.strip(),
        "date": melted["date_raw"].apply(parse_month),
        "value": melted["value_raw"].apply(clean_number),
        "unit": melted[unit_col].astype(str).str.strip() if unit_col else "",
        "currency": melted[currency_col].astype(str).str.strip() if currency_col else "",
        "related": melted[related_col].astype(str).str.strip() if related_col else "",
        "source": melted[source_col].astype(str).str.strip() if source_col else "",
        "category": (
            melted[category_col].astype(str).str.strip()
            if category_col
            else default_category or ""
        ),
    })
    return result


def tidy_dataframe(df, default_category=None):
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "item", "date", "value", "currency", "unit", "related", "source", "category"
        ])

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").dropna(axis=1, how="all")

    result = convert_long_format(df, default_category)
    if result is None:
        result = convert_wide_format(df, default_category)

    if result is None:
        raise ValueError(
            "데이터 형식을 인식하지 못했습니다. "
            "세로형은 [품목, 날짜, 값], 가로형은 [품목, 2026-01, 2026-02...] 형태로 입력해 주세요."
        )

    for col in ["unit", "currency", "related", "source", "category"]:
        result[col] = result[col].replace({"nan": "", "None": ""}).fillna("").astype(str).str.strip()

    result["item"] = result["item"].replace({"nan": "", "None": ""}).fillna("").astype(str).str.strip()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["date", "value"])
    result = result[result["item"] != ""]

    # 동일 품목·동일 월 중복 시 마지막 값 사용
    result = (
        result.sort_values(["item", "date"])
        .drop_duplicates(subset=["item", "date"], keep="last")
        .reset_index(drop=True)
    )
    result["ym"] = result["date"].map(lambda value: f"{value.year}년 {value.month}월")
    return result


@st.cache_data(show_spinner=False)
def load_excel(file_bytes):
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_names = xls.sheet_names

    raw_sheet = select_sheet(sheet_names, "원자재")
    fx_sheet = select_sheet(sheet_names, "환율")

    loaded = {}
    errors = []

    if raw_sheet:
        try:
            raw_source = pd.read_excel(io.BytesIO(file_bytes), sheet_name=raw_sheet)
            loaded["원자재"] = tidy_dataframe(raw_source, "원자재")
        except Exception as exc:
            errors.append(f"원자재 시트: {exc}")

    if fx_sheet:
        try:
            fx_source = pd.read_excel(io.BytesIO(file_bytes), sheet_name=fx_sheet)
            loaded["환율"] = tidy_dataframe(fx_source, "환율")
        except Exception as exc:
            errors.append(f"환율 시트: {exc}")

    # 원자재/환율 시트명이 없으면 첫 시트의 '구분' 열로 분리 시도
    if not loaded and sheet_names:
        first_df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_names[0])
        combined = tidy_dataframe(first_df)

        if "category" in combined.columns and combined["category"].str.strip().ne("").any():
            category_text = combined["category"].str.lower()
            raw_mask = category_text.str.contains("원자재|raw|commodity", regex=True)
            fx_mask = category_text.str.contains("환율|fx|exchange|currency", regex=True)

            if raw_mask.any():
                loaded["원자재"] = combined[raw_mask].copy()
            if fx_mask.any():
                loaded["환율"] = combined[fx_mask].copy()

        if not loaded:
            loaded["원자재"] = combined
            errors.append(
                "시트명을 찾지 못해 첫 번째 시트를 원자재 데이터로 불러왔습니다."
            )

    return loaded, errors, sheet_names


# =========================================================
# 연도별 업로드 양식 변환기
# =========================================================
_load_legacy_excel = load_excel
YEAR_SHEET_PATTERN = re.compile(r"^\s*(20\d{2})\s*년?\s*$")
FX_CODES = {"USD", "EUR", "JPY", "CNY", "GBP", "AUD", "CAD", "CHF"}


def _upload_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def _currency_code(value):
    text = _upload_text(value).upper()
    matched = re.search(r"\b([A-Z]{3})\b", text)
    return matched.group(1) if matched else text


def _month_header(value, year):
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value.year, value.month, 1)
    if hasattr(value, "year") and hasattr(value, "month"):
        return pd.Timestamp(int(value.year), int(value.month), 1)

    text = _upload_text(value)
    full = re.search(r"(20\d{2})\D+(1[0-2]|0?[1-9])", text)
    if full:
        return pd.Timestamp(int(full.group(1)), int(full.group(2)), 1)
    month = re.fullmatch(r"(1[0-2]|0?[1-9])\s*월?", text)
    if month:
        return pd.Timestamp(year, int(month.group(1)), 1)
    return pd.NaT


def _find_upload_header(raw):
    for row_no in range(min(len(raw), 20)):
        values = {_upload_text(value).lower() for value in raw.iloc[row_no].tolist()}
        if ({"구분", "원료항목", "항목"} & values) and ({"no.", "no", "번호"} & values):
            return row_no
    raise ValueError("'no.'와 '구분'이 있는 헤더 행을 찾지 못했습니다.")


def _read_year_sheet(xls, sheet_name, year):
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
    header_row = _find_upload_header(raw)
    header = raw.iloc[header_row].tolist()
    aliases = {
        "item": {"구분", "원료항목", "항목"},
        "related": {"관련 구매품", "관련구매품"},
        "currency": {"통화"},
        "unit": {"단위"},
    }
    positions = {}
    for column_no, value in enumerate(header):
        label = re.sub(r"\s+", " ", _upload_text(value).lower())
        for name, candidates in aliases.items():
            if label in candidates and name not in positions:
                positions[name] = column_no

    month_columns = []
    for column_no, value in enumerate(header):
        month = _month_header(value, year)
        if pd.notna(month):
            month_columns.append((column_no, month))
    if "item" not in positions or not month_columns:
        raise ValueError(f"{sheet_name}: 항목 또는 월별 데이터 열을 찾지 못했습니다.")

    records = []
    for row_no in range(header_row + 1, len(raw)):
        row = raw.iloc[row_no]
        item = _upload_text(row.iloc[positions["item"]])
        if not item:
            continue
        currency = _upload_text(row.iloc[positions["currency"]]) if "currency" in positions else ""
        category = "환율" if _currency_code(item) in FX_CODES and not currency else "원자재"
        unit = _upload_text(row.iloc[positions["unit"]]) if "unit" in positions else ""
        related = _upload_text(row.iloc[positions["related"]]) if "related" in positions else ""
        if category == "환율":
            item = _currency_code(item)
            currency = "KRW"
            unit = "100 JPY" if item == "JPY" else f"1 {item}"

        for column_no, month in month_columns:
            value = clean_number(row.iloc[column_no])
            if pd.isna(value):
                continue
            records.append({
                "item": item,
                "date": month,
                "value": value,
                "unit": unit,
                "currency": currency,
                "related": related,
                "source": "",
                "category": category,
            })
    return pd.DataFrame(records)


def _read_source_sheet(xls):
    source_name = next((name for name in xls.sheet_names if _upload_text(name) == "출처"), None)
    if source_name is None:
        return {}
    raw = pd.read_excel(xls, sheet_name=source_name, header=None)
    source_map = {}
    section = ""
    for row_no in range(len(raw)):
        values = [_upload_text(value) for value in raw.iloc[row_no].tolist()]
        joined = " ".join(value for value in values if value)
        if "원자재" in joined and "no." not in joined.lower():
            section = "원자재"
        elif "환율" in joined and "no." not in joined.lower():
            section = "환율"
        lowered = [value.lower() for value in values]
        if not ({"no.", "no", "번호"} & set(lowered)) or "출처" not in lowered:
            continue
        item_col = lowered.index("원료항목") if "원료항목" in lowered else (
            lowered.index("통화") if "통화" in lowered else None
        )
        if item_col is None:
            continue
        source_col = lowered.index("출처")
        for data_row_no in range(row_no + 1, len(raw)):
            data = [_upload_text(value) for value in raw.iloc[data_row_no].tolist()]
            if any("▶" in value for value in data) or ({"no.", "no", "번호"} & {value.lower() for value in data}):
                break
            if item_col >= len(data) or not data[item_col]:
                continue
            item = _currency_code(data[item_col]) if section == "환율" else data[item_col]
            source_map[(section, item)] = data[source_col] if source_col < len(data) else ""
    return source_map


@st.cache_data(show_spinner=False)
def load_yearly_upload_excel(file_bytes):
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    frames = []
    errors = []
    for sheet_name in xls.sheet_names:
        matched = YEAR_SHEET_PATTERN.match(_upload_text(sheet_name))
        if not matched:
            continue
        try:
            frame = _read_year_sheet(xls, sheet_name, int(matched.group(1)))
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            errors.append(str(exc))

    if not frames:
        # 기존 세로형/가로형 파일도 계속 지원합니다.
        return _load_legacy_excel(file_bytes)

    combined = pd.concat(frames, ignore_index=True)
    source_map = _read_source_sheet(xls)
    combined["source"] = combined.apply(
        lambda row: source_map.get((row["category"], row["item"]), ""), axis=1
    )
    combined = (
        combined.sort_values(["category", "item", "date"])
        .drop_duplicates(["category", "item", "date"], keep="last")
        .reset_index(drop=True)
    )
    combined["ym"] = combined["date"].map(lambda value: f"{value.year}년 {value.month}월")
    loaded = {
        "원자재": combined[combined["category"] == "원자재"].copy(),
        "환율": combined[combined["category"] == "환율"].copy(),
    }
    return loaded, errors, xls.sheet_names


# 화면 구성은 기존 앱 그대로 두고 업로드 변환기만 새 양식 지원 버전으로 교체합니다.
load_excel = load_yearly_upload_excel


# =========================================================
# 분석 계산
# =========================================================
def filter_period(df, months_to_show):
    if df.empty:
        return df

    latest_month = df["date"].max()
    start_month = latest_month - pd.DateOffset(months=months_to_show - 1)
    return df[df["date"].between(start_month, latest_month)].copy()


def get_item_meta(df):
    if "currency" not in df.columns:
        df = df.copy()
        df["currency"] = ""
    meta = (
        df.sort_values("date")
        .groupby("item", as_index=False)
        .agg({
            "unit": "last",
            "currency": "last",
            "related": "last",
            "source": "last",
        })
    )
    return meta.set_index("item").to_dict("index")


def get_snapshot(df):
    rows = []
    for item, group in df.groupby("item"):
        group = group.sort_values("date")
        latest_row = group.iloc[-1]
        latest_date = latest_row["date"]
        latest_value = latest_row["value"]

        prev_date = latest_date - pd.DateOffset(months=1)
        prev_match = group[group["date"] == prev_date]
        prev_value = prev_match.iloc[-1]["value"] if not prev_match.empty else pd.NA

        six_month_date = latest_date - pd.DateOffset(months=6)
        six_month_match = group[group["date"] == six_month_date]
        six_month_value = (
            six_month_match.iloc[-1]["value"] if not six_month_match.empty else pd.NA
        )

        previous_year = latest_date.year - 1
        previous_year_values = group.loc[
            group["date"].dt.year == previous_year, "value"
        ].dropna()
        previous_year_average = (
            previous_year_values.mean() if not previous_year_values.empty else pd.NA
        )

        mom = (
            (latest_value / prev_value - 1) * 100
            if pd.notna(prev_value) and prev_value != 0
            else pd.NA
        )
        mom_difference = (
            latest_value - prev_value if pd.notna(prev_value) else pd.NA
        )
        six_month_difference = (
            latest_value - six_month_value if pd.notna(six_month_value) else pd.NA
        )
        six_month_change = (
            (latest_value / six_month_value - 1) * 100
            if pd.notna(six_month_value) and six_month_value != 0
            else pd.NA
        )
        previous_year_difference = (
            latest_value - previous_year_average
            if pd.notna(previous_year_average)
            else pd.NA
        )
        previous_year_change = (
            (latest_value / previous_year_average - 1) * 100
            if pd.notna(previous_year_average) and previous_year_average != 0
            else pd.NA
        )

        rows.append({
            "item": item,
            "latest_date": latest_date,
            "latest_value": latest_value,
            "prev_value": prev_value,
            "mom_difference": mom_difference,
            "mom": mom,
            "six_month_value": six_month_value,
            "six_month_difference": six_month_difference,
            "six_month_change": six_month_change,
            "previous_year": previous_year,
            "previous_year_average": previous_year_average,
            "previous_year_difference": previous_year_difference,
            "previous_year_change": previous_year_change,
        })

    return pd.DataFrame(rows)


def format_value(value, unit=""):
    if pd.isna(value):
        return "-"

    if abs(value) >= 1000:
        text = f"{value:,.2f}"
    else:
        text = f"{value:,.2f}"

    return f"{text} {unit}".strip()


def combined_unit(currency="", unit=""):
    currency = str(currency or "").strip()
    unit = str(unit or "").strip()
    if currency and unit:
        return f"{currency} / {unit}"
    return currency or unit


def format_delta(value):
    if pd.isna(value):
        return "-"
    if value > 0:
        return f"▲ {abs(value):.2f}%"
    if value < 0:
        return f"▼ {abs(value):.2f}%"
    return "• 0.00%"


def format_table_delta(value):
    if pd.isna(value):
        return "-"
    if value > 0:
        return f"🔴 ▲ {abs(value):.2f}%"
    if value < 0:
        return f"🔵 ▼ {abs(value):.2f}%"
    return "⚪ • 0.00%"


def format_signed_value(value, unit=""):
    if pd.isna(value):
        return "-"
    if value > 0:
        prefix = "▲"
    elif value < 0:
        prefix = "▼"
    else:
        prefix = "•"
    return f"{prefix} {abs(value):,.2f} {unit}".strip()


def colorize_change(value):
    text = str(value).strip()
    if text.startswith("▲"):
        css_class = "change-up"
    if text.startswith("▼"):
        css_class = "change-down"
    elif text.startswith("•"):
        css_class = "change-flat"
    elif not text.startswith("▲"):
        return html.escape(text)
    return f'<span class="{css_class}">{html.escape(text)}</span>'


def map_dataframe(frame, function):
    """Apply a cell formatter across old and new pandas versions."""
    if hasattr(frame, "map"):
        return frame.map(function)
    return frame.applymap(function)


# =========================================================
# 화면 렌더링
# =========================================================
def render_kpis(df, show_related=True):
    snapshot = get_snapshot(df)
    metadata = get_item_meta(df)
    cols = st.columns(4)

    for index, row in snapshot.iterrows():
        meta = metadata.get(row["item"], {})
        related = meta.get("related") or "-"
        unit = meta.get("unit") or ""
        value_unit = combined_unit(meta.get("currency"), unit)

        with cols[index % 4]:
            with st.container(border=True):
                st.markdown(f"**{row['item']}**")
                if show_related:
                    st.caption(f"사용 품목: {related}")
                if pd.isna(row["mom"]):
                    delta_badge = '<span class="metric-flat">전월 데이터 없음</span>'
                elif row["mom"] > 0:
                    delta_badge = (
                        f'<span class="metric-up">▲ 전월 대비 {abs(row["mom"]):.2f}%</span>'
                    )
                elif row["mom"] < 0:
                    delta_badge = (
                        f'<span class="metric-down">▼ 전월 대비 {abs(row["mom"]):.2f}%</span>'
                    )
                else:
                    delta_badge = '<span class="metric-flat">• 전월 대비 0.00%</span>'

                metric_html = (
                    '<div class="custom-metric">'
                    f'<div class="metric-month">{row["latest_date"].year}년 '
                    f'{row["latest_date"].month}월</div>'
                    f'<div class="metric-value">{html.escape(format_value(row["latest_value"], value_unit))}</div>'
                    f'{delta_badge}</div>'
                )
                st.markdown(
                    "<style>"
                    ".custom-metric{background:#f8f9fb;border:1px solid #e1e5eb;"
                    "border-radius:12px;padding:14px 14px 13px;margin-top:8px}"
                    ".metric-month{font-size:.82rem;color:#54616f;margin-bottom:4px}"
                    ".metric-value{font-size:1.42rem;color:#17233b;font-weight:750;"
                    "margin-bottom:8px;white-space:nowrap}"
                    ".metric-up,.metric-down,.metric-flat{display:inline-block;border-radius:999px;"
                    "padding:3px 8px;font-size:.83rem;font-weight:750}"
                    ".metric-up{color:#c62828;background:#ffebee}"
                    ".metric-down{color:#1565c0;background:#e3f2fd}"
                    ".metric-flat{color:#616161;background:#eeeeee}"
                    "</style>" + metric_html,
                    unsafe_allow_html=True,
                )


def render_summary_table(df, show_related=True):
    snapshot = get_snapshot(df)
    metadata = get_item_meta(df)

    rows = []
    for _, row in snapshot.iterrows():
        meta = metadata.get(row["item"], {})
        currency = meta.get("currency") or "-"
        unit = meta.get("unit") or "-"
        value_unit = combined_unit(meta.get("currency"), meta.get("unit"))
        summary_row = {
            "품목": row["item"],
            "통화": currency,
            "단위": unit,
            "최신 기준월": f"{row['latest_date'].year}년 {row['latest_date'].month}월",
            "최신값": format_value(row["latest_value"], value_unit),
            "전월 가격": format_value(row["prev_value"], value_unit),
            "전월 차이": format_signed_value(row["mom_difference"], value_unit),
            "전월 증감률": format_delta(row["mom"]),
            "6개월 전 가격": format_value(
                row["six_month_value"], value_unit
            ),
            "6개월 차이": format_signed_value(
                row["six_month_difference"], value_unit
            ),
            "6개월 증감률": format_delta(row["six_month_change"]),
            f"{int(row['previous_year'])}년 평균가격": format_value(
                row["previous_year_average"], value_unit
            ),
            "전년 차이": format_signed_value(
                row["previous_year_difference"], value_unit
            ),
            "전년 증감률": format_delta(row["previous_year_change"]),
            "출처": meta.get("source") or "-",
        }
        if show_related:
            summary_row = {
                "품목": summary_row.pop("품목"),
                "사용 품목": meta.get("related") or "-",
                **summary_row,
            }
        rows.append(summary_row)

    summary_df = pd.DataFrame(rows)
    change_columns = [
        "전월 차이", "전월 증감률",
        "6개월 차이", "6개월 증감률",
        "전년 차이", "전년 증감률",
    ]
    summary_html = map_dataframe(summary_df, lambda value: html.escape(str(value)))
    for column in change_columns:
        if column in summary_df.columns:
            summary_html[column] = summary_df[column].map(colorize_change)

    table_html = summary_html.to_html(index=False, escape=False, classes="comparison-table")
    comparison_css = (
        "<style>"
        ".comparison-scroll{overflow-x:auto;margin-bottom:.5rem}"
        ".comparison-table{width:100%;border-collapse:collapse;font-size:.92rem}"
        ".comparison-table th{background:#f3f5f8;color:#263238;font-weight:700;"
        "padding:10px 9px;border:1px solid #dfe3e8;white-space:nowrap}"
        ".comparison-table td{padding:9px;border:1px solid #e5e7eb;white-space:nowrap}"
        ".comparison-table tbody tr:hover{background:#fafbfc}"
        ".change-up{display:inline-block;color:#c62828;background:#fff0ef;"
        "border-radius:6px;padding:3px 7px;font-weight:750}"
        ".change-down{display:inline-block;color:#1565c0;background:#edf5ff;"
        "border-radius:6px;padding:3px 7px;font-weight:750}"
        ".change-flat{display:inline-block;color:#616161;background:#f1f3f5;"
        "border-radius:6px;padding:3px 7px;font-weight:650}"
        "</style>"
    )
    st.markdown(
        comparison_css + '<div class="comparison-scroll">' + table_html + "</div>",
        unsafe_allow_html=True,
    )


def render_monthly_table(df, show_related=True, comparison_df=None, key="monthly"):
    comparison_source = comparison_df if comparison_df is not None else df
    metadata = get_item_meta(comparison_source)
    snapshot = get_snapshot(comparison_source).set_index("item")
    available_months = df["date"].drop_duplicates().sort_values().tolist()
    if not available_months:
        st.info("월별 표에 표시할 데이터가 없습니다.")
        return

    table_start, table_end = st.select_slider(
        "월별 데이터 조회 기간",
        options=available_months,
        value=(available_months[0], available_months[-1]),
        format_func=lambda value: f"{value.year}년 {value.month}월",
        key=f"{key}_table_period",
    )
    table_df = df[df["date"].between(table_start, table_end)].copy()
    month_dates = table_df["date"].drop_duplicates().sort_values().tolist()
    month_order = [f"{value.year}년 {value.month}월" for value in month_dates]
    month_headers = {
        label: (f"{value.year}년", f"{value.month}월")
        for label, value in zip(month_order, month_dates)
    }

    pivot = (
        table_df.pivot_table(index="item", columns="ym", values="value", aggfunc="last")
        .reindex(columns=month_order)
        .reset_index()
        .rename(columns={"item": "품목"})
    )

    if show_related:
        pivot.insert(
            1,
            "사용 품목",
            pivot["품목"].map(lambda x: metadata.get(x, {}).get("related") or "-"),
        )
    currency_position = 2 if show_related else 1
    pivot.insert(
        currency_position,
        "통화",
        pivot["품목"].map(lambda x: metadata.get(x, {}).get("currency") or "-"),
    )
    pivot.insert(
        currency_position + 1,
        "단위",
        pivot["품목"].map(lambda x: metadata.get(x, {}).get("unit") or "-"),
    )
    pivot["전월 차이"] = pivot["품목"].map(
        lambda item: snapshot.loc[item, "mom_difference"] if item in snapshot.index else pd.NA
    )
    pivot["전월 증감률"] = pivot["품목"].map(
        lambda item: snapshot.loc[item, "mom"] if item in snapshot.index else pd.NA
    )
    pivot["전년 평균가격"] = pivot["품목"].map(
        lambda item: snapshot.loc[item, "previous_year_average"] if item in snapshot.index else pd.NA
    )
    pivot["전년 차이"] = pivot["품목"].map(
        lambda item: snapshot.loc[item, "previous_year_difference"] if item in snapshot.index else pd.NA
    )
    pivot["전년 증감률"] = pivot["품목"].map(
        lambda item: snapshot.loc[item, "previous_year_change"] if item in snapshot.index else pd.NA
    )

    info_columns = ["품목"]
    if show_related:
        info_columns.append("사용 품목")
    info_columns.extend(["통화", "단위"])
    previous_years = snapshot["previous_year"].dropna()
    previous_year_label = (
        f"{int(previous_years.max())}년 평균가격"
        if not previous_years.empty
        else "전년도 평균가격"
    )

    display_columns = (
        info_columns
        + month_order
        + ["전월 차이", "전월 증감률", "전년 평균가격", "전년 차이", "전년 증감률"]
    )
    monthly_view = pivot[display_columns].copy()
    monthly_view.columns = pd.MultiIndex.from_tuples(
        [("기본 정보", column) for column in info_columns]
        + [month_headers[column] for column in month_order]
        + [
            ("전월 대비", "차이"),
            ("전월 대비", "증감률"),
            ("전년 대비", previous_year_label),
            ("전년 대비", "차이"),
            ("전년 대비", "증감률"),
        ]
    )

    safe_view = map_dataframe(
        monthly_view,
        lambda value: "-" if pd.isna(value) else html.escape(str(value))
    )
    for month in month_order:
        month_header = month_headers[month]
        safe_view[month_header] = monthly_view[month_header].map(
            lambda value: "-" if pd.isna(value) else f"{value:,.2f}"
        )
    safe_view[("전월 대비", "차이")] = monthly_view[("전월 대비", "차이")].map(
        lambda value: colorize_change(format_signed_value(value))
    )
    safe_view[("전월 대비", "증감률")] = monthly_view[("전월 대비", "증감률")].map(
        lambda value: colorize_change(format_delta(value))
    )
    safe_view[("전년 대비", previous_year_label)] = monthly_view[
        ("전년 대비", previous_year_label)
    ].map(lambda value: "-" if pd.isna(value) else f"{value:,.2f}")
    safe_view[("전년 대비", "차이")] = monthly_view[("전년 대비", "차이")].map(
        lambda value: colorize_change(format_signed_value(value))
    )
    safe_view[("전년 대비", "증감률")] = monthly_view[("전년 대비", "증감률")].map(
        lambda value: colorize_change(format_delta(value))
    )

    table_html = safe_view.to_html(
        index=False,
        escape=False,
        border=0,
        classes="monthly-comparison-table",
    )
    monthly_css = (
        "<style>"
        ".monthly-scroll{overflow-x:auto;border:1px solid #d9dee7;border-radius:10px}"
        ".monthly-comparison-table{width:max-content;min-width:100%;border-collapse:collapse;"
        "font-size:1rem;color:#17233b}"
        ".monthly-comparison-table th{background:#e9edf3;color:#17233b;font-weight:800;"
        "padding:10px 9px;border:1px solid #cfd6df;white-space:nowrap;text-align:center}"
        ".monthly-comparison-table thead tr:first-child th{background:#dce3ec;"
        "font-size:1.02rem;border-bottom:2px solid #aeb8c6}"
        ".monthly-comparison-table td{padding:10px 9px;border:1px solid #e0e4ea;"
        "white-space:nowrap;text-align:right;font-weight:520}"
        ".monthly-comparison-table td:first-child{text-align:left;font-weight:750}"
        ".monthly-comparison-table tbody tr:nth-child(even){background:#f8fafc}"
        ".monthly-comparison-table tbody tr:hover{background:#fff8e6}"
        ".monthly-comparison-table .change-up{color:#c62828;background:#ffebee;"
        "border-radius:6px;padding:4px 7px;font-weight:800}"
        ".monthly-comparison-table .change-down{color:#1565c0;background:#e3f2fd;"
        "border-radius:6px;padding:4px 7px;font-weight:800}"
        ".monthly-comparison-table .change-flat{color:#616161;background:#eeeeee;"
        "border-radius:6px;padding:4px 7px;font-weight:700}"
        "</style>"
    )
    st.markdown(
        monthly_css + '<div class="monthly-scroll">' + table_html + "</div>",
        unsafe_allow_html=True,
    )


def render_chart(df, key):
    available_months = df["date"].drop_duplicates().sort_values().tolist()
    if not available_months:
        st.info("차트에 표시할 월별 데이터가 없습니다.")
        return

    chart_start, chart_end = st.select_slider(
        "차트 조회 기간",
        options=available_months,
        value=(available_months[0], available_months[-1]),
        format_func=lambda value: f"{value.year}년 {value.month}월",
        key=f"{key}_chart_period",
    )
    chart_source = df[df["date"].between(chart_start, chart_end)].copy()

    items = sorted(chart_source["item"].unique().tolist())
    selected = st.multiselect(
        "차트 품목 선택",
        options=items,
        default=items[: min(4, len(items))],
        key=f"{key}_items",
    )

    if not selected:
        st.info("차트에 표시할 품목을 1개 이상 선택해 주세요.")
        return

    normalize = st.toggle(
        "변동률 비교용 지수화 (첫 달=100)",
        value=False,
        key=f"{key}_normalize",
        help="단위가 서로 다른 원자재나 환율의 움직임을 한 차트에서 비교할 때 사용합니다.",
    )

    chart_df = chart_source[chart_source["item"].isin(selected)].copy()
    if "currency" not in chart_df.columns:
        chart_df["currency"] = ""
    chart_df["display_unit"] = chart_df.apply(
        lambda row: combined_unit(row.get("currency", ""), row.get("unit", "")),
        axis=1,
    )

    if normalize:
        chart_df["chart_value"] = chart_df.groupby("item")["value"].transform(
            lambda x: x / x.iloc[0] * 100 if x.iloc[0] != 0 else x
        )
        y_title = "지수 (첫 달=100)"
    else:
        chart_df["chart_value"] = chart_df["value"]
        y_title = "값"

    fig = go.Figure()
    line_colors = [
        "#1565C0", "#EF5350", "#2E7D32", "#8E24AA",
        "#F57C00", "#00838F", "#6D4C41", "#C2185B",
    ]
    label_endpoints = []

    for item_index, item in enumerate(selected):
        item_df = chart_df[chart_df["item"] == item].sort_values("date")
        line_color = line_colors[item_index % len(line_colors)]
        fig.add_trace(
            go.Scatter(
                x=item_df["date"],
                y=item_df["chart_value"],
                mode="lines+markers",
                name=item,
                line={"width": 3, "color": line_color},
                marker={"size": 7, "color": line_color},
                customdata=item_df[["value", "display_unit"]],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "기준월: %{x|%Y년 %m월}<br>"
                    "값: %{customdata[0]:,.2f} %{customdata[1]}"
                    "<extra></extra>"
                ),
            )
        )
        if not item_df.empty:
            last_row = item_df.iloc[-1]
            label_endpoints.append({
                "item": item,
                "date": last_row["date"],
                "value": float(last_row["chart_value"]),
                "color": line_color,
            })

    # 끝점 값이 가까운 지표들은 라벨을 위아래로 자동 분산합니다.
    if label_endpoints:
        endpoint_values = [point["value"] for point in label_endpoints]
        value_range = max(endpoint_values) - min(endpoint_values)
        collision_threshold = max(value_range * 0.06, abs(max(endpoint_values)) * 0.008, 1e-9)
        sorted_points = sorted(label_endpoints, key=lambda point: point["value"])
        label_groups = []
        current_group = [sorted_points[0]]

        for point in sorted_points[1:]:
            if point["value"] - current_group[-1]["value"] <= collision_threshold:
                current_group.append(point)
            else:
                label_groups.append(current_group)
                current_group = [point]
        label_groups.append(current_group)

        for group in label_groups:
            group_size = len(group)
            for group_index, point in enumerate(group):
                vertical_offset = int((group_index - (group_size - 1) / 2) * 34)
                fig.add_annotation(
                    x=point["date"],
                    y=point["value"],
                    text=f"<b>{html.escape(str(point['item']))}</b>",
                    showarrow=True,
                    arrowhead=0,
                    arrowwidth=2,
                    arrowcolor=point["color"],
                    ax=42,
                    ay=vertical_offset,
                    xanchor="left",
                    font={"color": point["color"], "size": 13},
                    bgcolor="rgba(255,255,255,0.94)",
                    bordercolor=point["color"],
                    borderwidth=1,
                    borderpad=4,
                )

    fig.update_layout(
        height=520,
        margin={"l": 20, "r": 230, "t": 20, "b": 20},
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
        },
        xaxis={"title": "", "tickformat": "%Y년 %m월"},
        yaxis={"title": y_title},
    )

    st.plotly_chart(fig, use_container_width=True)


def render_section(df, key, months_to_show):
    if df.empty:
        st.warning("표시할 데이터가 없습니다.")
        return

    filtered = filter_period(df, months_to_show)
    show_related = key != "환율"

    latest = filtered["date"].max()
    earliest = filtered["date"].min()
    st.caption(
        f"표시 기간: {earliest.year}년 {earliest.month}월 ~ "
        f"{latest.year}년 {latest.month}월 "
        f"/ {filtered['item'].nunique()}개 품목"
    )

    st.markdown("### 요약 지표")
    # 비교 계산에는 표시 기간 밖의 직전 월/전년도 데이터도 필요합니다.
    render_kpis(df, show_related=show_related)

    st.divider()
    st.markdown("### 월별 데이터")
    render_monthly_table(
        df,
        show_related=show_related,
        comparison_df=df,
        key=key,
    )

    st.divider()
    st.markdown("### 품목별 추이")
    # 차트 기간은 업로드된 전체 월 범위에서 별도로 선택합니다.
    render_chart(df, key)


# =========================================================
# 엑셀 템플릿 생성
# =========================================================
def make_template_bytes():
    month_range = pd.date_range(
        pd.Timestamp(NOW.year, NOW.month, 1) - pd.DateOffset(months=11),
        periods=12,
        freq="MS",
    )

    raw_items = [
        ("알루미늄", "USD/MT", "알루미늄 리드지", "LME"),
        ("브렌트유", "USD/bbl", "석유화학계 원자재", "공공 데이터"),
        ("나프타", "USD/MT", "PP수지·HEMA·GMMA 등", "공공 데이터"),
        ("프로필렌", "USD/MT", "PP수지·블리스터 케이스", "공공 데이터"),
        ("펄프", "USD/MT", "세일즈팩·라벨 원지", "공공 데이터"),
        ("폐골판지", "KRW/kg", "카톤박스", "공공 데이터"),
    ]
    fx_items = [
        ("USD/KRW", "KRW", "수입 원자재·부자재", "한국은행"),
        ("EUR/KRW", "KRW", "유럽 수입품", "한국은행"),
        ("JPY/KRW", "KRW/100JPY", "일본 수입품", "한국은행"),
    ]

    raw_rows = []
    for idx, (item, unit, related, source) in enumerate(raw_items):
        base = 100 + idx * 10
        for month_index, month in enumerate(month_range):
            raw_rows.append({
                "품목": item,
                "날짜": month,
                "값": round(base * (1 + month_index * 0.01), 2),
                "단위": unit,
                "사용 품목": related,
                "출처": source,
            })

    fx_rows = []
    for idx, (item, unit, related, source) in enumerate(fx_items):
        base = [1380, 1510, 920][idx]
        for month_index, month in enumerate(month_range):
            fx_rows.append({
                "품목": item,
                "날짜": month,
                "값": round(base * (1 + month_index * 0.002), 2),
                "단위": unit,
                "사용 품목": related,
                "출처": source,
            })

    guide = pd.DataFrame({
        "항목": [
            "필수 시트",
            "필수 열",
            "선택 열",
            "날짜 형식",
            "중복 처리",
            "가로형 지원",
        ],
        "설명": [
            "원자재, 환율",
            "품목, 날짜, 값",
            "단위, 사용 품목, 출처",
            "2026-01-01 또는 2026-01",
            "같은 품목·같은 월이 중복되면 아래쪽 값을 사용",
            "품목 열 옆에 2026-01, 2026-02처럼 월별 열을 두어도 인식",
        ],
    })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(raw_rows).to_excel(writer, sheet_name="원자재", index=False)
        pd.DataFrame(fx_rows).to_excel(writer, sheet_name="환율", index=False)
        guide.to_excel(writer, sheet_name="작성안내", index=False)

        workbook = writer.book
        for sheet_name in ["원자재", "환율", "작성안내"]:
            ws = workbook[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for column_cells in ws.columns:
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                ws.column_dimensions[column_cells[0].column_letter].width = min(max_length + 3, 35)

    return output.getvalue()


# =========================================================
# 앱 본문
# =========================================================
st.title("원가 영향 인자 대시보드")
st.caption("엑셀 파일을 업로드하면 원자재 및 환율 데이터를 자동으로 분석합니다.")

with st.sidebar:
    st.header("데이터 설정")

    template_bytes = make_template_bytes()
    st.download_button(
        "엑셀 입력 양식 다운로드",
        data=template_bytes,
        file_name="원가_영향인자_입력양식.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    uploaded_file = st.file_uploader(
        "원가 영향인자 엑셀 업로드",
        type=["xlsx", "xlsm"],
        help="권장 시트명: 원자재, 환율 / 필수 열: 품목, 날짜, 값",
    )

    months_to_show = st.selectbox(
        "표시 기간",
        options=[6, 12, 18, 24, 36],
        index=1,
        format_func=lambda x: f"최근 {x}개월",
    )

    st.divider()
    st.markdown(
        """
        **필수 입력값**
        - 품목
        - 날짜
        - 값

        **선택 입력값**
        - 단위
        - 사용 품목
        - 출처
        """
    )

if uploaded_file is None:
    st.info("왼쪽에서 엑셀 입력 양식을 내려받아 데이터를 입력한 뒤 업로드해 주세요.")

    st.markdown("### 지원하는 엑셀 형식")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**세로형 권장**")
        st.dataframe(
            pd.DataFrame({
                "품목": ["나프타", "나프타", "USD/KRW"],
                "날짜": ["2026-06", "2026-07", "2026-07"],
                "값": [650, 670, 1385],
                "단위": ["USD/MT", "USD/MT", "KRW"],
                "사용 품목": ["PP수지", "PP수지", "수입품"],
            }),
            hide_index=True,
            use_container_width=True,
        )

    with col2:
        st.markdown("**가로형도 지원**")
        st.dataframe(
            pd.DataFrame({
                "품목": ["나프타", "USD/KRW"],
                "단위": ["USD/MT", "KRW"],
                "2026-05": [640, 1370],
                "2026-06": [650, 1380],
                "2026-07": [670, 1385],
            }),
            hide_index=True,
            use_container_width=True,
        )

    st.stop()

try:
    file_bytes = uploaded_file.getvalue()
    datasets, load_messages, sheet_names = load_excel(file_bytes)
except Exception as exc:
    st.error(f"엑셀 파일을 읽는 중 오류가 발생했습니다: {exc}")
    st.stop()

st.success(
    f"'{uploaded_file.name}' 파일을 불러왔습니다. "
    f"확인된 시트: {', '.join(sheet_names)}"
)

for message in load_messages:
    st.warning(message)

available_tabs = []
if "원자재" in datasets and not datasets["원자재"].empty:
    available_tabs.append("원자재")
if "환율" in datasets and not datasets["환율"].empty:
    available_tabs.append("환율")

if not available_tabs:
    st.error("원자재 또는 환율 데이터를 찾지 못했습니다.")
    st.stop()

tabs = st.tabs(available_tabs)

for tab, tab_name in zip(tabs, available_tabs):
    with tab:
        st.subheader(tab_name)
        render_section(
            datasets[tab_name],
            key=tab_name,
            months_to_show=months_to_show,
        )

st.divider()
st.caption(
    "업로드한 파일 안의 값만 사용하며, 외부 웹사이트나 API에는 자동 연결하지 않습니다."
)
