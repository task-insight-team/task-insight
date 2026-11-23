"""엔진 직접 범위 인자(department·project_id, PR #15) 대응 회귀 테스트 (FakeLLM, 실제 API 미사용).

엔진에 해당 필드가 없으면(#15 머지 전) 모듈 전체를 건너뛴다 → 머지 순서와 무관하게 테스트가 깨지지 않는다.
기대값은 정답 파일이 아니라 엔진(run_tool) 직접 호출로 얻는다.
"""

from __future__ import annotations

import pytest

from engine import run_tool

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.guard import supports
from agent.prompts import SYSTEM_PROMPT, scope_note
from agent.tests.helpers import AS_OF, kafe_dataset

pytestmark = pytest.mark.skipif(not supports("count_overdue_tasks", "department"),
                                reason="엔진에 department·project_id 인자가 아직 없음 (PR #15 머지 전)")


@pytest.fixture(scope="module")
def ds():
    return kafe_dataset()


def run(ds, question, *calls):
    resp = create_plan(question, ds, AS_OF, FakeLLM(*[FakeLLM.calls(c) for c in calls]))
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan)) if resp.status == "plan" else None
    return resp, out


# ---------------------------------------------------------------------------
# 반드시 검증할 질문
# ---------------------------------------------------------------------------
def test_research_completion_rate_by_employee(ds):
    """연구팀 직원별 완료율: 이전에는 unsupported, 이제 department 인자로 계산."""
    resp, out = run(ds, "연구팀 직원별 완료율은?",
                    ("calculate_completion_rate", {"group_by": "담당자ID", "department": "연구"}))
    assert resp.status == "plan", resp.message
    assert out.status == "ok"
    res = out.steps[0].result
    assert res.model_dump() == run_tool(ds, "calculate_completion_rate", resp.plan.steps[0].params).model_dump()
    research = set(ds.employees.loc[ds.employees["department"] == "연구", "employee_id"])
    assert res.rows and {r["담당자ID"] for r in res.rows} <= research  # 연구 부서 직원만
    assert out.steps[0].explanation.startswith("[부서 연구]")


def test_quality_overdue_with_department_arg(ds):
    """품질팀 기한 초과: '품질팀'도 '품질'로 맞춰 계획에 저장, 결과는 aggregate 필터 방식과 같다."""
    resp, out = run(ds, "품질팀의 기한 초과 미완료 업무는?", ("count_overdue_tasks", {"department": "품질팀"}))
    assert resp.status == "plan", resp.message
    assert resp.plan.steps[0].params["department"] == "품질"
    agg = run_tool(ds, "aggregate", {"op": "count", "filters": [
        {"column": "부서", "op": "==", "value": "품질"}, {"column": "상태", "op": "!=", "value": "완료"},
        {"column": "기한", "op": "<", "value": AS_OF}]})
    assert out.steps[0].result.value == agg.value
    first = out.steps[0].explanation.splitlines()[0]
    assert first.startswith("[부서 품질]") and f"{int(agg.value)}건" in first


def test_project_completion_rate(ds):
    resp, out = run(ds, "P03 프로젝트의 완료율은?", ("calculate_completion_rate", {"project_id": "P03"}))
    assert resp.status == "plan", resp.message
    res = out.steps[0].result
    assert res.value == run_tool(ds, "calculate_completion_rate", {"project_id": "P03"}).value
    assert out.steps[0].explanation.startswith("[프로젝트 P03]")


def test_unknown_department_in_question_is_asked_back(ds):
    llm = FakeLLM()  # 질문에 '인사부'가 있으면 LLM 호출 전에 막는다
    resp = create_plan("존재하지 않는 인사부의 기한 초과 업무는?", ds, AS_OF, llm)
    assert resp.status == "clarify" and "인사" in resp.message and not llm.prompts


def test_unknown_department_direct_arg_is_rejected_before_approval(ds):
    """질문에 부서 단어가 없어도, LLM이 없는 부서를 department 인자로 넣으면 계획 단계에서 거부한다."""
    resp = create_plan("인사 쪽 기한 초과는?", ds, AS_OF, FakeLLM(FakeLLM.calls(("count_overdue_tasks", {"department": "인사"}))))
    assert resp.status == "clarify" and resp.plan is None
    assert "인사" in resp.message and "품질" in resp.message  # 가능한 값 안내


def test_unknown_project_direct_arg_is_rejected(ds):
    resp = create_plan("완료율 알려줘", ds, AS_OF, FakeLLM(FakeLLM.calls(("calculate_completion_rate", {"project_id": "P77"}))))
    assert resp.status == "clarify" and "P77" in resp.message


@pytest.mark.parametrize("question,call", [
    ("P01 프로젝트의 기한 초과 업무는?", ("count_overdue_tasks", {"project_id": "P01"})),      # 완료 프로젝트
    ("P01 프로젝트의 미완료 업무 수는?",
     ("aggregate", {"op": "count", "filters": [{"column": "프로젝트ID", "op": "==", "value": "P01"},
                                              {"column": "상태", "op": "!=", "value": "완료"}]})),
])
def test_valid_zero_is_a_normal_result(ds, question, call):
    """존재하는 대상인데 조건에 맞는 업무가 0건 → 오류가 아니라 정상 0건."""
    resp, out = run(ds, question, call)
    assert resp.status == "plan", resp.message
    assert out.status == "ok" and out.steps[0].result.ok and out.steps[0].result.value == 0
    assert "0건" in out.steps[0].explanation


def test_valid_zero_summary_says_zero(ds):
    # 영업 부서 + P05: 각각 있는 값이지만 조합은 0건 (엔진 PR #15 설명의 예)
    resp, out = run(ds, "영업팀의 P05 업무 현황은?",
                    ("summarize_by_category", {"group_by": "상태", "department": "영업", "project_id": "P05"}))
    assert resp.status == "plan", resp.message
    res = out.steps[0].result
    if res.rows:  # 데이터상 0건이 아니면 이 사례는 의미가 없으므로 결과 일치만 확인
        assert res.model_dump() == run_tool(ds, "summarize_by_category", resp.plan.steps[0].params).model_dump()
    else:
        assert res.ok and "0건" in out.steps[0].explanation


# ---------------------------------------------------------------------------
# 조건 대조 규칙
# ---------------------------------------------------------------------------
def test_direct_arg_counts_only_for_supporting_tools(ds):
    """mh_overrun은 department 인자가 없다 → 인자를 넣어도 무시되고, 부서 조건 미반영으로 거부."""
    wrong = FakeLLM.calls(("mh_overrun", {"department": "품질"}))
    resp = create_plan("품질팀 MH 초과는?", ds, AS_OF, FakeLLM(wrong, wrong))
    assert resp.status == "unsupported" and resp.plan is None


def test_company_wide_plan_still_rejected(ds):
    """부서를 물었는데 department 인자 없이 전사 기한 초과 → 여전히 거부 후 재계획."""
    llm = FakeLLM(FakeLLM.calls(("count_overdue_tasks", {})), FakeLLM.calls(("count_overdue_tasks", {"department": "품질"})))
    resp = create_plan("품질팀에서 마감 넘긴 업무는?", ds, AS_OF, llm)
    assert resp.status == "plan" and resp.plan.steps[0].params["department"] == "품질" and len(llm.prompts) == 2
    assert "count_overdue_tasks" in llm.prompts[1]["user"]  # 재계획 안내에 직접 인자 지원 도구가 나온다


def test_compound_with_direct_args(ds):
    calls = [("calculate_completion_rate", {"department": "제조"}), ("calculate_completion_rate", {"department": "품질"})]
    resp = create_plan("제조 부서와 품질 부서 완료율 비교", ds, AS_OF, FakeLLM(FakeLLM.calls(*calls)))
    assert resp.status == "plan" and len(resp.plan.steps) == 2


def test_prompt_lists_direct_scope_tools():
    note = scope_note()
    for t in ("count_overdue_tasks", "summarize_by_category", "calculate_completion_rate"):
        assert t in note
    assert "mh_overrun" in note and "받지 못한다" in note
    assert "calculate_completion_rate(group_by=담당자ID, department=연구)" in SYSTEM_PROMPT


def test_single_group_completion_is_not_called_lowest(ds):
    resp, out = run(ds, "P03 프로젝트의 완료율은?",
                    ("calculate_completion_rate", {"group_by": "프로젝트ID", "project_id": "P03"}))
    text = out.steps[0].explanation
    assert "P03의 완료율은" in text and "가장 낮은" not in text
