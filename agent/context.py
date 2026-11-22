"""LLM에 보낼 데이터 설명과, 계획 검증에 쓸 허용 값 목록.

보내는 것: 표·컬럼 이름(한국어/영어), 형식, 범주값(부서·상태·분류·문제유형 등), ID 목록, 기준일.
보내지 않는 것: 업무명·프로젝트명 등 자유 텍스트(contracts.FREE_TEXT_COLUMNS), 숫자 값, 평가 정답.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from contracts import ALLOWED_VALUES, DISPLAY_NAMES, FREE_TEXT_COLUMNS
from engine.loader import Dataset

# 값 목록을 확인할 범주 컬럼 (표준 데이터)
CATEGORICAL = ["department", "project_id", "assignee_id", "employee_id", "status", "project_status", "category",
               "issue_type", "priority", "role", "project_type", "lead_department"]
MAX_VALUES = 60  # 한 컬럼에서 보여줄 최대 값 수
GENERIC_MAX_UNIQUE = 20  # 일반 CSV: 고유값이 이 이하인 텍스트 컬럼만 범주로 본다
GENERIC_MAX_LEN = 30
_TO_INTERNAL = {v: k for k, v in DISPLAY_NAMES.items()}


def show(col: str) -> str:
    return DISPLAY_NAMES.get(col, col)


def _dtype(s: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(s):
        return "날짜"
    if pd.api.types.is_numeric_dtype(s):
        return "숫자"
    return "텍스트"


@dataclass
class Vocabulary:
    as_of: str
    kind: str  # "standard" | "generic"
    columns: dict[str, dict[str, str]] = field(default_factory=dict)  # 표 -> {내부 컬럼: 형식}
    values: dict[str, list[str]] = field(default_factory=dict)  # 내부 컬럼 -> 허용 값

    def table_of(self, table: str) -> dict[str, str]:
        return self.columns.get(table, {})

    def resolve(self, table: str, name: Optional[str]) -> Optional[str]:
        """영어·한국어 컬럼 이름 → 내부 이름. 없으면 None (자유 텍스트 컬럼도 목록에 없음)."""
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()
        cols = self.table_of(table)
        for cand in (name, _TO_INTERNAL.get(name)):
            if cand and cand in cols:
                return cand
        return None


def build_vocabulary(ds: Dataset, as_of: str) -> Vocabulary:
    vocab = Vocabulary(as_of=as_of, kind=ds.kind)
    tables = {"main": ds.main, "employees": ds.employees, "projects": ds.projects}
    for name, df in tables.items():
        if df is None:
            continue
        vocab.columns[name] = {c: _dtype(df[c]) for c in df.columns
                               if not c.startswith("_") and c not in FREE_TEXT_COLUMNS}

    def add(col: str, series: pd.Series) -> None:
        vals = sorted({str(v) for v in series.dropna().unique() if str(v).strip()})
        vocab.values[col] = sorted(set(vocab.values.get(col, [])) | set(vals))

    if ds.kind == "standard":
        for name, df in tables.items():
            if df is None:
                continue
            for col in CATEGORICAL:
                if col in df.columns:
                    add(col, df[col])
        if "employee_id" in vocab.values:  # 담당자 ID = 직원 표의 ID (+ 업무 표에만 있는 ID)
            vocab.values["assignee_id"] = sorted(set(vocab.values.get("assignee_id", [])) | set(vocab.values["employee_id"]))
        for col, allowed in ALLOWED_VALUES["tasks"].items():
            vocab.values[col] = sorted(set(vocab.values.get(col, [])) | set(allowed))
    else:
        df = ds.main
        for col, kind in vocab.columns["main"].items():
            if kind != "텍스트":
                continue
            s = df[col].dropna().astype(str)
            if 0 < s.nunique() <= GENERIC_MAX_UNIQUE and s.str.len().max() <= GENERIC_MAX_LEN:
                add(col, s)
    return vocab


def _date_range(ds: Dataset) -> str:
    out = []
    for col in ("start_date", "due_date", "completed_at"):
        if col in ds.main.columns and pd.api.types.is_datetime64_any_dtype(ds.main[col]):
            s = ds.main[col].dropna()
            if not s.empty:
                out.append(f"{show(col)} {s.min():%Y-%m-%d}~{s.max():%Y-%m-%d}")
    return ", ".join(out)


def render_context(ds: Dataset, vocab: Vocabulary) -> str:
    table_label = {"main": "main (업무 표: 직원·프로젝트 정보가 결합됨)" if vocab.kind == "standard" else "main (올린 CSV)",
                   "employees": "employees (직원 표)", "projects": "projects (프로젝트 표)"}
    lines = [f"[기준일 as_of] {vocab.as_of}", f"[데이터 종류] {'표준(업무·직원·프로젝트)' if vocab.kind == 'standard' else '일반 CSV'}",
             "", "[표와 컬럼] 한국어 이름(내부 이름): 형식"]
    for table, cols in vocab.columns.items():
        lines.append(f"- {table_label[table]}: " + ", ".join(f"{show(c)}({c}): {t}" for c, t in cols.items()))
    if vocab.values:
        lines += ["", "[범주 컬럼의 실제 값] 필터·질문 대상은 이 값만 쓸 수 있다"]
        for col, vals in vocab.values.items():
            shown = vals[:MAX_VALUES]
            more = f" 외 {len(vals) - MAX_VALUES}개" if len(vals) > MAX_VALUES else ""
            lines.append(f"- {show(col)}({col}): {', '.join(v[:GENERIC_MAX_LEN] for v in shown)}{more}")
    if vocab.kind == "standard":
        dr = _date_range(ds)
        if dr:
            lines += ["", f"[날짜 범위] {dr}"]
    lines += ["", "자유 텍스트 컬럼(업무명·프로젝트명)은 분석·필터·묶음 기준으로 쓸 수 없다."]
    return "\n".join(lines)
