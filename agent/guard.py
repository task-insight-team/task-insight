"""LLM이 고른 도구 호출을 실행 전에 검증한다 (실행은 하지 않음).

엔진(run_tool)도 입력을 검증하지만, 계획 단계에서 미리 걸러 사용자에게 승인받을 계획 자체를 깨끗하게 만든다.
- 허용 목록(engine.TOOLS)에 있는 도구인가
- contracts의 *Params 모델로 형식이 맞는가 (모델 검증만, 계산 없음)
- 컬럼이 데이터에 있고 자유 텍스트 컬럼이 아닌가
- 필터 값과 직접 범위 인자(department·project_id)가 데이터에 실제로 있는가
  (엔진도 없는 대상과 0건을 구분하지만, 승인 전에 되묻기 위해 계획 단계에서 먼저 확인한다)
- 기준일(as_of)은 외부에서 받은 값으로 고정
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from contracts import FREE_TEXT_COLUMNS, AnalysisPlan
from engine import TOOLS

from agent.context import Vocabulary, show

MAX_STEPS = 4
MIN_TOP_N = 5
AS_OF_TOOLS = ("count_overdue_tasks", "summarize_by_category")  # as_of를 받는 도구 (항상 외부 기준일로 채움)
# 도구별 컬럼 이름을 받는 필드 (aggregate.group_by는 목록)
COLUMN_FIELDS: dict[str, list[str]] = {
    "aggregate": ["value_column", "multiply_by", "group_by"],
    "calculate_ratio": ["value_column", "denominator_column", "group_by"],
    "count_overdue_tasks": ["group_by"],
    "summarize_by_category": ["group_by"],
    "calculate_completion_rate": ["group_by"],
    "compare_periods": ["date_column", "value_column", "group_by"],
    "find_recurring_issues": ["key"],
    "mh_overrun": ["group_by"],
}
VALUE_OPS = ("==", "!=", "in")


# 도구가 직접 받는 범위 인자 → 값 목록을 확인할 컬럼. 실제 Params 모델에 필드가 있는 도구에서만 유효하다.
DIRECT_SCOPE = {"department": "department", "project_id": "project_id"}


def supports(tool: str, field: str) -> bool:
    spec = TOOLS.get(tool)
    return spec is not None and field in spec.params.model_fields


def _canonical(vocab: Vocabulary, col: str, value: Any) -> Optional[str]:
    """'품질팀'·'품질부'는 데이터의 '품질'로 맞춘다 (엔진 규칙과 같음). 없는 값이면 None."""
    allowed = vocab.values.get(col, [])
    v = str(value).strip()
    if v in allowed:
        return v
    for suffix in ("팀", "부서", "부"):
        if v.endswith(suffix) and v[: -len(suffix)] in allowed:
            return v[: -len(suffix)]
    return None


class Rejected(Exception):
    """이 호출은 실행 계획에 넣지 않는다. 메시지는 사용자에게 보여줄 한국어."""


def _clean(v: Any) -> Any:
    """None 값 키를 지운다 (Gemini는 nullable 필드에 null을 보냄 → 기본값이 적용되도록)."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_clean(x) for x in v if x is not None]
    return v


def _check_column(vocab: Vocabulary, table: str, name: Any) -> str:
    if name in FREE_TEXT_COLUMNS or name in ("업무명", "프로젝트명"):
        raise Rejected(f"'{name}'은 자유 텍스트 컬럼이라 분석 기준으로 쓸 수 없습니다.")
    col = vocab.resolve(table, name)
    if col is None:
        cols = ", ".join(show(c) for c in vocab.table_of(table))
        raise Rejected(f"'{name}' 컬럼이 데이터에 없습니다. 사용할 수 있는 컬럼: {cols}")
    return col


def _check_values(vocab: Vocabulary, col: str, op: str, value: Any) -> None:
    allowed = vocab.values.get(col)
    if not allowed or op not in VALUE_OPS or value is None:
        return
    if op == "in":
        items = [v.strip() for v in value.split(",")] if isinstance(value, str) else (value if isinstance(value, list) else [value])
    else:
        items = [value]
    unknown = [str(v) for v in items if str(v).strip() and str(v) not in allowed]
    if unknown:
        raise Rejected(f"{show(col)} '{', '.join(unknown)}'은(는) 데이터에 없습니다. 가능한 값: {', '.join(allowed)}")


def validate_call(name: str, args: Any, vocab: Vocabulary) -> tuple[dict, list[str]]:
    """검증된 params와 안내문 목록을 돌려준다. 실행할 수 없으면 Rejected."""
    spec = TOOLS.get(name)
    if spec is None:
        raise Rejected(f"지원하지 않는 분석 도구입니다: {name}")
    if not isinstance(args, dict):
        raise Rejected(f"{name}: 도구 입력이 올바른 형식이 아닙니다.")
    args = _clean(args)
    notes: list[str] = []

    if name in AS_OF_TOOLS:
        given = args.get("as_of")
        if given and given != vocab.as_of:
            notes.append(f"기준일은 설정값 {vocab.as_of}로 고정했습니다 (AI 제안 {given} 무시).")
        args["as_of"] = vocab.as_of

    try:
        parsed = spec.params.model_validate(args)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(x) for x in e["loc"]) or "(전체)" for e in exc.errors())
        raise Rejected(f"{name}: 도구 입력 형식이 잘못되었습니다 ({fields}).") from None
    params = parsed.model_dump(mode="json")
    # 순위 질문에서 top_n이 너무 작으면 1위 동점이 잘린다 → 최소 MIN_TOP_N행은 남긴다
    if name == "aggregate" and params.get("top_n") and params["top_n"] < MIN_TOP_N and params.get("sort") != "group":
        params["top_n"] = MIN_TOP_N

    table = params.get("table", "main")
    if not vocab.table_of(table):
        raise Rejected(f"'{table}' 표가 없습니다. 해당 파일을 함께 올려 주세요.")
    for f in COLUMN_FIELDS.get(name, []):
        val = params.get(f)
        for item in (val if isinstance(val, list) else [val]):
            if item:
                _check_column(vocab, table, item)
    for flt in (params.get("filters") or []) + (params.get("condition") or []):
        col = _check_column(vocab, table, flt["column"])
        if flt.get("compare_column"):  # 컬럼끼리 비교: 비교 대상 컬럼도 데이터에 있어야 한다 (값 확인은 하지 않음)
            _check_column(vocab, table, flt["compare_column"])
        else:
            _check_values(vocab, col, flt["op"], flt.get("value"))
    for field, col in DIRECT_SCOPE.items():  # department·project_id 직접 인자 (지원하는 도구만 model_dump에 남는다)
        if supports(name, field) and params.get(field) not in (None, ""):
            canon = _canonical(vocab, col, params[field])
            if canon is None:
                raise Rejected(f"{show(col)} '{params[field]}'은(는) 데이터에 없습니다. "
                               f"가능한 값: {', '.join(vocab.values.get(col, []))}")
            params[field] = canon
    return params, notes


# 질문에 직접 적힌 대상(부서·프로젝트ID·직원ID)이 데이터에 있는지 LLM 호출 전에 확인한다.
# (라우팅용 키워드 매칭이 아니라 '없는 대상을 지어내지 않게' 막는 검증)
_DEPT_RE = re.compile(r"([가-힣A-Za-z]{2,})(?:팀|부서|부)(?=[\s은는이가을를의에도만과와,.?!]|$)")
_PROJECT_RE = re.compile(r"(?<![A-Za-z0-9])[Pp](\d{2,3})(?![0-9])")
_EMP_RE = re.compile(r"(?<![A-Za-z0-9])[Ee](\d{3,4})(?![0-9])")


def check_question_targets(question: str, vocab: Vocabulary) -> list[str]:
    if vocab.kind != "standard":
        return []
    problems = []
    depts = [d for d in vocab.values.get("department", []) if d != "미배정"]
    for stem in dict.fromkeys(_DEPT_RE.findall(question)):
        if depts and not any(stem in d or d in stem for d in depts):
            problems.append(f"부서 '{stem}'은(는) 데이터에 없습니다. 있는 부서: {', '.join(depts)}")
    for label, rx, col in (("프로젝트", _PROJECT_RE, "project_id"), ("직원", _EMP_RE, "employee_id")):
        known = vocab.values.get(col, [])
        prefix = "P" if col == "project_id" else "E"
        for digits in dict.fromkeys(rx.findall(question)):
            cand = [k for k in known if k[1:].lstrip("0") == digits.lstrip("0")]
            if known and not cand:
                problems.append(f"{label} '{prefix}{digits}'은(는) 데이터에 없습니다 ({known[0]}~{known[-1]}).")
    return problems


# ---------------------------------------------------------------------------
# 질문 조건 ↔ 계획 대조: 질문에 명시된 조건이 계산 인자에 실제로 반영됐는지 확인한다.
# 반영되지 않은 단계는 승인 대상 계획에서 뺀다 (전체 결과를 대신 보여 주지 않음).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Condition:
    kind: str  # department | project | employee | status | overdue | month
    value: str
    label: str  # 사용자 안내용


_STATUS_WORDS = {"진행중": "진행중", "진행 중": "진행중", "미착수": "미착수", "보류": "보류"}
_OVERDUE_RE = re.compile(r"(기한|마감|납기|데드라인).{0,8}(지난|지나|넘|초과|경과)|기한\s*초과|지연된|지연 업무|밀린|연체")
# 질문이 요구한 지표 → 그 지표를 계산하는 도구 (aggregate의 개수·합계로는 대신할 수 없음)
_METRICS = [
    (re.compile(r"완료율|완수율|완료 비율"), "calculate_completion_rate,summarize_by_category", "지표 = 완료율"),
    (re.compile(r"비용\s*초과|예산"), "cost_vs_budget", "지표 = 예산 대비 비용"),
    (re.compile(r"MH\s*초과|초과\s*MH|투입\s*시간.{0,6}초과|공수.{0,6}초과", re.I), "mh_overrun", "지표 = MH 초과"),
]
_YM_RE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})(?!\d)\s*월?(?!\s*[-./]?\s*\d)")  # 2026-11-19 같은 날짜는 제외
_M_RE = re.compile(r"(?<![\d-])(\d{1,2})\s*월(?!\s*\d+\s*일)")


def extract_conditions(question: str, vocab: Vocabulary) -> list[Condition]:
    """질문에 명시된 조건 (데이터에 실제로 있는 값만). 표준 데이터에서만 검사한다."""
    if vocab.kind != "standard":
        return []
    out: list[Condition] = []
    for d in vocab.values.get("department", []):
        if d != "미배정" and d in question:
            out.append(Condition("department", d, f"부서 = {d}"))
    for label, rx, col, kind in (("프로젝트", _PROJECT_RE, "project_id", "project"), ("직원", _EMP_RE, "employee_id", "employee")):
        for digits in dict.fromkeys(rx.findall(question)):
            for k in vocab.values.get(col, []):
                if k[1:].lstrip("0") == digits.lstrip("0"):
                    out.append(Condition(kind, k, f"{label} = {k}"))
    for word, status in _STATUS_WORDS.items():
        if word in question and not any(c.value == status for c in out):
            out.append(Condition("status", status, f"상태 = {status}"))
    if _OVERDUE_RE.search(question):
        out.append(Condition("overdue", "", "기한 초과(기한 < 기준일, 미완료)"))
    months = [f"{y}-{int(m):02d}" for y, m in _YM_RE.findall(question) if 1 <= int(m) <= 12]
    if not months:
        months = [f"{vocab.as_of[:4]}-{int(m):02d}" for m in _M_RE.findall(question) if 1 <= int(m) <= 12]
    for ym in dict.fromkeys(months):
        out.append(Condition("month", ym, f"기간 = {ym}"))
    for rx, tools, label in _METRICS:
        if rx.search(question):
            out.append(Condition("metric", tools, label))
    return out


def _filter_values(f: dict) -> list[str]:
    v = f.get("value")
    if isinstance(v, list):
        return [str(x).strip() for x in v]
    if isinstance(v, str) and f.get("op") == "in":
        return [x.strip() for x in v.split(",")]
    return [] if v is None else [str(v).strip()]


def _filters(tool: str, params: dict, vocab: Vocabulary) -> list[tuple[Optional[str], str, list[str]]]:
    table = params.get("table", "main")
    return [(vocab.resolve(table, f.get("column")), f.get("op"), _filter_values(f)) for f in params.get("filters") or []]


def step_covers(tool: str, params: dict, cond: Condition, vocab: Vocabulary) -> bool:
    flt = _filters(tool, params, vocab)

    def has(cols: tuple, value: str) -> bool:
        return any(c in cols and op in ("==", "in") and value in vals for c, op, vals in flt)

    if cond.kind == "department":  # filters 또는 department 직접 인자 (지원 도구만)
        return has(("department", "lead_department"), cond.value) or (
            supports(tool, "department") and params.get("department") == cond.value)
    if cond.kind == "project":  # filters 또는 project_id 직접 인자 (지원 도구만)
        return has(("project_id",), cond.value) or (supports(tool, "project_id") and params.get("project_id") == cond.value)
    if cond.kind == "employee":
        return has(("assignee_id", "employee_id"), cond.value)
    if cond.kind == "status":
        return has(("status",), cond.value)
    if cond.kind == "overdue":
        if tool == "count_overdue_tasks":
            return True
        due = any(c == "due_date" and op in ("<", "<=") for c, op, _ in flt)
        open_ = any(c == "status" and ((op == "!=" and "완료" in vals) or (op in ("==", "in") and vals and "완료" not in vals))
                    for c, op, vals in flt)
        return due and open_
    if cond.kind == "metric":
        return tool in cond.value.split(",")
    if cond.kind == "month":
        if tool == "compare_periods":
            return cond.value in (params.get("current"), params.get("previous"))
        date_cols = {c for c, t in vocab.table_of(params.get("table", "main")).items() if t == "날짜"} | {"start_month", "completed_month"}
        return any(c in date_cols and any(v.startswith(cond.value) for v in vals) for c, op, vals in flt)
    return True


def suggest_filters(conditions: list[Condition], as_of: str) -> list[dict]:
    """조건을 aggregate filters로 옮긴 예시 (재계획 안내용)."""
    out: list[dict] = []
    for c in conditions:
        if c.kind == "department":
            out.append({"column": "부서", "op": "==", "value": c.value})
        elif c.kind == "project":
            out.append({"column": "프로젝트ID", "op": "==", "value": c.value})
        elif c.kind == "employee":
            out.append({"column": "담당자ID", "op": "==", "value": c.value})
        elif c.kind == "status":
            out.append({"column": "상태", "op": "==", "value": c.value})
        elif c.kind == "overdue":
            out += [{"column": "상태", "op": "!=", "value": "완료"}, {"column": "기한", "op": "<", "value": as_of}]
        elif c.kind == "month":
            out.append({"column": "완료월", "op": "==", "value": c.value})
    return out


def missing_conditions(tool: str, params: dict, conditions: list[Condition], vocab: Vocabulary) -> list[Condition]:
    return [c for c in conditions if not step_covers(tool, params, c, vocab)]


# 범위 조건: 각 단계가 지켜야 한다 (같은 종류 값이 여럿이면 그중 하나 이상). 전사 결과로 대신 답하지 않게 막는다.
# 지표·기한 초과 조건: 계획 전체에서 한 단계 이상이 계산하면 된다 (한 단계가 두 지표를 동시에 계산할 수는 없음).
SCOPE_KINDS = ("department", "project", "employee", "status", "month")


def step_scope_missing(tool: str, params: dict, conditions: list[Condition], vocab: Vocabulary) -> list[Condition]:
    """이 단계가 범위 조건 종류별로 질문의 값을 하나도 반영하지 않았으면 그 조건들을 돌려준다."""
    lacking: list[Condition] = []
    for kind in SCOPE_KINDS:
        group = [c for c in conditions if c.kind == kind]
        if group and not any(step_covers(tool, params, c, vocab) for c in group):
            lacking += group
    return lacking


def plan_missing(steps: list, conditions: list[Condition], vocab: Vocabulary) -> list[Condition]:
    """계획의 어느 단계도 반영하지 않은 조건 (모든 종류)."""
    return [c for c in conditions if not any(step_covers(s.tool, s.params, c, vocab) for s in steps)]


# 질문에 필요한 정보(개념) → 데이터에 있어야 할 컬럼. 일반 CSV는 컬럼 이름에 힌트가 들어 있으면 있는 것으로 본다.
# 없으면 다른 컬럼으로 대신 분석하지 않고 LLM 호출 전에 '지원 불가'로 안내한다.
REQUIRED_CONCEPTS = [
    ("부서", re.compile(r"부서|팀(?!원)"), ("department", "lead_department"), ("부서", "dept", "department", "team", "팀")),
    ("업무 상태(완료·미완료)", re.compile(r"미완료|완료율|완수율|완료된|완료 업무|완료한|진행\s?중|미착수|보류"), ("status",),
     ("상태", "status")),
    ("기한", re.compile(r"기한|마감|납기|데드라인|지연"), ("due_date",), ("기한", "마감", "지연", "due", "deadline", "delay", "late")),
    ("담당자·직원", re.compile(r"담당자|직원|사원|인력"), ("assignee_id", "employee_id"),
     ("담당", "직원", "사원", "employee", "assignee", "staff")),
    ("프로젝트", re.compile(r"프로젝트"), ("project_id",), ("프로젝트", "project")),
    ("투입시간(MH)", re.compile(r"MH|공수|투입\s*시간", re.I), ("planned_mh", "actual_mh"), ("mh", "공수", "hour", "시간")),
    ("예산", re.compile(r"예산"), ("budget",), ("예산", "budget")),
    ("문제유형", re.compile(r"문제\s*유형|부적합"), ("issue_type",), ("문제", "issue", "유형")),
]


def missing_concepts(question: str, vocab: Vocabulary) -> list[str]:
    """질문이 요구하는 정보 중 현재 데이터에 해당 컬럼이 없는 것 (예: 매출 CSV에 '부서별 미완료')."""
    all_cols = {c for cols in vocab.columns.values() for c in cols}
    lowered = [str(c).lower() for c in all_cols] + [show(c).lower() for c in all_cols]
    missing = []
    for label, rx, internal, hints in REQUIRED_CONCEPTS:
        if not rx.search(question):
            continue
        if any(c in all_cols for c in internal):
            continue
        if vocab.kind != "standard" and any(h in name for h in hints for name in lowered):
            continue
        missing.append(label)
    return missing


# 질문에 자주 나오는 업무·매출 용어 → 그 정보를 담은 컬럼 이름에 들어 있을 만한 단어.
# 질문에 이 용어가 있는데 맞는 컬럼이 없으면 '없는 정보'로 보고 다른 컬럼으로 대신하지 않는다.
# (모든 의미 관계를 해석하려는 것이 아니라, 검증 가능한 흔한 용어만 확인한다)
BUSINESS_TERMS = [
    ("고객", re.compile(r"고객|거래처"), ("customer", "client", "고객", "거래처")),
    ("상품·제품", re.compile(r"상품|제품|품목"), ("product", "item", "goods", "sku", "상품", "제품", "품목")),
    ("매출", re.compile(r"매출|판매액|판매 금액"), ("sales", "revenue", "매출", "판매액")),
    ("금액", re.compile(r"금액"), ("금액", "액", "amount", "sales", "revenue", "cost", "비용", "예산", "budget")),
    ("이익", re.compile(r"이익|마진|수익"), ("profit", "margin", "income", "이익", "마진", "수익")),
    ("연봉·급여", re.compile(r"연봉|급여|임금|월급|보수"), ("salary", "wage", "pay", "연봉", "급여", "임금")),
    ("만족도", re.compile(r"만족도|만족"), ("satisfaction", "만족")),
    ("주문", re.compile(r"주문"), ("order", "주문")),
    ("수량", re.compile(r"수량|판매량"), ("qty", "quantity", "units", "volume", "수량", "판매량")),
    ("가격", re.compile(r"가격|단가"), ("price", "가격", "단가")),
    ("지역", re.compile(r"지역|지점|도시"), ("region", "area", "city", "location", "branch", "지역", "지점", "도시")),
    ("채널", re.compile(r"채널"), ("channel", "채널")),
]
# '월별'·'달마다'의 월 → 월·날짜 컬럼
MONTH_WORDS = ("월", "달", "월간", "매월")
MONTH_HINTS = ("month", "월", "date", "일자", "날짜")
GROUP_RE = re.compile(r"([가-힣A-Za-z0-9_]+?)\s*(?:별|마다)(?![가-힣])|([가-힣A-Za-z0-9_]+?)별로")


def _matching_cols(vocab: Vocabulary, aliases: tuple) -> list[str]:
    cols = vocab.columns.get("main", {})
    return [c for c in cols if any(a.lower() in c.lower() or a.lower() in show(c).lower() for a in aliases)]


def _inside_value(word: str, question: str, vocab: Vocabulary) -> bool:
    """질문에 나온 용어가 실제 범주값(질문에도 그대로 나온 값)의 일부인가. 예: 유형 = '제품불량'의 '제품'."""
    return any(word in v and v in question for vals in vocab.values.values() for v in vals if len(v) > len(word))


@dataclass
class QuestionCols:
    missing_terms: list = field(default_factory=list)  # 질문 용어인데 맞는 컬럼이 없음 (예: 고객)
    unknown_groups: list = field(default_factory=list)  # 'X별'의 X를 컬럼에 대응시킬 수 없음
    group_cols: set = field(default_factory=set)  # 질문이 요구한 집계 기준 컬럼
    mention_cols: set = field(default_factory=set)  # 질문에 나온 컬럼 (기준·지표 모두)
    metric_cols: set = field(default_factory=set)  # 질문에 나온 숫자 컬럼 (지표)
    verbatim_numeric: set = field(default_factory=set)  # 이름이 질문에 그대로 나온 숫자 컬럼
    month_from_daily: bool = False  # '월별'인데 일 단위 날짜 컬럼만 있음
    open_terms: list = field(default_factory=list)  # (TI_COLUMN_HINTS ON) 이름은 다르지만 의미가 같을 수 있는 후보 컬럼이 있는 용어


def question_columns(question: str, vocab: Vocabulary) -> QuestionCols:
    """질문에서 요구한 컬럼(집계 기준·지표)을 실제 스키마와 대조한다."""
    qc = QuestionCols()
    cols = vocab.columns.get("main", {})
    for label, rx, aliases in BUSINESS_TERMS:
        m = rx.search(question)
        if m:
            found = _matching_cols(vocab, aliases)
            if found:
                qc.mention_cols |= set(found)
            elif label == "금액" and engine_has("multiply") and product_needed(question, vocab):
                continue  # 두 숫자 컬럼의 곱(예: 현재수량 × 단가)으로 계산 가능 → 계획 단계에서 곱셈을 검사
            elif label == "가격" and engine_has("percent") and unit_price_needed(question, vocab):
                continue  # 합계 ÷ 합계(예: 매출액 ÷ 판매수량)로 계산 가능 → 계획 단계에서 나눗셈을 검사
            elif not _inside_value(m.group(0), question, vocab):  # '제품불량'처럼 데이터 값의 일부면 컬럼 요청이 아님
                cands = open_candidates(label, vocab)  # 스위치 OFF·표준 데이터·후보 없음이면 빈 목록 → 기존처럼 '없는 정보'
                if cands:
                    qc.open_terms.append(OpenTerm(label, m.group(0), TERM_KIND[label], cands))
                else:
                    qc.missing_terms.append(label)
    for c in cols:  # 컬럼 이름 자체가 질문에 나온 경우 (sales, 건수 등)
        if len(c) >= 2 and (c.lower() in question.lower() or (show(c) != c and show(c) in question)):
            qc.mention_cols.add(c)
    if vocab.kind != "standard":  # 표준 데이터는 기존 조건 대조(부서·프로젝트·지표)로 확인
        for m in GROUP_RE.finditer(question):
            word = (m.group(1) or m.group(2) or "").strip()
            if not word:
                continue
            target, matched = set(), None
            if word in ("부서", "팀"):  # 부서 정보가 있는지는 REQUIRED_CONCEPTS가 확인, 여기서는 해당 컬럼에 대응
                target = set(_matching_cols(vocab, ("부서", "dept", "department", "team", "팀")))
                if not target:
                    continue
            elif word in MONTH_WORDS or word.endswith("월"):
                target = set(_matching_cols(vocab, MONTH_HINTS)) | {c for c, t in cols.items() if t == "날짜"}
                target |= {c for c, r in vocab.roles.items() if r in ("date_day", "date_month")}
                if target and not any(vocab.roles.get(c) == "date_month" or "month" in c.lower() for c in target):
                    qc.month_from_daily = True  # 월로 묶어 달라는데 일 단위 날짜만 있음 (월 묶음 도구 없음)
            else:
                for label, rx, aliases in BUSINESS_TERMS:
                    if rx.search(word):
                        target, matched = set(_matching_cols(vocab, aliases)), label
                        break
                else:
                    target = {c for c in cols if word.lower() == c.lower() or word == show(c)
                              or (len(word) >= 2 and (word.lower() in c.lower() or word in show(c)))}
            if target:
                qc.group_cols |= target
                qc.mention_cols |= target
            elif matched and any(t.label == matched for t in qc.open_terms):  # '고객별' → 의미 해석 후보로 묶음
                for t in qc.open_terms:
                    if t.label == matched:
                        t.group = True
            elif not any(word == t for t in qc.missing_terms):
                cands = [] if matched else open_group_candidates(vocab, qc)
                if cands:  # (스위치 ON) 이름이 같은 컬럼은 없지만 묶을 수 있는 후보가 있음 → 계획 후 검증
                    qc.open_terms.append(OpenTerm(word, word, "category", cands, group=True))
                else:
                    qc.unknown_groups.append(word)
    # 지표: 질문에 컬럼 이름이 그대로 나오면 그 컬럼만 (예: '불량수량 합계' → 불량수량, 생산수량은 아님)
    verbatim = {c for c in cols if cols.get(c) == "숫자" and len(c) >= 2 and c.lower() in question.lower()}
    qc.metric_cols = verbatim or {c for c in qc.mention_cols if cols.get(c) == "숫자"}
    qc.verbatim_numeric = verbatim
    return qc


# 일반 CSV에서 현재 엔진에 없는 연산 (비율·비중, 열 간 비교, 월 묶음): 다른 계산으로 대신하지 않고 미리 안내
_RATIO_RE = re.compile(r"(비율|비중|점유율|퍼센트|백분율|%|[가-힣]+률|[가-힣]+율)")
_RATIO_OK = ("증감률", "증감율", "변화율", "증가율", "감소율", "성장률")  # compare_periods가 계산
_COMPARE_RE = re.compile(r"보다.{0,15}?(많|적|크|작|높|낮|부족|모자|초과|미만|넘)")  # "안전재고보다 현재수량이 적은"


def engine_has(feature: str) -> bool:
    """엔진이 실제로 지원하는 기능인지 (계약에서 확인 → 엔진 버전과 무관하게 정확, 머지 순서와 무관)."""
    from contracts import AggregateParams, Filter
    if feature == "period":
        return "period" in AggregateParams.model_fields
    if feature == "ratio":
        return "calculate_ratio" in TOOLS
    if feature == "compare":
        return "compare_column" in Filter.model_fields
    if feature == "multiply":  # 엔진 PR #40
        return "multiply_by" in AggregateParams.model_fields
    if feature == "percent":  # 엔진 PR #40
        from contracts import RatioParams
        return "percent" in RatioParams.model_fields
    return False


# ---------------------------------------------------------------------------
# 컬럼 의미 해석 (기능 스위치 TI_COLUMN_HINTS, 기본 OFF, 일반 CSV만)
# 질문 용어(고객·매출 등)나 'X별'의 X와 이름이 같은 컬럼이 없을 때, 의미가 같을 수 있는 후보 컬럼이 있으면
# 바로 막지 않고 Gemini가 고르게 한다. 고른 컬럼은 코드가 검증하고(존재·자료형·다른 용어와 중복·기존 별칭과 충돌·계산 의도),
# 사용자는 승인 화면에서 "질문의 '고객'을 'buyer' 컬럼으로 해석했습니다"를 보고 승인하거나 취소한다.
# 후보가 없으면(명백히 없는 정보) 지금처럼 사전 차단한다.
# ---------------------------------------------------------------------------
_SWITCH_ON = ("1", "true", "on", "yes")


def column_hints_on() -> bool:
    """환경변수 → Streamlit secrets 순서로 TI_COLUMN_HINTS를 읽는다. 없거나 알 수 없는 값이면 OFF."""
    v = os.environ.get("TI_COLUMN_HINTS")
    if v is None:
        try:
            import streamlit as st  # 배포 환경에서만 의미 있음

            v = st.secrets.get("TI_COLUMN_HINTS")  # secrets 파일이 없으면 예외 → OFF
        except Exception:  # noqa: BLE001
            v = None
    return v is not None and str(v).strip().lower() in _SWITCH_ON


@dataclass
class OpenTerm:
    label: str  # BUSINESS_TERMS 이름 또는 'X별'의 X
    word: str  # 질문에 나온 그대로 (승인 화면 표시용)
    kind: str  # category / money / qty / score
    candidates: list
    group: bool = False  # 'X별' 묶음 기준으로 쓰였는가


# 의미 해석을 허용하는 용어와 그 자료형. 그 밖의 용어(주문 등)는 지금처럼 이름이 맞는 컬럼만 쓴다.
TERM_KIND = {"고객": "category", "상품·제품": "category", "지역": "category", "채널": "category",
             "매출": "money", "금액": "money", "이익": "money", "연봉·급여": "money", "가격": "money",
             "수량": "qty", "만족도": "score"}
_QTY_WORDS = ("수량", "판매량", "qty", "quantity", "units", "volume", "개수", "건수", "count", "cnt", "재고", "stock")
_MONEY_WORDS = ("금액", "액", "가격", "단가", "price", "amount", "sales", "revenue", "매출", "cost", "비용", "원가",
                "이익", "profit", "salary", "연봉", "급여", "budget", "예산")
_OTHER_NUM_WORDS = ("일수", "연수", "년수", "기간", "나이", "age", "시간", "hour", "day", "year", "율", "률", "rate",
                    "ratio", "%", "점수", "score", "평점", "rating", "번호", "id", "no")
# 이런 이름의 컬럼은 그 용어(지표)로 해석하지 않는다 (예: 연봉 → 근속연수, 매출 → 판매수량 금지)
KIND_CONFLICTS = {"money": _QTY_WORDS + _OTHER_NUM_WORDS,
                  "qty": _MONEY_WORDS + _OTHER_NUM_WORDS,
                  "score": _MONEY_WORDS + _QTY_WORDS + ("일수", "연수", "나이", "age", "번호", "id")}
# 묶음 기준(범주)으로 해석하지 않는 다른 차원의 이름 (예: 고객 → 창고·공정·등급 금지)
_OTHER_DIMENSIONS = ("창고", "warehouse", "공정", "라인", "line", "등급", "grade", "유형", "type", "분류", "category",
                     "상태", "status", "월", "month", "date", "일자", "날짜", "년", "year", "성별", "gender", "요일")


# 범주 용어마다 '다른 대상'을 가리키는 이름 (예: 고객 ↔ 매장·공급업체·담당자). 이런 컬럼은 후보로 내지 않는다.
_SELLER_SIDE = ("store", "매장", "점포", "shop", "지점", "branch", "site", "공장", "plant", "센터", "center",
                "vendor", "supplier", "공급", "seller", "판매자", "판매처", "brand", "브랜드", "rep", "manager", "직원", "담당")
TERM_CONFLICTS = {
    "고객": _SELLER_SIDE,
    "상품·제품": ("buyer", "customer", "client", "고객", "store", "매장", "점포", "vendor", "supplier", "rep", "manager"),
    "지역": ("buyer", "customer", "client", "vendor", "supplier", "product", "item", "brand", "rep", "manager", "channel"),
    "채널": ("buyer", "customer", "client", "vendor", "supplier", "product", "item", "brand", "region", "rep", "manager"),
}
# Gemini에 알려 줄 용어의 뜻 (후보가 이 뜻이 아니면 쓰지 않게)
TERM_MEANING = {
    "고객": "제품·서비스를 사는 쪽(구매자·고객사). 매장·지점·공급업체·브랜드·담당자는 고객이 아니다",
    "상품·제품": "판매·생산되는 물건(품목). 고객·매장·공급업체는 상품이 아니다",
    "지역": "지리적 위치(도시·권역). 매장·고객·상품은 지역이 아니다",
    "채널": "판매·유입 경로(온라인·오프라인·앱 등)",
    "매출": "판매로 번 금액", "금액": "돈 단위의 값", "이익": "매출에서 비용을 뺀 금액",
    "연봉·급여": "직원에게 주는 보수", "가격": "한 단위의 값", "수량": "개수 단위의 양", "만족도": "만족 정도의 점수",
}


def _names(col: str) -> tuple:
    return col.lower(), show(col).lower()


def _claimed_by_other(col: str, label: str) -> bool:
    """다른 업무 용어·필수 개념의 별칭과 맞는 컬럼인가 (예: region은 '지역' → '고객'으로 쓰지 않음)."""
    names = _names(col)
    for other, _rx, aliases in BUSINESS_TERMS:
        if other != label and any(a.lower() in n for a in aliases for n in names):
            return True
    return any(h.lower() in n for _l, _rx, _i, hints in REQUIRED_CONCEPTS for h in hints for n in names)


def _conflicts(col: str, words) -> bool:
    names = _names(col)
    return any(w.lower() in n for w in words for n in names)


def open_candidates(label: str, vocab: Vocabulary) -> list[str]:
    """(스위치 ON, 일반 CSV) 질문 용어와 의미가 같을 수 있는 후보 컬럼. 없으면 [] → 기존처럼 사전 차단."""
    kind = TERM_KIND.get(label)
    if not kind or vocab.kind == "standard" or not column_hints_on():
        return []
    out = []
    for c in vocab.columns.get("main", {}):
        role = vocab.roles.get(c)
        if kind == "category":
            if role != "category" or _conflicts(c, _OTHER_DIMENSIONS + TERM_CONFLICTS.get(label, ())):
                continue
        elif role != "number" or _conflicts(c, KIND_CONFLICTS[kind]):
            continue
        if _claimed_by_other(c, label):
            continue
        out.append(c)
    return out


def open_group_candidates(vocab: Vocabulary, qc: "QuestionCols") -> list[str]:
    """(스위치 ON, 일반 CSV) 'X별'의 X와 이름이 같은 컬럼이 없을 때 묶음 기준이 될 수 있는 후보 (범주형, 다른 용도로 안 쓰인 것)."""
    if vocab.kind == "standard" or not column_hints_on():
        return []
    return [c for c in vocab.columns.get("main", {})
            if vocab.roles.get(c) == "category" and c not in qc.mention_cols
            and not _conflicts(c, _OTHER_DIMENSIONS) and not _claimed_by_other(c, "")]


def open_cols(qc: "QuestionCols", group: bool) -> set:
    """의미 해석 후보 컬럼 (group=True: 묶음·범주 용어, False: 지표 용어). OFF면 항상 빈 집합."""
    return {c for t in qc.open_terms if (t.kind == "category") == group for c in t.candidates}


def open_term_check(tool: str, params: dict, qc: "QuestionCols", vocab: Vocabulary) -> tuple[list[str], list[str]]:
    """계획이 의미 해석 용어를 후보 컬럼 하나에 정확히 대응시켰는지. (problems, 승인 화면 안내)"""
    if not qc.open_terms:
        return [], []
    table = params.get("table", "main")
    groups = params.get("group_by")
    groups = groups if isinstance(groups, list) else ([groups] if groups else [])
    gcols = {vocab.resolve(table, g) for g in groups} - {None}
    vcols = {vocab.resolve(table, params.get(k)) for k in ("value_column", "multiply_by", "denominator_column")
             if params.get(k)} - {None}
    fcols = {vocab.resolve(table, f.get("column")) for f in (params.get("filters") or [])
             if isinstance(f, dict) and f.get("column")} - {None}
    problems, notes, taken = [], [], {}
    for t in qc.open_terms:
        cands = ", ".join(show(c) for c in t.candidates)
        if t.group:
            pool, role = gcols, "묶음 기준"
        elif t.kind == "category":
            pool, role = gcols | fcols, "조건"
        else:
            pool, role = vcols, "지표"
        hit = sorted(c for c in pool if c in t.candidates)
        if not hit:
            if t.group:
                wrong = sorted(gcols - qc.mention_cols)
                if wrong:
                    problems.append(f"질문의 묶음 기준({t.word}별)을 후보({cands})가 아닌 "
                                    f"'{', '.join(show(c) for c in wrong)}'(으)로 계획했습니다")
                else:
                    problems.append(f"질문의 묶음 기준({t.word}별)이 계획에 없습니다 (group_by 필요, 후보: {cands})")
            else:
                problems.append(f"질문의 '{t.word}'에 해당하는 컬럼을 계획이 쓰지 않았습니다 (후보: {cands})")
            continue
        if len(hit) > 1:
            problems.append(f"질문의 '{t.word}'을(를) 여러 컬럼({', '.join(show(c) for c in hit)})으로 해석해 확실하지 않습니다")
            continue
        col = hit[0]
        if col in taken:
            problems.append(f"'{show(col)}' 컬럼을 '{taken[col]}'와(과) '{t.word}' 두 용어에 같이 썼습니다")
            continue
        taken[col] = t.word
        alt = [show(c) for c in t.candidates if c != col]
        notes.append(f"질문의 '{t.word}'을(를) '{show(col)}' 컬럼({role})으로 해석했습니다."
                     + (f" 다른 후보: {', '.join(alt)}." if alt else "")
                     + " 맞지 않으면 [취소]를 누르고 질문에 컬럼 이름을 넣어 다시 물어 주세요.")
    return problems, notes


def open_term_hint(qc: "QuestionCols", clarify_tool: str, limit_tool: str) -> str:
    """Gemini에 줄 해석 안내 (컬럼 이름만, 데이터 값·자유 텍스트 없음)."""
    if not qc.open_terms:
        return ""
    lines = ["[컬럼 해석 안내]"]
    for t in qc.open_terms:
        use = "group_by" if t.group else ("value_column" if t.kind != "category" else "group_by 또는 filters")
        meaning = f" '{t.word}'의 뜻: {TERM_MEANING[t.label]}." if t.label in TERM_MEANING else ""
        lines.append(f"- 질문의 '{t.word}'과(와) 이름이 같은 컬럼은 없다.{meaning} 이름이 다른 후보: "
                     f"{', '.join(show(c) for c in t.candidates)}. 후보가 질문의 용어와 같은 대상을 가리킬 때만 그 컬럼 하나를 "
                     f"{use}에 써라. 후보가 다른 대상(예: 고객 대신 매장)을 가리키면 쓰지 마라.")
    lines.append(f"- 질문의 대상을 가리키는 컬럼이 후보에 없으면 {limit_tool}를 호출하라. "
                 f"후보가 여럿인데 어느 것인지 이름만으로 확실하지 않으면 {clarify_tool}를 호출하라. "
                 "후보가 아닌 컬럼이나 다른 계산으로 대신하지 마라.")
    return "\n".join(lines)


def generic_unsupported(question: str, vocab: Vocabulary, qc: QuestionCols) -> list[str]:
    if vocab.kind == "standard":
        return []
    cols = vocab.columns.get("main", {})
    reasons = []
    for m in (_RATIO_RE.finditer(question) if not engine_has("ratio") else []):
        word = m.group(1)
        if any(ok in question for ok in _RATIO_OK) and word in _RATIO_OK:
            continue
        if word in _RATIO_OK or any(word in c or c in word for c in cols if len(c) >= 2):
            continue  # 증감률 또는 그 이름의 컬럼이 이미 있음
        reasons.append(f"'{word}' 같은 비율·비중 계산 (두 값을 나누는 도구가 아직 없음)")
        break
    if _COMPARE_RE.search(question) and len(qc.verbatim_numeric) >= 2 and not engine_has("compare"):
        reasons.append("두 컬럼 값을 서로 비교하는 조건 (컬럼 간 비교 필터가 아직 없음)")
    if qc.month_from_daily and not engine_has("period"):
        reasons.append("일 단위 날짜를 월별로 묶어 여러 달을 나열하는 집계 (월 묶음 도구가 아직 없음, 두 달 비교는 가능)")
    return reasons


def generic_mismatch(tool: str, params: dict, qc: QuestionCols, vocab: Vocabulary) -> list[str]:
    """일반 CSV: 계획 인자가 질문이 요구하지 않은 컬럼으로 바뀌었으면 사유 목록 (설명 문구가 아니라 실제 인자를 본다)."""
    if vocab.kind == "standard":
        return []
    table = params.get("table", "main")
    problems = []
    groups = params.get("group_by")
    groups = groups if isinstance(groups, list) else ([groups] if groups else [])
    for g in groups:
        col = vocab.resolve(table, g)
        if col and vocab.roles.get(col) == "text":
            problems.append(f"'{show(col)}'은(는) 자유 텍스트 컬럼이라 묶음 기준으로 쓰지 않습니다")
            continue
        if col and col not in qc.mention_cols and col not in open_cols(qc, group=True):
            want = ", ".join(show(c) for c in sorted(qc.group_cols)) or "(질문에 집계 기준 없음)"
            problems.append(f"집계 기준 '{show(col)}'은(는) 질문에서 요구하지 않았습니다 (요청 기준: {want})")
    if tool in ("aggregate", "calculate_ratio") and qc.group_cols:  # 질문의 'X별'이 계획에서 빠지면 전체 값만 나옴
        resolved = {vocab.resolve(table, g) for g in groups}
        if not resolved & qc.group_cols:
            want = ", ".join(show(c) for c in sorted(qc.group_cols))
            problems.append(f"질문의 묶음 기준({want}별)이 계획에 없습니다 (group_by 필요)")
    value = params.get("value_column")
    if value and qc.metric_cols:
        col = vocab.resolve(table, value)
        if col and col not in qc.metric_cols and col not in open_cols(qc, group=False):
            problems.append(f"지표 '{show(col)}'은(는) 질문의 지표({', '.join(show(c) for c in sorted(qc.metric_cols))})와 다릅니다")
    return problems


# ---------------------------------------------------------------------------
# 계산 의도 대조: 질문이 합계를 물었는지 비율·비중을 물었는지, 비교·월별을 물었는지와 실제 인자를 맞춰 본다.
# (설명 문구가 아니라 실제 도구·인자를 검사한다)
# ---------------------------------------------------------------------------
_SHARE_RE = re.compile(r"비중|점유율|구성비|차지하는")
_RATE_STEM_RE = re.compile(r"([가-힣A-Za-z0-9]{2,}?)(?:률|율)|([가-힣A-Za-z0-9]+)\s*(?:비율|비중|퍼센트)")  # 불량률은 → 불량
_MONTH_INTENT_RE = re.compile(r"월별|월간|매월|달마다|월마다|달별|월 단위|월단위")
RATIO_KIND_LABEL = {"share": "비중", "column_ratio": "컬럼 비율", "condition_rate": "조건 비율"}


def ratio_intent(question: str) -> Optional[str]:
    """'share'(비중) / 'rate'(…률·…율·비율) / None. 증감률·변화율은 compare_periods가 계산하므로 비율 요청으로 보지 않는다."""
    if _SHARE_RE.search(question):
        return "share"
    for m in _RATIO_RE.finditer(question):
        if m.group(1) not in _RATIO_OK:
            return "rate"
    return None


def ratio_stems(question: str) -> list[str]:
    """'불량률' → '불량', '처리완료 비율' → '처리완료' (분자 컬럼·조건 확인용)."""
    out = []
    for m in _RATE_STEM_RE.finditer(question):
        stem = (m.group(1) or m.group(2) or "").strip()
        if stem and stem + "률" not in _RATIO_OK and stem + "율" not in _RATIO_OK and len(stem) >= 1:
            out.append(stem)
    return out


def _fvals(f: dict) -> list[str]:
    v = f.get("value")
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and f.get("op") == "in":
        return [x.strip() for x in v.split(",")]
    return [] if v is None else [str(v)]


def fill_ratio_denominator(params: dict, vocab: Vocabulary) -> dict:
    """column_ratio에 분모가 빠졌을 때, 분자를 뺀 숫자 컬럼이 하나뿐이면 그것을 분모로 채운다 (승인 전 계획에 명시됨).
    후보가 여럿이면 채우지 않는다 → intent_check가 되묻는다."""
    if params.get("kind") != "column_ratio" or params.get("denominator_column") or not params.get("value_column"):
        return params
    table = params.get("table", "main")
    num = vocab.resolve(table, params["value_column"])
    others = [c for c, t in vocab.table_of(table).items() if t == "숫자" and c != num]
    return dict(params, denominator_column=others[0]) if len(others) == 1 else params


# ---------------------------------------------------------------------------
# 곱셈(금액 = 수량 × 단가)과 ×100 하지 않는 나눗셈(단가 = 매출액 ÷ 판매수량): 엔진 PR #40
# 질문이 그 계산을 요구하고, 그 값을 담은 컬럼이 데이터에 없을 때만 허용한다.
# ---------------------------------------------------------------------------
_PRODUCT_RE = re.compile(r"금액|총액|가치|곱(?:한|해|하기|셈)|×")
_PER_UNIT_RE = re.compile(r"단가|평균\s*(?:판매\s*)?가격|개당|건당|당\s*(?:매출|금액|가격)|÷|나눈|나눠|나누")
_OTHER_CALC_RE = re.compile(r"합계|총|평균|차이|초과|증감|변화|추이|건수|개수")
_DIVIDE_RE = re.compile(r"÷|나눈|나눠|나누")
PRICE_ALIASES = ("단가", "가격", "price")
QTY_ALIASES = ("수량", "판매량", "qty", "quantity", "units", "volume")
AMOUNT_ALIASES = ("금액", "총액", "매출", "액", "amount", "sales", "revenue")
AGG_FN = {"sum": "SUM", "mean": "AVG", "max": "MAX", "min": "MIN"}


def _numeric_cols(vocab: Vocabulary, table: str = "main") -> list[str]:
    return [c for c, t in vocab.table_of(table).items() if t == "숫자"]


def _like(cols, aliases) -> list[str]:
    return [c for c in cols if any(a.lower() in str(c).lower() or a.lower() in show(c).lower() for a in aliases)]


def _named_numeric(question: str, vocab: Vocabulary, table: str = "main") -> list[str]:
    """이름이 질문에 그대로 나온 숫자 컬럼 (질문에 나온 순서)."""
    q = question.lower()
    found = [(q.find(str(c).lower()), c) for c in _numeric_cols(vocab, table) if len(str(c)) >= 2 and str(c).lower() in q]
    return [c for _, c in sorted(found)]


def product_needed(question: str, vocab: Vocabulary, table: str = "main") -> bool:
    """질문이 금액(두 컬럼의 곱)을 묻고, 금액을 담은 컬럼은 없고, 곱할 숫자 컬럼이 둘 이상 있다."""
    if not _PRODUCT_RE.search(question):
        return False
    nums = _numeric_cols(vocab, table)
    amount = [c for c in _like(nums, AMOUNT_ALIASES) if c not in _like(nums, PRICE_ALIASES)]
    return not amount and len(nums) >= 2


def unit_price_needed(question: str, vocab: Vocabulary, table: str = "main") -> bool:
    """질문이 단가처럼 ×100 하지 않는 나눗셈을 묻고, 단가를 담은 컬럼은 없다 (있으면 그 컬럼을 그대로 집계)."""
    if ratio_intent(question):
        return False
    # 'A 대비 B'는 두 컬럼 이름이 모두 나오고 다른 계산(합계·차이·초과 등)이 없을 때만 나눗셈으로 본다 (예: 계획 대비 실적 초과 ≠ 나눗셈)
    by_ratio = "대비" in question and len(_named_numeric(question, vocab, table)) >= 2 and not _OTHER_CALC_RE.search(question)
    if not (_PER_UNIT_RE.search(question) or by_ratio):
        return False
    nums = _numeric_cols(vocab, table)
    return not _like(nums, PRICE_ALIASES) and len(nums) >= 2


def expected_product(question: str, vocab: Vocabulary, table: str = "main") -> tuple[Optional[set], bool, str]:
    """(곱할 두 컬럼, 질문에 둘 다 나왔는지, 정할 수 없을 때 사유)."""
    nums = _numeric_cols(vocab, table)
    named = _named_numeric(question, vocab, table)
    price = _like(nums, PRICE_ALIASES)
    qty = [c for c in _like(nums, QTY_ALIASES) if c not in price]
    if len(named) == 2:
        return set(named), True, ""
    if len(named) == 1:
        other = [c for c in (price if named[0] not in price else qty) if c != named[0]]
        if len(other) == 1:
            return {named[0], other[0]}, False, ""
    elif not named and len(price) == 1 and len(qty) == 1:
        return {qty[0], price[0]}, False, ""
    cands = ", ".join(show(c) for c in nums)
    return None, False, (f"곱할 두 컬럼을 질문에서 정할 수 없습니다 (숫자 컬럼: {cands}). "
                         f"'현재수량과 단가를 곱한 금액'처럼 두 컬럼을 알려 주세요")


def expected_division(question: str, vocab: Vocabulary, table: str = "main") -> tuple[Optional[tuple], bool, str]:
    """((분자, 분모), 질문에 둘 다 나왔는지, 정할 수 없을 때 사유)."""
    nums = _numeric_cols(vocab, table)
    named = _named_numeric(question, vocab, table)
    price = _like(nums, PRICE_ALIASES)
    amount = [c for c in _like(nums, AMOUNT_ALIASES) if c not in price]
    qty = [c for c in _like(nums, QTY_ALIASES) if c not in price and c not in amount]
    ask = (f"나눌 분자·분모를 질문에서 정할 수 없습니다 (숫자 컬럼: {', '.join(show(c) for c in nums)}). "
           f"'매출액 ÷ 판매수량'처럼 분자와 분모를 알려 주세요")
    if len(named) == 2:
        a, b = named
        if re.search(re.escape(str(a)) + r"\S*\s*대비", question):  # 'A 대비 B' → B ÷ A
            return (b, a), True, ""
        if _DIVIDE_RE.search(question):  # 'A ÷ B', 'A를 B로 나눈' → 앞이 분자
            return (a, b), True, ""
        if a in amount and b in qty:
            return (a, b), True, ""
        if b in amount and a in qty:
            return (b, a), True, ""
        return None, False, ask
    if len(named) == 1:
        if named[0] in amount and len(qty) == 1:
            return (named[0], qty[0]), False, ""
        if named[0] in qty and len(amount) == 1:
            return (amount[0], named[0]), False, ""
    elif not named and len(amount) == 1 and len(qty) == 1:
        return (amount[0], qty[0]), False, ""
    return None, False, ask


def _arith_check(tool: str, params: dict, question: str, vocab: Vocabulary, table: str, cols: dict) -> tuple[list[str], list[str]]:
    """곱셈(multiply_by)·×100 없는 나눗셈(percent=false) ↔ 질문의 계산 의도."""
    problems: list[str] = []
    notices: list[str] = []
    generic = vocab.kind != "standard"
    mult = params.get("multiply_by")
    need_prod = product_needed(question, vocab, table)
    if tool == "aggregate" and mult:
        a, b = vocab.resolve(table, params.get("value_column")), vocab.resolve(table, mult)
        shown = f"{show(a or params.get('value_column'))} × {show(b or mult)}"
        if params.get("op") == "count" or not params.get("value_column"):
            problems.append("개수(count)에는 곱셈(multiply_by)을 쓸 수 없습니다")
        elif not need_prod:
            problems.append(f"질문은 금액·곱셈을 요구하지 않았는데 '{shown}' 곱셈을 계획했습니다. 질문에 나온 값만 그대로 계산합니다")
        else:
            for c in (a, b):
                if c and cols.get(c) != "숫자":
                    problems.append(f"'{show(c)}'은(는) 숫자 컬럼이 아니라 곱할 수 없습니다")
            if a and a == b:
                problems.append("같은 컬럼끼리 곱했습니다")
            want, named, why = expected_product(question, vocab, table)
            if want is None:
                problems.append(why)
            elif {a, b} != want:
                order = sorted(want, key=lambda c: (c in _like(want, PRICE_ALIASES), str(c)))  # 수량 × 단가 순서로 표시
                problems.append(f"곱셈 '{shown}'은(는) 질문에 맞는 '{' × '.join(show(c) for c in order)}'와 다릅니다")
            elif not named:
                fn = AGG_FN.get(params.get("op"), "SUM")
                notices.append(f"질문에 곱할 컬럼이 없어 {fn}({shown})로 계산합니다. 다른 컬럼이 맞다면 질문에 두 컬럼을 적어 다시 물어 주세요.")
    elif need_prod and engine_has("multiply") and tool in ("aggregate", "calculate_ratio"):
        problems.append("질문은 금액(두 컬럼의 곱)을 물었는데 곱셈 없이 계산합니다. aggregate의 multiply_by로 곱해야 합니다")

    per_unit = unit_price_needed(question, vocab, table)
    if tool == "calculate_ratio" and params.get("percent") is False:
        num = vocab.resolve(table, params.get("value_column"))
        den = vocab.resolve(table, params.get("denominator_column"))
        if params.get("kind") != "column_ratio":
            problems.append("×100 하지 않는 나눗셈(percent=false)은 column_ratio에서만 씁니다")
        elif not (num and den):
            problems.append("나눗셈에는 분자(value_column)와 분모(denominator_column)가 모두 필요합니다")
        elif ratio_intent(question):
            problems.append("질문은 백분율(…률·비율·%)을 물었는데 ×100 하지 않는 나눗셈입니다")
        elif not per_unit:
            problems.append(f"질문은 나눗셈(단가·…당)을 요구하지 않았는데 '{show(num)} ÷ {show(den)}' 나눗셈을 계획했습니다")
        else:
            if num == den:
                problems.append("나눗셈의 분자와 분모가 같은 컬럼입니다")
            want, named, why = expected_division(question, vocab, table)
            if want is None:
                problems.append(why)
            elif (num, den) != want:
                problems.append(f"나눗셈 'SUM({show(num)}) ÷ SUM({show(den)})'은(는) 질문에 맞는 "
                                f"'SUM({show(want[0])}) ÷ SUM({show(want[1])})'와 다릅니다")
            elif not named:
                notices.append(f"질문에 분자·분모가 없어 SUM({show(num)}) ÷ SUM({show(den)})로 계산합니다 (×100 하지 않음, 행별 값의 평균 아님). "
                               f"다른 컬럼이 맞다면 질문에 분자와 분모를 적어 다시 물어 주세요.")
    elif per_unit and engine_has("percent"):
        if tool == "calculate_ratio":
            problems.append("질문은 단가처럼 ×100 하지 않는 나눗셈인데 백분율(×100)로 계산합니다 (percent=false 필요)")
        elif tool == "aggregate" and generic:
            problems.append("질문은 나눗셈(합계 ÷ 합계)을 물었는데 합계·평균 계산입니다. calculate_ratio(column_ratio, percent=false)여야 합니다")
    return problems, notices


def intent_check(tool: str, params: dict, question: str, qc: "QuestionCols", vocab: Vocabulary) -> tuple[list[str], list[str]]:
    """(실행하면 안 되는 사유, 승인 전에 보여 줄 안내). 질문의 계산 의도와 계획 인자가 다르면 사유를 돌려준다."""
    problems: list[str] = []
    notices: list[str] = []
    generic = vocab.kind != "standard"
    table = params.get("table", "main")
    cols = vocab.table_of(table)
    intent = ratio_intent(question)

    # 1) 비율 도구 ↔ 비율 요청 (×100 하지 않는 나눗셈은 아래 _arith_check에서 따로 검사)
    if tool == "calculate_ratio" and params.get("percent") is not False:
        kind = params.get("kind")
        if not intent:
            problems.append("질문은 비율·비중을 묻지 않았는데 비율 계산(calculate_ratio)을 계획했습니다. 합계·개수 등 질문의 계산만 허용합니다")
        elif intent == "share" and kind != "share":
            problems.append(f"질문은 전체 대비 비중을 물었는데 '{RATIO_KIND_LABEL.get(kind, kind)}' 계산입니다")
        elif intent == "rate" and kind == "share":
            problems.append("질문은 비율(…률)을 물었는데 전체 대비 비중 계산입니다 (분자·분모가 다름)")
        num = vocab.resolve(table, params.get("value_column"))
        den = vocab.resolve(table, params.get("denominator_column"))
        if kind == "column_ratio" and not (params.get("value_column") and params.get("denominator_column")):
            problems.append("column_ratio에는 분자(value_column)와 분모(denominator_column)가 모두 필요합니다")
        if kind == "condition_rate" and not params.get("condition"):
            problems.append("condition_rate에는 분자 조건(condition)이 필요합니다")
        if intent and kind == "column_ratio" and num and den:
            stems = ratio_stems(question)
            if num == den:
                problems.append("비율의 분자와 분모가 같은 컬럼입니다")
            if not (num in qc.verbatim_numeric or any(s in num or s in show(num) for s in stems)):
                problems.append(f"분자 '{show(num)}'은(는) 질문의 비율({', '.join(stems) or '-'})과 맞지 않습니다")
            named_dens = qc.verbatim_numeric - {num}
            if named_dens and den not in named_dens:
                problems.append(f"분모 '{show(den)}'은(는) 질문에 나온 분모({', '.join(show(c) for c in sorted(named_dens))})와 다릅니다")
            elif not named_dens:
                others = [c for c, t in cols.items() if t == "숫자" and c != num]
                if len(others) > 1:  # 분모 후보가 여럿이면 추측하지 않고 되묻는다
                    problems.append(f"분모가 질문에 없고 후보가 여러 개({', '.join(show(c) for c in others)})라 정할 수 없습니다. "
                                    f"'… 대비 {show(num)} 비율'처럼 분모를 알려 주세요")
                else:
                    notices.append(f"분모를 질문에서 정하지 않아 '{show(den)}'을(를) 분모로 씁니다 "
                                   f"(비율 = {show(num)} 합계 ÷ {show(den)} 합계). 다른 분모가 맞다면 질문에 분모를 적어 다시 물어 주세요.")
        if intent and kind == "condition_rate":
            for f in params.get("condition") or []:
                missing = [v for v in _fvals(f) if v and v not in question]
                if missing:
                    problems.append(f"조건 값 '{', '.join(missing)}'은(는) 질문에 없는 조건입니다")
    elif generic and intent and tool == "aggregate" and engine_has("ratio") and not params.get("multiply_by"):
        problems.append("질문은 비율·비중을 물었는데 합계·개수·평균 계산입니다. 비율 계산(calculate_ratio)이어야 합니다")

    # 2) 컬럼끼리 비교 ↔ 질문의 비교 대상
    cmp_filters = [f for f in params.get("filters") or [] if f.get("compare_column")]
    for f in cmp_filters:
        a, b = vocab.resolve(table, f.get("column")), vocab.resolve(table, f.get("compare_column"))
        if not (a in qc.verbatim_numeric and b in qc.verbatim_numeric) and generic:
            problems.append(f"비교 '{show(a or f.get('column'))} {f.get('op')} {show(b or f.get('compare_column'))}'은(는) 질문의 비교 대상과 다릅니다")
    compare_intent = generic and _COMPARE_RE.search(question) and len(qc.verbatim_numeric) >= 2
    if compare_intent and engine_has("compare") and tool in ("aggregate", "calculate_ratio") and not cmp_filters:
        problems.append(f"질문의 컬럼 비교({', '.join(show(c) for c in sorted(qc.verbatim_numeric))})가 계획에 없습니다")

    # 3) 월별 묶음 ↔ 질문의 월별 요청
    groups = params.get("group_by") or []
    groups = groups if isinstance(groups, list) else [groups]
    resolved = [vocab.resolve(table, g) for g in groups]
    if params.get("period") == "month":
        if not (qc.month_from_daily or _MONTH_INTENT_RE.search(question)):
            problems.append("질문은 월별 집계를 요청하지 않았는데 월별로 묶었습니다")
        if generic and not any(vocab.roles.get(c) in ("date_day", "date_month") for c in resolved if c):
            problems.append("월별로 묶을 날짜 컬럼이 묶음 기준에 없습니다")
    elif generic and qc.month_from_daily and engine_has("period") and tool in ("aggregate", "calculate_ratio") \
            and any(vocab.roles.get(c) == "date_day" for c in resolved if c):
        problems.append("질문은 월별을 물었는데 일 단위 날짜로 묶었습니다 (period=month 필요)")

    # 4)·5) 곱셈·×100 없는 나눗셈 (엔진 PR #40)
    p2, n2 = _arith_check(tool, params, question, vocab, table, cols)
    return problems + p2, notices + n2


def visible_columns(vocab: Vocabulary) -> list[str]:
    from agent.context import MAX_NAME_LEN, safe_label
    cols = vocab.columns.get("main", {})
    return [show(c) for c in cols if safe_label(c, MAX_NAME_LEN) and safe_label(show(c), MAX_NAME_LEN)]


def plan_fingerprint(plan: AnalysisPlan) -> str:
    """승인 대상 계획의 지문 (질문·단계·인자·설명 전체)."""
    text = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def recheck_plan(plan: AnalysisPlan) -> Optional[str]:
    """실행 직전 구조 재검증. 문제가 있으면 사유 문자열."""
    if not plan.steps:
        return "실행할 단계가 없습니다."
    if len(plan.steps) > MAX_STEPS:
        return f"분석 단계는 최대 {MAX_STEPS}개까지 실행합니다."
    if len({s.id for s in plan.steps}) != len(plan.steps):
        return "단계 ID가 중복되었습니다."
    for s in plan.steps:
        spec = TOOLS.get(s.tool)
        if spec is None:
            return f"지원하지 않는 분석 도구입니다: {s.tool}"
        try:
            spec.params.model_validate(s.params)
        except ValidationError:
            return f"{s.id}({s.tool})의 입력 형식이 잘못되었습니다."
    return None
