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


# ---------------------------------------------------------------------------
# 추가 묶음: 바꿔 말한 질문(paraphrase), 답하면 안 되는 질문(refuse), 측정
# ---------------------------------------------------------------------------
import json as _json

LIMIT = ("note_limitation", {"reason": "이 데이터에는 해당 정보가 없어 답할 수 없습니다."})


def test_question_sets_load():
    assert len(ev.load_questions()) == 12  # 기본은 발표용 core만
    allq = ev.load_questions(ev.SETS)
    assert len(allq) == 12 + sum(1 for q in allq if q.set != "core")
    extra = [q for q in allq if q.set == "paraphrase"]
    assert extra and all(q.same_as in RIGHT for q in extra)


def test_extra_questions_file_has_no_answers():
    """추가 질문 파일에는 정답 값이 없다 (정답은 기존 정답 파일 그대로)."""
    data = _json.loads(ev.EXTRA_QUESTIONS.read_text(encoding="utf-8"))
    assert all(set(q) <= {"id", "set", "same_as", "dataset", "question"} for q in data["questions"])


def test_paraphrase_uses_original_answer(datasets):
    qs = [q for q in ev.load_questions(("paraphrase",)) if q.same_as in ("Q4", "S4")]
    right = ev.grade(ev.run_questions(qs, datasets, lambda q: FakeLLM(FakeLLM.calls(RIGHT[q.same_as]))))
    assert right and all(r["pass"] for r in right)
    wrong = ev.grade(ev.run_questions(qs[:1], datasets, lambda q: FakeLLM(FakeLLM.calls(
        ("aggregate", {"op": "count", "group_by": ["프로젝트ID"]})), FakeLLM.calls(
        ("aggregate", {"op": "count", "group_by": ["프로젝트ID"]})))))
    assert not wrong[0]["pass"]


def refuse_run(datasets, qid, *calls):
    q = [x for x in ev.load_questions(("refuse",)) if x.id == qid]
    return ev.grade(ev.run_questions(q, datasets, lambda _: FakeLLM(*[FakeLLM.calls(*c) for c in calls])))[0]


def test_refuse_passes_when_agent_declines(datasets):
    r = refuse_run(datasets, "N1", [LIMIT], [LIMIT])
    assert r["pass"] and r["calls"] == []


def test_refuse_fails_when_agent_runs_another_analysis(datasets):
    """없는 '상품'을 물었는데 전체 매출을 대신 계산해 결과를 내면 오답 (2026-11-23 실제 Gemini에서 나온 행동).

    에이전트 동작과 무관하게 채점 규칙만 확인한다 (에이전트가 막도록 고쳐져도 이 테스트는 그대로 유효).
    """
    from engine import run_tool
    shown = run_tool(datasets["sample"], "aggregate", {"op": "sum", "value_column": "sales"})
    run = ev.Run("N2", "상품별 매출은?", "ok", set="refuse", stage="exec",
                 calls=[{"tool": "aggregate", "params": shown.params}], results=[shown])
    r = ev.grade([run])[0]
    assert shown.ok and not r["pass"] and "다른 분석" in r["reason"]
    # 실행은 했지만 도구가 계산 불가 사유를 낸 경우(숫자 없음)는 정답
    failed = run_tool(datasets["sample"], "aggregate", {"op": "sum", "value_column": "상품"})
    ok_run = ev.Run("N2", "상품별 매출은?", "error", set="refuse", stage="exec",
                    calls=[{"tool": "aggregate", "params": failed.params}], results=[failed])
    assert not failed.ok and ev.grade([ok_run])[0]["pass"]


def test_refuse_question_is_blocked_before_llm(datasets):
    """데이터에 없는 지표(연봉)는 LLM 호출 전에 차단 → 거절형 정답."""
    class Down:
        model = "down"

        def generate(self, *a, **k):
            raise LLMError("server", "호출되면 안 됨")

    q = [x for x in ev.load_questions(("refuse",)) if x.id == "N3"]
    runs = ev.run_questions(q, datasets, lambda *a: Down())
    r = ev.grade(runs)[0]

    assert runs[0].llm_calls == 0
    assert r["status"] == "unsupported"
    assert r["pass"]


def test_refuse_api_error_is_not_pass():
    """거절형 질문에서 API 오류가 발생했다면 정답으로 채점하지 않는다."""
    run = ev.Run(
        "N3",
        "연봉이 가장 높은 직원은?",
        "error",
        "Gemini 서버 오류",
        set="refuse",
        stage="plan",
    )
    assert not ev.grade([run])[0]["pass"]


def test_llm_calls_are_counted(datasets):
    """첫 계획이 조건을 놓쳐 재계획하면 LLM 호출 2회로 센다."""
    q = [x for x in ev.load_questions(("paraphrase",)) if x.id == "Q1-p1"]
    one = ev.run_questions(q, datasets, lambda _: FakeLLM(FakeLLM.calls(RIGHT["Q1"])))
    assert one[0].llm_calls == 1
    q = [x for x in ev.load_questions() if x.id == "Q1"]
    two = ev.run_questions(q, datasets, lambda _: FakeLLM(
        FakeLLM.calls(("calculate_completion_rate", {})), FakeLLM.calls(RIGHT["Q1"])))
    assert two[0].llm_calls == 2


def test_summary_markdown(datasets):
    qs = ev.load_questions() + [x for x in ev.load_questions(("refuse",)) if x.id == "N1"]
    calls = {**RIGHT, "N1": LIMIT}
    report = ev.grade(ev.run_questions(qs, datasets, lambda q: FakeLLM(FakeLLM.calls(calls[q.id]), FakeLLM.calls(calls[q.id]))))
    md = ev.summary_md(report)
    assert "| core | 12/12 |" in md and "| refuse | 1/1 |" in md and "| N1 |" in md
