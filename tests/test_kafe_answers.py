"""kafe 데이터 평가 질문 Q1~Q7: 고정 정답(tests/fixtures/kafe/evaluation_answers.json)과 비교.

정답 파일은 데이터 제작 스크립트가 계산한 값이고, 별도의 pandas 계산으로 한 번 더 일치를 확인했다.
엑셀로 Q1·Q6·Q7 정도는 손으로 확인해 두는 것을 권장한다 (docs/answer_key.md).
테스트가 실패하면 정답 파일을 고치지 말고 엔진을 확인한다.
"""

from conftest import AS_OF, rows_by
from engine import run_tool


def test_q1_overdue_unfinished(kafe, answers):
    r = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF})
    assert r.ok and r.value == answers["Q1"]["answer"] == 15
    assert [row["업무ID"] for row in r.rows] == answers["Q1"]["answer_ids"]


def test_q2_unfinished_by_department(kafe, answers):
    r = run_tool(kafe, "summarize_by_category", {"group_by": "부서"})
    assert rows_by(r, "부서", "미완료(건)") == answers["Q2"]["answer"]
    per_head = {k: v for k, v in rows_by(r, "부서", "1인당 미완료").items() if v is not None}
    assert per_head == answers["Q2"]["supplementary"]["unfinished_per_head"]
    assert r.rows[0]["부서"] == "품질"


def test_q3_mh_overrun(kafe, answers):
    r = run_tool(kafe, "mh_overrun", {"group_by": "프로젝트ID"})
    assert r.rows[0]["프로젝트ID"] == answers["Q3"]["answer"] == "P05"
    sup = answers["Q3"]["supplementary"]
    assert rows_by(r, "프로젝트ID", "초과MH 합계") == {k: v["mh_overrun_sum"] for k, v in sup.items()}
    assert sum(row["초과 투입 업무(건)"] for row in r.rows) == 29 + 5  # 완료 초과 29건 + 미완료 초과 5건


def test_q4_cost_vs_budget(kafe, answers):
    r = run_tool(kafe, "cost_vs_budget", {})
    top = r.rows[0]
    assert top["프로젝트ID"] == answers["Q4"]["answer"] == "P01"
    assert abs(top["비용 초과율(%)"] / 100 - answers["Q4"]["answer_value"]) <= 0.005
    for pid, sup in answers["Q4"]["supplementary"].items():
        row = next(x for x in r.rows if x["프로젝트ID"] == pid)
        assert row["예산"] == sup["budget"]
        assert row["실적 인건비"] == sup["actual_labor_cost"]
        assert row["실적 비인건비"] == sup["actual_nonlabor_cost"]
        assert row["실적 총비용"] == sup["actual_total_cost"]
        assert abs(row["비용 초과율(%)"] / 100 - sup["cost_overrun_rate"]) <= 0.00005


def test_q5_lowest_completion(kafe, answers):
    r = run_tool(kafe, "calculate_completion_rate", {"group_by": "프로젝트ID"})
    assert r.rows[0]["프로젝트ID"] == answers["Q5"]["answer"] == "P10"
    assert r.rows[0]["완료율(%)"] / 100 == answers["Q5"]["answer_value"]
    sup = answers["Q5"]["supplementary"]
    assert rows_by(r, "프로젝트ID", "완료(건)") == {k: v["completed"] for k, v in sup.items()}


def test_q6_missing_data(kafe, answers):
    r = run_tool(kafe, "check_missing_data", {})
    assert r.value == answers["Q6"]["answer"] == 7
    ids = answers["Q6"]["answer_ids"]
    assert [x["업무ID"] for x in r.rows if x["컬럼"] == "담당자ID"] == ids["assignee_id_missing"]
    assert [x["업무ID"] for x in r.rows if x["컬럼"] == "기한"] == ids["due_date_missing"]
    assert "178칸" in r.basis  # 구조적 공백은 문제로 세지 않음


def test_q7_most_assigned_employee(kafe, answers):
    r = run_tool(kafe, "aggregate", {"op": "count", "group_by": ["담당자ID"], "sort": "value_desc", "top_n": 1})
    assert r.rows[0] == {"담당자ID": answers["Q7"]["answer"], "개수(건)": 9}


def test_recurring_issue_types(kafe):
    """SCENARIOS S5: 커넥터 접촉불량 5건이 4개 프로젝트에서 반복."""
    r = run_tool(kafe, "find_recurring_issues", {"key": "문제유형", "min_count": 2})
    assert rows_by(r, "문제유형", "발생(건)") == {"커넥터 접촉불량": 5, "온도 드리프트": 4, "PCB 솔더링 불량": 3, "방수 실링 누설": 2}
    assert r.rows[0]["관련 프로젝트"] == "P02, P05, P06, P10"


def test_same_question_same_answer(kafe):
    """재현성: 같은 입력이면 같은 결과."""
    p = {"as_of": AS_OF, "group_by": "프로젝트ID"}
    assert run_tool(kafe, "count_overdue_tasks", p).model_dump() == run_tool(kafe, "count_overdue_tasks", p).model_dump()
