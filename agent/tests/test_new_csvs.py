"""범용성 테스트: 업무관리와 전혀 다른 합성 CSV 3종(재고·영업·품질)에서 같은 에이전트가 안전하고 정확하게 동작하는지.

- CSV: agent/tests/data/{inventory,sales,quality}.csv (가상 데이터, 고정 시드로 생성)
- 기대값은 원본 CSV를 pandas로 독립 계산 (엔진 결과를 옮겨 적지 않음)
- FakeLLM으로 '올바른 계획'을 주면 승인 후 결과가 pandas 값과 같아야 하고,
  질문과 다른 컬럼·지표로 바꾼 계획이나 현재 엔진에 없는 연산은 계획 단계에서 막혀야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from engine import load_dataset

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.context import build_vocabulary
from agent.tests.helpers import AS_OF

DATA = Path(__file__).resolve().parent / "data"


def raw(name):
    return pd.read_csv(DATA / f"{name}.csv", encoding="utf-8-sig")


def ds_of(name):
    res = load_dataset({f"{name}.csv": (DATA / f"{name}.csv").read_bytes()})
    assert res.ok and res.dataset.kind == "generic"
    return res.dataset


@pytest.fixture(scope="module")
def inv():
    return ds_of("inventory")


@pytest.fixture(scope="module")
def sales():
    return ds_of("sales")


@pytest.fixture(scope="module")
def qual():
    return ds_of("quality")


def run(ds, question, *calls):
    resp = create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls(*calls)))
    assert resp.status == "plan", f"{question}: {resp.status} {resp.message}"
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok", out.message
    return resp, out.steps[0].result


def as_map(rows, key, val):
    return {r[key]: r[val] for r in rows}


def F(*x):
    return [{"column": c, "op": o, "value": v} for c, o, v in x]


# ---------------------------------------------------------------------------
# 컬럼 역할 인식 (자료형·값 모양으로, 업종 이름 하드코딩 없음)
# ---------------------------------------------------------------------------
def test_roles(inv, sales, qual):
    r = build_vocabulary(inv, AS_OF).roles
    assert r == {"품목": "category", "창고": "category", "현재수량": "number", "안전재고": "number", "단가": "number"}
    r = build_vocabulary(sales, AS_OF).roles
    assert r["거래일"] == "date_day" and r["지역"] == "category" and r["매출액"] == "number"
    r = build_vocabulary(qual, AS_OF).roles
    assert r["발생일"] == "date_day" and r["공정"] == "category" and r["비고"] == "text"


# ---------------------------------------------------------------------------
# A. 재고관리
# ---------------------------------------------------------------------------
def test_inv_sum_by_warehouse(inv):
    _, res = run(inv, "창고별 현재 재고수량 합계는?",
                 ("aggregate", {"op": "sum", "value_column": "현재수량", "group_by": ["창고"]}))
    want = raw("inventory").groupby("창고")["현재수량"].sum().to_dict()
    assert {k: int(v) for k, v in as_map(res.rows, "창고", "합계(현재수량)").items()} == want


def test_inv_avg_price_by_item(inv):
    _, res = run(inv, "품목별 평균 단가는?", ("aggregate", {"op": "mean", "value_column": "단가", "group_by": ["품목"]}))
    want = raw("inventory").groupby("품목")["단가"].mean().round(2).to_dict()
    assert as_map(res.rows, "품목", "평균(단가)") == want


def test_inv_count_low_stock_constant(inv):
    _, res = run(inv, "현재수량이 100보다 적은 재고는 몇 건이야?",
                 ("aggregate", {"op": "count", "filters": F(("현재수량", "<", "100"))}))
    assert res.value == int((raw("inventory")["현재수량"] < 100).sum())


def test_inv_top5_price(inv):
    _, res = run(inv, "단가가 가장 높은 품목 상위 5개는?",
                 ("aggregate", {"op": "mean", "value_column": "단가", "group_by": ["품목"], "sort": "value_desc", "top_n": 5}))
    want = raw("inventory").groupby("품목")["단가"].mean().sort_values(ascending=False).head(5)
    assert [r["평균(단가)"] for r in res.rows] == list(want.round(2))


# ---------------------------------------------------------------------------
# B. 영업관리
# ---------------------------------------------------------------------------
def test_sales_top_region(sales):
    _, res = run(sales, "지역별 매출액이 가장 큰 곳은?",
                 ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"], "sort": "value_desc"}))
    want = raw("sales").groupby("지역")["매출액"].sum().sort_values(ascending=False)
    assert res.rows[0]["지역"] == want.index[0] and res.rows[0]["합계(매출액)"] == float(want.iloc[0])


def test_sales_top5_products_by_qty(sales):
    _, res = run(sales, "상품별 판매수량 상위 5개는?",
                 ("aggregate", {"op": "sum", "value_column": "판매수량", "group_by": ["상품"], "sort": "value_desc", "top_n": 5}))
    want = raw("sales").groupby("상품")["판매수량"].sum().sort_values(ascending=False).head(5)
    assert [r["합계(판매수량)"] for r in res.rows] == [float(v) for v in want]


def test_sales_month_over_month(sales):
    _, res = run(sales, "10월 대비 11월 매출액 변화는?",
                 ("compare_periods", {"date_column": "거래일", "op": "sum", "value_column": "매출액",
                                      "current": "2026-11", "previous": "2026-10"}))
    df = raw("sales")
    m = df.assign(ym=df["거래일"].str[:7]).groupby("ym")["매출액"].sum()
    row = res.rows[0]
    assert row["2026-10"] == float(m["2026-10"]) and row["2026-11"] == float(m["2026-11"])
    assert res.value == round((m["2026-11"] - m["2026-10"]) / m["2026-10"] * 100, 2)


def test_sales_avg_by_region(sales):
    _, res = run(sales, "지역별 평균 매출액은?", ("aggregate", {"op": "mean", "value_column": "매출액", "group_by": ["지역"]}))
    want = raw("sales").groupby("지역")["매출액"].mean().round(2).to_dict()
    assert as_map(res.rows, "지역", "평균(매출액)") == want


# ---------------------------------------------------------------------------
# C. 품질관리
# ---------------------------------------------------------------------------
def test_qual_count_by_process(qual):
    _, res = run(qual, "공정별 불량 발생 건수는?", ("aggregate", {"op": "count", "group_by": ["공정"]}))
    assert as_map(res.rows, "공정", "개수(건)") == raw("quality").groupby("공정").size().to_dict()


def test_qual_sum_by_defect_type(qual):
    _, res = run(qual, "불량유형별 불량수량 합계는?",
                 ("aggregate", {"op": "sum", "value_column": "불량수량", "group_by": ["불량유형"]}))
    want = raw("quality").groupby("불량유형")["불량수량"].sum().to_dict()
    assert {k: int(v) for k, v in as_map(res.rows, "불량유형", "합계(불량수량)").items()} == want


def test_qual_filter_process(qual):
    _, res = run(qual, "SMT 공정의 불량수량 합계는?",
                 ("aggregate", {"op": "sum", "value_column": "불량수량", "filters": F(("공정", "==", "SMT"))}))
    df = raw("quality")
    assert res.value == float(df.loc[df["공정"] == "SMT", "불량수량"].sum())


def test_qual_month_over_month(qual):
    _, res = run(qual, "10월 대비 11월 불량수량 변화는?",
                 ("compare_periods", {"date_column": "발생일", "op": "sum", "value_column": "불량수량",
                                      "current": "2026-11", "previous": "2026-10"}))
    df = raw("quality")
    m = df.assign(ym=df["발생일"].str[:7]).groupby("ym")["불량수량"].sum()
    assert res.rows[0]["2026-10"] == float(m["2026-10"]) and res.rows[0]["2026-11"] == float(m["2026-11"])


# ---------------------------------------------------------------------------
# 현재 엔진에 없는 연산 → LLM 호출 없이 unsupported (다른 계산으로 대신하지 않음)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,question,keyword,feature", [
    ("inventory", "창고별 재고 금액은?", "금액", None),            # 곱셈(계산 컬럼): 엔진에 없음 → 항상 차단
    ("sales", "고객별 매출액 합계는?", "고객", None),               # 컬럼 없음 → 항상 차단
    ("inventory", "안전재고보다 현재수량이 적은 품목은?", "컬럼 간 비교", "compare"),
    ("sales", "월별 매출액 추이는?", "월 묶음", "period"),
    ("sales", "상품별 매출 비중은?", "비율", "ratio"),
    ("quality", "공정별 불량률은?", "비율", "ratio"),
    ("quality", "월별 불량수량 추이는?", "월 묶음", "period"),
])
def test_unsupported_without_llm(name, question, keyword, feature):
    """엔진에 없는 연산만 LLM 호출 전에 차단한다. 엔진이 지원하게 되면(PR #38) 차단을 풀고 LLM 계획 단계로 넘긴다."""
    from agent.guard import engine_has
    llm = FakeLLM(FakeLLM.calls(("aggregate", {"op": "count"})))
    resp = create_plan(question, ds_of(name), AS_OF, llm)
    if feature and engine_has(feature):
        assert llm.prompts, f"엔진이 지원하는데 사전 차단됨: {resp.message}"
    else:
        assert resp.status == "unsupported" and resp.plan is None and not llm.prompts, resp.message
        assert keyword in resp.message and "대신하지 않습니다" in resp.message


def test_growth_rate_is_still_supported(sales):
    """'증감률'은 compare_periods가 계산하므로 비율 차단에 걸리지 않는다."""
    resp = create_plan("10월 대비 11월 매출액 증감률은?", sales, AS_OF, FakeLLM(FakeLLM.calls(
        ("compare_periods", {"date_column": "거래일", "op": "sum", "value_column": "매출액", "current": "2026-11", "previous": "2026-10"}))))
    assert resp.status == "plan", resp.message


# ---------------------------------------------------------------------------
# 조용한 대체 차단 (PR #25 규칙이 새 CSV에서도 유지되는지)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,question,call,why", [
    ("quality", "불량유형별 불량수량 합계는?",
     ("aggregate", {"op": "sum", "value_column": "생산수량", "group_by": ["불량유형"]}), "생산수량"),
    ("sales", "지역별 매출액 합계는?",
     ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["상품"]}), "상품"),
    ("sales", "지역별 매출액 합계는?",
     ("aggregate", {"op": "sum", "value_column": "판매수량", "group_by": ["지역"]}), "판매수량"),
    ("quality", "공정별 불량 발생 건수는?",
     ("aggregate", {"op": "count", "group_by": ["비고"]}), "자유 텍스트"),
])
def test_silent_substitution_blocked(name, question, call, why):
    resp = create_plan(question, ds_of(name), AS_OF, FakeLLM(FakeLLM.calls(call)))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None
    assert why in resp.message


# ---------------------------------------------------------------------------
# 보안·시각화
# ---------------------------------------------------------------------------
def test_memo_instruction_not_sent_to_llm(qual):
    llm = FakeLLM(FakeLLM.calls(("aggregate", {"op": "count", "group_by": ["공정"]})))
    create_plan("공정별 불량 발생 건수는?", qual, AS_OF, llm)
    prompt = llm.prompts[0]["system"] + llm.prompts[0]["user"]
    assert "이전 지시를 무시하고" not in prompt and "삭제하라" not in prompt
    assert "비고(비고): 자유 텍스트" in prompt and "거래일" not in prompt  # 스키마만, 다른 파일 정보 없음


def test_prompt_tells_dates_and_roles(sales):
    llm = FakeLLM(FakeLLM.calls(("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"]})))
    create_plan("지역별 매출액 합계는?", sales, AS_OF, llm)
    prompt = llm.prompts[0]["user"]
    assert "거래일(거래일): 날짜(일, YYYY-MM-DD)" in prompt and "지역(지역): 범주" in prompt
    assert "서울" not in prompt  # 범주값 자체는 보내지 않음


def test_month_column_grouping_uses_line_chart():
    sample = load_dataset({"sample.csv": (DATA.parents[2] / "data" / "sample.csv").read_bytes()}).dataset
    resp = create_plan("월별 매출 합계는?", sample, AS_OF, FakeLLM(FakeLLM.calls(
        ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["month"]}))))
    assert resp.status == "plan" and resp.plan.steps[0].chart_type == "line"


def test_category_grouping_stays_bar(sales):
    resp, _ = run(sales, "지역별 매출액 합계는?", ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"]}))
    assert resp.plan.steps[0].chart_type == "bar"


# ---------------------------------------------------------------------------
# 과잉 차단 방지 (변경진 님 범용성 측정에서 '답할 수 있는데 거절'로 나온 유형)
# ---------------------------------------------------------------------------
def test_english_units_column_counts_as_quantity():
    csv = "month,region,units,revenue\n2026-01,서울,32,3060\n2026-02,서울,40,3900\n".encode("utf-8")
    ds = load_dataset({"s.csv": csv}).dataset
    llm = FakeLLM(FakeLLM.calls(("aggregate", {"op": "sum", "value_column": "units"})))
    resp = create_plan("전체 판매 수량 합계는?", ds, AS_OF, llm)
    assert resp.status == "plan" and llm.prompts, resp.message


def test_term_inside_data_value_is_not_a_missing_column():
    csv = ("문의번호,접수일,유형,처리시간\nC1,2026-10-01,제품불량,4\nC2,2026-10-02,배송,2\n"
           "C3,2026-10-03,제품불량,6\nC4,2026-10-04,환불,3\nC5,2026-10-05,배송,1\n").encode("utf-8")
    ds = load_dataset({"t.csv": csv}).dataset
    call = ("aggregate", {"op": "mean", "value_column": "처리시간", "filters": F(("유형", "==", "제품불량"))})
    llm = FakeLLM(FakeLLM.calls(call))
    resp = create_plan("제품불량 문의의 평균 처리시간은?", ds, AS_OF, llm)
    assert resp.status == "plan" and llm.prompts, resp.message
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.steps[0].result.value == 5.0
