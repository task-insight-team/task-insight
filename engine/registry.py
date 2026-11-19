"""도구 목록과 안전한 실행 진입점.

agent는 run_tool만 호출한다. LLM이 만든 params는 여기서 Pydantic으로 검증되고,
허용 목록(TOOLS)에 없는 도구나 형식이 틀린 입력은 실행되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

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
    SummarizeByCategoryParams,
    ToolResult,
)
from engine import tools
from engine.loader import Dataset


@dataclass(frozen=True)
class ToolSpec:
    func: Callable[[Dataset, BaseModel], ToolResult]
    params: type[BaseModel]
    description: str  # LLM에게 보여줄 설명


TOOLS: dict[str, ToolSpec] = {
    "aggregate": ToolSpec(tools.aggregate, AggregateParams,
        "합계(sum)·평균(mean)·개수(count). table(main/employees/projects), group_by, filters, sort, top_n. 일반 CSV에도 쓸 수 있다."),
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


def run_tool(data: Union[Dataset, pd.DataFrame], name: str, params: dict) -> ToolResult:
    """도구를 이름으로 실행한다. 어떤 경우에도 예외 대신 ToolResult를 돌려준다."""
    ds = data if isinstance(data, Dataset) else Dataset.generic(data)
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
    """LLM function calling용 도구 설명 (이름·설명·입력 JSON 스키마)."""
    return [{"name": n, "description": s.description, "parameters": s.params.model_json_schema()}
            for n, s in TOOLS.items()]
