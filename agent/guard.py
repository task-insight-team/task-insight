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
from dataclasses import dataclass
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
    for flt in params.get("filters") or []:
        col = _check_column(vocab, table, flt["column"])
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
