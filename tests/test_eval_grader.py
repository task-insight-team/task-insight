"""채점 스크립트(eval/run_eval.py) 자체가 맞게 채점하는지. 실제 Gemini 대신 FakeLLM으로 도구 선택을 정해 준다.

맞는 도구를 고르면 통과, 틀린 도구·틀린 조건이면 오답으로 처리되는지 확인한다.
"""

import pytest

from agent import FakeLLM, LLMError
from eval import run_eval as ev

AS_OF = ev.KAFE_AS_OF
MONTH = {"date_column": "month", "op": "sum", "value_column": "sales"}

# 사람이 고른 '맞는' 도구 호출 (에이전트가 이렇게 고르면 정답이어야 한다)
RIGHT = {
    "Q1": ("count_overdue_tasks", {"as_of": AS_OF}),
    "Q2": ("summarize_by_category", {"group_by": "부서", "as_of": AS_OF}),
    "Q3": ("mh_overrun", {"group_by": "프로젝트ID"}),
    "Q4": ("cost_vs_budget", {}),
    "Q5": ("calculate_completion_rate", {"group_by": "프로젝트ID"}),
    "Q6": ("check_missing_data", {}),
    "Q7": ("aggregate", {"op": "count", "group_by": ["담당자ID"], "sort": "value_desc"}),
    "S1": ("aggregate", {"op": "sum", "value_column": "sales"}),
    "S2": ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["region"]}),
    "S3": ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["month"]}),
    "S4": ("compare_periods", {**MONTH, "current": "2026-02", "group_by": "region"}),
    "S5": ("compare_periods", {**MONTH, "current": "2026-01"}),
}


def fake(calls: dict):
    return lambda q: FakeLLM(FakeLLM.calls(calls[q.id]))


@pytest.fixture(scope="module")
def datasets():
    return ev.load_datasets()


def run_and_grade(datasets, calls, only):
    questions = [q for q in ev.load_questions() if q.id in only]
    return {r["id"]: r for r in ev.grade(ev.run_questions(questions, datasets, fake(calls)))}


def test_right_tools_get_full_marks(datasets):
    report = run_and_grade(datasets, RIGHT, RIGHT)
    failed = {k: (r["status"], r["reason"]) for k, r in report.items() if not r["pass"]}
    assert not failed and len(report) == 12


@pytest.mark.parametrize("qid, call", [
    ("Q1", ("summarize_by_category", {"group_by": "상태", "as_of": AS_OF})),   # 기한 초과가 아니라 상태별 건수
    ("Q3", ("aggregate", {"table": "projects", "op": "sum", "value_column": "예산", "group_by": ["프로젝트ID"]})),
    ("Q4", ("calculate_completion_rate", {"group_by": "프로젝트ID"})),          # 다른 지표
    ("Q5", ("aggregate", {"op": "count", "group_by": ["프로젝트ID"]})),           # 완료율 아님
    ("Q7", ("aggregate", {"op": "count", "group_by": ["프로젝트ID"], "sort": "value_desc"})),  # 직원이 아님
    ("S2", ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["month"]})),
    ("S4", ("compare_periods", {**MONTH, "current": "2026-02"})),               # 감소 지역 비교 없음
])
def test_wrong_tools_fail(datasets, qid, call):
    assert not run_and_grade(datasets, {qid: call}, {qid})[qid]["pass"]


@pytest.mark.parametrize("qid, tool, params", [
    ("Q1", "aggregate", {"op": "count", "filters": [{"column": "상태", "op": "!=", "value": "완료"}]}),  # 미완료 전체 44
    ("Q2", "summarize_by_category", {"group_by": "프로젝트ID", "as_of": AS_OF}),
    ("Q3", "mh_overrun", {"group_by": "부서"}),
    ("Q4", "calculate_completion_rate", {"group_by": "프로젝트ID"}),  # P01이 맨 위여도 초과율 컬럼이 없음
    ("Q5", "aggregate", {"op": "count", "group_by": ["프로젝트ID"]}),
    ("Q6", "aggregate", {"op": "count", "filters": [{"column": "담당자ID", "op": "is_null"}]}),  # 담당자 빈칸만 4건
    ("Q7", "aggregate", {"op": "count", "group_by": ["부서"], "sort": "value_desc"}),
])
def test_grader_rejects_wrong_results(datasets, qid, tool, params):
    """에이전트 검증을 거치지 않고 엔진 결과를 바로 채점: 다른 계산이면 정답을 내지 않는다."""
    from engine import run_tool
    import json
    specs = ev.kafe_specs(json.loads(ev.KAFE_ANSWERS.read_text(encoding="utf-8")))
    result = run_tool(datasets["kafe"], tool, params)
    assert result.ok, result.error
    assert not all(ev.check([result], s)[0] for s in specs[qid])


@pytest.mark.parametrize("qid, tool, params", [
    ("Q1", "summarize_by_category", {"group_by": "상태", "as_of": AS_OF}),  # 상태별 기한 초과 3+9+3 = 15
    ("Q6", "check_missing_data", {"include_structural": True}),            # 구조적 공백은 따로, 확인 필요 7
])
def test_grader_accepts_other_correct_routes(datasets, qid, tool, params):
    """다른 방법이라도 정답을 내면 정답으로 친다."""
    from engine import run_tool
    import json
    specs = ev.kafe_specs(json.loads(ev.KAFE_ANSWERS.read_text(encoding="utf-8")))
    result = run_tool(datasets["kafe"], tool, params)
    assert all(ev.check([result], s)[0] for s in specs[qid])


def test_impossible_question_must_not_produce_numbers(datasets):
    """S5: 2025-12 데이터가 없으니 숫자를 내면 오답, 사유를 안내하면 정답."""
    wrong = {"S5": ("compare_periods", {**MONTH, "current": "2026-02"})}
    assert not run_and_grade(datasets, wrong, {"S5"})["S5"]["pass"]


def test_api_error_is_a_fail(datasets):
    class Down:
        model = "down"

        def generate(self, *a, **k):
            raise LLMError("server", "Gemini 서버 오류")

    questions = [q for q in ev.load_questions() if q.id == "Q1"]
    report = ev.grade(ev.run_questions(questions, datasets, lambda q: Down()))
    assert not report[0]["pass"] and report[0]["status"] == "error"


def test_answers_not_needed_while_agent_runs(datasets, monkeypatch, tmp_path):
    """1단계(에이전트 실행)는 정답 파일 없이 돌아간다 = 실행 중에는 정답을 읽지 않는다."""
    monkeypatch.setattr(ev, "KAFE_ANSWERS", tmp_path / "none.json")
    monkeypatch.setattr(ev, "SAMPLE_EXPECTED", tmp_path / "none2.json")
    questions = [q for q in ev.load_questions() if q.id in ("Q1", "S1")]
    runs = ev.run_questions(questions, datasets, fake(RIGHT))
    assert [r.status for r in runs] == ["ok", "ok"]
    with pytest.raises(FileNotFoundError):
        ev.grade(runs)
