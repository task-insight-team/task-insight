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


# ---------------------------------------------------------------------------
# run_tool 입력 정규화: DataFrame / 정상 Dataset / 코드 재로드 전후 Dataset / 구조가 다른 Dataset / 잘못된 입력
# (2026-11-23 배포에서 발견: 재로드 후 캐시된 옛 Dataset을 일반 CSV로 오인 → "업무 파일이 있어야 한다" 오류)
# ---------------------------------------------------------------------------
import importlib
from types import SimpleNamespace

import pandas as pd
import pytest

import engine.loader as loader_mod
from conftest import kafe_files
from engine.loader import DATASET_VERSION, Dataset


def _reloaded_dataset():
    """Streamlit 배포가 새 코드를 받을 때처럼 engine.loader를 다시 읽고, 새 클래스로 kafe를 불러온다."""
    fresh = importlib.reload(loader_mod)
    try:
        return fresh.load_dataset(kafe_files("tasks", "employees", "projects")).dataset
    finally:
        importlib.reload(loader_mod)


def test_dataframe_is_wrapped_as_generic(sample_df):
    r = run_tool(sample_df, "aggregate", {"op": "sum", "value_column": "sales"})
    assert r.ok and r.value == 500


def test_current_dataset_runs(kafe):
    assert isinstance(kafe, Dataset) and kafe.version == DATASET_VERSION
    r = run_tool(kafe, "count_overdue_tasks", {"as_of": AS_OF, "department": "품질"})
    assert r.ok and r.value == 8


def test_dataset_from_reloaded_module_runs():
    """클래스가 달라도 같은 구조 버전이면 업무 데이터로 계산한다 (수정 전에는 일반 CSV로 오인해 실패)."""
    ds = _reloaded_dataset()
    from engine.registry import Dataset as registry_dataset
    assert type(ds) is not registry_dataset  # 클래스가 달라진 상황을 실제로 재현했는지
    r = run_tool(ds, "count_overdue_tasks", {"as_of": AS_OF, "department": "품질"})
    assert r.ok, r.error
    assert r.value == 8


def test_all_tools_run_on_reloaded_dataset():
    """도구 9개가 재로드 후 Dataset에서 형식 오류 없이 돈다."""
    ds = _reloaded_dataset()
    calls = {
        "aggregate": {"op": "count", "group_by": ["부서"]},
        "compare_periods": {"date_column": "완료월", "op": "count", "current": "2026-10"},
        "count_overdue_tasks": {"as_of": AS_OF},
        "summarize_by_category": {"group_by": "부서", "as_of": AS_OF},
        "calculate_completion_rate": {"group_by": "프로젝트ID"},
        "find_recurring_issues": {},
        "mh_overrun": {},
        "cost_vs_budget": {},
        "check_missing_data": {},
    }
    from engine import TOOLS
    assert set(calls) == set(TOOLS)
    for name, params in calls.items():
        r = run_tool(ds, name, params)
        assert r.ok, f"{name}: {r.error}"


@pytest.mark.parametrize("version", [DATASET_VERSION + 1, 0])
def test_dataset_with_other_version_is_refused(kafe, version):
    """구조 버전이 다른 Dataset(앱 업데이트 전에 만든 데이터 등)으로는 계산하지 않고 사유를 알린다."""
    old = Dataset(kind=kafe.kind, main=kafe.main, employees=kafe.employees, projects=kafe.projects, version=version)
    r = run_tool(old, "count_overdue_tasks", {"as_of": AS_OF})
    assert not r.ok and r.value is None and not r.rows
    assert f"버전 {version}" in r.error and "다시 불러와" in r.error


def test_dataset_without_version_is_refused(kafe):
    """버전 정보가 없는 옛 객체(이 수정 전 코드로 만든 데이터)도 무조건 실행하지 않는다."""
    legacy = SimpleNamespace(kind=kafe.kind, main=kafe.main, employees=kafe.employees, projects=kafe.projects,
                             table=kafe.table)
    r = run_tool(legacy, "count_overdue_tasks", {"as_of": AS_OF})
    assert not r.ok and "정보 없음" in r.error


@pytest.mark.parametrize("bad", [object(), None, "tasks.csv", {"kind": "standard"},
                                 SimpleNamespace(kind="weird", main=pd.DataFrame(), employees=None, projects=None,
                                                 table=lambda n: None, version=DATASET_VERSION)])
def test_invalid_input_is_refused(bad):
    r = run_tool(bad, "aggregate", {"op": "count"})
    assert not r.ok and "데이터 형식" in r.error
