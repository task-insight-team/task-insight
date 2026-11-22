"""회귀 테스트: 질문에 명시된 조건(부서·프로젝트·직원·상태·기한 초과·기간)이 계획에 반영되지 않으면 승인 대상에서 제외한다.

배경: "품질팀에서 마감 넘긴 업무는?"에 전사 기한 초과 15건을 먼저 답하던 오류 (2026-11-22 실제 Gemini에서 발견).
"""

from __future__ import annotations

import pytest

from engine import run_tool

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.context import build_vocabulary
from agent.guard import extract_conditions
from agent.tests.helpers import AS_OF, REPRESENTATIVE, kafe_dataset


@pytest.fixture(scope="module")
def ds():
    return kafe_dataset()


def F(*items):
    return [{"column": c, "op": o, "value": v} for c, o, v in items]


QUALITY_OVERDUE = {"op": "count", "filters": F(("부서", "==", "품질"), ("상태", "!=", "완료"), ("기한", "<", AS_OF))}


# ---------------------------------------------------------------------------
# 조건 추출
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question,expected", [
    ("품질팀에서 마감 넘긴 업무는?", {("department", "품질"), ("overdue", "")}),
    ("P03 프로젝트 비용은?", {("project", "P03")}),
    ("E032 직원 업무 수", {("employee", "E032")}),
    ("진행중인 업무 몇 건?", {("status", "진행중")}),
    ("2026년 10월에 완료된 업무 수", {("month", "2026-10")}),
    ("10월 완료 업무", {("month", "2026-10")}),
    ("2026-11-19 기준 기한이 지난 업무", {("overdue", "")}),        # 날짜는 기간 조건이 아님
    ("11월 19일 기준 기한 초과 업무", {("overdue", "")}),
    ("완료율이 가장 낮은 프로젝트는?", {("metric", "calculate_completion_rate,summarize_by_category")}),  # 상태 조건 아님
    ("담당자나 기한이 누락된 업무는?", set()),                       # '기한 누락'은 기한 초과가 아님
    ("비용 초과율이 가장 큰 프로젝트는?", {("metric", "cost_vs_budget")}),
    ("계획 대비 MH 초과가 가장 큰 프로젝트는?", {("metric", "mh_overrun")}),
    ("P03 프로젝트 비용은?", {("project", "P03")}),
])
def test_extract_conditions(ds, question, expected):
    got = {(c.kind, c.value) for c in extract_conditions(question, build_vocabulary(ds, AS_OF))}
    assert got == expected


# ---------------------------------------------------------------------------
# 핵심 회귀: 품질팀 마감 초과
# ---------------------------------------------------------------------------
def test_quality_overdue_company_wide_plan_is_rejected_then_fixed_on_retry(ds):
    llm = FakeLLM(FakeLLM.calls(("count_overdue_tasks", {"group_by": "부서"})),   # 1차: 전사 결과 (조건 누락)
                  FakeLLM.calls(("aggregate", QUALITY_OVERDUE)))                  # 재계획: 품질 필터 반영
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF, llm)
    assert resp.status == "plan" and [s.tool for s in resp.plan.steps] == ["aggregate"]
    assert len(llm.prompts) == 2 and "부서 = 품질" in llm.prompts[1]["user"]
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    expected = run_tool(ds, "aggregate", resp.plan.steps[0].params).value
    assert out.steps[0].result.value == expected
    first_line = out.explanation.splitlines()[0]
    assert "부서 == 품질" in first_line and f"{int(expected)}건" in first_line
    assert "15건" not in first_line  # 전사 수치를 먼저 말하지 않는다


def test_quality_overdue_rejected_when_retry_still_ignores_condition(ds):
    wrong = FakeLLM.calls(("count_overdue_tasks", {"group_by": "부서"}))
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF, FakeLLM(wrong, wrong))
    assert resp.status == "unsupported" and resp.plan is None
    assert "부서 = 품질" in resp.message and "임의로 보여 드리지 않습니다" in resp.message


def test_retry_failure_does_not_fall_back_to_company_wide(ds):
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF,
                       FakeLLM(FakeLLM.calls(("count_overdue_tasks", {})), RuntimeError("retry down")))
    assert resp.status == "unsupported" and resp.plan is None


def test_overdue_needs_both_due_and_open_status(ds):
    only_dept = {"op": "count", "filters": F(("부서", "==", "품질"))}
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF,
                       FakeLLM(FakeLLM.calls(("aggregate", only_dept)), FakeLLM.calls(("aggregate", only_dept))))
    assert resp.status == "unsupported"


# ---------------------------------------------------------------------------
# 다른 조건 종류
# ---------------------------------------------------------------------------
def test_department_scoped_completion_rate_is_unsupported(ds):
    """완료율 도구는 부서 조건을 받지 못한다 → 전사 결과 대신 지원 불가."""
    wrong = FakeLLM.calls(("calculate_completion_rate", {"group_by": "담당자ID"}))
    resp = create_plan("연구팀 직원별 완료율은?", ds, AS_OF, FakeLLM(wrong, wrong))
    assert resp.status == "unsupported" and resp.plan is None and "부서 = 연구" in resp.message


@pytest.mark.parametrize("question,good,bad", [
    ("P03 프로젝트 비용은?", ("cost_vs_budget", {"project_id": "P03"}), ("cost_vs_budget", {})),
    ("E032 직원 업무 수", ("aggregate", {"op": "count", "filters": F(("담당자ID", "==", "E032"))}),
     ("aggregate", {"op": "count", "group_by": ["담당자ID"]})),
    ("진행중인 업무 몇 건?", ("aggregate", {"op": "count", "filters": F(("상태", "==", "진행중"))}),
     ("aggregate", {"op": "count"})),
    ("2026년 10월에 완료된 업무 수", ("aggregate", {"op": "count", "filters": F(("완료월", "==", "2026-10"))}),
     ("aggregate", {"op": "count", "filters": F(("상태", "==", "완료"))})),
    ("P05 진행중 업무", ("aggregate", {"op": "count", "filters": F(("프로젝트ID", "in", "P05"), ("상태", "==", "진행중"))}),
     ("aggregate", {"op": "count", "filters": F(("프로젝트ID", "==", "P05"))})),
])
def test_condition_kinds(ds, question, good, bad):
    ok = create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls(good)))
    assert ok.status == "plan", ok.message
    rejected = create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls(bad), FakeLLM.calls(bad)))
    assert rejected.status == "unsupported" and rejected.plan is None


def test_mixed_plan_keeps_only_covered_steps(ds):
    llm = FakeLLM(FakeLLM.calls(("aggregate", QUALITY_OVERDUE), ("count_overdue_tasks", {"group_by": "부서"})))
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF, llm)
    assert resp.status == "plan" and [s.tool for s in resp.plan.steps] == ["aggregate"]
    assert any("제외" in n for n in resp.notices)


def test_questions_without_conditions_do_not_retry(ds):
    for question, tool, args in REPRESENTATIVE:
        llm = FakeLLM(FakeLLM.calls((tool, args)))
        resp = create_plan(question, ds, AS_OF, llm)
        assert resp.status == "plan" and len(llm.prompts) == 1, question


def test_metric_swap_is_rejected(ds):
    """'연구팀 직원별 완료율' → 부서 필터를 건 '업무 수'로 바꿔 답하면 안 된다 (실제 Gemini에서 발견)."""
    swapped = FakeLLM.calls(("aggregate", {"op": "count", "group_by": ["담당자ID"], "filters": F(("부서", "==", "연구"))}))
    resp = create_plan("연구팀 직원별 완료율은?", ds, AS_OF, FakeLLM(swapped, swapped))
    assert resp.status == "unsupported" and resp.plan is None and "완료율" in resp.message


def test_retry_prompt_suggests_exact_filters(ds):
    llm = FakeLLM(FakeLLM.calls(("count_overdue_tasks", {"group_by": "부서"})), FakeLLM.calls(("aggregate", QUALITY_OVERDUE)))
    create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF, llm)
    hint = llm.prompts[1]["user"]
    assert '"value": "품질"' in hint and '"op": "!="' in hint and AS_OF in hint
