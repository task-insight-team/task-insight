"""회귀 테스트: 질문이 요구한 집계 기준·지표와 실제 도구 인자 대조 (조용한 컬럼 대체 차단). FakeLLM, 실제 API 미사용.

배경 (2026-11-23): 일반 CSV(month, region, sales)에 "고객별 매출 합계"를 물으면, LLM이 한계를 말하지 않고
region으로 바꿔 계획하면 그대로 통과할 수 있었다. 설명 문구가 아니라 실제 인자(group_by·value_column)를 검사한다.
"""

from __future__ import annotations

import json

import pytest

from engine import load_dataset

from agent import FakeLLM, approve_plan, create_plan, execute_plan
from agent.tests.helpers import AS_OF, REPRESENTATIVE, ROOT, kafe_dataset

EVAL = ROOT / "eval"


@pytest.fixture(scope="module")
def sales():
    return load_dataset({"sample.csv": (ROOT / "data" / "sample.csv").read_bytes()}).dataset


@pytest.fixture(scope="module")
def kafe():
    return kafe_dataset()


SUM_BY_REGION = ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["region"]})
SUM_BY_MONTH = ("aggregate", {"op": "sum", "value_column": "sales", "group_by": ["month"]})
MOM = ("compare_periods", {"date_column": "month", "op": "sum", "value_column": "sales", "current": "2026-02"})


# ---------------------------------------------------------------------------
# A-3 필수 사례 (sample.csv: month, region, sales)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question,call", [
    ("지역별 매출 합계는?", SUM_BY_REGION),
    ("월별 매출 합계는?", SUM_BY_MONTH),
    ("전월 대비 매출 변화는?", MOM),
])
def test_supported_sales_questions(sales, question, call):
    resp = create_plan(question, sales, AS_OF, FakeLLM(FakeLLM.calls(call)))
    assert resp.status == "plan", resp.message
    assert execute_plan(sales, resp.plan, approve_plan(resp.plan)).status == "ok"


@pytest.mark.parametrize("question,expected", [
    ("고객별 매출 합계는?", ("unsupported", "clarify")),
    ("직원별 매출은?", ("unsupported", "clarify")),
    ("부서별 미완료 업무는?", ("unsupported",)),
])
def test_unanswerable_sales_questions_without_llm(sales, question, expected):
    llm = FakeLLM()  # 호출되면 실패: 스키마 대조만으로 막아야 한다
    resp = create_plan(question, sales, AS_OF, llm)
    assert resp.status in expected and resp.plan is None and not llm.prompts, (resp.status, resp.message)


def test_customer_question_message_lists_real_columns(sales):
    resp = create_plan("고객별 매출 합계는?", sales, AS_OF, FakeLLM())
    assert "고객" in resp.message and all(c in resp.message for c in ("month", "region", "sales"))
    assert "대신하지 않습니다" in resp.message


# ---------------------------------------------------------------------------
# LLM이 한계를 말하지 않고 조용히 다른 컬럼으로 바꾼 계획 → 실행 가능한 계획을 내보내지 않음
# ---------------------------------------------------------------------------
def test_silent_group_substitution_is_blocked(sales):
    """'월별'을 물었는데 region으로 조용히 바꾼 계획 (한계 안내 없음)."""
    resp = create_plan("월별 매출 합계는?", sales, AS_OF, FakeLLM(FakeLLM.calls(SUM_BY_REGION)))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None
    assert "region" in resp.message and "month" in resp.message


def test_silent_substitution_customer_never_reaches_llm(sales):
    llm = FakeLLM(FakeLLM.calls(SUM_BY_REGION))
    resp = create_plan("고객별 매출 합계는?", sales, AS_OF, llm)
    assert resp.status == "unsupported" and resp.plan is None and not llm.prompts


def test_extra_group_not_in_question_is_blocked(sales):
    """질문에 집계 기준이 없는데(전체 매출) 지역별로 나눈 계획."""
    resp = create_plan("전체 매출은?", sales, AS_OF, FakeLLM(FakeLLM.calls(SUM_BY_REGION)))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None


def test_mixed_plan_keeps_only_matching_step(sales):
    resp = create_plan("지역별 매출 합계는?", sales, AS_OF, FakeLLM(FakeLLM.calls(SUM_BY_REGION, SUM_BY_MONTH)))
    assert resp.status == "plan" and len(resp.plan.steps) == 1
    assert resp.plan.steps[0].params["group_by"] == ["region"]


def test_metric_substitution_is_blocked():
    csv = "month,region,sales,cost\n2026-01,서울,100,40\n2026-02,서울,150,60\n".encode("utf-8")
    ds = load_dataset({"pl.csv": csv}).dataset
    wrong = ("aggregate", {"op": "sum", "value_column": "cost", "group_by": ["region"]})
    resp = create_plan("지역별 매출 합계는?", ds, AS_OF, FakeLLM(FakeLLM.calls(wrong)))
    assert resp.status in ("clarify", "unsupported") and resp.plan is None and "cost" in resp.message


def test_unknown_group_word_is_asked_back(sales):
    llm = FakeLLM()
    resp = create_plan("브랜드별 매출은?", sales, AS_OF, llm)
    assert resp.status == "clarify" and "브랜드" in resp.message and not llm.prompts


def test_decrease_region_without_byword_is_allowed(sales):
    """'감소 지역은?'처럼 '별' 없이 지역을 물어도 지역 그룹 비교는 질문과 일치."""
    call = ("compare_periods", {"date_column": "month", "op": "sum", "value_column": "sales", "current": "2026-02",
                                "group_by": "region"})
    resp = create_plan("전월 대비 매출 변화와 감소 지역은?", sales, AS_OF, FakeLLM(FakeLLM.calls(call)))
    assert resp.status == "plan", resp.message


# ---------------------------------------------------------------------------
# 과잉 차단 방지: 팀 평가 질문 묶음(eval/)과 kafe 대표 질문
# ---------------------------------------------------------------------------
def _eval_questions():
    extra = json.loads((EVAL / "extra_questions.json").read_text(encoding="utf-8"))["questions"]
    base = [dict(q, dataset="sample") for q in json.loads((EVAL / "sample_questions.json").read_text(encoding="utf-8"))["questions"]]
    return extra + base


@pytest.mark.parametrize("q", _eval_questions(), ids=lambda q: q["id"])
def test_eval_questions_precheck(sales, kafe, q):
    """N으로 시작하는 질문(답하면 안 되는 질문)만 사전 차단, 나머지는 LLM 단계로 넘어가야 한다."""
    ds = sales if q["dataset"] == "sample" else kafe
    llm = FakeLLM(FakeLLM.calls(("check_missing_data", {})))  # 넘어가는지만 본다
    resp = create_plan(q["question"], ds, AS_OF, llm)
    if q["id"].startswith("N"):
        assert resp.status in ("unsupported", "clarify") and resp.plan is None, resp.message
    else:
        assert llm.prompts, f"과잉 차단: {q['question']} → {resp.status} {resp.message}"


@pytest.mark.parametrize("question,tool,args", REPRESENTATIVE, ids=[r[1] for r in REPRESENTATIVE])
def test_kafe_representative_unchanged(kafe, question, tool, args):
    resp = create_plan(question, kafe, AS_OF, FakeLLM(FakeLLM.calls((tool, args))))
    assert resp.status == "plan", resp.message
