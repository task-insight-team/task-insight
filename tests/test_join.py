"""표 결합 검증 (과제 Step 5 확장 ②: 두 데이터셋 결합 — 키 중복과 행 증가 검증)."""

import pandas as pd

from conftest import AS_OF, ERR, KAFE, kafe_files
from engine import load_dataset, run_tool


def test_join_keeps_row_count(kafe):
    res = load_dataset(kafe_files())
    assert res.join.tasks_rows == res.join.joined_rows == 150


def test_budget_is_not_double_counted(kafe):
    """예산을 업무에 붙여 더하면 109.03억, 프로젝트당 한 번만 쓰면 7.03억."""
    tasks = pd.read_csv(KAFE / "tasks.csv", encoding="utf-8-sig")
    projects = pd.read_csv(KAFE / "projects.csv", encoding="utf-8-sig")
    naive = tasks.merge(projects, on="project_id")["budget"].sum()
    assert naive == 10_903_000_000  # 잘못된 방식
    assert "budget" not in kafe.main.columns  # 엔진은 예산을 업무 행에 붙이지 않는다
    r = run_tool(kafe, "cost_vs_budget", {})
    assert sum(row["예산"] for row in r.rows) == 703_000_000


def test_unassigned_department(kafe):
    assert (kafe.main.loc[kafe.main["assignee_id"].isna(), "department"] == "미배정").all()


def test_duplicate_employee_id_blocks_join():
    files = {**kafe_files("tasks", "projects"), "employees.csv": ERR / "employees_dup.csv"}
    res = load_dataset(files)
    assert not res.ok and not res.join.ok
    assert res.join.issues[0].code == "duplicate_key" and "E001" in res.join.issues[0].message


def test_duplicate_key_not_fixed_by_dedupe():
    files = {**kafe_files("tasks", "projects"), "employees.csv": ERR / "employees_dup.csv"}
    res = load_dataset(files, drop_duplicates=True)  # 내용이 다른 중복이라 완전 중복 제거로는 해결되지 않음
    assert not res.ok


def test_tasks_only_still_works():
    res = load_dataset(kafe_files("tasks"))
    assert res.ok and "department" not in res.dataset.main.columns
    assert run_tool(res.dataset, "count_overdue_tasks", {"as_of": AS_OF}).value == 15
    r = run_tool(res.dataset, "cost_vs_budget", {})
    assert not r.ok and "3개" in r.error


def test_duplicate_kind_rejected():
    res = load_dataset({"a.csv": KAFE / "tasks.csv", "b.csv": KAFE / "tasks.csv"})
    assert not res.ok and res.errors[0].code == "duplicate_kind"


def test_mixed_standard_and_generic_rejected():
    res = load_dataset({**kafe_files("tasks"), "sample.csv": KAFE.parent / "sample.csv"})
    assert not res.ok and res.errors[0].code == "mixed_files"


def test_dimension_without_tasks_rejected():
    res = load_dataset(kafe_files("employees", "projects"))
    assert not res.ok and res.errors[0].code == "tasks_required"
