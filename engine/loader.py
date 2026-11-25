"""CSV 업로드 검증, 표 결합, 전처리.

흐름: load_dataset({파일이름: bytes}) -> LoadResult(dataset, reports, join)
- 파일마다 어떤 표(tasks·employees·projects·generic)인지 컬럼으로 판단하고 검증한다.
- 표준 표 3개는 업무 기준으로 다대일 결합한다. 결합 후 행 수가 그대로인지 확인한다.
- 원본 파일·원본 DataFrame은 수정하지 않는다. 문제는 예외 대신 Issue로 알린다.
- 숨은 컬럼: "_row"(원본 데이터 행 번호, 1부터), "_invalid"(형식 오류 행, 계산에서 제외).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from contracts import (
    ALLOWED_VALUES,
    CHECK_IF_MISSING,
    DATE_COLUMNS,
    DATE_FORMAT,
    DISPLAY_NAMES,
    ID_COLUMN,
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    NONCONFORMANCE_CATEGORY,
    NUMBER_COLUMNS,
    REQUIRED_VALUES,
    STATUS_DONE,
    TABLE_COLUMNS,
    UNASSIGNED,
    CleaningResult,
    ColumnInfo,
    Issue,
    JoinReport,
    ValidationReport,
)

Source = Union[bytes, str, Path]
MAX_LISTED_ROWS = 50
NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")

# 일반 CSV 전용: 흔한 날짜 표기(2026/01/05, 2026.1.5, 2026-01-05 13:45)와 월 표기(2026/01)를 YYYY-MM-DD·YYYY-MM으로 맞춘다.
# 20260105처럼 구분자 없는 8자리는 일반 숫자와 구분할 수 없어 바꾸지 않는다.
GENERIC_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
GENERIC_MONTH_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
# 연도가 뒤에 오는 날짜 (01/15/2026, 15.01.2026 13:45). 월/일 순서는 값을 보고 정한다
GENERIC_DMY_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
# 일반 CSV 전용: 숫자 앞뒤의 통화·단위 기호 (₩1,200 / 1,200원 / $15 / 12.5%)
UNIT_RE = re.compile(r"^\s*[₩$]?\s*(.*?)\s*(원|%)?\s*$")
# 앞자리 0이 있는 코드(우편번호 00123 등)는 숫자로 바꾸지 않는다
LEADING_ZERO_RE = re.compile(r"^0\d")
DELIMITERS = [",", ";", "\t", "|"]


# ---------------------------------------------------------------------------
# 결과 묶음
# ---------------------------------------------------------------------------
# Dataset 구조 버전. load_dataset·build_dataset가 만드는 표 구조(파생 컬럼, 결합 방식, 컬럼 형식)를 바꾸면 1 올린다.
# run_tool은 버전이 다른 Dataset(예: 앱 업데이트 전에 불러와 캐시에 남은 데이터)으로는 계산하지 않는다.
DATASET_VERSION = 2  # 2: 일반 CSV 형식 오류를 행 전체가 아니라 그 칸만 제외, 연도가 뒤에 오는 날짜 인식


@dataclass
class Dataset:
    """분석 대상. main은 결합된 업무 표(standard) 또는 일반 CSV(generic)."""
    kind: str  # "standard" | "generic"
    main: pd.DataFrame
    employees: Optional[pd.DataFrame] = None
    projects: Optional[pd.DataFrame] = None
    version: int = DATASET_VERSION  # 이 데이터를 만든 엔진의 구조 버전

    @classmethod
    def generic(cls, df: pd.DataFrame) -> "Dataset":
        return cls(kind="generic", main=df)

    def table(self, name: str) -> Optional[pd.DataFrame]:
        return {"main": self.main, "employees": self.employees, "projects": self.projects}.get(name)


@dataclass
class LoadResult:
    dataset: Optional[Dataset]
    reports: list[ValidationReport] = field(default_factory=list)
    join: Optional[JoinReport] = None
    errors: list[Issue] = field(default_factory=list)  # 파일 조합 자체의 문제

    @property
    def ok(self) -> bool:
        return self.dataset is not None


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------
def _show(col: str) -> str:
    return DISPLAY_NAMES.get(col, col)


def _label(col: str) -> str:
    """사용자 안내용 컬럼 이름: '기한(due_date)'. 한국어 이름이 없으면 원래 이름."""
    name = _show(col)
    return f"{name}({col})" if name != col else col


KIND_LABEL = {"tasks": "업무(tasks)", "employees": "직원(employees)", "projects": "프로젝트(projects)"}


def _issue(level: str, code: str, message: str, mask: pd.Series, df: pd.DataFrame) -> Issue:
    hit = df.loc[mask, "_row"].tolist()
    return Issue(level=level, code=code, message=message, count=len(hit), rows=[int(r) for r in hit[:MAX_LISTED_ROWS]])


def _fail(filename: str, code: str, message: str, encoding: Optional[str] = None) -> tuple[None, ValidationReport]:
    return None, ValidationReport(filename=filename, ok=False, kind="generic", n_rows=0, n_cols=0, encoding=encoding,
                                  issues=[Issue(level="error", code=code, message=message)])


def _decode(raw: bytes) -> tuple[Optional[str], Optional[str]]:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, None


def _detect_kind(columns: list[str]) -> str:
    """컬럼이 가장 많이 겹치는 표준 표. ID 컬럼이 있고 그 표 컬럼의 절반 이상이 겹쳐야 인정.

    employee_id·department만 있는 일반 인사 CSV처럼 몇 개만 겹치는 파일은 일반 CSV로 본다.
    """
    best, best_score = "generic", 0
    for kind, cols in TABLE_COLUMNS.items():
        score = len(set(columns) & set(cols))
        if ID_COLUMN[kind] in columns and score * 2 >= len(cols) and score > best_score:
            best, best_score = kind, score
    return best


def _delimiter(text: str) -> str:
    """첫 줄에 쉼표가 없으면 세미콜론·탭·세로줄 중 가장 많은 것을 구분자로 쓴다."""
    header = text.split("\n", 1)[0]
    if "," in header:
        return ","
    counts = {d: header.count(d) for d in DELIMITERS[1:]}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _dtype(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    return "text"


def _report(df: pd.DataFrame, filename: str, kind: str, encoding: Optional[str], issues: list[Issue]) -> ValidationReport:
    visible = [c for c in df.columns if not c.startswith("_")]
    columns = [ColumnInfo(name=c, dtype=_dtype(df[c]), missing=int(df[c].isna().sum())) for c in visible]
    ok = not any(i.level == "error" for i in issues)
    return ValidationReport(filename=filename, ok=ok, kind=kind, n_rows=len(df), n_cols=len(visible),
                            encoding=encoding, columns=columns, issues=issues)


CELL_ONLY = "그 칸만 빈칸으로 보고 이 컬럼 계산에서 제외합니다 (같은 행의 다른 컬럼은 그대로 씁니다)."


def _to_number(df: pd.DataFrame, col: str, issues: list[Issue], allow_negative: bool = False,
               row_level: bool = True) -> None:
    """숫자로 바꾼다. 숫자가 아닌 값은 빈칸(NA)이 된다.

    row_level=True(표준 표): 그 행을 형식 오류로 보고 모든 계산에서 뺀다 (업무 표는 행 단위로 믿을 수 있어야 함).
    row_level=False(일반 CSV): 그 칸만 뺀다. 다른 컬럼의 글자 섞인 값 때문에 상관없는 컬럼 계산이 틀어지지 않게.
    """
    text = df[col]
    clean = text.fillna("").str.replace(",", "", regex=False)
    bad = text.notna() & ~clean.str.match(NUMBER_RE)
    if bad.any():
        what = "해당 행은 계산에서 제외합니다." if row_level else CELL_ONLY
        issues.append(_issue("warning", "invalid_number",
                             f"'{_label(col)}'에 숫자가 아닌 값이 있습니다 (예: N/A, 없음). {what}", bad, df))
        if row_level:
            df.loc[bad, "_invalid"] = True
    df[col] = pd.to_numeric(clean.where(~bad & text.notna()), errors="coerce").astype("Float64")
    if not allow_negative:
        neg = (df[col] < 0).fillna(False).astype(bool)
        if neg.any():
            issues.append(_issue("warning", "negative_number", f"'{_label(col)}'에 음수가 있습니다. 해당 행은 계산에서 제외합니다.",
                                 neg, df))
            df.loc[neg, "_invalid"] = True


# ---------------------------------------------------------------------------
# 1. 파일 하나 읽기
# ---------------------------------------------------------------------------
def read_table(source: Source, filename: str = "") -> tuple[Optional[pd.DataFrame], ValidationReport]:
    """CSV 하나를 읽고 검증한다. error가 있으면 DataFrame 대신 None."""
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    filename = filename or (Path(source).name if not isinstance(source, bytes) else "")

    if len(raw) > MAX_UPLOAD_BYTES:
        return _fail(filename, "too_large", f"파일이 너무 큽니다. {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 이하만 올릴 수 있습니다.")
    if not raw.strip():
        return _fail(filename, "empty_file", "빈 파일입니다. 헤더와 데이터가 있는 CSV를 올려 주세요.")
    text, encoding = _decode(raw)
    if text is None:
        return _fail(filename, "encoding", "글자 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949(엑셀 기본) CSV만 지원합니다.")
    try:
        df = pd.read_csv(io.StringIO(text), sep=_delimiter(text), dtype=str, keep_default_na=False,
                         skipinitialspace=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        return _fail(filename, "parse_error", f"CSV 형식을 읽을 수 없습니다: {exc}", encoding)

    df.columns = [str(c).strip() for c in df.columns]
    # 줄 끝 쉼표 때문에 생긴 이름 없는 빈 컬럼은 버린다 (값이 하나라도 있으면 남긴다)
    empty_unnamed = [c for c in df.columns if c.startswith("Unnamed:") and (df[c].str.strip() == "").all()]
    df = df.drop(columns=empty_unnamed)
    if len(df) == 0:
        return _fail(filename, "no_rows", "헤더만 있고 데이터 행이 없습니다.", encoding)
    if len(df) > MAX_ROWS:
        return _fail(filename, "too_many_rows", f"행이 너무 많습니다({len(df):,}행). {MAX_ROWS:,}행 이하만 분석합니다.", encoding)
    if len(set(df.columns)) != len(df.columns):
        return _fail(filename, "duplicate_headers", "같은 이름의 컬럼이 두 번 이상 있습니다.", encoding)
    if any(c.startswith("_") for c in df.columns):
        return _fail(filename, "reserved_header", "'_'로 시작하는 컬럼 이름은 쓸 수 없습니다.", encoding)

    for col in df.columns:
        df[col] = df[col].str.strip().replace("", pd.NA)
    df.insert(0, "_row", range(1, len(df) + 1))
    df["_invalid"] = False

    kind = _detect_kind(list(df.columns))
    if kind == "generic":
        return _validate_generic(df, filename, encoding)
    return _validate_standard(df, kind, filename, encoding)


def _validate_standard(df: pd.DataFrame, kind: str, filename: str, encoding: Optional[str]):
    issues: list[Issue] = []
    missing = [c for c in TABLE_COLUMNS[kind] if c not in df.columns]
    if missing:
        report = _report(df, filename, kind, encoding, [Issue(
            level="error", code="missing_columns",
            message=f"{KIND_LABEL[kind]} 파일에 필요한 컬럼이 없습니다: {', '.join(_label(c) for c in missing)}")])
        return None, report

    req = REQUIRED_VALUES[kind]
    miss = df[req].isna().any(axis=1)
    if miss.any():
        blank = [c for c in req if df.loc[miss, c].isna().any()]  # 실제로 비어 있는 컬럼만 안내
        issues.append(_issue("warning", "missing_values",
                             f"꼭 있어야 할 값이 비어 있습니다 ({', '.join(_label(c) for c in blank)}). 해당 행은 계산에서 제외합니다.",
                             miss, df))
        df.loc[miss, "_invalid"] = True

    for col in CHECK_IF_MISSING[kind]:
        m = df[col].isna()
        if m.any():
            note = f" ({UNASSIGNED})" if col == "assignee_id" else ""
            issues.append(_issue("warning", "needs_check",
                                 f"'{_label(col)}' 빈칸이 있습니다{note}. 값은 그대로 두고 확인 필요로 표시합니다.", m, df))

    for col in DATE_COLUMNS[kind]:
        text = df[col]
        parsed = pd.to_datetime(text, format=DATE_FORMAT, errors="coerce")
        bad = text.notna() & parsed.isna()
        if bad.any():
            issues.append(_issue("warning", "invalid_date", f"'{_label(col)}' 날짜 형식이 YYYY-MM-DD가 아닙니다. 해당 행은 계산에서 제외합니다.",
                                 bad, df))
            df.loc[bad, "_invalid"] = True
        df[col] = parsed

    for col in NUMBER_COLUMNS[kind]:
        _to_number(df, col, issues)

    for col, allowed in ALLOWED_VALUES[kind].items():
        bad = df[col].notna() & ~df[col].isin(allowed)
        if bad.any():
            issues.append(_issue("warning", "invalid_value", f"'{_label(col)}' 값은 {', '.join(allowed)} 중 하나여야 합니다. 해당 행은 계산에서 제외합니다.",
                                 bad, df))
            df.loc[bad, "_invalid"] = True

    if kind == "tasks":
        issues.extend(_task_consistency(df))
    issues.extend(_duplicate_issues(df, ID_COLUMN[kind]))
    return df, _report(df, filename, kind, encoding, issues)


def _task_consistency(df: pd.DataFrame) -> list[Issue]:
    issues = []
    done = (df["status"] == STATUS_DONE).fillna(False).astype(bool)
    nonconf = (df["category"] == NONCONFORMANCE_CATEGORY).fillna(False).astype(bool)
    checks = [
        ("warning", "due_before_start", "기한이 시작일보다 앞선 업무가 있습니다.", df["due_date"] < df["start_date"]),
        ("warning", "done_without_date", "상태는 '완료'인데 완료일이 비어 있습니다.", done & df["completed_at"].isna()),
        ("warning", "date_without_done", "완료일이 있는데 상태가 '완료'가 아닙니다 (상태 기준으로 계산).", ~done & df["completed_at"].notna()),
        ("warning", "issue_type_missing", f"'{NONCONFORMANCE_CATEGORY}' 업무인데 문제유형이 비어 있습니다.", nonconf & df["issue_type"].isna()),
        ("info", "structural_blank", "미완료 업무의 완료일 빈칸은 정상입니다 (구조적 공백).", ~done & df["completed_at"].isna()),
        ("info", "structural_blank", f"분류가 '{NONCONFORMANCE_CATEGORY}'이 아닌 업무의 문제유형 빈칸은 정상입니다 (구조적 공백).",
         ~nonconf & df["issue_type"].isna()),
    ]
    for level, code, msg, mask in checks:
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            issues.append(_issue(level, code, msg, mask, df))
    return issues


def _validate_generic(df: pd.DataFrame, filename: str, encoding: Optional[str]):
    issues: list[Issue] = []
    for col in [c for c in df.columns if not c.startswith("_")]:
        nonnull = df[col].dropna()
        if nonnull.empty:
            continue
        if _normalize_dates(df, col, nonnull, issues):
            continue
        if nonnull.str.match(LEADING_ZERO_RE).any() and not nonnull.str.contains(".", regex=False).any():
            continue  # 00123 같은 코드는 글자로 둔다
        stripped = nonnull.str.extract(UNIT_RE, expand=True)
        numbers = stripped[0].str.replace(",", "", regex=False)
        if numbers.str.match(NUMBER_RE).mean() >= 0.5:
            units = sorted(set(stripped[1].dropna()) | ({"₩"} if nonnull.str.contains("₩").any() else set())
                           | ({"$"} if nonnull.str.contains("$", regex=False).any() else set()))
            if units:
                df[col] = df[col].str.extract(UNIT_RE, expand=True)[0].where(df[col].notna())
                issues.append(Issue(level="info", code="unit_removed",
                                    message=f"'{_label(col)}'의 단위 기호({', '.join(units)})를 빼고 숫자로 읽었습니다.",
                                    count=int(nonnull.str.contains(r"[₩$원%]").sum())))
            _to_number(df, col, issues, allow_negative=True, row_level=False)
    issues.extend(_duplicate_issues(df, None))
    return df, _report(df, filename, "generic", encoding, issues)


def _normalize_dates(df: pd.DataFrame, col: str, nonnull: pd.Series, issues: list[Issue]) -> bool:
    """값 절반 이상이 날짜·월 표기면 YYYY-MM-DD(또는 YYYY-MM) 글자로 맞춘다. 바꿨으면 True."""
    def plausible(year: pd.Series, mon: pd.Series) -> pd.Series:
        return (year.astype("Float64").between(1900, 2100) & mon.astype("Float64").between(1, 12)).fillna(False).astype(bool)

    def to_iso(y: pd.Series, m: pd.Series, d: pd.Series) -> tuple[pd.Series, pd.Series]:
        iso = y + "-" + m.str.zfill(2) + "-" + d.str.zfill(2)
        return iso, pd.to_datetime(iso, format=DATE_FORMAT, errors="coerce").notna() & plausible(y, m)

    day = nonnull.str.extract(GENERIC_DATE_RE)
    month = nonnull.str.extract(GENERIC_MONTH_RE)
    day_iso, day_ok = to_iso(day[0], day[1], day[2])
    month_ok = plausible(month[0], month[1])
    dmy = nonnull.str.extract(GENERIC_DMY_RE)
    if day_ok.mean() < 0.5 and dmy[0].notna().mean() >= 0.5:  # 연도가 뒤: 13 이상인 쪽이 일
        a, b = dmy[0].astype("Float64"), dmy[1].astype("Float64")
        a_day, b_day = bool((a > 12).any()), bool((b > 12).any())
        if a_day == b_day:  # 둘 다 12 이하(또는 둘 다 13 이상)면 월/일 순서를 알 수 없어 바꾸지 않는다
            issues.append(Issue(level="info", code="date_ambiguous",
                                message=f"'{_label(col)}'는 월/일 순서를 알 수 없어 날짜로 바꾸지 않았습니다 (YYYY-MM-DD로 올려 주세요).",
                                count=int(dmy[0].notna().sum())))
            return False
        day_iso, day_ok = to_iso(dmy[2], dmy[1], dmy[0]) if a_day else to_iso(dmy[2], dmy[0], dmy[1])
    if day_ok.mean() >= 0.5:
        iso, good, label = day_iso, day_ok, "YYYY-MM-DD"
    elif month_ok.mean() >= 0.5:  # 1500.50 같은 소수는 월 범위(1~12)를 벗어나 여기서 걸러진다
        iso, good, label = month[0] + "-" + month[1].str.zfill(2), month_ok, "YYYY-MM"
    else:
        return False
    good = good.astype(bool)
    changed = int((good & (iso != nonnull)).sum())
    bad = nonnull.index[~good]
    df.loc[good[good].index, col] = iso[good]
    if changed:
        issues.append(Issue(level="info", code="date_normalized",
                            message=f"'{_label(col)}' 날짜 표기를 {label} 형식으로 맞췄습니다.",
                            count=changed))
    if len(bad):  # 일반 CSV: 그 칸만 빈칸으로 (날짜를 쓰는 계산에서만 빠지고, 같은 행의 숫자는 그대로 쓴다)
        mask = df.index.isin(bad)
        issues.append(_issue("warning", "invalid_date", f"'{_label(col)}'에 날짜로 읽을 수 없는 값이 있습니다. {CELL_ONLY}",
                             pd.Series(mask, index=df.index), df))
        df.loc[mask, col] = pd.NA
    return True


def _duplicate_issues(df: pd.DataFrame, id_col: Optional[str]) -> list[Issue]:
    issues: list[Issue] = []
    data_cols = [c for c in df.columns if not c.startswith("_")]
    dup = df.duplicated(subset=data_cols, keep="first")
    if dup.any():
        issues.append(_issue("warning", "duplicate_rows", "완전히 같은 행이 중복되어 있습니다 (확인 후 첫 행만 남기고 제거 가능).",
                             dup, df))
    if id_col and id_col in df.columns:
        exact = df.duplicated(subset=data_cols, keep=False)
        dup_id = df[id_col].notna() & df.duplicated(subset=[id_col], keep=False) & ~exact
        if dup_id.any():
            issues.append(_issue("warning", "duplicate_id", f"'{_label(id_col)}'는 같은데 내용이 다른 행이 있습니다. 확인이 필요합니다.",
                                 dup_id, df))
    return issues


# ---------------------------------------------------------------------------
# 2. 전처리 (사용자가 확인한 뒤에만)
# ---------------------------------------------------------------------------
def clean(df: pd.DataFrame, drop_duplicates: bool = True, drop_invalid: bool = False) -> tuple[pd.DataFrame, CleaningResult]:
    """전처리본을 만든다. 입력 df는 바꾸지 않는다."""
    out = df.copy()
    before = len(out)
    notes: list[str] = []
    removed_dup = removed_invalid = 0
    if drop_duplicates:
        data_cols = [c for c in out.columns if not c.startswith("_")]
        dup = out.duplicated(subset=data_cols, keep="first")
        removed_dup = int(dup.sum())
        out = out.loc[~dup]
        if removed_dup:
            notes.append(f"완전 중복 행 {removed_dup}개 제거 (첫 행 유지)")
    if drop_invalid:
        bad = out["_invalid"].astype(bool)
        removed_invalid = int(bad.sum())
        out = out.loc[~bad]
        if removed_invalid:
            notes.append(f"형식 오류 행 {removed_invalid}개 제외")
    return out.reset_index(drop=True), CleaningResult(before_rows=before, after_rows=len(out), removed_duplicates=removed_dup,
                                                      removed_invalid=removed_invalid, notes=notes)


# ---------------------------------------------------------------------------
# 3. 여러 파일 읽고 결합
# ---------------------------------------------------------------------------
def load_dataset(files: dict[str, Source], drop_duplicates: bool = False) -> LoadResult:
    """{파일이름: 내용}을 받아 검증·결합한다. drop_duplicates=True면 결합 전 완전 중복 행을 제거한다."""
    reports: list[ValidationReport] = []
    tables: dict[str, list[pd.DataFrame]] = {}
    for name, src in files.items():
        df, rep = read_table(src, name)
        reports.append(rep)
        if df is not None:
            if drop_duplicates:
                df, _ = clean(df, drop_duplicates=True)
            tables.setdefault(rep.kind, []).append(df)

    def fail(code: str, msg: str) -> LoadResult:
        return LoadResult(dataset=None, reports=reports, errors=[Issue(level="error", code=code, message=msg)])

    if any(not r.ok for r in reports):
        bad = ", ".join(r.filename or "(이름 없음)" for r in reports if not r.ok)
        return fail("file_errors", f"읽을 수 없는 파일이 있습니다 ({bad}). 파일별 검증 결과를 확인해 주세요.")
    if not tables:
        return fail("no_files", "분석할 파일이 없습니다.")
    for kind, dfs in tables.items():
        if len(dfs) > 1:
            names = ", ".join(r.filename for r in reports if r.ok and r.kind == kind)
            if kind == "generic":
                return fail("duplicate_kind", f"일반 CSV는 한 번에 하나만 분석할 수 있습니다 ({names}). 하나만 올려 주세요.")
            return fail("duplicate_kind", f"{KIND_LABEL[kind]} 파일이 {len(dfs)}개입니다 ({names}). 종류마다 하나씩만 올려 주세요.")

    one = {k: v[0] for k, v in tables.items()}
    if "generic" in one:
        if len(one) > 1:
            return fail("mixed_files", "표준 파일(업무·직원·프로젝트)과 일반 CSV는 함께 분석할 수 없습니다. 일반 CSV는 하나만 올려 주세요.")
        return LoadResult(dataset=Dataset.generic(one["generic"]), reports=reports)
    if "tasks" not in one:
        if len(one) == 1:  # 직원표나 프로젝트표 하나만 → 일반 CSV처럼 분석
            return LoadResult(dataset=Dataset.generic(next(iter(one.values()))), reports=reports)
        return fail("tasks_required", "직원·프로젝트 파일을 결합하려면 업무(tasks) 파일이 필요합니다.")

    dataset, join = build_dataset(one["tasks"], one.get("employees"), one.get("projects"))
    return LoadResult(dataset=dataset, reports=reports, join=join)


def build_dataset(tasks: pd.DataFrame, employees: Optional[pd.DataFrame], projects: Optional[pd.DataFrame]
                  ) -> tuple[Optional[Dataset], JoinReport]:
    """업무 기준으로 직원·프로젝트를 다대일 결합한다. 예산은 업무 행에 붙이지 않는다(중복 합산 방지)."""
    issues: list[Issue] = []
    view = tasks.copy()

    def report(ok: bool, rows: int) -> JoinReport:
        return JoinReport(ok=ok, tasks_rows=len(tasks), joined_rows=rows, has_employees=employees is not None,
                          has_projects=projects is not None, issues=issues)

    for name, dim, key in (("직원", employees, "employee_id"), ("프로젝트", projects, "project_id")):
        if dim is None:
            continue
        valid = dim.loc[~dim["_invalid"].astype(bool) & dim[key].notna()]
        dup = valid[key].duplicated(keep=False)
        if dup.any():
            ids = ", ".join(sorted(valid.loc[dup, key].unique())[:10])
            issues.append(Issue(level="error", code="duplicate_key",
                                message=f"{name} 표의 {_label(key)}가 중복됩니다({ids}). 결합하면 업무 행이 늘어나므로 결합하지 않습니다.",
                                count=int(dup.sum())))
            return None, report(False, len(tasks))

    if employees is not None:
        emp = employees.loc[~employees["_invalid"].astype(bool), ["employee_id", "department", "role", "hourly_labor_cost"]]
        view = view.merge(emp, how="left", left_on="assignee_id", right_on="employee_id", validate="many_to_one")
        view = view.drop(columns="employee_id")
        orphan = view["assignee_id"].notna() & view["department"].isna()
        if orphan.any():
            issues.append(_issue("warning", "orphan_assignee", "직원 표에 없는 담당자ID가 있습니다. 부서는 '미확인'으로 둡니다.",
                                 orphan, view))
        view.loc[view["assignee_id"].isna(), "department"] = UNASSIGNED
        view.loc[orphan, "department"] = "미확인"
        labor = view["actual_mh"] * view["hourly_labor_cost"]
        no_rate = (view["actual_mh"] == 0).fillna(False).astype(bool)
        view["labor_cost"] = labor.mask(no_rate & labor.isna(), 0)
        unknown = view["labor_cost"].isna() & view["actual_mh"].notna()
        if unknown.any():
            issues.append(_issue("warning", "labor_cost_unknown", "실적MH가 있지만 담당자 단가를 알 수 없어 인건비를 계산할 수 없는 업무가 있습니다.",
                                 unknown, view))
        view["total_cost"] = view["labor_cost"] + view["actual_nonlabor_cost"]

    if projects is not None:
        prj = projects.loc[~projects["_invalid"].astype(bool),
                           ["project_id", "project_name", "lead_department", "project_type", "status"]]
        view = view.merge(prj.rename(columns={"status": "project_status"}), how="left", on="project_id",
                          validate="many_to_one")
        orphan = view["project_id"].notna() & view["project_status"].isna()
        if orphan.any():
            issues.append(_issue("warning", "orphan_project", "프로젝트 표에 없는 프로젝트ID가 있습니다.", orphan, view))
        issues.append(Issue(level="info", code="budget_not_joined",
                            message="예산은 업무 행에 붙이지 않고 프로젝트 표에서 프로젝트당 한 번만 씁니다 (중복 합산 방지)."))

    if len(view) != len(tasks):
        issues.append(Issue(level="error", code="row_count_changed",
                            message=f"결합 후 행 수가 바뀌었습니다 ({len(tasks)} → {len(view)}). 결합 결과를 쓰지 않습니다."))
        return None, report(False, len(view))

    view["mh_over"] = (view["actual_mh"] - view["planned_mh"]).clip(lower=0)
    view["start_month"] = view["start_date"].dt.strftime("%Y-%m")
    view["completed_month"] = view["completed_at"].dt.strftime("%Y-%m")
    return Dataset(kind="standard", main=view, employees=employees, projects=projects), report(True, len(view))
