"""도구 목록과 안전한 실행 진입점.

agent는 run_tool만 호출한다. LLM이 만든 params는 여기서 Pydantic으로 검증되고,
허용 목록(TOOLS)에 없는 도구나 형식이 틀린 입력은 실행되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Union

import pandas as pd
from pydantic import BaseModel, ValidationError

from contracts import (
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
from engine import tools
from engine.loader import DATASET_VERSION, Dataset


@dataclass(frozen=True)
class ToolSpec:
    func: Callable[[Dataset, BaseModel], ToolResult]
    params: type[BaseModel]
    description: str  # LLM에게 보여줄 설명


TOOLS: dict[str, ToolSpec] = {
    "aggregate": ToolSpec(tools.aggregate, AggregateParams,
        "합계(sum)·평균(mean)·개수(count)·최댓값(max)·최솟값(min). table(main/employees/projects), group_by, filters, sort, top_n. period=month면 group_by의 날짜 컬럼을 월별로 묶는다(월별 추이). 일반 CSV에도 쓸 수 있다."),
    "calculate_ratio": ToolSpec(tools.calculate_ratio, RatioParams,
        "비율(%). kind=share: 그룹별 비중(value_column 합계 또는 건수, group_by 필수) / column_ratio: 합계(value_column)÷합계(denominator_column) (예: 불량률) / condition_rate: condition에 맞는 행 수÷전체 행 수 (예: 완료 비율). group_by·filters·period=month. 일반 CSV에도 쓸 수 있다."),
    "compare_periods": ToolSpec(tools.compare_periods, ComparePeriodsParams,
        "두 달(YYYY-MM)의 건수·합계·평균 비교, 증감과 증감률. previous 생략 시 전월. 일반 CSV에도 쓸 수 있다."),
    "count_overdue_tasks": ToolSpec(tools.count_overdue_tasks, CountOverdueParams,
        "기준일(as_of) 기준 기한이 지난 미완료 업무 수와 목록. group_by로 그룹별."),
    "summarize_by_category": ToolSpec(tools.summarize_by_category, SummarizeByCategoryParams,
        "부서·프로젝트·분류·상태 등 범주별 전체·완료·미완료·완료율. 부서별이면 인원과 1인당 미완료도."),
    "calculate_completion_rate": ToolSpec(tools.calculate_completion_rate, CompletionRateParams,
        "완료율(완료 건수/전체 건수). group_by로 그룹별, 낮은 순 정렬."),
    "find_recurring_issues": ToolSpec(tools.find_recurring_issues, FindRecurringParams,
        "같은 문제유형이 min_count건 이상 반복된 항목과 관련 프로젝트·업무."),
    "mh_overrun": ToolSpec(tools.mh_overrun, MhOverrunParams,
        "계획 대비 실적 MH 초과. 그룹별 초과MH 합계, 많은 순 정렬."),
    "cost_vs_budget": ToolSpec(tools.cost_vs_budget, CostVsBudgetParams,
        "프로젝트별 예산 대비 실적 비용(인건비+비인건비)과 비용 초과율, 높은 순 정렬."),
    "check_missing_data": ToolSpec(tools.check_missing_data, CheckMissingParams,
        "확인이 필요한 누락 데이터(담당자·기한 빈칸 등) 목록과 업무 수. 정상인 구조적 공백은 제외."),
}


_DATASET_ATTRS = ("kind", "main", "employees", "projects", "table")
NOT_DATA = "분석할 데이터 형식이 아닙니다. 파일을 다시 불러와 주세요."


def _as_dataset(data: object) -> tuple[Optional[Dataset], Optional[str]]:
    """run_tool 입력을 Dataset으로 정규화한다. (Dataset, None) 또는 (None, 계산하지 않는 사유).

    - DataFrame → 일반 CSV Dataset
    - Dataset → 구조 버전(version)이 지금 엔진(DATASET_VERSION)과 같을 때만 사용
    - 그 밖 → 형식 오류

    클래스 비교(isinstance)로만 Dataset을 고르지 않는 이유: Streamlit 배포는 새 코드를 받으면 엔진 모듈을 다시 읽는데,
    st.cache_resource에 남은 데이터는 옛 Dataset 클래스로 만든 것이라 isinstance가 False가 된다.
    그러면 업무 데이터를 일반 CSV로 잘못 보고 "업무 파일이 있어야 한다"는 오류가 난다 (2026-11-23 배포에서 확인).
    그래서 모양(속성)으로 Dataset을 알아보되, 옛 코드로 만든 데이터를 무조건 쓰지 않도록 구조 버전을 확인한다.
    """
    if isinstance(data, pd.DataFrame):
        return Dataset.generic(data), None
    if not all(hasattr(data, a) for a in _DATASET_ATTRS):
        return None, NOT_DATA
    version = getattr(data, "version", None)
    if version != DATASET_VERSION:
        shown = "정보 없음" if version is None else str(version)
        return None, (f"앱이 업데이트되어, 이전에 불러온 데이터의 구조(버전 {shown})가 지금 엔진(버전 {DATASET_VERSION})과 "
                      "다릅니다. 잘못 계산하지 않도록 실행하지 않았습니다. 데이터를 다시 불러와 주세요.")
    if getattr(data, "kind", None) not in ("standard", "generic") or not isinstance(getattr(data, "main", None), pd.DataFrame):
        return None, NOT_DATA
    return data, None  # type: ignore[return-value]


def run_tool(data: Union[Dataset, pd.DataFrame], name: str, params: dict) -> ToolResult:
    """도구를 이름으로 실행한다. 어떤 경우에도 예외 대신 ToolResult를 돌려준다."""
    ds, problem = _as_dataset(data)
    if ds is None:
        return ToolResult(tool=name, params=params or {}, error=problem)
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(tool=name, params=params or {},
                          error=f"지원하지 않는 도구입니다: {name}. 사용 가능: {', '.join(TOOLS)}")
    try:
        parsed = spec.params.model_validate(params or {})
    except ValidationError as exc:
        fields = ", ".join(".".join(str(x) for x in e["loc"]) or "(전체)" for e in exc.errors())
        return ToolResult(tool=name, params=params or {}, error=f"도구 입력 형식이 잘못되었습니다: {fields}")
    try:
        return spec.func(ds, parsed)
    except Exception as exc:  # noqa: BLE001 - 도구 오류가 앱 전체를 멈추지 않게
        return ToolResult(tool=name, params=parsed.model_dump(), error=f"도구 실행 중 오류: {type(exc).__name__}")


def tool_schemas() -> list[dict]:
    """LLM function calling용 도구 설명 (이름·설명·입력 JSON 스키마). 표준 JSON Schema라 $defs·$ref가 있을 수 있다."""
    return [{"name": n, "description": s.description, "parameters": s.params.model_json_schema()}
            for n, s in TOOLS.items()]


# 타입이 없는(Any) 필드에 붙일 설명. Gemini Schema는 타입 없는 필드를 받지 않는다.
_UNTYPED_HINT = "비교할 값. 숫자·날짜(YYYY-MM-DD)도 문자열로 쓴다. op가 in이면 쉼표로 구분 (예: \"P01,P05\")"


def _flatten(node: object, defs: dict) -> object:
    """$ref를 펼치고, Optional(anyOf [X, null])은 X + nullable로, 타입 없는 필드는 문자열로 바꾼다."""
    if isinstance(node, list):
        return [_flatten(x, defs) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = dict(defs[node["$ref"].split("/")[-1]])
        extra = {k: v for k, v in node.items() if k != "$ref"}
        return _flatten({**target, **extra}, defs)
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b.get("type") != "null"]
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        if len(branches) == 1:
            merged = _flatten({**branches[0], **rest}, defs)
            if len(branches) < len(node["anyOf"]):
                merged["nullable"] = True
            return merged
        return {**_flatten(rest, defs), "anyOf": _flatten(branches, defs)}
    out = {k: _flatten(v, defs) for k, v in node.items() if k not in ("$defs", "const")}
    if "const" in node:  # 값이 하나뿐인 Literal은 const로 나오는데 Gemini Schema는 const를 받지 않는다
        out["enum"] = [node["const"]]
        out.setdefault("type", "string")
    if "properties" in node:
        out["properties"] = {k: _flatten(v, defs) for k, v in node["properties"].items()}
    if not any(k in out for k in ("type", "anyOf", "properties", "items", "enum")):
        out["type"] = "string"
        out["nullable"] = True
        out.setdefault("description", _UNTYPED_HINT)
    return out


def gemini_tool_declarations() -> list[dict]:
    """Gemini function calling용 도구 선언 (name·description·parameters).

    google-genai의 types.FunctionDeclaration(**d)나 types.Tool(function_declarations=[...])에 그대로 넣을 수 있다.
    $defs·$ref를 펼치고 Optional을 nullable로 바꿔 Gemini Schema 형식에 맞춘다. 검증은 그대로 run_tool이 한다.
    """
    out = []
    for name, spec in TOOLS.items():
        schema = spec.params.model_json_schema()
        out.append({"name": name, "description": spec.description,
                    "parameters": _flatten(schema, schema.get("$defs", {}))})
    return out
