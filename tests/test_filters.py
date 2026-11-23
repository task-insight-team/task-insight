"""부서·프로젝트 필터, 그리고 '없는 대상'과 '조건에 맞는 0건'의 구분."""

import pytest

from conftest import AS_OF, kafe_files
from engine import gemini_tool_declarations, run_tool


def F(*conds):
    return [{"column": c, "op": o, "value": v} for c, o, v in conds]


# ---------------------------------------------------------------------------
# 없는 대상 → 오류 + 가능한 값 / 대상은 맞는데 0건 → 정상 결과 0
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filters, expect_in_error", [
    (F(("부서", "==", "인사")), "가능한 값: 경영지원, 미배정, 연구, 영업, 제조, 품질"),
    (F(("프로젝트ID", "==", "P99")), "'P99' 값이 없습니다"),
    (F(("부서", "in", "품질,인사")), "'인사' 값이 없습니다"),
    (F(("담당자ID", "!=", "E999")), "'E999' 값이 없습니다"),
])
def test_unknown_target_is_an_error(kafe, filters, expect_in_error):
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": filters})
    assert not r.ok and expect_in_error in r.error


def test_zero_rows_is_a_valid_zero(kafe):
    """P01은 전부 완료 → 'P01 미완료 업무'는 오류가 아니라 0건."""
    cond = F(("프로젝트ID", "==", "P01"), ("상태", "!=", "완료"))
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": cond})
    assert r.ok and r.value == 0 and r.rows == [{"구분": "전체", "개수(건)": 0}] and r.used_rows == 0
    grouped = run_tool(kafe, "aggregate", {"op": "count", "group_by": ["부서"], "filters": cond})
    assert grouped.ok and grouped.value == 0 and grouped.rows == []
    total = run_tool(kafe, "aggregate", {"op": "sum", "value_column": "실적MH", "filters": cond})
    assert total.ok and total.value == 0


def test_zero_rows_mean_is_not_computable(kafe):
    cond = F(("프로젝트ID", "==", "P01"), ("상태", "!=", "완료"))
    r = run_tool(kafe, "aggregate", {"op": "mean", "value_column": "실적MH", "filters": cond})
    assert not r.ok and "0건이라 평균" in r.error


def test_date_and_number_filters_are_not_value_checked(kafe):
    """없는 날짜·숫자로 비교해도 '없는 대상'이 아니다 (정상 0건 또는 결과)."""
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": F(("기한", "==", "2030-01-01"))})
    assert r.ok and r.value == 0
    r = run_tool(kafe, "aggregate", {"op": "count", "filters": F(("계획MH", "==", "12345"))})
    assert r.ok and r.value == 0


def test_team_suffix_is_matched(kafe):
    a = run_tool(kafe, "aggregate", {"op": "count", "filters": F(("부서", "==", "품질팀"))})
    b = run_tool(kafe, "aggregate", {"op": "count", "filters": F(("부서", "==", "품질"))})
    assert a.ok and a.value == b.value == 42


# ---------------------------------------------------------------------------
# 도구 3개의 department·project_id
# ---------------------------------------------------------------------------
def test_overdue_by_department(kafe):
    r = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "department": "품질"})
    assert r.ok and r.value == 8 and {x["부서"] for x in r.rows} == {"품질"}
    assert "조건: 부서=품질" in r.basis
    assert run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "department": "품질팀"}).value == 8


def test_overdue_scoped_totals_add_up(kafe):
    """부서별로 따로 구한 기한 초과 합 = 전체 15건 (Q1과 같음)."""
    total = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF})
    depts = ["품질", "제조", "연구", "경영지원", "영업", "미배정"]
    parts = [run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "department": d}).value for d in depts]
    assert total.value == 15 and sum(parts) == 15


def test_overdue_zero_for_finished_project(kafe):
    r = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "project_id": "P01"})
    assert r.ok and r.value == 0 and r.rows == []


def test_completion_rate_by_department(kafe):
    r = run_tool(kafe, "calculate_completion_rate", {"group_by": "담당자ID", "department": "연구"})
    assert r.ok and "조건: 부서=연구" in r.basis
    by_dept = run_tool(kafe, "summarize_by_category", {"group_by": "부서"})
    research = next(x for x in by_dept.rows if x["부서"] == "연구")
    assert r.used_rows == research["전체(건)"] and r.value == research["완료율(%)"]


def test_completion_rate_project(kafe):
    r = run_tool(kafe, "calculate_completion_rate", {"project_id": "P10"})
    assert r.ok and r.value == 20.0  # Q5 정답과 같은 값


def test_summarize_project_scope_drops_headcount(kafe):
    """프로젝트 안의 건수를 부서 전체 인원으로 나누지 않는다."""
    r = run_tool(kafe, "summarize_by_category", {"group_by": "부서", "project_id": "P05", "as_of": AS_OF})
    assert r.ok and sum(x["전체(건)"] for x in r.rows) == 16
    assert all("인원" not in x and "1인당 미완료" not in x for x in r.rows)


def test_valid_targets_with_no_overlap_is_zero_not_unknown(kafe):
    """영업 부서는 P05 업무가 없다 → 'P05가 없다'가 아니라 0건."""
    r = run_tool(kafe, "summarize_by_category", {"group_by": "상태", "department": "영업", "project_id": "P05"})
    assert r.ok and r.rows == [] and "0건" in r.basis
    r = run_tool(kafe, "calculate_completion_rate", {"department": "영업", "project_id": "P05"})
    assert not r.ok and "0건이라 완료율" in r.error


@pytest.mark.parametrize("tool, params", [
    ("count_overdue_tasks", {"as_of": AS_OF, "department": "인사"}),
    ("calculate_completion_rate", {"project_id": "P99"}),
    ("summarize_by_category", {"group_by": "상태", "department": "총무"}),
])
def test_unknown_scope_is_an_error(kafe, tool, params):
    r = run_tool(kafe, tool, params)
    assert not r.ok and "값이 없습니다" in r.error and "가능한 값" in r.error


def test_department_scope_needs_employees():
    from engine import load_dataset
    tasks_only = load_dataset(kafe_files("tasks")).dataset
    r = run_tool(tasks_only, "count_overdue_tasks", {"as_of": AS_OF, "department": "품질"})
    assert not r.ok and "직원 파일" in r.error


def test_new_fields_are_optional_in_gemini_schema():
    decl = {d["name"]: d["parameters"] for d in gemini_tool_declarations()}
    for name in ("count_overdue_tasks", "calculate_completion_rate", "summarize_by_category"):
        props = decl[name]["properties"]
        assert props["department"]["nullable"] and props["project_id"]["nullable"]
        assert "department" not in decl[name].get("required", [])
