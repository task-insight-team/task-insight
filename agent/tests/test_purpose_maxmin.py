"""회귀 테스트: aggregate 최댓값·최솟값(엔진 PR #32)이 승인 전 계획 문구에 보이는지 (FakeLLM, 실제 API 미사용).

문구 함수는 엔진과 무관하게 바로 확인하고, 계획 생성까지의 확인은 엔진이 max·min을 지원할 때만 실행한다
(#32 머지 전에는 건너뜀 → 머지 순서와 무관).
"""

from __future__ import annotations

import pytest
from typing import get_args

from contracts import AggregateParams

from agent import FakeLLM, create_plan
from agent.planner import _purpose
from agent.tests.helpers import AS_OF, kafe_dataset

ENGINE_HAS_MAXMIN = {"max", "min"} <= set(get_args(AggregateParams.model_fields["op"].annotation))


@pytest.mark.parametrize("op,expected", [
    ("max", "실적MH 최댓값 집계 (분류별)"),
    ("min", "실적MH 최솟값 집계 (분류별)"),
    ("sum", "실적MH 합계 집계 (분류별)"),      # 기존 연산 문구는 그대로
    ("mean", "실적MH 평균 집계 (분류별)"),
])
def test_purpose_labels(op, expected):
    assert _purpose("aggregate", {"op": op, "value_column": "실적MH", "group_by": ["분류"]}) == expected


def test_count_label_unchanged():
    assert _purpose("aggregate", {"op": "count", "group_by": ["담당자ID"]}) == "개수 집계 (담당자ID별)"


@pytest.mark.skipif(not ENGINE_HAS_MAXMIN, reason="엔진에 aggregate max·min이 아직 없음 (PR #32 머지 전)")
def test_plan_summary_shows_max():
    resp = create_plan("분류별 최대 실적MH는?", kafe_dataset(), AS_OF, FakeLLM(FakeLLM.calls(
        ("aggregate", {"op": "max", "value_column": "실적MH", "group_by": ["분류"], "sort": "value_desc"}))))
    assert resp.status == "plan", resp.message
    assert resp.plan.steps[0].purpose == "실적MH 최댓값 집계 (분류별)"
    assert "실적MH 최댓값 집계 (분류별)" in resp.plan.summary
