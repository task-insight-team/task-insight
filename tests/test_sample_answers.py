"""과제 문서의 sample.csv 정답 (운영자 제공 값 그대로).

총 500, 서울 250, 부산 250, 1월 300, 2월 200, 전월 대비 약 -33.33%
"""

from conftest import rows_by
from engine import run_tool


def test_total_sales(sample_df):
    r = run_tool(sample_df, "aggregate", {"op": "sum", "value_column": "sales"})
    assert r.ok and r.value == 500 and r.used_rows == 4


def test_sales_by_region(sample_df):
    r = run_tool(sample_df, "aggregate", {"op": "sum", "value_column": "sales", "group_by": ["region"]})
    assert r.ok
    assert rows_by(r, "region", "합계(sales)") == {"부산": 250, "서울": 250}


def test_sales_by_month(sample_df):
    r = run_tool(sample_df, "aggregate", {"op": "sum", "value_column": "sales", "group_by": ["month"]})
    assert rows_by(r, "month", "합계(sales)") == {"2026-01": 300, "2026-02": 200}


def test_month_over_month(sample_df):
    r = run_tool(sample_df, "compare_periods",
                 {"date_column": "month", "op": "sum", "value_column": "sales", "current": "2026-02"})
    assert r.ok and r.value == -33.33
    row = r.rows[0]
    assert row["2026-01"] == 300 and row["2026-02"] == 200 and row["증감"] == -100


def test_declining_region(sample_df):
    """Step 3 예시: 전월 대비 변화와 감소 지역 비교."""
    r = run_tool(sample_df, "compare_periods", {"date_column": "month", "op": "sum", "value_column": "sales",
                                                "current": "2026-02", "group_by": "region"})
    assert r.ok and r.value == -33.33
    assert rows_by(r, "region", "증감률(%)") == {"부산": -75.0, "서울": 50.0}


def test_no_previous_month(sample_df):
    r = run_tool(sample_df, "compare_periods",
                 {"date_column": "month", "op": "sum", "value_column": "sales", "current": "2026-01"})
    assert not r.ok and "2025-12" in r.error
