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


# ---------------------------------------------------------------------------
# 시연 질문 묶음 (demo)
# ---------------------------------------------------------------------------
def test_demo_d4_answer_is_derived_from_answer_key_and_raw_csv():
    """D4(품질팀 기한 초과 8건)는 엔진이 아니라 정답 파일 Q1의 업무 목록 × 원본 직원 CSV로 다시 계산해 확인한다."""
    import pandas as pd
    answers = {a["id"]: a for a in _json.loads(ev.KAFE_ANSWERS.read_text(encoding="utf-8"))["answers"]}
    tasks = pd.read_csv(ev.KAFE_DIR / "tasks.csv", dtype=str)
    emps = pd.read_csv(ev.KAFE_DIR / "employees.csv", dtype=str)
    dept = tasks.set_index("task_id")["assignee_id"].map(emps.set_index("employee_id")["department"])
    quality = sum(dept.get(t) == "품질" for t in answers["Q1"]["answer_ids"])
    expected = _json.loads(ev.DEMO_EXPECTED.read_text(encoding="utf-8"))["answers"][0]
    assert expected["id"] == "D4" and expected["checks"][0]["equals"] == quality == 8


DEMO_RIGHT = {"D1": RIGHT["Q2"], "D2": RIGHT["Q3"], "D3": RIGHT["Q4"], "D5": RIGHT["Q6"],
              "D4": ("count_overdue_tasks", {"as_of": AS_OF, "department": "품질"})}


def test_demo_right_tools_pass(datasets):
    qs = ev.load_questions(("demo",))
    assert [q.id for q in qs] == ["D1", "D2", "D3", "D4", "D5"]
    report = ev.grade(ev.run_questions(qs, datasets, lambda q: FakeLLM(FakeLLM.calls(DEMO_RIGHT[q.id]))))
    assert all(r["pass"] for r in report), [(r["id"], r["reason"]) for r in report if not r["pass"]]


def test_demo_company_wide_answer_fails(datasets):
    """품질팀을 물었는데 전사 기한 초과(15건)로 답하면 오답 (에이전트 동작과 무관하게 채점 규칙 확인)."""
    from engine import run_tool
    whole = run_tool(datasets["kafe"], "count_overdue_tasks", {"as_of": AS_OF})
    run = ev.Run("D4", "품질팀 기한 초과", "ok", set="demo", stage="exec",
                 calls=[{"tool": whole.tool, "params": whole.params}], results=[whole])
    assert whole.value == 15 and not ev.grade([run])[0]["pass"]


# ---------------------------------------------------------------------------
# 범용성 측정 묶음 (general): 다른 업종 데이터 3종
# ---------------------------------------------------------------------------
GEN_RIGHT = {
    "G01": [("aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["region"]})],
    "G02": [("aggregate", {"op": "sum", "value_column": "units"})],
    "G03": [("aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["month"], "sort": "value_desc"})],
    "G04": [("aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["month"], "sort": "value_asc"})],
    "G05": [("compare_periods", {"date_column": "month", "op": "sum", "value_column": "revenue", "current": "2026-12",
                                 "group_by": "region"})],
    "G06": [("aggregate", {"op": "mean", "value_column": "revenue",
                           "filters": [{"column": "region", "op": "==", "value": "서울"}]})],
    "G07": [("aggregate", {"op": "sum", "value_column": "revenue", "filters": [
                {"column": "region", "op": "==", "value": "부산"}, {"column": "month", "op": "<=", "value": "2026-06"}]}),
            ("aggregate", {"op": "sum", "value_column": "revenue", "filters": [
                {"column": "region", "op": "==", "value": "부산"}, {"column": "month", "op": ">=", "value": "2026-07"}]})],
    "G08": [("compare_periods", {"date_column": "month", "op": "sum", "value_column": "revenue", "current": "2026-12",
                                 "previous": "2026-01", "group_by": "region"})],
    "G11": [("aggregate", {"op": "sum", "value_column": "수량", "group_by": ["창고"]})],
    "G12": [("aggregate", {"op": "mean", "value_column": "단가", "group_by": ["분류"]})],
    "G13": [("aggregate", {"op": "sum", "value_column": "수량",
                           "filters": [{"column": "분류", "op": "==", "value": "원자재"}]})],
    "G17": [("aggregate", {"op": "count", "group_by": ["유형"]})],
    "G18": [("aggregate", {"op": "mean", "value_column": "처리시간", "group_by": ["담당팀"]})],
    "G19": [("compare_periods", {"date_column": "접수일", "op": "count", "current": "2026-11"})],
}


def gen_run(datasets, calls_by_id, ids):
    qs = [q for q in ev.load_questions(("general",)) if q.id in ids]
    runs = ev.run_questions(qs, datasets, lambda q: FakeLLM(FakeLLM.calls(*calls_by_id[q.id]),
                                                           FakeLLM.calls(*calls_by_id[q.id])))
    return {r["id"]: r for r in ev.grade(runs)}


def test_general_not_in_all():
    """--set all은 발표 전 확인용이라 호출 수가 늘지 않게 general을 넣지 않는다."""
    assert "general" not in ev.SETS and "general" in ev.EXTRA_SETS
    qs = ev.load_questions(("general",))
    assert len(qs) == 21 and {q.dataset for q in qs} == {"sales2026", "inventory", "support"}


def test_general_expected_matches_raw_csv():
    """정답 값을 엔진이 아니라 원본 CSV(pandas)로 다시 계산해 확인. sales2026은 과제 문서 정답과도 일치."""
    import pandas as pd
    exp = {a["id"]: a for a in _json.loads(ev.GENERAL_EXPECTED.read_text(encoding="utf-8"))["answers"]}
    s = pd.read_csv(ev.GENERAL_DIR / "sales_2026.csv")
    assert s.revenue.sum() == 80910 and s.units.sum() == 733  # 과제 문서: 총 매출 80,910 / 총 주문 733
    assert exp["G01"]["checks"][0]["equals"] == s.groupby("region").revenue.sum().to_dict()
    month = s.groupby("month").revenue.sum()
    assert (month.idxmax(), month.max(), month.idxmin(), month.min()) == ("2026-12", 7860, "2026-03", 5950)
    busan = s[s.region == "부산"]
    assert busan[busan.month <= "2026-06"].revenue.sum() == 12950 and busan[busan.month >= "2026-07"].revenue.sum() == 9030
    assert round(s[s.region == "서울"].revenue.mean(), 2) == exp["G06"]["checks"][0]["equals"]
    inv = pd.read_csv(ev.GENERAL_DIR / "inventory.csv")
    assert exp["G11"]["checks"][0]["equals"] == inv.groupby("창고").수량.sum().to_dict()
    assert exp["G12"]["checks"][0]["equals"] == inv.groupby("분류").단가.mean().round(2).to_dict()
    assert exp["G14"]["checks"][0]["equals"] == int((inv.수량 < inv.안전재고).sum())
    assert exp["G15"]["checks"][0]["id"] == (inv.수량 * inv.단가).groupby(inv.창고).sum().idxmax()
    sup = pd.read_csv(ev.GENERAL_DIR / "support_tickets.csv")
    assert exp["G17"]["checks"][0]["equals"] == sup.유형.value_counts().to_dict()
    assert exp["G18"]["checks"][0]["equals"] == sup.groupby("담당팀").처리시간.mean().round(2).to_dict()
    rate = (sup.처리상태 == "처리완료").groupby(sup.담당팀).mean()
    assert exp["G20"]["checks"][0]["id"] == rate.idxmin()


def engine_runs(datasets, calls_by_id):
    """에이전트를 거치지 않고 엔진으로 직접 계산한 결과를 채점용 Run으로 만든다 (엔진이 답할 수 있는지 확인용)."""
    from engine import run_tool
    qs = {q.id: q for q in ev.load_questions(("general",))}
    runs = []
    for qid, calls in calls_by_id.items():
        results = [run_tool(datasets[qs[qid].dataset], name, params) for name, params in calls]
        runs.append(ev.Run(qid, qs[qid].text, "ok", set="general", stage="exec",
                           calls=[{"tool": n, "params": p} for n, p in calls], results=results))
    return {r["id"]: r for r in ev.grade(runs)}


def test_general_engine_can_answer(datasets):
    """answer 질문과 일부 limit 질문(G07 두 단계, G08 비교 달 지정)은 맞는 도구를 고르면 엔진이 정답을 낸다.

    에이전트가 실제로 맞는 도구를 고르는지는 실제 Gemini 측정(--set general)으로 따로 본다.
    """
    report = engine_runs(datasets, GEN_RIGHT)
    wrong = {k: r["reason"] for k, r in report.items() if not r["pass"]}
    assert not wrong and all(r["outcome"] == "정답" for r in report.values())


@pytest.mark.parametrize("qid", ["G09", "G14", "G15", "G16", "G20"])
def test_general_limit_honest_refusal_passes(datasets, qid):
    r = gen_run(datasets, {qid: [LIMIT]}, {qid})[qid]
    assert r["pass"] and r["outcome"] == "정직한 거절"


@pytest.mark.parametrize("qid, call", [
    ("G16", ("aggregate", {"op": "sum", "value_column": "수량", "group_by": ["입고일"]})),  # 월별 대신 날짜별
    ("G09", ("aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["region"]})),  # 비중 대신 합계
])
def test_general_limit_wrong_numbers_fail(datasets, qid, call):
    """한계 질문에 틀린 숫자(월별 대신 날짜별, 비중 대신 합계)를 내면 오답 (에이전트 동작과 무관하게 채점 규칙 확인)."""
    r = engine_runs(datasets, {qid: [call]})[qid]
    assert not r["pass"] and r["outcome"] == "오답"


def test_general_refuse(datasets):
    ok = gen_run(datasets, {"G10": [LIMIT]}, {"G10"})["G10"]
    assert ok["pass"] and ok["outcome"] == "정직한 거절"
    sub = ev.Run("G10", "고객 만족도가 가장 낮은 지역은?", "ok", set="general", stage="exec",
                 calls=[{"tool": "aggregate", "params": {}}],
                 results=[run_tool_sales(datasets)])
    r = ev.grade([sub])[0]
    assert not r["pass"] and r["outcome"] == "대체 분석"


def run_tool_sales(datasets):
    from engine import run_tool
    return run_tool(datasets["sales2026"], "aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["region"]})


def test_general_summary_has_classification(datasets):
    report = [engine_runs(datasets, {"G01": GEN_RIGHT["G01"]})["G01"], gen_run(datasets, {"G09": [LIMIT]}, {"G09"})["G09"]]
    md = ev.summary_md(report)
    assert "범용성 측정 (general)" in md and "| answer | 1 | 1 |" in md and "| limit | 1 | 0 | 1 |" in md


# ---------------------------------------------------------------------------
# 처음 보는 질문 묶음 (holdout): 에이전트를 고친 뒤 general에만 맞춘 수정이 아닌지 확인
# ---------------------------------------------------------------------------
HOLD_RIGHT = {
    "H01": [("aggregate", {"op": "sum", "value_column": "units", "group_by": ["region"], "sort": "value_desc"})],
    "H02": [("aggregate", {"op": "sum", "value_column": "revenue",
                           "filters": [{"column": "region", "op": "==", "value": "대구"}]})],
    "H03": [("aggregate", {"op": "sum", "value_column": "revenue", "group_by": ["region"],
                           "filters": [{"column": "month", "op": "==", "value": "2026-10"}]})],
    "H04": [("compare_periods", {"date_column": "month", "op": "sum", "value_column": "revenue", "current": "2026-09"})],
    "H07": [("aggregate", {"op": "count", "group_by": ["창고"]})],
    "H08": [("aggregate", {"op": "mean", "value_column": "수량",
                           "filters": [{"column": "분류", "op": "==", "value": "완제품"}]})],
    "H10": [("aggregate", {"op": "mean", "value_column": "단가",
                           "filters": [{"column": "창고", "op": "==", "value": "평택"}]})],
    "H11": [("aggregate", {"op": "count", "filters": [{"column": "입고일", "op": ">=", "value": "2026-11-01"},
                                                     {"column": "입고일", "op": "<", "value": "2026-12-01"}]})],
    "H12": [("aggregate", {"op": "count", "group_by": ["채널"]})],
    "H13": [("aggregate", {"op": "mean", "value_column": "처리시간",
                           "filters": [{"column": "유형", "op": "==", "value": "제품불량"}]})],
    "H14": [("aggregate", {"op": "count", "group_by": ["처리상태"]})],
    "H15": [("aggregate", {"op": "mean", "value_column": "처리시간", "group_by": ["유형"], "sort": "value_desc"})],
}


def holdout_engine_runs(datasets, calls_by_id):
    from engine import run_tool
    qs = {q.id: q for q in ev.load_questions(("holdout",))}
    runs = [ev.Run(qid, qs[qid].text, "ok", set="holdout", stage="exec",
                   calls=[{"tool": n, "params": p} for n, p in calls],
                   results=[run_tool(datasets[qs[qid].dataset], n, p) for n, p in calls])
            for qid, calls in calls_by_id.items()]
    return {r["id"]: r for r in ev.grade(runs)}


def test_holdout_is_separate_from_general():
    assert "holdout" in ev.EXTRA_SETS and "holdout" not in ev.SETS
    hold = ev.load_questions(("holdout",))
    gen = {q.text for q in ev.load_questions(("general",))}
    assert len(hold) == 17 and not ({q.text for q in hold} & gen)  # 같은 문장 없음


def test_holdout_expected_matches_raw_csv():
    import pandas as pd
    exp = {a["id"]: a for a in _json.loads(ev.HOLDOUT_EXPECTED.read_text(encoding="utf-8"))["answers"]}
    s = pd.read_csv(ev.GENERAL_DIR / "sales_2026.csv")
    units = s.groupby("region").units.sum()
    assert exp["H01"]["checks"][0]["id"] == units.idxmax() and exp["H01"]["checks"][0]["value"] == units.max()
    assert exp["H03"]["checks"][0]["equals"] == s[s.month == "2026-10"].set_index("region").revenue.to_dict()
    m = s.groupby("month").revenue.sum()
    assert exp["H04"]["checks"][0]["equals"] == round((m["2026-09"] / m["2026-08"] - 1) * 100, 2)
    g = s.groupby("region")[["revenue", "units"]].sum()
    assert exp["H05"]["checks"][0]["equals"] == (g.revenue / g.units).round(2).to_dict()
    inv = pd.read_csv(ev.GENERAL_DIR / "inventory.csv")
    assert exp["H07"]["checks"][0]["equals"] == inv.창고.value_counts().to_dict()
    assert exp["H09"]["checks"][0]["equals"] == inv.groupby("분류").단가.max().to_dict()
    assert exp["H11"]["checks"][0]["equals"] == int((inv.입고일.str[:7] == "2026-11").sum())
    sp = pd.read_csv(ev.GENERAL_DIR / "support_tickets.csv")
    assert exp["H12"]["checks"][0]["equals"] == sp.채널.value_counts().to_dict()
    assert exp["H14"]["checks"][0]["equals"] == sp.처리상태.value_counts().to_dict()
    assert exp["H15"]["checks"][0]["id"] == sp.groupby("유형").처리시간.mean().idxmax()


def test_holdout_engine_can_answer(datasets):
    report = holdout_engine_runs(datasets, HOLD_RIGHT)
    wrong = {k: r["reason"] for k, r in report.items() if not r["pass"]}
    assert not wrong and len(report) == 12


@pytest.mark.parametrize("qid", ["H05", "H09", "H17"])
def test_holdout_limit_honest_refusal_passes(datasets, qid):
    q = [x for x in ev.load_questions(("holdout",)) if x.id == qid]
    r = ev.grade(ev.run_questions(q, datasets, lambda _: FakeLLM(FakeLLM.calls(LIMIT), FakeLLM.calls(LIMIT))))[0]
    assert r["pass"] and r["outcome"] == "정직한 거절"


def test_holdout_h09_answerable_with_max(datasets):
    """집계 max(최댓값) 추가로 '분류별 최고 단가'(H09)는 엔진으로 정답을 낼 수 있다 (이전에는 한계 질문)."""
    r = holdout_engine_runs(datasets, {"H09": [("aggregate", {"op": "max", "value_column": "단가", "group_by": ["분류"]})]})
    assert r["H09"]["pass"] and r["H09"]["outcome"] == "정답"


# ---------------------------------------------------------------------------
# 월 묶음(aggregate period=month)·비율 도구(calculate_ratio)로 답할 수 있게 된 한계 질문
# ---------------------------------------------------------------------------
NEW_TOOL_RIGHT = {
    "G09": [("calculate_ratio", {"kind": "share", "value_column": "revenue", "group_by": ["region"]})],
    "G16": [("aggregate", {"op": "sum", "value_column": "수량", "group_by": ["입고일"], "period": "month"})],
    "G20": [("calculate_ratio", {"kind": "condition_rate", "group_by": ["담당팀"], "sort": "value_asc",
                                 "condition": [{"column": "처리상태", "op": "==", "value": "처리완료"}]})],
}


def test_general_limit_answerable_with_month_and_ratio(datasets):
    report = engine_runs(datasets, NEW_TOOL_RIGHT)
    wrong = {k: r["reason"] for k, r in report.items() if not r["pass"]}
    assert not wrong and all(r["outcome"] == "정답" for r in report.values())


def test_holdout_h17_answerable_with_monthly_condition_rate(datasets):
    calls = [("calculate_ratio", {"kind": "condition_rate", "group_by": ["접수일"], "period": "month",
                                  "condition": [{"column": "처리상태", "op": "==", "value": "처리완료"}]})]
    r = holdout_engine_runs(datasets, {"H17": calls})["H17"]
    assert r["pass"] and r["outcome"] == "정답", r["reason"]


def test_general_g14_answerable_with_compare_column(datasets):
    calls = [("aggregate", {"op": "count", "filters": [{"column": "수량", "op": "<", "compare_column": "안전재고"}]})]
    r = engine_runs(datasets, {"G14": calls})["G14"]
    assert r["pass"] and r["outcome"] == "정답", r["reason"]
