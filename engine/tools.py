"""분석 도구 9개.

규칙
- 입력: Dataset + contracts의 *Params 모델 / 출력: 항상 ToolResult (예외 대신 error에 사유).
- 컬럼 이름은 영어(내부 이름)와 한국어(DISPLAY_NAMES)를 모두 받는다. 없는 컬럼이면 실행하지 않는다.
- 자유 텍스트(업무명·프로젝트명)는 결과 rows에 넣지 않는다 (프롬프트 주입 방지). 화면은 ID로 이름을 찾아 쓴다.
- rows의 첫 키는 범주(차트 x축), 나머지 숫자 키는 값. 순위형 도구는 rows[0]이 1위.
- 형식 오류 행(_invalid)은 계산에서 제외하고 used_rows에 실제 사용 행 수를 넣는다.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import pandas as pd

from contracts import (
    CHECK_IF_MISSING,
    DATE_FORMAT,
    DISPLAY_NAMES,
    FREE_TEXT_COLUMNS,
    NONCONFORMANCE_CATEGORY,
    REQUIRED_VALUES,
    ROUND_DIGITS,
    STATUS_DONE,
    UNASSIGNED,
    AggregateParams,
    CheckMissingParams,
    CompletionRateParams,
    ComparePeriodsParams,
    CostVsBudgetParams,
    CountOverdueParams,
    FindRecurringParams,
    MhOverrunParams,
    RatioParams,
    SummarizeByCategoryParams,
    ToolResult,
)
from engine.loader import Dataset

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OP_LABEL = {"sum": "합계", "mean": "평균", "count": "개수", "max": "최댓값", "min": "최솟값"}
_TO_INTERNAL = {v: k for k, v in DISPLAY_NAMES.items()}
NEEDS_STANDARD = "업무(tasks) 파일이 있어야 쓸 수 있는 도구입니다."


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def r(x: Any) -> Optional[float]:
    """소수 둘째 자리 사사오입. NaN/None은 None."""
    if x is None or pd.isna(x):
        return None
    q = Decimal(1).scaleb(-ROUND_DIGITS)
    return float(Decimal(str(float(x))).quantize(q, rounding=ROUND_HALF_UP))


def _native(v: Any) -> Any:
    if v is None or (not isinstance(v, (list, str)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime(DATE_FORMAT)
    if hasattr(v, "item"):
        return v.item()
    return v


def _clean_rows(rows: list[dict]) -> list[dict]:
    return [{k: _native(v) for k, v in row.items()} for row in rows]


def _show(col: str) -> str:
    return DISPLAY_NAMES.get(col, col)


def _resolve(df: pd.DataFrame, name: Optional[str]) -> Optional[str]:
    """영어·한국어 컬럼 이름을 실제 컬럼으로. 없거나 숨은 컬럼이면 None."""
    if not name:
        return None
    name = name.strip()
    if name.startswith("_"):
        return None
    for cand in (name, _TO_INTERNAL.get(name)):
        if cand and cand in df.columns:
            return cand
    return None


def _err(tool: str, p: Any, msg: str) -> ToolResult:
    return ToolResult(tool=tool, params=p.model_dump(), error=msg)


def _missing_col(tool: str, p: Any, name: str, df: pd.DataFrame) -> ToolResult:
    cols = [_show(c) for c in df.columns if not c.startswith("_") and c not in FREE_TEXT_COLUMNS]
    return _err(tool, p, f"'{name}' 컬럼이 데이터에 없습니다. 사용할 수 있는 컬럼: {', '.join(cols)}")


def _group_col(tool: str, p: Any, df: pd.DataFrame, name: str) -> tuple[Optional[str], Optional[ToolResult]]:
    col = _resolve(df, name)
    if col is None:
        return None, _missing_col(tool, p, name, df)
    if col in FREE_TEXT_COLUMNS:
        return None, _err(tool, p, f"'{name}'는 자유 텍스트 컬럼이라 묶음 기준으로 쓸 수 없습니다. ID 컬럼을 쓰세요.")
    return col, None


def _parse_date(s: Optional[str]) -> Optional[pd.Timestamp]:
    if not s or not DATE_RE.match(s):
        return None
    ts = pd.to_datetime(s, format=DATE_FORMAT, errors="coerce")
    return None if pd.isna(ts) else ts


MAX_LISTED_VALUES = 20


def _is_dateish(s: pd.Series) -> bool:
    """날짜·월 글자 컬럼(일반 CSV)이면 True. 이런 컬럼은 '없는 날짜'도 정상 0건이라 값 확인을 하지 않는다."""
    text = s.dropna().astype(str)
    return not text.empty and text.str.match(r"^\d{4}-\d{2}(-\d{2})?$").mean() >= 0.5


def _unknown_values(s: pd.Series, col: str, wanted: list) -> Optional[str]:
    """글자 범주 컬럼에서 데이터에 없는 값을 찾으면 안내 문장, 모두 있으면 None.

    없는 대상(오타·없는 부서)과 '값은 맞는데 조건에 맞는 행이 0건'을 구분하기 위해 쓴다.
    """
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or _is_dateish(s):
        return None
    present = set(s.dropna().astype(str))
    missing = [str(v) for v in wanted if str(v) not in present]
    if not missing:
        return None
    msg = f"'{_show(col)}'에 {', '.join(repr(m) for m in missing)} 값이 없습니다."
    # 범주처럼 짧은 값만 안내한다 (일반 CSV의 메모 같은 문장은 목록에 넣지 않음)
    if col not in FREE_TEXT_COLUMNS and len(present) <= 60 and all(len(v) <= 30 for v in present):
        values = sorted(present)
        more = f" 외 {len(values) - MAX_LISTED_VALUES}개" if len(values) > MAX_LISTED_VALUES else ""
        msg += f" 가능한 값: {', '.join(values[:MAX_LISTED_VALUES])}{more}"
    return msg


def _match_value(s: pd.Series, value: str) -> str:
    """'품질팀'·'품질부'처럼 붙여 쓴 이름은 데이터에 있는 '품질'로 맞춘다 (없으면 그대로)."""
    present = set(s.dropna().astype(str))
    if value in present:
        return value
    for suffix in ("팀", "부서", "부"):
        if value.endswith(suffix) and value[: -len(suffix)] in present:
            return value[: -len(suffix)]
    return value


def _scope(data: pd.DataFrame, p: Any) -> tuple[pd.DataFrame, list[str], Optional[str]]:
    """department·project_id 조건을 적용한다. (조건 적용 데이터, 기준 설명, 없는 대상이면 안내 문장)."""
    desc = []
    full = data  # 값 확인은 조건을 적용하기 전 전체 데이터로 한다 (영업+P05처럼 조합이 0건인 경우는 '없는 대상'이 아님)
    for field, col in (("department", "department"), ("project_id", "project_id")):
        value = getattr(p, field, None)
        if value is None or str(value).strip() == "":
            continue
        if col not in full.columns:
            return data, desc, f"'{_show(col)}' 정보가 없어 {_show(col)} 조건을 적용할 수 없습니다 (직원 파일 필요)."
        value = _match_value(full[col], str(value).strip())
        bad = _unknown_values(full[col], col, [value])
        if bad:
            return data, desc, bad
        data = data.loc[(data[col] == value).fillna(False).astype(bool)]
        desc.append(f"{_show(col)}={value}")
    return data, desc, None


def _base(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[~df["_invalid"].astype(bool)] if "_invalid" in df.columns else df


def _done(df: pd.DataFrame) -> pd.Series:
    return (df["status"] == STATUS_DONE).fillna(False).astype(bool)


def _standard(ds: Dataset) -> bool:
    return ds.kind == "standard"


def _apply_filters(tool: str, p: Any, df: pd.DataFrame, data: pd.DataFrame,
                   filters: list) -> tuple[pd.DataFrame, list[str], Optional[ToolResult]]:
    """filters를 차례로 적용한다. (적용된 데이터, 기준 설명, 오류 결과 또는 None).

    없는 대상(오타·없는 부서)은 0건이 아니라 오류로 알린다. 값 확인은 형식 오류 행만 뺀 전체 표(df)로 한다.
    """
    desc: list[str] = []
    for f in filters:
        col = _resolve(data, f.column)
        if col is None:
            return data, desc, _missing_col(tool, p, f.column, df)
        s = data[col]
        other = getattr(f, "compare_column", None)
        if f.op in ("is_null", "not_null"):
            mask = s.isna() if f.op == "is_null" else s.notna()
        elif other:  # 같은 행의 다른 컬럼과 비교 (예: 수량 < 안전재고)
            if f.op == "in":
                return data, desc, _err(tool, p, "컬럼끼리 비교에는 in을 쓸 수 없습니다.")
            ocol = _resolve(data, other)
            if ocol is None:
                return data, desc, _missing_col(tool, p, other, df)
            kinds = [("num" if pd.api.types.is_numeric_dtype(x) else
                      "date" if pd.api.types.is_datetime64_any_dtype(x) else "text") for x in (s, data[ocol])]
            if kinds[0] != kinds[1] or (kinds[0] == "text" and f.op not in ("==", "!=")):
                return data, desc, _err(tool, p, f"'{f.column}'와 '{other}'는 형식이 달라 비교할 수 없습니다.")
            o = data[ocol]
            mask = {"==": s == o, "!=": s != o, ">": s > o, ">=": s >= o, "<": s < o, "<=": s <= o}[f.op]
            mask = mask & s.notna() & o.notna()  # 한쪽이 빈칸이면 비교하지 않는다
            data = data.loc[mask.fillna(False).astype(bool)]
            desc.append(f"{_show(col)} {f.op} {_show(ocol)}")
            continue
        else:
            value: Any = f.value
            if f.op == "in" and isinstance(value, str):  # Gemini는 "P01,P05"처럼 문자열로 보낸다
                value = [v.strip() for v in value.split(",") if v.strip()]
            elif f.op == "in" and not isinstance(value, list):
                value = [value]
            try:
                conv = pd.Timestamp if pd.api.types.is_datetime64_any_dtype(s) else (
                    float if pd.api.types.is_numeric_dtype(s) else str)
                value = [conv(v) for v in value] if f.op == "in" else conv(value)
            except (TypeError, ValueError):
                return data, desc, _err(tool, p, f"필터 값 '{f.value}'의 형식이 '{f.column}' 컬럼과 맞지 않습니다.")
            if f.op in ("==", "!=", "in"):
                if conv is str:
                    value = [_match_value(s, v) for v in value] if f.op == "in" else _match_value(s, value)
                bad = _unknown_values(_base(df)[col], col, value if f.op == "in" else [value])
                if bad:
                    return data, desc, _err(tool, p, bad)
            if f.op == "in":
                mask = s.isin(value)
            else:
                mask = {"==": s == value, "!=": s != value, ">": s > value, ">=": s >= value,
                        "<": s < value, "<=": s <= value}[f.op]
        data = data.loc[mask.fillna(False).astype(bool)]
        desc.append(f"{_show(col)} {f.op} {'' if f.value is None else f.value}".strip())
    return data, desc, None


def _apply_period(tool: str, p: Any, data: pd.DataFrame,
                  groups: list[str]) -> tuple[pd.DataFrame, dict[str, str], Optional[ToolResult]]:
    """period="month"면 group_by의 날짜 컬럼을 'YYYY-MM'으로 바꿔 월별로 묶는다.

    (바꾼 데이터, 컬럼→표시 이름, 오류 결과 또는 None). 원본은 고치지 않고 복사본에서 바꾼다.
    날짜(YYYY-MM-DD)·월(YYYY-MM) 형식이 아닌 값은 빈칸이 되어 묶음에서 빠진다.
    """
    shown = {g: _show(g) for g in groups}
    period = getattr(p, "period", None)
    if not period:
        return data, shown, None
    dates = [g for g in groups if pd.api.types.is_datetime64_any_dtype(data[g]) or _is_dateish(data[g])]
    if not dates:
        return data, shown, _err(tool, p, "월별로 묶으려면 group_by에 날짜 컬럼(YYYY-MM-DD)이 있어야 합니다.")
    data = data.assign(**{g: _period_series(data[g]) for g in dates})
    for g in dates:
        shown[g] = f"{_show(g)}(월)"
    return data, shown, None


# ---------------------------------------------------------------------------
# 1. aggregate: 범용 합계·평균·개수
# ---------------------------------------------------------------------------
def aggregate(ds: Dataset, p: AggregateParams) -> ToolResult:
    tool = "aggregate"
    df = ds.table(p.table)
    if df is None:
        return _err(tool, p, f"'{p.table}' 표가 없습니다. 해당 파일을 함께 올려 주세요.")
    data = _base(df)

    value_col = None
    if p.op != "count":  # 합계·평균·최댓값·최솟값은 숫자 값 컬럼이 필요
        if not p.value_column:
            return _err(tool, p, f"{OP_LABEL[p.op]}을 구하려면 값 컬럼(value_column)이 필요합니다.")
        value_col = _resolve(data, p.value_column)
        if value_col is None:
            return _missing_col(tool, p, p.value_column, df)
        if not pd.api.types.is_numeric_dtype(data[value_col]):
            return _err(tool, p, f"'{p.value_column}'은 숫자 컬럼이 아니라 {OP_LABEL[p.op]}을 구할 수 없습니다.")

    groups = []
    for g in p.group_by:
        col, bad = _group_col(tool, p, data, g)
        if bad:
            return bad
        groups.append(col)

    data, desc, bad = _apply_filters(tool, p, df, data, p.filters)
    if bad:
        return bad

    if value_col is not None:
        data = data.loc[data[value_col].notna()]
    data, shown, bad = _apply_period(tool, p, data, groups)
    if bad:
        return bad
    for g in groups:
        data = data.loc[data[g].notna()]

    label = f"{OP_LABEL[p.op]}({_show(value_col)})" if value_col else "개수(건)"
    if data.empty:  # 대상은 맞는데 조건에 맞는 행이 0개
        cond = f" (조건: {' / '.join(desc)})" if desc else ""
        if p.op in ("mean", "max", "min"):  # 0건이면 평균·최댓값·최솟값은 정의되지 않음 (합계·개수는 0)
            return _err(tool, p, f"조건에 맞는 데이터가 0건이라 {OP_LABEL[p.op]}을 계산할 수 없습니다{cond}.")
        rows = [] if groups else [{"구분": "전체", label: 0}]
        return ToolResult(tool=tool, params=p.model_dump(), rows=rows, value=0.0,
                          unit="건" if p.op == "count" else "",
                          basis=f"{OP_LABEL[p.op]} 기준, 조건에 맞는 행 0개{cond}", used_rows=0)
    fmt = int if p.op == "count" else r
    total = len(data) if p.op == "count" else getattr(data[value_col], p.op)()
    if groups:
        grouped = data.groupby(groups, sort=True)
        agg = grouped.size() if p.op == "count" else getattr(grouped[value_col], p.op)()
        rows = [{**{shown[g]: (k[i] if isinstance(k, tuple) else k) for i, g in enumerate(groups)}, label: fmt(v)}
                for k, v in agg.items()]
        if p.sort != "group":
            rows.sort(key=lambda x: (x[label] if p.sort == "value_asc" else -x[label]))  # 안정 정렬: 동점은 그룹 순
        if p.top_n:
            rows = rows[: p.top_n]
    else:
        rows = [{"구분": "전체", label: fmt(total)}]

    basis = f"{OP_LABEL[p.op]} 기준"
    if groups:
        basis += f", {'·'.join(shown[g] for g in groups)}별"
    if desc:
        basis += f", 조건: {' / '.join(desc)}"
    basis += f", {len(data)}행 사용"
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=r(total),
                      unit="건" if p.op == "count" else "", basis=basis, used_rows=len(data))


# ---------------------------------------------------------------------------
# 1-1. calculate_ratio: 비중·컬럼 비율·조건 비율 (%)
# ---------------------------------------------------------------------------
RATIO_KEY = {"share": "비중(%)", "column_ratio": "비율(%)", "condition_rate": "비율(%)"}


def calculate_ratio(ds: Dataset, p: RatioParams) -> ToolResult:
    tool = "calculate_ratio"
    df = ds.table(p.table)
    if df is None:
        return _err(tool, p, f"'{p.table}' 표가 없습니다. 해당 파일을 함께 올려 주세요.")
    data = _base(df)

    def numeric(name: Optional[str], role: str) -> tuple[Optional[str], Optional[ToolResult]]:
        if not name:
            return None, _err(tool, p, f"{role} 컬럼이 필요합니다.")
        col = _resolve(data, name)
        if col is None:
            return None, _missing_col(tool, p, name, df)
        if not pd.api.types.is_numeric_dtype(data[col]):
            return None, _err(tool, p, f"'{name}'은 숫자 컬럼이 아니라 {role}로 쓸 수 없습니다.")
        return col, None

    num_col = den_col = None
    if p.kind == "share":
        if not p.group_by:
            return _err(tool, p, "비중을 구하려면 무엇별 비중인지(group_by)가 필요합니다.")
        if p.value_column:
            num_col, bad = numeric(p.value_column, "비중의 값")
            if bad:
                return bad
    elif p.kind == "column_ratio":
        num_col, bad = numeric(p.value_column, "비율의 분자(value_column)")
        if bad:
            return bad
        den_col, bad = numeric(p.denominator_column, "비율의 분모(denominator_column)")
        if bad:
            return bad
    elif not p.condition:
        return _err(tool, p, "조건 비율을 구하려면 분자 조건(condition)이 필요합니다.")

    groups = []
    for g in p.group_by:
        col, bad = _group_col(tool, p, data, g)
        if bad:
            return bad
        groups.append(col)

    data, desc, bad = _apply_filters(tool, p, df, data, p.filters)
    if bad:
        return bad
    for c in (num_col, den_col):
        if c is not None:
            data = data.loc[data[c].notna()]
    data, shown, bad = _apply_period(tool, p, data, groups)
    if bad:
        return bad
    for g in groups:
        data = data.loc[data[g].notna()]
    cond = f" (조건: {' / '.join(desc)})" if desc else ""
    if data.empty:
        return _err(tool, p, f"조건에 맞는 데이터가 0건이라 비율을 계산할 수 없습니다{cond}.")

    key = RATIO_KEY[p.kind]
    if p.kind == "condition_rate":
        hit, cdesc, bad = _apply_filters(tool, p, df, data, p.condition)
        if bad:
            return bad
        num = pd.Series(data.index.isin(hit.index).astype(int), index=data.index)
        den = pd.Series(1, index=data.index)
        num_label, den_label, fmt = "해당(건)", "전체(건)", int
        formula = f"비율 = 조건({' / '.join(cdesc)})에 맞는 행 수 ÷ 전체 행 수 × 100"
    elif p.kind == "column_ratio":
        num, den = data[num_col], data[den_col]
        num_label, den_label, fmt = f"합계({_show(num_col)})", f"합계({_show(den_col)})", r
        formula = f"비율 = 합계({_show(num_col)}) ÷ 합계({_show(den_col)}) × 100"
    else:
        num = data[num_col] if num_col else pd.Series(1, index=data.index)
        den = None
        num_label, den_label, fmt = (f"합계({_show(num_col)})", None, r) if num_col else ("개수(건)", None, int)
        formula = f"비중 = {'·'.join(shown[g] for g in groups)}별 {num_label} ÷ 전체 {num_label} × 100"

    total_num = float(num.sum())
    total_den = float(den.sum()) if den is not None else total_num
    if total_den == 0:
        return _err(tool, p, f"분모 합계가 0이라 비율을 계산할 수 없습니다{cond}.")

    def pct(a: float, b: float) -> Optional[float]:
        return None if b == 0 else r(a / b * 100)

    if groups:
        gnum = num.groupby([data[g] for g in groups], sort=True).sum()
        gden = den.groupby([data[g] for g in groups], sort=True).sum() if den is not None else None
        rows = []
        for k, v in gnum.items():
            row = {shown[g]: (k[i] if isinstance(k, tuple) else k) for i, g in enumerate(groups)}
            row[num_label] = fmt(v)
            if gden is not None:
                row[den_label] = fmt(gden[k])
                row[key] = pct(float(v), float(gden[k]))
            else:
                row[key] = pct(float(v), total_num)
            rows.append(row)
        order = p.sort or ("value_desc" if p.kind == "share" else "group")
        if order != "group":  # 계산 불가(None)는 맨 뒤
            sign = 1 if order == "value_asc" else -1
            rows.sort(key=lambda x: (x[key] is None, sign * (x[key] or 0)))
        if p.top_n:
            rows = rows[: p.top_n]
    else:
        rows = [{"구분": "전체", num_label: fmt(total_num), den_label: fmt(total_den), key: pct(total_num, total_den)}]

    basis = formula
    if groups:
        basis += f", {'·'.join(shown[g] for g in groups)}별"
    if desc:
        basis += f", 조건: {' / '.join(desc)}"
    if den is not None and groups and any(x[key] is None for x in rows):
        basis += ", 분모가 0인 그룹은 계산 불가(빈칸)"
    basis += f", {len(data)}행 사용"
    value = None if p.kind == "share" else pct(total_num, total_den)
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=value,
                      unit="%", basis=basis, used_rows=len(data))


# ---------------------------------------------------------------------------
# 2. count_overdue_tasks: 기한 초과 미완료 업무
# ---------------------------------------------------------------------------
def count_overdue_tasks(ds: Dataset, p: CountOverdueParams) -> ToolResult:
    tool = "count_overdue_tasks"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    as_of = _parse_date(p.as_of)
    if as_of is None:
        return _err(tool, p, f"기준일 '{p.as_of}' 형식이 잘못되었습니다. YYYY-MM-DD로 입력해 주세요.")

    data, scope, bad = _scope(_base(ds.main), p)
    if bad:
        return _err(tool, p, bad)
    no_due = int(data["due_date"].isna().sum())
    data = data.loc[data["due_date"].notna()]
    overdue = (~_done(data) & (data["due_date"] < as_of)).fillna(False).astype(bool)
    basis = (f"기준일 {p.as_of}, 상태≠완료(미착수·진행중·보류 포함), 기한<기준일 (기한 당일은 초과 아님), "
             f"기한 빈칸 {no_due}건은 판정 불가로 제외, {len(data)}행 사용")
    if scope:
        basis = f"조건: {', '.join(scope)}, " + basis

    if p.group_by:
        col, bad = _group_col(tool, p, data, p.group_by)
        if bad:
            return bad
        tmp = data.assign(_over=overdue, _open=~_done(data))
        g = tmp.groupby(col).agg(over=("_over", "sum"), open=("_open", "sum"), total=("_over", "size")).reset_index()
        g = g.sort_values(["over", col], ascending=[False, True])
        rows = [{_show(col): row[col], "기한 초과(건)": int(row["over"]), "미완료(건)": int(row["open"]),
                 "전체(건)": int(row["total"])} for _, row in g.iterrows()]
    else:
        od = data.loc[overdue].assign(_days=lambda d: (as_of - d["due_date"]).dt.days)
        od = od.sort_values("task_id")
        rows = []
        for _, row in od.iterrows():
            item = {"업무ID": row["task_id"], "프로젝트ID": row["project_id"], "담당자ID": row["assignee_id"]}
            if "department" in od.columns:
                item["부서"] = row["department"]
            item.update({"상태": row["status"], "기한": row["due_date"], "초과 일수": int(row["_days"]),
                         "진행률(%)": row["progress_percent"], "원본 행": int(row["_row"])})
            rows.append(item)

    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=int(overdue.sum()), unit="건",
                      basis=basis, used_rows=len(data))


# ---------------------------------------------------------------------------
# 3. summarize_by_category: 범주별 현황
# ---------------------------------------------------------------------------
def summarize_by_category(ds: Dataset, p: SummarizeByCategoryParams) -> ToolResult:
    tool = "summarize_by_category"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    data, scope, unknown = _scope(_base(ds.main), p)
    if unknown:
        return _err(tool, p, unknown)
    col, bad = _group_col(tool, p, data, p.group_by)
    if bad:
        return bad
    as_of = None
    if p.as_of:
        as_of = _parse_date(p.as_of)
        if as_of is None:
            return _err(tool, p, f"기준일 '{p.as_of}' 형식이 잘못되었습니다. YYYY-MM-DD로 입력해 주세요.")

    data = data.loc[data[col].notna()]
    if data.empty:
        if scope:  # 대상은 맞는데 조건에 맞는 업무가 0건
            return ToolResult(tool=tool, params=p.model_dump(), rows=[], unit="건", used_rows=0,
                              basis=f"조건: {', '.join(scope)}, 조건에 맞는 업무 0건")
        return _err(tool, p, "집계할 데이터가 없습니다.")
    headcount = None
    if col == "department" and ds.employees is not None and not scope:  # 조건이 있으면 부서 전체 인원으로 나누지 않음
        headcount = _base(ds.employees)["department"].value_counts()

    rows = []
    for key, grp in data.groupby(col, sort=True):
        done = _done(grp)
        total = len(grp)
        row = {_show(col): key, "전체(건)": total, "완료(건)": int(done.sum()), "미완료(건)": int((~done).sum())}
        for st in ("미착수", "진행중", "보류"):
            row[f"{st}(건)"] = int((grp["status"] == st).fillna(False).sum())
        row["완료율(%)"] = r(done.sum() / total * 100)
        if as_of is not None:
            row["기한 초과(건)"] = int((~done & (grp["due_date"] < as_of)).fillna(False).astype(bool).sum())
        if headcount is not None:
            n = int(headcount.get(key, 0))
            row["인원"] = n if key != UNASSIGNED else None
            row["1인당 미완료"] = r(row["미완료(건)"] / n) if n else None
        rows.append(row)
    rows.sort(key=lambda x: (-x["미완료(건)"], str(x[_show(col)])))

    basis = f"{_show(col)}별 건수, 미완료=상태≠완료, 완료율=완료/전체×100"
    if scope:
        basis = f"조건: {', '.join(scope)}, " + basis
    if headcount is not None:
        basis += ", 인원=직원 표의 부서별 인원"
    if as_of is not None:
        basis += f", 기준일 {p.as_of}"
    basis += f", {len(data)}행 사용"
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), unit="건", basis=basis,
                      used_rows=len(data))


# ---------------------------------------------------------------------------
# 4. calculate_completion_rate: 완료율
# ---------------------------------------------------------------------------
def calculate_completion_rate(ds: Dataset, p: CompletionRateParams) -> ToolResult:
    tool = "calculate_completion_rate"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    data, scope, bad = _scope(_base(ds.main), p)
    if bad:
        return _err(tool, p, bad)
    if data.empty:
        if scope:
            return _err(tool, p, f"조건({', '.join(scope)})에 맞는 업무가 0건이라 완료율을 계산할 수 없습니다 (분모 0).")
        return _err(tool, p, "업무가 없어 완료율을 계산할 수 없습니다 (분모 0).")
    done = _done(data)
    overall = done.sum() / len(data) * 100
    if p.group_by:
        col, bad = _group_col(tool, p, data, p.group_by)
        if bad:
            return bad
        rows = []
        for key, grp in data.assign(_d=done).loc[data[col].notna()].groupby(col, sort=True):
            rows.append({_show(col): key, "전체(건)": len(grp), "완료(건)": int(grp["_d"].sum()),
                         "완료율(%)": r(grp["_d"].sum() / len(grp) * 100)})
        rows.sort(key=lambda x: x["완료율(%)"])  # 낮은 순 (동점은 그룹 순)
    else:
        rows = [{"구분": "전체", "전체(건)": len(data), "완료(건)": int(done.sum()), "완료율(%)": r(overall)}]
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=r(overall), unit="%",
                      basis=(f"조건: {', '.join(scope)}, " if scope else "")
                      + f"완료율=상태가 '완료'인 건수/전체 건수×100 (건수 기준), {len(data)}행 사용",
                      used_rows=len(data))


# ---------------------------------------------------------------------------
# 5. compare_periods: 두 달 비교
# ---------------------------------------------------------------------------
def _period_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.strftime("%Y-%m")
    text = series.astype("string")
    out = pd.Series(pd.NA, index=series.index, dtype="string")
    is_month = text.str.match(MONTH_RE.pattern).fillna(False).astype(bool)
    is_date = text.str.match(DATE_RE.pattern).fillna(False).astype(bool)
    out[is_month] = text[is_month]
    out[is_date] = text[is_date].str[:7]
    return out


def compare_periods(ds: Dataset, p: ComparePeriodsParams) -> ToolResult:
    tool = "compare_periods"
    if not MONTH_RE.match(p.current):
        return _err(tool, p, f"비교 기간 '{p.current}' 형식이 잘못되었습니다. YYYY-MM으로 입력해 주세요.")
    previous = p.previous or (pd.Period(p.current, freq="M") - 1).strftime("%Y-%m")
    if not MONTH_RE.match(previous):
        return _err(tool, p, f"이전 기간 '{previous}' 형식이 잘못되었습니다. YYYY-MM으로 입력해 주세요.")

    data = _base(ds.main)
    date_col = _resolve(data, p.date_column)
    if date_col is None:
        return _missing_col(tool, p, p.date_column, ds.main)
    period = _period_series(data[date_col])
    if period.isna().all():
        return _err(tool, p, f"'{p.date_column}'은 날짜(YYYY-MM-DD)나 월(YYYY-MM) 형식 컬럼이 아닙니다.")

    value_col = None
    if p.op in ("sum", "mean"):
        if not p.value_column:
            return _err(tool, p, f"{OP_LABEL[p.op]}을 비교하려면 값 컬럼(value_column)이 필요합니다.")
        value_col = _resolve(data, p.value_column)
        if value_col is None:
            return _missing_col(tool, p, p.value_column, ds.main)
        if not pd.api.types.is_numeric_dtype(data[value_col]):
            return _err(tool, p, f"'{p.value_column}'은 숫자 컬럼이 아니라 비교할 수 없습니다.")
    group_col = None
    if p.group_by:
        group_col, bad = _group_col(tool, p, data, p.group_by)
        if bad:
            return bad

    data = data.assign(_period=period)
    if value_col is not None:
        data = data.loc[data[value_col].notna()]
    cur, prev = data.loc[data["_period"] == p.current], data.loc[data["_period"] == previous]
    if prev.empty:
        return _err(tool, p, f"비교할 이전 기간({previous}) 데이터가 없어 증감을 계산할 수 없습니다.")
    if cur.empty:
        return _err(tool, p, f"비교 기간({p.current}) 데이터가 없어 증감을 계산할 수 없습니다.")

    def calc(part: pd.DataFrame) -> Optional[float]:
        if p.op == "count":
            return float(len(part))
        return None if part.empty else float(getattr(part[value_col], p.op)())

    def change(a, b):
        if a is None or b is None:
            return None, None, "한쪽 기간에 데이터 없음"
        if a == 0:
            return b - a, None, "이전 값이 0이라 증감률 계산 불가"
        return b - a, (b - a) / a * 100, ""

    t_prev, t_cur = calc(prev), calc(cur)
    t_change, t_pct, t_note = change(t_prev, t_cur)
    rows = []
    if group_col:
        for k in sorted(set(prev[group_col].dropna()) | set(cur[group_col].dropna()), key=str):
            a, b = calc(prev.loc[prev[group_col] == k]), calc(cur.loc[cur[group_col] == k])
            d, pct, note = change(a, b)
            rows.append({_show(group_col): k, previous: r(a), p.current: r(b), "증감": r(d), "증감률(%)": r(pct), "비고": note})
    else:
        rows.append({"구분": "전체", previous: r(t_prev), p.current: r(t_cur), "증감": r(t_change),
                     "증감률(%)": r(t_pct), "비고": t_note})

    label = f"{OP_LABEL[p.op]}({_show(value_col)})" if value_col else "건수"
    used = len(prev) + len(cur)
    error = f"{previous} 값이 0이라 증감률을 계산할 수 없습니다." if not group_col and t_pct is None else None
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=r(t_pct), unit="%",
                      basis=f"{label} 비교, {_show(date_col)} 기준 {previous} → {p.current}, "
                            f"증감률=(이번-이전)/이전×100 (value=전체 증감률), {used}행 사용",
                      used_rows=used, error=error)


# ---------------------------------------------------------------------------
# 6. find_recurring_issues: 반복 문제
# ---------------------------------------------------------------------------
def find_recurring_issues(ds: Dataset, p: FindRecurringParams) -> ToolResult:
    tool = "find_recurring_issues"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    data = _base(ds.main)
    col, bad = _group_col(tool, p, data, p.key)
    if bad:
        return bad
    data = data.loc[data[col].notna()]
    if data.empty:
        return _err(tool, p, f"'{p.key}' 값이 있는 업무가 없습니다.")
    rows = []
    for key, grp in data.groupby(col, sort=True):
        if len(grp) < p.min_count:
            continue
        row = {_show(col): key, "발생(건)": len(grp), "미완료(건)": int((~_done(grp)).sum()),
               "관련 프로젝트": ", ".join(sorted(grp["project_id"].dropna().unique())),
               "업무ID": ", ".join(sorted(grp["task_id"].dropna()))}
        if "department" in grp.columns:
            row["관련 부서"] = ", ".join(sorted(grp["department"].dropna().unique()))
        rows.append(row)
    rows.sort(key=lambda x: (-x["발생(건)"], str(x[_show(col)])))
    return ToolResult(tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=len(rows), unit="개 유형",
                      basis=f"{_show(col)}이 같은 업무가 {p.min_count}건 이상이면 반복으로 봄, {len(data)}행 사용",
                      used_rows=len(data))


# ---------------------------------------------------------------------------
# 7. mh_overrun: 계획 대비 실적 MH 초과
# ---------------------------------------------------------------------------
def mh_overrun(ds: Dataset, p: MhOverrunParams) -> ToolResult:
    tool = "mh_overrun"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    data = _base(ds.main)
    col, bad = _group_col(tool, p, data, p.group_by)
    if bad:
        return bad
    data = data.loc[data[col].notna()]
    done = _done(data)
    flagged = (done & (data["actual_mh"] > data["planned_mh"] * 1.2)) | (~done & (data["actual_mh"] > data["planned_mh"]))
    tmp = data.assign(_done=done, _flag=flagged.fillna(False).astype(bool))
    rows = []
    for key, grp in tmp.groupby(col, sort=True):
        comp = grp.loc[grp["_done"]]
        ratio = comp["actual_mh"].sum() / comp["planned_mh"].sum() if comp["planned_mh"].sum() else None
        rows.append({_show(col): key, "계획MH": r(grp["planned_mh"].sum()), "실적MH": r(grp["actual_mh"].sum()),
                     "초과MH 합계": r(grp["mh_over"].sum()), "완료업무 실적/계획": r(ratio),
                     "초과 투입 업무(건)": int(grp["_flag"].sum())})
    rows.sort(key=lambda x: -x["초과MH 합계"])
    return ToolResult(
        tool=tool, params=p.model_dump(), rows=_clean_rows(rows), unit="MH", used_rows=len(data),
        basis=("초과MH=업무별 max(실적MH−계획MH, 0)의 합 (미완료 업무의 남은 계획이 초과분을 상쇄하지 않음), "
               "초과 투입=완료는 실적>계획×1.2, 미완료는 실적>계획, 실적MH는 기준일까지 누적값, "
               f"{len(data)}행 사용"))


# ---------------------------------------------------------------------------
# 8. cost_vs_budget: 예산 대비 비용
# ---------------------------------------------------------------------------
def cost_vs_budget(ds: Dataset, p: CostVsBudgetParams) -> ToolResult:
    tool = "cost_vs_budget"
    if not _standard(ds) or ds.projects is None or ds.employees is None:
        return _err(tool, p, "예산 대비 비용은 업무·직원·프로젝트 파일 3개가 모두 있어야 계산할 수 있습니다.")
    data = _base(ds.main)
    projects = _base(ds.projects).set_index("project_id")
    if p.project_id:
        if p.project_id not in projects.index:
            return _err(tool, p, f"프로젝트 '{p.project_id}'가 프로젝트 표에 없습니다.")
        projects = projects.loc[[p.project_id]]
        data = data.loc[data["project_id"] == p.project_id]

    rows, unknown = [], 0
    for pid, prj in projects.iterrows():
        part = data.loc[data["project_id"] == pid]
        missing = int(part["labor_cost"].isna().sum())
        unknown += missing
        labor, nonlabor = part["labor_cost"].sum(), part["actual_nonlabor_cost"].sum()
        total = labor + nonlabor
        budget = prj["budget"]
        rate = (total / budget * 100 - 100) if budget else None
        rows.append({"프로젝트ID": pid, "예산": r(budget), "실적 인건비": r(labor), "실적 비인건비": r(nonlabor),
                     "실적 총비용": r(total), "비용 초과율(%)": r(rate), "프로젝트 상태": prj["status"],
                     "비고": f"인건비 계산 불가 업무 {missing}건 제외" if missing else ""})
    rows.sort(key=lambda x: (x["비용 초과율(%)"] is None, -(x["비용 초과율(%)"] or 0)))
    return ToolResult(
        tool=tool, params=p.model_dump(), rows=_clean_rows(rows), unit="%", used_rows=len(data),
        basis=("실적 인건비=실적MH×담당자 시간당 인건비(추정), 실적 총비용=인건비+실적 비인건비, "
               "비용 초과율=실적 총비용/예산×100−100, 예산은 프로젝트 표에서 프로젝트당 1회만 사용, "
               f"기준일까지 누적 실적(완료 시점 예측 아님), {len(data)}행 사용"
               + (f", 인건비 계산 불가 {unknown}건 제외" if unknown else "")))


# ---------------------------------------------------------------------------
# 9. check_missing_data: 누락 데이터 점검
# ---------------------------------------------------------------------------
def check_missing_data(ds: Dataset, p: CheckMissingParams) -> ToolResult:
    tool = "check_missing_data"
    if not _standard(ds):
        return _err(tool, p, NEEDS_STANDARD)
    data = _base(ds.main)
    done = _done(data)
    nonconf = (data["category"] == NONCONFORMANCE_CATEGORY).fillna(False).astype(bool)
    reasons = {"assignee_id": "담당자 미지정 (배정 확인 필요)", "due_date": "기한 미입력 (일정 확인 필요)"}

    checks: list[tuple[str, pd.Series, str, str]] = []
    for c in CHECK_IF_MISSING["tasks"]:
        checks.append((c, data[c].isna(), "확인 필요", reasons.get(c, "값 없음")))
    checks.append(("completed_at", done & data["completed_at"].isna(), "확인 필요", "완료인데 완료일 없음"))
    checks.append(("issue_type", nonconf & data["issue_type"].isna(), "확인 필요", "부적합 대응인데 문제유형 없음"))
    if p.include_structural:
        checks.append(("completed_at", ~done & data["completed_at"].isna(), "구조적 공백", "미완료라 완료일 없음 (정상)"))
        checks.append(("issue_type", ~nonconf & data["issue_type"].isna(), "구조적 공백", "부적합 대응이 아니라 문제유형 없음 (정상)"))

    rows = []
    for c, mask, kind, reason in checks:
        for _, row in data.loc[mask.fillna(False).astype(bool)].sort_values("task_id").iterrows():
            rows.append({"업무ID": row["task_id"], "컬럼": _show(c), "분류": kind, "사유": reason,
                         "프로젝트ID": row["project_id"], "상태": row["status"], "원본 행": int(row["_row"])})
    need = [x for x in rows if x["분류"] == "확인 필요"]
    structural = int((~done & data["completed_at"].isna()).sum() + (~nonconf & data["issue_type"].isna()).sum())
    invalid = len(ds.main) - len(data)
    return ToolResult(
        tool=tool, params=p.model_dump(), rows=_clean_rows(rows), value=len({x["업무ID"] for x in need}), unit="건(업무)",
        used_rows=len(data),
        basis=(f"확인 필요 = 담당자·기한 빈칸, 완료인데 완료일 없음, 부적합 대응인데 문제유형 없음 "
               f"({len(need)}칸). 구조적 공백(값이 없어야 정상인 칸) {structural}칸은 문제로 세지 않음"
               + (f", 형식 오류 {invalid}행은 업로드 검증 결과 참고" if invalid else "")))
