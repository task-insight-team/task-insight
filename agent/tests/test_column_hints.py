"""컬럼 의미 해석 (기능 스위치 TI_COLUMN_HINTS, 기본 OFF, 일반 CSV만).

- OFF: 질문 용어와 이름이 맞는 컬럼이 없으면 지금처럼 LLM 호출 없이 '지원 불가'·되묻기.
- ON: 의미가 같을 수 있는 후보 컬럼이 있으면 Gemini가 고르게 하고, 고른 컬럼을 코드가 검증한다
  (존재·자료형·다른 용어와 중복·기존 별칭 충돌·계산 의도). 승인 화면에 해석을 표시한다.
  후보가 없으면(명백히 없는 정보) ON에서도 사전 차단한다.
- 기대값은 pandas로 독립 계산한다. 가상 데이터만 쓴다.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from engine import load_dataset

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.context import build_vocabulary
from agent.guard import column_hints_on, question_columns
from agent.prompts import CLARIFY_TOOL, LIMIT_TOOL
from agent.tests.helpers import AS_OF

BUYERS = ["가상상사", "나래무역", "다온유통", "라온마트"]
REGIONS = ["서울", "부산", "대구"]


def _csv(cols: dict) -> bytes:
    return pd.DataFrame(cols).to_csv(index=False).encode("utf-8-sig")


def _rows(n=24):
    return {"buyer": [BUYERS[i % 4] for i in range(n)], "region": [REGIONS[i % 3] for i in range(n)],
            "판매수량": [(i * 7) % 11 + 1 for i in range(n)], "sales": [((i * 37) % 23 + 5) * 1000 for i in range(n)]}


BUYER = _csv(_rows())
VENDOR = _csv(dict(_rows(), company=[["갑", "을"][i % 2] for i in range(24)]))
KOREAN = _csv({"거래처명": [BUYERS[i % 4] for i in range(24)], "매출액": [(i % 9 + 1) * 500 for i in range(24)]})
NO_CUSTOMER = _csv({"region": [REGIONS[i % 3] for i in range(24)], "product": [["A", "B"][i % 2] for i in range(24)],
                    "sales": [(i % 5 + 1) * 100 for i in range(24)]})
STAFF = _csv({"직원": [f"직원{i % 6}" for i in range(24)], "부서": [["생산", "품질"][i % 2] for i in range(24)],
              "근속연수": [i % 10 + 1 for i in range(24)], "평가점수": [60 + i % 40 for i in range(24)]})


def ds_of(data: bytes, name="data.csv"):
    res = load_dataset({name: data})
    assert res.ok and res.dataset.kind == "generic"
    return res.dataset


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("TI_COLUMN_HINTS", "1")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv("TI_COLUMN_HINTS", raising=False)


def plan(data, question, *replies):
    llm = FakeLLM(*replies)
    return create_plan(question, ds_of(data), AS_OF, llm), llm


def calls(*pairs):
    return FakeLLM.calls(*pairs)


SUM_BY_BUYER = ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["buyer"]})
pandas_buyer = pd.read_csv(io.BytesIO(BUYER), encoding="utf-8-sig").groupby("buyer")["sales"].sum().to_dict()


# ---------------------------------------------------------------------------
# 기능 스위치
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value, want", [("1", True), ("true", True), ("ON", True), (" yes ", True),
                                         ("0", False), ("false", False), ("", False), ("켜기", False)])
def test_switch_values(monkeypatch, value, want):
    monkeypatch.setenv("TI_COLUMN_HINTS", value)
    assert column_hints_on() is want


def test_switch_defaults_off_without_env_or_secrets(off):
    assert column_hints_on() is False  # 테스트 환경에는 secrets 파일이 없음 → 예외 → OFF


def test_switch_reads_streamlit_secrets(off, monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, "secrets", {"TI_COLUMN_HINTS": True})  # secrets.toml: TI_COLUMN_HINTS = true
    assert column_hints_on() is True
    monkeypatch.setattr(st, "secrets", {"TI_COLUMN_HINTS": "off"})
    assert column_hints_on() is False


def test_env_wins_over_secrets(monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, "secrets", {"TI_COLUMN_HINTS": True})
    monkeypatch.setenv("TI_COLUMN_HINTS", "0")
    assert column_hints_on() is False


# ---------------------------------------------------------------------------
# OFF: 기존 동작 그대로 (LLM 호출 없음)
# ---------------------------------------------------------------------------
def test_off_customer_without_alias_is_unsupported_without_llm(off):
    resp, llm = plan(BUYER, "고객별 매출 합계는?", calls(SUM_BY_BUYER))
    assert resp.status == "unsupported" and "고객" in resp.message and llm.prompts == []


def test_off_unknown_group_word_asks_back_without_llm(off):
    resp, llm = plan(BUYER, "바이어별 매출 합계는?", calls(SUM_BY_BUYER))
    assert resp.status == "clarify" and llm.prompts == []


# ---------------------------------------------------------------------------
# ON: 허용해야 할 질문 (pandas 대조, 승인 화면에 해석 표시)
# ---------------------------------------------------------------------------
def test_on_customer_maps_to_buyer(on):
    resp, llm = plan(BUYER, "고객별 매출 합계는?", calls(SUM_BY_BUYER))
    assert resp.status == "plan", resp.message
    assert "'buyer' 컬럼(묶음 기준)으로 해석" in resp.plan.summary and "[취소]" in resp.plan.summary
    assert "[컬럼 해석 안내]" in llm.prompts[0]["user"] and "buyer" in llm.prompts[0]["user"]
    out = execute_plan(ds_of(BUYER), resp.plan, approve_plan(resp.plan))
    rows = out.steps[0].result.rows
    assert {r["buyer"]: r[list(r)[-1]] for r in rows} == pandas_buyer


def test_on_unknown_group_word_maps_to_buyer(on):
    resp, _ = plan(BUYER, "바이어별 매출 합계는?", calls(SUM_BY_BUYER))
    assert resp.status == "plan", resp.message
    assert "'바이어'을(를) 'buyer' 컬럼(묶음 기준)으로 해석" in resp.plan.summary


@pytest.mark.parametrize("switch", ["1", "0"])
def test_existing_alias_still_works_without_notice(monkeypatch, switch):
    """'거래처'는 원래 '고객' 별칭 → ON·OFF 모두 그대로 허용, 해석 안내 없음."""
    monkeypatch.setenv("TI_COLUMN_HINTS", switch)
    resp, llm = plan(KOREAN, "고객별 매출은?", calls(("aggregate", {"op": "sum", "value_column": "매출액",
                                                                     "group_by": ["거래처명"]})))
    assert resp.status == "plan", resp.message
    assert "해석했습니다" not in resp.plan.summary and "[컬럼 해석 안내]" not in llm.prompts[0]["user"]


def test_on_two_candidates_shows_alternative(on):
    resp, _ = plan(VENDOR, "고객별 매출 합계는?", calls(SUM_BY_BUYER))
    assert resp.status == "plan", resp.message
    assert "다른 후보: company" in resp.plan.summary


def test_on_two_candidates_gemini_may_ask_back(on):
    resp, _ = plan(VENDOR, "고객별 매출 합계는?", calls((CLARIFY_TOOL, {"question": "buyer와 company 중 어느 것인가요?"})))
    assert resp.status == "clarify" and resp.plan is None


# ---------------------------------------------------------------------------
# ON: 거절·되묻기해야 할 질문
# ---------------------------------------------------------------------------
def test_on_no_candidate_customer_is_still_blocked_without_llm(on):
    """region은 '지역', product는 '상품' 별칭 → '고객' 후보가 없음 → 명백히 없는 정보."""
    resp, llm = plan(NO_CUSTOMER, "고객별 매출은?", calls(("aggregate", {"op": "sum", "value_column": "sales",
                                                                         "group_by": ["region"]})))
    assert resp.status == "unsupported" and llm.prompts == []


@pytest.mark.parametrize("other", ["store", "vendor", "brand", "매장명", "담당자"])
def test_on_customer_is_not_mapped_to_seller_side_column(on, other):
    """실제 Gemini가 '고객'을 store(매장)로 해석한 사례 → 고객이 아닌 대상(매장·공급업체·브랜드·담당자)은 후보에서 뺀다."""
    data = _csv({other: [["가", "나", "다"][i % 3] for i in range(24)], "sales": [(i % 5 + 1) * 100 for i in range(24)]})
    resp, llm = plan(data, "고객별 매출은?", calls(("aggregate", {"op": "sum", "value_column": "sales", "group_by": [other]})))
    assert resp.status != "plan" and llm.prompts == []


def test_on_hint_tells_gemini_what_the_term_means(on):
    _, llm = plan(BUYER, "고객별 매출 합계는?", calls(SUM_BY_BUYER))
    assert "매장·지점·공급업체·브랜드·담당자는 고객이 아니다" in llm.prompts[0]["user"]


def test_on_missing_salary_is_still_blocked_without_llm(on):
    """근속연수·평가점수는 연봉으로 해석하지 않는다 (자료형은 숫자지만 이름이 다른 지표)."""
    resp, llm = plan(STAFF, "직원별 연봉은?", calls(("aggregate", {"op": "sum", "value_column": "근속연수",
                                                                    "group_by": ["직원"]})))
    assert resp.status == "unsupported" and "연봉" in resp.message and llm.prompts == []


def test_on_candidates_exclude_other_dimensions_and_aliases(on):
    vocab = build_vocabulary(ds_of(VENDOR), AS_OF)
    qc = question_columns("고객별 매출 합계는?", vocab)
    assert [t.candidates for t in qc.open_terms] == [["buyer", "company"]]  # region(지역)·판매수량·sales 제외


WRONG_PLANS = [
    ("다른 그룹 기준", ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["region"]})),
    ("그룹 기준 빠짐", ("aggregate", {"op": "sum", "value_column": "sales"})),
    ("다른 지표", ("aggregate", {"op": "sum", "value_column": "판매수량", "group_by": ["buyer"]})),
    ("후보 둘을 함께", ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["buyer", "company"]})),
    ("무단 곱셈", ("aggregate", {"op": "sum", "value_column": "sales", "multiply_by": "판매수량", "group_by": ["buyer"]})),
    ("무단 비율", ("calculate_ratio", {"kind": "share", "value_column": "sales", "group_by": ["buyer"]})),
]


@pytest.mark.parametrize("why, call", WRONG_PLANS, ids=[w for w, _ in WRONG_PLANS])
def test_on_wrong_plans_are_not_executable(on, why, call):
    """잘못된 계획은 실행 가능한 계획으로 나오지 않는다 (고칠 수 있는 묶음 문제는 1회 재계획, 그래도 같으면 거절)."""
    if call[1].get("multiply_by") and "multiply_by" not in __import__("contracts").AggregateParams.model_fields:
        pytest.skip("엔진에 multiply_by 없음")
    resp, llm = plan(VENDOR, "고객별 매출 합계는?", calls(call), calls(call))
    assert resp.status != "plan", f"{why}: {resp.plan.summary if resp.plan else ''}"
    assert len(llm.prompts) <= 2  # 재계획은 많아야 1회


def test_on_wrong_group_is_fixed_on_retry(on):
    wrong = ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["region"]})
    resp, llm = plan(BUYER, "고객별 매출 합계는?", calls(wrong), calls(SUM_BY_BUYER))
    assert resp.status == "plan" and resp.plan.steps[0].params["group_by"] == ["buyer"]
    assert "질문의 묶음 기준(고객별)" in llm.prompts[1]["user"]


def test_on_unsupported_operation_gemini_limitation_is_unsupported(on):
    resp, _ = plan(BUYER, "고객별 매출 표준편차는?", calls((LIMIT_TOOL, {"reason": "표준편차 도구가 없습니다."})))
    assert resp.status == "unsupported" and resp.plan is None


def test_on_tampering_after_approval_is_refused(on):
    resp, _ = plan(BUYER, "고객별 매출 합계는?", calls(SUM_BY_BUYER))
    token = approve_plan(resp.plan)
    tampered = resp.plan.model_copy(deep=True)
    tampered.steps[0].params["group_by"] = ["region"]
    out = execute_plan(ds_of(BUYER), tampered, token)
    assert out.status != "ok"


def test_on_standard_data_unchanged(on):
    """표준(kafe) 데이터는 스위치와 무관하게 의미 해석 후보를 만들지 않는다."""
    from agent.tests.helpers import kafe_dataset
    vocab = build_vocabulary(kafe_dataset(), AS_OF)
    qc = question_columns("고객별 매출은?", vocab)
    assert qc.open_terms == []
