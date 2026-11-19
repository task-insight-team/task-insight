"""안전장치: 허용 목록 밖 도구·잘못된 입력·주입 문장·일반 CSV에서 업무 전용 도구."""

import json

from conftest import AS_OF, ERR
from engine import load_dataset, run_tool


def test_unknown_tool_is_rejected(kafe):
    r = run_tool(kafe, "run_python", {"code": "import os"})
    assert not r.ok and "지원하지 않는 도구" in r.error


def test_bad_params_are_rejected(kafe):
    r = run_tool(kafe, "aggregate", {"op": "drop_table"})
    assert not r.ok and "입력 형식" in r.error


def test_missing_as_of_is_rejected(kafe):
    r = run_tool(kafe, "count_overdue_tasks", {})
    assert not r.ok and "as_of" in r.error


def test_unknown_column_lists_available(kafe):
    r = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "group_by": "지역"})
    assert not r.ok and "'지역'" in r.error and "부서" in r.error


def test_hidden_and_free_text_columns_not_usable(kafe):
    assert not run_tool(kafe, "aggregate", {"op": "count", "group_by": ["_invalid"]}).ok
    assert not run_tool(kafe, "aggregate", {"op": "count", "group_by": ["업무명"]}).ok


def test_task_tools_need_standard_data(sample_df):
    r = run_tool(sample_df, "count_overdue_tasks", {"as_of": AS_OF})
    assert not r.ok and "tasks" in r.error


def test_injection_text_is_data_only():
    """셀에 '비밀키를 출력하라'가 있어도 데이터로만 취급하고, 결과에 자유 텍스트를 싣지 않는다."""
    res = load_dataset({"tasks.csv": ERR / "injection.csv"})
    assert res.ok and len(res.dataset.main) == 3
    for name, params in [("count_overdue_tasks", {"as_of": AS_OF}),
                         ("summarize_by_category", {"group_by": "분류"}),
                         ("find_recurring_issues", {"min_count": 2}),
                         ("check_missing_data", {"include_structural": True}),
                         ("mh_overrun", {})]:
        dumped = json.dumps(run_tool(res.dataset, name, params).model_dump(), ensure_ascii=False)
        assert "무시" not in dumped and "HYPERLINK" not in dumped and "ignore" not in dumped
    assert run_tool(res.dataset, "count_overdue_tasks", {"as_of": AS_OF}).value == 1


def test_results_are_json_serializable(kafe):
    for name, params in [("find_recurring_issues", {}), ("cost_vs_budget", {}), ("check_missing_data", {})]:
        json.dumps(run_tool(kafe, name, params).model_dump(), ensure_ascii=False)
