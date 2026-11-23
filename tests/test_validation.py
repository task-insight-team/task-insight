"""업로드 검증과 전처리 (과제 Step 1·2의 오류 안내 기준)."""

from conftest import DATA, ERR, KAFE
from contracts import MAX_UPLOAD_BYTES
from engine import clean, read_table, run_tool


def codes(report):
    return {i.code for i in report.issues}


def test_kafe_files_detected():
    for name, kind in [("tasks", "tasks"), ("employees", "employees"), ("projects", "projects")]:
        _, rep = read_table(KAFE / f"{name}.csv")
        assert rep.ok and rep.kind == kind and rep.encoding == "utf-8-sig"


def test_kafe_tasks_report():
    _, rep = read_table(KAFE / "tasks.csv")
    needs = [i for i in rep.issues if i.code == "needs_check"]
    assert [i.count for i in needs] == [4, 3]  # 담당자 빈칸 4, 기한 빈칸 3
    structural = sum(i.count for i in rep.issues if i.code == "structural_blank")
    assert structural == 178 and all(i.level == "info" for i in rep.issues if i.code == "structural_blank")


def test_empty_file():
    df, rep = read_table(ERR / "empty.csv")
    assert df is None and not rep.ok and codes(rep) == {"empty_file"}


def test_header_only():
    df, rep = read_table(ERR / "header_only.csv")
    assert df is None and codes(rep) == {"no_rows"}


def test_too_large():
    df, rep = read_table(b"a,b\n" + b"1,2\n" * (MAX_UPLOAD_BYTES // 4 + 1))
    assert df is None and codes(rep) == {"too_large"}


def test_missing_columns():
    df, rep = read_table(ERR / "missing_columns.csv")
    assert df is None and rep.kind == "tasks" and codes(rep) == {"missing_columns"}
    assert "due_date" in rep.issues[0].message and "completed_at" in rep.issues[0].message


def test_cp949_encoding_is_supported():
    df, rep = read_table(ERR / "cp949_ok.csv")
    assert rep.ok and rep.encoding == "cp949" and df.loc[0, "담당부서"] == "설비팀"


def test_bad_rows_are_reported_with_row_numbers():
    df, rep = read_table(ERR / "bad_tasks.csv")
    assert rep.ok  # 행 단위 문제는 경고: 안내 후 분석 가능
    found = {}
    for i in rep.issues:
        found.setdefault(i.code, []).extend(i.rows)
    assert found["invalid_date"] == [2]  # 11/05
    assert found["invalid_value"] == [3]  # 상태 '대기'
    assert found["invalid_number"] == [4]  # 실적MH N/A
    assert found["negative_number"] == [5]
    assert found["due_before_start"] == [6]
    assert found["done_without_date"] == [7]
    assert found["duplicate_rows"] == [8]  # 1행과 완전히 같음
    assert found["duplicate_id"] == [9, 10]  # 같은 ID, 다른 내용
    assert found["missing_values"] == [11]  # 분류 빈칸


def test_clean_reports_before_after_counts_and_keeps_original():
    df, _ = read_table(ERR / "bad_tasks.csv")
    out, res = clean(df, drop_duplicates=True, drop_invalid=True)
    assert (res.before_rows, res.removed_duplicates, res.removed_invalid, res.after_rows) == (11, 1, 5, 5)
    assert len(out) == 5 and len(df) == 11  # 원본은 그대로


def test_invalid_numbers_excluded_from_sum():
    df, rep = read_table(ERR / "invalid_numbers.csv")
    issue = next(i for i in rep.issues if i.code == "invalid_number")
    assert issue.rows == [2, 3]
    r = run_tool(df, "aggregate", {"op": "sum", "value_column": "sales"})
    assert r.value == 150 and r.used_rows == 2  # 100 + 50, N/A·없음·빈칸 제외


def test_sample_is_generic():
    _, rep = read_table(DATA / "sample.csv")
    assert rep.kind == "generic"
    assert {c.name: c.dtype for c in rep.columns} == {"month": "text", "region": "text", "sales": "number"}


# ---------------------------------------------------------------------------
# 사용자에게 보일 안내 문구: 한국어 컬럼 이름 + 원래 이름, 실제 문제만 안내
# ---------------------------------------------------------------------------
def messages(rep):
    return {i.code: i.message for i in rep.issues}


def test_messages_use_korean_names():
    _, rep = read_table(ERR / "bad_tasks.csv")
    m = messages(rep)
    assert "'시작일(start_date)'" in m["invalid_date"]
    assert "'계획MH(planned_mh)'" in m["negative_number"]
    assert "'실적MH(actual_mh)'" in m["invalid_number"]
    assert "'상태(status)'" in m["invalid_value"]
    assert "'업무ID(task_id)'" in m["duplicate_id"]
    _, rep = read_table(ERR / "missing_columns.csv")
    assert rep.issues[0].message.startswith("업무(tasks) 파일에 필요한 컬럼이 없습니다: 기한(due_date)")


def test_missing_values_names_only_blank_columns():
    _, rep = read_table(ERR / "bad_tasks.csv")
    msg = messages(rep)["missing_values"]
    assert "분류(category)" in msg and "task_id" not in msg and "planned_mh" not in msg


def test_file_errors_name_the_files():
    from engine import load_dataset
    res = load_dataset({"empty.csv": ERR / "empty.csv", "header_only.csv": ERR / "header_only.csv"})
    assert not res.ok and "empty.csv" in res.errors[0].message and "header_only.csv" in res.errors[0].message
