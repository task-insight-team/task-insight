"""kafe 원본 데이터 정합성 (엔진을 거치지 않고 pandas로 직접 확인).

엔진 결과가 맞으려면 원본이 이 조건을 지켜야 한다. 데이터 파일이 바뀌면 여기서 먼저 걸린다.
"""

import pandas as pd
import pytest

from conftest import AS_OF, ROOT

D = ROOT / "data" / "kafe"


def read(name):
    return pd.read_csv(D / f"{name}.csv", dtype=str, keep_default_na=False).replace("", pd.NA)


@pytest.fixture(scope="module")
def raw():
    return read("tasks"), read("employees"), read("projects")


def date(s):
    return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")


def num(s):
    return pd.to_numeric(s, errors="coerce")


def ids(df, mask, key="task_id"):
    return sorted(df.loc[mask.fillna(False).astype(bool), key])


def test_sizes_and_unique_keys(raw):
    t, e, p = raw
    assert (len(t), len(e), len(p)) == (150, 50, 10)
    assert t.task_id.is_unique and e.employee_id.is_unique and p.project_id.is_unique


def test_foreign_keys(raw):
    t, e, p = raw
    assert ids(t, ~t.project_id.isin(p.project_id)) == []
    assert ids(t, t.assignee_id.notna() & ~t.assignee_id.isin(e.employee_id)) == []
    assert set(p.lead_department) <= set(e.department)


def test_dates_are_consistent(raw):
    t, _, p = raw
    start, due, done_at = date(t.start_date), date(t.due_date), date(t.completed_at)
    for col in ("start_date", "due_date", "completed_at"):
        assert ids(t, t[col].notna() & date(t[col]).isna()) == [], f"{col} 형식"
    assert ids(t, due < start) == []
    assert ids(t, done_at < start) == []
    assert ids(t, done_at > pd.Timestamp(AS_OF)) == []  # 기준일 이후 완료 없음
    assert ids(t, (start > pd.Timestamp(AS_OF)) & (t.status != "미착수")) == []  # 미래 시작은 미착수만
    assert ids(p, date(p.planned_end_date) < date(p.project_start_date), "project_id") == []


def test_status_progress_and_completion(raw):
    t, _, _ = raw
    done = t.status == "완료"
    progress = num(t.progress_percent)
    assert ids(t, done & t.completed_at.isna()) == []
    assert ids(t, ~done & t.completed_at.notna()) == []
    assert ids(t, done & (progress != 100)) == []
    assert ids(t, ~done & (progress == 100)) == []
    assert ids(t, (t.status == "미착수") & ((progress > 0) | (num(t.actual_mh) > 0))) == []


def test_issue_type_only_for_nonconformance(raw):
    t, _, _ = raw
    nonconf = t.category == "부적합 대응"
    assert ids(t, ~nonconf & t.issue_type.notna()) == []
    assert ids(t, nonconf & t.issue_type.isna()) == []


def test_numbers_are_valid(raw):
    t, e, p = raw
    for col in ("planned_mh", "actual_mh", "planned_nonlabor_cost", "actual_nonlabor_cost"):
        assert ids(t, num(t[col]).isna() | (num(t[col]) < 0)) == [], col
    assert ids(t, num(t.planned_mh) == 0) == []
    assert (num(e.hourly_labor_cost) > 0).all() and (num(p.budget) > 0).all()


def test_project_status_matches_tasks(raw):
    t, _, p = raw
    m = t.merge(p[["project_id", "status"]].rename(columns={"status": "p_status"}), on="project_id")
    assert ids(m, (m.p_status == "완료") & (m.status != "완료")) == []


def test_known_totals(raw):
    """정답표의 기본 수치: 확인 필요 7칸(담당자 4·기한 3), 구조적 공백 178칸, 예산 7.03억 (잘못 결합하면 15.5배)."""
    t, _, p = raw
    assert t.assignee_id.isna().sum() == 4 and t.due_date.isna().sum() == 3
    structural = (t.completed_at.isna() & (t.status != "완료")).sum() + (
        (t.category != "부적합 대응") & t.issue_type.isna()).sum()
    assert structural == 178
    budget = num(p.budget).sum()
    naive = num(t.merge(p, on="project_id").budget).sum()
    assert budget == 703_000_000 and round(naive / budget, 1) == 15.5
