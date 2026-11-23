"""실제 Gemini API 통합 테스트. 무료 호출 한도를 아끼기 위해 명시적으로 켰을 때만 실행한다.

실행 조건: GEMINI_API_KEY가 있고 RUN_LIVE_GEMINI=1 (둘 중 하나라도 없으면 건너뜀, CI는 그대로 통과)
    $env:RUN_LIVE_GEMINI="1"; pytest agent/tests/test_live_gemini.py -v      # 약 10~20회 호출
"""

from __future__ import annotations

import os

import pytest

from agent import approve_plan, create_plan, execute_plan
from agent.tests.helpers import AS_OF, REPRESENTATIVE, UNSUPPORTED, kafe_dataset

LIVE = os.environ.get("RUN_LIVE_GEMINI") == "1" and bool(os.environ.get("GEMINI_API_KEY"))
pytestmark = pytest.mark.skipif(not LIVE, reason="실제 Gemini 테스트는 RUN_LIVE_GEMINI=1 과 GEMINI_API_KEY가 있을 때만 실행")


@pytest.fixture(scope="module")
def ds():
    return kafe_dataset()


@pytest.mark.parametrize("question,tool,_args", REPRESENTATIVE, ids=[r[1] for r in REPRESENTATIVE])
def test_representative_question(ds, question, tool, _args):
    resp = create_plan(question, ds, AS_OF)
    assert resp.status == "plan", f"{resp.status}: {resp.message} {resp.rejected}"
    assert tool in [s.tool for s in resp.plan.steps], [s.tool for s in resp.plan.steps]
    out = execute_plan(ds, resp.plan, approve_plan(resp.plan))
    assert out.status == "ok", out.message


@pytest.mark.parametrize("question", UNSUPPORTED)
def test_unsupported_question_is_not_answered(ds, question):
    resp = create_plan(question, ds, AS_OF)
    assert resp.status in ("unsupported", "plan", "clarify"), resp.message
    if resp.status == "plan":  # 관련 통계만 제공할 때는 한계 안내가 반드시 있어야 한다
        assert resp.notices, "판단·예측 질문인데 한계 안내 없이 계획을 만들었습니다"
