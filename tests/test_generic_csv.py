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


def test_bad_dates_are_flagged_and_excluded():
    res = load("date,amount\n2026-01-05,1\n2026-02-30,2\n2026/02/10,3\n모름,4\n")
    bad = next(i for r in res.reports for i in r.issues if i.code == "invalid_date")
    assert bad.rows == [2, 4]
    r = run_tool(res.dataset, "aggregate", {"op": "sum", "value_column": "amount"})
    assert r.value == 4 and r.used_rows == 2


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
    assert price.value == 4500 and price.used_rows == 3  # (300)은 숫자 아님 → 제외
    # 형식 오류 행은 행 전체를 계산에서 뺀다 → D행의 rate(5%)도 빠져 (12.5+8+10)/3
    assert run_tool(res.dataset, "aggregate", {"op": "mean", "value_column": "rate"}).value == 10.17


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
