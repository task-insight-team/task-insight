"""LLM이 고른 도구 호출을 실행 전에 검증한다 (실행은 하지 않음).

엔진(run_tool)도 입력을 검증하지만, 계획 단계에서 미리 걸러 사용자에게 승인받을 계획 자체를 깨끗하게 만든다.
- 허용 목록(engine.TOOLS)에 있는 도구인가
- contracts의 *Params 모델로 형식이 맞는가 (모델 검증만, 계산 없음)
- 컬럼이 데이터에 있고 자유 텍스트 컬럼이 아닌가
- 필터 값(부서·프로젝트·직원 등)이 데이터에 실제로 있는가  ← 엔진은 '없는 값'과 '0건'을 구분하지 못함
- 기준일(as_of)은 외부에서 받은 값으로 고정
"""

from __future__ import annotations

import hashlib
import json
import re
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
    if name == "cost_vs_budget" and params.get("project_id"):
        _check_values(vocab, "project_id", "==", params["project_id"])
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


def unscoped_department_notices(question: str, steps: list, vocab: Vocabulary) -> list[str]:
    """질문에 나온 부서로 좁혀지지 않은 단계가 있으면 사용자에게 알린다 (일부 도구는 부서 필터가 없음)."""
    if vocab.kind != "standard":
        return []
    depts = [d for d in vocab.values.get("department", []) if d != "미배정"]
    named = [d for d in depts if any(stem in d or d in stem for stem in _DEPT_RE.findall(question)) or d in question]
    notes = []
    for dept in named:
        for s in steps:
            filtered = any(vocab.resolve(s.params.get("table", "main"), f.get("column")) == "department"
                           and dept in str(f.get("value")) for f in s.params.get("filters") or [])
            groups = s.params.get("group_by")
            groups = groups if isinstance(groups, list) else [groups]
            if filtered:
                continue
            if any(vocab.resolve("main", g) == "department" for g in groups if g):
                notes.append(f"질문한 '{dept}' 부서의 값은 결과 표의 '{dept}' 행을 확인하세요.")
            else:
                notes.append(f"'{s.purpose}'은(는) '{dept}' 부서로 좁히지 않은 전체 결과입니다 (이 분석 도구는 부서 조건을 지원하지 않습니다).")
    return list(dict.fromkeys(notes))


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
