"""엔진 PR #38(월별 묶음·비율 도구·컬럼끼리 비교) 연동 테스트 (FakeLLM, 실제 API 미사용).

- 엔진에 해당 기능이 없으면(#38 머지 전) 모듈 전체를 건너뛴다 → 머지 순서와 무관.
- 기대값은 원본 CSV를 pandas로 독립 계산한다.
- 핵심: 질문과 다른 계산(합계↔비율, 잘못된 분자·분모, 빠진 비교, 월별 누락·과잉)은 승인 대상 계획이 되지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from engine import load_dataset

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.guard import engine_has
from agent.tests.helpers import AS_OF

pytestmark = pytest.mark.skipif(not (engine_has("ratio") and engine_has("period") and engine_has("compare")),
                                reason="엔진에 월별 묶음·비율·컬럼 비교가 아직 없음 (PR #38 머지 전)")

DATA = Path(__file__).resolve().parent / "data"


def raw(n):
    return pd.read_csv(DATA / f"{n}.csv", encoding="utf-8-sig")


def ds_of(n):
    return load_dataset({f"{n}.csv": (DATA / f"{n}.csv").read_bytes()}).dataset


def ds_bytes(name, text):
    res = load_dataset({name: text.encode("utf-8")})
    assert res.ok
    return res.dataset


def plan(ds, question, call, retry=True):
    calls = [FakeLLM.calls(call)] + ([FakeLLM.calls(call)] if retry else [])
    return create_plan(question, ds, AS_OF, FakeLLM(*calls))


def run(ds, question, call):
    resp = plan(ds, question, call)
    assert resp.status == "plan", f"{question}: {resp.status} {resp.message}"
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok", out.message
    return resp, out.steps[0]


MONTH_SALES = ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["거래일"], "period": "month"})
DEFECT_RATE = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "생산수량", "group_by": ["공정"]})
BELOW_SAFETY = ("aggregate", {"op": "count", "group_by": ["품목"],
                              "filters": [{"column": "현재수량", "op": "<", "compare_column": "안전재고"}]})
SALES_SHARE = ("calculate_ratio", {"kind": "share", "value_column": "매출액", "group_by": ["상품"]})


# ---------------------------------------------------------------------------
# 반드시 검증할 질문 4개: pandas와 비교 + 승인 화면 문구 + 차트 종류
# ---------------------------------------------------------------------------
def test_monthly_sales_trend():
    resp, step = run(ds_of("sales"), "월별 매출액 추이는?", MONTH_SALES)
    s = raw("sales")
    want = s.assign(ym=s["거래일"].str[:7]).groupby("ym")["매출액"].sum()
    got = {r["거래일(월)"]: r["합계(매출액)"] for r in step.result.rows}
    assert got == {k: float(v) for k, v in want.items()}
    assert "월별 집계" in step.purpose and "거래일" in step.purpose and step.chart_type == "line"


def test_defect_rate_by_process():
    resp, step = run(ds_of("quality"), "공정별 불량률은?", DEFECT_RATE)
    q = raw("quality")
    g = q.groupby("공정")[["불량수량", "생산수량"]].sum()
    want = (g["불량수량"] / g["생산수량"] * 100).round(2).to_dict()
    assert {r["공정"]: r["비율(%)"] for r in step.result.rows} == want
    assert step.result.value == round(q["불량수량"].sum() / q["생산수량"].sum() * 100, 2)
    assert "불량수량 합계 ÷ 생산수량 합계" in step.purpose and step.chart_type == "bar"
    assert any("분모" in n and "생산수량" in n for n in resp.notices)  # 분모가 질문에 없어 승인 전 명시
    hi = max(step.result.rows, key=lambda r: r["비율(%)"])
    assert f"{hi['공정']}이(가) {hi['비율(%)']}%" in step.explanation.replace(".0%", "%")


def test_below_safety_stock():
    resp, step = run(ds_of("inventory"), "안전재고보다 현재수량이 적은 품목은?", BELOW_SAFETY)
    i = raw("inventory")
    want = i[i["현재수량"] < i["안전재고"]].groupby("품목").size().to_dict()
    assert {r["품목"]: r["개수(건)"] for r in step.result.rows} == want
    assert "현재수량 < 안전재고" in step.purpose and step.chart_type == "bar"


def test_sales_share_by_product():
    resp, step = run(ds_of("sales"), "상품별 매출 비중은?", SALES_SHARE)
    s = raw("sales")
    want = (s.groupby("상품")["매출액"].sum() / s["매출액"].sum() * 100).round(2).to_dict()
    assert {r["상품"]: r["비중(%)"] for r in step.result.rows} == want
    assert "매출액 비중" in step.purpose and step.chart_type == "bar"
    assert "100%와 조금 다를 수" in step.explanation  # 반올림 합 99.99% 등을 100%라고 말하지 않음


# ---------------------------------------------------------------------------
# 질문과 계산 의도 불일치 → 승인 대상 계획이 되지 않음
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,question,call,why", [
    # 합계 질문에 비율 도구 (보고된 핵심 사례)
    ("quality", "공정별 불량수량 합계는?",
     ("calculate_ratio", {"kind": "share", "value_column": "불량수량", "group_by": ["공정"]}), "비율·비중을 묻지 않았는데"),
    ("sales", "지역별 매출액 합계는?",
     ("calculate_ratio", {"kind": "share", "value_column": "매출액", "group_by": ["지역"]}), "비율·비중을 묻지 않았는데"),
    # 비율 질문에 합계 도구
    ("quality", "공정별 불량률은?",
     ("aggregate", {"op": "sum", "value_column": "불량수량", "group_by": ["공정"]}), "합계·개수·평균 계산"),
    ("sales", "상품별 매출 비중은?",
     ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["상품"]}), "합계·개수·평균 계산"),
    # 잘못된 분자·분모, 잘못된 비율 종류
    ("quality", "공정별 불량률은?",
     ("calculate_ratio", {"kind": "column_ratio", "value_column": "생산수량", "denominator_column": "불량수량", "group_by": ["공정"]}), "분자"),
    ("quality", "공정별 불량률은?",
     ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "불량수량", "group_by": ["공정"]}), "분자와 분모가 같은"),
    ("quality", "공정별 불량률은?",
     ("calculate_ratio", {"kind": "share", "value_column": "불량수량", "group_by": ["공정"]}), "전체 대비 비중"),
    ("sales", "상품별 매출 비중은?",
     ("calculate_ratio", {"kind": "column_ratio", "value_column": "매출액", "denominator_column": "판매수량", "group_by": ["상품"]}), "비중을 물었는데"),
    # 월별 누락·과잉
    ("sales", "월별 매출액 추이는?",
     ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["거래일"]}), "일 단위 날짜로 묶었습니다"),
    ("sales", "지역별 매출액 합계는?",
     ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"], "period": "month"}), "월별 집계를 요청하지 않았는데"),
    # 날짜 컬럼 오류 (월별인데 날짜가 아닌 컬럼으로 묶음)
    ("sales", "월별 매출액 추이는?",
     ("aggregate", {"op": "sum", "value_column": "매출액", "group_by": ["지역"], "period": "month"}), "날짜 컬럼"),
    # 컬럼 비교 누락·대상 틀림
    ("inventory", "안전재고보다 현재수량이 적은 품목은?",
     ("aggregate", {"op": "count", "group_by": ["품목"]}), "컬럼 비교"),
    ("inventory", "안전재고보다 현재수량이 적은 품목은?",
     ("aggregate", {"op": "count", "group_by": ["품목"], "filters": [{"column": "현재수량", "op": "<", "compare_column": "단가"}]}), "비교 대상과 다릅니다"),
    # 없는 컬럼과 비교
    ("inventory", "안전재고보다 현재수량이 적은 품목은?",
     ("aggregate", {"op": "count", "filters": [{"column": "현재수량", "op": "<", "compare_column": "최소재고"}]}), "최소재고"),
])
def test_intent_mismatch_blocked(name, question, call, why):
    resp = plan(ds_of(name), question, call)
    assert resp.status in ("clarify", "unsupported") and resp.plan is None, resp.message
    assert why in resp.message, resp.message


def test_condition_rate_condition_must_be_in_question():
    csv = "팀,처리상태,처리시간\nA,처리완료,3\nA,진행중,5\nB,처리완료,2\nB,처리완료,4\nC,보류,1\n"
    ds = ds_bytes("t.csv", csv)
    good = ("calculate_ratio", {"kind": "condition_rate", "group_by": ["팀"], "condition": [{"column": "처리상태", "op": "==", "value": "처리완료"}]})
    resp, step = run(ds, "팀별 처리완료 비율은?", good)
    assert {r["팀"]: r["비율(%)"] for r in step.result.rows} == {"A": 50.0, "B": 100.0, "C": 0.0}
    bad = ("calculate_ratio", {"kind": "condition_rate", "group_by": ["팀"], "condition": [{"column": "처리상태", "op": "==", "value": "보류"}]})
    resp = plan(ds, "팀별 처리완료 비율은?", bad)
    assert resp.status in ("clarify", "unsupported") and "보류" in resp.message


def test_ambiguous_denominator_is_asked_back():
    """분모가 질문에 없고 후보가 여럿이면 추측하지 않는다."""
    csv = "공정,불량수량,생산수량,투입수량\nA,2,100,120\nB,3,200,210\n"
    ds = ds_bytes("amb.csv", csv)
    call = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "생산수량", "group_by": ["공정"]})
    resp = plan(ds, "공정별 불량률은?", call)
    assert resp.status == "clarify" and "분모" in resp.message and "투입수량" in resp.message


def test_explicit_denominator_must_match():
    csv = "공정,불량수량,생산수량,투입수량\nA,2,100,120\nB,3,200,210\n"
    ds = ds_bytes("exp.csv", csv)
    ok = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "투입수량", "group_by": ["공정"]})
    resp = plan(ds, "투입수량 대비 불량수량 비율을 공정별로 알려줘", ok)
    assert resp.status == "plan" and not any("분모를 질문에서" in n for n in resp.notices)
    wrong = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "생산수량", "group_by": ["공정"]})
    resp = plan(ds, "투입수량 대비 불량수량 비율을 공정별로 알려줘", wrong)
    assert resp.status in ("clarify", "unsupported") and "분모" in resp.message


# ---------------------------------------------------------------------------
# 경계 상황: 분모 0, 연도가 다른 같은 달
# ---------------------------------------------------------------------------
def test_zero_denominator_group_and_total():
    ds = ds_bytes("z.csv", "공정,불량수량,생산수량\nA,1,0\nB,2,10\n")
    resp, step = run(ds, "공정별 불량률은?", DEFECT_RATE)
    assert {r["공정"]: r["비율(%)"] for r in step.result.rows} == {"A": None, "B": 20.0}
    assert "분모가 0인 1개 그룹은 계산하지 않았습니다" in step.explanation
    ds = ds_bytes("z2.csv", "공정,불량수량,생산수량\nA,1,0\nB,2,0\n")
    resp = plan(ds, "공정별 불량률은?", DEFECT_RATE)
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "error" and not out.steps[0].result.rows and "계산할 수 없습니다" in out.steps[0].explanation


def test_month_grouping_keeps_years_apart():
    ds = ds_bytes("y.csv", "거래일,지역,매출액\n2025-11-03,서울,100\n2025-11-20,부산,50\n2026-11-02,서울,7\n2026-10-05,서울,3\n")
    resp, step = run(ds, "월별 매출액 추이는?", MONTH_SALES)
    assert {r["거래일(월)"]: r["합계(매출액)"] for r in step.result.rows} == {"2025-11": 150.0, "2026-10": 3.0, "2026-11": 7.0}


# ---------------------------------------------------------------------------
# 회귀: 곱셈은 계속 차단, 합계 질문은 그대로, 승인 지문 유지
# ---------------------------------------------------------------------------
def test_multiplication_still_unsupported():
    llm = FakeLLM()
    resp = create_plan("창고별 재고 금액은?", ds_of("inventory"), AS_OF, llm)
    assert resp.status == "unsupported" and not llm.prompts


def test_sum_question_still_works():
    resp, step = run(ds_of("quality"), "공정별 불량수량 합계는?",
                     ("aggregate", {"op": "sum", "value_column": "불량수량", "group_by": ["공정"]}))
    assert {r["공정"]: int(r["합계(불량수량)"]) for r in step.result.rows} == raw("quality").groupby("공정")["불량수량"].sum().to_dict()


def test_changed_ratio_plan_needs_reapproval():
    resp = plan(ds_of("quality"), "공정별 불량률은?", DEFECT_RATE)
    approval = approve_plan(resp.plan)
    changed = resp.plan.model_copy(deep=True)
    changed.steps[0].params["denominator_column"] = "불량수량"
    out = execute_plan(ds_of("quality"), changed, approval)
    assert out.status == "error" and "다시" in out.message


def test_missing_denominator_single_candidate_is_filled_and_shown():
    """실제 Gemini에서 본 사례: column_ratio에 분모가 빠짐. 후보가 하나(생산수량)뿐이면 채우고 승인 화면에 분모를 명시."""
    incomplete = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "group_by": ["공정"]})
    llm = FakeLLM(FakeLLM.calls(incomplete))
    resp = create_plan("공정별 불량률은?", ds_of("quality"), AS_OF, llm)
    assert resp.status == "plan" and len(llm.prompts) == 1
    assert resp.plan.steps[0].params["denominator_column"] == "생산수량"
    assert "불량수량 합계 ÷ 생산수량 합계" in resp.plan.steps[0].purpose
    assert any("분모" in n and "생산수량" in n for n in resp.notices)


AMB_CSV = "공정,불량수량,생산수량,투입수량\nA,2,100,120\nB,3,200,210\n"


def test_missing_denominator_many_candidates_retry_then_fixed():
    ds = ds_bytes("amb2.csv", AMB_CSV)
    incomplete = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "group_by": ["공정"]})
    explicit = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "denominator_column": "생산수량", "group_by": ["공정"]})
    llm = FakeLLM(FakeLLM.calls(incomplete), FakeLLM.calls(explicit))
    resp = create_plan("생산수량 대비 불량수량 비율을 공정별로 알려줘", ds, AS_OF, llm)
    assert resp.status == "plan" and len(llm.prompts) == 2 and "분모(denominator_column)" in llm.prompts[1]["user"]


def test_missing_denominator_many_candidates_twice_is_not_approved():
    ds = ds_bytes("amb3.csv", AMB_CSV)
    incomplete = ("calculate_ratio", {"kind": "column_ratio", "value_column": "불량수량", "group_by": ["공정"]})
    resp = create_plan("공정별 불량률은?", ds, AS_OF, FakeLLM(FakeLLM.calls(incomplete), FakeLLM.calls(incomplete)))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None and "분모" in resp.message


def test_condition_rate_without_condition_rejected():
    ds = ds_bytes("t2.csv", "팀,처리상태\nA,처리완료\nB,진행중\n")
    call = ("calculate_ratio", {"kind": "condition_rate", "group_by": ["팀"]})
    resp = plan(ds, "팀별 처리완료 비율은?", call)
    assert resp.status in ("clarify", "unsupported") and "condition" in resp.message
