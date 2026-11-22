"""승인된 계획만 실행한다.

approve_plan(plan)  → 사용자가 '승인' 버튼을 눌렀을 때 호출. 계획의 지문(PlanApproval)을 만든다.
execute_plan(ds, plan, approval) → 지문이 현재 계획과 같을 때만 engine.run_tool로 단계별 실행.
계획이 승인 후 바뀌었으면(인자 하나라도) 실행하지 않고 다시 승인을 요구한다.
"""

from __future__ import annotations

import time
from typing import Optional

from contracts import AnalysisPlan
from engine import run_tool
from engine.loader import Dataset

from agent.explain import explain_result, notices_from_summary
from agent.guard import plan_fingerprint, recheck_plan
from agent.schemas import ExecutionResponse, LogEntry, PlanApproval, StepResult

REAPPROVE = "승인한 계획과 실행하려는 계획이 다릅니다. 계획을 다시 확인하고 승인해 주세요."


def approve_plan(plan: AnalysisPlan) -> PlanApproval:
    """사용자의 명시적 승인. 화면의 '승인' 버튼에서만 호출한다."""
    return PlanApproval(plan_hash=plan_fingerprint(plan))


def execute_plan(ds: Optional[Dataset], plan: Optional[AnalysisPlan], approval: Optional[PlanApproval]) -> ExecutionResponse:
    question = plan.question if plan else ""
    if plan is None:
        return ExecutionResponse(status="error", message="실행할 계획이 없습니다.")
    if approval is None:
        return ExecutionResponse(status="error", question=question, message="계획을 승인한 뒤에 실행할 수 있습니다.")
    if approval.plan_hash != plan_fingerprint(plan):
        return ExecutionResponse(status="error", question=question, message=REAPPROVE)
    if ds is None:
        return ExecutionResponse(status="error", question=question, message="분석할 데이터를 먼저 불러와 주세요.")
    problem = recheck_plan(plan)
    if problem:
        return ExecutionResponse(status="error", question=question, message=problem)

    steps, log = [], []
    for step in plan.steps:
        t0 = time.perf_counter()
        result = run_tool(ds, step.tool, dict(step.params))  # 엔진이 다시 검증하고 항상 ToolResult를 돌려줌
        elapsed = int((time.perf_counter() - t0) * 1000)
        steps.append(StepResult(step_id=step.id, tool=step.tool, purpose=step.purpose, chart_type=step.chart_type,
                                result=result, explanation=explain_result(step, result)))
        log.append(LogEntry(step_id=step.id, tool=step.tool, params=step.params, ok=result.ok, error=result.error,
                            used_rows=result.used_rows, elapsed_ms=elapsed))

    n_ok = sum(1 for s in steps if s.result.ok)
    status = "ok" if n_ok == len(steps) else ("partial" if n_ok else "error")
    parts = [s.explanation for s in steps]
    parts += [f"※ {n}" for n in notices_from_summary(plan.summary)]
    message = {"ok": "분석을 완료했습니다.", "partial": "일부 단계는 계산하지 못했습니다. 단계별 사유를 확인해 주세요.",
               "error": "모든 단계를 계산하지 못했습니다. 단계별 사유를 확인해 주세요."}[status]
    return ExecutionResponse(status=status, question=question, message=message, steps=steps,
                             explanation="\n\n".join(parts), log=log)
