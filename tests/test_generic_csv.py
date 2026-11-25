"""일반 CSV 범용성: kafe·sample.csv가 아닌 흔한 형식의 CSV도 읽고 계산한다."""

from engine import load_dataset, read_table, run_tool


def load(text: str, name: str = "data.csv", encoding: str = "utf-8"):
    res = load_dataset({name: text.encode(encoding)})
    assert res.ok, [i.message for r in res.reports for i in r.issues]
    assert res.dataset.kind == "generic"
    return res


def codes(res) -> set[str]:
    return {i.code for r in res.reports for i in r.issues}


def test_semicolon_and_tab_delimiters():
    for text in ("region;sales\n서울;100\n부산;50\n", "region\tsales\n서울\t100\n부산\t50\n"):
        r = run_tool(load(text).dataset, "aggregate", {"op": "sum", "value_column": "sales"})
        assert r.ok and r.value == 150


def test_comma_inside_quotes_still_comma_delimited():
    r = run_tool(load('item,price\nA,"1,200"\nB,800\n').dataset, "aggregate", {"op": "sum", "value_column": "price"})
    assert r.ok and r.value == 2000


def test_mixed_date_formats_are_normalized():
    res = load("date,amount\n2026/01/05,100\n2026.01.20,200\n2026-1-25,300\n2026-02-03 13:45:00,400\n")
    assert "date_normalized" in codes(res)
    r = run_tool(res.dataset, "compare_periods",
                 {"date_column": "date", "op": "sum", "value_column": "amount", "current": "2026-02"})
    assert r.ok and r.rows[0]["2026-01"] == 600 and r.rows[0]["2026-02"] == 400 and r.value == -33.33


def test_bad_dates_are_flagged_and_only_that_cell_excluded():
    """일반 CSV: 날짜로 읽을 수 없는 칸만 빈칸 → 날짜를 쓰는 계산에서만 빠지고, 같은 행의 숫자는 그대로 쓴다."""
    res = load("date,amount\n2026-01-05,1\n2026-02-30,2\n2026/02/10,3\n모름,4\n")
    bad = next(i for r in res.reports for i in r.issues if i.code == "invalid_date")
    assert bad.rows == [2, 4] and "그 칸만" in bad.message
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "amount"})
    assert r.value == 10 and r.used_rows == 4
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "amount", "group_by": ["date"], "period": "month"})
    assert {x["date(월)"]: x["합계(amount)"] for x in r.rows} == {"2026-01": 1, "2026-02": 3}


def test_dotted_month_column():
    res = load("month,sales\n2026.01,100\n2026.02,70\n")
    r = run_tool(res.dataset, "compare_periods", {"date_column": "month", "op": "sum", "value_column": "sales",
                                                  "current": "2026-02"})
    assert r.ok and r.value == -30


def test_decimal_numbers_are_not_months():
    res = load("item,price\nA,1500.50\nB,2300.75\nC,1999.5\n")
    assert "date_normalized" not in codes(res)
    assert run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "price"}).value == 5800.75


def test_currency_and_percent_units():
    res = load("item,price,rate\nA,₩1200,12.5%\nB,1500원,8%\nC,\"1,800\",10%\nD,(300),5%\n")
    assert "unit_removed" in codes(res)
    price = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "price"})
    assert price.value == 4500 and price.used_rows == 3  # (300)은 숫자 아님 → 그 칸만 제외
    # 일반 CSV는 그 칸만 뺀다 → D행의 rate(5%)는 그대로 써서 (12.5+8+10+5)/4
    assert run_tool(res.dataset, "aggregate", {"op": "mean", "value_column": "rate"}).value == 8.88


def test_leading_zero_codes_stay_text():
    res = load("zip,sales\n00123,10\n04567,20\n00123,5\n")
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "sales", "group_by": ["zip"]})
    assert {row["zip"] for row in r.rows} == {"00123", "04567"}


def test_trailing_comma_column_dropped():
    _, rep = read_table("a,b,\n1,2,\n3,4,\n".encode(), "t.csv")
    assert [c.name for c in rep.columns] == ["a", "b"]


def test_partial_standard_columns_is_generic():
    """employee_id·department만 겹치는 인사 CSV는 직원표로 오인하지 않는다."""
    res = load("employee_id,department,salary\nE1,영업,300\nE2,품질,400\n", "hr.csv")
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "salary", "group_by": ["department"]})
    assert r.ok and r.value == 700


def test_cp949_korean_headers():
    res = load("지역,매출\n서울,100\n부산,50\n", encoding="cp949")
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "매출", "group_by": ["지역"]})
    assert r.ok and r.value == 150


# ---------------------------------------------------------------------------
# 집계 최댓값·최솟값 (op = max / min)
# ---------------------------------------------------------------------------
INV = "품목,분류,단가\nA,부품,3200\nB,부품,9800\nC,원자재,850\nD,원자재,1500\nE,완제품,48000\n"


def test_max_min_total_and_grouped():
    ds = load(INV).dataset
    assert run_tool(ds, "aggregate", {"op": "max", "value_column": "단가"}).value == 48000
    assert run_tool(ds, "aggregate", {"op": "min", "value_column": "단가"}).value == 850
    r = run_tool(ds, "aggregate", {"op": "max", "value_column": "단가", "group_by": ["분류"], "sort": "value_desc"})
    assert r.ok and r.rows[0] == {"분류": "완제품", "최댓값(단가)": 48000.0}
    assert {x["분류"]: x["최댓값(단가)"] for x in r.rows} == {"완제품": 48000, "부품": 9800, "원자재": 1500}
    assert "최댓값 기준" in r.basis


def test_max_min_need_numeric_value_column():
    ds = load(INV).dataset
    assert "값 컬럼" in run_tool(ds, "aggregate", {"op": "max"}).error
    assert "숫자 컬럼이 아니" in run_tool(ds, "aggregate", {"op": "min", "value_column": "품목"}).error


def test_max_min_on_zero_rows_is_not_computable():
    ds = load(INV).dataset
    r = run_tool(ds, "aggregate", {"op": "max", "value_column": "단가",
                                   "filters": [{"column": "단가", "op": ">", "value": "100000"}]})
    assert not r.ok and "0건이라 최댓값" in r.error


def test_max_min_on_kafe(kafe):
    """업무 데이터에서도 같은 방식 (예: 프로젝트별 가장 큰 실적MH 업무)."""
    import pandas as pd
    r = run_tool(kafe, "aggregate", {"op": "max", "value_column": "실적MH", "group_by": ["프로젝트ID"]})
    expect = pd.to_numeric(kafe.main.loc[~kafe.main["_invalid"], "actual_mh"]).groupby(kafe.main["project_id"]).max()
    assert r.ok and {x["프로젝트ID"]: x["최댓값(실적MH)"] for x in r.rows} == expect.to_dict()


# ---------------------------------------------------------------------------
# 월별 묶음 (aggregate period = "month")
# ---------------------------------------------------------------------------
DAILY = "date,region,amount\n2026-01-05,A,100\n2026/01/20,B,200\n2026-02-03,A,400\n"


def test_period_month_groups_daily_dates():
    ds = load(DAILY).dataset
    r = run_tool(ds, "aggregate", {"op": "sum", "value_column": "amount", "group_by": ["date"], "period": "month"})
    assert r.ok and r.rows == [{"date(월)": "2026-01", "합계(amount)": 300.0}, {"date(월)": "2026-02", "합계(amount)": 400.0}]
    assert "date(월)별" in r.basis and r.value == 700
    r = run_tool(ds, "aggregate", {"op": "count", "group_by": ["region", "date"], "period": "month"})
    assert {(x["region"], x["date(월)"]): x["개수(건)"] for x in r.rows} == {
        ("A", "2026-01"): 1, ("A", "2026-02"): 1, ("B", "2026-01"): 1}


def test_period_month_needs_date_group():
    r = run_tool(load(DAILY).dataset, "aggregate",
                 {"op": "sum", "value_column": "amount", "group_by": ["region"], "period": "month"})
    assert not r.ok and "날짜 컬럼" in r.error


def test_period_month_does_not_change_source(kafe):
    before = kafe.main["completed_at"].copy()
    r = run_tool(kafe, "aggregate", {"op": "count", "group_by": ["완료일"], "period": "month"})
    import pandas as pd
    done = pd.to_datetime(kafe.main.loc[~kafe.main["_invalid"], "completed_at"]).dropna()
    assert r.ok and {x["완료일(월)"]: x["개수(건)"] for x in r.rows} == done.dt.strftime("%Y-%m").value_counts().to_dict()
    assert kafe.main["completed_at"].equals(before)


def test_without_period_output_is_unchanged():
    """period를 주지 않으면 기존 결과와 같다 (날짜별 그대로)."""
    r = run_tool(load(DAILY).dataset, "aggregate", {"op": "sum", "value_column": "amount", "group_by": ["date"]})
    assert [x["date"] for x in r.rows] == ["2026-01-05", "2026-01-20", "2026-02-03"]


# ---------------------------------------------------------------------------
# 비율 도구 (calculate_ratio)
# ---------------------------------------------------------------------------
PROD = ("date,line,prod,bad,status\n2026-01-05,A,100,5,완료\n2026-01-20,B,200,2,진행\n"
        "2026-02-03,A,300,9,완료\n2026-02-10,C,0,0,완료\n")


def ratio(params):
    return run_tool(load(PROD).dataset, "calculate_ratio", params)


def test_share_by_sum_and_by_count():
    r = ratio({"kind": "share", "value_column": "prod", "group_by": ["line"]})
    assert r.ok and r.unit == "%" and r.value is None
    assert [(x["line"], x["비중(%)"]) for x in r.rows] == [("A", 66.67), ("B", 33.33), ("C", 0.0)]  # 큰 순
    r = ratio({"kind": "share", "group_by": ["line"]})
    assert {x["line"]: x["비중(%)"] for x in r.rows} == {"A": 50.0, "B": 25.0, "C": 25.0}
    assert "group_by" in ratio({"kind": "share"}).error


def test_column_ratio_and_zero_denominator_group():
    r = ratio({"kind": "column_ratio", "value_column": "bad", "denominator_column": "prod"})
    assert r.ok and r.value == 2.67 and r.rows[0]["비율(%)"] == 2.67
    r = ratio({"kind": "column_ratio", "value_column": "bad", "denominator_column": "prod", "group_by": ["line"]})
    assert {x["line"]: x["비율(%)"] for x in r.rows} == {"A": 3.5, "B": 1.0, "C": None}
    assert "분모가 0인 그룹" in r.basis
    zero = ratio({"kind": "column_ratio", "value_column": "bad", "denominator_column": "prod",
                  "filters": [{"column": "line", "op": "==", "value": "C"}]})
    assert not zero.ok and "분모 합계가 0" in zero.error
    assert "숫자 컬럼이 아니" in ratio({"kind": "column_ratio", "value_column": "line",
                                       "denominator_column": "prod"}).error


def test_condition_rate_monthly():
    cond = [{"column": "status", "op": "==", "value": "완료"}]
    r = ratio({"kind": "condition_rate", "condition": cond})
    assert r.ok and r.value == 75.0 and r.rows[0]["해당(건)"] == 3 and r.rows[0]["전체(건)"] == 4
    r = ratio({"kind": "condition_rate", "condition": cond, "group_by": ["date"], "period": "month"})
    assert {x["date(월)"]: x["비율(%)"] for x in r.rows} == {"2026-01": 50.0, "2026-02": 100.0}
    assert "condition" in ratio({"kind": "condition_rate"}).error


def test_condition_rate_unknown_value_and_zero_rows():
    bad = ratio({"kind": "condition_rate", "condition": [{"column": "status", "op": "==", "value": "없음"}]})
    assert not bad.ok and "값이 없습니다" in bad.error
    empty = ratio({"kind": "condition_rate", "condition": [{"column": "status", "op": "==", "value": "완료"}],
                   "filters": [{"column": "prod", "op": ">", "value": "10000"}]})
    assert not empty.ok and "0건이라" in empty.error


def test_condition_rate_matches_completion_rate_on_kafe(kafe):
    """업무 데이터에서 '상태=완료' 조건 비율은 완료율 도구와 같다."""
    a = run_tool(kafe, "calculate_ratio", {"kind": "condition_rate", "group_by": ["부서"],
                                           "condition": [{"column": "상태", "op": "==", "value": "완료"}]})
    b = run_tool(kafe, "calculate_completion_rate", {"group_by": "부서"})
    assert a.ok and b.ok and a.value == b.value
    assert {x["부서"]: x["비율(%)"] for x in a.rows} == {x["부서"]: x["완료율(%)"] for x in b.rows}


# ---------------------------------------------------------------------------
# 컬럼끼리 비교 필터 (Filter.compare_column)
# ---------------------------------------------------------------------------
STOCK = "품목,수량,안전재고,창고\nA,100,200,평택\nB,500,300,평택\nC,50,,김해\nD,80,80,김해\n"


def test_compare_column_filter():
    ds = load(STOCK).dataset
    r = run_tool(ds, "aggregate", {"op": "count", "filters": [
        {"column": "수량", "op": "<", "compare_column": "안전재고"}]})
    assert r.ok and r.value == 1 and "수량 < 안전재고" in r.basis  # C는 안전재고 빈칸이라 비교하지 않음
    r = run_tool(ds, "aggregate", {"op": "count", "filters": [
        {"column": "수량", "op": "<=", "compare_column": "안전재고"}], "group_by": ["창고"]})
    assert {x["창고"]: x["개수(건)"] for x in r.rows} == {"김해": 1, "평택": 1}


def test_compare_column_errors():
    ds = load(STOCK).dataset
    assert "형식이 달라" in run_tool(ds, "aggregate", {"op": "count", "filters": [
        {"column": "수량", "op": "<", "compare_column": "창고"}]}).error
    assert "없" in run_tool(ds, "aggregate", {"op": "count", "filters": [
        {"column": "수량", "op": "<", "compare_column": "없는컬럼"}]}).error


# ---------------------------------------------------------------------------
# 계산 컬럼: aggregate multiply_by (값 × 곱할 컬럼), calculate_ratio percent=False (×100 없는 나눗셈)
# ---------------------------------------------------------------------------
STOCK_VALUE = "품목,창고,수량,단가,비고\nA,평택,10,100,a\nB,평택,5,,b\nC,김해,2,300,c\nD,김해,,50,d\nE,김해,4,25,e\n"


def test_multiply_by_sum_total_and_grouped():
    ds = load(STOCK_VALUE).dataset
    r = run_tool(ds, "aggregate", {"op": "sum", "value_column": "수량", "multiply_by": "단가"})
    assert r.ok and r.value == 1700 and r.rows == [{"구분": "전체", "합계(수량×단가)": 1700.0}]
    assert r.used_rows == 3 and "수량×단가" in r.basis and "빈칸" in r.basis  # B·D는 한쪽이 빈칸이라 제외
    r = run_tool(ds, "aggregate", {"op": "sum", "value_column": "수량", "multiply_by": "단가",
                                   "group_by": ["창고"], "sort": "value_desc"})
    assert r.rows == [{"창고": "평택", "합계(수량×단가)": 1000.0}, {"창고": "김해", "합계(수량×단가)": 700.0}]
    r = run_tool(ds, "aggregate", {"op": "max", "value_column": "수량", "multiply_by": "단가"})
    assert r.value == 1000 and "최댓값(수량×단가)" in r.rows[0]


def test_multiply_by_errors_and_source_unchanged():
    ds = load(STOCK_VALUE).dataset
    before = ds.main.copy()
    assert "count" in run_tool(ds, "aggregate", {"op": "count", "multiply_by": "단가"}).error
    assert "숫자 컬럼이 아니" in run_tool(ds, "aggregate", {"op": "sum", "value_column": "수량",
                                                          "multiply_by": "비고"}).error
    assert "없습니다" in run_tool(ds, "aggregate", {"op": "sum", "value_column": "수량",
                                                    "multiply_by": "없는컬럼"}).error
    assert ds.main.equals(before) and "_calc" not in ds.main.columns


def test_multiply_by_absent_keeps_output():
    ds = load(STOCK_VALUE).dataset
    r = run_tool(ds, "aggregate", {"op": "sum", "value_column": "수량", "group_by": ["창고"]})
    assert r.rows == [{"창고": "김해", "합계(수량)": 6.0}, {"창고": "평택", "합계(수량)": 15.0}]
    assert "계산식" not in r.basis


def test_column_ratio_without_percent():
    r = ratio({"kind": "column_ratio", "value_column": "prod", "denominator_column": "bad", "percent": False,
               "group_by": ["line"]})
    assert r.ok and r.unit == "" and "×100 하지 않음" in r.basis
    assert {x["line"]: x["값(prod÷bad)"] for x in r.rows} == {"A": 28.57, "B": 100.0, "C": None}
    assert r.value == 37.5  # 600 ÷ 16
    assert ratio({"kind": "column_ratio", "value_column": "bad", "denominator_column": "prod"}).unit == "%"  # 기본값은 그대로


def test_percent_false_only_for_column_ratio():
    for kind, extra in (("share", {"group_by": ["line"]}),
                        ("condition_rate", {"condition": [{"column": "status", "op": "==", "value": "완료"}]})):
        r = ratio({"kind": kind, "percent": False, **extra})
        assert not r.ok and "column_ratio에서만" in r.error


# ---------------------------------------------------------------------------
# 일반 CSV 형식 오류는 그 칸만 제외 (DATASET_VERSION 2). 표준 업무 표는 지금처럼 행 전체 제외
# ---------------------------------------------------------------------------
def test_text_in_other_numeric_column_does_not_change_this_column():
    """Ticket처럼 숫자·글자가 섞인 컬럼 때문에 상관없는 Fare 평균이 바뀌면 안 된다 (DABench test_ave.csv에서 발견)."""
    res = load("ticket,fare\nA/5 21171,10\n113803,20\n3101,30\nPC 17599,40\n12345,50\n")
    issue = next(i for r in res.reports for i in r.issues if i.code == "invalid_number")
    assert issue.rows == [1, 4] and "그 칸만" in issue.message
    ds = res.dataset
    assert run_tool(ds, "aggregate", {"op": "mean", "value_column": "fare"}).value == 30  # 5행 모두
    t = run_tool(ds, "aggregate", {"op": "sum", "value_column": "ticket"})
    assert t.value == 113803 + 3101 + 12345 and t.used_rows == 3  # 글자 칸만 빠짐
    assert not ds.main["_invalid"].any()


def test_standard_tables_still_drop_whole_row():
    """업무 표준 표는 기존 규칙(형식 오류 행은 모든 계산에서 제외) 그대로."""
    from pathlib import Path
    df, rep = read_table(Path(__file__).resolve().parents[1] / "data" / "errors" / "bad_tasks.csv")
    issue = next(i for i in rep.issues if i.code == "invalid_number")
    assert rep.kind == "tasks" and "해당 행은 계산에서 제외" in issue.message
    assert df.loc[df["_row"] == issue.rows[0], "_invalid"].all()


def test_year_last_dates_month_first_and_day_first():
    us = load("when,v\n01/15/2026 10:00,1\n02/03/2026 00:00,2\n12/31/2026 23:59,3\n")
    assert list(us.dataset.main["when"]) == ["2026-01-15", "2026-02-03", "2026-12-31"]
    r = run_tool(us.dataset, "aggregate", {"op": "mean", "value_column": "v", "group_by": ["when"], "period": "month"})
    assert {x["when(월)"]: x["평균(v)"] for x in r.rows} == {"2026-01": 1, "2026-02": 2, "2026-12": 3}
    eu = load("when,v\n15.01.2026,1\n03.02.2026,2\n")
    assert list(eu.dataset.main["when"]) == ["2026-01-15", "2026-02-03"]


def test_ambiguous_year_last_dates_are_not_guessed():
    res = load("when,v\n01/02/2026,1\n03/04/2026,2\n")
    assert "date_ambiguous" in codes(res)
    assert list(res.dataset.main["when"]) == ["01/02/2026", "03/04/2026"]  # 월/일을 추측하지 않고 글자로 둔다
