"""실제 Gemini API 통합 테스트. GEMINI_API_KEY가 없으면 건너뛴다 (CI는 키 없이 통과).

    $env:GEMINI_API_KEY="..."; pytest agent/tests/test_live_gemini.py -v
"""

from __future__ import annotations

import os

import pytest

from agent import approve_plan, create_plan, execute_plan
from agent.tests.helpers import AS_OF, REPRESENTATIVE, UNSUPPORTED, kafe_dataset

pytestmark = pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY 없음 (실제 API 테스트 생략)")


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
