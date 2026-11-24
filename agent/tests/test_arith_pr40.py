"""엔진 PR #40(곱셈 multiply_by, ×100 하지 않는 나눗셈 percent=false) 연동 테스트 (FakeLLM, 실제 API 미사용).

- 엔진에 해당 기능이 없으면(#40 머지 전) 모듈 전체를 건너뛴다 → 머지 순서와 무관.
- 기대값은 원본 CSV를 pandas로 독립 계산한다.
- 핵심: 질문이 요구하지 않은 곱셈·나눗셈은 막고, 곱셈·나눗셈이 필요한 질문을 합계·평균으로 대신하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from engine import load_dataset

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.guard import engine_has
from agent.tests.helpers import AS_OF, kafe_dataset

pytestmark = pytest.mark.skipif(not (engine_has("multiply") and engine_has("percent") and engine_has("ratio")),
                                reason="엔진에 곱셈·×100 없는 나눗셈이 아직 없음 (PR #40 머지 전)")

DATA = Path(__file__).resolve().parent / "data"


def raw(n):
    return pd.read_csv(DATA / f"{n}.csv", encoding="utf-8-sig")


def ds_of(n):
    return load_dataset({f"{n}.csv": (DATA / f"{n}.csv").read_bytes()}).dataset


def ds_bytes(name, text):
    res = load_dataset({name: text.encode("utf-8")})
    assert res.ok
    return res.dataset


def plan(ds, question, call):
    return create_plan(question, ds, AS_OF, FakeLLM(FakeLLM.calls(call), FakeLLM.calls(call)))


def run(ds, question, call):
    resp = plan(ds, question, call)
    assert resp.status == "plan", f"{question}: {resp.status} {resp.message}"
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok", out.message
    return resp, out


STOCK_VALUE = ("aggregate", {"op": "sum", "value_column": "현재수량", "multiply_by": "단가", "group_by": ["창고"]})
UNIT_PRICE_REGION = ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액", "denominator_column": "판매수량",
                                         "group_by": ["지역"], "percent": False})
UNIT_PRICE_PRODUCT = ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액", "denominator_column": "판매수량",
                                          "group_by": ["상품"], "percent": False})


# ---------------------------------------------------------------------------
# 지원 질문: pandas와 비교 + 승인 화면 계산식 + 설명
# ---------------------------------------------------------------------------
def test_stock_value_by_warehouse():
    resp, out = run(ds_of("inventory"), "창고별 재고 금액은?", STOCK_VALUE)
    step = out.steps[0]
    i = raw("inventory")
    want = i.assign(v=i["현재수량"] * i["단가"]).groupby("창고")["v"].sum()
    assert {r["창고"]: r["합계(현재수량×단가)"] for r in step.result.rows} == {k: float(v) for k, v in want.items()}
    assert "SUM(현재수량 × 단가)" in step.purpose and "×100 없음" in step.purpose and step.chart_type == "bar"
    assert any("곱할 컬럼" in n and "현재수량 × 단가" in n for n in resp.notices)  # 질문에 컬럼이 없어 계산식을 확인시킴
    top = want.idxmax()
    assert top in step.explanation and f"{int(want.max()):,}" in step.explanation


def test_unit_price_by_region():
    resp, out = run(ds_of("sales"), "지역별 평균 판매단가는?", UNIT_PRICE_REGION)
    step = out.steps[0]
    s = raw("sales")
    g = s.groupby("지역")[["매출액", "판매수량"]].sum()
    want = (g["매출액"] / g["판매수량"]).round(2).to_dict()
    assert {r["지역"]: r["값(매출액÷판매수량)"] for r in step.result.rows} == want
    assert "SUM(매출액) ÷ SUM(판매수량)" in step.purpose and "×100 하지 않음" in step.purpose
    assert step.result.unit != "%"
    hi = max(want, key=want.get)
    assert hi in step.explanation and f"{want[hi]:,}" in step.explanation
    assert "%" not in step.explanation.split("근거")[0]  # 원 단위 나눗셈에 % 붙이지 않음
    assert "분모가 0이라" not in step.explanation  # 이전 버그: '모든 그룹의 분모가 0' 오설명
    assert step.result.value == round(s["매출액"].sum() / s["판매수량"].sum(), 2)


def test_unit_price_by_product():
    resp, out = run(ds_of("sales"), "상품별 판매 단가는?", UNIT_PRICE_PRODUCT)
    s = raw("sales")
    g = s.groupby("상품")[["매출액", "판매수량"]].sum()
    want = (g["매출액"] / g["판매수량"]).round(2).to_dict()
    assert {r["상품"]: r["값(매출액÷판매수량)"] for r in out.steps[0].result.rows} == want


def test_named_columns_need_no_notice_and_order_follows_question():
    resp, _ = run(ds_of("inventory"), "창고별 안전재고와 단가를 곱한 금액은?",
                  ("aggregate", {"op": "sum", "value_column": "안전재고", "multiply_by": "단가", "group_by": ["창고"]}))
    assert not any("곱할 컬럼" in n for n in resp.notices)
    resp, out = run(ds_of("sales"), "지역별 판매수량 대비 매출액은?", UNIT_PRICE_REGION)  # 'A 대비 B' → B ÷ A
    assert "SUM(매출액) ÷ SUM(판매수량)" in out.steps[0].purpose


# ---------------------------------------------------------------------------
# 막아야 하는 계획: 질문과 다른 계산은 승인 대상 계획이 되지 않는다
# ---------------------------------------------------------------------------
BLOCKED = [
    # 단순 합계 질문에 곱셈이 끼어듦 (실제 위험: 승인 화면은 '현재수량 합계'인데 곱셈 실행)
    ("inventory", "창고별 현재수량 합계는?", STOCK_VALUE),
    # 곱셈이 필요한 질문을 단순 합계로 대체
    ("inventory", "창고별 재고 금액은?", ("aggregate", {"op": "sum", "value_column": "현재수량", "group_by": ["창고"]})),
    # 잘못된 곱셈 컬럼 (재고 금액인데 안전재고 × 단가)
    ("inventory", "창고별 재고 금액은?", ("aggregate", {"op": "sum", "value_column": "안전재고", "multiply_by": "단가", "group_by": ["창고"]})),
    # 개수에 곱셈
    ("inventory", "창고별 재고 금액은?", ("aggregate", {"op": "count", "value_column": "현재수량", "multiply_by": "단가", "group_by": ["창고"]})),
    # 같은 컬럼끼리 곱셈
    ("inventory", "창고별 재고 금액은?", ("aggregate", {"op": "sum", "value_column": "단가", "multiply_by": "단가", "group_by": ["창고"]})),
    # 합계 질문에 나눗셈
    ("sales", "지역별 매출액 합계는?", UNIT_PRICE_REGION),
    # 백분율 질문에 ×100 없는 나눗셈
    ("quality", "공정별 불량률은?", ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량",
                                                       "denominator_column": "생산수량", "group_by": ["공정"], "percent": False})),
    # 단가 질문을 합계·평균으로 대체
    ("sales", "지역별 평균 판매단가는?", ("aggregate", {"op": "mean", "value_column": "매출액", "group_by": ["지역"]})),
    ("sales", "지역별 평균 판매단가는?", ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"]})),
    # 단가 질문을 백분율(×100)로
    ("sales", "지역별 평균 판매단가는?", ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액",
                                                          "denominator_column": "판매수량", "group_by": ["지역"]})),
    # 분자·분모 뒤바뀜
    ("sales", "지역별 평균 판매단가는?", ("calculate_ratio", {"kind": "column_ratio", "value_column": "판매수량",
                                                          "denominator_column": "매출액", "group_by": ["지역"], "percent": False})),
    # 분모 같음
    ("sales", "지역별 평균 판매단가는?", ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액",
                                                          "denominator_column": "매출액", "group_by": ["지역"], "percent": False})),
    # percent=false를 비중에
    ("sales", "지역별 평균 판매단가는?", ("calculate_ratio", {"kind": "share", "value_column": "매출액",
                                                          "group_by": ["지역"], "percent": False})),
]


@pytest.mark.parametrize("name,question,call", BLOCKED, ids=[f"{q}|{c[1]}" for _, q, c in BLOCKED])
def test_mismatched_arith_plan_is_not_approved(name, question, call):
    resp = plan(ds_of(name), question, call)
    assert resp.status != "plan" and resp.plan is None, f"{question}: 통과하면 안 됨 → {resp.plan}"


def test_standard_data_rejects_unrequested_multiplication():
    resp = plan(kafe_dataset(), "부서별 실적 MH 합계는?",
                ("aggregate", {"table": "main", "op": "sum", "value_column": "실적MH", "multiply_by": "계획MH", "group_by": ["부서"]}))
    assert resp.status != "plan"


# ---------------------------------------------------------------------------
# 모호하면 되묻기
# ---------------------------------------------------------------------------
def test_ambiguous_multiplication_asks_back():
    ds = ds_bytes("amb_mul.csv", "창고,현재수량,입고수량,단가\nA,10,5,100\nB,20,6,200\n")
    resp = plan(ds, "창고별 재고 금액은?", ("aggregate", {"op": "sum", "value_column": "현재수량", "multiply_by": "단가", "group_by": ["창고"]}))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None and "곱할 두 컬럼" in resp.message


def test_ambiguous_division_asks_back():
    ds = ds_bytes("amb_div.csv", "지역,매출액,원가액,판매수량\nA,100,60,5\nB,200,90,8\n")
    resp = plan(ds, "지역별 판매 단가는?", ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액",
                                                         "denominator_column": "판매수량", "group_by": ["지역"], "percent": False}))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None and "분자·분모" in resp.message


# ---------------------------------------------------------------------------
# 결측치·0으로 나누기: 임의의 수치를 만들지 않는다
# ---------------------------------------------------------------------------
def test_multiplication_skips_blank_rows():
    text = "창고,현재수량,단가\nA,10,100\nA,5,\nB,,300\nB,2,50\n"
    _, out = run(ds_bytes("blank.csv", text), "창고별 재고 금액은?", STOCK_VALUE)
    df = pd.read_csv(pd.io.common.StringIO(text)).dropna()
    want = df.assign(v=df["현재수량"] * df["단가"]).groupby("창고")["v"].sum().to_dict()
    assert {r["창고"]: r["합계(현재수량×단가)"] for r in out.steps[0].result.rows} == want


def test_division_zero_denominator_group():
    ds = ds_bytes("zero.csv", "지역,매출액,판매수량\nA,100,0\nA,50,0\nB,300,4\n")
    _, out = run(ds, "지역별 판매 단가는?", UNIT_PRICE_REGION)
    step = out.steps[0]
    assert {r["지역"]: r["값(매출액÷판매수량)"] for r in step.result.rows} == {"A": None, "B": 75.0}
    assert "분모가 0인 1개 그룹" in step.explanation and "B" in step.explanation


def test_division_zero_denominator_total_makes_no_number():
    ds = ds_bytes("zero2.csv", "지역,매출액,판매수량\nA,100,0\nB,300,0\n")
    resp = plan(ds, "지역별 판매 단가는?", UNIT_PRICE_REGION)
    assert resp.status == "plan"
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "error" and not any(s.result.rows for s in out.steps)


# ---------------------------------------------------------------------------
# 승인 후 변조
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [("multiply_by", "안전재고"), ("op", "mean")])
def test_tampered_multiplication_needs_reapproval(field, value):
    ds = ds_of("inventory")
    resp = plan(ds, "창고별 재고 금액은?", STOCK_VALUE)
    approval = approve_plan(resp.plan)
    changed = resp.plan.model_copy(deep=True)
    changed.steps[0].params[field] = value
    out = execute_plan(ds, changed, approval)
    assert out.status == "error" and "다시" in out.message


@pytest.mark.parametrize("field,value", [("percent", True), ("denominator_column", "매출액")])
def test_tampered_division_needs_reapproval(field, value):
    ds = ds_of("sales")
    resp = plan(ds, "지역별 평균 판매단가는?", UNIT_PRICE_REGION)
    approval = approve_plan(resp.plan)
    changed = resp.plan.model_copy(deep=True)
    changed.steps[0].params[field] = value
    out = execute_plan(ds, changed, approval)
    assert out.status == "error" and "다시" in out.message


# ---------------------------------------------------------------------------
# 실제 Gemini에서 본 사례: '지역별 평균 판매단가'에서 group_by가 빠져 전체 값만 나옴
# ---------------------------------------------------------------------------
NO_GROUP = ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액", "denominator_column": "판매수량", "percent": False})


def test_missing_group_is_not_approved():
    resp = plan(ds_of("sales"), "지역별 평균 판매단가는?", NO_GROUP)
    assert resp.status != "plan" and "지역" in resp.message


def test_missing_group_is_fixed_on_retry():
    llm = FakeLLM(FakeLLM.calls(NO_GROUP), FakeLLM.calls(UNIT_PRICE_REGION))
    resp = create_plan("지역별 평균 판매단가는?", ds_of("sales"), AS_OF, llm)
    assert resp.status == "plan" and len(llm.prompts) == 2 and "지역" in llm.prompts[1]["user"]
    assert resp.plan.steps[0].params["group_by"] == ["지역"]
