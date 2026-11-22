"""AI 에이전트 오프라인 테스트 (실제 Gemini 호출 없음, FakeLLM 사용)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from contracts import AnalysisPlan, PlanStep
from engine import TOOLS, run_tool
from engine.registry import ToolSpec

from agent import FakeLLM, LLMError, LLMReply, approve_plan, create_plan, execute_plan
from agent.context import build_vocabulary, render_context
from agent.tests.helpers import AS_OF, REPRESENTATIVE, kafe_dataset

AGENT_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ds():
    return kafe_dataset()


@pytest.fixture
def no_execution(monkeypatch):
    """계획 단계에서 엔진 도구가 하나라도 실행되면 실패하게 만든다."""
    def boom(*_a, **_k):
        raise AssertionError("계획 단계에서 도구가 실행되었습니다")
    for name, spec in list(TOOLS.items()):
        monkeypatch.setitem(TOOLS, name, ToolSpec(boom, spec.params, spec.description))


def plan_for(ds, tool, args, question="질문"):
    return create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls((tool, args))))


# ---------------------------------------------------------------------------
# 대표 질문 7개: 계획 → 승인 → 실행, 결과는 엔진 직접 호출과 같아야 한다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question,tool,args", REPRESENTATIVE, ids=[r[1] for r in REPRESENTATIVE])
def test_plan_does_not_execute(ds, no_execution, question, tool, args):
    resp = plan_for(ds, tool, args, question)
    assert resp.status == "plan", resp.message
    assert [s.tool for s in resp.plan.steps] == [tool]
    assert resp.plan.question == question


@pytest.mark.parametrize("question,tool,args", REPRESENTATIVE, ids=[r[1] for r in REPRESENTATIVE])
def test_approved_plan_returns_engine_result(ds, question, tool, args):
    resp = plan_for(ds, tool, args, question)
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok", out.message
    step = out.steps[0]
    direct = run_tool(ds, tool, resp.plan.steps[0].params)
    assert step.result.model_dump() == direct.model_dump()  # ToolResult 그대로
    assert out.log[0].ok and out.log[0].params == resp.plan.steps[0].params


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


@pytest.mark.parametrize("question,tool,args", REPRESENTATIVE, ids=[r[1] for r in REPRESENTATIVE])
def test_explanation_numbers_come_from_result(ds, question, tool, args):
    """설명문의 숫자는 모두 ToolResult(value·rows·params·basis)에 있는 값이어야 한다."""
    resp = plan_for(ds, tool, args, question)
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    res = out.steps[0].result
    source = f"{res.value} {res.rows} {res.params} {res.basis}"
    allowed = _numbers(source) | {str(int(float(n))) for n in _numbers(source) if float(n).is_integer()}
    allowed |= {str(len(res.rows))}
    stray = {n for n in _numbers(out.steps[0].explanation) if n not in allowed and n.rstrip("0").rstrip(".") not in allowed}
    assert not stray, f"결과에 없는 숫자: {stray}\n{out.steps[0].explanation}"


def test_explanations_name_the_leader(ds):
    """1위 범주가 설명에 그대로 들어간다 (엔진 rows[0] 기준, 정답 파일 미사용)."""
    for question, tool, args in REPRESENTATIVE[2:5] + REPRESENTATIVE[6:]:
        resp = plan_for(ds, tool, args, question)
        out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
        first = out.steps[0].result.rows[0]
        assert str(next(iter(first.values()))) in out.steps[0].explanation


# ---------------------------------------------------------------------------
# 계획 검증
# ---------------------------------------------------------------------------
def test_unknown_tool_is_rejected(ds):
    resp = plan_for(ds, "run_python", {"code": "import os"})
    assert resp.status == "clarify" and resp.plan is None
    assert "지원하지 않는" in resp.message


def test_invalid_params_rejected(ds):
    resp = plan_for(ds, "aggregate", {"op": "median", "value_column": "실적MH"})
    assert resp.status == "clarify" and resp.plan is None
    assert "형식" in resp.message


def test_unknown_department_is_reported_not_zero(ds):
    resp = plan_for(ds, "aggregate", {"op": "count", "filters": [{"column": "부서", "op": "==", "value": "인사"}]})
    assert resp.status == "clarify"
    assert "인사" in resp.message and "품질" in resp.message  # 가능한 값 안내


def test_unknown_project_is_reported(ds):
    resp = plan_for(ds, "cost_vs_budget", {"project_id": "P99"})
    assert resp.status == "clarify" and "P99" in resp.message


def test_unknown_column_rejected(ds):
    resp = plan_for(ds, "summarize_by_category", {"group_by": "연봉"})
    assert resp.status == "clarify" and "연봉" in resp.message


def test_free_text_column_rejected(ds):
    resp = plan_for(ds, "mh_overrun", {"group_by": "업무명"})
    assert resp.status == "clarify" and "자유 텍스트" in resp.message


def test_valid_filters_pass(ds):
    args = {"op": "count", "filters": [{"column": "부서", "op": "==", "value": "품질"},
                                       {"column": "상태", "op": "!=", "value": "완료"},
                                       {"column": "기한", "op": "<", "value": AS_OF}]}
    resp = plan_for(ds, "aggregate", args)
    assert resp.status == "plan"
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok" and out.steps[0].result.value == run_tool(ds, "aggregate", resp.plan.steps[0].params).value


def test_in_filter_values_checked(ds):
    ok = plan_for(ds, "aggregate", {"op": "count", "filters": [{"column": "프로젝트ID", "op": "in", "value": "P01,P05"}]})
    bad = plan_for(ds, "aggregate", {"op": "count", "filters": [{"column": "프로젝트ID", "op": "in", "value": "P01,P77"}]})
    assert ok.status == "plan" and bad.status == "clarify" and "P77" in bad.message


def test_as_of_is_forced_to_external_value(ds):
    resp = plan_for(ds, "count_overdue_tasks", {"as_of": "2030-01-01"})
    assert resp.plan.steps[0].params["as_of"] == AS_OF
    assert any("기준일" in n for n in resp.notices)


def test_as_of_injected_when_missing(ds):
    resp = plan_for(ds, "count_overdue_tasks", {"group_by": "부서"})
    assert resp.status == "plan" and resp.plan.steps[0].params["as_of"] == AS_OF


def test_nullable_none_uses_default(ds):
    resp = plan_for(ds, "mh_overrun", {"group_by": None})
    assert resp.status == "plan" and resp.plan.steps[0].params["group_by"] == "프로젝트ID"


def test_max_four_steps_and_dedup(ds):
    calls = [("check_missing_data", {}), ("check_missing_data", {}), ("cost_vs_budget", {}),
             ("mh_overrun", {}), ("calculate_completion_rate", {"group_by": "부서"}),
             ("find_recurring_issues", {})]
    resp = create_plan("여러 가지", ds, AS_OF, FakeLLM(FakeLLM.calls(*calls)))
    assert resp.status == "plan" and len(resp.plan.steps) == 4
    assert [s.id for s in resp.plan.steps] == ["s1", "s2", "s3", "s4"]
    assert any("최대 4" in r for r in resp.rejected)


def test_partial_rejection_keeps_valid_steps_and_tells_user(ds):
    calls = [("cost_vs_budget", {}), ("aggregate", {"op": "count", "filters": [{"column": "부서", "op": "==", "value": "인사"}]})]
    resp = create_plan("q", ds, AS_OF, FakeLLM(FakeLLM.calls(*calls)))
    assert resp.status == "plan" and len(resp.plan.steps) == 1
    assert any("인사" in n for n in resp.notices) and "인사" in resp.plan.summary


# ---------------------------------------------------------------------------
# 지원 불가 · 되묻기
# ---------------------------------------------------------------------------
def test_clarification(ds):
    resp = create_plan("상황 어때?", ds, AS_OF, FakeLLM(FakeLLM.calls(("ask_clarification", {"question": "어느 부서를 볼까요?"}))))
    assert resp.status == "clarify" and resp.message == "어느 부서를 볼까요?" and resp.plan is None


def test_limitation_only_is_unsupported(ds):
    reply = FakeLLM.calls(("note_limitation", {"reason": "인사평가는 데이터로 판단할 수 없습니다."}))
    resp = create_plan("어떤 직원의 인사평가가 가장 좋은가?", ds, AS_OF, FakeLLM(reply))
    assert resp.status == "unsupported" and resp.plan is None
    assert "인사평가" in resp.message


def test_limitation_with_related_stats(ds):
    reply = FakeLLM.calls(("note_limitation", {"reason": "미래 예산은 예측할 수 없습니다. 현재 예산 대비 비용을 보여 드립니다."}),
                          ("cost_vs_budget", {}))
    resp = create_plan("앞으로 예산이 얼마나 필요할 것인가?", ds, AS_OF, FakeLLM(reply))
    assert resp.status == "plan" and resp.notices
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert "예측할 수 없습니다" in out.explanation


def test_llm_text_with_numbers_is_not_shown(ds):
    reply = FakeLLM.calls(("note_limitation", {"reason": "예산이 3억 원 더 필요합니다."}))
    resp = create_plan("앞으로 예산?", ds, AS_OF, FakeLLM(reply))
    assert "3억" not in resp.message


def test_no_calls_is_error(ds):
    resp = create_plan("q", ds, AS_OF, FakeLLM(LLMReply(calls=[], text="P05가 370MH 초과입니다")))
    assert resp.status == "error" and "370" not in resp.message


# ---------------------------------------------------------------------------
# 오류 처리
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["quota", "auth", "timeout", "server", "bad_response", "model"])
def test_llm_errors(ds, kind):
    resp = create_plan("q", ds, AS_OF, FakeLLM(LLMError(kind)))
    assert resp.status == "error" and resp.plan is None and resp.message == LLMError.MESSAGES[kind]


def test_unexpected_exception(ds):
    resp = create_plan("q", ds, AS_OF, FakeLLM(RuntimeError("boom")))
    assert resp.status == "error" and resp.plan is None


def test_missing_api_key(ds, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("agent.llm._secret_key", lambda: None)
    resp = create_plan("기한 초과 업무는?", ds, AS_OF)  # llm 미지정 → GeminiClient
    assert resp.status == "error" and "GEMINI_API_KEY" in resp.message


@pytest.mark.parametrize("question,as_of,data", [("", AS_OF, True), ("q" * 501, AS_OF, True),
                                                  ("q", "2026/11/19", True), ("q", "2026-13-45", True), ("q", AS_OF, False)])
def test_bad_inputs(ds, question, as_of, data):
    llm = FakeLLM()  # 호출되면 '응답 없음' 오류 → 입력 검증이 먼저 막아야 한다
    resp = create_plan(question, ds if data else None, as_of, llm)
    assert resp.status == "error" and not llm.prompts


# ---------------------------------------------------------------------------
# 승인 · 실행
# ---------------------------------------------------------------------------
def test_execute_requires_approval(ds, monkeypatch):
    resp = plan_for(ds, "check_missing_data", {})
    called = []
    monkeypatch.setattr("agent.executor.run_tool", lambda *a, **k: called.append(1))
    out = execute_plan(ds, resp.plan, None)
    assert out.status == "error" and not called and not out.steps


def test_changed_plan_needs_reapproval(ds, monkeypatch):
    resp = plan_for(ds, "cost_vs_budget", {})
    approval = approve_plan(resp.plan)
    changed = resp.plan.model_copy(deep=True)
    changed.steps[0].params["project_id"] = "P03"
    called = []
    monkeypatch.setattr("agent.executor.run_tool", lambda *a, **k: called.append(1))
    out = execute_plan(ds, changed, approval)
    assert out.status == "error" and "다시" in out.message and not called


def test_approval_of_other_plan_rejected(ds):
    a = plan_for(ds, "cost_vs_budget", {}).plan
    b = plan_for(ds, "mh_overrun", {}).plan
    assert execute_plan(ds, b, approve_plan(a)).status == "error"


def test_multi_step_independent_plan(ds):
    calls = [("count_overdue_tasks", {"group_by": "부서"}), ("cost_vs_budget", {}), ("check_missing_data", {})]
    resp = create_plan("전체 현황", ds, AS_OF, FakeLLM(FakeLLM.calls(*calls)))
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok" and [s.step_id for s in out.steps] == ["s1", "s2", "s3"]
    assert len(out.log) == 3 and all(e.ok for e in out.log)


def test_step_error_is_reported_without_numbers(ds):
    plan = AnalysisPlan(question="q", steps=[
        PlanStep(id="s1", tool="cost_vs_budget", params={"project_id": "P99"}),
        PlanStep(id="s2", tool="check_missing_data", params={"include_structural": False})])
    out = execute_plan(ds, plan, approve_plan(plan))
    assert out.status == "partial"
    assert out.steps[0].result.error and "계산할 수 없습니다" in out.steps[0].explanation
    assert out.log[0].ok is False and out.log[1].ok is True


def test_tampered_plan_with_unknown_tool_not_run(ds):
    plan = AnalysisPlan(question="q", steps=[PlanStep(id="s1", tool="drop_table", params={})])
    out = execute_plan(ds, plan, approve_plan(plan))
    assert out.status == "error" and not out.steps


# ---------------------------------------------------------------------------
# 프롬프트·정답 보호
# ---------------------------------------------------------------------------
def test_prompt_has_no_free_text_and_has_vocab(ds):
    llm = FakeLLM(FakeLLM.calls(("check_missing_data", {})))
    create_plan("누락 데이터는?", ds, AS_OF, llm)
    prompt = llm.prompts[0]["system"] + llm.prompts[0]["user"]
    for name in ds.main["task_name"].dropna().unique():
        assert name not in prompt
    for name in ds.projects["project_name"].dropna().unique():
        assert name not in prompt
    assert AS_OF in prompt and "품질" in prompt and "P10" in prompt and "E050" in prompt
    assert "ask_clarification" in llm.prompts[0]["tools"] and set(TOOLS) <= set(llm.prompts[0]["tools"])


def test_context_has_no_numeric_values(ds):
    text = render_context(ds, build_vocabulary(ds, AS_OF))
    assert "88000000" not in text and "budget" in text  # 예산 컬럼 이름은 있고 값은 없음


def test_agent_code_never_reads_answers():
    for path in AGENT_DIR.rglob("*.py"):
        if "tests" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        for banned in ("fixtures", "evaluation_answers", "SCENARIOS", "answer_key"):
            assert banned not in src, f"{path.name}에 {banned}"
        assert "date.today" not in src and "datetime.now" not in src and "Timestamp.now" not in src, path.name


def test_small_top_n_is_widened_to_show_ties(ds):
    resp = plan_for(ds, "aggregate", {"op": "count", "group_by": ["담당자ID"], "sort": "value_desc", "top_n": 1})
    assert resp.plan.steps[0].params["top_n"] == 5


@pytest.mark.parametrize("question,word", [("인사부 미완료 업무는?", "인사"), ("생산팀 업무 현황 알려줘", "생산"),
                                           ("P77 프로젝트 비용은?", "P77"), ("E999 직원 업무 수는?", "E999")])
def test_unknown_target_in_question_is_asked_back_before_llm(ds, question, word):
    llm = FakeLLM()  # 호출되면 실패
    resp = create_plan(question, ds, AS_OF, llm)
    assert resp.status == "clarify" and word in resp.message and not llm.prompts


@pytest.mark.parametrize("question", ["품질팀 미완료 업무는?", "경영지원부 업무 현황", "P03 프로젝트 비용은?",
                                      "E032 직원 업무는?", "전부 보여줘", "일부 프로젝트의 대부분 업무", "내부 업무 현황"])
def test_known_targets_and_common_words_pass(ds, question):
    resp = create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls(("check_missing_data", {}))))
    assert resp.status == "plan", resp.message


def test_department_not_applied_is_disclosed(ds):
    resp = create_plan("연구팀 직원별 완료율은?", ds, AS_OF,
                       FakeLLM(FakeLLM.calls(("calculate_completion_rate", {"group_by": "담당자ID"}))))
    assert resp.status == "plan" and any("연구" in n and "좁히지 않은" in n for n in resp.notices)


def test_department_filter_applied_has_no_warning(ds):
    args = {"op": "count", "filters": [{"column": "부서", "op": "==", "value": "제조"}, {"column": "상태", "op": "!=", "value": "완료"}]}
    resp = create_plan("제조 부서 미완료 업무 몇 건?", ds, AS_OF, FakeLLM(FakeLLM.calls(("aggregate", args))))
    assert resp.status == "plan" and not resp.notices
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert "부서 == 제조" in out.explanation and f"{num_of(out)}건" in out.explanation


def num_of(out):
    return int(out.steps[0].result.value)


def test_department_grouping_points_to_row(ds):
    resp = create_plan("품질팀 마감 넘긴 일 몇 개야?", ds, AS_OF,
                       FakeLLM(FakeLLM.calls(("count_overdue_tasks", {"group_by": "부서"}))))
    assert any("'품질' 행" in n for n in resp.notices)
