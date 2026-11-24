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
    "aggregate": ["value_column", "group_by"],
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
            elif not _inside_value(m.group(0), question, vocab):  # '제품불량'처럼 데이터 값의 일부면 컬럼 요청이 아님
                qc.missing_terms.append(label)
    for c in cols:  # 컬럼 이름 자체가 질문에 나온 경우 (sales, 건수 등)
        if len(c) >= 2 and (c.lower() in question.lower() or (show(c) != c and show(c) in question)):
            qc.mention_cols.add(c)
    if vocab.kind != "standard":  # 표준 데이터는 기존 조건 대조(부서·프로젝트·지표)로 확인
        for m in GROUP_RE.finditer(question):
            word = (m.group(1) or m.group(2) or "").strip()
            if not word:
                continue
            target = set()
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
                        target = set(_matching_cols(vocab, aliases))
                        break
                else:
                    target = {c for c in cols if word.lower() == c.lower() or word == show(c)
                              or (len(word) >= 2 and (word.lower() in c.lower() or word in show(c)))}
            if target:
                qc.group_cols |= target
                qc.mention_cols |= target
            elif not any(word == t for t in qc.missing_terms):
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
    return False


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
        if col and col not in qc.mention_cols:
            want = ", ".join(show(c) for c in sorted(qc.group_cols)) or "(질문에 집계 기준 없음)"
            problems.append(f"집계 기준 '{show(col)}'은(는) 질문에서 요구하지 않았습니다 (요청 기준: {want})")
    value = params.get("value_column")
    if value and qc.metric_cols:
        col = vocab.resolve(table, value)
        if col and col not in qc.metric_cols:
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


def intent_check(tool: str, params: dict, question: str, qc: "QuestionCols", vocab: Vocabulary) -> tuple[list[str], list[str]]:
    """(실행하면 안 되는 사유, 승인 전에 보여 줄 안내). 질문의 계산 의도와 계획 인자가 다르면 사유를 돌려준다."""
    problems: list[str] = []
    notices: list[str] = []
    generic = vocab.kind != "standard"
    table = params.get("table", "main")
    cols = vocab.table_of(table)
    intent = ratio_intent(question)

    # 1) 비율 도구 ↔ 비율 요청
    if tool == "calculate_ratio":
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
    elif generic and intent and tool == "aggregate" and engine_has("ratio"):
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
    return problems, notices


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
